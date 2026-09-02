---
name: debug-pipeline
description: Symptom-indexed ops runbook for the score/update/queue/portfolio pipeline — today's scores missing or partial, `trader update` blocked by a scoring-lock/ScoringVersionMismatch error, MySQL 2013 read-timeout zombie-query cascades, task-queue daemon down/stale-heartbeat/livelocked, the scheduled post-market close pipeline failing, stale regime/breadth, the Flask API serving stale config after an edit, noisy GitNexus stale-index warnings, and live-Portfolio push notifications arriving late. Use when the user reports "scores didn't run," "update failed," "MySQL keeps timing out," "the queue looks stuck," "close pipeline didn't finish," "dashboard shows old numbers," or any variant of "X is broken, why?" for this pipeline.
---

# /debug-pipeline — symptom-indexed ops runbook

This is a **triage index**, not a deep-dive. Each symptom below: check → fix →
verify, with the exact command to run. For architecture background read
[task-queue.md](../../docs/task-queue.md); for score-formula forensics
(intraday swings, fakeouts) use `/debug-scores` instead — this skill is about
the *pipeline* (did the job run, is it stuck, is the data fresh), not *why a
score moved*.

## GUARDS (read before touching anything)

1. **Never blind-retry a timing-out query.** A client-side `read_timeout` kills
   the CLIENT only — MySQL keeps executing server-side. Each retry stacks
   another zombie query until disk IO saturates and even trivial indexed
   queries start failing. Always `SHOW FULL PROCESSLIST` first, `KILL` the
   stack, THEN `EXPLAIN` the query that's slow. See §3.
2. **The harness's own background runner is NOT the queue.** If you're
   debugging "why did two heavy jobs collide on MySQL," check whether either
   was launched via a raw `run_in_background`/detached process instead of
   `trader queue submit` — that bypasses CPU/DB admission entirely and the
   daemon will happily admit a `scheduled` `trader update` on top of it.
3. **Restarting `trader-api` MUST run backgrounded** or it hangs the agent
   foreground forever (a hidden persistent child holds the shell's stdout
   pipe). See §7.
4. **GitNexus stale-index warnings after a local commit are EXPECTED, not a
   bug.** Do not chase them with `npx gitnexus analyze` before a push. See §8.
5. **A scoring-lock (`ScoringVersionMismatch`) block is a symptom, not
   necessarily a code bug** — it fires whenever live scoring config drifts from
   the recorded fingerprint for the active version, including from an
   intentional-but-unlocked change. Diagnose which side is wrong before
   "fixing" it by disabling the guard. See §2.
6. **"Queue CLI hangs" may be a wedged DRIVE, not the daemon** (2026-07-29). A dead
   volume (e.g. the D: backup target) hangs drive enumeration, and pwsh STARTUP
   enumerates drives — so every PowerShell-launched `trader queue ...` "hangs" while
   the daemon is perfectly healthy. Before daemon surgery: check liveness via Bash
   `tasklist` and heartbeat via direct `python.exe trader.py queue status` (Git Bash).
   If `Get-Date` hangs in a fresh PowerShell, the shell path is the casualty. Do NOT
   keep probing the wedged drive (each Test-Path/CIM probe parks another unkillable
   process against the mount manager). `trader queue kill` on a task wedged in dead-
   drive I/O still frees budget accounting even if the process can't die yet. See
   traps.md "A wedged drive masquerades as a queue-daemon livelock".
7. **Don't diagnose the close pipeline as one monolithic job.** Since
   2026-06-29 `post_market_daily.ps1` runs 3 INDEPENDENT phases (scores+
   portfolio, options pull, derived tail) — a phase-1 crash does NOT skip
   phases 2/3 anymore. Check *which* phase failed, not just "did it fail." See
   §6. **This 3-phase architecture is currently an UNCOMMITTED working-tree
   edit** (`git status` shows `scripts/post_market_daily.ps1` modified vs
   HEAD as of 2026-07) — a `git checkout`, `git clean`, `git stash`, or branch
   reset would silently revert it to the old 2-phase throw-on-crash version.
   Verify with `git diff HEAD -- scripts/post_market_daily.ps1` before
   relying on phase-independence, and commit the file if it's meant to be the
   standing behavior.
