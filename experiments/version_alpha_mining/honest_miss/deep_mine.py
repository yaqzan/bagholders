"""Deeper honest miss-mine on the v70 call ledger: test interactions + derived
predicates for a z>=3 cohort the single-feature scan (max |z|~2.1) missed.

Single-feature found: 75-79 MID tercile of macd/rsi/trend/ta/td (collinear) is the
miss-cohort (optTP~46.5 vs 50.83 marginal); 70-74 hi vol_pct misses / mid wins.
Hypothesis: these are 'mid-conviction / lift-driven' 75-79 signals (got to the tier
via weekly/boost, not strong components). Test predicates that concentrate that.

optTP15 (the tradable barrier) is the metric. z vs the tier marginal.
"""
import math, polars as pl

LED = ".cache/honest_miss/ledger_5y.parquet"
df = pl.read_parquet(LED).filter(pl.col("opt15").is_not_null())

def z(p, n, p0):
    if n < 1 or not (0 < p0 < 1): return 0.0
    se = math.sqrt(p0*(1-p0)/n)
    return (p-p0)/se if se > 0 else 0.0

def rep(name, sub, base, n_tier):
    if sub.height < 50:
        print(f"  {name:42} N={sub.height:<5} (too small)"); return
    p = sub["opt15"].mean()
    frac = 100*sub.height/n_tier
    print(f"  {name:42} N={sub.height:<5} ({frac:4.1f}% tier) optTP={p*100:5.2f} z={z(p,sub.height,base):+5.2f} vs_marg={ (p-base)*100:+5.2f}")

for lo, hi, label in [(75,79,'75-79'), (70,74,'70-74'), (80,84,'80-84')]:
    tier = df.filter((pl.col("overall")>=lo)&(pl.col("overall")<=hi))
    base = tier["opt15"].mean(); N = tier.height
    print(f"\n=== {label} (N={N}, marginal optTP={base*100:.2f}) — DEMOTE = optTP below marginal ===")
    def q(col, lo_q=0.33, hi_q=0.66):
        s = tier.filter(pl.col(col).is_not_null())
        return s[col].quantile(lo_q), s[col].quantile(hi_q)
    # mid-tercile of the collinear technicals (the single-feature miss)
    for c in ("macd","rsi","trend"):
        a,b = q(c)
        if a is not None and a!=b:
            rep(f"{c}_mid", tier.filter((pl.col(c)>a)&(pl.col(c)<=b)), base, N)
    # 2-feature mid intersections (does collinear stacking concentrate the miss?)
    ma,mb = q("macd"); ra,rb = q("rsi"); ta_,tb = q("trend")
    if None not in (ma,ra):
        rep("macd_mid & rsi_mid", tier.filter((pl.col("macd")>ma)&(pl.col("macd")<=mb)&(pl.col("rsi")>ra)&(pl.col("rsi")<=rb)), base, N)
    if None not in (ma,ra,ta_):
        rep("macd&rsi&trend all mid", tier.filter((pl.col("macd")>ma)&(pl.col("macd")<=mb)&(pl.col("rsi")>ra)&(pl.col("rsi")<=rb)&(pl.col("trend")>ta_)&(pl.col("trend")<=tb)), base, N)
    # derived: component dispersion (std of the 4 technicals) — low dispersion = all-mid = ambiguous
    disp = (pl.concat_list(["trend","macd","rsi","stoch"]).list.std()).alias("disp")
    td2 = tier.with_columns(disp)
    da,db = td2["disp"].quantile(0.33), td2["disp"].quantile(0.66)
    if da is not None and da!=db:
        rep("low component-dispersion", td2.filter(pl.col("disp")<=da), base, N)
        rep("high component-dispersion", td2.filter(pl.col("disp")>db), base, N)
    # lift-driven: continuation-lifted, earn-boosted, weekly-driven into the tier
    rep("cont_lift>0", tier.filter(pl.col("cont_lift")>0), base, N)
    rep("scw_dampen>0.5", tier.filter(pl.col("scw_dampen")>0.5), base, N)
    rep("ich_dampen>0", tier.filter(pl.col("ich_dampen")>0), base, N)
    rep("mcd_dampen>0", tier.filter(pl.col("mcd_dampen")>0), base, N)
    rep("sector_breadth_wave!=0", tier.filter(pl.col("sector_breadth_wave")!=0), base, N)
    rep("dvaw<0 (vol authority neg)", tier.filter(pl.col("daily_volume_authority_wave")<0), base, N)
    # vol_pct interaction (the 70-74 promote signal): hi vol = miss?
    va,vb = q("vol_pct")
    if va is not None and va!=vb:
        rep("vol_pct hi (top tercile)", tier.filter(pl.col("vol_pct")>vb), base, N)
        rep("vol_pct mid", tier.filter((pl.col("vol_pct")>va)&(pl.col("vol_pct")<=vb)), base, N)
    # macd_mid AND hi vol (mid-conviction + high vol = double trouble?)
    if None not in (ma,vb):
        rep("macd_mid & vol_pct_hi", tier.filter((pl.col("macd")>ma)&(pl.col("macd")<=mb)&(pl.col("vol_pct")>vb)), base, N)
