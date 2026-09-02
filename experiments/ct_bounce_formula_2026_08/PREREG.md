# PREREG — CT-successor bounce formula (designed-sleeve entry criterion)

STATUS: LOCKED 2026-08-13 (git commit = lock). Consumes `honest_ledger_v1.parquet`.
Goal: an explicit P(bounce) model for the deep-pullback cohort — the designed sleeve's
entry criterion (upgrades ct15-paper-sleeve ARM B later via its own amendment). Research
only; no ship, no live-book change.

## Cohort (LOCKED)

Rows with `ct_flag` OR trend component ≤ 20, mining era [2021-01-04 .. 2026-06-15]
(same embargo as residual_mining: post-2026-06-15 untouched), ripe only.

## Target + validation (LOCKED)

- Train target: `l2_expected` (regression) AND binary `l2_kind=='tp'` (classification) —
  both reported; the ENTRY RULE derives from the classifier's calibrated probability.
- GOLD validation: `l3` realized P&L on the covered subset — time-split only (no random
  CV): train ≤2025-06-30, validate 2025-07-01..2026-06-15, with a 30-day purge gap.
- Features: PIT-safe only — pullback depth/duration (price vs recent peak), RSI, volume
  fields, weekly fields, regime composite, liquidity tier, PIT mcap, price level.
  BANNED: delisted flag as feature (outcome leakage), any post-entry information,
  calendar.

## Model class (LOCKED — small-N discipline)

Logistic regression and monotone-constrained shallow GBM (≤ depth 3, ≤ 200 trees) ONLY.
No deep nets, no unconstrained boosting. Coefficients/monotonicity reported — the model
must be READABLE (a formula, not an oracle).

## Success bar (LOCKED)

The model earns "candidate entry criterion" ONLY if, on the time-split validation:
(1) top-quintile-by-model mean `l2_expected` beats the cohort's raw-`overall`-ordering
top quintile by ≥ +3.0pp; (2) same-sign advantage on the `l3` gold subset; (3) the
advantage survives delisted-exclusion. Else verdict = "raw score ordering stands; no
formula earned." Calibration curve reported either way.

## Stop rule

One feature set, two model classes, one time split — as enumerated. No re-splitting,
no feature additions after outcomes. FINDINGS either way; if candidate: the
ct15-paper-sleeve ARM-B amendment is drafted for owner visibility, NOT auto-applied.
