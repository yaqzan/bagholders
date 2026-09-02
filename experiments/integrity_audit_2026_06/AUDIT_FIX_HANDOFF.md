# Scoring Integrity Audit — Fix Campaign Handoff (2026-06-09)

**For:** the executing agent (no prior conversation context assumed — everything you need is here + the repo docs).
**From:** 2026-06-09 full-pipeline audit of `compute_overall_score` + all feature builders/callers + DB row verification.
**Mission:** fix four verified integrity defects in the scoring pipeline, rebuild the damaged caches, honestly re-validate the affected mechanisms, and ship the result as ONE bundled scoring version (v71 candidate) with the full recalc/assess/research-pack tail. Then close the doc drift.

**Authorization scope:** the user has explicitly asked for this campaign — "kick off a workflow that starts the process of fixing all of these and doing the recalcs where needed." You may proceed end-to-end (fix → validate → version bump → recalc → assess → docs) without asking, per the autonomous-ship convention, EXCEPT the two marked user-decision points in §6.

**Companion ledger entries:** [`alpha_mining/NEW_LEADS.md`](../../alpha_mining/NEW_LEADS.md) section "⚠ I. 2026-06-09 scoring-integrity audit" (findings I1–I4) and leads N1–N5. This document is the executable version of that block. Update both at the end.

---

## 0. Current-state snapshot (verify before starting — things move)

| Item | State at handoff (2026-06-09) | Re-verify with |
|---|---|---|
| Active scoring | v70 (`c70d16d22`), DB version id 70 | `cat ALGORITHM_VERSION`; `trader algorithm active` |
| Active portfolio | v70 Apex profile (calls-only, TP+30/SL−70, dead-hold −0.40/−0.15, overflow 0.035) | `algorithm_versions/portfolio_profiles.json` |
| **Working tree** | **DIRTY — the 2026-06-09 calendar-hold + honest-theta standardization is UNCOMMITTED on `main`** (strategy_config, monte_carlo, backtest_cascade, task_queue/daemon, docs, etc. — see known-issues CURRENT SHIP STATE top entry) | `git status`; check whether it has since been committed |
| Task queue daemon | running (`.cache/task_queue.db` active) | `trader queue status` |
| Barrier cache | **TRUNCATED** — 117 dates only, 2025-12-19 → 2026-06-08, 1,436,256 rows × 3 sets (`30dte_generic`, `30dte_opt`, `15dte_opt`) | Appendix Q3 |
| Market Wave source CSV | **MISSING** — `.cache/market_wave/` and `.cache/sector_etf_screen/` directories do not exist | `Test-Path .cache\market_wave\predictive_market_wave_v57_source.csv` |
| Holdout lock | DISABLED (`CALIBRATION_CUTOFF_DATE=None`, user-directed 2026-06-05) — full-window data usable | `strategy_config.py` |

**If the calendar-hold work is still uncommitted when you reach elevation (§5): stage ONLY your integrity files (`git add <explicit paths>`), never `git add -A`/`git add .`, and never revert/commit the calendar-hold files yourself.** If a conflict makes that impossible, stop and surface it to the user — that is the one tree-state situation you must not bulldoze.

---

## 1. The four defects (evidence → fix spec → verdict procedure)

### F1 [CRITICAL — silent no-op] v57 Sector Market Wave score transform is inert in ALL current row sets

**What:** `SECTOR_BREADTH_WAVE_ENABLED=True` ([strategy_config.py:453-474](../../strategy_config.py)) and every doc lists the v57 Market Wave in the active stack, but the source file `.cache/market_wave/predictive_market_wave_v57_source.csv` (and the fallback default `.cache/sector_etf_screen/sector_breadth_daily_2020plus.csv`) is missing. [`database/utils/sector_breadth_wave.py`](../../database/utils/sector_breadth_wave.py) `_load_rows()` (line ~201) returns `[]` on a missing path **with no warning** → empty series → `wave_on_or_before()` returns 50.0 → stress=0 (stress_start 35.24 < 50) and repair=0 (repair_start 67.20 > 50) → zero adjustment, no `weight_info` key, invisible.

