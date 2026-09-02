"""Rank the Track B coarse frontier for the 'fastest-to-2x, collapse-tolerant' objective.
Reads results/sweep_coarse.json (27 flat cells + cascade_ref, pooled over 38 quarterly
rolling starts). Prints cells sorted by P(reach 2x) then speed, marks the cascade baseline,
and flags the Pareto frontier on (maximize P(2x ever), minimize P(collapse))."""
import json, os

P = os.path.join(os.path.dirname(__file__), 'results', 'sweep_coarse.json')
d = json.load(open(P))
cells = d['cells']

def g(c, k):
    v = c.get(k)
    return v if v is not None else float('nan')

# normalize: p_2x_ever/p_collapse stored as fractions -> %
for c in cells:
    c['_p2x'] = g(c, 'p_2x_ever') * 100
    c['_p2x50'] = g(c, 'p_2x_before_50dd') * 100
    c['_pcol'] = g(c, 'p_collapse') * 100
    c['_md'] = g(c, 'median_days_2x')
    c['_p25'] = g(c, 'p25_days_2x')
    c['_wdd'] = g(c, 'worst_dd_pct')
    c['_comp'] = g(c, 'median_compound_pct')
    c['_gross'] = g(c, 'gross_pct') * 100

# Pareto frontier: a cell is dominated if another has >= P(2x) AND <= P(collapse) AND <= median_days (one strict)
def dominated(a, b):
    md_a = a['_md'] if a['_md'] == a['_md'] else 1e9
    md_b = b['_md'] if b['_md'] == b['_md'] else 1e9
    ge = (b['_p2x'] >= a['_p2x'] - 1e-9) and (b['_pcol'] <= a['_pcol'] + 1e-9) and (md_b <= md_a + 1e-9)
    gt = (b['_p2x'] > a['_p2x'] + 1e-9) or (b['_pcol'] < a['_pcol'] - 1e-9) or (md_b < md_a - 1e-9)
    return ge and gt
for a in cells:
    a['_pareto'] = not any(dominated(a, b) for b in cells if b is not a)

cells.sort(key=lambda c: (-c['_p2x'], c['_md'] if c['_md'] == c['_md'] else 1e9))

hdr = f"{'cell':14} {'gross%':>6} {'P2x%':>6} {'medDay':>7} {'p25Day':>7} {'P2x<50dd%':>10} {'pColl%':>7} {'wDD%':>6} {'medComp%':>9} {'':>3}"
print(f"meta: {d['n_windows']} windows x N={d['n_iter']} = {cells[0]['n_paths']} paths/cell | step={d['step_months']}mo | horizon={d['horizon_days']}d")
print(hdr); print('-' * len(hdr))
for c in cells:
    star = 'PF' if c['_pareto'] else ''
    base = '<<' if c['cell'] == 'cascade_ref' else ''
    md = f"{c['_md']:.0f}" if c['_md'] == c['_md'] else '-'
    p25 = f"{c['_p25']:.0f}" if c['_p25'] == c['_p25'] else '-'
    print(f"{c['cell']:14} {c['_gross']:>6.0f} {c['_p2x']:>6.1f} {md:>7} {p25:>7} {c['_p2x50']:>10.1f} {c['_pcol']:>7.1f} {c['_wdd']:>6.1f} {c['_comp']:>9.1f} {(star+base):>3}")

print('\nPareto frontier (PF) on [max P(2x), min P(collapse), min median-days]:')
for c in sorted([c for c in cells if c['_pareto']], key=lambda c: -c['_p2x']):
    md = f"{c['_md']:.0f}d" if c['_md'] == c['_md'] else 'n/a'
    print(f"  {c['cell']:14} gross={c['_gross']:.0f}%  P(2x)={c['_p2x']:.1f}%  med={md}  P(coll)={c['_pcol']:.1f}%  wDD={c['_wdd']:.0f}%")
cr = next(c for c in cells if c['cell'] == 'cascade_ref')
print(f"\ncascade_ref baseline: P(2x)={cr['_p2x']:.1f}%  med={cr['_md']:.0f}d  P(coll)={cr['_pcol']:.1f}%  wDD={cr['_wdd']:.0f}%  comp={cr['_comp']:.1f}%")
