#! python3
"""Leg 2 - PTJ 200-DMA Trend Theory - UNIVERSE per-stock, SURVIVORSHIP-HONEST audit.

See experiments/ptj_trend_audit/PREREGISTRATION.md (locked 2026-08-10) for the
full spec. This script BUILDS the pipeline and its self-tests, and supports
--smoke (a small, fast, end-to-end proof the pipeline works). Per the build
brief: the FULL run (~1626 symbols x ~40-96 grid cells) is queued by the
orchestrator and is NOT run by this script's author -- --full exists as a
real code path so the orchestrator has an exact command to queue, but it is
never invoked here.

Substrate: .cache/ptj_trend_audit/universe_1997.parquet
  schema [symbol Utf8, date Date, close Float64, volume Float64?, delisted_date Date?]
If that file does not exist yet, --smoke synthesizes its own small frame
(does not block the build on the pull job finishing).

--------------------------------------------------------------------------
MECHANISM SPEC (as implemented; matches PREREGISTRATION.md Leg 2)
--------------------------------------------------------------------------
Eligibility: a name enters the rule only at its own bar index >= length+19
(0-based bar_idx; i.e. it needs to have accumulated length+20 bars). This is
STRICTLY LATER than the MA's own mathematical warmup (length-1 bars), by
design -- it is the young-listing guard, separate from and in addition to
the MA-warmup requirement.

Per-name STATE (same causal two-threshold + forward_fill + fill(0) construction
as experiments/ptj_trend_audit/spy_claim.py, vectorized here via polars
`.over("symbol")` window expressions instead of numpy loops): computed from
the name's OWN full price history (rolling_mean / ewm_mean with
min_samples=length so SMA and EMA share warmup treatment -- documented in
spy_claim.py, same choice here for the same comparability reason).

PER-NAME SLEEVE mode ("the clean test of the rule"): each name's equity is
indexed to 1.0 AT ITS OWN first-eligible bar (not at its IPO bar). This is
implemented via a "neutral multiplier" trick: pre-eligibility rows get a
1.0 multiplier (no-op) fed into a per-symbol cum_prod; once eligible, the
real (1+net-return) multiplier takes over. This gives an exactly-causal,
fully-vectorized equivalent of "start compounding at eligibility" with no
python loop over symbols. A name terminates (delists) at its last data bar;
after that its per-name sleeve equity is FROZEN at the terminal value and
padded forward (via a small cross-join limited to names whose last bar
precedes the global max date) so the cross-sectional average continues to
include it at a constant value -- "matching a buy-and-hold-to-death
investor" (explicit prereg instruction). It is never phantom-continued
(no fabricated returns after the last bar) and never silently dropped
(dropping would look-ahead-bias the survivor set out of the average).
Portfolio path = date-wise mean of all currently-included (eligible,
possibly-frozen) per-name sleeve equities.

PORTFOLIO mode (monthly-eval ONLY, "embeds a breadth-timing bet"): at each
calendar month-end, every currently-eligible, not-yet-terminated name is
classified in-state (1) or not (0) using that date's state. The following
month, the portfolio is 100% deployed, equally split ONLY across the
in-state names (weight = 1/n_in_state; out-of-state names get weight 0 --
their capital is NOT held in per-name cash, it is redeployed into whichever
names DO qualify). If zero names qualify at an eval date, the portfolio
sits in cash for that month. Turnover cost = sum of |weight change| per
name * cost_bps/1e4, charged on the first holding day of the new month
(t+1-consistent). This does NOT need the delisted-padding/densification
step: a delisted name simply stops having rows and drops out of the
cross-section with zero special-casing (unlike sleeve mode, which must
freeze it to avoid a look-ahead-biased shrinking denominator).

MONTHLY-EVAL rebalance (per-name mode): the per-name STATE is resampled at
month-ends only (sampled value forward-filled through the month, same
month-end flag construction as spy_claim.py's build_month_end_idx) before
being fed through the identical t+1/cost/sleeve machinery used for the
daily-rebalance variant.

B&H comparison arm: "same eligibility, no filter" -- i.e. exposure=1 from
the name's own first-eligible bar through its last bar, zero transitions,
zero cost. Built with the identical sleeve/padding machinery so it is
directly comparable to the filtered arm.

DEVIATION NOTE (declared, not silent): PREREGISTRATION.md does not give
column-level locked schemas for universe_grid.parquet, delisted_cohort.parquet,
or spread_panel.parquet (unlike spy_grid.parquet's fully-locked 20-column
schema, and unlike universe_1997.parquet's explicit input schema). Schemas
below were DESIGNED from the P2a-P2d prose to carry every number those gates
need, plus a small number of clearly-marked diagnostic extras. Flagged here
per the "if a locked field is impossible/missing, say so" rule -- this is
the analogous case of an INCOMPLETE lock rather than an impossible field.

DEVIATION NOTE 2: the spread-panel block-bootstrap block size is not locked
anywhere in the prereg (only Leg 1's canonical-cell bootstrap specifies
block=63 DAYS; the spread panel is inherently a MONTHLY series). Implemented
block=3 MONTHS, N=1000, seed=0 -- a reasonable analogous choice, explicitly
flagged as not-locked.

Run:
    python experiments/ptj_trend_audit/universe_claim.py                # selftests only (offline, no file needed)
    python experiments/ptj_trend_audit/universe_claim.py --smoke        # + small (30-symbol) end-to-end run + summary
    python experiments/ptj_trend_audit/universe_claim.py --full         # THE FULL RUN -- orchestrator queues this, do not run here

Outputs (under .cache/ptj_trend_audit/), --smoke writes them with a
"_smoke" suffix so they never collide with (or get mistaken for) the
orchestrator's real --full outputs:
    universe_grid.parquet / universe_grid_smoke.parquet
    delisted_cohort.parquet / delisted_cohort_smoke.parquet
    spread_panel.parquet / spread_panel_smoke.parquet
"""
import sys

_ROOT = r"C:\Development\Trader"
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import mannwhitneyu, norm

OUT_DIR = Path(_ROOT) / ".cache" / "ptj_trend_audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)
UNIVERSE_PATH = OUT_DIR / "universe_1997.parquet"

TRADING_DAYS_YR = 252.0
YOUNG_GUARD_BARS = 20  # "L+20" young-listing guard, on top of the MA's own L-bar warmup

# ============================================================================
# LOCKED GRID (PREREGISTRATION.md Leg 2)
# ============================================================================
KERNELS = ["SMA", "EMA"]
LENGTHS = [50, 100, 150, 200]
BANDS = [0.0, 0.03]
COST_BPS_GRID = [0, 25]
MODE_REBALANCE = [
    ("per_name", "daily"),
    ("per_name", "monthly_eval"),
    ("portfolio", "monthly_eval"),
]
CANONICAL = dict(kernel="SMA", length=200, band=0.0, cost_bps=25, mode="per_name", rebalance="daily")

