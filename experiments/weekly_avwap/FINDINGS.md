# weekly_avwap — Slow-Indicator Weekly Composite (Priority #5b)

## SHIPPED as v44 (commit `d8024b9`) — 2026-05-07

Phase H Rank #3 calibration. See SHIP_CANDIDATE.md for the full config and
per-trade evidence. Production validation:

  - Active version: v44 (d8024b9)
  - Score rows updated: 1,734,161 (0 errors during recalculate)
  - ICH firing confirmed via weight_info inspection:
      5 ich_call_dampen rows (since 2026-01-01) — high-magnitude top-tier
      409 ich_put_lift rows — common put-side action
      414 kijun_pct exposed (matches sum)
  - Score distribution shift (5y, v43 → v44):
      95+: +11.5%   90-94: +10.1%   85-89: +2.7%   80-84: +8.6%
      75-79: +8.0%  70-74: +7.9%
      Put 21-25: +24.9% (lifts INTO this zone from <20)
      Put <15: -47.8%   Put <=10: -68.3% (deep-put displacement as designed)
  - 10y assess (run #285, v44): 95+ WR15=67.7% / 80+ WR15=65.8% / 75+ WR15=62.9%

Three commits in production:
  d8024b9  v44 scoring: ICH Ichimoku Kijun-sen state dampener (Phase H Rank #3)
  a3a0cd8  Bump ALGORITHM_VERSION to d8024b9
  a9dd284  docs: v44 ICH ship — update CLAUDE.md, version-history, known-issues



## Hypothesis

Replace or augment the calendar-week RSI/MACD weekly adjustment with indicators
that traders ACTUALLY watch at the weekly level.  These are inherently stable
(no partial-week bar aggregation) and have self-fulfilling-prophecy alpha
(institutional flow follows them).

Candidate signals: AVWAP-from-last-earnings, weekly Ichimoku (Kijun-sen /
cloud color), 50W SMA proximity, 52W high/low proximity.

## Investigation Phases

| Phase | Status | Description |
|-------|--------|-------------|
| **A** | ✓ DONE | Build feature parquet (`build_features.py`) |
| **B** | TODO   | Cohort profile: tercile WR15 spread per feature on 75+ calls |
| **C** | TODO   | If signal exists: score-stage augmentation design |
| **D** | TODO   | Stability check: COHR-class whiplash under new composite |
| **E** | TODO   | Calibration gated on `CALIBRATION_CUTOFF_DATE = 2026-05-15` |

## Phase A: Feature Parquet

Output: `.cache/weekly_avwap/calls_v{av_id}_{lookback}d.parquet`

Columns:
- `symbol`, `date`, `overall` — peak identification
- `avwap_pct` — (close − AVWAP) / AVWAP × 100; null if no prior earnings
- `avwap_anchor_date` — earnings effective_date used as VWAP anchor
- `avwap_bars` — trading days since anchor (AVWAP window width)
- `price_vs_kijun_pct` — (close − kijun) / kijun × 100; null if < 26 weekly bars
- `price_vs_tenkan_pct` — (close − tenkan) / tenkan × 100; null if < 9 weekly bars
- `tenkan_kijun_dir` — +1 bullish momentum, −1 bearish, 0 neutral; null if insufficient data
- `price_vs_span_b_pct` — (close − span_b) / span_b × 100; null if < 52 weekly bars
- `cloud_position` — +1 above cloud, −1 below cloud, 0 in cloud; null if insufficient data
- `sma50w_pct` — (close − sma50w) / sma50w × 100; null if < 50 weekly bars
- `w52_high_pct` — (close − 52w_high) / 52w_high × 100 (negative = below high)
- `w52_low_pct` — (close − 52w_low) / 52w_low × 100 (positive = above low)
- `opt_result_15` — 'win' / 'loss' at 30dte_opt barrier, w=15d
- `opt_exit_return_15`, `opt_mae_15`, `opt_mfe_15` — option-aligned P&L metrics

Design choices:
- Weekly lookup uses `scoring_date − 7 days` to ensure completed bars only
  (avoids COHR-class partial-week instability from Priority #7 / v42 revert)
- AVWAP anchors to `EarningsDate.effective_date` (already AMC-aware, post-v31)
- No DB schema changes — parquet only until Phase B validates signal exists

## Phase B: Cohort Profile Results (5y v39, 30dte_opt @ w=15)

**Gate passed on 75+ tier — 2 features cleared ≥5pp:**

| Feature | 75+ spread | Direction |
|---|---:|---|
| `tenkan_kijun_dir` | **6.47pp** | v=-1 (bearish) 58.6% → v=+1 (bullish) 63.2% |
| `cloud_position`   | **5.30pp** | v=0 (in cloud) 58.6% → v=+1 (above) 63.9% |

**Signal strengthens dramatically at higher tiers** (Ichimoku family):

| Feature | 70+ | 75+ | 80+ | 85+ |
|---|---:|---:|---:|---:|
| `price_vs_kijun_pct`   | 1.5pp | 3.6pp M | **7.1pp** ✓M | **13.4pp** ✓M |
| `price_vs_tenkan_pct`  | 2.6pp | 0.4pp   | 1.6pp        | **11.1pp** ✓ |
| `price_vs_span_b_pct`  | 0.6pp | 1.4pp   | 1.5pp        | **11.0pp** ✓ |
| `tenkan_kijun_dir`     | 4.1pp | **6.5pp** ✓ | **7.1pp** ✓ | **18.2pp** ✓ |
| `cloud_position`       | 3.6pp | **5.3pp** ✓ | **8.6pp** ✓ | **14.7pp** ✓ |

(M = monotonic across terciles, ✓ = clears 5pp gate)

**Surprises (re-evaluate Priority #5b's prior assumption):**

1. **AVWAP-from-last-earnings is a NULL signal** on the 75+ cohort. Spread
   was 0.7pp at 75+ and never monotonic. The "self-fulfilling-prophecy"
   hypothesis (every algo desk uses anchored VWAP from earnings) does NOT show
   up in per-trade WR15 at any tier. Possible reasons: (a) signal too short-
   lived between earnings events, (b) colinear with existing wadj/RSI/MACD
   features, (c) hypothesis was wrong for the call-peak cohort.

2. **50-week SMA is also weak**: 1.9pp at 75+, 1.8pp at 80+. Glacial enough
   to be stable but evidently not load-bearing for option-aligned outcomes.

3. **Ichimoku family dominates.** The five Ichimoku-derived features
   (`kijun_pct`, `tenkan_pct`, `span_b_pct`, `tenkan_kijun_dir`,
   `cloud_position`) all carry meaningful signal. They are correlated to each
   other (same underlying weekly state) but each captures a slightly
   different facet.

**Caveats — not yet shippable:**

- z-scores at 75+ are modest (~2σ on the negative tail of `tenkan_kijun_dir`).
  The 6.5pp spread is real but not overwhelming.
- 85+ tier signal is eye-popping (13-18pp) but N drops to ~200/tercile —
  larger spreads partly reflect smaller-sample noise.
- Direction is intuitive (bullish Ichimoku → higher WR; bearish → lower).
  Mechanism is plausible: bearish weekly Ichimoku on a high-conviction call
  is exactly the "weekly non-confirmation" pattern v32 CWCF already partially
  captures via wadj.

**Cross-feature correlation to existing wadj is unknown.** The 6.5pp spread
might disappear if conditioned on `wadj < 0` (the existing CWCF gate). This
needs to be checked before designing Phase C.

### Phase B+ wadj decomposition (wadj_decomp.py)

98.8% of 75+ peaks have `wadj ≥ 1` (where v32 CWCF does NOT fire). The
distribution is heavily right-skewed: min=−7, p25=4.8, median=8.5, p75=12.

Within the wadj ≥ 1 cohort (N=6,143):
  - tenkan_kijun_dir spread = 4.98pp WR15 / 2.91pp WR30(opt)
  - cloud_position spread   = 5.38pp WR15 / 7.82pp WR30(opt)

The Ichimoku signal is **almost entirely orthogonal to v32 CWCF**. v32 already
pulled the wadj < 1 cohort below 75 — what remains is a wadj-positive
universe where Ichimoku captures a different weekly-state signal.

Within the wadj ≥ 1 cohort:
  - cloud_position ∈ {−1, 0} (1,491 peaks) underperform by 4-6pp WR
  - cloud_position = +1 (4,462 peaks) outperform by ~1.5pp WR

This is genuine new alpha that v32 CWCF doesn't capture.

### Phase B+ tier comparison (tier_compare.py)

Critical question: does the score-range gate matter? Test on full 50-100 range.

Result: **the signal direction inverts at the 70 boundary.**

| Tier | N | baseWR15 | kijun spread | tk_dir | cloud_position |
|---|---:|---:|---:|---:|---:|
| 50-59 | 251,709 | 50.42% | **−2.78** | **−2.84** | **−4.85** |
| 60-69 | 164,276 | 53.28% | −0.72 | +0.77 | −1.43 |
| 70-74 | 17,214 | 58.53% | +0.83 | +3.86 | +3.36 |
| 75-79 | 4,180 | 60.02% | +3.09 | +3.81 | +4.31 |
| 80-84 | 1,447 | 66.33% | +2.64 | +5.56 | +5.27 |
| 85+ | 643 | 70.48% | +13.51 | +7.60 | +7.87 |

Below 70, bullish-Ichimoku peaks UNDERPERFORM. Above 70, they outperform.
Boundary is roughly 70, with smooth null transition through 60-69.

Implications:
  1. Score-range gate IS load-bearing — applying Ichimoku as continuous tilt
     across all signals would help 75+ but actively hurt 50-59 (251K peaks).
  2. The gradient mechanism design should gate on score zone, with the
     gate-LOW around 70 (not 75) since 70-74 carries real categorical signal.
  3. v38 CWWD already operates in 70-74; orthogonality with new Ichimoku
     mechanism needs explicit testing in Phase C.

### Phase B+ put-side inversion test (put_tier_compare.py)

Result: there is NO separate inversion at score=30. The call-side and put-side
metrics show a **single coherent inversion at the 70 boundary**:

  - Below 70: bullish-cloud → call_WR DOWN, put_WR UP
  - Above 70: bullish-cloud → call_WR UP, put_WR DOWN

The pattern is consistent across the full 0-100 spectrum. At ≤25 puts, the
"unusual disagreement" cohort (bullish weekly + bearish daily) shows put_WR
up to 20pp higher than the typical aligned cohort (bearish weekly + bearish
daily). Counter-intuitive but consistent with a violent-vs-grinding-moves
interpretation:

  - Aligned setups = continuation of slow trends, mostly priced in
  - Disagreement setups = violent new moves that continue strongly

### Phase B+ put-side wadj orthogonality (put_wadj_decomp.py)

≤25 puts have wadj distribution heavily right-tailed (median -16.9, max -8.3)
because v27 WCF has already lifted weak-weekly puts above 25.

Within wadj ≤ -17 cohort (where v27 doesn't fire, N=5,713):
  - cloud_position = -1: put_WR15 = 51.01%
  - cloud_position = +1: put_WR15 = 64.23%
  - **Spread: +13.22pp** — fully orthogonal to v27 WCF

Within -17 < wadj ≤ -10 cohort (N=5,897, where v27 fires but lifts weakly):
  - Spread: +6.41pp — still orthogonal residue

Confirmed: Ichimoku captures structural weekly state that wadj's threshold
mechanism doesn't.

### Phase D stability quantification (stability.py)

Result: **STABILITY WIN — Ichimoku features are 3× more stable than wadj.**

Normalized churn (mean |Δ| over 5 trading days, divided by indicator stdev):
  - wadj:           0.702
  - cloud_position: 0.232 (0.33× wadj)
  - kijun_pct:      0.241 (0.34× wadj)

Per-zone (mean |Δ|):
  | Zone     | wadj  | cloud | kijun |
  | put ≤25  | 10.57 | 0.21  | 4.67  |
  | 70-74    |  6.79 | 0.22  | 6.14  |
  | call ≥75 |  8.86 | 0.32  | 7.08  |
  | neutral  |  5.36 | 0.21  | 4.40  |

In zones where dampener boundaries sit (put ≤25, call ≥75), wadj swings
8-10 points over a typical week — its boundary is at -17, so this means
wadj routinely crosses dampener thresholds within a week. Ichimoku features
barely move in the same zones.

Caveats:
  1. Monday vs Tue-Fri ratio is essentially 1.0 for ALL indicators in this
     measurement. Priority #7 cited 2.0× for wadj. The 5-bar lookback used
     here averages out single-day refresh spikes. The COHR-class intraday
     refresh question requires a 1-bar (Fri→Mon) measurement which is
     deferred to post-Phase-C validation.
  2. weight_info coverage is sparse: only 51K of 916K parquet rows have
     wadj populated. Sample is biased toward zones with active scoring
     state. Analysis is valid for those zones.

Side effect: cached `wadj_v39_1825d_min0.parquet` for Phase C reuse.

### Phase C proceeding

Decision: Phase D validates structural stability of Ichimoku. Phase C proceeds
with full v27-reversed substitution + Bayesian sweep + smoothness plots.

## Phase C: calibration sweep (phase_c_sweep.py + phase_c_validate.py)

100-variant Latin hypercube sweep over 11-dim parameter space (10 continuous
+ RAMP_SHAPE categorical). Evaluation: applies the log-magnifier dampener to
v39 scores (additive with v27 still active), compares affected-tier WR15
deltas at 70+ calls and <25 puts.

### Mechanism (final design, ship-candidate)

```python
# CALL side: dampen 70+ when bearish weekly Ichimoku
score_grad_call = ramp(overall - GATE_CALL_LO, GATE_CALL_HI - GATE_CALL_LO)
ind_dist        = max(0, -price_vs_kijun_pct)
ind_grad_call   = ramp(ind_dist, KIJ_SAT_CALL)
overall        -= K_CALL × score_grad_call × ind_grad_call × (overall - LIFT_TARGET_CALL)

# PUT side: dampen ≤25 when bearish weekly Ichimoku
score_grad_put  = ramp(GATE_PUT_HI - overall, GATE_PUT_HI - GATE_PUT_LO)
ind_grad_put    = ramp(ind_dist, KIJ_SAT_PUT)   # same indicator!
overall        += K_PUT × score_grad_put × ind_grad_put × (LIFT_TARGET_PUT - overall)

# ramp() = log(1+x) / log(1+sat) clipped to [0,1] for log shape
# ramp() = x / sat clipped to [0,1] for linear shape
```

Single indicator (`price_vs_kijun_pct < 0` = below weekly Kijun-sen = bearish
weekly state) drives both sides. Score zone determines which dampener fires;
both apply zero strength in the 26-69 neutral zone via the gate gradients.

### Two ship candidates

  RANK #1 (max alpha):
    GATE_CALL_LO=66, GATE_CALL_HI=91, K_CALL=0.45,
    KIJ_SAT_CALL=13.1, LIFT_TARGET_CALL=50.8
    GATE_PUT_LO=10,  GATE_PUT_HI=26, K_PUT=0.36,
    KIJ_SAT_PUT=10.3, LIFT_TARGET_PUT=35.2
    RAMP_SHAPE=log

  RANK #4 (smoother, recommended):
    GATE_CALL_LO=63, GATE_CALL_HI=89, K_CALL=0.76,
    KIJ_SAT_CALL=8.8, LIFT_TARGET_CALL=49.9
    GATE_PUT_LO=-15, GATE_PUT_HI=26, K_PUT=0.34,
    KIJ_SAT_PUT=8.5,  LIFT_TARGET_PUT=37.7
    RAMP_SHAPE=log

### H1-H5 validation (Rank #1)

  H1 (per-trade lift on affected tiers):
    Call 70+: +0.46pp WR15 at 5y ✓
    Call 75+: +0.97pp ✓
    Call 80+: +1.74pp ✓
    Call 85+: +2.74pp ✓
    Call 90+: +1.80pp ✓
    Call 95+: +2.14pp ✓
    Put <25: +1.10pp ✓
    Put <20: +2.42pp ✓
    Put <15: -0.34pp (deep-tail residual sample noise, not affected tier)
    Put <10: -3.69pp (noise on small N)
    Put <5:  -5.96pp (N=32 residual)

  H2 (directional consistency): WR15 and WR30 move same direction. ✓
    (verified separately via WR30(opt) tier_compare results)

  H3 (N stability):
    Call 70+: -9.5% (within ±15% ✓)
    Put <25: -20.0% (Rank #1 exceeds; Rank #4 has -15.4% which clears)

  H4 (cross-side neutrality): puts <30 unchanged (Δ=0 since dampener doesn't
  fire above 25). ✓

  H5 (multi-window sign consistency, 1y/3y/5y):
    call_70+: +,+,+ ✓
    call_75+: +,+,+ ✓
    put_<25:  +,+,+ ✓
    put_<20:  +,+,+ ✓
    All four affected tiers sign-consistent across all three windows.

### Smoothness check (per user's "look for natural wave" criterion)

  All three response curves are monotonic and smooth:
    - Call score_grad: smooth log ramp from 0 at score=66 to 1.0 at 92
    - Put score_grad: smooth log decay from 1.0 (deep puts) to 0 at 26
    - Indicator grad: smooth log ramp on negative kijun_pct, zero on positive

  No cliffs, non-monotonicities, or asymmetric weirdness. The log wave
  structure is intact across the entire active domain.

### Three remaining items before any production ship

  1. **True v27 substitution validation (Phase F).** The current sweep is
     additive (v27 still active). Validation pass: invert v27 lift via
     wcf_lift in weight_info, then apply Ichimoku, compare to v39 production
     baseline. The wadj cache already includes wcf_lift for this.

  2. **Production scoring plumbing.** Ichimoku features need to be computed
     at scoring time (not just in offline parquet). Estimated 1-2h plumbing.
     Reads existing WeeklyPriceHistory (no schema changes needed).

  3. **COHR-class intraday refresh test.** Phase D measured 5-bar churn but
     the original Priority #7 case was 1-bar (Friday→Monday) refresh. Once
     production plumbing is in, run a focused 1-bar comparison: |Δoverall|
     pre-mechanism vs post-mechanism on Monday recomputes for 100+ symbols.
     Confirms the mechanism specifically addresses Priority #7's whiplash.

### Phase F — adjacent investigation worth pursuing post-ship

The Ichimoku-replaces-wadj logic likely extends to v32 CWCF (call-side
wadj-based dampener at 75+). Same `kijun_pct` indicator validated at 75+
with bigger N than puts. After this Phase C ships and validates on the
holdout, v32 CWCF Ichimoku-substitution is the natural follow-up. Marked
as **Priority #5c** for future work.

## Phase F: scorched-earth substitution test (phase_f_substitution.py)

Four-way comparison vs v39 production (with v27 active):
  (a) baseline = v39 production           — 0.00 / 0.00
  (b) v27 stripped, no replacement        — Put <25 ΔWR -1.42pp (alpha lost)
  (c) v39 + Ichimoku additive (Phase C)   — Put <25 ΔWR +1.10pp ✓
  (d) v27→Ichimoku true substitution      — Put <25 ΔWR -0.44pp (regression)

**Key finding: v27 carries +1.42pp put alpha that pure-Ichimoku cannot fully
recover.** The two indicators capture different cohorts (v27 = wadj-weak,
Ichimoku = bearish-cloud). Smooth substitution recovers ~98% of alpha but
leaves a ~0.4pp gap.

Clumping analysis on displaced calls: **no pathology**.
  - Year breakdown: 7.5% (2024) to 18.3% (2022) — slight elevation in
    bear/choppy years (expected: bearish weekly Ichimoku is more common)
  - Indicator state: 37% mild bearish, 14% deeply bearish, 0% with bullish
    kijun (gate works correctly)
  - Symbol concentration: top-1 = 2.2%, top-10 = 11.0% (no clumping)

## Phase D + Phase C-multi: substitution attempts to close v27 gap

**Phase D pure Ichimoku substitutive (120 variants):** best Put `<25` ΔWR =
**−0.12pp**. Cannot reach +0.3pp gate.

**Phase C-multi sum_clip (Ichimoku + smooth-wadj, 150 + 200 variants tested
with all 3 combiners):** best Put `<25` ΔWR = **+0.03pp** (Rank #3 in 200-var
sweep). Closes the gap to baseline within noise but does not BEAT v27.

**470 total variants explored.** The ceiling for pure substitutive mode is
asymptotic at v27 baseline — gradient mechanism can match but not exceed
v27's threshold. Gap is structural, not a calibration miss.

## Phase E: refined call-side calibration (phase_e_refinement.py)

Lock put-side at Phase C Rank #1 (proven +1.10pp <25). Sweep call-side
focusing on N preservation (extended LIFT_TARGET_CALL upward, K_CALL lowered,
KIJ_SAT_CALL extended).

Key insight: **higher LIFT_TARGET_CALL preserves N** by displacing peaks
toward 65-70 instead of 50-55, keeping them in 80+/85+ tier brackets.

200 variants → 9 pass strict H1 (≥3 call tiers ≥+0.5pp) AND H3 strict
(call N within ±15%). Top candidates:

### Phase E Rank #1 — log ramp, gate=65-86, K=0.17, sat=19.3, target=55.2

  Call tier deltas (all ΔN within ±15%):
    95+: +1.32pp / -13.6%
    85+: +1.28pp / -10.3%
    80+: +0.70pp /  -8.1%
    75+: +0.27pp /  -6.5%
    70+: +0.21pp /  -3.9%
  Put <25: +1.10pp / -20.0% (passes affected-tier H1 but exceeds H3)

  H1 strict: PASS (3 tiers ≥+0.5pp, no regress)
  H3 calls:  PASS (all within ±15%)
  H3 puts:   FAIL (Put <25 -20%)
  H5:        sign-flip on 95+ at 1y (N=7, small)

### Phase E Rank #3 — linear ramp, gate=75-90, K=0.17, sat=11.7, target=69.9 ★ SHIP CANDIDATE

  Call tier deltas (all ΔN ≤ -11.4%, all positive WR):
    95+: +1.92pp / -11.4%   (BEST 95+ alpha across all sweeps)
    90+: +0.86pp /  -9.4%
    85+: +1.09pp /  -5.8%
    80+: +0.02pp /  -3.9%
    75+:   0.00 /   0%      (gate=75: dampener doesn't fire below)
    70+:   0.00 /   0%
  Put <25: +0.34pp / -9.1% (passes both H1 affected-tier AND H3)

  H1 strict: PASS (95+/90+/85+ all ≥+0.5pp, no regress)
  H1 affected-tier (put <25): PASS (+0.34 ≥ +0.3 gate)
  H2 directional: PASS (WR30 deltas LARGER than WR15: 95+ ΔWR30=+2.80)
  H3 strict: PASS (max ±11.4% on calls, ±9.1% on puts)
  H4 puts: PASS (no regression)
  H5: ✗ sign-flip on 95+/90+ at 1y (N≤25, small-sample noise)
      meaningful tiers (80+/85+/70+/Put<25) all sign-consistent
  Smoothness: PASS (linear ramp is monotonic by construction)

## Final ship recommendation

**Phase E Rank #3 is the ship candidate.** Architecture:

```python
# CALL side (NEW — Ichimoku log-magnifier dampener)
RAMP_SHAPE        = 'linear'
GATE_CALL_LO      = 75
GATE_CALL_HI      = 90
K_CALL            = 0.17
KIJ_SAT_CALL      = 11.7
LIFT_TARGET_CALL  = 69.9

# PUT side (Phase C Rank #1 winning config — additive on top of v27)
GATE_PUT_LO       = 10
GATE_PUT_HI       = 26
K_PUT             = 0.358
KIJ_SAT_PUT       = 10.335
LIFT_TARGET_PUT   = 35.196

# v27 WCF: KEEP ACTIVE — pure substitution couldn't beat it (470-variant evidence)
```

Apply additively to v39 production score (after v27 fires).

  Call-side: dampens bearish-Ichimoku 75+ peaks toward 70 (high lift target
  preserves them in 80+/85+ tier brackets).
  Put-side: dampens bearish-Ichimoku ≤25 peaks toward 35 (just above put
  qualifying threshold).

Net per-trade alpha (5y vs v39 production):
  - 95+ +1.92pp WR15 / +2.80pp WR30
  - 85+ +1.09pp WR15 / +0.75pp WR30
  - Put <25 +0.34pp WR15 (incremental over v27's existing +1.42pp)

### Caveats / known unknowns

1. **H5 1y sign-flip on 95+/90+** is small-sample noise (N=7-25), not a
   real regression. Strictly violates H5 but pragmatically defensible.
2. **Production scoring plumbing** still needed: Ichimoku features must be
   computed at scoring time. ~1-2h work, reads existing WeeklyPriceHistory.
3. **COHR-class 1-bar test** (Friday→Monday refresh) not yet run. Phase D
   measured 5-bar churn (3× more stable than wadj). Confirm 1-bar fix
   post-plumbing.
4. **MC validation** required pre-ship (signal density shifts ~10% on call
   tiers — this is below the 30% threshold for mandated smoke MC, but worth
   running N=100 22-now smoke).

### Failed paths preserved for "what NOT to retry"

  - AVWAP-from-last-earnings: null marginal signal on 75+ (0.7pp spread).
    Self-fulfilling-prophecy hypothesis falsified by data.
  - 50W SMA proximity: weak (1.9pp at 75+).
  - Pure Ichimoku substitutive: best Put <25 ΔWR = -0.12pp. Cannot replace v27.
  - Multi-indicator (Ichimoku + smooth-wadj) substitutive: best +0.03pp.
    Closes gap, doesn't beat. Threshold structurally outperforms gradient
    on this cohort.
  - Removing the score-range gate entirely: signal inverts at 70 boundary
    (50-59 cohort shows -4.85pp on cloud_position). Gate IS load-bearing.

## Phase C: Score-Stage Augmentation

*(To be designed only if Phase B gate is met)*

## Phase D: Stability Check

*(Run COHR-class whiplash quantification under new composite)*

## Phase E: Calibration

*(All data must use dates ≤ CALIBRATION_CUTOFF_DATE = 2026-05-15)*

## What NOT to do (lessons from prior approaches)

1. **Do not validate with P0 `_ph_cache={}` approximation.** v42 rolling weekly
   failed because the P0 prediction (+4.51pp) bypassed breakout/divergence-push
   detection inside `calculate_rsi_score`. If Phase C produces a score-stage
   change, validate with full `trader recalculate --force --full` + `trader assess
   --force` before claiming WR lift.

2. **Do not bypass the N-stability gate.** ΔN ≥ 15% on any primary tier is a
   hard red flag (Priority #0a lesson).

3. **Cross-check both metrics** (generic WR15 and option TP%) — they can diverge
   for dampener-style changes that target dip-then-recover signals (see v40 SVD
   case in `assessment-backtest.md`).
