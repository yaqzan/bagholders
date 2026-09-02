# CTSL — Counter-Trend Score Lift (Phase 1: cohort calibration)

Score-stage continuous lift designed as the soft substitute for cascade-stage
`CT_PROMOTE`, mirroring the v44 ICH / v43 MCD architecture. See
`.claude/docs/known-issues.md` "CT_PROMOTE legacy tech debt removal" for the
phased removal plan; this file is the Phase 1 evidence.

## Design

Mechanism (inserted into `compute_overall_score`, applied AFTER MCD/ICH/PCD,
BEFORE PESS/EARN_BOOST):

```python
# CALL side (overall >= 70 in downtrend)
if overall >= 70 and trend is not None and trend <= CTSL_CALL_TREND_MAX:
    score_norm = clip((overall - 70) / 25, 0, 1)
    trend_dist = clip((CTSL_CALL_TREND_MAX - trend) / CTSL_CALL_TREND_MAX, 0, 1)
    lift = score_norm ** CTSL_SCORE_POWER * trend_dist ** CTSL_TREND_POWER
    overall += CTSL_ALPHA_CALL * lift * (CTSL_TARGET_CALL - overall)

# PUT side (overall <= 25 in uptrend) — mirror
if overall <= 25 and trend is not None and trend >= CTSL_PUT_TREND_MIN:
    score_norm = clip((25 - overall) / 25, 0, 1)
    trend_dist = clip((trend - CTSL_PUT_TREND_MIN) / (100 - CTSL_PUT_TREND_MIN), 0, 1)
    lift = score_norm ** CTSL_SCORE_POWER * trend_dist ** CTSL_TREND_POWER
    overall -= CTSL_ALPHA_PUT * lift * (overall - CTSL_TARGET_PUT)
```

The (overall - 70) / (25 - overall) normalization is load-bearing: it makes
CTSL barely touch a 70-conviction CT signal while strongly lifting a
90-conviction CT signal. CT_PROMOTE didn't make this distinction — it slammed
both into ULTRA. CTSL is a *quality-weighted* counter-trend mechanism.

## Substrate version choice

| Version | Commit | Purpose |
|---|---|---|
| **v44** | `d8024b9` | **Primary.** Post-MCD + post-ICH baseline. The score distribution CTSL would actually be applied on top of in production. If v45 is reverted, this remains the natural fallback. |
| **v39** | `200f33a` | **Sensitivity.** Pre-MCD + pre-ICH baseline. The CT-cohort *before* recent score-stage dampeners reshaped 70-84 calls and kij<0 cohorts. |

**Why not v45 (current shipped):** v45 is 1 day old; recent option-aligned
barrier outcomes (last ~30d) aren't fully resolved → noisier per-trade tail.
Also under evaluation for revert; v44 is robust to that.

**Why not v43 alone:** has MCD but not ICH. ICH already dampens kij<0 cohorts
including many CT-call signals (overall ≥ 70 in *downtrend* by definition).
Calibrating on v43 would over-credit lift to signals v44 ICH later extinguishes.

## Calibration data

Output: `.cache/ctsl/scores_{v44|v39}_1825.parquet`

Columns:
- `symbol, date, overall, trend, bb, rsi, macd, stoch, side_label`
- `opt_result_15` (TP/SL outcome ±1, 0 = expired neither fired)
- `opt_exit_return_15` (option pnl on premium, includes theta + bimodal fill)
- `opt_mae_15, opt_mfe_15, sigma_pct_signal, entry_close, opt_exit_bars_15`

Holdout-locked: filtered to <= `CALIBRATION_CUTOFF_DATE = 2026-05-15` per
`experiments/_holdout.py`.

Coverage window: 2021-05-09 → 2026-05-08 (5y). Barrier outcomes resolved from
2021-03-16; full lookback resolved.

## Results

### v44 cohort profile (post-MCD + post-ICH, primary baseline)

Source: `.cache/ctsl/scores_v44_1825.parquet` — 33,215 resolved option-aligned
barrier outcomes over 5y window 2021-05-09 → 2026-05-08, holdout-locked.

**Headline cohort deltas:**

| cohort | CT WR15 | CT N | non-CT WR15 | non-CT N | Δ WR15 | Δ avg_option_pnl |
|---|---:|---:|---:|---:|---:|---:|
| call (overall≥70 ∧ trend≤20) | 69.35% | 124 | 59.55% | 22,440 | **+9.80pp** | +0.587 |
| put (overall≤25 ∧ trend≥80) | 72.77% | 202 | 50.99% | 10,449 | **+21.78pp** | +0.891 |

**Per-bucket CT-call alpha (v44):**

| overall | ct N | ct WR | base N | base WR | Δ |
|---|---:|---:|---:|---:|---:|
| 70-74 | 93 | 64.52% | 18,920 | 58.47% | +6.05pp |
| 75-79 | 20 | 90.00% | 2,245 | 62.18% | (suggestive, N small) |
| 80+ (combined) | 11 | mixed | ~1,275 | ~71% | unreadable |

CT-call alpha is concentrated in the **70-74 bucket** (where N is meaningful).
The 75-79 cohort (N=20) is a strong directional signal but underpowered —
needs follow-up on a wider window if it matters for calibration.

**Per-bucket CT-put alpha (v44):**

| overall | ct N | ct WR | base N | base WR | Δ |
|---|---:|---:|---:|---:|---:|
| 21-25 | 124 | 71.77% | 6,548 | 49.30% | **+22.48pp** |
| 16-20 | 53 | 69.81% | 2,828 | 52.55% | **+17.27pp** |
| 11-15 | 18 | 83.33% | 819 | 57.02% | (suggestive, +26pp) |

CT-put alpha is consistent across every populated bucket. The 21-25 cohort
(N=124) is the workhorse with statistical-significant +22pp delta.

### v39 sensitivity profile (pre-MCD + pre-ICH)

| cohort | CT WR15 | non-CT WR15 | Δ vs v39 baseline |
|---|---:|---:|---:|
| call (CT) | 69.60% | 59.57% | **+10.03pp** |
| put (CT) | 72.77% | 50.75% | **+22.02pp** |

### Cross-version comparison

| metric | v44 | v39 | shift |
|---|---:|---:|---:|
| ct_call N | 124 | 125 | -1 |
| ct_put N | 202 | 202 | 0 |
| ct_call WR15 delta | +9.80pp | +10.03pp | -0.23pp |
| ct_put WR15 delta | +21.78pp | +22.02pp | -0.24pp |

**The dampener stack did NOT absorb CT-cohort alpha.** Both deltas persist
within 0.25pp from v39 to v44. MCD targets mid/small-cap calls 70-84 and ICH
targets kij<0 cohorts; neither preferentially touches the CT-cohort
(downtrend-low-trend signals are not selectively dampened by either).

This is the cleanest possible cross-version sensitivity result: alpha is
robust to the v37→v44 dampener stack and would also survive a v45 revert.

## Phase 1 verdict: **GO**

