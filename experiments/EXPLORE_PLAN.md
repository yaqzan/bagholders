# EXPLORE_PLAN — Time-to-2x Concentration & Regime-Wave Exploration

**Status:** Build complete (B1+B2), unit tests green, coarse sweeps queued off_market. Awaiting coarse results to build the frontier, run the A/B, and pick drill cells. Track C is DESIGN-ONLY this pass.

**Stage:** v74 Apex profile · 30 DTE · calls-only · TP +30 / SL −70 · dead-hold ON (collapse-PREVENTING) · 5 Stage-3 DD levers ON (RXDD/SVR/MWDD/TVDD/BDIV) · uncapped · $50k base. **PORTFOLIO-STAGE research only** — NO `ALGORITHM_VERSION` bump, NO `trader recalculate`, NO scoring-code change. Uses existing v74 score rows.

---

## 0. The New Objective — minimum time-to-2x, collapse-TOLERANT

The system has optimized compound and WorstDD for its whole history. It has **never** optimized for *how fast a fresh start doubles*. This run introduces that metric. It is **start-date dependent**, so every config is evaluated over **rolling start dates across the 10y history** (so the metric means "if I start at a random point, how fast" — not "if I start in the lucky 2024 window") AND randomized fills (MC seeds).

### Exact metric definitions (per config, pooled across all rolling-start windows × all MC seeds)

| Metric | Definition |
|---|---|
| **median_days_2x** | Median **calendar** days from start to first equity ≥ 2× start ($100k), **among paths that reach it**. `t2x_bar` (trading-bar index) × 1.449 cal-days/bar. |
| **p25_days_2x / p75_days_2x** | 25th / 75th percentile of the same (linear-interp quantile), among reachers. |
| **p_2x_ever** | P(reach 2x at any point within the path horizon). |
| **p_2x_before_50dd** | P(reach 2x **strictly before** the first 50%-drawdown-from-peak). The path-ordering metric — a 2x that arrives only *after* a 50% DD does NOT count. (`t2x_bar < t_50dd_bar`, strictly.) |
| **p_collapse** | P(final equity ≤ 20% of start = ≤ $10k for a $50k start). Collapse-TOLERANT means we *report* this, not gate on it — but it is the y-axis of the decision frontier. |
| median_compound_pct / worst_dd_pct | Reference only (median final compound %, max DD %). |

