"""Cheap decisive pre-test for regime-conditioned component reweighting (de-weight TREND in bear/chop).

The weather_components diagnostic: TREND is the dominant score-driver but regime-HARMFUL in bear/chop
(2022 -2.42 / 2023 -1.81 ΔEV). The funded question (verify_value): a reweight can only help by changing
75+ GATE membership for the better. De-weighting trend in bear/chop would DROP high-trend 75+ names.

THE TEST (no rescore needed): within the 75+ cohort, split by TREND tercile × regime. If high-trend 75+
has BELOW-base apex WR in bear/chop (and is fine in bull), de-weighting trend there removes bad names =>
real lead. If flat => no separable signal at the gate => reweight-trap / gradient-inert => NULL.

Read-only. apex predictand. Regime = year (2022/2023 = documented bear/chop) + regime_composite bands.
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

# --- 75+ cohort with components ---
df = pre_cutoff_filter(pl.read_parquet(cache_path("vv_v74_70plus"))).filter(pl.col("overall") >= 75)
print(f"75+ cohort (pre-cutoff): {df.height:,}", flush=True)

# --- regime per date ---
DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
rr = DB.execute_sql("SELECT date, regime_composite, vix_close FROM market_regime WHERE regime_composite IS NOT NULL").fetchall()
reg = pl.DataFrame({"date": [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in rr],
                    "rcomp": [float(r[1]) for r in rr],
                    "vix": [float(r[2]) if r[2] is not None else None for r in rr]})
df = df.join(reg, on="date", how="left")

# --- apex outcome ---
Peak = namedtuple("Peak", "symbol_id date overall")
peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
res, sk = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
win = {}; ev = {}
for r in res:
    dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
    sw = r.get("swing", {}).get("30d")
    if sw and sw.get("result") in EV:
        win[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0
        ev[(r["symbol"], dk)] = EV[sw["result"]]
df = df.with_columns(
    win=pl.struct(["symbol", "date"]).map_elements(lambda x: win.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64),
    ev=pl.struct(["symbol", "date"]).map_elements(lambda x: ev.get((x["symbol"], x["date"]), None), return_dtype=pl.Float64),
    year=pl.col("date").str.slice(0, 4),
).filter(pl.col("win") >= 0)
print(f"apex-matched: {df.height:,}  base WR={df['win'].mean()*100:.2f}%  base EV={df['ev'].mean()*100:+.2f}%\n", flush=True)

tr = df["trend"].to_numpy().astype(float)
w = df["win"].to_numpy().astype(float)
e = df["ev"].to_numpy().astype(float)
yr = df["year"].to_numpy()
rc = df["rcomp"].to_numpy().astype(float)


def split_trend(mask, label):
    if mask.sum() < 150:
        print(f"  {label:>22}: N={int(mask.sum())} (too small)"); return
    t, ww, ee = tr[mask], w[mask], e[mask]
    base_wr = ww.mean() * 100; base_ev = ee.mean() * 100
    q1, q2 = np.quantile(t, [1/3, 2/3])
    lo = t <= q1; mid = (t > q1) & (t <= q2); hi = t > q2
    def s(m): return (ww[m].mean()*100, ee[m].mean()*100, int(m.sum())) if m.sum() else (float("nan"),)*2+(0,)
    (lw, le, ln), (mw, me, mn), (hw, he, hn) = s(lo), s(mid), s(hi)
    print(f"  {label:>22}: N={int(mask.sum()):>6} base WR={base_wr:5.1f}% EV={base_ev:+5.2f} | "
          f"loTREND {lw:5.1f}%/{le:+5.2f}(n{ln}) midT {mw:5.1f}%(n{mn}) hiTREND {hw:5.1f}%/{he:+5.2f}(n{hn}) | "
          f"hi-base WR {hw-base_wr:+5.1f}pp EV {he-base_ev:+5.2f}")


print("=== apex outcome by TREND tercile within the 75+ gate, per regime ===")
print("  (the lead needs: hiTREND BELOW base in BEAR/CHOP, but ~OK in BULL => de-weighting trend removes bad names there)\n")
print("-- by YEAR (2022/2023 = documented bear/chop) --")
for y in ["2021", "2022", "2023", "2024", "2025"]:
    split_trend(yr == y, f"year {y}")
print("\n-- by regime_composite band --")
split_trend(rc < 45, "STRESS (rcomp<45)")
split_trend((rc >= 45) & (rc < 60), "NEUTRAL (45-60)")
split_trend(rc >= 60, "BULL (rcomp>=60)")
print("\n-- by VIX --")
vx = df["vix"].to_numpy().astype(float)
split_trend(vx >= 22, "VIX>=22 (stress)")
split_trend(vx < 16, "VIX<16 (calm)")
