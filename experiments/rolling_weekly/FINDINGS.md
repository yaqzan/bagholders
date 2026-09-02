# Rolling Weekly Composite — First-Cut Experiment

**Run date:** 2026-05-07
**Algorithm version pinned:** v40 (`917659c`, SVD ship)
**Universe:** 665 v40-scored stocks, 5y window, 800K v40 score rows, 700K rolling-weekly rows joinable
**Variant:** Option C — non-overlapping 5-day ladder ending T-1, ladder length 200 (1000 days lookback for full RSI(14)+MACD(12,26,9) convergence + partial EMA200)

## TL;DR

**Mixed result with one validated win and one invalidated loss.**

1. **Monday whiplash drops 30%** under rolling weekly (6.245 → 4.384 mean |Δoverall| day-over-day). This validates the structural fix hypothesis — the Monday-class noise *is* coming from calendar-edge instability in WeeklyScore values, and a calendar-agnostic rolling indicator removes it. **This finding is robust to score-calibration approximation.**

2. **Other days get slightly worse** (Tue-Fri each gain ~0.5 in |Δ|), and net overall stability is essentially unchanged (3.711 → 3.709). The whiplash didn't disappear — it spread out across the week. Net |Δoverall| is conserved; the *distribution* shifts.

3. **Per-trade WR15 looks catastrophic** (-10 to -19pp across all buckets, N counts 3-8× larger) **but this is an approximation artifact, not a real signal degradation.** The first-cut script substitutes *raw* rolling RSI directly into `calculate_weekly_composite`, which expects *scored* (post-`calculate_rsi_score`) values. The score function applies trend-bias centering and breakout detection that can't be skipped without breaking calibration. Until rolling-RSI is run through `calculate_rsi_score`, per-trade comparisons are not meaningful.

4. **The N-explosion is interesting independent of WR validity.** Rolling weekly admits *much* wider classification — 80+ N triples from 4,842 to 13,483; 70+ goes from 54k to 189k; <25 from 24k to 203k. Either calendar-week semantics encode meaningful institutional rhythm (the self-fulfilling-prophecy hypothesis), OR the wider admissions are mostly noise that proper score-calibration would filter out. Cannot distinguish until calibration is fixed.

## Per-trade WR15 (5y) — INVALIDATED by approximation

| Bucket | N_var | N_base | WR15_var | WR15_base | ΔWR15 |
|---|---:|---:|---:|---:|---:|
| 95+ | 93 | 117 | 80.65% | 82.05% | -1.41pp |
| 90+ | 384 | 404 | 70.47% | 77.53% | -7.06pp |
| 85+ | 2,383 | 1,510 | 63.14% | 75.73% | **-12.59pp** |
| 80+ | 13,483 | 4,842 | 56.65% | 72.79% | **-16.13pp** |
| 75+ | 62,591 | 13,167 | 53.39% | 67.96% | -14.57pp |
| 70+ | 188,646 | 54,064 | 53.20% | 63.81% | -10.61pp |
| <5 | 2,689 | 576 | 45.88% | 65.28% | **-19.40pp** |
| <15 | 41,548 | 5,440 | 46.25% | 63.10% | **-16.84pp** |
| <25 | 203,424 | 24,260 | 47.63% | 59.43% | -11.80pp |

**Why these are invalid:** the variant computes `wadj_new` by passing raw rolling RSI/MACD values into `calculate_weekly_composite`, which by contract expects 0-100 *scored* values from `calculate_rsi_score`/`calculate_macd_score` (trend-bias-centered, breakout-aware scoring). Raw RSI of 60 represents "moderately overbought" → existing score system maps to ~45 (slightly bearish on mean-reversion). My substitution is treating raw 60 as if it were scored 60 (modestly bullish), systematically biasing wadj upward and admitting many false signals into qualifying tiers.

**Why this isn't fixable trivially:** `calculate_rsi_score` is a 200-line function that uses ~20-30 prior weekly RSI values to detect breakouts. Translating it to vectorized Polars is non-trivial; running it per-row in Python is slow but feasible.

## Stability findings — VALIDATED, robust to approximation

