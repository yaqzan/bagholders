"""Per-trade barrier-outcome cache.

Pre-computes barrier-touch outcomes for every (symbol, date, side, W_days) over
the available price_history. Each row stores win/stop/expire result + excursions.
Refactored assess pipeline does a single bulk JOIN against this cache instead
of forward-walking the same price bars thousands of times.

Storage:
    SQLite (.cache/barrier_outcomes.db)        — canonical write target. All
                                                  upserts go here.
    DuckDB mirror (.cache/barrier_outcomes.duckdb) — read-only mirror of the
                                                  SQLite file, rebuilt on
                                                  every refresh_recent().
                                                  Read path uses DuckDB by
                                                  default (~50-100x faster on
                                                  the bulk JOIN against peaks)
                                                  and falls back to SQLite if
                                                  the mirror is missing.

Backend selection: env var BARRIER_CACHE_BACKEND ∈ {'auto','duck','sqlite'}.
'auto' (default) uses DuckDB when the mirror exists, SQLite otherwise.

Schema:
    (symbol, date, side, W_days) -> outcome row

For the standard config we cache:
    side='high' (PUT):  K=1.0, M=2.0
    side='low'  (CALL): K=2.0, M=5.0
    W in {1, 3, 5, 7, 15, 30, 60, 90} (calendar days)

Both scaled (K*sqrt(W/30), M*sqrt(W/30)) and unscaled (K, M) outcomes stored.
"""
from __future__ import annotations
import os
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict
import numpy as np

# Project root
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / '.cache'
CACHE_DIR.mkdir(exist_ok=True)
CACHE_DB = CACHE_DIR / 'barrier_outcomes.db'
CACHE_DUCK = CACHE_DIR / 'barrier_outcomes.duckdb'

# Barrier parameter SETS — each set is one (K_high, M_high, K_low, M_low, reference_days)
# tuple cached separately. New sets can be added by extending BARRIER_SETS — the cache
# writer materializes one row-per-(symbol, date, side, w_days, barrier_set).
# Phase 16 (2026-04-28): added '15dte_opt' for 15 DTE option-aligned barriers.
BARRIER_SETS = {
    '30dte_generic': {  # legacy default — directional WR (M=5σ, M=2σ near-irrelevant stops)
        'k_high': 1.0,  'm_high': 2.0,
        'k_low':  2.0,  'm_low':  5.0,
        'reference_days': 30,
    },
    '15dte_opt': {       # 15 DTE option-aligned (Phase 15B C1 barriers)
        'k_high': 0.903, 'm_high': 0.516,   # PUT TP=0.35 / SL=-0.20
        'k_low':  0.903, 'm_low':  0.774,   # CALL TP=0.35 / SL=-0.30
        'reference_days': 15,
    },
    '30dte_opt': {       # 30 DTE option-aligned (Phase H5_HOLD15_H40 barriers, BASE regime)
        'k_high': 1.274, 'm_high': 0.728,   # PUT TP=0.35 / SL=-0.20  (PREMIUM_MULT=1.82, DELTA=0.5)
        'k_low':  1.274, 'm_low':  1.092,   # CALL TP_BASE=0.35 / SL_BASE=-0.30
        'reference_days': 30,
    },
    '30dte_apex': {      # 30 DTE LIVE APEX predictand (TP +30% / SL -70%, HOLD). PREMIUM_MULT=1.82, DELTA=0.5.
        'k_high': 1.274, 'm_high': 0.728,   # PUT TP=0.35 / SL=-0.20 (puts OFF in Apex; kept for parity)
        'k_low':  1.092, 'm_low':  2.548,   # CALL TP=0.30 -> 1.092sigma win ; SL=-0.70 -> 2.548sigma stop
        'reference_days': 30,
    },
}
DEFAULT_BARRIER_SET = '30dte_generic'

# Backward-compat module-level aliases (some callers still read these)
SWING_K_HIGH = BARRIER_SETS[DEFAULT_BARRIER_SET]['k_high']
SWING_M_HIGH = BARRIER_SETS[DEFAULT_BARRIER_SET]['m_high']
SWING_K_LOW  = BARRIER_SETS[DEFAULT_BARRIER_SET]['k_low']
SWING_M_LOW  = BARRIER_SETS[DEFAULT_BARRIER_SET]['m_low']
SWING_REFERENCE_DAYS = BARRIER_SETS[DEFAULT_BARRIER_SET]['reference_days']

PERIODS = [('1d', 1), ('3d', 3), ('5d', 5), ('7d', 7), ('15d', 15), ('30d', 30),
           ('60d', 60), ('90d', 90)]
