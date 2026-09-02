"""REFINEMENT ROUND -- step 3: re-apply the pre-registered bars to the
R1-refined premium arms (ledger_v1_refined.parquet, built by
rescore_refined.py from the FROZEN refit_report.json coefficients).

Sibling script to compute_metrics.py (EXECUTE step 3 offers either
"compute_metrics.py extended (or a sibling script)"; a sibling was chosen
so the ORIGINAL pertrade_results.{json,txt} stay untouched as the
pre-refinement record). Reuses compute_metrics.py's generic primitives
(clustered_paired / ols_with_clustered_se / MIN_CELL_N / skip_str / n_ok)
by direct import -- NOT reimplemented -- but writes its own thin M1/M2
block functions pointed at the new refined-arm columns, since
compute_metrics.py's own m2_block/m3_block are hardwired to the original
ARM_NAMES module constant (rvconst/rvgamma/f2const/f2gamma) and monkey-
patching shared module state for a two-arm addition was judged riskier
than a small, faithful re-declaration using the exact same formulas.

Scope (VERDICT.md REFINEMENT ROUND):
  M1 -- refined premium vs RV vs (for context) the original F2, overall +
        RV terciles (ledger's OWN rv_tercile bucketing column, t1=2.1153
        t2=3.7677 per ledger_v1_meta.json -- NOT the fit-table-frozen
        cutoffs used internally by the refined FORMULA itself; those are
        two deliberately distinct things, see refit_report.txt NOTE),
        liquid-primary deciding, all-inclusive robustness-only.
  M2 -- d10 (unchanged scope, "overall" deciding) + d15 RE-SCOPED (R2):
        ex-earnings is now the PRIMARY/deciding d15 read; earnings-window
        trades reported separately as a watch line (P1.4 scope, not fixed
        by this round).
  M3 -- UNCHANGED this round (carried forward verbatim from
        results/pertrade_results.json; NOT recomputed -- the first-touch
        walk needs the daily forward path the ledger doesn't persist at
        daily granularity; VERDICT.md's own re-application scope marks M3
        unchanged).
  R3 -- harness-parity STATEMENT only (no both-ways rescore -- see
        rationale in the printed section and the final report; the arms
        did not omit an earnings vega_ratio, they used a disclosed
        real-empirical substitute for production's sampled one, already
        applied identically to ALL arms including the refined ones by
        rescore_refined.py's vega_ratio_for()).

Same clustering (date-clustered OLS sandwich SE, gex_test.py's
ols_with_clustered_se, reused verbatim), same N-label/SKIP<30 discipline,
same liquidity stratification (liquid_ge5 primary / all-inclusive
robustness) as compute_metrics.py.

Output: results/refinement_results.{json,txt}. Numbers only -- no
pass/fail language (FABLE judges).
"""
from __future__ import annotations

import os
import sys
import json
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import numpy as np      # noqa: E402
import polars as pl     # noqa: E402

# Reused verbatim -- NOT reimplemented (compute_metrics.py has no heavy
# module-level side effects; importing it does not touch MySQL or run main()).
from compute_metrics import (          # noqa: E402
    clustered_paired, skip_str, n_ok, MIN_CELL_N, fmt_clust,
)
from experiments._holdout import assert_no_holdout_leak, CUTOFF  # noqa: E402

REFINED_LEDGER_PATH = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1_refined.parquet")
REFINED_META_PATH = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1_refined_meta.json")
ORIG_RESULTS_JSON = os.path.join(_HERE, "results", "pertrade_results.json")
REFIT_REPORT_JSON = os.path.join(_HERE, "refit_report.json")
OUT_JSON = os.path.join(_HERE, "results", "refinement_results.json")
OUT_TXT = os.path.join(_HERE, "results", "refinement_results.txt")

lines = []


def log(msg=""):
    print(msg, flush=True)
    lines.append(str(msg))


