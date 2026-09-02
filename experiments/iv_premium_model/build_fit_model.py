"""IV Premium Model -- RECON fit script (gameplan P2.3 residual path).

Lineage: experiments/gamma_iv_phaseb/ proved the gamma explosion is real but
could not dose a decisive verdict off the raw Polygon panel (19.3% overall /
10.7% on the decisive 2022-08 window, vs the pre-registered 60% bar) --
COVERAGE-BLOCKED. VERDICT.md's own escape hatch: fit a MODEL of atm_iv on the
panel's covered rows (real-IV truth), then apply the MODELED premium at 100%
dose engine-wide. This script builds that fit table and evaluates the model
ladder. It does NOT wire anything into monte_carlo.py / backtest_cascade.py --
recon + design only (see DESIGN.md).

Fit-table grain: (symbol, date) from .cache/polygon_iv/iv_ledger_polygon.parquet
(8,643 rows). Joins:
  (i)   engine-faithful realized_vol -- bit-for-bit port of
        monte_carlo.realized_vol(closes, base_idx, lookback=60) /
        backtest_cascade.realized_vol_pct (same formula, same VOL_LOOKBACK=60),
        recomputed from price_history so the model's input is EXACTLY what the
        engine feeds it at runtime (not the panel's own `vol_pct`, which comes
        from a different pipeline -- experiments/rel_strength/ -- of unverified
        lookback convention).
  (ii)  MarketRegime.vix_close (+ regime_composite) by date.
  (iii) MarketBreadth.mcclellan_oscillator by date (optional/secondary).

Read-only: MySQL SELECT only (price_history, market_regime, market_breadth).
No writes to MySQL, no engine edits, no commits, no queue submissions.

Outputs:
  experiments/iv_premium_model/fit_table.parquet   (derived cache; reproducible)
  experiments/iv_premium_model/fit_report.json
  experiments/iv_premium_model/fit_report.txt

Holdout: panel ends 2026-05-15 < CALIBRATION_CUTOFF_DATE (2026-06-15) --
enforced via experiments._holdout.assert_no_holdout_leak (should trivially
pass; enforced anyway per FABLE spec).

Fitting: numpy.linalg.lstsq only (no sklearn, no statsmodels).

Usage: python experiments/iv_premium_model/build_fit_model.py
ASCII-only stdout/output files (PYTHONUTF8=1).
"""
from __future__ import annotations

import os
import sys
import json
import math
import hashlib
from datetime import date as _date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

import numpy as np
import polars as pl

from experiments._holdout import assert_no_holdout_leak, CUTOFF  # noqa: E402
from database.trader_database import DB                          # noqa: E402

PANEL_PATH = os.path.join(_ROOT, ".cache", "polygon_iv", "iv_ledger_polygon.parquet")
FIT_TABLE_PATH = os.path.join(_HERE, "fit_table.parquet")
REPORT_JSON = os.path.join(_HERE, "fit_report.json")
REPORT_TXT = os.path.join(_HERE, "fit_report.txt")

VOL_LOOKBACK = 60           # monte_carlo.VOL_LOOKBACK / backtest_cascade.VOL_BARS (strategy_config.STRATEGY_30DTE.VOL_LOOKBACK)
PREMIUM_MULT_30DTE = 1.82   # strategy_config.STRATEGY_30DTE.PREMIUM_MULT -- engine's CURRENT fallback formula
PH_QUERY_START = "2022-03-01"   # >=100 trading days of buffer before panel's 2022-08-01 start
TIME_FOLD_SPLIT = "2025-01-01"  # train A: < this; test A: >= this (per mission: fit 22-08..24-12 / predict 25+)

FORM_NAMES = ["F0_rescale", "F1_affine_rv", "F2_affine_rv_vix", "F3_interaction"]
FORM_NPARAMS = {"F0_rescale": 1, "F1_affine_rv": 2, "F2_affine_rv_vix": 3, "F3_interaction": 4}


# ---------------------------------------------------------------------------
# Engine-faithful realized vol (bit-for-bit port)
# ---------------------------------------------------------------------------

def engine_realized_vol(closes, idx, lookback=VOL_LOOKBACK):
    """Port of monte_carlo.realized_vol(closes, base_idx, lookback) /
    backtest_cascade.realized_vol_pct (same formula: population stdev of
    simple daily returns over the trailing `lookback` bars ending at idx
    inclusive, *100). Returns None if insufficient history/valid returns."""
    if idx < lookback:
        return None
    rets = []
    for j in range(idx - lookback + 1, idx + 1):
        prev = closes[j - 1]
        if prev > 0:
            rets.append((closes[j] - prev) / prev)
    if len(rets) < lookback // 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100.0


def vix_band(v):
    if v is None:
        return None
    if v < 20.0:
        return "calm_lt20"
    if v < 26.0:
        return "slowbleed_20_26"
    if v < 28.0:
        return "elevated_26_28"
    return "panic_ge28"


def md5_fold(symbol, k=5):
    """Stable (process-independent) hash fold -- Python's str hash() is
    salt-randomized per process (backtest_cascade.py earnings-vega precedent)."""
    return int(hashlib.md5(symbol.encode("utf-8")).hexdigest(), 16) % k


