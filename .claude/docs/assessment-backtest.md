## Assessment/Backtest (assess_scores.py)

### Swing barrier methodology

Assess pipeline uses **vol-adjusted barrier-touch** outcomes. Per peak, one O(N) forward walk checks each period (**1d/3d/5d/7d/15d/30d/60d/90d**) for whether the intraday move hits target before stop within W **calendar days** of the peak.

**Period units**: W is calendar days (aligns with option DTE semantics — 30d = "what happens within 30 calendar days" = the 30-DTE hold window). Barrier magnitudes per label preserved vs. prior trading-bar regime (30d still = K·σ·1.0) so pre/post-conversion rows stay comparable. **150d dropped 2026-05-08** (diagnostic-only, no ship gate used it). **WR3/WR5 added 2026-05-08** for multi-window diagnostics; active optimization target is WR15.

σ = `_realized_vol_pct` — 60-day realized stdev of daily returns.

**Barrier parameters (anchored at 30d, scaled by sqrt(W/30)):**
- **HIGH cell (calls)**: K=2.0σ target / M=5.0σ stop — win = intraday high touches entry×(1+K×σ×√(W/30))
- **LOW cell (puts)**: K=1.0σ target / M=2.0σ stop — win = intraday low touches entry×(1−K×σ×√(W/30))
  > **Code-naming caveat:** `assess_scores.py` uses the *inverted* suffix — calls(HIGH) are `SWING_K_LOW=2.0`/`SWING_M_LOW=5.0`; puts(LOW) are `SWING_K_HIGH=1.0`/`SWING_M_HIGH=2.0`. Match by value, not name.
- **1d period**: vol-adjusted (was direction-only pre-2026-05-08) — K_eff≈0.37σ/M_eff≈0.91σ; expect WR1 near 50% by construction.

**Effective barriers per period (HIGH cell):**

| Period | K_eff (target) | M_eff (stop) | Use |
|---|---:|---:|---|
| WR1 | 0.37σ | 0.91σ | first-bar diagnostic |
| WR3 | 0.63σ | 1.58σ | Stage 1 early-shakeout diagnostic |
| WR5 | 0.82σ | 2.04σ | Stage 1 early-shakeout diagnostic |
| WR7 | 0.97σ | 2.41σ | Stage 1 short-horizon diagnostic |
| **WR15** | **1.41σ** | **3.54σ** | **Stage 1 PRIMARY (optimization target)** |
| WR30 | 2.00σ | 5.00σ | Stage 1 multi-window non-regression |
| WR60 | 2.83σ | 7.07σ | Bridge/Historic.js diagnostic |
| WR90 | 3.46σ | 8.66σ | Bridge/Historic.js diagnostic |

**Two walk implementations exist, NOT interchangeable.** `database/barrier_cache.py::_walk_outcome` writes the cache serving every cached `wr`/`tp` number (2016-04-26→present); `assess_scores.py::_swing_walk` is the fresh fallback for barrier tuples matching no `BARRIER_SETS` entry (e.g. `tp26`), for the pre-2016 portion of any deep window, and for experiments calling it directly. As of 2026-08-10 they agree except the same-bar double-touch tie-break. Never compare cache-served vs walk-served WR across that boundary; a window deeper than ~10y silently mixes both.

- **Same-bar double touch (D1 — OPEN).** When a daily bar spans both barriers, OHLC can't say which hit first. Cache tests target first and returns `win` unconditionally (implicit P=1.00); `_swing_walk` breaks tie on close direction. **Measured 2026-08-10 vs Polygon option minute data** (`experiments/double_touch_d1_2026_08/FINDINGS.md`, 3,672 resolved events): true rate is **41–43%, not 100%** — cache runs **~+3.0pp WR15 optimistic** on double-touch-heavy anchors; close-direction rule is inverted not just weak (11-12% order accuracy), so the fresh walk is optimistic too (+1.1pp); gap reconciles to ±0.2pp. Every historical cached WR carries this optimism bound (larger the nearer stop sits to target). **Pre-registered decision: NO RECOMMENDATION** — instrument failed its fidelity gate (OPRA minute coverage 61% liquid tier, 0.09% least-liquid), no convention changed. A future change would be deliberate re-baselining (all 4 barrier sets rebuild, ~40 verdicts re-annotated), not a bugfix. Evidence: `experiments/assess_reanchor_2026_08/CACHE_VS_WALK_FINDINGS.md`.
- **Expire mark-out (D4 — FIXED 2026-08-10).** Expire prices hard sell at last bar **inside** the window; `exit_bars` counts in-window only. `_swing_walk` previously marked out one bar past cutoff; now mirrors cache. Affects returns/`exit_bars`, never win rate; only fresh-walk path moved.
- **Stop vs expire labels (D5 — FIXED 2026-08-10).** Cache reads now take stored `fire_type` (0=expire/1=tp/2=sl) instead of the old "exit_close within 0.5% of stop" heuristic (mislabelled gap-through stops and near-stop expires). Heuristic remains only for ~139k pre-`fire_type` rows of 28.8M, and for `result_unscaled` (no unscaled fire type stored). Win rate unaffected; stop/expire splits move.
- **Window reachability (D2 — known, unfixed).** `_swing_walk` gates each period on forward data reaching past cutoff before walking, discarding outcomes already decided at the trailing edge; cache keeps them — why fresh-walk/cache peak counts differ ~0.1% at the most recent end (not "live-data drift").

Regression coverage D4/D5: `tests/test_barrier_walk_conventions.py`. D4 attribution harness: `experiments/assess_reanchor_2026_08/walk_expire_diff.py`.

**Current scoring target: WR15.** Doctrine moved off WR7-primary since short-horizon winners can still raise major DD risk. WR3/WR5/WR7 remain diagnostics; WR30 is non-regression confirmation. Don't rank new scoring candidates by WR7 unless a short-horizon diagnostic is explicitly requested.

### Option-aligned TP% metric variants (`metric='wr'` / `'tp'` / `'tp26'`)

`assess_scores.py`'s `DTE_METRIC_PRESETS` (keyed `(dte, metric)`) selects K/M barrier set. `metric='wr'` = generic directional barrier (DTE-agnostic). Two OPTION-ALIGNED variants, both 30-DTE only:

| Metric | Anchor era | Calls (k_low/m_low) | Puts (k_high/m_high) | Status |
|---|---|---:|---:|---|
| `tp` | Phase H5_HOLD15_H40 (2026-04-28) | 1.274σ/1.092σ (TP+35%/SL−30%) | 1.274σ/0.728σ (TP+35%/SL−20%) | **Frozen** — feeds W1-W6 |
| `tp26` | 2026-08-10 TP/SL retune | 0.364σ/3.64σ (TP+10%/SL−100%) | 1.274σ/0.728σ (unchanged) | Measurement-only, additive |

**Why two anchors, not one moved anchor.** The 2026-08-10 joint retune (`experiments/tpsl_refine_2026_08/`) moved LIVE Core/Apex canon to calls TP+10%/SL−100%. `tp`'s anchors stayed at Phase-H5 numbers because that family feeds Stage 1 W1-W6 gates (`stage1_growth_gate.py`, `signal_supply.py`) and ~40 closed-axis verdicts in `known-issues.md`/auto-memory cite those exact numbers. `tp26` (`experiments/assess_reanchor_2026_08/`) is a SECOND, ADDITIVE metric at the CURRENT live canon, computed for the active version only (no historical backfill), 30 DTE only (both live profiles are 30 DTE since the 2026-08-02 P0.3 switch; `STRATEGY_15DTE` untouched by the retune).

**Puts are identical between `tp`/`tp26` by construction** — `OPT_30DTE.PUT_TP`/`PUT_SL` untouched by the 2026-08-10 ship (puts off portfolio-wide), so both share the 1.274σ/0.728σ pair. **But don't use put-bucket parity as a wiring check** — same peaks + barriers do NOT mean bit-identical rows (falsified 2026-08-10): `tp`'s full K/M tuple matches `BARRIER_SETS['30dte_opt']` (cache-served); `tp26`'s doesn't (forward-walks). The two paths resolve same-bar double touches differently (cache unconditional win vs walk close-direction tie-break), so cached path reads **~1.9–2.3pp higher WR** on put anchors, plus a small deterministic N gap at each window's trailing edge — EXPECTED, not a tp26 bug. Details: `experiments/assess_reanchor_2026_08/CACHE_VS_WALK_FINDINGS.md`.

