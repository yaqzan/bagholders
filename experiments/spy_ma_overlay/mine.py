#! python3
"""
Stage-3 overlay mine: SPY {SMA,EMA} x {150,200} state / distance-band / days-in-state /
fresh-cross features, computed point-in-time, joined onto the calls-only v74 Apex MC
DD-ledger trade tape. This is Leg 3 ("Stage-3 overlay mine on OUR sleeve") of
experiments/ptj_trend_audit/PREREGISTRATION.md (PTJ 200-DMA audit, locked 2026-08-10
~01:40 ET) -- the only leg of that audit licensed to change the options sleeve.

P3 (prereg, predicted): below-200DMA-entry cohorts show EV >= rest (buy-weakness
winners, per G19/G45 trend_ma_lattice precedent), OR their low-EV dissolves once the
levers-OFF slice is applied (per G43-style redundancy with shipped RXDD/MWDD). This
mine evaluates that prediction against the full Leg-3 escalation gate (a)-(e); it does
not assume the answer.

Structural clone of experiments/market_wave_dd_v70/mine.py (tape load/concat, MIN_N=200
cohort floor, the dd_conc loss-concentration formula, window handling) and
experiments/spy_breadth_corr_dd/probe.py (the levers-OFF slice precedent, join_asof
context-join pattern). Upgrades over both clones, per this mine's brief:
  - date-clustered t/z instead of naive per-row z (the tape is many MC seeds x the
    same trade-days; a per-row z double-counts identical trade-days across seeds and
    is anticonservative -- see cluster_t()).
  - a run-length "days_in_state" feature (strictly increments within a state run,
    resets to 1 on flip) feeding days-bucket and fresh-cross(<=10d) cohorts.
  - full evaluation of the Leg-3 escalation gate (a)-(e) per cohort.

RXDD / MWDD "firing" band -- read live from strategy_config.STRATEGY_30DTE, never
hardcoded (see print_constants()):
  - RXDD: literal VIX [20, 28]. Source: strategy_config.py's own RXDD comment ("VIX
    20-28 = worst call cohort" -- the empirical finding the shipped
    Gaussian(RXDD_VIX_C=22.701, RXDD_VIX_W=3.14) was later fit to), independently
    confirmed by PREREGISTRATION.md Leg 3's own wording ("RXDD 2x2 (VIX 20-28 at
    entry vs not)"). This literal comment-sourced band is preferred over re-deriving
    an arbitrary +/-1-width slice from (VIX_C, VIX_W), which would under-cover it
    (+/-3.14 = [19.56, 25.84], narrower than the empirical finding).
  - MWDD: no equivalent literal comment band exists for MWDD, so the band is DERIVED
    from the live shipped Gaussian center+width: [MWDD_MCC_C - MWDD_MCC_W,
    MWDD_MCC_C + MWDD_MCC_W], plus the mechanism's own VIX-panic exclusion
    (vix_close < MWDD_VIX_PANIC) since VIX_PANIC is one of the "surrounding"
    MWDD_ constants read alongside MCC_C/MCC_W. This asymmetry (literal band for
    RXDD, derived band for MWDD) is a documented, deliberate choice under the HARD
    RULES "closest literal match, do not invent silently" instruction -- flag it if
    a stricter empirically-mined MWDD band supersedes this later.

HARD RULES honored: ASCII-only prints (cp1252-safe -- no unicode anywhere in output);
run with PYTHONIOENCODING=utf-8 PYTHONUTF8=1; polars infer_schema_length=None on every
row-built DataFrame; NaN-vs-null handled explicitly (np.isfinite choke point before any
z/t computation -- see cluster_t()); numpy seed 0; MySQL reads limited to MarketRegime +
MarketBreadth, single-table selects only (no SQL joins -- context is joined in polars);
no git.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/spy_ma_overlay/mine.py --selftest
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/spy_ma_overlay/mine.py --smoke
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/spy_ma_overlay/mine.py            # full run, all 12 windows
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
# G29 worktree-PYTHONPATH trap: force THIS root first even if invoked from elsewhere.
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT_DIR = ROOT / "experiments" / "spy_ma_overlay"
CACHE_DIR = ROOT / ".cache" / "spy_ma_overlay"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = CACHE_DIR / "report.json"

np.random.seed(0)

MIN_N = 200  # cohort floor, mirrors market_wave_dd_v70.MIN_N
ALL_WINDOWS = ["2018", "2020", "2020_crash", "2021", "2022", "2023", "2024",
               "2025", "dip", "22-now", "5y", "10y"]
POOL_WINDOWS = ["5y", "10y"]              # tables (1)/(4)/(5) pooled population
GATE_PRIMARY_WINDOW = "5y"                # gate (a) reference window ("pooled-5y dEV")
CANON_WINDOWS = ["2020", "2021", "2022", "2023", "2024", "2025", "dip", "22-now"]  # gate (c)

KERNELS = ["SMA", "EMA"]
LENGTHS = [150, 200]
AXIS_DIMS = ["state", "pct_band", "days_bucket", "state_days",
             "fresh_cross_below", "fresh_cross_above"]
LEVELS = {
    "state": ["above", "below"],
    "pct_band": ["<=-10%", "(-10,-3]", "(-3,0]", "(0,+3]", "(+3,+10]", ">+10%"],
    "days_bucket": ["<=10", "11-40", "41-120", ">120"],
    "state_days": [f"{s}_{d}" for s in ["above", "below"]
                   for d in ["<=10", "11-40", "41-120", ">120"]],
    "fresh_cross_below": [True, False],
    "fresh_cross_above": [True, False],
}
ALL_COHORTS = [(K, L, dim, lvl) for K in KERNELS for L in LENGTHS
               for dim in AXIS_DIMS for lvl in LEVELS[dim]]

HEADLINE_COHORTS = [
    ("SMA", 200, "state", "below"),
    ("SMA", 200, "pct_band", "<=-10%"),
    ("SMA", 200, "fresh_cross_below", True),
    ("SMA", 200, "state_days", "below_>120"),
]

LEVERS_OFF_VIX = 20.0
LEVERS_OFF_MCC_ABS = 30.0
DD_ACTIVE_ENTRY_DD = 0.13
DEEP_DEV_SCAN = -0.02     # table (3) trigger: pooled-primary-window dEV <= this
GATE_A_DEV = -0.03
GATE_A_Z = -2.5
GATE_B_DD_CONC = 1.5
GATE_C_MAX_CONTRA = 1
GATE_C_Z = 2.0
GATE_D_DEV = -0.02


# ===========================================================================
# small helpers
# ===========================================================================

def _norm_date(df: pl.DataFrame, col: str) -> pl.DataFrame:
    if col not in df.columns:
        return df
    dt = df.schema[col]
    if dt == pl.Date:
        return df
    if dt == pl.Datetime:
        return df.with_columns(pl.col(col).cast(pl.Date))
    if dt == pl.Utf8:
        return df.with_columns(pl.col(col).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return df.with_columns(pl.col(col).cast(pl.Date, strict=False))


def cohort_id(K, L, dim, level) -> str:
    return f"{K}{L}:{dim}={level}"


def fmt_num(v, nd=4, signed=False):
    if v is None:
        return "n/a"
    if isinstance(v, float) and not math.isfinite(v):
        return "n/a"
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


def _assert_finite(arr: np.ndarray, ctx: str) -> None:
    """The repo's documented silent false-NULL / NaN-is-not-null trap: a NaN sneaking
    into a mean/std computation does not raise, it silently poisons the result. This
    is the choke point -- every z/t computation in this file routes through here
    first and RAISES instead of propagating."""
    if arr.size and not np.all(np.isfinite(arr)):
        bad = arr[~np.isfinite(arr)]
        raise ValueError(
            f"non-finite value(s) entering z/t computation [{ctx}]: "
            f"{bad[:5].tolist()} (n_bad={bad.size}/{arr.size})"
        )


def cluster_t(coh_daily, rest_daily) -> tuple:
    """Date-clustered t-stat of cohort-vs-rest option_pnl, over per-entry-date
    means (Welch two-sample t).

    The tape has many MC seeds x the same trade-days, so a naive per-ROW t is
    anticonservative (it re-counts the same calendar day once per seed).
    Aggregating each side to ONE mean-pnl-per-calendar-day first (the caller's
    job -- see cohort_stats) collapses that duplication before testing.

    IMPORTANT (discovered while smoke-testing, not assumed up front): the
    SPY-MA cohorts in this file are MARKET-LEVEL features -- every trade
    entered on the same calendar date shares the identical SMA/EMA state
    (it depends only on entry_date, never on symbol/seed). So a given date is
    always WHOLLY cohort or WHOLLY rest; cohort-days and rest-days are a
    disjoint partition of the window's trading days, they never co-occur
    within a date. A one-sample test of same-date cohort-minus-rest diffs is
    therefore degenerate here (zero overlapping dates, always). The correct
    "t over dates" for a date-partitioning feature is a two-sample test
    between {per-day mean pnl on cohort days} and {per-day mean pnl on rest
    days} -- which is what this function does (Welch's t, unequal variance,
    unequal n). Returns (t or None, (n_dates_cohort, n_dates_rest))."""
    a = np.asarray(coh_daily, dtype=float)
    b = np.asarray(rest_daily, dtype=float)
    _assert_finite(a, "cluster_t cohort-day means")
    _assert_finite(b, "cluster_t rest-day means")
    na, nb = a.size, b.size
    if na < 2 or nb < 2:
        return None, (na, nb)
    va, vb = float(a.var(ddof=1)), float(b.var(ddof=1))
    se2 = va / na + vb / nb
    if se2 <= 0.0 or not math.isfinite(se2):
        return None, (na, nb)
    t = float((a.mean() - b.mean()) / math.sqrt(se2))
    return t, (na, nb)


# ===========================================================================
# tape loading (structural clone of market_wave_dd_v70/mine.py::load_tapes)
# ===========================================================================

def load_tapes(windows: list) -> pl.DataFrame:
    frames = []
    for w in windows:
        f = TAPE_DIR / f"tape_{w}.parquet"
        if not f.exists():
            print(f"!! missing {f} -- skipping window {w}")
            continue
        df = pl.read_parquet(f)
        rows_raw = df.height
        side_dist = df.group_by("side").len().sort("side")
        df = _norm_date(df, "entry_date")
        df = df.with_columns(pl.lit(w).alias("window"))
        df_calls = df.filter(pl.col("side") == "call")
        tier_dist = (df_calls.group_by("tier").len().sort("tier")
                     if "tier" in df_calls.columns else None)
        dmin = df_calls["entry_date"].min() if df_calls.height else None
        dmax = df_calls["entry_date"].max() if df_calls.height else None
        side_str = ", ".join(f"{r['side']}={r['len']}" for r in side_dist.iter_rows(named=True))
        print(f"[[{w:10s}]] rows_raw={rows_raw:>9d}  rows_calls={df_calls.height:>9d}  "
              f"dates=[{dmin} .. {dmax}]")
        print(f"    side_dist(raw)  : {side_str}")
        if tier_dist is not None:
            tier_str = ", ".join(f"{r['tier']}={r['len']}" for r in tier_dist.iter_rows(named=True))
            print(f"    tier_dist(calls): {tier_str}")
        frames.append(df_calls)
    if not frames:
        print("!! no tape data loaded")
        sys.exit(2)
    T = pl.concat(frames, how="diagonal_relaxed")
    T = T.with_columns(
        (pl.col("premium_cost") * pl.col("option_pnl")).alias("pnl_dollars"),
        (pl.col("option_pnl") < 0).alias("loser"),
    )
    return T


# ===========================================================================
# SPY-MA context: point-in-time features on the daily MarketRegime/MarketBreadth
# series (recursive/rolling only, no resample -- see PREREGISTRATION.md G28)
# ===========================================================================

def load_spy_context() -> pl.DataFrame:
    """MySQL reads limited to MarketRegime + MarketBreadth, single-table selects
    only (no SQL join -- context is combined in polars, mirroring the clone
    sources' load_context())."""
    from database.models.core import MarketBreadth, MarketRegime

    reg_rows = list(MarketRegime.select(MarketRegime.date, MarketRegime.spy_close,
                                         MarketRegime.vix_close).order_by(MarketRegime.date))
    R = pl.DataFrame({
        "date": [r.date for r in reg_rows],
        "spy_close": [float(r.spy_close) if r.spy_close is not None else None for r in reg_rows],
        "vix_close": [float(r.vix_close) if r.vix_close is not None else None for r in reg_rows],
    }, infer_schema_length=None)

    brd_rows = list(MarketBreadth.select(
        MarketBreadth.date, MarketBreadth.mcclellan_oscillator).order_by(MarketBreadth.date))
    B = pl.DataFrame({
        "date": [r.date for r in brd_rows],
        "mcc": [float(r.mcclellan_oscillator) if r.mcclellan_oscillator is not None else None
                for r in brd_rows],
    }, infer_schema_length=None)

    R = _norm_date(R, "date").sort("date").with_columns(pl.col("date").cast(pl.Date))
    B = _norm_date(B, "date").sort("date").with_columns(pl.col("date").cast(pl.Date))
    ctx = R.join(B, on="date", how="full", coalesce=True).sort("date")
    ctx = ctx.with_columns([pl.col(c).forward_fill() for c in ["spy_close", "vix_close", "mcc"]])
    print(f"   SPY/VIX/McClellan context rows={ctx.height}  "
          f"({ctx['date'].min()} .. {ctx['date'].max()})")
    return ctx


def add_spy_ma_features(ctx: pl.DataFrame) -> pl.DataFrame:
    """Per kernel {SMA,EMA} x length {150,200}: state_above, pct_dist band,
    run-length days_in_state (-> days bucket + state_days compound), and
    fresh_cross_{below,above}(<=10d). Every expression here is causal
    (rolling/recursive on the sorted daily series) -- no centered windows, no
    resampling, so there is no look-ahead by construction (verified by
    self-test (a))."""
    out = ctx.sort("date")
    close = pl.col("spy_close")
    for K in KERNELS:
        for L in LENGTHS:
            p = f"{K}{L}"
            if K == "SMA":
                ma_expr = close.rolling_mean(window_size=L)
            else:
                ma_expr = close.ewm_mean(span=L, adjust=False, min_samples=L)
            out = out.with_columns(ma_expr.alias(f"{p}_ma"))
            warm = pl.col(f"{p}_ma").is_null()

            out = out.with_columns(
                (close > pl.col(f"{p}_ma")).alias(f"{p}_above_raw"),
                (close / pl.col(f"{p}_ma") - 1.0).alias(f"{p}_pct_dist"),
            )
            out = out.with_columns(
                pl.when(warm).then(None)
                  .otherwise(pl.when(pl.col(f"{p}_above_raw")).then(pl.lit("above"))
                             .otherwise(pl.lit("below")))
                  .alias(f"{p}_state")
            )
            out = out.with_columns(
                pl.when(warm).then(None)
                  .when(pl.col(f"{p}_pct_dist") <= -0.10).then(pl.lit("<=-10%"))
                  .when(pl.col(f"{p}_pct_dist") <= -0.03).then(pl.lit("(-10,-3]"))
                  .when(pl.col(f"{p}_pct_dist") <= 0.0).then(pl.lit("(-3,0]"))
                  .when(pl.col(f"{p}_pct_dist") <= 0.03).then(pl.lit("(0,+3]"))
                  .when(pl.col(f"{p}_pct_dist") <= 0.10).then(pl.lit("(+3,+10]"))
                  .otherwise(pl.lit(">+10%"))
                  .alias(f"{p}_pct_band")
            )
            # run-length "days in current state": cumsum(state-change indicator)
            # -> run id -> cum_count within run id (order-preserving; verified by
            # self-test (d)).
            chg = (pl.col(f"{p}_state") != pl.col(f"{p}_state").shift(1)).fill_null(True)
            out = out.with_columns(chg.alias(f"_{p}_chg"))
            out = out.with_columns(pl.col(f"_{p}_chg").cum_sum().alias(f"_{p}_runid"))
            out = out.with_columns(
                pl.col(f"_{p}_chg").cast(pl.Int64).cum_count().over(f"_{p}_runid")
                  .alias(f"_{p}_dis_raw")
            )
            out = out.with_columns(
                pl.when(warm).then(None).otherwise(pl.col(f"_{p}_dis_raw"))
                  .alias(f"{p}_days_in_state")
            )
            out = out.drop([f"_{p}_chg", f"_{p}_runid", f"_{p}_dis_raw"])
            out = out.with_columns(
                pl.when(pl.col(f"{p}_days_in_state").is_null()).then(None)
                  .when(pl.col(f"{p}_days_in_state") <= 10).then(pl.lit("<=10"))
                  .when(pl.col(f"{p}_days_in_state") <= 40).then(pl.lit("11-40"))
                  .when(pl.col(f"{p}_days_in_state") <= 120).then(pl.lit("41-120"))
                  .otherwise(pl.lit(">120"))
                  .alias(f"{p}_days_bucket")
            )
            out = out.with_columns(
                pl.when(warm).then(None)
                  .otherwise(pl.col(f"{p}_state") + pl.lit("_") + pl.col(f"{p}_days_bucket"))
                  .alias(f"{p}_state_days"),
                (~warm & (pl.col(f"{p}_state") == "below") & (pl.col(f"{p}_days_in_state") <= 10))
                  .alias(f"{p}_fresh_cross_below"),
                (~warm & (pl.col(f"{p}_state") == "above") & (pl.col(f"{p}_days_in_state") <= 10))
                  .alias(f"{p}_fresh_cross_above"),
            )
            out = out.with_columns((~warm & pl.col(f"{p}_above_raw")).alias(f"{p}_above"))
            out = out.drop([f"{p}_above_raw"])
            out = out.sort("date")  # belt-and-suspenders: guarantee order for the next kernel/L pass
    return out


# ===========================================================================
# cohort statistics
# ===========================================================================

def cohort_stats(df: pl.DataFrame, axis_col: str, level, total_loss: float, total_n: int) -> dict:
    """Stats for ONE cohort cell (axis_col == level) vs the rest of `df` (same
    axis, other levels; nulls excluded from both sides). Returns None if the
    cohort or its complement is too small to trust."""
    if axis_col not in df.columns:
        return None
    coh = df.filter(pl.col(axis_col) == level)
    rest = df.filter((pl.col(axis_col) != level) & pl.col(axis_col).is_not_null())
    n = coh.height
    if n < MIN_N or rest.height == 0:
        return None
    mpnl = float(coh["option_pnl"].mean())
    rpnl = float(rest["option_pnl"].mean())
    wr = float((coh["option_pnl"] > 0).mean())
    loss_usd = coh.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 0.0
    dd_conc = ((loss_usd / total_loss) / (n / total_n)) if total_loss > 0 and total_n > 0 else None

    cd = coh.group_by("entry_date").agg(pl.col("option_pnl").mean().alias("m"))
    rd = rest.group_by("entry_date").agg(pl.col("option_pnl").mean().alias("m"))
    z, (n_dates_coh, n_dates_rest) = cluster_t(cd["m"].to_numpy(), rd["m"].to_numpy())

    return {
        "n": n, "share": n / total_n if total_n else None,
        "mean_pnl": mpnl, "rest_mean_pnl": rpnl, "dEV": mpnl - rpnl,
        "wr": wr, "z": z, "n_dates": min(n_dates_coh, n_dates_rest),
        "n_dates_cohort": n_dates_coh, "n_dates_rest": n_dates_rest,
        "dd_conc": dd_conc, "skip": False,
    }


def compute_cohort_table(df: pl.DataFrame, cohorts: list) -> dict:
    """Runs cohort_stats for every (K,L,dim,level) tuple in `cohorts` against a
    single population `df`. Returns {cohort_id: stats_dict} (stats_dict has
    skip=True and no numeric fields if the cohort was too small)."""
    total_loss = df.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    total_n = df.height
    out = {}
    for (K, L, dim, lvl) in cohorts:
        cid = cohort_id(K, L, dim, lvl)
        col = f"{K}{L}_{dim}"
        stats = cohort_stats(df, col, lvl, total_loss, total_n) if total_n else None
        if stats is None:
            out[cid] = {"cohort_id": cid, "K": K, "L": L, "dim": dim, "level": str(lvl), "skip": True}
        else:
            stats.update({"cohort_id": cid, "K": K, "L": L, "dim": dim, "level": str(lvl)})
            out[cid] = stats
    return out


# ===========================================================================
# printers
# ===========================================================================

def print_cohort_table(index: dict, title: str, sort_key="dEV"):
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)
    print(f"{'cohort_id':38s} {'n':>9s} {'share':>7s} {'mean_pnl':>9s} {'dEV':>9s} "
          f"{'wr':>6s} {'z':>7s} {'n_dt':>5s} {'dd_conc':>8s}")
    rows = [r for r in index.values() if not r.get("skip")]
    skipped = [r for r in index.values() if r.get("skip")]
    rows.sort(key=lambda r: (r[sort_key] if r.get(sort_key) is not None else 0.0))
    for r in rows:
        print(f"{r['cohort_id']:38s} {r['n']:>9d} {r['share']:>7.3f} "
              f"{fmt_num(r['mean_pnl']):>9s} {fmt_num(r['dEV'], signed=True):>9s} "
              f"{fmt_num(r['wr'], 3):>6s} {fmt_num(r['z'], 1):>7s} "
              f"{str(r['n_dates']):>5s} {fmt_num(r['dd_conc'], 2):>8s}")
    if skipped:
        names = ", ".join(r["cohort_id"] for r in skipped[:12])
        more = " ..." if len(skipped) > 12 else ""
        print(f"  ({len(skipped)} cohort(s) skipped: n<{MIN_N} or empty complement) -> {names}{more}")


