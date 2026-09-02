"""
P1.5 robustness-matrix collector -- LEFT READY, NOT RUN as part of the Block D
queue batch (per the executor task spec: "a collector script left ready (not
run)"). Run this by hand AFTER experiments/pessimism_cert/run_cert.py's
results/summary.json exists (fully or partially -- this tolerates missing
cells).

WHAT IT RENDERS: for each profile (Core, Apex), for each pessimism arm
(everything in run_cert.ARMS except 'baseline'), the per-window delta vs the
'baseline' arm on the three T-gate-relevant reads (WorstDD, p_collapse,
mean_ret), plus a MECHANICAL flag (not a ship verdict -- see NOTE below) when
a delta crosses a threshold:
  - collapse newly appears (baseline p_coll==0 and arm p_coll>0) on ANY window
  - WorstDD degrades by more than DD_FLAG_THRESHOLD_PP on the 5y or 22-now window
    (the two T4/T5-primary windows)

NOTE: this script prints descriptive deltas + a mechanical threshold flag. It
does NOT issue a keep/revert/ship verdict -- that judgment belongs to whoever
reads the matrix (per this batch's "no verdicts" charter). "Flagged" means
"crossed the threshold below, worth a human look", nothing stronger.

Usage:
  python experiments/pessimism_cert/collect_matrix.py [--summary results/summary.json]
"""
from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SUMMARY = os.path.join(HERE, 'results', 'summary.json')

DD_FLAG_THRESHOLD_PP = 5.0     # percentage points of WorstDD degradation
FOCUS_WINDOWS = ['5y', '22-now']   # T4/T5-primary per assessment-backtest.md


def load(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--summary', default=DEFAULT_SUMMARY)
    ap.add_argument('--dd-flag-pp', type=float, default=DD_FLAG_THRESHOLD_PP)
    args = ap.parse_args()

    if not os.path.exists(args.summary):
        raise SystemExit(f"{args.summary} does not exist yet -- run run_cert.py first "
                         f"(fully or partially; this script tolerates missing cells).")

    data = load(args.summary)
    cells = data['cells']
    windows = data['windows']
    profiles = data['profiles']
    arms = [a for a in data['arms'] if a != 'baseline']

    if 'baseline' not in cells:
        raise SystemExit("no 'baseline' arm in this summary.json -- the matrix needs a "
                         "zero-pessimism reference row to diff against (see run_cert.py "
                         "docstring on why 'baseline' was added beyond the 6 named arms).")

    print(f"{'='*100}\nP1.5 PESSIMISM-CERTIFICATION ROBUSTNESS MATRIX\n{'='*100}")
    print(f"Windows: {windows}\nProfiles: {profiles}\nArms (vs baseline): {arms}\n")

    any_flag = False
    for profile in profiles:
        print(f"\n--- profile={profile} ---")
        base_by_window = cells.get('baseline', {}).get(profile, {})
        for arm in arms:
            arm_by_window = cells.get(arm, {}).get(profile, {})
            print(f"\n  arm={arm}")
            print(f"    {'window':<12}{'base_DD':>9}{'arm_DD':>9}{'d_DD':>8}"
                  f"{'base_coll':>11}{'arm_coll':>10}{'flag':>8}")
            for w in windows:
                b = base_by_window.get(w)
                a = arm_by_window.get(w)
                if b is None or a is None:
                    print(f"    {w:<12}  (missing cell -- base={'ok' if b else 'MISSING'} "
                          f"arm={'ok' if a else 'MISSING'})")
                    continue
                b_dd, a_dd = b.get('worst_dd'), a.get('worst_dd')
                b_coll, a_coll = b.get('p_coll'), a.get('p_coll')
                d_dd = (a_dd - b_dd) if (a_dd is not None and b_dd is not None) else None
                flag = ''
                if b_coll is not None and a_coll is not None and b_coll == 0 and a_coll > 0:
                    flag = 'COLLAPSE+'
                    any_flag = True
                elif (w in FOCUS_WINDOWS and d_dd is not None and d_dd > args.dd_flag_pp):
                    flag = f'DD+{d_dd:.1f}pp'
                    any_flag = True
                print(f"    {w:<12}{b_dd!s:>9}{a_dd!s:>9}"
                      f"{(f'{d_dd:+.1f}' if d_dd is not None else '--'):>8}"
                      f"{b_coll!s:>11}{a_coll!s:>10}{flag:>8}")

    print(f"\n{'='*100}")
    if any_flag:
        print("One or more cells crossed the mechanical flag threshold above -- see "
              "'flag' column. This is a pointer to look closer (e.g. re-validate that "
              "lever/window at N=500), not a verdict.")
    else:
        print("No cell crossed the mechanical flag threshold on the data currently "
              "available (missing cells are NOT the same as 'passed' -- check for "
              "'(missing cell...)' rows above before treating this as complete).")


if __name__ == '__main__':
    main()
