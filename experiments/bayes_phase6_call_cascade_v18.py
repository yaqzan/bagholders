"""Phase 6: Call cascade + MaxPos re-sweep on v18.

The current call cascade (85+=15%, 80-84=12%, 75-79=12%, 70-74=5%) + MaxPos=14
was validated on v17. v18 shipped the asymmetric MACD gate and asymmetric
weekly scaling, which shifted raw call WR slightly (85+ +0.1pp, 80+ +0.2pp,
95+ at 90.0%). Re-sweep to confirm the shape still holds, or find a better one.

Put side locked at production (TP=30, SL=-20, cascade 15/12/12, PUT_BREADTH_MODE=none).
Calls breadth-adaptive TP/SL locked at production (h30/35, h35/40).

Budget 18 evals, ~24 min at ~80s/eval on 6 windows x realistic-only.
"""

import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from experiments.bayes_mc import (
    PhaseOptimizer, DEFAULT_CONFIG, print_summary,
)


def main():
    param_space = {
        'TIER_ALLOC.top':      [0.12, 0.15, 0.18, 0.20, 0.22],  # 85+
        'TIER_ALLOC.mid':      [0.10, 0.12, 0.15, 0.18],        # 80-84
        'TIER_ALLOC.low':      [0.08, 0.10, 0.12, 0.15],        # 75-79
        'TIER_ALLOC.overflow': [0.00, 0.03, 0.05, 0.07],        # 70-74
        'MAX_POSITIONS':       [12, 14, 16, 18],
    }

    seeds = [
        # current production (v18)
        {'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.12, 'TIER_ALLOC.low': 0.12,
         'TIER_ALLOC.overflow': 0.05, 'MAX_POSITIONS': 14},
        # top-heavy
        {'TIER_ALLOC.top': 0.20, 'TIER_ALLOC.mid': 0.12, 'TIER_ALLOC.low': 0.10,
         'TIER_ALLOC.overflow': 0.03, 'MAX_POSITIONS': 14},
        # flat
        {'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.12, 'TIER_ALLOC.low': 0.12,
         'TIER_ALLOC.overflow': 0.05, 'MAX_POSITIONS': 14},
        # bigger pool + trim tiers
        {'TIER_ALLOC.top': 0.12, 'TIER_ALLOC.mid': 0.10, 'TIER_ALLOC.low': 0.10,
         'TIER_ALLOC.overflow': 0.05, 'MAX_POSITIONS': 18},
        # low-tier emphasized (75-79 is the compounding engine)
        {'TIER_ALLOC.top': 0.15, 'TIER_ALLOC.mid': 0.12, 'TIER_ALLOC.low': 0.15,
         'TIER_ALLOC.overflow': 0.05, 'MAX_POSITIONS': 14},
        # no overflow (70-74 excluded)
        {'TIER_ALLOC.top': 0.18, 'TIER_ALLOC.mid': 0.15, 'TIER_ALLOC.low': 0.12,
         'TIER_ALLOC.overflow': 0.00, 'MAX_POSITIONS': 14},
    ]

    base_cfg = dict(DEFAULT_CONFIG)  # puts at production, calls breadth-adaptive at production

    opt = PhaseOptimizer(
        phase_name='6_call_cascade_v18',
        param_space=param_space,
        base_config=base_cfg,
        seeds=seeds,
        budget=18,
        batch_size=3,
        patience=2,
        epsilon=0.02,
        bandwidth=0.22,
    )
    best, evaluated = opt.run()
    print_summary('6_call_cascade_v18', best, evaluated)


if __name__ == '__main__':
    main()
