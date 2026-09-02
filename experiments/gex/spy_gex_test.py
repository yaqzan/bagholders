"""
spy_gex_test.py -- Track A of the GEX cluster experiment (DESIGN_CLUSTER.md
"Track A -- SPY market-level GEX regime / escape" + the 2026-07-07 AMENDMENT).

Two variants, both gated on data availability (this script must be safely
runnable BEFORE either variant's real inputs exist -- that is the VERIFY-
mechanics contract):

  A1 (SPY-chain, as originally registered, coverage-caveated): daily SPY
     dealer-GEX/cluster series from .cache/experiment_data/spy_gex_features.parquet,
     with the chain-quality gate from the AMENDMENT (n_strikes>=50 AND
     n_contracts>=500). PENDING if that file does not exist yet -- only the
     2-row spy_gex_features_smoke.parquet exists as of this writing, so A1's
     mechanics are verified against the smoke file and the real run deferred.

  A2 (cross-sectional aggregate, pre-registered in the AMENDMENT): daily
     market-level series aggregated from the EXISTING per-name signal-date
     features in .cache/experiment_data/gex_features.parquet -- mkt_gex_ratio
     (mean gex_ratio), mkt_neg_share (share gex_regime==-1), gated on
     n_signals>=30/day. This needs the 36-col cluster-feature schema (this
     script only touches gex_ratio/gex_regime, which are present in BOTH the
     old 25-col and new 36-col builds, so it can run either way -- but the
     AMENDMENT ties A2's readiness to "per-name features are ready" i.e. the
     36-col rebuild, so this script gates on column count and reports PENDING
     against the old 25-col full file, verifying mechanics against the 36-col
     gex_features_smoke.parquet instead).

Both variants share: three light MySQL control queries (SPY closes,
market_regime, market_breadth -- cached to parquet, skipped on cache hit),
week-clustered OLS (adapted from gex_test.py's date-clustered sandwich SE),
date-halves stability, and the top-vs-bottom-quintile forward-DD materiality
bar. Output: spy_gex_results.txt + .json, every cell N-labeled, N<30 SKIPPED.

READ-ONLY offline experiment. New files only under experiments/gex/ and
.cache/experiment_data/. MySQL touched ONLY for the three control/price
queries below (each a single light SELECT, cached after first run). Never
touches scoring.py/core.py/simulator.py/api.py/strategy_config.py/
dealer_gex.py/DESIGN.md/DESIGN_CLUSTER.md/MATH_SPEC_CLUSTERS.md, and never
writes anywhere under experiments/iv_skew/.

Usage:
    python experiments/gex/spy_gex_test.py

PYTHONUTF8=1 required (ASCII-only prints; no statsmodels -- numpy OLS only).
"""
from __future__ import annotations

import argparse
import json
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

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
CACHE_DIR = os.path.join(_ROOT, ".cache", "experiment_data")

GEX_FEATURES = os.path.join(CACHE_DIR, "gex_features.parquet")                     # A2 real input
GEX_FEATURES_SMOKE = os.path.join(CACHE_DIR, "gex_features_smoke.parquet")         # A2 mechanics-verify fallback
SPY_GEX_FEATURES = os.path.join(CACHE_DIR, "spy_gex_features.parquet")             # A1 real input
SPY_GEX_FEATURES_SMOKE = os.path.join(CACHE_DIR, "spy_gex_features_smoke.parquet")  # A1 mechanics-verify fallback

RS_LEDGER = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
PROXY_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "proxy_ledger.parquet")
IV_LEDGER_EXT = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger_ext.parquet")

SPY_CLOSES_CACHE = os.path.join(CACHE_DIR, "spy_closes.parquet")
MKT_REGIME_CACHE = os.path.join(CACHE_DIR, "mkt_regime_ctrl.parquet")
MKT_BREADTH_CACHE = os.path.join(CACHE_DIR, "mkt_breadth_ctrl.parquet")

OUT_TXT = os.path.join(_HERE, "spy_gex_results.txt")
OUT_JSON = os.path.join(_HERE, "spy_gex_results.json")

QUERY_START = "2025-01-02"
QUERY_END = "2026-07-06"
EXT_GAP_CUTOFF = "2026-05-15"     # in-sample cutoff for anything touching cohort labels (A2)
MIN_CELL_N = 30                   # small-N cells must be visibly skipped
A2_MIN_SIGNALS_PER_DAY = 30       # AMENDMENT: days with <30 signals excluded
A1_MIN_STRIKES = 50               # AMENDMENT chain-quality gate
A1_MIN_CONTRACTS = 500            # AMENDMENT chain-quality gate
FWD_VOL_WINDOWS = (5, 10)         # forward realized-vol horizons (trading bars)
FWD_DD_WINDOW = 10                # forward max-drawdown horizon (trading bars)
QUINTILE_BAR = 1.5                # AMENDMENT/DESIGN economic-materiality bar (top vs bottom quintile fwd DD ratio)

