"""Magnitude/EV mine for the 75+ HOLD strat. Binary TP-reach was uniform ~82%; this
mines the LOSS MAGNITUDE: among the ~18% that miss, which cohorts ride the wide SL
down to a deep loss (MAE_sigma >= SL_sigma). Those drag hold-strat EV/DD even at the
same TP-reach — the real demote targets.

hold EV proxy per cohort:  TP*0.33  -  SLhit*SL  -  (miss-not-SL)*0.40
(TP=+33%; deep loss = wide SL ~85%; shallow miss held to day-15 ~ -40% theta).
"""
import math, polars as pl
LED = ".cache/honest_miss/ledger_holdmag_5y.parquet"
df = pl.read_parquet(LED).filter(pl.col("tp15").is_not_null() & pl.col("vol_pct").is_not_null() & (pl.col("vol_pct") > 0))
# MAE in sigma units (vol-fair). swing mae is adverse magnitude; use abs.
df = df.with_columns((pl.col("mae15").abs() / pl.col("vol_pct")).alias("mae_sig"))

PREMIUM_MULT, DELTA = 1.82, 0.5
SL_PREM = 0.85                       # wide SL ~85% premium (agent's hold optimum)
SL_SIG = SL_PREM * PREMIUM_MULT / DELTA       # ~3.09 sigma underlying
TP, MIDLOSS = 0.33, 0.40
print(f"SL={SL_PREM:.0%} premium = {SL_SIG:.2f}sigma underlying; TP=+{TP:.0%}, midloss=-{MIDLOSS:.0%}\n")

def ev(tp, slhit):
    return tp*TP - slhit*SL_PREM - max(0, 1-tp-slhit)*MIDLOSS

def stats(t):
    if not t.height: return None
    tp = t["tp15"].mean()
    miss = t.filter(pl.col("tp15") == 0)
    slhit = (miss.filter(pl.col("mae_sig") >= SL_SIG).height / t.height) if t.height else 0
    return dict(N=t.height, tp=tp, slhit=slhit, ev=ev(tp, slhit),
               miss_avgmae=miss["mae_sig"].mean() if miss.height else None)

def z(p, n, p0):
    return round((p-p0)/math.sqrt(p0*(1-p0)/n), 2) if (n > 0 and 0 < p0 < 1) else 0.0

print("tier baselines (hold EV):")
for lo, hi in [(70,74),(75,79),(80,84),(85,99),(75,99)]:
    s = stats(df.filter((pl.col("overall")>=lo)&(pl.col("overall")<=hi)))
    if s: print(f"  {lo}-{hi}: N={s['N']:<5} TP={s['tp']*100:.1f}% SLhit={s['slhit']*100:.1f}% EV={s['ev']*100:+.2f}% missMAE={s['miss_avgmae']:.2f}sig")

NUM = ["stoch","trend","macd","bb","rsi","ta","td","w_adj","w_comp","w_bias","w_mom","vol_pct",
       "cont_lift","mcd_dampen","ich_dampen","scw_dampen","scw_scalar","daily_volume_authority_wave",
       "pre_boost","pre_regime","mis_stress"]

# DEMOTE scan on 75+ deploy zone: which cohort has the WORST hold EV (low TP and/or high SLhit)?
T = df.filter((pl.col("overall")>=75)&(pl.col("overall")<=99))
base = stats(T)
print(f"\n=== 75+ deploy zone EV scan (base EV={base['ev']*100:+.2f}%, SLhit={base['slhit']*100:.1f}%) — worst-EV cohort = demote ===")
rows=[]
for f in NUM:
    if f not in T.columns: continue
    s = T.filter(pl.col(f).is_not_null())
    if s.height < 400 or s[f].n_unique() < 5: continue
    q33, q66 = s[f].quantile(0.33), s[f].quantile(0.66)
    if q33 is None or q33 == q66: continue
    for nm, b in (("lo", s.filter(pl.col(f)<=q33)), ("mid", s.filter((pl.col(f)>q33)&(pl.col(f)<=q66))), ("hi", s.filter(pl.col(f)>q66))):
        if b.height < 150: continue
        st = stats(b)
        # z of the EV gap via the SL-hit rate (the tail driver) vs base
        zz = z(st['slhit'], st['N'], base['slhit'])
        rows.append((f, nm, st['N'], st['tp']*100, st['slhit']*100, st['ev']*100, zz))
rows.sort(key=lambda r: r[5])   # worst EV first
print(f"  {'feat':<14}{'bin':<5}{'N':>6}{'TP%':>7}{'SLhit%':>8}{'EV%':>8}{'SLhit_z':>8}")
for f, nm, n, tp, sl, e, zz in rows[:12]:
    print(f"  {f:<14}{nm:<5}{n:>6}{tp:>7.1f}{sl:>8.1f}{e:>8.2f}{zz:>8.2f}")
