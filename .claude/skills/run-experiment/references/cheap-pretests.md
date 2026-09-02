# Cheap decisive pre-tests — catalog

Referenced from `/run-experiment` Phase 1. All reusable, all read-only /
no-MC, all meant to kill a bad hypothesis in minutes before you build
anything expensive. Pick the one that matches your hypothesis shape.

- **EV-by-band on the existing tape** — bucket the candidate feature and
  read mean option-pnl per band on stored rows. No rescore needed. This is
  the fastest possible test and should usually run first.

- **Dropped-rows apex-WR test** — for "smooth this cliff / fidelity-fix this
  component" ideas: compute the apex WR of the rows the fix would DROP from
  the 75+ gate. If dropped-rows WR > stable-rows WR, the cliff is a
  load-bearing signal, not noise — do not smooth it. This is the house rule
  established in `experiments/score_fidelity/` (the MACD-phase-discontinuity
  audit): the codebase already smoothed the RSI range-gates but kept the RSI
  breakout/divergence pushes for exactly this reason (the v42-load-bearing
  bands), and the same test correctly stopped a MACD-cliff smoothing attempt
  because the dropped rows ran at 75-80% apex WR vs a 73% stable-rows
  baseline.

- **Component-tercile sign-consistency across regime classifiers** — for
  "reweight component X in regime Y" ideas: compute the hi-tercile-vs-base
  apex EV of the component split by MULTIPLE regime classifiers
  independently (e.g. `regime_composite` AND `VIX`, not just one). A real
  regime-conditioned signal has a LARGE, CONSISTENT spread across
  classifiers; a sign flip between classifiers is noise, not signal. This
  killed `experiments/regime_reweight/` (2026-06-25): TREND, the motivating
  axis, was the flattest component (±0.8pp) and the noisiest cell (`bb`)
  flipped sign between the composite and VIX classifiers within the same
  nominal regime — exactly the noise-floor pattern this test is designed to
  catch.

- **Day-of-week look-ahead split** — for any weekly-feature cohort: split
  the cohort's option-TP by day-of-week. If Monday's rate is much higher
  than Friday's (Friday ≈ unconditional baseline), the "edge" is look-ahead
  contamination from a recalc reading a completed weekly bar mid-week, not
  real alpha. Proof case: the pre-v69 `w_mom∈[5,8)` 70-74 cohort ran optTP15
  68.3% on Monday signals vs 49.75% on Friday signals — the edge vanishes
  exactly as the week becomes complete.

- **All-levers-off orthogonality slice** — for any new Stage-3 DD-lever
  idea: mine the DD-active subset of the MC tape with every shipped lever
  disabled (as of 2026-07, look up the current lever set — historically
  RXDD/SVR/MWDD/TVDD/BDIV/F3F). If the candidate's edge only shows up
  because a shipped lever happens to be off, it's redundant with that
  lever, not a new orthogonal signal (the "G44" pattern: a candidate
  low-EV cohort that inverts to good exactly where the overlapping shipped
  lever is off). Also demand sign-stability across years/regimes in this
  slice — a lever whose sign flips year-to-year in the orthogonal slice is
  not a lever.

- **N-escalation stability** — does the effect survive 100→300→500? A
  cohort z or an MC delta that looks real at N=100 routinely reverses at
  N=300-500 (documented MC noise floor: ±5-8pp DD, 1.6-1.8× compound swing
  at N=300 single-window). Never trust an N=100/150 "win" as a ship
  candidate; escalate before believing it.

## When none of these apply

If the hypothesis doesn't fit any of the above shapes, the fallback is still
cheap: a 2-arm `ScoreSimulator` diff on a stride sample (every Nth symbol,
not the full universe) can usually validate or kill a hypothesis in under an
hour before committing to a full sharded ReSim campaign. This was the
decisive first step that killed the TA-component-removal probe
(`experiments/weather_components/`, the "v75-lean" TA-zeroing rescore) before
any recalc/MC campaign was spent on it.
