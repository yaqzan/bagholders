"""Phase 7: 95+ tier split sweep.

Current v18 cascade lumps 95+ (WR15=90.0%) with 85-94 (WR15~76%) into one
`top=15%` tier. 95+ fires only ~4x/year but has exceptional per-trade EV.
Splitting the top tier could let 95+ deploy larger capital on those rare days.

Sweep:
  top_95plus:   {0.18, 0.20, 0.22, 0.25, 0.30}
  top_85_94:    {0.10, 0.12, 0.15, 0.18}
  mid (80-84):  {0.12, 0.15}
  low (75-79):  {0.12, 0.15}

Base config: overflow=0 (phase 6c finding), MaxPos=14, puts at production.
"""

import os, sys, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import DEFAULT_CONFIG, SWEEP_WINDOWS, apply_config
import monte_carlo as mc
from database.models.core import AlgorithmVersion

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def _run(cfg, label):
    print(f'\n[{label}] cfg_top={cfg["TIER_ALLOC"]}')
    v = AlgorithmVersion.get_active_scores_version()
    apply_config(cfg)
    # The candidate uses an augmented score_to_tier that maps 95+ to 'ultra'.
    # We patch mc.score_to_tier to use 5-tier logic when 'ultra' is set.
    results = {}
    for wl, d1, d2 in SWEEP_WINDOWS:
        t0 = time.time()
        results[wl] = mc.run_window(wl, d1, d2, v)
        print(f'  [{wl}] {time.time()-t0:.0f}s')
    return results


VARIANTS = [
    # Label, (ultra_alloc, top_alloc, mid_alloc, low_alloc)
    # Ultra = 95+, Top = 85-94, Mid = 80-84, Low = 75-79. Overflow=0.
    ('BASE_NOSPLIT', None, 0.15, 0.15, 0.15),   # C_FLAT15 — control
    ('A_20/15/15/15', 0.20, 0.15, 0.15, 0.15),
    ('B_25/15/15/15', 0.25, 0.15, 0.15, 0.15),
    ('C_30/12/12/12', 0.30, 0.12, 0.12, 0.12),
    ('D_22/18/15/12', 0.22, 0.18, 0.15, 0.12),
    ('E_25/12/15/15', 0.25, 0.12, 0.15, 0.15),
]


def _install_split_score_to_tier(split_on):
    """If split_on=True, treat 95+ as tier 'ultra'."""
    if split_on:
        def score_to_tier(score):
            if score >= 95: return 'ultra'
            if score >= 85: return 'top'
            if score >= 80: return 'mid'
            if score >= 75: return 'low'
            return 'overflow'
    else:
        def score_to_tier(score):
            if score >= 85: return 'top'
            if score >= 80: return 'mid'
            if score >= 75: return 'low'
            return 'overflow'
    mc.score_to_tier = score_to_tier


def main():
    results = {}
    for label, ultra, top, mid, low in VARIANTS:
        cfg = dict(DEFAULT_CONFIG)
        if ultra is not None:
            cfg['TIER_ALLOC'] = {'ultra': ultra, 'top': top, 'mid': mid, 'low': low, 'overflow': 0.0}
            _install_split_score_to_tier(True)
        else:
            cfg['TIER_ALLOC'] = {'top': top, 'mid': mid, 'low': low, 'overflow': 0.0}
            _install_split_score_to_tier(False)
        cfg['MAX_POSITIONS'] = 14
        cfg['N_ITER'] = 300
        cfg['COLLISION_MODES'] = ['conservative', 'realistic']
        results[label] = _run(cfg, label)

    print('\n' + '='*150)
    hdr = f'{"Window":<10}'
    for lbl, *_ in VARIANTS: hdr += f'  {lbl:>18}'
    print(hdr)
    print('-'*150)
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            mr = results[lbl][wl]['realistic']['mean_ret']
            row += f'  {mr:>+17.1f}%'
        print(row)

    print('\nConservative DD:')
    for wl, _, _ in SWEEP_WINDOWS:
        row = f'{wl:<10}'
        for lbl, *_ in VARIANTS:
            dd = results[lbl][wl]['conservative']['worst_dd']
            row += f'  {dd:>17.1f}%'
        print(row)

    out = os.path.join(LOG_DIR, 'phase_7_95plus_split.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'variants': VARIANTS, 'results': results}, f, default=str, indent=2)
    print(f'\nSaved: {out}')


if __name__ == '__main__':
    main()
