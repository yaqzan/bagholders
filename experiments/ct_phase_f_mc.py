"""
Phase F — confirm Phase E fl060 hybrids across 2023/2025 windows.

Phase E (n_iter=500, 2022/2024/22-now) identified two ship candidates:
  - H_floored_regime_fl060_rc090   22-now Real +4,044M%  DD-C 74.4%
  - H_floored_regime_fl060_rc092   22-now Real +4,560M%  DD-C 74.7%  <- primary winner

Phase F: run both + baseline_v19 on 2023 + 2025 (the recovery + choppy years
Phase E didn't cover). Gates:
  - Cons WorstDD < 80% on every window
  - Realistic mean beats baseline_v19 on both windows
  - Bull-year preservation confirmed (tested in 2024 at Phase E)
  - Bear-year dominance confirmed (tested in 2022 at Phase E, 2× baseline)

If both clear 2023 + 2025 cleanly, primary winner ships.

Note: sim lookback is main()'s default (today - 2022-01-01), which covers
2023 and 2025 but not 2021. 5y continuous window would need a deeper sim
patch — deferred to a separate run if needed.

Output: experiments/ct_phase_f.out
"""
from __future__ import annotations

from datetime import date

from experiments.ct_designs import make_ct_score
from experiments.ct_phase_b_mc import main


VARIANTS = [
    ('baseline_v19',                None),
    ('H_floored_regime_fl060_rc090', make_ct_score('floored_regime', alpha=0.50, floor=0.60, regime_cutoff=0.90)),
    ('H_floored_regime_fl060_rc092', make_ct_score('floored_regime', alpha=0.50, floor=0.60, regime_cutoff=0.92)),
]


if __name__ == '__main__':
    windows = [
        ('2023', date(2023, 1, 1), date(2023, 12, 31)),
        ('2025', date(2025, 1, 1), date(2025, 12, 31)),
    ]
    main(VARIANTS, windows=windows, n_iter=500)
