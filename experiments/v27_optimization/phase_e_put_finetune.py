"""Phase E (revised) — PUT-side fine-tune given puts_first wins.

Phase D winner (puts_first + PUT_SL=-0.10 + MaxPos=18 + hold=3) shows
puts dominate the slot pool (2,674 vs 105 calls on 22-now). Call-side
optimization has minimal portfolio impact in this regime.

This phase fine-tunes PUT-side knobs around the Phase D winner:
1. Even tighter PUT_SL (s05, s08) — does v27 absorb -0.05 / -0.08?
2. PUT_TIER_ALLOC depths (put_top, put_mid, put_low individually)
3. PUT_THRESHOLD (currently 25; test 20, 22, 28, 30 — does narrower
   put concentration help, or wider catch more setups?)
4. PUT_TP (currently 0.30; test 0.25, 0.28, 0.32, 0.35)
5. MaxPos extension {16, 18, 20, 22, 25}

Locked: puts_first priority, PUT_SL_HOLD_BARS_DEFAULT=3, EARN_SUPP_PUT=False,
ultra=0.28, call cascade as-is (impact minimal at ~100 trades).

Screening: 22-now × N=100 × 3 modes, MC_NO_MP=1 required.
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
    extended_apply_config as _orig_extended,
    SCREENING_WINDOWS,
    BASE_CONFIG,
)
from experiments.v27_optimization.phase_d_full import (
    extended_apply_config_d,
    PUT_SL_VARIANTS as PHASE_D_VARIANTS,
)
from experiments import bayes_mc

# Extended PUT_SL variants for Phase E — even tighter
PUT_SL_VARIANTS = dict(PHASE_D_VARIANTS)
PUT_SL_VARIANTS.update({
    's05':       ('static', -0.05, None),
    's07':       ('static', -0.07, None),
    's08':       ('static', -0.08, None),
    's12':       ('static', -0.12, None),
})


def extended_apply_config_e(cfg: dict):
    """Phase E: same logic as Phase D but with extended variant table + PUT_THRESHOLD/PUT_TP knobs."""
    _orig_extended(cfg)

    # PUT_SL variant decoding (extended table)
    variant = cfg.get('PUT_SL_VARIANT')
    if variant:
        mode, static_sl, stepped = PUT_SL_VARIANTS[variant]
        mc.PUT_SL_MODE = mode
        if mode == 'static':
            mc.PUT_SL = static_sl
            mc.PUT_NET_SL = static_sl + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.PUT_SL_SIGMA = abs(static_sl) * mc.PREMIUM_MULT / mc.DELTA
        else:
            mc.PUT_SL_STEPPED = list(stepped)
            deepest = stepped[-1][1]
            mc.PUT_SL = deepest
            mc.PUT_NET_SL = deepest + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.PUT_SL_SIGMA = abs(deepest) * mc.PREMIUM_MULT / mc.DELTA

    if 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        mc.PUT_SL_HOLD_BARS_DEFAULT = cfg['PUT_SL_HOLD_BARS_DEFAULT']
        mc.PUT_SL_HOLD_BARS_MONDAY = cfg['PUT_SL_HOLD_BARS_DEFAULT'] + 1
    if 'PUT_PRIORITY' in cfg:
        mc.PUT_PRIORITY = cfg['PUT_PRIORITY']

    # NEW: PUT_TP variant
    if 'PUT_TP' in cfg:
        mc.PUT_TP = cfg['PUT_TP']
        mc.PUT_NET_TP = cfg['PUT_TP'] + mc.SLIP_ENTRY + mc.SLIP_TP
        mc.PUT_TP_SIGMA = cfg['PUT_TP'] * mc.PREMIUM_MULT / mc.DELTA

    # NEW: PUT_THRESHOLD
    if 'PUT_THRESHOLD' in cfg:
        mc.PUT_THRESHOLD = cfg['PUT_THRESHOLD']

    # PUT_TIER_ALLOC depths individually
    if 'PUT_TIER_ALLOC' in cfg:
        mc.PUT_TIER_ALLOC = dict(cfg['PUT_TIER_ALLOC'])


# Run config wrapper (same pattern as joint_sweep.run_config_3mode)
def run_config_3mode_e(cfg, windows=None, version=None, silent=True):
    if windows is None:
        windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_e(cfg2)
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
            'mean_ret':   float(real.get('mean_ret', 0)),
            'med_ret':    float(real.get('med_ret', 0)),
            'worst_dd':   max_dd,
            'mean_dd':    float(real.get('mean_dd', 0)),
            'p_collapse': max_collapse,
            'call_tp':    float(real.get('call_tp', 0)),
            'put_tp':     float(real.get('put_tp', 0)),
            'call_trades': float(real.get('call_trades', 0)),
            'put_trades':  float(real.get('put_trades', 0)),
        }
    return out


bayes_mc.apply_config = extended_apply_config_e
bayes_mc.run_config = run_config_3mode_e
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}


# Phase E base: lock Phase D winner + new defaults for Phase E knobs
PHASE_E_BASE = dict(BASE_CONFIG)
PHASE_E_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_E_BASE['EARN_SUPP_PUT']  = False
PHASE_E_BASE['TIER_ALLOC']     = {'ultra': 0.28, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_E_BASE['PUT_SL_VARIANT'] = 's10'           # Phase D winner
PHASE_E_BASE['PUT_SL_HOLD_BARS_DEFAULT'] = 3
PHASE_E_BASE['PUT_PRIORITY']   = 'puts_first'    # locked
PHASE_E_BASE['MAX_POSITIONS']  = 18              # Phase D winner
PHASE_E_BASE['PUT_TP']         = 0.30            # production
PHASE_E_BASE['PUT_THRESHOLD']  = 25              # production
PHASE_E_BASE['N_ITER']         = 100
PHASE_E_BASE['COLLISION_MODES']= ['conservative', 'realistic', 'optimistic']


PARAM_SPACE = {
    'PUT_SL_VARIANT':           ['s05', 's07', 's08', 's10', 's12', 's15'],   # extend tighter
    'MAX_POSITIONS':            [16, 18, 20, 22, 25],                          # extend higher
    'PUT_TP':                   [0.25, 0.28, 0.30, 0.32, 0.35],
    'PUT_THRESHOLD':            [20, 22, 25, 28],
    'PUT_TIER_ALLOC.put_top':   [0.12, 0.15, 0.18, 0.22],
    'PUT_TIER_ALLOC.put_mid':   [0.10, 0.12, 0.14],
    'PUT_TIER_ALLOC.put_low':   [0.08, 0.10, 0.12],
}

# Seeds: Phase D winner + boundary probes
SEED_CONFIGS = [
    # 1. Phase D winner (anchor)
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 18, 'PUT_TP': 0.30, 'PUT_THRESHOLD': 25,
     'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
    # 2. Even tighter SL
    {'PUT_SL_VARIANT': 's08', 'MAX_POSITIONS': 18, 'PUT_TP': 0.30, 'PUT_THRESHOLD': 25,
     'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
    # 3. Wider TP
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 18, 'PUT_TP': 0.35, 'PUT_THRESHOLD': 25,
     'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
    # 4. Tighter TP (faster recycle)
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 18, 'PUT_TP': 0.25, 'PUT_THRESHOLD': 25,
     'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
    # 5. Concentrated puts (top tier)
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 18, 'PUT_TP': 0.30, 'PUT_THRESHOLD': 25,
     'PUT_TIER_ALLOC.put_top': 0.22, 'PUT_TIER_ALLOC.put_mid': 0.14, 'PUT_TIER_ALLOC.put_low': 0.10},
    # 6. Wider PUT_THRESHOLD (catch more)
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 18, 'PUT_TP': 0.30, 'PUT_THRESHOLD': 28,
     'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
    # 7. Narrower PUT_THRESHOLD (concentrate)
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 18, 'PUT_TP': 0.30, 'PUT_THRESHOLD': 22,
     'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
    # 8. MaxPos extension
    {'PUT_SL_VARIANT': 's10', 'MAX_POSITIONS': 22, 'PUT_TP': 0.30, 'PUT_THRESHOLD': 25,
     'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
    # 9. Combined: tighter SL + tighter TP + concentrated cascade
    {'PUT_SL_VARIANT': 's08', 'MAX_POSITIONS': 18, 'PUT_TP': 0.28, 'PUT_THRESHOLD': 25,
     'PUT_TIER_ALLOC.put_top': 0.18, 'PUT_TIER_ALLOC.put_mid': 0.14, 'PUT_TIER_ALLOC.put_low': 0.10},
]


def main():
    print('=' * 100)
    print('PHASE E — PUT-side fine-tune (puts_first locked, v27)')
    print('=' * 100)
    print(f'Locked: priority=puts_first, hold=3, EARN=False, ultra=0.28')
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Total grid cells: {n}')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_e',
        param_space=PARAM_SPACE,
        base_config=PHASE_E_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '50')),
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.30,
        kappa_init=2.0,
        kappa_final=1.0,
        windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_e', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_e_top_candidates.json')
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
    print(f'\nTop 10 candidates saved to {out_path}')


if __name__ == '__main__':
    main()
