"""task_queue.daemon — the reconcile/admission loop (sole authoritative writer).

Operating modes:
    * run()      — persistent supervised daemon (the default). Reconciles on
                   start, then ticks every interval and heartbeats. Graceful
                   shutdown leaves running children alive (re-adopted next start)
                   — it never drains-by-killing.
    * run_once() — a single reconcile+tick (the `trader queue tick` cron mode).
                   Skips if a live daemon already holds the queue.

Crash-safety spine:
    * single-instance via a Windows named mutex + a daemon_singleton CAS;
    * (pid, create_time) identity on every liveness/kill check;
    * two-phase launch (state=launching + attempt_token committed before spawn)
      so a crashed launch is reconciled — adopted via launch.json or safely
      requeued — never double-executed;
    * resource occupancy derived from task rows; admission is priority-ordered
      with aging + reservation so a core-starved high-priority task is never
      leapfrogged by lower-priority work (Phase 2 preemption then frees cores).
"""
from __future__ import annotations

import os
import signal
import sys
import time
from uuid import uuid4

from . import launcher, preempt, reaper, resources, winproc
from .model import (
    BACKOFF_BASE_S,
    BACKOFF_CAP_S,
    DEFAULT_RECONCILE_INTERVAL_S,
    HEARTBEAT_STALE_S,
    LAUNCH_GRACE_S,
    Priority,
    State,
    now,
)
from .store import QueueStore

DAEMON_MUTEX = "Global\\TraderQueueDaemon"
TIMEOUT_BACKSTOP_GRACE_S = 30.0  # daemon kills past timeout_s + grace (wrapper enforces first)
STALENESS_INTERVAL_S = 120.0     # run bounded staleness_check predicates at most this often
PRUNE_INTERVAL_S = 600.0         # prune terminal tasks + artifacts at most this often
ORPHAN_SWEEP_INTERVAL_S = 90.0   # throttle the tagged-orphan safety sweep


