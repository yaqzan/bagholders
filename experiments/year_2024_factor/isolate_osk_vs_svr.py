"""Does the direct-skew (OSK) entry filter add on TOP of the shipped SVR (semivol_r band-pass)?
Covered window, premium-aware (actual option P&L). proxy_ledger has BOTH features + option pnl.
Monthly-compound book (same robust model as iv_skew/premium_sim) so concurrency timing can't bias it."""
import sys
from pathlib import Path
import numpy as np, polars as pl
ROOT = Path(__file__).resolve().parents[2]
df = pl.read_parquet(ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet")

# shipped SVR gentleband (semivol_r) -> alloc scale in [FLOOR,1]
LO_CUT,LO_FULL,HI_FULL,HI_CUT,FLOOR = 0.50,0.70,1.25,1.65,0.50
def svr_scale(s):
    if s is None: return 1.0
    if s < LO_CUT or s > HI_CUT: return FLOOR
    if s < LO_FULL: return FLOOR + (1-FLOOR)*(s-LO_CUT)/(LO_FULL-LO_CUT)
    if s > HI_FULL: return FLOOR + (1-FLOOR)*(HI_CUT-s)/(HI_CUT-HI_FULL)
    return 1.0

def apex_pnl(pmax,pmin,p15):
    if pmin is None or pmax is None or p15 is None: return None
    if pmin <= -0.70: return -0.70
    if pmax >= 0.30: return 0.30
    return max(p15,-0.70)

def maxdd(c):
    peak=-1e9; dd=0.0
    for v in c:
        peak=max(peak,v); dd=min(dd,(v-peak)/peak if peak>0 else 0.0)
    return dd

# covered tradable cohort
d = df.filter(pl.col("opt_skew").is_not_null() & pl.col("pnl15").is_not_null() & (pl.col("overall")>=75))
d = d.with_columns(
    pl.struct(["pnl_max","pnl_min","pnl15"]).map_elements(
        lambda r: apex_pnl(r["pnl_max"],r["pnl_min"],r["pnl15"]), return_dtype=pl.Float64).alias("apex_pnl"),
    pl.col("semivol_r").map_elements(svr_scale, return_dtype=pl.Float64).alias("svr_w"),
    pl.col("date").str.slice(0,7).alias("ym"),
).filter(pl.col("apex_pnl").is_not_null())
print(f"covered 75+ with option pnl: N={d.height}  {d['date'].min()}..{d['date'].max()}")

def book(d, f=0.10):
    """premium-WEIGHTED monthly book: each month, equity *= (1 + f * weighted_mean(apex_pnl)).
    weight = svr_w * osk_w per signal (alloc scaling). returns (ret,maxdd,N,perTrade_wtd,win)."""
    m = d.group_by("ym").agg([
        ((pl.col("apex_pnl")*pl.col("w")).sum()/pl.col("w").sum()).alias("wm"),
        pl.len().alias("n"),
        (pl.col("apex_pnl")*pl.col("w")).sum().alias("num"), pl.col("w").sum().alias("den"),
    ]).sort("ym")
    eq=1.0; curve=[1.0]
    for wm in m["wm"].to_list():
        eq*= (1.0 + f*wm); curve.append(eq)
    ptw = float((d["apex_pnl"]*d["w"]).sum()/d["w"].sum())
    win = 100*float((d["apex_pnl"]>0).mean())
    return eq-1.0, maxdd(curve), d.height, ptw*100, win

def osk_w(col, lo_q, floor=0.5):
    """band-pass-ish: full weight above lo_q quantile, floor below (drop the euphoric cheap-side)."""
    thr = d[col].quantile(lo_q)
    return pl.when(pl.col(col) >= thr).then(1.0).otherwise(floor)

print("\n=== premium-aware monthly book (f=0.10), 75+ covered window ===")
print(f"{'config':38} {'ret':>9} {'maxDD':>8} {'perTrade_wtd':>13} {'win':>6}")
# baseline variants
configs = [
    ("baseline (flat, no filter)",      pl.lit(1.0)),
    ("SVR only (shipped)",              pl.col("svr_w")),
    ("OSK only (drop bot-33% skew)",    osk_w("opt_skew",0.33)),
    ("SVR + OSK (drop bot-33% skew)",   pl.col("svr_w")*osk_w("opt_skew",0.33)),
    ("OSK only (drop bot-50% skew)",    osk_w("opt_skew",0.50)),
    ("SVR + OSK (drop bot-50% skew)",   pl.col("svr_w")*osk_w("opt_skew",0.50)),
]
rows=[]
for name, wexpr in configs:
    dd = d.with_columns(wexpr.alias("w"))
    r = book(dd)
    rows.append((name,)+r)
    print(f"{name:38} {r[0]*100:+8.1f}% {r[1]*100:7.1f}% {r[3]:+12.1f}% {r[4]:5.0f}%")

base_svr = [r for r in rows if r[0]=="SVR only (shipped)"][0]
print(f"\n=== incremental OSK on top of SVR (vs 'SVR only') ===")
for r in rows:
    if r[0].startswith("SVR + OSK"):
        print(f"  {r[0]:34} Δret={ (r[1]-base_svr[1])*100:+.1f}pp  ΔmaxDD={(r[2]-base_svr[2])*100:+.1f}pp  ΔperTrade={r[4]-base_svr[4]:+.1f}pp")
print("\nNOTE: ~1.3y selloff-heavy window (net-negative book). The question is whether OSK improves")
print("the SVR book per-trade/DD here; it CANNOT be COVID-gated (no option data pre-2025).")
