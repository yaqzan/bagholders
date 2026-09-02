"""Phase H2 — graduated PUT_SL (wider during hold, tighter after).

Tests user's hypothesis: holds had real-world value, the bug-fix invalidated
that ONLY because gap-through during hold was unmodeled. A graduated SL that
is WIDER during the hold (catastrophe protection) but TIGHTER after (velocity
when trade has confirmed direction) might give us both:
- Avoid noise stops at trade entry
- Avoid catastrophic gap-through during hold
- Capture velocity post-hold

Uses the existing PUT_SL_MODE='stepped' feature in `compute_put_outcome`.
Format: list of (max_bar, sl_pct) tuples — first whose max_bar >= current
bar wins.

The hold mechanic still applies: SL inactive for first N bars regardless of
the stepped value. So graduated SL is only relevant for bars > sl_hold.

Tested variants compare:
- Static (PUT_SL=-0.10, hold=2 — H1 winner) as control
- Graduated: hold-period wide (-0.30/-0.40), post-hold tight (-0.10/-0.15)
- Various hold values to see interaction

Locked: H1 winner architecture (calls_first, EARN=False, ultra=0.25, MaxPos=16,
prod cascade, F3f production).
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

# Variant decoder: (mode, static_sl_or_None, stepped_or_None)
PUT_SL_VARIANTS = {
    # Static controls
    'stat10':         ('static', -0.10, None),
    'stat15':         ('static', -0.15, None),
    'stat20':         ('static', -0.20, None),
    # Graduated — wider during hold, tighter after
    # Format: [(bar_max_inclusive, sl_pct), ...]
    'grad_30_15':     ('stepped', None, [(2, -0.30), (15, -0.15)]),  # 2-bar wide protection, then tight
    'grad_30_10':     ('stepped', None, [(2, -0.30), (15, -0.10)]),  # 2-bar wide, then very tight
    'grad_40_15':     ('stepped', None, [(2, -0.40), (15, -0.15)]),  # wider catastrophe protection
    'grad3_30_15':    ('stepped', None, [(3, -0.30), (15, -0.15)]),  # 3-bar hold-wide
    'grad3_40_10':    ('stepped', None, [(3, -0.40), (15, -0.10)]),  # 3-bar wide + tightest post
    'grad3_50_15':    ('stepped', None, [(3, -0.50), (15, -0.15)]),  # very wide hold (no fire during)
    'grad1_30_15':    ('stepped', None, [(1, -0.30), (15, -0.15)]),  # 1-bar minimal hold
    'grad_50_20':     ('stepped', None, [(2, -0.50), (15, -0.20)]),  # max protection + production-tight
}


def extended_apply_config_h2(cfg: dict):
    _orig_apply(cfg)
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
            mc.PUT_SL = stepped[-1][1]
            mc.PUT_NET_SL = stepped[-1][1] + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.PUT_SL_SIGMA = abs(stepped[-1][1]) * mc.PREMIUM_MULT / mc.DELTA
    if 'PUT_SL_HOLD_BARS_DEFAULT' in cfg:
        mc.PUT_SL_HOLD_BARS_DEFAULT = cfg['PUT_SL_HOLD_BARS_DEFAULT']
        mc.PUT_SL_HOLD_BARS_MONDAY = max(0, cfg['PUT_SL_HOLD_BARS_DEFAULT'] + 1) if cfg['PUT_SL_HOLD_BARS_DEFAULT'] > 0 else 0


def run_config_3mode_h2(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_h2(cfg2)
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


bayes_mc.apply_config = extended_apply_config_h2
bayes_mc.run_config = run_config_3mode_h2
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

import experiments.v27_optimization.joint_sweep as _js
_js.extended_apply_config = extended_apply_config_h2


PHASE_H2_BASE = dict(BASE_CONFIG)
PHASE_H2_BASE['PUT_TP'] = 0.30
PHASE_H2_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12}
PHASE_H2_BASE['EARN_SUPP_PUT'] = False
PHASE_H2_BASE['TIER_ALLOC'] = {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
PHASE_H2_BASE['MAX_POSITIONS'] = 16
PHASE_H2_BASE['N_ITER'] = 100
PHASE_H2_BASE['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
PHASE_H2_BASE['PUT_BREADTH_MODE'] = 'none'
PHASE_H2_BASE['PUT_SL_VARIANT'] = 'stat10'  # H1 winner static SL
PHASE_H2_BASE['PUT_SL_HOLD_BARS_DEFAULT'] = 2  # H1 winner hold
PHASE_H2_BASE['PUT_PRIORITY'] = 'calls_first'


PARAM_SPACE = {
    'PUT_SL_VARIANT':           list(PUT_SL_VARIANTS.keys()),
    'PUT_SL_HOLD_BARS_DEFAULT': [0, 1, 2, 3],
}

# Strategic seeds — anchor at H1 winner + key graduated variants
SEED_CONFIGS = [
    # Controls (H1 winners)
    {'PUT_SL_VARIANT': 'stat10', 'PUT_SL_HOLD_BARS_DEFAULT': 2},   # H1 rank 1
    {'PUT_SL_VARIANT': 'stat15', 'PUT_SL_HOLD_BARS_DEFAULT': 0},   # H1 rank 2
    {'PUT_SL_VARIANT': 'stat20', 'PUT_SL_HOLD_BARS_DEFAULT': 0},   # H1 rank 3
    # Graduated variants (the user's hypothesis)
    {'PUT_SL_VARIANT': 'grad_30_15', 'PUT_SL_HOLD_BARS_DEFAULT': 2},  # 2-bar wide-30, then tight-15
    {'PUT_SL_VARIANT': 'grad_30_10', 'PUT_SL_HOLD_BARS_DEFAULT': 2},  # 2-bar wide-30, then very tight-10
    {'PUT_SL_VARIANT': 'grad_40_15', 'PUT_SL_HOLD_BARS_DEFAULT': 2},  # wider catastrophe at -40
    {'PUT_SL_VARIANT': 'grad3_30_15', 'PUT_SL_HOLD_BARS_DEFAULT': 3}, # 3-bar wide protection
    {'PUT_SL_VARIANT': 'grad3_40_10', 'PUT_SL_HOLD_BARS_DEFAULT': 3}, # 3-bar wide + tightest post
    {'PUT_SL_VARIANT': 'grad3_50_15', 'PUT_SL_HOLD_BARS_DEFAULT': 3}, # very wide hold protection
    {'PUT_SL_VARIANT': 'grad1_30_15', 'PUT_SL_HOLD_BARS_DEFAULT': 1}, # minimal 1-bar hold
    {'PUT_SL_VARIANT': 'grad_50_20', 'PUT_SL_HOLD_BARS_DEFAULT': 2},  # max protection + prod-tight post
    # Sanity: graduated with hold=0 (should match static post-hold)
    {'PUT_SL_VARIANT': 'grad_30_15', 'PUT_SL_HOLD_BARS_DEFAULT': 0},
]


def main():
    print('=' * 100)
    print('PHASE H2 — graduated PUT_SL (wide during hold, tight after)')
    print('=' * 100)
    print('Hypothesis: holds had real value, but bug masked gap-through. Graduated SL gives:')
    print('  - WIDE during hold (catastrophe protection)')
    print('  - TIGHT after hold (capture velocity)')
    print()
    print(f'Variants: {len(PUT_SL_VARIANTS)} (3 static controls, 9 graduated)')
    for k, v in PUT_SL_VARIANTS.items():
        print(f'  {k}: {v}')
    print()
    print(f'Param space: {len(PARAM_SPACE)} axes, {len(PUT_SL_VARIANTS) * 4} cells')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_h2',
        param_space=PARAM_SPACE,
        base_config=PHASE_H2_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '25')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_h2', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_h2_top_candidates.json')
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
