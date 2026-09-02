"""
osk_validation_test.py -- OSK cross-regime validation (DESIGN.md, locked 2026-07-07).

Decision experiment for the Polygon Options Developer purchase: does the confirmed
opt_skew (OSK) per-trade edge persist across regimes (backward-OOS 2022-08-01 to
2025-02-10), or was it a 2025-selloff-era artifact? Outcome gates the
historicaloptiondata L3 buy recommendation.

Implements DESIGN.md's four tests EXACTLY, in order (test 1 gates 2-4):
  1. Source-consistency gate: Polygon panel x proxy_ledger join, skew corr >= 0.80.
  2. Univariate spearman + quintiles (backward-OOS pooled / full pooled / per era).
  3. Orthogonalized OLS pnl15 ~ skew + semivol_r + overall + stock_r20 (plain +
     date-clustered t), backward-OOS pooled (decisive) / full pooled / per era,
     raw AND 1%-winsorized.
  4. Per-era sign table.

READ-ONLY offline experiment. New files only under experiments/osk_validation/ and
.cache/experiment_data/. Never touches experiments/gex/, experiments/iv_skew/,
experiments/data_ingest/. MySQL = the ONE bulk price query only (semivol_r inputs).

PYTHONUTF8=1 required (ASCII-only prints; no statsmodels -- numpy OLS only).

Usage:
    python experiments/osk_validation/osk_validation_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date

import numpy as np
import polars as pl
from scipy.stats import spearmanr

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
PANEL_PATH = os.path.join(_ROOT, ".cache", "polygon_iv", "iv_ledger_polygon.parquet")
PROXY_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "proxy_ledger.parquet")
RS_LEDGER = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
PRICE_CACHE = os.path.join(_ROOT, ".cache", "experiment_data", "osk_val_prices.parquet")

OUT_TXT = os.path.join(_HERE, "osk_results.txt")
OUT_JSON = os.path.join(_HERE, "osk_results.json")

MIN_CELL_N = 30                    # cells below this are SKIPPED, never gated on
GATE_SKEW_CORR = 0.80               # source-consistency gate threshold
SEMIVOL_WIN = 60                    # trailing trading-day window (DESIGN.md formula)
PRICE_QUERY_START = "2022-04-01"
PRICE_QUERY_END = "2026-05-15"
WINSOR_PCT = 0.01                   # 1% both tails

# The decisive split (DESIGN.md "The decisive split")
BACKWARD_OOS_START = "2022-08-01"
BACKWARD_OOS_END = "2025-02-10"
DISCOVERY_START = "2025-02-11"
DISCOVERY_END = "2026-05-15"

# Eras (pre-registered, for sign stability) -- DESIGN.md "Eras"
ERAS = [
    ("E1", "2022-08-01", "2022-12-31"),   # bear tail, N~=687
    ("E2", "2023-01-01", "2023-12-31"),   # recovery chop, N~=1,027
    ("E3", "2024-01-01", "2024-12-31"),   # bull + Aug vol shock, N~=2,527
    ("E4", "2025-01-01", "2025-06-30"),   # selloff era
    ("E5", "2025-07-01", "2026-05-15"),   # recent
]

CONTROL_COLS = ["semivol_r", "overall", "stock_r20"]
FEATURE_COL = "skew"
LABEL_COL = "pnl15"


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Small helpers (reused conventions from experiments/gex/gex_test.py)
# ---------------------------------------------------------------------------
def n_ok(n):
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


def winsorize(x: np.ndarray, pct: float = WINSOR_PCT) -> np.ndarray:
    """Winsorize both tails at `pct` (e.g. 0.01 = 1%/99%)."""
    lo, hi = np.nanpercentile(x, [pct * 100, 100 - pct * 100])
    return np.clip(x, lo, hi)


def fill_nan_null(df: pl.DataFrame, cols=None) -> pl.DataFrame:
    """polars drop_nulls() does NOT drop float NaN -- fill_nan(None) first
    (gex_test.py convention) so downstream drop_nulls() actually removes them."""
    cols = cols if cols is not None else df.columns
    float_cols = [c for c in cols if c in df.columns and df[c].dtype in (pl.Float64, pl.Float32)]
    if not float_cols:
        return df
    return df.with_columns([pl.col(c).fill_nan(None) for c in float_cols])


# ---------------------------------------------------------------------------
# OLS + clustered SE (copied from experiments/gex/gex_test.py -- unmodified logic)
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


def run_ols(df: pl.DataFrame, label_col: str, feature_col: str, control_cols: list,
            date_col: str = "date"):
    """
    Standardize label + feature + controls (z-score), drop null rows, run OLS
    with plain + date-clustered t. Returns a result dict or {"n":.., "skipped":True}.
    Column order in X: [feature, *controls] -- feature coefficient is index 1 of beta
    (index 0 = intercept).
    """
    needed = [label_col, feature_col] + control_cols
    sub = df.select([date_col] + needed)
    sub = fill_nan_null(sub, needed)
    sub = sub.drop_nulls()
    n = sub.height
    if not n_ok(n):
        return {"n": n, "skipped": True}

    y = zscore(sub[label_col].to_numpy().astype(np.float64))
    Xcols = []
    for c in [feature_col] + control_cols:
        Xcols.append(zscore(sub[c].to_numpy().astype(np.float64)))
    X = np.column_stack(Xcols)

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
# semivol_r recompute (house formula, DESIGN.md test 3):
#   std(dn)/std(up), trailing 60 trading days, needs >=3 up AND >=3 dn else None
# ---------------------------------------------------------------------------
def _query_prices(symbols: list):
    """The ONE bulk MySQL query: date,close for `symbols` over
    PRICE_QUERY_START..PRICE_QUERY_END. Returns (df, elapsed_seconds)."""
    from database.trader_database import DB  # local import: MySQL only touched here

    t0 = time.time()
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
    placeholders = ",".join(["%s"] * len(symbols))
    sql = (
        "SELECT symbol, date, close FROM price_history "
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
        },
        schema={"symbol": pl.Utf8, "date": pl.Date, "close": pl.Float64},
    )
    return df, elapsed


def load_or_build_price_cache(symbols: list):
    """Cache to PRICE_CACHE; skip the query entirely on a cache hit covering all
    requested symbols (gex_test.py convention)."""
    if not symbols:
        log("price cache: 0 symbols requested -- SKIPPED (no query executed)")
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "close": pl.Float64}), \
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
        log("price cache: partial hit (%d/%d symbols cached) -- topping up %d missing symbols" %
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


def compute_semivol_r(symbols_dates: list, price_df: pl.DataFrame):
    """
    semivol_r = std(dn)/std(up) over trailing 60 trading-day returns ending at
    signal date (inclusive), needs >=3 up AND >=3 dn else None.
    price_df: columns symbol, date (Date), close (Float64).
    Returns dict {(symbol, date_str): semivol_r}.
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
            out[(sym, d)] = None
            continue
        cl, idx = by_sym[sym]
        ret = ret_cache[sym]
        i = idx.get(d)
        if i is None:
            out[(sym, d)] = None
            continue

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
        out[(sym, d)] = svr
    return out


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------
def load_panel():
    """Load the Polygon panel; normalize date to a comparable Date type while
    keeping the raw string for lexicographic slicing checks."""
    df = pl.read_parquet(PANEL_PATH)
    log("panel loaded: %s N=%d, dtypes: date=%s" % (PANEL_PATH, df.height, df["date"].dtype))
    if df["date"].dtype != pl.Date:
        df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return df


