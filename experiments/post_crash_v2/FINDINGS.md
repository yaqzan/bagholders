# Post-Crash Put Cohort — Profiling Findings

**Investigation date:** 2026-05-05 · v36 (`d5ef1f5`) active · 5y lookback (16,842 put peaks)

## Question

Are puts that fire after a stock has *recently* fallen sharply (post-crash signals) systematically less predictive than the rest of the put population, and if so can we surgically remove them at the score stage?

## Headline finding

**Yes — the cohort is real, large, and signal is sign-consistent across all 6 years and both barrier sets.** The cleanest discriminator is `ret_10d` (close return over last 10 trading bars at signal date), NOT `pct_from_252h` or `pct_from_ema50` (the prior null-result feature).

### Best cohort: `overall ≤ 25 AND ret_10d < -15%`

| barrier set | tier | cohort N | cohort WR15 | rest-of-tier WR15 | lift | z |
|---|---|---:|---:|---:|---:|---:|
| 30dte_generic (K=1.0σ) | ≤25 | 2,767 | 69.3% | 75.0% | **-5.65pp** | **-6.20 \*\*\*** |
| 30dte_opt (K=1.274σ) | ≤25 | 2,767 | 43.9% | 51.0% | **-7.15pp** | **-6.88 \*\*\*** |
| 30dte_generic | ≤15 | 785 | 69.9% | 77.9% | -8.03pp | -4.76 \*\*\* |
| 30dte_opt | ≤15 | 785 | 45.4% | 54.9% | -9.57pp | -4.84 \*\*\* |

The signal **strengthens** at the option-aligned barrier (z=-6.88 > z=-6.20). The cohort is still net-positive at 43.9% (above put BE 36.4%), but the alpha-margin is 7-9pp thinner than the rest of the put population. In the cascade these signals occupy slots that displace higher-quality puts.

## Feature ranking

Tested 5 candidate features × put score tier (5y, generic barrier, baseline 74.0% at ≤25):

| feature | best cohort | N | lift | z | clean? |
|---|---|---:|---:|---:|:---:|
| **ret_10d** | < -15% | 2,767 | **-5.65pp** | **-6.20 \*\*\*** | ✓ |
| ret_5d | < -15% | 1,109 | -5.02pp | -3.69 \*\*\* | ✓ |
| sigma_expansion | > 2.5 | 751 | -4.46pp | -2.73 \*\* | ✓ but smaller N |
| ret_5d AND sigma_expansion | combined | 729 | -5.55pp | -3.34 \*\*\* | weakens at opt barrier (z -1.50) |
| pct_from_252h | < -50% | 5,656 | -0.78pp | -1.10 | **null** — static drawdown is not predictive |
| gap_down_5d_count | ≥ 2 | 1,692 | -0.90pp | -0.80 | **null** |

**Key insight:** the prior null result tested `ext_pct` (static distance vs slow-moving EMA50). That is mechanically different from the user's intuition — a stock can be near EMA50 immediately after a sharp 1-week drop from much higher. The right feature is **velocity over a 10-bar window**.

## Regime discriminator — NOT needed

The 5y null-result analysis predicted that the cohort would behave differently across regimes (i.e., breakdowns continue in 2021-22 bear, exhaust in 2024-25). Tested by year and by composite/VIX/breadth:

**`ret_10d < -15%` cohort lift by year (≤25 tier, generic):**

| year | N cohort | lift | z |
|---|---:|---:|---:|
| 2021 | 89 | -0.94 | -0.19 (small N — neutral) |
| 2022 | 746 | **-6.80** | **-4.08 \*\*\*** |
| 2023 | 358 | **-6.62** | **-2.78 \*\*** |
| 2024 | 387 | -4.76 | -1.91 \* |
| 2025 | 315 | -2.14 | -0.79 |
| 2026 | 107 | -5.17 | -1.21 |

**Sign-consistent every year** (all 6 windows negative). Only 2025 is statistically near-neutral; in 2022 (the supposedly "bear keeps going" year) the cohort actually under-performs MOST.

**Cohort lift by composite regime (≤25, generic):**