**Which gates read which variant.** W1-W6, the Hydration-Adjusted Growth verdict, `stage1_growth_gate.py`, `signal_supply.py`, and `research_pack.py`'s comparability-unit loop (`for metric in ("wr","tp")`) all read LEGACY `tp`/`wr`, unchanged by `tp26`. `tp26` participates in no ship gate — exists so the Assessment dashboard TP% tab can show option-TP% under the live barriers without disturbing gate history. Promoting `tp26` into the gate path is a distinct, explicitly-ratified decision this addition doesn't make. Design record: `experiments/assess_reanchor_2026_08/PREREG.md`.

### Scaled vs Unscaled win rates

Two parallel checks in `_swing_walk`:

| View | Target formula | Behavior |
|------|---------------|------------------------|
| **Scaled** (default) | `K·σ·√(W/30)` | Target grows with holding period — harder to win at 60d/90d vs 7d/15d |
| **Unscaled** | `K·σ` (fixed) | Same target all periods — win rate monotonically non-decreasing (hit day 7 = win for all W≥7) |

Unscaled answers "given this fixed vol-normalized threshold, how fast does the stock reach it." Monotonicity violations indicate stale runs (NULL unscaled fields pre-implementation) or tiny N, not code bugs.

**DB columns**: `win_rate_unscaled_{1d,3d,5d,7d,15d,30d,60d,90d}` on both `ScoreAssessmentResult` and `ScoreAssessmentMeta`.

**Side assignment**: `_peak_side(score)` returns `'low'` (call: win=rise) for score≥50, `'high'` (put: win=drop) for score<50.

**Global correlation output** (two lines after IC table):
```
Correlation (score vs call win,     HIGH peaks): 1d=... | 7d=... | 30d=... | ...
Correlation (put strength vs put win, LOW peaks): 1d=... | 7d=... | 30d=... | ...
```
HIGH: Pearson(score,win) for score≥50, positive=higher score→more call wins. LOW: Pearson(50−score,win) for score<50, positive=lower score→more put wins. Pooling both sides is invalid — opposite-direction strategies.

### Band-level metrics

| Metric | Meaning |
|--------|---------|
| `win_rate_{p}` | Barrier-touch p_win: % where K×σ target hit before M×σ stop within W bars |
| `avg_return_{p}` | Side-adjusted EV: positive = trade direction worked |
| `avg_mae_winner_30d` | Max adverse excursion on eventual winners — sets stop-loss floor |
| `avg_mfe_30d` | Best gain reached in raw % — gauges exit quality vs. potential |
| `mfe_p25_{p}`/`mfe_p75_{p}`/`mfe_p90_{p}` | Raw % MFE percentiles |
| `avg_mfe_sigma_{p}` | Mean MFE in σ units — vol-normalized, comparable to K barrier |
| `median_mfe_sigma_{p}` | p50 MFE in σ — base TP anchor |
| `mfe_sigma_p25_{p}` | Conservative TP anchor in σ — 75% of trades exceed |
| `mfe_sigma_p75_{p}` | Aggressive TP anchor in σ — 25% of trades exceed |
| `capture_ratio_30d` | avg_return/avg_mfe; >0.7 = excellent |
| `ic_{p}` | Pearson IC within score band; >0 = dispersion meaningful |
| `shakeout_depth` | win_rate_7d − win_rate_60d; negative = entries struggle initially |
| `shakeout_recovery` | First of [7,15,30] where win rate recovers to 60d level |

**MFE sigma normalization**: `mfe_sigma = mfe_pct / vol_pct` (60-day realized daily σ at signal date) — comparable across stocks, consistent with K·σ barrier definition. Convert back: `entry × (1 + mfe_sigma_pN_{W} × stock_σ)`.

**Observed values (v17, 5y, ~11.5k peaks for 80+ calls):** 80+ calls at 30d: WR15=73.6%, WR30=75.0% cumulative (discrete: 85-89=77.3%, 80-84=72.5%). <25 puts at 30d: WR15=63.4% cumulative. **Post-v18**: put Ret30 no longer structurally negative — `<25`≈−0.1% (flat), `<15`≈+0.25% (positive); asymmetric weekly(1.5×)+MACD gate(45) lifted put quality; canonical MC portfolio put TP 45-50% clears 43.5% BE by 1-6pp. *v14 reference: 80+ calls WR30≈75.6%, <25 puts WR30≈63.8%.*

### DB schema

Periods `{1d,3d,5d,7d,15d,30d,60d,90d}`, per period: `win_rate_{p}`, `win_rate_unscaled_{p}`, `avg_return_{p}`, `avg_mae_{p}`, `avg_mfe_{p}`, `mfe_p25_{p}`, `mfe_p75_{p}`, `mfe_p90_{p}`, `avg_mfe_sigma_{p}`, `median_mfe_sigma_{p}`, `mfe_sigma_p25_{p}`, `mfe_sigma_p75_{p}`, `median_*`, `swing_p_stop_{p}`, `swing_p_expire_{p}`, `swing_avg_win_pnl_{p}`, `swing_avg_stop_pnl_{p}`, `capture_ratio_{p}`. Migration in `ScoreAssessmentResult.ensure_schema()`. 150d dropped, WR3/WR5 added 2026-05-08 same migration.

### Score bucketing

Twelve cumulative buckets (symmetric 6/6, updated 2026-04-17):
- Calls: `BUY_THRESHOLDS = [95,90,85,80,75,70]` → `95+/90+/85+/80+/75+/70+`
- Puts: `SELL_THRESHOLDS = [30,25,20,15,10,5]` → `<30/<25/<20/<15/<10/<5`

Non-overlapping `IC_BANDS` (intra-band Pearson IC) every 5 points: `95-100/90-94/85-89/80-84/75-79/70-74/26-30/21-25/16-20/11-15/6-10/0-5`. Put buckets were `<25/<15/<5` (3), expanded to 6 for call-parity granularity post-v18. New rows populate on next `trader assess --force`; pre-v18 rows keep old 3-bucket shape.

### Assessment windows & lookback

`ScoreAssessmentRun.lookback_days`. Windows: `1y`(365), `2y`(730), `3y`(1095), `5y`(1825), `10y`(3650). 25y unsupported. `WINDOWS` in `assess_scores.py` and the recalc auto-tail both include 10y.

**Default behavior**: `trader assess` with no lookback → `run_all_windows`, a **single-pass consolidated assessment**: extracts peaks ONCE at 10y/3650d, computes forward barrier walks ONCE, then **slices `results` by date cutoff** for 1y/2y/3y/5y/10y rows — avoids 5× redundant peak extraction. Each window still produces its own `ScoreAssessmentRun` + bucket rows.

After the consolidated walk, `update_historic_peaks()` runs once (DTE-agnostic, first DTE strategy only in multi-DTE invocation). API aggregates across all runs per window per `dte_strategy`.

`--dte both`: single-pass runs separately per DTE — 30 DTE first (cache-served, ~3 min), then 15 DTE (cache-bypassed, K/M differ, ~30-60 min full forward walk). Default no-flag path (since 2026-05-15): 30 DTE only; 15 DTE is explicit research scope.

**DTE selection** (Phase 16): no flag → 30 DTE assessments + temporal stats; `--dte 30` → 30 DTE only; `--dte 15` → 15 DTE research path; `--dte both` → explicit refresh of both.

**`--days` is NOT a valid flag.** Use positional syntax:
```bash
trader assess --force                # 1y..10y, 30 DTE only
trader assess --force --dte 30       # 30 DTE only, all 5 windows
trader assess --force --dte 15       # 15 DTE only, all 5 windows
trader assess --force --dte both     # explicit refresh both DTEs
trader assess --force --profiles all # temporal calendar rows, Sentinel/Core/Apex
trader assess --force 3y             # single window (3y), 30 DTE
trader assess --force 5y --dte 15    # single window (5y), 15 DTE only
trader assess --force 1825           # raw days works too — single window, 30 DTE
```
Passing an explicit lookback runs just that window. `--days 1825` gets parsed as a symbol name (`symbol='--DAYS'`), finds 0 peaks.

