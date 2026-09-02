# Track B — Concentration grid × time-to-2x

Portfolio-stage research. **NO `ALGORITHM_VERSION` bump, NO `trader recalculate`,
NO scoring-code change.** Uses the existing **v74 Apex** score rows, 30 DTE,
calls-only.

## The question

Replace the production tier cascade with a **flat** sizing — *N* concurrent
positions × *alloc%* each, top-conviction-first, score ≥ 75 calls only — and
measure a metric the system has never tracked:

> **minimum time-to-2x ($50k → $100k), collapse-TOLERANT.**

Per (N, alloc%) cell, pooled across monthly-rolling start dates:

| Metric | Meaning |
|---|---|
| `median_days_2x` (+P25/P75) | calendar days to 2× **among paths that reach it** |
| `p_2x_before_50dd` | P(reach 2× **before** a 50% drawdown) |
| `p_2x_ever` | P(reach 2× ever within the window horizon) |
| `p_collapse` | P(collapse ≤ 20% of start) |
| `median_compound_pct`, `worst_dd_pct` | reference only |

`time-to-2x` is heavily **start-date dependent**, so the harness **rolls the
start monthly across the 10y history** and **pools all paths**. The metric then
means *"if I start at a random point, how fast"* — not *"if I start in 2024."*

## Files

| File | Role |
|---|---|
| `sweep.py` | the harness: builds the grid, rolls monthly start windows, launches `monte_carlo.py` per (cell × window) via env overrides, pools per-path arrays, computes the metric set |
| `test_metrics.py` | **synthetic unit test** of the first-passage / 2x-before-50%-DD / collapse / quantile / window-roll / grid-cap / env-build logic. ZERO MySQL, ZERO MC. Run it first. |
| `results/` | output JSONs (`sweep_<stage>.json`) + transient per-run sidecars (auto-deleted) |

## Run the unit test (now, no compute)

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/concentration_2x/test_metrics.py
```

Exit 0 = the metric core is correct. Every input is a hand-built path where the
answer is known by construction (e.g. a path that hits 2× at bar 40 **after** a
50%-DD at bar 10 must NOT count as "2x before 50% DD").

## The grid

- **flat** cells: N ∈ {1,2,3,4,5,7,10,14} × alloc ∈ {10,15,20,25,33,50}%,
  **gross-capped at 100%** (cells with N·alloc > 1.0 dropped) → **26 flat cells**.
- **cascade_ref**: production cascade ULTRA 20 / TOP 15 / MID 8 / LOW 3, MaxPos 14.
- Total: **27 cells.**

Flat cell wiring (per the R1 MC-surface spec): all four call tiers
(`TIER_ULTRA_OV=TIER_TOP_OV=TIER_MID_OV=TIER_LOW_OV`) set equal to alloc%,
`TIER_OVERFLOW_OV=0` (score < 75 disabled), `MAX_POSITIONS_OVERRIDE=N`,
`MAX_POSITIONS_CALL=N`, `MAX_POSITIONS_PUT=0` + put-tier allocs zeroed
(pure calls-only). Fill order is the engine's native top-conviction-first
(primary score ≥ 75 sorted by −score, ct_call promoted ahead).

## Engine surface (added to `monte_carlo.py`, all default-OFF / byte-identical)

The harness drives the existing override surface plus a small, additive,
env-gated instrumentation patch (portfolio-stage research infra, NOT scoring):

| Env | Purpose |
|---|---|
| `TIER_*_OV`, `MAX_POSITIONS_OVERRIDE`, `MAX_POSITIONS_CALL/PUT` | flat / cascade alloc + slot caps (pre-existing) |
| `WIN_START` / `WIN_END` / `WIN_LABEL` | **NEW** — arbitrary single window → enables monthly rolling |
| `STARTING_CASH_OV` | **NEW** — start capital; 2× target = 2 × this (default 50k) |
| `MC_RETURN_PATHS=1` | per-iter arrays `finals` / `dds` / `t1m_bars` / **`t2x_bars`** / **`t_50dd_bars`** |
| `MC_RESULTS_JSON` | machine-readable sidecar; **now carries the per-iter `paths` block when `MC_RETURN_PATHS=1`** |
| `MC_NO_DB_PERSIST=1` | never touch MySQL writes |

The two NEW per-iter milestones recorded in `run_single_sim`:
- `t2x_bar` — first trading-day idx where equity ≥ 2× start.
- `t_50dd_bar` — first trading-day idx where running drawdown-from-peak ≥ 50%.

Together they answer the **path-ordering** question (did 2× happen *before* the
first 50% DD?) that the pre-existing scalar `finals`/`dds` arrays cannot.
`t1m_bar` ($1M = 20× for a $50k start) was the wrong milestone for this metric.
All additions are no-ops unless the env flags are set — default MC runs and DB
payloads are unchanged.

> **Calendar conversion:** `t2x_bar` is a trading-day index; days-to-2x is
> reported in **calendar days** = `t2x_bar × 365.25/252 ≈ t2x_bar × 1.449`.

## Important interpretation note — the 5 Stage-3 DD levers stay ON

v74 Apex's 5 call-alloc dampeners (RXDD/SVR/MWDD/TVDD/BDIV) plus F3F and the
DD-soft band remain **active** during these runs — they multiply into
`alloc_frac` alongside the flat tier alloc. So a "flat N×alloc%" cell is
**concentration *within the live levered Apex sleeve***, not a pure unmodulated
flat allocation. This is the correct baseline (we test concentration on top of
the shipped strategy). The dead-hold stays ON (collapse-PREVENTING — never
disable it).

## Stages & commands

`smoke` is a tiny logic shakedown (2 cells, N=10, 1 window). `coarse` is the
full grid at N=100. `drill` re-runs frontier cells at N=500 (DD-signal noise
floor is N=300; compound swings 1.6–1.8× below N=500).

**Compute discipline:** all heavy runs go through the **task queue**, never raw
background. Today is a market day — heavy sweeps use `--window off_market` so they
never outrank the scheduled `trader update`. Always `--env PYTHONIOENCODING=utf-8`
(queue stdout-buffering + Windows cp1252 trap).

### smoke (tiny — may run inline, but prefer the queue)

```bash
trader queue submit --priority high --db light --cpu 4 --restartable \
  --dedup conc2x-smoke --reason "Track B concentration time-to-2x smoke" \
  --env PYTHONIOENCODING=utf-8 \
  -- python experiments/concentration_2x/sweep.py --stage smoke