A1_SERIES = ["gex_ratio", "flip_dist", "gap_below", "esc_below"]
A2_SERIES = ["mkt_gex_ratio", "mkt_neg_share"]
CONTROL_COLS = ["vix_close", "vix_chg5", "mcclellan_oscillator", "trin", "breadth_ema"]
LABELS = ["fwd_vol_5d", "fwd_vol_10d", "fwd_dd_10d", "cohort_net_path", "cohort_pnl15"]
PRIMARY_DD_LABEL = "fwd_dd_10d"


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Small helpers (mirrors gex_test.py's conventions)
# ---------------------------------------------------------------------------
def n_ok(n, label=""):
    return n is not None and n >= MIN_CELL_N


def skip_str(n):
    return "SKIPPED (N=%d)" % (0 if n is None else n)


def zscore(x: np.ndarray) -> np.ndarray:
    mu = np.nanmean(x)
    sd = np.nanstd(x, ddof=1)
    if not np.isfinite(sd) or sd < 1e-12:
        return np.full_like(x, np.nan)
    return (x - mu) / sd


def quintile_edges(x: np.ndarray):
    qs = np.nanpercentile(x, [0, 20, 40, 60, 80, 100])
    if len(np.unique(qs)) < 3:
        return None
    return qs


def quintile_assign(x: np.ndarray, edges: np.ndarray) -> np.ndarray:
    bins = np.digitize(x, edges[1:-1], right=True) + 1
    bins = np.clip(bins, 1, 5)
    return bins


def iso_week_key(dates: np.ndarray) -> np.ndarray:
    """Convert an array of date-like values to an ISO 'YYYY-Www' string cluster key."""
    out = []
    for d in dates:
        if isinstance(d, (np.datetime64,)):
            d = d.astype("datetime64[D]").item()
        if hasattr(d, "isocalendar"):
            iso = d.isocalendar()
            out.append("%04d-W%02d" % (iso[0], iso[1]))
        else:
            dd = date.fromisoformat(str(d)[:10])
            iso = dd.isocalendar()
            out.append("%04d-W%02d" % (iso[0], iso[1]))
    return np.array(out)


# ---------------------------------------------------------------------------
# OLS + WEEK-clustered SE (adapted from gex_test.py's ols_with_clustered_se;
# the only change is the cluster key -- ISO year-week instead of date)
# ---------------------------------------------------------------------------
def ols_with_clustered_se(y: np.ndarray, X: np.ndarray, groups: np.ndarray):
    """
    Plain-vanilla numpy OLS with homoskedastic SE AND WEEK-clustered sandwich SE.
    X must NOT include an intercept column (we add one). groups = ISO-week keys.
    Returns dict: beta (excl. intercept), se_plain, t_plain, se_clust, t_clust, n, g (n weekly clusters).
    """
    n = X.shape[0]
    Xc = np.column_stack([np.ones(n), X])
    k = Xc.shape[1]

    XtX = Xc.T @ Xc
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(XtX)

    beta_full, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ beta_full

    # --- plain homoskedastic SE ---
    dof = max(n - k, 1)
    sigma2 = float((resid @ resid) / dof)
    cov_plain = sigma2 * XtX_inv
    se_plain = np.sqrt(np.diag(cov_plain))

    # --- WEEK-clustered sandwich SE ---
    uniq_groups = np.unique(groups)
    G = uniq_groups.shape[0]
    meat = np.zeros((k, k))
    for g in uniq_groups:
        mask = groups == g
        Xg = Xc[mask]
        eg = resid[mask]
        score_g = Xg.T @ eg
        meat += np.outer(score_g, score_g)
    small_sample = (G / max(G - 1, 1)) if G > 1 else 1.0
    cov_clust = small_sample * (XtX_inv @ meat @ XtX_inv)
    se_clust = np.sqrt(np.diag(cov_clust))

    with np.errstate(divide='ignore', invalid='ignore'):
        t_plain = beta_full / se_plain
        t_clust = beta_full / se_clust

    return {
        "beta": beta_full,
        "se_plain": se_plain,
        "t_plain": t_plain,
        "se_clust": se_clust,
        "t_clust": t_clust,
        "n": n,
        "g": int(G),
    }


def run_ols_one_series(df: pl.DataFrame, label_col: str, series_col: str, control_cols: list,
                        date_col: str = "date"):
    """
    Standardize label + series + controls (z-score), drop null rows, run OLS
    with plain + WEEK-clustered t. Returns a result dict or {"n":.., "skipped":True}.
    Column order in X: [series, *controls] -- series coefficient is beta index 1
    (index 0 = intercept).
    """
    needed = [label_col, series_col] + control_cols
    present = [c for c in needed if c in df.columns]
    if len(present) < len(needed) or df.height == 0:
        return {"n": 0, "skipped": True, "reason": "missing columns: %s" %
                 [c for c in needed if c not in df.columns]}

    sub = df.select([date_col] + needed)
    float_cols = [c for c in needed if sub[c].dtype in (pl.Float64, pl.Float32)]
    sub = sub.with_columns([pl.col(c).fill_nan(None) for c in float_cols]).drop_nulls()
    n = sub.height
    if not n_ok(n):
        return {"n": n, "skipped": True}

    y = zscore(sub[label_col].to_numpy().astype(np.float64))
    Xcols = []
    for c in [series_col] + control_cols:
        Xcols.append(zscore(sub[c].to_numpy().astype(np.float64)))
    X = np.column_stack(Xcols)

    valid_mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    n_valid = int(valid_mask.sum())
    if not n_ok(n_valid):
        return {"n": n_valid, "skipped": True}
    y = y[valid_mask]
    X = X[valid_mask]
    dates_valid = sub[date_col].to_numpy()[valid_mask]
    week_groups = iso_week_key(dates_valid)

    res = ols_with_clustered_se(y, X, week_groups)
    return {
        "n": n_valid,
        "skipped": False,
        "series": series_col,
        "label": label_col,
        "beta": float(res["beta"][1]),
        "t_plain": float(res["t_plain"][1]),
        "t_clust": float(res["t_clust"][1]),
        "se_plain": float(res["se_plain"][1]),
        "se_clust": float(res["se_clust"][1]),
        "n_week_clusters": res["g"],
        "control_betas": {c: float(res["beta"][2 + i]) for i, c in enumerate(control_cols)},
    }


# ---------------------------------------------------------------------------
# Control data: THREE light MySQL queries, cached to parquet, skipped on hit
# ---------------------------------------------------------------------------
def _load_or_query(cache_path, build_fn, label):
    if os.path.exists(cache_path):
        df = pl.read_parquet(cache_path)
        log("%s: cache HIT at %s (N=%d rows) -- query SKIPPED" % (label, cache_path, df.height))
        return df, {"source": "cache_hit", "rows": df.height, "elapsed_s": 0.0}
    log("%s: cache MISS -- running light query, %s..%s" % (label, QUERY_START, QUERY_END))
    t0 = time.time()
    df = build_fn()
    elapsed = time.time() - t0
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    df.write_parquet(cache_path)
    log("%s: query returned N=%d rows in %.2fs -- wrote %s" % (label, df.height, elapsed, cache_path))
    return df, {"source": "fresh_query", "rows": df.height, "elapsed_s": round(elapsed, 2)}


def _query_spy_closes():
    """Query 1/3: SPY daily closes. Also computes forward vol/DD labels here
    (they are pure functions of the close series, not a second query)."""
    from database.trader_database import DB

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=30000")
    cur = DB.execute_sql(
        "SELECT date, close FROM price_history WHERE symbol=%s AND date BETWEEN %s AND %s ORDER BY date",
        ("SPY", QUERY_START, QUERY_END),
    )
    rows = [r for r in cur.fetchall() if r[1] is not None]  # filter date+close together, keep aligned
    df = pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "close": [float(r[1]) for r in rows],
        },
        schema={"date": pl.Date, "close": pl.Float64},
    ).sort("date")
    return _compute_spy_labels(df)


