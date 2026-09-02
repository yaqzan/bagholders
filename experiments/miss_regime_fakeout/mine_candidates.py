"""Phases 2-4 -- cohort mining (date-clustered SE) + orthogonality + dual-split
replication for candidates 1-3, over the Phase-1 ledger (ledger_v{ver}.parquet).

A4: date-clustered SE via ols_with_clustered_se, REUSED verbatim (same algorithm,
numpy cluster-sandwich OLS with G/(G-1) small-sample factor, regress outcome on a
cohort indicator, indicator's t_clust = date-clustered z) from
experiments/gex/gex_test.py::ols_with_clustered_se. Copied inline (not imported)
to avoid a fragile cross-experiment import path; algorithm is byte-identical to
the source, cited below.

A7: SPREAD_TILT literal-overlap pre-check runs FIRST, before any c2 mining --
mandatory gate. If the admission zone (68-77) substantially duplicates the rows
SPREAD_TILT already tilts (75-79 & spread>SPREAD_TILT_LO), c2's gate-qualifying
population is carved to EXCLUDE that already-covered region.

Commitment 4 (panic exclusion): every candidate's base population excludes
VIX>=28 (baked in here, not post-hoc).

Commitment 1 (clustered-SE primacy): the plain lift/z screening tables
(analyze_ledger.py style, binomial SE) are SCREENING-ONLY; the reported
gate z is always the date-clustered OLS t_clust.

Bars (DESIGN.md S5/A3): candidate advances only at z_clust>=3 with N>=500 on
BOTH interleaved-year replication halves. Era split (A5b) is report-only;
ERA-LOCAL flag if effect exists in only one era.

PYTHONUTF8=1 required (ASCII-only prints). Foreground-safe: reads one existing
parquet (<1min) + pure numpy/polars aggregation (per A8).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import polars as pl

RESULTS_DIR = os.path.join(_HERE, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
OUT_TXT = os.path.join(RESULTS_DIR, "candidates_report.txt")
OUT_JSON = os.path.join(RESULTS_DIR, "candidates_report.json")

MIN_CELL_N_SCREEN = 50   # practical default (commitment 2/3)
MIN_CELL_N_FLOOR = 30    # absolute floor (commitment 3)
GATE_Z = 3.0
GATE_N = 500
CONTROL_COLS = ["overall", "sigma_pct"]   # score level + realized-vol level (barrier_outcomes join)
VIX_PANIC = 28.0         # commitment 4: excluded from every candidate's own fire condition
SIGN_FLIP_MIN_N = 100    # commitment 6: sign flips on N<100 cells are noise

# strategy_config.py live values (A7 overlap pre-check)
SPREAD_TILT_LO = 26.9
SPREAD_TILT_HI = 31.3
SPREAD_TILT_BAND_LO, SPREAD_TILT_BAND_HI = 75, 79


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# ols_with_clustered_se -- REUSED verbatim from experiments/gex/gex_test.py
# (A4: date-clustered sandwich OLS, G/(G-1) small-sample factor). Copied
# inline rather than cross-imported; algorithm unchanged from the source.
# ---------------------------------------------------------------------------
def ols_with_clustered_se(y: np.ndarray, X: np.ndarray, groups: np.ndarray):
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

    dof = max(n - k, 1)
    sigma2 = float((resid @ resid) / dof)
    cov_plain = sigma2 * XtX_inv
    se_plain = np.sqrt(np.diag(cov_plain))

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

    with np.errstate(divide="ignore", invalid="ignore"):
        t_plain = beta_full / se_plain
        t_clust = beta_full / se_clust

    return {"beta": beta_full, "se_plain": se_plain, "t_plain": t_plain,
            "se_clust": se_clust, "t_clust": t_clust, "n": n, "g": int(G)}


def zscore(x: np.ndarray) -> np.ndarray:
    mu = np.nanmean(x)
    sd = np.nanstd(x, ddof=1)
    if not np.isfinite(sd) or sd < 1e-12:
        return np.full_like(x, np.nan)
    return (x - mu) / sd


def clustered_test(df: pl.DataFrame, treat_col: str, y_col: str, control_cols: list,
                    date_col: str = "date"):
    """Regress z(y) ~ treatment + controls, date-clustered SE. treat_col may be
    bool/int/float. Returns dict with n, n_clusters, beta, t_plain, t_clust, or
    {'n':n,'skipped':True} if N < MIN_CELL_N_FLOOR."""
    needed = [treat_col, y_col] + control_cols
    sub = df.select([date_col] + needed)
    float_like = [c for c in needed if sub[c].dtype in (pl.Float64, pl.Float32)]
    if float_like:
        sub = sub.with_columns([pl.col(c).fill_nan(None) for c in float_like])
    sub = sub.drop_nulls()
    n = sub.height
    if n < MIN_CELL_N_FLOOR:
        return {"n": n, "skipped": True}

    treat_raw = sub[treat_col].to_numpy().astype(np.float64)
    # binary/bool treatment stays 0/1 (uninformative to z-score a near-constant
    # indicator's variance the same way as a continuous feature); continuous
    # features get z-scored for a standardized beta.
    is_binary = set(np.unique(treat_raw[~np.isnan(treat_raw)]).tolist()) <= {0.0, 1.0}
    x_treat = treat_raw if is_binary else zscore(treat_raw)

    y = zscore(sub[y_col].to_numpy().astype(np.float64))
    Xcols = [x_treat]
    for c in control_cols:
        Xcols.append(zscore(sub[c].to_numpy().astype(np.float64)))
    X = np.column_stack(Xcols)

    valid = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    n_valid = int(valid.sum())
    if n_valid < MIN_CELL_N_FLOOR:
        return {"n": n_valid, "skipped": True}
    y, X = y[valid], X[valid]
    dates_valid = sub[date_col].to_numpy()[valid]

    res = ols_with_clustered_se(y, X, dates_valid)
    n_treated = int(treat_raw[valid].sum()) if is_binary else None
    return {
        "n": n_valid, "skipped": False, "n_clusters": res["g"],
        "n_treated": n_treated, "is_binary": bool(is_binary),
        "beta": float(res["beta"][1]), "t_plain": float(res["t_plain"][1]),
        "t_clust": float(res["t_clust"][1]),
        "se_plain": float(res["se_plain"][1]), "se_clust": float(res["se_clust"][1]),
    }


def lift_table(df: pl.DataFrame, feature_col: str, min_n=MIN_CELL_N_SCREEN):
    """Plain-SE lift/z screening table (analyze_ledger.py convention). SCREENING
    ONLY -- never gate-qualifying (commitment 1)."""
    import math
    baseline = float(df["is_miss"].mean())
    n_total = df.height
    g = (df.group_by(feature_col)
            .agg(pl.len().alias("n"), pl.col("is_miss").mean().alias("miss_rate"))
            .filter(pl.col("n") >= min_n))
    rows = []
    for r in g.iter_rows(named=True):
        n, mr = r["n"], r["miss_rate"]
        if baseline <= 0 or baseline >= 1:
            z, lift = 0.0, 1.0
        else:
            se = math.sqrt(baseline * (1 - baseline) / n)
            z = (mr - baseline) / se if se > 0 else 0.0
            lift = mr / baseline
        rows.append({"value": r[feature_col], "n": n, "miss_rate": mr, "lift": lift, "z": z,
                     "pct_of_cohort": n / n_total if n_total else 0.0})
    return {"baseline_miss": baseline, "n_total": n_total, "cells": rows}


def quintile_lift_table(df: pl.DataFrame, feature_col: str, min_n=MIN_CELL_N_SCREEN):
    """Quintile-bin a continuous feature and report N-labeled miss-rate per bin
    (screening-only, plain SE -- mirrors gex_test.py's quintile pattern, applied
    here to c2_spread / c3_near_gate_dist which lift_table's groupby can't bin)."""
    import math
    sub = df.select([feature_col, "is_miss"]).with_columns(pl.col(feature_col).fill_nan(None)).drop_nulls()
    if sub.height < min_n:
        return {"n": sub.height, "skipped": True}
    x = sub[feature_col].to_numpy().astype(np.float64)
    y = sub["is_miss"].to_numpy().astype(np.float64)
    baseline = float(np.mean(y))
    edges = np.nanpercentile(x, [0, 20, 40, 60, 80, 100])
    if len(np.unique(edges)) < 3:
        return {"n": sub.height, "skipped": True, "reason": "degenerate distribution"}
    bins = np.clip(np.digitize(x, edges[1:-1], right=True) + 1, 1, 5)
    rows = []
    for qi in range(1, 6):
        m = bins == qi
        n = int(m.sum())
        if n < min_n:
            rows.append({"q": qi, "n": n, "skipped": True})
            continue
        mr = float(np.mean(y[m]))
        se = math.sqrt(baseline * (1 - baseline) / n) if 0 < baseline < 1 else None
        z = (mr - baseline) / se if se else 0.0
        rows.append({"q": qi, "n": n, "range": [float(x[m].min()), float(x[m].max())],
                     "miss_rate": mr, "lift": (mr / baseline if baseline > 0 else 1.0), "z": z})
    return {"n": sub.height, "skipped": False, "baseline_miss": baseline, "quintiles": rows}


def sign_stability(df: pl.DataFrame, treat_col: str, y_col: str):
    """Per-year sign of mean(y|treat) - mean(y|~treat), N-labeled. Flip flagged
    only among N>=SIGN_FLIP_MIN_N cells (commitment 6)."""
    out = {}
    for yr in sorted(df["year"].unique().to_list()):
        sub = df.filter(pl.col("year") == yr)
        n = sub.height
        if n < MIN_CELL_N_FLOOR:
            out[str(yr)] = {"n": n, "skipped": True}
            continue
        t = sub.filter(pl.col(treat_col) == True)  # noqa: E712
        u = sub.filter(pl.col(treat_col) == False)  # noqa: E712
        if t.height < 5 or u.height < 5:
            out[str(yr)] = {"n": n, "skipped": True, "reason": "treated/untreated too thin"}
            continue
        d = float(t[y_col].mean()) - float(u[y_col].mean())
        out[str(yr)] = {"n": n, "n_treated": t.height, "diff": d,
                         "sign": "+" if d > 0 else ("-" if d < 0 else "0")}
    signs = [v["sign"] for v in out.values() if not v.get("skipped") and v["n"] >= SIGN_FLIP_MIN_N]
    flip = len(set(signs)) > 1 if len(signs) >= 2 else False
    return out, flip


def split_test(df: pl.DataFrame, treat_col: str, y_col: str, control_cols: list, split_col: str):
    """Run clustered_test independently on each value of split_col."""
    out = {}
    for val in sorted(df[split_col].unique().to_list()):
        sub = df.filter(pl.col(split_col) == val)
        out[str(val)] = clustered_test(sub, treat_col, y_col, control_cols)
    return out


# ---------------------------------------------------------------------------
# A7 -- SPREAD_TILT literal-overlap pre-check (MANDATORY before c2 mining)
# ---------------------------------------------------------------------------
def spread_tilt_overlap_check(df: pl.DataFrame, out_lines: list, out_json: dict):
    log("\n=== A7: SPREAD_TILT literal-overlap pre-check (mandatory before c2 mining) ===")
    out_lines.append("\n=== A7: SPREAD_TILT literal-overlap pre-check ===\n")

    calls = df.filter(pl.col("signal_type") == "CALL")
    tilted_mask = (
        (pl.col("overall") >= SPREAD_TILT_BAND_LO) & (pl.col("overall") <= SPREAD_TILT_BAND_HI) &
        pl.col("c2_spread").is_not_null() & (pl.col("c2_spread") > SPREAD_TILT_LO)
    )
    zone_mask = pl.col("c2_admission_zone") & pl.col("c2_spread").is_not_null()

    tilted = calls.filter(tilted_mask)
    zone = calls.filter(zone_mask)
    both = calls.filter(tilted_mask & zone_mask)

    n_tilted, n_zone, n_both = tilted.height, zone.height, both.height
    pct_tilted_in_zone = (n_both / n_tilted * 100) if n_tilted else 0.0
    pct_zone_that_is_tilted = (n_both / n_zone * 100) if n_zone else 0.0

    # score-range-only overlap (ignoring spread condition) for context
    zone_scores = calls.filter(pl.col("c2_admission_zone"))
    tilted_range = zone_scores.filter((pl.col("overall") >= SPREAD_TILT_BAND_LO) & (pl.col("overall") <= SPREAD_TILT_BAND_HI))
    pct_zone_in_tilt_scorerange = (tilted_range.height / zone_scores.height * 100) if zone_scores.height else 0.0

    lines = [
        "SPREAD_TILT fires: score in [%d,%d] AND spread > %.1f (shipped 75-79-band-only CALL haircut)\n" %
        (SPREAD_TILT_BAND_LO, SPREAD_TILT_BAND_HI, SPREAD_TILT_LO),
        "c2 admission zone: score in [%d,%d] (any spread)\n" % (68, 77),
        "N(SPREAD_TILT-tilted rows) = %d\n" % n_tilted,
        "N(c2 admission-zone rows, spread known) = %d\n" % n_zone,
        "N(intersection) = %d\n" % n_both,
        "overlap %% of SPREAD_TILT-tilted rows that ALSO fall in c2's zone = %.1f%%\n" % pct_tilted_in_zone,
        "overlap %% of c2's zone rows that are ALSO SPREAD_TILT-tilted    = %.1f%%\n" % pct_zone_that_is_tilted,
        "(context) %% of c2 zone rows whose SCORE alone (ignoring spread) falls in SPREAD_TILT's 75-79 band = %.1f%%\n" %
        pct_zone_in_tilt_scorerange,
    ]
    verdict = "CARVE-OUT APPLIED" if pct_tilted_in_zone >= 50.0 else "NO CARVE-OUT NEEDED"
    lines.append("VERDICT: %s (threshold: >=50%% of tilted rows also in zone => treat as duplication risk)\n" % verdict)
    lines.append("Gate-qualifying c2 population = admission zone MINUS the SPREAD_TILT-tilted region "
                  "(applied regardless of verdict, as the conservative default).\n")

    for ln in lines:
        log("  " + ln.rstrip())
        out_lines.append(ln)

    out_json["a7_spread_tilt_overlap"] = {
        "n_tilted": n_tilted, "n_zone": n_zone, "n_intersection": n_both,
        "pct_tilted_rows_in_zone": round(pct_tilted_in_zone, 2),
        "pct_zone_rows_that_are_tilted": round(pct_zone_that_is_tilted, 2),
        "pct_zone_rows_in_tilt_scorerange": round(pct_zone_in_tilt_scorerange, 2),
        "carve_out_verdict": verdict,   # distinct key from candidate "verdict" (ADVANCES/FAILS) --
    }                                    # do NOT rename to "verdict", the A3 kill-criterion scan
                                          # below filters on that exact key name to find ONLY
                                          # candidate rows.
    return tilted_mask


# ---------------------------------------------------------------------------
# Per-candidate Phase 2+3+4 pipeline
# ---------------------------------------------------------------------------
def run_candidate(name, df_full: pl.DataFrame, treat_col: str, control_cols: list,
                   out_lines: list, out_json: dict, y_col="exit_return",
                   extra_note: str = ""):
    log("\n=== %s: Phase 2 (clustered mining) -> Phase 3 (orthogonality) -> Phase 4 (replication) ===" % name)
    out_lines.append("\n=== %s ===\n" % name)
    if extra_note:
        out_lines.append(extra_note + "\n")
    cj = {}

    # ---- Phase 2: full-sample clustered gate test ----
    full_res = clustered_test(df_full, treat_col, y_col, control_cols)
    cj["phase2_full"] = full_res
    if full_res.get("skipped"):
        line = "  Phase 2 [FULL]: %s -- SKIPPED (N=%d < floor %d)\n" % (name, full_res["n"], MIN_CELL_N_FLOOR)
        log(line.rstrip()); out_lines.append(line)
    else:
        n_treated = full_res.get("n_treated")
        line = ("  Phase 2 [FULL]: N=%d (N_treated=%s, G=%d dates) beta=%+.4f "
                "t_plain=%+.2f t_clust=%+.2f\n" %
                (full_res["n"], n_treated, full_res["n_clusters"], full_res["beta"],
                 full_res["t_plain"], full_res["t_clust"]))
        log(line.rstrip()); out_lines.append(line)

    # screening lift table (plain SE, screening-only)
    if df_full[treat_col].dtype == pl.Boolean:
        lt = lift_table(df_full, treat_col)
        cj["phase2_screening_lift"] = lt
        for c in lt["cells"]:
            out_lines.append("    screening (plain-SE, non-gate-qualifying): value=%s N=%d miss=%.1f%% lift=%.2f z=%+.1f\n" %
                              (c["value"], c["n"], c["miss_rate"] * 100, c["lift"], c["z"]))
    else:
        qlt = quintile_lift_table(df_full, treat_col)
        cj["phase2_screening_quintiles"] = qlt
        if qlt.get("skipped"):
            out_lines.append("    screening quintiles: SKIPPED (N=%d)\n" % qlt.get("n", 0))
        else:
            for q in qlt["quintiles"]:
                if q.get("skipped"):
                    out_lines.append("    Q%d: SKIPPED (N=%d)\n" % (q["q"], q["n"]))
                else:
                    out_lines.append("    Q%d [%.2f,%.2f]: N=%d miss=%.1f%% lift=%.2f z=%+.1f (plain-SE, non-gate-qualifying)\n" %
                                      (q["q"], q["range"][0], q["range"][1], q["n"], q["miss_rate"] * 100, q["lift"], q["z"]))

    n_full = full_res.get("n", 0)
    z_full = full_res.get("t_clust", 0.0) if not full_res.get("skipped") else 0.0
    n_gate_metric = full_res.get("n_treated") if full_res.get("n_treated") is not None else n_full
    clears_phase2 = (not full_res.get("skipped")) and abs(z_full) >= GATE_Z and n_gate_metric >= GATE_N

    # ---- Phase 3: orthogonality (only if Phase 2 cleared, per DESIGN S9) ----
    if clears_phase2:
        log("  Phase 2 CLEARED bar (|z_clust|=%.2f>=%.1f, N=%d>=%d) -- running Phase 3 orthogonality" %
            (abs(z_full), GATE_Z, n_gate_metric, GATE_N))
        out_lines.append("  Phase 2 CLEARED bar -- Phase 3 orthogonality:\n")
        ortho = {}
        slices = {
            "RXDD_off_vix_lt_20": pl.col("vix") < 20,
            "MWDD_off_abs_mcc_gt_22": pl.col("mcc").abs() > 22,
            "TVDD_off_trin_extreme": (pl.col("trin") < 0.77) | (pl.col("trin") > 1.31),
        }
        all_off = None
        for label, mask in slices.items():
            sub = df_full.filter(mask)
            r = clustered_test(sub, treat_col, y_col, control_cols)
            ortho[label] = r
            all_off = mask if all_off is None else (all_off & mask)
            if r.get("skipped"):
                out_lines.append("    [%s]: SKIPPED (N=%d)\n" % (label, r.get("n", 0)))
            else:
                out_lines.append("    [%s]: N=%d t_clust=%+.2f\n" % (label, r["n"], r["t_clust"]))
        sub_all_off = df_full.filter(all_off)
        r_all_off = clustered_test(sub_all_off, treat_col, y_col, control_cols)
        ortho["ALL_3_LEVERS_OFF"] = r_all_off
        if r_all_off.get("skipped"):
            out_lines.append("    [ALL-3-LEVERS-OFF]: SKIPPED (N=%d) (note: only RXDD/MWDD/TVDD tested -- "
                              "SVR/BDIV orthogonality not computed this pass, no per-symbol semivol_r / "
                              "SPY-proximity data pulled into this ledger)\n" % r_all_off.get("n", 0))
            ortho_verdict = "INCONCLUSIVE (thin all-off slice)"
        else:
            ortho_verdict = ("SURVIVES all-3-levers-off" if abs(r_all_off["t_clust"]) >= 2.0
                              else "DISSOLVES in all-3-levers-off (re-detects a shipped lever)")
            out_lines.append("    [ALL-3-LEVERS-OFF]: N=%d t_clust=%+.2f -- %s\n" %
                              (r_all_off["n"], r_all_off["t_clust"], ortho_verdict))
        cj["phase3_orthogonality"] = ortho
        cj["phase3_verdict"] = ortho_verdict
    else:
        log("  Phase 2 did NOT clear bar (|z_clust|=%.2f, N=%d) -- Phase 3 orthogonality SKIPPED (design: only run for Phase-2 survivors)" %
            (abs(z_full), n_gate_metric))
        out_lines.append("  Phase 3 orthogonality: SKIPPED (Phase 2 did not clear z>=3/N>=500)\n")
        cj["phase3_orthogonality"] = None
        cj["phase3_verdict"] = "not run (Phase 2 not cleared)"

    # ---- Phase 4: dual-split replication (computed regardless -- cheap, and the
    # report needs real numbers for every candidate even if Phase 2 already fails) ----
    out_lines.append("  Phase 4 replication:\n")
    interleaved = {}
    for half in ["even", "odd"]:
        sub = df_full.filter(pl.col("interleave_half") == half)
        r = clustered_test(sub, treat_col, y_col, control_cols)
        interleaved[half] = r
        if r.get("skipped"):
            out_lines.append("    [interleaved=%s]: SKIPPED (N=%d)\n" % (half, r.get("n", 0)))
        else:
            out_lines.append("    [interleaved=%s]: N=%d (N_treated=%s) t_clust=%+.2f\n" %
                              (half, r["n"], r.get("n_treated"), r["t_clust"]))
    cj["phase4_interleaved"] = interleaved

    def _clears(r):
        if r is None or r.get("skipped"):
            return False
        nn = r.get("n_treated") if r.get("n_treated") is not None else r.get("n", 0)
        return abs(r.get("t_clust", 0.0)) >= GATE_Z and nn >= GATE_N

    replicated_both_halves = _clears(interleaved.get("even")) and _clears(interleaved.get("odd"))
    out_lines.append("  REPLICATION (interleaved-year, THE bar): %s\n" %
                      ("PASS (both halves z>=3,N>=500)" if replicated_both_halves else "FAIL"))
    cj["replicated_interleaved"] = replicated_both_halves

    # era split (A5b, report-only)
    era = {}
    for half in ["2016_2021", "2022_2026"]:
        sub = df_full.filter(pl.col("era_half") == half)
        r = clustered_test(sub, treat_col, y_col, control_cols)
        era[half] = r
        if r.get("skipped"):
            out_lines.append("    [era=%s]: SKIPPED (N=%d)\n" % (half, r.get("n", 0)))
        else:
            out_lines.append("    [era=%s]: N=%d t_clust=%+.2f\n" % (half, r["n"], r["t_clust"]))
    cj["phase4_era_report_only"] = era

    era_signs = []
    for v in era.values():
        if not v.get("skipped") and v.get("n", 0) >= SIGN_FLIP_MIN_N:
            era_signs.append("+" if v["beta"] > 0 else ("-" if v["beta"] < 0 else "0"))
    era_locked = len(set(era_signs)) > 1 if len(era_signs) >= 2 else False

    # per-year sign table (only meaningful for boolean treatments; for continuous
    # treatments use the OLS beta sign per year instead)
    if df_full[treat_col].dtype == pl.Boolean:
        sign_tbl, sign_flip = sign_stability(df_full, treat_col, y_col)
    else:
        sign_tbl, sign_flip = {}, False
        for yr in sorted(df_full["year"].unique().to_list()):
            sub = df_full.filter(pl.col("year") == yr)
            r = clustered_test(sub, treat_col, y_col, control_cols)
            if r.get("skipped"):
                sign_tbl[str(yr)] = {"n": r.get("n", 0), "skipped": True}
            else:
                sign_tbl[str(yr)] = {"n": r["n"], "beta": r["beta"], "sign": "+" if r["beta"] > 0 else ("-" if r["beta"] < 0 else "0")}
        signs = [v["sign"] for v in sign_tbl.values() if not v.get("skipped") and v["n"] >= SIGN_FLIP_MIN_N]
        sign_flip = len(set(signs)) > 1 if len(signs) >= 2 else False

    out_lines.append("  per-year sign table: " + json.dumps(sign_tbl, default=str) + "\n")
    out_lines.append("  sign flip across years (N>=%d cells only): %s\n" % (SIGN_FLIP_MIN_N, sign_flip))
    era_flag = "ERA-LOCAL" if (era_locked or sign_flip) else "era-stable"
    out_lines.append("  ERA-ROBUSTNESS flag: %s\n" % era_flag)
    cj["phase4_sign_table"] = sign_tbl
    cj["phase4_sign_flip"] = sign_flip
    cj["era_robustness_flag"] = era_flag

    verdict = "ADVANCES" if (clears_phase2 and replicated_both_halves and
                              cj.get("phase3_verdict", "").startswith("SURVIVES")) else "FAILS"
    out_lines.append("  >>> %s VERDICT: %s\n" % (name, verdict))
    log("  >>> %s VERDICT: %s" % (name, verdict))
    cj["verdict"] = verdict
    out_json[name] = cj
    return verdict


def main():
    t0 = time.time()
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    ledger_path = os.path.join(RESULTS_DIR, f"ledger_v{av.id}.parquet")
    if not os.path.exists(ledger_path):
        log("ERROR: ledger not found at %s -- run build_ledger.py (Phase 1) first" % ledger_path)
        sys.exit(1)

    df = pl.read_parquet(ledger_path)
    log("=== Phases 2-4: mine_candidates.py -- loaded %s (%d rows) ===" % (ledger_path, df.height))

    out_lines = ["=== miss_regime_fakeout Phases 2-4 report (v%d) ===\n" % av.id,
                 "ledger: %s (%d rows)\n" % (ledger_path, df.height),
                 "GATE bar: |z_clust|>=%.1f AND N>=%d, replicated on BOTH interleaved-year halves\n" % (GATE_Z, GATE_N)]
    out_json = {"meta": {"version": av.id, "ledger_rows": df.height, "gate_z": GATE_Z, "gate_n": GATE_N}}

    # commitment 4: panic exclusion baked into every candidate's base population
    df_nonpanic = df.filter((pl.col("vix").is_null()) | (pl.col("vix") < VIX_PANIC))
    log("non-panic (VIX<%.0f) population: %d / %d rows" % (VIX_PANIC, df_nonpanic.height, df.height))
    out_lines.append("non-panic (VIX<%.0f, commitment 4) population: %d / %d rows\n" % (VIX_PANIC, df_nonpanic.height, df.height))

    # ---- A7 mandatory pre-check ----
    calls_nonpanic = df_nonpanic.filter(pl.col("signal_type") == "CALL")
    tilted_mask = spread_tilt_overlap_check(df_nonpanic, out_lines, out_json)

    # =========================================================================
    # Candidate 1: reversal-class volume-blend transition guard (calls, primary)
    # =========================================================================
    c1_pop = calls_nonpanic
    run_candidate(
        "candidate_1_reversal_volume_guard_CALLS", c1_pop, "c1_is_reversal", CONTROL_COLS,
        out_lines, out_json,
        extra_note="population: CALLS (overall>=68), non-panic. Treatment=c1_is_reversal "
                    "(volume_signal in {ABSORPTION,CLIMAX} AND magnitude>0 -- the EXACT code "
                    "gate condition for the blend model; literal ratio_50-distance-to-threshold "
                    "is NOT persisted historically, see build_ledger.py docstring).",
    )
    # secondary: puts
    puts_nonpanic = df_nonpanic.filter(pl.col("signal_type") == "PUT")
    run_candidate(
        "candidate_1_reversal_volume_guard_PUTS_secondary", puts_nonpanic, "c1_is_reversal", CONTROL_COLS,
        out_lines, out_json, extra_note="SECONDARY check (design's own live evidence was calls-only).",
    )
    # secondary: continuous magnitude within reversal-only subset
    c1_rev_only = c1_pop.filter(pl.col("c1_is_reversal"))
    r_mag = clustered_test(c1_rev_only, "c1_reversal_mag", "exit_return", CONTROL_COLS)
    out_json["candidate_1_continuous_magnitude_secondary"] = r_mag
    if r_mag.get("skipped"):
        out_lines.append("\ncandidate_1 continuous-magnitude secondary (within reversal-only): SKIPPED (N=%d)\n" % r_mag.get("n", 0))
    else:
        out_lines.append("\ncandidate_1 continuous-magnitude secondary (within reversal-only): N=%d t_clust=%+.2f\n" %
                          (r_mag["n"], r_mag["t_clust"]))

    # =========================================================================
    # Candidate 2: admission-boundary component-disagreement guard (calls only)
    # A7 carve-out: gate-qualifying population EXCLUDES the SPREAD_TILT-tilted region.
    # =========================================================================
    c2_zone_full = calls_nonpanic.filter(pl.col("c2_admission_zone") & pl.col("c2_spread").is_not_null())
    c2_carved = c2_zone_full.filter(~(
        (pl.col("overall") >= SPREAD_TILT_BAND_LO) & (pl.col("overall") <= SPREAD_TILT_BAND_HI) &
        (pl.col("c2_spread") > SPREAD_TILT_LO)
    ))
    log("\nc2 admission zone: full N=%d, carved (SPREAD_TILT region excluded) N=%d" %
        (c2_zone_full.height, c2_carved.height))
    out_lines.append("\nc2 admission zone: full N=%d, carved (SPREAD_TILT-tilted region excluded per A7) N=%d\n" %
                      (c2_zone_full.height, c2_carved.height))

    run_candidate(
        "candidate_2_admission_boundary_spread_CARVED", c2_carved, "c2_spread", CONTROL_COLS,
        out_lines, out_json,
        extra_note="population: CALLS, admission zone [68,77], non-panic, SPREAD_TILT-tilted "
                    "region (75-79 & spread>26.9) EXCLUDED per A7 carve-out. GATE-QUALIFYING run.",
    )
    # transparency: uncaved full-zone version (NOT gate-qualifying if it just re-detects SPREAD_TILT)
    run_candidate(
        "candidate_2_admission_boundary_spread_FULLZONE_fyi", c2_zone_full, "c2_spread", CONTROL_COLS,
        out_lines, out_json,
        extra_note="FYI ONLY (not gate-qualifying) -- full zone WITHOUT the A7 carve-out, for comparison.",
    )

    # =========================================================================
    # Candidate 3: regime-multiplier reapplication boundary-crossing guard (calls)
    # =========================================================================
    c3_pop = calls_nonpanic.filter(pl.col("c3_near_gate_dist").is_not_null())
    c3_pop = c3_pop.with_columns((pl.col("c3_near_gate_dist") <= 2).alias("c3_near_gate_flag"))
    run_candidate(
        "candidate_3_regime_boundary_crossing_CALLS", c3_pop, "c3_near_gate_flag", CONTROL_COLS,
        out_lines, out_json,
        extra_note="population: CALLS, non-panic, pre_regime known. Treatment=c3_near_gate_flag "
                    "(min(|pre_regime-70|,|pre_regime-75|)<=2).",
    )
    r_dist = clustered_test(c3_pop, "c3_near_gate_dist", "exit_return", CONTROL_COLS)
    out_json["candidate_3_continuous_distance_secondary"] = r_dist
    if r_dist.get("skipped"):
        out_lines.append("\ncandidate_3 continuous near_gate_dist secondary: SKIPPED (N=%d)\n" % r_dist.get("n", 0))
    else:
        out_lines.append("\ncandidate_3 continuous near_gate_dist secondary: N=%d t_clust=%+.2f "
                          "(NOTE: sign convention -- positive beta on RAW distance means FURTHER "
                          "from gate = higher exit_return, i.e. near-gate is BAD if beta>0)\n" %
                          (r_dist["n"], r_dist["t_clust"]))
    # actual-crossing flags (likely thin)
    for cross_col in ["c3_cross_70", "c3_cross_75"]:
        r = clustered_test(c3_pop, cross_col, "exit_return", CONTROL_COLS)
        out_json["candidate_3_%s_secondary" % cross_col] = r
        if r.get("skipped"):
            out_lines.append("candidate_3 %s secondary: SKIPPED (N=%d)\n" % (cross_col, r.get("n", 0)))
        else:
            out_lines.append("candidate_3 %s secondary: N=%d (N_treated=%s) t_clust=%+.2f\n" %
                              (cross_col, r["n"], r.get("n_treated"), r["t_clust"]))

    # =========================================================================
    # A3 whole-pass kill criterion
    # =========================================================================
    # A3 governs candidates 1-3 AS PRE-REGISTERED (their PRIMARY population/treatment) --
    # secondary/FYI sub-tests (PUTS_secondary, FULLZONE_fyi, continuous-magnitude,
    # cross_70/75) are diagnostic context and must NOT independently trigger "advances"
    # for the whole-pass kill check.
    PRIMARY_CANDIDATE_KEYS = [
        "candidate_1_reversal_volume_guard_CALLS",
        "candidate_2_admission_boundary_spread_CARVED",
        "candidate_3_regime_boundary_crossing_CALLS",
    ]
    all_verdicts = {k: v.get("verdict") for k, v in out_json.items()
                     if isinstance(v, dict) and "verdict" in v and k.startswith("candidate_")}
    verdicts = {k: v for k, v in all_verdicts.items() if k in PRIMARY_CANDIDATE_KEYS}
    any_advance = any(v == "ADVANCES" for v in verdicts.values())
    kill_line = ("\n=== A3 WHOLE-PASS KILL CRITERION ===\n"
                 "candidates 1-3 PRIMARY verdicts: %s\n"
                 "(secondary/FYI sub-test verdicts, diagnostic only, NOT part of the A3 check: %s)\n"
                 "%s\n" % (verdicts,
                           {k: v for k, v in all_verdicts.items() if k not in PRIMARY_CANDIDATE_KEYS},
                           "AT LEAST ONE ADVANCES -- pass does NOT close null; positives stop at evidence memo "
                           "per A3 (FABLE arranges adversarial verification before anything is called real)."
                           if any_advance else
                           "NONE cleared z>=3/N>=500 on BOTH replication halves -- residual-fakeout "
                           "supply-quality axis CLOSES NULL per A3 (no same-pass feature extensions, no re-cuts)."))
    log(kill_line)
    out_lines.append(kill_line)
    out_json["meta"]["whole_pass_kill_check"] = {
        "verdicts": verdicts, "any_advance": any_advance,
        "axis_status": "OPEN (evidence memo pending)" if any_advance else "CLOSED NULL",
    }

    elapsed = time.time() - t0
    out_lines.append("\n=== DONE in %.1fs ===\n" % elapsed)
    out_json["meta"]["elapsed_s"] = round(elapsed, 1)

    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.writelines(out_lines)
    with open(OUT_JSON, "w", encoding="ascii") as f:
        json.dump(out_json, f, indent=2, default=str)
    log("\nwrote %s" % OUT_TXT)
    log("wrote %s" % OUT_JSON)
    log("=== DONE in %.1fs ===" % elapsed)


if __name__ == "__main__":
    main()
