# Dynamic TP/SL — Pre-flight + Stage 3 Phase B (CLOSED NULL 2026-05-09)

**Hypothesis (user-proposed).** Per-day TP/SL should depend on the size of the
qualifying signal pool that day. High signal density → recycle losers faster
(tighter SL). Low density → hold through noise (wider SL).

**Outcome (overall).** Cohort signal is REAL (z=+11.23 puts, z=+4.07 calls
on same-side daily count vs WR15 at 30dte_opt barriers, 5y v46 holdout-locked).
But it does NOT translate to portfolio-stage alpha when used as a regime
classifier — Stage 3 Phase B at N=300 × 22-now shows EVERY count-based stress
threshold underperforms breadth-based on WorstDD (+1 to +7pp regression).

NULL. Add to "WHAT NOT TO DO": `daily_signal_count` is a fixed-barrier quality
signal, not a regime classifier. Don't swap `BREADTH_THRESHOLD` for a count-
based switch — quiet-tape days are sparse, not high-vol; widening TP/SL on
them leaves capital exposed during normal moves.

---

## Phase A — Pre-flight cohort signal (2026-05-09)

Computed `daily_cash_demand_t = sum(tier_alloc_i for i in qualifying signals
on date t)` per signal date, split signals into demand quintiles, measured
30dte_opt barrier outcomes (W=15) per cohort.

### Cohort split (5y v46, fixed 30dte_opt barriers W=15)

| Cohort | CALL TP% | CALL z | CALL bars | PUT TP% | PUT z | PUT bars |
|---|---:|---:|---:|---:|---:|---:|
| Q1 (lowest demand) | 63.02% | -1.69 | 1.48 | 45.74% | -5.32 | 1.43 |
| Q5 (highest demand) | 68.94% | +1.69 | 1.31 | **60.44%** | **+8.27** | 1.44 |
| **Q5 vs Q1 ΔTP** | **+5.92pp** | **z=+2.38** | -0.16 | **+14.69pp** | **z=+9.63** | +0.01 |

Population baselines: CALL N=3,622 TP=65.96% / PUT N=10,607 TP=51.45%.

### Confound check ruled out pure regime

Same-side demand >> cross-side demand magnitude on both sides. Cross-side
REVERSES (controls for bull/bear regime). Residual queue-depth signal exists.
Alloc-weighting adds nothing over raw count (z=+10.75 raw count vs +11.23
alloc-weighted on puts).

### Marginal info vs BREADTH_THRESHOLD=40

2x2 split confirmed cash_demand carries info BEYOND breadth. Most striking:
**breadth≤40 × demand-high CALL cell → TP=71.26% (best of all cohorts)** —
currently treated as "stress" by breadth proxy.

### Recycling-velocity story: WEAK

Avg bars-to-resolution differs by ~0.16 bars (calls) / ~0 bars (puts) across
cohorts. Both sides resolve in ~1.4 bars regardless of demand. Falsifies the
recycling-velocity component of the original hypothesis.

---

## Phase B — Stage 3 portfolio MC validation (2026-05-09)

Wired env-overridable `STRESS_MODE` ('breadth'|'count') in monte_carlo.py.
When 'count', `is_stressed(call) = (same_side_count_today <=
STRESS_COUNT_THRESHOLD_CALL)`. Calls only — puts already use
PUT_BREADTH_MODE='none' (always BASE).

### Smoke MC (N=80 × 22-now) — appeared promising

| config | CTP% | WorstDD% | ΔWorstDD |
|---|---:|---:|---:|
| baseline_breadth | 63.4 | 62.6 | — |
| count_thr1 | 62.2 | 62.0 | -0.6pp |
| count_thr3 | 63.6 | **59.2** | **-3.4pp** |

