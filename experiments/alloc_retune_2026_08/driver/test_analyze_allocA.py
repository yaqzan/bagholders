#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_analyze_allocA.py -- unit tests for analyze_allocA.py on tiny SYNTHETIC
CSV rows (never real Phase A output -- the BUILD spec forbids this session
from analyzing real results). Pure Python, no DB, no monte_carlo import, no
queue -- runs in well under a second.

Row construction reuses allocA_run.CoreCell/ApexCell (.row_fields()/.cell_name)
so the synthetic CSV schema can never drift from what the real driver emits.

Run directly: python experiments/alloc_retune_2026_08/driver/test_analyze_allocA.py
Or via pytest: pytest experiments/alloc_retune_2026_08/driver/test_analyze_allocA.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import analyze_allocA as aa           # noqa: E402
from allocA_run import CoreCell, ApexCell, CORE_SHAPES  # noqa: E402  (module-level defs only, no monte_carlo import)

CANON_WINDOWS = aa.CANON_WINDOWS


def mkrows(cell, dd_map, med_map=None, p_coll_map=None, windows=CANON_WINDOWS):
    """One CSV-row-shaped dict per window in `windows`, using cell.row_fields()
    for the descriptor columns so the schema always matches allocA_run.py."""
    med_map = med_map or {}
    p_coll_map = p_coll_map or {}
    rows = []
    for w in windows:
        row = {
            'phase': 'A', 'profile': ('apex' if isinstance(cell, ApexCell) else 'core'),
            'window': w,
            **cell.row_fields(),
            'n_iter': 150, 'n_call_signals': 10,
            'mean_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'med_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'p10_ret': 0.0, 'p90_ret': 0.0,
            'worst_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'mean_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'p_coll': p_coll_map.get(w, p_coll_map.get('_default', 0.0)),
            'tp_rate': 50.0, 'sl_rate': 10.0, 'hard_rate': 40.0, 'both_rate': 0.0,
            'avg_open_premium_base_pct': 30.0, 'max_open_premium_base_pct': 45.0,
            'call_trades': 20.0, 'put_trades': 0.0,
            'elapsed_prepare_s': 0.0, 'elapsed_sim_s': 1.0,
        }
        rows.append(row)
    return rows


def core_cell(shape, max_pos, gross):
    ultra, top, mid, low = CORE_SHAPES[shape]
    is_baseline = (shape, max_pos, round(gross, 2)) == aa.CORE_BASELINE
    return CoreCell(shape, ultra, top, mid, low, max_pos, gross, is_baseline)


def apex_cell(n, frac):
    return ApexCell(n, frac, (n, frac) == aa.APEX_BASELINE)


def build_core_rows():
    rows = []
    # BASELINE S0/mp14/g0.50
    rows += mkrows(core_cell('S0', 14, 0.50),
                    dd_map={'2022': 50.0, '2024': 38.0, '22-now': 55.0, '2020_crash': 70.0},
                    med_map={'22-now': 600.0})
    # PARETO winner: dominates baseline on BOTH axes (lower dd, higher med) --
    # not a grid-neighbor of baseline (shape S2 is 2 steps from S0), so this
    # ONLY enters via the Pareto set, exercising that path independently of
    # baseline_group.
    rows += mkrows(core_cell('S2', 14, 0.50),
                    dd_map={'2022': 45.0, '2024': 34.0, '22-now': 48.0, '2020_crash': 66.0},
                    med_map={'22-now': 900.0})
    # DOMINATED survivor: worse dd AND worse med than the S2 winner -- stays a
    # survivor (p_coll=0) but must be excluded from the Pareto set.
    rows += mkrows(core_cell('S3', 12, 0.65),
                    dd_map={'2022': 60.0, '2024': 45.0, '22-now': 65.0, '2020_crash': 80.0},
                    med_map={'22-now': 200.0})
    # NEIGHBOR (shape axis, S0->S1 at base mp/gross): deliberately BAD on both
    # metrics so it would not naturally qualify for Pareto or top-12 -- must
    # still be CARRIED via baseline_group (exercises core_neighbor_names).
    rows += mkrows(core_cell('S1', 14, 0.50),
                    dd_map={'2022': 90.0, '2024': 80.0, '22-now': 95.0, '2020_crash': 99.0},
                    med_map={'22-now': 10.0})
    # DROPPED: collapse on 2020_crash (p_coll>0) -- must be excluded from
    # survivors entirely regardless of how good its other windows look.
    rows += mkrows(core_cell('S8', 10, 0.40),
                    dd_map={'2022': 30.0, '2024': 20.0, '22-now': 35.0, '2020_crash': 98.0},
                    med_map={'22-now': 5000.0},
                    p_coll_map={'2020_crash': 8.0})
    # INCOMPLETE: missing '2024' entirely (job not finished) -- must be
    # excluded from now_metrics/carried by virtue of having a '22-now' row
    # (it still does here), but IS exercised for the "missing window in
    # display table" branch of write_table via a genuinely absent '2024' key.
    rows += mkrows(core_cell('S9', 16, 0.80),
                    dd_map={'2022': 55.0, '22-now': 58.0, '2020_crash': 72.0},
                    med_map={'22-now': 300.0},
                    windows=[w for w in CANON_WINDOWS if w != '2024'])
    return rows


