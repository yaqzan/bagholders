"""Peak-Fakeout study -- Phase A read-only interaction mine (NO recalc, NO MC).

Implements experiments/peak_fakeout/PREREGISTRATION.md: within a peak-state
entry (price at/near its own multi-month high after a strong run), does any
feature (F1-F12) separate the plunge cohort from the continuation cohort?

Usage:
    PYTHONIOENCODING=utf-8 python -u experiments/peak_fakeout/mine.py --smoke
    PYTHONIOENCODING=utf-8 python -u experiments/peak_fakeout/mine.py         # full run

--smoke: 50 symbols (45 alphabetical + 5 shortest-price-history, to exercise
the young-listing null-guard), end-to-end (self-tests + cohort table, likely
mostly empty at N=50 -- this is expected, see MIN_N_POOLED).
default: full funded-ledger universe (697 symbols, N=5,810 post-coverage-drop).

Output: .cache/peak_fakeout/{features,cohort_stats}_v74_{tag}.parquet +
experiments/peak_fakeout/SUMMARY_{tag}.txt. ASCII-only stdout (Windows cp1252
console -- no unicode).

Statistical machinery (bayes_layer, ols_robust/logit_irls lineage, the
self-test structure, add_window_column) is COPIED from (not imported from)
trend_ma_lattice/mine.py: both experiment dirs use a bare `import features`
of their OWN local features.py, and Python's sys.modules module-name cache
would let whichever one is imported first silently win for the OTHER too --
a real cross-experiment collision. Copying avoids it (the prompt explicitly
sanctions "copy or import" for exactly this reason).

P_run gate vs runup20_sig FEATURE -- two different quantities, both locked
by their own source document, kept deliberately separate (not a bug):
  - PREREGISTRATION.md's P_run gate is explicitly defined as a 20d run-up
    normalized by 60d vol ("sigma = 60d daily close-to-close vol"). Computed
    here as _p_run_measure = _runup20_raw / (sigma60*sqrt(20)).
  - The `runup20_sig` FEATURE column (used as F3's parabolic-ratio
    denominator, and as a regression control) is normalized by sigma20
    (matched-horizon vol) per the orchestrator prompt's literal formula, and
    lives in features.py. These are NOT meant to be the same column -- see
    features.py's module docstring.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date

import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import features  # noqa: E402  (local module, experiments/peak_fakeout/features.py)
from experiments._holdout import assert_no_holdout_leak, CUTOFF  # noqa: E402

OUT_DIR = os.path.join(_ROOT, ".cache", "peak_fakeout")
os.makedirs(OUT_DIR, exist_ok=True)
SUMMARY_DIR = _HERE

LEDGER_PATH = os.path.join(_ROOT, ".cache", "trend_ma_lattice", "ledger_v74_full.parquet")
FEATURES_V74_PATH = os.path.join(_ROOT, ".cache", "trend_ma_lattice", "features_v74_full.parquet")
OHLC_PATH = os.path.join(_ROOT, ".cache", "intraday_overnight", "ohlc.parquet")
LOOKUP_MCAP_PATH = os.path.join(OUT_DIR, "lookup_mcap.parquet")
LOOKUP_EARNINGS_PATH = os.path.join(OUT_DIR, "lookup_earnings.parquet")
LOOKUP_VIX_PATH = os.path.join(OUT_DIR, "lookup_vix.parquet")

OHLC_MAX_DATE = date(2026, 6, 8)     # coverage drop threshold (locked, PREREGISTRATION)

WINDOW_ORDER = ["2021", "2022", "2023", "2024", "2025+"]
MIN_N_POOLED = 30          # minimum resolved-N to appear as a scored cohort cell
MIN_N_WINDOW = 10          # minimum per-window N to enter Bayesian pooling / sign-consistency
MIN_CELL_CONTROL_SIDE = 20  # minimum N on EACH side (bucket / rest) to attempt a clustered fit
N_MC = 10000
RNG_SEED = 20260715

SMOKE_N_ALPHA = 45
SMOKE_N_YOUNG = 5

# --- F1-F12 registry: (label, source_column, bucket_kind) -------------------
FEATURE_SPECS = [
    ("F1_pit_mcap_b", "pit_mcap_b", "tercile"),
    ("F2_vol20", "vol20", "tercile"),
    ("F3_parabolic", "parabolic", "tercile"),
    ("F4_climax_day", "climax_day", "tercile"),
    ("F5_streak_up", "streak_up", "fixed_streak"),
    ("F6_high_zone_age", "high_zone_age", "fixed_zone_age"),
    ("F7_base_days", "base_days", "tercile"),
    ("F8_vol_z", "vol_z", "tercile"),
    ("F9_days_to_earnings", "days_to_earnings", "fixed_earnings"),
    ("F10_runup60_sig", "runup60_sig", "tercile"),
    ("F11_trend_dominant", "trend", "fixed_trend"),
    ("F12_vix_band", "vix_close", "fixed_vix"),
]


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="50-symbol end-to-end smoke run")
    return ap.parse_args()


# =============================================================================
# Load + assemble
# =============================================================================
def load_ledger():
    df = pl.read_parquet(LEDGER_PATH)
    if "_cached_symbols" in df.columns:
        df = df.drop("_cached_symbols")
    n0 = df.height
    df = df.filter(pl.col("date") <= OHLC_MAX_DATE)
    n1 = df.height
    print(f"[ledger] loaded {n0} rows; dropped {n0-n1} rows with date > {OHLC_MAX_DATE.isoformat()} "
          f"(OHLC coverage) -> {n1}", flush=True)
    return df


def join_controls_and_repl(ledger_df: pl.DataFrame) -> pl.DataFrame:
    feat = pl.read_parquet(
        FEATURES_V74_PATH,
        columns=["symbol", "date", "dist_EMA_50", "dist_EMA_200",
                 "control_pct_ema50", "control_pct_ema200", "repl_win", "repl_ev"],
    )
    before = ledger_df.height
    df = ledger_df.join(feat, on=["symbol", "date"], how="left")
    after = df.height
    if after != before:
        raise AssertionError(f"[join_controls_and_repl] row count changed {before}->{after} "
                              f"-- (symbol,date) is not unique in features_v74_full.parquet")
    n_ctl = df.filter(pl.col("control_pct_ema50").is_not_null()).height
    n_repl = df.filter(pl.col("repl_win").is_not_null()).height
    print(f"[controls] joined dist_EMA_50/200 + control_pct_ema50/200 + repl_win/repl_ev "
          f"(control_pct_ema50 populated={n_ctl}/{after}, repl_win populated={n_repl}/{after})", flush=True)
    return df


def load_ohlc_arrays(symbols: set[str]):
    """symbol -> (dates: list[date], closes: np.ndarray, volumes: np.ndarray)."""
    t0 = time.time()
    df = pl.read_parquet(OHLC_PATH)
    df = df.with_columns(pl.col("date").str.to_date().alias("date"))
    df = df.filter(pl.col("symbol").is_in(list(symbols))).sort(["symbol", "date"])
    out = {}
    for sym, g in df.group_by("symbol", maintain_order=True):
        sym = sym[0] if isinstance(sym, tuple) else sym
        out[sym] = (
            g["date"].to_list(),
            g["close"].to_numpy().astype(np.float64),
            g["volume"].to_numpy().astype(np.float64),
        )
    print(f"[ohlc] loaded {len(out)}/{len(symbols)} symbols from {OHLC_PATH} "
          f"in {time.time()-t0:.1f}s", flush=True)
    return out


def load_lookups():
    mcap = pl.read_parquet(LOOKUP_MCAP_PATH)
    earnings = pl.read_parquet(LOOKUP_EARNINGS_PATH)
    vix = pl.read_parquet(LOOKUP_VIX_PATH)
    mcap_by_sym = {r["symbol"]: (r["mcap_b_latest"], r["anchor_close"])
                   for r in mcap.iter_rows(named=True)}
    earnings_by_sym = defaultdict(list)
    for r in earnings.iter_rows(named=True):
        earnings_by_sym[r["symbol"]].append(r["earnings_date"])
    for sym in earnings_by_sym:
        earnings_by_sym[sym].sort()
    vix_by_date = {r["date"]: r["vix_close"] for r in vix.iter_rows(named=True)}
    vix_source = "cache_reuse/mysql/market_regime" if vix.height else "none"
    print(f"[lookups] mcap={mcap.height} earnings_symbols={len(earnings_by_sym)} "
          f"vix_dates={vix.height} (source availability: {vix_source})", flush=True)
    return mcap_by_sym, earnings_by_sym, vix_by_date


def choose_smoke_symbols(ledger_df: pl.DataFrame, ohlc_bar_counts: dict) -> list[str]:
    all_syms = sorted(ledger_df["symbol"].unique().to_list())
    alpha = all_syms[:SMOKE_N_ALPHA]
    by_len = sorted(all_syms, key=lambda s: ohlc_bar_counts.get(s, 0))
    young = by_len[:SMOKE_N_YOUNG]
    chosen = sorted(set(alpha) | set(young))
    print(f"[smoke] {len(alpha)} alphabetical + {len(young)} shortest-history "
          f"({young}) -> {len(chosen)} unique symbols", flush=True)
    return chosen


# =============================================================================
# Feature-frame assembly (per-symbol causal series + F1/F9/F11/F12 fusion)
# =============================================================================
def build_feature_frame(ledger_df: pl.DataFrame, ohlc_arrays: dict, mcap_by_sym: dict,
                         earnings_by_sym: dict, vix_by_date: dict) -> pl.DataFrame:
    from database.utils.scoring import compute_pit_mcap_b

    t0 = time.time()
    by_sym = defaultdict(list)
    for r in ledger_df.iter_rows(named=True):
        by_sym[r["symbol"]].append(r)

    rows = []
    n_syms = 0
    n_no_ohlc = 0
    for sym, sig_rows in by_sym.items():
        oh = ohlc_arrays.get(sym)
        if oh is None:
            n_no_ohlc += len(sig_rows)
            continue
        dates, closes, volumes = oh
        date_to_idx = {d: i for i, d in enumerate(dates)}
        series = features.build_symbol_series(closes, volumes)
        mcap_b_latest, anchor_close = mcap_by_sym.get(sym, (None, None))
        sorted_earn = earnings_by_sym.get(sym, [])

        for r in sig_rows:
            idx = date_to_idx.get(r["date"])
            if idx is None:
                n_no_ohlc += 1
                continue
            frow = features.extract_row(idx, series)
            close_entry = closes[idx]

            pit_mcap_b = compute_pit_mcap_b(mcap_b_latest, close_entry, anchor_close)
            days_to_earn = features.compute_days_to_earnings(sorted_earn, r["date"])
            vix_close = vix_by_date.get(r["date"])

            row = dict(frow)
            row.update({
                "symbol": sym, "date": r["date"], "overall": r["overall"], "trend": r["trend"],
                "apex_win": r["apex_win"], "apex_ev": r["apex_ev"], "gen_win": r["gen_win"],
                "fwd_caldays": r["fwd_caldays"], "_idx": idx, "version_id": r["version_id"],
                "window": r["window"],
                "dist_EMA_50": r.get("dist_EMA_50"), "dist_EMA_200": r.get("dist_EMA_200"),
                "control_pct_ema50": r.get("control_pct_ema50"),
                "control_pct_ema200": r.get("control_pct_ema200"),
                "repl_win": r.get("repl_win"), "repl_ev": r.get("repl_ev"),
                "pit_mcap_b": pit_mcap_b,
                "days_to_earnings": days_to_earn,
                "vix_close": vix_close,
            })
            rows.append(row)
        del series
        n_syms += 1

    df = pl.DataFrame(rows, infer_schema_length=None)  # G7: None-early/float-later cols
    print(f"[features] built {df.height} rows x {df.width} cols across {n_syms} symbols "
          f"(rows skipped for missing OHLC/date={n_no_ohlc}) in {time.time()-t0:.1f}s", flush=True)
    return df


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
# Self-tests (mandatory; run before any cohort analysis; exit 1 on failure)
# =============================================================================
def selftest_lookahead(feat_frame: pl.DataFrame, ohlc_arrays: dict, n_sample=50, seed=7):
    rng = np.random.default_rng(seed)
    eligible = feat_frame.filter(pl.col("_idx") >= 60)
    if eligible.height == 0:
        print("[selftest ST1 lookahead] no eligible rows -> FAIL")
        return False
    take = min(n_sample, eligible.height)
    pick = rng.choice(eligible.height, size=take, replace=False).tolist()
    sample = eligible[pick]
    cols = features.feature_columns()
    mismatches = []
    checked = 0
    for r in sample.iter_rows(named=True):
        sym, idx = r["symbol"], r["_idx"]
        _, closes, volumes = ohlc_arrays[sym]
        series_full = features.build_symbol_series(closes, volumes)
        series_trunc = features.build_symbol_series(closes[: idx + 1], volumes[: idx + 1])
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
    print(f"[selftest ST1 lookahead] pairs={take} feature_checks={checked} mismatches={len(mismatches)} "
          f"-> {'PASS' if ok else 'FAIL'}")
    for m in mismatches[:8]:
        print(f"    LOOK-AHEAD LEAK sym={m[0]} date={m[1]} col={m[2]} full={m[3]} trunc={m[4]}")
    return ok


def selftest_base_rate(feat_frame: pl.DataFrame, smoke: bool = False):
    """Full-universe (N=5,810 post-drop) apex WR/plunge are 70.07%/23.25%
    (verified directly against the ledger parquet -- within the prereg's
    quoted 70.1%/23.3%). The prompt's +/-0.3pp tolerance is a FULL-POPULATION
    check; it is not statistically meaningful on a --smoke 50-symbol (~400
    row), non-random (45-alphabetical + 5-youngest) slice, where sampling
    composition alone moves the aggregate several points. DEVIATION (documented,
    not silent): in --smoke mode this test widens to +/-5pp -- still a real
    gross-error catch (inverted labels, wrong outcome column, broken join
    would blow past 5pp) but not the tight full-population bar. Full-mode
    tolerance is unchanged (+/-0.3pp, exactly as specified)."""
    y = feat_frame["apex_win"].to_numpy().astype(float)
    n = int(np.sum(~np.isnan(y)))
    wr = float(np.nanmean(y)) if n else float("nan")
    ev = feat_frame["apex_ev"].to_numpy()
    plunge = float(np.mean(ev == -0.7)) if len(ev) else float("nan")
    tol = 0.05 if smoke else 0.003
    ok = (n > 0 and abs(wr - 0.701) <= tol and abs(plunge - 0.233) <= tol)
    print(f"[selftest ST2 base_rate] N={n} apex_wr={wr:.4f} (expect 0.701+/-{tol:.3f}) "
          f"plunge={plunge:.4f} (expect 0.233+/-{tol:.3f}) smoke={smoke} -> {'PASS' if ok else 'FAIL'}")
    return ok


def selftest_holdout(ledger_df, feat_frame):
    try:
        assert_no_holdout_leak(ledger_df, context="peak_fakeout ledger")
        assert_no_holdout_leak(feat_frame, context="peak_fakeout feat_frame")
    except AssertionError as e:
        print(f"[selftest ST3 holdout] FAIL: {e}")
        return False
    max_d = feat_frame["date"].max() if feat_frame.height else None
    print(f"[selftest ST3 holdout] max_date={max_d} cutoff={CUTOFF.isoformat()} -> PASS")
    return True


def selftest_finX():
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
    res = fit_clustered(X_bad[finX], y[finX], clusters[finX], min_n_side=MIN_CELL_CONTROL_SIDE)
    ok = (res is not None and res["n_used"] == n_expected and n_expected < n
          and math.isfinite(res["t"]))
    print(f"[selftest ST4 finX] n_total={n} injected_nan_rows={len(set(bad_idx.tolist()))} "
          f"n_used_after_drop={n_expected} t={res['t'] if res else None} -> {'PASS' if ok else 'FAIL'}")
    return ok


def selftest_young_listing(feat_frame: pl.DataFrame):
    young = feat_frame.filter(pl.col("n_bars_before_entry") < features.MIN_TOTAL_BARS)
    if young.height == 0:
        print("[selftest ST5 young_listing] no young-listing rows in this run's universe "
              "(none of the selected symbols had a signal at <260 pre-entry bars) -> PASS (vacuous)")
        return True
    # Runs AFTER the fill_nan(None) choke point (main() ordering), so the
    # features.py NaN sentinel has already been normalized to a real null --
    # is_not_null() alone is the correct check here (not is_nan()).
    rolling_cols = ["sigma20", "sigma60", "runup5_sig", "runup20_sig", "runup60_sig",
                    "pct_from_high126", "pct_from_high252", "vol20", "parabolic",
                    "climax_day", "streak_up", "high_zone_age", "base_days", "vol_z"]
    bad = 0
    for c in rolling_cols:
        n_non_null = young.filter(pl.col(c).is_not_null()).height
        bad += n_non_null
    ok = (bad == 0) and (young.height > 0)
    print(f"[selftest ST5 young_listing] {young.height} young-listing rows (<260 bars) present in frame; "
          f"non-null rolling-stat values found={bad} (expect 0) -> {'PASS' if ok else 'FAIL'}")
    return ok


# =============================================================================
# Stats machinery -- COPIED/adapted from trend_ma_lattice/mine.py (see module
# docstring for why copy, not import), extended with cluster-robust (CR1) SEs.
# =============================================================================
def _zscore_nan(x):
    """z-score using non-NaN mean/std; NaN STAYS NaN (no imputation). The
    controlled regression's finX guard relies on this -- imputing NaN->0
    (as trend_ma_lattice's own _standardize does, and which this study
    deliberately does NOT reuse) would make every row 'finite' and silently
    defeat the finX mask (the documented false-NULL trap this study's
    prompt explicitly calls out -- see ST4)."""
    finite = x[~np.isnan(x)]
    if finite.size == 0:
        return np.full_like(x, np.nan)
    mu, sd = finite.mean(), finite.std()
    if not sd or math.isnan(sd) or sd < 1e-9:
        sd = 1.0
    return (x - mu) / sd


def bayes_layer(per_window, metric, rng, n_mc=N_MC):
    """Copied verbatim from trend_ma_lattice/mine.py (A4 Bayesian layer)."""
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
    D = np.array(draws)
    points = np.array(points)
    within_var = D.var(axis=1)
    tau2 = max(0.0, float(np.var(points, ddof=1) - within_var.mean())) if len(points) > 1 else 0.0
    tau = math.sqrt(tau2)
    noise = rng.normal(0.0, tau, size=D.shape) if tau > 0 else np.zeros_like(D)
    Dn = D + noise
    pooled = Dn.mean(axis=0)
    P_pooled_gt0 = float((pooled > 0).mean())
    pooled_sign = np.sign(pooled)
    per_window_sign = np.sign(Dn)
    agree_frac = (per_window_sign == pooled_sign[None, :]).mean(axis=0)
    P_sign_consistent = float((agree_frac >= 0.8 - 1e-9).mean())
    return dict(pooled_delta=float(pooled.mean()), P_pooled_gt0=P_pooled_gt0,
                P_sign_consistent=P_sign_consistent, tau=tau, n_windows=len(ws))


def window_signs(per_window):
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


# --- CR1 cluster-robust logit / OLS(LPM) sandwich, clusters = entry date ----
def _fit_logit_ridge(X, y, max_iter=25, tol=1e-8, ridge=1e-6):
    n, k = X.shape
    beta = np.zeros(k)
    for _ in range(max_iter):
        eta = np.clip(X @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(p * (1 - p), 1e-6, None)
        z = eta + (y - p) / w
        XtWX = (X * w[:, None]).T @ X + ridge * np.eye(k)
        XtWz = (X * w[:, None]).T @ z
        try:
            beta_new = np.linalg.solve(XtWX, XtWz)
        except np.linalg.LinAlgError:
            return None
        if not np.all(np.isfinite(beta_new)):
            return None
        if np.max(np.abs(beta_new - beta)) < tol:
            beta = beta_new
            break
        beta = beta_new
    return beta


def _cluster_sandwich_se(bread, score, clusters, n, k):
    _, inv = np.unique(clusters, return_inverse=True)
    G = int(inv.max()) + 1 if n else 0
    sums = np.zeros((G, k))
    np.add.at(sums, inv, score)
    meat = sums.T @ sums
    corr = (G / max(G - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = corr * (bread @ meat @ bread)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    return se, G


def clustered_logit_effect(X, y, clusters, ridge=1e-6):
    """Returns dict(beta,t,n_used,n_clusters,method) for column 1 of X (the
    treatment dummy), or None if infeasible."""
    n, k = X.shape
    beta = _fit_logit_ridge(X, y, ridge=ridge)
    if beta is None:
        return None
    eta = np.clip(X @ beta, -30, 30)
    p = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(p * (1 - p), 1e-6, None)
    XtWX = (X * w[:, None]).T @ X + ridge * np.eye(k)
    try:
        bread = np.linalg.pinv(XtWX)
    except np.linalg.LinAlgError:
        return None
    score = X * (y - p)[:, None]
    se, G = _cluster_sandwich_se(bread, score, clusters, n, k)
    if se[1] <= 1e-12 or not np.isfinite(se[1]) or not np.isfinite(beta[1]):
        return None
    t1 = beta[1] / se[1]
    if not np.isfinite(t1):
        return None
    return dict(beta=float(beta[1]), t=float(t1), n_used=n, n_clusters=G, method="logit")


def clustered_ols_effect(X, y, clusters):
    n, k = X.shape
    XtX = X.T @ X
    try:
        bread = np.linalg.pinv(XtX)
    except np.linalg.LinAlgError:
        return None
    beta = bread @ (X.T @ y)
    if not np.all(np.isfinite(beta)):
        return None
    resid = y - X @ beta
    score = X * resid[:, None]
    se, G = _cluster_sandwich_se(bread, score, clusters, n, k)
    if se[1] <= 1e-12 or not np.isfinite(se[1]):
        return None
    t1 = beta[1] / se[1]
    if not np.isfinite(t1):
        return None
    return dict(beta=float(beta[1]), t=float(t1), n_used=n, n_clusters=G, method="ols_fallback")


def fit_clustered(X, y, clusters, min_n_side=MIN_CELL_CONTROL_SIDE):
    """logit first; falls back to linear-probability OLS if logit is
    infeasible/unstable (small-N separation etc) -- per the prompt's
    'logit (or robust OLS if unstable at small N)' instruction."""
    d = X[:, 1]
    n1 = int(np.sum(d > 0.5))
    n0 = int(len(d) - n1)
    if n1 < min_n_side or n0 < min_n_side:
        return None
    res = clustered_logit_effect(X, y, clusters)
    if res is None:
        res = clustered_ols_effect(X, y, clusters)
    return res


def clustered_cell_effect(X, y, clusters, sample_mask, min_n_side=MIN_CELL_CONTROL_SIDE):
    """finX guard (drop non-finite regressor rows) + sample restriction +
    valid-y mask, THEN fit. Never raises; returns a dict with t=nan and a
    'method' explaining why on any infeasible path (the G47 false-NULL trap
    the prompt calls out is specifically avoided by doing the finX masking
    BEFORE fitting, using _zscore_nan -- not the imputing _standardize_impute
    -- for every control column that feeds X)."""
    finX = np.isfinite(X).all(axis=1)
    valid_y = ~np.isnan(y)
    m = sample_mask & finX & valid_y
    n_used = int(m.sum())
    if n_used < 2 * min_n_side:
        return dict(t=float("nan"), beta=float("nan"), n_used=n_used, n_clusters=0, method="insufficient_n")
    res = fit_clustered(X[m], y[m], clusters[m], min_n_side=min_n_side)
    if res is None:
        return dict(t=float("nan"), beta=float("nan"), n_used=n_used, n_clusters=0, method="infeasible")
    if n_used >= 50 and not math.isfinite(res["t"]):
        return dict(t=float("nan"), beta=float("nan"), n_used=n_used, n_clusters=res.get("n_clusters", 0),
                    method="CONTRACT_VIOLATION_nonfinite_t_at_n>=50")
    return res


def _prop_z(p_cell, p_base, n_cell):
    if p_base is None or math.isnan(p_base) or not (0 < p_base < 1) or n_cell <= 0:
        return 0.0
    se = math.sqrt(p_base * (1 - p_base) / n_cell)
    if se <= 0:
        return 0.0
    return (p_cell - p_base) / se


def _sumcount(mask, y):
    yv = y[mask]
    n = int(np.sum(~np.isnan(yv)))
    wins = float(np.nansum(yv)) if n else 0.0
    return wins, n


def _meanse(mask, y):
    yv = y[mask]
    n = int(np.sum(~np.isnan(yv)))
    mean = float(np.nanmean(yv)) if n else float("nan")
    se = float(np.nanstd(yv) / math.sqrt(n)) if n > 1 else float("nan")
    return mean, se, n


# =============================================================================
# Prevalence ladder (locked, discretion-free; applied ONCE on prevalence only,
# evaluated BEFORE any outcome column is read -- these functions never touch
# apex_win/apex_ev/apex ev/plunge, only the price-derived feature columns).
# =============================================================================
def _prevalence(df: pl.DataFrame, expr: pl.Expr) -> tuple[float, int]:
    n_total = df.height
    n_hit = df.filter(expr).height
    return (n_hit / n_total if n_total else 0.0), n_total


def choose_p_high_rung(df: pl.DataFrame) -> dict:
    def prev(pct, col):
        return _prevalence(df, pl.col(col) >= -pct)

    p0, nv0 = prev(0.03, "pct_from_high126")
    if p0 > 0.40:
        p1, nv1 = prev(0.015, "pct_from_high126")
        if p1 > 0.40:
            p2, nv2 = prev(0.015, "pct_from_high252")
            return dict(rung="252d_1.5pct", pct=0.015, col="pct_from_high252", prevalence=p2, n_total=nv2)
        return dict(rung="126d_1.5pct", pct=0.015, col="pct_from_high126", prevalence=p1, n_total=nv1)
    if p0 < 0.15:
        p3, nv3 = prev(0.05, "pct_from_high126")
        return dict(rung="126d_5pct", pct=0.05, col="pct_from_high126", prevalence=p3, n_total=nv3)
    return dict(rung="126d_3pct(default)", pct=0.03, col="pct_from_high126", prevalence=p0, n_total=nv0)


def choose_p_run_rung(df: pl.DataFrame) -> dict:
    def prev(sig):
        return _prevalence(df, pl.col("_p_run_measure") >= sig)

    p0, nv0 = prev(1.5)
    if p0 > 0.40:
        p1, nv1 = prev(2.0)
        return dict(rung="2.0sig", sig=2.0, prevalence=p1, n_total=nv1)
    if p0 < 0.15:
        p2, nv2 = prev(1.25)
        if p2 < 0.15:
            p3, nv3 = prev(1.0)
            return dict(rung="1.0sig", sig=1.0, prevalence=p3, n_total=nv3)
        return dict(rung="1.25sig", sig=1.25, prevalence=p2, n_total=nv2)
    return dict(rung="1.5sig(default)", sig=1.5, prevalence=p0, n_total=nv0)


def choose_p_both_rung(df: pl.DataFrame, p_high_choice: dict, p_run_choice: dict) -> dict:
    def prev(col, pct, sig):
        return _prevalence(df, (pl.col(col) >= -pct) & (pl.col("_p_run_measure") >= sig))

    p0, nv0 = prev(p_high_choice["col"], p_high_choice["pct"], p_run_choice["sig"])
    if p0 >= 0.08:
        return dict(rung="base(chosen_parents)", p_high_col=p_high_choice["col"], p_high_pct=p_high_choice["pct"],
                    p_run_sig=p_run_choice["sig"], prevalence=p0, n_total=nv0)
    p1, nv1 = prev("pct_from_high126", 0.05, p_run_choice["sig"])
    if p1 >= 0.08:
        return dict(rung="widen_phigh_5pct", p_high_col="pct_from_high126", p_high_pct=0.05,
                    p_run_sig=p_run_choice["sig"], prevalence=p1, n_total=nv1)
    p2, nv2 = prev("pct_from_high126", 0.05, 1.25)
    tag = "widen_both_phigh5pct_prun1.25sig" if p2 >= 0.08 else "widen_both_STILL_below_8pct(best_effort)"
    return dict(rung=tag, p_high_col="pct_from_high126", p_high_pct=0.05, p_run_sig=1.25,
                prevalence=p2, n_total=nv2)


def apply_peak_states(df: pl.DataFrame):
    df = df.with_columns(
        (pl.col("_runup20_raw") / (pl.col("sigma60") * math.sqrt(20))).alias("_p_run_measure")
    )
    p_high_choice = choose_p_high_rung(df)
    p_run_choice = choose_p_run_rung(df)
    p_both_choice = choose_p_both_rung(df, p_high_choice, p_run_choice)

    df = df.with_columns([
        (pl.col(p_high_choice["col"]) >= -p_high_choice["pct"]).fill_null(False).alias("P_high"),
        (pl.col("_p_run_measure") >= p_run_choice["sig"]).fill_null(False).alias("P_run"),
        ((pl.col(p_both_choice["p_high_col"]) >= -p_both_choice["p_high_pct"])
         & (pl.col("_p_run_measure") >= p_both_choice["p_run_sig"])).fill_null(False).alias("P_both"),
    ])
    return df, p_high_choice, p_run_choice, p_both_choice


# =============================================================================
# Bucket columns -- global terciles (F1,F2,F3,F4,F7,F8,F10) computed ONCE on
# ALL rows, plus the locked fixed cuts (F5,F6,F9), binary (F11), bands (F12).
# =============================================================================
def compute_tercile_cuts(df: pl.DataFrame, col: str):
    vals = df[col].drop_nulls()
    if vals.len() < 30:
        return None
    q1 = vals.quantile(1 / 3, interpolation="linear")
    q2 = vals.quantile(2 / 3, interpolation="linear")
    return (q1, q2)


def _tercile_expr(col, q1, q2):
    c = pl.col(col)
    return (pl.when(c.is_null()).then(None)
              .when(c <= q1).then(pl.lit("T1_low"))
              .when(c <= q2).then(pl.lit("T2_mid"))
              .otherwise(pl.lit("T3_high")))


def _fixed_streak_expr(col):
    c = pl.col(col)
    return (pl.when(c.is_null()).then(None)
              .when(c <= 2).then(pl.lit("<=2"))
              .when(c <= 5).then(pl.lit("3-5"))
              .otherwise(pl.lit(">=6")))


def _fixed_zone_age_expr(col):
    c = pl.col(col)
    return (pl.when(c.is_null()).then(None)
              .when(c <= 3).then(pl.lit("<=3"))
              .when(c <= 10).then(pl.lit("4-10"))
              .otherwise(pl.lit(">10")))


def _fixed_earnings_expr(col):
    # Nulls intentionally fall into '>21_or_none' (F9 spec) -- NOT bucket-less.
    c = pl.col(col)
    return (pl.when(c <= 7).then(pl.lit("<=7"))
              .when(c <= 21).then(pl.lit("8-21"))
              .otherwise(pl.lit(">21_or_none")))


def _fixed_trend_expr(col):
    c = pl.col(col)
    return (pl.when(c.is_null()).then(None)
              .when(c >= 70).then(pl.lit("trend_dominant"))
              .otherwise(pl.lit("oscillator_dominant")))


def _fixed_vix_expr(col):
    c = pl.col(col)
    return (pl.when(c.is_null()).then(None)
              .when(c < 20).then(pl.lit("<20"))
              .when(c <= 28).then(pl.lit("20-28"))
              .otherwise(pl.lit(">28")))


def add_bucket_columns(df: pl.DataFrame):
    tercile_cuts = {}
    exprs = []
    for label, col, kind in FEATURE_SPECS:
        bcol = f"b_{label}"
        if col not in df.columns:
            print(f"[buckets] WARNING: source column {col!r} for {label} not present -- skipping")
            continue
        if kind == "tercile":
            cuts = compute_tercile_cuts(df, col)
            if cuts is None:
                print(f"[buckets] {label}: <30 non-null values in {col!r} -- no tercile cuts, all-null bucket")
                exprs.append(pl.lit(None).alias(bcol))
                continue
            tercile_cuts[label] = cuts
            exprs.append(_tercile_expr(col, cuts[0], cuts[1]).alias(bcol))
        elif kind == "fixed_streak":
            exprs.append(_fixed_streak_expr(col).alias(bcol))
        elif kind == "fixed_zone_age":
            exprs.append(_fixed_zone_age_expr(col).alias(bcol))
        elif kind == "fixed_earnings":
            exprs.append(_fixed_earnings_expr(col).alias(bcol))
        elif kind == "fixed_trend":
            exprs.append(_fixed_trend_expr(col).alias(bcol))
        elif kind == "fixed_vix":
            exprs.append(_fixed_vix_expr(col).alias(bcol))
        else:
            raise ValueError(f"unknown bucket kind {kind!r}")
    df = df.with_columns(exprs)
    return df, tercile_cuts


# =============================================================================
# Cohort scan: for each peak-state P in {P_high,P_run,P_both,UNCONDITIONAL}
# and each feature bucket b -- N, apex_wr, d_wr_pp, plunge_rate, d_plunge_pp,
# d_ev, z_raw/z_clust (WR & plunge), t_controlled (WR & plunge, CR1-clustered,
# finX-guarded), interaction_delta_pp (vs the marginal/unconditional cell),
# bayes_layer per window, replication sign check.
# =============================================================================


def _precompute_pop_bases(n, pop_masks, y_wr, y_plunge, y_ev, y_repl_wr, window_arr):
    bases = {}
    for pname, pmask in pop_masks.items():
        wr_wins, wr_n = _sumcount(pmask, y_wr)
        pl_wins, pl_n = _sumcount(pmask, y_plunge)
        ev_mean, ev_se, ev_n = _meanse(pmask, y_ev)
        repl_wins, repl_n = _sumcount(pmask, y_repl_wr)
        by_window = {}
        for lbl in WINDOW_ORDER:
            wmask = pmask & (window_arr == lbl)
            w_wr_wins, w_wr_n = _sumcount(wmask, y_wr)
            w_pl_wins, w_pl_n = _sumcount(wmask, y_plunge)
            w_ev_mean, w_ev_se, w_ev_n = _meanse(wmask, y_ev)
            by_window[lbl] = dict(wr=(w_wr_wins, w_wr_n), plunge=(w_pl_wins, w_pl_n),
                                   ev=(w_ev_mean, w_ev_se, w_ev_n))
        bases[pname] = dict(
            mask=pmask,
            wr=(wr_wins / wr_n if wr_n else float("nan")), wr_n=wr_n,
            plunge=(pl_wins / pl_n if pl_n else float("nan")), plunge_n=pl_n,
            ev=ev_mean, ev_n=ev_n,
            repl_wr=(repl_wins / repl_n if repl_n else float("nan")), repl_n=repl_n,
            by_window=by_window,
        )
    return bases


def _per_window_list(mask_cell, mask_pop, window_arr, y, by_window_key, pop_bases_by_window, metric):
    out = []
    for lbl in WINDOW_ORDER:
        mw = mask_cell & (window_arr == lbl)
        if metric == "wr":
            wins, n = _sumcount(mw, y)
            base_wins, base_n = pop_bases_by_window[lbl][by_window_key]
            delta = (wins / n - base_wins / base_n) if (n >= MIN_N_WINDOW and base_n >= MIN_N_WINDOW) else None
            out.append(dict(label=lbl, n=n, wins=wins, base_n=base_n, base_wins=base_wins, delta=delta))
        else:
            mean, se, n = _meanse(mw, y)
            base_mean, base_se, base_n = pop_bases_by_window[lbl][by_window_key]
            delta = (mean - base_mean) if (n >= MIN_N_WINDOW and base_n >= MIN_N_WINDOW) else None
            out.append(dict(label=lbl, n=n, mean=mean, se=se, base_n=base_n, base_mean=base_mean,
                             base_se=base_se, delta=delta))
    return out


def compute_cell(feat_label, cell_value, pop_name, bucket_mask, pop_bases, y_wr, y_plunge, y_ev,
                  y_repl_wr, X_controls_full, date_cluster, window_arr, rng, marg_cache=None):
    """Returns a flat dict for ONE cohort-scan row (one feature-bucket cell
    within one peak-state population, or the UNCONDITIONAL/marginal version).
    `X_controls_full` = [overall_z, trend_z, runup20_sig_z, ema50_z] (n x 4,
    NaN-preserving z-scores) computed once outside this function."""
    n = len(bucket_mask)
    pop = pop_bases[pop_name]
    sample = pop["mask"]

    cell_mask = sample & bucket_mask
    ncell = int(cell_mask.sum())
    resolved_wr = int(np.sum(~np.isnan(y_wr[cell_mask])))
    if ncell < MIN_N_POOLED or resolved_wr < MIN_N_POOLED:
        return None

    wr_cell = float(np.nansum(y_wr[cell_mask])) / resolved_wr
    plunge_cell = float(np.nanmean(y_plunge[cell_mask]))
    ev_cell = float(np.nanmean(y_ev[cell_mask]))

    base_wr, base_plunge, base_ev = pop["wr"], pop["plunge"], pop["ev"]
    d_wr_pp = (wr_cell - base_wr) * 100.0 if not math.isnan(base_wr) else float("nan")
    d_plunge_pp = (plunge_cell - base_plunge) * 100.0 if not math.isnan(base_plunge) else float("nan")
    d_ev = (ev_cell - base_ev) if not math.isnan(base_ev) else float("nan")

    z_raw_wr = _prop_z(wr_cell, base_wr, resolved_wr)
    z_raw_plunge = _prop_z(plunge_cell, base_plunge, ncell)

    # --- z_clust: CR1-clustered [intercept,bucket_dummy] WITHIN `sample` ----
    X_bucket = np.column_stack([np.ones(n), bucket_mask.astype(float)])
    cell_wr = clustered_cell_effect(X_bucket, y_wr, date_cluster, sample)
    cell_plunge = clustered_cell_effect(X_bucket, y_plunge, date_cluster, sample)
    z_clust_wr, z_clust_plunge = cell_wr["t"], cell_plunge["t"]

    # --- t_controlled: CR1-clustered on the FULL frame; interaction cells add
    # the peak-state main effect + use (bucket & peak_state) as the treatment
    # dummy; marginal cells use bucket alone, no peak-state control. See
    # module docstring for why this specification was chosen. ---------------
    if pop_name == "UNCONDITIONAL":
        treat = bucket_mask.astype(float)
        controls = X_controls_full
    else:
        treat = (bucket_mask & sample).astype(float)
        controls = np.column_stack([X_controls_full, sample.astype(float)])
    Xc = np.column_stack([np.ones(n), treat, controls])
    full_sample = np.ones(n, dtype=bool)
    ctl_wr = clustered_cell_effect(Xc, y_wr, date_cluster, full_sample)
    ctl_plunge = clustered_cell_effect(Xc, y_plunge, date_cluster, full_sample)

    # --- bayes layer (per-window, within `sample`) --------------------------
    pw_wr = _per_window_list(cell_mask, sample, window_arr, y_wr, "wr", pop["by_window"], "wr")
    pw_plunge = _per_window_list(cell_mask, sample, window_arr, y_plunge, "plunge", pop["by_window"], "wr")
    pw_ev = _per_window_list(cell_mask, sample, window_arr, y_ev, "ev", pop["by_window"], "ev")
    b_wr = bayes_layer(pw_wr, "wr", rng)
    b_plunge = bayes_layer(pw_plunge, "wr", rng)
    b_ev = bayes_layer(pw_ev, "ev", rng)
    p_sign_wr = b_wr["P_sign_consistent"] if b_wr else float("nan")
    p_sign_plunge = b_plunge["P_sign_consistent"] if b_plunge else float("nan")
    p_pooled_ev = b_ev["P_pooled_gt0"] if b_ev else float("nan")
    signs = window_signs(pw_wr)

    # --- replication sign check (leg6) --------------------------------------
    repl_resolved = int(np.sum(~np.isnan(y_repl_wr[cell_mask])))
    repl_same_sign = None
    if repl_resolved >= MIN_N_POOLED and not math.isnan(pop["repl_wr"]):
        repl_wr_cell = float(np.nansum(y_repl_wr[cell_mask])) / repl_resolved
        d_repl = repl_wr_cell - pop["repl_wr"]
        if not math.isnan(d_wr_pp):
            repl_same_sign = (d_repl > 0) == (d_wr_pp > 0)

    row = dict(
        peak_state=pop_name, feature=feat_label, cell=str(cell_value),
        n=ncell, resolved_n=resolved_wr,
        apex_wr=wr_cell, d_wr_pp=d_wr_pp, plunge_rate=plunge_cell, d_plunge_pp=d_plunge_pp,
        apex_ev=ev_cell, d_ev=d_ev,
        z_raw_wr=z_raw_wr, z_raw_plunge=z_raw_plunge,
        z_clust_wr=z_clust_wr, z_clust_plunge=z_clust_plunge,
        z_clust_wr_n_used=cell_wr["n_used"], z_clust_plunge_n_used=cell_plunge["n_used"],
        t_controlled_wr=ctl_wr["t"], t_controlled_plunge=ctl_plunge["t"],
        t_controlled_wr_n_used=ctl_wr["n_used"], t_controlled_wr_method=ctl_wr["method"],
        t_controlled_plunge_n_used=ctl_plunge["n_used"], t_controlled_plunge_method=ctl_plunge["method"],
        P_sign_consistent_wr=p_sign_wr, P_sign_consistent_plunge=p_sign_plunge,
        P_pooled_ev=p_pooled_ev, signs=signs,
        repl_resolved_n=repl_resolved, repl_same_sign=repl_same_sign,
        interaction_delta_wr_pp=None, interaction_delta_plunge_pp=None, interaction_delta_ev=None,
    )

    if pop_name != "UNCONDITIONAL" and marg_cache is not None:
        marg = marg_cache.get((feat_label, str(cell_value)))
        if marg is not None:
            if not (math.isnan(d_wr_pp) or math.isnan(marg["d_wr_pp"])):
                row["interaction_delta_wr_pp"] = abs(d_wr_pp) - abs(marg["d_wr_pp"])
            if not (math.isnan(d_plunge_pp) or math.isnan(marg["d_plunge_pp"])):
                row["interaction_delta_plunge_pp"] = abs(d_plunge_pp) - abs(marg["d_plunge_pp"])
            if not (math.isnan(d_ev) or math.isnan(marg["d_ev"])):
                row["interaction_delta_ev"] = abs(d_ev) - abs(marg["d_ev"])

    return row


def run_cohort_scan(df: pl.DataFrame, rng):
    n = df.height
    y_wr = df["apex_win"].to_numpy().astype(float)
    y_ev = df["apex_ev"].to_numpy().astype(float)
    y_plunge = (df["apex_ev"].to_numpy() == -0.7).astype(float)
    y_repl_wr = (df["repl_win"].to_numpy().astype(float) if "repl_win" in df.columns
                 else np.full(n, np.nan))

    overall_z = _zscore_nan(df["overall"].to_numpy().astype(float))
    trend_z = _zscore_nan(df["trend"].to_numpy().astype(float))
    runup20_sig_z = _zscore_nan(df["runup20_sig"].to_numpy().astype(float))
    ema50_z = _zscore_nan(df["control_pct_ema50"].to_numpy().astype(float))
    X_controls_full = np.column_stack([overall_z, trend_z, runup20_sig_z, ema50_z])

    window_arr = df["window"].to_numpy()
    dates_arr = df["date"].to_numpy()
    _, date_cluster = np.unique(dates_arr, return_inverse=True)

    pop_masks = {
        "UNCONDITIONAL": np.ones(n, dtype=bool),
        "P_high": df["P_high"].to_numpy().astype(bool),
        "P_run": df["P_run"].to_numpy().astype(bool),
        "P_both": df["P_both"].to_numpy().astype(bool),
    }
    pop_bases = _precompute_pop_bases(n, pop_masks, y_wr, y_plunge, y_ev, y_repl_wr, window_arr)

    results = []
    marg_cache = {}
    t0 = time.time()
    n_cells_attempted = 0
    for feat_label, col, kind in FEATURE_SPECS:
        bcol = f"b_{feat_label}"
        if bcol not in df.columns:
            continue
        bvals = df[bcol].to_numpy()
        cats = sorted({v for v in bvals.tolist() if v is not None})
        for cell in cats:
            bucket_mask = (bvals == cell)
            n_cells_attempted += 1
            marg_row = compute_cell(feat_label, cell, "UNCONDITIONAL", bucket_mask, pop_bases,
                                     y_wr, y_plunge, y_ev, y_repl_wr, X_controls_full,
                                     date_cluster, window_arr, rng)
            if marg_row is not None:
                marg_cache[(feat_label, str(cell))] = marg_row
                results.append(marg_row)
            for pname in ["P_high", "P_run", "P_both"]:
                n_cells_attempted += 1
                row = compute_cell(feat_label, cell, pname, bucket_mask, pop_bases,
                                    y_wr, y_plunge, y_ev, y_repl_wr, X_controls_full,
                                    date_cluster, window_arr, rng, marg_cache=marg_cache)
                if row is not None:
                    results.append(row)

    print(f"[cohort] {n_cells_attempted} cells enumerated, {len(results)} scored "
          f"(>= MIN_N_POOLED={MIN_N_POOLED}) in {time.time()-t0:.1f}s", flush=True)
    if not results:
        return pl.DataFrame({"peak_state": [], "feature": [], "cell": [], "n": []}), pop_bases
    return pl.DataFrame(results, infer_schema_length=None), pop_bases


# =============================================================================
# Gate evaluation (6 legs, PREREGISTRATION "Multiplicity bar")
# =============================================================================
def evaluate_gate(row: dict) -> dict:
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
        active = "wr" if abs(z_wr if not math.isnan(z_wr) else 0.0) >= abs(z_pl if not math.isnan(z_pl) else 0.0) else "plunge"

    t_ctl = row[f"t_controlled_{active}"]
    leg2 = (not math.isnan(t_ctl)) and abs(t_ctl) >= 2.5

    psign = row[f"P_sign_consistent_{active}"]
    leg3 = (psign is not None) and (not math.isnan(psign)) and psign > 0.90

    idelta = row.get(f"interaction_delta_{active}_pp")
    idelta_ev = row.get("interaction_delta_ev")
    is_marginal = row["peak_state"] == "UNCONDITIONAL"
    if is_marginal:
        leg4 = False
    else:
        leg4 = ((idelta is not None and not math.isnan(idelta) and idelta >= 2.0)
                or (idelta_ev is not None and not math.isnan(idelta_ev) and idelta_ev >= 0.02))

    leg5 = row["n"] >= 150
    leg6 = row.get("repl_same_sign") is True

    if is_marginal:
        z_active = z_wr if active == "wr" else z_pl
        verdict = "marginal_report_only" if (leg1 and abs(z_active) >= 4.0) else "marginal_null"
        n_legs = int(leg1) + int(leg2) + int(leg3)  # legs 4-6 N/A for marginal cells
    else:
        legs = [leg1, leg2, leg3, leg4, leg5, leg6]
        n_legs = sum(bool(x) for x in legs)
        verdict = "FINDING" if n_legs == 6 else ("NEAR_MISS" if n_legs >= 4 else "null")

    return dict(active_outcome=active, leg1_z_clust=leg1, leg2_t_controlled=leg2, leg3_p_sign=leg3,
                leg4_interaction=leg4, leg5_n=leg5, leg6_replication=leg6, n_legs_passed=n_legs,
                verdict=verdict)


def _f(v, fmt="+.2f", na="  n/a"):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return na
    try:
        return format(v, fmt)
    except (TypeError, ValueError):
        return str(v)


# =============================================================================
# SUMMARY block
# =============================================================================
def format_summary(tag, ledger_df, feat_frame, cohort_df, selftest_results, selftest_names,
                    p_high_choice, p_run_choice, p_both_choice, smoke, t_start):
    lines = ["==== SUMMARY ===="]

    def p(s=""):
        lines.append(s)

    for name, ok in zip(selftest_names, selftest_results):
        p(f"selftest {name}: {'PASS' if ok else 'FAIL'}")
    p(f"mode={'SMOKE' if smoke else 'FULL'} version=v74 tag={tag}")
    p(f"ledger N={ledger_df.height} feat_frame N={feat_frame.height}")

    if feat_frame.height:
        y_wr = feat_frame["apex_win"].to_numpy().astype(float)
        y_ev = feat_frame["apex_ev"].to_numpy()
        base_wr = float(np.nanmean(y_wr))
        base_plunge = float(np.mean(y_ev == -0.7))
        p(f"base_apex_wr={base_wr:.4f} (expect ~0.701) base_plunge_rate={base_plunge:.4f} (expect ~0.233)")
        windows_present = sorted(set(feat_frame["window"].drop_nulls().to_list()))
        p(f"windows_present={windows_present}")

    p("")
    p("-- prevalence ladder (locked, discretion-free; evaluated BEFORE reading outcome cols) --")
    p(f"P_high: rung={p_high_choice['rung']} col={p_high_choice.get('col')} pct={p_high_choice.get('pct')} "
      f"prevalence={_f(p_high_choice.get('prevalence'), '.1%')} (N_total={p_high_choice.get('n_total')})")
    p(f"P_run:  rung={p_run_choice['rung']} sig={p_run_choice.get('sig')} "
      f"prevalence={_f(p_run_choice.get('prevalence'), '.1%')} (N_total={p_run_choice.get('n_total')})")
    p(f"P_both: rung={p_both_choice['rung']} p_high_col={p_both_choice.get('p_high_col')} "
      f"p_high_pct={p_both_choice.get('p_high_pct')} p_run_sig={p_both_choice.get('p_run_sig')} "
      f"prevalence={_f(p_both_choice.get('prevalence'), '.1%')} (N_total={p_both_choice.get('n_total')})")
    if feat_frame.height and "P_high" in feat_frame.columns:
        for pname in ["P_high", "P_run", "P_both"]:
            n_true = int(feat_frame[pname].sum())
            p(f"  {pname} final in-frame count: {n_true}/{feat_frame.height} ({100.0*n_true/feat_frame.height:.1f}%)")

    p("")
    if cohort_df.height == 0 or "n" not in cohort_df.columns:
        p(f"cohort table: EMPTY (no cell reached MIN_N_POOLED={MIN_N_POOLED} -- expected in --smoke mode "
          f"at N~50 symbols)")
        p("==== END SUMMARY ====")
        return "\n".join(lines)

    p(f"cohort cells scored={cohort_df.height}")
    expected_false_z3 = cohort_df.height * 0.0027
    n_z3 = cohort_df.filter(
        pl.col("z_clust_wr").abs().fill_null(0.0) .ge(3.0)
        | pl.col("z_clust_plunge").abs().fill_null(0.0).ge(3.0)
    ).height
    p(f"expected_false_hits (alpha~0.0027 @ |z_clust|>=3, two-sided): {expected_false_z3:.2f}  "
      f"observed_cells_|z_clust|>=3(WR-or-plunge)={n_z3}")

    gate_rows = [evaluate_gate(r) for r in cohort_df.iter_rows(named=True)]
    gate_df = pl.DataFrame(gate_rows, infer_schema_length=None)
    full = pl.concat([cohort_df, gate_df], how="horizontal")

    zw = full["z_clust_wr"].fill_null(0.0).abs().to_numpy()
    zp = full["z_clust_plunge"].fill_null(0.0).abs().to_numpy()
    full = full.with_columns(pl.Series("_rank", np.maximum(zw, zp)))
    ranked = full.sort("_rank", descending=True)

    p("")
    p("-- top 20 cells by max(|z_clust_wr|,|z_clust_plunge|) --")
    p(f"{'rk':>3} {'state':<13} {'feature':<20} {'cell':<12} {'N':>5} {'dWRpp':>7} {'dPLpp':>7} "
      f"{'zWR':>6} {'zPL':>6} {'tWR':>6} {'tPL':>6} {'Psgn':>5} {'legs':>4} verdict")
    for i, r in enumerate(ranked.head(20).iter_rows(named=True)):
        psgn_key = f"P_sign_consistent_{r['active_outcome']}"
        p(f"{i+1:>3} {r['peak_state']:<13} {r['feature']:<20} {str(r['cell'])[:11]:<12} {r['n']:>5} "
          f"{_f(r['d_wr_pp'])} {_f(r['d_plunge_pp'])} {_f(r['z_clust_wr'])} {_f(r['z_clust_plunge'])} "
          f"{_f(r['t_controlled_wr'])} {_f(r['t_controlled_plunge'])} {_f(r.get(psgn_key), '.2f')} "
          f"{r['n_legs_passed']:>4} {r['verdict']}")

    findings = full.filter(pl.col("verdict") == "FINDING")
    near_misses = full.filter(pl.col("verdict") == "NEAR_MISS")
    marginal_report = full.filter(pl.col("verdict") == "marginal_report_only")

    p("")
    p(f"-- FINDINGs (all 6 legs pass): {findings.height} --")
    for r in findings.iter_rows(named=True):
        z_active = r[f"z_clust_{r['active_outcome']}"]
        p(f"  {r['peak_state']} {r['feature']} cell={r['cell']} N={r['n']} d_wr_pp={_f(r['d_wr_pp'])} "
          f"d_plunge_pp={_f(r['d_plunge_pp'])} active={r['active_outcome']} z_clust={_f(z_active)}")

    p("")
    p(f"-- NEAR_MISSes (>=4/6 legs pass): {near_misses.height} --")
    for r in near_misses.iter_rows(named=True):
        p(f"  {r['peak_state']} {r['feature']} cell={r['cell']} N={r['n']} legs={r['n_legs_passed']}/6 "
          f"[z_clust={r['leg1_z_clust']} t_ctl={r['leg2_t_controlled']} psign={r['leg3_p_sign']} "
          f"interact={r['leg4_interaction']} n>=150={r['leg5_n']} repl={r['leg6_replication']}] "
          f"d_wr_pp={_f(r['d_wr_pp'])} d_plunge_pp={_f(r['d_plunge_pp'])}")

    p("")
    p(f"-- marginal/unconditional cells clearing report-only bar (|z_clust|>=4): {marginal_report.height} --")
    for r in marginal_report.head(15).iter_rows(named=True):
        p(f"  {r['feature']} cell={r['cell']} N={r['n']} active={r['active_outcome']} "
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
    print(f"[start] peak_fakeout mine.py smoke={args.smoke}", flush=True)

    ledger_df = load_ledger()
    if ledger_df.height == 0:
        print("[FATAL] ledger is empty after coverage-drop filter -- aborting")
        sys.exit(2)
    ledger_df = join_controls_and_repl(ledger_df)

    if args.smoke:
        counts_df = pl.read_parquet(OHLC_PATH, columns=["symbol"])
        counts = counts_df.group_by("symbol").agg(pl.len().alias("n")).to_dicts()
        bar_counts = {r["symbol"]: r["n"] for r in counts}
        smoke_syms = choose_smoke_symbols(ledger_df, bar_counts)
        ledger_df = ledger_df.filter(pl.col("symbol").is_in(smoke_syms))
        tag = "smoke"
        universe = set(smoke_syms)
    else:
        tag = "full"
        universe = set(ledger_df["symbol"].unique().to_list())

    print(f"[universe] {len(universe)} symbols, {ledger_df.height} ledger rows", flush=True)

    ohlc_arrays = load_ohlc_arrays(universe)
    missing_ohlc = universe - set(ohlc_arrays.keys())
    if missing_ohlc:
        print(f"[ohlc] WARNING: {len(missing_ohlc)} symbols had no OHLC rows: {sorted(missing_ohlc)[:10]}")

    mcap_by_sym, earnings_by_sym, vix_by_date = load_lookups()

    feat_frame = build_feature_frame(ledger_df, ohlc_arrays, mcap_by_sym, earnings_by_sym, vix_by_date)
    if feat_frame.height == 0:
        print("[FATAL] feature frame is empty -- aborting before self-tests")
        sys.exit(2)
    # Recompute 'window' fresh (rather than trust-carrying the ledger's copy) --
    # cheap and removes any dependency on that upstream column being bug-free.
    feat_frame = add_window_column(feat_frame)

    # ---- ONE fill_nan(None) choke point (polars NaN != null trap), THEN
    # self-tests, THEN buckets -- exact ordering per the Deliverables spec.
    # features.py returns NaN (not None) as its "insufficient data" sentinel;
    # this is the single place that normalizes it to a real null before any
    # strict-cast/is_null()-based logic downstream (self-tests included).
    feat_frame = feat_frame.with_columns(pl.col(pl.Float64).fill_nan(None))

    # ---- mandatory self-tests (after the choke point, before buckets) ----------
    print("\n[selftests] running...", flush=True)
    ok1 = selftest_lookahead(feat_frame, ohlc_arrays, n_sample=50)
    ok2 = selftest_base_rate(feat_frame, smoke=args.smoke)
    ok3 = selftest_holdout(ledger_df, feat_frame)
    ok4 = selftest_finX()
    ok5 = selftest_young_listing(feat_frame)
    selftest_results = (ok1, ok2, ok3, ok4, ok5)
    selftest_names = ("ST1_lookahead", "ST2_base_rate", "ST3_holdout", "ST4_finX", "ST5_young_listing")

    empty_choice = dict(rung="n/a", col=None, pct=float("nan"), sig=float("nan"),
                         p_high_col=None, p_high_pct=float("nan"), p_run_sig=float("nan"),
                         prevalence=float("nan"), n_total=0)
    if not all(selftest_results):
        print("\n[FATAL] one or more mandatory self-tests FAILED -- aborting before cohort analysis "
              "(per contract: STOP and fix before any cohort analysis).")
        summary_text = format_summary(tag, ledger_df, feat_frame, pl.DataFrame({"n": []}),
                                       selftest_results, selftest_names,
                                       empty_choice, empty_choice, empty_choice, args.smoke, t_start)
        print(summary_text)
        _write_summary(tag, summary_text)
        sys.exit(1)
    print("[selftests] all PASS\n", flush=True)

    # ---- prevalence ladder (locked; BEFORE reading outcome cols for tuning) ----
    feat_frame, p_high_choice, p_run_choice, p_both_choice = apply_peak_states(feat_frame)
    print(f"[prevalence] P_high rung={p_high_choice['rung']} prevalence={p_high_choice['prevalence']*100:.1f}%")
    print(f"[prevalence] P_run  rung={p_run_choice['rung']} prevalence={p_run_choice['prevalence']*100:.1f}%")
    print(f"[prevalence] P_both rung={p_both_choice['rung']} prevalence={p_both_choice['prevalence']*100:.1f}%",
          flush=True)

    # ---- buckets (global terciles + fixed cuts), THEN cohort scan --------------
    feat_frame, tercile_cuts = add_bucket_columns(feat_frame)
    print(f"[buckets] tercile cuts computed for: {sorted(tercile_cuts.keys())}", flush=True)

    rng = np.random.default_rng(RNG_SEED)
    cohort_df, pop_bases = run_cohort_scan(feat_frame, rng)

    # ---- write outputs -----------------------------------------------------
    feat_path = os.path.join(OUT_DIR, f"features_v74_{tag}.parquet")
    cohort_path = os.path.join(OUT_DIR, f"cohort_stats_v74_{tag}.parquet")
    feat_frame.write_parquet(feat_path)
    cohort_df.write_parquet(cohort_path)
    print(f"[output] wrote {feat_path} ({feat_frame.height} rows)")
    print(f"[output] wrote {cohort_path} ({cohort_df.height} rows)")

    summary_text = format_summary(tag, ledger_df, feat_frame, cohort_df, selftest_results, selftest_names,
                                   p_high_choice, p_run_choice, p_both_choice, args.smoke, t_start)
    print(summary_text)
    _write_summary(tag, summary_text)
    print(f"\n[done] total wall time {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
