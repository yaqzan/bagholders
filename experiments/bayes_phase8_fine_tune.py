"""Phase 8: Fine-tune around Phase 7 winner — does small overflow restore 2023/2025?

Phase 7 B (25/15/15/15/0 @ MP14) wins 22-now by +3.87x but loses 2023 (-38%)
and 2025 (-61%) vs production. Hypothesis: a small 70-74 allocation (2-4%)
may restore part of these years without sacrificing the 22-now uplift.

Also test ultra tier magnitude: 22 / 25 / 28 — is 25% already peak?
"""

import os, sys, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import DEFAULT_CONFIG, SWEEP_WINDOWS, apply_config
import monte_carlo as mc
from database.models.core import AlgorithmVersion

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def _install_split_score_to_tier():
    def score_to_tier(score):
        if score >= 95: return 'ultra'
        if score >= 85: return 'top'
        if score >= 80: return 'mid'
        if score >= 75: return 'low'
        return 'overflow'
    mc.score_to_tier = score_to_tier


VARIANTS = [
    # (label, ultra, top, mid, low, overflow)
    ('P7_BEST_B',    0.25, 0.15, 0.15, 0.15, 0.00),  # Phase 7 winner — control
    ('OVR_02',       0.25, 0.15, 0.15, 0.15, 0.02),
    ('OVR_03',       0.25, 0.15, 0.15, 0.15, 0.03),
    ('OVR_04',       0.25, 0.15, 0.15, 0.15, 0.04),
    ('ULTRA_22',     0.22, 0.15, 0.15, 0.15, 0.00),
    ('ULTRA_28',     0.28, 0.15, 0.15, 0.15, 0.00),
    # Hybrid: Phase 6c B (more conservative, keep 12/12 mid/low)
    ('B_NO_OVR',     None, 0.15, 0.12, 0.12, 0.00),
    ('B_SPLIT_25',   0.25, 0.15, 0.12, 0.12, 0.00),
]


def _run(cfg, label):
    print(f'\n[{label}] alloc={cfg["TIER_ALLOC"]}')
    v = AlgorithmVersion.get_active_scores_version()
    apply_config(cfg)
    results = {}
    for wl, d1, d2 in SWEEP_WINDOWS:
        t0 = time.time()
        results[wl] = mc.run_window(wl, d1, d2, v)
        print(f'  [{wl}] {time.time()-t0:.0f}s')
    return results


def main():
    results = {}
    for label, ultra, top, mid, low, ovr in VARIANTS:
        if ultra is not None:
            _install_split_score_to_tier()
            alloc = {'ultra': ultra, 'top': top, 'mid': mid, 'low': low, 'overflow': ovr}
        else:
            def simple(score):
                if score >= 85: return 'top'
                if score >= 80: return 'mid'
                if score >= 75: return 'low'
                return 'overflow'
            mc.score_to_tier = simple
            alloc = {'top': top, 'mid': mid, 'low': low, 'overflow': ovr}
        cfg = dict(DEFAULT_CONFIG)
        cfg['TIER_ALLOC']       = alloc
        cfg['MAX_POSITIONS']    = 14
        cfg['N_ITER']           = 300
        cfg['COLLISION_MODES']  = ['conservative', 'realistic']
        results[label] = _run(cfg, label)

    print('\n' + '='*180)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>16}'
    print(hdr)
    print('-'*180)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            mr = results[lbl][wl]['realistic']['mean_ret']
            row += f'  {mr:>+15.1f}%'
        print(row)

    print('\nConservative DD:')
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            dd = results[lbl][wl]['conservative']['worst_dd']
            flag = '!' if dd > 80 else ' '
            row += f'  {dd:>14.1f}%{flag}'
        print(row)

    out = os.path.join(LOG_DIR, 'phase_8_fine_tune.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'variants': VARIANTS, 'results': results}, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