def _compute_spy_labels(df: pl.DataFrame) -> pl.DataFrame:
    """
    Forward 5d/10d realized vol (std of daily log returns over the NEXT 5/10
    trading bars, annualized x sqrt(252)) and forward 10d max drawdown
    (min over next 10 bars of close_t+k/close_t - 1). Strictly AFTER date t
    (no same-day leakage): label at row i uses close[i+1 .. i+W] only.
    """
    close = df["close"].to_numpy().astype(np.float64)
    n = len(close)
    log_ret = np.full(n, np.nan)
    if n > 1:
        log_ret[1:] = np.log(close[1:] / close[:-1])

    # log_ret[k] = ln(close[k]/close[k-1]) -- the return REALIZED on bar k.
    # Label at row i must use ONLY bars strictly after i, i.e. log_ret[i+1 .. i+w].
    fwd_vol = {w: np.full(n, np.nan) for w in FWD_VOL_WINDOWS}
    fwd_dd = np.full(n, np.nan)
    for i in range(n):
        for w in FWD_VOL_WINDOWS:
            if i + w < n:
                window = log_ret[i + 1:i + w + 1]
                if len(window) == w and np.all(np.isfinite(window)):
                    fwd_vol[w][i] = float(np.std(window, ddof=1) * np.sqrt(252))
        if i + FWD_DD_WINDOW < n:
            fwd_path = close[i + 1:i + FWD_DD_WINDOW + 1] / close[i] - 1.0
            if len(fwd_path) == FWD_DD_WINDOW:
                fwd_dd[i] = float(np.min(fwd_path))

    out = df.with_columns([
        pl.Series("fwd_vol_5d", fwd_vol[5], dtype=pl.Float64),
        pl.Series("fwd_vol_10d", fwd_vol[10], dtype=pl.Float64),
        pl.Series("fwd_dd_10d", fwd_dd, dtype=pl.Float64),
    ])
    return out


def _query_mkt_regime():
    """Query 2/3: market_regime daily rows (VIX + composite). Derives vix_chg5."""
    from database.trader_database import DB

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=30000")
    cur = DB.execute_sql(
        "SELECT date, vix_close, vix_10d_change, spy_close, spy_ema50, spy_ema200, "
        "internal_breadth_score, vix_score, market_trend_score, regime_composite, regime_multiplier "
        "FROM market_regime WHERE date BETWEEN %s AND %s ORDER BY date",
        (QUERY_START, QUERY_END),
    )
    rows = cur.fetchall()

    def f(v):
        return float(v) if v is not None else None

    df = pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "vix_close": [f(r[1]) for r in rows],
            "vix_10d_change": [f(r[2]) for r in rows],
            "spy_close": [f(r[3]) for r in rows],
            "spy_ema50": [f(r[4]) for r in rows],
            "spy_ema200": [f(r[5]) for r in rows],
            "internal_breadth_score": [f(r[6]) for r in rows],
            "vix_score": [f(r[7]) for r in rows],
            "market_trend_score": [f(r[8]) for r in rows],
            "regime_composite": [f(r[9]) for r in rows],
            "regime_multiplier": [f(r[10]) for r in rows],
        },
        schema={"date": pl.Date, "vix_close": pl.Float64, "vix_10d_change": pl.Float64,
                "spy_close": pl.Float64, "spy_ema50": pl.Float64, "spy_ema200": pl.Float64,
                "internal_breadth_score": pl.Float64, "vix_score": pl.Float64,
                "market_trend_score": pl.Float64, "regime_composite": pl.Float64,
                "regime_multiplier": pl.Float64},
    ).sort("date")

    vix = df["vix_close"].to_numpy().astype(np.float64)
    n = len(vix)
    vix_chg5 = np.full(n, np.nan)
    for i in range(n):
        if i - 5 >= 0 and np.isfinite(vix[i]) and np.isfinite(vix[i - 5]):
            vix_chg5[i] = vix[i] - vix[i - 5]
    df = df.with_columns(pl.Series("vix_chg5", vix_chg5, dtype=pl.Float64))
    return df


