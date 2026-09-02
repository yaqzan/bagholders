"""
Phase OP2 master orchestrator — runs the full pipeline autonomously:

  Phase 1 (axis screen)  → top-3 axes
    Phase 2 (combo search) → top-3 candidates
      Phase 3 (canonical MC) → ship gate eval
        if pass:
          Ship action: update strategy_config.py, drift test, recalculate --force

Each phase saves results to experiments/op2/phase{N}_results.json so failures
can be resumed mid-pipeline by deleting failed-phase JSON.

Usage:
    PYTHONIOENCODING=utf-8 python experiments/op2/orchestrator.py [--skip-ship]

Logging: appends to experiments/op2/orchestrator.log with timestamps.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXP_DIR = os.path.join(ROOT, 'experiments', 'op2')
LOG = os.path.join(EXP_DIR, 'orchestrator.log')


def _log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line); sys.stdout.flush()
    try:
        with open(LOG, 'a') as fh:
            fh.write(line + '\n')
    except OSError:
        pass


def _run(name, cmd, timeout=21600):
    _log(f"=== {name} START ===")
    _log(f"  cmd: {' '.join(cmd)}")
    t0 = time.time()
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        _log(f"!!! {name} TIMED OUT after {timeout}s")
        return False
    elapsed = time.time() - t0
    ok = proc.returncode == 0
    _log(f"=== {name} {'OK' if ok else 'FAIL'} (exit={proc.returncode}, {elapsed:.0f}s) ===")
    return ok


def main():
    skip_ship = '--skip-ship' in sys.argv

    _log("=" * 80)
    _log("OP2 ORCHESTRATOR — autonomous pipeline start")
    _log("=" * 80)

    # ── Phase 1 ───────────────────────────────────────────────────────
    p1_path = os.path.join(EXP_DIR, 'phase1_results.json')
    if os.path.exists(p1_path):
        _log(f"Phase 1: skipping — results already exist at {p1_path}")
    else:
        if not _run('PHASE 1', [sys.executable, '-u',
                                os.path.join(EXP_DIR, 'phase1_axis_screen.py')]):
            _log("Phase 1 FAILED — abort")
            sys.exit(1)

    # ── Phase 2 ───────────────────────────────────────────────────────
    p2_path = os.path.join(EXP_DIR, 'phase2_results.json')
    if os.path.exists(p2_path):
        _log(f"Phase 2: skipping — results already exist at {p2_path}")
    else:
        if not _run('PHASE 2', [sys.executable, '-u',
                                os.path.join(EXP_DIR, 'phase2_combo_search.py')]):
            _log("Phase 2 FAILED — abort")
            sys.exit(1)

    # ── Phase 3 ───────────────────────────────────────────────────────
    p3_path = os.path.join(EXP_DIR, 'phase3_canonical_results.json')
    if os.path.exists(p3_path):
        _log(f"Phase 3: skipping — results already exist at {p3_path}")
    else:
        if not _run('PHASE 3', [sys.executable, '-u',
                                os.path.join(EXP_DIR, 'phase3_canonical_mc.py')]):
            _log("Phase 3 FAILED — abort")
            sys.exit(1)

    # ── Ship gate eval ────────────────────────────────────────────────
    with open(p3_path) as fh:
        p3 = json.load(fh)
    passing = [c for c in p3.get('candidates', []) if c.get('gate', {}).get('pass')]
    _log(f"Ship gate: {len(passing)} of {len(p3['candidates'])} candidates passed")
    if not passing:
        _log("No candidates passed ship gate — exit without shipping")
        return

    if skip_ship:
        _log("--skip-ship flag set — pipeline complete without ship action")
        for c in passing:
            av = ', '.join(f"{k}={v}" for k, v in c['axis_values'].items())
            _log(f"  PASSING: util={c['phase2_util']:+.2f} [{av}]")
        return

    # ── Ship action ───────────────────────────────────────────────────
    winner = passing[0]   # highest phase2_util
    av = ', '.join(f"{k}={v}" for k, v in winner['axis_values'].items())
    _log(f"SHIPPING winner: util={winner['phase2_util']:+.2f} [{av}]")

    if not _run('SHIP ACTION', [sys.executable, '-u',
                                 os.path.join(EXP_DIR, 'ship_action.py'),
                                 json.dumps(winner['axis_values'])]):
        _log("Ship action FAILED — manual intervention required")
        sys.exit(1)

    _log("=" * 80)
    _log("OP2 ORCHESTRATOR — pipeline complete (ship + recalculate done)")
    _log("=" * 80)


if __name__ == '__main__':
    main()
