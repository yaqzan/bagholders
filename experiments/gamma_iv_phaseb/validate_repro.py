"""
Gamma x IV Phase B -- validation-arm repro comparator (see DESIGN.md).

Compares the `valgoff` arm's output (both IV_PREMIUM/GAMMA_AWARE flags unset,
ORIGINAL goff settings: N_ITER=150, full 2016-06-01 grid, cell flat_n4_a25)
against the archived experiments/concentration_2x/results/sweep_drill_goff.json
+ drill_goff_partial/roll_*.json. Expects EXACT equality -- the engine is
seeded/deterministic and the IV plug is a provable no-op when IV_PREMIUM is
unset (hand-verified in DESIGN.md's "Implementation notes" section). ANY diff
means STOP and investigate (distinguish edit breakage from underlying data
drift, e.g. yfinance retro-adjustments, by checking whether diffs are uniform
across windows).

This script reports facts (MATCH / MISMATCH) only. It does not render a ship
verdict -- that is FABLE's call.

Usage: python experiments/gamma_iv_phaseb/validate_repro.py
ASCII-only output.
"""
import json
import os
import sys

REPO = r"C:\Development\Trader"
RESULTS = os.path.join(REPO, "experiments", "concentration_2x", "results")

REF_AGG = os.path.join(RESULTS, "sweep_drill_goff.json")
NEW_AGG = os.path.join(RESULTS, "sweep_drill_phaseb_valgoff.json")
REF_PARTIAL_DIR = os.path.join(RESULTS, "drill_goff_partial")
NEW_PARTIAL_DIR = os.path.join(RESULTS, "drill_phaseb_valgoff_partial")

CELL = "flat_n4_a25"


def load(path):
    with open(path, 'r') as f:
        return json.load(f)


def compare_aggregate():
    if not os.path.exists(NEW_AGG):
        print(f"[AGG] MISSING {NEW_AGG}")
        return False
    ref = load(REF_AGG)
    new = load(NEW_AGG)
    ref_cell = next((c for c in ref['cells'] if c['cell'] == CELL), None)
    new_cell = next((c for c in new['cells'] if c['cell'] == CELL), None)
    if ref_cell is None or new_cell is None:
        print(f"[AGG] MISSING cell {CELL}: ref_has={bool(ref_cell)} new_has={bool(new_cell)}")
        return False
    diffs = []
    for k in sorted(set(ref_cell) | set(new_cell)):
        rv, nv = ref_cell.get(k), new_cell.get(k)
        if rv != nv:
            diffs.append((k, rv, nv))
    if diffs:
        print(f"[AGG] MISMATCH on {len(diffs)} field(s):")
        for k, rv, nv in diffs:
            print(f"    {k}: ref={rv!r}  new={nv!r}")
        return False
    print(f"[AGG] EXACT MATCH -- all {len(ref_cell)} fields identical for cell={CELL}")
    print(f"      n_paths={ref_cell.get('n_paths')}  n_windows={ref_cell.get('n_windows')}")
    return True


def compare_partials():
    if not os.path.isdir(NEW_PARTIAL_DIR):
        print(f"[PARTIAL] MISSING dir {NEW_PARTIAL_DIR}")
        return False
    ref_files = sorted(f for f in os.listdir(REF_PARTIAL_DIR)
                       if f.startswith('roll_') and f.endswith('.json'))
    new_files = sorted(f for f in os.listdir(NEW_PARTIAL_DIR)
                       if f.startswith('roll_') and f.endswith('.json'))
    ref_set, new_set = set(ref_files), set(new_files)
    only_ref = sorted(ref_set - new_set)
    only_new = sorted(new_set - ref_set)
    if only_ref:
        print(f"[PARTIAL] {len(only_ref)} window(s) in ref but MISSING in new: "
              f"{only_ref[:5]}{' ...' if len(only_ref) > 5 else ''}")
    if only_new:
        print(f"[PARTIAL] {len(only_new)} window(s) in new but not in ref (unexpected extra): "
              f"{only_new[:5]}{' ...' if len(only_new) > 5 else ''}")
    common = sorted(ref_set & new_set)
    n_match = 0
    n_mismatch = 0
    mismatch_examples = []
    for fn in common:
        ref_data = load(os.path.join(REF_PARTIAL_DIR, fn))
        new_data = load(os.path.join(NEW_PARTIAL_DIR, fn))
        ref_paths = ref_data.get('cells', {}).get(CELL)
        new_paths = new_data.get('cells', {}).get(CELL)
        if ref_paths == new_paths:
            n_match += 1
        else:
            n_mismatch += 1
            if len(mismatch_examples) < 3:
                mismatch_examples.append(fn)
    print(f"[PARTIAL] {n_match}/{len(common)} common windows EXACT MATCH on cell={CELL} "
          f"path records ({n_mismatch} mismatched)")
    if mismatch_examples:
        print(f"[PARTIAL] example mismatched windows: {mismatch_examples}")
    return n_mismatch == 0 and not only_ref and len(common) == len(ref_files)


def main():
    print("=" * 72)
    print("Gamma x IV Phase B -- validation-arm (valgoff) repro comparator")
    print(f"  ref (archived): {REF_AGG}")
    print(f"  new (valgoff) : {NEW_AGG}")
    print("=" * 72)
    ok_agg = compare_aggregate()
    ok_partial = compare_partials()
    print("=" * 72)
    print(f"RESULT: aggregate={'EXACT MATCH' if ok_agg else 'MISMATCH/MISSING'}  "
          f"partials={'EXACT MATCH' if ok_partial else 'MISMATCH/MISSING'}")
    print("=" * 72)
    sys.exit(0 if (ok_agg and ok_partial) else 1)


if __name__ == "__main__":
    main()