# ---------------------------------------------------------------------------
# M1 -- premium level (refined vs rv, + original F2 for context)
# ---------------------------------------------------------------------------
def m1_refined_block(df, tag):
    sub = df.filter(
        pl.col("real_premium_pct").is_not_null() & (pl.col("real_premium_pct") > 0)
        & pl.col("premium_rv_pct").is_not_null() & pl.col("premium_f2refined_pct").is_not_null()
    )
    n = sub.height
    if not n_ok(n):
        return {"tag": tag, "n": n, "skipped": True}
    real = sub["real_premium_pct"].to_numpy().astype(float)
    rv_p = sub["premium_rv_pct"].to_numpy().astype(float)
    f2o_p = sub["premium_f2_pct"].to_numpy().astype(float)          # original F2 (context)
    f2r_p = sub["premium_f2refined_pct"].to_numpy().astype(float)   # refined F2 (the R1 candidate)
    dates = sub["date"].to_numpy()
    err_rv = np.abs(rv_p - real) / real
    err_f2o = np.abs(f2o_p - real) / real
    err_f2r = np.abs(f2r_p - real) / real
    clust_refined_vs_rv = clustered_paired(err_f2r - err_rv, dates)
    clust_refined_vs_orig = clustered_paired(err_f2r - err_f2o, dates)
    return {
        "tag": tag, "n": n, "skipped": False,
        "median_err_rv": float(np.median(err_rv)),
        "median_err_f2_orig": float(np.median(err_f2o)),
        "median_err_f2_refined": float(np.median(err_f2r)),
        "mean_err_rv": float(np.mean(err_rv)),
        "mean_err_f2_orig": float(np.mean(err_f2o)),
        "mean_err_f2_refined": float(np.mean(err_f2r)),
        "refined_beats_rv_median": bool(np.median(err_f2r) < np.median(err_rv)),
        "refined_beats_orig_median": bool(np.median(err_f2r) < np.median(err_f2o)),
        "paired_diff_refined_vs_rv_clust": clust_refined_vs_rv,
        "paired_diff_refined_vs_orig_clust": clust_refined_vs_orig,
    }


def render_m1_refined(m1, tag):
    b = m1.get(tag)
    if b is None or b.get("skipped"):
        log(f"  [{tag}] {skip_str(b.get('n') if b else None)}")
        return
    log(f"  [{tag}] N={b['n']}  median|err| rv={b['median_err_rv']:.4f} "
        f"f2_orig={b['median_err_f2_orig']:.4f} f2_refined={b['median_err_f2_refined']:.4f}  "
        f"(refined<rv: {b['refined_beats_rv_median']}  refined<orig_f2: {b['refined_beats_orig_median']})")
    log(f"      paired-diff(refined-rv) clust: {fmt_clust(b['paired_diff_refined_vs_rv_clust'])}")
    log(f"      paired-diff(refined-origF2) clust: {fmt_clust(b['paired_diff_refined_vs_orig_clust'])}")


# ---------------------------------------------------------------------------
# M2 -- P&L fidelity at a checkpoint, refined arms vs rvconst (+ orig f2gamma context)
# ---------------------------------------------------------------------------
def m2_refined_block(df, checkpoint, tag):
    real_col = f"real_pnl_d{checkpoint}"
    base = df.filter(pl.col(real_col).is_not_null())
    n_base = base.height
    result = {"tag": tag, "checkpoint": checkpoint, "n_base": n_base, "arms": {}}
    if not n_ok(n_base):
        result["skipped"] = True
        return result
    result["skipped"] = False

    arm_cols = {
        "rvconst": f"pred_pnl_d{checkpoint}_rvconst",
        "f2gamma_orig": f"pred_pnl_d{checkpoint}_f2gamma",
        "f2const_orig": f"pred_pnl_d{checkpoint}_f2const",
        "f2gammarefined": f"pred_pnl_d{checkpoint}_f2gammarefined",
        "f2constrefined": f"pred_pnl_d{checkpoint}_f2constrefined",
    }
    for arm, col in arm_cols.items():
        arm_sub = base.filter(pl.col(col).is_not_null())
        n = arm_sub.height
        if not n_ok(n):
            result["arms"][arm] = {"n": n, "skipped": True}
            continue
        pred = arm_sub[col].to_numpy().astype(float)
        real = arm_sub[real_col].to_numpy().astype(float)
        ae = np.abs(pred - real)
        result["arms"][arm] = {"n": n, "mae": float(np.mean(ae)), "bias": float(np.mean(pred - real))}

    pf2r_col, prv_col = f"pred_pnl_d{checkpoint}_f2gammarefined", f"pred_pnl_d{checkpoint}_rvconst"
    pair_sub = base.filter(pl.col(pf2r_col).is_not_null() & pl.col(prv_col).is_not_null())
    n_pair = pair_sub.height
    if n_ok(n_pair):
        real = pair_sub[real_col].to_numpy().astype(float)
        pf2r = pair_sub[pf2r_col].to_numpy().astype(float)
        prv = pair_sub[prv_col].to_numpy().astype(float)
        ae_f2r = np.abs(pf2r - real)
        ae_rv = np.abs(prv - real)
        dates = pair_sub["date"].to_numpy()
        clust = clustered_paired(ae_f2r - ae_rv, dates)
        clust["f2gammarefined_beats_rvconst"] = bool(clust.get("mean_diff", 0) < 0) if not clust.get("skipped") else None
        result["pair_f2gammarefined_vs_rvconst"] = clust
    else:
        result["pair_f2gammarefined_vs_rvconst"] = {"skipped": True, "n": n_pair}

    # Secondary: refined vs the ORIGINAL f2gamma (does the refit itself help vs the pre-refit candidate?)
    pf2o_col = f"pred_pnl_d{checkpoint}_f2gamma"
    pair_sub2 = base.filter(pl.col(pf2r_col).is_not_null() & pl.col(pf2o_col).is_not_null())
    n_pair2 = pair_sub2.height
    if n_ok(n_pair2):
        real = pair_sub2[real_col].to_numpy().astype(float)
        pf2r = pair_sub2[pf2r_col].to_numpy().astype(float)
        pf2o = pair_sub2[pf2o_col].to_numpy().astype(float)
        dates = pair_sub2["date"].to_numpy()
        clust2 = clustered_paired(np.abs(pf2r - real) - np.abs(pf2o - real), dates)
        clust2["f2gammarefined_beats_f2gamma_orig"] = bool(clust2.get("mean_diff", 0) < 0) if not clust2.get("skipped") else None
        result["pair_f2gammarefined_vs_f2gamma_orig"] = clust2
    else:
        result["pair_f2gammarefined_vs_f2gamma_orig"] = {"skipped": True, "n": n_pair2}
    return result


