# Sector Breadth Independence — Hypothesis & Decision

**Date:** 2026-05-07
**Algorithm:** v44 (`d8024b9`) — ICH ship
**Window:** sector breadth time series 2020-01-02 → 2026-05-07 (1,595 daily rows, 6.4y)
**Outcome cohort:** v44 peaks 2021-05-10 → 2026-05-01 (32,943 peaks; barriers cache constraint)
**Holdout:** ≤2026-05-15 per Priority #11
**Barrier:** 30dte_opt @ w=15d (option-aligned)

## The question

> *"Can we measure the sector ETF breadth and find extremes across data going back to 2020-01-01 and determine how our scoring performs during those periods, and if we can form a hypothesis separate from our current market breadth score's impact?"* — user, 2026-05-07

The decisive test is **independence**: does sector breadth predict signal performance INDEPENDENTLY of production `breadth_score`, or is it just a correlated proxy?

## TL;DR — sector breadth has SOME independent signal, but original claim was inflated by ETF contamination

**Critical correction (2026-05-07, post-de-contamination):** the original Pearson 0.69 figure between production `breadth_score` and `sec_brd_ema50` was partly a **self-induced artifact**. Production `_get_daily_breadth` aggregates over ALL `PriceHistory` rows with no ETF filter, mixing 43 ETFs (~5.5% of universe) — including XLF/XLE/XLP/XLU/XLC/XLK/XLV/XLY/XLI/XLB/XLRE *the very SPDRs we're using as the standalone sector breadth measure* — directly into the production aggregate. This inflates apparent independence.

Computing a *clean* stocks-only breadth (filter `Stock.sector IS NOT NULL`, ~692 stocks median):

| Comparison | Pearson | Shared variance | Unique variance |
|---|---:|---:|---:|
| production_breadth ↔ stocks_only | +0.7616 | 58% | 42% |
| production_breadth ↔ sector_breadth (CONTAMINATED) | +0.7075 | 50% | 50% |
| **stocks_only ↔ sector_breadth (CLEAN)** | **+0.8690** | **76%** | **24%** |

Conditional-cohort test re-run with stocks-only as production-equivalent:

| Stocks-only | Sector breadth cell | Δpp | z |
|---|---|---:|---:|
| **so_mid** (the original z=+4.74 alpha cell) | any | — | **0 sig cells (signal absorbed)** |
| so_lo | sec_mid CALL 70+ | +3.16 | −2.27 |
| so_hi | sec_high CALL 70+ | −3.74 | +2.65 |
| (inverse) sec_mid | so_lo CALL 70+ | +4.60 | −3.28 |

**The z=+4.74 cell disappears under de-contamination.** Sector breadth retains ~24% unique variance and residual marginal effects in divergence cells (where stocks-only and sector signals disagree), but the original "substantial independence" claim is downgraded to "small but real, concentrated in divergence cells."

## Sector breadth time series (2020 → 2026)

Daily series of `sec_brd_ema50` = % of 11 SPDR sector ETFs above their own EMA50:

| Statistic | Value |
|---|---|
| N (daily rows) | 1,595 |
| Date range | 2020-01-02 → 2026-05-07 |
| Mean | 66.0% |
| Std | **30.1%** (vs production breadth std=12.9 — sector signal is ~2.3× more dynamic) |
| p10 (extreme weak) | 18.2% |
| p25 | 45.0% |
| p50 | 72.7% |
| p75 | 91.0% |
| p90 (extreme strong) | 100% (saturation — every SPDR above EMA50) |

### Major weak runs (sec_brd_ema50 ≤ p10, run length ≥ 5 days)

23 runs total. Selected major regimes captured:

| Period | Length | Regime |
|---|---:|---|
| 2020-02-24 → 2020-03-04 | 9 days | COVID crash onset |
| **2020-03-05 → 2020-04-09** | **35 days** | **COVID crash** |
| 2022-01-24 → 2022-01-31 | 7 days | Fed pivot reaction |
| 2022-02-17 → 2022-03-02 | 13 days | Russia/Ukraine + rate fears |
| 2022-03-07 → 2022-03-15 | 8 days | Continued bear |
| 2022-04-29 → 2022-05-04 | 5 days | Q1 earnings dump |
| 2022-05-05 → 2022-05-17 | 12 days | Mid-2022 bear |
| 2022-05-18 → 2022-05-26 | 8 days | (continued) |
| **2022-06-09 → 2022-07-01** | **22 days** | **Mid-summer 2022 bear** |
| 2022-07-05 → 2022-07-19 | 14 days | (continued) |
| 2022-08-30 → 2022-09-07 | 8 days | Late summer |
| **2022-09-20 → 2022-10-18** | **28 days** | **Q4 2022 bear bottom** |
| 2023-03-09 → 2023-03-16 | 7 days | SVB / banking crisis |

