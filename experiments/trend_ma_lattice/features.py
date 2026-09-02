"""Point-in-time EMA/SMA lattice feature engineering for the trend MA-lattice study.

See GAMEPLAN.md sections 2 (trend anchors) and 3 (A1 feature grid / A2 sub-term
decomposition) for the spec this implements.

POINT-IN-TIME GUARANTEE: every array built here is CAUSAL by construction -- the
value at index i is a pure function of closes[0..i] only. talib.EMA / talib.SMA
are recursive / rolling filters that never look forward (this is the same call
core.py:3808-3817 uses to populate the stored Indicator.ema_*/ma_* columns, over
the FULL close-price array). All derived arrays (above/dist/slope/cross/stack/
divergence) are built with strictly backward-looking numpy ops (explicit shifts
with an index-0 sentinel, never np.roll wraparound; forward-fill via
np.maximum.accumulate over already-seen indices only). Consequence: computing
the full-array series ONCE per symbol and indexing at a signal date is
mathematically identical to recomputing on a series truncated at that date --
exactly the property mine.py's look-ahead self-test (#2) checks empirically.
"""
from __future__ import annotations

import math
import numpy as np
import talib

KERNELS = ("EMA", "SMA")
PERIODS = (8, 13, 21, 34, 50, 100, 150, 200)
CROSS_PAIRS = ((8, 21), (13, 34), (21, 50), (21, 100), (50, 100), (50, 200), (100, 200))
STACK_TRIPLE = (21, 50, 200)          # stack alignment / trend-lattice anchor periods
DIVERGENCE_PERIODS = (21, 50, 200)    # kernel-divergence (EMA-SMA spread) anchor periods

# "days_since_cross" / "fresh cross within <=5d" are measured in TRADING BARS here
# (the arrays are indexed by trading bar, not calendar day) -- see mine.py deviations
# note in the final report. This does not affect the point-in-time property.
SLOPE_LOOKBACK_BARS = 10
FRESH_CROSS_BARS = 5
CROSS_CAP_BARS = 60

# --- trend sub-term formula constants -- MUST mirror database/models/core.py
# Stock.calculate_trend_score (lines ~6029-6114) EXACTLY. lookback=20 default,
# weekly=False (daily indicators only; this study does not touch the weekly path).
TREND_LOOKBACK = 20
TREND_MOM_SHORT_BARS = 10   # past_short = recent_indicators[min(10, lookback)]
TREND_MOM_LONG_BARS = 20    # past_long  = recent_indicators[lookback]


def talib_ma(closes: np.ndarray, kernel: str, period: int) -> np.ndarray:
    if kernel == "EMA":
        return talib.EMA(closes, timeperiod=period)
    if kernel == "SMA":
        return talib.SMA(closes, timeperiod=period)
    raise ValueError(f"unknown kernel {kernel!r}")


