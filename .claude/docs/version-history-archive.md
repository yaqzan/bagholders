# Version History — Archive (v60 and earlier)

Split out of `version-history.md` to keep the active doc lean. Newest-first, same format. Active/recent versions (v69 -> v74 + all 2026-06 portfolio releases) are in **[version-history.md](version-history.md)**.

## Active Version: v60 (`d4a3e9fec`) — 2026-05-19 (r054 SCW + 30DTE DD Call Cap)

Score-stage + 30DTE portfolio-stage ship. `ALGORITHM_VERSION` = `d4a3e9fec`; DB AlgorithmVersion row v60. Refines v50 Stoch Conviction Wave (SCW) into r054 smooth scalar form: base low-stoch/weak-weekly SCW stays, adds boundary relief, raw-stochastic relief, overextension taper. Sector overlay params present but `overlay_scale=0.0` — inactive in v60.

Portfolio: 30DTE only, global 14-slot pool, concurrent-call cap 12, DD soft-band `LO=0.35, HI=0.55, CALL_FLOOR=0.40` (`callcap12_dd035055f040`, r054 portfolio short sweep). Result: avg focus-window DD +0.90pp, no material max-DD worsening, log-return guardrail +0.054, call TP drift -0.02pp, ~7.4 fewer call trades. Stage-3 overlay on v60 scorer, not a separate score version.

Ship note: repair wrapper completed full score fill (1,721,162 updated / 32,765 skipped / 0 errors); resumed cleanly at `.codex/runs/v60_ship_resume_after_recalc_20260519_040602` after a watchdog killed its own post-recalc sanity query. Final coverage: 1,742,361 non-null v60 rows, 772 symbols, 2,658 dates, latest full date 2026-05-18 (768 rows). Research pack: `.cache/algorithm_versions/v60/research_pack/manifest.json`. W5 is REVIEW REQUIRED (95+ calls, p<=15, p16-20 below provisional floors) — accepted as a documented throughput trade-off, not a ship veto.

---

## v59 (`4fd7ffa9`) — 2026-05-15 (Daily Volume Authority Wave)

Score-stage ship via pointer `8fc70f34`; DB row v59. Replaces the brittle daily-volume conviction contribution: daily conviction supplies short-horizon impulse, weekly volume force governs how much authority it carries. High-quality weekly volume lets daily conviction matter; weak weekly authority fades it; high score tiers softened not cliff-filtered.

Recalc split after CLI hit MySQL timeouts; direct daily-component runner completed 993,426 updated / 5,504 skipped / 0 errors. Null shells removed -> 993,426 non-null v59 rows, 768 current-day rows, coverage 2020-12-31 through 2026-05-15. Tail refresh at `.codex/runs/v59_tail_refresh_20260515_1328`.

Tail refresh completed against v59 (`db:59`, `4fd7ffa9`): historic peaks, 30 DTE WR, 30 DTE TP, 15 DTE TP, both temporal calendars. Research pack: `.cache/algorithm_versions/v59/research_pack/manifest.json`. Legacy 5y WR7 utility: CALL N=2,478/utility=2,703.516, PUT N=4,545/utility=3,682.923, total N=7,023/utility=6,386.439. 22-now portfolio: 4,878 trades, call TP 65.35%, put TP 47.64%, max DD 69.79%. 2020 crash/cycle windows not ready (5y recalc starts 2020-12-31) — need off-hours 10y/full coverage before treating as production-equivalent.

Repair note: a stale cross-version sidecar recalc touched v57 rows in the latest 150-day window during ship; stopped and repaired from v57 pointer checkout `440ebdc1` (commit `e568b2f4`), restoring 80,278 v57 rows for 2025-12-16..2026-05-15, zero nulls.

Data repair: 2016-01-01+ market-data quality backfill at `.codex/runs/market_data_backfill_2016_afterhours_20260515_160557/backfill/summary.json` — 254,476 price rows repaired/refreshed, 103,833 changed, 310 symbols recalculated, 0 symbol errors. Pre-2016 cleanup deliberately deferred (`pre_scope_followup_summary.json`: 115,348 older rows / 272 symbols). Remaining follow-ups: targeted v59 coverage check on pre-v59 repair, retry/audit of 14 invalid/empty fetches, separate pre-2016 pass.

---

## v57 rollback baseline (`e568b2f4`) — 2026-05-15 (v58 Retune Rolled Back)

User-directed rollback: v58's WR15/utility gain did not translate to 2026-focused/broader-window 30DTE portfolio evidence. `ALGORITHM_VERSION` -> `e568b2f4`, `strategy_config.py` restored pre-v58 continuation-echo weights, 30 DTE stayed default. v59 shipped on top of this restored v57, not on v58.

Evidence: 2026 30DTE v57 `6713.3%/15.6% DD` vs v58 `4476.7%/24.8% DD`; 2020-now v57 `4.76e24%/91.7% DD` vs v58 `1.95e24%/94.7% DD`. v58 kept only as a research artifact for future continuation-echo salvage.

---

## v58 (`3cfc4dc2`) — 2026-05-13 (Continuation Echo Legacy WR7/N Retune, REVERTED 2026-05-15)

Score-stage ship via pointer `26317eba`; DB row v58, later reverted. Retuned the existing continuation-echo wave on top of v57 direct Market Wave scores. Kept hard-75 retention rule: sub-75 continuation lifts persist only when rounded destination reaches 75+.

Candidate = v57 rank-1 temporal echo params, selected under then-active WR7 utility + tradable CALL N gate.

| Bucket | dN | WR7 delta | Opt15 delta |
|---|---:|---:|---:|
| 95+ | -13 | +4.118pp | +9.804pp |
| 90-94 | -19 | +5.686pp | +4.153pp |
| 85-89 | +176 | +3.497pp | +4.817pp |
| 80-84 | +3,083 | +0.987pp | +1.870pp |
| 75-79 | +1,080 | -0.000pp | -0.238pp |

Utility-like score +4,864.805, +2,393 75+ CALL signals, +4,800.2 weighted incremental WR7 winners. A cleaner `raw_promote75_count_ge_10__ramp70_76` guard was found but depends on daily universe counts outside the per-symbol scorer, so not shipped; 75-79 softness accepted since ship gate at the time was legacy WR7 utility + N count.

Recalc at `.codex/runs/v58_recalc_20260513_084326`: `trader recalculate 1d` 1,514 updated/2 skipped/0 errors, then `--force` 1,033,561 updated/5,848 skipped/0 errors, plus historic-update/WR-TP assessment/30-15 DTE temporal stats. Verified: 1,039,409 score rows, 2020-12-31..2026-05-13, 15 assessment runs, 12 meta rows, 180 result rows, 1,694 historic events, 2 temporal rows.

N-floor soft check REVIEW: 95+ at 20.9 offered/year vs floor 26 (mostly inherited from v57). Same-window v57->v58 throughput: 95+ 114->112, 85-94 481->505, 80-84 1,399->1,597, 75-79 6,648->8,005. Accepted trade-off: flat tiny-N 95+ for large N expansion in 85/80/75 tiers with positive WR7 utility.

---

## v57 (`e568b2f4`) — 2026-05-13 (Direct Market Wave Score Transform)

Score-stage ship. Replaced v56 dual-wave Market Wave dampener with direct Market Wave score transform (`bayes_185`). Baseline for v58 retune. AlgorithmVersion v57, commit `e568b2f4`, pointer `440ebdc1`, coverage 2015-12-31..2026-05-13.

---

## v56 (`c6f384ab`) — 2026-05-12 (Market Wave Dual-Wave Dampener)

Score-stage ship via pointer `c979fc79`; recalc at `.cache/scoring_ships/v56_market_wave_dual_bayes150_20260512_210518`. Mechanism: sector-ETF Market-Wave dual-wave — crash echo dampens CALL signal-band scores, bull-repair thrust neutralizes PUT signal-band scores. Persists changed `Score.overall`; exposes `weight_info['sector_breadth_wave']` when active.

Candidate: v53 clean-baseline `marketwave_22now_compound_dd / bayes_150`, chosen after v55 `seed_099` showed real regime smoothing but worsened 30DTE Calendar DD and gave back compound. Replay from 2022-01-01 vs v53: full return 8.95e18% -> 1.66e19%, full max DD 73.57% -> 42.29%, trades 4,981 -> 4,881, 2025 tariff +123.73, other shocks +500.44, preserve -11.75. Single score-stage mechanism, not a stacked v55 dampener.

Follow-ups: confirm full recalc/assessment Calendar DD matches replay direction before final; re-optimize `bayes_150` with an explicit 2022-now yearly smoothness term + preserve/reference floor; keep production names generic (`SECTOR_BREADTH_WAVE_*`), ticker-case names stay in experiments/docs only.

---

## v50 (`b0c1954`) — 2026-05-11 (Conservative Stoch Conviction Wave)

Score-stage ship; DB active AlgorithmVersion v50. Conservative call-side timing dampener for low-stochastic/weak-weekly 70+ calls, wave-shaped penalty fading out at high conviction.

### Mechanism

```python
if overall >= 70 and stoch < 50 and wadj < 14:
    stoch_wave = clip((50 - stoch) / 50, 0, 1) ** 1.5
    weekly_gap = clip((14 - wadj) / 14, 0, 1)
    conviction_decay = clip((100 - overall) / 30, 0, 1) ** 6
    overall -= 8.0 * stoch_wave * weekly_gap * conviction_decay
```

Applied after earnings boost, before `weight_info`; exposes `scw_dampen` when penalty > 0.5.

### Ship Evidence

Candidate: `call_mp8_sp1.5_dc6_w14` from v46 actual-peak sweep. Formal gauntlet PASS W1/W2/W4/W5/W6; W3 minor 1y 70+ cumulative softness accepted (live allocation skips 70-74 calls; benefit weighted toward preserving higher-tier N/WR).

Clean 5y v50 recalc 2026-05-11, zero write errors: 1,032,046 updated, 5,846 skipped, v50 rows 542,638 -> 1,037,892, coverage 2020-12-31..2026-05-11. Queued 10y full recalc cancelled by user request; 5y is the ship baseline.

