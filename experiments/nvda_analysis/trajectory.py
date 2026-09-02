"""Clean NVDA swing table + monthly trajectory + v70 score at turning points."""
import csv, os
from collections import defaultdict

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

price=[]
with open(os.path.join(D,'price_history.csv')) as f:
    for r in csv.DictReader(f):
        price.append((r['date'], float(r['close'])))
price.sort()
dates=[p[0] for p in price]; closes=[p[1] for p in price]

# rolling 21-bar local extrema as swing pivots, then dedupe by 15% alternation
def swings(dates, closes, win=15, pct=0.15):
    piv=[]
    n=len(closes)
    for i in range(n):
        lo=max(0,i-win); hi=min(n,i+win+1)
        seg=closes[lo:hi]
        if closes[i]==max(seg): piv.append((dates[i],closes[i],'PEAK'))
        elif closes[i]==min(seg): piv.append((dates[i],closes[i],'TROUGH'))
    # collapse consecutive same-kind keeping the extreme, require pct move to alternate
    out=[]
    for d,c,k in piv:
        if out and out[-1][2]==k:
            if (k=='PEAK' and c>=out[-1][1]) or (k=='TROUGH' and c<=out[-1][1]):
                out[-1]=(d,c,k)
            continue
        if out and abs(c-out[-1][1])/out[-1][1] < pct:
            # not enough move; replace if more extreme in same eventual dir, else skip
            continue
        out.append((d,c,k))
    return out

sw=swings(dates,closes)
# v70 scores by date
v70={}
comp={}  # date -> (overall,trend,rsi,macd,stoch,bb,ta)
with open(os.path.join(D,'scores_by_version.csv')) as f:
    for r in csv.DictReader(f):
        if r['label']!='v70': continue
        try: ov=int(r['overall'])
        except: continue
        v70[r['date']]=ov
        comp[r['date']]=(ov,r['trend'],r['rsi'],r['macd'],r['stoch'],r['bb'],r['technical_alignment'])

print("=== NVDA SWING TABLE (15-bar local extrema, >=15% alternation) ===")
print(f"{'date':12} {'close':>8} {'kind':7} {'move%':>7}  v70:[ov tr rsi mac sto bb ta]")
prev=None
for d,c,k in sw:
    mv='' if prev is None else f"{(c-prev)/prev*100:+.0f}%"
    cc=comp.get(d)
    cstr=f"{cc[0]:>3} {cc[1]:>2} {cc[2]:>3} {cc[3]:>3} {cc[4]:>3} {cc[5]:>2} {cc[6]:>2}" if cc else "(no v70 row)"
    print(f"{d:12} {c:8.2f} {k:7} {mv:>7}  [{cstr}]")
    prev=c

print("\n=== MONTHLY: close + v70 overall (month-end) ===")
bymon=defaultdict(list)
for d,c in price:
    bymon[d[:7]].append((d,c))
print(f"{'month':8} {'close':>8} {'v70ov':>6} {'tr':>3} {'rsi':>3} {'mac':>3} {'sto':>3} {'bb':>3} {'ta':>3}")
for m in sorted(bymon):
    d,c=bymon[m][-1]
    cc=comp.get(d)
    if cc:
        print(f"{m:8} {c:8.2f} {cc[0]:>6} {cc[1]:>3} {cc[2]:>3} {cc[3]:>3} {cc[4]:>3} {cc[5]:>3} {cc[6]:>3}")
    else:
        print(f"{m:8} {c:8.2f} {'':>6}")
