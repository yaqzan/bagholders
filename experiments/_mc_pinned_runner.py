"""
Shared subprocess-per-window MC runner for the Block D evidence drivers
(P1.2 deep_crash_screen, P1.5 pessimism_cert, P1.4 vega_state, P0.3
apex_dte_dd evidence). NEW file -- monte_carlo.py itself is NOT edited (the
engine-edit window is closed); this only launches it as a subprocess with
env overrides, exactly the documented "bare invocation + env var" interface
(.claude/skills/run-monte-carlo/SKILL.md).

WHY PER-WINDOW, NOT PER-ARM-COVERING-ALL-WINDOWS:
monte_carlo.py's own main() writes MC_RESULTS_JSON only ONCE, after its
ENTIRE internal WINDOWS loop finishes (verified monte_carlo.py:4520-4584). A
queue timeout/preemption partway through an arm's multi-window run loses
ALL of that arm's progress, not just the in-flight window -- exactly the
failure mode traps.md's "Sharded-ReSim budgeting" entry warns about ("give
the harness a skip-if-parquet-exists resume guard BEFORE the first launch").
This module drives monte_carlo.py ONE (arm x window) at a time via
WINDOWS_OVERRIDE=<single label>, writing one small JSON per (arm, window)
cell and skipping any cell whose JSON already exists. A kill costs at most
ONE in-flight cell (bounded further by the per-cell `timeout_s`), and a
requeue resumes for free. Process-startup overhead per extra subprocess
launch is ~seconds -- negligible next to actual MC compute.

VERSION PINNING:
Every cell pins ALGORITHM_VERSION_PIN to the version resolved ONCE at
driver-start (resolve_pinned_version), so a multi-hour queued batch stays
internally consistent even if the active scores version changes mid-run.
This guards the exact staleness class found LIVE in this session:
experiments/n_floor_v46/summary.json silently carries algorithm_version_id=72
while its own FINDINGS.md header still says "v46" -- a prior ad-hoc rerun
overwrote the file in place with no version tag and nobody noticed. Every
output JSON here records the resolved commit in its own meta so a later
reader can tell whether the pin matches what the driver was told to target,
without trusting a filename or a docstring.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, 'monte_carlo.py')

# Informational only (CLAUDE.md "Active scoring version: v74 (f9fb7b934)" as of
# 2026-06-15). A mismatch prints a warning but never blocks -- the resolved
# commit is always recorded in the output meta so this is auditable after the
# fact either way, rather than crashing a long queued batch over a version
# that legitimately moved on between submit-time and run-time.
EXPECTED_VERSION_COMMIT_PREFIX = 'f9fb7b934'


def resolve_pinned_version() -> tuple[str, int]:
    """Resolve the active scores version ONCE per driver process. Returns
    (git_commit, id). Callers should thread the returned commit into every
    cell via `pin_commit=` so the whole batch stays internally consistent."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from database.models.core import AlgorithmVersion  # noqa: E402
    v = AlgorithmVersion.get_active_scores_version()
    commit = v.git_commit
    if not commit.startswith(EXPECTED_VERSION_COMMIT_PREFIX):
        print(f"[_mc_pinned_runner] WARNING: active version {commit} (id={v.id}) does "
              f"NOT match the expected v74 prefix {EXPECTED_VERSION_COMMIT_PREFIX!r}. "
              f"This batch was specified against v74 -- proceeding with whatever is "
              f"ACTUALLY active (pinned + recorded in every output JSON's meta) rather "
              f"than blocking; verify before trusting these numbers as v74 evidence.",
              flush=True)
    else:
        print(f"[_mc_pinned_runner] pinned to active version {commit} (id={v.id})", flush=True)
    return commit, v.id


