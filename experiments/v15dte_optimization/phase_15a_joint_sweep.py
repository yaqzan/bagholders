"""Phase 15A — 15 DTE joint Bayesian sweep (TP/SL + cascade alloc + MaxPos).

Anchored on monte_carlo_15dte.py. Phase A purpose: find the {TP, SL,
PUT_TP, PUT_SL, allocation, MaxPos} combo that maximizes 22-now
compound while keeping every-window DD-C <= 80%.

The 15 DTE first-pass MC at H5 strategy params (TP=0.35/SL=-0.30, prod allocs)
showed 22-now Realistic +1.0e15% (vs 30 DTE +4.1e15%, **+25k%** higher) but
breached the DD-C floor on 3 windows (2025=87%, 22-now=87%, 5y=87%).

Hypothesis: H5 wider-TP/tighter-SL was tuned for 30 DTE/15-bar holds. At
15 DTE, the tighter SL fires too fast (less recovery time on intraday
shakeouts), driving correlated drawdown. Per-trade A/B suggests TP=0.30/
SL=-0.35 is closer to the 15 DTE optimum (deeper SL gives recovery room,
narrower TP fires earlier — AvgTPBar 1.3-1.5 bars).

Sweep axes:
- TP_BASE             : {0.25, 0.28, 0.30, 0.33, 0.35}
- SL_BASE             : {-0.30, -0.33, -0.35, -0.38, -0.40}
- PUT_TP              : {0.25, 0.28, 0.30, 0.33}
- PUT_SL              : {-0.18, -0.20, -0.22, -0.25}
- TIER_ALLOC.ultra    : {0.12, 0.15, 0.18, 0.22}
- TIER_ALLOC.top      : {0.10, 0.12, 0.15}
- TIER_ALLOC.mid      : {0.10, 0.12, 0.15}
- TIER_ALLOC.low      : {0.10, 0.12, 0.15}
- PUT_TIER_ALLOC.put_top: {0.08, 0.10, 0.12, 0.15}
- PUT_TIER_ALLOC.put_mid: {0.08, 0.10, 0.12, 0.15}
- PUT_TIER_ALLOC.put_low: {0.08, 0.10, 0.12, 0.15}
- MAX_POSITIONS       : {10, 12, 14, 16}

Locked at 15 DTE defaults:
- HOLD_DAYS=7, PREMIUM_MULT=1.29, HARD_SELL_LOSS=-0.45
- F3f production thresholds, EARN_SUPP_PUT=True, calls_first
- TP_STRESS = TP_BASE + 0.05, SL_STRESS = SL_BASE - 0.05  (paired stressed)
"""
from __future__ import annotations
import json
import os
import sys
import math
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# CRITICAL: import monte_carlo_15dte AS mc — bayes_mc imports `monte_carlo as mc`
# at module level, but we redirect via re-binding before running.
import monte_carlo_15dte as mc15
from experiments import bayes_mc

# Force bayes_mc to use the 15 DTE module everywhere
bayes_mc.mc = mc15

# DD-targeted utility (mirrors phase_h4 pattern)
DD_TARGET = 0.65
DD_HARD_LIMIT = 0.80


def utility_15a(window_results: dict) -> dict:
    per_window_log = {}
    log_sum = 0.0
    max_dd = 0.0
    max_coll = 0.0
    for label, r in window_results.items():
        w = bayes_mc.WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, float(r['mean_ret']))
        lr = math.log1p(ret_pct / 100.0)
        log_sum += w * lr
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


bayes_mc.utility_from_results = utility_15a


