#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Subprocess-per-(arm, rolling-window) MC runner for the lifecycle-MC panels.

Sibling of experiments/_mc_pinned_runner.py, NOT a duplicate: that module
drives monte_carlo.py's FIXED named-window list via WINDOWS_OVERRIDE (used by
deep_crash_screen / pessimism_cert / vega_state / apex_dte_dd's P0.3
evidence). This module drives an ARBITRARY rolling window via
WIN_START/WIN_END/WIN_LABEL -- the mechanism experiments/concentration_2x/
sweep.py and experiments/ladder_vs_core/dump_episodes.py use for monthly/
quarterly-rolled starts. monte_carlo.py's own main() checks WIN_START/WIN_END
FIRST and, if both are set, that fully replaces WINDOWS_OVERRIDE (verified
monte_carlo.py:4522-4533) -- the two mechanisms are mutually exclusive per
invocation, so this runner never sets WINDOWS_OVERRIDE.

monte_carlo.py is FROZEN for this task -- this only launches it as a
subprocess with env overrides (the documented bare-invocation + env-var
interface), same discipline as every sibling P0-P1 evidence driver.

Resume-safe: one JSON per (arm, window) cell; skips (returns the cached parse)
if the output path already exists. A kill/preemption costs at most ONE
in-flight cell.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MC = os.path.join(ROOT, 'monte_carlo.py')


def resolve_pinned_version() -> tuple[str, int]:
    """Resolve the active scores version ONCE per driver process. Returns
    (git_commit, id). Same pattern as _mc_pinned_runner.resolve_pinned_version
    (duplicated locally rather than imported so this module has zero import-
    time dependency on the _mc_pinned_runner module beyond convention)."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from database.models.core import AlgorithmVersion  # noqa: E402
    v = AlgorithmVersion.get_active_scores_version()
    print(f"[_rolling_runner] pinned to active version {v.git_commit} (id={v.id})", flush=True)
    return v.git_commit, v.id


def run_one_rolling_window(env_overrides: dict, win_label: str, win_start: str, win_end: str,
                           n_iter: int, out_json_path: str, pin_commit: str,
                           timeout_s: int = 1800, cpu: int = 4) -> dict | None:
    """Run monte_carlo.py for ONE arbitrary [win_start, win_end) window; write
    out_json_path via MC_RESULTS_JSON. Resume-safe: returns the parsed dict
    without spawning a subprocess if out_json_path already exists. Never
    raises -- a subprocess failure/timeout is logged and returns None so the
    caller (panels.py) can keep going and a resubmit fills the gap in later.

    env_overrides: the arm's recipe (envs.SPRINT_ENV / envs.CORE_ENV).
    Returns the parsed {win_label: {...,'paths': {...}}} dict, or None.
    """
    if os.path.exists(out_json_path):
        try:
            with open(out_json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  [WARN] existing {out_json_path} unreadable ({e}); delete it "
                  f"manually to force a re-run.", flush=True)
            return None

    env = dict(os.environ)   # preserve queue-injected MC_WORKERS/TRADER_QUEUE_CORES/PATH
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env['MC_NO_DB_PERSIST'] = '1'
    env['MC_RETURN_PATHS'] = '1'
    env['N_ITER_OVERRIDE'] = str(n_iter)
    env['WIN_START'] = win_start
    env['WIN_END'] = win_end
    env['WIN_LABEL'] = win_label
    env['MC_RESULTS_JSON'] = out_json_path
    env['ALGORITHM_VERSION_PIN'] = pin_commit
    if 'MC_WORKERS' not in env:
        env['MC_WORKERS'] = str(max(1, cpu))
    for k, v in env_overrides.items():
        env[k] = str(v)

    os.makedirs(os.path.dirname(os.path.abspath(out_json_path)), exist_ok=True)
    log_path = out_json_path[:-5] + '.log' if out_json_path.endswith('.json') else out_json_path + '.log'

    t0 = time.time()
    print(f"  [RUN] {win_label} ({win_start}..{win_end}) n_iter={n_iter} -> {out_json_path}",
          flush=True)
    try:
        proc = subprocess.run([sys.executable, '-u', MC], cwd=ROOT, env=env,
                              timeout=timeout_s, capture_output=True, text=True)
    except subprocess.TimeoutExpired as e:
        dur = time.time() - t0
        print(f"  [TIMEOUT] {win_label} after {dur:.0f}s (cap {timeout_s}s) -- skipping "
              f"for now, a resubmit will retry it.", flush=True)
        try:
            with open(log_path, 'w', encoding='utf-8') as lf:
                lf.write((e.stdout or '') if isinstance(e.stdout, str) else '')
                lf.write((e.stderr or '') if isinstance(e.stderr, str) else '')
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"  [ERROR] {win_label} subprocess launch failed: {e}", flush=True)
        return None

    dur = time.time() - t0
    try:
        with open(log_path, 'w', encoding='utf-8') as lf:
            lf.write(proc.stdout or '')
            lf.write(proc.stderr or '')
    except Exception:
        pass

    if proc.returncode != 0:
        print(f"  [FAIL] {win_label} rc={proc.returncode} ({dur:.0f}s) -- see {log_path}",
              flush=True)
        return None
    if not os.path.exists(out_json_path):
        print(f"  [FAIL] {win_label} exited 0 but {out_json_path} was not written "
              f"({dur:.0f}s) -- see {log_path}", flush=True)
        return None
    print(f"  [OK] {win_label} ({dur:.0f}s)", flush=True)
    with open(out_json_path, 'r', encoding='utf-8') as f:
        return json.load(f)
