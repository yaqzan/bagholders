---
name: ship-version
description: Ship a scoring change (Stage 1) end-to-end — the strict commit/bump/recalc sequence, scoring-lock capture, algorithm silo checkpoints, market-hours-aware recalc, assess + research-pack + version registration, live-Portfolio re-qualification, and doc updates. Use when a Stage 1 hypothesis has cleared the ship gate (W1-W6 SHIP/FLAG) and you are elevating it to the new active ALGORITHM_VERSION, or the user says "ship this version", "bump the algorithm version", or "promote vNN to active".
---

# /ship-version — ship a scoring change to the active ALGORITHM_VERSION

Elevates a Stage-1-gated scoring candidate (see [/ship-gates](../ship-gates/SKILL.md)
for what "gated" means) to the live `ALGORITHM_VERSION`. This is the **Stage 1**
ship path only — `Score.overall` changes. If your change is TP/SL/HOLD_DAYS/
PREMIUM_MULT/BREADTH_THRESHOLD (Stage 2) or anything else portfolio (F3F,
MaxPos, cascade, dampener knobs, profiles) → **NO version bump** — use
`/ship-portfolio` instead. Decision rule + gates: [assessment-backtest.md](../../docs/assessment-backtest.md)
"Decision: which stage applies?".

Precondition before starting this skill: gate evidence is complete
(`stage1_growth_gate.py` returned SHIP, or FLAG with a documented justification +
named watch metric per `/ship-gates`), and you have autonomous-ship authorization
context (either the user explicitly asked you to ship, or you are in a
pre-authorized autonomous loop — see auto-memory `feedback_autonomous_ship.md`).
If evidence is incomplete, stop and go mine/gate first
([/find-and-ship-alpha](../find-and-ship-alpha/SKILL.md) Phase 6 is the canonical
walk-through of this same sequence from the discovery side).

## GUARDS (read before touching ALGORITHM_VERSION)

1. **THE ORDER is commit code → bump commit → THEN recalc. Never recalc before
   the bump commit lands.** `AlgorithmVersion.get_or_create_current()` reads the
   `ALGORITHM_VERSION` **file**, NOT git HEAD. If you recalculate while the file
   still points at the OLD commit, the new formula computes but gets tagged
   under the OLD version's `version_id` — corrupting that version's historical
   rows ("v33 formula stored under v32 rows", per `deploy.md` — **this has broken
   three times**). The fix is two separate commits before any recalc:
   ```bash
   # 1. Scoring code only. NO recalc yet.
   git add database/utils/scoring.py database/models/core.py simulator.py
   git commit -m "v75 scoring: <what/why>"
   # 2. Bump ALGORITHM_VERSION file -> that commit. SEPARATE commit.
   git rev-parse --short HEAD > ALGORITHM_VERSION
   git add ALGORITHM_VERSION && git commit -m "Bump ALGORITHM_VERSION to $(git rev-parse --short HEAD)"
   # 3. ONLY NOW recalc (see "Recalc sequencing" below).
   ```
   `tests/test_algorithm_version_sync.py` is the mechanical guard: it fails if
   HEAD touched `database/utils/scoring.py` / `simulator.py` / `database/models/core.py`
   but the `ALGORITHM_VERSION` file doesn't match HEAD or HEAD~. Run it after
   step 2, before step 3: `python tests/test_algorithm_version_sync.py`.
2. **Scoring lock must be captured for the new commit, or `trader update` blocks
   next.** `database/scoring_version_guard.py` fingerprints the resolved
   `strategy_config.SCORING` + `CALIBRATION_CUTOFF_DATE` + the scoring-formula
   source bytes and stores it in `algorithm_versions/scoring_locks.json` keyed by
   commit hash. If the new version's commit has no lock entry, later writes can
   warn or (once wiring lands) refuse. Capture it right after the bump commit:
   ```python
   from database.scoring_version_guard import capture_lock
   from database.models.core import AlgorithmVersion
   capture_lock(AlgorithmVersion.get_or_create_current(), note="v75 ship")
   ```
   Then `python tools/check_scoring_version_integrity.py` (exit 0 = match) and
   commit `algorithm_versions/scoring_locks.json` **in the same commit as the
   intentional change** — never let the lock drift into a separate later commit.
   Trap (auto-memory `feedback_scoring_lock_cutoff_drift.md`): toggling
   `CALIBRATION_CUTOFF_DATE` (or any locked `SCORING` constant) without
   re-running `capture_lock` breaks the guard fingerprint and can block ALL
   score writes with a "Refusing score write" / `ScoringVersionMismatch` error —
   the fix is always re-run `capture_lock` + recommit `scoring_locks.json`, never
   toggle the lock target back. See [/debug-pipeline](../debug-pipeline/SKILL.md)
   if you hit this mid-ship. Note: `database/scoring_version_guard.py` is
   documented as **DORMANT** at write entry points as of the 2026-05-31
   integrity doc (`.claude/docs/scoring-version-integrity.md`) — verify current
   wiring by grepping call sites of `verify_active_before_write` before
   assuming it will hard-block; capture the lock regardless, it's cheap and is
   the documented recovery path either way.
