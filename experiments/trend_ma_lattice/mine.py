"""Trend MA-lattice study -- Phase A read-only point-in-time mine (NO recalc, NO MC).

Implements GAMEPLAN.md sections 2-4 (A1 feature grid, A2 sub-term decomposition,
A3 judgment surface + partial-regression controls, A4 Bayesian layer).

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/trend_ma_lattice/mine.py --smoke
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/trend_ma_lattice/mine.py [--symbols N]

--smoke: ~20 liquid, long-history symbols, end-to-end (self-tests + tiny cohort table).
default: full universe of symbols with >=1 75+ CALL (v_active) signal in [2021-01-01, cutoff].
--symbols N: cap the full-universe symbol list to the first N (alphabetical).

Output: .cache/trend_ma_lattice/{ledger,features,cohort_stats}_v{ID}[_smoke].parquet
ASCII-only stdout (Windows cp1252 console -- no unicode). Ends with a delimited
==== SUMMARY ==== / ==== END SUMMARY ==== block.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections import namedtuple, defaultdict
from datetime import date

import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import features  # noqa: E402  (local module, experiments/trend_ma_lattice/features.py)
from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter, CUTOFF, cutoff_iso  # noqa: E402

OUT_DIR = os.path.join(_ROOT, ".cache", "trend_ma_lattice")
os.makedirs(OUT_DIR, exist_ok=True)

LEDGER_START = "2021-01-01"          # matches the A3 per-window split (2021..2025+)
PRICE_HISTORY_START = date(2010, 1, 1)  # ~11y buffer before LEDGER_START for EMA200 warmup

# --- barrier thresholds (sigma units) -- reused/consistent with experiments/component_reweight
# label.py + database/barrier_cache.py BARRIER_SETS['30dte_apex'] (TP=+30% -> 1.092s, SL=-70% -> 2.548s).
APEX_UP, APEX_DN = 1.092, 2.548      # "apex15": funded Core/Apex TP+30/SL-70 @ 15 calendar days
GEN_UP, GEN_DN = 1.414, 3.536        # "WR15": dashboard-generic barrier @ 15 calendar days (label.py gen15)
HOLD_W = 15
MAXW_CALDAYS = 20                    # forward-walk horizon (buffer past 15d for weekday/holiday gaps)
VOL_WINDOW, VOL_MIN = 60, 20         # realized-vol lookback (trading bars)

EV_WIN, EV_STOP, EV_EXPIRE = 0.30, -0.70, -0.40   # +TP/-SL/-expire per-trade pnl (apex_ev_of_parquet.py)

WINDOW_ORDER = ["2021", "2022", "2023", "2024", "2025+"]

SMOKE_SYMBOLS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "NFLX", "CRM",
                  "ADBE", "AVGO", "COST", "PEP", "JPM", "V", "MA", "UNH", "XOM", "WMT"]

MIN_N_POOLED = 30       # minimum pooled N to appear in the ranked cohort table
MIN_N_WINDOW = 10       # minimum per-window N to enter the Bayesian pooling / sign-consistency count
MIN_CELL_CONTROL = 5    # minimum N on EACH side (cohort / rest) to attempt controlled regression
Z_PREFILTER = 2.0       # stage-1 cheap screen before the expensive controls+Bayes treatment
N_MC = 10000            # Bayesian layer Monte Carlo draws

FAMILY_ABOVE = "above-below"
FAMILY_DIST = "dist"
FAMILY_SLOPE = "slope"
FAMILY_CROSS_STATE = "cross-state"
FAMILY_CROSS_FRESH = "cross-freshness"          # PRIMARY
FAMILY_STACK = "stack-alignment"
FAMILY_KDIV = "kernel-divergence"               # PRIMARY
FAMILY_TREND_LATTICE = "trend-interaction"
FAMILY_SUBTERM = "subterm-decomposition"        # PRIMARY
PRIMARY_FAMILIES = (FAMILY_KDIV, FAMILY_CROSS_FRESH, FAMILY_SUBTERM)


# =============================================================================
# CLI / version resolution
# =============================================================================
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="~20-symbol end-to-end smoke run")
    ap.add_argument("--symbols", type=int, default=None, help="cap full-universe symbol count")
    return ap.parse_args()


def resolve_active_version():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f"[version] active_scores_version id={av.id} commit={av.git_commit} label={av.production_label}")
    expected_commit = "f9fb7b934"
    if av.git_commit != expected_commit:
        print(f"[version] NOTE: active commit {av.git_commit} != v74 baseline {expected_commit} at time "
              f"this script was written -- proceeding with the CURRENT active version regardless "
              f"(the ledger is always built fresh against whatever is active; verify this is intended).")
    return int(av.id), av.git_commit


# =============================================================================
# Symbol universe + price history
# =============================================================================
def full_universe_symbols(version_id, cap=None):
    from database.trader_database import DB
    cur = DB.execute_sql(
        "SELECT DISTINCT symbol FROM scores WHERE version_id=%d AND overall>=75 "
        "AND date BETWEEN '%s' AND '%s' ORDER BY symbol" % (version_id, LEDGER_START, cutoff_iso())
    )
    syms = [r[0] for r in cur.fetchall()]
    if cap:
        syms = syms[:cap]
        print(f"[symbols] full-universe capped to first {cap} (alphabetical) of the 75+ CALL universe")
    return syms


def load_price_history(symbols, start=PRICE_HISTORY_START):
    """Bulk-load (dates, closes, highs, lows) per symbol from PriceHistory.
    Batched .in_() queries (not per-row FK access) -- avoids the peewee FK-per-row trap."""
    from database.models.technical import PriceHistory
    out = {}
    BATCH = 150
    t0 = time.time()
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        rows = list(PriceHistory.select(PriceHistory.symbol, PriceHistory.date, PriceHistory.close,
                                         PriceHistory.high, PriceHistory.low)
                    .where(PriceHistory.symbol.in_(batch), PriceHistory.date >= start)
                    .order_by(PriceHistory.symbol, PriceHistory.date)
                    .tuples())
        by_sym = defaultdict(list)
        for sym, d, c, h, l in rows:
            by_sym[sym].append((d, float(c), float(h), float(l)))
        for sym, recs in by_sym.items():
            recs.sort(key=lambda r: r[0])
            out[sym] = (
                [r[0] for r in recs],
                np.array([r[1] for r in recs], dtype=np.float64),
                np.array([r[2] for r in recs], dtype=np.float64),
                np.array([r[3] for r in recs], dtype=np.float64),
            )
    print(f"[price] loaded {len(out)}/{len(symbols)} symbols with PriceHistory in {time.time()-t0:.1f}s", flush=True)
    return out


# =============================================================================
# Forward-path walk + labels (barrier-agnostic style, cloned from
# experiments/component_reweight/build_ledger.py, simplified to just the
# thresholds this study needs).
# =============================================================================
def realized_vol(closes: np.ndarray, window=VOL_WINDOW, min_window=VOL_MIN) -> np.ndarray:
    """Causal rolling realized vol (sample std of simple returns), vectorized via cumsum."""
    n = len(closes)
    if n < 2:
        return np.full(n, np.nan)
    ret = np.zeros(n)
    ret[1:] = closes[1:] / closes[:-1] - 1.0
    csum = np.concatenate([[0.0], np.cumsum(ret)])
    csum2 = np.concatenate([[0.0], np.cumsum(ret ** 2)])
    idx = np.arange(n)
    lo = np.maximum(1, idx - window + 1)
    cnt = idx - lo + 1
    s = csum[idx + 1] - csum[lo]
    s2 = csum2[idx + 1] - csum2[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s / cnt
        var = (s2 - cnt * mean * mean) / np.maximum(cnt - 1, 1)
    sigma = np.sqrt(np.maximum(var, 0.0))
    return np.where(cnt >= min_window, sigma, np.nan)


def forward_touch(highs, lows, dates, i0, entry, sigma, thresholds_up, thresholds_dn, maxw_caldays):
    """Single forward walk resolving first-touch calendar-day for every requested
    up/dn sigma threshold simultaneously. Returns (t_up: {thr:caldays|None},
    t_dn: {thr:caldays|None}, fwd_caldays: int)."""
    t_up = {t: None for t in thresholds_up}
    t_dn = {t: None for t in thresholds_dn}
    remaining_up = set(thresholds_up)
    remaining_dn = set(thresholds_dn)
    base_date = dates[i0]
    cum_up = cum_dn = 0.0
    last_cd = 0
    n = len(dates)
    j = i0 + 1
    while j < n:
        cd = (dates[j] - base_date).days
        if cd > maxw_caldays:
            break
        up_ex = (highs[j] - entry) / entry / sigma
        dn_ex = (entry - lows[j]) / entry / sigma
        if up_ex > cum_up:
            cum_up = up_ex
        if dn_ex > cum_dn:
            cum_dn = dn_ex
        last_cd = cd
        if remaining_up:
            for t in list(remaining_up):
                if cum_up >= t:
                    t_up[t] = cd
                    remaining_up.discard(t)
        if remaining_dn:
            for t in list(remaining_dn):
                if cum_dn >= t:
                    t_dn[t] = cd
                    remaining_dn.discard(t)
        j += 1
        if not remaining_up and not remaining_dn and last_cd >= HOLD_W:
            break
    return t_up, t_dn, last_cd


def label_win_ev(t_up, t_dn, fwd_caldays, up_thr, dn_thr, W=HOLD_W, ev=False):
    """Same-bar-tie-loses convention (label.py win_expr). Returns (win 0/1/None[, ev])."""
    ttp = t_up.get(up_thr)
    tsl = t_dn.get(dn_thr)
    ttp_ok = ttp is not None and ttp <= W
    tsl_ok = tsl is not None and tsl <= W
    if ttp_ok and (not tsl_ok or ttp < tsl):
        return (1, EV_WIN) if ev else 1
    if tsl_ok and (not ttp_ok or tsl <= ttp):
        return (0, EV_STOP) if ev else 0
    if fwd_caldays >= W:
        return (0, EV_EXPIRE) if ev else 0
    return (None, None) if ev else None


# =============================================================================
# Ledger (A3 judgment surface, funded 75+ CALL v_active) -- scores query + labels
# =============================================================================
def query_scores(version_id, symbols):
    from database.bulk_cache import chunked_query_by_year
    sym_in = ",".join("'%s'" % s.replace("'", "") for s in symbols)
    template = ("SELECT symbol, date, overall, trend, pct_from_ema50, pct_from_ema200 FROM scores "
                "WHERE version_id={vid} AND overall>=75 AND symbol IN ({syms}) "
                "AND date BETWEEN '{y_start}' AND '{y_end}' AND date <= '{cutoff}'")
    rows = chunked_query_by_year(template, start_year=2021, end_year=2027,
                                  vid=version_id, syms=sym_in, cutoff=cutoff_iso(), verbose=True)
    return rows


def run_tag(smoke, capped, symbols):
    """Cache-slot tag. 'smoke' / 'full' are RESERVED for the canonical 20-symbol smoke
    universe and the genuine uncapped full universe respectively -- a --symbols N capped
    run (a partial universe) gets its own 'full_capN' slot so it can never be silently
    mistaken for -- or silently overwritten by -- the real full-universe cache. `capped`
    is whether --symbols was explicitly passed (NOT inferred from list length -- a cap
    that happens to land near or above the true universe size must still get its own slot)."""
    if smoke:
        return "smoke"
    return f"full_cap{len(symbols)}" if capped else "full"


def ledger_cache_path(version_id, tag):
    return os.path.join(OUT_DIR, f"ledger_v{version_id}_{tag}.parquet")


def build_ledger(version_id, version_commit, symbols, price_cache, tag, force=False):
    """Funded 75+ CALL ledger: scores + apex15/gen15 labels + apex_ev, cached to parquet
    keyed by version_id + run tag so a version bump (or a --symbols-capped partial run)
    never silently reuses another run's cache."""
    path = ledger_cache_path(version_id, tag)
    want_symbols = set(symbols)
    if os.path.exists(path) and not force:
        df = pl.read_parquet(path)
        # A symbol contributes zero rows to the ledger if it never fired a 75+ CALL
        # signal, so "every scored symbol" != "every symbol with a ledger row" --
        # validate against the requested universe (want_symbols), not the ledger's
        # own distinct-symbol column, which would false-negative on that gap.
        cached_meta = set(df["_cached_symbols"][0].split(",")) if df.height and "_cached_symbols" in df.columns else None
        symbol_match = cached_meta == want_symbols if cached_meta is not None else False
        if df.height and df["version_id"][0] == version_id and symbol_match:
            print(f"[ledger] cache hit: {path} rows={df.height} version_id={version_id} "
                  f"(commit={version_commit})")
            return df.drop("_cached_symbols") if "_cached_symbols" in df.columns else df
        print(f"[ledger] cache STALE (version or symbol-set mismatch) -- rebuilding: {path}")

    t0 = time.time()
    score_rows = query_scores(version_id, symbols)
    print(f"[ledger] pulled {len(score_rows)} 75+ CALL score rows for version_id={version_id} "
          f"(commit={version_commit}) in {time.time()-t0:.1f}s", flush=True)

    by_sym = defaultdict(list)
    for sym, d, ov, tr, p50, p200 in score_rows:
        by_sym[sym].append((d, int(ov), int(tr) if tr is not None else None,
                             float(p50) if p50 is not None else None,
                             float(p200) if p200 is not None else None))

    recs = []
    for sym, sig_rows in by_sym.items():
        pc = price_cache.get(sym)
        if pc is None:
            continue
        dates, closes, highs, lows = pc
        date_to_idx = {d: i for i, d in enumerate(dates)}
        sigma_arr = realized_vol(closes)
        for (d, ov, tr, p50, p200) in sig_rows:
            idx = date_to_idx.get(d)
            if idx is None:
                continue
            sigma = sigma_arr[idx]
            entry = closes[idx]
            apex_win = apex_ev = gen_win = None
            fwd_cd = None
            if sigma and not math.isnan(sigma) and sigma > 0 and entry > 0:
                t_up, t_dn, fwd_cd = forward_touch(highs, lows, dates, idx, entry, sigma,
                                                    (APEX_UP, GEN_UP), (APEX_DN, GEN_DN), MAXW_CALDAYS)
                apex_win, apex_ev = label_win_ev(t_up, t_dn, fwd_cd, APEX_UP, APEX_DN, ev=True)
                gen_win = label_win_ev(t_up, t_dn, fwd_cd, GEN_UP, GEN_DN, ev=False)
            recs.append({
                "symbol": sym, "date": d, "overall": ov, "trend": tr,
                "pct_from_ema50": p50, "pct_from_ema200": p200,
                "apex_win": apex_win, "apex_ev": apex_ev, "gen_win": gen_win,
                "fwd_caldays": fwd_cd, "_idx": idx, "version_id": version_id,
            })

    if not recs:
        # Empty schema guard: add_window_column needs a 'date' column to exist even
        # when the requested symbol set produced zero 75+ CALL signals (e.g. a small
        # --symbols cap). main() checks height==0 right after this returns.
        df = pl.DataFrame({"symbol": [], "date": [], "overall": [], "trend": [],
                            "pct_from_ema50": [], "pct_from_ema200": [], "apex_win": [],
                            "apex_ev": [], "gen_win": [], "fwd_caldays": [], "_idx": [],
                            "version_id": []}, schema={"symbol": pl.Utf8, "date": pl.Date,
                            "overall": pl.Int64, "trend": pl.Int64, "pct_from_ema50": pl.Float64,
                            "pct_from_ema200": pl.Float64, "apex_win": pl.Int64, "apex_ev": pl.Float64,
                            "gen_win": pl.Int64, "fwd_caldays": pl.Int64, "_idx": pl.Int64,
                            "version_id": pl.Int64})
    else:
        df = pl.DataFrame(recs, infer_schema_length=None)  # G7: None-early/float-later cols
    df = add_window_column(df)
    cached_symbols_str = ",".join(sorted(want_symbols))
    df = df.with_columns(pl.lit(cached_symbols_str).alias("_cached_symbols"))
    df.write_parquet(path)
    print(f"[ledger] built {df.height} rows -> {path} ({time.time()-t0:.1f}s total)", flush=True)
    return df.drop("_cached_symbols")


