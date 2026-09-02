# Regime×DD / VIX-band Stage-3 investigation — v70 Apex (2026-06-04, overnight)

**Objective:** reduce Apex drawdown without reducing compound (a strict Pareto win),
per user request. Portfolio-stage (Stage 3) — no `ALGORITHM_VERSION` bump, no recalc,
reversible. Active substrate: v70 (`c70d16d22`) Apex profile (calls-only, wide −70% SL,
HOLD-to-day-15, 70-74 overflow @0.035, uncapped, MaxPos 14).

## Method

Generated an MC trade-tape on the live v70 Apex config (`MC_TRADE_TAPE=1
N_ITER_OVERRIDE=300 monte_carlo.py`, task #35, all 12 windows, 6.4M call trades),
then mined high-DD cohorts and explosion winners with a custom calls-only join of
market context (breadth / regime / VIX) by `entry_date`
(`experiments/regime_dd_v70/mine.py`). The same run printed the live-Apex N=300
per-window baseline.

**Engine bug fixed en route:** `_dump_trade_tape` (env-gated diagnostic in
`monte_carlo.py`) crashed on polars schema inference when a window's `ct` column is
`None` early and `ct_call` later (2021+). Fixed with `infer_schema_length=None` on
both `pl.DataFrame(...)` calls. Zero effect on any simulation result.

## Finding 1 — regime×DD (entry_dd × breadth) contraction is a CRASH ARTIFACT → rejected

The dd_ledger H3-style "lethal pocket" (`entry_dd × mid-breadth`) that looked strong on
crash-only partial windows **dissolved on the full 6.4M-trade tape** and inverts by regime:

| pocket = entry_dd[0.20,0.55) × breadth[40,55) | loser-rate lift | mean option pnl |
|---|---|---|
| 2020 / 2020_crash | 2.52 / 2.46 | **−0.55 / −0.72** (lethal) |
| 2022 / 2023 | 1.53 / 1.26 | −0.20 / −0.07 (bad) |
| **2021 / 2024 / dip / 5y / 22-now / 10y** | **0.76–1.08** | **+0.07 to +0.16 (positive-EV)** |

Contracting this pocket unconditionally would cut compound in the bull/normal years —
the documented "DD-mitigation is structural" trap. **Not shipped.** (This *confirms* the
honest-frontier doctrine: blunt drawdown gating doesn't Pareto-improve.)

## Finding 2 — explosion-winner analysis is NULL

Ranking seeds by `episodes.final_value`, top-decile runs are near-identical to the rest
in win-rate, tier-mix, entry breadth/VIX. The only whisper: top runs enter at marginally
*lower* drawdown. The 10× final-value spread is sequencing variance, not a learnable entry
signature. No mechanism.

## Finding 3 — VIX regime band is the one robust, Pareto-shaped signal → the ship candidate

Strongest single-axis cohort in the whole tape (calls, 6.4M trades, base loser-rate 0.300):

| VIX band | loser-rate | lift | z | mean option pnl |
|---|---|---|---|---|
| **20–28** (slow-bleed / topping) | 0.322 | 1.07 | **+52.6** | +0.027 (≈ break-even, low-EV) |
| ≥28 (panic / capitulation) | 0.241 | 0.80 | **−90.3** | **+0.153** (best — leave alone) |
| <20 (calm) | ~0.30 | ~1.00 | ~0 | ~+0.05 (neutral) |

Mean-reversion: calls bought in the elevated-but-not-panic regime underperform; calls
bought near capitulation pay. **None of the shipped levers use VIX** (F3F=breadth,
regime-slope=dead, DD-soft-band=DD). Surgically contracting the *low-EV* VIX 20–28 band
(leaving panic/calm/bull untouched) cuts exposure into the bleed regimes that build DD,
at minimal compound cost.

## Mechanism — RXDD (VIX-regime call-alloc dampener)

`monte_carlo.py`: smooth Gaussian-bump multiplier on call alloc at `_try_fill_call`
(`alloc_frac *= _rxdd_call_scale(dd, vix_today)`), gated to a no-op when disabled,
when VIX is unavailable, or when running drawdown < `RXDD_DD_MIN`:

```
rxdd_scale = 1 − DEPTH · exp(−0.5·((vix − VIX_C)/VIX_W)²)      (in [1−DEPTH, 1.0])
```

Env-overridable knobs `RXDD_ENABLED / RXDD_VIX_C / RXDD_VIX_W / RXDD_DEPTH / RXDD_DD_MIN`,
default OFF. VIX threaded via the existing router market-map loader, only loaded when
enabled. Wired into `driver.py` ENV_MAP for sweeps.

**Verify (N=300, 2022+dip):** OFF reproduces the baseline *exactly* (2022 74.0/+45.6,
dip 58.1/+181.3 = byte-identical to #35) → the 9-site edit is a true no-op when off.
ON (depth 0.35) cuts 2022 WorstDD −10.3pp and is a near-no-op on low-VIX dip (−0.9pp) —
mechanism live and directionally exact. Collapse stays 0.

## Phase B — LHS-16, N=100 × [2020_crash, 2022, 2023, 2024, dip, 5y]

Baseline (RXDD off): 2020_crash 83.0 / 2022 73.9 / 2023 71.5 / 2024 50.0 / dip 57.4 / 5y 76.3.

**~14 of 16 candidates cut 5y WorstDD with compound flat-or-up and 0 collapse** — Pareto
robustness across the search space. Top clean (collapse=0, worst-window compound ≥ −0.15 log):

| cand | params (VIX_C / W / DEPTH / DD_MIN) | 5y WorstDD Δ | avg compound Δ (log) | collapse |
|---|---|---|---|---|
| **c01** | 22.7 / 3.14 / 0.447 / 0.077 | **−5.1pp** | **+0.071** | 0 |
| c07 | 22.0 / 4.89 / 0.324 / 0.24 | **−6.3pp** | +0.036 | 0 |
| c08 | 24.0 / 5.16 / 0.418 / 0.177 | −4.5pp | +0.027 | 0 |
| c00 | 23.0 / 6.59 / 0.258 / 0.26 | −5.0pp | +0.025 | 0 |

The `DD_MIN` gate is load-bearing: the winners only contract the bleed-band *while already
drawn down*, which is why compound holds (even rises) while DD falls.

## Phase C — N=300 × 8 windows (COVID-inclusive) — DONE (task #38): CLEAN PARETO WIN

All 3 candidates pass; **winner = c00** (= B-c01): `VIX_C 22.701 / W 3.14 / DEPTH 0.447 / DD_MIN 0.077`.

c00 per-window vs baseline (N=300):

| window | WorstDD | median return | note |
|---|---|---|---|
| **5y** | 78.8 → **73.0** (−5.8pp) | 337,679% → **410,679%** (+22%) | T4 primary ✓ |
| **22-now** | 79.2 → **72.8** (−6.4pp) | 94,833% → **128,285%** (+35%) | |
| **2022** | 74.0 → **64.4** (−9.6pp) | +45.6% → **+86.1%** (+89%) | |
| 2024 | 50.0 → 46.8 (−3.2) | 6,042% → 5,826% (−3.6%, noise) | |
| dip | 58.1 → 52.9 (−5.2) | 181% → 180% (flat) | |
| 2023 | 72.0 → 72.4 (**+0.4**) | −17.7% → −15.2% (better) | only DD "regression", trivial |
| 2020 / 2020_crash | 83.3/83.0 → **unchanged** | unchanged | VIX in panic band (≥28), outside the bump — by design |

**Gate verdict (Stage-3 T1–T7):** T4 PASS (5y WorstDD −5.8pp ≫ +1pp bar) · T5 PASS (worst per-window
DD regression +0.4pp, ≪ 5pp) · T6 PASS (collapse=0 every window incl. 2020/COVID/22-now) ·
T7 PASS (compound *up*, not regressed). c02/c01 also pass (slightly more DD, less compound).

The COVID windows being untouched is the key safety property: c00's narrow Gaussian (W=3.14, center
22.7) contracts only VIX ~20–26 (slow-bleed), and VIX in the 2020 crash was in the ≥28 panic band
where the bump is ~0 — so the high-EV panic-entry winners are preserved and collapse stays 0.

## Phase D — N=500 × 8 windows ship-gate (task #39): CONFIRMED — T1–T7 ALL PASS

c00 at the formal N=500 gate (vs N=500 baseline): **5y WorstDD 78.8 → 73.2 (−5.6pp)**,
DD-focus-window avg reduction **+4.62pp**, avg compound delta **+0.094 (UP)**, worst-window
compound −0.036, **collapse=0 on every window** (2020, 2020_crash, 2022, 2023, 2024, dip,
22-now, 5y). Stable across N=100 (B) / N=300 (C) / N=500 (D): dd5y +5.8 → +5.6, comp
+0.091 → +0.094. **Not N-noise.**

Gate: T1 (Stage 1+2 frozen — portfolio-only) ✓ · T2 (N=500×8) ✓ · T3 (8 windows) ✓ ·
T4 (5y WorstDD −5.6pp) ✓ · T5 (worst per-window DD regression +0.4pp) ✓ · T6 (collapse=0) ✓ ·
T7 (compound up, within OOM) ✓.

## Decision: ✅ SHIPPED 2026-06-04 — live in v70 Apex

After the N=500 ship-gate confirmed (T1–T7 PASS), RXDD was wired across ALL consumers
(strategy_config `STRATEGY_30DTE.RXDD_ENABLED=True` + c00 params; monte_carlo + backtest_cascade +
trader.py `_cmd_backtest`/`_cmd_alloc` + api.py `/api/backtest/run`+`/api/trader/simulate` +
Backtest.js + mechanism_registry + drift-guard). All gates green (drift-guard 591 / registry 11 mechs /
dte-audit); `trader backtest` + `trader alloc` smoke clean. Portfolio-stage → no `ALGORITHM_VERSION`
bump, no recalc, stays v70. Reversible via `RXDD_ENABLED=False`. Ship record + per-consumer wiring:
`SHIP_HANDOFF.md`.

## Ship path (Stage 3, portfolio-only) — if a clean winner confirms

1. `mechanism_registry.py` — add RXDD `MechanismSpec` (30 DTE enabled; 15 DTE `not_wired`).
2. `strategy_config.py` — add `RXDD_*` fields to `DteStrategyConfig`, set on `STRATEGY_30DTE`
   (winner params); 15 DTE disabled.
3. `monte_carlo.py` — flip `RXDD_*` defaults to read `_cfg.RXDD_*` (currently inert hardcoded).
4. `backtest_cascade.py` — mirror the mechanism in the deterministic entry loop.
5. `api.py` + `trader.py` (_cmd_backtest/_cmd_alloc) + `src/pages/Backtest.js` — expose/apply.
6. `tests/test_strategy_config_drift.py` — add RXDD pairs; run drift-guard + `test_mechanism_registry.py` + `experiments/_dte_audit/audit.py`.
7. `trader temporal-refresh --profiles all`; commit; push. No `ALGORITHM_VERSION` bump, no recalc. Reversible via git revert of `strategy_config.py`.