def apply_15a_config(cfg: dict):
    """Apply config to monte_carlo_15dte module.
    Mirrors bayes_mc.apply_config but on mc15. Auto-derives stressed values
    from base via +/-0.05 (paired pattern from H5)."""
    m = mc15
    # Call side
    m.TP_BASE     = cfg['TP_BASE']
    m.TP_STRESS   = cfg.get('TP_STRESS', cfg['TP_BASE'] + 0.05)
    m.SL_BASE     = cfg['SL_BASE']
    m.SL_STRESS   = cfg.get('SL_STRESS', cfg['SL_BASE'] - 0.05)
    m.NET_TP_BASE   = m.TP_BASE   + m.SLIP_ENTRY + m.SLIP_TP
    m.NET_TP_STRESS = m.TP_STRESS + m.SLIP_ENTRY + m.SLIP_TP
    m.NET_SL_BASE   = m.SL_BASE   + m.SLIP_ENTRY + m.SLIP_SL
    m.NET_SL_STRESS = m.SL_STRESS + m.SLIP_ENTRY + m.SLIP_SL
    m.TP_SIGMA_BASE   = m.TP_BASE   * m.PREMIUM_MULT / m.DELTA
    m.TP_SIGMA_STRESS = m.TP_STRESS * m.PREMIUM_MULT / m.DELTA
    m.SL_SIGMA_BASE   = abs(m.SL_BASE)   * m.PREMIUM_MULT / m.DELTA
    m.SL_SIGMA_STRESS = abs(m.SL_STRESS) * m.PREMIUM_MULT / m.DELTA

    # Put side
    m.PUT_TP = cfg['PUT_TP']
    m.PUT_SL = cfg['PUT_SL']
    m.PUT_TP_STRESS = cfg.get('PUT_TP_STRESS', cfg['PUT_TP'])
    m.PUT_SL_STRESS = cfg.get('PUT_SL_STRESS', cfg['PUT_SL'])
    m.PUT_NET_TP = m.PUT_TP + m.SLIP_ENTRY + m.SLIP_TP
    m.PUT_NET_SL = m.PUT_SL + m.SLIP_ENTRY + m.SLIP_SL
    m.PUT_TP_SIGMA = m.PUT_TP * m.PREMIUM_MULT / m.DELTA
    m.PUT_SL_SIGMA = abs(m.PUT_SL) * m.PREMIUM_MULT / m.DELTA

    # Locks: hold=0, calls_first, EARN=on
    m.PUT_SL_HOLD_BARS_DEFAULT = 0
    m.PUT_SL_HOLD_BARS_MONDAY = 0
    m.PUT_PRIORITY = 'calls_first'
    m.EARN_SUPP_PUT = True
    m.PUT_BREADTH_MODE = 'none'

    # Cascade allocations — _full_config nests dotted keys, so read from cfg['TIER_ALLOC'] dict.
    # Also support flat dotted keys for backward compatibility.
    ta = dict(m.TIER_ALLOC)
    cfg_ta = cfg.get('TIER_ALLOC', {}) if isinstance(cfg.get('TIER_ALLOC'), dict) else {}
    ta['ultra'] = cfg.get('TIER_ALLOC.ultra', cfg_ta.get('ultra', ta.get('ultra', 0.18)))
    ta['top']   = cfg.get('TIER_ALLOC.top',   cfg_ta.get('top',   ta.get('top',   0.12)))
    ta['mid']   = cfg.get('TIER_ALLOC.mid',   cfg_ta.get('mid',   ta.get('mid',   0.15)))
    ta['low']   = cfg.get('TIER_ALLOC.low',   cfg_ta.get('low',   ta.get('low',   0.15)))
    ta['overflow'] = 0.00
    m.TIER_ALLOC = ta

    pta = dict(m.PUT_TIER_ALLOC)
    cfg_pta = cfg.get('PUT_TIER_ALLOC', {}) if isinstance(cfg.get('PUT_TIER_ALLOC'), dict) else {}
    pta['put_top'] = cfg.get('PUT_TIER_ALLOC.put_top', cfg_pta.get('put_top', pta.get('put_top', 0.10)))
    pta['put_mid'] = cfg.get('PUT_TIER_ALLOC.put_mid', cfg_pta.get('put_mid', pta.get('put_mid', 0.12)))
    pta['put_low'] = cfg.get('PUT_TIER_ALLOC.put_low', cfg_pta.get('put_low', pta.get('put_low', 0.12)))
    m.PUT_TIER_ALLOC = pta

    # F3f thresholds (locked at 15 DTE defaults from H4)
    m.F3F_CALL_FLOOR = 0.50
    m.F3F_PUT_FLOOR  = 0.50

    # Capacity
    m.MAX_POSITIONS = cfg['MAX_POSITIONS']

    # Sweep knobs
    m.N_ITER = cfg['N_ITER']
    m.COLLISION_MODES = list(cfg['COLLISION_MODES'])


SCREENING_WINDOWS = [
    ('22-now', date(2022, 1, 1), date(2026, 4, 15)),
]
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}


