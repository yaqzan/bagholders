"""Phase 6c: Decompose Phase 6b win into MaxPos vs overflow removal vs cascade flattening.

Phase 6b winner (15/15/15/0 @ 12) differs from production (15/12/12/5 @ 14) on
three axes simultaneously. This phase isolates each axis to find the true lever.
"""

import json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import DEFAULT_CONFIG, SWEEP_WINDOWS, apply_config
import monte_carlo as mc
from database.models.core import AlgorithmVersion

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def _run(cfg, label):
    print(f'\n{"="*96}\n{label}  (N={cfg["N_ITER"]}, modes={cfg["COLLISION_MODES"]})\n{"="*96}')
    v = AlgorithmVersion.get_active_scores_version()
    apply_config(cfg)
    results = {}
    for wl, d1, d2 in SWEEP_WINDOWS:
        t0 = time.time()
        results[wl] = mc.run_window(wl, d1, d2, v)
        print(f'  [{wl}] {time.time()-t0:.0f}s')
    return results


VARIANTS = [
    # Label, TIER_ALLOC, MAX_POSITIONS
    ('A_PROD',       {'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.05}, 14),
    ('B_NO_OVR',     {'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.00}, 14),
    ('C_FLAT15',     {'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}, 14),
    ('D_MP12',       {'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.05}, 12),
    ('E_NO_OVR_MP12',{'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.00}, 12),
    ('F_CAND',       {'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}, 12),
]


def main():
    results = {}
    for label, alloc, mp in VARIANTS:
        cfg = dict(DEFAULT_CONFIG)
        cfg['TIER_ALLOC'] = alloc
        cfg['MAX_POSITIONS'] = mp
        cfg['N_ITER'] = 300
        cfg['COLLISION_MODES'] = ['conservative', 'realistic']
        results[label] = _run(cfg, f'{label}  alloc={alloc}  MP={mp}')

    print('\n' + '='*160)
    hdr = f'{"Window":<10}'
    for lbl, _, _ in VARIANTS:
        hdr += f'  {lbl:>16}'
    print(hdr)
    print('-'*160)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, _, _ in VARIANTS:
            mr = results[lbl][wl]['realistic']['mean_ret']
            row += f'  {mr:>+15.1f}%'
        print(row)

    print('\nConservative DD (80% floor):')
    print('-'*160)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, _, _ in VARIANTS:
            dd = results[lbl][wl]['conservative']['worst_dd']
            flag = '!' if dd > 80 else ' '
            row += f'  {dd:>14.1f}%{flag}'
        print(row)

    out = os.path.join(LOG_DIR, 'phase_6c_decompose.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'variants': [(l, a, m) for l, a, m in VARIANTS], 'results': results},
                  f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
