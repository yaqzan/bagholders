# Sector Wave Substrate — Architecture & Indicator Design

**Status:** Design doc, pre-implementation.
**Premise (per user 2026-05-07):** the same mean-reversion physics governs every nesting level of the price universe (stock → sector → market). Each operates at a different oscillation phase. We want to fine-tune each stock's score relative to its *sector's* oscillation, not the market-wide one. Continuous gradient mechanisms only — no threshold gates.

## Why score-stage, not cascade-stage

The 2026-05-06 v38/v39 ships explicitly retired cascade-stage filters (`WEAK_WEEKLY_CALL_DROP`, `EARN_SUPP_PUT`) by re-encoding them as score-stage dampeners (CWWD, PESS). Reason: cascade filters violate the principle that **the score should fully represent the value of the outcome**. A high-scored signal silently filtered at the cascade is a UX/fidelity bug.

All sector-substrate mechanisms below are **score-stage continuous gradients**, not cascade-stage labels. The previously-recommended "CT-Sector cascade promotion" is retracted.

CT_PROMOTE itself remains in production as legacy debt; the sector-substrate work below should not replicate its label-based pattern. (A future ship could plausibly retire CT_PROMOTE the same way v38/v39 retired their predecessors.)

## Sector → ETF mapping

| GICS sector | yfinance `sectorKey` | Primary ETF | Secondary (if needed) |
|---|---|---|---|
| Technology | technology | XLK | SMH (chip subset) |
| Health Care | healthcare | XLV | IBB (biotech) |
| Financials | financial-services | XLF | KRE (regional banks) |
| Energy | energy | XLE | XOP (E&P) |
| Consumer Discretionary | consumer-cyclical | XLY | RTH (retail) |
| Consumer Staples | consumer-defensive | XLP | — |
| Industrials | industrials | XLI | ITA (defense) |
| Utilities | utilities | XLU | — |
| Materials | basic-materials | XLB | GDX (gold miners), URA |
| Real Estate | real-estate | XLRE | VNQ |
| Communication Services | communication-services | XLC | XLG (large cap comm) |

The screen uses **only the primary SPDR**. Secondary ETFs are listed for future investigation if a sub-sector signal warrants finer slicing.

## Indicator set — what to compute on each sector ETF

Methodology guidance: track tools the **actual trading population uses** to leverage self-fulfilling-prophecy alpha. % above EMA50 is the user-named control.

### Tier 1: Universal (compute always)

| Feature | Why it's load-bearing | Math |
|---|---|---|
| `pct_ema50` ⭐ control | Single most universally-watched MA position metric | `(close - ema_50) / ema_50 × 100` |
| `pct_ema200` | The "is the trend up or down" universal | `(close - ema_200) / ema_200 × 100` |
| `rsi_14` | Universal momentum oscillator; Phase 1 already shows z=+4.09 at RSI>70 | Standard Wilder RSI |
| `macd_hist` | Universal trend-momentum confirmation | Histogram (signed) |
| `bb_position` | Mean-reversion practitioners' standard | `(close - lower) / (upper - lower)` |

### Tier 2: Institutional / algorithmic (load-bearing per Priority #5b)

| Feature | Why it's load-bearing | Math |
|---|---|---|
| `kijun_state` | v44 ICH just shipped this for stocks; Asian/algo desks watch the weekly cloud | `(close - kijun) / kijun` where kijun = (max26 + min26)/2 |
| `avwap_quarter` | Anchored VWAP from last quarterly anchor — institutional standard for sector flow | Σ(price × vol) / Σ(vol) since last quarter-end |
| `pct_52w` | Universally-watched 52-week high/low proximity | `(close - low_52w) / (high_52w - low_52w)` |

### Tier 3: Cross-asset confirmations (optional)

| Feature | Why | Math |
|---|---|---|
| `vix_at_signal` | Macro fear backdrop for sector ETF moves | VIX close on signal date |
| `dxy_state` | Dollar strength affects sector rotation | DXY % above EMA50 |
| `tnx_state` | Long yield affects rate-sensitive sectors (XLU, XLRE, XLF) | TNX % above EMA50 |

### Sweep design

