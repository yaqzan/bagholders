"""task_queue.winproc — OS-level primitives: named locks + process identity.

Two responsibilities:

1. Single-instance / overlap locking via a Windows *named mutex* (a kernel
   object the OS releases automatically when the holder dies — no stale-lock
   cleanup, which is exactly what a plain file lock gets wrong). Used to guard
   the daemon singleton (Global\\TraderQueueDaemon) and the `trader update`
   overlap (Global\\TraderUpdate). A crash-safe O_EXCL file-lock fallback covers
   non-Windows dev and the rare case the named mutex can't be created.

2. Process identity. Windows recycles PIDs fast, so every liveness / kill /
   suspend check must verify the process is *the same one* via its create_time.
   `(pid, create_time)` is the durable identity used throughout the queue.

psutil is the primary backend for identity + suspend/resume; a ctypes/ntdll
fallback keeps suspend/resume working if psutil is ever unavailable.
"""
from __future__ import annotations

import os
import sys
import re
import json
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"
ERROR_ALREADY_EXISTS = 183

_ROOT = Path(__file__).resolve().parents[1]
_LOCK_DIR = _ROOT / ".cache" / "locks"

# Tolerance when comparing process create_time (psutil precision varies slightly
# between sampling points; a few ms of drift is the same process).
_CTIME_TOL_S = 1.5


# --------------------------------------------------------------------------- #
# psutil (optional)
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - environment dependent
    import psutil  # noqa: F401
    _HAVE_PSUTIL = True
except Exception:  # pragma: no cover
    psutil = None  # type: ignore
    _HAVE_PSUTIL = False


def have_psutil() -> bool:
    return _HAVE_PSUTIL


# --------------------------------------------------------------------------- #
# Process identity
# --------------------------------------------------------------------------- #
def proc_create_time(pid: int) -> float | None:
    """create_time() of `pid`, or None if it doesn't exist / not accessible."""
    if pid is None:
        return None
    if _HAVE_PSUTIL:
        try:
            return float(psutil.Process(int(pid)).create_time())
        except Exception:
            return None
    # Fallback: no psutil — we can only answer "exists" (see pid_is_alive).
    return None


def current_create_time() -> float | None:
    """create_time() of THIS process — recorded by a launched child as its
    identity so the daemon can re-adopt it safely after a crash."""
    return proc_create_time(os.getpid())


def _pid_exists(pid: int) -> bool:
    if pid is None:
        return False
    if _HAVE_PSUTIL:
        try:
            return psutil.pid_exists(int(pid))
        except Exception:
            return False
    # Portable fallback (no psutil).
    try:
        if IS_WINDOWS:
            # os.kill(pid, 0) raises if the pid is invalid; OSError(errno=EINVAL?)
            os.kill(int(pid), 0)
            return True
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    except OSError:
        return False


def pid_is_alive(pid: int | None, create_time: float | None = None) -> bool:
    """True iff `pid` is alive AND (when create_time given) is the SAME process.

    Always pass create_time when you have it — without it a recycled PID reads as
    'alive' and you risk acting on an unrelated process.
    """
    if pid is None:
        return False
    if not _pid_exists(int(pid)):
        return False
    if create_time is None:
        return True
    actual = proc_create_time(int(pid))
    if actual is None:
        # Can't confirm identity (no psutil / access). Be conservative: treat as
        # NOT the same process so we never suspend/kill a possibly-recycled PID.
        return False
    return abs(actual - float(create_time)) <= _CTIME_TOL_S


def identity(pid: int | None) -> tuple[int, float | None] | None:
    """Snapshot (pid, create_time) for a live pid, else None."""
    if pid is None or not _pid_exists(int(pid)):
        return None
    return (int(pid), proc_create_time(int(pid)))


