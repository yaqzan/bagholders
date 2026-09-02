"""task_queue.model — task schema, priority tiers, states, resource classes.

Pure data + parsing only. No SQLite, no psutil, no process work here so this
module stays cheap to import. The Task dataclass field order/names MUST match the
`tasks` table columns in task_queue.store (a unit test asserts they agree).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, fields
from enum import IntEnum
from typing import Any


# --------------------------------------------------------------------------- #
# Priority tiers
# --------------------------------------------------------------------------- #
class Priority(IntEnum):
    """Lower value = higher priority (runs first, preempts higher numbers)."""

    CRITICAL = 0    # rare; a ship recalc someone is actively waiting on
    HIGH = 10       # interactive, agent-requested compute
    SCHEDULED = 20  # the ~45-min `trader update` job ("high, but not so high")
    NORMAL = 30
    LOW = 40        # fill-in work (e.g. recalc an old version's Sentinel run)
    IDLE = 50       # only when nothing else wants the box


_PRIORITY_ALIASES = {
    "critical": Priority.CRITICAL, "crit": Priority.CRITICAL,
    "high": Priority.HIGH, "hi": Priority.HIGH,
    "scheduled": Priority.SCHEDULED, "sched": Priority.SCHEDULED,
    "normal": Priority.NORMAL, "norm": Priority.NORMAL, "default": Priority.NORMAL,
    "low": Priority.LOW,
    "idle": Priority.IDLE,
}


def parse_priority(token: Any, default: Priority = Priority.NORMAL) -> Priority:
    """Accept a tier name ('low'), int value (40), or Priority — return Priority."""
    if token is None or token == "":
        return default
    if isinstance(token, Priority):
        return token
    if isinstance(token, int):
        return Priority(_nearest_tier_value(token))
    s = str(token).strip().lower()
    if s in _PRIORITY_ALIASES:
        return _PRIORITY_ALIASES[s]
    if s.lstrip("-").isdigit():
        return Priority(_nearest_tier_value(int(s)))
    raise ValueError(f"Unknown priority {token!r}; use one of {sorted(_PRIORITY_ALIASES)}")


def _nearest_tier_value(v: int) -> int:
    return min((p.value for p in Priority), key=lambda x: abs(x - v))


def priority_name(value: int) -> str:
    try:
        return Priority(value).name.lower()
    except ValueError:
        return f"p{value}"


# Tasks in tiers at/above LOW (numerically >= LOW) are "low priority": they get a
# reduced core grant and are the first preempted. Used by resources + daemon.
LOW_PRIORITY_CUTOFF = Priority.LOW


# --------------------------------------------------------------------------- #
# Task state machine
# --------------------------------------------------------------------------- #
class State:
    QUEUED = "queued"          # waiting for admission
    LAUNCHING = "launching"    # spawn intent committed, pid not yet observed
    RUNNING = "running"
    SUSPENDED = "suspended"    # process frozen by preemption (Phase 2)
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Counts against the dedup partial-unique index (one active task per dedup_key).
ACTIVE_STATES = (State.QUEUED, State.LAUNCHING, State.RUNNING, State.SUSPENDED)
# Holds real machine resources right now (charged against the budget each tick).
OCCUPYING_STATES = (State.LAUNCHING, State.RUNNING, State.SUSPENDED)
TERMINAL_STATES = (State.DONE, State.FAILED, State.CANCELLED)


# --------------------------------------------------------------------------- #
# Resource classes
# --------------------------------------------------------------------------- #
# DB weight: a recalc opens one MySQL connection PER worker, so "heavy" means
# "opens a worker-pool's worth of connections". The DB budget (resources.py) is
# sized so at most ONE heavy-DB job runs concurrently — protecting MySQL, whose
# read/write timeouts are deliberately tight (30s) and cascade to 2013 errors
# under contention. A suspended job STILL charges its DB weight (a frozen worker
# keeps its connection + any held row locks).
DB_WEIGHTS = {"none": 0, "light": 1, "heavy": 2}
IO_WEIGHTS = {"none": 0, "light": 1, "heavy": 2}
DB_CLASSES = tuple(DB_WEIGHTS)
IO_CLASSES = tuple(IO_WEIGHTS)


# Time windows gate WHEN a task may be admitted (in addition to resources).
WINDOW_ANY = "any"
WINDOW_OFF_MARKET = "off_market"   # only when the NYSE session is closed
TIME_WINDOWS = (WINDOW_ANY, WINDOW_OFF_MARKET)


# --------------------------------------------------------------------------- #
# Tuning constants
# --------------------------------------------------------------------------- #
DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 30.0          # next_eligible = now + BASE * 2**(attempts-1)
BACKOFF_CAP_S = 3600.0
LAUNCH_GRACE_S = 30.0          # a 'launching' row older than this w/ no pid → reconcile
HEARTBEAT_STALE_S = 30.0       # daemon heartbeat older than this == wedged/dead
DEFAULT_RECONCILE_INTERVAL_S = 5.0
# Aging: a queued task's effective priority improves by one step every
# AGING_STEP_S of wait, capped so it can never cross into the next *tier* above
# its base (prevents an idle job from ever preempting a real high-pri job).
AGING_STEP_S = 600.0           # 10 min of waiting == 1 priority point better
AGING_MAX_BOOST = 9            # never close the 10-point gap between tiers


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Task row
# --------------------------------------------------------------------------- #
@dataclass
class Task:
    """One unit of queued/queued-and-running compute. Mirrors the tasks table."""

    # identity / payload
    id: int | None = None
    command_json: str = "[]"            # JSON list of argv tokens (verbatim)
    cwd: str | None = None              # working dir; None == project root
    env_json: str | None = None         # JSON dict of extra env vars
    # scheduling
    priority: int = int(Priority.NORMAL)
    cpu_request: int = 1
    cpu_grant: int | None = None        # cores actually granted at admission
    db_class: str = "none"
    io_class: str = "none"
    time_window: str = WINDOW_ANY
    # lifecycle
    state: str = State.QUEUED
    held: int = 0                       # queue-level pause (won't be admitted)
    throttled: int = 0                  # RUNNING but demoted to IDLE + pinned (Phase 2 preemption)
    pid: int | None = None
    create_time: float | None = None    # process create_time for (pid,ctime) identity
    run_dir: str | None = None          # .codex/runs/<id>
    attempt_token: str | None = None    # UUID for the current launch attempt
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    restartable: int = 0                # 1 == idempotent, safe to auto-requeue
    exclusive: int = 0                  # 1 == only one active task per dedup_key
    dedup_key: str | None = None
    timeout_s: int | None = None        # wall-clock job ceiling (runaway guard)
    ttl_s: int | None = None            # expire if still queued past created_at+ttl
    not_before: float | None = None     # epoch; backoff / delayed start
    staleness_check_json: str | None = None  # argv for a bounded "still needed?" predicate
    # metadata
    reason: str | None = None
    created_by: str | None = None
    created_at: float = field(default_factory=now)
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    error: str | None = None
    progress: str | None = None
    updated_at: float = field(default_factory=now)
    # CLI intents (CLI writes, daemon applies + clears)
    cancel_requested: int = 0
    kill_requested: int = 0
    target_priority: int | None = None
    hold_requested: int = 0
    release_requested: int = 0

    # ---- convenience -----------------------------------------------------
    @property
    def argv(self) -> list[str]:
        try:
            v = json.loads(self.command_json or "[]")
            return [str(x) for x in v] if isinstance(v, list) else []
        except Exception:
            return []

    @property
    def env(self) -> dict[str, str]:
        if not self.env_json:
            return {}
        try:
            d = json.loads(self.env_json)
            return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
        except Exception:
            return {}

    @property
    def staleness_argv(self) -> list[str]:
        if not self.staleness_check_json:
            return []
        try:
            v = json.loads(self.staleness_check_json)
            return [str(x) for x in v] if isinstance(v, list) else []
        except Exception:
            return []

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def occupies_resources(self) -> bool:
        return self.state in OCCUPYING_STATES

    @property
    def db_weight(self) -> int:
        return DB_WEIGHTS.get(self.db_class, 0)

    @property
    def io_weight(self) -> int:
        return IO_WEIGHTS.get(self.io_class, 0)

    @property
    def is_low_priority(self) -> bool:
        return self.priority >= int(LOW_PRIORITY_CUTOFF)

    def effective_priority(self, ref: float | None = None, base: int | None = None) -> int:
        """Base priority improved by aging (smaller == higher). Never crosses
        into the next tier above the base (capped by AGING_MAX_BOOST). `base`
        overrides self.priority (e.g. the daemon's market-hours floor), so aging
        runs on the floored tier and the gap to a higher tier is preserved."""
        b = self.priority if base is None else int(base)
        if self.state != State.QUEUED:
            return b
        ref = now() if ref is None else ref
        waited = max(0.0, ref - self.created_at)
        boost = min(AGING_MAX_BOOST, int(waited // AGING_STEP_S))
        return b - boost

    # ---- row mapping -----------------------------------------------------
    @classmethod
    def column_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        """Build a Task from a sqlite3.Row / mapping / sequence."""
        names = cls.column_names()
        if hasattr(row, "keys"):  # sqlite3.Row or dict-like
            data = {k: row[k] for k in row.keys() if k in names}
        else:                      # sequence in column order
            data = dict(zip(names, row))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {n: getattr(self, n) for n in self.column_names()}
