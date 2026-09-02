---
name: queue-ops
description: Everything about `trader queue` — the always-on priority job scheduler that admits, runs, and preempts long-running compute (recalcs, sweeps, Monte Carlo, research packs). Covers submit flags, priority tiers and the market-hours HIGH-floor, --db/--cpu semantics, the wait/exit-code completion bridge, kill vs cancel vs hold/release, daemon health/restart, and audit. Use when the user asks to queue a recalc/sweep/MC run, wants to check queue status, needs to kill or re-prioritize a task, is debugging why a queued job never started, or says anything like "queue this", "what's running", "is the daemon up", "wait for task N".
---

# /queue-ops — run and manage long compute through `trader queue`

A supervised task-queue daemon is **always running** on this box, backed by SQLite
(`.cache/task_queue.db`, never MySQL). It admits work by priority with CPU/DB-aware
budgets so nothing collides with the scheduled `trader update` or with MySQL's
tight timeouts. **Any compute that takes minutes+ or hammers CPU/DB goes through
this — never run it raw, and never via the harness's own background-process flag**
(that bypasses admission entirely — see GUARD 1).

Read: [task-queue.md](../../docs/task-queue.md) (full runbook — Quick start / CLI
reference / Priority tiers / Resource model / Preemption / `trader update`
integration / Self-maintenance / Crash safety / Ops-config / Files), CLAUDE.md
"Long-Running Compute" section, `queue_manager.py` (argparse tree,
`build_queue_parser` ~line 401), `task_queue/daemon.py` (admission/preemption),
`task_queue/model.py` (`Priority` enum, aging).

## GUARDS (read before you submit anything)

1. **The harness's `run_in_background` flag is NOT the queue.** Launching a recalc/
   sweep/MC job via Bash/PowerShell's background flag (or any raw detached
   process) bypasses the queue's CPU/DB admission entirely — the daemon then
   thinks those cores are free and can admit a `scheduled` `trader update` on top
   of your job, colliding on MySQL. "I'm actively watching it this turn" does
   **not** exempt a job — actively-waited compute still goes through
   `trader queue submit`, just at `--priority high`. The only things that run raw
   are genuinely light foreground checks: seconds-to-~1min, no full-universe
   MySQL scan, and never `ScoreSimulator`/`recalculate`/`assess`/Monte
   Carlo/research-pack/parquet-cache builds — those are *always* queued, even
   read-only, even from a worktree.

2. **`high` (10) is NOT always safe during market hours.** `daemon.py`'s
   `_market_floor_base()` (the RTH market-hours guard) floors any `HIGH`-tier task
   down to `NORMAL` (30) during regular trading hours — *unless* `--window
   off_market` is set or the guard env-flag is off. This is the mechanism, not
   just a policy note: a plain `trader queue submit --priority high ...` typed at
   10am silently runs at effective priority 30 and can no longer preempt or
   outrank `scheduled` (the `trader update` tier, 20). See "Priority tiers" below
   for what to do instead. **CAUTION — this exact guard is currently an
   uncommitted edit** in this working tree (`task_queue/daemon.py`, +44 lines,
   `TRADER_QUEUE_MARKET_GUARD` default ON) — it only takes effect once the
   `TraderQueueDaemon` process is stopped and restarted (the running daemon
   process still has the old code loaded in memory). Run `trader queue status`
   and check the daemon's start time / `git status --short task_queue/daemon.py`
   before assuming this guard is live; if it's uncommitted and the daemon hasn't
   been bounced since the edit landed, don't rely on it — treat `high` during RTH
   as if it could genuinely outrank `scheduled` and pick `--window off_market` or
   `normal`/`low` explicitly instead.

