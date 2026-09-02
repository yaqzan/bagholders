"""Phase 3: Put cascade allocation (3 tiers <=15 / 16-20 / 21-25).

Inherits Phase 1 + 2 winners. Sweeps per-tier allocation %:

  put_alloc_top (<=15):  {0.10, 0.12, 0.15, 0.18, 0.20}
  put_alloc_mid (16-20): {0.08, 0.10, 0.12, 0.15, 0.18}
  put_alloc_low (21-25): {0.05, 0.08, 0.10, 0.12, 0.15}

5×5×5 = 125 grid. Bayesian will evaluate ~15 before converging.

Reads Phase 1+2 best from logs; nests PUT_TIER_ALLOC dict via 'PUT_TIER_ALLOC.*'
dotted-key syntax handled by PhaseOptimizer._full_config.
"""

import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import (
    PhaseOptimizer, DEFAULT_CONFIG, print_summary,
)

LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')


def _load_best(phase_file):
    path = os.path.join(LOG_DIR, phase_file)
    if not os.path.exists(path):
        return None
    best = None
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            rec = json.loads(line)
            if rec.get('event') == 'eval':
                if best is None or rec['utility'] > best['utility']:
                    best = rec
    return best['params'] if best else None


def main():
    p1 = _load_best('phase_1_put_tpsl.jsonl') or {'PUT_TP': 0.30, 'PUT_SL': -0.20}
    p2 = _load_best('phase_2_put_breadth.jsonl') or {
        'PUT_BREADTH_MODE': 'none', 'PUT_BREADTH_THRESHOLD': 50,
        'PUT_TP_STRESS': p1['PUT_TP'], 'PUT_SL_STRESS': p1['PUT_SL'],
    }
    print(f'[phase3] inheriting phase 1: {p1}')
    print(f'[phase3] inheriting phase 2: {p2}')

    param_space = {
        'PUT_TIER_ALLOC.put_top': [0.10, 0.12, 0.15, 0.18, 0.20],
        'PUT_TIER_ALLOC.put_mid': [0.08, 0.10, 0.12, 0.15, 0.18],
        'PUT_TIER_ALLOC.put_low': [0.05, 0.08, 0.10, 0.12, 0.15],
    }

    seeds = [
        # current production
        {'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
        # flat (all equal)
        {'PUT_TIER_ALLOC.put_top': 0.12, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.12},
        # top-heavy
        {'PUT_TIER_ALLOC.put_top': 0.20, 'PUT_TIER_ALLOC.put_mid': 0.12, 'PUT_TIER_ALLOC.put_low': 0.08},
        # conservative
        {'PUT_TIER_ALLOC.put_top': 0.12, 'PUT_TIER_ALLOC.put_mid': 0.10, 'PUT_TIER_ALLOC.put_low': 0.08},
        # aggressive across board
        {'PUT_TIER_ALLOC.put_top': 0.18, 'PUT_TIER_ALLOC.put_mid': 0.15, 'PUT_TIER_ALLOC.put_low': 0.12},
    ]

    base_cfg = dict(DEFAULT_CONFIG)
    base_cfg['PUT_TP'] = p1['PUT_TP']
    base_cfg['PUT_SL'] = p1['PUT_SL']
    base_cfg['PUT_BREADTH_MODE'] = p2['PUT_BREADTH_MODE']
    base_cfg['PUT_BREADTH_THRESHOLD'] = p2['PUT_BREADTH_THRESHOLD']
    base_cfg['PUT_TP_STRESS'] = p2['PUT_TP_STRESS']
    base_cfg['PUT_SL_STRESS'] = p2['PUT_SL_STRESS']

    opt = PhaseOptimizer(
        phase_name='3_put_cascade',
        param_space=param_space,
        base_config=base_cfg,
        seeds=seeds,
        budget=15,
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.22,
    )
    best, evaluated = opt.run()
    print_summary('3_put_cascade', best, evaluated)


if __name__ == '__main__':
    main()
