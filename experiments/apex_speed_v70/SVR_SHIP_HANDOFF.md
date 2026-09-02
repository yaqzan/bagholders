# SVR (semivol_r skew-bridge) entry-filter — SHIPPED 2026-06-05

Status: **SHIPPED** (portfolio-stage, NO `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`).
Default = **ENABLED on STRATEGY_30DTE / Apex**. The operationalization of the confirmed
option-skew alpha (`experiments/iv_skew/`) via the 10y MC-computable `semivol_r` bridge — the
ONE orthogonal-alpha lead from the 2026-06-04/05 apex-speed overnight.

## What it does
`semivol_r = std(downside 60d returns)/std(upside 60d returns)` (high = downside-heavy =
put-skew-like = CHEAP call). Cohort (10y): low svr (~0.5, euphoric/EXPENSIVE call) is the
WORST call cohort; very-high (~1.4, crash-mode) weak; middle (~0.7-1.25) sweet spot. The
filter is a smooth band-pass: full call alloc in [LO_FULL, HI_FULL], contracting linearly toward
`SVR_FLOOR` down to `SVR_LO_CUT` (euphoric) and up to `SVR_HI_CUT` (crash). Calls only; puts
unaffected. The Pareto winner is the **gentleband (c00)**:
`SVR_LO_CUT 0.50 / SVR_LO_FULL 0.70 / SVR_HI_FULL 1.25 / SVR_HI_CUT 1.65 / SVR_FLOOR 0.50`.

## Validation (Stage-3 MC, monte_carlo.py via driver.py, vs live Apex RXDD-on baseline)
- **Phase B** N=100x6: gentleband +0.106 compound, +0.122 bear, dd5y -2.7pp, collapse 0.
- **Phase C** N=300x8 incl COVID: gentleband Pareto (compound up + DD down), collapse=0.
- **Phase D ship-gate** N=500x8 incl COVID (`svrD_results.json`):
  - 5y WorstDD **73.2 → 67.4 (−5.8pp)** AND 5y compound **+28.6%** (med 413,972 → 532,466)
  - 22-now DD **72.9 → 67.3 (−5.6pp)**, compound **+40%**; dip DD −6.4pp; 2022 DD −0.8pp;
    2020_crash (COVID) DD **83.3 → 79.3 (−4.0pp)**
  - max annual DD regression **+1.4pp (2024)** ≪ T5 +5pp; **collapse=0 on EVERY window incl COVID**
  - Stage-3 T1–T7 all PASS (Pareto: DD↓ AND compound↑). Lone soft spot: 2024 median compound
    −18% (theoretical, far inside T7 ±3 OOM; offset by the 5y/22-now headline gains).
- **Live-engine smoke** (`run_cascade_backtest`, 2024-06→now, $50k, 75+): OFF $4.84M/DD 68.3%
  → ON $4.42M/DD **63.9% (−4.4pp)** — SVR fires, DD down as designed.
- Per-trade backing (prior hunt): option-P&L by svr quintile (75+) win 0.35→0.55→0.51→0.39
  (inverted-U); underlying-barrier apex15 +1.9pp z=+11.57 (10y N=394k). Orthogonal to the price score.

## Ship wiring (13-consumer, all DONE)
1. **LIVE feature** — `database/utils/semivol.py:compute_semivol_r(closes, idx)` (verified 10/10
   match to the `.cache/iv_skew/proxy_ledger.parquet` build). backtest_cascade computes it INLINE
   per signal from price history (works for live/future dates); MC uses the static cache
   `.cache/apex_speed_v70/semivol_map.parquet` (built by `build_semivol_cache.py`).
2. `monte_carlo.py` — SVR consts read `getattr(_cfg, ...)`; `_svr_scale` band-pass; `_svr_load`
   static cache; applied in `_try_fill_call`. (env override still works for sweeps.)
3. `backtest_cascade.py` — consts + `_svr_call_scale` + `compute_semivol_r` import + `TradeOutcome.
   semivol_r` field + per-signal stamp in `run_cascade_backtest` build loop + apply in fill loop +
   `cfg.get` override keys. (semivol_r travels WITH the outcome → the `compute_and_store_temporal`
   vacuum call applies SVR with no separate map; sidesteps the run_backtest-in-two-places gotcha.)
4. `strategy_config.py` — `SVR_*` on `DteStrategyConfig`; STRATEGY_30DTE (enabled, gentleband) +
   STRATEGY_15DTE (disabled, schema parity).
5. `mechanism_registry.py` — SVR `MechanismSpec` (30 enabled, 15 not_wired + reason).
6. `tests/test_strategy_config_drift.py` — 6 SVR pairs in pairs_mc + pairs_bc (drift-guard 603).
7. `trader.py` `_cmd_backtest` (consts+helper+stamp+apply) + `_cmd_alloc` (display line);
   `api.py` `/api/trader/simulate` (inline loop) + `/api/backtest/run` (cfg override keys);
   `src/pages/Backtest.js` (DEFAULT_ADVANCED + buildAdvancedFromConfig + FIELD_TIPS + send-keys).
8. Gate: drift-guard 603 + registry 12 + dte-audit all green; `trader backtest` + `trader alloc`
   smoke clean (SVR line shows; inline loop runs).
9. Stage 3 = portfolio-only: NO `ALGORITHM_VERSION` bump, NO recalc. **Follow-ups:** `temporal-
   refresh --profiles all` (queued) + research-pack rebuild for VersionCompare; **RESTART the backend.**

## Reversal
`STRATEGY_30DTE.SVR_ENABLED=False` (or env `SVR_ENABLED=0`). Env-gated OFF = byte-identical baseline.
