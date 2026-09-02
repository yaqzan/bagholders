"""Phase 2: Breadth-adaptive put TP / SL.

Inherits Phase 1's optimal PUT_TP / PUT_SL as the 'base' values. Now sweeps the
breadth-adaptive switch:

  PUT_BREADTH_MODE in {'none', 'invert', 'same'}
  PUT_BREADTH_THRESHOLD in {30, 40, 50, 60, 70}
  PUT_TP_STRESS offset from base ∈ {-0.05, 0.0, +0.05}
  PUT_SL_STRESS offset from base ∈ {-0.05, 0.0, +0.05}

mode='none' = current production (no switch); threshold ignored.
mode='invert' = widen exits when breadth >= threshold (bullish tape = put headwind).
mode='same' = widen when breadth <= threshold (mirror calls; baseline control).

Reads Phase 1's best from experiments/bayes_logs/phase_1_put_tpsl.jsonl.
"""

import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import (
    PhaseOptimizer, DEFAULT_CONFIG, print_summary,
)

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def load_phase1_best():
    path = os.path.join(LOG_DIR, 'phase_1_put_tpsl.jsonl')
    if not os.path.exists(path):
        print(f'Phase 1 log missing: {path} — falling back to production defaults')
        return {'PUT_TP': 0.30, 'PUT_SL': -0.20}
    best = None
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            rec = json.loads(line)
            if rec.get('event') == 'eval':
                if best is None or rec['utility'] > best['utility']:
                    best = rec
    if best is None:
        return {'PUT_TP': 0.30, 'PUT_SL': -0.20}
    return best['params']


def main():
    phase1 = load_phase1_best()
    tp_base = phase1.get('PUT_TP', 0.30)
    sl_base = phase1.get('PUT_SL', -0.20)
    print(f'[phase2] inheriting Phase 1 base: PUT_TP={tp_base:+.2f}  PUT_SL={sl_base:+.2f}')

    # Candidate stress values (base + offset, clipped to [0.15, 0.45] / [-0.40, -0.05])
    def _clip_tp(v): return round(max(0.15, min(0.45, v)), 2)
    def _clip_sl(v): return round(max(-0.40, min(-0.05, v)), 2)
    tp_stress_vals = sorted({_clip_tp(tp_base + dx) for dx in (-0.05, 0.0, 0.05, 0.10)})
    sl_stress_vals = sorted({_clip_sl(sl_base + dx) for dx in (-0.05, 0.0, 0.05, 0.10)})

    param_space = {
        'PUT_BREADTH_MODE':      ['none', 'invert', 'same'],
        'PUT_BREADTH_THRESHOLD': [30, 40, 50, 60, 70],
        'PUT_TP_STRESS':         tp_stress_vals,
        'PUT_SL_STRESS':         sl_stress_vals,
    }

    seeds = [
        # baseline (no switch)
        {'PUT_BREADTH_MODE': 'none',   'PUT_BREADTH_THRESHOLD': 50,
         'PUT_TP_STRESS': _clip_tp(tp_base), 'PUT_SL_STRESS': _clip_sl(sl_base)},
        # invert at 50 with modest widening
        {'PUT_BREADTH_MODE': 'invert', 'PUT_BREADTH_THRESHOLD': 50,
         'PUT_TP_STRESS': _clip_tp(tp_base + 0.05), 'PUT_SL_STRESS': _clip_sl(sl_base - 0.05)},
        # invert at 60 (stricter trigger)
        {'PUT_BREADTH_MODE': 'invert', 'PUT_BREADTH_THRESHOLD': 60,
         'PUT_TP_STRESS': _clip_tp(tp_base + 0.05), 'PUT_SL_STRESS': _clip_sl(sl_base - 0.05)},
        # same (mirror calls) — baseline control
        {'PUT_BREADTH_MODE': 'same',   'PUT_BREADTH_THRESHOLD': 50,
         'PUT_TP_STRESS': _clip_tp(tp_base + 0.05), 'PUT_SL_STRESS': _clip_sl(sl_base - 0.05)},
        # invert aggressive widening
        {'PUT_BREADTH_MODE': 'invert', 'PUT_BREADTH_THRESHOLD': 50,
         'PUT_TP_STRESS': _clip_tp(tp_base + 0.10), 'PUT_SL_STRESS': _clip_sl(sl_base - 0.10)},
    ]

    base_cfg = dict(DEFAULT_CONFIG)
    base_cfg['PUT_TP'] = tp_base
    base_cfg['PUT_SL'] = sl_base

    opt = PhaseOptimizer(
        phase_name='2_put_breadth',
        param_space=param_space,
        base_config=base_cfg,
        seeds=seeds,
        budget=15,
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.25,
    )
    best, evaluated = opt.run()
    print_summary('2_put_breadth', best, evaluated)


if __name__ == '__main__':
    main()