Smoke result LOOKED like a -3.4pp DD win at thr3. But N=80 is at the noise
floor edge (per known-issues.md MC Noise Floor: "DD signal reliable at
N≥300, ±3pp inter-seed"; at N=80 inter-seed noise is ~±5-6pp).

### Phase B (N=300 × 22-now) — null

| config | CTP% | PTP% | CTrd | WorstDD% | MeanDD% | ΔWorstDD vs baseline |
|---|---:|---:|---:|---:|---:|---:|
| baseline_breadth | 63.40 | 60.30 | 1919 | **62.60** | 48.40 | — |
| count_thr1 | 62.20 | 60.10 | 1979 | 63.70 | 46.90 | +1.10pp |
| count_thr2 | 63.00 | 59.90 | 1956 | 63.80 | 47.00 | +1.20pp |
| count_thr3 | 63.60 | 60.00 | 1924 | 64.70 | 48.60 | +2.10pp |
| count_thr4 | 64.00 | 60.10 | 1899 | 69.70 | 49.50 | **+7.10pp** |
| count_thr5 | 64.20 | 60.10 | 1882 | 67.70 | 49.70 | +5.10pp |

Every threshold regresses on WorstDD. Per-trade quality (CTP%) gradient is
real and POSITIVE (thr4=64.0, thr5=64.2 vs baseline 63.4) — count-based
switch IS picking better-quality call days for tighter barriers. But the
DD impact dominates: tighter base barriers on quiet-tape days expose capital
to correlated DD events when those days happen to be early-stress signals.

P3 (DD primary): FAIL at every threshold.
P4 (per-window DD ≤+5pp): FAIL at thr4/thr5 (+7.1, +5.1pp).
T7 (compound non-regression OOM ±3): some pass, some fail — but moot given P3.

---

## Mechanistic interpretation — why cohort signal didn't translate

The count-based switch fires "stress" (wider TP/SL) on QUIET-tape days (few
signals firing). Those days aren't high-vol regimes — they're just sparse.
Widening TP/SL there doesn't capture more realized vol; it leaves capital
exposed during normal-vol moves that happen to be sparse.

Breadth-based switch correctly anchors stress on **broad market participation
collapse** = high-vol regime where wider TP/SL genuinely captures more of the
realized move distribution. Count is correlated with breadth but not
identical, and the residual (quiet but not stressed) days are exactly where
the count-based mode picks the wrong barrier.

The cohort signal at fixed barriers (Q5 demand TP=68.9% calls, 60.4% puts)
is a **quality signal** — it identifies days where signals are more likely
to win at current TP. But the right way to USE that signal is alloc-up on
those days (more capital on high-quality cohorts), NOT to flip the regime
switch.

---

## Lesson (add to known-issues.md "WHAT NOT TO DO")

**Don't swap `BREADTH_THRESHOLD` for a same-side-count-based switch.**
Cohort z>3 at fixed barriers does not imply the cohort variable is a regime
classifier. Quiet-tape days are sparse, not stressed; widening TP/SL there
hurts DD by exposing capital during normal-vol moves. Same general lesson
as F3F call recal NULL (2026-05-08) and BSD NULL (2026-05-06/07): cohort
z>3 is necessary but not sufficient for portfolio recalibration.

If the queue-depth cohort signal IS to be used, the natural mechanism is
**alloc scaling on high-demand days** (more capital deployed when more
high-quality cohorts are stacked), NOT regime classification.

---

## Recommended follow-ups (low priority)

- **Same-side count alloc scaler** (instead of regime swap): on
  high-same-side-count days, scale tier_alloc UP. Captures the queue-depth
  signal as a sizing variable, not a barrier variable. Untested.
- **breadth × count joint regime gate**: keep breadth-based stress switch,
  but on breadth>40 days where same-side count is in Q5, EXPAND alloc.
  Targets the specific 71.26% TP cell. Untested.

Both are speculative — neither has the cohort z-evidence the original swap
hypothesis had. Defer until other priorities exhaust.

---

## Artifacts

- `experiments/dynamic_tpsl/build_features.py` — feature builder
  (15,525 peaks, .cache/experiment_data/dynamic_tpsl_v46_5y.parquet, 0.3 MB)
- `experiments/dynamic_tpsl/cohort_analysis.py` — quintile splits +
  breadth-marginal check
- `experiments/dynamic_tpsl/confound_check.py` — same-side vs cross-side
  regime test
- `experiments/dynamic_tpsl/smoke_mc.py` — N=80 × 22-now smoke
- `experiments/dynamic_tpsl/phase_b_n300.py` — N=300 × 22-now sweep over
  STRESS_COUNT_THRESHOLD_CALL ∈ {1,2,3,4,5}
- `experiments/dynamic_tpsl/logs/` — all run logs

## monte_carlo.py wiring disposition

Added env-overridable `STRESS_MODE` (default 'breadth' = production behavior)
and `STRESS_COUNT_THRESHOLD_CALL` (default 5, only consulted when
STRESS_MODE='count'). Wired through `is_stressed()` and `precompute_outcomes`.
**Default OFF behavior is byte-identical to pre-experiment.** Reversion is a
single revert of monte_carlo.py if user prefers clean state. Pattern matches
existing `REALLOC_STRATEGY` research knob (also default OFF).

---

# 15 DTE Put Execution-Floor Formula Pass (2026-05-10)

**User constraint.** TP/SL in the 10-15% premium range is intraday noise for
15 DTE options. Treat this as non-actionable, not as a Bayesian candidate band.

**Harness change.** Added experiment-local `formula_bayes.py` and
`formula_mc_smoke.py` support for a 15 DTE execution floor:

- `EXEC_TP_FLOOR_15DTE = 0.20`
- `EXEC_SL_FLOOR_15DTE = 0.20`
- anchored formulas scale from `max(shipped_tp, exec_floor)`, so current
  `PUT_TP=0.06` cannot force the optimizer back into micro-TP space.
- smoke harness can compare current baseline, fixed execution-floor baseline,
  and candidate-only put admission filters.

## Result A — Dynamic TP/SL above the floor is not the edge

Bayesian anchored fit with 20% TP floor collapsed to essentially fixed 20/20:

| sample | TP% | SL% | avg TP | avg SL |
|---|---:|---:|---:|---:|
| valid current 6/-20 | 67.22 | 32.70 | 6.00% | -20.00% |
| valid floor 20/-20 | 59.12 | 40.69 | 20.00% | -20.00% |
| valid formula | 59.12 | 40.69 | 20.03% | -20.12% |

MC smoke confirmed formula ≈ fixed 20:

| window | comparison | ΔDD | ΔPutTP |
|---|---|---:|---:|
| 22-now N=150 | formula 20 vs fixed 20 | -0.03pp | -0.01pp |
| 5y N=150 | formula 20 vs fixed 20 | -0.04pp | +0.01pp |

Against current 6% TP baseline, 20% floor loses on PutTP and usually DD:

| window | ΔDD | ΔPutTP |
|---|---:|---:|
| 22-now N=150 | +2.67pp | -4.56pp |
| 5y N=150 | +0.87pp | -6.24pp |

Interpretation: current 6% put TP is simulator-favorable micro-exit behavior.
Once the 20% execution floor is enforced, WR7/N/MFE/MAE does not discover a
useful continuous TP formula. Do not keep tuning put TP inside 10-20%.

## Result B — Admission, not TP, is the viable next lead

Candidate: admit only `put_mid`/`put_top` when derived pressure >= 0.60,
use TP=20%, SL=-20%.

| window | admitted puts | ΔDD vs current full 6% baseline | ΔPutTP |
|---|---:|---:|---:|
| 2022 N=150 | 697/3150 | -6.83pp | -9.88pp |
| 2023 N=150 | 562/2763 | -7.20pp | +2.40pp |
| 2024 N=150 | 168/2206 | -7.93pp | -2.81pp |
| 2025 N=150 | 291/2191 | -7.15pp | -1.85pp |
| 22-now N=150 | 1736/10716 | -4.82pp | -9.62pp |
| 5y N=150 | 1736/11112 | -2.97pp | -11.07pp |

Sparse-window caveat:

| window | admitted puts | ΔDD | note |
|---|---:|---:|---|
| 2021 N=150 | 0/511 | -1.28pp | put-disable, not put alpha |
| dip N=150 | 18/679 | -6.75pp | too sparse; PutTP=0 |

The lead is therefore **not** "raise PUT_TP to 20%". The lead is:

> If 15 DTE put exits must be outside execution noise, use WR7/N/MFE pressure
> as an admission/allocation filter. Low-pressure 15 DTE puts are probably not
> tradable with an actionable TP.

Next validation should be an admission/allocation sweep, not another fixed
TP grid. Required before ship consideration: N>=300 all-window validation with
a minimum-activity fallback for sparse regimes, plus comparison against simply
turning off low-pressure puts.