ERAS = [
    ("1995-2002", "1995-01-01", "2002-12-31"),
    ("2003-2007", "2003-01-01", "2007-12-31"),
    ("2008-2012", "2008-01-01", "2012-12-31"),
    ("2013-2019", "2013-01-01", "2019-12-31"),
    ("2020-2021", "2020-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023-now", "2023-01-01", "2100-01-01"),
]

GRID_COLUMNS = [
    "rule_id", "kernel", "length", "band_pct", "cost_bps", "mode", "rebalance",
    "cagr", "cagr_bh", "maxdd", "maxdd_bh", "sharpe", "sharpe_bh",
    "time_in_mkt", "trades_per_yr", "worst_year", "nw_t_excess", "n_names_avg",
]
DELISTED_COHORT_COLUMNS = [
    "symbol", "cohort", "death_proxy", "first_eligible_date", "last_date", "n_bars",
    "bh_lifetime_return", "filtered_lifetime_return", "improvement",
    "filtered_le_neg80", "filtered_le_neg50", "bh_le_neg80", "bh_le_neg50",
]
SPREAD_PANEL_COLUMNS = [
    "kernel", "length", "date", "above_ret", "below_ret", "spread", "n_above", "n_below",
]


# ============================================================================
# Data loading
# ============================================================================
def load_universe():
    if not UNIVERSE_PATH.exists():
        return None
    df = pl.read_parquet(UNIVERSE_PATH)
    return df.sort(["symbol", "date"])


def make_synthetic_universe(n_symbols=30, n_delisted=10, n_days=1400, seed=0):
    """Synthetic panel for --smoke when the real parquet isn't built yet.
    n_delisted of the n_symbols terminate early (staggered) to exercise the
    delisted-cohort / termination machinery even with no real data."""
    rng = np.random.default_rng(seed)
    start = dt.date(2015, 1, 1)
    all_dates = [start + dt.timedelta(days=i) for i in range(n_days)]
    # business-day-ish calendar (drop weekends) so gaps look like a real market
    all_dates = [d for d in all_dates if d.weekday() < 5]
    n = len(all_dates)

    rows = {"symbol": [], "date": [], "close": [], "volume": [], "delisted_date": []}
    for s in range(n_symbols):
        sym = f"SYN{s:02d}"
        drift = rng.uniform(-0.0002, 0.0006)
        vol = rng.uniform(0.012, 0.03)
        close = 20.0 * np.cumprod(1.0 + rng.normal(drift, vol, n))
        # staggered listing: some names start later than day 0
        listing_offset = int(rng.integers(0, 200)) if s > 5 else 0
        if s < n_delisted:
            # staggered death: ends somewhere in the middle third of the series
            death_idx = int(rng.integers(int(n * 0.35), int(n * 0.75)))
            sym_dates = all_dates[listing_offset:death_idx]
            sym_close = close[listing_offset:death_idx]
            delisted_date = sym_dates[-1]
        else:
            sym_dates = all_dates[listing_offset:]
            sym_close = close[listing_offset:]
            delisted_date = None
        rows["symbol"].extend([sym] * len(sym_dates))
        rows["date"].extend(sym_dates)
        rows["close"].extend(sym_close.tolist())
        rows["volume"].extend((rng.uniform(1e5, 1e7, len(sym_dates))).tolist())
        rows["delisted_date"].extend([delisted_date] * len(sym_dates))

    df = pl.DataFrame(rows, infer_schema_length=None)
    df = df.with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
        pl.col("delisted_date").cast(pl.Date),
    )
    return df.sort(["symbol", "date"])


# ============================================================================
# Feature building (pure polars, vectorized over("symbol"), no python loops
# over symbols)
# ============================================================================
def add_bar_idx(df):
    return df.with_columns(pl.int_range(0, pl.len()).over("symbol").alias("bar_idx"))


def add_ma_columns(df, combos):
    """combos: iterable of (kernel, length). Adds one column per combo,
    named ma_{kernel}{length}. NaN/null before `length` samples via
    min_samples=length -- identical warmup-parity choice to spy_claim.py."""
    exprs = []
    for kernel, length in combos:
        col = f"ma_{kernel}{length}"
        if kernel == "SMA":
            exprs.append(pl.col("close").rolling_mean(window_size=length, min_samples=length).over("symbol").alias(col))
        else:
            exprs.append(pl.col("close").ewm_mean(span=length, adjust=False, min_samples=length).over("symbol").alias(col))
    return df.with_columns(exprs)


def add_state_column(df, ma_col, band, out_col):
    """Causal two-threshold state: above upper=1, below lower=0, else null
    -> forward_fill (per symbol) -> fill_null(0) [start in cash]. Identical
    semantics to spy_claim.py's state_price_ma, expressed as polars exprs."""
    upper = pl.col(ma_col) * (1.0 + band)
    lower = pl.col(ma_col) * (1.0 - band)
    raw = (
        pl.when(pl.col("close") > upper).then(1.0)
        .when(pl.col("close") < lower).then(0.0)
        .otherwise(None)
    )
    return df.with_columns(
        raw.forward_fill().over("symbol").fill_null(0.0).alias(out_col)
    )


def add_monthly_eval_flag(df):
    ym = pl.col("date").dt.strftime("%Y-%m")
    is_month_end = (ym.shift(-1).over("symbol") != ym).fill_null(True)
    return df.with_columns(is_month_end.alias("is_month_end"))


def resample_state_monthly(df, state_col, out_col):
    """Sample state_col only at month-ends, forward-fill through the month
    (per symbol). Leading rows before the first-ever month-end default to 0
    (cash) -- consistent with the base state's own fallback default."""
    sampled = pl.when(pl.col("is_month_end")).then(pl.col(state_col)).otherwise(None)
    return df.with_columns(
        sampled.forward_fill().over("symbol").fill_null(0.0).alias(out_col)
    )


# ============================================================================
# Per-name simulate (vectorized) -> sleeve equity anchored at eligibility
# ============================================================================
def simulate_per_name(df, state_col, cost_bps, cash_yield_pct=0.0):
    """Adds: ret, exposure, transitions, strat_ret, sleeve_equity (eligibility-
    anchored to 1.0, null before eligibility), bh_sleeve_equity (same
    eligibility, zero-cost always-in comparator)."""
    daily_cash = (1.0 + cash_yield_pct) ** (1.0 / TRADING_DAYS_YR) - 1.0

    df = df.with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).fill_null(0.0).alias("ret")
    )
    df = df.with_columns(
        pl.col(state_col).shift(1).over("symbol").fill_null(0.0).alias("exposure")
    )
    df = df.with_columns(
        (pl.col("exposure") != pl.col("exposure").shift(1).over("symbol").fill_null(0.0))
        .cast(pl.Float64).alias("transitions")
    )
    df = df.with_columns(
        (
            pl.col("exposure") * pl.col("ret")
            + (1.0 - pl.col("exposure")) * daily_cash
            - pl.col("transitions") * (cost_bps / 10000.0)
        ).alias("strat_ret")
    )
    eligible = pl.col("bar_idx") >= (pl.col("_length") + YOUNG_GUARD_BARS - 1)
    df = df.with_columns(eligible.alias("eligible"))

    sleeve_mult = pl.when(pl.col("eligible")).then(1.0 + pl.col("strat_ret")).otherwise(1.0)
    df = df.with_columns(sleeve_mult.cum_prod().over("symbol").alias("_sleeve_raw"))
    df = df.with_columns(
        pl.when(pl.col("eligible")).then(pl.col("_sleeve_raw")).otherwise(None).alias("sleeve_equity")
    ).drop("_sleeve_raw")

    bh_mult = pl.when(pl.col("eligible")).then(1.0 + pl.col("ret")).otherwise(1.0)
    df = df.with_columns(bh_mult.cum_prod().over("symbol").alias("_bh_raw"))
    df = df.with_columns(
        pl.when(pl.col("eligible")).then(pl.col("_bh_raw")).otherwise(None).alias("bh_sleeve_equity")
    ).drop("_bh_raw")

    return df


