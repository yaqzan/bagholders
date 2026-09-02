"""Event-vs-Cyclical noise decomposition -- read-only mine (NO recalc, NO MC).

Implements experiments/event_noise_decomp/PREREGISTRATION.md (LOCKED) verbatim.

Partitions the funded v74 75+ CALL ledger (apex15 barrier) into EVENT-driven vs
CYCLICAL outcomes using point-in-time idiosyncratic jump detection, then measures
forecast skill separately on each subset.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/event_noise_decomp/mine.py --selftest
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/event_noise_decomp/mine.py --smoke
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/event_noise_decomp/mine.py

Outputs:
    .cache/event_noise_decomp/classified_ledger[_smoke].parquet
    experiments/event_noise_decomp/SUMMARY[_smoke].txt
    experiments/event_noise_decomp/results[_smoke].json

ASCII-only stdout (Windows cp1252 console -- no unicode anywhere).

--- Provenance of copied/imported machinery (deliberate, see below) ------------
* Barrier logic (realized_vol / forward_touch / label_win_ev) is COPIED VERBATIM
  from experiments/trend_ma_lattice/mine.py (the module that BUILT this ledger).
  It is copied rather than imported because that module does a bare
  `import features` of its OWN local features.py, as does peak_fakeout/mine.py;
  importing both would let one experiment's `features` silently win for the
  other via sys.modules (the collision peak_fakeout/mine.py documents).
* CR1 cluster-robust sandwich machinery is IMPORTED (not reimplemented) from
  experiments/peak_fakeout/mine.py via importlib under a private module name.
  That is the only one of the two we import, so no `features` collision arises.

--- Implementation readings of the locked spec (declared, not silent) ---------
R1. `sigma_idio_s(t)` = "stdev of r_idio over the same trailing 120d window
    ending at t-1". PRIMARY reading implemented here is the LITERAL one: the
    sample stdev of the `r_idio_s` SERIES over that window, where each past day's
    r_idio used its OWN point-in-time beta. A second reading (the OLS residual
    stdev of the single regression that produced beta_s(t)) is computed as
    `sigma_idio_resid` and reported as a robustness diagnostic (jump-flag
    agreement rate). Neither uses any information at or after day t.
R2. The trailing "120 trading days" / "252 trading days" windows are ranges on
    the EMPIRICAL global trading-day grid (>= MIN_SYMS_TRADING_DAY distinct
    symbols with a bar), not counts of the symbol's own bars. A symbol needs
    >= MIN_BETA_BARS valid (r_s, r_SPY) pairs inside that grid range, else the
    row is `beta_unavailable`.
R3. The counterfactual re-walk scales each in-window bar's high/low/close by the
    cumulative factor exp(sum of (mu - r_idio)) over jump days up to that bar,
    i.e. jump-day idiosyncratic returns are replaced by the trade's own non-jump
    mean idiosyncratic drift while the beta*market component is left untouched.
    The entry price and the entry-date sigma the original label used are held
    fixed. Bars past the ledger's fwd_caldays (out to MAXW_CALDAYS, which the
    original walk may also have visited) carry the last cumulative factor.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# G-trap (worktree PYTHONPATH): prove we are running THIS checkout's code.
assert os.path.abspath(__file__).startswith(_ROOT), "module path escaped project root"

from experiments._holdout import assert_no_holdout_leak, CUTOFF  # noqa: E402

# =============================================================================
# Locked constants
# =============================================================================
OUT_DIR = os.path.join(_ROOT, ".cache", "event_noise_decomp")
os.makedirs(OUT_DIR, exist_ok=True)

LEDGER_PATH = os.path.join(_ROOT, ".cache", "trend_ma_lattice", "ledger_v74_full.parquet")

# --- apex15 barrier (COPIED VERBATIM from experiments/trend_ma_lattice/mine.py:49-55)
APEX_UP, APEX_DN = 1.092, 2.548      # TP +30% / SL -70% in sigma units
# The ledger builder walked FOUR thresholds at once (apex + the generic WR15 pair). The
# walk's early-break (`not remaining_up and not remaining_dn and last_cd >= HOLD_W`) is
# therefore a function of ALL FOUR, so `fwd_caldays` only reproduces bit-exactly if the
# same four are requested. Carried here for exactly that reason.
GEN_UP, GEN_DN = 1.414, 3.536
HOLD_W = 15                          # hold window (compared against CALENDAR days in label_win_ev)
MAXW_CALDAYS = 20                    # forward-walk horizon
VOL_WINDOW, VOL_MIN = 60, 20         # realized-vol lookback (trading bars)
EV_WIN, EV_STOP, EV_EXPIRE = 0.30, -0.70, -0.40

# --- jump detection (PREREGISTRATION 1.1)
BETA_WIN = 120                       # trailing trading days ending t-1
MIN_BETA_BARS = 80                   # >= 80 valid bars else beta_unavailable
SIGMA_WIN = 120
MIN_SIGMA_BARS = 80
JUMP_RATE_WIN = 252
K_PRIMARY = 3.0
K_LADDER = (2.5, 3.0, 4.0)
EVENT_SHARE_THRESH = 0.5             # |jump_ret| >= 0.5 * |total_ret| => EVENT

# --- empirical trading-day grid (G50: never the static holiday table)
MIN_SYMS_TRADING_DAY = 50

# --- gating (trap 5: degenerate-bucket CR1 blowup)
MIN_CELL_CONTROL_SIDE = 20
BUCKET_SHARE_LO, BUCKET_SHARE_HI = 0.05, 0.95
MIN_N_BAND = 150                     # prereg B4 reporting floor (reported, not enforced)

# --- checklist (M5)
CHECK_COMPONENTS = ["trend", "bb", "rsi", "macd", "stoch", "technical_alignment"]
CHECK_THRESH_PRIMARY = 70
CHECK_THRESH_LADDER = (60, 70, 80)

# --- score bands (M3). A6: the 4-band ladder is DESCRIPTIVE ONLY (underpowered above
# 84 on this substrate); the PRIMARY band contrast is the fixed 2-band collapse.
SCORE_BANDS = [("75-79", 75, 80), ("80-84", 80, 85), ("85-89", 85, 90), ("90+", 90, 101)]
SCORE_BANDS_2 = [("75-79", 75, 80), ("80+", 80, 101)]

# --- A6 fixed pooled anchor (orchestrator's independent audit of the funded ledger).
# The pipeline MUST reproduce these to 4 dp before any subset split is trustworthy.
ANCHOR = dict(
    n=5854, wr=0.7009, ev=0.0208,
    bands={"75-79": 0.7010, "80-84": 0.6968, "85-89": 0.7065, "90+": 0.7340},
    band_n={"75-79": 4267, "80-84": 1217, "85-89": 276, "90+": 94},
)

# --- A5: pre-entry jump contamination of the SIGNAL
PRE_JUMP_WINS = (5, 10, 20)          # trading days ENDING AT (and including) the entry date
PRE_JUMP_PRIMARY = 10
TEXTURE_COLLINEAR_BAR = 0.7          # D5: |rho| >= 0.7 => abandon to the parked texture cell
PEAK_FAKEOUT_FEATURES = os.path.join(_ROOT, ".cache", "peak_fakeout", "features_v74_full.parquet")
# Candidate climactic-acceleration proxies in that frame (identified by name from its
# 55-column schema): F3 `parabolic` (run-up curvature ratio) and F4 `climax_day`.
TEXTURE_NUMERIC_COLS = ["parabolic", "climax_day"]
TEXTURE_BUCKET_COLS = ["b_F3_parabolic", "b_F4_climax_day"]

# --- bootstrap
N_BOOT = 1000
BOOT_SEED = 20260725

OHLC_START = "2019-01-01"
OHLC_START_YEAR, OHLC_END_YEAR = 2019, 2027
SCORES_START_YEAR, SCORES_END_YEAR = 2021, 2027

MOM_WIN = 20                         # trailing-20d momentum baseline (M4)

SMOKE_N_ALPHA = 45
SMOKE_N_YOUNG = 5


# =============================================================================
# CR1 machinery -- IMPORTED from peak_fakeout/mine.py (never reimplemented)
# =============================================================================
def _load_pf_mine():
    path = os.path.join(_ROOT, "experiments", "peak_fakeout", "mine.py")
    spec = importlib.util.spec_from_file_location("_end_pf_mine", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_end_pf_mine"] = mod
    spec.loader.exec_module(mod)
    for fn in ("clustered_cell_effect", "clustered_ols_effect", "clustered_logit_effect"):
        if not hasattr(mod, fn):
            raise ImportError(f"peak_fakeout/mine.py is missing {fn} -- CR1 machinery moved; STOP")
    return mod


_PF = None


def pf():
    global _PF
    if _PF is None:
        _PF = _load_pf_mine()
    return _PF


# =============================================================================
# Barrier logic -- COPIED VERBATIM from experiments/trend_ma_lattice/mine.py
# (realized_vol :152, forward_touch :173, label_win_ev :213)
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
    up/dn sigma threshold simultaneously."""
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


def outcome_label(t_up, t_dn, fwd_caldays, up_thr=APEX_UP, dn_thr=APEX_DN, W=HOLD_W):
    """{'win','stop','expire',None} -- the 3-way label M2b's confusion matrix needs.
    Derived from the SAME branch structure as label_win_ev (no independent logic)."""
    ttp = t_up.get(up_thr)
    tsl = t_dn.get(dn_thr)
    ttp_ok = ttp is not None and ttp <= W
    tsl_ok = tsl is not None and tsl <= W
    if ttp_ok and (not tsl_ok or ttp < tsl):
        return "win"
    if tsl_ok and (not ttp_ok or tsl <= ttp):
        return "stop"
    if fwd_caldays >= W:
        return "expire"
    return None


# =============================================================================
# Small stats helpers
# =============================================================================
def wilson_ci(x, n, z=1.96):
    if n <= 0:
        return (float("nan"), float("nan"))
    p = x / n
    d = 1.0 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(max(p * (1 - p) / n + z * z / (4 * n * n), 0.0))
    return ((c - s) / d, (c + s) / d)


def _ranks(a: np.ndarray) -> np.ndarray:
    """Average ranks (1..n), ties averaged."""
    n = len(a)
    order = np.argsort(a, kind="mergesort")
    sorted_a = a[order]
    ranks = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3:
        return float("nan")
    rx, ry = _ranks(x[m]), _ranks(y[m])
    sx, sy = rx.std(), ry.std()
    if sx <= 0 or sy <= 0:
        return float("nan")
    return float(np.mean((rx - rx.mean()) * (ry - ry.mean())) / (sx * sy))


def _finite_mask(*arrs):
    m = np.ones(len(arrs[0]), dtype=bool)
    for a in arrs:
        m &= np.isfinite(a)
    return m


def cr1_dummy(dummy, y, clusters, mask, binary, label, strict=True):
    """CR1 clustered effect of a 0/1 dummy on y, restricted to `mask`.

    binary=True  -> imported clustered_cell_effect (logit, OLS fallback)
    binary=False -> imported clustered_ols_effect on a linear-probability/EV fit,
                    with the SAME finX/valid-y masking and side-size gating.

    Trap 4/5: non-finite regressor rows are masked out BEFORE the fit, bucket
    share must be within [5%, 95%] and both sides >= MIN_CELL_CONTROL_SIDE, and
    a gated cell that comes back with a non-finite t raises loudly.
    """
    d = np.asarray(dummy, dtype=np.float64)
    yy = np.asarray(y, dtype=np.float64)
    m = np.asarray(mask, dtype=bool) & _finite_mask(d, yy)
    n_used = int(m.sum())
    n1 = int((d[m] > 0.5).sum())
    n0 = n_used - n1
    share = (n1 / n_used) if n_used else float("nan")
    out = dict(label=label, n_used=n_used, n_hi=n1, n_lo=n0, share=share,
               beta=float("nan"), t=float("nan"), n_clusters=0, method="", gated=False)
    if n_used == 0:
        out["method"] = "empty"
        return out
    if not (BUCKET_SHARE_LO <= share <= BUCKET_SHARE_HI):
        out["method"] = "gated_out_share"
        return out
    if n1 < MIN_CELL_CONTROL_SIDE or n0 < MIN_CELL_CONTROL_SIDE:
        out["method"] = "gated_out_side_n"
        return out
    X = np.column_stack([np.ones(len(d)), d])
    cl = np.asarray(clusters)
    if binary:
        res = pf().clustered_cell_effect(X, yy, cl, m, min_n_side=MIN_CELL_CONTROL_SIDE)
    else:
        finX = np.isfinite(X).all(axis=1)
        mm = m & finX & np.isfinite(yy)
        res = pf().clustered_ols_effect(X[mm], yy[mm], cl[mm])
        if res is None:
            res = dict(beta=float("nan"), t=float("nan"), n_used=int(mm.sum()),
                       n_clusters=0, method="infeasible")
        else:
            res = dict(res)
            res["n_used"] = int(mm.sum())
    out.update(beta=float(res.get("beta", float("nan"))), t=float(res.get("t", float("nan"))),
               n_clusters=int(res.get("n_clusters", 0)), method=res.get("method", ""),
               gated=True)
    if strict and not math.isfinite(out["t"]):
        raise RuntimeError(
            f"[CR1 CONTRACT VIOLATION] non-finite t on a GATED cell '{label}' "
            f"(n_used={n_used} n_hi={n1} n_lo={n0} method={out['method']}). "
            f"Trap 4: a NaN t must never reach a verdict."
        )
    return out


def cr1_continuous(x, y, clusters, mask, label, strict=True):
    """CR1 clustered slope of continuous x on y (used for Spearman-rank and
    monotonicity legs). Uses the IMPORTED clustered_ols_effect sandwich."""
    xx = np.asarray(x, dtype=np.float64)
    yy = np.asarray(y, dtype=np.float64)
    m = np.asarray(mask, dtype=bool) & _finite_mask(xx, yy)
    n_used = int(m.sum())
    out = dict(label=label, n_used=n_used, beta=float("nan"), t=float("nan"),
               n_clusters=0, method="", gated=False)
    if n_used < 2 * MIN_CELL_CONTROL_SIDE:
        out["method"] = "insufficient_n"
        return out
    if np.nanstd(xx[m]) <= 1e-12:
        out["method"] = "degenerate_x"
        return out
    X = np.column_stack([np.ones(n_used), xx[m]])
    res = pf().clustered_ols_effect(X, yy[m], np.asarray(clusters)[m])
    if res is None:
        out["method"] = "infeasible"
        if strict:
            raise RuntimeError(f"[CR1 CONTRACT VIOLATION] infeasible continuous fit '{label}' "
                               f"(n_used={n_used}). Trap 4.")
        return out
    out.update(beta=float(res["beta"]), t=float(res["t"]),
               n_clusters=int(res["n_clusters"]), method=res["method"], gated=True)
    if strict and not math.isfinite(out["t"]):
        raise RuntimeError(f"[CR1 CONTRACT VIOLATION] non-finite t on '{label}' (n={n_used}). Trap 4.")
    return out


def date_block_bootstrap(stat_fn, dates_arr, n_draws=N_BOOT, seed=BOOT_SEED):
    """Resample entry DATES (not rows) with replacement; returns (lo, hi, mean)
    of the 95% percentile interval of stat_fn(row_index_array)."""
    groups = defaultdict(list)
    for i, d in enumerate(dates_arr):
        groups[d].append(i)
    keys = list(groups.keys())
    idx_by_key = [np.asarray(groups[k], dtype=np.int64) for k in keys]
    G = len(keys)
    if G < 2:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_draws):
        pick = rng.integers(0, G, size=G)
        rows = np.concatenate([idx_by_key[p] for p in pick])
        v = stat_fn(rows)
        if v is not None and np.isfinite(v):
            vals.append(v)
    if not vals:
        return (float("nan"), float("nan"), float("nan"))
    v = np.asarray(vals)
    return (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)), float(v.mean()))


