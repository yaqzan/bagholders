# Calendar + Honest-Theta Standardization — ship plan (execute ONCE, after #93)

**Decision (2026-06-09):** standardize calendar-day hold + honest theta as the engine
standard, in ONE validated Stage-3 ship, at the OPTIMAL (DTE, hold) from task #93
(option curve) + #92 (dead-hold popout realism). No interim/throwaway number. No
`ALGORITHM_VERSION` bump (portfolio-stage). Re-bases the live Portfolio page once.

## The bug being fixed (root cause)
- `strategy_config.py:768`: `HOLD_DAYS: int  # trading bars to hard sell` (intent = trading bars).
- `monte_carlo.py` (default): holds `base_idx + 1 + HOLD_DAYS` = **15 trading bars** (~21 cal days);
  theta over `30`-as-**bars** (dishonest — should be ~21 cal days into a 30-cal option = ~-45%, not -29%).
- `backtest_cascade.py` (LIVE Portfolio engine): `deadline = signal_date + timedelta(days=HOLD_DAYS)`
  = **15 CALENDAR days** (~11 bars); theta also `bars_held`/`30`-as-bars (dishonest).
- So mc and bc were simulating **different hold lengths** AND both mispriced theta.
- drift-guard literally encodes the conflation: `('bc.HOLD_CALENDAR_DAYS', …, s.HOLD_DAYS) # 15 trading ≈ 15 calendar`.

## Target standard
- Unit = **calendar days** (options expire on calendar dates; theta is calendar). Canonical.
- Hold = `HOLD_CAL_DAYS` calendar days (value = #93 optimum; #89 showed monotonic↑ to ~27, plateau 24-27).
- Theta = honest: `cal_held = (fire_bar.date - signal_date).days`, `total_dte = NOMINAL_CAL_DTE` (30, calendar).
- DTE bought = `NOMINAL_CAL_DTE` (value = #93 optimum across {15,21,30,45,60}); premium ∝ √(DTE/30).

## Consumer checklist (the ship)
1. **strategy_config.py** — add to `DteStrategyConfig` (both STRATEGY_30DTE + STRATEGY_15DTE for schema-parity):
   `CALENDAR_HOLD: bool = True`, `HOLD_CAL_DAYS: int = <#93>`, `NOMINAL_CAL_DTE: int = 30`.
   Keep `HOLD_DAYS` (legacy/other refs) but it stops driving the hold.
2. **monte_carlo.py** — module knobs already exist (env-gated). Flip defaults to read from `_cfg`
   (CALENDAR_HOLD/HOLD_CAL_DAYS/NOMINAL_CAL_DTE), retain env override for experiments. Honest-theta
   branch + DTE premium/σ scaling already implemented & smoke-validated.
3. **backtest_cascade.py** — deadline already calendar (good). Port HONEST THETA into all call-path
   theta sites: `_option_aware_pnl` closure (TP/SL/hard), dead-hold walk (`high_pnl_k`/`open_pnl_k`/
   `close_pnl`/`last_pnl`), prem-stop `cur_pnl`. Helper: `_held(fire_idx) = (ph_rows[fire_idx].date -
   signal_date).days` when CALENDAR_THETA; `theta_dte = NOMINAL_CAL_DTE`. Add √(DTE/30) premium/σ scaling
   to match mc. Source `HOLD_CALENDAR_DAYS` from new `HOLD_CAL_DAYS` field.
4. **monte_carlo_15dte.py / backtest_cascade_15dte.py** — same theta port for parity (15 DTE is a
   separate strategy; its HOLD_CAL_DAYS ≈ #93-15dte or keep ~half-DTE). Wire fields for schema-parity.
5. **tests/test_strategy_config_drift.py** — add new fields to `pairs_mc`/`pairs_bc`/`pairs_mc15`/
   `pairs_bc15` + the schema-parity field-map (line ~659) + parity count. Update the stale
   `# 15 trading ≈ 15 calendar` comment on the bc.HOLD_CALENDAR_DAYS pair.
6. **mechanism_registry.py** — register if treated as a mechanism (or note portfolio-standard).
7. **api.py `/api/backtest/run`** — accept/echo the new fields; **src/pages/Backtest.js** — DEFAULT_ADVANCED + FIELD_TIPS.
8. **portfolio_engine.py** — no change (consumes bc); the strategy-fingerprint change auto-triggers
   the live-Portfolio re-qualification sweep on next `trader update` (expected, honest re-base).

## Validation gate (before declaring shipped)
- `python tests/test_strategy_config_drift.py` + `test_mechanism_registry.py` green.
- bc bit-exact check: a single-version window backtest reproduces expected (the docs cite bc≡MC
  single-version validation) — confirm calendar+honest path is internally consistent.
- Re-validate **dead-hold + SL collapse-safety under honest theta** (N=300×8, collapse must stay 0)
  — honest theta deepens losses; confirm the dead-hold still prevents collapse.
- Smoke: `trader backtest` + `trader alloc` show new hold/DTE.
- Then re-run `trader assess` (calendar-aligned) + `temporal-refresh --profiles all` + research-pack
  so dashboard/VersionCompare/live-Portfolio reflect honest numbers.

## Inputs still pending (fill from results)
- `HOLD_CAL_DAYS` (30 DTE): from #93 option-curve EV optimum + #92 popout-realism (how far toward
  expiry is execution-robust). #89 portfolio sweep leaned 24-27 (but leaned on dead-hold popouts → #92 gates it).
- `NOMINAL_CAL_DTE`: from #93 DTE sweep {15,21,30,45,60} (smoke hinted D30 ≥ D45 on EV).
- THEN re-measure the allocation/exposure lever on the honest engine (the "50% → more?" question).