# --------------------------------------------------------------------------- #
# Process-tree suspend / resume (single-process primitives; tree-walk in preempt)
# --------------------------------------------------------------------------- #
def suspend_pid(pid: int, create_time: float | None = None) -> bool:
    """Suspend a single process (all threads). Identity-checked. Phase 2 helper."""
    if not pid_is_alive(pid, create_time):
        return False
    if _HAVE_PSUTIL:
        try:
            psutil.Process(int(pid)).suspend()
            return True
        except Exception:
            pass
    return _ntdll_suspend(int(pid), resume=False)


def resume_pid(pid: int, create_time: float | None = None) -> bool:
    if not pid_is_alive(pid, create_time):
        return False
    if _HAVE_PSUTIL:
        try:
            psutil.Process(int(pid)).resume()
            return True
        except Exception:
            pass
    return _ntdll_suspend(int(pid), resume=True)


def _ntdll_suspend(pid: int, resume: bool) -> bool:  # pragma: no cover - Windows-only fallback
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_SUSPEND_RESUME = 0x0800
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, int(pid))
        if not h:
            return False
        try:
            fn = ntdll.NtResumeProcess if resume else ntdll.NtSuspendProcess
            status = fn(h)
            return status == 0
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


def kill_tree(pid: int | None, create_time: float | None = None, timeout: float = 5.0) -> bool:
    """Terminate a process AND its whole tree (multiprocessing.Pool workers are
    separate child processes — killing only the root leaves them orphaned).
    Identity-checked: a recycled PID with a mismatched create_time is left alone.
    """
    if pid is None:
        return True
    if not pid_is_alive(pid, create_time):
        return True
    if _HAVE_PSUTIL:
        try:
            root = psutil.Process(int(pid))
            procs = root.children(recursive=True) + [root]
            for p in procs:  # children first via terminate() then escalate
                try:
                    p.terminate()
                except Exception:
                    pass
            _gone, alive = psutil.wait_procs(procs, timeout=timeout)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
            return True
        except Exception:
            pass
    # Fallback: Windows taskkill /T (tree) /F, else SIGKILL the root.
    if IS_WINDOWS:  # pragma: no cover - fallback path
        try:
            import subprocess

            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
            )
            return True
        except Exception:
            return False
    try:  # pragma: no cover - POSIX dev fallback
        os.kill(int(pid), 9)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Tag-based orphan reaping
#
# Every task process inherits TRADER_QUEUE_TASK_ID + TRADER_QUEUE_ATTEMPT in its
# environment (set in task_queue.launcher.launch), and so do ALL its descendants.
# A parent-chain tree-walk (kill_tree) misses descendants when an intermediate
# process dies and reparents them (git-bash / MSYS layers, multiprocessing
# workers) — the broken link makes them unreachable from the supervisor pid.
# Matching on the inherited env tag finds those orphans regardless of the parent
# chain, which is the only reliable way to reap them on Windows.
# --------------------------------------------------------------------------- #
ENV_TASK_ID = "TRADER_QUEUE_TASK_ID"
ENV_ATTEMPT = "TRADER_QUEUE_ATTEMPT"


def iter_tagged():
    """Yield (pid, task_id, attempt) for live processes carrying the queue task
    env tag. Best-effort: processes whose environ() is inaccessible (system procs
    or already-exited) are skipped. Excludes the calling process."""
    if not _HAVE_PSUTIL:
        return
    me = os.getpid()
    for p in psutil.process_iter(["pid"]):
        try:
            pid = int(p.pid)
            if pid == me:
                continue
            env = p.environ()
        except Exception:
            continue
        tid = env.get(ENV_TASK_ID)
        if tid:
            yield pid, str(tid), str(env.get(ENV_ATTEMPT) or "")


