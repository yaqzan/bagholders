"""Phase 6b: Full-fidelity validation of Phase 6 winner vs production.

Phase 6 converged at N=200/Realistic-only. Re-run at N=500/3-modes/6-windows
to confirm:
  (a) Conservative DD floor (80%) holds
  (b) 2023 / 2025 regressions are real, not noise
  (c) 22-now compounded uplift survives
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
        r = mc.run_window(wl, d1, d2, v)
        results[wl] = r
        print(f'  [{wl}] done in {time.time()-t0:.0f}s')
    return results


def main():
    # Candidate config from Phase 6 winner
    cand = dict(DEFAULT_CONFIG)
    cand['TIER_ALLOC']    = {'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.0}
    cand['MAX_POSITIONS'] = 12
    cand['N_ITER']        = 500
    cand['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

    # Production baseline
    prod = dict(DEFAULT_CONFIG)
    prod['N_ITER'] = 500
    prod['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

    cand_res = _run(cand, 'PHASE 6 CANDIDATE  (15/15/15/0 @ MaxPos=12)')
    prod_res = _run(prod, 'PRODUCTION         (15/12/12/5 @ MaxPos=14)')

    print('\n' + '='*140)
    print(f'{"Window":<10}  {"PROD-R":>14}  {"CAND-R":>14}  {"delta%":>8}  '
          f'{"PROD-C":>14}  {"CAND-C":>14}  {"CAND DD-R":>9}  {"CAND DD-C":>9}  {"PROD DD-C":>9}')
    print('='*140)
    for wl, _, _ in SWEEP_WINDOWS:
        pr   = prod_res[wl]['realistic']['mean_ret']
        cr   = cand_res[wl]['realistic']['mean_ret']
        pc   = prod_res[wl]['conservative']['mean_ret']
        cc   = cand_res[wl]['conservative']['mean_ret']
        dd_r = cand_res[wl]['realistic']['worst_dd']
        dd_c = cand_res[wl]['conservative']['worst_dd']
        pdd_c= prod_res[wl]['conservative']['worst_dd']
        delta = (cr - pr) / abs(pr) * 100 if pr else 0
        print(f'{wl:<10}  {pr:>+13.1f}%  {cr:>+13.1f}%  {delta:>+7.1f}%  '
              f'{pc:>+13.1f}%  {cc:>+13.1f}%  {dd_r:>8.1f}%  {dd_c:>8.1f}%  {pdd_c:>8.1f}%')

    out = os.path.join(LOG_DIR, 'phase_6b_validation.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'candidate_config': {k: v for k, v in cand.items() if k not in ('COLLISION_MODES',)},
            'candidate_results': cand_res,
            'production_results': prod_res,
        }, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
