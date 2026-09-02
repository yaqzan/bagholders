#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BIT-EXACT validation: NEW in-process load-once path vs OLD subprocess path.

Runs ONE cell (cascade_ref) on ONE window (2024) at N=20 with the SAME window
label/seed basis through BOTH paths and asserts the per-iteration arrays
(finals / dds / t2x_bars / t_50dd_bars) are BIT-IDENTICAL.

This is the gate the brief requires before the fast harness may be used. Run via
the task queue (it's a tiny MC). Exit 0 = identical; exit 1 = mismatch.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import sweep  # noqa: E402

WLABEL = 'bitexact_2024'
WS = date(2024, 1, 1)
WE = date(2024, 12, 31)
N_ITER = 20
SC = 50_000.0
CELL_NAME = 'cascade_ref'

KEYS = ('finals', 'dds', 't2x_bars', 't_50dd_bars')


def _legacy_arrays() -> dict:
    """Run the OLD subprocess path for the cell+window and return raw per-iter arrays."""
    grid = sweep.build_grid()
    cell = next(c for c in grid if c.name == CELL_NAME)
    rj = sweep.RESULTS_DIR / f"_bitexact_legacy_{CELL_NAME}.json"
    env = sweep.cell_env(cell, N_ITER, WLABEL, WS, WE, rj, SC, workers=None)
    print(f"[legacy] running subprocess MC for {CELL_NAME} / {WLABEL} N={N_ITER}", flush=True)
    proc = subprocess.run(
        [sys.executable, '-u', str(REPO / 'monte_carlo.py')],
        env=env, cwd=str(REPO),
    )
    if proc.returncode != 0:
        raise SystemExit(f"legacy MC failed rc={proc.returncode}")
    data = json.loads(rj.read_text())
    wkey = WLABEL if WLABEL in data else next(k for k in data if k != '_meta')
    p = data[wkey]['paths']
    rj.unlink(missing_ok=True)
    return {k: p.get(k) for k in KEYS}


def _inproc_arrays() -> tuple[dict, dict]:
    """Run the NEW in-process load-once path for the cell+window TWO ways and return
    (fresh_pool_arrays, reused_pool_arrays):
      - fresh:  _simulate_window(ctx, cell_params, pool=None)  (single-config path)
      - reused: build ONE window pool, broadcast cell_params, simulate (sweep path)
    Both must match legacy AND each other. Also simulates a DIFFERENT cell first on
    the reused pool to prove the per-cell broadcast resets worker globals (no stale
    cross-cell leakage)."""
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    os.environ['STARTING_CASH_OV'] = repr(SC)
    os.environ['N_ITER_OVERRIDE'] = str(N_ITER)
    os.environ['MC_RETURN_PATHS'] = '1'
    os.environ['MC_NO_DB_PERSIST'] = '1'
    sys.path.insert(0, str(REPO))
    import monte_carlo as mc  # noqa: E402
    assert abs(mc.STARTING_CASH - SC) < 1e-6 and mc.N_ITER == N_ITER
    version = mc.AlgorithmVersion.get_active_scores_version()
    print(f"[inproc] version {version.git_commit} id={version.id}", flush=True)
    grid = sweep.build_grid()
    cell = next(c for c in grid if c.name == CELL_NAME)
    other = next(c for c in grid if c.name == 'flat_n1_a50')  # different sizing
    cp = sweep.cell_params(cell)

    ctx = mc._prepare_window(WLABEL, WS, WE, version)

    # (1) fresh-pool path (pool=None -> builds & tears down its own pool)
    res_fresh = mc._simulate_window(ctx, cell_params=cp, persist=False, pool=None)['seeded']

    # (2) reused-pool path: build one window pool, run a DIFFERENT cell first (to
    # dirty the worker globals), then run the target cell — proving the broadcast
    # resets them. This mirrors exactly how the sweep drives a window.
    n_workers = min(int(os.environ.get('MC_WORKERS', '0')) or os.cpu_count() or 4, N_ITER)
    pool = mc._make_window_pool(ctx, n_workers)
    try:
        _ = mc._simulate_window(ctx, cell_params=sweep.cell_params(other),
                                persist=False, pool=pool)['seeded']
        res_reused = mc._simulate_window(ctx, cell_params=cp, persist=False, pool=pool)['seeded']
    finally:
        pool.close(); pool.join()

    return ({k: res_fresh.get(k) for k in KEYS},
            {k: res_reused.get(k) for k in KEYS})


def _cmp(name_a, a, name_b, b) -> bool:
    ok = True
    for k in KEYS:
        x, y = a.get(k), b.get(k)
        if x is None or y is None:
            print(f"  [{name_a} vs {name_b}] {k}: MISSING ({name_a}={x is not None}, {name_b}={y is not None})")
            ok = False
            continue
        if len(x) != len(y):
            print(f"  [{name_a} vs {name_b}] {k}: LENGTH MISMATCH {len(x)} vs {len(y)}")
            ok = False
            continue
        diffs = [(i, x[i], y[i]) for i in range(len(x)) if x[i] != y[i]]
        if diffs:
            print(f"  [{name_a} vs {name_b}] {k}: {len(diffs)} DIFFS (first 5): {diffs[:5]}")
            ok = False
        else:
            print(f"  [{name_a} vs {name_b}] {k}: IDENTICAL  (n={len(x)})")
    return ok


def main():
    # Run the in-process paths FIRST in this process; the legacy path is a clean
    # subprocess so its module import-time env is independent.
    inp_fresh, inp_reused = _inproc_arrays()
    leg = _legacy_arrays()

    print("\n--- legacy subprocess vs in-process FRESH pool (single-config path) ---")
    ok1 = _cmp('legacy', leg, 'inproc_fresh', inp_fresh)
    print("\n--- legacy subprocess vs in-process REUSED pool (sweep path) ---")
    ok2 = _cmp('legacy', leg, 'inproc_reused', inp_reused)
    print("\n--- in-process FRESH vs REUSED (cross-cell broadcast reset check) ---")
    ok3 = _cmp('inproc_fresh', inp_fresh, 'inproc_reused', inp_reused)

    print("\nSample (first 3 finals):")
    print(f"  legacy        : {leg['finals'][:3]}")
    print(f"  inproc_fresh  : {inp_fresh['finals'][:3]}")
    print(f"  inproc_reused : {inp_reused['finals'][:3]}")
    print(f"\nt2x_bars legacy       : {leg['t2x_bars']}")
    print(f"t2x_bars inproc_reused: {inp_reused['t2x_bars']}")

    if ok1 and ok2 and ok3:
        print("\n[BIT-EXACT] PASS — legacy == fresh-pool == reused-pool (all identical).")
        sys.exit(0)
    else:
        print("\n[BIT-EXACT] FAIL — arrays differ. DO NOT use the fast harness.")
        sys.exit(1)


if __name__ == '__main__':
    main()