Latest 5y 30DTE WR assess (v50 run #335 vs v46 run #304):

| Bucket | v46 N | v46 WR7 | v50 N | v50 WR7 |
|---|---:|---:|---:|---:|
| 95+ | 22 | 95.5% | 23 | 95.7% |
| 90+ | 93 | 84.9% | 93 | 84.9% |
| 85+ | 341 | 81.5% | 348 | 81.3% |
| 80+ | 919 | 81.9% | 938 | 81.0% |
| 75+ | 2,155 | 79.0% | 2,171 | 78.7% |
| 70+ | 12,881 | 71.0% | 10,907 | 72.1% |

Verdict: acceptable ship. Desired 70+ cleanup materialized via large N reduction in the non-bought 70-74-heavy cohort; high-tier N stable/up; 80+ WR7 softer by 0.9pp (watch, not blocking).

### Files / Verification

- `strategy_config.py`: SCW constants in `ScoringConfig`/`SCORING`.
- `database/utils/scoring.py`: SCW transform in `compute_overall_score`.
- `ALGORITHM_VERSION`: `b0c1954`.
- Verification: `python -m py_compile strategy_config.py database/utils/scoring.py`, `python tests/test_strategy_config_drift.py`, `python tests/test_algorithm_version_sync.py`, clean 5y force recalc, assess tail, historic update, temporal refresh.

---

## Portfolio cleanup — DD circuit breaker retired (2026-05-11)

**No scoring change, no `ALGORITHM_VERSION` bump.** Removed binary `DD_CIRCUIT_BREAKER` from strategy config, MC, deterministic backtests, API, Backtest UI, drift guard, mechanism registry.

Rationale: March-June 2020 drawdown research showed the path-dependent portfolio stop is the wrong abstraction for crash avoidance — fires only after concurrent exposure is already open, invisible on score/dashboard surface. Replacement direction: sector ETF breadth thrust/reverse-thrust wave logic with echo decay, implemented separately after the Bayesian breadth sweep.

## Portfolio ship — SAW Put U-curve calibrated for 15 DTE (2026-05-09)

**No scoring change, no `ALGORITHM_VERSION` bump.** Second ship under the three-stage framework. Stage 3 only (DD MC) — Stage 1 (scoring) and Stage 2 (barriers) frozen at v46/current TP-SL-HOLD.

### Background

30 DTE SAW Put U-curve shipped 2026-05-08 (`fa1b099`), `Region B`: quadratic, mid=72/hw=18/floor=0.55/ceil=1.35/power=3.0. That 30 DTE optimum puts alloc into the brd 60-90 "bad zone" and amplifies at brd<20/brd>95 (mean-reversion alpha).

15 DTE was intentionally left disabled at 30 DTE ship time (registry `dte_15_status='disabled'`). Smoke test N=20 x 22-now confirmed the 30 DTE optimum does NOT transfer: applying Region B to 15 DTE gives 5y_dd +2.9pp (worse) — different alpha geometry from 15 DTE's smaller `PREMIUM_MULT=1.29` (vs 1.82), half-DTE `HOLD_DAYS=7` (vs 15), MaxPos=8 (vs 14).

### Ship config

```python
# STRATEGY_15DTE
SAW_PUT_UCURVE_ENABLED   = True
SAW_PUT_UCURVE_SHAPE     = 'sigmoid'    # vs 30 DTE 'quadratic'
SAW_PUT_UCURVE_MIDPOINT  = 70.0         # vs 30 DTE 72.0
SAW_PUT_UCURVE_HALFWIDTH = 25.0         # wider than 30 DTE 18.0
SAW_PUT_UCURVE_FLOOR     = 0.65         # milder than 30 DTE 0.55
SAW_PUT_UCURVE_CEIL      = 1.00         # NO amplification vs 30 DTE 1.35
SAW_PUT_UCURVE_POWER     = 2.0          # unused (sigmoid)
SAW_PUT_UCURVE_K         = 12.0         # sigmoid sharpness
```

### Key finding — pure contraction, no amplification

ALL top 10 Phase B candidates by 5y_dd had `ceil=1.00`: for 15 DTE, breadth-extreme amplification (ceil>1) HURTS at high breadth where puts fire; only mid-zone contraction reduces DD. 15 DTE's faster theta + smaller premium cushion means a larger position fraction at brd<20/brd>95 can't recover before the bar-7 hard-sell (30 DTE's longer hold absorbs gap-downs; 15 DTE doesn't). U-curve tail amplification is alpha for 30 DTE, drag for 15 DTE.

### Stage 3 Phase D ship gate (N=500 x 8 windows)

| Window | base_dd | cand_dd | dd delta | ret delta |
|---|---:|---:|---:|---:|
| 5y | 80.8% | 78.9% | -1.92pp | +64.8% |
| 22-now | 80.7% | 78.9% | -1.76pp | +101.8% |
| 2025 | 76.6% | 74.3% | -2.3pp | +19.7% |
| 2023 | 68.9% | 66.7% | -2.2pp | +5855% |
| 2022 | 80.9% | 79.8% | -1.1pp | +54.5% |
| 2024 | 75.3% | 75.2% | -0.1pp | +94.0% |
| dip | 63.5% | 63.1% | -0.5pp | -24.3% |
| 2021 | 68.0% | 67.7% | -0.3pp | +6.7% |

T4 (5y_dd <= baseline+1pp): PASS (-1.92pp). T5 (no annual >5pp regr): PASS (max 0.0pp). T6 (0% collapse): PASS every cell. T7 (5y compound +-3 OOM): PASS (OOM_delta=+0.22). All 8 windows DD reduces; 7-of-8 windows compound improves (only dip regresses -24% on low-N tail, DD also reduces there).

### Calibration trail

| Phase | Approach | N x windows | Variants | Result |
|---|---|---:|---:|---|
| B | LHS seeds + UCB Bayesian | 200x4 | 40 | top1 5y_dd 77.6% (sigm 70/25/0.65/1.00/4.0) |
| C | Drill top 5 | 300x8 | 5 | 5/5 PASS T-gates; top1 Δ5y_dd -1.78pp |
| D | Final ship gate | 500x8 | 1+baseline | top1 PASS all T-gates Δ5y_dd -1.92pp |

Utility is DD-dominated (corrected mid-sweep: initial T7 was bidirectional, fixed to one-sided downside-only; initial util over-weighted compound, fixed to dd_reward dominant). Phase B JSONL has 47 records incl. cross-process duplicates from earlier restarts; Phase C dedups by params and ranks by 5y_dd.

### Files modified

| File | Change |
|---|---|
| `strategy_config.py` | STRATEGY_15DTE: `SAW_PUT_UCURVE_ENABLED=False->True` + 7 ship values |
| `mechanism_registry.py` | `dte_15_status='disabled'->'enabled'`, removed `dte_15_wiring_mode`/`dte_15_reason`, added `ship_date_15='2026-05-09'` |
| `tests/test_strategy_config_drift.py` | removed transient "wired_neutral" assertions; +8 SAW pairs to `pairs_mc15`/`pairs_bc15`, 311 total checks |
| `monte_carlo_15dte.py` | 8 module-level env-overridable SAW constants + `_saw_load_sec_brd`/`saw_sec_brd_on_or_before`/`saw_put_ucurve_scale`; `_try_fill_put` applies `* saw_put_ucurve_scale(today)` |
| `backtest_cascade_15dte.py` | same wiring; `premium = alloc_frac * reg_scale * dd_scale * saw_scale * equity` |

Verification: `python tests/test_strategy_config_drift.py` (311 pass), `python tests/test_mechanism_registry.py` (9 mechs, 102 checks), `python experiments/_dte_audit/audit.py` (108 MC/BC scripts clean), `python trader.py alloc 50000 --strategy 15dte` (SAW ENABLED with ship config).

Research (untracked, `experiments/saw_put_ucurve_15dte/`): `sweep_phase_b.py`, `sweep_phase_c.py`, `sweep_phase_d.py`, `run_chain.py` (B->C->D driver), `ship_helper.py`, phase result jsonl/logs.

---

## Portfolio ship — CTSL Counter-Trend Score Lift, additive on CT_PROMOTE (2026-05-08)

**No scoring change, no `ALGORITHM_VERSION` bump.** First ship under the three-stage calibration framework (`process.md` + `assessment-backtest.md` 2026-05-08). CTSL is a score-stage continuous lift applied at signal-load time in `monte_carlo.py`/`backtest_cascade.py`/`api.py /api/trader/simulate`; stacks ADDITIVELY on `CT_PROMOTE` rather than replacing it.

### Three-stage calibration trail

| Stage | Method | Compute | Outcome |
|---|---|---|---|
| Stage 1 scoring sweep (WR7 gate) | Staged LHS (B100+C200+D50) on v46 generic-barrier parquet | ~30s | Winner tm=15/76, target=98.4/-0.13, alpha=0.56/0.83, p=2.82/0.99, floor/ceiling=74.7/27.9, snw=+0.75/-0.22, snp=2.27/1.68 |
| Stage 2 SL tax/barrier | Phase A baseline pin only (Phase B skipped — barriers already well-tuned; put SL locked -20% by DD floor) | ~3min | LOCKED: TP_BASE=33%, TP_STRESS=42%, SL_BASE=-27%, SL_STRESS=-40%, HOLD=15, BREADTH_THRESHOLD=40 |
| Stage 3 Tertiary portfolio MC | 3-config matrix (A/B/C) x N=500 x 8 windows on v46 | ~18min | Config B (stack) PASSES T1-T7; Config C (substitute) FAILS T4 at +2.20pp 5y DD. "Phased removal" goal rejected as NULL |

Stage 1 evidence (5y v46 generic barriers, affected cohort): W1 PASS, W2 PASS (call WR7=78.3%/WR15=84.1%/WR30=82.6%; put WR7=88.7%/WR15=92.1%/WR30=91.4%), W3 PASS (call 1y/3y/5y 77.8/78.4/78.3%; put 89.1/87.6/88.7%), W4 PASS (0 breaches, max regression -0.31pp), W5 technical fail (STALE H6 floor table — v46 baseline already below floor for top tiers, recalibration owed separately), W6 technical fail (inherited v46 anomaly: 80-84 baseline WR7=82.16% > 85-89 baseline=80.24%, not CTSL-introduced).

Stage 3 T1-T7 (N=500x8x v46):

| Gate | Config B (stack) | Config C (substitute) |
|---|:---:|:---:|
| T1 (Stage1+2 frozen) | PASS | PASS |
| T2 (N=500+) | PASS | PASS |
| T3 (8 windows) | PASS | PASS |
| T4 (5y WorstDD <= A+1.0pp) | PASS -0.40pp | FAIL +2.20pp |
| T5 (per-window DD <= +5pp) | PASS | PASS |
| T6 (P(collapse)=0%) | PASS | PASS |
| T7 (compound OOM +-3) | PASS | PASS |

Per-window DD vs A baseline: 2021 62.5%->63.2%(B)/55.6%(C); 2022 70.6%->69.7%/70.7%; 2023 72.2%->69.0%/68.9%; 2024 61.6%->62.3%/59.2%; 2025 64.0%->59.0%/60.1%; dip 49.8%->48.4%/52.1%; 22-now 73.1%->69.7%/69.6%; 5y 71.0%->70.6%/73.2% (C fails).

Mechanism: CT_PROMOTE was an accidental DD damper in bear-tape MC — force-promoting 70-74 ct_call to ULTRA (0.20 alloc) displaced higher-conviction 95+ signals from the 14-slot MaxPos pool, capping ULTRA exposure exactly when 95+ would lose more. Removing it (Config C) frees the cap, worsening 5y DD +2.2pp. Stacking CTSL on top (Config B) keeps the slot cap and adds CTSL's quality-aligned sort order: -0.40pp 5y DD net.

### Files / consumer wiring (13-consumer checklist per `deploy.md`)

| # | File | Change |
|---|---|---|
| 0 | `mechanism_registry.py` | New CTSL `MechanismSpec` (30 DTE enabled, 15 DTE not_wired); SAW Put U-curve registry `not_wired`->`wired_neutral` |
| 1 | `strategy_config.py` | 15 new `CTSL_*` fields in `DteStrategyConfig`; STRATEGY_30DTE per winner; STRATEGY_15DTE `CTSL_ENABLED=False` |
| 2 | `monte_carlo.py` | CTSL constants now read from `_cfg`; helpers `_ctsl_call_lift`/`_ctsl_put_dampen`/`_apply_ctsl_to_signals` wired into `load_signals`/`load_put_signals` |
| 3 | `monte_carlo_15dte.py` | verified NO CTSL trace |
| 4 | `backtest_cascade.py` | mirrored CTSL constants+helpers; applied end of `load_signals`/`load_put_signals` via `_replace()` |
| 5 | `backtest_cascade_15dte.py` | verified NO CTSL trace |
| 6 | `api.py` | `/api/trader/simulate` applies `_apply_ctsl_to_signals`; `/api/backtest/run` inherits via `run_cascade_backtest`; 9 CTSL fields in `params` response via `_bcfg.CTSL_*` |
| 7 | `trader.py _cmd_backtest` | new `CTSL_ENABLED` constant + apply blocks; fixed pre-existing latent bug (`_alloc_cfg.WEAK_WEEKLY_CALL_*` -> `_scfg_f3f`, from `c94e4ae` 2026-05-05) |
| 8 | `trader.py _cmd_alloc` | CTSL display line in GUIDELINE block |
| 9 | `tests/test_strategy_config_drift.py` | +14 CTSL pairs (30 DTE only); drift count 281 -> 295 |
| 10 | `src/pages/Backtest.js` | 9 new `ctsl_*` fields in `DEFAULT_ADVANCED`/`defaultsFromCfg` (sourced from `/api/strategy/config`, which auto-includes CTSL via `asdict()`) |
| 11-13 | `known-issues.md` + `trading-strategy.md` + `version-history.md` | this entry |

Verification: `tests/test_strategy_config_drift.py` OK (295 constants); `tests/test_mechanism_registry.py` OK (9 mechanisms, 102 checks); `trader alloc 50000` renders CTSL line; `trader backtest --from 2025-01-01 --capital 50000` runs clean (1,587 closed trades: 711 calls+876 puts, 38 ct_call/59 ct_put promotions, max DD 33.9%, TP rate 54.6%, +6.8pp above BE 47.8%).

Calibration: `experiments/ctsl/FINDINGS.md`. Stage 1 parquet: `.cache/ctsl/scores_v46_stage1_1825.parquet`. Stage 3 logs: `experiments/ctsl/stage3_*.log`.

---

## Infra ship — DuckDB barrier_cache mirror (2026-05-08)

Read-side perf ship. **No scoring change, no version bump.** `barrier_outcomes` cache gains a DuckDB read mirror at `.cache/barrier_outcomes.duckdb`; `peaks_to_swing_results()` uses it by default, SQLite stays canonical write target.

**Bench (`experiments/duckdb_spike/bench_focused.py`):**

| N peaks | sqlite | duck_native | speedup |
|---:|---:|---:|---:|
| 1,000 | 52.0s | 0.53s | 98x |
| 10,000 | 46.0s | 0.94s | 49x |
| 50,000 | (~230s extrap) | 2.82s | ~80x |

Correctness: 100 spot-checked records identical at N=1,000 across all 3 DuckDB backends. Production smoke test confirmed result-count parity forced-sqlite vs default-duck.

What changed: `CACHE_DUCK = .cache/barrier_outcomes.duckdb`; `rebuild_duck_mirror()` (atomic tmp+rename, closes held connection first for Windows lock; ~146s for 9.9GB -> 2.32GB); `_select_backend()` resolves env `BARRIER_CACHE_BACKEND ∈ {'auto','duck','sqlite'}` ('auto' uses duck when mirror exists and mtime >= SQLite source); `peaks_to_swing_results()` now dispatches to `_peaks_to_swing_results_sqlite()` (fallback) or `_peaks_to_swing_results_duck()`; `refresh_recent()`/`backfill_sets()` call `rebuild_duck_mirror()` at end (best-effort, degrades silently without duckdb); new CLI `python -m database.barrier_cache rebuild-duck`; `stats()` reports both backend states + staleness.

Unchanged: all writes (`upsert_rows`, `compute_outcomes_for_symbol`) still SQLite; `lookup_batch()` (no callers found) still SQLite; public `peaks_to_swing_results()` signature unchanged, all 4 callers pick up speedup transparently.

Reversibility: delete `.cache/barrier_outcomes.duckdb` -> auto-fallback to SQLite; or `BARRIER_CACHE_BACKEND=sqlite` for one run. Zero MySQL/schema changes.

Alternatives rejected: `duck_attach` (sqlite_scanner over existing file, zero migration) 0.76x — slower than baseline, killed. `duck_parquet` 73-58x but 20% slower than native and 2x file size (1.19GB vs 2.32GB) — native chosen. MySQL 5.7 InnoDB partitioning: blocked on FK drops, only ~5-15% recalc gain — skipped in favor of DuckDB.

Polars standardization survey: 131 files on polars vs 16 on pandas (pandas usages are at the yfinance ingestion boundary or completed research artifacts) — no active migration needed, closed as no-op.

On-demand bulk cache: new `database/bulk_cache.py` (`materialize_polars`, `chunked_query_by_year`) formalizes per-experiment parquet caching, documented in `process.md` "On-demand bulk cache pattern" — preferred over a global nightly snapshot (smaller, simpler, no during-day staleness).

Spike artifacts preserved: `experiments/duckdb_spike/{duck_cache.py,bench.py,bench_focused.py}`.

---

## Active Version: v45 (`56eb1f8`) — 2026-05-08 (Breadth ETF de-contamination)

Infrastructure ship, no scoring formula change. Version bump tracks the `Score.overall` shift from removing 45 ETFs from the production breadth universe; breadth-score formula, regime composite weights, all score-stage dampeners byte-identical to v44 — only the breadth aggregator input is cleaner.

### v45 — Breadth ETF de-contamination (ship 2026-05-08, `56eb1f8`)

**The bug.** `market_breadth._get_daily_breadth` selected ALL `PriceHistory` rows for target date with no ETF filter. ~45 ETFs in the ~772-entry universe have NULL `Stock.sector`:

| Category | Tickers |
|---|---|
| Sector SPDRs | XLK, XLV, XLY, XLI, XLB, XLRE, XLF, XLE, XLP, XLU, XLC |
| Broad indices | SPY, QQQ, IWM, DIA |
| Leveraged 3x | TQQQ, SOXL, LABD, BOIL, SVIX, TNA (most distorting) |
| International | EEM, EWY, EWZ, FXI, ASHR, KWEB |
| Commodity/bond | GLD, SLV, IAU, FBTC, IBIT, TLT, HYG, IEF |
| Sub-industry/thematic | SMH, SOXX, IGV, ARKQ, ARKX, DRAM, UFO, URA, PINV.TO |

Investigation: `experiments/sector_etf_screen/stocks_only_breadth.py` (2026-05-07 sector-breadth-independence analysis). Daily correlations: production_breadth<->sector_breadth (contaminated) Pearson +0.7075 (50% shared variance); stocks_only<->sector_breadth (clean) +0.8690 (76%). The original +0.69 correlation reading "sector breadth has 50% unique variance" was inflated by direct overlap (sector SPDRs are in both universes); de-contaminated orthogonality is 24%.

**Fix (one-line patch)** in `market_breadth._get_daily_breadth`: pre-fetch sectored symbols once, use as `IN` filter on first `PriceHistory.select`:

```python
sectored_syms = {
    row.symbol for row in
    Stock.select(Stock.symbol).where(Stock.sector.is_null(False))
}
today_rows = PriceHistory.select(...).where(
    (PriceHistory.date == target_date) &
    (PriceHistory.symbol.in_(sectored_syms))
)
```

Downstream queries inherit the filter via `symbols_list`. ETFs have NULL sector because yfinance returns none for ETF products, so the filter cleanly drops ETFs without touching real stocks.

Coverage check (2026-05-07): total issues 772->727 (-45, -5.8%); advancing -> 233 (reduces 3x-ETF-driven inflation); % above EMA50 -> 59.56% (cleaner equal-weighted measure).

Downstream propagation: `MarketBreadth.breadth_score` recomputed under filtered universe -> `MarketRegime.regime_composite` (35% weight on breadth) shifts -> `regime_multiplier` shifts -> `Score.overall` shifts, though formula is unchanged. Production breadth and stocks-only breadth correlate +0.7616, so most days shift a few pp at most.

**Procedure (executed 2026-05-08 overnight):** edit `market_breadth.py:_get_daily_breadth` -> bump `ALGORITHM_VERSION` to `56eb1f8` -> `trader breadth-backfill 1825` (~30min) -> `trader regime-backfill 1825` (~5min) -> `trader recalculate --force --full` (~25min) -> `trader assess --force` (~10min) -> `trader temporal-refresh` (~5min).

Closes Priority `H_CONTAMINATION` from `BREADTH_INDEPENDENCE.md` (2026-05-07 sector ETF screen).

**Bundled portfolio ship (2026-05-08, `fa1b099`) — SAW Put U-curve:** sector-breadth-driven put alloc gradient. Original null-result framing was a misapplication of strict P3/P4 — actual annual breakdown shows 7-of-8 windows improve compound (only dip and 5y compound-chain fail), DD reduces where it matters (5y -1.1pp, 22-now -2.4pp, 2022 -4.6pp). Shipped as a separate portfolio commit on top of v45.

Config (Region B Phase B winner): quadratic mid=72, hw=18, floor=0.55, ceil=1.35, power=3.0. At each put signal date, look up cross-sector ETF breadth (% of 11 SPDRs above EMA50), apply scale to put alloc: 0.55x contraction at trough (brd=72), recovery to 1.0 by brd=54/90, full 1.35x amplification at <20/>95 extremes (mean-reversion alpha). 30 DTE: shipped. 15 DTE: disabled (not validated under bounded-fill MC). Wiring: `strategy_config.py` (8 SAW fields), `monte_carlo.py`, `backtest_cascade.py`, `tests/test_strategy_config_drift.py` (147 checks), `trader.py _cmd_alloc` display.

Stage C N=300x8 vs baseline: 2021 +1.30e8% vs +6.40e7% (+103%); 2022 +4.05e6% vs +2.57e6% (+57%), DD 73.0%->68.4% (-4.6pp); 2023 +6.01e6% vs +3.90e6% (+54%), DD 71.6%->70.5%; 2024 +2.79e10% vs +1.41e10% (+98%); 2025 +3.54e7% vs +3.20e7% (+11%); dip +4.96e4% vs +7.37e4% (-33%); 22-now +7.82e26% vs +2.77e26% (+182%), DD 72.4%->70.0% (-2.4pp); 5y +8.50e30% vs +1.30e31% (-35% compound-chain artifact), DD 71.7%->70.6%.

See `experiments/saw_put_ucurve/OVERNIGHT_SHIP_REPORT.md`, `experiments/saw_put_ucurve/sweep_phase_c.py`.

Bundled cosmetic artifact: `trader.py:ETF_SYMBOLS` extended with XLK/XLV/XLY/XLI/XLB/XLRE/SOXX (backfilled 2026-05-07 to support the sector-ETF cohort screen; no behavior change, these were always ETFs in practice).

### v44 — ICH: Ichimoku Kijun-sen state dampener (ship 2026-05-07, `d8024b9`)

v44 = v43 (MCD) + ICH. Score-stage continuous dampener using bearish weekly Kijun-sen state, asymmetric-K power-law on score_norm for calls. Orthogonal to v43 MCD: 35% cohort overlap, +4.60pp WR15 marginal lift on the MCD-not-fired sub-cohort at 75+. Same Phase H Rank #3 config wins on both v39 and v43 baselines (robustness validated).

**Mechanism.** Applied AFTER MCD/PCD, BEFORE PESS/EARN_BOOST:

```python
if kijun_pct is not None and kijun_pct < 0:    # bearish weekly Ichimoku state
    ind_dist = -kijun_pct                       # how far below kijun-sen

    # CALL side — asymmetric-K (power-law on score_norm)
    if overall >= 69:
        score_norm = max(0, (overall - 69) / (90 - 69))   # NO upper clip
        K_eff = 0.359 * (score_norm ** 2.68)              # POWER concentrates at 95+
        ind_grad = ramp_linear(ind_dist, 18.4)
        overall -= K_eff * ind_grad * (overall - 63.8)

    # PUT side — symmetric log ramp (Phase C Rank #1 architecture)
    if overall <= 27:
        score_grad = ramp_log((27 - overall) / (27 - 10))
        ind_grad = ramp_log(ind_dist, 8.8)
        overall += 0.278 * score_grad * ind_grad * (33.4 - overall)
```

Why asymmetric-K: Phase B cohort analysis showed Ichimoku WR spread scales non-linearly with score (+18.96pp at 85+, +7.87pp at 75+); uniform K under-dampens 95+. `K_POWER=2.68` concentrates dampening at top tiers while preserving 75-79 N.

Per-trade evidence (5y v43 baseline, 30dte_opt @ w=15d):

| Tier | ΔWR15 | ΔN | Notes |
|---|---:|---:|---|
| 95+ | +5.77pp | -7.7% | strongest tier alpha |
| 90+ | +1.45pp | -14.4% | within H3 +-15% |
| 85+ | +0.89pp | -8.3% | strong |
| 80+ | -0.01pp | -3.4% | flat (intentional, preserve N) |
| 75+ | +0.25pp | -3.8% | small positive |
| 70+ | +0.14pp | -3.0% | small positive |
| Put <25 | +0.72pp | -14.9% | within H3 +-15% |
| Put <20 | +2.15pp | -37.7% | strong on subset |

H1-H5: H1 strict (>=+0.5pp on >=3 call tiers) PASS (95+/90+/85+); H1 affected-tier (Put<25 >=+0.3pp) PASS (+0.72); H2 directional PASS on top tiers (95+ Δ30=+6.09); H3 N stability (+-15%) PASS all primary tiers (max -14.9%); H4 puts neutral/better PASS; H5 multi-window: small-N sign-flips on 95+/90+/85+ at 1y (consistent with PCD/MCD/CWWD baseline pattern; meaningful tiers Call 70+/75+, Put <25/<15 all sign-consistent). Smoothness PASS (asymmetric power-law monotonic by construction).

Stability: Ichimoku features 3x more stable than wadj over 5-bar windows. COHR test on v43: -1.36pp Monday churn within affected cohort (1.58% of day-pairs); net population Mon/Tue-Fri ratio unchanged at 1.99x but affected peaks see structural improvement.

Calibration (`experiments/weekly_avwap/`, 1,170+ variants across 7 sweeps): A-B feature build+cohort profiling (916K peaks, AVWAP killed, Ichimoku validated); D stability check; C 100+200 variants additive (Rank #1 = +1.10pp <25); D-sub 120 variants substitutive (asymptote -0.12pp put alpha, proved v27 can't be replaced); C-multi 200 variants Ichimoku+smooth-wadj substitutive; E 200 variants constrained refinement; F scorched-earth substitution + clumping check; G 200 variants asymmetric-K intro (K_POWER=1.58 winner); H 250 variants refined multi-tier objective (K_POWER=2.68 final ship winner).

Robustness: same Phase H Rank #3 config wins on both v39 and v43 baselines — Phase F cross-tab showed only 35% cohort overlap with MCD (mcap-based vs structural weekly state, distinct signals).

**What NOT to retry:** AVWAP-from-last-earnings — NULL signal at 75+ (0.7pp spread); "self-fulfilling-prophecy" hypothesis (Priority #5b) empirically falsified. 50W SMA/52W high-low — weak (1-2pp at 75+). Pure substitution of v27 — 470 variants tested, asymptotes at +0.03pp; v27's threshold genuinely outperforms gradient substitution. Removing the score-range gate entirely — signal inverts at 70 boundary (50-59 cohort -4.85pp inversion); the gate matters.

## Active Version: v43 (`e083032`) — 2026-05-07 (MCD mcap dampener) [now superseded by v44]

v43 = v39 (CWWD+PESS) + MCD. Score-stage continuous asymmetric dampener for calls in [70,84] using log10(mcap_b) as confidence-shifter. Cohort signal structural (year-stable 2022-2025, monotonic across mcap bins): at fixed score, large-cap call TP exceeds micro-cap by 8.2pp on 75+ at 5y. Targets over-confidence in mid/small-cap 80-84 while leaving 70-72 small-caps and $100B+ large-caps untouched.

### v43 — MCD: Mcap Dampener (ship 2026-05-07, `e083032`)

**Mechanism.** Calls-only, applied AFTER PCD, BEFORE PESS/EARN_BOOST:

```python
if MCD_GATE_LO <= overall <= MCD_GATE_HI and mcap_b is not None and mcap_b > 0:
    log_mcap = log10(mcap_b)                                              # mcap in $B
    weakness = mcap_factor^MCD_MCAP_POWER * score_factor^MCD_SCORE_POWER
        mcap_factor  = clip((LOG_HI - log_mcap) / (LOG_HI - LOG_LO), 0, 1)
        score_factor = clip((overall - GATE_LO) / (GATE_HI - GATE_LO), 0, 1)
    overall -= MCD_ALPHA * weakness * (overall - MCD_TARGET)
```

Ship constants (calibrated over 15,195 variants): `MCD_GATE_LO=70, MCD_GATE_HI=84`, `MCD_LOG_LO=0.50` ($3.16B full strength), `MCD_LOG_HI=1.90` ($79.43B zero strength), `MCD_ALPHA=0.80, MCD_TARGET=61`, `MCD_MCAP_POWER=0.70` (concave mcap ramp), `MCD_SCORE_POWER=1.50` (mild quadratic concentration at high scores — 19/20 top sweep variants converged here).

Why dual power-law: MCAP_POWER<1 keeps mcap_factor near 1 across small-to-mid cap, drops faster near LOG_HI. SCORE_POWER>1 concentrates dampening at the top of the gate (80-84 mid-caps) while barely touching 70-72. TARGET=61 (well below GATE_LO=70) drives dampened signals fully out of the qualifying universe (typical drop 9-18 points); untouched signals capped at GATE_HI=84 so 85+ preserved by construction.

H1-H5 ship gate (5y v39 baseline, 30dte_opt @ W=15d):

| Tier | base TP | dampened TP | Δ | gap to next | verdict |
|---|---:|---:|---:|---:|---|
| 70-74 | 58.53% | 58.59% | +0.06 | -> +5.09 | CALIBRATED |
| 75-79 | 60.02% | 63.68% | +3.66 | -> +2.41 | favorable over-correction |
| 80-84 | 66.33% | 66.09% | -0.24 | -> +2.83 | CALIBRATED |
| 85-89 | 68.91% | 68.91% | 0.00 | -> +6.09 | untouched |
| 90+ | 75.00% | 75.00% | 0.00 | — | untouched |

H1 (affected tier 75+): 5y +2.73pp, 10y +2.96pp (sign-consistent). H2 directional PASS. H3 N stability: -44.9% drop on 75+ (within v27 WCF precedent ~75% drop; a bigger drop is fine if the dropped signals are the targeted bad-quality cohort). H4 puts neutral (calls-only gate). H5 5y/10y both positive on 75+. Spillover on 80+/85+/90+/95+: 0.00pp. Gradient preservation: all inter-bucket gaps >= 2.41pp.

Cohort signal that motivated the ship (5y v39): large_50-200B N=1,394 TP65.8% (+3.3 vs 62.5% 75+ baseline); xl_200B-1T N=612 65.4% (+2.9); mega_1T+ N=207 64.3% (+1.8); mid_10-50B N=1,822 61.7% (-0.8); small_2-10B N=1,523 60.5% (-2.0); micro_lt2B N=382 57.6% (-4.9). Monotonic 8.2pp ladder across ~5,940 signals, year-stable 2022-2025.

Calibration (3 sweep generations, 15,195 variants): v1 linear (3,429 variants, LO=0.0/HI=2.2/a=0.30/T=65/mp=1/sp=1) 5y +1.92/10y +1.93, min_gap 2.50, RMSE 1.23, N drop -24.7%. v2 power-law (3,456, LO=0.7/HI=1.8/a=0.95/T=65/mp=0.5/sp=2.0) 5y +2.11/10y +2.30, min_gap 2.38, RMSE 0.98, N drop -39.2%. v3 CHAMPION (8,310, LO=0.5/HI=1.9/a=0.80/T=61/mp=0.7/sp=1.5) 5y +2.73/10y +2.96, min_gap 2.41, RMSE 1.16, N drop -44.9%. Top 20 v3 candidates within +-0.13pp of each other on 5y lift.

Plumbing: mcap_b loaded once per symbol from `Stock.market_cap`. Wired through `Score.calculate_overall_score()`, `Stock.calculate_scores_batched()`, `Stock.recalculate_scores_batched()`, `simulator.StockContext` (`ctx.mcap_b`). `weight_info` exposes `mcd_dampen` and `mcd_mcap_b` when active, for `explain-scores` audits.

Artifacts: `experiments/sector_mcap_cohort/` (initial cohort z-mining, surfaced monotonic mcap gradient + basic-materials/utilities put underperformance); `experiments/mcap_dampener/` (`build_features.py`, `sweep.py` v1, `sweep_v2_powerlaw.py`, `sweep_v3_bayes.py`, `check_lift_side.py`, `check_calibration.py`, `sanity_v3.py`, `FINDINGS.md`, `rerank_v2.py`/`rerank.py`).

---

## v39 (`200f33a`) — 2026-05-06 (CWWD + PESS) — was active 2026-05-06 to 2026-05-07

v42 shipped 2026-05-07 (rolling weekly composite) and REVERTED same-day (`9ad2ed9`) after production assess on full 10y revealed catastrophic per-trade WR regression (-13pp on 80+/85+/95+, N +137-186%). Active version returned to v40 (SVD), then v40 was also reverted to clear the whole post-v39 stack to a known-stable baseline. SVD's per-trade evidence had passed H1 affected-tier (75+ +0.36pp at 5y, sign-consistent multi-window) but is held pending a future post-2026-11-15 holdout validation.

v41 AlgorithmVersion row is a build-artifact from `get_or_create_current()` running mid-transition. Both v41/v42 rows and their Score rows remain in DB (version-keyed) for instant API fall-back. Active version after revert: v40's predecessor, v39.

### v42 — Rolling Weekly Composite (ship 2026-05-07, REVERTED + DROPPED 2026-05-07)

Active ~24h. Production assess on full 10y v42 vs v40 baseline: 5y 95+ WR15 91.7%->78.6% (-13.1pp, +186% N); 85+ 83.3%->69.2% (-14.1pp, +151% N); 80+ 80.5%->67.3% (-13.2pp, +137% N).

Why P0's +4.51pp prediction was wrong: P0's eval pipeline called `calculate_rsi_score`/`calculate_macd_score` with `_ph_cache={}` (empty), bypassing breakout-push/divergence-push detection — those rare (<5% of signals) paths carry the top-tier alpha (+-15pp conviction lift). Calendar weekly's coarser RSI fires them; rolling's smoother indicators rarely do, so mid-quality signals calendar would have promoted leak into top tiers via raw score instead, diluting WR and blowing up N.

Revert (`9ad2ed9`): `WEEKLY_MODE` default -> `'calendar'`, `ALGORITHM_VERSION` rolled back to v40 then v39. Drop (later commit): entire P1-P4 infrastructure removed — the ~3-sec/cycle cron compute against an unused table wasn't worth carrying dead code for an "instant re-enable" pending someone restructuring the breakout-push lift bands (backfill cost on re-implementation: one-time 94 sec).

Removed: `database/utils/rolling_weekly.py` (491 lines); `database/models/technical.py:RollingWeeklyIndicator`; exports in `database/models/__init__.py`/`database/__init__.py`; `database/models/core.py` 4 scoring sites collapsed to calendar-only; `simulator.py:StockContext` `weekly_mode`/`rolling_history_full`; `strategy_config.py:WEEKLY_MODE` flag; `trader.py` incremental_update cron block; `api.py` `/api/stocks/<sym>/rolling-weekly-indicators`; `tests/test_rolling_weekly.py`; `tests/test_strategy_config_drift.py:check_weekly_mode` (130 checks now); DB table `rolling_weekly_indicators` dropped (851,274 rows).

Preserved: `experiments/rolling_weekly/` (7 build scripts, FINDINGS.md, P5_CUTOVER.md, JSON outputs — contains both the correct correlation/stability findings and the incorrect +4.51pp P0 prediction with its approximation flaw).

For future indicator-input investigations: validate against the FULL `calculate_rsi_score` path with `_ph_cache` populated from real PriceHistory, never an empty mock; run the full `trader recalculate --force --full` + `trader assess --force` pipeline before claiming any WR lift; N-stability (H3, ΔN >= 15% on any primary tier) is a hard red flag; to resurrect, rebuild from commit `5ec8ee7` (P1-P4 infra) but first solve the breakout-push gap (restructure lift bands to fire on rolling inputs, or prove smoothed-indicator alpha exceeds lost push-band alpha at all top tiers).

---

## v40 (`917659c`) — 2026-05-06 (REVERTED 2026-05-07)

v40 = v39 + SVD (Score Velocity Dampener). Drifts decelerating 75+ signals out of cascade-qualifying territory based on 5-day score velocity.

### v40 — REVERTED 2026-05-07 (`f89dde0` + `1f090e6`)

User-directed rollback alongside the v42 revert, to a known-stable v39 baseline.

**Critical finding: calibration metric and dashboard metric disagree.** H1-H5 gate used option-aligned barriers (TP=1.274sigma/SL=0.983sigma, `metric='tp'`, via `barrier_outcomes` cache). Dashboard Assessment tab uses generic WR15 (K=2.0sigma HIGH/M=5.0sigma stop, scaled sqrt(W/30), much wider). Comparing v40 vs v39 directly from the `ScoreAssessmentResult` table:

| Window | Metric | 95+ Δ | 90+ Δ | 85+ Δ | 80+ Δ | 75+ Δ | Deep puts (<5/<10) Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| 5y | Option TP% (calibration) | 0.00pp | 0.00pp | -0.03pp | +0.08pp | +0.37pp | not measured |
| 1y | Generic WR15 (dashboard) | -5.0pp | -4.6pp | +1.2pp | -1.0pp | +0.2pp | +0.5-1.5pp |
| 5y | Generic WR15 (dashboard) | -1.3pp | -1.0pp | 0.0pp | -0.3pp | 0.0pp | +1.5-2.0pp |
| 10y | Generic WR15 (dashboard) | -1.2pp | -1.4pp | -0.3pp | -0.2pp | -0.3pp | +0.6-1.5pp |

Why they diverge: SVD-displaced 75+ signals tend to dip then recover. Generic 2.0sigma target: recovery hits the wide barrier -> counted a win. Option's 1.274sigma TP/0.983sigma SL: early dip fires SL before recovery -> loss. SVD correctly identifies option-losers that the wide-barrier metric can't see.

Practical implications: option TP% shows SVD is a genuine small improvement (+0.37pp at 5y on 75+, sign-consistent 1y/3y/5y/10y); generic WR15 essentially flat on middle tiers, mildly negative on top tiers (95+/90+, micro-N noise), mildly positive on deep puts. N drops ~3.3% on 75+ (175 signals/5y). Re-ship when confirmed via post-2026-11-15 holdout, or if top-tier option TP% accumulates more evidence at N>=1000.

Re-ship = two-commit replay: `git cherry-pick 917659c 146c3cd` then `trader recalculate --force --full`. Calibration artifacts in `experiments/score_velocity/` remain valid against v39 baseline.

### v40 (original ship summary, preserved for reference)

**Motivation.** Per-trade profiling (5y v39, confirmed 10y, 30dte_opt @ w15) showed strong discrimination by score velocity:

| Cohort (75+) | N | opt_WR15 | lift vs others | z |
|---|---:|---:|---:|---:|
| heavy decel (vel_5d <= -10) | 38 | 44.7% | -18.30pp | -2.33 |
| decel mild (-10<vel<=-5) | 47 | 51.1% | -11.95pp | -1.69 |
| (-5<vel<=0) | 126 | 54.8% | -8.34pp | -1.92 |
| heavy accel (vel>+10) | 4,603 | 64.0% | +5.34pp | +3.35 |

Heavy-decel (44.7%) is below call BE of 45% — EV-negative. Spread heavy-decel to heavy-accel: 19.3pp on 75+.

**Mechanism.** Saturated (k=1) score-stage dampener drifting overall toward `SVD_TARGET=72` when score is cascade-eligible AND velocity negative:

```python
SVD_GATE_SCORE = 75
SVD_GATE_VEL   = 0
SVD_TARGET     = 72
SVD_ALPHA      = 0.80
SVD_K          = 1

if overall >= 75 and velocity_5d is not None and velocity_5d < 0:
    weakness = clip(-velocity_5d / 1, 0, 1)  # saturates immediately for any decel
    overall -= 0.80 * weakness * (overall - 72)
```

`velocity_5d = overall_today - overall_5_trading_days_ago` (positional shift). Batch scorers compute from stored Score.overall values; single-row scoring uses prior-day stored score as proxy.

With k=1: `overall_new = 0.20*overall + 57.6`. Examples: (75,-1)->72.6 drops out; (80,-1)->73.6 drops out; (85,-1)->74.6 drops out; (90,-1)->75.6 stays in 75-79; (95,-1)->76.6 stays in 75-79; vel>=0 -> no change.

k=1 (saturated) preferred over smooth k=3/k=5: decel cohort underperforms uniformly regardless of magnitude; refinement showed k=1 beats k=3 by 0.13pp composite. Apply order: AFTER PESS, BEFORE EARN_BOOST (so earnings boost can't re-amplify a displaced peak).

**H1-H5 Ship Gate (10y v39 baseline, affected-tier framework):** all Δ values are option-aligned barriers (TP=1.274sigma/SL=0.983sigma, `metric='tp'`) — dashboard generic WR15 is flatter (see REVERTED section).

| Window | 75+ Δ TP% | 80+ Δ | 85+ Δ | 90+ Δ | 95+ Δ | N75 drop |
|---|---:|---:|---:|---:|---:|---:|
| 1y | +0.36pp | +0.10 | -0.03 | 0.00 | 0.00 | -3.0% |
| 3y | +0.51pp | +0.06 | -0.03 | 0.00 | 0.00 | -3.2% |
| 5y | +0.37pp | +0.08 | -0.03 | 0.00 | 0.00 | -3.3% |
| 10y | +0.36pp | +0.08 | -0.03 | 0.00 | 0.00 | -3.3% |

H1 affected-tier PASS (5y/3y/1y/10y positive on 75+); H2 directional PASS; H3 N stability PASS (-3.2% << 15%); H4 puts neutral (gate overall>=75); H5 all 4 windows positive on 75+. Standard H1 (>=+0.5pp on >=3 tiers) does NOT pass — use affected-tier framework (same pattern as v38 CWWD, v39 PESS).

Displaced cohort (175 signals/5y, dropped from 75+ to <75): aggregate WR15 51.4% (vs 75+ baseline 62.9%, -11.5pp); 75-79 sub N=157 WR15=50.3% (-10pp vs 60.4% baseline); 80-84 sub N=14 WR15=57.1% (-11pp); 85-89 sub N=4 (noise). Genuinely EV-negative/marginal trades.

**Two-stage calibration:** Stage 1 coarse 108-variant grid (`experiments/score_velocity/sweep.py`, 5y v39): gate_vel ∈{0,-3,-5,-10}, target∈{65,70,73}, alpha∈{0.50,0.75,0.95}, k∈{3,5,10}; 11 candidates pass affected-tier gate at 5y, 2 at 10y; winner `g0_t65_a95_k3` (composite +0.274). Stage 2 Bayesian-style dense refinement (`bayes_refine.py`, 700 variants on 10y v39): gate∈{0,-1,-2,-3}, target∈{62,64,65,66,68,70,72}, alpha∈{0.70-1.00}, k∈{1,2,3,4,5} — winner gate=0, target=72, alpha=0.80, k=1, composite +0.337 (+0.063 over Stage 1); k=1 beats k=3 by 0.13pp; 5y/3y/1y/10y all positive on 75+.

Code: `database/utils/scoring.py` (SVD constants + `build_velocity_5d_map()` + logic between PESS and EARN_BOOST); `database/models/core.py` (velocity_5d wired into `calculate_scores_batched`, `recalculate_scores_batched`, single-row path); `simulator.py` (velocity_5d in `StockContext` + bulk score load with version dedup). `weight_info` adds `svd_dampen`/`svd_vel5d` when active.

Artifacts in `experiments/score_velocity/`: `build_features.py`, `profile.py`, `sweep.py`, `FINDINGS.md`, output logs/jsonl, `.cache/score_velocity/calls_v39_3650.parquet` (10y feature cache, 54k call peaks).

---

## v39 (`200f33a`) — 2026-05-06 (CWWD + PESS)

v39 = v38 (CWWD) + PESS — score-stage replacements for cascade-stage filters `WEAK_WEEKLY_CALL_DROP` and `EARN_SUPP_PUT` (previously portfolio-stage knobs). Fixes the **dashboard divergence problem**: under the old filters, a score like 73 wadj-neg call or 18-with-earnings put showed green/red on dashboard but cascade silently skipped it. Score-stage versions drift the affected cohorts out of qualifying ranges so the dashboard matches what the cascade trades.

Both filters retired this ship: `EARN_SUPP_PUT` -> False (replaced by PESS in v39); `WEAK_WEEKLY_CALL_DROP` -> False (replaced by CWWD in v38). Frontend StockTable `cascade_skip` badges removed (API stopped emitting them); the API field stays for any future cascade-stage filter.

### Two "Current" Versions

| Mechanism | Code | Used when |
|-----------|------|-----------|
| HEAD / writer | `AlgorithmVersion.get_or_create_current()` | Score.build(), recalculate_scores_full |
| Active / reader | `AlgorithmVersion.get_active_scores_version()` | Flask API, assessment |

New version rows get `git_message` from `git log` when first created. Active version = highest `id` among versions with non-empty `git_message`; a row with empty `git_message` means the API may keep using an older version until DB is fixed.

### Reverting to a Prior Version

```bash
trader revert v14            # by AlgorithmVersion id
trader revert 14             # same
trader revert 83851db        # by git commit (exact or unique prefix)
```

Overwrites `ALGORITHM_VERSION` file only — no git checkout, no recalculate. Scoring code must still be reverted manually so future `trader update` runs match the target version.

---

## Cross-Version Per-Trade WR (5y / 30 DTE / barrier-touch)

| Bucket | v21 | v27 | v28 | v29 | v30 | v31 | v32 | v35 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 95+ | 90.0% (N=20) | 90.5% | 92.1% | 93.6% | 91.1% | — | — | 80.6% (N=129) |
| 90+ | 74.6% | 75.0% | 81.2% | 82.2% | 82.3% | — | — | 76.2% (N=534) |
| 85+ | 76.1% | 75.6% | 80.2% | 80.9% | 80.8% | — | — | 75.7% (N=2,034) |
| 80+ | 74.0% | 73.6% | 76.2% | 75.6% | 75.7% | — | — | 70.7% (N=6,895) |
| 75+ | 73.0% | 73.3% | 74.3% | 74.0% | 73.9% | — | — | 66.5% (N=19,128) |
| <5 put | 69.9% | 79.1% | 80.8% | 80.4% | 80.1% | — | — | 63.7% (N=1,761) |
| <15 put | 72.1% | 76.7% | 76.8% | 77.2% | 77.2% | — | — | 60.8% (N=12,389) |
| <25 put | 69.0% | 75.9% | 75.9% | 75.9% | 76.1% | — | — | 57.9% (N=50,480) |

v35 column is on a different baseline (v34_calibration sweep, 5y, generic barriers) than v21-v31 (cumulative WR15 vs 30dte_opt barriers) — not directly comparable. Vs v34 production calibration, v35 lifts 95+/90+/85+/80+ by +4.20/+4.18/+0.86/+0.98pp WR15 with N stable.

Key: v27 lifted puts (+5-9pp). v28 lifted top calls (+4-6pp via earnings boost) — the alpha-injection ship. v29 = per-trade neutral. v30 = AMC architectural fix. v31 = strict-future earnings fix (D0 edge case, rare). v32 = call-side WCF-mirror dampener targeting `wadj<0` on calls 75+, expected +1pp on 75-89 per pre-ship sweep.

Run `experiments/version_comparison.py` after explicit v31/v32 assess to populate the column.

---

## Session Commits 2026-04-29

```
f2329a1  Option-pricing-aware MC + Phase OP1 (DD=0.60) [SHIPPED then REVERTED]
2233dc8  Revert Phase OP1 ship (DD breaker 0.60 -> 0.68)
9a9da33  AMC-aware earnings effective_date + [low,high] sampling refactor
ff2cb91  Bump ALGORITHM_VERSION to 9a9da33
3432fb8  Bound MC random fill by trigger barrier
```

## Session Commits 2026-04-30

```
f3ec7c1  Strict-future earnings semantics + precomputed effective_date column   [v31]
cd71bee  Bump ALGORITHM_VERSION to f3ec7c1
859b946  Phase A.1 — strategy_config.py + drift-guard test
e7e3339  Phase A.2 — refactor 4 MC/backtest engines to read from strategy_config
0086bfc  Phase A.3 — refactor api.py + trader.py to read from strategy_config
b5b3d3e  Phase A.4 — frontend fetches strategy defaults from /api/strategy/config
```

## Session Commits 2026-05-01

```
43eecea  v32 scoring: call-side WCF-mirror dampener (Priority #4 close)         [v32]
27829a3  Bump ALGORITHM_VERSION to 43eecea (v32 call-WCF-mirror dampener)
```

## Session Commits 2026-05-04

```
(pending) H3 DD-soft band call alloc contraction (30 DTE, portfolio-stage)     [no version bump]
(pending) v32_optim joint Bayesian ship: cascade + TP/SL retune (30 DTE only)  [no version bump]
e50fe83  Earnings calendar ghost-row hardening: lookup filter + scrape dedup + cleanup
e77714f  v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores
5e8cf80  Bump ALGORITHM_VERSION to e77714f (v35 EARN_BOOST recalibration)
```

## Session Commits 2026-05-05

```
(pending) v15_optim cascade-only ship: 15 DTE TIER_ALLOC + PUT_TIER_ALLOC retune  [no version bump]
6f9afda  v37 scoring: post-crash put dampener (PCD)                              [v37]
20f0fe8  Bump ALGORITHM_VERSION to 6f9afda (v37 PCD post-crash put dampener)
c94e4ae  Ship WEAK_WEEKLY_CALL_DROP D variant (30 DTE portfolio filter)          [retired 2026-05-06]
068fb4c  trader alloc: display WEAK_WEEKLY_CALL_DROP filter status               [retired 2026-05-06]
```

## Session Commits 2026-05-06

```
fb3c793  dashboard: surface cascade-stage skip filters with visual badge          [SkipBadge]
b093e2d  v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75      [v38]
86ddb4f  Bump ALGORITHM_VERSION to b093e2d (v38 CWWD)
200f33a  v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT [v39]
d4f2a2c  Bump ALGORITHM_VERSION to 200f33a (v39 PESS)
```

## Session Commits 2026-05-07 — v42 Rolling Weekly + v40 SVD BOTH SHIPPED, BOTH REVERTED same-day

Active version returned to v39 (`200f33a`) after a two-stage rollback: v42 reverted first (regression), v40 reverted ~30 min later (precautionary cleanup of the entire post-v39 stack).

```
5ec8ee7  P1-P4: Rolling weekly indicator infrastructure (flag-gated, default off)
942a6fb  docs: P1-P4 rolling weekly ship documented (branch state)
5e6e3d3  P5: flip WEEKLY_MODE default to 'rolling' — rolling weekly composite is now active
63e5825  Bump ALGORITHM_VERSION to 5e6e3d3 (rolling weekly P5 ship)
9cde846  Merge branch 'rolling-weekly-p1-p4' — rolling weekly composite ships as v42
3431e5f  docs: rolling weekly is SHIPPED as v42 (post-cutover)
b739e5d  post-v42 cleanup: historic_peaks post-close gate + dashboard ETF additions
436baea  log BSD v39 null + queue v42 BSD re-run
9ad2ed9  Revert v42 rolling weekly ship — catastrophic per-trade WR regression
3045cf1  docs: mark v42 rolling weekly as REVERTED with regression evidence + lessons
f89dde0  Revert "Bump ALGORITHM_VERSION to 917659c (v40 SVD)"
1f090e6  Revert "v40 scoring: SVD (Score Velocity Dampener)"
0318349  docs: CLAUDE.md active version → v39 post-SVD-revert
```

v42 revert reason: production assess on full 10y v42 vs v40 showed 5y 95+ -13.1pp/85+ -14.1pp/80+ -13.2pp WR15 with N counts up 137-186%; P0's per-trade +4.51pp prediction bypassed breakout-push/divergence-push detection in `calculate_rsi_score` (where calendar's top-tier alpha lives).

v40 revert reason: precautionary cleanup alongside v42 rollback, not a regression — SVD's per-trade evidence remained valid (75+ TP% +0.36pp at 5y, sign-consistent multi-window). Re-ship = revert `1f090e6`+`f89dde0` then recalculate.

ALGORITHM_VERSION transitioned `200f33a`(v39) -> `917659c`(v40, ~24h) -> `5e6e3d3`(v42, ~2h) -> `917659c`(v42 revert) -> `200f33a`(v40 revert, final). v42's original recalc rows persist in DB (version-keyed) but API reads v39 rows.

P1-P4 rolling weekly infrastructure preserved for future calibration. SVD calibration artifacts in `experiments/score_velocity/` remain valid against v39 baseline.

### Rolling Weekly Composite — replacement for calendar WeeklyScore inputs

**Problem.** Calendar weekly indicators are unstable mid-week and ambiguous at lookup (this-week's Monday vs last-week's). COHR=84/=59 ghost on 2026-05-06: a Score row at 19:26 read this-week's WeeklyScore (composite=53), at 20:23 read last-week's (composite=65) — same date, two writes, different weekly inputs. Monday-class pattern: |Δoverall| 6.245 on Mon vs ~3.0-3.3 Tue-Fri (2x).

Three approaches tried (`experiments/weekly_proximity/` + `rolling_weekly/`): (1) day-of-week wadj dampener — overfit 5y, fails H5, stability +17% WORSE; (2) Monday-only dampener — marginal -3.6% Mon improvement at -0.5pp WR15 cost on 95+; (3) rolling weekly composite — the working fix.

**Design (Option C, non-overlapping ladder ending T-1):** for scoring date T, weekly indicators computed on a 14-bar ladder of non-overlapping 5-day aggregates ending T-1: `[T-5..T-1],[T-10..T-6],...,[T-70..T-66]`. Today's daily bar (T) not in the ladder — immune to intra-day partial-bar instability. Same 14-bar lookback as calendar RSI(14); 200-bar history for MACD(12,26,9)+EMA200 convergence. Daily-keyed `RollingWeeklyIndicator` table; 850K rows backfilled (722 stocks x ~5y) in 94 sec via `multiprocessing.Pool`.

Validation: 80+ WR15 (P0 10% sample) calendar 58.82% -> rolling 63.33% (+4.51pp); 85+ 56.86%->60.00% (+3.14pp); 75+ 59.06%->60.92% (+1.87pp); 70+ 56.03%->55.61% (-0.42pp); <25 48.54%->48.93% (+0.39pp). Mon |Δoverall| raw substitution 6.245->4.384 (-30%); proper scoring 5.073->4.306 (-15%); net |Δoverall| all days 3.711->3.709 (~unchanged). Rolling vs calendar raw RSI correlation +0.9645, MACD line +0.9926, MACD hist +0.9853.

Self-fulfilling-prophecy (institutional rhythm) hypothesis largely refuted — the two signals are 96-99% correlated; the 80+/85+ alpha is from rolling removing single-day noise spikes, not from calendar boundaries carrying behavioral information.

Architecture (P1-P4, in branch): `database/models/technical.py:RollingWeeklyIndicator` (daily-keyed, NULL-tolerant); `database/utils/rolling_weekly.py` (491 lines new — `compute_for_stock`, `bulk_backfill`, `incremental_update`, `get_for_scoring`, `get_weekly_inputs_for_scoring`, `load_rolling_history_map`, `get_weekly_inputs_batch`); `strategy_config.WEEKLY_MODE` flag default `'calendar'`; 4 scoring sites routed through the flag (`Score.calculate_overall_score`, `calculate_scores_batched`, `recalculate_scores_batched`, `simulator.py:StockContext`), else-branch bit-identical to calendar; `trader update` cron incrementally populates the table regardless of flag; `/api/stocks/<sym>/rolling-weekly-indicators` display endpoint; `tests/test_strategy_config_drift.py` +1 (131 total); `tests/test_rolling_weekly.py` new, 5/5 passing.

Trade-offs: Tue-Fri |Δ| each ~+0.5 worse than calendar, net |Δ| unchanged (whiplash redistributes from Monday-concentrated to even-across-week). 70+ tier essentially tied; 80+/85+ are the alpha tiers. P0's approximation (skipped breakout-push) means absolute WR levels in test are low vs production — only the cal-vs-roll delta is trustworthy.

Files: see `experiments/rolling_weekly/P5_CUTOVER.md` (24 modified, 11 new, 3,532 lines added). Cutover (P5) was one config line + 25-min recalc; revert identical with no data loss (Score table version-keyed).

### v38 + v39 — score-stage replacements for default-on cascade filters

**Motivation.** 2026-05-06 audit found the dashboard showing scores the cascade silently filtered (`EARN_SUPP_PUT` since 2026-04-26 = 10 days; `WEAK_WEEKLY_CALL_DROP` since 2026-05-05 = 1 day) — real-money users deploying from the StockTable had no signal the strategy would skip these.

Defense in depth then root-cause fix: (1) dashboard-first (`fb3c793`) — `cascade_skip` field on `/api/stocks/all`+`/api/stocks/<sym>`, SkipBadge pill on StockTable desktop+mobile, auto-deployed for market-open protection; (2) v38 CWWD (`b093e2d`) — score-stage extension of CWCF below 75, drifts wadj-neg 70-74 below 70, replaces WEAK_WEEKLY_CALL_DROP; (3) v39 PESS (`200f33a`) — score-stage lift for puts in [16,20] near earnings, target=28, replaces EARN_SUPP_PUT; (4) both filters retired (False for 30/15 DTE), frontend SkipBadge entries removed. Single source of truth = the score itself.

Gate fix (`assessment-backtest.md`): original H1-H5 measured only {95+,90+,85+,80+,75+} — blind to sub-75 changes (CWWD pushes cohort below 70; 75+ tiers byte-identical, H1 shows nothing). Fixed with an "affected tier" framework: sub-75 changes -> affected tier is 70+ cumulative; put filters -> <25 cumulative. Soft gates, judgment, no strict thresholds.

Per-trade evidence (5y v37 baseline -> v39): CWWD 70+ TP +0.62pp/N -4.6%, spillover +-0.0pp on 75+/80+/85+/90+/95+. PESS <25 TP +0.06pp/N -2.3%, spillover +-0.4pp max on tighter put tiers (gradient edges only). PESS impact modest because v37 PCD already pre-filtered most of the 16-20 earn-window cohort — residual ~65 puts/year is what PESS targets; portfolio impact via slot displacement matches the original EARN_SUPP_PUT N=1000 MC validation (+44.7% 5y compound).

Score-distribution deltas (5y v37->v39 DB count): 70+ 24,024->22,859 (-1,165/-4.8%, mostly CWWD); 70-74 isolated 18,296->17,173 (-1,123); <=25 11,936->11,705 (-231, PESS partial lifts + downstream EARN_BOOST); 16-20 isolated 3,370->3,311 (-59 cleared above 20).

---

## v37 — Post-Crash Put Dampener (PCD ship 2026-05-05, `6f9afda`)

Score-stage dampener lifting put scores OUT of any put bucket (<=25) when the underlying fell >1.0 stock-sigmas over the last 10 trading bars. Fixes the put-cohort regression where puts firing right after sharp drops underperform the put baseline by -7.15pp WR15 at the option-aligned barrier (z=-6.88, N=2,767 over 5y).

**Mechanism** (in `compute_overall_score`, after continuation boost, before earnings boost):

```python
PCD_GATE         = 25      # only fires on overall <= 25
PCD_RET10D_SIGMA = -1.0    # 10-bar return below -1.0 stock-sigmas
PCD_TARGET       = 30      # lift target (just above all put buckets)

if overall <= PCD_GATE and ret_10d_sigma is not None and ret_10d_sigma <= PCD_RET10D_SIGMA:
    overall = max(overall, PCD_TARGET)
```

`ret_10d_sigma = ret_10d / (sigma_60 * sqrt(10))`, sigma_60 = 60-day realized daily stdev (same vol definition as the strategy's TP/SL barriers). Sigma normalization corrects the stock-vol confound: -10% over 10 days is ~3 sigma for low-vol KO but noise for high-vol PLTR — the cohort regression concentrates in low/mid-vol stocks (z=-5.03 in sigma_d 1.5-2.5%) and is insignificant in high-vol stocks (z=-1.63 in sigma_d>6%).

H1-H5 ship gate (30dte_opt @ W=15d, 5y vs v36 baseline):

| Tier | 1y lift | 3y lift | 5y lift | H5 sign-consistent? |
|---|---:|---:|---:|:---:|
| <5 | +4.97 | +2.73 | +2.95 | yes |
| <15 | +1.96 | +2.72 | +3.22 | yes |
| <25 | +2.08 | +1.09 | +1.56 | yes |
| 95+/90+/85+/80+/75+/70+ (calls) | 0.00 | 0.00 | 0.00 | unchanged (gate overall<=25) |

N stability: ~30% of put peaks at <=25 displaced (designed; precedent v27 WCF dropped 75%), floor N >>50 on every tier. <=25: ~16,872->~11,841; <=15: ~4,149->~2,683; <=5: ~591->~339.

Calibration sweep (`experiments/post_crash_v2/`): SIG_DISC_100 (-1.0sigma) sum_lift +22.86, <5 5y +3.12/<15 +3.15/<25 +1.51, PASS -> shipped. SIG_DISC_125 (-1.25sigma) +9.58, FAILS (1y <15 turns negative). SIG_DISC_150 (-1.5sigma) +7.83, PASS but lower lift. SIG_DISC_175 (-1.75sigma) +4.23, FAILS. RAW_DISC_10 (-10% raw) +32.08, PASS but breaks vol-fairness — rejected despite marginally higher lift: unfair across vol regimes, breaks sigma-defined consistency with rest of strategy, not future-proof under universe drift.

Stock-vol confound check (raw ret_10d<=-10% cohort by daily vol): 1.5-2.5%/d N=634 cohort WR 66.4% vs rest 75.8%, lift -9.41, z=-5.03 (sig); 2.5-4.0%/d N=1,840, -5.49, z=-4.40 (sig); 4.0-6.0%/d N=1,622, -2.16, z=-1.49 (n.s.); >6.0%/d N=1,022, -3.29, z=-1.63 (n.s.). Post-crash signal is statistically meaningful only where -10% is a multi-sigma move.

Plumbing: `build_ret10d_sigma_map(ph_rows_asc)` produces {date: ret_10d_sigma} per symbol, called once per batch scorer (`calculate_scores_batched`, `recalculate_scores_batched`, simulator's `StockContext.__init__`); single-row path does an 80-bar lookback outside batch context.

No MC validation needed per `assessment-backtest.md` — per-trade gate is the appropriate gate for scoring changes; signal density shift on <=15 is -19%, under the 30% smoke-MC trigger threshold. Calls untouched.

`weight_info` adds `pcd_active=1` and `pcd_r10sigma=<value>` when the dampener fires.

Artifacts: `experiments/post_crash_v2/FINDINGS.md`, `SHIP_PROPOSAL.md`, `sweep_pcd_sigma.py`, `verify_sigma_opt.py`, captured outputs (`sigma_norm.out`, `sigma_sweep.out`, `verify_sigma.out`).

---

## v15_optim 15 DTE Cascade-Only Ship (2026-05-05, portfolio-stage, 15 DTE only)

Same 4-phase Bayesian campaign structure as v32_optim, applied to 15 DTE on v35 algorithm. **Cascade-only ship** — Phase C TP/SL changes tested and rejected (degraded 2024 DD when combined with cascade).

**Headline (N=500 x 8-window canonical, 15 DTE current shipped vs ship):**

| metric | baseline | shipped | factor |
|---|---:|---:|---:|
| 5y compound | +1.53e8% | +3.26e19% | 2.1e11x |
| 22-now compound | +1.93e4% | +4.04e13% | 2.1e9x |
| 5y DD | 78.9% | 74.5% | -4.4pp |
| 22-now DD | 79.8% | 74.6% | -5.2pp |
| 2022 DD | 79.4% | 74.0% | -5.4pp |
| dip DD | 77.9% | 74.2% | -3.7pp |
| 2024 DD | 86.0% | 85.1% | -0.9pp |

Per-window (N=500): 2021 ret +2.34e8%->+4.48e8% (+91%), DD 78.9%->72.5% (-6.4pp); 2022 +16,740%->+321k% (+1818%), DD 79.4%->74.0% (-5.4pp); 2023 +62,420%->+531k% (+751%), DD 72.7%->70.3% (-2.5pp); 2024 +2.88e9%->+6.72e8% (-77%), DD 86.0%->85.1% (-0.9pp); 2025 +34,720%->+144k% (+316%), DD 77.6%->78.3% (+0.6pp); dip +2,578%->+7,605% (+195%), DD 77.9%->74.2% (-3.7pp); 22-now +19,290%->+4.04e13% (2.1e9x), DD 79.8%->74.6% (-5.2pp); 5y +1.53e8%->+3.26e19% (2.1e11x), DD 78.9%->74.5% (-4.4pp).

Parameter changes (15 DTE only, no TP/SL changes): TIER_ALLOC.ultra (95+) 0.18 unchanged; TIER_ALLOC.top (85-94) 0.12->0.17 (opposite of 30 DTE's mid-heavy shape — 15 DTE rewards top-tier concentration); TIER_ALLOC.mid (80-84) 0.15->0.12; TIER_ALLOC.low (75-79) 0.15->0.08 (significantly cut, 15 DTE volume engine smaller); PUT_TIER_ALLOC put_top/put_mid/put_low all cut to 0.08 floor (from 0.10/0.12/0.12 — DD safety).

30 DTE v32_optim shape: 0.20/0.15/0.10/0.10 + monotonic puts 12/10/8. 15 DTE v15_optim shape: 0.18/0.17/0.12/0.08 + all-floor puts 8/8/8. Rationale: 15 DTE's PREMIUM_MULT=1.29 (vs 1.82) and HOLD_DAYS=7 (vs 15) means smaller sigma-thresholds and faster theta — signal-quality-to-execution-cost ratio is best at the top tier (85-94, highest per-trade WR); lower tiers cut because smaller sigma-cushion + faster theta raises false-signal cost.

Methodology: Phase B 100x8, 16 evals Bayesian -> winner 18/17/12/08 + 8/8/8 puts (util +79.6/DD 74.9%). Phase C 100x8, 18 evals -> marginal winner TP=0.33/breadth=40 (util -55/DD 78.1%), never beat current. Phase D 300x8, 8 candidates -> B1 cascade-only crushes JOINT (B1+C1): 2024 DD spiked to 86% under JOINT. Phase E 500x8 ship gate -> B1 cascade-only 5y +3.26e19%/DD 74.5%, PASS with single 2024 soft fail.

Trade-offs accepted: 2024 compound -77% (still +6.7e8%, DD also better -0.9pp there); 2025 DD +0.6pp (within MC noise); 70% absolute DD target unreachable for 15 DTE, -4 to -6pp relative reduction is what's available.

Critical finding: campaign confirmed `monte_carlo_15dte.py` had the same MP-worker env-var-override bug as `monte_carlo.py` (fixed same pattern: TIER_*_OV, TP_BASE_OV, etc.).

Per-trade quality preserved: call TP 57.8%->58.0%; put TP 52.2%->48.0% (drops because 15 DTE puts now rare, 174->553 5y trades, quality of remainder holds).

JOINT rejection: Phase C TP/SL winner combined with B1 cascade spiked 2024 DD 85.1%(B1 alone)->86.0%(JOINT), 5y compound dropped 3.26e19%->5.42e24% at N=300 (then 3.26e19% at N=500) — 15 DTE TP/SL is at a local optimum with narrow tolerance; only cascade has headroom.

Artifacts: `experiments/v15_optim/phase_{b_cascade,c_tpsl,d_validate,e_n500_ship}.py` + logs/jsonl.

---

### Phase call/put split + reallocation (2026-05-05) — both NULL

Two slot-management hypotheses, user-directed, both falsified at v32 + bounded-fill MC. Reallocation mechanism implemented and remains in code env-gated OFF.

Phase 1 — static call/put pool reservation: 6 variants of `(MAX_POSITIONS_CALL, MAX_POSITIONS_PUT)` reserving 2/4/6/7 put slots or capping puts at 4/2. Stage 1 N=80 x 22-now/5y: forcing more puts via call cap loses 5y compound 71-94%; capping puts gains -5 to -9pp DD at 90-99% compound loss. No Pareto improvement — killed at Stage 1.

Phase 2 — reallocation/displacement (`REALLOC_STRATEGY`), previously unimplemented: `Position` extended with `entry_idx`/`entry_underlying`/`premium_pct`; `close_by_sym_idx` lookup built lazily when `REALLOC_STRATEGY!=''`; `_try_realloc()` displaces same-side held position via mark-to-model (`option_pricing.option_pnl_pct` at today's close). 5 strategies tested N=80->150->300.

Bug found+fixed: `_mc_init_worker`/`_mc_iter_worker` weren't passing `close_by_sym_idx` to MP workers, silently failing all realloc attempts under MP — verified fixed via debug counter (~5-20 successful displacements/sim-year).

Stage 3 (`R_score_low_adv10`, N=300x8): P3 fails (22-now compound -11.3% vs baseline), 5y DD +0.9pp worse, dip +6.5pp worse. Stage 2 N=150 apparent win (5y +11.1%, -1.3pp DD) was MC noise — same Stage2->Stage3 reversal pattern as Phase OP1's reverted DD=0.60 ship. Per-trade quality invariant across baseline/candidate — realloc only changes which positions carry to natural exit.

Mechanism stays in code as an env-gated research knob (like `MAX_PUTS_PER_DAY`, `WEAK_WEEKLY_PUT_DROP`); default `REALLOC_STRATEGY=''` = OFF = no production behavior change.

Lessons re-confirmed: lock decisions on 5y, treat 22-now as confirmation only (Stage 2's 5y win was invalidated by 22-now Stage 3); N=150 4-window screening insufficient for ship decisions (Phase OP1 lesson repeats).

See `experiments/bayes_phase_callput_split/FINDINGS.md`, `experiments/bayes_phase_realloc/FINDINGS.md`.

---

## v35 — EARN_BOOST Recalibration on v34 Pre-Boost Scores (2026-05-04, `e77714f`)

The v28 lift table (`experiments/v27_optimization/phase_tp3b_lift_table.json`) was built on v27 historical scores (April 2026). v32 (CWCF dampener), v33 (continuation boost), v34 (CSWC dampener) each shifted the call score distribution the v28 boost amplifies. v35 rebuilds the lift table from v34 `pre_boost` scores (extracted from `weight_info`) so the boost matches the score mix actually arriving at the boost stage.

**Calibration changes (`database/utils/scoring.py`):**

| Param | v34 (prior) | v35 | Why |
|---|---:|---:|---|
| LIFT_TABLE_PATH | `.../phase_tp3b_lift_table.json` | `experiments/v34_calibration/lift_table_v34.json` | rebuilt on v34 pre-boost distribution |
| EARN_BOOST_MAX | 0.50 | 0.55 | v34 lift cells show larger raw lifts (pre1 calls 75-79 +29.2pp) |
| EARN_BOOST_LIFT_NORM_CALL | 22.3 | 14.0 | earlier saturation — calls in 70-79 now reach strength ~1.0 |
| EARN_BOOST_LIFT_NORM_PUT | 16.3 | 16.3 | unchanged, put cells didn't shift enough |
| EARN_BOOST_WINDOW | 5 | 5 | unchanged, pre7 cohort weak across calls and puts |

Lift table big-shift cells (v34 pre-boost vs v27, pp DeltaWR vs no-cohort baseline): low|pre1|70-74 +13.8(N=535)->+21.4(N=879); low|pre1|75-79 +17.7(N=129)->+29.2(N=183); low|pre1|80-84 +22.3(N=17)->+24.5(N=54); low|pre3|75-79 +18.6(N=98)->+15.1(N=312); low|pre3|80-84 +17.1(N=27)->+1.0(N=78) (CWCF/continuation boost effect); low|pre7|80-84 +9.6(N=43)->+3.3(N=99); high|pre1|11-15 +6.9(N=88)->+37.3(N=93) (v32 WCF lift filtered weak-weekly puts); high|pre1|16-20 +5.9(N=163)->+20.6(N=279); high|pre1|21-25 +9.2(N=283)->+22.1(N=447); high|pre7|11-15 -13.3(N=86)->-13.5(N=246) flat (still bad).

H1-H5 ship gate (V34_M55_NC14 winner, 5y vs prior calibration):

| Bucket | Prior WR15/N | v35 WR15/N | ΔWR | ΔN |
|---|---|---|---:|---:|
| 95+ | 76.42%/123 | 80.62%/129 | +4.20pp | +4.9% |
| 90+ | 72.04%/540 | 76.22%/534 | +4.18pp | -1.1% |
| 85+ | 74.80%/1,881 | 75.66%/2,034 | +0.86pp | +8.1% |
| 80+ | 69.68%/6,330 | 70.66%/6,895 | +0.98pp | +8.9% |
| 75+ | 66.47%/19,062 | 66.47%/19,128 | flat | +0.3% |
| <5 | 61.00%/1,482 | 63.71%/1,761 | +2.72pp | +18.8% |
| <15 | 60.67%/12,155 | 60.83%/12,389 | +0.16pp | +1.9% |
| <25 | 57.90%/50,480 | 57.90%/50,480 | flat | flat |

H1: 4 of 5 call tiers >=+0.5pp (>=3 required), no tier regresses >-1.0pp. H3: N drift within +-15%, no tier <50 peaks. H4: <5 +2.72pp/<15 +0.16pp (puts strictly improved/unchanged). H5: 95+/90+/80+ sign-consistent 1y/3y/5y; 85+ has 1y -0.97pp on small N=435 (noise). No MC required per `assessment-backtest.md` — signal density unchanged at 70+.

Sweep methodology `experiments/v34_calibration/`: Phase 1 (15 variants) gradient over MAX/NORM_CALL/NORM_PUT/WINDOW/PUT_ADMIT; Phase 2 (15) combined stacks + portfolio-weighted alpha ranking; Phase 3 (29) fine 2D grid around (MAX=0.55, NC=15); `verify_winner.py` confirms multi-window sign consistency. Total runtime ~3 min after one-time ~90s cache build.

---

## v32_optim Joint Bayesian Ship (2026-05-04, portfolio-stage, 30 DTE only)

Joint cascade + TP/SL retune via 4-phase Bayesian campaign on v34. **Largest single portfolio ship to date — 68x 5y compound improvement at neutral DD.**

**Headline (N=500 x 8-window, vs baseline):**

| metric | baseline | shipped | factor |
|---|---:|---:|---:|
| 5y compound | +1.42e28% | +9.67e29% | 68x |
| 22-now compound | +8.45e24% | +2.42e25% | 2.86x |
| 5y DD | 72.4% | 72.9% | +0.5pp (MC noise) |
| 22-now DD | 69.9% | 70.1% | +0.2pp |

Per-window N=500: 2021 +4.72e7%->+2.05e8% (+335%), DD 66.8%->58.8% (-8.1pp); 2022 +6.61e5%->+2.76e6% (+318%), DD 68.7%->71.4%; 2023 +3.88e6%->+7.79e6% (+101%), DD 66.5%->70.5%; 2024 +2.98e9%->+2.75e9% (-7.9%), DD 58.2%->60.1%; 2025 +1.40e7%->+2.75e7% (+96%), DD 69.4%->67.0% (-2.4pp); dip +2.38e4%->+1.52e4% (-36%), DD 57.3%->51.8% (-5.5pp); 22-now +8.45e24%->+2.42e25% (+186%), DD 69.9%->70.1%; 5y +1.42e28%->+9.67e29% (+6,708%), DD 72.4%->72.9%.

Parameter changes (30 DTE only; 15 DTE config split off, kept prior values): TIER_ALLOC.mid (80-84) 0.12->0.10 (lower per-trade WR, cut frees velocity for 75-79); PUT_TIER_ALLOC.put_top(<=15) 0.10->0.12 (strongest put bucket was under-allocated); put_mid(16-20) 0.12->0.10 (restore monotonic shape); put_low(21-25) 0.12->0.08 (weakest, cut); TP_BASE 0.35->0.33 (v34's stronger per-trade WR compounds faster with a narrower TP); TP_STRESS 0.40->0.42 (paired with wider stress SL); SL_BASE -0.30->-0.27 (tighter, faster capital recycling); SL_STRESS -0.35->-0.40 (breathing room in elevated-vol regimes); BREADTH_THRESHOLD 50->40 (stress band fires less often).

Methodology: Phase B 100x8, 16 evals (5 hand seeds + 11 Bayesian) -> winner 20/15/10/10 + 12/10/8 puts. Phase C 100x8, 10 evals over TP/SL/breadth (cascade locked) -> winner TP=0.33/0.42, SL=-0.27/-0.40, breadth=40. Phase D 300x8, 8 candidates -> JOINT_B1_C1 wins (5y +8.68e29%/DD 69.9%). Phase E 500x8 final gate -> headline 5y +6,708% at neutral DD, shipped.

Per-trade quality preserved: call TP 59.4%->58.9% (noise), put TP 45.7%->45.0%.

**Critical wiring fix shipped alongside:** Windows multiprocessing `spawn` re-imports `monte_carlo.py` per worker, ignoring in-process module-global patches — sweeps mutating `mc.TIER_ALLOC={...}` were silently running with `strategy_config` defaults in workers. Fix: env-var overrides at module load time (`TP_BASE_OV`, `TIER_ULTRA_OV`, etc.) so workers inherit sweep params via env. Canonical pattern: `experiments/v32_optim/phase_b_cascade.py` `apply_cfg()`.

Trade-offs accepted: 2024 -7.9% (noise); dip -36% but DD also -5.5pp (more conservative sizing in choppy tape, matching the DD<70% priority); 5y DD +0.5pp/22-now +0.2pp (within +-2-3pp MC noise at N=500).

Artifacts: `experiments/v32_optim/phase_{b_cascade,c_tpsl,d_validate,e_n500_ship}.py` + logs/jsonl.

---

## H3 — DD-Soft Band Call Alloc Contraction (2026-05-04, portfolio-stage)

Mild contraction of call allocation when running portfolio drawdown is in [0.40,0.60]. Linear interp: alloc multiplier 1.0 at DD=LO, 0.50 at DD>=HI. Calls-only, puts unaffected. Distinct from F3F (breadth-driven). Disabled for 15 DTE (not validated under bounded-fill MC). The separate binary DD circuit breaker was retired 2026-05-11.

Why: `dd_ledger` investigation (`experiments/dd_ledger/`, 4.83M trade-tape rows from N=300x8-window MC) found high-DD-concentration cells — CALL 75-79 x entry_dd=mid(0.20-0.40) x regime=HEALTHY: DD-conc 19.7x (1.8% of trades = 36% of cohort DD$); CALL 80-84 x concur_calls=hi(8-12): DD-conc 5.95x (11% of trades = 65% of DD$); CALL 75+ x entry_dd=mid x brd=mid_lo: DD-conc 7.16x.

H3 and H4 (`MAX_POSITIONS_CALL` cap) tested in parallel. H4 (any cap 8/10/12) failed P4 (2023 -33% MedRet at N=500 — narrow-bull regime cost is structural). H3 v1 (LO=0.20 HI=0.40 FLOOR=0.50) failed P4 (too-frequent contraction at shallow recoverable DDs). H3 v2 (LO=0.40 HI=0.60 FLOOR=0.50) shipped clean — only fires deep-tail.

P1-P6 validation (N=500x8 vs PYTHONHASHSEED=0 baseline): 2021 DD-C 65.8%->68.2% (+2.4); 2022 72.3%->72.1% (-0.2); 2023 62.0%->58.5% (-3.5); 2024 54.6%->54.2% (-0.4); dip 57.9%->56.9% (-1.0); 22-now 75.5%->72.4% (-3.1); 2025 70.9%->68.1% (-2.8); 5y 75.8%->71.4% (-4.4).

P3 PASS (5y/22-now compound MedRet within MC noise floor: 5y -8.9%, 22-now -2.9%; baseline N=300->N=500 alone drifts up to +-25% on identical strategies). P4 PASS (no annual >25% regression, worst 2025 -5.1%/dip -4.1%). P5 PASS (0% collapse every cell). P6 PASS (6 of 8 windows improve DD, only 2021 regresses +2.4pp, single-year noise). Per-trade quality unchanged: Call TP 58.3%->58.4%, Put TP 46.0%->46.0%, CTrd 3,566->3,578.

Constants (`strategy_config.py`): `DD_SOFT_BAND_LO=0.40`, `DD_SOFT_BAND_HI=0.60`, `DD_SOFT_CALL_FLOOR=0.50`.

Mechanism (inside `_try_fill_call`, before `premium_cost`):
```python
dd_scale = 1.0
if DD_SOFT_BAND_HI > DD_SOFT_BAND_LO and dd > DD_SOFT_BAND_LO:
    if dd >= DD_SOFT_BAND_HI:
        dd_scale = DD_SOFT_CALL_FLOOR
    else:
        t = (dd - DD_SOFT_BAND_LO) / (DD_SOFT_BAND_HI - DD_SOFT_BAND_LO)
        dd_scale = 1.0 - t * (1.0 - DD_SOFT_CALL_FLOOR)
alloc_frac = TIER_ALLOC[tier] * reg_scale_c * dd_scale
```

Wired in `monte_carlo.py`, `monte_carlo_15dte.py` (disabled), `backtest_cascade.py`, `backtest_cascade_15dte.py` (disabled), `api.py /api/backtest/run`, `tests/test_strategy_config_drift.py`. Portfolio-stage only, no version bump. Full hypothesis derivation: `experiments/dd_ledger/FINDINGS.md`.

---

## v32 — Call-Side WCF-Mirror Dampener (2026-05-01, `43eecea`)

Mirrors the v27 put WCF lift on the call side. When `overall >= 75 AND wadj < 1`, dampens toward 55:

```
weakness     = clip((1 - wadj) / 1, 0, 1)
overall     -= 0.95 x weakness x (overall - 55)
```

Why shipped: `experiments/miss_ledger/` (per-signal ledger over 41,389 v31 peaks, joined to 30dte_opt barriers at w=15d) found the wadj-neg cohort was the largest single-feature MISS driver system-wide: calls 70+ with `wadj<0` miss at 52.5% vs cohort baseline 41.4% (lift 1.27, z=+10.1), compounding with `vsig=CONVICTION` (56.1% miss)/`vmag=mid` (57.5%). The asymmetric weekly amplifier (puts x1.5, calls x1.0) left calls with bearish weekly drag fully exposed.

Calibration (32 variants, `experiments/miss_ledger/call_wcf_mirror_sweep.py`): K=0.95, wadj_cutoff=+1, score_gate=75, lift_target=55. Wider cutoffs (5-17) gave larger per-trade lift but collapsed N 27-90% — failed H3. Evidence concentrated entirely at wadj<0, so the discrete narrow-cutoff form matched better than a tanh saturating amp.

H1-H5 ship gate (5y/3y/1y vs 30dte_opt barriers, w=15d):

| Bucket | v31 baseline | v32 sweep prediction | ΔTP | ΔN |
|---|---:|---:|---:|---:|
| 95+ | 69.8% | 69.0% | -0.72pp | -2.3% |
| 90+ | 66.5% | 66.3% | -0.18pp | -3.5% |
| 85+ | 66.8% | 67.8% | +0.95pp | -5.3% |
| 80+ | 62.4% | 63.5% | +1.09pp | -7.5% |
| 75+ | 59.9% | 61.0% | +1.14pp | -9.9% |
| 70+ | 58.6% | 58.9% | +0.29pp | -3.0% |

Multi-window 75+: 1y +0.32pp/3y +1.49pp/5y +1.14pp, sign-consistent. Puts unchanged (gate=75). No MC required per `assessment-backtest.md` "Why N=300 canonical MC is NOT the gate for scoring changes" — signal density shift <30% on every primary tier. `weight_info` adds `cwcf_dampen` when active.

Closes the call-side leg of Priority #4 (Weekly Adj Smooth Scaling) in known-issues.md. Put-side `wadj ∈ (-13,0)` band remains open for future investigation.

---

## Phase A — Single Source of Truth (2026-04-30, commits 859b946–b5b3d3e)

Replaced 7-location duplication of strategy constants with `strategy_config.py`: `OptionStrategyConfig` (premium-side: TP, SL, slippage, PUT_TP, PUT_SL hold bars) and `DteStrategyConfig` (per-DTE: HOLD_DAYS, PREMIUM_MULT, HARD_SELL_LOSS, cascade allocs, MaxPos, F3F, regime slopes, DD soft-band, EARN_SUPP_PUT, CT). Derived values (`NET_TP`, `TP_SIGMA`, `NET_HARD_SELL`) are `@property`. `STRATEGY_30DTE`/`STRATEGY_15DTE` both alias `SHARED_OPTION` so `assess_combos()` auto-dedupes the 15 DTE TP% pass (Python identity check -> single combined run, saves ~5.5 min/assess cycle).

Two latent bugs surfaced+fixed: (1) `backtest_cascade.py` sigma-thresholds stale at v19 values (TP=0.30 instead of 0.35, SL=-0.35 instead of -0.30) — firing TP 17% too eagerly for ~2 weeks; (2) `trader.py` `trader tp/sl` helpers had pre-H5 display values (call_tp_pct=30/35 instead of 35/40).

Consumer files: `monte_carlo.py`, `monte_carlo_15dte.py`, `backtest_cascade.py`, `backtest_cascade_15dte.py`, `trader.py`, `api.py`, `src/pages/Backtest.js`+`Dashboard.js`.

---

## v31 — Strict-Future Earnings Semantics + effective_date Column (2026-04-30)

Fixes the D0 same-day-reaction edge case in EARN_BOOST proximity and V6 volume-amplifier suppression. Prior `bisect_left`/`range(0,W+1)` accepted `delta=0` (signal_date==effective_date), so a signal on an earnings reaction day hit `pre1` with full proximity=1.0 — inflating score + muting volume on prices that already contained the gap.

Discovery case: NBIS Apr 29 BMO at 08:00 -> v30 score inflated ~70->79 via `pre1 75-79` lift cell (+17.7pp). v31 fixed score = 70, `ern_boost=None`.

Fix: `bisect_left`->`bisect_right` and `1<=delta<=W` in three days-to-earn helpers; `range(0,W+1)`->`range(1,W+1)`; `effective_date>today` (was `>=`); `_earnings_supp_strength` rejects `nearest_d<1`.

Schema: new `EarningsDate.effective_date` DateField, indexed on `(symbol, effective_date)`, backfilled in `ensure_schema()` (16,420 rows, 8,588 AMC shifted forward). `_load_effective_earnings_dates` reads the column directly (no per-row Python compute).

Impact: per-trade WR neutral vs v30 (rare D0 cohort, ~1 signal/stock/year). `trader recalculate --force --full` completed 2026-04-30.

---

## v30 — AMC-Aware Earnings effective_date (2026-04-29, `9a9da33`)

`EarningsDate.call_time` drives effective-date shift: AMC events (call_time >= 16:00 ET) shift forward to next trading day so the date represents when the price reaction appears.

Discovery case: MDLZ Apr 28 AMC -> reaction Apr 29; a trade entered Apr 28 close was previously non-spanning (vega not sampled) — with effective_date it's now spanning, vega sampled, loss correctly modeled.

Applied at all earnings-loading sites (score-stage AND portfolio-stage) -> score-stage change -> `ALGORITHM_VERSION` bumped to v30 (`9a9da33`). Per-trade WR ~equivalent to v29 (+-0.1pp on most buckets).

---

## Option-Pricing-Aware MC (shipped 2026-04-29)

Replaces static sigma-barrier P&L assumption with delta+theta+vega closed-form model + seeded random fill within the trigger bar.

Key files: `option_pricing.py` (`option_pnl_pct(side, U_t, U_0, bars_held, premium_pct, vega_ratio, delta)` + `adjusted_underlying_threshold()`, reserved for Phase 2); `monte_carlo.py` (`compute_trade_outcome`/`compute_put_outcome` record fire-bar OHLC + trigger barriers, `resolve(outcome, rng)` applies bounded fill+theta+vega, `COLLISION_MODES=['seeded']`); `backtest_cascade.py` (same model, mid-bar fill deterministic, `OPTION_PRICING_AWARE=0` env reverts to legacy for A/B).

Bounded fill semantics (`3432fb8`):

| kind | side | sample range |
|---|---|---|
| tp | call | [tp_level, high] — limit-or-better |
| tp | put | [low, tp_level] — limit-or-better |
| sl | call | [low, sl_level] — stop-or-worse |
| sl | put | [sl_level, high] — stop-or-worse |
| both | any | [low, high] — path ambiguous |
| hard | any | day-15 close — deterministic |

**Bimodal SL fill model (shipped 2026-04-30):** empirical audit of 124,872 real SL-firing bars found median close position = 0.992 (half close AT barrier, intraday triggers), mean = 0.698. Prior uniform [low,sl_level] overstated SL loss by ~5-15% of premium per fire. New semantics: intraday trigger (open>sl_level for calls) -> fill=sl_level deterministic; gap-through (open<=sl_level for calls) -> fill ~ Uniform(low,open). Calibration fix, not a strategy change — no version bump.

Vega ratio sampling: trades spanning an EarningsDate effective_date sample per-iter empirical post/pre price ratio from `experiments/iv_crush_samples.csv` (N=20,870 ATM options); non-spanning trades use vega=1.0. Earnings map always loaded (no longer gated by `IV_CRUSH_ENABLED`).

---

## Phase OP1 — Portfolio Retune Attempted, REVERTED (2026-04-29)

Screening: N=80 22-now on 12 configs, C2 (DD=0.60) appeared to win; shipped `f2329a1`.

OP1b validation (N=300x8) reversed the finding:

| Window | C0 baseline (DD=0.68) | C2 (DD=0.60) | Δ Return | Δ DD |
|---|---:|---:|---:|---:|
| 22-now | +63.8T%/77.0% | +73.8T%/76.0% | +15.6% | -1.0pp |
| 5y | +57.0 Quad%/75.3% | +53.9 Quad%/77.8% | -5.4% | +2.5pp |

C2 is 22-now-biased — per-year losses in 2022/2023/2025 compound past 2021/2024 wins on 5y. Reverted to `DD_CIRCUIT_BREAKER=0.68` via `2233dc8`.

**Lesson: N=150 4-window validation is insufficient for ship decisions — need N=300+ x 8-window before ship.**

Dead ends: wider call SL (-0.40/-0.45) -> 22-now WorstDD 82.0%, floor breach; wider put SL (-0.30) -> lifts TP rate but -88% 22-now compound (capital velocity); smaller ULTRA tier (0.13) -> marginal at best.

---

## Phase OP2 — Investigation Complete, NOT SHIPPED (2026-04-30)

Full pipeline: 27-axis screen + 35-eval Bayesian combo + 13 canonical MC candidates at N=300x8. **No candidate passes the 80% DD ship gate, including the baseline itself** — that gate was calibrated for the old 3-mode Conservative MC; under bounded-fill seeded MC, baseline runs 81-88% DD on every window with 0-22% collapse rate.

What failed: all Phase 2 aggressive combos (SL=0.22, HOLD=12, ULTRA=0.12, DD<=0.65) had 34-55% collapse rates — tighter SL -> more bounded-fill draws -> catastrophic tail.

Best candidate (Phase 4, variance reduction): `WIDE_SL_MP10` (SL=-0.35, MaxPos=10) — 5y compound +1,681% better than baseline ($1.28 quadrillion vs $72 trillion), passes collapse+annual-25% gates, fails DD by 1-3pp on 4 windows.

Three options left for user decision: (A) relax gate to 90% DD floor and ship `WIDE_SL_MP10`; (B) revisit MC fill model fidelity; (C) no ship, park investigation.

Bimodal fill Phase 4 re-run: calibration delivered 1-11pp DD improvement, 2022/2023/dip now PASS 80% gate, 2021/2024/2025/22-now/5y still breach. `WIDE_SL_MP10` still fails relative gate (introduces dip-window collapse rate 3.7% vs 0%). No portfolio knob shipped.

Do NOT pursue without structural reason: aggressive optimization (tighter SL/HOLD/ULTRA) — 30-55% collapse under bounded-fill; reliance on deterministic backtest alone for ship decisions; Design B as HARD_SELL_LOSS replacement (Phase 1 marginal +0.15 utility, catches only 5-9 trades).

Preserved infrastructure: `experiments/op2/orchestrator.py`, `op2_runner.py`, `phase[1-4]*_results.json`.

---

## V6 + DD Circuit Breaker Re-Ship (2026-04-29, `b8c2666`)

V6 (volume-amplifier earnings log-gradient, originally shipped as v29/`8473cba` and reverted) re-shipped after IV-crush evaluation revealed the revert was based on flat-pricing MC artifacts.

What shipped: `ALGORITHM_VERSION` flipped back to `8473cba` (v29 score rows already in DB); `volume_amplifier.py` V6 pre-only log-gradient (W=2 cal days, M=1.0, pre-only); `monte_carlo.py` `DD_CIRCUIT_BREAKER` default -> 0.68 (after OP1 revert); `backtest_cascade.py` mirror.

N=500x8-window canonical (IV stochastic, vs v28+stoch baseline): V6 unmodified beats v28 by +1284% 5y compound, +379% 22-now. All Conservative DD-C <=80% on V6 (B68); V0 breaches 5 of 8 windows.

---

## 15 DTE Variant — Phase 15B + Phase 16 (shipped 2026-04-28)

Why: 15 DTE needs only 0.774sigma underlying for TP=30% (vs 1.092sigma for 30 DTE); AvgTPBar 1.3-1.5 bars vs 1.7-2.2 — capital recycles 30-50% faster.

Phase 15B: direct DTE swap showed DD-C 87% breaching the 80% floor on 3 windows. `DD_CIRCUIT_BREAKER=0.60` combined with MaxPos=8 and F3F floors=0.40 fixed the breach.

C1 shipped config vs 30 DTE H5:

| Param | 30 DTE | 15 DTE C1 |
|---|---|---|
| `HOLD_DAYS` | 15 | 7 |
| `PREMIUM_MULT` | 1.82 | 1.29 |
| `HARD_SELL_LOSS` | -0.40 | -0.45 |
| `MAX_POSITIONS` | 14 | 8 |
| `F3F_CALL_FLOOR` | 0.50 | 0.40 |
| `F3F_PUT_FLOOR` | 0.50 | 0.40 |
| `DD_CIRCUIT_BREAKER` | 0.68 | 0.60 |

N=500 validation: 22-now +54x, 5y +346x over 30 DTE, all Conservative DD-C <=74.3%. Accepted regression: 2025 -54% (DD breaker fires more aggressively in choppy 2025). Historical note: the binary DD breaker was retired 2026-05-11; v54 later moved the sector-breadth crash/recovery response into persisted scores.

Phase 16 (full integration): `assess_scores.py` accepts `--dte 15` (bypasses barrier_outcomes cache since K/M differ); `backtest_cascade_15dte.py` with C1 defaults, `compute_and_store_temporal()` writes `BacktestTemporalStats` with `dte_strategy='15'`; API `/api/assessment?dte=15`, `/api/backtest/temporal?dte=15`, `/api/backtest/run?dte=15`; frontend Assessment.js 30/15 DTE toggle (persisted localStorage); schema `ScoreAssessmentRun.dte_strategy`/`BacktestTemporalStats.dte_strategy` with composite unique indexes.

`barrier_outcomes` cache now stores both barrier sets (`'30dte_generic'`, `'15dte_opt'`), ~3.6GB total; `assess_scores.calculate_forward_returns` auto-detects via `_detect_barrier_set()`.

Runtime estimates: `trader assess --force` (both DTEs, all windows+temporals) ~10 min; `--dte 15` alone ~5.5 min; `trader recalculate --force --full` end-to-end ~25 min.

---

## Phase EVR-1 — Earnings Volume Suppression Re-Calibration (per-trade gate 2026-04-30)

Per-trade gate at 5y on v31 baseline PASSED for OFF (no earnings volume suppression). OFF admits +8 extra 95+ peaks and +18 extra 90+ peaks, marginal WR of admitted signals ~75%/77% (above call BE); 80+ bucket +0.4pp better under OFF (+39 N). Stronger suppression hurts. Canonical MC validation gated on Phase OP2 landing first. Full spec + EVR-2 next steps: `.claude/docs/known-issues.md` Priority #17.

---

## Phase EVS — V6 Log-Gradient (Reverted, then Re-shipped)

Original V6 reverted 2026-04-28 after canonical MC failure (5y -11.3%, 22-now -35.8%) — mechanism: slot-displacement interaction with EARN_SUPP_PUT caused compound regression. Re-shipped 2026-04-29 after IV-aware MC evaluation showed V6 is unambiguously safer and higher-returning under realistic option pricing.

---

## IV Crush Model (Research Tool — NOT Enabled in Live Trading)

`iv_crush_model.py` — env-gated (default `IV_CRUSH_ENABLED=0`). Two modes: `deterministic` (kappa_pre x kappa_post compound) and `stochastic` (sample from `experiments/iv_crush_samples.csv`, N=20,870 ATM options, Feb 2025-Apr 2026). Superseded for canonical MC by the inline option-pricing-aware path (vega sampled inside `option_pnl_pct`). Kept for: `monte_carlo_15dte.py` and sweep scripts not yet ported; `_load_samples()`/`find_spanning_earnings()` helpers reused by the new path.

Usage:
```bash
IV_CRUSH_ENABLED=1 IV_CRUSH_MODE=stochastic IV_CRUSH_SEED=42 \
  ALGORITHM_VERSION_PIN=8473cba PYTHONIOENCODING=utf-8 python -u monte_carlo.py
```

---

## Phase OP2 — 15 DTE (Still Pending)

No candidate sweep run yet for 15 DTE. Same gate-calibration issue almost certainly applies (likely worse — smaller hold window means tighter theta-induced variance). Deferred until 30 DTE gate calibration is decided.

---

## Performance Benchmarks (post-2026-04-27 architecture refactor)

- Recalculate (`--force --full` 10y x 740 stocks): ~15 min (was ~60 min)
- Auto-assess tail (5 windows): ~3 min (was ~30 min) — uses `barrier_outcomes` SQLite cache
- Backtest temporal (calendar data): ~3.2 min (was ~10 min) — uses Numba JIT walks
- End-to-end full-recalculate flow: ~21 min (was ~100 min)

The `barrier_outcomes` cache (`.cache/barrier_outcomes.db`, ~12.7M rows) is auto-refreshed nightly via `trader update` (`database.barrier_cache.refresh_recent(days=160)`). Cache invalidated only by new price data — recalculate/version bumps do NOT invalidate it.
