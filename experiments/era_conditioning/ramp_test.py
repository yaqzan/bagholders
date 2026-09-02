"""
ramp_test.py -- Era-Conditioning Program, Phase A (DESIGN.md, locked 2026-07-08).

Adoption-ramp test: for the three strongest in-era cells (OSK skew, GEX pinning,
GEX cohort timing), compute effect size in ROLLING 6-month windows stepped
monthly across 2025-02 -> 2026-06 (in-sample only, <= holdout cutoff 2026-06-15;
leak-guarded) and read the trajectory for a RAMP-SUPPORTED vs FLAT/DECAY signal.

This is a report-only, pre-registered diagnostic. It does not gate anything and
ships nothing; it calibrates the prior for Phase C (the v75 candidate sweep).

  Cell A (OSK):     spearman(skew, pnl15) + clustered OLS beta of
                     pnl15 ~ skew + semivol_r + overall + stock_r20
                     -- iv_ledger_polygon discovery-window panel (2025-02-11..2026-05-15).
  Cell B (pinning):  clustered OLS beta of mfe15 ~ local_density + overall +
                     stock_r20 + vol_pct -- gex_features x rs_ledger panel.
  Cell C (cohort):   WEEK-clustered OLS beta of cohort_net_path ~ mkt_gex_ratio +
                     vix_close + vix_chg5 + mcclellan_oscillator + trin + breadth_ema
                     -- the spy_gex_test.py A2 daily aggregate panel.

Trend metric (pre-registered, DESIGN.md): OLS slope of |effect size| vs window
index (+ |spearman| for cell A alongside); sign of slope; count of windows with
|t_clust| >= 2; first-third vs last-third mean |effect|. RAMP-SUPPORTED iff
positive slope AND last-third windows uniformly stronger than first-third for
>= 2 of 3 cells; else FLAT/DECAY. Windows overlap (6m step 1m) -- this is a
direction read, not a significance claim on the slope itself.

READ-ONLY offline experiment. New files only under experiments/era_conditioning/
and .cache/era_conditioning/. ZERO MySQL queries -- every input is an existing
on-disk parquet cache (built by osk_validation/, gex/ experiments already run).
This script REUSES (imports, never duplicates) those experiments' panel-build
and OLS/clustered-SE machinery. HOLDOUT_DISABLE is never set.

PYTHONUTF8=1 required (ASCII-only prints; no statsmodels -- numpy OLS only).

Usage:
    python experiments/era_conditioning/ramp_test.py
"""
from __future__ import annotations

import calendar
import json
import os
import sys
import time
from datetime import date, timedelta

import numpy as np
import polars as pl
from scipy.stats import spearmanr

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# --- Reuse (do not duplicate) the existing panel-build + OLS machinery. ---
from experiments.osk_validation.osk_validation_phase2 import build_discovery_panel  # noqa: E402
from experiments.osk_validation.osk_validation_test import (  # noqa: E402
    load_panel as osk_load_panel,
    load_rs_ledger_slim as osk_load_rs_ledger_slim,
    run_ols as osk_run_ols,
    fill_nan_null as osk_fill_nan_null,
    CONTROL_COLS as CELL_A_CONTROLS,
    FEATURE_COL as CELL_A_FEATURE,
    LABEL_COL as CELL_A_LABEL,
)
from experiments.gex.gex_test import (  # noqa: E402
    run_ols_one_feature,
    CONTROL_COLS_MECH as CELL_B_CONTROLS,
)
from experiments.gex.gex_cluster_test import (  # noqa: E402
    build_panel as build_cell_b_panel,
    DEFAULT_FEATURES as CELL_B_FEATURES_PATH,
)
from experiments.gex.spy_gex_test import (  # noqa: E402
    build_a2_daily_series,
    build_a2_cohort_labels,
    build_controls as build_cell_c_controls,
    run_ols_one_series,
    CONTROL_COLS as CELL_C_CONTROLS,
)

OUT_TXT = os.path.join(_HERE, "ramp_results.txt")
OUT_JSON = os.path.join(_HERE, "ramp_results.json")

