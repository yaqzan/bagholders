"""Track A portfolio A/B: baseline vs market-scaled vs equity-scaled.
Reads the three coarse MC_RESULTS_JSON arms (each window has a per-iter 'paths'
block: finals/dds/t2x_bars/t_50dd_bars/starting_cash) and prints, per window per
arm: median return %, worst DD %, P(collapse), and the time-to-2x objective
(median calendar days-to-2x among reachers, P(reach 2x), P(2x before a 50% DD)).
Pure JSON read - no MySQL, no MC."""
import json, os, statistics as st

BASE = os.path.join(os.path.dirname(__file__), 'out')
ARMS = {'baseline': 'coarse_baseline.json', 'market': 'coarse_market.json', 'equity': 'coarse_equity.json'}
TD = 365.0 / 252.0  # trading-bar index -> calendar days

def load(f):
    with open(os.path.join(BASE, f)) as fh:
        return json.load(fh)

data = {k: load(v) for k, v in ARMS.items()}
wins = [k for k, v in data['baseline'].items() if isinstance(v, dict) and 'med_ret' in v]

def arm_metrics(arm, w):
    wd = data[arm].get(w, {})
    p = wd.get('paths', {})
    finals = p.get('finals', []) or []
    t2x = p.get('t2x_bars', []) or []
    t50 = p.get('t_50dd_bars', []) or []
    start = p.get('starting_cash', 50000.0)
    N = len(finals)
    reach = [t for t in t2x if t is not None]
    med2x = (st.median(reach) * TD) if reach else None
    p2x = (len(reach) / N * 100) if N else float('nan')
    def before(i):
        a, b = t2x[i], t50[i]
        return a is not None and (b is None or a < b)
    p2x50 = (sum(1 for i in range(N) if before(i)) / N * 100) if N else float('nan')
    pcol = (sum(1 for x in finals if x <= 0.2 * start) / N * 100) if N else float('nan')
    return dict(med_ret=wd.get('med_ret', float('nan')), worst_dd=wd.get('worst_dd', float('nan')),
                p_coll=wd.get('p_coll', float('nan')), med2x=med2x, p2x=p2x, p2x50=p2x50, pcol=pcol, N=N)

order = [w for w in ['2024', '2022', '2023', '2021', '2025', 'dip', '22-now', '5y'] if w in wins]
hdr = f"{'window':8} {'arm':9} {'medRet%':>10} {'wDD%':>6} {'pColl%':>7} {'med2x_d':>8} {'P2x%':>6} {'P2x<50dd%':>10}"
print(hdr); print('-' * len(hdr))
for w in order:
    for arm in ['baseline', 'market', 'equity']:
        x = arm_metrics(arm, w)
        md = f"{x['med2x']:.0f}" if x['med2x'] is not None else '-'
        print(f"{w:8} {arm:9} {x['med_ret']:>10.1f} {x['worst_dd']:>6.1f} {x['p_coll']:>7.1f} {md:>8} {x['p2x']:>6.1f} {x['p2x50']:>10.1f}")
    print()
print(f"(N per cell: {arm_metrics('baseline', order[0])['N']} iters; med2x_d = calendar days to 2x among reachers; '-' = window never reaches 2x)")