| regime | N | lift | z |
|---|---:|---:|---:|
| STRESS (<30) | 75 | -5.08 | -0.95 |
| CAUTN (30-50) | 837 | -5.43 | -3.40 \*\*\* |
| NEUT/H (50-70) | 563 | -0.70 | -0.36 |
| HEALTHY (>70) | 527 | **-10.54** | **-4.82 \*\*\*** |

There is some regime structure (it's worst in HEALTHY and CAUTN, neutral in NEUT/H) but **the sign is negative everywhere** — no regime where the post-crash cohort *outperforms*. Unlike the prior `ext_pct` failure which had a 2024-25-flips-vs-2022 cliff, this signal is robustly negative across the whole 5y. **An unconditional (regime-blind) dampener is justified — no discriminator gating required.**

## Why this differs from the prior null result

The prior `experiments/post_crash_floor/` work tested `pct_from_ema50` and `pct_from_252h`. Neither captured velocity:
- `pct_from_252h < -50%` cohort: lift -0.78pp z=-1.10 — confirmed null on this dataset too.
- `ext_pct` (vs EMA50) heavily correlated with multi-quarter trend, not recent crash.

The user's framing — "stocks that recently fell off a cliff" — is specifically about *velocity*, and the data agrees. `ret_10d` directly encodes velocity over a 2-week window, captures both gap-downs and sustained drops, and is regime-invariant.

## Mechanism recommendation

Mirror the v27 WCF / v32 CWCF discrete-cutoff dampener pattern:

```python
# Post-crash put dampener — pull score up toward 30 (out of put-bucket reach)
PCD_GATE          = 25      # only fires on overall <= 25
PCD_RET_RAMP_LO   = -0.15   # weakness=0 at ret_10d >= -15%
PCD_RET_RAMP_HI   = -0.25   # weakness=1 at ret_10d <= -25%
PCD_K             = 0.65    # lift coefficient
PCD_TARGET        = 30      # lift toward score=30 (just above any put bucket)

if overall <= PCD_GATE and ret_10d is not None:
    if ret_10d <= PCD_RET_RAMP_LO:
        weakness = clip((PCD_RET_RAMP_LO - ret_10d) / (PCD_RET_RAMP_LO - PCD_RET_RAMP_HI), 0, 1)
        overall += PCD_K * weakness * (PCD_TARGET - overall)
```

**Calibration starting points (need fast_variant_runner.py sweep):**
- K ∈ {0.50, 0.65, 0.80, 0.95}  — controls how strongly to lift
- ramp endpoints — discrete (binary at -15%) vs ramp (-15% → -25%)
- target ∈ {28, 30, 32}  — 30 just clears the ≤25 gate so the cascade can't pick them
- gate ∈ {25, 20, 15}  — should we only dampen ≤15 (where signal is strongest at -9.57pp) or all ≤25?

**Per-trade ship gate:** H1-H5 from assessment-backtest.md. Specifically:
- H4 says "puts neutral or better" — this is the test. Suppressing the worst-quartile of puts SHOULD lift average put TP% per tier.
- H1 (call tiers ≥+0.5pp): irrelevant here, this is a put-side change.
- H3 N stability: dampener will move ~2,767 of 16,971 (~16%) of put peaks out of ≤25; need to confirm no tier drops below 50 N at 5y. The ≤15 tier (4,136 peaks) loses up to ~785 (~19%) — check it stays > 1,500 at 5y per ship gate.

**Don't ship** until:
1. fast_variant_runner.py sweep on K × target × gate confirms cohort lift translates to per-tier WR15/TP% lift after dampening
2. Optional N=100 smoke MC at 22-now if signal density on ≤15 shifts >30%

## Files

- `experiments/post_crash_v2/build_features.py` — feature parquet builder
- `experiments/post_crash_v2/profile_cohorts.py` — cohort × axis × tier table
- `experiments/post_crash_v2/regime_split.py` — VIX/breadth/composite/year splits
- `experiments/post_crash_v2/option_barrier_check.py` — generic vs option-barrier validation
- `.cache/post_crash_v2/features_v36_1825.parquet` — feature parquet (3.4 MB, 16,971 rows × 41 cols)
- `experiments/post_crash_v2/{profile,regime,opt_barrier}.out` — captured outputs
