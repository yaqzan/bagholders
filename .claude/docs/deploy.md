# Deploy & Ship Procedures

How to ship a scoring change, a portfolio change, or a new portfolio mechanism end-to-end. Cross-references ship gates (per-trade vs MC) in [assessment-backtest.md](assessment-backtest.md).

---

## Decision: which stage applies (three-stage framework, 2026-05-08)?

Ask what the change touches:
- `Score.overall` (compute_overall_score, score-stage dampener, weight, lift, gate) → **Stage 1** (W1-W6, WR15-primary, recalculate + ALGORITHM_VERSION bump)
- TP/SL/HOLD_DAYS/PREMIUM_MULT/BREADTH_THRESHOLD → **Stage 2** (B1-B5, option TP% primary, no version bump, barrier_outcomes rebuild required)
- Anything else portfolio (F3F, MaxPos, cascade alloc, DD soft-band, sector-concentration, dead-hold, slippage, regime slopes, EARN_SUPP_PUT, CT_PROMOTE, SAW) → **Stage 3** (T1-T7, DD-primary MC, no version bump)

Full criteria: [assessment-backtest.md](assessment-backtest.md) "Three-Stage Calibration Framework".

**Never chain Stage 1→2→3 sweeps in one ship** — each stage takes hours; ship one at a time so each validates before the next unfreezes.

---

## Shipping a Scoring Change

`get_or_create_current()` reads the ALGORITHM_VERSION **file**, not git HEAD. Recalc-ing while the file points at the old commit corrupts the old version's rows with the new formula ("v33 formula stored under v32 rows" incident, broken three times). Strict sequence:

```bash
# 1. Implement + commit scoring code (NO recalc yet)
git add database/utils/scoring.py database/models/core.py simulator.py
git commit -m "v46 scoring: ..."

# 2. Bump ALGORITHM_VERSION file -> that commit, separate commit
git rev-parse --short HEAD > ALGORITHM_VERSION
git add ALGORITHM_VERSION && git commit -m "Bump ALGORITHM_VERSION to $(git rev-parse --short HEAD~)"

# 3. NOW recalc — scores tag correctly to the new version.
# Market hours: fill today's rows first, then 5y force; run 10y full off-hours.
python trader.py recalculate 1d
python trader.py recalculate --force
# off-hours:
python trader.py recalculate --force --full
```

These recalculate commands are deploy/elevate/ship steps for a versioned scoring candidate — don't run from ordinary algorithm-refinement worktrees (those use simulations/sweeps + candidate snapshots until elevation).

### Algorithm silo checkpoints

Scoring ships also produce a local silo under `algorithm_versions/vNN/` (scoring code, scoring/portfolio variables, source fingerprints, evidence/cache manifests) — doesn't replace the ALGORITHM_VERSION commit-pointer flow, lets versions be compared without relying only on git history.

During refinement, work in isolated branches/worktrees; the worktree is staging code. Snapshot before elevation:
```bash
python trader.py algorithm snapshot-staging --key candidate-name --label short-label
```
After the code commit + ALGORITHM_VERSION bump exist, create/resolve the DB version row and capture the ship-candidate silo from the recorded commit:
```bash
python trader.py algorithm snapshot-git-ref --status ship_candidate --create-db-version
```
Default key `v<AlgorithmVersion.id>`; writes tracked files to `algorithm_versions/vNN/`, prepares `.cache/algorithm_versions/vNN/` for large artifacts. After recalc+validation:
```bash
python trader.py algorithm promote-candidate vNN --status shipped
```
Backfill of older vNN silos is deferred, off-hours, newest-to-oldest.

`tests/test_algorithm_version_sync.py` fails if HEAD modifies scoring files but ALGORITHM_VERSION doesn't match HEAD or HEAD~.

**Detached-run guard:** a background/Codex runner must preserve the staged order — never generate a single `recalculate force full` script right after a version bump during market hours. Either run the market-hours sequence (1d → 5y force) or explicitly defer the 10y full step to off-hours.

**10y is still required for final Stage 1 validation** — calibration sweeps and W3 multi-window gates need it; a market-hours 5y recalc is interim usability only.

