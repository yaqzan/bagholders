#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_phaseB_cells.py -- ONE-TIME mechanical derivation of the Phase B cell
grid (PREREG.md section 4 "fill rule"). Run once; its printed output was
hand-copied into phaseB_run.py's CORE_CELLS / APEX_CELLS as LOCKED literals
(same pattern as phaseA_run.py's TP_GRID/SL_GRID -- Phase B does not
re-derive this at job-run time, so a queue job never depends on
out/phaseA_*.csv still being present/unchanged on disk).

Rule (PREREG.md section 4, mechanical):
  for each carried cell (tp, sl): add (tp-0.05,sl), (tp+0.05,sl),
  (tp,sl-0.05), (tp,sl+0.05); clip tp to [0.10,1.10], sl to [-1.00,-0.25];
  dedup against carried and against each other; final = carried U fills.
  If new cells exceed 30/profile, drop fills whose PARENT carried cell
  ranks worst on Phase A 22-now worst_dd, until at 30.

Dedup rule (this module's mechanical reading, "against each other" is
underspecified in PREREG beyond "dedup"): first-seen-wins in CARRIED-list
traversal order -- i.e. a fill cell claimed by an earlier parent (per the
verbatim carried-list order given in the Phase B brief) is not re-generated
for a later parent that would also produce it. This makes the result a pure
function of (carried list order, Phase A 22-now worst_dd ranking) -- no
other ordering choices enter.

