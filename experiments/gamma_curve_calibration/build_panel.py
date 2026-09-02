"""Gamma-Curve Calibration -- Phase 1 PANEL BUILD.

Implements the extraction + pair-construction + exclusion pipeline + strata/
cells + integrity gates + parquet outputs specified in:
  experiments/gamma_curve_calibration/PREREGISTRATION.md  (SS F/N/S -- frozen)
  experiments/gamma_curve_calibration/DESIGN.md            (SS 1-6)

THE SPEC IS BINDING. Every constant below is transcribed verbatim from
PREREGISTRATION.md SS F/N with a citation comment. Where this script had to
make a judgment call the spec left genuinely open, the call is documented
in-line AND in COVERAGE.md's "IMPLEMENTATION NOTES" or "OPEN QUESTIONS"
section (rail SS S.4: ambiguity -> STOP on that item, write the question,
continue with the rest).

Pipeline (SS F "Exclusion pipeline" row, ORDER PINNED):
  raw option_prices (chunked extraction)
    -> adjacent-row pairs per option_id (g in [1,4])
    -> join options metadata + price_history (S_d, S_d')
    -> eligibility filter: DTE in [5,45], V_d>=0.10, V_d'>=0.05, m_d in [-0.35,0.35]
       ("pairs built" -- the population entering the 5-stage pipeline)
    -> (1) corp-action  -> (2) earnings -> (3) bad-print -> (4) IV-solve failure
       -> (5) strike-centroid flag (+ terminal 2% STOP)
    -> stratum assignment (liquid) -> cell assignment
    -> train/OOS split (pair-end <= 2026-06-15 vs later)
    -> parquet + COVERAGE.md + attempt_log.md

CRITICAL RAIL (PREREGISTRATION SS S, DESIGN SS 5): for OOS rows this script
may print/write ONLY (cell_id, integer pair-count, floor-met boolean) -- no
means, moments, quantiles, errors, delta estimates, D-IV values, nothing else,
in ANY output including COVERAGE.md. See `_oos_counts_only()` -- it is the
ONLY function in this file allowed to touch the OOS dataframe, and it only
ever runs a COUNT aggregation.

TRAPS forwarded (DESIGN SS 6 + CLAUDE.md + traps.md -- read before editing):
  - Queue stdout needs PYTHONIOENCODING=utf-8 / PYTHONUTF8=1 (set on submit)
    AND ASCII-only prints -- this file uses plain ASCII throughout, no
    unicode punctuation, no sigma/arrow glyphs.
  - polars heterogeneous-row frames need infer_schema_length=None; this file
    avoids the issue by always constructing frames with an explicit schema.
  - MySQL query >30s = missing index -> chunk smaller, NEVER raise the
    client read_timeout (database/trader_database.py pins it at 30s on
    purpose). EMPIRICALLY VALIDATED HERE 2026-07-18 (light foreground
    checks, seconds-scale, not a full option_prices scan): a single-day
    option_prices extraction (567,693 rows, 2026-06-01) took 8.44s
    (~67k rows/s -- date-range filters use `options_prices_date_IDX` as a
    range scan but are NOT index-only ("Using where" without "Using index"
    in EXPLAIN), so each match needs a base-table lookup). At that
    throughput a calendar-MONTH chunk (~5-20M rows) would take 75-300s+ --
    unsafe against any tight per-statement cap and a bad unit of work for
    resumability/preemption. This script chunks by SINGLE CALENDAR DAY (see
    `_daily_ranges`) instead of the brief's suggested "month or year", each
    request individually cheap and independently resumable/cacheable. A
    second, distinct effect was also observed: `SET SESSION
    MAX_EXECUTION_TIME` cold-cache runs can run meaningfully slower than an
    immediately-following warm-cache re-run of the identical query (InnoDB
    buffer-pool effect, not a query-plan issue) -- per-chunk timeouts are
    set generously (60s for day chunks, up to 180s for the small one-shot
    tables) rather than tightly to the observed warm-cache norm, so a cold
    chunk doesn't spuriously fail. An automatic bisection retry (date-range
    halving, falling back to option_id-range halving once the date span
    hits 1 day) remains as a safety net for any chunk that still times out.
    Both effects are IMPLEMENTATION NOTES (COVERAGE.md), not open
    questions -- directly-measured infra behavior, not a spec ambiguity.
  - CAST(x AS DOUBLE) is MySQL 8.0.17+ only; this box runs MySQL 5.7.44.
    Use `col * 1e0` to force pymysql to hand back a native float instead of
    decimal.Decimal (avoids ~180M scalar Decimal->float conversions).
  - Never peewee row objects in bulk; never `.symbol` in a loop (FK-per-row
    trap) -- this file only ever uses raw SQL via DB.execute_sql(...).fetchall().
  - NYSE calendar must be built EMPIRICALLY from PriceHistory distinct dates,
    never a static holiday table (wave_cycle_mine trap, 2026-07-16: a static
    table missed the 2025-01-09 Carter national-mourning closure).

Outputs:
  .cache/gamma_curve_calibration/train_panel.parquet
  .cache/gamma_curve_calibration/oos_panel.parquet
  .cache/gamma_curve_calibration/panel_build_meta.json
  experiments/gamma_curve_calibration/COVERAGE.md
  experiments/gamma_curve_calibration/results/attempt_log.md  (appended)

Usage:
  python experiments/gamma_curve_calibration/build_panel.py [--smoke]
      [--force-chunks] [--chunk-days N] [--min-symbols-trading-day N]

  --smoke restricts extraction to the most recent ~10 calendar days for a
  fast (~1-2 min) local correctness check. It still writes real (tiny)
  parquets/COVERAGE.md under the SAME paths as a full run -- do not use
  --smoke output as the frozen panel; re-run without it for Phase 1 proper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)          # worktree PYTHONPATH trap guard (traps.md)

import numpy as np                  # noqa: E402
import polars as pl                 # noqa: E402
from scipy.special import ndtr      # noqa: E402  (vectorized standard-normal CDF)

from database.trader_database import DB  # noqa: E402

assert os.path.abspath(DB.__class__.__module__).startswith(_ROOT.split(os.sep)[-1]) or True, \
    "sanity placeholder"  # DB is a peewee MySQLDatabase singleton; nothing to origin-assert here.


# =============================================================================
# SS F -- FROZEN CONSTANTS (verbatim from PREREGISTRATION.md; cite the row)
# =============================================================================

# "Train window (pairs by date d) | 2025-02-10 <= d, with pair-end <= 2026-06-15"
# "Boundary source | FROZEN HERE at 2026-06-15. A December re-lock of
#  strategy_config.CALIBRATION_CUTOFF_DATE does NOT move this boundary either way"
# NOTE: deliberately NOT importing strategy_config.CALIBRATION_CUTOFF_DATE even
# though it currently also reads "2026-06-15" -- this experiment's boundary is
# frozen independently per SS F and must not silently follow a future re-lock.
TRAIN_CUTOFF = date(2026, 6, 15)

# "DTE range tau_d | [5, 45] calendar days"
DTE_MIN, DTE_MAX = 5, 45

# "Pair definition | ... g = d' - d in calendar days, 1 <= g <= 4"
GAP_MIN, GAP_MAX = 1, 4

# "Price floors | V_d >= 0.10, V_d' >= 0.05 (USD)"
V_D_FLOOR = 0.10
V_DPRIME_FLOOR = 0.05

# "Base stratum | volume_d >= 5"
BASE_VOLUME_MIN = 5

# "Liquid stratum | volume_d >= 50 AND open_interest_d >= 100. NULL or 0 in
#  either column -> the pair FAILS that stratum"
LIQUID_VOLUME_MIN = 50
LIQUID_OI_MIN = 100

# "Moneyness range | m_d = ln(S_d/K) in [-0.35, +0.35]"
MONEYNESS_MIN, MONEYNESS_MAX = -0.35, 0.35

# "Cell edges | m: [-0.35, -0.15, -0.05, +0.05, +0.15, +0.35] x
#  tau: [5, 10, 17, 28, 45]. Cell assignment: left-open right-closed"
CELL_M_EDGES = [-0.35, -0.15, -0.05, 0.05, 0.15, 0.35]
CELL_TAU_EDGES = [5, 10, 17, 28, 45]
N_M_BINS = len(CELL_M_EDGES) - 1     # 5
N_TAU_BINS = len(CELL_TAU_EDGES) - 1  # 4

# "Core grid | the 8 cells with m in (-0.15, +0.15]"
# OPEN QUESTION (see COVERAGE.md): under the CELL_M_EDGES list above, the
# m-bins whose interval is a SUBSET of (-0.15, +0.15] are bins 2,3,4 (1-indexed:
# (-0.15,-0.05], (-0.05,0.05], (0.05,0.15]) -- THREE bins, not two. 3 m-bins x
# 4 tau-bins = 12 cells, not 8. There is no bin-selection under the stated
# edges that yields exactly 8. This is flagged as an OPEN QUESTION; this
# script proceeds with the literal-edge-derived 12-cell core grid (the only
# reading directly computable from SS F's own edge list) and reports the
# discrepancy loudly in COVERAGE.md. This directly affects the bars
# population (decision layer) and the "6 of the 8 core cells" OOS floor
# language (SS N) -- reinterpreted here as "6 of the {N_CORE_CELLS} core
# cells" (absolute count 6 held pinned, denominator corrected).
CORE_GRID_M_BINS = {2, 3, 4}          # 1-indexed bins fully inside (-0.15, +0.15]
N_CORE_CELLS = len(CORE_GRID_M_BINS) * N_TAU_BINS  # 12 under this resolution

# "Exclusion pipeline (ORDER PINNED) | pairs built -> (1) corp-action
#  (S-ratio within 1% of {2,3,4,5,10,1/2,1/3,1/4,1/5,1/10} -> symbol +-5
#  trading days; any |r_S|>0.25) -> (2) earnings ([T-1,T+1]) -> (3) bad-print
#  (V_d < intrinsic - 0.02*S_d, or |r_V|>3.0) -> (4) IV-solve failure ->
#  (5) strike-centroid flag -> stratum assignment -> cell assignment"
CORP_ACTION_RATIOS = [2.0, 3.0, 4.0, 5.0, 10.0, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10]
CORP_ACTION_RATIO_TOL = 0.01          # "within 1%"
CORP_ACTION_ABS_RS_THRESHOLD = 0.25   # "any |r_S| > 0.25"
CORP_ACTION_WINDOW_TRADING_DAYS = 5   # "+-5 trading days"

EARNINGS_WINDOW_TRADING_DAYS = 1      # "[T-1, T+1]"

BAD_PRINT_INTRINSIC_TOL = 0.02        # "V_d < max(0, S_d-K) - 0.02*S_d"
BAD_PRINT_ABS_RV_THRESHOLD = 3.0      # "|r_V| > 3.0"

# "Implied-vol solve | bisection on normalized BS call (r=0,q=0), w in
#  [0.005, 3.0]; no solution -> excluded at stage 4, counted"
IV_SOLVE_LO, IV_SOLVE_HI = 0.005, 3.0
IV_SOLVE_ITERS = 70

# "Strike-centroid gate | per (symbol, d): strikes K with |ln(S_d/K)| < 0.10
#  ... |S_d/centroid - 1| > 0.10 flags the symbol-day"
CENTROID_MONEYNESS_BAND = 0.10
CENTROID_DEVIATION_THRESHOLD = 0.10

# "Centroid STOP | flagged fraction > 2% of symbol-days -> HALTS TERMINALLY"
CENTROID_STOP_FRACTION = 0.02

# "Train floor: liquid CALL core-grid pairs >= 50,000"
TRAIN_FLOOR_N = 50_000
# "OOS floors: bars-population pairs >= 20,000 AND >= 6 of the {N_CORE_CELLS}
#  core cells with >= 1,000 OOS pairs each"
OOS_FLOOR_TOTAL_N = 20_000
OOS_FLOOR_CELLS_REQUIRED = 6
OOS_FLOOR_CELL_MIN_N = 1_000

# "Self-test vectors" pinned vector (S_d, S_d', V_d, K, tau_d) -- echoed here
# for build_panel's own reference; the actual self-test lives in fit_curve.py.
PINNED_VECTORS = [
    (100.0, 103.0, 3.00, 100.0, 30),
    (100.0, 97.0, 3.00, 100.0, 30),
    (100.0, 100.0, 3.00, 100.0, 30),
    (50.0, 50.5, 0.80, 52.0, 10),
    (200.0, 196.0, 12.00, 190.0, 42),
]

MIN_SYMBOLS_TRADING_DAY_DEFAULT = 50  # wave_cycle_mine calendar_features.py precedent


# =============================================================================
# Paths
# =============================================================================
CACHE_DIR = os.path.join(_ROOT, ".cache", "gamma_curve_calibration")
RAW_DIR = os.path.join(CACHE_DIR, "_raw")
RAW_OP_DAILY_DIR = os.path.join(RAW_DIR, "option_prices_daily")
RAW_OPTIONS_PARQUET = os.path.join(RAW_DIR, "options_meta.parquet")
RAW_PRICE_HISTORY_PARQUET = os.path.join(RAW_DIR, "price_history.parquet")
RAW_EARNINGS_PARQUET = os.path.join(RAW_DIR, "earnings.parquet")

TRAIN_PARQUET = os.path.join(CACHE_DIR, "train_panel.parquet")
OOS_PARQUET = os.path.join(CACHE_DIR, "oos_panel.parquet")
META_JSON = os.path.join(CACHE_DIR, "panel_build_meta.json")

EXPERIMENT_DIR = _HERE
COVERAGE_PATH = os.path.join(EXPERIMENT_DIR, "COVERAGE.md")
RESULTS_DIR = os.path.join(EXPERIMENT_DIR, "results")
ATTEMPT_LOG = os.path.join(RESULTS_DIR, "attempt_log.md")

for d in (CACHE_DIR, RAW_DIR, RAW_OP_DAILY_DIR, RESULTS_DIR):
    os.makedirs(d, exist_ok=True)


# =============================================================================
# Small utilities
# =============================================================================
def log(msg: str) -> None:
    """ASCII-only stdout print, flushed (queue-stdout-buffering trap)."""
    print(msg, flush=True)


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True,
            text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except Exception as e:
        return f"UNKNOWN ({e})"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _append_attempt_log(outcome: str, detail: str = "") -> None:
    """Append-only (rail SS S.1). NEVER open in 'w' mode."""
    ts = datetime.now().isoformat(timespec="seconds")
    cmd = " ".join(sys.argv)
    line = f"- [{ts}] git={_git_head()} cmd=\"{cmd}\" outcome={outcome}"
    if detail:
        line += f" detail=\"{detail}\""
    with open(ATTEMPT_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    log(f"[attempt_log] {line}")


def _ensure_attempt_log_header() -> None:
    if not os.path.exists(ATTEMPT_LOG) or os.path.getsize(ATTEMPT_LOG) == 0:
        with open(ATTEMPT_LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(
                "# gamma_curve_calibration -- attempt log (append-only, rail SS S.1)\n\n"
                "Every driver invocation (build_panel.py, fit_curve.py), including dry\n"
                "runs and aborts, appends one line here: timestamp, git HEAD, exact\n"
                "command, outcome. This file is NEVER overwritten, only appended to.\n\n"
            )


# =============================================================================
# Phase A -- empirical NYSE trading-day calendar (never a static table)
# =============================================================================
class TradingCalendar:
    def __init__(self, days: list):
        self.days = days                       # sorted list[date]
        self.index_of = {d: i for i, d in enumerate(days)}

    def offset_bounds(self, center: date, n: int):
        """Return (lo_date, hi_date) = center -n/+n TRADING days, clamped to
        the index range. If `center` is not itself an indexed trading day,
        fall back to the nearest indexed day <= center (conservative: widens
        the exclusion window slightly rather than narrowing it)."""
        idx = self.index_of.get(center)
        if idx is None:
            # nearest trading day at or before `center`
            import bisect
            pos = bisect.bisect_right(self.days, center) - 1
            if pos < 0:
                idx = 0
            else:
                idx = pos
        lo = max(0, idx - n)
        hi = min(len(self.days) - 1, idx + n)
        return self.days[lo], self.days[hi]


def build_trading_calendar(min_symbols: int, start: date, end: date) -> TradingCalendar:
    """Empirical NYSE trading-day index built from PriceHistory distinct
    dates (never a static holiday table -- wave_cycle_mine trap). A date
    counts as a trading day iff >= `min_symbols` distinct price_history rows
    exist for it (guards against stray partial-data days, though price_history
    is a curated OHLCV table and no stray weekend rows were observed in a
    2026-07-18 spot check of 772-773 symbols/day)."""
    t0 = time.time()
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=60000")
    rows = DB.execute_sql(
        "SELECT date, COUNT(*) FROM price_history WHERE date BETWEEN %s AND %s "
        "GROUP BY date ORDER BY date",
        (start, end),
    ).fetchall()
    days = [d for d, n in rows if n >= min_symbols]
    n_rejected = len(rows) - len(days)
    log(f"[calendar] empirical trading-day index {start}..{end}: {len(rows)} distinct "
        f"dates, {len(days)} pass >= {min_symbols}-symbol threshold ({n_rejected} "
        f"rejected) ({time.time()-t0:.1f}s)")
    if not days:
        raise RuntimeError("[calendar] empty trading-day index -- cannot proceed")
    return TradingCalendar(days)


# =============================================================================
# Phase B -- one-shot small-table extraction (options, price_history, earnings)
# =============================================================================
def extract_options_meta(force: bool = False) -> pl.DataFrame:
    if not force and os.path.exists(RAW_OPTIONS_PARQUET):
        df = pl.read_parquet(RAW_OPTIONS_PARQUET)
        log(f"[extract] options metadata: cache hit ({len(df):,} rows)")
        return df
    t0 = time.time()
    # Cold-cache variance observed empirically 2026-07-18: an uncapped
    # standalone run took 37.4s, but the SAME query (same session, first
    # touch) hit a 60s cap once during --smoke testing -- InnoDB buffer-pool
    # cold-cache cost, not a query-plan problem. Budget generously (180s);
    # day-level chunking (not this one-shot small-table pull) is what
    # actually bounds worst-case wall-clock for the 90M-row table.
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=180000")
    rows = DB.execute_sql(
        "SELECT id, symbol, strike_price*1e0, expiration_date, option_type FROM options"
    ).fetchall()
    df = pl.DataFrame(
        rows, schema=["option_id", "symbol", "strike", "expiration_date", "option_type"],
        orient="row", infer_schema_length=None,
    ).with_columns([
        pl.col("option_id").cast(pl.Int64),
        pl.col("strike").cast(pl.Float64),
        pl.col("option_type").str.to_lowercase().alias("side"),
    ]).drop("option_type")
    df.write_parquet(RAW_OPTIONS_PARQUET, compression="snappy")
    log(f"[extract] options metadata: {len(df):,} rows ({time.time()-t0:.1f}s)")
    return df


def extract_price_history(start: date, end: date, force: bool = False) -> pl.DataFrame:
    if not force and os.path.exists(RAW_PRICE_HISTORY_PARQUET):
        df = pl.read_parquet(RAW_PRICE_HISTORY_PARQUET)
        log(f"[extract] price_history: cache hit ({len(df):,} rows)")
        return df
    t0 = time.time()
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
    rows = DB.execute_sql(
        "SELECT symbol, date, close*1e0 FROM price_history WHERE date BETWEEN %s AND %s",
        (start, end),
    ).fetchall()
    df = pl.DataFrame(
        rows, schema=["symbol", "date", "close"], orient="row", infer_schema_length=None,
    ).with_columns(pl.col("close").cast(pl.Float64))
    df.write_parquet(RAW_PRICE_HISTORY_PARQUET, compression="snappy")
    log(f"[extract] price_history {start}..{end}: {len(df):,} rows ({time.time()-t0:.1f}s)")
    return df


def extract_earnings(force: bool = False) -> pl.DataFrame:
    """symbol + effective_date (the TRADING DAY the price reaction appears --
    IMPLEMENTATION NOTE, COVERAGE.md: SS F's exclusion row says "an
    EarningsDate for the symbol" without specifying date vs effective_date.
    effective_date is the field database/models/core.py's own docstring
    documents as engineered for exactly this proximity purpose (BMO/intraday
    = same as date, AMC = next trading day); using it (falling back to
    `date` when effective_date is NULL, per the model's migration-safety
    nullability) is the reading consistent with the codebase's own
    convention. Not treated as a blocking open question."""
    if not force and os.path.exists(RAW_EARNINGS_PARQUET):
        df = pl.read_parquet(RAW_EARNINGS_PARQUET)
        log(f"[extract] earnings: cache hit ({len(df):,} rows)")
        return df
    t0 = time.time()
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=30000")
    rows = DB.execute_sql(
        "SELECT symbol, COALESCE(effective_date, date) FROM earnings_dates"
    ).fetchall()
    df = pl.DataFrame(
        rows, schema=["symbol", "t_date"], orient="row", infer_schema_length=None,
    )
    df.write_parquet(RAW_EARNINGS_PARQUET, compression="snappy")
    log(f"[extract] earnings: {len(df):,} rows ({time.time()-t0:.1f}s)")
    return df


# =============================================================================
# Phase C -- chunked option_prices extraction (SINGLE CALENDAR DAY chunks;
# see module docstring for the empirical timing that forced this granularity)
# =============================================================================
def _daily_ranges(start: date, end: date):
    d = start
    one = timedelta(days=1)
    out = []
    while d <= end:
        out.append(d)
        d += one
    return out


_DAY_CHUNK_TIMEOUT_MS = 60000  # generous vs the ~8-15s warm-cache norm (see module docstring)


def _fetch_option_prices_range(lo: date, hi: date):
    DB.execute_sql(f"SET SESSION MAX_EXECUTION_TIME={_DAY_CHUNK_TIMEOUT_MS}")
    return DB.execute_sql(
        "SELECT option_id, date, price*1e0, volume, open_interest, iv*1e0 "
        "FROM option_prices WHERE date BETWEEN %s AND %s",
        (lo, hi),
    ).fetchall()


def _fetch_option_prices_by_option_range(day: date, oid_lo: int, oid_hi: int):
    DB.execute_sql(f"SET SESSION MAX_EXECUTION_TIME={_DAY_CHUNK_TIMEOUT_MS}")
    return DB.execute_sql(
        "SELECT option_id, date, price*1e0, volume, open_interest, iv*1e0 "
        "FROM option_prices WHERE date=%s AND option_id BETWEEN %s AND %s",
        (day, oid_lo, oid_hi),
    ).fetchall()


def _resilient_fetch_day(day: date, oid_bounds, depth: int = 0):
    """Safety net beyond the day-level default chunk: if a SINGLE day still
    times out (future density growth), bisect that day by option_id range
    instead (date range can't be split further once it's 1 day wide)."""
    try:
        return _fetch_option_prices_range(day, day)
    except Exception as e:
        if depth >= 24:
            raise
        oid_lo, oid_hi = oid_bounds
        if oid_hi <= oid_lo:
            raise
        mid = (oid_lo + oid_hi) // 2
        log(f"[extract] day {day} timed out ({e!r}); bisecting by option_id "
            f"[{oid_lo},{mid}] + [{mid+1},{oid_hi}]")
        left = _fetch_by_oid_resilient(day, oid_lo, mid, depth + 1)
        right = _fetch_by_oid_resilient(day, mid + 1, oid_hi, depth + 1)
        return left + right


def _fetch_by_oid_resilient(day: date, oid_lo: int, oid_hi: int, depth: int):
    try:
        return _fetch_option_prices_by_option_range(day, oid_lo, oid_hi)
    except Exception as e:
        if depth >= 24 or oid_hi <= oid_lo:
            raise
        mid = (oid_lo + oid_hi) // 2
        log(f"[extract] day {day} option_id range [{oid_lo},{oid_hi}] still slow "
            f"({e!r}); bisecting further")
        left = _fetch_by_oid_resilient(day, oid_lo, mid, depth + 1)
        right = _fetch_by_oid_resilient(day, mid + 1, oid_hi, depth + 1)
        return left + right


_OID_BOUNDS_CACHE = None


def _option_id_bounds():
    global _OID_BOUNDS_CACHE
    if _OID_BOUNDS_CACHE is None:
        DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=15000")
        r = DB.execute_sql("SELECT MIN(id), MAX(id) FROM options").fetchall()
        _OID_BOUNDS_CACHE = (int(r[0][0]), int(r[0][1]))
    return _OID_BOUNDS_CACHE


def extract_option_prices_chunked(start: date, end: date, force: bool = False) -> list:
    """Day-by-day, resumable (skip a day whose parquet already exists unless
    --force-chunks). Returns the list of per-day parquet paths written."""
    days = _daily_ranges(start, end)
    paths = []
    t_all0 = time.time()
    n_hit, n_built = 0, 0
    total_rows = 0
    for day in days:
        out_path = os.path.join(RAW_OP_DAILY_DIR, f"{day.isoformat()}.parquet")
        if not force and os.path.exists(out_path):
            paths.append(out_path)
            n_hit += 1
            continue
        t0 = time.time()
        rows = _resilient_fetch_day(day, _option_id_bounds())
        df = pl.DataFrame(
            rows,
            schema=["option_id", "date", "price", "volume", "open_interest", "iv"],
            orient="row", infer_schema_length=None,
        )
        if len(df) > 0:
            df = df.with_columns([
                pl.col("option_id").cast(pl.Int64),
                pl.col("price").cast(pl.Float64),
                pl.col("volume").cast(pl.Int64),
                pl.col("open_interest").cast(pl.Int64),
                pl.col("iv").cast(pl.Float64),
            ])
        else:
            df = pl.DataFrame(schema={
                "option_id": pl.Int64, "date": pl.Date, "price": pl.Float64,
                "volume": pl.Int64, "open_interest": pl.Int64, "iv": pl.Float64,
            })
        df.write_parquet(out_path, compression="snappy")
        n_built += 1
        total_rows += len(df)
        if n_built % 20 == 0:
            log(f"[extract] option_prices: {n_built} days built so far "
                f"({total_rows:,} rows, last day {day} took {time.time()-t0:.1f}s)")
        paths.append(out_path)
    log(f"[extract] option_prices chunked extraction: {len(days)} days total "
        f"({n_hit} cache-hit, {n_built} built, {total_rows:,} new rows) in "
        f"{time.time()-t_all0:.1f}s")
    return paths


# =============================================================================
# BS math (normalized, r=0, q=0). m = ln(S/K). Vectorized (numpy + scipy.ndtr)
# -- build-time bulk IV-solve does not need 1e-12 precision, only enough for
# a converged bisection and a stable stored w_d; the 1e-12-precision scalar
# self-test lives in fit_curve.py (uses math.erf, not duplicated here).
# =============================================================================
def bs_call_frac_vec(m: np.ndarray, w: np.ndarray) -> np.ndarray:
    d1 = (m + 0.5 * w * w) / w
    d2 = d1 - w
    return ndtr(d1) - np.exp(-m) * ndtr(d2)


def bs_frac_vec(m: np.ndarray, w: np.ndarray, is_call: np.ndarray) -> np.ndarray:
    call = bs_call_frac_vec(m, w)
    put = call - 1.0 + np.exp(-m)   # put-call parity, r=0,q=0
    return np.where(is_call, call, put)


def solve_iv_vectorized(m: np.ndarray, target: np.ndarray, is_call: np.ndarray,
                         lo: float = IV_SOLVE_LO, hi: float = IV_SOLVE_HI,
                         iters: int = IV_SOLVE_ITERS):
    """Vectorized bisection solving bs_frac_vec(m, w, is_call) == target for w.
    Returns (w, ok) where ok=False means [lo,hi] does not bracket a root
    (SS F: "no solution -> excluded at stage 4, counted")."""
    n = len(m)
    lo_arr = np.full(n, lo, dtype=np.float64)
    hi_arr = np.full(n, hi, dtype=np.float64)
    f_lo = bs_frac_vec(m, lo_arr, is_call) - target
    f_hi = bs_frac_vec(m, hi_arr, is_call) - target
    ok = (np.sign(f_lo) * np.sign(f_hi)) <= 0
    ok &= np.isfinite(f_lo) & np.isfinite(f_hi)
    for _ in range(iters):
        mid = 0.5 * (lo_arr + hi_arr)
        f_mid = bs_frac_vec(m, mid, is_call) - target
        same_sign_as_lo = (np.sign(f_lo) * np.sign(f_mid)) > 0
        lo_arr = np.where(same_sign_as_lo, mid, lo_arr)
        hi_arr = np.where(same_sign_as_lo, hi_arr, mid)
        f_lo = np.where(same_sign_as_lo, f_mid, f_lo)
    w = 0.5 * (lo_arr + hi_arr)
    w = np.where(ok, w, np.nan)
    return w, ok


# =============================================================================
# Phase D -- build adjacent-row pairs (polars: sort + shift(-1) per option_id)
# =============================================================================
def build_pairs(daily_paths: list, options_df: pl.DataFrame,
                 price_history_df: pl.DataFrame) -> pl.DataFrame:
    t0 = time.time()
    lazy = pl.scan_parquet(daily_paths)
    raw = lazy.sort(["option_id", "date"]).collect()
    log(f"[pairs] raw option_prices rows assembled: {len(raw):,} ({time.time()-t0:.1f}s)")

    t0 = time.time()
    paired = raw.with_columns([
        pl.col("date").shift(-1).over("option_id").alias("date_dprime"),
        pl.col("price").shift(-1).over("option_id").alias("V_dprime"),
    ]).filter(pl.col("date_dprime").is_not_null())
    paired = paired.rename({"date": "date_d", "price": "V_d", "volume": "volume_d",
                             "open_interest": "oi_d", "iv": "iv_raw_d"})
    paired = paired.with_columns(
        (pl.col("date_dprime") - pl.col("date_d")).dt.total_days().cast(pl.Int32).alias("g")
    ).filter((pl.col("g") >= GAP_MIN) & (pl.col("g") <= GAP_MAX))
    log(f"[pairs] adjacent pairs with g in [{GAP_MIN},{GAP_MAX}]: {len(paired):,} "
        f"({time.time()-t0:.1f}s)")

    # join options metadata (symbol, strike, expiration_date, side)
    t0 = time.time()
    paired = paired.join(options_df, on="option_id", how="inner")
    log(f"[pairs] after join to options metadata: {len(paired):,} ({time.time()-t0:.1f}s)")

    # join price_history twice: S_d at date_d, S_dprime at date_dprime.
    # A pair whose (symbol, date_d) or (symbol, date_dprime) has no
    # price_history row is DROPPED here (IMPLEMENTATION NOTE, COVERAGE.md:
    # SS F's pair definition technically wants the walk-forward to skip to
    # the NEXT option_prices row when price_history is missing at the
    # naive next-print date; that is a rare multi-hop case this script does
    # not implement -- a missing price_history row for an actively-traded,
    # non-delisted symbol on a real NYSE session is expected to be ~0 in
    # practice. This under-counts by a conservative, safe direction rather
    # than risking a mis-paired row).
    t0 = time.time()
    ph_d = price_history_df.rename({"date": "date_d", "close": "S_d"})
    ph_dp = price_history_df.rename({"date": "date_dprime", "close": "S_dprime"})
    paired = paired.join(ph_d, on=["symbol", "date_d"], how="inner")
    paired = paired.join(ph_dp, on=["symbol", "date_dprime"], how="inner")
    log(f"[pairs] after price_history join (both legs): {len(paired):,} ({time.time()-t0:.1f}s)")

    # derived quantities
    t0 = time.time()
    paired = paired.with_columns([
        (pl.col("expiration_date") - pl.col("date_d")).dt.total_days().cast(pl.Int32).alias("tau_d"),
        (pl.col("S_dprime") - pl.col("S_d")).truediv(pl.col("S_d")).alias("r_S"),
        (pl.col("V_dprime") - pl.col("V_d")).truediv(pl.col("V_d")).alias("r_V"),
    ]).with_columns([
        (pl.col("S_d") / pl.col("strike")).log().alias("m_d"),
        (pl.col("S_dprime") / pl.col("strike")).log().alias("m_dprime"),
    ])

    # SS F eligibility: DTE, price floors, moneyness ("pairs built" population)
    eligible = paired.filter(
        (pl.col("tau_d") >= DTE_MIN) & (pl.col("tau_d") <= DTE_MAX)
        & (pl.col("V_d") >= V_D_FLOOR) & (pl.col("V_dprime") >= V_DPRIME_FLOOR)
        & (pl.col("m_d") >= MONEYNESS_MIN) & (pl.col("m_d") <= MONEYNESS_MAX)
    )
    log(f"[pairs] after DTE/price-floor/moneyness eligibility: {len(eligible):,} "
        f"({time.time()-t0:.1f}s)")

    # DESIGN SS3 "Inclusion": volume_d >= 5 (base stratum) is ALSO an
    # eligibility gate, not one of the 5 pinned exclusion stages -- rows
    # failing it are not part of any stratum. Counted but not written to the
    # final parquet (see COVERAGE.md exclusion-count taxonomy note).
    n_before_base = len(eligible)
    based = eligible.filter(pl.col("volume_d") >= BASE_VOLUME_MIN)
    n_sub_base_volume = n_before_base - len(based)
    log(f"[pairs] after base-stratum volume_d>={BASE_VOLUME_MIN} eligibility: "
        f"{len(based):,} ({n_sub_base_volume:,} dropped)")

    return based, n_sub_base_volume, len(raw), len(paired)


# =============================================================================
# Phase E -- exclusion pipeline, PINNED ORDER, first-match attribution
# =============================================================================
def _flag_by_symbol_date_windows(df: pl.DataFrame, events: pl.DataFrame,
                                  check_cols: list) -> pl.Series:
    """events: columns [symbol, lo_date, hi_date]. Returns a boolean Series
    (len == len(df)) True where ANY of `check_cols` in df falls within ANY
    matching-symbol event window. Vectorized join restricted to event-symbols
    (cheap: most symbols have zero events)."""
    if events.height == 0:
        return pl.Series([False] * df.height)
    df = df.with_row_index("_rowid")
    hit_ids = set()
    joined = df.join(events, on="symbol", how="inner")
    if joined.height > 0:
        cond = pl.lit(False)
        for c in check_cols:
            cond = cond | ((pl.col(c) >= pl.col("lo_date")) & (pl.col(c) <= pl.col("hi_date")))
        flagged = joined.filter(cond).select("_rowid").unique()
        hit_ids = set(flagged["_rowid"].to_list())
    return pl.Series([rid in hit_ids for rid in df["_rowid"].to_list()])


def stage1_corp_action(df: pl.DataFrame, cal: TradingCalendar):
    """SS F (1): S-ratio within 1% of a simple split ratio -> exclude the
    symbol's pairs within +-5 trading days of the action date (taken as
    date_dprime, the day the ratio discontinuity appears -- IMPLEMENTATION
    NOTE, matches SS F's own "S_{d+1}/S_d" numerator convention); PLUS any
    pair with |r_S| > 0.25 regardless of ratio match."""
    t0 = time.time()
    ratio = df["S_dprime"] / df["S_d"]
    is_ratio_match = pl.Series([False] * df.height)
    for cand in CORP_ACTION_RATIOS:
        is_ratio_match = is_ratio_match | ((ratio / cand - 1.0).abs() <= CORP_ACTION_RATIO_TOL)
    abs_rs_flag = df["r_S"].abs() > CORP_ACTION_ABS_RS_THRESHOLD

    action_rows = df.filter(is_ratio_match).select(["symbol", "date_dprime"]).unique()
    events = []
    for symbol, action_date in action_rows.iter_rows():
        lo, hi = cal.offset_bounds(action_date, CORP_ACTION_WINDOW_TRADING_DAYS)
        events.append((symbol, lo, hi))
    events_df = pl.DataFrame(events, schema=["symbol", "lo_date", "hi_date"], orient="row") \
        if events else pl.DataFrame(schema={"symbol": pl.Utf8, "lo_date": pl.Date, "hi_date": pl.Date})

    window_flag = _flag_by_symbol_date_windows(df, events_df, ["date_d"])
    flag = window_flag | abs_rs_flag
    n_excl = int(flag.sum())
    log(f"[stage1 corp_action] {len(action_rows)} distinct (symbol,action_date) events "
        f"detected; {n_excl:,} pairs excluded ({time.time()-t0:.1f}s)")
    return flag, n_excl, events_df


def stage2_earnings(df: pl.DataFrame, earnings_df: pl.DataFrame, cal: TradingCalendar):
    """SS F (2): d or d+1 in [T-1, T+1] of an EarningsDate for the symbol."""
    t0 = time.time()
    events = []
    for symbol, t_date in earnings_df.iter_rows():
        if t_date is None:
            continue
        lo, hi = cal.offset_bounds(t_date, EARNINGS_WINDOW_TRADING_DAYS)
        events.append((symbol, lo, hi))
    events_df = pl.DataFrame(events, schema=["symbol", "lo_date", "hi_date"], orient="row") \
        if events else pl.DataFrame(schema={"symbol": pl.Utf8, "lo_date": pl.Date, "hi_date": pl.Date})
    flag = _flag_by_symbol_date_windows(df, events_df, ["date_d", "date_dprime"])
    n_excl = int(flag.sum())
    log(f"[stage2 earnings] {n_excl:,} pairs excluded ({time.time()-t0:.1f}s)")
    return flag, n_excl


def stage3_bad_print(df: pl.DataFrame):
    """SS F (3): V_d < max(0, S_d-K) - 0.02*S_d, OR |r_V| > 3.0."""
    t0 = time.time()
    intrinsic = (df["S_d"] - df["strike"]).clip(lower_bound=0.0)
    below_intrinsic = df["V_d"] < (intrinsic - BAD_PRINT_INTRINSIC_TOL * df["S_d"])
    rv_blowup = df["r_V"].abs() > BAD_PRINT_ABS_RV_THRESHOLD
    flag = below_intrinsic | rv_blowup
    n_excl = int(flag.sum())
    log(f"[stage3 bad_print] {n_excl:,} pairs excluded ({time.time()-t0:.1f}s)")
    return flag, n_excl


def stage4_iv_solve(df: pl.DataFrame):
    """SS F (4): bisection on normalized BS, w in [0.005,3.0]; no solution ->
    excluded, counted. Stores w_d (the solved implied vol at d) for every
    surviving row -- this is what fit_curve.py reads (never re-solved)."""
    t0 = time.time()
    m = df["m_d"].to_numpy()
    target = (df["V_d"] / df["S_d"]).to_numpy()
    is_call = (df["side"] == "call").to_numpy()
    w, ok = solve_iv_vectorized(m, target, is_call)
    flag = pl.Series(~ok)
    n_excl = int(flag.sum())
    log(f"[stage4 iv_solve] {n_excl:,} pairs excluded (no bracket) ({time.time()-t0:.1f}s)")
    return flag, n_excl, pl.Series("w_d", w), pl.Series("iv_solve_ok", ok)


def stage5_centroid(df: pl.DataFrame):
    """SS F centroid gate + terminal STOP. Computed on rows surviving 1-4,
    base stratum, CALL side only, per symbol-day; the resulting flag applies
    to ALL rows (CALL+PUT) sharing that (symbol, date_d)."""
    t0 = time.time()
    call_rows = df.filter(pl.col("side") == "call")
    near_atm = call_rows.filter((pl.col("m_d")).abs() < CENTROID_MONEYNESS_BAND)
    per_strike = (
        near_atm.group_by(["symbol", "date_d", "strike"])
        .agg(pl.col("volume_d").sum().alias("w"))
    )
    centroid = (
        per_strike.group_by(["symbol", "date_d"])
        .agg([
            (pl.col("strike") * pl.col("w")).sum().alias("_num"),
            pl.col("w").sum().alias("_den"),
        ])
        .with_columns((pl.col("_num") / pl.col("_den")).alias("centroid"))
        .select(["symbol", "date_d", "centroid"])
    )
    # symbol-days present in the base-stratum CALL population (denominator)
    symbol_days = call_rows.select(["symbol", "date_d"]).unique()
    joined = symbol_days.join(centroid, on=["symbol", "date_d"], how="left")
    # need S_d per symbol-day for the deviation test
    s_lookup = call_rows.select(["symbol", "date_d", "S_d"]).unique(
        subset=["symbol", "date_d"], keep="first"
    )
    joined = joined.join(s_lookup, on=["symbol", "date_d"], how="left")
    joined = joined.with_columns(
        pl.when(pl.col("centroid").is_not_null())
        .then((pl.col("S_d") / pl.col("centroid") - 1.0).abs() > CENTROID_DEVIATION_THRESHOLD)
        .otherwise(False)   # "No qualifying strike -> UNFLAGGED, retained, counted separately"
        .alias("flagged")
    )
    n_no_qualifying_strike = int(joined["centroid"].is_null().sum())
    n_total_symbol_days = joined.height
    flagged_table = joined.filter(pl.col("flagged"))
    n_flagged_symbol_days = flagged_table.height
    fraction = (n_flagged_symbol_days / n_total_symbol_days) if n_total_symbol_days else 0.0

    flagged_keys = flagged_table.select(["symbol", "date_d"])
    flag = df.join(
        flagged_keys.with_columns(pl.lit(True).alias("_centroid_flag")),
        on=["symbol", "date_d"], how="left",
    )["_centroid_flag"].fill_null(False)
    n_excl = int(flag.sum())
    log(f"[stage5 centroid] {n_total_symbol_days:,} symbol-days evaluated, "
        f"{n_no_qualifying_strike:,} had no qualifying strike (unflagged, retained), "
        f"{n_flagged_symbol_days:,} flagged ({fraction*100:.3f}% of symbol-days), "
        f"{n_excl:,} pairs excluded ({time.time()-t0:.1f}s)")
    return (flag, n_excl, fraction, flagged_table, n_total_symbol_days, n_flagged_symbol_days,
            n_no_qualifying_strike)


# =============================================================================
# Phase F -- stratum + cell assignment
# =============================================================================
def assign_strata_cells(df: pl.DataFrame):
    oi_is_null = df["oi_d"].is_null()
    liquid = (df["volume_d"] >= LIQUID_VOLUME_MIN) & (df["oi_d"].fill_null(0) >= LIQUID_OI_MIN)
    # "would-be-liquid but for null OI" disclosure denominator/numerator
    would_be_liquid_volume_leg = df["volume_d"] >= LIQUID_VOLUME_MIN

    m_bin = np.digitize(df["m_d"].to_numpy(), CELL_M_EDGES, right=True)   # left-open right-closed
    tau_bin = np.digitize(df["tau_d"].to_numpy(), CELL_TAU_EDGES, right=True)
    # digitize with right=True on N+1 edges gives values in [0, N_BINS+1]; 0
    # and N_BINS+1 are out-of-range on the LOW side only (right=True makes
    # the top edge inclusive, e.g. tau_d==45 lands correctly in bin 4). This
    # is CONFIRMED (2026-07-18 smoke run + direct verification) to be a real,
    # non-trivial-scale case, not a floating-point fluke: tau_d is an
    # integer calendar-day count, so tau_d==5 (== DTE_MIN, itself an
    # INCLUSIVE inclusion-filter boundary per SS F's "[5,45]") is common --
    # 294 of 67,404 eligible rows (0.44%) in the --smoke sample. m_d==-0.35
    # exactly (continuous log-moneyness) accounted for 0/294 as expected.
    # This is OQ-3 in COVERAGE.md: SS F's inclusion range is CLOSED at the
    # low end ("in [-0.35,+0.35]", "[5,45]") but cell assignment is
    # LEFT-OPEN at that same low end -- a tau_d==5 row is "eligible" but has
    # no strictly-defined cell. Resolution used here: clip into the
    # boundary bin (bin 1) rather than silently narrowing the frozen
    # inclusion range by re-excluding these rows.
    m_bin_clipped = np.clip(m_bin, 1, N_M_BINS)
    tau_bin_clipped = np.clip(tau_bin, 1, N_TAU_BINS)
    out_of_range = (m_bin != m_bin_clipped) | (tau_bin != tau_bin_clipped)
    n_boundary_clipped = int(out_of_range.sum())
    if n_boundary_clipped:
        log(f"[cells] {n_boundary_clipped} rows at the closed-inclusion/left-open-cell "
            f"boundary (tau_d==5 or m_d==-0.35 exactly) clipped into bin 1 -- see OQ-3, "
            f"COVERAGE.md")

    cell_id = [f"m{mb}_t{tb}" for mb, tb in zip(m_bin_clipped, tau_bin_clipped)]
    core_grid = np.isin(m_bin_clipped, list(CORE_GRID_M_BINS))

    df = df.with_columns([
        oi_is_null.alias("oi_d_is_null"),
        liquid.alias("liquid_stratum"),
        would_be_liquid_volume_leg.alias("_would_be_liquid_volume_leg"),
        pl.Series("cell_m_bin", m_bin_clipped.astype(np.int32)),
        pl.Series("cell_tau_bin", tau_bin_clipped.astype(np.int32)),
        pl.Series("cell_id", cell_id),
        pl.Series("core_grid", core_grid),
    ])
    is_bars_population = (
        (df["side"] == "call") & df["liquid_stratum"] & df["core_grid"]
        & df["excl_stage"].is_null()
    )
    df = df.with_columns(is_bars_population.alias("is_bars_population"))
    return df, n_boundary_clipped


# =============================================================================
# Phase G -- train/OOS split
# =============================================================================
def split_train_oos(df: pl.DataFrame, cutoff: date):
    train = df.filter(pl.col("date_dprime") <= cutoff).with_columns(pl.lit("train").alias("split_label"))
    oos = df.filter(pl.col("date_d") > cutoff).with_columns(pl.lit("oos").alias("split_label"))
    n_buffer = df.height - train.height - oos.height
    return train, oos, n_buffer


# =============================================================================
# Phase H -- OOS reporting (CRITICAL RAIL: counts only, nothing else, ever)
# =============================================================================
def _oos_counts_only(oos_df: pl.DataFrame) -> dict:
    """THE ONLY function permitted to touch the OOS dataframe for reporting
    purposes. Emits ONLY: per-cell_id integer pair-count (bars population
    only) + the two N-floor booleans. No mean/quantile/error/delta/D-IV/
    anything else is computed here or anywhere else in this file for OOS
    rows -- PREREGISTRATION SS S / DESIGN SS 5 critical rail."""
    bars = oos_df.filter(pl.col("is_bars_population"))
    total_n = bars.height
    per_cell = (
        bars.group_by("cell_id").agg(pl.len().alias("n"))
        .sort("cell_id")
    )
    per_cell_counts = {row[0]: int(row[1]) for row in per_cell.iter_rows()}
    n_cells_meeting_floor = sum(1 for n in per_cell_counts.values() if n >= OOS_FLOOR_CELL_MIN_N)
    floor_met = (total_n >= OOS_FLOOR_TOTAL_N) and (n_cells_meeting_floor >= OOS_FLOOR_CELLS_REQUIRED)
    return {
        "total_bars_population_n": total_n,
        "per_cell_bars_population_n": per_cell_counts,
        "n_cells_meeting_1000_floor": n_cells_meeting_floor,
        "n_core_cells": N_CORE_CELLS,
        "floor_met": floor_met,
    }


# =============================================================================
# COVERAGE.md static content (OPEN QUESTIONS / IMPLEMENTATION NOTES) -- kept
# as a module-level constant so it is IDENTICAL on every run and can be
# reviewed/quoted before Phase 1 ever executes.
# =============================================================================
OPEN_QUESTIONS_MD = """## OPEN QUESTIONS (blocking -- need FABLE/user SS A adjudication)

Per SS S rail 4 ("ambiguity -> STOP on that item, write the question into
COVERAGE.md, continue with the rest") these four items were NOT resolved by
executor choice. The build/fit proceed using the stated interim resolutions
so Phase 1 can produce a panel and a self-test report at all; OQ-1/OQ-2/OQ-3
materially affect the decision-layer bars population and should be
adjudicated before Phase 2 (2026-12-15+) locks in a reading. OQ-4 is more
severe: it BLOCKS the fit step outright under a strict reading (see below)
and should be adjudicated BEFORE the fit is ever run, not just before
Phase 2.

### OQ-1: "8 cells" vs the literal cell-edge arithmetic (12 cells)

PREREGISTRATION.md SS F states: "Core grid | the 8 cells with m in (-0.15,
+0.15]". DESIGN.md SS 3 repeats "8 cells" and separately confirms the FULL
grid is "5x4 = 20 cells" (5 moneyness bins from the 6-value edge list
`[-0.35,-0.15,-0.05,+0.05,+0.15,+0.35]`, times 4 tau bins from the 5-value
edge list `[5,10,17,28,45]`).

Under those edges (left-open, right-closed, as SS F itself specifies), the
m-bins are: (-0.35,-0.15], (-0.15,-0.05], (-0.05,0.05], (0.05,0.15],
(0.15,0.35]. The bins whose interval is a SUBSET of (-0.15,+0.15] are bins 2,
3, 4 -- THREE bins, not two. 3 m-bins x 4 tau-bins = 12 cells. There is no
selection of bins under the stated edge list that produces exactly 8 (that
would require exactly 2 m-bins, and no documented reason to drop one of the
three central bins, e.g. the ATM (-0.05,0.05] bin, is given anywhere in
either document).

**Resolution used in this build:** proceed with the literal-edge-derived
12-cell core grid (`CORE_GRID_M_BINS = {2,3,4}` in build_panel.py, m-bins
1-indexed). This is the only reading directly computable from SS F's own
edge list without inventing an unstated bin-drop rule.

**Downstream impact:** (a) the "bars population" (liquid CALL core grid --
the decision-layer MAE population for Bar A/B in Phase 2) is defined over 12
cells, not 8; (b) SS N's OOS floor language "6 of the 8 core cells with >=
1,000 OOS pairs each" is reinterpreted here as "6 of the {N_CORE_CELLS}
core cells" -- the absolute count 6 is held pinned, the denominator is
corrected to 12. This script does NOT infer a proportionally-scaled
replacement (e.g. "9 of 12" to preserve a 75% ratio) -- that would be an
additional, unstated assumption on top of an already-ambiguous spec.

If FABLE/user determines the intended core grid was narrower (e.g. dropping
the ATM bin, or narrower tau coverage), amend SS F via SS A and re-run
Phase 1 in full per rail SS S.2 (no silent refit).

### OQ-2: M0 self-test "on every panel row" vs the -1.0 P&L floor

PREREGISTRATION.md SS F's M0 self-test row requires: "assert V_d*(1+pnl)
equals the closed form on the pinned vector AND on every panel row to
1e-9". `option_pricing.option_pnl_pct` (the wrapper's mandated import,
DESIGN SS 2) applies `max(-1.0, pnl_pre_vega)` -- i.e. it floors the
predicted option value at $0 (an option cannot be worth less than zero).
The literal closed form `V_d*sqrt((tau_d-1)/tau_d) + 0.5*(S_d'-S_d)` has NO
such floor -- it can go negative for a sufficiently large adverse move on a
cheap (near price-floor V_d=0.10) contract, which is common enough in the
base-stratum population (not just a pathological corner case) that a literal
byte-for-byte comparison against the UNFLOORED closed form would fail on a
non-trivial fraction of real panel rows, even though the wrapper is being
called correctly.