# =============================================================================
# Classification primitives (unit-testable)
# =============================================================================
def detect_jumps(r_idio, sigma, k):
    """Jump day iff |r_idio| >= k * sigma. NaN-safe (NaN -> not a jump)."""
    r = np.asarray(r_idio, dtype=np.float64)
    s = np.asarray(sigma, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        out = np.abs(r) >= (k * s)
    out &= np.isfinite(r) & np.isfinite(s)
    return out


def pre_jump_window(jump_row, idio_row, g0, n):
    """A5: (hit, signed_sum) over the n trading days ENDING AT (and INCLUDING) grid slot
    g0 -- i.e. slots [g0-n+1, g0]. Uses only information available at the entry close."""
    lo = max(0, g0 - n + 1)
    jm = jump_row[lo:g0 + 1]
    if jm.size == 0:
        return None, None
    hit = bool(np.any(jm))
    sgn = float(np.nansum(np.where(jm, idio_row[lo:g0 + 1], 0.0))) if hit else 0.0
    return hit, sgn


def pre_jump_cohort(hit, signed):
    if hit is None:
        return None
    if not hit:
        return "NOJUMP"
    return "POS" if signed > 0 else ("NEG" if signed < 0 else "ZERO")


def classify_trade(n_jumps, jump_ret, total_ret, thresh=EVENT_SHARE_THRESH):
    """EVENT / CYCLICAL / MIXED per PREREGISTRATION 1.2."""
    if n_jumps == 0:
        return "CYCLICAL"
    if abs(jump_ret) >= thresh * abs(total_ret):
        return "EVENT"
    return "MIXED"


def counterfactual_walk(dates, highs, lows, closes, i0, sigma, win_local_idx,
                        jump_flags, r_idio_vals, mu, maxw=MAXW_CALDAYS):
    """Re-walk the apex15 barrier on a path whose jump-day idiosyncratic returns
    are replaced by `mu` (see R3 in the module docstring).

    win_local_idx: observed-array indices of the bars inside W (ascending).
    jump_flags / r_idio_vals: aligned to win_local_idx.
    Returns (win, ev, label3, fwd_caldays).
    """
    entry = closes[i0]
    base_date = dates[i0]
    # extended slice: every bar the ORIGINAL walk could have visited
    hi_idx = i0
    j = i0 + 1
    while j < len(dates) and (dates[j] - base_date).days <= maxw:
        hi_idx = j
        j += 1
    n_slice = hi_idx - i0 + 1
    H = np.array(highs[i0:hi_idx + 1], dtype=np.float64)
    L = np.array(lows[i0:hi_idx + 1], dtype=np.float64)
    D = list(dates[i0:hi_idx + 1])
    adj = np.ones(n_slice, dtype=np.float64)
    cum = 0.0
    jset = {}
    for k, gi in enumerate(win_local_idx):
        if jump_flags[k]:
            jset[gi] = mu - r_idio_vals[k]
    for local in range(1, n_slice):
        obs_i = i0 + local
        if obs_i in jset:
            cum += jset[obs_i]
        adj[local] = math.exp(cum)
    H = H * adj
    L = L * adj
    t_up, t_dn, fwd = forward_touch(H, L, D, 0, entry, sigma,
                                     (APEX_UP, GEN_UP), (APEX_DN, GEN_DN), maxw)
    win, ev = label_win_ev(t_up, t_dn, fwd, APEX_UP, APEX_DN, ev=True)
    lbl = outcome_label(t_up, t_dn, fwd)
    return win, ev, lbl, fwd


# =============================================================================
# Empirical trading-day grid (G50)
# =============================================================================
def build_trading_grid(ohlc: pl.DataFrame, min_syms: int):
    counts = (ohlc.group_by("date")
                  .agg(pl.col("symbol").n_unique().alias("n_syms"))
                  .sort("date"))
    keep = counts.filter(pl.col("n_syms") >= min_syms)
    grid = keep["date"].to_list()
    dropped = counts.height - keep.height
    return grid, dropped, counts


def calendar_overlap_check(grid_dates):
    """DIAGNOSTIC ONLY (never a source): report disagreement between the empirical
    grid and the static NYSE_HOLIDAYS table, which is known-wrong in-window."""
    try:
        from database.utils.trading_calendar import is_trading_day
    except Exception as e:  # pragma: no cover
        return dict(available=False, error=str(e))
    gset = set(grid_dates)
    lo, hi = min(grid_dates), max(grid_dates)
    static_only, empirical_only = [], []
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            st = bool(is_trading_day(d))
            em = d in gset
            if st and not em:
                static_only.append(d.isoformat())
            elif em and not st:
                empirical_only.append(d.isoformat())
        d += timedelta(days=1)
    return dict(available=True, n_static_only=len(static_only), n_empirical_only=len(empirical_only),
                static_only_sample=static_only[:12], empirical_only_sample=empirical_only[:12])


# =============================================================================
# Data loading
# =============================================================================
def load_ledger():
    df = pl.read_parquet(LEDGER_PATH)
    if "_cached_symbols" in df.columns:
        df = df.drop("_cached_symbols")
    df = df.with_columns((pl.col("date") > CUTOFF).alias("post_cutoff"))
    n_post = int(df["post_cutoff"].sum())
    print(f"[ledger] {df.height} rows  {df['date'].min()}..{df['date'].max()}  "
          f"post_cutoff_tagged={n_post} (cutoff={CUTOFF.isoformat()})", flush=True)
    pre = df.filter(~pl.col("post_cutoff"))
    assert_no_holdout_leak(pre, context="event_noise_decomp verdict rows")
    return df


def anchor_check(ledger_full: pl.DataFrame) -> dict:
    """A6 harness-validation anchor. Computed on the COMPLETE funded ledger (never the
    smoke subset), against the orchestrator's independently audited numbers."""
    d = ledger_full.filter(pl.col("apex_win").is_not_null())
    got = dict(n=d.height,
               wr=round(float(d["apex_win"].mean()), 4),
               ev=round(float(d["apex_ev"].mean()), 4),
               bands={}, band_n={})
    for nm, lo, hi in SCORE_BANDS:
        b = d.filter((pl.col("overall") >= lo) & (pl.col("overall") < hi))
        got["bands"][nm] = round(float(b["apex_win"].mean()), 4) if b.height else float("nan")
        got["band_n"][nm] = b.height
    diffs = []
    if got["n"] != ANCHOR["n"]:
        diffs.append(f"N {got['n']} != {ANCHOR['n']}")
    for f in ("wr", "ev"):
        if got[f] != ANCHOR[f]:
            diffs.append(f"{f} {got[f]} != {ANCHOR[f]}")
    for nm in ANCHOR["bands"]:
        if got["bands"].get(nm) != ANCHOR["bands"][nm]:
            diffs.append(f"band[{nm}] WR {got['bands'].get(nm)} != {ANCHOR['bands'][nm]}")
        if got["band_n"].get(nm) != ANCHOR["band_n"][nm]:
            diffs.append(f"band[{nm}] N {got['band_n'].get(nm)} != {ANCHOR['band_n'][nm]}")
    ok = not diffs
    print("[ANCHOR A6] pooled baseline on the FULL funded ledger:")
    print(f"[ANCHOR A6]   N={got['n']} (bar {ANCHOR['n']})  WR={got['wr']} (bar {ANCHOR['wr']})  "
          f"EV={got['ev']:+} (bar {ANCHOR['ev']:+})")
    print(f"[ANCHOR A6]   band WR " + " / ".join(f"{nm}={got['bands'][nm]}(N={got['band_n'][nm]})"
                                                  for nm, _, _ in SCORE_BANDS))
    print(f"[ANCHOR A6]   bar     " + " / ".join(f"{nm}={ANCHOR['bands'][nm]}(N={ANCHOR['band_n'][nm]})"
                                                  for nm, _, _ in SCORE_BANDS))
    if ok:
        print("[ANCHOR A6]   MATCH (exact to 4 dp) -- pooled baseline reproduced.", flush=True)
    else:
        print("[ANCHOR A6]   *** MISMATCH -- THE PIPELINE DOES NOT REPRODUCE THE AUDITED "
              "BASELINE ***", flush=True)
        for s in diffs:
            print(f"[ANCHOR A6]     DISAGREEMENT: {s}", flush=True)
    return dict(ok=ok, got=got, expected=ANCHOR, disagreements=diffs)


def _ohlc_frame(rows) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "open": [float(r[2]) if r[2] is not None else float("nan") for r in rows],
            "high": [float(r[3]) if r[3] is not None else float("nan") for r in rows],
            "low": [float(r[4]) if r[4] is not None else float("nan") for r in rows],
            "close": [float(r[5]) if r[5] is not None else float("nan") for r in rows],
            "volume": [float(r[6]) if r[6] is not None else float("nan") for r in rows],
        },
        infer_schema_length=None,   # trap 2
    )


def load_ohlc_full(force=False) -> pl.DataFrame:
    from database.bulk_cache import materialize_polars, chunked_query_by_year

    def _build():
        frames = []
        for yr in range(OHLC_START_YEAR, OHLC_END_YEAR):
            rows = chunked_query_by_year(
                "SELECT symbol, date, open, high, low, close, volume FROM price_history "
                "WHERE date BETWEEN '{y_start}' AND '{y_end}' AND date >= '{min_date}'",
                start_year=yr, end_year=yr + 1, min_date=OHLC_START, verbose=True,
            )
            frames.append(_ohlc_frame(rows))
            del rows
        return pl.concat(frames).sort(["symbol", "date"])

    path = materialize_polars("event_noise_ohlc_2019", _build, force=force)
    df = pl.read_parquet(path)
    print(f"[ohlc] {df.height:,} bars  {df['symbol'].n_unique()} symbols  "
          f"{df['date'].min()}..{df['date'].max()}  <- {path}", flush=True)
    return df


def load_ohlc_symbols(symbols) -> pl.DataFrame:
    """Smoke path: direct narrow pull (no giant cache)."""
    from database.bulk_cache import chunked_query_by_year
    syms = ",".join("'%s'" % s.replace("'", "") for s in sorted(set(symbols)))
    rows = chunked_query_by_year(
        "SELECT symbol, date, open, high, low, close, volume FROM price_history "
        "WHERE symbol IN ({syms}) AND date BETWEEN '{y_start}' AND '{y_end}' "
        "AND date >= '{min_date}'",
        start_year=OHLC_START_YEAR, end_year=OHLC_END_YEAR, min_date=OHLC_START,
        syms=syms, verbose=False,
    )
    df = _ohlc_frame(rows).sort(["symbol", "date"])
    print(f"[ohlc] {df.height:,} bars  {df['symbol'].n_unique()} symbols "
          f"{df['date'].min()}..{df['date'].max()} (direct smoke pull)", flush=True)
    return df


def load_components(version_id: int, force=False) -> pl.DataFrame:
    """6 stored ensemble members for the 75+ v{vid} rows. Built with a String
    `date` (the weather_components/build_ledger.py pattern) and CAST to pl.Date
    here before any join (the documented dtype trap)."""
    from database.bulk_cache import materialize_polars, chunked_query_by_year

    def _build():
        rows = chunked_query_by_year(
            "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, technical_alignment "
            "FROM scores WHERE version_id={vid} AND overall >= 75 "
            "AND date BETWEEN '{y_start}' AND '{y_end}'",
            start_year=SCORES_START_YEAR, end_year=SCORES_END_YEAR, vid=version_id,
            timeout_ms=300_000, verbose=True,
        )

        def fnum(v):
            return float(v) if v is not None else float("nan")

        return pl.DataFrame(
            {
                "symbol": [r[0] for r in rows],
                "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
                "c_overall": [fnum(r[2]) for r in rows],
                "c_trend": [fnum(r[3]) for r in rows],
                "c_bb": [fnum(r[4]) for r in rows],
                "c_rsi": [fnum(r[5]) for r in rows],
                "c_macd": [fnum(r[6]) for r in rows],
                "c_stoch": [fnum(r[7]) for r in rows],
                "c_technical_alignment": [fnum(r[8]) for r in rows],
            },
            infer_schema_length=None,   # trap 2
        )

    path = materialize_polars(f"event_noise_components_v{version_id}", _build, force=force)
    df = pl.read_parquet(path)
    # dtype trap: String isoformat -> pl.Date BEFORE the join
    if df.schema["date"] == pl.Utf8:
        df = df.with_columns(pl.col("date").str.to_date())
    # trap 3: NaN != null -- single choke point
    num_cols = [c for c in df.columns if df.schema[c] in (pl.Float64, pl.Float32)]
    df = df.with_columns([pl.col(c).fill_nan(None) for c in num_cols])
    dup = df.height - df.select(["symbol", "date"]).unique().height
    if dup:
        raise AssertionError(f"[components] (symbol,date) is NOT unique: {dup} duplicate keys -- STOP")
    print(f"[components] {df.height:,} rows, (symbol,date) unique, date dtype={df.schema['date']}",
          flush=True)
    return df


def load_earnings(symbols):
    """Ghost-filtered effective_date list per symbol (equivalent to
    core.py:_filter_ghost_earnings). NOT via _load_earnings_by_symbol /
    _days_to_next_earnings_for -- those are gated on EARN_BOOST_ENABLED (False
    in v74) and would silently return empty."""
    from database.models.core import EarningsDate
    BATCH = 150
    raw = defaultdict(list)
    syms = sorted(set(symbols))
    t0 = time.time()
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        q = (EarningsDate
             .select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time,
                     EarningsDate.effective_date, EarningsDate.reported_eps)
             .where(EarningsDate.symbol.in_(batch))
             .tuples())
        for sym, d, ct, eff, rep in q:
            raw[sym].append((d, ct, eff, rep))
    n_raw = sum(len(v) for v in raw.values())
    out = {}
    n_ghost = 0
    for sym, rows in raw.items():
        kept = _filter_ghost_earnings_local(rows)
        n_ghost += len(rows) - len(kept)
        effs = sorted({r[2] for r in kept if r[2] is not None})
        out[sym] = effs
    print(f"[earnings] {n_raw} raw rows across {len(raw)}/{len(syms)} symbols; "
          f"ghost-filtered {n_ghost}; usable effective_dates="
          f"{sum(len(v) for v in out.values())} ({time.time()-t0:.1f}s)", flush=True)
    return out


def _filter_ghost_earnings_local(rows):
    """Equivalent of database/models/core.py:_filter_ghost_earnings (copied, not
    imported: the module-level import chain there pulls scoring config)."""
    WINDOW = 30
    real_eff = [r[2] for r in rows if r[3] is not None and r[2] is not None]
    if not real_eff:
        return rows
    out = []
    for r in rows:
        d, ct, eff, rep = r
        if rep is not None or eff is None:
            out.append(r)
            continue
        if any(abs((eff - re).days) <= WINDOW for re in real_eff):
            continue
        out.append(r)
    return out


def load_earnings_jumps(symbols):
    from database.models.core import EarningsJump
    BATCH = 150
    out = defaultdict(list)
    syms = sorted(set(symbols))
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        q = (EarningsJump
             .select(EarningsJump.symbol, EarningsJump.earnings_date, EarningsJump.jump_pct)
             .where(EarningsJump.symbol.in_(batch))
             .tuples())
        for sym, d, jp in q:
            if d is not None and jp is not None:
                out[sym].append((d, float(jp)))
    for sym in out:
        out[sym].sort(key=lambda r: r[0])
    print(f"[earnings_jumps] {sum(len(v) for v in out.values())} rows across "
          f"{len(out)}/{len(syms)} symbols", flush=True)
    return out


