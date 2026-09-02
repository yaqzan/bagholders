# Staging Recalc via Git Worktrees — Runbook

**Status: groundwork in place, NOT YET ACTIVE.** Multiple parquet-backed experiments currently run against the production cache. Adopt only after those finish and a cutover is scheduled.

Goal: ship a scoring change without a dashboard outage. Staging recalculates under the new algorithm version while production keeps serving the old one; promotion is a single git operation, atomic from the API's perspective.

## When to use

- A scoring change requiring full `trader recalculate --force --full` (~25 min), during which production would otherwise serve blank/partial data (API pins to `ALGORITHM_VERSION`, new version has no rows yet for un-processed stocks).
- NOT needed for portfolio-stage changes (don't bump `ALGORITHM_VERSION`, don't require recalc).

## How it works

Each git worktree has its own dir, its own `ALGORITHM_VERSION` file, its own `database/utils/scoring.py`. Both connect to the same MySQL DB. `score` table keyed `(symbol, date, version_id)` so version rows partition cleanly.

| | Production worktree | Staging worktree |
|---|---|---|
| Path | `c:\Development\Trader` | `c:\Development\Trader-staging` |
| Branch | `main` | `algo-vNN` |
| `ALGORITHM_VERSION` | old commit | new commit |
| `database/utils/scoring.py` | old formula | new formula |
| `trader update` cron | runs (old version_id) | optional (new version_id) |
| `.cache/` | own copy (gitignored) | own copy |

`config` imports from `C:\Development\Archivist\archiver\config.py` (outside the trader repo) — DB credentials shared automatically, no `.env` copying needed.

## Setup (one-time)

```bash
cd c:/Development/Trader
git worktree add ../Trader-staging -b algo-vNN
cd ../Trader-staging
# fresh branch off main with its own copy of every tracked file;
# untracked files (.cache/, logs) are NOT copied.
```

Verify config resolves the same external path:
```bash
cd c:/Development/Trader-staging
python -c "import config; print(config.__file__)"
# Expected: C:\Development\Archivist\archiver\config.py
```

## Workflow

**1. Make the scoring change in staging:**
```bash
cd c:/Development/Trader-staging
# edit database/utils/scoring.py, run experiments, etc.
git add database/utils/scoring.py database/models/core.py simulator.py
git commit -m "vNN scoring: ..."
git rev-parse --short HEAD > ALGORITHM_VERSION
git add ALGORITHM_VERSION && git commit -m "Bump ALGORITHM_VERSION to $(git rev-parse --short HEAD~)"
```
Production's `ALGORITHM_VERSION` is untouched — its API and cron keep serving the old version.

**2. Run recalc from staging:**
```bash
cd c:/Development/Trader-staging
python trader.py recalculate --force --full
```
Writes scores under the new version_id; production is unaffected. ~25 min. During this window: production API serves old version (no degradation); production `trader update` cron keeps writing today's scores under the old version_id. Staging's own cron is NOT recommended during the recalc itself (collides with the recalc's writes for today) — enable only after recalc completes, before promote, to keep staging's "today" row fresh.

**3. Verify coverage before promote:**
```bash
cd c:/Development/Trader-staging
python staging_recalc.py verify   # or: verify <commit>
```
Confirms: every production stock has >=1 score row under the new version; latest production date has a row for every active stock; no date gaps in lookback; `ScoreAssessmentRun` has rows for the new version (one per window per DTE); `BacktestTemporalStats` populated.

**4. Refresh same-day staleness:** if recalc finished hours ago and production has kept writing `today` under the old version, top up right before promote:
```bash
python trader.py recalculate --force 1   # today only, ~30 sec
```

**5. Promote:**
```bash
cd c:/Development/Trader-staging && git push origin algo-vNN
cd c:/Development/Trader && git fetch
git merge --ff-only origin/algo-vNN
# single commit advances ALGORITHM_VERSION old->new; API now pins to new
# version, whose rows are already populated (verified step 3) — no outage.
```
The merge IS the cutover — atomic, milliseconds.

**6. Post-promote:**
```bash
cd c:/Development/Trader
python tests/test_strategy_config_drift.py
PYTHONIOENCODING=utf-8 python trader.py temporal-refresh
# update docs: known-issues.md CURRENT SHIP STATE, version-history.md, etc.
```

## Rollback

```bash
cd c:/Development/Trader
git revert -m 1 <merge-commit>   # restores old ALGORITHM_VERSION
# Old version's score rows remain in the DB (version-keyed) —
# API immediately serves them again, no recalc needed.
```

## Caveats

**Schema changes must be additive only during the staging window.** A staging commit adding a column to `score` appears in the shared MySQL DB the moment staging's `ensure_schema()` runs — production on the old commit ignores extra columns (Peewee tolerates them) fine. Renaming/removing a column production reads/writes breaks production immediately.
- Additive only (new column/index/table) -> OK
- Rename/drop/type-change -> NOT OK during staging window; use a multi-step migration (add on commit B, dual-write on C, drop on D) or apply at promote time with a brief production stop.

**Same-day staleness during recalc.** Production's cron keeps writing today's scores under the old version_id while staging recalcs; if staging finishes at 11:00 and promote happens at 14:00, three hours of production updates have no matching new-version row. Fix: step 4, or run staging's cron for the gap window.

**Cache isolation.** `.cache/barrier_outcomes.db` (~10 GB) is keyed `(symbol, date, side, K, M)`, not version — both worktrees can read it; writes are safe as long as K/M barriers don't differ (they come from git-tracked `strategy_config.py`, so they shouldn't). Per-experiment parquets at `.cache/<exp>/*.parquet` are version-tagged (e.g. `calls_v46_1825.parquet`) — each worktree builds its own, no contention.

**MySQL connection contention.** Both worktrees query the same DB. `barrier_outcomes.db` is local-disk SQLite so not the issue, but heavy MySQL load from staging recalc can slow production API. The 30s read/write timeout (`database/trader_database.py:25-26`) guards against indefinite hangs; if production sees 2013 errors during staging recalc, lower worker count: `trader recalculate --force --full --workers 4`.

**Two API processes (optional):** preview the new version before promote by starting a second Flask API from staging on a different port — it reads staging's `ALGORITHM_VERSION`, serves the new version. Production API on the original port is untouched.
```bash
cd c:/Development/Trader-staging
FLASK_RUN_PORT=5001 python api.py
```

**Cleanup after promote** (and a few days confidence):
```bash
cd c:/Development/Trader
git worktree remove ../Trader-staging
git branch -d algo-vNN
# old version's score rows can stay indefinitely (free rollback path),
# or clean up later: DELETE FROM scores WHERE version_id = <old_id>;
# (keep the AlgorithmVersion row even if scores are deleted, so historical
# logs/commit references still resolve)
```

## What this does NOT solve

- Bugs in the new scoring formula — the verify step (3) is the only quality gate; bring the per-trade gate evidence (H1-H5, assessment-backtest.md) BEFORE the merge.
- Long recalcs (days, not minutes) — same-day staleness becomes unmanageable. Don't staging-ship anything taking >24h to recalculate.
- `barrier_outcomes.db` rebuilds — rare (only K/M barrier changes), but production experiments using the shared cache see inconsistent data mid-rebuild. Schedule barrier-cache rebuilds off-hours, separate from staging ships.
