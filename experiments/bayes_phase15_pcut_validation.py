"""Phase 15: N=1000 validation of PCUT_BULL_05 vs shipped CUT_ONLY.

Phase 14 (N=500) found mild put cut in BULL regimes (slope_put_up=-0.5)
adds +0.30 utility over current production (slope_put_up=0.0). Margin is
modest so we validate at N=1000 and include boundary variants to ensure
monotonicity.

Variants:
- SHIPPED       (su=0, sd=1, spu=0,     spd=0)    current production
- PCUT_BULL_025 (su=0, sd=1, spu=-0.25, spd=0)    milder cut
- PCUT_BULL_05  (su=0, sd=1, spu=-0.50, spd=0)    Phase 14 winner
- PCUT_BULL_075 (su=0, sd=1, spu=-0.75, spd=0)    deeper cut
- PCUT_BULL_10  (su=0, sd=1, spu=-1.00, spd=0)    full cut
"""

import os, sys, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import SWEEP_WINDOWS, utility_from_results
import monte_carlo as mc
from database.models.core import AlgorithmVersion

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')
os.makedirs(LOG_DIR, exist_ok=True)


def _install_production_cascade():
    mc.TIER_ALLOC = {
        'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00,
    }
    mc.PUT_TIER_ALLOC = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
    mc.MAX_POSITIONS = 14
    def score_to_tier(score):
        if score >= 95: return 'ultra'
        if score >= 85: return 'top'
        if score >= 80: return 'mid'
        if score >= 75: return 'low'
        return 'overflow'
    mc.score_to_tier = score_to_tier


VARIANTS = [
    # label,           su,   sd,   spu,   spd
    ('SHIPPED',        0.00, 1.00,  0.00, 0.00),
    ('PCUT_BULL_025',  0.00, 1.00, -0.25, 0.00),
    ('PCUT_BULL_05',   0.00, 1.00, -0.50, 0.00),
    ('PCUT_BULL_075',  0.00, 1.00, -0.75, 0.00),
    ('PCUT_BULL_10',   0.00, 1.00, -1.00, 0.00),
]

N_ITER = 1000
COLLISION_MODES = ['conservative', 'realistic']


def _run(label, su, sd, spu, spd):
    _install_production_cascade()
    mc.REGIME_SLOPE_UP       = su
    mc.REGIME_SLOPE_DOWN     = sd
    mc.REGIME_SLOPE_PUT_UP   = spu if spu != 0.0 else None
    mc.REGIME_SLOPE_PUT_DOWN = spd if spd != 0.0 else None
    mc.REGIME_SLOPE          = 1.0
    mc.REGIME_SLOPE_PUT      = 0.0
    mc.N_ITER = N_ITER
    mc.COLLISION_MODES = list(COLLISION_MODES)
    v = AlgorithmVersion.get_active_scores_version()

    print(f'\n[{label}] su={su}  sd={sd}  spu={spu}  spd={spd}  N={N_ITER}')
    results = {}
    for wl, d1, d2 in SWEEP_WINDOWS:
        t0 = time.time()
        wr = mc.run_window(wl, d1, d2, v)
        results[wl] = wr
        rr = wr.get('realistic', {})
        cr = wr.get('conservative', {})
        print(f'  [{wl:<6}] {time.time()-t0:>4.0f}s  '
              f'Real={rr.get("mean_ret", 0):>+14.1f}%  DD={rr.get("worst_dd", 0):>5.1f}%  '
              f'Cons_DD={cr.get("worst_dd", 0):>5.1f}%')
    return results


def main():
    all_results = {}
    for label, su, sd, spu, spd in VARIANTS:
        t0 = time.time()
        all_results[label] = _run(label, su, sd, spu, spd)
        print(f'[{label}] total {time.time()-t0:.0f}s')

    print('\n' + '='*120)
    print('REALISTIC MEAN RETURN (N=%d)' % N_ITER)
    print('='*120)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>16}'
    print(hdr)
    print('-'*120)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            mr = all_results[lbl][wl].get('realistic', {}).get('mean_ret', 0.0)
            row += f'  {mr:>+15.1f}%'
        print(row)

    print('\n' + '='*120)
    print('CONSERVATIVE WORST DD')
    print('='*120)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>16}'
    print(hdr)
    print('-'*120)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            dd = all_results[lbl][wl].get('conservative', {}).get('worst_dd', 0.0)
            flag = '!' if dd > 80 else ' '
            row += f'  {dd:>14.1f}%{flag}'
        print(row)

    print('\n' + '='*100)
    print('UTILITY RANKING (N=%d)' % N_ITER)
    print('='*100)
    utils = []
    for lbl, *_ in VARIANTS:
        w_results = {}
        for wl, _, _ in SWEEP_WINDOWS:
            r = all_results[lbl][wl].get('realistic', {})
            w_results[wl] = {
                'mean_ret':  r.get('mean_ret', 0),
                'worst_dd':  r.get('worst_dd', 0),
                'p_collapse': r.get('p_coll', 0),
            }
        u = utility_from_results(w_results)
        utils.append((lbl, u))
        print(f'{lbl:<16}  util={u["utility"]:>8.2f}  logret={u["log_return"]:>7.2f}  '
              f'dd_pen={u["dd_penalty"]:>6.2f}  maxDD={u["max_dd"]:>5.1f}%')

    utils.sort(key=lambda x: -x[1]['utility'])
    print('\nFinal ranking:')
    for lbl, u in utils:
        print(f'  {lbl:<16}  util={u["utility"]:.3f}')

    out = os.path.join(LOG_DIR, 'phase_15_pcut_validation.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'variants': VARIANTS, 'n_iter': N_ITER,
            'results': all_results,
            'utils': [{'label': l, **u} for l, u in utils],
        }, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
