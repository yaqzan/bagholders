"""Phase H4 — DD-targeted sweep anchored at V1 baseline.

V1 baseline (focused_comparison): production strategy with ONLY hold=3→0 change.
- PUT_SL=-0.20, hold=0, EARN_SUPP_PUT=True, calls_first
- ultra=0.25, top/mid/low=0.15, put cascade 0.15/0.12/0.12
- MaxPos=14 (production), F3f production
- Result: +25.7B% Real, DD 84.0%

Goal: drive DD from 84% toward 65-72% target while preserving compound return.

Custom utility:
- Soft DD penalty starts at 0.65 (target)
- Hard DD penalty above 0.80 (floor)
- Collapse penalty doubled

Sweep axes:
- MAX_POSITIONS ∈ {10, 12, 14}
- TIER_ALLOC.ultra ∈ {0.18, 0.22, 0.25}
- TIER_ALLOC.top/mid/low ∈ {0.10, 0.12, 0.15}
- PUT_TIER_ALLOC.put_top ∈ {0.10, 0.12, 0.15}
- F3F_PUT_FLOOR ∈ {0.50, 0.60, 0.70}
- F3F_CALL_FLOOR ∈ {0.50, 0.60, 0.70}

Locked: PUT_SL=-0.20, hold=0, EARN=True, calls_first, F3f thresholds prod.
"""
from __future__ import annotations
import json
import os
import sys
import math

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

DD_TARGET = 0.65
DD_HARD_LIMIT = 0.80


def utility_from_results_h4(window_results: dict) -> dict:
    per_window_log = {}
    log_sum = 0.0
    w_sum = 0.0
    max_dd = 0.0
    max_coll = 0.0
    for label, r in window_results.items():
        w = bayes_mc.WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, float(r['mean_ret']))
        lr = math.log1p(ret_pct / 100.0)
        log_sum += w * lr
        w_sum += w
        per_window_log[label] = lr
        max_dd = max(max_dd, float(r['worst_dd']) / 100.0)
        max_coll = max(max_coll, float(r['p_collapse']) / 100.0)

    soft_excess = max(0.0, max_dd - DD_TARGET)
    soft_penalty = 30.0 * (soft_excess ** 2) / 0.01

    hard_excess = max(0.0, max_dd - DD_HARD_LIMIT)
    hard_penalty = 200.0 * (hard_excess ** 2) / 0.01

    collapse_penalty = 1000.0 * max_coll

    utility = log_sum - soft_penalty - hard_penalty - collapse_penalty

    return {
        'utility': utility, 'log_return': log_sum,
        'log_return_weighted_sum': log_sum,
        'dd_penalty': soft_penalty + hard_penalty,
        'collapse_penalty': collapse_penalty,
        'max_dd': max_dd, 'max_collapse': max_coll,
        'per_window_log': per_window_log,
    }


bayes_mc.utility_from_results = utility_from_results_h4


def extended_apply_config_h4(cfg: dict):
    _orig_apply(cfg)
    # V1 baseline locks
    mc.PUT_SL_MODE = 'static'
    mc.PUT_SL = -0.20
    mc.PUT_NET_SL = -0.20 + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.PUT_SL_SIGMA = 0.20 * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_SL_HOLD_BARS_DEFAULT = 0
    mc.PUT_SL_HOLD_BARS_MONDAY = 0
    mc.PUT_PRIORITY = 'calls_first'
    mc.EARN_SUPP_PUT = True

    # Sweep axes
    if 'F3F_PUT_FLOOR' in cfg:   mc.F3F_PUT_FLOOR = cfg['F3F_PUT_FLOOR']
    if 'F3F_CALL_FLOOR' in cfg:  mc.F3F_CALL_FLOOR = cfg['F3F_CALL_FLOOR']

    if any(k in cfg for k in ('TIER_ALLOC.ultra', 'TIER_ALLOC.top', 'TIER_ALLOC.mid', 'TIER_ALLOC.low')):
        ta = dict(mc.TIER_ALLOC)
        ta['ultra'] = cfg.get('TIER_ALLOC.ultra', ta.get('ultra', 0.25))
        ta['top']   = cfg.get('TIER_ALLOC.top',   ta.get('top',   0.15))
        ta['mid']   = cfg.get('TIER_ALLOC.mid',   ta.get('mid',   0.15))
        ta['low']   = cfg.get('TIER_ALLOC.low',   ta.get('low',   0.15))
        mc.TIER_ALLOC = ta

    if 'PUT_TIER_ALLOC.put_top' in cfg:
        pta = dict(mc.PUT_TIER_ALLOC)
        pta['put_top'] = cfg['PUT_TIER_ALLOC.put_top']
        pta['put_mid'] = cfg.get('PUT_TIER_ALLOC.put_mid', pta.get('put_mid', 0.12))
        pta['put_low'] = cfg.get('PUT_TIER_ALLOC.put_low', pta.get('put_low', 0.12))
        mc.PUT_TIER_ALLOC = pta


