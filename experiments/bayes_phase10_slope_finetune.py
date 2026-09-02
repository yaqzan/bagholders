"""Phase 10: Fine-tune call-side regime slope around 1.0.

Phase 9 winner was CALL_PRO_ONLY (slope_c=+1.0, slope_p=0.0) — +23% 22-now
Realistic over baseline. Margin vs S0_BASELINE only +0.75 logret. Need to
verify: (a) local peak near 1.0, (b) where 2021/2024 regression crosses zero,
(c) if slight asymmetric put-side counter-cyclical helps.

Variants (slope_c, slope_p):
  S0         : (0.00, 0.00)  baseline reference
  SC25       : (0.25, 0.00)
  SC50       : (0.50, 0.00)
  SC75       : (0.75, 0.00)  <- previous PRO_HALF (without put scaling)
  SC100      : (1.00, 0.00)  <- Phase 9 winner
  SC125      : (1.25, 0.00)
  SC100_PN25 : (1.00,-0.25)  call pro + mild put counter
  SC100_PN50 : (1.00,-0.50)  call pro + put counter
  SC100_PP25 : (1.00,+0.25)  call pro + mild put pro
"""

import os, sys, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import SWEEP_WINDOWS, utility_from_results
import monte_carlo as mc
from database.models.core import AlgorithmVersion

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def _install_production_cascade():
    mc.TIER_ALLOC = {
        'ultra':    0.25,
        'top':      0.15,
        'mid':      0.15,
        'low':      0.15,
        'overflow': 0.00,
    }
    mc.PUT_TIER_ALLOC = {
        'put_top': 0.15,
        'put_mid': 0.12,
        'put_low': 0.12,
    }
    mc.MAX_POSITIONS = 14
    def score_to_tier(score):
        if score >= 95: return 'ultra'
        if score >= 85: return 'top'
        if score >= 80: return 'mid'
        if score >= 75: return 'low'
        return 'overflow'
    mc.score_to_tier = score_to_tier


VARIANTS = [
    ('S0',          0.00,  0.00),
    ('SC25',        0.25,  0.00),
    ('SC50',        0.50,  0.00),
    ('SC75',        0.75,  0.00),
    ('SC100',       1.00,  0.00),
    ('SC125',       1.25,  0.00),
    ('SC100_PN25',  1.00, -0.25),
    ('SC100_PN50',  1.00, -0.50),
    ('SC100_PP25',  1.00, +0.25),
]

N_ITER = 400  # higher than Phase 9's 250 to reduce noise
COLLISION_MODES = ['conservative', 'realistic']


def _run(label, slope_c, slope_p):
    _install_production_cascade()
    mc.REGIME_SLOPE     = slope_c
    mc.REGIME_SLOPE_PUT = slope_p
    mc.N_ITER = N_ITER
    mc.COLLISION_MODES = list(COLLISION_MODES)
    v = AlgorithmVersion.get_active_scores_version()

    print(f'\n[{label}] slope_c={slope_c}  slope_p={slope_p}')
    results = {}
    for wl, d1, d2 in SWEEP_WINDOWS:
        t0 = time.time()
        wr = mc.run_window(wl, d1, d2, v)
        results[wl] = wr
        rr = wr.get('realistic', {})
        cr = wr.get('conservative', {})
        print(f'  [{wl:<6}] {time.time()-t0:>4.0f}s  '
              f'Real={rr.get("mean_ret", 0):>+12.1f}%  DD={rr.get("worst_dd", 0):>5.1f}%  '
              f'Cons_DD={cr.get("worst_dd", 0):>5.1f}%')
    return results


def main():
    all_results = {}
    for label, sc, sp in VARIANTS:
        t0 = time.time()
        all_results[label] = _run(label, sc, sp)
        print(f'[{label}] total {time.time()-t0:.0f}s')

    print('\n' + '='*180)
    print('REALISTIC MEAN RETURN')
    print('='*180)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>14}'
    print(hdr)
    print('-'*180)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            mr = all_results[lbl][wl].get('realistic', {}).get('mean_ret', 0.0)
            row += f'  {mr:>+13.1f}%'
        print(row)

    print('\n' + '='*180)
    print('CONSERVATIVE WORST DD (80% floor)')
    print('='*180)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>14}'
    print(hdr)
    print('-'*180)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            dd = all_results[lbl][wl].get('conservative', {}).get('worst_dd', 0.0)
            flag = '!' if dd > 80 else ' '
            row += f'  {dd:>12.1f}%{flag}'
        print(row)

    print('\n' + '='*100)
    print('UTILITY RANKING (higher = better)')
    print('='*100)
    utils = []
    for lbl, *_ in VARIANTS:
        w_results = {}
        for wl, _, _ in SWEEP_WINDOWS:
            r = all_results[lbl][wl].get('realistic', {})
            w_results[wl] = {
                'mean_ret': r.get('mean_ret', 0),
                'worst_dd': r.get('worst_dd', 0),
                'p_collapse': r.get('p_coll', 0),
            }
        u = utility_from_results(w_results)
        utils.append((lbl, u))
        print(f'{lbl:<14}  util={u["utility"]:>8.2f}  logret={u["log_return"]:>7.2f}  '
              f'dd_pen={u["dd_penalty"]:>6.2f}  maxDD={u["max_dd"]:>5.1f}%')

    utils.sort(key=lambda x: -x[1]['utility'])
    print('\nTop 3:')
    for lbl, u in utils[:3]:
        print(f'  {lbl:<14}  util={u["utility"]:.2f}')

    out = os.path.join(LOG_DIR, 'phase_10_slope_finetune.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'variants': VARIANTS,
            'n_iter': N_ITER,
            'results': all_results,
            'utils': [{'label': l, **u} for l, u in utils],
        }, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