8. **A "hung" close-pipeline task showing ~0 CPU and an empty run.log is
   usually NEITHER hung nor log-less.** The queue-tracked pid is the
   `task_queue.launcher` python — a waiter that idles at seconds of CPU
   across an hours-long healthy run — and the task-level
   `.codex/runs/task_<id>_*/run.log` is empty by design; the worker logs to
   `.codex/runs/post_market_daily_<ts>/run.log` (UTF-16). Read that dir's
   `status.json` for the live phase and sample the LEAF python's CPU before
   declaring a hang. See §6 "False-hang pattern."

---

## 1. Today's scores missing or partial

**Check:** confirm which symbols are actually missing and why, don't guess.

```bash
python trader.py score-missing            # recovers all symbols missing today's
                                           # score, or whose score PREDATES a later
                                           # price re-pull (pulled_at > Score.updated_at + 5min)
python trader.py score-missing AAPL MSFT   # limit to specific symbols
```

If `trader update` itself didn't run at all (not just partial), check the
queue first — the scheduled update is queue-routed (Model B):

```bash
python trader.py queue status             # daemon [OK]/[DOWN]? was `trader-update`
                                           # admitted, or still queued?
python trader.py queue list --all --limit 20
```

If the daemon was down at the scheduled time, `trader queue run-update` falls
back to running `trader update` inline automatically (zero-regression design)
— so "queue was down" alone does not explain a missing update; look for an
actual process crash or a `Global\TraderUpdate` named-mutex collision (two
updates/recalcs launched the same way can't overlap by design — one silently
no-ops).

**Fix:** `trader update` for today's session, or a targeted recalc if the gap
is historical:

```bash
python trader.py update                        # today only, options OFF — this is
                                                 # itself the ~45-min full-universe
                                                 # SCHEDULED-tier job (task_queue/model.py),
                                                 # not a light check — see queue note below
python trader.py recalculate 1d                 # today's rows under the current version
python trader.py recalculate SYM_A SYM_B 30d     # targeted backfill for specific symbols
```

`trader update` and anything beyond a same-day fix are full-universe jobs —
**queue them** (`trader queue submit --priority high --db heavy --cpu N --
python trader.py update` / `... recalculate ...`), never run raw, especially
during market hours. See [queue-ops](../queue-ops/SKILL.md).

**Verify:** `python trader.py explain-scores SYM 1` (2-line verdict:
CORRECT/BAD_LUCK/MISS/PENDING) or hit `GET /api/stocks/<sym>` and confirm
today's date + version.

---

## 2. `trader update`/`recalculate` blocked with a scoring-lock error

**Symptom:** a write is refused with `ScoringVersionMismatch: Refusing score
write... live scoring config/formula != the lock for AlgorithmVersion vNN`.
This is `database/scoring_version_guard.py`'s write-time integrity check — it
fires whenever the live resolved `strategy_config.SCORING` + the effective
`database/utils/scoring.py` constants + `CALIBRATION_CUTOFF_DATE` don't
sha256-match the fingerprint captured for the currently-active version's
commit. Common trigger: toggling `CALIBRATION_CUTOFF_DATE` (or any locked
`SCORING` constant) without re-running `capture_lock` afterward.

