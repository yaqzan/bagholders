"""Phase 12: Asymmetric regime slope (BULL vs STRESS separately).

Production ships SC100 (symmetric slope_c=+1.0). This phase asks whether
splitting the bull-side slope (slope_up, applied when regime_mult > 1.0)
from the stress-side slope (slope_down, applied when regime_mult < 1.0)
extracts further alpha.

Hypothesis map:
  offensive-heavy: boost big in BULL, moderate cut in STRESS -> favours bull years
  protective-heavy: moderate boost in BULL, big cut in STRESS -> favours 22-now DD
  cut-only: slope_up=0, slope_down=1.0 -> no bull boost at all
  boost-only: slope_up=1.0, slope_down=0.0 -> no stress cut at all
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


# (label, slope_up, slope_down) -- slope_put_up/down always 0 (puts unscaled)
VARIANTS = [
    ('SYM100',     1.00, 1.00),   # production SC100 baseline
    ('OFF_UP2',    2.00, 1.00),   # stronger bull boost, same stress cut
    ('OFF_UP15',   1.50, 1.00),   # moderate extra bull, same stress cut
    ('PROT_DN2',   1.00, 2.00),   # same bull, deeper stress cut
    ('PROT_DN15',  1.00, 1.50),   # same bull, mild extra stress cut
    ('CUT_ONLY',   0.00, 1.00),   # no bull boost, full stress cut
    ('BOOST_ONLY', 1.00, 0.00),   # full bull, no stress cut
    ('ASYM_UP2_DN05', 2.00, 0.50),  # aggressive bull, shallow stress
    ('ASYM_UP05_DN2', 0.50, 2.00),  # shallow bull, aggressive stress
]

N_ITER = 400
COLLISION_MODES = ['conservative', 'realistic']


def _run(label, su, sd):
    _install_production_cascade()
    mc.REGIME_SLOPE_UP   = su
    mc.REGIME_SLOPE_DOWN = sd
    mc.REGIME_SLOPE      = 1.0  # unused but kept consistent
    mc.REGIME_SLOPE_PUT  = 0.0
    mc.REGIME_SLOPE_PUT_UP   = None
    mc.REGIME_SLOPE_PUT_DOWN = None
    mc.N_ITER = N_ITER
    mc.COLLISION_MODES = list(COLLISION_MODES)
    v = AlgorithmVersion.get_active_scores_version()

    print(f'\n[{label}] slope_up={su}  slope_down={sd}  N={N_ITER}')
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
    for label, su, sd in VARIANTS:
        t0 = time.time()
        all_results[label] = _run(label, su, sd)
        print(f'[{label}] total {time.time()-t0:.0f}s')

    print('\n' + '='*140)
    print('REALISTIC MEAN RETURN (N=400 iter)')
    print('='*140)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>15}'
    print(hdr)
    print('-'*140)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            mr = all_results[lbl][wl].get('realistic', {}).get('mean_ret', 0.0)
            row += f'  {mr:>+14.1f}%'
        print(row)

    print('\n' + '='*140)
    print('CONSERVATIVE WORST DD')
    print('='*140)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>15}'
    print(hdr)
    print('-'*140)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            dd = all_results[lbl][wl].get('conservative', {}).get('worst_dd', 0.0)
            flag = '!' if dd > 80 else ' '
            row += f'  {dd:>13.1f}%{flag}'
        print(row)

    print('\n' + '='*100)
    print('UTILITY RANKING (N=400)')
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
        print(f'{lbl:<18}  util={u["utility"]:>8.2f}  logret={u["log_return"]:>7.2f}  '
              f'dd_pen={u["dd_penalty"]:>6.2f}  maxDD={u["max_dd"]:>5.1f}%')

    utils.sort(key=lambda x: -x[1]['utility'])
    print('\nFinal ranking:')
    for lbl, u in utils:
        print(f'  {lbl:<18}  util={u["utility"]:.3f}')

    out = os.path.join(LOG_DIR, 'phase_12_asym_slope.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'variants': VARIANTS, 'n_iter': N_ITER,
            'results': all_results,
            'utils': [{'label': l, **u} for l, u in utils],
        }, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