def add_window_column(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col("date") >= date(2025, 1, 1)).then(pl.lit("2025+"))
          .when(pl.col("date") >= date(2024, 1, 1)).then(pl.lit("2024"))
          .when(pl.col("date") >= date(2023, 1, 1)).then(pl.lit("2023"))
          .when(pl.col("date") >= date(2022, 1, 1)).then(pl.lit("2022"))
          .when(pl.col("date") >= date(2021, 1, 1)).then(pl.lit("2021"))
          .otherwise(None)
          .alias("window")
    )


# =============================================================================
# Feature frame (A1 grid + A2 sub-term decomposition), joined onto the ledger
# =============================================================================
def _back(arr, idx, back):
    j = idx - back
    if j < 0 or j >= len(arr):
        return float("nan")
    return arr[j]


def build_feature_frame(ledger_df: pl.DataFrame, price_cache: dict) -> pl.DataFrame:
    """Streams symbol-by-symbol: builds the causal series once, extracts every
    signal row for that symbol, then discards the series before moving on
    (memory: keeps only the small OHLC arrays resident, not the ~125 derived
    per-symbol feature arrays for the whole universe at once)."""
    t0 = time.time()
    by_sym = defaultdict(list)
    for r in ledger_df.iter_rows(named=True):
        by_sym[r["symbol"]].append(r)

    rows = []
    n_syms = 0
    for sym, sig_rows in by_sym.items():
        pc = price_cache.get(sym)
        if pc is None:
            continue
        dates, closes, highs, lows = pc
        series = features.build_symbol_series(closes)
        ma = series["ma"]
        for r in sig_rows:
            idx = r["_idx"]
            frow = features.extract_row(idx, series)

            ema8, ema13, ema21, ema34 = ma[("EMA", 8)][idx], ma[("EMA", 13)][idx], ma[("EMA", 21)][idx], ma[("EMA", 34)][idx]
            ema50, ema100, ema200 = ma[("EMA", 50)][idx], ma[("EMA", 100)][idx], ma[("EMA", 200)][idx]
            close = closes[idx]
            ema21_m10 = _back(ma[("EMA", 21)], idx, 10)
            ema50_m20 = _back(ma[("EMA", 50)], idx, 20)
            trend_recon, pos_s, align_s, mom_s, macro_s = features.recompute_trend(
                ema21, ema50, ema200, close, ema21_m10, ema50_m20)

            # horizon-variant sub-terms (A2 "per horizon-variant") -- diagnostic only
            pos_alt = features.position_alt(ema21, ema100, close)
            align_fast = features.alignment_alt(ema13, ema34)
            align_slow = features.alignment_alt(ema50, ema100)
            mom_fast = features.momentum_alt(ema13, _back(ma[("EMA", 13)], idx, 5),
                                              ema34, _back(ma[("EMA", 34)], idx, 10))
            mom_slow = features.momentum_alt(ema34, _back(ma[("EMA", 34)], idx, 20),
                                              ema100, _back(ma[("EMA", 100)], idx, 40))
            macro_alt = features.macro_alt(ema100, ema200)

            row = dict(frow)
            row.update({
                "symbol": sym, "date": r["date"], "overall": r["overall"], "trend": r["trend"],
                "pct_from_ema50": r["pct_from_ema50"], "pct_from_ema200": r["pct_from_ema200"],
                "window": r["window"], "apex_win": r["apex_win"], "apex_ev": r["apex_ev"],
                "gen_win": r["gen_win"], "fwd_caldays": r["fwd_caldays"], "_idx": idx,
                "trend_recon": trend_recon,
                "subterm_position": pos_s, "subterm_alignment": align_s,
                "subterm_momentum": mom_s, "subterm_macro": macro_s,
                "subterm_position_alt": pos_alt,
                "subterm_alignment_alt_fast": align_fast, "subterm_alignment_alt_slow": align_slow,
                "subterm_momentum_alt_fast": mom_fast, "subterm_momentum_alt_slow": mom_slow,
                "subterm_macro_alt": macro_alt,
            })
            rows.append(row)
        del series
        n_syms += 1

    df = pl.DataFrame(rows, infer_schema_length=None)  # G7
    print(f"[features] built {df.height} rows x {df.width} cols across {n_syms} symbols "
          f"in {time.time()-t0:.1f}s", flush=True)
    return df


