"""Phase EVS-IV-DD V3 — DD circuit breaker sweep.

V1/V2 sweeps showed allocation/MaxPos/HOLD/SL alone cannot bring 22-now
DD-C below 80% under stochastic IV crush.  Smoke test with
DD_CIRCUIT_BREAKER=0.50 (mirrors the 15 DTE strategy mechanism) brought
DD-C from 90% to 64.5% — the missing structural lever.

V3 sweeps the breaker threshold + companion params to find the highest-
return config that holds DD-C <= 80% on every window at N=200 screening.

Anchor: V1 winner params (best from V1 sweep).
Sweep:
  DD_CIRCUIT_BREAKER: {0.55, 0.60, 0.65, 0.70}
  TIER_ALLOC.ultra:   {0.10, 0.12, 0.15}    (slightly looser when breaker is on)
  MAX_POSITIONS:      {10, 12, 14}
  HARD_SELL_LOSS:     {-0.30, -0.35, -0.40}
"""
from __future__ import annotations
import os, sys, json, math
from datetime import date

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
from database.models.core import AlgorithmVersion

V29 = AlgorithmVersion.get(AlgorithmVersion.git_commit == '8473cba')
print(f"Pinned to v29 (id={V29.id})", flush=True)

SCREENING_WINDOWS = [('22-now', date(2022, 1, 1), date(2026, 4, 15))]
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

DD_TARGET = 0.65
DD_HARD_LIMIT = 0.80


def utility_from_results_dd(window_results: dict) -> dict:
    log_sum = 0.0; max_dd = 0.0; max_coll = 0.0; per = {}
    for label, r in window_results.items():
        w = bayes_mc.WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, float(r['mean_ret']))
        lr = math.log1p(ret_pct / 100.0)
        log_sum += w * lr; per[label] = lr
        max_dd = max(max_dd, float(r['worst_dd']) / 100.0)
        max_coll = max(max_coll, float(r['p_collapse']) / 100.0)
    soft = 30.0 * (max(0.0, max_dd - DD_TARGET) ** 2) / 0.01
    hard = 200.0 * (max(0.0, max_dd - DD_HARD_LIMIT) ** 2) / 0.01
    coll_pen = 1000.0 * max_coll
    return {
        'utility': log_sum - soft - hard - coll_pen, 'log_return': log_sum,
        'log_return_weighted_sum': log_sum, 'dd_penalty': soft + hard,
        'collapse_penalty': coll_pen, 'max_dd': max_dd, 'max_collapse': max_coll,
        'per_window_log': per,
    }


bayes_mc.utility_from_results = utility_from_results_dd


def extended_apply_config_v3(cfg: dict):
    _orig_apply(cfg)
    mc.PUT_SL_MODE = 'static'
    mc.PUT_SL = -0.20
    mc.PUT_NET_SL = -0.20 + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.PUT_SL_SIGMA = 0.20 * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_SL_HOLD_BARS_DEFAULT = 0
    mc.PUT_SL_HOLD_BARS_MONDAY = 0
    mc.PUT_PRIORITY = 'calls_first'
    mc.EARN_SUPP_PUT = True

    if 'DD_CIRCUIT_BREAKER' in cfg:
        mc.DD_CIRCUIT_BREAKER = cfg['DD_CIRCUIT_BREAKER']
    if 'F3F_PUT_FLOOR' in cfg:   mc.F3F_PUT_FLOOR = cfg['F3F_PUT_FLOOR']
    if 'F3F_CALL_FLOOR' in cfg:  mc.F3F_CALL_FLOOR = cfg['F3F_CALL_FLOOR']

    if any(k in cfg for k in ('TIER_ALLOC.ultra', 'TIER_ALLOC.top', 'TIER_ALLOC.mid', 'TIER_ALLOC.low')):
        ta = dict(mc.TIER_ALLOC)
        ta['ultra'] = cfg.get('TIER_ALLOC.ultra', ta.get('ultra', 0.12))
        ta['top']   = cfg.get('TIER_ALLOC.top',   ta.get('top',   0.10))
        ta['mid']   = cfg.get('TIER_ALLOC.mid',   ta.get('mid',   0.12))
        ta['low']   = cfg.get('TIER_ALLOC.low',   ta.get('low',   0.15))
        mc.TIER_ALLOC = ta

    if 'PUT_TIER_ALLOC.put_top' in cfg:
        pta = dict(mc.PUT_TIER_ALLOC)
        pta['put_top'] = cfg['PUT_TIER_ALLOC.put_top']
        pta['put_mid'] = cfg.get('PUT_TIER_ALLOC.put_mid', pta.get('put_mid', 0.10))
        pta['put_low'] = cfg.get('PUT_TIER_ALLOC.put_low', pta.get('put_low', 0.10))
        mc.PUT_TIER_ALLOC = pta

    if 'HARD_SELL_LOSS' in cfg:
        mc.HARD_SELL_LOSS = cfg['HARD_SELL_LOSS']
        mc.NET_HARD_SELL = mc.HARD_SELL_LOSS + mc.SLIP_ENTRY + mc.SLIP_HARD