# =============================================================================
# Point-in-time panel: beta / sigma_idio / r_idio / jumps
# =============================================================================
class Panel:
    __slots__ = ("symbols", "sym_idx", "grid", "grid_idx", "beta", "r_idio",
                 "sigma_a", "sigma_b", "jumps", "n_bars_win", "rs", "rm")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _prefix(a: np.ndarray) -> np.ndarray:
    """Prefix sums with a leading zero column: P[:, k] = sum of a[:, :k]."""
    S, T = a.shape
    P = np.zeros((S, T + 1), dtype=np.float64)
    np.cumsum(a, axis=1, out=P[:, 1:])
    return P


def _window_sum(P: np.ndarray, win: int) -> np.ndarray:
    """Sum over grid window [t-win, t-1] (ENDS AT t-1: no same-day info)."""
    T = P.shape[1] - 1
    t = np.arange(T)
    a = np.maximum(0, t - win)
    return P[:, t] - P[:, a]


def build_panel(ohlc: pl.DataFrame, grid_dates, market_symbol="SPY") -> Panel:
    t0 = time.time()
    grid_idx = {d: i for i, d in enumerate(grid_dates)}
    symbols = sorted(ohlc["symbol"].unique().to_list())
    if market_symbol not in symbols:
        raise SystemExit(f"[FATAL] market proxy {market_symbol} is ABSENT from the OHLC pull "
                         f"-- refusing to substitute. STOP.")
    sym_idx = {s: i for i, s in enumerate(symbols)}
    S, T = len(symbols), len(grid_dates)
    R = np.full((S, T), np.nan, dtype=np.float64)

    for sym, g in ohlc.sort(["symbol", "date"]).group_by("symbol", maintain_order=True):
        sym = sym[0] if isinstance(sym, tuple) else sym
        si = sym_idx[sym]
        ds = g["date"].to_list()
        cl = g["close"].to_numpy().astype(np.float64)
        if len(cl) < 2:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            r = np.full(len(cl), np.nan)
            r[1:] = np.log(cl[1:] / cl[:-1])
        for k in range(1, len(ds)):
            gi = grid_idx.get(ds[k])
            if gi is not None and np.isfinite(r[k]):
                R[si, gi] = r[k]

    mi = sym_idx[market_symbol]
    rm = R[mi, :].copy()                       # (T,)
    rs = R                                     # (S,T)

    valid = np.isfinite(rs) & np.isfinite(rm)[None, :]
    x = np.where(valid, rm[None, :], 0.0)
    y = np.where(valid, rs, 0.0)
    Pn = _prefix(valid.astype(np.float64))
    Px = _prefix(x)
    Py = _prefix(y)
    Pxx = _prefix(x * x)
    Pxy = _prefix(x * y)
    Pyy = _prefix(y * y)

    n = _window_sum(Pn, BETA_WIN)
    Sx = _window_sum(Px, BETA_WIN)
    Sy = _window_sum(Py, BETA_WIN)
    Sxx = _window_sum(Pxx, BETA_WIN)
    Sxy = _window_sum(Pxy, BETA_WIN)
    Syy = _window_sum(Pyy, BETA_WIN)

    with np.errstate(invalid="ignore", divide="ignore"):
        den = n * Sxx - Sx * Sx
        ok = (n >= MIN_BETA_BARS) & (np.abs(den) > 1e-18)
        beta = np.where(ok, (n * Sxy - Sx * Sy) / np.where(np.abs(den) > 1e-18, den, 1.0), np.nan)
        # reading R1(b): residual stdev of THIS regression (robustness diagnostic)
        Sxy_c = Sxy - np.where(n > 0, Sx * Sy / np.where(n > 0, n, 1.0), 0.0)
        Syy_c = Syy - np.where(n > 0, Sy * Sy / np.where(n > 0, n, 1.0), 0.0)
        ssr = Syy_c - beta * Sxy_c
        sigma_b = np.where(ok & (n > 2), np.sqrt(np.maximum(ssr, 0.0) / np.maximum(n - 2, 1)), np.nan)

    with np.errstate(invalid="ignore"):
        r_idio = rs - beta * rm[None, :]
    r_idio = np.where(np.isfinite(rs) & np.isfinite(beta), r_idio, np.nan)

    # reading R1(a) PRIMARY: stdev of the r_idio SERIES over [t-120, t-1]
    v2 = np.isfinite(r_idio)
    z2 = np.where(v2, r_idio, 0.0)
    Pn2 = _prefix(v2.astype(np.float64))
    Pz = _prefix(z2)
    Pz2 = _prefix(z2 * z2)
    n2 = _window_sum(Pn2, SIGMA_WIN)
    S1 = _window_sum(Pz, SIGMA_WIN)
    S2 = _window_sum(Pz2, SIGMA_WIN)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean2 = np.where(n2 > 0, S1 / np.where(n2 > 0, n2, 1.0), np.nan)
        var2 = (S2 - n2 * mean2 * mean2) / np.maximum(n2 - 1, 1)
        sigma_a = np.where(n2 >= MIN_SIGMA_BARS, np.sqrt(np.maximum(var2, 0.0)), np.nan)

    jumps = {k: detect_jumps(r_idio, sigma_a, k) for k in K_LADDER}

    print(f"[panel] S={S} T={T} built in {time.time()-t0:.1f}s; "
          f"beta coverage={np.isfinite(beta).mean()*100:.1f}% of cells, "
          f"sigma_a coverage={np.isfinite(sigma_a).mean()*100:.1f}%", flush=True)
    return Panel(symbols=symbols, sym_idx=sym_idx, grid=grid_dates, grid_idx=grid_idx,
                 beta=beta, r_idio=r_idio, sigma_a=sigma_a, sigma_b=sigma_b,
                 jumps=jumps, n_bars_win=n, rs=rs, rm=rm)


def build_obs_arrays(ohlc: pl.DataFrame):
    """Per-symbol observed bars (ALL bars, not grid-restricted) -- the exact
    substrate the ledger's own forward walk used."""
    out = {}
    for sym, g in ohlc.sort(["symbol", "date"]).group_by("symbol", maintain_order=True):
        sym = sym[0] if isinstance(sym, tuple) else sym
        dates = g["date"].to_list()
        closes = g["close"].to_numpy().astype(np.float64)
        highs = g["high"].to_numpy().astype(np.float64)
        lows = g["low"].to_numpy().astype(np.float64)
        out[sym] = dict(dates=dates, closes=closes, highs=highs, lows=lows,
                        idx={d: i for i, d in enumerate(dates)},
                        sigma=realized_vol(closes))
    return out