def build_symbol_series(closes: np.ndarray) -> dict:
    """closes: 1D float64 ascending-date array (index 0 = earliest bar).

    Returns a dict of same-length causal arrays:
      ma[(kernel,period)]          -> MA level (NaN during warmup)
      above[(kernel,period)]       -> {0.,1.,nan}  close > MA
      dist[(kernel,period)]        -> signed % = (close-MA)/MA*100
      slope_sign[(kernel,period)]  -> {-1,0,1,nan} sign(MA[i]-MA[i-10])
      cross[(kernel,pf,ps)]        -> dict(state, days_since, fresh_golden, fresh_death)
      stack[kernel]                -> 0-4 aligned count (close/MA21/MA50/MA200 ordering), NaN if insufficient
      kdiv[period]                 -> (EMA_p-SMA_p)/SMA_p * 100
    """
    closes = np.asarray(closes, dtype=np.float64)
    n = len(closes)

    ma = {}
    for period in PERIODS:
        for kernel in KERNELS:
            ma[(kernel, period)] = talib_ma(closes, kernel, period)

    above, dist, slope_sign = {}, {}, {}
    for key, arr in ma.items():
        with np.errstate(invalid="ignore"):
            above[key] = np.where(np.isnan(arr), np.nan, (closes > arr).astype(float))
            dist[key] = (closes - arr) / arr * 100.0
        shifted = np.full(n, np.nan)
        if n > SLOPE_LOOKBACK_BARS:
            shifted[SLOPE_LOOKBACK_BARS:] = arr[: n - SLOPE_LOOKBACK_BARS]
        with np.errstate(invalid="ignore"):
            slope_sign[key] = np.sign(arr - shifted)

    cross = {}
    idx_arr = np.arange(n)
    for kernel in KERNELS:
        for (pf, ps) in CROSS_PAIRS:
            fast, slow = ma[(kernel, pf)], ma[(kernel, ps)]
            valid = ~np.isnan(fast) & ~np.isnan(slow)
            is_above = valid & (fast > slow)
            changed = np.zeros(n, dtype=bool)
            if n > 1:
                changed[1:] = valid[1:] & valid[:-1] & (is_above[1:] != is_above[:-1])
            last_cross = np.where(changed, idx_arr, -1)
            last_cross = np.maximum.accumulate(last_cross)  # forward-fill, backward-looking only
            days_since = np.where(last_cross >= 0, (idx_arr - last_cross).astype(float), np.nan)
            days_since_capped = np.where(np.isnan(days_since), np.nan,
                                          np.minimum(days_since, float(CROSS_CAP_BARS)))
            days_since_capped = np.where(valid, days_since_capped, np.nan)
            fresh = np.where(np.isnan(days_since), False, days_since <= FRESH_CROSS_BARS)
            cross[(kernel, pf, ps)] = dict(
                state=np.where(valid, is_above.astype(float), np.nan),
                days_since=days_since_capped,
                fresh_golden=np.where(valid, (fresh & is_above).astype(float), np.nan),
                fresh_death=np.where(valid, (fresh & ~is_above).astype(float), np.nan),
            )

    stack = {}
    for kernel in KERNELS:
        e21, e50, e200 = ma[(kernel, 21)], ma[(kernel, 50)], ma[(kernel, 200)]
        valid = ~np.isnan(e21) & ~np.isnan(e50) & ~np.isnan(e200)
        c1 = (closes > e21).astype(float)
        c2 = (closes > e50).astype(float)
        c3 = (closes > e200).astype(float)
        c4 = ((e21 > e50) & (e50 > e200)).astype(float)
        aligned = c1 + c2 + c3 + c4
        stack[kernel] = np.where(valid, aligned, np.nan)

    kdiv = {}
    for period in DIVERGENCE_PERIODS:
        e, s = ma[("EMA", period)], ma[("SMA", period)]
        valid = ~np.isnan(e) & ~np.isnan(s)
        with np.errstate(invalid="ignore"):
            d = (e - s) / s * 100.0
        kdiv[period] = np.where(valid, d, np.nan)

    return dict(ma=ma, above=above, dist=dist, slope_sign=slope_sign,
                cross=cross, stack=stack, kdiv=kdiv, n=n)


def extract_row(i: int, series: dict) -> dict:
    """Flatten all feature values at index i into a flat {colname: float|nan} dict."""
    row = {}
    for (kernel, period), arr in series["ma"].items():
        row[f"ma_{kernel}_{period}"] = float(arr[i])
    for (kernel, period), arr in series["above"].items():
        row[f"above_{kernel}_{period}"] = float(arr[i])
    for (kernel, period), arr in series["dist"].items():
        row[f"dist_{kernel}_{period}"] = float(arr[i])
    for (kernel, period), arr in series["slope_sign"].items():
        row[f"slope_{kernel}_{period}"] = float(arr[i])
    for (kernel, pf, ps), d in series["cross"].items():
        tag = f"{kernel}_{pf}_{ps}"
        row[f"cstate_{tag}"] = float(d["state"][i])
        row[f"cdays_{tag}"] = float(d["days_since"][i])
        row[f"cfg_{tag}"] = float(d["fresh_golden"][i])
        row[f"cfd_{tag}"] = float(d["fresh_death"][i])
    for kernel, arr in series["stack"].items():
        row[f"stack_{kernel}"] = float(arr[i])
    for period, arr in series["kdiv"].items():
        row[f"kdiv_{period}"] = float(arr[i])
    return row


def feature_columns() -> list[str]:
    """Column-name list matching extract_row's keys (built from a throwaway 5-bar series)."""
    dummy = build_symbol_series(np.linspace(1.0, 2.0, max(210, CROSS_CAP_BARS + 10)))
    return list(extract_row(len(dummy["ma"][("EMA", 8)]) - 1, dummy).keys())


