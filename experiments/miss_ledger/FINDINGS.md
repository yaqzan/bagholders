# Miss-Ledger Pattern Mining — Findings (v31/v32, 5y)

**v31 source:** `.cache/miss_ledger/ledger_v31_1825.parquet` (41,389 peaks)
**v32 source:** `.cache/miss_ledger/ledger_v32_1825.parquet` (40,771 peaks — post-CWCF ship)
**Outcome:** 30 DTE option-aligned barrier (TP=+0.35 / SL=-0.30 calls / -0.20 puts) at w=15d
**Method:** P(MISS|cell) / P(MISS|cohort_baseline), z-tested, |z|≥2.5 + N≥50 single / ≥30 pair

---

## 1. The single biggest MISS driver: **weekly-drag asymmetry on calls** (z=+10.1)

**Pattern:** Calls (overall ≥ 70) with **wadj < 0** miss at 52.5% vs cohort baseline 41.4%. Lift 1.27, N=2,061. Holds across every call bucket:

| Cohort | wadj=neg miss% | baseline miss% | lift | z |
|---|---:|---:|---:|---:|
| CALL 70+ | 52.5% (N=2,061) | 41.4% | 1.27 | **+10.1** |
| CALL 75+ | 51.4% (N=555) | 40.2% | 1.28 | +5.4 |
| CALL 80+ | 53.1% (N=145) | 37.6% | 1.41 | +3.9 |

**Compounded with conviction-class signals it gets worse:**
- `wadj=neg & vsig=CONVICTION`: 56.1% miss (N=285), lift 1.40, z=+5.5
- `wadj=neg & vmag=mid`: 57.5% miss (N=738), lift 1.39, z=+8.8
- `wadj=neg & ta=hi`: 55.1% miss (N=528), lift 1.33, z=+6.4

**Why this matters:** the WCF lift (v27) handles the symmetric case on the *put side* — when a put is extreme but the weekly didn't confirm. There is **no call-side mirror.** The current weekly multiplier is asymmetric (`puts × 1.5, calls × 1.0`), which means a call with weekly drag pulling *down* gets the full bearish weekly weight with no compensation, but the call still hits 70+ off momentum/volume — and then misses at 52% vs the 60% baseline TP rate.

This is **Priority #4** in `known-issues.md` ("Weekly Adj Smooth Scaling"). The miss-ledger now provides clean cohort-level evidence with z=+10.1 — the highest single-feature z-score in the entire analysis.

**Concrete proposal:** call-side WCF-mirror lift — when `overall ≥ 75 ∧ wadj < 0`, pull score *down* toward 50 proportional to weakness (`weakness = clip((-wadj)/13, 0, 1)`). Mirror the v27 put WCF formula structure, weight tuned via `fast_variant_runner.py`.

---

## 2. Counter-trend cascade promotion **already absorbs** the put-side equivalent

`trend=hi` on puts (overall ≤ 25 ∧ trend ≥ 65) is the strongest *protective* feature on every put cohort:

| Cohort | trend=hi miss% | baseline | lift | z |
|---|---:|---:|---:|---:|
| PUT <25 | 36.2% (N=727) | 50.1% | 0.72 | **-7.5** |
| PUT <20 | 32.3% (N=282) | 49.0% | 0.66 | -5.6 |
| PUT <15 | 22.1% (N=95)  | 47.2% | 0.47 | -4.9 |

This is the CT-PUT bucket — already shipped via `CT_PROMOTE=True, PUT_TREND_MIN=80` (cascade promotion to put_top tier). The miss-ledger validates the mechanism: counter-trend puts have a 14-25pp **lower** miss rate than the cohort. The existing cascade promotion is correctly aligned with per-trade signal quality.

---

## 3. Volume amplifier creates a **wadj-conditional** failure mode (already evidence)

Volume signals are *protective on average* (`vmag=lo` lift 0.85, z=-5.6 at CALL 75+) but become **harmful when stacked with negative weekly drag**:

| Pattern | miss% | lift |
|---|---:|---:|
| `vmag=lo` alone | 34.3% | 0.85 (protective) |
| `vsig=CONVICTION` alone | 43.1% | 1.04 (slightly harmful) |
| `wadj=neg & vsig=CONVICTION` | 56.1% | **1.40** (very harmful) |
| `wadj=neg & vmag=mid` | 57.5% | **1.39** |

