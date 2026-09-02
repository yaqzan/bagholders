"""Peak-Fakeout study -- point-in-time OHLC feature engineering (pure library).

See experiments/peak_fakeout/PREREGISTRATION.md for the locked feature-family
definitions (F1-F12) this implements.

POINT-IN-TIME GUARANTEE: every array here is CAUSAL by construction -- the
value at index i is a pure function of closes[0..i] / volumes[0..i] only.
All rolling stats use cumsum/cummax prefix tricks (never a centered window,
never np.roll wraparound). Consequence (mirrors trend_ma_lattice/features.py's
own guarantee, and is checked empirically by mine.py's selftest ST1): computing
the full-array series ONCE per symbol and indexing at a signal date is
identical to recomputing on a series truncated at that date.

Null-guard policy (locked by the orchestrator prompt): any "rolling stat"
feature (sigma20/60, runup5/20/60_sig, pct_from_high126/252, F2 vol20,
F3 parabolic, F4 climax_day, F5 streak_up, F6 high_zone_age, F7 base_days,
F8 vol_z, F10 runup60_sig) is null if EITHER (a) its own lookback window has
<90% of the required bars available, OR (b) the symbol has <260 total bars
before entry (blanket young-listing guard -- see MIN_TOTAL_BARS). Both
guards are enforced in extract_row(), never inside the raw array builders
(which compute defensively and never crash on short series).

F1 (mcap), F9 (earnings), F11 (trend), F12 (vix) are NOT OHLC lookback-window
features (F1 is a snapshot ratio via an imported utility; F9/F11/F12 come from
external, non-price data sources) -- the blanket young-listing guard does NOT
apply to them; they are computed/joined in mine.py, not here. See mine.py for
that fusion step and the report's "implementation notes" for why this reading
of the prompt's "Rolling stats" header was chosen.
"""
from __future__ import annotations

import bisect
import math
import warnings

import numpy as np

MIN_TOTAL_BARS = 260          # blanket young-listing guard (task-specified)
MIN_COVERAGE_FRAC = 0.90      # per-feature "90% of required bars" guard

# runup20_sig / runup5_sig are normalized by sigma20 (matched-horizon vol),
# per the orchestrator prompt's literal formula. NOTE: this is a DIFFERENT
# quantity from the PREREGISTRATION's P_run gate measure, which is locked to
# use sigma60 ("sigma = 60d daily close-to-close vol") -- mine.py computes
# that gate quantity separately from the raw _runup20_raw/_sigma60 columns
# exposed below, rather than overloading this column with two definitions.
# See mine.py module docstring "P_run gate vs runup20_sig feature" note.


def _rolling_mean_std_count(x: np.ndarray, window: int, min_frac: float = MIN_COVERAGE_FRAC):
    """Causal rolling (mean, std, count) via cumsum prefix trick (same
    mechanism as trend_ma_lattice's realized_vol, generalized to expose mean
    + count and a configurable coverage fraction instead of a hardcoded floor).

    x may contain NaN (e.g. rets[0]). count[i] = # non-NaN values in the
    trailing `window` slice ending at i (window shrinks near i=0 -- never
    looks outside [0, i]). mean/std (ddof=1) are NaN wherever
    count < ceil(window*min_frac)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    valid = ~np.isnan(x)
    xf = np.where(valid, x, 0.0)
    csum = np.concatenate([[0.0], np.cumsum(xf)])
    csum2 = np.concatenate([[0.0], np.cumsum(xf * xf)])
    ccnt = np.concatenate([[0.0], np.cumsum(valid.astype(np.float64))])
    idx = np.arange(n)
    lo = np.maximum(0, idx - window + 1)
    cnt = ccnt[idx + 1] - ccnt[lo]
    s = csum[idx + 1] - csum[lo]
    s2 = csum2[idx + 1] - csum2[lo]
    min_needed = max(2, math.ceil(window * min_frac))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s / cnt
        var = (s2 - cnt * mean * mean) / np.maximum(cnt - 1, 1)
    std = np.sqrt(np.maximum(var, 0.0))
    ok = cnt >= min_needed
    mean = np.where(ok, mean, np.nan)
    std = np.where(ok, std, np.nan)
    return mean, std, cnt


def _rolling_max_causal(x: np.ndarray, window: int) -> np.ndarray:
    """Causal rolling max (inclusive of i, expanding for i < window-1).
    NaN-safe (nanmax; an all-NaN window returns NaN, never crashes/raises).
    Never looks past index i regardless of what exists later in `x` --
    (see module docstring: this is what makes ST1 truncation bit-identical)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if n >= window:
            windows = np.lib.stride_tricks.sliding_window_view(x, window)
            out[window - 1:] = np.nanmax(windows, axis=1)
        # expanding max (causal prefix op) for i < window-1
        filled = np.where(np.isnan(x), -np.inf, x)
        cm = np.maximum.accumulate(filled)
        cm = np.where(np.isneginf(cm), np.nan, cm)
        lim = min(window - 1, n)
        out[:lim] = cm[:lim]
    return out


