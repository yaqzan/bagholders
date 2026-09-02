"""Wave/Cycle Mine -- statistical machinery, COPIED (not imported) from
experiments/peak_fakeout/mine.py (which itself copied/adapted it from
experiments/trend_ma_lattice/mine.py).

Copy-not-import reason (per PREREGISTRATION.md "Statistical machinery" +
peak_fakeout/mine.py's own module docstring): every experiment directory in
this family uses a bare local `import features` of its OWN features.py.
Python's sys.modules module-name cache would let whichever experiment's
features.py is imported FIRST silently win for every other experiment that
also does `import features` in the same process -- a real cross-experiment
collision if this stats module were imported from a shared location that
itself sits alongside a same-named `features` module. Copying the stats
layer (functions with no such name collision, but kept alongside the same
copy-lineage for a single source of truth per experiment) avoids any
ambiguity about which experiment's numerics apply. This file is wave_cycle_
mine's OWN copy; edits here do not affect peak_fakeout or trend_ma_lattice.

Everything below is IDENTICAL in numerics to peak_fakeout/mine.py's copies
(CR1 cluster-robust sandwich, Bayesian hierarchical pooling, prevalence/z
helpers, tercile bucketing) -- only cosmetic renames (none) or removed
peak-state-interaction plumbing (not needed here; wave_cycle_mine's cells
are marginal-only, see PREREGISTRATION.md "Cells (marginal only -- NO
peak-state interaction)").
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl

MIN_N_POOLED = 30          # minimum resolved-N to appear as a scored cohort cell
MIN_N_WINDOW = 10          # minimum per-window N to enter Bayesian pooling / sign-consistency
MIN_CELL_CONTROL_SIDE = 20  # minimum N on EACH side (bucket / rest) to attempt a clustered fit
N_MC = 10000

WINDOW_ORDER = ["2021", "2022", "2023", "2024", "2025+"]


def _zscore_nan(x):
    """z-score using non-NaN mean/std; NaN STAYS NaN (no imputation) -- the
    controlled regression's finX guard relies on this (imputing NaN->0 would
    make every row 'finite' and silently defeat the finX mask)."""
    finite = x[~np.isnan(x)]
    if finite.size == 0:
        return np.full_like(x, np.nan)
    mu, sd = finite.mean(), finite.std()
    if not sd or math.isnan(sd) or sd < 1e-9:
        sd = 1.0
    return (x - mu) / sd


def bayes_layer(per_window, metric, rng, n_mc=N_MC):
    """Jeffreys per-window + hierarchical (random-effects) pooling.
    Copied verbatim from peak_fakeout/mine.py (itself copied from
    trend_ma_lattice/mine.py's A4 Bayesian layer)."""
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
    infeasible/unstable (small-N separation etc)."""
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
    'method' explaining why on any infeasible path."""
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
    """Naive (non-clustered) binomial-proportion z. Report-only -- NEVER
    gates (PREREGISTRATION.md leg1 requires the CR1-clustered z)."""
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


def compute_tercile_cuts(df: pl.DataFrame, col: str):
    vals = df[col].drop_nulls()
    if vals.len() < 30:
        return None
    q1 = vals.quantile(1 / 3, interpolation="linear")
    q2 = vals.quantile(2 / 3, interpolation="linear")
    return (q1, q2)


def tercile_expr(col, q1, q2):
    c = pl.col(col)
    return (pl.when(c.is_null()).then(None)
              .when(c <= q1).then(pl.lit("T1_low"))
              .when(c <= q2).then(pl.lit("T2_mid"))
              .otherwise(pl.lit("T3_high")))