def kill_tagged(task_id, attempt: str | None = None) -> list[int]:
    """Kill every live process whose env tag matches `task_id` (and `attempt`
    when given), returning the reaped pids. Used right after a preempt kill_tree
    to sweep descendants the parent-chain walk could not reach."""
    tid = str(task_id)
    want = None if attempt is None else str(attempt)
    killed: list[int] = []
    for pid, ptid, pattempt in iter_tagged():
        if ptid != tid:
            continue
        if want is not None and pattempt != want:
            continue
        if kill_tree(pid, proc_create_time(pid)):
            killed.append(pid)
    return killed


# --------------------------------------------------------------------------- #
# Named locks (single-daemon guard, update overlap)
# --------------------------------------------------------------------------- #
class NamedLock:
    """Cross-process single-holder lock.

    Windows: a named mutex (auto-released by the OS on holder death).
    Elsewhere / on mutex-creation failure: a crash-safe O_EXCL pidfile that is
    stolen only if the recorded (pid, create_time) is no longer alive.

    Usage:
        lock = NamedLock("Global\\\\TraderUpdate")
        if not lock.acquire():
            ...  # someone else holds it
            return
        try:
            ...
        finally:
            lock.release()

    or as a context manager (check truthiness):
        with NamedLock(name) as lock:
            if not lock:
                return
            ...
    """

    def __init__(self, name: str):
        self.name = name
        self.acquired = False
        self._handle = None          # Windows mutex HANDLE
        self._path: Path | None = None  # fallback pidfile

    # ---- public ----------------------------------------------------------
    def acquire(self) -> bool:
        if self.acquired:
            return True
        if IS_WINDOWS:
            result = self._acquire_mutex()  # 'acquired' | 'held' | 'unavailable'
            if result == "acquired":
                self.acquired = True
                return True
            if result == "held":
                return False  # authoritative: another process holds it
            # 'unavailable' (mutex couldn't be created at all) -> file-lock fallback
        if self._acquire_file():
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            if self._handle is not None:
                self._close_handle(self._handle)
                self._handle = None
            if self._path is not None:
                # Only unlink if it's still ours (best-effort).
                try:
                    if self._path.exists():
                        self._path.unlink()
                except Exception:
                    pass
                self._path = None
        finally:
            self.acquired = False

    def __enter__(self) -> "NamedLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def __bool__(self) -> bool:
        return self.acquired

    # ---- windows mutex ---------------------------------------------------
    def _acquire_mutex(self) -> str:  # pragma: no cover - Windows-only
        """Returns 'acquired', 'held' (someone else owns it), or 'unavailable'
        (the mutex object couldn't be created — caller falls back to a file lock)."""
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            handle = k32.CreateMutexW(None, True, self.name)
            err = ctypes.get_last_error()
            if not handle:
                return "unavailable"  # couldn't create -> file-lock fallback
            if err == ERROR_ALREADY_EXISTS:
                # Object already existed (another process holds it) -> not ours.
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                k32.CloseHandle(handle)
                return "held"
            self._handle = handle
            return "acquired"
        except Exception:
            return "unavailable"

    @staticmethod
    def _close_handle(handle) -> None:  # pragma: no cover - Windows-only
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle(handle)
        except Exception:
            pass

    # ---- portable pidfile fallback --------------------------------------
    def _acquire_file(self) -> bool:
        _LOCK_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", self.name)
        path = _LOCK_DIR / f"{safe}.lock"
        payload = json.dumps(
            {"pid": os.getpid(), "ctime": current_create_time(), "ts": time.time()}
        ).encode()
        for _attempt in range(2):
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                try:
                    os.write(fd, payload)
                finally:
                    os.close(fd)
                self._path = path
                return True
            except FileExistsError:
                # Steal only if the recorded holder is dead.
                holder_dead = True
                try:
                    rec = json.loads(path.read_text() or "{}")
                    holder_dead = not pid_is_alive(rec.get("pid"), rec.get("ctime"))
                except Exception:
                    holder_dead = True  # unreadable -> assume stale
                if holder_dead:
                    try:
                        path.unlink()
                    except Exception:
                        return False
                    continue  # retry once
                return False
        return False
