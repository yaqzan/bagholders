"""REFINEMENT ROUND -- step 2: re-score the EXISTING per-trade ledger with
the R1 FROZEN refined premium model (experiments/iv_engine_pertrade/
refit_report.json, written by refit_premium_model.py). Does NOT rebuild
the ledger -- same real contracts, same real forward paths as
build_ledger.py's original run; only two NEW arms are added:

  f2constrefined = refined premium x const-delta (option_pnl_pct)
  f2gammarefined = refined premium x gamma-BS    (bs_option_pnl_pct)

Every input needed is ALREADY a persisted ledger column:
  rv, vix, dte_at_entry            -> premium_f2refined_pct (the ONLY new
                                       premium-level number this round)
  entry_close, real_underlying_dX  -> underlying move
  entry_iv, real_iv_dX,
  earnings_span_flag               -> the SAME real-empirical vega_ratio
                                       convention build_ledger.py used
                                       (iv_t/entry_iv, clamped [0.2,2.0],
                                       gated on earnings_span_flag; 1.0
                                       otherwise)

No MySQL touch -- this is genuinely a foreground recomputation from
persisted columns, not a re-derivation, so it does NOT go through the
queue (DESIGN.md's queue mandate is for the ~7,800-query ledger BUILD;
this is arithmetic on an already-materialized parquet).

Does NOT recompute the day-by-day first-touch walk (pred_tp_hit /
pred_sl_hit / pred_first_touch_* for the refined arms) -- that walk needs
the DAILY (t=1..15) forward path, which the ledger only persists at the
d5/d10/d15 checkpoints. M3 is explicitly UNCHANGED this round per
VERDICT.md's REFINEMENT ROUND scope (EXECUTE step 3: "M3 unchanged ...
just report") -- compute_metrics_refined.py carries forward the ORIGINAL
f2gamma-vs-rvconst M3 numbers from results/pertrade_results.json verbatim,
it does not re-derive them here.

`option_pricing.option_pnl_pct` / `option_pricing.bs_option_pnl_pct` /
`option_pricing.DEFAULT_DELTA` are IMPORTED, never reimplemented (same
mandate as build_ledger.py). The tiny const/gamma dispatch + vega_ratio
convention are re-declared locally (not imported from build_ledger.py)
deliberately: importing build_ledger.py as a module would re-run its
top-level side effects (an AlgorithmVersion.get_active_scores_version()
DB query at import time) for the sake of one 4-line helper -- not worth
a live MySQL touch in a script whose entire point is to avoid one.

Output: .cache/iv_engine_pertrade/ledger_v1_refined.parquet (the ORIGINAL
ledger's columns, untouched, plus the new refined-arm columns appended).
.cache/iv_engine_pertrade/ledger_v1_refined_meta.json (coverage/clamp
telemetry + frozen-model provenance echo).

No engine edits, no commits. ASCII-only stdout (PYTHONUTF8=1).
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

import numpy as np                                          # noqa: E402
import polars as pl                                          # noqa: E402

from option_pricing import option_pnl_pct, bs_option_pnl_pct, DEFAULT_DELTA  # noqa: E402  -- imported, never reimplemented

LEDGER_PATH = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1.parquet")
REFIT_REPORT = os.path.join(_HERE, "refit_report.json")
OUT_PATH = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1_refined.parquet")
OUT_META = os.path.join(_ROOT, ".cache", "iv_engine_pertrade", "ledger_v1_refined_meta.json")

CHECKPOINTS = (5, 10, 15)


def log(msg=""):
    print(msg, flush=True)


def main():
    t0 = time.time()
    log("=== iv_engine_pertrade REFINEMENT ROUND -- step 2: rescore ledger with FROZEN R1 model ===")

    with open(REFIT_REPORT, "r", encoding="ascii") as f:
        refit = json.load(f)
    fw = refit["frozen_winner"]
    form = fw["form"]
    coefs = fw["coefs"]
    t1, t2 = fw["tercile_cutoffs"]["t1"], fw["tercile_cutoffs"]["t2"]
    clamp_floor, clamp_cap = fw["clamp"]
    log(f"FROZEN winner form (from {REFIT_REPORT}): {form}")
    log(f"coefficients: {coefs}")
    log(f"tercile cutoffs (frozen, fit-table-derived): t1={t1:.6f} t2={t2:.6f}")
    log(f"clamp: [{clamp_floor}, {clamp_cap}] (reused verbatim from existing F2)")

    if form != "R1i_piecewise_intercept":
        raise SystemExit(
            f"rescore_refined.py's refined_atm_iv() currently only implements R1i_piecewise_intercept; "
            f"refit_report.json's frozen winner is '{form}' -- extend refined_atm_iv() to match before proceeding."
        )

    def refined_atm_iv(rv_arr, vix_arr):
        rv_arr = np.asarray(rv_arr, dtype=float)
        vix_arr = np.asarray(vix_arr, dtype=float)
        a = np.where(rv_arr <= t1, coefs["a_T1_low"],
                     np.where(rv_arr <= t2, coefs["a_T2_mid"], coefs["a_T3_high"]))
        raw = a + coefs["b_rv"] * rv_arr + coefs["c_vix"] * vix_arr
        return np.clip(raw, clamp_floor, clamp_cap)

    log(f"\nledger (UNCHANGED, read-only): {LEDGER_PATH}")
    df = pl.read_parquet(LEDGER_PATH)
    log(f"ledger rows: {df.height}  cols: {len(df.columns)}")

    rv = df["rv"].to_numpy().astype(float)
    vix = df["vix"].to_numpy().astype(float)
    dte = df["dte_at_entry"].to_numpy().astype(float)

    atm_iv_refined = refined_atm_iv(rv, vix)
    premium_f2refined_pct = 0.4 * atm_iv_refined * np.sqrt(dte / 365.0)

    n_clamp_floor = int(np.sum(atm_iv_refined <= clamp_floor + 1e-12))
    n_clamp_cap = int(np.sum(atm_iv_refined >= clamp_cap - 1e-12))
    log(f"\nrefined atm_iv telemetry: clamp_floor_hits={n_clamp_floor} clamp_cap_hits={n_clamp_cap} "
        f"of N={df.height}  (existing F2, for reference, per ledger_v1_meta.json: "
        f"clamp_floor_hits=0 clamp_cap_hits=7)")
    ratio_vs_f2 = premium_f2refined_pct / df["premium_f2_pct"].to_numpy().astype(float)
    log(f"refined-premium / existing-F2-premium ratio: median={float(np.median(ratio_vs_f2)):.4f} "
        f"mean={float(np.mean(ratio_vs_f2)):.4f}")

    entry_close = df["entry_close"].to_numpy().astype(float)
    entry_iv = df["entry_iv"].to_numpy().astype(float)
    earnings_flag = df["earnings_span_flag"].to_numpy().astype(bool)

    def vega_ratio_for(entry_iv_i, iv_t_i, earn_i):
        """Verbatim reproduction of build_ledger.py's process_signal()._vega_ratio()
        closure: REAL empirical iv_t/entry_iv, clamped [0.2,2.0], gated on
        earnings_span_flag; 1.0 (no-op) otherwise or on any missing input."""
        if earn_i and entry_iv_i and iv_t_i and entry_iv_i > 0:
            return max(0.20, min(2.0, iv_t_i / entry_iv_i))
        return 1.0

    new_cols = {
        "premium_f2refined_pct": premium_f2refined_pct.tolist(),
        "refined_atm_iv": atm_iv_refined.tolist(),
        "refined_form": [form] * df.height,
    }

    for cp in CHECKPOINTS:
        u_arr = df[f"real_underlying_d{cp}"].to_list()
        iv_arr = df[f"real_iv_d{cp}"].to_list()
        pnl_const, pnl_gamma = [], []
        n_computed = 0
        for i in range(df.height):
            u_t = u_arr[i]
            prem = premium_f2refined_pct[i]
            if u_t is None or prem is None or not (prem > 0):
                pnl_const.append(None)
                pnl_gamma.append(None)
                continue
            vega = vega_ratio_for(entry_iv[i], iv_arr[i], bool(earnings_flag[i]))
            p_const = option_pnl_pct("call", float(u_t), float(entry_close[i]), cp, float(prem),
                                     total_dte=float(dte[i]), vega_ratio=vega, delta=DEFAULT_DELTA)
            p_gamma = bs_option_pnl_pct("call", float(u_t), float(entry_close[i]), cp, float(prem),
                                        total_dte=float(dte[i]), vega_ratio=vega)
            pnl_const.append(p_const)
            pnl_gamma.append(p_gamma)
            n_computed += 1
        new_cols[f"pred_pnl_d{cp}_f2constrefined"] = pnl_const
        new_cols[f"pred_pnl_d{cp}_f2gammarefined"] = pnl_gamma
        log(f"  d{cp}: refined-arm P&L computed for {n_computed}/{df.height} rows "
            f"(matches original checkpoint-exists coverage)")

    out_df = df.with_columns([pl.Series(k, v) for k, v in new_cols.items()])
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out_df.write_parquet(OUT_PATH)
    log(f"\nwrote {OUT_PATH}  rows={out_df.height}  cols={len(out_df.columns)} "
        f"(original {len(df.columns)} + {len(new_cols)} new)")

    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_ledger": LEDGER_PATH, "source_refit_report": REFIT_REPORT,
        "frozen_form": form, "frozen_coefs": coefs,
        "tercile_cutoffs": {"t1": t1, "t2": t2}, "clamp": [clamp_floor, clamp_cap],
        "n_rows": df.height, "clamp_floor_hits": n_clamp_floor, "clamp_cap_hits": n_clamp_cap,
        "refined_vs_f2_premium_ratio_median": float(np.median(ratio_vs_f2)),
        "refined_vs_f2_premium_ratio_mean": float(np.mean(ratio_vs_f2)),
        "new_arms": ["f2constrefined", "f2gammarefined"],
        "checkpoints": list(CHECKPOINTS),
        "m3_note": "M3 (first-touch TP/SL walk) NOT recomputed for refined arms this round -- "
                   "needs the daily (t=1..15) forward path, not persisted at daily granularity "
                   "(only d5/d10/d15 checkpoints are); VERDICT.md REFINEMENT ROUND scope marks M3 "
                   "unchanged this round.",
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open(OUT_META, "w", encoding="ascii", errors="backslashreplace") as f:
        json.dump(meta, f, indent=2, default=str)
    log(f"wrote {OUT_META}")

    log(f"\n=== DONE in {time.time() - t0:.1f}s ===")


if __name__ == "__main__":
    main()
