# Weekly-Adjustment Day-of-Week Dampener — Bayesian Optimization

**Run date:** 2026-05-06
**Algorithm version pinned:** v40 (`917659c`, SVD ship)
**Universe:** All v40-scored stocks, 5y window (800,312 score rows, 37.4M barrier outcomes)
**Optimizer:** scikit-optimize `gp_minimize`, 80 evaluations, EI acquisition
**Approximation:** linear-Jacobian — `new_overall ≈ overall + (new_wadj − wadj) × regime_factor` where `regime_factor = regime_mult` if pre_regime ≥ 50 else `(2 − regime_mult)`. Exact when vol_mult ≈ 1 (the NEUTRAL majority case); approximate otherwise. Final variant validation requires the simulator path.

## TL;DR

**NULL RESULT for whiplash reduction.** The 5-parameter day-of-week wadj dampener does NOT reduce score volatility — it makes it worse. The optimizer's "best" 5y solution overfits per-trade WR and fails H5 sign-consistency on 5 of 9 buckets. Targeted Monday-only dampening also fails: aggressive forms create boundary effects at Fri→Mon, mild forms (Mon=0.85) give marginal 3.6% Monday-only improvement at cost of 0.5pp WR15 on 95+.

**The whiplash isn't from wadj's day-of-week trustworthiness — it's from the WeeklyScore values themselves jumping at the Mon/Wed boundary.** Dampening wadj at the daily layer treats a symptom; the actual fix is at the WeeklyScore computation (don't compute weekly indicators from partial weekly bars).

## What we measured

### Baseline (5y, v40, no dampening)

| Bucket | N | WR15 | WR30 |
|---|---:|---:|---:|
| 95+ | 120 | 80.83% | 84.17% |
| 90+ | 416 | 77.22% | 78.37% |
| 85+ | 1,573 | 75.81% | 75.78% |
| 80+ | 5,112 | 72.48% | 72.16% |
| 75+ | 14,049 | 67.80% | 68.17% |
| 70+ | 58,709 | 63.55% | 63.32% |
| <5 | 699 | 62.95% | 65.24% |
| <15 | 6,589 | 63.58% | 63.32% |
| <25 | 30,371 | 59.27% | 58.22% |

**Baseline stability** (mean |Δoverall| day-over-day): **3.732**

### Per-day-of-week stability (the key insight)

| Day | Baseline |Δ| | N pairs |
|---|---:|---:|
| **Mon** | **6.234** | 148,645 |
| Tue | 3.339 | 165,817 |
| Wed | 3.042 | 164,509 |
| Thu | 3.088 | 159,345 |
| Fri | 3.171 | 161,332 |

**Monday's whiplash is ~2× the rest of the week.** This confirms the user's intuition that Monday IS the day where partial-weekly-data noise dominates. But fixing it via wadj dampening doesn't work (see below).

## Phase 2 — 5-D Bayesian optimization

Search space: `[d_mon, d_tue, d_wed, d_thu, d_fri] ∈ [0, 1.5]^5`
(Allow >1 to test "wadj is undertrusted" hypothesis; allow 0 to test pure neutralization.)

Loss: weighted sum of WR15 deltas across 9 affected buckets, with 5pt N-floor penalty per primary tier (75+, 80+, 85+, 90+) that drops >15% in N.

### Best result (loss = -17.72)

| Day | Coefficient |
|---|---:|
| Mon | **0.632** (mild dampen) |
| Tue | 1.088 (slight amp) |
| Wed | 1.066 (slight amp) |
| Thu | 1.146 (moderate amp) |
| **Fri** | **0.313** (heavy dampen) |

### Per-trade detail (5y, vs baseline)

| Bucket | N_var | N_base | ΔN% | WR15_var | WR15_base | ΔWR15 |
|---|---:|---:|---:|---:|---:|---:|
| 95+ | 84 | 120 | **-30%** | 89.29% | 80.83% | **+8.45pp** |
| 90+ | 398 | 416 | -4% | 80.70% | 77.22% | +3.48pp |
| 85+ | 1,413 | 1,573 | -10% | 76.98% | 75.81% | +1.17pp |
| 80+ | 4,519 | 5,112 | -12% | 73.83% | 72.48% | +1.35pp |
| 75+ | 13,353 | 14,049 | -5% | 68.79% | 67.80% | +0.99pp |
| 70+ | 53,838 | 58,709 | -8% | 64.19% | 63.55% | +0.64pp |
| <5 | 753 | 699 | +8% | 65.47% | 62.95% | +2.52pp |
| <15 | 6,094 | 6,589 | -7% | 63.87% | 63.58% | +0.29pp |
| <25 | 30,754 | 30,371 | +1% | 59.39% | 59.27% | +0.12pp |

