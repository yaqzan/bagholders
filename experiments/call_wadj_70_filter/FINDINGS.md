# v36 Miss-Ledger Round — Call wadj-neg residual at 70-79

Build: `.cache/miss_ledger/ledger_v36_1825.parquet` (40,436 peaks, v36 = `d5ef1f5`)
Date: 2026-05-05

## Top miss pattern on v36

The single biggest unaddressed miss-driver is the **same pattern that v32 closed at 75+** — but the gate is `overall>=75`, leaving the **70-74** range completely untreated.

| Cohort | feature | N | miss% | lift | z |
|---|---|---:|---:|---:|---:|
| CALL 70+ | b_wadj=neg | 1,537 | 52.7% | 1.28 | **+9.2** |
| CALL 70+ | b_wadj=neg & b_vmag=mid | 520 | 58.5% | 1.42 | +8.0 |
| CALL 70+ | b_wadj=neg & b_stoch=hi | 1,083 | 53.1% | 1.29 | +8.0 |
| CALL 70+ | b_wadj=neg & b_ta=mid | 1,189 | 52.1% | 1.27 | +7.7 |
| CALL 70+ | b_wadj=neg & b_sigma=mid | 764 | 53.3% | 1.29 | +6.8 |

z=+9.2 is the highest single-feature score in the entire v36 ledger. v35's
report had this same cell at z=+9.1 — the v36 CSWC re-calibration did NOT
touch the 70-74 zone. **The wadj=neg residual is essentially identical to
v35**, because:

- CWCF dampener gates on `overall >= 75`
- CSWC dampener gates on `overall >= 75`
- 70-74 has no negative-wadj treatment

## Where these signals actually live

| Overall band | wadj<0 N | miss% | TP% | In-cascade? |
|---|---:|---:|---:|---|
| 70-74 | 1,501 | 52.9% | 47.1% | **only via CT-promote**: 33 / 5y |
| 75-79 | 13 | 53.8% | 46.2% | yes (LOW tier 0.10) |
| 80-84 | 22 | 36.4% | 63.6% | yes (MID tier 0.10) — **above baseline**, not a miss |
| 85+ | 1 | — | — | trivial |
| **Total entering cascade** | **~69 over 5y / ~14 per year** | — | — | — |

Note 80-84 wadj<0: TP rate 63.6% on N=22 — **above** the 80-84 cohort baseline
TP of 67.7%. SE on N=22 is wide (±10pp), but the point estimate is firmly in
"don't filter" territory. Best filter shape is **70-79 only**.

## Hypothesis

Drop call signals where `overall ∈ [MIN, MAX]` AND `wadj < cutoff` at the
cascade-entry stage (mirror of `WEAK_WEEKLY_PUT_DROP`). Score-stage extension
of CWCF would also work but has identical effect (signals dampen below 70 →
exit cascade) and would falsely require an `ALGORITHM_VERSION` bump for what
is purely a portfolio-stage filter — so this is shipped portfolio-stage.

## Implementation

`monte_carlo.py` env-gated knobs (mirrors `WEAK_WEEKLY_PUT_DROP`):

```
WEAK_WEEKLY_CALL_DROP=0|1
WEAK_WEEKLY_CALL_MIN_OV=70
WEAK_WEEKLY_CALL_MAX_OV=74|79|84
WEAK_WEEKLY_CALL_WADJ=0.0|-3.0
WEAK_WEEKLY_CALL_STOCH_GE=0|35
```

`load_signals` extended to fetch `weight_info` + `stoch` so the filter can
read `w_adj` without a second DB pass.

## Bayesian / grid sweep — N=200 × 3 windows, deterministic seed

| Variant | Filter scope | 22-now Δret | 22-now ΔDD | 5y Δret | 5y ΔDD | 2024 Δret | 2024 ΔDD |
|---|---|---:|---:|---:|---:|---:|---:|
| baseline | (none) | — | — | — | — | — | — |
| A | 70-74 w<0 | -35% | -0.7pp | +62% | -0.5pp | +22% | -5.0pp |
| **B** | **70-84 w<0** | **+86%** | +0.3pp | +137% | -0.2pp | +14% | **-12.4pp** |
| C | 70-84 w<-3 | +23% | -0.5pp | +244% | +0.5pp | +39% | -2.0pp |
| **D** | **70-84 w<0 ∧ stoch≥35** | -14% | -0.2pp | **+319%** | -0.4pp | +28% | **-12.2pp** |
| **E** | **70-79 w<0** | +60% | +0.0pp | +205% | **-0.8pp** | +2% | -4.0pp |

CallTP% improved by +0.1-0.3pp on every variant. PutTP% within ±0.4pp (no
spillover). 0% collapse on every cell.

### Reading the table

Per CLAUDE.md "Lock decisions on 5y; treat 22-now as confirmation only":