def _safe(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def recompute_trend(ema21, ema50, ema200, close, ema21_m10, ema50_m20):
    """Exact reproduction of Stock.calculate_trend_score (core.py ~6029-6114),
    lookback=20, weekly=False.

    Inputs are the CURRENT bar's ema_21/ema_50/ema_200/close, plus ema_21 from
    TREND_MOM_SHORT_BARS(10) bars back and ema_50 from TREND_MOM_LONG_BARS(20)
    bars back (see module docstring). None/NaN-safe.

    Returns (trend_int, position_score, alignment_score, momentum_score, macro_score),
    or a 5-tuple of Nones if inputs are insufficient -- mirrors the original
    function's early-return conditions:
      - current ema_21 or ema_50 missing -> None (hard)
      - close missing -> None (hard; guards the _ph_cache.get(...) is None branch)
      - ema_21 10-bars-back missing -> None (hard; past_short.ema_21 is None check)
      - ema_50 20-bars-back missing -> SOFT degrade (momentum_score = mom_short only)
      - ema_200 missing -> SOFT degrade (position_score = pos_short only; macro_score = 50)
    """
    ema21 = _safe(ema21); ema50 = _safe(ema50); ema200 = _safe(ema200)
    close = _safe(close); ema21_m10 = _safe(ema21_m10); ema50_m20 = _safe(ema50_m20)

    if ema21 is None or ema50 is None or close is None or ema21_m10 is None:
        return (None,) * 5

    # === 1. PRICE POSITION (30%) ===
    price_vs_50 = (close - ema50) / ema50 * 100
    pos_short = 50 + 50 * math.tanh(price_vs_50 * 0.3)
    if ema200 is not None:
        price_vs_200 = (close - ema200) / ema200 * 100
        pos_long = 50 + 50 * math.tanh(price_vs_200 * 0.15)
        position_score = pos_short * 0.6 + pos_long * 0.4
    else:
        position_score = pos_short

    # === 2. MA ALIGNMENT (25%) ===
    alignment_pct = (ema21 - ema50) / ema50 * 100
    alignment_score = 50 + 50 * math.tanh(alignment_pct * 0.4)

    # === 3. TREND MOMENTUM (20%) ===
    if ema21_m10 == 0:
        mom_short = 50.0
    else:
        slope_21 = (ema21 - ema21_m10) / ema21_m10 * 100
        mom_short = 50 + 50 * math.tanh(slope_21 * 0.5)
    if ema50_m20 is not None and ema50_m20 != 0:
        slope_50 = (ema50 - ema50_m20) / ema50_m20 * 100
        mom_long = 50 + 50 * math.tanh(slope_50 * 0.8)
        momentum_score = mom_short * 0.5 + mom_long * 0.5
    else:
        momentum_score = mom_short

    # === 4. MACRO STRUCTURE (25%) ===
    if ema200 is not None and ema200 != 0:
        macro_pct = (ema50 - ema200) / ema200 * 100
        macro_score = 50 + 50 * math.tanh(macro_pct * 0.2)
    else:
        macro_score = 50.0

    raw_score = (position_score * 0.30 + alignment_score * 0.25
                 + momentum_score * 0.20 + macro_score * 0.25)
    trend_int = int(max(0, min(100, round(raw_score))))
    return trend_int, position_score, alignment_score, momentum_score, macro_score


# --- horizon-variant sub-terms (A2 "per horizon-variant of each sub-term") -----------
# Same functional shapes as core.py's sub-terms, but built from adjacent periods in
# our mined EMA lattice instead of the shipped 21/50/200 anchor set. Purely diagnostic
# (never fed back into a real score) -- used only to test whether a faster/slower
# horizon carries more (or less) signal than the shipped anchor.
def position_alt(ema21, ema100, close):
    ema21 = _safe(ema21); ema100 = _safe(ema100); close = _safe(close)
    if close is None or ema21 is None:
        return None
    pos_short = 50 + 50 * math.tanh((close - ema21) / ema21 * 100 * 0.3)
    if ema100 is None:
        return pos_short
    pos_long = 50 + 50 * math.tanh((close - ema100) / ema100 * 100 * 0.15)
    return pos_short * 0.6 + pos_long * 0.4


def alignment_alt(ema_fast, ema_slow):
    ema_fast = _safe(ema_fast); ema_slow = _safe(ema_slow)
    if ema_fast is None or ema_slow is None or ema_slow == 0:
        return None
    return 50 + 50 * math.tanh((ema_fast - ema_slow) / ema_slow * 100 * 0.4)


def momentum_alt(ema_fast, ema_fast_prev, ema_slow, ema_slow_prev):
    ema_fast = _safe(ema_fast); ema_fast_prev = _safe(ema_fast_prev)
    ema_slow = _safe(ema_slow); ema_slow_prev = _safe(ema_slow_prev)
    if ema_fast is None or ema_fast_prev is None or ema_fast_prev == 0:
        return None
    mom_short = 50 + 50 * math.tanh((ema_fast - ema_fast_prev) / ema_fast_prev * 100 * 0.5)
    if ema_slow is None or ema_slow_prev is None or ema_slow_prev == 0:
        return mom_short
    mom_long = 50 + 50 * math.tanh((ema_slow - ema_slow_prev) / ema_slow_prev * 100 * 0.8)
    return mom_short * 0.5 + mom_long * 0.5


def macro_alt(ema_mid, ema_slow):
    ema_mid = _safe(ema_mid); ema_slow = _safe(ema_slow)
    if ema_mid is None or ema_slow is None or ema_slow == 0:
        return None
    return 50 + 50 * math.tanh((ema_mid - ema_slow) / ema_slow * 100 * 0.2)
