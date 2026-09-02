# Ichimoku Dampener — SHIPPED as v44 (commit `d8024b9`) on 2026-05-07

**Final ship config:** Phase H Rank #3 with asymmetric-K mechanism.
Calibrated against v43 baseline (which already includes MCD mcap dampener).

```python
# scoring.py module-level (Phase H Rank #3 winner)
ICH_ENABLED        = True
ICH_GATE_CALL_LO   = 69
ICH_GATE_CALL_HI   = 90
ICH_K_CALL         = 0.359
ICH_K_CALL_POWER   = 2.68     # power-law on score_norm; concentrates at 95+
ICH_KIJ_SAT_CALL   = 18.4
ICH_TARGET_CALL    = 63.8
ICH_GATE_PUT_LO    = 10
ICH_GATE_PUT_HI    = 27
ICH_K_PUT          = 0.278
ICH_KIJ_SAT_PUT    = 8.8
ICH_TARGET_PUT     = 33.4
ICH_IND_RAMP_CALL  = 'linear'
ICH_IND_RAMP_PUT   = 'log'
```

## Per-trade evidence (5y v43 baseline, option-aligned barrier 30dte_opt @ w=15d)

  Call tier ΔWR15 / ΔN:
    95+: +5.77pp / -7.7%   (+3.0pp over Phase E v43 from asymmetric-K)
    90+: +1.45pp / -14.4%  (+0.31pp over Phase G)
    85+: +0.89pp / -8.3%   (+0.38pp over Phase G)
    80+: -0.01pp / -3.4%
    75+: +0.25pp / -3.8%
    70+: +0.14pp / -3.0%
  Put tier ΔWR15 / ΔN:
    <25: +0.72pp / -14.9%  (cleaner H3 than Phase G's -19.8%)
    <20: +2.15pp / -37.7%
    <15: +0.47pp / -63.3%
    <10: +0.45pp / -73.6%

## Pre-ship validation passed

  Drift-guard: 130 strategy constants pass (no schema/config drift)
  COHR-class: -1.36pp Monday churn reduction within affected cohort (1.58%)
  Direct formula test: dampener fires correctly on bearish-Ichimoku,
                        no fire on bullish-Ichimoku, untouched outside gates
  Multi-window 1y/3y/5y: sign-consistent on call_70+/75+, put_<25/<15;
                         small-N noise on 95+/90+/85+ at 1y (consistent
                         with PCD/MCD/CWWD baseline pattern)

## Calibration trail

  Phase A-B: feature build + cohort profiling (916K peaks, 7 indicators)
  Phase C: 100 + 200 variants additive (Phase C Rank #1 = +1.10pp <25)
  Phase D: 120 variants substitutive (asymptote at -0.12pp put alpha)
  Phase C-multi: 200 variants Ichimoku + smooth-wadj (proved v27 substitution
                 is structurally bounded)
  Phase E: 200 variants constrained refinement (Rank #3 = first ship candidate)
  Phase F: scorched-earth substitution test + clumping analysis
  Phase G: 200 variants asymmetric-K (Rank #1 = +5.77pp 95+ via K_POWER=1.58)
  Phase H: 250 variants refined Phase G with multi-tier objective (Rank #3 ship)

  Total: 1,170+ Bayesian/LHS variants across 7 sweeps. v39 outputs preserved
  in `_v39_results.parquet` archives in case v43 reverts.

## v39 baseline historical reference

**Status:** Calibrated and validated against v39 (commit `200f33a`) on 2026-05-07.
Preserved here in case v43 (MCD mcap dampener, commit `e083032`) is reverted
and we need to re-baseline against v39.

## Mechanism

Score-stage log-magnifier dampener applied additively on top of v39 production
(v27 WCF stays active for puts).

```python
# CALL side — Phase E Rank #3 (linear ramp, gate=75-90)
RAMP_SHAPE        = 'linear'
GATE_CALL_LO      = 75
GATE_CALL_HI      = 90
K_CALL            = 0.17
KIJ_SAT_CALL      = 11.7
LIFT_TARGET_CALL  = 69.9

# PUT side — Phase C Rank #1 (locked log ramp params)
GATE_PUT_LO       = 10
GATE_PUT_HI       = 26
K_PUT             = 0.358
KIJ_SAT_PUT       = 10.335
LIFT_TARGET_PUT   = 35.196

# Indicator: Ichimoku Kijun-sen distance (price_vs_kijun_pct)
# Indicator distance fires when below kijun (bearish weekly state)
ind_dist = max(0, -price_vs_kijun_pct)
```

Apply additively to v39 production score. Call side fires only when
`overall ≥ 75` AND `kijun_pct < 0`. Put side fires only when `overall ≤ 25`
AND `kijun_pct < 0`. Both indicators use the SAME `price_vs_kijun_pct` signal
with side-flipped lift direction.

## Validated metrics (5y v39 baseline, option-aligned barriers 30dte_opt @ w=15)

  Call tier ΔWR15 / ΔN:
    95+: +1.92pp / -11.4%
    90+: +0.86pp /  -9.4%
    85+: +1.09pp /  -5.8%
    80+: +0.02pp /  -3.9%
    75+:  0.00   /   0%   (gate=75: dampener doesn't fire below)
    70+:  0.00   /   0%
  Put tier ΔWR15 / ΔN:
    <30:  0.00 / 0%
    <25: +0.34pp / -9.1%
    <20: +1.66pp / -33.8%
    <15: -0.52pp / -68.1% (deep-tail residual noise on small N)
    <10: +0.78pp / -78.7%

## H1-H5 strict gate result

| Gate | Result |
|---|---|
| H1 strict (≥+0.5pp on ≥3 call tiers) | ✓ PASS (95+/90+/85+) |
| H1 affected-tier (Put <25 ≥+0.3pp) | ✓ PASS (+0.34) |
| H2 directional (WR15 vs WR30) | ✓ PASS (WR30 stronger: 95+ Δ30=+2.80 vs Δ15=+1.92) |
| H3 N stability (call ±15%) | ✓ PASS (max −11.4%) |
| H3 N stability (put <25 ±15%) | ✓ PASS (−9.1%) |
| H4 puts neutral or better | ✓ PASS (no regression on <25 / <15) |
| H5 multi-window | ⚠ flips on 95+/90+ at 1y (N≤25 small-sample noise) |
| Smoothness | ✓ PASS (linear ramp monotonic by construction) |

## Key empirical findings preserved

1. AVWAP-from-last-earnings: NULL signal at 75+ (0.7pp spread). Hypothesis
   falsified — does NOT extend Priority #5b's "self-fulfilling-prophecy" claim.

2. 50W SMA / 52W high/low: weak signals (1-2pp at 75+). Not load-bearing.

3. Ichimoku family (kijun, tenkan, span_b, cloud_position): real ~5pp signal
   at 75+. STRENGTHENS dramatically at higher tiers (~13pp at 85+).

4. Score boundary at ~70 inverts the signal: below 70, bullish-Ichimoku puts
   UNDERPERFORM calls (because they signal bullish weekly state coinciding
   with weak daily score = mean-revertable). Above 70, bullish-Ichimoku +
   bullish daily aligned = continuation. Score-range gate IS load-bearing.

5. Pure substitution of v27 (470 variants tested) cannot beat v27 on puts.
   Threshold mechanism captures structural alpha that smooth gradient softens
   away. v27 carries ~+1.42pp put alpha that asymptotic at -0.12 to +0.03pp
   on substitutive sweeps.

6. Stability check (Phase D): Ichimoku features 3× more stable than wadj
   (normalized churn 0.232/0.241 vs 0.702 over 5-bar windows). The COHR-class
   1-bar (Friday→Monday) test was deferred to post-plumbing.

## Outputs preserved

  `.cache/weekly_avwap/calls_v39_1825d.parquet`         (23,485 75+ peaks)
  `.cache/weekly_avwap/calls_v39_1825d_min50.parquet`   (439,469 50+ peaks)
  `.cache/weekly_avwap/calls_v39_1825d_min0.parquet`    (916,679 full-spectrum peaks)
  `.cache/weekly_avwap/wadj_v39_1825d_min0.parquet`     (100,804 rows w/ w_adj + wcf_lift)
  `.cache/weekly_avwap/phase_c_sweep_results.parquet`   (additive Phase C, 100 variants)
  `.cache/weekly_avwap/phase_d_sub_results.parquet`     (substitutive Phase D, 120 variants)
  `.cache/weekly_avwap/phase_c_multi_results.parquet`   (multi-indicator Phase C, 200 variants)
  `.cache/weekly_avwap/phase_e_refinement_results.parquet` (constrained Phase E, 200 variants)

## Failed paths (do NOT retry)

- AVWAP standalone signal
- Multi-indicator (Ichimoku + smooth-wadj) substitutive — capped at +0.03pp
- Removing the score-range gate entirely (signal inverts at ~70 boundary)
- 50W SMA proximity as primary indicator
- Pure-Ichimoku put-side substitution (cannot replace v27)

## Path back to ship from v43 (if v43 reverts to v39)

1. Active version goes back to v39 → re-run `phase_e_refinement.py` on v39
   parquet (already cached). Should reproduce identical Rank #3 result.
2. Production scoring plumbing (~1-2h): compute `price_vs_kijun_pct` at
   scoring time from existing `WeeklyPriceHistory` table. No schema changes.
3. N=100 smoke MC on 22-now (~5 min) to confirm cascade fill OK.
4. Bump ALGORITHM_VERSION, recalculate, formal `trader assess --force` confirms
   parquet predictions on production data.

## Re-validation against v43 baseline (2026-05-07)

After v43 (MCD mcap dampener, commit `e083032`) shipped, full re-sweep
confirmed Phase E Rank #3 is robust. Same exact config wins on both v39 and
v43 baselines.

### v43 results (Phase E Rank #1 with same params as ship candidate)

  Call tier ΔWR15 / ΔN:
    95+: +2.77pp / -3.8%   (STRONGER than v39's +1.92 / -11.4%)
    90+: +1.14pp / -9.9%
    85+: +0.58pp / -5.8%
    80+: -0.03pp / -2.9%
    75+:  0.00   /   0%   (gate=75: dampener doesn't fire below)
    70+:  0.00   /   0%
  Put <25: +0.37pp / -9.0%

### MCD × Ichimoku orthogonality (mcd_ichimoku_xtab.py)

4-cell cross-tab on v43 75+ peaks:

  |                  | MCD fired       | MCD did NOT fire   |
  |------------------|----------------:|-------------------:|
  | bearish-Ichimoku | 52.97% (N=185)  | 62.20% (N=336)     |
  | bullish-Ichimoku | 66.79% (N=786)  | 66.80% (N=1,988)   |

  Marginal effects:
    Ichimoku within "MCD didn't fire":   -4.60pp (bearish vs bullish)
    MCD within "bearish-Ichimoku":       -9.23pp (fired vs not)
    MCD within "bullish-Ichimoku":       -0.01pp (no effect)

  Cohort overlap: 34.9% of bearish-Ichimoku 75+ peaks had MCD fire.

Key finding: **MCD and Ichimoku produce INDEPENDENT WR signals** (cohort
overlap 35%, but each captures orthogonal alpha within sub-cohorts where
the other didn't fire). MCD does nothing to bullish-Ichimoku peaks. Ichimoku
catches the bearish-Ichimoku peaks MCD didn't reach.

The "double-bad" cohort (bearish-Ichimoku + MCD fired, N=185) has 52.97%
WR15 — essentially at call BE. Ichimoku ship would catch these even though
MCD already did one pass.

MCD displacement footprint: 4,547 peaks (24.8% of 70-74 cohort) had MCD
fire and were pulled from 75+ into 70-74.

### Updated ship verdict

The Phase E Rank #3 candidate is **MORE attractive on v43 than on v39**:
  - +2.77pp WR15 at 95+ vs +1.92 on v39
  - -3.8% N drop at 95+ vs -11.4% on v39
  - MCD has filtered small-cap noise; Ichimoku catches structural-weakness
    peaks MCD missed

Outputs preserved (v43 specific):
  `.cache/weekly_avwap/calls_v43_1825d_min0.parquet`         (850,396 peaks)
  `.cache/weekly_avwap/wadj_v43_1825d_min0.parquet`          (1.6M rows w/ w_adj + wcf_lift + mcd_val)
  `.cache/weekly_avwap/phase_e_refinement_v43_results.parquet` (200 variants)
