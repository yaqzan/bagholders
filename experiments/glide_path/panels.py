#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Panel generation for the glide-path screen (DESIGN.md sections 9/11/13).

Builds ONLY what doesn't already exist:
  - sentinel / core_noddsb / sentinel_noddsb  -> experiments/glide_path/results/panels/<arm>/
  - core                                       -> GAP-FILL in place into
    experiments/lifecycle_mc/results/panels/core/ (resume-safe: a complete
    lifecycle_mc core panel costs zero subprocesses).

Grid, runner, and version pin are all IMPORTED from lifecycle_mc (which itself
imports the grid from concentration_2x.sweep) -- no re-derivation.

THIS IS THE QUEUE JOB.
  python -u experiments/glide_path/panels.py [--n-iter 100] [--cpu 4] [--timeout-s 1200]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.lifecycle_mc.panels import build_grid, PANELS_DIR as LIFECYCLE_PANELS_DIR  # noqa: E402
from experiments.lifecycle_mc._rolling_runner import run_one_rolling_window, resolve_pinned_version  # noqa: E402
from experiments.glide_path.envs import BUILD_ARMS, GAPFILL_ARMS, PROVENANCE  # noqa: E402

RESULTS = os.path.join(HERE, 'results')
PANELS_DIR = os.path.join(RESULTS, 'panels')


def arm_dir_for(arm_name: str) -> str:
    """core lives (and gap-fills) in lifecycle_mc's own panels dir; every
    other arm lives under this experiment's panels dir."""
    if arm_name in GAPFILL_ARMS:
        return os.path.join(LIFECYCLE_PANELS_DIR, arm_name)
    return os.path.join(PANELS_DIR, arm_name)


def generate_panels(n_iter: int, cpu: int, timeout_s: int, max_windows: int = 0) -> dict:
    pin_commit, pin_id = resolve_pinned_version()
    windows = build_grid()
    if max_windows and max_windows > 0:
        windows = windows[:max_windows]
    arms = dict(GAPFILL_ARMS)   # core first (cheap gap-fill), then the new builds
    arms.update(BUILD_ARMS)
    print(f"[glide-panels] arms={list(arms)}  n_windows={len(windows)}  n_iter={n_iter}  "
          f"pinned_version={pin_commit} (id={pin_id})", flush=True)

    all_cells: dict[str, dict] = {}
    for arm_name, arm_env in arms.items():
        all_cells[arm_name] = {}
        adir = arm_dir_for(arm_name)
        for (wlabel, ws, we) in windows:
            out_path = os.path.join(adir, f'{wlabel}.json')
            res = run_one_rolling_window(arm_env, wlabel, ws.isoformat(), we.isoformat(),
                                         n_iter, out_path, pin_commit,
                                         timeout_s=timeout_s, cpu=cpu)
            all_cells[arm_name][wlabel] = res.get(wlabel) if res else None

    missing = [f"{a}/{w}" for a in arms for w in all_cells[a] if all_cells[a][w] is None]
    if missing:
        print(f"[glide-panels] {len(missing)} cell(s) still missing (timeout/fail) -- "
              f"a resubmit retries only these: {missing}", flush=True)
    else:
        print("[glide-panels] all cells complete.", flush=True)

    meta = {
        'n_iter': n_iter, 'n_windows': len(windows),
        'pinned_version_commit': pin_commit, 'pinned_version_id': pin_id,
        'provenance': PROVENANCE, 'missing_cells': missing,
        'window_labels': [w[0] for w in windows],
        'core_panel_location': arm_dir_for('core'),
        'design_doc': 'experiments/glide_path/DESIGN.md',
    }
    os.makedirs(PANELS_DIR, exist_ok=True)
    meta_path = os.path.join(PANELS_DIR, f'_meta_n{n_iter}.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
    print(f"[glide-panels] wrote {meta_path}", flush=True)
    return all_cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=100)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--timeout-s', type=int, default=1200)
    ap.add_argument('--max-windows', type=int, default=0,
                    help='debug cap on quarterly windows per arm (0=all)')
    args = ap.parse_args()
    generate_panels(args.n_iter, args.cpu, args.timeout_s, max_windows=args.max_windows)


if __name__ == '__main__':
    main()
