#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_analyze_allocB.py -- unit tests for analyze_allocB.py on tiny SYNTHETIC
CSV rows (never real Phase B output -- the BUILD spec forbids this session
from analyzing real results). Pure Python, no DB, no monte_carlo import, no
queue -- runs in well under a second. Mirrors
experiments/tpsl_refine_2026_08/driver/test_analyze_phaseB.py's scenario
design (same dd/med numbers, same expected lane outcomes) with alloc's
Core/Apex cell identities substituted for tpsl's (tp,sl) pairs -- proves the
lane math itself is an unmodified port while the cell-key generalization
(cell_name string vs raw tuple) is exercised end-to-end.

Row construction reuses allocA_run.CoreCell/ApexCell (.row_fields()/
.cell_name) so the synthetic CSV schema can never drift from what
allocB_run.py actually emits (same reuse pattern test_analyze_allocA.py
already uses for Phase A).

Run directly: python experiments/alloc_retune_2026_08/driver/test_analyze_allocB.py
Or via pytest: pytest experiments/alloc_retune_2026_08/driver/test_analyze_allocB.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import analyze_allocB as ab                       # noqa: E402
from allocA_run import CoreCell, ApexCell, CORE_SHAPES   # noqa: E402  (module-level defs only, no monte_carlo import)
from allocB_run import PHASE_B_WINDOWS             # noqa: E402  (module-level defs only, no monte_carlo import)


def core_cell(shape, max_pos, gross, is_baseline=False):
    ultra, top, mid, low = CORE_SHAPES[shape]
    return CoreCell(shape, ultra, top, mid, low, max_pos, gross, is_baseline)


def apex_cell(n, frac, is_baseline=False):
    return ApexCell(n, frac, is_baseline)


def mkrows(cell, dd_map, med_map=None, p_coll_map=None, windows=PHASE_B_WINDOWS):
    """One CSV-row-shaped dict per window in `windows`, using cell.row_fields()
    for the descriptor columns so the schema always matches allocB_run.py.
    dd_map/med_map/p_coll_map: window -> value, with an optional '_default'
    key for unlisted windows (worst_dd/med_ret default 0.0, p_coll 0.0)."""
    med_map = med_map or {}
    p_coll_map = p_coll_map or {}
    rows = []
    for w in windows:
        row = {
            'phase': 'B', 'profile': ('apex' if isinstance(cell, ApexCell) else 'core'),
            'window': w,
            **cell.row_fields(),
            'n_iter': 300, 'n_call_signals': 10,
            'mean_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'med_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'p10_ret': 0.0, 'p90_ret': 0.0,
            'worst_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'mean_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'p_coll': p_coll_map.get(w, p_coll_map.get('_default', 0.0)),
            'tp_rate': 50.0, 'sl_rate': 30.0, 'hard_rate': 20.0, 'both_rate': 0.0,
            'avg_open_premium_base_pct': 30.0, 'max_open_premium_base_pct': 45.0,
            'call_trades': 20.0, 'put_trades': 0.0,
            'elapsed_prepare_s': 0.1, 'elapsed_sim_s': 1.0,
        }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# CORE scenario -- same dd/med numbers and expected lane outcomes as
# tpsl's test_analyze_phaseB.build_core_rows(), alloc cell identities
# substituted for (tp,sl) pairs (several are REAL Phase B cells, for
# authenticity -- the analyzer does not require locked-grid membership to
# evaluate lanes, only the accounting section cross-checks it).
# ---------------------------------------------------------------------------
BASE = ('S0', 14, 0.50)     # real in-phase baseline
A_ID = ('S9', 10, 0.40)     # real carried cell
B_ID = ('S3', 12, 0.40)     # real carried cell
C_ID = ('S8', 14, 0.50)     # real carried cell
D_ID = ('S8', 16, 0.50)     # real carried cell
E_ID = ('S8', 20, 0.65)     # real neighbor cell
F_ID = ('S9', 20, 0.50)     # real carried cell
G_ID = ('S9', 8, 0.40)      # real neighbor cell (new, mp-floor extension)
H_ID = ('S9', 18, 0.30)     # real neighbor cell (new, gross-floor extension)