### Portfolio Profile Temporal Stats

`BacktestTemporalStats` is a portfolio-stage cache, not score-stage. Rows unique by `(version, dte_strategy, portfolio_profile)` — `version` is the scoring `AlgorithmVersion`, `portfolio_profile` a key from `algorithm_versions/portfolio_profiles.json`, applied after score rows are read, so Sentinel/Core/Apex never imply different `Score.overall` or consume a scoring version number.

`trader temporal-refresh --profiles all` after any portfolio-stage change refreshes all profile calendar rows; `--profile core` or `--profiles sentinel,apex` for targeted repair. Assessment Calendar UI calls `/api/backtest/temporal?version=vNN&dte=30&profile=core`.

### Version resolution in assess

`assess_scores.run()` resolves version via `get_or_create_current()` (HEAD commit), NOT `get_active_scores_version()`. If HEAD matches active version, fine; if uncommitted changes exist or a new commit has no scores yet, assess may target the wrong version. `--version v12` overrides explicitly.

### In-Memory Assessment API (no DB writes)

| Function | Purpose |
|----------|---------|
| `calculate_forward_returns_from_cache(peaks, ph_by_sym)` | Forward-return calc from pre-loaded price data |
| `assess_peaks_in_memory(peaks, ph_by_sym, lookback_days)` | Full pipeline → structured dict; zero DB writes |
| `run_assessment_on_peaks(peaks, ph_by_sym, lookback_days)` | Prints standard tables; used by simulator.py |
| `print_diff_assessment(old_data, new_data, label_old, label_new)` | Side-by-side diff: WR30/Ret30/MAE30/MFE30/Cap30 per bucket. **Only prints the 30d column** — `bucketed_stats` has all periods. For WR15 (primary target), extend the `metrics` list or read `old_data['bucketed_stats'][bucket]['win_rate_15d']` directly. Don't judge ablations on WR30 alone. |

### Signal Audit — `explain_score_accuracy`

```
trader explain-scores [SYMBOL …] [days]
```
Per signal (2 lines): (1) date · overall score+type(H/L) · components `[BB TR RSI MACD ST TA]` · volume signal/magnitude · weekly composite · 7d/15d/30d forward returns+peak/trough · verdict. (2) raw indicator values (`RSI= Stoch= MACDh= BB=position EMA50=%`) + confirmation ratio.

Filters by active algorithm version (same as API/dashboard). Duplicate rows for a date = active version has multiple score rows in DB → scoring pipeline issue.

**Verdict logic:** `CORRECT` — intraday peak/trough moved >1% favorably within 30d. `BAD_LUCK` — wrong outcome but indicators confirmed. `MISS` — wrong outcome AND indicators neutral/contradicting. `PENDING` — insufficient forward data.

When asked to "assess scores"/"review signal accuracy"/"look at misses"/"suggest improvements": run `trader explain-scores` first, don't ask the user for data.

**Key fields for revision reasoning:**

| Field | What it tells you |
|-------|------------------|
| `[BB TR RSI MACD ST TA]` | Which sub-signal drove overall; high TREND + neutral RSI/BB = momentum not mean-reversion |
| `Vol/Mag` e.g. `CONVICTION/0.72` | Low-magnitude MISSes → volume multiplier over-amplifying |
| `Wk:nn` weekly composite | Weekly contradicting daily → weekly adjustment insufficiently dampening |
| `Reg: mult/LABEL` | Regime multiplier at signal time; MISSes clustering BULL/STRESS |
| `Brd:nn` breadth | HIGH call into breadth 70+ = fighting a healthy market |
| `conf=0.xx` | conf>0.45 MISS = bad luck; conf<0.25 = firing without indicator support |

### Intraday Score Audit Log (`score_intraday_logs`/`ScoreIntradayLog`)

`Score` is keyed `(symbol,date,version)` and overwritten in place each `trader update` — DB never retains how a score evolved intraday. `ScoreIntradayLog` (`database/models/core.py`, table `score_intraday_logs`) is the **append-only** companion: one row per symbol per update run, capturing full scoring state — the substrate for diagnosing intraday swings/"fakeouts" (a 92 that becomes a 60 by afternoon with little price movement).

Written by `score_calculation_service._record_intraday_score` → `ScoreIntradayLog.record_latest` at end of `calculate_for_update_stock` (`audit_intraday_scores=True` default, failures swallowed, never blocks an update). `core.reapply_regime_today()` also logs each patched row with `source='regime_reapply_today'`. Captured: `logged_at`(μs), `run_key`, `source`, `score_updated_at`, `overall`, `pre_regime`, `pre_boost`, six component scores, `regime_multiplier`, `volume_signal`/`volume_magnitude`, `price`, full `weight_info_json`/`factor_json`/`inputs_json`. Indexes: `(date,version,logged_at)`, `(symbol,date,version,logged_at)`, `(run_key,logged_at)`.

**Tooling:** `trader intraday-swings [days] [--fakeouts]` ranks biggest swings; `trader intraday-drill SYM [date]` prints per-snapshot timeline + stage attribution, names dominant cause. Dashboard: `SwingBadge` (↕N, amber=fakeout) on `StockTable` via `intraday_swing` field on `/api/stocks/all`; drill JSON at `/api/stocks/<sym>/intraday-swing`. Logic: [intraday_diagnostics.py](../../intraday_diagnostics.py) (shared CLI+API).

**Query pattern:** `WHERE symbol=? AND date=? AND version=?` ordered by `logged_at`; spread `max(overall)−min(overall)` within a group = intraday swing. Fakeout = large score spread, small price range. Diff highest-vs-lowest `overall` snapshot, attribute down the pipeline: `overall = regime_mult(boosts(dampeners(weighted_sum(components) + weekly_adjustment)))`.

| Stage | weight_info keys | "fakeout" signature |
|---|---|---|
| Daily components | `trend/bb/rsi/macd/stoch/ta` + weights | components flip between runs while price barely moves (data-refresh oscillation) |
| Weekly adjustment | `w_comp`, `w_adj`, `wadj_partial`, `wadj_completed`, `weekly_adj_gap`, `weekly_gap_flag` | `w_adj` jumps when resolver switches partial↔completed week (COHR/VICR whiplash) |
| Dampeners | `scw_dampen`, `mcd_dampen`, `ich_*`, `cwwd_dampen`, `cwcf_dampen`, `pcd_*`, `wvd_lift`, `sector_breadth_wave` | a dampener toggles on/off across runs, moving `pre_boost` while weekly+components stay flat |
| Volume amp | `volume_signal`, `volume_magnitude`, `wvd_lift`/`daily_volume_authority_wave` | CONVICTION lift vanishes for one run (transient stale volume read), recovers |
| Boosts | `ern_boost`, `cont_lift`, `days_to_ern` | earnings-boost gate is a hard cliff at 70/25 on `pre_boost`; small shift drops boost entirely |
| Regime | `regime_multiplier`, `pre_regime` | `pre_regime→overall` compression changes when `reapply_regime_today` runs |

**Three fakeout families observed 2026-05-28/29 (active v60):** (1) weekly transition — PWR 53→81, ELF 49→20, `w_adj` flipping partial↔completed (33/14pt gap), price flat; (2) volume-amp dropout — AMSC 95→72→89, ~+18 conviction lift vanished one run; (3) component oscillation — BBW bouncing 29↔48↔69, raw RSI/Stoch flipping between refreshes.

**Caveats:** (a) data exists only from the 2026-05-27 ship (`c0bfd1d67`), append-only growth, retention TBD. (b) stored `weight_info` reflects whatever the **production scheduler** ran at capture time, which can be ahead of the active `ALGORITHM_VERSION` silo — e.g. live v60-tagged rows carry `wadj_partial`/`wadj_completed` keys that the v60 silo and `main` `scoring.py` don't emit (those live only in v65/v66 silos). Trust captured `weight_info`, not the silo source. (c) not every run logs every symbol; snapshot counts vary.

