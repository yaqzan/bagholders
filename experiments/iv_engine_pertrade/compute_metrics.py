"""Per-trade F2-gamma engine validation -- METRIC PASSES (M1/M2/M3 + A3).

Foreground polars reads against the ledger built by build_ledger.py. No
MySQL. Implements experiments/iv_engine_pertrade/DESIGN.md section 4
(PRE-REGISTERED BARS, verbatim FABLE's) + AMENDMENTS A1-A3 (2026-07-12).

NUMBERS ONLY -- no pass/fail language emitted by this script. FABLE applies
the bars; this script's job is to make every number FABLE needs legible:
median errors, MAE, agreement/FNR, clustered-t paired differences, N-labels
(liquid-primary N + all-inclusive N), era split, band split, RV terciles,
A2 checkpoint-loss stats, A3 telemetry, and A1's projected-N-at-+60/+120-
trading-days line (a mechanical arithmetic projection, not a verdict).

AMENDMENTS A1 framing: every M1/M2/M3 cell is computed on BOTH strata --
liquid_ge5 (PRIMARY / deciding) and all-inclusive (robustness / context
only, carries the lastPrice-staleness caveat -- never substituted to force
a decision).

Usage: python experiments/iv_engine_pertrade/compute_metrics.py
Output: experiments/iv_engine_pertrade/results/pertrade_results.{json,txt}
ASCII-only.
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
_GEX_DIR = os.path.join(_ROOT, "experiments", "gex")
if _GEX_DIR not in sys.path:
    sys.path.insert(0, _GEX_DIR)

import numpy as np      # noqa: E402
import polars as pl     # noqa: E402

from gex_test import ols_with_clustered_se           # noqa: E402  -- reused VERBATIM, not reimplemented
from experiments._holdout import assert_no_holdout_leak, CUTOFF  # noqa: E402

LEDGER_PATH = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1.parquet")
META_PATH = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1_meta.json")
OUT_JSON = os.path.join(_HERE, "results", "pertrade_results.json")
OUT_TXT = os.path.join(_HERE, "results", "pertrade_results.txt")

MIN_CELL_N = 30          # reused from experiments/gex/gex_test.py
ARM_NAMES = ["rvconst", "rvgamma", "f2const", "f2gamma"]
PRIMARY_PAIR = ("f2gamma", "rvconst")   # candidate vs production, per DESIGN.md section 4

lines = []


def log(msg=""):
    print(msg, flush=True)
    lines.append(str(msg))


def skip_str(n):
    return "SKIPPED (N=%d)" % (0 if n is None else n)


def n_ok(n):
    return n is not None and n >= MIN_CELL_N


def clustered_paired(diff, dates):
    """Intercept-only OLS + date-clustered SE on a paired difference series
    (DESIGN.md section 4: 'ols_with_clustered_se(y=diff, X=<none>, groups=dates)').
    Returns dict(n, g, mean, se_clust, t_clust) or {'skipped': True, 'n': n}."""
    n = len(diff)
    if n < MIN_CELL_N:
        return {"skipped": True, "n": n}
    res = ols_with_clustered_se(np.asarray(diff, dtype=float), np.empty((n, 0)), np.asarray(dates))
    return {
        "skipped": False, "n": n, "n_clusters": res["g"],
        "mean_diff": float(res["beta"][0]), "se_clust": float(res["se_clust"][0]),
        "t_clust": float(res["t_clust"][0]),
    }


# ---------------------------------------------------------------------------
# M1 -- premium level
# ---------------------------------------------------------------------------
def m1_block(df, tag):
    sub = df.filter(
        pl.col("real_premium_pct").is_not_null() & (pl.col("real_premium_pct") > 0)
        & pl.col("premium_rv_pct").is_not_null() & pl.col("premium_f2_pct").is_not_null()
    )
    n = sub.height
    if not n_ok(n):
        return {"tag": tag, "n": n, "skipped": True}
    real = sub["real_premium_pct"].to_numpy().astype(float)
    rv_p = sub["premium_rv_pct"].to_numpy().astype(float)
    f2_p = sub["premium_f2_pct"].to_numpy().astype(float)
    dates = sub["date"].to_numpy()
    err_rv = np.abs(rv_p - real) / real
    err_f2 = np.abs(f2_p - real) / real
    clust = clustered_paired(err_f2 - err_rv, dates)
    return {
        "tag": tag, "n": n, "skipped": False,
        "median_err_rv": float(np.median(err_rv)), "median_err_f2": float(np.median(err_f2)),
        "mean_err_rv": float(np.mean(err_rv)), "mean_err_f2": float(np.mean(err_f2)),
        "f2_beats_rv_median": bool(np.median(err_f2) < np.median(err_rv)),
        "paired_diff_clust": clust,
    }


# ---------------------------------------------------------------------------
# M2 -- P&L fidelity at a checkpoint (d10 / d15)
# ---------------------------------------------------------------------------
def m2_block(df, checkpoint, tag):
    real_col = f"real_pnl_d{checkpoint}"
    base = df.filter(pl.col(real_col).is_not_null())
    n_base = base.height
    result = {"tag": tag, "checkpoint": checkpoint, "n_base": n_base, "arms": {}}
    if not n_ok(n_base):
        result["skipped"] = True
        return result
    result["skipped"] = False
    for arm in ARM_NAMES:
        pred_col = f"pred_pnl_d{checkpoint}_{arm}"
        arm_sub = base.filter(pl.col(pred_col).is_not_null())
        n = arm_sub.height
        if not n_ok(n):
            result["arms"][arm] = {"n": n, "skipped": True}
            continue
        pred = arm_sub[pred_col].to_numpy().astype(float)
        real = arm_sub[real_col].to_numpy().astype(float)
        ae = np.abs(pred - real)
        result["arms"][arm] = {"n": n, "mae": float(np.mean(ae)), "bias": float(np.mean(pred - real))}

    pf2_col, prv_col = f"pred_pnl_d{checkpoint}_f2gamma", f"pred_pnl_d{checkpoint}_rvconst"
    pair_sub = base.filter(pl.col(pf2_col).is_not_null() & pl.col(prv_col).is_not_null())
    n_pair = pair_sub.height
    if n_ok(n_pair):
        real = pair_sub[real_col].to_numpy().astype(float)
        pf2 = pair_sub[pf2_col].to_numpy().astype(float)
        prv = pair_sub[prv_col].to_numpy().astype(float)
        ae_f2 = np.abs(pf2 - real)
        ae_rv = np.abs(prv - real)
        dates = pair_sub["date"].to_numpy()
        clust = clustered_paired(ae_f2 - ae_rv, dates)
        clust["f2gamma_beats_rvconst"] = bool(clust.get("mean_diff", 0) < 0) if not clust.get("skipped") else None
        result["pair_f2gamma_vs_rvconst"] = clust
    else:
        result["pair_f2gamma_vs_rvconst"] = {"skipped": True, "n": n_pair}
    return result


# ---------------------------------------------------------------------------
# M3 -- decision fidelity (TP-hit agreement, SL-hit FNR), ripe15==True only
# ---------------------------------------------------------------------------
def m3_block(df, tag):
    base = df.filter(pl.col("ripe15") == True)  # noqa: E712
    n_base = base.height
    result = {"tag": tag, "n_base": n_base, "arms": {}}
    if not n_ok(n_base):
        result["skipped"] = True
        return result
    result["skipped"] = False

    for arm in ARM_NAMES:
        tp_col, sl_col = f"pred_tp_hit_{arm}", f"pred_sl_hit_{arm}"
        agree_sub = base.filter(pl.col(tp_col).is_not_null() & pl.col("real_tp_hit").is_not_null())
        n_agree = agree_sub.height
        agreement = None
        if n_ok(n_agree):
            agree_vec = (agree_sub[tp_col] == agree_sub["real_tp_hit"]).to_numpy().astype(float)
            agreement = float(np.mean(agree_vec))

        sl_true_sub = base.filter((pl.col("real_sl_hit") == True) & pl.col(sl_col).is_not_null())  # noqa: E712
        n_sl_true = sl_true_sub.height
        fnr = None
        if n_ok(n_sl_true):
            fnr = float(np.mean((~sl_true_sub[sl_col]).to_numpy().astype(bool)))
        result["arms"][arm] = {
            "n_agreement": n_agree, "agreement": agreement,
            "n_sl_true": n_sl_true, "sl_fnr": fnr,
        }

    # Clustered comparison, primary pair: per-trade agreement-indicator diff + FN-indicator diff.
    a1, a2 = PRIMARY_PAIR
    pair_sub = base.filter(
        pl.col(f"pred_tp_hit_{a1}").is_not_null() & pl.col(f"pred_tp_hit_{a2}").is_not_null()
        & pl.col("real_tp_hit").is_not_null()
    )
    n_pair = pair_sub.height
    if n_ok(n_pair):
        agree_a1 = (pair_sub[f"pred_tp_hit_{a1}"] == pair_sub["real_tp_hit"]).to_numpy().astype(float)
        agree_a2 = (pair_sub[f"pred_tp_hit_{a2}"] == pair_sub["real_tp_hit"]).to_numpy().astype(float)
        dates = pair_sub["date"].to_numpy()
        clust = clustered_paired(agree_a1 - agree_a2, dates)
        clust["f2gamma_beats_rvconst"] = bool(clust.get("mean_diff", 0) > 0) if not clust.get("skipped") else None
        result["pair_agreement_f2gamma_vs_rvconst"] = clust
    else:
        result["pair_agreement_f2gamma_vs_rvconst"] = {"skipped": True, "n": n_pair}

    sl_pair_sub = base.filter(
        (pl.col("real_sl_hit") == True) & pl.col(f"pred_sl_hit_{a1}").is_not_null()  # noqa: E712
        & pl.col(f"pred_sl_hit_{a2}").is_not_null()
    )
    n_sl_pair = sl_pair_sub.height
    if n_ok(n_sl_pair):
        fn_a1 = (~sl_pair_sub[f"pred_sl_hit_{a1}"]).to_numpy().astype(float)
        fn_a2 = (~sl_pair_sub[f"pred_sl_hit_{a2}"]).to_numpy().astype(float)
        dates = sl_pair_sub["date"].to_numpy()
        clust = clustered_paired(fn_a1 - fn_a2, dates)
        clust["f2gamma_not_worse"] = bool(clust.get("mean_diff", 0) <= 0) if not clust.get("skipped") else None
        result["pair_slfnr_f2gamma_vs_rvconst"] = clust
    else:
        result["pair_slfnr_f2gamma_vs_rvconst"] = {"skipped": True, "n": n_sl_pair}
    return result


# ---------------------------------------------------------------------------
# A3 -- telemetry: implied ATM constant + F2-vs-real premium bias by tercile
# ---------------------------------------------------------------------------
def a3_block(df, tag):
    sub = df.filter(
        pl.col("real_premium_pct").is_not_null() & (pl.col("real_premium_pct") > 0)
        & pl.col("entry_iv").is_not_null() & (pl.col("entry_iv") > 0)
        & pl.col("dte_at_entry").is_not_null()
    )
    n = sub.height
    if not n_ok(n):
        return {"tag": tag, "n": n, "skipped": True}
    real_pct = sub["real_premium_pct"].to_numpy().astype(float)
    iv = sub["entry_iv"].to_numpy().astype(float)
    dte = sub["dte_at_entry"].to_numpy().astype(float)
    k_implied = real_pct / (iv * np.sqrt(dte / 365.0))
    f2_p = sub["premium_f2_pct"].to_numpy().astype(float)
    signed_bias_f2 = (f2_p - real_pct) / real_pct
    return {
        "tag": tag, "n": n, "skipped": False,
        "k_implied_median": float(np.median(k_implied)), "k_implied_mean": float(np.mean(k_implied)),
        "k_assumed": 0.4,
        "f2_signed_bias_median": float(np.median(signed_bias_f2)), "f2_signed_bias_mean": float(np.mean(signed_bias_f2)),
    }


# ---------------------------------------------------------------------------
# Driver: apply the same cut-matrix to M1 / M2(d10) / M2(d15) / M3 / A3,
# each on liquid_ge5 (primary) and all-inclusive (robustness) strata.
# ---------------------------------------------------------------------------
def run_all_cuts(df_insample):
    out = {"m1": {}, "m2_d10": {}, "m2_d15": {}, "m3": {}, "a3": {}}

    def cuts_for(stratum_df, stratum_name):
        blocks = {
            "overall": stratum_df,
        }
        for tercile in ("T1_low", "T2_mid", "T3_high"):
            blocks[f"rv_{tercile}"] = stratum_df.filter(pl.col("rv_tercile") == tercile)
        for band in ("75+", "70-74"):
            blocks[f"band_{band}"] = stratum_df.filter(pl.col("band") == band)
        for era in ("2025", "2026H1"):
            blocks[f"era_{era}"] = stratum_df.filter(pl.col("era") == era)
        for flag, label in ((True, "earnings_yes"), (False, "earnings_no")):
            blocks[label] = stratum_df.filter(pl.col("earnings_span_flag") == flag)
        return {f"{stratum_name}|{k}": v for k, v in blocks.items()}

    liquid_df = df_insample.filter(pl.col("liquid_ge5") == True)   # noqa: E712
    all_df = df_insample

    all_cut_sets = {}
    all_cut_sets.update(cuts_for(liquid_df, "liquid"))
    all_cut_sets.update(cuts_for(all_df, "all_inclusive"))

    for tag, sub in all_cut_sets.items():
        out["m1"][tag] = m1_block(sub, tag)
        out["m2_d10"][tag] = m2_block(sub, 10, tag)
        out["m2_d15"][tag] = m2_block(sub, 15, tag)
        out["m3"][tag] = m3_block(sub, tag)
        out["a3"][tag] = a3_block(sub, tag)

    return out


# ---------------------------------------------------------------------------
# A2 -- checkpoint-loss / freshness diagnostics
# ---------------------------------------------------------------------------
def a2_report(df_insample):
    out = {}
    for stratum_name, sdf in (("liquid", df_insample.filter(pl.col("liquid_ge5") == True)),  # noqa: E712
                              ("all_inclusive", df_insample)):
        n = sdf.height
        stale = sdf["stale_frac"].drop_nulls().to_numpy()
        cell = {"n": n, "n_with_stale_frac": len(stale)}
        if len(stale):
            cell["stale_frac_mean"] = float(np.mean(stale))
            cell["stale_frac_p25_p50_p75"] = [float(x) for x in np.percentile(stale, [25, 50, 75])]
        for cp in (5, 10, 15):
            exist = sdf.filter(pl.col(f"real_premium_d{cp}").is_not_null())
            n_exist = exist.height
            if n_exist:
                n_not_fresh = exist.filter(pl.col(f"d{cp}_fresh") == False).height  # noqa: E712
                cell[f"d{cp}_checkpoint_exists_n"] = n_exist
                cell[f"d{cp}_stale_frac_of_existing"] = round(n_not_fresh / n_exist, 4)
            else:
                cell[f"d{cp}_checkpoint_exists_n"] = 0
                cell[f"d{cp}_stale_frac_of_existing"] = None
        out[stratum_name] = cell
    return out


# ---------------------------------------------------------------------------
# A1 -- projected N at +60 / +120 trading days (mechanical projection only)
# ---------------------------------------------------------------------------
def a1_projection(df_all, n_matched_liquid_insample):
    n_trading_days = df_all.filter(~pl.col("is_shadow_oos"))["date"].n_unique()
    rate = n_matched_liquid_insample / n_trading_days if n_trading_days else 0.0
    return {
        "observed_trading_days_with_any_signal": n_trading_days,
        "liquid_matched_n_now": n_matched_liquid_insample,
        "liquid_signals_per_trading_day": round(rate, 4),
        "projected_liquid_n_plus60td": round(n_matched_liquid_insample + rate * 60, 1),
        "projected_liquid_n_plus120td": round(n_matched_liquid_insample + rate * 120, 1),
        "note": "steady-state extrapolation of the observed liquid-match rate; mechanical arithmetic, not a verdict.",
    }


# ---------------------------------------------------------------------------
# Text rendering helpers
# ---------------------------------------------------------------------------
def fmt_clust(c):
    if c is None:
        return "n/a"
    if c.get("skipped"):
        return skip_str(c.get("n"))
    return f"diff={c['mean_diff']:+.4f} se={c['se_clust']:.4f} t={c['t_clust']:+.2f} N={c['n']} G={c['n_clusters']}"


def render_m1(m1, tag):
    b = m1.get(tag)
    if b is None or b.get("skipped"):
        log(f"  [{tag}] {skip_str(b.get('n') if b else None)}")
        return
    log(f"  [{tag}] N={b['n']}  median|err| rv={b['median_err_rv']:.4f} f2={b['median_err_f2']:.4f} "
        f"(f2<rv median: {b['f2_beats_rv_median']})  mean|err| rv={b['mean_err_rv']:.4f} f2={b['mean_err_f2']:.4f}  "
        f"paired-diff(f2-rv) clust: {fmt_clust(b['paired_diff_clust'])}")


def render_m2(m2, tag):
    b = m2.get(tag)
    if b is None or b.get("skipped"):
        log(f"  [{tag}] d{list(m2.values())[0].get('checkpoint','?') if m2 else '?'}: {skip_str(b.get('n_base') if b else None)}")
        return
    parts = []
    for arm in ARM_NAMES:
        a = b["arms"].get(arm, {})
        if a.get("skipped"):
            parts.append(f"{arm}={skip_str(a.get('n'))}")
        else:
            parts.append(f"{arm} MAE={a['mae']:.4f}(N={a['n']})")
    log(f"  [{tag}] d{b['checkpoint']} N_base={b['n_base']}  " + "  ".join(parts))
    log(f"      pair(f2gamma-rvconst) MAE-diff clust: {fmt_clust(b['pair_f2gamma_vs_rvconst'])}")


def render_m3(m3, tag):
    b = m3.get(tag)
    if b is None or b.get("skipped"):
        log(f"  [{tag}] {skip_str(b.get('n_base') if b else None)}")
        return
    for arm in ARM_NAMES:
        a = b["arms"].get(arm, {})
        agr = f"{a['agreement']:.3f}" if a.get("agreement") is not None else skip_str(a.get("n_agreement"))
        fnr = f"{a['sl_fnr']:.3f}" if a.get("sl_fnr") is not None else skip_str(a.get("n_sl_true"))
        log(f"      {arm:8s} TP-agreement={agr} (N={a['n_agreement']})  SL-FNR={fnr} (N_real_sl={a['n_sl_true']})")
    log(f"      pair agreement-diff(f2gamma-rvconst) clust: {fmt_clust(b['pair_agreement_f2gamma_vs_rvconst'])}")
    log(f"      pair SL-FNR-diff(f2gamma-rvconst) clust: {fmt_clust(b['pair_slfnr_f2gamma_vs_rvconst'])}")


def render_a3(a3, tag):
    b = a3.get(tag)
    if b is None or b.get("skipped"):
        log(f"  [{tag}] {skip_str(b.get('n') if b else None)}")
        return
    log(f"  [{tag}] N={b['n']}  k_implied median={b['k_implied_median']:.4f} mean={b['k_implied_mean']:.4f} "
        f"(assumed k=0.4)  f2-vs-real signed bias median={b['f2_signed_bias_median']:+.4f} mean={b['f2_signed_bias_mean']:+.4f}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    log("=== iv_engine_pertrade METRIC PASSES (M1/M2/M3 + A3, AMENDMENTS A1/A2) ===")
    log(f"ledger: {LEDGER_PATH}")

    df = pl.read_parquet(LEDGER_PATH)
    with open(META_PATH, "r", encoding="ascii") as f:
        meta = json.load(f)
    log(f"ledger rows: {df.height}  cols: {len(df.columns)}")
    log(f"built_at={meta.get('built_at')}  version_id={meta.get('version_id')}  cutoff={meta.get('cutoff')}")

    df_insample = df.filter(~pl.col("is_shadow_oos"))
    df_shadow = df.filter(pl.col("is_shadow_oos"))
    assert_no_holdout_leak(df_insample, context="iv_engine_pertrade M1-M3 (in-sample slice)")
    log(f"holdout guard: PASSED (cutoff={CUTOFF.isoformat()}); in-sample N={df_insample.height}  shadow_oos(watch-only) N={df_shadow.height}")

    n_liquid = df_insample.filter(pl.col("liquid_ge5") == True).height  # noqa: E712
    n_all = df_insample.height
    log(f"liquid_ge5 (PRIMARY stratum): N={n_liquid}   all-inclusive (robustness): N={n_all}")

    log("\n--- A1: projected N (mechanical projection, liquid-primary) ---")
    proj = a1_projection(df, n_liquid)
    log(f"  observed trading days with any signal: {proj['observed_trading_days_with_any_signal']}")
    log(f"  liquid-matched signals/trading-day: {proj['liquid_signals_per_trading_day']}")
    log(f"  liquid N now={n_liquid}  projected +60td={proj['projected_liquid_n_plus60td']}  "
        f"projected +120td={proj['projected_liquid_n_plus120td']}")

    log("\n--- A2: checkpoint-loss / freshness diagnostics ---")
    a2 = a2_report(df_insample)
    for stratum, cell in a2.items():
        log(f"  [{stratum}] N={cell['n']}  stale_frac: N_with_value={cell['n_with_stale_frac']} "
            f"mean={cell.get('stale_frac_mean')} p25/p50/p75={cell.get('stale_frac_p25_p50_p75')}")
        for cp in (5, 10, 15):
            log(f"      d{cp}: checkpoint_exists_N={cell[f'd{cp}_checkpoint_exists_n']} "
                f"stale_frac_of_existing={cell[f'd{cp}_stale_frac_of_existing']}")

    log("\n--- running M1 / M2(d10) / M2(d15) / M3 / A3 across the full cut matrix ---")
    results = run_all_cuts(df_insample)

    log("\n=== M1 (premium level: median |model-real|/real) ===")
    for tag in sorted(results["m1"].keys()):
        if tag.startswith("liquid|"):
            render_m1(results["m1"], tag)
    log("\n  -- all-inclusive (robustness / context only; lastPrice-staleness caveat applies) --")
    for tag in sorted(results["m1"].keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_"):
            render_m1(results["m1"], tag)

    log("\n=== M2 (P&L fidelity: MAE at d10) ===")
    for tag in sorted(results["m2_d10"].keys()):
        if tag.startswith("liquid|"):
            render_m2(results["m2_d10"], tag)
    log("\n  -- all-inclusive (robustness) --")
    for tag in sorted(results["m2_d10"].keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_"):
            render_m2(results["m2_d10"], tag)

    log("\n=== M2 (P&L fidelity: MAE at d15) ===")
    for tag in sorted(results["m2_d15"].keys()):
        if tag.startswith("liquid|"):
            render_m2(results["m2_d15"], tag)
    log("\n  -- all-inclusive (robustness) --")
    for tag in sorted(results["m2_d15"].keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_"):
            render_m2(results["m2_d15"], tag)

    log("\n=== M3 (decision fidelity: TP-hit agreement, SL-hit FNR; ripe15==True) ===")
    for tag in sorted(results["m3"].keys()):
        if tag.startswith("liquid|"):
            log(f"  [{tag}]")
            render_m3(results["m3"], tag)
    log("\n  -- all-inclusive (robustness) --")
    for tag in sorted(results["m3"].keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_"):
            log(f"  [{tag}]")
            render_m3(results["m3"], tag)

    log("\n=== A3 (telemetry: implied ATM constant + F2-vs-real premium bias) ===")
    for tag in sorted(results["a3"].keys()):
        if tag.startswith("liquid|"):
            render_a3(results["a3"], tag)
    log("\n  -- all-inclusive (robustness) --")
    for tag in sorted(results["a3"].keys()):
        if tag.startswith("all_inclusive|overall") or tag.startswith("all_inclusive|rv_"):
            render_a3(results["a3"], tag)

    elapsed = time.time() - t0
    log(f"\n=== DONE in {elapsed:.1f}s ===")

    full_json = {
        "meta": meta,
        "in_sample_n": df_insample.height, "shadow_oos_n": df_shadow.height,
        "liquid_ge5_n": n_liquid, "all_inclusive_n": n_all,
        "a1_projection": proj, "a2_freshness": a2,
        "results": results,
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
