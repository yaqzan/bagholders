#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Panel generation: {sprint, core} x quarterly-rolled 2y windows -> per-window
JSON under results/panels/<arm>/<window_label>.json.

Grid = concentration_2x/sweep.py's own quarterly-start convention, IMPORTED
(not re-derived) per DESIGN.md section 5: monthly_windows(step_months=3,
horizon_days=730, hist_start=2016-06-01, hist_end=2026-04-15).

THIS IS THE QUEUE JOB. Resume-safe: skips any (arm, window) cell whose JSON
already exists (via _rolling_runner.run_one_rolling_window). A preemption /
resubmit costs only the in-flight cell.

Usage (bare -- queue-submittable):
  python -u experiments/lifecycle_mc/panels.py [--n-iter 100] [--cpu 4] [--timeout-s 1200]
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

from experiments.concentration_2x.sweep import monthly_windows, HIST_START, HIST_END, HORIZON_DAYS  # noqa: E402
from experiments.lifecycle_mc._rolling_runner import run_one_rolling_window, resolve_pinned_version  # noqa: E402
from experiments.lifecycle_mc.envs import ARMS, PROVENANCE  # noqa: E402

RESULTS = os.path.join(HERE, 'results')
PANELS_DIR = os.path.join(RESULTS, 'panels')

STEP_MONTHS = 3          # quarterly, per DESIGN.md section 5 / task instruction
MIN_HORIZON_DAYS = 180   # concentration_2x.sweep's own default floor


def build_grid():
    """Quarterly-rolled 2y windows over the full 10y history. Returns a list
    of (label, start_date, end_date). Imported logic, local call only."""
    return monthly_windows(hist_start=HIST_START, hist_end=HIST_END,
                           horizon_days=HORIZON_DAYS, step_months=STEP_MONTHS,
                           min_horizon_days=MIN_HORIZON_DAYS)


def generate_panels(n_iter: int, cpu: int, timeout_s: int, arms: dict | None = None,
                    max_windows: int = 0) -> dict:
    """Generate (or resume) every (arm, window) cell. Returns
    {arm: {window_label: cell_dict_or_None}}. Safe to call repeatedly --
    already-present per-window JSONs are loaded, not recomputed.

    max_windows: debug/validation cap (0 = all ~38 quarterly windows) --
    mirrors concentration_2x/sweep.py's own --max-windows convenience for a
    fast manual shakedown of a couple of cells before trusting a full run."""
    arms = arms if arms is not None else ARMS
    pin_commit, pin_id = resolve_pinned_version()
    windows = build_grid()
    if max_windows and max_windows > 0:
        windows = windows[:max_windows]
    print(f"[panels] arms={list(arms)}  n_windows={len(windows)}  n_iter={n_iter}  "
          f"grid=quarterly(step={STEP_MONTHS}mo, horizon={HORIZON_DAYS}d, "
          f"{HIST_START}..{HIST_END})  pinned_version={pin_commit} (id={pin_id})", flush=True)

    all_cells: dict[str, dict] = {}
    for arm_name, arm_env in arms.items():
        all_cells[arm_name] = {}
        arm_dir = os.path.join(PANELS_DIR, arm_name)
        for (wlabel, ws, we) in windows:
            out_path = os.path.join(arm_dir, f'{wlabel}.json')
            res = run_one_rolling_window(arm_env, wlabel, ws.isoformat(), we.isoformat(),
                                         n_iter, out_path, pin_commit,
                                         timeout_s=timeout_s, cpu=cpu)
            all_cells[arm_name][wlabel] = res.get(wlabel) if res else None

    missing = [f"{a}/{w}" for a in arms for w in all_cells[a] if all_cells[a][w] is None]
    if missing:
        print(f"[panels] {len(missing)} cell(s) still missing (timeout/fail) -- "
              f"a resubmit will retry only these: {missing}", flush=True)
    else:
        print("[panels] all cells complete.", flush=True)

    meta = {
        'n_iter': n_iter, 'grid': {'step_months': STEP_MONTHS, 'horizon_days': HORIZON_DAYS,
                                    'hist_start': HIST_START.isoformat(), 'hist_end': HIST_END.isoformat(),
                                    'min_horizon_days': MIN_HORIZON_DAYS, 'n_windows': len(windows)},
        'pinned_version_commit': pin_commit, 'pinned_version_id': pin_id,
        'provenance': PROVENANCE, 'missing_cells': missing,
        'window_labels': [w[0] for w in windows],
    }
    os.makedirs(PANELS_DIR, exist_ok=True)
    meta_path = os.path.join(PANELS_DIR, f'_meta_n{n_iter}.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
    print(f"[panels] wrote {meta_path}", flush=True)
    return all_cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=100)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--timeout-s', type=int, default=1200,
                    help='per-(arm,window) subprocess cap; every window here is a 2y '
                         'span (or a shorter tail-clipped remainder), much cheaper than '
                         'the multi-year windows other drivers budget for')
    ap.add_argument('--max-windows', type=int, default=0,
                    help='debug cap on number of quarterly windows per arm (0=all ~38)')
    args = ap.parse_args()
    generate_panels(args.n_iter, args.cpu, args.timeout_s, max_windows=args.max_windows)


if __name__ == '__main__':
    main()
