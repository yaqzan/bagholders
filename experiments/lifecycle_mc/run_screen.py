#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lifecycle MC -- single entry point: generate panels (resume-safe) then
compose the 3 pre-registered policy arms (DESIGN.md), write + print the
screen summary. This is the ONE command submitted to the task queue.

Portfolio-stage research. NO ALGORITHM_VERSION bump, NO scoring change,
NO engine-file edits (monte_carlo.py etc. are FROZEN for this task --
every knob here is env-var-driven via subprocess). Phase 3 (auto-rotate
wiring) is USER-LOCKED and is NOT touched by this script. Renders NO verdict.

Usage (bare -- queue-submittable):
  python -u experiments/lifecycle_mc/run_screen.py [--n-iter 100] [--reps-per-start 500] \
      [--cpu 4] [--panel-timeout-s 1200]

Resume-safe end to end: panels.py skips any (arm, window) cell whose JSON
already exists; the policy composition step is cheap and always re-runs (it
has no MC/MySQL cost, so there is nothing to resume there -- it simply
reflects whatever panel cells are currently on disk).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.lifecycle_mc.panels import generate_panels  # noqa: E402
from experiments.lifecycle_mc.policy import run_all_arms, print_summary  # noqa: E402
import json  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=100, help='MC engine iterations per window (the "N" of this screen tier)')
    ap.add_argument('--reps-per-start', type=int, default=500, help='resampling reps/pooled-start in the composer')
    ap.add_argument('--horizon-days', type=int, default=5 * 365)
    ap.add_argument('--seed', type=int, default=20260713)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--panel-timeout-s', type=int, default=1200)
    ap.add_argument('--max-windows', type=int, default=0,
                    help='debug cap on number of quarterly windows per arm (0=all ~38)')
    args = ap.parse_args()

    t0 = time.time()
    print(f"[run_screen] === STAGE 1: panel generation (N={args.n_iter}) ===", flush=True)
    generate_panels(args.n_iter, args.cpu, args.panel_timeout_s, max_windows=args.max_windows)
    print(f"[run_screen] panel generation done in {time.time()-t0:.0f}s", flush=True)

    print(f"\n[run_screen] === STAGE 2: policy composition ===", flush=True)
    t1 = time.time()
    result = run_all_arms(args.n_iter, args.reps_per_start, args.horizon_days, args.seed)
    print_summary(result)

    out_path = os.path.join(HERE, 'results', f'screen_n{args.n_iter}.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    print(f"\n[run_screen] wrote {out_path}  (composition {time.time()-t1:.0f}s, "
          f"total {time.time()-t0:.0f}s)", flush=True)
    print("[run_screen] DONE. No verdict rendered -- see DESIGN.md section 8/10 for the "
          "N=500 gate this screen feeds into.", flush=True)


if __name__ == '__main__':
    main()