Cap-30 tie-break rule (this module's mechanical reading): rank carried
PARENTS worst-to-best by Phase A 22-now worst_dd (worst = highest dd first,
DD-primary doctrine). Walk that order; for each parent, drop its own new
fill cells one at a time (in the fixed generation order: tp-0.05, tp+0.05,
sl-0.05, sl+0.05, skipping any already deduped away) until the running total
hits exactly `cap`. A parent's carried cell itself is NEVER dropped -- only
cells it contributed as NEW fills.

Both CORE and APEX independently land at EXACTLY 30/30 under this rule
(verified below) -- the cap machinery fires for both profiles.
"""
from __future__ import annotations

import csv
import glob
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
EXP_DIR = os.path.dirname(_THIS_DIR)
OUT_DIR = os.path.join(EXP_DIR, 'out')

# Carried lists -- VERBATIM from the Phase B brief (itself sourced from
# out/phaseA_summary.md's "CARRIED to Phase B" tables; Core's list additionally
# folds in the (0.30,-0.70) incumbent that the summary's cap-15 truncated out,
# per LESSONS.md's 2026-08-10 "spec note" -- PREREG section 1 mandates the
# incumbent as the in-phase baseline regardless of the carry list). NOT
# re-derived from the CSVs here -- use as given, only the FILL step is new.
CORE_CARRIED = [
    (0.15, -0.90), (0.15, -1.00), (0.15, -0.60), (0.20, -0.70), (0.15, -0.50),
    (0.15, -0.70), (0.20, -0.50), (0.25, -1.00), (0.25, -0.90), (0.20, -1.00),
    (0.25, -0.80), (0.15, -0.80), (0.25, -0.70), (0.30, -0.80), (0.30, -0.60),
    (0.30, -0.70),
]
APEX_CARRIED = [
    (0.15, -0.60), (0.15, -0.30), (0.15, -0.50), (0.15, -0.40), (0.15, -0.80),
    (0.15, -0.90), (0.15, -0.70), (0.20, -0.50), (0.15, -1.00), (0.20, -0.60),
    (0.20, -0.30), (0.25, -0.50), (0.25, -0.70), (0.30, -0.70), (0.30, -0.60),
]

TP_MIN, TP_MAX = 0.10, 1.10
SL_MIN, SL_MAX = -1.00, -0.25
CAP_PER_PROFILE = 30


def _r2(x):
    return round(x + 0.0, 2)


def _clip_tp(tp):
    return _r2(max(TP_MIN, min(TP_MAX, tp)))


def _clip_sl(sl):
    return _r2(max(SL_MIN, min(SL_MAX, sl)))


def gen_fills(tp, sl):
    """Raw (pre-dedup) fill candidates in PREREG's literal order."""
    return [
        (_clip_tp(tp - 0.05), _r2(sl)),
        (_clip_tp(tp + 0.05), _r2(sl)),
        (_r2(tp), _clip_sl(sl - 0.05)),
        (_r2(tp), _clip_sl(sl + 0.05)),
    ]


def load_22now_worstdd(profile, out_dir=OUT_DIR):
    """(tp,sl) -> Phase A 22-now worst_dd, from out/phaseA_*.csv (excludes
    smoke files). Used ONLY for the cap-30 tie-break ranking."""
    out = {}
    for p in glob.glob(os.path.join(out_dir, 'phaseA_*.csv')):
        if 'smoke' in os.path.basename(p).lower():
            continue
        with open(p, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if row['profile'] != profile or row['window'] != '22-now':
                    continue
                key = (_r2(float(row['tp'])), _r2(float(row['sl'])))
                out[key] = float(row['worst_dd'])
    return out


def compute_final_cells(carried, dd_lookup, cap=CAP_PER_PROFILE):
    """Returns (final_sorted_list, diagnostics_dict). `carried`: ordered list
    of (tp,sl) tuples (order matters for first-seen dedup). `dd_lookup`:
    (tp,sl) -> Phase A 22-now worst_dd, MUST cover every carried cell."""
    carried = [(_r2(t), _r2(s)) for (t, s) in carried]
    if len(set(carried)) != len(carried):
        raise SystemExit(f"duplicate cell in carried list: {carried}")
    missing = [c for c in carried if c not in dd_lookup]
    if missing:
        raise SystemExit(f"carried cells missing 22-now Phase A worst_dd: {missing}")

    seen = set(carried)
    fills_by_parent = {}
    for parent in carried:
        new_for_parent = []
        for f in gen_fills(*parent):
            if f in seen:
                continue
            seen.add(f)
            new_for_parent.append(f)
        fills_by_parent[parent] = new_for_parent

    final_set = set(carried)
    for v in fills_by_parent.values():
        final_set.update(v)
    n_before_cap = len(final_set)

    dropped = []
    if n_before_cap > cap:
        worst_first = sorted(carried, key=lambda p: dd_lookup[p], reverse=True)
        cur = set(final_set)
        for parent in worst_first:
            if len(cur) <= cap:
                break
            for f in fills_by_parent[parent]:
                if len(cur) <= cap:
                    break
                if f in cur:
                    cur.discard(f)
                    dropped.append((parent, f))
        final_set = cur

    final_sorted = sorted(final_set)

    # Self-checks -- mechanical derivation, no judgment calls, so these must
    # hold unconditionally.
    assert len(final_sorted) == min(n_before_cap, cap), (len(final_sorted), n_before_cap, cap)
    assert set(carried) <= final_set, "a carried cell was dropped -- only fills may be dropped"
    for tp, sl in final_sorted:
        assert TP_MIN - 1e-9 <= tp <= TP_MAX + 1e-9, (tp, sl)
        assert SL_MIN - 1e-9 <= sl <= SL_MAX + 1e-9, (tp, sl)
    assert len(final_sorted) == len(set(final_sorted)), "dedup failed -- duplicate in final set"

    diagnostics = dict(
        n_carried=len(carried), n_fill_candidates_raw=len(carried) * 4,
        n_fill_candidates_post_dedup=sum(len(v) for v in fills_by_parent.values()),
        n_before_cap=n_before_cap, n_dropped=len(dropped), n_final=len(final_sorted),
        dropped=dropped, fills_by_parent=fills_by_parent,
    )
    return final_sorted, diagnostics


def _report(profile, carried):
    dd_lookup = load_22now_worstdd(profile)
    final, diag = compute_final_cells(carried, dd_lookup)
    print(f"\n=== {profile} ===")
    print(f"carried={diag['n_carried']}  raw_candidates={diag['n_fill_candidates_raw']}  "
          f"post_dedup_fills={diag['n_fill_candidates_post_dedup']}  "
          f"before_cap={diag['n_before_cap']}  dropped={diag['n_dropped']}  final={diag['n_final']}")
    if diag['dropped']:
        print("dropped (parent -> fill):")
        for parent, f in diag['dropped']:
            print(f"   {parent} -> {f}")
    print(f"FINAL {profile.upper()}_CELLS ({len(final)}):")
    print(final)
    return final, diag


if __name__ == '__main__':
    core_final, core_diag = _report('core', CORE_CARRIED)
    apex_final, apex_diag = _report('apex', APEX_CARRIED)
    assert len(core_final) == 30 and len(apex_final) == 30, "expected exactly 30/30 -- re-check cap logic"
    assert (0.30, -0.70) in core_final and (0.30, -0.70) in apex_final, "incumbent missing from final set"
    print("\n[OK] both profiles land at exactly 30 cells; incumbent present in both.")