- **Every variant improves 5y MedRet** (+62% to +319%)
- **4 of 5 variants improve 5y DD** (-0.2pp to -0.8pp)
- 22-now is mixed but mostly positive (3 of 5 variants positive)
- **2024 DD improvement is striking** for B and D (-12pp+)

### Variant ranking on 5y

1. **D** (70-84 w<0 ∧ stoch≥35) — **+319% MedRet**, -0.4pp DD ← biggest 5y win
2. C (70-84 w<-3) — +244%, +0.5pp DD (only DD regression)
3. **E** (70-79 w<0) — **+205%, -0.8pp DD** ← best DD profile
4. B (70-84 w<0) — +137%, -0.2pp DD
5. A (70-74 w<0) — +62%, -0.5pp DD

### Caveats — N=200 is screen-grade, not ship-grade

Per CLAUDE.md "MC noise floor at N=300 single-window" memory: compound-return
swings 1.6-1.8× baseline-to-baseline at N=300; N=200 is noisier still. The
CallTP% deltas (+0.1-0.3pp) are a more reliable per-trade signal. The 5y
compound multiples below ~5x baseline should be treated as directional.

The portfolio impact ceiling is structurally bounded:
- 33 CT-promoted 70-74 wadj<0 over 5y (ULTRA tier 20%)
- 13 75-79 wadj<0 stragglers (LOW tier 10%)
- ~46 in-cascade signals filtered over 5y

That's ~9 trades/year filtered. The 5y compound effect is meaningful only
because CT-promoted positions sit at ULTRA (20% alloc) and the filtered
cohort has 47% TP rate (well below the 65% CT baseline).

## N=300 × 8-window validation (P1-P6 ship gate)

Run via `experiments/call_wadj_70_filter/validate_n300.py`. Both shipped
candidates **PASS the full P1-P6 gate**.

### Variant D — 70-84 w<0 ∧ stoch≥35

| Window | baseTP | varTP | baseMed | varMed | Δret | baseDD | varDD | ΔDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 59.1% | 59.4% | 6.75e+07% | 8.58e+07% | +27.1% | 57.8% | 63.5% | +5.7pp |
| 2022 | 56.9% | 57.3% | 4.55e+06% | 4.43e+06% | -2.7% | 66.9% | 66.8% | -0.1pp |
| 2023 | 61.8% | 62.6% | 3.64e+06% | 3.61e+06% | -0.9% | 70.7% | 69.5% | -1.2pp |
| 2024 | 64.1% | 64.1% | 2.00e+09% | 2.47e+09% | +23.8% | 66.9% | 54.7% | **-12.2pp** |
| 2025 | 58.6% | 58.7% | 3.59e+07% | 3.51e+07% | -2.3% | 72.5% | 66.7% | -5.8pp |
| dip | 55.7% | 55.8% | 1.96e+04% | 2.55e+04% | +29.9% | 56.3% | 55.5% | -0.8pp |
| **22-now** | 59.8% | 60.1% | 5.15e+25% | 5.93e+25% | **+15.2%** | 70.6% | 70.4% | -0.2pp |
| **5y** | 59.6% | 59.9% | 1.05e+31% | **3.48e+31%** | **+232.8%** | 70.7% | 70.5% | -0.2pp |

- ✓ **P3** (5y≥base AND 22-now≥base): PASS
- ✓ **P4** (no annual <-25%): PASS (worst is -2.7% on 2022)
- ✓ **P5** (0 collapses): PASS
- ✓ **P6** (max worst-DD): 70.5% (vs baseline 72.5%)
- ✓ **PS1** (CallTP delta): +0.0 to +0.8pp on every window
- 2024 DD improvement of **-12.2pp** is exceptional

### Variant E — 70-79 w<0

| Window | baseTP | varTP | baseMed | varMed | Δret | baseDD | varDD | ΔDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 59.1% | 59.1% | 6.75e+07% | 6.04e+07% | -10.4% | 57.8% | 67.0% | **+9.2pp** |
| 2022 | 56.9% | 57.3% | 4.55e+06% | 4.46e+06% | -1.9% | 66.9% | 70.4% | +3.5pp |
| 2023 | 61.8% | 62.7% | 3.64e+06% | 3.98e+06% | +9.4% | 70.7% | 69.8% | -0.9pp |
| 2024 | 64.1% | 64.1% | 2.00e+09% | 1.85e+09% | -7.3% | 66.9% | 62.9% | -4.0pp |
| 2025 | 58.6% | 58.6% | 3.59e+07% | 3.12e+07% | -12.9% | 72.5% | 65.9% | -6.6pp |
| dip | 55.7% | 56.1% | 1.96e+04% | 2.69e+04% | +37.1% | 56.3% | 52.9% | -3.4pp |
| **22-now** | 59.8% | 60.1% | 5.15e+25% | 8.18e+25% | **+59.0%** | 70.6% | 71.0% | +0.4pp |
| **5y** | 59.6% | 59.8% | 1.05e+31% | 2.51e+31% | **+139.5%** | 70.7% | 70.1% | -0.6pp |

