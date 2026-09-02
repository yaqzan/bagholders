"""Phase 1: Put TP / SL sweep (fixed, no breadth adaptation).

Parameter space:
  PUT_TP ∈ {0.20, 0.25, 0.30, 0.35, 0.40}
  PUT_SL ∈ {-0.35, -0.30, -0.25, -0.20, -0.15, -0.10}

That is 5 × 6 = 30 candidates. Bayesian optimizer will evaluate ~12-15 before
converging (baseline seed + 4 diversity seeds + up to ~10 adaptive picks).

Everything else locked at current production:
  - Calls: breadth-adaptive TP 30/35, SL 35/40, breadth threshold 50
  - Put breadth adaptation: OFF (tested in Phase 2)
  - Put cascade: 15/12/12 (swept in Phase 3)
  - Shared MaxPos = 14 (restructured in Phase 4)
"""

import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.bayes_mc import (
    PhaseOptimizer, DEFAULT_CONFIG, print_summary,
)


def main():
    param_space = {
        'PUT_TP':  [0.20, 0.25, 0.30, 0.35, 0.40],
        'PUT_SL':  [-0.35, -0.30, -0.25, -0.20, -0.15, -0.10],
    }

    # Seed configs — baseline + 4 diversity corners/informed priors
    seeds = [
        {'PUT_TP': 0.30, 'PUT_SL': -0.20},  # production baseline (v18)
        {'PUT_TP': 0.25, 'PUT_SL': -0.15},  # tighter both — fastest cycling
        {'PUT_TP': 0.35, 'PUT_SL': -0.25},  # looser both — longer holds
        {'PUT_TP': 0.40, 'PUT_SL': -0.35},  # most forgiving
        {'PUT_TP': 0.20, 'PUT_SL': -0.10},  # tightest extreme
    ]

    base_cfg = dict(DEFAULT_CONFIG)

    # Stress STRESS params track BASE when mode='none' — avoids divergence in config space.
    # Phase 1 never exercises breadth-adaptive puts, so this is a no-op in practice.
    opt = PhaseOptimizer(
        phase_name='1_put_tpsl',
        param_space=param_space,
        base_config=base_cfg,
        seeds=seeds,
        budget=15,
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.22,
        kappa_init=2.0,
        kappa_final=1.0,
    )

    # Keep PUT_TP_STRESS / PUT_SL_STRESS synced with base for seed evaluations
    # (apply_config reads them but PUT_BREADTH_MODE=='none' means they're ignored).

    best, evaluated = opt.run()
    print_summary('1_put_tpsl', best, evaluated)

    # Helper hint for phase 2
    print('\nRECOMMENDED for Phase 2 seed:')
    print(f'  PUT_TP base = {best["params"]["PUT_TP"]}')
    print(f'  PUT_SL base = {best["params"]["PUT_SL"]}')


if __name__ == '__main__':
    main()
