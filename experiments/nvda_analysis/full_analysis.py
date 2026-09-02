"""Comprehensive inline NVDA analysis (workflow fallback). Pure stdlib."""
import csv, json, os, math
from collections import defaultdict

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
def P(*a): print(*a)

# ---------- load ----------
price=[]
with open(os.path.join(D,'price_history.csv')) as f:
    for r in csv.DictReader(f):
        price.append((r['date'],float(r['open']),float(r['high']),float(r['low']),float(r['close'])))
price.sort()
dates=[p[0] for p in price]; closes=[p[4] for p in price]
idx={d:i for i,d in enumerate(dates)}

ind={}
with open(os.path.join(D,'indicators.csv')) as f:
    for r in csv.DictReader(f):
        ind[r['date']]=r

def fnum(x):
    try: return float(x)
    except: return None

# forward returns
fwd15={}; fwd30={}
for i,d in enumerate(dates):
    c=closes[i]
    fwd15[d]=(closes[i+15]-c)/c if i+15<len(closes) else None
    fwd30[d]=(closes[i+30]-c)/c if i+30<len(closes) else None

# scores by version
sv=defaultdict(dict)   # label -> date -> row(dict of overall/comps)
with open(os.path.join(D,'scores_by_version.csv')) as f:
    for r in csv.DictReader(f):
        try: r['overall']=int(r['overall'])
        except: continue
        for k in ('trend','rsi','macd','stoch','bb','technical_alignment','volume'):
            try: r[k]=int(r[k])
            except: r[k]=None
        sv[r['label']][r['date']]=r

def pearson(xs,ys):
    n=len(xs)
    if n<20: return None
    mx=sum(xs)/n; my=sum(ys)/n
    sx=sum((x-mx)**2 for x in xs); sy=sum((y-my)**2 for y in ys)
    if sx==0 or sy==0: return None
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(sx*sy)

# ---------- swing table ----------
def swings(dates,closes,win=15,pct=0.15):
    piv=[]; n=len(closes)
    for i in range(n):
        seg=closes[max(0,i-win):min(n,i+win+1)]
        if closes[i]==max(seg): piv.append((dates[i],closes[i],'PEAK'))
        elif closes[i]==min(seg): piv.append((dates[i],closes[i],'TROUGH'))
    out=[]
    for d,c,k in piv:
        if out and out[-1][2]==k:
            if (k=='PEAK' and c>=out[-1][1]) or (k=='TROUGH' and c<=out[-1][1]): out[-1]=(d,c,k)
            continue
        if out and abs(c-out[-1][1])/out[-1][1]<pct: continue
        out.append((d,c,k))
    return out
SW=swings(dates,closes)
PIVDATES={d for d,_,_ in SW}