**DB evidence (re-run via Appendix Q1/Q2):** zero rows carry `sector_breadth_wave` in `weight_info` across **v60, v69, AND v70** on deep-stress dates (2025-04-08 tariff selloff, 2022-06-13 bear, 2020-03-16 COVID) and bull-repair dates (2026-02-10). All three row sets were recalced in June 2026 (honest cross-version campaign), after the CSV vanished. So:
- The honest v70 baseline, the Apex profile sweeps, and the RXDD/SVR/MWDD/TVDD calibrations all ran **wave-less** — internally consistent, still valid, but the docs' mechanism inventory is wrong.
- **Dropping a rebuilt CSV in place without a version bump would silently change live scores vs stored history.** Never do that — the wave only comes back (if at all) through the versioned ship in §5.

**Fix spec:**
1. **Rebuild the source series** self-contained from SPDR sector-ETF prices using `market_breadth._load_sector_etf_breadth_rows(end_date=...)` ([market_breadth.py:260](../../market_breadth.py)) — the MWDD mine already used this to rebuild 1999+ history. Write a builder script at `experiments/integrity_audit_2026_06/build_market_wave_source.py` producing a CSV with columns `date`, `pct_above_ema50`, `pct_above_ema200` (the loader accepts those names; with the shipped `source='market_wave'` + `mode='direct_market_wave'`, `_apply_market_wave_source()` computes `market_wave_score` deterministically from those columns using `DEFAULT_MARKET_WAVE_PARAMS` — no other inputs needed).
   - ⚠ **This is NOT a reproduction of the lost v57 file.** The original CSV (provenance unknown, possibly precomputed scores) is gone. Your rebuilt series is a NEW candidate input. Treat the wave as a fresh Stage-1 mechanism A/B, not a restoration.
2. **Add a loud guard** in `sector_breadth_wave.py`: when `enabled=True` and (path missing OR last row older than ~7 calendar days vs the requested `signal_date`), emit a prominent log/stderr warning once per process (do NOT hard-raise — recalc and tests must still run; the failure mode to kill is *silent*).
3. **Kill the staleness-by-design problem** if the wave survives: nothing in `trader.py` refreshes this CSV (verified — no references), so even when the file existed, live dates past its last row forward-filled stale values. If the wave re-validates, either (a) regenerate the CSV in `trader close-update`, or (b) better, compute the series from the DB/SPDR prices directly with an in-process cache, removing the file dependency.

**Verdict procedure (decides ship content):** honest Stage-1 A/B on the v70 substrate — wave ON (rebuilt source, shipped params) vs OFF (= current de-facto baseline) via `ScoreSimulator`, evaluated per-discrete-bucket WR15/N on the option-aligned barrier + generic sanity + W5 growth gate.
- **Default expectation: RETIRE.** The v57 calibration was fitted on pre-v69 look-ahead scores, and the MWDD mining (2026-06-05) showed breadth-crash cohorts are mean-reversion *winners* — dampening calls in crash states is the documented crash-artifact trap. If ON does not clearly pass (W1–W6, W5 SHIP), set `SECTOR_BREADTH_WAVE_ENABLED=False`, keep the code, and clean the docs (it has been OFF in practice all era — formal retirement just makes the config tell the truth).
- If ON passes honestly, include it in the v71 ship with the rebuilt + guarded + refreshed source.

### F2 [HIGH — live look-ahead leak, v69 class] `mis_stress` (CALL side) + JA4 `put_regime_multiplier` read the SPY weekly composite with current-week look-ahead

**What:** [`database/models/core.py`](../../database/models/core.py) `_load_spy_wk_composite_map()` (~line 3080) loads SPY `WeeklyScore` rows (Monday-keyed; in recalc/backfill each row holds the **complete Mon–Fri week**) and `_spy_wk_on_or_before(target=score_date)` (~3094) forward-fills to the signal date — selecting the **current week's** row. A Mon–Thu historical signal therefore reads a `spy_wk` containing future bars. This is byte-for-byte the v69 weekly look-ahead class; the v69 fix covered the per-stock weekly path but missed these two consumers in core.py's regime helpers.

