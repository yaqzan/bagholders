# WVD on v45 — TP15/TP30 + Portfolio MC Report

**Date:** 2026-05-08
**Baseline:** v45 (`56eb1f8`, ETF-filter from breadth + SAW Put U-curve)
**Investigation:** weekly-volume cohort signal → smooth-gradient post-hoc dampener → portfolio MC

---

## TL;DR — what the data says

**Per-trade signal:** strong, replicating, sign-consistent across TP15/TP30 × 1y/3y/5y.
**Portfolio impact:** NEGATIVE. WVD as a *signal dampener* hurts both compound AND DD at 22-now and 5y.

This is the textbook "scoring change with positive per-trade gate that fails canonical MC" pattern
documented in the codebase multiple times (v22, v23, v29 reverts; A050 conviction dampener; etc.).
The per-trade win is real; the slot-displacement cost exceeds it at portfolio scale.

---

## Per-Trade Gate (PASSES — H1-H5 strict, multi-barrier, multi-window)

**5y v45 baseline, 30dte_opt @ w=15 AND w=30, ship-candidate `gl70_gh85_k0.40_p1.5_fs0.10_t55`:**

| Tier | Δ TP15 | Δ TP30 | Δ N | H5 sign |
|---|---:|---:|---:|:---:|
| Call 75+ | **+1.72pp** | +1.57pp | -11.8% | ✓ all positive 6/6 |
| Call 80+ | **+1.55pp** | +0.78pp | -28.4% | ✓ all positive 6/6 |
| Call 85+ | **+2.64pp** | **+2.66pp** | -37.0% | ✓ all positive 6/6 |
| Call 90+ | **+2.45pp** | **+4.41pp** | -29.4% | ✓ all positive 6/6 |
| Put <20 | +0.87pp | +0.78pp | -13.7% | ✓ all positive 6/6 |
| Put <15 | **+3.78pp** | **+3.66pp** | -29.5% | ✓ 5/6 (1y TP30 -0.70 small N) |

**12 of 12 call cells positive across (1y/3y/5y) × (TP15/TP30).** Strong, replicating signal.

---

## Portfolio MC (FAILS — N=200 × 22-now + 5y)

**Configuration:** standard production strategy (30 DTE, F3F breadth, DD soft-band, SAW Put U-curve).
WVD applied at signal-load time via runtime monkey-patch (zero production-code modification).

| Window | Metric | Baseline | WVD | Δ |
|---|---|---:|---:|---:|
| 22-now | Mean Compound | +3.28×10²⁰% | +2.25×10²⁰% | **0.69× (-31%)** |
| 22-now | Med Compound | +4.00×10¹⁹% | +1.20×10¹⁹% | 0.30× (-70%) |
| 22-now | **WorstDD** | **66.2%** | **68.5%** | **+2.3pp ✗** |
| 22-now | MeanDD | 51.1% | 54.3% | +3.2pp |
| 22-now | Call TP% | 63.3% | 63.9% | +0.6pp ✓ |
| 22-now | Call trades | 1262 | 1185 | -6% |
| **5y** | **Mean Compound** | **+3.27×10²⁴%** | **+1.01×10²⁴%** | **0.31× (-69%)** |
| 5y | Med Compound | +1.02×10²³% | +6.03×10²²% | 0.59× (-41%) |
| 5y | **WorstDD** | **67.2%** | **74.5%** | **+7.3pp ✗✗** |
| 5y | MeanDD | 51.7% | 54.8% | +3.1pp |
| 5y | Call TP% | 62.3% | 63.0% | +0.7pp ✓ |
| 5y | Call trades | 1707 | 1622 | -5% |

**Total displacement (across all windows × 200 iters):**
- Calls dampened: 1,750 (most pushed to 70-74 overflow tier where alloc=0%)
- Calls dropped to <70: 6 (negligible)
- Puts lifted: 2,149 (none pushed above 25 — boundary by design)

**P(collapse) = 0% on every cell on both runs.** No safety violations.

### Why WVD as a dampener hurts portfolio

Same lesson the codebase has documented before: **capital velocity > per-trade quality**.

WVD removes ~5-6% of call trades. Each removed trade was net-positive EV (CTP% just slightly
below cohort baseline). The compounding engine relies on *frequent* deployment of capital.
Removing trades reduces compounding cycles enough that the +0.6pp per-trade quality gain
fails to compensate.

The DD damage on 5y (+7.3pp) is harder to explain on volume alone but likely traces to
slot-fill timing. Removing high-force1 calls from random days creates clustered uncovered
windows where the bear-year selloffs hit harder.

