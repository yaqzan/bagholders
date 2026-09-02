"""Environment fingerprint embedded into every runner's summary artifact.

Pure stdlib + best-effort numpy (try/except; None if numpy is absent -- this
build sandbox itself has no numpy, which is exactly the case this module must
tolerate gracefully). The only subprocess calls are read-only `git rev-parse
HEAD` / `git status --porcelain`, run with cwd=repo_root; never raises on
failure (e.g. git missing, or not a git checkout) -- fields degrade to None.

Usage:
    from fingerprint import capture
    fp = capture(repo_root)   # -> dict, JSON- and ASCII-safe

Selftest (DB-free, offline):
    python experiments/newbox_rebaseline/fingerprint.py --selftest
"""
from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_KEYS = (
    'timestamp_utc', 'python_version', 'platform', 'machine', 'hostname',
    'cpu_count', 'numpy_version', 'numpy_blas', 'git_commit', 'git_dirty',
)


def _numpy_info() -> tuple[str | None, str | None]:
    """Best-effort numpy version + BLAS/config info. Returns (None, None) if
    numpy is not importable (tolerated, never raises)."""
    try:
        import numpy as np  # noqa: local, optional dependency
    except Exception:
        return None, None

    version = getattr(np, '__version__', None)
    blas = None
    try:
        import contextlib
        import io
        # Newer numpy: show_config(mode='dicts') returns a dict. Older numpy:
        # show_config() takes no args and only prints to stdout -- capture it.
        try:
            info = np.show_config(mode='dicts')
            if info is not None:
                blas = json.dumps(info, default=str)
        except TypeError:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                np.show_config()
            blas = buf.getvalue().strip() or None
        except Exception:
            blas = None
        if blas is None and hasattr(np, '__config__') and hasattr(np.__config__, 'show'):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                np.__config__.show()
            blas = buf.getvalue().strip() or None
    except Exception:
        blas = None
    return version, blas


def _git_info(repo_root: Path) -> tuple[str | None, bool | None]:
    """Read-only `git rev-parse HEAD` + `git status --porcelain`, cwd=repo_root.
    Returns (commit_or_None, dirty_bool_or_None). Never raises."""
    commit = None
    dirty = None
    try:
        out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(repo_root),
                              capture_output=True, text=True, timeout=15)
        if out.returncode == 0:
            commit = out.stdout.strip() or None
    except Exception:
        commit = None
    try:
        out2 = subprocess.run(['git', 'status', '--porcelain'], cwd=str(repo_root),
                               capture_output=True, text=True, timeout=15)
        if out2.returncode == 0:
            dirty = bool(out2.stdout.strip())
    except Exception:
        dirty = None
    return commit, dirty


def capture(repo_root: Path) -> dict:
    """Capture the full fingerprint dict. Never raises -- every field
    degrades to None on failure rather than aborting the caller's run."""
    repo_root = Path(repo_root)
    numpy_version, numpy_blas = _numpy_info()
    git_commit, git_dirty = _git_info(repo_root)
    return {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'python_version': sys.version.split('\n')[0].strip(),
        'platform': platform.platform(),
        'machine': platform.machine(),
        'hostname': socket.gethostname(),
        'cpu_count': os.cpu_count(),
        'numpy_version': numpy_version,
        'numpy_blas': numpy_blas,
        'git_commit': git_commit,
        'git_dirty': git_dirty,
    }


def _selftest() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    here = Path(__file__).resolve().parent
    repo_root = here.parent.parent

    try:
        fp = capture(repo_root)
    except Exception as e:
        check('capture() runs without raising', False, str(e))
        return 1
    check('capture() runs without raising', True)

    missing = [k for k in REQUIRED_KEYS if k not in fp]
    check('capture() returns all required keys', not missing, f"missing: {missing}")

    check('cpu_count is a positive int', isinstance(fp.get('cpu_count'), int) and fp['cpu_count'] > 0,
          f"got {fp.get('cpu_count')!r}")

    # numpy absence must be TOLERATED, not raise -- this sandbox has no numpy,
    # so this is exercised for real, not just simulated.
    if fp['numpy_version'] is None:
        check('numpy absent tolerated (numpy_version=None)', True)
    else:
        check(f'numpy present (numpy_version={fp["numpy_version"]!r})', True)

    # ASCII-safety of the whole serialized fingerprint (Windows cp1252 console
    # safety -- every artifact this package writes must be pure ASCII).
    try:
        blob = json.dumps(fp)
        blob.encode('ascii')
        check('capture() output is JSON- and ASCII-safe', True)
    except UnicodeEncodeError as e:
        check('capture() output is JSON- and ASCII-safe', False, str(e))
    except Exception as e:
        check('capture() output is JSON-serializable', False, str(e))

    if fp.get('git_commit'):
        check(f"git_commit resolved ({fp['git_commit'][:12]}...)", True)
    else:
        print("[WARN] git_commit is None (not a git checkout here, or git missing) -- tolerated, not a failure")

    if fp.get('git_dirty') is None:
        print("[WARN] git_dirty is None (git status failed) -- tolerated, not a failure")
    else:
        check(f"git_dirty resolved (dirty={fp['git_dirty']})", True)

    # Repeated calls must be independent / not raise on a second capture.
    try:
        fp2 = capture(repo_root)
        check('capture() is callable repeatedly without raising', True)
        check('cpu_count is stable across two captures in the same process',
              fp['cpu_count'] == fp2['cpu_count'])
    except Exception as e:
        check('capture() is callable repeatedly without raising', False, str(e))

    return 0 if ok else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        raise SystemExit(_selftest())
    _repo_root = Path(__file__).resolve().parent.parent.parent
    print(json.dumps(capture(_repo_root), indent=2, default=str))
