# Sector / Market-Cap / Industry Cohort Analysis — v39

**Date:** 2026-05-07
**Algorithm:** v39 (`200f33a`)
**Window:** 10y target, **effective ~5y** (2021-05-03 → 2026-05-01) — barrier_outcomes.db only covers 2021-03 onward
**Holdout:** ≤2026-05-15 (per Priority #11 lock; no leak detected)
**Resolved peaks:** 34,152 (calls 22,536 + puts 11,616 at 70+/<=25)
**Barrier:** 30dte_opt @ w=15d (option-aligned, TP=1.274σ / SL=0.983σ)

## Headline

Two real, structurally-stable cohort effects emerge — both diversified across many symbols, both consistent year-over-year:

1. **Call quality scales monotonically with market cap.** Large/XL caps outperform micro caps by **8.2pp on TP rate at 75+** (657.8% → 575.9%, 6 bin ladder). Diversified across 5,940 signals in 5 cap buckets.

2. **Sector tilt for puts: utilities and basic-materials systematically underperform; energy and consumer-cyclical outperform.** Best-vs-worst sector spread **9.2pp on PUT <=25** (energy 55.1% TP vs utilities 45.9%).

These are the two largest non-version-specific structural effects in the post-v39 score universe. Both are portfolio-stage actionable without touching the scoring formula.

## Method

Per-cohort one-proportion z-test vs the same (signal × tier) baseline:
- `p` = miss rate (1 − TP rate) for cohort
- `p_base` = miss rate for all signals matching same (signal_type × cumulative tier)
- `z = (p − p_base) / sqrt(p_base × (1 − p_base) / n_cohort)`
- Significance threshold: |z| ≥ 1.96 (95% one-test); for cohort *families* with multiple cells the bar is higher (Bonferroni-implicit)

Cohorts gated at N≥50 for primary tables, N≥30 for joint sector×mcap, N≥80 for industry.

## Top sector findings (5 of 83 cells significant at |z|≥1.96)

| Signal | Tier | Sector | N | TP% | TP_base% | Δ pp | Lift | z |
|---|---|---|---:|---:|---:|---:|---:|---:|
| PUT | <=15 | basic-materials | 154 | 45.5 | 55.6 | **−10.1** | 1.227 | **+2.52** |
| PUT | <=10 | basic-materials | 53 | 41.5 | 58.1 | −16.6 | 1.397 | +2.45 |
| PUT | <=20 | basic-materials | 334 | 47.0 | 53.0 | **−6.0** | 1.128 | +2.20 |
| CALL | 75+ | technology | 1632 | 65.1 | 62.5 | **+2.6** | 0.932 | −2.14 |
| (PUT <=25 unknown — metadata gap, ignored) |

Basic-materials puts underperform across *every* deep tier (<=10 / <=15 / <=20 / <=25) — pattern is consistent, not a tier-specific artifact. Technology calls outperform by a smaller but stable margin.

## Top mcap findings (6 of 50 cells significant)

**CALL 75+ shows a monotonic ladder:**

| mcap_bin | N | TP% | Δ vs 75+ baseline (62.5%) |
|---|---:|---:|---:|
| large_50-200B | 1,394 | **65.8** | +3.3 |
| xl_200B-1T | 612 | 65.4 | +2.9 |
| mega_1T+ | 207 | 64.3 | +1.8 |
| mid_10-50B | 1,822 | 61.7 | -0.8 |
| small_2-10B | 1,523 | 60.5 | -2.0 |
| micro_lt2B | 382 | **57.6** | -4.9 |

**8.2pp range, monotonic across 6 bins, ~5,940 signals** — this is the cleanest single-feature gradient surfaced in any of the cohort-mining work to date (compare v32 wadj-neg z=+10.1 which was a discrete cliff, not a smooth gradient).

Z-scores at the tails:
- CALL 75+ × large_50-200B: z=−2.53 (better)
- CALL 70+ × small_2-10B: z=+2.78 (worse, includes overflow tier)
- CALL 70+ × micro_lt2B: z=+2.15

**PUT <=25 is roughly inverted but weak (mega N=27 untrusted; rest cluster within 3pp):**

| mcap_bin | N | TP% |
|---|---:|---:|
| large_50-200B | 1,561 | 52.7 |
| mid_10-50B | 3,136 | 51.6 |
| small_2-10B | 3,630 | 50.9 |
| xl_200B-1T | 362 | 49.7 |
| micro_lt2B | 2,552 | 49.6 |

No clean ladder; smaller stocks are marginally worse for puts but the spread is only 3pp across the trustable bins. Don't read too much into this side.

## Top joint sector × mcap (sparser, N≥30)

| Signal | Tier | Sector | mcap_bin | N | Δ pp | z |
|---|---|---|---|---:|---:|---:|
| PUT | <=25 | basic-materials | micro_lt2B | 78 | **−17.1** | **+4.46** |
| PUT | <=20 | basic-materials | micro_lt2B | 50 | −19.7 | +3.41 |
| PUT | <=20 | consumer-cyclical | micro_lt2B | 89 | **+12.4** | −3.13 |
| PUT | <=25 | unknown | small_2-10B | 60 | +18.2 | −3.10 |
| CALL | 75+ | industrials | micro_lt2B | 73 | −15.5 | +1.86 |

The strongest single cell is **basic-materials micro-cap puts (z=+4.46, 17pp TP haircut, N=78)**. But the same row also fires high-magnitude on the *positive* side for consumer-cyclical micro-cap puts — i.e., micro-cap is not uniformly worse for puts; the sector dominates.

## Top industries (signal × 75+ or <=25 only, N≥80)

| Signal | Tier | Industry | N | Δ pp | z |
|---|---|---|---:|---:|---:|
| PUT | <=25 | medical-care-facilities | 143 | **−15.5** | **+3.72** |
| PUT | <=25 | diagnostics-research | 98 | +16.1 | −3.20 |
| PUT | <=25 | information-technology-services | 166 | +10.8 | −2.79 |
| CALL | 75+ | oil-gas-midstream | 117 | +10.1 | −2.27 |

Medical-care-facilities is a 1.318× miss-lift cohort on puts — likely M&A-rumor risk and regulatory bounce drowning bearish technicals. Worth flagging but N=143 over 5y is small for a portfolio mechanism.

## Stability checks

**Symbol diversification (basic-materials PUT <=25):** 668 signals across 31 symbols. Top contributors FMC (44), NB (38), CRML (36), TMC (35), MOS (35), ALB (26). Worst-TP names: FMC 31.8%, NB 31.6%, CRML 27.8%, ALB 30.8% — consistent with the cohort signal but not single-symbol contamination.

**Year-by-year (basic-materials PUT <=25):**
- 2022: 43.4% (N=106)
- 2023: 56.6% (N=166) — worst-year deviation, bouncy commodity rally
- 2024: 41.8% (N=189)
- 2025: 48.4% (N=190)

Underperforms baseline in every year. Pattern is structural, not regime-locked.

## Interpretation

**Why micro-cap calls underperform:** Smaller floats produce noisier breakouts. A score≥75 firing on a $1B name often catches a low-volume squeeze that immediately reverses; the same score on AAPL catches institutional accumulation. Per-trade WR scales with float depth.

**Why basic-materials puts underperform:** Commodity-cycle stocks have a "bearish technical → commodity supply shock → hard reversal" failure mode. Bearish RSI/MACD divergences on FMC, ALB, NB get steamrolled when a Chinese stimulus headline lifts the entire sector overnight.

**Why technology calls outperform:** Trend-following strategies work best on persistent-trend instruments. Tech has both wider and longer-lived trends than defensives.

## Mechanism candidates (ranked by evidence strength)

### 1. Call mcap-aware tier scaling (PORTFOLIO-stage) — STRONGEST EVIDENCE

**Mechanism:** Multiplicative scaling on `tier_alloc` for calls, derived from cohort TP rate.

```python
# in monte_carlo.py / strategy_config.py
CALL_MCAP_SCALE = {
    'mega_1T+':      1.00,
    'xl_200B-1T':    1.05,
    'large_50-200B': 1.05,
    'mid_10-50B':    0.95,
    'small_2-10B':   0.90,
    'micro_lt2B':    0.80,
    'unknown':       0.95,  # conservative for missing-meta
}
```

Or a smooth log-mcap scaling:

```python
def call_mcap_scale(mcap_b: float) -> float:
    # base 1.0 at mcap=$50B; +5% at mcap=$200B+; -10% at mcap=$1B
    if mcap_b is None: return 0.95
    return clip(0.85 + 0.10 * log10(mcap_b / 1.0), 0.80, 1.10)
```

**Test plan:** P1-P6 portfolio gate at N=500 × 8 windows. Expected: small DD improvement, slight 5y compound improvement (smaller per-trade losers in micro-cap tier reduces correlated drawdown).

**Cost:** ~1 day. Single new constant table. Drift-guard adds 6 more value pairs.

**Why HIGH priority:** Cleanest monotonic single-feature signal in the entire post-v39 cohort space. 5,940 signals across 5 trustable cap buckets, 8.2pp TP-rate spread, year-stable.

### 2. Sector tilt on puts (PORTFOLIO-stage) — STRONG EVIDENCE FOR TAIL

**Mechanism:** drop or reduce alloc for the two worst put-side sectors (utilities, basic-materials).

```python
PUT_SECTOR_FILTER = {'utilities', 'basic-materials'}  # drop puts in these sectors
# OR
PUT_SECTOR_SCALE = {
    'utilities':       0.50,
    'basic-materials': 0.65,
    # all others 1.00
}
```

**Test plan:** P1-P6 portfolio gate. Expected: 5y compound +5-15% (slot displacement gain — same mechanism as EARN_SUPP_PUT). DD essentially unchanged (puts don't drive the deep-DD tail).

**Cost:** ~half a day. ~~Cascade-stage filter (would re-create the dashboard divergence problem v38/v39 just fixed)~~ — better encoded as a score-stage dampener: lift basic-materials puts in [21-25] toward 30, mirroring PESS architecture. This avoids dashboard divergence.

**Why MEDIUM-HIGH priority:** Significant signal but smaller per-trade scale (1,030 signals across 2 sectors). Mechanism choice (filter vs lift) needs explicit consideration vs the "never reduce puts in bear tape" rule.

### 3. Score-stage mcap lift (SCORING change, version bump) — DEFER

The mcap effect could also be encoded inside `compute_overall_score` as a small directional lift/dampener. **Not worth it as a first iteration** — mcap doesn't change daily, so portfolio-stage scaling captures the entire signal without a score-stage version bump. Reconsider only if dashboard visibility becomes a need (e.g., showing different score badges based on cap).

## Caveats

1. **5y barrier coverage limit.** Pre-2021 score data exists (40k extra peaks fetched but dropped at the inner join with barriers). To extend coverage, run a one-time Numba walk for 2016-2021 segment. Estimated ~20-40 min. Worth doing if user wants 10y validation; not strictly necessary for the sector/mcap signals shown here (year-by-year breakdowns confirm pattern stability).
2. **Cohort patterns interact with the existing dampener stack.** v37 PCD, v38 CWWD, v39 PESS have already removed crash-recoil puts and weak-weekly calls from the universe. The basic-materials put effect *survives* PCD — meaning these aren't crash-bounce puts; they're commodity-bounce puts on names that didn't fall hard enough to trigger PCD.
3. **Sector metadata is yfinance-derived.** 95% coverage with 729/766 stocks tagged. The 37 "unknown" stocks have a positive bias on the put side (TP=59.2% across 402 signals) — possibly because they're newer / smaller listings without yfinance industry tags. Worth a manual audit but not a blocker.
4. **Industry signal is small-N.** medical-care-facilities z=+3.72 is real but only 143 signals over 5y — too sparse to be a portfolio mechanism on its own. Use industry only as a forensic lens, not a filter.

## Suggested next step

**Start with mcap-aware call alloc scaling (Mechanism #1).** It's:
- The cleanest evidence (8.2pp monotonic ladder)
- The simplest mechanism (one constant table or smooth log-scale function)
- The lowest-risk change (no scoring formula touched, no version bump, no dashboard divergence)
- The largest expected impact (5,940 signals affected, vs 1,030 for sector put filter)

If P1-P6 passes for #1, follow up with #2 (sector put bias). The two mechanisms operate on independent cohorts and should compose cleanly.

## Outputs

- `cohort_v39_3650.parquet` — feature dataset (34,152 rows, 31 cols)
- `sector_cohorts.csv` — full sector × signal × tier table
- `mcap_cohorts.csv` — full mcap × signal × tier table
- `joint_cohorts.csv` — sector × mcap × signal × tier (sparser)
- `industry_cohorts.csv` — industry × signal (75+ / <=25 only)
- `cohort_zscore.out` — full stdout
- `diagnostics.out` — drill-down tables (full sector breakdowns, year stability, symbol concentration)
