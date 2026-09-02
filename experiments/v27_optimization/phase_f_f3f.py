"""Phase F — F3f breadth-allocation thresholds.

With puts_first + tight PUT_SL dominating, F3f scaling on PUTS becomes
load-bearing (every put trade is critical). F3f tuned pre-v27 for a
balanced calls+puts cascade — may be over-suppressing puts now.

Knobs:
- F3F_CALL_THRESH (50): below this breadth, calls scale down
- F3F_CALL_FLOOR (0.70): minimum call alloc scale
- F3F_PUT_THRESH (75): above this breadth, puts scale down
- F3F_PUT_FLOOR (0.75): minimum put alloc scale at peak breadth
- F3F_PUT_HIGH (95): breadth at which floor reached

Locked at Phase E winner.
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
)
from experiments.v27_optimization.joint_sweep import (
    SCREENING_WINDOWS,
)
from experiments import bayes_mc


def extended_apply_config_f(cfg: dict):
    _e_apply(cfg)
    if 'F3F_CALL_THRESH' in cfg: mc.F3F_CALL_THRESH = cfg['F3F_CALL_THRESH']
    if 'F3F_CALL_FLOOR' in cfg:  mc.F3F_CALL_FLOOR = cfg['F3F_CALL_FLOOR']
    if 'F3F_CALL_LOW' in cfg:    mc.F3F_CALL_LOW = cfg['F3F_CALL_LOW']
    if 'F3F_PUT_THRESH' in cfg:  mc.F3F_PUT_THRESH = cfg['F3F_PUT_THRESH']
    if 'F3F_PUT_FLOOR' in cfg:   mc.F3F_PUT_FLOOR = cfg['F3F_PUT_FLOOR']
    if 'F3F_PUT_HIGH' in cfg:    mc.F3F_PUT_HIGH = cfg['F3F_PUT_HIGH']


def run_config_3mode_f(cfg, windows=None, version=None, silent=True):
    if windows is None: windows = SCREENING_WINDOWS
    cfg2 = dict(cfg)
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    extended_apply_config_f(cfg2)
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


bayes_mc.apply_config = extended_apply_config_f
bayes_mc.run_config = run_config_3mode_f
bayes_mc.SWEEP_WINDOWS = SCREENING_WINDOWS
bayes_mc.WINDOW_WEIGHTS = {'22-now': 1.0}


# Phase F base: lock Phase E winner
PHASE_F_BASE = dict(PHASE_E_BASE)
# Apply Phase E winner params (copy from rank-1 of phase_e_top_candidates.json):
PHASE_F_BASE['PUT_SL_VARIANT'] = 's05'
PHASE_F_BASE['MAX_POSITIONS']  = 18
PHASE_F_BASE['PUT_TP']         = 0.35
PHASE_F_BASE['PUT_THRESHOLD']  = 25
PHASE_F_BASE['PUT_TIER_ALLOC'] = {'put_top': 0.12, 'put_mid': 0.10, 'put_low': 0.10}
PHASE_F_BASE['N_ITER']         = 100
# F3f defaults
PHASE_F_BASE['F3F_CALL_THRESH'] = 50.0
PHASE_F_BASE['F3F_CALL_FLOOR']  = 0.70
PHASE_F_BASE['F3F_CALL_LOW']    = 20.0
PHASE_F_BASE['F3F_PUT_THRESH']  = 75.0
PHASE_F_BASE['F3F_PUT_FLOOR']   = 0.75
PHASE_F_BASE['F3F_PUT_HIGH']    = 95.0


PARAM_SPACE = {
    'F3F_PUT_THRESH': [65.0, 75.0, 85.0, 100.0],   # last value = no scaling
    'F3F_PUT_FLOOR':  [0.60, 0.75, 0.85, 0.95, 1.00],  # 1.00 = no scaling
    'F3F_PUT_HIGH':   [85.0, 90.0, 95.0, 100.0],
    'F3F_CALL_THRESH':[40.0, 50.0, 60.0],
    'F3F_CALL_FLOOR': [0.60, 0.70, 0.80, 1.00],   # 1.00 = no scaling
}

SEED_CONFIGS = [
    # Anchor: Phase E winner (production F3f)
    {'F3F_PUT_THRESH': 75.0, 'F3F_PUT_FLOOR': 0.75, 'F3F_PUT_HIGH': 95.0,
     'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 0.70},
    # Disable F3f entirely (test if scaling helps at all)
    {'F3F_PUT_THRESH': 100.0, 'F3F_PUT_FLOOR': 1.00, 'F3F_PUT_HIGH': 100.0,
     'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 1.00},
    # Disable put F3f only
    {'F3F_PUT_THRESH': 100.0, 'F3F_PUT_FLOOR': 1.00, 'F3F_PUT_HIGH': 100.0,
     'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 0.70},
    # Wider put threshold (let puts fire higher in breadth)
    {'F3F_PUT_THRESH': 85.0, 'F3F_PUT_FLOOR': 0.75, 'F3F_PUT_HIGH': 95.0,
     'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 0.70},
    # Gentler put cut (less suppression)
    {'F3F_PUT_THRESH': 75.0, 'F3F_PUT_FLOOR': 0.85, 'F3F_PUT_HIGH': 95.0,
     'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 0.70},
    # Tighter put threshold (cut more aggressively)
    {'F3F_PUT_THRESH': 65.0, 'F3F_PUT_FLOOR': 0.60, 'F3F_PUT_HIGH': 90.0,
     'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 0.70},
    # Disable call F3f (let calls fire at full alloc in stress)
    {'F3F_PUT_THRESH': 75.0, 'F3F_PUT_FLOOR': 0.75, 'F3F_PUT_HIGH': 95.0,
     'F3F_CALL_THRESH': 50.0, 'F3F_CALL_FLOOR': 1.00},
]


def main():
    print('=' * 100)
    print('PHASE F — F3f breadth-alloc thresholds (locked Phase E winner)')
    print('=' * 100)
    import functools, operator
    n = functools.reduce(operator.mul, [len(v) for v in PARAM_SPACE.values()])
    print(f'Param space: {len(PARAM_SPACE)} axes, {n} cells')
    print(f'Seeds: {len(SEED_CONFIGS)}')
    print()

    optimizer = bayes_mc.PhaseOptimizer(
        phase_name='v27_phase_f',
        param_space=PARAM_SPACE,
        base_config=PHASE_F_BASE,
        seeds=SEED_CONFIGS,
        budget=int(os.environ.get('SWEEP_BUDGET', '30')),
        batch_size=3, patience=2, epsilon=0.02, bandwidth=0.30,
        kappa_init=2.0, kappa_final=1.0, windows=SCREENING_WINDOWS,
    )

    best, evaluated = optimizer.run()
    bayes_mc.print_summary('v27_phase_f', best, evaluated)

    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    out_path = os.path.join(os.path.dirname(__file__), 'phase_f_top_candidates.json')
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
