import json, numpy as np, polars as pl
P={"VL_LO":1.158,"VL_HI":2.174,"KL":0.885,"TL":77.13,"VD_LO":2.487,"VD_HI":3.398,"KD":0.900,"TD":72.16}
def newov(ov,vol):
    o=ov.copy()
    m=(ov>=70)&(ov<=74); s=np.clip((P["VL_HI"]-vol)/(P["VL_HI"]-P["VL_LO"]),0,1); o=np.where(m,o+P["KL"]*s*(P["TL"]-o),o)
    m2=(ov>=75)&(ov<=79); wk=np.clip((vol-P["VD_LO"])/(P["VD_HI"]-P["VD_LO"]),0,1); o=np.where(m2,o-P["KD"]*wk*(o-P["TD"]),o)
    return np.rint(o)
for TAG,yrs in [("5y",5),("10y",10)]:
    df=pl.read_parquet(".cache/call_boundary_residual_v60/ledger_%s.parquet"%TAG).filter(pl.col("win15").is_not_null()&pl.col("vol_pct").is_not_null())
    ov=df["overall"].to_numpy().astype(float); vol=df["vol_pct"].to_numpy().astype(float); w=df["win15"].to_numpy().astype(float)
    no=newov(ov,vol)
    def wr(o,lo,hi):
        m=(o>=lo)&(o<=hi); a=w[m]; return (round(a.mean()*100,2) if len(a) else None, int(len(a)))
    print("== %s (per-yr N = N/%d) =="%(TAG,yrs))
    for lo,hi,lbl in [(70,74,"70-74"),(75,79,"75-79"),(80,84,"80-84"),(85,89,"85-89")]:
        b=wr(ov,lo,hi); a=wr(no,lo,hi)
        print("  %s base WR15=%s N=%d (%d/yr) -> VCBW WR15=%s N=%d (%d/yr)"%(lbl,b[0],b[1],b[1]//yrs,a[0],a[1],a[1]//yrs))
    # cumulative gradient
    for thr in [70,75,80,85]:
        bm=ov>=thr; am=no>=thr
        print("  >=%d base WR15=%.2f N=%d -> VCBW WR15=%.2f N=%d"%(thr,w[bm].mean()*100,bm.sum(),w[am].mean()*100,am.sum()))