# =============================================================================
# Self-tests (mandatory, run at top of every invocation, cheap samples)
# =============================================================================
def sample_selftest_rows(version_id, symbols, n=800):
    """Independent of the 75+ CALL ledger -- pulls ANY scored (symbol,date,trend)
    row for these symbols so self-tests 1-3 have >=500 rows even in --smoke mode
    (formula correctness doesn't need to be restricted to the cohort population)."""
    from database.trader_database import DB
    sym_in = ",".join("'%s'" % s.replace("'", "") for s in symbols)
    sql = ("SELECT symbol, date, trend FROM scores WHERE version_id=%d AND symbol IN (%s) "
           "AND date BETWEEN '%s' AND '%s' ORDER BY RAND() LIMIT %d"
           % (version_id, sym_in, LEDGER_START, cutoff_iso(), n))
    rows = DB.execute_sql(sql).fetchall()
    return [(s, d, (int(t) if t is not None else None)) for s, d, t in rows]


def selftest_ema_sma(sample_rows, price_cache, n_target=500):
    from database.models.technical import Indicator
    syms = sorted({s for s, _, _ in sample_rows})
    # Bound by the same [LEDGER_START, cutoff] window sample_selftest_rows queried --
    # without this, a symbol with a decade+ of Indicator history (e.g. XOM back to the
    # 1960s) pulls its ENTIRE row set just to look up a handful of dates in the sample.
    ledger_start_date = date.fromisoformat(LEDGER_START)
    stored = {}
    BATCH = 150
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        for sym, d, e21, e50, e200, m21, m50, m200 in (
            Indicator.select(Indicator.symbol, Indicator.date, Indicator.ema_21, Indicator.ema_50,
                              Indicator.ema_200, Indicator.ma_21, Indicator.ma_50, Indicator.ma_200)
            .where(Indicator.symbol.in_(batch), Indicator.date >= ledger_start_date,
                   Indicator.date <= CUTOFF).tuples()
        ):
            stored[(sym, d)] = (e21, e50, e200, m21, m50, m200)

    checked = 0
    failures = []
    rows_used = 0
    for sym, d, _ in sample_rows:
        pc = price_cache.get(sym)
        st = stored.get((sym, d))
        if pc is None or st is None:
            continue
        dates, closes, _, _ = pc
        idx_map = {dt: i for i, dt in enumerate(dates)}
        idx = idx_map.get(d)
        if idx is None:
            continue
        rows_used += 1
        ema21 = talib_val(closes, "EMA", 21, idx)
        ema50 = talib_val(closes, "EMA", 50, idx)
        ema200 = talib_val(closes, "EMA", 200, idx)
        sma21 = talib_val(closes, "SMA", 21, idx)
        sma50 = talib_val(closes, "SMA", 50, idx)
        sma200 = talib_val(closes, "SMA", 200, idx)
        pairs = [("ema_21", ema21, st[0]), ("ema_50", ema50, st[1]), ("ema_200", ema200, st[2]),
                 ("ma_21", sma21, st[3]), ("ma_50", sma50, st[4]), ("ma_200", sma200, st[5])]
        for name, mine, stored_v in pairs:
            if stored_v is None or mine is None or math.isnan(mine):
                continue
            stored_v = float(stored_v)
            rel = abs(mine - stored_v) / max(abs(stored_v), 1e-6)
            checked += 1
            if rel > 1e-3:
                failures.append((sym, d, name, mine, stored_v, rel))
        if rows_used >= n_target:
            break

    pass_rate = 1.0 - (len(failures) / checked if checked else 1.0)
    ok = checked >= 200 and pass_rate >= 0.98
    print(f"[selftest1 ema_sma] rows_used={rows_used} field_checks={checked} failures={len(failures)} "
          f"pass_rate={pass_rate*100:.2f}% -> {'PASS' if ok else 'FAIL'}")
    for f in failures[:5]:
        print(f"    mismatch sym={f[0]} date={f[1]} field={f[2]} mine={f[3]:.4f} stored={f[4]:.4f} rel={f[5]:.5f}")
    return ok


