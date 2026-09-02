"""Focused 5-variant comparison — bug-fixed MC.

Tests specific hypotheses raised by the user:
1. Documented optimal strategy (production) is a strong baseline
2. The ONLY change of removing hold (hold=3 -> hold=0) on production
3. H1 winner architecture vs production

Variants:
  V0: PRODUCTION as documented
      PUT_SL=-0.20, hold=3, EARN=True, ultra=0.25, top/mid/low=0.15,
      MaxPos=14, calls_first, F3f production
  V1: PROD + hold=0 (only change)
      Same as V0 but hold=0
  V2: PROD + hold=0 + EARN=False (Phase E confirmed bug-independent)
      Same as V1 but EARN_SUPP_PUT=False
  V3: H1 RANK-2 WINNER (current best per H1)
      PUT_SL=-0.15, hold=0, EARN=False, ultra=0.25, MaxPos=16
  V4: H1 RANK-2 + MaxPos=14 (test if MaxPos=14 is genuinely optimal vs 16)
      PUT_SL=-0.15, hold=0, EARN=False, ultra=0.25, MaxPos=14

Each variant runs at screening config (22-now × N=100 × 3 modes).
Reports util, DD, returns, trade counts side by side.
"""
from __future__ import annotations
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments.v27_optimization.joint_sweep import (
    extended_apply_config as _orig_apply,
    SCREENING_WINDOWS,
    BASE_CONFIG,
)


def apply_variant(cfg):
    _orig_apply(cfg)
    if 'PUT_SL_BASE' in cfg:
        mc.PUT_SL_MODE = 'static'
        mc.PUT_SL = cfg['PUT_SL_BASE']
        mc.PUT_NET_SL = cfg['PUT_SL_BASE'] + mc.SLIP_ENTRY + mc.SLIP_SL
        mc.PUT_SL_SIGMA = abs(cfg['PUT_SL_BASE']) * mc.PREMIUM_MULT / mc.DELTA
    if 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        mc.PUT_SL_HOLD_BARS_DEFAULT = cfg['PUT_SL_HOLD_BARS_DEFAULT']
        mc.PUT_SL_HOLD_BARS_MONDAY = max(0, cfg['PUT_SL_HOLD_BARS_DEFAULT'] + 1) if cfg['PUT_SL_HOLD_BARS_DEFAULT'] > 0 else 0
    if 'PUT_PRIORITY' in cfg:
        mc.PUT_PRIORITY = cfg['PUT_PRIORITY']


def base_config():
    cfg = dict(BASE_CONFIG)
    cfg['N_ITER'] = 100
    cfg['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    return cfg


VARIANTS = {
    'V0_PROD': {
        'PUT_SL_BASE': -0.20, 'PUT_TP': 0.30,
        'PUT_SL_HOLD_BARS_DEFAULT': 3, 'PUT_PRIORITY': 'calls_first',
        'EARN_SUPP_PUT': True, 'MAX_POSITIONS': 14,
        'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
        'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    },
    'V1_PROD_HOLD0': {
        'PUT_SL_BASE': -0.20, 'PUT_TP': 0.30,
        'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first',
        'EARN_SUPP_PUT': True, 'MAX_POSITIONS': 14,
        'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
        'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    },
    'V2_PROD_HOLD0_NOEARN': {
        'PUT_SL_BASE': -0.20, 'PUT_TP': 0.30,
        'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first',
        'EARN_SUPP_PUT': False, 'MAX_POSITIONS': 14,
        'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
        'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    },
    'V3_H1_WINNER': {
        'PUT_SL_BASE': -0.15, 'PUT_TP': 0.30,
        'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first',
        'EARN_SUPP_PUT': False, 'MAX_POSITIONS': 16,
        'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
        'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    },
    'V4_H1_MAXP14': {
        'PUT_SL_BASE': -0.15, 'PUT_TP': 0.30,
        'PUT_SL_HOLD_BARS_DEFAULT': 0, 'PUT_PRIORITY': 'calls_first',
        'EARN_SUPP_PUT': False, 'MAX_POSITIONS': 14,
        'TIER_ALLOC': {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
        'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    },
}


def run_one(label, variant_cfg):
    cfg = base_config()
    cfg.update(variant_cfg)
    apply_variant(cfg)
    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()
    label0, d_start, d_end = SCREENING_WINDOWS[0]
    print(f'\n[{label}] running...')
    t0 = time.time()
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wr = mc.run_window(label0, d_start, d_end, version)
    real = wr.get('realistic', {})
    cons = wr.get('conservative', {})
    opt  = wr.get('optimistic', {})
    max_dd = max(float(real.get('worst_dd', 0)), float(cons.get('worst_dd', 0)), float(opt.get('worst_dd', 0)))
    max_coll = max(float(real.get('p_coll', 0)), float(cons.get('p_coll', 0)), float(opt.get('p_coll', 0)))
    print(f'  duration: {time.time()-t0:.1f}s')
    return {
        'label':      label,
        'mean_ret':   float(real.get('mean_ret', 0)),
        'med_ret':    float(real.get('med_ret', 0)),
        'cons_ret':   float(cons.get('mean_ret', 0)),
        'opt_ret':    float(opt.get('mean_ret', 0)),
        'worst_dd':   max_dd,
        'mean_dd':    float(real.get('mean_dd', 0)),
        'p_coll':     max_coll,
        'call_tp':    float(real.get('call_tp', 0)),
        'put_tp':     float(real.get('put_tp', 0)),
        'call_trades': float(real.get('call_trades', 0)),
        'put_trades':  float(real.get('put_trades', 0)),
    }


def main():
    print('=' * 100)
    print('FOCUSED COMPARISON — bug-fixed MC, 22-now × N=100 × 3 modes')
    print('=' * 100)
    results = {}
    for label, cfg in VARIANTS.items():
        results[label] = run_one(label, cfg)

    print('\n' + '=' * 110)
    print('RESULTS')
    print('=' * 110)
    print(f'{"variant":<25}  {"Cons Ret%":>14}  {"Real Ret%":>14}  {"Opt Ret%":>14}  {"DD%":>5}  {"Coll%":>5}  {"CTP":>5}  {"PTP":>5}  {"CTrd":>6}  {"PTrd":>6}')
    print('-' * 110)
    for label, r in results.items():
        print(f'{label:<25}  {r["cons_ret"]:>+12.1f}%  {r["mean_ret"]:>+12.1f}%  {r["opt_ret"]:>+12.1f}%  {r["worst_dd"]:>4.1f}%  {r["p_coll"]:>4.1f}%  {r["call_tp"]:>4.1f}%  {r["put_tp"]:>4.1f}%  {r["call_trades"]:>5.0f}  {r["put_trades"]:>5.0f}')

    out_path = os.path.join(os.path.dirname(__file__), 'focused_comparison.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\nSaved: {out_path}')


if __name__ == '__main__':
    main()
