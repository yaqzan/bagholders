"""task_queue.store — SQLite persistence (the single source of truth).

State lives in .cache/task_queue.db, NOT MySQL. Mirrors the proven
database/barrier_cache.py connection pattern (WAL, synchronous=NORMAL) and adds:

    * busy_timeout so a transient lock retries instead of erroring,
    * isolation_level=None + explicit BEGIN IMMEDIATE for the daemon's per-tick
      batched writes (acquires the writer lock up front, avoiding deferred-txn
      upgrade deadlocks against the CLI),
    * a PARTIAL UNIQUE index on dedup_key over active states so the DB rejects a
      duplicate submit atomically (no read-then-write race),
    * resource occupancy DERIVED from task rows (no mutable counters to repair
      after a crash),
    * a daemon_singleton row taken via a compare-and-set under the writer lock.

The Task dataclass field names are the table columns (a unit test asserts this).
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .model import (
    ACTIVE_STATES,
    OCCUPYING_STATES,
    Task,
    now,
)

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
DB_PATH = CACHE_DIR / "task_queue.db"

# Where each task's .codex/runs/<id>/ artifacts live.
RUNS_DIR = ROOT / ".codex" / "runs"

_ACTIVE_SQL_LIST = ", ".join(f"'{s}'" for s in ACTIVE_STATES)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS tasks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    command_json         TEXT    NOT NULL DEFAULT '[]',
    cwd                  TEXT,
    env_json             TEXT,
    priority             INTEGER NOT NULL DEFAULT 30,
    cpu_request          INTEGER NOT NULL DEFAULT 1,
    cpu_grant            INTEGER,
    db_class             TEXT    NOT NULL DEFAULT 'none',
    io_class             TEXT    NOT NULL DEFAULT 'none',
    time_window          TEXT    NOT NULL DEFAULT 'any',
    state                TEXT    NOT NULL DEFAULT 'queued',
    held                 INTEGER NOT NULL DEFAULT 0,
    throttled            INTEGER NOT NULL DEFAULT 0,
    pid                  INTEGER,
    create_time          REAL,
    run_dir              TEXT,
    attempt_token        TEXT,
    attempts             INTEGER NOT NULL DEFAULT 0,
    max_attempts         INTEGER NOT NULL DEFAULT 3,
    restartable          INTEGER NOT NULL DEFAULT 0,
    exclusive            INTEGER NOT NULL DEFAULT 0,
    dedup_key            TEXT,
    timeout_s            INTEGER,
    ttl_s                INTEGER,
    not_before           REAL,
    staleness_check_json TEXT,
    reason               TEXT,
    created_by           TEXT,
    created_at           REAL    NOT NULL,
    started_at           REAL,
    finished_at          REAL,
    exit_code            INTEGER,
    error                TEXT,
    progress             TEXT,
    updated_at           REAL    NOT NULL,
    cancel_requested     INTEGER NOT NULL DEFAULT 0,
    kill_requested       INTEGER NOT NULL DEFAULT 0,
    target_priority      INTEGER,
    hold_requested       INTEGER NOT NULL DEFAULT 0,
    release_requested    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_sched ON tasks(state, priority, created_at);

-- One active task per dedup_key. The DB rejects a duplicate submit atomically.
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_dedup
    ON tasks(dedup_key)
    WHERE dedup_key IS NOT NULL AND state IN ({_ACTIVE_SQL_LIST});

CREATE TABLE IF NOT EXISTS daemon_singleton (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    pid          INTEGER,
    create_time  REAL,
    boot_id      TEXT,
    heartbeat_ts REAL NOT NULL DEFAULT 0,
    started_at   REAL
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    task_id INTEGER,
    kind    TEXT    NOT NULL,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
"""

# Idempotent column additions for forward migrations (mirror barrier_cache).
_MIGRATIONS: list[str] = [
    "ALTER TABLE tasks ADD COLUMN throttled INTEGER NOT NULL DEFAULT 0",
]

_VALID_COLUMNS = set(Task.column_names())


class DedupConflict(Exception):
    """Raised when a submit collides with an existing active dedup_key."""

    def __init__(self, dedup_key: str, existing: Task | None):
        self.dedup_key = dedup_key
        self.existing = existing
        eid = existing.id if existing else "?"
        super().__init__(f"active task already exists for dedup_key={dedup_key!r} (id={eid})")


