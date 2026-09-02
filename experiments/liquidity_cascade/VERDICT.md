# Liquidity-Aware Cascade (P3.6 / open-item #10) — VERDICT MEMO (FABLE, 2026-07-14)

**Status: ENGINE-BLOCKED — the LIQUIDITY_FLOOR stays default-OFF; the apparent N=500 win is a
verified CONCENTRATION ARTIFACT; the actual P3.6 question (fill-realism at size) transfers to the
P3.7 real-fill loop.** Pre-registration: DESIGN.md (floor grid {0,5,20,100} from the measured
distribution; expectation was a compound REGRESSION as the fill-realism fee). Adversarial
verification: MEASUREMENT-VALID, mis-attribution proven (Opus pass, 5 deliverables, this file's
source of record).

## What the A/B showed (queue 630, N=500 × 9 windows × Core/Apex)
Core 5y med_ret +3,027 → +4,507 (floor_20) at flat DD; 2025 window +19.8 → +61.1. Opposite the
pre-registered direction.

## What the verification proved
1. **No bug (H-D refuted):** all 27 drop counts reconcile exactly; drops confined to 2025-02-11+
   real-coverage rows as designed; paired seeds intact (zero-drop cells bit-identical to baseline).
2. **Concentration artifact (H-A, dominant):** a RANDOM equal-count drop reproduces large same-sign
   gains (22-now +167 vs floor's +538 at N=50). Decisive attribution: the dropped set is identical
   for both profiles, yet Core (MaxPos 14, book under-full in 2025 — median 3 signals/day) gains
   +1,480 while Apex (MaxPos 4, always full, no headroom) gains +11. Gain scales with concentration
   HEADROOM, not trade quality: dropping names promotes survivors up the rank-tier ladder
   (0.03→0.08→0.15→0.20), deploying more capital in fewer names in an up-market.
3. **Tail rescaling (H-B):** floor deltas on 2021-2024 are exactly 0.0 (bit-identical); the entire
   5y/22-now effect is one ~1.3-1.5× 2025-tail factor on a 31× base. The "flat DD" headline is
   structural: the DD-defining episode (2022 bear) is untouchable by a 2025-only filter.
4. **Selection is weak and non-significant (H-C-lite):** dropped-cohort underlying 15d forward
   return +4.0% vs kept +6.4%, day-clustered t = −0.74; sign FLIPS in the choppy dip window
   (floors HURT there). Not a durable edge. The real-option P&L gap is quote-staleness-contaminated
   and invisible to the MC anyway.
5. **The structural finding (promoted to traps.md):** the MC prices via idealized constant-delta on
   the underlying with no spread/illiquidity penalty — a liquidity FILTER in this engine can only
   remove names (helping via concentration) and can never pay the cost the design meant to price.
   The fill-realism fee is UNOBSERVABLE with the current instrument.

## Rulings
1. **LIQUIDITY_FLOOR ships nowhere.** Stays default-OFF (0.0) as committed (`429cfa58b`); no
   further floor sweeps on this engine — they measure concentration side-effects by construction.
2. **P3.6 closes ENGINE-BLOCKED** (distinct from NULL — the question is real, the instrument can't
   see it). Re-open path: the P3.7 real-fill loop (its original gameplan pairing) accumulates N≥30
   real fills → fit a grounded spread/fill-penalty model → re-run THIS harness with the penalty in
   the engine. Only then does a floor's cost/benefit become measurable.
3. The incidental "thinning an under-full book concentrates capital up the tiers" texture is
   recorded, NOT chased: it sign-flips by regime, collides with the P3.2 frontier result (which
   swept MaxPos/tiers directly and found the shipped config optimal), and exposure-concentration
   mechanisms are the capital-velocity law's home turf. Any future interest routes through a
   dedicated Stage-3 design, not through a liquidity filter's side-effect.
4. The `option_volume_30d` feature + its two resolution bug-fixes are banked (useful for P3.7's
   slippage analysis and any future liquidity work).

## Artifacts
DESIGN.md · results/summary.json + per-arm/window jsons · feature build (commit `429cfa58b`) ·
adversarial-verification scratch artifacts (rand_ctrl_*.py, ctrl_*.json — session scratchpad) ·
queue task 630.