def print_headline_by_window(headline_by_window: dict):
    print()
    print("=" * 100)
    print("TABLE 2: per-window dEV / sign / date-cluster-z -- 4 HEADLINE cohorts")
    print("=" * 100)
    for cid, by_win in headline_by_window.items():
        print(f"\n--- {cid} ---")
        print(f"{'window':10s} {'n':>9s} {'share':>7s} {'mean_pnl':>9s} {'dEV':>9s} "
              f"{'sign':>5s} {'z':>7s} {'n_dt':>5s}")
        for w, r in by_win.items():
            if r.get("skip"):
                print(f"{w:10s}   skip (n<{MIN_N})")
                continue
            sign = "NEG" if r["dEV"] < 0 else ("POS" if r["dEV"] > 0 else "ZERO")
            print(f"{w:10s} {r['n']:>9d} {r['share']:>7.3f} {fmt_num(r['mean_pnl']):>9s} "
                  f"{fmt_num(r['dEV'], signed=True):>9s} {sign:>5s} {fmt_num(r['z'], 1):>7s} "
                  f"{str(r['n_dates']):>5s}")


def print_2x2(cid: str, kind: str, on_stats: dict, off_stats: dict):
    def s(r):
        if r is None or r.get("skip"):
            return "skip"
        return f"n={r['n']} dEV={fmt_num(r['dEV'], signed=True)} z={fmt_num(r['z'], 1)}"
    print(f"    {kind:5s} firing=ON : {s(on_stats)}")
    print(f"    {kind:5s} firing=OFF: {s(off_stats)}")