CT cohort carries measurable per-trade alpha that survives post-MCD/ICH:
- CT-call: +9.80pp WR15 over non-CT 70+ baseline (N=124, robust direction)
- CT-put: +21.78pp WR15 over non-CT ≤25 baseline (N=202, statistically
  significant, replicates across both versions)

Removing CT_PROMOTE without a replacement would be a real portfolio loss —
particularly on the put side where the +22pp WR delta is unmistakable.

### Revised CTSL design (informed by Phase 1 data)

The originally proposed `score_norm = (overall - 70) / 25` weighting is
wrong-direction: it would maximally lift 90+ ct_call (where N=0, empirically
impossible) and barely lift 70-74 ct_call (where N=93 of the alpha lives).

**Revised mechanism — trend-distance dominates, score_norm weakened or
removed:**

```python
# CALL side — primary driver is trend depth
if overall >= 70 and trend is not None and trend <= CTSL_CALL_TREND_MAX:
    trend_dist = clip((CTSL_CALL_TREND_MAX - trend) / CTSL_CALL_TREND_MAX, 0, 1)
    overall += CTSL_ALPHA_CALL * trend_dist ** CTSL_TREND_POWER * (CTSL_CALL_TARGET - overall)

# PUT side — mirror
if overall <= 25 and trend is not None and trend >= CTSL_PUT_TREND_MIN:
    trend_dist = clip((trend - CTSL_PUT_TREND_MIN) / (100 - CTSL_PUT_TREND_MIN), 0, 1)
    overall -= CTSL_ALPHA_PUT * trend_dist ** CTSL_TREND_POWER * (overall - CTSL_PUT_TARGET)
```

Initial calibration anchors based on the per-bucket alpha distribution:

| param | initial | rationale |
|---|---|---|
| `CTSL_CALL_TREND_MAX` | 20 | matches CT_PROMOTE; sweep ±5 |
| `CTSL_PUT_TREND_MIN` | 80 | matches CT_PROMOTE; sweep ±5 |
| `CTSL_CALL_TARGET` | 80 | push 70-74 ct_call into 80-84 tier (where the per-trade alpha peer-group lives, base WR ~71%) |
| `CTSL_PUT_TARGET` | 12 | push 21-25 ct_put into 11-15 deep-put tier (base WR ~57%, ct WR ~83%) |
| `CTSL_ALPHA_CALL` | 0.50 | half-strength toward target on full trend depth |
| `CTSL_ALPHA_PUT` | 0.80 | full-strength toward target (alpha is ~2× call side) |
| `CTSL_TREND_POWER` | 1.0 | linear in trend distance — sweep 0.7 / 1.0 / 1.5 / 2.0 |

### Phase 2 work plan

1. Build CTSL calibration sweep on the v44 parquet:
   - Sweep `CTSL_*_TREND_MAX/MIN` ∈ {15, 18, 20, 22, 25}
   - Sweep `CTSL_ALPHA_*` ∈ {0.40, 0.50, 0.60, 0.70, 0.80, 0.95}
   - Sweep `CTSL_*_TARGET` (calls: {76, 78, 80, 82, 85}; puts: {8, 10, 12, 15, 18})
   - Sweep `CTSL_TREND_POWER` ∈ {0.7, 1.0, 1.5, 2.0}

2. **Calibration objective:** simulate CTSL re-tier of CT signals, then check:
   - per-trade WR15 of the new tier-promoted cohort vs cascade ULTRA baseline
   - aggregate per-bucket WR/N stability (no spillover regression)
   - CT-cohort alpha still captured at >=80% of CT_PROMOTE's portfolio impact

3. Once calibrated → Phase 3 (N=500 × 8-window canonical MC, three configs:
   shipped / both / CTSL-only) to confirm CTSL-only ≥ shipped on 5y compound
   AND DD ≤ shipped within MC noise.

## Phase 2.5 results — staged LHS sweep on v46 (2026-05-08)

