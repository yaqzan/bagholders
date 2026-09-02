# 45-DTE Sprint Probe — Verdict (FABLE, 2026-07-14)

**Tier: SCREEN, N=100** (task 609). `experiments/apex_dte_dd/results/sweep_drill_dte45.json`,
113 monthly-rolled 2y windows, 45-DTE (`nominal_cal_dte=45`, `hold_cal_days=40`), $50k start.

| cell | P(2x) | median days-to-2x | median compound | worst DD | collapse |
|---|---:|---:|---:|---:|---:|
| flat_n4 | 57.2% | 145d | — | 79.6% | 0.0% |
| flat_n10 | 60.2% | — | +41.2% | 64.8% | 0.0% |
| cascade_ref | — | — | +103.7% | 72.3% | 0.0% |

Collapse = 0 in every cell.

## Verdict

Vs the 30-DTE evidence base (P2x 72%, median ~113d, and task 610's n10 held-window dominance),
45-DTE Pareto-beats 30-DTE on **ZERO** of the pre-registered axes {compound, DD, collapse}
(collapse ties; everything else worse or equal).

Per gameplan P3.3's own kill clause: **the DTE axis is CLOSED PERMANENTLY.**

**Dormant re-open** (kept verbatim from the gameplan row): one-shot re-run if an IV-aware premium
engine ever ships — the (GAMMA_AWARE+IV_MODEL) pair is currently PARKED, so this condition is
dormant.