def pad_and_aggregate_sleeve(df, value_col, global_max_date, all_dates):
    """Freeze each name's `value_col` at its terminal (last-bar) value and
    pad forward to global_max_date (cross-join limited to names that need
    padding), then average across all currently-included names per date.
    Names before their own eligibility are simply absent (null) and are
    excluded by polars' null-skipping mean -- never phantom-continued,
    never silently dropped once eligible.

    `all_dates` MUST be the true universe-wide date axis (one 'date' column,
    every trading day across the WHOLE loaded frame), not inferred from
    `df` itself: `df` here is often a narrow slice (one grid cell's columns,
    or in a self-test a single symbol) whose own date range can fall well
    short of the true calendar, which would silently produce zero padding
    rows for any name that isn't currently the longest-lived one in the
    slice. Caught by selftest_c_delisted_termination on a single-symbol
    frame; passing the axis explicitly removes the implicit "this slice
    happens to span the full calendar" assumption entirely."""
    real = df.filter(pl.col(value_col).is_not_null()).select(["symbol", "date", value_col])

    meta = (
        df.filter(pl.col(value_col).is_not_null())
        .group_by("symbol", maintain_order=True)
        .agg(pl.col("date").last().alias("last_date"), pl.col(value_col).last().alias("term_val"))
    )
    pad_meta = meta.filter(pl.col("last_date") < global_max_date)

    if pad_meta.height > 0:
        pad_rows = (
            pad_meta.join(all_dates, how="cross")
            .filter(pl.col("date") > pl.col("last_date"))
            .select(["symbol", "date", pl.col("term_val").alias(value_col)])
        )
        full = pl.concat([real, pad_rows], how="vertical")
    else:
        full = real

    port = full.group_by("date").agg(pl.col(value_col).mean().alias("portfolio_equity"),
                                      pl.col(value_col).len().alias("n_names")).sort("date")
    return port


# ============================================================================
# Portfolio mode (cross-sectional renormalization, monthly-eval only)
# ============================================================================
def build_portfolio_mode(df, state_col, cost_bps, cash_yield_pct=0.0):
    """Returns (port_df with columns [date, portfolio_ret, n_instate],
    bh_port_df with columns [date, portfolio_ret]) at monthly-eval,
    t+1-consistent. Needs no padding: a delisted name just stops appearing
    in the cross-section from its last bar onward (no special-casing)."""
    daily_cash = (1.0 + cash_yield_pct) ** (1.0 / TRADING_DAYS_YR) - 1.0
    active = df.filter(pl.col("eligible"))

    eval_frame = (
        active.filter(pl.col("is_month_end"))
        .select(["symbol", "date", state_col])
        .sort(["symbol", "date"])
    )
    monthly_agg = eval_frame.group_by("date").agg(pl.col(state_col).sum().alias("n_instate")).sort("date")
    eval_frame = eval_frame.join(monthly_agg, on="date", how="left")
    eval_frame = eval_frame.with_columns(
        pl.when((pl.col(state_col) == 1.0) & (pl.col("n_instate") > 0))
        .then(1.0 / pl.col("n_instate"))
        .otherwise(0.0)
        .alias("weight_next")
    )
    eval_frame = eval_frame.with_columns(
        (pl.col("weight_next") - pl.col("weight_next").shift(1).over("symbol").fill_null(0.0)).abs().alias("weight_change")
    )
    turnover_by_date = eval_frame.group_by("date").agg(pl.col("weight_change").sum().alias("turnover")).sort("date")

    active = active.join(
        eval_frame.select(["symbol", "date", "weight_next"]), on=["symbol", "date"], how="left"
    )
    active = active.with_columns(
        pl.col("weight_next").forward_fill().over("symbol").fill_null(0.0).alias("weight")
    )
    active = active.with_columns(
        pl.col("weight").shift(1).over("symbol").fill_null(0.0).alias("exposure_weight")
    )

    daily = active.group_by("date").agg(
        (pl.col("exposure_weight") * pl.col("ret")).sum().alias("invested_ret"),
        pl.col("exposure_weight").sum().alias("total_weight"),
    ).sort("date")
    daily = daily.join(turnover_by_date, on="date", how="left").with_columns(pl.col("turnover").fill_null(0.0))
    # turnover cost is realized on the day the new weights take effect (t+1
    # of the eval date -> the eval date's turnover cost is charged on the
    # NEXT trading day's return, matching the same t+1 lag as `weight`
    # itself, which is shifted by one day into exposure_weight above).
    daily = daily.with_columns(pl.col("turnover").shift(1).fill_null(0.0).alias("turnover_lag1"))
    daily = daily.with_columns(
        (
            pl.col("invested_ret")
            + (1.0 - pl.col("total_weight")) * daily_cash
            - pl.col("turnover_lag1") * (cost_bps / 10000.0)
        ).alias("portfolio_ret")
    )

    # B&H comparator for portfolio mode: equal-weight across ALL currently-
    # eligible (not-yet-terminated) names every day, no state filter, no cost.
    bh_daily = active.group_by("date").agg(pl.col("ret").mean().alias("portfolio_ret")).sort("date")

    return daily.select(["date", "portfolio_ret"]), bh_daily, monthly_agg


# ============================================================================
# Metrics (numpy, operating on the small date-indexed aggregated series)
# ============================================================================
def max_dd_vec(equity, base=1.0):
    full = np.concatenate(([base], equity))
    peak = np.maximum.accumulate(full)
    return float((full / peak - 1.0).min())


def sharpe_ratio(ret):
    sd = ret.std(ddof=1)
    if sd <= 0 or not np.isfinite(sd):
        return float("nan")
    return float(ret.mean() / sd * np.sqrt(TRADING_DAYS_YR))


def annual_returns(dates_np, equity):
    years = dates_np.astype("datetime64[Y]").astype(int) + 1970
    uniq_years = sorted(set(years.tolist()))
    year_end_val = {y: equity[np.where(years == y)[0][-1]] for y in uniq_years}
    out = []
    for i, y in enumerate(uniq_years):
        if i == 0:
            continue
        out.append((y, float(year_end_val[y] / year_end_val[uniq_years[i - 1]] - 1.0)))
    return out


def build_month_end_idx(dates_np):
    ym = dates_np.astype("datetime64[M]")
    idx = np.where(ym[1:] != ym[:-1])[0]
    return np.append(idx, len(dates_np) - 1)


def monthly_returns_from_equity(equity, month_end_idx):
    path = np.concatenate(([1.0], equity[month_end_idx]))
    return path[1:] / path[:-1] - 1.0


def newey_west_t(x, lag=3):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < lag + 2:
        return float("nan")
    mean = x.mean()
    u = x - mean
    s = float((u * u).sum()) / n
    for l in range(1, lag + 1):
        if l >= n:
            break
        cov = float((u[l:] * u[:-l]).sum()) / n
        w = 1.0 - l / (lag + 1)
        s += 2.0 * w * cov
    var_mean = s / n
    if not (var_mean > 0):
        return float("nan")
    return float(mean / np.sqrt(var_mean))


def _assert_finite(arr, name):
    arr = np.asarray(arr, dtype=float)
    if arr.size and not np.all(np.isfinite(arr)):
        n_bad = int((~np.isfinite(arr)).sum())
        raise AssertionError(f"NaN/Inf choke: {name} has {n_bad} non-finite value(s) reaching a stat computation")


def circular_block_bootstrap_mean_t(x, block, n_boot=1000, seed=0):
    """Block-bootstrap SE on the mean of x (used for the spread-panel t,
    since that panel is monthly and Leg 1's canonical daily-block bootstrap
    doesn't directly translate -- see DEVIATION NOTE 2 in the module
    docstring: block size here is NOT locked by the prereg)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < block * 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    means = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]) % n
        idx = idx.reshape(-1)[:n]
        means[b] = x[idx].mean()
    se = means.std(ddof=1)
    if se <= 0:
        return float("nan"), float("nan")
    t = float(x.mean() / se)
    return t, float(se)


def mannwhitney_z(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x = x[np.isfinite(x)]
    y = y[np.isfinite(y)]
    if len(x) < 2 or len(y) < 2:
        return float("nan")
    res = mannwhitneyu(x, y, alternative="two-sided", method="asymptotic")
    mu = len(x) * len(y) / 2.0
    sign = 1.0 if res.statistic >= mu else -1.0
    p = max(res.pvalue, 1e-300)
    return float(sign * norm.isf(p / 2.0))


def cell_metrics(dates_np, equity, ret, bh_equity):
    n = len(equity)
    cagr = float(equity[-1] ** (TRADING_DAYS_YR / n) - 1.0)
    cagr_bh = float(bh_equity[-1] ** (TRADING_DAYS_YR / n) - 1.0)
    maxdd = max_dd_vec(equity)
    maxdd_bh = max_dd_vec(bh_equity)
    shrp = sharpe_ratio(ret)
    bh_ret = np.zeros(n)
    bh_ret[1:] = bh_equity[1:] / bh_equity[:-1] - 1.0
    shrp_bh = sharpe_ratio(bh_ret)
    worst_year = min(r for _, r in annual_returns(dates_np, equity))
    month_end_idx = build_month_end_idx(dates_np)
    monthly_strat = monthly_returns_from_equity(equity, month_end_idx)
    monthly_bh = monthly_returns_from_equity(bh_equity, month_end_idx)
    excess = monthly_strat - monthly_bh
    _assert_finite(excess, "cell_metrics.monthly_excess")
    nw_t = newey_west_t(excess, lag=3)
    return dict(cagr=cagr, cagr_bh=cagr_bh, maxdd=maxdd, maxdd_bh=maxdd_bh,
                sharpe=shrp, sharpe_bh=shrp_bh, worst_year=worst_year, nw_t_excess=nw_t)


# ============================================================================
# Self-tests
# ============================================================================
def _pick_selftest_symbols(df, n=5):
    counts = df.group_by("symbol").agg(pl.len().alias("n")).filter(pl.col("n") >= 300)
    syms = counts.sort("n", descending=True).head(n)["symbol"].to_list()
    return syms


def selftest_a_no_lookahead(df):
    syms = _pick_selftest_symbols(df, n=5)
    rng = np.random.default_rng(0)
    checked = 0
    for sym in syms:
        one = df.filter(pl.col("symbol") == sym).sort("date")
        n = one.height
        one = add_bar_idx(one)
        one = add_ma_columns(one, [("SMA", 200)])
        one = add_state_column(one, "ma_SMA200", 0.0, "state_full")
        state_full = one["state_full"].to_numpy()
        dates_all = one["date"].to_numpy()

        lo = min(250, n - 1)
        if lo >= n - 1:
            continue
        sample_idx = rng.choice(np.arange(lo, n), size=min(20, n - lo), replace=False)
        for d in sample_idx:
            trunc = one.head(d + 1).drop(["bar_idx", "ma_SMA200", "state_full"])
            trunc = add_bar_idx(trunc)
            trunc = add_ma_columns(trunc, [("SMA", 200)])
            trunc = add_state_column(trunc, "ma_SMA200", 0.0, "state_full")
            trunc_last = trunc["state_full"][-1]
            assert trunc_last == state_full[d], (
                f"look-ahead leak: symbol={sym} d={d} date={dates_all[d]} "
                f"trunc={trunc_last} full={state_full[d]}"
            )
            checked += 1
    assert checked > 0, "selftest (a) found no eligible symbols to check -- data too thin"
    print(f"(a) no-look-ahead truncation: PASS ({checked} checks across {len(syms)} symbols, production polars pipeline)")


def selftest_b_synthetic_known_value():
    close = np.array([100.0] * 10 + [200.0] * 10 + [50.0] * 10)
    one = pl.DataFrame({
        "symbol": ["ZZZ"] * 30,
        "date": [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(30)],
        "close": close, "volume": [1e6] * 30, "delisted_date": [None] * 30,
    }, infer_schema_length=None).with_columns(pl.col("date").cast(pl.Date), pl.col("delisted_date").cast(pl.Date))

    one = add_bar_idx(one)
    one = add_ma_columns(one, [("SMA", 5)])
    one = add_state_column(one, "ma_SMA5", 0.0, "state")
    state = one["state"].to_numpy()
    expected_state = np.array([0.0] * 10 + [1.0] * 10 + [0.0] * 10)
    assert np.array_equal(state, expected_state), f"state mismatch: {state.tolist()}"

    one = one.with_columns(pl.lit(5).alias("_length"))
    sim0 = simulate_per_name(one, "state", cost_bps=0)
    sim25 = simulate_per_name(one, "state", cost_bps=25)
    trans = sim0["transitions"].to_numpy()
    exposure_transitions = np.where(trans == 1)[0].tolist()
    assert exposure_transitions == [11, 21], f"exposure transitions wrong: {exposure_transitions}"

    drag = sim0["strat_ret"].to_numpy() - sim25["strat_ret"].to_numpy()
    expected_drag = trans * (25 / 10000.0)
    max_err = float(np.max(np.abs(drag - expected_drag)))
    assert max_err <= 1e-9, f"cost-drag identity violated: max_err={max_err}"

    print(
        "(b) synthetic known-value: PASS (production polars pipeline; state transitions at raw idx "
        f"[10,20]; exposure/cost transitions at {exposure_transitions}; cost-drag identity max_err={max_err:.2e} <= 1e-9)"
    )


def selftest_c_delisted_termination(df):
    delisted = df.filter(pl.col("delisted_date").is_not_null())
    if delisted.height == 0:
        print("(c) delisted termination: SKIPPED -- no delisted names in this frame (smoke-synthetic with n_delisted=0?)")
        return
    # need enough bars to actually clear the length=50 + L+20 eligibility guard
    # (>=70 bars minimum), with a little margin so there are real eligible rows
    # to test -- some delisted names have as few as 5 total bars and would give
    # a null terminal value, which is not a meaningful test of freeze-forward.
    counts = delisted.group_by("symbol").agg(pl.len().alias("n")).filter(pl.col("n") >= 90).sort("n")
    if counts.height == 0:
        print("(c) delisted termination: SKIPPED -- no delisted name has >=90 bars in this frame")
        return
    sym = counts["symbol"][0]  # shortest-lived (but still eligible) delisted name -> fastest real test
    one = df.filter(pl.col("symbol") == sym).sort("date")
    global_max = df["date"].max()
    all_dates = df.select("date").unique().sort("date")  # TRUE universe-wide axis, not `one`'s own narrow range

    one = add_bar_idx(one)
    one = add_ma_columns(one, [("SMA", 50)])
    one = add_state_column(one, "ma_SMA50", 0.0, "state")
    one = one.with_columns(pl.lit(50).alias("_length"))
    sim = simulate_per_name(one, "state", cost_bps=25)
    last_date = sim["date"].max()
    term_val = sim.filter(pl.col("date") == last_date)["sleeve_equity"][0]

    port = pad_and_aggregate_sleeve(sim, "sleeve_equity", global_max, all_dates)
    after = port.filter(pl.col("date") > last_date)
    if after.height > 0:
        vals = after["portfolio_equity"].to_numpy()
        assert np.allclose(vals, term_val, atol=1e-12), (
            f"symbol={sym}: padded value drifted after termination (expected frozen {term_val}, got range "
            f"[{vals.min()},{vals.max()}])"
        )
        n_padded = after.height
    else:
        n_padded = 0
    print(
        f"(c) delisted termination: PASS (symbol={sym}, last_date={last_date}, n_bars={sim.height}, "
        f"{n_padded} padded post-termination rows all frozen at terminal value {term_val:.6f})"
    )


def selftest_d_young_name_exclusion(df):
    counts = df.group_by("symbol").agg(pl.len().alias("n")).filter(pl.col("n") >= 300)
    syms = counts.sort("n", descending=True).head(3)["symbol"].to_list()
    lengths_tested = [50, 200]
    checked = 0
    for sym in syms:
        one = df.filter(pl.col("symbol") == sym).sort("date")
        one = add_bar_idx(one)
        for length in lengths_tested:
            if one.height <= length + YOUNG_GUARD_BARS:
                continue
            one2 = add_ma_columns(one, [("SMA", length)])
            one2 = add_state_column(one2, f"ma_SMA{length}", 0.03, "state")
            one2 = one2.with_columns(pl.lit(length).alias("_length"))
            sim = simulate_per_name(one2, "state", cost_bps=25)
            pre = sim.filter(pl.col("bar_idx") < (length + YOUNG_GUARD_BARS - 1))
            n_bad = pre.filter(pl.col("eligible")).height
            n_bad_sleeve = pre.filter(pl.col("sleeve_equity").is_not_null()).height
            assert n_bad == 0, f"symbol={sym} length={length}: {n_bad} rows marked eligible before bar L+20"
            assert n_bad_sleeve == 0, f"symbol={sym} length={length}: sleeve_equity defined before eligibility"
            checked += 1
    assert checked > 0, "selftest (d) found no symbols long enough to check"
    print(
        f"(d) young-name exclusion: PASS ({checked} symbol/length combos, lengths tested={lengths_tested}; "
        "no eligible=True or sleeve_equity row before bar_idx < length+19)"
    )


def selftest_e_nan_choke():
    rng = np.random.default_rng(0)
    x = rng.normal(0.01, 0.05, 40)
    y = rng.normal(0.0, 0.06, 40)
    _assert_finite(x, "selftest_e.x")
    _assert_finite(y, "selftest_e.y")
    t, se = circular_block_bootstrap_mean_t(x, block=3, n_boot=200, seed=0)
    _assert_finite([t, se], "selftest_e.bootstrap_t_se")
    z = mannwhitney_z(x, y)
    _assert_finite([z], "selftest_e.mannwhitney_z")
    nwt = newey_west_t(x, lag=3)
    _assert_finite([nwt], "selftest_e.newey_west_t")
    print(
        "(e) NaN choke: PASS (synthetic 40-obs series; block-bootstrap t/se, Mann-Whitney z, "
        "and Newey-West t all asserted np.isfinite before use)"
    )


def run_selftests(df):
    selftest_a_no_lookahead(df)
    selftest_b_synthetic_known_value()
    selftest_c_delisted_termination(df)
    selftest_d_young_name_exclusion(df)
    selftest_e_nan_choke()


# ============================================================================
# Grid cell evaluation (per-name modes)
# ============================================================================
def eval_per_name_cell(df, kernel, length, band, cost_bps, rebalance, global_max_date, all_dates):
    ma_col = f"ma_{kernel}{length}"
    df = add_state_column(df, ma_col, band, "state_raw")
    df = df.with_columns(pl.lit(length).alias("_length"))
    if rebalance == "monthly_eval":
        df = resample_state_monthly(df, "state_raw", "state_use")
    else:
        df = df.with_columns(pl.col("state_raw").alias("state_use"))

    sim = simulate_per_name(df, "state_use", cost_bps)
    strat_port = pad_and_aggregate_sleeve(sim, "sleeve_equity", global_max_date, all_dates)
    bh_port = pad_and_aggregate_sleeve(sim, "bh_sleeve_equity", global_max_date, all_dates)

    dates_np = strat_port["date"].to_numpy().astype("datetime64[D]")
    equity = strat_port["portfolio_equity"].to_numpy()
    bh_equity = bh_port["portfolio_equity"].to_numpy()
    n_names_avg = float(strat_port["n_names"].mean())

    ret = np.empty(len(equity))
    ret[0] = equity[0] - 1.0
    ret[1:] = equity[1:] / equity[:-1] - 1.0
    m = cell_metrics(dates_np, equity, ret, bh_equity)

    exposure_frac = sim.filter(pl.col("eligible"))["exposure"].mean()
    n = len(equity)
    total_transitions = float(sim.filter(pl.col("eligible"))["transitions"].sum())
    n_names_for_trades = sim.select(pl.col("symbol").n_unique()).item()
    trades_per_yr = (total_transitions / max(n_names_for_trades, 1) / 2.0) / (n / TRADING_DAYS_YR)

    band_tag = str(int(round(band * 100)))
    rule_id = f"{kernel}{length}_b{band_tag}_c{cost_bps}_pername_{rebalance}"
    row = dict(
        rule_id=rule_id, kernel=kernel, length=length, band_pct=round(band * 100.0, 4),
        cost_bps=cost_bps, mode="per_name", rebalance=rebalance,
        time_in_mkt=float(exposure_frac) if exposure_frac is not None else float("nan"),
        trades_per_yr=trades_per_yr, n_names_avg=n_names_avg, **m,
    )
    return row, dates_np, equity, bh_equity


def eval_portfolio_cell(df, kernel, length, band, cost_bps, global_max_date):
    ma_col = f"ma_{kernel}{length}"
    df = add_state_column(df, ma_col, band, "state_raw")
    df = df.with_columns(pl.lit(length).alias("_length"))
    eligible = pl.col("bar_idx") >= (pl.col("_length") + YOUNG_GUARD_BARS - 1)
    df = df.with_columns(eligible.alias("eligible"))
    df = df.with_columns((pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).fill_null(0.0).alias("ret"))

    port_ret_df, bh_ret_df, n_instate_df = build_portfolio_mode(df, "state_raw", cost_bps)
    dates_np = port_ret_df["date"].to_numpy().astype("datetime64[D]")
    ret = port_ret_df["portfolio_ret"].to_numpy()
    equity = np.cumprod(1.0 + ret)
    bh_ret = bh_ret_df["portfolio_ret"].to_numpy()
    bh_equity = np.cumprod(1.0 + bh_ret)
    # align lengths defensively (should already match: both built off the same date grid of eligible rows)
    n = min(len(equity), len(bh_equity))
    dates_np, equity, bh_equity, ret = dates_np[:n], equity[:n], bh_equity[:n], ret[:n]

    m = cell_metrics(dates_np, equity, ret, bh_equity)
    n_instate_avg = float(n_instate_df["n_instate"].mean())
    time_in_mkt = float((ret != 0).mean())  # crude but honest: fraction of days with any deployed capital

    band_tag = str(int(round(band * 100)))
    rule_id = f"{kernel}{length}_b{band_tag}_c{cost_bps}_portfolio_monthly_eval"
    row = dict(
        rule_id=rule_id, kernel=kernel, length=length, band_pct=round(band * 100.0, 4),
        cost_bps=cost_bps, mode="portfolio", rebalance="monthly_eval",
        time_in_mkt=time_in_mkt, trades_per_yr=float("nan"), n_names_avg=n_instate_avg, **m,
    )
    return row, dates_np, equity, bh_equity


# ============================================================================
# Delisted cohort (P2c) and spread panel (P2d)
# ============================================================================
def build_delisted_cohort(df, global_max_date):
    """Canonical rule (SMA200, band0, 25bps, per-name, daily) filtered
    lifetime return vs B&H lifetime return, per name, both cohorts."""
    df = add_bar_idx(df)
    df = add_ma_columns(df, [("SMA", CANONICAL["length"])])
    df = add_state_column(df, f"ma_SMA{CANONICAL['length']}", CANONICAL["band"], "state_raw")
    df = df.with_columns(pl.lit(CANONICAL["length"]).alias("_length"))
    sim = simulate_per_name(df, "state_raw", CANONICAL["cost_bps"])

    meta = (
        sim.filter(pl.col("eligible"))
        .group_by("symbol", maintain_order=True)
        .agg(
            pl.col("date").first().alias("first_eligible_date"),
            pl.col("date").last().alias("last_date"),
            pl.len().alias("n_bars"),
            pl.col("sleeve_equity").last().alias("filtered_terminal"),
            pl.col("bh_sleeve_equity").last().alias("bh_terminal"),
        )
    )
    delisted_dates = df.group_by("symbol", maintain_order=True).agg(pl.col("delisted_date").first())
    meta = meta.join(delisted_dates, on="symbol", how="left")

    # death proxy: raw price return over the final 126 bars of each name's own history
    last_bars = (
        df.group_by("symbol", maintain_order=True)
        .agg(pl.col("close").tail(127))
        .with_columns(
            pl.when(pl.col("close").list.len() >= 2)
            .then(pl.col("close").list.last() / pl.col("close").list.first() - 1.0)
            .otherwise(None)
            .alias("final_126d_return")
        )
        .select(["symbol", "final_126d_return"])
    )
    meta = meta.join(last_bars, on="symbol", how="left")

    meta = meta.with_columns(
        pl.when(pl.col("delisted_date").is_not_null()).then(pl.lit("delisted")).otherwise(pl.lit("survivor")).alias("cohort"),
        (pl.col("filtered_terminal") - 1.0).alias("filtered_lifetime_return"),
        (pl.col("bh_terminal") - 1.0).alias("bh_lifetime_return"),
    )
    meta = meta.with_columns(
        (pl.col("filtered_lifetime_return") - pl.col("bh_lifetime_return")).alias("improvement"),
        pl.when(pl.col("cohort") == "survivor").then(None)
        .when(pl.col("final_126d_return") <= -0.40).then(pl.lit("death_like"))
        .otherwise(pl.lit("mna_like")).alias("death_proxy"),
        (pl.col("filtered_lifetime_return") <= -0.80).alias("filtered_le_neg80"),
        (pl.col("filtered_lifetime_return") <= -0.50).alias("filtered_le_neg50"),
        (pl.col("bh_lifetime_return") <= -0.80).alias("bh_le_neg80"),
        (pl.col("bh_lifetime_return") <= -0.50).alias("bh_le_neg50"),
    )
    return meta.select(DELISTED_COHORT_COLUMNS)


def build_spread_panel(df):
    df = add_bar_idx(df)
    combos = [("SMA", 50), ("SMA", 200), ("EMA", 50), ("EMA", 200)]
    df = add_ma_columns(df, combos)
    df = add_monthly_eval_flag(df)

    panels = []
    for kernel, length in combos:
        ma_col = f"ma_{kernel}{length}"
        eligible = pl.col("bar_idx") >= (length + YOUNG_GUARD_BARS - 1)
        me = (
            df.filter(pl.col("is_month_end") & eligible & pl.col(ma_col).is_not_null())
            .select(["symbol", "date", "close", ma_col])
            .sort(["symbol", "date"])
        )
        me = me.with_columns(
            (pl.col("close").shift(-1).over("symbol") / pl.col("close") - 1.0).alias("fwd_1m_ret"),
            (pl.col("close") > pl.col(ma_col)).alias("above"),
        )
        monthly = me.group_by("date").agg(
            pl.col("fwd_1m_ret").filter(pl.col("above")).mean().alias("above_ret"),
            pl.col("fwd_1m_ret").filter(~pl.col("above")).mean().alias("below_ret"),
            pl.col("above").sum().alias("n_above"),
            (~pl.col("above")).sum().alias("n_below"),
        ).sort("date")
        monthly = monthly.with_columns(
            (pl.col("above_ret") - pl.col("below_ret")).alias("spread"),
            pl.lit(kernel).alias("kernel"), pl.lit(length).alias("length"),
        )
        panels.append(monthly.select(SPREAD_PANEL_COLUMNS))
    return pl.concat(panels, how="vertical")


def spread_panel_stats(panel_df):
    """Returns dict per (kernel,length): {t, se, mean, era_signs: {era: sign}, n_pos_eras}."""
    out = {}
    for (kernel, length), sub in panel_df.group_by(["kernel", "length"]):
        sub = sub.sort("date").filter(pl.col("spread").is_not_null())
        spread = sub["spread"].to_numpy()
        dates_np = sub["date"].to_numpy().astype("datetime64[D]")
        if len(spread) < 8:
            out[(kernel, length)] = dict(t=float("nan"), se=float("nan"), mean=float("nan"), n_months=len(spread), era_signs={}, n_pos_eras=0)
            continue
        t, se = circular_block_bootstrap_mean_t(spread, block=3, n_boot=1000, seed=0)
        era_signs = {}
        for name, start, end in ERAS:
            s, e = np.datetime64(start), np.datetime64(end)
            mask = (dates_np >= s) & (dates_np <= e)
            if mask.sum() > 0:
                era_signs[name] = "+" if float(spread[mask].mean()) > 0 else "-"
        n_pos = sum(1 for v in era_signs.values() if v == "+")
        out[(kernel, length)] = dict(t=t, se=se, mean=float(spread.mean()), n_months=len(spread),
                                      era_signs=era_signs, n_pos_eras=n_pos)
    return out


# ============================================================================
# Summary printing (ASCII-ONLY) -- shared shape between --smoke and --full
# ============================================================================
def fmt_pct(x, digits=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x * 100:+.{digits}f}%"


def print_summary(tag, rows, canon_row, cohort_df, panel_stats, n_symbols, n_delisted):
    print("\n" + "=" * 78)
    print(f"SECTION 1: CANONICAL ({CANONICAL['kernel']}{CANONICAL['length']}, per-name, daily, "
          f"band=0, cost=25bps) vs B&H  [{tag}]")
    print("=" * 78)
    print(f"universe: {n_symbols} symbols ({n_delisted} delisted)")
    if canon_row is not None:
        print(f"{'':24s}{'Strategy':>14s}{'Buy&Hold':>14s}")
        print(f"{'CAGR':24s}{fmt_pct(canon_row['cagr']):>14s}{fmt_pct(canon_row['cagr_bh']):>14s}")
        print(f"{'MaxDD':24s}{fmt_pct(canon_row['maxdd']):>14s}{fmt_pct(canon_row['maxdd_bh']):>14s}")
        print(f"{'Sharpe':24s}{canon_row['sharpe']:>14.3f}{canon_row['sharpe_bh']:>14.3f}")
        print(f"{'Time-in-market':24s}{fmt_pct(canon_row['time_in_mkt']):>14s}{'(n/a)':>14s}")
        print(f"{'Trades/yr/name':24s}{canon_row['trades_per_yr']:>14.2f}{'n/a':>14s}")
        print(f"{'Worst calendar year':24s}{fmt_pct(canon_row['worst_year']):>14s}")
        print(f"NW t-stat (monthly excess vs B&H, lag3): {canon_row['nw_t_excess']:.3f}")
        print(f"avg names/day in sleeve average: {canon_row['n_names_avg']:.1f}")
    else:
        print("  (canonical cell not found in this run's grid)")

    print("\n" + "=" * 78)
    print(f"SECTION 2: GRID ({len(rows)} cells)")
    print("=" * 78)
    for r in sorted(rows, key=lambda r: (r["mode"], r["rebalance"], r["kernel"], r["length"], r["band_pct"], r["cost_bps"])):
        print(
            f"  {r['rule_id']:40s} cagr={fmt_pct(r['cagr'],1):>8s} bh={fmt_pct(r['cagr_bh'],1):>8s} "
            f"maxdd={fmt_pct(r['maxdd'],1):>8s} sharpe={r['sharpe']:>6.2f} nw_t={r['nw_t_excess']:>6.2f} "
            f"n_avg={r['n_names_avg']:>5.1f}"
        )

    print("\n" + "=" * 78)
    print("SECTION 3: DELISTED COHORT (P2c)")
    print("=" * 78)
    if cohort_df is not None and cohort_df.height > 0:
        for cohort in ["delisted", "survivor"]:
            sub = cohort_df.filter(pl.col("cohort") == cohort)
            if sub.height == 0:
                print(f"  {cohort}: 0 names")
                continue
            filt = sub["filtered_lifetime_return"].to_numpy()
            bh = sub["bh_lifetime_return"].to_numpy()
            med_filt = float(np.nanmedian(filt))
            med_bh = float(np.nanmedian(bh))
            med_improve = med_filt - med_bh
            frac_f80 = float(sub["filtered_le_neg80"].mean())
            frac_b80 = float(sub["bh_le_neg80"].mean())
            frac_f50 = float(sub["filtered_le_neg50"].mean())
            frac_b50 = float(sub["bh_le_neg50"].mean())
            z = mannwhitney_z(filt, bh)
            print(f"  {cohort} (n={sub.height}): med filtered={fmt_pct(med_filt)} med bh={fmt_pct(med_bh)} "
                  f"med improvement={med_improve*100:+.2f}pp  MannWhitney z={z:.2f}")
            print(f"    frac<=-80%: filtered={frac_f80*100:.1f}% bh={frac_b80*100:.1f}% "
                  f"(fall={( frac_b80-frac_f80)*100:+.1f}pp)   "
                  f"frac<=-50%: filtered={frac_f50*100:.1f}% bh={frac_b50*100:.1f}% "
                  f"(fall={(frac_b50-frac_f50)*100:+.1f}pp)")
        if cohort_df.filter(pl.col("cohort") == "delisted").height > 0:
            dead = cohort_df.filter((pl.col("cohort") == "delisted") & (pl.col("death_proxy") == "death_like"))
            mna = cohort_df.filter((pl.col("cohort") == "delisted") & (pl.col("death_proxy") == "mna_like"))
            print(f"  death-like: {dead.height}  mna-like: {mna.height}")
    else:
        print("  (no delisted names in this frame)")

    print("\n" + "=" * 78)
    print("SECTION 4: SPREAD PANEL (P2d) -- above vs below own MA, forward-1m equal-weight")
    print("=" * 78)
    for (kernel, length), stats in sorted(panel_stats.items()):
        signs = ",".join(f"{k}:{v}" for k, v in stats["era_signs"].items())
        print(
            f"  {kernel}{length}: mean_spread={fmt_pct(stats['mean'])} block-boot t={stats['t']:.2f} "
            f"n_months={stats['n_months']} pos_eras={stats['n_pos_eras']}/{len(stats['era_signs'])}  [{signs}]"
        )

    print("\n" + "=" * 78)
    print(f"SECTION 5: PREREG GATE EVALUATION (Leg 2) [{tag}]")
    print("=" * 78)
    if canon_row is not None:
        dd_mag = abs(canon_row["maxdd"]) * 100.0
        bh_dd_mag = abs(canon_row["maxdd_bh"]) * 100.0
        dd_pass = dd_mag <= (bh_dd_mag - 10.0)
        print(f"P2a-DD (predict TRUE): canonical MaxDD<=B&H MaxDD-10pp: {dd_mag:.2f}% vs "
              f"req<={bh_dd_mag-10:.2f}% -> {'TRUE' if dd_pass else 'FALSE'}")
        cagr_diff_pp = (canon_row["cagr"] - canon_row["cagr_bh"]) * 100.0
        b_beats = cagr_diff_pp > 0.5 and canon_row["nw_t_excess"] >= 2.0
        b_matches = abs(cagr_diff_pp) <= 0.5
        verdict = "BEATS" if b_beats else ("MATCHES" if b_matches else "COSTS")
        print(f"P2b-CAGR (open question): diff={cagr_diff_pp:+.2f}pp nw_t={canon_row['nw_t_excess']:.2f} -> {verdict}")
    else:
        print("  P2a/P2b: canonical cell not present in this run")
    print("  P2c: see SECTION 3 (delisted vs survivor median-improvement comparison is the mechanism test)")
    print("  P2d: see SECTION 4 (need block-boot t>=2 AND sign positive in >=5/7 eras)")


# ============================================================================
# Orchestration: smoke / full
# ============================================================================
def run_pipeline(df, tag, out_suffix):
    n_symbols = df.select(pl.col("symbol").n_unique()).item()
    n_delisted = df.filter(pl.col("delisted_date").is_not_null()).select(pl.col("symbol").n_unique()).item()
    global_max_date = df["date"].max()
    all_dates = df.select("date").unique().sort("date")  # TRUE universe-wide axis for freeze-forward padding

    base = add_bar_idx(df)
    base = add_ma_columns(base, [(k, l) for k in KERNELS for l in LENGTHS])
    base = add_monthly_eval_flag(base)

    rows = []
    canon_row = None
    for kernel in KERNELS:
        for length in LENGTHS:
            for band in BANDS:
                for cost in COST_BPS_GRID:
                    for mode, rebalance in MODE_REBALANCE:
                        cell = base.select(
                            ["symbol", "date", "close", "volume", "delisted_date", "bar_idx",
                             "is_month_end", f"ma_{kernel}{length}"]
                        )
                        if mode == "per_name":
                            row, *_ = eval_per_name_cell(cell, kernel, length, band, cost, rebalance, global_max_date, all_dates)
                        else:
                            row, *_ = eval_portfolio_cell(cell, kernel, length, band, cost, global_max_date)
                        rows.append(row)
                        if (kernel == CANONICAL["kernel"] and length == CANONICAL["length"]
                                and band == CANONICAL["band"] and cost == CANONICAL["cost_bps"]
                                and mode == CANONICAL["mode"] and rebalance == CANONICAL["rebalance"]):
                            canon_row = row

    grid_df = pl.DataFrame(rows, infer_schema_length=None).select(GRID_COLUMNS)
    grid_df = grid_df.with_columns([pl.col(c).fill_nan(None) for c in
                                     ["cagr", "cagr_bh", "maxdd", "maxdd_bh", "sharpe", "sharpe_bh",
                                      "time_in_mkt", "trades_per_yr", "worst_year", "nw_t_excess", "n_names_avg"]])
    grid_path = OUT_DIR / f"universe_grid{out_suffix}.parquet"
    grid_df.write_parquet(grid_path)

    cohort_df = build_delisted_cohort(df, global_max_date)
    cohort_path = OUT_DIR / f"delisted_cohort{out_suffix}.parquet"
    cohort_df.write_parquet(cohort_path)

    panel_df = build_spread_panel(df)
    panel_path = OUT_DIR / f"spread_panel{out_suffix}.parquet"
    panel_df.write_parquet(panel_path)
    panel_stats = spread_panel_stats(panel_df)

    print(f"\nwrote {grid_path}  rows={grid_df.height}")
    print(f"wrote {cohort_path}  rows={cohort_df.height}")
    print(f"wrote {panel_path}  rows={panel_df.height}")

    print_summary(tag, rows, canon_row, cohort_df, panel_stats, n_symbols, n_delisted)


def run_smoke():
    df = load_universe()
    if df is not None:
        delisted_syms = (
            df.filter(pl.col("delisted_date").is_not_null())
            .select("symbol").unique().sort("symbol").head(10)["symbol"].to_list()
        )
        survivor_syms = (
            df.filter(pl.col("delisted_date").is_null())
            .select("symbol").unique().sort("symbol").head(20)["symbol"].to_list()
        )
        pick = sorted(delisted_syms + survivor_syms)
        df = df.filter(pl.col("symbol").is_in(pick)).sort(["symbol", "date"])
        tag = f"SMOKE, {len(pick)} REAL symbols ({len(delisted_syms)} delisted + {len(survivor_syms)} survivors)"
    else:
        df = make_synthetic_universe(n_symbols=30, n_delisted=10, seed=0)
        tag = "SMOKE, 30 SYNTHETIC symbols (universe_1997.parquet not built yet)"

    print("=" * 78)
    print("SELF-TESTS")
    print("=" * 78)
    run_selftests(df)

    print(f"\nRunning smoke pipeline: {tag}")
    run_pipeline(df, tag, "_smoke")


def run_full():
    df = load_universe()
    if df is None:
        print("ERROR: .cache/ptj_trend_audit/universe_1997.parquet does not exist. "
              "Run pull_universe.py (queued, db-heavy) first.")
        sys.exit(1)
    print("=" * 78)
    print("SELF-TESTS")
    print("=" * 78)
    run_selftests(df)
    print(f"\nRunning FULL pipeline on {df.select(pl.col('symbol').n_unique()).item()} symbols, {df.height} rows")
    run_pipeline(df, "FULL", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="self-tests only")
    parser.add_argument("--smoke", action="store_true", help="30-symbol end-to-end smoke run + summary")
    parser.add_argument("--full", action="store_true", help="THE FULL RUN -- queue this, do not run interactively")
    args = parser.parse_args()

    if args.full:
        run_full()
        return
    if args.smoke:
        run_smoke()
        return

    # --selftest and the no-flags default are the same thing: self-tests only,
    # using real data if present else synthetic. Deliberately never touches
    # the full grid -- see module docstring.
    df = load_universe()
    if df is None:
        df = make_synthetic_universe(n_symbols=30, n_delisted=10, seed=0)
    print("=" * 78)
    print("SELF-TESTS")
    print("=" * 78)
    run_selftests(df)
    if not args.selftest:
        print(
            "\nNo flags given: ran self-tests only. Use --smoke for a small end-to-end run, "
            "or --full for the real ~1626-symbol run (queue it -- do not run interactively)."
        )


if __name__ == "__main__":
    main()
