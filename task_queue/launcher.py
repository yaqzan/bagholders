"""task_queue.launcher — spawn a task as a supervised, finalizing child.

Each task runs under a thin *supervisor* process (this module, invoked as
`python -m task_queue.launcher --run <run_dir>`). The supervisor:

    1. writes launch.json (its own pid + create_time + attempt_token) as its
       FIRST act — the anchor the daemon uses to reconcile a launch after a
       crash without risking double-execution;
    2. writes the established .codex/runs/<id>/ contract (pid.txt, status.json),
       redirecting the real command's stdout/stderr to run.log / stderr.log;
    3. runs the real command (its own child); enforces the wall-clock timeout;
    4. ALWAYS finalizes in a `finally` (done.json on exit 0, else failed.json) —
       so a graceful finish is recorded instantly, independent of daemon health.

The daemon tracks the SUPERVISOR pid; the real command + any multiprocessing
workers are its descendants, so a tree-walk (preempt/kill) from the supervisor
pid reaches everything. The supervisor holds no MySQL connection itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import winproc

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".codex" / "runs"

# Artifact filenames (compatible with the existing .codex/runs contract).
F_TASK = "task.json"        # supervisor input (argv, cwd, timeout, ids)
F_META = "metadata.json"    # human/daemon record
F_LAUNCH = "launch.json"    # supervisor identity + attempt token (reconcile anchor)
F_PID = "pid.txt"
F_STATUS = "status.json"
F_RUNLOG = "run.log"
F_ERRLOG = "stderr.log"
F_WRAPLOG = "wrapper.log"
F_DONE = "done.json"
F_FAILED = "failed.json"


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).isoformat()


def _write_json_atomic(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def make_run_dir(task_id: int, attempt_token: str) -> Path:
    return RUNS_DIR / f"task_{task_id}_{attempt_token[:8]}"


def _normalize_argv(argv: list[str]) -> list[str]:
    """Run 'python ...' under the SAME interpreter as the daemon (the venv), so a
    Task Scheduler context without 'python' on PATH still works."""
    if argv and str(argv[0]).lower() in ("python", "python3", "py", "python.exe"):
        return [sys.executable] + list(argv[1:])
    return list(argv)


# --------------------------------------------------------------------------- #
# Parent side (daemon calls this)
# --------------------------------------------------------------------------- #
def launch(task, run_dir: Path, env_overlay: dict | None = None) -> subprocess.Popen:
    """Spawn the supervisor for `task`. Returns the Popen (daemon records pid +
    create_time). Must be called AFTER the task row is set state='launching' with
    this attempt_token + run_dir committed.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    argv = _normalize_argv(task.argv)

    task_doc = {
        "task_id": task.id,
        "attempt_token": task.attempt_token,
        "argv": argv,
        "cwd": task.cwd or str(ROOT),
        "timeout_s": task.timeout_s,
        "run_dir": str(run_dir),
    }
    _write_json_atomic(run_dir / F_TASK, task_doc)
    _write_json_atomic(
        run_dir / F_META,
        {
            "task_id": task.id,
            "attempt_token": task.attempt_token,
            "command": argv,
            "cwd": task.cwd or str(ROOT),
            "priority": task.priority,
            "cpu_grant": task.cpu_grant,
            "db_class": task.db_class,
            "io_class": task.io_class,
            "reason": task.reason,
            "created_by": task.created_by,
            "queued_at": _iso(task.created_at),
            "launched_at": _iso(),
        },
    )

    # Child env: base + task env + queue core-cap overlay (overlay wins so the
    # grant is authoritative) + queue identity + UTF-8 for redirected output.
    env = dict(os.environ)
    env.update(task.env)
    env.update(env_overlay or {})
    env["TRADER_QUEUE_TASK_ID"] = str(task.id)
    env["TRADER_QUEUE_ATTEMPT"] = str(task.attempt_token or "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

    wrap_log = open(run_dir / F_WRAPLOG, "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "task_queue.launcher", "--run", str(run_dir)],
            cwd=str(ROOT),
            env=env,
            stdout=wrap_log,
            stderr=wrap_log,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        wrap_log.close()  # child inherited its own handle
    return proc


def read_launch(run_dir: Path | str) -> dict | None:
    """launch.json {pid, ctime, attempt_token, started_at} or None."""
    return _read_json(Path(run_dir) / F_LAUNCH)


def read_terminal(run_dir: Path | str) -> dict | None:
    """Final outcome from done.json/failed.json, normalized to
    {state, exit_code, reason} — or None if not yet finalized."""
    rd = Path(run_dir)
    done = _read_json(rd / F_DONE)
    if done is not None:
        return {"state": "done", "exit_code": done.get("exit_code", 0), "reason": None}
    failed = _read_json(rd / F_FAILED)
    if failed is not None:
        return {
            "state": "failed",
            "exit_code": failed.get("exit_code"),
            "reason": failed.get("failure_reason"),
        }
    return None


def tail_log(run_dir: Path | str, name: str = F_RUNLOG, lines: int = 40) -> str:
    p = Path(run_dir) / name
    if not p.exists():
        return ""
    try:
        data = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(data[-lines:])
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Child side (the supervisor: `python -m task_queue.launcher --run <run_dir>`)
# --------------------------------------------------------------------------- #
def run_wrapper(run_dir: Path) -> int:
    run_dir = Path(run_dir)
    doc = _read_json(run_dir / F_TASK) or {}
    argv = _normalize_argv([str(x) for x in doc.get("argv", [])])
    cwd = doc.get("cwd") or str(ROOT)
    timeout_s = doc.get("timeout_s")
    task_id = doc.get("task_id")
    token = doc.get("attempt_token")
    started = time.time()

    status_paths = {
        "run_log": str(run_dir / F_RUNLOG),
        "stderr_log": str(run_dir / F_ERRLOG),
        "status_json": str(run_dir / F_STATUS),
    }
    finalized = False

    # 1. Identity anchor FIRST (lets the daemon reconcile this launch safely).
    _write_json_atomic(
        run_dir / F_LAUNCH,
        {
            "pid": os.getpid(),
            "ctime": winproc.current_create_time(),
            "attempt_token": token,
            "task_id": task_id,
            "started_at": _iso(started),
        },
    )
    (run_dir / F_PID).write_text(str(os.getpid()), encoding="utf-8")

    def _status(state: str, exit_code=None) -> None:
        _write_json_atomic(
            run_dir / F_STATUS,
            {
                "task_id": task_id,
                "attempt_token": token,
                "state": state,
                "pid": os.getpid(),
                "exit_code": exit_code,
                "started_at": _iso(started),
                "updated_at": _iso(),
                "output_paths": status_paths,
            },
        )

    def _finalize(state: str, exit_code, reason=None) -> None:
        nonlocal finalized
        if finalized:
            return
        finalized = True
        payload = {
            "finished_at": _iso(),
            "exit_code": exit_code,
            "output_paths": status_paths,
        }
        if state == "done":
            _write_json_atomic(run_dir / F_DONE, payload)
        else:
            payload["failure_reason"] = reason or "nonzero exit"
            _write_json_atomic(run_dir / F_FAILED, payload)
        _status("completed" if state == "done" else state, exit_code)

    rc = -1
    try:
        _status("running")
        if not argv:
            _finalize("failed", 2, reason="empty command")
            return 2

        cflags = 0x00000200 if os.name == "nt" else 0  # CREATE_NEW_PROCESS_GROUP
        with open(run_dir / F_RUNLOG, "wb") as out, open(run_dir / F_ERRLOG, "wb") as err:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=dict(os.environ),
                stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                creationflags=cflags, close_fds=True,
            )
            deadline = (started + float(timeout_s)) if timeout_s else None
            timed_out = False
            while True:
                try:
                    rc = proc.wait(timeout=2.0)
                    break
                except subprocess.TimeoutExpired:
                    if deadline and time.time() > deadline:
                        winproc.kill_tree(proc.pid, winproc.proc_create_time(proc.pid))
                        try:
                            rc = proc.wait(timeout=15)
                        except Exception:
                            rc = -1
                        timed_out = True
                        break
                    _status("running")  # heartbeat the artifact

        if timed_out:
            _finalize("timeout", rc, reason=f"exceeded timeout of {timeout_s}s")
        elif rc == 0:
            _finalize("done", 0)
        else:
            _finalize("failed", rc, reason=f"exit code {rc}")
        return rc if isinstance(rc, int) else 1
    except Exception as e:  # supervisor itself errored — still finalize
        _finalize("failed", rc if isinstance(rc, int) else 1, reason=f"supervisor error: {e}")
        return 1
    finally:
        if not finalized:
            _finalize("failed", rc if isinstance(rc, int) else 1, reason="supervisor exited without finalizing")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "--run":
        return run_wrapper(Path(argv[1]))
    sys.stderr.write("usage: python -m task_queue.launcher --run <run_dir>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