def build_apex_rows():
    rows = []
    # BASELINE n10/f0.10 (gross=1.0) -- constant worst-of-4 DD=65, so a
    # strictly-better cell on both DD and 22-now med will dominate it.
    rows += mkrows(apex_cell(10, 0.10), dd_map={'_default': 65.0}, med_map={'22-now': 100.0})
    # Genuine tradeoff vs baseline (better DD, worse growth) -- stays Pareto.
    rows += mkrows(apex_cell(6, 0.15), dd_map={'_default': 55.0}, med_map={'22-now': 80.0})
    # Dominates baseline (better DD AND better growth) -- excludes baseline
    # from the Pareto set.
    rows += mkrows(apex_cell(8, 0.10), dd_map={'_default': 60.0}, med_map={'22-now': 110.0})
    # Dominated by the n8/f0.10 cell above -- excluded from Pareto.
    rows += mkrows(apex_cell(8, 0.08), dd_map={'_default': 62.0}, med_map={'22-now': 90.0})
    # Neighbor of baseline on the frac axis (f0.10 -> f0.08 at n10) -- bad on
    # both metrics, must still be carried via baseline_group.
    rows += mkrows(apex_cell(10, 0.08), dd_map={'_default': 99.0}, med_map={'22-now': 1.0})
    return rows


def _cells(profile, rows):
    all_cells = aa.merge_cells(rows)
    return {k[1]: v for k, v in all_cells.items() if k[0] == profile}


def test_core_neighbor_names_boundary_and_missing():
    """S0 sits at shape-grid index 0 -> only ONE shape neighbor (S1), never a
    fabricated 'S-1'. A candidate neighbor absent from the survivor set (here:
    the gross-axis neighbors 0.40/0.65 were never simulated) must be silently
    dropped, not invented."""
    existing = {aa.core_cell_name('S0', 14, 0.50), aa.core_cell_name('S1', 14, 0.50),
                aa.core_cell_name('S0', 12, 0.50)}
    got = aa.core_neighbor_names(existing)
    assert got == existing, got   # S0@16, S0@14@0.40, S0@14@0.65 all absent -> dropped
    print("[OK] test_core_neighbor_names_boundary_and_missing")


def test_apex_neighbor_names_filtered_grid():
    """Baseline n10/f0.10's n-axis neighbor n12/f0.10 (gross=1.20) and frac-axis
    neighbor n10/f0.125 (gross=1.25) are OUTSIDE the [0.60,1.05] filter and so
    never exist as real cells -- must not be invented even though they are
    mathematically valid grid points."""
    existing = {aa.apex_cell_name(10, 0.10), aa.apex_cell_name(8, 0.10), aa.apex_cell_name(10, 0.08)}
    got = aa.apex_neighbor_names(existing)
    assert got == existing, got
    print("[OK] test_apex_neighbor_names_filtered_grid")


def test_p_coll_drop():
    rows = build_core_rows()
    cells = _cells('core', rows)
    res = aa.analyze_profile('core', cells)
    dropped_names = {n for n, _ in res['dropped']}
    assert dropped_names == {aa.core_cell_name('S8', 10, 0.40)}, dropped_names
    assert aa.core_cell_name('S8', 10, 0.40) not in res['survivors']
    print("[OK] test_p_coll_drop")


