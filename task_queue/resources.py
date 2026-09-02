"""task_queue.resources — machine budget, admission fit, core-grant env injection.

The queue governs core load WITHOUT changing the heavy jobs: it injects the env
vars the codebase already honors (TRADER_RECALC_MAX_WORKERS, MC_WORKERS) so a
job's worker pool sizes itself to the grant. One number — `cpu_grant` — is the
worker cap AND the cores charged against the budget (no double-counting; CPU
affinity, a Phase-2 throttle lever, is deliberately NOT used for admission).

Resource occupancy is DERIVED from task rows every tick (task_queue.store), so
nothing here keeps a mutable counter to repair after a crash.

DB is the protected resource: `db_budget` defaults to 2 with heavy=2, so AT MOST
ONE heavy-DB job (a recalc that opens a worker-pool's worth of MySQL connections)
runs at a time. Override budgets via env on the box if needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta, timezone

from .model import WINDOW_ANY, WINDOW_OFF_MARKET


@dataclass
class Budget:
    cores: int
    db: int
    io: int


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except Exception:
        return default


def cpu_count() -> int:
    return os.cpu_count() or 4


def machine_budget() -> Budget:
    """Total schedulable resources. Reserves one core for the OS + daemon +
    `trader update` headroom. All three are env-overridable for tuning."""
    cpu = cpu_count()
    cores = _env_int("TRADER_QUEUE_CORE_BUDGET", max(1, cpu - 1))
    db = _env_int("TRADER_QUEUE_DB_BUDGET", 2)          # ~one heavy-DB job at a time
    io = _env_int("TRADER_QUEUE_IO_BUDGET", max(2, cpu // 2))
    return Budget(cores=cores, db=db, io=io)


def low_priority_core_cap() -> int:
    """Reduced core ceiling for low/idle work so headroom stays free for incoming
    high-priority work."""
    return _env_int("TRADER_QUEUE_LOW_CORE_CAP", max(1, cpu_count() // 4))


def preempt_mode() -> str:
    """How the daemon frees cores for a starved high-priority task:
      * 'throttle' (default) — demote the victim tree to IDLE + shrink affinity
        (gentle, reversible, safe even for DB-holders);
      * 'suspend' — freeze pure-compute (db_class=none) victims for a harder core
        release (DB-holders still get throttled, never suspended);
      * 'off' — no live preemption; a starved high-pri task just waits (Phase-1
        reserve-only behavior).
    DB-slot reclaim (for a high-pri heavy-DB task) always uses kill+requeue of a
    restartable lower-priority DB-holder, regardless of mode."""
    m = os.environ.get("TRADER_QUEUE_PREEMPT_MODE", "throttle").strip().lower()
    return m if m in ("throttle", "suspend", "off") else "throttle"


def core_grant(task) -> int:
    """Cores to grant `task`: its request, clamped to the machine, and further
    reduced for low/idle-tier work."""
    req = max(1, int(task.cpu_request or 1))
    budget = machine_budget()
    grant = min(req, max(1, budget.cores))
    if task.is_low_priority:
        grant = min(grant, low_priority_core_cap())
    return max(1, grant)


def cpu_oversubscribe() -> bool:
    """Let HIGH/CRITICAL work size its worker pool to the WHOLE box and contend, instead of
    each job being boxed into its own reservation.

    Rationale (2026-07-29): the db budget (2) is the real concurrency cap — at most two
    heavy-DB jobs ever run. Under a strict reservation model each of those is also boxed into
    its `--cpu`, so a 4-core request leaves ~27 cores idle even though nothing else can start.
    Reservations are the right model when many jobs compete; with a hard db bound they just
    strand capacity. Here the OS scheduler is the better arbiter: oversubscribed threads
    time-slice, and a job that is briefly serial (I/O wait, a single-threaded phase) yields to
    the other automatically rather than holding cores it is not using.

    ADMISSION accounting is deliberately NOT relaxed — `cpu_grant` still reflects the honest
    reservation so preemption bookkeeping (`_occupancy`) and fairness stay intact. Only the
    child's WORKER-POOL sizing is inflated.

    Default ON under .cache/RESEARCH_PRIORITY; TRADER_QUEUE_CPU_OVERSUBSCRIBE=0 disables.
    """
    v = os.environ.get("TRADER_QUEUE_CPU_OVERSUBSCRIBE", "1").strip().lower()
    return v not in ("0", "false", "off")


def worker_grant(task, admission_grant: int) -> int:
    """Cores a job's internal pool may size itself to — which is NOT the same as the cores
    reserved for it at admission. For HIGH/CRITICAL work under oversubscribe this is the whole
    schedulable budget; the OS then arbitrates between the (at most two, per the db bound)
    concurrent heavy jobs."""
    if not cpu_oversubscribe():
        return admission_grant
    prio = getattr(task, "priority", None)
    prio_val = getattr(prio, "value", prio)
    try:
        is_top = int(prio_val) <= 10          # CRITICAL (0) / HIGH (10)
    except (TypeError, ValueError):
        is_top = False
    if not is_top:
        return admission_grant
    return max(admission_grant, machine_budget().cores)


def core_env(grant: int) -> dict[str, str]:
    """Env overlay that caps a child job's worker pool to `grant` cores."""
    g = str(int(grant))
    env = {
        "TRADER_RECALC_MAX_WORKERS": g,
        "MC_WORKERS": g,
        "TRADER_QUEUE_CORES": g,
    }
    if int(grant) <= 1:
        env["MC_NO_MP"] = "1"  # a 1-core grant: skip multiprocessing overhead
    return env