def run_config_3mode_v3(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg); cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_v3(cfg2)
    version = V29
    out = {}
    for label, d_start, d_end in windows:
        if silent:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                wr = mc.run_window(label, d_start, d_end, version)
        else:
            wr = mc.run_window(label, d_start, d_end, version)
        real = wr.get('realistic', {}); cons = wr.get('conservative', {}); opt = wr.get('optimistic', {})
        max_dd = max(float(real.get('worst_dd', 0)), float(cons.get('worst_dd', 0)), float(opt.get('worst_dd', 0)))
        max_coll = max(float(real.get('p_coll', 0)), float(cons.get('p_coll', 0)), float(opt.get('p_coll', 0)))
        out[label] = {
            'mean_ret': float(real.get('mean_ret', 0)),
            'med_ret': float(real.get('med_ret', 0)),
            'worst_dd': max_dd, 'mean_dd': float(real.get('mean_dd', 0)),
            'p_collapse': max_coll,
            'call_tp': float(real.get('call_tp', 0)),
            'put_tp': float(real.get('put_tp', 0)),
            'call_trades': float(real.get('call_trades', 0)),
            'put_trades': float(real.get('put_trades', 0)),
            'cons_dd': float(cons.get('worst_dd', 0)),
            'real_dd': float(real.get('worst_dd', 0)),
        }
    return out


bayes_mc.apply_config = extended_apply_config_v3
bayes_mc.run_config = run_config_3mode_v3
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_v3

BASE = dict(BASE_CONFIG)
BASE['PUT_TP'] = 0.35
BASE['EARN_SUPP_PUT'] = True
BASE['MAX_POSITIONS'] = 14
BASE['N_ITER'] = 200
BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
BASE['PUT_BREADTH_MODE'] = 'none'
BASE['TIER_ALLOC']     = {'ultra': 0.12, 'top': 0.10, 'mid': 0.12, 'low': 0.15, 'overflow': 0.00}
BASE['PUT_TIER_ALLOC'] = {'put_top': 0.10, 'put_mid': 0.10, 'put_low': 0.12}
BASE['F3F_CALL_FLOOR'] = 0.50
BASE['F3F_PUT_FLOOR']  = 0.50
BASE['HARD_SELL_LOSS'] = -0.40
BASE['TP_BASE']  = 0.35
BASE['SL_BASE']  = -0.30
BASE['HOLD_DAYS'] = 15

PARAM_SPACE = {
    'DD_CIRCUIT_BREAKER': [0.55, 0.60, 0.65, 0.70, 0.75],
    'TIER_ALLOC.ultra':   [0.10, 0.12, 0.15, 0.18],
    'TIER_ALLOC.top':     [0.10, 0.12, 0.15],
    'PUT_TIER_ALLOC.put_top': [0.08, 0.10, 0.12],
    'MAX_POSITIONS':      [10, 12, 14],
    'HARD_SELL_LOSS':     [-0.30, -0.35, -0.40],
}

