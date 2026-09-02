"""Phase Pri-1 — call/put priority refinement using assessment-driven WR tables.

Background: v28 (e3c8678) puts now match or beat calls at comparable conviction
(e.g. <25 puts WR15=73.9% vs 75+ calls WR15=72.9% on 5y). Production is locked
to PUT_PRIORITY='calls_first' which under-weights puts vs their empirical edge.

This phase sweeps a new 'wr_merged' priority mode where signals are ordered by
per-tier empirical priority (from assessment), with axes for the priority
formula, call-side bias, and CT-tag priority. 'calls_first', 'puts_first',
and the existing 'merged' (raw conviction) are included as control arms.

Locks (from production v28 + H5_HOLD15_H40 ship):
  TP_BASE=0.35, TP_STRESS=0.40, SL_BASE=-0.30, SL_STRESS=-0.35
  HARD_SELL_LOSS=-0.40, PUT_TP=0.35, PUT_SL=-0.20
  PUT_SL_HOLD=0, EARN_SUPP_PUT=True, MAX_POSITIONS=14, HOLD_DAYS=15
  TIER_ALLOC: ultra=0.18, top=0.12, mid=0.15, low=0.15, overflow=0
  PUT_TIER_ALLOC: put_top=0.10, put_mid=0.12, put_low=0.12
  F3F_CALL_FLOOR=0.50, F3F_PUT_FLOOR=0.50
  CT_PROMOTE=True

Sweep axes:
  PUT_PRIORITY:   wr_merged | merged | calls_first | puts_first
  WR_FORMULA:     WR15 | WR30 | WR15xIC | WR15xLogN | EV
  CALL_BIAS:      0.94 | 0.97 | 1.00 | 1.03 | 1.06
  WR_FALLBACK:    50.0 | 65.0 | 70.0   (priority for unknown tiers e.g. CT defaults)

Methodology:
  - N=100 screening on 22-now × 3 collision modes
  - Top-3 validated at N=500 × 8 windows × 3 modes
  - Ship gate: 22-now Real >= V0 (calls_first), all DD-C <= 80%,
    no per-window Realistic loss > 25% vs V0
"""
from __future__ import annotations
import json
import math
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
from experiments.v28_optimization.priority_data import (
    compute_tier_stats,
    build_priority_table,
)


DD_TARGET = 0.65
DD_HARD_LIMIT = 0.80


def utility_from_results_pri(window_results: dict) -> dict:
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


bayes_mc.utility_from_results = utility_from_results_pri


# Pre-compute tier stats once (read from hardcoded v28 5y assessment fallback)
_TIER_STATS = compute_tier_stats()


def extended_apply_config_pri(cfg: dict):
    """Apply config + production locks + wr_merged priority injection."""
    _orig_apply(cfg)

    # Production v28 ship locks (must match monte_carlo.py defaults as of 2026-04-28)
    mc.HOLD_DAYS               = 15
    mc.TP_BASE                 = 0.35
    mc.TP_STRESS               = 0.40
    mc.SL_BASE                 = -0.30
    mc.SL_STRESS               = -0.35
    mc.HARD_SELL_LOSS          = -0.40
    mc.NET_TP_BASE             = 0.35 + mc.SLIP_ENTRY + mc.SLIP_TP
    mc.NET_TP_STRESS           = 0.40 + mc.SLIP_ENTRY + mc.SLIP_TP
    mc.NET_SL_BASE             = -0.30 + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.NET_SL_STRESS           = -0.35 + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.NET_HARD_SELL           = -0.40 + mc.SLIP_ENTRY + mc.SLIP_HARD
    mc.TP_SIGMA_BASE           = 0.35 * mc.PREMIUM_MULT / mc.DELTA
    mc.TP_SIGMA_STRESS         = 0.40 * mc.PREMIUM_MULT / mc.DELTA
    mc.SL_SIGMA_BASE           = 0.30 * mc.PREMIUM_MULT / mc.DELTA
    mc.SL_SIGMA_STRESS         = 0.35 * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_TP                  = 0.35
    mc.PUT_SL                  = -0.20
    mc.PUT_NET_TP              = 0.35 + mc.SLIP_ENTRY + mc.SLIP_TP
    mc.PUT_NET_SL              = -0.20 + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.PUT_TP_SIGMA            = 0.35 * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_SL_SIGMA            = 0.20 * mc.PREMIUM_MULT / mc.DELTA
    mc.PUT_SL_HOLD_BARS_DEFAULT = 0
    mc.PUT_SL_HOLD_BARS_MONDAY  = 0
    mc.PUT_SL_MODE             = 'static'
    mc.MAX_POSITIONS           = 14
    mc.MAX_POSITIONS_CALL      = None
    mc.MAX_POSITIONS_PUT       = None
    mc.TIER_ALLOC = {'ultra': 0.18, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
    mc.PUT_TIER_ALLOC = {'put_top': 0.10, 'put_mid': 0.12, 'put_low': 0.12}
    mc.F3F_CALL_FLOOR          = 0.50
    mc.F3F_PUT_FLOOR           = 0.50
    mc.EARN_SUPP_PUT           = True
    mc.CT_PROMOTE              = True

    # Phase Pri-1 sweep axes
    priority = cfg.get('PUT_PRIORITY', 'calls_first')
    mc.PUT_PRIORITY = priority

    if priority == 'wr_merged':
        formula  = cfg.get('WR_FORMULA',  'WR15')
        cb       = float(cfg.get('CALL_BIAS', 1.00))
        fallback = float(cfg.get('WR_FALLBACK', 65.0))
        # CT bonus: keep them ranked above any ordinary tier so CT promotion is preserved
        ct_call_pri = cfg.get('CT_CALL_PRI', None)
        ct_put_pri  = cfg.get('CT_PUT_PRI',  None)
        mc.WR_PRIORITY_TABLE = build_priority_table(
            formula=formula, call_bias=cb, fallback=fallback,
            tier_stats=_TIER_STATS,
            ct_call_pri=ct_call_pri, ct_put_pri=ct_put_pri,
        )
        mc.WR_PRIORITY_FALLBACK = fallback
    else:
        # Clear table so non-wr_merged modes behave as documented
        mc.WR_PRIORITY_TABLE = {}


def run_config_3mode_pri(cfg, windows=None, version=None, silent=True):
    if windows is None:
        windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_pri(cfg2)
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
        max_dd = max(float(real.get('worst_dd', 0)),
                     float(cons.get('worst_dd', 0)),
                     float(opt.get('worst_dd', 0)))
        max_collapse = max(float(real.get('p_coll', 0)),
                           float(cons.get('p_coll', 0)),
                           float(opt.get('p_coll', 0)))
        out[label] = {
            'mean_ret': float(real.get('mean_ret', 0)),
            'med_ret':  float(real.get('med_ret', 0)),
            'worst_dd': max_dd,
            'mean_dd':  float(real.get('mean_dd', 0)),
            'p_collapse': max_collapse,
            'call_tp':  float(real.get('call_tp', 0)),
            'put_tp':   float(real.get('put_tp', 0)),
            'call_trades': float(real.get('call_trades', 0)),
            'put_trades':  float(real.get('put_trades', 0)),
            'cons_ret': float(cons.get('mean_ret', 0)),
            'opt_ret':  float(opt.get('mean_ret', 0)),
        }
    return out


bayes_mc.apply_config = extended_apply_config_pri
bayes_mc.run_config = run_config_3mode_pri
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}
import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_pri