# ---------------------------------------------------------------------------
# DB pulls (read-only, indexed)
# ---------------------------------------------------------------------------

def _to_date(d):
    return d if isinstance(d, _date) else _date.fromisoformat(str(d)[:10])


def fetch_price_history(symbols, start, end):
    placeholders = ",".join(["%s"] * len(symbols))
    sql = (f"SELECT symbol, date, close FROM price_history "
           f"WHERE symbol IN ({placeholders}) AND date BETWEEN %s AND %s "
           f"ORDER BY symbol, date")
    params = list(symbols) + [start, end]
    cur = DB.execute_sql(sql, params)
    rows = cur.fetchall()
    out = {}
    for sym, d, c in rows:
        if c is None:
            continue
        dd, cc = out.setdefault(sym, ([], []))
        dd.append(_to_date(d))
        cc.append(float(c))
    return out


def fetch_regime(start, end):
    cur = DB.execute_sql(
        "SELECT date, vix_close, regime_composite FROM market_regime "
        "WHERE date BETWEEN %s AND %s ORDER BY date", (start, end))
    out = {}
    for d, vix, comp in cur.fetchall():
        out[_to_date(d)] = (float(vix) if vix is not None else None,
                            float(comp) if comp is not None else None)
    return out


def fetch_breadth(start, end):
    cur = DB.execute_sql(
        "SELECT date, mcclellan_oscillator FROM market_breadth "
        "WHERE date BETWEEN %s AND %s ORDER BY date", (start, end))
    out = {}
    for d, mcc in cur.fetchall():
        out[_to_date(d)] = float(mcc) if mcc is not None else None
    return out


# ---------------------------------------------------------------------------
# Fit-table build
# ---------------------------------------------------------------------------

def build_fit_table():
    panel = pl.read_parquet(PANEL_PATH)
    n_panel = panel.height
    n_dupe = n_panel - panel.unique(subset=["symbol", "date"]).height

    symbols = panel["symbol"].unique().to_list()
    panel_min_date, panel_max_date = panel["date"].min(), panel["date"].max()

    print(f"[build] panel rows={n_panel} distinct_symbols={len(symbols)} "
          f"dupe_symbol_date_rows={n_dupe} date_range={panel_min_date}..{panel_max_date}")

    ph = fetch_price_history(symbols, PH_QUERY_START, panel_max_date)
    regime = fetch_regime(panel_min_date, panel_max_date)
    breadth = fetch_breadth(panel_min_date, panel_max_date)
    print(f"[build] price_history symbols_found={len(ph)}/{len(symbols)} "
          f"regime_days={len(regime)} breadth_days={len(breadth)}")

    rows = panel.to_dicts()
    out = []
    miss_reasons = {"symbol_not_in_ph": 0, "date_not_in_ph": 0,
                    "insufficient_bars": 0, "insufficient_valid_returns": 0}
    vix_miss = 0
    breadth_miss = 0

    for r in rows:
        sym = r["symbol"]
        d = _date.fromisoformat(r["date"])
        rv = None
        sym_ph = ph.get(sym)
        if sym_ph is None:
            miss_reasons["symbol_not_in_ph"] += 1
        else:
            dates_arr, closes_arr = sym_ph
            # exact match required (mirrors mc's `dates.index(signal_date)`)
            lo, hi = 0, len(dates_arr)
            idx = None
            # binary search for exact date (dates_arr sorted ascending)
            import bisect as _bisect
            pos = _bisect.bisect_left(dates_arr, d)
            if pos < len(dates_arr) and dates_arr[pos] == d:
                idx = pos
            if idx is None:
                miss_reasons["date_not_in_ph"] += 1
            elif idx < VOL_LOOKBACK:
                miss_reasons["insufficient_bars"] += 1
            else:
                rv = engine_realized_vol(closes_arr, idx, VOL_LOOKBACK)
                if rv is None:
                    miss_reasons["insufficient_valid_returns"] += 1

        vix, comp = regime.get(d, (None, None))
        if vix is None:
            vix_miss += 1
        mcc = breadth.get(d)
        if mcc is None:
            breadth_miss += 1

        out.append({
            "symbol": sym, "date": r["date"], "year": d.year,
            "dte": r["dte"], "atm_iv": r["atm_iv"],
            "panel_vol_pct": r["vol_pct"], "panel_iv_rv": r["iv_rv"],
            "overall": r["overall"],
            "engine_rv": rv, "vix_close": vix, "regime_composite": comp,
            "mcclellan": mcc, "vix_band": vix_band(vix),
        })

    df = pl.DataFrame(out)
    n_rv = df.filter(pl.col("engine_rv").is_not_null()).height
    n_vix = df.filter(pl.col("vix_close").is_not_null()).height
    n_mcc = df.filter(pl.col("mcclellan").is_not_null()).height
    n_eligible = df.filter(pl.col("engine_rv").is_not_null() & pl.col("vix_close").is_not_null()).height

    coverage = {
        "n_panel": n_panel, "n_dupe_symbol_date_rows": n_dupe,
        "n_rv_join_hit": n_rv, "rv_join_pct": round(100.0 * n_rv / n_panel, 2),
        "n_vix_join_hit": n_vix, "vix_join_pct": round(100.0 * n_vix / n_panel, 2),
        "n_mcclellan_join_hit": n_mcc, "mcclellan_join_pct": round(100.0 * n_mcc / n_panel, 2),
        "n_fit_eligible_rv_and_vix": n_eligible,
        "fit_eligible_pct": round(100.0 * n_eligible / n_panel, 2),
        "rv_miss_reasons": miss_reasons, "vix_miss": vix_miss, "breadth_miss": breadth_miss,
    }
    print(f"[build] RV join {n_rv}/{n_panel} ({coverage['rv_join_pct']}%) "
          f"VIX join {n_vix}/{n_panel} ({coverage['vix_join_pct']}%) "
          f"fit-eligible {n_eligible}/{n_panel} ({coverage['fit_eligible_pct']}%)")
    print(f"[build] RV miss reasons: {miss_reasons}")

    # Holdout enforcement (panel end 2026-05-15 < cutoff 2026-06-15; trivial pass expected)
    assert_no_holdout_leak(df.select(pl.col("date")), context="iv_premium_model fit table")
    print(f"[build] holdout check OK (cutoff={CUTOFF.isoformat()}, panel_max={panel_max_date})")

    df.write_parquet(FIT_TABLE_PATH)
    print(f"[build] wrote {FIT_TABLE_PATH} ({df.height} rows)")
    return df, coverage