3. **Alpha-mining sweeps default to `high`, not `low`.** Per CLAUDE.md and
   `task-queue.md`: this is high-value interactive research, not fire-and-forget
   housekeeping. Past market hours, use `high` freely. During market hours, either
   add `--window off_market`, or drop to `normal`/`low` (both sort below
   `scheduled` so the update always wins), or keep `high` only if it's `--db
   light` and short (see GUARD 2 for why "high" isn't automatically the escape
   hatch you'd think). Reserve `critical` for genuine emergencies — it's the one
   tier that outranks `trader update` unconditionally.

4. **Worktrees have their own EMPTY queue DB.** The queue DB path is
   `.cache/task_queue.db` resolved relative to the process's cwd (or
   `TRADER_QUEUE_DB` env override) — a git worktree checkout is a different
   directory with its own `.cache/`, so tasks submitted from inside a worktree
   land in a queue nobody's daemon is watching (unless you also pointed
   `TRADER_QUEUE_DB` at the shared file). **Always submit from the MAIN
   checkout** (`C:\Development\Trader`), even when the work itself was developed
   in an experiment worktree. Pass `--cwd` to point the *job* at the worktree if
   it needs to run from there.

5. **`--exclusive` only blocks a 2nd active submit if `--dedup` matches.** Without
   an explicit `--dedup`, `--exclusive` derives the key from the literal command
   string (`"cmd:" + joined argv`) — change any flag/arg and it's a different key,
   so two near-identical jobs (e.g. different `--version`) will both run. Give
   sweeps and recurring jobs an explicit, stable `--dedup <key>` you control.

6. **`PYTHONIOENCODING=utf-8` for queued python that prints unicode.** The queue's
   own launcher already sets this for every child, but if your command is itself
   a wrapper (PowerShell calling python calling a subprocess), verify unicode
   output isn't getting cp1252-mangled/blinding your `run.log` — pass
   `--env PYTHONIOENCODING=utf-8` explicitly if output looks truncated or garbled.

7. **Editing engine/config files while a queued sweep is running.** If your queued
   job subprocess-imports scoring/engine code (Windows multiprocessing
   re-imports per worker), do not edit those files mid-run — a worker can die on
   the mid-edit import state and `pool.map` hangs forever with no error. Let the
   queued job finish (or kill it) before touching files it imports.

---

## 1. Submit — the canonical template

```bash
trader queue submit --priority <tier> --db <none|light|heavy> --cpu N \
  [--restartable] [--exclusive] --dedup <stable-key> --reason "<why>" \
  [--ttl 24h] [--timeout 2h] [--not-before 30m] [--env KEY=VAL] \
  -- <your command...>
```

Full flag reference (all confirmed against `queue_manager.py:build_queue_parser`):

| Flag | Default | Notes |
|---|---|---|
| `--priority` / `-p` | `normal` | `critical\|high\|scheduled\|normal\|low\|idle` (aliases: `crit`, `hi`, `sched`, `norm`/`default`; a bare int also works, snapped to the nearest tier) |
| `--cpu` | `1` | cores requested; clamped to the machine's core budget; low/idle-tier requests are additionally capped (see Resource model) |
| `--db` | `none` | `none\|light\|heavy` — MySQL load class |
| `--io` | `none` | `none\|light\|heavy` — mirrors `--db` for disk/network-bound work |
| `--window` | `any` | `any\|off_market` — `off_market` excludes the task from admission entirely until RTH ends, regardless of resources |
| `--restartable` | off | idempotent; safe for the daemon to auto-requeue after an infra failure |
| `--exclusive` | off | only one active task per dedup key; **derives the key from the command text if `--dedup` is omitted** (GUARD 5) |
| `--dedup KEY` | none | a 2nd active submit under the same key is a no-op (prints "deduped: active task #N already exists...") |
| `--ttl` | none | cancel if still queued past this duration (e.g. `24h`) |
| `--timeout` | none | wall-clock job ceiling (e.g. `2h`) |
| `--not-before` | none | delay start by a duration (e.g. `30m`) |
| `--max-attempts` | `3` | matches `DEFAULT_MAX_ATTEMPTS` |
| `--reason` | none | shown at a glance in `list`/`show` |
| `--by` | none | who queued it (agent/session id) |
| `--cwd` | project root | working directory for the launched command |
| `--env KEY=VAL` | none | repeatable — one `--env` per var |
| `--staleness-check '<cmd>'` | none | shlex-split command run while still queued; exit 0 = no longer needed → auto-cancel; non-zero/timeout/error = keep (fail-safe) |
| trailing `-- <command>` | **required** | everything after the literal `--`; omitting it prints an error + example and exits 2 |

Example (matches `queue_manager.py`'s own usage hint verbatim):

```bash
trader queue submit --priority low -- python tools/build_research_pack.py --version v46
```

On success it prints `queued task #<id>  priority=<tier>  cpu=<n>  db=<class>  cmd: <preview>`.
If the daemon isn't running, submit still succeeds (the task sits `QUEUED`) but it
also prints a note: `queue daemon is not running (...). Start it with 'trader queue
daemon' (or run 'trader queue tick').` — don't ignore that line; nothing will run
until the daemon (or a `tick`) picks it up.