PHASE_PRI_BASE = dict(BASE_CONFIG)
PHASE_PRI_BASE['N_ITER'] = 100
PHASE_PRI_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
PHASE_PRI_BASE['PUT_PRIORITY'] = 'calls_first'
PHASE_PRI_BASE['WR_FORMULA']   = 'WR15'
PHASE_PRI_BASE['CALL_BIAS']    = 1.00
PHASE_PRI_BASE['WR_FALLBACK']  = 65.0


PARAM_SPACE = {
    'PUT_PRIORITY':  ['wr_merged', 'merged', 'calls_first', 'puts_first'],
    'WR_FORMULA':    ['WR15', 'WR30', 'WR15xIC', 'WR15xLogN', 'EV'],
    'CALL_BIAS':     [0.94, 0.97, 1.00, 1.03, 1.06],
    'WR_FALLBACK':   [50.0, 65.0, 70.0],
}


# Hand-curated seeds — cover the corners + control arms
SEED_CONFIGS = [
    # 1. Production baseline (control arm V0)
    {'PUT_PRIORITY': 'calls_first', 'WR_FORMULA': 'WR15', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 2. puts_first control
    {'PUT_PRIORITY': 'puts_first',  'WR_FORMULA': 'WR15', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 3. raw merged (existing) control
    {'PUT_PRIORITY': 'merged',      'WR_FORMULA': 'WR15', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 4. wr_merged neutral (no bias)
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR15', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 5. wr_merged slight call bias (calls have call BE=56.3% vs put BE=43.5%)
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR15', 'CALL_BIAS': 1.03, 'WR_FALLBACK': 65.0},
    # 6. wr_merged stronger call bias
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR15', 'CALL_BIAS': 1.06, 'WR_FALLBACK': 65.0},
    # 7. wr_merged put-favoring bias
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR15', 'CALL_BIAS': 0.97, 'WR_FALLBACK': 65.0},
    # 8. wr_merged WR30 formula (longer-window stability, smaller-N tiers)
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR30', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 9. wr_merged IC-weighted (rewards bands with positive cross-sectional IC)
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR15xIC', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 10. wr_merged log-N dampened (penalize tiny-N tiers)
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR15xLogN', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 11. wr_merged EV (asymmetric reward × WR)
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'EV', 'CALL_BIAS': 1.00, 'WR_FALLBACK': 65.0},
    # 12. wr_merged WR15 + slight call bias + put-fallback
    {'PUT_PRIORITY': 'wr_merged',   'WR_FORMULA': 'WR15', 'CALL_BIAS': 1.03, 'WR_FALLBACK': 70.0},
]


def main():
    print('=' * 100)
    print('PHASE PRI-1 — call/put priority refinement (wr_merged + control arms)')
    print('=' * 100)
    print()
    print('Per-tier WR15/N/IC (from v28 5y assessment):')
    for k, s in _TIER_STATS.items():
        print(f'  {k:>10s}: WR15={s["wr15"]:5.1f}  WR30={s["wr30"]:5.1f}  N={s["n"]:>6d}  IC={s["ic"]:+.4f}')
    print()
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v28_phase_pri1',
        param_space=PARAM_SPACE,
        base_config=PHASE_PRI_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '30')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v28_phase_pri1', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_pri1_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in ranked[:10]:
            payload.append({
                'rank': len(payload) + 1,
                'utility': r['utility'],
                'params': r['params'],
                'windows': r['windows'],
                'log_return': r['util_info']['log_return'],
                'max_dd':     r['util_info']['max_dd'],
                'max_collapse': r['util_info']['max_collapse'],
            })
        json.dump(payload, f, indent=2, default=str)
    print(f'\nTop 10 saved to {out_path}')


if __name__ == '__main__':
    main()