**Leak direction (why it inflates):** `_load_mis_stress_map()` (~3295) computes `gap = max(0, spy_wk − regime_composite)`, full strength at 30 (`MIS_STRESS_FULL`). With look-ahead spy_wk, the mis-stress softener relaxes call regime-compression preferentially in weeks that *end* strong → calls admitted into future-strong weeks → WR inflation on the affected cohort (~162 full-strength days + partials; at mult 0.78 a full softener moves an 80-call ~+1.7pts — enough to cross the 75 tradability gate). `_compute_put_regime_mult()` (~3102, JA4) blends 25% spy_wk into the put multiplier → puts suppressed in future-strong weeks → put-assessment inflation (lower stakes — puts are OFF in all v70 profiles, but assessment/dashboard numbers carry it). Live update reads the *partial* current week → live-vs-recalc divergence (also an intraday fakeout channel for these mechanisms).

**Fix spec (mandatory, regardless of A/B outcomes):** change the SPY-weekly lookup to **last-completed-week**: look up at `target − timedelta(days=7)` — the exact convention `build_kijun_pct_map` / `build_wv_force1_map` use ([scoring.py:347-470](../../database/utils/scoring.py)). Cleanest implementation: add `_spy_wk_last_completed(sorted_dates, date_map, target)` that calls `_spy_wk_on_or_before(..., target - timedelta(days=7))`, then switch **every** call site — do not miss any:
- single-row path: core.py ~669-677
- `calculate_scores_batched`: ~3422-3429 and ~4024-4028 / ~4139-4143
- `recalculate_scores_batched`: ~4666-4672 / ~4914-4918 and the ~5229 wrapper
- `_load_mis_stress_map()` internal lookup: ~3328

Run `gitnexus_impact` on `_spy_wk_on_or_before` and `_load_mis_stress_map` first to catch any call site this list missed. This makes live and recalc consistent (both use the completed week) and removes the leak.

**Verdict procedure:** with the lagged lookup in place, A/B each mechanism on the honest substrate (ScoreSimulator, 5y, option barrier + W5):
- `mis_stress` lagged-ON vs OFF (`MIS_STRESS_CALL_DAMPEN=0`): the original ship evidence (+0.2pp WR15, +5.6% N on 22-now) was thin AND measured with the leak AND calibrated on the 2026-04-09 composite-inversion-era thesis. **Default: retire if not clearly positive** — bias to simplification on the honest substrate.
- JA4 lagged-ON vs OFF: same. With puts off portfolio-wide, retiring it is nearly free; keep only if the put-assessment surface clearly benefits.
- Document both verdicts either way (FINDINGS.md). Both are `Score.overall` changes → part of the v71 bundle.

### F3 [HIGH — ops landmine] `barrier_outcomes` cache truncated; next full recalc would write cont-echo-inconsistent history

**What:** `.cache/barrier_outcomes.db` now covers only **117 dates (2025-12-19 → 2026-06-08)** — it was evidently wiped and re-seeded by the nightly `refresh_recent(days=160)` (start date ≈ 160d before the mtime; likely collateral of the 2026-06-08/09 calendar-hold engine work). It used to cover multiple years. The continuation echo's prior-win loader `_load_cont_barrier_wins()` (core.py ~3349) returns `{}` outside coverage **silently** → `cont_lift` would be zero for every recalc date before 2025-12-19 → **any `trader recalculate --force --full` run before the cache is rebuilt writes rows whose continuation behavior differs by era, with no error.** (Current stored v70 rows are fine — they were computed while the cache was still full. The landmine is the NEXT recalc, i.e. exactly the one this campaign performs.) Side effect: `trader assess` historical windows lose the cache fast-path (slow, but correct — it falls back to full forward walks).

