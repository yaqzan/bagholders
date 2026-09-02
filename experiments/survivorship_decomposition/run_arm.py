"""
P2.A step 4 -- survivorship decomposition, arms B and C.

Three-arm design (see FINDINGS.md + data-acquisition.md "Step 4 needs THREE
arms"): the 2026-07-29 price_history rebuild changed conventions AND the
universe at once, so a naive before/after conflates them.

  A  frozen baseline: contaminated conventions + survivor-only universe
     (experiments/data_ingest/survivor_baseline_pre_sharadar/, run 2026-07-19
     on THIS box, N=300, pinned f9fb7b934)
  B  clean conventions + survivor-only universe   <- --arm B
  C  clean conventions + full PIT universe        <- --arm C

  A->B = substrate-repair effect;  B->C = the TRUE survivorship discount.

Arms B and C are identical except that B sets MC_UNIVERSE_FILE to the frozen
811-symbol survivor allow-list (build_universe.py). Both arms MUST match arm
A's parameters: N=300, v74 pinned, 16 windows (STANDARD_12 + DEEP_4), Core +
Apex profile recipes imported from the same modules arm A's driver used --
imported, not copied, so this driver can never silently drift from them.

Arm-B evidence-honesty guard: after each completed cell the MC subprocess log
must contain the '[universe-filter]' engagement line -- a silently-inert
filter would make arm B == arm C and the whole decomposition worthless, so a
missing line is FATAL (exit 2), not a warning.

Usage (queue-submittable bare):
  python -u experiments/survivorship_decomposition/run_arm.py --arm B [--n-iter 300] [--cpu 14]

Resume-safe: skips any cell whose JSON already exists under results/<arm>/.
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

from experiments._mc_pinned_runner import (  # noqa: E402
    resolve_pinned_version, run_one_window, STANDARD_12, DEEP_4,
)
from experiments.deep_crash_screen.run_screen import CORE_ENV  # noqa: E402
from experiments.holdout_oos_2026_12.run_h3_envelope import FROZEN_ENV as APEX_LIVE_ENV  # noqa: E402

RESULTS = os.path.join(HERE, 'results')
UNIVERSE_FILE = os.path.join(HERE, 'survivor_universe_811.txt')

ALL_WINDOWS = STANDARD_12 + DEEP_4   # 16 labels, same as arm A

PROFILES = {
    'core': dict(CORE_ENV),
    'apex': dict(APEX_LIVE_ENV),
}

ARM_DESC = {
    'B': 'clean conventions + survivor-only 811 universe (MC_UNIVERSE_FILE)',
    'C': 'clean conventions + full PIT universe (no filter)',
}


def _assert_filter_engaged(out_path: str):
    """FATAL if a completed arm-B cell's MC log lacks the universe-filter line."""
    log_path = out_path[:-5] + '.log'
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            log = f.read()
    except OSError:
        # Resumed cell: JSON pre-existed, log may not. Only fresh runs write logs;
        # fresh runs are the ones this guard exists for.
        return
    if '[universe-filter]' not in log:
        print(f"FATAL: arm B cell {out_path} ran WITHOUT the universe filter "
              f"engaging (no '[universe-filter]' line in {log_path}). "
              f"Arm B would silently equal arm C. Aborting.", flush=True)
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arm', required=True, choices=['B', 'C'])
    ap.add_argument('--n-iter', type=int, default=300)
    ap.add_argument('--cpu', type=int, default=14)
    ap.add_argument('--timeout-s', type=int, default=7200,
                    help='per-(profile,window) subprocess cap; arm C runs the '
                         'doubled universe so cells run longer than arm A did')
    args = ap.parse_args()

    if args.arm == 'B':
        if not os.path.exists(UNIVERSE_FILE):
            print(f"FATAL: {UNIVERSE_FILE} missing -- run build_universe.py first.", flush=True)
            sys.exit(2)
        n_syms = sum(1 for ln in open(UNIVERSE_FILE, encoding='utf-8')
                     if ln.strip() and not ln.strip().startswith('#'))
        if n_syms != 811:
            print(f"FATAL: {UNIVERSE_FILE} has {n_syms} symbols, expected 811.", flush=True)
            sys.exit(2)

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[survivorship_decomposition] arm={args.arm} ({ARM_DESC[args.arm]})  "
          f"N={args.n_iter}  profiles={list(PROFILES)}  windows={ALL_WINDOWS}  "
          f"pinned_version={pin_commit} (id={pin_id})", flush=True)

    all_cells = {}
    for profile, base_env in PROFILES.items():
        env = dict(base_env)
        if args.arm == 'B':
            env['MC_UNIVERSE_FILE'] = UNIVERSE_FILE
        all_cells[profile] = {}
        prof_dir = os.path.join(RESULTS, args.arm, profile)
        for label in ALL_WINDOWS:
            out_path = os.path.join(prof_dir, f'{label}.json')
            fresh = not os.path.exists(out_path)
            res = run_one_window(env, label, args.n_iter, out_path, pin_commit,
                                 deep=True, timeout_s=args.timeout_s, cpu=args.cpu)
            if args.arm == 'B' and fresh and res is not None:
                _assert_filter_engaged(out_path)
            all_cells[profile][label] = res.get(label) if res else None

    summary = {
        'arm': args.arm,
        'arm_desc': ARM_DESC[args.arm],
        'universe_file': UNIVERSE_FILE if args.arm == 'B' else None,
        'doctrine': 'SCREEN, not GATE -- deep windows are never a '
                    'calibration/tuning/ship target (assessment-backtest.md).',
        'n_iter': args.n_iter,
        'pinned_version_commit': pin_commit,
        'pinned_version_id': pin_id,
        'standard_12_windows': STANDARD_12,
        'deep_4_windows': DEEP_4,
        'cells': all_cells,
    }
    out_summary = os.path.join(RESULTS, args.arm, 'summary.json')
    with open(out_summary, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"\n[survivorship_decomposition] wrote {out_summary}", flush=True)

    missing = [f"{p}/{w}" for p in PROFILES for w in ALL_WINDOWS
               if all_cells[p].get(w) is None]
    if missing:
        print(f"[survivorship_decomposition] {len(missing)} cell(s) missing "
              f"(timeout/fail) -- resubmit to fill them in: {missing}", flush=True)
        sys.exit(1)
    print("[survivorship_decomposition] all cells complete.", flush=True)


if __name__ == '__main__':
    main()