CELL_B_FEATURE = "local_density"
CELL_B_LABEL = "mfe15"
CELL_C_FEATURE = "mkt_gex_ratio"
CELL_C_LABEL = "cohort_net_path"

# Pre-registered window design (DESIGN.md Phase A): 12 start months, 6-calendar-
# month windows, stepped 1 month, clamped to the (currently-equal) holdout cutoff.
WINDOW_MONTHS = 6
WINDOW_START_MONTHS = [(2025, m) for m in range(2, 13)] + [(2026, 1)]  # 2025-02..2026-01 (12)
PHASE_A_CUTOFF = date(2026, 6, 15)  # DESIGN.md-locked cutoff; sanity-checked against live CUTOFF below

N_FLOOR_ROWS = 300   # cells A/B (per-signal-row panels)
N_FLOOR_DAYS = 100   # cell C (per-trading-day aggregate panel)
T_CLUST_TREND_BAR = 2.0


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Window construction
# ---------------------------------------------------------------------------
def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def build_windows():
    windows = []
    for idx, (y, m) in enumerate(WINDOW_START_MONTHS):
        start = date(y, m, 1)
        end_exclusive = add_months(start, WINDOW_MONTHS)
        end = end_exclusive - timedelta(days=1)
        clamped = end > PHASE_A_CUTOFF
        if clamped:
            end = PHASE_A_CUTOFF
        windows.append({"idx": idx, "start": start, "end": end, "clamped": clamped})
    return windows


