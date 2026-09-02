"""Phase 3: WIN-MAGNITUDE resolution — does a higher score predict a BIGGER favorable run?

Phases 1-2 showed the score has ~0 per-trade DIRECTIONAL resolution above the 70 gate
(binary apex win, and the 3-outcome apex EV, are both flat by band). But the sigma-barrier
caps a "win" at +30% TP, so it cannot see run SIZE — and the funded book monetizes size via
leverage + the dead-hold popout. This is the one dimension where the gradient could still
earn its keep.

Metric: forward Max Favorable Excursion over the apex hold (~K=20 trading bars), in daily-sigma
units:  MFE_sigma = (max(high[t+1..t+K]) / close[t] - 1) / vol60.  Higher = bigger favorable run.

If MFE_sigma is FLAT across score bands within 70+ -> the gradient is dead on size too ->
the 70-84 dampener/gradient machinery is a parsimony-cut candidate (the funded book uses the
GATE + leverage, not the magnitude). If MFE_sigma RISES with score -> the gradient earns its
keep through size, and the tier cascade is justified (just not by win-rate).

Read-only, in-sample. price_feats has close+high only (no low -> MFE only, no MAE).
"""
import os
import sys
import json
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from database.bulk_cache import cache_path
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion
from experiments._holdout import pre_cutoff_filter, cutoff_iso

VID = AlgorithmVersion.get_active_scores_version().id
K = 20  # ~28 cal days ~ the apex hold
BANDS = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 100)]


def spearman(x, y):
    rx = np.argsort(np.argsort(x)); ry = np.argsort(np.argsort(y))
    rx = (rx - rx.mean()); ry = (ry - ry.mean())
    d = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / d) if d > 0 else float("nan")


def main():
    out = {"version": VID, "K_bars": K, "cutoff": cutoff_iso()}
    print(f"\n################ PHASE 3  win-magnitude (MFE-sigma)  v{VID}  K={K} bars ################", flush=True)

    # ---- forward MFE over next K bars from the price panel (close + high) ----
    pf = pl.read_parquet(cache_path("price_feats_raw_2015")).sort(["symbol", "date"])
    pf = pf.with_columns(
        ret=(pl.col("close") / pl.col("close").shift(1).over("symbol")).log(),
        # forward max high over t+1..t+K  (reverse-roll-reverse trick, per symbol)
        fwd_high=pl.col("high").shift(-1).reverse().rolling_max(window_size=K, min_periods=K).reverse().over("symbol"),
    )
    pf = pf.with_columns(vol60=pl.col("ret").rolling_std(60, min_samples=60).over("symbol"))
    pf = pf.with_columns(
        mfe=(pl.col("fwd_high") / pl.col("close") - 1.0),
    ).with_columns(
        mfe_sig=pl.when(pl.col("vol60") > 0).then(pl.col("mfe") / pl.col("vol60")).otherwise(None),
    ).select(["symbol", "date", "mfe_sig"])

    # ---- 70+ cohort ----
    df = pre_cutoff_filter(pl.read_parquet(cache_path("vv_v74_70plus")))
    df = df.join(pf, on=["symbol", "date"], how="left").drop_nulls(subset=["mfe_sig"])

    # apex label (to split win-conditional)
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    results, sk = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    win = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("30d")
        if sw and sw.get("result") in ("win", "stop", "expire"):
            win[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0
    df = df.with_columns(
        win=pl.struct(["symbol", "date"]).map_elements(lambda x: win.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64)
    )

    sc = df["overall"].to_numpy().astype(float)
    mfe = df["mfe_sig"].to_numpy().astype(float)
    wn = df["win"].to_numpy()
    n = len(sc)
    print(f"70+ cohort with MFE (pre-cutoff): {n:,}", flush=True)

    base_mfe = mfe.mean(); med_mfe = float(np.median(mfe))
    print(f"\n=== MFE-sigma by score band (within 70+)  base mean={base_mfe:.3f}σ  median={med_mfe:.3f}σ ===")
    print(f"  {'band':>8} | {'N':>7} | {'mean MFE-σ':>11} | {'median':>8} | {'p75':>7} | {'(mean-base)':>11} | {'win-cond mean':>13}")
    rows = []
    for lo, hi in BANDS:
        m = (sc >= lo) & (sc <= hi); nk = int(m.sum())
        if nk == 0: continue
        mm = mfe[m]
        wc = mfe[m & (wn == 1)]
        meank = float(mm.mean()); medk = float(np.median(mm)); p75 = float(np.quantile(mm, 0.75))
        wck = float(wc.mean()) if len(wc) else float("nan")
        print(f"{lo}-{hi:>3} | {nk:>7,} | {meank:10.3f}σ | {medk:7.3f}σ | {p75:6.3f}σ | {meank-base_mfe:+10.3f} | {wck:12.3f}σ")
        rows.append({"band": f"{lo}-{hi}", "n": nk, "mean_mfe_sig": meank, "median_mfe_sig": medk,
                     "p75_mfe_sig": p75, "win_cond_mean": wck})
    out["bands"] = rows

    sp = spearman(sc, mfe)
    # OOF resolution of a logistic predicting "big run" (MFE > 70+ median) from overall? simplest: Spearman + band slope.
    # also: does a higher score raise the chance of a BIG run (top-quartile MFE)?
    bigq = np.quantile(mfe, 0.75)
    print(f"\n  Spearman(overall, MFE-σ) within 70+ = {sp:+.4f}")
    print(f"  P(top-quartile MFE | band):")
    for lo, hi in BANDS:
        m = (sc >= lo) & (sc <= hi)
        if m.sum() == 0: continue
        print(f"    {lo}-{hi}: {(mfe[m] >= bigq).mean()*100:5.2f}%   (base 25.0%)")
    out["spearman_overall_mfe"] = sp
    out["base_mean_mfe_sig"] = base_mfe

    print(f"\n  VERDICT: flat MFE-σ across bands + Spearman~0 => the score GRADIENT above 70 does not predict")
    print(f"  run SIZE either => the 70-84 gradient/dampener machinery is a parsimony-cut candidate (gate+leverage,")
    print(f"  not magnitude). A rising MFE-σ with score => the gradient earns its keep through size.")

    jp = os.path.join(os.path.dirname(__file__), "phase3_results.json")
    with open(jp, "w") as f:
        json.dump(out, f, indent=2, default=float)
    print(f"\nwrote {jp}")


if __name__ == "__main__":
    main()
