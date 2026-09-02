# MACD Gradient Gate Re-Investigation — NULL RESULT

**Date:** 2026-05-05
**Trigger:** User request to re-investigate MACD suppression around CRM Apr 20-24,
preferring gradient magnifier over the production cliff threshold (`PUT_MACD_GATE = 45`).

## Result

**NULL across 24 variants in 3 phases.** The current production cliff at
`pre_no_macd < 45 -> w_macd = 0` is at or near the per-trade optimum on 5y v35.
No tested gradient shape (symmetric widen, directional, hybrid, sigmoid)
produces meaningful improvement over the cliff.

## CRM Apr 20-22 framing

User cited CRM around April 20-24 as a case where MACD suppression seemed
inadequate. Investigation (`weight_info`):

- Apr 21: trend=4, bb=19, rsi=43, macd=73, stoch=3 -> **w_macd already 0.0**
- pre-WCF score = 25 (deeply bearish put correctly identified)
- WCF lift then pulled to 49 via `w_adj=0.8 ∈ (-17, +1)` cohort

**The MACD cliff fired correctly on CRM.** The score=49 outcome was not a MACD
gate failure — it was the v27 WCF lift muting an otherwise-valid put because
the weekly was weakly bullish. Any further MACD-gate changes don't address
this case.

## Phase 1 — Symmetric widening (8 variants, 5y)

`g(pre_no_macd) = 1` below LO, `0` above HI, linear in between. Tested
LO/HI ∈ {(45,55), (45,60), (45,65), (50,60)} plus sigmoids centered 50/55.

All variants flat-to-slight-negative on puts, calls 85+ regress 0.4-0.9pp.
**Mechanism:** symmetric gradient dampens MACD even when MACD aligns bearish,
removing correct downward pressure that the production cliff also doesn't have
but happens to dominate via the broader 45 threshold. Output:
[`widen_5y.out`](widen_5y.out).

## Phase 2 — Pure directional and weak hybrid (8 variants, 5y)

Pure directional replacing cliff:
`g = bearish_setup × contradicting × STRENGTH`
where `bearish_setup = (50-pre_no_macd)/SETUP_K`, `contradicting = (macd-50)/MACD_K`.

Weak hybrid (cliff + directional layered on top): same formula above 45.

Pure directional admits 6-10% fewer puts (cliff was muting MACD on more cases
than just contradicting). Weak hybrids fired barely above the cliff because
`bearish_setup` saturates at pre_no_macd=50 (peak 0.33 strength in 45-50 zone).
Output: [`directional_5y.out`](directional_5y.out).

## Phase 3 — Cliff + corrected directional ramp (8 variants, 5y)

`g = max((HI - pre_no_macd) / (HI - LO), 0) × max((macd - 50) / MACD_K, 0) × STRENGTH`
above 45 cliff. Tested HI ∈ {55, 60, 65}, MACD_K ∈ {10, 15, 20}, STRENGTH ∈ {0.5, 1.0}.

| Variant | <25 N | <25 WR15 | <15 N | <15 WR15 | <5 N | <5 WR15 | 85+ N | 85+ WR15 | 75+ N | 75+ WR15 |
|---|---|---|---|---|---|---|---|---|---|---|
| binary45 | 6890 | 74.6% | 1813 | 76.1% | 291 | 76.9% | 381 | 77.7% | 3589 | 74.1% |
| hi55_mk15_s10 | 6949 | 74.7% | 1813 | 76.1% | 291 | 76.9% | 375 | 77.3% | 3526 | 74.1% |
| hi60_mk15_s10 | 6963 | 74.7% | 1813 | 76.1% | 291 | 76.9% | 370 | 77.0% | 3416 | 74.0% |
| hi60_mk15_s05 | 6905 | 74.6% | 1811 | 76.1% | 291 | 76.9% | 374 | 77.3% | 3477 | 74.0% |
| hi60_mk10_s10 | 6963 | 74.7% | 1814 | 76.1% | 291 | 76.9% | 370 | 77.0% | 3416 | 74.0% |
| hi60_mk20_s10 | 6953 | 74.8% | 1813 | 76.1% | 291 | 76.9% | 370 | 77.0% | 3419 | 74.0% |
| hi65_mk15_s10 | 6971 | 74.7% | 1813 | 76.1% | 291 | 76.9% | 357 | 77.6% | 3273 | 74.0% |

**`<15 N=1813, <15 WR15=76.1%, <5 N=291, <5 WR15=76.9%` are EXACT-IDENTICAL across
all variants** — the deep-bear cohort is fully saturated by the cliff at 45.

The widening only touches the borderline 26-35 zone. Adding ~60-80 puts there
has WR ≈ 75% — same as cliff-cohort. No alpha concentration in that zone.

Calls 85+ marginally hurt (-0.4 to -0.7pp WR15 on N reduction of 1.6-2.9%) —
gate bleeds onto borderline-call signals where MACD was lifting them past 85.

Output: [`phase3_5y.out`](phase3_5y.out).

## Why all gradients fail

1. **Cliff at 45 already captures the high-EV cohort.** v18's PUT_MACD_GATE=45
   ship validation (5y +6.5pp on <15) showed MACD-suppressing alpha is
   concentrated where pre_no_macd is materially bearish (well below 50).
   That cohort doesn't grow when threshold is widened.

2. **Borderline 45-60 pre_no_macd × macd>50 zone has no alpha concentration.**
   ~2,500 candidate signals exist; widening adds ~60-80 to put cohort with
   ~75% WR — same as production. WR doesn't lift, just shifts a few converts.

3. **Symmetric softening BELOW 45 was already tested in 2026
   (`experiments/macd_smooth_gate_*.out`)** — same null. Adding widening
   ABOVE 45 (this round) doesn't change the conclusion.

4. **Other v35 score-stage mechanisms saturate before the gradient can fire:**
   ext-focal dampener, capitulation, exhaustion, mis_stress, WCF lift,
   CWCF dampener, EARN_BOOST. By the time MACD gate runs, much of the score
   trajectory is already determined by 5 other components and weekly.

5. **The CRM-style "MACD seems suppressed" cases are actually WCF lift problems**,
   not MACD gate problems. Cliff fires correctly; lift then over-corrects.

## Recommendation

**Do NOT ship a MACD gradient.** Production cliff at `PUT_MACD_GATE = 45` is
the optimum. Add to `known-issues.md` "CLOSED — NULL RESULTS" and "What NOT
to do" so future investigations don't re-test the same axes.

If the underlying motivation is "borderline puts are getting suppressed",
the right axis is the **WCF lift gate** (currently `overall < 28 ∧ w_adj > -17`),
not the MACD gate. CRM's score=49 came from WCF lift on `w_adj = 0.8` — a
weekly-confirmed-as-weakly-bullish but technically-bearish state. A targeted
narrowing of the WCF lift `w_adj` admission threshold would address the CRM
case directly. That investigation is separate and not pursued here.

## Artifacts

- `widen_sweep.py` / `widen_5y.out` — Phase 1 (symmetric widening)
- `directional_sweep.py` / `directional_5y.out` — Phase 2 (directional, weak hybrid)
- `phase3_sweep.py` / `phase3_5y.out` — Phase 3 (cliff + corrected ramp)