def run_one_window(env_overrides: dict, window_label: str, n_iter: int, out_json_path: str,
                    pin_commit: str, deep: bool = False, timeout_s: int = 3600,
                    cpu: int = 4) -> dict | None:
    """Run monte_carlo.py for ONE window label; write out_json_path via
    MC_RESULTS_JSON. Resume-safe: returns the parsed dict without spawning a
    subprocess if out_json_path already exists. Never raises -- a subprocess
    failure or timeout is logged and returns None so callers can keep going
    (the caller's own summary step tolerates missing cells; a resubmit fills
    them in later).

    env_overrides: arm/profile-specific env (e.g. VEGA_STATE, SLIP_SL_OV,
        TIER_ULTRA_OV, NOMINAL_CAL_DTE, ...). Values are stringified.
    deep: pass MC_WINDOW_SET=deep so ltcm_1998/dotcom_crash_2000_2002/
        gfc_crash_2007_2009/2007_now exist in WINDOWS at all (harmless no-op
        for the 12 standard labels).
    """
    if os.path.exists(out_json_path):
        print(f"  [SKIP] {window_label} -> {out_json_path} already exists (resume)", flush=True)
        try:
            with open(out_json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  [WARN] existing {out_json_path} unreadable ({e}); will NOT "
                  f"re-run automatically -- delete it manually to force a re-run.",
                  flush=True)
            return None

    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env['MC_NO_DB_PERSIST'] = '1'          # never pollute monte_carlo_run across many cells
    env['N_ITER_OVERRIDE'] = str(n_iter)
    env['WINDOWS_OVERRIDE'] = window_label
    env['MC_RESULTS_JSON'] = out_json_path
    env['ALGORITHM_VERSION_PIN'] = pin_commit
    env['MC_WORKERS'] = str(max(1, cpu))
    if deep:
        env['MC_WINDOW_SET'] = 'deep'
    for k, v in env_overrides.items():
        env[k] = str(v)

    os.makedirs(os.path.dirname(os.path.abspath(out_json_path)), exist_ok=True)
    log_path = out_json_path[:-5] + '.log' if out_json_path.endswith('.json') else out_json_path + '.log'

    t0 = time.time()
    print(f"  [RUN] {window_label} n_iter={n_iter} -> {out_json_path}", flush=True)
    try:
        proc = subprocess.run([sys.executable, '-u', MC], cwd=ROOT, env=env,
                              timeout=timeout_s, capture_output=True, text=True)
    except subprocess.TimeoutExpired as e:
        dur = time.time() - t0
        print(f"  [TIMEOUT] {window_label} after {dur:.0f}s (cap {timeout_s}s) -- "
              f"skipping this cell for now, a resubmit will retry it.", flush=True)
        try:
            with open(log_path, 'w', encoding='utf-8') as lf:
                lf.write((e.stdout or '') if isinstance(e.stdout, str) else '')
                lf.write((e.stderr or '') if isinstance(e.stderr, str) else '')
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"  [ERROR] {window_label} subprocess launch failed: {e}", flush=True)
        return None

    dur = time.time() - t0
    try:
        with open(log_path, 'w', encoding='utf-8') as lf:
            lf.write(proc.stdout or '')
            lf.write(proc.stderr or '')
    except Exception:
        pass

    if proc.returncode != 0:
        print(f"  [FAIL] {window_label} rc={proc.returncode} ({dur:.0f}s) -- see {log_path}",
              flush=True)
        return None
    if not os.path.exists(out_json_path):
        print(f"  [FAIL] {window_label} exited 0 but {out_json_path} was not written "
              f"({dur:.0f}s) -- see {log_path}", flush=True)
        return None
    print(f"  [OK] {window_label} ({dur:.0f}s)", flush=True)
    with open(out_json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Window label sets, all verified against monte_carlo.py source (never
# hand-typed from memory -- see traps.md discipline on this).
# ---------------------------------------------------------------------------

# Standard 12-row default WINDOWS list (monte_carlo.py:1515-1528).
STANDARD_12 = ['2018', '2020', '2020_crash', '2021', '2022', '2023', '2024',
               'dip', '22-now', '2025', '5y', '10y']

# P1.2 deep-window SCREEN tier (monte_carlo.py DEEP_WINDOWS, ab2726a3c).
DEEP_4 = ['ltcm_1998', 'dotcom_crash_2000_2002', 'gfc_crash_2007_2009', '2007_now']

# House "ship validation" set (.claude/skills/run-monte-carlo/SKILL.md section 4):
# T3's 8 canonical windows + 2020_crash, per GUARD 2 ("always screen 2020_crash
# and 2020, never just the 8 T3 windows" -- treat T3's 8 as a floor not a
# ceiling). Used here for P1.5/P1.4 (both spec "N=300 x 8 windows incl
# 2020_crash" in the task text -- this 9-row set is this repo's own documented
# shorthand for that phrase, see SKILL.md line ~152).
SHIP_VALIDATION_9 = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now',
                     '5y', '2020_crash']
