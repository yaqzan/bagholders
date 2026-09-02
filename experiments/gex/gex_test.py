"""
gex_test.py -- Deliverable 3 of the GEX experiment: the full pre-registered
analysis (DESIGN.md D1/D4/D5/D6). Runs end-to-end against a features parquet
(dealer_gex.py output schema) and writes gex_results.txt + gex_results.json.

READ-ONLY offline experiment. New files only under experiments/gex/ and
.cache/experiment_data/. Never touches scoring.py/core.py/simulator.py/
api.py/strategy_config.py/dealer_gex.py/DESIGN.md/MATH_SPEC.md, and never
writes anywhere under experiments/iv_skew/.

Usage:
    python experiments/gex/gex_test.py [--features PATH]
    python experiments/gex/gex_test.py --selftest-guard

PYTHONUTF8=1 required (ASCII-only prints; no statsmodels -- numpy OLS only).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

import numpy as np
import polars as pl
from scipy.stats import spearmanr, pearsonr

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
DEFAULT_FEATURES = os.path.join(_ROOT, ".cache", "experiment_data", "gex_features.parquet")
RS_LEDGER = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
PROXY_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "proxy_ledger.parquet")
IV_LEDGER_EXT = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger_ext.parquet")
GEX_BUILD_REPORT = os.path.join(_ROOT, ".cache", "experiment_data", "gex_build_report.json")
PRICE_CACHE = os.path.join(_ROOT, ".cache", "experiment_data", "gex_test_prices.parquet")

OUT_TXT = os.path.join(_HERE, "gex_results.txt")
OUT_JSON = os.path.join(_HERE, "gex_results.json")

EXT_GAP_CUTOFF = "2026-06-15"          # D5: pnl15-resolved ext gap slice cutoff
PRICE_QUERY_START = "2026-01-02"       # bulk price cache window (D5/B instructions)
PRICE_QUERY_END = "2026-07-06"
SEMIVOL_WIN = 60                       # trailing trading-day window (build_proxy.py:24)
MIN_CELL_N = 30                        # small-N cells must be visibly skipped

PRIMARY_FEATURES = ["gex_ratio", "gex_regime", "flip_dist", "callwall_dist"]
CONTROL_COLS_ORTHO = ["opt_skew", "semivol_r", "overall", "stock_r20"]
CONTROL_COLS_MECH = ["overall", "stock_r20", "vol_pct"]
MECH_LABELS = ["net_path", "mfe15", "mae15"]

# Forward mechanism-gap ledger convention (component_reweight/build_ledger.py:90-162)
MAXW = 40          # forward window, calendar days
VOL_WIN = 60       # realized-vol lookback (trading bars)


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def n_ok(n, label=""):
    """Return True if n >= MIN_CELL_N; caller must print SKIPPED itself if False."""
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
    """5 quantile bin edges (0,20,40,60,80,100 pct). Returns None if degenerate."""
    qs = np.nanpercentile(x, [0, 20, 40, 60, 80, 100])
    if len(np.unique(qs)) < 3:
        return None
    return qs


def quintile_assign(x: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Assign each value to bin 1..5 using edges (inclusive of the right edge)."""
    bins = np.digitize(x, edges[1:-1], right=True) + 1
    bins = np.clip(bins, 1, 5)
    return bins