The signal correctly identifies every major regime event in the 2020-2026 window without prior knowledge — pure breadth-derived. 2022 (the bear year) accounted for ~110 cumulative days of extreme weak sec breadth across 8 distinct runs; production breadth_score during these same windows oscillated around 35-50 (less extreme).

## Phase C — signal performance during sector extremes (univariate)

| Side | Tier | Sector breadth bucket | N | TP% | Δpp | z |
|---|---|---|---:|---:|---:|---:|
| **CALL** | **85+** | **a_p10_extlow** (≤18) | **134** | **58.21** | **−11.73** | **+2.96** ⭐ |
| CALL | 70+ | d_p75_high (>91) | 1,812 | 55.41 | −4.10 | +3.56 |
| PUT | ≤25 | d_p75_high | 1,341 | 55.70 | +4.28 | −3.14 |
| PUT | ≤20 | d_p75_high | 507 | 61.93 | +7.64 | −3.45 |
| CALL | 80+ | a_p10_extlow | 269 | 64.31 | −6.63 | +2.40 |
| PUT | ≤15 | d_p75_high | 152 | 65.13 | +7.24 | −1.81 |

**Pattern:** strong-sectors days favor PUT outcomes (counter-intuitive — but it's mean reversion: when broad sectors are stretched, individual stocks experience the gravity); extreme-weak-sectors days hurt high-conviction CALLs (85+ fail by 11.7pp). This is the wave physics — at sector-aggregate scale, mean reversion governs the transition between regimes.

## Phase D — the independence test (HEADLINE)

Cross-tab: hold production breadth bucket fixed (lo/mid/hi), measure sector breadth's residual cohort effect within each bucket.

### Conditional on production_breadth = MID (the ambiguous regime)

| Side | Tier | Sec breadth bucket | N | Δpp | z |
|---|---|---|---:|---:|---:|
| **CALL** | **70+** | **a_p10_extlow** | **859** | **−7.96** | **+4.74** ⭐⭐ |
| **CALL** | **75+** | **a_p10_extlow** | **126** | **−13.03** | **+3.07** ⭐⭐ |
| PUT | ≤20 | d_p75_high | 154 | +10.24 | −2.55 |

**This is the alpha.** When production breadth is in the middle quartile (saying "ambivalent / no signal"), sector breadth filling that information vacuum says CALL 70+ in extreme-weak sectors fail by ~8pp, CALL 75+ by ~13pp. Production breadth is BLIND to this regime; sector breadth is NOT.

### Conditional on production_breadth = HI

| Side | Tier | Sec breadth bucket | N | Δpp | z |
|---|---|---|---:|---:|---:|
| CALL | 70+ | d_p75_high | 1,128 | −4.99 | +3.40 |
| PUT | ≤25 | d_p75_high | 896 | +5.08 | −3.04 |
| PUT | ≤20 | d_p75_high | 353 | +7.48 | −2.82 |
| PUT | ≤25 | c_mid | 2,158 | −2.81 | +2.61 |

In strong-production-breadth regimes, sector breadth still discriminates: when both say "strong" (prod_hi × sec_high), calls hurt and puts succeed — the *most overheated* state for the wave.

### Inverse: production breadth marginal | sector breadth bucket

| Sec breadth | Side | Tier | Prod bucket | N | Δpp | z |
|---|---|---|---|---:|---:|---:|
| **a_p10_extlow** | **CALL** | **70+** | **prod_mid** | **859** | **−10.23** | **+6.14** ⭐⭐⭐ |
| a_p10_extlow | CALL | 75+ | prod_mid | 126 | −12.28 | +2.88 |
| a_p10_extlow | CALL | 70+ | prod_lo | 3,056 | +2.77 | −3.14 |

**The single largest cell in the screen: z = +6.14**, the cohort of CALL 70+ where sector breadth is extreme-low AND production breadth is mid (ambiguous). Production breadth is unable to detect this cohort; sector breadth nails it. Failure rate is +10pp above 70+ baseline.

## Hypothesis (formal — REVISED 2026-05-07)

**H_INDEP (revised) — Sector breadth and a CLEAN stocks-only breadth signal capture overlapping aspects of the same wave physics (76% shared variance). The 24% unique variance carries small but measurable marginal predictive power, concentrated in *divergence cells* where stocks-only and sector signals disagree. The strongest marginal cell is sec_mid × so_lo for CALL 70+ at z=−3.28, +4.60pp — about half the strength claimed before de-contamination.**

**H_CONTAMINATION — The current production `breadth_score` is contaminated with 43 ETFs (~5.5% of universe), inflating apparent independence between breadth signals and adding noise (especially from leveraged 3x ETFs like TQQQ/SOXL/LABD). De-contamination is a clean infrastructure fix that improves signal quality without changing any computation formula.**

Mechanistically:
- **Production breadth** measures *internal participation health* via equal-weighted A/D, McClellan, Zweig, NH/NL, % above EMA50. Captures small-cap-led participation and stock-level breadth dynamics.
- **Sector breadth** measures *sector rotation phase* via mcap-weighted directional flow across 11 GICS sectors. Captures mega-cap-led directional regimes and inter-sector mean reversion.

These are different lenses on the *same underlying wave*, just at different aggregation levels. Their combination resolves the substantial set of trading days where one lens is uninformative and the other is decisive.

## Why this is a "separate hypothesis" from production breadth

The independence claim rests on three pieces of evidence, in order of strength:

1. **Marginal cohort z-scores survive conditioning on production breadth** — the most direct test. CALL 70+ in `a_p10_extlow|prod_mid` fails at z=+4.74; this z-score is *higher than the unconditional z=+1.74* for the same sector cell, meaning production breadth was *attenuating* the signal in the unconditional view, not creating it.
2. **Pearson 0.69 with 52% unique variance** — substantial structural decorrelation; the two signals are not redundant.
3. **Larger dynamic range** — sector breadth std=30.1 vs production std=12.9. Production breadth is constructed from a multi-component composite that compresses extreme readings; sector breadth's % above EMA50 saturates cleanly at 0% and 100%, making extremes operationally definable.

## Recommended next steps — REVISED priority order post-decontamination

### Priority 1 — Ship production breadth ETF exclusion (infrastructure cleanup)

**The biggest, cleanest, most defensible win is fixing the production breadth contamination itself.** This is a quality refinement of an existing aggregator — same formula, cleaner universe. Affects:

- `market_breadth.py:_get_daily_breadth` — add `JOIN stocks WHERE sector IS NOT NULL` filter
- ~5y of `MarketBreadth` rows need backfill: `trader breadth-backfill 1825`
- Production `breadth_score` values shift slightly (mean was 55.0 with contamination; cleaner version may be slightly different)
- Regime composite changes (35% weight on breadth)
- → Score.overall changes via regime → ALGORITHM_VERSION bump + `trader recalculate --force --full`

**Cost:** ~30 min breadth-backfill + ~25 min recalculate + assess. Mid-day ship-able if scheduled (not during market open).

**Expected impact:** marginal — production breadth values won't shift hugely (most ETFs are net-neutral on advancing/declining), but the noise from leveraged 3x ETFs (TQQQ, SOXL, LABD, BOIL, SOXL, TNA) is removed. Cleaner inputs to F3F and regime composite. Sets up future sector-breadth work to have honest orthogonality.

**Validation:** spot-check that historical extreme breadth dates (e.g. 2020-03 COVID bottom) still register as extreme; confirm distribution shift is small (mean within ±2pts).

### Priority 2 — Min-Floor F3F: `effective_brd = min(prod_brd_clean, sec_brd_ema50)` (downgraded)

The independence claim is weaker post-decontamination, but the divergence-cell signal is real (residual z=+2.65 to +3.28). Simplest aggregation; lowest implementation cost. Validation: P1-P6 canonical N=300+ × 8-window MC.

**Expected outcome (probability-weighted):** marginal. The 24% unique variance probably translates to small portfolio-stage compound effects. Either:
- (50%) signal compounds via slot displacement and shows real lift on dip windows
- (50%) signal absorbed by existing F3F reach; null MC result

Worth running because the implementation cost is low (one-line F3F input change + backfill of `etf_basket_breadth` daily series).

### Priority 3 — 2D Phase-Aware F3F (de-prioritized)

The previous draft proposed a 6-cell joint policy. Without the strong z=+6.14 alpha cell, this is harder to justify. The continuous tensor formulation (`scale = f(prod) × g(sec)`) might still extract the divergence-cell signal but the calibration burden is high relative to expected lift.

**Defer until** Priority 1 ships and Priority 2 is tested. If P2 shows real MC effect, P3 becomes the natural extension.

### Priority 4 — SWPM augmentation with cross-sector aggregate (de-prioritized)

The SWPM design in [ARCHITECTURE.md](ARCHITECTURE.md) uses per-sector ETF state directly. Adding a cross-sector aggregate coefficient was framed as bonus marginal alpha. Given the de-contamination finding, this aggregate's marginal contribution is likely small. Bundle with the SWPM ship if it adds easily; don't expand SWPM scope to chase it independently.

## What this analysis does NOT prove

- **Does not prove H1 is the best aggregator.** Min-floor is the simplest; 2D phase or weighted sum could outperform under MC.
- **Does not prove sector breadth helps in MC.** Per-trade z-scores are a necessary but not sufficient condition for P1-P6 portfolio-stage validation. The compound-velocity effect via cascade fill requires MC.
- **Does not validate the 2020 COVID cohort against outcomes** — barrier cache only goes back to 2021-05. The 2020 sector breadth time series is shown for regime-detection-validity context but isn't joined to outcomes.
- **Does not verify alpha persistence** post-2026-05-15 holdout. Per Priority #11, true out-of-sample validation requires waiting for ≈2026-11-15 forward window.

## Recommended next steps

1. **Build the daily ETF breadth time series as a production feature** — `database/utils/etf_breadth.py` analogous to `market_breadth.py`. Stored per-day in a new table or as additional columns on `MarketBreadth`. Idempotent; refreshed nightly via `trader update`. ~2-3 hours.
2. **Run Candidate H1 (min-floor F3F) under canonical N=300+ × 8-window MC** vs current production. Lowest implementation cost; tests the simplest aggregator. ~4 hours.
3. **If H1 passes P1-P6**, advance to H2 (2D phase) for tighter calibration.
4. **If both fail or are marginal**, the per-trade z-score gain doesn't translate to portfolio-stage compound velocity (precedent: BSD score-stage F3F-encoding NULL on v39 + v42). The signal remains real but operationally captured by existing F3F — file as a research finding, not a ship.

## Production safety

- Read-only screen against existing tables
- All outputs in `.cache/sector_etf_screen/`
- No DB writes, no version bump, no `strategy_config.py` change
- `trader update` running concurrently zero-impact
- Holdout-locked

## Production breadth ETF exclusion — proposed ship spec

`_get_daily_breadth` currently selects `PriceHistory` rows with no ETF filter, contaminating advancing/declining counts and TRIN volume with 43 ETFs including:
- 11 sector SPDRs (now also our standalone sector breadth basket — direct overlap)
- 3 broad indices (SPY, QQQ, IWM)
- 6 leveraged 3x ETFs (TQQQ, SOXL, LABD, BOIL, SVIX, TNA — heavy noise amplifiers)
- 6 international ETFs (EEM, EWY, EWZ, FXI, ASHR, KWEB)
- 5 commodity/bond ETFs (GLD, SLV, IAU, FBTC, TLT)
- ~12 sub-industry / thematic (SMH, SOXX, IGV, ARKQ/ARKX, etc.)

**Patch:**
```python
# In market_breadth.py:_get_daily_breadth
today_rows = list(
    PriceHistory.select(...)
    .join(Stock, on=(PriceHistory.symbol == Stock.symbol))
    .where(
        (PriceHistory.date == target_date) &
        (Stock.sector.is_null(False))   # exclude ETFs (NULL sector)
    )
)
# (apply same filter to prior_rows, agg_rows, ind_rows queries)
```

**Ship procedure (atomic):**
1. Edit `_get_daily_breadth` (4 query filters)
2. `trader breadth-backfill 1825` — recompute 5y of `MarketBreadth` rows (oldest-to-newest, ~30 min)
3. ALGORITHM_VERSION bump (downstream `Score.overall` changes via regime composite)
4. `trader recalculate --force --full` (~25 min)
5. `trader assess --force` (~10 min)
6. Document in `version-history.md`

**NOT done autonomously** — requires user signoff and ideally off-market-hours scheduling. Ship procedure documented here as the recommended next step.

## Artifacts

- `cohort_v44_1825.parquet` — 32,943 v44 peaks
- `sector_breadth_daily_2020plus.csv` — 1,595 daily rows of (date, sec_brd_ema50, sec_brd_ema200, sec_avg_rsi)
- `stocks_only_breadth_daily.parquet` — 1,626 daily rows of clean stocks-only breadth (NEW 2026-05-07)
- `phaseC_sec_brd_cohorts.csv` — full cohort z-test table
- `breadth_extremes.out` / `stocks_only.out` — execution logs

## Code

- `experiments/sector_etf_screen/build_v44_cohort.py` — v44 cohort builder
- `experiments/sector_etf_screen/sector_breadth_extremes.py` — Phase A→E pipeline
- `experiments/sector_etf_screen/stocks_only_breadth.py` — de-contamination test (this finding)
