"""DuckDB-backed implementations of the barrier_cache hot read path.

Three backends side-by-side with the existing SQLite path so we can A/B test:
  1. duck_attach_sqlite  — DuckDB attaches the existing .cache/barrier_outcomes.db
                            via sqlite_scanner. Zero migration.
  2. duck_native         — DuckDB table copied once from SQLite. Native columnar.
  3. duck_parquet        — barrier_outcomes exported to parquet; DuckDB queries it.

All three implement the same surface as
`barrier_cache.peaks_to_swing_results(peaks, barrier_set)` so the benchmark
harness can swap them transparently.
"""
from __future__ import annotations
import math
from collections import defaultdict
from datetime import datetime as _dt
from pathlib import Path

import duckdb

from database.barrier_cache import (
    BARRIER_SETS, DEFAULT_BARRIER_SET, PERIODS, CACHE_DB,
)

ROOT = Path(__file__).resolve().parents[2]
DUCK_NATIVE  = ROOT / '.cache' / 'barrier_outcomes.duckdb'
DUCK_PARQUET = ROOT / '.cache' / 'barrier_outcomes.parquet'

W_TO_LABEL = {w: lbl for lbl, w in PERIODS}


# ----- one-time materializers ---------------------------------------------------

def materialize_duck_native(force: bool = False) -> Path:
    """Copy SQLite barrier_outcomes into a native DuckDB file."""
    if DUCK_NATIVE.exists() and not force:
        return DUCK_NATIVE
    if DUCK_NATIVE.exists():
        DUCK_NATIVE.unlink()
    con = duckdb.connect(str(DUCK_NATIVE))
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{CACHE_DB.as_posix()}' AS src (TYPE SQLITE, READ_ONLY);")
    # Copy with explicit DATE cast — SQLite stores ISO strings, DuckDB benefits
    # from typed columns for predicate pushdown and partition-prune-equivalent.
    con.execute("""
    CREATE TABLE barrier_outcomes AS
    SELECT
        symbol,
        CAST(date AS DATE)            AS date,
        side,
        w_days,
        barrier_set,
        result,
        exit_offset,
        exit_close,
        exit_return,
        mae_pct,
        mfe_pct,
        result_u,
        entry_close,
        sigma_pct,
        exit_bars,
        fire_type,
        fire_open,
        fire_high,
        fire_low
    FROM src.barrier_outcomes;
    """)
    # Indexes — DuckDB's zonemaps already do range elimination, but a
    # min/max index on the join keys helps point-lookups.
    con.execute("CREATE INDEX bo_sym_date ON barrier_outcomes(symbol, date);")
    con.execute("CREATE INDEX bo_set      ON barrier_outcomes(barrier_set);")
    con.close()
    return DUCK_NATIVE


def materialize_duck_parquet(force: bool = False) -> Path:
    """Export barrier_outcomes to a single parquet file (Snappy, row-group=128k)."""
    if DUCK_PARQUET.exists() and not force:
        return DUCK_PARQUET
    if DUCK_PARQUET.exists():
        DUCK_PARQUET.unlink()
    con = duckdb.connect(":memory:")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{CACHE_DB.as_posix()}' AS src (TYPE SQLITE, READ_ONLY);")
    con.execute(f"""
    COPY (
        SELECT
            symbol,
            CAST(date AS DATE) AS date,
            side,
            w_days,
            barrier_set,
            result, exit_offset, exit_close, exit_return,
            mae_pct, mfe_pct, result_u, entry_close, sigma_pct,
            exit_bars, fire_type, fire_open, fire_high, fire_low
        FROM src.barrier_outcomes
        ORDER BY barrier_set, symbol, date
    )
    TO '{DUCK_PARQUET.as_posix()}'
    (FORMAT 'parquet', COMPRESSION 'snappy', ROW_GROUP_SIZE 131072);
    """)
    con.close()
    return DUCK_PARQUET


# ----- shared output formatter --------------------------------------------------

def _format_results(rows, peak_keys, peaks, barrier_set):
    """Identical post-JOIN reshape used by all backends.

    rows: iterable of (sym, dt_iso_or_date, side, w, result, eo, ec, er,
                      mae, mfe, ru, entry, sigma,
                      exit_bars, fire_type, fire_open, fire_high, fire_low)
    peak_keys: dict {(sym, dt_iso, side): score}
    peaks: original peak list (used to preserve order + skipped count)
    """
    bs = BARRIER_SETS[barrier_set]
    bs_k_high, bs_m_high = bs['k_high'], bs['m_high']
    bs_k_low,  bs_m_low  = bs['k_low'],  bs['m_low']
    bs_ref               = bs['reference_days']

    by_key = defaultdict(lambda: {'periods': {}})

    for r in rows:
        (sym, dt, side, w, result, eo, ec, er, mae, mfe, ru,
         entry, sigma, exit_bars, fire_type, fire_open, fire_high, fire_low) = r

        # Normalize dt to ISO string for peak_keys lookup
        if hasattr(dt, 'isoformat'):
            dt_iso = dt.isoformat()
            dt_obj = dt
        else:
            dt_iso = dt
            dt_obj = _dt.strptime(dt, '%Y-%m-%d').date()

        score = peak_keys.get((sym, dt_iso, side))
        if score is None:
            continue

        label = W_TO_LABEL.get(w)
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
            elif ec is not None and abs(ec - t_stop) / max(0.01, abs(t_stop)) < 0.005:
                kind = 'stop'
            else:
                kind = 'expire'

        if ru is None:
            kind_u = None
        elif w == 1:
            kind_u = 'win' if ru == 1 else 'stop'
        else:
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
    return results, skipped