Looks great on 5y. **But:**

### H5 sign-consistency check (1y / 3y / 5y)

| Bucket | 1y | 3y | 5y | Consistent? |
|---|---:|---:|---:|:---:|
| 95+ | +16.67 | +2.88 | +8.45 | ✓ |
| 90+ | +0.63 | +5.08 | +3.48 | ✓ |
| 85+ | **−3.01** | −0.93 | +1.17 | ✗ |
| 80+ | **−0.89** | +0.03 | +1.35 | ✗ |
| 75+ | +0.02 | +1.11 | +0.99 | ✓ |
| 70+ | **−0.07** | +0.21 | +0.64 | ✗ |
| <5 | +5.77 | +1.85 | +2.52 | ✓ |
| <15 | +3.15 | **−0.49** | +0.29 | ✗ |
| <25 | +0.01 | **−0.10** | +0.12 | ✗ |

**Fails H5 on 5 of 9 buckets, including 80+ and 85+ — primary cascade tiers.** The 5y win is window-specific overfit. Per assessment-backtest.md guidance: "A 5y win that flips at 1y is noise."

### Stability under "best" variant

| | Baseline |Δ| | Best variant |Δ| | Change |
|---|---:|---:|---:|
| ALL | 3.732 | 4.375 | **+17.2% (worse)** |
| Mon | 6.234 | 4.820 | -22.7% (better) |
| Tue | 3.339 | 4.549 | +36.2% (worse) |
| Wed | 3.042 | 3.079 | +1.2% (~same) |
| Thu | 3.088 | 3.187 | +3.2% (slightly worse) |
| **Fri** | **3.171** | **6.282** | **+98% (much worse)** |
| Pre-boost cliff [65-75] | 3.451 | 3.748 | +8.6% (worse) |
| Put cliff [25-32] | 5.036 | 6.146 | +22.1% (worse) |
| Large swings (>10pt) | 65,171 | 76,917 | +18% more incidents |
| Huge swings (>20pt) | 8,190 | 8,820 | +7.7% more incidents |

**The optimizer's "WR-best" solution roughly DOUBLES Friday's whiplash** because aggressive Friday dampening (×0.31) creates a big mid-week-amped → Friday-dampened jump. Net: more whiplash, not less.

## Phase 3 — Monday-only sweep

Hypothesis: Monday is the *only* day with structurally noisy wadj. Test [Mon ∈ {0.0, 0.3, 0.5, 0.7, 0.85, 1.0}, others = 1.0].

| Mon coeff | 95+ N | 95+ WR15 | 80+ N | 80+ WR15 | <25 N | <25 WR15 | All |Δ| | Mon |Δ| | Fri |Δ| |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 87 | 86.2% | 4,098 | 71.29% | 30,041 | 56.39% | 4.637 | 7.082 | 3.171 |
| 0.30 | 87 | 86.2% | 4,227 | 71.66% | 27,575 | 57.69% | 4.190 | 6.247 | 3.171 |
| 0.50 | 87 | 86.2% | 4,389 | 71.82% | 26,928 | 58.52% | 3.958 | 5.957 | 3.171 |
| 0.70 | 99 | 85.9% | 4,626 | 72.19% | 27,405 | 59.26% | 3.792 | 5.902 | 3.171 |
| **0.85** | **117** | **80.3%** | **4,805** | **72.23%** | **28,707** | **59.33%** | **3.726** | **6.007** | **3.171** |
| 1.00 (base) | 120 | 80.8% | 5,112 | 72.48% | 30,371 | 59.27% | 3.732 | 6.234 | 3.171 |

### Reading the Monday-only sweep

- **Aggressive Mon dampening (≤ 0.5)** boosts 95+ WR15 to ~86% but at cost of -28% N (120 → 87) — fails H3, and overall stability gets WORSE (3.96 vs 3.73 baseline) because Mon's score now diverges further from Fri's.
- **Mon = 0.85** is the only variant where overall stability marginally improves (3.726 vs 3.732). Mon-specific |Δ| drops from 6.234 to 6.007 (3.6% improvement). 95+ WR15 drops 0.5pp; other tiers unchanged within noise.
- **Multi-window check on Mon = 0.5** (which Phase 2 essentially included): 1y +0.91pp on 90+, but 3y/5y both -2.0+. Fails H5.

### Why Monday-only also fails

