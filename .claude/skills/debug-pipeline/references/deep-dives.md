# debug-pipeline deep-dives

Overflow detail from the main [SKILL.md](../SKILL.md) — read a section here only
after the main file's check/fix/verify steps point you at it.

---

## §3 deep-dive — MySQL zombie-query cascade, full incident

`database/trader_database.py` sets a client-side `read_timeout`; pymysql
disconnects the CLIENT only when it fires — the query keeps running
server-side. Each blind retry stacks another zombie, and enough zombies
saturate disk IO until *everything* times out, including trivial queries.

A documented incident: a 10y recalc bulk insert (1.76M rows) drifted InnoDB
table statistics and flipped a `historic_peaks` JOIN off its expected
composite index (down to a 2,088-row / 0.14s plan on paper, but the drifted
stats meant the optimizer stopped choosing it in practice). `ANALYZE TABLE
<table>` did **not** by itself restore the plan. The actual fix was
restructuring the query: split an `OR` condition into `AND`-ranges that each
map 1:1 onto an index, then stitch/sort the results in Python, rather than a
single statement combining JOIN + OR + ORDER BY (that combination is prone to
falling off an index plan under stat drift).

**House doctrine, verbatim:** >30s = missing index or a bad query plan — fix
with `EXPLAIN` + query restructuring, never by raising `read_timeout`.

**Peewee FK-per-row trap** (a separate but related cause of "the process
looks stuck but CPU is low" — not a timeout per se, but frequently
misdiagnosed as one): accessing `.symbol` (not `.symbol_id`) on a
`Score`/peewee model fires one SELECT per row via `get_rel_instance` (lazy
`DeferredForeignKey`). A loop over ~190k rows becomes ~190k MySQL roundtrips
— invisible in `SHOW PROCESSLIST` because each roundtrip is sub-millisecond,
so nothing ever shows up as "long-running." Diagnose by sampling the running
process (e.g. `py-spy dump --pid N`) and looking for `get_rel_instance` in the
stack. Fix: read the raw `row.symbol_id` key in bulk loops, or call
`.tuples()` on the query instead of iterating model instances.

---

## §4 deep-dive — task-queue daemon internals relevant to debugging

**Daemon restart semantics:** the daemon uses a Windows named mutex + a
SQLite compare-and-set singleton guard, so only one daemon instance can hold
the queue at a time. Restarting the daemon process kills only that process —
its already-launched task children are NOT killed, and the new daemon
instance adopts them on its next reconcile tick via the `launch.json`
crash-safe adoption path (a launch intent is committed before spawn
specifically so a crashed launcher never causes a double-execution or an
unowned child). If you need to stop the daemon's own process specifically
(not its task tree), target its pid directly — a tree-kill would also kill
every task it's supervising.

**Livelock history (why current preemption uses base priority, not aged
priority):** an earlier design used the task's *aged* effective priority
(base + up to +9 from waiting) for preemption-eligibility decisions. This
caused an observed cycle: a queued task would age past an equal-or-higher
scheduled task's effective priority, get admitted, immediately get
preempted back down once the scheduled task's own tick re-evaluated, re-age,
get re-admitted, and so on — a livelock that never let the `scheduled`
`trader update` job actually complete cleanly. The fix (documented in a
`task_queue/daemon.py` code comment) is that preemption eligibility now uses
**base** priority (post market-hours-floor, pre-aging) — aging only helps a
task get *admitted* in the first place, never lets it out-preempt something
it structurally shouldn't be able to beat. If you see oscillating
kill+requeue cycles on the same task id today, this specific bug class is
supposed to be closed — treat it as a fresh regression report, not "the
known livelock," unless you can show aged priority is back in the preemption
path.

**Orphan sweep mechanics:** the daemon injects `TRADER_QUEUE_TASK_ID` /
`TRADER_QUEUE_ATTEMPT` into every launched child's environment specifically
so a periodic sweep (roughly every tick) can find and reap any process tree
still tagged with an id that's no longer an active task (e.g. an
intermediate shell in a `git bash → bash → python` chain died/reparented and
the normal parent-chain kill-tree walk lost track of the leaf). This was
built after an incident where a preempted deep-recalc's python process
survived untracked and kept hammering MySQL. Manual recovery recipe if you
find one yourself: trace the process tree via its parent-chain (e.g.
`Get-CimInstance Win32_Process` filtered to the suspicious top-level shell),
`trader queue hold <id>` FIRST (otherwise the daemon may re-admit and spawn a
fresh copy once the tick after your kill runs), then force-kill the
top-level surviving process tree directly (not the daemon).

---

## Where these come from

MySQL/FK-per-row: `feedback_mysql_zombie_queries.md`,
`feedback_peewee_fk_per_row_trap.md` (auto-memory).
Queue orphan/livelock: `project_trader_close_queue_routing.md` (auto-memory),
`task_queue/daemon.py` code comments (livelock rationale), and
[task-queue.md](../../../docs/task-queue.md) preemption section.