# ----- backends -----------------------------------------------------------------

def _build_peak_keys(peaks):
    peak_keys = {}
    for p in peaks:
        side = 'low' if p.overall >= 50 else 'high'
        d_iso = p.date.isoformat() if hasattr(p.date, 'isoformat') else p.date
        peak_keys[(p.symbol_id, d_iso, side)] = int(p.overall)
    return peak_keys


def _query_duckdb(con, peak_keys, barrier_set):
    """Common DuckDB query path: register peak_keys as a relation, JOIN, fetch."""
    if not peak_keys:
        return []
    # Build a list of (sym, date, side) tuples.
    rows = [(s, d, side) for (s, d, side) in peak_keys.keys()]
    # Register via DuckDB's pyarrow / list path. Easiest: use VALUES list.
    # For 50k+ peaks the VALUES literal is hot — use a registered
    # python list relation instead.
    con.register('_peaks_in', _PeakRelation(rows))
    try:
        cur = con.execute(f"""
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
        return cur.fetchall()
    finally:
        con.unregister('_peaks_in')


class _PeakRelation:
    """Tiny pyarrow-table wrapper so DuckDB can JOIN our peak list directly."""
    def __init__(self, rows):
        import pyarrow as pa
        self.table = pa.table({
            'symbol': [r[0] for r in rows],
            'date'  : [r[1] for r in rows],
            'side'  : [r[2] for r in rows],
        })
    def __arrow_c_stream__(self, requested_schema=None):
        return self.table.__arrow_c_stream__(requested_schema)


# Connection caches so repeated calls don't reopen the DB.
_DUCK_ATTACH_CON = None
_DUCK_NATIVE_CON = None
_DUCK_PARQUET_CON = None


def peaks_via_duck_attach(peaks, barrier_set: str = DEFAULT_BARRIER_SET):
    """Backend 2: DuckDB attaches the existing SQLite file."""
    global _DUCK_ATTACH_CON
    if _DUCK_ATTACH_CON is None:
        _DUCK_ATTACH_CON = duckdb.connect(":memory:")
        _DUCK_ATTACH_CON.execute("INSTALL sqlite; LOAD sqlite;")
        _DUCK_ATTACH_CON.execute(
            f"ATTACH '{CACHE_DB.as_posix()}' AS src (TYPE SQLITE, READ_ONLY);"
        )
        _DUCK_ATTACH_CON.execute(
            "CREATE OR REPLACE VIEW barrier_outcomes AS "
            "SELECT * FROM src.barrier_outcomes;"
        )
    if not peaks:
        return [], 0
    peak_keys = _build_peak_keys(peaks)
    rows = _query_duckdb(_DUCK_ATTACH_CON, peak_keys, barrier_set)
    return _format_results(rows, peak_keys, peaks, barrier_set)


def peaks_via_duck_native(peaks, barrier_set: str = DEFAULT_BARRIER_SET):
    """Backend 3: native DuckDB table."""
    global _DUCK_NATIVE_CON
    if _DUCK_NATIVE_CON is None:
        if not DUCK_NATIVE.exists():
            materialize_duck_native()
        _DUCK_NATIVE_CON = duckdb.connect(str(DUCK_NATIVE), read_only=True)
    if not peaks:
        return [], 0
    peak_keys = _build_peak_keys(peaks)
    rows = _query_duckdb(_DUCK_NATIVE_CON, peak_keys, barrier_set)
    return _format_results(rows, peak_keys, peaks, barrier_set)


def peaks_via_duck_parquet(peaks, barrier_set: str = DEFAULT_BARRIER_SET):
    """Backend 4: parquet via DuckDB."""
    global _DUCK_PARQUET_CON
    if _DUCK_PARQUET_CON is None:
        if not DUCK_PARQUET.exists():
            materialize_duck_parquet()
        _DUCK_PARQUET_CON = duckdb.connect(":memory:")
        _DUCK_PARQUET_CON.execute(
            f"CREATE OR REPLACE VIEW barrier_outcomes AS "
            f"SELECT * FROM read_parquet('{DUCK_PARQUET.as_posix()}');"
        )
    if not peaks:
        return [], 0
    peak_keys = _build_peak_keys(peaks)
    rows = _query_duckdb(_DUCK_PARQUET_CON, peak_keys, barrier_set)
    return _format_results(rows, peak_keys, peaks, barrier_set)
