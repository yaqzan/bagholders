"""IV per-trade M1 RE-READ on the corrected as-traded-spot calibration panel.

Re-opens experiments/iv_engine_pertrade/'s FAILED M1 bar on a NEW DATA CLASS
(re-open condition (b)-adjacent: the calibration TARGET is replaced, not the
functional form). Single variable under test: the panel whose `atm_iv` column
is the regression target.

  OLD panel      : .cache/polygon_iv/iv_ledger_polygon.parquet
                   (price_history.close = split+DIVIDEND back-adjusted spot used
                   both to pick the "ATM" strike and as S in the BS solve)
  CORRECTED panel: .cache/polygon_real_premium/iv_panel_corrected.parquet
                   (as-traded spot throughout; honest pnl15 + fwd_bars)

FORBIDDEN and not done here:
  * no new functional form -- ONLY the two already-frozen forms are refit:
      F2  = a + b_rv*rv + c_vix*vix                       (3 params)
      R1i = a_{T(rv)} + b_rv*rv + c_vix*vix               (5 params)
  * no bar relaxation -- M1/M2/M3 text is printed verbatim next to each result
  * no edits to experiments/iv_premium_model/* or experiments/iv_engine_pertrade/*
    or .cache/polygon_iv/*.  build_fit_model.py's builder is REUSED by
    monkey-patching its module-level PANEL_PATH / FIT_TABLE_PATH globals from
    this script (same code path, no file edit, output redirected into
    .cache/polygon_real_premium/m1_reread/).
  * no MySQL writes; no queue submissions.

Validation ledger is UNCHANGED: .cache/iv_engine_pertrade/ledger_v1.parquet
(yfinance lastPrice, 2025-02+, per-trade contract persisted).

Outputs:
  .cache/polygon_real_premium/m1_reread/fit_table_old.parquet
  .cache/polygon_real_premium/m1_reread/fit_table_corrected.parquet
  experiments/polygon_real_premium/iv_m1_reread.json
  experiments/polygon_real_premium/iv_m1_reread.txt

Numbers only -- no verdict, no adoption language.
ASCII-only stdout/files (PYTHONUTF8=1 / PYTHONIOENCODING=utf-8).
"""
from __future__ import annotations

