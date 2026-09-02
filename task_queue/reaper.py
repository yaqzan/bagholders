"""task_queue.reaper — cheap, deterministic self-maintenance (Phase 4).

This is the "audit without burning tokens/compute" layer. Everything here is
deterministic code (NO LLM, no per-tick agent call):

  * run_staleness_checks — for queued tasks that carry an optional staleness
    predicate, run it bounded in a subprocess. Convention: **exit 0 = the work is
    no longer needed -> cancel**; any non-zero exit / timeout / error KEEPS the
    task (fail-safe — never cancel work because a predicate hung). shell=False
    always (the argv is a list, not a shell string).
  * prune_terminal — delete terminal tasks + their .codex/runs artifacts older
    than the retention window so the DB and disk don't grow unbounded.
  * audit — a one-shot "what looks suspicious?" report for `trader queue audit`
    (long-idle, now-stale, failed, duplicate commands). Run on demand, not every
    tick.

The daemon calls run_staleness_checks + prune_terminal on a throttle (not every
5s tick); TTL/dedup are handled elsewhere (daemon._expire_and_timeout + the
partial-unique index).
"""
from __future__ import annotations

import os
import shutil
import subprocess

from .model import State, now


def _retention_days() -> float:
    try:
        return float(os.environ.get("TRADER_QUEUE_RETENTION_DAYS", "7"))
    except Exception:
        return 7.0


def _staleness_timeout() -> float:
    try:
        return float(os.environ.get("TRADER_QUEUE_STALENESS_TIMEOUT_S", "10"))
    except Exception:
        return 10.0


def _is_stale(task) -> bool:
    """True iff the task's staleness predicate says the work is no longer needed.
    Fail-safe: any error/timeout/non-zero exit -> False (keep)."""
    argv = task.staleness_argv
    if not argv:
        return False
    try:
        r = subprocess.run(argv, capture_output=True, timeout=_staleness_timeout())
        return r.returncode == 0
    except Exception:
        return False


def run_staleness_checks(store) -> int:
    cancelled = 0
    for t in store.list_tasks(states=(State.QUEUED,)):
        if not t.staleness_argv:
            continue
        if _is_stale(t):
            store.set_state(t.id, State.CANCELLED,
                            error="staleness_check: work no longer needed", finished_at=now())
            store.log_event(t.id, "staleness_cancel", "staleness_check exited 0 (work no longer needed)")
            cancelled += 1
    return cancelled


def prune_terminal(store, retention_days: float | None = None) -> int:
    rd = _retention_days() if retention_days is None else retention_days
    cutoff = now() - rd * 86400.0
    pruned = 0
    for t in store.list_tasks(states=(State.DONE, State.FAILED, State.CANCELLED), limit=100000):
        ts = t.finished_at or t.updated_at or t.created_at
        if ts and ts < cutoff:
            if t.run_dir:
                try:
                    shutil.rmtree(t.run_dir, ignore_errors=True)
                except Exception:
                    pass
            store.delete_task(t.id)
            pruned += 1
    return pruned


def audit(store, idle_hours: float = 6.0) -> dict:
    """Deterministic 'what looks suspicious?' scan for `trader queue audit`."""
    ts = now()
    long_idle = []
    stale = []
    failed = []
    cmd_seen: dict[str, list[int]] = {}

    for t in store.list_tasks(states=(State.QUEUED, State.LAUNCHING, State.RUNNING, State.SUSPENDED)):
        if t.state == State.QUEUED and (ts - t.created_at) > idle_hours * 3600:
            long_idle.append(t)
        cmd_seen.setdefault(" ".join(t.argv), []).append(t.id)

    for t in store.list_tasks(states=(State.QUEUED,)):
        if t.staleness_argv and _is_stale(t):
            stale.append(t)

    failed = store.list_tasks(states=(State.FAILED,), limit=50)
    duplicates = {k: v for k, v in cmd_seen.items() if len(v) > 1}
    return {"long_idle": long_idle, "stale": stale, "failed": failed, "duplicate_commands": duplicates}
