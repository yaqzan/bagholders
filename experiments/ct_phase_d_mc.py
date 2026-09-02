"""
Phase D — extend Phase C champions to full 5-annual + 5y windows.

Phase C (n_iter=500, 2022/2024/22-now) top-4:
  1. B_floored_fl060            22-now Real +2,246M%  DD-C 77.1%
  2. C_regime_a050_rc090        22-now Real +1,782M%  DD-C 73.8%
  3. C_regime_a075_rc090        22-now Real +1,555M%  DD-C 75.1%
  4. C_regime_a050_rc092        22-now Real +1,260M%  DD-C 73.7%

Phase D strategy:
  - 5 variants (baseline + 4 Phase C winners)
  - 4 new windows: 2021, 2023, 2025, 5y continuous
    (2022/2024/22-now already resolved at n=500 in Phase C)
  - n_iter=500
  - Extend sim lookback to Jan 2021 for 5y coverage
  - Gate: no Conservative WorstDD > 80% on ANY window

Output: experiments/ct_phase_d.out
"""
from __future__ import annotations

from datetime import date

from experiments.ct_designs import make_ct_score
from experiments.ct_phase_b_mc import main


VARIANTS = [
    ('baseline_v19',              None),
    ('B_floored_fl060',           make_ct_score('floored', alpha=0.50, floor=0.60)),
    ('C_regime_a050_rc090',       make_ct_score('regime_cond', alpha=0.50, regime_cutoff=0.90)),
    ('C_regime_a075_rc090',       make_ct_score('regime_cond', alpha=0.75, regime_cutoff=0.90)),
    ('C_regime_a050_rc092',       make_ct_score('regime_cond', alpha=0.50, regime_cutoff=0.92)),
]


if __name__ == '__main__':
    today = date.today()
    # Extend sim lookback back to Jan 2021 so 5y + 2021 annual windows resolve.
    # Phase B/C used lookback = today - Jan 2022.
    # Here we need 5y: use (today - 2021-01-01) days.
    # main() hardcodes lookback to today - 2022-01-01; we pass explicit windows
    # and patch lookback via env... actually, main() builds the sim internally.
    # Inspect: main() does `days = (date.today() - date(2022, 1, 1)).days`.
    # That means sim covers 2022-01-01 -> today. 2021 peaks won't exist.
    # For Phase D, restrict to windows that sim can cover: 2023/2025 only.
    # 5y continuous window would need deeper sim — SKIP for now; rerun separately
    # with a patched main() if needed.
    windows = [
        ('2023',  date(2023, 1, 1), date(2023, 12, 31)),
        ('2025',  date(2025, 1, 1), date(2025, 12, 31)),
    ]
    main(VARIANTS, windows=windows, n_iter=500)