_MA_CACHE = {}


def talib_val(closes, kernel, period, idx):
    key = (id(closes), kernel, period)
    arr = _MA_CACHE.get(key)
    if arr is None:
        arr = features.talib_ma(closes, kernel, period)
        _MA_CACHE[key] = arr
    v = arr[idx]
    return None if math.isnan(v) else float(v)


def selftest_lookahead(feat_frame: pl.DataFrame, price_cache, n_sample=50, seed=7):
    rng = np.random.default_rng(seed)
    eligible = feat_frame.filter(pl.col("_idx") >= 60)
    if eligible.height == 0:
        print("[selftest2 lookahead] no eligible rows -> FAIL")
        return False
    take = min(n_sample, eligible.height)
    pick = rng.choice(eligible.height, size=take, replace=False).tolist()
    sample = eligible[pick]
    cols = features.feature_columns()
    mismatches = []
    checked = 0
    for r in sample.iter_rows(named=True):
        sym, idx = r["symbol"], r["_idx"]
        _, closes, _, _ = price_cache[sym]
        series_full = features.build_symbol_series(closes)
        series_trunc = features.build_symbol_series(closes[: idx + 1])
        row_full = features.extract_row(idx, series_full)
        row_trunc = features.extract_row(idx, series_trunc)
        for c in cols:
            a, b = row_full[c], row_trunc[c]
            checked += 1
            a_nan, b_nan = math.isnan(a), math.isnan(b)
            if a_nan and b_nan:
                continue
            if a_nan != b_nan or abs(a - b) > 1e-6:
                mismatches.append((sym, r["date"], c, a, b))
    ok = checked > 0 and len(mismatches) == 0
    print(f"[selftest2 lookahead] pairs={take} feature_checks={checked} mismatches={len(mismatches)} "
          f"-> {'PASS' if ok else 'FAIL'}")
    for m in mismatches[:8]:
        print(f"    LOOK-AHEAD LEAK sym={m[0]} date={m[1]} col={m[2]} full={m[3]} trunc={m[4]}")
    return ok


def selftest_trend_recomposition(sample_rows, price_cache, n_target=500):
    checked = 0
    diffs = []
    bad = []
    for sym, d, stored_trend in sample_rows:
        if stored_trend is None:
            continue
        pc = price_cache.get(sym)
        if pc is None:
            continue
        dates, closes, _, _ = pc
        idx_map = {dt: i for i, dt in enumerate(dates)}
        idx = idx_map.get(d)
        if idx is None:
            continue
        ema21 = talib_val(closes, "EMA", 21, idx)
        ema50 = talib_val(closes, "EMA", 50, idx)
        ema200 = talib_val(closes, "EMA", 200, idx)
        ema21_m10 = talib_val(closes, "EMA", 21, idx - 10) if idx >= 10 else None
        ema50_m20 = talib_val(closes, "EMA", 50, idx - 20) if idx >= 20 else None
        trend_recon, *_ = features.recompute_trend(ema21, ema50, ema200, closes[idx], ema21_m10, ema50_m20)
        if trend_recon is None:
            continue
        checked += 1
        d_abs = abs(trend_recon - stored_trend)
        diffs.append(d_abs)
        if d_abs > 1:
            bad.append((sym, d, stored_trend, trend_recon))
        if checked >= int(n_target * 1.5):
            break
    ok = checked >= 500 and len(bad) == 0
    max_diff = max(diffs) if diffs else None
    print(f"[selftest3 trend_recon] checked={checked} within_pm1={checked-len(bad)} max_abs_diff={max_diff} "
          f"-> {'PASS' if ok else 'FAIL'}")
    for b in bad[:8]:
        print(f"    mismatch sym={b[0]} date={b[1]} stored={b[2]} mine={b[3]}")
    if checked < 500:
        print(f"    NOTE: only {checked} rows had computable trend_recon (need >=500) -- widen sample_selftest_rows(n=...)")
    return ok


def selftest_holdout(ledger_df, feat_frame):
    try:
        assert_no_holdout_leak(ledger_df, context="trend_ma_lattice ledger")
        assert_no_holdout_leak(feat_frame, context="trend_ma_lattice feat_frame")
    except AssertionError as e:
        print(f"[selftest4 holdout] FAIL: {e}")
        return False
    max_d = feat_frame["date"].max() if feat_frame.height else None
    print(f"[selftest4 holdout] max_date={max_d} cutoff={CUTOFF.isoformat()} -> PASS")
    return True


# =============================================================================
# Replication surface (full-universe 75+ barrier_outcomes DuckDB mirror)
# =============================================================================
def build_replication_surface(feat_frame: pl.DataFrame) -> pl.DataFrame:
    from database.barrier_cache import peaks_to_swing_results
    Peak = namedtuple("Peak", "symbol_id date overall")
    uniq = feat_frame.select(["symbol", "date", "overall"]).unique()
    peaks = [Peak(r["symbol"], r["date"], int(r["overall"])) for r in uniq.iter_rows(named=True)]
    if not peaks:
        return pl.DataFrame({"symbol": [], "date": [], "repl_win": [], "repl_ev": []})
    t0 = time.time()
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    EV_MAP = {"win": EV_WIN, "stop": EV_STOP, "expire": EV_EXPIRE}
    rows = []
    for r in results:
        sw = (r.get("swing") or {}).get("15d")
        if not sw:
            continue
        kind = sw.get("result")
        if kind not in EV_MAP:
            continue
        rows.append({"symbol": r["symbol"], "date": r["date"],
                     "repl_win": 1 if kind == "win" else 0, "repl_ev": EV_MAP[kind]})
    print(f"[replication] barrier_set=30dte_apex period=15d(scaled sqrt(15/30) vs funded ledger's FIXED "
          f"1.092/2.548 -- see deviations) matched={len(rows)}/{uniq.height} skipped={skipped} "
          f"in {time.time()-t0:.1f}s", flush=True)
    if not rows:
        return pl.DataFrame({"symbol": [], "date": [], "repl_win": [], "repl_ev": []})
    return pl.DataFrame(rows, infer_schema_length=None)  # G7


# =============================================================================
# A3/A4: bucket columns, cohort scan, partial-regression controls, Bayesian layer
# =============================================================================
def _bucket4(colname, e0=40, e1=55, e2=70):
    c = pl.col(colname)
    return (pl.when(c < e0).then(pl.lit(f"lt{e0}"))
              .when(c < e1).then(pl.lit(f"{e0}-{e1}"))
              .when(c < e2).then(pl.lit(f"{e1}-{e2}"))
              .when(c.is_not_null()).then(pl.lit(f"ge{e2}"))
              .otherwise(None))


