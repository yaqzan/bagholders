"""Phase 4: MaxPos structure (shared pool vs separate call/put caps).

Inherits phases 1-3 winners. Sweeps:

  MAX_POSITIONS_CALL in {None, 8, 10, 12, 14, 16}   (None = share pool)
  MAX_POSITIONS_PUT  in {None, 4, 6, 8, 10, 12}      (None = share pool)
  MAX_POSITIONS      in {14, 16, 18, 20}             (pool size or upper cap when separate)

To keep the space tractable, we evaluate specific structural schemes rather than
the full cartesian product:

  A) Shared pool baseline: (None, None, MAX_POS in {14, 16, 18, 20})
  B) Separate pools: (call_cap, put_cap) sensible pairs
  C) Soft-cap scheme is implemented by setting put_cap << call_cap so puts only
     fill after calls — simulates 'overflow' behavior for puts.
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
    p3 = _load_best('phase_3_put_cascade.jsonl') or {
        'PUT_TIER_ALLOC.put_top': 0.15, 'PUT_TIER_ALLOC.put_mid': 0.12,
        'PUT_TIER_ALLOC.put_low': 0.12,
    }
    print(f'[phase4] inheriting p1={p1}  p2={p2}  p3={p3}')

    # Parameter space — all 3 treated as separate axes; candidate pairs
    # (None, None) + various (call, put) splits.
    param_space = {
        'MAX_POSITIONS':      [12, 14, 16, 18, 20],
        'MAX_POSITIONS_CALL': [None, 8, 10, 12, 14, 16],
        'MAX_POSITIONS_PUT':  [None, 4, 6, 8, 10, 12],
    }

    # Seeds: mix of shared-pool sizes and structured separate schemes
    seeds = [
        # A — shared pool (current production baseline)
        {'MAX_POSITIONS': 14, 'MAX_POSITIONS_CALL': None, 'MAX_POSITIONS_PUT': None},
        {'MAX_POSITIONS': 18, 'MAX_POSITIONS_CALL': None, 'MAX_POSITIONS_PUT': None},
        # B — separate, balanced
        {'MAX_POSITIONS': 20, 'MAX_POSITIONS_CALL': 12, 'MAX_POSITIONS_PUT': 8},
        {'MAX_POSITIONS': 16, 'MAX_POSITIONS_CALL': 10, 'MAX_POSITIONS_PUT': 6},
        # C — put-heavy (bear-weighted)
        {'MAX_POSITIONS': 20, 'MAX_POSITIONS_CALL': 10, 'MAX_POSITIONS_PUT': 10},
        # D — call-dominant (puts as overflow)
        {'MAX_POSITIONS': 16, 'MAX_POSITIONS_CALL': 14, 'MAX_POSITIONS_PUT': 4},
        # E — large call pool, modest puts
        {'MAX_POSITIONS': 20, 'MAX_POSITIONS_CALL': 14, 'MAX_POSITIONS_PUT': 6},
    ]

    base_cfg = dict(DEFAULT_CONFIG)
    base_cfg['PUT_TP'] = p1['PUT_TP']
    base_cfg['PUT_SL'] = p1['PUT_SL']
    base_cfg['PUT_BREADTH_MODE'] = p2['PUT_BREADTH_MODE']
    base_cfg['PUT_BREADTH_THRESHOLD'] = p2['PUT_BREADTH_THRESHOLD']
    base_cfg['PUT_TP_STRESS'] = p2['PUT_TP_STRESS']
    base_cfg['PUT_SL_STRESS'] = p2['PUT_SL_STRESS']
    base_cfg['PUT_TIER_ALLOC'] = {
        'put_top': p3.get('PUT_TIER_ALLOC.put_top', 0.15),
        'put_mid': p3.get('PUT_TIER_ALLOC.put_mid', 0.12),
        'put_low': p3.get('PUT_TIER_ALLOC.put_low', 0.12),
    }

    opt = PhaseOptimizer(
        phase_name='4_maxpos',
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
    print_summary('4_maxpos', best, evaluated)


if __name__ == '__main__':
    main()