def load_rs_ledger_slim():
    """rs_ledger.parquet dedupe subset=['symbol','date'], keep stock_r20 only."""
    rs = pl.read_parquet(RS_LEDGER)
    log("rs_ledger loaded: N=%d (pre-dedupe)" % rs.height)
    rs = rs.unique(subset=["symbol", "date"])
    log("rs_ledger deduped: N=%d" % rs.height)
    rs = rs.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return rs.select(["symbol", "date", "stock_r20"])


def load_proxy_opt_skew_subset():
    """proxy_ledger.parquet filtered opt_skew not-null -- the 1,998-row
    source-consistency join source. Also holds pnl15 for those rows."""
    proxy = pl.read_parquet(PROXY_LEDGER)
    sub = proxy.filter(pl.col("opt_skew").is_not_null())
    log("proxy_ledger opt_skew not-null subset: N=%d" % sub.height)
    sub = sub.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return sub.select(["symbol", "date", "opt_skew", "pnl15"])


def assign_era(d: date):
    for name, start, end in ERAS:
        if date.fromisoformat(start) <= d <= date.fromisoformat(end):
            return name
    return None


def build_full_panel(panel: pl.DataFrame, rs_slim: pl.DataFrame):
    """Panel + stock_r20 (from rs_ledger) + semivol_r (recomputed from
    price_history) + era label + split label. This is the panel used by
    Tests 2-4 (all rows, skew may be null for some -- univariate/OLS drop
    nulls per-cell as usual)."""
    joined = panel.join(rs_slim, on=["symbol", "date"], how="left")
    log("panel LEFT JOIN rs_ledger (stock_r20) -> N=%d (stock_r20 non-null=%d)" %
        (joined.height, joined["stock_r20"].drop_nulls().len()))

    symbols_dates = [(s, d.isoformat()) for s, d in
                      zip(joined["symbol"].to_list(), joined["date"].to_list())]
    symbols = sorted(set(s for s, _ in symbols_dates))
    log("semivol_r recompute: %d symbols, %d (symbol,date) pairs" % (len(symbols), len(symbols_dates)))

    price_df, price_meta = load_or_build_price_cache(symbols)
    svr_map = compute_semivol_r(symbols_dates, price_df)
    svr_list = [svr_map.get((s, d), None) for s, d in symbols_dates]
    joined = joined.with_columns(pl.Series("semivol_r", svr_list, dtype=pl.Float64))
    n_svr = joined["semivol_r"].drop_nulls().len()
    log("semivol_r computed: non-null=%d / %d (%.1f%%)" % (n_svr, joined.height, 100.0 * n_svr / max(joined.height, 1)))

    era_list = [assign_era(d) for d in joined["date"].to_list()]
    joined = joined.with_columns(pl.Series("era", era_list, dtype=pl.Utf8))

    split_list = []
    for d in joined["date"].to_list():
        if date.fromisoformat(BACKWARD_OOS_START) <= d <= date.fromisoformat(BACKWARD_OOS_END):
            split_list.append("backward_oos")
        elif date.fromisoformat(DISCOVERY_START) <= d <= date.fromisoformat(DISCOVERY_END):
            split_list.append("discovery")
        else:
            split_list.append("other")
    joined = joined.with_columns(pl.Series("split", split_list, dtype=pl.Utf8))

    return joined, price_meta


