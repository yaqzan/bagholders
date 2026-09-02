"""Unit + integration tests for the task_queue subsystem.

Run:
    python tests/test_task_queue.py                # fast logic + crash-safety tests
    python tests/test_task_queue.py --integration  # also spawn a real task

The default suite spawns NO processes (it drives the daemon's decision logic with
fabricated rows + a monkeypatched launcher), so it is fast and shell-independent.
The integration test launches a real supervised task end-to-end.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from task_queue import api, preempt, resources, winproc  # noqa: E402
from task_queue.daemon import DAEMON_MUTEX, Daemon  # noqa: E402
from task_queue.model import Priority, State, Task  # noqa: E402
from task_queue.store import DedupConflict, QueueStore  # noqa: E402

_PASS = 0
_FAIL = 0
_SKIP = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL {msg}")


def skip(msg: str) -> None:
    global _SKIP
    _SKIP += 1
    print(f"  skip {msg}")


def _db() -> str:
    d = tempfile.mkdtemp(prefix="tq_test_")
    return os.path.join(d, "q.db")


# --------------------------------------------------------------------------- #
def test_columns_match():
    print("[columns]")
    st = QueueStore(_db())
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(tasks)").fetchall()}
    check(cols == set(Task.column_names()), "Task dataclass fields == tasks table columns")
    st.close()


def test_dedup():
    print("[dedup]")
    st = QueueStore(_db())
    r1 = api.submit(["python", "-c", "pass"], dedup="k1", store=st)
    r2 = api.submit(["python", "-c", "pass"], dedup="k1", store=st)
    check(r1.created and not r2.created, "2nd active submit with same dedup is a no-op")
    check(r2.task.id == r1.task.id, "dedup returns the existing task")
    raised = False
    try:
        api.submit(["python", "-c", "pass"], dedup="k1", on_conflict="raise", store=st)
    except DedupConflict:
        raised = True
    check(raised, "on_conflict='raise' raises DedupConflict")
    # a different key is allowed
    r3 = api.submit(["python", "-c", "pass"], dedup="k2", store=st)
    check(r3.created, "different dedup key is allowed")
    st.close()


def test_resource_accounting():
    print("[resources]")
    st = QueueStore(_db())
    st.insert_task(Task(command_json='["x"]', state=State.RUNNING, cpu_request=3,
                        cpu_grant=3, db_class="heavy", io_class="light"))
    u = st.resource_usage()
    check(u["cores"] == 3 and u["db"] == 2 and u["io"] == 1,
          "resource_usage derives cores=3/db=2/io=1 from the running row")
    # queued tasks do NOT occupy resources
    api.submit(["python", "-c", "pass"], cpu=4, store=st)
    check(st.resource_usage()["cores"] == 3, "queued task does not occupy cores")
    st.close()


def test_admission_reservation():
    print("[admission/reservation]")
    os.environ["TRADER_QUEUE_CORE_BUDGET"] = "4"
    try:
        st = QueueStore(_db())
        # occupy 3 of 4 cores with a running task
        st.insert_task(Task(command_json='["x"]', state=State.RUNNING, cpu_request=3, cpu_grant=3))
        hi = api.submit(["python", "-c", "pass"], priority="high", cpu=2, store=st).task
        lo = api.submit(["python", "-c", "pass"], priority="low", cpu=1, store=st).task

        d = Daemon(store=st)
        launched = []
        d._launch = lambda t, g: launched.append((t.id, g))  # decision-only

        d._admit()
        check(launched == [], "core-starved high task blocks lower-pri leapfrog (free=1, need 2)")

        os.environ["TRADER_QUEUE_CORE_BUDGET"] = "5"  # free becomes 2
        launched.clear()
        d._admit()
        check([x[0] for x in launched] == [hi.id],
              "high admitted once it fits; low stays blocked behind the now-full budget")
        check(lo.id not in [x[0] for x in launched], "low not admitted (budget consumed by high)")
        st.close()
    finally:
        os.environ.pop("TRADER_QUEUE_CORE_BUDGET", None)


def test_priority_order_and_aging():
    print("[priority/aging]")
    os.environ["TRADER_QUEUE_CORE_BUDGET"] = "1"
    try:
        st = QueueStore(_db())
        lo = api.submit(["python", "-c", "pass"], priority="low", cpu=1, store=st).task
        hi = api.submit(["python", "-c", "pass"], priority="high", cpu=1, store=st).task
        d = Daemon(store=st)
        launched = []
        d._launch = lambda t, g: launched.append(t.id)
        d._admit()
        check(launched == [hi.id], "with 1 free core, the higher-priority task wins regardless of FIFO")
        # aging: a long-waiting low task improves but never crosses a tier boundary
        from task_queue.model import AGING_STEP_S, Priority
        t = st.get_task(lo.id)
        t.created_at = time.time() - AGING_STEP_S * 50  # very old
        eff = t.effective_priority()
        check(eff < int(Priority.LOW) and eff > int(Priority.NORMAL),
              "aged low task improves but stays below the NORMAL tier")
        st.close()
    finally:
        os.environ.pop("TRADER_QUEUE_CORE_BUDGET", None)


def test_reconcile_dead_pid_requeue():
    print("[reconcile: dead pid -> requeue]")
    st = QueueStore(_db())
    t = Task(command_json='["x"]', state=State.RUNNING, restartable=1, attempts=1,
             max_attempts=3, pid=999_999_990, create_time=1.0)
    st.insert_task(t)
    d = Daemon(store=st)
    d._reconcile_one(st.get_task(t.id))
    r = st.get_task(t.id)
    check(r.state == State.QUEUED, "restartable running task w/ dead pid -> requeued")
    check(r.not_before and r.not_before > time.time(), "requeue sets a future backoff (not_before)")
    check(r.pid is None and r.attempt_token is None, "pid/token cleared on requeue")
    st.close()


def test_reconcile_dead_pid_nonrestartable_fail():
    print("[reconcile: dead pid, non-restartable -> fail]")
    st = QueueStore(_db())
    t = Task(command_json='["x"]', state=State.RUNNING, restartable=0, attempts=1,
             pid=999_999_991, create_time=1.0)
    st.insert_task(t)
    d = Daemon(store=st)
    d._reconcile_one(st.get_task(t.id))
    check(st.get_task(t.id).state == State.FAILED, "non-restartable vanished task -> failed")
    st.close()


def test_reconcile_live_adoption_and_pid_reuse():
    print("[reconcile: identity (pid,create_time)]")
    st = QueueStore(_db())
    my_pid = os.getpid()
    my_ct = winproc.current_create_time()
    # live + matching ctime -> adopted (stays running)
    a = Task(command_json='["x"]', state=State.RUNNING, pid=my_pid, create_time=my_ct)
    st.insert_task(a)
    d = Daemon(store=st)
    d._reconcile_one(st.get_task(a.id))
    check(st.get_task(a.id).state == State.RUNNING, "live pid with matching create_time stays running")
    # live pid but WRONG ctime (simulated PID reuse) -> treated as vanished
    b = Task(command_json='["x"]', state=State.RUNNING, restartable=0, pid=my_pid,
             create_time=(my_ct or 0) + 5000.0)
    st.insert_task(b)
    d._reconcile_one(st.get_task(b.id))
    check(st.get_task(b.id).state == State.FAILED,
          "live pid with MISMATCHED create_time treated as vanished (PID-reuse guard)")
    st.close()


def test_intents_cancel_queued():
    print("[intents]")
    st = QueueStore(_db())
    t = api.submit(["python", "-c", "pass"], store=st).task
    api.cancel(t.id, store=st)
    check(st.get_task(t.id).cancel_requested == 1, "cancel writes an intent, not a direct state flip")
    d = Daemon(store=st)
    d._apply_intents()
    check(st.get_task(t.id).state == State.CANCELLED, "daemon applies cancel intent -> queued task cancelled")
    # reprioritize intent
    t2 = api.submit(["python", "-c", "pass"], priority="low", store=st).task
    api.set_priority(t2.id, "high", store=st)
    d._apply_intents()
    from task_queue.model import Priority
    check(st.get_task(t2.id).priority == int(Priority.HIGH), "daemon applies reprioritize intent")
    st.close()


def test_ttl_expiry():
    print("[ttl]")
    st = QueueStore(_db())
    t = Task(command_json='["x"]', state=State.QUEUED, ttl_s=1, created_at=time.time() - 100)
    st.insert_task(t)
    d = Daemon(store=st)
    d._expire_and_timeout()
    check(st.get_task(t.id).state == State.CANCELLED, "queued task past its TTL is expired")
    st.close()


def test_named_lock():
    print("[named lock]")
    name = "Global\\TQ_UNIT_TEST_LOCK"
    a = winproc.NamedLock(name)
    b = winproc.NamedLock(name)
    check(a.acquire(), "first acquire succeeds")
    check(not b.acquire(), "second acquire on the same name fails while held")
    a.release()
    check(b.acquire(), "after release, acquire succeeds")
    b.release()


def _daemon_mutex_held_elsewhere() -> bool:
    """True if Global\\TraderQueueDaemon is currently held by ANOTHER process.

    This test needs to be the first/only holder of the real daemon mutex to
    exercise the singleton guard. On a box where the production
    `TraderQueueDaemon` is live (the normal state -- see .claude/docs/
    task-queue.md), that mutex is already held system-wide, so this test's
    own acquire attempt would legitimately fail for a reason that has nothing
    to do with the guard logic under test. Probe with a throwaway lock on the
    SAME name (acquire+immediately release) rather than guessing from process
    lists -- the mutex itself is the only authoritative answer, and this
    mirrors the exact acquire/release pattern `test_named_lock` above already
    uses for a private lock name.
    """
    probe = winproc.NamedLock(DAEMON_MUTEX)
    held_elsewhere = not probe.acquire()
    if not held_elsewhere:
        probe.release()
    return held_elsewhere


def test_single_daemon_guard():
    print("[single-daemon guard]")
    if _daemon_mutex_held_elsewhere():
        skip("first daemon acquires the singleton -- Global\\TraderQueueDaemon is "
             "already held by another process (the production daemon is live); "
             "this test needs to be the sole holder to exercise the guard")
        return
    path = _db()
    d1 = Daemon(store=QueueStore(path))
    d2 = Daemon(store=QueueStore(path))
    check(d1.acquire(), "first daemon acquires the singleton")
    check(not d2.acquire(), "second daemon is refused (mutex + CAS)")
    d1.release()
    d1.store.close()
    d2.store.close()


def test_window_helpers():
    print("[windows]")
    check(resources.window_open("any") is True, "'any' window is always open")
    check(isinstance(resources.is_off_market_now(), bool), "is_off_market_now() returns a bool")
    check(isinstance(resources.eastern_now().hour, int), "eastern_now() computes an ET clock")


def test_cli_token_recovery():
    print("[cli verbatim '--' recovery]")
    path = _db()
    os.environ["TRADER_QUEUE_DB"] = path
    try:
        import queue_manager
        queue_manager.main(["submit", "--priority", "low", "--",
                            "python", "-c", "print('x y')"])
        tasks = api.list_tasks()
        check(len(tasks) == 1, "one task queued via CLI main()")
        argv = tasks[0].argv
        check(argv == ["python", "-c", "print('x y')"],
              "command after '--' stored verbatim (incl. the space-containing arg)")
        # 'python' is normalized to the running interpreter at LAUNCH time, not
        # at submit — so the stored command stays human-readable + re-resolvable.
        from task_queue.launcher import _normalize_argv
        check(_normalize_argv(argv)[0] == sys.executable,
              "launcher normalizes 'python' -> the running interpreter at launch")
    finally:
        os.environ.pop("TRADER_QUEUE_DB", None)


def test_run_update_routes_when_daemon_up():
    print("[phase3: run-update routes through queue when daemon up]")
    path = _db()
    os.environ["TRADER_QUEUE_DB"] = path
    try:
        st = QueueStore(path)
        st.acquire_singleton(os.getpid(), winproc.current_create_time(), "testboot", 30.0)
        st.close()
        import queue_manager
        queue_manager.main(["run-update"])
        upd = [t for t in api.list_tasks() if t.dedup_key == "trader-update"]
        check(len(upd) == 1, "daemon up -> update enqueued as a queue task")
        check(upd and upd[0].priority == int(Priority.SCHEDULED), "enqueued at 'scheduled' priority")
        check(upd and upd[0].db_class == "heavy" and upd[0].exclusive == 1, "db=heavy + exclusive")
        check(upd and upd[0].restartable == 0, "update NOT restartable (no double-send of alerts)")
        queue_manager.main(["run-update"])  # second tick
        upd2 = [t for t in api.list_tasks() if t.dedup_key == "trader-update"]
        check(len(upd2) == 1, "second run-update deduped (exclusive) -> no double-queue")
    finally:
        os.environ.pop("TRADER_QUEUE_DB", None)


def test_run_update_inline_when_daemon_down():
    print("[phase3: run-update inline fallback when daemon down]")
    path = _db()
    os.environ["TRADER_QUEUE_DB"] = path
    try:
        import subprocess as _sp

        calls = []

        class _R:
            returncode = 0

        orig = _sp.run
        _sp.run = lambda *a, **k: (calls.append(list(a[0])), _R())[-1]
        import queue_manager
        try:
            queue_manager.main(["run-update"])  # daemon down -> inline
        except SystemExit:
            pass
        finally:
            _sp.run = orig
        check(bool(calls) and calls[0][1:] == ["trader.py", "update"],
              "daemon down -> runs `trader.py update` inline (zero regression)")
        check(not api.list_tasks(), "inline path enqueues nothing")
    finally:
        os.environ.pop("TRADER_QUEUE_DB", None)


def test_throttle_suspend_accounting():
    print("[phase2: preemption-aware accounting]")
    st = QueueStore(_db())
    st.insert_task(Task(command_json='["x"]', state=State.RUNNING, throttled=1,
                        cpu_request=3, cpu_grant=3, db_class="heavy", io_class="heavy"))
    u = st.resource_usage()
    check(u["cores"] == 0 and u["db"] == 2 and u["io"] == 2,
          "throttled task yields cores but keeps db+io footprint")
    st2 = QueueStore(_db())
    st2.insert_task(Task(command_json='["x"]', state=State.SUSPENDED,
                         cpu_request=3, cpu_grant=3, db_class="heavy", io_class="heavy"))
    u2 = st2.resource_usage()
    check(u2["cores"] == 0 and u2["db"] == 2 and u2["io"] == 0,
          "suspended task yields cores+io but its frozen MySQL connection still counts")
    st.close()
    st2.close()


def test_preemption_throttle():
    print("[phase2: throttle-first core preemption]")
    os.environ["TRADER_QUEUE_CORE_BUDGET"] = "3"
    os.environ["TRADER_QUEUE_PREEMPT_MODE"] = "throttle"
    try:
        st = QueueStore(_db())
        v = st.insert_task(Task(command_json='["x"]', state=State.RUNNING, priority=int(Priority.LOW),
                                cpu_request=3, cpu_grant=3, pid=os.getpid(),
                                create_time=winproc.current_create_time()))
        hi = api.submit(["python", "-c", "pass"], priority="high", cpu=2, store=st).task
        d = Daemon(store=st)
        launched = []
        d._launch = lambda t, g: launched.append(t.id)
        throttled = []
        orig = preempt.throttle
        preempt.throttle = lambda store, vv: (throttled.append(vv.id),
                                              store.update_fields(vv.id, throttled=1), True)[-1]
        try:
            d._admit()
        finally:
            preempt.throttle = orig
        check(throttled == [v.id], "high-pri preempts (throttles) the lower-pri victim")
        check(launched == [hi.id], "high-pri admitted after preemption freed cores")
        st.close()
    finally:
        os.environ.pop("TRADER_QUEUE_CORE_BUDGET", None)
        os.environ.pop("TRADER_QUEUE_PREEMPT_MODE", None)


def test_preemption_db_kill_requeue():
    print("[phase2: DB-slot reclaim via kill+requeue]")
    os.environ["TRADER_QUEUE_DB_BUDGET"] = "2"
    try:
        st = QueueStore(_db())
        v = Task(command_json='["x"]', state=State.RUNNING, priority=int(Priority.LOW),
                 cpu_request=1, cpu_grant=1, db_class="heavy", restartable=1, attempts=1,
                 pid=os.getpid(), create_time=winproc.current_create_time())
        st.insert_task(v)
        hi = api.submit(["python", "-c", "pass"], priority="high", db="heavy", cpu=1, store=st).task
        d = Daemon(store=st)
        launched = []
        d._launch = lambda t, g: launched.append(t.id)
        import task_queue.winproc as W
        killed = []
        orig = W.kill_tree
        W.kill_tree = lambda *a, **k: (killed.append(a[0]), True)[-1]
        try:
            d._admit()
        finally:
            W.kill_tree = orig
        rv = st.get_task(v.id)
        check(killed and rv.state == State.QUEUED, "restartable DB-holder killed+requeued to free a DB slot")
        check(rv.attempts == 0, "preemption requeue undoes the launch attempt (neutral, no max-attempts burn)")
        check(launched == [hi.id], "high-pri DB task admitted after the DB slot is reclaimed")
        st.close()
    finally:
        os.environ.pop("TRADER_QUEUE_DB_BUDGET", None)


def test_restore_preempted():
    print("[phase2: restore throttled when cores free]")
    os.environ["TRADER_QUEUE_CORE_BUDGET"] = "4"
    try:
        st = QueueStore(_db())
        v = Task(command_json='["x"]', state=State.RUNNING, throttled=1, cpu_request=2,
                 cpu_grant=2, pid=os.getpid(), create_time=winproc.current_create_time())
        st.insert_task(v)
        d = Daemon(store=st)
        restored = []
        orig = preempt.restore
        preempt.restore = lambda store, vv: (restored.append(vv.id),
                                             store.update_fields(vv.id, throttled=0), True)[-1]
        try:
            d._restore_preempted()
        finally:
            preempt.restore = orig
        check(restored == [v.id], "throttled task restored when cores are free")
        check(st.get_task(v.id).throttled == 0, "throttled flag cleared on restore")
        st.close()
    finally:
        os.environ.pop("TRADER_QUEUE_CORE_BUDGET", None)


def test_staleness_check():
    print("[phase4: staleness predicate]")
    from task_queue import reaper

    st = QueueStore(_db())
    api.submit(["python", "-c", "pass"],
               staleness_check=[sys.executable, "-c", "import sys; sys.exit(0)"], store=st)  # stale
    keep = api.submit(["python", "-c", "pass"],
                      staleness_check=[sys.executable, "-c", "import sys; sys.exit(1)"], store=st).task  # keep
    n = reaper.run_staleness_checks(st)
    check(n == 1, "exit-0 staleness_check -> task cancelled (no longer needed)")
    check(st.get_task(keep.id).state == State.QUEUED, "non-zero staleness_check -> kept (fail-safe)")
    st.close()


def test_prune_terminal():
    print("[phase4: prune terminal tasks]")
    from task_queue import reaper

    st = QueueStore(_db())
    old = st.insert_task(Task(command_json='["x"]', state=State.DONE, finished_at=time.time() - 100 * 86400))
    recent = st.insert_task(Task(command_json='["x"]', state=State.DONE, finished_at=time.time()))
    pruned = reaper.prune_terminal(st, retention_days=7)
    check(pruned == 1, "terminal task older than retention pruned")
    check(st.get_task(old.id) is None, "pruned task row deleted")
    check(st.get_task(recent.id) is not None, "recent terminal task kept")
    st.close()


def test_audit():
    print("[phase4: audit scan]")
    from task_queue import reaper

    st = QueueStore(_db())
    idle = st.insert_task(Task(command_json='["a"]', state=State.QUEUED, created_at=time.time() - 10 * 3600))
    api.submit(["b"], store=st)  # fresh, should not be flagged
    rep = reaper.audit(st, idle_hours=6.0)
    check(len(rep["long_idle"]) == 1 and rep["long_idle"][0].id == idle.id,
          "audit flags the long-idle queued task and not the fresh one")
    st.close()


def test_integration_throttle_suspend():
    print("[phase2 integration: real throttle/suspend on a child tree]")
    import subprocess

    import psutil

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        ct = winproc.proc_create_time(p.pid)
        check(preempt.throttle_tree(p.pid, ct), "throttle_tree applied")
        check(psutil.Process(p.pid).nice() == psutil.IDLE_PRIORITY_CLASS,
              "child demoted to IDLE priority class")
        check(preempt.restore_tree(p.pid, ct), "restore_tree applied")
        check(psutil.Process(p.pid).nice() == psutil.NORMAL_PRIORITY_CLASS,
              "child restored to NORMAL priority class")
        preempt.suspend_tree(p.pid, ct)
        time.sleep(0.3)
        status = psutil.Process(p.pid).status()
        check(status == psutil.STATUS_STOPPED, f"suspend_tree froze the child (status={status})")
        preempt.resume_tree(p.pid, ct)
    finally:
        winproc.kill_tree(p.pid, winproc.proc_create_time(p.pid))


def test_integration_spawn():
    print("[integration: real spawn]")
    import shutil

    from task_queue.launcher import tail_log

    st = QueueStore(_db())
    res = api.submit([sys.executable, "-c", "import time; time.sleep(1); print('integ-ok')"],
                     priority="low", restartable=True, store=st)
    d = Daemon(store=st)
    if not d.acquire():
        check(False, "could not acquire daemon for integration")
        return
    try:
        d.reconcile()
        d.tick()  # launch
        t = None
        for _ in range(40):
            time.sleep(0.5)
            d.tick()
            t = st.get_task(res.task.id)
            if t.is_terminal:
                break
        log = tail_log(t.run_dir, lines=5) if t and t.run_dir else ""
        check(t is not None and t.state == State.DONE, f"task reached done (state={t.state if t else None})")
        check("integ-ok" in log, "captured the task's stdout in run.log")
        if t and t.run_dir:
            shutil.rmtree(t.run_dir, ignore_errors=True)
    finally:
        d.release()
        st.close()


def test_no_preempt_between_equal_priority():
    """Regression (2026-06-09 livelock): two EQUAL base-priority tasks that cannot
    coexist (DB-budget conflict) must NOT kill+requeue each other. Aging governs
    admission ORDER (anti-starvation), not preemption rights — a queued task aged
    past an equal-base RUNNING task must not preempt it. A genuinely HIGHER base
    task must still preempt."""
    print("[no-preempt: equal base priority, db conflict]")
    os.environ["TRADER_QUEUE_DB_BUDGET"] = "2"
    os.environ["TRADER_QUEUE_CORE_BUDGET"] = "8"
    # The victim's pid is THIS process; neuter the real kill so the (correct)
    # higher-priority preemption in the positive control can't kill the test runner.
    _kt, _ktag = winproc.kill_tree, winproc.kill_tagged
    winproc.kill_tree = lambda *a, **k: None
    winproc.kill_tagged = lambda *a, **k: []
    try:
        st = QueueStore(_db())
        # RUNNING high task holding the full DB budget (heavy=2), with a LIVE pid so it
        # is a genuine preemption candidate (exercises the priority gate, not liveness).
        run = Task(command_json='["x"]', state=State.RUNNING, priority=int(Priority.HIGH),
                   cpu_request=2, cpu_grant=2, db_class="heavy", restartable=1,
                   pid=os.getpid(), create_time=winproc.current_create_time(),
                   started_at=time.time())
        st.insert_task(run)
        # QUEUED high task needing a DB slot (light=1), AGED ~6h so its effective
        # priority is boosted to the max, below the running task's base 10.
        q = Task(command_json='["x"]', state=State.QUEUED, priority=int(Priority.HIGH),
                 cpu_request=2, db_class="light", created_at=time.time() - 3600 * 6)
        st.insert_task(q)
        check(st.get_task(q.id).effective_priority() < st.get_task(run.id).priority,
              "precondition: aged queued task's effective priority beats the running base")

        d = Daemon(store=st)
        launched = []
        d._launch = lambda t, g: launched.append(t.id)
        d._admit()
        check(st.get_task(run.id).state == State.RUNNING,
              "equal-base running DB-holder NOT preempted by an aged equal-base queued task")
        check(q.id not in launched,
              "aged queued task does not launch (waits for the DB slot to free naturally)")

        # Positive control: a genuinely HIGHER base task (critical) still preempts.
        crit = Task(command_json='["x"]', state=State.QUEUED, priority=int(Priority.CRITICAL),
                    cpu_request=2, db_class="heavy", restartable=1, created_at=time.time())
        st.insert_task(crit)
        d._admit()
        check(st.get_task(run.id).state == State.QUEUED,
              "higher-base (critical) task preempts the running high DB-holder (legit preemption intact)")
        st.close()
    finally:
        winproc.kill_tree, winproc.kill_tagged = _kt, _ktag
        os.environ.pop("TRADER_QUEUE_DB_BUDGET", None)
        os.environ.pop("TRADER_QUEUE_CORE_BUDGET", None)


def main() -> int:
    run_integration = "--integration" in sys.argv
    tests = [
        test_columns_match,
        test_dedup,
        test_resource_accounting,
        test_admission_reservation,
        test_priority_order_and_aging,
        test_reconcile_dead_pid_requeue,
        test_reconcile_dead_pid_nonrestartable_fail,
        test_reconcile_live_adoption_and_pid_reuse,
        test_intents_cancel_queued,
        test_ttl_expiry,
        test_named_lock,
        test_single_daemon_guard,
        test_window_helpers,
        test_cli_token_recovery,
        test_throttle_suspend_accounting,
        test_preemption_throttle,
        test_preemption_db_kill_requeue,
        test_no_preempt_between_equal_priority,
        test_restore_preempted,
        test_run_update_routes_when_daemon_up,
        test_run_update_inline_when_daemon_down,
        test_staleness_check,
        test_prune_terminal,
        test_audit,
    ]
    if run_integration:
        tests.append(test_integration_throttle_suspend)
        tests.append(test_integration_spawn)
    for t in tests:
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            global _FAIL
            _FAIL += 1
            print(f"  FAIL {t.__name__} raised {e}")
    suffix = f", {_SKIP} skipped" if _SKIP else ""
    print(f"\n{_PASS} passed, {_FAIL} failed{suffix}")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