def build_core_rows():
    rows = []
    # BASELINE
    rows += mkrows(core_cell(*BASE, is_baseline=True),
                    dd_map={'5y': 60.0, '22-now': 55.0, '2021': 40.0, '2022': 50.0,
                             '2023': 42.0, '2024': 38.0, '2025': 41.0, 'dip': 30.0, '2020_crash': 70.0},
                    med_map={'5y': 500.0})
    # A: LANE-DD entrant only (dd_5y=58.0<=58.5, dd_22now=55.3<=55.5, med_5y=400>=375)
    rows += mkrows(core_cell(*A_ID),
                    dd_map={'5y': 58.0, '22-now': 55.3, '2021': 39.0, '2022': 49.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 68.0},
                    med_map={'5y': 400.0})
    # B: LANE-GROWTH entrant only (med_5y=900>=750, dd_5y=60.3<=60.5, dd_22now=55.9<=56.0);
    # deliberately ranks WORSE on dd_5y than A/C/D so it is NOT naturally in the
    # dd_5y top-3 -- exercises the "growth entrant takes slot 3" displacement.
    rows += mkrows(core_cell(*B_ID),
                    dd_map={'5y': 60.3, '22-now': 55.9, '2021': 43.0, '2022': 54.0,
                             '2023': 46.0, '2024': 42.0, '2025': 45.0, 'dip': 31.0, '2020_crash': 71.0},
                    med_map={'5y': 900.0})
    # C: LANE-DD entrant, best dd_5y of the set
    rows += mkrows(core_cell(*C_ID),
                    dd_map={'5y': 50.0, '22-now': 53.0, '2021': 38.0, '2022': 47.0,
                             '2023': 40.0, '2024': 36.0, '2025': 39.0, 'dip': 28.0, '2020_crash': 66.0},
                    med_map={'5y': 450.0})
    # D: LANE-DD entrant, 2nd best dd_5y
    rows += mkrows(core_cell(*D_ID),
                    dd_map={'5y': 52.0, '22-now': 54.0, '2021': 39.0, '2022': 48.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 67.0},
                    med_map={'5y': 420.0})
    # E: survivor, non-entrant (fails both lanes outright)
    rows += mkrows(core_cell(*E_ID),
                    dd_map={'5y': 65.0, '22-now': 58.0, '2021': 40.0, '2022': 50.0,
                             '2023': 42.0, '2024': 38.0, '2025': 41.0, 'dip': 30.0, '2020_crash': 70.0},
                    med_map={'5y': 200.0})
    # F: would qualify for LANE-DD on dd_5y/dd_22now/med_5y alone, but 2023
    # regresses worst_dd by +6pp vs baseline (>5pp limit) -- must be excluded.
    rows += mkrows(core_cell(*F_ID),
                    dd_map={'5y': 57.0, '22-now': 55.2, '2021': 39.0, '2022': 49.0,
                             '2023': 48.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 68.0},
                    med_map={'5y': 380.0})
    # G: DROPPED (p_coll>0 on 2020_crash)
    rows += mkrows(core_cell(*G_ID),
                    dd_map={'5y': 55.0, '22-now': 54.0, '2021': 39.0, '2022': 48.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 90.0},
                    med_map={'5y': 300.0},
                    p_coll_map={'2020_crash': 5.0})
    # H: INCOMPLETE (missing '5y' and 'dip' rows entirely -- job not finished)
    rows += mkrows(core_cell(*H_ID),
                    dd_map={'22-now': 54.0, '2021': 39.0, '2022': 48.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, '2020_crash': 67.0},
                    med_map={},
                    windows=[w for w in PHASE_B_WINDOWS if w not in ('5y', 'dip')])
    return rows


# ---------------------------------------------------------------------------
# APEX scenario -- same numbers/outcomes as tpsl's build_apex_rows(), real
# Phase B apex cell identities.
# ---------------------------------------------------------------------------
ABASE_ID = (10, 0.100)   # real baseline
AP1_ID = (8, 0.125)      # real carried cell
AP2_ID = (6, 0.100)      # real carried cell
AP3_ID = (8, 0.080)      # real carried cell
AQ_ID = (12, 0.060)      # real neighbor cell (new)
AR_ID = (12, 0.080)      # real carried cell


