# GEX Experiment — VERDICT MEMO

**Date:** 2026-07-07 · **Owner:** FABLE (architect) · **Status: CLOSED — NULL (per-trade track), with one documented follow-up lead**
**Pre-registration:** all panels, features, and kill rules were locked in DESIGN.md before any GEX
number was computed. Bar: orthogonalized |t| ≥ 3 on pnl15 with opt_skew in the model = PASS candidate;
2.0–3.0 = WEAK; sub-period sign flip on the constant-coverage subset = kill. Baseline to beat/complement:
opt_skew univariate spearman +0.110 full / +0.145 (75+) on the same panel.

## Panels actually tested
| Panel | N | Notes |
|---|---|---|
| Mechanism (rs_ledger ∩ chains, labels mfe15/mae15/net_path) | 25,698 | ≤ 2026-05-15, leak-guarded |
| Decisive ortho (proxy 1,998 + ext gap 542, pnl15 + full controls) | 2,527–2,532 | pooling parity code-verified |
| 75+ money subset | 554 | where the opt_skew edge concentrates |
| OOS money (true holdout > 6/15) | 300 staged, **0 resolved** | resolves ~2026-07-29 |
| Cache | 10.3M chain rows, 631 symbols, 26,582 signals | .cache/experiment_data/gex_chain.parquet |

## Per-feature verdicts (pre-registered primaries)
| Feature | Money uni ρ (full / 75+) | Money OLS t_plain / t_clust | Mech OLS t_plain / t_clust | Sub-period signs (pooled · const-cov) | VERDICT |
|---|---|---|---|---|---|
| gex_ratio | +0.030 / +0.001 | **+2.31 / +1.84** (proxy-only +2.46/+1.89) | +5.06 / +1.92 | +/+/+ · **+/+/− FLIP** | **NULL** (WEAK band, killed by const-cov flip + clustering) |
| gex_regime | −0.020 / −0.008 | +0.34 / +0.35 | +3.14 / +1.52 | flips both | **NULL** |
| flip_dist | −0.023 / −0.038 | +0.75 / +0.58 | +2.89 / +1.53 | flips both | **NULL** (also: flip exists for only 83% of signals) |
| callwall_dist | +0.013 / −0.065 | −1.99 / **−0.69** | −3.30 / **−3.31** | −/+/+ · +/−/− flips | **NULL** (money t is clustering artifact; mech survivor killed by sign instability) |

## The three load-bearing findings
1. **No redundancy excuse — the features are genuinely orthogonal and genuinely weak.** Max |corr|
   vs opt_skew is 0.094 (flip_dist). GEX fails not because opt_skew already owns the information,
   but because per-name, per-trade dealer-positioning information content is tiny at our horizon.
2. **Date-clustering collapse is the story.** Mechanism-track plain t up to +5.06 falls to ≤1.92
   clustered (25,698 signals ÷ ~330 trading days of shared gamma backdrop). Dealer GEX in our data is a
   MARKET-SYNCHRONIZED variable, not a cross-sectional selector. Any naive GEX backtest that treats
   same-day signals as independent will manufacture significance — this is the exact failure mode the
   source system's "100% WR" claim exhibits.
3. **callwall_dist sign is anti-magnetism.** Near-the-wall = strength (mech t_clust −3.31, the only
   clustering survivor), i.e., a momentum echo (stocks that ran sit near their wall), not "room to run"
   pull. It still dies on sub-period sign flips (+/−/− both stability runs), so it is not stageable.

## What was verified (evidence quality)
- Math module self-tested against locked references (BS gamma, synthetic off-center flip 98.5222,
  put/call parity, degenerate chains); scipy cross-check to 5.6e-17.
- Ledger pooling parity code-read: ext skew/pnl15 computed by the *identical imported functions*
  (otm_iv, fwd_pnl); controls recomputed from price_history with the proxy builder's exact formulas.
- Holdout: leak guard passed on both in-sample panels (live cutoff 2026-06-15); guard raise-behavior
  self-tested; no calibration touched any post-cutoff row. NaN-handling bug in Phase 1/4 (drop_nulls
  vs float NaN) found and fixed before reading results; Phases 2/3 masked correctly throughout.
- Not run: adversarial verify panel — reserved for positives; no feature reached PASS on even the
  most favorable (plain-t) reading, so the kill is implementation-independent.

## OOS
0 resolved OOS money rows exist today (pnl15 = 15 trading days; resolves progressively from ~7/09,
fully ~2026-07-29 — requires re-running experiments/iv_skew/build_iv_ext.py first). Given the in-sample
NULL there is nothing to corroborate; the staged 300-row OOS cohort matters ONLY if gex_ratio is ever
re-opened. Do not spend the re-read unless that happens.

## DIRECTION (next iteration — do not start without a decision)
- **Per-trade / cross-sectional GEX re-mining: CLOSED.** Do not re-grade `overall` with GEX features;
  do not re-test regime/flip/wall variants on stock panels. This joins the known-issues "what not to do" family.
- **The one live lead: market-LEVEL GEX as a DD/exposure lever (Stage 3).** The clustering collapse is
  itself evidence that GEX acts at the market level. SPY chains exist in option_prices (~10k rows/date)
  but are NOT in our cache (signal-symbol pulls only). A SPY-only netGEX/flip-regime series
  (~365 dates, ~3.7M rows, ~40 min queued pull + existing dealer_gex.py unchanged) tested as an
  exposure-shaping lever against the RXDD/MWDD/TVDD/BDIV stack is a genuinely NEW input class
  (dealer positioning vs breadth/VIX/TRIN). Prior caveat: the DD-lever well was declared dry after 4
  levers — this lead needs a Stage-3 N=300+ MC bar and the mechanism_registry route, not a quick look.
- **Assets that persist:** gex_chain.parquet (10.3M rows), gex_features.parquet (26,582 signals),
  dealer_gex.py (self-tested), gex_test.py (full harness incl. clustered SEs) — reusable for the SPY
  follow-up and the (conditional) OOS re-read at zero rebuild cost.

## Artifacts
DESIGN.md (pre-registration + all recon amendments) · MATH_SPEC.md (locked math) ·
build_gex_cache.py / dealer_gex.py / gex_test.py · gex_results.txt / gex_results.json ·
.cache/experiment_data/{gex_chain.parquet, gex_features.parquet, gex_build_report.json}