def _window_slice(df: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    if df.height == 0:
        return df
    return df.filter((pl.col("date") >= start) & (pl.col("date") <= end))


# ---------------------------------------------------------------------------
# CELL A: OSK (iv_ledger_polygon discovery panel)
# ---------------------------------------------------------------------------
def build_cell_a_base():
    panel = osk_load_panel()
    rs_slim = osk_load_rs_ledger_slim()
    disc_panel, price_meta, join_meta = build_discovery_panel(panel, rs_slim)
    assert_no_holdout_leak(disc_panel, "era_ramp")
    return disc_panel, {
        "n": disc_panel.height,
        "date_min": str(disc_panel["date"].min()) if disc_panel.height else None,
        "date_max": str(disc_panel["date"].max()) if disc_panel.height else None,
        "price_query": price_meta,
        "join_meta": join_meta,
    }


def cell_a_window(disc_panel: pl.DataFrame, start: date, end: date):
    sub = _window_slice(disc_panel, start, end)
    if sub.height:
        assert_no_holdout_leak(sub, "era_ramp")
    row = {"start": start.isoformat(), "end": end.isoformat(), "n_raw": sub.height}

    # --- spearman(skew, pnl15) ---
    sp_sub = sub.select(["skew", "pnl15"])
    sp_sub = osk_fill_nan_null(sp_sub).drop_nulls()
    n_sp = sp_sub.height
    if n_sp >= N_FLOOR_ROWS:
        rho, pval = spearmanr(
            sp_sub["skew"].to_numpy().astype(np.float64),
            sp_sub["pnl15"].to_numpy().astype(np.float64),
        )
        row.update({"spearman_n": n_sp, "spearman_rho": float(rho), "spearman_p": float(pval), "spearman_skipped": False})
    else:
        row.update({"spearman_n": n_sp, "spearman_rho": None, "spearman_p": None, "spearman_skipped": True})

    # --- clustered OLS: pnl15 ~ skew + semivol_r + overall + stock_r20 ---
    res = osk_run_ols(sub, CELL_A_LABEL, CELL_A_FEATURE, CELL_A_CONTROLS)
    n_ols = res.get("n", 0)
    if (not res.get("skipped")) and n_ols >= N_FLOOR_ROWS:
        row.update({
            "ols_n": n_ols, "beta": res["beta_feature"],
            "t_plain": res["t_plain_feature"], "t_clust": res["t_clust_feature"],
            "ols_skipped": False,
        })
    else:
        row.update({"ols_n": n_ols, "beta": None, "t_plain": None, "t_clust": None, "ols_skipped": True})

    row["window_skipped"] = row["ols_skipped"]  # decisive metric (beta/t_clust) gates window admission
    return row


# ---------------------------------------------------------------------------
# CELL B: pinning (gex_features x rs_ledger)
# ---------------------------------------------------------------------------
def build_cell_b_base():
    panel = build_cell_b_panel(CELL_B_FEATURES_PATH)  # already caps date<=2026-05-15, holdout-guarded inside
    return panel, {
        "n": panel.height,
        "date_min": str(panel["date"].min()) if panel.height else None,
        "date_max": str(panel["date"].max()) if panel.height else None,
    }


def cell_b_window(panel: pl.DataFrame, start: date, end: date):
    sub = _window_slice(panel, start, end)
    if sub.height:
        assert_no_holdout_leak(sub, "era_ramp")
    row = {"start": start.isoformat(), "end": end.isoformat(), "n_raw": sub.height}

    res = run_ols_one_feature(sub, CELL_B_LABEL, CELL_B_FEATURE, CELL_B_CONTROLS)
    n = res.get("n", 0)
    if (not res.get("skipped")) and n >= N_FLOOR_ROWS:
        row.update({
            "n": n, "beta": res["beta_feature"],
            "t_plain": res["t_plain_feature"], "t_clust": res["t_clust_feature"],
            "skipped": False,
        })
    else:
        row.update({"n": n, "beta": None, "t_plain": None, "t_clust": None, "skipped": True})
    return row


# ---------------------------------------------------------------------------
# CELL C: cohort timing (spy_gex_test.py A2 panel)
# ---------------------------------------------------------------------------
def build_cell_c_base():
    a2_daily, a2_status = build_a2_daily_series()
    spy_df, controls_df, controls_meta = build_cell_c_controls()
    market_panel = spy_df.join(controls_df, on="date", how="inner")
    net_path_daily, _pnl15_daily = build_a2_cohort_labels()  # holdout-guarded inside (<=2026-05-15)

    a2_panel = pl.DataFrame()
    if a2_daily.height:
        a2_panel = a2_daily.join(market_panel, on="date", how="inner")
        if net_path_daily.height:
            a2_panel = a2_panel.join(net_path_daily.select(["date", "cohort_net_path"]), on="date", how="left")
        else:
            a2_panel = a2_panel.with_columns(pl.lit(None).cast(pl.Float64).alias("cohort_net_path"))

    meta = {
        "a2_status": a2_status,
        "n_days_raw": a2_panel.height,
        "n_cohort_net_path_notnull": a2_panel["cohort_net_path"].drop_nulls().len() if a2_panel.height else 0,
        "date_min": str(a2_panel["date"].min()) if a2_panel.height else None,
        "date_max": str(a2_panel["date"].max()) if a2_panel.height else None,
        "controls_meta": controls_meta,
    }
    return a2_panel, meta


def cell_c_window(panel: pl.DataFrame, start: date, end: date):
    sub = _window_slice(panel, start, end)
    if sub.height:
        assert_no_holdout_leak(sub, "era_ramp")
    row = {"start": start.isoformat(), "end": end.isoformat(), "n_raw": sub.height}

    res = run_ols_one_series(sub, CELL_C_LABEL, CELL_C_FEATURE, CELL_C_CONTROLS)
    n = res.get("n", 0)
    if (not res.get("skipped")) and n >= N_FLOOR_DAYS:
        row.update({
            "n": n, "beta": res["beta"],
            "t_plain": res["t_plain"], "t_clust": res["t_clust"],
            "skipped": False,
        })
    else:
        row.update({"n": n, "beta": None, "t_plain": None, "t_clust": None, "skipped": True})
    return row


# ---------------------------------------------------------------------------
# Trend read (pre-registered, DESIGN.md): shared across cells
# ---------------------------------------------------------------------------
def trend_block(window_rows: list, value_key: str, t_key: str = "t_clust"):
    """OLS slope of |value_key| vs window index (over usable windows only),
    sign, count of windows with |t_clust|>=2, first-third vs last-third mean
    |value_key| (thirds of the FIXED 12-window design, nulls excluded from the
    third means). Windows overlap by construction -- slope is a direction read,
    not a significance claim."""
    n_total = len(window_rows)
    y_all = [row.get(value_key) for row in window_rows]
    t_all = [row.get(t_key) for row in window_rows]

    xs = [i for i in range(n_total) if y_all[i] is not None]
    ys = [abs(y_all[i]) for i in xs]
    n_tclust_hi = sum(1 for i in range(n_total) if t_all[i] is not None and abs(t_all[i]) >= T_CLUST_TREND_BAR)

    slope = None
    sign = None
    if len(xs) >= 2:
        slope_val, _intercept = np.polyfit(np.array(xs, dtype=np.float64), np.array(ys, dtype=np.float64), 1)
        slope = float(slope_val)
        sign = "+" if slope > 0 else ("-" if slope < 0 else "0")

    k = n_total // 3
    first_idx = list(range(0, k))
    last_idx = list(range(n_total - k, n_total))
    first_vals = [abs(y_all[i]) for i in first_idx if y_all[i] is not None]
    last_vals = [abs(y_all[i]) for i in last_idx if y_all[i] is not None]
    first_mean = float(np.mean(first_vals)) if first_vals else None
    last_mean = float(np.mean(last_vals)) if last_vals else None
    last_gt_first = (first_mean is not None and last_mean is not None and last_mean > first_mean)

    return {
        "n_windows_total": n_total,
        "n_windows_used_for_slope": len(xs),
        "slope": slope,
        "sign": sign,
        "n_windows_tclust_geq2": n_tclust_hi,
        "first_third_n": len(first_vals),
        "first_third_mean_abs": first_mean,
        "last_third_n": len(last_vals),
        "last_third_mean_abs": last_mean,
        "last_gt_first": last_gt_first,
    }


def cell_verdict(trend: dict) -> str:
    if trend["n_windows_used_for_slope"] < 2 or trend["first_third_mean_abs"] is None or trend["last_third_mean_abs"] is None:
        return "INSUFFICIENT_DATA"
    if trend["sign"] == "+" and trend["last_gt_first"]:
        return "RAMP"
    return "FLAT_DECAY"


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
def _fmt_window_row_abc(row: dict, has_spearman: bool):
    if has_spearman:
        sp = ("N/A" if row.get("spearman_rho") is None else "%.4f" % row["spearman_rho"])
        parts = "spearman=%s (N=%s)" % (sp, row.get("spearman_n"))
    else:
        parts = ""
    beta = row.get("beta")
    if beta is None:
        ols_part = "beta=N/A (N=%s) SKIPPED" % row.get("ols_n", row.get("n"))
    else:
        ols_part = "beta=%.4f t_plain=%.3f t_clust=%.3f (N=%s)" % (
            beta, row["t_plain"], row["t_clust"], row.get("ols_n", row.get("n"))
        )
    if parts:
        return "%s | %s" % (parts, ols_part)
    return ols_part


def write_cell_report(out_lines: list, cell_name: str, feature: str, label: str, controls: list,
                       windows_meta: list, rows: list, has_spearman: bool,
                       trend_beta: dict, trend_spearman: dict, verdict: str, n_floor: int, n_floor_unit: str):
    out_lines.append("\n=== CELL %s: %s ~ %s + %s (N floor=%d %s) ===\n" %
                      (cell_name, label, feature, " + ".join(controls), n_floor, n_floor_unit))
    for wm, row in zip(windows_meta, rows):
        clamp_note = " [CLAMPED]" if wm["clamped"] else ""
        out_lines.append("  W%02d [%s..%s]%s: %s\n" %
                          (wm["idx"], wm["start"].isoformat(), wm["end"].isoformat(), clamp_note,
                           _fmt_window_row_abc(row, has_spearman)))

    out_lines.append("\n  --- TREND (beta) ---\n")
    out_lines.append(
        "  windows used=%d/%d  slope=%s sign=%s  windows |t_clust|>=2: %d/%d\n"
        "  first-third(n=%d) mean|beta|=%s  last-third(n=%d) mean|beta|=%s  last>first=%s\n" % (
            trend_beta["n_windows_used_for_slope"], trend_beta["n_windows_total"],
            ("%.6f" % trend_beta["slope"]) if trend_beta["slope"] is not None else "N/A",
            trend_beta["sign"], trend_beta["n_windows_tclust_geq2"], trend_beta["n_windows_total"],
            trend_beta["first_third_n"],
            ("%.4f" % trend_beta["first_third_mean_abs"]) if trend_beta["first_third_mean_abs"] is not None else "N/A",
            trend_beta["last_third_n"],
            ("%.4f" % trend_beta["last_third_mean_abs"]) if trend_beta["last_third_mean_abs"] is not None else "N/A",
            trend_beta["last_gt_first"],
        )
    )
    if trend_spearman is not None:
        out_lines.append("\n  --- TREND (|spearman|, cell A secondary read) ---\n")
        out_lines.append(
            "  windows used=%d/%d  slope=%s sign=%s\n"
            "  first-third(n=%d) mean|spearman|=%s  last-third(n=%d) mean|spearman|=%s  last>first=%s\n" % (
                trend_spearman["n_windows_used_for_slope"], trend_spearman["n_windows_total"],
                ("%.6f" % trend_spearman["slope"]) if trend_spearman["slope"] is not None else "N/A",
                trend_spearman["sign"],
                trend_spearman["first_third_n"],
                ("%.4f" % trend_spearman["first_third_mean_abs"]) if trend_spearman["first_third_mean_abs"] is not None else "N/A",
                trend_spearman["last_third_n"],
                ("%.4f" % trend_spearman["last_third_mean_abs"]) if trend_spearman["last_third_mean_abs"] is not None else "N/A",
                trend_spearman["last_gt_first"],
            )
        )
    out_lines.append("\n  *** CELL %s VERDICT (beta-based): %s ***\n" % (cell_name, verdict))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    log("=== ramp_test.py -- Era-Conditioning Phase A (DESIGN.md) ===")
    log("holdout cutoff (live, from strategy_config.py): %s" % HOLDOUT_CUTOFF.isoformat())
    log("Phase A pre-registered cutoff: %s" % PHASE_A_CUTOFF.isoformat())
    if HOLDOUT_CUTOFF != PHASE_A_CUTOFF:
        log("*** WARNING: live HOLDOUT_CUTOFF (%s) != Phase-A pre-registered cutoff (%s) -- "
            "using the pre-registered %s for window clamping; the leak guard still enforces the LIVE cutoff. ***" %
            (HOLDOUT_CUTOFF.isoformat(), PHASE_A_CUTOFF.isoformat(), PHASE_A_CUTOFF.isoformat()))

    windows_meta = build_windows()

    out_lines = []
    out_json = {"meta": {}}
    out_lines.append("=== ramp_test.py results (Era-Conditioning Phase A) ===\n")
    out_lines.append("holdout cutoff (live): %s\n" % HOLDOUT_CUTOFF.isoformat())
    out_lines.append("Phase A pre-registered cutoff: %s\n" % PHASE_A_CUTOFF.isoformat())
    out_lines.append("\nWINDOWS (12, 6-calendar-month, stepped 1 month):\n")
    for wm in windows_meta:
        out_lines.append("  W%02d: %s .. %s%s\n" % (
            wm["idx"], wm["start"].isoformat(), wm["end"].isoformat(),
            " [CLAMPED to Phase-A cutoff]" if wm["clamped"] else ""))
    out_json["meta"]["holdout_cutoff"] = HOLDOUT_CUTOFF.isoformat()
    out_json["meta"]["phase_a_cutoff"] = PHASE_A_CUTOFF.isoformat()
    out_json["meta"]["windows"] = [
        {"idx": wm["idx"], "start": wm["start"].isoformat(), "end": wm["end"].isoformat(), "clamped": wm["clamped"]}
        for wm in windows_meta
    ]

    # =========================================================================
    # Build base panels (once each; reused across all 12 windows)
    # =========================================================================
    log("\n--- Building Cell A base panel (OSK discovery, reuses osk_validation build_discovery_panel) ---")
    cell_a_base, cell_a_base_meta = build_cell_a_base()
    log("Cell A base panel: N=%d, %s..%s" % (cell_a_base_meta["n"], cell_a_base_meta["date_min"], cell_a_base_meta["date_max"]))
    out_lines.append("\nCell A base panel (OSK discovery): N=%d, %s..%s\n" %
                      (cell_a_base_meta["n"], cell_a_base_meta["date_min"], cell_a_base_meta["date_max"]))
    out_json["meta"]["cell_a_base"] = cell_a_base_meta

    log("\n--- Building Cell B base panel (gex_features x rs_ledger, reuses gex_cluster_test.build_panel) ---")
    cell_b_base, cell_b_base_meta = build_cell_b_base()
    log("Cell B base panel: N=%d, %s..%s" % (cell_b_base_meta["n"], cell_b_base_meta["date_min"], cell_b_base_meta["date_max"]))
    out_lines.append("\nCell B base panel (gex_features x rs_ledger): N=%d, %s..%s\n" %
                      (cell_b_base_meta["n"], cell_b_base_meta["date_min"], cell_b_base_meta["date_max"]))
    out_json["meta"]["cell_b_base"] = cell_b_base_meta

    log("\n--- Building Cell C base panel (spy_gex_test A2 aggregate, reuses build_a2_daily_series/build_a2_cohort_labels/build_controls) ---")
    cell_c_base, cell_c_base_meta = build_cell_c_base()
    log("Cell C base panel: N_days=%d (cohort_net_path notnull=%d), %s..%s" %
        (cell_c_base_meta["n_days_raw"], cell_c_base_meta["n_cohort_net_path_notnull"],
         cell_c_base_meta["date_min"], cell_c_base_meta["date_max"]))
    out_lines.append("\nCell C base panel (A2 daily aggregate): N_days=%d (cohort_net_path notnull=%d), %s..%s\n" %
                      (cell_c_base_meta["n_days_raw"], cell_c_base_meta["n_cohort_net_path_notnull"],
                       cell_c_base_meta["date_min"], cell_c_base_meta["date_max"]))
    out_json["meta"]["cell_c_base"] = cell_c_base_meta

    # =========================================================================
    # Per-window effect sizes
    # =========================================================================
    log("\n--- Computing per-window effect sizes (12 windows x 3 cells) ---")
    cell_a_rows = [cell_a_window(cell_a_base, wm["start"], wm["end"]) for wm in windows_meta]
    cell_b_rows = [cell_b_window(cell_b_base, wm["start"], wm["end"]) for wm in windows_meta]
    cell_c_rows = [cell_c_window(cell_c_base, wm["start"], wm["end"]) for wm in windows_meta]

    for wm, ra, rb, rc in zip(windows_meta, cell_a_rows, cell_b_rows, cell_c_rows):
        log("  W%02d [%s..%s]: A n=%s beta=%s t_clust=%s | B n=%s beta=%s t_clust=%s | C n=%s beta=%s t_clust=%s" % (
            wm["idx"], wm["start"].isoformat(), wm["end"].isoformat(),
            ra.get("ols_n"), ("%.4f" % ra["beta"]) if ra.get("beta") is not None else "N/A",
            ("%.3f" % ra["t_clust"]) if ra.get("t_clust") is not None else "N/A",
            rb.get("n"), ("%.4f" % rb["beta"]) if rb.get("beta") is not None else "N/A",
            ("%.3f" % rb["t_clust"]) if rb.get("t_clust") is not None else "N/A",
            rc.get("n"), ("%.4f" % rc["beta"]) if rc.get("beta") is not None else "N/A",
            ("%.3f" % rc["t_clust"]) if rc.get("t_clust") is not None else "N/A",
        ))

    # =========================================================================
    # Trend reads + verdicts
    # =========================================================================
    trend_a_beta = trend_block(cell_a_rows, "beta", "t_clust")
    trend_a_spear = trend_block(cell_a_rows, "spearman_rho", "t_clust")  # secondary read (no its own t; reuses ols t_clust window admission via same rows)
    trend_b_beta = trend_block(cell_b_rows, "beta", "t_clust")
    trend_c_beta = trend_block(cell_c_rows, "beta", "t_clust")

    verdict_a = cell_verdict(trend_a_beta)
    verdict_b = cell_verdict(trend_b_beta)
    verdict_c = cell_verdict(trend_c_beta)

    write_cell_report(out_lines, "A (OSK)", CELL_A_FEATURE, CELL_A_LABEL, CELL_A_CONTROLS,
                      windows_meta, cell_a_rows, True, trend_a_beta, trend_a_spear, verdict_a,
                      N_FLOOR_ROWS, "rows")
    write_cell_report(out_lines, "B (pinning)", CELL_B_FEATURE, CELL_B_LABEL, CELL_B_CONTROLS,
                      windows_meta, cell_b_rows, False, trend_b_beta, None, verdict_b,
                      N_FLOOR_ROWS, "rows")
    write_cell_report(out_lines, "C (cohort timing)", CELL_C_FEATURE, CELL_C_LABEL, CELL_C_CONTROLS,
                      windows_meta, cell_c_rows, False, trend_c_beta, None, verdict_c,
                      N_FLOOR_DAYS, "days")

    ramp_count = sum(1 for v in (verdict_a, verdict_b, verdict_c) if v == "RAMP")
    overall_label = "RAMP-SUPPORTED" if ramp_count >= 2 else "FLAT/DECAY"

    out_lines.append("\n\n=== OVERALL PROGRAM LABEL ===\n")
    out_lines.append("  Cell A (OSK) verdict:            %s\n" % verdict_a)
    out_lines.append("  Cell B (pinning) verdict:         %s\n" % verdict_b)
    out_lines.append("  Cell C (cohort timing) verdict:   %s\n" % verdict_c)
    out_lines.append("  Cells RAMP: %d/3 (bar: >=2/3 RAMP AND positive-slope-with-last>first per cell)\n" % ramp_count)
    out_lines.append("  => %s\n" % overall_label)
    log("\n=== OVERALL: %s (cells RAMP=%d/3: A=%s B=%s C=%s) ===" %
        (overall_label, ramp_count, verdict_a, verdict_b, verdict_c))

    out_json["cell_a"] = {
        "feature": CELL_A_FEATURE, "label": CELL_A_LABEL, "controls": CELL_A_CONTROLS,
        "n_floor": N_FLOOR_ROWS, "n_floor_unit": "rows",
        "windows": cell_a_rows, "trend_beta": trend_a_beta, "trend_spearman": trend_a_spear,
        "verdict": verdict_a,
    }
    out_json["cell_b"] = {
        "feature": CELL_B_FEATURE, "label": CELL_B_LABEL, "controls": CELL_B_CONTROLS,
        "n_floor": N_FLOOR_ROWS, "n_floor_unit": "rows",
        "windows": cell_b_rows, "trend_beta": trend_b_beta,
        "verdict": verdict_b,
    }
    out_json["cell_c"] = {
        "feature": CELL_C_FEATURE, "label": CELL_C_LABEL, "controls": CELL_C_CONTROLS,
        "n_floor": N_FLOOR_DAYS, "n_floor_unit": "days",
        "windows": cell_c_rows, "trend_beta": trend_c_beta,
        "verdict": verdict_c,
    }
    out_json["overall"] = {
        "rule": "RAMP-SUPPORTED iff positive slope AND last-third > first-third for >=2 of 3 cells; else FLAT/DECAY",
        "cell_verdicts": {"A": verdict_a, "B": verdict_b, "C": verdict_c},
        "cells_ramp_count": ramp_count,
        "label": overall_label,
    }

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
