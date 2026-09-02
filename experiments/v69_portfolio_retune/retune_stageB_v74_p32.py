"""
P3.2 -- Core tier/overflow re-sweep on v74's deflated supply (Stage B screen).
Gameplan row P3.2: "Axes: TIER_MID {.08,.10,.12} x TIER_LOW {.03,.05,.08} x
OVERFLOW {0,.02,.035} x MaxPos {12,14,16}; staged B(100)->C(300)->D(500x10
incl COVID) paired seeds; ship only Pareto/targeted-selectivity (c04-class),
never uniform-shrink (c08-class); overflow hard cap 0.040." This driver is
Stage B only (N=100 screen); Stage C/D are future follow-ups, not in scope
here.

REUSED HARNESS (per the task's explicit instruction -- do not re-derive):
experiments/v69_portfolio_retune/driver.py's `run_candidate` +
`ENV_MAP` (subprocess-per-cell bare monte_carlo.py invocation, WHY documented
in driver.py's own module docstring: TP/SL/tier sigma globals are frozen at
monte_carlo.py IMPORT time, so a fresh subprocess per candidate is the only
correct way to vary them). This is the SAME pattern retune_stageA.py /
retune_stageB.py already use for their own (v69-era, non-v74) grids -- this
file does not edit those, it is a new sibling for the v74 P3.2 grid.

EXISTING v74 RESULTS CHECK (done this session, before writing this file):
`grep -rln "v74" experiments/v69_portfolio_retune/` returned NOTHING -- every
file in that directory (retune_stageA/B/C.py, stageA_n100.jsonl,
stageB_n120.jsonl, stageC_n300.jsonl, driver.py, etc.) is dated 2026-05-31,
pre-dates v74, and carries no v74 tag. **0 of the 81 grid cells below already
exist anywhere in that harness; all 81 are submitted fresh.**

GRID (81 cells = 3x3x3x3): TIER_ULTRA/TIER_TOP held at Core's shipped values
(0.20/0.15 -- gameplan's axes list only MID/LOW/OVERFLOW/MaxPos, not
ULTRA/TOP). Calls-only (PUT_TIER_*=0, MAX_POSITIONS_PUT=0), MAX_POSITIONS_CALL
= MaxPos (matches Core's own convention of a shared calls-only pool).

WINDOWS: the T3 8-canonical set, matching retune_stageA.py/retune_stageB.py's
own WINDOWS list exactly (2020_crash is deliberately deferred to Stage D per
the gameplan's own "B(100)->C(300)->D(500x10 incl COVID)" cadence -- Stage B
is a screen, not the collapse-safety gate).

N=100 (Stage B screen tier per the task spec).

RESUME-SAFE (added on top of the reused harness -- retune_stageA/B.py
themselves truncate their output file on every run): reads any existing
output JSONL first and skips cells whose name is already recorded, so a
requeue after a timeout only pays for the missing cells.

Usage (no required args -- queue-submittable bare):
  python -u experiments/v69_portfolio_retune/retune_stageB_v74_p32.py [--n-iter 100]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402
from experiments._mc_pinned_runner import resolve_pinned_version  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

TIER_MID_VALUES = [0.08, 0.10, 0.12]
TIER_LOW_VALUES = [0.03, 0.05, 0.08]
OVERFLOW_VALUES = [0.0, 0.02, 0.035]
MAXPOS_VALUES = [12, 14, 16]


def cfg(tier_mid, tier_low, overflow, max_pos):
    return dict(
        TIER_ULTRA=0.20, TIER_TOP=0.15,          # Core shipped values, unchanged
        TIER_MID=tier_mid, TIER_LOW=tier_low, TIER_OVERFLOW=overflow,
        PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0,
        MAX_POSITIONS=max_pos, MAX_POSITIONS_CALL=max_pos, MAX_POSITIONS_PUT=0,
    )


def cell_name(tier_mid, tier_low, overflow, max_pos):
    return (f"mid{int(round(tier_mid*100)):02d}_low{int(round(tier_low*100)):02d}_"
            f"ovf{int(round(overflow*1000)):03d}_mp{max_pos}")


def build_grid():
    grid = {}
    for tm, tl, ov, mp in itertools.product(TIER_MID_VALUES, TIER_LOW_VALUES,
                                             OVERFLOW_VALUES, MAXPOS_VALUES):
        grid[cell_name(tm, tl, ov, mp)] = cfg(tm, tl, ov, mp)
    return grid


def summarize(w):
    labels = list(w.keys())
    max_coll = max(w[k]['p_collapse'] for k in labels)
    max_dd = max(w[k]['worst_dd'] for k in labels)
    avg_med = sum(w[k]['med_ret'] for k in labels) / len(labels)
    pos = sum(1 for k in labels if w[k]['med_ret'] > 0)
    fivey = w.get('5y', {})
    now = w.get('22-now', {})
    return dict(max_coll=max_coll, max_dd=max_dd, avg_med=avg_med, pos_windows=pos,
                fivey_med=fivey.get('med_ret'), fivey_dd=fivey.get('worst_dd'),
                now_med=now.get('med_ret'), now_dd=now.get('worst_dd'))


def load_done(out_path):
    done = set()
    if not os.path.exists(out_path):
        return done
    with open(out_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)['name'])
            except Exception:
                continue
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=100)
    ap.add_argument('--workers', type=int, default=0)
    ap.add_argument('--timeout', type=int, default=1800,
                    help='per-cell subprocess ceiling (s); one slow cell must not sink '
                         'the remaining 80')
    args = ap.parse_args()

    pin_commit, pin_id = resolve_pinned_version()
    grid = build_grid()
    out_path = os.path.join(HERE, f'stageB_v74_p32_n{args.n_iter}.jsonl')
    done = load_done(out_path)

    print(f"[p32_stageB] {len(grid)} cells total, {len(done)} already done (resume), "
          f"{len(grid) - len(done)} remaining. N={args.n_iter} windows={WINDOWS} "
          f"pinned_version={pin_commit} (id={pin_id})", flush=True)

    results = []
    with open(out_path, 'a', encoding='utf-8') as f:
        for name, params in grid.items():
            if name in done:
                continue
            t0 = time.time()
            print(f"\n[p32_stageB] {name} N={args.n_iter} params={params}", flush=True)
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
                  f"pos={s['pos_windows']}/8  5y={s['fivey_med']}%/{s['fivey_dd']}%  "
                  f"22now={s['now_med']}%/{s['now_dd']}%  ({time.time()-t0:.0f}s)", flush=True)

    n_total_done = len(load_done(out_path))
    print(f"\n[p32_stageB] wrote/appended {out_path} -- {n_total_done}/{len(grid)} cells "
          f"complete overall.", flush=True)
    if n_total_done < len(grid):
        print(f"[p32_stageB] {len(grid) - n_total_done} cell(s) still missing -- resubmit "
              f"to fill them in (resume-safe: this script skips names already in the "
              f"jsonl).", flush=True)


if __name__ == '__main__':
    main()