def _dist_bucket(colname):
    c = pl.col(colname)
    return (pl.when(c < -10).then(pl.lit("lt-10"))
              .when(c < 0).then(pl.lit("m10-0"))
              .when(c < 10).then(pl.lit("0-10"))
              .when(c.is_not_null()).then(pl.lit("ge10"))
              .otherwise(None))


def _days_bucket(colname):
    c = pl.col(colname)
    return (pl.when(c <= 5).then(pl.lit("0-5"))
              .when(c <= 15).then(pl.lit("6-15"))
              .when(c <= 30).then(pl.lit("16-30"))
              .when(c.is_not_null()).then(pl.lit("31-60"))
              .otherwise(None))


def build_bucket_specs():
    """Returns list of (colname, family, polars_expr) for every cohort cell axis."""
    specs = []
    for kernel in features.KERNELS:
        for period in features.PERIODS:
            tag = f"{kernel}_{period}"
            specs.append((f"b_above_{tag}", FAMILY_ABOVE,
                          pl.when(pl.col(f"above_{tag}") == 1.0).then(pl.lit("above"))
                            .when(pl.col(f"above_{tag}") == 0.0).then(pl.lit("below"))
                            .otherwise(None)))
            specs.append((f"b_dist_{tag}", FAMILY_DIST, _dist_bucket(f"dist_{tag}")))
            specs.append((f"b_slope_{tag}", FAMILY_SLOPE,
                          pl.when(pl.col(f"slope_{tag}") > 0).then(pl.lit("up"))
                            .when(pl.col(f"slope_{tag}") < 0).then(pl.lit("down"))
                            .when(pl.col(f"slope_{tag}") == 0).then(pl.lit("flat"))
                            .otherwise(None)))
    for kernel in features.KERNELS:
        for (pf, ps) in features.CROSS_PAIRS:
            tag = f"{kernel}_{pf}_{ps}"
            specs.append((f"b_cstate_{tag}", FAMILY_CROSS_STATE,
                          pl.when(pl.col(f"cstate_{tag}") == 1.0).then(pl.lit("fast_above"))
                            .when(pl.col(f"cstate_{tag}") == 0.0).then(pl.lit("fast_below"))
                            .otherwise(None)))
            specs.append((f"b_cdays_{tag}", FAMILY_CROSS_FRESH, _days_bucket(f"cdays_{tag}")))
            specs.append((f"b_cfg_{tag}", FAMILY_CROSS_FRESH,
                          pl.when(pl.col(f"cfg_{tag}") == 1.0).then(pl.lit("fresh_golden"))
                            .when(pl.col(f"cfg_{tag}").is_not_null()).then(pl.lit("no"))
                            .otherwise(None)))
            specs.append((f"b_cfd_{tag}", FAMILY_CROSS_FRESH,
                          pl.when(pl.col(f"cfd_{tag}") == 1.0).then(pl.lit("fresh_death"))
                            .when(pl.col(f"cfd_{tag}").is_not_null()).then(pl.lit("no"))
                            .otherwise(None)))
    for kernel in features.KERNELS:
        specs.append((f"b_stack_{kernel}", FAMILY_STACK,
                      pl.when(pl.col(f"stack_{kernel}").is_not_null())
                        .then(pl.col(f"stack_{kernel}").cast(pl.Int32).cast(pl.Utf8))
                        .otherwise(None)))
    for period in features.DIVERGENCE_PERIODS:
        kc = f"kdiv_{period}"
        specs.append((f"b_kdivsign_{period}", FAMILY_KDIV,
                      pl.when(pl.col(kc) > 0.15).then(pl.lit("pos"))
                        .when(pl.col(kc) < -0.15).then(pl.lit("neg"))
                        .when(pl.col(kc).is_not_null()).then(pl.lit("flat"))
                        .otherwise(None)))
        specs.append((f"b_kdivmag_{period}", FAMILY_KDIV,
                      pl.when(pl.col(kc).abs() < 0.3).then(pl.lit("tiny"))
                        .when(pl.col(kc).abs() < 1.0).then(pl.lit("mild"))
                        .when(pl.col(kc).abs() < 2.5).then(pl.lit("notable"))
                        .when(pl.col(kc).is_not_null()).then(pl.lit("extreme"))
                        .otherwise(None)))
    specs.append(("b_trend_bucket", FAMILY_TREND_LATTICE, _bucket4("trend")))
    specs.append(("b_disagree_hi_below_ema200", FAMILY_TREND_LATTICE,
                  pl.when((pl.col("trend") >= 70) & (pl.col("above_EMA_200") == 0.0)).then(pl.lit("disagree"))
                    .when(pl.col("above_EMA_200").is_not_null()).then(pl.lit("agree_or_na"))
                    .otherwise(None)))
    specs.append(("b_disagree_lo_above_ema200", FAMILY_TREND_LATTICE,
                  pl.when((pl.col("trend") < 40) & (pl.col("above_EMA_200") == 1.0)).then(pl.lit("disagree"))
                    .when(pl.col("above_EMA_200").is_not_null()).then(pl.lit("agree_or_na"))
                    .otherwise(None)))
    specs.append(("b_disagree_hi_below_sma200", FAMILY_TREND_LATTICE,
                  pl.when((pl.col("trend") >= 70) & (pl.col("above_SMA_200") == 0.0)).then(pl.lit("disagree"))
                    .when(pl.col("above_SMA_200").is_not_null()).then(pl.lit("agree_or_na"))
                    .otherwise(None)))
    specs.append(("b_disagree_hi_macrobear", FAMILY_TREND_LATTICE,
                  pl.when((pl.col("trend") >= 70) & (pl.col("ma_EMA_50") < pl.col("ma_EMA_200"))).then(pl.lit("disagree"))
                    .when(pl.col("ma_EMA_200").is_not_null()).then(pl.lit("agree_or_na"))
                    .otherwise(None)))
    specs.append(("b_trend_x_stackEMA", FAMILY_TREND_LATTICE,
                  pl.when(pl.col("stack_EMA").is_not_null())
                    .then(_bucket4("trend") + pl.lit("_stk") + pl.col("stack_EMA").cast(pl.Int32).cast(pl.Utf8))
                    .otherwise(None)))
    for name in ["subterm_position", "subterm_alignment", "subterm_momentum", "subterm_macro",
                 "subterm_position_alt", "subterm_alignment_alt_fast", "subterm_alignment_alt_slow",
                 "subterm_momentum_alt_fast", "subterm_momentum_alt_slow", "subterm_macro_alt"]:
        specs.append((f"b_{name}", FAMILY_SUBTERM, _bucket4(name)))
    return specs


def add_bucket_columns(df: pl.DataFrame, specs) -> pl.DataFrame:
    exprs = [expr.alias(colname) for colname, _family, expr in specs]
    return df.with_columns(exprs)


# --- numpy-only OLS (HC1 robust SE) + logistic IRLS -------------------------
def _standardize(x):
    finite = x[~np.isnan(x)]
    if finite.size == 0:
        return np.zeros_like(x)
    mu, sd = finite.mean(), finite.std()
    if not sd or math.isnan(sd) or sd < 1e-9:
        sd = 1.0
    return np.where(np.isnan(x), 0.0, (x - mu) / sd)


def ols_robust(X, y):
    XtX = X.T @ X
    XtX_inv = np.linalg.pinv(XtX)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    n, k = X.shape
    meat = (X * (resid ** 2)[:, None]).T @ X
    cov = XtX_inv @ meat @ XtX_inv * (n / max(n - k, 1))
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 1e-12, beta / se, 0.0)
    return beta, se, t


