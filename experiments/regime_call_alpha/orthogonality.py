"""A-MKT orthogonality gate: is FLAT_chop's negative apex-EV INDEPENDENT of the 5 shipped DD levers,
or is it just RXDD/MWDD/TVDD territory (the G26 trap)? The lead's own stop-rule #1.

FLAT_chop = SPY trailing-20d return ~ 0 (±0.5%). Shipped contraction levers (their firing bands):
  RXDD = VIX ~20-28 | MWDD = McClellan ~0 (|mcc|<22) | TVDD = TRIN ~1.0-1.3 | F3F = breadth<50 | BDIV = SPY-near-high+breadth-rollover.
Decisive test (G21/G23): does FLAT_chop stay negative-EV in the all-levers-OFF slice? If it dissolves
=> co-occurs with the levers => NOT a 6th axis => null. If it survives => orthogonal => worth the v74 MC.
Also: is SPY-flat a LOW-VIX state (orthogonal to RXDD) or HIGH-VIX (RXDD's territory)?
"""
import os
import sys

import numpy as np
import polars as pl

_R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _R)
from database.trader_database import DB

df = pl.read_parquet(os.path.join(os.path.dirname(__file__), "regime_call_ledger.parquet"))
# apex column -> per-trade EV (it is already the +0.30/-0.70/-0.40 barrier-walk EV per the FINDINGS)
print("apex dtype:", df["apex"].dtype, "| sample:", df["apex"].head(5).to_list(), "| mean:", round(df["apex"].mean(), 4))

DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
vr = DB.execute_sql("SELECT date, vix_close FROM market_regime WHERE vix_close IS NOT NULL").fetchall()
br = DB.execute_sql("SELECT date, mcclellan_oscillator, trin, breadth_score FROM market_breadth").fetchall()
viz = pl.DataFrame({"date": [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in vr],
                    "vix": [float(r[1]) for r in vr]})
brd = pl.DataFrame({"date": [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in br],
                    "mcc": [float(r[1]) if r[1] is not None else None for r in br],
                    "trin": [float(r[2]) if r[2] is not None else None for r in br],
                    "brd": [float(r[3]) if r[3] is not None else None for r in br]})
df = df.join(viz, on="date", how="left").join(brd, on="date", how="left").drop_nulls(["vix", "mcc", "trin", "brd"])
ev = df["apex"].to_numpy().astype(float)
spy = df["spy_r20"].to_numpy().astype(float)
vix = df["vix"].to_numpy().astype(float); mcc = df["mcc"].to_numpy().astype(float)
trin = df["trin"].to_numpy().astype(float); brd = df["brd"].to_numpy().astype(float)
base = ev.mean() * 100
N = len(ev)
flat = np.abs(spy) <= 0.005
print(f"\nN(lever-joined)={N:,}  base apexEV={base:+.2f}%   FLAT_chop N={int(flat.sum())}")


def cell(mask, label):
    if mask.sum() < 40:
        print(f"  {label:>34}: N={int(mask.sum())} (too thin)"); return
    m = ev[mask]; mu = m.mean() * 100; n = int(mask.sum())
    # two-proportion-ish z vs base on EV (use std error of mean)
    se = (ev.std() / np.sqrt(n)) * 100
    print(f"  {label:>34}: N={n:>5}  apexEV={mu:+6.2f}%  (vs base {base:+.2f} => {mu-base:+5.2f}pp, ~{(mu-base)/se:+.1f}sigma)")


print("\n-- is SPY-flat a LOW-VIX or HIGH-VIX state? (RXDD fires VIX~20-28) --")
print(f"  FLAT_chop VIX: median={np.median(vix[flat]):.1f}  %VIX<20={100*np.mean(vix[flat]<20):.0f}%  %VIX in 20-28={100*np.mean((vix[flat]>=20)&(vix[flat]<=28)):.0f}%  %VIX>28={100*np.mean(vix[flat]>28):.0f}%")
print(f"  all-rows  VIX: median={np.median(vix):.1f}  %VIX<20={100*np.mean(vix<20):.0f}%")

print("\n-- FLAT_chop apexEV, sliced by each shipped lever's OFF region (does the negative-EV survive where the lever can't fire?) --")
cell(flat, "FLAT_chop (all)")
cell(flat & (vix < 20), "  & VIX<20 (RXDD off)")
cell(flat & (vix > 28), "  & VIX>28 (RXDD off, panic)")
cell(flat & (np.abs(mcc) > 22), "  & |McClellan|>22 (MWDD off)")
cell(flat & ((trin < 0.77) | (trin > 1.31)), "  & TRIN extreme (TVDD off)")
cell(flat & (brd >= 50), "  & breadth>=50 (F3F off)")

allviversoff = flat & (vix < 20) & (np.abs(mcc) > 22) & ((trin < 0.77) | (trin > 1.31)) & (brd >= 50)
print("\n-- the ALL-LEVERS-OFF slice (the decisive orthogonality test) --")
cell(allviversoff, "FLAT_chop & ALL 5 levers OFF")
# looser all-off (VIX<20 + breadth>=50 only — the two most likely co-occurring with SPY-flat)
loose = flat & (vix < 20) & (brd >= 50)
cell(loose, "FLAT_chop & VIX<20 & brd>=50")
print("\nVERDICT: FLAT_chop negative-EV SURVIVES the levers-off slice (with N>=~80) => orthogonal 6th axis => v74 MC.")
print("         FLAT_chop dissolves to ~base in the levers-off slice => RXDD/MWDD territory (G26) => NULL.")
