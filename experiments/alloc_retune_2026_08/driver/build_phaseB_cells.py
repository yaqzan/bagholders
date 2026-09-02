#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_phaseB_cells.py -- ONE-TIME mechanical derivation of the Phase B cell
sets (PREREG.md section 1 "Refine" + the Phase B dispatch brief's exact
carried lists and neighbor rule). Run once; its printed output is hand-copied
into allocB_run.py's CORE_CELLS / APEX_CELLS as LOCKED literals (same pattern
as experiments/tpsl_refine_2026_08/driver/build_phaseB_cells.py -- Phase B
does not re-derive this at job-run time, so a queue job never depends on
out/allocA_*.csv / out/allocA_summary.md still being present/unchanged).

Rule (Phase B dispatch brief, mechanical, generalizing the tpsl precedent's
+-0.05 TP/SL fill rule from a 2-axis grid to alloc's 3-axis Core (shape x
MaxPos x gross) and 2-axis Apex (n x flat_frac) grids):

CORE -- for each carried (shape, max_pos, gross) cell, generate ONE grid step
per NUMERIC axis, holding the other axes (incl. shape) at the parent's own
value:
    (shape, max_pos-2, gross), (shape, max_pos+2, gross)   -- MaxPos axis
    (shape, max_pos, gross_pred), (shape, max_pos, gross_succ)  -- Gross axis
  max_pos clipped to [8,22] (grid spacing 2; one step beyond the Phase A
  blast bounds [10,20], mirroring the tpsl fill-rule precedent of extending
  past the blast floor/ceiling by one step).
  gross adjacency uses the extended list [0.30,0.40,0.50,0.65,0.80] (0.30 is
  one step below the Phase A blast floor 0.40; 0.80 was already the Phase A
  ceiling, so it is not a further extension -- it appears here only as the
  successor of a carried cell AT 0.65, which mechanically happens for
  exactly one parent, S9/mp20/g0.65).
  SHAPE axis has NO ordering -- never stepped; only {S9,S8,S3} (the carried
  cells' own shapes) ever appear. The in-phase baseline S0/mp14/g0.50 is
  OUTSIDE this shape universe entirely (S0 is not one of {S9,S8,S3}) and is
  "ALSO always include[d]" per the brief -- i.e. added as one static extra
  cell, not folded into CORE_CARRIED as a 16th neighbor-generating parent
  (unlike the tpsl precedent, which DID fold its incumbent into the carried
  list -- the alloc brief's wording is explicitly different: "shapes stay
  fixed to {S9,S8,S3}" scopes the neighbor machinery to just those three).
  Cap: 40 TOTAL core cells = 15 carried + 1 baseline (both always present,
  never truncated) + up to 24 new neighbor fills.

APEX -- for each carried (n, frac) cell, generate one grid step per axis:
    (n-2, frac), (n+2, frac)              -- n axis, clipped to [4,16]
    (n, frac_pred), (n, frac_succ)        -- frac axis, extended list
                                              [0.06,0.08,0.10,0.125,0.15]
  EVERY candidate (both axes) is then filtered by the band n*frac in
  [0.60,1.05] -- same filter Phase A applied, "STILL APPLIES to every cell"
  per the brief. Baseline n10/f0.10 is already inside APEX_CARRIED (all 9
  Phase A survivors are carried), so no separate flat-add step is needed for
  Apex. Cap 25 (carried=9 always present + new fills, capped at 25 total).

Dedup rule (mirrors the tpsl precedent's stated reading, generalized):
first-seen-wins in CARRIED-list traversal order -- i.e. a candidate cell
already claimed (as carried, or as an earlier parent's new fill) is not
re-generated for a later parent that would also produce it. This makes the
result a pure function of (carried list order, Phase A 22-now worst_dd
ranking) -- no other ordering choices enter. CORE_CARRIED / APEX_CARRIED
below are VERBATIM the orchestrator's Phase B dispatch order (== the
out/allocA_summary.md "CARRIED to Phase B" table row order).

Cap tie-break rule (verbatim mechanism from the tpsl precedent): rank
carried PARENTS worst-to-best by Phase A 22-now worst_dd (worst = highest dd
first, DD-primary doctrine; Python's stable sort preserves CORE_CARRIED's
own traversal order for any dd ties -- and there ARE exact float ties here,
see LESSONS.md). Walk that order; for each parent, drop its own new fill
cells one at a time (in the fixed per-axis generation order given above,
skipping any already deduped away) until the running total hits exactly
`cap`. A parent's carried cell itself -- and the flat-added baseline -- are
NEVER dropped, only cells a parent contributed as NEW fills.
"""
from __future__ import annotations

import csv
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
EXP_DIR = os.path.dirname(_THIS_DIR)
OUT_DIR = os.path.join(EXP_DIR, 'out')

# Carried lists -- VERBATIM from the Phase B dispatch brief (itself sourced
# from out/allocA_summary.md's "CARRIED to Phase B" tables). NOT re-derived
# from the CSVs here -- use as given, only the NEIGHBOR step is new.
CORE_CARRIED = [
    ('S9', 10, 0.40), ('S8', 14, 0.50), ('S8', 20, 0.50), ('S8', 10, 0.50),
    ('S8', 18, 0.50), ('S8', 16, 0.50), ('S8', 12, 0.50), ('S9', 14, 0.40),
    ('S9', 20, 0.40), ('S9', 12, 0.40), ('S9', 18, 0.40), ('S9', 16, 0.40),
    ('S9', 20, 0.50), ('S9', 20, 0.65), ('S3', 12, 0.40),
]
CORE_BASELINE = ('S0', 14, 0.50)   # flat add-on -- see module docstring; never generates neighbors

APEX_CARRIED = [
    (6, 0.100), (6, 0.125), (6, 0.150), (8, 0.080), (8, 0.100),
    (8, 0.125), (10, 0.080), (10, 0.100), (12, 0.080),
]
APEX_BASELINE = (10, 0.100)   # already inside APEX_CARRIED -- no separate add needed

CORE_MAXPOS_RANGE = (8, 22)
CORE_GROSS_EXT = [0.30, 0.40, 0.50, 0.65, 0.80]
CORE_CAP = 40
CORE_FIXED = len(CORE_CARRIED) + 1   # + baseline

APEX_N_RANGE = (4, 16)
APEX_FRAC_EXT = [0.06, 0.08, 0.10, 0.125, 0.15]
APEX_BAND = (0.60, 1.05)
APEX_CAP = 25


def _r2(x):
    return round(float(x) + 0.0, 2)


def _r3(x):
    return round(float(x) + 0.0, 3)


def _adjacent(value, ext_list, ndig):
    """predecessor/successor of `value` in the ordered ext_list, rounded to
    ndig for robust float-literal membership. Returns a list of 0-2 values."""
    r = round if ndig is None else (lambda v: round(v, ndig))
    vals = [r(v) for v in ext_list]
    rv = r(value)
    if rv not in vals:
        raise SystemExit(f"{value} (rounded {rv}) not found in extended grid {ext_list}")
    i = vals.index(rv)
    out = []
    if i - 1 >= 0:
        out.append(vals[i - 1])
    if i + 1 < len(vals):
        out.append(vals[i + 1])
    return out


def gen_core_fills(shape, max_pos, gross):
    """Raw (pre-dedup) fill candidates in a FIXED order: MaxPos-down,
    MaxPos-up, Gross-down, Gross-up. Each entry already respects its own
    axis's bound/adjacency rule; entries that fall outside are simply
    omitted (never invented/clamped to a boundary value)."""
    out = []
    lo, hi = CORE_MAXPOS_RANGE
    if max_pos - 2 >= lo:
        out.append((shape, max_pos - 2, _r2(gross)))
    if max_pos + 2 <= hi:
        out.append((shape, max_pos + 2, _r2(gross)))
    for g in _adjacent(gross, CORE_GROSS_EXT, 2):
        if _r2(g) < _r2(gross):
            out.append((shape, max_pos, _r2(g)))
    for g in _adjacent(gross, CORE_GROSS_EXT, 2):
        if _r2(g) > _r2(gross):
            out.append((shape, max_pos, _r2(g)))
    return out


def gen_apex_fills(n, frac):
    """Raw (pre-dedup) fill candidates in FIXED order: n-down, n-up,
    frac-down, frac-up. Band filter n*frac in [0.60,1.05] applied to EVERY
    candidate (both axes) before it is even offered -- 'STILL APPLIES to
    every cell' per the brief."""
    lo_n, hi_n = APEX_N_RANGE
    lo_g, hi_g = APEX_BAND
    out = []

    def _band_ok(nn, ff):
        g = nn * ff
        return (lo_g - 1e-9) <= g <= (hi_g + 1e-9)

    if n - 2 >= lo_n and _band_ok(n - 2, frac):
        out.append((n - 2, _r3(frac)))
    if n + 2 <= hi_n and _band_ok(n + 2, frac):
        out.append((n + 2, _r3(frac)))
    for f in _adjacent(frac, APEX_FRAC_EXT, 3):
        if _r3(f) < _r3(frac) and _band_ok(n, f):
            out.append((n, _r3(f)))
    for f in _adjacent(frac, APEX_FRAC_EXT, 3):
        if _r3(f) > _r3(frac) and _band_ok(n, f):
            out.append((n, _r3(f)))
    return out


def load_22now_worstdd(profile, out_dir=OUT_DIR):
    """cell tuple -> Phase A 22-now worst_dd, from out/allocA_*.csv (excludes
    smoke/paths files). Used ONLY for the cap tie-break ranking."""
    import glob
    out = {}
    for p in sorted(glob.glob(os.path.join(out_dir, 'allocA_*.csv'))):
        base = os.path.basename(p).lower()
        if 'smoke' in base or 'paths' in base:
            continue
        with open(p, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if row['profile'] != profile or row['window'] != '22-now':
                    continue
                if profile == 'core':
                    key = (row['shape_name'], int(row['max_pos']), _r2(row['gross']))
                else:
                    key = (int(row['max_pos']), _r3(row['flat_frac']))
                out[key] = float(row['worst_dd'])
    return out


def compute_final_cells(carried, gen_fn, dd_lookup, cap, fixed_extra=()):
    """Generic cap/dedup/truncate engine shared by Core and Apex.

    carried: ordered list of parent tuples (order = first-seen-wins dedup
      order AND the stable-sort tie-break order for the cap truncation).
    gen_fn: parent tuple -> ordered list of raw candidate tuples.
    dd_lookup: parent tuple -> Phase A 22-now worst_dd (must cover every
      carried parent -- fixed_extra cells are exempt, they never generate
      fills and are never ranked/dropped).
    fixed_extra: cells ALWAYS present, outside the neighbor-generation
      universe entirely (alloc's Core baseline S0/mp14/g0.50) -- counted
      toward `cap` but never a truncation candidate and never a dedup seed
      collision risk (verified disjoint by shape/axis construction).
    """
    if len(set(carried)) != len(carried):
        raise SystemExit(f"duplicate cell in carried list: {carried}")
    missing = [c for c in carried if c not in dd_lookup]
    if missing:
        raise SystemExit(f"carried cells missing 22-now Phase A worst_dd: {missing}")

    seen = set(carried)
    fills_by_parent = {}
    for parent in carried:
        new_for_parent = []
        for f in gen_fn(*parent):
            if f in seen:
                continue
            seen.add(f)
            new_for_parent.append(f)
        fills_by_parent[parent] = new_for_parent

    final_set = set(carried) | set(fixed_extra)
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

    final_sorted = sorted(final_set, key=lambda t: [str(x) for x in t])

    assert len(final_sorted) == min(n_before_cap, cap), (len(final_sorted), n_before_cap, cap)
    assert set(carried) <= final_set, "a carried cell was dropped -- only fills may be dropped"
    assert set(fixed_extra) <= final_set, "a fixed-extra (baseline) cell was dropped -- must never happen"
    assert len(final_sorted) == len(set(final_sorted)), "dedup failed -- duplicate in final set"

    diagnostics = dict(
        n_carried=len(carried), n_fixed_extra=len(fixed_extra),
        n_fill_candidates_post_dedup=sum(len(v) for v in fills_by_parent.values()),
        n_before_cap=n_before_cap, n_dropped=len(dropped), n_final=len(final_sorted),
        dropped=dropped, fills_by_parent=fills_by_parent,
    )
    return final_sorted, diagnostics


def _report(profile, carried, gen_fn, cap, fixed_extra=()):
    dd_lookup = load_22now_worstdd(profile)
    final, diag = compute_final_cells(carried, gen_fn, dd_lookup, cap, fixed_extra)
    print(f"\n=== {profile} ===")
    print(f"carried={diag['n_carried']}  fixed_extra={diag['n_fixed_extra']}  "
          f"post_dedup_fills={diag['n_fill_candidates_post_dedup']}  "
          f"before_cap={diag['n_before_cap']}  dropped={diag['n_dropped']}  final={diag['n_final']}")
    if diag['dropped']:
        print("dropped (parent -> fill), worst-parent-first order:")
        for parent, f in diag['dropped']:
            dd = dd_lookup[parent]
            print(f"   {parent} (22-now dd={dd:.3f}) -> {f}")
    print("fills kept, by parent (worst-to-best dd order):")
    for parent in sorted(carried, key=lambda p: dd_lookup[p], reverse=True):
        kept = [f for f in diag['fills_by_parent'][parent] if f in set(final)]
        print(f"   {parent} (dd={dd_lookup[parent]:.3f}): {kept}")
    print(f"FINAL {profile.upper()}_CELLS ({len(final)}):")
    print(final)
    return final, diag


if __name__ == '__main__':
    core_final, core_diag = _report('core', CORE_CARRIED, gen_core_fills, CORE_CAP,
                                     fixed_extra=[CORE_BASELINE])
    apex_final, apex_diag = _report('apex', APEX_CARRIED, gen_apex_fills, APEX_CAP,
                                     fixed_extra=[])

    assert CORE_BASELINE in core_final, "core baseline S0/mp14/g0.50 missing from final set"
    assert APEX_BASELINE in apex_final, "apex baseline n10/f0.10 missing from final set"
    for shape, mp, g in core_final:
        assert shape in ('S9', 'S8', 'S3', 'S0'), f"unexpected shape leaked in: {shape}"
        assert 8 <= mp <= 22, f"MaxPos out of clipped range: {mp}"
        assert g in CORE_GROSS_EXT, f"gross off the extended grid: {g}"
    for n, frac in apex_final:
        assert 4 <= n <= 16, f"n out of clipped range: {n}"
        g = n * frac
        assert 0.60 - 1e-9 <= g <= 1.05 + 1e-9, f"apex band filter violated: n={n} frac={frac} g={g}"
    print(f"\n[OK] core final={len(core_final)} (cap {CORE_CAP}), "
          f"apex final={len(apex_final)} (cap {APEX_CAP}); baselines present; all bounds/bands verified.")