## 2. Priority tiers — exact ranks and the market-hours floor

```
CRITICAL = 0   HIGH = 10   SCHEDULED = 20   NORMAL = 30   LOW = 40   IDLE = 50
```
Lower number = higher priority = runs first = preempts higher numbers.
`SCHEDULED` (20) is the tier `trader update`/`close-update` run at via Model B —
CLAUDE.md's "high, but not so high" framing. Aging: a queued task's *effective*
priority improves by 1 point per 10 min waited, capped at 9 — it can approach but
never cross into the tier above its base (anti-starvation, not a tier
redefinition).

**Choosing a tier:**
- Past market hours → `high` freely for alpha-mining/research work (nothing to
  protect from).
- During market hours (RTH) → don't let a heavy job outrank the update. Pick one:
  `--window off_market` (defer until close), OR `normal`/`low` (both sort below
  `scheduled` unconditionally), OR keep `high` only if `--db light` and short.
  Recall GUARD 2: even a bare `high` submit gets floored to `normal` by the
  daemon's own guard during RTH (when that guard is actually live in the running
  process — verify per GUARD 2 before depending on it).
- `critical` — genuine emergencies only; it's the one tier that outranks
  `trader update` itself.
- Generic fire-and-forget housekeeping → `low`/`idle`, not `high`.

**Resource budgets** (`resources.machine_budget()`): cores = `cpu_count() - 1`
(headroom reserved); DB budget = `2`, weighted `none=0/light=1/heavy=2` — so
**exactly one `heavy`-db job fits at a time** (a 2nd would need 2+2=4 > 2), while
two `light` jobs (1+1=2) coexist fine. Low/idle-tier (`priority >= LOW`) CPU
grants are additionally capped at `cpu_count() // 4` (or `TRADER_QUEUE_LOW_CORE_CAP`).
The granted cpu count is injected into the child's env as
`TRADER_RECALC_MAX_WORKERS` / `MC_WORKERS` / `TRADER_QUEUE_CORES` (and `MC_NO_MP=1`
when the grant is exactly 1) — the queue governs load without you touching the
job's own code.

## 3. Waiting for completion — the harness bridge

The queue is **not** harness-notified on its own. `trader queue wait <id>` polls
internally and exits on terminal state — run it **with the harness's
`run_in_background` flag** so you're re-invoked with the result instead of
blocking the whole session:

```bash
trader queue wait <id> [--timeout 3h] [--interval 60s] [--lines 30]
```

Exit codes (confirmed from `queue_manager.cmd_wait`):
- **`0`** — task reached `DONE`
- **`1`** — task reached `FAILED` or `CANCELLED`
- **`2`** — timeout expired while still non-terminal, **or the id doesn't exist**

Default `--timeout` is `None` (waits indefinitely) — always pass an explicit
`--timeout` for anything you're driving from an agent turn, so a stuck job
doesn't hang the session forever. Monitor without blocking via
`trader queue status` / `trader queue list` in the meantime.

## 4. Status / list / show / logs

```bash
trader queue status              # daemon [OK]/[DOWN], heartbeat age, cores/db/io used-vs-budget, task counts
trader queue list [--state S] [--all] [--limit 100]   # default: only QUEUED/LAUNCHING/RUNNING/SUSPENDED
trader queue show <id> [--lines 20]   # full detail: command, cwd, resources, flags, attempts, pid, timestamps, exit code, pending intents, last 12 events, run.log tail
trader queue logs <id> [--lines 40] [--stderr]
```

`status`'s daemon line reads `[OK]`/`[DOWN]` plus a reason and, if a pid is known,
`pid=<n> heartbeat=<age>s ago`. A stale heartbeat (default staleness threshold
`HEARTBEAT_STALE_S = 30.0`) means the daemon process is wedged or dead even if the
OS process still shows as running — treat it as down and consider a restart (see
§6).

## 5. `kill` vs `cancel` vs `hold`/`release`

All four set an **intent flag**; the daemon applies it on its next tick — none of
these write a terminal state directly except `cancel` on a still-`QUEUED` task.