### Recalculate speed paths

- `recalculate --force --full --scores-only` — writes score rows only, defers historic/assessment/temporal tail.
- `recalculate --tail-only --tail-window 5y` — runs the derived tail separately after score writes finish.
- `recalculate --force --full --reuse-components-from vNN` — clones expensive component/context columns from a prior version, recomputes only `overall`; formula-only ships where components didn't change.
- `recalculate --force --full --score-versions vNN,vMM` — writes requested historical sidecar versions in the same batched context walk; dangerous during scoring ships (shares data loads/component builders from the current checkout, swaps `compute_overall_score`); efficient only for tightly-controlled recent multi-algo comparison, not a full old-code replay.
- Default workers `cpu_count - 1`; `--workers N` or `TRADER_RECALC_MAX_WORKERS=N` to throttle if MySQL bottlenecks.

**Score-row contamination guard:** after a candidate scoring edit, never put a prior shipped version in `--score-versions` to compare/repair/backfill it — overwrites those rows with candidate-checkout component context even though `version_id` stays old. Ship = recalculate only the newly bumped active version. To repair/replay an older version: check out its pointer/silo, verify loaded code + ALGORITHM_VERSION match the target DB row, run a target-only repair with a run artifact recording version id/commit/command/date window. To analyze old versions without writes: `tools/build_research_pack.py`, `tools/backfill_research_packs.py`, existing score rows, or read-only SQL.

**Never trust P0 (10%-sample / `_ph_cache={}` approximation) alone for indicator-input changes.** v42 rolling weekly: P0 said +4.51pp, full-faithful production assess showed −13.2pp (17.7pp error) — the approximation bypasses breakout/divergence-push detection inside `calculate_rsi_score` (±15pp lift on extreme RSI). Mandate: full `recalculate --force --full` + `assess --force`, read the production WR table before claiming lift.

**Never ship on small-stock-screen evidence alone.** Phase I 2026-05-08: 60-stock sweep showed +6/+7pp on 85+; full universe (772 stocks) showed −3.0pp. 95+ tier in the 60-stock screen had N=2-5 — noise.

**Never modify the live serving checkout's scoring code to test a hypothesis.** Use an isolated worktree; edit the real scoring path there, snapshot with `trader algorithm snapshot-staging` before elevation. Don't run shared production score writers from that sandbox except from the designated ship checkout. No env-gates, no flags, no "default OFF" research knobs in shipped scoring files. `ScoreSimulator(scoring_fn=variant_fn)` and runtime monkey-patches with `try/finally` teardown remain valid for quick probes; before W1-W6 validation, migrate the variant into the staging checkout and rerun evidence through `trader simulate` / `ScoreSimulator(scoring_fn=None)` / a staging-native runner. Legacy pattern: `experiments/weekly_avwap/phase_i_wadj_blend_sweep.py`.

