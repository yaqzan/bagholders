#!/usr/bin/env python
"""Extract the (P_collapse, median-days-to-2x) Pareto frontier from rb_* sweep results.

A config is DOMINATED if another has collapse<= AND median<= (one strict). The
non-dominated set, sorted by collapse ascending, is monotonic in median by
construction (more collapse => strictly faster). Prints the full surface, the
frontier, and the distinct (DTE,gross,positions) on the frontier (= Stage-2 fine-SL targets)."""
import json, glob, re, sys

POS = {'flat_n2_a50':2,'flat_n3_a33':3,'flat_n4_a25':4,'flat_n5_a20':5,'flat_n10_a10':10}
pat = re.compile(r'rb\d*_d(\d+)_g(\d+)_sl(\d+)')   # rb_ (s1), rb2_ (fine-SL), rb3_ (knee-fill)

pts = []
for f in sorted(glob.glob('experiments/concentration_2x/results/sweep_drill_rb*.json')):
    d = json.load(open(f))
    m = pat.search(d.get('tag',''))
    if not m:
        continue
    dte, gross, sl = int(m.group(1)), int(m.group(2)), int(m.group(3))
    for c in d['cells']:
        n = POS.get(c['cell'])
        if n is None:
            continue
        pts.append(dict(dte=dte, gross=gross, sl=sl, n=n,
                        med=c['median_days_2x'], coll=c['p_collapse']*100,
                        p2x=c['p_2x_ever']*100, dd=c['worst_dd_pct'],
                        comp=c['median_compound_pct']))

def label(p):
    return f"{p['dte']}d/g{p['gross']}/n{p['n']}/SL-{p['sl']}"

# Full surface sorted by median
print(f"=== FULL SURFACE ({len(pts)} configs), sorted by median days->2x ===")
print('{:<20}{:>8}{:>9}{:>7}{:>7}{:>9}'.format('config','medDay','collaps%','P2x%','wDD%','comp%'))
for p in sorted(pts, key=lambda x: x['med']):
    print('{:<20}{:>8.1f}{:>9.3f}{:>7.1f}{:>7.1f}{:>9.1f}'.format(
        label(p), p['med'], p['coll'], p['p2x'], p['dd'], p['comp']))

# Pareto frontier: minimize (coll, med)
front = []
for p in pts:
    dom = any((q['coll'] <= p['coll'] and q['med'] <= p['med'] and
               (q['coll'] < p['coll'] or q['med'] < p['med'])) for q in pts if q is not p)
    if not dom:
        front.append(p)
# de-dup identical (coll,med)
seen=set(); uniq=[]
for p in sorted(front, key=lambda x:(x['coll'], x['med'])):
    k=(round(p['coll'],3), round(p['med'],1))
    if k in seen: continue
    seen.add(k); uniq.append(p)

print(f"\n=== PARETO FRONTIER ({len(uniq)} non-dominated), collapse asc -> median must strictly drop ===")
print('{:<20}{:>8}{:>9}{:>7}{:>7}{:>9}'.format('config','medDay','collaps%','P2x%','wDD%','comp%'))
for p in uniq:
    print('{:<20}{:>8.1f}{:>9.3f}{:>7.1f}{:>7.1f}{:>9.1f}'.format(
        label(p), p['med'], p['coll'], p['p2x'], p['dd'], p['comp']))

print("\n=== Stage-2 fine-SL targets (distinct DTE/gross/positions on frontier) ===")
tg = sorted({(p['dte'], p['gross'], p['n']) for p in uniq})
for dte, gross, n in tg:
    print(f"  {dte}d / g{gross} / n{n}")