SEED_CONFIGS = [
    # 1. Breaker=0.65 + H5-ish anchor
    {'DD_CIRCUIT_BREAKER': 0.65, 'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14, 'HARD_SELL_LOSS': -0.40},
    # 2. Breaker=0.60 + H5
    {'DD_CIRCUIT_BREAKER': 0.60, 'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14, 'HARD_SELL_LOSS': -0.40},
    # 3. Breaker=0.70 + H5
    {'DD_CIRCUIT_BREAKER': 0.70, 'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14, 'HARD_SELL_LOSS': -0.40},
    # 4. Breaker=0.55 + H5
    {'DD_CIRCUIT_BREAKER': 0.55, 'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14, 'HARD_SELL_LOSS': -0.40},
    # 5. Breaker=0.65 + tighter cascade
    {'DD_CIRCUIT_BREAKER': 0.65, 'TIER_ALLOC.ultra': 0.12, 'TIER_ALLOC.top': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.08, 'MAX_POSITIONS': 12, 'HARD_SELL_LOSS': -0.35},
    # 6. Breaker=0.60 + tighter
    {'DD_CIRCUIT_BREAKER': 0.60, 'TIER_ALLOC.ultra': 0.12, 'TIER_ALLOC.top': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.08, 'MAX_POSITIONS': 12, 'HARD_SELL_LOSS': -0.35},
    # 7. Breaker=0.70 + tighter
    {'DD_CIRCUIT_BREAKER': 0.70, 'TIER_ALLOC.ultra': 0.12, 'TIER_ALLOC.top': 0.10,
     'PUT_TIER_ALLOC.put_top': 0.08, 'MAX_POSITIONS': 12, 'HARD_SELL_LOSS': -0.35},
    # 8. Breaker=0.75 (mild) + H5
    {'DD_CIRCUIT_BREAKER': 0.75, 'TIER_ALLOC.ultra': 0.18, 'TIER_ALLOC.top': 0.12,
     'PUT_TIER_ALLOC.put_top': 0.10, 'MAX_POSITIONS': 14, 'HARD_SELL_LOSS': -0.40},
]


def main():
    print('=' * 100)
    print('PHASE EVS-IV-DD V3 — DD circuit breaker sweep')
    print('=' * 100)
    print(f'IV stochastic | Pinned to v29 | Screening N=200')
    print(f'Param space ({len(PARAM_SPACE)} axes):')
    for k, v in PARAM_SPACE.items(): print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    budget = int(os.environ.get('SWEEP_BUDGET', '40'))
    print(f'Budget: {budget}', flush=True)

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='evs_iv_dd_v3',
        param_space=PARAM_SPACE, base_config=BASE, seeds=SEED_CONFIGS,
        budget=budget, batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )
    best, evaluated = optimizer.run()
    bayes_mc.print_summary('evs_iv_dd_v3', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'evs_iv_dd_v3_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            w = r['windows'].get('22-now', {})
            payload.append({
                'rank': len(payload) + 1, 'utility': r['utility'], 'params': r['params'],
                'mean_ret_22now': w.get('mean_ret'), 'cons_dd_22now': w.get('cons_dd'),
                'real_dd_22now': w.get('real_dd'), 'worst_dd_22now': w.get('worst_dd'),
                'log_return': r['util_info']['log_return'], 'max_dd': r['util_info']['max_dd'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 saved to {out_path}')
    print()
    print(f"{'R':<3} {'Util':>8} {'MeanRet':>15} {'DD-C':>6} {'DD-R':>6}  Params")
    print('-' * 130)
    for i, r in enumerate(ranked[:10]):
        w = r['windows'].get('22-now', {})
        ret = w.get('mean_ret', 0); cdd = w.get('cons_dd', 0); rdd = w.get('real_dd', 0)
        ps = ' '.join(f"{k.split('.')[-1]}={v}" for k, v in r['params'].items())
        print(f"  {i+1:<3} {r['utility']:>+7.2f}  {ret:>+13,.0f}%  {cdd:>5.1f}% {rdd:>5.1f}%  {ps}")


if __name__ == '__main__':
    main()
