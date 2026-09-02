import os, json, numpy as np, polars as pl
P = {"VL_LO":1.158,"VL_HI":2.174,"KL":0.885,"TL":77.13,"VD_LO":2.487,"VD_HI":3.398,"KD":0.900,"TD":72.16}
for TAG in ["5y","10y"]:
    f=".cache/call_boundary_residual_v60/ledger_%s.parquet"%TAG
    if not os.path.exists(f): print(TAG,"MISSING"); continue
    df=pl.read_parquet(f).filter(pl.col("win15").is_not_null()&pl.col("vol_pct").is_not_null())
    ov=df["overall"].to_numpy().astype(float); vol=df["vol_pct"].to_numpy().astype(float)
    w15=df["win15"].to_numpy().astype(float)
    def newov(P):
        o=ov.copy()
        m=(ov>=70)&(ov<=74); s=np.clip((P["VL_HI"]-vol)/(P["VL_HI"]-P["VL_LO"]),0,1); o=np.where(m,o+P["KL"]*s*(P["TL"]-o),o)
        m2=(ov>=75)&(ov<=79); wk=np.clip((vol-P["VD_LO"])/(P["VD_HI"]-P["VD_LO"]),0,1); o=np.where(m2,o-P["KD"]*wk*(o-P["TD"]),o)
        return np.rint(o)
    def wr(mask): a=w15[mask]; return (round(a.mean()*100,2),int(len(a))) if len(a) else (None,0)
    res={}
    for lbl,thr in [("ge75",75),("ge80",80)]:
        b=wr(ov>=thr); n=wr(newov(P)>=thr); res[lbl]={"base":b,"vcbw":n,"dWR":round(n[0]-b[0],2),"dN":n[1]-b[1]}
    print(TAG, json.dumps(res))
