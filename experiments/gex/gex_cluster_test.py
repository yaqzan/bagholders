"""
gex_cluster_test.py -- Track B of the GEX cluster experiment (DESIGN_CLUSTER.md).

Per-name conditional cluster-escape test: does spot ESCAPING the dominant gamma
cluster (esc_above), with more runway to the next cluster (gap_room_up), or in
a low-friction pocket (local_density), predict FASTER/LARGER favorable moves,
net of momentum (stock_r20/stock_r60/overall/vol_pct)?

t_up ENCODING FINDING (pre-req code-read of component_reweight/build_ledger.py,
verified against the actual rs_ledger.parquet schema):
  rs_ledger.t_up is NOT a scalar. It is a List(Int64) of length 43, one entry
  per UP_GRID threshold (np.round(np.arange(0.40, 2.501, 0.05), 2) -- 0.40..2.50
  sigma in 0.05 steps; build_ledger.py:44). t_up[i] = first calendar day the
  cumulative-max FAVORABLE excursion (high vs entry close, sigma-normalized by
  60d realized daily vol) reached UP_GRID[i]; 999 = never reached within
  MAXW=40 calendar days (build_ledger.py:8,140-141). t_dn is the same shape
  against DN_GRID (93 entries, 0.40..5.00) for adverse excursions.
  This script canonically reads UP_GRID index 12 == sigma 1.00 (verified:
  UP_GRID[12] = 1.00) as "the" t_up column for the primary/secondary labels
  below -- a clean, economically legible "reached a 1-sigma favorable move"
  threshold, the same order of magnitude as mfe15 typically resolves within.
  PRIMARY label: reached_up1_15 = binary LPM, 1 if t_up[12] <= 15 (calendar
  days) else 0 (999 sentinel safely fails <=15, no special-casing needed).
  SECONDARY label: t_up_1sigma = the raw t_up[12] value (999 kept as censored
  "slow/never" for spearman rank -- higher t_up = slower = worse, so rho sign
  is flipped in reporting relative to reached_up1_15 sign, noted inline).
  mfe15 secondary (favorable/speed), mae15 risk-side -- both already scalar
  in rs_ledger, unchanged.

READ-ONLY offline experiment. New files only under experiments/gex/ and
.cache/experiment_data/. Never touches dealer_gex.py/gex_test.py/scoring/
core/simulator/api/strategy_config, no env-gates, HOLDOUT_DISABLE never set.
Reuses gex_test.py's OLS+clustered-SE machinery, coverage-map loader, and
holdout guard by import (not copy) so both harnesses stay in lockstep.

Usage:
    python experiments/gex/gex_cluster_test.py [--features PATH]

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
from scipy.stats import spearmanr, pearsonr

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# Reuse gex_test.py's machinery by import -- never copy/fork this logic.
from experiments.gex.gex_test import (  # noqa: E402
    n_ok, skip_str, zscore, quintile_edges, quintile_assign,
    ols_with_clustered_se, run_ols_one_feature, _load_coverage_map, _thirds_split,
    MIN_CELL_N,
)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
DEFAULT_FEATURES = os.path.join(_ROOT, ".cache", "experiment_data", "gex_features.parquet")
SMOKE_FEATURES = os.path.join(_ROOT, ".cache", "experiment_data", "gex_features_smoke.parquet")
RS_LEDGER = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
IV_LEDGER_EXT = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger_ext.parquet")
PROXY_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "proxy_ledger.parquet")

OUT_TXT = os.path.join(_HERE, "gex_cluster_results.txt")
OUT_JSON = os.path.join(_HERE, "gex_cluster_results.json")

PANEL_A_DATE_CAP = "2026-05-15"  # rs_ledger ends here; DESIGN_CLUSTER panel spec

# UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2) (build_ledger.py:44) -- index 12 == 1.00 sigma.
UP1_SIGMA_IDX = 12
UP1_SIGMA_VALUE = 1.00
REACHED_WINDOW_DAYS = 15  # "reached-within-window" horizon, matches mfe15/mae15 horizon

PRIMARY_FEATURES = ["esc_above", "gap_room_up", "local_density"]
CONTROL_COLS = ["stock_r20", "stock_r60", "overall", "vol_pct"]
LABELS = ["reached_up1_15", "mfe15", "mae15"]  # primary label first
SECONDARY_LABEL = "t_up_1sigma"  # rank/spearman secondary, not in decisive OLS bar

EVENT_COHORT_MIN_N = 1000  # DESIGN_CLUSTER bar: event-cohort N >= 1,000


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Panel build: features (36-col, or smoke 36-col) INNER JOIN rs_ledger
# ---------------------------------------------------------------------------
def build_panel(features_path: str) -> pl.DataFrame:
    log("\n--- Building Panel (features x rs_ledger INNER JOIN on symbol,date) ---")
    if not os.path.exists(RS_LEDGER):
        log("rs_ledger.parquet not found at %s -- panel SKIPPED" % RS_LEDGER)
        return pl.DataFrame()

    feats = pl.read_parquet(features_path)
    if feats.height and feats["date"].dtype != pl.Date:
        feats = feats.with_columns(pl.col("date").cast(pl.Date))
    log("features: N=%d rows, %d columns (path=%s)" % (feats.height, len(feats.columns), features_path))

    rs = pl.read_parquet(RS_LEDGER)
    rs = rs.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))

    # Derive t_up_1sigma (scalar) and reached_up1_15 (binary LPM) from the List(Int64)
    # t_up column BEFORE selecting -- these are the only two derived columns needed.
    rs = rs.with_columns(
        pl.col("t_up").list.get(UP1_SIGMA_IDX).alias("t_up_1sigma")
    )
    rs = rs.with_columns(
        (pl.col("t_up_1sigma") <= REACHED_WINDOW_DAYS).cast(pl.Int64).alias("reached_up1_15")
    )

    keep_rs = ["symbol", "date", "overall", "vol_pct", "mfe15", "mae15",
               "t_up_1sigma", "reached_up1_15", "stock_r20", "stock_r60"]
    keep_rs = [c for c in keep_rs if c in rs.columns]
    rs_slim = rs.select(keep_rs)

    panel = feats.join(rs_slim, on=["symbol", "date"], how="inner")
    log("Panel: features N=%d, rs_ledger N=%d, inner-join N=%d" % (feats.height, rs.height, panel.height))

    # DESIGN_CLUSTER: dates <= 2026-05-15
    if panel.height:
        panel = panel.filter(pl.col("date") <= pl.lit(PANEL_A_DATE_CAP).str.strptime(pl.Date, "%Y-%m-%d"))
        log("Panel after date cap <= %s: N=%d" % (PANEL_A_DATE_CAP, panel.height))

    if panel.height:
        assert_no_holdout_leak(panel, "gex_cluster")
        log("Panel: assert_no_holdout_leak PASSED (max date <= cutoff %s)" % HOLDOUT_CUTOFF.isoformat())

    return panel


# ---------------------------------------------------------------------------
# Panel-B ortho join (Phase 4 redundancy vs opt_skew/semivol_r) -- reuse
# gex_test.py's proxy-arm assembly if cheap; else skip with a note.
# ---------------------------------------------------------------------------
def build_ortho_join(panel: pl.DataFrame):
    """Join panel (already features x rs_ledger) against proxy_ledger's
    opt_skew/semivol_r on (symbol, date) -- cheap in-memory join, no MySQL,
    no new price fetch (unlike gex_test.py's ext-arm which needs a bulk price
    query for semivol_r recompute on ext rows -- skipped here, proxy-arm only,
    per DESIGN_CLUSTER Phase 4 "reuse if cheap; else skip with note")."""
    log("\n--- Building ortho join (proxy_ledger opt_skew/semivol_r, in-memory only) ---")
    if not os.path.exists(PROXY_LEDGER):
        log("proxy_ledger.parquet not found -- ortho join SKIPPED (Phase 4 redundancy vs opt_skew/semivol_r will note this)")
        return pl.DataFrame(), "proxy_ledger.parquet not found"
    if panel.height == 0:
        log("panel empty -- ortho join SKIPPED")
        return pl.DataFrame(), "panel empty"

    proxy_raw = pl.read_parquet(PROXY_LEDGER)
    proxy_raw = proxy_raw.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    proxy = proxy_raw.filter(pl.col("opt_skew").is_not_null())
    proxy = proxy.select(["symbol", "date", "opt_skew", "semivol_r"])
    log("proxy arm: opt_skew NOT NULL -> N=%d" % proxy.height)

    ortho = panel.join(proxy, on=["symbol", "date"], how="inner")
    log("ortho join (panel x proxy_ledger) -> N=%d" % ortho.height)
    if ortho.height:
        assert_no_holdout_leak(ortho, "gex_cluster_ortho")
        log("ortho join: assert_no_holdout_leak PASSED")
    return ortho, None


# ---------------------------------------------------------------------------
# Phase 1: EVENT COHORTS
# ---------------------------------------------------------------------------
def _cohort_label_stats(df: pl.DataFrame, label: str):
    if label not in df.columns or df.height == 0:
        return {"n": 0, "skipped": True}
    sub = df.select([label])
    if sub[label].dtype in (pl.Float64, pl.Float32):
        sub = sub.with_columns(pl.col(label).fill_nan(None))
    sub = sub.drop_nulls()
    n = sub.height
    if not n_ok(n):
        return {"n": n, "skipped": True}
    x = sub[label].to_numpy().astype(np.float64)
    return {"n": n, "skipped": False, "mean": float(np.mean(x)), "median": float(np.median(x))}


def phase1_event_cohorts(panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== PHASE 1: EVENT COHORTS ===")
    out_lines.append("\n=== PHASE 1: EVENT COHORTS ===\n")
    out_json["phase1"] = {}

    if panel.height == 0 or "esc_above" not in panel.columns:
        out_lines.append("  panel empty or esc_above missing -- Phase 1 SKIPPED entirely\n")
        out_json["phase1"]["skipped"] = True
        return

    # --- esc_above 0 vs 1, full panel + 75+ subset ---
    for subset_name, sub_df in [("FULL", panel), ("overall>=75", panel.filter(pl.col("overall") >= 75) if "overall" in panel.columns else pl.DataFrame())]:
        out_lines.append("\n  --- esc_above cohorts (%s) ---\n" % subset_name)
        out_json["phase1"].setdefault(subset_name, {})
        for esc_val in [0, 1]:
            if sub_df.height == 0:
                cohort = pl.DataFrame()
            else:
                cohort = sub_df.filter(pl.col("esc_above").fill_nan(None) == float(esc_val))
            n_total = cohort.height
            out_lines.append("    esc_above=%d: N=%d\n" % (esc_val, n_total))
            cohort_json = {"n": n_total}
            if n_total < EVENT_COHORT_MIN_N:
                out_lines.append("      (event-cohort N < %d bar -- reported for reference, not bar-qualifying)\n" % EVENT_COHORT_MIN_N)
                cohort_json["meets_event_cohort_bar"] = False
            else:
                cohort_json["meets_event_cohort_bar"] = True
            for label in LABELS + [SECONDARY_LABEL]:
                stat = _cohort_label_stats(cohort, label)
                cohort_json[label] = stat
                if stat.get("skipped"):
                    out_lines.append("      %s: %s\n" % (label, skip_str(stat.get("n", 0))))
                else:
                    out_lines.append("      %s: N=%d mean=%.4f median=%.4f\n" %
                                      (label, stat["n"], stat["mean"], stat["median"]))
            out_json["phase1"][subset_name]["esc_above=%d" % esc_val] = cohort_json

    # --- spot-in-gap vs in-cluster, if derivable from esc flags + gap features ---
    # "in-gap" = esc_above==1 (escaped upward, now sitting in the gap toward the
    # next cluster) OR esc_below==1 (escaped downward). "in-cluster" = neither
    # escape flag set (spot still inside the dominant cluster's span).
    out_lines.append("\n  --- spot-in-gap (esc_above==1 OR esc_below==1) vs in-cluster (neither) ---\n")
    if "esc_below" in panel.columns:
        esc_any = (pl.col("esc_above").fill_nan(None) == 1.0) | (pl.col("esc_below").fill_nan(None) == 1.0)
        in_gap = panel.filter(esc_any)
        # in_cluster = both flags explicitly 0 (excludes null-flag rows from either bucket)
        in_cluster = panel.filter(
            (pl.col("esc_above").fill_nan(None) == 0.0) & (pl.col("esc_below").fill_nan(None) == 0.0)
        )
        for name, cohort in [("in_gap", in_gap), ("in_cluster", in_cluster)]:
            n_total = cohort.height
            out_lines.append("    %s: N=%d\n" % (name, n_total))
            cohort_json = {"n": n_total, "meets_event_cohort_bar": n_total >= EVENT_COHORT_MIN_N}
            for label in LABELS:
                stat = _cohort_label_stats(cohort, label)
                cohort_json[label] = stat
                if stat.get("skipped"):
                    out_lines.append("      %s: %s\n" % (label, skip_str(stat.get("n", 0))))
                else:
                    out_lines.append("      %s: N=%d mean=%.4f median=%.4f\n" %
                                      (label, stat["n"], stat["mean"], stat["median"]))
            out_json["phase1"].setdefault("gap_vs_cluster", {})[name] = cohort_json
    else:
        out_lines.append("    esc_below not present -- gap-vs-cluster split SKIPPED\n")
        out_json["phase1"]["gap_vs_cluster"] = {"skipped": True, "reason": "esc_below missing"}

    # --- gap_room_up quartiles WITHIN esc_above==1 (fast-track claim) ---
    out_lines.append("\n  --- gap_room_up quartiles WITHIN esc_above==1 (fast-track: more runway => faster/larger move) ---\n")
    if "gap_room_up" in panel.columns:
        escaped = panel.filter(pl.col("esc_above").fill_nan(None) == 1.0)
        sub = escaped.select(["gap_room_up"] + [c for c in LABELS if c in escaped.columns])
        sub = sub.with_columns([pl.col(c).fill_nan(None) for c in sub.columns
                                 if sub[c].dtype in (pl.Float64, pl.Float32)]).drop_nulls(subset=["gap_room_up"])
        n_escaped = sub.height
        out_lines.append("    esc_above==1 with non-null gap_room_up: N=%d\n" % n_escaped)
        out_json["phase1"]["gap_room_up_quartiles"] = {"n_escaped": n_escaped}
        if not n_ok(n_escaped):
            out_lines.append("    quartiles: %s\n" % skip_str(n_escaped))
            out_json["phase1"]["gap_room_up_quartiles"]["skipped"] = True
        else:
            x = sub["gap_room_up"].to_numpy().astype(np.float64)
            qs = np.nanpercentile(x, [0, 25, 50, 75, 100])
            if len(np.unique(qs)) < 3:
                out_lines.append("    quartiles: SKIPPED (degenerate distribution)\n")
                out_json["phase1"]["gap_room_up_quartiles"]["skipped"] = True
            else:
                qbins = np.clip(np.digitize(x, qs[1:-1], right=True) + 1, 1, 4)
                q_json = {}
                for qi in range(1, 5):
                    m = qbins == qi
                    nq = int(m.sum())
                    row = {"n": nq}
                    if nq < MIN_CELL_N:
                        out_lines.append("      Q%d: %s\n" % (qi, skip_str(nq)))
                        row["skipped"] = True
                    else:
                        row["skipped"] = False
                        line = "      Q%d: N=%d" % (qi, nq)
                        for label in [c for c in LABELS if c in sub.columns]:
                            yv = sub[label].to_numpy().astype(np.float64)[m]
                            row[label] = {"mean": float(np.mean(yv)), "median": float(np.median(yv))}
                            line += " %s_mean=%.4f" % (label, row[label]["mean"])
                        out_lines.append(line + "\n")
                    q_json["Q%d" % qi] = row
                out_json["phase1"]["gap_room_up_quartiles"]["skipped"] = False
                out_json["phase1"]["gap_room_up_quartiles"]["quartiles"] = q_json
    else:
        out_lines.append("    gap_room_up not present -- quartile split SKIPPED\n")
        out_json["phase1"]["gap_room_up_quartiles"] = {"skipped": True, "reason": "gap_room_up missing"}


# ---------------------------------------------------------------------------
# Phase 2: DECISIVE OLS
# ---------------------------------------------------------------------------
def phase2_decisive_ols(panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== PHASE 2: DECISIVE OLS ===")
    out_lines.append("\n=== PHASE 2: DECISIVE OLS (bar: date-clustered |t|>=3, all controls) ===\n")
    out_json["phase2"] = {"full": {}, "subset75": {}, "interaction": {}}

    def report(res, feature, label, tag, container):
        key = "%s|%s" % (feature, label)
        if res is None or res.get("skipped"):
            n = res.get("n", 0) if res else 0
            out_lines.append("  [%s] %s ~ %s: %s\n" % (tag, label, feature, skip_str(n)))
            container[key] = {"n": n, "skipped": True}
            return
        out_lines.append(
            "  [%s] %s ~ %s + controls: N=%d (G=%d dates) beta=%.4f t_plain=%.3f t_clust=%.3f\n" %
            (tag, label, feature, res["n"], res["n_clusters"], res["beta_feature"],
             res["t_plain_feature"], res["t_clust_feature"])
        )
        container[key] = res

    if panel.height == 0:
        out_lines.append("  panel empty -- Phase 2 SKIPPED entirely\n")
        out_json["phase2"]["skipped"] = True
        return

    # --- full panel ---
    out_lines.append("\n  --- FULL panel: label ~ feature + stock_r20 + stock_r60 + overall + vol_pct ---\n")
    for feature in PRIMARY_FEATURES:
        for label in LABELS:
            res = run_ols_one_feature(panel, label, feature, CONTROL_COLS)
            report(res, feature, label, "FULL", out_json["phase2"]["full"])

    # --- 75+ subset ---
    if "overall" in panel.columns:
        sub75 = panel.filter(pl.col("overall") >= 75)
        out_lines.append("\n  --- overall>=75 subset: label ~ feature + stock_r20 + stock_r60 + overall + vol_pct ---\n")
        for feature in PRIMARY_FEATURES:
            for label in LABELS:
                res = run_ols_one_feature(sub75, label, feature, CONTROL_COLS)
                report(res, feature, label, "75+", out_json["phase2"]["subset75"])
    else:
        out_lines.append("\n  overall column missing -- 75+ subset SKIPPED entirely\n")
        out_json["phase2"]["subset75"] = {"skipped": True}

    # --- interaction cohort regression: within esc_above==1 only, label ~ gap_room_up + controls ---
    out_lines.append("\n  --- PUREST fast-track test: within esc_above==1, label ~ gap_room_up + controls ---\n")
    if "esc_above" in panel.columns:
        escaped = panel.filter(pl.col("esc_above").fill_nan(None) == 1.0)
        out_lines.append("    esc_above==1 subset: N=%d\n" % escaped.height)
        out_json["phase2"]["interaction"]["n_escaped_subset"] = escaped.height
        for label in LABELS:
            res = run_ols_one_feature(escaped, label, "gap_room_up", CONTROL_COLS)
            report(res, "gap_room_up", label, "esc_above==1 ONLY", out_json["phase2"]["interaction"])
    else:
        out_lines.append("    esc_above not present -- interaction test SKIPPED\n")
        out_json["phase2"]["interaction"] = {"skipped": True, "reason": "esc_above missing"}


# ---------------------------------------------------------------------------
# Phase 3: STABILITY (date thirds, pooled AND constant-coverage subset)
# ---------------------------------------------------------------------------
def _stability_one_cluster(df: pl.DataFrame, label_col: str, control_cols: list, subset_label: str,
                            out_lines: list, out_json_container: dict):
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
        out_lines.append("    [%s] %s ~ %s:\n" % (subset_label, label_col, feature))
        for cl in cell_lines:
            out_lines.append(cl + "\n")
        non_null_signs = [s for s in signs if s is not None]
        flip = len(set(non_null_signs)) > 1 if len(non_null_signs) >= 2 else False
        out_lines.append("        sign sequence=%s flip=%s\n" % (signs, flip))
        result_by_feature[feature] = {"signs": signs, "flip": flip}
    out_json_container[subset_label] = result_by_feature


def phase3_stability(panel: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== PHASE 3: SUB-PERIOD STABILITY ===")
    out_lines.append("\n=== PHASE 3: SUB-PERIOD STABILITY ===\n")
    out_json["phase3"] = {"reached_up1_15": {}, "coverage_map_status": None}

    label_col = "reached_up1_15"  # primary label per DESIGN_CLUSTER bar

    out_lines.append("\n  --- %s, pooled thirds ---\n" % label_col)
    _stability_one_cluster(panel, label_col, CONTROL_COLS, "pooled", out_lines, out_json["phase3"]["reached_up1_15"])

    constant_symbols, skip_reason = _load_coverage_map()
    out_json["phase3"]["coverage_map_status"] = "loaded" if constant_symbols is not None else ("SKIPPED: %s" % skip_reason)
    if constant_symbols is None:
        out_lines.append("\n  --- %s, constant-coverage-restricted thirds: SKIPPED (%s) ---\n" % (label_col, skip_reason))
    else:
        out_lines.append("\n  --- %s, constant-coverage-restricted thirds (%d constant symbols) ---\n" %
                          (label_col, len(constant_symbols)))
        if panel.height and "symbol" in panel.columns:
            restricted = panel.filter(pl.col("symbol").is_in(list(constant_symbols)))
        else:
            restricted = pl.DataFrame()
        _stability_one_cluster(restricted, label_col, CONTROL_COLS, "constant_coverage", out_lines, out_json["phase3"]["reached_up1_15"])


# ---------------------------------------------------------------------------
# Phase 4: REDUNDANCY
# ---------------------------------------------------------------------------
def phase4_redundancy(panel: pl.DataFrame, ortho: pl.DataFrame, ortho_skip_reason, out_lines: list, out_json: dict):
    log("\n=== PHASE 4: REDUNDANCY ===")
    out_lines.append("\n=== PHASE 4: REDUNDANCY ===\n")
    out_json["phase4"] = {}

    # --- vs raw momentum (stock_r20/stock_r60), from panel itself ---
    mom_cols = ["stock_r20", "stock_r60"]
    present_mom = [c for c in PRIMARY_FEATURES + mom_cols if c in panel.columns]
    missing_mom = [c for c in PRIMARY_FEATURES + mom_cols if c not in panel.columns]
    out_lines.append("\n  --- primary features vs stock_r20/stock_r60 (is escape just re-badged momentum?) ---\n")
    if missing_mom:
        out_lines.append("  missing columns: %s\n" % missing_mom)
    if panel.height == 0 or len(present_mom) < 2:
        out_lines.append("  panel empty or insufficient columns -- SKIPPED\n")
        out_json["phase4"]["vs_momentum"] = {"skipped": True}
    else:
        sub = panel.select(present_mom)
        sub = sub.with_columns([pl.col(c).fill_nan(None) for c in sub.columns
                                 if sub[c].dtype in (pl.Float64, pl.Float32)]).drop_nulls()
        n = sub.height
        if not n_ok(n):
            out_lines.append("  correlation: %s\n" % skip_str(n))
            out_json["phase4"]["vs_momentum"] = {"skipped": True, "n": n}
        else:
            arrs = {c: sub[c].to_numpy().astype(np.float64) for c in present_mom}
            pear, spear = {}, {}
            out_lines.append("  N=%d (complete rows across %s)\n" % (n, present_mom))
            header = "         " + " ".join("%12s" % c for c in present_mom)
            out_lines.append("  Pearson matrix:\n  " + header + "\n")
            for c1 in present_mom:
                row_vals = []
                for c2 in present_mom:
                    r, _ = pearsonr(arrs[c1], arrs[c2])
                    row_vals.append(r)
                    pear["%s|%s" % (c1, c2)] = float(r)
                out_lines.append("  %8s " % c1 + " ".join("%12.3f" % v for v in row_vals) + "\n")
            out_lines.append("\n  Spearman matrix:\n  " + header + "\n")
            for c1 in present_mom:
                row_vals = []
                for c2 in present_mom:
                    r, _ = spearmanr(arrs[c1], arrs[c2])
                    row_vals.append(r)
                    spear["%s|%s" % (c1, c2)] = float(r)
                out_lines.append("  %8s " % c1 + " ".join("%12.3f" % v for v in row_vals) + "\n")
            out_json["phase4"]["vs_momentum"] = {"n": n, "columns": present_mom, "pearson": pear, "spearman": spear, "skipped": False}

    # --- vs opt_skew/semivol_r, ortho-panel join ---
    out_lines.append("\n  --- primary features vs opt_skew/semivol_r (ortho-panel join) ---\n")
    if ortho_skip_reason is not None:
        out_lines.append("  ortho join SKIPPED (%s) -- vs opt_skew/semivol_r check SKIPPED with note\n" % ortho_skip_reason)
        out_json["phase4"]["vs_ortho"] = {"skipped": True, "reason": ortho_skip_reason}
    else:
        ortho_cols = ["opt_skew", "semivol_r"]
        present_ortho = [c for c in PRIMARY_FEATURES + ortho_cols if c in ortho.columns]
        missing_ortho = [c for c in PRIMARY_FEATURES + ortho_cols if c not in ortho.columns]
        if missing_ortho:
            out_lines.append("  missing columns: %s\n" % missing_ortho)
        if ortho.height == 0 or len(present_ortho) < 2:
            out_lines.append("  ortho panel empty or insufficient columns -- SKIPPED\n")
            out_json["phase4"]["vs_ortho"] = {"skipped": True}
        else:
            sub = ortho.select(present_ortho)
            sub = sub.with_columns([pl.col(c).fill_nan(None) for c in sub.columns
                                     if sub[c].dtype in (pl.Float64, pl.Float32)]).drop_nulls()
            n = sub.height
            if not n_ok(n):
                out_lines.append("  correlation: %s\n" % skip_str(n))
                out_json["phase4"]["vs_ortho"] = {"skipped": True, "n": n}
            else:
                arrs = {c: sub[c].to_numpy().astype(np.float64) for c in present_ortho}
                pear, spear = {}, {}
                out_lines.append("  N=%d (complete rows across %s)\n" % (n, present_ortho))
                header = "         " + " ".join("%12s" % c for c in present_ortho)
                out_lines.append("  Pearson matrix:\n  " + header + "\n")
                for c1 in present_ortho:
                    row_vals = []
                    for c2 in present_ortho:
                        r, _ = pearsonr(arrs[c1], arrs[c2])
                        row_vals.append(r)
                        pear["%s|%s" % (c1, c2)] = float(r)
                    out_lines.append("  %8s " % c1 + " ".join("%12.3f" % v for v in row_vals) + "\n")
                out_lines.append("\n  Spearman matrix:\n  " + header + "\n")
                for c1 in present_ortho:
                    row_vals = []
                    for c2 in present_ortho:
                        r, _ = spearmanr(arrs[c1], arrs[c2])
                        row_vals.append(r)
                        spear["%s|%s" % (c1, c2)] = float(r)
                    out_lines.append("  %8s " % c1 + " ".join("%12.3f" % v for v in row_vals) + "\n")
                out_json["phase4"]["vs_ortho"] = {"n": n, "columns": present_ortho, "pearson": pear, "spearman": spear, "skipped": False}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="GEX Track B: cluster-escape conditional test")
    parser.add_argument("--features", type=str, default=DEFAULT_FEATURES, help="path to features parquet")
    args = parser.parse_args()

    t_start = time.time()
    log("=== GEX gex_cluster_test.py -- Track B ===")
    log("features path: %s" % args.features)
    log("holdout cutoff (live, from strategy_config.py): %s" % HOLDOUT_CUTOFF.isoformat())
    log("t_up encoding: List(Int64) len=43 over UP_GRID 0.40..2.50 step 0.05; "
        "index %d == sigma %.2f used for reached_up1_15 / t_up_1sigma" % (UP1_SIGMA_IDX, UP1_SIGMA_VALUE))

    if not os.path.exists(args.features):
        log("ERROR: features file not found: %s" % args.features)
        sys.exit(1)

    out_lines = []
    out_json = {"meta": {
        "t_up_encoding": "List(Int64) len=43 over UP_GRID=np.round(np.arange(0.40,2.501,0.05),2) "
                         "(build_ledger.py:44); index %d == sigma %.2f" % (UP1_SIGMA_IDX, UP1_SIGMA_VALUE),
        "primary_label": "reached_up1_15 = 1 if t_up[%d] <= %d else 0 (999 sentinel safely fails)" %
                          (UP1_SIGMA_IDX, REACHED_WINDOW_DAYS),
    }}
    out_lines.append("=== GEX gex_cluster_test.py results (Track B) ===\n")
    out_lines.append("features path: %s\n" % args.features)
    out_lines.append("holdout cutoff: %s\n" % HOLDOUT_CUTOFF.isoformat())
    out_lines.append("\nt_up ENCODING FINDING:\n")
    out_lines.append("  rs_ledger.t_up is List(Int64) length 43, one entry per UP_GRID threshold\n")
    out_lines.append("  (np.round(np.arange(0.40, 2.501, 0.05), 2), build_ledger.py:44). t_dn is\n")
    out_lines.append("  List(Int64) length 93 over DN_GRID (0.40..5.00). 999 = never reached within\n")
    out_lines.append("  MAXW=40 calendar days. This script uses UP_GRID index %d (sigma=%.2f) as the\n" %
                      (UP1_SIGMA_IDX, UP1_SIGMA_VALUE))
    out_lines.append("  canonical t_up scalar. PRIMARY label reached_up1_15 = 1 if t_up[%d] <= %d else 0.\n" %
                      (UP1_SIGMA_IDX, REACHED_WINDOW_DAYS))
    out_lines.append("  SECONDARY label t_up_1sigma = raw t_up[%d] (999=censored, for rank/spearman).\n" % UP1_SIGMA_IDX)

    # --- Panel ---
    panel = build_panel(args.features)
    out_lines.append("\nPanel (features x rs_ledger) N=%d\n" % panel.height)
    out_json["meta"]["panel_n"] = panel.height
    if panel.height:
        out_json["meta"]["panel_date_min"] = str(panel["date"].min())
        out_json["meta"]["panel_date_max"] = str(panel["date"].max())
        n_esc_above = int(panel["esc_above"].fill_nan(None).sum()) if "esc_above" in panel.columns else None
        out_lines.append("  esc_above==1 count: %s / %d\n" % (n_esc_above, panel.height))
        out_json["meta"]["n_esc_above_1"] = n_esc_above

    # --- Ortho join (Phase 4 vs opt_skew/semivol_r) ---
    ortho, ortho_skip_reason = build_ortho_join(panel)
    out_lines.append("Ortho join (panel x proxy_ledger) N=%d%s\n" %
                      (ortho.height, (" (SKIPPED: %s)" % ortho_skip_reason) if ortho_skip_reason else ""))
    out_json["meta"]["ortho_n"] = ortho.height
    out_json["meta"]["ortho_skip_reason"] = ortho_skip_reason

    # --- Phase 1-4 ---
    phase1_event_cohorts(panel, out_lines, out_json)
    phase2_decisive_ols(panel, out_lines, out_json)
    phase3_stability(panel, out_lines, out_json)
    phase4_redundancy(panel, ortho, ortho_skip_reason, out_lines, out_json)

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
