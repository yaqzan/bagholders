"""Phase G — composite refinement combining best from Phase E + F.

Locks Phase E winner + Phase F F3f variant, then explores:
1. Even tighter PUT_SL: s02, s03 (extend boundary further)
2. Even wider PUT_TP: 0.40, 0.45
3. PUT_PRIORITY revisit: 'merged' with new tight settings (Phase D merged
   was bad with PUT_SL=-0.20, may be different at -0.05)
4. F3f variant: {phase_e_baseline, phase_f_winner, phase_f_disabled}
5. Call cascade fine-tune for the rare 100 calls

Screening: 22-now × N=100 × 3 modes, MC_NO_MP=1.
"""
from __future__ import annotations
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments.v27_optimization.phase_e_put_finetune import (
    extended_apply_config_e as _e_apply,
    PHASE_E_BASE,
    PUT_SL_VARIANTS as PHASE_E_VARIANTS,
)
from experiments.v27_optimization.joint_sweep import SCREENING_WINDOWS
from experiments import bayes_mc

# Extended Phase G variants — even tighter
PUT_SL_VARIANTS = dict(PHASE_E_VARIANTS)
PUT_SL_VARIANTS.update({
    's02':  ('static', -0.02, None),
    's03':  ('static', -0.03, None),
    's04':  ('static', -0.04, None),
})

# Override PUT_SL_VARIANTS in phase_e module so its decoder uses extended set
import experiments.v27_optimization.phase_e_put_finetune as _pe_module
_pe_module.PUT_SL_VARIANTS = PUT_SL_VARIANTS


# F3f preset configurations
F3F_PRESETS = {
    'phase_e':           {'F3F_PUT_THRESH': 75.0, 'F3F_PUT_FLOOR': 0.75, 'F3F_PUT_HIGH': 95.0,
                          'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 0.70},
    'phase_f_winner':    {'F3F_PUT_THRESH': 65.0, 'F3F_PUT_FLOOR': 0.85, 'F3F_PUT_HIGH': 100.0,
                          'F3F_CALL_THRESH': 60.0, 'F3F_CALL_FLOOR': 0.60},
    'disabled':          {'F3F_PUT_THRESH': 100.0, 'F3F_PUT_FLOOR': 1.00, 'F3F_PUT_HIGH': 100.0,
                          'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 1.00},
}


def extended_apply_config_g(cfg: dict):
    """Phase G: Phase E apply + F3f preset + cascade depth."""
    _e_apply(cfg)

    # F3f preset decoding
    preset = cfg.get('F3F_PRESET')
    if preset and preset in F3F_PRESETS:
        for k, v in F3F_PRESETS[preset].items():
            setattr(mc, k, v)

    # Call cascade depth (rare but matters)
    if 'TIER_ALLOC.top' in cfg or 'TIER_ALLOC.mid' in cfg or 'TIER_ALLOC.low' in cfg:
        ta = dict(mc.TIER_ALLOC)
        ta['top'] = cfg.get('TIER_ALLOC.top', ta.get('top', 0.15))
        ta['mid'] = cfg.get('TIER_ALLOC.mid', ta.get('mid', 0.15))
        ta['low'] = cfg.get('TIER_ALLOC.low', ta.get('low', 0.15))
        mc.TIER_ALLOC = ta


def run_config_3mode_g(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_g(cfg2)
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


bayes_mc.apply_config = extended_apply_config_g
bayes_mc.run_config = run_config_3mode_g
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}


# Phase G base: lock Phase E winner; F3f from Phase F winner
PHASE_G_BASE = dict(PHASE_E_BASE)
PHASE_G_BASE['PUT_SL_VARIANT'] = 's05'
PHASE_G_BASE['MAX_POSITIONS']  = 18
PHASE_G_BASE['PUT_TP']         = 0.35
PHASE_G_BASE['PUT_THRESHOLD']  = 25
PHASE_G_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.12, 'put_mid': 0.10, 'put_low': 0.10}
PHASE_G_BASE['PUT_SL_HOLD_BARS_DEFAULT'] = 3
PHASE_G_BASE['PUT_PRIORITY']   = 'puts_first'
PHASE_G_BASE['F3F_PRESET']     = 'phase_f_winner'
PHASE_G_BASE['N_ITER']         = 100
PHASE_G_BASE['COLLISION_MODES']= ['conservative', 'realistic', 'optimistic']
# call cascade defaults
PHASE_G_BASE['TIER_ALLOC']     = {'ultra': 0.28, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}


PARAM_SPACE = {
    'PUT_SL_VARIANT':    ['s02', 's03', 's04', 's05', 's07', 's08'],
    'PUT_TP':            [0.30, 0.35, 0.40, 0.45],
    'PUT_PRIORITY':      ['puts_first', 'merged'],
    'F3F_PRESET':        ['phase_e', 'phase_f_winner', 'disabled'],
    'TIER_ALLOC.top':    [0.13, 0.15, 0.18],   # call cascade — minor effect
}

SEED_CONFIGS = [
    # 1. Phase E winner exact
    {'PUT_SL_VARIANT': 's05', 'PUT_TP': 0.35, 'PUT_PRIORITY': 'puts_first',
     'F3F_PRESET': 'phase_e', 'TIER_ALLOC.top': 0.15},
    # 2. Phase E + Phase F F3f
    {'PUT_SL_VARIANT': 's05', 'PUT_TP': 0.35, 'PUT_PRIORITY': 'puts_first',
     'F3F_PRESET': 'phase_f_winner', 'TIER_ALLOC.top': 0.15},
    # 3. Even tighter PUT_SL
    {'PUT_SL_VARIANT': 's03', 'PUT_TP': 0.35, 'PUT_PRIORITY': 'puts_first',
     'F3F_PRESET': 'phase_f_winner', 'TIER_ALLOC.top': 0.15},
    # 4. Tightest PUT_SL boundary
    {'PUT_SL_VARIANT': 's02', 'PUT_TP': 0.35, 'PUT_PRIORITY': 'puts_first',
     'F3F_PRESET': 'phase_f_winner', 'TIER_ALLOC.top': 0.15},
    # 5. Even wider TP
    {'PUT_SL_VARIANT': 's05', 'PUT_TP': 0.40, 'PUT_PRIORITY': 'puts_first',
     'F3F_PRESET': 'phase_f_winner', 'TIER_ALLOC.top': 0.15},
    # 6. Tighter SL + wider TP combo
    {'PUT_SL_VARIANT': 's03', 'PUT_TP': 0.40, 'PUT_PRIORITY': 'puts_first',
     'F3F_PRESET': 'phase_f_winner', 'TIER_ALLOC.top': 0.15},
    # 7. Merged priority revisit (with new tight settings)
    {'PUT_SL_VARIANT': 's05', 'PUT_TP': 0.35, 'PUT_PRIORITY': 'merged',
     'F3F_PRESET': 'phase_f_winner', 'TIER_ALLOC.top': 0.15},
    # 8. Disabled F3f (confirm production F3f isn't needed)
    {'PUT_SL_VARIANT': 's05', 'PUT_TP': 0.35, 'PUT_PRIORITY': 'puts_first',
     'F3F_PRESET': 'disabled', 'TIER_ALLOC.top': 0.15},
]


def main():
    print('=' * 100)
    print('PHASE G — composite refinement')
    print('=' * 100)
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Param space: {len(PARAM_SPACE)} axes, {n} cells')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_g',
        param_space=PARAM_SPACE,
        base_config=PHASE_G_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '40')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_g', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_g_top_candidates.json')
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
