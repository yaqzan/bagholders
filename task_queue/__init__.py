"""task_queue — single-node priority job scheduler for the Trader box.

A lightweight, crash-safe queue that runs long compute (recalcs, sweeps, MC,
research-pack hydration, old-version profile reruns) by priority with
resource-aware admission, so heavy jobs stop colliding and saturating CPU + the
(deliberately tight-timeout) MySQL DB. Low-priority "fill-in" work runs on a
reduced core grant when the box is idle and yields the moment higher-priority
work arrives.

Design notes:
    * State lives in SQLite (.cache/task_queue.db) — NEVER MySQL. The queue
      exists to protect MySQL bandwidth; putting its own bookkeeping there would
      defeat the purpose. Mirrors the database/barrier_cache.py connection
      pattern (WAL, synchronous=NORMAL, idempotent schema).
    * A persistent daemon (task_queue.daemon) is the SOLE authoritative writer
      of task state/pid. The CLI writes *intents* (cancel/priority/pause) that
      the daemon applies — this dissolves the CLI-vs-daemon write race.
    * Core load is governed by injecting the env vars the codebase already
      honors (TRADER_RECALC_MAX_WORKERS, MC_WORKERS), so heavy jobs need ZERO
      changes to be governed.

Public surface:
    * task_queue.api    — programmatic submit/list/cancel/... (queue_lib) for
                          agents and workflows.
    * queue_manager     — argparse CLI exposed as `trader queue ...`.
    * task_queue.daemon — the reconcile/admission loop (`trader queue daemon`).

Submodules are imported lazily (psutil / sqlite work stays off the import path
of `import task_queue`) to keep startup cheap and avoid cycles.
"""
from __future__ import annotations

__all__ = ["api", "daemon", "store", "model", "resources", "launcher", "winproc"]

# Subsystem version — bump on schema-affecting changes.
__version__ = "1.0.0-phase1"