**Why these and not compound/WorstDD:** the objective is explicitly speed-to-double under a collapse budget. The grounding shows v74 Apex returns are radically path-concentrated (2024 +288% carries everything; jumps unpredictable from the curve's own momentum). A "median 5y +1,143%" hides that a random starter may sit in a long flat. time-to-2x + p_2x_before_50dd is the honest "how fast, how safe" pair.

### Engine instrumentation that makes this computable (landed by Build B1, additive, default-OFF = byte-identical)

`monte_carlo.py` (+40/−2, portfolio-stage infra, no version bump):
- `STARTING_CASH` reads `STARTING_CASH_OV` env (default 50000.0).
- `run_single_sim` records `t2x_bar` (first bar equity ≥ 2× start) and `t_50dd_bar` (first bar DD-from-peak ≥ 50%) alongside the pre-existing `t1m_bar`.
- `MC_RETURN_PATHS=1` exports per-iter arrays `finals` / `dds` / `t1m_bars` / `t2x_bars` / `t_50dd_bars` / `n_trading_days` / `starting_cash`.
- `MC_RESULTS_JSON` sidecar now carries the full per-iter `paths` block (previously only 5 aggregate keys — path-level data was unreachable by a subprocess driver).
- NEW `WIN_START` / `WIN_END` / `WIN_LABEL` env → single arbitrary window (this is what enables monthly/quarterly start-rolling).

> The B1 metric helper (`experiments/concentration_2x/sweep.py`) does the path-ordering, quantiles, collapse tally, calendar conversion, gross-cap grid build, and monthly-window rolling — all unit-tested on synthetic arrays with known answers.

---

## 1. The Three Tracks

### Track B — Concentration grid (BUILT, queued)
Replace the tier cascade with **flat (N concurrent positions × alloc% each)**, top-conviction-first, score ≥ 75 calls only, *on top of the live v74 Apex sleeve* (the 5 DD levers + F3F + DD-soft-band still multiply into `alloc_frac` — this is concentration WITHIN the shipped strategy, the correct baseline, not a pure flat allocation).

- **Grid:** N ∈ {1,2,3,4,5,7,10,14} × alloc ∈ {10,15,20,25,33,50}%, **gross-capped at 100%** (drops e.g. n10×a25=250%, n14×a10=140%; keeps n2×a50=100%, n10×a10=100%) → 26 flat cells.
- **+ cascade_ref** cell: production cascade ULTRA20/TOP15/MID8/LOW3, MaxPos14 — the reference point.
- **Flat cell wiring:** all 4 call tiers `TIER_*_OV` set equal, `TIER_OVERFLOW_OV=0` (score<75 disabled), `MAX_POSITIONS_OVERRIDE=N` + `MAX_POSITIONS_CALL=N`, `MAX_POSITIONS_PUT=0` + put tier allocs zeroed (calls-only enforced two ways). Engine's native fill order is already top-conviction-first (primary score≥75 sorted by −score, ct_call promoted).

### Track A — Equity-curve regime wave (BUILT; A/B logic green; **engine arm NOT landed**)
A smooth regime-strength index from market-context inputs that **scales aggression up in strong regimes, withholds in weak**, A/B'd against an **equity-curve-native** variant (drawdown-from-peak / weekly-state-classify) to test whether the equity curve adds anything beyond the market inputs.

- `wave.py` — PURE primitives (no MySQL/MC/engine import):
  - `regime_strength_index(vix, breadth, mcclellan, trin, bdiv, semivol_r)` → strength ∈ [0,1] via weighted power-mean (p=0.4, sub-geometric so one VIX alarm crushes an otherwise-strong reading); `strength_to_aggression` → bounded mult [0.50, 1.50], neutral 0.5→~1.0.
  - Equity-native: `drawdown_from_peak`, `drawdown_space`, `WeeklyStateClassifier` ({flat, skyrocketing, drawing_down, recovering} on weekly-downsampled curve with dead-band + hysteresis to **suppress flat-curve whipsaw** — explicitly handles the Jan2025 +51%→Mar −28% cautionary case so SKY is not chased into the drawdown), `EquityNativeScaler`.
- `analyze.py` — the core A/B: extracts no-look-ahead predictors at each anchor (equity-native: dd, 4w momentum, weekly-state one-hot; market: regime strength) and forward H=21d targets; reports univariate corr + per-state forward-return means + **nested-model incremental OOS R² (M0: 1+strength vs M1: +equity-native, K-folds split BY PATH** so curve noise can't leak train→test) with a SHIP/NULL/HURTS verdict vs a noise band.
- **A/B's prior:** curve momentum does NOT predict the next jump (grounding: Jan2025 +51% → Mar −28%). The analyzer is built to confirm/refute that, and the cautionary case is unit-asserted in the classifier.

### Track C — Tandem (DESIGN ONLY this pass — see §5)
Regime-gated concentration: concentrate when the wave says strong, diversify/withhold when weak. Build *after* A+B coarse results tell us (a) which flat (N,alloc) cells sit on the time-to-2x frontier and (b) whether the regime index actually carries forward signal.

---

## 2. What Was Built (files)

| File | Track | Role |
|---|---|---|
| `experiments/concentration_2x/sweep.py` | B | Grid build (gross-cap), monthly/quarterly window roller, per-cell flat/cascade env wiring, time-to-2x metric computation, JSON output. |
| `experiments/concentration_2x/test_metrics.py` | B | 15 synthetic test fns (path-ordering, collapse boundary, quantiles, grid gross-cap, env wiring). |
| `experiments/concentration_2x/README.md` | B | Design + interpretation caveats (levers stay ON; flat = concentration within Apex sleeve). |
| `experiments/equity_wave/wave.py` | A | Regime-strength index + aggression map + equity-native scaler (pure primitives). |
| `experiments/equity_wave/test_wave.py` | A | 72 synthetic tests (strength range/monotonicity, flat-whipsaw guard, classifier states, scaler bounds). |
| `experiments/equity_wave/analyze.py` | A | Equity-vs-market A/B (nested OOS R² by-path, per-state forward means, verdict) + `--selftest`. |
| `experiments/equity_wave/README.md` | A | Strength formula, flat-whipsaw guard, exact wiring point, the daily-curve-export gap, command set. |
| `monte_carlo.py` | B (engine) | Additive time-to-2x instrumentation (t2x_bar / t_50dd_bar / STARTING_CASH_OV / WIN_* / full paths block in MC_RESULTS_JSON). Default-OFF = byte-identical. |

---

## 3. Unit-Test Status

- **Track B** (`test_metrics.py`): ran inline, ZERO MySQL/MC — **all 15 checks PASSED**. Covers exact median/P25/P75, the strict-before path-ordering (t2x@40 after t50@10 does NOT count; boundary t2x==t50 NOT counted), collapse boundary ($10k/$9999/$10001), collapse independent of 2x, calendar conversion, quantile interp, monthly-window roll (1st-of-month, clamp end, drop <180d runway), grid gross-cap, and the flat/cascade env-wiring assertions.
- **Track A** (`test_wave.py`): **72/72 PASS** — strength range/monotonicity/power-mean asymmetry, NaN safety, aggression bounds, drawdown_from_peak/space, WeeklyStateClassifier (incl. the flat-curve de-whipsaw + post-+51%-jump SKY→DOWN flip), EquityNativeScaler bounds.
- **Track A analyzer** (`analyze.py --selftest`): **GREEN** — correctly verdicts NULL on a constructed market-driven-only population (OOS gain −0.0027 inside noise band) and ADDS-POWER on a constructed curve-predictive population (+0.0380 OOS R²) → the A/B logic distinguishes real curve signal from noise.

---

## 4. What Was Queued (coarse sweeps)

Submitted under the task queue at **`high` priority + `--window off_market`** (market was OPEN at ~1:20 PM ET Mon 2026-06-15; both deferred until after 16:00 ET close so they never outrank the scheduled `trader update`), `--db light`, `--cpu 6`, `--restartable`, `--env PYTHONIOENCODING=utf-8`. Smokes (#202 Track B, #203 Track A) ran `normal` and are GREEN.

| Task | id | Stage | Cmd | Window |
|---|---|---|---|---|
| Track B coarse | **#204** | 26 flat + cascade_ref × quarterly-roll (`--step-months 3`, ~38 windows) × N=100 | `python experiments/concentration_2x/sweep.py --stage coarse --step-months 3 --workers 6` | off_market |
| Track A coarse **baseline** | **#205** | N=100 × 8 windows (2021,2022,2023,2024,2025,dip,22-now,5y), exports paths + t2x | `python monte_carlo.py` (MC_NO_DB_PERSIST=1, MC_RETURN_PATHS=1, N_ITER=100, MC_RESULTS_JSON=…/coarse_baseline.json) | off_market |

Pre-existing high heavy-MC jobs **#195/#196** also wait for the same off_market window. All admit after 16:00 ET.

### ⚠ Track A build-gap (must fix before the market-vs-equity A/B can run)
The Smoke agent grep-confirmed **`MC_EMIT_CURVE` / `AW_ENABLED` / `AW_MODE` / `aw_scale` DO NOT EXIST in `monte_carlo.py`** — B2 documented them as future ~6-line additive wiring, not landed. Consequences:
1. Only the **baseline** Track A arm was submitted. Submitting market/equity arms now would silently no-op (AW_ENABLED ignored) → baseline-identical runs.
2. The analyzer's primary input — the **daily/weekly equity curve** — is not exported (only first-passage bars + worst-3 DD episodes are). The A/B needs the curve.

**Two small additive landings required (default-OFF, byte-identical when env unset):**
- (a) `aw_scale` arm at `monte_carlo.py:~2562/2668` (the `alloc_frac = TIER_ALLOC[...] * reg_scale_c * dd_scale * … * bdiv_scale` line), gated on `AW_ENABLED`/`AW_MODE` (market | equity), reading the per-date maps already loaded each day (vix/mcc/trin/bdiv/breadth/svr at ~2495-2502) for market mode, and the running equity peak/dd + weekly state for equity mode.
- (b) `MC_EMIT_CURVE`-gated daily (or weekly-downsampled) equity-curve export into the `MC_RESULTS_JSON` paths block.

The **baseline #205 still produces real time-to-2x objective data** (t2x_bars/t_50dd_bars/finals/dds + WIN_* are landed), so Track A's *objective* read is not blocked — only the *market-vs-equity A/B* is.

---

## 5. Track C — Tandem DESIGN (build after A+B coarse)

**Hypothesis:** the time-to-2x frontier is a speed↔DD tradeoff (smoke already shows flat_n5_a15 = 52d@49.8%DD vs cascade_ref = 132d@26.8%DD). A *static* concentration cell is a fixed point on that frontier. A **regime-gated** concentration could ride the frontier dynamically — concentrate (few, large slots) when the wave says the regime is strong (chase the 2x fast), diversify/withhold (many small slots, or reduce gross) when weak (protect against the 50% DD that kills p_2x_before_50dd).

**Design (parametrize, don't hardcode):**
- **Inputs:** the Track A regime strength `s ∈ [0,1]` (and, if Track A says it adds power, the equity-native state).
- **Gated sizing:** interpolate between a *concentrated* cell `(N_hi, alloc_hi)` (e.g. the frontier's fast-2x cell) at high `s` and a *defensive* cell `(N_lo, alloc_lo)` (e.g. cascade-like, more slots / lower gross) at low `s`. Concretely: effective slot count `N(s) = round(N_lo + s·(N_hi − N_lo))`, effective alloc `a(s) = a_lo + s·(a_hi − a_lo)`, gross-capped. **Top-conviction-first fill is unchanged.**
- **Two gating sources to A/B (mirrors Track A):** market-context `s` vs equity-native `s` — answers whether the *tandem* benefits from the equity curve or only from the market inputs.
- **Withhold variant:** at very low `s`, *skip new entries* (hold cash) rather than just diversify — directly targets p_2x_before_50dd by sitting out the regimes that produce the 50% DD. This is the most aggressive collapse-budget lever.
- **Wiring:** Track C reuses the Track A `aw_scale` arm (once landed) but routes `s` into BOTH the per-trade alloc AND the effective MaxPos. It's a superset of the Track A engine arm; do NOT build a parallel mechanism.

**Build trigger / decision inputs for Track C:**
1. Track B coarse → identify the 2-4 frontier cells `(N_hi, alloc_hi)` (fast 2x, acceptable p_collapse) and the defensive anchor.
2. Track A coarse → does regime `s` carry forward signal (analyzer SHIP/FLAG vs NULL)? If NULL, Track C's *market-gating* is unlikely to beat a static frontier cell — pivot to equity-native gating or shelve.
3. Only then write `experiments/tandem_2x/` (sweep over (N_hi,N_lo,a_hi,a_lo,withhold-threshold)).

---

## 6. Analysis Plan (when coarse sweeps finish)

### Step 1 — Track B frontier
From `experiments/concentration_2x/results/sweep_coarse.json`:
- Plot/table **p_collapse (y) vs median_days_2x (x)** for all 27 cells. The frontier = lowest-left envelope (fast 2x at low collapse).
- Cross-cut with **p_2x_before_50dd** (the safety-adjusted speed metric) and p_2x_ever.
- Expected shape (from smoke + grounding): higher N×alloc gross → faster 2x but higher DD/collapse; cascade_ref = slow-but-safe anchor. Identify cells that **Pareto-dominate** cascade_ref on (speed, collapse).
- Sanity: confirm the metric is genuinely start-date-rolled (n_windows ≈ 38 quarterly, n_paths ≈ 38×100) — not a single-window artifact.

### Step 2 — Track A A/B verdict
- If the `aw_scale` + `MC_EMIT_CURVE` arms are landed and the market/equity arms run: `python experiments/equity_wave/analyze.py --paths-json …/coarse_*.json --H 21 --out …/ab_verdict.json`. Read the SHIP/NULL/HURTS verdict on incremental OOS R² (equity-native beyond market-context).
- If the arms are NOT landed by analysis time: run the analyzer on the **baseline** curves (once MC_EMIT_CURVE lands) to at least answer "does the equity curve predict its own forward jumps/DD" — the core null question — even without the live scaling arm.
- **Decision:** market-context `s` adds power → Track C gates on market. Equity-native adds power beyond market → Track C gates on equity (or both). Both NULL → regime-gating is dead weight; Track C should test withhold-only on a hard DD trigger, or shelve, and the recommendation becomes "pick the best static frontier cell."

### Step 3 — Decide drill cells (N=500, monthly-roll)
- **Replace B1's placeholder drill cells** (`flat_n3_a25,flat_n5_a20,flat_n4_a25,flat_n2_a50,cascade_ref`) with the **actual coarse frontier**: the 3-5 cells with best p_2x_before_50dd at acceptable p_collapse and lowest median_days_2x, **+ cascade_ref** as the control.
- Drill command (B1): `python experiments/concentration_2x/sweep.py --stage drill --cells <frontier> --step-months 1 --workers 8` at N=500 (monthly-roll, ~113 windows). Rationale for N=500 only on the frontier: DD-signal noise floor is N=300; compound swings 1.6-1.8× at N<500 — don't run N=500 across all 27.
- If Track A shipped: add the winning regime arm to the drill at N=500 (baseline + winning arm only).
- Submit via queue, `high` + `--window off_market` (or freely if past close), `--db light`, `--restartable`, `--dedup`, `PYTHONIOENCODING=utf-8`. Bridge with `trader queue wait <id>` under the harness `run_in_background` flag.

### Step 4 — Synthesize the recommendation
Produce the final answer to the objective: **"Starting from a random point with $50k, the fastest collapse-tolerant route to $100k is config X (median Y days, P(2x-before-50%-DD)=Z, P(collapse)=W),"** vs the cascade_ref and vs whether the regime wave / tandem improves it.

---

## 7. Compute Discipline (obeyed; for the next agent)
- ALL heavy MC via `trader queue submit … -- <cmd>` — never raw Bash background (bypasses CPU+MySQL admission, collides with scheduled `trader update`).
- Market-hours: heavy sweeps `--window off_market` (defer past 16:00 ET) so they never outrank `trader update` (tier 20); `--db light` (MC bulk-loads once then CPU-bound), `--cpu N`, `--restartable`, `--dedup <stable-key>`, `--env PYTHONIOENCODING=utf-8` (queue stdout buffering + Windows cp1252 trap).
- Staged: coarse N=100 across the grid first; drill N=500 only on the frontier later. N=300 = DD-signal noise floor.
- NO MySQL-touching MC in Build (synthetic-array unit tests only); real-data runs only via the queue.
- Track A engine arms (`aw_scale`, `MC_EMIT_CURVE`) are additive, default-OFF, byte-identical when env unset — must stay so (portfolio-stage research, no version bump).