**N=200 noise floor:** documented as 1.6-1.8× compound variance. The 5y compound delta
(-69%) is OUTSIDE this noise floor (would be ~±25% in noise alone). The DD delta (+7.3pp)
is also outside noise floor (~±3pp at N=200). The negative result is real, not seed noise.

---

## Recommendation: DO NOT SHIP WVD-Call as a dampener

The per-trade signal is genuinely strong, but the dampener architecture is wrong for this signal.

### Three salvage paths worth exploring (in priority order)

1. **CT-promotion instead of dampening** (highest expected value)
   - Identify the *positive* side of the cohort signal: 75+ calls with NEGATIVE wv_force1
     (no climax, sustained accumulation pattern)
   - Promote those to ULTRA tier instead of demoting climax-vol calls
   - Preserves capital velocity AND captures alpha
   - Cohort data: at calls 75+, Q1 of wv_force1 (negative) WR15 = 65.2% (-0.3 vs base) — but
     Q3 (mid-positive) WR15 = 75.0% (+8.96, z=+3.26). The signal is at Q3 (tier promotion)
     not Q5 (dampening).

2. **Put-side-only WVD** (lower risk, smaller win)
   - Put displacement is bounded by score boundary (puts lifted but never exit ≤25 universe)
   - PTP% flat means put trades are still in cascade; only their tier may shift
   - Test put-only WVD with no call dampening — likely portfolio-neutral but per-trade-positive

3. **Milder calibration** (K=0.20 v44-original) — running now as `mc_wvd_mild.py`
   - Half the dampening strength
   - May reduce capital velocity loss enough to break even

### What NOT to ship

- WVD-Call dampener at K=0.40 (aggressive) — hurts both compound and DD
- WVD-Call dampener at K=0.20 (mild) — also hurts compound (-59% / -68%), DD only marginally
  positive on 5y (-0.8pp, within MC noise floor)
- ANY dampener that displaces 5%+ of call trades without compensating tier-promotion

### Updated portfolio MC table — both K=0.40 and K=0.20

| Window | Metric | Baseline | WVD K=0.40 | WVD K=0.20 mild |
|---|---|---:|---:|---:|
| 22-now | Compound (Mean) | +3.28×10²⁰% | -31% | -59% |
| 22-now | WorstDD | 66.2% | +2.3pp ✗ | +1.4pp ✗ |
| 22-now | Call TP% | 63.3% | +0.6 | +0.3 |
| 22-now | CTrd | 1262 | -6% | flat |
| 5y | Compound (Mean) | +3.27×10²⁴% | -69% | -68% |
| **5y** | **WorstDD** | **67.2%** | **+7.3pp ✗** | **-0.8pp ✓** |
| 5y | Call TP% | 62.3% | +0.7 | +0.4 |
| 5y | CTrd | 1707 | -5% | flat |

The mild K=0.20 showed marginal 5y DD improvement (-0.8pp, within ±3pp MC noise floor at N=200)
but with compound still -68%. The dampener architecture is wrong for this signal at any tested K.

---

## Honest read of the v45 score-stage change

The user's recent v45 ship (ETF filter from breadth, ALGORITHM_VERSION=`56eb1f8`) reduced
qualifying signal density by ~56%. Combined with the SAW Put U-curve portfolio change, the
strategy is already capturing tighter, higher-quality signals. The cohort baseline at v45
(75+ WR15 = 66.04%, 80+ = 73.98%) is significantly better than v44 (65.60% / 71.03%).

**Implication:** the "low-hanging fruit" of obvious per-trade quality wins may largely be
captured in v45. The remaining cohort signals (like wv_force1) are real but require
*different mechanism architectures* — promotion, alloc-scaling, or path-dependent gates —
rather than score-stage dampening.

---

## Files

- `experiments/weekly_volume/build_features.py`, `cohort_profile.py`, `sweep.py`,
  `multi_window_validate.py`, `eval_tp15_tp30.py`, `mc_wvd.py`, `mc_wvd_mild.py`
- `.cache/weekly_volume/calls_v45_1825.parquet`, `puts_v45_1825.parquet`
- Logs: `cohort_v45.out`, `sweep_v45.out`, `multi_window_v45.out`, `tp15_tp30_v45.out`,
  `mc_baseline.log`, `mc_wvd.log`, `mc_wvd_mild.log`
- `FINDINGS.md` — investigation writeup (updated for v45)
- `REPORT_v45.md` — this file