3. **Never recalc a prior shipped version's rows from the new candidate
   checkout.** Do not pass an older version into `--score-versions` "just to
   compare" during a ship — the candidate checkout's component/context builders
   can silently corrupt the older version's stored rows even though
   `version_id` looks correct. During a ship, recalculate ONLY the newly bumped
   active version. To repair/replay an older version, check out THAT version's
   own pointer/silo and recalculate it in isolation with a logged run artifact.
4. **10y is still required for final validation, not just 5y.** The
   market-hours order below front-loads a 5y recalc for dashboard usability, but
   Stage 1's W3 multi-time-window gate needs 10y. Treat the 5y recalc as an
   interim usability step and run `recalculate --force --full` off-hours before
   calling the ship validated.
5. **A new version is not gateable/comparable until the three-part
   comparability unit exists.** `signal_supply.py` missing for the new version
   makes the growth gate fall back to `FALLBACK_COVERAGE=0.92` and can
   false-SHIP a candidate that's actually cutting N (the documented v63 trap).
   `tools/build_research_pack.py --version vNN --run-portfolio-windows` now
   auto-runs the supply + PRF tail by default and prints
   `comparability_unit=COMPLETE|INCOMPLETE` as its final line — treat
   `INCOMPLETE` as a hard stop, not a warning.
6. **`trader assess` resolves the HEAD-ish pointer, not the active-scores
   reader.** `assess_scores.run()` calls `AlgorithmVersion.get_or_create_current()`
   (the `ALGORITHM_VERSION` file), NOT `get_active_scores_version()`. On a dirty
   tree mid-ship this can target the wrong version silently. Pass an explicit
   `--version vNN` whenever there is any ambiguity about what HEAD currently
   points at. `trader assess` also takes **positional lookback only** —
   `--days 1825` is silently parsed as a symbol name, not a flag.
7. **Restart the Flask API backgrounded, or it hangs the agent.** After the bump
   + recalc, the API process still serves the old config/version metadata until
   restarted. `server.bat restart trader-api` run in the harness foreground
   **hangs indefinitely** for an agent (a persistent child holds the pipe). Run
   it via the PowerShell tool with `run_in_background: true`; verify
   `GET http://127.0.0.1:5000/health` → 200 afterward. Bash `cmd.exe /c
   "server.bat ..."` silently no-ops (exit 0, old code stays loaded).
8. **The live Portfolio re-qualification sweep on the next `trader update` is
   EXPECTED, not a bug.** `portfolio_engine.py` fingerprints the active
   `version_id`; the first `trader update` after this ship re-looks-up each open
   position's entry-date score under the new version, and any position that no
   longer clears `_min_call_threshold` exits at that session's close tagged
   `version_sweep`. Realized history is frozen and untouched. Don't try to
   "prevent" this — it's the intended adapt-to-new-version behavior. See
   [/portfolio-ops](../portfolio-ops/SKILL.md).

## Step 1 — Confirm you're actually gated to ship

```bash
python trader.py algorithm active                     # confirm current baseline
python experiments/version_scorecard/signal_supply.py --versions <base>,<cand>
python experiments/version_scorecard/stage1_growth_gate.py --baseline <base> --candidate <cand>
```
Verdict must be SHIP, or FLAG with the documented justification + named
post-ship watch metric (see [/ship-gates](../ship-gates/SKILL.md)). Do not
proceed on BLOCK. If the candidate lives in an `algo-exp/<name>` worktree, this
is also the moment to pull/merge latest from `main` if another ship landed
since the worktree started (per `algorithm-version-silos.md` Refinement
Workflow) and re-verify the gate on the merged state before elevating.

## Step 2 — Migrate the candidate into the serving checkout

If the winning formula was developed in a worktree/sandbox, port it into the
real serving scoring path now (`database/utils/scoring.py`,
`database/models/core.py`, `simulator.py` as applicable) — the ship-candidate
silo must represent the actual shipped code, not a sandbox-only variant. No
env-gates, no `PHASE_X_ENABLED`, no "default OFF" research knobs land in shipped
scoring files (`process.md` no-env-gates rule) — a variant that was toggled via
monkey-patch or `ScoreSimulator(scoring_fn=...)` for evidence-gathering must be
migrated to always-on code before this step, not left conditional.

Before elevating, snapshot the staging state:
```bash
python trader.py algorithm snapshot-staging --key <candidate-name> --label <short-label>
```