**Fix spec (HARD PRECONDITION for §5 — do this FIRST, it is the longest pole):**
1. Queue the full rebuild off-hours: `trader queue submit --priority low --db heavy --cpu 4 --restartable --dedup barrier-backfill-3650 --window off_market --reason "restore barrier cache history before integrity recalc" -- python -m database.barrier_cache backfill 3650` (the `backfill [days]` verb exists — verified in `database/barrier_cache.py` `__main__`; 3650 = 10y to match the eventual `--force --full` recalc span). Expect hours; bridge with `trader queue wait <id>` under `run_in_background`.
2. The DuckDB mirror rebuilds automatically at the end of `backfill`; if not, `python -m database.barrier_cache rebuild-duck`.
3. **Add a coverage guard** in `_load_cont_barrier_wins()`: if `lo_date < MIN(date) in cache for that barrier_set`, log a loud one-time warning ("continuation echo priors missing before YYYY-MM-DD — barrier cache truncated?"). This is the regression test for the whole class.
4. **Verify before recalc:** re-run Appendix Q3 — min date must be ≤ your recalc window start (~2016 for 10y).
5. Investigate-while-it-runs (cheap, optional): confirm whether the truncation was an intentional re-key from the calendar-hold barrier changes. Either way the rebuild uses current barrier definitions, which is what the v71 recalc needs.

### F4 [MEDIUM — survivorship leak] MCD uses TODAY's `Stock.market_cap` for ALL historical dates

**What:** `mcap_b` is loaded from `Stock.market_cap` (populated by yfinance `ticker.info['marketCap']` — a *current* snapshot; core.py:3620) and applied to every historical scoring date (core.py:708-709 single-row, 3936 batch, 4499 recalc). Stocks that GREW into large caps — whose historical small-cap signals disproportionately won — escape historical MCD dampening, while shrinkers get dampened. Part of the 8.2pp mcap↔TP gradient and MCD's +2.73pp ship evidence is survivorship artifact, and MCD's −44.9% N cost on 75+ may be partly unjustified.

**Fix spec:** point-in-time proxy `mcap_t ≈ mcap_today × close_t / close_today` computed inside the batch/recalc loops (closes already loaded; `close_today` = latest close in the loaded history — keep the definition consistent across all three paths). Document the approximation (ignores share-count drift from buybacks/dilution; split-consistent with adjusted closes; far better than static). Implement as a helper so single-row/batched/recalc share it.

**Verdict procedure:** rebuild the mcap cohort table on the honest substrate with PIT mcap (`experiments/mcap_dampener/` harness exists — `build_features.py` needs the PIT column added): if the monotonic mcap↔TP ladder survives at z≥3, recalibrate MCD's constants against the PIT distribution (the LOG_LO/LOG_HI anchors shift when caps are deflated to their historical values); if the ladder collapses, retire MCD (set `MCD_ENABLED=False`) and recover its N. Either verdict joins the v71 bundle. Note: MCD is the single biggest N consumer in the stack — retirement/recalibration here is the largest N-recovery on the board.

### Cleared suspicions (close these out in docs — no code work)

- **C1:** the CLAUDE.md / known-issues note "ICH `kijun_pct` + WVD `wv_force1` likely share a residual weekly look-ahead (future cleanup)" is **FALSE**. Both builders ([scoring.py:347](../../database/utils/scoring.py) / :410) use a deliberate last-completed-week lookup (`date − 7d` bisect over Monday-keyed weekly bars) and are wired identically in all three scoring paths (verified: core.py 713-733, 3938-3952, 4501-4517). Retire the note at the §5.7 doc batch.
- **C2:** continuation-echo outcome timing is point-in-time-safe — `compute_cont_prior_signal` ([scoring.py:495](../../database/utils/scoring.py)) gates each prior window's outcome on elapsed gap (`wins.get(W) if gap >= W`), so unresolved-window outcomes cannot leak. No fix needed (F3 is about cache *coverage*, not timing).

---

## 2. What this campaign is NOT (do not scope-creep into closed ground)

