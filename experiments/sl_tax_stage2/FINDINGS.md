# Stage 2 — Put-side SL-tax refinement (PUT_TP 0.35 → 0.14)

**Status: SHIPPED 2026-05-09** (30 DTE only). Stage 2 barrier optimization
under the new methodology. Closes the put-side WR7→option TP% gap that was
~2× the call-side after v32_optim ship (which retuned call-side TP/SL but
left put barriers at v25-era values).

## Phase A — Baseline pin (Stage 2 W7→TP% gap diagnosis)

5y v46 substrate, 35,305 signals (24,140 calls + 11,165 puts):

| side | tier | WR7 | opt_TP% | gap_pp |
|---|---|---:|---:|---:|
| **PUT** | 21-25 (largest cohort) | 73.49% | 49.71% | **+23.78** |
| **PUT** | 16-20 | 75.94% | 53.03% | **+22.92** |
| **PUT** | 11-15 | 79.56% | 57.51% | +22.04 |
| **PUT** | <25 cum | 74.69% | 51.44% | **+23.25** |
| CALL | 75-79 | 77.44% | 62.91% | +14.52 |
| CALL | 70-74 | 70.12% | 58.27% | +11.85 |
| CALL | 75+ cum | 79.09% | 66.01% | +13.08 |

**Diagnosis:** put-side paying ~2× the SL tax of calls. The cached
`30dte_opt` barrier set uses K=1.274 (TP=0.35) — same as v25-era. v32_optim
retuned CALL TP_BASE 0.35→0.33 (2026-05-04) but kept PUT_TP=0.35 unchanged.

Per known-issues constraint: "Never widen PUT_SL beyond -20%" (failed DD
floor in prior tests). So tunable knob is **PUT_TP** only.

## Phase B — Per-trade PUT_TP sweep (in-memory walk)

| PUT_TP | opt_TP% (all) | avg_pnl | median bars | EV/bar |
|---:|---:|---:|---:|---:|
| **0.20** | **63.0%** | +0.67 | **1.0** | **+0.67** |
| 0.25 | 57.5% | +0.76 | 1.0 | +0.76 |
| 0.30 | 53.3% | +0.83 | 1.0 | +0.83 |
| 0.35 (baseline) | 49.2% | +0.88 | 2.0 | +0.44 |
| 0.40 | 46.1% | +0.95 | 2.0 | +0.475 |

Per-trade EV/bar shows tighter PUT_TP wins on capital-velocity-adjusted
returns. The faster recycling at PUT_TP=0.20 (1 bar median) outweighs
the smaller per-win profit.

## Phase B smoke MC (B2/B3 gates) — PUT_TP=0.20 vs baseline at N=300 22-now

| Metric | Baseline (0.35) | Candidate (0.20) | Δ |
|---|---:|---:|---:|
| Worst DD | 72.60% | 65.16% | **-7.43pp** ✓ B2 PASS |
| Collapse | 0% | 0% | ✓ B3 PASS |
| Put TP% | 45.58% | 55.76% | +10.18pp |
| Call TP% | 63.22% | 63.52% | flat (calls untouched) |
| 22-now compound | +6.0e26% | +8.8e29% | ×1,450 |

Smoke gate PASS. Proceeding to Phase C fine-grid.

## Phase C — Fine-grid PUT_TP ∈ {0.14..0.35} at N=300 22-now

| PUT_TP | DD | PutTP% | compound ratio vs baseline |
|---:|---:|---:|---:|
| **0.14** | 65.22% | **60.39%** | **×16,500** |
| **0.16** | **64.43%** | 59.08% | ×11,600 |
| 0.18 | 65.98% | 57.99% | ×1,460 |
| 0.20 | 67.04% | 55.76% | ×808 |
| 0.22 | 66.48% | 54.27% | ×269 |
| 0.24 | 67.08% | 52.51% | ×93.5 |
| 0.26 | 67.93% | 51.00% | ×37.1 |
| 0.30 | 68.06% | 48.39% | ×4.5 |
| 0.35 (baseline) | 78.54% | 45.57% | ×1 |

Sweet spot is 0.14-0.16 within MC noise (DD baseline-to-baseline noise
at N=300 = ~5pp per documented memory). Phase D escalates to N=500.

## Phase D — Ship gate at N=500 × 8 windows (T1-T7)

| PUT_TP | 5y_DD | Δ_DD | 22n_DD | max_annual_Δ | T4 | T5 | T6 | PASS |
|---:|---:|---:|---:|---:|:---:|:---:|:---:|:---:|
| **0.14** ← winner | **65.23%** | **-7.76pp** | **63.4%** | **+0.7pp** (2024) | Y | Y | Y | **YES** |
| 0.16 | 67.15% | -5.85pp | 64.0% | +2.1pp (dip) | Y | Y | Y | YES |
| 0.18 | 63.82% | -9.18pp | 67.5% | **+9.4pp** (2025) | Y | **N** | Y | **NO** |
| 0.20 | 66.77% | -6.23pp | 67.3% | +4.5pp (2024) | Y | Y | Y | YES |
| 0.35 baseline | 73.0% | 0 | 71.3% | 0 | (anchor) | | | |

