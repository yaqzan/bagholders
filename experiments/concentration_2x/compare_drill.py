"""Coarse (N=100) vs Drill (N=500) side-by-side for the drilled frontier cells.
Confirms whether the time-to-2x frontier ranking + collapse rates hold at higher N."""
import json, os
R = os.path.join(os.path.dirname(__file__), 'results')
coarse = {c['cell']: c for c in json.load(open(os.path.join(R, 'sweep_coarse.json')))['cells']}
drill = {c['cell']: c for c in json.load(open(os.path.join(R, 'sweep_drill.json')))['cells']}

def f(c, k, mul=1.0):
    v = c.get(k)
    return float('nan') if v is None else v * mul

dm = json.load(open(os.path.join(R, 'sweep_drill.json')))
print(f"drill meta: {dm['n_windows']} windows x N={dm['n_iter']} = {list(drill.values())[0]['n_paths']} paths/cell\n")
hdr = f"{'cell':14} | {'P2x% C->D':>14} | {'medDay C->D':>14} | {'pColl% C->D':>14} | {'wDD% C->D':>13} | {'medComp% C->D':>16}"
print(hdr); print('-' * len(hdr))
order = sorted(drill.keys(), key=lambda k: -f(drill[k], 'p_2x_ever'))
for k in order:
    c, dd = coarse.get(k, {}), drill[k]
    def md(x): return f"{x:.0f}" if x == x else '-'
    p2c, p2d = f(c, 'p_2x_ever', 100), f(dd, 'p_2x_ever', 100)
    mdc, mdd = f(c, 'median_days_2x'), f(dd, 'median_days_2x')
    pcc, pcd = f(c, 'p_collapse', 100), f(dd, 'p_collapse', 100)
    wc, wd = f(c, 'worst_dd_pct'), f(dd, 'worst_dd_pct')
    cc, cd = f(c, 'median_compound_pct'), f(dd, 'median_compound_pct')
    tag = '  <<' if k == 'cascade_ref' else ''
    print(f"{k:14} | {p2c:>6.1f}->{p2d:>5.1f} | {md(mdc):>6}->{md(mdd):>5} | {pcc:>6.1f}->{pcd:>5.1f} | {wc:>5.0f}->{wd:>5.0f} | {cc:>7.0f}->{cd:>6.0f}{tag}")