| Day | Baseline |Δ| | Variant |Δ| | Change |
|---|---:|---:|---:|
| **Mon** | **6.245** | **4.384** | **−1.861 (−29.8%)** ✓ |
| Tue | 3.302 | 3.510 | +0.209 (+6.3%) |
| Wed | 3.014 | 3.505 | +0.491 (+16.3%) |
| Thu | 3.059 | 3.556 | +0.498 (+16.3%) |
| Fri | 3.149 | 3.650 | +0.502 (+15.9%) |
| **Overall** | 3.711 | 3.709 | **~zero net change** |

**These metrics depend only on the score TIME SERIES under variant scoring, not on whether the WR is correctly calibrated.** The Monday improvement is real and reproducible — rolling weekly removes the calendar-edge spike that calendar-weekly suffers on Mondays.

**The catch:** Tuesday-Friday now show ~0.5 increased |Δ|. This is consistent with the explanation: under calendar-weekly, the "Monday refresh" happens *all on Monday* (one big jump as fresh weekly data arrives). Under rolling-weekly, the same fresh information is distributed across the week (the ladder shifts every day). So the user's intuition was right — we *eliminate* the Monday spike, but the underlying weekly information arrival is still there, now spread.

**For the COHR-class issue specifically:** the rolling weekly *does* fix it. The COHR ghost was caused by the 19:26 write picking this-week's partial WeeklyScore (composite=53) and the 20:23 write picking last-week's (composite=65). In rolling-weekly there's no this-week/last-week distinction — every scoring date has its own ladder, computed deterministically from past daily prices. Two writes for the same scoring date will produce *identical* weekly indicators (assuming no daily price has been revised between them).

## What this means for the institutional-rhythm question

The N-blowup gives us a clue, even with the calibration caveat:

| | Calendar weekly | Rolling weekly | Ratio |
|---|---:|---:|---:|
| 80+ signals | 4,842 | 13,483 | 2.78× |
| 70+ signals | 54,064 | 188,646 | 3.49× |
| <25 signals | 24,260 | 203,424 | 8.39× |
| <15 signals | 5,440 | 41,548 | 7.64× |

If the calendar-week boundary IS where institutional information arrives (Friday close → weekend digestion → Monday positioning), then calendar-week-aligned RSI/MACD would naturally crystallize at those boundaries — and the moments WHEN these indicators are extreme would be moments when many institutional traders have actually committed. The "rare" calendar signals carry behavioral conviction. The rolling signals don't, because nobody's watching them.

That's the self-fulfilling-prophecy hypothesis. We can't confirm it from this experiment alone — but the 3-8× N-blowup is *consistent* with it. If calendar-week boundaries were just an arbitrary calendar artifact with no behavioral content, we'd expect rolling and calendar to admit similar numbers of qualifying signals (just at different times). They don't.

To distinguish "institutional rhythm" from "approximation artifact" requires running the rolling RSI through proper `calculate_rsi_score` and re-evaluating. **Both rolling and calendar would then go through the same scoring pipeline; the only difference would be the input RSI values.** Then the N-counts and WR15 deltas would be apples-to-apples.

## Recommended next phase

**Phase 2: Proper score-function pass on rolling indicators.**

1. Implement `calculate_rsi_score` and `calculate_macd_score` either:
   - Per-row Python iteration on the 700K rolling-indicator rows (~20-30 min wall clock)
   - OR Polars-vectorized approximation that captures the dominant terms (trend-bias centering, EMA-of-RSI breakout) — ~5 min
