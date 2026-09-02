"""Comprehensive cheap pre-test: does ANY component have a regime-separable apex-gate signal?

Extends pretest.py to all 6 components x regime bands. For each (component, regime) cell, report the
hi-tercile-minus-base apex EV at the 75+ gate. The user's full idea (dynamic weighting across regimes)
needs at least ONE cell with a LARGE, ROBUST (not single-window) hi-vs-base spread that a real-time
regime classifier (composite/VIX) can target. If every cell is within noise / single-window, the whole
dynamic-reweight space is null at the gate (verify_value gradient-inert, confirmed per-regime).
"""
import os
import sys
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _R)
from database.bulk_cache import cache_path
from database.barrier_cache import peaks_to_swing_results
from database.trader_database import DB
from experiments._holdout import pre_cutoff_filter

EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
COMPS = ["trend", "bb", "rsi", "macd", "stoch", "ta"]

df = pre_cutoff_filter(pl.read_parquet(cache_path("vv_v74_70plus"))).filter(pl.col("overall") >= 75)
DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
rr = DB.execute_sql("SELECT date, regime_composite, vix_close FROM market_regime WHERE regime_composite IS NOT NULL").fetchall()
reg = pl.DataFrame({"date": [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in rr],
                    "rcomp": [float(r[1]) for r in rr], "vix": [float(r[2]) if r[2] is not None else None for r in rr]})
df = df.join(reg, on="date", how="left")
Peak = namedtuple("Peak", "symbol_id date overall")
peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
res, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
ev = {}
for r in res:
    dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
    sw = r.get("swing", {}).get("30d")
    if sw and sw.get("result") in EV:
        ev[(r["symbol"], dk)] = EV[sw["result"]]
df = df.with_columns(ev=pl.struct(["symbol", "date"]).map_elements(lambda x: ev.get((x["symbol"], x["date"])), return_dtype=pl.Float64)).filter(pl.col("ev").is_not_null())
e = df["ev"].to_numpy().astype(float)
rc = df["rcomp"].to_numpy().astype(float); vx = df["vix"].to_numpy().astype(float)
regimes = {"STRESS<45": rc < 45, "NEUT45-60": (rc >= 45) & (rc < 60), "BULL>=60": rc >= 60,
           "VIX>=22": vx >= 22, "VIX<16": vx < 16}
print(f"75+ apex-matched: {df.height:,}  base EV={e.mean()*100:+.2f}%\n")
print("hi-tercile-minus-regime-base apex EV (pp) per component x regime  [a real lead = LARGE & consistent across regimes that share a sign]")
print(f"  {'component':>8} | " + " | ".join(f"{k:>10}" for k in regimes))
big = []
for c in COMPS:
    cv = df[c].to_numpy().astype(float)
    cells = []
    for rk, rm in regimes.items():
        if rm.sum() < 150:
            cells.append("   n/a"); continue
        cvr, evr = cv[rm], e[rm]
        base = evr.mean() * 100
        hi = cvr > np.quantile(cvr, 2/3)
        spread = evr[hi].mean() * 100 - base
        cells.append(f"{spread:+6.2f}")
        if abs(spread) >= 2.5:
            big.append((c, rk, spread))
    print(f"  {c:>8} | " + " | ".join(f"{x:>10}" for x in cells))
print(f"\n  |hi-base EV| >= 2.5pp cells: {big if big else 'NONE'}")
print("  (verify_value: nothing predicts apex above the gate. A regime-conditioned reweight is shippable")
print("   ONLY if a component shows a LARGE hi-vs-base spread that is CONSISTENT across the regime")
print("   classifiers a mechanism can use (composite AND vix), not a single-window/single-classifier flicker.)")