def _query_mkt_breadth():
    """Query 3/3: market_breadth daily rows (McClellan, TRIN, EMA% breadth)."""
    from database.trader_database import DB

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=30000")
    cur = DB.execute_sql(
        "SELECT date, mcclellan_oscillator, mcclellan_summation, trin, "
        "pct_above_ema50, pct_above_ema200, ad_line "
        "FROM market_breadth WHERE date BETWEEN %s AND %s ORDER BY date",
        (QUERY_START, QUERY_END),
    )
    rows = cur.fetchall()

    def f(v):
        return float(v) if v is not None else None

    df = pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "mcclellan_oscillator": [f(r[1]) for r in rows],
            "mcclellan_summation": [f(r[2]) for r in rows],
            "trin": [f(r[3]) for r in rows],
            "pct_above_ema50": [f(r[4]) for r in rows],
            "pct_above_ema200": [f(r[5]) for r in rows],
            "ad_line": [f(r[6]) for r in rows],
        },
        schema={"date": pl.Date, "mcclellan_oscillator": pl.Float64, "mcclellan_summation": pl.Float64,
                "trin": pl.Float64, "pct_above_ema50": pl.Float64, "pct_above_ema200": pl.Float64,
                "ad_line": pl.Float64},
    ).sort("date")
    # "breadth EMA%" control per DESIGN_CLUSTER.md Track A controls list -> pct_above_ema50
    df = df.rename({"pct_above_ema50": "breadth_ema"})
    return df


def build_controls():
    """Runs (or loads-from-cache) all THREE light control queries and returns
    a single merged controls frame keyed by date, plus per-query stats."""
    spy_df, spy_meta = _load_or_query(SPY_CLOSES_CACHE, _query_spy_closes, "Query1 SPY closes")
    regime_df, regime_meta = _load_or_query(MKT_REGIME_CACHE, _query_mkt_regime, "Query2 market_regime")
    breadth_df, breadth_meta = _load_or_query(MKT_BREADTH_CACHE, _query_mkt_breadth, "Query3 market_breadth")

    controls = regime_df.select(["date", "vix_close", "vix_chg5"]).join(
        breadth_df.select(["date", "mcclellan_oscillator", "trin", "breadth_ema"]),
        on="date", how="inner",
    )
    log("controls merged (regime x breadth, inner on date): N=%d" % controls.height)

    meta = {
        "spy_closes": {**spy_meta, "date_min": str(spy_df["date"].min()) if spy_df.height else None,
                       "date_max": str(spy_df["date"].max()) if spy_df.height else None},
        "mkt_regime": {**regime_meta, "date_min": str(regime_df["date"].min()) if regime_df.height else None,
                       "date_max": str(regime_df["date"].max()) if regime_df.height else None},
        "mkt_breadth": {**breadth_meta, "date_min": str(breadth_df["date"].min()) if breadth_df.height else None,
                        "date_max": str(breadth_df["date"].max()) if breadth_df.height else None},
        "controls_merged_n": controls.height,
    }
    return spy_df, controls, meta


# ---------------------------------------------------------------------------
# A2: cross-sectional aggregate from per-name gex_features.parquet
# ---------------------------------------------------------------------------
def _check_a2_readiness():
    """
    AMENDMENT: A2 "runs when per-name features are ready" i.e. the 36-col
    cluster-feature rebuild. Returns (path_to_use, status, n_cols) where
    status in {"real", "mechanics_verify_smoke", "absent"}.
    """
    if os.path.exists(GEX_FEATURES):
        df = pl.read_parquet(GEX_FEATURES, n_rows=1)
        n_cols = len(df.columns)
        if n_cols >= 36:
            return GEX_FEATURES, "real", n_cols
        # old-schema full file present but not yet rebuilt with cluster cols
        if os.path.exists(GEX_FEATURES_SMOKE):
            smoke_df = pl.read_parquet(GEX_FEATURES_SMOKE, n_rows=1)
            return GEX_FEATURES_SMOKE, "mechanics_verify_smoke", len(smoke_df.columns)
        return GEX_FEATURES, "PENDING_old_schema", n_cols
    if os.path.exists(GEX_FEATURES_SMOKE):
        smoke_df = pl.read_parquet(GEX_FEATURES_SMOKE, n_rows=1)
        return GEX_FEATURES_SMOKE, "mechanics_verify_smoke", len(smoke_df.columns)
    return None, "absent", 0


def build_a2_daily_series():
    """
    Returns (daily_df, status_dict). daily_df has columns:
      date, mkt_gex_ratio, mkt_neg_share, n_signals
    gated on n_signals >= A2_MIN_SIGNALS_PER_DAY (rows below the gate are
    DROPPED, not zero-filled -- excluded days are reported in status_dict).
    """
    path, status, n_cols = _check_a2_readiness()
    result = {"status": status, "path": path, "n_cols": n_cols}
    if path is None:
        log("A2: gex_features.parquet ABSENT (and no smoke fallback) -- A2 PENDING")
        return pl.DataFrame(), result

    feats = pl.read_parquet(path)
    if feats.height and feats["date"].dtype != pl.Date:
        feats = feats.with_columns(pl.col("date").cast(pl.Date))

    needed = ["date", "gex_ratio", "gex_regime"]
    missing = [c for c in needed if c not in feats.columns]
    if missing:
        log("A2: source %s missing columns %s -- A2 PENDING" % (path, missing))
        result["missing_columns"] = missing
        return pl.DataFrame(), result

    per_day = (
        feats.select(["date", "gex_ratio", "gex_regime"])
        .drop_nulls()
        .group_by("date")
        .agg([
            pl.col("gex_ratio").mean().alias("mkt_gex_ratio"),
            (pl.col("gex_regime") == -1).mean().alias("mkt_neg_share"),
            pl.len().alias("n_signals"),
        ])
        .sort("date")
    )
    n_days_total = per_day.height
    gated = per_day.filter(pl.col("n_signals") >= A2_MIN_SIGNALS_PER_DAY)
    n_excluded = n_days_total - gated.height
    log("A2 (%s): %d raw signal-days -> %d days with n_signals>=%d (%d excluded)" %
        (status, n_days_total, gated.height, A2_MIN_SIGNALS_PER_DAY, n_excluded))

    result.update({
        "n_days_total": n_days_total,
        "n_days_gated": gated.height,
        "n_days_excluded_low_n": n_excluded,
        "date_min": str(gated["date"].min()) if gated.height else None,
        "date_max": str(gated["date"].max()) if gated.height else None,
    })
    return gated, result


