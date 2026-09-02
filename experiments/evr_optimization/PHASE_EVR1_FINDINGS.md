# Phase EVR-1 — Pre-event earnings volume calibration on v31 baseline

**Date:** 2026-04-30
**Active version:** v31 (`f3ec7c1`) — strict-future earnings + AMC-aware effective_date
**MC architecture:** option-pricing-aware, bounded random fill (commits `f2329a1`, `9a9da33`, `3432fb8`)

## Question

Two architectural shifts since the original V6/V7 sweeps make their findings load-bearing-but-stale:
1. **Strict-future earnings** (this PR / v31): `_earn_nearest_d_*` now uses `bisect_right` and requires `1 <= delta <= W`, eliminating the D₀ leak that biased original V6/V7 numbers.
2. **Option-pricing-aware MC**: theta + sampled vega modelled explicitly; the volume amplifier may have been compensating for unmodeled option-pricing noise.

Test whether the V6 log-gradient earnings volume suppression (W=2 cal days, M=1.0, pre-only, log shape) is still pulling its weight, or whether `OFF` (no suppression at all) ties or beats it — in which case we can simplify by deleting `_earnings_supp_strength` entirely.

## Setup

Per-trade A/B via `simulator.diff_assess` against active v31 DB peaks. Volume amplifier monkey-patched per variant (`experiments/evr_optimization/phase_evr1_per_trade.py`).

**Variants tested (all pre-only):**

| Variant | Strategy | W | M | k | Strength at d=1 | Strength at d=2 |
|---|---|---:|---:|---:|---:|---:|
| **B** (production) | log | 2 | 1.00 | — | 0.631 | 0.000 |
| OFF | off | — | — | — | 0 | 0 |
| W1 | log | 1 | 1.00 | — | **0.000** ⚠ | — |
| W3 | log | 3 | 1.00 | — | 0.792 | 0.500 |
| M075 | log | 2 | 0.75 | — | 0.474 | 0.000 |
| M050 | log | 2 | 0.50 | — | 0.316 | 0.000 |
| TANH | tanh | 2 | 1.00 | 1.0 | 1.000 | 0.790 |

**W1 finding:** at strict-future W=1, only `nearest_d == 1` qualifies, and `log(W+1-d)/log(W+1) = log(1)/log(2) = 0`. W1 produces zero suppression — mathematically identical to OFF. Dropped from 5y sweep.

## 2y per-trade results

Active v31 baseline. Tabulated as Δ vs B (sim) on each bucket. Sim numbers used for B-vs-variant comparison (DB column would only differ in noise across runs since DB is fixed).

### CALLS (WR15 / N delta)

| Bucket | B sim | OFF | W1 | W3 | M075 | M050 | TANH |
|---|---:|---:|---:|---:|---:|---:|---:|
| 95+ N=36 | 88.6% | -2.9pp / 0% | -2.9pp / 0% | -0.4pp / -2.8% | +0.6pp / +5.6% | -1.4pp / +11.1% | -1.1pp / -8.3% |
| 90+ N=117 | 77.4% | -0.2pp / 0% | -0.2pp / 0% | -0.3pp / -5.1% | -0.2pp / -0.9% | 0.0pp / 0% | -0.3pp / -5.1% |
| 85+ N=284 | 78.6% | +0.6pp / +3.2% | +0.6pp / +3.2% | -0.6pp / -2.8% | +0.1pp / +0.7% | +0.3pp / +1.4% | -0.1pp / -2.5% |
| 80+ N=815 | 72.1% | +0.4pp / +1.6% | +0.4pp / +1.6% | 0.0pp / -0.9% | -0.1pp / -0.1% | +0.1pp / +0.4% | 0.0pp / -0.6% |
| 75+ N=2014 | 72.2% | -0.1pp / +1.0% | -0.1pp / +1.0% | 0.0pp / -0.5% | -0.1pp / +0.1% | +0.1pp / +0.4% | -0.1pp / -0.6% |
| 70+ N=5665 | 70.9% | 0.0pp / +0.4% | 0.0pp / +0.4% | 0.0pp / -0.1% | 0.0pp / 0.0% | 0.0pp / +0.2% | 0.0pp / -0.2% |

### PUTS (WR15 / N delta)

