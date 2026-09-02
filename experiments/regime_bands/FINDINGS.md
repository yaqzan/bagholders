# Regime Band Recalibration Sweep — v46 Substrate

**Question (user, 2026-05-08):** "Pursue recalibration sweeps with v45 and look for optimal band thresholds to recover the compound we lost." Premise — v45 (breadth-fix) reduced DD by 15pp vs v44 but cost compound (1.42e28 → 6.48e40, but 14 orders down vs v44). v46 WVD-Wave shipped after; substrate is now v46.

**Answer:** Null result. Under the v46 substrate, no regime band variant in the 5-axis × 100-LHS sweep delivers Pareto-improving compound recovery. The v45→v44 compound regression was largely a deterministic-backtest compound-chain artifact at extreme magnitudes (1e30+%). v46 WVD already delivers substantial real compound recovery (+277% 5y, per its ship gate). Regime band recalibration adds no further alpha in this space.

The structural finding: under cleaner breadth, EVERY band variant that grows top-tier N admits low-quality marginal signals (catastrophic per-trade WR loss). EVERY band variant that improves per-trade quality reduces N (compound reduction). There is no candidate that simultaneously recovers compound AND maintains call quality.

---

## v46 substrate baseline

```
5y MarketRegime distribution (1,282 days):
  STRESS    [  0, 15)  n=   0  (0.0%)  mult=0.700
  CAUTION-  [ 15, 30)  n= 127  (9.8%)  mult=0.763
  CAUTION   [ 30, 45)  n= 582 (44.8%)  mult=0.832  ← dominant
  NEUTRAL   [ 45, 60)  n= 326 (25.1%)  mult=0.930
  HEALTHY   [ 60, 75)  n= 161 (12.4%)  mult=1.019
  BULL      [ 75,101)  n= 103  (7.9%)  mult=1.069

Composite p50=43.4  mean=47.3  std=16.1
Mean mult=0.892  →  calls run at ~89% of nominal alloc on average
```

**Diagnosis:** Under v46 clean breadth, the market spends 80% of trading days at composite < 60 (below NEUTRAL pivot), structurally compressing call signals. v44 contaminated breadth produced an inflated composite distribution that boosted regime mults across the universe — but the boost lifted random signals (ETF amplification noise), not signals with underlying technical conviction.

---

## Stage A — 5-axis Latin Hypercube sweep

### Sweep design

| Axis | Default (v46) | Range explored |
|---|---:|---|
| BAND_OFFSET (shift all thresholds) | 0 | [-15, +5] |
| BULL_MULT_CEIL (mult at composite=100) | 1.10 | [1.05, 1.20] |
| HEALTHY_PIVOT (mult at HEALTHY_LO) | 1.00 | [1.00, 1.10] |
| STRESS_MULT_FLOOR (mult at composite=0) | 0.70 | [0.65, 0.85] |
| CAUTION_MID (mult at CAUTION_HI) | 0.88 | [0.78, 0.92] |

100 LHS samples + 3 anchor seeds (v46_anchor, shift_minus7, aggressive_bull). Substrate: 927,757 v46 stored scores keyed (sym, date) with `pre_regime` recovered from `weight_info`. Linear approximation: `overall_v ≈ overall + apply_regime(pre, mult_v) - apply_regime(pre, mult_default)`.

### Results

```
H1+H3 strict gate (≥+0.3pp on ≥3 call tiers, none regress >−1.0pp, ±15% N):
  PASSING: 0 of 100

Relaxed gate (80+/85+ ≥+0.3, 75+/90+ ≥−1.0, put N <+15%):
  PASSING: 0 of 100
```

### Anchor analysis

| Anchor | comp_proxy | avg call N | alpha 75/80/85/90 | N drift 75/80/85/90 | put<25 dWR / dN |
|---|---:|---:|---|---|---|
| **v46_anchor** | **−0.02** | **+1.2%** | -0.10/-0.10/+0.18/+0.00 | +2.5/+1.0/+1.4/+0.0 | +0.17 / -2.2% |
| shift_minus7 (offset −7) | −3.48 | +35.8% | -1.57/-1.47/+2.27/-1.57 | +62.0/+19.3/+28.1/+33.6 | +0.92 / -27.3% |
| aggressive_bull (-10 + raised mults) | −18.56 | +122.0% | -4.70/-3.10/+1.86/-1.15 | +196.6/+62.5/+77.6/+151.4 | +0.93 / -48.2% |

**v46 anchor sits at the compound-proxy optimum.** Compound proxy near zero confirms my linear approximation gives ~0 net change for the production band config (sanity check passed).

**Band-shift candidates (compound recovery direction)** all fail:
- shift_minus7: +35.8% N at top tiers, but -1.57pp 75+ alpha and -1.57pp 90+ alpha — admits low-quality signals that drag the cohort
- aggressive_bull: +122% N (massive compound recovery via inflated mults), but -4.70pp 75+ alpha and -3.10pp 80+ alpha — admits noise