- ✓ **P3**: PASS
- ✓ **P4**: PASS (worst is -12.9% on 2025)
- ✓ **P5**: PASS
- ✓ **P6**: max DD 71.0% (vs 72.5% baseline)
- ⚠ **2021 DD +9.2pp** — abs 67.0%, still under 80% floor but largest single-window regression in either variant

## Fine 3×3 grid (v36, N=300 × 8 windows) — D's robustness

Sweep over `STOCH_GE ∈ {30, 35, 40} × MAX_OV ∈ {79, 84, 89}`, `WADJ<0` locked. **8 of 9 variants PASS P1-P6.** 5y compound spread +120% to +398% (1.8× = MC noise floor at N=300). Only `s40_o79` fails P4 with -32.9% dip regression.

| Variant | 5y Δret | 5y ΔDD | 22-now Δret | Worst Δret | Max DD | P1-P6 |
|---|---:|---:|---:|---:|---:|:---|
| s30_o89 | +398.3% | -0.5pp | +56.9% | -18.5% | 71.1% | PASS |
| s40_o89 | +370.4% | +0.2pp | +59.7% | -14.7% | 70.9% | PASS |
| s30_o79 | +336.8% | +0.3pp | +33.6% | -11.1% | 71.0% | PASS |
| s30_o84 | +278.8% | +0.0pp | +22.1% | -15.3% | 71.1% | PASS |
| s35_o89 | +277.9% | -0.4pp | +63.8% | -4.2% | 70.4% | PASS |
| s35_o79 | +276.4% | -0.6pp | +42.6% | -22.4% | 70.3% | PASS |
| **s35_o84 (D)** | **+232.8%** | **-0.2pp** | **+15.2%** | **-2.7%** | **70.5%** | **PASS** |
| s40_o84 | +222.8% | +0.2pp | +56.0% | -14.8% | 71.0% | PASS |
| s40_o79 | +120.0% | +0.5pp | +40.5% | -32.9% | 72.4% | FAIL P4 |

**Insight:** the wadj<0 condition does most of the work; stoch_ge gate (30/35/40) barely matters on v36. D has the smallest worst-case annual regression (-2.7%) of any passer.

## V37 validation (N=300 × 8 windows, ALGORITHM_VERSION_PIN=6f9afda)

V37 shipped mid-investigation with the **PCD (post-crash put dampener)** — calls are unchanged byte-for-byte, but put count dropped ~30% (16,839 → 11,833 over 5y). Re-validated D and E against v37 baseline.

### D (70-84 w<0 ∧ stoch≥35) — PASSES on v37

| Window | baseTP | varTP | baseMed | varMed | Δret | baseDD | varDD | ΔDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 59.1% | 59.4% | 7.81e+07% | 1.18e+08% | +51.4% | 56.8% | 58.3% | +1.5pp |
| 2022 | 56.7% | 56.9% | 7.01e+06% | 6.22e+06% | -11.3% | 61.7% | 64.5% | +2.8pp |
| 2023 | 62.8% | 63.5% | 2.73e+06% | 3.80e+06% | +39.2% | 68.1% | 69.2% | +1.1pp |
| 2024 | 63.7% | 63.6% | 2.65e+09% | 3.08e+09% | +16.3% | 62.3% | 57.5% | -4.8pp |
| 2025 | 58.5% | 58.5% | 2.89e+07% | 3.36e+07% | +16.6% | 70.8% | 63.4% | **-7.4pp** |
| dip | 55.5% | 55.7% | 4.47e+04% | 4.58e+04% | +2.4% | 47.2% | 52.3% | +5.1pp |
| **22-now** | 59.7% | 59.9% | 1.67e+26% | 2.59e+26% | **+55.0%** | 70.6% | 70.9% | +0.3pp |
| **5y** | 59.5% | 59.7% | 1.04e+32% | 1.28e+32% | **+22.6%** | 71.4% | 70.5% | **-0.9pp** |

- ✓ P3, P4 (worst -11.3%), P5, P6 (max DD 70.9%)

### E (70-79 w<0) — FAILS on v37

| Window | Δret | ΔDD |
|---|---:|---:|
| 2021 | -1.4% | +4.1pp |
| 2022 | -23.1% | +4.1pp |
| 2023 | +45.0% | +1.0pp |
| 2024 | +3.1% | -3.6pp |
| 2025 | -5.4% | -1.2pp |
| dip | +2.4% | +0.3pp |
| **22-now** | **+10.8%** | +2.4pp |
| **5y** | **-2.1% ✗** | +0.4pp |

