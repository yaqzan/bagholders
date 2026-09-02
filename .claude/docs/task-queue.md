# Task Queue — Priority Job Scheduler

A single-node priority scheduler (`task_queue/` package + `trader queue …` CLI) that runs long compute (recalcs, sweeps, MC, research-pack hydration, old-version profile reruns) by priority with resource-aware admission, so heavy jobs stop colliding and saturating CPU + the tight-timeout MySQL DB. Low-priority "fill-in" work runs on a reduced core grant when idle, yields the moment higher-priority work arrives.

Built 2026-06-02 (branch `feat/task-queue`). State lives in **SQLite** (`.cache/task_queue.db`) — never MySQL (the queue exists to *protect* MySQL bandwidth). Core load is governed by injecting the env vars heavy jobs already honor (`TRADER_RECALC_MAX_WORKERS`, `MC_WORKERS`) — zero changes needed in those jobs.

---

## Quick start

```bash
trader queue daemon                 # start the scheduler (foreground; see auto-start below)
trader queue status                 # daemon health + cores/db/io budget vs used
trader queue list                   # active tasks at a glance

trader queue submit --priority low --db heavy --cpu 8 --ttl 24h \
  --dedup sentinel-recalc-v46 --restartable \
  --reason "old-version Sentinel for VersionCompare" \
  -- python tools/build_research_pack.py --version v46 --run-portfolio-windows
trader queue show 7                 # full detail + events + run.log tail
trader queue wait 7 --timeout 3h    # block til done; run with run_in_background=true to be alerted
trader queue kill 7                 # stop a running task (an agent decided it's moot)
```

**Core sizing — see "Core sizing" section below (REVISED 2026-07-29).** `--cpu` on high/critical work is now an admission hint, not a parallelism setting — keep it modest (4-8); on `scheduled`/`normal`/`low` it's the honest worker cap, so a SERIAL job should request 2-3 regardless of db class (`core_grant()` never upsizes — over-requesting just hoards).

`trader queue tick` runs one reconcile pass and exits — a no-daemon cron alternative to the persistent daemon.