**Run the W5 N-capacity-floor check after recalc:**
```bash
PYTHONIOENCODING=utf-8 python -u experiments/n_floor_v46/check_signals.py   # ~5 sec
```
Classifies each tier SAFE (within 5% of baseline) / MARGINAL (buffer zone) / REVIEW (below floor). Soft gate — REVIEW is acceptable if the candidate offsets volume loss via allocation bumps or per-trade WR uplift; document the trade-off in known-issues.md. (Static floor table demoted to report-only 2026-06-11 — W5's binding-window hydration owns droughts; read the freshest `experiments/n_floor_v46/summary_v2.json`.)

**Post-recalc comparability unit** (added 2026-06-12, automated 2026-06-15) — a new version isn't comparable/gateable until all three parts exist. `build_research_pack` produces all three by default and prints `comparability_unit=COMPLETE|INCOMPLETE`:
```bash
python tools/build_research_pack.py --version vNN --run-portfolio-windows
#   1. pack (--profiles defaults to ALL) + auto-tail (2)+(3)
#   2. experiments/version_scorecard/signal_supply.py --versions vNN          (supply/hydration row)
#   3. experiments/version_scorecard/portfolio_response.py --materialize vNN  (PRF matched sizing -> derived_portfolio.json)
```
If INCOMPLETE, the tail names which part failed — rerun before treating as gateable. Missing (2) runs the Stage-1 growth gate on APPROXIMATED supply (the documented false-SHIP trap); missing (1)/(3) leaves VersionCompare hydration/PRF cells null; a sentinel-only pack (no `--profiles all`) drops the version from apex/core VersionCompare toggles entirely. v74 hit both 2026-06-15 (tail was manual AND `--profiles` defaulted to sentinel) — both now default-on. `derived_portfolio.json` (instrument-only) is also the seeded first candidate for post-ship Stage-3 retune when supply density shifted — F proposes, MC disposes (T1-T7 still mandatory).

Before shipping, also do an N-awareness read: N increases in binding high-quality tiers (80+/85+/90+) are a large positive when WR is flat/up; N drops in 70-74 are acceptable (live allocation skips that overflow bucket). Don't accept/reject on cumulative 70+ N alone — require discrete-bucket WR/N deltas to see whether quality moved up or capacity was merely deleted. Recalibrate the floor table (`run.py`+`reaggregate.py` against the new active version) only if the ship shifts ≥30% offered/year on any binding tier.

---

## Shipping a Portfolio Change (Stage 2 or 3)

**Stage 2** (TP/SL/HOLD/PREMIUM_MULT/BREADTH_THRESHOLD): see [assessment-backtest.md](assessment-backtest.md) "Stage 2: Barrier Optimization Gate" — coarse Bayesian (5-20 candidates), barrier_outcomes rebuild per candidate, B1-B5 gate.

**Stage 3** (F3F, MaxPos, cascade alloc, DD soft-band, sector-concentration, dead-hold, slippage, regime slopes, EARN_SUPP_PUT, CT_PROMOTE, SAW):
1. Edit `strategy_config.py` (single source of truth).
2. Drift-guard: `python tests/test_strategy_config_drift.py` (~1s, mandatory).
3. Validate at N=500+ × 8 windows — [assessment-backtest.md](assessment-backtest.md) Stage 3 gates T1-T7.
4. After ship: `python trader.py temporal-refresh --profiles all` (calendar/monthly tabs reflect new params for Sentinel/Core/Apex, both DTEs).
5. Run the post-ship audit checklist below (drift-guard doesn't catch display strings).
6. `PYTHONIOENCODING=utf-8 python -u experiments/n_floor_v46/check_signals.py` — Stage 3 doesn't shift `Score.overall`, so offered counts are invariant, should always be SAFE. Non-SAFE means something else changed — investigate before declaring shipped.
7. Recalibrate the floor table (`experiments/n_floor_v46/run.py` + `reaggregate.py`) after ANY change touching MaxPos/cascade alloc/DD-soft band/F3F floors/dead-hold/SAW U-curve — the floor is calibrated against realized fills, which depend on these mechanics.

No ALGORITHM_VERSION bump, no `trader recalculate` — per-bucket assess WR/TP% is invariant to portfolio changes. Profile versions live in `algorithm_versions/portfolio_profiles.json`; score rows stay keyed only by scoring `AlgorithmVersion`.

Sector-concentration waves are Stage 3 exposure shaping — keep Stage 1 WR15/high-tier-N ranking unchanged; validate any sector throttle through T1-T7 plus exposure attribution, not per-score recalc.

**Never use N=150 4-window MC as a portfolio ship gate** — Phase OP1 rankings flip at N=300; Phase realloc Stage 2 N=150 wins reversed at Stage 3 N=300×8. **Lock decisions on 5y; treat 22-now as confirmation only** — 22-now-only wins reverse at 5y.

---

## Post-Ship Audit Checklist (Existing Portfolio Values)

Drift-guard catches numeric value mismatches in `monte_carlo.py`, `backtest_cascade.py`, `api.py` but NOT display strings, docstrings, tooltips, or fallback constants baking values into formatted text. A clean drift-guard pass means engines compute correctly, NOT that the UI/CLI shows the new values.

Run after ANY portfolio value change (TP/SL/cascade/allocation/breadth threshold/F3F/MaxPos/dead-hold/DD soft-band):
```bash
# 1. Value drift in engines (already covered by drift-guard)
python tests/test_strategy_config_drift.py

# 2. CLI display strings
PYTHONIOENCODING=utf-8 python trader.py alloc 50000 2>&1 | grep -E "Call exits|Put exits|F3f|DD soft|cascade|Hard sell"
PYTHONIOENCODING=utf-8 python trader.py tp        # if TP changed
PYTHONIOENCODING=utf-8 python trader.py stop      # if SL changed

# 3. Help-text docstrings (static, won't auto-update)
PYTHONIOENCODING=utf-8 python trader.py backtest --help 2>&1 | grep -iE "default|widens|breadth|tp|sl"

# 4. Frontend fallback constants (used when /api/strategy/config fetch fails)
grep -nE "DEFAULT_ADVANCED" src/pages/Backtest.js | head -3
# verify literal values match strategy_config.py STRATEGY_30DTE

# 5. Frontend tooltips referencing breadth threshold or hardcoded TP/SL
grep -nE "breadth score <= [0-9]+|TP \$\{" src/pages/Backtest.js | head -10

# 6. Dashboard MarketPanel — fetches /api/strategy/config; visual check on load

# 7. python tools/capital_plan_refresh.py after any portfolio-profile or scoring-version ship;
#    review the VERDICT DELTA banner, update .claude/docs/capital-plan-2026.md if it fires
```

Files that commonly hold stale display strings (drift-guard blind spots): `trader.py` `_cmd_tp_stop` docstring ("Stressed (breadth ≤ N): TP=+X% SL=-Y%"); `trader.py` `_cmd_alloc` docstring+print block (EXITS/CASCADE summaries); `trader.py` `_cmd_backtest` docstring ("--tp default N (widens to M when breadth ≤ K)"); `src/pages/Backtest.js` `DEFAULT_ADVANCED` (literal fallback numeric defaults) and `getFieldTips()`/`FIELD_TIPS` (tooltip strings); `api.py` parameter docstrings ("default 50" in query-param descriptions).

Prefer runtime-formatted strings over baked literals:
```python
# Bad — bakes "50" at write time:
print(f"adaptive: TP=+40 when breadth<=50")
# Good — reads live value at runtime:
print(f"adaptive: TP=+{int(opt.TP_STRESS*100)} when breadth<={opt.BREADTH_THRESHOLD}")
```

---

## Adding a NEW Portfolio Mechanism — Full Consumer Checklist

A new strategy field (e.g. H3 `DD_SOFT_BAND_*` 2026-05-04, SAW Put U-curve 2026-05-08) wires 10+ consumers. Drift-guard catches value drift, not missing-mechanism bugs in display/UI/CLI.

> **Step 0 (precondition)** — add a `mechanism_registry.REGISTRY` entry BEFORE editing any engine. Every mechanism gets one `MechanismSpec` in [mechanism_registry.py](../../mechanism_registry.py). Declare both DTE statuses (`enabled`/`disabled`/`n/a`); `disabled` requires a non-empty `reason`. Shipping 30 DTE only → 15 DTE row must be `disabled` with a `wiring_mode` (`not_wired` if the engine adds zero constants, `wired_neutral` if it wires no-op defaults) and a free-text reason why.
>
> Run `python tests/test_mechanism_registry.py` — fails with a specific gap message if registry says `enabled` but the engine isn't wired (or vice versa). Pre-commit hook ([tools/git-hooks/pre-commit](../../tools/git-hooks/pre-commit)) refuses commits failing drift-guard OR the registry test — never `--no-verify` for a mechanism ship. (Registry exists because SAW Put U-curve and DD soft-band both shipped 30-DTE-only in 2026-05 without an explicit 15 DTE decision recorded in code.)

| # | File | What to update |
|---|------|---------------|
| 0 | `mechanism_registry.py` | PRECONDITION — `MechanismSpec`, DTE statuses + `wiring_mode` + reason. Run test BEFORE editing engines. |
| 1 | `strategy_config.py` | Add field to `DteStrategyConfig`/`OptionStrategyConfig`; set on `STRATEGY_30DTE`/`STRATEGY_15DTE`; comment ship date + headline metric |
| 2 | `monte_carlo.py` | Module-level `NAME = float(os.environ.get('NAME', str(_cfg.NAME)))`; apply in `_try_fill_call`/`_try_fill_put`/resolve loop; add to `main()` print summary |
| 3 | `monte_carlo_15dte.py` | Same (or wire disabled if not validated for 15 DTE) |
| 4 | `backtest_cascade.py` | Module-level constants + apply in deterministic entry loop (mirror MC) |
| 5 | `backtest_cascade_15dte.py` | Same |
| 6 | `api.py` | Pull from `_cfg` + accept query-param override in `/api/backtest/run`; apply in entry loop; expose in `params` response |
| 7 | `trader.py` `_cmd_backtest` | Pull from `_scfg_f3f`; apply in entry loop; add to summary print block |
| 8 | `trader.py` `_cmd_alloc` | Pull from `_alloc_cfg`; add display line in GUIDELINE section |
| 9 | `tests/test_strategy_config_drift.py` | Add to `pairs_mc`/`pairs_bc`/`pairs_mc15`/`pairs_bc15` per registry's wiring declarations |
| 10 | `src/pages/Backtest.js` | Add to `DEFAULT_ADVANCED`, `buildAdvancedFromConfig`, `FIELD_TIPS` |
| 11 | `.claude/docs/known-issues.md` | Update CURRENT SHIP STATE; append CLOSED — SHIPPED; add WHAT NOT TO DO from null calibrations |
| 12 | `.claude/docs/trading-strategy.md` | Add mechanism section under Exit Logic/Position Sizing; update authoritative snapshot |
| 13 | `.claude/docs/version-history.md` | New section: mechanism description + N=500 validation table + commit reference |

Drift-guard only checks values, not mechanism wiring — a mechanism wired into MC engines but not `trader.py`'s deterministic backtest or the frontend silently diverges dashboard simulation from actual strategy. The registry (step 0) closes the engine-wiring gap; consumers 6-10 still rely on this checklist.

**Smoke after wiring:**
```bash
python tests/test_strategy_config_drift.py             # 1s — value drift
python tests/test_mechanism_registry.py                # <1s — registry vs engine consistency
PYTHONIOENCODING=utf-8 python trader.py alloc 50000    # 2s — CLI shows new mechanism
PYTHONIOENCODING=utf-8 python trader.py backtest --from 2025-01-01 --capital 50000 2>&1 | grep -iE "your_field"  # 30s — deterministic backtest applies + prints
```
**After ship:** `PYTHONIOENCODING=utf-8 python trader.py temporal-refresh --profiles all`

---

## Shipping a Portfolio Change — Site-Wide Propagation (schema-driven)

Portfolio-stage changes (TP/SL, caps, cascade, RXDD, dead-hold, DTE router, CTSL, SAW, F3F, regime slopes, slippage, …) must propagate site-wide — the portfolio analog of a scoring ship bumping ALGORITHM_VERSION. The param manifest (`portfolio_param_manifest.PARAMS`) drives the Backtest advanced-params UI, `/api/backtest/run` overrides, and a CI guard — most propagation is automatic.

**Editing an existing knob's value** (e.g. Apex TP 30→28):

| # | Consumer | Action |
|---|----------|--------|
| 1 | `strategy_config.py` | edit the value; comment ship date + headline metric |
| 2 | `algorithm_versions/portfolio_profiles.json` | if profile-specific (differs Apex/Core/Sentinel), update each override; if shared, lives only in strategy_config |
| 3 | drift-guard | `python tests/test_strategy_config_drift.py` (+ `test_mechanism_registry.py`) |
| 4 | VersionCompare / research packs | `trader queue submit … -- python tools/build_research_pack.py --version vNN --profiles all --run-portfolio-windows` — rebuilds per-profile stress windows. Required for ANY portfolio change (numbers are stale otherwise). |
| 5 | Assessment calendar | `trader temporal-refresh --profiles all` — rebuilds `BacktestTemporalStats` per profile (separate surface from VersionCompare) |
| 6 | docs | known-issues CURRENT SHIP STATE + trading-strategy authoritative block + version-history |

Backtest UI default auto-updates (fetches `/api/strategy/param-manifest`, seeded live from strategy_config+profile) — no manual frontend edit. Allocator reads the live config/profile too.

**Adding a NEW knob** (also do the 13-consumer engine wiring above):

| # | Consumer | Action |
|---|----------|--------|
| 0 | `portfolio_param_manifest.py` | add a `Param` (surfaces in UI + overridable) or an `EXCLUDED` entry with a reason. `tests/test_portfolio_param_manifest.py` fails until classified. |
| 1 | engines | editable-per-run knobs: `run_backtest` reads from `cfg` (`cfg.get('your_key', MODULE_DEFAULT)`), add `your_key` to `ENGINE_CFG_KEYS` — `test_engine_cfg_keys_in_sync` enforces the pair. Mirror across mc/mc15/bc15 for bit-identical MC. |
| 2-6 | (as above) | drift-guard, VersionCompare rebuild, calendar, docs |

**Editable vs display-only:** a manifest knob is editable (overridable per-run in UI) iff its cfg target is in `ENGINE_CFG_KEYS`; otherwise display-only — still reproduces on profile select (engine applies from config), just not yet UI-tweakable. Wiring it (step 1) flips it editable.

**Never ship a portfolio change without step 4 (research-pack rebuild)** — the exact gap that left VersionCompare's Apex/Core stale + RXDD-inert (2026-06-04), and `min_score=75` silently dropping the 70-74 overflow band on the Backtest page.

---

## Live Portfolio — auto re-qualification on a ship

The live Portfolio page (v70 Apex tracker, `portfolio_engine.py`) is a persisted forward ledger that adapts to ships automatically — no manual step beyond the normal recalc/propagation above:

- Fingerprints the active scoring `version_id` + Apex config (`_strategy_fingerprint`). On the first `trader update` after a scoring ship (new ALGORITHM_VERSION + recalc) or portfolio change (any strategy_config/portfolio_profiles.json edit), it runs a re-qualification sweep that session: each open position's entry-date score is re-looked-up under the new version, and if it no longer clears the cascade threshold (`_min_call_threshold` = lowest tier with non-zero alloc) the position exits at session close (market hours), tagged `version_sweep`/`strategy_sweep`, close notification fires. Survivors ride to their barriers; new entries use new rules; a strategy change is adopt-new-barriers for open positions.
- Realized history is preserved — closed trades + past equity frozen, never recomputed. Validated bit-exact against `run_cascade_backtest` for a single-version window.
- Precondition: the sweep re-scores entry signals under the new version, so that version's `Score` rows for the held `(symbol, entry_date)` pairs must exist — a scoring ship's recalc already produces them; a portfolio-only change reuses the same rows.

Exits only execute on completed market sessions — a sweep triggered outside market hours actions at the next session close.

---

## Smoke MC (any change)

```bash
MC_NO_MP=1 N_ITER_OVERRIDE=100 WINDOWS_OVERRIDE=22-now python -u monte_carlo.py
```
For scoring changes, optional — run only if signal density shifts >30% in a high-allocation tier.

---

## Calibration Holdout Lock

Any new `experiments/*/sweep.py`, miss-ledger mining script, or lift-table rebuild MUST gate on `CALIBRATION_CUTOFF_DATE = "2026-06-15"` (in `strategy_config.py`; re-locked 2026-06-11 after the 2026-06-04 disable — see known-issues Priority #11 for lock history).

```python
from experiments._holdout import assert_no_holdout_leak
assert_no_holdout_leak(df, context="my_sweep")  # top of main()
```

`build_features.py`-style scripts should prefer `pre_cutoff_filter()` for materialization-time enforcement. `HOLDOUT_DISABLE=1` env var is the explicit bypass for live-trading evaluation contexts. Re-evaluation target ≈2026-12-15 (first OOS read on the v71/v72 honest stack).
</content>
