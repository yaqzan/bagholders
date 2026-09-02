"""Regime-robustness of the risk-shaping edge: does score>=75 beat top-momentum
on the Apex option EV in EVERY window, or only in bull tape?

Reuses one barrier join on the 300k sample, then slices by calendar year.
Also prints EV-by-momentum-decile (full window) to confirm the inverted-U
(moderate momentum = good option EV, extreme momentum = tail-stopped).
Read-only. EV = +0.30/-0.70/-0.40. In-sample.
"""
import os
import sys
import math
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
PERIOD = "30d"


def welch_t(a, b):
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var() / len(a) + b.var() / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else float("nan")


def main():
    sc = pl.read_parquet(materialize_polars(f"skill_v{VID}_allscores_2016",
                                            lambda: (_ for _ in ()).throw(RuntimeError("missing"))))
    if sc.height > 300000:
        sc = sc.sample(n=300000, seed=17)
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(sc["symbol"], sc["date"])]
    print(f"\n=== WINDOW-ROBUSTNESS  v{VID}  30dte_apex EV ===\nbarrier join {len(peaks):,}...", flush=True)
    results, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    evm = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            evm[(r["symbol"], dk)] = EV[sw["result"]]
    sc = sc.with_columns(
        ev=pl.struct(["symbol", "date"]).map_elements(lambda x: evm.get((x["symbol"], x["date"])), return_dtype=pl.Float64),
        yr=pl.col("date").str.slice(0, 4),
    ).drop_nulls(["ev"])
    pf = pl.read_parquet(materialize_polars("price_feats_raw_2015",
                                            lambda: (_ for _ in ()).throw(RuntimeError("missing"))))
    pf = pf.sort(["symbol", "date"]).with_columns(
        mom=(pl.col("close").shift(21).over("symbol") / pl.col("close").shift(252).over("symbol") - 1.0)
    ).select(["symbol", "date", "mom"])
    sc = sc.join(pf, on=["symbol", "date"], how="left").drop_nulls(["mom"])

    ov = sc["overall"].to_numpy(); ev = sc["ev"].to_numpy().astype(float)
    mom = sc["mom"].to_numpy(); yr = sc["yr"].to_numpy()

    def compare(mask, label):
        o, e, m = ov[mask], ev[mask], mom[mask]
        if len(o) < 200:
            return
        s75 = o >= 75
        if s75.sum() < 30:
            return
        sel = s75.sum() / len(o)
        topm = m >= np.quantile(m, 1 - sel)
        clim = e.mean()
        t = welch_t(e[s75], e[topm])
        print(f"{label:>8} | {len(o):>7,} | {clim*100:+6.2f}% | {e[s75].mean()*100:+7.2f}% | "
              f"{e[topm].mean()*100:+8.2f}% | {(e[s75].mean()-e[topm].mean())*100:+7.2f} | {t:+5.2f}")

    print(f"\n{'window':>8} | {'N':>7} | {'climEV':>6} | {'75+ EV':>7} | {'topMomEV':>8} | {'75+-mom':>7} | {'t':>5}")
    compare(np.ones(len(ov), bool), "FULL")
    for y in ("2021", "2022", "2023", "2024", "2025"):
        compare(yr == y, y)
    print("  ^ risk-shaping edge = (75+ EV) - (top-momentum EV). Robust if positive across regimes.\n")

    # EV by 12-1 momentum decile (full) — the inverted-U mechanism
    dec = np.quantile(mom, np.linspace(0, 1, 11))
    print("--- EV by 12-1 momentum decile (full) — moderate vs extreme momentum on the payoff ---")
    print(f"{'decile':>7} | {'mom range':>20} | {'N':>7} | {'EV':>8}")
    for i in range(10):
        lo, hi = dec[i], dec[i + 1]
        d = (mom >= lo) & (mom <= hi) if i == 9 else (mom >= lo) & (mom < hi)
        if d.sum():
            print(f"{i+1:>7} | {lo:>+8.1%}..{hi:>+8.1%} | {d.sum():>7,} | {ev[d].mean()*100:+7.2f}%")


if __name__ == "__main__":
    main()