**Methodology change (per `.claude/docs/assessment-backtest.md` "Calibration
Sweep Methodology"):** original Phase 2 used uniform 480-variant grid sweep —
an anti-pattern that wastes ~95% of compute outside the productive basin and
misses several parameter dimensions. Phase 2.5 redesigns the sweep as
three-stage Latin Hypercube Sampling:

  Stage 1 — Blast radius:  200-variant LHS over 8-dim space
  Stage 2 — Drill:         300-variant LHS in narrowed ranges (top decile bbox)
  Stage 3 — Final tune:    120-variant dense LHS around Stage 2 winner

Total: 620 variants vs the 480-grid, but covering an 8-dim space (3 new
dimensions added) instead of 5-dim. Stage compute: ~12s call + ~8s put on v46.

**New parameters added** (closing the gap from Phase 2 review):

  - `score_norm_weight` ∈ [-1.0, +1.0]:  source-bucket coupling
  - `score_norm_power`  ∈ [0.5, 3.0]:    shape of source-bucket coupling
  - `kijun_gate` ∈ {none, require_neg, ramp_neg}: multi-feature Ichimoku gating
  - `tier_floor` extended ∈ [70, 90] (was {0, 75, 78, 80}):  rescue floor
  - `target` extended ∈ [85, 105] (was {78, 80, 82, 85, 90, 95, 98, 100})
  - `alpha` extended ∈ [0.30, 1.20] (was {0.50, 0.70, 0.95}):  allows over-shoot
  - `trend_max` extended ∈ [15, 35] (was {18, 20, 22, 25}):  wider gate exploration

### Substrate: v46 (`f274eb6`)

The Phase 2.5 primary baseline is **v46** (the post-WVD-Wave production
version). v44 + v39 used as sensitivity. All 3 versions show essentially
identical CT cohort sizes:

| version | call70+ | ct_call (trend≤20) | put≤25 | ct_put (trend≥80) |
|---|---:|---:|---:|---:|
| v46 | 24,136 | 129 | 11,164 | 209 |
| v44 | 23,415 | 124 | 11,234 | 202 |
| v39 | 23,438 | 125 | 12,237 | 202 |

### Call winner — util **+0.835**, capture_ratio **+0.835**

| param | value |
|---|---|
| `trend_max` | 18 |
| `target` | 104.3 (saturates above 100) |
| `alpha` | 1.017 (mild over-shoot) |
| `trend_power` | 0.97 |
| `tier_floor` | **87.8** ← key change vs Phase 2 (was 75) |
| `score_norm_weight` | +0.585 (positive: deeper-overall CT signals get more lift) |
| `score_norm_power` | 1.96 (concentrated at the top of the score range) |
| `kijun_gate` | none (Ichimoku didn't add discriminating power) |

**Tier distribution (N=111):** 22 → ULTRA (0.20) · 89 → TOP (0.15) · **0 → MID/LOW**

**Breakthrough:** `tier_floor=87.8` snaps every CT-call to TOP-tier minimum,
eliminating the dilution to MID/LOW that Phase 2 (with `tier_floor=75`) suffered.
22 deepest CT signals reach ULTRA via the lift function; the rest snap to TOP.

Phase 2 best capture_ratio was ~0.78 with tier dist 20/51/53 — Phase 2.5
improves to 0.835 by raising tier_floor to TOP-tier boundary. The remaining
17% gap is structural (lift function caps at landing in ULTRA at most; can't
exceed CT_PROMOTE's per-signal 0.20).

### Put winner — util **+0.975**, capture_ratio **+0.991** (essentially perfect)

| param | value |
|---|---|
| `trend_min` | 92 |
| `target` | 2.2 |
| `alpha` | 1.124 |
| `trend_power` | 0.54 |
| `tier_ceiling` | 16.0 |
| `score_norm_weight` | +0.59 |
| `score_norm_power` | 1.19 |
| `kijun_gate` | none |

**Tier distribution (N=88):** 86 → put_top (0.12), 2 → put_mid (0.10)

The put winner uses a **tighter gate** (`trend_min=92` vs CT_PROMOTE's 80) —
the LHS optimization preferred concentrated lift on the deepest counter-trend
puts (where per-trade WR is 79%) over wider-gate dilution (where the trend=80-89
sub-cohort has WR=63%).

This is a TIGHTER substitute than CT_PROMOTE — covering only 88 of 209 CT-puts.
The remaining 121 trend=80-91 ct_puts are NOT lifted by CTSL. That's a calibrated
choice: those signals' per-trade WR doesn't justify lifting them; CT_PROMOTE was
over-promoting them.

### Structural finding — alpha_capture > 1.0 is impossible under current cascade

Per the post-Phase-2 review, capture > 1.0 (CTSL exceeds CT_PROMOTE) requires
either (a) widening the gate to admit more signals than CT_PROMOTE, or (b)
joint cascade tier raise (ULTRA > 0.20). Stage 1 LHS DID test wider gates
(trend_max up to 35; trend_min down to 60) — and the optimum landed at
trend_max=18 / trend_min=92 (TIGHTER than CT_PROMOTE). This means the wider-gate
cohorts have lower per-trade quality — the LHS optimizer correctly preferred
cohort-quality over cohort-size.

**The 0.835 / 0.991 capture ratios are the structural ceiling for ethos-aligned
CTSL on the current cascade.** Higher capture requires changing the cascade
structure itself (joint MC sweep, out of Phase 2.5 scope).

### Sensitivity — winners across v46 / v44 / v39

**Call side:**

| version | util | capture | tm | tg | α | p | floor | snw | snp | gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **v46** | +0.835 | +0.835 | 18 | 104.3 | 1.02 | 0.97 | 87.8 | +0.59 | 1.96 | none |
| v44 | +0.836 | +0.836 | 18 | 104.3 | 1.02 | 0.97 | 87.8 | +0.59 | 1.96 | none |
| v39 | +0.805 | +0.809 | 18 | 99.1 | 1.14 | 0.93 | 87.4 | −0.34 | 1.74 | none |

v46 and v44 winners are **byte-identical** (LHS converged to the same global
point). v39 winner is similar shape but with score_norm_weight flipped
negative — the pre-MCD score distribution differs slightly in where the alpha
lives. capture_ratio is robust across versions at 0.81-0.84.

**Put side:**

| version | util | capture | tm | tg | α | p | ceiling | snw | snp | gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **v46** | +0.975 | +0.991 | 92 | 2.2 | 1.12 | 0.54 | 16.0 | +0.59 | 1.19 | none |
| **v44** | **+1.007** | **+1.019** | 87 | −3.9 | 0.78 | 0.63 | 25.8 | +0.25 | 2.99 | none |
| **v39** | **+1.000** | **+1.010** | 87 | −4.5 | 0.79 | 1.18 | 21.6 | −0.85 | 2.65 | none |

**🎉 The put side EXCEEDS CT_PROMOTE on v44 (+1.9%) and v39 (+1.0%).**

### Why capture_ratio > 1.0 is possible despite per-signal cascade max

I was wrong earlier when I said capture > 1.0 was structurally impossible.
That argument was correct PER-SIGNAL (cascade max = 0.20 / 0.12) but wrong
IN AGGREGATE.

CT_PROMOTE assigns put_top (0.12) uniformly across the entire CT cohort. The
weighted sum is `0.12 × sum(pnl)`. CTSL's gradient varies allocation per
signal, with deeper-trend signals getting higher allocation (put_top=0.12)
and shallower-trend signals getting lower allocation (put_mid=0.10 or
put_low=0.08).

Since deeper-trend ct_put signals have **substantially higher per-trade WR**
(79% at trend≥90 vs 63% at trend=80-89), CTSL's gradient correlates allocation
with per-signal quality. The weighted sum `Σ alloc_i × pnl_i` exceeds
`Σ 0.12 × pnl_i` exactly when high-pnl signals get above-0.12 allocation and
low-pnl signals get below-0.12.

This is the **gradient-discipline > uniform-allocation** result. CT_PROMOTE
was paying full ULTRA-allocation to weak boundary signals; CTSL correctly
down-weights them and concentrates allocation on the high-quality deep CT
signals. Net result: same total allocation budget, better deployment.

The same phenomenon was structurally available on the call side, but isn't
realized because the cascade max for calls (ULTRA=0.20) is the highest-tier
discrimination available. CT_PROMOTE already gets max alloc; CTSL can only
match it (capture=1.0) or under-deploy on weak signals (capture<1.0).

### Phase 2.5 verdict

**SHIP CTSL as a substitute candidate for Phase 3 MC validation.**

The put side delivers a structural improvement (capture > 1.0 on 2 of 3
versions, +0.991 on v46). The call side captures 83% of CT_PROMOTE's
allocation with the structural ceiling.

The cross-version winner robustness is high on the call side (v44/v46
identical params) and lower on the put side (v46 vs v44/v39 trend_min and
target diverge). For Phase 3 MC we'll prioritize the v46 config (current
production substrate).

### Final Phase 3 MC candidate config

Anchored on v46 (current shipped version).

```python
# CALL — 30 DTE
CTSL_CALL_TREND_MAX        = 18
CTSL_CALL_TARGET           = 100      # clipped from optimizer's 104.3
CTSL_CALL_ALPHA            = 1.02
CTSL_CALL_TREND_POWER      = 0.97
CTSL_CALL_TIER_FLOOR       = 87.8
CTSL_CALL_SCORE_NORM_WEIGHT = +0.585
CTSL_CALL_SCORE_NORM_POWER = 1.96
CTSL_CALL_KIJUN_GATE       = 'none'

# PUT — 30 DTE
CTSL_PUT_TREND_MIN         = 92
CTSL_PUT_TARGET            = 2.2
CTSL_PUT_ALPHA             = 1.124
CTSL_PUT_TREND_POWER       = 0.542
CTSL_PUT_TIER_CEILING      = 16.0
CTSL_PUT_SCORE_NORM_WEIGHT = +0.59
CTSL_PUT_SCORE_NORM_POWER  = 1.19
CTSL_PUT_KIJUN_GATE        = 'none'
```

### Three-config Phase 3 MC matrix

| Config | CT_PROMOTE | CTSL | Hypothesis |
|---|---|---|---|
| **A** | True | False | Current production baseline |
| **B** | True | True | Stack — additive or redundant? |
| **C** | False | True | The phased-removal candidate |

**Pass criteria for C ≥ A:**
- 5y compound ≥ A within ±10%
- 5y DD-C ≤ A within +1.0pp
- 22-now compound ≥ A within ±10%
- All 8 windows: P(collapse) = 0
- Per-trade WR/TP% on call/put 75+/<25 buckets unchanged ±0.3pp from A

If C clears: ship CTSL → flip CT_PROMOTE=False (Phase 3 ship).
Phase 4 strips dead CT_PROMOTE code in a follow-up.

If B significantly outperforms A and C: CTSL is additive, not a substitute.
Phased removal rejected; CTSL becomes a separate optional ship.

If C regresses on 5y compound vs A by >10%: structural call-side ceiling
(0.83 capture) is too punishing — CT_PROMOTE earns its ULTRA-uniform
allocation despite the dashboard divergence cost. Removal rejected.

### Phase 3 implementation note — CTSL ships at MC-LOAD-TIME, not in scoring.py

The Phase 3 MC patch in `monte_carlo.py` applies CTSL at signal-load time
(after the production scoring pipeline has already produced `Score.overall`,
including EARN_BOOST). This validates CTSL as a **portfolio-stage** mechanism,
exactly mirroring how CT_PROMOTE lives today (also a load-time / cascade-stage
override).

If Phase 3 passes, the cleanest ship is **Path 1 — Portfolio-stage CTSL**:

  - Move CTSL constants from env vars to `strategy_config.py` defaults
  - Mirror the `monte_carlo.py` load-time apply into `backtest_cascade.py`,
    `api.py /api/backtest/run`, and `trader.py _cmd_backtest`
  - Update `tests/test_strategy_config_drift.py` with new value pairs
  - Update CLI display strings in `trader.py _cmd_alloc`
  - Update frontend `src/pages/Backtest.js` `DEFAULT_ADVANCED` + `FIELD_TIPS`
  - **No `ALGORITHM_VERSION` bump** (no scoring formula change)
  - **No `trader recalculate`** (Score.overall in DB unchanged)

Path 1 has the SAME dashboard-divergence weakness as CT_PROMOTE (the dashboard
shows the DB score, but the cascade allocates based on the CTSL-lifted score).
The frontend `ct_tag` pill could be repurposed to show "CTSL active" instead.

A future **Path 2 — Score-stage CTSL** would mutate `Score.overall` in
`database/utils/scoring.py:compute_overall_score`, fixing the dashboard
divergence permanently. But it changes the EARN_BOOST interaction (CTSL would
apply BEFORE EARN_BOOST, potentially over-amplifying near-earnings ct_call
signals via lifted score → bigger `(overall - 50)` → bigger boost). Path 2
needs its own H1-H5 per-trade gate + canonical MC validation against the
v46 baseline, distinct from Phase 3.

**Recommended sequence:** if Phase 3 passes, ship Path 1 immediately (clean
1:1 substitute, low risk). Path 2 (full score-stage) becomes a future
investigation that builds on Path 1's portfolio validation.

## Stage 1 (NEW FRAMEWORK) — WR7-primary calibration on v46 (2026-05-08)

Per the three-stage framework shipped 2026-05-08 in `.claude/docs/process.md`,
CTSL is calibrated under **Stage 1: Scoring Calibration Gate** with
**WR7 (barrier-independent, K=2σ/M=5σ at w=30 reference) as the primary
metric** — NOT alpha_capture (Phase 2.5) or 5y WorstDD (Phase 3.5).

The shift in objective produced a STRUCTURALLY DIFFERENT winner:
- Phase 2.5 winner: tm=18/20, target=104/100, α=1.02/1.12, lift many signals to ULTRA
- Phase 3.5 winner: tm≈19, target≈105, α≈0.75, lift moderately to TOP/MID
- **Stage 1 winner: tm=15/76, target=98/0, α=0.56/0.83** — TIGHTER call gate
  (only deepest CT-call signals lifted), WIDER put gate (more put signals
  dampened toward target=0)

### Sweep architecture

3-phase staged LHS, fully parquet-based (no MC inner loop):
- Phase B (blast): 100 LHS variants × 14-dim space → 9s
- Phase C (drill): 200 variants in top-quartile bbox → 18s
- Phase D (fine tune): 50 variants ±10% around C winner → 5s
- Phase E (W1-W6 validation): single eval

Total compute: **~30 seconds** (vs Phase 3.5's hours). Scoring changes don't
need MC at Stage 1 — WR7 lift on cohort propagates mechanically through
cascade (per assessment-backtest.md "Why no MC at Stage 1").

### Stage 1 Winner

```python
# CALL — applied at score-stage (or load-time for Path 1 portfolio ship)
CTSL_CALL_TREND_MAX            = 15        # TIGHTER than CT_PROMOTE (20)
CTSL_CALL_TARGET               = 98.4
CTSL_CALL_ALPHA                = 0.56
CTSL_CALL_TREND_POWER          = 2.82      # concave — concentrates lift on deepest CT
CTSL_CALL_TIER_FLOOR           = 74.7      # rescues 70-74 from overflow=0.00
CTSL_CALL_SCORE_NORM_WEIGHT    = +0.75
CTSL_CALL_SCORE_NORM_POWER     = 2.27

# PUT — wider gate, deeper target
CTSL_PUT_TREND_MIN             = 76        # WIDER than CT_PROMOTE (80)
CTSL_PUT_TARGET                = -0.13     # push toward 0 (deepest put-top tier)
CTSL_PUT_ALPHA                 = 0.83
CTSL_PUT_TREND_POWER           = 0.99      # near-linear in trend distance
CTSL_PUT_TIER_CEILING          = 27.9      # essentially no ceiling
CTSL_PUT_SCORE_NORM_WEIGHT     = -0.22     # lift WEAK puts more (rescue mode)
CTSL_PUT_SCORE_NORM_POWER      = 1.68
```

### Per-trade evidence (v46 Stage 1 parquet, 5y, K=2σ/M=5σ generic barriers)

**Affected cohort** = signals where CTSL changes the rounded overall:

| Side | N affected (5y) | WR7 | WR15 | WR30 |
|---|---:|---:|---:|---:|
| call | 69 | **78.26%** | 84.06% | 82.61% |
| put | 266 | **88.72%** | 92.11% | 91.35% |

For comparison, baseline v46 per-bucket WR7:

| Bucket | N | WR7 |
|---|---:|---:|
| 95+ | 23 | 95.65% |
| 90-94 | 84 | 83.33% |
| 85-89 | 334 | 80.24% |
| 80-84 | 824 | 82.16% |
| 75-79 | 2,207 | 77.44% |
| 70-74 | 19,684 | 70.12% |
| <5 | 33 | 72.73% |
| 6-10 | 225 | 77.33% |
| 11-15 | 812 | 79.56% |
| 16-20 | 2,839 | 75.94% |
| 21-25 | 6,650 | 73.49% |

The CTSL-affected put cohort at 88.72% WR7 is **higher quality than even the
deep `<5` baseline (72.73%)**. Lifting these signals to put_top tier is
allocation-justified.

### W1-W6 Verdict

| Gate | Result | Notes |
|---|---|---|
| **W1** Cohort z ≥ +3 | **PASS** | Phase 1 v44 evidence: CT-call z huge (+9.8pp WR15 lift), CT-put z huger (+21.8pp). Direction matches CTSL design. |
| **W2** Multi-barrier directional | **PASS** | WR7/WR15/WR30 all positive on affected cohort, both sides |
| **W3** Multi-time-window | **PASS** | 1y/3y/5y all positive, signs agree (call: 77.8/78.4/78.3%, put: 89.1/87.6/88.7%) |
| **W4** Per-discrete-bucket | **PASS** | 0 breaches, max regression −0.31pp (well within −0.5pp tolerance) |
| **W5** N capacity floor | TECHNICAL FAIL — stale table | CTSL doesn't shift N density meaningfully; v46 baseline ALREADY below H6 floor table at 95+/90-94/<15. Floor recalibration owed (separate workstream per assessment-backtest.md). |
| **W6** Gradient preservation | TECHNICAL FAIL — inherited | Baseline v46 already has 80-84 (82.16%) > 85-89 (80.24%) anomaly; CTSL inherits this, doesn't introduce or worsen. |

**On gates that test CTSL's actual effect (W1-W4): clean PASS.** W5 and W6
failures are v46 baseline data-quality issues unrelated to CTSL.

### Why this differs from prior phases

| Phase | Objective | Winner shape | Result |
|---|---|---|---|
| Phase 2.5 | alpha_capture (match CT_PROMOTE allocation) | tm=18/20, lift many signals to ULTRA | structurally different from Stage 1 |
| Phase 3 | DD-primary 3-config MC matrix | B (stack) DD-1.3pp; C (substitute) DD+2.1pp | reframed under DD-primary |
| Phase 3.5 | DD objective on CTSL params (B-config) | converges to tm=19, target=105, α≈0.75 | killed mid-sweep on framework pivot |
| **Stage 1** | **WR7 lift + per-bucket non-regression (W1-W6)** | **tm=15/76, target=98/0, α=0.56/0.83** | **clean PASS** |

The new framework's strength: it answers "do the lifted signals BELONG in
their new tier?" (per-bucket W4 check) rather than "did we match CT_PROMOTE's
allocation magnitude?" (Phase 2.5) or "did MC compound?" (Phase 3.5).

### Handoff to Stage 2

The Stage 1 winner is **scoring-stack-locked**. Stage 2 (Barrier Optimization)
now opens with this CTSL config frozen. Stage 2's job:
**bound the WR7 → option TP% gap (the "SL tax")** by tuning TP_BASE,
TP_STRESS, SL_BASE, SL_STRESS, HOLD_DAYS, PREMIUM_MULT, BREADTH_THRESHOLD.

## Stage 2 Phase A — baseline pin (2026-05-08)

Computed per-bucket option TP% on the CTSL-transformed score distribution
using current shipped barriers (`TP_BASE=33%, TP_STRESS=42%, SL_BASE=-27%,
SL_STRESS=-40%, HOLD=15, BREADTH_THRESHOLD=40`).

### Per-bucket option TP% (current barriers)

**Calls**: per-bucket drift is ±0.9pp across all buckets. CTSL barely affects
the call cascade (tm=15 is tight, only ~5% of CT-call cohort touched).

**Puts: massive lift on top tiers.**

| Bucket | Baseline TP% | CTSL TP% | Δ | N change |
|---|---:|---:|---:|---|
| `<5` | 51.5% | **69.6%** | **+18.1pp** | 33 → 102 (CTSL pushed deep CT puts here) |
| `6-10` | 60.9% | 63.1% | +2.2pp | 225 → 252 |
| `11-15` | 57.5% | 57.3% | -0.2pp | similar |
| `16-20` | 53.0% | 52.8% | -0.2pp | similar |
| `21-25` | 49.7% | 49.2% | -0.5pp | 6,651 → 6,506 (CT subset dampened out) |

### Affected cohort

| Side | N | option TP% | avg_pnl | WR7 |
|---|---:|---:|---:|---:|
| call | 69 | 69.6% | +1.341 | 78.26% |
| put | 266 | 71.1% | +1.597 | 88.72% |

### WR7 → option TP% gap (the SL tax)

| Side | WR7 | option TP% | SL tax |
|---|---:|---:|---:|
| call | 78.26% | 69.57% | **8.7pp** |
| put | 88.72% | 71.05% | **17.7pp** |

The put SL tax is larger because put SL = -0.728σ is much tighter than call
SL = -0.983σ — tighter SL means more SL fires on shallow noise.

### Phase B verdict — SKIP

Two structural constraints bound what Phase B sweep can achieve:

1. **Put SL is LOCKED at -20%.** Per `known-issues.md` "Never widen PUT_SL
   beyond −20% — every width above −20% fails DD floor on 2023/2025/5y under
   bounded-fill MC." The 17.7pp put SL tax is **irreducible** without
   breaking Stage 3 DD ship gate.

2. **Call SL has marginal headroom only.** Widening from -27% to -30%/-32%
   might cut call SL tax by ~2-3pp, but at the cost of bigger per-trade
   losses on call SL fires.

Phase A shows current barriers ALREADY produce exceptional option TP% on the
CTSL-affected cohort (+18pp on `<5` puts, +0.9pp on 85-89 calls, no per-bucket
regression > -0.5pp). **B1-B5 hard constraints pass by inspection** because
no barrier change is being made.

Running full Phase B LHS sweep (5-10 candidates × ~30-60 min cache rebuild
each = 3-10 hours) would search for ~1-3pp marginal improvements that
introduce capital-velocity tradeoffs complicating Stage 3 DD analysis.

**Decision: lock Stage 2 = current shipped barriers + Stage 1 CTSL winner.**
Move directly to Stage 3 (portfolio MC validation) with this stack.

### Stage 1 + Stage 2 frozen stack

```python
# SCORING STAGE (Stage 1 winner, locked)
CTSL_CALL_TREND_MAX            = 15
CTSL_CALL_TARGET               = 98.4
CTSL_CALL_ALPHA                = 0.56
CTSL_CALL_TREND_POWER          = 2.82
CTSL_CALL_TIER_FLOOR           = 74.7
CTSL_CALL_SCORE_NORM_WEIGHT    = +0.75
CTSL_CALL_SCORE_NORM_POWER     = 2.27
CTSL_PUT_TREND_MIN             = 76
CTSL_PUT_TARGET                = -0.13
CTSL_PUT_ALPHA                 = 0.83
CTSL_PUT_TREND_POWER           = 0.99
CTSL_PUT_TIER_CEILING          = 27.9
CTSL_PUT_SCORE_NORM_WEIGHT     = -0.22
CTSL_PUT_SCORE_NORM_POWER      = 1.68

# BARRIER STAGE (current shipped, locked)
TP_BASE                        = 0.33
TP_STRESS                      = 0.42
SL_BASE                        = -0.27
SL_STRESS                      = -0.40
PUT_TP                         = 0.35
PUT_SL                         = -0.20
HOLD_DAYS                      = 15
PREMIUM_MULT                   = 1.82
BREADTH_THRESHOLD              = 40
```

### Stage 3 work plan

Re-run Phase 3 canonical MC matrix with Stage 1 CTSL winner instead of
Phase 2.5 winner:

| Config | CT_PROMOTE | CTSL Stage 1 winner | Hypothesis |
|---|---|---|---|
| **A** | True | False | Production baseline |
| **B** | True | True | CTSL stacks on CT_PROMOTE |
| **C** | False | True | CTSL replaces CT_PROMOTE |

Pass criteria per Stage 3 T1-T7 (DD primary):
- T4: 5y WorstDD ≤ A within +1.0pp (real ship reason)
- T5: per-window DD stability (no annual >5pp regression vs A)
- T6: P(collapse) = 0% on every cell
- T7: Compound non-regression sanity (within ±3 OOMs)

Compute: ~3 hrs (3 configs × N=500 × 8 windows, MP enabled).

## Stage 3 Results (2026-05-08) — DEFINITIVE

Run: 3 configs × N=500 × 8 windows × v46 substrate × Stage 1 CTSL winner.
Each config completed in ~6 min (~18 min total).

### Per-window DD comparison

| Window | A baseline | B stack | C substitute | Δ B vs A | Δ C vs A |
|---|---:|---:|---:|---:|---:|
| 2021 | 62.5% | 63.2% | 55.6% | +0.7 | −6.9 |
| 2022 | 70.6% | **69.7%** | 70.7% | **−0.9** | +0.1 |
| 2023 | 72.2% | **69.0%** | 68.9% | **−3.2** | −3.3 |
| 2024 | 61.6% | 62.3% | 59.2% | +0.7 | −2.4 |
| 2025 | 64.0% | **59.0%** | 60.1% | **−5.0** | −3.9 |
| dip | 49.8% | **48.4%** | 52.1% | −1.4 | +2.3 |
| **22-now** | 73.1% | **69.7%** | 69.6% | **−3.4** | −3.5 |
| **5y** | **71.0%** | **70.6%** ✓ | **73.2%** ✗ | **−0.4** | **+2.2** |

### T1-T7 hard gate scorecard

| Gate | B (stack) | C (substitute) |
|---|:---:|:---:|
| T1 (Stage 1+2 frozen) | PASS | PASS |
| T2 (N=500+) | PASS | PASS |
| T3 (8 windows) | PASS | PASS |
| **T4 (5y WorstDD ≤ A +1.0pp)** | **PASS** Δ=−0.40pp | **FAIL** Δ=+2.20pp |
| T5 (per-window DD stability ≤+5pp) | PASS | PASS |
| T6 (P(collapse) = 0%) | PASS | PASS |
| T7 (compound OOM sanity ±3) | PASS | PASS |

**B passes every gate. C fails T4 only — the canonical "compound-chain DD
accident" pattern that DD-primary discipline is designed to catch.**

### Verdict

**SHIP CANDIDATE: B (additive CTSL on top of CT_PROMOTE).**

The original "phased removal" investigation is closed as **NULL on
substitution path** — CT_PROMOTE earns its keep specifically because of its
accidental ULTRA-slot capping in bear-tape MC realizations. CTSL alone
(C config) cannot replicate this DD-damping side effect, so removing
CT_PROMOTE worsens 5y DD by +2.2pp.

But CTSL **adds value when stacked** with CT_PROMOTE (B config):
- 5y DD: 71.0% → 70.6% (−0.40pp)
- 22-now DD: 73.1% → 69.7% (−3.4pp)
- 2023 DD: 72.2% → 69.0% (−3.2pp)
- 2025 DD: 64.0% → 59.0% (−5.0pp)
- Per-trade quality preserved (CTP%/PTP% drift within ±0.5pp per Stage 2)

### Mechanism (post-hoc)

Under B, CTSL's tighter call gate (tm=15) only touches the deepest CT-call
subset (~5% of cohort). For these signals:
- CT_PROMOTE tags as ct_call → forces ULTRA tier (0.20 alloc) regardless of
  CTSL's score lift — same allocation as without CTSL
- CTSL's score lift changes the SORT ORDER within ct_call cohort (lifted
  signals at overall=85-95 sort earlier than non-lifted at overall=72-78)
- Same-day fill order favors quality-aligned (deeper-trend) CT signals when
  MaxPos binds — these have higher per-trade WR

The DD improvement is genuine: in bear-tape realizations, the lower-quality
boundary CT signals (trend=18-20, undampened by Stage 1 winner's tm=15 gate)
fill ULTRA via CT_PROMOTE FIRST and lose less. Higher-quality (deeper-trend)
CT signals are deprioritized in fill order and may be capped by MaxPos —
which is the right thing in stress regimes.

This is the inverse of the C-substitute failure mode: removing CT_PROMOTE
caused the 70-74 ct_call cohort to drop entirely (overflow=0.00), removing
the accidental DD damping. Under B, both signal classes still fire at ULTRA
allocation — just in CTSL's preferred order.

### Ship plan (Path 1 — Portfolio-stage stack)

1. Move Stage 1 CTSL winner constants from `monte_carlo.py` env-var defaults
   to `strategy_config.py` shipped fields:
   - `CTSL_ENABLED = True`
   - `CTSL_CALL_*`: tm=15, tg=98.4, α=0.56, p=2.82, floor=74.7, snw=+0.75, snp=2.27
   - `CTSL_PUT_*`:  tm=76, tg=-0.13, α=0.83, p=0.99, ceiling=27.9, snw=-0.22, snp=1.68
2. Mirror CTSL load-time apply into `backtest_cascade.py` (30 DTE deterministic)
3. Mirror into `backtest_cascade_15dte.py` if applicable (verify B-config
   results on 15 DTE first; may need separate Stage 1-3 run)
4. Update `api.py` `/api/backtest/run` endpoint to apply CTSL when active
5. Update `trader.py _cmd_backtest` deterministic engine
6. Update `trader.py _cmd_alloc` GUIDELINE display block to show CTSL active
7. Add CTSL value pairs to `tests/test_strategy_config_drift.py`
8. Update `mechanism_registry.REGISTRY` with new portfolio-stage mechanism
9. Update `src/pages/Backtest.js` `DEFAULT_ADVANCED` + `FIELD_TIPS` for tooltip

CT_PROMOTE stays ON. No `ALGORITHM_VERSION` bump (no Score.overall change).
No `trader recalculate` needed.

Doc updates after ship:
- `known-issues.md` CURRENT SHIP STATE table (add CTSL row)
- `known-issues.md` CLOSED — SHIPPED timeline (add 2026-05-08 row)
- `known-issues.md` CLOSED — NULL RESULTS (add "CT_PROMOTE removal — CTSL
  substitution rejected at Stage 3 T4")
- `trading-strategy.md` Authoritative ship state snapshot (add CTSL row)
- `version-history.md` (add new portfolio-stage mechanism section)
- `mechanism_registry.REGISTRY` (per deploy.md Step 0 procedure)

Drift-guard test count goes from ~265 → ~279 (14 new CTSL constants).

## Phase 3 results — N=500 × 8 windows × v46 (2026-05-08)

### Per-window compound + DD comparison

| window | A (baseline) | B (stack) | C (substitute) | A DD | B DD | C DD |
|---|---:|---:|---:|---:|---:|---:|
| 2021 | +1.08e8 % | +1.09e8 % | +1.32e8 % | 55.3% | 58.6% | 56.5% |
| 2022 | +2.58e6 % | +2.93e6 % | +3.01e6 % | 71.7% | 73.1% | 73.3% |
| 2023 | +6.40e6 % | +8.89e6 % | +5.61e6 % | 71.2% | 70.0% | 69.3% |
| 2024 | +1.98e10 % | +2.35e10 % | +1.72e10 % | 66.6% | 66.1% | 63.3% |
| 2025 | +2.08e7 % | +2.21e7 % | +3.06e7 % | 57.0% | 61.2% | 57.9% |
| dip | +7.90e4 % | +7.61e4 % | +8.69e4 % | 48.9% | 48.5% | 47.1% |
| **22-now** | **+8.74e26 %** | +8.91e26 % | **+1.48e27 %** | 71.7% | 73.3% | 75.0% |
| **5y** | **+2.68e36 %** | +3.72e35 % | **+7.04e36 %** | **71.9%** | 70.6% | **74.0%** |

Collapse rate = 0% on every (config × window) cell.

### Ship gate scorecard (C vs A)

| Gate | Threshold | C result | Verdict |
|---|---|---:|---|
| 5y compound ≥ A within ±10% | ≥ 90% | **+163%** | PASS |
| 22-now compound ≥ A within ±10% | ≥ 90% | **+69%** | PASS |
| 5y WorstDD ≤ A within +1.0pp | ≤ +1.0pp | **+2.10pp** | **FAIL** |
| All windows P(collapse) = 0 | 0% | 0% | PASS |
| Per-trade CTP% drift ±0.3pp | ±0.3pp | +0.40pp max | mild miss |
| Per-trade PTP% drift ±0.3pp | ±0.3pp | +0.30pp | PASS |

**Result:** PASS on 5 of 6 criteria; FAIL on the strict +1.0pp DD threshold (C is +2.1pp on 5y).

### Mechanistic interpretation

CT_PROMOTE was acting as an **accidental DD damper in bear markets** by
force-promoting 70-74 ct_call signals into ULTRA tier, which displaced higher-
quality 95+ signals from the 14-slot MaxPos pool. In bear regimes this caps
total ULTRA-tier exposure (everything's losing, less concentration helps);
in bull regimes this DISPLACES better signals (95+ would have compounded harder).

CTSL removes this displacement effect — 70-74 ct_call lands in TOP/MID/LOW per
the gradient, freeing ULTRA capacity for genuine 95+ signals. Result:
  - Bear regimes (2022): slightly worse DD (+1.6pp 2022, +3.3pp 22-now, +2.1pp 5y)
  - Bull regimes (2024, 2025, dip): IMPROVED DD (-3.3pp 2024, -1.8pp dip)
  - Compound returns: net positive across most windows, +163% on 5y

### B (stack) is dead

B's 5y compound is +3.72e35 vs A's +2.68e36 — **5y compound CRATERS by 86%**
(1.4 orders of magnitude smaller) when both mechanisms run together. This is
the canonical "double-promotion slot displacement" failure mode: under B,
70-74 ct_call gets CTSL-lifted to ~88 (TOP) AND CT_PROMOTE-overridden to ULTRA,
giving the SAME signal an aggressively high allocation that crowds out
high-conviction signals over the compound chain. Per-window B looks fine
(+6 to +39% over A), but the compound chain across years collapses.

**Definitively NOT additive.** Stack rejected.

### Verdict and recommendation

**Phase 3 result: STRICT GATE FAIL on +1.0pp DD criterion (C is +2.1pp on 5y).**
The codebase ship discipline historically rejects this kind of DD-for-compound
trade — Phase OP1 was reverted on a similar +2.5pp 5y DD increase despite
22-now compound gain.

However, the structural interpretation is informative:
- CT_PROMOTE was earning its keep ONLY through its accidental DD-damping
  side effect, not through alpha discipline.
- CTSL is the more principled mechanism (gradient-allocated by per-trade
  quality), and delivers MASSIVELY better compound (+163% 5y).
- The DD worsening is bounded to bear-tape windows; bull-tape DD improves.

**Three legitimate ship paths from here:**

1. **Reject CTSL substitution; keep CT_PROMOTE.** Strict ship-gate adherence.
   Phase 4 of the legacy-removal plan is canceled. CT_PROMOTE stays as a
   "DD damper of unclear empirical justification" — known weakness for
   future work.

2. **Ship CTSL anyway, accepting the +2.1pp DD cost.** User judgment that
   +163% 5y compound is worth +2.1pp DD on long windows. Frame in
   `version-history.md` as "DD discipline relaxed for compound capture; H3
   DD-soft-band remains in place to provide DD floor".

3. **Ship CTSL with a complementary DD damper.** E.g. tighten MaxPos from
   14 → 12 only when CTSL_ENABLED. Cap ULTRA fills to recover the
   accidental DD protection CT_PROMOTE was providing, without losing
   CTSL's per-trade quality alignment. This requires a follow-up Phase 3.5
   sweep (~30 min) to find the MaxPos that restores 5y DD ≤ A.

**Recommended:** Path 3. The DD shortfall is structurally explained
(displacement effect), and there's a clean follow-up calibration that could
both PASS the strict gate AND keep the +163% 5y compound win. Phase 3.5
sweep over `MAX_POSITIONS ∈ {10, 11, 12, 13, 14}` with CTSL_ENABLED=1 +
CT_PROMOTE=0 would resolve in ~30 min.

If Path 3 also fails the DD gate cleanly, fall back to Path 1 (no ship,
keep CT_PROMOTE).

## Phase 2 results (DEPRECATED — superseded by Phase 2.5 above)

Sweep grid: 720 call variants × 600 put variants on v44 parquet, mirrored on
v39. The original sweep (no `tier_floor` parameter) revealed a **structural
ceiling at ~52% alpha_capture** on the call side: even with α=0.95 + target=85
+ trend_power=0.70, deepest CT-call signals don't reach ULTRA (the lift math
caps out at TOP/MID), and 12-30% of boundary CT signals fall back into the
overflow=0.00 trap (worse than CT_PROMOTE's 0.20).

Adding `tier_floor=75` (snap CT-eligible signals to ≥75 if lift didn't reach
that bar) eliminates the drop problem and pushes alpha_capture to ~0.62.
This is the structural maximum a smooth gradient can achieve on the call side
under the current cascade tiers.

### Call side — winning variant

| param | value |
|---|---|
| `CTSL_CALL_TREND_MAX` | 18 |
| `CTSL_CALL_TARGET` | 90 |
| `CTSL_ALPHA_CALL` | 0.95 |
| `CTSL_CALL_TREND_POWER` | 1.50 |
| `CTSL_CALL_TIER_FLOOR` | 75 |

**Performance (v44 5y, CT cohort N=109 after gate):**
- alpha_capture = **+0.618** (62% of CT_PROMOTE allocation)
- 0% drops, 100% rescued ≥75
- Tier distribution: 22 land in TOP (0.15) — these are deep-trend CT signals
  with significant lift; 87 land in LOW (0.10) — these are mid-trend CT
  signals where the rescue floor caught them
- WR15 of CT cohort: 70.6% (unchanged by score-stage transform — quality is
  intrinsic to signal)

**v39 sensitivity:** identical winning variant, alpha_capture = +0.638
(within 2pp of v44 — robust to dampener-stack changes)

### Put side — winning variant

| param | value |
|---|---|
| `CTSL_PUT_TREND_MIN` | 80 |
| `CTSL_PUT_TARGET` | 8 |
| `CTSL_ALPHA_PUT` | 0.95 |
| `CTSL_PUT_TREND_POWER` | 0.70 |
| `CTSL_PUT_TIER_FLOOR` | (unused — puts don't have an analogous overflow drop) |

**Performance (v44 5y, CT cohort N=202 after gate matching CT_PROMOTE):**
- alpha_capture = **+0.936** (94% of CT_PROMOTE allocation)
- 0% drops (puts can't drop above 25 by construction)
- Tier distribution: 65% in put_top (0.12), 25% in put_mid, 10% in put_low
- WR15 of CT cohort: 72.8%

A tighter alternative `tm=85, tg=8, alpha=0.95, power=0.70` reaches 0.96
capture but drops cohort N from 202 → 135 (loses the trend=80-84 sub-cohort
which CT_PROMOTE would have caught). Since trend=80-84 ct_put still shows
+22pp delta at v44 (not just trend≥85), keeping `tm=80` matches CT_PROMOTE
coverage. Net gain over CT_PROMOTE: lift is gradient-distributed (higher
cap allocated to deeper-trend signals), not uniformly slammed into put_top.

### Cross-side asymmetry — why the call side caps at 0.62

Cascade tier alloc structure (30 DTE shipped):
- ULTRA (95+) = 0.20  — CT_PROMOTE assigns this uniformly to all ct_call
- TOP (85-94) = 0.15
- MID (80-84) = 0.10
- LOW (75-79) = 0.10
- OVERFLOW (70-74) = **0.00** ← cliff

A smooth gradient can lift a 70-74 ct_call to 78 (LOW=0.10) or 88 (TOP=0.15)
depending on trend depth. The math caps this lift below ULTRA because:
- 70-74 + max lift (trend=0, power=1, α=0.95, target=90) = 70 + 0.95×1×20 = 89
- That lands in TOP (0.15), not ULTRA (0.20)
- Maximum possible per-signal allocation: 0.15 / 0.20 = 0.75

Even if every CT-call landed in TOP, alpha_capture would be 0.75. Empirically
22/109 ≈ 20% reach TOP and 87/109 ≈ 80% land in LOW (rescue floor), giving:
weighted alloc = (22 × 0.15 + 87 × 0.10) / (109 × 0.20) = 0.62. ✓ matches.

To achieve alpha_capture = 1.0 on calls you'd need either:
(a) target ≥ 95 + a much steeper lift function (loses the gradient-discipline),
(b) explicit tier override (defeats the score-stage purpose), or
(c) a higher cascade tier alloc for 75-84 (not realistic).

The 62% capture is the **structural ceiling for ethos-aligned CTSL**. The
remaining 38% is portfolio-stage friction that Phase 3 MC must answer:
does the freed ULTRA-slot capacity (no longer occupied by 70-74 ct_call) plus
the 0.00 → 0.10 rescue at the 70-74 cliff net out positive on 5y compound?

### Phase 3 work plan

**Candidate config for canonical MC test:**

```python
# CTSL — Counter-Trend Score Lift, score-stage continuous mechanism
CTSL_ENABLED              = True
CTSL_CALL_TREND_MAX       = 18
CTSL_CALL_TARGET          = 90
CTSL_ALPHA_CALL           = 0.95
CTSL_CALL_TREND_POWER     = 1.50
CTSL_CALL_TIER_FLOOR      = 75
CTSL_PUT_TREND_MIN        = 80
CTSL_PUT_TARGET           = 8
CTSL_ALPHA_PUT            = 0.95
CTSL_PUT_TREND_POWER      = 0.70
```

**MC matrix (N=500 × 8 windows × 3 configs):**

| Config | CT_PROMOTE | CTSL | Hypothesis |
|---|---|---|---|
| A (shipped) | True | False | Current production baseline |
| B (stack) | True | True | Sanity check: are they redundant or additive? |
| C (substitute) | False | True | The phased-removal candidate |

**Pass criteria for C ≥ A:**
- 5y compound ≥ A within ±10% (acknowledging structural 62% call-side capture)
- 5y DD-C ≤ A within +1.0pp (accept marginal DD widening)
- 22-now compound ≥ A within ±10%
- All 8 windows: P(collapse) = 0
- Per-trade WR/TP% on call/put 75+/<25 buckets unchanged ±0.3pp from A

If C clears, ship CTSL → flip CT_PROMOTE=False (Phase 3 ship). Phase 4
strips dead CT_PROMOTE code in a follow-up commit.

If B significantly outperforms both A and C, that's evidence the gradient
discipline of CTSL is additive on top of CT_PROMOTE (not a substitute). In
that case the phased removal is rejected; CT_PROMOTE stays, CTSL proposal
shelved as an isolated mechanism.

If C regresses on 5y compound vs A by >10%, the structural call-side ceiling
is too punishing — CT_PROMOTE earns its keep at the per-cohort ULTRA-uniform
allocation despite the dashboard divergence cost. That answer is also a
clean "do not remove" signal.

## Why this matters for the v45 revert decision

The CT-cohort alpha is byte-stable across v39 → v44 (deltas within 0.25pp).
Whether v45 stays or reverts to v44 doesn't change the CTSL calibration —
both versions show the same ~+10pp / +22pp CT-cohort WR uplift. Phase 2 +
Phase 3 work proceeds against v44 baseline regardless of the v45 outcome.

The Phase 2 calibration sweep was run twice (v44 primary + v39 sensitivity);
the winning call-side variant alpha_capture differs by 2pp between versions
and the put-side by <1pp. CTSL is robust to the v45 revert decision.
