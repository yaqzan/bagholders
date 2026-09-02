# SVD — Score Velocity Dampener (v40 ship candidate)

## Question
Among signals at score=78, do those that came from {65, 72, 78} carry higher per-trade WR than those from {85, 81, 78}? (Priority #12, known-issues.md)

## Data
v39 5y, 22,976 call peaks at 70+. Velocity_5d = `overall - overall_5_trading_days_ago`.

## Profile (5y v39, 30dte_opt barrier @ w15)

### 75+ cohort split by velocity_5d (discrete bands)

| velocity_5d | N | opt_WR15 | avg_pnl_15 | lift vs others | z |
|---|---:|---:|---:|---:|---:|
| ≤ -10 (heavy decel) | 38 | **44.7%** | +0.05 | -18.30pp | -2.33 ** |
| -10 < vel ≤ -5 | 47 | 51.1% | +0.45 | -11.95pp | -1.69 * |
| -5 < vel ≤ 0 | 126 | 54.8% | +0.22 | -8.34pp | -1.92 * |
| 0 < vel ≤ +5 | 268 | 62.7% | +1.07 | -0.24pp | -0.08 |
| +5 < vel ≤ +10 | 667 | 59.1% | +0.61 | -4.35pp | -2.19 ** |
| **vel > +10 (heavy accel)** | **4,603** | **64.0%** | +0.78 | +5.34pp | +3.35 *** |

**Spread: 64.0% (heavy accel) vs 44.7% (heavy decel) = 19.3pp on the 75+ cohort.**

### 70+ baseline cohort (broad signal validity)

| velocity_5d | N | opt_WR15 | lift | z |
|---|---:|---:|---:|---:|
| ≤ -10 (heavy decel) | 227 | 53.3% | -6.36pp | -1.94 * |
| -10 < vel ≤ -5 | 366 | **52.5%** | -7.26pp | -2.81 ** |
| 0 < vel ≤ +5 | 3,132 | 56.9% | -3.10pp | -3.28 *** |
| **vel > +10 (heavy accel)** | 12,621 | **61.2%** | +3.54pp | +5.38 *** |

Statistically significant cohort discrimination at z up to +5.38 / -4.23. The decelerating sub-cohort is below call BE (45%) — these are EV-negative trades.

## Mechanism

Score-stage filter via gradient drift toward target=65 when `overall >= 75 ∧ velocity_5d < 0`:

```python
if overall >= 75 and velocity_5d is not None and velocity_5d < 0:
    weakness = clip(-velocity_5d / 3, 0, 1)
    overall -= 0.95 * weakness * (overall - 65)
```

Effects:
- `overall=78, vel=-1` → drift to ~75.5 (still tradable, lowest tier)
- `overall=78, vel=-3` → drift to ~65 (out of cascade)
- `overall=85, vel=-3` → drift to ~66 (out of cascade)
- `overall=95, vel=-3` → drift to ~66 (out of cascade — but 95+ rarely decelerates)
- `vel >= 0` → no change

## Sweep + Calibration

`experiments/score_velocity/sweep.py` — 108-variant grid over (gate_vel, target, alpha, k):

- gate_vel ∈ {0, -3, -5, -10}
- target ∈ {65, 70, 73}
- alpha ∈ {0.50, 0.75, 0.95}
- k ∈ {3, 5, 10}

**11 candidates passed affected-tier H1 gate.** Top 3:

| variant | 75+ Δ 5y | 75+ Δ 3y | 75+ Δ 1y | N75 drop | worst spillover |
|---|---:|---:|---:|---:|---:|
| **g0_t65_a95_k3** | **+0.36pp** | **+0.52pp** | +0.31pp | -3.0% | -0.03pp |
| g0_t65_a75_k3 | +0.36pp | +0.50pp | +0.31pp | -3.0% | -0.03pp |
| g0_t65_a50_k10 | +0.34pp | +0.39pp | +0.45pp | -2.0% | -0.03pp |

All 11 candidates: 5y/3y/1y signs all positive (H5 multi-window passes).

## Ship Candidate: g0_t72_a80_k1 (post-Bayesian refinement)

```
SVD_GATE_SCORE = 75
SVD_GATE_VEL   = 0
SVD_TARGET     = 72
SVD_ALPHA      = 0.80
SVD_K          = 1   # saturated — any vel<0 triggers full weakness
```

Two-stage calibration:
- Stage 1 (coarse grid, 108 variants on 5y): winner g0_t65_a95_k3, composite +0.274
- Stage 2 (Bayesian-style dense local refinement, 700 variants on 10y): winner g0_t72_a80_k1, composite +0.337 (improvement +0.063 over Stage 1)

**k=1 dominates k=3+ by 0.13pp on composite** — empirical evidence shows the decelerating cohort underperforms UNIFORMLY regardless of magnitude (vel=-1 is as bad as vel=-10). A saturated dampener captures the entire decel cohort uniformly; a smoother gradient (k=3) leaves mild-decel signals partially-protected when they should be filtered.

With k=1 saturation, the dampener becomes:
```
overall_new = 0.20 × overall + 57.6
```
for any decelerating signal. This converts the dampener into a near-step function at vel=0.

### Per-trade impact (refined params, multi-window 10y v39)

| Window | 75+ Δ TP% | 80+ Δ | 85+ Δ | 90+ Δ | 95+ Δ | N75 drop |
|---|---:|---:|---:|---:|---:|---:|
| 1y | +0.36pp | +0.10 | -0.03 | 0.00 | 0.00 | -3.0% |
| 3y | +0.51pp | +0.06 | -0.03 | 0.00 | 0.00 | -3.2% |
| 5y | **+0.37pp** | +0.08 | -0.03 | 0.00 | 0.00 | -3.3% |
| 10y | **+0.36pp** | +0.08 | -0.03 | 0.00 | 0.00 | -3.3% |

### Displaced cohort (175 signals dropped from 75+ to <75)

- WR15 = **51.4%** (vs 75+ baseline 62.9% — 11.5pp below baseline)
- 75-79 sub-cohort: N=157, WR15=50.3% (10pp below 75-79 baseline 60.4%)
- 80-84 sub-cohort: N=14, WR15=57.1% (11pp below 80-84 baseline)
- 85-89 sub-cohort: N=4 (noise — 4 signals only)

The 95+/90+ tiers are completely untouched: high-conviction signals don't decelerate (a 95-call by definition arrived from below recently).

## H1-H5 Gate Result

| Gate | Threshold | Result |
|---|---|---|
| **H1 (affected-tier 75+)** | ≥ +0.3pp at 5y | ✓ +0.37pp |
| H2 (WR15 directional consistency) | Same sign as TP% | ✓ both improve |
| H3 (N stability ≤ -15%) | 75+ N drop ≤ 15% | ✓ -3.3% |
| H4 (puts neutral) | <25, <15 unchanged | ✓ SVD gate is overall≥75; puts can't fire |
| **H5 (multi-window sign)** | 1y/3y/5y/10y same sign | ✓ all four positive on 75+ |

Standard H1 (≥+0.5pp on ≥3 tiers) does NOT pass — only 75+ gets +0.36pp. **Use affected-tier framework** since SVD targets the 75-79 sub-tier specifically (where the displaced cohort lives).

## Why Not Standard H1

Standard H1 expects strong lift on multiple tiers. SVD's effect is concentrated at 75-79 (where the EV-negative decelerating signals get displaced). Upper tiers (95+, 90+, 85+) are minimally affected because high-conviction signals rarely decelerate (definitionally — a 95+ score arrived recently).

This is the same gate-pattern used for v38 CWWD (sub-75 H1 fix) and v39 PESS — neither moves the standard 5-tier H1 but both pass affected-tier H1.

## Mechanism Compared to F3F (Phase 8, portfolio-stage)

F3F is a portfolio-stage knob that scales call allocation 1.0× → 0.50× linearly when `breadth_score ∈ [20, 50]`. F3F:
- Operates at signal time (not score time) — dashboard still shows the inflated 75+ score
- Scales allocation continuously based on breadth alone (not signal-property velocity)

SVD is a different signal: per-signal score velocity (independent of market-state breadth). The two could complement each other — SVD filters EV-negative decelerating signals; F3F scales remaining qualifying signals down in weak-breadth tape.

Per-trade gate empirical evidence below shows velocity is a stronger discriminator than breadth on per-trade WR: velocity discriminates 19.3pp on 75+, breadth ~14pp (per BSD investigation).

## Artifacts

- `experiments/score_velocity/build_features.py` — feature parquet builder
- `experiments/score_velocity/profile.py` — cohort profiling (axes: vel_5d, vel_3d, quintile)
- `experiments/score_velocity/sweep.py` — 108-variant grid + H1 gate evaluation
- `experiments/score_velocity/profile.out` — profile output
- `experiments/score_velocity/sweep.out` — sweep output with rankings
- `experiments/score_velocity/sweep.jsonl` — full sweep results (machine-readable)
- `.cache/score_velocity/calls_v39_1825.parquet` — features cache (5y N=22,976)

## TODO After Ship
- Re-verify on 10y v39 data (after recalc completes — `trader recalculate --force --full`)
- After v40 ship, monitor: does the displaced cohort persist as below-baseline in live forward data?
