"""Phase H — bug-fixed realistic-SL sweep with breadth-adaptive conditioning.

Built on top of the path-dependent SL P&L bug fix in monte_carlo.py.
Previous Phase D/E/G findings (PUT_SL=-0.02, -0.05, -0.10) were artifacts
of the static-SL-loss bug — the MC reported -X% loss when SL fired,
ignoring how far underlying had drifted past the barrier during hold.

Now realized SL P&L = max(min(barrier, close-based actual P&L), -1.0)
which properly tracks gap-through losses.

Design philosophy:
- Realistic SL range only: PUT_SL ∈ {-0.10, -0.15, -0.20, -0.25, -0.30}
  (no tighter than typical option bid-ask + IV noise budget)
- Test breadth-adaptive: PUT_SL_BASE × PUT_SL_STRESS pair, conditional on breadth
- Revisit puts_first vs calls_first under bug-fixed loss accounting (the
  puts_first DD-C halving may have been bug-amplified)
- Hold MaxPos / cascade / call-side at production-conservative defaults to
  isolate SL signal cleanly

Decision gate:
- If best Phase H utility < production_baseline + 1.0, conclude that
  realistic SL is at production -20% level (tighter doesn't help once bug
  is fixed). Stop chasing SL refinement.
- If best ≥ production + 2.0 with DD-C improvement, continue to Phase I.

Screening: 22-now N=100 × 3 modes, MC_NO_MP=1.
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


def extended_apply_config_h(cfg: dict):
    """Phase H apply: PUT_SL_BASE/STRESS + breadth mode + priority + MaxPos."""
    _orig_apply(cfg)

    # Put SL base + stress (using static mode, no stepped)
    if 'PUT_SL_BASE' in cfg:
        mc.PUT_SL_MODE = 'static'
        mc.PUT_SL = cfg['PUT_SL_BASE']
        mc.PUT_NET_SL = cfg['PUT_SL_BASE'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.PUT_SL_SIGMA = abs(cfg['PUT_SL_BASE']) * mc.PREMIUM_MULT / mc.DELTA
    if 'PUT_SL_STRESS' in cfg:
        mc.PUT_SL_STRESS = cfg['PUT_SL_STRESS']
    if 'PUT_TP_STRESS' in cfg:
        mc.PUT_TP_STRESS = cfg['PUT_TP_STRESS']
    if 'PUT_BREADTH_MODE' in cfg:
        mc.PUT_BREADTH_MODE = cfg['PUT_BREADTH_MODE']
    if 'PUT_BREADTH_THRESHOLD' in cfg:
        mc.PUT_BREADTH_THRESHOLD = cfg['PUT_BREADTH_THRESHOLD']

    # Priority
    if 'PUT_PRIORITY' in cfg:
        mc.PUT_PRIORITY = cfg['PUT_PRIORITY']

    # SL hold
    if 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        mc.PUT_SL_HOLD_BARS_DEFAULT = cfg['PUT_SL_HOLD_BARS_DEFAULT']
        mc.PUT_SL_HOLD_BARS_MONDAY = cfg['PUT_SL_HOLD_BARS_DEFAULT'] + 1


def run_config_3mode_h(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_h(cfg2)
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


bayes_mc.apply_config = extended_apply_config_h
bayes_mc.run_config = run_config_3mode_h
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_h


# Phase H base: production-style locked params (avoid contamination from earlier flawed phases)
PHASE_H_BASE = dict(BASE_CONFIG)
PHASE_H_BASE['PUT_TP'] = 0.30
PHASE_H_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_H_BASE['EARN_SUPP_PUT'] = False  # Phase E confirmed redundant
PHASE_H_BASE['TIER_ALLOC'] = {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_H_BASE['MAX_POSITIONS'] = 16
PHASE_H_BASE['PUT_SL_HOLD_BARS_DEFAULT'] = 3
PHASE_H_BASE['N_ITER'] = 100
PHASE_H_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
# defaults for new axes
PHASE_H_BASE['PUT_SL_BASE'] = -0.20
PHASE_H_BASE['PUT_SL_STRESS'] = -0.20
PHASE_H_BASE['PUT_TP_STRESS'] = 0.30
PHASE_H_BASE['PUT_BREADTH_MODE'] = 'none'
PHASE_H_BASE['PUT_BREADTH_THRESHOLD'] = 50
PHASE_H_BASE['PUT_PRIORITY'] = 'calls_first'


PARAM_SPACE = {
    'PUT_SL_BASE':            [-0.10, -0.15, -0.20, -0.25, -0.30],
    'PUT_SL_STRESS':          [-0.10, -0.15, -0.20, -0.25, -0.30, -0.40],
    'PUT_BREADTH_MODE':       ['none', 'invert', 'same'],
    'PUT_BREADTH_THRESHOLD':  [40, 50, 60],
    'PUT_PRIORITY':           ['calls_first', 'puts_first'],
}

# Carefully-chosen seeds spanning meaningful corners of the design space
SEED_CONFIGS = [
    # 1. Production baseline (anchor — must run for comparison)
    {'PUT_SL_BASE': -0.20, 'PUT_SL_STRESS': -0.20, 'PUT_BREADTH_MODE': 'none',
     'PUT_BREADTH_THRESHOLD': 50, 'PUT_PRIORITY': 'calls_first'},
    # 2. Production + puts_first (tests architectural shift independent of SL)
    {'PUT_SL_BASE': -0.20, 'PUT_SL_STRESS': -0.20, 'PUT_BREADTH_MODE': 'none',
     'PUT_BREADTH_THRESHOLD': 50, 'PUT_PRIORITY': 'puts_first'},
    # 3. Tighter realistic SL static
    {'PUT_SL_BASE': -0.15, 'PUT_SL_STRESS': -0.15, 'PUT_BREADTH_MODE': 'none',
     'PUT_BREADTH_THRESHOLD': 50, 'PUT_PRIORITY': 'calls_first'},
    # 4. Tighter SL + puts_first
    {'PUT_SL_BASE': -0.15, 'PUT_SL_STRESS': -0.15, 'PUT_BREADTH_MODE': 'none',
     'PUT_BREADTH_THRESHOLD': 50, 'PUT_PRIORITY': 'puts_first'},
    # 5. Wider SL static (-30%)
    {'PUT_SL_BASE': -0.30, 'PUT_SL_STRESS': -0.30, 'PUT_BREADTH_MODE': 'none',
     'PUT_BREADTH_THRESHOLD': 50, 'PUT_PRIORITY': 'calls_first'},
    # 6. Breadth-adaptive: tighter in HIGH breadth (bull tape, puts struggle)
    #    'invert' fires stress when breadth >= threshold
    {'PUT_SL_BASE': -0.20, 'PUT_SL_STRESS': -0.10, 'PUT_BREADTH_MODE': 'invert',
     'PUT_BREADTH_THRESHOLD': 60, 'PUT_PRIORITY': 'calls_first'},
    # 7. Breadth-adaptive: WIDER SL in LOW breadth (stress tape, hold high-conviction puts)
    #    'same' fires stress when breadth <= threshold (mirror calls)
    {'PUT_SL_BASE': -0.20, 'PUT_SL_STRESS': -0.30, 'PUT_BREADTH_MODE': 'same',
     'PUT_BREADTH_THRESHOLD': 40, 'PUT_PRIORITY': 'calls_first'},
    # 8. Breadth-adaptive + puts_first
    {'PUT_SL_BASE': -0.20, 'PUT_SL_STRESS': -0.10, 'PUT_BREADTH_MODE': 'invert',
     'PUT_BREADTH_THRESHOLD': 60, 'PUT_PRIORITY': 'puts_first'},
    # 9. Aggressive adaptive: tight base, very tight in stress
    {'PUT_SL_BASE': -0.15, 'PUT_SL_STRESS': -0.10, 'PUT_BREADTH_MODE': 'invert',
     'PUT_BREADTH_THRESHOLD': 60, 'PUT_PRIORITY': 'puts_first'},
    # 10. Phase D winner equivalent (tight static, puts_first) - sanity check post-fix
    {'PUT_SL_BASE': -0.10, 'PUT_SL_STRESS': -0.10, 'PUT_BREADTH_MODE': 'none',
     'PUT_BREADTH_THRESHOLD': 50, 'PUT_PRIORITY': 'puts_first'},
]


def main():
    print('=' * 100)
    print('PHASE H — BUG-FIXED realistic-SL sweep with breadth conditioning')
    print('=' * 100)
    print('Locked: PUT_TP=0.30, MaxPos=16, hold=3, EARN=False, ultra=0.25, prod cascade')
    print()
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Total grid cells: {n}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()
    print('Decision gate:')
    print('  - if best util < baseline_util + 1.0: STOP (realistic SL = production-near)')
    print('  - if best util >= baseline_util + 2.0 AND DD-C improves: continue')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_h',
        param_space=PARAM_SPACE,
        base_config=PHASE_H_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '40')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_h', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_h_top_candidates.json')
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