Goal: identify which Tier 2/3 indicators add **marginal predictive value beyond Tier 1**. Uses the screen harness from `analyze.py` extended with marginal cohort tests.

```python
control_features = ['pct_ema50']  # user-named control

# For each candidate feature in Tier 2/3:
for cand in candidates:
    # 1. Univariate cohort z-test (already done in screen for some)
    z_uni = cohort_z(cand, tier='75+', side='CALL')

    # 2. Marginal-residual cohort test:
    #    Among signals where pct_ema50 is in middle bucket (low signal),
    #    does cand have predictive lift?
    sub = df.filter(pct_ema50_bucket == 'neutral')
    z_marginal = cohort_z(cand, df=sub)

    # 3. Joint orthogonality:
    #    Pearson(pct_ema50, cand) on signal dates — high collinearity = redundant
    rho = pearson(df['pct_ema50'], df[cand])

    # Keep cand if z_marginal>=2 AND |rho|<=0.7 AND z_uni>=2
```

## Sector Wave Phase Modulator (SWPM) — proposed score-stage mechanism

A SINGLE bidirectional gradient that captures the entire sector wave phase in one continuous coefficient ∈ [−1, +1], applied to call scores in [70, 84] (mirrors MCD gating range):

```python
# Sector wave phase coefficient ∈ [-1, +1]
# +1 = sector deeply oversold (favors calls — counter-trend lift)
# -1 = sector deeply overheated (hurts calls — overbought dampen)
def sector_phase(sec_etf_pct_ema50, sec_etf_rsi):
    # EMA position component, normalized: -1 (deep below) to +1 (deep above)
    ema_phase = clip(sec_etf_pct_ema50 / SWPM_EMA_K, -1, 1)
    # RSI component, centered at 50: -1 (RSI=10) to +1 (RSI=90)
    rsi_phase = clip((sec_etf_rsi - 50) / 40, -1, 1)
    # Composite — equal weight, can sweep
    return (ema_phase + rsi_phase) / 2

# Inside compute_overall_score, applied AFTER PCD/MCD/CWWD, BEFORE EARN_BOOST:
phase = sector_phase(...)  # ∈ [-1, +1]

if 70 <= overall <= 84 and phase is not None:
    # Score-factor gates power-law concentration at top of range (mirror MCD)
    score_factor = clip((overall - 70) / 14, 0, 1)^SWPM_SCORE_POWER

    if phase < 0:
        # Overheated sector → drift score DOWN toward (overall - 9)
        weakness = (-phase)^SWPM_PHASE_POWER * score_factor
        overall -= SWPM_ALPHA_DOWN * weakness * (overall - SWPM_TARGET_DOWN)
    else:
        # Oversold sector → lift score UP toward (overall + 6)
        strength = phase^SWPM_PHASE_POWER * score_factor
        overall += SWPM_ALPHA_UP * strength * (SWPM_TARGET_UP - overall)
```

### Calibration axes

| Param | Initial guess | Range to sweep |
|---|---|---|
| `SWPM_EMA_K` | 6 (% above/below EMA50 for full saturation) | 4-10 |
| `SWPM_PHASE_POWER` | 1.0 | 0.7-1.5 |
| `SWPM_SCORE_POWER` | 1.5 (mirror MCD) | 1.0-2.0 |
| `SWPM_ALPHA_DOWN` | 0.50 | 0.2-0.9 |
| `SWPM_ALPHA_UP` | 0.30 | 0.1-0.6 |
| `SWPM_TARGET_DOWN` | 65 | 60-72 |
| `SWPM_TARGET_UP` | 88 | 85-92 |

ALPHA_DOWN > ALPHA_UP because the screen evidence is asymmetric: overheating-hurts-calls signal is z=+4.09 (strong), while counter-trend-lift signal is z=−4.30 (also strong but at 70+ tier where displacement upward is less impactful in the cascade than displacement downward).

### Why one bidirectional mechanism instead of two

Alternatives considered:
- (a) Two separate score-stage mechanisms (SOD for overheating, SCT-SL for counter-trend)
- (b) One bidirectional SWPM **(recommended)**

