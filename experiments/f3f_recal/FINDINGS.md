# F3F Call-Side Recalibration — under v45/v46 substrate

**Status: NULL RESULT** (2026-05-08). Current production F3F call curve
`(FLOOR=0.50, LOW=30, THRESH=50)` is Pareto-optimal within MC noise on
the v46 substrate. Brd 20-30 per-trade WR advantage does NOT translate
to portfolio-stage alpha because correlated DD risk in low-breadth tape
exactly offsets the per-trade gain.

## Motivation

Current production F3F call curve: `FLOOR=0.50, LOW=30, THRESH=50` —
1.0 scale at brd ≥ 50, linear cut to 0.50 below brd 30.

Calibrated under v25-era contaminated breadth (pre-v45 universe included
~45 ETFs, including 6 leveraged 3x products that distorted advancing/declining
counts). v45 (2026-05-08, `56eb1f8`) cleaned the breadth universe to
sectored stocks only (727-728 issues). v46 (2026-05-08, `f274eb6`) added
the WVD-Wave score-stage modulator.

Question: post v45/v46, does the F3F curve still match empirical alpha?

## Profile findings (`profile_v46.py`)

5y v46 substrate, 24,136 call signals at overall ≥ 70 — ALPHA SIGNAL IS REAL:

| brd bin | N (calls) | WR15 (option-aligned 30dte_opt) | current F3F scale |
|---|---:|---:|---:|
| 10-20 | 184 | 57.61% | 0.50 |
| **20-30** | **1,060** | **64.15%** | **0.50** ← floored, but HIGHEST WR |
| 30-40 | 3,951 | 59.98% | 0.62 |
| 40-50 | 4,955 | 58.57% | 0.88 |
| 50-60 | 7,110 | 59.51% | 1.00 |
| 60-70 | 4,663 | 58.48% | 1.00 |
| 70-80 | 1,198 | 60.60% | 1.00 |
| 80-90 | 56 | 55.36% | 1.00 |

The current F3F call curve floors at exactly the breadth band where
empirical per-trade call WR is highest. Looks like the curve is
structurally inverted vs the alpha distribution.

## Stage 1 — N=100 × 8 windows, 26 Bayesian iterations

Tree-search per user instruction: 5 hand-designed candidates spanning the
call-side design space, then Bayesian iteration around the best regions.

### Stage 1 final ranking (top 8)

| rk | cfg | util | maxDD | 5y | 22n |
|---|---|---:|---:|---|---|
| 1 | `(0.75, 15, 70)` | +259.5 | 70.7% | +1.96e31% | +6.59e26% |
| 2 | `(0.50, 20, 50)` | +258.9 | 71.0% | +4.78e30% | +1.14e27% |
| 3 | `(0.50, 25, 50)` | +256.2 | 71.1% | +9.17e30% | +2.63e26% |
| 4 | **`C0_baseline (0.50, 30, 50)`** | +256.0 | 70.4% | +3.81e30% | +2.91e26% |
| 5 | `(0.50, 25, 60)` | +253.6 | 70.2% | +5.20e30% | +6.84e25% |
| 6 | `(0.65, 20, 70)` | +252.9 | 70.7% | +2.94e30% | +1.54e26% |
| 7 | `(0.40, 25, 50)` | +252.7 | 71.5% | +6.36e30% | +4.09e26% |
| 8 | `(0.50, 15, 70)` | +251.8 | 70.6% | +2.53e30% | +2.04e26% |

**Top 4 within 3.5 utility points — squarely within MC noise floor at
N=100.** Two structurally distinct directions tie:
- `(0.75, 15, 70)` — softer cut, wider transition zone
- `(0.50, 20, 50)` — same depth as baseline, tighter floor zone

Both increase brd 20-30 alloc vs baseline (where it floors to 0.50).

### Stage 1 lessons

- AMP variants (FLOOR > 1.0) consistently DD-blowout (+8 to +14pp DD
  vs baseline) regardless of LOW/THRESH. Per-trade WR advantage in low-brd
  band does NOT translate to portfolio safety — correlated risk dominates.
- DEEP CUT variants (FLOOR ≤ 0.40) with wide LOW (20-25) and THRESH 50
  give marginal DD improvement (-0.4pp) but crush compound (60× lower).
- SHALLOW CUT variants (FLOOR ≥ 0.85) are essentially "no cut": +8pp DD
  vs baseline.
- The brd 50-70 zone (where THRESH operates) IS doing real safety work.
  THRESH=50 vs 70 affects DD by 1-3pp.

### Wall-clock note

Each Stage 1 eval took ~280-330s at N=100. Surprise: the original
estimate of 22 min/eval at N=300 was wrong — wall-time scales with
per-worker iterations under MP, not total iterations. With ~10 cores,
N=100 → 300 increase is mostly absorbed by parallelism.

## Stage 2 — N=300 × 8 windows, top 5 candidates from Stage 1

Selection: top 5 by Stage 1 utility (ranks 1, 2, 3, 4=baseline, 5).
P1-P6 ship gate per `assessment-backtest.md`.

### Stage 2 results

