import json, random, numpy as np, polars as pl
BASE={"VL_LO":1.158,"VL_HI":2.174,"KL":0.885,"TL":77.13,"VD_LO":2.487,"VD_HI":3.398,"KD":0.900,"TD":72.16}
L={TAG:pl.read_parquet(".cache/call_boundary_residual_v60/ledger_%s.parquet"%TAG)
        .filter(pl.col("win15").is_not_null()&pl.col("vol_pct").is_not_null()) for TAG in ["5y","10y"]}
A={TAG:(L[TAG]["overall"].to_numpy().astype(float),L[TAG]["vol_pct"].to_numpy().astype(float),
        L[TAG]["win15"].to_numpy().astype(float),np.array([d[:4] for d in L[TAG]["date"].to_list()])) for TAG in L}
def ev(P,TAG):
    ov,vol,w,yr=A[TAG]; o=ov.copy()
    m=(ov>=70)&(ov<=74); s=np.clip((P["VL_HI"]-vol)/(P["VL_HI"]-P["VL_LO"]),0,1); o=np.where(m,o+P["KL"]*s*(P["TL"]-o),o)
    m2=(ov>=75)&(ov<=79); wk=np.clip((vol-P["VD_LO"])/(P["VD_HI"]-P["VD_LO"]),0,1); o=np.where(m2,o-P["KD"]*wk*(o-P["TD"]),o); o=np.rint(o)
    def wr(mask): a=w[mask]; return (a.mean()*100 if len(a) else 0, len(a))
    bw,bn=wr(ov>=75); nw,nn=wr(o>=75)
    b80,_=wr(ov>=80); n80,_=wr(o>=80)
    okyr=True
    if TAG=="5y":
        for y in ["2021","2022","2023","2024","2025"]:
            bb,_=wr((ov>=75)&(yr==y)); aa,_=wr((o>=75)&(yr==y))
            if aa< bb-0.5: okyr=False
    return {"dWR":nw-bw,"dN":nn-bn,"n80_ok":abs(n80-b80)<0.01,"okyr":okyr,"ge75WR":nw,"ge75N":nn}
def passes(P):
    r5=ev(P,"5y"); r10=ev(P,"10y")
    if not(r5["n80_ok"] and r10["n80_ok"] and r5["okyr"]): return None
    if r5["dN"]<600 or r10["dN"]<0 or r5["dWR"]<=0 or r10["dWR"]<=0: return None
    return r5,r10
rng=random.Random(7)
cands=[]
b=passes(BASE)
if b: cands.append((BASE,b,"BASE"))
for _ in range(1200):
    P={k: BASE[k]*(1+rng.uniform(-0.08,0.08)) for k in BASE}
    r=passes(P)
    if r: cands.append((P,r,"drill"))
# utility: 5y dWR primary + small 10y dWR + tiny N
def util(c): r5,r10=c[1]; return r5["dWR"]+0.3*r10["dWR"]+0.0008*r5["dN"]
cands.sort(key=lambda c:-util(c))
print("PASS=%d"%len(cands))
for P,(r5,r10),tag in cands[:4]:
    print("util=%.3f [%s] 5y dWR=%+.2f dN=%+d (WR=%.2f N=%d) | 10y dWR=%+.2f dN=%+d"%(
        util((P,(r5,r10),tag)),tag,r5["dWR"],r5["dN"],r5["ge75WR"],r5["ge75N"],r10["dWR"],r10["dN"]))
best=cands[0][0]
print("LOCK="+json.dumps({k:round(v,4) for k,v in best.items()}))