P("########## A. VERSION ACCURACY ##########")
v70=sv['v70']
rows=[]
for lbl,recs in sv.items():
    items=sorted(recs.items())
    if len(items)<590: continue
    xs=[];ys=[];ys30=[]
    cn=cw15=cw30=pn=pw=0
    ovs=[]
    for d,r in items:
        ov=r['overall']; ovs.append(ov)
        f15=fwd15.get(d); f30=fwd30.get(d)
        if f15 is not None:
            xs.append(ov-50); ys.append(f15); ys30.append(f30 if f30 is not None else 0)
            if ov>=75:
                cn+=1; cw15+= 1 if f15>0 else 0; cw30+= 1 if (f30 or 0)>0 else 0
            elif ov<=25:
                pn+=1; pw+= 1 if f15<0 else 0
    ic15=pearson(xs,ys); ic30=pearson(xs,ys30)
    ovs_s=sorted(ovs); n=len(ovs_s)
    p10=ovs_s[n//10]; p90=ovs_s[9*n//10]
    in4070=sum(1 for o in ovs if 40<=o<=70)/n*100
    # swing scorecard: at each pivot, score vs own trailing-60d median, right direction?
    med_right=0; med_tot=0
    rd=dict(items)
    for d,c,k in SW:
        if d not in rd: continue
        i=idx.get(d)
        # trailing 60 trading-day median of overall
        trail=[rd[dd]['overall'] for dd in dates[max(0,i-60):i] if dd in rd]
        if len(trail)<20: continue
        med=sorted(trail)[len(trail)//2]
        ov=rd[d]['overall']; med_tot+=1
        if k=='PEAK' and ov<med: med_right+=1      # leaning bearish into a top = right
        if k=='TROUGH' and ov>med: med_right+=1    # leaning bullish into a bottom = right
    rows.append((lbl,ic15,ic30,cn,(cw15/cn if cn else None),(cw30/cn if cn else None),
                 pn,(pw/pn if pn else None),min(ovs),max(ovs),p10,p90,in4070,
                 (med_right/med_tot if med_tot else None),med_tot))
rows.sort(key=lambda r:-(r[1] or -9))
P(f"{'ver':5} {'IC15':>6} {'IC30':>6} {'callN':>5} {'c+15%':>6} {'c+30%':>6} {'putN':>4} {'p+%':>4} {'min':>3} {'max':>3} {'p10':>3} {'p90':>3} {'%40-70':>6} {'swingRt':>7}")
for r in rows:
    lbl,ic15,ic30,cn,cw15,cw30,pn,pw,mn,mx,p10,p90,in4070,sr,_=r
    g=lambda v: f"{v*100:.0f}" if v is not None else '-'
    P(f"{lbl:5} {(ic15 or 0):6.3f} {(ic30 or 0):6.3f} {cn:5} {g(cw15):>6} {g(cw30):>6} {pn:4} {g(pw):>4} {mn:3} {mx:3} {p10:3} {p90:3} {in4070:6.0f} {g(sr):>7}")
# aggregate
ic15s=[r[1] for r in rows if r[1] is not None]
P(f"\nIC15 across {len(ic15s)} versions: mean={sum(ic15s)/len(ic15s):+.3f} min={min(ic15s):+.3f} max={max(ic15s):+.3f}")
P(f"NOTE: positive IC means higher score -> higher fwd return. Near 0 = no predictive value on NVDA.")

P("\n########## B. TURNING-POINT PATTERN ##########")
P("Swing pivots w/ raw indicators + v70 components:")
P(f"{'date':11} {'close':>7} {'kind':6} {'mv%':>5} | RAW rsi stoch mh  bbpos e50% | COMP tr rsi sto bb mac ta ov")
prev=None
for d,c,k in SW:
    mv='' if prev is None else f"{(c-prev)/prev*100:+.0f}"
    r=ind.get(d,{})
    rr=fnum(r.get('rsi')); rs=fnum(r.get('stoch')); mh=fnum(r.get('macd_hist'))
    ub=fnum(r.get('upper_band')); lb=fnum(r.get('lower_band'))
    bbpos=(c-lb)/(ub-lb) if (ub and lb and ub>lb) else None
    e50=fnum(r.get('ema_50')); e50p=((c-e50)/e50*100) if e50 else None
    cc=v70.get(d,{})
    def g(x,f="{:.0f}"):
        return f.format(x) if x is not None else '-'
    P(f"{d:11} {c:7.2f} {k:6} {mv:>5} | {g(rr):>3} {g(rs):>5} {g(mh,'{:.1f}'):>5} {g(bbpos,'{:.2f}'):>5} {g(e50p,'{:+.0f}'):>5} | "
      f"{g(cc.get('trend')):>2} {g(cc.get('rsi')):>3} {g(cc.get('stoch')):>3} {g(cc.get('bb')):>2} {g(cc.get('macd')):>3} {g(cc.get('technical_alignment')):>2} {g(cc.get('overall')):>2}")
    prev=c

# divergence fingerprint at peaks vs troughs (v70 components)
def comp_at(d,k):
    r=v70.get(d,{}); return r.get(k)
peaks=[d for d,_,kk in SW if kk=='PEAK']; troughs=[d for d,_,kk in SW if kk=='TROUGH']
def avg(vals): vals=[v for v in vals if v is not None]; return sum(vals)/len(vals) if vals else None
P(f"\nPEAK fingerprint (v70 comp avg, N={len(peaks)}): trend={avg([comp_at(d,'trend') for d in peaks]):.0f} "
  f"stoch={avg([comp_at(d,'stoch') for d in peaks]):.0f} rsi={avg([comp_at(d,'rsi') for d in peaks]):.0f} "
  f"bb={avg([comp_at(d,'bb') for d in peaks]):.0f} overall={avg([comp_at(d,'overall') for d in peaks]):.0f}")
P(f"TROUGH fingerprint (v70 comp avg, N={len(troughs)}): trend={avg([comp_at(d,'trend') for d in troughs]):.0f} "
  f"stoch={avg([comp_at(d,'stoch') for d in troughs]):.0f} rsi={avg([comp_at(d,'rsi') for d in troughs]):.0f} "
  f"bb={avg([comp_at(d,'bb') for d in troughs]):.0f} overall={avg([comp_at(d,'overall') for d in troughs]):.0f}")

# RULE TESTS on v70 components across all bars
def rule_eval(name, predicate, target_kind, horizon=15):
    # predicate(row,date)->bool ; flag bars; check (a) coverage of pivots within +/-5 bars, (b) fwd return sign
    flagged=[d for d in dates if d in v70 and predicate(v70[d],d)]
    # pivot capture: a target pivot is 'caught' if any flagged bar within [-5,+2] of it
    targets=[d for d,_,kk in SW if kk==target_kind]
    caught=0
    for td in targets:
        ti=idx[td]
        if any(abs(idx[fd]-ti)<=5 and idx[fd]<=ti+2 for fd in flagged): caught+=1
    # fwd outcome of flagged bars
    if target_kind=='PEAK':  # expect DOWN move after
        good=sum(1 for d in flagged if (fwd15.get(d) or 0)<0);
    else:
        good=sum(1 for d in flagged if (fwd15.get(d) or 0)>0)
    n=len([d for d in flagged if fwd15.get(d) is not None])
    P(f"  RULE {name}: flagged={len(flagged)} bars | pivots_caught={caught}/{len(targets)} {target_kind} | "
      f"fwd15 correct-dir={good}/{n} ({100*good/n:.0f}%)" if n else f"  RULE {name}: flagged={len(flagged)} (no fwd)")

P("\nFalsifiable rule tests (v70 components):")
rule_eval("TOP: trend>=90 & stoch<=10 & bb>=60", lambda r,d: r['trend']>=90 and r['stoch']<=10 and r['bb']>=60, 'PEAK')
rule_eval("TOP: trend>=90 & stoch<=15 & rsi<=35", lambda r,d: r['trend']>=90 and r['stoch']<=15 and r['rsi']<=35, 'PEAK')
rule_eval("BOT: trend<=40 & stoch>=85", lambda r,d: r['trend']<=40 and r['stoch']>=85, 'TROUGH')
rule_eval("BOT: stoch>=90 & rsi>=55 & bb<=40", lambda r,d: r['stoch']>=90 and r['rsi']>=55 and r['bb']<=40, 'TROUGH')

P("\n########## C. SCORING FIDELITY ##########")
# trend saturation
tvals=[r['trend'] for r in v70.values() if r['trend'] is not None]
P(f"TREND comp on NVDA (v70): n={len(tvals)} >=90: {100*sum(1 for t in tvals if t>=90)/len(tvals):.0f}%  "
  f">=80:{100*sum(1 for t in tvals if t>=80)/len(tvals):.0f}%  <=40:{100*sum(1 for t in tvals if t<=40)/len(tvals):.0f}%  "
  f"min={min(tvals)} max={max(tvals)} median={sorted(tvals)[len(tvals)//2]}")
# TA inertness
def stats(vals):
    vals=[v for v in vals if v is not None]; n=len(vals); m=sum(vals)/n
    sd=math.sqrt(sum((v-m)**2 for v in vals)/n)
    return m,sd,min(vals),max(vals)
for comp in ('trend','rsi','stoch','bb','macd','technical_alignment','overall'):
    m,sd,mn,mx=stats([r[comp] for r in v70.values()])
    # corr with fwd15
    xs=[];ys=[]
    for d,r in v70.items():
        f=fwd15.get(d)
        if f is not None and r[comp] is not None: xs.append(r[comp]); ys.append(f)
    ic=pearson(xs,ys)
    P(f"  {comp:20} mean={m:5.1f} sd={sd:5.1f} range=[{mn},{mx}] corr(comp,fwd15)={ (ic if ic is not None else 0):+.3f}")

# component vs raw faithfulness at ~16 dates (pivots + a few)
P("\nComponent-vs-RAW cross-check (v70):")
P(f"{'date':11} | rawRSI->rsiC | rawSTOCH->stoC | macdH->macdC | bbpos->bbC | price_vs_e50->trendC")
sample=[d for d,_,_ in SW]
for d in sample:
    r=ind.get(d,{}); cc=v70.get(d,{})
    rr=fnum(r.get('rsi')); rs=fnum(r.get('stoch')); mh=fnum(r.get('macd_hist'))
    ub=fnum(r.get('upper_band')); lb=fnum(r.get('lower_band')); c=closes[idx[d]]
    bbpos=(c-lb)/(ub-lb) if (ub and lb and ub>lb) else None
    e50=fnum(r.get('ema_50')); e50p=((c-e50)/e50*100) if e50 else None
    def g(x,f="{:.0f}"): return f.format(x) if x is not None else '-'
    P(f"{d:11} | {g(rr):>4}->{g(cc.get('rsi')):>3} | {g(rs):>5}->{g(cc.get('stoch')):>3} | "
      f"{g(mh,'{:+.1f}'):>5}->{g(cc.get('macd')):>3} | {g(bbpos,'{:.2f}'):>4}->{g(cc.get('bb')):>3} | {g(e50p,'{:+.0f}'):>4}%->{g(cc.get('trend')):>3}")

P("\n########## D. CURRENT SIGNAL ##########")
last=sorted(v70.items())[-1]
P(f"Latest v70 row {last[0]}: {json.dumps({k:last[1][k] for k in ('overall','trend','rsi','macd','stoch','bb','technical_alignment','price','pct_from_ema50','pct_from_ema200','bb_position','regime_composite','regime_multiplier','volume_signal','next_earnings')}, default=str)}")
# reference class: v70-family >=75 outcomes
fam=['v70','v66','v65','v64','v60','v59']
P(f"\nReference class: when {fam} put NVDA >=75, forward outcomes:")
seen=set(); cn=cw15=cw30=0; samples=[]
for lbl in fam:
    for d,r in sorted(sv[lbl].items()):
        if r['overall']>=75 and (d) not in seen:
            seen.add(d)
            f15=fwd15.get(d); f30=fwd30.get(d)
            if f15 is None: continue
            cn+=1; cw15+= 1 if f15>0 else 0; cw30+= 1 if (f30 or 0)>0 else 0
            samples.append((d,r['overall'],f15,f30))
P(f"  unique >=75 days w/ fwd data: N={cn}  fwd15>0: {cw15}/{cn} ({100*cw15/cn:.0f}%)  fwd30>0: {cw30}/{cn} ({100*cw30/cn:.0f}%)" if cn else "  N=0")
for d,ov,f15,f30 in samples[:40]:
    P(f"    {d} ov={ov} fwd15={f15*100:+.1f}% fwd30={(f30*100 if f30 is not None else float('nan')):+.1f}%")
