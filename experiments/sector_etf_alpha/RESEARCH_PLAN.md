# Sector ETF Alpha — SWPM Stage 1 Calibration

**Date:** 2026-05-09 (overnight autonomous)
**Active version:** v46 (`f274eb6`, WVD-Wave shipped)
**Holdout:** ≤ 2026-05-15 (`experiments/_holdout` filter mandatory)
**Framework:** Three-stage calibration (per `assessment-backtest.md` 2026-05-08 update). Stage 1 only — barrier-independent, WR7-primary.

## Background

`experiments/sector_etf_screen/` (Phase 0/0.5/1/1b/2 on v43, 2026-05-07) found four cohort effects at z|≥3:

| # | Cell | z | direction |
|---|---|---:|:---:|
| 1 | CALL 70+ × sec ETF >+5% above EMA50 | +4.49 | dampen |
| 2 | PUT ≤25 × sec ETF RSI < 30 | +4.66 | lift OUT of put zone |
| 3 | CALL 70+ × sec ETF RSI > 70 | +3.74 | dampen |
| 4 | CALL 70+ × sec ETF RSI < 30 | -3.00 | lift |

Phase 1 used WR15 at option-aligned barriers (TP=1.274σ/SL=0.983σ). The **new Stage 1 framework (2026-05-08)** anchors on **WR7** with multi-barrier-window directional consistency at WR3/WR5/WR7/WR15/WR30 on the AFFECTED COHORT — not cumulative tier WR.

The proposed mechanism, **SWPM (Sector Wave Phase Modulator)**, is documented in `experiments/sector_etf_screen/ARCHITECTURE.md` but has not been calibrated.

## Investigation goal

**Validate or reject SWPM under the new Stage 1 framework on the current v46 substrate, using the staged Bayesian calibration methodology (LHS → drill → fine).** If a winner clears W1-W6, document the ship candidate. **DO NOT SHIP** — Stage 1 ship requires explicit user authorization.

## Hypothesis families (10 total)

The Phase 0/0.5/1/1b/2 screen has only explored sector-ETF state in *isolation* (RSI, %EMA50, counter-trend). The substrate has shifted v43 → v46 with five new score-stage mechanisms (PCD, CWWD, PESS, MCD, ICH, WVD). Many of those re-shape the score distribution that the cohort-z signal originally fit. **Re-validate first, then extend.**

### A. Re-validation on v46 substrate (RE-PROVE existing)
- **H1**: sector-ETF % above EMA50 ≥ +5%, CALL 70+ → expect z ≥ +3 dampen
- **H2**: sector-ETF RSI ≥ 70, CALL 70+ → expect z ≥ +3 dampen
- **H3**: sector-ETF RSI ≤ 30, CALL 70+ → expect z ≥ +3 lift (counter-trend)
- **H4**: sector-ETF RSI ≤ 30, PUT ≤25 → expect z ≥ +3 lift OUT (put fails)

### B. NEW dimensions
- **H5**: stock-vs-sector relative strength (`stock_ret_10d − sector_ret_10d`) — terciles on signal date
  - H5a: outperforming stock × CALL = continuation (lift)?
  - H5b: underperforming stock catching up × CALL = mean-reversion alpha?

- **H6**: sector ETF MACD line direction × stock signal direction
  - H6a: sector MACD positive (above zero) + CALL 70+ = sector momentum confirmation
  - H6b: sector MACD negative + CALL 70+ = counter-trend

- **H7**: cross-sector dispersion (% of 11 SPDRs above EMA50) at signal time
  - When sector breadth is concentrated (top quartile of SPDRs above EMA50), is alpha better or worse?

- **H8**: SPY context interaction — does the sector signal STACK or SUBSUME the SPY backdrop?
  - Test: control for SPY pct_ema50 in cohort z, see if sector signal residualizes

- **H9**: sector ETF momentum (ret_20d) terciles
  - H9a: sector falling 20d × PUT = continuation
  - H9b: sector falling 20d × CALL = counter-trend

- **H10**: stock-mcap × sector-state interaction (small-cap may be more responsive to sector lead/lag)

## Data plan

### Cohort parquet — `cohort_v46_1825.parquet`
- Source: same pattern as `experiments/sector_etf_screen/build_v44_cohort.py`
- Active version: 46
- Lookback: 1825 days (5y)
- Barrier set: GENERIC (`.cache/runner/barriers_1825.parquet`) at w_days ∈ {1, 7, 15, 30}
- Holdout filter: `pre_cutoff_filter` mandatory
- Joined columns: peaks (overall, components, weight_info, regime), stock meta (sector, mcap), barrier outcomes per W, breadth_score, sector ETF features at signal date

### ETF features — reuse `etf_features_wide.parquet`
14 ETFs × 7 features daily. Already covers SPY, XLK, XLV, XLY, XLI, XLB, XLRE, XLF, XLE, XLP, XLU, XLC, SMH, IWM. Missing: weekly composite (would need to build), MACD signal/hist (only line stored).

### Stock features (to add for H5)
- stock_ret_5d, stock_ret_10d, stock_ret_20d at signal date
- Read from PriceHistory join; compute log returns

## Stage 1 framework gates (from assessment-backtest.md)

