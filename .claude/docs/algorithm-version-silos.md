# Algorithm Version Silos

Tracked scoring snapshots live under `algorithm_versions/`; large generated datasets/sweep caches under `.cache/algorithm_versions/<key>/`. Bridges "git history is the only algorithm archive" to explicit local snapshots that can be compared, promoted, and eventually loaded directly.

## Layout

Flat shipped-key layout:

```text
algorithm_versions/
  registry.json
  portfolio_profiles.json
  v58/
    manifest.json
    scoring/
    scoring_config.json
    portfolio_snapshot.json
    portfolio_sources/
    evidence_manifest.json
  portfolio/
    <snapshot-key>.json
```

`registry.json`/`vNN/` are scoring-algorithm version surfaces. Portfolio risk profiles (Sentinel/Core/Apex) are a separate post-score layer tracked by `portfolio_profiles.json` — comparable against any score version, never write different `Score.overall` rows, never occupy future `ALGORITHM_VERSION` identifiers.

No `algorithm_versions/shipped/` yet — `vNN/` is already the shipped/DB-linked key. Non-DB candidates use `algorithm_versions/candidates/<name>/`.

Large artifacts:

```text
.cache/algorithm_versions/v58/
  datasets/
  experiment_caches/
  checksums.json
```

`evidence_manifest.json` (tracked) records cache paths, commands, checksums, evidence summaries. Never commit large parquet/SQLite/CSV cache outputs into `algorithm_versions/`.

## CLI

```bash
trader algorithm active
trader algorithm list
trader algorithm snapshot-staging --key candidate-name --label short-label
trader algorithm snapshot-current --status ship_candidate --create-db-version
trader algorithm snapshot-git-ref --version v58 --key v58 --status shipped
trader algorithm backfill-git-snapshots --force
trader algorithm rebuild-index
trader algorithm document-snapshots
trader algorithm promote-candidate vNN --status shipped
trader algorithm diff v57 v58
trader algorithm cache-root v58

trader portfolio-snapshot current --key pNN-or-label
trader portfolio-snapshot list
trader portfolio-snapshot diff old new
```

`snapshot-current`/`snapshot-staging` copy the current worktree state (correct for a staging/candidate branch). `snapshot-git-ref` copies from the commit recorded on the selected `AlgorithmVersion` row — preferred for shipped `vNN/` silos when the working tree is dirty or has moved on. `backfill-git-snapshots` only copies code + writes manifests; it does not recalculate scores, rebuild assessments, or hydrate experiment caches.

## Refinement Workflow

Scoring refinement starts in an isolated worktree/branch by default, seeded from the current production/staging baseline, then becomes that agent's own sandbox for scoring edits, sims, sweeps, caches, candidate snapshots. Editing `database/utils/scoring.py`, `strategy_config.SCORING`, and scoring-adjacent files is allowed there — staging code, not the live serving contract.

Rules:
- Keep live/serving `main` stable unless the user explicitly asks otherwise.
- Each algorithm-refinement agent uses its own branch/worktree by default.
- Do not run shared production score writers (`trader update`, `trader recalculate`) from a scoring sandbox against active production version rows unless explicitly performing the elevation/ship process from the designated ship checkout — recalc belongs after the version bump in deploy, not the refinement loop.
- May continue on a stale base if another agent ships first; pull/merge + resolve conflicts before elevating.
- Before elevation: `trader algorithm snapshot-staging`.
- Promotion to ship candidate requires normal scoring evidence + a silo snapshot.
- After the version bump creates/resolves an `AlgorithmVersion` row: `trader algorithm snapshot-git-ref --status ship_candidate --create-db-version` so `vNN/` is copied from the DB row's commit.
- Mark shipped only after recalc + validation pass: `trader algorithm promote-candidate vNN --status shipped`.
- No env-gated research knobs in shipped scoring files — sandbox branches may edit the real scoring path directly, but the final shipped silo must represent the actual scoring code path, not a hidden flag state.

## Staging-Native Sweeps

Simulation/sweep work in refinement worktrees runs against the staging checkout path by default:

```bash
python trader.py simulate --compare --assess
python experiments/<candidate>/run_sweep.py
```

Use `ScoreSimulator(scoring_fn=None)` or import `database.utils.scoring` from the checkout so the measured formula matches what gets snapshotted. Experiment scripts own cache building, candidate parameter enumeration, ranking, artifact writing — not the only place the candidate formula exists once viable.

Legacy `ScoreSimulator(scoring_fn=variant_fn)` and runtime monkey patches remain useful for quick probes. Before W1-W6 validation, move winning logic into the real staging scoring path, rerun validation through it, then snapshot with `trader algorithm snapshot-staging`.

Staging-native sweeps are still no-write validation — never run `trader update`/`trader recalculate` from ordinary scoring worktrees; those are deploy/elevate/ship steps after the version bump.

## Runtime Loader Status

Current: `trader update`/`trader recalculate` still import the checkout code. Silo tooling does not block update calls when checkout code doesn't match a silo fingerprint.

`ALGORITHM_VERSION` as a commit pointer stays compatible with the DB model but cannot alone prove the running checkout matches the silo snapshot — the silo manifest records commit, dirty state, copied files, fingerprints for a future stricter guard.

Target loader phase: `ALGORITHM_VERSION` still resolves the DB row → row/manifest points to `algorithm_versions/vNN/` → scoring engine can load the selected silo, compare fingerprints, fail/warn on divergence from the checkout/staging code.

Do not flip runtime scoring to silo-loaded code until parity tests prove the active silo produces the same rows as the current checkout path.

## Multi-Version Update Runtime

```bash
trader update --score-versions v57,v58
trader update --score-version v57 --score-version v56
```

Normal update behavior unchanged when the flag is absent. With it: yfinance pulled once, indicators calculated once, active/current score computed first, then component scores copied into additional `Score` rows and `overall` recomputed per requested version's siloed `compute_overall_score`.

Boundary lives in `score_calculation_service.py`. `trader.py` parses explicit CLI flags (+`TRADER_SCORE_VERSIONS`), builds a `ScoreCalculationService`, delegates current + multi-version scoring to it.

When runtime-loadable versions are validated, the update path writes sidecar versions inline during the live daily score calc (current + requested silo engines share live inputs, no re-fetch). If a caller disables validation, the service falls back to the older post-score path for tests/ad-hoc use.

Runtime limitations (first hook):
- Requested versions need runtime-loadable silos with `strategy_config.py` + `database/utils/scoring.py`.
- Uses the current checkout's component-score calcs + context loaders, swaps only the siloed overall-score engine — good for recent versions/side-by-side daily comparisons, not a full old-code replay for early versions whose component logic lived in different files.
- Active current version is skipped if included (normal update already wrote it).

`trader recalculate` shares the sidecar write path for historical backfills:

```bash
trader recalculate --force --full --score-versions v57,v58
```

Shares the current batched data load, component fields, regime/breadth context, volume context across requested engines, recomputes `overall` per siloed engine. Use for recent multi-algorithm comparisons — not a full old-code replay when a version's component calculators/upstream feature builders differed.

**Hard safety boundary:** never run this sidecar writer from a staging/candidate checkout against previously shipped versions — a `v59` checkout can still supply component/context builders while writing rows tagged `v57`, corrupting the historical version even though `version_id` looks right. Sidecar writes are allowed only when every requested version is explicitly runtime-loaded from a validated silo and the command intentionally produces same-context comparison rows. For repair/canonical evidence/production history, use the target version's own pointer checkout/silo and recalculate only that target. For read-only research, build research packs from existing rows instead of running score writers.

## Efficient Multi-Version Update Design (planned)

Regular update can extend to compute several algorithms in one pass:
1. Pull yfinance once.
2. Calculate daily/weekly indicators once.
3. Build per-symbol context once (prices, weekly scores, earnings dates, regime maps, breadth maps, volume inputs).
4. Load N scoring engines from selected silos/worktree modules.
5. Per score date, evaluate each engine against the same context.
6. Batch upsert score rows under each engine's `AlgorithmVersion.id`.

