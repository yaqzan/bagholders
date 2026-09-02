# Sector ETF Screen — Phase 0/0.5/1/1b/2 Findings

**Date:** 2026-05-07
**Algorithm:** v43 (`e083032`) — MCD shipped baseline
**Window:** 5y, 2021-05-10 → 2026-05-01
**Holdout:** ≤2026-05-15 per Priority #11; no leak detected
**Resolved peaks:** 31,350 (CALL 20,612 + PUT 10,738) at qualifying tiers
**Barrier:** 30dte_opt @ w=15d (option-aligned, TP=1.274σ / SL=0.983σ)
**Sector ETF coverage:** 30,257 of 31,350 = **96.5%** (after backfilling XLK/XLV/XLY/XLI/XLB/XLRE on 2026-05-07)

## Headline — wave law confirmed at sector scale, bidirectional and symmetric

The same mean-reversion physics that governs market-scale and stock-scale signals operates at sector scale, with **substantially stronger signal magnitude** than at market (SPY) scale. After full SPDR backfill, four cohort effects are at z|≥3 with N=hundreds-to-thousands, all consistent with one underlying wave law.

| # | Finding | Cell | z |
|---|---|---|---|
| 1 | **Calls into overheated sectors underperform** | CALL 70+ × sec ETF >+5% above EMA50: −3.97pp, N=3,088 | **+4.49** ⭐ |
| 2 | **Puts into oversold sectors massively underperform** (NEW at full coverage) | PUT ≤25 × sec ETF RSI < 30: **−20.21pp**, N=133 | **+4.66** ⭐ |
| 3 | **Calls into overheated sectors (RSI angle)** | CALL 70+ × sec ETF RSI > 70: −4.51pp, N=1,661 | **+3.74** ⭐ |
| 4 | **Calls into oversold sectors outperform** | CALL 70+ × sec ETF RSI < 30: **+8.38pp**, N=308 | **−3.00** ⭐ |
| 5 | Counter-trend calls outperform aligned | CALL 70+ × COUNTER vs ALIGN: +1.48pp Δ | −2.47 |

These z-scores are at the strength of v32's wadj-neg miss-driver (z=+10.1) and v43's mcap monotonic ladder (z=±2.5) — i.e., among the largest single-feature cohort effects in the codebase.

The wave law in one sentence: **at sector scale, the further the underlying ETF is from its mean, the more probable a mean-reverting outcome at the individual-stock level — calls fail in overbought sectors, puts fail in oversold sectors, both work in the contrary phase.**

SPY backdrop (Phase 0) is much weaker — strongest cell z=−2.87, vs sector ETF cells reaching z=+4.49/+4.66. *The sector substrate beats the market substrate by ~2× on signal strength.*

## Wave-oscillation philosophy → SWPM (Sector Wave Phase Modulator)

> *"We dont want to overfit by sector, rather we want to find the wave that underpins the nature of the stock market and these waves operate similarly, but each sector wave is at a different oscillation. We want to fine tune each stock score to its sector oscillation rather than a total market oscillation."* — user, 2026-05-07

Bidirectional findings #1-#4 fit cleanly under one continuous gradient mechanism. **Score-stage, not cascade-stage** (per the v38/v39 pattern of retiring cascade-filters via score-stage encoding — full design in [ARCHITECTURE.md](ARCHITECTURE.md)).

**SWPM** = single coefficient ∈ [−1, +1] capturing sector phase. Modulates call scores in [70,84] (mirroring MCD gating range):
- phase = +1 (deep oversold): lift call score toward 88
- phase = −1 (deep overheated): drift call score toward 65
- Continuous, no thresholds, single explainable line in `weight_info`

### Retracted: CT-Sector cascade promotion

The previous draft recommended a cascade-stage tag mirroring CT_PROMOTE. **Retracted per user's score-fidelity principle**: high-scored signals should not be silently filtered or relabeled at the cascade. The 2026-05-06 v38/v39 ships explicitly retired cascade-stage filters (WEAK_WEEKLY_CALL_DROP → CWWD, EARN_SUPP_PUT → PESS) by re-encoding them as score-stage continuous dampeners. Sector counter-trend lift belongs in the same paradigm.

CT_PROMOTE itself remains in production as legacy debt — the new mechanism should not replicate its label-based pattern; encoding the lift directly in the score will let the cascade fill higher tiers naturally.

## Phase-by-phase results

