"""
Stage C -- P3.2 Core tier/overflow re-sweep, N=300 confirm on Stage B's frontier survivors.

Gameplan row P3.2 staging cadence: "staged B(100)->C(300)->D(500x10 incl COVID)". Stage B
(retune_stageB_v74_p32.py, 81 cells, N=100, task 612) screened the full
TIER_MID x TIER_LOW x OVERFLOW x MaxPos grid; the Stage-B disposition (gameplan.md 6b,
2026-07-14): the shipped Core cell (mid08_low03_ovf000_mp14) is confirmed near-frontier
(3rd of 81) and every OVERFLOW>0 family is dominated on v74's deflated supply. Survivors
promoted to this Stage C: MaxPos in {12, 16} at the winning mid08_low03_ovf000 tier/overflow
combination, PLUS the shipped mp14 cell itself as an in-run control -- so this run is a
clean 3-point MaxPos-only comparison at N=300, all sharing the same tier/overflow.

CELLS (3, not the full 81 -- narrowed by the Stage-B read, not re-derived here):
  mid08_low03_ovf000_mp12   candidate
  mid08_low03_ovf000_mp14   shipped control -- byte-identical to STRATEGY_30DTE's live
                            TIER_ALLOC={ultra:.20, top:.15, mid:.08, low:.03, overflow:0}
                            / MAX_POSITIONS=14 (verified directly against strategy_config.py,
                            not re-derived)
  mid08_low03_ovf000_mp16   candidate

REUSED HARNESS (per this repo's own doctrine -- do not re-derive): imports
`cfg`/`cell_name`/`summarize`/`load_done` from retune_stageB_v74_p32.py (same cell-naming /
config-building / resume logic, so a Stage-B cell name and a Stage-C cell name for the same
params are byte-identical) and `run_candidate` from driver.py (same subprocess-per-cell
harness, same PYTHONHASHSEED=0 paired-seed convention -- driver.py pins PYTHONHASHSEED so
the same window/iteration index draws the same underlying path across different candidates,
isolating the config's effect). This file adds no new MC mechanics, only a narrower cell
set, a bigger N, and two extra windows.

WINDOWS: Stage B's 8-window T3-canonical set PLUS 2020_crash and 2020. Stage B's own
docstring deliberately deferred 2020_crash "to Stage D per the gameplan's own
B(100)->C(300)->D(500x10 incl COVID) cadence -- Stage B is a screen, not the
collapse-safety gate." This Stage C pulls the always-screen-2020_crash rule forward
(Stage B lacked it) rather than waiting on a Stage D that isn't scheduled yet.

N=300 (Stage C tier per the task spec). Stage D would be N=500x10 incl COVID with paired
seeds across 10 replicates -- out of scope here.

RESUME-SAFE: same pattern as retune_stageB_v74_p32.py -- reads any existing output JSONL
first and skips cells already recorded, so a requeue after a timeout only pays for the
missing cells.

Usage (no required args -- queue-submittable bare):
  python -u experiments/v69_portfolio_retune/retune_stageC_v74_p32.py [--n-iter 300]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments.v69_portfolio_retune.retune_stageB_v74_p32 import (  # noqa: E402
    cfg, cell_name, summarize, load_done,
)
from experiments._mc_pinned_runner import resolve_pinned_version  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Stage B's 8-window T3-canonical set, PLUS 2020_crash and 2020 (always-screen-2020_crash
# rule -- Stage B deferred these two by its own design; see module docstring).
WINDOWS = ['2020_crash', '2020', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

# Stage B survivors: mid08_low03_ovf000 confirmed near-frontier (3rd of 81, gameplan 6b
# disposition 2026-07-14); MaxPos {12, 16} promoted as candidates, mp14 carried forward as
# the shipped in-run control. Fixed axes match Stage B's winning cell exactly.
STAGE_C_MAXPOS = [12, 14, 16]
FIXED_TIER_MID = 0.08
FIXED_TIER_LOW = 0.03
FIXED_OVERFLOW = 0.0


def build_grid():
    grid = {}
    for mp in STAGE_C_MAXPOS:
        grid[cell_name(FIXED_TIER_MID, FIXED_TIER_LOW, FIXED_OVERFLOW, mp)] = cfg(
            FIXED_TIER_MID, FIXED_TIER_LOW, FIXED_OVERFLOW, mp)
    return grid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=300)
    ap.add_argument('--workers', type=int, default=0)
    ap.add_argument('--timeout', type=int, default=3600,
                    help='per-cell subprocess ceiling (s); Stage B (N=100, 8 windows) '
                         'averaged 211s/cell (max 299s) -- N=300 x 10 windows scales '
                         'roughly 3.75x, so 3600s leaves a large safety margin')
    args = ap.parse_args()

    pin_commit, pin_id = resolve_pinned_version()
    grid = build_grid()
    out_path = os.path.join(HERE, f'stageC_v74_p32_n{args.n_iter}.jsonl')
    done = load_done(out_path)

    print(f"[p32_stageC] {len(grid)} cells total, {len(done)} already done (resume), "
          f"{len(grid) - len(done)} remaining. N={args.n_iter} windows={WINDOWS} "
          f"pinned_version={pin_commit} (id={pin_id})", flush=True)

    results = []
    with open(out_path, 'a', encoding='utf-8') as f:
        for name, params in grid.items():
            if name in done:
                continue
            t0 = time.time()
            print(f"\n[p32_stageC] {name} N={args.n_iter} params={params}", flush=True)
            try:
                w = run_candidate(params, args.n_iter, WINDOWS,
                                  workers=args.workers, timeout=args.timeout, tag=name)
            except Exception as e:
                print(f"  ERROR {name}: {e}", flush=True)
                continue
            s = summarize(w)
            rec = dict(name=name, params=params, summary=s, windows=w,
                      dur_s=round(time.time() - t0, 1),
                      pinned_version_commit=pin_commit, pinned_version_id=pin_id)
            f.write(json.dumps(rec) + '\n')
            f.flush()
            results.append(rec)
            print(f"  -> coll={s['max_coll']}%  maxDD={s['max_dd']}%  avgMed={s['avg_med']:+.1f}%  "
                  f"pos={s['pos_windows']}/{len(WINDOWS)}  5y={s['fivey_med']}%/{s['fivey_dd']}%  "
                  f"22now={s['now_med']}%/{s['now_dd']}%  ({time.time()-t0:.0f}s)", flush=True)

    n_total_done = len(load_done(out_path))
    print(f"\n[p32_stageC] wrote/appended {out_path} -- {n_total_done}/{len(grid)} cells "
          f"complete overall.", flush=True)
    if n_total_done < len(grid):
        print(f"[p32_stageC] {len(grid) - n_total_done} cell(s) still missing -- resubmit "
              f"to fill them in (resume-safe: this script skips names already in the "
              f"jsonl).", flush=True)


if __name__ == '__main__':
    main()
