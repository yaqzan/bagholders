"""Phase H5 — loss-limiting axes (hard sell, hold days, TP).

After H4 finds the best alloc/MaxPos/F3f combo, H5 sweeps the loss-limiting
levers that aren't allocation-related:
- HOLD_DAYS (max hold before forced exit): 10, 12, 13, 15
- HARD_SELL_LOSS: -0.45, -0.50, -0.55 (option loss at hard sell)
- PUT_TP: 0.30, 0.35 (wider TP captures more MFE on rare big winners)
- CALL_TP_BASE: 0.30, 0.35
- CALL_SL_BASE: -0.30, -0.35 (call SL — same MAE-anchoring opportunity as puts)

Anchored at H4 winner (which sets allocs/MaxPos/F3f).
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


def utility_from_results_h5(window_results: dict) -> dict:
    log_sum = 0.0
    max_dd = 0.0
    max_coll = 0.0
    for label, r in window_results.items():
        w = bayes_mc.WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, float(r['mean_ret']))
        log_sum += w * math.log1p(ret_pct / 100.0)
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
        'max_dd': max_dd, 'max_collapse': max_coll, 'per_window_log': {},
    }


bayes_mc.utility_from_results = utility_from_results_h5


def _load_h4_winner():
    p = os.path.join(os.path.dirname(__file__), 'phase_h4_top_candidates.json')
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        data = json.load(f)
    if not data:
        return {}
    return data[0]['params']


_H4_WIN = _load_h4_winner()


def extended_apply_config_h5(cfg: dict):
    _orig_apply(cfg)
    # V1 + H4 winner locks
    mc.PUT_SL_MODE = 'static'
    mc.PUT_SL = -0.20
    mc.PUT_NET_SL = -0.20 + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.PUT_SL_SIGMA = 0.20 * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_SL_HOLD_BARS_DEFAULT = 0
    mc.PUT_SL_HOLD_BARS_MONDAY = 0
    mc.PUT_PRIORITY = 'calls_first'
    mc.EARN_SUPP_PUT = True

    # H4 winner allocs/F3f/MaxPos (loaded at module load; override if present in cfg)
    if _H4_WIN:
        ta = dict(mc.TIER_ALLOC)
        ta['ultra'] = _H4_WIN.get('TIER_ALLOC.ultra', ta.get('ultra', 0.25))
        ta['top']   = _H4_WIN.get('TIER_ALLOC.top',   ta.get('top',   0.15))
        ta['mid']   = _H4_WIN.get('TIER_ALLOC.mid',   ta.get('mid',   0.15))
        mc.TIER_ALLOC = ta
        pta = dict(mc.PUT_TIER_ALLOC)
        pta['put_top'] = _H4_WIN.get('PUT_TIER_ALLOC.put_top', pta.get('put_top', 0.15))
        mc.PUT_TIER_ALLOC = pta
        if 'F3F_PUT_FLOOR' in _H4_WIN:  mc.F3F_PUT_FLOOR  = _H4_WIN['F3F_PUT_FLOOR']
        if 'F3F_CALL_FLOOR' in _H4_WIN: mc.F3F_CALL_FLOOR = _H4_WIN['F3F_CALL_FLOOR']
        if 'MAX_POSITIONS' in _H4_WIN:  mc.MAX_POSITIONS  = _H4_WIN['MAX_POSITIONS']

    # H5 sweep axes
    if 'HOLD_DAYS' in cfg:        mc.HOLD_DAYS = cfg['HOLD_DAYS']
    if 'HARD_SELL_LOSS' in cfg:
        mc.HARD_SELL_LOSS = cfg['HARD_SELL_LOSS']
        mc.NET_HARD_SELL = cfg['HARD_SELL_LOSS'] + mc.SLIP_ENTRY + mc.SLIP_HARD
    if 'PUT_TP' in cfg:
        mc.PUT_TP = cfg['PUT_TP']
        mc.PUT_NET_TP = cfg['PUT_TP'] + mc.SLIP_ENTRY + mc.SLIP_TP
        mc.PUT_TP_SIGMA = cfg['PUT_TP'] * mc.PREMIUM_MULT / mc.DELTA
    if 'CALL_TP_BASE' in cfg:
        mc.TP_BASE = cfg['CALL_TP_BASE']
        mc.NET_TP_BASE = cfg['CALL_TP_BASE'] + mc.SLIP_ENTRY + mc.SLIP_TP
        mc.TP_SIGMA_BASE = cfg['CALL_TP_BASE'] * mc.PREMIUM_MULT / mc.DELTA
    if 'CALL_SL_BASE' in cfg:
        mc.SL_BASE = cfg['CALL_SL_BASE']
        mc.NET_SL_BASE = cfg['CALL_SL_BASE'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.SL_SIGMA_BASE = abs(cfg['CALL_SL_BASE']) * mc.PREMIUM_MULT / mc.DELTA


def run_config_3mode_h5(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_h5(cfg2)
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


bayes_mc.apply_config = extended_apply_config_h5
bayes_mc.run_config = run_config_3mode_h5
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}
import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_h5


PHASE_H5_BASE = dict(BASE_CONFIG)
PHASE_H5_BASE['N_ITER'] = 100
PHASE_H5_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
PHASE_H5_BASE['HOLD_DAYS'] = 15
PHASE_H5_BASE['HARD_SELL_LOSS'] = -0.50
PHASE_H5_BASE['PUT_TP'] = 0.30
PHASE_H5_BASE['CALL_TP_BASE'] = 0.30
PHASE_H5_BASE['CALL_SL_BASE'] = -0.35


PARAM_SPACE = {
    'HOLD_DAYS':       [10, 12, 13, 15],
    'HARD_SELL_LOSS':  [-0.55, -0.50, -0.45, -0.40],
    'PUT_TP':          [0.30, 0.35],
    'CALL_TP_BASE':    [0.30, 0.35],
    'CALL_SL_BASE':    [-0.30, -0.35],
}

SEED_CONFIGS = [
    # 1. H4 winner + production-default loss-limiting params (anchor)
    {'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.50, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.35},
    # 2. Earlier hard sell at day 12
    {'HOLD_DAYS': 12, 'HARD_SELL_LOSS': -0.50, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.35},
    # 3. Even earlier hard sell at day 10
    {'HOLD_DAYS': 10, 'HARD_SELL_LOSS': -0.50, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.35},
    # 4. Tighter HARD_SELL (quicker exit at midway)
    {'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.40, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.35},
    # 5. Wider PUT_TP (catch bigger winners)
    {'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.50, 'PUT_TP': 0.35, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.35},
    # 6. Wider CALL_TP (same idea)
    {'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.50, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.35, 'CALL_SL_BASE': -0.35},
    # 7. Tighter CALL_SL (MAE-anchored — call MAEw_15d=-0.727σ, -0.30 ≈ 1.5×)
    {'HOLD_DAYS': 15, 'HARD_SELL_LOSS': -0.50, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.30},
    # 8. Combined: shorter hold + tighter SL
    {'HOLD_DAYS': 12, 'HARD_SELL_LOSS': -0.45, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.30},
    # 9. Aggressive loss-limit
    {'HOLD_DAYS': 10, 'HARD_SELL_LOSS': -0.40, 'PUT_TP': 0.30, 'CALL_TP_BASE': 0.30, 'CALL_SL_BASE': -0.30},
    # 10. Wider TPs both sides + shorter hold
    {'HOLD_DAYS': 12, 'HARD_SELL_LOSS': -0.50, 'PUT_TP': 0.35, 'CALL_TP_BASE': 0.35, 'CALL_SL_BASE': -0.30},
]


def main():
    print('=' * 100)
    print('PHASE H5 — loss-limiting axes (hard sell, hold days, TP)')
    print('=' * 100)
    if _H4_WIN:
        print(f'H4 winner locked: {_H4_WIN}')
    else:
        print('No H4 winner found — using V1 baseline locks')
    print()
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)}')

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_h5',
        param_space=PARAM_SPACE,
        base_config=PHASE_H5_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '30')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_h5', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_h5_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            payload.append({
                'rank': len(payload) + 1, 'utility': r['utility'],
                'params': r['params'], 'windows': r['windows'],
                'log_return': r['util_info']['log_return'],
                'max_dd': r['util_info']['max_dd'],
                'max_collapse': r['util_info']['max_collapse'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 saved to {out_path}')


if __name__ == '__main__':
    main()