Monday's whiplash isn't because Monday's wadj is wrong — **it's because Monday's WeeklyScore is computed from a 1-day partial weekly bar, which produces RSI/MACD values wildly different from Friday's complete-week WeeklyScore.** Dampening wadj on Monday makes Monday's overall score departure from Friday's *larger*, not smaller (the wadj contribution carries weekly information that's actually being LOST on Monday already by the partial-bar issue; cutting wadj further removes the only carrying signal).

This is a structural problem in the WeeklyScore computation, not a wadj-weighting problem.

## Why the Bayesian optimizer's "best" still failed

Three reasons the Bayesian optimum doesn't generalize:

1. **The loss function rewarded WR15 improvement weighted by bucket importance, with N-penalty only on primary tiers above a 15% drop.** The optimizer found a corner where dropping 95+ N by 30% (just under the gate's tolerance for primary tiers) yields a quality-improvement that looks great on 5y. But that 30% N drop is filtering out signals — the per-trade gain is partly an artifact of selecting only the "purest" wadj-strong-confirmed signals.

2. **5y aggregates over 4 distinct macro regimes** (2021 bull, 2022 bear, 2023 narrow-bull, 2024 strong-bull, 2025 chop). The 5y-best parameters happen to be optimal in some regime mix but not others. Per H5 sign-consistency: 80+, 85+, 70+ all fail across windows.

3. **The lever is too small for what we're trying to fix.** wadj is one of ~6 components feeding overall. Day-of-week shifts can move the score by ~0.5-3pp on average. The whiplash incidents (>20pt swings) are dominated by larger structural shifts (WeeklyScore lookup flipping, vol amp flipping, regime mult shifts) — wadj dampening doesn't move the needle on those.

## Recommendations

### Don't ship the day-of-week wadj dampener

It's a NULL RESULT for the whiplash problem AND a noisy-overfit for per-trade WR. The Mon=0.85 mild variant gives marginal stability improvement but loses 0.5pp WR15 on 95+ — not a clean trade.

### Do address the WeeklyScore root cause

The actual fix is structural at WeeklyScore computation:

**H_weekly_proximity (the original idea, not yet tested):**

```python
def get_weekly_for_scoring(symbol, signal_date):
    days_into_week = signal_date.weekday() + 1  # Mon=1, Fri=5
    cur_wk_start = signal_date - timedelta(days=signal_date.weekday())
    prev_wk_start = cur_wk_start - timedelta(days=7)
    cur = WeeklyScore.get_or_none(symbol=symbol, date=cur_wk_start)
    prev = WeeklyScore.get_or_none(symbol=symbol, date=prev_wk_start)

    # Blend: Mon-Tue trust last week fully; Wed onward ramp
    if days_into_week <= 2:
        return prev or cur
    cur_w  = (days_into_week - 2) / 3.0   # Wed=0.33, Thu=0.67, Fri=1.00
    prev_w = 1.0 - cur_w
    return BlendedWeeklyScore(
        composite = cur.composite * cur_w + prev.composite * prev_w,
        rsi       = cur.rsi       * cur_w + prev.rsi       * prev_w,
        macd      = cur.macd      * cur_w + prev.macd      * prev_w,
        trend     = cur.trend     * cur_w + prev.trend     * prev_w,
    )
```

Why this might work where wadj dampening didn't:
- It addresses the WEEKLYSCORE values themselves (the source of Monday whiplash), not the wadj formula on top
- Mon-Tue use last-week's COMPLETE WeeklyScore — eliminates partial-bar instability entirely on the worst-whiplash days
- Wed onward proximity-blends — smooth transition rather than cliff
- The day-of-week dependence is explicit and structural, not a parameter to tune

To test it requires a different runner that re-derives `wadj` from blended composites at the score level. Cannot be evaluated by the linear-Jacobian shortcut used in this experiment because changing the COMPOSITE changes BOTH `w_bias` AND `w_mom` (the latter via `delta = composite - prev_composite`). Need a full pipeline run.

**Estimated cost:** ~2 days to build the runner that handles WeeklyScore replacement; full per-trade gate sweep over freeze_day ∈ {1, 2, 3} and ramp shape ∈ {linear, cosine} is a 6-variant × 5y → quick.

### Don't conflate the COHR-class with the Monday-class

The original COHR=84 ghost was an INTRA-DAY swing (19:26 → 20:23, both writes on Wednesday). That comes from the lookup path inconsistency between `weekly_score` property (returns last-week's Monday) and `output_hash` (returns current week's Monday). It's NOT a day-of-week effect — both writes happened on the same day.

To fix the COHR-class:
- Standardize the lookup: replace both call sites with a single `get_weekly_for_scoring()` function (Issue A in the prior conversation)
- Stabilize WeeklyScore values: don't recompute past-week WeeklyScore using partial current-week bar (Issue B)

Both are orthogonal to the wadj dampener and orthogonal to weekly proximity blending.

### Priority for ship

Order from highest leverage to lowest:

| # | What | Estimated impact | Cost |
|---|---|---|---|
| 1 | Fix `weekly_score` / `output_hash` path inconsistency | Eliminates intra-day flips on the COHR class | Low (1 day, code change only) |
| 2 | Stabilize WeeklyIndicator past-week values (don't include partial current bar in RSI(14) lookback) | Eliminates the COHR-class root cause | Medium (3 days, schema-aware) |
| 3 | Test the Mon-Tue freeze + Wed-Fri proximity blend (H_weekly_proximity above) | Reduces Monday-class whiplash | Medium (2 days runner + 1 day sweep) |
| 4 | EMA-smooth wadj at daily layer (Priority #7) | Backstop for residual day-over-day noise | Low (1 day) |
| 5 | ~~Day-of-week wadj dampener~~ — **NULL RESULT, do not retry** |  |  |

## Artifacts

| File | Content |
|---|---|
| `01_runner.py` | Polars-based fast runner with day-of-week wadj dampener |
| `02_bayesian_opt.py` | 5-D gp_minimize over [Mon..Fri] coefficients |
| `03_multiwindow_validate.py` | 1y/3y/5y validation + H5 sign-consistency check |
| `04_stability_breakdown.py` | Day-by-day |Δoverall| breakdown + cliff cohort + large-swing counts |
| `05_monday_only.py` | Targeted Mon-only sweep + multi-window for Mon=0.5 |
| `opt_result.json` | Bayesian opt full result (best params, baseline stats, 80 evals) |
| `multiwindow_validate.json` | 1y/3y/5y stats per candidate |
| `stability_breakdown.json` | Per-day, per-cliff stability stats |
| `monday_only.json` | Mon-only sweep stats |

## New hypotheses worth testing

### H1 — Mon-Tue freeze + Wed-Fri proximity blend (the actual fix the user proposed)

Don't dampen wadj. Replace the WeeklyScore lookup itself with a proximity-blended view that uses last-week's complete WeeklyScore on Mon/Tue (when current-week is too partial to be informative) and ramps in current-week on Wed/Thu/Fri. Already specced in the prior conversation; not yet implemented because it requires a different runner architecture (full pipeline replay through `calculate_weekly_adjustment`).

### H2 — WeeklyIndicator stability fix

Make WeeklyIndicator.rsi/macd for date=W use only weekly bars with date ≤ W. Currently the partial current-week bar gets included in the 14-bar RSI lookback for ALL recent past weeks, making them all unstable as new daily data arrives. Fix at the WeeklyIndicator computation layer, not at scoring.

### H3 — Path-consistent WeeklyScore lookup

Single source of truth function that both `weekly_score` property and `output_hash` use. Eliminates the lookup-path flip that caused the original COHR ghost. Pure code-hygiene change, no scoring math.

### H4 — Score-history audit log + measure whiplash incidence directly

Currently we infer whiplash from cross-day score deltas. Adding a `ScoreHistory` table (one row per Score write, not just latest) lets us measure intra-day swings directly. Would have shown the COHR 19:26 (=59) → 20:23 (=84) flip explicitly. Prerequisite for any future "stability calibration" work.

### H5 — EMA-smooth wadj at daily layer (Priority #7 follow-up)

`wadj_today = α × wadj_now + (1-α) × wadj_prev_day`. Backstops H1-H3 by smoothing whatever residual swing remains. Tunable α via Bayesian opt — different formulation from this experiment (which scaled wadj by day-of-week; this would scale by yesterday-vs-today).

The α-EMA differs from the day-of-week dampener tested here because:
- It explicitly mixes today's wadj with yesterday's stored wadj (not modifying based on what day it is)
- Yesterday's wadj is already a stable known value
- Smoothing is uniform across days, not day-specific
- Hypothesis: this would actually reduce whiplash where the day-of-week dampener didn't

Worth Bayesian-optimizing α ∈ [0, 1] separately, with stability metric as the *primary* loss function (not WR15) since the goal is whiplash reduction.

---

**Conclusion:** The day-of-week wadj dampener is a wrong-shape solution to the right problem. Phase 1 of any next attempt should be H3 (path-consistency) + H2 (WeeklyIndicator stability) — these are the structural fixes that make the underlying WeeklyScore values sensible. After those, H1 (proximity blend) and H5 (EMA-smooth) become smaller-magnitude calibration knobs on a stable foundation.
