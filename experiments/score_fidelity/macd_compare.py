"""Compare base v74 vs smooth-phase MACD: funded tradable impact + stability gain.

Three questions:
  1. FUNDED (Apex 75+): does smoothing change the 75+ set? If so, is the change ACCRETIVE
     (added rows >= base 75+ apex WR, dropped rows <=) or dilutive/neutral?
  2. MACD effect: |Δmacd| distribution — how big is the discontinuity removal, and is it
     concentrated in the 70-80 gate neighborhood?
  3. STABILITY (the value-add): day-over-day macd jumps on ADJACENT trading days — does the
     smooth version have fewer large jumps (the discontinuity removed)? + gate-crossing rate.
"""
import os
import sys
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _R)
from database.barrier_cache import peaks_to_swing_results

EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
C = os.path.join(_R, ".cache", "verify_value")
base = pl.read_parquet(os.path.join(C, "macd_base.parquet"))
_smooth_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(C, "macd_smooth.parquet")
smooth = pl.read_parquet(_smooth_path)
print(f"smooth arm: {os.path.basename(_smooth_path)}")
print(f"base: {base.height:,} rows (>=55, >=75: {(base['overall']>=75).sum():,})   "
      f"smooth: {smooth.height:,} (>=75: {(smooth['overall']>=75).sum():,})")

j = base.rename({"overall": "ov_b", "macd": "macd_b"}).join(
    smooth.rename({"overall": "ov_s", "macd": "macd_s"}), on=["symbol", "date"], how="full", coalesce=True)
j = j.with_columns(ov_b=pl.col("ov_b").fill_null(54), ov_s=pl.col("ov_s").fill_null(54))
n = j.height
n_ov_diff = (j["ov_b"] != j["ov_s"]).sum()
mb = j["macd_b"].to_numpy(); ms = j["macd_s"].to_numpy()
mm = ~(np.isnan(mb.astype(float)) | np.isnan(ms.astype(float)))
dmacd = (ms.astype(float) - mb.astype(float))
n_macd_diff = int((np.abs(dmacd[mm]) > 0).sum())
print(f"\narms differ: overall {n_ov_diff:,} rows | macd {n_macd_diff:,} rows", flush=True)
if n_macd_diff == 0:
    print("*** MACD IDENTICAL — patch did not take. Abort. ***"); sys.exit(1)

# === 1. FUNDED: 75+ membership change ===
ovb = j["ov_b"].to_numpy(); ovs = j["ov_s"].to_numpy()
added = (ovb < 75) & (ovs >= 75)    # smoothing PROMOTED into 75+
dropped = (ovb >= 75) & (ovs < 75)  # smoothing DEMOTED out of 75+
stable75 = (ovb >= 75) & (ovs >= 75)
print(f"\n=== 1. FUNDED (75+ membership) ===")
print(f"  base 75+: {int((ovb>=75).sum()):,}   smooth 75+: {int((ovs>=75).sum()):,}   "
      f"net {int((ovs>=75).sum())-int((ovb>=75).sum()):+,}")
print(f"  added (->75+): {int(added.sum()):,}   dropped (75+->): {int(dropped.sum()):,}   "
      f"stable: {int(stable75.sum()):,}  (churn {(int(added.sum())+int(dropped.sum()))/max(1,int((ovb>=75).sum()))*100:.1f}% of base 75+)")

# apex outcome of added/dropped/stable
def apex(mask):
    sub = j.filter(pl.Series(mask))
    if sub.height == 0:
        return None
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(sub["symbol"], sub["date"])]
    res, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    ev = []
    for r in res:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("30d")
        if sw and sw.get("result") in EV:
            ev.append((1 if sw["result"] == "win" else 0, EV[sw["result"]]))
    if not ev:
        return None
    w = np.array([e[0] for e in ev]); e = np.array([e[1] for e in ev])
    return len(ev), w.mean() * 100, e.mean() * 100

for nm, msk in [("stable 75+", stable75), ("ADDED ->75+", added), ("DROPPED 75+->", dropped)]:
    r = apex(msk)
    if r:
        print(f"  apex {nm:>14}: N={r[0]:>5}  WR={r[1]:5.1f}%  EV={r[2]:+6.2f}%")
print("  ^ accretive if ADDED WR >= stable and DROPPED WR <= stable (smoothing improves selection);")
print("    ~equal => stability-only (funded-neutral). The verify_value bar: changed set must not be worse.")

# === 2. MACD effect ===
d70 = mm & (ovb >= 68) & (ovb <= 80)
print(f"\n=== 2. MACD-component effect ===")
print(f"  |Δmacd| over all >=55 rows: mean {np.abs(dmacd[mm]).mean():.2f}  "
      f">=5: {int((np.abs(dmacd[mm])>=5).sum()):,} ({(np.abs(dmacd[mm])>=5).mean()*100:.1f}%)  "
      f">=10: {int((np.abs(dmacd[mm])>=10).sum()):,}")
print(f"  in the 68-80 gate band: mean |Δmacd| {np.abs(dmacd[d70]).mean():.2f}  "
      f">=5: {(np.abs(dmacd[d70])>=5).mean()*100:.1f}%  (these are the discontinuity-zone rows)")

# === 3. STABILITY: day-over-day macd jumps on adjacent trading days ===
def dod_jumps(df, col):
    d = df.sort(["symbol", "date"]).with_columns(
        dgap=(pl.col("date").str.to_date() - pl.col("date").str.to_date().shift(1).over("symbol")).dt.total_days(),
        dm=(pl.col(col) - pl.col(col).shift(1).over("symbol")).abs(),
    ).filter((pl.col("dgap") >= 1) & (pl.col("dgap") <= 4) & pl.col("dm").is_not_null())
    return d["dm"].to_numpy()

jb = dod_jumps(base, "macd"); js = dod_jumps(smooth, "macd")
print(f"\n=== 3. STABILITY (adjacent-trading-day |Δmacd|) ===")
print(f"  base  : N={len(jb):,}  mean {jb.mean():.2f}  >=10: {(jb>=10).mean()*100:.2f}%  >=15: {(jb>=15).mean()*100:.2f}%")
print(f"  smooth: N={len(js):,}  mean {js.mean():.2f}  >=10: {(js>=10).mean()*100:.2f}%  >=15: {(js>=15).mean()*100:.2f}%")
print(f"  -> large-jump (>=10) reduction: {(1 - (js>=10).mean()/max(1e-9,(jb>=10).mean()))*100:+.1f}%  "
      f"(the discontinuity-driven fakeout removal)")

# overall day-over-day gate crossings (75)
def gate_cross(df):
    d = df.sort(["symbol", "date"]).with_columns(
        dgap=(pl.col("date").str.to_date() - pl.col("date").str.to_date().shift(1).over("symbol")).dt.total_days(),
        prev=pl.col("overall").shift(1).over("symbol"),
    ).filter((pl.col("dgap") >= 1) & (pl.col("dgap") <= 4) & pl.col("prev").is_not_null())
    cur = d["overall"].to_numpy(); prv = d["prev"].to_numpy()
    cross = ((cur >= 75) != (prv >= 75)).sum()
    return cross, len(cur)
cb = gate_cross(base); cs = gate_cross(smooth)
print(f"  overall 75-gate day-over-day crossings: base {cb[0]:,}/{cb[1]:,} ({cb[0]/cb[1]*100:.2f}%)  "
      f"smooth {cs[0]:,}/{cs[1]:,} ({cs[0]/cs[1]*100:.2f}%)  -> {(1-(cs[0]/cs[1])/(cb[0]/cb[1]))*100:+.1f}%")
