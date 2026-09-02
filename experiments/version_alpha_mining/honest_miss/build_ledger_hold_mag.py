"""Magnitude-aware HOLD ledger: per 75+ call signal, capture TP-reach (hold win) AND
the 15d MAE (max adverse excursion %), MFE (%), MFE_sigma — so the hold strat's real
risk (the ~18% that miss AND ride the wide SL down = deep losers) can be mined per
cohort. Binary TP-reach was uniform ~82%; loss MAGNITUDE may not be.
HOLD barrier = TP 1.274 sigma, no early stop (m=10). Holdout-locked <=2026-05-15.
"""
import os, json, time
from collections import namedtuple
os.environ.setdefault("PYTHONIOENCODING", "utf-8"); os.environ.setdefault("PYTHONUTF8", "1")
from database.trader_database import DB
import assess_scores as A

CUTOFF = "2026-05-15"; START = os.environ.get("LEDGER_START", "2021-05-15")
LO, HI, VERSION_ID = 70, 99, 70
HOLD = {'k_low': 1.274, 'm_low': 10.0, 'k_high': 1.274, 'm_high': 10.0}
Peak = namedtuple("Peak", ["symbol_id", "date", "overall"])
WI = ["stoch","trend","macd","bb","rsi","ta","td","w_adj","w_comp","w_bias","w_mom",
      "wadj_completed","wadj_partial","weekly_adj_gap","cont_lift","mcd_dampen","ich_dampen",
      "scw_dampen","scw_scalar","daily_volume_authority_wave","sector_breadth_wave",
      "pre_boost","pre_regime","mis_stress"]

def main():
    t0=time.time()
    rows=DB.execute_sql("SELECT symbol,date,overall,weight_info FROM scores WHERE version_id=%d "
        "AND overall BETWEEN %d AND %d AND date BETWEEN '%s' AND '%s' AND weight_info IS NOT NULL"
        % (VERSION_ID,LO,HI,START,CUTOFF)).fetchall()
    print("pulled",len(rows),"in %.1fs"%(time.time()-t0),flush=True)
    peaks=[]; wiby={}
    for sym,d,ov,wi in rows:
        peaks.append(Peak(sym,d,int(ov)))
        try: wiby[(sym,d)]=json.loads(wi)
        except: wiby[(sym,d)]={}
    # HOLD barrier walk (TP-reach + magnitude)
    A.SWING_K_LOW,A.SWING_M_LOW=HOLD['k_low'],HOLD['m_low']
    A.SWING_K_HIGH,A.SWING_M_HIGH=HOLD['k_high'],HOLD['m_high']
    t1=time.time()
    try:
        res=A._forward_walk_subset(peaks)
    finally:
        A.SWING_K_LOW,A.SWING_M_LOW=2.0,5.0; A.SWING_K_HIGH,A.SWING_M_HIGH=1.0,2.0
    print("hold walk",len(res),"in %.1fs"%(time.time()-t1),flush=True)
    recs=[]
    for r in res:
        sw=r.get("swing",{}); s15=sw.get("15d") or {}; s7=sw.get("7d") or {}; s30=sw.get("30d") or {}
        def win(s):
            return None if (not s or s.get("result") is None) else (1 if s["result"]=="win" else 0)
        sym,d=r["symbol"],r["date"]
        rec={"symbol":sym,"date":d.isoformat() if hasattr(d,"isoformat") else str(d),
             "overall":int(r["score"]),"vol_pct":float(r["vol_pct"]) if r.get("vol_pct") is not None else None,
             "tp7":win(s7),"tp15":win(s15),"tp30":win(s30),
             "mae15":s15.get("mae"),"mfe15":s15.get("mfe"),"mfe_sigma15":s15.get("mfe_sigma")}
        wi=wiby.get((sym,d),{})
        for f in WI:
            v=wi.get(f); rec[f]=float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) else (float(int(v)) if isinstance(v,bool) else None)
        recs.append(rec)
    import polars as pl
    os.makedirs(".cache/honest_miss",exist_ok=True)
    df=pl.DataFrame(recs,infer_schema_length=None).filter(pl.col("tp15").is_not_null())
    df.write_parquet(".cache/honest_miss/ledger_holdmag_5y.parquet")
    print("WROTE ledger_holdmag_5y.parquet N=",df.height,flush=True)
    for lo,hi in [(70,74),(75,79),(80,84),(85,99),(75,99)]:
        t=df.filter((pl.col("overall")>=lo)&(pl.col("overall")<=hi))
        if not t.height: continue
        miss=t.filter(pl.col("tp15")==0)
        print(f"  {lo}-{hi}: N={t.height} TPreach={t['tp15'].mean()*100:.1f}% | on MISSES: avgMAE={miss['mae15'].mean():.1f}% medMAE={miss['mae15'].median():.1f}% avgMFEsig={miss['mfe_sigma15'].mean():.2f}",flush=True)

if __name__=="__main__":
    main()