class Daemon:
    def __init__(self, store: QueueStore | None = None, interval: float = DEFAULT_RECONCILE_INTERVAL_S):
        self.store = store or QueueStore()
        self.interval = float(interval)
        self.boot_id = uuid4().hex
        self.pid = os.getpid()
        self.ctime = winproc.current_create_time()
        self._lock = winproc.NamedLock(DAEMON_MUTEX)
        self._stop = False
        self._last_update_alert = 0.0  # throttles the missing-update watchdog
        self._last_staleness = 0.0     # throttles staleness_check sweeps
        self._last_prune = 0.0         # throttles terminal-task pruning
        self._last_orphan_sweep = 0.0  # throttles the tagged-orphan safety sweep
        # Market-hours guard (default ON): during RTH no non-CRITICAL job may
        # outrank the SCHEDULED `trader update`. Set TRADER_QUEUE_MARKET_GUARD=0
        # to disable.
        self._market_guard = os.environ.get(
            "TRADER_QUEUE_MARKET_GUARD", "1").strip().lower() not in ("0", "false", "off")

    # ---- lifecycle -------------------------------------------------------
    def acquire(self) -> bool:
        """Mutex first (cheap, same-machine), then the SQLite CAS (auditable owner)."""
        if not self._lock.acquire():
            return False
        if not self.store.acquire_singleton(self.pid, self.ctime, self.boot_id, HEARTBEAT_STALE_S):
            self._lock.release()
            return False
        return True

    def release(self) -> None:
        self._lock.release()

    def _install_signal_handlers(self) -> None:
        def _handler(_sig, _frm):
            self._stop = True

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _handler)
                except Exception:
                    pass

    def _sleep(self, secs: float) -> None:
        end = time.time() + secs
        while not self._stop and time.time() < end:
            time.sleep(min(0.5, max(0.0, end - time.time())))

    def run(self) -> int:
        if not self.acquire():
            print("queue daemon already running (lock held); exiting.")
            return 1
        self._install_signal_handlers()
        self.store.log_event(None, "daemon_start", f"pid={self.pid} boot={self.boot_id[:8]}")
        print(f"queue daemon started pid={self.pid} boot={self.boot_id[:8]} interval={self.interval}s")
        try:
            self.reconcile()
            while not self._stop:
                try:
                    self.tick()
                    self.store.heartbeat(self.pid, self.boot_id)
                except Exception as e:  # a bad tick must not kill the daemon
                    self.store.log_event(None, "tick_error", str(e))
                self._sleep(self.interval)
        finally:
            self.store.log_event(None, "daemon_stop", f"pid={self.pid} (running children left alive)")
            self.release()
            self.store.close()
        print("queue daemon stopped (running tasks left in place).")
        return 0

    def run_once(self) -> int:
        health = self.store.daemon_health(HEARTBEAT_STALE_S)
        if health["alive"]:
            print(f"a live daemon holds the queue (pid={health['pid']}); tick skipped.")
            return 0
        if not self.acquire():
            print("could not acquire queue lock; another tick/daemon is active.")
            return 0
        try:
            self.reconcile()
            self.tick()
            self.store.heartbeat(self.pid, self.boot_id)
        finally:
            self.release()
        return 0

    # ---- top-level passes ------------------------------------------------
    def reconcile(self) -> None:
        """Startup: bring DB state into agreement with reality, recover any
        preempted (throttled/suspended) tasks so nothing stays frozen across a
        crash, then sweep for untracked orphan supervisors."""
        for t in self.store.nonterminal_tasks():
            if t.state in (State.LAUNCHING, State.RUNNING, State.SUSPENDED):
                self._reconcile_one(t)
        self._recover_preempted_on_startup()
        self._scan_untracked_orphans()
        self._sweep_tagged_orphans(force=True)

    def tick(self) -> None:
        self._apply_intents()
        self._expire_and_timeout()
        for t in self.store.nonterminal_tasks():
            if t.state in (State.LAUNCHING, State.RUNNING, State.SUSPENDED):
                self._reconcile_one(t)
        self._admit()
        self._restore_preempted()
        self._watch_update()
        self._reap()
        self._sweep_tagged_orphans()

    # ---- per-task reconciliation ----------------------------------------
    def _reconcile_one(self, t) -> None:
        if t.state == State.LAUNCHING:
            # Give a just-committed launch a moment before judging it.
            if (now() - (t.updated_at or 0)) < LAUNCH_GRACE_S:
                return
            info = launcher.read_launch(t.run_dir) if t.run_dir else None
            if (
                info
                and info.get("attempt_token") == t.attempt_token
                and winproc.pid_is_alive(info.get("pid"), info.get("ctime"))
            ):
                self.store.update_fields(
                    t.id, state=State.RUNNING, pid=info.get("pid"),
                    create_time=info.get("ctime"), started_at=now(),
                )
                self.store.log_event(t.id, "adopt", "launching->running")
                return
            term = launcher.read_terminal(t.run_dir) if t.run_dir else None
            if term:
                self._finalize_from_artifact(t, term)
            else:
                self._fail_or_requeue(t, "launch did not start")
            return

        # RUNNING or SUSPENDED
        term = launcher.read_terminal(t.run_dir) if t.run_dir else None
        if term:
            self._finalize_from_artifact(t, term)
            return
        alive = winproc.pid_is_alive(t.pid, t.create_time)
        if t.state == State.SUSPENDED:
            if not alive:
                self._fail_or_requeue(t, "suspended process died")
            # else: deliberately frozen by preemption — leave it; _restore_preempted
            # (or startup recovery) resumes it when cores free.
            return
        # RUNNING and no terminal artifact:
        if not alive:
            self._fail_or_requeue(t, "process vanished (no terminal artifact)")

    def _finalize_from_artifact(self, t, term: dict) -> None:
        if term["state"] == "done":
            self.store.set_state(t.id, State.DONE, exit_code=term.get("exit_code"), finished_at=now())
        else:
            self.store.set_state(
                t.id, State.FAILED, exit_code=term.get("exit_code"),
                error=term.get("reason") or "failed", finished_at=now(),
            )
        self.store.log_event(t.id, "finalize", f"{term['state']} ec={term.get('exit_code')}")

    def _fail_or_requeue(self, t, reason: str) -> None:
        """Infrastructure failure (vanish / launch-didn't-start). Requeue with
        backoff if the task is restartable (idempotent) and has attempts left;
        otherwise fail. NOTE: a clean nonzero *application* exit is finalized via
        _finalize_from_artifact and is NOT auto-requeued."""
        if t.restartable and t.attempts < t.max_attempts:
            backoff = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, t.attempts - 1)))
            self.store.update_fields(
                t.id, state=State.QUEUED, pid=None, create_time=None,
                run_dir=None, attempt_token=None, cpu_grant=None,
                not_before=now() + backoff, error=reason,
            )
            self.store.log_event(
                t.id, "requeue", f"{reason}; attempt {t.attempts}/{t.max_attempts}; backoff {backoff:.0f}s"
            )
        else:
            self.store.set_state(t.id, State.FAILED, error=reason, finished_at=now())
            self.store.log_event(t.id, "fail", reason)

    # ---- CLI intents -----------------------------------------------------
    def _apply_intents(self) -> None:
        for t in self.store.nonterminal_tasks():
            if t.cancel_requested or t.kill_requested:
                if t.state == State.QUEUED:
                    self.store.set_state(
                        t.id, State.CANCELLED, cancel_requested=0, kill_requested=0,
                        finished_at=now(), error="cancelled",
                    )
                    self.store.log_event(t.id, "cancel", "queued task cancelled")
                else:  # running/suspended/launching -> stop the tree
                    self._stop_task(t, "cancelled by request")
                    self.store.update_fields(t.id, cancel_requested=0, kill_requested=0)
                continue
            if t.target_priority is not None:
                self.store.update_fields(t.id, priority=int(t.target_priority), target_priority=None)
                self.store.log_event(t.id, "reprioritize", f"-> {t.target_priority}")
            if t.hold_requested:
                self.store.update_fields(t.id, held=1, hold_requested=0)
                self.store.log_event(t.id, "hold", "")
            if t.release_requested:
                self.store.update_fields(t.id, held=0, release_requested=0)
                self.store.log_event(t.id, "release", "")

    def _stop_task(self, t, reason: str) -> None:
        winproc.kill_tree(t.pid, t.create_time)
        if t.run_dir:  # leave a terminal artifact so any racing reconcile agrees
            try:
                launcher._write_json_atomic(
                    __import__("pathlib").Path(t.run_dir) / launcher.F_FAILED,
                    {"finished_at": launcher._iso(), "exit_code": None, "failure_reason": reason},
                )
            except Exception:
                pass
        self.store.set_state(t.id, State.CANCELLED, error=reason, finished_at=now())
        self.store.log_event(t.id, "kill", reason)

    # ---- expiry / timeout ------------------------------------------------
    def _expire_and_timeout(self) -> None:
        ts = now()
        for t in self.store.list_tasks(states=(State.QUEUED,)):
            if t.ttl_s and (ts - t.created_at) > t.ttl_s:
                self.store.set_state(t.id, State.CANCELLED, error="ttl expired", finished_at=now())
                self.store.log_event(t.id, "expire", f"ttl {t.ttl_s}s exceeded while queued")
        for t in self.store.list_tasks(states=(State.RUNNING,)):
            if t.timeout_s and t.started_at and (ts - t.started_at) > (t.timeout_s + TIMEOUT_BACKSTOP_GRACE_S):
                self._stop_task(t, f"timeout backstop ({t.timeout_s}s)")
                # backstop kill records CANCELLED; reclassify as timeout failure
                self.store.set_state(t.id, State.FAILED, error="timeout", finished_at=now())

    # ---- admission + preemption -----------------------------------------
    def _fits(self, t, grant: int, usage: dict, budget) -> bool:
        return (
            usage.get("cores", 0) + grant <= budget.cores
            and usage.get("db", 0) + t.db_weight <= budget.db
            and usage.get("io", 0) + t.io_weight <= budget.io
        )

    @staticmethod
    def _occupancy(v) -> tuple[int, int, int]:
        """(cores, db, io) a victim contributes right now — mirrors
        store.resource_usage so preemption bookkeeping stays consistent."""
        g = int(v.cpu_grant if v.cpu_grant is not None else v.cpu_request)
        if v.state == State.SUSPENDED:
            return (0, v.db_weight, 0)
        if v.throttled:
            return (0, v.db_weight, v.io_weight)
        return (g, v.db_weight, v.io_weight)

    def _market_floor_base(self, base: int, off_market: bool) -> int:
        """Market-hours guard: a HIGH research job must never outrank the
        SCHEDULED `trader update`. During RTH, floor any tier strictly between
        CRITICAL and SCHEDULED (i.e. HIGH) down to NORMAL — NORMAL is above the
        low-priority core cap, so the job keeps its FULL grant and still uses the
        whole box when no update is running, but it now sorts below the update
        and is throttle-eligible the instant one is admitted. CRITICAL stays the
        sole escape hatch that outranks the update. No-op off-market or when the
        guard env-flag is disabled."""
        if not self._market_guard or off_market:
            return base
        if int(Priority.CRITICAL) < base < int(Priority.SCHEDULED):
            return int(Priority.NORMAL)
        return base

    def _admit(self) -> None:
        budget = resources.machine_budget()
        mode = resources.preempt_mode()
        usage = self.store.resource_usage()
        ts = now()
        off_market = resources.is_off_market_now()
        cands = [
            t for t in self.store.queued_tasks()
            if (t.not_before is None or ts >= t.not_before) and resources.window_open(t.time_window)
        ]
        # Highest effective priority (aging applied) first; FIFO tiebreak. The
        # market-hours floor is applied to the BASE before aging so the gap to
        # the SCHEDULED update is preserved through aging (NORMAL ages to >=21).
        cands.sort(key=lambda t: (
            t.effective_priority(ts, base=self._market_floor_base(t.priority, off_market)),
            t.created_at,
        ))

        blocked = {"cores": False, "db": False, "io": False}
        for t in cands:
            grant = resources.core_grant(t)
            need_db = t.db_weight > 0
            need_io = t.io_weight > 0
            # Reservation: don't let a lower-priority task leapfrog a higher one
            # that is starved on a resource this task also needs.
            if blocked["cores"] or (need_db and blocked["db"]) or (need_io and blocked["io"]):
                continue
            if not self._fits(t, grant, usage, budget) and mode != "off":
                # Free resources by preempting strictly-lower-priority running work.
                self._preempt_for(t, grant, usage, budget, mode, ts, off_market)
            if self._fits(t, grant, usage, budget):
                self._launch(t, grant)
                resources.add_usage(usage, t, grant)
            else:
                if usage.get("cores", 0) + grant > budget.cores:
                    blocked["cores"] = True
                if usage.get("db", 0) + t.db_weight > budget.db:
                    blocked["db"] = True
                if usage.get("io", 0) + t.io_weight > budget.io:
                    blocked["io"] = True

    def _preempt_for(self, t, grant: int, usage: dict, budget, mode: str, ts: float,
                     off_market: bool = True) -> None:
        # Preemption eligibility is governed by BASE priority, NOT aged
        # effective_priority. Aging governs admission ORDER (anti-starvation in
        # _admit) but must never grant a queued task the right to preempt an
        # equal-or-higher base-priority RUNNING task. Using effective_priority here
        # caused a kill+requeue livelock: a queued task aged past an equal-base
        # running task, preempted it, then got preempted back once it was the waiter
        # (observed 2026-06-09 between two `high` jobs with a DB-budget conflict).
        # The market-hours floor is a fixed transform (not aging), so applying it
        # to both sides is livelock-safe and lets the SCHEDULED update preempt a
        # HIGH research job during RTH (floored HIGH=NORMAL > SCHEDULED).
        t_pri = self._market_floor_base(t.priority, off_market)

        def lower(v) -> bool:
            return (self._market_floor_base(v.priority, off_market) > t_pri
                    and winproc.pid_is_alive(v.pid, v.create_time))

        # CORE scarcity -> throttle (or suspend pure-compute) lowest-pri victims.
        if usage.get("cores", 0) + grant > budget.cores:
            victims = [v for v in self.store.occupying_tasks()
                       if v.state == State.RUNNING and not v.throttled and lower(v)]
            victims.sort(key=lambda v: (v.priority, v.created_at), reverse=True)
            for v in victims:
                if usage.get("cores", 0) + grant <= budget.cores:
                    break
                g = int(v.cpu_grant if v.cpu_grant is not None else v.cpu_request)
                if mode == "suspend" and v.db_weight == 0:
                    preempt.suspend(self.store, v)
                    usage["cores"] = usage.get("cores", 0) - g
                    usage["io"] = usage.get("io", 0) - v.io_weight
                    act = "suspend"
                else:
                    preempt.throttle(self.store, v)
                    usage["cores"] = usage.get("cores", 0) - g
                    act = "throttle"
                self.store.log_event(t.id, "preempt", f"{act} #{v.id} to free cores")

        # DB scarcity -> kill+requeue a restartable lower-pri DB-holder (throttle /
        # suspend don't release a MySQL connection; killing does).
        if t.db_weight > 0 and usage.get("db", 0) + t.db_weight > budget.db:
            dbv = [v for v in self.store.occupying_tasks()
                   if v.db_weight > 0 and v.restartable
                   and v.state in (State.RUNNING, State.SUSPENDED) and lower(v)]
            dbv.sort(key=lambda v: (v.priority, v.created_at), reverse=True)
            for v in dbv:
                if usage.get("db", 0) + t.db_weight <= budget.db:
                    break
                c, d, i = self._occupancy(v)
                old_token = v.attempt_token
                winproc.kill_tree(v.pid, v.create_time)
                # The supervisor tree-walk misses descendants reparented by a dead
                # intermediate (git-bash / MP workers); reap them by env tag so the
                # DB slot is genuinely freed and no orphan keeps hammering MySQL.
                stragglers = winproc.kill_tagged(str(v.id), old_token)
                self._requeue_preempted(v, f"killed to reclaim a DB slot for #{t.id}")
                if stragglers:
                    self.store.log_event(
                        v.id, "orphan_kill",
                        f"swept {len(stragglers)} tagged straggler(s) the tree-kill missed",
                    )
                usage["cores"] = usage.get("cores", 0) - c
                usage["db"] = usage.get("db", 0) - d
                usage["io"] = usage.get("io", 0) - i
                self.store.log_event(t.id, "preempt", f"kill+requeue DB-holder #{v.id}")

    def _requeue_preempted(self, v, reason: str) -> None:
        # Preemption is not a failure: undo the launch attempt so a relaunch nets
        # neutral and repeated preemption can't exhaust max_attempts.
        new_attempts = max(0, int(v.attempts) - 1)
        self.store.update_fields(
            v.id, state=State.QUEUED, throttled=0, pid=None, create_time=None,
            run_dir=None, attempt_token=None, cpu_grant=None, started_at=None,
            not_before=None, attempts=new_attempts, error=reason,
        )
        self.store.log_event(v.id, "preempt_requeue", reason)

    def _restore_preempted(self) -> None:
        """When cores free up, un-throttle / resume the highest-priority preempted
        tasks that fit (no oversubscription)."""
        budget = resources.machine_budget()
        usage = self.store.resource_usage()
        free = budget.cores - usage.get("cores", 0)
        if free <= 0:
            return
        cands = [v for v in self.store.occupying_tasks()
                 if (v.state == State.RUNNING and v.throttled) or v.state == State.SUSPENDED]
        cands.sort(key=lambda v: v.effective_priority())  # highest priority restored first
        for v in cands:
            if not winproc.pid_is_alive(v.pid, v.create_time):
                continue
            g = int(v.cpu_grant if v.cpu_grant is not None else v.cpu_request)
            if free < g:
                continue
            if v.state == State.SUSPENDED:
                preempt.resume_task(v)
                self.store.set_state(v.id, State.RUNNING)
                self.store.log_event(v.id, "resume", "cores free; resumed")
            else:
                preempt.restore(self.store, v)
            free -= g

    def _recover_preempted_on_startup(self) -> None:
        """After a daemon restart, un-freeze any throttled/suspended tasks so work
        isn't stuck; the first admission pass re-applies preemption if still needed."""
        for v in self.store.occupying_tasks():
            if not winproc.pid_is_alive(v.pid, v.create_time):
                continue
            if v.state == State.SUSPENDED:
                preempt.resume_task(v)
                self.store.set_state(v.id, State.RUNNING)
                self.store.log_event(v.id, "resume", "daemon restart: resumed (re-decide via admission)")
            elif v.throttled:
                preempt.restore(self.store, v)
                self.store.log_event(v.id, "restore", "daemon restart: un-throttled (re-decide via admission)")

    def _launch(self, t, grant: int) -> None:
        token = uuid4().hex
        run_dir = launcher.make_run_dir(t.id, token)
        # Worker-pool sizing is decoupled from the admission reservation: under
        # oversubscribe a HIGH/CRITICAL job sizes its pool to the whole box and contends,
        # while `cpu_grant` below stays the honest reservation so preemption bookkeeping
        # (_occupancy) and fairness are unaffected. The db bound (2) remains the real cap on
        # how many heavy jobs can contend at once.
        overlay = resources.core_env(resources.worker_grant(t, grant))
        # Phase 1: durable launch intent committed BEFORE spawn (two-phase).
        t.attempt_token = token
        t.run_dir = str(run_dir)
        t.cpu_grant = grant
        t.attempts = int(t.attempts) + 1
        self.store.update_fields(
            t.id, state=State.LAUNCHING, attempt_token=token, run_dir=str(run_dir),
            cpu_grant=grant, attempts=t.attempts, not_before=None, error=None,
            started_at=None, finished_at=None, exit_code=None,
        )
        try:
            proc = launcher.launch(t, run_dir, overlay)
        except Exception as e:
            self._fail_or_requeue(t, f"launch error: {e}")
            return
        pid = proc.pid
        ctime = winproc.proc_create_time(pid)
        self.store.update_fields(
            t.id, state=State.RUNNING, pid=pid, create_time=ctime, started_at=now()
        )
        self.store.log_event(t.id, "launch", f"pid={pid} grant={grant} run={run_dir.name}")

    # ---- cheap deterministic self-maintenance (throttled) ----------------
    def _reap(self) -> None:
        ts = now()
        if ts - self._last_staleness >= STALENESS_INTERVAL_S:
            self._last_staleness = ts
            try:
                reaper.run_staleness_checks(self.store)
            except Exception as e:
                self.store.log_event(None, "reaper_error", f"staleness: {e}")
        if ts - self._last_prune >= PRUNE_INTERVAL_S:
            self._last_prune = ts
            try:
                pruned = reaper.prune_terminal(self.store)
                if pruned:
                    self.store.log_event(None, "prune", f"removed {pruned} terminal task(s) + artifacts")
            except Exception as e:
                self.store.log_event(None, "reaper_error", f"prune: {e}")

    # ---- trader-update schedule awareness (Model B) ----------------------
    def _watch_update(self) -> None:
        """Opt-in (set TRADER_QUEUE_UPDATE_WATCH_MIN): during market hours, alert
        if no `trader update` task has arrived within the expected cadence — i.e.
        the Task Scheduler entry that routes update through the queue is broken.
        The daemon never *launches* update (Task Scheduler owns timing); it only
        watches."""
        raw = os.environ.get("TRADER_QUEUE_UPDATE_WATCH_MIN")
        if not raw:
            return
        try:
            window = float(raw) * 60.0
        except Exception:
            return
        if resources.is_off_market_now():
            return  # market closed -> no update expected
        dedup = os.environ.get("TRADER_QUEUE_UPDATE_DEDUP", "trader-update")
        latest = self.store.latest_with_dedup(dedup)
        last_seen = latest.created_at if latest else 0.0
        ts = now()
        if ts - last_seen > window and ts - self._last_update_alert > window:
            self._last_update_alert = ts
            msg = (f"no '{dedup}' task in {raw}m during market hours — the Task "
                   "Scheduler entry routing update through the queue may be broken")
            self.store.log_event(None, "update_watch_alert", msg)
            print(f"[queue] WARNING: {msg}")

    # ---- orphan backstop -------------------------------------------------
    def _scan_untracked_orphans(self) -> None:
        if not winproc.have_psutil():
            return
        try:
            import psutil
        except Exception:
            return
        tracked = {int(t.pid) for t in self.store.nonterminal_tasks() if t.pid}
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = p.info.get("cmdline") or []
                is_supervisor = any("task_queue.launcher" in str(c) for c in cmdline)
                if is_supervisor and int(p.pid) not in tracked:
                    winproc.kill_tree(p.pid, winproc.proc_create_time(p.pid))
                    self.store.log_event(None, "orphan_kill", f"untracked supervisor pid={p.pid}")
            except Exception:
                continue

    def _sweep_tagged_orphans(self, force: bool = False) -> None:
        """Throttled safety net for the preempt/kill orphan class: kill any
        task-tagged process whose (task_id, attempt) the store no longer holds as
        a live launching/running/suspended attempt. Catches descendants orphaned
        by an incomplete tree-kill across a broken parent chain (git-bash / MSYS
        / multiprocessing-worker reparenting) — the class that accumulated
        untracked full-recalc clusters hammering MySQL."""
        if not force and (now() - self._last_orphan_sweep) < ORPHAN_SWEEP_INTERVAL_S:
            return
        self._last_orphan_sweep = now()
        if not winproc.have_psutil():
            return
        rows_by_id: dict[str, list] = {}
        for t in self.store.nonterminal_tasks():
            rows_by_id.setdefault(str(t.id), []).append(t)

        def is_orphan(ptid: str, pattempt: str) -> bool:
            rows = rows_by_id.get(ptid)
            if not rows:
                return True  # task terminal / gone -> any leftover process is an orphan
            for t in rows:
                if t.state in (State.LAUNCHING, State.RUNNING, State.SUSPENDED):
                    # Belongs to a live attempt (or token momentarily unknowable ->
                    # keep it, be conservative). Stale-attempt procs fall through.
                    if not t.attempt_token or str(t.attempt_token) == pattempt:
                        return False
            return True  # only QUEUED/held rows remain -> stale procs are orphans

        killed: list = []
        try:
            for pid, ptid, pattempt in winproc.iter_tagged():
                if is_orphan(ptid, pattempt) and winproc.kill_tree(pid, winproc.proc_create_time(pid)):
                    killed.append((ptid, pid))
        except Exception:
            return
        if killed:
            self.store.log_event(
                None, "orphan_kill",
                f"tagged-orphan sweep reaped {len(killed)}: {killed[:12]}",
            )


def run_daemon(interval: float = DEFAULT_RECONCILE_INTERVAL_S) -> int:
    return Daemon(interval=interval).run()


def run_once() -> int:
    return Daemon().run_once()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    raise SystemExit(run_once() if mode == "once" else run_daemon())