import os
import sys
import json
import math
import time
import argparse

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
# option_pricing routes option_pnl_pct through the BS model when GAMMA_AWARE=1.
# The const-delta arm MUST be the production path -> force the flag OFF before
# option_pricing is imported anywhere.
os.environ["GAMMA_AWARE"] = "0"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard
_IVPM_DIR = os.path.join(_ROOT, "experiments", "iv_premium_model")
_GEX_DIR = os.path.join(_ROOT, "experiments", "gex")
for _d in (_IVPM_DIR, _GEX_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import numpy as np                                                  # noqa: E402
import polars as pl                                                 # noqa: E402

import build_fit_model as bfm                                       # noqa: E402  -- builder REUSED, never edited
from gex_test import ols_with_clustered_se                          # noqa: E402  -- reused VERBATIM
from option_pricing import (                                        # noqa: E402  -- imported, never reimplemented
    option_pnl_pct, bs_option_pnl_pct, DEFAULT_DELTA,
)
from experiments._holdout import assert_no_holdout_leak, CUTOFF     # noqa: E402

assert os.environ.get("GAMMA_AWARE", "0") != "1", "GAMMA_AWARE must be OFF for the const-delta arm"
assert bfm.__file__.startswith(_ROOT), "build_fit_model imported from outside this checkout"

OLD_PANEL = os.path.join(_ROOT, ".cache", "polygon_iv", "iv_ledger_polygon.parquet")
CORR_PANEL = os.path.join(_ROOT, ".cache", "polygon_real_premium", "iv_panel_corrected.parquet")
OUT_CACHE = os.path.join(_ROOT, ".cache", "polygon_real_premium", "m1_reread")
LEDGER_PATH = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1.parquet")
LEDGER_META = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1_meta.json")
REFINED_LEDGER = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1_refined.parquet")
REAL_PREM_LEDGER = os.path.join(_ROOT, ".cache", "polygon_real_premium", "real_premium_ledger.parquet")
PUB_PERTRADE = os.path.join(_ROOT, "experiments", "iv_engine_pertrade", "results", "pertrade_results.json")
PUB_REFINE = os.path.join(_ROOT, "experiments", "iv_engine_pertrade", "results", "refinement_results.json")
PUB_REFIT = os.path.join(_ROOT, "experiments", "iv_engine_pertrade", "refit_report.json")

OUT_JSON = os.path.join(_HERE, "iv_m1_reread.json")
OUT_TXT = os.path.join(_HERE, "iv_m1_reread.txt")

MIN_CELL_N = 30            # gex_test.py convention, reused
K_ATM = 0.4                # Brenner-Subrahmanyam mapping constant (NOT retuned)
CHECKPOINTS = (5, 10, 15)

# Shipped/frozen constants, verbatim from monte_carlo.py (via refit_premium_model.py)
F2_SHIPPED = {"a": 0.14920, "b_rv": 0.09545, "c_vix": 0.006242}
F2_SHIPPED_CLAMP = (0.163326, 1.758980)

BAR_M1 = ("M1 (premium level): F2 median |model-real|/real entry premium beats RV-premium "
          "overall AND in >=2 of 3 RV terciles.")
BAR_M2 = ("M2 (P&L fidelity): F2+gamma beats RV+const-delta on per-trade P&L MAE at d10 AND d15 "
          "overall, and is not worse in the high-RV tercile.")
BAR_M3 = ("M3 (decision fidelity): F2+gamma TP-hit classification agreement vs the real path >= "
          "production's, AND its SL-hit false-negative rate is not worse (missed real SLs = "
          "hidden risk).")

lines = []


def log(msg=""):
    print(msg, flush=True)
    lines.append(str(msg))


def finite_mask(*arrays):
    m = np.ones(len(arrays[0]), dtype=bool)
    for a in arrays:
        m &= np.isfinite(np.asarray(a, dtype=float))
    return m


def clustered_paired(diff, dates, label=""):
    """Intercept-only OLS + date-clustered sandwich SE on a paired difference.
    Non-finite rows are masked BEFORE the regression; the resulting t is
    asserted finite (a NaN t would read as a null result -- the exact failure
    mode this re-read must not produce)."""
    diff = np.asarray(diff, dtype=float)
    dates = np.asarray(dates)
    m = np.isfinite(diff)
    n_dropped = int((~m).sum())
    diff, dates = diff[m], dates[m]
    n = len(diff)
    if n < MIN_CELL_N:
        return {"skipped": True, "n": n, "n_nonfinite_dropped": n_dropped}
    res = ols_with_clustered_se(diff, np.empty((n, 0)), dates)
    t = float(res["t_clust"][0])
    se = float(res["se_clust"][0])
    mean = float(res["beta"][0])
    if not (math.isfinite(t) and math.isfinite(se) and math.isfinite(mean)):
        raise RuntimeError(
            "NON-FINITE clustered statistic at %s: mean=%r se=%r t=%r n=%d -- aborting loudly "
            "rather than reporting a NaN as a null result." % (label, mean, se, t, n))
    return {"skipped": False, "n": n, "n_clusters": int(res["g"]), "mean_diff": mean,
            "se_clust": se, "t_clust": t, "n_nonfinite_dropped": n_dropped}


# ---------------------------------------------------------------------------
# STEP 1/2a -- fit tables, built by build_fit_model.py's OWN builder
# ---------------------------------------------------------------------------
def build_fit_table_for(panel_path, out_path, tag):
    """Call build_fit_model.build_fit_table() with its module globals
    re-pointed. No edit to build_fit_model.py; nothing is written into
    experiments/iv_premium_model/."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    saved = (bfm.PANEL_PATH, bfm.FIT_TABLE_PATH)
    try:
        bfm.PANEL_PATH = panel_path
        bfm.FIT_TABLE_PATH = out_path
        assert not os.path.abspath(bfm.FIT_TABLE_PATH).startswith(
            os.path.abspath(_IVPM_DIR)), "refusing to write into experiments/iv_premium_model/"
        log("[fit-table:%s] panel=%s" % (tag, panel_path))
        t0 = time.time()
        df, coverage = bfm.build_fit_table()
        log("[fit-table:%s] built in %.1fs -> %s" % (tag, time.time() - t0, out_path))
    finally:
        bfm.PANEL_PATH, bfm.FIT_TABLE_PATH = saved
    return df, coverage


# ---------------------------------------------------------------------------
# STEP 2 -- refit the two FROZEN forms (F2, R1i) on a fit table
# ---------------------------------------------------------------------------
def design_f2(rv, vix):
    return np.column_stack([np.ones_like(rv), rv, vix])


def design_r1i(rv, vix, tl):
    d1 = (tl == "T1_low").astype(float)
    d2 = (tl == "T2_mid").astype(float)
    d3 = (tl == "T3_high").astype(float)
    return np.column_stack([d1, d2, d3, rv, vix])


def tercile_label(rv, t1, t2):
    rv = np.asarray(rv, dtype=float)
    return np.where(rv <= t1, "T1_low", np.where(rv <= t2, "T2_mid", "T3_high"))


F2_NAMES = ["a", "b_rv", "c_vix"]
R1I_NAMES = ["a_T1_low", "a_T2_mid", "a_T3_high", "b_rv", "c_vix"]


def _fit(X, y):
    coefs, _r, _rk, _sv = np.linalg.lstsq(X, y, rcond=None)
    return coefs


def fit_panel(ft_df, tag):
    """Reproduce build_fit_model.py's fit-eligible filter, F2 fit, clamp
    derivation, tercile freeze, R1i fit, and the SAME OOF protocol
    (time-fold split 2025-01-01 fwd+rev, 5-fold md5(symbol)%5)."""
    n_panel = ft_df.height
    fit_df = ft_df.filter(pl.col("engine_rv").is_not_null() & pl.col("vix_close").is_not_null())
    rv = fit_df["engine_rv"].to_numpy().astype(float)
    vix = fit_df["vix_close"].to_numpy().astype(float)
    y = fit_df["atm_iv"].to_numpy().astype(float)
    dates = fit_df["date"].to_list()
    symbols = fit_df["symbol"].to_list()
    ok = finite_mask(rv, vix, y)
    n_nonfinite = int((~ok).sum())
    if n_nonfinite:
        rv, vix, y = rv[ok], vix[ok], y[ok]
        dates = [d for d, k in zip(dates, ok) if k]
        symbols = [s for s, k in zip(symbols, ok) if k]
    n = len(y)

    # ---- clamps: form-independent, from the panel's OWN atm_iv (full panel) ----
    atm_all = ft_df["atm_iv"].to_numpy().astype(float)
    atm_all = atm_all[np.isfinite(atm_all)]
    p1, p50, p99 = (float(v) for v in np.quantile(atm_all, [0.01, 0.50, 0.99]))
    physical_floor, physical_cap = 0.08, 2.50
    clamp_floor = max(p1, physical_floor)
    clamp_cap = min(p99, physical_cap) if p99 > physical_cap else p99

    # ---- frozen RV terciles for R1i (this table's own engine_rv distribution) ----
    t1, t2 = (float(v) for v in np.quantile(rv, [1.0 / 3, 2.0 / 3]))
    tl = tercile_label(rv, t1, t2)

    c_f2 = _fit(design_f2(rv, vix), y)
    c_r1i = _fit(design_r1i(rv, vix, tl), y)

    def pred(form, coefs, rv_a, vix_a, tl_a):
        if form == "F2":
            return design_f2(rv_a, vix_a) @ coefs
        return design_r1i(rv_a, vix_a, tl_a) @ coefs

    def r2_mae(yy, pp):
        resid = yy - pp
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
        return (1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")),\
               float(np.mean(np.abs(resid)))

    is_train_a = np.array([d < bfm.TIME_FOLD_SPLIT for d in dates])
    is_test_a = ~is_train_a
    fold_id = np.array([bfm.md5_fold(s, 5) for s in symbols])

    out_forms = {}
    for form, coefs_full, names in (("F2", c_f2, F2_NAMES), ("R1i", c_r1i, R1I_NAMES)):
        pred_full = pred(form, coefs_full, rv, vix, tl)
        r2_full, mae_full = r2_mae(y, pred_full)

        c_fwd = _fit(design_f2(rv[is_train_a], vix[is_train_a]) if form == "F2"
                     else design_r1i(rv[is_train_a], vix[is_train_a], tl[is_train_a]), y[is_train_a])
        p_fwd = pred(form, c_fwd, rv[is_test_a], vix[is_test_a], tl[is_test_a])
        _, mae_fwd = r2_mae(y[is_test_a], p_fwd)

        c_rev = _fit(design_f2(rv[is_test_a], vix[is_test_a]) if form == "F2"
                     else design_r1i(rv[is_test_a], vix[is_test_a], tl[is_test_a]), y[is_test_a])
        p_rev = pred(form, c_rev, rv[is_train_a], vix[is_train_a], tl[is_train_a])
        _, mae_rev = r2_mae(y[is_train_a], p_rev)

        pooled = np.zeros(n)
        for k in range(5):
            te = fold_id == k
            tr = ~te
            c_k = _fit(design_f2(rv[tr], vix[tr]) if form == "F2"
                       else design_r1i(rv[tr], vix[tr], tl[tr]), y[tr])
            pooled[te] = y[te] - pred(form, c_k, rv[te], vix[te], tl[te])
        mae_sym = float(np.mean(np.abs(pooled)))
        blended = float(np.mean([mae_fwd, mae_rev, mae_sym]))

        def by_t(yy, pp, tt):
            d = {}
            for name in ("T1_low", "T2_mid", "T3_high"):
                m = tt == name
                if m.sum() == 0:
                    continue
                d[name] = {"n": int(m.sum()),
                           "mean_resid": float(np.mean(yy[m] - pp[m])),
                           "mae": float(np.mean(np.abs(yy[m] - pp[m])))}
            return d

        out_forms[form] = {
            "coefs": {k: float(v) for k, v in zip(names, coefs_full)},
            "in_sample_r2": r2_full, "in_sample_mae": mae_full,
            "oof_fwd": mae_fwd, "oof_rev": mae_rev, "oof_symbol_fold": mae_sym,
            "oof_blended": blended,
            "rev_fwd_degradation_pct": 100.0 * (mae_rev - mae_fwd) / mae_fwd,
            "resid_in_sample_by_tercile": by_t(y, pred_full, tl),
            "oof_resid_by_tercile": {
                "fwd_fit_pre2025_predict_2025plus": by_t(y[is_test_a], p_fwd, tl[is_test_a]),
                "rev_fit_2025plus_predict_pre2025": by_t(y[is_train_a], p_rev, tl[is_train_a]),
            },
            "coefs_fwd": {k: float(v) for k, v in zip(names, c_fwd)},
            "coefs_rev": {k: float(v) for k, v in zip(names, c_rev)},
        }

    # ---- FROZEN-MODEL residuals evaluated on each subsample -----------------
    # This is the definition refit_premium_model.py used for its "F2_existing"
    # OOF-by-tercile rows (pred = the FULL-SAMPLE, CLAMPED model evaluated on
    # the fold's rows -- NOT a fold-refit), and therefore the definition behind
    # the VERDICT's "+0.048 calm-name reverse-fold resid". Reported alongside
    # the true fold-refit numbers above so both readings are on the record.
    frozen_eval = {}
    for form, coefs_full in (("F2", c_f2), ("R1i", c_r1i)):
        raw = pred(form, coefs_full, rv, vix, tl)
        clamped = np.clip(raw, clamp_floor, clamp_cap)
        resid = y - clamped
        d = {}
        for sub_name, mask in (("full", np.ones(n, dtype=bool)),
                               ("pre2025_rows", is_train_a),
                               ("2025plus_rows", is_test_a)):
            cell = {}
            for tname in ("T1_low", "T2_mid", "T3_high"):
                mm = mask & (tl == tname)
                if mm.sum() == 0:
                    continue
                cell[tname] = {"n": int(mm.sum()), "mean_resid": float(np.mean(resid[mm])),
                               "mae": float(np.mean(np.abs(resid[mm])))}
            d[sub_name] = cell
        frozen_eval[form] = d

    return {
        "tag": tag, "n_panel": n_panel, "n_fit_eligible": fit_df.height,
        "n_fit_used": n, "n_nonfinite_dropped": n_nonfinite,
        "frozen_model_resid_by_tercile": frozen_eval,
        "date_min": min(dates), "date_max": max(dates),
        "n_train_a_pre2025": int(is_train_a.sum()), "n_test_a_2025plus": int(is_test_a.sum()),
        "clamps": {"panel_atm_iv_p1": p1, "panel_atm_iv_p50": p50, "panel_atm_iv_p99": p99,
                   "physical_floor": physical_floor, "physical_cap": physical_cap,
                   "proposed_floor": float(clamp_floor), "proposed_cap": float(clamp_cap)},
        "tercile_freeze": {"t1": t1, "t2": t2},
        "atm_iv_summary": {"mean": float(np.mean(atm_all)), "median": float(np.median(atm_all)),
                           "p90": float(np.quantile(atm_all, 0.90)), "max": float(np.max(atm_all))},
        "forms": out_forms,
    }


# ---------------------------------------------------------------------------
# STEP 3 -- premium models applied to the UNCHANGED validation ledger
# ---------------------------------------------------------------------------
def premium_for(model, rv, vix, dte):
    if model["form"] == "F2":
        c = model["coefs"]
        raw = c["a"] + c["b_rv"] * rv + c["c_vix"] * vix
    else:
        c = model["coefs"]
        a = np.where(rv <= model["t1"], c["a_T1_low"],
                     np.where(rv <= model["t2"], c["a_T2_mid"], c["a_T3_high"]))
        raw = a + c["b_rv"] * rv + c["c_vix"] * vix
    iv_hat = np.clip(raw, model["clamp"][0], model["clamp"][1])
    return K_ATM * iv_hat * np.sqrt(dte / 365.0), iv_hat


def vega_ratio_for(entry_iv_i, iv_t_i, earn_i):
    """Verbatim reproduction of build_ledger.py / rescore_refined.py's
    _vega_ratio(): REAL empirical iv_t/entry_iv clamped [0.2,2.0], gated on
    earnings_span_flag; 1.0 otherwise."""
    if earn_i and entry_iv_i and iv_t_i and entry_iv_i > 0:
        return max(0.20, min(2.0, iv_t_i / entry_iv_i))
    return 1.0


def rescore_ledger(df, models):
    rv = df["rv"].to_numpy().astype(float)
    vix = df["vix"].to_numpy().astype(float)
    dte = df["dte_at_entry"].to_numpy().astype(float)
    entry_close = df["entry_close"].to_numpy().astype(float)
    entry_iv = df["entry_iv"].to_list()
    earn = df["earnings_span_flag"].to_numpy().astype(bool)

    new_cols = {}
    telemetry = {}
    for key, model in models.items():
        prem, iv_hat = premium_for(model, rv, vix, dte)
        new_cols["prem_%s" % key] = prem.tolist()
        telemetry[key] = {
            "clamp_floor_hits": int(np.sum(iv_hat <= model["clamp"][0] + 1e-12)),
            "clamp_cap_hits": int(np.sum(iv_hat >= model["clamp"][1] - 1e-12)),
            "iv_hat_median": float(np.median(iv_hat)),
            "premium_pct_median": float(np.median(prem)),
        }
        for cp in CHECKPOINTS:
            u_arr = df["real_underlying_d%d" % cp].to_list()
            iv_arr = df["real_iv_d%d" % cp].to_list()
            pc, pg = [], []
            for i in range(df.height):
                u_t = u_arr[i]
                p = prem[i]
                if u_t is None or not (p > 0) or not math.isfinite(p):
                    pc.append(None)
                    pg.append(None)
                    continue
                vr = vega_ratio_for(entry_iv[i], iv_arr[i], bool(earn[i]))
                pc.append(option_pnl_pct("call", float(u_t), float(entry_close[i]), cp, float(p),
                                         total_dte=float(dte[i]), vega_ratio=vr, delta=DEFAULT_DELTA))
                pg.append(bs_option_pnl_pct("call", float(u_t), float(entry_close[i]), cp, float(p),
                                            total_dte=float(dte[i]), vega_ratio=vr))
            new_cols["pnl_d%d_%s_const" % (cp, key)] = pc
            new_cols["pnl_d%d_%s_gamma" % (cp, key)] = pg
    out = df.with_columns([pl.Series(k, v) for k, v in new_cols.items()])
    return out, telemetry


def m1_cell(sub, key, tag):
    s = sub.filter(
        pl.col("real_premium_pct").is_not_null() & (pl.col("real_premium_pct") > 0)
        & pl.col("premium_rv_pct").is_not_null() & pl.col("prem_%s" % key).is_not_null()
    )
    n = s.height
    if n < MIN_CELL_N:
        return {"tag": tag, "model": key, "n": n, "skipped": True}
    real = s["real_premium_pct"].to_numpy().astype(float)
    rvp = s["premium_rv_pct"].to_numpy().astype(float)
    cand = s["prem_%s" % key].to_numpy().astype(float)
    dates = s["date"].to_numpy()
    m = finite_mask(real, rvp, cand) & (real > 0)
    real, rvp, cand, dates = real[m], rvp[m], cand[m], dates[m]
    err_rv = np.abs(rvp - real) / real
    err_c = np.abs(cand - real) / real
    med_rv, med_c = float(np.median(err_rv)), float(np.median(err_c))
    return {
        "tag": tag, "model": key, "n": int(len(real)), "skipped": False,
        "median_err_rv": med_rv, "median_err_cand": med_c,
        "gap_cand_minus_rv": med_c - med_rv,
        "mean_err_rv": float(np.mean(err_rv)), "mean_err_cand": float(np.mean(err_c)),
        "cand_beats_rv_median": bool(med_c < med_rv),
        "signed_bias_median": float(np.median((cand - real) / real)),
        "signed_bias_mean": float(np.mean((cand - real) / real)),
        "paired_diff_clust": clustered_paired(err_c - err_rv, dates, "M1 %s %s" % (tag, key)),
    }


def m2_cell(sub, key, cp, tag):
    real_col = "real_pnl_d%d" % cp
    base = sub.filter(pl.col(real_col).is_not_null())
    if base.height < MIN_CELL_N:
        return {"tag": tag, "model": key, "checkpoint": cp, "n_base": base.height, "skipped": True}
    res = {"tag": tag, "model": key, "checkpoint": cp, "n_base": base.height, "skipped": False,
           "arms": {}}
    arm_cols = {"rvconst": "pred_pnl_d%d_rvconst" % cp,
                "f2gamma_published": "pred_pnl_d%d_f2gamma" % cp,
                "cand_gamma": "pnl_d%d_%s_gamma" % (cp, key),
                "cand_const": "pnl_d%d_%s_const" % (cp, key)}
    for arm, col in arm_cols.items():
        a = base.filter(pl.col(col).is_not_null())
        if a.height < MIN_CELL_N:
            res["arms"][arm] = {"n": a.height, "skipped": True}
            continue
        pred = a[col].to_numpy().astype(float)
        real = a[real_col].to_numpy().astype(float)
        m = finite_mask(pred, real)
        res["arms"][arm] = {"n": int(m.sum()),
                            "mae": float(np.mean(np.abs(pred[m] - real[m]))),
                            "bias": float(np.mean(pred[m] - real[m]))}
    cg, rc = arm_cols["cand_gamma"], arm_cols["rvconst"]
    pair = base.filter(pl.col(cg).is_not_null() & pl.col(rc).is_not_null())
    if pair.height >= MIN_CELL_N:
        real = pair[real_col].to_numpy().astype(float)
        p1 = pair[cg].to_numpy().astype(float)
        p2 = pair[rc].to_numpy().astype(float)
        res["pair_candgamma_vs_rvconst"] = clustered_paired(
            np.abs(p1 - real) - np.abs(p2 - real), pair["date"].to_numpy(),
            "M2 d%d %s %s" % (cp, tag, key))
    else:
        res["pair_candgamma_vs_rvconst"] = {"skipped": True, "n": pair.height}
    return res


def a3_cell(sub, key, tag):
    s = sub.filter(
        pl.col("real_premium_pct").is_not_null() & (pl.col("real_premium_pct") > 0)
        & pl.col("entry_iv").is_not_null() & (pl.col("entry_iv") > 0)
        & pl.col("dte_at_entry").is_not_null() & pl.col("prem_%s" % key).is_not_null()
    )
    if s.height < MIN_CELL_N:
        return {"tag": tag, "model": key, "n": s.height, "skipped": True}
    real = s["real_premium_pct"].to_numpy().astype(float)
    iv = s["entry_iv"].to_numpy().astype(float)
    dte = s["dte_at_entry"].to_numpy().astype(float)
    cand = s["prem_%s" % key].to_numpy().astype(float)
    k_imp = real / (iv * np.sqrt(dte / 365.0))
    m = finite_mask(k_imp, cand, real)
    return {"tag": tag, "model": key, "n": int(m.sum()), "skipped": False,
            "k_implied_median": float(np.median(k_imp[m])),
            "k_implied_mean": float(np.mean(k_imp[m])), "k_assumed": K_ATM,
            "signed_bias_median": float(np.median((cand[m] - real[m]) / real[m])),
            "signed_bias_mean": float(np.mean((cand[m] - real[m]) / real[m]))}


def cut_matrix(df_insample):
    cuts = {}
    for stratum_name, sdf in (("liquid", df_insample.filter(pl.col("liquid_ge5") == True)),  # noqa: E712
                              ("all_inclusive", df_insample)):
        blocks = {"overall": sdf}
        for t in ("T1_low", "T2_mid", "T3_high"):
            blocks["rv_%s" % t] = sdf.filter(pl.col("rv_tercile") == t)
        for b in ("75+", "70-74"):
            blocks["band_%s" % b] = sdf.filter(pl.col("band") == b)
        for e in ("2025", "2026H1"):
            blocks["era_%s" % e] = sdf.filter(pl.col("era") == e)
        blocks["earnings_yes"] = sdf.filter(pl.col("earnings_span_flag") == True)   # noqa: E712
        blocks["earnings_no"] = sdf.filter(pl.col("earnings_span_flag") == False)   # noqa: E712
        for k, v in blocks.items():
            cuts["%s|%s" % (stratum_name, k)] = v
    return cuts


def m1_verdict_count(m1_res, key, stratum="liquid"):
    """Mechanical tercile tally for the verbatim M1 bar (>=2/3 + overall)."""
    out = {"overall": None, "terciles": {}, "n_terciles_beaten": 0, "n_terciles_evaluated": 0}
    o = m1_res.get("%s|overall" % stratum, {}).get(key)
    if o and not o.get("skipped"):
        out["overall"] = {"median_err_rv": o["median_err_rv"], "median_err_cand": o["median_err_cand"],
                          "beats": o["cand_beats_rv_median"], "n": o["n"]}
    for t in ("T1_low", "T2_mid", "T3_high"):
        c = m1_res.get("%s|rv_%s" % (stratum, t), {}).get(key)
        if not c or c.get("skipped"):
            out["terciles"][t] = {"skipped": True, "n": (c or {}).get("n")}
            continue
        out["terciles"][t] = {"median_err_rv": c["median_err_rv"], "median_err_cand": c["median_err_cand"],
                              "gap": c["gap_cand_minus_rv"], "beats": c["cand_beats_rv_median"], "n": c["n"]}
        out["n_terciles_evaluated"] += 1
        if c["cand_beats_rv_median"]:
            out["n_terciles_beaten"] += 1
    return out


# ---------------------------------------------------------------------------
# STEP 4 -- SUPPLEMENTARY: M1 against real traded Polygon contract prices
# ---------------------------------------------------------------------------
def supplementary_m1(led_scored, models):
    rp = pl.read_parquet(REAL_PREM_LEDGER).filter(pl.col("status") == "kept")
    rp = rp.rename({"signal_date": "date"})
    keep = ["symbol", "date", "atm_strike", "expiration_date", "dte_cal", "spot_unadj",
            "entry_premium_real", "entry_volume", "entry_trades", "liquid_entry", "moneyness"]
    rp = rp.select(keep).rename({"atm_strike": "poly_strike", "expiration_date": "poly_expiration",
                                 "entry_volume": "poly_volume", "entry_trades": "poly_trades",
                                 "liquid_entry": "poly_liquid"})
    j = led_scored.join(rp, on=["symbol", "date"], how="inner")
    out = {"real_premium_ledger_kept_n": pl.read_parquet(REAL_PREM_LEDGER).filter(
               pl.col("status") == "kept").height,
           "overlap_n": j.height}
    if j.height == 0:
        out["note"] = "zero overlap"
        return out

    j = j.with_columns([
        (pl.col("entry_premium_real") / pl.col("spot_unadj")).alias("poly_real_premium_pct"),
        (pl.col("poly_strike") == pl.col("strike")).alias("same_strike"),
        (pl.col("poly_expiration") == pl.col("expiration_date")).alias("same_expiration"),
    ])
    j = j.with_columns((pl.col("same_strike") & pl.col("same_expiration")).alias("same_contract"))
    out["overlap_liquid_ge5_n"] = j.filter(pl.col("liquid_ge5") == True).height       # noqa: E712
    out["overlap_poly_liquid_n"] = j.filter(pl.col("poly_liquid") == True).height     # noqa: E712
    out["overlap_same_contract_n"] = j.filter(pl.col("same_contract") == True).height  # noqa: E712
    out["overlap_band_counts"] = {r["band"]: r["len"] for r in j.group_by("band").len().to_dicts()}
    out["overlap_date_min"] = j["date"].min()
    out["overlap_date_max"] = j["date"].max()

    rv = j["rv"].to_numpy().astype(float)
    vix = j["vix"].to_numpy().astype(float)
    dte_poly = j["dte_cal"].to_numpy().astype(float)
    # RV-premium baseline is DTE-invariant in production (PREMIUM_MULT * rv/100)
    prem_rv = j["premium_rv_pct"].to_numpy().astype(float)
    cols = {"prem_rv_poly": prem_rv.tolist()}
    for key, model in models.items():
        p, _ = premium_for(model, rv, vix, dte_poly)
        cols["prem_poly_%s" % key] = p.tolist()
    j = j.with_columns([pl.Series(k, v) for k, v in cols.items()])

    def cell(sub, key, tag):
        s = sub.filter(pl.col("poly_real_premium_pct").is_not_null()
                       & (pl.col("poly_real_premium_pct") > 0))
        if s.height < MIN_CELL_N:
            return {"tag": tag, "model": key, "n": s.height, "skipped": True}
        real = s["poly_real_premium_pct"].to_numpy().astype(float)
        rvp = s["prem_rv_poly"].to_numpy().astype(float)
        cand = s["prem_poly_%s" % key].to_numpy().astype(float)
        dates = s["date"].to_numpy()
        m = finite_mask(real, rvp, cand) & (real > 0)
        real, rvp, cand, dates = real[m], rvp[m], cand[m], dates[m]
        err_rv = np.abs(rvp - real) / real
        err_c = np.abs(cand - real) / real
        return {"tag": tag, "model": key, "n": int(len(real)), "skipped": False,
                "median_err_rv": float(np.median(err_rv)),
                "median_err_cand": float(np.median(err_c)),
                "gap_cand_minus_rv": float(np.median(err_c) - np.median(err_rv)),
                "cand_beats_rv_median": bool(np.median(err_c) < np.median(err_rv)),
                "signed_bias_median": float(np.median((cand - real) / real)),
                "paired_diff_clust": clustered_paired(err_c - err_rv, dates,
                                                      "SUPP %s %s" % (tag, key))}

    strata = {
        "poly_all_overlap": j,
        "poly_liquid_entry": j.filter(pl.col("poly_liquid") == True),        # noqa: E712
        "poly_same_contract": j.filter(pl.col("same_contract") == True),     # noqa: E712
        "yf_liquid_ge5": j.filter(pl.col("liquid_ge5") == True),             # noqa: E712
        "poly_same_contract_and_liquid": j.filter(
            (pl.col("same_contract") == True) & (pl.col("poly_liquid") == True)),  # noqa: E712
    }
    res = {}
    for sname, sdf in strata.items():
        for cutname, cdf in [("overall", sdf)] + [("rv_%s" % t, sdf.filter(pl.col("rv_tercile") == t))
                                                  for t in ("T1_low", "T2_mid", "T3_high")]:
            tag = "%s|%s" % (sname, cutname)
            res[tag] = {k: cell(cdf, k, tag) for k in models}
    out["cells"] = res
    out["tallies"] = {k: {sname: m1_verdict_count(res, k, sname) for sname in strata} for k in models}
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-fit-tables", action="store_true",
                    help="reuse already-built fit tables in .cache/polygon_real_premium/m1_reread/")
    args = ap.parse_args()

    t_start = time.time()
    log("=== IV per-trade M1 RE-READ (corrected calibration panel) ===")
    log("forms refit: F2 (frozen 3-param) and R1i (frozen 5-param piecewise-intercept) ONLY.")
    log("validation ledger UNCHANGED: %s" % LEDGER_PATH)
    log("")

    # ---------------- STEP 1/2a: fit tables ----------------
    ft_paths = {"old": os.path.join(OUT_CACHE, "fit_table_old.parquet"),
                "corrected": os.path.join(OUT_CACHE, "fit_table_corrected.parquet")}
    panels = {"old": OLD_PANEL, "corrected": CORR_PANEL}
    fts, covs = {}, {}
    for tag in ("old", "corrected"):
        if args.reuse_fit_tables and os.path.exists(ft_paths[tag]):
            fts[tag] = pl.read_parquet(ft_paths[tag])
            covs[tag] = {"reused": True}
            log("[fit-table:%s] REUSED %s (%d rows)" % (tag, ft_paths[tag], fts[tag].height))
        else:
            fts[tag], covs[tag] = build_fit_table_for(panels[tag], ft_paths[tag], tag)
    log("")

    # ---- COVERAGE CONTROL: restrict both panels to their common (symbol,date) rows.
    # The corrected panel has 9,287 rows vs the old panel's 8,643 (the corrected
    # anchor resolves a different set of strikes, so some rows that missed now
    # keep and vice-versa). Refitting on the INTERSECTION isolates the LABEL
    # change (atm_iv recomputed against as-traded spot) from the COVERAGE change.
    keys_old = set(zip(fts["old"]["symbol"].to_list(), fts["old"]["date"].to_list()))
    keys_new = set(zip(fts["corrected"]["symbol"].to_list(), fts["corrected"]["date"].to_list()))
    common = keys_old & keys_new
    log("[coverage-control] old rows=%d corrected rows=%d common (symbol,date)=%d "
        "(old-only=%d, corrected-only=%d)"
        % (len(keys_old), len(keys_new), len(common), len(keys_old - keys_new),
           len(keys_new - keys_old)))
    common_df = pl.DataFrame({"symbol": [k[0] for k in sorted(common)],
                              "date": [k[1] for k in sorted(common)]},
                             infer_schema_length=None)
    fts["old_common"] = fts["old"].join(common_df, on=["symbol", "date"], how="inner")
    fts["corrected_common"] = fts["corrected"].join(common_df, on=["symbol", "date"], how="inner")
    log("")

    fitres = {tag: fit_panel(fts[tag], tag)
              for tag in ("old", "corrected", "old_common", "corrected_common")}

    # ---------------- STEP 1: reproduction of the published fit ----------------
    pub_refit = json.load(open(PUB_REFIT, "r", encoding="ascii"))
    pub_pertrade = json.load(open(PUB_PERTRADE, "r", encoding="ascii"))
    pub_refine = json.load(open(PUB_REFINE, "r", encoding="ascii"))

    log("=== STEP 1 -- REPRODUCTION on the OLD panel ===")
    log("panel rows=%d  fit-eligible=%d  used=%d  dates %s..%s"
        % (fitres["old"]["n_panel"], fitres["old"]["n_fit_eligible"], fitres["old"]["n_fit_used"],
           fitres["old"]["date_min"], fitres["old"]["date_max"]))
    repro = {}

    pub_f2 = pub_refit["f2_reference"]["coefs"]
    got_f2 = fitres["old"]["forms"]["F2"]["coefs"]
    for k in F2_NAMES:
        repro["F2_%s" % k] = {"published": pub_f2[k], "recomputed": got_f2[k],
                             "delta": got_f2[k] - pub_f2[k]}
    pub_r1i = pub_refit["frozen_winner"]["coefs"]
    got_r1i = fitres["old"]["forms"]["R1i"]["coefs"]
    for k in R1I_NAMES:
        repro["R1i_%s" % k] = {"published": pub_r1i[k], "recomputed": got_r1i[k],
                              "delta": got_r1i[k] - pub_r1i[k]}
    pub_tc = pub_refit["frozen_winner"]["tercile_cutoffs"]
    for k in ("t1", "t2"):
        repro["R1i_tercile_%s" % k] = {"published": pub_tc[k],
                                       "recomputed": fitres["old"]["tercile_freeze"][k],
                                       "delta": fitres["old"]["tercile_freeze"][k] - pub_tc[k]}
    repro["clamp_floor"] = {"published": F2_SHIPPED_CLAMP[0],
                            "recomputed": fitres["old"]["clamps"]["proposed_floor"],
                            "delta": fitres["old"]["clamps"]["proposed_floor"] - F2_SHIPPED_CLAMP[0]}
    repro["clamp_cap"] = {"published": F2_SHIPPED_CLAMP[1],
                          "recomputed": fitres["old"]["clamps"]["proposed_cap"],
                          "delta": fitres["old"]["clamps"]["proposed_cap"] - F2_SHIPPED_CLAMP[1]}
    repro["F2_oof_rev_T1_low_resid"] = {
        "published": pub_refit["f2_reference"]["residual_by_tercile"]["T1_low"]["mean_resid"],
        "recomputed": None, "delta": None}   # filled below (different definition; see note)
    # The VERDICT's "+0.048 calm-name OOF resid" is F2's REVERSE-FOLD T1_low mean resid.
    pub_rev_t1 = pub_refit["oof_residual_by_tercile"]["F2_existing"][
        "rev_fit_2025plus_predict_pre2025"]["T1_low"]["mean_resid"]
    got_rev_t1 = fitres["old"]["forms"]["F2"]["oof_resid_by_tercile"][
        "rev_fit_2025plus_predict_pre2025"]["T1_low"]["mean_resid"]
    repro["F2_frozen_on_pre2025_T1_resid"] = {
        "published": pub_rev_t1,
        "recomputed": fitres["old"]["frozen_model_resid_by_tercile"]["F2"]["pre2025_rows"]["T1_low"]["mean_resid"],
        "delta": fitres["old"]["frozen_model_resid_by_tercile"]["F2"]["pre2025_rows"]["T1_low"]["mean_resid"] - pub_rev_t1}
    pub_full_t1 = pub_refit["f2_reference"]["residual_by_tercile"]["T1_low"]["mean_resid"]
    repro["F2_frozen_fullsample_T1_resid"] = {
        "published": pub_full_t1,
        "recomputed": fitres["old"]["frozen_model_resid_by_tercile"]["F2"]["full"]["T1_low"]["mean_resid"],
        "delta": fitres["old"]["frozen_model_resid_by_tercile"]["F2"]["full"]["T1_low"]["mean_resid"] - pub_full_t1}
    repro["F2_truerevfold_T1_low_mean_resid_NOTE"] = {
        "published": pub_rev_t1, "recomputed": got_rev_t1, "delta": got_rev_t1 - pub_rev_t1}
    del repro["F2_oof_rev_T1_low_resid"]

    for k, v in repro.items():
        log("  %-38s published=%+.6f  recomputed=%+.6f  delta=%+.2e"
            % (k, v["published"], v["recomputed"], v["delta"]))
    log("  NOTE on the last row: refit_premium_model.py's 'F2_existing' OOF-by-tercile rows are")
    log("  NOT a fold-refit -- they evaluate the FROZEN full-sample clamped F2 on each fold's rows.")
    log("  The VERDICT's '+0.048 calm-name reverse-fold resid' is therefore the")
    log("  F2_frozen_on_pre2025_T1_resid row above (reproduced exactly). A TRUE fold-refit F2")
    log("  (fit 2025+, predict pre-2025, unclamped, build_fit_model.py's own OOF convention)")
    log("  gives +0.0826 on the same cell -- a different, larger, same-signed quantity. Both are")
    log("  reported; only the first is the published number.")
    log("")

    # ---------------- models ----------------
    models = {}
    models["f2_shipped"] = {"form": "F2", "coefs": dict(F2_SHIPPED),
                            "clamp": F2_SHIPPED_CLAMP, "note": "monte_carlo.py shipped rounded coefs"}
    for tag in ("old", "corrected", "old_common", "corrected_common"):
        cl = (fitres[tag]["clamps"]["proposed_floor"], fitres[tag]["clamps"]["proposed_cap"])
        models["f2_%s" % tag] = {"form": "F2", "coefs": fitres[tag]["forms"]["F2"]["coefs"],
                                 "clamp": cl}
        models["r1i_%s" % tag] = {"form": "R1i", "coefs": fitres[tag]["forms"]["R1i"]["coefs"],
                                  "clamp": cl, "t1": fitres[tag]["tercile_freeze"]["t1"],
                                  "t2": fitres[tag]["tercile_freeze"]["t2"]}
    HEADLINE = ["f2_shipped", "f2_old", "r1i_old", "f2_corrected", "r1i_corrected"]
    CONTROL = ["f2_old_common", "r1i_old_common", "f2_corrected_common", "r1i_corrected_common"]
    # The published refined arm reused F2's clamp verbatim; r1i_old must therefore
    # carry the same clamp for an apples-to-apples reproduction (it does: the old
    # panel's own clamp IS that clamp -- asserted in the reproduction table above).

    log("=== premium models under test (no new forms) ===")
    for k, m in models.items():
        log("  %-12s form=%-4s clamp=[%.6f, %.6f] coefs=%s%s"
            % (k, m["form"], m["clamp"][0], m["clamp"][1],
               {kk: round(vv, 6) for kk, vv in m["coefs"].items()},
               ("  terciles=[%.4f, %.4f]" % (m["t1"], m["t2"])) if m["form"] == "R1i" else ""))
    log("")

    log("=== STEP 2 -- REFIT on the CORRECTED panel (same forms, same builder, same OOF protocol) ===")
    log("  panel sizes: old n_fit=%d  corrected n_fit=%d  (common-row control n_fit=%d/%d)"
        % (fitres["old"]["n_fit_used"], fitres["corrected"]["n_fit_used"],
           fitres["old_common"]["n_fit_used"], fitres["corrected_common"]["n_fit_used"]))
    for tag in ("old", "corrected", "old_common", "corrected_common"):
        c = fitres[tag]["clamps"]
        s = fitres[tag]["atm_iv_summary"]
        log("  [%s] atm_iv p1=%.4f p50=%.4f p90=%.4f p99=%.4f max=%.4f -> clamp [%.6f, %.6f]"
            % (tag, c["panel_atm_iv_p1"], c["panel_atm_iv_p50"], s["p90"], c["panel_atm_iv_p99"],
               s["max"], c["proposed_floor"], c["proposed_cap"]))
    log("")
    for form in ("F2", "R1i"):
        log("  -- %s coefficients --" % form)
        for tag in ("old", "corrected", "old_common", "corrected_common"):
            f = fitres[tag]["forms"][form]
            log("    %-18s %s" % (tag, {k: round(v, 6) for k, v in f["coefs"].items()}))
        log("  -- %s fit quality --" % form)
        for tag in ("old", "corrected", "old_common", "corrected_common"):
            f = fitres[tag]["forms"][form]
            log("    %-18s R2=%.4f MAE=%.4f | OOF fwd=%.4f rev=%.4f sym=%.4f blended=%.5f "
                "rev/fwd degradation=%+.1f%%"
                % (tag, f["in_sample_r2"], f["in_sample_mae"], f["oof_fwd"], f["oof_rev"],
                   f["oof_symbol_fold"], f["oof_blended"], f["rev_fwd_degradation_pct"]))
        log("  -- %s residuals by RV tercile (mean_resid = actual_iv - model_iv; "
            "POSITIVE = model UNDER-predicts) --" % form)
        for tag in ("old", "corrected"):
            fe = fitres[tag]["frozen_model_resid_by_tercile"][form]
            tf = fitres[tag]["forms"][form]["oof_resid_by_tercile"]
            log("    [%s] frozen-model-on-subsample (published definition):" % tag)
            for sub in ("full", "pre2025_rows", "2025plus_rows"):
                log("      %-14s %s" % (sub, "  ".join(
                    "%s(n=%d) resid=%+.4f mae=%.4f" % (t, v["n"], v["mean_resid"], v["mae"])
                    for t, v in fe[sub].items())))
            log("    [%s] TRUE fold-refit OOF (build_fit_model convention, unclamped):" % tag)
            for sub, lbl in (("fwd_fit_pre2025_predict_2025plus", "fwd"),
                             ("rev_fit_2025plus_predict_pre2025", "rev")):
                log("      %-14s %s" % (lbl, "  ".join(
                    "%s(n=%d) resid=%+.4f mae=%.4f" % (t, v["n"], v["mean_resid"], v["mae"])
                    for t, v in tf[sub].items())))
        log("")

    # ---------------- STEP 3: apply the bars to the ledger ----------------
    led = pl.read_parquet(LEDGER_PATH)
    meta = json.load(open(LEDGER_META, "r", encoding="ascii"))
    log("ledger rows=%d cols=%d built_at=%s version_id=%s"
        % (led.height, len(led.columns), meta.get("built_at"), meta.get("version_id")))
    led_scored, telem = rescore_ledger(led, models)

    # premium-column reproduction checks against the archived ledgers
    prem_checks = {}
    d = np.abs(np.asarray(led_scored["prem_f2_shipped"].to_list(), dtype=float)
               - led["premium_f2_pct"].to_numpy().astype(float))
    prem_checks["f2_shipped_vs_ledger_premium_f2_pct"] = {"max_abs_diff": float(np.max(d)),
                                                          "median_abs_diff": float(np.median(d))}
    d = np.abs(np.asarray(led_scored["prem_f2_old"].to_list(), dtype=float)
               - led["premium_f2_pct"].to_numpy().astype(float))
    prem_checks["f2_old_refit_vs_ledger_premium_f2_pct"] = {"max_abs_diff": float(np.max(d)),
                                                            "median_abs_diff": float(np.median(d))}
    if os.path.exists(REFINED_LEDGER):
        ref = pl.read_parquet(REFINED_LEDGER)
        d = np.abs(np.asarray(led_scored["prem_r1i_old"].to_list(), dtype=float)
                   - ref["premium_f2refined_pct"].to_numpy().astype(float))
        prem_checks["r1i_old_vs_ledger_premium_f2refined_pct"] = {
            "max_abs_diff": float(np.max(d)), "median_abs_diff": float(np.median(d))}
    log("\n[premium-column reproduction vs archived ledgers]")
    for k, v in prem_checks.items():
        log("  %-45s max|diff|=%.3e median|diff|=%.3e" % (k, v["max_abs_diff"], v["median_abs_diff"]))

    df_ins = led_scored.filter(~pl.col("is_shadow_oos"))
    df_shadow = led_scored.filter(pl.col("is_shadow_oos"))
    assert_no_holdout_leak(df_ins, context="iv_m1_reread (in-sample slice)")
    n_liquid = df_ins.filter(pl.col("liquid_ge5") == True).height   # noqa: E712
    log("\nholdout guard PASSED (cutoff=%s); in-sample N=%d (published 2551)  liquid_ge5 N=%d "
        "(published 563)  shadow_oos N=%d" % (CUTOFF.isoformat(), df_ins.height, n_liquid,
                                              df_shadow.height))

    cuts = cut_matrix(df_ins)
    m1_res, m2_d10, m2_d15, a3_res = {}, {}, {}, {}
    for tag, sub in cuts.items():
        m1_res[tag] = {k: m1_cell(sub, k, tag) for k in models}
        m2_d10[tag] = {k: m2_cell(sub, k, 10, tag) for k in models}
        m2_d15[tag] = {k: m2_cell(sub, k, 15, tag) for k in models}
        a3_res[tag] = {k: a3_cell(sub, k, tag) for k in models}

    tallies = {k: {"liquid": m1_verdict_count(m1_res, k, "liquid"),
                   "all_inclusive": m1_verdict_count(m1_res, k, "all_inclusive")} for k in models}

    log("\n=== M1 -- BAR (verbatim) ===")
    log("  %s" % BAR_M1)
    for k in HEADLINE:
        t = tallies[k]["liquid"]
        log("\n  [model %s] liquid-primary" % k)
        o = t["overall"]
        log("    overall  N=%d  median|err| rv=%.4f cand=%.4f  gap=%+.4f  beats=%s"
            % (o["n"], o["median_err_rv"], o["median_err_cand"],
               o["median_err_cand"] - o["median_err_rv"], o["beats"]))
        for tt in ("T1_low", "T2_mid", "T3_high"):
            c = t["terciles"][tt]
            if c.get("skipped"):
                log("    %-8s SKIPPED (N=%s)" % (tt, c.get("n")))
                continue
            log("    %-8s N=%d  rv=%.4f cand=%.4f  gap=%+.4f  beats=%s"
                % (tt, c["n"], c["median_err_rv"], c["median_err_cand"], c["gap"], c["beats"]))
        log("    TERCILE TALLY: %d/%d beaten (bar: >=2/3)  overall beaten: %s"
            % (t["n_terciles_beaten"], t["n_terciles_evaluated"], o["beats"]))
        for tt in ("overall", "rv_T1_low", "rv_T2_mid", "rv_T3_high"):
            c = m1_res["liquid|%s" % tt][k]
            if not c.get("skipped"):
                cl = c["paired_diff_clust"]
                if not cl.get("skipped"):
                    log("      clust(cand-rv) %-10s diff=%+.4f se=%.4f t=%+.2f N=%d G=%d"
                        % (tt, cl["mean_diff"], cl["se_clust"], cl["t_clust"], cl["n"],
                           cl["n_clusters"]))

    log("\n=== M1 -- all-inclusive (robustness only; lastPrice-staleness caveat) ===")
    for k in HEADLINE:
        t = tallies[k]["all_inclusive"]
        o = t["overall"]
        log("  [%s] overall N=%d rv=%.4f cand=%.4f gap=%+.4f | tercile tally %d/%d"
            % (k, o["n"], o["median_err_rv"], o["median_err_cand"],
               o["median_err_cand"] - o["median_err_rv"], t["n_terciles_beaten"],
               t["n_terciles_evaluated"]))

    log("\n=== M1 -- era / band sign-consistency (liquid stratum, cross-cutting requirement) ===")
    for tag in ("liquid|era_2025", "liquid|era_2026H1", "liquid|band_75+", "liquid|band_70-74",
                "liquid|earnings_no", "liquid|earnings_yes"):
        log("  [%s]" % tag)
        for k in HEADLINE:
            c = m1_res[tag][k]
            if c.get("skipped"):
                log("    %-14s SKIPPED (N=%s)" % (k, c.get("n")))
                continue
            log("    %-14s N=%d rv=%.4f cand=%.4f gap=%+.4f beats=%s"
                % (k, c["n"], c["median_err_rv"], c["median_err_cand"],
                   c["gap_cand_minus_rv"], c["cand_beats_rv_median"]))

    log("\n=== M1 -- COVERAGE CONTROL (both panels restricted to their common (symbol,date) rows) ===")
    log("  Isolates the LABEL change (atm_iv target recomputed on as-traded spot) from the")
    log("  COVERAGE change (the corrected anchor keeps a partly different row set).")
    for k in CONTROL:
        t = tallies[k]["liquid"]
        o = t["overall"]
        gaps = "  ".join("%s %+.4f" % (tt, t["terciles"][tt]["gap"]) for tt in
                         ("T1_low", "T2_mid", "T3_high") if not t["terciles"][tt].get("skipped"))
        log("  %-22s overall N=%d rv=%.4f cand=%.4f gap=%+.4f | tercile tally %d/%d | %s"
            % (k, o["n"], o["median_err_rv"], o["median_err_cand"],
               o["median_err_cand"] - o["median_err_rv"], t["n_terciles_beaten"],
               t["n_terciles_evaluated"], gaps))

    log("\n=== M2 -- BAR (verbatim) ===")
    log("  %s" % BAR_M2)
    log("  (R2 re-scope from the refinement round: ex-earnings is the DECIDING d15 cell;")
    log("   earnings-window trades are a watch line.)")
    for cpname, res in (("d10", m2_d10), ("d15", m2_d15)):
        for tag in ("liquid|overall", "liquid|earnings_no", "liquid|earnings_yes",
                    "liquid|rv_T3_high"):
            log("\n  [%s %s]" % (cpname, tag))
            for k in HEADLINE:
                b = res[tag][k]
                if b.get("skipped"):
                    log("    %-12s SKIPPED (N_base=%s)" % (k, b.get("n_base")))
                    continue
                arms = b["arms"]
                log("    %-12s MAE rvconst=%s candgamma=%s candconst=%s f2gamma_pub=%s"
                    % (k,
                       ("%.4f(N=%d)" % (arms["rvconst"]["mae"], arms["rvconst"]["n"]))
                       if not arms["rvconst"].get("skipped") else "SKIP",
                       ("%.4f(N=%d)" % (arms["cand_gamma"]["mae"], arms["cand_gamma"]["n"]))
                       if not arms["cand_gamma"].get("skipped") else "SKIP",
                       ("%.4f" % arms["cand_const"]["mae"])
                       if not arms["cand_const"].get("skipped") else "SKIP",
                       ("%.4f" % arms["f2gamma_published"]["mae"])
                       if not arms["f2gamma_published"].get("skipped") else "SKIP"))
                cl = b["pair_candgamma_vs_rvconst"]
                if not cl.get("skipped"):
                    log("                 clust(candgamma-rvconst) diff=%+.4f se=%.4f t=%+.2f N=%d G=%d"
                        % (cl["mean_diff"], cl["se_clust"], cl["t_clust"], cl["n"], cl["n_clusters"]))

    log("\n=== A3 telemetry (implied ATM constant + candidate-vs-real signed bias) ===")
    for tag in ("liquid|overall", "liquid|rv_T1_low", "liquid|rv_T2_mid", "liquid|rv_T3_high"):
        log("  [%s]" % tag)
        for k in HEADLINE:
            b = a3_res[tag][k]
            if b.get("skipped"):
                log("    %-12s SKIPPED (N=%s)" % (k, b.get("n")))
                continue
            log("    %-12s N=%d k_implied med=%.4f mean=%.4f (assumed 0.4)  signed bias med=%+.4f mean=%+.4f"
                % (k, b["n"], b["k_implied_median"], b["k_implied_mean"],
                   b["signed_bias_median"], b["signed_bias_mean"]))

    log("\n=== M3 -- BAR (verbatim) ===")
    log("  %s" % BAR_M3)
    m3_pub = pub_pertrade["results"]["m3"]["liquid|overall"]
    for arm in ("rvconst", "f2gamma"):
        a = m3_pub["arms"][arm]
        log("  [published, liquid|overall] %-8s TP-agreement=%.3f (N=%d)  SL-FNR=%.3f (N_real_sl=%d)"
            % (arm, a["agreement"], a["n_agreement"], a["sl_fnr"], a["n_sl_true"]))
    log("  [published] pair agreement-diff(f2gamma-rvconst) diff=%+.4f t=%+.2f N=%d"
        % (m3_pub["pair_agreement_f2gamma_vs_rvconst"]["mean_diff"],
           m3_pub["pair_agreement_f2gamma_vs_rvconst"]["t_clust"],
           m3_pub["pair_agreement_f2gamma_vs_rvconst"]["n"]))
    log("  [published] pair SL-FNR-diff(f2gamma-rvconst) diff=%+.4f t=%+.2f N=%d"
        % (m3_pub["pair_slfnr_f2gamma_vs_rvconst"]["mean_diff"],
           m3_pub["pair_slfnr_f2gamma_vs_rvconst"]["t_clust"],
           m3_pub["pair_slfnr_f2gamma_vs_rvconst"]["n"]))
    log("  NOT RE-DERIVABLE for any NEW premium model from the persisted ledger: the first-touch")
    log("  TP/SL walk needs the DAILY t=1..15 forward underlying AND option-IV path; ledger_v1")
    log("  persists only the d5/d10/d15 checkpoints. Recomputing M3 requires re-running")
    log("  build_ledger.py (~2,551 forward option_prices path queries, db=heavy). The refinement")
    log("  round hit the same wall and carried M3 forward verbatim; this re-read does the same.")

    # ---------------- STEP 4: supplementary ----------------
    log("\n=== STEP 4 (SUPPLEMENTARY) -- M1 against REAL traded Polygon contract prices ===")
    supp = supplementary_m1(df_ins, models)
    log("  real_premium_ledger kept N=%d" % supp["real_premium_ledger_kept_n"])
    log("  overlap with the in-sample validation ledger: N=%d  (liquid_ge5 %s / polygon-liquid %s / "
        "same contract %s)" % (supp["overlap_n"], supp.get("overlap_liquid_ge5_n"),
                               supp.get("overlap_poly_liquid_n"), supp.get("overlap_same_contract_n")))
    if supp["overlap_n"]:
        log("  overlap dates %s..%s  bands=%s" % (supp["overlap_date_min"], supp["overlap_date_max"],
                                                  supp["overlap_band_counts"]))
        for sname in ("poly_all_overlap", "poly_liquid_entry", "yf_liquid_ge5",
                      "poly_same_contract", "poly_same_contract_and_liquid"):
            log("  -- stratum %s --" % sname)
            for k in HEADLINE:
                t = supp["tallies"][k][sname]
                o = t["overall"]
                if o is None:
                    log("    %-14s overall SKIPPED" % k)
                    continue
                gaps = "  ".join("%s %+.4f(N=%d)" % (tt, t["terciles"][tt]["gap"],
                                                     t["terciles"][tt]["n"])
                                 for tt in ("T1_low", "T2_mid", "T3_high")
                                 if not t["terciles"][tt].get("skipped"))
                log("    %-14s overall N=%d rv=%.4f cand=%.4f gap=%+.4f | tally %d/%d | %s"
                    % (k, o["n"], o["median_err_rv"], o["median_err_cand"],
                       o["median_err_cand"] - o["median_err_rv"],
                       t["n_terciles_beaten"], t["n_terciles_evaluated"], gaps))

    elapsed = time.time() - t_start
    log("\n=== DONE in %.1fs ===" % elapsed)

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "panels": panels, "fit_tables": ft_paths,
        "fit_table_coverage": covs,
        "fit_results": fitres,
        "models": {k: {kk: (list(vv) if isinstance(vv, tuple) else vv) for kk, vv in m.items()}
                   for k, m in models.items()},
        "step1_reproduction": repro,
        "premium_column_reproduction": prem_checks,
        "ledger_meta": meta,
        "ledger_n_in_sample": df_ins.height, "ledger_n_liquid_ge5": n_liquid,
        "ledger_n_shadow_oos": df_shadow.height,
        "premium_telemetry": telem,
        "bars": {"M1": BAR_M1, "M2": BAR_M2, "M3": BAR_M3},
        "m1": m1_res, "m1_tallies": tallies, "m2_d10": m2_d10, "m2_d15": m2_d15, "a3": a3_res,
        "m3_published_carried_forward": m3_pub,
        "published_reference": {
            "m1_refined_liquid": {t: pub_refine["m1_refined"][t] for t in
                                  ("liquid|overall", "liquid|rv_T1_low", "liquid|rv_T2_mid",
                                   "liquid|rv_T3_high")},
            "m1_origf2_liquid": {t: pub_pertrade["results"]["m1"][t] for t in
                                 ("liquid|overall", "liquid|rv_T1_low", "liquid|rv_T2_mid",
                                  "liquid|rv_T3_high")},
            "a3_liquid": {t: pub_pertrade["results"]["a3"][t] for t in
                          ("liquid|overall", "liquid|rv_T1_low", "liquid|rv_T2_mid",
                           "liquid|rv_T3_high")},
            "m2_d10_liquid_overall": pub_refine["m2_d10_refined"]["liquid|overall"],
            "m2_d15_liquid_earnings_no": pub_refine["m2_d15_refined"]["liquid|earnings_no"],
        },
        "supplementary_real_polygon": supp,
        "elapsed_s": round(elapsed, 1),
    }
    with open(OUT_JSON, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(payload, f, indent=2, default=str)
    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote %s" % OUT_JSON)
    print("wrote %s" % OUT_TXT)


if __name__ == "__main__":
    main()
