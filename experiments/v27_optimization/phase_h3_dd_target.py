"""Phase H3 — DD-targeted alloc + breadth + MaxPos sweep.

Goal: drive 22-now DD-C from current ~80% down toward 65-70% (v25-class)
while keeping compound return positive. Modifies utility to penalize DD
starting at 0.65 instead of 0.80 — pushes Bayesian toward LOW-DD configs.

Locks: PUT_SL=-0.15, hold=0 (H1 rank 2 — lowest DD at 79.4%), calls_first,
EARN_SUPP_PUT=False, F3f production thresholds (will be swept).

Sweeps (DD reduction levers):
- TIER_ALLOC.ultra: {0.15, 0.20, 0.25} — currently 0.25
- TIER_ALLOC.top/mid/low: {0.10, 0.12, 0.15} — currently 0.15
- PUT_TIER_ALLOC.put_top: {0.10, 0.12, 0.15} — currently 0.15
- F3F_PUT_FLOOR: {0.50, 0.65, 0.75} — currently 0.75 (lower = more cut)
- F3F_CALL_FLOOR: {0.50, 0.65, 0.70} — currently 0.70
- MAX_POSITIONS: {10, 12, 14, 16}
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

# ---- Custom utility: DD penalty starting at 0.65 ----
DD_TARGET = 0.65   # below this no penalty
DD_HARD_LIMIT = 0.80   # above this very heavy penalty


def utility_from_results_dd_targeted(window_results: dict) -> dict:
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

    # Soft penalty starting at DD_TARGET (0.65), quadratic
    soft_excess = max(0.0, max_dd - DD_TARGET)
    soft_penalty = 30.0 * (soft_excess ** 2) / 0.01    # 0.65→0, 0.70→7.5, 0.75→30, 0.80→67.5

    # Hard penalty above DD_HARD_LIMIT (0.80) — extra steep
    hard_excess = max(0.0, max_dd - DD_HARD_LIMIT)
    hard_penalty = 200.0 * (hard_excess ** 2) / 0.01

    collapse_penalty = 1000.0 * max_coll  # double the collapse penalty

    utility = log_sum - soft_penalty - hard_penalty - collapse_penalty

    return {
        'utility': utility,
        'log_return': log_sum,
        'log_return_weighted_sum': log_sum,
        'dd_penalty': soft_penalty + hard_penalty,
        'collapse_penalty': collapse_penalty,
        'max_dd': max_dd,
        'max_collapse': max_coll,
        'per_window_log': per_window_log,
    }


bayes_mc.utility_from_results = utility_from_results_dd_targeted


def extended_apply_config_h3(cfg: dict):
    _orig_apply(cfg)
    # H1 winner locks
    mc.PUT_SL_MODE = 'static'
    mc.PUT_SL = -0.15
    mc.PUT_NET_SL = -0.15 + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.PUT_SL_SIGMA = 0.15 * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_SL_HOLD_BARS_DEFAULT = 0
    mc.PUT_SL_HOLD_BARS_MONDAY = 0
    mc.PUT_PRIORITY = 'calls_first'

    # F3f
    if 'F3F_PUT_FLOOR' in cfg:   mc.F3F_PUT_FLOOR = cfg['F3F_PUT_FLOOR']
    if 'F3F_CALL_FLOOR' in cfg:  mc.F3F_CALL_FLOOR = cfg['F3F_CALL_FLOOR']
    if 'F3F_PUT_THRESH' in cfg:  mc.F3F_PUT_THRESH = cfg['F3F_PUT_THRESH']
    if 'F3F_CALL_THRESH' in cfg: mc.F3F_CALL_THRESH = cfg['F3F_CALL_THRESH']

    # Cascade depths (call)
    if 'TIER_ALLOC.ultra' in cfg or 'TIER_ALLOC.top' in cfg or 'TIER_ALLOC.mid' in cfg or 'TIER_ALLOC.low' in cfg:
        ta = dict(mc.TIER_ALLOC)
        ta['ultra'] = cfg.get('TIER_ALLOC.ultra', ta.get('ultra', 0.25))
        ta['top']   = cfg.get('TIER_ALLOC.top',   ta.get('top',   0.15))
        ta['mid']   = cfg.get('TIER_ALLOC.mid',   ta.get('mid',   0.15))
        ta['low']   = cfg.get('TIER_ALLOC.low',   ta.get('low',   0.15))
        mc.TIER_ALLOC = ta

    # Put cascade
    if 'PUT_TIER_ALLOC.put_top' in cfg or 'PUT_TIER_ALLOC.put_mid' in cfg or 'PUT_TIER_ALLOC.put_low' in cfg:
        pta = dict(mc.PUT_TIER_ALLOC)
        pta['put_top'] = cfg.get('PUT_TIER_ALLOC.put_top', pta.get('put_top', 0.15))
        pta['put_mid'] = cfg.get('PUT_TIER_ALLOC.put_mid', pta.get('put_mid', 0.12))
        pta['put_low'] = cfg.get('PUT_TIER_ALLOC.put_low', pta.get('put_low', 0.12))
        mc.PUT_TIER_ALLOC = pta


def run_config_3mode_h3(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_h3(cfg2)
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


bayes_mc.apply_config = extended_apply_config_h3
bayes_mc.run_config = run_config_3mode_h3
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_h3


PHASE_H3_BASE = dict(BASE_CONFIG)
PHASE_H3_BASE['PUT_TP'] = 0.30
PHASE_H3_BASE['EARN_SUPP_PUT'] = False
PHASE_H3_BASE['MAX_POSITIONS'] = 16
PHASE_H3_BASE['N_ITER'] = 100
PHASE_H3_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
PHASE_H3_BASE['PUT_BREADTH_MODE'] = 'none'
PHASE_H3_BASE['TIER_ALLOC'] = {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_H3_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_H3_BASE['F3F_PUT_FLOOR'] = 0.75
PHASE_H3_BASE['F3F_CALL_FLOOR'] = 0.70
PHASE_H3_BASE['F3F_PUT_THRESH'] = 75
PHASE_H3_BASE['F3F_CALL_THRESH'] = 50


PARAM_SPACE = {
    'TIER_ALLOC.ultra':         [0.15, 0.20, 0.25],
    'TIER_ALLOC.top':           [0.10, 0.12, 0.15],
    'TIER_ALLOC.mid':           [0.10, 0.12, 0.15],
    'PUT_TIER_ALLOC.put_top':   [0.10, 0.12, 0.15],
    'F3F_PUT_FLOOR':            [0.50, 0.65, 0.75],
    'F3F_CALL_FLOOR':           [0.50, 0.65, 0.70],
    'MAX_POSITIONS':            [10, 12, 14, 16],
}

# Strategic seeds: production baseline + DD-reduction probes
SEED_CONFIGS = [
    # 1. Production-equivalent (with H1 SL fix locked)
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 16},
    # 2. Smaller allocs (linear DD reduction)
    {'TIER_ALLOC.ultra': 0.20, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.12, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 16},
    # 3. Conservative allocs
    {'TIER_ALLOC.ultra': 0.15, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.10, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 16},
    # 4. Tighter F3f (more aggressive cut in stress)
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50, 'MAX_POSITIONS': 16},
    # 5. Smaller MaxPos (less correlated DD)
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 12},
    # 6. Smaller MaxPos + smaller allocs
    {'TIER_ALLOC.ultra': 0.20, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.12, 'F3F_PUT_FLOOR': 0.65, 'F3F_CALL_FLOOR': 0.65, 'MAX_POSITIONS': 12},
    # 7. Aggressive DD reduction
    {'TIER_ALLOC.ultra': 0.15, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.10, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50, 'MAX_POSITIONS': 10},
    # 8. Mid-aggressive
    {'TIER_ALLOC.ultra': 0.20, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'F3F_PUT_FLOOR': 0.65, 'F3F_CALL_FLOOR': 0.65, 'MAX_POSITIONS': 14},
    # 9. Heavy F3f only (alloc unchanged)
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.50, 'F3F_CALL_FLOOR': 0.50, 'MAX_POSITIONS': 14},
    # 10. Heavy on MaxPos cut only
    {'TIER_ALLOC.ultra': 0.25, 'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.15, 'F3F_PUT_FLOOR': 0.75, 'F3F_CALL_FLOOR': 0.70, 'MAX_POSITIONS': 10},
]


def main():
    print('=' * 100)
    print('PHASE H3 — DD-targeted alloc + breadth + MaxPos sweep')
    print('=' * 100)
    print(f'Custom utility: DD penalty starts at {DD_TARGET*100:.0f}%, hard penalty above {DD_HARD_LIMIT*100:.0f}%')
    print(f'Locked: PUT_SL=-0.15, hold=0, calls_first (H1 rank 2 — lowest DD candidate)')
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
        phase_name='v27_phase_h3',
        param_space=PARAM_SPACE,
        base_config=PHASE_H3_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '40')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_h3', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_h3_top_candidates.json')
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
