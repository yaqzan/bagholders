"""Phase 9: Regime-aware allocation sweep.

Hypothesis: scale TIER_ALLOC by market regime_multiplier — in STRESS (mult<1.0)
reduce alloc (pro-cyclical) or increase it (counter-cyclical). Separate slopes
for calls vs puts since puts are structurally contra-regime (put signals cluster
in weak-breadth periods by design).

Baseline (SLOPE=0) is the just-shipped ultra-split cascade (b6f84a5).

Variants:
  S0_BASELINE   : SLOPE_C=0, SLOPE_P=0                 (control)
  PRO_HALF      : SLOPE_C=+0.5, SLOPE_P=+0.5           (mild pro-cyclical)
  PRO_FULL      : SLOPE_C=+1.0, SLOPE_P=+1.0           (1:1 w/ regime_mult)
  PRO_STRONG    : SLOPE_C=+1.5, SLOPE_P=+1.5           (amplify 1.5x)
  COUNTER_HALF  : SLOPE_C=-0.5, SLOPE_P=-0.5           (mild counter-cyclical)
  COUNTER_FULL  : SLOPE_C=-1.0, SLOPE_P=-1.0           (boost in stress)
  ASYM_CALL_PRO : SLOPE_C=+1.0, SLOPE_P=-1.0           (cut calls in stress, boost puts)
  ASYM_PUT_ONLY : SLOPE_C=0,    SLOPE_P=-1.0           (only puts get counter-cyclical)
  CALL_PRO_ONLY : SLOPE_C=+1.0, SLOPE_P=0              (only calls get pro-cyclical)
"""

import os, sys, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import SWEEP_WINDOWS, utility_from_results
import monte_carlo as mc
from database.models.core import AlgorithmVersion

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def _install_production_cascade():
    """Re-apply shipped ultra-split cascade (b6f84a5)."""
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
    # (label, SLOPE_CALL, SLOPE_PUT)
    ('S0_BASELINE',    0.0,  0.0),
    ('PRO_HALF',      +0.5, +0.5),
    ('PRO_FULL',      +1.0, +1.0),
    ('PRO_STRONG',    +1.5, +1.5),
    ('COUNTER_HALF',  -0.5, -0.5),
    ('COUNTER_FULL',  -1.0, -1.0),
    ('ASYM_CALL_PRO', +1.0, -1.0),
    ('ASYM_PUT_ONLY',  0.0, -1.0),
    ('CALL_PRO_ONLY', +1.0,  0.0),
]

N_ITER = 250
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
              f'Real={rr.get("mean_ret", 0):>+10.1f}%  DD={rr.get("worst_dd", 0):>5.1f}%  '
              f'Cons_DD={cr.get("worst_dd", 0):>5.1f}%')
    return results


def main():
    all_results = {}
    for label, sc, sp in VARIANTS:
        t0 = time.time()
        all_results[label] = _run(label, sc, sp)
        print(f'[{label}] total {time.time()-t0:.0f}s')

    print('\n' + '='*170)
    print('REALISTIC MEAN RETURN')
    print('='*170)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>16}'
    print(hdr)
    print('-'*170)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            mr = all_results[lbl][wl].get('realistic', {}).get('mean_ret', 0.0)
            row += f'  {mr:>+15.1f}%'
        print(row)

    print('\n' + '='*170)
    print('CONSERVATIVE WORST DD (80% floor)')
    print('='*170)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>16}'
    print(hdr)
    print('-'*170)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            dd = all_results[lbl][wl].get('conservative', {}).get('worst_dd', 0.0)
            flag = '!' if dd > 80 else ' '
            row += f'  {dd:>14.1f}%{flag}'
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
        print(f'{lbl:<16}  util={u["utility"]:>8.2f}  logret={u["log_return"]:>7.2f}  '
              f'dd_pen={u["dd_penalty"]:>6.2f}  maxDD={u["max_dd"]:>5.1f}%')

    utils.sort(key=lambda x: -x[1]['utility'])
    print('\nTop 3:')
    for lbl, u in utils[:3]:
        print(f'  {lbl:<16}  util={u["utility"]:.2f}')

    out = os.path.join(LOG_DIR, 'phase_9_regime_alloc.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'variants': VARIANTS,
            'results': all_results,
            'utils': [{'label': l, **u} for l, u in utils],
        }, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
