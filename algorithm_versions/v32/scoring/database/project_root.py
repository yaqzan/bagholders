"""Trader repo root for git and cwd. Override with env TRADER_PROJECT_ROOT."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

__all__ = [
    "get_trader_project_root",
    "chdir_trader_project",
    "trader_git_output",
]


def get_trader_project_root() -> Path:
    env = os.environ.get("TRADER_PROJECT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def chdir_trader_project() -> Path:
    root = get_trader_project_root()
    try:
        os.chdir(root)
    except OSError as e:
        print(f"Warning: could not chdir to {root}: {e}", file=sys.stderr)
    return root


def trader_git_output(args: list[str], *, timeout: float = 30) -> bytes:
    root = get_trader_project_root()
    return subprocess.check_output(
        ["git", *args],
        cwd=str(root),
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )
