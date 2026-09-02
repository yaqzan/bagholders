"""task_queue.api — queue_lib: the programmatic surface for agents & workflows.

    from task_queue import api as queue_lib
    queue_lib.submit(
        ["python", "tools/build_research_pack.py", "--version", "v46",
         "--run-portfolio-windows"],
        priority="low", db="heavy", cpu=4, ttl="24h",
        dedup="sentinel-recalc-v46", restartable=True,
        reason="old-version Sentinel results for VersionCompare",
    )

The CLI (queue_manager) is a thin wrapper over these. Mutations the daemon owns
(cancel/kill/priority/hold/release) are written as INTENTS the daemon applies on
its next tick — so this module never races the daemon over a live process.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from .model import (
    HEARTBEAT_STALE_S,
    DEFAULT_MAX_ATTEMPTS,
    Priority,
    State,
    Task,
    parse_priority,
)
from .store import DedupConflict, QueueStore


def _store(path=None) -> QueueStore:
    return QueueStore(path) if path else QueueStore()


def parse_duration(value) -> float | None:
    """'24h' / '30m' / '90s' / '2d' / '120' -> seconds. None/'' -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s)
    if not m:
        raise ValueError(f"bad duration {value!r} (use e.g. 90s, 30m, 24h, 2d)")
    n = float(m.group(1))
    return n * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


@dataclass
class SubmitResult:
    task: Task
    created: bool  # False == an active dedup twin already existed


def submit(
    command,
    *,
    priority="normal",
    cpu: int = 1,
    db: str = "none",
    io: str = "none",
    window: str = "any",
    restartable: bool = False,
    exclusive: bool = False,
    dedup: str | None = None,
    ttl=None,
    timeout=None,
    not_before=None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    reason: str | None = None,
    created_by: str | None = None,
    env: dict | None = None,
    cwd: str | None = None,
    staleness_check=None,
    on_conflict: str = "return_existing",  # 'return_existing' | 'raise'
    store: QueueStore | None = None,
) -> SubmitResult:
    if isinstance(command, str):
        raise ValueError("pass command as a list of argv tokens, not a string")
    argv = [str(c) for c in command]
    if not argv:
        raise ValueError("empty command")
    # exclusive implies dedup on the command itself if no key given
    if exclusive and not dedup:
        dedup = "cmd:" + " ".join(argv)

    nb_delay = parse_duration(not_before)
    nb_epoch = (time.time() + nb_delay) if nb_delay else None

    task = Task(
        command_json=json.dumps(argv),
        cwd=cwd,
        env_json=json.dumps(dict(env)) if env else None,
        priority=int(parse_priority(priority)),
        cpu_request=max(1, int(cpu)),
        db_class=db,
        io_class=io,
        time_window=window,
        restartable=1 if restartable else 0,
        exclusive=1 if exclusive else 0,
        dedup_key=dedup,
        ttl_s=_as_int(parse_duration(ttl)),
        timeout_s=_as_int(parse_duration(timeout)),
        not_before=nb_epoch,
        max_attempts=int(max_attempts),
        reason=reason,
        created_by=created_by,
        staleness_check_json=json.dumps([str(x) for x in staleness_check]) if staleness_check else None,
    )

    own = store is None
    st = store or _store()
    try:
        try:
            st.insert_task(task)
            return SubmitResult(task=task, created=True)
        except DedupConflict as e:
            if on_conflict == "raise":
                raise
            return SubmitResult(task=e.existing or task, created=False)
    finally:
        if own:
            st.close()


def _as_int(v) -> int | None:
    return None if v is None else int(v)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def get(task_id: int, store: QueueStore | None = None) -> Task | None:
    own = store is None
    st = store or _store()
    try:
        return st.get_task(task_id)
    finally:
        if own:
            st.close()


def list_tasks(states=None, limit=None, store: QueueStore | None = None) -> list[Task]:
    own = store is None
    st = store or _store()
    try:
        return st.list_tasks(states=states, limit=limit)
    finally:
        if own:
            st.close()


# --------------------------------------------------------------------------- #
# Intents (daemon applies on next tick)
# --------------------------------------------------------------------------- #
def _intent(task_id: int, store: QueueStore | None = None, **fields) -> bool:
    own = store is None
    st = store or _store()
    try:
        t = st.get_task(task_id)
        if not t or t.is_terminal:
            return False
        st.update_fields(task_id, **fields)
        return True
    finally:
        if own:
            st.close()


def cancel(task_id: int, store: QueueStore | None = None) -> bool:
    return _intent(task_id, store, cancel_requested=1)


def kill(task_id: int, store: QueueStore | None = None) -> bool:
    return _intent(task_id, store, kill_requested=1)


def set_priority(task_id: int, priority, store: QueueStore | None = None) -> bool:
    return _intent(task_id, store, target_priority=int(parse_priority(priority)))


def hold(task_id: int, store: QueueStore | None = None) -> bool:
    return _intent(task_id, store, hold_requested=1)


def release(task_id: int, store: QueueStore | None = None) -> bool:
    return _intent(task_id, store, release_requested=1)


def clear(states=(State.QUEUED,), priority=None, store: QueueStore | None = None) -> int:
    """Request-cancel every matching task. Returns count flagged."""
    own = store is None
    st = store or _store()
    try:
        n = 0
        pri = int(parse_priority(priority)) if priority is not None else None
        for t in st.list_tasks(states=states):
            if pri is not None and t.priority != pri:
                continue
            st.update_fields(t.id, cancel_requested=1)
            n += 1
        return n
    finally:
        if own:
            st.close()


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
def daemon_health(store: QueueStore | None = None) -> dict:
    own = store is None
    st = store or _store()
    try:
        return st.daemon_health(HEARTBEAT_STALE_S)
    finally:
        if own:
            st.close()


def status(store: QueueStore | None = None) -> dict:
    from . import resources

    own = store is None
    st = store or _store()
    try:
        usage = st.resource_usage()
        counts: dict[str, int] = {}
        for t in st.list_tasks(limit=10000):
            counts[t.state] = counts.get(t.state, 0) + 1
        return {
            "daemon": st.daemon_health(HEARTBEAT_STALE_S),
            "resources": resources.summary(usage),
            "counts": counts,
        }
    finally:
        if own:
            st.close()
