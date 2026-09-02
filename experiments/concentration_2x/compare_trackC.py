"""Track C: equity-DD insurance overlay (AW equity) vs no-AW baseline, same fast cells.
Does the overlay restore collapse=0 / cut DD without killing time-to-2x speed?"""
import json, os
R = os.path.join(os.path.dirname(__file__), 'results')
noaw = {c['cell']: c for c in json.load(open(os.path.join(R, 'sweep_drill_noaw.json')))['cells']}
aw = {c['cell']: c for c in json.load(open(os.path.join(R, 'sweep_trackC_aw.json')))['cells']}

def f(c, k, mul=1.0):
    v = c.get(k); return float('nan') if v is None else v * mul
def md(x): return f"{x:.0f}" if x == x else '-'

print("Track C — no-AW (baseline)  ->  AW equity insurance overlay\n")
hdr = f"{'cell':14} | {'P2x% base->AW':>14} | {'medDay base->AW':>17} | {'pColl% base->AW':>16} | {'wDD% base->AW':>15} | {'medComp% base->AW':>18}"
print(hdr); print('-' * len(hdr))
for k in sorted(aw.keys(), key=lambda k: -f(aw[k], 'p_2x_ever')):
    b, a = noaw[k], aw[k]
    print(f"{k:14} | {f(b,'p_2x_ever',100):>6.1f}->{f(a,'p_2x_ever',100):>5.1f} | "
          f"{md(f(b,'median_days_2x')):>7}->{md(f(a,'median_days_2x')):>7} | "
          f"{f(b,'p_collapse',100):>6.1f}->{f(a,'p_collapse',100):>6.1f} | "
          f"{f(b,'worst_dd_pct'):>6.0f}->{f(a,'worst_dd_pct'):>6.0f} | "
          f"{f(b,'median_compound_pct'):>8.0f}->{f(a,'median_compound_pct'):>7.0f}")