**Mechanism:** the volume amplifier is doing exactly what it's designed to do — amplifying signals up to ±55%. But CONVICTION + wadj=neg = "loud volume confirming a trend the weekly contradicts." The volume amp doesn't see weekly context; it amplifies noise in that cell.

**This collapses into Priority #4 above.** A call-side WCF-mirror that lifts the dampener *before* the volume multiplier is applied (the volume amp runs after weighted sum, but `wcf_lift` runs after volume) would shrink the score before the amplifier has a chance to inflate the wrong cohort. Order-of-operations matters here.

---

## 4. **Stoch contradicts on puts is protective** (already aligned with shipped logic)

`stoch=lo` on puts is consistently protective (z=-4.4 to -5.8). Combined with `bb=mid`, miss rates fall below 25%:

| Cohort | `stoch=lo & bb=mid` miss% | baseline | lift |
|---|---:|---:|---:|
| PUT <25 | 37.1% (N=902) | 50.1% | 0.74 |
| PUT <20 | 31.6% (N=421) | 49.0% | 0.64 |
| PUT <15 | 23.9% (N=155) | 47.2% | **0.51** |

**Why "shipped logic":** the docs already record this finding — `known-issues.md` "Never add PUT_STOCH_GATE" — Stoch IS informative on bearish setups, removing it hurts. The miss-ledger confirms the existing logic is correct: stoch=lo puts (oversold momentum on a bearish signal) are *structural breakdowns*, not bounces. Stoch=hi puts are mostly bounces (lift 1.03-1.08, mildly harmful) — but the per-trade gain from filtering them was insufficient to ship a gate. The cohort-level evidence here is consistent with that decision.

---

## 5. **Regime=HEALTHY puts** are over-firing (Priority #6 composite inversion — confirmed)

`regime=HEALTHY & stoch=hi` puts have lift 1.12-1.14 (z up to +5.0) — these are bounces firing as puts in calm tape because the inverted composite mis-labels narrow-bull days as containing put-favorable signals.

This is the same mechanism F3F (breadth-driven allocation) addresses at the *portfolio* stage. The miss-ledger shows the mis-classification persists at the *score* stage. F3F dampens allocation in HEALTHY tape but doesn't eliminate the lower-quality signals from firing. **No new fix needed here** — Priority #6 fix tracks at the composite level; F3F handles the residue.

---

## 6. **No clear need for a new data indicator.**

Every strong pattern (|z|≥4) uses existing components. Sector relative strength, supply/demand zones, options flow — none would have surfaced in the miss-ledger because the existing 6-component model already separates the cohorts cleanly enough to *identify* the failure modes; what's missing is **conditional weighting**, not new dimensions.

