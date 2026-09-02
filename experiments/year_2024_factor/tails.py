"""
The pnl DISTRIBUTION by year — where exactly does 2024's edge come from?
Median winning trade is ~+0.28 every year (the +30% TP). So the year effect is in:
  (a) WIN FREQUENCY,  (b) LEFT TAIL (catastrophic -90% dead-hold-expiry bags),
  (c) RIGHT TAIL (gap-up TP fills sampled above +30% -> convexity).
Pure tape mining.
"""
import sys
from pathlib import Path
import polars as pl
ROOT = Path(__file__).resolve().parents[2]
TAPE = ROOT / ".cache" / "dd_ledger"
YEARS = ["2021", "2022", "2023", "2024", "2025"]

print("\n=== per-trade option_pnl distribution by year (live full-lever Apex tape) ===\n")
hdr = ("year |  meanPnl | LEFT TAIL            | RIGHT TAIL                       | netEV decomposition")
print(hdr)
sub = ("     |         | <-0.7   -0.7..-0.3  | >+0.3  >+0.5  >+1.0  >+2.0      | win_contrib  loss_drag")
print(sub); print("-"*108)
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists(): continue
    df = pl.read_parquet(f)
    n = df.height
    p = df["option_pnl"]
    def fr(expr): return 100*df.filter(expr).height/n
    lt_cat = fr(pl.col("option_pnl") < -0.7)             # near-total loss (dead-hold expiry)
    lt_mid = fr((pl.col("option_pnl") >= -0.7) & (pl.col("option_pnl") < -0.3))
    rt_tp  = fr(pl.col("option_pnl") > 0.3)
    rt_50  = fr(pl.col("option_pnl") > 0.5)
    rt_1   = fr(pl.col("option_pnl") > 1.0)
    rt_2   = fr(pl.col("option_pnl") > 2.0)
    # EV decomposition: sum of positive pnl / N  vs  sum of negative pnl / N
    win_contrib = df.filter(pl.col("option_pnl") > 0)["option_pnl"].sum()/n
    loss_drag   = df.filter(pl.col("option_pnl") < 0)["option_pnl"].sum()/n
    print(f"{y} | {p.mean():+7.3f} | {lt_cat:5.1f}  {lt_mid:9.1f}  | {rt_tp:5.1f} {rt_50:5.1f} {rt_1:5.1f} {rt_2:5.1f}    | "
          f"{win_contrib:+.3f}      {loss_drag:+.3f}")

print("\nlegend: <-0.7 = catastrophic dead-hold-expiry bags; >+1.0 = gap-up TP fills (momentum convexity).")
print("        netEV = win_contrib + loss_drag = meanPnl.  The year effect = MORE win_contrib AND LESS loss_drag.")

# How much of the YEAR's mean-pnl gap vs 2023 is win-side vs loss-side?
print("\n=== attribution of each year's edge vs the 2023 baseline (weak bull) ===\n")
base = None
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists(): continue
    df = pl.read_parquet(f); n=df.height
    wc = df.filter(pl.col("option_pnl")>0)["option_pnl"].sum()/n
    ld = df.filter(pl.col("option_pnl")<0)["option_pnl"].sum()/n
    if y=="2023": base=(wc,ld)
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists(): continue
    df = pl.read_parquet(f); n=df.height
    wc = df.filter(pl.col("option_pnl")>0)["option_pnl"].sum()/n
    ld = df.filter(pl.col("option_pnl")<0)["option_pnl"].sum()/n
    dwc = wc-base[0]; dld = ld-base[1]
    print(f"  {y}: meanPnl edge vs 2023 = {(wc+ld)-(base[0]+base[1]):+.3f}  "
          f"= win-side {dwc:+.3f}  +  loss-side {dld:+.3f}")