## Step 3 — THE ORDER: commit, bump, sync-check, recalc

```bash
# 1. Scoring code commit (no recalc).
git add database/utils/scoring.py database/models/core.py simulator.py
git commit -m "v75 scoring: <what/why>"

# 2. Bump ALGORITHM_VERSION -> that commit, separate commit.
git rev-parse --short HEAD > ALGORITHM_VERSION
git add ALGORITHM_VERSION && git commit -m "Bump ALGORITHM_VERSION to $(git rev-parse --short HEAD)"

# 3. Sync-guard check (mechanical enforcement of GUARD 1).
python tests/test_algorithm_version_sync.py

# 4. Capture the scoring lock for the new commit + commit scoring_locks.json (GUARD 2).
python -c "from database.scoring_version_guard import capture_lock; from database.models.core import AlgorithmVersion; capture_lock(AlgorithmVersion.get_or_create_current(), note='v75 ship')"
python tools/check_scoring_version_integrity.py
git add algorithm_versions/scoring_locks.json && git commit -m "v75: capture scoring lock"
```

Create or resolve the DB version row and capture the silo from the exact commit
recorded on that row (preferred over `snapshot-current`/`snapshot-staging` once
both commits exist, because it works even if the working tree has moved on):
```bash
python trader.py algorithm snapshot-git-ref --status ship_candidate --create-db-version
```

## Step 4 — Recalc, market-hours aware

