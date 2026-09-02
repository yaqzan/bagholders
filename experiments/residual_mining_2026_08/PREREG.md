# PREREG — Residual mining on honest labels

STATUS: LOCKED 2026-08-13 (git commit = lock). Consumes `honest_ledger_v1.parquet`
(LOCKED, acceptance-passed). Goal: find WHERE the current score mis-ranks under honest
labels — every surviving discriminator becomes a CANDIDATE for its own Stage-1 prereg;
nothing ships from here.

## Labels + embargo (LOCKED)

- PRIMARY label: `l2_expected` (calibrated-sim EV, tier-rate never-fill). `l3_*` used to
  VALIDATE any finding on its covered subset; `l1_*` reported for contrast only.
- **HOLDOUT EMBARGO: rows with date > 2026-06-15 are EXCLUDED from all mining** —
  they belong to December. Mining era = [2021-01-04 .. 2026-06-15], ripe rows only.

## Hypothesis space (pre-enumerated — nothing outside this list is tested)

Feature families over the ledger's 36 identity/feature columns: single-feature cohort
splits (quantile bins) for components (trend/macd/rsi/bb/stoch/ta), 11 weekly fields,
regime composite, volume-signal fields, liquidity tier (where present), PIT mcap bins,
delisted flag, ct_tag; PLUS two-way interactions restricted to (component ×
regime-composite), (component × liquidity-tier), (trend × any) — the CT lesson's
family. NO calendar features (closed axis). NO three-way interactions. Total enumerated
hypotheses counted and reported BEFORE outcomes; multiplicity controlled by
Benjamini-Hochberg FDR 0.05 across the full count.

## Survival bars (ALL required for "candidate" status; LOCKED)

1. Effect: cohort mean `l2_expected` lift vs complement ≥ |3.0pp| (house d_ev floor).
2. FDR-surviving at 0.05 across the declared hypothesis count.
3. Era stability: same-sign lift in ≥2 of 3 era thirds (2021-22 / 2023-24 / 2025-H1-26).
4. Survivor-robust: lift holds with delisted rows excluded.
5. L3 validation: on the covered subset, same-sign lift on `l3` realized P&L.
6. If the discriminator CUTS supply: an exposure-matched random control (floor_control
   method) must show attributable lift ≥ the same 3.0pp bar.

## Output

Ranked table (all tested hypotheses, effect/CI/q-value/era-signs/survivor/L3 columns) →
`out/`; FINDINGS lists candidates (could be zero — a clean null is a valid result) and
explicitly reports the hypothesis count. Stop rule: one pass over the enumerated space;
no post-hoc additions; follow-ups = new preregs per candidate.
