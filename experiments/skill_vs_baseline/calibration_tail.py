"""0c + 1b: reliability curve + full-N tail study + spread-skill, on the Apex predictand.

Runs on the FULL 70+ call cohort (no sampling — the tail study needs full 85+/90+ N).
Joins stored v73 components to the 30dte_apex barrier set.

  - RELIABILITY: realized Apex WR + EV by score band (does higher score -> better
    outcome on the REAL payoff? On the generic barrier it was flat.).
  - TAIL (1b): full-N EV / WR / stop-rate for 85-89 and 90-100 — the one place the
    signal-level skill test hinted at real alpha.
  - SPREAD-SKILL (0c): does component disagreement predict worse Apex EV at fixed score?

Read-only. EV map win/stop/expire = +0.30/-0.70/-0.40 (Apex payoff, v1 approx). In-sample.
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
MEMBERS = ["trend", "bb", "rsi", "macd", "stoch"]


def main():
    pq = materialize_polars(
        f"ensemble_spread_v{VID}_calls_2016",
        lambda: (_ for _ in ()).throw(RuntimeError(f"run spread_skill.py first to cache the v{VID} 70+ component parquet")),
    )
    df = pl.read_parquet(pq)
    print(f"\n=== CALIBRATION + TAIL + SPREAD-SKILL  v{VID}  barrier=30dte_apex ({PERIOD}) ===")
    print(f"70+ call cohort: {df.height:,}", flush=True)

    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), int(o)) for s, d, o in zip(df["symbol"], df["date"], df["overall"])]
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    ev = {}; wr = {}; stop = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            k = (r["symbol"], dk)
            ev[k] = EV[sw["result"]]; wr[k] = 1 if sw["result"] == "win" else 0
            stop[k] = 1 if sw["result"] == "stop" else 0
    match = len(ev)
    print(f"apex-barrier match: {match:,}/{df.height:,} ({match/df.height*100:.1f}%) [{skipped:,} uncached]")
    if match < 0.5 * df.height:
        print("\n*** LOW MATCH — 30dte_apex backfill not complete. Re-run after #174. ***"); return

    df = df.with_columns(
        ev=pl.struct(["symbol", "date"]).map_elements(lambda x: ev.get((x["symbol"], x["date"])), return_dtype=pl.Float64),
        wr=pl.struct(["symbol", "date"]).map_elements(lambda x: wr.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64),
        stop=pl.struct(["symbol", "date"]).map_elements(lambda x: stop.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64),
    ).filter(pl.col("wr") >= 0)

    mean5 = sum(pl.col(c) for c in MEMBERS) / 5.0
    var5 = sum((pl.col(c) - mean5) ** 2 for c in MEMBERS) / 5.0
    df = df.with_columns(spread=var5.sqrt())

    sc = df["overall"].to_numpy()
    evv = df["ev"].to_numpy().astype(float)
    wrv = df["wr"].to_numpy().astype(float)
    stv = df["stop"].to_numpy().astype(float)
    sp = df["spread"].to_numpy()

    bands = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 100)]
    print("\n--- 1. RELIABILITY + TAIL: realized Apex outcome by score band (FULL N) ---")
    print(f"{'band':>8} | {'N':>7} | {'WR':>6} | {'stop%':>6} | {'EV':>8}")
    for lo, hi in bands:
        m = (sc >= lo) & (sc <= hi)
        if m.sum() == 0:
            continue
        print(f"{lo}-{hi:>3} | {int(m.sum()):>7,} | {wrv[m].mean()*100:5.1f}% | {stv[m].mean()*100:5.1f}% | {evv[m].mean()*100:+7.2f}%")
    print("  ^ does EV rise with score on the REAL payoff? is the 85+/90+ tail net-positive EV?")

    print("\n--- 2. SPREAD-SKILL on Apex EV: EV by component-disagreement tercile, within band ---")
    print(f"{'band':>8} | {'N':>7} | {'loSpread EV':>11} | {'hiSpread EV':>11} | {'lo-hi':>6}")
    for lo, hi in bands:
        m = (sc >= lo) & (sc <= hi)
        if m.sum() < 90:
            continue
        spm, evm = sp[m], evv[m]
        q1, q2 = np.quantile(spm, [1 / 3, 2 / 3])
        loT, hiT = spm <= q1, spm > q2
        print(f"{lo}-{hi:>3} | {int(m.sum()):>7,} | {evm[loT].mean()*100:+10.2f}% | {evm[hiT].mean()*100:+10.2f}% | {(evm[loT].mean()-evm[hiT].mean())*100:+5.2f}")
    print("  ^ positive lo-hi => low-disagreement signals carry better EV (ensemble confidence is real on the payoff).")
    print(f"\nNOTE: EV = +0.30/-0.70/-0.40 win/stop/expire (Apex v1). In-sample.")


if __name__ == "__main__":
    main()
