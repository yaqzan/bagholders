# v69 Honest-Substrate Portfolio Retune (Stage 2/3) — RECOMMEND ONLY

Active scoring: v69 (commit 8b59206c) — weekly-transition-blend removed the weekly
look-ahead bug that inflated the backtest. Honest 75+ option-TP15 edge ~51% (+6pp over
45% call BE); put TP ~25-44% (at/below 36% put BE).

## Files
- driver.py        : subprocess-per-candidate MC harness (env-var param passing; required
                     because monte_carlo.py freezes TP/SL sigma globals at import, and the
                     per-candidate precompute runs in the parent). Streams child stdout to
                     _childlogs/<tag>.log. Parses SUMMARY table + per-window seeded line.
- run_baseline.py  : honest baseline at current params, N x 8 windows.
- sweep.py         : DD-primary coarse sweep (sizing candset, barriers candset).
- make_barrier_cands.py : builds Stage-2 barrier candidates on a survivable sizing base.
- validate.py      : N=500 x 8-window finalist validation + T1-T7 comparison.

## Key finding (N=60/100, current params on honest v69)
The CURRENT params (tuned on the inflated ~63% edge) are STRUCTURALLY BROKEN on honest v69:
  2022 WorstDD ~87%, P(collapse) ~3%; 5y WorstDD ~84%, P(collapse) ~100%.
The honest edge is too thin for the current aggression. A conservative RE-SIZE is mandatory.
