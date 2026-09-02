"""Quick inline exploration: NVDA swings, current signal, crude per-version accuracy."""
import csv, json, os, math
from datetime import date
from collections import defaultdict

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# ---- price ----
price = []
with open(os.path.join(D, 'price_history.csv')) as f:
    for r in csv.DictReader(f):
        price.append((r['date'], float(r['close']), float(r['high']), float(r['low'])))
price.sort()
dates = [p[0] for p in price]
close = {p[0]: p[1] for p in price}
closes = [p[1] for p in price]

print(f"NVDA price window: {dates[0]} .. {dates[-1]}  ({len(price)} bars)")
print(f"  close start={closes[0]:.2f}  end={closes[-1]:.2f}  min={min(closes):.2f}  max={max(closes):.2f}")

# ---- swing detection: ZigZag ~15% threshold on close ----
def zigzag(seq, dts, pct=0.15):
    piv = [(dts[0], seq[0], 'start')]
    last_pivot_i = 0
    direction = 0  # 1 up, -1 down
    for i in range(1, len(seq)):
        change = (seq[i] - seq[last_pivot_i]) / seq[last_pivot_i]
        if direction >= 0 and change <= -pct:
            piv.append((dts[i], seq[i], 'down_from_peak'))
            last_pivot_i = i; direction = -1
        elif direction <= 0 and change >= pct:
            piv.append((dts[i], seq[i], 'up_from_trough'))
            last_pivot_i = i; direction = 1
        else:
            # extend pivot if new extreme in current direction
            if direction >= 0 and seq[i] > seq[last_pivot_i]:
                last_pivot_i = i
            elif direction <= 0 and seq[i] < seq[last_pivot_i]:
                last_pivot_i = i
    return piv

piv = zigzag(closes, dates, 0.18)
print(f"\n=== NVDA major swings (ZigZag 18% on close) ===")
prev = None
for d, p, kind in piv:
    delta = '' if prev is None else f"{(p-prev)/prev*100:+.0f}%"
    print(f"  {d}  {p:8.2f}  {kind:16} {delta}")
    prev = p

# ---- forward returns at each date ----
fwd = {}
for i, (d, c, hi, lo) in enumerate(price):
    f15 = closes[i+15] if i+15 < len(closes) else None
    f30 = closes[i+30] if i+30 < len(closes) else None
    fwd[d] = (
        (f15 - c) / c if f15 else None,
        (f30 - c) / c if f30 else None,
    )

# ---- scores by version ----
rows = defaultdict(list)  # label -> list of (date, overall, trend, rsi, macd, stoch, bb, ta)
latest = {}               # label -> last row dict
with open(os.path.join(D, 'scores_by_version.csv')) as f:
    for r in csv.DictReader(f):
        lbl = r['label']
        try:
            ov = int(r['overall'])
        except:
            continue
        rec = (r['date'], ov, r['trend'], r['rsi'], r['macd'], r['stoch'], r['bb'], r['technical_alignment'])
        rows[lbl].append(rec)
for lbl in rows:
    rows[lbl].sort()
    latest[lbl] = rows[lbl][-1]

# ---- current signal across a curated set ----
focus = ['v70','v69','v66','v65','v64','v60','v59','v57','v53','v50','v46','v44','v37','v28','v18']
print(f"\n=== CURRENT NVDA SIGNAL (latest row per version) ===")
print(f"{'ver':6} {'date':12} {'overall':>7} {'trend':>5} {'rsi':>4} {'macd':>4} {'stoch':>5} {'bb':>4} {'ta':>4}")
for lbl in focus:
    if lbl in latest:
        d, ov, tr, rsi, macd, st, bb, ta = latest[lbl]
        print(f"{lbl:6} {d:12} {ov:7} {tr:>5} {rsi:>4} {macd:>4} {st:>5} {bb:>4} {ta:>4}")

# ---- crude per-version directional accuracy ----
# IC = pearson(score-50, fwd15) over the 2024-01..now window
def pearson(xs, ys):
    n=len(xs)
    if n<10: return None
    mx=sum(xs)/n; my=sum(ys)/n
    sx=sum((x-mx)**2 for x in xs); sy=sum((y-my)**2 for y in ys)
    if sx==0 or sy==0: return None
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    return cov/math.sqrt(sx*sy)

print(f"\n=== PER-VERSION DIRECTIONAL FIT (IC = corr(overall-50, fwd15)) ===")
res=[]
for lbl, recs in rows.items():
    xs=[]; ys=[]
    callwin=calln=putwin=putn=0
    for d, ov, *_ in recs:
        f15, f30 = fwd.get(d, (None,None))
        if f15 is None: continue
        xs.append(ov-50); ys.append(f15)
        if ov>=75:
            calln+=1; callwin += 1 if f15>0 else 0
        elif ov<=25:
            putn+=1; putwin += 1 if f15<0 else 0
    ic = pearson(xs, ys)
    if ic is None: continue
    res.append((lbl, ic, calln, (callwin/calln if calln else None), putn, (putwin/putn if putn else None)))
res.sort(key=lambda r:-(r[1] or -9))
print(f"{'ver':6} {'IC15':>7} {'callN':>6} {'call+%':>7} {'putN':>5} {'put+%':>6}")
for lbl, ic, cn, cw, pn, pw in res[:18]:
    cwp = f"{cw*100:.0f}" if cw is not None else '-'
    pwp = f"{pw*100:.0f}" if pw is not None else '-'
    print(f"{lbl:6} {ic:7.3f} {cn:6} {cwp:>7} {pn:5} {pwp:>6}")
print("  ... (bottom 5)")
for lbl, ic, cn, cw, pn, pw in res[-5:]:
    cwp = f"{cw*100:.0f}" if cw is not None else '-'
    pwp = f"{pw*100:.0f}" if pw is not None else '-'
    print(f"{lbl:6} {ic:7.3f} {cn:6} {cwp:>7} {pn:5} {pwp:>6}")