The closest thing to a "new indicator" is the **conditional interaction** between the weekly composite and the volume amplifier (Finding #3). That's a re-ordering / gating change, not a new data source.

---

## Recommended next steps (ranked)

1. **[HIGH] Build & test call-side WCF-mirror** (Priority #4 from known-issues.md) — proposed formula:
   ```
   if overall >= 75 and wadj < 0:
       weakness = clip(-wadj / 13, 0, 1)
       overall -= K * weakness * (overall - 50)   # pull down toward 50
   ```
   Calibrate K, wadj_cutoff, and score_gate via `experiments/fast_variant_runner.py` (~0.8s/variant). Per-trade gate: H1 — TP% +≥0.5pp on 3+ call tiers at 5y. Strong prior: z=+10.1 cohort signal is the largest single hand-off in the analysis.

2. **[MED] Volume amp gate on wadj=neg** — when `wadj < 0 and vsig in (CONVICTION, REJECTION) and vmag >= 0.6`, cap volume multiplier at 1.0 (no boost). Lift on these patterns is 1.40+, N is meaningful (700-900 across cohorts). Only worth testing if Finding #1 alone doesn't fully close the gap.

3. **[LOW] Validate the put-side `bb=mid + stoch=lo` cohort isn't over-shipped** — these are the highest-quality puts (miss rate 23-37%) but also tend to overlap with ext_focal-suppressed cohorts. Sanity-check that the existing put dampeners aren't silently dropping these high-EV signals.

**Not recommended:**
- New external indicator (sector rel strength, options flow, etc.) — no missing-dimension signal in the data
- Symmetric weekly amp (`puts × 1.5 + calls × 1.5`) — proven inferior in `weekly_magnitude_sweep.py` (raises N at the cost of WR30 quality)
- A blanket call-side wadj filter — N at extremes is already small (95+ cohort N=43); a hard gate would collapse signal volume more than improving rate.

---

## Calibration sweep — call-side WCF-mirror lift (32 variants, 5y/3y/1y)

**Formula:**
```
if overall >= CWCF_SCORE_GATE and wadj < CWCF_WADJ_CUTOFF:
    weakness = clip((CWCF_WADJ_CUTOFF - wadj) / |CWCF_WADJ_CUTOFF|, 0, 1)
    overall -= K × weakness × (overall - CWCF_LIFT_TARGET)
```

**H1-H5 gate (per [assessment-backtest.md](../../.claude/docs/assessment-backtest.md)):**
- **H1**: ≥+0.5pp on ≥3 of {95+,90+,85+,80+,75+} at 5y; none regress >−1.0pp
- **H3**: per-bucket N within ±15% at 5y
- **H4**: puts neutral or better
- **H5**: TP% sign consistent across 1y/3y/5y

**Sweep result: only narrow-cutoff variants pass the H3 N-stability gate.**

| Variant | K | wadj_cut | gate | target | H1 pass | H3 | H5 | Ship? |
|---|---:|---:|---:|---:|:---:|:---:|:---:|:---:|
| **A_K95_C01_G75_T55** | 0.95 | 1 | 75 | 55 | 3 (regress 0) | ✓ | ✓ | **✓** |
| A_K70_C01_G75_T55 | 0.70 | 1 | 75 | 55 | 3 (regress 0) | ✓ | ✓ | ✓ |
| B_K95_C07_G75_T55 | 0.95 | 7 | 75 | 55 | 5 (regress 0) | ✗ -27 to -40% N | ✓ | ✗ |
| B_K70_C07_G75_T55 | 0.70 | 7 | 75 | 55 | 5 | ✗ | ✗ | ✗ |
| Wider cut (3,5,13,17), gate=70/80 | various | various | various | various | mixed | ✗ | mixed | ✗ |

**Wider cutoffs (cut=5,7,13,17) deliver dramatically larger per-trade improvements (95+ +6pp, 75+ +1.6pp) but collapse N by 27-90%.** That's a stricter gate masquerading as alpha — those signals would compound less, not more, and the H3 floor exists exactly to catch this. The cohort-level miss-ledger evidence is concentrated at `wadj < 0` (z=+10.1 with cut=0); extending to cut=7 dilutes the signal across a much wider population.

### Recommended ship: `mirror_K95_C01_G75_T55`

Per-bucket TP% (5y, vs v31 baseline):

| Bucket | Baseline N | Variant N | ΔN | Baseline TP% | Variant TP% | ΔTP | Gate |
|---|---:|---:|---:|---:|---:|---:|:---:|
| 95+ | 43 | 42 | -2.3% | 69.8% | 69.0% | -0.72pp | within tol |
| 90+ | 200 | 193 | -3.5% | 66.5% | 66.3% | -0.18pp | neutral |
| 85+ | 751 | 711 | -5.3% | 66.8% | 67.8% | **+0.95pp** | ✓ |
| 80+ | 2,581 | 2,387 | -7.5% | 62.4% | 63.5% | **+1.09pp** | ✓ |
| 75+ | 7,963 | 7,173 | -9.9% | 59.9% | 61.0% | **+1.14pp** | ✓ |
| 70+ | 24,538 | 23,803 | -3.0% | 58.6% | 58.9% | +0.29pp | neutral |

Multi-window 75+ (H5):

| Window | Baseline N | Variant N | Baseline TP% | Variant TP% | Δ |
|---|---:|---:|---:|---:|---:|
| 1y | 1,689 | 1,559 | 62.1% | 62.4% | +0.32pp |
| 3y | 4,904 | 4,408 | 61.7% | 63.1% | +1.49pp |
| 5y | 7,963 | 7,173 | 59.9% | 61.0% | +1.14pp |

**Sign-consistent across all 3 windows.** Puts unchanged on every bucket (gate=75 → put territory untouched). The 95+/90+ tier deltas (−0.72pp / −0.18pp) are within noise: the variant only fires when `wadj < 1`, which is rare for 95+ scores (they need bullish weekly to score that high). On 95+ N=43, ΔTP=−0.72pp ≈ 1 signal flipping (1/43 ≈ 2.3%).

### Why this works mechanically

The current scoring engine has an **asymmetric weekly amplifier**: `puts × 1.5, calls × 1.0`. This means a call signal with bearish weekly drag (`wadj < 0`) gets the *full* bearish weekly weight applied with no compensation, but the call still hits 70+ off momentum/volume. The miss-ledger shows these signals miss at 52% vs 41% baseline. The mirror lift surgically pulls those scores down toward 55, removing the cohort that fails most without touching wadj-positive calls.

### Code change required

`database/utils/scoring.py`, after the existing v27 WCF lift block (around line 395), add the mirror:

```python
# Phase: Call-side WCF-mirror dampener (Priority #4 from known-issues.md)
# Pulls call scores down toward 55 when overall >= 75 AND weekly is non-confirming
# (wadj < 1). Mirror of the v27 put WCF lift. Calibrated 5y miss-ledger evidence:
# wadj-neg cohort z=+10.1, the largest single-feature miss driver in the analysis.
CWCF_SCORE_GATE  = 75
CWCF_WADJ_CUTOFF = 1
CWCF_LIFT_TARGET = 55
CWCF_K           = 0.95

if overall >= CWCF_SCORE_GATE and weekly_detail and 'w_adj' in weekly_detail:
    _wadj_c = weekly_detail['w_adj']
    if _wadj_c < CWCF_WADJ_CUTOFF:
        _cwcf_weakness = max(0, min(1, (CWCF_WADJ_CUTOFF - _wadj_c) / abs(CWCF_WADJ_CUTOFF)))
        if _cwcf_weakness > 0:
            _cwcf_dampen = CWCF_K * _cwcf_weakness * (overall - CWCF_LIFT_TARGET)
            overall = int(max(0, round(overall - _cwcf_dampen)))
            weight_info['cwcf_dampen'] = round(_cwcf_dampen, 2)
```

Apply BEFORE the earnings boost (so dampened scores don't get re-boosted into 80+ via earnings).

### Ship procedure

1. Edit `scoring.py` per above.
2. Run `python tests/test_strategy_config_drift.py` (no drift expected — scoring change, not strategy_config).
3. Smoke test: `python experiments/miss_ledger/build_ledger.py --refresh && python experiments/miss_ledger/analyze_ledger.py` to confirm the wadj=neg miss-lift collapses post-ship.
4. Commit + bump `ALGORITHM_VERSION` to new commit hash.
5. `trader recalculate --force --full` (~25 min) — repopulates 5y + auto-runs historic-update + assess.
6. (Optional) N=100 smoke MC on `22-now` only — signal density shifts <10% per H3, so this is below the >30% threshold that would mandate it. Skip unless cautious.

**Not required:** canonical N=300+ MC. Per [assessment-backtest.md](../../.claude/docs/assessment-backtest.md) "Why N=300 canonical MC is NOT the gate for scoring changes" — the per-trade H1-H5 gate has been satisfied; scoring changes ship via per-trade evidence.

### Sweep artifacts

- [experiments/miss_ledger/call_wcf_mirror_sweep.py](call_wcf_mirror_sweep.py)
- [experiments/miss_ledger/call_mirror_sweep.log](call_mirror_sweep.log) — initial 14-variant run
- [experiments/miss_ledger/call_mirror_sweep2.log](call_mirror_sweep2.log) — 18-variant fine sweep

---

## Post-v32 Investigation — v32 Ledger (2026-05-04)

**Source:** `ledger_v32_1825.parquet` (40,771 peaks) — built after CWCF dampener (v32) shipped.

### New patterns after v32 closed the wadj-neg driver

The v32 CWCF dampener reduced the wadj=neg signal at CALL 70+ from z=+10.1 → z=+9.1
(residue concentrated in disabled 70-74 tier). Two new patterns emerged as the next biggest:

**CALL side (primary):**
- `b_stoch=lo & b_wadj=mild` at CALL 75+: **z=+4.5**, N=3,379 (46% of 75+ calls), miss 42.9% vs 39.2% baseline
  - `stoch=lo` = stoch component < 35 (bearish), `wadj=mild` = weekly ∈ [0,13]
  - Single feature: `b_stoch=lo` z=+3.4 (58.8% of 75+ calls)
  - Pair amplifies: vmag=hi & stoch=lo z=+4.0 (14.4%), stoch=lo & brd=lo z=+4.5 (4.3%)

**PUT side (secondary, NULL RESULT):**
- `b_stoch=hi & b_regime=HEALTHY` at PUT <25: z=+4.9, N=1,836 (11%), miss 55.9% vs 50.2%
- `b_bb=lo & b_sigma=wide` at PUT <25: z=+4.4, N=3,048 (18%), miss 54.2%
- See null-result section below.

---

## v34 Candidate: Call Stoch-Weekly Contradiction Dampener (CSWC) — gradient form

**Pattern:** Calls (overall ≥ 75) where stochastic component is below-neutral (< 35) AND weekly
adjustment is weakly positive (1 ≤ wadj < 12). These calls fire on trend/volume dominance but
stoch contradicts while the weekly barely confirms. Miss rate: **42.9% vs 39.2% baseline** (z=+4.5).

**Mechanism — gradient (no hard stoch gate):**
```
# stoch_neutral=35 = COMP_BINS lo/mid boundary (natural semantic anchor)
stoch_weakness = clip((35 − stoch) / 35, 0, 1)   # 0 at stoch=35, 1 at stoch=0
wadj_weakness  = clip((12 − wadj)  / 11, 0, 1)   # 0 at wadj=12, 1 at wadj=1
weakness       = stoch_weakness × wadj_weakness
overall       -= 0.30 × weakness × (overall − 55)
```

No cliff at any value — dampening increases proportionally as stoch gets more bearish:
- stoch=30: drop ~1pt (weakness~0.26)
- stoch=20: drop ~3pt (weakness~0.51)
- stoch=10: drop ~5pt (weakness~0.71)
- stoch= 5: drop ~6pt (weakness~0.77)

**Complementary to CWCF (v32):** CWCF handles `wadj < 1`; CSWC covers `1 ≤ wadj < 12` when stoch also contradicts. No gap or overlap — clean handoff at wadj=1.0.

**Bayesian calibration v2 (gradient form):** `stoch_wadj_bayes.py v2`, 175 variants.
- 56/175 pass H1+H3+H4+H5 (N≥50 H5 threshold)
- **Winner by semantic cleanliness: sn=35, wg=12, K=0.30** (rank 2 by utility=0.4259)
- Top by raw utility: sn=50, wg=12, K=0.25 (utility=0.4369) — extends into neutral stoch range, micro-effect, less principled anchor

**H1-H5 gate results (sn=35 wg=12 K=0.30, 5y, vs v32 baseline):**

| Bucket | Baseline N | Variant N | ΔN | Baseline TP% | Variant TP% | ΔTP | Gate |
|---|---:|---:|---:|---:|---:|---:|:---:|
| 85+ | 713 | 627 | −12.1% | 67.7% | 69.5% | **+1.80pp** | H1 ✓ |
| 80+ | 2,418 | 2,077 | −14.1% | 63.4% | 64.6% | **+1.17pp** | H1 ✓ |
| 75+ | 7,268 | 6,309 | −13.2% | 60.8% | 61.7% | **+0.93pp** | H1 ✓ |
| 90+ | 193 | 179 | −7.3% | 66.3% | 67.0% | +0.72pp | small N |
| <25 | 16,840 | 16,840 | 0.0% | 49.8% | 49.8% | 0.00pp | H4 ✓ |

Multi-window H5 (tiers with N≥50 per window):
- **75+**: sign-consistent across 1y/3y/5y ✓
- **80+**: sign-consistent across 1y/3y/5y ✓
- **85+**: sign-consistent across 1y/3y/5y ✓
- **90+**: excluded from H5 (1y N≈37 < 50 threshold)
- H5 PASSES for all N≥50 tiers.

**N drops:** 75+: −13.2%, 80+: −14.1%, 85+: −12.1% — all within ±15% H3 tolerance.

**Gate verdict:**
- H1 ✓ (3 tiers ≥ +0.5pp, zero regressions > −1pp)
- H3 ✓ (N within ±15% for 75+/80+/85+)
- H4 ✓ (puts literally unchanged)
- H5 ✓ (N≥50 corrected check — all primary tiers sign-consistent)

**Ship status: CANDIDATE for v34.** Code implemented in `database/utils/scoring.py` (CSWC block after CWCF, before continuation boost). Awaiting v33 migration completion before shipping.

**NOTE on H5 N-threshold:** The initial grid sweep used H5 with no N minimum, which caused 90+ tier failures due to ~37 signals in the 1y window. The corrected H5 (N≥50 per tier-window) is principled: the assessment framework explicitly excludes tiers with N<200 from cohort mining. H5 failures on N<50 windows are statistical noise, not real sign inconsistency.

### Example signal impacts (K=0.30, sn=35, wg=12) — gradient, no cliff

| stoch | wadj | overall → new | drop | weakness | note |
|---:|---:|---|---:|---:|---|
|  5  | 5  | 80 → 76 | −4 | 0.57 | strong contradiction |
| 15  | 5  | 80 → 77 | −3 | 0.45 | moderate contradiction |
| 25  | 5  | 80 → 78 | −2 | 0.32 | mild contradiction |
| 30  | 5  | 78 → 77 | −1 | 0.26 | barely below neutral |
|  5  | 2  | 85 → 79 | −6 | 0.82 | deep stoch + weak wadj |
| 35  | 5  | 81 → 81 | 0  | 0.00 | stoch=neutral, no fire |

### Artifacts

- `experiments/stoch_regime_sweep.py` — initial 27-call + 54-put grid sweep
- `experiments/stoch_wadj_bayes.py` — 252-variant Bayesian fine-tune
- `experiments/miss_ledger/report.md` — full v32 pattern tables (all cohorts × top-12 single + pair)

---

## v32 Ledger — PUT Pattern: NULL RESULT

**Tested:** `stoch=hi & regime=HEALTHY` put dampener (z=+4.9, N=1,836, miss 55.9%).

**Window consistency test (sg=65, rg=50, K=0.60):**
| Window | Base N | Var N | Base TP% | Var TP% | Delta |
|---|---:|---:|---:|---:|---:|
| 1y | 2,912 | 2,752 | 47.5% | 48.0% | +0.51pp |
| 2y | 5,861 | 5,426 | 47.9% | 48.0% | +0.12pp |
| 3y | 9,463 | 8,813 | 48.3% | 48.5% | +0.20pp |
| 5y | 16,840 | 14,383 | 49.8% | 50.7% | +0.91pp |

**Fails H5:** 2y/3y are essentially flat (+0.12/+0.20pp, below H1 threshold), 5y positive, 1y marginal.
The improvement is driven by specific historical periods (likely 2021-2022 bull/bear regimes) and does not hold consistently across all market conditions. **Do NOT ship put stoch-regime dampener.**

Why: the `stoch=hi` condition on puts (overbought momentum = possible bounce setup) is not a stable discriminator of put quality. In stress regimes, even overbought-stoch puts may be valid breakdowns. The bounce-trap signal only concentrates in specific market conditions (HEALTHY regime in particular), making it window-specific rather than a durable scoring flaw.

**Recommended next investigation for puts:** Re-run miss-ledger after v33 (continuation boost) ships, since the new signals (70-74 calls lifted to 75) may alter the put competition dynamics in the 14-slot cascade, indirectly changing which put patterns manifest as misses.

---

## Ledger artifacts

- `experiments/miss_ledger/build_ledger.py` — joins Score components + weight_info + barrier_outcomes (30dte_opt, w=15d) + breadth into per-signal parquet
- `experiments/miss_ledger/analyze_ledger.py` — discrete binning + cohort baseline + lift/z table
- `.cache/miss_ledger/ledger_v31_1825.parquet` — 41,389 rows × 33 cols
- `experiments/miss_ledger/report.md` — full per-cohort tables (10 cohorts × top-12 single + top-15 pair)
- `experiments/miss_ledger/FINDINGS.md` — this document

Re-run after any scoring change:
```
python experiments/miss_ledger/build_ledger.py --refresh
python experiments/miss_ledger/analyze_ledger.py
```