**Queued Python commands: never `py -3.11 …`.** The launcher resolves `py` to the pinned `Python311\python.exe` but forwards the launcher-only version flag verbatim, so python.exe gets `-3.11` → instant exit 2, stderr `Unknown option: -3` (task #546, 2026-08-17). Use `python script.py` or the explicit `C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe` path. Full entry: traps.md "Queue launcher resolves `py` but forwards `-3.11`".

---

## CLI reference

| Command | Purpose |
|---|---|
| `submit … -- <cmd>` | queue a command (everything after `--` is the verbatim command) |
| `list [--state S] [--all]` | tasks at a glance (active by default) |
| `status` | daemon health + resource budget/utilization + state counts |
| `show <id> [--lines N]` | full detail + recent events + run.log tail |
| `logs <id> [--lines N] [--stderr]` | tail a task's output |
| `wait <id> [--timeout 3h] [--interval 60s]` | block until terminal, then print result — run with harness `run_in_background` to be alerted (exit 0=done / 1=failed/cancelled / 2=timeout). See "Alerting the agent" below. |
| `kill <id>` / `cancel <id>` | stop a running task / cancel a queued one |
| `priority <id> <tier>` | re-prioritize |
| `hold <id>` / `release <id>` | pause (won't be admitted) / un-hold |
| `clear [--state queued] [--priority low]` | bulk-cancel matching tasks |
| `daemon [--interval N]` | run the persistent scheduler |
| `tick` | one reconcile pass then exit (cron mode) |
| `run-update [--close]` | Model B: route `trader update` through the queue, inline if daemon down |
| `audit [--idle-hours N] [--fix]` | deterministic "what's stale?" scan (no LLM) |

**`submit` flags:** `--priority` (critical/high/scheduled/normal/low/idle), `--cpu N`, `--db none|light|heavy`, `--io none|light|heavy`, `--window any|off_market`, `--restartable`, `--exclusive`, `--dedup KEY`, `--ttl 24h`, `--timeout 2h`, `--not-before 30m`, `--max-attempts N`, `--reason`, `--by`, `--cwd`, `--env KEY=VAL` (repeatable), `--staleness-check '<cmd>'`.

Programmatic API: `from task_queue import api as queue_lib` → `queue_lib.submit([...], priority="low", db="heavy", dedup="…", restartable=True)`.

---

## Priority tiers

`critical(0) → high(10) → scheduled(20) → normal(30) → low(40) → idle(50)` (lower = runs first/preempts). `scheduled` = the `trader update` tier. Low/idle run on a reduced core grant (`floor(cpu/4)`). Queued tasks age (effective priority improves with wait, capped below the next tier) — a low task is never starved forever.

### Choosing a priority — alpha-mining research is high-value

Scoring/alpha-mining research sweeps (ScoreSimulator re-scores, miss-ledger mining, calibration sweeps, candidacy screens) default to `high`, not `low`/`idle`. Generic fire-and-forget housekeeping (cache rebuilds, backfills not needed this turn) stays `low`/`idle`.

Subtlety: `high` (tier 10) sits above `scheduled` (tier 20, `trader update`'s tier), so a `high` job outranks the live update.

| When | What to use | Why |
|---|---|---|
| Past market hours | `high` freely | no update to protect; default for #1-priority alpha work |
| During market hours | `--window off_market`, OR `normal`/`low` (below `scheduled`, update always wins), OR `high` only with `--db light` + short | a `high` heavy-DB job would preempt/contend with the update on tight-timeout MySQL |
| Emergency only | `critical` | outranks even `trader update` — use sparingly |

Re-prioritize a running job when the regime changes (e.g. market just closed): `trader queue priority <id> high`.

### Alerting the agent on completion (`wait` = the bridge)

The queue is not harness-notified — a finishing task doesn't itself wake the agent. Bridge with `trader queue wait <id>` run under the harness `run_in_background` flag: it polls the store internally and exits when the task is terminal, and the harness re-invokes the agent on that exit.

```bash
trader queue submit --priority high --db light --cpu 8 --dedup my-sweep \
  --reason "…" -- python experiments/foo/sweep.py        # -> "queued task #N"
trader queue wait N --timeout 3h                          # run THIS with run_in_background=true
```

`wait` exits 0=done / 1=failed/cancelled / 2=timeout, prints final state + run.log tail. Replaces the older hand-rolled `until … queue show … ; sleep` poll loop. Live glance: `trader queue status` / `trader queue list`.

---

## Resource model

Admission is derived from live task rows each tick (no mutable counters to corrupt on a crash):

- **cores** — budget defaults to `cpu_count − 1`. A grant of `k` injects `TRADER_RECALC_MAX_WORKERS=k` + `MC_WORKERS=k` (and `MC_NO_MP=1` at k=1), charges `k` cores — one number is both worker cap and cores charged. All budgets self-scale from `cpu_count` with env overrides (below) — on the production 9950X3D box (32 threads): **31 cores / low-pri cap 8 / io 16 / db 2**; no daemon code changes needed for the core bump. Scheduled update chain runs at `--cpu 8` (`trader_update_via_queue.ps1` passes to `queue run-update`; close pipeline submits at `--cpu 8` in `post_market_daily.ps1`) — though see "Core sizing" below, the update's real footprint is ~1 core so its wrapper now requests 3.
- **db** — `db_weight` (light=1, heavy=2); budget defaults to 2, so at most one heavy-DB job runs at a time (a recalc opens a worker-pool's worth of MySQL connections) — the MySQL-bandwidth protection. Never raise `TRADER_QUEUE_DB_BUDGET` for more concurrency — cores are the sanctioned lever, concurrent DB holders are not (read_timeout cascade, traps.md).
- **io** — advisory, rarely binds.

Admission is priority-ordered with reservation: a core-starved high-priority task is never leapfrogged by lower-priority work waiting for the same resource.

---

## Core sizing — oversubscribed, the OS arbitrates (revised 2026-07-29)

The daemon self-scales from `os.cpu_count()` (32 threads → 31-core budget, io 16). DB budget stays 2 (MySQL-bound, deliberate) and IS the real concurrency cap — at most two heavy-DB jobs ever run. Boxing each into its own `--cpu` reservation therefore strands capacity (a 4-core request left ~27 idle while nothing else could start), so worker-pool sizing is decoupled from the admission reservation:

| tier | admission reservation | worker pool | effect |
|---|---|---|---|
| `critical` / `high` | its `--cpu` (honest) | the whole 31-core budget | contend freely; OS time-slices |
| `scheduled` / `normal` / `low` | its `--cpu` | its `--cpu` | stays boxed — the update can't hoard |

`resources.worker_grant()` inflates only the pool exported via `core_env` (`TRADER_RECALC_MAX_WORKERS`/`MC_WORKERS`/`TRADER_QUEUE_CORES`); `cpu_grant` stays the honest reservation so `_occupancy` preemption bookkeeping and fairness are untouched. Two oversubscribed heavy jobs time-slice; one that goes briefly serial (I/O wait, single-threaded phase) yields automatically instead of holding idle cores.

Disable via `TRADER_QUEUE_CPU_OVERSUBSCRIBE=0` (reverts to strict reservations). Requires a daemon restart either way — read at startup.

**Request cores you will actually use** even outside high tier: measured 2026-07-29, `trader update` reserved 8 and ran at ~90% of ONE core for 18 minutes (`trader.py:1684` per-stock loop is serial, stranding seven cores) — its wrapper now requests 3.

---

## Preemption (Phase 2, throttle-first)

When a high-priority task is core-starved, the daemon frees CPU by preempting the lowest-priority strictly-lower-priority running task (`TRADER_QUEUE_PREEMPT_MODE`, default `throttle`):

- **throttle** (default) — demote victim's process tree to IDLE priority class + shrink affinity. Reversible, safe even for DB-holders (they keep progressing, releasing MySQL locks). Yields cores in accounting but keeps DB footprint.
- **suspend** (`mode=suspend`) — freeze pure-compute (`db_class=none`) victims for a harder core release. Never a DB-holder — a frozen MySQL lock under the 30s timeout cascades to 2013 errors.
- **kill+requeue** — to reclaim a DB slot for a high-priority heavy-DB task, kill+requeue a restartable lower-priority DB-holder (throttle/suspend don't release a connection). Neutral on `attempts` (can't exhaust `max_attempts`).
- `mode=off` → no live preemption; starved high-pri task just waits.

Throttled/suspended tasks restore when cores free, and un-freeze on daemon restart (nothing stuck across a crash; admission re-decides).

---

## `trader update` integration (Model B)

Windows Task Scheduler stays the timekeeper; its action runs `trader queue run-update`, which:

- daemon healthy → enqueues update at `scheduled` priority (`--exclusive --dedup trader-update`, not restartable so alerts never double-send). Daemon runs it, preempting low-priority work.
- daemon down → runs `trader update` inline — today's behavior unchanged, so the trading-day pipeline never depends on the queue being up.

A `Global\TraderUpdate` named mutex in `trader.py:_run_update_command` prevents any two updates (or update + recalc launched the same way) from overlapping — independent of the queue. Optional daemon watchdog: `TRADER_QUEUE_UPDATE_WATCH_MIN` alerts if an expected update never arrives during market hours (catches a broken Task Scheduler entry).

Install the routed Task Scheduler entry: `scripts\install_trader_update_via_queue.ps1` (wrapper: `scripts\trader_update_via_queue.ps1`).

---

## Research priority + maintenance mode

**RESEARCH_PRIORITY (piloted 2026-07-29):** `.cache/RESEARCH_PRIORITY` (read by `queue_daemon.ps1`) makes research/cross-agent compute outrank the scheduled `trader update` — market guard OFF, no core reserved as update headroom, low-pri cap raised. `trader update` still runs at tier `scheduled` (20); it just no longer preempts `high` (10) work, so tier ordering alone puts research first. DB budget stays 2 and is NOT part of this flag — it's a MySQL-capacity limit, not an update reservation; raising it recovers nothing and reopens the read_timeout cascade (traps.md). Delete the flag + restart the daemon to revert.

**Maintenance mode** (`scripts/maintenance_mode.py --pause|--status|--resume`) pauses the trading-day pipeline for a full-box repair; implies the research-priority budgets. Works via repo-level guards on the wrapper scripts keyed on `.cache/MAINTENANCE_MODE` (`schtasks /change` needs elevation not obtainable non-interactively). `queue_daemon.ps1` reads the same flag to lift the market guard and update-headroom reservations. Resume = `--resume` + restart the daemon.

---

## Self-maintenance & audit (cheap, no LLM)

- Always-on, deterministic: TTL expiry + dedup (partial-unique index) + `max_attempts`/backoff + per-task `timeout` + a throttled reaper running bounded `staleness_check` predicates and pruning terminal tasks + artifacts past `TRADER_QUEUE_RETENTION_DAYS` (default 7).
- `staleness_check` convention: a command run while queued — exit 0 = work no longer needed → auto-cancel; non-zero/timeout/error = keep (fail-safe). Runs `shell=False`, bounded by a timeout. Backstops "agent forgot to remove a now-moot task."
- On-demand, deterministic: `trader queue audit` surfaces long-idle, now-stale, duplicate, failed tasks (`--fix` request-cancels idle/stale). Run when wanted — never per tick, so token/compute cost stays zero.

---

## Crash safety

- `(pid, create_time)` identity on every liveness/kill/preempt check (Windows recycles PIDs fast — a bare PID is never trusted).
- Two-phase launch + attempt token: a launch intent (`state=launching`) commits before spawn; on reconcile a crashed launch is adopted via `launch.json` or safely requeued — never double-executed.
- Single-daemon guard: a Windows named mutex + a `daemon_singleton` SQLite compare-and-set. CLI writes intents (cancel/kill/priority/hold); the daemon is sole authoritative writer of state/pid.
- Each task runs under a supervisor (`task_queue.launcher`) that writes the `.codex/runs/<id>/` contract and finalizes in a `finally`, so a graceful exit records instantly even if the daemon is down.

---

## Ops / config

Auto-start the daemon (at logon + restart on failure): `scripts\install_queue_daemon.ps1` (wrapper: `scripts\queue_daemon.ps1`).

Env knobs (all optional): `TRADER_QUEUE_DB` (state path; tests), `TRADER_QUEUE_CORE_BUDGET`, `TRADER_QUEUE_DB_BUDGET`, `TRADER_QUEUE_IO_BUDGET`, `TRADER_QUEUE_LOW_CORE_CAP`, `TRADER_QUEUE_PREEMPT_MODE`, `TRADER_QUEUE_MARKET_OPEN`/`_CLOSE` (off_market window), `TRADER_QUEUE_UPDATE_WATCH_MIN`, `TRADER_QUEUE_UPDATE_DEDUP`, `TRADER_QUEUE_RETENTION_DAYS`, `TRADER_QUEUE_STALENESS_TIMEOUT_S`, `TRADER_QUEUE_CPU_OVERSUBSCRIBE` (0 to disable oversubscription).

Dependency: `psutil>=5.9,<6` (`task_queue/requirements.txt`). Tests: `python tests/test_task_queue.py [--integration]`.

---

## Files

| File | Role |
|---|---|
| `task_queue/model.py` | Task dataclass, priority tiers, states, resource classes, tuning constants |
| `task_queue/store.py` | SQLite store (schema, CRUD, intents, dedup index, derived resource accounting, daemon singleton) |
| `task_queue/winproc.py` | Windows named mutex + `(pid,create_time)` identity + tree-kill + suspend/resume primitives |
| `task_queue/launcher.py` | supervisor that writes the `.codex/runs/<id>/` contract + finalizes |
| `task_queue/resources.py` | machine budget, core-grant env injection, admission fit, time windows, preempt mode |
| `task_queue/preempt.py` | tree-walk throttle/restore/suspend/resume (Phase 2) |
| `task_queue/daemon.py` | reconcile/admission/preemption loop, single-daemon guard, orphan adoption, watchdog, reaper |
| `task_queue/reaper.py` | staleness predicates, terminal-task pruning, audit scan |
| `task_queue/api.py` | `queue_lib` programmatic API |
| `queue_manager.py` | `trader queue …` CLI |
| `scripts/{queue_daemon,install_queue_daemon}.ps1` | daemon auto-start |
| `scripts/{trader_update_via_queue,install_trader_update_via_queue}.ps1` | Model-B update routing |

## Measured new-box wall-clocks (2026-07-29, 9950X3D — supersedes estimate-based guidance)

Full table + provenance caveats: `experiments/newbox_rebaseline/RUNTIME_TABLE.md`. Headlines: full 10y recalc+assess **38m** and 30y **66m** — both **excluding `--dte`, which is ~85% of total runtime** (10y projects ~6.7h with it) — budget `--dte` explicitly. MC E-tier certificate (16 cells, N=2000/1000) ~10min/arm at cpu 10; N=1000 matrices 7-46min; `trader update` is SERIAL (~1 core, ~18min, cpu request now 3).