**Check** which side actually drifted (the error names it — "resolved SCORING
config" vs "scoring.py formula") and whether the drift was intentional:

```bash
python tools/check_scoring_version_integrity.py   # no flags; asserts live config
                                                   # matches the recorded lock
```

**Fix** — if the drift is intentional (you meant to change the constant and
this version's config really is now different), re-capture the lock in the
SAME commit as the change:

```python
from database.scoring_version_guard import capture_lock
from database.models.core import AlgorithmVersion
capture_lock(AlgorithmVersion.get_or_create_current(), note="why")
```
then re-run `check_scoring_version_integrity.py` to confirm, and commit
`algorithm_versions/scoring_locks.json` alongside the change. **The `trader
algorithm lock-scoring` CLI verb the runtime error message references does
NOT exist** — `capture_lock` is a Python call, not a CLI subcommand (as of
2026-07; verify against `algorithm_versions/manager.py`'s subcommand table in
[queue-ops](../queue-ops/SKILL.md) / cli reference if this changes).

If the drift is UNINTENTIONAL (you didn't mean to change scoring behavior),
restore the config instead of re-locking — re-locking a genuine regression
bakes the bug in as "the new normal" for this version.

**Escape hatches** (ship-path only, know what you're doing): `TRADER_DEFINE_VERSION=1`
(intentionally redefining a version — always pair with a fresh `capture_lock`
afterward) or `TRADER_SCORING_GUARD_DISABLE=1` (emergency bypass, logged when
used). Neither belongs in routine debugging.

**Verify:** re-run the blocked write (`recalculate`/`update`) and confirm it
no longer raises.

---

## 3. MySQL 2013 read-timeout "zombie query" cascade

**Symptom:** repeating `2013, 'Lost connection to MySQL server during query'`
or read-timeout errors, sometimes even for indexed sub-second queries. Root
cause: a client-side `read_timeout` (`database/trader_database.py`) kills the
CLIENT only — the query keeps running server-side. Each blind retry stacks
another zombie, and enough zombies saturate disk IO until *everything* times
out, including trivial queries.

**Check — in this exact order, never skip to a retry:**

```sql
SHOW FULL PROCESSLIST;   -- find stacked/duplicate long-running queries
```
```sql
KILL <id>;               -- kill every stacked copy of the offending query BEFORE re-testing
```
```sql
EXPLAIN <the actual slow query>;   -- confirm it's using the expected index
```

House doctrine: **>30s = missing index or a bad query plan, never "raise
`read_timeout`."** Full incident writeup + the peewee FK-per-row lookalike
trap (loop looks "stuck" at low CPU, invisible in PROCESSLIST): see
[references/deep-dives.md §3](references/deep-dives.md#3-deep-dive--mysql-zombie-query-cascade-full-incident).

**Fix:** kill zombies, restructure the offending query (split `OR` into
`AND`-ranges each mapping onto an index; never JOIN+OR+ORDER BY in one
statement), re-test.

**Verify:** re-run the original command; `SHOW FULL PROCESSLIST` should show
no stacked duplicates, and the query should complete well under the timeout.

---

## 4. Task-queue daemon down / stale heartbeat / suspected livelock

**Check:**
```bash
python trader.py queue status
```
Look for `daemon: [OK] ...` vs `daemon: [DOWN] <reason> pid=... heartbeat=...
ago`. A transient stale heartbeat (tens of seconds) during a heavy tick can be
noise; a heartbeat stuck growing for minutes, or `[DOWN]` with no pid, means
the daemon process actually died.

**Fix — daemon down:**
```bash
python trader.py queue daemon           # foreground; or use the installed
                                         # scheduled-task wrapper scripts/queue_daemon.ps1
python trader.py queue tick             # one reconcile pass + exit — cron-mode
                                         # fallback if you don't want a persistent daemon
```
Restarting the daemon process does **not** kill its running children — the
new instance adopts them on its next tick. If you need to kill the daemon's
OWN process specifically (not its task tree), target its pid directly — do
not tree-kill. Full restart semantics, the historical livelock class (now
believed closed — treat recurrence as a fresh regression, not "the known
bug"), and the manual orphan-reap recipe (hold the task BEFORE killing its
process tree, or the daemon respawns it):
[references/deep-dives.md §4](references/deep-dives.md#4-deep-dive--task-queue-daemon-internals-relevant-to-debugging).

If you see repeated kill+requeue cycles on the same task id, check
`python trader.py queue show <id>`'s event log before assuming it's a bug —
it may be genuine resource contention.

**Verify:** `python trader.py queue status` shows `[OK]` with a fresh
heartbeat, and `python trader.py queue list` shows tasks actually progressing
(RUNTIME increasing, not stuck).

---

## 5. The harness ran heavy compute directly instead of through the queue

**Symptom:** `trader queue status` shows near-zero cores/db used, yet the
machine is visibly saturated (a `ScoreSimulator` sweep, `recalculate`,
`assess`, Monte Carlo, or research-pack build launched via a raw background
process instead of `trader queue submit`). The daemon has no visibility into
that load, so it will admit a `scheduled` `trader update` — or another
queued job — on top of it, and both collide on the tight-timeout MySQL
connection pool.

**Fix:** there is no way to retroactively "adopt" an already-running raw job
into the queue's accounting. Let it finish (or kill it), then re-submit
properly next time:
```bash
python trader.py queue submit --priority high --db heavy --cpu 4 \
  --dedup <stable-key> --reason "<why>" -- <the actual command>
```
See [queue-ops](../queue-ops/SKILL.md) for the full flag reference and
priority-tier table. The rule has no exceptions for "I'm actively watching
this turn" — a full-universe `ScoreSimulator` capturing-simulate over years of
history is a bulk MySQL load regardless of who's watching it.

**Verify:** `python trader.py queue status` shows the job's cores/db charged
against budget while it runs.

---

## 6. Scheduled post-market close pipeline failed or ran late

`scripts/post_market_daily.ps1` is the Task Scheduler entry (`\Stock Daily
Close Update`). Since 2026-06-29 it's **3 independent phases**, each run and
recorded regardless of the others' outcome (fixed specifically because a
Phase-1 crash used to `throw` and starve Phase 2/3 for the whole day):

| Phase | Command | Purpose |
|---|---|---|
| 1 | `trader.py update` (options OFF) | scores + live-Portfolio confirms — this is what fires the buy/sell push notifications minutes after close |
| 2 | `trader.py pull-options` | options-chain pull for the dashboard — independent of phase 1's success |
| 3 | `trader.py recalculate --tail-only --tail-window 5y` | derived tail: assess + temporal refresh |

**Check which phase(s) failed** — don't assume "the close pipeline broke" as
one unit:

```bash
python trader.py queue show <id>   # if daemon-healthy this ran as ONE
                                    # scheduled/exclusive/db=heavy job
                                    # (dedup=trader-close); check its run.log
```
The worker writes `.codex/runs/post_market_daily_<timestamp>/status.json`
(`phase` + `state`) and a `failed.json`/`done.json` with the collected
per-phase failure list (e.g. `"scores+portfolio (exit 1)"`, `"pull-options
(exit 1)"`, `"derived-tail (exit 1)"` — named after the actual Invoke-Phase
labels in the script, not a generic "phaseN") if any phase failed — read that
file directly if the queue task's own log is unhelpful. The script exits 1 if
**any** phase failed even though others may have succeeded, so a nonzero exit
code alone doesn't tell you which phase broke.

**False-hang pattern (2026-08-10, task #411):** the hang-hunter's first three
signals all mislead on this job. (1) The queue-tracked pid is the
`task_queue.launcher` python — a WAITER at ~3s CPU across an hours-long
healthy run; the real work is two levels down (launcher → the
`post_market_daily.ps1 -Worker` powershell → a `trader.py <phase>` python).
Walk the tree and sample the LEAF python's CPU twice, seconds apart. (2)
`trader queue logs <id>` / the task-level run.log are empty BY DESIGN — the
worker logs to `.codex/runs/post_market_daily_<ts>/run.log`, whose path is in
the adjacent `status.json` (`run_log` field, plus live `phase` +
`updated_at` — the single fastest health check). (3) That run.log is UTF-16;
byte tools render mojibake — use PowerShell `Get-Content`. Calibration:
phase 2 (pull-options) legitimately runs for HOURS at ~30% of one core, and a
healthy run's PROCESSLIST shows one fast per-symbol query ("Sending to
client"), not a stack (§3).

**Fix — rerun just the failed phase(s), not the whole pipeline:**
```bash
python trader.py update              # phase 1 — scores + portfolio sync
python trader.py pull-options        # phase 2 — options chain
python trader.py recalculate --tail-only --tail-window 5y   # phase 3 — assess/temporal tail
```
Phase 1 (`trader update`) is itself the ~45-min full-universe scheduled job
(task_queue/model.py's SCHEDULED-tier comment; `queue run-update`'s 45m
timeout default) — queue it (`trader queue submit --priority high --db heavy
-- python trader.py update`) rather than running it raw, the same as phase 3's
5y tail recalc. The standing rule in [queue-ops](../queue-ops/SKILL.md)
applies to all three; only a genuinely light check (a single-symbol
`explain-scores`, not a phase rerun) qualifies for direct foreground
execution.

**Verify:** `GET /api/portfolio/state` reflects a fresh sync timestamp;
`GET /api/stocks/<sym>` shows today's score; dashboard options data is
current.

---

## 7. Regime/breadth stale, or dashboard shows a value that doesn't match
   what you just computed

**Check** the last-updated dates:
```bash
GET /api/market/regime      # latest MarketRegime row
GET /api/market/breadth     # latest MarketBreadth row (?days=N for history)
```

**Fix — backfill gaps** (both take a positional day count, default 365; a
non-numeric arg is silently ignored and the default is used — don't pass a
flag-shaped token expecting an error):
```bash
python trader.py breadth-backfill [days]   # process oldest-to-newest (EMA chains need order)
python trader.py regime-backfill [days]
```

**Regime timing subtlety:** `trader update` scores each stock using the
LAST-AVAILABLE `MarketRegime` row (never an implicit 1.0 premarket
multiplier), then AFTER all stocks are scored, computes the fresh regime for
today and calls `reapply_regime_today(regime_multiplier, regime_composite,
target_date)` to patch every one of today's `Score` rows atomically with the
now-known regime state. If you're diagnosing "why does today's score not
reflect today's VIX/breadth," confirm this reapply step actually ran (it logs
to `ScoreIntradayLog` with `source='regime_reapply_today'` — see
[debug-scores](../debug-scores/SKILL.md) for reading that log) rather than
assuming the regime pipeline is broken.

**Fix — API/dashboard serving stale config after a code edit** (`api.py`,
`strategy_config.py`, `backtest_cascade.py`, `portfolio_param_manifest.py`,
or anything they import): the Flask backend must be restarted, and **this
MUST run backgrounded** — foreground hangs the agent indefinitely (a hidden
persistent child holds the shell's stdout pipe), and `cmd.exe /c
"server.bat ..."` via Bash silently no-ops (prints a banner, exits 0, old
code stays loaded). Use the PowerShell tool with `run_in_background: true`:
```powershell
& C:\Development\server.bat restart -Service trader-api
```
The frontend (`:3000`) hot-reloads on its own — no restart needed for React
changes.

**Verify:** `GET http://127.0.0.1:5000/health` returns 200 after the restart;
re-hit the endpoint you were debugging and confirm the new values/behavior.

---

## 8. GitNexus reports a stale index / "run analyze"

**This is expected noise after a local commit, not a bug to fix.** Per
CLAUDE.md's GitNexus block: `npx gitnexus analyze` only after a **successful
push**, when explicitly asked, or when a stale index actually blocks the
query you're trying to run right now. Do not run it reflexively after every
commit/merge.

**Loop-break rule** (also don't skip this): after you DO run
`npx gitnexus analyze`, check `git diff --name-only` — if every changed file
is in `{AGENTS.md, CLAUDE.md, .gitnexus/}` (i.e. only the auto-generated
symbol-count block changed), **stop**: do not commit those files, do not
re-run analyze again. A stale index right after a local commit resolves
naturally on the next push.

**Note if you're reading CLAUDE.md directly:** it currently carries **two**
GitNexus auto-generated blocks (a known artifact of the doc-restructure), one
saying "run analyze if stale" and a later one saying "only after a successful
push... do not run after local commits/merges/stale-index warnings unless
explicitly asked." **The LATER block's policy is authoritative** — post-push
only.

---

## 9. Live-Portfolio buy/sell push notifications arrive very late

**Symptom:** the expected 15:25-16:00 ET buy push (or a sell/exit push) shows
up hours late — e.g. after the options pull finishes instead of right after
close.

**Root cause (fixed 2026-06-18):** the old `close-update` ran scores +
options pull + portfolio sync as ONE pass, so a slow multi-hour options pull
delayed the portfolio confirms that depend only on scores. Phase 1 of
`post_market_daily.ps1` is now `trader.py update` (options OFF) specifically
so the portfolio engine — which prices options with its own closed-form
model and reads nothing from `option_prices` — syncs and fires pushes
immediately after scoring, independent of the (separate) options pull. If
late pushes recur, check whether phase 1 or phase 2 (see §6) is the one that
ran slow before assuming this regressed.

**Separate, still-open timing nuance:** the portfolio engine's ~16:00 ET
session gate can fill on a score that the close-update/options-pull path
later rewrites (a fill-vs-rewrite race on the same trading day) — this is a
known open item, not something you can fully "fix" by rerunning; if you hit
it, treat it as expected and check
[portfolio-ops](../portfolio-ops/SKILL.md) for the current status before
spending time on it.

**Verify:** `GET /api/portfolio/pending` reflects would-open/would-exit state
promptly after a `trader update` run; check the push actually fired via
`trader portfolio notify` output or the Pushover history.

---

## Self-update

If you hit a trap this skill missed, append it here (GUARDS + the relevant
numbered section) AND to [.claude/docs/traps.md](../../docs/traps.md) in the
same session — don't let a fresh discovery evaporate at session end.

## Evidence / see also

- [.claude/docs/task-queue.md](../../docs/task-queue.md) — full queue architecture, resource model, preemption
- [.claude/docs/traps.md](../../docs/traps.md) — the canonical trap registry (this skill's traps are a curated subset)
- `/queue-ops` — the full `trader queue` command surface + priority-tier decision table
- `/debug-scores` — score-formula forensics (intraday swings, fakeout families) as opposed to pipeline health
- `/portfolio-ops` — live Portfolio tracker operations, execution timing canon, known fill-race items
- `database/scoring_version_guard.py` — the scoring-lock mechanism (§2), read its module docstring for the full "why a byte fingerprint is wrong" rationale
- `scripts/post_market_daily.ps1` — the close-pipeline source (§6), read its header comments for the phase-independence rationale