| Gate | Threshold |
|---|---|
| **W1** | Cohort z ≥ +3 in proposed direction (pre-flight) — abandon if not met |
| **W2** | WR3 / WR5 / WR7 / WR15 / WR30 all move SAME direction on affected cohort at 5y |
| **W3** | WR7 1y / 3y / 5y signs agree on affected cohort |
| **W4** | Per-discrete-bucket WR7 ≥ baseline -0.5pp at 5y. Cumulative tiers can mask asymmetry. |
| **W5** | N capacity floor — per-tier offered/yr ≥ floor in `known-issues.md` H6 table |
| **W6** | Inter-bucket WR7 gradient preserved (95+ ≥ 90+ ≥ ... ≥ 70+; symmetric for puts) |

**Note on WR3/WR5:** the bulk barrier cache has w_days ∈ {1, 7, 15, 30, 60, 90, 150}, NOT {3, 5}. For W2 directional consistency I will use {WR1, WR7, WR15, WR30} as proxy. WR1 is a partial substitute for WR3/WR5 short-window noise check.

## Phase plan

### Phase A — Substrate refresh (~30 min)
1. Build `cohort_v46_1825.parquet` (peaks v46 + 4 W-buckets of barriers + meta + breadth + ETF features at signal date)
2. Add stock_ret_10d / stock_ret_20d
3. Holdout filter

### Phase B — W1 cohort z pre-flight (~20 min)
- Re-run all H1-H4 on v46 cohort. Flag if any weakened below z=+3 (would falsify the SWPM premise on current substrate).
- Run all H5-H10 NEW hypotheses with cohort z (Pearson approximation: `(p_cohort - p_rest) / sqrt(p_rest*(1-p_rest)/N_cohort)`)
- Output: cohort table per hypothesis × call/put × discrete tier; flag z|≥3 cells

### Phase C — Stage 1 LHS blast radius (~30 min)
For each hypothesis family that passes W1, sweep the SWPM parameter space via Latin Hypercube (~80-100 variants):

```
SWPM_EMA_K       ∈ [3, 12]    # %EMA50 saturation
SWPM_RSI_K       ∈ [25, 45]   # RSI distance saturation (from 50)
SWPM_PHASE_POWER ∈ [1.0, 3.0] # phase coefficient power (concentration)
SWPM_SCORE_POWER ∈ [1.0, 2.5] # score-norm power (mirror MCD)
SWPM_ALPHA_DOWN  ∈ [0.4, 1.0] # call dampen strength
SWPM_TARGET_DOWN ∈ [55, 70]   # call dampen target
SWPM_ALPHA_UP    ∈ [0.4, 1.0] # call lift strength (oversold sector)
SWPM_TARGET_UP   ∈ [80, 92]   # call lift target
SWPM_ALPHA_PUT   ∈ [0.4, 1.0] # put lift OUT strength (oversold sector → puts fail)
SWPM_TARGET_PUT  ∈ [28, 35]   # put lift target
SWPM_GATE_LO     ∈ [70]       # locked at MCD lo
SWPM_GATE_HI     ∈ [84]       # locked at MCD hi
```

Evaluate each variant: WR7 lift on affected cohort + per-discrete-bucket non-regression.

### Phase D — Bayesian drill (~30 min)
Top 25 candidates from Phase C → 200 Optuna evals in restricted basin.

### Phase E — Fine grid + W1-W6 gate (~10 min)
Dense ±10% around Phase D winner. Full multi-window WR1/7/15/30 directional check + N floor + gradient preservation.

### Phase F — Findings doc
Write `FINDINGS.md`:
- W1 cohort z table on v46 (re-validation + new)
- W2-W3 multi-window evidence on top candidate
- W4 per-discrete-bucket table
- W5 N capacity check
- W6 gradient preservation check
- Ship candidate config OR NULL with falsification reason
- Calibration trail (variant counts, top-10 table)

## Production safety

- Read-only against MySQL (SELECT only)
- All work under `experiments/sector_etf_alpha/` and `.cache/sector_etf_alpha/`
- NEVER modify production scoring code (`database/utils/scoring.py`, `core.py`, `simulator.py`, `api.py`, `trader.py`)
- Use runtime monkey-patch with `try/finally` teardown for variant evaluation (canonical pattern: `experiments/weekly_avwap/phase_i_wadj_blend_sweep.py`)
- Holdout filter mandatory before any aggregate
- NO `ALGORITHM_VERSION` bump
- NO `strategy_config.py` edits
- NO Score / MarketBreadth / EarningsDate writes
- DO NOT SHIP — Stage 1 ship requires user authorization

## Cost estimate

- Phase A: ~30 min
- Phase B: ~20 min
- Phase C: ~30 min
- Phase D: ~30 min
- Phase E: ~10 min
- Phase F: ~15 min docs

**Total ~2-2.5 hours** if signal exists. Less if W1 falsifies on v46 substrate.

## Decision tree

```
Phase B
├── ALL H1-H4 z ≥ +3 on v46     → SWPM premise intact. Run Phases C/D/E.
├── H1-H4 weakened to z ∈ [2,3] → marginal; run Phases C/D/E with skepticism, flag in FINDINGS
└── H1-H4 below z=2             → falsified on v46. Skip Phases C/D/E. Document falsification.

H5-H10 NEW
├── At least one z ≥ +3         → expand SWPM phase coefficient or add new dampener family
└── All below z=2                → confined to original Phase 1 axes
```