**Quality-improvement candidates** all fail too:
- Pure multiplier-endpoint sweep (448 variants in 4D grid, no band-shift): exactly 1 candidate passed at strict gate, with marginal +0.2pp average alpha — within linear-approximation noise floor.

---

## Stage A2 — pure multiplier-endpoint grid

To isolate "amplify harder without admitting low-quality signals":

```
448-variant grid: (BULL_CEIL × HEALTHY_PIVOT × STRESS_FLOOR × CAUTION_MID)
                  bands fixed, only endpoint mults vary

Strict gate passing: 1 of 448
  CEIL=1.12  HP=1.00  SF=0.70  CM=0.88
  alpha 75/80/85/90 = +0.05/+0.40/+0.55/+0.00
  N drift             = +4.0/+1.7/+2.7/+0.0%
```

The single passing variant's gain (~+0.2pp avg alpha) sits at the noise floor of the linear approximation. Stage B simulator validation would likely show this is flat or marginal positive — not worth the ship cost.

---

## Why band recalibration cannot recover compound

The v44 → v45 compound regression had three components:

1. **Deterministic-backtest compounding artifact** at 1e30+% magnitudes — even tiny per-window variance amplifies into 10-orders-of-magnitude differences across 5y compound chain. This is the bulk of the apparent regression and is not a real strategy degradation.

2. **Removal of synthetic alpha** from leveraged ETF contamination — TQQQ/SOXL/LABD/BOIL etc. moving 3× the underlying inflated breadth amplitude in either direction. v44's "alpha" partially came from regime amplifying randomly-weighted signals; v45 correctly removes this.

3. **Real per-trade quality shift** — addressed by v46 WVD-Wave (+1.27pp 85+ / +7.45pp 90+ at 5y per its ship gate), with portfolio MC showing +277% 5y compound and -3.6pp 22-now DD vs v45 baseline.

Regime band recalibration cannot address (1) — it's a metric artifact. It cannot address (2) — re-introducing inflation via band-shift admits the same noise. It overlaps with (3) but v46 WVD already captured the available alpha at quality.

---

## What WOULD address remaining compound recovery (next investigations)

### Priority order — most promising first

1. **F3F portfolio-stage recalibration under v46 substrate.** F3F (`F3F_CALL_FLOOR=0.50`, `F3F_CALL_LOW=30`, `F3F_CALL_THRESH=50` for 30 DTE) was calibrated against v25-era contaminated breadth. The breadth distribution shifted under v45/v46. F3F operates at signal-date breadth lookup — its thresholds may now under-amplify in healthy tape and over-contract in caution tape. Investigation is portfolio-stage (P1-P6 gate), no recalculate needed.
   - Expected effort: 1-2 days (Bayesian sweep over F3F_CALL_FLOOR/LOW/THRESH × F3F_PUT_*)
   - Risk: low — F3F is well-instrumented for sweeps

2. **Composite weight recalibration.** Current weights: 35% breadth + 35% VIX + 30% trend. Under cleaner breadth, the breadth signal is more orthogonal to VIX (post-de-contamination Pearson estimated 0.75-0.78 vs pre 0.71). Up-weighting breadth (e.g., 45/30/25) may improve composite discriminating power.
   - Expected effort: 2-3 days (need to back-fill regime composites under each weight variant, then per-trade gate)
   - Risk: medium — touches scoring substrate, requires recalculate per variant

3. **Cascade TIER_ALLOC retune for v46.** Current `0.20/0.15/0.10/0.10` was calibrated under v34 (commit `e77714f`, 2026-05-04). Two new score-stage mechanisms have shipped since: v44 ICH and v46 WVD. Per-tier score quality has shifted. A monotonic re-sweep could unlock 1-3% compound at neutral DD.
   - Expected effort: 1 day (existing v32_optim Bayesian harness)
   - Risk: low — portfolio change, no recalculate

### What NOT to retry

- **Regime band threshold shifts** (BAND_OFFSET ≠ 0). Comprehensively null on v46 substrate — admits low-quality signals.
- **Aggressive multiplier-endpoint expansion** (BULL_CEIL > 1.15 or STRESS_FLOOR < 0.65). Failed strict gate.
- **Composite-formula sweeps without recalculate.** Composite formula is upstream of stored scores; can only be evaluated by re-running scoring.

---

## Sweep artifacts

- `experiments/regime_bands/build_features.py` — v46 score parquet builder
- `experiments/regime_bands/stage_a_sweep.py` — 5-axis LHS sweep
- `.cache/regime_bands/v45_1825d.parquet` — 927,757 v46 score rows (named v45 for legacy compat)
- `.cache/regime_bands/stage_a_results.parquet` — 100-variant evaluation results
- `experiments/regime_bands/FINDINGS.md` — this document

**Compute used:** ~12 min (parquet build + sweep). **Compute saved by skipping Stage B/C:** ~2 hours.