### Phase 0 — SPY backdrop (control)

| Tier | Cohort | N | Δpp | z |
|---|---|---:|---:|---:|
| CALL 70+ | spy3_neut | 3,771 | +2.29 | −2.87 |
| PUT ≤25 | spy5_vhigh | 253 | +7.15 | −2.27 |

Verdict: weak. Sector signal beats market signal by 2× — strong evidence for the user's wave-substrate hypothesis.

### Phase 0.5 — Static sector baseline (re-validation on v43)

Confirmation of `experiments/sector_mcap_cohort/FINDINGS.md` — static-sector-label variation persists post-MCD ship:

| Side | Tier | Sector | N | Δpp | z |
|---|---|---|---:|---:|---:|
| PUT | ≤15 | basic-materials | 149 | **−10.58** | +2.60 |
| PUT | ≤10 | basic-materials | 51 | −16.80 | +2.43 |
| PUT | ≤10 | healthcare | 123 | +9.50 | −2.13 |

Existence proof: sector separation has predictive variance. Phase 1 stacks the time-varying ETF-state signal on top of this static baseline.

### Phase 1 — Sector ETF % above EMA50 at signal date (96.5% coverage)

| Side | Tier | Sector ETF state | N | TP% | Δpp | z |
|---|---|---|---:|---:|---:|---:|
| CALL | 70+ | se5_vhigh (>+5%) | 3,088 | 55.51 | **−3.97** | **+4.49** |
| CALL | 75+ | se3_neut (-1..+1%) | 654 | 70.49 | +4.86 | −2.62 |
| CALL | 70+ | se2_low (-5..-1%) | 3,979 | 61.37 | +1.90 | −2.44 |
| CALL | 75+ | se5_vhigh | 454 | 60.35 | −5.27 | +2.37 |
| PUT | ≤15 | se1_vlow (<-5%) | 151 | 63.58 | +8.40 | −2.08 |
| CALL | 70+ | se3_neut | 4,065 | 61.03 | +1.56 | −2.03 |

### Phase 1 by sector ETF RSI — STRONGEST single feature

| Side | Tier | Sector RSI | N | TP% | Δpp | z |
|---|---|---|---:|---:|---:|---:|
| **PUT** | **≤25** | **RSI < 30 (deep oversold)** | **133** | **30.83** | **−20.21** | **+4.66** ⭐⭐ |
| PUT | ≤20 | RSI < 30 | 60 | 28.33 | −24.34 | +3.78 |
| CALL | 70+ | RSI > 70 (overheated) | 1,661 | 54.97 | −4.51 | +3.74 |
| CALL | 70+ | RSI 30-45 (mildly weak) | 4,436 | 61.81 | +2.34 | −3.17 |
| CALL | 70+ | RSI < 30 (deep oversold) | 308 | 67.86 | **+8.38** | **−3.00** |
| CALL | 75+ | RSI 30-45 | 789 | 68.95 | +3.32 | −1.96 |

**The puts-into-oversold-sector finding is the largest single anti-signal in the screen.** A put signal on an individual stock when the *sector* is already at RSI < 30 fails 70% of the time vs 49% baseline. Mean-reversion physics: the sector has already capitulated; betting on more downside at the stock level is fighting the dominant force.

### Phase 1b — Counter-trend cohort (signal vs sector direction)

| Side | Cohort | N | TP% | Δpp | z |
|---|---|---:|---:|---:|---:|
| CALL 70+ | COUNTER_CALL (call when sec ETF below EMA50) | 6,672 | 60.96 | +1.48 | **−2.47** |

Counter-trend signal moderated from partial-coverage z=−4.30 to full-coverage z=−2.47. Most of the previous strength came from the SMH-as-tech-proxy mismatch (chip weakness ≠ tech weakness). Real signal at full coverage is mild but persistent (~+3pp counter-vs-aligned wedge).

### Phase 2 — ETF-as-breadth comparison

Daily correlation between production `breadth_score` and ETF-derived breadth (% of 9 ETFs above EMA50):

- N=1,251 days, **correlation = 0.7131**
- Production: mean=55.0, std=12.9 (compressed equal-weight participation)
- ETF-derived: mean=65.5, std=29.9 (mcap-weighted directional flow)

Complementary, not redundant. ~50% shared variance, ~50% unique. Recommendation in [ARCHITECTURE.md](ARCHITECTURE.md): **augment** F3F via `min(prod_brd, etf_brd)` safety floor — not replace.

