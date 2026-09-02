# Phase Pri-1 — Call/Put Priority Refinement (NULL RESULT)

**Date**: 2026-04-28
**Algorithm version**: v28 (e3c8678)
**Question**: Now that v28 puts match or beat calls at comparable conviction tiers, can a per-tier empirical-WR-driven priority (`wr_merged`) outperform the binary `calls_first` / `puts_first` / `merged` modes?

## TL;DR

**No.** 18 configs evaluated (12 seeds + 6 adaptive Bayesian iterations); converged. Production `calls_first` (utility +5.229) dominates every alternative:
- All 9 tested `wr_merged` variants lose to V0 by 19-83 utility points; most breach the 80% Conservative DD floor.
- Raw `merged` (existing `abs(score-50)` priority) loses by 11.8 utility points.
- `puts_first` collapses utility to -94.1 (severe DD breach).

**Do NOT ship**. `calls_first` remains optimal. The cause is structural: at v28 per-tier WRs, all 3 put tiers (74.5/73.0/74.0) outrank call mid (72.7) and low (72.1) — so any empirical-priority queue at neutral bias elevates puts above two thirds of the call cascade, producing put-flooded portfolios with the same DD dynamic as `puts_first`. Strong call-bias (≥1.06) suppresses puts but introduces within-tier random scrambling that costs more utility than it recovers.

**N=500 validation skipped**: every wr_merged candidate fails the V0 utility floor by 19+ points at N=100. The N=500 noise floor on log-return is ±5-9% per the v27 optimization log — nowhere near enough to flip a 19-utility-point gap. Validation would burn ~50 min of compute on guaranteed losses.

## Per-tier WR table (active v28 5y assessment, 2026-04-28)

| Tier (key) | Score range | WR15 | WR30 | N | Discrete IC15 |
|---|---|---:|---:|---:|---:|
| ultra | 95+ | 88.9% | 91.6% | 154 | +0.0414 |
| top   | 85-94 | 77.1% | — | 2,089 | +0.0730 |
| mid   | 80-84 | 72.7% | — | 4,973 | +0.0359 |
| low   | 75-79 | 72.1% | — | 12,574 | -0.0084 |
| overflow | 70-74 (alloc=0) | 69.7% | — | 38,642 | +0.0063 |
| put_top | ≤15 | 74.5% | 75.1% | 6,818 | +0.05 |
| put_mid | 16-20 | 73.0% | — | 5,826 | +0.04 |
| put_low | 21-25 | 74.0% | — | 9,706 | +0.04 |

**Headline observation**: at v28, all 3 put tiers (74.5/73.0/74.0) outrank call mid (72.7) and low (72.1). The two highest call tiers (ultra 88.9, top 77.1) sit above all puts.

## Sweep results (N=100, 22-now × 3 modes)

**18 configs evaluated** — 12 hand-curated seeds + 6 adaptive Bayesian iterations. Converged after 2 flat batches (no >2% improvement on best utility).

Decomposed: every config that produced a different utility score is shown (the 4 calls_first variants tie at +5.229 because formula/bias/fallback are no-ops when PUT_PRIORITY=calls_first).

| Rank | PUT_PRIORITY | FORMULA | BIAS | FALLBACK | Utility | logRet | MaxDD% | Note |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1-4 | calls_first | — | — | — | **+5.229** | +24.26 | 73.0 | V0 — current ship |
| 5 | merged (raw conviction) | — | — | — | −6.558 | +19.44 | 74.3 | existing pre-Pri-1 |
| 6-7 | wr_merged | WR15 | 1.03 | 65/70 | −13.733 | +24.34 | 76.3 | best wr_merged |
| 8 | wr_merged | WR15xLogN | 1.00 | 65 | −31.702 | +17.56 | 77.8 | logN dampened |
| 9-10 | wr_merged | WR15 | 1.00/0.97 | 65 | −42.628 | +17.01 | 79.1 | neutral / put-favor |
| — | wr_merged | WR30 | 1.00 | 65 | −42.628 | — | 79.1 | (matches WR15) |
| — | wr_merged | WR15xIC | 1.00 | 65 | −42.628 | — | 79.1 | IC weighting |
| — | wr_merged | EV | 1.00 | 65 | −42.628 | — | 79.1 | (monotonic in WR15) |
| — | wr_merged | WR15 | 1.06 | 65 | −78.155 | — | 81.4 | over-bias-call |
| — | puts_first | — | — | — | −94.076 | — | 86.2 | DD floor breach |

**Key dynamic visible in the table**: log-returns are similar across the top mode candidates (V0=+24.26, best wr_merged=+24.34) — the optimizer is differentiating purely on **DD**. wr_merged variants run 3-9pp higher MaxDD than V0, which compounds quadratically into the DD penalty term and dominates utility.

## Why every wr_merged variant fails

**The core problem**: The discrete WR15 differences between call mid/low (72.7/72.1) and put tiers (74.0/73.0/74.5) are smaller than MC noise (±2pp at N=100), but rank-order priority treats these as definitive. Three failure modes observed:

