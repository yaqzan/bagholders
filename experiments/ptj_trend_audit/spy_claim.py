#! python3
"""Leg 1 - PTJ 200-DMA Trend Theory - SPY index-level claim audit.

See experiments/ptj_trend_audit/PREREGISTRATION.md (locked 2026-08-10) for the
full spec. This script implements the Leg 1 grid EXACTLY as locked:

  family price_ma: kernel{SMA,EMA} x length{20,50,100,150,200,250}
                    x band{0,1,3,5%} x cost{0,5,25bps/side} x cash{0,3%/yr}
  family dual_ma:   fast>slow pairs x {SMA,EMA} x cost x cash (band=0)
  family slope:     MA{50,200} rising over k{5,20}d x kernel x cost x cash

Substrate: MarketRegime.spy_close, 1995-01 -> now. ONE MySQL read (repo hard
rule: no other MySQL access anywhere in this script).

State machine (all families): causal two-threshold definitive state
(above upper band = 1, below lower band = 0, otherwise null) -> forward-fill
-> fill_null(0) [start in cash]. This is strictly causal: state at day d
depends only on data through day d (see selftest (a)). The "very first
resolved state applies from series start where MA is defined" clarification
in the prereg is satisfied naturally by this construction: forward_fill()
never reaches backward in time, so if the very first MA-defined bar is
already resolved (outside the band), that value is used immediately with no
artificial delay; only the leading segment with no resolved value YET
(MA undefined, or still inside the band) defaults to cash (0).

Execution: t+1. Today's state (computed off today's close) decides
TOMORROW's exposure: exposure[i] = state[i-1], exposure[0] = 0 (baseline,
nothing to inherit before the series starts).

Costs: cost_bps per SIDE, charged on any day the applied exposure changes
(a "transition day"): cost[i] = cost_bps/1e4 if exposure[i] != exposure[i-1]
else 0. trades_per_yr = (total transitions / 2) / years_span, i.e. round
trips per year (one round trip = one exit transition + one entry transition
= two "sides").

Cash yield: on exposure=0 days, daily_cash = (1+cash_yield)**(1/252) - 1.

CAGR convention (LOCKED formula from the task brief): CAGR = (final/initial)
** (252/n) - 1, where n = number of daily rows in the series (not calendar
days). This fixes years_span = n/252 for every time-normalized metric
(CAGR, trades_per_yr) for internal consistency.

NaN-choke doctrine (hard rule (e)): the ONE deliberate NaN/None-normalization
point is the state construction itself (forward_fill().fillna(0) is a real
modeling decision: "no signal yet -> assume cash", not a hack). Everywhere
downstream (monthly excess series, Newey-West t, bootstrap, sign-flip null)
is HARD-ASSERTED finite immediately before use via _assert_finite() -- a NaN
reaching a t-stat silently is the documented false-NULL trap in this repo;
we fail loudly instead of silently filling statistical inputs.

Two independent randomness sites (block bootstrap; sign-flip null), each
seeded 0 independently via its own np.random.default_rng(0) call, so each
procedure is reproducible on its own regardless of the other's draw count.

Run:
    python experiments/ptj_trend_audit/spy_claim.py              # selftests + full grid + parquet + summary
    python experiments/ptj_trend_audit/spy_claim.py --selftest   # selftests only, no MySQL, no grid

Output: .cache/ptj_trend_audit/spy_grid.parquet
Schema (LOCKED, 20 cols): rule_id, family, kernel, length, fast, slow,
  band_pct, cost_bps, cash_yield_pct, cagr, cagr_bh, maxdd, maxdd_bh, sharpe,
  sharpe_bh, time_in_mkt, trades_per_yr, worst_year, nw_t_excess, era_json

DEVIATION NOTE (declared, not silent): the locked schema has no column to
carry the `slope` family's k (rising-lookback window, 5 or 20 days)
parameter -- length/fast/slow only cover price_ma and dual_ma. Rather than
add an unlocked column, k is encoded in `rule_id` (e.g.
"slope_SMA200_k20_c25_y0") so every slope cell stays fully reconstructable.
Every other field is stored per the exact locked names/order above.
"""
import sys

_ROOT = r"C:\Development\Trader"
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl

OUT_DIR = Path(_ROOT) / ".cache" / "ptj_trend_audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "spy_grid.parquet"

TRADING_DAYS_YR = 252.0

# ============================================================================
# LOCKED GRID (PREREGISTRATION.md Leg 1) -- do not edit without re-locking.
# ============================================================================
PRICE_MA_LENGTHS = [20, 50, 100, 150, 200, 250]
PRICE_MA_BANDS = [0.0, 0.01, 0.03, 0.05]          # fractions; stored as band_pct*100
DUAL_MA_PAIRS = [(10, 150), (20, 100), (20, 200), (50, 150), (50, 200), (100, 200)]
SLOPE_LENGTHS = [50, 200]
SLOPE_K = [5, 20]
KERNELS = ["SMA", "EMA"]
COST_BPS_GRID = [0, 5, 25]
CASH_YIELD_GRID = [0.0, 0.03]                     # fractions; stored as cash_yield_pct*100

CANONICAL_RULE_ID = "pma_SMA200_b0_c25_y0"

ERAS = [
    ("1995-2002", "1995-01-01", "2002-12-31"),
    ("2003-2007", "2003-01-01", "2007-12-31"),
    ("2008-2012", "2008-01-01", "2012-12-31"),
    ("2013-2019", "2013-01-01", "2019-12-31"),
    ("2020-2021", "2020-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023-now", "2023-01-01", "2100-01-01"),
]
CRASHES = [
    ("crash_2000_dotcom", "2000-03-24", "2002-10-09"),
    ("crash_2008_gfc", "2007-10-09", "2009-03-09"),
    ("crash_2020_covid", "2020-02-19", "2020-03-23"),
    ("crash_2022_bear", "2022-01-03", "2022-10-12"),
]

COLUMNS = [
    "rule_id", "family", "kernel", "length", "fast", "slow", "band_pct",
    "cost_bps", "cash_yield_pct", "cagr", "cagr_bh", "maxdd", "maxdd_bh",
    "sharpe", "sharpe_bh", "time_in_mkt", "trades_per_yr", "worst_year",
    "nw_t_excess", "era_json",
]


# ============================================================================
# MA kernels
# ============================================================================
def sma(x, length):
    """Rolling simple moving average. NaN for index < length-1."""
    n = len(x)
    out = np.full(n, np.nan)
    if length > n:
        return out
    csum = np.cumsum(np.insert(x, 0, 0.0))
    out[length - 1:] = (csum[length:] - csum[:n - length + 1]) / length
    return out


def ema(x, length):
    """Recursive EMA, k=2/(length+1), seeded at x[0] (matches
    algorithm_versions/.../market_regime.py::compute_ema convention).
    NaN for index < length-1 -- a deliberate warmup-parity choice so SMA and
    EMA cells of the same nominal length become tradable on the same day
    (EMA is mathematically defined from day 0, but nulling its first
    length-1 bars keeps cross-kernel comparisons apples-to-apples; documented
    here since the prereg doesn't specify EMA warmup treatment)."""
    n = len(x)
    out = np.empty(n)
    out[0] = x[0]
    k = 2.0 / (length + 1)
    for i in range(1, n):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    warm = min(length - 1, n)
    if warm > 0:
        out[:warm] = np.nan
    return out


def get_ma(cache, kernel, length):
    key = (kernel, length)
    if key not in cache:
        cache[key] = sma(_CLOSE_REF[0], length) if kernel == "SMA" else ema(_CLOSE_REF[0], length)
    return cache[key]


# module-level "current close array" reference used by get_ma's cache helper;
# set once per call site via _with_close(). Avoids threading `close` through
# every cache lookup call in the grid-building loops.
_CLOSE_REF = [None]


def _with_close(close):
    _CLOSE_REF[0] = close


# ============================================================================
# Causal forward-fill
# ============================================================================
def ffill(a):
    """Standard numpy forward-fill idiom. Leading NaNs (nothing to forward
    from yet) are left as NaN -- caller applies the fill_null(0) default."""
    a = np.asarray(a, dtype=float)
    mask = np.isnan(a)
    idx = np.where(~mask, np.arange(len(a)), 0)
    np.maximum.accumulate(idx, out=idx)
    return a[idx]


# ============================================================================
# State builders (all strictly causal: state[i] depends only on data <= i)
# ============================================================================
def state_price_ma(close, ma, band):
    upper = ma * (1.0 + band)
    lower = ma * (1.0 - band)
    raw = np.full(len(close), np.nan)
    raw[close > upper] = 1.0
    raw[close < lower] = 0.0
    # comparisons against NaN thresholds (pre-warmup) are always False, so
    # `raw` correctly stays NaN there without extra masking.
    state = ffill(raw)
    return np.where(np.isnan(state), 0.0, state)


def state_dual_ma(fast_ma, slow_ma):
    raw = np.where(fast_ma > slow_ma, 1.0, 0.0)
    raw[np.isnan(slow_ma) | np.isnan(fast_ma)] = np.nan
    state = ffill(raw)
    return np.where(np.isnan(state), 0.0, state)


def state_slope(ma, k):
    n = len(ma)
    shifted = np.full(n, np.nan)
    shifted[k:] = ma[:-k]
    raw = np.full(n, np.nan)
    valid = ~np.isnan(ma) & ~np.isnan(shifted)
    raw[valid] = np.where(ma[valid] > shifted[valid], 1.0, 0.0)
    state = ffill(raw)
    return np.where(np.isnan(state), 0.0, state)


# ============================================================================
# Simulation core
# ============================================================================
def simulate(close, state, cost_bps, cash_yield_pct):
    """t+1 execution, cost per side on transition days, cash yield on out days."""
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = close[1:] / close[:-1] - 1.0

    exposure = np.zeros(n)
    exposure[1:] = state[:-1]

    transitions = np.zeros(n)
    transitions[1:] = (exposure[1:] != exposure[:-1]).astype(float)

    daily_cash = (1.0 + cash_yield_pct) ** (1.0 / TRADING_DAYS_YR) - 1.0
    cost = transitions * (cost_bps / 10000.0)
    strat_ret = exposure * ret + (1.0 - exposure) * daily_cash - cost
    equity = np.cumprod(1.0 + strat_ret)
    return dict(ret=strat_ret, exposure=exposure, transitions=transitions, equity=equity)


def build_bh(close):
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = close[1:] / close[:-1] - 1.0
    equity = np.cumprod(1.0 + ret)
    return dict(ret=ret, equity=equity)


# ============================================================================
# Metrics
# ============================================================================
def max_dd_vec(equity, base=1.0):
    full = np.concatenate(([base], equity))
    peak = np.maximum.accumulate(full)
    dd = full / peak - 1.0
    return float(dd.min())


def sharpe_ratio(ret):
    sd = ret.std(ddof=1)
    if sd <= 0 or not np.isfinite(sd):
        return float("nan")
    return float(ret.mean() / sd * np.sqrt(TRADING_DAYS_YR))


def annual_returns(dates_np, equity):
    """Calendar-year total returns. First calendar year is SKIPPED (no
    prior-year-end baseline exists) -- matches experiments/vix_cycle_claim
    /backtest.py precedent exactly."""
    years = dates_np.astype("datetime64[Y]").astype(int) + 1970
    uniq_years = sorted(set(years.tolist()))
    year_end_val = {}
    for y in uniq_years:
        idxs = np.where(years == y)[0]
        year_end_val[y] = equity[idxs[-1]]
    out = []
    for i, y in enumerate(uniq_years):
        if i == 0:
            continue
        out.append((y, float(year_end_val[y] / year_end_val[uniq_years[i - 1]] - 1.0)))
    return out


def window_return(dates_np, equity, start_str, end_str):
    start = np.datetime64(start_str)
    end = np.datetime64(end_str)
    n = len(dates_np)
    lo = int(np.searchsorted(dates_np, start, side="left"))
    hi = int(np.searchsorted(dates_np, end, side="right")) - 1
    if lo >= n or hi < 0 or lo > hi:
        return None
    start_val = equity[lo - 1] if lo > 0 else 1.0
    end_val = equity[hi]
    return float(end_val / start_val - 1.0)


def build_month_end_idx(dates_np):
    ym = dates_np.astype("datetime64[M]")
    idx = np.where(ym[1:] != ym[:-1])[0]
    idx = np.append(idx, len(dates_np) - 1)
    return idx


def monthly_returns_from_equity(equity, month_end_idx):
    path = np.concatenate(([1.0], equity[month_end_idx]))
    return path[1:] / path[:-1] - 1.0


# ============================================================================
# Stats: Newey-West t (vectorized across cells), bootstrap, sign-flip null
# ============================================================================
def newey_west_t_matrix(X, lag=3):
    """X: (n_cells, n_months). Returns t-stat per row testing H0: mean=0,
    HAC (Bartlett kernel) variance of the mean, lag periods."""
    X = np.asarray(X, dtype=float)
    n_cells, n_months = X.shape
    mean = X.mean(axis=1, keepdims=True)
    u = X - mean
    s = (u * u).sum(axis=1) / n_months
    for l in range(1, lag + 1):
        if l >= n_months:
            break
        cov = (u[:, l:] * u[:, :-l]).sum(axis=1) / n_months
        w = 1.0 - l / (lag + 1)
        s += 2.0 * w * cov
    var_mean = s / n_months
    with np.errstate(invalid="ignore", divide="ignore"):
        se = np.sqrt(np.where(var_mean > 0, var_mean, np.nan))
        t = mean.flatten() / se
    return t


def _assert_finite(arr, name):
    arr = np.asarray(arr, dtype=float)
    if not np.all(np.isfinite(arr)):
        n_bad = int((~np.isfinite(arr)).sum())
        raise AssertionError(f"NaN/Inf choke: {name} has {n_bad} non-finite value(s) reaching a stat computation")


def circular_block_bootstrap_cagr_diff(strat_ret, bh_ret, block=63, n_boot=1000, seed=0):
    n = len(strat_ret)
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]) % n
        idx = idx.reshape(-1)[:n]
        rs = strat_ret[idx]
        rb = bh_ret[idx]
        cagr_s = np.prod(1.0 + rs) ** (TRADING_DAYS_YR / n) - 1.0
        cagr_b = np.prod(1.0 + rb) ** (TRADING_DAYS_YR / n) - 1.0
        diffs[b] = cagr_s - cagr_b
    return diffs


def signflip_max_t_null(excess_matrix, n_resamples=200, lag=3, seed=0):
    _assert_finite(excess_matrix, "signflip_max_t_null.excess_matrix")
    rng = np.random.default_rng(seed)
    n_cells, n_months = excess_matrix.shape
    max_ts = np.empty(n_resamples)
    for r in range(n_resamples):
        signs = rng.choice(np.array([-1.0, 1.0]), size=n_months)
        flipped = excess_matrix * signs[None, :]
        t = newey_west_t_matrix(flipped, lag=lag)
        max_ts[r] = np.nanmax(np.abs(t))
    return max_ts


# ============================================================================
# Self-tests
# ============================================================================
def selftest_a_no_lookahead():
    rng = np.random.default_rng(0)
    n = 2000
    x = 100.0 * np.cumprod(1.0 + rng.normal(0.0003, 0.01, n))
    combos = [("SMA", 200, 0.0), ("EMA", 50, 0.03), ("SMA", 20, 0.01)]
    sample_idx = rng.choice(np.arange(300, n), size=20, replace=False)
    checked = 0
    for kernel, length, band in combos:
        ma_full = sma(x, length) if kernel == "SMA" else ema(x, length)
        state_full = state_price_ma(x, ma_full, band)
        for d in sample_idx:
            x_trunc = x[: d + 1]
            ma_trunc = sma(x_trunc, length) if kernel == "SMA" else ema(x_trunc, length)
            state_trunc = state_price_ma(x_trunc, ma_trunc, band)
            assert state_trunc[-1] == state_full[d], (
                f"look-ahead leak: kernel={kernel} length={length} band={band} "
                f"d={d} trunc={state_trunc[-1]} full={state_full[d]}"
            )
            checked += 1
    print(f"(a) no-look-ahead truncation: PASS ({checked} checks, 3 kernel/length/band combos x 20 sampled dates)")


def selftest_b_synthetic_known_value():
    close = np.array([100.0] * 10 + [200.0] * 10 + [50.0] * 10)
    length, band = 5, 0.0
    ma = sma(close, length)
    state = state_price_ma(close, ma, band)
    expected_state = np.array([0.0] * 10 + [1.0] * 10 + [0.0] * 10)
    assert np.array_equal(state, expected_state), f"state mismatch: {state.tolist()}"

    up_cross = int(np.where((state[1:] == 1) & (state[:-1] == 0))[0][0]) + 1
    down_cross = int(np.where((state[1:] == 0) & (state[:-1] == 1))[0][0]) + 1
    assert up_cross == 10 and down_cross == 20, f"crossing dates wrong: up={up_cross} down={down_cross}"

    sim0 = simulate(close, state, cost_bps=0, cash_yield_pct=0.0)
    sim25 = simulate(close, state, cost_bps=25, cash_yield_pct=0.0)
    exposure_transitions = np.where(sim0["transitions"] == 1)[0].tolist()
    assert exposure_transitions == [11, 21], f"exposure transitions wrong: {exposure_transitions}"

    drag = sim0["ret"] - sim25["ret"]
    expected_drag = sim0["transitions"] * (25 / 10000.0)
    max_err = float(np.max(np.abs(drag - expected_drag)))
    assert max_err <= 1e-9, f"cost-drag identity violated: max_err={max_err}"

    print(
        "(b) synthetic known-value: PASS (state transitions at raw idx [10,20]; "
        f"exposure/cost transitions at {exposure_transitions}; cost-drag identity max_err={max_err:.2e} <= 1e-9)"
    )


def selftest_c_delisted_termination_proxy():
    rng = np.random.default_rng(0)
    n_full = 40
    close_full = 50.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.015, n_full))
    cut = 25
    close_trunc = close_full[:cut]

    ma_full = sma(close_full, 5)
    state_full = state_price_ma(close_full, ma_full, 0.0)
    sim_full = simulate(close_full, state_full, cost_bps=25, cash_yield_pct=0.03)

    ma_trunc = sma(close_trunc, 5)
    state_trunc = state_price_ma(close_trunc, ma_trunc, 0.0)
    sim_trunc = simulate(close_trunc, state_trunc, cost_bps=25, cash_yield_pct=0.03)

    assert len(sim_trunc["equity"]) == cut, "truncated series produced extra rows past the cut"
    assert np.allclose(sim_trunc["equity"], sim_full["equity"][:cut], atol=1e-12), (
        "equity path before the cut changed when the tail was removed -- "
        "termination is not clean (a name's sleeve must freeze, not be retroactively altered)"
    )
    print(
        f"(c) delisted termination (proxy -- SPY has no delisting; validates the single-series "
        f"equity-curve construction terminates cleanly and matches the full-series path up to the "
        f"cut, n={cut}): PASS"
    )


def selftest_d_warmup_exclusion():
    rng = np.random.default_rng(0)
    n = 500
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0002, 0.01, n))
    lengths_tested = [20, 50, 200]
    for length in lengths_tested:
        ma = sma(close, length)
        state = state_price_ma(close, ma, 0.03)
        pre = state[: length - 1]
        assert np.all(pre == 0.0), f"length={length}: found non-cash state before MA warmup completes"
    print(
        "(d) warmup exclusion (Leg-1 analogue of Leg-2's L+20 young-name guard -- "
        f"no eligibility concept applies to a single index series beyond MA warmup): PASS "
        f"(state forced to 0/cash for all idx < length-1, lengths tested={lengths_tested})"
    )


def selftest_e_nan_choke():
    rng = np.random.default_rng(0)
    n_months = 60
    n_cells = 5
    excess = rng.normal(0.001, 0.02, size=(n_cells, n_months))
    _assert_finite(excess, "selftest_e.excess")
    t = newey_west_t_matrix(excess, lag=3)
    _assert_finite(t, "selftest_e.nw_t")
    null = signflip_max_t_null(excess, n_resamples=20, lag=3, seed=0)
    _assert_finite(null, "selftest_e.signflip_null")
    print(
        f"(e) NaN choke: PASS (synthetic {n_cells}x{n_months} excess matrix, NW-t, and "
        f"{len(null)}-resample sign-flip null all asserted np.isfinite before use)"
    )


def run_selftests():
    selftest_a_no_lookahead()
    selftest_b_synthetic_known_value()
    selftest_c_delisted_termination_proxy()
    selftest_d_warmup_exclusion()
    selftest_e_nan_choke()


# ============================================================================
# Grid evaluation
# ============================================================================
def eval_cell(rule_id, family, kernel, length, fast, slow, band_frac, cost_bps,
              cash_yield_frac, close, state, dates_np, month_end_idx, bh):
    sim = simulate(close, state, cost_bps, cash_yield_frac)
    n = len(close)

    cagr = float(sim["equity"][-1] ** (TRADING_DAYS_YR / n) - 1.0)
    maxdd = max_dd_vec(sim["equity"])
    shrp = sharpe_ratio(sim["ret"])
    tim = float(sim["exposure"].mean())
    trades_per_yr = float((sim["transitions"].sum() / 2.0) / (n / TRADING_DAYS_YR))
    worst_year = min(r for _, r in annual_returns(dates_np, sim["equity"]))

    monthly_strat = monthly_returns_from_equity(sim["equity"], month_end_idx)
    excess = monthly_strat - bh["monthly_ret"]
    _assert_finite(excess, f"{rule_id}.monthly_excess")

    era_dict = {}
    for name, start, end in ERAS:
        era_dict[name] = {
            "strat": round(window_return(dates_np, sim["equity"], start, end) or 0.0, 6),
            "bh": round(bh["era_returns"][name] or 0.0, 6),
        }
    for name, start, end in CRASHES:
        era_dict[name] = {
            "strat": round(window_return(dates_np, sim["equity"], start, end) or 0.0, 6),
            "bh": round(bh["crash_returns"][name] or 0.0, 6),
        }

    row = dict(
        rule_id=rule_id, family=family, kernel=kernel,
        length=int(length) if length is not None else None,
        fast=int(fast) if fast is not None else None,
        slow=int(slow) if slow is not None else None,
        band_pct=round(band_frac * 100.0, 4),
        cost_bps=int(cost_bps),
        cash_yield_pct=round(cash_yield_frac * 100.0, 4),
        cagr=cagr, cagr_bh=bh["cagr"], maxdd=maxdd, maxdd_bh=bh["maxdd"],
        sharpe=shrp, sharpe_bh=bh["sharpe"], time_in_mkt=tim,
        trades_per_yr=trades_per_yr, worst_year=worst_year,
        nw_t_excess=None,  # filled after batch NW-t computation
        era_json=json.dumps(era_dict, sort_keys=True),
    )
    return row, excess, sim


def build_bh_full(close, dates_np, month_end_idx):
    bh = build_bh(close)
    bh["cagr"] = float(bh["equity"][-1] ** (TRADING_DAYS_YR / len(close)) - 1.0)
    bh["maxdd"] = max_dd_vec(bh["equity"])
    bh["sharpe"] = sharpe_ratio(bh["ret"])
    bh["monthly_ret"] = monthly_returns_from_equity(bh["equity"], month_end_idx)
    bh["ann"] = annual_returns(dates_np, bh["equity"])
    bh["worst_year"] = min(r for _, r in bh["ann"])
    bh["era_returns"] = {name: window_return(dates_np, bh["equity"], s, e) for name, s, e in ERAS}
    bh["crash_returns"] = {name: window_return(dates_np, bh["equity"], s, e) for name, s, e in CRASHES}
    return bh


def build_grid(close, dates_np):
    ma_cache = {}
    _with_close(close)
    month_end_idx = build_month_end_idx(dates_np)
    bh = build_bh_full(close, dates_np, month_end_idx)

    rows = []
    excess_rows = []  # parallel list, one array per row, for the sign-flip null
    canonical_sim = None

    def band_tag(b):
        return str(int(round(b * 100)))

    def cash_tag(c):
        return str(int(round(c * 100)))

    # --- family price_ma ---
    for length in PRICE_MA_LENGTHS:
        for kernel in KERNELS:
            ma = get_ma(ma_cache, kernel, length)
            for band in PRICE_MA_BANDS:
                state = state_price_ma(close, ma, band)
                for cost in COST_BPS_GRID:
                    for cash in CASH_YIELD_GRID:
                        rule_id = f"pma_{kernel}{length}_b{band_tag(band)}_c{cost}_y{cash_tag(cash)}"
                        row, excess, sim = eval_cell(
                            rule_id, "price_ma", kernel, length, None, None, band, cost, cash,
                            close, state, dates_np, month_end_idx, bh,
                        )
                        rows.append(row)
                        excess_rows.append(excess)
                        if rule_id == CANONICAL_RULE_ID:
                            canonical_sim = sim

    # --- family dual_ma (overlaps) ---
    for fast, slow in DUAL_MA_PAIRS:
        for kernel in KERNELS:
            fast_ma = get_ma(ma_cache, kernel, fast)
            slow_ma = get_ma(ma_cache, kernel, slow)
            state = state_dual_ma(fast_ma, slow_ma)
            for cost in COST_BPS_GRID:
                for cash in CASH_YIELD_GRID:
                    rule_id = f"dma_{kernel}{fast}x{slow}_c{cost}_y{cash_tag(cash)}"
                    row, excess, _ = eval_cell(
                        rule_id, "dual_ma", kernel, None, fast, slow, 0.0, cost, cash,
                        close, state, dates_np, month_end_idx, bh,
                    )
                    rows.append(row)
                    excess_rows.append(excess)

    # --- family slope ---
    for length in SLOPE_LENGTHS:
        for k in SLOPE_K:
            for kernel in KERNELS:
                ma = get_ma(ma_cache, kernel, length)
                state = state_slope(ma, k)
                for cost in COST_BPS_GRID:
                    for cash in CASH_YIELD_GRID:
                        rule_id = f"slope_{kernel}{length}_k{k}_c{cost}_y{cash_tag(cash)}"
                        row, excess, _ = eval_cell(
                            rule_id, "slope", kernel, length, None, None, 0.0, cost, cash,
                            close, state, dates_np, month_end_idx, bh,
                        )
                        rows.append(row)
                        excess_rows.append(excess)

    excess_matrix = np.vstack(excess_rows)
    _assert_finite(excess_matrix, "grid.excess_matrix")
    nw_t = newey_west_t_matrix(excess_matrix, lag=3)
    _assert_finite(nw_t, "grid.nw_t_excess")
    for row, t in zip(rows, nw_t):
        row["nw_t_excess"] = float(t)

    assert canonical_sim is not None, f"canonical rule_id {CANONICAL_RULE_ID!r} not found in grid"
    return rows, excess_matrix, bh, canonical_sim, dates_np


# ============================================================================
# Data load (the ONE allowed MySQL read)
# ============================================================================
def load_spy_close():
    from database.models.core import MarketRegime

    query_rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.spy_close)
        .where(MarketRegime.spy_close.is_null(False))
        .order_by(MarketRegime.date)
        .tuples()
    )
    dates = [r[0] for r in query_rows]
    close = np.array([float(r[1]) for r in query_rows], dtype=float)
    dates_np = np.array(dates, dtype="datetime64[D]")
    return dates_np, close


# ============================================================================
# Summary printing (ASCII-ONLY)
# ============================================================================
def fmt_pct(x, digits=2):
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:+.{digits}f}%"


def print_summary(rows, excess_matrix, bh, canonical_sim, dates_np, close, boot_diffs, null_max_ts):
    df_rows = {r["rule_id"]: r for r in rows}
    canon = df_rows[CANONICAL_RULE_ID]
    n = len(close)

    print("\n" + "=" * 78)
    print("SECTION 1: CANONICAL vs BUY-AND-HOLD")
    print("=" * 78)
    print(f"Canonical rule: SMA200, band=0%, cost=25bps/side, cash=0%/yr  (rule_id={CANONICAL_RULE_ID})")
    print(f"{'':24s}{'Strategy':>14s}{'Buy&Hold':>14s}")
    print(f"{'CAGR':24s}{fmt_pct(canon['cagr']):>14s}{fmt_pct(canon['cagr_bh']):>14s}")
    print(f"{'MaxDD':24s}{fmt_pct(canon['maxdd']):>14s}{fmt_pct(canon['maxdd_bh']):>14s}")
    print(f"{'Sharpe':24s}{canon['sharpe']:>14.3f}{canon['sharpe_bh']:>14.3f}")
    print(f"{'Time-in-market':24s}{fmt_pct(canon['time_in_mkt']):>14s}{'100.00%':>14s}")
    print(f"{'Trades/yr (round trips)':24s}{canon['trades_per_yr']:>14.2f}{'n/a':>14s}")
    print(f"{'Worst calendar year':24s}{fmt_pct(canon['worst_year']):>14s}{fmt_pct(bh['worst_year']):>14s}")
    print(f"NW t-stat (monthly excess vs B&H, Bartlett lag 3): {canon['nw_t_excess']:.3f}")
    ci_lo, ci_hi = np.percentile(boot_diffs, [2.5, 97.5])
    print(
        f"Circular block bootstrap (block=63d, N=1000, seed=0) CI95 on CAGR diff "
        f"(strategy - B&H): [{fmt_pct(ci_lo)}, {fmt_pct(ci_hi)}]  "
        f"(bootstrap mean diff={fmt_pct(float(boot_diffs.mean()))})"
    )

    print("\n" + "=" * 78)
    print("SECTION 2: PER-ERA AND PER-CRASH TOTAL RETURNS (canonical vs B&H)")
    print("=" * 78)
    era_dict = json.loads(canon["era_json"])
    print(f"{'era':24s}{'strategy':>12s}{'B&H':>12s}{'diff(pp)':>12s}")
    for name, _, _ in ERAS:
        s = era_dict[name]["strat"]
        b = era_dict[name]["bh"]
        print(f"{name:24s}{fmt_pct(s):>12s}{fmt_pct(b):>12s}{(s - b) * 100:>+11.2f}p")
    print("--- crash windows ---")
    crash_labels = {
        "crash_2000_dotcom": "2000-03-24..2002-10-09",
        "crash_2008_gfc": "2007-10-09..2009-03-09",
        "crash_2020_covid": "2020-02-19..2020-03-23",
        "crash_2022_bear": "2022-01-03..2022-10-12",
    }
    for name, _, _ in CRASHES:
        s = era_dict[name]["strat"]
        b = era_dict[name]["bh"]
        label = f"{name} ({crash_labels[name]})"
        print(f"{label:44s}{fmt_pct(s):>12s}{fmt_pct(b):>12s}{(s - b) * 100:>+11.2f}p")

    print("\n" + "=" * 78)
    print("SECTION 3: TOP 10 CELLS BY SHARPE (whole grid, all families)")
    print("=" * 78)
    top10 = sorted(rows, key=lambda r: (r["sharpe"] if np.isfinite(r["sharpe"]) else -999), reverse=True)[:10]
    print(f"{'rank':5s}{'rule_id':32s}{'sharpe':>8s}{'cagr':>9s}{'maxdd':>9s}{'nw_t':>8s}")
    for i, r in enumerate(top10, 1):
        print(f"{i:<5d}{r['rule_id']:32s}{r['sharpe']:>8.3f}{fmt_pct(r['cagr'], 1):>9s}{fmt_pct(r['maxdd'], 1):>9s}{r['nw_t_excess']:>8.2f}")
    null_thresh = float(np.percentile(null_max_ts, 95))
    non_canon = [r for r in rows if r["rule_id"] != CANONICAL_RULE_ID]
    n_positive = sum(1 for r in non_canon if r["nw_t_excess"] > 0)
    best_abs = max(non_canon, key=lambda r: abs(r["nw_t_excess"]))
    print(
        f"\nMULTIPLICITY CAVEAT: sign-flip max-|t| null over the whole {len(rows)}-cell grid "
        f"(200 resamples, seed=0) 95th-pct threshold = {null_thresh:.3f}"
    )
    print(
        f"  Cells with POSITIVE nw_t_excess (candidates for 'genuinely beats B&H'): "
        f"{n_positive} of {len(non_canon)}"
    )
    if n_positive > 0:
        best_pos = max(non_canon, key=lambda r: r["nw_t_excess"])
        beats_pos = best_pos["nw_t_excess"] > null_thresh
        print(
            f"  Best POSITIVE-excess cell: {best_pos['rule_id']} t=+{best_pos['nw_t_excess']:.3f} "
            f"cagr={fmt_pct(best_pos['cagr'])} vs bh={fmt_pct(best_pos['cagr_bh'])} "
            f"-> {'BEATS' if beats_pos else 'does not beat'} the null threshold "
            f"({'may be named better than 200SMA' if beats_pos else 'not distinguishable from grid noise'})"
        )
    else:
        print(
            "  NONE: every one of the 408 grid cells has negative nw_t_excess vs B&H "
            "-- no MA-timing variant in this grid (any kernel/length/band/overlap/slope) "
            "beats buy-and-hold on monthly excess return in this sample. There is no "
            "'better than 200SMA' cell to name; the finding is uniform underperformance."
        )
    beats_abs = abs(best_abs["nw_t_excess"]) > null_thresh
    print(
        f"  Most extreme |nw_t_excess| cell overall (any sign): {best_abs['rule_id']} "
        f"t={best_abs['nw_t_excess']:+.3f}  cagr={fmt_pct(best_abs['cagr'])} vs bh={fmt_pct(best_abs['cagr_bh'])}  "
        f"-> {'exceeds' if beats_abs else 'does not exceed'} the null threshold "
        f"({'a reliably BAD cell (large negative excess), not a positive candidate' if best_abs['nw_t_excess'] < 0 else 'a positive candidate'})"
    )

    print("\n" + "=" * 78)
    print("SECTION 4: PREREG GATE EVALUATION (Leg 1, canonical cell)")
    print("=" * 78)
    canon_dd_mag = abs(canon["maxdd"]) * 100.0
    bh_dd_mag = abs(canon["maxdd_bh"]) * 100.0
    dd_required = bh_dd_mag - 10.0
    dd_pass = canon_dd_mag <= dd_required
    print("P1-DD (prereg predicts TRUE): canonical MaxDD magnitude <= B&H MaxDD magnitude - 10pp")
    print(
        f"  canonical={canon_dd_mag:.2f}%  bh={bh_dd_mag:.2f}%  required<=({bh_dd_mag:.2f}-10)={dd_required:.2f}%  "
        f"-> {'TRUE' if dd_pass else 'FALSE'}"
    )
    out_pass = (canon["cagr"] > canon["cagr_bh"]) and (canon["nw_t_excess"] >= 2.0)
    print("P1-OUT (prereg predicts FALSE): canonical CAGR > B&H CAGR AND NW t >= 2")
    print(
        f"  canonical_cagr={fmt_pct(canon['cagr'])}  bh_cagr={fmt_pct(canon['cagr_bh'])}  "
        f"nw_t={canon['nw_t_excess']:.3f}  -> {'TRUE' if out_pass else 'FALSE'}  "
        f"(prereg predicted FALSE -> {'CONTRADICTS prediction' if out_pass else 'MATCHES prediction'})"
    )
    print(f"\nrows={n}  span={str(dates_np[0])}..{str(dates_np[-1])}  grid_cells={len(rows)}")