def run_config_15a(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    apply_15a_config(cfg2)
    if version is None:
        from database.models.core import AlgorithmVersion
        version = AlgorithmVersion.get_active_scores_version()
    out = {}
    for label, d_start, d_end in windows:
        if silent:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                window_res = mc15.run_window(label, d_start, d_end, version)
        else:
            window_res = mc15.run_window(label, d_start, d_end, version)
        real = window_res.get('realistic', {})
        cons = window_res.get('conservative', {})
        opt  = window_res.get('optimistic', {})
        max_dd = max(float(real.get('worst_dd', 0)),
                     float(cons.get('worst_dd', 0)),
                     float(opt.get('worst_dd', 0)))
        max_coll = max(float(real.get('p_coll', 0)),
                       float(cons.get('p_coll', 0)),
                       float(opt.get('p_coll', 0)))
        out[label] = {
            'mean_ret': float(real.get('mean_ret', 0)),
            'med_ret':  float(real.get('med_ret', 0)),
            'worst_dd': max_dd,  # max across modes (DD-C floor)
            'mean_dd':  float(real.get('mean_dd', 0)),
            'p_collapse': max_coll,
            'call_tp':  float(real.get('call_tp', 0)),
            'put_tp':   float(real.get('put_tp', 0)),
            'call_trades': float(real.get('call_trades', 0)),
            'put_trades':  float(real.get('put_trades', 0)),
            'cons_dd':  float(cons.get('worst_dd', 0)),
            'real_dd':  float(real.get('worst_dd', 0)),
        }
    return out


bayes_mc.apply_config = apply_15a_config
bayes_mc.run_config = run_config_15a


# Base config (15 DTE current production-equivalent)
BASE_CONFIG = {
    'TP_BASE':            0.35,    # 15 DTE current (= H5 for 30 DTE)
    'SL_BASE':           -0.30,
    'PUT_TP':             0.35,
    'PUT_SL':            -0.20,
    'TIER_ALLOC':         {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
    'PUT_TIER_ALLOC':     {'put_top': 0.10, 'put_mid': 0.12, 'put_low': 0.12},
    'MAX_POSITIONS':      14,
    'N_ITER':             75,        # was 100 — faster eval
    'COLLISION_MODES':    ['conservative', 'realistic', 'optimistic'],
}


# Tighter param space — focus on 6 axes that the per-trade A/B + first MC suggest
# matter most. Allocation depth (mid/low individually + put_mid/low) less critical
# than ultra/top split and put_top concentration.
PARAM_SPACE = {
    'TP_BASE':                  [0.28, 0.30, 0.33, 0.35],
    'SL_BASE':                  [-0.30, -0.33, -0.35, -0.38, -0.40],
    'PUT_TP':                   [0.28, 0.30, 0.33, 0.35],
    'PUT_SL':                   [-0.20, -0.22, -0.25],
    'TIER_ALLOC.ultra':         [0.12, 0.15, 0.18, 0.22],
    'PUT_TIER_ALLOC.put_top':   [0.08, 0.10, 0.12, 0.15],
    'MAX_POSITIONS':            [10, 12, 14],
}

# Strategic seeds — 6 most informative directions
SEED_CONFIGS = [
    # 1. Current 15 DTE (= H5 for 30 DTE) — anchor
    {'TP_BASE': 0.35, 'SL_BASE': -0.30, 'PUT_TP': 0.35, 'PUT_SL': -0.20,
     'TIER_ALLOC.ultra': 0.18, 'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14},
    # 2. Per-trade A/B baseline (TP=0.30, SL=-0.35) — main hypothesis
    {'TP_BASE': 0.30, 'SL_BASE': -0.35, 'PUT_TP': 0.30, 'PUT_SL': -0.20,
     'TIER_ALLOC.ultra': 0.18, 'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14},
    # 3. Wider SL — DD-targeted
    {'TP_BASE': 0.30, 'SL_BASE': -0.40, 'PUT_TP': 0.30, 'PUT_SL': -0.25,
     'TIER_ALLOC.ultra': 0.18, 'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14},
    # 4. Per-trade A/B + smaller MaxPos
    {'TP_BASE': 0.30, 'SL_BASE': -0.35, 'PUT_TP': 0.30, 'PUT_SL': -0.20,
     'TIER_ALLOC.ultra': 0.18, 'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 10},
    # 5. Per-trade A/B + smaller ultra
    {'TP_BASE': 0.30, 'SL_BASE': -0.35, 'PUT_TP': 0.30, 'PUT_SL': -0.20,
     'TIER_ALLOC.ultra': 0.12, 'PUT_TIER_ALLOC.put_top': 0.08, 'MAX_POSITIONS': 14},
    # 6. Concentrated ultra (top tier carries 95+ alpha at 79% TP)
    {'TP_BASE': 0.30, 'SL_BASE': -0.35, 'PUT_TP': 0.30, 'PUT_SL': -0.20,
     'TIER_ALLOC.ultra': 0.22, 'PUT_TIER_ALLOC.put_top': 0.15, 'MAX_POSITIONS': 14},
]


def main():
    print('=' * 100)
    print('PHASE 15A — 15 DTE joint sweep (TP/SL + cascade alloc + MaxPos)')
    print('=' * 100)
    print(f'Anchor : monte_carlo_15dte.py (HOLD=7, PREMIUM_MULT=1.29, HARD=-0.45)')
    print(f'Utility: DD_TARGET={DD_TARGET*100:.0f}%, DD_HARD={DD_HARD_LIMIT*100:.0f}%')
    print(f'Window : 22-now × N=100 × 3 modes (screening)')
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v15dte_phase_15a',
        param_space=PARAM_SPACE,
        base_config=BASE_CONFIG,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '50')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v15dte_phase_15a', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_15a_top_candidates.json')
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