def build_apex_rows():
    rows = []
    # BASELINE: worst9=65 (constant), med_22now=100 -- will be DOMINATED by
    # P2 (60,110) -> excluded from Pareto set, but must still ALWAYS be reported.
    rows += mkrows(apex_cell(*ABASE_ID, is_baseline=True), dd_map={'_default': 65.0}, med_map={'22-now': 100.0})
    # P1: better DD, worse growth than baseline -- genuine tradeoff, stays Pareto.
    rows += mkrows(apex_cell(*AP1_ID), dd_map={'_default': 55.0}, med_map={'22-now': 80.0})
    # P2: better DD AND better growth than baseline -- dominates baseline.
    rows += mkrows(apex_cell(*AP2_ID), dd_map={'_default': 60.0}, med_map={'22-now': 110.0})
    # P3: dominated BY P2 (60<=62 and 110>=90) -- excluded from Pareto, not by baseline.
    rows += mkrows(apex_cell(*AP3_ID), dd_map={'_default': 62.0}, med_map={'22-now': 90.0})
    # Q: DROPPED (p_coll>0 on one window)
    rows += mkrows(apex_cell(*AQ_ID), dd_map={'_default': 58.0}, med_map={'22-now': 95.0},
                    p_coll_map={'22-now': 2.0})
    # R: INCOMPLETE (missing one window)
    rows += mkrows(apex_cell(*AR_ID), dd_map={'_default': 59.0}, med_map={'22-now': 97.0},
                    windows=[w for w in PHASE_B_WINDOWS if w != 'dip'])
    return rows


def _cells(profile, rows):
    all_cells = ab.merge_cells(rows)
    return {k: v for k, v in all_cells.items() if k[0] == profile}


def _name(shape_or_n, mp_or_frac=None, gross=None):
    """cell_name for a (shape,mp,gross) or (n,frac) tuple, matching
    CoreCell/ApexCell.cell_name exactly."""
    if isinstance(shape_or_n, str):
        return f"{shape_or_n}_mp{mp_or_frac}_g{gross:.2f}"
    return f"n{shape_or_n}_f{mp_or_frac:.3f}"


def test_classify_cell():
    rows = build_core_rows()
    cells = _cells('core', rows)
    assert ab.classify_cell(cells[('core', _name(*BASE))])[0] == 'survivor'
    assert ab.classify_cell(cells[('core', _name(*G_ID))])[0] == 'dropped'
    assert ab.classify_cell(cells[('core', _name(*H_ID))])[0] == 'incomplete'
    print("[OK] test_classify_cell")


def test_annual_regression_gate():
    rows = build_core_rows()
    cells = _cells('core', rows)
    baseline = cells[('core', _name(*BASE))]
    assert ab.annual_regression_ok(cells[('core', _name(*A_ID))], baseline) is True    # A
    assert ab.annual_regression_ok(cells[('core', _name(*F_ID))], baseline) is False   # F (2023 +6pp)
    print("[OK] test_annual_regression_gate")


def test_find_baseline_key():
    rows = build_core_rows()
    cells = _cells('core', rows)
    assert ab.find_baseline_key(cells) == ('core', _name(*BASE))
    print("[OK] test_find_baseline_key")


def test_core_lanes_and_finalists():
    rows = build_core_rows()
    cells = _cells('core', rows)
    survivors = {k: v for k, v in cells.items() if ab.classify_cell(v)[0] == 'survivor'}
    # G (dropped) and H (incomplete) must be excluded; the other 7 remain.
    assert set(k[1] for k in survivors) == {
        _name(*BASE), _name(*A_ID), _name(*B_ID), _name(*C_ID),
        _name(*D_ID), _name(*E_ID), _name(*F_ID),
    }, survivors.keys()

    baseline = survivors[('core', _name(*BASE))]
    dd_entrants, growth_entrants, dd5y = ab.core_lane_membership(survivors, baseline)

    A, B, C, D, E, F = [('core', _name(*t)) for t in (A_ID, B_ID, C_ID, D_ID, E_ID, F_ID)]

    assert dd_entrants == {A, C, D}, dd_entrants
    assert growth_entrants == {B}, growth_entrants
    assert E not in dd_entrants and E not in growth_entrants   # non-entrant
    assert F not in dd_entrants and F not in growth_entrants   # regression-blocked

    # dd_5y ranking: C(50.0) < D(52.0) < A(58.0) < B(60.3). Natural top-3 by
    # dd_5y = [C, D, A] -- none are GROWTH, so B (the only GROWTH entrant)
    # must displace slot 3 (index 2). THIS is the growth-lane-displacement case.
    finalists = ab.core_finalists(dd_entrants, growth_entrants, dd5y)
    assert finalists == [C, D, B], finalists
    print("[OK] test_core_lanes_and_finalists (growth-lane displacement verified)")