# ===========================================================================
# self-tests
# ===========================================================================

def selftest_a_truncation(ctx_full: pl.DataFrame) -> bool:
    """(a) SPY-MA truncation no-look-ahead: recomputing the FULL feature pipeline
    on a series truncated at date d must reproduce the row for d bit-for-bit
    (float tolerance) vs the pipeline run on the full series. 20 sampled dates."""
    n = ctx_full.height
    raw = ctx_full.select(["date", "spy_close", "vix_close", "mcc"])
    lo = 250  # past the longest warmup (EMA200/SMA200 need 200 rows)
    if n <= lo + 20:
        print("  FAIL (a): series too short to sample 20 post-warmup dates")
        return False
    rng = np.random.RandomState(0)
    idx = rng.choice(np.arange(lo, n), size=20, replace=False)
    idx.sort()
    check_cols = [f"{K}{L}_{suf}" for K in KERNELS for L in LENGTHS
                  for suf in ["ma", "above", "pct_dist", "pct_band", "days_in_state",
                              "state_days", "fresh_cross_below", "fresh_cross_above"]]
    ok = True
    mismatches = 0
    for i in idx:
        d = ctx_full["date"][int(i)]
        trunc_raw = raw.filter(pl.col("date") <= d)
        trunc_feat = add_spy_ma_features(trunc_raw)
        last = trunc_feat.tail(1)
        full_row = ctx_full.filter(pl.col("date") == d)
        for c in check_cols:
            v_t, v_f = last[c][0], full_row[c][0]
            if isinstance(v_t, float) or isinstance(v_f, float):
                same = (v_t is None and v_f is None) or (
                    v_t is not None and v_f is not None
                    and math.isclose(float(v_t), float(v_f), rel_tol=1e-9, abs_tol=1e-12)
                )
            else:
                same = v_t == v_f
            if not same:
                print(f"  MISMATCH date={d} col={c}: truncated={v_t!r} full={v_f!r}")
                mismatches += 1
                ok = False
    if ok:
        print(f"  PASS (a): 20/20 sampled dates x {len(check_cols)} cols bit-identical "
              f"truncated-vs-full (no look-ahead)")
    else:
        print(f"  FAIL (a): {mismatches} mismatch(es) across 20 sampled dates")
    return ok


