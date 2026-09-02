"""
Phase C — fine-tune MC sweep around Phase B winners.

Phase B top-4 (all pass 80% DD-C floor, all beat baseline_v19 on 22-now Realistic):
  1. B_floored_fl060            22-now Real +2,336M%  (+3.55x baseline)
  2. C_regime_a050_rc090        22-now Real +1,657M%  (+2.52x baseline)
  3. C_regime_a050_rc095        22-now Real +1,157M%  (+1.76x baseline)
  4. B_floored_fl050            22-now Real   +951M%  (+1.45x baseline)

Phase C strategy:
  - Design B (floored): fine grid around floor=0.60 with alpha=0.50 fixed
  - Design C (regime_cond): grid of alpha x rc around (0.50, 0.90)
  - baseline_v19 for reference
  - 500 iter (2x Phase B precision) on same 3 windows (2022, 2024, 22-now)

Output: experiments/ct_phase_c.out
"""
from __future__ import annotations

from experiments.ct_designs import make_ct_score
from experiments.ct_phase_b_mc import main


VARIANTS = [
    ('baseline_v19',              None),

    # Design B family — fine grid around fl060 champion
    ('B_floored_fl055',           make_ct_score('floored', alpha=0.50, floor=0.55)),
    ('B_floored_fl060',           make_ct_score('floored', alpha=0.50, floor=0.60)),
    ('B_floored_fl065',           make_ct_score('floored', alpha=0.50, floor=0.65)),
    ('B_floored_fl070',           make_ct_score('floored', alpha=0.50, floor=0.70)),

    # Design C family — 2D grid around (a050, rc090)
    ('C_regime_a040_rc090',       make_ct_score('regime_cond', alpha=0.40, regime_cutoff=0.90)),
    ('C_regime_a050_rc085',       make_ct_score('regime_cond', alpha=0.50, regime_cutoff=0.85)),
    ('C_regime_a050_rc090',       make_ct_score('regime_cond', alpha=0.50, regime_cutoff=0.90)),
    ('C_regime_a050_rc092',       make_ct_score('regime_cond', alpha=0.50, regime_cutoff=0.92)),
    ('C_regime_a075_rc090',       make_ct_score('regime_cond', alpha=0.75, regime_cutoff=0.90)),
]


if __name__ == '__main__':
    main(VARIANTS, n_iter=500)