**Resolution used in this build:** build_panel.py itself does not run this
assertion (fit_curve.py owns M0/M1/M2 per the deliverable split); this note
documents the interim reading fit_curve.py implements: the REFERENCE closed
form used for the comparison applies the SAME floor
(`max(0.0, V_d*sqrt((tau_d-1)/tau_d) + 0.5*(S_d'-S_d))`), matching the
wrapper's actual (floored) behavior -- i.e. "the closed form" is read as
shorthand for the engine's true floored output, and the assertion becomes a
translation-correctness check (are the right arguments being passed in the
right order/units) rather than a claim that the linear formula never
saturates. The 5 pinned vectors are all comfortably positive under the
UNFLOORED formula so they pass either way; only the "every panel row" clause
is affected. Flagged for FABLE/user confirmation this is the intended
reading before Phase 2 treats an M0 self-test PASS as meaningful.

### OQ-3: cell assignment is left-open at a boundary the inclusion filter treats as closed

SS F's moneyness/DTE INCLUSION criteria are closed intervals: "m_d ... in
[-0.35, +0.35]" and "tau_d ... [5, 45] calendar days". SS F's CELL edges use
the SAME low-end numbers (-0.35 and 5) but are explicitly "left-open
right-closed" -- so a row with tau_d exactly 5, or m_d exactly -0.35, is
INCLUDED by the eligibility filter but has NO strictly-defined cell (the
first cell is `(5,10]` / `(-0.35,-0.15]`, both of which EXCLUDE their own
low edge). This is confirmed non-trivial in practice, not a theoretical
corner case: tau_d is an integer calendar-day count, so tau_d==5 is common
-- 294 of 67,404 eligible rows (0.44%) in a 2026-07-18 smoke sample (m_d
exactly -0.35 accounted for 0 of those 294, as expected for a continuous
log-moneyness value).

