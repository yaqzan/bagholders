"""Adversarial verification of the honest v71 candidate: 21-25 puts with LOW weekly
momentum (w_mom) underperform on option-TP (z=-4.2, optTP 33% < 36.4% put BE).
Check: exact threshold, multi-window 1y/3y/5y sign-consistency, BOTH barriers
(opt + generic), N, and overlap with the existing PCD post-crash dampener.
"""
import math, polars as pl
LED = ".cache/honest_miss/ledger_puts_5y.parquet"
df = pl.read_parquet(LED).filter(pl.col("opt15").is_not_null())
print("ledger columns:", df.columns)

def z(p, n, p0):
    if n < 1 or not (0 < p0 < 1): return 0.0
    return round((p-p0)/math.sqrt(p0*(1-p0)/n), 2)

t = df.filter((pl.col("overall")>=21)&(pl.col("overall")<=25)).filter(pl.col("w_mom").is_not_null())
q33 = t["w_mom"].quantile(0.33)
print(f"\n21-25 puts: N={t.height}, w_mom q33={q33:.2f}  (LOW = w_mom <= q33)")
base_o = t["opt15"].mean(); base_g = t.filter(pl.col('gen15').is_not_null())["gen15"].mean()
print(f"  tier marginal: optTP15={base_o*100:.2f}  genWR15={base_g*100:.2f}  (put BE opt=36.4)")

lo = t.filter(pl.col("w_mom")<=q33)
rest = t.filter(pl.col("w_mom")>q33)
print(f"\n  w_mom LOW   N={lo.height:<5} optTP={lo['opt15'].mean()*100:.2f} (z={z(lo['opt15'].mean(),lo.height,base_o)}) "
      f"genWR15={lo.filter(pl.col('gen15').is_not_null())['gen15'].mean()*100:.2f}")
print(f"  w_mom REST  N={rest.height:<5} optTP={rest['opt15'].mean()*100:.2f} genWR15={rest.filter(pl.col('gen15').is_not_null())['gen15'].mean()*100:.2f}")

# multi-window sign consistency (by calendar year of signal date)
print("\n  multi-window (w_mom LOW optTP vs tier marginal, per year):")
df2 = t.with_columns(pl.col("date").str.slice(0,4).alias("yr"))
for yr in ["2021","2022","2023","2024","2025"]:
    ty = df2.filter(pl.col("yr")==yr)
    if ty.height < 50: continue
    lyr = ty.filter(pl.col("w_mom")<=q33); byr = ty["opt15"].mean()
    if lyr.height < 20: continue
    print(f"    {yr}: tier_optTP={byr*100:5.2f}  LOW_optTP={lyr['opt15'].mean()*100:5.2f}  "
          f"dlt={ (lyr['opt15'].mean()-byr)*100:+5.2f}  N={lyr.height}")

# finer threshold sweep — where is the EV-negative (< put BE 36.4) boundary sharpest?
print("\n  w_mom threshold sweep (21-25): cohort below threshold")
for thr in [-15,-10,-8,-6,-4,-2,0]:
    c = t.filter(pl.col("w_mom")<=thr)
    if c.height < 50:
        print(f"    w_mom<={thr:>4}: N={c.height} (small)"); continue
    print(f"    w_mom<={thr:>4}: N={c.height:<5} optTP={c['opt15'].mean()*100:5.2f} z={z(c['opt15'].mean(),c.height,base_o):+5.2f} "
          f"genWR15={c.filter(pl.col('gen15').is_not_null())['gen15'].mean()*100:5.2f}")

# overlap with PCD: is pcd info in the ledger? check ret/sigma-ish columns
pcd_cols = [c for c in df.columns if 'pcd' in c.lower() or 'r10' in c.lower() or 'sigma' in c.lower() or 'ret' in c.lower()]
print(f"\n  PCD-related ledger cols: {pcd_cols or 'NONE (ledger has w-features, not pcd inputs)'}")
# does the LOW cohort also span lower put tiers (<=20)? (where PCD already lifts <=25 crash puts to >=30)
allp = df.filter(pl.col("w_mom").is_not_null())
for lo_,hi_ in [(1,15),(16,20),(21,25),(26,30)]:
    tt = allp.filter((pl.col("overall")>=lo_)&(pl.col("overall")<=hi_))
    if tt.height<50: continue
    q = tt["w_mom"].quantile(0.33); c = tt.filter(pl.col("w_mom")<=q)
    print(f"    {lo_}-{hi_}: w_mom_LOW optTP={c['opt15'].mean()*100:5.2f} vs tier {tt['opt15'].mean()*100:5.2f} "
          f"(dlt {(c['opt15'].mean()-tt['opt15'].mean())*100:+.2f}, z={z(c['opt15'].mean(),c.height,tt['opt15'].mean())})")