class QueueStore:
    """A connection to the queue DB. Cheap to open; safe to keep one per daemon
    and one short-lived per CLI invocation (WAL + busy_timeout handle overlap)."""

    def __init__(self, path: Path | str | None = None, busy_timeout_s: float = 5.0):
        # Default to the real queue DB, but honor TRADER_QUEUE_DB so tests / a
        # second box can run against an isolated store.
        self.path = str(path or os.environ.get("TRADER_QUEUE_DB") or DB_PATH)
        self.conn = sqlite3.connect(
            self.path,
            timeout=busy_timeout_s,
            isolation_level=None,  # autocommit; we manage BEGIN IMMEDIATE ourselves
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self.conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_s * 1000)}")

    # ---- lifecycle -------------------------------------------------------
    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "QueueStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def immediate(self):
        """BEGIN IMMEDIATE ... COMMIT/ROLLBACK — the daemon's batched tick write."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    # ---- task CRUD -------------------------------------------------------
    def insert_task(self, task: Task) -> Task:
        task.created_at = task.created_at or now()
        task.updated_at = now()
        cols = [c for c in Task.column_names() if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        vals = [getattr(task, c) for c in cols]
        sql = f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({placeholders})"
        try:
            cur = self.conn.execute(sql, vals)
        except sqlite3.IntegrityError:
            existing = self.find_active_by_dedup(task.dedup_key) if task.dedup_key else None
            raise DedupConflict(task.dedup_key or "", existing)
        task.id = int(cur.lastrowid)
        self.log_event(task.id, "submit", task.reason or "")
        return task

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone()
        return Task.from_row(row) if row else None

    def latest_with_dedup(self, dedup_key: str) -> Task | None:
        """Most recent task with this dedup_key in ANY state (for the daemon's
        missing-update watchdog)."""
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE dedup_key=? ORDER BY created_at DESC LIMIT 1",
            (dedup_key,),
        ).fetchone()
        return Task.from_row(row) if row else None

    def find_active_by_dedup(self, dedup_key: str) -> Task | None:
        row = self.conn.execute(
            f"SELECT * FROM tasks WHERE dedup_key=? AND state IN ({_ACTIVE_SQL_LIST}) "
            "ORDER BY id DESC LIMIT 1",
            (dedup_key,),
        ).fetchone()
        return Task.from_row(row) if row else None

    def list_tasks(
        self,
        states: Iterable[str] | None = None,
        limit: int | None = None,
        order: str = "id DESC",
    ) -> list[Task]:
        sql = "SELECT * FROM tasks"
        params: list[Any] = []
        if states:
            states = list(states)
            sql += f" WHERE state IN ({', '.join('?' for _ in states)})"
            params.extend(states)
        sql += f" ORDER BY {order}"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [Task.from_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def queued_tasks(self) -> list[Task]:
        """Queued, not held — admission candidates ordered by effective priority
        then FIFO. (Aging is applied in Python by the daemon.)"""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE state='queued' AND held=0 "
            "ORDER BY priority ASC, created_at ASC"
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def occupying_tasks(self) -> list[Task]:
        """Tasks currently holding machine resources (launching/running/suspended)."""
        states = ", ".join("?" for _ in OCCUPYING_STATES)
        rows = self.conn.execute(
            f"SELECT * FROM tasks WHERE state IN ({states})", list(OCCUPYING_STATES)
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def nonterminal_tasks(self) -> list[Task]:
        states = ", ".join("?" for _ in ACTIVE_STATES)
        rows = self.conn.execute(
            f"SELECT * FROM tasks WHERE state IN ({states}) ORDER BY id ASC",
            list(ACTIVE_STATES),
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def update_fields(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields.setdefault("updated_at", now())
        bad = set(fields) - _VALID_COLUMNS
        if bad:
            raise ValueError(f"unknown task columns: {sorted(bad)}")
        assigns = ", ".join(f"{c}=?" for c in fields)
        vals = list(fields.values()) + [int(task_id)]
        self.conn.execute(f"UPDATE tasks SET {assigns} WHERE id=?", vals)

    def set_state(self, task_id: int, state: str, **fields: Any) -> None:
        fields["state"] = state
        self.update_fields(task_id, **fields)

    def delete_task(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id=?", (int(task_id),))

    # ---- derived resource occupancy -------------------------------------
    def resource_usage(self) -> dict[str, int]:
        """Cores/db/io in use RIGHT NOW, derived from occupying task rows.
        Crash-correct by construction — there is no counter to repair.

        Preemption-aware (Phase 2):
          * a SUSPENDED task yields CPU + IO, but its frozen MySQL connection +
            any held row locks still count against the DB budget;
          * a THROTTLED task (RUNNING, demoted to IDLE) yields ~all CPU but keeps
            its DB + IO footprint.
        """
        from .model import State as _S

        cores = db = io = 0
        for t in self.occupying_tasks():
            grant = int(t.cpu_grant if t.cpu_grant is not None else t.cpu_request)
            if t.state == _S.SUSPENDED:
                db += t.db_weight
            elif t.throttled:
                db += t.db_weight
                io += t.io_weight
            else:
                cores += grant
                db += t.db_weight
                io += t.io_weight
        return {"cores": cores, "db": db, "io": io}

    # ---- daemon singleton (compare-and-set under the writer lock) --------
    def read_singleton(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM daemon_singleton WHERE id=1").fetchone()
        return dict(row) if row else None

    def acquire_singleton(
        self, pid: int, create_time: float | None, boot_id: str, stale_after_s: float
    ) -> bool:
        """Take ownership iff there is no LIVE, fresh owner (or the owner is us)."""
        from . import winproc

        self.conn.execute(
            "INSERT OR IGNORE INTO daemon_singleton(id, heartbeat_ts) VALUES (1, 0)"
        )
        ts = now()
        with self.immediate():
            row = self.conn.execute(
                "SELECT pid, create_time, heartbeat_ts FROM daemon_singleton WHERE id=1"
            ).fetchone()
            owner_pid = row["pid"] if row else None
            owner_ct = row["create_time"] if row else None
            owner_hb = row["heartbeat_ts"] if row else 0
            fresh = (ts - (owner_hb or 0)) <= stale_after_s
            owner_alive = (
                owner_pid is not None
                and fresh
                and winproc.pid_is_alive(owner_pid, owner_ct)
            )
            if owner_alive and owner_pid != pid:
                return False
            self.conn.execute(
                "UPDATE daemon_singleton SET pid=?, create_time=?, boot_id=?, "
                "heartbeat_ts=?, started_at=? WHERE id=1",
                (int(pid), create_time, boot_id, ts, ts),
            )
        return True

    def heartbeat(self, pid: int, boot_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE daemon_singleton SET heartbeat_ts=? WHERE id=1 AND pid=? AND boot_id=?",
            (now(), int(pid), boot_id),
        )
        return cur.rowcount > 0

    def daemon_health(self, stale_after_s: float) -> dict[str, Any]:
        """For `trader queue status` and the submit wrapper's daemon probe."""
        from . import winproc

        row = self.read_singleton()
        if not row or row.get("pid") is None:
            return {"alive": False, "reason": "no daemon registered", "pid": None}
        age = now() - (row.get("heartbeat_ts") or 0)
        fresh = age <= stale_after_s
        alive = winproc.pid_is_alive(row.get("pid"), row.get("create_time"))
        healthy = bool(fresh and alive)
        reason = "healthy"
        if not alive:
            reason = "process dead"
        elif not fresh:
            reason = f"heartbeat stale ({age:.0f}s)"
        return {
            "alive": healthy,
            "reason": reason,
            "pid": row.get("pid"),
            "boot_id": row.get("boot_id"),
            "heartbeat_age_s": age,
            "started_at": row.get("started_at"),
        }

    # ---- events ----------------------------------------------------------
    def log_event(self, task_id: int | None, kind: str, detail: str = "") -> None:
        try:
            self.conn.execute(
                "INSERT INTO events(ts, task_id, kind, detail) VALUES (?,?,?,?)",
                (now(), task_id, kind, (detail or "")[:2000]),
            )
        except Exception:
            pass  # event logging must never break the caller

    def recent_events(self, task_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if task_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (int(task_id), int(limit)),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]
