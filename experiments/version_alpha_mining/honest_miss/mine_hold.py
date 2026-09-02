"""Honest miss-mine on the HOLD barrier (TP-reach within 15d, NO early stop) for the
FUNDED Apex tiers (75+ calls, deploy-at-every-signal). This is the CORRECT barrier for
the hold strat (SL wide ~70-85%, exit day-15): the metric `opt15` here = did the call
reach +1.274sigma within 15d holding through dips (the fast-recycle early-SL misses that
RECOVER are wins here). Find: 75+ cohorts that fail EVEN WHEN HELD (demote), and 70-74
cohorts that succeed enough to promote into the deploy zone.
"""
import math, polars as pl
LED = ".cache/honest_miss/ledger_hold_5y.parquet"
df = pl.read_parquet(LED).filter(pl.col("opt15").is_not_null())
M = "opt15"   # HOLD TP-reach within 15d

def z(p, n, p0):
    return round((p-p0)/math.sqrt(p0*(1-p0)/n), 2) if (n > 0 and 0 < p0 < 1) else 0.0

print("HOLD barrier (TP-reach<=15d, no early stop) tier baselines:")
for lo, hi in [(70,74),(75,79),(80,84),(85,99),(75,99)]:
    t = df.filter((pl.col("overall")>=lo)&(pl.col("overall")<=hi))
    if t.height:
        print(f"  {lo}-{hi}: N={t.height:<5} holdTP15={t[M].mean()*100:5.2f}  (fast-recycle opt would be ~50; hold wins more)")

NUM = ["stoch","trend","macd","bb","rsi","ta","td","w_adj","w_comp","w_bias","w_mom","vol_pct",
       "cont_lift","mcd_dampen","ich_dampen","scw_dampen","scw_scalar","daily_volume_authority_wave",
       "sector_breadth_wave","pre_boost","pre_regime","mis_stress","wadj_completed","wadj_partial","weekly_adj_gap"]

def scan(tier, base, label, want):
    print(f"\n=== {label} (N={tier.height}, holdTP15={base*100:.2f}) — {want}; |z|>=3 strong ===")
    feats=[]
    for f in NUM:
        if f not in tier.columns: continue
        s=tier.filter(pl.col(f).is_not_null())
        if s.height<300 or s[f].n_unique()<5: continue
        q33,q66=s[f].quantile(0.33),s[f].quantile(0.66)
        if q33 is None or q33==q66: continue
        best=0.0; bi={}
        for nm,b in (("lo",s.filter(pl.col(f)<=q33)),("mid",s.filter((pl.col(f)>q33)&(pl.col(f)<=q66))),("hi",s.filter(pl.col(f)>q66))):
            if b.height<80: continue
            zz=z(b[M].mean(),b.height,base); bi[nm]=(round(b[M].mean()*100,1),b.height,zz)
            if abs(zz)>abs(best): best=zz
        feats.append((f,best,bi))
    feats.sort(key=lambda x:-abs(x[1]))
    for f,best,bi in feats[:8]:
        print(f"  {f:14} z={best:+5.2f}  " + " ".join(f"{k}={v[0]}/N{v[1]}/z{v[2]:+.1f}" for k,v in bi.items()))

# DEMOTE scans on the funded tiers
for lo,hi,lbl in [(75,79,'75-79 DEMOTE'),(80,84,'80-84 DEMOTE'),(75,99,'75+ DEMOTE (whole deploy zone)')]:
    t=df.filter((pl.col("overall")>=lo)&(pl.col("overall")<=hi))
    if t.height: scan(t, t[M].mean(), lbl, "DEMOTE=hold-TP below tier")
# PROMOTE scan on 70-74 (would they earn a deploy slot?)
t7074=df.filter((pl.col("overall")>=70)&(pl.col("overall")<=74))
base75=df.filter((pl.col("overall")>=75)&(pl.col("overall")<=79))[M].mean()
if t7074.height:
    scan(t7074, base75, "70-74 PROMOTE (vs 75-79 hold baseline)", "PROMOTE=hold-TP above the 75-79 marginal")
