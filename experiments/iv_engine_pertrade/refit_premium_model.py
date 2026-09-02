"""REFINEMENT ROUND R1 -- refit the F2 premium-level model with a
calm-tercile correction, per experiments/iv_engine_pertrade/VERDICT.md's
pre-registered REFINEMENT ROUND scope (the ONE allowed round).

Trigger (VERDICT.md M1): F2 beat RV on the overall liquid-primary median
(0.2096 vs 0.2153) but in only 1/3 RV terciles (bar: >=2/3). T1_low (calm
names) was significantly WORSE for F2 (paired-diff clust t=+3.10), with a
large positive signed bias (+0.328, A3 telemetry) -- F2 over-prices
calm-name premiums on REAL CONTRACTS. This script refits candidate
correction forms to try to fix that.

Fit table: experiments/iv_premium_model/fit_table.parquet (8,642
fit-eligible rows of 8,643, 2022-08+, the SAME calibration panel F2 itself
was fit on -- reused verbatim, never rebuilt here). Holdout-irrelevant
(panel max date 2026-05-15 < CALIBRATION_CUTOFF_DATE 2026-06-15); the
existing assert_no_holdout_leak call in build_fit_model.py already covers
this, re-asserted here defensively.

OOF protocol: the SAME one build_fit_model.py used, reused (not
reimplemented) via direct import: time-fold (split=2025-01-01, forward +
reverse) and 5-fold symbol-fold (md5(symbol)%5, stable across process
restarts). Blended OOF MAE = mean(fwd, rev, symbol-fold-pooled). Selection
applies the SAME F0-vs-F2 lesson recorded in
experiments/iv_premium_model/DESIGN.md Amendment A1 / fit_report.txt:
reverse-fold robustness and VIX-sensitivity retention dominate a close
blended-MAE tie -- the deployment direction (pricing this ledger's already-
matched contracts, and eventually 2016-2022 history the panel never
covered) is structurally the REVERSE-fold extrapolation direction, not the
forward one.

Candidate forms (VERDICT.md R1, verbatim):
  R1i   piecewise intercept by RV tercile + RV + VIX
        atm_iv_hat = a_{T(rv)} + b_rv*rv + c_vix*vix      (5 params)
  R1ii  a + b*RV + c*VIX + d*log(RV)                       (4 params)
  R1iii per-tercile multiplicative scale on the EXISTING FROZEN F2
        atm_iv_hat = scale_{T(rv)} * F2(rv, vix)            (3 params)

Tercile bounds for T(rv) are FROZEN once from this fit table's OWN RV
(engine_rv) distribution (recorded in the report) and reused identically
across every fold variant (full / time-fwd-train / time-rev-train / each
symbol-fold-train) -- NOT refit per fold (that would just be a fixed
feature-transform breakpoint, not a leaky fitted parameter), and NOT the
same cutoffs as the pertrade ledger's separately-computed rv_tercile
column (ledger_v1_meta.json: t1=2.1153 t2=3.7677, a different, smaller,
matched-population set of cutoffs used only for bucketing this experiment's
OWN reporting cells in compute_metrics.py -- kept deliberately distinct;
see NOTE in the output).

Existing F2 is NOT refit here (frozen/shipped, monte_carlo.py
IV_MODEL_COEF_*) -- its OOF numbers are pulled live from
experiments/iv_premium_model/fit_report.json as the ladder's reference
row, per EXECUTE step 1 ("ladder table (existing F2 + the 3 candidate
forms)").

Output: refit_report.json / refit_report.txt (ladder, OOF, coefficient
stability, residual-by-tercile, chosen form + FROZEN coefficients +
rationale). The chosen form's coefficients are the ones step 2
(rescore_refined.py) applies to the ledger -- frozen BEFORE that script
runs, per the task's ordering requirement.

No engine edits (option_pricing.py/monte_carlo.py imported from, never
modified). No commits. ASCII-only stdout/files (PYTHONUTF8=1).
Numbers only -- no pass/fail language.
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
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard
_IVPM_DIR = os.path.join(_ROOT, "experiments", "iv_premium_model")
if _IVPM_DIR not in sys.path:
    sys.path.insert(0, _IVPM_DIR)

import numpy as np                                          # noqa: E402
import polars as pl                                          # noqa: E402

from build_fit_model import md5_fold, r2_mae, TIME_FOLD_SPLIT  # noqa: E402  -- reused verbatim, not reimplemented
from experiments._holdout import assert_no_holdout_leak, CUTOFF  # noqa: E402

FIT_TABLE_PATH = os.path.join(_IVPM_DIR, "fit_table.parquet")
FIT_REPORT_JSON = os.path.join(_IVPM_DIR, "fit_report.json")   # source of the F2 reference OOF row (pulled live, not hand-copied)
OUT_JSON = os.path.join(_HERE, "refit_report.json")
OUT_TXT = os.path.join(_HERE, "refit_report.txt")

# FROZEN existing F2 constants -- verbatim copy of monte_carlo.py's shipped
# IV_MODEL_COEF_*/IV_MODEL_CLAMP_* (lines ~156-161). This is what the ledger's
# f2const/f2gamma arms actually computed (via monte_carlo._iv_premium_pct);
# reproduced here (not reimported from monte_carlo.py, which has import-time
# side effects -- os.environ IV_PREMIUM/IV_MODEL gating -- undesirable for a
# read-only recon script) so R1iii's "scale applied to the existing F2" has an
# exact, source-verified anchor.
F2_COEF_A = 0.14920
F2_COEF_B_RV = 0.09545
F2_COEF_C_VIX = 0.006242
F2_CLAMP_FLOOR = 0.163326
F2_CLAMP_CAP = 1.758980

FORM_NAMES = ["R1iii_f2_tercile_scale", "R1ii_logrv", "R1i_piecewise_intercept"]  # ascending param count (3,4,5) -- parsimony scan order
FORM_NPARAMS = {"R1iii_f2_tercile_scale": 3, "R1ii_logrv": 4, "R1i_piecewise_intercept": 5}
FORM_COEF_NAMES = {
    "R1iii_f2_tercile_scale": ["scale_T1_low", "scale_T2_mid", "scale_T3_high"],
    "R1ii_logrv": ["a", "b_rv", "c_vix", "d_logrv"],
    "R1i_piecewise_intercept": ["a_T1_low", "a_T2_mid", "a_T3_high", "b_rv", "c_vix"],
}

lines = []


def log(msg=""):
    print(msg, flush=True)
    lines.append(str(msg))


# ---------------------------------------------------------------------------
# F2 (existing, frozen -- not refit)
# ---------------------------------------------------------------------------
def f2_predict(rv, vix):
    raw = F2_COEF_A + F2_COEF_B_RV * rv + F2_COEF_C_VIX * vix
    return np.clip(raw, F2_CLAMP_FLOOR, F2_CLAMP_CAP)


# ---------------------------------------------------------------------------
# Tercile labeling (FROZEN cutoffs, computed once, passed in everywhere)
# ---------------------------------------------------------------------------
def tercile_label(rv, t1, t2):
    rv = np.asarray(rv, dtype=float)
    return np.where(rv <= t1, "T1_low", np.where(rv <= t2, "T2_mid", "T3_high"))


# ---------------------------------------------------------------------------
# Candidate design matrices + predict (dispatch by form name)
# ---------------------------------------------------------------------------
def design_matrix(form, rv, vix, tlabel=None):
    rv = np.asarray(rv, dtype=float)
    vix = np.asarray(vix, dtype=float)
    if form == "R1i_piecewise_intercept":
        d1 = (tlabel == "T1_low").astype(float)
        d2 = (tlabel == "T2_mid").astype(float)
        d3 = (tlabel == "T3_high").astype(float)
        return np.column_stack([d1, d2, d3, rv, vix])
    if form == "R1ii_logrv":
        ones = np.ones_like(rv)
        logrv = np.log(rv)
        return np.column_stack([ones, rv, vix, logrv])
    if form == "R1iii_f2_tercile_scale":
        f2 = f2_predict(rv, vix)
        d1 = (tlabel == "T1_low").astype(float) * f2
        d2 = (tlabel == "T2_mid").astype(float) * f2
        d3 = (tlabel == "T3_high").astype(float) * f2
        return np.column_stack([d1, d2, d3])
    raise ValueError(form)


def fit_form(form, rv, vix, y, tlabel=None):
    X = design_matrix(form, rv, vix, tlabel)
    coefs, _res, _rank, _sv = np.linalg.lstsq(X, np.asarray(y, dtype=float), rcond=None)
    return coefs


def predict_form(form, coefs, rv, vix, tlabel=None):
    X = design_matrix(form, rv, vix, tlabel)
    return X @ coefs


def coefs_to_dict(form, coefs):
    names = FORM_COEF_NAMES[form]
    return {n: float(c) for n, c in zip(names, coefs)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    log("=== iv_engine_pertrade REFINEMENT ROUND R1 -- premium-level refit ===")
    log(f"fit table: {FIT_TABLE_PATH}")

    df = pl.read_parquet(FIT_TABLE_PATH)
    n_panel = df.height
    fit_df = df.filter(pl.col("engine_rv").is_not_null() & pl.col("vix_close").is_not_null())
    n = fit_df.height
    log(f"panel rows={n_panel}  fit-eligible (RV+VIX both hit)={n} ({100.0*n/n_panel:.2f}%)")

    assert_no_holdout_leak(fit_df.select(pl.col("date")), context="iv_engine_pertrade R1 refit (fit table)")
    log(f"holdout check OK (cutoff={CUTOFF.isoformat()}, fit table max date={fit_df['date'].max()})")

    rv = fit_df["engine_rv"].to_numpy().astype(float)
    vix = fit_df["vix_close"].to_numpy().astype(float)
    y = fit_df["atm_iv"].to_numpy().astype(float)
    dates = fit_df["date"].to_list()
    symbols_arr = fit_df["symbol"].to_list()

    # ---- FREEZE RV tercile cutoffs (once, on the full fit-eligible sample) ----
    t1_freeze, t2_freeze = np.quantile(rv, [1.0 / 3, 2.0 / 3])
    tlabel_full = tercile_label(rv, t1_freeze, t2_freeze)
    log(f"\nFROZEN RV tercile cutoffs (fit-table-derived, n={n}): t1={t1_freeze:.6f} t2={t2_freeze:.6f}")
    log("  (NOTE: distinct from the pertrade ledger's OWN rv_tercile reporting-bucket cutoffs "
        "t1=2.1153 t2=3.7677 per ledger_v1_meta.json -- that column buckets THIS EXPERIMENT's "
        "matched-CALL population for M1/M2/M3 cell breakdowns; these frozen cutoffs are a property "
        "of the REFITTED MODEL FORMULA itself, applied to every row (ledger or otherwise) the same way.")
    for tname in ("T1_low", "T2_mid", "T3_high"):
        cnt = int((tlabel_full == tname).sum())
        log(f"  {tname}: n={cnt}")

    # ---- F2 reference row (frozen, not refit -- OOF numbers pulled live from fit_report.json) ----
    with open(FIT_REPORT_JSON, "r", encoding="ascii") as f:
        f2_report = json.load(f)
    f2_time = f2_report["oof_time_fold"]["F2_affine_rv_vix"]
    f2_sym = f2_report["oof_symbol_fold"]["F2_affine_rv_vix"]
    f2_blended = f2_report["blended_oof_mae"]["F2_affine_rv_vix"]
    f2_mae_fwd = f2_time["mae_fwd_fit_pre2025_predict_2025plus"]
    f2_mae_rev = f2_time["mae_rev_fit_2025plus_predict_pre2025"]
    f2_mae_sym = f2_sym["mae_cv_pooled"]
    f2_rev_degrad = 100.0 * (f2_mae_rev - f2_mae_fwd) / f2_mae_fwd
    pred_f2_full = f2_predict(rv, vix)
    f2_r2, f2_mae_full = r2_mae(y, pred_f2_full)
    log(f"\n[reference row, NOT refit] existing F2 (monte_carlo.py shipped coefs, pulled live from "
        f"{os.path.basename(FIT_REPORT_JSON)}):")
    log(f"  in-sample (this table) R2={f2_r2:.4f} MAE={f2_mae_full:.4f}")
    log(f"  OOF: fwd={f2_mae_fwd:.4f} rev={f2_mae_rev:.4f} symbol-fold={f2_mae_sym:.4f} "
        f"blended={f2_blended:.5f} rev/fwd degradation={f2_rev_degrad:+.1f}%")

    # ---- In-sample fit (full sample) for each candidate ----
    ladder = {}
    full_coefs = {}
    for form in FORM_NAMES:
        coefs = fit_form(form, rv, vix, y, tlabel_full)
        pred = predict_form(form, coefs, rv, vix, tlabel_full)
        r2, mae = r2_mae(y, pred)
        full_coefs[form] = coefs
        ladder[form] = {"n_params": FORM_NPARAMS[form], "coefs": coefs_to_dict(form, coefs), "r2": r2, "mae": mae}
    log("\n[ladder] in-sample (full fit-eligible sample, n=%d):" % n)
    log(f"  {'form':26s} {'params':>6s} {'R2':>8s} {'MAE':>8s}")
    log(f"  {'F2_existing(reference)':26s} {3:6d} {f2_r2:8.4f} {f2_mae_full:8.4f}   coefs={{'a': {F2_COEF_A}, 'b_rv': {F2_COEF_B_RV}, 'c_vix': {F2_COEF_C_VIX}}}")
    for form in FORM_NAMES:
        L = ladder[form]
        log(f"  {form:26s} {L['n_params']:6d} {L['r2']:8.4f} {L['mae']:8.4f}   coefs={L['coefs']}")

    # ---- OOF: time-fold (SAME split as build_fit_model.py) ----
    is_train_a = np.array([d < TIME_FOLD_SPLIT for d in dates])
    is_test_a = ~is_train_a
    n_train_a, n_test_a = int(is_train_a.sum()), int(is_test_a.sum())
    time_fold = {}
    oof_pred_fwd, oof_pred_rev = {}, {}   # retained for the OOF-by-tercile generalization check below
    for form in FORM_NAMES:
        tl_train_a = tlabel_full[is_train_a]
        tl_test_a = tlabel_full[is_test_a]
        c_fwd = fit_form(form, rv[is_train_a], vix[is_train_a], y[is_train_a], tl_train_a)
        pred_fwd = predict_form(form, c_fwd, rv[is_test_a], vix[is_test_a], tl_test_a)
        _, mae_fwd = r2_mae(y[is_test_a], pred_fwd)

        c_rev = fit_form(form, rv[is_test_a], vix[is_test_a], y[is_test_a], tl_test_a)
        pred_rev = predict_form(form, c_rev, rv[is_train_a], vix[is_train_a], tl_train_a)
        _, mae_rev = r2_mae(y[is_train_a], pred_rev)

        oof_pred_fwd[form] = pred_fwd
        oof_pred_rev[form] = pred_rev
        time_fold[form] = {
            "n_train_a": n_train_a, "n_test_a": n_test_a,
            "mae_fwd_fit_pre2025_predict_2025plus": mae_fwd, "coefs_fwd": coefs_to_dict(form, c_fwd),
            "mae_rev_fit_2025plus_predict_pre2025": mae_rev, "coefs_rev": coefs_to_dict(form, c_rev),
        }
    log(f"\n[oof time-fold] split={TIME_FOLD_SPLIT} (train_a n={n_train_a}, test_a n={n_test_a})")
    log(f"  {'F2_existing(reference)':26s} MAE_fwd={f2_mae_fwd:.4f}  MAE_rev={f2_mae_rev:.4f}  degradation={f2_rev_degrad:+.1f}%")
    for form in FORM_NAMES:
        T = time_fold[form]
        fwd, rev = T["mae_fwd_fit_pre2025_predict_2025plus"], T["mae_rev_fit_2025plus_predict_pre2025"]
        degr = 100.0 * (rev - fwd) / fwd
        log(f"  {form:26s} MAE_fwd={fwd:.4f}  MAE_rev={rev:.4f}  degradation={degr:+.1f}%")

    # ---- OOF: symbol-fold (5-fold, SAME md5(symbol)%5 as build_fit_model.py) ----
    fold_id = np.array([md5_fold(s, 5) for s in symbols_arr])
    symbol_fold = {}
    for form in FORM_NAMES:
        pooled_resid = np.zeros(n)
        fold_coefs = []
        for k in range(5):
            test_mask = fold_id == k
            train_mask = ~test_mask
            c_k = fit_form(form, rv[train_mask], vix[train_mask], y[train_mask], tlabel_full[train_mask])
            pred_k = predict_form(form, c_k, rv[test_mask], vix[test_mask], tlabel_full[test_mask])
            pooled_resid[test_mask] = y[test_mask] - pred_k
            fold_coefs.append(coefs_to_dict(form, c_k))
        mae_cv = float(np.mean(np.abs(pooled_resid)))
        symbol_fold[form] = {"n_folds": 5, "mae_cv_pooled": mae_cv, "fold_coefs": fold_coefs}
    log("\n[oof symbol-fold] 5-fold by md5(symbol)%5:")
    log(f"  {'F2_existing(reference)':26s} MAE_cv_pooled={f2_mae_sym:.4f}")
    for form in FORM_NAMES:
        log(f"  {form:26s} MAE_cv_pooled={symbol_fold[form]['mae_cv_pooled']:.4f}")

    # ---- Parsimony rule (mechanical, SAME 1.05x-of-best-in-ascending-params-order rule) ----
    blended = {}
    for form in FORM_NAMES:
        T, S = time_fold[form], symbol_fold[form]
        blended[form] = float(np.mean([T["mae_fwd_fit_pre2025_predict_2025plus"],
                                        T["mae_rev_fit_2025plus_predict_pre2025"], S["mae_cv_pooled"]]))
    best_oof = min(blended.values())
    threshold = best_oof * 1.05
    mechanical_winner = None
    for form in FORM_NAMES:  # already ascending-params order (3,4,5)
        if blended[form] <= threshold:
            mechanical_winner = form
            break
    log(f"\n[parsimony] blended OOF MAE (mean of fwd/rev/symbol-fold):")
    log(f"  F2_existing(reference) = {f2_blended:.5f}")
    for form in FORM_NAMES:
        log(f"  {form} = {blended[form]:.5f}")
    log(f"  best(candidates)={best_oof:.5f}  threshold(1.05x)={threshold:.5f}  MECHANICAL WINNER = {mechanical_winner}")

    # ---- Reverse-fold degradation + VIX-sensitivity check (the F0-vs-F2 lesson) ----
    rev_degrad = {}
    for form in FORM_NAMES:
        T = time_fold[form]
        fwd, rev = T["mae_fwd_fit_pre2025_predict_2025plus"], T["mae_rev_fit_2025plus_predict_pre2025"]
        rev_degrad[form] = 100.0 * (rev - fwd) / fwd

    # VIX-sensitivity: does c_vix appear (directly, forms i/ii) and stay same-sign/non-trivial
    # across fold variants? R1iii inherits F2's own c_vix by construction (always non-degenerate).
    vix_sensitivity = {}
    for form in FORM_NAMES:
        if form == "R1iii_f2_tercile_scale":
            vix_sensitivity[form] = {"has_own_vix_coef": False, "note": "inherits F2's c_vix=0.006242 by construction (scale multiplies the whole F2 output)"}
            continue
        fold_variants = {"full_sample": coefs_to_dict(form, full_coefs[form]),
                          "time_fwd_train": time_fold[form]["coefs_fwd"],
                          "time_rev_train": time_fold[form]["coefs_rev"]}
        for k, fc in enumerate(symbol_fold[form]["fold_coefs"]):
            fold_variants[f"symbol_fold_{k}_train"] = fc
        c_vix_vals = [v["c_vix"] for v in fold_variants.values()]
        vmin, vmax, vmean = min(c_vix_vals), max(c_vix_vals), float(np.mean(c_vix_vals))
        sign_flip = vmin < 0 < vmax
        rel_range = (vmax - vmin) / abs(vmean) if vmean != 0 else float("inf")
        vix_sensitivity[form] = {
            "has_own_vix_coef": True, "c_vix_min": vmin, "c_vix_max": vmax, "c_vix_mean": vmean,
            "sign_flip": bool(sign_flip), "rel_range": rel_range,
            "all_same_sign": bool(all(v > 0 for v in c_vix_vals) or all(v < 0 for v in c_vix_vals)),
        }
    log("\n[F0-vs-F2 lesson check] reverse-fold degradation + VIX-sensitivity per candidate:")
    log(f"  F2_existing(reference) rev/fwd degradation={f2_rev_degrad:+.1f}%  c_vix=+{F2_COEF_C_VIX} (frozen, non-degenerate, positive)")
    for form in FORM_NAMES:
        vs = vix_sensitivity[form]
        if vs["has_own_vix_coef"]:
            flip = "SIGN FLIPS" if vs["sign_flip"] else "always same sign"
            log(f"  {form:26s} rev/fwd degradation={rev_degrad[form]:+.1f}%  "
                f"c_vix range=[{vs['c_vix_min']:.5f},{vs['c_vix_max']:.5f}] rel_range={vs['rel_range']:.2f} ({flip})")
        else:
            log(f"  {form:26s} rev/fwd degradation={rev_degrad[form]:+.1f}%  {vs['note']}")

    # ---- Residual-by-tercile at full-sample fit (does the correction touch the T1_low bias?) ----
    residual_by_tercile = {}
    for form in FORM_NAMES:
        pred_full = predict_form(form, full_coefs[form], rv, vix, tlabel_full)
        resid_full = y - pred_full
        by_t = {}
        for tname in ("T1_low", "T2_mid", "T3_high"):
            mask = tlabel_full == tname
            by_t[tname] = {"n": int(mask.sum()), "mean_resid": float(np.mean(resid_full[mask])),
                           "mae": float(np.mean(np.abs(resid_full[mask])))}
        residual_by_tercile[form] = by_t
    f2_resid_full = y - pred_f2_full
    f2_by_t = {}
    for tname in ("T1_low", "T2_mid", "T3_high"):
        mask = tlabel_full == tname
        f2_by_t[tname] = {"n": int(mask.sum()), "mean_resid": float(np.mean(f2_resid_full[mask])),
                          "mae": float(np.mean(np.abs(f2_resid_full[mask])))}
    log("\n[residuals by FROZEN RV tercile, full-sample fit -- atm_iv target, this calibration panel]:")
    log(f"  F2_existing(reference): " + "  ".join(f"{t}: mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}" for t, s in f2_by_t.items()))
    for form in FORM_NAMES:
        rb = residual_by_tercile[form]
        log(f"  {form}: " + "  ".join(f"{t}: mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}" for t, s in rb.items()))
    log("  NOTE: the piecewise-intercept form's full-sample T-bucket residual means are tautologically "
        "~0 (OLS normal-equations property of a one-hot dummy regressor evaluated on its own fitting "
        "sample, same tercile labels) -- NOT evidence of a good fit by itself; the OOF-by-tercile check "
        "below (held-out predictions) is the generalization-relevant read.")

    # ---- OOF-by-tercile (held-out generalization check, the real test of "does the
    # calm-tercile correction generalize", not the tautological full-sample-fit read above) ----
    tl_test_a, tl_train_a = tlabel_full[is_test_a], tlabel_full[is_train_a]
    pred_f2_test_a, pred_f2_train_a = pred_f2_full[is_test_a], pred_f2_full[is_train_a]
    y_test_a, y_train_a = y[is_test_a], y[is_train_a]

    def _mae_by_tercile(y_arr, pred_arr, tl_arr):
        out = {}
        for tname in ("T1_low", "T2_mid", "T3_high"):
            mask = tl_arr == tname
            if mask.sum() == 0:
                continue
            out[tname] = {"n": int(mask.sum()), "mean_resid": float(np.mean(y_arr[mask] - pred_arr[mask])),
                         "mae": float(np.mean(np.abs(y_arr[mask] - pred_arr[mask])))}
        return out

    oof_tercile = {
        "F2_existing": {
            "fwd_fit_pre2025_predict_2025plus": _mae_by_tercile(y_test_a, pred_f2_test_a, tl_test_a),
            "rev_fit_2025plus_predict_pre2025": _mae_by_tercile(y_train_a, pred_f2_train_a, tl_train_a),
        }
    }
    for form in FORM_NAMES:
        oof_tercile[form] = {
            "fwd_fit_pre2025_predict_2025plus": _mae_by_tercile(y_test_a, oof_pred_fwd[form], tl_test_a),
            "rev_fit_2025plus_predict_pre2025": _mae_by_tercile(y_train_a, oof_pred_rev[form], tl_train_a),
        }
    log("\n[OOF-by-tercile, HELD-OUT predictions -- the generalization-relevant read for R1's premise "
        "(does the correction fix T1_low out of fold, not just in-fold)]:")
    log("  -- forward fold (fit<2025, predict>=2025 -- test_a, N=%d) --" % n_test_a)
    for label in ["F2_existing"] + FORM_NAMES:
        bt = oof_tercile[label]["fwd_fit_pre2025_predict_2025plus"]
        log(f"  {label:26s}: " + "  ".join(f"{t}(n={s['n']}): resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}" for t, s in bt.items()))
    log("  -- reverse fold (fit>=2025, predict<2025 -- train_a, N=%d) --" % n_train_a)
    for label in ["F2_existing"] + FORM_NAMES:
        bt = oof_tercile[label]["rev_fit_2025plus_predict_pre2025"]
        log(f"  {label:26s}: " + "  ".join(f"{t}(n={s['n']}): resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}" for t, s in bt.items()))

    # ---- SELECTION (F0-vs-F2 lesson applied: reverse-fold + VIX-sensitivity dominate ties) ----
    # Decision recorded as data-driven text below; both the mechanical winner and the
    # (possibly different) recommended winner are reported explicitly, never silently
    # substituted, per the DESIGN.md precedent for the original F0-vs-F2 call.
    candidates_by_blended = sorted(FORM_NAMES, key=lambda f: blended[f])
    best_blended_form = candidates_by_blended[0]
    best_rev_form = min(FORM_NAMES, key=lambda f: rev_degrad[f])

    rationale = []
    rationale.append(
        f"Mechanical parsimony rule (ascending param count 3/4/5, 1.05x-of-best-candidate-blended-OOF "
        f"threshold): winner = {mechanical_winner} (blended={blended[mechanical_winner]:.5f}, "
        f"threshold={threshold:.5f}). Reported faithfully; this recommendation section states whether "
        f"the mechanical winner is adopted or overridden, and why, not a silent substitution."
    )
    rationale.append(
        f"Lowest blended OOF MAE outright (no parsimony discount): {best_blended_form} "
        f"({blended[best_blended_form]:.5f})."
    )
    rationale.append(
        f"Lowest reverse-fold degradation (the deployment-relevant direction per the F0-vs-F2 lesson -- "
        f"this experiment's ledger and any future backward-priced history are structurally the REVERSE "
        f"extrapolation): {best_rev_form} ({rev_degrad[best_rev_form]:+.1f}%), vs F2's own reference "
        f"{f2_rev_degrad:+.1f}%."
    )
    for form in FORM_NAMES:
        rationale.append(
            f"{form}: blended={blended[form]:.5f} rev_degrad={rev_degrad[form]:+.1f}% "
            f"T1_low mean_resid={residual_by_tercile[form]['T1_low']['mean_resid']:+.4f} "
            f"(F2 reference T1_low mean_resid={f2_by_t['T1_low']['mean_resid']:+.4f})."
        )

    # Data-driven selection: prefer the mechanical winner UNLESS it shows F0-style
    # pathology (worst-of-the-pool reverse-fold degradation while another candidate is
    # simultaneously better on blended OOF or comparably close) -- mirrors the original
    # F0-vs-F2 override logic exactly, applied here to whatever the actual numbers show.
    worst_rev_form = max(FORM_NAMES, key=lambda f: rev_degrad[f])
    override = False
    if mechanical_winner == worst_rev_form and len(FORM_NAMES) > 1:
        override = True
        chosen_form = best_rev_form if blended[best_rev_form] <= threshold * 1.15 else mechanical_winner
    else:
        chosen_form = mechanical_winner
    rationale.append(
        f"{'OVERRIDE applied' if override else 'No override needed'}: mechanical winner "
        f"{'is' if mechanical_winner == worst_rev_form else 'is NOT'} simultaneously the worst "
        f"reverse-fold performer of the 3 candidates. CHOSEN FORM = {chosen_form}."
    )
    log("\n[selection] F0-vs-F2 lesson applied (reverse-fold robustness + VIX-sensitivity as tie-breakers, not raw MAE alone):")
    for r in rationale:
        log(f"  - {r}")

    chosen_coefs = coefs_to_dict(chosen_form, full_coefs[chosen_form])
    log(f"\n=== FROZEN WINNER: {chosen_form} ===")
    log(f"  coefficients: {chosen_coefs}")
    log(f"  RV tercile cutoffs (FROZEN, required by this form): t1={t1_freeze:.6f} t2={t2_freeze:.6f}")
    log(f"  blended OOF MAE={blended[chosen_form]:.5f}  rev/fwd degradation={rev_degrad[chosen_form]:+.1f}%  "
        f"(F2 reference: blended={f2_blended:.5f} degradation={f2_rev_degrad:+.1f}%)")

    elapsed = time.time() - t0
    log(f"\n=== DONE in {elapsed:.1f}s ===")

    report = {
        "n_panel": n_panel, "n_fit_eligible": n,
        "tercile_freeze": {"t1": float(t1_freeze), "t2": float(t2_freeze),
                           "note": "frozen from THIS fit table's own engine_rv distribution; "
                                   "distinct from the pertrade ledger's own rv_tercile reporting-bucket "
                                   "cutoffs (t1=2.1153 t2=3.7677, ledger_v1_meta.json)"},
        "f2_reference": {"coefs": {"a": F2_COEF_A, "b_rv": F2_COEF_B_RV, "c_vix": F2_COEF_C_VIX},
                         "clamp": [F2_CLAMP_FLOOR, F2_CLAMP_CAP],
                         "in_sample_r2": f2_r2, "in_sample_mae": f2_mae_full,
                         "oof_fwd": f2_mae_fwd, "oof_rev": f2_mae_rev, "oof_symbol_fold": f2_mae_sym,
                         "oof_blended": f2_blended, "rev_fwd_degradation_pct": f2_rev_degrad,
                         "residual_by_tercile": f2_by_t},
        "ladder_in_sample": ladder,
        "oof_time_fold": time_fold, "oof_symbol_fold": symbol_fold,
        "blended_oof_mae": blended, "best_oof_mae_candidates": best_oof,
        "parsimony_threshold_105pct": threshold, "mechanical_winner": mechanical_winner,
        "reverse_fold_degradation_pct": rev_degrad, "vix_sensitivity": vix_sensitivity,
        "residual_by_tercile": residual_by_tercile, "oof_residual_by_tercile": oof_tercile,
        "selection": {"override_applied": override, "chosen_form": chosen_form, "rationale": rationale},
        "frozen_winner": {"form": chosen_form, "coefs": chosen_coefs,
                          "tercile_cutoffs": {"t1": float(t1_freeze), "t2": float(t2_freeze)},
                          "clamp": [F2_CLAMP_FLOOR, F2_CLAMP_CAP],
                          "note": "clamp reused verbatim from the existing F2 (same panel-derived atm_iv "
                                  "percentile band, form-independent per iv_premium_model/DESIGN.md section 4); "
                                  "not re-derived by this refit."},
    }
    with open(OUT_JSON, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(report, f, indent=2, default=str)
    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(lines) + "\n")
    log(f"wrote {OUT_JSON}")
    log(f"wrote {OUT_TXT}")


if __name__ == "__main__":
    main()