(b) is simpler, has a single calibration surface, captures the wave law as a single physics, and has a single explainable line in `weight_info`. (a) has more knobs and risks the dampener and lift fighting each other in mid-band conditions.

### Validation gate

H1-H5 affected-tier (70+ cumulative since signal lives at gate boundary). Standard CWWD/MCD pattern:
- H1: 70+ TP% ≥ +0.3pp at 5y
- H2: WR15 directional consistency
- H3: N drift ≤ 15% on each tier
- H4: puts neutral (gate is overall ≥ 70 — calls only)
- H5: 1y/3y/5y sign-consistent

## ETF-derived breadth (Phase 2 follow-up)

The screen showed `corr(production_breadth, etf_breadth) = 0.71` — complementary, not redundant. Production breadth = equal-weighted internal participation; ETF breadth = mcap-weighted directional flow.

Recommended F3F augmentation (portfolio-stage, no scoring change, no version bump):

```python
# In F3F lookup at signal date:
prod_brd = MarketBreadth.breadth_score
etf_brd  = etf_basket_pct_above_ema50  # 14-ETF basket once XL* backfilled

# Safety-floor composite — when EITHER signal is weak, contract sizing
effective_brd = min(prod_brd, etf_brd)

# Apply existing F3F curve to effective_brd
scale_call = f3f_curve(effective_brd, F3F_CALL_*)
```

Validation: P1-P6 portfolio gate (canonical N=300+ × 8-window MC). No scoring change. Expected to help dip windows where ETF-flow led the production breadth indicator (e.g., Sep 2022, Apr 2025 selloffs).

## Implementation roadmap

### Phase A — Groundwork (this session, in-flight)
1. ✓ Backfill XLK/XLV/XLY/XLI/XLB/XLRE (Stock + 5y PriceHistory + Indicators)
2. ✓ Update `trader.py:ETF_SYMBOLS` to include all 11 SPDRs + SOXX
3. ✓ Re-run cohort screen at full 11-sector coverage to confirm signal magnitude
4. ✓ This ARCHITECTURE.md (design doc)

### Phase B — Sector Phase Module (next session)
1. Create `database/utils/sector_phase.py` — computes per-(sector_etf, date) features (Tier 1 + Tier 2 indicators)
2. Build `experiments/sector_phase/` — sweep harness with EMA% as control
3. Identify the 2-3 Tier 2/3 features with marginal lift (z_marginal ≥ 2, |rho| ≤ 0.7)

### Phase C — SWPM Calibration & Ship
1. `experiments/swpm_v44/sweep.py` — coarse grid over 7 axes
2. `bayes_refine.py` — dense Bayesian refinement
3. H1-H5 affected-tier gate at 1y/3y/5y/10y, holdout-locked
4. Score-stage code change in `database/utils/scoring.py` between PCD and EARN_BOOST
5. Wire `weight_info['swpm_dampen']` / `weight_info['swpm_lift']` for explain-scores
6. ALGORITHM_VERSION bump (this is a score-stage change)
7. `trader recalculate --force --full` + `trader assess --force`
8. Document in version-history.md

### Phase D — F3F Substrate Augmentation (parallel, portfolio-stage)
1. Implement `etf_basket_breadth` daily computation
2. A/B in MC: production F3F vs `min(prod_brd, etf_brd)` F3F
3. P1-P6 canonical MC gate (N=300+ × 8 windows)
4. No version bump — `strategy_config.py` mod only

### Phase E — Forward holdout re-evaluation (≈2026-11-15)
First true out-of-sample test of the SWPM mechanism. Scheduled for after the v37→v44 stack accumulates ≥6 months of post-cutoff data.

## Production safety

All groundwork:
- Read-only against existing tables for analysis (`scores`, `indicators`, `price_history`, `market_breadth`)
- Additive writes for new ETFs (`Stock.get_or_create`, `PriceHistory.bulk_build`, `Indicator` rows for 6 symbols)
- No edits to scoring engine, no version bump, no `strategy_config.py` change
- `trader update` running concurrently is safe — new ETFs handled by existing ETF_SYMBOLS skip-fundamentals path
- Holdout-gated all calibration via `experiments/_holdout.py`
