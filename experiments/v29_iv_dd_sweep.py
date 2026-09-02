"""Phase EVS-IV-DD — Bayesian sweep on v29 (V6) under stochastic IV crush.

Goal: find a portfolio-stage parameter set that ships V6 with DD-C <= 80% on
every window (the legacy Conservative-mode floor) AND positive return delta
vs v28 baseline.

Anchor: H5 production (current shipped) parameters.
  TIER_ALLOC.ultra=0.18 / top=0.12 / mid=0.15 / low=0.15
  PUT_TIER_ALLOC.put_top=0.10 / put_mid=0.12 / put_low=0.12
  HARD_SELL_LOSS=-0.40, F3F floors 0.50/0.50, MaxPos=14, hold=0

V1 result at N=500 (v29 + stochastic IV) showed 22-now DD-C = 90.0% (breach).
Sweep tries to find a config that pushes 22-now DD-C below 80% without
sacrificing the +1284% / +379% compound advantage on 5y / 22-now.

Sweep axes (highest-leverage DD reducers):
  TIER_ALLOC.ultra:   {0.10, 0.12, 0.15, 0.18}        (smaller = less concentration)
  TIER_ALLOC.top:     {0.08, 0.10, 0.12}
  TIER_ALLOC.mid:     {0.10, 0.12, 0.15}
  PUT_TIER_ALLOC.put_top: {0.08, 0.10, 0.12}
  HARD_SELL_LOSS:     {-0.30, -0.35, -0.40}           (less negative = capped DD)
  MAX_POSITIONS:      {10, 12, 14}                    (less = less correlated DD)
  F3F_CALL_FLOOR:     {0.40, 0.50, 0.60}              (deeper cut in stress)
  F3F_PUT_FLOOR:      {0.40, 0.50, 0.60}

Custom utility:
  - DD_TARGET = 0.65  (soft penalty starts here)
  - DD_HARD = 0.80    (heavy penalty above this — the binding floor)
  - log_return weighted (22-now)
  - 1000x collapse penalty

Usage:
    PYTHONIOENCODING=utf-8 python -u experiments/v29_iv_dd_sweep.py
"""
from __future__ import annotations
import os
import sys
import json
import math
from datetime import date

# CRITICAL: set IV crush env BEFORE any module that reads it imports
os.environ['IV_CRUSH_ENABLED'] = '1'
os.environ['IV_CRUSH_MODE']    = 'stochastic'
os.environ.setdefault('IV_CRUSH_SEED', '42')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments.v27_optimization.joint_sweep import (
    extended_apply_config as _orig_apply,
    BASE_CONFIG,
)
from experiments import bayes_mc

# ── v29 version handle (load once) ─────────────────────────────────────────
from database.models.core import AlgorithmVersion
V29 = AlgorithmVersion.get(AlgorithmVersion.git_commit == '8473cba')
print(f"Pinned to v29 (id={V29.id}, commit={V29.git_commit[:8]})", flush=True)

# ── Screening window ───────────────────────────────────────────────────────
# Single window for fast iteration. Once converged, validate top-3 at
# N=500 × 8 windows.
SCREENING_WINDOWS = [
    ('22-now', date(2022, 1, 1), date(2026, 4, 15)),
]
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

# ── Custom utility (DD-targeted) ───────────────────────────────────────────
DD_TARGET = 0.65
DD_HARD_LIMIT = 0.80


def utility_from_results_dd(window_results: dict) -> dict:
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


bayes_mc.utility_from_results = utility_from_results_dd

# ── Apply config (extends joint_sweep with new sweep axes) ─────────────────
def extended_apply_config_dd(cfg: dict):
    _orig_apply(cfg)
    # Lock the H5 ship defaults that aren't being swept
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
        ta['ultra'] = cfg.get('TIER_ALLOC.ultra', ta.get('ultra', 0.18))
        ta['top']   = cfg.get('TIER_ALLOC.top',   ta.get('top',   0.12))
        ta['mid']   = cfg.get('TIER_ALLOC.mid',   ta.get('mid',   0.15))
        ta['low']   = cfg.get('TIER_ALLOC.low',   ta.get('low',   0.15))
        mc.TIER_ALLOC = ta

    if 'PUT_TIER_ALLOC.put_top' in cfg:
        pta = dict(mc.PUT_TIER_ALLOC)
        pta['put_top'] = cfg['PUT_TIER_ALLOC.put_top']
        pta['put_mid'] = cfg.get('PUT_TIER_ALLOC.put_mid', pta.get('put_mid', 0.12))
        pta['put_low'] = cfg.get('PUT_TIER_ALLOC.put_low', pta.get('put_low', 0.12))
        mc.PUT_TIER_ALLOC = pta

    # NEW axis: HARD_SELL_LOSS
    if 'HARD_SELL_LOSS' in cfg:
        mc.HARD_SELL_LOSS = cfg['HARD_SELL_LOSS']
        mc.NET_HARD_SELL = cfg['HARD_SELL_LOSS'] + mc.SLIP_ENTRY + mc.SLIP_HARD