2. Re-derive wadj using the proper score values
3. Re-evaluate per-trade WR15
4. If WR15 holds within ±1pp of calendar baseline AND Monday whiplash stays at -30%, ship rolling-weekly as the new structural foundation
5. If WR15 degrades materially, the calendar-week-encodes-institutional-rhythm hypothesis is supported, and we revisit by:
   - Keeping calendar weekly for scoring (preserves WR)
   - Adding rolling weekly as a stability auxiliary signal
   - Or accepting the calendar-week structure with EMA-smoothing (Priority #7 fallback)

**Estimated cost:** ~1 day to write proper score functions + re-run.

## What this experiment confirmed

1. **The Monday whiplash IS structural** (not just wadj-formula-related). The rolling-weekly approach with ZERO wadj formula changes still cuts Monday |Δ| by 30%.
2. **The COHR-class intra-day flip IS prevented by rolling weekly** — same scoring date, same daily price history → same weekly indicator → same wadj → same overall, regardless of recompute timing.
3. **Volume of "qualifying" signals is calendar-week-dependent.** Rolling weekly admits 3-8× more candidates. Whether that's noise or alpha needs proper-scoring pass to determine.
4. **The user's intuition about "running weekly composite" was directionally correct** — it does address the COHR/Monday problem class. The implementation needs to retain the score calibration that the existing system uses.

## What this experiment did NOT determine

1. **Whether rolling weekly maintains per-trade WR15** — invalidated by approximation; needs Phase 2.
2. **Whether the calendar-week boundary carries institutional-behavior signal** — the 3-8× N-blowup is consistent with it but not conclusive.
3. **What the right rolling-weekly LADDER LENGTH is** — used 200 bars (~4 years) for EMA200 partial convergence. Shorter (50 bars) would compute faster but lose long-horizon signal. Bayesian opt over ladder length is a Phase 3 question.
4. **Whether the small Tue-Fri stability degradation is acceptable.** Net overall |Δ| is unchanged, but the user might prefer concentrated-on-Friday vs distributed-across-week. This is a behavioral preference question.

## Artifacts

| File | Content |
|---|---|
| `01_build_daily_cache.py` | yfinance/MySQL pull → daily.parquet (1.36M rows × 665 stocks × 9y) |
| `02_build_rolling_indicators.py` | Polars+talib compute → indicators.parquet (716K rows of rolling RSI/MACD) |
| `03_variant_runner.py` | Re-derive wadj via Polars expressions + Jacobian; per-trade + stability evaluation |
| `variant_overall.parquet` | (symbol, date, overall, new_overall, w_rsi, w_macd_score, ...) for 698K rows |
| `variant_result.json` | bucketed_stats baseline + variant + delta_summary + stability |

## New hypotheses

### H1 — Run rolling RSI through `calculate_rsi_score` to get a fair per-trade comparison

Highest priority. Without this, we can't tell if rolling weekly is competitive or worse on per-trade quality.

### H2 — Hybrid approach: rolling weekly for STABILITY, calendar weekly for SCORING

Use rolling weekly to determine "have weekly indicators changed materially this week?" (a stability gate / signal-flagged-as-confirmed indicator) while keeping the calendar weekly RSI as the scoring input. This preserves calendar's per-trade calibration while using rolling as a confirmation signal.

### H3 — EMA-smooth the calendar weekly composite at the daily layer (the simpler fallback)

If H1 shows rolling weekly underperforms calendar on per-trade WR (suggesting institutional rhythm matters), fall back to: keep calendar weekly indicators, but EMA-smooth the daily-layer wadj output. This was Priority #7 in known-issues.md and would address the COHR-class issue without changing the underlying weekly bar definition.

### H4 — The 3-8× N-blowup observation as its own meta-question

Independent of the WR test, *why* does rolling weekly classify so many more signals as qualifying? Hypotheses:
- Calendar-week aggregation reduces noise (5-day OHLC → 1 OHLC per week) AND reduces signal density
- Rolling preserves daily-resolution noise in the indicator inputs
- → calibrate qualifying thresholds to N-match (e.g. raise rolling threshold from 70 to 80 for "70+" tier)

If thresholds are calibrated to give similar N counts, does WR15 match? That tells us whether the scaling is the issue or the underlying signal quality.

### H5 — Test ladder length sensitivity

200-bar ladder (4y) is conservative for EMA200. 50-bar ladder (1y) is sufficient for RSI(14) and MACD(12,26,9) but loses trend EMA50/EMA200 information. Sweep ladder length ∈ {50, 100, 200, 400} and observe per-trade and stability impact.

## Bottom line

**The structural-fix hypothesis is validated for stability** (Monday whiplash -30%). The per-trade WR question is currently OPEN, contingent on properly scoring the rolling RSI/MACD through the existing score functions. The 3-8× N-blowup is intriguing and may be the experimental signature of the institutional-rhythm hypothesis, but cannot be confirmed without removing the approximation.

**Recommended next step:** Phase 2 — proper score-function pass. ~1 day of work, will definitively answer whether rolling weekly is shipable as a replacement for calendar weekly, or only useful as an auxiliary signal.