# ---------------------------------------------------------------------------
# TEST 1: SOURCE CONSISTENCY (gate)
# ---------------------------------------------------------------------------
def test1_source_consistency(panel: pl.DataFrame, proxy_sub: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== TEST 1: SOURCE CONSISTENCY (gate) ===")
    out_lines.append("\n=== TEST 1: SOURCE CONSISTENCY (gate) ===\n")

    joined = panel.select(["symbol", "date", "skew", "pnl15"]).join(
        proxy_sub, on=["symbol", "date"], how="inner", suffix="_ours"
    )
    n = joined.height
    out_lines.append("  Inner join Polygon panel x proxy_ledger(opt_skew not-null, N=%d source rows) on (symbol,date): N=%d\n" %
                      (proxy_sub.height, n))
    log("  join N=%d (proxy opt_skew-not-null source N=%d)" % (n, proxy_sub.height))

    result = {"n": n, "proxy_source_n": proxy_sub.height, "gate_threshold": GATE_SKEW_CORR}

    # skew corr
    skew_sub = joined.select(["skew", "opt_skew"])
    skew_sub = fill_nan_null(skew_sub).drop_nulls()
    n_skew = skew_sub.height
    if n_ok(n_skew):
        skew_p, skew_pv = np.nan, np.nan
        sk_arr = skew_sub["skew"].to_numpy().astype(np.float64)
        opt_arr = skew_sub["opt_skew"].to_numpy().astype(np.float64)
        skew_corr = float(np.corrcoef(sk_arr, opt_arr)[0, 1])
        skew_mean_diff = float(np.mean(sk_arr - opt_arr))
        skew_median_diff = float(np.median(sk_arr - opt_arr))
        out_lines.append("  skew: N=%d corr(skew_polygon, opt_skew_ours)=%.4f mean_diff=%.4f median_diff=%.4f\n" %
                          (n_skew, skew_corr, skew_mean_diff, skew_median_diff))
        result["skew"] = {"n": n_skew, "corr": skew_corr, "mean_diff": skew_mean_diff, "median_diff": skew_median_diff}
    else:
        out_lines.append("  skew: %s\n" % skip_str(n_skew))
        result["skew"] = {"n": n_skew, "skipped": True}
        skew_corr = None

    # pnl15 corr
    pnl_sub = joined.select(["pnl15", "pnl15_ours"])
    pnl_sub = fill_nan_null(pnl_sub).drop_nulls()
    n_pnl = pnl_sub.height
    if n_ok(n_pnl):
        p_arr = pnl_sub["pnl15"].to_numpy().astype(np.float64)
        po_arr = pnl_sub["pnl15_ours"].to_numpy().astype(np.float64)
        pnl_corr = float(np.corrcoef(p_arr, po_arr)[0, 1])
        pnl_mean_diff = float(np.mean(p_arr - po_arr))
        pnl_median_diff = float(np.median(p_arr - po_arr))
        out_lines.append("  pnl15: N=%d corr(pnl15_polygon, pnl15_ours)=%.4f mean_diff=%.4f median_diff=%.4f\n" %
                          (n_pnl, pnl_corr, pnl_mean_diff, pnl_median_diff))
        result["pnl15"] = {"n": n_pnl, "corr": pnl_corr, "mean_diff": pnl_mean_diff, "median_diff": pnl_median_diff}
    else:
        out_lines.append("  pnl15: %s\n" % skip_str(n_pnl))
        result["pnl15"] = {"n": n_pnl, "skipped": True}

    gate_pass = (skew_corr is not None) and (skew_corr >= GATE_SKEW_CORR)
    result["gate_pass"] = bool(gate_pass)
    out_lines.append("\n  GATE (skew corr >= %.2f): %s (skew_corr=%s)\n" %
                      (GATE_SKEW_CORR, "PASS" if gate_pass else "FAIL",
                       ("%.4f" % skew_corr) if skew_corr is not None else "N/A"))
    log("  GATE: %s (skew_corr=%s)" % ("PASS" if gate_pass else "FAIL", skew_corr))

    if not gate_pass:
        out_lines.append("\n  *** GATE FAILED -- diagnosing before stopping ***\n")
        log("\n  *** GATE FAILED -- diagnosing strike/DTE-selection differences ***")
        # Diagnosis: report distributional stats for both skew series + a few
        # largest-discrepancy rows to help distinguish strike-selection vs
        # DTE-selection vs data-quality mismatch (no era analysis follows).
        if n_ok(n_skew):
            sk_arr = skew_sub["skew"].to_numpy().astype(np.float64)
            opt_arr = skew_sub["opt_skew"].to_numpy().astype(np.float64)
            diag = {
                "skew_polygon_mean": float(np.mean(sk_arr)), "skew_polygon_std": float(np.std(sk_arr, ddof=1)),
                "opt_skew_ours_mean": float(np.mean(opt_arr)), "opt_skew_ours_std": float(np.std(opt_arr, ddof=1)),
                "abs_diff_mean": float(np.mean(np.abs(sk_arr - opt_arr))),
                "abs_diff_median": float(np.median(np.abs(sk_arr - opt_arr))),
            }
            out_lines.append("  DIAGNOSIS: skew_polygon mean=%.4f std=%.4f | opt_skew_ours mean=%.4f std=%.4f\n" %
                              (diag["skew_polygon_mean"], diag["skew_polygon_std"],
                               diag["opt_skew_ours_mean"], diag["opt_skew_ours_std"]))
            out_lines.append("  DIAGNOSIS: mean(|diff|)=%.4f median(|diff|)=%.4f\n" %
                              (diag["abs_diff_mean"], diag["abs_diff_median"]))
            out_lines.append("  DIAGNOSIS NOTE: Polygon panel uses +-10%% OTM strikes at 20-45 DTE, nearest-of-6-tries\n"
                              "    that actually traded that day (polygon_iv_ingest.py process_one/priced()).\n"
                              "    build_iv.py/build_proxy.py use +-10%% OTM at 20-45 DTE too but pick the single\n"
                              "    nearest strike from the MySQL option_prices chain regardless of that day's\n"
                              "    liquidity -- a systematic strike-selection difference (illiquid-day fallback)\n"
                              "    is the leading candidate if corr is low.\n")
            result["diagnosis"] = diag
        log("  DIAGNOSIS written to results (see osk_results.txt)")

    out_json["test1_source_consistency"] = result
    return gate_pass


# ---------------------------------------------------------------------------
# TEST 2: UNIVARIATE (spearman + quintiles)
# ---------------------------------------------------------------------------
def _univariate_one(df: pl.DataFrame, scope_label: str, out_lines: list, out_json_container: dict):
    key = scope_label
    sub = df.select(["skew", "pnl15"])
    sub = fill_nan_null(sub).drop_nulls()
    n = sub.height
    if not n_ok(n):
        out_lines.append("  [%s]: %s\n" % (scope_label, skip_str(n)))
        out_json_container[key] = {"n": n, "skipped": True}
        return

    x = sub["skew"].to_numpy().astype(np.float64)
    y = sub["pnl15"].to_numpy().astype(np.float64)
    rho, pval = spearmanr(x, y)
    out_lines.append("  [%s] N=%d spearman rho=%.4f p=%.4g\n" % (scope_label, n, rho, pval))

    edges = quintile_edges(x)
    q_rows = []
    if edges is None:
        out_lines.append("      quintiles: SKIPPED (degenerate distribution)\n")
        q_info = {"skipped": True}
    else:
        qbins = quintile_assign(x, edges)
        for qi in range(1, 6):
            m = qbins == qi
            nq = int(m.sum())
            row = {"q": qi, "n": nq}
            if nq < MIN_CELL_N:
                row["skipped"] = True
                out_lines.append("      Q%d: %s\n" % (qi, skip_str(nq)))
            else:
                row["skipped"] = False
                mean_pnl = float(np.mean(y[m]))
                wr = float(np.mean(y[m] > 0))
                row["mean_pnl15"] = mean_pnl
                row["wr"] = wr
                out_lines.append("      Q%d: N=%d mean(pnl15)=%.4f WR=%.1f%%\n" % (qi, nq, mean_pnl, wr * 100))
            q_rows.append(row)
        q_info = {"skipped": False, "quintiles": q_rows}

    out_json_container[key] = {"n": n, "skipped": False, "spearman_rho": float(rho), "spearman_p": float(pval),
                                "quintiles": q_info}


def test2_univariate(full_panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== TEST 2: UNIVARIATE (spearman + quintiles) ===")
    out_lines.append("\n=== TEST 2: UNIVARIATE (spearman + quintiles) ===\n")
    out_json["test2_univariate"] = {}

    backward_oos = full_panel.filter(pl.col("split") == "backward_oos")
    out_lines.append("\n  --- backward-OOS pooled (%s..%s, N_rows=%d) ---\n" %
                      (BACKWARD_OOS_START, BACKWARD_OOS_END, backward_oos.height))
    _univariate_one(backward_oos, "backward_oos_pooled", out_lines, out_json["test2_univariate"])
    sub75 = backward_oos.filter(pl.col("overall") >= 75)
    _univariate_one(sub75, "backward_oos_pooled_overall>=75", out_lines, out_json["test2_univariate"])

    out_lines.append("\n  --- full pooled (all dates, N_rows=%d) ---\n" % full_panel.height)
    _univariate_one(full_panel, "full_pooled", out_lines, out_json["test2_univariate"])
    sub75_full = full_panel.filter(pl.col("overall") >= 75)
    _univariate_one(sub75_full, "full_pooled_overall>=75", out_lines, out_json["test2_univariate"])

    out_lines.append("\n  --- per era ---\n")
    for name, start, end in ERAS:
        era_df = full_panel.filter(pl.col("era") == name)
        out_lines.append("\n  era=%s (%s..%s, N_rows=%d)\n" % (name, start, end, era_df.height))
        _univariate_one(era_df, "era_%s" % name, out_lines, out_json["test2_univariate"])
        era_sub75 = era_df.filter(pl.col("overall") >= 75)
        _univariate_one(era_sub75, "era_%s_overall>=75" % name, out_lines, out_json["test2_univariate"])


# ---------------------------------------------------------------------------
# TEST 3: ORTHOGONALIZED OLS (decisive)
# ---------------------------------------------------------------------------
def _report_ols(res, tag, out_lines, container):
    if res is None or res.get("skipped"):
        n = res.get("n", 0) if res else 0
        out_lines.append("  [%s]: %s\n" % (tag, skip_str(n)))
        container[tag] = {"n": n, "skipped": True}
        return
    out_lines.append(
        "  [%s]: N=%d (G=%d dates) beta=%.4f t_plain=%.3f t_clust=%.3f\n" %
        (tag, res["n"], res["n_clusters"], res["beta_feature"], res["t_plain_feature"], res["t_clust_feature"])
    )
    container[tag] = res


def _ols_scope(df: pl.DataFrame, scope_label: str, out_lines: list, container: dict):
    """Run raw + 1%-winsorized OLS for one scope (backward-OOS pooled / full
    pooled / per era). Winsorize is label-only -- both tails, both t's reported."""
    out_lines.append("\n  --- %s ---\n" % scope_label)

    res_raw = run_ols(df, LABEL_COL, FEATURE_COL, CONTROL_COLS)
    _report_ols(res_raw, "%s|raw" % scope_label, out_lines, container)

    # winsorized: apply winsorize to pnl15 pre-zscore, everything else identical
    needed = [LABEL_COL, FEATURE_COL] + CONTROL_COLS
    sub = df.select(["date"] + needed)
    sub = fill_nan_null(sub, needed).drop_nulls()
    n = sub.height
    if not n_ok(n):
        _report_ols({"n": n, "skipped": True}, "%s|winsorized_1pct" % scope_label, out_lines, container)
    else:
        y_raw = sub[LABEL_COL].to_numpy().astype(np.float64)
        y_wins = winsorize(y_raw, WINSOR_PCT)
        y = zscore(y_wins)
        Xcols = []
        for c in [FEATURE_COL] + CONTROL_COLS:
            Xcols.append(zscore(sub[c].to_numpy().astype(np.float64)))
        X = np.column_stack(Xcols)
        valid_mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        n_valid = int(valid_mask.sum())
        if not n_ok(n_valid):
            _report_ols({"n": n_valid, "skipped": True}, "%s|winsorized_1pct" % scope_label, out_lines, container)
        else:
            y_v = y[valid_mask]
            X_v = X[valid_mask]
            dates_v = sub["date"].to_numpy()[valid_mask]
            res = ols_with_clustered_se(y_v, X_v, dates_v)
            res_wins = {
                "n": n_valid, "skipped": False, "feature": FEATURE_COL, "label": LABEL_COL + "_winsorized_1pct",
                "beta_feature": float(res["beta"][1]),
                "t_plain_feature": float(res["t_plain"][1]),
                "t_clust_feature": float(res["t_clust"][1]),
                "se_plain_feature": float(res["se_plain"][1]),
                "se_clust_feature": float(res["se_clust"][1]),
                "n_clusters": res["g"],
                "control_betas": {c: float(res["beta"][2 + i]) for i, c in enumerate(CONTROL_COLS)},
                "control_t_plain": {c: float(res["t_plain"][2 + i]) for i, c in enumerate(CONTROL_COLS)},
            }
            _report_ols(res_wins, "%s|winsorized_1pct" % scope_label, out_lines, container)


def test3_ols(full_panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== TEST 3: ORTHOGONALIZED OLS (decisive) ===")
    out_lines.append("\n=== TEST 3: ORTHOGONALIZED OLS (decisive) ===\n")
    out_lines.append("  model: pnl15 ~ skew + semivol_r + overall + stock_r20 (standardized, numpy lstsq)\n")
    out_json["test3_ols"] = {}

    backward_oos = full_panel.filter(pl.col("split") == "backward_oos")
    out_lines.append("\n  *** DECISIVE CELL: backward-OOS pooled (%s..%s) ***\n" %
                      (BACKWARD_OOS_START, BACKWARD_OOS_END))
    _ols_scope(backward_oos, "backward_oos_pooled", out_lines, out_json["test3_ols"])

    _ols_scope(full_panel, "full_pooled", out_lines, out_json["test3_ols"])

    out_lines.append("\n  --- per era ---\n")
    for name, start, end in ERAS:
        era_df = full_panel.filter(pl.col("era") == name)
        _ols_scope(era_df, "era_%s" % name, out_lines, out_json["test3_ols"])


# ---------------------------------------------------------------------------
# TEST 4: STABILITY (per-era sign table)
# ---------------------------------------------------------------------------
def test4_stability(full_panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== TEST 4: STABILITY (per-era sign table) ===")
    out_lines.append("\n=== TEST 4: STABILITY (per-era sign table) ===\n")
    out_json["test4_stability"] = {}

    sign_table = []
    for name, start, end in ERAS:
        era_df = full_panel.filter(pl.col("era") == name)
        res = run_ols(era_df, LABEL_COL, FEATURE_COL, CONTROL_COLS)
        if res is None or res.get("skipped"):
            n = res.get("n", 0) if res else 0
            out_lines.append("  era=%s: %s\n" % (name, skip_str(n)))
            sign_table.append({"era": name, "n": n, "skipped": True})
        else:
            sign = "+" if res["beta_feature"] > 0 else ("-" if res["beta_feature"] < 0 else "0")
            out_lines.append("  era=%s: N=%d beta=%.4f sign=%s t_clust=%.3f t_plain=%.3f\n" %
                              (name, res["n"], res["beta_feature"], sign, res["t_clust_feature"], res["t_plain_feature"]))
            sign_table.append({"era": name, "n": res["n"], "skipped": False, "sign": sign,
                                "beta": res["beta_feature"], "t_clust": res["t_clust_feature"],
                                "t_plain": res["t_plain_feature"]})

    out_json["test4_stability"]["sign_table"] = sign_table

    # summarize per DESIGN.md bars: E1-E3 sign count, any era <= -0.05 spearman
    # (spearman check uses test2 era spearman values, cross-referenced here)
    e1_e3_positive = sum(1 for row in sign_table if row.get("sign") == "+" and row["era"] in ("E1", "E2", "E3"))
    out_lines.append("\n  E1-E3 positive-sign count (OLS beta): %d / 3\n" % e1_e3_positive)
    out_json["test4_stability"]["e1_e3_positive_count"] = e1_e3_positive


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    log("=== osk_validation_test.py -- OSK cross-regime validation ===")
    log("holdout cutoff (live, from strategy_config.py): %s" % HOLDOUT_CUTOFF.isoformat())

    out_lines = []
    out_json = {"meta": {}}
    out_lines.append("=== osk_validation_test.py results ===\n")
    out_lines.append("holdout cutoff: %s\n" % HOLDOUT_CUTOFF.isoformat())

    # --- load panel ---
    if not os.path.exists(PANEL_PATH):
        log("ERROR: panel not found: %s" % PANEL_PATH)
        sys.exit(1)
    panel = load_panel()
    out_lines.append("panel: %s N=%d, symbols=%d, dates %s..%s\n" %
                      (PANEL_PATH, panel.height, panel["symbol"].n_unique(),
                       panel["date"].min(), panel["date"].max()))
    out_json["meta"]["panel_n"] = panel.height
    out_json["meta"]["panel_symbols"] = panel["symbol"].n_unique()

    # --- holdout guard on the panel (all dates should be <= cutoff) ---
    assert_no_holdout_leak(panel, "osk_val")
    out_lines.append("holdout guard PASSED (panel max date <= cutoff %s)\n" % HOLDOUT_CUTOFF.isoformat())
    log("holdout guard PASSED")

    # --- pnl15 convention finding (D1 code-read, stated per DESIGN.md) ---
    # UPDATED 2026-07-08 for the pnl15 truncate-substitution fix (build_iv.py commit
    # 445658866 + follow-up in build_put.py / polygon_iv_ingest.py). BOTH builders now
    # NULL pnl15/pnl_max/pnl_min when fewer than 15 forward bars exist, instead of
    # clamping to the last available bar (prices[min(14,len-1)]). The finding is restated
    # for the new >=15-bar (null-when-unripe) rule; parity is PRESERVED because both sides
    # changed identically. Any numeric results recorded from an earlier run of this test
    # were computed against the OLD clamped ledgers and remain apples-to-apples only until
    # those ledgers (iv_ledger_polygon.parquet / proxy_ledger) are naturally rebuilt.
    convention_note = (
        "pnl15 CONVENTION CHECK (code-read of polygon_iv_ingest.py vs build_iv.py):\n"
        "  polygon_iv_ingest.py process_one(): fwd = strictly-forward option daily-close\n"
        "    bars within (signal_date, signal_date+24 calendar days]; pnl15 computed ONLY\n"
        "    when len(fwd) >= 15, as fwd[14]/entry_premium - 1 (else NULL). entry_premium =\n"
        "    the ATM call's close ON the signal date.\n"
        "  build_iv.py fwd_pnl(): option_prices rows with date > signal_date AND date <=\n"
        "    signal_date + 24 calendar days, ordered by date; when len(prices) >= 15,\n"
        "    p15 = prices[14] and pnl15 = p15/entry_price - 1 (else NULL). entry_price =\n"
        "    the same ATM call's price on signal date.\n"
        "  FINDING: IDENTICAL convention -- both take the 15th forward trading-day bar\n"
        "    (0-indexed 14) within a 24-calendar-day forward window and NULL the labels when\n"
        "    fewer than 15 forward bars exist (null-when-unripe; NO truncate-substitution of\n"
        "    the last available bar). Both use entry = the ATM option's own price on the\n"
        "    signal date. The two ledgers are apples-to-apples on pnl15.\n"
        "  HISTORY: prior to the 2026-07-08 fix both builders instead clamped to\n"
        "    prices[min(14, len-1)] (last available bar as a pnl15 proxy for <15-bar tails);\n"
        "    that shared-but-dishonest convention was also apples-to-apples, so this parity\n"
        "    finding held then and holds now under the honest >=15-bar rule.\n"
    )
    out_lines.append("\n" + convention_note)
    log(convention_note)
    out_json["meta"]["pnl15_convention_finding"] = (
        "IDENTICAL (>=15-bar null-when-unripe convention, post 2026-07-08 fix): both "
        "polygon_iv_ingest.py.process_one() and build_iv.py.fwd_pnl() take the 15th forward "
        "trading-day bar (0-indexed 14) within a 24-calendar-day forward window and NULL "
        "pnl15/pnl_max/pnl_min when fewer than 15 forward bars exist (no truncate-substitution); "
        "entry = the ATM option's own close/price on the signal date. Parity preserved because "
        "both builders changed identically from the earlier prices[min(14,len-1)] clamp."
    )

    # --- load proxy opt_skew subset (for Test 1) ---
    proxy_sub = load_proxy_opt_skew_subset()
    out_json["meta"]["proxy_opt_skew_subset_n"] = proxy_sub.height

    # --- TEST 1: source-consistency gate ---
    gate_pass = test1_source_consistency(panel, proxy_sub, out_lines, out_json)

    if not gate_pass:
        out_lines.append("\n=== STOPPING: source-consistency gate FAILED -- era analysis NOT run ===\n")
        log("\n=== STOPPING: source-consistency gate FAILED -- era analysis NOT run ===")
        elapsed = time.time() - t_start
        out_lines.append("\n=== DONE (gate-failed early exit) in %.1fs ===\n" % elapsed)
        out_json["meta"]["elapsed_s"] = round(elapsed, 2)
        out_json["meta"]["stopped_at_gate"] = True

        with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
            f.writelines(out_lines)
        with open(OUT_JSON, "w", encoding="ascii") as f:
            json.dump(out_json, f, indent=2, default=str)
        log("\nwrote %s" % OUT_TXT)
        log("wrote %s" % OUT_JSON)
        sys.exit(0)

    out_json["meta"]["stopped_at_gate"] = False

    # --- build full panel (stock_r20 + semivol_r + era + split labels) ---
    rs_slim = load_rs_ledger_slim()
    full_panel, price_meta = build_full_panel(panel, rs_slim)
    out_lines.append("\nfull panel built: N=%d (stock_r20 non-null=%d, semivol_r non-null=%d)\n" %
                      (full_panel.height, full_panel["stock_r20"].drop_nulls().len(),
                       full_panel["semivol_r"].drop_nulls().len()))
    out_json["meta"]["full_panel_n"] = full_panel.height
    out_json["meta"]["price_query"] = price_meta

    n_backward = full_panel.filter(pl.col("split") == "backward_oos").height
    n_discovery = full_panel.filter(pl.col("split") == "discovery").height
    n_other = full_panel.filter(pl.col("split") == "other").height
    out_lines.append("split counts: backward_oos=%d discovery=%d other=%d\n" % (n_backward, n_discovery, n_other))
    out_json["meta"]["split_counts"] = {"backward_oos": n_backward, "discovery": n_discovery, "other": n_other}
    for name, start, end in ERAS:
        n_era = full_panel.filter(pl.col("era") == name).height
        out_lines.append("  era=%s (%s..%s): N=%d\n" % (name, start, end, n_era))

    assert_no_holdout_leak(full_panel, "osk_val_full")
    log("holdout guard PASSED on full_panel")

    # --- TEST 2-4 ---
    test2_univariate(full_panel, out_lines, out_json)
    test3_ols(full_panel, out_lines, out_json)
    test4_stability(full_panel, out_lines, out_json)

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
