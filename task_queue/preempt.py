"""task_queue.preempt — Phase 2 live preemption primitives (throttle-first).

When a high-priority task is core-starved, the daemon frees CPU by acting on a
lower-priority running task's WHOLE PROCESS TREE — multiprocessing.Pool workers
and `trader recalculate` workers are separate child processes, so the root's
nice/affinity does NOT cascade to them on Windows; we must walk
`root.children(recursive=True)` and act on each.

Ladder (the daemon orchestrates; this module supplies the OS primitives):
  * THROTTLE (default) — every process in the tree -> IDLE priority class +
    affinity shrunk to one core. Reversible, and safe even for DB-holders (they
    keep progressing and releasing MySQL locks, just slowly).
  * SUSPEND  — freeze the tree (children first). Frees CPU hard. Used ONLY for
    pure-compute (db_class=none) victims: a suspended worker keeps its MySQL
    connection + any row locks, and under the DB's tight 30s timeout a frozen
    lock cascades to 2013 errors.
  * (KILL+REQUEUE of a restartable DB-holder, to reclaim a DB slot, lives in the
    daemon — it uses winproc.kill_tree + a neutral requeue.)

Every action is identity-checked via (pid, create_time) so a recycled PID is
never touched.
"""
from __future__ import annotations

import os

from . import winproc
from .model import State


def _tree(pid, create_time):
    """(root, [procs]) for a live, identity-matched pid — root first, then all
    descendants. ([] when psutil is unavailable or identity fails)."""
    if not winproc.have_psutil():
        return None, []
    if not winproc.pid_is_alive(pid, create_time):
        return None, []
    try:
        import psutil

        root = psutil.Process(int(pid))
        return root, [root] + root.children(recursive=True)
    except Exception:
        return None, []


def _throttle_core() -> int:
    n = os.cpu_count() or 1
    # Pin throttled work to the LAST core, keeping core 0 (often OS-busy) free.
    return max(0, n - 1)


def throttle_tree(pid, create_time) -> bool:
    root, procs = _tree(pid, create_time)
    if not procs:
        return False
    import psutil

    core = _throttle_core()
    ok = False
    for p in procs:
        try:
            p.nice(psutil.IDLE_PRIORITY_CLASS)
            try:
                p.cpu_affinity([core])
            except Exception:
                pass  # affinity is belt-and-suspenders; IDLE class is the lever
            ok = True
        except Exception:
            continue
    return ok


def restore_tree(pid, create_time) -> bool:
    root, procs = _tree(pid, create_time)
    if not procs:
        return False
    import psutil

    allcores = list(range(os.cpu_count() or 1))
    ok = False
    for p in procs:
        try:
            p.nice(psutil.NORMAL_PRIORITY_CLASS)
            try:
                p.cpu_affinity(allcores)
            except Exception:
                pass
            ok = True
        except Exception:
            continue
    return ok


def suspend_tree(pid, create_time) -> bool:
    root, procs = _tree(pid, create_time)
    if not procs:
        return winproc.suspend_pid(pid, create_time)
    for p in reversed(procs):  # children first (root is procs[0]); avoids racing a fresh spawn
        try:
            p.suspend()
        except Exception:
            pass
    return True


def resume_tree(pid, create_time) -> bool:
    root, procs = _tree(pid, create_time)
    if not procs:
        return winproc.resume_pid(pid, create_time)
    for p in procs:  # parent first
        try:
            p.resume()
        except Exception:
            pass
    return True


# --------------------------------------------------------------------------- #
# Task-level wrappers (mutate the store; the daemon calls these)
# --------------------------------------------------------------------------- #
def throttle(store, t) -> bool:
    ok = throttle_tree(t.pid, t.create_time)
    store.update_fields(t.id, throttled=1)
    store.log_event(t.id, "throttle", "demoted to IDLE to yield cores to higher-priority work")
    return ok


def restore(store, t) -> bool:
    ok = restore_tree(t.pid, t.create_time)
    store.update_fields(t.id, throttled=0)
    store.log_event(t.id, "restore", "restored to NORMAL priority")
    return ok


def suspend(store, t) -> bool:
    ok = suspend_tree(t.pid, t.create_time)
    store.set_state(t.id, State.SUSPENDED)
    store.log_event(t.id, "suspend", "frozen to free cores (pure-compute victim)")
    return ok


def resume_task(t) -> bool:
    """Resume a suspended/throttled tree (used by daemon reconcile on restart)."""
    return resume_tree(t.pid, t.create_time)
