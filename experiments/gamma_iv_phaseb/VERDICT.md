# Gamma × IV Phase B (gameplan P1.3+P2.3 combined) — VERDICT MEMO

**Date:** 2026-07-10 · **Owner:** FABLE (architect) · **Status: COVERAGE-BLOCKED (pre-registered
decision rule; same consequence as FAIL for the L3 buy — the gamma leg does NOT reopen).**
**Pre-registration:** DESIGN.md + two dated amendments (validation protocol; as-of join + 60% dosing
bar), each landed BEFORE the compute it governs. This run subsumed P1.3 (Phase A never ran — the
"Phase-A PASSED" premise in the tasking was false; the only prior artifacts were the 07-01 two-arm
runs that PRODUCED the +1754% explosion finding).

## What was built (permanent assets)
- **IV_PREMIUM engine plug** (monte_carlo.py + backtest_cascade.py): env-gated, default-OFF,
  `premium_pct = 0.4·atm_iv·sqrt(DTE/365)` at entry with strictly-backward as-of panel join
  (≤14cd staleness), per-window hit/miss + staleness telemetry. **Flags-off inertness BIT-PROVEN**
  (pristine-vs-edited same-day: 16/16 aggregate fields, 38/38 window partials identical — task 586).
- GAMMA_AWARE (option_pricing.py) committed default-OFF alongside, per gameplan P0.6(d).
- Validation-protocol finding: the archived 07-01 goff baseline is NOT bit-reproducible (0/38
  windows; P2x −15.4pp, median compound sign-flipped) with PROVEN-identical code — the MC's price
  substrate drifts (yfinance retro-adjustment; deep backfill). Archived MC artifacts are
  reference-only; engine-edit validation = same-day pristine-vs-edited pairing. (→ traps.md)

## The A/B (same-day paired, N=300 × 13 quarterly windows 2022-08..2025-08 × 2 cells, collapse=0 everywhere)

| arm | flat_n4_a25: P2x% / med comp% / DD% | cascade_ref: P2x% / med comp% / DD% |
|---|---|---|
| base | 66.7 / +42.5 / 76.1 | 86.7 / +303.7 / 47.1 |
| gamma | 97.3 / +895.5 / 73.5 | 100.0 / +2587.8 / 41.5 |
| iv (19.3% dose) | 55.8 / −3.1 / 76.8 | 72.5 / +202.9 / 49.8 |
| gamma+iv (19.3% dose) | 95.4 / +664.8 / 73.1 | 100.0 / +1843.9 / 42.3 |

- **The explosion reproduces on fresh data** (gamma vs base) — P2.3's target phenomenon is stable,
  not a July-1 artifact.
- **The decisive criterion could not be dosed:** IV coverage 19.3% overall (60,454/313,237 lookups),
  **10.7% on the decisive 2022-08 window** (panel thins toward its own start: 687 rows in 2022 vs
  2,858 in 2025), vs the pre-registered ≥60% bar. Root cause is structural, not fixable by join
  logic: the $79 panel is keyed by SIGNAL date over a signal-subset universe, while the engine
  prices premium at every ENTRY (late cascade fills, re-entries, window tails past 2026-05-15).
  Exact join 15.6% → as-of ≤14cd join 19.3% (+3.7pp only — confirms misses are structural).
- **Direction texture (recorded, NOT a verdict):** every treated comparison moves the explosion
  DOWN (gamma+iv 664.8 vs gamma 895.5 on flat; 1843.9 vs 2587.8 on cascade; iv compresses base) —
  consistent with the error-cancellation thesis (RV premium too cheap for big movers), magnitude
  unquantifiable at ~19% dose. An under-dosed FAIL would have been a false negative; an under-dosed
  "explosion tamed" claim would have been equally dishonest. Neither is claimed.

## Rulings
1. **P2.3: COVERAGE-BLOCKED** per Amendment 2's decision rule. Gamma leg of the L3 case does NOT
   reopen. Gamma stays parked at ≤$79 sunk, engine flags default-OFF in production
   (standing anti-goal: never flip GAMMA_AWARE alone).
2. **P2.5 ($2,035 L3 buy): RESOLVED OFF** — its gate was "P2.3 OR P2.4 PASS"; P2.4 FAILED
   (2026-07-07, OSK) and P2.3 is coverage-blocked. No purchase at current evidence.
3. **The workstream's live continuation (unfunded tonight, own pre-registration required):** the
   calibrated premium MODEL — fit IV ≈ f(realized_vol, VIX-state) on the panel's ~60k covered
   lookups (real-IV truth, 2022-08+), then apply the MODELED premium at 100% dose engine-wide.
   This is the only form that can ever price the 2016+/2020-crash gate windows (no purchasable
   panel reaches them for this universe), and it converts this run's panel from an under-dosed
   treatment into a calibration set. P1.4 (vega state) pairs with it. Engine-fidelity adoption
   rules apply (no version bump; "does any gate DECISION flip?" is the acceptance question).
4. No adversarial verify: outcome is a block/null, not a positive. Harness integrity evidence:
   bit-proven off-path inertness, hand-verified premium units (0.0532 fraction at IV 0.464/30d),
   per-window telemetry, archived under-dosed outputs (`*_cov16*`) preserved alongside.

## Artifacts
DESIGN.md (+2 amendments) · results/validation_repro.txt (incl. INERTNESS TEST) ·
results/sweep_drill_phaseb_{base,gamma,iv,gammaiv}.json (+ `*_cov16` archives of the exact-join run) ·
results/coverage_{iv,gammaiv}{,_cov16}/ (per-window hit/miss/staleness) · run_phaseb.py ·
validate_repro.py · queue tasks 583 (valgoff), 586 (pristine), 587-590 (4-arm), 591-592 (as-of rerun).