| Bucket | B sim | OFF | W1 | W3 | M075 | M050 | TANH |
|---|---:|---:|---:|---:|---:|---:|---:|
| <5 N=142 | 76.3% | +1.0pp / +1.4% | +1.0pp / +1.4% | +0.8pp / +0.7% | +0.3pp / +1.4% | +0.8pp / +0.7% | +0.8pp / +4.2% |
| <15 N=701 | 71.6% | +0.3pp / -0.6% | +0.3pp / -0.6% | +0.1pp / 0.0% | 0.0pp / 0.0% | +0.2pp / 0.0% | +0.5pp / -0.6% |
| <25 N=2228 | 72.2% | +0.1pp / -0.2% | +0.1pp / -0.2% | 0.0pp / 0.0% | 0.0pp / -0.1% | 0.0pp / -0.1% | +0.1pp / 0.0% |

## Headline

**V6 mechanism is per-trade-inert at 2y.** All six tested parameterizations (off / narrow / wide / gentle / smooth) cluster within ±1pp of B on every bucket above N=200. Top 95+ deltas (-2.9 to +0.6pp) sit on N=33-40 — single trades flipping verdict, statistical noise.

**OFF trends slightly *better* than B on puts** (+0.1 to +1.0pp on every put bucket) — consistent direction across all 6 put buckets. Calls are mixed; small positive on 85+/80+ (+0.4 to +0.6pp), neutral elsewhere.

**The strict-future fix in v31 dramatically reduced V6's actual impact.** Production B suppresses only d=1 at strength 0.631; d=2 evaluates to log(1)/log(3) = 0. So the "W=2" production label is misleading — the mechanism effectively is "d=1 only with mild dampening."

## 5y per-trade results (all 6 variants complete)

5y N is ~5× the 2y sample. Statistical power is now meaningful at 95+ (N=86 for B, vs 36 at 2y).

### CALLS (5y, sim WR15 Δ / N% Δ vs B)

| Bucket | B sim | OFF | TANH | W3 | M075 | M050 |
|---|---:|---:|---:|---:|---:|---:|
| 95+ N=86 | 88.2% | **-1.1pp / +9.3%** | -2.1pp / -7.0% | -1.8pp / -4.7% | -0.1pp / -1.2% | -0.6pp / +4.7% |
| 90+ N=265 | 80.6% | -0.2pp / +6.8% | -1.4pp / -6.8% | -1.6pp / -7.5% | -0.1pp / -0.4% | -0.3pp / +2.3% |
| 85+ N=674 | 80.3% | 0.0pp / +3.1% | -0.4pp / -4.2% | -0.6pp / -4.5% | 0.0pp / +0.9% | 0.0pp / +1.3% |
| 80+ N=1854 | 75.0% | **+0.4pp / +2.1%** | +0.2pp / -1.1% | +0.1pp / -1.2% | +0.1pp / +0.4% | +0.2pp / +1.1% |
| 75+ N=4573 | 73.5% | 0.0pp / +0.9% | 0.0pp / -0.8% | 0.0pp / -0.9% | 0.0pp / +0.2% | +0.1pp / +0.4% |
| 70+ N=12390 | 71.3% | 0.0pp / +0.3% | 0.0pp / -0.2% | 0.0pp / -0.3% | 0.0pp / +0.1% | 0.0pp / +0.2% |

### PUTS (5y)

All variants tied with B on every put bucket within ±0.7pp WR15. <5 noise (N=365) only material delta.

| Bucket | B sim | OFF | TANH | W3 | M075 | M050 |
|---|---:|---:|---:|---:|---:|---:|
| <5 N=365 | 79.6% | -0.1pp | +0.7pp | +0.7pp | +0.1pp | +0.4pp |
| <15 N=2063 | 76.2% | +0.1pp | +0.4pp | +0.2pp | +0.1pp | +0.2pp |
| <25 N=6827 | 74.9% | 0.0pp | +0.1pp | 0.0pp | 0.0pp | 0.0pp |

### Headline (5y, all 6 variants)

**OFF beats B per-trade.** OFF admits 8 extra 95+ peaks and 18 extra 90+ peaks. Marginal WR of admitted-only signals:

- 95+ marginal: `(87.1×94 − 88.2×86)/8 = ~75% WR` — well above 56.3% call BE
- 90+ marginal: `(80.4×283 − 80.6×265)/18 = ~77% WR` — well above BE
- 80+ marginal: WR15 actually IMPROVES (+0.4pp on +39 admitted N) — strongest signal

