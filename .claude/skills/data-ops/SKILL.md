---
name: data-ops
description: Data lifecycle operations — recalculate (score backfill/refresh), rebuild-parquets, barrier-cache (SQLite/DuckDB), the bulk_cache parquet pattern for experiments, breadth/regime backfills, historic-update, version purge, and the options-pull phase. Use when the user asks to backfill scores, refresh indicators/components after a formula change, rebuild an experiment's parquet cache, speed up a barrier-touch sweep, reclaim MySQL disk from retired versions, or asks "why is my data stale / missing / slow".
---

# /data-ops — Data lifecycle: recalculate, parquet caches, barrier cache, backfills, purge

Everything that moves bulk data through this repo without changing the scoring
*formula* itself: backfilling/refreshing `Score` rows, building experiment
parquet caches, the barrier-touch cache (SQLite canonical + DuckDB mirror),
breadth/regime/historic-peaks backfills, and reclaiming MySQL disk from
retired algorithm versions. If you're changing the scoring formula, this skill
gets you the data-refresh mechanics; the ship sequencing itself is
`/ship-version`.

**All heavy operations below are `trader queue submit` candidates** — see
[task-queue.md](../../docs/task-queue.md) / `/queue-ops`. Nothing in this
skill should be run raw in the foreground except the tiny 1-day/smoke-sized
invocations explicitly marked as such.

## GUARDS

1. **`recalculate`'s default scope is DAILY components only, missing-only, 5y
   — not "everything."** No flags = `DAILY_COMPONENTS = {'trend', 'bb', 'rsi',
   'macd', 'stoch', 'ta', 'overall'}` (`recalculate_scores.py`), NOT `weekly`
   or `dte`. `ALL_MODES = DAILY_COMPONENTS | {'weekly', 'dte'}` only runs with
   `--all` (~92% extra runtime — reserve for once-per-ship, not routine
   backfills). If a symbol's weekly/DTE fields look stale after a "full"
   recalc, you probably ran without `--all`.
2. **`--score-versions vNN` silently implies `--force`.** Passing extra
   sidecar versions to compute recalculates them regardless of missing-only
   state (it prints a note when it auto-flips `--force` on). Never pass an
   **older** (pre-cadence) version here from a candidate/worktree checkout —
   shared component builders can corrupt that version's already-shipped rows
   (see GUARD 6). This is a writer op, not a read-only comparison.
3. **`rebuild-parquets` freshness check is a filename convention, not a
   content diff.** An experiment is "fresh" (skipped) iff its `.cache/<exp>/`
   directory already contains **any** file matching `*_v{active_id}_*.parquet`
   — it does not re-check that the parquet's actual content matches the
   current formula. After a silent scoring-adjacent change that doesn't bump
   `ALGORITHM_VERSION` (rare, but possible for input-only recomputes), use
   `--all` to force-rebuild rather than trusting the tag.
4. **Never edit engine/config files while a queued sweep that subprocess-
   imports them is running.** Windows multiprocessing re-imports the module
   per worker; a mid-sweep edit leaves a worker importing half-written state
   and `pool.map` hangs forever (cost a prior sweep ~5h). Let the queued job
   finish or explicitly kill it first.
5. **Peewee FK access in bulk loops = one SELECT per row.** `row.symbol` on a
   `Score`/`PriceHistory` row is a lazy `DeferredForeignKey` — looping ~190k
   rows fires ~190k MySQL roundtrips (invisible in `SHOW PROCESSLIST`, each
   sub-ms, but the loop stalls for tens of minutes at ~25% of one core). Use
   `row.symbol_id` (the raw varchar key) in any bulk loop, or `.tuples()`.