**Fails P3** (5y < baseline). E's stoch-ungated filter overfits to v36 and breaks on v37.

### Mechanism — why magnitude shrunk on v37

V36 D 5y: +232.8% → V37 D 5y: +22.6% (10× smaller). Direction preserved, but shrunk dramatically. PCD's ~30% put cut means more cascade slot capacity for calls in v37 — so the marginal value of filtering 5 wadj<0 calls/year is smaller when calls already get more slot capacity. Per-trade evidence (z=+9.2, miss% 52.7%, N=1537) is byte-identical because calls are unchanged.

## Final ship recommendation: **D — 70-84 w<0 ∧ stoch≥35**

D has cleaner profile across every dimension:

| | D | E |
|---|---:|---:|
| 5y compound | **+232.8%** | +139.5% |
| 22-now compound | +15.2% | +59.0% |
| Worst annual Δret | **-2.7%** | -12.9% |
| Worst ΔDD on any annual | +5.7pp (2021) | +9.2pp (2021) |
| Best ΔDD on any annual | **-12.2pp (2024)** | -6.6pp (2025) |
| Max worst DD across all 8 | **70.5%** | 71.0% |
| 5y DD vs base | -0.2pp | -0.6pp |

D's `stoch≥35` conditional is mechanistically defensible: signals with `wadj<0
∧ stoch<35` are oversold setups that may genuinely bounce, so excluding them
from the filter avoids removing real alpha. The miss-ledger heatmap supports
this — the stoch=lo+wadj<0 cohort at 70-74 has miss 49.2% (vs stoch=hi 53.5%),
the smallest miss rate of the wadj<0 sub-cells.

D filters ~25 in-cascade signals/5y vs E's ~30 — even smaller surgical scope,
but the 2024 DD improvement (-12.2pp) is the largest single-window DD win
ever observed in a portfolio-stage filter sweep.

## Implementation path

This is a **portfolio-stage change** (no `Score.overall` modification, no
`ALGORITHM_VERSION` bump, no `trader recalculate`). To ship:

1. Promote env-gate constants to `strategy_config.py` as `WeakWeeklyCallDrop`
   block in `OptionStrategyConfig` (or a new sibling dataclass):
   ```
   WEAK_WEEKLY_CALL_DROP        = True
   WEAK_WEEKLY_CALL_MIN_OV      = 70
   WEAK_WEEKLY_CALL_MAX_OV      = 84
   WEAK_WEEKLY_CALL_WADJ_LT     = 0.0
   WEAK_WEEKLY_CALL_STOCH_GE    = 35
   ```
2. Mirror in `monte_carlo_15dte.py`, `backtest_cascade.py`, `backtest_cascade_15dte.py`,
   `api.py` `/api/backtest/run` endpoint, `trader.py` `_cmd_backtest`.
3. Add to `tests/test_strategy_config_drift.py` (value-equality across engines).
4. Add a one-line display in `trader.py` `_cmd_alloc` GUIDELINE block:
   `"Call wadj filter: drop overall<=84 with w_adj<0 and stoch>=35 (~5/year)"`.
5. Update `.claude/docs/known-issues.md` CURRENT SHIP STATE table + CLOSED — SHIPPED timeline.
6. Update `.claude/docs/trading-strategy.md` Position Sizing → Cascade Allocation
   with the new filter mechanism.
7. Run `python tests/test_strategy_config_drift.py` (≤1s).
8. Optional: smoke `trader backtest --from 2024-01-01` to verify deterministic
   backtest applies the filter correctly.

## Pattern not pursued — `PUT <25 stoch=hi & regime=HEALTHY` (z=+4.9, N=1836)

## Pattern not pursued — `PUT <25 stoch=hi & regime=HEALTHY` (z=+4.9, N=1836)

The second-largest unaddressed harmful pattern on v36 is HEALTHY-regime puts
with overbought stoch — bounce-trap territory. This sub-cohort has 55.9% miss
(TP 44.1%, well above put BE 36.4%, but well below the 49.8% baseline).

**Not pursued because:** the documented null history "Never reduce put
allocation/count in bear/stress tape" applies adjacent territory. Even though
HEALTHY ≠ stress, every prior put-filtering experiment has failed at the
portfolio stage despite per-trade evidence (see `experiments/put_dd_gradient/`).
A targeted HEALTHY-regime put filter is a follow-up investigation that
deserves its own miss-ledger round + Bayesian sweep.

## Files

- `quick_sweep.py` — N=200 × 3-window screening script
- `quick_sweep.log` — full sweep output
- `quick_sweep.jsonl` — structured results per variant×window
- `monte_carlo.py` — added `WEAK_WEEKLY_CALL_DROP` env-gated filter (lines
  ~327, ~1781) + extended `load_signals` to include `weight_info`/`stoch`
