"""
P1.2 -- deep-window crash SCREEN (not gate). Gameplan row P1.2 / gitnexus
commit ab2726a3c (MC_WINDOW_SET=deep). Runs the 4 named pre-2016 SCREEN
windows (ltcm_1998, dotcom_crash_2000_2002, gfc_crash_2007_2009, 2007_now)
PLUS the standard 12-row default WINDOWS list -- i.e. every label
MC_WINDOW_SET=deep makes available -- at N=300, for both the Core and Apex
LIVE profiles.

DOCTRINE (assessment-backtest.md "Deep-window screens (SCREEN, not GATE)",
commit 6b155033b): these deep windows are survivor-only (riding the 1995
v74 score+regime+breadth backfill) and are NEVER a calibration/tuning/ship
target. A deep FAIL is a mandatory mechanism investigation, never an
automatic revert. A deep PASS is weak comfort, never collapse-proof. This
driver only PRESENTS the numbers (per-window/per-profile JSONs + a plain
side-by-side summary) -- it renders no verdict.

PROFILE ENV RECIPES (do not re-derive -- verified against source):
  Core: STRATEGY_30DTE bare defaults. Verified line-by-line against
    strategy_config.py (MAX_POSITIONS=14, TIER_ALLOC 20/15/8/3/overflow-0,
    GROSS_PREMIUM_CAP=CALL_PREMIUM_CAP=0.50, PUT_PREMIUM_CAP=0.0, OPP_SAT_*
    16.0/4.0/0.50/0.55, DD_SOFT_BAND .35/.55/.40, all 6 DD levers already
    True on monte_carlo.py's own base _cfg=STRATEGY_30DTE) IS
    algorithm_versions/portfolio_profiles.json's "core" entry byte-for-byte
    -- so the Core arm is a bare invocation, no env overrides at all beyond
    the sizing knobs set explicitly below for self-documentation /
    robustness against any stray inherited env leakage from the queue
    worker's shell.
  Apex: imported verbatim from experiments/holdout_oos_2026_12/
    run_h3_envelope.py's FROZEN_ENV -- the pre-registered, documented
    "live-Apex-15DTE recipe" (NOMINAL_CAL_DTE=15/HOLD_CAL_DAYS=13,
    SL_BASE/STRESS_OV=-0.85, flat tiers 0.25x4, MaxPos 4, gross/call cap
    0.9). Imported, not copied, so this driver can never silently drift
    from that frozen recipe.

Usage (no required args -- queue-submittable bare):
  python -u experiments/deep_crash_screen/run_screen.py [--n-iter 300] [--cpu 4]

Resume-safe: skips any (profile, window) cell whose JSON already exists
under results/<profile>/<window>.json.
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
from experiments.holdout_oos_2026_12.run_h3_envelope import FROZEN_ENV as APEX_LIVE_ENV  # noqa: E402

RESULTS = os.path.join(HERE, 'results')

ALL_WINDOWS = STANDARD_12 + DEEP_4   # 16 labels

# Core = bare STRATEGY_30DTE defaults, spelled out explicitly for
# self-documentation and to be robust against stray env leakage (see
# docstring -- these values are byte-identical to leaving them unset).
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

PROFILES = {
    'core': CORE_ENV,
    'apex': dict(APEX_LIVE_ENV),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=300)
    ap.add_argument('--cpu', type=int, default=4)
    ap.add_argument('--timeout-s', type=int, default=3600,
                    help='per-(profile,window) subprocess cap; the 2007_now '
                         '(19y) and dotcom_crash_2000_2002 (2.5y) windows are '
                         'the likely long poles')
    args = ap.parse_args()

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[deep_crash_screen] N={args.n_iter}  profiles={list(PROFILES)}  "
          f"windows={ALL_WINDOWS}  pinned_version={pin_commit} (id={pin_id})", flush=True)

    all_cells = {}   # profile -> {window: result-dict-or-None}
    for profile, env in PROFILES.items():
        all_cells[profile] = {}
        prof_dir = os.path.join(RESULTS, profile)
        for label in ALL_WINDOWS:
            out_path = os.path.join(prof_dir, f'{label}.json')
            res = run_one_window(env, label, args.n_iter, out_path, pin_commit,
                                  deep=True, timeout_s=args.timeout_s, cpu=args.cpu)
            all_cells[profile][label] = res.get(label) if res else None

    # Plain side-by-side summary -- no verdict, just the numbers + doctrine note.
    summary = {
        'doctrine': 'SCREEN, not GATE -- see assessment-backtest.md "Deep-window '
                    'screens (SCREEN, not GATE)" + gitnexus 6b155033b. A deep FAIL '
                    'is a mandatory mechanism investigation, never an automatic '
                    'revert. A deep PASS is weak comfort, never collapse-proof.',
        'n_iter': args.n_iter,
        'pinned_version_commit': pin_commit,
        'pinned_version_id': pin_id,
        'standard_12_windows': STANDARD_12,
        'deep_4_windows': DEEP_4,
        'cells': all_cells,
    }
    deep_focus = {}
    for profile in PROFILES:
        deep_focus[profile] = {
            label: all_cells[profile].get(label) for label in DEEP_4
        }
    summary['deep_windows_only'] = deep_focus

    out_summary = os.path.join(RESULTS, 'summary.json')
    with open(out_summary, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f"\n[deep_crash_screen] wrote {out_summary}", flush=True)

    missing = [f"{p}/{w}" for p in PROFILES for w in ALL_WINDOWS if all_cells[p].get(w) is None]
    if missing:
        print(f"[deep_crash_screen] {len(missing)} cell(s) still missing "
              f"(timeout/fail) -- resubmit to fill them in: {missing}", flush=True)
    else:
        print("[deep_crash_screen] all cells complete.", flush=True)

    print("\n=== DEEP WINDOWS ONLY (worst_dd / p_coll / mean_ret) ===", flush=True)
    for label in DEEP_4:
        print(f"  {label}:", flush=True)
        for profile in PROFILES:
            r = deep_focus[profile].get(label)
            if r is None:
                print(f"    {profile:>6}: (missing)", flush=True)
            else:
                print(f"    {profile:>6}: worst_dd={r.get('worst_dd')}  "
                      f"mean_dd={r.get('mean_dd')}  p_coll={r.get('p_coll')}  "
                      f"mean_ret={r.get('mean_ret')}  med_ret={r.get('med_ret')}", flush=True)


if __name__ == '__main__':
    main()
