"""Phase D — full boundary + extended axes sweep.

Adds to Phase B's findings:
1. PUT_SL boundary extension: tighter (-0.10, -0.13) and finer grain
2. PUT_SL_MODE: 'static' vs 'stepped' (period-aware, MAE-anchored)
3. MAX_POSITIONS extension: 18, 20 (Phase B converged at 16, the boundary)
4. PUT_SL_HOLD_BARS_DEFAULT: 0, 2, 3, 4 (production = 3)
5. PUT_PRIORITY: 'calls_first' (current), 'puts_first', 'merged' (conviction-sorted)

MAE-anchored stepped variants from v27 5y assessment:
  Puts <25:   MAEw_7d=0.32σ  MAEw_15d=0.39σ  MAEw_30d=0.51σ
  Mapped to option SL @1.0×: bar 1-5 ~-9%, 6-10 ~-11%, 11-15 ~-14%
  @1.25×: bar 1-5 ~-11%, 6-10 ~-14%, 11-15 ~-17%

PUT_SL_VARIANT encodes both static and stepped modes:
  's10', 's13', 's15', 's17', 's20'  - static -10%/-13%/-15%/-17%/-20%
  'step1x', 'step125x', 'step15x'    - stepped MAE-anchored at 1.0/1.25/1.5×

Screening config (same as Phase B):
  - 22-now single window
  - N=100 iterations
  - All 3 collision modes
  - MC_NO_MP=1 required
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
from experiments import bayes_mc

# ---- PUT_SL_VARIANT decoder ----
# Maps the categorical sweep value to (PUT_SL_MODE, PUT_SL_or_STEPPED config)

PUT_SL_VARIANTS = {
    's10':       ('static', -0.10, None),
    's13':       ('static', -0.13, None),
    's15':       ('static', -0.15, None),   # Phase B winner
    's17':       ('static', -0.17, None),
    's20':       ('static', -0.20, None),   # production baseline
    # Stepped mappings (in units of MAE_winner_σ for puts <25 from v27 5y):
    # bar 1-5 -> MAEw_7d=0.32σ, bar 6-10 -> MAEw_15d=0.39σ, bar 11-15 -> MAEw_30d=0.51σ
    # option SL = 0.5 * MAE_σ / 1.82 = 0.275 * MAE_σ → -8.8%, -10.7%, -14.0% at 1.0×
    'step1x':    ('stepped', None, [(5, -0.09), (10, -0.11), (15, -0.14)]),
    'step125x':  ('stepped', None, [(5, -0.11), (10, -0.13), (15, -0.17)]),
    'step15x':   ('stepped', None, [(5, -0.13), (10, -0.16), (15, -0.21)]),
}


def extended_apply_config_d(cfg: dict):
    """Phase D extended apply: handles PUT_SL_VARIANT, PUT_SL_HOLD_BARS_*, PUT_PRIORITY."""
    _orig_extended(cfg)

    # PUT_SL variant decoding
    variant = cfg.get('PUT_SL_VARIANT')
    if variant:
        mode, static_sl, stepped = PUT_SL_VARIANTS[variant]
        mc.PUT_SL_MODE = mode
        if mode == 'static':
            mc.PUT_SL = static_sl
            mc.PUT_NET_SL = static_sl + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.PUT_SL_SIGMA = abs(static_sl) * mc.PREMIUM_MULT / mc.DELTA
        else:
            mc.PUT_SL_STEPPED = list(stepped)
            # PUT_SL becomes the deepest band (used as a fallback default)
            deepest = stepped[-1][1]
            mc.PUT_SL = deepest
            mc.PUT_NET_SL = deepest + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.PUT_SL_SIGMA = abs(deepest) * mc.PREMIUM_MULT / mc.DELTA

    # Hold bars
    if 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        mc.PUT_SL_HOLD_BARS_DEFAULT = cfg['PUT_SL_HOLD_BARS_DEFAULT']
    if 'PUT_SL_HOLD_BARS_MONDAY' in cfg:
        mc.PUT_SL_HOLD_BARS_MONDAY = cfg['PUT_SL_HOLD_BARS_MONDAY']
    elif 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        # Auto: Monday = default+1 (mirrors production)
        mc.PUT_SL_HOLD_BARS_MONDAY = cfg['PUT_SL_HOLD_BARS_DEFAULT'] + 1

    # Priority
    if 'PUT_PRIORITY' in cfg:
        mc.PUT_PRIORITY = cfg['PUT_PRIORITY']


bayes_mc.apply_config = extended_apply_config_d
bayes_mc.run_config = run_config_3mode
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

# CRITICAL: joint_sweep.run_config_3mode calls extended_apply_config by bare name,
# which resolves to joint_sweep's module global — NOT bayes_mc.apply_config.
# Monkey-patch joint_sweep.extended_apply_config so its run_config_3mode picks up
# our phase-D-extended apply_config.
import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_d


# Phase D base: lock CONFIRMED Phase C winner (cand_2 — PASSED ship gate)
# cand_2 was rank 2 at N=100 but rank 1 at N=500 because cand_1's concentrated
# puts (put_top=0.22) regressed -30.6% on 2024 at full fidelity (FAIL gate).
# cand_2 = PUT_SL=-0.15, put_top=0.15 (default cascade), EARN_SUPP_PUT=False,
# MaxPos=16, ultra=0.28. +573% on 22-now Realistic, 0% Realistic regression,
# DD-C 71.5%, 0% collapse.
PHASE_D_BASE = dict(BASE_CONFIG)
PHASE_D_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_D_BASE['EARN_SUPP_PUT'] = False
PHASE_D_BASE['TIER_ALLOC'] = {'ultra': 0.28, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_D_BASE['N_ITER'] = 100
PHASE_D_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']

# Defaults for new axes (set if not in PARAM_SPACE)
PHASE_D_BASE['PUT_SL_VARIANT'] = 's20'              # production
PHASE_D_BASE['PUT_SL_HOLD_BARS_DEFAULT'] = 3        # production
PHASE_D_BASE['PUT_PRIORITY'] = 'calls_first'        # production


PARAM_SPACE = {
    'PUT_SL_VARIANT':           ['s10', 's13', 's15', 's17', 's20',
                                  'step1x', 'step125x', 'step15x'],
    'MAX_POSITIONS':            [14, 16, 18, 20],
    'PUT_SL_HOLD_BARS_DEFAULT': [0, 2, 3, 4],
    'PUT_PRIORITY':             ['calls_first', 'puts_first', 'merged'],
}

# Seeds: anchor configurations that probe each axis
SEED_CONFIGS = [
    # 1. Production baseline (sanity check)
    {'PUT_SL_VARIANT': 's20', 'MAX_POSITIONS': 14, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    # 2. Phase B winner (replicates rank 1)
    {'PUT_SL_VARIANT': 's15', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    # 3. Tighter PUT_SL (boundary)
    {'PUT_SL_VARIANT': 's13', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    # 4. Even tighter (artifact zone test)
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    # 5. MaxPos extension
    {'PUT_SL_VARIANT': 's15', 'MAX_POSITIONS': 18, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    {'PUT_SL_VARIANT': 's15', 'MAX_POSITIONS': 20, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    # 7. Stepped SL (MAE-principled)
    {'PUT_SL_VARIANT': 'step1x',   'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    {'PUT_SL_VARIANT': 'step125x', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    # 9. Hold bar variations
    {'PUT_SL_VARIANT': 's15', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first'},
    {'PUT_SL_VARIANT': 's15', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 4, 'PUT_PRIORITY': 'calls_first'},
    # 11. Put priority experiments
    {'PUT_SL_VARIANT': 's15', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'puts_first'},
    {'PUT_SL_VARIANT': 's15', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'merged'},
    # 13. Combined: stepped + tighter hold + merged priority
    {'PUT_SL_VARIANT': 'step1x', 'MAX_POSITIONS': 16, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'merged'},
    # 14. Combined: tighter SL + larger pool + puts_first
    {'PUT_SL_VARIANT': 's13', 'MAX_POSITIONS': 18, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'puts_first'},
]


def main():
    print('=' * 100)
    print('PHASE D — full boundary + extended axes sweep (v27 ad02704)')
    print('=' * 100)
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Total grid cells: {n}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print(f'Screening: 22-now × N={PHASE_D_BASE["N_ITER"]} × all 3 modes (MC_NO_MP=1 REQUIRED)')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_d',
        param_space=PARAM_SPACE,
        base_config=PHASE_D_BASE,
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
    bayes_mc.print_summary('v27_phase_d', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_d_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            full = dict(PHASE_D_BASE)
            for k, v in r['params'].items():
                full[k] = v
            payload.append({
                'rank':    len(payload) + 1,
                'utility': r['utility'],
                'params':  r['params'],
                'full_config': {k: full.get(k) for k in [
                    'PUT_SL_VARIANT', 'MAX_POSITIONS', 'PUT_SL_HOLD_BARS_DEFAULT', 'PUT_PRIORITY',
                    'PUT_TIER_ALLOC', 'TIER_ALLOC', 'EARN_SUPP_PUT'
                ]},
                'windows': r['windows'],
                'log_return': r['util_info']['log_return'],
                'max_dd':  r['util_info']['max_dd'],
                'max_collapse': r['util_info']['max_collapse'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 candidates saved to {out_path}')


if __name__ == '__main__':
    main()