def _consecutive_streak_causal(bool_arr: np.ndarray) -> np.ndarray:
    """streak[i] = # of consecutive True values in bool_arr ending at i
    (inclusive); 0 where bool_arr[i] is False. O(n) vectorized via the
    forward-fill-only maximum.accumulate trick (same causal pattern
    trend_ma_lattice/features.py uses for cross-freshness days_since)."""
    n = len(bool_arr)
    idx = np.arange(n)
    reset_points = np.where(~bool_arr, idx, -1)
    last_reset = np.maximum.accumulate(reset_points)
    streak = idx - last_reset
    return np.where(bool_arr, streak, 0)


def compute_base_days(closes: np.ndarray, idx: int, exclude: int = 21, cap: int = 252) -> float:
    """F7 base_days: days since price last traded at/above TODAY's close
    (closes[idx]), searching j in [max(0,idx-cap), idx-exclude] for the MOST
    RECENT (largest j) qualifying day. Point query (not a precomputed array --
    only needed at the ~8/symbol signal rows, not every one of the ~1.9M bars;
    a targeted backward scan bounded to `cap` entries is cheap and avoids
    building a 5th O(n) array per symbol for a quantity used at <1% of bars).

    Returns idx-j (naturally in [exclude, cap]) if found, else `cap` (both
    "never found within the cap-day search" and "found exactly at the
    search floor" collapse to the same sentinel -- accepted, F7 buckets on
    terciles so this coarse tail doesn't need finer resolution). Never
    crashes: insufficient history (idx < exclude) also returns `cap`, and the
    MIN_TOTAL_BARS blanket guard in extract_row() nulls it out entirely for
    young listings regardless of what this function would return."""
    hi = idx - exclude
    if hi < 0:
        return float(cap)
    lo = max(0, idx - cap)
    if hi < lo:
        return float(cap)
    ref = closes[idx]
    window = closes[lo: hi + 1]
    nz = np.nonzero(window >= ref)[0]
    if nz.size == 0:
        return float(cap)
    j = lo + nz[-1]
    return float(min(idx - j, cap))


def compute_days_to_earnings(sorted_earnings_dates, entry_date, max_window: int = 60):
    """F9 raw days_to_earnings: (next earnings date >= entry) - entry, in
    days. None if no upcoming earnings date at all, OR if the next one is
    more than `max_window` days out (both cases bucket to '>21_or_none' in
    mine.py's bucket spec, not left bucket-less -- see PREREGISTRATION F9).
    `sorted_earnings_dates` must be pre-sorted ascending (bisect_left)."""
    if not sorted_earnings_dates:
        return None
    i = bisect.bisect_left(sorted_earnings_dates, entry_date)
    if i >= len(sorted_earnings_dates):
        return None
    delta = (sorted_earnings_dates[i] - entry_date).days
    if delta > max_window:
        return None
    return delta