While in the scoring code you will be tempted by adjacent ideas. These are **freshly closed** — do not re-open (see known-issues WHAT NOT TO DO + NEW_LEADS traps):
- VIX weekly-MACD / velocity / acceleration — null as DD lever AND as regime-multiplier input (2026-06-09).
- Directional re-grades of `overall` (miss-cohort dampers, per-stock normalization, divergence, relative strength, 70-74 selective promotion) — all null on the option barrier (`experiments/missretest_apex15/`).
- New market-context call-sizing DD levers — the well is DRY after RXDD/SVR/MWDD/TVDD + F3F.
- Regime multiplier band/weight re-tunes — read-only A/B on honest v70 already showed current is optimal-or-best.
- Generic-barrier-only evidence — the gen15 trap (SVD/v42). Option-aligned barrier is primary, always.

The ONE adjacent expansion that IS in-scope if runway allows: **N1 dampener ablation** (§4 Phase D) — subtractive auditing of existing mechanisms, which the missretest closures do not cover.

---

## 3. Workflow rules you must follow (the repo's hard constraints)

1. **Worktree first.** This is scoring-algorithm work → `git worktree add ../Trader-exp-integrity -b algo-exp/integrity-audit` (from current `main` HEAD; the uncommitted calendar-hold changes correctly stay behind on main). All scoring edits + simulations happen there. Worktree closeout (merge or document + remove) is part of the task.
2. **Queue ALL long compute.** Anything minutes+ (backfill, ScoreSimulator 5y sweeps, recalc, assess, MC, research pack) goes through `trader queue submit` — never raw, never the harness background runner for heavy jobs. Alpha/integrity sweeps default `--priority high` **off-hours**; during market hours use `--window off_market` or `normal/low`, and never let a heavy-DB job outrank the scheduled `trader update`. Get notified via `trader queue wait <id> --timeout <t>` run with the harness `run_in_background` flag.
3. **Never run `trader update`/`trader recalculate` from the worktree.** Sims/sweeps only. Recalc happens from the designated ship checkout (`main`) after elevation.
4. **GitNexus:** `gitnexus_impact({target, direction:"upstream"})` before editing each symbol (`_spy_wk_on_or_before`, `_load_mis_stress_map`, `_load_cont_barrier_wins`, `compute_overall_score` call-path helpers, `SectorBreadthWaveSeries.load`); `gitnexus_detect_changes()` before each commit; `npx gitnexus analyze` only after a successful push (loop-break rule: if analyze only changes generated count lines, stop — don't commit them).
5. **Growth-gate honesty:** when running `experiments/version_scorecard/stage1_growth_gate.py`, ALWAYS run `signal_supply.py` for the candidate first — a `~` (fallback coverage) in gate output = untrustworthy verdict (documented false-SHIP trap).
6. **Full-faithful validation for final claims.** ScoreSimulator A/Bs are for iteration; the shipping claim comes from the real recalc + `trader assess --force` numbers (P0-approximation trap, v42 lesson).
7. **Tests after `strategy_config.py` edits:** `python tests/test_strategy_config_drift.py` + `python tests/test_mechanism_registry.py` + `python experiments/_dte_audit/audit.py`. The pre-commit hook enforces the first two — never `--no-verify`.
8. **Doc-update timing:** CLAUDE.md / `.claude/docs/*.md` edits trigger a permission prompt that can stall for hours. Batch ALL doc edits into one turn at the END (§5.7), or immediately before queueing a long task. `alpha_mining/*` and `experiments/*` files are safe to edit anytime.
9. **trader-api restart** (needed after strategy_config changes for the API to serve them): run `& C:\Development\server.bat restart -Service trader-api` via the **PowerShell tool with `run_in_background: true`** — foreground hangs forever; Bash `cmd.exe /c` silently no-ops. Verify `GET http://127.0.0.1:5000/health` → 200.
10. **Live Portfolio behavior on ship:** the portfolio engine auto-runs a re-qualification sweep on the first `trader update` after the version bump (entry-date scores re-looked-up under v71; non-qualifying positions exited at session close with `version_sweep` close notifications). This is EXPECTED — do not "fix" it; do make sure the recalc produced v71 rows for held positions' entry dates before the next market session's update (the normal 5y force recalc covers this).
11. **Score-row contamination guard:** recalculate ONLY the new version. Never put v57/v60/v69/v70 in `--score-versions` from this checkout.
12. **EOD invariance:** keep every fix EOD-deterministic. Do not touch the intraday confidence paths (`INTRADAY_TYPE_CONF_GATE` etc.).

---

## 4. Execution plan (phases; A∥B∥C can overlap)

### Phase A — unblock (start immediately, runs in background)
1. `trader queue status` (daemon up? anything heavy running?).
2. Queue the **F3 barrier backfill** (`backfill 3650`, off_market, heavy-DB, restartable, dedup `barrier-backfill-3650`). Bridge with `trader queue wait` in background. *Everything in Phase E waits on this.*

### Phase B — code fixes (worktree `algo-exp/integrity-audit`)
3. **F2:** add `_spy_wk_last_completed()` + switch all call sites (list in §1-F2; confirm with gitnexus_impact). Unit test: synthetic Monday-keyed weekly rows → assert a Wednesday lookup returns the PRIOR week's value.
4. **F4:** PIT-mcap helper + wire into single-row/batched/recalc. Unit test on a synthetic price series.
5. **F1:** `build_market_wave_source.py` (SPDR breadth → CSV with `date,pct_above_ema50,pct_above_ema200`, full history 2016+ minimum, ideally 1999+ to match MWDD's rebuild) + the loud missing/stale guard in `sector_breadth_wave.py`.
6. **F3 guard:** coverage warning in `_load_cont_barrier_wins`.
7. Three small regression tests under `tests/` (spy-wk lag; wave-source-present-when-enabled; cont-echo coverage warning) so this defect class can't silently recur.

### Phase C — honest A/B evaluation (worktree, ScoreSimulator, queue the heavy runs)
8. Build the evaluation ledger once (5y honest substrate, option-aligned barrier primary + generic sanity; reuse `experiments/component_reweight/` capture_inputs fast re-score or `experiments/rqc_v60/` ledger pattern).
9. Evaluate, per mechanism, per-discrete-bucket WR15/N + W5 growth verdict (with REAL supply):
   - a. baseline-as-is (current code, leaks included) — the reference;
   - b. F2-fixed (lagged spy_wk), mis_stress ON vs OFF, JA4 ON vs OFF;
   - c. F4-fixed PIT-mcap MCD ON (recalibrated if cohort survives) vs OFF;
   - d. F1 wave ON (rebuilt source) vs OFF.
10. Decide the v71 bundle per the default verdicts (§1, §6). Write `experiments/integrity_audit_2026_06/FINDINGS.md` with every verdict + evidence table.
11. **Stretch (only if runway):** Phase D — N1 ablations of the remaining pre-v69-calibrated dampeners (WCF, CWCF, CWWD, CSWC, SCW, WVD) on the same ledger, eval-only; ship ONLY clear-cut failures, defer marginal calls to a follow-up. Do not let this delay the integrity ship.

### Phase E — elevation + ship (designated checkout = `main`; F3 backfill must be DONE)
12. `trader algorithm snapshot-staging --key integrity-audit --label "F1-F4 honest fixes"` (from the worktree).
13. Merge the worktree branch to `main` (stage ONLY your files if main still dirty — §0 warning). `gitnexus_detect_changes()` → commit scoring code (NO recalc yet).
14. Bump: `git rev-parse --short HEAD > ALGORITHM_VERSION` → separate commit (`tests/test_algorithm_version_sync.py` enforces this pairing).
15. `trader algorithm snapshot-git-ref --status ship_candidate --create-db-version`.
16. **Recalc sequence (queued, from main):** market hours → `trader recalculate 1d` first (today's dashboard rows), then `trader recalculate --force` (5y); `trader recalculate --force --full` (10y) queued `--window off_market`. All `--db heavy` via the queue; never detached-raw.
17. `trader assess --force` (after recalc), then research pack: `python tools/build_research_pack.py --version v71 --profiles all --run-portfolio-windows` (queued).
18. W5 N-floor signal check: `PYTHONIOENCODING=utf-8 python -u experiments/n_floor_v46/check_signals.py` (~5s). REVIEW is a soft gate — document the trade-off if it fires.
19. Stage-3 sanity smoke: N=300 × {5y, 22-now, 2022, 2020_crash} Apex MC vs the v70 baseline numbers — collapse must stay 0 everywhere. The shipped DD levers (RXDD/SVR/MWDD/TVDD) were calibrated on wave-less v70; if v71 mostly removes inflation the distribution shift should be small. **Full Stage-3 re-sweep ONLY if the smoke shows collapse>0 or a major DD regression** — otherwise note "spot-checked" and move on.
20. `trader algorithm promote-candidate v71 --status shipped` after validation passes. Push. Then (and only then) `npx gitnexus analyze` if needed.

### Phase F — closeout
21. **Doc batch (ONE turn):** known-issues CURRENT SHIP STATE + CLOSED timeline rows; retire the C1 "ICH/WVD residual look-ahead" note; correct the "v57 Market Wave active in the stack" claims in CLAUDE.md / trading-strategy.md / scoring-algorithm.md per the F1 verdict; version-history.md v71 section; NEW_LEADS.md — mark I1–I4 resolved with verdicts, re-rank N1.
22. Restart trader-api (rule §3.9). Verify dashboard serves v71.
23. Worktree closeout: classify shipped, merge confirmed, remove `../Trader-exp-integrity`.
24. Auto-memory: one entry for the campaign outcome (what shipped, what was retired, where the record lives).

---

## 5. Acceptance criteria ("done" means)

- [ ] Barrier cache covers the full 10y recalc span (Appendix Q3 min date ≤ 2016-06).
- [ ] `_spy_wk_*` lookups lagged in ALL paths; unit test green.
- [ ] PIT-mcap in all three scoring paths; unit test green.
- [ ] Wave: either honestly re-shipped (rebuilt + guarded + refreshed source) or formally retired (`ENABLED=False`) — no third state; loud guard in place either way.
- [ ] Cont-echo coverage guard in place.
- [ ] v71 row set complete (1d → 5y → 10y), assess + research pack + N-floor done, Stage-3 smoke collapse=0.
- [ ] Per-mechanism verdicts recorded in FINDINGS.md; NEW_LEADS.md + known-issues updated; docs no longer claim inert mechanisms are active.
- [ ] Drift-guard / registry / dte-audit / algorithm-version-sync all green; pushed.

**Success framing — read this twice:** v71 removes inflation. **Aggregate WR15 may DROP relative to v70's stored numbers, and that is success, not failure** (v69 precedent: −12pp accepted because it was honest). Do NOT reject a leak fix because the backtest got worse. The gates that must not regress are: collapse=0, the W4 noise-aware per-bucket guard against *self-inflicted* regressions from retirements (a retirement should restore N at neutral-or-better honest WR, not delete winners), and Stage-3 smoke sanity.

---

## 6. Decision points + my recommendations

**Pre-authorized defaults (proceed without asking):**
- F2 + F4 fixes ship unconditionally (correctness; the A/Bs only decide whether the *mechanisms* stay).
- mis_stress / JA4 / wave / MCD: keep-vs-retire decided by the §1 verdict procedures, with a **bias toward retirement** when evidence is marginal — the honest-era meta-lesson is that directional micro-mechanisms rarely re-earn their keep, and every retirement recovers N and simplifies the audit surface.
- One bundled v71 ship (not four micro-ships) — one recalc, one assess, one research pack, one doc batch.

**User-decision points (surface, don't decide):**
1. **If main is still dirty with calendar-hold at elevation time and selective staging is not cleanly possible** — stop and ask (§0).
2. **Historical options/IV data purchase** (NEW_LEADS N3, ORATS/CBOE-class backfill) — unblocks the top staged lead (OSK) and IV-aware MC; costs money. Recommend it in your final summary; do not purchase anything.

**Other recommendations, in order:**
1. **Kick Phase A first, always** — the barrier backfill is hours-long and gates the recalc; everything else fits inside its shadow.
2. **Don't chase exact v57-wave reproduction.** The original CSV is unrecoverable; the honest A/B against today's baseline (wave-OFF) is the only question that matters.
3. **Prefer DB-computed series over CSV files** for any market-level input that survives (the missing-file failure mode is structural to file-based inputs; the loud guard is the floor, removal of the dependency is the ceiling).
4. **If Phase D (dampener ablations) produces big N recoveries, resist bundling marginal ones.** Ship clear-cut verdicts; park the rest as ranked NEW_LEADS entries with the ledger evidence attached. A clean honest v71 beats a sprawling one.
5. **Watch the 70-74 overflow interaction:** retirements (esp. MCD) push N back into 70-84. The Apex overflow tier (0.035) consumes 70-74 supply — if 70-74 density jumps >30%, run the N=300 smoke before declaring (cascade fill dynamics, not return optimization).
6. **Timebox:** this is one focused overnight for Phases A–C + E if verdicts are clean; two if Phase D is included. The queue + `wait` bridges make the long tails unattended-safe.

---

## Appendix — verification queries (run before AND after)

**Q1 — wave inertness (expect 0 everywhere pre-fix; >0 on stress/repair dates for v71 if wave ships):**
```python
from database.models.core import Score
import datetime as dt
for vid in [70]:  # add 71 post-ship
    for d in ['2025-04-08','2022-06-13','2020-03-16','2026-02-10']:
        dd = dt.date.fromisoformat(d)
        tot = Score.select().where(Score.date==dd, Score.version==vid).count()
        sbw = Score.select().where(Score.date==dd, Score.version==vid,
                                   Score.weight_info.contains('sector_breadth_wave')).count()
        print(vid, d, tot, sbw)
```
Result at handoff: v60/v69/v70 all `sector_wave 0` on every date above (totals 637–775/day).

**Q2 — mis_stress leak signature (post-F2: spy_wk used for date D must equal the W−1 completed composite):** spot-check `_load_mis_stress_map()` values for a known mid-week date against the SPY WeeklyScore table — pre-fix, a Tuesday uses the row dated that week's Monday (complete week); post-fix it must use the prior Monday's row.

**Q3 — barrier cache coverage:**
```python
import sqlite3
con = sqlite3.connect('.cache/barrier_outcomes.db')
print(con.execute("SELECT barrier_set, MIN(date), MAX(date), COUNT(DISTINCT date) "
                  "FROM barrier_outcomes GROUP BY barrier_set").fetchall())
```
Result at handoff: all three sets `('2025-12-19','2026-06-08',117)` — must become min ≤ recalc window start before Phase E.

**Q4 — file presence:** `.cache/market_wave/predictive_market_wave_v57_source.csv` and `.cache/sector_etf_screen/sector_breadth_daily_2020plus.csv` — both MISSING at handoff.

**Key file/line index (verified 2026-06-09; re-verify lines after the calendar-hold work lands):**
| What | Where |
|---|---|
| Wave loader silent fallback | `database/utils/sector_breadth_wave.py` `_load_rows` ~201, `SectorBreadthWaveSeries.load` ~52 |
| Wave application in scoring | `database/utils/scoring.py` ~1557-1611 |
| Wave config (path/params) | `strategy_config.py` ~453-474 |
| SPY-wk map + on_or_before + JA4 | `database/models/core.py` ~3080-3115 |
| mis_stress map builder | `database/models/core.py` ~3295-3346 |
| spy_wk/mis_stress call sites | core.py ~669-681, ~3422-3429, ~4024-4028/4139-4143, ~4666-4672/4914-4918, ~5229 |
| cont-echo prior-win loader | `database/models/core.py` `_load_cont_barrier_wins` ~3349 |
| cont-echo gap gating (clean) | `database/utils/scoring.py` `compute_cont_prior_signal` 495-522 |
| mcap static load sites | core.py 708-709, 3936, 4499; populated at 3620 (`ticker.info['marketCap']`) |
| kijun/wv_force builders (clean) | `database/utils/scoring.py` 347-470 |
| SPDR breadth rebuild helper | `market_breadth.py` `_load_sector_etf_breadth_rows` 260 |
| Barrier backfill CLI | `python -m database.barrier_cache backfill 3650` (verbs at barrier_cache.py ~1059) |