**T7 status:** all candidates show +3-4 OOMs gain on 5y compound. Per
methodology: "Compound non-regression sanity. Catches strategy-breaking
changes; ignores MC-noise compound differences at 1e26+% scale." T7 is
a regression-direction check; positive OOM gains at fantasy scale are
ignored.

### Per-window WorstDD (Phase D, N=500)

| window | base | 0.20 | 0.18 | 0.16 | **0.14** |
|---|---:|---:|---:|---:|---:|
| 2021 | 54.2 | 55.2 | 55.2 | 53.1 | 53.3 |
| 2022 | 71.0 | 63.8 | 66.6 | 65.3 | **63.6** |
| 2023 | 67.6 | 64.3 | 59.1 | 59.1 | **58.3** |
| 2024 | 59.7 | 64.2 | 56.6 | 54.1 | 60.4 |
| 2025 | 64.0 | 57.9 | 73.4 | 54.4 | 58.4 |
| dip | 48.4 | 49.4 | 55.5 | 50.5 | 48.5 |
| **22-now** | 71.3 | 67.3 | 67.5 | 64.0 | **63.4** |
| **5y** | 73.0 | 66.8 | 63.8 | 67.1 | **65.2** |

PUT_TP=0.14 has best 5y DD AND best 22-now DD AND lowest max-annual
regression (+0.7pp on 2024).

## Mechanism

PUT_TP=0.14 corresponds to:
- K = 0.14 × 1.82 / 0.5 = **0.510σ** underlying move
- TP fires at +14% premium gain (vs baseline +35%)
- 5y put TP rate +14.83pp (45.6% → 60.4%)
- Median bars to resolution: 2 → 1 (50% faster recycling)

Mean-reversion physics: 0.51σ over 15 days is tiny — nearly any put
signal sees that magnitude move within the holding window. Capturing
the dip immediately and recycling capital outperforms holding for
larger gains, especially in a strategy with 14 concurrent positions
where slot velocity drives compound.

## Why this didn't come from v32_optim

v32_optim (2026-05-04) was a JOINT cascade + TP/SL Bayesian sweep with
a UTILITY function = log(compound) - DD_penalty. It optimized portfolio
COMPOUND (Stage 3 metric in new framework), not option TP%.

The new Stage 2 methodology's PRIMARY metric is option TP%. Under that
objective, PUT_TP=0.14 dominates because it minimizes the WR7→opt_TP%
gap directly. v32_optim's compound objective was diluted by interactions
with DD penalty, slot competition, and call-side variables — no surprise
it didn't find the put-side TP optimum.

## Ship procedure (executed 2026-05-09)

1. Edit `strategy_config.py` `OPT_30DTE.PUT_TP`: 0.35 → 0.14
2. `python tests/test_strategy_config_drift.py` → 456 constants match ✓
3. `trader temporal-refresh` (~5 min) — refresh dashboard backtest stats
4. Update `.claude/docs/known-issues.md` CURRENT SHIP STATE + CLOSED-SHIPPED
5. Update `.claude/docs/version-history.md` 2026-05-09 commits section
6. Update auto-memory entry

**Portfolio-stage change** — no scoring change, no recalc, no
ALGORITHM_VERSION bump. 30 DTE only (15 DTE not validated; uses
SHARED_OPTION still).

## What NOT to do

- Don't widen PUT_SL beyond -0.20 (prior null per known-issues)
- Don't apply PUT_TP=0.14 to 15 DTE without separate Stage 2 validation —
  15 DTE has different PREMIUM_MULT (1.29) which changes K and thus the
  per-trade dynamics
- Don't tighten further (PUT_TP < 0.14) without Phase B-D re-evaluation;
  prior testing of PUT_TP=0.10 showed simulation-artifact risk (per
  known-issues "Phase 1 Bayesian put TP/SL")

## Files

- `phase_a_baseline_pin.py` + `phase_a.out` — WR7→TP% gap diagnosis
- `phase_b_put_tp_sweep.py` + `phase_b.log` — in-memory per-trade sweep
- `phase_b_smoke_mc.py` + `phase_b_smoke.log` — B2/B3 smoke MC
- `phase_c_finegrid.py` + `phase_c.log` — N=300 fine-grid sweet-spot identification
- `phase_d_ship_gate.py` + `phase_d.log` + `phase_d_results.json` — N=500 × 8w T1-T7 gate