| Command | Effect |
|---|---|
| `trader queue kill <id>` | If running/suspended/launching: daemon stops the process tree, writes a terminal `failed.json` artifact, and sets state to **`CANCELLED`** (not `FAILED`). |
| `trader queue cancel <id>` | If `QUEUED`: sets `CANCELLED` directly (`error="cancelled"`). If already running: converges to the **same** stop path as `kill` — cancel and kill are identical once a task is running. |
| `trader queue hold <id>` | Sets `held=1`. Does **not** touch a running process — only gates *future* admission (a queued-but-held task won't be picked up). |
| `trader queue release <id>` | Un-holds (`held=0`). |

`trader queue priority <id> <new_priority>` re-prioritizes a queued task (two
positionals: id, then the tier token). `trader queue clear [--state QUEUED]
[--priority P]` bulk-cancels matches (default state filter is `QUEUED`).
All four single-task commands print `"task #{id} not found or already finished"`
if the id doesn't exist or is already terminal — that's not an error you need to
chase, it's the expected message for a stale id.

## 6. Daemon health, restart, and the `tick` fallback

```bash
trader queue daemon [--interval 5.0]   # runs the reconcile/admission loop in the FOREGROUND, forever
trader queue tick                      # ONE reconcile pass then exit ("cron mode"); no-ops if a live daemon already holds the queue
```

There is no separate background-service mode inside `queue_manager.py` itself —
persistent operation is provided by the Windows Scheduled Task
(`scripts/install_queue_daemon.ps1` registers `TraderQueueDaemon`, logon-start,
restart-on-failure) wrapping `scripts/queue_daemon.ps1` which just forwards to
`trader queue daemon`. **If you need the daemon to pick up a code change (e.g. the
market-hours guard in GUARD 2), you must stop and restart the `TraderQueueDaemon`
scheduled task** — a running Python process doesn't hot-reload `daemon.py`.
Prefer doing this in an idle window (no running tasks) since a daemon restart
briefly stops admission (queued tasks wait; running tasks are unaffected — the
daemon only orchestrates, it doesn't own child processes directly).

If the daemon is confirmed down and you can't restart it right now, `trader queue
tick` runs one admission pass manually — useful to unstick a `QUEUED` job for a
single cycle without starting the persistent loop.

## 7. `run-update` — Model-B routing for the scheduled pipeline

```bash
trader queue run-update [--close] [--cpu 4] [--timeout 45m]
```

This is what routes a bare `trader update`/`close-update` invocation through the
queue in isolation (via `scripts/trader_update_via_queue.ps1`). If the daemon is
healthy: **enqueues** `update`/`close-update` (per `--close`) at
`priority=scheduled, db=heavy, exclusive=True, dedup=trader-update[-close]` (base
dedup key overridable via `TRADER_QUEUE_UPDATE_DEDUP`). If the daemon is down:
runs the update **inline** via `subprocess.run` — a zero-regression fallback so
the trading-day pipeline never silently depends on the queue being up. A
`Global\TraderUpdate` Windows named mutex prevents overlap between this path and
a directly-invoked `trader update`/`close-update`/queue-launched recalc
regardless of routing.

**This is NOT what the scheduled CLOSE pipeline runs.** The actual scheduled
close-of-day entry is `scripts/post_market_daily.ps1` (dedup `trader-close`, NOT
`trader-update-close`), which self-submits via `queue submit --priority
scheduled --exclusive --db heavy --dedup trader-close` and runs THREE
independent phases: `trader.py update` (scores+portfolio, options OFF),
`trader.py pull-options`, and `trader.py recalculate --tail-only --tail-window
5y`. That script's own header comment says it explicitly: "`queue run-update
--close` would route close-update only and drop the options pull + derived
tail." Debugging "why didn't the close pipeline run through the queue" starts at
`post_market_daily.ps1` and the `trader-close` dedup key — not here. See
`/debug-pipeline` and memory `project_trader_close_queue_routing.md`.

## 8. `audit` — cheap deterministic housekeeping scan

```bash
trader queue audit [--idle-hours 6.0] [--show-failed] [--fix]
```

No LLM involved — scans for tasks queued longer than `--idle-hours`, tasks whose
`--staleness-check` says "no longer needed," duplicate commands, and (with
`--show-failed`) recent failures. `--fix` requests-cancel the idle/stale ones
(the daemon applies it next tick, same intent-flag mechanism as `kill`/`cancel`).
Run this on demand when the queue "feels" cluttered — it's read-only unless
`--fix` is passed.

## 9. Environment variables (subsystem-level overrides)

| Variable | Purpose | Default |
|---|---|---|
| `TRADER_QUEUE_CORE_BUDGET` | total schedulable cores | `cpu_count()-1` |
| `TRADER_QUEUE_DB_BUDGET` | DB budget | `2` |
| `TRADER_QUEUE_IO_BUDGET` | IO budget | `max(2, cpu//2)` |
| `TRADER_QUEUE_LOW_CORE_CAP` | ceiling for low/idle-tier CPU grants | `max(1, cpu_count()//4)` |
| `TRADER_QUEUE_PREEMPT_MODE` | `throttle` (default) / `suspend` / `off` | `throttle` |
| `TRADER_QUEUE_MARKET_OPEN` / `_MARKET_CLOSE` | RTH window override, `HH:MM` | `09:30` / `16:00` |
| `TRADER_QUEUE_MARKET_GUARD` | `0`/`false`/`off` disables the HIGH→NORMAL RTH floor | ON (see GUARD 2 for the uncommitted-edit caveat) |
| `TRADER_QUEUE_UPDATE_DEDUP` | base dedup key for the routed update job | `trader-update` |
| `TRADER_QUEUE_UPDATE_WATCH_MIN` | opt-in watchdog: warn if no update-dedup task arrives within N min during RTH | unset = disabled |
| `TRADER_QUEUE_DB` | override the SQLite queue-state DB file path | `.cache/task_queue.db` |
| `TRADER_QUEUE_RETENTION_DAYS` | days to retain terminal tasks/artifacts before pruning | `7` |
| `TRADER_QUEUE_STALENESS_TIMEOUT_S` | timeout for a `--staleness-check` subprocess | `10` |

Verify current values by reading `queue_manager.py`/`task_queue/daemon.py`/
`task_queue/resources.py` directly if a number here matters for a decision —
these are source-confirmed as of 2026-07-06 but env overrides can shift them
per-machine.

## 10. Preemption — what happens when the box is full

- **Core scarcity** → default mode `throttle`: demotes the lowest-priority
  *strictly-lower* running victim to IDLE-class CPU affinity (1 core), reversible
  once the pressure clears. `suspend` mode (pure-compute, `db_class=none` victims
  only — **never** suspend a job holding a MySQL connection, that would freeze a
  lock against a 30s-timeout DB) is opt-in via `TRADER_QUEUE_PREEMPT_MODE=suspend`.
- **DB scarcity** → kills and requeues a **restartable** lower-priority DB-holder
  (throttle/suspend don't release a MySQL connection, only killing does).
- **Preemption eligibility uses base priority** (post market-hours-floor,
  pre-aging) — using the aged/effective priority for eligibility was tried and
  reverted after an observed livelock (see the comment in `daemon.py` near
  `_preempt_for`). Don't "fix" this back without re-reading that history.
- **`--window off_market`** is an independent admission gate layered on top of
  resources — excluded from candidates entirely while off-market is false,
  regardless of how much headroom exists.

## Sample end-to-end session

```bash
trader queue status                                   # 1. check the box first
trader queue submit --priority high --db heavy --cpu 6 --restartable \
  --dedup rp-v74-5y --reason "post-recalc research pack v74" --timeout 2h \
  -- python tools/build_research_pack.py --version v74 --run-portfolio-windows  # 2. submit
trader queue wait 123 --timeout 2h --lines 40         # 3. wait, under harness run_in_background
trader queue show 123 && trader queue logs 123 --stderr --lines 100  # 4. inspect on either outcome
```

## Evidence / see also

- [.claude/docs/task-queue.md](../../docs/task-queue.md) — the full runbook this
  skill compresses; read it for the resource-model math and crash-safety details
  not repeated here.
- [.claude/docs/traps.md](../../docs/traps.md) — trap registry; the market-hours
  floor, worktree-empty-DB, and harness-background-is-not-the-queue traps live
  there too under Infrastructure/DB and Process/shipping.
- `/run-experiment` — for the sweep-lifecycle side of "what do I queue and why."
- `/debug-pipeline` — for queue-daemon-down / stale-heartbeat / scheduled-close
  pipeline symptoms specifically.

## Self-update

If you hit a trap this skill missed, append it to GUARDS above **and** to
`.claude/docs/traps.md` in the same session.
