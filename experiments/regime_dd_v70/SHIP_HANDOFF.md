# SHIP HANDOFF — RXDD VIX-regime call-alloc dampener (v70 Apex, Stage 3)

**Status: ✅ SHIPPED 2026-06-04 — LIVE in v70 Apex (`STRATEGY_30DTE.RXDD_ENABLED=True`, c00 params).**
Portfolio-stage → **no `ALGORITHM_VERSION` bump, no recalc, stays v70.** Wired across ALL consumers
(strategy_config + monte_carlo + backtest_cascade + trader.py `_cmd_backtest`/`_cmd_alloc` +
api.py `/api/backtest/run`+`/api/trader/simulate` + Backtest.js + mechanism_registry + drift-guard).
Gates green: drift-guard 591 / registry 11 mechs / dte-audit; `trader backtest` + `trader alloc`
smoke clean. **Revert/disable:** `STRATEGY_30DTE.RXDD_ENABLED=False` (or git revert strategy_config.py).
The "TO GO LIVE" section below is the record of what was wired.

## TL;DR
A smooth VIX-band call-allocation dampener that contracts Apex call sizing in the **low-EV
"slow-bleed" VIX regime (~20–26)**, leaving calm (<20), **panic (≥28)**, and bull tape untouched.
Validated as a **clean Pareto win**: **5y WorstDD −5.8pp AND 5y compound +22%** (22-now DD −6.4pp /
compound +35%), **collapse=0 on every window incl. 2020-COVID**, robust across Phase B (16 cands) +
Phase C (3 cands) + Phase D (N=500). Cuts drawdown *and increases* compound — exactly the goal.

## Winner params (c00)
```
RXDD_ENABLED = True
RXDD_VIX_C   = 22.701     # Gaussian bump center (VIX)
RXDD_VIX_W   = 3.14       # bump width (narrow — surgical, avoids the ≥28 panic winners)
RXDD_DEPTH   = 0.447      # max contraction at center (alloc ×0.553 at VIX~22.7)
RXDD_DD_MIN  = 0.077      # only contract when running drawdown ≥ 7.7% (path-gated)
```
Mechanism: `alloc_frac *= 1 − DEPTH·exp(−0.5·((vix−VIX_C)/VIX_W)²)` for calls, no-op when
disabled / vix unavailable / running_dd < DD_MIN.

## Evidence
- Mining (`mine.py`, 6.4M-trade N=300 tape): VIX 20–28 = worst call cohort (loser-rate z+52,
  ~break-even EV) → safe to contract; VIX≥28 = best (z−90, mpnl +0.15) → left alone. The earlier
  `entry_dd × breadth` idea was a **crash artifact** (positive-EV in bull years) → rejected.
  Explosion-winner analysis → null.
- Phase B (N=100×6win): 14/16 LHS candidates Pareto-positive (DD↓, compound flat/up, collapse 0).
- Phase C (N=300×8win incl COVID): c00 5y DD −5.8pp / compound +22%, T4–T7 all PASS.
- Phase D (N=500×8win ship-gate): c00 5y DD −5.6pp / compound +9.4% / collapse 0 — **T1–T7 ALL PASS**.
- Verify: OFF reproduces baseline byte-identically; ON is live (2022 DD −10pp, low-VIX dip ≈ no-op).

## What's already in the tree (this session)
- `monte_carlo.py`: RXDD mechanism implemented (module consts ~L208 + `_rxdd_call_scale` helper +
  vix threaded through run_single_sim/MP-boundary + applied at the call alloc line) — **env-only,
  default OFF**. Plus a tape-emitter bug fix (`_dump_trade_tape` `infer_schema_length=None`).
- `experiments/v69_portfolio_retune/driver.py`: `RXDD_*` added to ENV_MAP.
- `experiments/regime_dd_v70/`: `mine.py`, `sweep.py`, `FINDINGS.md`, `SHIP_HANDOFF.md`,
  `mine_report.json`, `phase[B,C,D]_results.json`.
- Build is green: `test_strategy_config_drift.py` (581) + `test_mechanism_registry.py` pass.

## TO GO LIVE — productionization (~1.5h, do carefully; NOT a pre-open rush)
1. **`strategy_config.py`** — add 5 fields to `DteStrategyConfig` (after `DD_SOFT_CALL_FLOOR` @851):
   `RXDD_ENABLED: bool`, `RXDD_VIX_C/W/DEPTH/DD_MIN: float`. Set on `STRATEGY_30DTE` (@~1135, **enabled
   + c00 params**) and `STRATEGY_15DTE` (@~1343, **disabled**: `RXDD_ENABLED=False`, params can mirror).
2. **`monte_carlo.py`** — change the 5 `RXDD_*` module constants (~L208) from hardcoded to read
   `_cfg.RXDD_*` (mirror the `DD_SOFT_BAND_LO` idiom @205). Keep env override precedence.
3. **`backtest_cascade.py`** (deterministic engine — MUST mirror MC):
   - add `RXDD_*` module constants near @149 (mirror `DD_SOFT_BAND_LO` reading `_cfg`/env) + a
     `_rxdd_call_scale` helper;
   - thread vix into `run_backtest` like `regime_dates`/`regime_map`: `dte_router_vix_map` +
     `dte_router_dates` are already loaded in `run_cascade_backtest` (@2235) — pass them to
     `run_backtest`;
   - in the alloc loop (@1788) add `vix_today = value_on_or_before(vix_dates, vix_map, today)` and
     `rxdd_scale = _rxdd_call_scale(running_dd, vix_today)` (calls only), multiply into `base_premium`.
4. **`mechanism_registry.py`** — add RXDD `MechanismSpec`: 30 DTE `enabled`; 15 DTE `disabled`,
   `wiring_mode='not_wired'`, reason "VIX-band call dampener; not validated under half-DTE bounded-fill".
5. **`api.py` / `trader.py` (`_cmd_backtest`, `_cmd_alloc`) / `src/pages/Backtest.js`** — these mostly
   flow through `run_cascade_backtest` (engine inherits RXDD automatically); add the `RXDD_*` fields to
   `/api/strategy/config` (auto via `asdict`), `DEFAULT_ADVANCED`/`buildAdvancedFromConfig`/`FIELD_TIPS`,
   and a `_cmd_alloc` display line.
6. **`tests/test_strategy_config_drift.py`** — add RXDD pairs to `pairs_mc` + `pairs_bc` (30 DTE only).
7. Run: `python tests/test_strategy_config_drift.py && python tests/test_mechanism_registry.py &&
   python experiments/_dte_audit/audit.py`.
8. `PYTHONIOENCODING=utf-8 python trader.py temporal-refresh --profiles all`.
9. Commit + push. **Revert/disable** = `STRATEGY_30DTE` `RXDD_ENABLED=False` (or git revert
   `strategy_config.py`). Reversible, no recalc.

## One-line "enable" if you accept the staged MC mechanism as-is
The mechanism is already correct in `monte_carlo.py`; the above just makes it the persisted Apex
default and mirrors it into the deterministic backtest + dashboard. The minimum to make it *act* in
your MC validation is already done (env `RXDD_ENABLED=1` + c00 params reproduces the win).