# =============================================================================
# Per-trade classification
# =============================================================================
def classify_ledger(ledger: pl.DataFrame, panel: Panel, obs: dict,
                    earnings: dict, ejumps: dict) -> pl.DataFrame:
    t0 = time.time()
    recs = []
    cov = defaultdict(int)
    for row in ledger.iter_rows(named=True):
        sym, d = row["symbol"], row["date"]
        rec = dict(row)
        rec["coverage"] = "ok"
        rec["beta_unavailable"] = False
        rec["grid_gap"] = False
        for k in K_LADDER:
            rec[f"n_jumps_k{k}"] = None
            rec[f"jump_ret_k{k}"] = None
            rec[f"subset_k{k}"] = None
        rec["total_ret"] = None
        rec["revalid_win"] = None
        rec["revalid_fwd"] = None
        rec["revalid_gen_win"] = None
        rec["cf_win"] = None
        rec["cf_ev"] = None
        rec["cf_label"] = None
        rec["orig_label"] = None
        rec["mom20"] = None
        rec["jump_rate_252"] = None
        rec["d_earn_in_window"] = None
        rec["prior_earn_jump_pct"] = None
        rec["n_nonjump_days"] = None
        rec["mu_drift"] = None
        for n_pre in PRE_JUMP_WINS:
            rec[f"pre_jump_{n_pre}"] = None
            rec[f"pre_jump_signed_ret_{n_pre}"] = None
            rec[f"pre_jump_cohort_{n_pre}"] = None

        o = obs.get(sym)
        if o is None:
            rec["coverage"] = "no_ohlc"
            cov["no_ohlc"] += 1
            recs.append(rec)
            continue
        i0 = o["idx"].get(d)
        if i0 is None:
            rec["coverage"] = "no_bar_at_entry"
            cov["no_bar_at_entry"] += 1
            recs.append(rec)
            continue
        sigma = o["sigma"][i0]
        entry = o["closes"][i0]
        if not (np.isfinite(sigma) and sigma > 0 and np.isfinite(entry) and entry > 0):
            rec["coverage"] = "no_sigma"
            cov["no_sigma"] += 1
            recs.append(rec)
            continue

        si = panel.sym_idx.get(sym)
        g0 = panel.grid_idx.get(d)

        # --- A5 PRE-ENTRY features (data up to AND INCLUDING the entry close only).
        # Window = the n trading days ENDING AT the entry date, i.e. grid slots
        # [g0-n+1, g0] inclusive. r_idio(t) uses beta from [t-120,t-1] and the day-t
        # return (known at the close); sigma(t) uses [t-120,t-1]. Both are PIT.
        if si is not None and g0 is not None:
            jrow = panel.jumps[K_PRIMARY][si]
            irow = panel.r_idio[si]
            for n_pre in PRE_JUMP_WINS:
                hit, sgn = pre_jump_window(jrow, irow, g0, n_pre)
                if hit is None:
                    continue
                rec[f"pre_jump_{n_pre}"] = hit
                rec[f"pre_jump_signed_ret_{n_pre}"] = sgn
                rec[f"pre_jump_cohort_{n_pre}"] = pre_jump_cohort(hit, sgn)
            lo_r = max(0, g0 - JUMP_RATE_WIN)
            rec["jump_rate_252"] = int(jrow[lo_r:g0].sum())
        if i0 >= MOM_WIN:
            with np.errstate(invalid="ignore", divide="ignore"):
                _m = float(np.log(entry / o["closes"][i0 - MOM_WIN]))
            rec["mom20"] = _m if np.isfinite(_m) else None

        # --- validation: reproduce the ledger's stored apex_win on the UNMODIFIED path
        t_up, t_dn, fwd_re = forward_touch(o["highs"], o["lows"], o["dates"], i0, entry, sigma,
                                            (APEX_UP, GEN_UP), (APEX_DN, GEN_DN), MAXW_CALDAYS)
        win_re, ev_re = label_win_ev(t_up, t_dn, fwd_re, APEX_UP, APEX_DN, ev=True)
        rec["revalid_win"] = win_re
        rec["revalid_fwd"] = fwd_re
        rec["revalid_gen_win"] = label_win_ev(t_up, t_dn, fwd_re, GEN_UP, GEN_DN, ev=False)
        rec["orig_label"] = outcome_label(t_up, t_dn, fwd_re)

        fwd_cd = row["fwd_caldays"]
        if fwd_cd is None:
            rec["coverage"] = "no_fwd_caldays"
            cov["no_fwd_caldays"] += 1
            recs.append(rec)
            continue

        # --- holding window W = [entry+1, entry+fwd_caldays] (prereg A1)
        w_idx = []
        j = i0 + 1
        while j < len(o["dates"]) and (o["dates"][j] - d).days <= fwd_cd:
            w_idx.append(j)
            j += 1
        if not w_idx:
            rec["coverage"] = "empty_window"
            cov["empty_window"] += 1
            recs.append(rec)
            continue

        gcols, missing_grid, bad_beta = [], 0, 0
        for jj in w_idx:
            gi = panel.grid_idx.get(o["dates"][jj])
            if gi is None:
                missing_grid += 1
                gcols.append(None)
                continue
            if si is None or not np.isfinite(panel.r_idio[si, gi]) or not np.isfinite(panel.sigma_a[si, gi]):
                bad_beta += 1
            gcols.append(gi)
        if missing_grid:
            rec["grid_gap"] = True
        if bad_beta or missing_grid:
            rec["beta_unavailable"] = True
            rec["coverage"] = "beta_unavailable"
            cov["beta_unavailable"] += 1
            recs.append(rec)
            continue

        r_idio_w = np.array([panel.r_idio[si, gi] for gi in gcols], dtype=np.float64)
        last = w_idx[-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            total_ret = float(np.log(o["closes"][last] / entry))
        rec["total_ret"] = total_ret if np.isfinite(total_ret) else None
        if rec["total_ret"] is None:
            rec["coverage"] = "bad_total_ret"
            cov["bad_total_ret"] += 1
            recs.append(rec)
            continue

        for k in K_LADDER:
            jf = np.array([bool(panel.jumps[k][si, gi]) for gi in gcols], dtype=bool)
            jret = float(r_idio_w[jf].sum()) if jf.any() else 0.0
            rec[f"n_jumps_k{k}"] = int(jf.sum())
            rec[f"jump_ret_k{k}"] = jret
            rec[f"subset_k{k}"] = classify_trade(int(jf.sum()), jret, total_ret)
            if k == K_PRIMARY:
                jf_primary = jf

        # --- M2b counterfactual (all K, headline at K_PRIMARY)
        for k in K_LADDER:
            jf = np.array([bool(panel.jumps[k][si, gi]) for gi in gcols], dtype=bool)
            nonjump = r_idio_w[~jf]
            mu = float(nonjump.mean()) if nonjump.size else 0.0
            cf_win, cf_ev, cf_lbl, cf_fwd = counterfactual_walk(
                o["dates"], o["highs"], o["lows"], o["closes"], i0, sigma,
                w_idx, jf, r_idio_w, mu)
            rec[f"cf_win_k{k}"] = cf_win
            rec[f"cf_ev_k{k}"] = cf_ev
            rec[f"cf_label_k{k}"] = cf_lbl
            if k == K_PRIMARY:
                rec["cf_win"] = cf_win
                rec["cf_ev"] = cf_ev
                rec["cf_label"] = cf_lbl
                rec["n_nonjump_days"] = int((~jf).sum())
                rec["mu_drift"] = mu

        # --- ex-ante earnings features (entry close or earlier only; pit_unverified)
        effs = earnings.get(sym) or []
        lo_d, hi_d = d + timedelta(days=1), d + timedelta(days=int(fwd_cd))
        rec["d_earn_in_window"] = bool(any(lo_d <= e <= hi_d for e in effs))
        ej = ejumps.get(sym) or []
        prior = [jp for (ed, jp) in ej if ed < d]
        rec["prior_earn_jump_pct"] = float(prior[-1]) if prior else None

        recs.append(rec)

    df = pl.DataFrame(recs, infer_schema_length=None)   # trap 2
    num_cols = [c for c in df.columns if df.schema[c] in (pl.Float64, pl.Float32)]
    df = df.with_columns([pl.col(c).fill_nan(None) for c in num_cols])   # trap 3
    print(f"[classify] {df.height} rows in {time.time()-t0:.1f}s; coverage drops: "
          f"{dict(cov) if cov else 'none'}", flush=True)
    return df


# =============================================================================
# Metrics
# =============================================================================
def _ev_wr(win, ev, mask):
    m = mask & np.isfinite(win) & np.isfinite(ev)
    n = int(m.sum())
    if n == 0:
        return dict(n=0, wins=0, wr=float("nan"), ev=float("nan"),
                    wr_lo=float("nan"), wr_hi=float("nan"))
    x = int(np.nansum(win[m]))
    lo, hi = wilson_ci(x, n)
    return dict(n=n, wins=x, wr=100.0 * x / n, ev=float(np.mean(ev[m])),
                wr_lo=100.0 * lo, wr_hi=100.0 * hi)


def _pearson(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3 or np.nanstd(a[m]) <= 0 or np.nanstd(b[m]) <= 0:
        return float("nan"), int(m.sum())
    c = np.corrcoef(a[m], b[m])
    return (float(c[0, 1]) if np.isfinite(c[0, 1]) else float("nan")), int(m.sum())


def _texture_collinearity(usable: pl.DataFrame, _unused=None) -> dict:
    """A5/D5: |rho| between pre_jump_{primary} and the PARKED peak_fakeout climactic-
    acceleration texture proxy. Columns are joined in main() with a `pf_` prefix; if the
    frame or an identifiable acceleration/climax feature is absent we say so and skip
    rather than guessing at a substitute."""
    col = f"pre_jump_{PRE_JUMP_PRIMARY}"
    out = dict(bar=TEXTURE_COLLINEAR_BAR, primary_feature=col, available=False, pairs={})
    if col not in usable.columns:
        out["note"] = f"{col} absent"
        return out
    pj = np.array([1.0 if v else (0.0 if v is not None else np.nan)
                   for v in usable[col].to_list()])
    found_any = False
    for c in TEXTURE_NUMERIC_COLS:
        pc = f"pf_{c}"
        if pc not in usable.columns:
            out["pairs"][c] = dict(status="absent")
            continue
        found_any = True
        v = usable[pc].cast(pl.Float64).to_numpy()
        r, n = _pearson(pj, v)
        rs = spearman(pj, v)
        out["pairs"][c] = dict(status="ok", n=n, pearson=r, spearman=rs,
                               exceeds_bar=bool(np.isfinite(r) and abs(r) >= TEXTURE_COLLINEAR_BAR))
    # composite parked-texture proxy: top-tercile parabolic OR top-tercile climax_day
    bcols = [f"pf_{c}" for c in TEXTURE_BUCKET_COLS if f"pf_{c}" in usable.columns]
    if bcols:
        found_any = True
        tex = np.zeros(usable.height, dtype=float)
        seen = np.zeros(usable.height, dtype=bool)
        for bc in bcols:
            vals = usable[bc].to_list()
            for i, v in enumerate(vals):
                if v is not None:
                    seen[i] = True
                    if v == "T3_high":
                        tex[i] = 1.0
        tex = np.where(seen, tex, np.nan)
        r, n = _pearson(pj, tex)
        out["pairs"]["texture_T3high_parabolic_OR_climax"] = dict(
            status="ok", n=n, phi=r, exceeds_bar=bool(np.isfinite(r) and abs(r) >= TEXTURE_COLLINEAR_BAR),
            cols=bcols)
    out["available"] = found_any
    if not found_any:
        out["note"] = ("peak_fakeout features frame not joined (or no acceleration/climax column "
                       "identifiable by name) -- D5 SKIPPED, not guessed")
    return out


def compute_metrics(cdf: pl.DataFrame, k_primary=K_PRIMARY) -> dict:
    """All verdict statistics, computed on PRE-CUTOFF, coverage-ok rows only."""
    res = {}
    usable = cdf.filter((pl.col("coverage") == "ok") & (~pl.col("post_cutoff")))
    res["n_ledger"] = cdf.height
    res["n_post_cutoff_excluded"] = int(cdf["post_cutoff"].sum())
    res["n_usable"] = usable.height
    vc = cdf["coverage"].value_counts().sort("coverage")
    res["coverage_breakdown"] = {str(r["coverage"]): int(r["count"]) for r in vc.iter_rows(named=True)}
    if usable.height == 0:
        res["EMPTY"] = True
        return res

    win = usable["apex_win"].cast(pl.Float64).to_numpy()
    ev = usable["apex_ev"].cast(pl.Float64).to_numpy()
    overall = usable["overall"].cast(pl.Float64).to_numpy()
    dates = np.array([d for d in usable["date"].to_list()])
    years = np.array([d.year for d in usable["date"].to_list()])
    clusters = np.array([d.toordinal() for d in usable["date"].to_list()])
    subset = np.array(usable[f"subset_k{k_primary}"].to_list(), dtype=object)
    jump_ret = usable[f"jump_ret_k{k_primary}"].cast(pl.Float64).to_numpy()
    total_ret = usable["total_ret"].cast(pl.Float64).to_numpy()

    SUBSETS = ["ALL", "EVENT", "CYCLICAL", "MIXED"]

    def smask(name):
        return np.ones(len(win), dtype=bool) if name == "ALL" else (subset == name)

    # ---------------- M1 ----------------
    res["M1"] = {}
    for s in SUBSETS:
        res["M1"][s] = _ev_wr(win, ev, smask(s))
        res["M1"][s]["share"] = float(smask(s).mean())
    res["M1_by_year"] = {}
    for s in SUBSETS:
        res["M1_by_year"][s] = {}
        for y in sorted(set(years.tolist())):
            res["M1_by_year"][s][str(y)] = _ev_wr(win, ev, smask(s) & (years == y))

    # ---------------- M2 ----------------
    def nu_var(rows):
        jr, tr = jump_ret[rows], total_ret[rows]
        m = np.isfinite(jr) & np.isfinite(tr)
        if m.sum() < 3:
            return float("nan")
        vt = np.var(tr[m])
        return float(np.var(jr[m]) / vt) if vt > 0 else float("nan")

    def r2_jump_on_total(rows):
        jr, tr = jump_ret[rows], total_ret[rows]
        m = np.isfinite(jr) & np.isfinite(tr)
        if m.sum() < 3:
            return float("nan")
        c = np.corrcoef(jr[m], tr[m])
        return float(c[0, 1] ** 2) if np.isfinite(c[0, 1]) else float("nan")

    def event_share(rows):
        return float((subset[rows] == "EVENT").mean())

    allrows = np.arange(len(win))
    m2 = dict(nu_var=nu_var(allrows), r2=r2_jump_on_total(allrows), event_share=event_share(allrows))
    for nm, fn in (("nu_var", nu_var), ("r2", r2_jump_on_total), ("event_share", event_share)):
        lo, hi, mean = date_block_bootstrap(fn, dates)
        m2[nm + "_ci"] = [lo, hi]
        m2[nm + "_boot_mean"] = mean
    m2["by_year"] = {}
    for y in sorted(set(years.tolist())):
        rows = np.where(years == y)[0]
        m2["by_year"][str(y)] = dict(n=int(len(rows)), nu_var=nu_var(rows),
                                     r2=r2_jump_on_total(rows), event_share=event_share(rows))
    res["M2"] = m2

    # ---------------- M2b: nu_flip ----------------
    res["M2b"] = {}
    for k in K_LADDER:
        ol = np.array(usable["orig_label"].to_list(), dtype=object)
        cl_ = np.array(usable[f"cf_label_k{k}"].to_list(), dtype=object)
        valid = np.array([a is not None and b is not None for a, b in zip(ol, cl_)])
        flips = np.array([a != b for a, b in zip(ol, cl_)]) & valid
        n_valid = int(valid.sum())
        nu_flip = float(flips.sum() / n_valid) if n_valid else float("nan")

        def _flip(rows, _flips=flips, _valid=valid):
            v = _valid[rows]
            if v.sum() == 0:
                return float("nan")
            return float(_flips[rows][v].mean())

        lo, hi, bmean = date_block_bootstrap(_flip, dates)
        conf = defaultdict(int)
        for a, b in zip(ol, cl_):
            if a is not None and b is not None:
                conf[f"{a}->{b}"] += 1
        base = _ev_wr(win, ev, np.ones(len(win), dtype=bool))
        n_eff = base["n"] * (1.0 - nu_flip) if np.isfinite(nu_flip) else float("nan")
        wl, wh = (wilson_ci(base["wins"] * (n_eff / base["n"]), n_eff)
                  if np.isfinite(n_eff) and n_eff > 0 else (float("nan"), float("nan")))
        entry = dict(k=k, n_valid=n_valid, nu_flip=nu_flip, nu_flip_ci=[lo, hi],
                     nu_flip_boot_mean=bmean, confusion=dict(conf),
                     headline_wr=base["wr"], headline_n=base["n"],
                     n_eff=n_eff, wr_ci_widened=[100.0 * wl, 100.0 * wh],
                     wr_ci_nominal=[base["wr_lo"], base["wr_hi"]])
        # counterfactual payoff (diagnostic)
        cfw = usable[f"cf_win_k{k}"].cast(pl.Float64).to_numpy()
        cfe = usable[f"cf_ev_k{k}"].cast(pl.Float64).to_numpy()
        entry["counterfactual_payoff"] = _ev_wr(cfw, cfe, np.ones(len(win), dtype=bool))
        entry["by_year"] = {}
        for y in sorted(set(years.tolist())):
            rows = np.where(years == y)[0]
            entry["by_year"][str(y)] = dict(n=int(len(rows)), nu_flip=_flip(rows))
        res["M2b"][str(k)] = entry

    # ---------------- K ladder subset shares ----------------
    res["K_ladder"] = {}
    for k in K_LADDER:
        sub_k = np.array(usable[f"subset_k{k}"].to_list(), dtype=object)
        res["K_ladder"][str(k)] = {
            s: dict(share=float((sub_k == s).mean()),
                    **_ev_wr(win, ev, sub_k == s))
            for s in ("EVENT", "CYCLICAL", "MIXED")
        }

    # ---------------- M3: gradient skill within 75+ ----------------
    res["M3"] = {}
    for s in SUBSETS:
        sm = smask(s)
        entry = {}
        entry["spearman_win"] = spearman(overall[sm], win[sm])
        entry["spearman_ev"] = spearman(overall[sm], ev[sm])
        rk_x = np.full(len(win), np.nan)
        rk_y = np.full(len(win), np.nan)
        idx = np.where(sm & np.isfinite(overall) & np.isfinite(win))[0]
        if len(idx) >= 3:
            rk_x[idx] = _ranks(overall[idx])
            rk_y[idx] = _ranks(win[idx])
        entry["spearman_cr1"] = cr1_continuous(rk_x, rk_y, clusters, sm,
                                               f"M3_spearman_rank_{s}", strict=(len(idx) >= 2 * MIN_CELL_CONTROL_SIDE))
        rk_x2 = np.full(len(win), np.nan)
        rk_y2 = np.full(len(win), np.nan)
        idx2 = np.where(sm & np.isfinite(overall) & np.isfinite(ev))[0]
        if len(idx2) >= 3:
            rk_x2[idx2] = _ranks(overall[idx2])
            rk_y2[idx2] = _ranks(ev[idx2])
        entry["spearman_ev_cr1"] = cr1_continuous(rk_x2, rk_y2, clusters, sm,
                                                  f"M3_spearman_ev_rank_{s}",
                                                  strict=(len(idx2) >= 2 * MIN_CELL_CONTROL_SIDE))
        # A6: 4-band ladder is DESCRIPTIVE ONLY; every N<150 cell is flagged underpowered.
        entry["bands"] = {}
        for nm, lo_b, hi_b in SCORE_BANDS:
            bm = sm & (overall >= lo_b) & (overall < hi_b)
            stats = _ev_wr(win, ev, bm)
            stats["meets_B4_N150"] = bool(stats["n"] >= MIN_N_BAND)
            stats["underpowered"] = bool(stats["n"] < MIN_N_BAND)
            stats["cr1_ev_vs_rest"] = cr1_dummy(bm.astype(float), ev, clusters, sm,
                                                binary=False, label=f"M3_band_{nm}_{s}_ev")
            stats["cr1_win_vs_rest"] = cr1_dummy(bm.astype(float), win, clusters, sm,
                                                 binary=True, label=f"M3_band_{nm}_{s}_win")
            entry["bands"][nm] = stats
        # top-vs-bottom band EV spread (B2)
        b_top, b_bot = None, None
        for nm, _, _ in reversed(SCORE_BANDS):
            if entry["bands"][nm]["n"] > 0:
                b_top = nm
                break
        for nm, _, _ in SCORE_BANDS:
            if entry["bands"][nm]["n"] > 0:
                b_bot = nm
                break
        if b_top and b_bot and b_top != b_bot:
            entry["band_ev_spread_pp"] = 100.0 * (entry["bands"][b_top]["ev"] - entry["bands"][b_bot]["ev"])
            entry["band_wr_spread_pp"] = entry["bands"][b_top]["wr"] - entry["bands"][b_bot]["wr"]
            entry["band_top"], entry["band_bottom"] = b_top, b_bot

        # ---- A6 PRIMARY band contrast: fixed 2-band collapse 75-79 vs 80+ ----
        b2 = {}
        for nm, lo_b, hi_b in SCORE_BANDS_2:
            bm = sm & (overall >= lo_b) & (overall < hi_b)
            st = _ev_wr(win, ev, bm)
            st["underpowered"] = bool(st["n"] < MIN_N_BAND)
            b2[nm] = st
        hi_m = sm & (overall >= 80)
        b2["contrast"] = dict(
            ev_spread_pp=100.0 * (b2["80+"]["ev"] - b2["75-79"]["ev"]),
            wr_spread_pp=b2["80+"]["wr"] - b2["75-79"]["wr"],
            cr1_ev=cr1_dummy(hi_m.astype(float), ev, clusters, sm, binary=False,
                             label=f"A6_2band_{s}_ev"),
            cr1_win=cr1_dummy(hi_m.astype(float), win, clusters, sm, binary=True,
                              label=f"A6_2band_{s}_win"),
        )
        b2["by_year"] = {}
        for y in sorted(set(years.tolist())):
            ym = sm & (years == y)
            a = _ev_wr(win, ev, ym & (overall >= 80))
            b = _ev_wr(win, ev, ym & (overall >= 75) & (overall < 80))
            b2["by_year"][str(y)] = dict(
                hi=a, lo=b,
                ev_spread_pp=100.0 * (a["ev"] - b["ev"]) if (a["n"] and b["n"]) else float("nan"),
                wr_spread_pp=(a["wr"] - b["wr"]) if (a["n"] and b["n"]) else float("nan"))
        entry["bands2"] = b2

        entry["by_year"] = {}
        for y in sorted(set(years.tolist())):
            ym = sm & (years == y)
            yb = {}
            for nm, lo_b, hi_b in SCORE_BANDS:
                st = _ev_wr(win, ev, ym & (overall >= lo_b) & (overall < hi_b))
                st["underpowered"] = bool(st["n"] < MIN_N_BAND)
                yb[nm] = st
            entry["by_year"][str(y)] = dict(
                n=int(ym.sum()),
                spearman_win=spearman(overall[ym], win[ym]),
                spearman_ev=spearman(overall[ym], ev[ym]),
                bands=yb,
            )
        res["M3"][s] = entry

    # ---------------- M4: skill vs baselines ----------------
    mom = usable["mom20"].cast(pl.Float64).to_numpy()
    res["M4"] = {}
    for s in SUBSETS:
        sm = smask(s)
        clim = _ev_wr(win, ev, sm)
        entry = dict(climatology=clim)
        # score skill: top band (>=85) and >=80 vs subset climatology
        entry["score_cuts"] = {}
        for cut in (80, 85, 90):
            cm = sm & (overall >= cut)
            st = _ev_wr(win, ev, cm)
            st["wr_skill_pp"] = st["wr"] - clim["wr"] if st["n"] else float("nan")
            st["ev_skill_pp"] = 100.0 * (st["ev"] - clim["ev"]) if st["n"] else float("nan")
            st["cr1_win"] = cr1_dummy(cm.astype(float), win, clusters, sm, binary=True,
                                      label=f"M4_score_ge{cut}_{s}_win")
            st["cr1_ev"] = cr1_dummy(cm.astype(float), ev, clusters, sm, binary=False,
                                     label=f"M4_score_ge{cut}_{s}_ev")
            entry["score_cuts"][str(cut)] = st
        # momentum baseline: quintiles of trailing-20d return, within subset
        mm = sm & np.isfinite(mom)
        entry["momentum"] = dict(coverage=int(mm.sum()), quintiles={})
        if mm.sum() >= 5 * MIN_CELL_CONTROL_SIDE:
            qs = np.quantile(mom[mm], np.linspace(0, 1, 6))
            qidx = np.full(len(win), -1, dtype=int)
            for qi in range(5):
                lo_q, hi_q = qs[qi], qs[qi + 1]
                sel = mm & (mom >= lo_q) & ((mom <= hi_q) if qi == 4 else (mom < hi_q))
                qidx[sel] = qi
                entry["momentum"]["quintiles"][f"Q{qi+1}"] = dict(
                    lo=float(lo_q), hi=float(hi_q), **_ev_wr(win, ev, sel))
            # momentum-matched base rate for the >=85 cut (skill BEYOND momentum)
            for cut in (80, 85):
                cm = mm & (overall >= cut)
                if cm.sum() >= MIN_CELL_CONTROL_SIDE:
                    wsum = nsum = 0.0
                    evsum = 0.0
                    for qi in range(5):
                        w_q = float((qidx[cm] == qi).sum())
                        base_q = mm & (qidx == qi)
                        if base_q.sum() > 0 and w_q > 0:
                            wsum += w_q * float(np.nanmean(win[base_q]))
                            evsum += w_q * float(np.nanmean(ev[base_q]))
                            nsum += w_q
                    matched_wr = 100.0 * wsum / nsum if nsum else float("nan")
                    matched_ev = evsum / nsum if nsum else float("nan")
                    obs_ = _ev_wr(win, ev, cm)
                    entry["momentum"][f"matched_ge{cut}"] = dict(
                        n=obs_["n"], wr=obs_["wr"], matched_base_wr=matched_wr,
                        skill_beyond_momentum_pp=obs_["wr"] - matched_wr,
                        ev=obs_["ev"], matched_base_ev=matched_ev,
                        ev_skill_beyond_momentum_pp=100.0 * (obs_["ev"] - matched_ev))
            # momentum's OWN discrimination (top vs bottom quintile)
            q_top = mm & (qidx == 4)
            q_bot = mm & (qidx == 0)
            a, b = _ev_wr(win, ev, q_top), _ev_wr(win, ev, q_bot)
            entry["momentum"]["top_minus_bottom_wr_pp"] = a["wr"] - b["wr"]
            entry["momentum"]["top_minus_bottom_ev_pp"] = 100.0 * (a["ev"] - b["ev"])
            entry["momentum"]["cr1_topq_win"] = cr1_dummy(q_top.astype(float), win, clusters, mm,
                                                          binary=True, label=f"M4_momQ5_{s}_win")
        entry["by_year"] = {}
        for y in sorted(set(years.tolist())):
            ym = sm & (years == y)
            cl_y = _ev_wr(win, ev, ym)
            hi_y = _ev_wr(win, ev, ym & (overall >= 85))
            entry["by_year"][str(y)] = dict(clim_wr=cl_y["wr"], n=cl_y["n"],
                                            ge85_wr=hi_y["wr"], ge85_n=hi_y["n"],
                                            skill_pp=hi_y["wr"] - cl_y["wr"] if hi_y["n"] else float("nan"))
        res["M4"][s] = entry

    # ---------------- M5: checklist ----------------
    res["M5"] = {}
    for thr in CHECK_THRESH_LADDER:
        col = f"n_checks_{thr}"
        if col not in usable.columns:
            continue
        nchk = usable[col].cast(pl.Float64).to_numpy()
        thr_entry = dict(threshold=thr, coverage=int(np.isfinite(nchk).sum()))
        thr_entry["subsets"] = {}
        for s in SUBSETS:
            sm = smask(s) & np.isfinite(nchk)
            sub = dict(n=int(sm.sum()), levels={})
            for lvl in range(0, 7):
                lm = sm & (nchk == lvl)
                sub["levels"][str(lvl)] = _ev_wr(win, ev, lm)
            sub["cr1_monotone_ev"] = cr1_continuous(nchk, ev, clusters, sm,
                                                    f"M5_nchecks{thr}_{s}_ev",
                                                    strict=(sm.sum() >= 2 * MIN_CELL_CONTROL_SIDE))
            sub["cr1_monotone_win"] = cr1_continuous(nchk, win, clusters, sm,
                                                     f"M5_nchecks{thr}_{s}_win",
                                                     strict=(sm.sum() >= 2 * MIN_CELL_CONTROL_SIDE))
            # hi/lo split: cut closest to 50/50 with share inside the gate
            cut, best = None, None
            for c in range(1, 7):
                share = float((nchk[sm] >= c).mean()) if sm.sum() else float("nan")
                if not np.isfinite(share) or not (BUCKET_SHARE_LO <= share <= BUCKET_SHARE_HI):
                    continue
                dist = abs(share - 0.5)
                if best is None or dist < best:
                    best, cut = dist, c
            sub["hi_cut"] = cut
            if cut is not None:
                hi = sm & (nchk >= cut)
                sub["hi"] = _ev_wr(win, ev, hi)
                sub["lo"] = _ev_wr(win, ev, sm & ~hi)
                sub["ev_spread_pp"] = 100.0 * (sub["hi"]["ev"] - sub["lo"]["ev"])
                sub["cr1_hi_ev"] = cr1_dummy(hi.astype(float), ev, clusters, sm, binary=False,
                                             label=f"M5_hi{thr}_{s}_ev")
                sub["cr1_hi_win"] = cr1_dummy(hi.astype(float), win, clusters, sm, binary=True,
                                              label=f"M5_hi{thr}_{s}_win")
                # C2: compare against the continuous `overall` gradient on the SAME rows
                ohi = sm & (overall >= 80)
                o_hi, o_lo = _ev_wr(win, ev, ohi), _ev_wr(win, ev, sm & ~ohi)
                sub["overall_ev_spread_pp"] = 100.0 * (o_hi["ev"] - o_lo["ev"])
                sub["C2_delta_pp"] = sub["ev_spread_pp"] - sub["overall_ev_spread_pp"]
                # 2x2 n_checks(hi/lo) x overall band(hi/lo)
                cells = {}
                for a_lbl, a_m in (("nchk_hi", hi), ("nchk_lo", sm & ~hi)):
                    for b_lbl, b_m in (("score_hi", ohi), ("score_lo", sm & ~ohi)):
                        cells[f"{a_lbl}|{b_lbl}"] = _ev_wr(win, ev, a_m & b_m)
                sub["two_by_two"] = cells
            sub["by_year"] = {}
            for y in sorted(set(years.tolist())):
                ym = sm & (years == y)
                if cut is not None:
                    hy = ym & (nchk >= cut)
                    sub["by_year"][str(y)] = dict(
                        n=int(ym.sum()),
                        hi=_ev_wr(win, ev, hy), lo=_ev_wr(win, ev, ym & ~hy),
                        ev_spread_pp=100.0 * (_ev_wr(win, ev, hy)["ev"] - _ev_wr(win, ev, ym & ~hy)["ev"]))
                else:
                    sub["by_year"][str(y)] = dict(n=int(ym.sum()))
            thr_entry["subsets"][s] = sub
        res["M5"][str(thr)] = thr_entry

    # ---------------- Step 4: ex-ante features (pit_unverified flagged) ----------
    res["EXANTE"] = dict(pit_unverified=True,
                         note="d_earn_in_window and prior_earn_jump_pct derive from a CURRENT "
                              "earnings-calendar snapshot (prereg A2). Flagged pit_unverified. "
                              "No sizing recommendation is made by this harness.")
    earn = np.array([1.0 if v else (0.0 if v is not None else np.nan)
                     for v in usable["d_earn_in_window"].to_list()])
    jr252 = usable["jump_rate_252"].cast(pl.Float64).to_numpy()
    pej = usable["prior_earn_jump_pct"].cast(pl.Float64).to_numpy()

    def _cohort_block(name, values, cuts_kind):
        blk = dict(name=name, coverage=int(np.isfinite(values).sum()), cohorts={})
        base = np.isfinite(values)
        if cuts_kind == "bool":
            groups = [("TRUE", base & (values > 0.5)), ("FALSE", base & (values <= 0.5))]
        else:
            v = values[base]
            if v.size < 3 * MIN_CELL_CONTROL_SIDE or np.nanstd(v) <= 0:
                blk["cohorts"] = {}
                blk["note"] = "insufficient coverage for terciles"
                return blk
            q1, q2 = np.quantile(v, [1 / 3, 2 / 3])
            blk["cuts"] = [float(q1), float(q2)]
            groups = [("T1_low", base & (values <= q1)),
                      ("T2_mid", base & (values > q1) & (values <= q2)),
                      ("T3_high", base & (values > q2))]
        for lbl, gm in groups:
            st = _ev_wr(win, ev, gm)
            st["cr1_ev"] = cr1_dummy(gm.astype(float), ev, clusters, base, binary=False,
                                     label=f"EXANTE_{name}_{lbl}_ev")
            st["cr1_win"] = cr1_dummy(gm.astype(float), win, clusters, base, binary=True,
                                      label=f"EXANTE_{name}_{lbl}_win")
            st["by_year"] = {str(y): _ev_wr(win, ev, gm & (years == y))
                             for y in sorted(set(years.tolist()))}
            # subset composition (does the ex-ante proxy actually track EVENT?)
            st["event_share_in_cohort"] = float((subset[gm] == "EVENT").mean()) if gm.sum() else float("nan")
            blk["cohorts"][lbl] = st
        return blk

    # ---------------- A5: pre-entry jump contamination of the SIGNAL -------------
    # PRE-ENTRY and therefore genuinely ex-ante (no honesty-firewall issue): the
    # window ends AT the entry close. Bars D1-D5 are adjudicated by the orchestrator.
    res["A5"] = dict(primary_window=PRE_JUMP_PRIMARY, windows={})
    for n_pre in PRE_JUMP_WINS:
        ccol = f"pre_jump_cohort_{n_pre}"
        if ccol not in usable.columns:
            continue
        coh = np.array(usable[ccol].to_list(), dtype=object)
        sgn = usable[f"pre_jump_signed_ret_{n_pre}"].cast(pl.Float64).to_numpy()
        have = np.array([c is not None for c in coh])
        base = have & (coh == "NOJUMP")
        blk = dict(window=n_pre, coverage=int(have.sum()),
                   is_primary=bool(n_pre == PRE_JUMP_PRIMARY), cohorts={})
        for lbl in ("NOJUMP", "ANY", "POS", "NEG", "ZERO"):
            if lbl == "ANY":
                gm = have & (coh != "NOJUMP")
            else:
                gm = have & (coh == lbl)
            st = _ev_wr(win, ev, gm)
            st["share"] = float(gm.sum() / max(int(have.sum()), 1))
            st["underpowered"] = bool(st["n"] < MIN_N_BAND)
            if lbl == "NOJUMP":
                st["cr1_ev_vs_nojump"] = dict(label="base", method="is_base", gated=False,
                                              t=float("nan"), beta=float("nan"), n_used=st["n"])
                st["cr1_win_vs_nojump"] = dict(label="base", method="is_base", gated=False,
                                               t=float("nan"), beta=float("nan"), n_used=st["n"])
                st["ev_vs_nojump_pp"] = 0.0
                st["wr_vs_nojump_pp"] = 0.0
            else:
                samp = gm | base
                st["cr1_ev_vs_nojump"] = cr1_dummy(gm.astype(float), ev, clusters, samp,
                                                   binary=False, label=f"A5_{n_pre}_{lbl}_ev")
                st["cr1_win_vs_nojump"] = cr1_dummy(gm.astype(float), win, clusters, samp,
                                                    binary=True, label=f"A5_{n_pre}_{lbl}_win")
                bstat = _ev_wr(win, ev, base)
                st["ev_vs_nojump_pp"] = 100.0 * (st["ev"] - bstat["ev"]) if st["n"] else float("nan")
                st["wr_vs_nojump_pp"] = st["wr"] - bstat["wr"] if st["n"] else float("nan")
            # D2/D3: per calendar year
            st["by_year"] = {}
            for y in sorted(set(years.tolist())):
                ym = gm & (years == y)
                by = _ev_wr(win, ev, ym)
                bb = _ev_wr(win, ev, base & (years == y))
                by["ev_vs_nojump_pp"] = (100.0 * (by["ev"] - bb["ev"])
                                         if (by["n"] and bb["n"]) else float("nan"))
                by["wr_vs_nojump_pp"] = (by["wr"] - bb["wr"]) if (by["n"] and bb["n"]) else float("nan")
                by["meets_D3_N150"] = bool(by["n"] >= MIN_N_BAND)
                st["by_year"][str(y)] = by
            # D4: score-band control -- 4-band ladder AND the A6 2-band collapse
            st["by_band"] = {}
            for nm, lo_b, hi_b in SCORE_BANDS:
                bmask = (overall >= lo_b) & (overall < hi_b)
                cell = _ev_wr(win, ev, gm & bmask)
                bb = _ev_wr(win, ev, base & bmask)
                cell["ev_vs_nojump_pp"] = (100.0 * (cell["ev"] - bb["ev"])
                                           if (cell["n"] and bb["n"]) else float("nan"))
                cell["nojump_n"] = bb["n"]
                cell["underpowered"] = bool(cell["n"] < MIN_N_BAND)
                cell["cr1_ev_vs_nojump"] = cr1_dummy(gm.astype(float), ev, clusters,
                                                     (gm | base) & bmask, binary=False,
                                                     label=f"A5_{n_pre}_{lbl}_band{nm}_ev")
                st["by_band"][nm] = cell
            st["by_band2"] = {}
            for nm, lo_b, hi_b in SCORE_BANDS_2:
                bmask = (overall >= lo_b) & (overall < hi_b)
                cell = _ev_wr(win, ev, gm & bmask)
                bb = _ev_wr(win, ev, base & bmask)
                cell["ev_vs_nojump_pp"] = (100.0 * (cell["ev"] - bb["ev"])
                                           if (cell["n"] and bb["n"]) else float("nan"))
                cell["nojump_n"] = bb["n"]
                cell["underpowered"] = bool(cell["n"] < MIN_N_BAND)
                cell["cr1_ev_vs_nojump"] = cr1_dummy(gm.astype(float), ev, clusters,
                                                     (gm | base) & bmask, binary=False,
                                                     label=f"A5_{n_pre}_{lbl}_band2{nm}_ev")
                st["by_band2"][nm] = cell
            # ex-post subset composition (descriptive: does pre-entry contamination
            # predict an EVENT-driven OUTCOME?)
            st["subset_mix"] = {ss: float((subset[gm] == ss).mean()) if gm.sum() else float("nan")
                                for ss in ("EVENT", "CYCLICAL", "MIXED")}
            blk["cohorts"][lbl] = st
        blk["signed_ret_summary"] = dict(
            n=int(np.isfinite(sgn).sum()),
            mean=float(np.nanmean(sgn)) if np.isfinite(sgn).any() else float("nan"),
            p10=float(np.nanpercentile(sgn, 10)) if np.isfinite(sgn).any() else float("nan"),
            p90=float(np.nanpercentile(sgn, 90)) if np.isfinite(sgn).any() else float("nan"))
        res["A5"]["windows"][str(n_pre)] = blk

    # D5: collinearity with the PARKED peak_fakeout climactic-acceleration texture cell
    res["A5"]["D5_texture_collinearity"] = _texture_collinearity(usable, win)

    res["EXANTE"]["d_earn_in_window"] = _cohort_block("d_earn_in_window", earn, "bool")
    res["EXANTE"]["jump_rate_252"] = _cohort_block("jump_rate_252", jr252, "tercile")
    res["EXANTE"]["prior_earn_jump_pct"] = _cohort_block("prior_earn_jump_pct", pej, "tercile")

    return res


# =============================================================================
# Reporting
# =============================================================================
def _f(v, nd=2, width=0):
    if v is None:
        s = "n/a"
    elif isinstance(v, float) and not math.isfinite(v):
        s = "n/a"
    elif isinstance(v, float):
        s = f"{v:.{nd}f}"
    else:
        s = str(v)
    return s.rjust(width) if width else s


def _tline(res_cr1):
    if not res_cr1:
        return "n/a"
    if not res_cr1.get("gated"):
        return f"[{res_cr1.get('method','?')}]"
    return f"{res_cr1['t']:+.2f} ({res_cr1.get('method','')},G={res_cr1.get('n_clusters',0)})"


def build_summary(res: dict, meta: dict) -> str:
    L = []
    A = L.append
    A("=" * 100)
    A("EVENT vs CYCLICAL NOISE DECOMPOSITION -- v74 funded 75+ CALL ledger (apex15 barrier)")
    A("Implements experiments/event_noise_decomp/PREREGISTRATION.md (LOCKED). NUMBERS ONLY -- NO VERDICT.")
    A("=" * 100)
    for k, v in meta.items():
        A(f"  {k}: {v}")
    A("")
    if res.get("EMPTY"):
        A("NO USABLE ROWS -- see coverage breakdown.")
        A(f"  coverage: {res.get('coverage_breakdown')}")
        return "\n".join(L)

    A("-" * 100)
    A("COVERAGE")
    A("-" * 100)
    A(f"  ledger rows              : {res['n_ledger']}")
    A(f"  post-cutoff excluded     : {res['n_post_cutoff_excluded']}")
    A(f"  usable (coverage==ok)    : {res['n_usable']}")
    for k, v in sorted(res["coverage_breakdown"].items()):
        A(f"    coverage[{k}]{' ' * max(0, 20 - len(k))}: {v}")
    A("")
    anc = res.get("anchor_check")
    if anc:
        A("-" * 100)
        A("A6 ANCHOR -- pooled baseline on the COMPLETE funded ledger vs the orchestrator's audit")
        A("-" * 100)
        g, x = anc["got"], anc["expected"]
        A(f"  {'':<10} {'N':>7} {'WR':>8} {'EV':>9} {'75-79':>8} {'80-84':>8} {'85-89':>8} {'90+':>8}")
        A(f"  {'measured':<10} {g['n']:>7} {_f(g['wr'],4,8)} {_f(g['ev'],4,9)} "
          + " ".join(_f(g['bands'][nm], 4, 8) for nm, _, _ in SCORE_BANDS))
        A(f"  {'audited':<10} {x['n']:>7} {_f(x['wr'],4,8)} {_f(x['ev'],4,9)} "
          + " ".join(_f(x['bands'][nm], 4, 8) for nm, _, _ in SCORE_BANDS))
        A(f"  band N measured : " + " ".join(f"{nm}={g['band_n'][nm]}" for nm, _, _ in SCORE_BANDS))
        if anc["ok"]:
            A("  STATUS: MATCH (exact to 4 dp).")
        else:
            A("  STATUS: *** MISMATCH -- SUBSET SPLITS ARE NOT TRUSTWORTHY ***")
            for s in anc["disagreements"]:
                A(f"    DISAGREEMENT: {s}")
        A("")

    # M1
    A("-" * 100)
    A("M1 -- PAYOFF BY SUBSET (apex15, EV map win+0.30 / stop-0.70 / expire-0.40)")
    A("-" * 100)
    A(f"  {'subset':<10} {'N':>6} {'share':>7} {'WR%':>7} {'WR 95% CI':>16} {'EV':>8}")
    for s in ("ALL", "EVENT", "CYCLICAL", "MIXED"):
        d = res["M1"][s]
        A(f"  {s:<10} {d['n']:>6} {d['share']*100:>6.1f}% {_f(d['wr'],2,7)} "
          f"{('[' + _f(d['wr_lo'],1) + ',' + _f(d['wr_hi'],1) + ']'):>16} {_f(d['ev'],4,8)}")
    A("")
    A("  per-year (N / WR% / EV):")
    years = sorted(res["M1_by_year"]["ALL"].keys())
    A(f"  {'subset':<10} " + " ".join(f"{y:>20}" for y in years))
    for s in ("ALL", "EVENT", "CYCLICAL", "MIXED"):
        cells = []
        for y in years:
            d = res["M1_by_year"][s][y]
            cells.append(f"{d['n']:>4}/{_f(d['wr'],1):>5}/{_f(d['ev'],3):>6}".rjust(20))
        A(f"  {s:<10} " + " ".join(cells))
    A("")

    # M2
    A("-" * 100)
    A("M2 -- NOISE CONSTANT (variance share, R^2, EVENT population share); date-block bootstrap 1000 draws")
    A("-" * 100)
    m2 = res["M2"]
    for nm, lbl in (("nu_var", "nu_var = Var(jump_ret)/Var(total_ret)"),
                    ("r2", "R^2 of jump_ret on total_ret"),
                    ("event_share", "EVENT population share")):
        ci = m2[nm + "_ci"]
        A(f"  {lbl:<42} = {_f(m2[nm],4)}   95% CI [{_f(ci[0],4)}, {_f(ci[1],4)}]  "
          f"(boot mean {_f(m2[nm+'_boot_mean'],4)})")
    A("")
    A(f"  per-year: {'year':<6} {'N':>6} {'nu_var':>9} {'R^2':>8} {'EVENT share':>12}")
    for y, d in sorted(m2["by_year"].items()):
        A(f"            {y:<6} {d['n']:>6} {_f(d['nu_var'],4,9)} {_f(d['r2'],4,8)} {_f(d['event_share'],4,12)}")
    A("")

    # M2b
    A("-" * 100)
    A("M2b -- HEADLINE nu_flip (counterfactual re-walk; jump-day idio returns replaced by the")
    A("       trade's own non-jump mean idio drift, beta*market untouched, entry-date sigma fixed)")
    A("-" * 100)
    for k in K_LADDER:
        e = res["M2b"][str(k)]
        star = "  <== PRIMARY" if k == K_PRIMARY else ""
        A(f"  K={k}{star}")
        A(f"    N valid          : {e['n_valid']}")
        A(f"    nu_flip          : {_f(e['nu_flip'],4)}   95% CI [{_f(e['nu_flip_ci'][0],4)}, "
          f"{_f(e['nu_flip_ci'][1],4)}]")
        A(f"    N_eff = N(1-nu)  : {_f(e['n_eff'],1)}  (from N={e['headline_n']})")
        A(f"    headline WR      : {_f(e['headline_wr'],2)}%  nominal CI "
          f"[{_f(e['wr_ci_nominal'][0],2)}, {_f(e['wr_ci_nominal'][1],2)}]  "
          f"WIDENED at N_eff [{_f(e['wr_ci_widened'][0],2)}, {_f(e['wr_ci_widened'][1],2)}]")
        cp = e["counterfactual_payoff"]
        A(f"    counterfactual   : N={cp['n']} WR={_f(cp['wr'],2)}% EV={_f(cp['ev'],4)} (diagnostic)")
        A(f"    confusion (orig->counterfactual):")
        for kk, vv in sorted(e["confusion"].items(), key=lambda kv: -kv[1]):
            A(f"        {kk:<20} {vv}")
        A(f"    per-year nu_flip : " + "  ".join(
            f"{y}={_f(d['nu_flip'],3)}(N={d['n']})" for y, d in sorted(e["by_year"].items())))
        A("")

    # K ladder
    A("-" * 100)
    A("K-LADDER -- subset composition and payoff at K in {2.5, 3.0, 4.0}")
    A("-" * 100)
    A(f"  {'K':>5} {'subset':<10} {'share':>8} {'N':>6} {'WR%':>7} {'EV':>8}")
    for k in K_LADDER:
        for s in ("EVENT", "CYCLICAL", "MIXED"):
            d = res["K_ladder"][str(k)][s]
            A(f"  {k:>5} {s:<10} {d['share']*100:>7.1f}% {d['n']:>6} {_f(d['wr'],2,7)} {_f(d['ev'],4,8)}")
    A("")

    # M3
    A("-" * 100)
    A("M3 -- GRADIENT SKILL WITHIN 75+ (per subset), date-clustered CR1 (clusters = entry date)")
    A("-" * 100)
    for s in ("ALL", "EVENT", "CYCLICAL", "MIXED"):
        e = res["M3"][s]
        A(f"  [{s}]")
        A(f"    Spearman(overall, apex_win) = {_f(e['spearman_win'],4)}   CR1 t = {_tline(e['spearman_cr1'])}"
          f"   (n_used={e['spearman_cr1'].get('n_used')})")
        A(f"    Spearman(overall, apex_ev)  = {_f(e['spearman_ev'],4)}   CR1 t = {_tline(e['spearman_ev_cr1'])}")
        b2 = e["bands2"]
        A(f"    A6 PRIMARY 2-band contrast (75-79 vs 80+):")
        A(f"      {'band':<8} {'N':>6} {'underpwr':>9} {'WR%':>7} {'EV':>8}")
        for nm, _, _ in SCORE_BANDS_2:
            d = b2[nm]
            A(f"      {nm:<8} {d['n']:>6} {str(d['underpowered']):>9} {_f(d['wr'],2,7)} {_f(d['ev'],4,8)}")
        ct = b2["contrast"]
        A(f"      80+ minus 75-79: EV {_f(ct['ev_spread_pp'],2)} pp / WR {_f(ct['wr_spread_pp'],2)} pp"
          f"   CR1 t EV = {_tline(ct['cr1_ev'])}   CR1 t WIN = {_tline(ct['cr1_win'])}")
        A(f"      per-year EV spread pp: " + "  ".join(
            f"{y}={_f(d['ev_spread_pp'],2)}(N {d['lo']['n']}/{d['hi']['n']})"
            for y, d in sorted(b2["by_year"].items())))
        A(f"    4-band ladder [A6: DESCRIPTIVE ONLY -- may not carry a verdict leg]:")
        A(f"    {'band':<8} {'N':>6} {'underpwr':>9} {'WR%':>7} {'WR 95% CI':>16} {'EV':>8} "
          f"{'CR1 t (EV vs rest)':>24} {'CR1 t (WIN vs rest)':>24}")
        for nm, _, _ in SCORE_BANDS:
            d = e["bands"][nm]
            flag = "UNDERPOWERED" if d["underpowered"] else ""
            A(f"    {nm:<8} {d['n']:>6} {str(d['underpowered']):>9} {_f(d['wr'],2,7)} "
              f"{('[' + _f(d['wr_lo'],1) + ',' + _f(d['wr_hi'],1) + ']'):>16} {_f(d['ev'],4,8)} "
              f"{_tline(d['cr1_ev_vs_rest']):>24} {_tline(d['cr1_win_vs_rest']):>24} {flag}")
        if "band_ev_spread_pp" in e:
            A(f"    4-band EV spread ({e['band_top']} - {e['band_bottom']}) = "
              f"{_f(e['band_ev_spread_pp'],2)} pp   WR spread = {_f(e['band_wr_spread_pp'],2)} pp "
              f"[descriptive]")
        A(f"    per-year Spearman(win): " + "  ".join(
            f"{y}={_f(d['spearman_win'],3)}(N={d['n']})" for y, d in sorted(e["by_year"].items())))
        A("")

    # M4
    A("-" * 100)
    A("M4 -- SKILL VS BASELINES (climatology = subset base rate; momentum = trailing-20d return rank)")
    A("-" * 100)
    for s in ("ALL", "EVENT", "CYCLICAL", "MIXED"):
        e = res["M4"][s]
        c = e["climatology"]
        A(f"  [{s}] climatology: N={c['n']} WR={_f(c['wr'],2)}% EV={_f(c['ev'],4)}")
        A(f"    {'cut':<8} {'N':>6} {'WR%':>7} {'WR skill pp':>12} {'EV':>8} {'EV skill pp':>12} "
          f"{'CR1 t WIN':>22} {'CR1 t EV':>22}")
        for cut in ("80", "85", "90"):
            d = e["score_cuts"][cut]
            A(f"    >={cut:<6} {d['n']:>6} {_f(d['wr'],2,7)} {_f(d['wr_skill_pp'],2,12)} "
              f"{_f(d['ev'],4,8)} {_f(d['ev_skill_pp'],2,12)} {_tline(d['cr1_win']):>22} "
              f"{_tline(d['cr1_ev']):>22}")
        m = e["momentum"]
        A(f"    momentum coverage: {m['coverage']}")
        if m.get("quintiles"):
            for q, d in sorted(m["quintiles"].items()):
                A(f"      {q} mom20 in [{_f(d['lo'],4)},{_f(d['hi'],4)}] N={d['n']:>5} "
                  f"WR={_f(d['wr'],2)}% EV={_f(d['ev'],4)}")
            A(f"      momentum own discrimination: top-bottom WR "
              f"{_f(m.get('top_minus_bottom_wr_pp'),2)} pp / EV {_f(m.get('top_minus_bottom_ev_pp'),2)} pp "
              f"CR1 t(topQ,win)={_tline(m.get('cr1_topq_win'))}")
            for cut in ("80", "85"):
                mk = m.get(f"matched_ge{cut}")
                if mk:
                    A(f"      score>={cut} vs MOMENTUM-MATCHED base: N={mk['n']} WR={_f(mk['wr'],2)}% "
                      f"matched={_f(mk['matched_base_wr'],2)}% skill={_f(mk['skill_beyond_momentum_pp'],2)} pp"
                      f" | EV skill={_f(mk['ev_skill_beyond_momentum_pp'],2)} pp")
        A(f"    per-year (clim WR / >=85 WR / skill pp): " + "  ".join(
            f"{y}:{_f(d['clim_wr'],1)}/{_f(d['ge85_wr'],1)}/{_f(d['skip'] if 'skip' in d else d['skill_pp'],1)}"
            for y, d in sorted(e["by_year"].items())))
        A("")

    # M5
    A("-" * 100)
    A("M5 -- CHECKLIST FORM: n_checks = count of {trend,bb,rsi,macd,stoch,technical_alignment} >= threshold")
    A("-" * 100)
    for thr in CHECK_THRESH_LADDER:
        blk = res["M5"].get(str(thr))
        if not blk:
            continue
        star = "  <== PRIMARY" if thr == CHECK_THRESH_PRIMARY else ""
        A(f"  threshold={thr}{star}   coverage={blk['coverage']}")
        for s in ("ALL", "EVENT", "CYCLICAL", "MIXED"):
            sub = blk["subsets"].get(s)
            if not sub:
                continue
            A(f"    [{s}] N={sub['n']}  hi_cut(n_checks>=) = {sub.get('hi_cut')}")
            A(f"      {'n_checks':<9} {'N':>6} {'WR%':>7} {'EV':>8}")
            for lvl in range(0, 7):
                d = sub["levels"][str(lvl)]
                if d["n"] == 0:
                    continue
                A(f"      {lvl:<9} {d['n']:>6} {_f(d['wr'],2,7)} {_f(d['ev'],4,8)}")
            A(f"      CR1 monotone EV  t = {_tline(sub['cr1_monotone_ev'])}   "
              f"slope={_f(sub['cr1_monotone_ev'].get('beta'),5)}")
            A(f"      CR1 monotone WIN t = {_tline(sub['cr1_monotone_win'])}")
            if sub.get("hi_cut") is not None:
                A(f"      hi vs lo: EV spread = {_f(sub['ev_spread_pp'],2)} pp "
                  f"(hi N={sub['hi']['n']} EV={_f(sub['hi']['ev'],4)} / lo N={sub['lo']['n']} "
                  f"EV={_f(sub['lo']['ev'],4)})  CR1 t EV = {_tline(sub['cr1_hi_ev'])}")
                A(f"      C2: overall(>=80 vs 75-79) EV spread = {_f(sub['overall_ev_spread_pp'],2)} pp "
                  f"-> n_checks minus overall = {_f(sub['C2_delta_pp'],2)} pp")
                A(f"      2x2 (N / WR% / EV):")
                for cell, d in sorted(sub["two_by_two"].items()):
                    A(f"        {cell:<22} {d['n']:>6} {_f(d['wr'],2,7)} {_f(d['ev'],4,8)}")
                A(f"      per-year EV spread: " + "  ".join(
                    f"{y}={_f(d.get('ev_spread_pp'),2)}(N={d['n']})"
                    for y, d in sorted(sub["by_year"].items())))
            A("")
    A("")

    # A5
    A("-" * 100)
    A("A5 -- PRE-ENTRY JUMP CONTAMINATION OF THE SIGNAL (window ENDS AT the entry close: ex-ante)")
    A("     cohorts: NOJUMP base / ANY / POS (signed jump sum > 0) / NEG (< 0). CR1 vs the NOJUMP base.")
    A("-" * 100)
    a5 = res.get("A5", {})
    for n_pre in PRE_JUMP_WINS:
        blk = a5.get("windows", {}).get(str(n_pre))
        if not blk:
            continue
        star = "  <== PRIMARY" if blk.get("is_primary") else ""
        sr = blk["signed_ret_summary"]
        A(f"  window = {n_pre} trading days{star}   coverage={blk['coverage']}   "
          f"signed_ret mean={_f(sr['mean'],4)} p10={_f(sr['p10'],4)} p90={_f(sr['p90'],4)}")
        A(f"    {'cohort':<8} {'N':>6} {'share':>7} {'underpwr':>9} {'WR%':>7} {'EV':>8} "
          f"{'dEV vs NOJUMP pp':>17} {'CR1 t EV':>22} {'CR1 t WIN':>22}")
        for lbl in ("NOJUMP", "ANY", "POS", "NEG", "ZERO"):
            d = blk["cohorts"].get(lbl)
            if not d or d["n"] == 0:
                continue
            A(f"    {lbl:<8} {d['n']:>6} {d['share']*100:>6.1f}% {str(d['underpowered']):>9} "
              f"{_f(d['wr'],2,7)} {_f(d['ev'],4,8)} {_f(d['ev_vs_nojump_pp'],2,17)} "
              f"{_tline(d['cr1_ev_vs_nojump']):>22} {_tline(d['cr1_win_vs_nojump']):>22}")
        for lbl in ("ANY", "POS", "NEG"):
            d = blk["cohorts"].get(lbl)
            if not d or d["n"] == 0:
                continue
            A(f"    [{lbl}] per-year (N / dEV pp / D3 N>=150): " + "  ".join(
                f"{y}:{v['n']}/{_f(v['ev_vs_nojump_pp'],2)}/{str(v['meets_D3_N150'])[0]}"
                for y, v in sorted(d["by_year"].items())))
            A(f"    [{lbl}] D4 score-band control -- A6 2-band: " + "  ".join(
                f"{nm}:N={v['n']}(base {v['nojump_n']}) dEV={_f(v['ev_vs_nojump_pp'],2)} pp "
                f"t={_tline(v['cr1_ev_vs_nojump'])}"
                for nm, v in d["by_band2"].items()))
            A(f"    [{lbl}] D4 score-band control -- 4-band: " + "  ".join(
                f"{nm}:N={v['n']} dEV={_f(v['ev_vs_nojump_pp'],2)} pp"
                + ("[UP]" if v["underpowered"] else "")
                for nm, v in d["by_band"].items()))
            A(f"    [{lbl}] ex-post subset mix: " + "  ".join(
                f"{k}={_f(v,3)}" for k, v in d["subset_mix"].items()))
        A("")
    d5 = a5.get("D5_texture_collinearity", {})
    A(f"  D5 collinearity with the PARKED peak_fakeout climactic-acceleration texture cell "
      f"(bar |rho| >= {TEXTURE_COLLINEAR_BAR}):")
    if not d5.get("available"):
        A(f"    SKIPPED: {d5.get('note','no texture frame')}")
    else:
        for nm, v in d5["pairs"].items():
            if v.get("status") != "ok":
                A(f"    {nm:<42} {v.get('status')}")
                continue
            r = v.get("pearson", v.get("phi"))
            extra = f" spearman={_f(v.get('spearman'),4)}" if "spearman" in v else ""
            A(f"    {nm:<42} n={v['n']:>6} rho={_f(r,4)}{extra}  "
              f"exceeds_bar={v['exceeds_bar']}")
    A("")

    # EXANTE
    A("-" * 100)
    A("STEP 4 -- EX-ANTE FEATURES  [pit_unverified = TRUE for every earnings-derived leg]")
    A("-" * 100)
    A(f"  {res['EXANTE']['note']}")
    for key in ("d_earn_in_window", "jump_rate_252", "prior_earn_jump_pct"):
        blk = res["EXANTE"][key]
        A(f"  [{blk['name']}] coverage={blk['coverage']}"
          + (f"  cuts={[round(c,4) for c in blk['cuts']]}" if blk.get("cuts") else "")
          + (f"  NOTE: {blk['note']}" if blk.get("note") else ""))
        if not blk["cohorts"]:
            A("")
            continue
        A(f"    {'cohort':<10} {'N':>6} {'WR%':>7} {'EV':>8} {'EVENTshare':>11} "
          f"{'CR1 t EV':>22} {'CR1 t WIN':>22}")
        for lbl, d in blk["cohorts"].items():
            A(f"    {lbl:<10} {d['n']:>6} {_f(d['wr'],2,7)} {_f(d['ev'],4,8)} "
              f"{_f(d['event_share_in_cohort'],3,11)} {_tline(d['cr1_ev']):>22} {_tline(d['cr1_win']):>22}")
        for lbl, d in blk["cohorts"].items():
            A(f"    {lbl} per-year (N/WR%/EV): " + "  ".join(
                f"{y}:{v['n']}/{_f(v['wr'],1)}/{_f(v['ev'],3)}" for y, v in sorted(d["by_year"].items())))
        A("")
    A("=" * 100)
    A("END OF REPORT -- numbers only; adjudication against the pre-registered bars is the")
    A("orchestrator's job (see PREREGISTRATION.md sections 3a-3d).")
    A("=" * 100)
    return "\n".join(L)


def _jsonify(o):
    if isinstance(o, dict):
        return {str(k): _jsonify(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonify(v) for v in o]
    # bool MUST be tested before int (bool is an int subclass -- otherwise every
    # `underpowered` / `pit_unverified` flag serializes as 1/0 instead of true/false)
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer, int)):
        return int(o)
    if isinstance(o, (date,)):
        return o.isoformat()
    if o is None or isinstance(o, str):
        return o
    return str(o)


# =============================================================================
# SELF-TESTS (fully offline, synthetic)
# =============================================================================
def _synth_panel(n_sym=6, n_days=400, seed=7, closure_day=None, spike=None):
    """Build a synthetic OHLC polars frame on a business-day calendar."""
    rng = np.random.default_rng(seed)
    d0 = date(2020, 1, 1)
    days = []
    d = d0
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    syms = ["SPY"] + [f"SYN{i}" for i in range(n_sym - 1)]
    rows = []
    mkt = rng.normal(0.0, 0.01, n_days)
    for si, s in enumerate(syms):
        if s == "SPY":
            r = mkt
        else:
            r = 1.0 * mkt + rng.normal(0.0, 0.015, n_days)
        c = 100.0 * np.exp(np.cumsum(r))
        for t, dd in enumerate(days):
            if closure_day is not None and dd == closure_day:
                continue
            cl = c[t]
            if spike is not None and s == spike[0] and dd == spike[1]:
                cl = cl * spike[2]
            rows.append((s, dd, cl, cl * 1.005, cl * 0.995, cl, 1e6))
    df = pl.DataFrame(
        {"symbol": [r[0] for r in rows], "date": [r[1] for r in rows],
         "open": [r[2] for r in rows], "high": [r[3] for r in rows],
         "low": [r[4] for r in rows], "close": [r[5] for r in rows],
         "volume": [r[6] for r in rows]},
        infer_schema_length=None,
    ).sort(["symbol", "date"])
    return df, days, syms


def selftest_1_no_lookahead():
    df, days, syms = _synth_panel()
    t = 300
    grid, _, _ = build_trading_grid(df, min_syms=3)
    p0 = build_panel(df, grid)
    df2, _, _ = _synth_panel(spike=("SYN0", days[t], 1.35))
    grid2, _, _ = build_trading_grid(df2, min_syms=3)
    p1 = build_panel(df2, grid2)
    si = p0.sym_idx["SYN0"]
    gi = p0.grid_idx[days[t]]
    b0, b1 = p0.beta[si, gi], p1.beta[si, gi]
    s0, s1 = p0.sigma_a[si, gi], p1.sigma_a[si, gi]
    assert np.isfinite(b0) and np.isfinite(s0), "ST1 fixture produced no beta/sigma"
    assert abs(b0 - b1) < 1e-12, f"ST1 LOOK-AHEAD: beta changed {b0} -> {b1}"
    assert abs(s0 - s1) < 1e-12, f"ST1 LOOK-AHEAD: sigma changed {s0} -> {s1}"
    # and the spike DOES change the same-day r_idio (fixture is live)
    assert abs(p0.r_idio[si, gi] - p1.r_idio[si, gi]) > 0.05, "ST1 fixture spike had no effect"
    print("  ST1 no-look-ahead (beta/sigma at t unchanged by a spike at t): PASS")


def selftest_2_jump_boundary():
    # helper-level boundary
    sig = np.array([0.02, 0.02, 0.02])
    r = np.array([0.06, 0.0599999, -0.06])
    j = detect_jumps(r, sig, 3.0)
    assert j[0] and (not j[1]) and j[2], f"ST2 helper boundary wrong: {j}"
    assert not detect_jumps(np.array([np.nan]), np.array([0.02]), 3.0)[0], "ST2 NaN must not be a jump"
    # end-to-end boundary: set day t's return to exactly k*sigma with zero market move
    for mult, want in ((3.0000001, True), (2.9999, False)):
        df, days, syms = _synth_panel(seed=11)
        grid, _, _ = build_trading_grid(df, min_syms=3)
        p = build_panel(df, grid)
        t = 320
        gi = p.grid_idx[days[t]]
        si = p.sym_idx["SYN0"]
        sig_t = p.sigma_a[si, gi]
        beta_t = p.beta[si, gi]
        assert np.isfinite(sig_t) and np.isfinite(beta_t)
        # force r_SPY(t)=0 and r_SYN0(t) = mult*sigma  -> r_idio(t) = mult*sigma exactly
        rows = df.to_dicts()
        by = defaultdict(dict)
        for r_ in rows:
            by[r_["symbol"]][r_["date"]] = r_
        spy_prev = by["SPY"][days[t - 1]]["close"]
        by["SPY"][days[t]]["close"] = spy_prev
        by["SPY"][days[t]]["high"] = spy_prev * 1.005
        by["SPY"][days[t]]["low"] = spy_prev * 0.995
        prev = by["SYN0"][days[t - 1]]["close"]
        newc = prev * math.exp(mult * sig_t)
        by["SYN0"][days[t]]["close"] = newc
        by["SYN0"][days[t]]["high"] = newc * 1.005
        by["SYN0"][days[t]]["low"] = newc * 0.995
        df2 = pl.DataFrame(rows, infer_schema_length=None).sort(["symbol", "date"])
        grid2, _, _ = build_trading_grid(df2, min_syms=3)
        p2 = build_panel(df2, grid2)
        gi2 = p2.grid_idx[days[t]]
        si2 = p2.sym_idx["SYN0"]
        got = bool(p2.jumps[3.0][si2, gi2])
        assert abs(p2.sigma_a[si2, gi2] - sig_t) < 1e-12, "ST2 sigma must be unchanged by the day-t edit"
        assert got == want, f"ST2 end-to-end boundary mult={mult}: got jump={got} want={want}"
    print("  ST2 jump-detection boundary at K=3.0 (both sides, helper + end-to-end): PASS")


def selftest_3_classification():
    assert classify_trade(0, 0.0, 0.10) == "CYCLICAL"
    assert classify_trade(2, 0.08, 0.10) == "EVENT"          # 0.08 >= 0.5*0.10
    assert classify_trade(1, 0.02, 0.10) == "MIXED"          # 0.02 <  0.5*0.10
    assert classify_trade(1, -0.06, 0.10) == "EVENT"         # magnitude, signed-agnostic
    assert classify_trade(1, 0.05, 0.10) == "EVENT"          # exactly at the 0.5 bar -> EVENT
    assert classify_trade(0, 0.0, 0.0) == "CYCLICAL"
    print("  ST3 EVENT/CYCLICAL/MIXED classification: PASS")


def selftest_4_counterfactual_identity():
    rng = np.random.default_rng(3)
    n = 40
    d0 = date(2022, 3, 1)
    dates, d = [], d0
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    r = rng.normal(0.001, 0.02, n)
    c = 100.0 * np.exp(np.cumsum(r))
    h, l = c * 1.01, c * 0.99
    i0 = 5
    sigma = 0.02
    w_idx = [j for j in range(i0 + 1, n) if (dates[j] - dates[i0]).days <= MAXW_CALDAYS]
    t_up, t_dn, fwd = forward_touch(h, l, dates, i0, c[i0], sigma,
                                     (APEX_UP, GEN_UP), (APEX_DN, GEN_DN), MAXW_CALDAYS)
    win0, ev0 = label_win_ev(t_up, t_dn, fwd, APEX_UP, APEX_DN, ev=True)
    lbl0 = outcome_label(t_up, t_dn, fwd)
    jf = np.zeros(len(w_idx), dtype=bool)
    ri = np.zeros(len(w_idx))
    win1, ev1, lbl1, fwd1 = counterfactual_walk(dates, h, l, c, i0, sigma, w_idx, jf, ri, mu=0.0)
    assert (win0, ev0, lbl0, fwd) == (win1, ev1, lbl1, fwd1), \
        f"ST4 identity FAILED: {(win0,ev0,lbl0,fwd)} != {(win1,ev1,lbl1,fwd1)}"
    # non-empty jump set with mu equal to the replaced value is ALSO an identity
    jf2 = np.zeros(len(w_idx), dtype=bool)
    jf2[0] = True
    ri2 = np.zeros(len(w_idx))
    ri2[0] = 0.037
    win2, ev2, lbl2, _ = counterfactual_walk(dates, h, l, c, i0, sigma, w_idx, jf2, ri2, mu=0.037)
    assert (win2, ev2, lbl2) == (win0, ev0, lbl0), "ST4 mu==r_idio identity FAILED"
    print("  ST4 counterfactual re-walk identity (empty jump set + mu==r_idio): PASS")


def _explicit_cr1_t(X, y, clusters):
    """Independent, loop-based CR1 sandwich (the 'hand-computed' reference)."""
    n, k = X.shape
    XtX = X.T @ X
    bread = np.linalg.inv(XtX)
    beta = bread @ (X.T @ y)
    resid = y - X @ beta
    meat = np.zeros((k, k))
    for g in np.unique(clusters):
        m = clusters == g
        sg = (X[m] * resid[m][:, None]).sum(axis=0)
        meat += np.outer(sg, sg)
    G = len(np.unique(clusters))
    corr = (G / max(G - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = corr * (bread @ meat @ bread)
    se = math.sqrt(cov[1, 1])
    return beta[1] / se, beta[1]


def selftest_5_cr1():
    rng = np.random.default_rng(19)
    n = 200
    d = (rng.random(n) < 0.5).astype(float)
    clusters = rng.integers(0, 25, size=n)
    y = 0.3 + 0.15 * d + rng.normal(0, 0.4, n)
    X = np.column_stack([np.ones(n), d])
    ref_t, ref_b = _explicit_cr1_t(X, y, clusters)
    got = pf().clustered_ols_effect(X, y, clusters)
    assert got is not None, "ST5 clustered_ols_effect returned None on a clean fixture"
    assert math.isfinite(got["t"]), "ST5 non-finite t on a clean fixture"
    assert abs(got["t"] - ref_t) < 1e-8, f"ST5 CR1 t mismatch: {got['t']} vs hand-computed {ref_t}"
    assert abs(got["beta"] - ref_b) < 1e-10, "ST5 beta mismatch"
    # gated wrapper: finite t and the NaN-poison guard actually fires
    yb = (rng.random(n) < (0.4 + 0.1 * d)).astype(float)
    r = cr1_dummy(d, yb, clusters, np.ones(n, dtype=bool), binary=True, label="ST5_binary")
    assert r["gated"] and math.isfinite(r["t"]), f"ST5 gated binary CR1 not finite: {r}"
    ypois = y.copy()
    ypois[3] = np.nan
    r2 = cr1_dummy(d, ypois, clusters, np.ones(n, dtype=bool), binary=False, label="ST5_nanmask")
    assert r2["gated"] and math.isfinite(r2["t"]) and r2["n_used"] == n - 1, \
        f"ST5 NaN row was not masked out of the fit: {r2}"
    # share gate must refuse a degenerate bucket
    dd = np.zeros(n)
    dd[:3] = 1.0
    r3 = cr1_dummy(dd, y, clusters, np.ones(n, dtype=bool), binary=False, label="ST5_degenerate")
    assert not r3["gated"] and r3["method"] == "gated_out_share", f"ST5 share gate did not fire: {r3}"
    print("  ST5 CR1 t finite + matches an independent hand-computed sandwich; "
          "NaN-mask + share-gate fire: PASS")


def selftest_6_trading_grid():
    closure = None
    df, days, syms = _synth_panel(n_days=120, seed=5)
    # pick a mid business day and remove it for EVERY symbol
    closure = days[60]
    df2, days2, _ = _synth_panel(n_days=120, seed=5, closure_day=closure)
    grid, dropped, counts = build_trading_grid(df2, min_syms=3)
    assert closure not in set(grid), "ST6 closure day leaked into the empirical grid"
    assert len(grid) == len(days) - 1, f"ST6 grid length {len(grid)} != {len(days)-1}"
    # and the un-closed panel keeps it
    grid_full, _, _ = build_trading_grid(df, min_syms=3)
    assert closure in set(grid_full), "ST6 control: closure day should exist without the closure"
    print("  ST6 empirical trading-day index (known closure removed, weekends absent): PASS")


def selftest_7_pre_jump_window():
    """A5: the window ENDS AT and INCLUDES the entry slot, and never reaches past it."""
    T = 60
    j = np.zeros(T, dtype=bool)
    r = np.zeros(T)
    j[30] = True
    r[30] = 0.09
    j[41] = True
    r[41] = -0.05
    # entry exactly ON the jump -> inside a 1-day window
    assert pre_jump_window(j, r, 30, 1) == (True, 0.09)
    # entry 9 slots later -> inside a 10-day window (30 is slot g0-9)
    assert pre_jump_window(j, r, 39, 10)[0] is True
    # entry 10 slots later -> OUTSIDE a 10-day window
    assert pre_jump_window(j, r, 40, 10)[0] is False
    # ... but inside the 20-day window
    assert pre_jump_window(j, r, 40, 20)[0] is True
    # never looks FORWARD: at slot 29 the day-30 jump is invisible
    assert pre_jump_window(j, r, 29, 20)[0] is False
    # signed sum does not pool opposite-signed jumps into a magnitude
    hit, sgn = pre_jump_window(j, r, 41, 20)
    assert hit and abs(sgn - 0.04) < 1e-12, f"ST7 signed sum wrong: {sgn}"
    assert pre_jump_cohort(hit, sgn) == "POS"
    assert pre_jump_cohort(True, -0.02) == "NEG"
    assert pre_jump_cohort(False, 0.0) == "NOJUMP"
    assert pre_jump_cohort(None, None) is None
    print("  ST7 A5 pre-entry jump window (inclusive of entry, no forward leak, signed split): PASS")


def run_selftests():
    print("[selftest] running offline synthetic self-tests...", flush=True)
    t0 = time.time()
    selftest_1_no_lookahead()
    selftest_2_jump_boundary()
    selftest_3_classification()
    selftest_4_counterfactual_identity()
    selftest_5_cr1()
    selftest_6_trading_grid()
    selftest_7_pre_jump_window()
    print(f"SELFTEST PASS ({time.time()-t0:.1f}s)", flush=True)


# =============================================================================
# Main
# =============================================================================
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="offline synthetic self-tests only")
    ap.add_argument("--smoke", action="store_true", help="50-symbol end-to-end smoke run")
    ap.add_argument("--force", action="store_true", help="force-rebuild bulk caches")
    ap.add_argument("--no-selftest", action="store_true", help="skip self-tests on a real run")
    return ap.parse_args()


def choose_smoke_symbols(ledger: pl.DataFrame):
    """45 alphabetical + 5 shortest-history (trap 6: young listings are the
    full-scale-only failure class -- put them IN the smoke)."""
    syms = sorted(ledger["symbol"].unique().to_list())
    from database.trader_database import DB
    quoted = ",".join("'%s'" % s.replace("'", "") for s in syms)
    cur = DB.execute_sql(
        f"SELECT symbol, COUNT(*) c FROM price_history WHERE symbol IN ({quoted}) "
        f"AND date >= '{OHLC_START}' GROUP BY symbol ORDER BY c ASC"
    )
    rows = cur.fetchall()
    youngest = [r[0] for r in rows[:SMOKE_N_YOUNG]]
    alpha = [s for s in syms if s not in youngest][:SMOKE_N_ALPHA]
    picked = sorted(set(alpha) | set(youngest))
    print(f"[smoke] {len(picked)} symbols = {len(alpha)} alphabetical + {len(youngest)} "
          f"shortest-history {youngest} (bar counts "
          f"{[r[1] for r in rows[:SMOKE_N_YOUNG]]})", flush=True)
    return picked


def main():
    args = parse_args()
    t_start = time.time()
    if args.selftest:
        run_selftests()
        return
    if not args.no_selftest:
        run_selftests()

    tag = "_smoke" if args.smoke else ""
    ledger = load_ledger()
    anchor = anchor_check(ledger)     # A6: always on the COMPLETE ledger, even in smoke

    if args.smoke:
        picked = choose_smoke_symbols(ledger)
        ledger = ledger.filter(pl.col("symbol").is_in(picked))
        print(f"[smoke] ledger restricted to {ledger.height} rows", flush=True)
        ohlc = load_ohlc_symbols(picked + ["SPY"])
        min_syms = max(5, len(set(ohlc["symbol"].to_list())) // 2)
        print(f"[smoke] trading-day threshold SCALED to >={min_syms} symbols "
              f"(the LOCKED verdict value is {MIN_SYMS_TRADING_DAY}; smoke only)", flush=True)
    else:
        ohlc = load_ohlc_full(force=args.force)
        min_syms = MIN_SYMS_TRADING_DAY

    if "SPY" not in set(ohlc["symbol"].to_list()):
        raise SystemExit("[FATAL] SPY absent from price_history pull -- STOP (no substitution).")

    grid, dropped, counts = build_trading_grid(ohlc, min_syms)
    print(f"[grid] {len(grid)} empirical trading days {grid[0]}..{grid[-1]} "
          f"(dropped {dropped} dates with < {min_syms} symbols)", flush=True)
    cal_chk = calendar_overlap_check(grid)
    print(f"[grid] static-calendar overlap DIAGNOSTIC (never a source): {cal_chk}", flush=True)

    panel = build_panel(ohlc, grid)
    obs = build_obs_arrays(ohlc)

    version_id = int(ledger["version_id"][0])
    comps = load_components(version_id, force=args.force)
    before = ledger.height
    ledger = ledger.join(comps, on=["symbol", "date"], how="left")
    if ledger.height != before:
        raise AssertionError(f"[join] row count changed {before}->{ledger.height} -- "
                             f"(symbol,date) not unique in the components frame")
    n_join = int(ledger["c_bb"].is_not_null().sum())
    n_trend_mismatch = int(ledger.filter(
        pl.col("c_trend").is_not_null() & (pl.col("c_trend") != pl.col("trend").cast(pl.Float64))).height)
    print(f"[join] components joined: {n_join}/{ledger.height} rows "
          f"({100.0*n_join/max(ledger.height,1):.1f}%); trend-column mismatches={n_trend_mismatch}",
          flush=True)
    if n_trend_mismatch:
        print(f"[join] WARNING: {n_trend_mismatch} rows where the components `trend` disagrees with "
              f"the ledger `trend` -- join key or version drift; investigate before trusting M5.",
              flush=True)

    for thr in CHECK_THRESH_LADDER:
        cols = [pl.col(f"c_{c}") for c in CHECK_COMPONENTS]
        any_null = None
        for c in cols:
            any_null = c.is_null() if any_null is None else (any_null | c.is_null())
        expr = None
        for c in cols:
            term = (c >= thr).cast(pl.Int64)
            expr = term if expr is None else (expr + term)
        ledger = ledger.with_columns(
            pl.when(any_null).then(None).otherwise(expr).alias(f"n_checks_{thr}"))

    # --- A5/D5: join the PARKED peak_fakeout texture features (read-only, prefixed pf_)
    if os.path.exists(PEAK_FAKEOUT_FEATURES):
        want = [c for c in (TEXTURE_NUMERIC_COLS + TEXTURE_BUCKET_COLS)]
        pf_df = pl.read_parquet(PEAK_FAKEOUT_FEATURES)
        have = [c for c in want if c in pf_df.columns]
        missing = [c for c in want if c not in pf_df.columns]
        if have:
            sel = pf_df.select(["symbol", "date"] + have)
            dup = sel.height - sel.select(["symbol", "date"]).unique().height
            if dup:
                raise AssertionError(f"[texture] (symbol,date) not unique in "
                                     f"{PEAK_FAKEOUT_FEATURES}: {dup} dups -- STOP")
            sel = sel.rename({c: f"pf_{c}" for c in have})
            before = ledger.height
            ledger = ledger.join(sel, on=["symbol", "date"], how="left")
            if ledger.height != before:
                raise AssertionError(f"[texture] join changed row count {before}->{ledger.height}")
            cov = int(ledger[f"pf_{have[0]}"].is_not_null().sum())
            print(f"[texture] peak_fakeout features joined: cols={have} missing={missing} "
                  f"coverage={cov}/{ledger.height}", flush=True)
        else:
            print(f"[texture] NO acceleration/climax column identifiable by name in "
                  f"{PEAK_FAKEOUT_FEATURES} -- D5 will be SKIPPED (not guessed)", flush=True)
    else:
        print(f"[texture] {PEAK_FAKEOUT_FEATURES} absent -- D5 will be SKIPPED (not guessed)",
              flush=True)

    syms = sorted(ledger["symbol"].unique().to_list())
    earnings = load_earnings(syms)
    ejumps = load_earnings_jumps(syms)

    cdf = classify_ledger(ledger, panel, obs, earnings, ejumps)

    # ---- validation assertion: re-walk reproduces the STORED apex_win ----
    val = cdf.filter(pl.col("revalid_win").is_not_null() & pl.col("apex_win").is_not_null())
    n_val = val.height
    n_match = int((val["revalid_win"] == val["apex_win"]).sum())
    n_fwd_match = int((val["revalid_fwd"] == val["fwd_caldays"]).sum())
    gval = val.filter(pl.col("gen_win").is_not_null() & pl.col("revalid_gen_win").is_not_null())
    n_gen_match = int((gval["revalid_gen_win"] == gval["gen_win"]).sum())
    rate = n_match / n_val if n_val else 0.0
    print(f"[VALIDATION] re-walk reproduces stored apex_win on {n_match}/{n_val} = {rate*100:.3f}% "
          f"(bar 99%)", flush=True)
    print(f"[VALIDATION]   fwd_caldays match {n_fwd_match}/{n_val} = "
          f"{100.0*n_fwd_match/max(n_val,1):.3f}%   gen_win match {n_gen_match}/{gval.height} = "
          f"{100.0*n_gen_match/max(gval.height,1):.3f}%", flush=True)
    validation = dict(n=n_val, n_match=n_match, rate=rate, n_fwd_match=n_fwd_match,
                      fwd_rate=n_fwd_match / n_val if n_val else 0.0,
                      n_gen=gval.height, n_gen_match=n_gen_match,
                      threshold=0.99, passed=bool(rate >= 0.99))
    if not validation["passed"]:
        print("[VALIDATION] *** BELOW 0.99 -- the harness is NOT trustworthy. STOPPING. ***", flush=True)
        cdf.write_parquet(os.path.join(OUT_DIR, f"classified_ledger{tag}.parquet"))
        raise SystemExit(f"[FATAL] barrier re-walk reproduced only {rate*100:.2f}% of stored apex_win "
                         f"(need >= 99%). See PROCEDURE step: STOP and report.")

    out_parquet = os.path.join(OUT_DIR, f"classified_ledger{tag}.parquet")
    cdf.write_parquet(out_parquet)
    print(f"[out] {out_parquet} ({cdf.height} rows, {len(cdf.columns)} cols)", flush=True)

    res = compute_metrics(cdf)
    res["validation"] = validation
    res["anchor_check"] = anchor
    res["calendar_overlap_diagnostic"] = cal_chk
    res["sigma_reading"] = ("PRIMARY sigma_idio = stdev of the r_idio SERIES over [t-120,t-1] "
                            "(each past day's own PIT beta); sigma_idio_resid (single-regression "
                            "residual stdev) computed as a robustness diagnostic -- see module docstring R1")

    meta = dict(
        run=("SMOKE" if args.smoke else "FULL"),
        version_id=version_id,
        cutoff=CUTOFF.isoformat(),
        ledger=LEDGER_PATH,
        n_symbols=len(syms),
        n_trading_days=len(grid),
        trading_day_min_symbols=min_syms,
        K_primary=K_PRIMARY,
        K_ladder=str(K_LADDER),
        beta_window=BETA_WIN, min_beta_bars=MIN_BETA_BARS,
        checklist_threshold_primary=CHECK_THRESH_PRIMARY,
        bootstrap_draws=N_BOOT,
        validation_rewalk=f"{n_match}/{n_val} = {rate*100:.3f}% (bar 99%)",
        anchor_A6=("MATCH" if anchor["ok"] else "MISMATCH: " + "; ".join(anchor["disagreements"])),
        pre_jump_windows=str(PRE_JUMP_WINS), pre_jump_primary=PRE_JUMP_PRIMARY,
        wall_seconds=round(time.time() - t_start, 1),
    )
    summary = build_summary(res, meta)
    sum_path = os.path.join(_HERE, f"SUMMARY{tag}.txt")
    with open(sum_path, "w", encoding="ascii", errors="strict") as fh:
        fh.write(summary + "\n")
    json_path = os.path.join(_HERE, f"results{tag}.json")
    with open(json_path, "w", encoding="ascii", errors="strict") as fh:
        json.dump(_jsonify(dict(meta=meta, results=res)), fh, indent=1)
    print(summary)
    print(f"\n[out] {sum_path}")
    print(f"[out] {json_path}")
    print(f"[done] wall {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