def selftest_b_join_coverage(ctx_feat: pl.DataFrame) -> bool:
    """(b) join coverage >=99% on tape_2024."""
    T = load_tapes(["2024"])
    T = T.sort("entry_date")
    joined = T.join_asof(ctx_feat.rename({"date": "entry_date"}), on="entry_date", strategy="backward")
    total = joined.height
    matched = joined.filter(pl.col("SMA200_ma").is_not_null()).height
    coverage = matched / total if total else 0.0
    ok = coverage >= 0.99
    tag = "PASS" if ok else "FAIL"
    print(f"  {tag} (b): join coverage on tape_2024 = {matched}/{total} = {coverage:.4%} (need >=99%)")
    if not ok:
        misses = joined.filter(pl.col("SMA200_ma").is_null())
        sample = misses.select("entry_date").unique().sort("entry_date")
        print(f"    misses (unique entry_dates, up to 20): {sample['entry_date'].to_list()[:20]}")
    return ok


def selftest_c_nan_choke(ctx_full: pl.DataFrame) -> bool:
    """(c) NaN choke point: cluster_t must RAISE (not silently propagate) on a
    non-finite input, and real point-in-time features must already be clean."""
    ok = True
    good = np.array([0.01, 0.02, 0.015, -0.01])
    try:
        cluster_t(np.array([0.01, float("nan"), 0.02]), good)
        print("  FAIL (c): cluster_t did not raise on a NaN input (cohort side)")
        ok = False
    except ValueError:
        print("  PASS (c): cluster_t raised ValueError on a manufactured NaN input (cohort side)")
    try:
        cluster_t(good, np.array([0.01, float("inf"), 0.02]))
        print("  FAIL (c): cluster_t did not raise on an Inf input (rest side)")
        ok = False
    except ValueError:
        print("  PASS (c): cluster_t raised ValueError on a manufactured Inf input (rest side)")
    sample = ctx_full.filter(pl.col("SMA200_ma").is_not_null())["SMA200_pct_dist"].to_numpy()
    if sample.size and not np.all(np.isfinite(sample)):
        print("  FAIL (c): SMA200_pct_dist contains non-finite values post-warmup")
        ok = False
    else:
        print(f"  PASS (c): SMA200_pct_dist all finite post-warmup (n={sample.size})")
    return ok