```

### coarse (full grid, N=100) — quarterly start-roll to stay in budget

```bash
trader queue submit --priority high --db light --cpu 8 --restartable \
  --window off_market \
  --dedup conc2x-coarse --reason "Track B coarse: 27 cells x quarterly-roll x N=100" \
  --env PYTHONIOENCODING=utf-8 \
  -- python experiments/concentration_2x/sweep.py --stage coarse --step-months 3 --workers 8
```

`--step-months 3` ≈ 38 windows × 27 cells = ~1,026 MC runs. For the strict
**monthly** metric use `--step-months 1` (~113 windows × 27 = ~3,051 runs — much
heavier; run only after the quarterly coarse identifies the frontier).

### drill (frontier cells, N=500, monthly roll)

```bash
trader queue submit --priority high --db light --cpu 8 --restartable \
  --window off_market \
  --dedup conc2x-drill --reason "Track B drill: frontier cells x monthly-roll x N=500" \
  --env PYTHONIOENCODING=utf-8 \
  -- python experiments/concentration_2x/sweep.py --stage drill \
     --cells flat_n3_a25,flat_n5_a20,flat_n4_a25,flat_n2_a50,cascade_ref \
     --step-months 1 --workers 8
```

Pick `--cells` from the coarse frontier (best `p_2x_before_50dd` at acceptable
`p_collapse`, with the lowest `median_days_2x`).

### Watch / be alerted

```bash
trader queue wait <id> --timeout 6h     # run with run_in_background=true to be alerted on completion
```

## Output shape (`results/sweep_<stage>.json`)

```json
{
  "stage": "coarse", "n_iter": 100, "starting_cash": 50000.0,
  "horizon_days": 730, "step_months": 3, "n_windows": 38,
  "window_span": ["2016-06-01", "2026-04-15"],
  "cells": [
    {"cell": "flat_n3_a25", "n_paths": 3800, "n_reach_2x": ...,
     "median_days_2x": ..., "p25_days_2x": ..., "p75_days_2x": ...,
     "p_2x_ever": ..., "p_2x_before_50dd": ..., "p_collapse": ...,
     "median_compound_pct": ..., "worst_dd_pct": ..., "gross_pct": 0.75, ...},
    ...
  ]
}
```

## Holdout note

The calibration holdout lock (`CALIBRATION_CUTOFF_DATE`) governs **scoring-lift
fits**, not portfolio-stage backtests. This harness rolls start dates across the
**full** 10y history *including* the recent period on purpose — it is a
live-trading-evaluation backtest of sizing on frozen v74 score rows, the exact
context the lock exempts (`HOLDOUT_DISABLE=1`). No score formula is fit here, so
the cutoff does not apply.
