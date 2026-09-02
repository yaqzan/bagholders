"""Joint Bayesian sweep across all v27 portfolio-stage knobs.

Builds on experiments/bayes_mc.py harness with extended apply_config to handle:
  - EARN_SUPP_PUT (on/off)
  - F3F_* breadth thresholds
  - REGIME_SLOPE_* (legacy path, off by default with BREADTH_ALLOC_ENABLED=True)
  - CT_PROMOTE knobs
  - PUT_TP / PUT_SL (already in bayes_mc.py)
  - All cascade allocations (already in bayes_mc.py)

Screening config:
  - 22-now single window (the user's stated objective)
  - N=100 iterations
  - All 3 collision modes (DD-C floor is binding constraint)

Why screening on 22-now alone: the user's headline metric is 22-now compound
return + minimal DD. Annual windows at full N=500 are reserved for Phase C.

Output:
  - experiments/bayes_logs/phase_v27_joint.jsonl  (per-eval log)
  - experiments/v27_optimization/phase_b_top_candidates.json
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments import bayes_mc

# ----------------------------------------------------------------------------
# Extended apply_config: handle the knobs bayes_mc.py doesn't natively touch.
# ----------------------------------------------------------------------------

_orig_apply_config = bayes_mc.apply_config


def extended_apply_config(cfg: dict):
    """Apply the standard config plus extended v27-era knobs."""
    _orig_apply_config(cfg)

    # F3f breadth knob
    if 'F3F_CALL_THRESH' in cfg:    mc.F3F_CALL_THRESH = cfg['F3F_CALL_THRESH']
    if 'F3F_CALL_FLOOR' in cfg:     mc.F3F_CALL_FLOOR = cfg['F3F_CALL_FLOOR']
    if 'F3F_CALL_LOW' in cfg:       mc.F3F_CALL_LOW = cfg['F3F_CALL_LOW']
    if 'F3F_PUT_THRESH' in cfg:     mc.F3F_PUT_THRESH = cfg['F3F_PUT_THRESH']
    if 'F3F_PUT_FLOOR' in cfg:      mc.F3F_PUT_FLOOR = cfg['F3F_PUT_FLOOR']
    if 'F3F_PUT_HIGH' in cfg:       mc.F3F_PUT_HIGH = cfg['F3F_PUT_HIGH']
    if 'BREADTH_ALLOC_ENABLED' in cfg: mc.BREADTH_ALLOC_ENABLED = cfg['BREADTH_ALLOC_ENABLED']

    # EARN_SUPP_PUT
    if 'EARN_SUPP_PUT' in cfg:        mc.EARN_SUPP_PUT = cfg['EARN_SUPP_PUT']
    if 'EARN_SUPP_PUT_DAYS' in cfg:   mc.EARN_SUPP_PUT_DAYS = cfg['EARN_SUPP_PUT_DAYS']
    if 'EARN_SUPP_PUT_MIN_OV' in cfg: mc.EARN_SUPP_PUT_MIN_OV = cfg['EARN_SUPP_PUT_MIN_OV']
    if 'EARN_SUPP_PUT_MAX_OV' in cfg: mc.EARN_SUPP_PUT_MAX_OV = cfg['EARN_SUPP_PUT_MAX_OV']

    # CT_PROMOTE
    if 'CT_PROMOTE' in cfg:           mc.CT_PROMOTE = cfg['CT_PROMOTE']
    if 'CT_PUT_TREND_MIN' in cfg:     mc.CT_PUT_TREND_MIN = cfg['CT_PUT_TREND_MIN']
    if 'CT_CALL_TREND_MAX' in cfg:    mc.CT_CALL_TREND_MAX = cfg['CT_CALL_TREND_MAX']

    # Regime slopes (legacy path, ignored when BREADTH_ALLOC_ENABLED=True)
    if 'REGIME_SLOPE_UP' in cfg:        mc.REGIME_SLOPE_UP = cfg['REGIME_SLOPE_UP']
    if 'REGIME_SLOPE_DOWN' in cfg:      mc.REGIME_SLOPE_DOWN = cfg['REGIME_SLOPE_DOWN']
    if 'REGIME_SLOPE_PUT_UP' in cfg:    mc.REGIME_SLOPE_PUT_UP = cfg['REGIME_SLOPE_PUT_UP']
    if 'REGIME_SLOPE_PUT_DOWN' in cfg:  mc.REGIME_SLOPE_PUT_DOWN = cfg['REGIME_SLOPE_PUT_DOWN']


bayes_mc.apply_config = extended_apply_config


# ----------------------------------------------------------------------------
# Screening windows + utility tweak
# ----------------------------------------------------------------------------

SCREENING_WINDOWS = [
    ('22-now', date(2022, 1, 1), date(2026, 4, 15)),
]
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}

# ----------------------------------------------------------------------------
# Override utility to read worst_dd ACROSS all 3 modes (not just realistic).
# Run all 3 modes in run_config; pull max DD over modes; use realistic mean
# for return.
# ----------------------------------------------------------------------------

def run_config_3mode(cfg: dict, windows=None, version=None, silent=True):
    """Same as bayes_mc.run_config but runs all 3 collision modes and
    captures the worst DD across modes (Conservative mode is the binding floor)."""
    if windows is None:
        windows = SCREENING_WINDOWS

    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config(cfg2)

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
        # max DD across modes — Conservative usually highest
        max_dd = max(
            float(real.get('worst_dd', 0)),
            float(cons.get('worst_dd', 0)),
            float(opt.get('worst_dd', 0)),
        )
        max_collapse = max(
            float(real.get('p_coll', 0)),
            float(cons.get('p_coll', 0)),
            float(opt.get('p_coll', 0)),
        )
        out[label] = {
            'mean_ret':   float(real.get('mean_ret', 0)),  # use realistic for utility
            'med_ret':    float(real.get('med_ret', 0)),
            'worst_dd':   max_dd,                          # ACROSS modes (DD-C floor)
            'mean_dd':    float(real.get('mean_dd', 0)),
            'p_collapse': max_collapse,
            'call_tp':    float(real.get('call_tp', 0)),
            'put_tp':     float(real.get('put_tp', 0)),
            'call_trades': float(real.get('call_trades', 0)),
            'put_trades':  float(real.get('put_trades', 0)),
            'cons_ret':   float(cons.get('mean_ret', 0)),
            'opt_ret':    float(opt.get('mean_ret', 0)),
            'cons_dd':    float(cons.get('worst_dd', 0)),
            'real_dd':    float(real.get('worst_dd', 0)),
        }
    return out


# Replace bayes_mc.run_config with our 3-mode version for the optimizer
bayes_mc.run_config = run_config_3mode


# ----------------------------------------------------------------------------
# Joint parameter space — focus on highest-leverage axes given v27.
# ----------------------------------------------------------------------------

# Production v27 baseline (current monte_carlo.py defaults)
BASE_CONFIG = dict(bayes_mc.DEFAULT_CONFIG)
BASE_CONFIG.update({
    # Locked call-side defaults
    'TP_BASE':            0.30,
    'TP_STRESS':          0.35,
    'SL_BASE':           -0.35,
    'SL_STRESS':         -0.40,
    'BREADTH_THRESHOLD':  50,
    'TIER_ALLOC':         {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00},
    # Put-side baseline
    'PUT_TP':             0.30,
    'PUT_SL':            -0.20,
    'PUT_TP_STRESS':      0.30,
    'PUT_SL_STRESS':     -0.20,
    'PUT_BREADTH_MODE':   'none',
    'PUT_BREADTH_THRESHOLD': 50,
    'PUT_TIER_ALLOC':     {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    'PUT_THRESHOLD':      25,
    # Capacity
    'MAX_POSITIONS':         14,
    'MAX_POSITIONS_CALL':    None,
    'MAX_POSITIONS_PUT':     None,
    # Sweep knobs
    'N_ITER':             100,
    'COLLISION_MODES':    ['conservative', 'realistic', 'optimistic'],
    # Extended v27-era knobs
    'BREADTH_ALLOC_ENABLED': True,
    'F3F_CALL_THRESH':       50.0,
    'F3F_CALL_FLOOR':        0.70,
    'F3F_CALL_LOW':          20.0,
    'F3F_PUT_THRESH':        75.0,
    'F3F_PUT_FLOOR':         0.75,
    'F3F_PUT_HIGH':          95.0,
    'EARN_SUPP_PUT':         True,
    'EARN_SUPP_PUT_DAYS':    5,
    'EARN_SUPP_PUT_MIN_OV':  16,
    'EARN_SUPP_PUT_MAX_OV':  20,
    'CT_PROMOTE':            True,
    'CT_PUT_TREND_MIN':      80,
    'CT_CALL_TREND_MAX':     20,
})


# Joint param space.
# Design philosophy:
# - 6 axes, mostly 3 levels each
# - Tight ranges around production (don't search exotic territory)
# - Highest leverage: put cascade concentration, put SL, EARN_SUPP_PUT
# - Cross-axis: cascade depth × MAX_POSITIONS interact via slot competition
PARAM_SPACE = {
    # Axis 1: Put SL — wider may capture shakeout-recovery EV with cleaner v27 puts
    'PUT_SL':                       [-0.15, -0.20, -0.25, -0.30],

    # Axis 2: Put cascade top tier (15 default; cleaner puts may justify concentration)
    'PUT_TIER_ALLOC.put_top':       [0.15, 0.18, 0.22],

    # Axis 3: Put cascade middle (currently 12; symmetry)
    'PUT_TIER_ALLOC.put_mid':       [0.10, 0.12, 0.15],

    # Axis 4: EARN_SUPP_PUT — Priority #14 hypothesis: redundant with v27 weekly fix
    'EARN_SUPP_PUT':                [True, False],

    # Axis 5: Capacity — put N collapsed -75%; pool dynamics shift
    'MAX_POSITIONS':                [12, 14, 16],

    # Axis 6: Call ultra alloc (95+ tier, currently 25%)
    'TIER_ALLOC.ultra':             [0.22, 0.25, 0.28],
}

# Seed evaluations: production baseline + reasonable perturbations
SEED_CONFIGS = [
    # Production baseline — must be evaluated
    {'PUT_SL': -0.20, 'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12,
     'EARN_SUPP_PUT': True, 'MAX_POSITIONS': 14, 'TIER_ALLOC.ultra': 0.25},
    # Concentrated puts (cleaner v27 signals deserve more weight)
    {'PUT_SL': -0.20, 'PUT_TIER_ALLOC.put_top': 0.22, 'PUT_TIER_ALLOC.put_mid': 0.15,
     'EARN_SUPP_PUT': True, 'MAX_POSITIONS': 14, 'TIER_ALLOC.ultra': 0.25},
    # Wider put SL (test shakeout-recovery hypothesis)
    {'PUT_SL': -0.30, 'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12,
     'EARN_SUPP_PUT': True, 'MAX_POSITIONS': 14, 'TIER_ALLOC.ultra': 0.25},
    # No EARN_SUPP_PUT (test redundancy with v27 weekly fix)
    {'PUT_SL': -0.20, 'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12,
     'EARN_SUPP_PUT': False, 'MAX_POSITIONS': 14, 'TIER_ALLOC.ultra': 0.25},
    # Tighter MaxPos (reduced put N -> fewer slots needed)
    {'PUT_SL': -0.20, 'PUT_TIER_ALLOC.put_top': 0.18, 'PUT_TIER_ALLOC.put_mid': 0.12,
     'EARN_SUPP_PUT': True, 'MAX_POSITIONS': 12, 'TIER_ALLOC.ultra': 0.25},
]


def main():
    print('=' * 100)
    print('JOINT BAYESIAN SWEEP — v27 (ad02704) — 22-now screening')
    print('=' * 100)
    print(f'Param space: {len(PARAM_SPACE)} axes')
    for k, v in PARAM_SPACE.items():
        print(f'  {k}: {v}')
    print(f'Seeds: {len(SEED_CONFIGS)} explicit configs')
    print(f'Screening: 22-now × N={BASE_CONFIG["N_ITER"]} × all 3 collision modes')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_joint',
        param_space=PARAM_SPACE,
        base_config=BASE_CONFIG,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '40')),
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.30,
        kappa_init=2.0,
        kappa_final=1.0,
        windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_joint', best, evaluated)

    # Save top-K to JSON for Phase C consumption
    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    top_k = ranked[:10]
    out_path = os.path.join(os.path.dirname(__file__), 'phase_b_top_candidates.json')
    with open(out_path, 'w') as f:
        payload = []
        for r in top_k:
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