def run_config_3mode_h4(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_h4(cfg2)
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


bayes_mc.apply_config = extended_apply_config_h4
bayes_mc.run_config = run_config_3mode_h4
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}
import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_h4


PHASE_H4_BASE = dict(BASE_CONFIG)
PHASE_H4_BASE['PUT_TP'] = 0.30
PHASE_H4_BASE['EARN_SUPP_PUT'] = True
PHASE_H4_BASE['MAX_POSITIONS'] = 14
PHASE_H4_BASE['N_ITER'] = 100
PHASE_H4_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
PHASE_H4_BASE['PUT_BREADTH_MODE'] = 'none'
PHASE_H4_BASE['TIER_ALLOC'] = {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_H4_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_H4_BASE['F3F_PUT_FLOOR'] = 0.75
PHASE_H4_BASE['F3F_CALL_FLOOR'] = 0.70


PARAM_SPACE = {
    'TIER_ALLOC.ultra':         [0.18, 0.22, 0.25],
    'TIER_ALLOC.top':           [0.10, 0.12, 0.15],
    'TIER_ALLOC.mid':           [0.10, 0.12, 0.15],
    'PUT_TIER_ALLOC.put_top':   [0.10, 0.12, 0.15],
    'F3F_PUT_FLOOR':            [0.50, 0.60, 0.70],
    'F3F_CALL_FLOOR':           [0.50, 0.60, 0.70],
    'MAX_POSITIONS':            [10, 12, 14],
}

# Strategic seeds: V1 baseline + DD-reduction probes
SEED_CONFIGS = [
    # 1. V1 EXACT baseline (production allocs + hold=0)
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 14},
    # 2. Smaller MaxPos
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 12},
    # 3. Mid-cut allocs only
    {'TIER_ALLOC.ultra': 0.22, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.12, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 14},
    # 4. Aggressive alloc cuts
    {'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.10, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 14},
    # 5. F3f floors only (more cut in stress)
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50, 'MAX_POSITIONS': 14},
    # 6. Mid alloc + F3f
    {'TIER_ALLOC.ultra': 0.22, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.12, 'F3F_PUT_FLOOR': 0.60, 'F3F_CALL_FLOOR': 0.60, 'MAX_POSITIONS': 14},
    # 7. Aggressive alloc + F3f
    {'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.10, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50, 'MAX_POSITIONS': 14},
    # 8. Aggressive everything
    {'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.10, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50, 'MAX_POSITIONS': 10},
    # 9. Mid alloc + MaxPos=12
    {'TIER_ALLOC.ultra': 0.22, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.12, 'F3F_PUT_FLOOR': 0.60, 'F3F_CALL_FLOOR': 0.60, 'MAX_POSITIONS': 12},
    # 10. Just put cascade reduction
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.10, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 14},
]


def main():
    print('=' * 100)
    print('PHASE H4 — DD-targeted (V1 anchor: PUT_SL=-0.20, hold=0, EARN=True, calls_first)')
    print('=' * 100)
    print(f'Custom utility: DD_TARGET={DD_TARGET*100:.0f}%, DD_HARD={DD_HARD_LIMIT*100:.0f}%')
    print()
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_h4',
        param_space=PARAM_SPACE,
        base_config=PHASE_H4_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '40')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_h4', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_h4_top_candidates.json')
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