def selftest_d_run_length(ctx_full: pl.DataFrame) -> bool:
    """(d) days_in_state strictly increments within a state run, resets to 1 on flip."""
    ok = True
    for K in KERNELS:
        for L in LENGTHS:
            p = f"{K}{L}"
            sub = (ctx_full.filter(pl.col(f"{p}_ma").is_not_null())
                   .select(["date", f"{p}_state", f"{p}_days_in_state"]).sort("date"))
            states = sub[f"{p}_state"].to_list()
            days = sub[f"{p}_days_in_state"].to_list()
            bad = 0
            for i in range(1, len(states)):
                if states[i] == states[i - 1]:
                    if days[i] != days[i - 1] + 1:
                        bad += 1
                else:
                    if days[i] != 1:
                        bad += 1
            if bad:
                print(f"  FAIL (d) {p}: {bad} run-length violation(s) over {len(states)} rows")
                ok = False
            else:
                print(f"  PASS (d) {p}: run-length verified over {len(states)} rows (0 violations)")
    return ok


def selftest_e_fresh_cross(ctx_full: pl.DataFrame) -> bool:
    """(e) fresh_cross flags fire (nonzero) and are mutually exclusive with the
    opposite state (and with each other)."""
    ok = True
    for K in KERNELS:
        for L in LENGTHS:
            p = f"{K}{L}"
            sub = ctx_full.filter(pl.col(f"{p}_ma").is_not_null())
            n_below = sub.filter(pl.col(f"{p}_fresh_cross_below")).height
            n_above = sub.filter(pl.col(f"{p}_fresh_cross_above")).height
            bad_below = sub.filter(pl.col(f"{p}_fresh_cross_below") & (pl.col(f"{p}_state") != "below")).height
            bad_above = sub.filter(pl.col(f"{p}_fresh_cross_above") & (pl.col(f"{p}_state") != "above")).height
            both = sub.filter(pl.col(f"{p}_fresh_cross_below") & pl.col(f"{p}_fresh_cross_above")).height
            if n_below == 0 or n_above == 0:
                print(f"  FAIL (e) {p}: fresh_cross never fires (below={n_below} above={n_above})")
                ok = False
                continue
            if bad_below or bad_above or both:
                print(f"  FAIL (e) {p}: exclusivity violated (bad_below={bad_below} "
                      f"bad_above={bad_above} both_simultaneously={both})")
                ok = False
                continue
            print(f"  PASS (e) {p}: fresh_cross_below n={n_below}, fresh_cross_above n={n_above}, "
                  f"mutually exclusive with opposite state and each other")
    return ok


def run_selftests() -> bool:
    print("== building SPY context + features for self-tests ==")
    ctx = load_spy_context()
    ctx_feat = add_spy_ma_features(ctx)
    print("\n-- self-test (a): SPY-MA truncation no-look-ahead --")
    a = selftest_a_truncation(ctx_feat)
    print("\n-- self-test (b): join coverage >=99% on tape_2024 --")
    b = selftest_b_join_coverage(ctx_feat)
    print("\n-- self-test (c): NaN choke point --")
    c = selftest_c_nan_choke(ctx_feat)
    print("\n-- self-test (d): days_in_state run-length invariant --")
    d = selftest_d_run_length(ctx_feat)
    print("\n-- self-test (e): fresh_cross nonzero + mutually exclusive --")
    e = selftest_e_fresh_cross(ctx_feat)
    all_ok = a and b and c and d and e
    print(f"\n== SELFTEST SUMMARY: a={'PASS' if a else 'FAIL'} b={'PASS' if b else 'FAIL'} "
          f"c={'PASS' if c else 'FAIL'} d={'PASS' if d else 'FAIL'} e={'PASS' if e else 'FAIL'} "
          f"-> {'ALL PASS' if all_ok else 'FAILURES PRESENT'} ==")
    return all_ok