def build_a2_cohort_labels():
    """
    Date-aggregated cohort outcome labels for A2, IN-SAMPLE ONLY (<=2026-05-15
    per AMENDMENT/DESIGN "In-sample only ... for anything touching cohort
    labels"): mean net_path by date from rs_ledger (leak-guarded), and mean
    pnl15 by date reusing gex_test.py's panel-B assembly convention (proxy_ledger
    opt_skew-notnull rows + ext resolved rows, pooled, grouped by date).
    """
    net_path_daily = pl.DataFrame()
    if os.path.exists(RS_LEDGER):
        rs = pl.read_parquet(RS_LEDGER)
        rs = rs.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
        rs = rs.filter(pl.col("date") <= pl.lit(EXT_GAP_CUTOFF).str.strptime(pl.Date, "%Y-%m-%d"))
        if rs.height and {"mfe15", "mae15"}.issubset(rs.columns):
            rs = rs.with_columns((pl.col("mfe15") - pl.col("mae15")).alias("net_path"))
            assert_no_holdout_leak(rs, "spy_gex_a2_net_path")
            net_path_daily = (
                rs.select(["date", "net_path"]).drop_nulls()
                .group_by("date").agg([
                    pl.col("net_path").mean().alias("cohort_net_path"),
                    pl.len().alias("n_net_path"),
                ]).sort("date")
            )
        log("A2 cohort net_path (rs_ledger, <=%s): %d dates" % (EXT_GAP_CUTOFF, net_path_daily.height))
    else:
        log("A2 cohort net_path: rs_ledger.parquet not found -- SKIPPED")

    pnl15_daily = pl.DataFrame()
    frames = []
    if os.path.exists(PROXY_LEDGER):
        proxy = pl.read_parquet(PROXY_LEDGER)
        proxy = proxy.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
        proxy = proxy.filter(pl.col("opt_skew").is_not_null() & (pl.col("date") <= pl.lit(EXT_GAP_CUTOFF).str.strptime(pl.Date, "%Y-%m-%d")))
        if proxy.height and "pnl15" in proxy.columns:
            frames.append(proxy.select(["date", "pnl15"]))
        log("A2 cohort pnl15 proxy-arm (opt_skew notnull, <=%s): N=%d" % (EXT_GAP_CUTOFF, proxy.height))
    if os.path.exists(IV_LEDGER_EXT):
        ext = pl.read_parquet(IV_LEDGER_EXT)
        ext = ext.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
        ext = ext.filter(
            (pl.col("pnl15_resolved") == True) &  # noqa: E712
            (pl.col("date") <= pl.lit(EXT_GAP_CUTOFF).str.strptime(pl.Date, "%Y-%m-%d"))
        )
        if ext.height and "pnl15" in ext.columns:
            frames.append(ext.select(["date", "pnl15"]))
        log("A2 cohort pnl15 ext-arm (pnl15_resolved, <=%s): N=%d" % (EXT_GAP_CUTOFF, ext.height))
    if frames:
        pooled = pl.concat(frames, how="vertical_relaxed").drop_nulls()
        assert_no_holdout_leak(pooled, "spy_gex_a2_pnl15")
        pnl15_daily = (
            pooled.group_by("date").agg([
                pl.col("pnl15").mean().alias("cohort_pnl15"),
                pl.len().alias("n_pnl15"),
            ]).sort("date")
        )
    log("A2 cohort pnl15 pooled: %d dates" % pnl15_daily.height)

    return net_path_daily, pnl15_daily


# ---------------------------------------------------------------------------
# A1: SPY-chain daily features with chain-quality gate
# ---------------------------------------------------------------------------
def _check_a1_readiness():
    if os.path.exists(SPY_GEX_FEATURES):
        df = pl.read_parquet(SPY_GEX_FEATURES, n_rows=1)
        return SPY_GEX_FEATURES, "real", len(df.columns)
    if os.path.exists(SPY_GEX_FEATURES_SMOKE):
        df = pl.read_parquet(SPY_GEX_FEATURES_SMOKE, n_rows=1)
        return SPY_GEX_FEATURES_SMOKE, "mechanics_verify_smoke", len(df.columns)
    return None, "absent", 0


