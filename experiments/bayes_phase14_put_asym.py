"""Phase 14: Asymmetric put-side regime slope exploration.

CUT_ONLY (shipped 2026-04-17) proved that stress-side contraction alone is
the call-side optimum. The put side was tested in Phase 11 under symmetric
slopes only and ruled out (slope_p=0). This phase re-opens puts under the
asymmetric framework — puts may benefit from:

  - Cut in BULL regimes (puts structurally underperform in rallies)
  - Boost in STRESS regimes (puts win when markets fall)
  - Combined: both-sided adjustment

Also retests deeper call stress cuts now that bull boost is off.

Variants (slope_up, slope_down, slope_put_up, slope_put_down):
- SHIPPED      (0.00, 1.00,  0.00,  0.00)  — current production baseline
- PCUT_BULL_05 (0.00, 1.00, -0.50,  0.00)  — mild put cut in BULL
- PCUT_BULL_10 (0.00, 1.00, -1.00,  0.00)  — full put cut in BULL
- PBOOST_STR_05(0.00, 1.00,  0.00, -0.50)  — mild put boost in STRESS
- PBOOST_STR_10(0.00, 1.00,  0.00, -1.00)  — full put boost in STRESS
- PCOMBO_05    (0.00, 1.00, -0.50, -0.50)  — cut in BULL + boost in STRESS
- PCOMBO_10    (0.00, 1.00, -1.00, -1.00)  — aggressive both
- CCUT_150     (0.00, 1.50,  0.00,  0.00)  — revisit deeper call cut

Note: slope semantics are `scale = 1 + slope*(regime_mult-1.0)`. For puts,
slope_put_up<0 reduces allocation when regime_mult>1 (BULL); slope_put_down<0
boosts allocation when regime_mult<1 (STRESS).
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
    # label,          su,   sd,   spu,   spd
    ('SHIPPED',       0.00, 1.00,  0.00,  0.00),
    ('PCUT_BULL_05',  0.00, 1.00, -0.50,  0.00),
    ('PCUT_BULL_10',  0.00, 1.00, -1.00,  0.00),
    ('PBOOST_STR_05', 0.00, 1.00,  0.00, -0.50),
    ('PBOOST_STR_10', 0.00, 1.00,  0.00, -1.00),
    ('PCOMBO_05',     0.00, 1.00, -0.50, -0.50),
    ('PCOMBO_10',     0.00, 1.00, -1.00, -1.00),
    ('CCUT_150',      0.00, 1.50,  0.00,  0.00),
]

N_ITER = 500
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

    print('\n' + '='*140)
    print('REALISTIC MEAN RETURN (N=%d)' % N_ITER)
    print('='*140)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>14}'
    print(hdr)
    print('-'*140)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            mr = all_results[lbl][wl].get('realistic', {}).get('mean_ret', 0.0)
            row += f'  {mr:>+13.1f}%'
        print(row)

    print('\n' + '='*140)
    print('CONSERVATIVE WORST DD')
    print('='*140)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>14}'
    print(hdr)
    print('-'*140)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            dd = all_results[lbl][wl].get('conservative', {}).get('worst_dd', 0.0)
            flag = '!' if dd > 80 else ' '
            row += f'  {dd:>12.1f}%{flag}'
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
        print(f'{lbl:<14}  util={u["utility"]:>8.2f}  logret={u["log_return"]:>7.2f}  '
              f'dd_pen={u["dd_penalty"]:>6.2f}  maxDD={u["max_dd"]:>5.1f}%')

    utils.sort(key=lambda x: -x[1]['utility'])
    print('\nFinal ranking:')
    for lbl, u in utils:
        print(f'  {lbl:<14}  util={u["utility"]:.3f}')

    out = os.path.join(LOG_DIR, 'phase_14_put_asym.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'variants': VARIANTS, 'n_iter': N_ITER,
            'results': all_results,
            'utils': [{'label': l, **u} for l, u in utils],
        }, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
