# OSK Cross-Regime Validation — Pre-Registration

**Owner:** FABLE · **Locked 2026-07-07, before any era statistic was computed.**
**Purpose:** the decision experiment the Polygon Options Developer purchase exists for
(data-acquisition.md): does the confirmed opt_skew (OSK) per-trade edge persist across regimes,
or was it a 2025-selloff-era artifact? **Outcome gates real money:** PASS ⇒ recommend the
historicaloptiondata L3 buy (~$2,035, depth to 2002); KILL ⇒ route to ORATS evaluation. Because a
PASS triggers a purchase recommendation, **any PASS must survive independent adversarial verification
before being reported as PASS.**

## Panel (verified by recon 2026-07-07)
`.cache/polygon_iv/iv_ledger_polygon.parquet` — 8,643 rows, 669 symbols, 2022-08-01 → 2026-05-15,
the COMPLETE rs_ledger 70+ universe for the window (10,793 attempted, 80% chain-hit; kept-rate by
year 72/66/78/87/87%). Cols incl. skew (86.2% non-null), pnl15 (99.3%), atm_iv, iv_rv, entry_premium,
overall, vol_pct. All dates ≤ holdout cutoff (2026-06-15); run `assert_no_holdout_leak(df,'osk_val')`
anyway. Labels: pnl15 = 15-TRADING-bar forward ATM-call P&L (harness must code-read
polygon_iv_ingest.py to confirm the fwd_pnl convention matches build_iv.py's; state it in results).

## The decisive split
- **BACKWARD-OOS: 2022-08-01 → 2025-02-10** (~4,600 rows). Predates the discovery panel
  (proxy_ledger opt_skew coverage starts 2025-02-11). No calibration, sweep, or eyeball has ever
  touched option data in this window. This is the validation.
- **DISCOVERY-ERA: 2025-02-11 → 2026-05-15** (~4,000 rows). Overlaps the original confirmation;
  used for source-consistency and continuity, never as validation evidence.

## Eras (pre-registered, for sign stability)
E1 2022-08→2022-12 (bear tail, N≈687) · E2 2023 (recovery chop, N≈1,027) · E3 2024 (bull + Aug
vol shock, N≈2,527) · E4 2025-01→2025-06 (selloff era) · E5 2025-07→2026-05-15 (recent).
E1–E3 are fully pre-discovery.

## Tests (in order; 1 gates the rest)
1. **SOURCE CONSISTENCY (gate):** inner-join Polygon panel × proxy_ledger (opt_skew not-null, 1,998
   rows) on (symbol,date). Report corr(skew_polygon, opt_skew_ours), corr(pnl15_polygon, pnl15_ours),
   mean/median diffs, N. **Gate: skew corr ≥ 0.80.** If it fails, STOP era analysis; diagnose
   strike/DTE-selection differences first and report — a source mismatch would poison everything.
2. **Univariate:** spearman(skew, pnl15) + quintile table (mean pnl15 + WR = share pnl15>0):
   backward-OOS pooled, full pooled, each era; FULL and overall≥75 subsets (75+ cells will be thin
   ~200-400/era — report, never gate on them).
3. **Orthogonalized OLS (decisive):** pnl15 ~ skew + semivol_r + overall + stock_r20, standardized,
   numpy lstsq, plain AND date-clustered t (the GEX campaign's sandwich; date = signal date).
   stock_r20 joined from rs_ledger; semivol_r recomputed from price_history (house formula:
   std(dn)/std(up), trailing 60 trading days, ≥3 up and ≥3 dn else null — one bulk cached MySQL
   query). OLS on raw pnl15 AND 1%-winsorized (both reported; spearman is the magnitude-comparison
   primary since the original +0.110/+0.145 was rank-based).
4. **Stability:** per-era sign table of the skew beta (clustered t per era, N-labeled).

## Bars (locked)
- **PASS** (⇒ L3-buy recommendation, AFTER adversarial verify): backward-OOS pooled clustered
  t(skew) ≥ +2.5 AND backward-OOS spearman ≥ +0.05 AND ≥2 of E1–E3 positive-sign AND no era with
  spearman ≤ −0.05.
- **WEAK:** backward-OOS same sign, clustered t ∈ [1.5, 2.5) → "suggestive persistence; extend via
  ORATS $99/mo for 1-2 months before committing L3."
- **KILL:** backward-OOS clustered t < 1.5, or negative sign, or an era ≤ −0.05 with N ≥ 500.
- Adversarial verify on PASS (Opus): independent recomputation from the raw parquet, winsorization
  sensitivity, per-era N audit, source-boundary artifact hunt (does the effect jump at 2025-02?),
  cluster-key validity, and the survivorship framing check below.

## Honesty constraints (stated in results verbatim)
- Early-era signals come from a survivor-tilted universe (rs_ledger builds on our price_history;
  delisted names absent) — absolute WR/pnl levels are optimistic FLOORS; the sign/persistence
  question is the claim, not levels. The delisted-equity ingest (parallel arm) will harden this later.
- The original +0.110/+0.145 baselines were computed on OUR chains; apples-to-apples comparison uses
  the source-consistency join, not raw cross-source magnitude deltas.
- Single data vendor for the backward window; no second source to triangulate pre-2025. L3 purchase
  (if PASS) is itself the second-source acquisition.
- Even PASS = "per-trade residual confirmed across regimes, data-locked" — portfolio/ship claims
  still require the full Stage-1/Stage-3 gates later; this experiment only decides the DATA BUY.

## AMENDMENT 1 — 2026-07-07, after the source-consistency gate FAILED, BEFORE any predictive
## Polygon-skew statistic was computed
Gate result: skew corr = 0.199 (< 0.80), pnl15 corr = 0.930, marginals match (means 0.041/0.039),
sign agreement 69.9%. Premium data agrees; the SKEW RECIPES diverge — ours = single nearest strike
from EOD chain snapshot (quotes/OI exist regardless of trading); Polygon's = nearest-of-6 strikes
with actual trades that day (trade-based closes). The recipes cannot be made identical on illiquid
strikes. Restructured plan, locked now:

- **D1 DIAGNOSIS (report-only):** on the 1,917-row overlap join, quantify selection mismatch:
  ATM-strike match rate, DTE match rate, OTM-leg strike distances where recoverable; corr(skew)
  CONDITIONAL on matched selection (if both ledgers carry the selection metadata). Purpose: attribute
  the 0.199 to selection vs price-level disagreement. If even matched-selection IVs disagree
  materially, flag DATA-QUALITY and stop for user decision.
- **D2 MEASUREMENT-ROBUSTNESS REPLICATION (new decisive step):** does POLYGON-skew predict pnl15 on
  the DISCOVERY window (2025-02-11 → 2026-05-15, N≈4,000) where our-skew's edge is confirmed?
  Full battery: spearman + quintiles, orthogonalized OLS (skew + semivol_r + overall + stock_r20),
  plain + date-clustered t, raw + 1%-winsorized, FULL + 75+.
  **Bars (locked): REPLICATES = spearman ≥ +0.055 (half the original +0.110) AND clustered t ≥ +2.0
  → proceed to the era/backward-OOS analysis (Tests 2–4 as registered, Polygon-skew uniformly,
  original bars unchanged). FAILS-TO-REPLICATE = below either → STOP; verdict "OSK
  measurement-robustness FLAG"** — the edge exists only under our recipe of the same quantity; the
  L3 buy decision escalates to the user with the full diagnosis (a third vendor cannot validate a
  recipe-specific edge), and the honest next step is recipe-sensitivity analysis on OUR data, not
  more vendors.
- Rationale for pre-registering D2 at half-magnitude rather than full: vendor/recipe noise at
  corr 0.199 attenuates an identical true signal by roughly sqrt of the reliability; demanding the
  full +0.110 under a recipe this different would over-kill a real edge. Halving is the standard
  attenuation allowance, fixed here before computation.

## Deliverables
1. `experiments/osk_validation/osk_validation_test.py` + results .txt/.json (Sonnet, mechanics-verified).
2. FABLE verdict vs bars → `VERDICT.md`; adversarial panel iff PASS.
3. data-acquisition.md + memory updated with the buy decision.
