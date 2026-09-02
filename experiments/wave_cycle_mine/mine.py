"""Wave/Cycle Structure Mine -- Phase A read-only mine (NO recalc, NO MC).

Implements experiments/wave_cycle_mine/PREREGISTRATION.md: on the funded v74
75+ CALL substrate (identical to peak_fakeout's), does either the W-family
(market-calendar / OPEX-cycle phase) or the S-family (per-name path-structure
/ "waviness": variance ratio, permutation entropy, LZ complexity, Kaufman
efficiency) separate outcome cohorts, marginal-only (no peak-state
interaction)?

Usage:
    PYTHONIOENCODING=utf-8 python -u experiments/wave_cycle_mine/mine.py --smoke
    PYTHONIOENCODING=utf-8 python -u experiments/wave_cycle_mine/mine.py         # full run

--smoke: 50 symbols (45 alphabetical + 5 shortest-price-history) end-to-end.
default: full funded-ledger universe (base frame N=5,810).

Output: .cache/wave_cycle_mine/{features,cohort_stats}_v74_{tag}.parquet +
experiments/wave_cycle_mine/SUMMARY_{tag}.txt. ASCII-only stdout (Windows
cp1252 console -- no unicode; invoke with PYTHONIOENCODING=utf-8).

Base frame: .cache/peak_fakeout/features_v74_full.parquet (NOT rebuilt --
loaded as-is, N=5,810, holdout-safe, coverage-dropped by peak_fakeout already).

Statistical machinery (CR1 clustered sandwich, Bayesian hierarchical pooling,
tercile bucketing) is COPIED (not imported) into experiments/wave_cycle_mine/
stats.py from experiments/peak_fakeout/mine.py -- see that file's own
docstring for the copy-not-import rationale (bare `import features` name
collision across sibling experiment dirs). W-family calendar functions live
in calendar_features.py, a genuine SHARED import between this script and
prep_mirror.py (no name-collision risk -- see that module's docstring).
Report-only diagnostics live in diagnostics.py.

DEVIATION: the brief's "~600-900 line study script" line budget is for THIS
file; after factoring stats.py/calendar_features.py/diagnostics.py out (the
brief explicitly sanctions a local stats.py, and the same reasoning was
extended to the calendar + diagnostics layers), mine.py itself is still
~1000 lines -- over that target. Flagged rather than silently exceeded; for
comparison, peak_fakeout/mine.py (the explicit style/structure precedent),
which keeps ALL of its stats machinery inline rather than factored into a
sibling file, is 1252 lines by itself.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import date

import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import calendar_features  # noqa: E402  (local, shared with prep_mirror.py)
import diagnostics  # noqa: E402  (local; report-only, factored out for line budget)
import stats  # noqa: E402  (local; COPIED stats machinery, see module docstring)
from experiments._holdout import assert_no_holdout_leak, CUTOFF  # noqa: E402

OUT_DIR = os.path.join(_ROOT, ".cache", "wave_cycle_mine")
os.makedirs(OUT_DIR, exist_ok=True)
SUMMARY_DIR = _HERE

BASE_FRAME_PATH = os.path.join(_ROOT, ".cache", "peak_fakeout", "features_v74_full.parquet")
OHLC_PATH = os.path.join(_ROOT, ".cache", "intraday_overnight", "ohlc.parquet")
MIRROR_PATH = os.path.join(_ROOT, ".cache", "wave_cycle_mine", "mirror_2016_2020.parquet")

MIN_S_BARS = 126        # S-family young-listing guard: >=126 bars STRICTLY BEFORE entry
S1_MIN_VALID = 100      # S1 vr5: >=100 valid trailing returns required (of a 120-window)
RNG_SEED = 20260716     # NEW seed for this study (locked in PREREGISTRATION.md)
BE_WR = 0.45            # call break-even WR -- PREREGISTRATION leg 6 "Actionability" (report-only)

SMOKE_N_ALPHA = 45
SMOKE_N_YOUNG = 5

W_FEATURES = [
    ("W1_days_to_opex", "b_W1_days_to_opex"),
    ("W2_opex_week", "b_W2_opex_week"),
    ("W3_turn_of_month", "b_W3_turn_of_month"),
    ("W4_dow", "b_W4_dow"),
    ("W5_month_half", "b_W5_month_half"),
    ("W6_quarter_end_week", "b_W6_quarter_end_week"),
]
S_FEATURES = [
    ("S1_vr5", "s1_vr5", "b_S1_vr5"),
    ("S2_perm_entropy", "s2_perm_entropy", "b_S2_perm_entropy"),
    ("S3_lz76", "s3_lz76", "b_S3_lz76"),
    ("S4_kaufman_er20", "s4_kaufman_er20", "b_S4_kaufman_er20"),
]


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="50-symbol end-to-end smoke run")
    return ap.parse_args()


# =============================================================================
# Load + assemble
# =============================================================================
def choose_smoke_symbols(df: pl.DataFrame, bar_counts: dict) -> list:
    all_syms = sorted(df["symbol"].unique().to_list())
    alpha = all_syms[:SMOKE_N_ALPHA]
    by_len = sorted(all_syms, key=lambda s: bar_counts.get(s, 0))
    young = by_len[:SMOKE_N_YOUNG]
    chosen = sorted(set(alpha) | set(young))
    print(f"[smoke] {len(alpha)} alphabetical + {len(young)} shortest-history "
          f"({young}) -> {len(chosen)} unique symbols", flush=True)
    return chosen


def load_close_arrays(symbols: set) -> dict:
    """symbol -> dict(dates=list[date], closes=ndarray, idx={date:i}).
    Loaded ONCE from .cache/intraday_overnight/ohlc.parquet, grouped by
    symbol -- same source/convention as peak_fakeout/mine.py's own
    load_ohlc_arrays (dates cast from string, sorted ascending)."""
    t0 = time.time()
    df = pl.read_parquet(OHLC_PATH, columns=["symbol", "date", "close"])
    df = df.with_columns(pl.col("date").str.to_date().alias("date"))
    df = df.filter(pl.col("symbol").is_in(list(symbols))).sort(["symbol", "date"])
    out = {}
    for sym, g in df.group_by("symbol", maintain_order=True):
        sym = sym[0] if isinstance(sym, tuple) else sym
        dates = g["date"].to_list()
        closes = g["close"].to_numpy().astype(np.float64)
        out[sym] = dict(dates=dates, closes=closes, idx={d: i for i, d in enumerate(dates)})
    print(f"[ohlc] loaded {len(out)}/{len(symbols)} symbols from {OHLC_PATH} "
          f"in {time.time()-t0:.1f}s", flush=True)
    return out


# =============================================================================
# S-family: causal (PIT) per-name path-structure features
# =============================================================================
def causal_log_returns(closes: np.ndarray) -> np.ndarray:
    """r[i] = ln(closes[i]/closes[i-1]); r[0] undefined (NaN). Computed ONCE
    per symbol over the FULL history -- causality is enforced entirely by the
    point-functions below only ever reading indices <= idx, never by
    truncating this array (see ST1 selftest_lookahead, which corrupts the
    array's FUTURE entries and checks the point-functions are blind to it)."""
    n = len(closes)
    r = np.full(n, np.nan)
    if n > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            r[1:] = np.log(closes[1:] / closes[:-1])
    return r


def s1_vr5(rets: np.ndarray, idx: int) -> float:
    """Lo-MacKinlay variance ratio on trailing 120 returns ending at idx
    (>=100 valid required): VR = Var(overlapping 5-day sums)/(5*Var(1-day)),
    unbiased (ddof=1) variances, demeaned."""
    lo = idx - 119
    if lo < 1:
        return float("nan")
    window = rets[lo: idx + 1]
    valid = window[~np.isnan(window)]
    if valid.size < S1_MIN_VALID:
        return float("nan")
    mu = valid.mean()
    rc = valid - mu
    var1 = np.var(rc, ddof=1)
    if not np.isfinite(var1) or var1 <= 0 or valid.size < 5:
        return float("nan")
    csum = np.concatenate([[0.0], np.cumsum(rc)])
    sums5 = csum[5:] - csum[:-5]
    if sums5.size < 2:
        return float("nan")
    var5 = np.var(sums5, ddof=1)
    vr = var5 / (5.0 * var1)
    return float(vr) if np.isfinite(vr) else float("nan")


def s2_perm_entropy(rets: np.ndarray, idx: int, m: int = 3, tau: int = 1, window_n: int = 60) -> float:
    """Permutation entropy, order m=3, tau=1, on trailing 60 returns (58
    patterns), stable-argsort tie-break, H = -sum p ln p / ln(6)."""
    lo = idx - (window_n - 1)
    if lo < 1:
        return float("nan")
    r = rets[lo: idx + 1]
    if r.size != window_n or np.isnan(r).any():
        return float("nan")
    npat = window_n - (m - 1) * tau
    if npat < 1:
        return float("nan")
    counts: dict = {}
    for i in range(npat):
        w = r[i: i + m * tau: tau]
        order = tuple(np.argsort(w, kind="stable"))
        counts[order] = counts.get(order, 0) + 1
    total = sum(counts.values())
    h = 0.0
    for c in counts.values():
        p = c / total
        h -= p * math.log(p)
    denom = math.log(math.factorial(m))
    return float(h / denom) if denom > 0 else float("nan")


def _lz76_complexity(bits: list) -> int:
    """Kaspar-Schuster (1976) LZ complexity of a binary sequence (list of 0/1
    ints). Standard i/k/hist_len pointer algorithm -- validated standalone
    against known-shape cases before embedding (constant seq -> C=2, random
    seq -> C ~ n/log2(n)); see final report for the validation transcript."""
    s = bits
    n = len(s)
    if n == 0:
        return 0
    i, k, hist_len = 0, 1, 1
    c = 1
    k_max = 1
    while True:
        if s[i + k - 1] == s[hist_len + k - 1]:
            k += 1
            if hist_len + k > n:
                c += 1
                break
        else:
            if k > k_max:
                k_max = k
            i += 1
            if i == hist_len:
                c += 1
                hist_len += k_max
                if hist_len + 1 > n:
                    break
                i = 0
                k = 1
                k_max = 1
            else:
                k = 1
    return c


def s3_lz76(rets: np.ndarray, idx: int, window_n: int = 60) -> float:
    """LZ76 complexity of the binary sign sequence (r>0 -> 1 else 0) over
    trailing 60 returns, normalized c = C*log2(n)/n (Kaspar-Schuster)."""
    lo = idx - (window_n - 1)
    if lo < 1:
        return float("nan")
    r = rets[lo: idx + 1]
    if r.size != window_n or np.isnan(r).any():
        return float("nan")
    bits = [1 if x > 0 else 0 for x in r]
    C = _lz76_complexity(bits)
    n = window_n
    return float(C * math.log2(n) / n)


def s4_kaufman_er20(closes: np.ndarray, idx: int, window: int = 20) -> float:
    """|P_t - P_{t-20}| / sum|P_i - P_{i-1}| over the trailing 20 bars
    (raw prices), range [0,1]."""
    lo = idx - window
    if lo < 0:
        return float("nan")
    net_change = abs(closes[idx] - closes[lo])
    path = float(np.abs(np.diff(closes[lo: idx + 1])).sum())
    if path <= 0 or not np.isfinite(path):
        return float("nan")
    return float(net_change / path)


def build_s_features(df: pl.DataFrame, close_arrays: dict):
    """Adds s1_vr5/s2_perm_entropy/s3_lz76/s4_kaufman_er20 (Float64, NaN
    sentinel for insufficient data) + _s_n_before_entry (bars strictly before
    entry) + _s_young_listing (bool, idx < MIN_S_BARS). rets computed ONCE
    per symbol (causal_log_returns over the FULL history) and reused --
    causality is enforced by the point-functions' own idx-bounded slicing,
    not by truncating the array (see module docstring)."""
    t0 = time.time()
    n = df.height
    dates = df["date"].to_list()
    syms = df["symbol"].to_list()

    rets_by_symbol = {sym: causal_log_returns(v["closes"]) for sym, v in close_arrays.items()}

    s1 = [float("nan")] * n
    s2 = [float("nan")] * n
    s3 = [float("nan")] * n
    s4 = [float("nan")] * n
    n_before = [0] * n
    young = [True] * n
    n_no_ohlc = 0
    for i in range(n):
        sym, d = syms[i], dates[i]
        arr = close_arrays.get(sym)
        if arr is None:
            n_no_ohlc += 1
            continue
        idx = arr["idx"].get(d)
        if idx is None:
            n_no_ohlc += 1
            continue
        n_before[i] = idx
        if idx < MIN_S_BARS:
            continue
        young[i] = False
        rets = rets_by_symbol[sym]
        closes = arr["closes"]
        s1[i] = s1_vr5(rets, idx)
        s2[i] = s2_perm_entropy(rets, idx)
        s3[i] = s3_lz76(rets, idx)
        s4[i] = s4_kaufman_er20(closes, idx)

    out = df.with_columns([
        pl.Series("s1_vr5", s1, dtype=pl.Float64),
        pl.Series("s2_perm_entropy", s2, dtype=pl.Float64),
        pl.Series("s3_lz76", s3, dtype=pl.Float64),
        pl.Series("s4_kaufman_er20", s4, dtype=pl.Float64),
        pl.Series("_s_n_before_entry", n_before, dtype=pl.Int64),
        pl.Series("_s_young_listing", young, dtype=pl.Boolean),
    ])
    print(f"[s_features] computed s1-s4 for {n} rows (young_listing<{MIN_S_BARS}bars is the null-guard; "
          f"no_ohlc/date_miss={n_no_ohlc}) in {time.time()-t0:.1f}s", flush=True)
    return out, rets_by_symbol


# =============================================================================
# Self-tests (run FIRST, hard-fail the run on any failure)
# =============================================================================
def selftest_lookahead(df: pl.DataFrame, close_arrays: dict, rets_by_symbol: dict,
                        n_sample: int = 50, seed: int = 7) -> bool:
    """Sample (symbol,entry) rows; replace all bars AFTER entry with
    shuffled+jittered garbage; recompute S-features; must be IDENTICAL."""
    rng = np.random.default_rng(seed)
    eligible = df.filter(pl.col("_s_n_before_entry") >= MIN_S_BARS)
    if eligible.height == 0:
        print("[selftest ST1 lookahead] no eligible rows -> FAIL")
        return False
    take = min(n_sample, eligible.height)
    pick = rng.choice(eligible.height, size=take, replace=False).tolist()
    sample = eligible[pick]
    mismatches = []
    checked = 0
    for r in sample.iter_rows(named=True):
        sym, d = r["symbol"], r["date"]
        arr = close_arrays.get(sym)
        if arr is None:
            continue
        idx = arr["idx"].get(d)
        if idx is None:
            continue
        closes_full = arr["closes"]
        tail_len = len(closes_full) - (idx + 1)
        closes_corrupt = closes_full.copy()
        if tail_len > 0:
            garbage = rng.normal(loc=float(np.nanmean(closes_full)),
                                  scale=float(np.nanstd(closes_full)) + 1.0, size=tail_len)
            rng.shuffle(garbage)
            closes_corrupt[idx + 1:] = garbage
        rets_full = rets_by_symbol[sym]
        rets_corrupt = causal_log_returns(closes_corrupt)
        vals_full = (s1_vr5(rets_full, idx), s2_perm_entropy(rets_full, idx),
                     s3_lz76(rets_full, idx), s4_kaufman_er20(closes_full, idx))
        vals_corrupt = (s1_vr5(rets_corrupt, idx), s2_perm_entropy(rets_corrupt, idx),
                         s3_lz76(rets_corrupt, idx), s4_kaufman_er20(closes_corrupt, idx))
        for name, a, b in zip(("s1_vr5", "s2_perm_entropy", "s3_lz76", "s4_kaufman_er20"),
                               vals_full, vals_corrupt):
            checked += 1
            a_nan, b_nan = math.isnan(a), math.isnan(b)
            if a_nan and b_nan:
                continue
            if a_nan != b_nan or abs(a - b) > 1e-9:
                mismatches.append((sym, d, name, a, b))
    ok = checked > 0 and len(mismatches) == 0
    print(f"[selftest ST1 lookahead] pairs={take} feature_checks={checked} mismatches={len(mismatches)} "
          f"-> {'PASS' if ok else 'FAIL'}")
    for m in mismatches[:8]:
        print(f"    LOOK-AHEAD LEAK sym={m[0]} date={m[1]} col={m[2]} full={m[3]} corrupt={m[4]}")
    return ok


def selftest_base_rate(df: pl.DataFrame, smoke: bool) -> bool:
    """Pooled apex WR 70.1%/plunge 23.3%, N=5,810 (full mode only, +/-0.2pp).
    # DEVIATION: PREREGISTRATION.md / the orchestrator brief specify this
    # tight tolerance "(full mode only)" and do not state a --smoke-mode
    # tolerance. Mirrors experiments/peak_fakeout/mine.py's OWN
    # selftest_base_rate resolution of the identical tension (same base
    # frame, same predicate): a --smoke 50-symbol (~400 row), non-random
    # (45-alphabetical + 5-youngest) slice moves the aggregate several points
    # on sampling composition alone, so the tight bar is not meaningful there.
    # Widen to +/-5pp in smoke mode -- still a real gross-error catch
    # (inverted labels, wrong outcome column, broken join blow past 5pp),
    # not the full-population bar."""
    y = df["apex_win"].to_numpy().astype(float)
    n = int(np.sum(~np.isnan(y)))
    wr = float(np.nanmean(y)) if n else float("nan")
    ev = df["apex_ev"].to_numpy()
    plunge = float(np.mean(ev == -0.7)) if len(ev) else float("nan")
    tol = 0.05 if smoke else 0.002
    ok = (n > 0 and abs(wr - 0.701) <= tol and abs(plunge - 0.233) <= tol)
    print(f"[selftest ST2 base_rate] N={n} apex_wr={wr:.4f} (expect 0.701+/-{tol:.3f}) "
          f"plunge={plunge:.4f} (expect 0.233+/-{tol:.3f}) smoke={smoke} -> {'PASS' if ok else 'FAIL'}")
    if not smoke:
        ok_n = (n == 5810)
        print(f"[selftest ST2 base_rate] full-mode N check: N={n} expect=5810 -> {'PASS' if ok_n else 'FAIL'}")
        ok = ok and ok_n
    return ok


def selftest_holdout(df: pl.DataFrame) -> bool:
    try:
        assert_no_holdout_leak(df, context="wave_cycle_mine feat_frame")
    except AssertionError as e:
        print(f"[selftest ST3 holdout] FAIL: {e}")
        return False
    max_d = df["date"].max() if df.height else None
    print(f"[selftest ST3 holdout] max_date={max_d} cutoff={CUTOFF.isoformat()} -> PASS")
    return True


def selftest_finX() -> bool:
    rng = np.random.default_rng(42)
    n = 200
    d = (rng.random(n) < 0.5).astype(float)
    x1 = rng.normal(0, 1, n)
    y = (rng.random(n) < (0.5 + 0.1 * d)).astype(float)
    X = np.column_stack([np.ones(n), d, x1])
    bad_idx = rng.choice(n, size=15, replace=False)
    X_bad = X.copy()
    X_bad[bad_idx, 2] = np.nan
    clusters = rng.integers(0, 20, n)

    finX = np.isfinite(X_bad).all(axis=1)
    n_expected = int(finX.sum())
    res = stats.fit_clustered(X_bad[finX], y[finX], clusters[finX], min_n_side=stats.MIN_CELL_CONTROL_SIDE)
    ok = (res is not None and res["n_used"] == n_expected and n_expected < n
          and math.isfinite(res["t"]))
    print(f"[selftest ST4 finX] n_total={n} injected_nan_rows={len(set(bad_idx.tolist()))} "
          f"n_used_after_drop={n_expected} t={res['t'] if res else None} -> {'PASS' if ok else 'FAIL'}")
    return ok


def selftest_young_listing(df: pl.DataFrame) -> bool:
    young = df.filter(pl.col("_s_young_listing"))
    if young.height == 0:
        print(f"[selftest ST5 young_listing] no young-listing rows (<{MIN_S_BARS} bars) in this run's "
              f"universe -> PASS (vacuous)")
        return True
    bad = 0
    for c in ("s1_vr5", "s2_perm_entropy", "s3_lz76", "s4_kaufman_er20"):
        bad += young.filter(pl.col(c).is_not_null()).height
    ok = bad == 0
    print(f"[selftest ST5 young_listing] {young.height} young-listing rows (<{MIN_S_BARS} bars) present; "
          f"non-null S-feature values found={bad} (expect 0) -> {'PASS' if ok else 'FAIL'}")
    return ok


def selftest_calendar(cal: "calendar_features.WCalendar") -> bool:
    """ST6: hardcoded calendar unit-test asserts, locked in PREREGISTRATION.md,
    plus two ERA asserts (coordinator patch 2026-07-16) that specifically
    exercise the EMPIRICAL trading-day index in the pre-2023 region the
    static NYSE_HOLIDAYS table does not cover: April 2019 OPEX must shift
    off Good Friday 2019-04-19 (which WAS the 3rd Friday) to 2019-04-18,
    and 2018-12-05 (G.H.W. Bush national day of mourning, a one-off full
    closure) must not be a trading day -- December 2018 TOM/rank logic then
    simply reflects that via the shared per-month rank derivation."""
    opex_by_month = {}
    for od in cal.opex_days:
        opex_by_month[(od.year, od.month)] = od
    apr25 = opex_by_month.get((2025, 4))
    jun25 = opex_by_month.get((2025, 6))
    apr19 = opex_by_month.get((2019, 4))

    checks = [
        ("april_2025_opex==2025-04-17", apr25 == date(2025, 4, 17), apr25),
        ("june_2025_opex==2025-06-20", jun25 == date(2025, 6, 20), jun25),
        ("days_to_opex==0_on_opex_day",
         (cal.days_to_opex(apr25) == 0) if apr25 else False,
         cal.days_to_opex(apr25) if apr25 else None),
        ("tom_2024-12-30==True", cal.turn_of_month(date(2024, 12, 30)) is True, None),
        ("tom_2024-12-31==True", cal.turn_of_month(date(2024, 12, 31)) is True, None),
        ("tom_2025-01-02==True", cal.turn_of_month(date(2025, 1, 2)) is True, None),
        ("qew_2025-03-31==True", cal.quarter_end_week(date(2025, 3, 31)) is True, None),
        ("qew_2025-03-21==False", cal.quarter_end_week(date(2025, 3, 21)) is False, None),
        ("era:april_2019_opex==2019-04-18", apr19 == date(2019, 4, 18), apr19),
        ("era:2018-12-05_not_trading_day",
         date(2018, 12, 5) not in cal.index_of, None),
    ]
    ok = all(c[1] for c in checks)
    print(f"[selftest ST6 calendar] {len(checks)} hardcoded asserts -> {'PASS' if ok else 'FAIL'}")
    for name, passed, val in checks:
        extra = f" (got {val})" if val is not None else ""
        print(f"    {name}: {'PASS' if passed else 'FAIL'}{extra}")
    return ok


# =============================================================================
# Era-mirror leg5 (W-family PREREGISTRATION leg 5: 2016-2020 era-independence
# replication; locked fallback if the mirror is missing -- caps at NEAR_MISS).
# =============================================================================
def build_era_lookup(cal: "calendar_features.WCalendar"):
    """Returns (lookup, available). lookup: (feature_label, cell_str) ->
    dict(d_wr_sign, d_plunge_sign, n_cell), computed on the 2016-2020
    era-mirror slice (recomputing W-bucket columns via the SAME shared
    calendar_features module + the SAME WCalendar instance as the main
    frame). (None, False) if the mirror parquet is missing/empty at runtime
    -- mine.py must still run in that case (era_repl_sign='NA', W-family
    capped at NEAR_MISS -- see evaluate_gate)."""
    if not os.path.exists(MIRROR_PATH):
        print(f"[era_mirror] NOT FOUND at {MIRROR_PATH} -- W-family leg5 falls back to the repl_win "
              f"breadth check, capped at NEAR_MISS (PREREGISTRATION.md locked fallback).", flush=True)
        return None, False
    mdf = pl.read_parquet(MIRROR_PATH)
    if mdf.height == 0:
        print(f"[era_mirror] {MIRROR_PATH} exists but is EMPTY -- same fallback as missing.", flush=True)
        return None, False
    mdf = calendar_features.add_w_columns(mdf, cal)
    y_wr = mdf["win"].to_numpy().astype(float)
    has_ev = "ev" in mdf.columns
    y_plunge = (mdf["ev"].to_numpy() == -0.70).astype(float) if has_ev else np.full(mdf.height, np.nan)
    base_wr = float(np.nanmean(y_wr))
    base_plunge = float(np.nanmean(y_plunge)) if has_ev else float("nan")
    print(f"[era_mirror] loaded {mdf.height} rows base_wr={base_wr:.4f} "
          f"base_plunge={base_plunge:.4f} has_ev={has_ev}", flush=True)

    out = {}
    for feat_label, bcol in W_FEATURES:
        if bcol not in mdf.columns:
            continue
        bvals = mdf[bcol].to_numpy()
        cats = sorted({v for v in bvals.tolist() if v is not None})
        for cell in cats:
            mask = (bvals == cell)
            n_cell = int(mask.sum())
            entry = dict(n_cell=n_cell, d_wr_sign=None, d_plunge_sign=None)
            if n_cell >= stats.MIN_N_WINDOW:
                wr_cell = float(np.nanmean(y_wr[mask]))
                entry["d_wr_sign"] = (wr_cell - base_wr) > 0
                if has_ev:
                    pl_cell = float(np.nanmean(y_plunge[mask]))
                    entry["d_plunge_sign"] = (pl_cell - base_plunge) > 0
            out[(feat_label, str(cell))] = entry
    return out, True


# =============================================================================
# Cohort scan (marginal only -- NO peak-state interaction, per
# PREREGISTRATION.md "Cells (marginal only -- NO peak-state interaction)").
# =============================================================================
def precompute_base(n, y_wr, y_plunge, y_ev, y_repl_wr, window_arr):
    full = np.ones(n, dtype=bool)
    wr_wins, wr_n = stats._sumcount(full, y_wr)
    pl_wins, pl_n = stats._sumcount(full, y_plunge)
    ev_mean, ev_se, ev_n = stats._meanse(full, y_ev)
    repl_wins, repl_n = stats._sumcount(full, y_repl_wr)
    by_window = {}
    for lbl in stats.WINDOW_ORDER:
        wmask = (window_arr == lbl)
        w_wr_wins, w_wr_n = stats._sumcount(wmask, y_wr)
        w_pl_wins, w_pl_n = stats._sumcount(wmask, y_plunge)
        w_ev_mean, w_ev_se, w_ev_n = stats._meanse(wmask, y_ev)
        by_window[lbl] = dict(wr=(w_wr_wins, w_wr_n), plunge=(w_pl_wins, w_pl_n),
                               ev=(w_ev_mean, w_ev_se, w_ev_n))
    return dict(
        wr=(wr_wins / wr_n if wr_n else float("nan")), wr_n=wr_n,
        plunge=(pl_wins / pl_n if pl_n else float("nan")), plunge_n=pl_n,
        ev=ev_mean, ev_n=ev_n,
        repl_wr=(repl_wins / repl_n if repl_n else float("nan")), repl_n=repl_n,
        by_window=by_window,
    )


def _per_window_list(bucket_mask, window_arr, y, by_window_base, metric):
    out = []
    for lbl in stats.WINDOW_ORDER:
        wmask = bucket_mask & (window_arr == lbl)
        if metric == "wr":
            wins, n = stats._sumcount(wmask, y)
            base_wins, base_n = by_window_base[lbl]["wr"]
            delta = ((wins / n - base_wins / base_n)
                     if (n >= stats.MIN_N_WINDOW and base_n >= stats.MIN_N_WINDOW) else None)
            out.append(dict(label=lbl, n=n, wins=wins, base_n=base_n, base_wins=base_wins, delta=delta))
        else:
            mean, se, n = stats._meanse(wmask, y)
            base_mean, base_se, base_n = by_window_base[lbl]["ev"]
            delta = ((mean - base_mean)
                     if (n >= stats.MIN_N_WINDOW and base_n >= stats.MIN_N_WINDOW) else None)
            out.append(dict(label=lbl, n=n, mean=mean, se=se, base_n=base_n, base_mean=base_mean,
                             base_se=base_se, delta=delta))
    return out


def compute_cell(family, feat_label, cell_value, bucket_mask, base, y_wr, y_plunge, y_ev, y_repl_wr,
                  X_controls_full, date_cluster, window_arr, rng):
    """One cohort row: cell-vs-REST of the WHOLE substrate (marginal only)."""
    n = len(bucket_mask)
    ncell = int(bucket_mask.sum())
    resolved_wr = int(np.sum(~np.isnan(y_wr[bucket_mask])))
    if ncell < stats.MIN_N_POOLED or resolved_wr < stats.MIN_N_POOLED:
        return None

    wr_cell = float(np.nansum(y_wr[bucket_mask])) / resolved_wr
    plunge_cell = float(np.nanmean(y_plunge[bucket_mask]))
    ev_cell = float(np.nanmean(y_ev[bucket_mask]))

    base_wr, base_plunge, base_ev = base["wr"], base["plunge"], base["ev"]
    d_wr_pp = (wr_cell - base_wr) * 100.0 if not math.isnan(base_wr) else float("nan")
    d_plunge_pp = (plunge_cell - base_plunge) * 100.0 if not math.isnan(base_plunge) else float("nan")
    d_ev = (ev_cell - base_ev) if not math.isnan(base_ev) else float("nan")

    z_raw_wr = stats._prop_z(wr_cell, base_wr, resolved_wr)
    z_raw_plunge = stats._prop_z(plunge_cell, base_plunge, ncell)

    full_sample = np.ones(n, dtype=bool)
    X_bucket = np.column_stack([np.ones(n), bucket_mask.astype(float)])
    cell_wr = stats.clustered_cell_effect(X_bucket, y_wr, date_cluster, full_sample)
    cell_plunge = stats.clustered_cell_effect(X_bucket, y_plunge, date_cluster, full_sample)

    Xc = np.column_stack([np.ones(n), bucket_mask.astype(float), X_controls_full])
    ctl_wr = stats.clustered_cell_effect(Xc, y_wr, date_cluster, full_sample)
    ctl_plunge = stats.clustered_cell_effect(Xc, y_plunge, date_cluster, full_sample)

    pw_wr = _per_window_list(bucket_mask, window_arr, y_wr, base["by_window"], "wr")
    pw_plunge = _per_window_list(bucket_mask, window_arr, y_plunge, base["by_window"], "wr")
    b_wr = stats.bayes_layer(pw_wr, "wr", rng)
    b_plunge = stats.bayes_layer(pw_plunge, "wr", rng)
    p_sign_wr = b_wr["P_sign_consistent"] if b_wr else float("nan")
    p_sign_plunge = b_plunge["P_sign_consistent"] if b_plunge else float("nan")
    signs = stats.window_signs(pw_wr)

    repl_resolved = int(np.sum(~np.isnan(y_repl_wr[bucket_mask])))
    repl_same_sign = None
    if repl_resolved >= stats.MIN_N_POOLED and not math.isnan(base["repl_wr"]) and not math.isnan(d_wr_pp):
        repl_wr_cell = float(np.nansum(y_repl_wr[bucket_mask])) / repl_resolved
        d_repl = repl_wr_cell - base["repl_wr"]
        repl_same_sign = (d_repl > 0) == (d_wr_pp > 0)

    prevalence = ncell / n if n else float("nan")
    actionable_below_be = (wr_cell < BE_WR) if not math.isnan(wr_cell) else None
    actionable_ev_spread = (abs(d_ev) >= 0.03) if not math.isnan(d_ev) else None

    return dict(
        family=family, feature=feat_label, cell=str(cell_value),
        n=ncell, resolved_n=resolved_wr, prevalence=prevalence,
        apex_wr=wr_cell, d_wr_pp=d_wr_pp, plunge_rate=plunge_cell, d_plunge_pp=d_plunge_pp,
        apex_ev=ev_cell, d_ev=d_ev,
        z_raw_wr=z_raw_wr, z_raw_plunge=z_raw_plunge,
        z_clust_wr=cell_wr["t"], z_clust_plunge=cell_plunge["t"],
        t_controlled_wr=ctl_wr["t"], t_controlled_plunge=ctl_plunge["t"],
        t_controlled_wr_n_used=ctl_wr["n_used"], t_controlled_wr_method=ctl_wr["method"],
        t_controlled_plunge_n_used=ctl_plunge["n_used"], t_controlled_plunge_method=ctl_plunge["method"],
        P_sign_consistent_wr=p_sign_wr, P_sign_consistent_plunge=p_sign_plunge,
        signs=signs,
        repl_resolved_n=repl_resolved, repl_same_sign=repl_same_sign,
        actionable_below_be=actionable_below_be, actionable_ev_spread=actionable_ev_spread,
    )


def run_cohort_scan(df: pl.DataFrame, rng):
    n = df.height
    y_wr = df["apex_win"].to_numpy().astype(float)
    y_ev = df["apex_ev"].to_numpy().astype(float)
    y_plunge = (df["apex_ev"].to_numpy() == -0.7).astype(float)
    y_repl_wr = (df["repl_win"].to_numpy().astype(float) if "repl_win" in df.columns
                 else np.full(n, np.nan))

    overall_z = stats._zscore_nan(df["overall"].to_numpy().astype(float))
    trend_z = stats._zscore_nan(df["trend"].to_numpy().astype(float))
    vol20_z = stats._zscore_nan(df["vol20"].to_numpy().astype(float))
    runup20_z = stats._zscore_nan(df["runup20_sig"].to_numpy().astype(float))
    ema50_z = stats._zscore_nan(df["control_pct_ema50"].to_numpy().astype(float))
    with np.errstate(divide="ignore", invalid="ignore"):
        ln_mcap = np.log(df["pit_mcap_b"].to_numpy().astype(float))
    ln_mcap = np.where(np.isfinite(ln_mcap), ln_mcap, np.nan)
    ln_mcap_z = stats._zscore_nan(ln_mcap)
    vix_z = stats._zscore_nan(df["vix_close"].to_numpy().astype(float))
    # PREREGISTRATION controlled-fit controls, in the order given:
    # [overall, trend, vol20, runup20_sig, control_pct_ema50, ln(pit_mcap_b), vix_close]
    X_controls_full = np.column_stack([overall_z, trend_z, vol20_z, runup20_z, ema50_z, ln_mcap_z, vix_z])

    window_arr = df["window"].to_numpy()
    dates_arr = df["date"].to_numpy()
    _, date_cluster = np.unique(dates_arr, return_inverse=True)

    base = precompute_base(n, y_wr, y_plunge, y_ev, y_repl_wr, window_arr)

    results = []
    t0 = time.time()
    n_attempted = 0
    all_features = [("W", lbl, bcol) for lbl, bcol in W_FEATURES] + \
                   [("S", lbl, bcol) for lbl, _, bcol in S_FEATURES]
    for family, feat_label, bcol in all_features:
        if bcol not in df.columns:
            print(f"[cohort] WARNING: bucket column {bcol!r} for {feat_label} missing -- skipping")
            continue
        bvals = df[bcol].to_numpy()
        cats = sorted({v for v in bvals.tolist() if v is not None})
        for cell in cats:
            n_attempted += 1
            bucket_mask = (bvals == cell)
            row = compute_cell(family, feat_label, cell, bucket_mask, base, y_wr, y_plunge, y_ev,
                                y_repl_wr, X_controls_full, date_cluster, window_arr, rng)
            if row is not None:
                results.append(row)

    print(f"[cohort] {n_attempted} cells enumerated ({len(all_features)} feature-columns), "
          f"{len(results)} scored (>=MIN_N_POOLED={stats.MIN_N_POOLED}) in {time.time()-t0:.1f}s", flush=True)
    if not results:
        return pl.DataFrame({"family": [], "feature": [], "cell": [], "n": []}), base
    return pl.DataFrame(results, infer_schema_length=None), base


# =============================================================================
# Gate evaluation (5 gating legs, PREREGISTRATION "Finding bar"; leg 6
# Actionability is reported on every row via actionable_below_be/
# actionable_ev_spread above, but never gates).
# =============================================================================
def evaluate_gate(row: dict, era_lookup, era_available) -> dict:
    z_wr, z_pl = row["z_clust_wr"], row["z_clust_plunge"]
    leg1_wr = (not math.isnan(z_wr)) and abs(z_wr) >= 3.0
    leg1_pl = (not math.isnan(z_pl)) and abs(z_pl) >= 3.0
    leg1 = leg1_wr or leg1_pl

    if leg1_wr and leg1_pl:
        active = "wr" if abs(z_wr) >= abs(z_pl) else "plunge"
    elif leg1_wr:
        active = "wr"
    elif leg1_pl:
        active = "plunge"
    else:
        active = ("wr" if abs(z_wr if not math.isnan(z_wr) else 0.0)
                  >= abs(z_pl if not math.isnan(z_pl) else 0.0) else "plunge")

    t_ctl = row[f"t_controlled_{active}"]
    leg2 = (not math.isnan(t_ctl)) and abs(t_ctl) >= 2.5

    psign = row[f"P_sign_consistent_{active}"]
    leg3 = (psign is not None) and (not math.isnan(psign)) and psign > 0.90

    leg4 = (row["n"] >= 150) and (not math.isnan(row["prevalence"])) and (0.05 <= row["prevalence"] <= 0.95)

    d_active_pp = row["d_wr_pp"] if active == "wr" else row["d_plunge_pp"]
    era_capped = False
    era_repl_sign = None

    if row["family"] == "S":
        leg5 = row.get("repl_same_sign") is True
    else:  # W-family
        if era_available:
            entry = era_lookup.get((row["feature"], row["cell"]))
            era_sign_field = "d_wr_sign" if active == "wr" else "d_plunge_sign"
            era_side_sign = entry.get(era_sign_field) if entry else None
            if (era_side_sign is None or d_active_pp is None
                    or (isinstance(d_active_pp, float) and math.isnan(d_active_pp))):
                era_repl_sign = None
            else:
                era_repl_sign = (era_side_sign == (d_active_pp > 0))
            leg5 = era_repl_sign is True
        else:
            # PREREGISTRATION locked fallback: repl_win breadth check
            # substitutes for the era slice, but the cell is capped at
            # NEAR_MISS regardless of leg count (calendar effects need
            # era-independence; breadth replication shares the same dates).
            leg5 = row.get("repl_same_sign") is True
            era_capped = True

    legs = [leg1, leg2, leg3, leg4, leg5]
    n_legs = sum(bool(x) for x in legs)
    if era_capped:
        verdict = "NEAR_MISS" if n_legs >= 4 else "null"
    else:
        verdict = "FINDING" if n_legs == 5 else ("NEAR_MISS" if n_legs >= 4 else "null")

    return dict(active_outcome=active, leg1_z_clust=leg1, leg2_t_controlled=leg2, leg3_p_sign=leg3,
                leg4_n_prevalence=leg4, leg5_replication=leg5, era_repl_sign=era_repl_sign,
                era_capped=era_capped, n_legs_passed=n_legs, verdict=verdict)


def _f(v, fmt="+.2f", na="  n/a"):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return na
    try:
        return format(v, fmt)
    except (TypeError, ValueError):
        return str(v)


# Diagnostics (report-only, never gated) live in diagnostics.py -- see that
# module for diagnostic_supply_vs_baseline / diagnostic_s_correlation /
# diagnostic_young_listing / diagnostic_per_window_base.


# =============================================================================
# SUMMARY block
# =============================================================================
def format_summary(tag, df, cohort_df, gate_df, selftest_results, selftest_names,
                    era_available, cal, smoke, t_start) -> str:
    lines = ["==== SUMMARY ===="]

    def p(s=""):
        lines.append(s)

    p("-- (1) self-test results --")
    for name, ok in zip(selftest_names, selftest_results):
        p(f"selftest {name}: {'PASS' if ok else 'FAIL'}")
    p(f"mode={'SMOKE' if smoke else 'FULL'} version=v74 tag={tag}")
    p(f"era_mirror_available={era_available}")

    p("")
    p("-- (2) base rates + N --")
    p(f"N={df.height}")
    if df.height:
        y_wr = df["apex_win"].to_numpy().astype(float)
        y_ev = df["apex_ev"].to_numpy()
        base_wr = float(np.nanmean(y_wr))
        base_plunge = float(np.mean(y_ev == -0.7))
        p(f"base_apex_wr={base_wr:.4f} (expect ~0.701) base_plunge_rate={base_plunge:.4f} (expect ~0.233)")
        windows_present = sorted(set(df["window"].drop_nulls().to_list()))
        p(f"windows_present={windows_present}")
        p(diagnostics.diagnostic_per_window_base(df))

    if cohort_df.height == 0 or "n" not in cohort_df.columns:
        p("")
        p(f"cohort table: EMPTY (no cell reached MIN_N_POOLED={stats.MIN_N_POOLED})")
        p("==== END SUMMARY ====")
        return "\n".join(lines)

    full = pl.concat([cohort_df, gate_df], how="horizontal")
    zw = full["z_clust_wr"].fill_null(0.0).abs().to_numpy()
    zp = full["z_clust_plunge"].fill_null(0.0).abs().to_numpy()
    full = full.with_columns(pl.Series("_rank", np.maximum(zw, zp)))

    n_cells = full.height
    expected_false_z3 = n_cells * 2 * 0.0027
    n_z3 = full.filter(
        pl.col("z_clust_wr").abs().fill_null(0.0).ge(3.0)
        | pl.col("z_clust_plunge").abs().fill_null(0.0).ge(3.0)
    ).height
    p(f"cohort cells scored={n_cells} (PREREGISTRATION expects ~29 cells x 2 outcomes, "
      f"expected false |z|>=3 ~=0.16 under the global null)")
    p(f"expected_false_hits(alpha~0.0027, two-sided, x2 outcomes)={expected_false_z3:.2f}  "
      f"observed_cells_|z_clust|>=3(WR-or-plunge)={n_z3}")

    def _cell_table(sub: pl.DataFrame, title: str):
        p("")
        p(f"-- {title} --")
        if sub.height == 0:
            p("  (no cells scored in this family)")
            return
        p(f"{'feature':<20} {'cell':<10} {'N':>5} {'prev%':>6} {'dWRpp':>7} {'dPLpp':>7} "
          f"{'zWR':>6} {'zPL':>6} {'tWR':>6} {'tPL':>6} {'Psgn':>5} {'legs':>4} {'era/repl':>9} verdict")
        ranked = sub.sort("_rank", descending=True)
        for r in ranked.iter_rows(named=True):
            psgn_key = f"P_sign_consistent_{r['active_outcome']}"
            if r["family"] == "W":
                era_str = "NA" if not era_available else str(r.get("era_repl_sign"))
            else:
                era_str = str(r.get("repl_same_sign"))
            p(f"{r['feature']:<20} {r['cell']:<10} {r['n']:>5} {r['prevalence']*100:>6.1f} "
              f"{_f(r['d_wr_pp'])} {_f(r['d_plunge_pp'])} {_f(r['z_clust_wr'])} {_f(r['z_clust_plunge'])} "
              f"{_f(r['t_controlled_wr'])} {_f(r['t_controlled_plunge'])} {_f(r.get(psgn_key), '.2f')} "
              f"{r['n_legs_passed']:>4} {era_str:>9} {r['verdict']}")

    _cell_table(full.filter(pl.col("family") == "W"), "(3) W-family cell table")
    _cell_table(full.filter(pl.col("family") == "S"), "(4) S-family cell table")

    p("")
    p("-- (5) diagnostics (report-only, never gated) --")
    p(diagnostics.diagnostic_supply_vs_baseline(df, cal, MIN_S_BARS))
    p("")
    p(diagnostics.diagnostic_s_correlation(df))
    p("")
    p(diagnostics.diagnostic_young_listing(df, MIN_S_BARS))

    findings = full.filter(pl.col("verdict") == "FINDING")
    near_misses = full.filter(pl.col("verdict") == "NEAR_MISS")

    p("")
    p(f"-- (6) FINDINGs (all 5 legs pass): {findings.height} --")
    for r in findings.iter_rows(named=True):
        p(f"  {r['family']} {r['feature']} cell={r['cell']} N={r['n']} d_wr_pp={_f(r['d_wr_pp'])} "
          f"d_plunge_pp={_f(r['d_plunge_pp'])} active={r['active_outcome']} "
          f"actionable_below_BE45={r['actionable_below_be']} actionable_ev_spread>=0.03={r['actionable_ev_spread']}")

    p("")
    p(f"-- NEAR_MISSes (>=4/5 legs pass): {near_misses.height} --")
    for r in near_misses.iter_rows(named=True):
        p(f"  {r['family']} {r['feature']} cell={r['cell']} N={r['n']} legs={r['n_legs_passed']}/5 "
          f"[z_clust={r['leg1_z_clust']} t_ctl={r['leg2_t_controlled']} psign={r['leg3_p_sign']} "
          f"n_prev={r['leg4_n_prevalence']} repl={r['leg5_replication']}] era_capped={r.get('era_capped')} "
          f"d_wr_pp={_f(r['d_wr_pp'])} d_plunge_pp={_f(r['d_plunge_pp'])}")

    p("==== END SUMMARY ====")
    return "\n".join(lines)


def _write_summary(tag, text):
    path = os.path.join(SUMMARY_DIR, f"SUMMARY_{tag}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[output] wrote {path}")


# =============================================================================
# main
# =============================================================================
def main():
    args = parse_args()
    t_start = time.time()
    print(f"[start] wave_cycle_mine mine.py smoke={args.smoke}", flush=True)
    print(f"[paths] BASE_FRAME_PATH={BASE_FRAME_PATH}", flush=True)
    print(f"[paths] OHLC_PATH={OHLC_PATH}", flush=True)
    print(f"[paths] MIRROR_PATH={MIRROR_PATH}", flush=True)
    print(f"[paths] OUT_DIR={OUT_DIR}", flush=True)

    df = pl.read_parquet(BASE_FRAME_PATH)
    n0 = df.height
    print(f"[base_frame] loaded {n0} rows x {df.width} cols from {BASE_FRAME_PATH}", flush=True)

    if args.smoke:
        counts_df = pl.read_parquet(OHLC_PATH, columns=["symbol"])
        counts = counts_df.group_by("symbol").agg(pl.len().alias("n")).to_dicts()
        bar_counts = {r["symbol"]: r["n"] for r in counts}
        smoke_syms = choose_smoke_symbols(df, bar_counts)
        df = df.filter(pl.col("symbol").is_in(smoke_syms))
        tag = "smoke"
    else:
        tag = "full"
    print(f"[universe] {df['symbol'].n_unique()} symbols, {df.height} rows (tag={tag})", flush=True)

    if df.height == 0:
        print("[FATAL] frame is empty after smoke-filter -- aborting")
        sys.exit(2)

    # ---- W-family (pure calendar functions of date) -------------------------
    cal = calendar_features.WCalendar()
    print(f"[calendar] trading_day_index {cal.start}..{cal.end}: {len(cal.trading_days)} trading days, "
          f"{len(cal.opex_days)} OPEX days", flush=True)
    df = calendar_features.add_w_columns(df, cal)

    # ---- S-family (PIT trailing-close features) ------------------------------
    universe = set(df["symbol"].unique().to_list())
    close_arrays = load_close_arrays(universe)
    missing_ohlc = universe - set(close_arrays.keys())
    if missing_ohlc:
        print(f"[ohlc] WARNING: {len(missing_ohlc)} symbols had no OHLC rows: {sorted(missing_ohlc)[:10]}")
    df, rets_by_symbol = build_s_features(df, close_arrays)

    # ---- G7/G-choke: NaN -> null BEFORE any is_not_null()/bucketing/fits ----
    df = df.with_columns(pl.col(pl.Float64).fill_nan(None))

    # ---- mandatory self-tests (hard-fail gate) -------------------------------
    print("\n[selftests] running...", flush=True)
    ok1 = selftest_lookahead(df, close_arrays, rets_by_symbol, n_sample=50)
    ok2 = selftest_base_rate(df, smoke=args.smoke)
    ok3 = selftest_holdout(df)
    ok4 = selftest_finX()
    ok5 = selftest_young_listing(df)
    ok6 = selftest_calendar(cal)
    selftest_results = (ok1, ok2, ok3, ok4, ok5, ok6)
    selftest_names = ("ST1_lookahead", "ST2_base_rate", "ST3_holdout", "ST4_finX",
                       "ST5_young_listing", "ST6_calendar")

    if not all(selftest_results):
        print("\n[FATAL] one or more mandatory self-tests FAILED -- aborting before cohort analysis "
              "(per contract: STOP and fix before any cohort analysis).")
        summary_text = format_summary(tag, df, pl.DataFrame({"n": []}), pl.DataFrame({}),
                                       selftest_results, selftest_names, False, cal, args.smoke, t_start)
        print(summary_text)
        _write_summary(tag, summary_text)
        sys.exit(1)
    print("[selftests] all PASS\n", flush=True)

    # ---- bucket columns (S-family terciles; W-family buckets already added) -
    tercile_cuts = {}
    exprs = []
    for _label, col, bcol in S_FEATURES:
        cuts = stats.compute_tercile_cuts(df, col)
        if cuts is None:
            print(f"[buckets] {col}: <30 non-null values -- no tercile cuts, all-null bucket")
            exprs.append(pl.lit(None).alias(bcol))
            continue
        tercile_cuts[col] = cuts
        exprs.append(stats.tercile_expr(col, cuts[0], cuts[1]).alias(bcol))
    df = df.with_columns(exprs)
    print(f"[buckets] S-family tercile cuts computed for: {sorted(tercile_cuts.keys())}", flush=True)

    # ---- era mirror (W-family leg5; locked fallback if missing) -------------
    era_lookup, era_available = build_era_lookup(cal)

    # ---- cohort scan + gate evaluation ---------------------------------------
    rng = np.random.default_rng(RNG_SEED)
    cohort_df, _base = run_cohort_scan(df, rng)

    if cohort_df.height:
        gate_rows = [evaluate_gate(r, era_lookup, era_available) for r in cohort_df.iter_rows(named=True)]
        gate_df = pl.DataFrame(gate_rows, infer_schema_length=None)
    else:
        gate_df = pl.DataFrame({})

    # ---- write outputs --------------------------------------------------------
    feat_path = os.path.join(OUT_DIR, f"features_v74_{tag}.parquet")
    cohort_path = os.path.join(OUT_DIR, f"cohort_stats_v74_{tag}.parquet")
    df.write_parquet(feat_path)
    if cohort_df.height:
        # DEVIATION (style, not substance): unlike peak_fakeout/mine.py (which
        # persists cohort stats WITHOUT gate/verdict columns, computing those
        # transiently only for the SUMMARY text), this study merges gate_df
        # (legs_passed/verdict/era_repl_sign) into the persisted parquet --
        # the prereg's "Cells" deliverable explicitly names legs_passed +
        # classification as per-cell outputs, so persisting them makes the
        # parquet self-sufficient for downstream reads without re-deriving
        # verdicts from raw z-scores.
        pl.concat([cohort_df, gate_df], how="horizontal").write_parquet(cohort_path)
    else:
        cohort_df.write_parquet(cohort_path)
    print(f"[output] wrote {feat_path} ({df.height} rows)")
    print(f"[output] wrote {cohort_path} ({cohort_df.height} rows)")

    summary_text = format_summary(tag, df, cohort_df, gate_df, selftest_results, selftest_names,
                                   era_available, cal, args.smoke, t_start)
    print(summary_text)
    _write_summary(tag, summary_text)
    print(f"\n[done] total wall time {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