def build_a1_daily_series():
    """
    Returns (gated_df, status_dict). Applies the AMENDMENT chain-quality gate:
    exclude dates with n_strikes < 50 OR n_contracts < 500 (columns already
    present in the SPY daily-features schema per gex_build_report.json /
    dealer_gex.py's diagnostic output columns).
    """
    path, status, n_cols = _check_a1_readiness()
    result = {"status": status, "path": path, "n_cols": n_cols}
    if path is None:
        log("A1: spy_gex_features.parquet ABSENT (and no smoke fallback) -- A1 PENDING")
        return pl.DataFrame(), result

    df = pl.read_parquet(path)
    if df.height and df["date"].dtype != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))

    needed = ["date", "n_strikes", "n_contracts"] + A1_SERIES
    missing = [c for c in needed if c not in df.columns]
    if missing:
        log("A1: source %s missing columns %s -- A1 PENDING" % (path, missing))
        result["missing_columns"] = missing
        return pl.DataFrame(), result

    n_total = df.height
    gate_mask = (pl.col("n_strikes") >= A1_MIN_STRIKES) & (pl.col("n_contracts") >= A1_MIN_CONTRACTS)
    excluded = df.filter(~gate_mask)
    gated = df.filter(gate_mask)
    n_excluded = excluded.height
    log("A1 (%s): %d raw dates -> %d pass chain-quality gate (n_strikes>=%d AND n_contracts>=%d), %d excluded" %
        (status, n_total, gated.height, A1_MIN_STRIKES, A1_MIN_CONTRACTS, n_excluded))
    if n_excluded:
        ex_list = excluded.select(["date", "n_strikes", "n_contracts"]).to_dicts()
        log("  excluded dates (first 10): %s" % ex_list[:10])
        result["excluded_dates_sample"] = [
            {"date": str(r["date"]), "n_strikes": r["n_strikes"], "n_contracts": r["n_contracts"]}
            for r in ex_list[:20]
        ]

    result.update({
        "n_dates_total": n_total,
        "n_dates_gated": gated.height,
        "n_dates_excluded_chain_quality": n_excluded,
        "date_min": str(gated["date"].min()) if gated.height else None,
        "date_max": str(gated["date"].max()) if gated.height else None,
    })
    return gated, result