def logit_irls(X, y, max_iter=25, tol=1e-8, ridge=1e-6):
    n, k = X.shape
    beta = np.zeros(k)
    for _ in range(max_iter):
        eta = np.clip(X @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(p * (1 - p), 1e-6, None)
        z = eta + (y - p) / w
        XtWX = (X * w[:, None]).T @ X + ridge * np.eye(k)
        XtWz = (X * w[:, None]).T @ z
        beta_new = np.linalg.solve(XtWX, XtWz)
        if np.max(np.abs(beta_new - beta)) < tol:
            beta = beta_new
            break
        beta = beta_new
    eta = np.clip(X @ beta, -30, 30)
    p = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(p * (1 - p), 1e-6, None)
    XtWX = (X * w[:, None]).T @ X + ridge * np.eye(k)
    cov = np.linalg.pinv(XtWX)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 1e-12, beta / se, 0.0)
    return beta, se, t


def controlled_t(mask, y_wr, y_ev, trend_z, p50_z, p200_z):
    """Returns (t_wr_controlled, t_ev_controlled) -- dummy-variable logit/OLS of
    win/EV on [intercept, cohort_dummy, trend, pct_from_ema50, pct_from_ema200]."""
    m = mask.astype(float)
    X = np.column_stack([np.ones(len(m)), m, trend_z, p50_z, p200_z])
    t_wr = t_ev = float("nan")
    # rows with NaN regressors (e.g. dist_EMA_200 null on <200-bar listings) must be
    # DROPPED, not passed to IRLS/OLS -- a NaN X poisons the fit and the try/except
    # would silently blank t_controlled (false-NULL on the PRIMARY gate).
    finX = np.isfinite(X).all(axis=1)
    ok_wr = (~np.isnan(y_wr)) & finX
    if ok_wr.sum() >= 2 * MIN_CELL_CONTROL and m[ok_wr].sum() >= MIN_CELL_CONTROL and \
       (ok_wr.sum() - m[ok_wr].sum()) >= MIN_CELL_CONTROL:
        try:
            _, _, t = logit_irls(X[ok_wr], y_wr[ok_wr])
            t_wr = float(t[1])
        except Exception:
            pass
    ok_ev = (~np.isnan(y_ev)) & finX
    if ok_ev.sum() >= 2 * MIN_CELL_CONTROL and m[ok_ev].sum() >= MIN_CELL_CONTROL and \
       (ok_ev.sum() - m[ok_ev].sum()) >= MIN_CELL_CONTROL:
        try:
            _, _, t = ols_robust(X[ok_ev], y_ev[ok_ev])
            t_ev = float(t[1])
        except Exception:
            pass
    return t_wr, t_ev


# --- A4 Bayesian layer (numpy only) ------------------------------------------
def bayes_layer(per_window, metric, rng, n_mc=N_MC):
    """per_window: list of dicts, each either {n,wins,base_n,base_wins} (metric='wr')
    or {n,mean,se,base_mean,base_se} (metric='ev'). Windows with n < MIN_N_WINDOW are
    dropped. Returns None if fewer than 2 windows qualify, else a stats dict."""
    ws = [w for w in per_window if w["n"] >= MIN_N_WINDOW and w.get("base_n", 0) >= MIN_N_WINDOW]
    if len(ws) < 2:
        return None
    draws, points = [], []
    for w in ws:
        if metric == "wr":
            d = rng.beta(0.5 + w["wins"], 0.5 + (w["n"] - w["wins"]), size=n_mc)
            bd = rng.beta(0.5 + w["base_wins"], 0.5 + (w["base_n"] - w["base_wins"]), size=n_mc)
            point = (w["wins"] / w["n"]) - (w["base_wins"] / w["base_n"])
        else:
            se = max(w["se"], 1e-9)
            bse = max(w["base_se"], 1e-9)
            d = rng.normal(w["mean"], se, size=n_mc)
            bd = rng.normal(w["base_mean"], bse, size=n_mc)
            point = w["mean"] - w["base_mean"]
        draws.append(d - bd)
        points.append(point)
    D = np.array(draws)               # (n_windows, n_mc)
    points = np.array(points)
    within_var = D.var(axis=1)
    tau2 = max(0.0, float(np.var(points, ddof=1) - within_var.mean())) if len(points) > 1 else 0.0
    tau = math.sqrt(tau2)
    noise = rng.normal(0.0, tau, size=D.shape) if tau > 0 else np.zeros_like(D)
    Dn = D + noise
    pooled = Dn.mean(axis=0)          # (n_mc,)
    P_pooled_gt0 = float((pooled > 0).mean())
    pooled_sign = np.sign(pooled)
    per_window_sign = np.sign(Dn)
    agree_frac = (per_window_sign == pooled_sign[None, :]).mean(axis=0)
    P_sign_consistent = float((agree_frac >= 0.8 - 1e-9).mean())
    return dict(pooled_delta=float(pooled.mean()), P_pooled_gt0=P_pooled_gt0,
                P_sign_consistent=P_sign_consistent, tau=tau, n_windows=len(ws),
                point_deltas={"n": len(ws)})


def window_signs(per_window):
    """'+'/'-'/'.' string, one char per WINDOW_ORDER entry ('.' = insufficient N)."""
    by_w = {w["label"]: w for w in per_window}
    out = []
    for lbl in WINDOW_ORDER:
        w = by_w.get(lbl)
        if w is None or w["n"] < MIN_N_WINDOW or w.get("base_n", 0) < MIN_N_WINDOW:
            out.append(".")
            continue
        delta = w.get("delta")
        out.append("+" if delta is not None and delta > 0 else ("-" if delta is not None else "."))
    return "".join(out)