### Note on tertiary-portfolio filters vs assessment

`assess_scores.py` computes barrier-touch WR per bucket on the **full population** — no tertiary-portfolio filters (F3f, CT cascade promotion, EARN_SUPP_PUT, SAW U-curve). Keeps assessment comparable across versions, independent of Stage 2/3 params. Use `monte_carlo.py`/`backtest_cascade.py` for post-filter outcomes. A Stage 3 ship (e.g. EARN_SUPP_PUT) doesn't change per-bucket WR in `assess_scores.py` — it operates after scores are read. This design property makes Stage 1 (per-trade WR15) barrier-independent and Stage 3 (DD-primary MC) per-trade-quality-invariant.

---

## Score Simulator (simulator.py)

Bulk-loads all indicator/price/weekly-score data once, runs the scoring pipeline in memory. No DB writes.

| Method | Purpose |
|--------|---------|
| `ScoreSimulator(symbols, lookback_days, scoring_fn)` | Init; pass `scoring_fn` to override `compute_overall_score` |
| `.simulate(since)` | Returns `{(symbol,date): overall_score}` for all loaded stocks |
| `.compare(sim_scores)` | Score-level diff vs DB: mean/median/max delta |
| `.diff_assess(sim_scores, symbol)` | Side-by-side assessment diff vs DB — fastest formula feedback loop |

**Metric display gotcha:** `diff_assess`→`print_diff_assessment` only shows WR30 by default. Stage 1 primary is WR15 with WR3/5/7/15/30 diagnostics — either extend the `metrics` list in `print_diff_assessment`, or read `bucketed_stats[bucket]['win_rate_{3d,5d,7d,15d,30d}']` directly. A change that improves WR7 but regresses WR15 is the old short-horizon overfit class — don't promote it; a change improving WR15 but damaging WR3/5/7 needs an explicit shakeout/DD review.

```
trader simulate [SYMBOL …] [days] [--compare] [--assess] [--diff-assess]
```

### Testing scoring hypotheses without polluting production

**Core rule: do not modify the live serving checkout to test a hypothesis.** Scoring experiments default to isolated algorithm-refinement worktrees where the real scoring path is staging code, editable directly. No env-gates, no `PHASE_X_ENABLED` flags, no "default OFF" research knobs in shipped scoring files. Failed hypotheses leave zero trace in shipped production.

**Acceptable patterns:**
1. **Isolated algorithm-refinement worktree** (default). Edit real scoring path, simulate/sweep in sandbox, snapshot with `trader algorithm snapshot-staging` before elevation. Don't run shared production score writers from the sandbox except the designated ship checkout; recalculation happens after the version bump in deploy, not during refinement.
2. **Staging-native simulation/sweeps** (preferred for ship candidates). `trader simulate`, `ScoreSimulator(scoring_fn=None)`, or an `experiments/<candidate>/` runner importing checkout scoring code. The formula under validation should live in staging scoring before W1-W6.
3. **`ScoringFn` override (prototype only).** `ScoreSimulator(symbols=..., scoring_fn=my_variant)`; full per-signal context in `simulator.py:446` kwargs. For quick probes; migrate into staging if it wins.
4. **Runtime monkey-patch (prototype only, when ScoringFn lacks the inputs)** — patch an internal helper (e.g. `calculate_weekly_adjustment`) from the experiment script:
   ```python
   import database.utils.scoring as _sc
   _ORIGINAL = _sc.calculate_weekly_adjustment
   _PARAMS = {'alpha': 0.0, 'current_kijun_pct': None}
   def _patched(*args, **kwargs):
       total, detail = _ORIGINAL(*args, **kwargs)
       # ... apply variant logic using _PARAMS ...
       return total, detail
   def _install():
       _sc.calculate_weekly_adjustment = _patched
       import simulator as _sm; _sm.calculate_weekly_adjustment = _patched  # if imported by name elsewhere
   def _uninstall():
       _sc.calculate_weekly_adjustment = _ORIGINAL
       import simulator as _sm; _sm.calculate_weekly_adjustment = _ORIGINAL
   try:
       _install(); run_sweep()
   finally:
       _uninstall()  # GUARANTEE production restored even on crash
   ```
   For per-signal state not in the patched signature, thread via a closure slot wrapping the OUTER call (e.g. `compute_overall_score`), patching both outer+inner. Reference: `experiments/weekly_avwap/phase_i_wadj_blend_sweep.py`. Promote any winner into staging scoring before final validation.

**Why this matters:** hypotheses fail more often than succeed (Phase I 2026-05-08, v42 rolling weekly 2026-05-07, v40 SVD 2026-05-07, v22-v24 reverts). A failed env-gate left in shipped code creates strategy ambiguity, breaks bit-identical reproducibility, leaks into auto-memory/CLAUDE.md, and slows future audit passes over `database/utils/scoring.py`.

---

## Calibration Sweep Methodology — staged Bayesian, NOT brute-force grid

**Default to staged Bayesian (LHS blast radius → drill top quartile → final fine-grid).** Uniform grid sweeps waste compute and miss optima; this pattern has repeated wins across ships.

**The wrong default — uniform grid.** `5×5×6×4×4=2,400` variants evenly distributed: (1) ~95% of compute lands in already-known-bad regions; (2) doesn't scale (6th param at 5 levels=12,000, 7th=60,000); (3) uniform resolution can't hit local optima (α=0.93 optimum missed by a coarse {0.4,0.6,0.8,0.95} or {0.1,0.5,0.9} grid); (4) knobs too expensive to add stop being questioned.

**The right default — three-stage Bayesian:**

| Stage | Approach | Variants | Coverage | Compute |
|---|---|---|---|---|
| **1. Blast radius** | LHS/Sobol over full high-dim space | ~50–150 | wide, coarse | ~5–20 min |
| **2. Drill** | Bayesian (Optuna/skopt) on Stage 1's top-quartile basin | ~100–400 | narrow, dense | ~10–30 min |
| **3. Final tune** | Dense grid/LHS within ±10% of Stage 2 winner | ~30–80 | very narrow, very dense | ~5–10 min |

~200–600 total variants typically beats a 2,000-variant uniform grid on both coverage and optimum-finding.

**Canonical examples:**

| Sweep | Shipped | Pattern |
|---|---|---|
| `experiments/v32_optim/phase_b_cascade.py` | v32_optim cascade retune (5y compound 68×) | 16 Bayesian × N=100×8 windows Stage 2; N=300/500 Stage 3 confirmation |
| `experiments/mcap_dampener/sweep_v3_bayes.py` | v43 MCD score-stage dampener | 3-stage: v1 linear(3,429)→v2 power-law(3,456)→v3 dense Bayesian(8,310) |
| `experiments/weekly_avwap/` (Phase H) | v44 ICH score-stage dampener | 1,170+ variants, 7 sequenced sweeps (A profile→B-D refine→E-H drill) |
| `experiments/v34_calibration/` (Phase 1-3) | v35 EARN_BOOST recalibration | gradient(15)→combined-stack(15)→2D fine grid(29) |

Read these scripts before writing a new sweep — LHS sampler, Optuna integration, JSONL logging, ranking are already solved.

**When uniform grid IS appropriate:** Stage 3 fine-tuning around a known optimum (~50 dense-grid variants); 2-3 parameter exhaustive sweeps (e.g. metric×period×bucket); sanity baselines (4-5 hand-picked variants alongside the Bayesian winner).

**Before launching a sweep — preflight:**
1. List ALL knobs, including tempting-to-fix ones — no empirical answer = put it in the search space.
2. Separate empirically-locked (cascade tier structure, barrier set) from empirically-open (lift shape, gate boundaries, normalization) — sweep the open ones.
3. Define the objective explicitly before launching — composite metrics beat single-metric optima, e.g. `utility = α×alpha_capture − β×frac_dropped − γ×|new_overall_distribution_skew|`.
4. Pre-commit to the stage gate thresholds before seeing results (see Three-Stage Framework below).
5. Run on holdout-locked data — `experiments/_holdout.py` enforces this; never calibrate past `CALIBRATION_CUTOFF_DATE`.

