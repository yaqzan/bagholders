# OSK Cross-Regime Validation — VERDICT

**Date:** 2026-07-07 · **Owner:** FABLE · **Verdict: KILL (per pre-registered bars) — do NOT buy
historicaloptiondata L3 for OSK validation.** The $79 Polygon trial did exactly its job: it killed a
$2,035 purchase premise cheaply. Pre-registration: DESIGN.md (+ Amendment 1), bars locked before
computation.

## The chain of results
1. **Source-consistency gate FAILED as designed** (phase 1): Polygon-skew vs our-skew row-level corr
   0.199 despite matching marginals and pnl15 corr 0.930. D1 attribution: OTM-leg strike selection is
   the dominant driver (skew corr 0.35 in the best-matched ATM quartile vs 0.09 in the worst; atm_iv
   agreement 0.837, no data-quality flag). Finding: our build_iv ledgers never persist their own
   strike/DTE selection — transient in SQL, uncheckable afterward.
2. **D2 measurement-robustness replication: REPLICATES.** On the discovery window (2025-02-11→
   2026-05-15) Polygon-skew predicts pnl15: spearman +0.090 (bar ≥0.055; attenuation-consistent vs
   our-recipe +0.110), 75+ +0.114 (N=683), orthogonalized clustered t +3.38 (bar ≥2.0), quintile WR
   34.5%→47.4%. **The 2025-26 OSK edge is real and not a measurement artifact.**
3. **Backward-OOS (2022-08 → 2025-02-10, N=3,807 — data never touched by any calibration): the
   univariate edge is ABSENT.** Spearman −0.002 (p=0.91); 75+ subset −0.073 (N=514); era E1
   (2022 bear tail) −0.107 with N=569 → trips the pre-registered kill clause (era ≤ −0.05, N ≥ 500).
   **Bar label: KILL.**

## The honest nuance (recorded, NOT a verdict rescue)
The ORTHOGONALIZED backward-OOS cell is strongly positive — β +0.049, clustered t +4.60 (winsorized
+4.47), era signs +4/5 (only E1 negative, t −0.87) — a classic suppression pattern: stock_r20
negatively predicts pnl15 pre-2025 (mean-reversion era, control t −3.17) and masks a conditional skew
relation that controlling unmasks. Caveats before anyone believes it: (a) this conditional form is NOT
the edge that was confirmed (the confirmed claim was rankable univariate skew); (b) clustered SE <
plain SE here (negative intra-day correlation) — unusual, unverified; (c) it is off-registration.
Status: **unverified observation; at most ONE pre-registered follow-up** ("conditional OSK:
skew-net-of-momentum") if ever prioritized — it does not change today's buy decision.

## What this means
- **OSK is a REGIME-CONDITIONAL edge, not a universal per-trade residual.** Present and robust
  2025-2026; univariately absent 2022-2024; sign-negative in the 2022 bear tail. The staged status
  ("confirmed per-trade residual, data-locked") is DOWNGRADED to "confirmed in-regime (2025-26,
  measurement-robust); fails backward univariate validation across 2022-24."
- **Buy decision (the experiment's purpose): NO L3 purchase for OSK.** The doc's KILL path applies;
  ORATS-for-OSK is equally moot — more history cannot validate an edge that is regime-local by
  nature. The RESIDUAL case for deep option history (gamma-curve/MC crash-fidelity across 2008/2020)
  is a SEPARATE justification, unaffected by this verdict — decide it on model-fidelity grounds.
- Survivorship note (pre-registered): early-era signals are survivor-tilted; levels optimistic. The
  KILL is on SIGN/absence, which survivorship does not plausibly manufacture.
- Polygon Developer stays valuable regardless: 4y premium panel powers the gamma/model-fidelity
  workstream (its intended second purpose) and delivered this verdict for $79.

## Artifacts
DESIGN.md (prereg + amendment) · osk_validation_test.py / osk_validation_phase2.py ·
osk_results.{txt,json} / osk_results_phase2.{txt,json} · panel: .cache/polygon_iv/iv_ledger_polygon.parquet
(8,643 rows, 2022-08→2026-05, 70+ universe complete) · price cache: .cache/experiment_data/osk_val_prices.parquet.
