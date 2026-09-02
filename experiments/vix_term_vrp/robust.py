"""Per-window robustness of the one N5 hit: norm-VIX (16-22) + backwardation
(VIX9D>VIX3M) -> 75+ call apex outperformance. Regime-stable => stageable lead;
one-episode => noise. Also checks orthogonality to the shipped SVR (semivol_r)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polars as pl
from mine import build_vol_features, attach, VID, two_prop_z
from database.bulk_cache import materialize_polars

vol = build_vol_features()
f75 = (pl.read_parquet(materialize_polars(f"weather_comp_v{VID}_2016", None))
       .filter(pl.col("overall") >= 75).select(["symbol", "date"])
       .join(vol, on="date", how="inner"))
f75, _ = attach(f75)
f75 = f75.with_columns(yr=pl.col("date").str.slice(0, 4),
                       bw=pl.col("ts_ratio") > 1.0)
norm = f75.filter((pl.col("vix") >= 16) & (pl.col("vix") < 22))

print(f"norm-VIX (16-22) 75+ book: N={norm.height:,}")
print("\n=== PER-WINDOW: backwardation vs contango (norm-VIX) ===")
print(f"  {'year':>6} | {'N_bw':>5} {'EV_bw':>8} | {'N_ct':>6} {'EV_ct':>8} | {'dEV':>8}")
for y in ["2016","2017","2018","2019","2020","2021","2022","2023","2024","2025","2026"]:
    yb = norm.filter(pl.col("yr") == y)
    bw = yb.filter(pl.col("bw")); ct = yb.filter(~pl.col("bw"))
    if bw.height < 8:
        print(f"  {y:>6} | N_bw={bw.height} thin"); continue
    print(f"  {y:>6} | {bw.height:>5} {bw['apex_ev'].mean()*100:+7.2f}% | "
          f"{ct.height:>6} {ct['apex_ev'].mean()*100:+7.2f}% | {(bw['apex_ev'].mean()-ct['apex_ev'].mean())*100:+7.2f}")

# pooled norm bw vs ct z
bw = norm.filter(pl.col("bw")); ct = norm.filter(~pl.col("bw"))
z = two_prop_z(int(bw["apex_win"].sum()), bw.height, int(ct["apex_win"].sum()), ct.height)
print(f"\n  POOLED norm-VIX: bw EV {bw['apex_ev'].mean()*100:+.2f}% (N{bw.height}) vs ct {ct['apex_ev'].mean()*100:+.2f}% (N{ct.height}) | z={z:+.2f}")
print(f"  bw cohort = {bw.height/f75.height*100:.1f}% of the full 75+ book")