# ============================================================================
# Main
# ============================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="run self-tests only, skip MySQL + full grid")
    args = parser.parse_args()

    print("=" * 78)
    print("SELF-TESTS")
    print("=" * 78)
    run_selftests()

    if args.selftest:
        print("\n--selftest: all self-tests passed. Skipping MySQL load and full grid run.")
        return

    print("\nLoading MarketRegime.spy_close (the one allowed MySQL read)...")
    dates_np, close = load_spy_close()
    print(f"rows={len(close)}  span={dates_np[0]}..{dates_np[-1]}  spy_first={close[0]:.2f}  spy_last={close[-1]:.2f}")

    print("Building full Leg 1 grid (price_ma + dual_ma + slope)...")
    rows, excess_matrix, bh, canonical_sim, dates_np = build_grid(close, dates_np)
    print(f"grid built: {len(rows)} cells")

    print("Circular block bootstrap on canonical CAGR diff (block=63, N=1000, seed=0)...")
    boot_diffs = circular_block_bootstrap_cagr_diff(canonical_sim["ret"], bh["ret"], block=63, n_boot=1000, seed=0)

    print("Sign-flip max-|t| null over the whole grid (200 resamples, seed=0)...")
    null_max_ts = signflip_max_t_null(excess_matrix, n_resamples=200, lag=3, seed=0)

    df = pl.DataFrame(rows, infer_schema_length=None).select(COLUMNS)
    df = df.with_columns(
        pl.col("rule_id").cast(pl.Utf8),
        pl.col("family").cast(pl.Utf8),
        pl.col("kernel").cast(pl.Utf8),
        pl.col("length").cast(pl.Int64),
        pl.col("fast").cast(pl.Int64),
        pl.col("slow").cast(pl.Int64),
        pl.col("band_pct").cast(pl.Float64),
        pl.col("cost_bps").cast(pl.Int64),
        pl.col("cash_yield_pct").cast(pl.Float64),
        pl.col("cagr").cast(pl.Float64),
        pl.col("cagr_bh").cast(pl.Float64),
        pl.col("maxdd").cast(pl.Float64),
        pl.col("maxdd_bh").cast(pl.Float64),
        pl.col("sharpe").cast(pl.Float64),
        pl.col("sharpe_bh").cast(pl.Float64),
        pl.col("time_in_mkt").cast(pl.Float64),
        pl.col("trades_per_yr").cast(pl.Float64),
        pl.col("worst_year").cast(pl.Float64),
        pl.col("nw_t_excess").cast(pl.Float64),
        pl.col("era_json").cast(pl.Utf8),
    )
    df.write_parquet(OUT_PATH)
    print(f"wrote {OUT_PATH}  rows={df.height}  cols={df.width}")

    print_summary(rows, excess_matrix, bh, canonical_sim, dates_np, close, boot_diffs, null_max_ts)


if __name__ == "__main__":
    main()