| rk_s1 | candidate | 5y | 22n | maxDD | P3 | P4 | P5 | P6 | PASS |
|---|---|---|---|---:|:---:|:---:|:---:|:---:|:---:|
| 1 | bayes_016 `(0.75, 15, 70)` | +5e31 | +7e26 | 73.8% | N | **N** (2025 -45.6%) | Y | N | **NO** |
| 2 | bayes_026 `(0.50, 20, 50)` | +1e31 | +8e26 | 72.9% | Y | Y | Y | **N** (-1.1pp) | **NO** |
| 3 | bayes_008 `(0.50, 25, 50)` | +2e31 | +1e27 | 73.0% | Y | Y | Y | **N** (-1.2pp) | **NO** |
| 4 | **C0_baseline `(0.50, 30, 50)`** | +1e31 | +8e26 | **71.8%** | (Y) | (Y) | (Y) | (Y) | (anchor) |
| 5 | bayes_007 `(0.50, 25, 60)` | +4e30 | +3e26 | 70.3% | N | **N** (2021 -40.2%, 2025 -40.1%) | Y | Y | **NO** |

### Pareto curve at N=300

```
DD ↓  (better)              (worse)  →
70.3%  bayes_007       — less compound, slightly safer
71.8%  baseline        — middle of frontier
72.9%  bayes_026       — ~same compound as baseline, slightly worse DD
73.0%  bayes_008       — slight compound gain, worse DD
73.8%  bayes_016       — best compound, worst DD
```

**Baseline sits squarely on the Pareto frontier. No candidate strictly
dominates.** The "compound improvements" of bayes_026/008 at 3-sig-figs
precision are essentially identical to baseline within MC noise; the DD
differences (1.1-1.2pp) are real.

## Decision

**DO NOT SHIP.** F3F call curve at production parameters
`(FLOOR=0.50, LOW=30, THRESH=50)` is Pareto-optimal within MC noise on
v46 substrate. The empirical brd 20-30 alpha signal IS real at the
per-trade level (64.15% WR15 vs 58-60% baseline) but does NOT translate
to portfolio compound improvement once correlated DD risk is accounted
for.

This is consistent with the v44 ICH retune null and the v46 regime band
recalibration null in the same session. Three null results in a row in
the v45→v46 era for compound-recovery hypotheses suggests the current
scoring + portfolio stack is dense around its Pareto frontier.

## Why this matters (preserved as research finding)

**Per-trade WR ≠ portfolio safety on low-breadth tape.** When breadth is
low (20-30), individual call signals MAY be high-quality contrarian
mean-reversion setups (64% WR15), but they tend to fire SIMULTANEOUSLY
across many stocks. When 14 of those positions are open during a
broad-market reversal, the correlated drawdown is large. Cutting
allocation in low-breadth regimes is therefore safety-driven, not
alpha-driven — and it's correctly tuned at FLOOR=0.50.

This is structurally important for any future F3F or breadth-weighted
mechanism design: per-trade barrier WR cohort analysis is a NECESSARY
but not SUFFICIENT signal for portfolio recalibration.

## Next priorities (per handoff, recommended order)

1. **Cascade TIER_ALLOC retune** (1 day, portfolio-stage)
   - Current: `0.20/0.15/0.10/0.10` calibrated under v34
   - Two new score-stage mechanisms shipped since: v44 ICH + v46 WVD
   - Per-tier score quality has shifted; monotonic re-sweep could unlock
     1-3% compound at neutral DD
   - Pattern: existing `experiments/v32_optim/phase_b_cascade.py` Bayesian
     harness — same template

2. **Composite weight rebalance** (2-3 days, scoring-stage)
   - Current: 35% breadth + 35% VIX + 30% trend
   - Hypothesis: post-de-contamination breadth-VIX correlation dropped from
     0.71 → ~0.75-0.78 (cleaner orthogonality), so up-weighting breadth
     (45/30/25) may improve discrimination
   - Cost: needs recalc per variant — use Bayesian acquisition with N=8-12
     variants, ~3h each
   - Gate: H1-H5 per-trade

## Files

- `profile_v46.py` — profiling script that surfaced the (apparent) inversion
- `profile_v46.out` — full profile output (5y v46 substrate)
- `stage1_sweep.py` — Bayesian sweep harness (5 seeds + 21 bayes evals)
- `stage1_results.jsonl` — full Stage 1 eval log
- `stage1.log` — Stage 1 stdout log
- `stage2_validate.py` — N=300 P1-P6 gate validation harness
- `stage2_results.jsonl` — full Stage 2 eval log (per-window data)
- `stage2.log` — Stage 2 stdout log

## Lessons logged

- **Per-trade cohort WR is NOT a sufficient signal for portfolio
  recalibration.** Correlated risk in same-cohort tape can offset
  per-trade alpha exactly. Always validate via portfolio-stage MC at
  N≥300 before claiming a structural finding from cohort analysis alone.
- **MC wall-time scales with per-worker iterations, not total.** N=100 →
  N=300 is mostly absorbed by parallelism with ~10 cores. Future sweep
  budgets should plan against per-worker iter count, not nominal N.
- **Stage 2 N=300 reveals real DD differences that Stage 1 N=100 masks.**
  The bayes_016 candidate looked identical to baseline at N=100 (DD 70.7%
  vs 70.4%) but was 2pp worse at N=300 (73.8% vs 71.8%). Always escalate
  before any ship decision.
