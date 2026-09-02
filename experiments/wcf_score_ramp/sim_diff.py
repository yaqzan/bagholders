"""
WCF score-gate ramp - full-faithful ScoreSimulator A/B (READ-ONLY).

Runs the complete production scoring path (all push bands, dampeners, boosts,
regime - the v42 lesson: no approximations) over the full universe for 1y,
twice on ONE loaded context:

  arm A: WCF_LIFT_RAMP_TOP = 28  (bit-exact legacy binary gate)
  arm B: WCF_LIFT_RAMP_TOP = 31  (candidate ramp)

Then diffs the two score dicts. Structural claim under test: every diff has
base in [28,30] and new in (base, 49], i.e. the ramp touches ONLY the
predicted neutral-zone cohort and no cascade-tradable bucket changes.

Also runs sim(arm A) vs stored DB scores as a parity cross-check.
"""
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# CRITICAL: the global PYTHONPATH on this box includes the MAIN checkout
# (C:\Development\Trader), so a bare `python experiments/.../x.py` in a
# worktree silently imports MAIN's modules (which lack the candidate code).
# Pin this worktree's root ahead of everything.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sim_diff_results.json')
LOOKBACK = int(os.environ.get('WCF_SIM_LOOKBACK', '365'))

import database.utils.scoring as sc  # noqa: E402
import simulator as _simmod  # noqa: E402
from simulator import ScoreSimulator  # noqa: E402

print('scoring module:', sc.__file__, flush=True)
print('simulator module:', _simmod.__file__, flush=True)
assert os.path.abspath(sc.__file__).lower().startswith(ROOT.lower()), \
    f'scoring imported from outside the worktree: {sc.__file__}'
assert os.path.abspath(_simmod.__file__).lower().startswith(ROOT.lower()), \
    f'simulator imported from outside the worktree: {_simmod.__file__}'
assert hasattr(sc, 'WCF_LIFT_RAMP_TOP'), 'candidate constant missing - wrong code'

print(f'Loading full universe, lookback={LOOKBACK}d ...', flush=True)
sim = ScoreSimulator(symbols=None, lookback_days=LOOKBACK)

print('Arm A: RAMP_TOP=28 (legacy binary) ...', flush=True)
sc.WCF_LIFT_RAMP_TOP = 28
base = sim.simulate()
print(f'  {len(base)} (sym,date) scores', flush=True)

res = {'lookback_days': LOOKBACK, 'scored_pairs': len(base), 'arms': {}}
all_viol = 0
for top in (31, 33):
    print(f'Arm RAMP_TOP={top} ...', flush=True)
    sc.WCF_LIFT_RAMP_TOP = top
    ramp = sim.simulate()
    diffs = [(k, v, ramp.get(k)) for k, v in base.items() if ramp.get(k) != v]
    # structural claim: base in [28, top-1], new in (base, 49]
    viol = [d for d in diffs
            if d[1] is None or d[2] is None
            or not (28 <= d[1] <= top - 1) or not (d[1] < d[2] <= 49)]
    all_viol += len(viol)
    pair_counts = Counter((d[1], d[2]) for d in diffs)
    res['arms'][f'top{top}'] = {
        'diff_rows': len(diffs),
        'diff_pct': round(100.0 * len(diffs) / max(1, len(base)), 4),
        'structural_violations': len(viol),
        'violation_examples': [
            (d[0][0], str(d[0][1]), d[1], d[2]) for d in viol[:20]],
        'diff_pairs_top': {f'{a}->{b}': n
                           for (a, b), n in pair_counts.most_common(30)},
    }
print(json.dumps(res, indent=2))
viol = all_viol

print('\nParity cross-check: arm A vs stored DB scores', flush=True)
sc.WCF_LIFT_RAMP_TOP = 28
try:
    sim.compare(base)
except Exception as e:  # noqa: BLE001
    print('compare() failed:', repr(e))

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(res, f, indent=2, default=str)
print('wrote', OUT)
print('VERDICT:', 'CLEAN - ramp touches only the predicted neutral-zone cohort'
      if not viol else f'STRUCTURAL SURPRISE - {viol} violations, investigate')
