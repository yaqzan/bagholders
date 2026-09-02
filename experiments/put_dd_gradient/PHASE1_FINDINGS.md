# Phase 1 — Diagnostic Findings

## Critical reframe: the premise has shifted

**Original premise:** "high put scores are catastrophic to DD despite high WR"

**Actual pattern (5y, v31, 30dte_opt barriers, w_days=15):**

| Side | Bucket | N | WR15 (K=1.0σ) | OptTP15 (1.092σ) | AvgBars |
|---|---|---:|---:|---:|---:|
| CALL | 70-74 | 16,575 | 58.0% | 58.1% | 1.60 |
| CALL | 75-79 | 5,382 | 58.7% | 58.8% | 1.52 |
| CALL | 80-84 | 1,830 | 60.6% | 60.7% | 1.49 |
| CALL | 85-89 | 551 | 67.1% | 67.1% | 1.39 |
| CALL | 90-94 | 157 | 65.6% | 65.6% | 1.47 |
| CALL | 95+ | 43 | 69.8% | 69.8% | 1.19 |
| PUT | 21-25 | 8,073 | 48.6% | **51.2%** | 1.39 |
| PUT | 16-20 | 4,721 | 49.5% | 50.3% | 1.41 |
| PUT | 11-15 | 2,548 | 51.8% | 48.0% | 1.42 |
| PUT | 6-10 | 1,010 | **55.7%** | **44.2%** | 1.36 |
| PUT | <=5 | 494 | 52.2% | 47.8% | 1.39 |

### Three findings

**1. Calls show WR15 ≈ OptTP15. Puts diverge dramatically with depth.**
- Calls: barrier-touch WR matches option TP rate at every conviction tier.
- Puts: deeper conviction → HIGHER barrier-touch WR (capitulation often pierces the loose 0.71σ barrier within 15d) but LOWER option TP rate (capitulation reverses fast, doesn't sustain the 1.092σ move needed for +30% premium).

**2. Put N is no longer the runaway problem.**
- Put count 5y: 16,846. Call count: 24,538. **Put:call = 0.69×** (puts are now LESS frequent than calls).
- Old "5:1 ratio" docs are stale — wcf_lift (v27, shipped 2026-04-27) already cut put N by ~75% on the weak-weekly cohort. The user's premise that "put N is too high" is largely already addressed.

**3. The DD risk is concentrated in macro-correlated days, not deep buckets.**
- Per-day put fire distribution: median 11/day, p95=35/day, **max=124/day** (2022-08-30).
- Top 20 highest put-density days are ALL in 2022 (Aug-Sep + Dec) and Feb 2023 — sustained bear-tape clusters.
- 2.1% of days have ≥50 put fires. These days produce correlated SL clusters → DD spikes.

### What this means for the intervention

The original mental model ("deep puts are quality-degraded individually") is partially wrong. The data says:
- **Per-trade level**: deep puts (`<10`, `<5`) ARE quality-degraded for option strategy specifically (44-48% OptTP15 vs 56.3% break-even). But the issue is the K=1.0σ vs 1.092σ barrier mismatch, not raw directional accuracy.
- **Portfolio level**: DD damage comes from macro-correlated days (50+ concurrent puts) where many positions move together.

These are TWO different problems with different fixes:

| Problem | Best lever | What it looks like |
|---|---|---|
| Per-trade OptTP gap on deep puts | Score-stage **depth-graduated dampener** (smooth/log) — lift deeper puts more aggressively toward neutral | Reduce N at <=15 buckets, especially <=10 / <=5 |
| Macro-correlated DD spikes | Portfolio-stage **daily put cap** OR breadth-aware position throttle | Cap N puts/day, OR scale alloc by daily put-fire count |

## Recommended next steps (auto-proceeding)

1. **Phase 1C — MC ablation** (running next, ~30 min): drop_lt5 / drop_lt10 / drop_lt15 vs baseline on dip+22-now+5y at N=200. Direct test of "does cutting deep puts at the cascade input reduce DD?" If yes, that's the floor case for any score-stage mechanism.

2. **Phase 2 — Mechanism design** (focus shifted):
   - Primary: **smooth depth-graduated lift** at the score stage. `lift = K × log(1 + max(0, depth_threshold - overall)) / log(depth_threshold + 1) × (target - overall)`. Stacks on top of v27 wcf_lift.
   - Secondary (if portfolio-side intervention is acceptable): **daily put cap** at portfolio-stage. Caps total puts admitted to cascade per day (sorted by conviction). Doesn't change scoring at all.

3. **Phase 3+4 — Bayesian sweep + canonical MC** at N=500 × 8 windows. DD-primary gate.

The depth-graduated lift is the cleaner score-stage answer to the user's request. The daily put cap is the cleaner answer to the macro-correlation DD source — but it's portfolio-stage, not score-stage. Worth raising both.
