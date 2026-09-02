"""Phase E — Call-side TP/SL + cascade depth sweep.

Apply same MAE-anchored approach to calls. From v27 5y assessment:
  Calls 75+: MAEw_15d = -0.727σ → option SL @1.0× = -20%, @1.25× = -25%, @1.5× = -30%
  Calls 95+: MAEw_15d = -0.911σ → option SL @1.0× = -25%, @1.25× = -31%

Production CALL_SL_BASE = -0.35 ≈ 1.75× MAEw_15d for 75+ — same over-sizing
as we found in puts. Tighter SL may improve capital velocity.

Phase E locks Phase D winner's put-side params and explores:
  - CALL_SL_BASE, CALL_SL_STRESS (breadth-adaptive pair)
  - CALL_TP_BASE, CALL_TP_STRESS
  - TIER_ALLOC.top, .mid, .low (cascade depth)

Screening: 22-now N=100 × 3 modes, MC_NO_MP=1 required.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments.v27_optimization.joint_sweep import (
    extended_apply_config as _orig_extended,
    run_config_3mode,
    SCREENING_WINDOWS,
    BASE_CONFIG,
)
from experiments.v27_optimization.phase_d_full import extended_apply_config_d
from experiments import bayes_mc


def extended_apply_config_e(cfg: dict):
    """Phase E: Phase D apply + extended call-side knobs."""
    extended_apply_config_d(cfg)

    # Call TP/SL — recomputed from cfg keys directly (override BASE_CONFIG values)
    if 'CALL_SL_BASE' in cfg:    mc.SL_BASE = cfg['CALL_SL_BASE']
    if 'CALL_SL_STRESS' in cfg:  mc.SL_STRESS = cfg['CALL_SL_STRESS']
    if 'CALL_TP_BASE' in cfg:    mc.TP_BASE = cfg['CALL_TP_BASE']
    if 'CALL_TP_STRESS' in cfg:  mc.TP_STRESS = cfg['CALL_TP_STRESS']

    # Recompute derived constants
    mc.NET_TP_BASE   = mc.TP_BASE   + mc.SLIP_ENTRY + mc.SLIP_TP
    mc.NET_TP_STRESS = mc.TP_STRESS + mc.SLIP_ENTRY + mc.SLIP_TP
    mc.NET_SL_BASE   = mc.SL_BASE   + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.NET_SL_STRESS = mc.SL_STRESS + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.TP_SIGMA_BASE   = mc.TP_BASE   * mc.PREMIUM_MULT / mc.DELTA
    mc.TP_SIGMA_STRESS = mc.TP_STRESS * mc.PREMIUM_MULT / mc.DELTA
    mc.SL_SIGMA_BASE   = abs(mc.SL_BASE)   * mc.PREMIUM_MULT / mc.DELTA
    mc.SL_SIGMA_STRESS = abs(mc.SL_STRESS) * mc.PREMIUM_MULT / mc.DELTA


bayes_mc.apply_config = extended_apply_config_e
bayes_mc.run_config = run_config_3mode
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}


# Phase E base: lock Phase D winner (will be filled from phase_d_top_candidates.json
# at runtime). For now use Phase C winner (cand_2) as fallback.
def _load_phase_d_winner():
    """Load Phase D top candidate from JSON if available, else fall back to cand_2."""
    pd_path = os.path.join(os.path.dirname(__file__), 'phase_d_top_candidates.json')
    if os.path.exists(pd_path):
        with open(pd_path) as f:
            data = json.load(f)
        if data:
            top = data[0]
            full = top.get('full_config', {})
            return {
                'PUT_SL_VARIANT':         full.get('PUT_SL_VARIANT', 's15'),
                'PUT_SL_HOLD_BARS_DEFAULT': full.get('PUT_SL_HOLD_BARS_DEFAULT', 3),
                'PUT_PRIORITY':           full.get('PUT_PRIORITY', 'calls_first'),
                'MAX_POSITIONS':          full.get('MAX_POSITIONS', 16),
                'PUT_TIER_ALLOC':         full.get('PUT_TIER_ALLOC', {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}),
                'EARN_SUPP_PUT':          full.get('EARN_SUPP_PUT', False),
                'TIER_ALLOC_ULTRA':       full.get('TIER_ALLOC', {}).get('ultra', 0.28),
            }
    # Fallback to Phase C cand_2
    return {
        'PUT_SL_VARIANT': 's15',
        'PUT_SL_HOLD_BARS_DEFAULT': 3,
        'PUT_PRIORITY': 'calls_first',
        'MAX_POSITIONS': 16,
        'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
        'EARN_SUPP_PUT': False,
        'TIER_ALLOC_ULTRA': 0.28,
    }


PHASE_E_BASE = dict(BASE_CONFIG)
_pd_winner = _load_phase_d_winner()
PHASE_E_BASE['PUT_SL_VARIANT'] = _pd_winner['PUT_SL_VARIANT']
PHASE_E_BASE['PUT_SL_HOLD_BARS_DEFAULT'] = _pd_winner['PUT_SL_HOLD_BARS_DEFAULT']
PHASE_E_BASE['PUT_PRIORITY'] = _pd_winner['PUT_PRIORITY']
PHASE_E_BASE['MAX_POSITIONS'] = _pd_winner['MAX_POSITIONS']
PHASE_E_BASE['PUT_TIER_ALLOC'] = _pd_winner['PUT_TIER_ALLOC']
PHASE_E_BASE['EARN_SUPP_PUT'] = _pd_winner['EARN_SUPP_PUT']
# Cascade defaults — will be swept
ULTRA = _pd_winner['TIER_ALLOC_ULTRA']
PHASE_E_BASE['TIER_ALLOC'] = {'ultra': ULTRA, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
# Call TP/SL defaults (production)
PHASE_E_BASE['CALL_TP_BASE']   = 0.30
PHASE_E_BASE['CALL_TP_STRESS'] = 0.35
PHASE_E_BASE['CALL_SL_BASE']   = -0.35
PHASE_E_BASE['CALL_SL_STRESS'] = -0.40
PHASE_E_BASE['N_ITER'] = 100
PHASE_E_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']


PARAM_SPACE = {
    # Call SL: tighter than -0.35 (production) tested with MAE-anchored points
    # MAEw_15d=-0.727σ → option SL @1.0×=-20%, @1.25×=-25%, @1.5×=-30%
    'CALL_SL_BASE':       [-0.25, -0.30, -0.35, -0.40],
    'CALL_SL_STRESS':     [-0.30, -0.35, -0.40, -0.45],
    # Call TP: explore both tighter and wider
    'CALL_TP_BASE':       [0.25, 0.30, 0.35],
    'CALL_TP_STRESS':     [0.30, 0.35, 0.40],
    # Cascade depth — 75-79 tier ('low') is the high-volume compounder
    'TIER_ALLOC.top':     [0.13, 0.15, 0.17],
    'TIER_ALLOC.mid':     [0.13, 0.15, 0.17],
    'TIER_ALLOC.low':     [0.13, 0.15, 0.17],
}

# Seeds: production baseline + boundary points
SEED_CONFIGS = [
    # 1. Production baseline
    {'CALL_SL_BASE': -0.35, 'CALL_SL_STRESS': -0.40, 'CALL_TP_BASE': 0.30, 'CALL_TP_STRESS': 0.35,
     'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.15},
    # 2. Tighter SL (1.25× MAE-anchored)
    {'CALL_SL_BASE': -0.30, 'CALL_SL_STRESS': -0.35, 'CALL_TP_BASE': 0.30, 'CALL_TP_STRESS': 0.35,
     'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.15},
    # 3. Even tighter SL (1.0× MAE-anchored, boundary)
    {'CALL_SL_BASE': -0.25, 'CALL_SL_STRESS': -0.30, 'CALL_TP_BASE': 0.30, 'CALL_TP_STRESS': 0.35,
     'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.15},
    # 4. Wider TP (capture more MFE)
    {'CALL_SL_BASE': -0.35, 'CALL_SL_STRESS': -0.40, 'CALL_TP_BASE': 0.35, 'CALL_TP_STRESS': 0.40,
     'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.15},
    # 5. Tighter TP (faster recycle)
    {'CALL_SL_BASE': -0.35, 'CALL_SL_STRESS': -0.40, 'CALL_TP_BASE': 0.25, 'CALL_TP_STRESS': 0.30,
     'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.15},
    # 6. Cascade flatten (lower top, raise low)
    {'CALL_SL_BASE': -0.35, 'CALL_SL_STRESS': -0.40, 'CALL_TP_BASE': 0.30, 'CALL_TP_STRESS': 0.35,
     'TIER_ALLOC.top': 0.13, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.17},
    # 7. Cascade peaked (raise top)
    {'CALL_SL_BASE': -0.35, 'CALL_SL_STRESS': -0.40, 'CALL_TP_BASE': 0.30, 'CALL_TP_STRESS': 0.35,
     'TIER_ALLOC.top': 0.17, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.13},
    # 8. Combined: tighter SL + tighter TP (extreme velocity)
    {'CALL_SL_BASE': -0.30, 'CALL_SL_STRESS': -0.35, 'CALL_TP_BASE': 0.25, 'CALL_TP_STRESS': 0.30,
     'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.15},
]


def main():
    print('=' * 100)
    print('PHASE E — Call TP/SL + cascade depth sweep (v27)')
    print('=' * 100)
    print(f'Locked from Phase D winner:')
    print(f'  PUT_SL_VARIANT: {PHASE_E_BASE["PUT_SL_VARIANT"]}')
    print(f'  PUT_SL_HOLD_BARS_DEFAULT: {PHASE_E_BASE["PUT_SL_HOLD_BARS_DEFAULT"]}')
    print(f'  PUT_PRIORITY: {PHASE_E_BASE["PUT_PRIORITY"]}')
    print(f'  MAX_POSITIONS: {PHASE_E_BASE["MAX_POSITIONS"]}')
    print(f'  EARN_SUPP_PUT: {PHASE_E_BASE["EARN_SUPP_PUT"]}')
    print(f'  TIER_ALLOC.ultra: {PHASE_E_BASE["TIER_ALLOC"]["ultra"]}')
    print()
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Total grid cells: {n}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_e',
        param_space=PARAM_SPACE,
        base_config=PHASE_E_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '50')),
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.30,
        kappa_init=2.0,
        kappa_final=1.0,
        windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_e', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_e_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            payload.append({
                'rank':    len(payload) + 1,
                'utility': r['utility'],
                'params':  r['params'],
                'windows': r['windows'],
                'log_return': r['util_info']['log_return'],
                'max_dd':  r['util_info']['max_dd'],
                'max_collapse': r['util_info']['max_collapse'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 candidates saved to {out_path}')


if __name__ == '__main__':
    main()