```bash
python trader.py recalculate 1d          # today's rows first (dashboard usable)
python trader.py recalculate --force     # 5y — off market-hours pressure
# Off-hours follow-up (10y is REQUIRED for final Stage 1 validation, GUARD 4):
python trader.py recalculate --force --full
```
This is the market-hours default order from `deploy.md`: fill today's rows
first so the dashboard stays usable, force a 5y recalc, then run the 10y
`--force --full` off-hours (queue it — `trader queue submit --priority high
--db heavy --cpu 4 --dedup <key> --reason "v75 10y recalc" -- python trader.py
recalculate --force --full`; wrap with `trader queue wait <id>` under
`run_in_background: true`). A resumed/interrupted-attempt case ("partial today
rows from an interrupted run") uses `trader recalculate --force 1d`. If the
change is formula-only and component columns are untouched, `--reuse-components-from
vNN` clones expensive component/context columns and only recomputes `overall` —
faster, but only valid when component fields genuinely didn't change. Default
workers are `cpu_count - 1`; throttle with `--workers N` or
`TRADER_RECALC_MAX_WORKERS=N` if MySQL becomes the bottleneck.

Post-recalc soft check (~5 sec):
```bash
PYTHONIOENCODING=utf-8 python -u experiments/n_floor_v46/check_signals.py
```
SAFE / MARGINAL / REVIEW per tier — soft gate, not a veto (a REVIEW is
acceptable if the candidate offsets the volume loss elsewhere; document the
trade-off). The static floor table itself is report-only since 2026-06-11 — W5's
binding-window hydration is the real gate, this is a sanity read.

## Step 5 — Assess, research pack, comparability unit

```bash
python trader.py assess --force                                    # positional lookback only, no --days
python tools/build_research_pack.py --version vNN --run-portfolio-windows
```
`build_research_pack` now auto-runs `signal_supply.py --versions vNN` and
`portfolio_response.py --materialize vNN` by default (`--profiles` defaults to
`all`) and prints `comparability_unit=COMPLETE|INCOMPLETE` as its last line.
`INCOMPLETE` names which part failed — rerun that script before treating the
version as gateable (GUARD 5). If GUARD 6 applies (HEAD ambiguity), pass
`--version vNN` explicitly on the assess call.

Then re-run the real gate on the real pack + `trader temporal-refresh` so
dashboard calendar/monthly tabs reflect the ship:
```bash
python experiments/version_scorecard/stage1_growth_gate.py --baseline <base> --candidate vNN
PYTHONIOENCODING=utf-8 python trader.py temporal-refresh --profiles all
```
Do the **guard-8 portfolio-DD sanity check** from `/ship-gates`: the Stage 1
gate is MC-free and can't see correlated-fill drawdown. Run at minimum a smoke
MC (`MC_NO_MP=1 N_ITER_OVERRIDE=100 WINDOWS_OVERRIDE=22-now python -u
monte_carlo.py`) — full N=500×8 T1-T7 confirmation only if signal density
shifted materially or the change touches a high-allocation tier.

## Step 6 — Register the version for the dropdown + cadence

The `ScoreVersionSelector` dropdown and `--score-versions` sidecar comparisons
only see a version that is (a) an `AlgorithmVersion` DB row (created in Step 3
via `--create-db-version`) and (b) inside the scoring **cadence**
(`_cadence_version_tokens` in `score_calculation_service.py`, floored by
`CADENCE_MIN_VERSION_ID` — never add a version below that floor). If the new
version should be a standing comparison target going forward (not just the new
active version), confirm it's picked up by the cadence set and backfilled via
`--score-versions vNN` from ITS OWN checkout/silo only (never as a sidecar from
the candidate checkout against older versions — GUARD 3).

```bash
python trader.py algorithm promote-candidate vNN --status shipped
python trader.py algorithm document-snapshots     # regenerate vNN/README.md, VERSION_GUIDE.md, algorithm-version-index.md
```
`document-snapshots` is the one that keeps `algorithm-version-index.md` from
going stale (as of 2026-07, it tops out at v66 despite v74 being active — this
step was apparently skipped for several ships; don't repeat that).

## Step 7 — Restart, drift-check, docs, commit/push

```bash
# Restart the API — BACKGROUNDED (GUARD 7), verify health after:
#   PowerShell tool, run_in_background: true:
#   & C:\Development\server.bat restart -Service trader-api
# GET http://127.0.0.1:5000/health -> 200

python tests/test_strategy_config_drift.py     # ~1s, if any strategy_config touched
```

Then run the `gitnexus_detect_changes` **MCP tool** (not a shell command) as the
pre-commit scope check; if the GitNexus MCP is unavailable in the session, fall
back to `git status --short` and stage files explicitly by path.

Docs to update (per `process.md` stale-data-per-ship checklist, scoped to this
ship — do NOT rewrite unrelated sections):
- `known-issues.md` CURRENT SHIP STATE header (new active version, commit,
  DB version id, silo path, one-line revert instruction — `/revert v<old>`)
- `version-history.md` (new top section: what/why/validation/revert)
- `CLAUDE.md` "Active scoring version" line in the Algorithm Versioning section
- `alpha_mining/NEW_LEADS.md` baseline block (bump the stated active version)
- Any `known-issues.md` WHAT NOT TO DO entries earned by nulls found en route
- Run `python tools/capital_plan_refresh.py`, review the VERDICT DELTA banner, and update `.claude/docs/capital-plan-2026.md` if it fires (after any scoring-version ship)

Bundle doc edits at the end (per `process.md` doc-update timing rule — don't
edit docs as the final step immediately before queueing another long task).
Then commit + push:
```bash
git add .claude/docs/known-issues.md .claude/docs/version-history.md CLAUDE.md alpha_mining/NEW_LEADS.md
git commit -m "docs: v75 ship"
git push
```
`npx gitnexus analyze` only after the push succeeds (never on local-commit-only
staleness warnings — see CLAUDE.md GitNexus loop-break rule).

## Revert path

If the ship needs to be undone, it is a pointer flip, not a git revert — see
[/revert](../revert/SKILL.md): `python trader.py revert <old-vNN-or-hash>`.
Score rows for both versions are preserved; nothing is rebuilt or deleted. This
is also the documented autonomous-mode failure path (auto-memory
`feedback_autonomous_ship.md`): if the post-recalc MC gate breaks, `trader
revert <predecessor>` and flag for morning review rather than leaving a broken
version live.

## Evidence / see also

- [.claude/docs/deploy.md](../../docs/deploy.md) — full ship procedure this
  skill operationalizes (read fully before a first real ship).
- [.claude/docs/algorithm-version-silos.md](../../docs/algorithm-version-silos.md) —
  silo layout, full `trader algorithm` CLI, refinement workflow, research-pack
  detail.
- [/ship-gates](../ship-gates/SKILL.md) — W1-W6 gate mechanics, growth-gate
  tooling, holdout lock, waiver ledger.
- [/find-and-ship-alpha](../find-and-ship-alpha/SKILL.md) Phase 6 — the same
  sequence narrated from the discovery side, with the mining/gating phases
  before it.
- [/portfolio-ops](../portfolio-ops/SKILL.md) — what the live Portfolio does on
  the post-ship re-qualification sweep (GUARD 8).
- [/debug-pipeline](../debug-pipeline/SKILL.md) — scoring-lock drift recovery,
  MySQL zombie queries during the recalc.
- `.claude/docs/scoring-version-integrity.md` — the v60 contamination
  postmortem behind GUARD 2/3 and the scoring-lock guard's dormant-wiring
  caveat.
- v73/v74 precedent (verify current commits/values via `git log --oneline -- ALGORITHM_VERSION`
  and `python trader.py algorithm active` — as of 2026-07 active is v74
  `f9fb7b934`, chain v74 ← v73 `07e9722b5` ← v72 `fc5671200`): both followed
  this exact commit→bump→recalc sequence; v74's ship additionally exposed the
  comparability-unit gap (Step 5) that is now automated.

## Self-update

If you hit a trap this skill missed, append it to GUARDS here AND to
`.claude/docs/traps.md` in the same session.
