"""Phase H1 — SL × HOLD_BARS joint sweep (bug-fixed MC).

The hold-protection mechanic is what creates the gap-through window.
Varying SL without varying hold misses the actual lever.

Hypothesis: with bug-fixed MC, **hold=0** (no SL hold) eliminates gap-through
risk by firing SL immediately at the barrier value. PUT_SL=-0.20 + hold=0
should match production's INTENDED behavior (stop fires at -20% loss as
soon as breached).

Compare:
- hold=0: SL active immediately. Realized loss ≈ sl_pct (barrier).
- hold=3 (production): 3-bar drift window. Realized often -50% to -100%.

Param space:
- PUT_SL ∈ {-0.10, -0.15, -0.20, -0.25, -0.30}
- HOLD_BARS ∈ {0, 1, 2, 3, 4}
- PUT_PRIORITY ∈ {calls_first, puts_first}
- Lock other axes at production defaults

Decision gate:
- If hold=0 + any SL gives positive utility & low collapse → ship that
- If even hold=0 has high collapse → strategy fundamentally weak under
  realistic option accounting; need to revisit signal quality / TP / etc.
"""
from __future__ import annotations
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments.v27_optimization.joint_sweep import (
    extended_apply_config as _orig_apply,
    SCREENING_WINDOWS,
    BASE_CONFIG,
)
from experiments import bayes_mc


def extended_apply_config_h1(cfg: dict):
    _orig_apply(cfg)
    if 'PUT_SL_BASE' in cfg:
        mc.PUT_SL_MODE = 'static'
        mc.PUT_SL = cfg['PUT_SL_BASE']
        mc.PUT_NET_SL = cfg['PUT_SL_BASE'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.PUT_SL_SIGMA = abs(cfg['PUT_SL_BASE']) * mc.PREMIUM_MULT / mc.DELTA
    if 'PUT_PRIORITY' in cfg:
        mc.PUT_PRIORITY = cfg['PUT_PRIORITY']
    if 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        mc.PUT_SL_HOLD_BARS_DEFAULT = cfg['PUT_SL_HOLD_BARS_DEFAULT']
        # Monday auto = +1 unless explicitly set
        if 'PUT_SL_HOLD_BARS_MONDAY' in cfg:
            mc.PUT_SL_HOLD_BARS_MONDAY = cfg['PUT_SL_HOLD_BARS_MONDAY']
        else:
            mc.PUT_SL_HOLD_BARS_MONDAY = max(0, cfg['PUT_SL_HOLD_BARS_DEFAULT'] + 1) if cfg['PUT_SL_HOLD_BARS_DEFAULT'] > 0 else 0


def run_config_3mode_h1(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_h1(cfg2)
    if version is None:
        from database.models.core import AlgorithmVersion
        version = AlgorithmVersion.get_active_scores_version()
    out = {}
    for label, d_start, d_end in windows:
        if silent:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                window_res = mc.run_window(label, d_start, d_end, version)
        else:
            window_res = mc.run_window(label, d_start, d_end, version)
        real = window_res.get('realistic', {})
        cons = window_res.get('conservative', {})
        opt  = window_res.get('optimistic', {})
        max_dd = max(float(real.get('worst_dd', 0)), float(cons.get('worst_dd', 0)), float(opt.get('worst_dd', 0)))
        max_collapse = max(float(real.get('p_coll', 0)), float(cons.get('p_coll', 0)), float(opt.get('p_coll', 0)))
        out[label] = {
            'mean_ret': float(real.get('mean_ret', 0)),
            'med_ret': float(real.get('med_ret', 0)),
            'worst_dd': max_dd, 'mean_dd': float(real.get('mean_dd', 0)),
            'p_collapse': max_collapse,
            'call_tp': float(real.get('call_tp', 0)),
            'put_tp': float(real.get('put_tp', 0)),
            'call_trades': float(real.get('call_trades', 0)),
            'put_trades': float(real.get('put_trades', 0)),
        }
    return out


bayes_mc.apply_config = extended_apply_config_h1
bayes_mc.run_config = run_config_3mode_h1
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_h1


PHASE_H1_BASE = dict(BASE_CONFIG)
PHASE_H1_BASE['PUT_TP'] = 0.30
PHASE_H1_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_H1_BASE['EARN_SUPP_PUT'] = False
PHASE_H1_BASE['TIER_ALLOC'] = {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_H1_BASE['MAX_POSITIONS'] = 16
PHASE_H1_BASE['N_ITER'] = 100
PHASE_H1_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
PHASE_H1_BASE['PUT_BREADTH_MODE'] = 'none'
PHASE_H1_BASE['PUT_SL_BASE'] = -0.20
PHASE_H1_BASE['PUT_SL_HOLD_BARS_DEFAULT'] = 3
PHASE_H1_BASE['PUT_PRIORITY'] = 'calls_first'


PARAM_SPACE = {
    'PUT_SL_BASE':              [-0.10, -0.15, -0.20, -0.25, -0.30],
    'PUT_SL_HOLD_BARS_DEFAULT': [0, 1, 2, 3, 4],
    'PUT_PRIORITY':             ['calls_first', 'puts_first'],
}

# Strategically chosen seeds covering corners + interesting interactions
SEED_CONFIGS = [
    # 1. Pure production
    {'PUT_SL_BASE': -0.20, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first'},
    # 2. Production + no hold (tests the "hold creates gap-through" hypothesis directly)
    {'PUT_SL_BASE': -0.20, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first'},
    # 3. Production + no hold + puts_first
    {'PUT_SL_BASE': -0.20, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'puts_first'},
    # 4. Tighter SL + no hold
    {'PUT_SL_BASE': -0.15, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first'},
    # 5. Tighter + no hold + puts_first
    {'PUT_SL_BASE': -0.15, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'puts_first'},
    # 6. Tightest + no hold + puts_first (boundary)
    {'PUT_SL_BASE': -0.10, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'puts_first'},
    # 7. Wider SL + no hold
    {'PUT_SL_BASE': -0.25, 'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first'},
    # 8. Hold=1 (minimum useful hold)
    {'PUT_SL_BASE': -0.20, 'PUT_SL_HOLD_BARS_DEFAULT': 1, 'PUT_PRIORITY': 'calls_first'},
    # 9. Hold=2 + tighter SL (compromise)
    {'PUT_SL_BASE': -0.15, 'PUT_SL_HOLD_BARS_DEFAULT': 2, 'PUT_PRIORITY': 'calls_first'},
    # 10. Phase H seed-best (-0.10 + hold=3 + puts_first) — verify under H1's config
    {'PUT_SL_BASE': -0.10, 'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'puts_first'},
]


def main():
    print('=' * 100)
    print('PHASE H1 — SL × HOLD_BARS joint sweep (bug-fixed MC)')
    print('=' * 100)
    print('Locked: PUT_TP=0.30, MaxPos=16, EARN=False, ultra=0.25, prod cascade, F3f production')
    print()
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Total grid cells: {n}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()
    print('Hypothesis: hold=0 should DRAMATICALLY reduce collapse rates because')
    print('  SL fires at barrier (no drift accumulation during hold). PUT_SL=-0.20 + hold=0')
    print('  matches the original "stop at -20%" intent.')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_h1',
        param_space=PARAM_SPACE,
        base_config=PHASE_H1_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '30')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_h1', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_h1_top_candidates.json')
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
    print(f'\nTop 10 saved to {out_path}')


if __name__ == '__main__':
    main()