PERIODS_BY_LABEL = {label: days for label, days in PERIODS}
PERIODS_BY_DAYS = {days: label for label, days in PERIODS}
SIMPLE_DIRECTION_PERIODS = {'1d'}
PERIOD_MAX_W = max(d for _, d in PERIODS)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS barrier_outcomes (
    symbol      TEXT    NOT NULL,
    date        TEXT    NOT NULL,        -- ISO YYYY-MM-DD
    side        TEXT    NOT NULL,        -- 'high' (put) or 'low' (call)
    w_days      INTEGER NOT NULL,        -- calendar days
    barrier_set TEXT    NOT NULL DEFAULT '30dte_generic',  -- key into BARRIER_SETS
    -- scaled barriers (K*sqrt(W/REF), M*sqrt(W/REF)):
    result      INTEGER,                  -- 1=win, 0=stop, NULL=expired or insufficient
    exit_offset INTEGER,                  -- calendar-day offset from base where target/stop hit
    exit_close  REAL,                     -- close price at exit / last bar if expired
    exit_return REAL,                     -- side-adjusted close-to-exit return %
    mae_pct     REAL,                     -- max adverse excursion %
    mfe_pct     REAL,                     -- max favorable excursion %
    -- unscaled barriers (K fixed, M fixed regardless of W):
    result_u    INTEGER,
    -- entry context:
    entry_close REAL,
    sigma_pct   REAL,                     -- 60-day realized vol % at signal
    -- option P&L fields (added for theta/bimodal-fill option accuracy):
    exit_bars   INTEGER,                  -- trading bars from signal to exit (for bars_held in option_pnl_pct)
    fire_type   INTEGER,                  -- 0=expire(hard-sell), 1=tp, 2=sl
    fire_open   REAL,                     -- open of the exit bar (NULL for expire)
    fire_high   REAL,                     -- high of the exit bar (NULL for expire)
    fire_low    REAL,                     -- low  of the exit bar (NULL for expire)
    PRIMARY KEY (symbol, date, side, w_days, barrier_set)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_bo_date ON barrier_outcomes(date);
CREATE INDEX IF NOT EXISTS idx_bo_sym_date ON barrier_outcomes(symbol, date);
CREATE INDEX IF NOT EXISTS idx_bo_set_date ON barrier_outcomes(barrier_set, date);
"""

_MIGRATION_SQL = [
    "ALTER TABLE barrier_outcomes ADD COLUMN exit_bars  INTEGER",
    "ALTER TABLE barrier_outcomes ADD COLUMN fire_type  INTEGER",
    "ALTER TABLE barrier_outcomes ADD COLUMN fire_open  REAL",
    "ALTER TABLE barrier_outcomes ADD COLUMN fire_high  REAL",
    "ALTER TABLE barrier_outcomes ADD COLUMN fire_low   REAL",
]


def get_conn(timeout=120):
    """Return a connection. Creates schema if missing and applies column migrations.

    timeout: seconds to wait for a write lock before raising OperationalError.
             Default 120s — long enough for a concurrent assess run to finish a
             batch commit and release the lock.
    """
    conn = sqlite3.connect(str(CACHE_DB), timeout=timeout)
    conn.executescript(_SCHEMA)
    # Idempotent column additions — safe to run every open, ignored if columns exist.
    for stmt in _MIGRATION_SQL:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-200000")  # 200MB cache
    return conn


def _realized_vol(closes_window):
    if len(closes_window) < 30:
        return None
    arr = np.asarray(closes_window, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    if len(rets) < 20:
        return None
    return float(np.std(rets[-60:]) * 100.0)


def _walk_outcome(closes, highs, lows, dates, base_idx, side, sigma_pct, W_days,
                   scale=True, barrier_set=DEFAULT_BARRIER_SET, opens=None):
    """Single-period barrier walk.

    Returns 11-tuple:
        (result, exit_offset, exit_close, exit_return, mae, mfe,
         exit_bars, fire_type, fire_open, fire_high, fire_low)

    result:      1=win(TP), 0=stop/expire, None=insufficient data
    exit_offset: calendar-day offset from base to exit bar
    exit_bars:   trading bars from base to exit (used as bars_held in option_pnl_pct)
    fire_type:   0=expire(hard-sell), 1=tp, 2=sl; None if insufficient
    fire_open/high/low: OHLC of the bar where barrier fired; None for expire/insufficient

    `barrier_set` selects K/M from BARRIER_SETS (e.g. '30dte_generic' default,
    '15dte_opt' for 15 DTE option-aligned barriers). Phase 16 (2026-04-28).
    """
    _null = (None,) * 11
    if sigma_pct is None or sigma_pct <= 0 or base_idx + 1 >= len(closes):
        return _null
    entry = closes[base_idx]
    if entry <= 0:
        return _null
    base_date = dates[base_idx]

    bs = BARRIER_SETS[barrier_set]
    K_base = bs['k_high'] if side == 'high' else bs['k_low']
    M_base = bs['m_high'] if side == 'high' else bs['m_low']
    ref_days = bs['reference_days']
    if scale:
        s = math.sqrt(W_days / ref_days)
        k = K_base * s
        m = M_base * s
    else:
        k = K_base
        m = M_base

    if side == 'high':  # put: win on drop
        t_win  = entry * (1 - k * sigma_pct / 100.0)
        t_stop = entry * (1 + m * sigma_pct / 100.0)
    else:               # call: win on rise
        t_win  = entry * (1 + k * sigma_pct / 100.0)
        t_stop = entry * (1 - m * sigma_pct / 100.0)

    cutoff_date = base_date + timedelta(days=W_days)
    running_max = entry
    running_min = entry
    last_close  = None
    last_offset = None
    bar_count   = 0   # trading bars walked so far

    for j in range(base_idx + 1, len(closes)):
        bar_date = dates[j]
        if bar_date > cutoff_date:
            break  # expired
        offset = (bar_date - base_date).days
        bar_count += 1
        last_offset = offset
        last_close  = closes[j]
        hi = highs[j] if highs is not None else closes[j]
        lo = lows[j]  if lows  is not None else closes[j]
        op = opens[j] if opens is not None else closes[j]
        if hi > running_max: running_max = hi
        if lo < running_min: running_min = lo
        if side == 'high':
            if lo <= t_win:
                exit_return = (entry - t_win) / entry * 100.0
                mae = (running_max - entry) / entry * 100.0
                mfe = (entry - running_min) / entry * 100.0
                return 1, offset, t_win, exit_return, mae, mfe, bar_count, 1, op, hi, lo
            if hi >= t_stop:
                exit_return = (entry - t_stop) / entry * 100.0
                mae = (running_max - entry) / entry * 100.0
                mfe = (entry - running_min) / entry * 100.0
                return 0, offset, t_stop, exit_return, mae, mfe, bar_count, 2, op, hi, lo
        else:
            if hi >= t_win:
                exit_return = (t_win - entry) / entry * 100.0
                mae = (entry - running_min) / entry * 100.0
                mfe = (running_max - entry) / entry * 100.0
                return 1, offset, t_win, exit_return, mae, mfe, bar_count, 1, op, hi, lo
            if lo <= t_stop:
                exit_return = (t_stop - entry) / entry * 100.0
                mae = (entry - running_min) / entry * 100.0
                mfe = (running_max - entry) / entry * 100.0
                return 0, offset, t_stop, exit_return, mae, mfe, bar_count, 2, op, hi, lo

    # Expired or insufficient
    if last_close is None:
        return _null

    # Need at least one bar PAST cutoff to confirm expire; otherwise insufficient.
    if dates[-1] <= cutoff_date:
        return _null

    # Expired — window passed without hitting either barrier (hard sell)
    if side == 'high':
        exit_return = (entry - last_close) / entry * 100.0
        mae = (running_max - entry) / entry * 100.0
        mfe = (entry - running_min) / entry * 100.0
    else:
        exit_return = (last_close - entry) / entry * 100.0
        mae = (entry - running_min) / entry * 100.0
        mfe = (running_max - entry) / entry * 100.0
    return 0, last_offset, last_close, exit_return, mae, mfe, bar_count, 0, None, None, None


def _resolve_periods(periods=None):
    """Normalize optional period selectors to PERIODS entries."""
    if periods is None:
        return PERIODS
    selected = []
    for p in periods:
        if isinstance(p, tuple) and len(p) == 2:
            label, days = p
            selected.append((str(label), int(days)))
            continue
        if isinstance(p, str):
            key = p.strip().lower()
            if key.endswith('d'):
                key = key[:-1]
            if not key.isdigit():
                raise ValueError(f"Unknown barrier period: {p!r}")
            days = int(key)
        else:
            days = int(p)
        label = PERIODS_BY_DAYS.get(days)
        if label is None:
            raise ValueError(f"Unsupported barrier period: {days}d")
        selected.append((label, days))
    return selected


def compute_outcomes_for_symbol(symbol, bars, barrier_sets=None, periods=None):
    """Compute all (date, side, W, barrier_set) outcomes for one symbol's bar history.

    bars: list of (date, close, high, low[, open]) sorted ascending.
          The open column (index 4) is optional for backward compat but required
          for option P&L fire-bar OHLC columns. Pass it when available.
    barrier_sets: iterable of barrier_set keys to compute. None = all in BARRIER_SETS.
    periods: optional iterable of period labels/days to compute. None = all PERIODS.
    Yields rows ready for INSERT (19 values per row including new option P&L columns).
    """
    if barrier_sets is None:
        barrier_sets = list(BARRIER_SETS.keys())
    periods = _resolve_periods(periods)
    if len(bars) < 30:
        return
    dates  = [b[0] for b in bars]
    closes = np.array([float(b[1]) for b in bars])
    highs  = np.array([float(b[2]) for b in bars])
    lows   = np.array([float(b[3]) for b in bars])
    # opens optional — present when bars have 5+ columns
    has_open = len(bars[0]) >= 5
    opens  = np.array([float(b[4]) for b in bars]) if has_open else None

    for i in range(30, len(bars)):
        sigma = _realized_vol(closes[max(0, i - 70):i + 1].tolist())
        if sigma is None or sigma <= 0:
            continue
        for side in ('high', 'low'):
            for label, W in periods:
                if label in SIMPLE_DIRECTION_PERIODS:
                    # 1d: simple next-bar direction; no scaled barriers — same for every set
                    if i + 1 >= len(bars):
                        result = None
                        eo = None; ec = None; er = None; mae = None; mfe = None
                        eb = None; ft = None; fo = None; fh = None; fl = None
                    else:
                        nxt_close = closes[i + 1]
                        if side == 'high':
                            r = nxt_close < closes[i]
                        else:
                            r = nxt_close > closes[i]
                        result = 1 if r else 0
                        eo = (dates[i + 1] - dates[i]).days
                        ec = float(nxt_close)
                        er = (closes[i] - nxt_close) / closes[i] * 100.0 if side == 'high' \
                             else (nxt_close - closes[i]) / closes[i] * 100.0
                        mae = max(0.0, (highs[i + 1] - closes[i]) / closes[i] * 100.0) if side == 'high' \
                              else max(0.0, (closes[i] - lows[i + 1]) / closes[i] * 100.0)
                        mfe = max(0.0, (closes[i] - lows[i + 1]) / closes[i] * 100.0) if side == 'high' \
                              else max(0.0, (highs[i + 1] - closes[i]) / closes[i] * 100.0)
                        eb = 1
                        ft = 1 if result == 1 else 0  # 1d: win=tp-like, loss=expire-like
                        fo = float(opens[i + 1]) if has_open else None
                        fh = float(highs[i + 1])
                        fl = float(lows[i + 1])
                    for bset in barrier_sets:
                        yield (symbol, dates[i].isoformat(), side, W, bset,
                               result, eo, ec, er, mae, mfe,
                               result,  # unscaled = scaled for 1d
                               float(closes[i]), float(sigma),
                               eb, ft, fo, fh, fl)
                else:
                    for bset in barrier_sets:
                        res_s = _walk_outcome(closes, highs, lows, dates, i, side, sigma, W,
                                               scale=True,  barrier_set=bset, opens=opens)
                        res_u = _walk_outcome(closes, highs, lows, dates, i, side, sigma, W,
                                               scale=False, barrier_set=bset, opens=opens)
                        # res_s: (result, exit_offset, exit_close, exit_return, mae, mfe,
                        #          exit_bars, fire_type, fire_open, fire_high, fire_low)
                        yield (symbol, dates[i].isoformat(), side, W, bset,
                               res_s[0], res_s[1], res_s[2], res_s[3], res_s[4], res_s[5],
                               res_u[0],
                               float(closes[i]), float(sigma),
                               res_s[6], res_s[7], res_s[8], res_s[9], res_s[10])


def upsert_rows(conn, rows, batch=10000):
    """Bulk INSERT OR REPLACE."""
    sql = """
    INSERT OR REPLACE INTO barrier_outcomes
        (symbol, date, side, w_days, barrier_set,
         result, exit_offset, exit_close, exit_return, mae_pct, mfe_pct,
         result_u, entry_close, sigma_pct,
         exit_bars, fire_type, fire_open, fire_high, fire_low)
    VALUES (?,?,?,?,?, ?,?,?,?,?,?, ?, ?,?, ?,?,?,?,?)
    """
    cur = conn.cursor()
    chunk = []
    inserted = 0
    for r in rows:
        chunk.append(r)
        if len(chunk) >= batch:
            cur.executemany(sql, chunk)
            inserted += len(chunk)
            chunk = []
    if chunk:
        cur.executemany(sql, chunk)
        inserted += len(chunk)
    conn.commit()
    return inserted


def backfill(symbols=None, lookback_days=1825, verbose=True, periods=None):
    """Backfill the cache from MySQL price_history.

    Args:
        symbols: list of symbol strings or None for all stocks
        lookback_days: how far back to compute outcomes
        periods: optional iterable of period labels/days to backfill. None = all PERIODS.
    Returns:
        rows inserted
    """
    return backfill_sets(symbols=symbols, lookback_days=lookback_days,
                         barrier_sets=None, verbose=verbose, periods=periods)


def backfill_sets(symbols=None, lookback_days=1825, barrier_sets=None, verbose=True, periods=None):
    """Same as backfill() but with explicit barrier_sets selector. Use this to
    populate selected barrier sets or periods without recomputing the full cache."""
    from database.models.technical import PriceHistory
    from database.models.core import Stock

    cutoff = date.today() - timedelta(days=lookback_days + 90)  # extra 90d for sigma window

    if symbols is None:
        symbols = [s.symbol for s in Stock.select()]
    if barrier_sets is None:
        barrier_sets = list(BARRIER_SETS.keys())
    periods = _resolve_periods(periods)

    conn = get_conn()
    total_rows = 0
    if verbose:
        period_desc = ','.join(label for label, _ in periods)
        print(f"  backfilling barrier_sets={barrier_sets} for {len(symbols)} symbols, "
              f"lookback={lookback_days}d periods={period_desc}", flush=True)
    try:
        for i, sym in enumerate(symbols):
            bars_q = (PriceHistory
                      .select(PriceHistory.date, PriceHistory.close,
                              PriceHistory.high, PriceHistory.low, PriceHistory.open)
                      .where(PriceHistory.symbol == sym, PriceHistory.date >= cutoff)
                      .order_by(PriceHistory.date)
                      .tuples())
            bars = list(bars_q)
            if len(bars) < 30:
                continue
            rows = list(compute_outcomes_for_symbol(
                sym, bars, barrier_sets=barrier_sets, periods=periods
            ))
            n = upsert_rows(conn, rows)
            total_rows += n
            if verbose and (i + 1) % 25 == 0:
                print(f"  [{i+1}/{len(symbols)}] {sym}: +{n} rows  (total {total_rows})", flush=True)
    finally:
        conn.close()
    # Refresh the DuckDB read mirror so subsequent peaks_to_swing_results calls
    # see the new rows. Skip the rebuild if duckdb isn't available — the SQLite
    # path remains a working fallback.
    try:
        rebuild_duck_mirror(verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"  duck mirror rebuild skipped: {e}")
    return total_rows


def lookup_batch(symbol_dates, side, w_days):
    """Bulk lookup for a list of (symbol, date) pairs at one (side, w_days).

    Returns dict {(symbol, date): row_dict_or_None}.
    """
    if not symbol_dates:
        return {}
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ','.join(['(?,?)'] * len(symbol_dates))
    params = []
    for sym, d in symbol_dates:
        params.append(sym)
        params.append(d.isoformat() if hasattr(d, 'isoformat') else d)
    sql = f"""
    SELECT symbol, date, result, exit_offset, exit_close, exit_return, mae_pct, mfe_pct,
           result_u, entry_close, sigma_pct,
           exit_bars, fire_type, fire_open, fire_high, fire_low
    FROM barrier_outcomes
    WHERE side = ? AND w_days = ?
      AND (symbol, date) IN ({placeholders})
    """
    cur.execute(sql, [side, w_days] + params)
    out = {}
    for r in cur.fetchall():
        sym, d_str = r[0], r[1]
        from datetime import datetime as _dt
        d = _dt.strptime(d_str, '%Y-%m-%d').date()
        out[(sym, d)] = {
            'result':      r[2],
            'exit_offset': r[3],
            'exit_close':  r[4],
            'exit_return': r[5],
            'mae_pct':     r[6],
            'mfe_pct':     r[7],
            'result_u':    r[8],
            'entry_close': r[9],
            'sigma_pct':   r[10],
            'exit_bars':   r[11],
            'fire_type':   r[12],
            'fire_open':   r[13],
            'fire_high':   r[14],
            'fire_low':    r[15],
        }
    conn.close()
    return out


def refresh_recent(days=160, symbols=None, verbose=True):
    """Refresh the trailing window of cached outcomes.

    Most rows in the cache are stable forever once computed. The exception is
    rows that were marked NULL (insufficient forward data) — these can flip
    to a definitive 1/0 once enough new price bars accumulate. Run this after
    each `trader update` to keep the cache current.

    Args:
        days: how many trailing calendar days to recompute (default 160 — covers
              the largest barrier window W=150 with 10d safety margin)
        symbols: optional restriction to specific symbols
    Returns:
        rows updated
    """
    from database.models.technical import PriceHistory
    from database.models.core import Stock

    cutoff_signal = date.today() - timedelta(days=days)
    # Need price history extending back enough for the sigma window (60d realized vol)
    cutoff_price = cutoff_signal - timedelta(days=90)

    if symbols is None:
        symbols = [s.symbol for s in Stock.select()]

    conn = get_conn()
    total = 0
    try:
        for i, sym in enumerate(symbols):
            bars = list(PriceHistory.select(PriceHistory.date, PriceHistory.close,
                                              PriceHistory.high, PriceHistory.low, PriceHistory.open)
                        .where(PriceHistory.symbol == sym, PriceHistory.date >= cutoff_price)
                        .order_by(PriceHistory.date)
                        .tuples())
            if len(bars) < 30:
                continue
            # Filter to only signals on or after cutoff_signal — but pass full bars
            # so the walk has access to forward bars for resolution
            rows = []
            for r in compute_outcomes_for_symbol(sym, bars):
                sig_date = date.fromisoformat(r[1])
                if sig_date >= cutoff_signal:
                    rows.append(r)
            n = upsert_rows(conn, rows)
            total += n
            if verbose and (i + 1) % 50 == 0:
                print(f"  refresh [{i+1}/{len(symbols)}]: total {total}", flush=True)
    finally:
        conn.close()
    try:
        rebuild_duck_mirror(verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"  duck mirror rebuild skipped: {e}")
    return total


def peaks_to_swing_results(peaks, verbose=False, barrier_set=DEFAULT_BARRIER_SET):
    """Bulk-build swing-style result dicts for a list of peaks via the cache.

    `barrier_set` selects which cached set to read ('30dte_generic' default,
    '15dte_opt' for 15 DTE option-aligned barriers). Returns dicts compatible
    with assess_scores._peak_to_result format.

    Routes to the DuckDB mirror by default (~50-100x faster on the bulk JOIN);
    falls back to the SQLite implementation when the mirror is missing or the
    BARRIER_CACHE_BACKEND env var forces it.

    Returns (results, n_uncached_skipped).
    """
    backend = _select_backend()
    if backend == 'duck':
        return _peaks_to_swing_results_duck(peaks, verbose, barrier_set)
    return _peaks_to_swing_results_sqlite(peaks, verbose, barrier_set)


def _peaks_to_swing_results_sqlite(peaks, verbose=False, barrier_set=DEFAULT_BARRIER_SET):
    """Original SQLite implementation. Kept as the canonical fallback."""
    if not peaks:
        return [], 0

    # Group peaks by side for bulk query
    sides = {}
    for p in peaks:
        side = 'low' if p.overall >= 50 else 'high'
        sides.setdefault(side, []).append(p)

    conn = get_conn()
    cur = conn.cursor()

    # Build (symbol, date, side) → score lookup dict — peaks is small
    peak_keys = {}
    for p in peaks:
        side = 'low' if p.overall >= 50 else 'high'
        d_iso = p.date.isoformat() if hasattr(p.date, 'isoformat') else p.date
        peak_keys[(p.symbol_id, d_iso, side)] = int(p.overall)

    if not peak_keys:
        return [], 0

    # Resolve K/M for the requested barrier set so we can disambiguate stop vs expire
    bs = BARRIER_SETS[barrier_set]
    bs_k_high, bs_m_high = bs['k_high'], bs['m_high']
    bs_k_low,  bs_m_low  = bs['k_low'],  bs['m_low']
    bs_ref               = bs['reference_days']

    # Build INDEXED temp table for the JOIN. Index on (symbol, date, side)
    # matches barrier_outcomes' primary key prefix → SQLite uses the index.
    cur.executescript("""
    DROP TABLE IF EXISTS _ps_peaks;
    CREATE TEMP TABLE _ps_peaks (
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        side TEXT NOT NULL,
        score INTEGER
    );
    """)
    insert_rows = [(s, d, side, sc) for (s, d, side), sc in peak_keys.items()]
    cur.executemany("INSERT INTO _ps_peaks VALUES (?,?,?,?)", insert_rows)
    cur.execute("CREATE INDEX _ps_peaks_idx ON _ps_peaks(symbol, date, side)")

    # JOIN — filtered by barrier_set
    cur.execute("""
    SELECT p.symbol, p.date, p.side, b.w_days,
           b.result, b.exit_offset, b.exit_close, b.exit_return,
           b.mae_pct, b.mfe_pct, b.result_u, b.entry_close, b.sigma_pct,
           b.exit_bars, b.fire_type, b.fire_open, b.fire_high, b.fire_low
    FROM _ps_peaks p
    JOIN barrier_outcomes b USING (symbol, date, side)
    WHERE b.barrier_set = ?
    """, (barrier_set,))

    # Period label lookup
    w_to_label = {w: lbl for lbl, w in PERIODS}

    # Group by (symbol, date) — JOIN already filtered to peak rows
    by_key = defaultdict(lambda: {'periods': {}})
    for r in cur.fetchall():
        sym, dt, side, w, result, eo, ec, er, mae, mfe, ru, entry, sigma, \
            exit_bars, fire_type, fire_open, fire_high, fire_low = r
        score = peak_keys.get((sym, dt, side))
        if score is None:
            continue  # safety
        key = (sym, dt)
        rec = by_key[key]
        rec['symbol'] = sym
        if 'date' not in rec:
            rec['date'] = _isodate_to_date(dt)
        rec['score'] = score
        rec['side'] = side
        rec['vol_pct'] = sigma
        rec['entry'] = entry

        # Map result int -> string label
        # win: result=1
        # stop: result=0 AND exit_close near barrier price
        # expire: result=0 AND exit_close NOT at barrier
        # None / insufficient: result IS NULL
        label = w_to_label.get(w)
        if label is None:
            continue

        if result is None:
            # Insufficient data — skip this period
            continue

        # Reconstruct stop barrier price to disambiguate stop vs expire — uses the
        # K/M of the *requested* barrier_set (not module-level legacy constants).
        if w == 1:
            # 1d period: simple direction. Win=1 -> 'win', Win=0 -> 'stop' (exact tie 'expire' rare)
            kind = 'win' if result == 1 else 'stop'
        else:
            scale = math.sqrt(w / bs_ref)
            if side == 'high':
                t_stop = entry * (1.0 + bs_m_high * scale * sigma / 100.0)
                t_win  = entry * (1.0 - bs_k_high * scale * sigma / 100.0)
            else:
                t_stop = entry * (1.0 - bs_m_low  * scale * sigma / 100.0)
                t_win  = entry * (1.0 + bs_k_low  * scale * sigma / 100.0)

            if result == 1:
                kind = 'win'
            elif fire_type is not None:
                # fire_type (0=expire, 1=tp, 2=sl) is written by _walk_outcome and
                # IS the exact answer — no reconstruction needed. (D5 fix 2026-08-10)
                kind = 'stop' if fire_type == 2 else 'expire'
            else:
                # FALLBACK ONLY — rows written before the fire_type column existed
                # (~139k NULL of 28.8M). Stop hit if exit_close is within 0.5% of
                # the stop barrier; else expire. Mislabels an expire whose last
                # close happens to land near the stop.
                if ec is not None and abs(ec - t_stop) / max(0.01, abs(t_stop)) < 0.005:
                    kind = 'stop'
                else:
                    kind = 'expire'

        if ru is None:
            kind_u = None
        elif w == 1:
            kind_u = 'win' if ru == 1 else 'stop'
        else:
            # Unscaled barriers (no sqrt scale). NOTE: fire_type describes the
            # SCALED walk only — there is no stored unscaled fire type — so the
            # 0.5% heuristic necessarily stays the primary path here (D5).
            if side == 'high':
                tu_stop = entry * (1.0 + bs_m_high * sigma / 100.0)
            else:
                tu_stop = entry * (1.0 - bs_m_low  * sigma / 100.0)
            if ru == 1:
                kind_u = 'win'
            elif ec is not None and abs(ec - tu_stop) / max(0.01, abs(tu_stop)) < 0.005:
                kind_u = 'stop'
            else:
                kind_u = 'expire'

        rec['periods'][label] = {
            'W':               w,
            'result':          kind,
            'result_unscaled': kind_u,
            'exit_day':        eo,
            'exit_close':      ec,
            'exit_ret':        er,
            'mae':             mae,
            'mfe':             mfe,
            'exit_bars':       exit_bars,
            'fire_type':       fire_type,
            'fire_open':       fire_open,
            'fire_high':       fire_high,
            'fire_low':        fire_low,
        }

    # Format output
    results = []
    skipped = 0
    for p in peaks:
        key = (p.symbol_id, p.date.isoformat() if hasattr(p.date, 'isoformat') else p.date)
        rec = by_key.get(key)
        if not rec or not rec['periods']:
            skipped += 1
            continue

        returns = {}; peak_returns = {}; mfes = {}; maes = {}
        for lbl, ps in rec['periods'].items():
            returns[lbl] = ps['exit_ret']
            peak_returns[lbl] = ps['mfe']
            mfes[lbl] = ps['mfe']
            maes[lbl] = ps['mae']

        results.append({
            'symbol':  rec['symbol'],
            'date':    rec['date'],
            'score':   rec['score'],
            'side':    rec['side'],
            'vol_pct': rec['vol_pct'],
            'returns': returns,
            'peaks':   peak_returns,
            'maes':    maes,
            'mfes':    mfes,
            'swing':   rec['periods'],
        })

    conn.close()
    if verbose:
        print(f"  cache hits: {len(results)} peaks; uncached: {skipped} (sqlite)")
    return results, skipped


def _isodate_to_date(s):
    """Convert ISO date string to date object."""
    from datetime import datetime as _dt
    return _dt.strptime(s, '%Y-%m-%d').date()


# =============================================================================
# DuckDB read mirror — see module docstring for the SQLite/DuckDB split.
# =============================================================================

_DUCK_CON = None  # cached read-only connection per process


def _select_backend() -> str:
    """Resolve BARRIER_CACHE_BACKEND env var into 'duck' or 'sqlite'."""
    pref = os.environ.get('BARRIER_CACHE_BACKEND', 'auto').lower()
    if pref == 'sqlite':
        return 'sqlite'
    if pref == 'duck':
        return 'duck'
    # auto: prefer duck when mirror exists and is at least as new as sqlite
    if not CACHE_DUCK.exists():
        return 'sqlite'
    if CACHE_DB.exists() and CACHE_DUCK.stat().st_mtime < CACHE_DB.stat().st_mtime:
        # Mirror is stale — fall back to sqlite, which is always current.
        return 'sqlite'
    return 'duck'


def _get_duck_con():
    """Cached read-only DuckDB connection to the mirror file."""
    global _DUCK_CON
    if _DUCK_CON is not None:
        return _DUCK_CON
    import duckdb
    _DUCK_CON = duckdb.connect(str(CACHE_DUCK), read_only=True)
    return _DUCK_CON


def _close_duck_con():
    global _DUCK_CON
    if _DUCK_CON is not None:
        try:
            _DUCK_CON.close()
        except Exception:
            pass
        _DUCK_CON = None


def rebuild_duck_mirror(verbose: bool = True) -> Path:
    """Rebuild the DuckDB mirror from the canonical SQLite cache.

    Atomic: writes to a temp path, then renames over the live file. Closes any
    cached read connection first so the rename doesn't fight a held file
    handle on Windows.
    """
    import duckdb
    if not CACHE_DB.exists():
        raise RuntimeError(f"SQLite cache missing at {CACHE_DB}; nothing to mirror")

    _close_duck_con()
    tmp = CACHE_DUCK.with_suffix('.duckdb.tmp')
    if tmp.exists():
        tmp.unlink()

    if verbose:
        import time as _time
        _t0 = _time.perf_counter()
        print(f"  rebuilding duck mirror from {CACHE_DB.name}...", flush=True)

    con = duckdb.connect(str(tmp))
    try:
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{CACHE_DB.as_posix()}' AS src (TYPE SQLITE, READ_ONLY);")
        # Typed DATE column for predicate pushdown; everything else copied as-is.
        con.execute("""
        CREATE TABLE barrier_outcomes AS
        SELECT
            symbol,
            CAST(date AS DATE) AS date,
            side,
            w_days,
            barrier_set,
            result, exit_offset, exit_close, exit_return,
            mae_pct, mfe_pct, result_u, entry_close, sigma_pct,
            exit_bars, fire_type, fire_open, fire_high, fire_low
        FROM src.barrier_outcomes;
        """)
        con.execute("CREATE INDEX bo_sym_date ON barrier_outcomes(symbol, date);")
        con.execute("CREATE INDEX bo_set      ON barrier_outcomes(barrier_set);")
    finally:
        con.close()

    if CACHE_DUCK.exists():
        CACHE_DUCK.unlink()
    tmp.rename(CACHE_DUCK)

    if verbose:
        import time as _time
        size_gb = CACHE_DUCK.stat().st_size / 1e9
        print(f"  duck mirror rebuilt: {size_gb:.2f} GB in {_time.perf_counter()-_t0:.1f}s",
              flush=True)
    return CACHE_DUCK


class _PeakArrowTable:
    """pyarrow Table wrapper so DuckDB can JOIN our peak list zero-copy."""
    def __init__(self, rows):
        import pyarrow as pa
        self.table = pa.table({
            'symbol': [r[0] for r in rows],
            'date'  : [r[1] for r in rows],
            'side'  : [r[2] for r in rows],
        })
    def __arrow_c_stream__(self, requested_schema=None):
        return self.table.__arrow_c_stream__(requested_schema)


def _peaks_to_swing_results_duck(peaks, verbose, barrier_set):
    """DuckDB-backed implementation of peaks_to_swing_results.

    Reads from the mirror file (CACHE_DUCK). Output identical to the SQLite
    path — verified by experiments/duckdb_spike/bench_focused.py.
    """
    if not peaks:
        return [], 0

    sides = {}
    for p in peaks:
        side = 'low' if p.overall >= 50 else 'high'
        sides.setdefault(side, []).append(p)

    peak_keys = {}
    for p in peaks:
        side = 'low' if p.overall >= 50 else 'high'
        d_iso = p.date.isoformat() if hasattr(p.date, 'isoformat') else p.date
        peak_keys[(p.symbol_id, d_iso, side)] = int(p.overall)

    if not peak_keys:
        return [], 0

    bs = BARRIER_SETS[barrier_set]
    bs_k_high, bs_m_high = bs['k_high'], bs['m_high']
    bs_k_low,  bs_m_low  = bs['k_low'],  bs['m_low']
    bs_ref               = bs['reference_days']

    con = _get_duck_con()
    rows_in = [(s, d, side) for (s, d, side) in peak_keys.keys()]
    con.register('_peaks_in', _PeakArrowTable(rows_in))
    try:
        cur = con.execute("""
        SELECT b.symbol, b.date, b.side, b.w_days,
               b.result, b.exit_offset, b.exit_close, b.exit_return,
               b.mae_pct, b.mfe_pct, b.result_u, b.entry_close, b.sigma_pct,
               b.exit_bars, b.fire_type, b.fire_open, b.fire_high, b.fire_low
        FROM _peaks_in p
        JOIN barrier_outcomes b
          ON b.symbol = p.symbol
         AND b.date   = CAST(p.date AS DATE)
         AND b.side   = p.side
        WHERE b.barrier_set = ?
        """, [barrier_set])
        joined = cur.fetchall()
    finally:
        con.unregister('_peaks_in')

    w_to_label = {w: lbl for lbl, w in PERIODS}
    by_key = defaultdict(lambda: {'periods': {}})
    for r in joined:
        (sym, dt, side, w, result, eo, ec, er, mae, mfe, ru,
         entry, sigma, exit_bars, fire_type, fire_open, fire_high, fire_low) = r

        if hasattr(dt, 'isoformat'):
            dt_iso = dt.isoformat(); dt_obj = dt
        else:
            dt_iso = dt; dt_obj = _isodate_to_date(dt)

        score = peak_keys.get((sym, dt_iso, side))
        if score is None:
            continue
        label = w_to_label.get(w)
        if label is None or result is None:
            continue

        key = (sym, dt_iso)
        rec = by_key[key]
        rec['symbol'] = sym
        rec.setdefault('date', dt_obj)
        rec['score'] = score
        rec['side']  = side
        rec['vol_pct'] = sigma
        rec['entry']   = entry

        if w == 1:
            kind = 'win' if result == 1 else 'stop'
        else:
            scale = math.sqrt(w / bs_ref)
            if side == 'high':
                t_stop = entry * (1.0 + bs_m_high * scale * sigma / 100.0)
            else:
                t_stop = entry * (1.0 - bs_m_low  * scale * sigma / 100.0)
            if result == 1:
                kind = 'win'
            elif fire_type is not None:
                # Exact stored answer (0=expire, 1=tp, 2=sl) — D5 fix 2026-08-10.
                kind = 'stop' if fire_type == 2 else 'expire'
            elif ec is not None and abs(ec - t_stop) / max(0.01, abs(t_stop)) < 0.005:
                kind = 'stop'   # FALLBACK: pre-fire_type rows only (~139k of 28.8M)
            else:
                kind = 'expire'

        if ru is None:
            kind_u = None
        elif w == 1:
            kind_u = 'win' if ru == 1 else 'stop'
        else:
            # fire_type is scaled-walk only — heuristic stays primary here (D5).
            if side == 'high':
                tu_stop = entry * (1.0 + bs_m_high * sigma / 100.0)
            else:
                tu_stop = entry * (1.0 - bs_m_low  * sigma / 100.0)
            if ru == 1:
                kind_u = 'win'
            elif ec is not None and abs(ec - tu_stop) / max(0.01, abs(tu_stop)) < 0.005:
                kind_u = 'stop'
            else:
                kind_u = 'expire'

        rec['periods'][label] = {
            'W': w, 'result': kind, 'result_unscaled': kind_u,
            'exit_day': eo, 'exit_close': ec, 'exit_ret': er,
            'mae': mae, 'mfe': mfe,
            'exit_bars': exit_bars, 'fire_type': fire_type,
            'fire_open': fire_open, 'fire_high': fire_high, 'fire_low': fire_low,
        }

    results = []
    skipped = 0
    for p in peaks:
        d_iso = p.date.isoformat() if hasattr(p.date, 'isoformat') else p.date
        rec = by_key.get((p.symbol_id, d_iso))
        if not rec or not rec['periods']:
            skipped += 1
            continue
        returns = {}; peak_returns = {}; mfes = {}; maes = {}
        for lbl, ps in rec['periods'].items():
            returns[lbl] = ps['exit_ret']
            peak_returns[lbl] = ps['mfe']
            mfes[lbl] = ps['mfe']
            maes[lbl] = ps['mae']
        results.append({
            'symbol': rec['symbol'], 'date': rec['date'], 'score': rec['score'],
            'side': rec['side'], 'vol_pct': rec['vol_pct'],
            'returns': returns, 'peaks': peak_returns,
            'maes': maes, 'mfes': mfes, 'swing': rec['periods'],
        })
    if verbose:
        print(f"  cache hits: {len(results)} peaks; uncached: {skipped} (duck)")
    return results, skipped


def stats():
    """Quick health check on the cache."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM barrier_outcomes")
    total = cur.fetchone()[0]
    cur.execute("SELECT MIN(date), MAX(date) FROM barrier_outcomes")
    dmin, dmax = cur.fetchone()
    cur.execute("SELECT COUNT(DISTINCT symbol) FROM barrier_outcomes")
    n_syms = cur.fetchone()[0]
    cur.execute("SELECT side, w_days, COUNT(*), SUM(result), SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) FROM barrier_outcomes GROUP BY side, w_days ORDER BY side, w_days")
    breakdown = cur.fetchall()
    conn.close()
    print(f"SQLite source: {CACHE_DB}")
    print(f"  size: {CACHE_DB.stat().st_size/1e9:.2f} GB")
    print(f"  rows: {total:,}")
    print(f"  date range: {dmin} -> {dmax}")
    print(f"  symbols: {n_syms}")
    if CACHE_DUCK.exists():
        sqlite_mtime = CACHE_DB.stat().st_mtime
        duck_mtime   = CACHE_DUCK.stat().st_mtime
        stale = duck_mtime < sqlite_mtime
        print(f"DuckDB mirror: {CACHE_DUCK}")
        print(f"  size: {CACHE_DUCK.stat().st_size/1e9:.2f} GB")
        print(f"  status: {'STALE — will fall back to sqlite' if stale else 'fresh'}")
    else:
        print(f"DuckDB mirror: missing — peaks_to_swing_results uses SQLite path. "
              f"Run `python -m database.barrier_cache rebuild-duck` to enable the fast path.")
    print(f"\nBreakdown by (side, w_days):")
    print(f"  {'side':<6} {'W':>4} {'N':>10} {'wins':>10} {'NULLs':>8}")
    for s, w, n, w_, nulls in breakdown:
        print(f"  {s:<6} {w:>4} {n:>10,} {w_ or 0:>10,} {nulls or 0:>8,}")


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'
    if cmd == 'backfill':
        n = backfill(lookback_days=int(sys.argv[2]) if len(sys.argv) > 2 else 1825)
        print(f"Inserted/replaced {n:,} rows")
        stats()
    elif cmd == 'backfill-periods':
        lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 1825
        periods_arg = sys.argv[3] if len(sys.argv) > 3 else ''
        periods = [p.strip() for p in periods_arg.split(',') if p.strip()] or None
        barrier_sets_arg = sys.argv[4] if len(sys.argv) > 4 else ''
        barrier_sets = [b.strip() for b in barrier_sets_arg.split(',') if b.strip()] or None
        n = backfill_sets(lookback_days=lookback, barrier_sets=barrier_sets, periods=periods)
        print(f"Inserted/replaced {n:,} rows")
    elif cmd == 'rebuild-duck':
        rebuild_duck_mirror(verbose=True)
        stats()
    elif cmd == 'stats':
        stats()
    else:
        print(f"Unknown command: {cmd}. "
              f"Use 'backfill [days]', 'backfill-periods [days] [3d,5d] [set,...]', "
              f"'rebuild-duck', or 'stats'")