1. **call_bias ≤ 1.04 (most variants)** — all 3 put tiers rank above call mid/low. After ultra/top calls fire, the next 7 tiers in the queue are 3 puts then 2 calls then overflow — slot pool floods with puts identical to `puts_first` behavior. DD breaches.

2. **call_bias = 1.06** — call tiers all outrank puts (calls_first-like ordering), BUT:
   - Within-tier order randomizes (mid signals fire random instead of by score-desc 84→80)
   - Put fill order inverts (put_low @ 74.0 outranks put_mid @ 73.0, so 21-25 puts fire before 16-20 puts; calls_first does score-asc = 16 first)
   - Net effect: utility −78.155 (worse than 1.03!), suggests the within-tier scrambling is destructive.

3. **WR15xLogN** (least bad at −31.7) — log-N dampening boosts large-N tiers, INVERTING low (75-79, N=12,574) above mid (80-84, N=4,973). Conviction-vs-volume confound. Still a DD breach.

## Cross-tier IC is too noisy to drive priority

The discrete-band IC values are mostly ±0.04 to ±0.07, with one outlier at top calls (+0.073) and another at low calls (−0.0084). These are within noise for the population sizes involved (Pearson IC standard error ≈ 1/√N ≈ 0.01 for N=10k, 0.04 for N=600). Any priority formula derived from these IC values is over-fitting noise.

This is the same lesson as Priority #6 (regime-aware allocation A050 conviction-dampening, NULL RESULT 2026-04-24) and Priority #11 (put rollup variants, NULL RESULT 2026-04-23): **per-trade barrier-touch WR is not a safe proxy for option-TP rate at portfolio scale**, especially when the WR differences (1-2pp) are within MC noise.

## What changed at v28 that motivated this experiment

v28 (e3c8678) shipped the earnings meta-score boost which lifts pre-earnings high-conviction signals. Per-tier WRs at v28 (5y):
- 95+ calls: 88.9% (vs v25 ~85%)
- ≤5 puts: 79.0% (vs v25 ~73%)
- 75+ calls: 72.9% (vs ≤25 puts: 73.9%) — puts EDGE OUT calls at the broadest tier

The hypothesis was that empirical-priority would naturally route to whichever side had the better signal each day. In practice, the cascade structure ALREADY does this implicitly through per-tier allocation fractions — the 14-slot pool with calls_first ordering captures all qualifying calls before puts compete, and the v28 put tier WRs (73-74%) match the 75-79 call tier (72%) at slot-equivalent positions. There's no alpha to recover from priority re-ordering.

## Code state

- `monte_carlo.py:128-152` — added `wr_merged` priority mode + `WR_PRIORITY_TABLE` / `WR_PRIORITY_FALLBACK` globals (production default still `calls_first`, no behavior change).
- `monte_carlo.py:1011-1049` — `wr_merged` block in the priority dispatch.
- `experiments/v28_optimization/priority_data.py` — pulls 5y assessment WR/IC, computes discrete-band tier stats, builds priority tables under each formula variant.
- `experiments/v28_optimization/phase_pri1_priority.py` — Bayesian sweep harness.
- `experiments/v28_optimization/phase_pri1_validate.py` — N=500 validation harness (NOT RUN — every wr_merged variant fails the +5.229 → ship gate by 18+ utility points; validating would burn compute on guaranteed losses).

The `wr_merged` priority mode is preserved in production code as research scaffolding (env-gated, default off). It can be reactivated for future experiments without further architecture work.

## What NOT to retry

1. **Do NOT pursue any wr_merged variant of `monte_carlo.PUT_PRIORITY`.** Every formula × bias × fallback combination tested at N=100 fails the V0 utility floor by 18+ utility points. The structural issue (puts outranking call mid/low → DD breach) is independent of formula.

2. **Do NOT split tier-allocation per side based on assessment WR.** The per-tier allocations (TIER_ALLOC, PUT_TIER_ALLOC) are already empirically tuned — adjusting them based on observed WR differences would re-introduce the same per-trade-vs-portfolio confound that made A050 fail.

3. **Do NOT retry priority schemes based on `IC` from the assessment.** Discrete-band IC values are within noise for the population sizes; any IC-weighted priority over-fits.

4. **Do NOT extend to 5-year-window WR data.** The 5y window already informs the existing tables; lookback-window choice (1y / 2y / 3y / 5y) doesn't address the underlying issue that priority order matters less than tier allocation under the 14-slot cap.

## Open question for future work

The one untested-but-plausible direction is **per-day signal-quality prioritization** — instead of per-tier static priority, use signal-time features (regime composite, breadth velocity, earnings proximity) to boost individual-signal priority within the merged queue. This would not fall to the per-trade-WR-vs-option-TP confound because it's an entry filter, not a static rank table. But it's substantially more complex and outside Phase Pri-1's scope.

## Files

- `experiments/v28_optimization/phase_pri1.out` — full sweep log
- `experiments/v28_optimization/phase_pri1_top_candidates.json` — top-10 ranked by utility
- `experiments/bayes_logs/phase_v28_phase_pri1.jsonl` — per-eval JSONL telemetry