def test_core_pareto_and_carried():
    rows = build_core_rows()
    cells = _cells('core', rows)
    res = aa.analyze_profile('core', cells)

    base = aa.core_cell_name('S0', 14, 0.50)
    winner = aa.core_cell_name('S2', 14, 0.50)
    dominated = aa.core_cell_name('S3', 12, 0.65)
    neighbor = aa.core_cell_name('S1', 14, 0.50)
    incomplete = aa.core_cell_name('S9', 16, 0.80)

    # 5 survivors (S8 dropped for p_coll>0); S9 counts as a survivor (p_coll=0
    # on its present windows) even though it's missing a '2024' row.
    assert set(res['survivors']) == {base, winner, dominated, neighbor, incomplete}, res['survivors'].keys()

    # Pareto: winner dominates baseline AND dominated -> only winner (+ any
    # other non-dominated point) remains. neighbor is dd/med-terrible -> not
    # Pareto. incomplete's numbers don't dominate winner -> not Pareto either.
    assert winner in res['pareto_set']
    assert base not in res['pareto_set']         # dominated by winner
    assert dominated not in res['pareto_set']     # dominated by winner
    assert neighbor not in res['pareto_set']      # worst on both metrics

    # baseline_group must include baseline + its present S1 shape-neighbor,
    # NOT the absent mp/gross-axis neighbors (never simulated here).
    assert res['baseline_group'] == {base, neighbor}, res['baseline_group']

    # CARRIED = Pareto U top12 U baseline_group, all within now_metrics.
    assert base in res['carried']       # via baseline_group
    assert neighbor in res['carried']   # via baseline_group (the whole point of this test)
    assert winner in res['carried']     # via Pareto (and top12)
    print("[OK] test_core_pareto_and_carried")


def test_core_no_survivors_is_graceful():
    """STOP-RULE shape: an all-collapsed profile must yield empty carried/
    pareto sets, not a crash (mirrors PREREG's 'no cell enters a lane' case)."""
    res = aa.analyze_profile('core', {})
    assert res['carried'] == set() and res['pareto_set'] == set() and res['survivors'] == {}
    print("[OK] test_core_no_survivors_is_graceful")


def test_apex_worst_of_4_pareto():
    rows = build_apex_rows()
    cells = _cells('apex', rows)
    res = aa.analyze_profile('apex', cells)

    base = aa.apex_cell_name(10, 0.10)
    tradeoff = aa.apex_cell_name(6, 0.15)
    dominator = aa.apex_cell_name(8, 0.10)
    dominated = aa.apex_cell_name(8, 0.08)
    neighbor = aa.apex_cell_name(10, 0.08)

    assert set(res['survivors']) == {base, tradeoff, dominator, dominated, neighbor}

    # dominator (60,110) beats base (65,100) and dominated (62,90) on BOTH
    # axes (worst-of-4 DD here == the constant '_default' DD since all 4
    # canonical windows share the same value) -> both excluded from Pareto.
    assert dominator in res['pareto_set']
    assert base not in res['pareto_set']
    assert dominated not in res['pareto_set']
    # tradeoff (55,80) vs dominator (60,110): neither dominates -> both Pareto.
    assert tradeoff in res['pareto_set']

    assert neighbor in res['baseline_group'] and base in res['baseline_group']
    assert neighbor in res['carried']   # carried via baseline_group despite being terrible
    print("[OK] test_apex_worst_of_4_pareto")


def test_end_to_end_md_report():
    """Full main() pipeline against a temp CSV, with OUT_DIR monkeypatched so
    nothing touches the real experiments/alloc_retune_2026_08/out/ directory.
    Checks: runs without error, output file exists, both profiles appear,
    baseline + neighbor + dropped cells are all visible in the text."""
    rows = build_core_rows() + build_apex_rows()
    fieldnames = list(rows[0].keys())

    orig_out_dir = aa.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            aa.OUT_DIR = tmp
            csv_path = os.path.join(tmp, 'allocA_synthetic_test.csv')
            import csv as _csv
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                w = _csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)

            out_path = os.path.join(tmp, 'allocA_summary_TEST.md')
            sys.argv = ['analyze_allocA.py', '--glob', 'allocA_synthetic_test.csv', '--out', out_path]
            aa.main()

            assert os.path.exists(out_path)
            with open(out_path, encoding='utf-8') as f:
                text = f.read()
            assert '## Profile: core' in text
            assert '## Profile: apex' in text
            assert 'BASELINE' in text
            assert 'S2 (' in text   # the core Pareto winner's descriptor rendered
            print(f"[OK] test_end_to_end_md_report ({text.count(chr(10)) + 1} lines)")
        finally:
            aa.OUT_DIR = orig_out_dir


if __name__ == '__main__':
    test_core_neighbor_names_boundary_and_missing()
    test_apex_neighbor_names_filtered_grid()
    test_p_coll_drop()
    test_core_pareto_and_carried()
    test_core_no_survivors_is_graceful()
    test_apex_worst_of_4_pareto()
    test_end_to_end_md_report()
    print("\n[ALL PASSED]")
