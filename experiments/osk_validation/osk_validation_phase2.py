"""
osk_validation_phase2.py -- OSK AMENDMENT 1 (DESIGN.md, locked 2026-07-07):
D1 diagnosis + D2 measurement-robustness replication, gated Tests 2-4.

Context: Test 1 (source-consistency gate, skew corr >= 0.80) FAILED at 0.199 in
osk_validation_test.py. pnl15 corr = 0.930 (premium data agrees); the SKEW
RECIPES diverge structurally (ours = single nearest strike from an EOD chain
snapshot regardless of that day's liquidity; Polygon's = nearest-of-6-tries
strike that ACTUALLY TRADED that day). AMENDMENT 1 restructures the plan:

  D1 DIAGNOSIS (report-only): quantify what in the 1,917-row overlap join
  drives corr=0.199 -- selection mismatch (strike/DTE) vs price-level
  disagreement. Our ledgers (iv_ledger.parquet / proxy_ledger.parquet) do NOT
  persist atm_strike or dte columns (verified by schema read: build_iv.py
  computes them transiently in SQL via ORDER BY ... LIMIT 1 but never writes
  them out) -- so an exact ours-vs-Polygon strike/DTE match rate is NOT
  computable from stored data. This is itself part of the diagnosis and is
  reported as such. What IS computable: atm_iv correlation on the overlap
  (a clean proxy for "even the ATM selection disagrees or agrees"), the
  Polygon-side DTE distribution (both sides are constructed to the same
  20-45 DTE window by design, so exact DTE distributional comparison across
  ledgers is a construction invariant, not an empirical unknown), and
  corr(skew) computed on the atm_iv-agreement-conditioned subset (the closest
  available proxy for "matched selection" given missing metadata).

  D2 MEASUREMENT-ROBUSTNESS REPLICATION (decisive, independent of D1): does
  POLYGON-skew predict pnl15 on the DISCOVERY window (2025-02-11..2026-05-15,
  N~=4,000) where OUR-skew's edge was originally confirmed? Full battery:
  spearman + quintiles, orthogonalized OLS (skew + semivol_r + overall +
  stock_r20), plain + date-clustered t, raw + 1%-winsorized, FULL + 75+.
  Bars (locked): REPLICATES iff spearman >= +0.055 AND clustered t >= +2.0
  (raw pnl15; winsorized reported alongside, not gated).

  IF D2 REPLICATES: run original Tests 2-4 (backward-OOS pooled, full pooled,
  eras E1-E5, univariate + orthogonalized OLS + per-era sign table) with
  POLYGON-skew uniformly. Original DESIGN.md PASS/WEAK/KILL bars apply to the
  backward-OOS cell.
  IF D2 FAILS: stop after D2. Verdict line = "OSK measurement-robustness FLAG".

This file imports and REUSES (does not duplicate/modify) osk_validation_test.py's
panel loader, OLS+clustered-SE machinery, semivol_r recompute, price-cache
builder, and small helpers. It writes its OWN outputs (osk_results_phase2.txt
/.json) and touches NO other experiment's files.

READ-ONLY offline experiment. New files only under experiments/osk_validation/
and .cache/experiment_data/. MySQL = the ONE bulk price query (shared cache
file with osk_validation_test.py, per DESIGN.md "build once, reuse for eras").
HOLDOUT_DISABLE is never set.

PYTHONUTF8=1 required (ASCII-only prints; no statsmodels -- numpy OLS only).

Usage:
    python experiments/osk_validation/osk_validation_phase2.py
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
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# Reuse (do not duplicate) the existing harness's machinery.
import osk_validation_test as p1  # noqa: E402

# ---------------------------------------------------------------------------
# Paths / constants (phase-2-specific outputs; panel/cache paths reused from p1)
# ---------------------------------------------------------------------------
PANEL_PATH = p1.PANEL_PATH
PROXY_LEDGER = p1.PROXY_LEDGER
RS_LEDGER = p1.RS_LEDGER
PRICE_CACHE = p1.PRICE_CACHE

OUT_TXT = os.path.join(_HERE, "osk_results_phase2.txt")
OUT_JSON = os.path.join(_HERE, "osk_results_phase2.json")

MIN_CELL_N = p1.MIN_CELL_N
CONTROL_COLS = p1.CONTROL_COLS
FEATURE_COL = p1.FEATURE_COL
LABEL_COL = p1.LABEL_COL
ERAS = p1.ERAS
BACKWARD_OOS_START = p1.BACKWARD_OOS_START
BACKWARD_OOS_END = p1.BACKWARD_OOS_END
DISCOVERY_START = p1.DISCOVERY_START
DISCOVERY_END = p1.DISCOVERY_END
WINSOR_PCT = p1.WINSOR_PCT

# AMENDMENT 1 D2 locked bars (half the original +0.110 spearman; see DESIGN.md
# rationale -- vendor/recipe noise at corr 0.199 attenuates a true signal by
# roughly sqrt(reliability); halving is the standard attenuation allowance).
D2_SPEARMAN_BAR = 0.055
D2_TCLUST_BAR = 2.0

log = p1.log
n_ok = p1.n_ok
skip_str = p1.skip_str
zscore = p1.zscore
quintile_edges = p1.quintile_edges
quintile_assign = p1.quintile_assign
winsorize = p1.winsorize
fill_nan_null = p1.fill_nan_null
ols_with_clustered_se = p1.ols_with_clustered_se
run_ols = p1.run_ols
load_or_build_price_cache = p1.load_or_build_price_cache
compute_semivol_r = p1.compute_semivol_r
assign_era = p1.assign_era


# ---------------------------------------------------------------------------
# D1 DIAGNOSIS
# ---------------------------------------------------------------------------
def d1_diagnosis(panel: pl.DataFrame, proxy_sub: pl.DataFrame, out_lines: list, out_json: dict):
    """Report-only. Quantify what drives the 1,917-row overlap join's skew
    corr=0.199: selection-metadata availability, atm_iv agreement, DTE
    construction-invariant check, and corr(skew) conditional on the closest
    available "matched selection" proxy (atm_iv-agreement quantiles), since
    neither ledger persists atm_strike/dte for a direct match-rate computation.
    """
    log("\n=== D1 DIAGNOSIS (report-only) ===")
    out_lines.append("\n=== D1 DIAGNOSIS (report-only) ===\n")
    result = {}

    # --- 0. Selection-metadata column inventory (both ledgers) ---
    OURS_IV_LEDGER_PATH = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger.parquet")
    polygon_cols = set(panel.columns)
    ours_iv_ledger = pl.read_parquet(OURS_IV_LEDGER_PATH)
    ours_iv_cols = set(ours_iv_ledger.columns)
    proxy_cols = set(proxy_sub.columns)  # only symbol/date/opt_skew/pnl15 selected upstream; check full file too
    proxy_full = pl.read_parquet(PROXY_LEDGER)
    proxy_full_cols = set(proxy_full.columns)

    selection_meta_polygon = sorted(c for c in ["atm_strike", "dte"] if c in polygon_cols)
    selection_meta_ours_iv = sorted(c for c in ["atm_strike", "dte"] if c in ours_iv_cols)
    selection_meta_ours_proxy = sorted(c for c in ["atm_strike", "dte"] if c in proxy_full_cols)

    out_lines.append(
        "  Selection-metadata column inventory:\n"
        "    Polygon panel (iv_ledger_polygon.parquet) HAS: %s\n"
        "    Our iv_ledger.parquet HAS: %s\n"
        "    Our proxy_ledger.parquet HAS: %s\n" % (
            selection_meta_polygon or "NONE",
            selection_meta_ours_iv or "NONE",
            selection_meta_ours_proxy or "NONE",
        )
    )
    log("  Polygon HAS atm_strike/dte: %s | ours iv_ledger HAS: %s | ours proxy_ledger HAS: %s" %
        (selection_meta_polygon, selection_meta_ours_iv, selection_meta_ours_proxy))

    code_read_note = (
        "  CODE-READ FINDING (build_iv.py atm_call()/otm_iv(), lines ~34-55): our SQL SELECTs\n"
        "    DO compute strike/DTE selection transiently (ORDER BY ABS(strike-target)/close or\n"
        "    ABS(strike-target) LIMIT 1, DATEDIFF(expiration,date) BETWEEN 20 AND 45) but NEVER\n"
        "    persist atm_strike or dte to iv_ledger.parquet's output columns -- so an EXACT\n"
        "    ours-vs-Polygon strike/DTE match-rate is NOT reconstructable from stored data.\n"
        "    This absence is itself diagnostic: it means the selection-mismatch hypothesis can\n"
        "    only be tested via a downstream proxy (atm_iv agreement), not a direct strike/DTE\n"
        "    comparison. Recommend (out of scope here): persist atm_strike/dte in a future\n"
        "    build_iv.py run if a repeat of this diagnosis is ever needed.\n"
    )
    out_lines.append(code_read_note)
    log("  CODE-READ: our ledgers never persisted atm_strike/dte -- exact match-rate not computable")

    result["selection_metadata_inventory"] = {
        "polygon_has": selection_meta_polygon,
        "ours_iv_ledger_has": selection_meta_ours_iv,
        "ours_proxy_ledger_has": selection_meta_ours_proxy,
        "code_read_finding": (
            "build_iv.py computes strike/DTE selection transiently in SQL (ORDER BY ... LIMIT 1) "
            "but never persists atm_strike/dte columns to iv_ledger.parquet or proxy_ledger.parquet. "
            "Direct ours-vs-Polygon strike/DTE match rate is therefore not computable from stored "
            "data; only downstream proxies (atm_iv agreement) are available."
        ),
    }

    # --- 1. DTE construction-invariant check (Polygon side; both windows are
    #     20-45 DTE BY CONSTRUCTION per polygon_iv_ingest.py --dte-lo/--dte-hi
    #     defaults and build_iv.py's DATEDIFF BETWEEN 20 AND 45 -- so this is
    #     reporting the realized distribution within a shared design window,
    #     not an empirical cross-ledger match since ours has no stored dte). ---
    dte_arr = panel["dte"].drop_nulls().to_numpy().astype(np.float64)
    dte_stats = {
        "n": int(len(dte_arr)),
        "mean": float(np.mean(dte_arr)) if len(dte_arr) else None,
        "median": float(np.median(dte_arr)) if len(dte_arr) else None,
        "min": float(np.min(dte_arr)) if len(dte_arr) else None,
        "max": float(np.max(dte_arr)) if len(dte_arr) else None,
        "pct_in_20_45": float(np.mean((dte_arr >= 20) & (dte_arr <= 45))) if len(dte_arr) else None,
    }
    out_lines.append(
        "\n  DTE (Polygon side, full panel N=%d): mean=%.1f median=%.1f min=%.0f max=%.0f "
        "pct_in[20,45]=%.1f%%\n"
        "    NOTE: both polygon_iv_ingest.py (--dte-lo 20 --dte-hi 45) and build_iv.py\n"
        "    (DATEDIFF BETWEEN 20 AND 45) target the SAME 20-45 DTE window BY CONSTRUCTION.\n"
        "    Since our ledger stores no dte column, this is a construction-invariant check\n"
        "    (both recipes aim at the same window), not an empirical exact-match rate.\n" %
        (dte_stats["n"], dte_stats["mean"], dte_stats["median"], dte_stats["min"], dte_stats["max"],
         100.0 * dte_stats["pct_in_20_45"])
    )
    log("  DTE (Polygon): mean=%.1f median=%.1f pct_in[20,45]=%.1f%%" %
        (dte_stats["mean"], dte_stats["median"], 100.0 * dte_stats["pct_in_20_45"]))
    result["dte_polygon_side"] = dte_stats

    # --- 2. atm_iv correlation on the overlap join (the cleanest available
    #     "even matched-selection prices agree?" signal, since atm_iv is the
    #     SAME ATM-call construct on both sides -- nearest-priced-strike to
    #     spot within the shared 20-45 DTE window). ---
    # proxy_ledger doesn't carry atm_iv (only opt_skew/pnl15 from iv_ledger);
    # atm_iv agreement is checked via a join to iv_ledger.parquet directly
    # (same source rows as proxy_sub's opt_skew, since build_proxy.py joins
    # iv_ledger's skew/pnl15 onto rs_ledger unchanged -- atm_iv sits in the
    # SAME iv_ledger.parquet rows keyed by (symbol,date)).
    ours_atmiv = ours_iv_ledger.select(["symbol", "date", "atm_iv"]).rename({"atm_iv": "atm_iv_ours"})
    ours_atmiv = ours_atmiv.with_columns(
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False)
    )
    panel_atmiv = panel.select(["symbol", "date", "atm_iv"]).rename({"atm_iv": "atm_iv_polygon"})
    joined_atmiv = panel_atmiv.join(ours_atmiv, on=["symbol", "date"], how="inner")
    joined_atmiv = fill_nan_null(joined_atmiv, ["atm_iv_polygon", "atm_iv_ours"]).drop_nulls(
        subset=["atm_iv_polygon", "atm_iv_ours"]
    )
    n_atmiv = joined_atmiv.height
    out_lines.append("\n  atm_iv overlap join (Polygon panel x our iv_ledger.parquet, on symbol+date): N=%d\n" % n_atmiv)
    if n_ok(n_atmiv):
        a_pg = joined_atmiv["atm_iv_polygon"].to_numpy().astype(np.float64)
        a_us = joined_atmiv["atm_iv_ours"].to_numpy().astype(np.float64)
        atmiv_corr = float(np.corrcoef(a_pg, a_us)[0, 1])
        atmiv_mean_diff = float(np.mean(a_pg - a_us))
        atmiv_median_absdiff = float(np.median(np.abs(a_pg - a_us)))
        out_lines.append(
            "    corr(atm_iv_polygon, atm_iv_ours)=%.4f mean_diff=%.4f median(|diff|)=%.4f\n" %
            (atmiv_corr, atmiv_mean_diff, atmiv_median_absdiff)
        )
        log("  atm_iv corr=%.4f (N=%d) -- the same ATM-call construct on both sides" % (atmiv_corr, n_atmiv))
        result["atm_iv_agreement"] = {
            "n": n_atmiv, "corr": atmiv_corr, "mean_diff": atmiv_mean_diff,
            "median_abs_diff": atmiv_median_absdiff,
        }
    else:
        out_lines.append("    %s\n" % skip_str(n_atmiv))
        result["atm_iv_agreement"] = {"n": n_atmiv, "skipped": True}
        atmiv_corr = None

    data_quality_flag = (atmiv_corr is not None) and (atmiv_corr < 0.7)
    if data_quality_flag:
        flag_msg = (
            "\n  *** DATA-QUALITY FLAG ***: atm_iv agreement corr=%.4f < 0.7 -- even the ATM-call\n"
            "  construct (same nearest-to-spot strike, same 20-45 DTE window) disagrees materially\n"
            "  between sources. Per AMENDMENT 1, this is a stop-for-user-decision condition. D2 is\n"
            "  still run below (it is independent of this diagnosis), but this flag MUST headline\n"
            "  the final report.\n" % atmiv_corr
        )
        out_lines.append(flag_msg)
        log(flag_msg)
    result["data_quality_flag"] = bool(data_quality_flag)
    result["data_quality_flag_threshold"] = 0.7

    # --- 3. corr(skew) conditional on atm_iv-agreement-matched subset (the
    #     best available proxy for "matched selection" given missing
    #     strike/dte metadata: rows where the ATM construct itself agrees
    #     closely are the closest thing to "selection matched"). ---
    joined_full = panel.select(["symbol", "date", "skew", "atm_iv"]).rename(
        {"skew": "skew_polygon", "atm_iv": "atm_iv_polygon"}
    ).join(
        proxy_sub.rename({"opt_skew": "skew_ours"}), on=["symbol", "date"], how="inner"
    ).join(
        ours_atmiv, on=["symbol", "date"], how="left"
    )
    joined_full = fill_nan_null(joined_full, ["skew_polygon", "skew_ours", "atm_iv_polygon", "atm_iv_ours"])
    base_n = joined_full.drop_nulls(subset=["skew_polygon", "skew_ours"]).height
    out_lines.append(
        "\n  Base skew-non-null overlap (sanity check vs Test1's reported N=1,769 on the same\n"
        "  symbol+date join before atm_iv is required): N=%d\n" % base_n
    )
    result["base_skew_overlap_n"] = base_n

    matched = joined_full.drop_nulls(subset=["skew_polygon", "skew_ours", "atm_iv_polygon", "atm_iv_ours"])
    matched = matched.with_columns(
        (pl.col("atm_iv_polygon") - pl.col("atm_iv_ours")).abs().alias("atm_iv_absdiff")
    )
    n_matched_all = matched.height
    out_lines.append(
        "\n  Rows with BOTH skew AND atm_iv on both sides (denominator for conditional corr): N=%d\n" %
        n_matched_all
    )
    conditional_result = {"n_with_atm_iv_both_sides": n_matched_all}
    if n_ok(n_matched_all):
        # tightest-quartile ("closest-matched") subset by |atm_iv diff|
        absdiff = matched["atm_iv_absdiff"].to_numpy().astype(np.float64)
        q25 = float(np.percentile(absdiff, 25))
        tight = matched.filter(pl.col("atm_iv_absdiff") <= q25)
        n_tight = tight.height
        out_lines.append(
            "    Closest-matched quartile (atm_iv |diff| <= %.4f, the tightest 25%% of ATM-selection\n"
            "    agreement -- best available proxy for 'matched selection' given no stored\n"
            "    strike/dte): N=%d\n" % (q25, n_tight)
        )
        if n_ok(n_tight):
            sk_pg = tight["skew_polygon"].to_numpy().astype(np.float64)
            sk_us = tight["skew_ours"].to_numpy().astype(np.float64)
            corr_tight = float(np.corrcoef(sk_pg, sk_us)[0, 1])
            out_lines.append("    corr(skew) on closest-matched quartile = %.4f (vs 0.199 unconditional)\n" % corr_tight)
            log("  corr(skew | closest-matched atm_iv quartile) = %.4f  N=%d" % (corr_tight, n_tight))
            conditional_result["closest_quartile"] = {
                "n": n_tight, "atm_iv_absdiff_q25_cutoff": q25, "skew_corr": corr_tight,
            }
        else:
            out_lines.append("    %s\n" % skip_str(n_tight))
            conditional_result["closest_quartile"] = {"n": n_tight, "skipped": True}

        # loosest-quartile (worst atm_iv agreement) for contrast
        q75 = float(np.percentile(absdiff, 75))
        loose = matched.filter(pl.col("atm_iv_absdiff") >= q75)
        n_loose = loose.height
        if n_ok(n_loose):
            sk_pg_l = loose["skew_polygon"].to_numpy().astype(np.float64)
            sk_us_l = loose["skew_ours"].to_numpy().astype(np.float64)
            corr_loose = float(np.corrcoef(sk_pg_l, sk_us_l)[0, 1])
            out_lines.append(
                "    Loosest-matched quartile (atm_iv |diff| >= %.4f) for contrast: N=%d corr(skew)=%.4f\n" %
                (q75, n_loose, corr_loose)
            )
            log("  corr(skew | loosest-matched atm_iv quartile) = %.4f  N=%d (contrast)" % (corr_loose, n_loose))
            conditional_result["loosest_quartile"] = {
                "n": n_loose, "atm_iv_absdiff_q75_cutoff": q75, "skew_corr": corr_loose,
            }
        else:
            conditional_result["loosest_quartile"] = {"n": n_loose, "skipped": True}
    else:
        out_lines.append("    %s\n" % skip_str(n_matched_all))
        conditional_result["closest_quartile"] = {"n": 0, "skipped": True}
    result["conditional_skew_corr"] = conditional_result

    # --- 4. Attribution headline ---
    headline = None
    if atmiv_corr is not None:
        if atmiv_corr >= 0.85:
            headline = (
                "ATTRIBUTION: atm_iv agreement is HIGH (corr=%.4f) while skew corr is LOW (0.199) -- "
                "this attributes the mismatch to the OTM-LEG SELECTION (illiquid +-10%% strikes), not "
                "the ATM/IV-solving machinery itself. Consistent with Test 1's diagnosis note: our "
                "recipe picks the single nearest strike regardless of that day's liquidity while "
                "Polygon walks out to 6 tries requiring an actual trade that day." % atmiv_corr
            )
        elif atmiv_corr >= 0.7:
            headline = (
                "ATTRIBUTION: atm_iv agreement is MODERATE (corr=%.4f), skew corr is LOW (0.199) -- "
                "some of the mismatch is upstream of OTM-leg selection (ATM construct itself has "
                "partial disagreement), but the much larger skew gap still implicates OTM-leg "
                "selection as the dominant driver." % atmiv_corr
            )
        else:
            headline = (
                "ATTRIBUTION: atm_iv agreement is LOW (corr=%.4f) -- the disagreement is NOT confined "
                "to OTM-leg selection; even the ATM construct disagrees, implicating a broader "
                "price-level/IV-solving difference (rates, dividend assumption, bar-close timing, or "
                "underlying-close source) beyond strike-selection alone." % atmiv_corr
            )
    else:
        headline = "ATTRIBUTION: inconclusive -- atm_iv overlap too thin to compute."
    out_lines.append("\n  %s\n" % headline)
    log("  " + headline)
    result["attribution_headline"] = headline

    out_json["d1_diagnosis"] = result
    return result


# ---------------------------------------------------------------------------
# D2 MEASUREMENT-ROBUSTNESS REPLICATION
# ---------------------------------------------------------------------------
def build_discovery_panel(panel: pl.DataFrame, rs_slim: pl.DataFrame):
    """Discovery-window (2025-02-11..2026-05-15) Polygon panel + stock_r20
    (rs_ledger LEFT JOIN) + semivol_r (recomputed, house formula, ONE bulk
    MySQL price query cached to PRICE_CACHE -- shared with the era phase)."""
    disc_start_d = date.fromisoformat(DISCOVERY_START)
    disc_end_d = date.fromisoformat(DISCOVERY_END)
    disc = panel.filter(
        (pl.col("date") >= disc_start_d) & (pl.col("date") <= disc_end_d)
    )
    log("discovery panel: N=%d (%s..%s)" % (disc.height, DISCOVERY_START, DISCOVERY_END))

    joined = disc.join(rs_slim, on=["symbol", "date"], how="left")
    n_stockr20 = joined["stock_r20"].drop_nulls().len()
    log("discovery panel LEFT JOIN rs_ledger (stock_r20) -> N=%d (stock_r20 non-null=%d)" %
        (joined.height, n_stockr20))

    symbols_dates = [(s, d.isoformat()) for s, d in
                      zip(joined["symbol"].to_list(), joined["date"].to_list())]
    symbols = sorted(set(s for s, _ in symbols_dates))
    log("semivol_r recompute (discovery scope): %d symbols, %d (symbol,date) pairs" %
        (len(symbols), len(symbols_dates)))

    price_df, price_meta = load_or_build_price_cache(symbols)
    svr_map = compute_semivol_r(symbols_dates, price_df)
    svr_list = [svr_map.get((s, d), None) for s, d in symbols_dates]
    joined = joined.with_columns(pl.Series("semivol_r", svr_list, dtype=pl.Float64))
    n_svr = joined["semivol_r"].drop_nulls().len()
    log("semivol_r computed (discovery): non-null=%d / %d (%.1f%%)" %
        (n_svr, joined.height, 100.0 * n_svr / max(joined.height, 1)))

    return joined, price_meta, {"stock_r20_nonnull": n_stockr20, "semivol_r_nonnull": n_svr}


def d2_univariate(df: pl.DataFrame, scope_label: str, out_lines: list, container: dict):
    """Reuses p1's _univariate_one (identical spearman + quintile-table logic;
    not copy-pasted)."""
    p1._univariate_one(df, scope_label, out_lines, container)


def d2_replication(disc_panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== D2 MEASUREMENT-ROBUSTNESS REPLICATION (decisive) ===")
    out_lines.append("\n=== D2 MEASUREMENT-ROBUSTNESS REPLICATION (decisive) ===\n")
    out_lines.append(
        "  Question: does POLYGON-skew predict pnl15 on the DISCOVERY window (%s..%s) where\n"
        "  OUR-skew's original edge (+0.110/+0.145) was confirmed?\n"
        "  Locked bars: REPLICATES iff spearman >= +%.3f AND clustered t >= +%.2f (raw pnl15).\n" %
        (DISCOVERY_START, DISCOVERY_END, D2_SPEARMAN_BAR, D2_TCLUST_BAR)
    )
    result = {"bars": {"spearman_bar": D2_SPEARMAN_BAR, "t_clust_bar": D2_TCLUST_BAR}}

    assert_no_holdout_leak(disc_panel, "osk_val_p2_discovery")
    out_lines.append("  holdout guard PASSED (discovery panel max date <= cutoff %s)\n" % HOLDOUT_CUTOFF.isoformat())

    # --- univariate: spearman + quintiles, FULL + overall>=75 ---
    out_lines.append("\n  --- univariate ---\n")
    univ = {}
    d2_univariate(disc_panel, "discovery_full", out_lines, univ)
    sub75 = disc_panel.filter(pl.col("overall") >= 75)
    out_lines.append("\n  discovery overall>=75 subset: N_rows=%d\n" % sub75.height)
    d2_univariate(sub75, "discovery_overall>=75", out_lines, univ)
    result["univariate"] = univ

    # --- orthogonalized OLS: raw + winsorized, FULL scope ---
    out_lines.append("\n  --- orthogonalized OLS: pnl15 ~ skew + semivol_r + overall + stock_r20 ---\n")
    ols_container = {}
    p1._ols_scope(disc_panel, "discovery_full", out_lines, ols_container)
    result["ols"] = ols_container

    # --- orthogonalized OLS: 75+ subset (report-only per registration; still
    #     computed if N floor clears) ---
    ols75_container = {}
    p1._ols_scope(sub75, "discovery_overall>=75", out_lines, ols75_container)
    result["ols_75plus"] = ols75_container

    # --- apply the locked bars to the FULL discovery scope (raw pnl15) ---
    full_spear = univ.get("discovery_full", {})
    full_ols_raw = ols_container.get("discovery_full|raw", {})
    spearman_val = full_spear.get("spearman_rho") if not full_spear.get("skipped") else None
    tclust_val = full_ols_raw.get("t_clust_feature") if not full_ols_raw.get("skipped") else None
    n_spearman = full_spear.get("n")
    n_ols = full_ols_raw.get("n")

    replicates = (
        spearman_val is not None and tclust_val is not None
        and spearman_val >= D2_SPEARMAN_BAR and tclust_val >= D2_TCLUST_BAR
    )
    out_lines.append(
        "\n  *** BAR APPLICATION (discovery_full, raw pnl15) ***\n"
        "    spearman=%s (N=%s, bar >= %.3f)   clustered_t=%s (N=%s, bar >= %.2f)\n"
        "    => %s\n" % (
            ("%.4f" % spearman_val) if spearman_val is not None else "N/A", n_spearman, D2_SPEARMAN_BAR,
            ("%.3f" % tclust_val) if tclust_val is not None else "N/A", n_ols, D2_TCLUST_BAR,
            "REPLICATES" if replicates else "FAILS-TO-REPLICATE",
        )
    )
    log("  D2 BAR: spearman=%s t_clust=%s => %s" % (
        spearman_val, tclust_val, "REPLICATES" if replicates else "FAILS-TO-REPLICATE"))

    # winsorized readout alongside (not gated, per DESIGN.md "report alongside")
    full_ols_wins = ols_container.get("discovery_full|winsorized_1pct", {})
    tclust_wins = full_ols_wins.get("t_clust_feature") if not full_ols_wins.get("skipped") else None
    out_lines.append(
        "    (1%%-winsorized alongside, not gated): clustered_t=%s\n" %
        (("%.3f" % tclust_wins) if tclust_wins is not None else "N/A")
    )

    # 75+ cell readout alongside (report-only, thin N expected)
    full_ols_75 = ols75_container.get("discovery_overall>=75|raw", {})
    spear_75 = univ.get("discovery_overall>=75", {})
    out_lines.append(
        "    75+ cell (report-only): spearman=%s (N=%s)  t_clust=%s (N=%s)\n" % (
            ("%.4f" % spear_75["spearman_rho"]) if not spear_75.get("skipped") else "N/A (%s)" % skip_str(spear_75.get("n")),
            spear_75.get("n"),
            ("%.3f" % full_ols_75["t_clust_feature"]) if not full_ols_75.get("skipped") else "N/A (%s)" % skip_str(full_ols_75.get("n")),
            full_ols_75.get("n"),
        )
    )

    result["bar_application"] = {
        "scope": "discovery_full_raw_pnl15",
        "spearman": spearman_val,
        "spearman_n": n_spearman,
        "t_clust": tclust_val,
        "t_clust_n": n_ols,
        "t_clust_winsorized": tclust_wins,
        "replicates": bool(replicates),
    }

    out_json["d2_replication"] = result
    return replicates, result


# ---------------------------------------------------------------------------
# GATED TESTS 2-4 (only if D2 replicates) -- POLYGON-skew uniformly, original
# scopes (backward-OOS pooled / full pooled / eras E1-E5), original bars.
# ---------------------------------------------------------------------------
def build_full_panel_polygon(panel: pl.DataFrame, rs_slim: pl.DataFrame):
    """Same construction as p1.build_full_panel but for the standalone
    phase-2 script's own full_panel (era + split labels, stock_r20, semivol_r
    over the FULL 2022-08-01..2026-05-15 panel -- reuses the SAME price cache
    file already populated/topped-up by d2's discovery-scope call, per
    DESIGN.md 'build once, cached ... the era phase needs the same cache')."""
    return p1.build_full_panel(panel, rs_slim)


def gated_tests_2_4(full_panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== GATED TESTS 2-4 (D2 REPLICATED -- POLYGON-skew, original scopes/bars) ===")
    out_lines.append("\n=== GATED TESTS 2-4 (D2 REPLICATED -- POLYGON-skew, original scopes/bars) ===\n")

    assert_no_holdout_leak(full_panel, "osk_val_p2_full")
    out_lines.append("  holdout guard PASSED on full_panel (max date <= cutoff %s)\n" % HOLDOUT_CUTOFF.isoformat())

    n_backward = full_panel.filter(pl.col("split") == "backward_oos").height
    n_discovery = full_panel.filter(pl.col("split") == "discovery").height
    n_other = full_panel.filter(pl.col("split") == "other").height
    out_lines.append("  split counts: backward_oos=%d discovery=%d other=%d\n" % (n_backward, n_discovery, n_other))
    for name, start, end in ERAS:
        n_era = full_panel.filter(pl.col("era") == name).height
        out_lines.append("    era=%s (%s..%s): N=%d\n" % (name, start, end, n_era))

    # Reuse p1's test2/test3/test4 verbatim (same functions, same panel schema
    # -- FEATURE_COL='skew' already points at Polygon's skew column since this
    # full_panel IS the Polygon panel).
    p1.test2_univariate(full_panel, out_lines, out_json)
    p1.test3_ols(full_panel, out_lines, out_json)
    p1.test4_stability(full_panel, out_lines, out_json)

    # --- Apply ORIGINAL DESIGN.md bars to the backward-OOS decisive cell ---
    backward_ols_raw = out_json.get("test3_ols", {}).get("backward_oos_pooled|raw", {})
    backward_univ = out_json.get("test2_univariate", {}).get("backward_oos_pooled", {})
    sign_table = out_json.get("test4_stability", {}).get("sign_table", [])

    t_clust_bwd = backward_ols_raw.get("t_clust_feature") if not backward_ols_raw.get("skipped") else None
    spearman_bwd = backward_univ.get("spearman_rho") if not backward_univ.get("skipped") else None

    e1_e3_pos = sum(1 for row in sign_table if row.get("sign") == "+" and row["era"] in ("E1", "E2", "E3"))
    any_era_leq_neg005 = False
    era_leq_detail = []
    for name, _, _ in ERAS:
        era_univ = out_json.get("test2_univariate", {}).get("era_%s" % name, {})
        era_rho = era_univ.get("spearman_rho") if not era_univ.get("skipped") else None
        era_n = era_univ.get("n")
        if era_rho is not None and era_rho <= -0.05:
            any_era_leq_neg005 = True
            era_leq_detail.append({"era": name, "spearman": era_rho, "n": era_n})

    pass_bar = (
        t_clust_bwd is not None and spearman_bwd is not None
        and t_clust_bwd >= 2.5 and spearman_bwd >= 0.05
        and e1_e3_pos >= 2 and not any_era_leq_neg005
    )
    weak_bar = (
        not pass_bar and t_clust_bwd is not None and spearman_bwd is not None
        and t_clust_bwd >= 1.5 and (t_clust_bwd > 0) == (spearman_bwd > 0)
    )
    kill_bar = (not pass_bar) and (not weak_bar)

    bar_label = "PASS" if pass_bar else ("WEAK" if weak_bar else "KILL")
    out_lines.append(
        "\n  *** ORIGINAL DESIGN.md BAR APPLICATION (backward-OOS pooled decisive cell) ***\n"
        "    t_clust(skew)=%s  spearman=%s  E1-E3 positive-sign=%d/3  any era<=-0.05=%s\n"
        "    => %s\n" % (
            ("%.3f" % t_clust_bwd) if t_clust_bwd is not None else "N/A",
            ("%.4f" % spearman_bwd) if spearman_bwd is not None else "N/A",
            e1_e3_pos, any_era_leq_neg005, bar_label,
        )
    )
    log("  ORIGINAL BAR (backward-OOS): t_clust=%s spearman=%s E1-E3+=%d/3 era<=-0.05=%s => %s" %
        (t_clust_bwd, spearman_bwd, e1_e3_pos, any_era_leq_neg005, bar_label))

    out_json["gated_tests_bar_application"] = {
        "scope": "backward_oos_pooled_raw",
        "t_clust": t_clust_bwd,
        "spearman": spearman_bwd,
        "e1_e3_positive_count": e1_e3_pos,
        "any_era_leq_neg0.05": any_era_leq_neg005,
        "era_leq_neg0.05_detail": era_leq_detail,
        "bar_label": bar_label,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    log("=== osk_validation_phase2.py -- OSK AMENDMENT 1 (D1 diagnosis + D2 replication) ===")
    log("holdout cutoff (live, from strategy_config.py): %s" % HOLDOUT_CUTOFF.isoformat())

    out_lines = []
    out_json = {"meta": {}}
    out_lines.append("=== osk_validation_phase2.py results (AMENDMENT 1: D1 diagnosis + D2 replication) ===\n")
    out_lines.append("holdout cutoff: %s\n" % HOLDOUT_CUTOFF.isoformat())
    out_lines.append(
        "\nCONTEXT: Test 1 source-consistency gate (osk_validation_test.py) FAILED: skew corr=0.199\n"
        "(< 0.80 gate), pnl15 corr=0.930. AMENDMENT 1 (DESIGN.md) restructures the plan to D1\n"
        "(diagnosis, report-only) + D2 (measurement-robustness replication, decisive) before any\n"
        "further era analysis.\n"
    )

    # --- load panel (Polygon side) ---
    if not os.path.exists(PANEL_PATH):
        log("ERROR: panel not found: %s" % PANEL_PATH)
        sys.exit(1)
    panel = p1.load_panel()
    out_lines.append("\npanel: %s N=%d, symbols=%d, dates %s..%s\n" %
                      (PANEL_PATH, panel.height, panel["symbol"].n_unique(),
                       panel["date"].min(), panel["date"].max()))
    out_json["meta"]["panel_n"] = panel.height
    out_json["meta"]["panel_symbols"] = panel["symbol"].n_unique()

    assert_no_holdout_leak(panel, "osk_val_p2")
    out_lines.append("holdout guard PASSED (panel max date <= cutoff %s)\n" % HOLDOUT_CUTOFF.isoformat())
    log("holdout guard PASSED")

    proxy_sub = p1.load_proxy_opt_skew_subset()
    out_json["meta"]["proxy_opt_skew_subset_n"] = proxy_sub.height

    # =====================================================================
    # D1 DIAGNOSIS (report-only)
    # =====================================================================
    d1_result = d1_diagnosis(panel, proxy_sub, out_lines, out_json)

    # =====================================================================
    # D2 MEASUREMENT-ROBUSTNESS REPLICATION (decisive; independent of D1)
    # =====================================================================
    rs_slim = p1.load_rs_ledger_slim()

    disc_panel, disc_price_meta, disc_join_meta = build_discovery_panel(panel, rs_slim)
    out_json["meta"]["discovery_panel_n"] = disc_panel.height
    out_json["meta"]["discovery_price_query"] = disc_price_meta
    out_json["meta"]["discovery_join_meta"] = disc_join_meta

    replicates, d2_result = d2_replication(disc_panel, out_lines, out_json)

    # =====================================================================
    # GATED: Tests 2-4 with Polygon-skew uniformly, IFF D2 replicates
    # =====================================================================
    if replicates:
        out_lines.append(
            "\n=== D2 REPLICATES -- proceeding to full era/backward-OOS battery (Tests 2-4) ===\n"
        )
        log("\n=== D2 REPLICATES -- proceeding to full era/backward-OOS battery ===")

        full_panel, price_meta = build_full_panel_polygon(panel, rs_slim)
        out_lines.append(
            "\nfull panel (Polygon-skew, all dates) built: N=%d (stock_r20 non-null=%d, "
            "semivol_r non-null=%d)\n" %
            (full_panel.height, full_panel["stock_r20"].drop_nulls().len(),
             full_panel["semivol_r"].drop_nulls().len())
        )
        out_json["meta"]["full_panel_n"] = full_panel.height
        out_json["meta"]["full_panel_price_query"] = price_meta

        gated_tests_2_4(full_panel, out_lines, out_json)
        out_json["meta"]["verdict_line"] = "D2 REPLICATES -- full battery run (see gated_tests_bar_application)"
    else:
        out_lines.append(
            "\n=== D2 FAILS-TO-REPLICATE -- STOPPING. Era/backward-OOS analysis NOT run. ===\n"
            "Verdict: \"OSK measurement-robustness FLAG\" -- the edge exists only under our recipe\n"
            "of the same quantity; the L3 buy decision escalates to the user with the full D1\n"
            "diagnosis above (a third vendor cannot validate a recipe-specific edge); the honest\n"
            "next step is recipe-sensitivity analysis on OUR data, not more vendors.\n"
        )
        log("\n=== D2 FAILS-TO-REPLICATE -- STOPPING (verdict: OSK measurement-robustness FLAG) ===")
        out_json["meta"]["verdict_line"] = "OSK measurement-robustness FLAG"

    elapsed = time.time() - t_start
    out_lines.append("\n=== DONE in %.1fs ===\n" % elapsed)
    out_json["meta"]["elapsed_s"] = round(elapsed, 2)
    out_json["meta"]["d2_replicates"] = bool(replicates)

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
