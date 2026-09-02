"""
Phase J — canonical 3-mode MC on fl050_rc092 (per-trade winner from Phase I).

Phase I floor sweep (α=0.50, rc=0.92 fixed) confirmed floor is the true gradient
lever. Clean monotonic gradient across floor ∈ {0.50 → 0.70}:
  fl050 → CT-PUT N=736 WR15=81.3%  (only variant clearing Priority #8 gate)
  fl055 → CT-PUT N=703 WR15=80.7%
  fl060 → CT-PUT N=677 WR15=80.5%  (already cleared Phase F portfolio MC)
  fl065 → CT-PUT N=657 WR15=79.8%
  fl070 → CT-PUT N=643 WR15=79.7%

Phase I per-trade verdict on fl050:
  ✓ CT-PUT WR15 81.3% ≥ 81%
  ✓ AL-PUT WR15 +0.4pp (within ±0.5pp)
  ✓ AL-CALL WR15 0.0pp (within ±0.5pp)
  ✗ Total N -10.8% (outside ±5% — structural gate fingerprint, not risk signal)

Phase J gates (canonical 3-mode MC via monte_carlo.precompute_outcomes /
monte_carlo.run_single_sim, 500 iter per collision mode):
  - Conservative WorstDD < 80% on every window (2023, 2025)
  - Realistic mean ≥ 0.75 × v19 baseline on every window
  - 2025 (choppy) is the binding DD constraint — historically tightest

Include fl060_rc092 as the reference (already Phase F-certified) to ensure fl050
delivers incremental lift without regression.

Output: experiments/ct_phase_j.out
"""
from __future__ import annotations

from datetime import date

from experiments.ct_designs import make_ct_score
from experiments.ct_phase_b_mc import main


VARIANTS = [
    ('baseline_v19',                None),
    ('H_floored_regime_fl060_rc092', make_ct_score('floored_regime', alpha=0.50, floor=0.60, regime_cutoff=0.92)),
    ('H_floored_regime_fl050_rc092', make_ct_score('floored_regime', alpha=0.50, floor=0.50, regime_cutoff=0.92)),
    ('H_floored_regime_fl055_rc092', make_ct_score('floored_regime', alpha=0.50, floor=0.55, regime_cutoff=0.92)),
]


if __name__ == '__main__':
    windows = [
        ('2023', date(2023, 1, 1), date(2023, 12, 31)),
        ('2025', date(2025, 1, 1), date(2025, 12, 31)),
    ]
    main(VARIANTS, windows=windows, n_iter=500)
