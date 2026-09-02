"""
P0.3 EVIDENCE run -- prep for a USER-LOCKED decision. APPLIES NOTHING (no
portfolio_profiles.json edit, no config change, no ALGORITHM_VERSION bump).

Gameplan row P0.3: "the standard 8-fixed-window Stage-3 T1-T7 gate has NOT
been run on this profile [the staged 30-DTE sprint] -- queue it as the
formalization step". The N=500 evidence that DOES exist
(experiments/apex_dte_dd/FINDINGS.md Result 4) is a MONTHLY-ROLLING-START
harness (concentration_2x/sweep.py, ~113 pooled windows) -- informative but
NOT the same instrument as the standard FIXED-window Stage-3 gate
(monte_carlo.py's own WINDOWS list) that every other portfolio ship is
measured against. This driver runs THAT instrument.

WINDOW SET: the full default 12-row WINDOWS list (bare -- no
WINDOWS_OVERRIDE filter), a strict superset of T3's 8 canonical windows
(2021/2022/2023/2024/2025/dip/22-now/5y) that ALSO naturally covers
2020/2020_crash/2018/10y. Per .claude/skills/run-monte-carlo/SKILL.md GUARD 2
("a T1-T7 pass on the 8 canonical windows alone that never touched
2020_crash is not a ship-grade result") and CLAUDE.md's own standing bar
("collapse incl 2020-COVID... means the survivor-corrected default 12-row
WINDOWS list") -- for a live-money 🔒 decision the superset is the right
instrument, and it costs nothing extra to implement (T3's 8 are a subset of
these 12, read directly off the same cells).

ARMS (3 -- baseline + both staged options, so the user's decision has BOTH
options' evidence side by side, not just one):
  baseline_live_apex_15dte : today's LIVE Apex (15-DTE), imported verbatim
      from experiments/holdout_oos_2026_12/run_h3_envelope.py's FROZEN_ENV.
      This is the T4/T5 baseline every delta below is measured against.
  staged_30dte_n4  (SHIP_HANDOFF.md Option A -- drop-in): FROZEN_ENV with
      exactly the 4 fields SHIP_HANDOFF.md names changed (NOMINAL_CAL_DTE
      15->30, HOLD_CAL_DAYS 13->27, SL_BASE_OV/SL_STRESS_OV -0.85->-0.70);
      tiers/caps/sat/DD-soft-band UNCHANGED from Apex (SHIP_HANDOFF.md:
      "tier_* stay 0.25, max_positions 4, call_max 4, gross_cap 0.9 --
      unchanged").
  staged_30dte_n10 (SHIP_HANDOFF.md Option B -- Pareto-best): Option A's 4
      fields PLUS the sizing SHIP_HANDOFF.md names (tiers 0.25->0.10 x4,
      max_positions/call_max 4->10, gross_cap/call_cap 0.9->1.0).
Built programmatically from FROZEN_ENV + the two named diffs -- never
hand-retyped, so there is no way for this driver's recipe to silently
diverge from SHIP_HANDOFF.md's own "How to apply" section.

N=500 (ship-tier, per the task spec -- this is the formal gate evidence).
Scale: 3 arms x 12 windows = 36 cells.

Usage (no required args -- queue-submittable bare):
  python -u experiments/apex_dte_dd/run_p03_evidence.py [--n-iter 500] [--cpu 4]

This driver renders NO ship verdict -- T4/T5/T6/T7 are computed as plain
arithmetic (deltas + the gate's own numeric thresholds) for the user to read;
the actual 🔒 apply-or-not call stays with the user, per P0.3's own charter.
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
    resolve_pinned_version, run_one_window, STANDARD_12,
)
from experiments.holdout_oos_2026_12.run_h3_envelope import FROZEN_ENV as APEX_LIVE_ENV  # noqa: E402

RESULTS = os.path.join(HERE, 'results_p03_evidence')
WINDOWS = STANDARD_12   # full default 12-row list; T3's 8 are a subset of this
T3_CANONICAL_8 = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

BASELINE_ENV = dict(APEX_LIVE_ENV)

OPTION_A_DIFF = {
    'NOMINAL_CAL_DTE': '30', 'HOLD_CAL_DAYS': '27',
    'SL_BASE_OV': '-0.70', 'SL_STRESS_OV': '-0.70',
}
STAGED_N4_ENV = dict(BASELINE_ENV)
STAGED_N4_ENV.update(OPTION_A_DIFF)

OPTION_B_EXTRA_DIFF = {
    'TIER_ULTRA_OV': '0.10', 'TIER_TOP_OV': '0.10', 'TIER_MID_OV': '0.10', 'TIER_LOW_OV': '0.10',
    'MAX_POSITIONS_OVERRIDE': '10', 'MAX_POSITIONS_CALL': '10',
    'GROSS_PREMIUM_CAP': '1.0', 'CALL_PREMIUM_CAP': '1.0',
}
STAGED_N10_ENV = dict(STAGED_N4_ENV)
STAGED_N10_ENV.update(OPTION_B_EXTRA_DIFF)

ARMS = {
    'baseline_live_apex_15dte': BASELINE_ENV,
    'staged_30dte_n4': STAGED_N4_ENV,
    'staged_30dte_n10': STAGED_N10_ENV,
}

DD_STABILITY_THRESHOLD_PP = 5.0   # T5
DD_5Y_TOLERANCE_PP = 1.0          # T4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=500)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--timeout-s', type=int, default=5400)
    args = ap.parse_args()

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[p03_evidence] N={args.n_iter}  arms={list(ARMS)}  windows={WINDOWS}  "
          f"pinned_version={pin_commit} (id={pin_id})", flush=True)
    for name, env in ARMS.items():
        print(f"  recipe[{name}] = {env}", flush=True)

    all_cells = {}
    for arm_name, arm_env in ARMS.items():
        all_cells[arm_name] = {}
        cell_dir = os.path.join(RESULTS, arm_name)
        for label in WINDOWS:
            out_path = os.path.join(cell_dir, f'{label}.json')
            res = run_one_window(arm_env, label, args.n_iter, out_path, pin_commit,
                                  deep=False, timeout_s=args.timeout_s, cpu=args.cpu)
            all_cells[arm_name][label] = res.get(label) if res else None

    # --- T1-T7 evidence table (plain arithmetic, no verdict) ---
    def gate_evidence(candidate_name):
        base = all_cells['baseline_live_apex_15dte']
        cand = all_cells[candidate_name]
        ev = {'t3_canonical_8_covered': all(w in WINDOWS for w in T3_CANONICAL_8),
              'per_window': {}, 't5_flagged_windows': [], 't6_collapse_windows': {}}
        for w in WINDOWS:
            b, c = base.get(w), cand.get(w)
            if b is None or c is None:
                ev['per_window'][w] = {'missing': True}
                continue
            d_dd = c['worst_dd'] - b['worst_dd']
            ev['per_window'][w] = {
                'baseline_worst_dd': b['worst_dd'], 'candidate_worst_dd': c['worst_dd'],
                'd_worst_dd_pp': d_dd,
                'baseline_p_coll': b['p_coll'], 'candidate_p_coll': c['p_coll'],
                'baseline_mean_ret': b['mean_ret'], 'candidate_mean_ret': c['mean_ret'],
            }
            if abs(d_dd) > DD_STABILITY_THRESHOLD_PP:
                ev['t5_flagged_windows'].append({'window': w, 'd_worst_dd_pp': d_dd})
            ev['t6_collapse_windows'][w] = {'baseline': b['p_coll'], 'candidate': c['p_coll']}
        fivey = ev['per_window'].get('5y', {})
        if not fivey.get('missing'):
            ev['t4_5y_dd_delta_pp'] = fivey['d_worst_dd_pp']
            ev['t4_within_plus1pp'] = fivey['d_worst_dd_pp'] <= DD_5Y_TOLERANCE_PP
        ev['t6_any_collapse_gt0'] = any(
            (v.get('baseline') or 0) > 0 or (v.get('candidate') or 0) > 0
            for v in ev['t6_collapse_windows'].values()
        )
        return ev

    evidence = {
        'staged_30dte_n4': gate_evidence('staged_30dte_n4'),
        'staged_30dte_n10': gate_evidence('staged_30dte_n10'),
    }

    summary = {
        'note': 'EVIDENCE ONLY -- no config applied, no verdict rendered. T4/T5/T6 fields '
                'are plain arithmetic against assessment-backtest.md\'s own numeric '
                'thresholds (T4 +1.0pp on 5y, T5 +-5pp per-window, T6 collapse==0); the '
                'apply-or-not call is the user\'s 🔒 per P0.3.',
        'n_iter': args.n_iter,
        'windows': WINDOWS,
        't3_canonical_8': T3_CANONICAL_8,
        'recipes': ARMS,
        'pinned_version_commit': pin_commit,
        'pinned_version_id': pin_id,
        'cells': all_cells,
        'gate_evidence': evidence,
    }
    out_summary = os.path.join(RESULTS, 'summary.json')
    os.makedirs(RESULTS, exist_ok=True)
    with open(out_summary, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"\n[p03_evidence] wrote {out_summary}", flush=True)

    missing = [f"{a}/{w}" for a in ARMS for w in WINDOWS if all_cells[a].get(w) is None]
    if missing:
        print(f"[p03_evidence] {len(missing)} cell(s) still missing -- resubmit to fill "
              f"them in: {missing}", flush=True)
    else:
        print("[p03_evidence] all cells complete.", flush=True)

    print("\n=== T1-T7 evidence (vs baseline_live_apex_15dte) ===", flush=True)
    for cand in ('staged_30dte_n4', 'staged_30dte_n10'):
        ev = evidence[cand]
        print(f"\n  {cand}:", flush=True)
        print(f"    T3 canonical-8 covered: {ev['t3_canonical_8_covered']}", flush=True)
        print(f"    T4 5y DD delta: {ev.get('t4_5y_dd_delta_pp')} pp "
              f"(within +{DD_5Y_TOLERANCE_PP}pp: {ev.get('t4_within_plus1pp')})", flush=True)
        print(f"    T5 windows flagged (|delta|>{DD_STABILITY_THRESHOLD_PP}pp): "
              f"{ev['t5_flagged_windows']}", flush=True)
        print(f"    T6 any p_collapse>0 (baseline or candidate): {ev['t6_any_collapse_gt0']}",
              flush=True)


if __name__ == '__main__':
    main()