def run_cohort_scan(feat_frame: pl.DataFrame, specs, rng):
    n = feat_frame.height
    y_wr = feat_frame["apex_win"].to_numpy().astype(float)
    y_ev = feat_frame["apex_ev"].to_numpy().astype(float)
    y_repl_wr = feat_frame["repl_win"].to_numpy().astype(float) if "repl_win" in feat_frame.columns else np.full(n, np.nan)
    y_repl_ev = feat_frame["repl_ev"].to_numpy().astype(float) if "repl_ev" in feat_frame.columns else np.full(n, np.nan)
    trend_raw = feat_frame["trend"].to_numpy().astype(float)
    # pct_from_ema50/200 is stored-NULL for the vast majority of backfilled/recalculated score
    # rows (only the live `trader update` path populates it) -- control_pct_ema50/200 (built in
    # main()) coalesces the stored value with our own validated dist_EMA_50/200 (identical
    # formula, see selftest1) so the controls are actually populated. See deviations note.
    p50_raw = feat_frame["control_pct_ema50"].to_numpy().astype(float)
    p200_raw = feat_frame["control_pct_ema200"].to_numpy().astype(float)
    trend_z, p50_z, p200_z = _standardize(trend_raw), _standardize(p50_raw), _standardize(p200_raw)
    window_arr = feat_frame["window"].to_numpy()

    base_wins = np.nansum(y_wr); base_n = np.sum(~np.isnan(y_wr))
    base_wr = base_wins / base_n if base_n else float("nan")
    base_ev_mean = np.nanmean(y_ev)
    base_ev_by_window = {}
    base_wr_by_window = {}
    for lbl in WINDOW_ORDER:
        mw = window_arr == lbl
        wr_w = y_wr[mw]
        ev_w = y_ev[mw]
        nwr = int(np.sum(~np.isnan(wr_w)))
        base_wr_by_window[lbl] = (int(np.nansum(wr_w)), nwr)
        nev = int(np.sum(~np.isnan(ev_w)))
        base_ev_by_window[lbl] = (float(np.nanmean(ev_w)) if nev else float("nan"),
                                   float(np.nanstd(ev_w) / math.sqrt(nev)) if nev > 1 else float("nan"), nev)

    results = []
    for colname, family, _expr in specs:
        if colname not in feat_frame.columns:
            continue
        col = feat_frame[colname].to_numpy()
        # set-based: np.unique sort dies on None-vs-str at full scale (young listings)
        cats = sorted({v for v in col.astype(object).tolist() if v is not None})
        for cell in cats:
            mask = (col == cell)
            ncell = int(mask.sum())
            if ncell < MIN_N_POOLED:
                continue
            wins = int(np.nansum(y_wr[mask]))
            resolved_n = int(np.sum(~np.isnan(y_wr[mask])))
            if resolved_n < MIN_N_POOLED:
                continue
            wr_cell = wins / resolved_n
            ev_cell = float(np.nanmean(y_ev[mask]))
            z_raw = ((wr_cell - base_wr) / math.sqrt(base_wr * (1 - base_wr) / resolved_n)
                     if 0 < base_wr < 1 and resolved_n > 0 else 0.0)
            # stage-1 cheap prefilter -- always keep PRIMARY families for full A2-A4 treatment
            if abs(z_raw) < Z_PREFILTER and family not in PRIMARY_FAMILIES:
                results.append(dict(family=family, cell=f"{colname}={cell}", n=ncell, resolved_n=resolved_n,
                                     apex_wr=wr_cell, apex_wr_delta=wr_cell - base_wr, apex_ev=ev_cell,
                                     apex_ev_delta=ev_cell - base_ev_mean, z_raw=z_raw, t_controlled=float("nan"),
                                     t_controlled_wr=float("nan"), P_pooled=float("nan"),
                                     P_sign_consistent=float("nan"), signs="......",
                                     replicates=None, verdict="screened_out"))
                continue

            t_wr, t_ev = controlled_t(mask, y_wr, y_ev, trend_z, p50_z, p200_z)

            per_window_wr, per_window_ev = [], []
            for lbl in WINDOW_ORDER:
                mw = mask & (window_arr == lbl)
                nwc = int(np.sum(~np.isnan(y_wr[mw])))
                winsc = int(np.nansum(y_wr[mw]))
                bwins, bn = base_wr_by_window[lbl]
                delta_wr = (winsc / nwc - bwins / bn) if nwc >= MIN_N_WINDOW and bn >= MIN_N_WINDOW else None
                per_window_wr.append(dict(label=lbl, n=nwc, wins=winsc, base_n=bn, base_wins=bwins, delta=delta_wr))
                nec = int(np.sum(~np.isnan(y_ev[mw])))
                mean_ev = float(np.nanmean(y_ev[mw])) if nec else float("nan")
                se_ev = float(np.nanstd(y_ev[mw]) / math.sqrt(nec)) if nec > 1 else float("nan")
                bmean, bse, bne = base_ev_by_window[lbl]
                delta_ev = (mean_ev - bmean) if nec >= MIN_N_WINDOW and bne >= MIN_N_WINDOW else None
                per_window_ev.append(dict(label=lbl, n=nec, mean=mean_ev, se=se_ev,
                                           base_n=bne, base_mean=bmean, base_se=bse, delta=delta_ev))

            bwr = bayes_layer(per_window_wr, "wr", rng)
            bev = bayes_layer(per_window_ev, "ev", rng)
            P_pooled = bev["P_pooled_gt0"] if bev else (bwr["P_pooled_gt0"] if bwr else float("nan"))
            P_sign = bev["P_sign_consistent"] if bev else (bwr["P_sign_consistent"] if bwr else float("nan"))
            signs = window_signs(per_window_ev)

            replicates = None
            rmask_n = int(np.sum(~np.isnan(y_repl_wr[mask])))
            if rmask_n >= MIN_N_POOLED:
                repl_base_n = int(np.sum(~np.isnan(y_repl_wr)))
                repl_base_wr = np.nansum(y_repl_wr) / repl_base_n if repl_base_n else float("nan")
                repl_wr_cell = np.nansum(y_repl_wr[mask]) / rmask_n
                z_repl = ((repl_wr_cell - repl_base_wr) / math.sqrt(repl_base_wr * (1 - repl_base_wr) / rmask_n)
                          if 0 < repl_base_wr < 1 else 0.0)
                same_sign = (z_repl > 0) == (z_raw > 0)
                replicates = bool(same_sign and abs(z_repl) >= 2.0)

            if family in PRIMARY_FAMILIES:
                clears = (abs(z_raw) >= 3.0 and not math.isnan(t_ev) and abs(t_ev) >= 2.5
                          and not math.isnan(P_sign) and P_sign > 0.90)
                verdict = "CLEARS_PRIMARY" if clears else "null"
            else:
                clears = abs(z_raw) >= 4.0 or bool(replicates)
                verdict = "CLEARS_EXPLORATORY" if clears else "null"

            results.append(dict(family=family, cell=f"{colname}={cell}", n=ncell, resolved_n=resolved_n,
                                 apex_wr=wr_cell, apex_wr_delta=wr_cell - base_wr, apex_ev=ev_cell,
                                 apex_ev_delta=ev_cell - base_ev_mean, z_raw=z_raw, t_controlled=t_ev,
                                 t_controlled_wr=t_wr, P_pooled=P_pooled, P_sign_consistent=P_sign,
                                 signs=signs, replicates=replicates, verdict=verdict))
    return pl.DataFrame(results, infer_schema_length=None), dict(base_wr=base_wr, base_ev_mean=base_ev_mean, base_n=int(base_n))


# =============================================================================
# SUMMARY block
# =============================================================================
def print_summary(version_id, version_commit, ledger_df, feat_frame, cohort_df, base_stats,
                   selftest_results, smoke):
    print("==== SUMMARY ====")
    names = ["ema_sma", "lookahead", "trend_recomposition", "holdout"]
    for name, ok in zip(names, selftest_results):
        print(f"selftest {name}: {'PASS' if ok else 'FAIL'}")
    print(f"mode={'SMOKE' if smoke else 'FULL'} version_id={version_id} commit={version_commit}")
    print(f"ledger N={ledger_df.height} feat_frame N={feat_frame.height} "
          f"base_apex_wr={base_stats['base_wr']:.3f} base_apex_ev={base_stats['base_ev_mean']:.3f} "
          f"base_N={base_stats['base_n']}")
    windows_present = sorted(set(ledger_df["window"].drop_nulls().to_list()))
    print(f"windows_present={windows_present}")

    if cohort_df.height == 0:
        print("cohort table: EMPTY (no cell reached MIN_N_POOLED=%d -- expected in --smoke mode)" % MIN_N_POOLED)
    else:
        scored = cohort_df.filter(pl.col("verdict") != "screened_out")
        print(f"cohort cells enumerated={cohort_df.height} passed_stage2_treatment={scored.height} "
              f"screened_out(stage1 z<{Z_PREFILTER})={cohort_df.height - scored.height}")
        n_cells_z3 = cohort_df.filter(pl.col("z_raw").abs() >= 3.0).height
        print(f"expected_false_hits (approx alpha*cells @ z>=3, two-sided alpha~0.0027): "
              f"{cohort_df.height * 0.0027:.1f}  observed_cells_z_ge_3={n_cells_z3}")

        print("")
        print("rank family                    cell                                   N  evD    wrD    z_raw t_ctl Ppool Psign signs  verdict")
        ranked = scored.with_columns(
            pl.when(pl.col("t_controlled").is_not_null() & pl.col("t_controlled").is_nan().not_())
              .then(pl.col("t_controlled").abs())
              .otherwise(pl.col("z_raw").abs())
              .alias("_rank")
        ).sort("_rank", descending=True)
        max_rows = 45 if not smoke else 20
        for i, r in enumerate(ranked.head(max_rows).iter_rows(named=True)):
            cell_disp = r["cell"][:38]
            print(f"{i+1:>3}  {r['family']:<24s} {cell_disp:<38s} {r['n']:>5d} "
                  f"{r['apex_ev_delta']:+.3f} {r['apex_wr_delta']:+.3f} {r['z_raw']:+.1f} "
                  f"{r['t_controlled']:+.1f} {r['P_pooled']:.2f} {r['P_sign_consistent']:.2f} "
                  f"{r['signs']:6s} {r['verdict']}")

        print("")
        for fam in PRIMARY_FAMILIES:
            fam_rows = scored.filter(pl.col("family") == fam)
            clears = fam_rows.filter(pl.col("verdict") == "CLEARS_PRIMARY")
            if fam_rows.height == 0:
                print(f"PRIMARY VERDICT [{fam}]: no cells reached stage-2 treatment (n={cohort_df.filter(pl.col('family')==fam).height} enumerated)")
            elif clears.height == 0:
                best = fam_rows.sort(pl.col("z_raw").abs(), descending=True).head(1)
                bz = best["z_raw"][0] if best.height else float("nan")
                print(f"PRIMARY VERDICT [{fam}]: NULL -- no cell clears (z>=3 & |t_ctl|>=2.5 & Psign>0.90); "
                      f"best |z_raw|={bz:+.1f} over {fam_rows.height} treated cells")
            else:
                top = clears.sort(pl.col("t_controlled").abs(), descending=True).head(1).to_dicts()[0]
                print(f"PRIMARY VERDICT [{fam}]: CLEARS -- {clears.height} cell(s) clear; top={top['cell']} "
                      f"z_raw={top['z_raw']:+.1f} t_ctl={top['t_controlled']:+.1f} Psign={top['P_sign_consistent']:.2f}")
    print("==== END SUMMARY ====")


