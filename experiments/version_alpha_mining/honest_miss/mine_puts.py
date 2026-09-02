"""Honest miss-mine on the v70 PUT ledger (option-TP). Mirror of mine.py for puts.
Puts: lower overall = stronger. Tradable gate is <=25.
  * 16-25 DEMOTE candidates: sub-cohorts whose put optTP is BELOW the tier marginal
    -> the honest put-miss cohort to purify (a put dampener/lift toward the gate).
  * 26-30 ADMIT candidates: sub-cohorts whose optTP is ABOVE the 21-25 marginal
    -> accretive puts currently just outside the gate.
Put losers are usually counter-trend (high trend/rsi, bullish weekly) — probe those.
"""
import os, json, math
import polars as pl

LED = ".cache/honest_miss/ledger_puts_5y.parquet"
df = pl.read_parquet(LED).filter(pl.col("opt15").is_not_null())
col = "opt15"

def z(p, n, p0):
    if n < 1 or not (0 < p0 < 1): return 0.0
    se = math.sqrt(p0*(1-p0)/n)
    return round((p-p0)/se, 2) if se > 0 else 0.0

NUM = ["stoch","trend","macd","bb","rsi","ta","td","w_adj","w_comp","w_bias","w_mom",
       "vol_pct","cont_lift","mcd_dampen","ich_dampen","scw_dampen","pre_boost","pre_regime","mis_stress"]

print("PUT tier marginals (honest v70, option-TP15):")
for lo, hi in [(1,15),(16,20),(21,25),(26,30)]:
    t = df.filter((pl.col("overall")>=lo)&(pl.col("overall")<=hi))
    if t.height:
        print(f"  {lo:2}-{hi:2}: N={t.height:<5} optTP={t[col].mean()*100:5.2f}  genWR15={t['gen15'].mean()*100:5.2f}")

def scan(tier, base, label):
    print(f"\n=== {label} feature scan (marginal optTP={base*100:.2f}; |z|>=3 = strong) ===")
    feats=[]
    for f in NUM:
        if f not in tier.columns: continue
        sub=tier.filter(pl.col(f).is_not_null())
        if sub.height<300 or sub[f].n_unique()<5: continue
        q33,q66=sub[f].quantile(0.33),sub[f].quantile(0.66)
        if q33 is None or q33==q66: continue
        bins={"lo":sub.filter(pl.col(f)<=q33),"mid":sub.filter((pl.col(f)>q33)&(pl.col(f)<=q66)),"hi":sub.filter(pl.col(f)>q66)}
        best=0.0; binfo={}
        for nm,b in bins.items():
            if b.height<100: continue
            p=b[col].mean(); zz=z(p,b.height,base); binfo[nm]=(round(p*100,2),b.height,zz)
            if abs(zz)>abs(best): best=zz
        feats.append((f,best,binfo))
    feats.sort(key=lambda x:-abs(x[1]))
    for f,best,bi in feats[:8]:
        print(f"  {f:12} strong_z={best:+5.2f}  " + " ".join(f"{k}=optTP{v[0]}/N{v[1]}/z{v[2]:+.1f}" for k,v in bi.items()))

# DEMOTE scan on the marginal tradable tier (21-25, weakest tradable put)
t2125=df.filter((pl.col("overall")>=21)&(pl.col("overall")<=25))
t1620=df.filter((pl.col("overall")>=16)&(pl.col("overall")<=20))
if t2125.height: scan(t2125, t2125[col].mean(), "21-25 DEMOTE (counter-trend put losers)")
if t1620.height: scan(t1620, t1620[col].mean(), "16-20 DEMOTE")

# targeted put-loser probes on 16-25 (counter-trend = high trend/rsi, bullish weekly)
trad=df.filter((pl.col("overall")>=16)&(pl.col("overall")<=25))
base=trad[col].mean()
print(f"\n=== 16-25 targeted put-loser probes (marginal optTP={base*100:.2f}) ===")
def probe(name,expr):
    try: sub=trad.filter(expr)
    except Exception as e: print(f"  {name:28} ERR {str(e)[:40]}"); return
    if sub.height<50: print(f"  {name:28} N={sub.height} (small)"); return
    p=sub[col].mean(); print(f"  {name:28} N={sub.height:<5} optTP={p*100:5.2f} z={z(p,sub.height,base):+5.2f} vs_marg={(p-base)*100:+5.2f}")
probe("trend_hi (counter-trend)", pl.col("trend")>60)
probe("rsi_hi (not oversold)", pl.col("rsi")>50)
probe("wadj_pos (bullish weekly)", pl.col("w_adj")>2)
probe("wmom_pos (rising weekly)", pl.col("w_mom")>2)
probe("macd_hi", pl.col("macd")>55)
probe("stoch_hi", pl.col("stoch")>55)
probe("vol_pct_hi", pl.col("vol_pct")>trad["vol_pct"].quantile(0.66))
probe("vol_pct_lo", pl.col("vol_pct")<trad["vol_pct"].quantile(0.33))
probe("mcd_dampen>0", pl.col("mcd_dampen")>0)
probe("trend_hi & wadj_pos", (pl.col("trend")>60)&(pl.col("w_adj")>2))