# ---------------------------------------------------------------------------
# Shared analysis: OLS + halves + quintile-DD-materiality, per variant/series/label
# ---------------------------------------------------------------------------
def _halves_split(df: pl.DataFrame, date_col: str = "date"):
    """Date-median split into two halves (not necessarily equal-N if ties)."""
    if df.height == 0:
        return []
    ordered = df.sort(date_col)
    median_date = ordered[date_col][ordered.height // 2]
    first = ordered.filter(pl.col(date_col) <= median_date)
    second = ordered.filter(pl.col(date_col) > median_date)
    return [first, second]


def _quintile_dd_ratio(df: pl.DataFrame, series_col: str, dd_label_col: str = PRIMARY_DD_LABEL):
    """
    Top-vs-bottom quintile of `series_col` -> mean fwd_dd_10d ratio. fwd_dd_10d
    is a (usually negative) max-drawdown; the "ratio" is defined as
    mean(|dd| in more-adverse quintile) / mean(|dd| in less-adverse quintile)
    so a value >= 1.5 means the extreme quintile has 1.5x the drawdown
    magnitude of the other extreme (the AMENDMENT/DESIGN bar).
    """
    sub = df.select([series_col, dd_label_col]).drop_nulls()
    n = sub.height
    if not n_ok(n):
        return {"n": n, "skipped": True}
    x = sub[series_col].to_numpy().astype(np.float64)
    y = sub[dd_label_col].to_numpy().astype(np.float64)
    edges = quintile_edges(x)
    if edges is None:
        return {"n": n, "skipped": True, "reason": "degenerate distribution"}
    qbins = quintile_assign(x, edges)
    q1_mask = qbins == 1
    q5_mask = qbins == 5
    n1, n5 = int(q1_mask.sum()), int(q5_mask.sum())
    if n1 < MIN_CELL_N or n5 < MIN_CELL_N:
        return {"n": n, "skipped": True, "reason": "Q1/Q5 below MIN_CELL_N (n1=%d n5=%d)" % (n1, n5)}
    mean_dd_q1 = float(np.mean(np.abs(y[q1_mask])))
    mean_dd_q5 = float(np.mean(np.abs(y[q5_mask])))
    lo, hi = (mean_dd_q1, mean_dd_q5) if mean_dd_q1 <= mean_dd_q5 else (mean_dd_q5, mean_dd_q1)
    ratio = (hi / lo) if lo > 1e-12 else float("inf")
    return {
        "n": n, "skipped": False,
        "n_q1": n1, "n_q5": n5,
        "mean_abs_dd_q1": mean_dd_q1, "mean_abs_dd_q5": mean_dd_q5,
        "ratio_extreme_over_other": ratio,
        "passes_1.5x_bar": bool(ratio >= QUINTILE_BAR),
    }


def analyze_variant(variant_name: str, df: pl.DataFrame, series_list: list, out_lines: list, out_json: dict):
    """Runs OLS (plain+week-clust), halves-sign-stability, and quintile-DD-ratio
    for every series x label combo in `series_list` x LABELS on `df`."""
    out_lines.append("\n--- %s analysis ---\n" % variant_name)
    out_json[variant_name] = {}

    if df.height == 0:
        out_lines.append("  %s: empty panel -- analysis SKIPPED entirely\n" % variant_name)
        out_json[variant_name]["skipped"] = True
        return

    decisive_rows = []  # for the final compact table
    for series in series_list:
        if series not in df.columns:
            out_lines.append("  [%s] series %s not in panel -- SKIPPED\n" % (variant_name, series))
            continue
        out_json[variant_name][series] = {}
        for label in LABELS:
            key = "%s|%s" % (series, label)
            if label not in df.columns:
                out_lines.append("  [%s x %s]: %s (label column absent)\n" % (series, label, skip_str(0)))
                out_json[variant_name][series][label] = {"n": 0, "skipped": True, "reason": "label column absent"}
                continue

            res = run_ols_one_series(df, label, series, CONTROL_COLS)
            if res.get("skipped"):
                out_lines.append("  [%s x %s]: %s\n" % (series, label, skip_str(res.get("n"))))
                out_json[variant_name][series][label] = res
                decisive_rows.append((series, label, res.get("n", 0), None, None, None))
                continue

            out_lines.append(
                "  [%s x %s] N=%d (G=%d wk) beta=%.4f t_plain=%.3f t_clust=%.3f\n" %
                (series, label, res["n"], res["n_week_clusters"], res["beta"], res["t_plain"], res["t_clust"])
            )
            decisive_rows.append((series, label, res["n"], res["beta"], res["t_plain"], res["t_clust"]))

            # --- halves stability ---
            halves = _halves_split(df)
            signs = []
            for hi, half in enumerate(halves):
                hres = run_ols_one_series(half, label, series, CONTROL_COLS)
                if hres.get("skipped"):
                    signs.append(None)
                    out_lines.append("      half %d: %s\n" % (hi + 1, skip_str(hres.get("n"))))
                else:
                    sign = "+" if hres["beta"] > 0 else ("-" if hres["beta"] < 0 else "0")
                    signs.append(sign)
                    out_lines.append("      half %d: N=%d beta=%.4f (sign=%s) t_plain=%.3f\n" %
                                      (hi + 1, hres["n"], hres["beta"], sign, hres["t_plain"]))
            non_null = [s for s in signs if s is not None]
            flip = len(set(non_null)) > 1 if len(non_null) >= 2 else False
            out_lines.append("      halves sign sequence=%s flip=%s\n" % (signs, flip))
            res["halves_signs"] = signs
            res["halves_flip"] = flip
            out_json[variant_name][series][label] = res

        # --- quintile DD-materiality bar (primary label only) ---
        qres = _quintile_dd_ratio(df, series, PRIMARY_DD_LABEL)
        if qres.get("skipped"):
            out_lines.append("  [%s] quintile %s materiality: %s\n" % (series, PRIMARY_DD_LABEL, skip_str(qres.get("n"))))
        else:
            out_lines.append(
                "  [%s] quintile %s materiality: N=%d Q1(n=%d)=%.4f Q5(n=%d)=%.4f ratio=%.3fx bar>=1.5x:%s\n" %
                (series, PRIMARY_DD_LABEL, qres["n"], qres["n_q1"], qres["mean_abs_dd_q1"],
                 qres["n_q5"], qres["mean_abs_dd_q5"], qres["ratio_extreme_over_other"], qres["passes_1.5x_bar"])
            )
        out_json[variant_name].setdefault("_quintile_dd", {})[series] = qres

    out_json[variant_name]["_decisive_table"] = [
        {"series": s, "label": l, "n": n, "beta": b, "t_plain": tp, "t_clust": tc}
        for (s, l, n, b, tp, tc) in decisive_rows
    ]


def report_cutoff_subset(variant_name: str, df: pl.DataFrame, out_lines: list, out_json: dict):
    """
    "pure market labels (SPY vol/DD) may use the full covered window but REPORT
    the <=cutoff subset alongside (the bar is judged on the cutoff-safe subset)."
    Re-runs the primary-label OLS (fwd_dd_10d) restricted to date<=cutoff and
    reports it next to the full-window read, for every series.
    """
    out_lines.append("\n--- %s: <=%s cutoff-safe subset (judged bar) vs full window ---\n" %
                      (variant_name, HOLDOUT_CUTOFF.isoformat()))
    if df.height == 0:
        out_lines.append("  empty panel -- SKIPPED\n")
        return
    sub = df.filter(pl.col("date") <= pl.lit(HOLDOUT_CUTOFF.isoformat()).str.strptime(pl.Date, "%Y-%m-%d"))
    out_json.setdefault(variant_name, {})["cutoff_subset_n"] = sub.height
    out_json[variant_name]["full_window_n"] = df.height
    out_lines.append("  full-window N=%d, cutoff-safe (<=%s) N=%d\n" % (df.height, HOLDOUT_CUTOFF.isoformat(), sub.height))
    for series in (A1_SERIES if variant_name == "A1" else A2_SERIES):
        if series not in df.columns:
            continue
        full_res = run_ols_one_series(df, PRIMARY_DD_LABEL, series, CONTROL_COLS)
        cut_res = run_ols_one_series(sub, PRIMARY_DD_LABEL, series, CONTROL_COLS)

        def fmt(r):
            if r.get("skipped"):
                return skip_str(r.get("n"))
            return "N=%d beta=%.4f t_plain=%.3f t_clust=%.3f" % (r["n"], r["beta"], r["t_plain"], r["t_clust"])

        out_lines.append("  [%s x %s] full=%s | cutoff-safe=%s\n" % (series, PRIMARY_DD_LABEL, fmt(full_res), fmt(cut_res)))
        out_json[variant_name].setdefault("_cutoff_subset_compare", {})[series] = {
            "full": full_res, "cutoff_safe": cut_res,
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="GEX Track A: SPY market-level GEX regime / escape")
    parser.parse_args()

    t_start = time.time()
    log("=== spy_gex_test.py -- GEX Track A (DESIGN_CLUSTER.md + AMENDMENT) ===")
    log("holdout cutoff (from strategy_config.py): %s" % HOLDOUT_CUTOFF.isoformat())
    log("query window: %s..%s" % (QUERY_START, QUERY_END))

    out_lines = ["=== spy_gex_test.py results (Track A) ===\n"]
    out_json = {"meta": {}}
    out_lines.append("query window: %s..%s\n" % (QUERY_START, QUERY_END))
    out_lines.append("holdout cutoff: %s\n" % HOLDOUT_CUTOFF.isoformat())

    # --- THREE light control queries (cached) ---
    log("\n--- Building control caches (3 light MySQL queries, cached, skipped on hit) ---")
    spy_df, controls_df, controls_meta = build_controls()
    out_lines.append("\nControl caches:\n")
    for k, v in controls_meta.items():
        out_lines.append("  %s: %s\n" % (k, v))
    out_json["meta"]["controls"] = controls_meta

    # merge SPY forward labels with controls -> base market-day panel
    market_panel = spy_df.join(controls_df, on="date", how="inner")
    log("market_panel (SPY labels x controls, inner join): N=%d" % market_panel.height)
    out_lines.append("market_panel (SPY labels x controls) N=%d\n" % market_panel.height)
    out_json["meta"]["market_panel_n"] = market_panel.height

    # --- A2: cross-sectional aggregate ---
    log("\n=== A2: cross-sectional aggregate (mkt_gex_ratio, mkt_neg_share) ===")
    out_lines.append("\n=== A2: cross-sectional aggregate ===\n")
    a2_daily, a2_status = build_a2_daily_series()
    out_lines.append("A2 status: %s\n" % json.dumps(a2_status, default=str))
    out_json["meta"]["a2_status"] = a2_status

    a2_panel = pl.DataFrame()
    if a2_daily.height:
        a2_panel = a2_daily.join(market_panel, on="date", how="inner")
        net_path_daily, pnl15_daily = build_a2_cohort_labels()
        if net_path_daily.height:
            a2_panel = a2_panel.join(net_path_daily.select(["date", "cohort_net_path"]), on="date", how="left")
        else:
            a2_panel = a2_panel.with_columns(pl.lit(None).cast(pl.Float64).alias("cohort_net_path"))
        if pnl15_daily.height:
            a2_panel = a2_panel.join(pnl15_daily.select(["date", "cohort_pnl15"]), on="date", how="left")
        else:
            a2_panel = a2_panel.with_columns(pl.lit(None).cast(pl.Float64).alias("cohort_pnl15"))
        log("A2 panel (aggregate x controls x cohort labels): N=%d" % a2_panel.height)
        out_lines.append("A2 panel N=%d\n" % a2_panel.height)
        out_json["meta"]["a2_panel_n"] = a2_panel.height

        if a2_status["status"] == "real":
            analyze_variant("A2", a2_panel, A2_SERIES, out_lines, out_json)
            report_cutoff_subset("A2", a2_panel, out_lines, out_json)
        else:
            out_lines.append(
                "A2 MECHANICS-VERIFY ONLY (status=%s) -- running the aggregation/OLS pipeline against "
                "the smoke/partial file to confirm code correctness; NOT a real result, A2 remains PENDING.\n" %
                a2_status["status"]
            )
            analyze_variant("A2_mechanics_verify", a2_panel, A2_SERIES, out_lines, out_json)
    else:
        out_lines.append("A2: no per-name feature series available -- A2 PENDING, analysis SKIPPED\n")
        out_json["A2"] = {"skipped": True, "status": a2_status["status"]}

    # --- A1: SPY-chain daily features ---
    log("\n=== A1: SPY-chain daily features (chain-quality gated) ===")
    out_lines.append("\n=== A1: SPY-chain daily features ===\n")
    a1_daily, a1_status = build_a1_daily_series()
    out_lines.append("A1 status: %s\n" % json.dumps(a1_status, default=str))
    out_json["meta"]["a1_status"] = a1_status

    a1_panel = pl.DataFrame()
    if a1_daily.height:
        a1_panel = a1_daily.join(market_panel, on="date", how="inner")
        log("A1 panel (SPY chain series x labels x controls): N=%d" % a1_panel.height)
        out_lines.append("A1 panel N=%d\n" % a1_panel.height)
        out_json["meta"]["a1_panel_n"] = a1_panel.height

        if a1_status["status"] == "real":
            analyze_variant("A1", a1_panel, A1_SERIES, out_lines, out_json)
            report_cutoff_subset("A1", a1_panel, out_lines, out_json)
        else:
            out_lines.append(
                "A1 MECHANICS-VERIFY ONLY (status=%s) -- running the chain-quality-gate/OLS pipeline "
                "against the smoke file to confirm code correctness; NOT a real result, A1 remains PENDING "
                "(coverage-limited first read caveat also applies once real data lands, per AMENDMENT).\n" %
                a1_status["status"]
            )
            analyze_variant("A1_mechanics_verify", a1_panel, A1_SERIES, out_lines, out_json)
    else:
        out_lines.append("A1: no SPY-chain feature series available -- A1 PENDING, analysis SKIPPED\n")
        out_json["A1"] = {"skipped": True, "status": a1_status["status"]}

    # --- Verdict-rule note (per AMENDMENT: closes only if BOTH A1 and A2 fail) ---
    out_lines.append(
        "\nVerdict-rule reminder (AMENDMENT): market-level GEX lead closes only if BOTH A1 and A2 fail "
        "their bars; either passing advances the lead (with the other's read reported alongside). "
        "This run's A1 status=%s, A2 status=%s -- no verdict can be rendered until both are 'real'.\n" %
        (a1_status["status"], a2_status["status"])
    )

    elapsed = time.time() - t_start
    out_lines.append("\n=== DONE in %.1fs ===\n" % elapsed)
    out_json["meta"]["elapsed_s"] = round(elapsed, 2)

    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.writelines(out_lines)
    with open(OUT_JSON, "w", encoding="ascii") as f:
        json.dump(out_json, f, indent=2, default=str)

    log("\nwrote %s" % OUT_TXT)
    log("wrote %s" % OUT_JSON)
    log("=== DONE in %.1fs ===" % elapsed)
    sys.exit(0)


if __name__ == "__main__":
    main()
