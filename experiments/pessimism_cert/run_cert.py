"""
P1.5 -- execution-pessimism CERTIFICATION sweep (NOT a re-tune). Gameplan row
P1.5 / gitnexus commit ab2726a3c (TP_FILL_MISS_P / ENTRY_FILL_MISS_P /
NEXT_OPEN_ANCHOR, siblings of the existing SLIP_SL_OV knob).

Question this answers: "do the shipped levers/params KEEP their keep-decision
under a more pessimistic fill model?" -- certification, never a calibration
target (these knobs exist ONLY for this sweep; they must never become a new
shipped default). Any flip in the robustness matrix (built by the separate
collect_matrix.py -- see below) is a signal to re-validate that specific
lever at N=500, not to change a default here.

ARMS (7 total -- the 6 named in the task + one addition, flagged below):
  baseline            : NO pessimism knobs set at all (today's shipped
                        execution-cost model). ADDED beyond the 6 named arms
                        -- without a zero-pessimism reference row there is
                        nothing for collect_matrix.py to diff against, so
                        "keeps its keep-decision" would be unanswerable.
                        Flagged here and in the final report; trivial to
                        drop by removing the 'baseline' key below.
  slip_sl_03          : SLIP_SL_OV=-0.03 (wider forced-exit half-spread;
                        current shipped value is -0.015, see
                        strategy_config.py STRATEGY_30DTE.option.SLIP_SL --
                        this arm is literally the "raise toward -0.02..-0.03"
                        scenario that field's own comment names).
  tp_miss_03          : TP_FILL_MISS_P=0.03 (3% of triggered TP touches fail
                        to fill -> unprotected full-range fill + forced-exit
                        slip).
  tp_miss_07          : TP_FILL_MISS_P=0.07 (7%, the harsher end of the
                        task's specified range).
  entry_miss_05       : ENTRY_FILL_MISS_P=0.05 (5% of entry orders never
                        fill -> slot/capital freed for the next candidate).
  next_open_anchor    : NEXT_OPEN_ANCHOR=1 (sensitivity-only: entry priced at
                        the NEXT day's open instead of the signal day's
                        close).
  combined_pessimist  : all four of the above together (SLIP_SL_OV=-0.03,
                        TP_FILL_MISS_P=0.07 [the worse of the two tested],
                        ENTRY_FILL_MISS_P=0.05, NEXT_OPEN_ANCHOR=1).

PROFILES: Core (bare STRATEGY_30DTE defaults) + Apex (imported verbatim from
experiments/holdout_oos_2026_12/run_h3_envelope.py's FROZEN_ENV -- the
documented live-Apex-15DTE recipe). See experiments/deep_crash_screen/
run_screen.py's docstring for the byte-for-byte provenance of both; not
re-derived here, imported/copied from the same source.

WINDOWS: the house "ship validation" 9-window set (T3's 8 canonical +
2020_crash -- .claude/skills/run-monte-carlo/SKILL.md GUARD 2: "always
screen 2020_crash ... treat T3's 8 as a floor, not the ceiling"). The task
text says "default 8 windows"; this repo's own documented ship-validation
invocation is 8+2020_crash=9 and calls it "8" colloquially (same SKILL.md
section) -- using that exact 9-row set here rather than a literal T3-8
so 2020_crash (the single most collapse-relevant window) is never skipped
in a pessimism-certification sweep. Flagged for visibility, easy to trim to
SHIP_VALIDATION_9[:-1] if 2020_crash should be dropped.

N=300 (screen tier per the task spec).

SCALE: 7 arms x 2 profiles x 9 windows = 126 (arm,profile,window) cells.
This is the largest of the Block D batch -- generous queue timeout is
intentional; the per-cell resume guard (skip-if-json-exists) means a second
submission after a timeout only pays for whatever cells are still missing.

Usage (no required args -- queue-submittable bare):
  python -u experiments/pessimism_cert/run_cert.py [--n-iter 300] [--cpu 4]
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
    resolve_pinned_version, run_one_window, SHIP_VALIDATION_9,
)
from experiments.holdout_oos_2026_12.run_h3_envelope import FROZEN_ENV as APEX_LIVE_ENV  # noqa: E402

RESULTS = os.path.join(HERE, 'results')
WINDOWS = SHIP_VALIDATION_9

CORE_ENV = {
    'TIER_ULTRA_OV': '0.20', 'TIER_TOP_OV': '0.15', 'TIER_MID_OV': '0.08',
    'TIER_LOW_OV': '0.03', 'TIER_OVERFLOW_OV': '0.0',
    'PUT_TIER_TOP_OV': '0.0', 'PUT_TIER_MID_OV': '0.0', 'PUT_TIER_LOW_OV': '0.0',
    'MAX_POSITIONS_OVERRIDE': '14', 'MAX_POSITIONS_CALL': '14', 'MAX_POSITIONS_PUT': '0',
    'GROSS_PREMIUM_CAP': '0.50', 'CALL_PREMIUM_CAP': '0.50', 'PUT_PREMIUM_CAP': '0.0',
    'OPP_SAT_CALL_REF': '16.0', 'OPP_SAT_PUT_REF': '4.0',
    'OPP_SAT_POWER': '0.50', 'OPP_SAT_FLOOR': '0.55',
    'PRACTICAL_EXPOSURE_ENABLED': '1', 'PRACTICAL_CAPITAL_CEILING': '0.0',
    'DD_SOFT_BAND_LO': '0.35', 'DD_SOFT_BAND_HI': '0.55', 'DD_SOFT_CALL_FLOOR': '0.40',
}
PROFILES = {'core': CORE_ENV, 'apex': dict(APEX_LIVE_ENV)}

ARMS = {
    'baseline':           {},
    'slip_sl_03':         {'SLIP_SL_OV': '-0.03'},
    'tp_miss_03':         {'TP_FILL_MISS_P': '0.03'},
    'tp_miss_07':         {'TP_FILL_MISS_P': '0.07'},
    'entry_miss_05':      {'ENTRY_FILL_MISS_P': '0.05'},
    'next_open_anchor':   {'NEXT_OPEN_ANCHOR': '1'},
    'combined_pessimist': {'SLIP_SL_OV': '-0.03', 'TP_FILL_MISS_P': '0.07',
                           'ENTRY_FILL_MISS_P': '0.05', 'NEXT_OPEN_ANCHOR': '1'},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=300)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--timeout-s', type=int, default=3600)
    args = ap.parse_args()

    pin_commit, pin_id = resolve_pinned_version()
    total_cells = len(ARMS) * len(PROFILES) * len(WINDOWS)
    print(f"[pessimism_cert] N={args.n_iter}  arms={list(ARMS)}  profiles={list(PROFILES)}  "
          f"windows={WINDOWS}  total_cells={total_cells}  pinned_version={pin_commit} "
          f"(id={pin_id})", flush=True)

    all_cells = {}   # arm -> profile -> window -> result-or-None
    for arm_name, arm_env in ARMS.items():
        all_cells[arm_name] = {}
        for profile, prof_env in PROFILES.items():
            all_cells[arm_name][profile] = {}
            env = dict(prof_env)
            env.update(arm_env)   # arm knobs never collide with profile sizing knobs
            cell_dir = os.path.join(RESULTS, arm_name, profile)
            for label in WINDOWS:
                out_path = os.path.join(cell_dir, f'{label}.json')
                res = run_one_window(env, label, args.n_iter, out_path, pin_commit,
                                      deep=False, timeout_s=args.timeout_s, cpu=args.cpu)
                all_cells[arm_name][profile][label] = res.get(label) if res else None

    summary = {
        'note': 'Certification sweep -- these knobs must never become a new shipped '
                'default. See collect_matrix.py for the robustness-matrix read '
                '(run separately, not part of this submission).',
        'n_iter': args.n_iter,
        'windows': WINDOWS,
        'arms': {k: v for k, v in ARMS.items()},
        'profiles': list(PROFILES),
        'pinned_version_commit': pin_commit,
        'pinned_version_id': pin_id,
        'cells': all_cells,
    }
    out_summary = os.path.join(RESULTS, 'summary.json')
    with open(out_summary, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"\n[pessimism_cert] wrote {out_summary}", flush=True)

    missing = [f"{a}/{p}/{w}" for a in ARMS for p in PROFILES for w in WINDOWS
              if all_cells[a][p].get(w) is None]
    if missing:
        print(f"[pessimism_cert] {len(missing)}/{total_cells} cell(s) still missing "
              f"-- resubmit to fill them in.", flush=True)
    else:
        print("[pessimism_cert] all cells complete.", flush=True)


if __name__ == '__main__':
    main()