def test_core_growth_entrant_already_in_top3_no_displacement():
    """Counter-case: when a GROWTH entrant's dd_5y is ALREADY good enough to
    land naturally in the top-3, no displacement should occur (slot 3 stays
    whatever plain dd_5y ranking gives)."""
    dd5y = {'X': 10.0, 'Y': 20.0, 'Z': 30.0, 'W': 40.0}
    dd_entrants = {'X', 'Z', 'W'}
    growth_entrants = {'Y'}   # Y is rank #2 by dd_5y -- already inside the natural top-3
    finalists = ab.core_finalists(dd_entrants | growth_entrants, growth_entrants, dd5y)
    assert finalists == ['X', 'Y', 'Z'], finalists   # no displacement -- Z (plain 3rd) stays
    print("[OK] test_core_growth_entrant_already_in_top3_no_displacement")


def test_core_no_entrants_is_graceful():
    """STOP-RULE shape: if nothing clears either lane, finalists must be []
    (not a crash), matching PREREG's 'no cell enters a lane' outcome."""
    dd_entrants, growth_entrants, dd5y = set(), set(), {}
    assert ab.core_finalists(dd_entrants, growth_entrants, dd5y) == []
    print("[OK] test_core_no_entrants_is_graceful")


def test_apex_pareto_and_finalists():
    rows = build_apex_rows()
    cells = _cells('apex', rows)
    survivors = {k: v for k, v in cells.items() if ab.classify_cell(v)[0] == 'survivor'}
    assert set(k[1] for k in survivors) == {
        _name(*ABASE_ID), _name(*AP1_ID), _name(*AP2_ID), _name(*AP3_ID),
    }, survivors.keys()   # Q (dropped) and R (incomplete) excluded

    baseline = survivors[('apex', _name(*ABASE_ID))]
    pareto, worst9 = ab.apex_pareto(survivors)

    BASE_K, P1, P2, P3 = [('apex', _name(*t)) for t in (ABASE_ID, AP1_ID, AP2_ID, AP3_ID)]

    assert worst9[BASE_K] == 65.0 and worst9[P1] == 55.0 and worst9[P2] == 60.0 and worst9[P3] == 62.0
    # P2 dominates BASE (60<=65, 110>=100) and P3 (60<=62, 110>=90) -> both excluded.
    # P1 vs P2 is a genuine tradeoff (neither dominates) -> both stay.
    assert pareto == {P1, P2}, pareto

    finalists = ab.apex_finalists(pareto, worst9)
    assert finalists == [P1, P2], finalists   # ranked by worst9 ascending, NOT incl P3 despite P3 < BASE
    assert BASE_K not in finalists   # dominated -> excluded from finalists...
    assert BASE_K in survivors       # ...but still reachable for the "always reported" row
    print("[OK] test_apex_pareto_and_finalists")


def test_end_to_end_md_report():
    """Full main() pipeline against a temp CSV, with OUT_DIR monkeypatched so
    nothing touches the real experiments/alloc_retune_2026_08/out/ directory.
    Checks: runs without error, output file exists, stays under the 180-line
    budget, and both profiles + both finalist sets appear."""
    rows = build_core_rows() + build_apex_rows()
    fieldnames = list(rows[0].keys())

    orig_out_dir = ab.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ab.OUT_DIR = tmp
            csv_path = os.path.join(tmp, 'allocB_synthetic_test.csv')
            import csv as _csv
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                w = _csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)

            out_path = os.path.join(tmp, 'allocB_summary_TEST.md')
            sys.argv = ['analyze_allocB.py', '--glob', 'allocB_synthetic_test.csv', '--out', out_path]
            ab.main()

            assert os.path.exists(out_path)
            with open(out_path, encoding='utf-8') as f:
                text = f.read()
            n_lines = text.count('\n') + 1
            assert n_lines <= 180, f"{n_lines} lines -- over budget"
            assert '## Profile: core' in text
            assert '## Profile: apex' in text
            assert 'S3 (' in text         # B (growth finalist)'s shape descriptor rendered somewhere
            assert 'BASELINE' in text     # apex baseline-always-reported path fired
            print(f"[OK] test_end_to_end_md_report ({n_lines} lines)")
        finally:
            ab.OUT_DIR = orig_out_dir


if __name__ == '__main__':
    test_classify_cell()
    test_annual_regression_gate()
    test_find_baseline_key()
    test_core_lanes_and_finalists()
    test_core_growth_entrant_already_in_top3_no_displacement()
    test_core_no_entrants_is_graceful()
    test_apex_pareto_and_finalists()
    test_end_to_end_md_report()
    print("\n[ALL PASSED]")