# ---------------------------------------------------------------------------
# Ladder: design matrices + lstsq fit + metrics
# ---------------------------------------------------------------------------

def design_matrix(form, rv, vix):
    rv = np.asarray(rv, dtype=float)
    vix = np.asarray(vix, dtype=float)
    ones = np.ones_like(rv)
    if form == "F0_rescale":
        return np.column_stack([rv])
    if form == "F1_affine_rv":
        return np.column_stack([ones, rv])
    if form == "F2_affine_rv_vix":
        return np.column_stack([ones, rv, vix])
    if form == "F3_interaction":
        return np.column_stack([ones, rv, vix, rv * vix])
    raise ValueError(form)


def fit_form(form, rv, vix, y):
    X = design_matrix(form, rv, vix)
    coefs, _res, _rank, _sv = np.linalg.lstsq(X, np.asarray(y, dtype=float), rcond=None)
    return coefs


def predict_form(form, coefs, rv, vix):
    X = design_matrix(form, rv, vix)
    return X @ coefs


def r2_mae(y, pred):
    y = np.asarray(y, dtype=float)
    resid = y - pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    mae = float(np.mean(np.abs(resid)))
    return r2, mae


def coefs_to_dict(form, coefs):
    names = {"F0_rescale": ["k"], "F1_affine_rv": ["a", "b_rv"],
             "F2_affine_rv_vix": ["a", "b_rv", "c_vix"],
             "F3_interaction": ["a", "b_rv", "c_vix", "d_rv_vix"]}[form]
    return {n: float(c) for n, c in zip(names, coefs)}


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def main():
    df, coverage = build_fit_table()
    fit_df = df.filter(pl.col("engine_rv").is_not_null() & pl.col("vix_close").is_not_null())
    n = fit_df.height
    rv = fit_df["engine_rv"].to_numpy()
    vix = fit_df["vix_close"].to_numpy()
    y = fit_df["atm_iv"].to_numpy()
    dates = fit_df["date"].to_list()
    years = fit_df["year"].to_numpy()
    vbands = fit_df["vix_band"].to_list()

    report = {"coverage": coverage, "n_fit": n}

    # ---- In-sample ladder ----
    ladder = {}
    for form in FORM_NAMES:
        coefs = fit_form(form, rv, vix, y)
        pred = predict_form(form, coefs, rv, vix)
        r2, mae = r2_mae(y, pred)
        ladder[form] = {"n_params": FORM_NPARAMS[form], "coefs": coefs_to_dict(form, coefs),
                        "r2": r2, "mae": mae}
    report["ladder_in_sample"] = ladder
    print("\n[ladder] in-sample:")
    for form in FORM_NAMES:
        L = ladder[form]
        print(f"  {form:18s} n_params={L['n_params']} R2={L['r2']:.4f} MAE={L['mae']:.4f} coefs={L['coefs']}")

    # ---- OOF: time-fold ----
    is_train_a = np.array([d < TIME_FOLD_SPLIT for d in dates])
    is_test_a = ~is_train_a
    time_fold = {}
    for form in FORM_NAMES:
        c_fwd = fit_form(form, rv[is_train_a], vix[is_train_a], y[is_train_a])
        pred_fwd = predict_form(form, c_fwd, rv[is_test_a], vix[is_test_a])
        _, mae_fwd = r2_mae(y[is_test_a], pred_fwd)

        c_rev = fit_form(form, rv[is_test_a], vix[is_test_a], y[is_test_a])
        pred_rev = predict_form(form, c_rev, rv[is_train_a], vix[is_train_a])
        _, mae_rev = r2_mae(y[is_train_a], pred_rev)

        time_fold[form] = {
            "n_train_a": int(is_train_a.sum()), "n_test_a": int(is_test_a.sum()),
            "mae_fwd_fit_pre2025_predict_2025plus": mae_fwd,
            "coefs_fwd": coefs_to_dict(form, c_fwd),
            "mae_rev_fit_2025plus_predict_pre2025": mae_rev,
            "coefs_rev": coefs_to_dict(form, c_rev),
        }
    report["oof_time_fold"] = time_fold
    print("\n[oof time-fold] split =", TIME_FOLD_SPLIT,
          f"(train_a n={int(is_train_a.sum())}, test_a n={int(is_test_a.sum())})")
    for form in FORM_NAMES:
        T = time_fold[form]
        print(f"  {form:18s} MAE_fwd(<25predict25+)={T['mae_fwd_fit_pre2025_predict_2025plus']:.4f} "
              f"MAE_rev(25+predict<25)={T['mae_rev_fit_2025plus_predict_pre2025']:.4f}")

    # ---- OOF: symbol-fold (5-fold by md5 hash) ----
    symbols_arr = fit_df["symbol"].to_list()
    fold_id = np.array([md5_fold(s, 5) for s in symbols_arr])
    symbol_fold = {}
    for form in FORM_NAMES:
        pooled_resid = np.zeros(n)
        fold_coefs = []
        for k in range(5):
            test_mask = fold_id == k
            train_mask = ~test_mask
            c_k = fit_form(form, rv[train_mask], vix[train_mask], y[train_mask])
            pred_k = predict_form(form, c_k, rv[test_mask], vix[test_mask])
            pooled_resid[test_mask] = y[test_mask] - pred_k
            fold_coefs.append(coefs_to_dict(form, c_k))
        mae_cv = float(np.mean(np.abs(pooled_resid)))
        symbol_fold[form] = {"n_folds": 5, "mae_cv_pooled": mae_cv, "fold_coefs": fold_coefs}
    report["oof_symbol_fold"] = symbol_fold
    print("\n[oof symbol-fold] 5-fold by md5(symbol)%5:")
    for form in FORM_NAMES:
        print(f"  {form:18s} MAE_cv_pooled={symbol_fold[form]['mae_cv_pooled']:.4f}")

    # ---- Parsimony rule ----
    blended = {}
    for form in FORM_NAMES:
        T = time_fold[form]
        S = symbol_fold[form]
        blended[form] = float(np.mean([T["mae_fwd_fit_pre2025_predict_2025plus"],
                                        T["mae_rev_fit_2025plus_predict_pre2025"],
                                        S["mae_cv_pooled"]]))
    best_oof = min(blended.values())
    threshold = best_oof * 1.05
    chosen = None
    for form in FORM_NAMES:  # already in fewest-params-first order
        if blended[form] <= threshold:
            chosen = form
            break
    report["blended_oof_mae"] = blended
    report["best_oof_mae"] = best_oof
    report["parsimony_threshold_105pct"] = threshold
    report["chosen_form"] = chosen
    print(f"\n[parsimony] blended OOF MAE (mean of fwd/rev/symbol-fold): {blended}")
    print(f"[parsimony] best={best_oof:.4f} threshold(1.05x)={threshold:.4f} -> CHOSEN = {chosen}")

    # ---- Clamps (form-independent: computed once from the panel's own atm_iv) ----
    atm_iv_all = df["atm_iv"].to_numpy()  # full panel, not just fit-eligible (mission spec)
    p1, p50, p99 = np.quantile(atm_iv_all, [0.01, 0.50, 0.99])
    physical_floor, physical_cap = 0.08, 2.50   # 8%..250% annualized -- physically-motivated band
    report["clamps"] = {
        "panel_atm_iv_p1": float(p1), "panel_atm_iv_p50": float(p50), "panel_atm_iv_p99": float(p99),
        "physical_floor": physical_floor, "physical_cap": physical_cap,
        "proposed_floor": float(max(p1, physical_floor)),
        "proposed_cap": float(min(p99, physical_cap) if p99 > physical_cap else p99),
    }
    prop_floor = report["clamps"]["proposed_floor"]
    prop_cap = report["clamps"]["proposed_cap"]
    print(f"\n[clamps] panel atm_iv p1={p1:.4f} p50={p50:.4f} p99={p99:.4f}; "
          f"physical band [{physical_floor},{physical_cap}]; PROPOSED [{prop_floor:.4f}, {prop_cap:.4f}]")

    rv_t1, rv_t2 = np.quantile(rv, [1.0 / 3, 2.0 / 3])
    rv_tercile = np.where(rv <= rv_t1, "T1_low", np.where(rv <= rv_t2, "T2_mid", "T3_high"))
    rv_p25, rv_p50v, rv_p75 = np.quantile(rv, [0.25, 0.50, 0.75])
    vix_obs_max = float(np.max(vix))
    fit_rows_full = fit_df.to_dicts()
    idx_by_key = {(r["symbol"], r["date"]): i for i, r in enumerate(fit_rows_full)}

    def analyze_form(form, label):
        """Full per-form deep-dive: coefficient stability across fold variants,
        residual structure (year / VIX band / RV tercile), VIX extrapolation
        check (raw + clamped), sanity anchors, and the model-vs-RV premium
        ratio by RV tercile. Returns a dict; also prints an ASCII summary."""
        coefs_full = fit_form(form, rv, vix, y)
        full_coefs_d = coefs_to_dict(form, coefs_full)

        fold_variants = {
            "full_sample": full_coefs_d,
            "time_fwd_train": time_fold[form]["coefs_fwd"],
            "time_rev_train": time_fold[form]["coefs_rev"],
        }
        for k, fc in enumerate(symbol_fold[form]["fold_coefs"]):
            fold_variants[f"symbol_fold_{k}_train"] = fc
        stability = {}
        for p in full_coefs_d.keys():
            vals = [v[p] for v in fold_variants.values()]
            vmin, vmax = min(vals), max(vals)
            vmean = float(np.mean(vals))
            sign_flip = (vmin < 0 < vmax)
            rel_range = (vmax - vmin) / abs(vmean) if vmean != 0 else float("inf")
            stability[p] = {"min": vmin, "max": vmax, "mean": vmean,
                            "sign_flip": bool(sign_flip), "rel_range": rel_range,
                            "all_same_sign": bool(all(v > 0 for v in vals) or all(v < 0 for v in vals)),
                            "flag_unstable": bool(sign_flip or rel_range > 0.5)}
        print(f"\n[stability] {label} ({form}) coefficients across {len(fold_variants)} fold variants:")
        for p, s in stability.items():
            flag = " *** UNSTABLE (rel_range>0.5) ***" if s["flag_unstable"] else ""
            same_sign = " [always same sign]" if s["all_same_sign"] else " [SIGN FLIPS]"
            print(f"  {p:10s} min={s['min']:.5f} max={s['max']:.5f} mean={s['mean']:.5f} "
                  f"rel_range={s['rel_range']:.3f}{same_sign}{flag}")

        pred_full = predict_form(form, coefs_full, rv, vix)
        resid_full = y - pred_full
        by_year = {str(yr): {"n": int((years == yr).sum()),
                             "mean_resid": float(np.mean(resid_full[years == yr])),
                             "mae": float(np.mean(np.abs(resid_full[years == yr])))}
                   for yr in sorted(set(int(x) for x in years))}
        by_vixband = {}
        for b in ["calm_lt20", "slowbleed_20_26", "elevated_26_28", "panic_ge28"]:
            mask = np.array([vb == b for vb in vbands])
            if mask.sum() > 0:
                by_vixband[b] = {"n": int(mask.sum()), "mean_resid": float(np.mean(resid_full[mask])),
                                "mae": float(np.mean(np.abs(resid_full[mask])))}
        by_rv_tercile = {}
        for t in ["T1_low", "T2_mid", "T3_high"]:
            mask = rv_tercile == t
            by_rv_tercile[t] = {"n": int(mask.sum()), "mean_resid": float(np.mean(resid_full[mask])),
                                "mae": float(np.mean(np.abs(resid_full[mask])))}
        print(f"[residuals] {label} full-sample fit, by year:")
        for yr, s in by_year.items():
            print(f"  {yr}: n={s['n']:4d} mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}")
        print(f"[residuals] by VIX band:")
        for b, s in by_vixband.items():
            print(f"  {b:18s} n={s['n']:4d} mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}")
        print(f"[residuals] by RV tercile (cuts: {rv_t1:.3f}, {rv_t2:.3f}):")
        for t, s in by_rv_tercile.items():
            print(f"  {t:8s} n={s['n']:4d} mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}")

        extrap_rows = []
        for rv_scenario_name, rv_val in [("rv_p25", rv_p25), ("rv_p50", rv_p50v), ("rv_p75", rv_p75)]:
            for vix_val in [18.0, 30.0, 50.0, 80.0]:
                raw = float(predict_form(form, coefs_full, np.array([rv_val]), np.array([vix_val]))[0])
                clamped = min(max(raw, prop_floor), prop_cap)
                extrap_rows.append({"rv_scenario": rv_scenario_name, "rv_val": float(rv_val),
                                    "vix": vix_val, "raw_pred": raw, "clamped_pred": clamped,
                                    "beyond_panel_vix_range": bool(vix_val > vix_obs_max)})
        print(f"[extrapolation] {label}: panel VIX max observed = {vix_obs_max:.2f}; predictions (raw / clamped):")
        for row in extrap_rows:
            flagx = " [BEYOND PANEL RANGE]" if row["beyond_panel_vix_range"] else ""
            print(f"  RV={row['rv_val']:.3f} ({row['rv_scenario']}) VIX={row['vix']:.0f}: "
                  f"raw={row['raw_pred']:.4f} clamped={row['clamped_pred']:.4f}{flagx}")

        def model_iv(rv_val, vix_val):
            raw = float(predict_form(form, coefs_full, np.array([rv_val]), np.array([vix_val]))[0])
            return min(max(raw, prop_floor), prop_cap)

        def premium_ivmodel_pct(rv_val, vix_val, dte_val):
            return 0.4 * model_iv(rv_val, vix_val) * math.sqrt(dte_val / 365.0)

        def premium_rv_pct(rv_val):
            return PREMIUM_MULT_30DTE * rv_val / 100.0   # engine's CURRENT fallback, NOMINAL_CAL_DTE=30

        ratios = []
        for i, r in enumerate(fit_rows_full):
            pr_rv = premium_rv_pct(rv[i])
            ratios.append((premium_ivmodel_pct(rv[i], vix[i], r["dte"]) / pr_rv) if pr_rv > 0 else None)
        ratios_arr = np.array([x for x in ratios if x is not None])
        ratio_by_tercile = {}
        for t in ["T1_low", "T2_mid", "T3_high"]:
            mask = rv_tercile == t
            rvals = np.array([ratios[i] for i in range(n) if mask[i] and ratios[i] is not None])
            ratio_by_tercile[t] = {"n": int(len(rvals)), "mean_ratio": float(np.mean(rvals)),
                                  "median_ratio": float(np.median(rvals))}
        print(f"[premium ratio] {label}: model_premium/RV_premium overall mean={np.mean(ratios_arr):.3f} "
              f"median={np.median(ratios_arr):.3f}")
        for t, s in ratio_by_tercile.items():
            print(f"  {t:8s} n={s['n']:4d} mean_ratio={s['mean_ratio']:.3f} median_ratio={s['median_ratio']:.3f}")

        anchor_symbols_dates = [("ELF", "2022-08-04")]
        order = np.argsort(rv)
        extra_idx = [int(order[-1]), int(order[-2]), int(order[0]), int(order[1])]
        calm_2025_candidates = [i for i in range(n) if fit_rows_full[i]["date"].startswith("2025")
                                and vix[i] < 16 and rv[i] < np.quantile(rv, 0.3)]
        if calm_2025_candidates:
            extra_idx.append(calm_2025_candidates[0])
        extra_idx.append(int(np.argmax(y)))
        for sym, d in anchor_symbols_dates:
            i = idx_by_key.get((sym, d))
            if i is not None:
                extra_idx.insert(0, i)
        anchors = []
        seen = set()
        for i in extra_idx:
            if i in seen:
                continue
            seen.add(i)
            r = fit_rows_full[i]
            m_iv = model_iv(rv[i], vix[i])
            pr_iv = premium_ivmodel_pct(rv[i], vix[i], r["dte"])
            pr_rv = premium_rv_pct(rv[i])
            anchors.append({
                "symbol": r["symbol"], "date": r["date"], "dte": r["dte"],
                "engine_rv": float(rv[i]), "vix_close": float(vix[i]),
                "actual_atm_iv": float(r["atm_iv"]),
                "model_atm_iv_raw": float(predict_form(form, coefs_full, np.array([rv[i]]), np.array([vix[i]]))[0]),
                "model_atm_iv_clamped": float(m_iv),
                "premium_ivmodel_pct": float(pr_iv), "premium_rv_pct": float(pr_rv),
                "ratio_ivmodel_over_rv": float(pr_iv / pr_rv) if pr_rv > 0 else None,
            })
        print(f"[anchors] {label}: {len(anchors)} sanity rows:")
        for a in anchors:
            print(f"  {a['symbol']:6s} {a['date']} dte={a['dte']:3d} RV={a['engine_rv']:.3f} "
                  f"VIX={a['vix_close']:.2f} actual_iv={a['actual_atm_iv']:.4f} "
                  f"model_iv={a['model_atm_iv_clamped']:.4f} (raw={a['model_atm_iv_raw']:.4f}) "
                  f"prem_ivmodel={a['premium_ivmodel_pct']:.4f} prem_rv={a['premium_rv_pct']:.4f} "
                  f"ratio={a['ratio_ivmodel_over_rv']:.3f}")

        return {
            "form": form, "label": label, "full_sample_coefs": full_coefs_d,
            "coefficient_stability": {"variants": fold_variants, "stability": stability},
            "residual_structure": {"by_year": by_year, "by_vix_band": by_vixband,
                                   "rv_tercile_cuts": {"t1_cut": float(rv_t1), "t2_cut": float(rv_t2)},
                                   "by_rv_tercile": by_rv_tercile},
            "vix_extrapolation": {"panel_vix_max_observed": vix_obs_max, "rows": extrap_rows},
            "premium_ratio_ivmodel_over_rv": {
                "overall_mean": float(np.mean(ratios_arr)), "overall_median": float(np.median(ratios_arr)),
                "by_rv_tercile": ratio_by_tercile,
                "note": "premium_ivmodel uses the row's own contract dte (0.4*iv*sqrt(dte/365)); "
                        "premium_rv uses the engine's CURRENT 30DTE-calendar-hold fallback "
                        "(PREMIUM_MULT=1.82 * engine_rv/100, dte-invariant at NOMINAL_CAL_DTE=30).",
            },
            "sanity_anchors": anchors,
        }

    report["form_analysis"] = {
        "F0_rescale": analyze_form("F0_rescale", "MECHANICAL parsimony-rule winner"),
        "F2_affine_rv_vix": analyze_form("F2_affine_rv_vix", "RECOMMENDED (see rationale)"),
    }
    report["recommendation"] = {
        "mechanical_parsimony_winner": chosen,
        "recommended_for_wiring": "F2_affine_rv_vix",
        "rationale": [
            "F0's blended OOF MAE (0.1786) is within the pre-registered 5% band of F1's best "
            "(0.1742, threshold 0.1829), so it wins the literal parsimony rule as specified -- "
            "reported faithfully above. This recommendation section is a judgment call flagged "
            "for FABLE's decision, not a silent override.",
            "F0 has ZERO VIX-sensitivity by construction (k*RV only) -- its VIX-30/50/80 "
            "extrapolation table is flat/vacuous (identical prediction at every VIX level), which "
            "defeats the mandated extrapolation check (c) and forecloses the P1.4 vega-state "
            "pairing this workstream exists to support.",
            "F0's apparent OOF competitiveness is a fold-direction artifact, not evidence of a "
            "better model: F0's forward-fold MAE (0.1514) is its BEST cut but its reverse-fold "
            "MAE (0.2118) is its WORST and the WORST of any form -- a 40% forward-to-reverse "
            "degradation, vs F2's 11% (0.1728 to 0.1920) and F3's 10%. The deployment use case "
            "(pricing 2016-2022 history the panel never covered) is a BACKWARD extrapolation, "
            "structurally closer to the 'reverse' fold than the 'forward' one.",
            "F1's much higher in-sample R2 vs F0 (0.360 vs 0.175, +0.185 from ONE extra parameter) "
            "shows atm_iv has a real, large intercept (~0.26) that a proportional-through-origin "
            "form cannot capture -- confirmed by F0's residual-by-RV-tercile pattern (T1 low-RV "
            "+0.162 under-prediction shrinking to T3 high-RV -0.004): the correction this class of "
            "model applies is concentrated at LOW RV (raises cost for calm entries), not at HIGH "
            "RV as the gamma_iv_phaseb 'error cancellation' prior assumed. This reframes the "
            "expected mechanism of any future PASS: a broad premium markup (mostly at low-RV "
            "entries), not a big-mover-targeted one.",
            "F2's VIX coefficient (c_vix) is small and noisy in magnitude across folds (rel_range "
            "176%) but is POSITIVE in all 8 fold variants (never sign-flips) -- a real, "
            "directionally-robust market-wide-vol effect on single-name ATM IV beyond idiosyncratic "
            "RV, even though its size is uncertain.",
            "F2's own intercept 'a' IS unstable across folds (rel_range ~75%, same regime-drift "
            "root cause as F1's) -- this is a genuine data feature (IV/RV richness has declined "
            "2022-to-2025 across the whole panel, visible in every form WITH an intercept), not a "
            "defect specific to F2. Flagged, not swept under the rug.",
            "F0's own k (0.1531) is remarkably close to the CURRENT engine formula's implied "
            "proportionality constant (PREMIUM_MULT=1.82 -> effective k~0.159 via "
            "0.4*k*sqrt(30/365)=1.82/100): answering F0's own framing question 'is PREMIUM_MULT "
            "just mis-scaled?' -- essentially NO (~4% apart). A pure rescale would barely move the "
            "needle, which is itself evidence that the fix has to come from the intercept/VIX "
            "terms F0 lacks, not from a better constant.",
        ],
    }
    print("\n[recommendation] mechanical parsimony winner:", chosen,
          "| RECOMMENDED for wiring: F2_affine_rv_vix (see fit_report rationale)")

    # ---- Write reports ----
    with open(REPORT_JSON, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n[write] {REPORT_JSON}")

    write_txt_report(report)
    print(f"[write] {REPORT_TXT}")

    return report


def write_txt_report(report):
    lines = []
    lines.append("IV PREMIUM MODEL -- FIT REPORT (RECON, DRAFT)")
    lines.append("=" * 70)
    cov = report["coverage"]
    lines.append(f"Panel rows: {cov['n_panel']} (dupe symbol/date rows: {cov['n_dupe_symbol_date_rows']})")
    lines.append(f"RV join:   {cov['n_rv_join_hit']}/{cov['n_panel']} ({cov['rv_join_pct']}%)")
    lines.append(f"  miss reasons: {cov['rv_miss_reasons']}")
    lines.append(f"VIX join:  {cov['n_vix_join_hit']}/{cov['n_panel']} ({cov['vix_join_pct']}%) (miss={cov['vix_miss']})")
    lines.append(f"McClellan: {cov['n_mcclellan_join_hit']}/{cov['n_panel']} ({cov['mcclellan_join_pct']}%) (miss={cov['breadth_miss']})")
    lines.append(f"Fit-eligible (RV+VIX both hit): {cov['n_fit_eligible_rv_and_vix']}/{cov['n_panel']} ({cov['fit_eligible_pct']}%)")
    lines.append("")
    lines.append("LADDER (in-sample, n=%d)" % report["n_fit"])
    lines.append("-" * 70)
    lines.append(f"{'form':18s} {'params':>6s} {'R2':>8s} {'MAE':>8s}  coefs")
    for form in FORM_NAMES:
        L = report["ladder_in_sample"][form]
        lines.append(f"{form:18s} {L['n_params']:6d} {L['r2']:8.4f} {L['mae']:8.4f}  {L['coefs']}")
    lines.append("")
    lines.append("OOF -- time-fold (split=%s)" % TIME_FOLD_SPLIT)
    lines.append("-" * 70)
    for form in FORM_NAMES:
        T = report["oof_time_fold"][form]
        lines.append(f"{form:18s} MAE_fwd(fit<2025,predict>=2025)={T['mae_fwd_fit_pre2025_predict_2025plus']:.4f}  "
                    f"MAE_rev(fit>=2025,predict<2025)={T['mae_rev_fit_2025plus_predict_pre2025']:.4f}")
    lines.append("")
    lines.append("OOF -- symbol-fold (5-fold, md5(symbol)%5)")
    lines.append("-" * 70)
    for form in FORM_NAMES:
        S = report["oof_symbol_fold"][form]
        lines.append(f"{form:18s} MAE_cv_pooled={S['mae_cv_pooled']:.4f}")
    lines.append("")
    lines.append("PARSIMONY")
    lines.append("-" * 70)
    lines.append(f"blended OOF MAE (mean of fwd/rev/symbol-fold): {report['blended_oof_mae']}")
    lines.append(f"best={report['best_oof_mae']:.4f}  threshold(1.05x)={report['parsimony_threshold_105pct']:.4f}")
    lines.append(f"CHOSEN FORM = {report['chosen_form']}")
    lines.append("")
    lines.append("CLAMPS (form-independent, from panel's own atm_iv distribution)")
    lines.append("-" * 70)
    c = report["clamps"]
    lines.append(f"panel atm_iv p1={c['panel_atm_iv_p1']:.4f} p50={c['panel_atm_iv_p50']:.4f} p99={c['panel_atm_iv_p99']:.4f}")
    lines.append(f"physical band = [{c['physical_floor']}, {c['physical_cap']}]")
    lines.append(f"PROPOSED clamp = [{c['proposed_floor']:.4f}, {c['proposed_cap']:.4f}]")

    for form_key, FA in report["form_analysis"].items():
        lines.append("")
        lines.append(f"===== FORM DEEP-DIVE: {form_key}  ({FA['label']}) =====")
        lines.append("-" * 70)
        lines.append("Coefficient stability across 8 fold variants (full/time-fwd/time-rev/5x symbol-fold):")
        for p, s in FA["coefficient_stability"]["stability"].items():
            flag = " *** UNSTABLE (rel_range>0.5) ***" if s["flag_unstable"] else ""
            same_sign = " [always same sign]" if s["all_same_sign"] else " [SIGN FLIPS]"
            lines.append(f"  {p:10s} min={s['min']:.5f} max={s['max']:.5f} mean={s['mean']:.5f} "
                        f"rel_range={s['rel_range']:.3f}{same_sign}{flag}")
        rs = FA["residual_structure"]
        lines.append("Residuals by year:")
        for yr, s in rs["by_year"].items():
            lines.append(f"  {yr}: n={s['n']:4d} mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}")
        lines.append("Residuals by VIX band:")
        for b, s in rs["by_vix_band"].items():
            lines.append(f"  {b:18s} n={s['n']:4d} mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}")
        lines.append(f"Residuals by RV tercile (cuts {rs['rv_tercile_cuts']['t1_cut']:.3f}, {rs['rv_tercile_cuts']['t2_cut']:.3f}):")
        for t, s in rs["by_rv_tercile"].items():
            lines.append(f"  {t:8s} n={s['n']:4d} mean_resid={s['mean_resid']:+.4f} mae={s['mae']:.4f}")
        ve = FA["vix_extrapolation"]
        lines.append(f"VIX extrapolation check (panel VIX max observed = {ve['panel_vix_max_observed']:.2f}):")
        for row in ve["rows"]:
            flag = "  [BEYOND PANEL RANGE]" if row["beyond_panel_vix_range"] else ""
            lines.append(f"  RV={row['rv_val']:.3f} ({row['rv_scenario']}) VIX={row['vix']:.0f}: "
                        f"raw={row['raw_pred']:.4f} clamped={row['clamped_pred']:.4f}{flag}")
        pr = FA["premium_ratio_ivmodel_over_rv"]
        lines.append("Premium ratio: model(0.4*iv*sqrt(dte/365)) / RV-fallback(1.82*RV/100):")
        lines.append(f"  overall mean={pr['overall_mean']:.3f} median={pr['overall_median']:.3f}")
        lines.append(f"  note: {pr['note']}")
        for t, s in pr["by_rv_tercile"].items():
            lines.append(f"  {t:8s} n={s['n']:4d} mean_ratio={s['mean_ratio']:.3f} median_ratio={s['median_ratio']:.3f}")
        lines.append("Sanity anchors:")
        for a in FA["sanity_anchors"]:
            lines.append(f"  {a['symbol']:6s} {a['date']} dte={a['dte']:3d} RV={a['engine_rv']:.3f} "
                        f"VIX={a['vix_close']:.2f} actual_iv={a['actual_atm_iv']:.4f} "
                        f"model_iv={a['model_atm_iv_clamped']:.4f}(raw={a['model_atm_iv_raw']:.4f}) "
                        f"prem_ivmodel={a['premium_ivmodel_pct']:.4f} prem_rv={a['premium_rv_pct']:.4f} "
                        f"ratio={a['ratio_ivmodel_over_rv']:.3f}")

    lines.append("")
    lines.append("RECOMMENDATION (judgment call flagged for FABLE's decision)")
    lines.append("-" * 70)
    rec = report["recommendation"]
    lines.append(f"Mechanical parsimony-rule winner : {rec['mechanical_parsimony_winner']}")
    lines.append(f"Recommended for wiring            : {rec['recommended_for_wiring']}")
    for i, line in enumerate(rec["rationale"], 1):
        lines.append(f"  {i}. {line}")
    txt = "\n".join(lines) + "\n"
    with open(REPORT_TXT, "w", encoding="ascii", errors="backslashreplace") as f:
        f.write(txt)


if __name__ == "__main__":
    main()