def render_m2_refined(m2, tag):
    b = m2.get(tag)
    if b is None or b.get("skipped"):
        log(f"  [{tag}] {skip_str(b.get('n_base') if b else None)}")
        return
    parts = []
    for arm in ("rvconst", "f2gamma_orig", "f2const_orig", "f2gammarefined", "f2constrefined"):
        a = b["arms"].get(arm, {})
        if a.get("skipped"):
            parts.append(f"{arm}={skip_str(a.get('n'))}")
        else:
            parts.append(f"{arm}={a['mae']:.4f}(N={a['n']})")
    log(f"  [{tag}] d{b['checkpoint']} N_base={b['n_base']}  MAE: " + "  ".join(parts))
    log(f"      pair(f2gammarefined-rvconst) MAE-diff clust: {fmt_clust(b['pair_f2gammarefined_vs_rvconst'])}")
    log(f"      pair(f2gammarefined-f2gamma_orig) MAE-diff clust: {fmt_clust(b['pair_f2gammarefined_vs_f2gamma_orig'])}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def cuts_for(stratum_df, stratum_name):
    blocks = {"overall": stratum_df}
    for tercile in ("T1_low", "T2_mid", "T3_high"):
        blocks[f"rv_{tercile}"] = stratum_df.filter(pl.col("rv_tercile") == tercile)
    for band in ("75+", "70-74"):
        blocks[f"band_{band}"] = stratum_df.filter(pl.col("band") == band)
    for era in ("2025", "2026H1"):
        blocks[f"era_{era}"] = stratum_df.filter(pl.col("era") == era)
    for flag, label in ((True, "earnings_yes"), (False, "earnings_no")):
        blocks[label] = stratum_df.filter(pl.col("earnings_span_flag") == flag)
    return {f"{stratum_name}|{k}": v for k, v in blocks.items()}


def main():
    t0 = time.time()
    log("=== iv_engine_pertrade REFINEMENT ROUND -- step 3: re-applied bars (M1/M2 refined, M3 carried forward, R3 statement) ===")
    log(f"refined ledger: {REFINED_LEDGER_PATH}")

    df = pl.read_parquet(REFINED_LEDGER_PATH)
    with open(REFINED_META_PATH, "r", encoding="ascii") as f:
        rmeta = json.load(f)
    log(f"ledger rows: {df.height}  cols: {len(df.columns)}")
    log(f"frozen refit form: {rmeta['frozen_form']}  coefs: {rmeta['frozen_coefs']}")
    log(f"tercile cutoffs (frozen, model-internal): {rmeta['tercile_cutoffs']}")

    df_insample = df.filter(~pl.col("is_shadow_oos"))
    df_shadow = df.filter(pl.col("is_shadow_oos"))
    assert_no_holdout_leak(df_insample, context="iv_engine_pertrade REFINEMENT ROUND M1/M2 (in-sample slice)")
    log(f"holdout guard: PASSED (cutoff={CUTOFF.isoformat()}); in-sample N={df_insample.height}  shadow_oos(watch-only) N={df_shadow.height}")

    n_liquid = df_insample.filter(pl.col("liquid_ge5") == True).height   # noqa: E712
    n_all = df_insample.height
    log(f"liquid_ge5 (PRIMARY stratum): N={n_liquid}   all-inclusive (robustness): N={n_all}")

    liquid_df = df_insample.filter(pl.col("liquid_ge5") == True)   # noqa: E712
    all_cut_sets = {}
    all_cut_sets.update(cuts_for(liquid_df, "liquid"))
    all_cut_sets.update(cuts_for(df_insample, "all_inclusive"))

    m1_res, m2d10_res, m2d15_res = {}, {}, {}
    for tag, sub in all_cut_sets.items():
        m1_res[tag] = m1_refined_block(sub, tag)
        m2d10_res[tag] = m2_refined_block(sub, 10, tag)
        m2d15_res[tag] = m2_refined_block(sub, 15, tag)

    # ---- M1 ----
    log("\n=== M1 REFINED (premium level: median |model-real|/real; rv vs original-F2 vs refined-F2) ===")
    for tag in sorted(m1_res.keys()):
        if tag.startswith("liquid|"):
            render_m1_refined(m1_res, tag)
    log("\n  -- all-inclusive (robustness / context only; lastPrice-staleness caveat applies) --")
    for tag in sorted(m1_res.keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_"):
            render_m1_refined(m1_res, tag)

    # ---- M2 d10 (unchanged scope: "overall" deciding) ----
    log("\n=== M2 REFINED (P&L fidelity: MAE at d10; unchanged scope, overall deciding) ===")
    for tag in sorted(m2d10_res.keys()):
        if tag.startswith("liquid|"):
            render_m2_refined(m2d10_res, tag)
    log("\n  -- all-inclusive (robustness) --")
    for tag in sorted(m2d10_res.keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_"):
            render_m2_refined(m2d10_res, tag)

    # ---- M2 d15 (R2 RE-SCOPED: ex-earnings is the deciding cell; earnings-window = watch line) ----
    log("\n=== M2 REFINED (P&L fidelity: MAE at d15; R2 RE-SCOPED -- 'earnings_no' cell is now the DECIDING read) ===")
    log("  -- DECIDING (liquid, ex-earnings-window) --")
    render_m2_refined(m2d15_res, "liquid|earnings_no")
    log("  -- WATCH LINE (liquid, earnings-window trades -- P1.4 scope, NOT fixed by this round) --")
    render_m2_refined(m2d15_res, "liquid|earnings_yes")
    log("  -- context: liquid|overall (pooled, pre-registration's original un-scoped cell) --")
    render_m2_refined(m2d15_res, "liquid|overall")
    log("  -- other liquid cuts (context) --")
    for tag in sorted(m2d15_res.keys()):
        if tag.startswith("liquid|") and tag not in ("liquid|earnings_no", "liquid|earnings_yes", "liquid|overall"):
            render_m2_refined(m2d15_res, tag)
    log("\n  -- all-inclusive (robustness) --")
    for tag in sorted(m2d15_res.keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_") \
           or tag in ("all_inclusive|earnings_no", "all_inclusive|earnings_yes"):
            render_m2_refined(m2d15_res, tag)

    # ---- M3 -- UNCHANGED, carried forward verbatim ----
    log("\n=== M3 (decision fidelity: TP-hit agreement, SL-hit FNR) -- UNCHANGED this round, carried forward verbatim ===")
    with open(ORIG_RESULTS_JSON, "r", encoding="ascii") as f:
        orig = json.load(f)
    m3_orig = orig["results"]["m3"]
    for tag in ("liquid|overall",):
        b = m3_orig.get(tag, {})
        if b.get("skipped"):
            log(f"  [{tag}] {skip_str(b.get('n_base'))}")
            continue
        for arm in ("rvconst", "f2gamma"):
            a = b["arms"].get(arm, {})
            agr = f"{a['agreement']:.3f}" if a.get("agreement") is not None else skip_str(a.get("n_agreement"))
            fnr = f"{a['sl_fnr']:.3f}" if a.get("sl_fnr") is not None else skip_str(a.get("n_sl_true"))
            log(f"      [{tag}] {arm:8s} TP-agreement={agr} (N={a['n_agreement']})  SL-FNR={fnr} (N_real_sl={a['n_sl_true']})")
        log(f"      [{tag}] pair agreement-diff(f2gamma-rvconst) clust: {fmt_clust(b['pair_agreement_f2gamma_vs_rvconst'])}")
        log(f"      [{tag}] pair SL-FNR-diff(f2gamma-rvconst) clust: {fmt_clust(b['pair_slfnr_f2gamma_vs_rvconst'])}")
    log("  NOTE: not recomputed for the refined premium -- the first-touch day-by-day walk needs the "
        "DAILY (t=1..15) forward underlying/premium path, which the ledger persists only at the "
        "d5/d10/d15 checkpoints. VERDICT.md's REFINEMENT ROUND scope marks M3 unchanged this round "
        "('M3 unchanged (TP agreement + SL-FNR at current N -- expected UNDERPOWERED, just report)'). "
        "SL-FNR was already SKIPPED or thin (N_real_sl=33 at liquid|overall) in the ORIGINAL read.")

    # ---- R3 -- harness parity statement (no both-ways rescore -- see rationale) ----
    log("\n=== R3 (harness parity: vega_ratio treatment, arms vs production) -- STATEMENT, no rescore triggered ===")
    log("  Production (monte_carlo.py resolve(), backtest_cascade.py _option_aware_pnl/_vega_for) applies")
    log("  an earnings-crush vega_ratio via option_pricing.sample_vega_ratio() -- a RANDOM DRAW from an")
    log("  empirical post/pre-earnings option-price-ratio pool (iv_crush_model, side+DTE-banded, N=20,870")
    log("  per project memory), threaded through the SAME option_pnl_pct/bs_option_pnl_pct vega_ratio kwarg")
    log("  this ledger's arms use. Gated on find_spanning_earnings(signal_date, exit_date, ...) != None,")
    log("  UNCONDITIONALLY on GAMMA_AWARE/IV_PREMIUM/IV_MODEL (core shipped behavior, not an experimental flag).")
    log("  build_ledger.py's FOUR original arms (ALL FOUR, including rvconst='production') already apply an")
    log("  earnings-crush vega_ratio too, via the SAME two option_pricing functions, gated on the ledger's own")
    log("  earnings_span_flag -- but using the REAL empirical iv_t/entry_iv ratio (deterministic, ex-post),")
    log("  not a sampled draw. DESIGN.md section 3 already discloses this explicitly as an information")
    log("  advantage ('flagged, not hidden'), and the earnings_yes/earnings_no split (already reported in")
    log("  the original bars, and now the R2 primary/watch-line split above) is the mitigation.")
    log("  rescore_refined.py's vega_ratio_for() reproduces this SAME real-empirical convention identically")
    log("  for the two new refined arms (verbatim copy of build_ledger.py's _vega_ratio() closure).")
    log("  FINDING: arms and production are CONSISTENT in MECHANISM (same vega_ratio pathway through the")
    log("  same two option_pricing functions, both gated on an earnings-spans-the-hold-window concept). The")
    log("  arms do NOT omit an earnings vega_ratio production applies -- they apply a disclosed, different-")
    log("  SOURCE (real vs sampled) one. The literal R3 trigger condition ('if the arms omitted...') is NOT")
    log("  met, so no both-ways (with/without-crush-parity) rescore was performed this round.")
    log("  Minor secondary note (not a parity break): the earnings-window BOUNDARY differs mechanically --")
    log("  production's exit_date is whichever bar the trade actually fires on (TP/SL/hard/trail-dependent);")
    log("  the ledger's earnings_span_flag uses a fixed forward window (up to 15 trading bars / the option's")
    log("  own quote availability). Both check 'does an earnings event fall inside the hold window', just")
    log("  with a slightly different window-end definition -- flagged for completeness, not a parity break.")

    elapsed = time.time() - t0
    log(f"\n=== DONE in {elapsed:.1f}s ===")

    full_json = {
        "refined_meta": rmeta,
        "in_sample_n": df_insample.height, "shadow_oos_n": df_shadow.height,
        "liquid_ge5_n": n_liquid, "all_inclusive_n": n_all,
        "m1_refined": m1_res, "m2_d10_refined": m2d10_res, "m2_d15_refined": m2d15_res,
        "m3_carried_forward_from": ORIG_RESULTS_JSON,
        "m3_carried_forward": {tag: m3_orig.get(tag) for tag in m3_orig if tag.startswith("liquid|") or tag.startswith("all_inclusive|overall")},
        "r3_statement": "consistent_mechanism_no_omission_no_rescore_triggered",
        "elapsed_s": round(elapsed, 1),
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(full_json, f, indent=2, default=str)
    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(lines) + "\n")
    log(f"wrote {OUT_JSON}")
    log(f"wrote {OUT_TXT}")


if __name__ == "__main__":
    main()