**Anti-patterns:** "grid 5 levels then drill if needed" (wastes compute Stage 1 LHS would rule out); dropping a knob to keep variant count low (that's now a hidden assumption); expanding the grid 2× and re-running (switch to Bayesian instead); picking the winner by visual inspection of a top-15 list (encode the objective function, let it rank).

---

## Three-Stage Calibration Framework

> **Stage-3 lens note (2026-08-12):** the MC-realism default flip made calibrated fills (`TP_FILL_MISS_P=0.15`+`GAP_AWARE`) the engine default — T1-T7 evidence is read under the calibrated lens from here on, canon as a labeled robustness arm. Stage-1/Stage-2 unaffected (separate measurement layers; D1 double-touch stays its own tracked item). Pre-2026-08-12 T-gate numbers are canon-era.

**Architectural principle (shipped 2026-05-08):** scoring, barriers, tertiary portfolio mechanics factor into three independent calibration stages, each optimizing its own objective on a frozen prior-stage output — eliminates objective-function contamination (DD/compound leaking into scoring sweeps), makes alpha-vs-tax bookkeeping explicit.

| Stage | Tuned | Primary metric | Frozen | Sweep style |
|---|---|---|---|---|
| **1. Scoring** | `compute_overall_score`, any score-stage dampener (CWWD, PESS, MCD, ICH, etc.) | **WR15 on affected cohort**, multi-window diagnostics, per-cohort z, per-discrete-bucket non-regression | barriers, tertiary portfolio | Bayesian on cohort z + WR15 |
| **2. Barriers** | TP_SIGMA, SL_SIGMA, HOLD_DAYS, PREMIUM_MULT, BREADTH_THRESHOLD | **option TP%** + avg_option_pnl; bounds WR15→option-capture gap tax | scoring stack | Coarse Bayesian (5-20), barrier-cache rebuild per candidate |
| **3. Tertiary Portfolio** | F3F, MaxPos, cascade allocation, DD soft-band, sector-concentration waves, dead-hold, slippage, regime slopes, EARN_SUPP_PUT, CT_PROMOTE, SAW Put U-curve | **5y WorstDD primary**, compound non-regression sanity | scoring + barriers | Full Bayesian + N=500×8 MC |

**Why this factorization:** old framing lumped TP_SIGMA/SL_SIGMA into "portfolio" alongside F3F/DD-breaker, obscuring barrier ownership (they uniquely key the `barrier_outcomes` cache). WR15 (not WR7) is now the score-stage target — penalizes candidates that look good short-horizon but create shakeout-prone DD risk. The WR15→option-capture gap is real but bounded; Stage 2 tunes TP/SL to bound the residual tax. Compound at MC scale (>1e10%) is unrealizable — DD is the only Stage 3 metric that translates to real trader experience.

---

### Stage 1: Scoring Calibration Gate (WR15-primary, barrier-independent)

**Applies to:** any change modifying `Score.overall` for any signal on any historical date.

**Why barrier-independent:** Stage 1 answers "did the score predict directional truth" — that must not depend on TP/SL choice, or scoring sweeps converge to barrier-overfit local optima that re-tune away once Stage 2 runs.

#### Primary objective
**Cohort z-score on the targeted feature (≥+3 required before any sweep starts)** — a dampener targeting e.g. "wadj<0 ∧ stoch≥35 ∧ overall∈[70,74]" must show z>3 vs the non-targeted cohort's WR15 first, or the search fits noise. Pre-flight tool: `experiments/miss_ledger/`.

Then optimize **WR15 on the affected cohort** (5y; 30 DTE assessment, generic K=2σ/M=5σ barriers, DTE-agnostic). Affected cohort = subset of signals the change actually modifies, NOT the global tier (which dilutes a focused dampener with untouched signals); tier-wide changes use the most-affected cumulative tier.

#### Hard constraints (Stage 1 fails on any breach)

| # | Constraint | Threshold |
|---|---|---|
| **W1** | Cohort z-score | ≥+3 on targeted feature, same direction as proposed change |
| **W2** | Multi-barrier-window directional consistency (5y, noise-aware 2026-06-11) | WR3/5/7/15/30 don't *confidently* contradict WR15 conclusion — contradiction counts only at two-proportion z≥2 AND cohort N≥100 (small-N sign flips are noise) |
| **W3** | Multi-time-window directional consistency on WR15 (noise-aware 2026-06-11) | 1y/3y/5y directions agree, same noise rule as W2 — a disagreeing window blocks only at z≥2 AND window-cohort N≥100 |
| **W4** | Per-discrete-bucket non-regression (noise-aware) | No bucket (95+…<5) regresses WR15 or option-TP by a real margin: `z≤-2`=flag, `z≤-3 ∧ ≥1.5pp ∧ N≥100`=block. Fixed-pp thresholds over-fire on thin tiers. **Separate hard guard, never folded into W5** (a scalar masks within-tier asymmetry — the ICH put-`<10` trap). |
| **W5** | Hydration-Adjusted Growth verdict | `g=ebar·lambda_eff` on both barriers + binding window → SHIP/FLAG/BLOCK (below). Nets WR15-up vs N-down; saturating `lambda_eff` blocks N-up/WR-down volume dumps. Subsumes the old recycle/shortfall/p20 cluster. |
| **W6** | Gradient preservation (noise-aware, FLAG-only, 2026-06-11 reform) | `stage1_growth_gate.py`'s `w6_gradient`: N<100 bands pool upward (95-100→90-94, since N≈15-130/5y makes raw gating statistical theater); adjacent-pair inversion counts only at pairwise z≤−2 on shrunk values; only **candidate-introduced** real inversions escalate (→FLAG, never BLOCK) — baseline-inherited inversions report without escalating (the CTSL 2026-05-08 false-fail class). Slow thin-tier rot is owned by `experiments/version_scorecard/tier_drift.py`, not per-ship gates. |

**OUT of Stage 1 (deliberately):** option TP%/avg_option_pnl (Stage 2); 5y compound/WorstDD/collapse/F3F-eligible density (Stage 3); cumulative-tier WR15 alone (replaced by per-discrete-bucket + multi-window — old "H1: ≥+0.5pp on ≥3 of 5 tiers" retired, it masked the ICH put-side tail-tier alpha loss).

#### N capacity floor table — **DEMOTED TO REPORT-ONLY 2026-06-11**

No longer a gate — it was calibrated vs v46-era supply and fired spurious REVIEW on every honest-era ship (v60, v71, v72 — three stale-calibration waivers, the ledger's three-strikes trigger). W5's binding-window `lambda_eff` subsumes its purpose with live data. Now context for the W5 verdict, never a veto. Recalibration queued 2026-06-11 (`experiments/n_floor_v46/run.py`+`reaggregate.py`); freshest `experiments/n_floor_v46/summary_v2.json` supersedes the table below (kept as historical v46 reference only):

| Tier | Class | Baseline (offered/yr) | Floor (offered/yr) | Buffer |
|---|---|---:|---:|---:|
| 95+ | binding | 31 | 26 | 15% |
| 85-94 | binding | 98 | 83 | 15% |
| 80-84 | binding | 194 | 164 | 15% |
| 75-79 | binding | 514 | 436 | 15% |
| p≤15 | binding | 231 | 196 | 15% |
| p16-20 | mild surplus | 570 | 398 | 30% |
| p21-25 | heavy surplus | 1,353 | 541 | 60% |
| 70-74 | overflow disabled, alloc=0 | n/a | n/a | n/a |

Refresh (report-only) after any portfolio ship or any scoring ship shifting ≥30% offered/year on a binding tier — re-run against the active version; don't hand-maintain this table, read `summary_v2.json`.

#### W5 — Hydration-Adjusted Growth verdict (auto-decision)

Replaces the hand-read recycle/shortfall/p20 cluster + N-awareness prose with one deterministic, MC-free number netting per-trade quality against cash-rotation velocity: `g = ebar · lambda_eff`.

- **`ebar`** = per-trade log edge of the *filled* book: `e(p) = p·ln(1+f·w) + (1-p)·ln(1-f·l)` averaged over slots a conviction-priority cascade actually fills (`demand`=MAX_POS/avg_hold_bars≈6.2/day, best tier first). `p`=shrunk WR15; `(f,w,l)` held CONSTANT at nominal 30 DTE values (reproduces call BE 45.0%, put BE 36.4% exactly) — sweep can't tune a barrier to win; over-supply changes which tiers fill but can't inflate `ebar`.
- **`lambda_eff`** = `demand · recycle_coverage` (velocity). SATURATES at the full book (extra supply earns zero credit — the v58-volume-bug guard); drought-aware (`recycle_coverage` = mean per-day `min(supply,demand)`, bursty supply scores below steady supply).

Computed on **both barriers** (`g_option`: p=tp_shrunk, PRIMARY; `g_generic`: p=wr_shrunk, directional sanity, closes SVD generic-vs-option divergence) and **each window** (2022/2023/2024/2025/dip/5y). Gates on the **binding (worst-g) window** — 5y mean is never the constraint (supply ~12-14/day vs ~6.2 demand), bear-tape droughts are. `ebar` held at 5y (quality is era-stable, recency ratio ~0.97-1.00); only `lambda_eff` varies by window.

**Verdict** (bootstrap CI on `dG`, real sampling error of thin top tiers, not an arbitrary constant):

| Verdict | Condition (binding window) |
|---|---|
| **BLOCK** | W4-severe (z≤-3) **OR** a barrier's best-case `dG`(p95) still <-eps (confident regression) |
| **SHIP** | option-barrier point `dG≥-eps` AND p05≥-2·eps AND no real W4 dip (confidently fine) |
| **FLAG** | otherwise — scoring-neutral tie/wide CI/small real W4 dip → route to Stage 2/3, do NOT auto-green |

`eps=1%` (knob). Scoring-neutral ties FLAG not SHIP (value is downstream); FLAG≠BLOCK, nothing stopped out. BLOCK keys on *either* barrier (dual veto); SHIP keys on the PRIMARY/option barrier.

**What the verdict encodes** (old N-awareness rules, automatic now): 80+/85+/90+ N expansion at flat-or-up WR → `ebar` up via better-tier fills → toward SHIP. 70-74 N compression → no effect (overflow disabled) → neutral. 75+/80+ or p≤15 starvation, especially bear tape → binding `lambda_eff` down → toward FLAG/BLOCK. N-up/WR-down volume dump (v42/v58 trap) → saturation gives no credit, `ebar` falls → BLOCK.

**Tooling:** after `recalculate`+`assess --force`+research-pack build: `python experiments/version_scorecard/signal_supply.py --versions <base>,<cand>` → `python experiments/version_scorecard/stage1_growth_gate.py --baseline <b> --candidate <c>` (seconds, no MC) → `python experiments/version_scorecard/portfolio_response.py --materialize <cand>` (PRF matched sizing — see deploy.md "Post-recalc comparability unit"). Methodology: [`experiments/version_scorecard/STAGE1_GROWTH_GATE.md`](../../experiments/version_scorecard/STAGE1_GROWTH_GATE.md). **Regression-test with `--selftest`, not `--replay`** — historical replay anchors (v40→v42 BLOCK etc.) were invalidated when 2026-06 honest-era recalcs overwrote pre-v69 packs; `--selftest` synthesizes SHIP/FLAG/BLOCK/W4 cases from any live pack so it can't rot.

**FLAG has teeth on the back end, not the front (2026-06-11).** FLAG stays easy to ship (most good historical ships — MCD, ICH — were FLAG-grade ties), but requires: (a) one-line documented justification, (b) a named post-ship watch metric (number, surface, "revert" value), (c) Stage 2/3 confirmation when the claimed value is downstream (v58 lesson: Stage-1-clean ≠ portfolio-safe). Exception: neutrality-track ships (below) substitute N1-N3 for (c) — a proven zero-tradable-change can't differ downstream.

#### What a passing/failing Stage 1 result looks like

Pass: cohort z≥+3 in predicted direction (W1); WR15 positive/flat on affected cohort at 5y, no confident (z≥2,N≥100) contradiction from WR3/5/7/30 (W2) or 1y/3y (W3); no bucket regresses by a real margin (W4); W5 SHIP/FLAG on binding window both barriers (N movement netted automatically; FLAG carries justification+watch metric+downstream confirmation); no candidate-introduced real gradient inversion (W6).

Fail: cohort z<+3 → abandon, don't calibrate noise (W1). WR3 confidently positive but WR15 confidently negative — barrier-fragile (W2). 1y confidently disagrees with 5y — time-window overfit (W3; thin-window sign flip is NOT this). A bucket loses ≥1.5pp at z≤-3 (cumulative `<25` +1pp hiding discrete `<10` -2pp — ICH within-tier-asymmetry trap) → W4 BLOCK. W5 BLOCK: a barrier's binding-window p95<-eps — confident velocity/quality regression, or a saturation-masked N-up/WR-down dump.

#### Stage 1 Bayesian sweep cadence

| Phase | Approach | Variants | Compute |
|---|---|---:|---|
| A. Cohort validation | z-score of targeted feature vs neighbors; abandon if z<+3 | 1 | <1 min (parquet cache) |
| B. Blast radius | LHS/Sobol over full param space, WR15 on affected cohort | 50-150 | 5-20 min |
| C. Drill | Bayesian (Optuna) on Phase B top quartile | 100-400 | 10-30 min |
| D. Final tune | Dense fine-grid ±10% of Phase C winner, full multi-window check | 30-80 | 5-10 min |
| E. Validate | Run W1-W6 on Phase D winner | 1 | <1 min |

**Why no MC at Stage 1:** a WR15-lifting change should improve per-trade quality available to the cascade; ranking Stage 1 candidates needs no MC. Stage 2/3 handle barrier/cascade interactions — an MC as the Stage 1 objective leaks portfolio metrics into scoring calibration, reintroducing the contamination this framework eliminates. **Exception:** if a Stage 1 winner shifts signal density >30% in any binding tier, run a quick N=100 smoke MC on `22-now` only to confirm cascade fill dynamics (not return optimization).

#### Stage 1-N — Neutrality-proof track (scoring-neutral/stability/honesty ships)

**Added 2026-06-11.** Three of the last four ships (v69 weekly-honesty, v71 leak fixes, v72 fakeout-stability) weren't growth-motivated — W-gates assume growth motivation, so these were evaluated ad-hoc; this track makes them first-class. A candidate declaring itself scoring-neutral ships on:

| # | Requirement | Template |
|---|---|---|
| **N1** | **Tradable-bucket invariance** — full-faithful `ScoreSimulator` A/B over ≥1y full universe: ZERO bucket-crossing diffs in tradable bands (call≥70, put≤30) | v72: 194,526 pairs, 0 violations |
| **N2** | **Reconstruction/bit-exactness proof** on whatever subset the mechanism claims to preserve | v72: 11,492/11,492 stored fired rows bit-exact |
| **N3** | **Direct evidence on the actual value metric** — the thing the ship is FOR, measured not asserted | v72: WCF fakeout groups −60% on `score_intraday_logs` replay |
| **N4** | W4 still runs on any diagnostic buckets that DO change | v72: `<30` bucket +0.8pp, reported |

W5 returns FLAG by construction (neutral change → dG≈0, ties FLAG by design) — satisfied by N1-N3 for this track; no Stage 2/3 confirmation needed when N1 proves zero tradable change. If N1 finds ANY tradable-bucket diff, the candidate isn't neutral — route through normal W1-W6.

#### Gate waiver ledger (normalization-of-deviance guard)

**Added 2026-06-11.** Every waived/excepted gate gets one row: date, version, gate, class, reason. **Rule: three waivers in the same class → fix or retire the gate before the next ship** — an always-waived gate is training us to ignore the alarm. Classes: `stale-calibration`, `tiny-N`, `inherited-baseline`, `scoring-neutral`, `other`.

| Date | Ship | Gate waived | Class | Note |
|---|---|---|---|---|
| 2026-05-08 | CTSL | W5 N-floor | stale-calibration | floor table v46-stale at ship |
| 2026-05-08 | CTSL | W6 gradient | inherited-baseline | 80-84>85-89 inversion pre-existed in v46 baseline |
| 2026-05-11 | v50 | W3 1y | tiny-N | 1y 70+ cumulative softness, thin window |
| 2026-05-19 | v60 | W5 N-floor REVIEW | stale-calibration | accepted as soft throughput trade-off |
| 2026-06-10 | v71 | N-floor REVIEW | stale-calibration | 3rd strike — table still v46-calibrated |
| 2026-06-11 | v72 | W5 FLAG | scoring-neutral | stability ship; value invisible to growth gate |
| 2026-06-11 | — | RESOLUTIONS | — | stale-calibration→floor table report-only+recalibration queued; tiny-N→W2/W3 N≥100 scoping+W6 pooling/z-gating; inherited-baseline→W6 inherited-vs-introduced distinction; scoring-neutral→Stage 1-N track |

---

### Stage 2: Barrier Optimization Gate

**Applies to:** any change to `TP_BASE`, `TP_STRESS`, `SL_BASE`, `SL_STRESS`, `HOLD_DAYS`, `PREMIUM_MULT`, `BREADTH_THRESHOLD`, `PUT_TP`/`PUT_SL`.

**Why separate:** these define the option-aligned barrier set; they invalidate the `barrier_outcomes` cache (keyed on K/M/W/PREMIUM_MULT) forcing forward-walk recompute, and drive option TP%/avg_option_pnl/the WR15→option-capture tax — metrics absent from Stage 1 (barrier-independent) and Stage 3 (DD-primary).

**Primary objective: Option TP% on full population, 5y, 30 DTE** — bound the WR15→option-capture tax between what scoring admits and what the strategy captures (some WR15-positive signals fire SL or resolve too late; TP/SL should bound that cost via capital recycling).

**Secondary: `avg_option_pnl_15d`** on the same buckets — catches theta-trap configs where TP% looks fine but P&L lags (TP fires day 14 under heavy theta drag).

**Hard constraints:**

| # | Constraint | Threshold |
|---|---|---|
| **B1** | Stage 1 frozen | Scoring stack unchanged; run drift-guard before sweep |
| **B2** | Smoke MC DD bound | N=300 × 22-now only: 5y WorstDD ≤ baseline +1.5pp |
| **B3** | Smoke MC collapse | 0% on 22-now × N=300 |
| **B4** | Per-tier option TP% non-regression | No call tier (95+…75+) regresses option TP% >1pp on full 5y |
| **B5** | Capital recycling efficiency | Median bars-to-resolution ≤ baseline +1 bar |

**Sweep cadence:** A. Baseline pin (current barrier set's TP%/pnl/DD; 1 variant, ~3 min cache-served) → B. Coarse Bayesian (LHS over TP_BASE/TP_STRESS/SL_BASE/SL_STRESS/BREADTH_THRESHOLD/HOLD_DAYS; 5-10 × full barrier rebuild + 22-now smoke; 1-3 hrs) → C. Fine refinement (top-3, ±10% Bayesian; 5-8; 1-2 hrs) → D. Validate (final candidate's full 5y assessment + N=300 22-now MC; 30 min). Each Stage 2 candidate needs a `barrier_outcomes` cache rebuild for the new K/M/HOLD set (~30-60 min 30 DTE, longer 15 DTE) — coarse sweep is mandatory, full Bayesian is impractical here.

---

### Stage 3: Tertiary Portfolio Gate

**Applies to:** F3F (call/put), MaxPos, cascade allocation (TIER_ALLOC, PUT_TIER_ALLOC), DD soft-band, sector-concentration waves, dead-hold, slippage, regime slopes (REGIME_SLOPE_UP/DOWN), EARN_SUPP_PUT, CT_PROMOTE, SAW Put U-curve, hard-sell timing/P&L.

**Why DD primary, compound secondary:** compound returns at scale (>1e10%, ≈$50k→$5T) are theoretical, not realizable — the MC engine assumes unlimited broker capacity, free liquidity on rare 95+ signals, no bid-ask drag, no IV crush; beyond ~1e15% is fantasy. Headline compound numbers (1e26%, 1e36%) measure model dynamics, not trader outcomes — 1e36%→1e35% loses nothing real. DD is what survives translation to real execution: a 71.9% drawdown is what a trader actually experiences; the 80% floor is a hard account-survival threshold, not theoretical. Compound becomes a "no catastrophic regression" sanity check: 1e36→1e30 is fine (both beyond cap); 1e10→1e5 is a real regression (crosses the realizable scale).

**Sector-concentration exposure waves:** a Stage 3 portfolio risk problem, not Stage 1 objective. If a Stage 1 candidate raises WR15/preserves N but produces correlated same-sector fills, keep the ranker unchanged and test a smooth sector-concentration allocation wave after scores+barriers freeze. Use risk-weighted post-fill exposure (`sector + call/put side`), not raw symbol count, as a soft throttle:
```text
sector_share_after = sector_exposure_after / total_book_exposure
pressure = smoothstep(start, full, sector_share_after)
scale = max(floor, 1 - max_cut * pressure)
```
The wave must not rewrite scores/`Score` rows or join the WR15 objective unless the sector feature independently proves WR15 signal value.

**Hard constraints (all required):**

| # | Constraint | Threshold |
|---|---|---|
| **T1** | Stage 1+2 frozen | Scoring/barrier set unchanged |
| **T2** | Iteration count | N=500+ per (window×mode) — N=150 4-window is insufficient (Phase OP1 lesson) |
| **T3** | Window coverage | All 8 canonical: 2021, 2022, 2023, 2024, 2025, dip, 22-now, 5y |
| **T4** | **5y WorstDD primary** | ≤ baseline +1.0pp — DD reduction is the real ship reason |
| **T5** | Per-window DD stability | No annual window regresses DD >5pp vs baseline |
| **T6** | Collapse rate | 0% on every (window×mode) cell |
| **T7** | Compound non-regression sanity | 5y compound OOM within ±3 of baseline — catches strategy-breaking changes, ignores MC-noise at 1e26+% scale |

**Soft constraints (judgment):** TS1 per-trade WR15/option TP% drift (should hold per Stage 1+2 invariant — drift means scoring is leaking through); TS2 slot displacement undocumented; TS3 per-window DD asymmetry (flag, don't auto-reject); TS4 "compound win at DD cost" (>50% compound lift + >1pp 5y DD worsening = Phase OP1 trap, reject unless compound crosses ~1e10% AND DD cost bounded); TS5 confirm DD-improvement isn't accidental CT_PROMOTE-class dampening crowding out high-conviction signals; TS6 report per-sector/per-side exposure before/after — DD wins should come from reduced clustering, not hidden quality drift.

**Sweep cadence:** A. Baseline pin (N=500×8, PYTHONHASHSEED=0..2, quantify MC noise; 3-6 hrs) → B. Bayesian (LHS+Optuna over portfolio knobs, N=100×8; 50-150; 4-8 hrs) → C. Drill top-5 (N=300×8; 4-6 hrs) → D. Ship gate (final at N=500×8; 1-2 hrs).

#### Deep-window screens (SCREEN, not GATE)

**P1.2 (gameplan):** `monte_carlo.py`'s opt-in `MC_WINDOW_SET=deep` env toggle (default `default`, unset=byte-identical) appends 4 named pre-2016 windows to the standard 12-row `WINDOWS`, riding the 1995-01-03 v74 score+regime+breadth backfill (survivor-only — see traps.md "Deep historical backfill... is survivor-only"):

| Window | Span | Stresses |
|---|---|---|
| `ltcm_1998` | 1998-07 to 1998-10 | LTCM/Russia/Asia crisis — short, sharp, liquidity-driven |
| `dotcom_crash_2000_2002` | 2000-03 to 2002-10 | Dot-com bust — 2.5-year GRIND, not a V-shape (a class 2020-COVID can't test) |
| `gfc_crash_2007_2009` | 2007-10 to 2009-03 | GFC — deep, credit-driven, slow-motion |
| `2007_now` | 2007-01 to present | GFC + everything since, one continuous compound read |

**SCREENS, not GATES** — never a Stage 3 hard constraint (T1-T7), a Stage 1/2 gate input, or a calibration target:
- **Survivor-only bias uncorrected here** — only names still existing (usually thrived) contribute to 1998-2009 history; every deep-window DD/collapse number is an optimistic FLOOR, not real (honest number needs the delisted-equity gap closed, gameplan P2.1). Precedent: dot-com-era crash test already shows Apex COLLAPSING (−84% 5y ret), Core at −42% even on this optimistic substrate.
- **A deep FAIL is a MANDATORY INVESTIGATION, never an automatic revert** — chase the mechanism (dead-hold sane across a multi-year grind? DD band-pass excluding the wrong regime shape?), don't reflexively kill the change.
- **A deep PASS is WEAK COMFORT, never collapse-proof** — the "collapse≈0 on EVERY window incl. 2020-COVID" bar is about the survivor-corrected default 12-row `WINDOWS`, not the deep screen.
- **Never calibrate/tune/Bayesian-sweep against a deep window** — they check a candidate that already cleared standard T1-T7 on default windows; selecting for `dotcom_crash_2000_2002` performance is overfitting to one survivor-biased accident.
- **Default path is bit-identical** — `MC_WINDOW_SET` unset/`=default` leaves `WINDOWS` exactly the standard 12-row list (verified via same-day pristine-vs-edited paired smoke, traps.md).

Usage: set `MC_WINDOW_SET=deep` before invoking any driver reading `monte_carlo.WINDOWS` (or pass the 4 labels via `WINDOWS_OVERRIDE`). Queue as `--db heavy` (`2007_now` spans ~19 years). See `experiments/deep_crash_screen/` for the P1.2 driver + env-recipe docs per profile.

---

### What NOT to do

- Do NOT use MC compound metrics inside a Stage 1 scoring objective — Stage 1 = WR15 + cohort z + N floor only.
- Do NOT encode sector concentration in Stage 1 scoring unless it independently predicts WR15 — handle correlated fills/DD clustering as a Stage 3 exposure wave.
- Do NOT use the old cumulative H1 (`≥+0.5pp on ≥3 of 5 tiers`) as a Stage 1 gate — masks within-tier asymmetry (ICH put-side: cumulative `<25` +0.72pp while discrete `<10` lost 2.4pp and 60% N). W4 is the corrected gate.
- Do NOT trust N=80/N=150 4-window MC as Stage 3 ship gates (Phase OP1 lesson).
- Do NOT bump `ALGORITHM_VERSION` for Stage 2 or 3 changes — only Stage 1 modifies `Score.overall`.
- Do NOT skip drift-guard (`tests/test_strategy_config_drift.py`) after editing `strategy_config.py`.
- Do NOT chain Stage 1→2→3 sweeps in a single ship — each takes hours; ship one stage at a time, validate before unfreezing the next.
- Do NOT calibrate/tune/gate any parameter on a deep/survivor-only window (`ltcm_1998`, `dotcom_crash_2000_2002`, `gfc_crash_2007_2009`, `2007_now`) — SCREENS only.

---

### Decision: which stage applies?

Ask what the change touches: modifies `Score.overall` (formula, dampener, weight, lift, gate) → **Stage 1**. TP/SL/HOLD/PREMIUM_MULT/BREADTH_THRESHOLD → **Stage 2**. Anything else portfolio (F3F, MaxPos, cascade, DD soft-band, sector wave, dead-hold, slippage, regime slopes, EARN_SUPP_PUT, CT_PROMOTE, SAW) → **Stage 3**.

Examples: editing `compute_overall_score` weights → Stage 1; new score-stage dampener → Stage 1; `TP_BASE` 0.30→0.35 → Stage 2; adding `DEAD_HOLD_TRIGGER_PNL` → Stage 3; slippage −0.013→0.0 → Stage 3; `F3F_CALL_FLOOR` → Stage 3; toggling `EARN_SUPP_PUT` → Stage 3; SAW Put U-curve → Stage 3; new sector-concentration wave → Stage 3.

---

### Iterative Score Improvement Workflow (Stage 1 anchor)

Primary method for proposing Stage 1 candidates: evidence-driven loop — examine real historical signals, identify cohort patterns, form a falsifiable hypothesis with cohort z>3, validate with WR15-primary multi-window check.

**Guiding principle:** anchor each iteration to specific historical signal cohorts, not abstract formula tweaks. A pattern on 2y of AAPL history is a hypothesis, not a finding — it becomes a finding when (a) cohort z≥+3 across the full 5y universe, (b) same direction holds on MSFT and NVDA, (c) Stage 1 W1-W6 all pass.

**Step-by-step:**
1. **Audit signal history**: `trader explain-scores AAPL 730`. Group by verdict (MISS/BAD_LUCK/CORRECT); look for cohort patterns behind MISS clusters.
2. **Form a falsifiable hypothesis with cohort definition.** Bad: "the RSI weight seems off." Good: "HIGH signals where TREND>70 AND RSI<55 AND volume<0.5 magnitude have 40% WR15 vs cohort baseline 60% — TREND is carrying the score without confirmation." Write the boolean predicate + proposed change explicitly.
3. **Pre-flight cohort z-score**: `python experiments/miss_ledger/cohort_z.py --cohort "trend>70 AND rsi<55 AND vol_mag<0.5"`. Abandon if z<+3.
4. **Move the candidate into staging scoring** — edit the real checkout scoring path in an algorithm-refinement worktree; `ScoreSimulator(scoring_fn=...)`/monkey-patches OK for early probes, but must be in staging before Phase B/C/D.
5. **Phase B blast radius**: `python experiments/<my_dampener>/phase_b_lhs.py --variants 100` — ranked variants by WR15 lift on affected cohort + cumulative/discrete WR/N + recycle-adjusted daily N deltas; reward high-tier N expansion (80+/85+/90+) at preserved WR15, don't penalize 70-74 N compression alone.
6. **Phase C drill**: `python experiments/<my_dampener>/phase_c_drill.py --variants 200` — Bayesian on top quartile.
7. **Phase D fine-tune + W1-W6 check**: `python experiments/<my_dampener>/phase_d_validate.py --winner <best>` — computes WR3/5/7/15/30 across 1y/3y/5y full universe + every discrete bucket, verifies W1-W6, emits `recycle_n_report.json` (whether N moved from unused 70-74 into binding/higher-quality tiers, stayed above the recycling threshold, or simply disappeared from 75+/80+ capacity).
8. **Ship**: if W1-W6 pass — commit → merge/pull → bump `ALGORITHM_VERSION` → deploy recalc sequence (`trader recalculate 1d`, then `trader recalculate --force`, `--force --full` off-hours) → `trader assess --force` to populate validation rows.

### What to look for in MISSes

| Pattern | Likely cause | What to change |
|---------|-------------|----------------|
| TREND high, RSI/BB neutral | TREND over-weighted in sideways market | Raise sideways `d` threshold, or add RSI confirmation gate |
| Volume magnitude <0.3 on all MISSes | Amplifier firing on weak signals | Raise minimum magnitude threshold for amplification |
| MISSes cluster at specific regime | Regime multiplier inflating score past threshold | Regime-aware threshold, or reduce multiplier magnitude |
| Weekly composite contradicts daily on all MISSes | Weekly dampening too weak | Increase weekly adjustment weight |
| All MISSes are LOW-score (put) | LOW bucket calibration gap — fires on bounce not breakdown | Investigate LOW-score driver; may need separate bearish component logic |
| High conf (>0.45) on MISSes | Model right, market wrong (BAD_LUCK) | No formula change; consider regime/breadth entry-gate filters instead |

### Output to anchor discussion on

After `explain-scores`, paste representative MISSes/BAD_LUCK signals: the 2-line output, the approximate win rate for that signal type from the assess table, and the proposed hypothesis. Claude evaluates the hypothesis against indicator logic, proposes specific formula changes, and simulates them — the correct agentic loop, not abstract algorithm discussion without signal evidence.