# ===========================================================================
# constants header
# ===========================================================================

def print_constants():
    import strategy_config as sc
    s = sc.STRATEGY_30DTE
    rxdd_lo, rxdd_hi = 20.0, 28.0
    mwdd_lo = float(s.MWDD_MCC_C) - float(s.MWDD_MCC_W)
    mwdd_hi = float(s.MWDD_MCC_C) + float(s.MWDD_MCC_W)
    print("=" * 100)
    print("CONSTANTS (read live from strategy_config.STRATEGY_30DTE)")
    print("=" * 100)
    print(f"RXDD_ENABLED={s.RXDD_ENABLED}  RXDD_VIX_C={s.RXDD_VIX_C}  RXDD_VIX_W={s.RXDD_VIX_W}  "
          f"RXDD_DEPTH={s.RXDD_DEPTH}  RXDD_DD_MIN={s.RXDD_DD_MIN}")
    print(f"  -> RXDD firing band used by this mine: VIX in [{rxdd_lo}, {rxdd_hi}]")
    print(f"     (literal band from strategy_config.py's own RXDD comment -- \"VIX 20-28 = worst")
    print(f"      call cohort\" -- the empirical finding the shipped Gaussian was fit to; matches")
    print(f"      PREREGISTRATION.md Leg-3's own \"VIX 20-28 at entry\" wording. NOT re-derived")
    print(f"      from VIX_C+/-VIX_W, which would under-cover it: [{s.RXDD_VIX_C - s.RXDD_VIX_W:.3f}, "
          f"{s.RXDD_VIX_C + s.RXDD_VIX_W:.3f}].)")
    print(f"MWDD_ENABLED={s.MWDD_ENABLED}  MWDD_MCC_C={s.MWDD_MCC_C}  MWDD_MCC_W={s.MWDD_MCC_W}  "
          f"MWDD_DEPTH={s.MWDD_DEPTH}  MWDD_DD_MIN={s.MWDD_DD_MIN}  MWDD_VIX_PANIC={s.MWDD_VIX_PANIC}")
    print(f"  -> MWDD firing band used by this mine: McClellan in [{mwdd_lo:.3f}, {mwdd_hi:.3f}]")
    print(f"     (= MWDD_MCC_C +/- MWDD_MCC_W -- no literal comment band exists for MWDD unlike")
    print(f"      RXDD, so this is DERIVED from the live shipped Gaussian center+width) AND")
    print(f"      vix_close < {s.MWDD_VIX_PANIC} (the mechanism's own VIX-panic exclusion).")
    print("=" * 100)
    return {
        "rxdd_enabled": bool(s.RXDD_ENABLED), "rxdd_vix_c": float(s.RXDD_VIX_C),
        "rxdd_vix_w": float(s.RXDD_VIX_W), "rxdd_depth": float(s.RXDD_DEPTH),
        "rxdd_dd_min": float(s.RXDD_DD_MIN), "rxdd_firing_band": [rxdd_lo, rxdd_hi],
        "mwdd_enabled": bool(s.MWDD_ENABLED), "mwdd_mcc_c": float(s.MWDD_MCC_C),
        "mwdd_mcc_w": float(s.MWDD_MCC_W), "mwdd_depth": float(s.MWDD_DEPTH),
        "mwdd_dd_min": float(s.MWDD_DD_MIN), "mwdd_vix_panic": float(s.MWDD_VIX_PANIC),
        "mwdd_firing_band": [mwdd_lo, mwdd_hi],
    }, (rxdd_lo, rxdd_hi), (mwdd_lo, mwdd_hi)


