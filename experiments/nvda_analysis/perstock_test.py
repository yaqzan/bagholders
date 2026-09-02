"""Per-stock test: apply the NVDA divergence hypothesis to NVDA itself, train(2024-25)/test(2026),
on BOTH the generic price-up metric AND the option-TP barrier. Shows the overfitting reality of
per-ticker tuning + the generic-vs-option gap, with explicit N so 'tiny-N = noise' is visible.
"""
import csv, os, math
from collections import defaultdict

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# NVDA price + v70 components
price = []
with open(os.path.join(D, 'price_history.csv')) as f:
    for r in csv.DictReader(f):
        price.append((r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close'])))
price.sort()
dates = [p[0] for p in price]
o = {d: p[1] for d, *p in [(x[0], *x[1:]) for x in price]}
hi = {p[0]: p[2] for p in price}; lo = {p[0]: p[3] for p in price}; cl = {p[0]: p[4] for p in price}
idx = {d: i for i, d in enumerate(dates)}
closes = [p[4] for p in price]

# 60-bar realized daily vol at each date
sig = {}
for i, d in enumerate(dates):
    lob = max(0, i - 59)
    rets = [closes[j] / closes[j-1] - 1 for j in range(max(1, lob), i+1)]
    if len(rets) >= 20:
        m = sum(rets)/len(rets)
        sig[d] = math.sqrt(sum((x-m)**2 for x in rets)/(len(rets)-1))

# v70 components
comp = {}
with open(os.path.join(D, 'scores_by_version.csv')) as f:
    for r in csv.DictReader(f):
        if r['label'] != 'v70':
            continue
        try:
            comp[r['date']] = {k: int(r[k]) for k in ('overall', 'trend', 'stoch', 'rsi', 'bb', 'macd')}
        except Exception:
            pass

def fwd15_up(d):
    i = idx[d]
    if i + 15 >= len(dates):
        return None
    return (closes[i+15] - closes[i]) / closes[i]

def opt15_call(d):
    """call option barrier: +0.901sigma favorable before +0.772sigma adverse, within 15 cal days.
    same-bar tie / day-15 expire = loss (conservative). None if too recent."""
    s = sig.get(d); i = idx[d]
    if not s:
        return None
    entry = closes[i]
    base = dates[i]
    from datetime import date as _date
    y, m, dd = map(int, base.split('-')); bd = _date(y, m, dd)
    t_up = t_dn = None
    last_cd = 0
    j = i + 1
    while j < len(dates):
        y2, m2, d2 = map(int, dates[j].split('-')); cd = (_date(y2, m2, d2) - bd).days
        if cd > 15:
            break
        last_cd = cd
        up = (hi[dates[j]] - entry) / entry / s
        dn = (entry - lo[dates[j]]) / entry / s
        if t_up is None and up >= 0.901:
            t_up = cd
        if t_dn is None and dn >= 0.772:
            t_dn = cd
        if t_up is not None or t_dn is not None:
            break
        j += 1
    if t_up is not None and (t_dn is None or t_up < t_dn):
        return 1
    if t_dn is not None and (t_up is None or t_dn <= t_up):
        return 0
    if last_cd >= 15:
        return 0
    return None

def period(d):
    if d < '2025-01-01': return '2024'
    if d < '2026-01-01': return '2025'
    if d <= '2026-05-15': return '2026<=cutoff'
    return '2026>cutoff'

# RULES
def dip(c): return c['trend'] <= 40 and c['stoch'] >= 85          # buy capitulation
def top(c): return c['trend'] >= 90 and c['stoch'] <= 15          # fade exhaustion

print("=== NVDA DIP-BUY rule (trend<=40 & stoch>=85): forward-up vs option-call-TP, by period ===")
print(f"{'period':14} {'Nsig':>5} {'%price-up15':>11} {'meanRet15':>10} {'optCallTP%':>10} {'optN':>5}")
agg = defaultdict(lambda: {'n':0, 'up':0, 'ret':[], 'ow':0, 'on':0})
for d, c in sorted(comp.items()):
    if not dip(c): continue
    p = period(d); a = agg[p]; a['n'] += 1
    fu = fwd15_up(d)
    if fu is not None:
        a['up'] += 1 if fu > 0 else 0; a['ret'].append(fu)
    ow = opt15_call(d)
    if ow is not None:
        a['on'] += 1; a['ow'] += ow
for p in ['2024', '2025', '2026<=cutoff', '2026>cutoff']:
    a = agg[p]
    nret = len(a['ret'])
    up = f"{100*a['up']/nret:.0f}% ({a['up']}/{nret})" if nret else "-"
    mr = f"{100*sum(a['ret'])/nret:+.1f}%" if nret else "-"
    ot = f"{100*a['ow']/a['on']:.0f}%" if a['on'] else "-"
    print(f"{p:14} {a['n']:>5} {up:>11} {mr:>10} {ot:>10} {a['on']:>5}")

print("\n=== NVDA TOP-FADE rule (trend>=90 & stoch<=15): forward-DOWN (fade works if price falls), by period ===")
print(f"{'period':14} {'Nsig':>5} {'%price-DOWN15':>13} {'meanRet15':>10}")
agg2 = defaultdict(lambda: {'n':0, 'dn':0, 'ret':[]})
for d, c in sorted(comp.items()):
    if not top(c): continue
    p = period(d); a = agg2[p]; a['n'] += 1
    fu = fwd15_up(d)
    if fu is not None:
        a['dn'] += 1 if fu < 0 else 0; a['ret'].append(fu)
for p in ['2024', '2025', '2026<=cutoff', '2026>cutoff']:
    a = agg2[p]; nret = len(a['ret'])
    dn = f"{100*a['dn']/nret:.0f}% ({a['dn']}/{nret})" if nret else "-"
    mr = f"{100*sum(a['ret'])/nret:+.1f}%" if nret else "-"
    print(f"{p:14} {a['n']:>5} {dn:>13} {mr:>10}")

print("\nNOTE: 'price-up15' is the GENERIC metric (the NVDA '72%' lived here). 'optCallTP%' is the")
print("OPTION barrier the strategy trades. The gap between them is the lesson. 2026 N is the other lesson.")