# =============================================================================
# main
# =============================================================================
def main():
    args = parse_args()
    t_start = time.time()
    print(f"[start] trend_ma_lattice mine.py  smoke={args.smoke} symbols_cap={args.symbols}", flush=True)

    version_id, version_commit = resolve_active_version()

    if args.smoke:
        symbols = SMOKE_SYMBOLS
        print(f"[symbols] SMOKE mode: {len(symbols)} symbols: {symbols}")
    else:
        symbols = full_universe_symbols(version_id, cap=args.symbols)
        print(f"[symbols] FULL universe: {len(symbols)} symbols with >=1 75+ CALL signal "
              f"({LEDGER_START}..{cutoff_iso()})")

    if not symbols:
        print("[FATAL] no symbols resolved -- aborting")
        sys.exit(2)

    tag = run_tag(args.smoke, args.symbols is not None, symbols)
    print(f"[symbols] run_tag={tag}")

    price_cache = load_price_history(symbols)
    missing = [s for s in symbols if s not in price_cache]
    if missing:
        print(f"[price] WARNING: {len(missing)} symbols had no PriceHistory rows: {missing[:10]}...")

    ledger_df = build_ledger(version_id, version_commit, symbols, price_cache, tag=tag)
    if ledger_df.height == 0:
        print("[FATAL] ledger is empty -- aborting before self-tests")
        sys.exit(2)
    ledger_df = pre_cutoff_filter(ledger_df)

    feat_frame = build_feature_frame(ledger_df, price_cache)
    if feat_frame.height == 0:
        print("[FATAL] feature frame is empty -- aborting before self-tests")
        sys.exit(2)
    feat_frame = pre_cutoff_filter(feat_frame)

    # ---- mandatory self-tests -------------------------------------------------
    print("\n[selftests] running...", flush=True)
    selftest_sample = sample_selftest_rows(version_id, symbols, n=900)
    ok1 = selftest_ema_sma(selftest_sample, price_cache, n_target=500)
    ok2 = selftest_lookahead(feat_frame, price_cache, n_sample=50)
    ok3 = selftest_trend_recomposition(selftest_sample, price_cache, n_target=500)
    ok4 = selftest_holdout(ledger_df, feat_frame)
    selftest_results = (ok1, ok2, ok3, ok4)
    if not all(selftest_results):
        print("\n[FATAL] one or more mandatory self-tests FAILED -- aborting before cohort analysis "
              "(per contract: STOP and fix before any cohort analysis).")
        print_summary(version_id, version_commit, ledger_df, feat_frame,
                      pl.DataFrame({"family": [], "cell": [], "n": [], "z_raw": [], "verdict": []}),
                      dict(base_wr=float("nan"), base_ev_mean=float("nan"), base_n=0),
                      selftest_results, args.smoke)
        sys.exit(1)
    print("[selftests] all PASS\n", flush=True)

    # NaN -> null across float cols: features.py uses NaN as its warmup/insufficient-
    # history sentinel (young listings <200 bars: 22/5854 full-universe rows), but
    # polars NaN is a VALUE not null -- it passes is_not_null() and kills strict
    # casts in add_bucket_columns. Null = "row absent from that axis" is the intended
    # cohort semantics. (Placed after selftests, before all downstream consumers.)
    feat_frame = feat_frame.with_columns(pl.col(pl.Float64).fill_nan(None))

    # ---- replication surface ---------------------------------------------------
    repl_df = build_replication_surface(feat_frame)
    if repl_df.height:
        feat_frame = feat_frame.join(repl_df, on=["symbol", "date"], how="left")
    else:
        feat_frame = feat_frame.with_columns(pl.lit(None).alias("repl_win"), pl.lit(None).alias("repl_ev"))

    # ---- partial-regression controls: pct_from_ema50/200 is stored-NULL for almost all
    # backfilled/recalculated score rows (only the live `trader update` path populates it --
    # a real data gap, confirmed empirically: 5817/5854 (99.4%) null on the full v74 75+ CALL
    # population). Coalesce the stored value (preferred when present) with our own mined
    # dist_EMA_50/200 -- the IDENTICAL formula (close-EMA_p)/EMA_p*100, validated to <0.1%
    # relative error against stored Indicator EMAs by selftest1 -- so the controls are
    # actually populated. See final report deviations note.
    n_before = feat_frame.filter(pl.col("pct_from_ema50").is_not_null()).height
    feat_frame = feat_frame.with_columns([
        pl.coalesce([pl.col("pct_from_ema50"), pl.col("dist_EMA_50")]).alias("control_pct_ema50"),
        pl.coalesce([pl.col("pct_from_ema200"), pl.col("dist_EMA_200")]).alias("control_pct_ema200"),
    ])
    n_after = feat_frame.filter(pl.col("control_pct_ema50").is_not_null()).height
    print(f"[controls] pct_from_ema50 stored-populated={n_before}/{feat_frame.height}; "
          f"control_pct_ema50 (coalesced w/ mined dist_EMA_50) populated={n_after}/{feat_frame.height}", flush=True)

    # ---- cohort scan (A3 + A4) --------------------------------------------------
    specs = build_bucket_specs()
    feat_frame = add_bucket_columns(feat_frame, specs)
    print(f"[cohort] enumerating {len(specs)} bucket axes over {feat_frame.height} rows...", flush=True)
    rng = np.random.default_rng(20260713)
    t0 = time.time()
    cohort_df, base_stats = run_cohort_scan(feat_frame, specs, rng)
    print(f"[cohort] {cohort_df.height} cells enumerated in {time.time()-t0:.1f}s", flush=True)

    # ---- write outputs ------------------------------------------------------
    feat_path = os.path.join(OUT_DIR, f"features_v{version_id}_{tag}.parquet")
    cohort_path = os.path.join(OUT_DIR, f"cohort_stats_v{version_id}_{tag}.parquet")
    feat_frame.write_parquet(feat_path)
    cohort_df.write_parquet(cohort_path)
    print(f"[output] wrote {feat_path} ({feat_frame.height} rows)")
    print(f"[output] wrote {cohort_path} ({cohort_df.height} rows)")

    print_summary(version_id, version_commit, ledger_df, feat_frame, cohort_df, base_stats,
                  selftest_results, args.smoke)
    print(f"\n[done] total wall time {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