# ===========================================================================
# main mining pipeline
# ===========================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="tape_2024 only, full pipeline end-to-end")
    ap.add_argument("--selftest", action="store_true", help="run self-tests only, no mining")
    args = ap.parse_args()

    t0 = time.time()
    const_report, (rxdd_lo, rxdd_hi), (mwdd_lo, mwdd_hi) = print_constants()

    if args.selftest:
        ok = run_selftests()
        sys.exit(0 if ok else 1)

    windows = ["2024"] if args.smoke else ALL_WINDOWS
    print(f"\n== mode: {'SMOKE (tape_2024 only)' if args.smoke else 'FULL RUN'} "
          f"-- windows={windows} ==")

    print("\n== loading tapes ==")
    T = load_tapes(windows)
    loaded_windows = sorted(T["window"].unique().to_list())
    print(f"   total call trades: {T.height}   windows loaded: {loaded_windows}")

    print("\n== building SPY-MA context ==")
    ctx = load_spy_context()
    ctx_feat = add_spy_ma_features(ctx)

    print("\n== joining SPY-MA context onto tape (asof backward on entry_date) ==")
    T = T.sort("entry_date")
    T = T.join_asof(ctx_feat.rename({"date": "entry_date"}), on="entry_date", strategy="backward")
    total = T.height
    matched = T.filter(pl.col("SMA200_ma").is_not_null()).height
    coverage = matched / total if total else 0.0
    print(f"   join coverage: {matched}/{total} = {coverage:.4%}")
    if coverage < 0.99:
        misses = T.filter(pl.col("SMA200_ma").is_null()).select("entry_date").unique().sort("entry_date")
        print(f"   !! coverage <99% -- missed entry_dates (up to 20): "
              f"{misses['entry_date'].to_list()[:20]}")

    T = T.with_columns(
        ((pl.col("vix_close") >= rxdd_lo) & (pl.col("vix_close") <= rxdd_hi)).alias("rxdd_firing"),
        ((pl.col("mcc") >= mwdd_lo) & (pl.col("mcc") <= mwdd_hi)
         & (pl.col("vix_close") < const_report["mwdd_vix_panic"])).alias("mwdd_firing"),
    )

    # -- population/window bookkeeping (with smoke-mode graceful fallback) --
    pool_windows_present = [w for w in POOL_WINDOWS if w in loaded_windows]
    if not pool_windows_present:
        pool_windows_present = loaded_windows
        print(f"   NOTE: none of POOL_WINDOWS {POOL_WINDOWS} loaded -- pooling over "
              f"{loaded_windows} instead (expected in --smoke mode)")
    gate_primary = GATE_PRIMARY_WINDOW if GATE_PRIMARY_WINDOW in loaded_windows else loaded_windows[0]
    if gate_primary != GATE_PRIMARY_WINDOW:
        print(f"   NOTE: primary gate window '{GATE_PRIMARY_WINDOW}' not loaded -- using "
              f"'{gate_primary}' instead (expected in --smoke mode)")
    canon_windows_present = [w for w in CANON_WINDOWS if w in loaded_windows]
    if not canon_windows_present:
        canon_windows_present = loaded_windows
        print(f"   NOTE: none of CANON_WINDOWS loaded -- using {loaded_windows} for gate (c) "
              f"instead (expected in --smoke mode)")

    pooled_df = T.filter(pl.col("window").is_in(pool_windows_present))
    primary_df = T.filter(pl.col("window") == gate_primary)
    levers_off_df = pooled_df.filter(
        (pl.col("vix_close") < LEVERS_OFF_VIX) & (pl.col("mcc").abs() > LEVERS_OFF_MCC_ABS))
    dd_active_df = pooled_df.filter(pl.col("entry_dd") >= DD_ACTIVE_ENTRY_DD)

    print(f"\n   pool windows      : {pool_windows_present}  (n={pooled_df.height})")
    print(f"   gate primary window: {gate_primary}  (n={primary_df.height})")
    print(f"   canon windows (gate c): {canon_windows_present}")
    print(f"   levers-OFF slice (vix<{LEVERS_OFF_VIX} AND |mcc|>{LEVERS_OFF_MCC_ABS}): "
          f"n={levers_off_df.height}  ({100*levers_off_df.height/max(1,pooled_df.height):.1f}% of pool)")
    print(f"   DD-active slice (entry_dd>={DD_ACTIVE_ENTRY_DD}): n={dd_active_df.height}  "
          f"({100*dd_active_df.height/max(1,pooled_df.height):.1f}% of pool)")

    # -- core cohort tables --
    print("\n== computing cohort tables (this is the compute-heavy part) ==")
    table1_index = compute_cohort_table(pooled_df, ALL_COHORTS)                 # (1)
    primary_index = compute_cohort_table(primary_df, ALL_COHORTS)               # feeds (3)/(6a)
    table4_index = compute_cohort_table(levers_off_df, ALL_COHORTS)             # (4)/(6d)
    table5_index = compute_cohort_table(dd_active_df, ALL_COHORTS)              # (5)/(6e)
    canon_index = {w: compute_cohort_table(T.filter(pl.col("window") == w), ALL_COHORTS)
                   for w in canon_windows_present}                              # (6c)
    headline_by_window = {
        cohort_id(*hc): {w: compute_cohort_table(T.filter(pl.col("window") == w), [hc])[cohort_id(*hc)]
                         for w in loaded_windows}
        for hc in HEADLINE_COHORTS
    }                                                                            # (2)

    # -- TABLE 1 --
    print_cohort_table(table1_index, f"TABLE 1: pooled {pool_windows_present} -- all cells, sorted by dEV")

    # -- TABLE 2 --
    print_headline_by_window(headline_by_window)

    # -- TABLE 3: RXDD/MWDD 2x2 for any cohort with pooled-primary dEV <= -0.02 --
    print()
    print("=" * 100)
    print(f"TABLE 3: RXDD / MWDD 2x2 for cohorts with {gate_primary}-window dEV <= {DEEP_DEV_SCAN}")
    print("=" * 100)
    rxdd_on_df, rxdd_off_df = primary_df.filter(pl.col("rxdd_firing")), primary_df.filter(~pl.col("rxdd_firing"))
    mwdd_on_df, mwdd_off_df = primary_df.filter(pl.col("mwdd_firing")), primary_df.filter(~pl.col("mwdd_firing"))
    rxdd_on_loss = rxdd_on_df.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    rxdd_off_loss = rxdd_off_df.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    mwdd_on_loss = mwdd_on_df.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    mwdd_off_loss = mwdd_off_df.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    table3 = {}
    n_deep = 0
    for cid, pr in primary_index.items():
        if pr.get("skip") or pr["dEV"] is None or pr["dEV"] > DEEP_DEV_SCAN:
            continue
        n_deep += 1
        K, L, dim, lvl_s = pr["K"], pr["L"], pr["dim"], pr["level"]
        lvl = lvl_s
        if dim in ("fresh_cross_below", "fresh_cross_above"):
            lvl = (lvl_s == "True")
        col = f"{K}{L}_{dim}"
        r_on = cohort_stats(rxdd_on_df, col, lvl, rxdd_on_loss, rxdd_on_df.height) if rxdd_on_df.height else None
        r_off = cohort_stats(rxdd_off_df, col, lvl, rxdd_off_loss, rxdd_off_df.height) if rxdd_off_df.height else None
        m_on = cohort_stats(mwdd_on_df, col, lvl, mwdd_on_loss, mwdd_on_df.height) if mwdd_on_df.height else None
        m_off = cohort_stats(mwdd_off_df, col, lvl, mwdd_off_loss, mwdd_off_df.height) if mwdd_off_df.height else None
        print(f"\n  {cid}  (primary dEV={fmt_num(pr['dEV'], signed=True)})")
        print_2x2(cid, "RXDD", r_on, r_off)
        print_2x2(cid, "MWDD", m_on, m_off)
        table3[cid] = {"rxdd_on": r_on, "rxdd_off": r_off, "mwdd_on": m_on, "mwdd_off": m_off}
    if n_deep == 0:
        print(f"  (no cohort had {gate_primary}-window dEV <= {DEEP_DEV_SCAN})")

    # -- TABLE 4 --
    print_cohort_table(table4_index,
                        f"TABLE 4: levers-OFF slice (vix<{LEVERS_OFF_VIX} AND |mcc|>{LEVERS_OFF_MCC_ABS}) "
                        f"-- pooled {pool_windows_present}, sorted by dEV")

    # -- TABLE 5 --
    print_cohort_table(table5_index,
                        f"TABLE 5: DD-active slice (entry_dd>={DD_ACTIVE_ENTRY_DD}) "
                        f"-- pooled {pool_windows_present}, sorted by dEV")

    # -- TABLE 6: THE GATE BLOCK --
    print()
    print("=" * 100)
    print("TABLE 6: ESCALATION GATE (prereg Leg 3, (a)-(e)) -- every cohort")
    print("=" * 100)
    gate_results = {}
    n_escalate = 0
    for (K, L, dim, lvl) in ALL_COHORTS:
        cid = cohort_id(K, L, dim, lvl)
        pr = primary_index.get(cid, {})
        t1 = table1_index.get(cid, {})
        t4 = table4_index.get(cid, {})
        t5 = table5_index.get(cid, {})

        # (a) primary-window dEV<=-0.03 AND cluster-z<=-2.5
        a_val_dev = pr.get("dEV") if not pr.get("skip") else None
        a_val_z = pr.get("z") if not pr.get("skip") else None
        pass_a = (a_val_dev is not None and a_val_z is not None
                  and a_val_dev <= GATE_A_DEV and a_val_z <= GATE_A_Z)

        # (b) pooled dd_conc>=1.5
        b_val = t1.get("dd_conc") if not t1.get("skip") else None
        pass_b = b_val is not None and b_val >= GATE_B_DD_CONC

        # (c) <=1 contradiction at |z|>=2 across canon windows, not crash(2020)-only-driven
        contra, support = [], []
        for w in canon_windows_present:
            rw = canon_index.get(w, {}).get(cid)
            if rw is None or rw.get("skip") or rw.get("z") is None:
                continue
            if rw["z"] >= GATE_C_Z:
                contra.append(w)
            elif rw["z"] <= -GATE_C_Z:
                support.append(w)
        crash_driven = len(support) >= 1 and set(support) == {"2020"}
        pass_c = (len(contra) <= GATE_C_MAX_CONTRA) and (not crash_driven)

        # (d) levers-OFF dEV<=-0.02
        d_val = t4.get("dEV") if not t4.get("skip") else None
        pass_d = d_val is not None and d_val <= GATE_D_DEV

        # (e) DD-active consistency: sign-preserved (dEV<=0) in the DD-active slice.
        # No explicit magnitude threshold was given in the brief for (e); this is a
        # documented interpretation (same-sign persistence), flagged in the report.
        e_val = t5.get("dEV") if not t5.get("skip") else None
        pass_e = e_val is not None and e_val <= 0.0

        escalate = pass_a and pass_b and pass_c and pass_d and pass_e
        if escalate:
            n_escalate += 1
        gate_results[cid] = {
            "a": {"pass": pass_a, "dEV": a_val_dev, "z": a_val_z},
            "b": {"pass": pass_b, "dd_conc": b_val},
            "c": {"pass": pass_c, "n_contra": len(contra), "contra_windows": contra,
                  "support_windows": support, "crash_driven": crash_driven},
            "d": {"pass": pass_d, "dEV": d_val},
            "e": {"pass": pass_e, "dEV": e_val},
            "escalate": escalate,
        }
        flag = "  <== ESCALATE" if escalate else ""
        print(f"{cid:38s} a[{('T' if pass_a else 'F')} dEV={fmt_num(a_val_dev, signed=True)} "
              f"z={fmt_num(a_val_z, 1)}] b[{('T' if pass_b else 'F')} {fmt_num(b_val, 2)}] "
              f"c[{('T' if pass_c else 'F')} contra={len(contra)}{contra} crash={crash_driven}] "
              f"d[{('T' if pass_d else 'F')} {fmt_num(d_val, signed=True)}] "
              f"e[{('T' if pass_e else 'F')} {fmt_num(e_val, signed=True)}] "
              f"-> ESCALATE:{'YES' if escalate else 'NO'}{flag}")
    print(f"\n  {n_escalate} of {len(ALL_COHORTS)} cohort(s) ESCALATE: YES")

    # -- write report.json --
    def _clean(o):
        if isinstance(o, dict):
            return {str(k): _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        if isinstance(o, float) and not math.isfinite(o):
            return None
        return o

    report = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "smoke": bool(args.smoke),
            "windows_loaded": loaded_windows,
            "pool_windows_used": pool_windows_present,
            "gate_primary_window": gate_primary,
            "canon_windows_used": canon_windows_present,
            "min_n": MIN_N,
            "levers_off_vix": LEVERS_OFF_VIX, "levers_off_mcc_abs": LEVERS_OFF_MCC_ABS,
            "dd_active_entry_dd": DD_ACTIVE_ENTRY_DD, "deep_dev_scan": DEEP_DEV_SCAN,
            "gate_thresholds": {"a_dEV": GATE_A_DEV, "a_z": GATE_A_Z, "b_dd_conc": GATE_B_DD_CONC,
                                 "c_max_contra": GATE_C_MAX_CONTRA, "c_z": GATE_C_Z, "d_dEV": GATE_D_DEV,
                                 "e_note": "documented interpretation: dEV<=0 (sign persistence), "
                                           "no explicit magnitude given in brief"},
            "constants": const_report,
            "total_call_trades": T.height,
            "join_coverage": coverage,
        },
        "table1_pooled": table1_index,
        "table2_headline_by_window": headline_by_window,
        "table3_rxdd_mwdd_2x2": table3,
        "table4_levers_off": table4_index,
        "table5_dd_active": table5_index,
        "table6_gate": gate_results,
        "primary_window_all_cohorts": primary_index,
        "canon_window_all_cohorts": canon_index,
    }
    REPORT_PATH.write_text(json.dumps(_clean(report), indent=2, default=str))

    elapsed = time.time() - t0
    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"mode                 : {'SMOKE' if args.smoke else 'FULL RUN'}")
    print(f"windows loaded       : {loaded_windows}")
    print(f"total call trades    : {T.height}")
    print(f"join coverage        : {coverage:.4%}")
    print(f"cohorts evaluated    : {len(ALL_COHORTS)}  (4 kernel/length x 6 axes x levels)")
    print(f"headline cohorts     : {[cohort_id(*hc) for hc in HEADLINE_COHORTS]}")
    print(f"cohorts w/ {gate_primary}-dEV<={DEEP_DEV_SCAN} (table 3 trigger): {n_deep}")
    print(f"cohorts ESCALATE:YES : {n_escalate} / {len(ALL_COHORTS)}")
    if n_escalate:
        esc_ids = [cid for cid, g in gate_results.items() if g["escalate"]]
        print(f"  -> {esc_ids}")
    print(f"report written to    : {REPORT_PATH}")
    print(f"elapsed              : {elapsed:.1f}s")
    print("=" * 100)


if __name__ == "__main__":
    main()