6. **Pre-v69 score rows are gone; the cadence floor is a hardcoded class
   constant, not an env var.** 145M pre-honest-era rows were purged
   (`tools/purge_score_versions.py --below 69 --swap`, 2026-06-12). Every
   version `>= AlgorithmVersion.CADENCE_MIN_VERSION_ID` (currently **69**,
   `database/models/core.py`) auto-joins the scoring cadence; there is no
   `CADENCE_MIN_VERSION_ID` env var to set — don't go looking for one. Never
   re-add a pre-69 id to any cadence/backfill list. Research on retired
   pre-69 versions goes through `.cache/algorithm_versions/vNN/research_pack/`
   packs, not raw rows (they no longer exist).
7. **Barrier-cache backend is env-selected; the DuckDB mirror auto-rebuilds
   after every writer call, but only those three.** `BARRIER_CACHE_BACKEND`
   (`auto`/`duck`/`sqlite`, default `auto` = DuckDB-if-present) picks which one
   `peaks_to_swing_results` reads. SQLite (`.cache/barrier_outcomes.db`) is the
   canonical writer; DuckDB (`.cache/barrier_outcomes.duckdb`) is a read-only
   mirror. `backfill()` / `backfill_sets()` / `refresh_recent()` — the only
   writer paths — each call `rebuild_duck_mirror()` automatically right after
   writing (wrapped in try/except that only skips it if `duckdb` is
   unavailable or errors; `database/barrier_cache.py` lines ~454-459 /
   ~562-566, and its module docstring: mirror "rebuilt on every
   `refresh_recent()`"). `python -m database.barrier_cache rebuild-duck` is the
   manual/recovery command for when that auto-rebuild was skipped, or after a
   raw/out-of-band SQLite write that bypassed those three functions — not a
   routine step after every backfill.
8. **The deep-backfill scores (1995→2026) exist but the assessment/research-
   pack windows historically capped at 10y** — as of this writing (2026-07)
   `assess_scores.py`'s `WINDOWS` list and `research_pack.py`'s
   `PortfolioWindow`s have uncommitted edits adding 15y/20y/30y and named
   dot-com/GFC/`deep_1995_now` windows (verify these are still present —
   `git status` / `git diff` on both files — before assuming they're live;
   they may since have been committed or reverted). **Every deep-window claim
   carries a survivorship caveat**: only ~278-391 of today's ~2000-symbol
   universe have 1990s-era data, so pre-2000s crash DD reads optimistic until
   the delisted-equity data buy lands (see
   [data-acquisition.md](../../docs/data-acquisition.md)).

## 1. `trader recalculate` — the decision table

Look up current behavior with `python trader.py recalculate --help`-equivalent
(there is no `--help`; read the flag table below, sourced from
`trader.py:4792-5076` and `recalculate_scores.py`) before assuming a default.

| Flag | Effect | Default when omitted |
|---|---|---|
| `[SYM]` | One symbol (uppercased) | all symbols |
| `[lookback]` | `Nd`/`Nw`/`Ny` or bare int days | **5y** (`_DEFAULT_YEARS = 5`) |
| `--force` | Recompute even where a score already exists (vs. missing-only) | off |
| `--full` | Extends default window to **10y** (`_FULL_YEARS = 10`) | off |
| `--auto` | Market-hours aware: 10y off-hours, 5y in/near market (2h pre-open buffer); explicit `--full` overrides | off |
| `--all` | Every component (`ALL_MODES`: daily + weekly + dte), not just daily | off — GUARD 1 |
| `--scores-only` | Skip the historic/assessment/temporal tail after recompute | off |
| `--tail-only` | Run ONLY the tail, skip score recompute (mutually exclusive with `--scores-only`) | off |
| `--tail-window N` (alias `--tail-from`) | Limits which of the 5 fixed assessment windows (`[365,730,1095,1825,3650]`) run in the tail, filtered to `≤N` days | all 5 |
| `--reuse-components-from vNN` (alias `--reuse-from`) | Formula-only ships: reuse another version's already-computed components; narrows scope to `{'overall'}` if no explicit mode given | none |
| `--score-versions vNN,vMM` (aliases `--versions`, `--score-version`) | Backfill sidecar comparison versions — **implies `--force`** (GUARD 2) | none |
| `--rebuild-parquets` | Chains a parquet rebuild after the tail (~5-15 min extra) | off |
| `--workers N` | Worker pool size | auto `min(cpu_count, 8)` |
| `--no-db-preflight` | Suppresses the DB-tuning hint print | preflight on |

**Market-hours ship order** (the sequence CLAUDE.md and `/ship-version` both
reference): after an `ALGORITHM_VERSION` bump, run `trader recalculate 1d`
first (today's dashboard rows exist under the new version fast), then
`trader recalculate --force` (5y backfill), then off-hours
`trader recalculate --force --full` (10y). Reserve `--all` for once
post-ship, not routine reruns — it's ~92% more runtime for weekly+DTE fields
most day-to-day work doesn't touch.

`recalculate` is also invocable as `backfill` (deprecated alias, prints a
yellow warning, identical behavior).

**Before writing score rows** (unless `--tail-only`), a scoring-version
integrity guard (`database.scoring_version_guard.verify_active_before_write`)
runs and can abort the whole recalc if the live config doesn't match the
active version's lock. Override via `TRADER_DEFINE_VERSION=1` only when
intentionally redefining a version (this is a ship-path escape hatch, not a
routine flag — see the scoring-lock trap below).

```bash
# Standard queued backfill after a formula-neutral input recompute.
# --window off_market guards against a high+db-heavy job preempting/preceding
# the scheduled `trader update` during RTH — drop it only if you've confirmed
# it's after market close (see the market-hours priority table in /queue-ops).
trader queue submit --priority high --db heavy --cpu 6 --restartable \
  --window off_market --dedup recalc-5y --reason "backfill after X" -- \
  python trader.py recalculate --force
```

### Scoring-lock cutoff drift (a `recalculate` failure mode, not a recalc bug)

If `trader recalculate` refuses every score write with "Refusing score write"
/ `ScoringVersionMismatch`, you (or a worktree agent) likely toggled
`CALIBRATION_CUTOFF_DATE` or another locked `SCORING` constant without
re-running the lock capture. Fix:
```python
from database.scoring_version_guard import capture_lock
from database.models.core import AlgorithmVersion
capture_lock(AlgorithmVersion.get_or_create_current(), note="...")
```
then `python tools/check_scoring_version_integrity.py` (exit 0 = match), then
commit `algorithm_versions/scoring_locks.json` **in the same commit** as
whatever intentional change caused the drift. There is no
`trader algorithm lock-scoring` CLI verb — despite what an old error message
may reference — use the `capture_lock` import directly.

## 2. `rebuild-parquets` — standalone or chained

```bash
trader rebuild-parquets                 # rebuild every stale experiment
trader rebuild-parquets --exp weekly_volume   # one experiment only
trader rebuild-parquets --all           # force-rebuild even if fresh (GUARD 3)
```

Runs each `experiments/*/build_features.py` as a subprocess with
`PYTHONIOENCODING=utf-8` forced. Skip logic (GUARD 3): an experiment is
"fresh" iff `.cache/<exp>/` contains any `*_v{active_id}_*.parquet` file,
where `{active_id}` is `AlgorithmVersion.get_active_scores_version().id`. Use
this after a version bump so downstream sweeps see v{N} data immediately —
either standalone or chained via `trader recalculate --rebuild-parquets`.

## 3. Barrier cache (SQLite canonical + DuckDB mirror)

`database/barrier_cache.py` is the barrier-touch outcome cache backing
assessment and most Stage-1/Stage-2 sweeps (`peaks_to_swing_results`).

- **Canonical writer**: SQLite `.cache/barrier_outcomes.db`, keyed
  `(symbol, date, side, K, M)`.
- **Read mirror**: DuckDB `.cache/barrier_outcomes.duckdb` — ~50-100x faster
  on bulk JOIN-style reads. Rebuild manually:
  ```bash
  python -m database.barrier_cache rebuild-duck
  ```
- **Backend selection**: `BARRIER_CACHE_BACKEND` env var, one of
  `auto` (default — DuckDB if the mirror file exists and is usable, else
  SQLite) / `duck` (force) / `sqlite` (force). Set `sqlite` to bypass a stale
  or corrupt mirror without rebuilding it.
- **Ad-hoc K/M parameters not pre-cached**: `database/barrier_walk_numba.py`
  — a Numba-JIT forward walk (`walk_one_put`/analogous call variant),
  ~50-100x faster than the interpreted fallback, API-compatible with the
  inner `_walk_outcome` in `barrier_cache.py`. Use this when a sweep tests
  barrier shapes (K, M, W) outside the pre-cached default barrier set instead
  of falling back to slow interpreted walks.
- **Nightly auto-rebuild**: the barrier cache refreshes its recent window on
  its own cadence; `refresh_recent(days=160)` is the incremental refresh path
  — note this only covers ~5.5 months, so a cohort analysis against
  "whatever's in the cache right now" is directional only. Backfill the full
  5y window (`backfill(lookback_days=1825)` / `backfill_sets(...)`) before
  any ship-gate decision that needs long history.

## 4. `bulk_cache` — the on-demand parquet pattern for new experiments

`database/bulk_cache.py` formalizes "pull from MySQL once, cache to parquet,
sweep variants in memory thereafter" — NOT a global nightly DB snapshot (the
`scores`/`PriceHistory` tables are tens of GB; most experiments need a narrow
slice).

```python
from database.bulk_cache import materialize_polars, chunked_query_by_year

def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id={vid} AND date BETWEEN '{y_start}' AND '{y_end}' "
        "AND overall >= 70",
        start_year=2016, end_year=2027, vid=74,
    )
    return pl.DataFrame(rows, schema=['symbol', 'date', 'overall'])

parquet_path = materialize_polars('my_experiment_peaks_v74_10y', _build)
```

- `cache_path(name)` resolves under `.cache/experiment_data/<name>.parquet` —
  `name` must be a flat identifier (no `/`).
- `materialize_polars(name, build_fn, deps=(), force=False)` skips `build_fn`
  entirely on a fresh cache hit (`is_fresh`: path exists AND is newer than
  every existing dep). No cache is auto-invalidated on a version bump —
  **bake the version into the name** (`mcap_v74_5y`-style) so a new version
  gets a new cache path instead of silently reading stale rows.
- Stale parquets are **not garbage-collected** by the module — delete by hand
  when re-running with different parameters under the same name.
- Query via `pl.read_parquet(path)` or `duckdb.read_parquet(str(path))`
  depending on how the sweep is structured.

This is the pattern every new experiment build script (`build_features.py`
under `experiments/<name>/`) should follow — it's also what `rebuild-parquets`
(§2) invokes per experiment.

## 5. Breadth / regime / historic-peaks backfills

```bash
trader breadth-backfill [days]     # default 365; oldest-to-newest (EMA chains need order)
trader regime-backfill [days]      # default 365
trader historic-update [window] [--intraday]   # default window = 365 (historic_peaks.WINDOW_DAYS)
```

- Both backfill commands take a bare positional int days count (default
  **365** each); a non-numeric argument is silently ignored (falls back to
  the default) — there is no error on a typo here, so double-check the value
  landed if the run finished suspiciously fast.
- `historic_peaks` is a denormalized, fully-rebuilt-each-run cache (truncate +
  insert) — rebuild it after **any** score modification, not just a version
  ship. `trader update` already includes it; a manual `historic-update` is for
  out-of-band recalcs.
- `--intraday` (`allow_intraday=True`) is real but undocumented in CLAUDE.md's
  CLI table — pass it when historic-peaks needs to reflect intraday score
  swings, not just end-of-day.
- Regime reapply timing: `trader update` scores using the **last-available**
  `MarketRegime` row (never an implicit 1.0 premarket multiplier); a fresh
  regime is computed only after all stocks are scored, then
  `reapply_regime_today()` patches today's rows atomically. A regime/breadth
  backfill run mid-day will not retroactively change already-applied
  multipliers on other symbols scored earlier that same run.

## 6. Version purge — reclaiming MySQL disk

```bash
python tools/purge_score_versions.py --below 69 --dry-run    # preview only
python tools/purge_score_versions.py --below 69 --optimize   # delete + reclaim disk
python tools/purge_score_versions.py --below N --swap        # copy-swap instead of DELETE
```

- `--below N` is **required** (int) — deletes/swaps `scores` and
  `dte_recommendations` rows for algorithm versions strictly below `N`.
  `AlgorithmVersion` rows themselves, assessment rows, and temporal stats are
  **kept** — only the bulk per-`(symbol, date)` rows go.
- `--swap` (copy survivors into a clone table, atomic rename, drop old
  tablespace) is the mass-deletion-safe path — at high deletion ratios, a
  plain `DELETE` on the interleaved clustered PK `(symbol, date, version_id)`
  crawls (documented ~1,500 rows/s ≈ hours) and can mis-plan into a zombie
  query. `--optimize` runs online-DDL table rebuild after a windowed DELETE
  to actually return disk (InnoDB doesn't reclaim space on DELETE alone).
- Uses a **dedicated long-timeout connection** distinct from the project's
  default 30s `read_timeout` — this is intentional (bulk deletes are
  multi-second by design); don't "fix" a slow purge by lowering its timeout.
- Never re-add a version below `AlgorithmVersion.CADENCE_MIN_VERSION_ID`
  (currently 69) to any cadence list after a purge — the rows are gone;
  research those versions via their research pack, not a re-backfill.
- Always run via the queue, off-market (`--priority` per the market-hours
  table in `/queue-ops`), never in the foreground — this is a multi-minute
  MySQL-heavy op by design.

## 7. Options pull — `trader close-update` / `pull-options`

- `trader update` (the scheduled scoring pass) never pulls options
  (`with_options=False`).
- `trader close-update` is the same pipeline with `with_options=True` —
  it's the post-close job that also pulls the options chain.
- `trader pull-options` is the standalone options-only refresh
  (`client.update_stocks(full=False, with_options=True)`) — use this to
  re-pull options without re-running the whole scoring pass.
- The scheduled post-market pipeline (`scripts/post_market_daily.ps1`) splits
  this into **independent phases** (as of a recent uncommitted edit — verify
  the script is still 3-phase before relying on this): Phase 1 `trader.py
  update` (scores, options OFF, fast), Phase 2 `trader.py pull-options`
  (independent of phase 1's outcome), Phase 3 the derived tail
  (`recalculate --tail-only --tail-window 5y`). A failure in one phase does
  not block the others — see `/debug-pipeline` for the failure-mode runbook.

## Evidence / see also

- [task-queue.md](../../docs/task-queue.md) / `/queue-ops` — how to actually
  submit every op above instead of running it raw.
- [CLAUDE.md](../../../CLAUDE.md) Architecture table — `database/barrier_cache.py`,
  `database/barrier_walk_numba.py`, `database/bulk_cache.py` one-liners.
- `/ship-version` — where `recalculate`'s market-hours ship order slots into
  the full scoring-ship sequence (bump-before-recalc ordering, silo
  checkpoints).
- `/debug-pipeline` — MySQL zombie-query symptom (read_timeout doesn't stop
  a server-side query; `SHOW PROCESSLIST` + `KILL`, never blind-retry), and
  the post-market pipeline failure-mode table.
- [data-acquisition.md](../../docs/data-acquisition.md) — why deep-backfill
  crash-DD numbers are survivor-only and what unblocks it.
- `.claude/docs/traps.md` — the full trap registry this skill's GUARDS draw
  from.

## Self-update

If you hit a data-lifecycle trap this skill missed — a new recalculate flag
interaction, a barrier-cache staleness surprise, a purge edge case — append
it to GUARDS above **and** to `.claude/docs/traps.md` in the same session.
