# Execution-Pessimism Certification (P1.5) — read (FABLE, 2026-07-13)

**Certification, NOT calibration.** N=300 per (arm x window x profile), 9 windows incl 2020_crash,
arms {SLIP_SL −0.03, TP-fill-miss 3%/7%, entry-fill-miss 5%, next-open anchor (sensitivity),
combined-pessimist} vs baseline. Queue task 607; full matrix: results/matrix.txt.

## CORE (held default): **CERTIFIED — collapse=0 under every arm including combined-pessimist.**
- Single knobs: DD deltas ≤ +4.3pp everywhere; collapse 0.
- Combined-pessimist: collapse **0.0 on all nine windows**; DD +10.8pp (22-now) / +10.1pp (5y) with
  the rest ≤ +7pp and 2020_crash actually −13.4pp. The long-window DD degradation is visibly
  dominated by the next-open-anchor component (+9.1/+8.4pp alone) — the known ~−1.2pp entry-realism
  methodology item, not a lever.
- **Disposition answer: NO shipped lever/param keep-decision flips.** No N=500 re-validation
  triggered. Margin note recorded: the asymmetric-cost canon carries roughly +10pp of Core's
  reported long-window DD headroom under fully pessimistic execution — the P3.7 real-fill loop is
  the instrument that will arbitrate this against reality (report-only until N≥30 fills).
- Curiosity worth keeping: next-open anchor IMPROVES crash-window DD (2020_crash −16.2pp) — in
  crash tape, opens gap down, so next-open entries are cheaper. Consistent with the entry-timing
  canon (gap direction is regime-dependent; the unconditional next-open haircut stands).

## APEX (opt-in sprint): **NOT execution-robust — the collapse budget is execution-conditional, now quantified.**
- entry-fill-miss 5% ALONE: collapse 3.3% (2022) / 1.0% (22-now) / 0.3% (5y).
- TP-fill-miss 7% ALONE: collapse **11.0%** (22-now) / 0.7% (5y) — the sprint's exit mechanism
  failing is itself a ruin channel.
- Combined-pessimist: collapse **25.7% (22-now) / 39.3% (5y)**; DD +19.4pp (2021), +14.3pp
  (2020_crash).
- Read: the sprint's ~2.4% user-approved collapse budget holds ONLY under the asymmetric-cost
  canon's fill assumptions. Long-window rates again partly reflect the held-form misuse (see
  deep_crash_screen RESULTS — held-Apex is ruin regardless), but TP-fill-miss hitting 11% on 22-now
  is within-sprint-relevant. This joins the deep-screen finding as P0.3/rotation decision context:
  execution quality and stop-at-2x discipline dominate the DTE choice.

## Standing consequences
1. No lever re-validation (no flip). 2. Core margin note + Apex execution-conditionality recorded
here and in the gameplan row. 3. The real-fill loop (P3.7, live since `4c8d462ef`) is the named
falsifier for the canon; a materially-worse realized read re-opens the execution-conditional
levers per its charter. 4. SCREEN-tier numbers; nothing tuned, nothing gated.
