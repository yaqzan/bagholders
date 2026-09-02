"""
Phase E — hybrid floored_regime design: apply floor only in stressed regimes.

Motivation from Phase C+D:
  - B_floored_fl060           +2,246M% 22-now Real  DD-C 77.1%  (highest compound)
  - C_regime_a050_rc090       +1,782M% 22-now Real  DD-C 73.8%  (most consistent)
  - C_regime_a050_rc092       +1,260M% 22-now Real  DD-C 73.7%  (lowest DD)

The two families address the v22 bull-year regression differently:
  - B floored keeps d dominance EVERYWHERE but clamps the gate cut at `floor`.
  - C regime_cond disables the gate ENTIRELY in bull regimes.

Hypothesis: applying the floored gate only in stressed regimes stacks both
benefits — bull-year TREND compounding is fully preserved (regime_cond path),
and stress-regime gating has a hard floor preventing over-throttle (floored path).

Grid:
  floor ∈ {0.50, 0.60, 0.70}
  rc    ∈ {0.90, 0.92, 0.95}
  alpha fixed at 0.50 (Phase C/D winner)

Plus baseline_v19 reference (run afresh for seed alignment; fl060 / a050_rc090
already resolved at 500 iter in Phase C — cross-reference those directly).

Windows: 2022/2024/22-now (Phase B/C default set — matches comparability frame).
If a hybrid champions, extend to 2023/2025 in a Phase F targeted run.

Output: experiments/ct_phase_e.out
"""
from __future__ import annotations

from experiments.ct_designs import make_ct_score
from experiments.ct_phase_b_mc import main


VARIANTS = [
    ('baseline_v19',                        None),

    # Hybrid grid: floored_regime (floor × rc), alpha=0.50
    ('H_floored_regime_fl050_rc090',        make_ct_score('floored_regime', alpha=0.50, floor=0.50, regime_cutoff=0.90)),
    ('H_floored_regime_fl050_rc092',        make_ct_score('floored_regime', alpha=0.50, floor=0.50, regime_cutoff=0.92)),
    ('H_floored_regime_fl050_rc095',        make_ct_score('floored_regime', alpha=0.50, floor=0.50, regime_cutoff=0.95)),
    ('H_floored_regime_fl060_rc090',        make_ct_score('floored_regime', alpha=0.50, floor=0.60, regime_cutoff=0.90)),
    ('H_floored_regime_fl060_rc092',        make_ct_score('floored_regime', alpha=0.50, floor=0.60, regime_cutoff=0.92)),
    ('H_floored_regime_fl060_rc095',        make_ct_score('floored_regime', alpha=0.50, floor=0.60, regime_cutoff=0.95)),
    ('H_floored_regime_fl070_rc090',        make_ct_score('floored_regime', alpha=0.50, floor=0.70, regime_cutoff=0.90)),
    ('H_floored_regime_fl070_rc092',        make_ct_score('floored_regime', alpha=0.50, floor=0.70, regime_cutoff=0.92)),
    ('H_floored_regime_fl070_rc095',        make_ct_score('floored_regime', alpha=0.50, floor=0.70, regime_cutoff=0.95)),
]


if __name__ == '__main__':
    main(VARIANTS, n_iter=500)