def build_symbol_series(closes, volumes) -> dict:
    """closes, volumes: 1D arrays, ascending-date order, index 0 = earliest bar.
    Returns a dict of same-length causal arrays (see module docstring for the
    point-in-time guarantee) consumed by extract_row()."""
    closes = np.asarray(closes, dtype=np.float64)
    volumes = np.asarray(volumes, dtype=np.float64)
    n = len(closes)

    rets = np.full(n, np.nan)
    if n > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            rets[1:] = closes[1:] / closes[:-1] - 1.0

    _, sigma20, cnt20 = _rolling_mean_std_count(rets, 20)
    _, sigma60, cnt60 = _rolling_mean_std_count(rets, 60)
    vol_mean60, _vol_std60_unused, volcnt60 = _rolling_mean_std_count(volumes, 60)

    def _shifted_ret(h):
        out = np.full(n, np.nan)
        if n > h:
            with np.errstate(divide="ignore", invalid="ignore"):
                out[h:] = closes[h:] / closes[:-h] - 1.0
        return out

    runup5_raw = _shifted_ret(5)
    runup20_raw = _shifted_ret(20)
    runup60_raw = _shifted_ret(60)

    with np.errstate(divide="ignore", invalid="ignore"):
        runup5_sig = runup5_raw / (sigma20 * math.sqrt(5))
        runup20_sig = runup20_raw / (sigma20 * math.sqrt(20))
        runup60_sig = runup60_raw / (sigma60 * math.sqrt(60))
        parabolic = np.where(np.abs(runup20_sig) > 0.3, runup5_sig / runup20_sig, np.nan)

    high126 = _rolling_max_causal(closes, 126)
    high252 = _rolling_max_causal(closes, 252)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_from_high126 = closes / high126 - 1.0
        pct_from_high252 = closes / high252 - 1.0

    ret_max10 = _rolling_max_causal(rets, 10)
    with np.errstate(divide="ignore", invalid="ignore"):
        climax_day = ret_max10 / sigma20

    up = np.zeros(n, dtype=bool)
    if n > 1:
        up[1:] = closes[1:] > closes[:-1]
    streak_up = _consecutive_streak_causal(up)

    with np.errstate(invalid="ignore"):
        inzone = pct_from_high126 >= -0.03   # NaN-safe: NaN comparisons -> False
    high_zone_age = _consecutive_streak_causal(inzone)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = volumes / vol_mean60
        vol_z = np.log(ratio)
    bad_vz = (volumes <= 0) | np.isnan(vol_mean60) | (vol_mean60 <= 0) | ~np.isfinite(vol_z)
    vol_z = np.where(bad_vz, np.nan, vol_z)

    return dict(
        n=n, closes=closes, volumes=volumes, rets=rets,
        sigma20=sigma20, sigma60=sigma60, cnt20=cnt20, cnt60=cnt60,
        runup5_raw=runup5_raw, runup20_raw=runup20_raw, runup60_raw=runup60_raw,
        runup5_sig=runup5_sig, runup20_sig=runup20_sig, runup60_sig=runup60_sig,
        parabolic=parabolic,
        pct_from_high126=pct_from_high126, pct_from_high252=pct_from_high252,
        climax_day=climax_day, streak_up=streak_up, high_zone_age=high_zone_age,
        vol_mean60=vol_mean60, vol_z=vol_z,
    )


def _cov_ok(n_total: int, window: int, min_frac: float = MIN_COVERAGE_FRAC) -> bool:
    avail = min(n_total, window)
    return avail >= math.ceil(window * min_frac)


def extract_row(idx: int, series: dict) -> dict:
    """Flatten all OHLC-derived feature values at index i into a flat dict,
    applying the null-guard policy (module docstring). `_young_listing` /
    `n_bars_before_entry` are diagnostics for ST5, not analytical features."""
    n_total = idx + 1
    young = n_total < MIN_TOTAL_BARS
    closes = series["closes"]

    def g(key):
        v = series[key][idx]
        return float("nan") if young else (float("nan") if math.isnan(v) else float(v))

    row = {
        "n_bars_before_entry": n_total,
        "_young_listing": bool(young),
        "sigma20": g("sigma20"),
        "sigma60": g("sigma60"),
        "runup5_sig": g("runup5_sig"),
        "runup20_sig": g("runup20_sig"),
        "runup60_sig": g("runup60_sig"),          # F10
        "_runup20_raw": g("runup20_raw"),          # internal: P_run gate uses this / sigma60 (mine.py)
        "_runup5_raw": g("runup5_raw"),
        "_runup60_raw": g("runup60_raw"),
    }

    row["pct_from_high126"] = (float("nan") if (young or not _cov_ok(n_total, 126))
                                else float(series["pct_from_high126"][idx]))
    row["pct_from_high252"] = (float("nan") if (young or not _cov_ok(n_total, 252))
                                else float(series["pct_from_high252"][idx]))

    row["vol20"] = row["sigma20"]                  # F2: raw sigma20 value
    row["parabolic"] = g("parabolic")              # F3
    row["climax_day"] = g("climax_day")            # F4
    row["streak_up"] = float("nan") if young else float(series["streak_up"][idx])          # F5
    row["high_zone_age"] = float("nan") if young else float(series["high_zone_age"][idx])  # F6
    row["base_days"] = float("nan") if young else compute_base_days(closes, idx)           # F7
    row["vol_z"] = g("vol_z")                      # F8

    return row


def feature_columns() -> list[str]:
    """OHLC-derived column names checked by mine.py's ST1 look-ahead
    truncation self-test (F1/F9/F11/F12 are external-lookup joins done in
    mine.py, not part of this pure-OHLC library -- see module docstring)."""
    return [
        "sigma20", "sigma60", "runup5_sig", "runup20_sig", "runup60_sig",
        "_runup20_raw", "_runup5_raw", "_runup60_raw",
        "pct_from_high126", "pct_from_high252", "vol20", "parabolic",
        "climax_day", "streak_up", "high_zone_age", "base_days", "vol_z",
    ]