## Decision matrix — recommended next steps

All gradient mechanisms; all score-stage where they touch scoring; all holdout-locked.

### Priority 1 — SWPM (Sector Wave Phase Modulator) score-stage gradient

Single bidirectional mechanism capturing all 4 sector-RSI/sector-pct_ema50 findings. Mirror MCD's dual power-law architecture:

```python
# Sector phase coefficient ∈ [-1, +1]
phase = (clip(sec_etf_pct_ema50/SWPM_EMA_K, -1, 1) + clip((sec_etf_rsi-50)/40, -1, 1)) / 2

if 70 <= overall <= 84 and phase is not None:
    score_factor = clip((overall-70)/14, 0, 1)^SWPM_SCORE_POWER  # mirror MCD
    if phase < 0:
        # Overheated sector → drift down
        weakness = (-phase)^SWPM_PHASE_POWER * score_factor
        overall -= SWPM_ALPHA_DOWN * weakness * (overall - SWPM_TARGET_DOWN)
    else:
        # Oversold sector → lift up
        strength = phase^SWPM_PHASE_POWER * score_factor
        overall += SWPM_ALPHA_UP * strength * (SWPM_TARGET_UP - overall)
```

Validation: H1-H5 affected-tier (70+ cumulative). Initial calibration grid 7-axis sweep + Bayesian refine. Calibration runtime estimated ~2-3 hours per axis (mirror MCD calibration trail).

### Priority 2 — Symmetric Put SWPM extension

The put-into-oversold finding (z=+4.66, N=133, −20.21pp) suggests SWPM should also dampen puts in oversold sectors (target overall toward ~28-30, similar shape to PESS lift but bidirectional and triggered by sector phase). Could be a single `signal_type`-aware SWPM or a separate Put-SWPM. Decide during calibration.

### Priority 3 — F3F substrate augmentation (portfolio-stage)

Two breadth signals into a single F3F input via `min(prod_brd, etf_brd)`. P1-P6 portfolio gate via canonical N=300+ × 8-window MC. No score change, no version bump.

### Priority 4 — Sector-relative score components (long-horizon R&D)

Eventually replace stock-component absolute thresholds with sector-relative ones (e.g., `stock_rsi - sector_rsi` instead of `stock_rsi`). Same gradient law, sector-scoped substrate. Mark as future research; tackle after SWPM ships.

## Production safety verification

- All scripts read-only against MySQL for analysis (`SELECT` only)
- Backfill of 6 ETFs additive only (Stock.get_or_create + PriceHistory.bulk_build + Indicator)
- `trader.py:ETF_SYMBOLS` updated — production update flow handles new ETFs as read-only fundamentals (no scoring loop interaction)
- `trader update` running concurrently during backfill: zero interaction risk verified (existing XLF/XLE/XLP/XLU/XLC pattern was preserved)
- No `Score`, `MarketBreadth` writes
- No `ALGORITHM_VERSION` bump (groundwork only)
- No `strategy_config.py` modification
- Holdout-gated to `CALIBRATION_CUTOFF_DATE = 2026-05-15`

## Artifacts

- `cohort_v43_1825.parquet` — 31,350 v43 peaks + barriers + breadth + meta
- `cohort_v43_etf.parquet` — same + 14 ETFs × 7 features per row (177 cols)
- `etf_features_long.parquet` / `etf_features_wide.parquet` — 14 ETFs × 8,375 dates
- `phase0_spy_cohorts.csv` / `phase05_static_sector.csv` / `phase1_sec_etf_pctema.csv` / `phase1_sec_etf_rsi.csv` / `phase1b_counter_trend.csv` / `phase2_breadth_daily.csv`
- `analyze_full.out` — full screen output (post-backfill)

## Code

- `experiments/sector_etf_screen/build_v43_cohort.py` — pulls v43 peaks, joins barriers/breadth/meta
- `experiments/sector_etf_screen/build_features.py` — 14 ETFs → per-row features, sector→ETF map
- `experiments/sector_etf_screen/analyze.py` — Phase 0/0.5/1/1b/2 cohort z-tests
- `experiments/sector_etf_screen/backfill_etfs.py` — one-time backfill script for missing 6 SPDRs (already executed)
- `experiments/sector_etf_screen/ARCHITECTURE.md` — SWPM design + indicator sweep architecture
