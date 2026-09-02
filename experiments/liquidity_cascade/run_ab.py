"""
P3.6 -- Liquidity-Aware Cascade Stage-3 A/B (gameplan.md P3.6 / known-issues.md
open-item #10). Full design + pre-registered floor grid + real-distribution
justification: experiments/liquidity_cascade/DESIGN.md -- read that FIRST,
this driver only executes what it pre-registers.

Question this answers: does an option_volume_30d liquidity skip move Stage-3
DD/collapse (T1-T7-style), and by how much, across the 9-window ship-
validation set x {Core, Apex}? Per DESIGN.md section 5.1, the PRE-REGISTERED
expectation is that any effect concentrates in windows overlapping the real
option_prices coverage window (2025-02-10+) -- real per-contract volume and
the pre-coverage equity-based fallback proxy are ~3-4 orders of magnitude
apart in scale, so the floor is expected to be a near-no-op on fallback-
tagged signals at every grid value. A near-zero delta on pre-2025 windows is
the PREDICTED result, not evidence the mechanism is inert -- read the honest
per-window/per-profile cells, do not average across windows that structurally
can't respond. No verdict is rendered here -- FABLE judges.

ARMS: LIQUIDITY_FLOOR in {0 (OFF/baseline), 5, 20, 100} -- the pre-registered
grid (DESIGN.md section 5), chosen from the REAL-only option_volume_30d
distribution (not the original known-issues guess, which the real measurement
supersedes). PAIRED (same window label -> identical seed per
.claude/skills/run-monte-carlo/SKILL.md GUARD 3).

PROFILES: Core (bare STRATEGY_30DTE defaults) + Apex (imported verbatim from
experiments/holdout_oos_2026_12/run_h3_envelope.py's FROZEN_ENV). Same
provenance as experiments/vega_state/run_ab.py and experiments/
deep_crash_screen/run_screen.py -- not re-derived.

WINDOWS: house "ship validation" 9-window set (T3's 8 canonical +
2020_crash), per .claude/skills/run-monte-carlo/SKILL.md GUARD 2 + section 4.

PREREQUISITE (this script's own first step, resume-safe): ensures the FULL
(non-smoke) option_volume_30d feature parquet exists -- calls
build_option_volume.main() directly, which no-ops via materialize_polars's
own freshness check if the parquet is already built. Without this, every
LIQUIDITY_FLOOR>0 arm would silently no-op (empty map -> filter passes
everything through) and the "A/B" would be four identical no-op arms.

N=500. Scale: 4 arms x 2 profiles x 9 windows = 72 cells.

Usage (no required args -- queue-submittable bare):
  python -u experiments/liquidity_cascade/run_ab.py [--n-iter 500] [--cpu 4]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from experiments._mc_pinned_runner import (  # noqa: E402
    resolve_pinned_version, run_one_window, SHIP_VALIDATION_9,
)
from experiments.holdout_oos_2026_12.run_h3_envelope import FROZEN_ENV as APEX_LIVE_ENV  # noqa: E402

RESULTS = os.path.join(HERE, 'results')
WINDOWS = SHIP_VALIDATION_9

# Verbatim from experiments/vega_state/run_ab.py -- same Core representation
# used by the most recent comparable Stage-3 A/B driver, not re-derived.
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

# Pre-registered grid, DESIGN.md section 5. floor_off is the required baseline.
ARMS = {
    'floor_off': {'LIQUIDITY_FLOOR': '0'},
    'floor_5':   {'LIQUIDITY_FLOOR': '5'},
    'floor_20':  {'LIQUIDITY_FLOOR': '20'},
    'floor_100': {'LIQUIDITY_FLOOR': '100'},
}
BASELINE_ARM = 'floor_off'


def _ensure_feature_cache():
    """Build the FULL option_volume_30d parquet if not already fresh.
    Resume-safe via materialize_polars's own is_fresh check -- a re-run of
    this whole driver after a queue restart does not rebuild it twice."""
    from database.bulk_cache import cache_path
    path = cache_path('liquidity_option_volume_30d')
    if path.exists():
        print(f"[liquidity_cascade_ab] feature cache already present: {path}", flush=True)
        return
    print("[liquidity_cascade_ab] feature cache missing -- building full "
          "(non-smoke) option_volume_30d now (db=heavy, real-era per-signal "
          "joins + one fallback-era bulk pull; see build_option_volume.py)...",
          flush=True)
    import experiments.liquidity_cascade.build_option_volume as bov
    bov.main()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=500)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--timeout-s', type=int, default=3600)
    args = ap.parse_args()

    _ensure_feature_cache()

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[liquidity_cascade_ab] N={args.n_iter}  arms={list(ARMS)}  "
          f"profiles={list(PROFILES)}  windows={WINDOWS}  "
          f"pinned_version={pin_commit} (id={pin_id})", flush=True)

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

    # Plain paired diffs (each floor arm - floor_off) per profile/window -- no verdict.
    diffs = {}
    for arm_name in ARMS:
        if arm_name == BASELINE_ARM:
            continue
        diffs[arm_name] = {}
        for profile in PROFILES:
            diffs[arm_name][profile] = {}
            for label in WINDOWS:
                off = all_cells[BASELINE_ARM][profile].get(label)
                on = all_cells[arm_name][profile].get(label)
                if off is None or on is None:
                    diffs[arm_name][profile][label] = None
                    continue
                diffs[arm_name][profile][label] = {
                    'd_worst_dd': (on.get('worst_dd') - off.get('worst_dd'))
                                  if (on.get('worst_dd') is not None and off.get('worst_dd') is not None) else None,
                    'd_p_coll': (on.get('p_coll') - off.get('p_coll'))
                                if (on.get('p_coll') is not None and off.get('p_coll') is not None) else None,
                    'd_mean_ret': (on.get('mean_ret') - off.get('mean_ret'))
                                  if (on.get('mean_ret') is not None and off.get('mean_ret') is not None) else None,
                }

    summary = {
        'note': 'Paired LIQUIDITY_FLOOR arm-vs-floor_off A/B. Pre-registered expectation '
                '(DESIGN.md section 5.1): effect concentrates in windows overlapping real '
                'option_prices coverage (2025-02-10+); near-zero delta on pre-2025 windows '
                'is PREDICTED, not a null finding. DD/collapse primary; compound regression '
                'is the expected fill-realism fee. No verdict rendered here -- FABLE judges.',
        'design_doc': 'experiments/liquidity_cascade/DESIGN.md',
        'n_iter': args.n_iter,
        'windows': WINDOWS,
        'profiles': list(PROFILES),
        'arms': {k: v for k, v in ARMS.items()},
        'baseline_arm': BASELINE_ARM,
        'pinned_version_commit': pin_commit,
        'pinned_version_id': pin_id,
        'cells': all_cells,
        'paired_diff_vs_floor_off': diffs,
    }
    out_summary = os.path.join(RESULTS, 'summary.json')
    os.makedirs(RESULTS, exist_ok=True)
    with open(out_summary, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"\n[liquidity_cascade_ab] wrote {out_summary}", flush=True)

    missing = [f"{a}/{p}/{w}" for a in ARMS for p in PROFILES for w in WINDOWS
              if all_cells[a][p].get(w) is None]
    if missing:
        print(f"[liquidity_cascade_ab] {len(missing)} cell(s) still missing -- resubmit to "
              f"fill them in: {missing}", flush=True)
    else:
        print("[liquidity_cascade_ab] all cells complete.", flush=True)

    print("\n=== paired diffs (arm - floor_off) ===", flush=True)
    for arm_name in diffs:
        print(f"  {arm_name}:", flush=True)
        for profile in PROFILES:
            print(f"    {profile}:", flush=True)
            for label in WINDOWS:
                d = diffs[arm_name][profile].get(label)
                print(f"      {label:<10} {d}", flush=True)


if __name__ == '__main__':
    main()