# ---------------------------------------------------------------------------
# OLS + clustered SE
# ---------------------------------------------------------------------------
def ols_with_clustered_se(y: np.ndarray, X: np.ndarray, groups: np.ndarray):
    """
    Plain-vanilla numpy OLS with homoskedastic SE AND date-clustered sandwich SE.
    X must already include an intercept column if desired by the caller (we add one).
    Returns dict: beta (excl. intercept), se_plain, t_plain, se_clust, t_clust, n, g (n groups).
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

    # --- date-clustered sandwich SE ---
    uniq_groups = np.unique(groups)
    G = uniq_groups.shape[0]
    meat = np.zeros((k, k))
    for g in uniq_groups:
        mask = groups == g
        Xg = Xc[mask]
        eg = resid[mask]
        score_g = Xg.T @ eg              # (k,)
        meat += np.outer(score_g, score_g)
    small_sample = (G / max(G - 1, 1)) if G > 1 else 1.0
    cov_clust = small_sample * (XtX_inv @ meat @ XtX_inv)
    se_clust = np.sqrt(np.diag(cov_clust))

    with np.errstate(divide='ignore', invalid='ignore'):
        t_plain = beta_full / se_plain
        t_clust = beta_full / se_clust

    return {
        "beta": beta_full,          # includes intercept at index 0
        "se_plain": se_plain,
        "t_plain": t_plain,
        "se_clust": se_clust,
        "t_clust": t_clust,
        "n": n,
        "g": int(G),
    }


def run_ols_one_feature(df: pl.DataFrame, label_col: str, feature_col: str, control_cols: list,
                         date_col: str = "date"):
    """
    Standardize label + feature + controls (z-score), drop null rows, run OLS
    with plain + date-clustered t. Returns a result dict or None if N < MIN_CELL_N.
    Column order in X: [feature, *controls] -- feature coefficient is index 1 of beta
    (index 0 = intercept).
    """
    needed = [label_col, feature_col] + control_cols
    sub = df.select([date_col] + needed).drop_nulls()
    n = sub.height
    if not n_ok(n):
        return {"n": n, "skipped": True}

    y = zscore(sub[label_col].to_numpy().astype(np.float64))
    Xcols = []
    for c in [feature_col] + control_cols:
        Xcols.append(zscore(sub[c].to_numpy().astype(np.float64)))
    X = np.column_stack(Xcols)

    # after z-scoring, any column with zero variance produces all-NaN -> drop those rows
    valid_mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    n_valid = int(valid_mask.sum())
    if not n_ok(n_valid):
        return {"n": n_valid, "skipped": True}
    y = y[valid_mask]
    X = X[valid_mask]
    dates_valid = sub[date_col].to_numpy()[valid_mask]

    res = ols_with_clustered_se(y, X, dates_valid)
    return {
        "n": n_valid,
        "skipped": False,
        "feature": feature_col,
        "label": label_col,
        "beta_feature": float(res["beta"][1]),
        "t_plain_feature": float(res["t_plain"][1]),
        "t_clust_feature": float(res["t_clust"][1]),
        "se_plain_feature": float(res["se_plain"][1]),
        "se_clust_feature": float(res["se_clust"][1]),
        "n_clusters": res["g"],
        "control_betas": {c: float(res["beta"][2 + i]) for i, c in enumerate(control_cols)},
        "control_t_plain": {c: float(res["t_plain"][2 + i]) for i, c in enumerate(control_cols)},
    }


# ---------------------------------------------------------------------------
# Panel A: MECHANISM (features INNER JOIN rs_ledger)
# ---------------------------------------------------------------------------
def build_panel_a(features: pl.DataFrame) -> pl.DataFrame:
    log("\n--- Building Panel A (MECHANISM: features x rs_ledger) ---")
    if not os.path.exists(RS_LEDGER):
        log("rs_ledger.parquet not found at %s -- Panel A SKIPPED" % RS_LEDGER)
        return pl.DataFrame()

    rs = pl.read_parquet(RS_LEDGER)
    rs = rs.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    feats = features.clone()
    if feats.height and feats["date"].dtype != pl.Date:
        feats = feats.with_columns(pl.col("date").cast(pl.Date))

    keep_rs = ["symbol", "date", "overall", "vol_pct", "mfe15", "mae15", "t_up", "stock_r20"]
    keep_rs = [c for c in keep_rs if c in rs.columns]
    rs_slim = rs.select(keep_rs)

    panel = feats.join(rs_slim, on=["symbol", "date"], how="inner")
    if panel.height:
        panel = panel.with_columns(
            (pl.col("mfe15") - pl.col("mae15")).alias("net_path")
        )
    log("Panel A: features N=%d, rs_ledger N=%d, inner-join N=%d" % (feats.height, rs.height, panel.height))

    # All dates must be <= 2026-05-15 per D5 (rs_ledger ends there); enforce holdout guard.
    if panel.height:
        assert_no_holdout_leak(panel, "gex_mechanism")
        log("Panel A: assert_no_holdout_leak PASSED (max date <= cutoff %s)" % HOLDOUT_CUTOFF.isoformat())
    return panel


# ---------------------------------------------------------------------------
# Panel B: ORTHO (proxy + ext, pooled)
# ---------------------------------------------------------------------------
def _semivol_r_and_stock_r20_from_prices(symbols_dates: list, price_df: pl.DataFrame):
    """
    Recompute semivol_r and stock_r20 for a list of (symbol, date) pairs using
    the EXACT D5-quoted formulas:
      semivol_r = std(dn)/std(up) over trailing 60 trading-day returns ending at
                  signal date, needs >=3 up AND >=3 dn else None (build_proxy.py:41-59)
      stock_r20 = close[d]/close[d-20 bars] - 1 (build_rs.py:47-54,71)
    price_df: columns symbol, date (Date), close (Float64), one row per trading day.
    Returns dict {(symbol, date_str): (semivol_r, stock_r20)}.
    """
    out = {}
    by_sym = {}
    for sym in price_df["symbol"].unique().to_list():
        sub = price_df.filter(pl.col("symbol") == sym).sort("date")
        dts = sub["date"].to_list()
        cl = sub["close"].to_numpy().astype(np.float64)
        idx = {d.isoformat(): i for i, d in enumerate(dts)}
        by_sym[sym] = (cl, idx)

    ret_cache = {}
    for sym, (cl, idx) in by_sym.items():
        ret = np.full(len(cl), np.nan)
        if len(cl) > 1:
            ret[1:] = cl[1:] / cl[:-1] - 1.0
        ret_cache[sym] = ret

    for sym, d in symbols_dates:
        if sym not in by_sym:
            out[(sym, d)] = (None, None)
            continue
        cl, idx = by_sym[sym]
        ret = ret_cache[sym]
        i = idx.get(d)
        if i is None:
            out[(sym, d)] = (None, None)
            continue

        # stock_r20 = close[i] / close[i-20] - 1
        r20 = None
        if i - 20 >= 0:
            a, b = cl[i - 20], cl[i]
            if a and b and a > 0:
                r20 = float(b / a - 1.0)

        # semivol_r = std(dn)/std(up) over trailing 60 returns ending at i (inclusive)
        svr = None
        if i - SEMIVOL_WIN + 1 >= 0:
            w = ret[i - SEMIVOL_WIN + 1:i + 1]
            w = w[~np.isnan(w)]
            if len(w) >= 30:
                up = w[w > 0]
                dn = w[w < 0]
                if len(up) >= 3 and len(dn) >= 3:
                    sd_up = np.std(up, ddof=0)
                    sd_dn = np.std(dn, ddof=0)
                    if sd_up > 1e-9:
                        svr = float(sd_dn / sd_up)
        out[(sym, d)] = (svr, r20)
    return out


def _query_prices(symbols: list):
    """The ONE bulk MySQL query shape: date,close,high,low for `symbols` over
    PRICE_QUERY_START..PRICE_QUERY_END. Returns (df, elapsed_seconds)."""
    from database.trader_database import DB  # local import: MySQL only touched here

    t0 = time.time()
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
    placeholders = ",".join(["%s"] * len(symbols))
    sql = (
        "SELECT symbol, date, close, high, low FROM price_history "
        "WHERE symbol IN (%s) AND date BETWEEN %%s AND %%s ORDER BY symbol, date" % placeholders
    )
    params = tuple(symbols) + (PRICE_QUERY_START, PRICE_QUERY_END)
    cur = DB.execute_sql(sql, params)
    rows = cur.fetchall()
    elapsed = time.time() - t0
    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "close": [float(r[2]) if r[2] is not None else None for r in rows],
            "high": [float(r[3]) if r[3] is not None else None for r in rows],
            "low": [float(r[4]) if r[4] is not None else None for r in rows],
        },
        schema={"symbol": pl.Utf8, "date": pl.Date, "close": pl.Float64,
                "high": pl.Float64, "low": pl.Float64},
    )
    return df, elapsed


def _load_or_build_price_cache(symbols: list):
    """
    ONE bulk MySQL query for date,close,high,low over PRICE_QUERY_START..
    PRICE_QUERY_END for the given symbols, cached to PRICE_CACHE. Skips the
    query entirely if the cache already exists AND already covers every
    requested symbol; if the on-disk cache is missing some requested symbols
    (e.g. a later caller within the same run needs a larger symbol set than
    the first caller warmed it with), tops it up with exactly one more bulk
    query for the DELTA symbols only and merges -- so within a single process
    run every caller ends up served from one cumulative on-disk cache rather
    than silently missing symbols. Skips the query entirely if symbols is empty.
    """
    if not symbols:
        if os.path.exists(PRICE_CACHE):
            df = pl.read_parquet(PRICE_CACHE)
            return df, {"rows": df.height, "elapsed_s": 0.0, "source": "cache_hit_empty_request"}
        log("price cache: 0 symbols requested -- SKIPPED (no query executed)")
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "close": pl.Float64,
                                     "high": pl.Float64, "low": pl.Float64}), \
            {"rows": 0, "elapsed_s": 0.0, "source": "skipped_no_symbols"}

    requested = set(symbols)

    if os.path.exists(PRICE_CACHE):
        t0 = time.time()
        df = pl.read_parquet(PRICE_CACHE)
        cached_syms = set(df["symbol"].unique().to_list())
        missing = sorted(requested - cached_syms)
        if not missing:
            log("price cache: hit at %s (N=%d rows) in %.2fs -- covers all %d requested symbols" %
                (PRICE_CACHE, df.height, time.time() - t0, len(requested)))
            return df, {"rows": df.height, "elapsed_s": round(time.time() - t0, 2), "source": "cache_hit"}
        log("price cache: partial hit (%d/%d symbols cached) -- topping up %d missing symbols with one more bulk query" %
            (len(cached_syms & requested), len(requested), len(missing)))
        top_up_df, elapsed = _query_prices(missing)
        log("price cache: top-up query returned %d rows in %.1fs" % (top_up_df.height, elapsed))
        df = pl.concat([df, top_up_df], how="vertical_relaxed").unique(subset=["symbol", "date"])
        df.write_parquet(PRICE_CACHE)
        log("price cache: merged + wrote %s (N=%d rows total)" % (PRICE_CACHE, df.height))
        return df, {"rows": df.height, "elapsed_s": round(elapsed, 2), "source": "topped_up"}

    log("price cache: MISS -- running ONE bulk query for %d symbols, %s..%s" %
        (len(symbols), PRICE_QUERY_START, PRICE_QUERY_END))
    df, elapsed = _query_prices(sorted(requested))
    log("price cache: query returned %d rows in %.1fs" % (df.height, elapsed))
    os.makedirs(os.path.dirname(PRICE_CACHE), exist_ok=True)
    df.write_parquet(PRICE_CACHE)
    log("price cache: wrote %s (N=%d rows)" % (PRICE_CACHE, df.height))
    return df, {"rows": df.height, "elapsed_s": round(elapsed, 2), "source": "fresh_query"}


def _collect_price_symbol_union():
    """
    Pre-scan the (already on-disk, no MySQL touched) iv_ledger_ext.parquet for
    every symbol that EITHER consumer of the price cache will need this run:
      - Panel B ext-arm: pnl15_resolved==True AND date<=EXT_GAP_CUTOFF
      - Phase C.ii mechanism-gap: overall>=70 AND date in [2026-05-16, 2026-06-21]
    so main() can warm the price cache with the FULL union in one query, and
    both build_panel_b/build_panel_c's own _load_or_build_price_cache calls
    become guaranteed cache hits (the top-up path in _load_or_build_price_cache
    is a defensive fallback only, not the intended path).
    """
    if not os.path.exists(IV_LEDGER_EXT):
        return []
    ext_raw = pl.read_parquet(IV_LEDGER_EXT)
    panelb_syms = set(
        ext_raw.filter(
            (pl.col("pnl15_resolved") == True) & (pl.col("date") <= EXT_GAP_CUTOFF)  # noqa: E712
        )["symbol"].unique().to_list()
    )
    gap_syms = set(
        ext_raw.filter(
            (pl.col("overall") >= 70) & (pl.col("date") >= "2026-05-16") & (pl.col("date") <= "2026-06-21")
        )["symbol"].unique().to_list()
    )
    return sorted(panelb_syms | gap_syms)


def build_panel_b(features: pl.DataFrame):
    """Returns (pooled_panel, proxy_only_panel, meta_dict)."""
    log("\n--- Building Panel B (ORTHO: proxy + ext, pooled) ---")
    meta = {"n_proxy_only": 0, "n_ext_added": 0, "n_pooled": 0, "price_query": None}

    feats = features.clone()
    if feats.height and feats["date"].dtype != pl.Date:
        feats = feats.with_columns(pl.col("date").cast(pl.Date))

    # --- proxy arm ---
    if not os.path.exists(PROXY_LEDGER):
        log("proxy_ledger.parquet not found -- Panel B proxy arm SKIPPED")
        proxy = pl.DataFrame()
    else:
        proxy_raw = pl.read_parquet(PROXY_LEDGER)
        proxy_raw = proxy_raw.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
        proxy = proxy_raw.filter(pl.col("opt_skew").is_not_null())
        proxy = proxy.select(["symbol", "date", "pnl15", "opt_skew", "semivol_r", "overall", "stock_r20"])
        log("proxy arm: opt_skew NOT NULL -> N=%d (%s..%s)" %
            (proxy.height, proxy["date"].min(), proxy["date"].max()) if proxy.height else "proxy arm: N=0")

    # --- ext arm ---
    if not os.path.exists(IV_LEDGER_EXT):
        log("iv_ledger_ext.parquet not found -- Panel B ext arm SKIPPED")
        ext_final = pl.DataFrame()
        ext_symbols_dates = []
    else:
        ext_raw = pl.read_parquet(IV_LEDGER_EXT)
        ext_raw = ext_raw.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
        ext = ext_raw.filter(
            (pl.col("pnl15_resolved") == True) & (pl.col("date") <= pl.lit(EXT_GAP_CUTOFF).str.strptime(pl.Date, "%Y-%m-%d"))  # noqa: E712
        )
        ext = ext.rename({"skew": "opt_skew"})
        ext = ext.select(["symbol", "date", "pnl15", "opt_skew", "overall"])
        log("ext arm: pnl15_resolved==True AND date<=%s -> N=%d" % (EXT_GAP_CUTOFF, ext.height))

        if ext.height == 0:
            ext_final = pl.DataFrame()
            ext_symbols_dates = []
        else:
            ext_symbols_dates = [(s, d.isoformat()) for s, d in zip(ext["symbol"].to_list(), ext["date"].to_list())]
            ext_symbols = sorted(set(s for s, _ in ext_symbols_dates))

            price_df, price_meta = _load_or_build_price_cache(ext_symbols)
            meta["price_query"] = price_meta

            recompute = _semivol_r_and_stock_r20_from_prices(ext_symbols_dates, price_df)
            svr_list = []
            r20_list = []
            for s, d in ext_symbols_dates:
                svr, r20 = recompute.get((s, d), (None, None))
                svr_list.append(svr)
                r20_list.append(r20)
            ext_final = ext.with_columns([
                pl.Series("semivol_r", svr_list, dtype=pl.Float64),
                pl.Series("stock_r20", r20_list, dtype=pl.Float64),
            ])
            ext_final = ext_final.select(["symbol", "date", "pnl15", "opt_skew", "semivol_r", "overall", "stock_r20"])

    if meta["price_query"] is None:
        # no ext symbols to price -- report a clean skip, per VERIFY item 3.
        _, price_meta = _load_or_build_price_cache([])
        meta["price_query"] = price_meta

    meta["n_proxy_only"] = proxy.height
    meta["n_ext_added"] = ext_final.height

    # --- concat proxy + ext ---
    frames = [f for f in [proxy, ext_final] if f.height]
    if not frames:
        log("Panel B: both arms empty -- pooled panel EMPTY")
        pooled_raw = pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "pnl15": pl.Float64,
                                           "opt_skew": pl.Float64, "semivol_r": pl.Float64,
                                           "overall": pl.Int64, "stock_r20": pl.Float64})
    else:
        pooled_raw = pl.concat(frames, how="vertical_relaxed")

    log("Panel B pre-join pooled (proxy+ext) N=%d" % pooled_raw.height)

    # --- inner join with features ---
    if pooled_raw.height and feats.height:
        pooled = pooled_raw.join(feats, on=["symbol", "date"], how="inner")
    else:
        pooled = pl.DataFrame()
    meta["n_pooled"] = pooled.height
    log("Panel B pooled INNER JOIN features -> N=%d" % pooled.height)

    if pooled.height:
        assert_no_holdout_leak(pooled, "gex_ortho")
        log("Panel B: assert_no_holdout_leak PASSED (max date <= cutoff %s)" % HOLDOUT_CUTOFF.isoformat())

    # proxy-only panel (secondary OLS), also joined to features
    if proxy.height and feats.height:
        proxy_only = proxy.join(feats, on=["symbol", "date"], how="inner")
        if proxy_only.height:
            assert_no_holdout_leak(proxy_only, "gex_ortho_proxy_only")
    else:
        proxy_only = pl.DataFrame()

    log("Panel B report: proxy-only N=%d, ext-added N=%d, pooled N=%d" %
        (meta["n_proxy_only"], meta["n_ext_added"], meta["n_pooled"]))

    return pooled, proxy_only, meta


# ---------------------------------------------------------------------------
# Panel C: FORWARD / OOS (never leak-guarded, never used for selection)
# ---------------------------------------------------------------------------
def build_panel_c(features: pl.DataFrame):
    """Returns dict with 'oos_ext' info and optionally 'mech_gap'/'true_holdout' frames."""
    log("\n--- Building Panel C (FORWARD/OOS -- never leak-guarded) ---")
    result = {"oos_resolved_n": 0, "mech_gap_status": "not_attempted"}

    # (i) ext rows is_oos==True with features -- count resolved pnl15
    if os.path.exists(IV_LEDGER_EXT):
        ext_raw = pl.read_parquet(IV_LEDGER_EXT)
        ext_raw = ext_raw.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
        oos = ext_raw.filter(pl.col("is_oos") == True)  # noqa: E712
        feats = features.clone()
        if feats.height and feats["date"].dtype != pl.Date:
            feats = feats.with_columns(pl.col("date").cast(pl.Date))
        oos_joined = oos.join(feats, on=["symbol", "date"], how="inner") if (oos.height and feats.height) else pl.DataFrame()
        n_oos_total = oos_joined.height
        n_resolved = int(oos_joined["pnl15_resolved"].sum()) if oos_joined.height and "pnl15_resolved" in oos_joined.columns else 0
        result["oos_total_n"] = n_oos_total
        result["oos_resolved_n"] = n_resolved
        if n_resolved == 0:
            log("OOS money read: 0 resolved, scheduled ~2026-07-15+ (is_oos rows with features N=%d)" % n_oos_total)
        else:
            log("OOS money read: %d resolved out of %d is_oos rows with features" % (n_resolved, n_oos_total))
    else:
        log("iv_ledger_ext.parquet not found -- OOS-money read SKIPPED")

    # (ii) OPTIONAL mechanism-gap slice: forward signals 2026-05-16..2026-06-15,
    # recompute mfe15/mae15 from price_history high/low/close (build_ledger.py convention).
    # Wrapped so failure does NOT block phases 1-3.
    try:
        mech_gap_df, true_holdout_df = _build_mechanism_gap_frame(features)
        result["mech_gap_df"] = mech_gap_df
        result["true_holdout_df"] = true_holdout_df
        result["mech_gap_status"] = "built" if (mech_gap_df is not None and mech_gap_df.height) else "empty"
        n_gap = mech_gap_df.height if mech_gap_df is not None else 0
        n_th = true_holdout_df.height if true_holdout_df is not None else 0
        log("Mechanism-gap slice (2026-05-16..2026-06-15): N=%d; true-holdout ripe sliver (2026-06-16..2026-06-21): N=%d" %
            (n_gap, n_th))
    except Exception as e:
        result["mech_gap_status"] = "SKIPPED: %s" % repr(e)[:200]
        result["mech_gap_df"] = None
        result["true_holdout_df"] = None
        log("Mechanism-gap slice: SKIPPED (%s)" % repr(e)[:200])

    return result


def _build_mechanism_gap_frame(features: pl.DataFrame):
    """
    OPTIONAL/lower-priority: forward signals 2026-05-16..2026-06-15 (mechanism-gap)
    and 2026-06-16..2026-06-21 (true-holdout ripe sliver), with mfe15/mae15
    recomputed from price_history high/low/close using the EXACT build_ledger.py
    convention (60d realized vol sigma, cumulative max excursions, 15 CALENDAR
    days, entry = signal close).

    Signal list (symbol, date, overall>=70) is sourced from the ALREADY-CACHED
    .cache/iv_skew/iv_ledger_ext.parquet (built by build_iv_ext.py off the ACTIVE
    scores version, overall>=70, D5) -- NOT a fresh live `scores` query, per the
    HARD STOP that MySQL in this script is the ONE bulk price query only. The
    only MySQL touch this function triggers is the shared price cache (high/low
    added to the same bulk query as Panel B's ext-arm pricing).
    """
    GAP_START, GAP_END = "2026-05-16", "2026-06-15"
    TH_START, TH_END = "2026-06-16", "2026-06-21"

    if not os.path.exists(IV_LEDGER_EXT):
        log("mechanism-gap: iv_ledger_ext.parquet not found -- SKIPPED (no signal source)")
        return pl.DataFrame(), pl.DataFrame()

    ext_raw = pl.read_parquet(IV_LEDGER_EXT)
    ext_sigs = ext_raw.filter(
        (pl.col("overall") >= 70) & (pl.col("date") >= GAP_START) & (pl.col("date") <= TH_END)
    ).select(["symbol", "date", "overall"]).unique()
    sigs = [(s, d, int(o)) for s, d, o in zip(
        ext_sigs["symbol"].to_list(), ext_sigs["date"].to_list(), ext_sigs["overall"].to_list())]
    log("mechanism-gap: %d candidate 70+ signals in %s..%s (source=iv_ledger_ext.parquet, no live query)" %
        (len(sigs), GAP_START, TH_END))
    if not sigs:
        return pl.DataFrame(), pl.DataFrame()

    symbols = sorted(set(s for s, _, _ in sigs))
    price_df, _ = _load_or_build_price_cache_high_low(symbols)

    by_sym = {}
    for sym in symbols:
        sub = price_df.filter(pl.col("symbol") == sym).sort("date")
        if sub.height == 0:
            continue
        dates = sub["date"].to_list()
        close = sub["close"].to_numpy().astype(np.float64)
        high = sub["high"].to_numpy().astype(np.float64)
        low = sub["low"].to_numpy().astype(np.float64)
        ret = np.full(len(close), np.nan)
        if len(close) > 1:
            ret[1:] = close[1:] / close[:-1] - 1.0
        sig = np.full(len(close), np.nan)
        for i in range(len(close)):
            lo = max(0, i - VOL_WIN + 1)
            if i - lo >= 20:
                w = ret[lo:i + 1]
                w = w[~np.isnan(w)]
                if len(w) >= 2:
                    sig[i] = np.std(w, ddof=1)
        idx = {d.isoformat(): i for i, d in enumerate(dates)}
        by_sym[sym] = (dates, close, high, low, sig, idx)

    recs = []
    for sym, d, ov in sigs:
        if sym not in by_sym:
            continue
        dates, close, high, low, sig, idx = by_sym[sym]
        i0 = idx.get(d)
        if i0 is None:
            continue
        s = sig[i0]
        entry = close[i0]
        if not (s and s > 1e-6 and entry > 0):
            continue
        j = i0 + 1
        caldays, up_ex, dn_ex = [], [], []
        d_date = date.fromisoformat(d)
        while j < len(dates):
            cd = (dates[j] - d_date).days
            if cd > MAXW:
                break
            if cd >= 1:
                caldays.append(cd)
                up_ex.append((high[j] - entry) / entry / s)
                dn_ex.append((entry - low[j]) / entry / s)
            j += 1
        if not caldays:
            continue
        caldays = np.array(caldays)
        cum_up = np.maximum.accumulate(np.array(up_ex))
        cum_dn = np.maximum.accumulate(np.array(dn_ex))

        def mfe_at(W):
            m = caldays <= W
            return float(cum_up[m].max()) if m.any() else None

        def mae_at(W):
            m = caldays <= W
            return float(cum_dn[m].max()) if m.any() else None

        mfe15 = mfe_at(15)
        mae15 = mae_at(15)
        if mfe15 is None or mae15 is None:
            continue
        recs.append({"symbol": sym, "date": d, "overall": ov, "vol_pct": round(float(s) * 100, 4),
                     "mfe15": mfe15, "mae15": mae15, "net_path": mfe15 - mae15})

    if not recs:
        return pl.DataFrame(), pl.DataFrame()

    all_df = pl.DataFrame(recs).with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
    feats = features.clone()
    if feats.height and feats["date"].dtype != pl.Date:
        feats = feats.with_columns(pl.col("date").cast(pl.Date))
    all_joined = all_df.join(feats, on=["symbol", "date"], how="inner") if feats.height else pl.DataFrame()

    if all_joined.height == 0:
        return pl.DataFrame(), pl.DataFrame()

    gap_df = all_joined.filter(
        (pl.col("date") >= pl.lit(GAP_START).str.strptime(pl.Date, "%Y-%m-%d")) &
        (pl.col("date") <= pl.lit(GAP_END).str.strptime(pl.Date, "%Y-%m-%d"))
    )
    th_df = all_joined.filter(
        (pl.col("date") >= pl.lit(TH_START).str.strptime(pl.Date, "%Y-%m-%d")) &
        (pl.col("date") <= pl.lit(TH_END).str.strptime(pl.Date, "%Y-%m-%d"))
    )
    return gap_df, th_df


def _load_or_build_price_cache_high_low(symbols: list):
    """
    Reuse the same PRICE_CACHE if it already has these symbols+high/low; otherwise
    this piggybacks on _load_or_build_price_cache which already selects high/low.
    Kept separate only to make the optional-path failure isolated from panel B.
    """
    return _load_or_build_price_cache(symbols)


# ---------------------------------------------------------------------------
# Phase 1: UNIVARIATE
# ---------------------------------------------------------------------------
def phase1_univariate(panel_a: pl.DataFrame, panel_b_pooled: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== PHASE 1: UNIVARIATE ===")
    out_lines.append("\n=== PHASE 1: UNIVARIATE ===\n")
    out_json["phase1"] = {}

    def do_one(df: pl.DataFrame, feature: str, label: str, subset_name: str, panel_name: str):
        key = "%s|%s|%s|%s" % (panel_name, feature, label, subset_name)
        needed = [c for c in [feature, label] if c in df.columns]
        if len(needed) < 2 or df.height == 0:
            out_lines.append("  [%s] %s x %s (%s): %s (missing columns/empty panel)\n" %
                              (panel_name, feature, label, subset_name, skip_str(0)))
            out_json["phase1"][key] = {"n": 0, "skipped": True}
            return
        sub = df.select([feature, label])
        sub = sub.with_columns([pl.col(c).fill_nan(None) for c in sub.columns
                                if sub[c].dtype in (pl.Float64, pl.Float32)]).drop_nulls()
        n = sub.height
        if not n_ok(n):
            out_lines.append("  [%s] %s x %s (%s): %s\n" % (panel_name, feature, label, subset_name, skip_str(n)))
            out_json["phase1"][key] = {"n": n, "skipped": True}
            return

        x = sub[feature].to_numpy().astype(np.float64)
        y = sub[label].to_numpy().astype(np.float64)

        if feature == "gex_regime":
            # group means by regime value instead of quintiles
            regimes = sorted(np.unique(x).tolist())
            groups = {}
            for r in regimes:
                m = x == r
                nr = int(m.sum())
                groups[str(int(r))] = {"n": nr, "mean_label": float(np.mean(y[m])) if nr >= MIN_CELL_N else None,
                                        "skipped": nr < MIN_CELL_N}
            rho, pval = spearmanr(x, y)
            out_lines.append("  [%s] %s x %s (%s) N=%d spearman rho=%.4f p=%.4g\n" %
                              (panel_name, feature, label, subset_name, n, rho, pval))
            for rk, rv in groups.items():
                if rv["skipped"]:
                    out_lines.append("      regime=%s: %s\n" % (rk, skip_str(rv["n"])))
                else:
                    out_lines.append("      regime=%s: N=%d mean(%s)=%.4f\n" % (rk, rv["n"], label, rv["mean_label"]))
            out_json["phase1"][key] = {"n": n, "skipped": False, "spearman_rho": float(rho), "spearman_p": float(pval),
                                        "regime_groups": groups}
            return

        rho, pval = spearmanr(x, y)
        edges = quintile_edges(x)
        out_lines.append("  [%s] %s x %s (%s) N=%d spearman rho=%.4f p=%.4g\n" %
                          (panel_name, feature, label, subset_name, n, rho, pval))
        q_info = {}
        if edges is None:
            out_lines.append("      quintiles: SKIPPED (degenerate distribution)\n")
            q_info = {"skipped": True}
        else:
            qbins = quintile_assign(x, edges)
            q_rows = []
            for qi in range(1, 6):
                m = qbins == qi
                nq = int(m.sum())
                row = {"q": qi, "n": nq}
                if nq < MIN_CELL_N:
                    row["skipped"] = True
                    out_lines.append("      Q%d: %s\n" % (qi, skip_str(nq)))
                else:
                    row["skipped"] = False
                    row["mean_label"] = float(np.mean(y[m]))
                    line = "      Q%d: N=%d mean(%s)=%.4f" % (qi, nq, label, row["mean_label"])
                    if label == "pnl15":
                        wr = float(np.mean(y[m] > 0))
                        row["wr"] = wr
                        line += " WR=%.1f%%" % (wr * 100)
                    out_lines.append(line + "\n")
                q_rows.append(row)
            q_info = {"skipped": False, "quintiles": q_rows}
        out_json["phase1"][key] = {"n": n, "skipped": False, "spearman_rho": float(rho), "spearman_p": float(pval),
                                    "quintiles": q_info}

    # --- panel A: mechanism labels, PRIMARY features, FULL + 75+ subset ---
    if panel_a.height:
        for feature in PRIMARY_FEATURES:
            for label in MECH_LABELS:
                do_one(panel_a, feature, label, "FULL", "A")
                if "overall" in panel_a.columns:
                    sub75 = panel_a.filter(pl.col("overall") >= 75)
                    do_one(sub75, feature, label, "overall>=75", "A")
    else:
        out_lines.append("  Panel A empty -- Phase 1 mechanism-track SKIPPED entirely\n")

    # --- panel B: pnl15, PRIMARY features, FULL + 75+ subset ---
    if panel_b_pooled.height:
        for feature in PRIMARY_FEATURES:
            do_one(panel_b_pooled, feature, "pnl15", "FULL", "B")
            if "overall" in panel_b_pooled.columns:
                sub75 = panel_b_pooled.filter(pl.col("overall") >= 75)
                do_one(sub75, feature, "pnl15", "overall>=75", "B")
    else:
        out_lines.append("  Panel B empty -- Phase 1 pnl15-track SKIPPED entirely\n")

    # --- D1 mechanism-specific reads ---
    out_lines.append("\n  Mechanism-specific reads (D1 predictions):\n")
    if panel_a.height and all(c in panel_a.columns for c in ["gex_regime", "mfe15", "mae15"]):
        neg1 = panel_a.filter(pl.col("gex_regime") == -1).select(["mfe15", "mae15"]).drop_nulls()
        pos1 = panel_a.filter(pl.col("gex_regime") == 1).select(["mfe15", "mae15"]).drop_nulls()
        if n_ok(neg1.height) and n_ok(pos1.height):
            m_neg_mfe, m_neg_mae = float(neg1["mfe15"].mean()), float(neg1["mae15"].mean())
            m_pos_mfe, m_pos_mae = float(pos1["mfe15"].mean()), float(pos1["mae15"].mean())
            out_lines.append("    regime=-1 (N=%d): mean mfe15=%.4f mean mae15=%.4f\n" % (neg1.height, m_neg_mfe, m_neg_mae))
            out_lines.append("    regime=+1 (N=%d): mean mfe15=%.4f mean mae15=%.4f\n" % (pos1.height, m_pos_mfe, m_pos_mae))
            widened = (m_neg_mfe > m_pos_mfe) and (m_neg_mae > m_pos_mae)
            out_lines.append("    D1 prediction (regime=-1 widens BOTH mfe15 and mae15): %s\n" %
                              ("CONFIRMED" if widened else "NOT confirmed"))
            out_json["phase1"]["regime_widen_check"] = {
                "n_neg1": neg1.height, "n_pos1": pos1.height,
                "mean_mfe15_neg1": m_neg_mfe, "mean_mae15_neg1": m_neg_mae,
                "mean_mfe15_pos1": m_pos_mfe, "mean_mae15_pos1": m_pos_mae,
                "confirmed": widened,
            }
        else:
            out_lines.append("    regime widen check: %s\n" % skip_str(min(neg1.height, pos1.height)))
            out_json["phase1"]["regime_widen_check"] = {"skipped": True}
    else:
        out_lines.append("    regime widen check: SKIPPED (panel A empty or missing columns)\n")
        out_json["phase1"]["regime_widen_check"] = {"skipped": True}

    if panel_a.height and all(c in panel_a.columns for c in ["callwall_dist", "mfe15"]):
        sub = panel_a.select(["callwall_dist", "mfe15"]).drop_nulls()
        if n_ok(sub.height):
            rho, pval = spearmanr(sub["callwall_dist"].to_numpy(), sub["mfe15"].to_numpy())
            out_lines.append("    callwall_dist x mfe15 spearman (N=%d): rho=%.4f p=%.4g (D1: expect correlation)\n" %
                              (sub.height, rho, pval))
            out_json["phase1"]["callwall_mfe15_check"] = {"n": sub.height, "spearman_rho": float(rho), "spearman_p": float(pval)}
        else:
            out_lines.append("    callwall_dist x mfe15 spearman: %s\n" % skip_str(sub.height))
            out_json["phase1"]["callwall_mfe15_check"] = {"skipped": True}
    else:
        out_lines.append("    callwall_dist x mfe15 spearman: SKIPPED (panel A empty or missing columns)\n")
        out_json["phase1"]["callwall_mfe15_check"] = {"skipped": True}


# ---------------------------------------------------------------------------
# Phase 2: ORTHOGONALIZED OLS
# ---------------------------------------------------------------------------
def phase2_ols(panel_a: pl.DataFrame, panel_b_pooled: pl.DataFrame, panel_b_proxy_only: pl.DataFrame,
               out_lines: list, out_json: dict):
    log("\n=== PHASE 2: ORTHOGONALIZED OLS ===")
    out_lines.append("\n=== PHASE 2: ORTHOGONALIZED OLS ===\n")
    out_json["phase2"] = {"pooled": {}, "proxy_only": {}, "mechanism": {}}

    def report(res, feature, tag, container):
        if res is None or res.get("skipped"):
            n = res.get("n", 0) if res else 0
            out_lines.append("  [%s] %s: %s\n" % (tag, feature, skip_str(n)))
            container[feature] = {"n": n, "skipped": True}
            return
        out_lines.append(
            "  [%s] %s: N=%d (G=%d dates) beta=%.4f t_plain=%.3f t_clust=%.3f\n" %
            (tag, feature, res["n"], res["n_clusters"], res["beta_feature"], res["t_plain_feature"], res["t_clust_feature"])
        )
        container[feature] = res

    if panel_b_pooled.height:
        out_lines.append("\n  --- Panel B POOLED (decisive): pnl15 ~ gex_feature + opt_skew + semivol_r + overall + stock_r20 ---\n")
        for feature in PRIMARY_FEATURES:
            res = run_ols_one_feature(panel_b_pooled, "pnl15", feature, CONTROL_COLS_ORTHO)
            report(res, feature, "B-pooled", out_json["phase2"]["pooled"])
    else:
        out_lines.append("  Panel B pooled empty -- decisive OLS SKIPPED entirely\n")

    if panel_b_proxy_only.height:
        out_lines.append("\n  --- Panel B PROXY-ONLY (secondary, 1,998-row comparability): pnl15 ~ gex_feature + opt_skew + semivol_r + overall + stock_r20 ---\n")
        for feature in PRIMARY_FEATURES:
            res = run_ols_one_feature(panel_b_proxy_only, "pnl15", feature, CONTROL_COLS_ORTHO)
            report(res, feature, "B-proxy-only", out_json["phase2"]["proxy_only"])
    else:
        out_lines.append("  Panel B proxy-only empty -- secondary OLS SKIPPED entirely\n")

    if panel_a.height:
        out_lines.append("\n  --- Panel A MECHANISM-track: net_path ~ feature + overall + stock_r20 + vol_pct ---\n")
        for feature in PRIMARY_FEATURES:
            res = run_ols_one_feature(panel_a, "net_path", feature, CONTROL_COLS_MECH)
            report(res, feature, "A-mechanism", out_json["phase2"]["mechanism"])
    else:
        out_lines.append("  Panel A empty -- mechanism-track OLS SKIPPED entirely\n")


# ---------------------------------------------------------------------------
# Phase 3: SUB-PERIOD STABILITY
# ---------------------------------------------------------------------------
def _load_coverage_map():
    """
    Per D5/Phase-3 instructions: read the constant-coverage symbol set from
    .cache/experiment_data/gex_build_report.json's coverage map. If that file
    doesn't exist yet or lacks the map, return None (caller prints SKIPPED).
    Constant-coverage = min_date <= 2025-03-01.
    """
    if not os.path.exists(GEX_BUILD_REPORT):
        return None, "gex_build_report.json does not exist yet"
    try:
        with open(GEX_BUILD_REPORT, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception as e:
        return None, "could not parse gex_build_report.json: %s" % repr(e)[:120]

    cov_map = report.get("coverage_map")
    if not cov_map:
        return None, "gex_build_report.json has no 'coverage_map' key"

    constant_symbols = set()
    for row in cov_map:
        min_d = row.get("min_date")
        sym = row.get("symbol")
        if sym is None or min_d is None:
            continue
        try:
            if date.fromisoformat(min_d[:10]) <= date(2025, 3, 1):
                constant_symbols.add(sym)
        except Exception:
            continue
    return constant_symbols, None


def _thirds_split(df: pl.DataFrame, date_col: str = "date"):
    """Split df into 3 equal-N thirds by date order (not calendar-equal)."""
    if df.height == 0:
        return []
    ordered = df.sort(date_col)
    n = ordered.height
    edges = [0, n // 3, 2 * n // 3, n]
    return [ordered.slice(edges[i], edges[i + 1] - edges[i]) for i in range(3)]


def _stability_one(df: pl.DataFrame, label_col: str, control_cols: list, subset_label: str, out_lines: list, out_json_container: dict):
    if df.height == 0:
        out_lines.append("    [%s] empty panel -- SKIPPED\n" % subset_label)
        out_json_container[subset_label] = {"skipped": True, "reason": "empty panel"}
        return
    thirds = _thirds_split(df)
    result_by_feature = {}
    for feature in PRIMARY_FEATURES:
        signs = []
        cell_lines = []
        for ti, third in enumerate(thirds):
            res = run_ols_one_feature(third, label_col, feature, control_cols)
            if res is None or res.get("skipped"):
                n = res.get("n", 0) if res else 0
                signs.append(None)
                cell_lines.append("        third %d: %s" % (ti + 1, skip_str(n)))
            else:
                sign = "+" if res["beta_feature"] > 0 else ("-" if res["beta_feature"] < 0 else "0")
                signs.append(sign)
                cell_lines.append("        third %d: N=%d beta=%.4f (sign=%s) t_plain=%.3f" %
                                   (ti + 1, res["n"], res["beta_feature"], sign, res["t_plain_feature"]))
        out_lines.append("    [%s] %s:\n" % (subset_label, feature))
        for cl in cell_lines:
            out_lines.append(cl + "\n")
        non_null_signs = [s for s in signs if s is not None]
        flip = len(set(non_null_signs)) > 1 if len(non_null_signs) >= 2 else False
        out_lines.append("        sign sequence=%s flip=%s\n" % (signs, flip))
        result_by_feature[feature] = {"signs": signs, "flip": flip}
    out_json_container[subset_label] = result_by_feature


def phase3_stability(panel_a: pl.DataFrame, panel_b_pooled: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== PHASE 3: SUB-PERIOD STABILITY ===")
    out_lines.append("\n=== PHASE 3: SUB-PERIOD STABILITY ===\n")
    out_json["phase3"] = {"panel_b": {}, "panel_a": {}, "coverage_map_status": None}

    # -- Panel B: pnl15 ~ feature + controls, thirds --
    out_lines.append("\n  --- Panel B (pnl15), pooled thirds ---\n")
    _stability_one(panel_b_pooled, "pnl15", CONTROL_COLS_ORTHO, "pooled", out_lines, out_json["phase3"]["panel_b"])

    constant_symbols, skip_reason = _load_coverage_map()
    out_json["phase3"]["coverage_map_status"] = "loaded" if constant_symbols is not None else ("SKIPPED: %s" % skip_reason)
    if constant_symbols is None:
        out_lines.append("\n  --- Panel B (pnl15), constant-coverage-restricted thirds: SKIPPED (%s) ---\n" % skip_reason)
    else:
        out_lines.append("\n  --- Panel B (pnl15), constant-coverage-restricted thirds (%d constant symbols) ---\n" %
                          len(constant_symbols))
        if panel_b_pooled.height and "symbol" in panel_b_pooled.columns:
            restricted = panel_b_pooled.filter(pl.col("symbol").is_in(list(constant_symbols)))
        else:
            restricted = pl.DataFrame()
        _stability_one(restricted, "pnl15", CONTROL_COLS_ORTHO, "constant_coverage", out_lines, out_json["phase3"]["panel_b"])

    # -- Panel A: net_path ~ feature + controls, thirds --
    out_lines.append("\n  --- Panel A (net_path), pooled thirds ---\n")
    _stability_one(panel_a, "net_path", CONTROL_COLS_MECH, "pooled", out_lines, out_json["phase3"]["panel_a"])

    if constant_symbols is None:
        out_lines.append("\n  --- Panel A (net_path), constant-coverage-restricted thirds: SKIPPED (%s) ---\n" % skip_reason)
    else:
        out_lines.append("\n  --- Panel A (net_path), constant-coverage-restricted thirds (%d constant symbols) ---\n" %
                          len(constant_symbols))
        if panel_a.height and "symbol" in panel_a.columns:
            restricted_a = panel_a.filter(pl.col("symbol").is_in(list(constant_symbols)))
        else:
            restricted_a = pl.DataFrame()
        _stability_one(restricted_a, "net_path", CONTROL_COLS_MECH, "constant_coverage", out_lines, out_json["phase3"]["panel_a"])


# ---------------------------------------------------------------------------
# Phase 4: REDUNDANCY
# ---------------------------------------------------------------------------
def phase4_redundancy(panel_b_pooled: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== PHASE 4: REDUNDANCY ===")
    out_lines.append("\n=== PHASE 4: REDUNDANCY ===\n")
    cols = PRIMARY_FEATURES + ["opt_skew", "semivol_r"]
    present = [c for c in cols if c in panel_b_pooled.columns]
    missing = [c for c in cols if c not in panel_b_pooled.columns]
    if missing:
        out_lines.append("  missing columns (not present in panel B): %s\n" % missing)

    if panel_b_pooled.height == 0 or len(present) < 2:
        out_lines.append("  Panel B empty or insufficient columns -- Phase 4 SKIPPED\n")
        out_json["phase4"] = {"skipped": True}
        return

    sub = panel_b_pooled.select(present)
    sub = sub.with_columns([pl.col(c).fill_nan(None) for c in sub.columns
                            if sub[c].dtype in (pl.Float64, pl.Float32)]).drop_nulls()
    n = sub.height
    if not n_ok(n):
        out_lines.append("  correlation matrix: %s\n" % skip_str(n))
        out_json["phase4"] = {"skipped": True, "n": n}
        return

    arrs = {c: sub[c].to_numpy().astype(np.float64) for c in present}
    pear = {}
    spear = {}
    out_lines.append("  N=%d (complete rows across %s)\n" % (n, present))
    out_lines.append("  Pearson matrix:\n")
    header = "         " + " ".join("%12s" % c for c in present)
    out_lines.append("  " + header + "\n")
    for c1 in present:
        row_vals = []
        for c2 in present:
            r, _ = pearsonr(arrs[c1], arrs[c2])
            row_vals.append(r)
            pear["%s|%s" % (c1, c2)] = float(r)
        out_lines.append("  %8s " % c1 + " ".join("%12.3f" % v for v in row_vals) + "\n")

    out_lines.append("\n  Spearman matrix:\n")
    out_lines.append("  " + header + "\n")
    for c1 in present:
        row_vals = []
        for c2 in present:
            r, _ = spearmanr(arrs[c1], arrs[c2])
            row_vals.append(r)
            spear["%s|%s" % (c1, c2)] = float(r)
        out_lines.append("  %8s " % c1 + " ".join("%12.3f" % v for v in row_vals) + "\n")

    out_json["phase4"] = {"n": n, "columns": present, "pearson": pear, "spearman": spear, "skipped": False}


# ---------------------------------------------------------------------------
# Self-test: guard actually raises
# ---------------------------------------------------------------------------
def selftest_guard():
    log("=== --selftest-guard: verifying assert_no_holdout_leak RAISES on post-cutoff data ===")
    fake = pl.DataFrame({
        "symbol": ["AAA", "BBB", "CCC"],
        "date": [date(2026, 1, 1), date(2026, 3, 1), date(2026, 6, 20)],
        "value": [1.0, 2.0, 3.0],
    })
    log("fake frame dates: %s (cutoff=%s)" % ([d.isoformat() for d in fake["date"].to_list()], HOLDOUT_CUTOFF.isoformat()))
    try:
        assert_no_holdout_leak(fake, "gex_selftest_guard")
        log("GUARD SELFTEST: FAIL -- assert_no_holdout_leak did NOT raise on a 2026-06-20 row")
        return 1
    except AssertionError as e:
        log("GUARD SELFTEST: PASS -- assert_no_holdout_leak raised as expected: %s" % str(e)[:200])
        return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="GEX deliverable 3: pre-registered analysis")
    parser.add_argument("--features", type=str, default=DEFAULT_FEATURES, help="path to features parquet")
    parser.add_argument("--selftest-guard", action="store_true", help="verify the holdout guard raises, then exit")
    args = parser.parse_args()

    if args.selftest_guard:
        sys.exit(selftest_guard())

    t_start = time.time()
    log("=== GEX gex_test.py -- deliverable 3 ===")
    log("features path: %s" % args.features)
    log("holdout cutoff (live, from strategy_config.py): %s" % HOLDOUT_CUTOFF.isoformat())

    if not os.path.exists(args.features):
        log("ERROR: features file not found: %s" % args.features)
        sys.exit(1)

    features = pl.read_parquet(args.features)
    log("features: N=%d rows, %d columns" % (features.height, len(features.columns)))

    out_lines = []
    out_json = {"meta": {}}
    out_lines.append("=== GEX gex_test.py results ===\n")
    out_lines.append("features path: %s\n" % args.features)
    out_lines.append("features N=%d\n" % features.height)
    out_lines.append("holdout cutoff: %s\n" % HOLDOUT_CUTOFF.isoformat())

    # --- Panel A ---
    panel_a = build_panel_a(features)
    out_lines.append("\nPanel A (MECHANISM) N=%d\n" % panel_a.height)
    out_json["meta"]["panel_a_n"] = panel_a.height

    # --- ONE bulk price query, pre-warmed with the FULL symbol union needed by
    # Panel B's ext-arm AND Phase C.ii's mechanism-gap slice (see D5/HARD STOPS:
    # "MySQL = the ONE bulk price query only"). Both consumers below then get
    # guaranteed cache hits from _load_or_build_price_cache; its own top-up path
    # is a defensive fallback only (fires if this union ever misses a symbol).
    price_symbol_union = _collect_price_symbol_union()
    log("\n--- Pre-warming price cache: union of Panel B ext-arm + Phase C.ii symbols = %d ---" %
        len(price_symbol_union))
    _, prewarm_meta = _load_or_build_price_cache(price_symbol_union)
    out_json["meta"]["price_query_prewarm"] = prewarm_meta

    # --- Panel B ---
    panel_b_pooled, panel_b_proxy_only, panel_b_meta = build_panel_b(features)
    out_lines.append("Panel B (ORTHO) proxy-only N=%d, ext-added N=%d, pooled N=%d\n" %
                      (panel_b_meta["n_proxy_only"], panel_b_meta["n_ext_added"], panel_b_meta["n_pooled"]))
    out_json["meta"]["panel_b"] = panel_b_meta

    # --- Panel C ---
    panel_c = build_panel_c(features)
    out_lines.append("Panel C (FORWARD/OOS) oos_resolved_n=%d, mech_gap_status=%s\n" %
                      (panel_c.get("oos_resolved_n", 0), panel_c.get("mech_gap_status", "unknown")))
    out_json["meta"]["panel_c"] = {
        "oos_total_n": panel_c.get("oos_total_n", 0),
        "oos_resolved_n": panel_c.get("oos_resolved_n", 0),
        "mech_gap_status": panel_c.get("mech_gap_status", "unknown"),
        "mech_gap_n": (panel_c["mech_gap_df"].height if panel_c.get("mech_gap_df") is not None else 0),
        "true_holdout_n": (panel_c["true_holdout_df"].height if panel_c.get("true_holdout_df") is not None else 0),
    }

    # --- Phase 1-4 ---
    phase1_univariate(panel_a, panel_b_pooled, out_lines, out_json)
    phase2_ols(panel_a, panel_b_pooled, panel_b_proxy_only, out_lines, out_json)
    phase3_stability(panel_a, panel_b_pooled, out_lines, out_json)
    phase4_redundancy(panel_b_pooled, out_lines, out_json)

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
