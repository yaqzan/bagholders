"""Phase D — boundary extension sweep.

Phase B converged with both winning axes at the boundary of the search space:
  - PUT_SL = -0.15 (lowest tested)
  - MAX_POSITIONS = 16 (highest tested)

This phase extends the boundaries to find the true optimum. Other params
fixed at Phase B winner.

Caution: PUT_SL = -0.10 was a documented same-stock re-entry artifact in
prior literature (raw put TP rate collapsed to 28-31% vs 43.5% BE). However,
v27 cuts put N -75%, so the artifact pressure may be reduced. Phase C/D
should explicitly verify raw PutTP% > 43.5% on all candidates.

Screening config (same as Phase B):
  - 22-now single window
  - N=100 iterations
  - All 3 collision modes
  - MC_NO_MP=1 (must be set in env)

Output: phase_d_top_candidates.json (consumed by phase_d_validate.py)
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.v27_optimization.joint_sweep import (
    extended_apply_config,
    run_config_3mode,
    SCREENING_WINDOWS,
    BASE_CONFIG,
)
from experiments import bayes_mc

bayes_mc.apply_config = extended_apply_config
bayes_mc.run_config = run_config_3mode
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}


# Phase B winner: rank 1 in phase_b_top_candidates.json
PHASE_B_WINNER = {
    'PUT_SL': -0.15,
    'PUT_TIER_ALLOC.put_top': 0.22,
    'PUT_TIER_ALLOC.put_mid': 0.12,
    'EARN_SUPP_PUT': True,
    'MAX_POSITIONS': 16,
    'TIER_ALLOC.ultra': 0.28,
}

# Phase D base: lock all Phase B winner params except PUT_SL and MAX_POSITIONS
PHASE_D_BASE = dict(BASE_CONFIG)
PHASE_D_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.22, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_D_BASE['EARN_SUPP_PUT'] = True
PHASE_D_BASE['TIER_ALLOC'] = {'ultra': 0.28, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_D_BASE['N_ITER'] = 100
PHASE_D_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

# Sweep just the two boundary axes
PARAM_SPACE = {
    'PUT_SL':         [-0.10, -0.13, -0.15, -0.17, -0.20],
    'MAX_POSITIONS':  [14, 16, 18, 20, 22],
}

# Seeds: anchor + boundary extensions
SEED_CONFIGS = [
    # Phase B winner (anchor)
    {'PUT_SL': -0.15, 'MAX_POSITIONS': 16},
    # Test PUT_SL extensions
    {'PUT_SL': -0.10, 'MAX_POSITIONS': 16},
    {'PUT_SL': -0.13, 'MAX_POSITIONS': 16},
    # Test MaxPos extensions
    {'PUT_SL': -0.15, 'MAX_POSITIONS': 18},
    {'PUT_SL': -0.15, 'MAX_POSITIONS': 20},
    # Combined extensions
    {'PUT_SL': -0.13, 'MAX_POSITIONS': 18},
    {'PUT_SL': -0.10, 'MAX_POSITIONS': 20},
]


def main():
    print('=' * 100)
    print('PHASE D — BOUNDARY EXTENSION (PUT_SL × MAX_POSITIONS)')
    print('=' * 100)
    print('Anchor: Phase B winner — put_top=0.22 put_mid=0.12 EARN=True ultra=0.28')
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Total grid cells: {n}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_d',
        param_space=PARAM_SPACE,
        base_config=PHASE_D_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '20')),
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.30,
        kappa_init=2.0,
        kappa_final=1.0,
        windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_d', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_d_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            # merge with Phase B locked params for full-config representation
            full = dict(PHASE_B_WINNER)
            full.update(r['params'])
            payload.append({
                'rank':    len(payload) + 1,
                'utility': r['utility'],
                'params':  full,
                'params_swept': r['params'],
                'windows': r['windows'],
                'log_return': r['util_info']['log_return'],
                'max_dd':  r['util_info']['max_dd'],
                'max_collapse': r['util_info']['max_collapse'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 candidates saved to {out_path}')


if __name__ == '__main__':
    main()
