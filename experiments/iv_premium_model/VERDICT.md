# Calibrated IV-Premium Model (F2) × Gamma — VERDICT MEMO

**Date:** 2026-07-12 · **Owner:** FABLE (architect) · **Status: PASS on the pre-registered P1.3 bars,
ADVERSARIALLY VERIFIED (measurement-valid). Instrument finding, NOT an adoption license.**
**Pre-registration:** DESIGN.md (approved-as-amended A1-A4: F2-over-F0 form ruling, cost-only hard
constraint, dose bar ≥95%, telemetry) before wiring; adversarial verification (Opus, 6 attack
surfaces) before this verdict, per house rule for positives.

## The A/B (same-day paired, N=300 × 38 windows 2016-06..2025-08 × 2 cells, dose 100.00%)

| arm | flat_n4 (sprint): P2x / med comp / DD / collapse | cascade (held): P2x / med comp / DD / collapse |
|---|---|---|
| base (RV premium, const-delta) | 55.9% / −4.2% / 78.0% / 0 | 64.7% / +107.8% / 72.3% / 0 |
| gamma alone | 97.7% / +747.5% / 75.6% / 0 | 100% / +818.7% / 71.0% / 0 |
| IV-model alone | 9.4% / −67.9% / 89.2% / **17.05%** | 8.3% / −41.5% / 78.5% / 0 |
| gamma + IV-model | 39.3% / −29.3% / 82.3% / 0.009% | 44.8% / +31.6% / 71.0% / **0 all 38 windows** |

Bars: explosion collapsed ✓ (from +1754%-class to at/below base) · P2x not pinned ✓ · DD ±5pp ✓ ·
held-cell collapse 0 ✓ (sprint's 1/11,400 tail path sits in a 2017 window, not a crash).
Dose 818,319/818,319 model-priced; clamp cap 0.75%, floor mathematically unreachable for F2;
realized model/RV premium ratio median ~1.17 (terciles 0.91/1.18/1.62).

## Findings (verified)
1. **The error-cancellation thesis is CONFIRMED and quantified.** The engine's apparent realism rests
   on two offsetting errors: gamma-alone is fantasy (+819% median), and the cheap RV premium was
   propping up the linear model — **IV-premium-alone is equally catastrophic** (17% collapse). The
   pair is STRICTLY COUPLED: never flip either alone, in either direction.
2. **The collapse mechanism of IV-alone is calm-market premium overpayment, not crash ruin.**
   Localization: 96-100% collapse in 2017-2019 low-RV windows (markup up to ~3.3× on calm names,
   linear payoff, theta); the 2020-crash windows show 0% (the model prices high-RV premium CHEAPER
   than the RV fallback there). Correct the naive crash prior when citing this.
3. **gamma+IV is a central estimate, not a floor** (verifier surface 5): F2 is OLS-unbiased overall
   (bias −0.001) but over-prices the low/mid-RV majority (+0.013/+0.025, ~73% of rows) and
   UNDER-prices the high-RV convexity-driving tail (−0.041); MAE 0.170 ≈ 37% of median IV. The
   −29.3%/+31.6% numbers are F2-as-specified, with wide model-fidelity error bars — the realistic
   engine could sit either side. Do NOT quote them as realistic point estimates.
4. **Measurement integrity:** barriers/sigma byte-identical across arms (cost-only constraint held);
   paired seeds shared; VIX join entry-date-exact (forward access unreachable by construction); all
   pooled metrics recompute bit-exact from per-window partials; DTE=30 uniform.

## Rulings
1. **P2.3's substantive question is ANSWERED without the $2k buy:** a real-IV-anchored premium kills
   the gamma explosion. The gameplan's "coupled" doctrine for the gamma/IV instrument is now
   evidence-backed at N=300×38, adversarially verified.
2. **Adoption (default-ON re-baseline) is NOT licensed by this A/B.** Gate for adoption: per-trade
   validation of the model-priced engine against REALIZED option P&L on our own option_prices panel
   (1.4y+, free, unblocked) — does the F2-priced engine track real fills better than the RV engine,
   per-trade, by cohort? Only after that: the engine-fidelity re-baseline procedure (no version bump;
   "does any gate DECISION flip?" is the acceptance question) + P1.4 vega-state pairing.
3. Production flags stay default-OFF (GAMMA_AWARE, IV_PREMIUM, IV_MODEL) — traps.md updated to
   forbid flipping EITHER side alone, now with quantified evidence.
4. Polygon subscription: no longer needed for this workstream — the panel is harvested and is the
   model's calibration truth-set; forward work runs on our own daily pulls.

## Artifacts
DESIGN.md (pre-reg + A1-A4) · fit_report.{json,txt} + fit_table.parquet + build_fit_model.py ·
build_vix_series.py · sanity_check_ivmodel.py · run_ivmodel.py ·
results/sweep_drill_ivm_{base,gamma,ivmodel,gamma_ivmodel}.json + coverage_* telemetry ·
adversarial verification transcript (session task, 6 surfaces, 32/32 bit-exact reconciliation) ·
queue tasks 593-596.
