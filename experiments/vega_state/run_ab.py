"""
P1.4 -- VEGA_STATE crash-vega A/B. Gameplan row P1.4 / gitnexus commit
ab2726a3c (calibrated ATM-IV-vs-VIX response ratio applied inside the
dead-hold CALL walk's vega_ratio, previously hardcoded 1.0 -- see
_compute_dead_hold_call in monte_carlo.py and this experiment's own
calibration.json / build_calibration.py, already committed).

Question this answers: "does marking the dead-hold's crash vega change
WorstDD / collapse / lever rankings?" Per calibration.json's own caveats,
the own-panel fit (n=2560, 2025-02..2026-06, never covers a real crash) has
r2~0.001 and a SLIGHTLY NEGATIVE VIX coefficient -- the opposite sign from
the "crash-vega-credit" hypothesis, though not distinguishable from zero.
This driver does not answer that question itself -- it produces the paired
VEGA_STATE=0/1 numbers; escalate to N=500 if |delta WorstDD| > 3pp per the
gameplan row's own escalation rule (that N=500 read is a separate future
step, not part of this submission).

ARMS: VEGA_STATE off ('0') and on ('1'), PAIRED (same window label ->
identical seed per .claude/skills/run-monte-carlo/SKILL.md GUARD 3 -- the
seed depends only on the label string, so this is a clean A/B by
construction as long as both arms use exactly WINDOWS below unchanged).

PROFILES: Core (bare STRATEGY_30DTE defaults) + Apex (imported verbatim
from experiments/holdout_oos_2026_12/run_h3_envelope.py's FROZEN_ENV). Same
provenance as experiments/deep_crash_screen/run_screen.py -- not re-derived.

WINDOWS: house "ship validation" 9-window set (T3's 8 canonical +
2020_crash), matching the task's literal "N=300 x 8 windows incl
2020_crash" via this repo's own documented shorthand for that phrase (see
.claude/skills/run-monte-carlo/SKILL.md GUARD 2 + section 4). The gameplan
P1.4 row itself says "N=300x8 incl 2020_crash", same wording.

N=300. Scale: 2 arms x 2 profiles x 9 windows = 36 cells.

Usage (no required args -- queue-submittable bare):
  python -u experiments/vega_state/run_ab.py [--n-iter 300] [--cpu 4]
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

ARMS = {'vega_off': {'VEGA_STATE': '0'}, 'vega_on': {'VEGA_STATE': '1'}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=300)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--timeout-s', type=int, default=3600)
    args = ap.parse_args()

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[vega_state_ab] N={args.n_iter}  arms={list(ARMS)}  profiles={list(PROFILES)}  "
          f"windows={WINDOWS}  pinned_version={pin_commit} (id={pin_id})", flush=True)

    all_cells = {}
    for arm_name, arm_env in ARMS.items():
        all_cells[arm_name] = {}
        for profile, prof_env in PROFILES.items():
            all_cells[arm_name][profile] = {}
            env = dict(prof_env)
            env.update(arm_env)
            cell_dir = os.path.join(RESULTS, arm_name, profile)
            for label in WINDOWS:
                out_path = os.path.join(cell_dir, f'{label}.json')
                res = run_one_window(env, label, args.n_iter, out_path, pin_commit,
                                      deep=False, timeout_s=args.timeout_s, cpu=args.cpu)
                all_cells[arm_name][profile][label] = res.get(label) if res else None

    # Plain paired diff (vega_on - vega_off) per profile/window -- no verdict.
    diffs = {}
    for profile in PROFILES:
        diffs[profile] = {}
        for label in WINDOWS:
            off = all_cells['vega_off'][profile].get(label)
            on = all_cells['vega_on'][profile].get(label)
            if off is None or on is None:
                diffs[profile][label] = None
                continue
            diffs[profile][label] = {
                'd_worst_dd': (on.get('worst_dd') - off.get('worst_dd'))
                              if (on.get('worst_dd') is not None and off.get('worst_dd') is not None) else None,
                'd_p_coll': (on.get('p_coll') - off.get('p_coll'))
                            if (on.get('p_coll') is not None and off.get('p_coll') is not None) else None,
                'd_mean_ret': (on.get('mean_ret') - off.get('mean_ret'))
                              if (on.get('mean_ret') is not None and off.get('mean_ret') is not None) else None,
            }

    summary = {
        'note': 'Paired VEGA_STATE off/on A/B. Escalate to N=500 if any |d_worst_dd| > 3pp '
                'per gameplan P1.4 (a separate future step, not part of this submission). '
                'No verdict rendered here.',
        'calibration_caveat': 'experiments/vega_state/calibration.json: own-panel fit '
                              'r2~0.001, slightly NEGATIVE VIX coefficient (opposite sign '
                              'from the crash-vega-credit hypothesis) -- read these numbers '
                              'with that caveat in view.',
        'n_iter': args.n_iter,
        'windows': WINDOWS,
        'profiles': list(PROFILES),
        'pinned_version_commit': pin_commit,
        'pinned_version_id': pin_id,
        'cells': all_cells,
        'paired_diff_vega_on_minus_off': diffs,
    }
    out_summary = os.path.join(RESULTS, 'summary.json')
    with open(out_summary, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"\n[vega_state_ab] wrote {out_summary}", flush=True)

    missing = [f"{a}/{p}/{w}" for a in ARMS for p in PROFILES for w in WINDOWS
              if all_cells[a][p].get(w) is None]
    if missing:
        print(f"[vega_state_ab] {len(missing)} cell(s) still missing -- resubmit to fill "
              f"them in: {missing}", flush=True)
    else:
        print("[vega_state_ab] all cells complete.", flush=True)

    print("\n=== paired diff (vega_on - vega_off) ===", flush=True)
    for profile in PROFILES:
        print(f"  {profile}:", flush=True)
        for label in WINDOWS:
            d = diffs[profile].get(label)
            print(f"    {label:<10} {d}", flush=True)


if __name__ == '__main__':
    main()