# ── Run config — pinned to v29 ────────────────────────────────────────────
def run_config_3mode_dd(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_dd(cfg2)
    # Always use v29 (V6) — that's the reason we're here
    version = V29
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
            'cons_ret': float(cons.get('mean_ret', 0)),
            'cons_dd':  float(cons.get('worst_dd', 0)),
            'real_dd':  float(real.get('worst_dd', 0)),
        }
    return out


bayes_mc.apply_config = extended_apply_config_dd
bayes_mc.run_config = run_config_3mode_dd
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_dd

# ── Base config (H5 ship anchor) ───────────────────────────────────────────
BASE = dict(BASE_CONFIG)
BASE['PUT_TP'] = 0.35
BASE['EARN_SUPP_PUT'] = True
BASE['MAX_POSITIONS'] = 14
BASE['N_ITER'] = 100
BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
BASE['PUT_BREADTH_MODE'] = 'none'
# H5 anchor parameters
BASE['TIER_ALLOC'] = {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
BASE['PUT_TIER_ALLOC'] = {'put_top': 0.10, 'put_mid': 0.12, 'put_low': 0.12}
BASE['F3F_CALL_FLOOR'] = 0.50
BASE['F3F_PUT_FLOOR'] = 0.50
BASE['HARD_SELL_LOSS'] = -0.40
BASE['TP_BASE']  = 0.35
BASE['SL_BASE'] = -0.30
BASE['HOLD_DAYS'] = 15

PARAM_SPACE = {
    'TIER_ALLOC.ultra':         [0.10, 0.12, 0.15, 0.18],
    'TIER_ALLOC.top':           [0.08, 0.10, 0.12],
    'TIER_ALLOC.mid':           [0.10, 0.12, 0.15],
    'PUT_TIER_ALLOC.put_top':   [0.08, 0.10, 0.12],
    'HARD_SELL_LOSS':           [-0.30, -0.35, -0.40],
    'MAX_POSITIONS':            [10, 12, 14],
    'F3F_CALL_FLOOR':           [0.40, 0.50, 0.60],
    'F3F_PUT_FLOOR':            [0.40, 0.50, 0.60],
}

# Strategic seeds
SEED_CONFIGS = [
    # 1. H5 EXACT anchor (baseline — V1 essentially)
    {'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.10, 'HARD_SELL_LOSS': -0.40, 'MAX_POSITIONS': 14,
     'F3F_CALL_FLOOR': 0.50, 'F3F_PUT_FLOOR': 0.50},
    # 2. Smaller ULTRA (0.18 -> 0.15)
    {'TIER_ALLOC.ultra': 0.15, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.10, 'HARD_SELL_LOSS': -0.40, 'MAX_POSITIONS': 14,
     'F3F_CALL_FLOOR': 0.50, 'F3F_PUT_FLOOR': 0.50},
    # 3. Smaller ULTRA + smaller MaxPos
    {'TIER_ALLOC.ultra': 0.15, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.08, 'HARD_SELL_LOSS': -0.40, 'MAX_POSITIONS': 12,
     'F3F_CALL_FLOOR': 0.50, 'F3F_PUT_FLOOR': 0.50},
    # 4. HARD_SELL tightened (less catastrophic per trade)
    {'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.10, 'HARD_SELL_LOSS': -0.30, 'MAX_POSITIONS': 14,
     'F3F_CALL_FLOOR': 0.50, 'F3F_PUT_FLOOR': 0.50},
    # 5. Aggressive cuts everywhere
    {'TIER_ALLOC.ultra': 0.12, 'TIER_ALLOC.top': 0.08, 'TIER_ALLOC.mid': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.08, 'HARD_SELL_LOSS': -0.30, 'MAX_POSITIONS': 10,
     'F3F_CALL_FLOOR': 0.40, 'F3F_PUT_FLOOR': 0.40},
    # 6. Mid-cut + F3f floors deeper
    {'TIER_ALLOC.ultra': 0.15, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'HARD_SELL_LOSS': -0.35, 'MAX_POSITIONS': 12,
     'F3F_CALL_FLOOR': 0.40, 'F3F_PUT_FLOOR': 0.40},
    # 7. Smaller put cascade + F3f
    {'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.08, 'HARD_SELL_LOSS': -0.35, 'MAX_POSITIONS': 14,
     'F3F_CALL_FLOOR': 0.40, 'F3F_PUT_FLOOR': 0.40},
    # 8. MaxPos=10 only
    {'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.15,
     'PUT_TIER_ALLOC.put_top': 0.10, 'HARD_SELL_LOSS': -0.40, 'MAX_POSITIONS': 10,
     'F3F_CALL_FLOOR': 0.50, 'F3F_PUT_FLOOR': 0.50},
    # 9. ULTRA=0.10 (very tight)
    {'TIER_ALLOC.ultra': 0.10, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'HARD_SELL_LOSS': -0.35, 'MAX_POSITIONS': 12,
     'F3F_CALL_FLOOR': 0.50, 'F3F_PUT_FLOOR': 0.50},
    # 10. MaxPos=10 + HARD=-0.30 + small ultra
    {'TIER_ALLOC.ultra': 0.12, 'TIER_ALLOC.top': 0.10, 'TIER_ALLOC.mid': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'HARD_SELL_LOSS': -0.30, 'MAX_POSITIONS': 10,
     'F3F_CALL_FLOOR': 0.50, 'F3F_PUT_FLOOR': 0.50},
]


def main():
    print('=' * 100)
    print('PHASE EVS-IV-DD — Bayesian on v29 + stochastic IV crush')
    print('=' * 100)
    print(f'IV_CRUSH_ENABLED={os.environ.get("IV_CRUSH_ENABLED")}  IV_CRUSH_MODE={os.environ.get("IV_CRUSH_MODE")}')
    print(f'Anchor: H5 ship (ULTRA=0.18, top=0.12, mid=0.15, F3f=0.50/0.50, HARD=-0.40)')
    print(f'DD target: <={DD_TARGET*100:.0f}% (soft) | floor: <={DD_HARD_LIMIT*100:.0f}% (hard)')
    print()
    print(f'Param space ({len(PARAM_SPACE)} axes):')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    budget = int(os.environ.get('SWEEP_BUDGET', '50'))
    print(f'Bayesian budget: {budget} evals')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='evs_iv_dd',
        param_space=PARAM_SPACE,
        base_config=BASE,
        seeds=SEED_CONFIGS,
        budget=budget,
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('evs_iv_dd', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'evs_iv_dd_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            w = r['windows'].get('22-now', {})
            payload.append({
                'rank':    len(payload) + 1,
                'utility': r['utility'],
                'params':  r['params'],
                'mean_ret_22now': w.get('mean_ret'),
                'cons_dd_22now':  w.get('cons_dd'),
                'real_dd_22now':  w.get('real_dd'),
                'worst_dd_22now': w.get('worst_dd'),
                'log_return': r['util_info']['log_return'],
                'max_dd':  r['util_info']['max_dd'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 saved to {out_path}')

    # Print top 10 inline
    print()
    print(f"{'Rank':<5} {'Util':>8} {'MeanRet':>15} {'DD-C':>6} {'DD-R':>6}  Params")
    print('-' * 100)
    for i, r in enumerate(ranked[:10]):
        w = r['windows'].get('22-now', {})
        ret = w.get('mean_ret', 0)
        cdd = w.get('cons_dd', 0)
        rdd = w.get('real_dd', 0)
        params_short = ' '.join(f"{k.split('.')[-1]}={v}" for k, v in r['params'].items())
        print(f"  {i+1:<3} {r['utility']:>+7.2f}  {ret:>+13,.0f}%  {cdd:>5.1f}% {rdd:>5.1f}%  {params_short}")


if __name__ == '__main__':
    main()
