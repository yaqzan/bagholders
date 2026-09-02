# 15 DTE PUT_TP Stage 2 Transfer Test — SHIPPED

Generated: 2026-05-09

## Result

30 DTE's PUT_TP tightening does transfer to 15 DTE, but the optimum is much
tighter because 15 DTE has lower premium multiple and faster theta decay.

**Ship: `SHARED_OPTION.PUT_TP = 0.06` for 15 DTE** (30 DTE remains
`OPT_30DTE.PUT_TP = 0.14`).

## Method

The 30 DTE Stage 2 ship changed put TP from +35% to +14% premium. We tested
whether the same SL-tax/capital-recycling effect transfers to 15 DTE.

Script: `experiments/sl_tax_stage2_15dte/put_tp_transfer_gate.py`

Wiring:
- Added `PUT_TP_OV` / `PUT_SL_OV` env overrides to `monte_carlo_15dte.py`.
- Removed a stale hard-coded 15 DTE `PUT_TP_SIGMA = 0.903` override from
  `trader.py`; deterministic backtest now reads `STRATEGY_15DTE.PUT_TP_SIGMA`
  from `strategy_config.py`.

## N=300 Search

Initial candidates: `0.35`, `0.20`, `0.16`, `0.14`, `0.12`.

Only `0.12` passed in the first run, but it sat at the tight edge. A refinement
run tested `0.12`, `0.10`, and `0.08`. `0.10` and `0.08` passed; `0.12` was
unstable with a 2024 DD regression.

## N=500 Ship Gate

Final candidates: baseline `0.35`, plus `0.10`, `0.08`, `0.06`.

| PUT_TP | 5y DD | Delta DD | 22-now DD | 5y return | 5y PutTP | PASS |
|---:|---:|---:|---:|---:|---:|:---:|
| 0.10 | 80.46% | +1.02pp | 79.13% | +2.86e26% | 62.02% | NO |
| 0.08 | 78.68% | -0.76pp | 78.94% | +3.57e26% | 62.72% | YES |
| **0.06** | **76.71%** | **-2.73pp** | **77.10%** | +2.02e26% | **63.24%** | **YES** |

Baseline (`PUT_TP=0.35`) at N=500:
- 5y DD: 79.44%
- 22-now DD: 78.9%
- 5y PutTP: 49.56%
- 5y return: +1.38e8%

## Per-window WorstDD

| Window | 0.35 baseline | 0.10 | 0.08 | 0.06 |
|---|---:|---:|---:|---:|
| 2021 | 69.9 | 69.2 | 69.2 | **68.5** |
| 2022 | 79.0 | 81.0 | 79.9 | **78.7** |
| 2023 | 66.0 | 67.3 | 70.0 | 68.0 |
| 2024 | 82.7 | 72.9 | 70.7 | **69.5** |
| 2025 | 71.2 | 67.4 | 68.4 | 68.1 |
| dip | 68.3 | 64.4 | 69.7 | 68.7 |
| 22-now | 78.9 | 79.1 | 78.9 | **77.1** |
| 5y | 79.4 | 80.5 | 78.7 | **76.7** |

`PUT_TP=0.06` is the best DD candidate: lowest 5y DD, lowest 22-now DD,
highest put TP rate, and no annual DD regression over +5pp.

## Mechanism

At 15 DTE:

`K = 0.06 * PREMIUM_MULT / DELTA = 0.06 * 1.29 / 0.50 = 0.1548 sigma`

This is a very tight target. It does not try to capture a full directional move;
it captures the first put-side dip and recycles capital quickly. Under the
bounded-fill MC, this reduces correlated put-side DD and improves 5y PutTP from
49.56% to 63.24%.

## Ship Notes

- Portfolio/barrier-stage only. No scoring change, no AlgorithmVersion bump, no
  recalc.
- `trader temporal-refresh` completed after ship.
- Drift guard PASS after editing config and removing the stale trader.py
  hard-coded fallback.
- 30 DTE unchanged at `PUT_TP=0.14`.