# --------------------------------------------------------------------------- #
# Admission accounting (the daemon greedily admits in priority order, threading
# a mutable `usage` dict through fits()/add_usage()).
# --------------------------------------------------------------------------- #
def fits(task, usage: dict, budget: Budget, grant: int | None = None) -> bool:
    g = core_grant(task) if grant is None else grant
    if usage.get("cores", 0) + g > budget.cores:
        return False
    if usage.get("db", 0) + task.db_weight > budget.db:
        return False
    if usage.get("io", 0) + task.io_weight > budget.io:
        return False
    return True


def add_usage(usage: dict, task, grant: int) -> None:
    usage["cores"] = usage.get("cores", 0) + int(grant)
    usage["db"] = usage.get("db", 0) + task.db_weight
    usage["io"] = usage.get("io", 0) + task.io_weight


def summary(usage: dict | None = None) -> dict:
    """Budget + utilization for `trader queue status`."""
    b = machine_budget()
    u = usage or {}
    return {
        "cpu_count": cpu_count(),
        "budget": {"cores": b.cores, "db": b.db, "io": b.io},
        "used": {"cores": u.get("cores", 0), "db": u.get("db", 0), "io": u.get("io", 0)},
        "free": {
            "cores": b.cores - u.get("cores", 0),
            "db": b.db - u.get("db", 0),
            "io": b.io - u.get("io", 0),
        },
        "low_priority_core_cap": low_priority_core_cap(),
    }


# --------------------------------------------------------------------------- #
# Time windows — gate WHEN a task may run (in addition to resources).
# --------------------------------------------------------------------------- #
def window_open(window: str | None) -> bool:
    if not window or window == WINDOW_ANY:
        return True
    if window == WINDOW_OFF_MARKET:
        return is_off_market_now()
    return True  # unknown window -> don't block


def _nth_sunday(year: int, month: int, n: int) -> int:
    d = ddate(year, month, 1)
    first_sun = 1 + (6 - d.weekday()) % 7  # weekday(): Mon=0 .. Sun=6
    return first_sun + 7 * (n - 1)


def eastern_now() -> datetime:
    """Current US/Eastern wall-clock as a naive datetime, computed from UTC with
    the US DST rule directly (no zoneinfo / Windows tzdata dependency)."""
    u = datetime.now(timezone.utc)
    y = u.year
    # Spring forward 02:00 EST (=07:00 UTC) 2nd Sun Mar; fall back 02:00 EDT
    # (=06:00 UTC) 1st Sun Nov.
    dst_start = datetime(y, 3, _nth_sunday(y, 3, 2), 7, tzinfo=timezone.utc)
    dst_end = datetime(y, 11, _nth_sunday(y, 11, 1), 6, tzinfo=timezone.utc)
    edt = dst_start <= u < dst_end
    return (u + timedelta(hours=-4 if edt else -5)).replace(tzinfo=None)


def _market_hours() -> tuple[dtime, dtime]:
    def _parse(name: str, default: dtime) -> dtime:
        raw = os.environ.get(name, "").strip()
        try:
            hh, mm = raw.split(":")
            return dtime(int(hh), int(mm))
        except Exception:
            return default

    return _parse("TRADER_QUEUE_MARKET_OPEN", dtime(9, 30)), _parse(
        "TRADER_QUEUE_MARKET_CLOSE", dtime(16, 0)
    )


def is_off_market_now() -> bool:
    """True when the NYSE session is closed: weekend, holiday, or outside RTH."""
    et = eastern_now()
    if et.weekday() >= 5:  # Saturday / Sunday
        return True
    try:  # holiday awareness reuses trader's pure NYSE calendar
        import trader as _t  # noqa
        if _t._is_nyse_closed_weekday(et.date()):
            return True
    except Exception:
        pass  # fail-safe: hours check below still gates weekday nights
    open_t, close_t = _market_hours()
    return not (open_t <= et.time() < close_t)