Data pull + indicator/context construction is the expensive part; `compute_overall_score` is cheap enough to run N times in memory.

Caveat: older scoring files use module-level constants/imports — multi-version injection should load each silo under a unique module namespace, or isolate engines by subprocess if globals collide. First production-grade implementation should start with two versions + a small symbol/date parity set before broad integration.

## Backfill Follow-Up

Git-history code snapshots are cheap, captured in working-tree manifests. Recalculated score rows, assessment rows, parquet caches, sweep datasets are the expensive part — wait for off-hours.

```bash
trader algorithm backfill-git-snapshots --force
```

Walks `AlgorithmVersion` rows descending, creates `vNN/` from each row's git commit, records `legacy_metadata_only` snapshots for unresolved refs (e.g. `baseline`), writes `diff_from_previous.json` per version, rebuilds:

```text
algorithm_versions/snapshot_index.json
algorithm_versions/SNAPSHOT_INDEX.md
algorithm_versions/VERSION_GUIDE.md
.claude/docs/algorithm-version-index.md
algorithm_versions/vNN/README.md
```

Early versions may show `missing: true` file entries or `scoring_config.extraction_error` when source-of-truth files didn't exist yet — still a valid git-history snapshot, just not a structured config snapshot. Attach experiment/cache evidence by manifest path + checksum later, never by committing large artifacts.

Run `trader algorithm document-snapshots` after a git-snapshot backfill or adding a new shipped silo — regenerates each `vNN/README.md`, `algorithm_versions/VERSION_GUIDE.md`, and `.claude/docs/algorithm-version-index.md`.

## Post-Recalc Research Packs

Reusable post-recalc surface for cross-version research — does not recalculate scores, gathers what's needed once score rows/assessments exist: score coverage + 5y/10y readiness; persisted assessment coverage for WR/TP rows; 5y WR15 utility + horizon utility by side/bucket; temporal backtest summary + monthly/yearly calendar readiness; named deterministic portfolio windows (`covid_crash_2020` = `2020-01-01`–`2020-04-30`, `covid_cycle_2020_2021`, `2020_now`, `22_now`); parquet/cache manifest + top-level `manifest.json`.

Build one pack after recalc + assessment:

```bash
python tools/build_research_pack.py --version v58 --run-portfolio-windows
```

Hydrate many historical versions when the DB already has score rows:

```bash
python tools/backfill_research_packs.py --min-version 39 --max-version 66 --force-assess --run-portfolio-windows --detach
```

Use `--force-assess` only when score rows were newly backfilled/changed; otherwise prefer missing-only assessment. Detached runner follows the Codex long-task artifact contract: `pid.txt`, `run.log`, `status.json`, terminal `done.json`/`failed.json`.

Output root:

```text
.cache/algorithm_versions/vNN/research_pack/
  manifest.json
  score_coverage.json
  assessment_coverage.json
  utility_5y_wr15.json
  utility_5y_wr7.json   # legacy compatibility artifact
  utility_by_horizon.json
  temporal_summary.json
  stress_windows.json
  parquet_manifest.json
```

Before a retro comparison, check whether a pack already exists: `ls .cache/algorithm_versions/` for present version dirs, check each for `research_pack/manifest.json`.

Assessment calendar consumes the same pack data via `/api/backtest/temporal?version=vNN&profile=sentinel`. Temporal rows key by `dte_strategy` + `portfolio_profile`; research-pack `temporal_summary.json` records both so Sentinel/Core/Apex rows don't collapse into one ambiguous summary. `stress_windows.json` used only when it declares the same portfolio profile — unprofiled legacy packs must not override a profile-toggle view. Missing/partial packs render waiting/read-only, never crash the page.

## Version-History Split Follow-Up

`.claude/docs/version-history.md` should become an index + per-version log files, e.g.:

```text
.claude/docs/version-history.md
.claude/docs/version-history/v58.md
.claude/docs/version-history/v57.md
```

Separate docs-cleanup task — don't mix with runtime/silo plumbing.