**Resolution used in this build:** these boundary rows are clipped into the
adjacent bin (bin 1 on the affected axis) rather than re-excluded -- silently
re-excluding them would narrow the frozen closed-interval inclusion range,
which SS F does not license. The count is tracked
(`n_boundary_clipped_cells_oq3` in panel_build_meta.json) and reported below.
Low severity (single-bin misassignment at an exact boundary, ~0.4% of
eligible rows in-sample) but flagged for completeness per rail SS S.4.

### OQ-4: the M1 self-test's 1e-12 bound vs `bs_value_frac`, PROVEN unreachable on a moved vector (fit_curve.py)

SS F's M1 self-test row requires: "assert M1 = the bs_value_frac-based ATM
roll at (m=0, g=1) to 1e-12". `fit_curve.py --selftest` (run 2026-07-18,
verbatim output archived in `results/attempt_log.md`) proves this bound is
UNREACHABLE for a moved vector (S_d' != S_d) under EITHER choice of
w-source, and is not cleanly reachable for a zero-move vector either once M1
uses SS F's own w_d definition. Algebraically, at m_d=0: `M1(w) -
bs_value_frac_roll(w) = (BS(m_d',w') - BS(0,w')) * (K - V_d/BS(0,w))`
(both sides evaluated at a common w) -- this vanishes only if there is no
move, OR `BS(0,w)*K == V_d` exactly. SS F's ONLY definition of w_d
("Implied-vol solve" row) is the TRUE bisection solution of that equation,
which satisfies it exactly BY CONSTRUCTION -- but only for the side that
uses it. `option_pricing.bs_value_frac` always derives its OWN internal vol
via a documented LINEARIZED approximation (`_vol_total_from_premium`,
"<1% off" per its own docstring) that CANNOT be overridden from outside, so
the two sides are structurally evaluated at two different, both-imperfect
w-values. Measured: feeding M1 the bisection w_d (SS F's actual
definition), all three ATM pinned vectors fail 1e-12 (diffs 4.632e-04 /
2.405e-04 / 1.092e-08); feeding M1 the SAME linearized shortcut
`bs_value_frac` uses internally instead, the zero-move vector passes
exactly (both sides collapse to one shared w) but the two moved vectors
still fail (2.4e-4 / 4.2e-4) since a linearized w only approximately
satisfies `BS(0,w)*K=V_d`. No w-choice clears 1e-12 on a moved vector.

**Resolution used in this build:** `fit_curve.py` implements M1 with the
bisection w_d (SS F's actual definition, used everywhere else in the
protocol) and reports the resulting diffs honestly rather than switching to
whichever w-source minimizes them. Per SS F "build STOPS on any failure",
`fit_curve.py`'s self-test is a HARD FAIL under the strict, literal 1e-12
reading, which means **Phase 1's fit step cannot complete as literally
specified until this is adjudicated via SS A** -- either the tolerance for
this specific check needs loosening to a value achievable given
`bs_value_frac`'s own documented linearization error (~1e-3 to 1e-4
relative), or the reference comparison needs a different construction
entirely (e.g. comparing against a bisection-based reimplementation of the
ATM roll rather than the existing linearized `bs_value_frac`). This is the
single highest-severity open question in this pack: unlike OQ-1/OQ-2/OQ-3,
it is not a matter of interpretation but a proven mathematical
incompatibility between two SS F rows (the M1 self-test's tolerance and the
Implied-vol solve row's bisection definition of w_d, combined with
`bs_value_frac`'s fixed internal approximation). M0 and the
M2(k=1)==M1 nesting checks are UNAFFECTED (both pass cleanly, 10/10 of the
non-M1 pinned-vector checks) -- this is scoped narrowly to the M1-vs-
`bs_value_frac` cross-check only.
"""

IMPLEMENTATION_NOTES_MD = """## IMPLEMENTATION NOTES (resolved via reasoned interpretation, not blocking)

- **Chunk granularity: SINGLE CALENDAR DAY, not month/year.** The brief
  suggested chunking option_prices extraction "by calendar month or year"
  (matching `database/bulk_cache.py`'s `chunked_query_by_year` precedent).
  Empirical, light foreground testing on 2026-07-18 (seconds-scale checks,
  never a full option_prices scan) measured ~67,000 rows/s for a
  `date BETWEEN` range on this table (MySQL 5.7.44; `options_prices_date_IDX`
  is used as a range scan but is NOT covering here -- EXPLAIN shows "Using
  where" without "Using index", so every match costs a base-table lookup).
  At that throughput a calendar-MONTH chunk (~5-20M rows depending on era)
  would run 75-300s+ per request -- a bad unit of work regardless of the
  exact timeout policy (too coarse for --restartable resumability, too easy
  to blow past any reasonable per-statement cap). This build chunks by
  single calendar day (`_daily_ranges`) instead: ~8-15s/day warm-cache,
  independently cached/resumable, with an automatic bisection-by-
  option_id-range fallback if a single day ever exceeds its (generous, 60s)
  budget. A SEPARATE effect was also observed and is worth naming
  explicitly: `SET SESSION MAX_EXECUTION_TIME` cold-cache runs can be
  meaningfully slower than an immediately-following warm-cache re-run of the
  IDENTICAL query (InnoDB buffer-pool warmup, not a query-plan regression) --
  per-chunk timeouts are therefore set well above the observed warm-cache
  norm (60s for day chunks vs an ~8-15s norm; 120-180s for the one-shot
  small-table pulls) rather than tightly to it.
- **`CAST(x AS DOUBLE)` is unavailable** (MySQL 5.7.44; that syntax needs
  8.0.17+). Used `col * 1e0` instead, which pymysql maps to a native Python
  float (avoiding ~180M scalar `decimal.Decimal`->float conversions in
  Python).
- **Earnings proximity uses `EarningsDate.effective_date`** (falling back to
  `date` when null), not the raw report `date` -- see the docstring on
  `extract_earnings()`.
- **Corp-action "action date"** is taken as `date_d'` (the day the ratio
  discontinuity first appears in the pair), matching SS F's own
  `S_{d+1}/S_d` numerator convention. The corp-action window check tests
  `date_d` only (not `date_d'`); the earnings window check tests both `date_d`
  and `date_d'` per SS F's explicit "d or d+1" wording.
- **Missing price_history at the naive next print date**: pairs are dropped
  (not multi-hop-searched forward) if either leg's (symbol, date) has no
  price_history row -- see `build_pairs()` docstring. Expected impact ~0 for
  actively-traded, non-delisted symbols.
- **OOS parquet upper bound in Phase 1** = whatever is naturally available in
  the production DB at build time (no artificial cap). The SS N "sealed OOS
  slice" (`min(2026-12-12, latest complete d')`) is explicitly a Phase-2,
  first-invocation-only computation per SS N -- Phase 1 does not compute or
  guess it.
- **COVERAGE.md is overwritten** on each successful run (it is not in rail
  SS S.1's "never overwrite" list, which names only `results/attempt_log.md`
  and `FINDINGS.md`); `results/attempt_log.md` is strictly append-only.
- **`volume_d >= 5` (base stratum)** is treated as a pre-pipeline ELIGIBILITY
  gate (DESIGN SS 3 lists it under "Inclusion", not "Exclusions"), not one of
  the 5 pinned, order-counted exclusion stages -- counted separately and
  disclosed, but not interleaved into the pinned stage-1..5 sequence.
"""


def _write_coverage_md(payload: dict) -> None:
    lines = []
    lines.append("# Gamma-Curve Calibration -- Phase 1 COVERAGE.md")
    lines.append("")
    lines.append(f"Generated: {payload['generated_at']} | git HEAD: {payload['git_head']}")
    lines.append(f"Build outcome: **{payload['outcome']}**")
    lines.append("")
    lines.append(OPEN_QUESTIONS_MD)
    lines.append(IMPLEMENTATION_NOTES_MD)

    lines.append("## Extraction summary")
    lines.append("")
    lines.append(f"- Raw option_prices rows assembled: {payload.get('n_raw_rows', 'n/a'):,}"
                  if isinstance(payload.get('n_raw_rows'), int) else "- Raw option_prices rows assembled: n/a")
    lines.append(f"- Adjacent pairs (g in [{GAP_MIN},{GAP_MAX}]) pre-eligibility: "
                  f"{payload.get('n_all_pairs', 'n/a')}")
    lines.append(f"- Eligible ('pairs built': DTE/price-floor/moneyness): "
                  f"{payload.get('n_eligible', 'n/a')}")
    lines.append(f"- Dropped for volume_d < {BASE_VOLUME_MIN} (base-stratum eligibility, "
                  f"NOT one of the pinned 5 stages): {payload.get('n_sub_base_volume', 'n/a')}")
    lines.append("")

    lines.append("## Exclusion pipeline (PINNED ORDER, first-match attribution)")
    lines.append("")
    lines.append("| Stage | Excluded (first-match) | Cumulative surviving |")
    lines.append("|---|---|---|")
    for row in payload.get("stage_table", []):
        lines.append(f"| {row['stage']} | {row['excluded']:,} | {row['surviving']:,} |")
    lines.append("")

    lines.append("## Strike-centroid gate")
    lines.append("")
    cg = payload.get("centroid", {})
    lines.append(f"- Symbol-days evaluated: {cg.get('n_total_symbol_days', 'n/a')}")
    lines.append(f"- No qualifying strike (UNFLAGGED, retained): {cg.get('n_no_qualifying_strike', 'n/a')}")
    lines.append(f"- Flagged (excluded): {cg.get('n_flagged_symbol_days', 'n/a')}")
    lines.append(f"- Flagged fraction: {cg.get('fraction', 0)*100:.4f}% "
                  f"(STOP threshold: {CENTROID_STOP_FRACTION*100:.1f}%)")
    lines.append("")

    lines.append("## Liquid-stratum null-OI disclosure")
    lines.append("")
    oi = payload.get("oi_disclosure", {})
    lines.append(f"- Otherwise-liquid pairs (volume_d >= {LIQUID_VOLUME_MIN}): "
                  f"{oi.get('denom', 'n/a')}")
    lines.append(f"- ...of which open_interest is NULL: {oi.get('numer', 'n/a')} "
                  f"({oi.get('pct', 0):.2f}%)")
    if oi.get("pct", 0) > 20.0:
        lines.append("- **>20% -- DISCLOSED per SS F; proceeding on non-null pairs only, "
                      "never relaxing the predicate.**")
    lines.append("")

    if payload["outcome"] == "CENTROID_STOP":
        lines.append("## CENTROID STOP -- flagged symbol-day table (halt-triggering)")
        lines.append("")
        lines.append("| symbol | date_d | S_d | centroid | deviation |")
        lines.append("|---|---|---|---|---|")
        for r in payload.get("flagged_table_rows", [])[:500]:
            lines.append(f"| {r['symbol']} | {r['date_d']} | {r['S_d']:.4f} | "
                          f"{r['centroid']:.4f} | {r['deviation']:.4f} |")
        if len(payload.get("flagged_table_rows", [])) > 500:
            lines.append(f"\n... ({len(payload['flagged_table_rows'])} total rows, truncated to 500)")
        lines.append("")
        lines.append("**Build HALTED per SS F Centroid STOP. Resolution is a FABLE/user SS A "
                      "decision logged before any resumed run. The executor may NOT hand-select "
                      "symbols to get under 2%.**")
        lines.append("")
        with open(COVERAGE_PATH, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        return

    lines.append("## TRAIN -- cell-N tables (full detail; TRAIN only per rail)")
    lines.append("")
    for stratum_name, table_key in (("base", "train_cells_base"), ("liquid", "train_cells_liquid")):
        lines.append(f"### {stratum_name} stratum")
        lines.append("")
        lines.append("| cell_id | m_bin | tau_bin | core_grid | n_call | n_put |")
        lines.append("|---|---|---|---|---|---|")
        for r in payload.get(table_key, []):
            lines.append(f"| {r['cell_id']} | {r['cell_m_bin']} | {r['cell_tau_bin']} | "
                          f"{r['core_grid']} | {r['n_call']} | {r['n_put']} |")
        lines.append("")
    lines.append(f"- TRAIN liquid CALL core-grid ('bars population') N = "
                  f"{payload.get('train_bars_n', 'n/a')} "
                  f"(floor: >= {TRAIN_FLOOR_N:,}; MET: {payload.get('train_floor_met', 'n/a')})")
    lines.append(f"- OQ-3 boundary-clipped rows (tau_d==5 or m_d==-0.35 exactly, clipped into "
                  f"bin 1; whole panel, both splits): {payload.get('n_boundary_clipped', 'n/a')}")
    lines.append("")

    lines.append("## OOS -- COUNTS ONLY (critical rail: no means/quantiles/errors ever)")
    lines.append("")
    oos_c = payload.get("oos_counts", {})
    lines.append(f"- Total OOS bars-population N: {oos_c.get('total_bars_population_n', 'n/a')} "
                  f"(floor: >= {OOS_FLOOR_TOTAL_N:,})")
    lines.append(f"- Core cells meeting >= {OOS_FLOOR_CELL_MIN_N:,}-pair floor: "
                  f"{oos_c.get('n_cells_meeting_1000_floor', 'n/a')} / "
                  f"{oos_c.get('n_core_cells', 'n/a')} required >= {OOS_FLOOR_CELLS_REQUIRED}")
    lines.append(f"- OOS floor met: **{oos_c.get('floor_met', 'n/a')}**")
    lines.append("")
    lines.append("| cell_id | OOS pair-count (bars population only) |")
    lines.append("|---|---|")
    for cid, n in sorted(oos_c.get("per_cell_bars_population_n", {}).items()):
        lines.append(f"| {cid} | {n} |")
    lines.append("")
    lines.append(f"- Train/OOS boundary-buffer pairs (neither train nor OOS: date_d <= "
                  f"{TRAIN_CUTOFF} < date_dprime): {payload.get('n_boundary_buffer', 'n/a')}")
    lines.append("")

    lines.append("## Parquet integrity")
    lines.append("")
    lines.append(f"- train_panel.parquet: {payload.get('train_n', 'n/a')} rows, "
                  f"sha256={payload.get('train_sha256', 'n/a')}")
    lines.append(f"- oos_panel.parquet: {payload.get('oos_n', 'n/a')} rows, "
                  f"sha256={payload.get('oos_sha256', 'n/a')}")
    lines.append("")

    with open(COVERAGE_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


# =============================================================================
# main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true",
                     help="restrict to the most recent ~10 calendar days for a fast local check")
    ap.add_argument("--force-chunks", action="store_true",
                     help="re-extract per-day option_prices chunks even if cached")
    ap.add_argument("--force-small-tables", action="store_true",
                     help="re-extract options/price_history/earnings even if cached")
    ap.add_argument("--min-symbols-trading-day", type=int, default=MIN_SYMBOLS_TRADING_DAY_DEFAULT)
    args = ap.parse_args()

    _ensure_attempt_log_header()
    _append_attempt_log("STARTED", detail=f"smoke={args.smoke}")

    t_start = time.time()
    try:
        DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=15000")
        op_range = DB.execute_sql("SELECT MIN(date), MAX(date) FROM option_prices").fetchall()[0]
        op_min, op_max = op_range[0], op_range[1]
        log(f"[main] option_prices span (live DB, at invocation): {op_min} .. {op_max}")

        if args.smoke:
            extract_start = max(op_min, op_max - timedelta(days=10))
            extract_end = op_max
            log(f"[main] --smoke: restricting extraction to {extract_start}..{extract_end}")
        else:
            extract_start, extract_end = op_min, op_max

        cal_start = min(extract_start - timedelta(days=30), date(2024, 6, 1))
        cal = build_trading_calendar(args.min_symbols_trading_day, cal_start, op_max)

        options_df = extract_options_meta(force=args.force_small_tables)
        price_history_df = extract_price_history(cal_start, op_max, force=args.force_small_tables)
        earnings_df = extract_earnings(force=args.force_small_tables)

        daily_paths = extract_option_prices_chunked(extract_start, extract_end, force=args.force_chunks)

        based, n_sub_base_volume, n_raw_rows, n_all_pairs = build_pairs(
            daily_paths, options_df, price_history_df
        )
        n_eligible = based.height
        log(f"[main] eligibility-surviving ('pairs built') population: {n_eligible:,}")

        stage_table = []
        surviving = based
        s1_flag, s1_n, corp_events = stage1_corp_action(surviving, cal)
        surviving = surviving.with_columns(s1_flag.alias("_f1"))
        stage_table.append({"stage": "1 corp_action", "excluded": s1_n,
                             "surviving": surviving.height - s1_n})
        rest1 = surviving.filter(~pl.col("_f1")).drop("_f1")
        excl1 = surviving.filter(pl.col("_f1")).drop("_f1").with_columns(
            pl.lit("corp_action").alias("excl_stage"))

        s2_flag, s2_n = stage2_earnings(rest1, earnings_df, cal)
        rest1 = rest1.with_columns(s2_flag.alias("_f2"))
        stage_table.append({"stage": "2 earnings", "excluded": s2_n,
                             "surviving": rest1.height - s2_n})
        rest2 = rest1.filter(~pl.col("_f2")).drop("_f2")
        excl2 = rest1.filter(pl.col("_f2")).drop("_f2").with_columns(
            pl.lit("earnings").alias("excl_stage"))

        s3_flag, s3_n = stage3_bad_print(rest2)
        rest2 = rest2.with_columns(s3_flag.alias("_f3"))
        stage_table.append({"stage": "3 bad_print", "excluded": s3_n,
                             "surviving": rest2.height - s3_n})
        rest3 = rest2.filter(~pl.col("_f3")).drop("_f3")
        excl3 = rest2.filter(pl.col("_f3")).drop("_f3").with_columns(
            pl.lit("bad_print").alias("excl_stage"))

        s4_flag, s4_n, w_d_series, iv_ok_series = stage4_iv_solve(rest3)
        rest3 = rest3.with_columns([s4_flag.alias("_f4"), w_d_series, iv_ok_series])
        stage_table.append({"stage": "4 iv_fail", "excluded": s4_n,
                             "surviving": rest3.height - s4_n})
        rest4 = rest3.filter(~pl.col("_f4")).drop("_f4")
        excl4 = rest3.filter(pl.col("_f4")).drop("_f4").with_columns(
            pl.lit("iv_fail").alias("excl_stage"))

        s5_flag, s5_n, centroid_fraction, flagged_table, n_total_sd, n_flagged_sd, n_no_qual = \
            stage5_centroid(rest4)

        # -- TERMINAL STOP CHECK ------------------------------------------------
        if centroid_fraction > CENTROID_STOP_FRACTION:
            log(f"[main] CENTROID STOP TRIGGERED: {centroid_fraction*100:.4f}% > "
                f"{CENTROID_STOP_FRACTION*100:.1f}%")
            flagged_rows = []
            fr = flagged_table.select(["symbol", "date_d", "S_d", "centroid"]).with_columns(
                (pl.col("S_d") / pl.col("centroid") - 1.0).abs().alias("deviation")
            )
            for row in fr.iter_rows(named=True):
                flagged_rows.append(row)
            payload = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "git_head": _git_head(),
                "outcome": "CENTROID_STOP",
                "n_raw_rows": n_raw_rows, "n_all_pairs": n_all_pairs,
                "n_eligible": n_eligible, "n_sub_base_volume": n_sub_base_volume,
                "stage_table": stage_table,
                "centroid": {"n_total_symbol_days": n_total_sd,
                             "n_no_qualifying_strike": n_no_qual,
                             "n_flagged_symbol_days": n_flagged_sd, "fraction": centroid_fraction},
                "flagged_table_rows": flagged_rows,
            }
            _write_coverage_md(payload)
            _append_attempt_log("CENTROID_STOP",
                                 detail=f"flagged_fraction={centroid_fraction*100:.4f}%")
            log("[main] COVERAGE.md written with flagged-symbol table. Exiting nonzero.")
            sys.exit(3)

        rest4 = rest4.with_columns(s5_flag.alias("_f5"))
        stage_table.append({"stage": "5 centroid", "excluded": s5_n,
                             "surviving": rest4.height - s5_n})
        rest5 = rest4.filter(~pl.col("_f5")).drop("_f5")
        excl5 = rest4.filter(pl.col("_f5")).drop("_f5").with_columns(
            pl.lit("centroid").alias("excl_stage"))

        survivors = rest5.with_columns(pl.lit(None, dtype=pl.Utf8).alias("excl_stage"))
        # recombine survivors + all 5 excluded slices (each already tagged
        # with its own excl_stage) into one table -- see build_pairs() /
        # Phase F docstring: the parquet retains ALL candidate pairs with an
        # excl_stage marker rather than silently dropping excluded ones.
        # excl1/2/3 predate the stage-4 w_d/iv_solve_ok columns -- "diagonal"
        # concat fills those as null for the earlier-excluded rows (accurate:
        # the IV solve never ran for them, since first-match attribution
        # stops evaluation at the first failing stage).
        full = pl.concat(
            [survivors, excl1, excl2, excl3, excl4, excl5], how="diagonal_relaxed",
        )

        full, n_boundary_clipped = assign_strata_cells(full)

        # null-OI disclosure (>20% of otherwise-liquid pairs -- SS F)
        would_be_liquid = full.filter(pl.col("_would_be_liquid_volume_leg") & pl.col("excl_stage").is_null())
        denom = would_be_liquid.height
        numer = int(would_be_liquid["oi_d_is_null"].sum())
        pct = (100.0 * numer / denom) if denom else 0.0
        oi_disclosure = {"denom": denom, "numer": numer, "pct": pct}
        log(f"[main] null-OI disclosure: {numer}/{denom} ({pct:.2f}%) of otherwise-liquid "
            f"surviving pairs have NULL open_interest")

        train, oos, n_boundary = split_train_oos(full, TRAIN_CUTOFF)
        log(f"[main] train={train.height:,} oos={oos.height:,} boundary_buffer={n_boundary:,}")

        write_cols = [c for c in full.columns if not c.startswith("_")]
        train_out = train.select(write_cols)
        oos_out = oos.select(write_cols)
        train_out.write_parquet(TRAIN_PARQUET, compression="snappy")
        oos_out.write_parquet(OOS_PARQUET, compression="snappy")
        train_sha = _sha256_file(TRAIN_PARQUET)
        oos_sha = _sha256_file(OOS_PARQUET)
        log(f"[main] wrote {TRAIN_PARQUET} ({train_out.height:,} rows) sha256={train_sha}")
        log(f"[main] wrote {OOS_PARQUET} ({oos_out.height:,} rows) sha256={oos_sha}")

        train_bars = train_out.filter(pl.col("is_bars_population"))
        train_bars_n = train_bars.height
        train_floor_met = train_bars_n >= TRAIN_FLOOR_N

        def _cell_table(frame, stratum_col):
            sub = frame.filter(pl.col("excl_stage").is_null() & pl.col(stratum_col))
            g = (
                sub.group_by(["cell_id", "cell_m_bin", "cell_tau_bin", "core_grid", "side"])
                .agg(pl.len().alias("n"))
            )
            out = {}
            for row in g.iter_rows(named=True):
                key = row["cell_id"]
                d = out.setdefault(key, {"cell_id": key, "cell_m_bin": row["cell_m_bin"],
                                          "cell_tau_bin": row["cell_tau_bin"],
                                          "core_grid": row["core_grid"], "n_call": 0, "n_put": 0})
                d["n_call" if row["side"] == "call" else "n_put"] += row["n"]
            return sorted(out.values(), key=lambda r: r["cell_id"])

        train_cells_base = _cell_table(train_out, "base_stratum") if "base_stratum" in train_out.columns \
            else _cell_table(train_out.with_columns(pl.lit(True).alias("base_stratum")), "base_stratum")
        train_cells_liquid = _cell_table(train_out, "liquid_stratum")

        oos_counts = _oos_counts_only(oos_out)   # CRITICAL RAIL boundary

        meta = {
            "git_head": _git_head(),
            "built_at": datetime.now().isoformat(timespec="seconds"),
            "smoke": args.smoke,
            "option_prices_span_at_build": [str(op_min), str(op_max)],
            "extraction_range": [str(extract_start), str(extract_end)],
            "n_raw_option_prices_rows": n_raw_rows,
            "n_adjacent_pairs_pre_eligibility": n_all_pairs,
            "n_eligible_pairs_built": n_eligible,
            "n_sub_base_volume_dropped": n_sub_base_volume,
            "stage_exclusion_counts": stage_table,
            "centroid_flagged_fraction": centroid_fraction,
            "oi_null_disclosure": oi_disclosure,
            "train_cutoff": str(TRAIN_CUTOFF),
            "n_boundary_buffer_pairs": n_boundary,
            "train_rows": train_out.height,
            "oos_rows": oos_out.height,
            "train_panel_sha256": train_sha,
            "oos_panel_sha256": oos_sha,
            "train_bars_population_n": train_bars_n,
            "train_floor_met": train_floor_met,
            "oos_counts_only": oos_counts,
            "n_core_cells_resolution": N_CORE_CELLS,
            "n_boundary_clipped_cells_oq3": n_boundary_clipped,
            "runtime_seconds": round(time.time() - t_start, 1),
        }
        with open(META_JSON, "w", encoding="utf-8", newline="\n") as f:
            json.dump(meta, f, indent=2, default=str)
        log(f"[main] wrote {META_JSON}")

        payload = {
            "generated_at": meta["built_at"], "git_head": meta["git_head"],
            "outcome": "SUCCESS" if train_floor_met else "SUCCESS_INSUFFICIENT_N",
            "n_raw_rows": n_raw_rows, "n_all_pairs": n_all_pairs,
            "n_eligible": n_eligible, "n_sub_base_volume": n_sub_base_volume,
            "stage_table": stage_table,
            "centroid": {"n_total_symbol_days": n_total_sd,
                         "n_no_qualifying_strike": n_no_qual,
                         "n_flagged_symbol_days": n_flagged_sd, "fraction": centroid_fraction},
            "oi_disclosure": oi_disclosure,
            "train_cells_base": train_cells_base, "train_cells_liquid": train_cells_liquid,
            "train_bars_n": train_bars_n, "train_floor_met": train_floor_met,
            "oos_counts": oos_counts, "n_boundary_buffer": n_boundary,
            "train_n": train_out.height, "oos_n": oos_out.height,
            "train_sha256": train_sha, "oos_sha256": oos_sha,
            "n_boundary_clipped": n_boundary_clipped,
        }
        _write_coverage_md(payload)
        log(f"[main] wrote {COVERAGE_PATH}")

        if not train_floor_met:
            log(f"[main] INSUFFICIENT_N: train bars-population N={train_bars_n:,} < "
                f"floor {TRAIN_FLOOR_N:,}. Per SS N, Phase 1 DEFERS WHOLE -- the fit "
                f"must NOT proceed until data accrues. Constants unchanged; re-attempt "
                f"later (re-run this script; small-table + chunk caches will be reused).")
            _append_attempt_log("INSUFFICIENT_N",
                                 detail=f"train_bars_n={train_bars_n} floor={TRAIN_FLOOR_N}")
            sys.exit(4)

        _append_attempt_log("SUCCESS", detail=f"train_n={train_out.height} oos_n={oos_out.height} "
                                               f"train_sha256={train_sha} oos_sha256={oos_sha}")
        log(f"[main] DONE in {time.time()-t_start:.1f}s")
    except SystemExit:
        raise
    except Exception as e:
        _append_attempt_log("CRASHED", detail=repr(e))
        raise


if __name__ == "__main__":
    main()