**The suppression-strength gradient is non-monotonic but tells a coherent story.** Strength at d=1 / d=2 → 95+ WR15:

| Variant | d=1 | d=2 | 95+ WR15 |
|---|---:|---:|---:|
| OFF | 0.000 | 0 | 87.1% |
| M050 | 0.316 | 0 | 87.6% |
| M075 | 0.474 | 0 | 88.1% |
| **B** | **0.631** | **0** | **88.2%** |
| W3 | 0.792 | 0.500 | 86.4% |
| TANH | 1.000 | 0.792 | 86.1% |

OFF→B (d=1 only): WR15 climbs slightly (87.1% → 88.2%) but at *lower* N. Beyond B (W3, TANH adding d=2 suppression): WR15 collapses 1.8-2.1pp. So there's a per-trade peak around B's d=1 strength of 0.631, but **the marginal-WR analysis flips the conclusion**: OFF's extra 8 calls at ~75% marginal WR are net-positive. B's V6 is filtering EV-positive signals.

**Bonus: 80+ tier is unambiguously better under OFF.** WR15 +0.4pp on +39 added peaks (N=1854 baseline — statistically solid). This bucket alone is worth shipping for.

**TANH and W3 are strictly worse than B.** Adding d=2 suppression cuts additional N from 95+/90+ at lower WR. Don't go there.

## Decision (per-trade gate, 5y, 4/6 variants)

**SHIP OFF — delete the V6 mechanism.** Targets to delete in `volume_amplifier.py`:
- `_earnings_supp_strength()` (lines 215-236)
- `_apply_earn_supp()` (lines 239-258)
- `_earn_nearest_d_from_set()` (lines 287-300)
- `_query_earn_nearest_d_db()` (lines 261-285)
- The V6 wrapper logic in `get_volume_multiplier_from_cache` (lines 766-768) and DB analog (lines 815-820)
- Constants `EARN_SUPP_WINDOW_DAYS`, `EARN_SUPP_MAX_STRENGTH` (lines 55-56)
- `_load_effective_earnings_dates` if not used elsewhere

~150 lines of code, plus the supporting `effective_date` lookups in score-stage paths if they're V6-only callers.

## What this finding does NOT prove

1. **Portfolio-level.** Phase EVS (2026-04-28) had cleaner per-trade signal than this and still failed canonical MC at -11.3% on 5y. Per-trade ≠ portfolio. The portfolio-level test must wait on Phase OP2 (current shipped knobs are structurally too aggressive under bounded-fill MC).
2. **Post-event volume.** EVR-1 only tests pre-event suppression (the production V6 scope). Post-event volume IS informational per Phase EVS V7 finding (-11pp on 95+ when post-suppressed). OFF means "don't pre-suppress" — it does NOT mean "ignore earnings." The amplifier still operates normally on post-event days.
3. **Score-stage interactions.** The earnings meta-score boost (v28) and EARN_SUPP_PUT (portfolio-stage filter) are independent mechanisms. Their behavior under "no V6" is not directly tested here, only their per-trade impact on the resulting score buckets.

## Caveats

1. **Simulator passes announcement dates, not effective_dates** (`simulator.py:257`). Production V6 reads `effective_date` via `_load_effective_earnings_dates`. All 6 variants share this issue (apples-to-apples relative comparison still valid), but absolute fidelity to v31 production semantics is imperfect. Worth a follow-up to update simulator.
2. **Per-trade WR ≠ option TP rate.** Per the v22/v23/v24 lessons (CLAUDE.md), per-trade tests are necessary but not sufficient. EVR-1 ship gate also requires canonical seeded MC at N=300+ × 8 windows under bounded-fill model.
3. **MC validation must wait on Phase OP2.** Current shipped portfolio knobs (B68, F3F=0.50/0.50, ULTRA=0.18, PUT_SL=-0.20) are flagged as structurally too aggressive under bounded-fill MC. Running EVR-1's MC step against a known-broken baseline contaminates the result. Per-trade gate is the only meaningful EVR-1 signal until OP2 lands.

## Files

- `experiments/evr_optimization/phase_evr1_per_trade.py` — sweep harness
- `experiments/evr_optimization/analyze_evr1.py` — log parser + comparison emitter
- `experiments/evr_optimization/phase_evr1_2y.out` — 2y raw output
- `experiments/evr_optimization/phase_evr1_5y.out` — 5y raw output (in progress)
