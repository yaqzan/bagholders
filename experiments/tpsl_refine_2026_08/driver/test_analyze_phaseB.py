#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_analyze_phaseB.py -- unit tests for analyze_phaseB.py on a tiny
SYNTHETIC CSV (never real Phase B output -- this session must not run the
analyzer on real data, per the Phase B build spec). Pure Python, no DB, no
monte_carlo import, no queue -- runs in well under a second.

Run directly: python experiments/tpsl_refine_2026_08/driver/test_analyze_phaseB.py
Or via pytest: pytest experiments/tpsl_refine_2026_08/driver/test_analyze_phaseB.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import analyze_phaseB as ab  # noqa: E402
from phaseB_run import PHASE_B_WINDOWS  # noqa: E402

BASE_TP, BASE_SL = 0.30, -0.70


def mkrows(profile, tp, sl, dd_map, med_map=None, p_coll_map=None, windows=PHASE_B_WINDOWS):
    """One CSV row per window in `windows`. dd_map/med_map/p_coll_map: window
    -> value, with a '_default' key for unlisted windows (worst_dd defaults
    0.0, med_ret 0.0, p_coll 0.0) -- most test cells only care about a few
    windows' real values."""
    med_map = med_map or {}
    p_coll_map = p_coll_map or {}
    rows = []
    for w in windows:
        rows.append({
            'phase': 'B', 'profile': profile, 'window': w, 'tp': tp, 'sl': sl,
            'n_iter': 300, 'n_call_signals': 10,
            'mean_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'med_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'p10_ret': 0.0, 'p90_ret': 0.0,
            'worst_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'mean_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'p_coll': p_coll_map.get(w, p_coll_map.get('_default', 0.0)),
            'tp_rate': 50.0, 'sl_rate': 30.0, 'hard_rate': 20.0, 'both_rate': 0.0,
            'elapsed_prepare_s': 0.1, 'elapsed_sim_s': 1.0,
            'realized_call_tp_pct': 45.0,
        })
    return rows


def build_core_rows():
    rows = []
    # BASELINE (0.30,-0.70)
    rows += mkrows('core', BASE_TP, BASE_SL,
                    dd_map={'5y': 60.0, '22-now': 55.0, '2021': 40.0, '2022': 50.0,
                             '2023': 42.0, '2024': 38.0, '2025': 41.0, 'dip': 30.0, '2020_crash': 70.0},
                    med_map={'5y': 500.0})
    # A: LANE-DD entrant only (dd_5y=58.0<=58.5, dd_22now=55.3<=55.5, med_5y=400>=375)
    rows += mkrows('core', 0.20, -0.70,
                    dd_map={'5y': 58.0, '22-now': 55.3, '2021': 39.0, '2022': 49.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 68.0},
                    med_map={'5y': 400.0})
    # B: LANE-GROWTH entrant only (med_5y=900>=750, dd_5y=60.3<=60.5, dd_22now=55.9<=56.0);
    # deliberately ranks WORSE on dd_5y than A/C/D so it is NOT naturally in the
    # dd_5y top-3 -- exercises the "growth entrant takes slot 3" displacement.
    rows += mkrows('core', 0.15, -0.60,
                    dd_map={'5y': 60.3, '22-now': 55.9, '2021': 43.0, '2022': 54.0,
                             '2023': 46.0, '2024': 42.0, '2025': 45.0, 'dip': 31.0, '2020_crash': 71.0},
                    med_map={'5y': 900.0})
    # C: LANE-DD entrant, best dd_5y of the set
    rows += mkrows('core', 0.15, -0.90,
                    dd_map={'5y': 50.0, '22-now': 53.0, '2021': 38.0, '2022': 47.0,
                             '2023': 40.0, '2024': 36.0, '2025': 39.0, 'dip': 28.0, '2020_crash': 66.0},
                    med_map={'5y': 450.0})
    # D: LANE-DD entrant, 2nd best dd_5y
    rows += mkrows('core', 0.20, -0.90,
                    dd_map={'5y': 52.0, '22-now': 54.0, '2021': 39.0, '2022': 48.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 67.0},
                    med_map={'5y': 420.0})
    # E: survivor, non-entrant (fails both lanes outright)
    rows += mkrows('core', 0.30, -0.60,
                    dd_map={'5y': 65.0, '22-now': 58.0, '2021': 40.0, '2022': 50.0,
                             '2023': 42.0, '2024': 38.0, '2025': 41.0, 'dip': 30.0, '2020_crash': 70.0},
                    med_map={'5y': 200.0})
    # F: would qualify for LANE-DD on dd_5y/dd_22now/med_5y alone, but 2023
    # regresses worst_dd by +6pp vs baseline (>5pp limit) -- must be excluded.
    rows += mkrows('core', 0.25, -0.70,
                    dd_map={'5y': 57.0, '22-now': 55.2, '2021': 39.0, '2022': 49.0,
                             '2023': 48.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 68.0},
                    med_map={'5y': 380.0})
    # G: DROPPED (p_coll>0 on 2020_crash)
    rows += mkrows('core', 0.10, -0.60,
                    dd_map={'5y': 55.0, '22-now': 54.0, '2021': 39.0, '2022': 48.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, 'dip': 29.0, '2020_crash': 90.0},
                    med_map={'5y': 300.0},
                    p_coll_map={'2020_crash': 5.0})
    # H: INCOMPLETE (missing '5y' and 'dip' rows entirely -- job not finished)
    rows += mkrows('core', 0.10, -0.50,
                    dd_map={'22-now': 54.0, '2021': 39.0, '2022': 48.0,
                             '2023': 41.0, '2024': 37.0, '2025': 40.0, '2020_crash': 67.0},
                    med_map={},
                    windows=[w for w in PHASE_B_WINDOWS if w not in ('5y', 'dip')])
    return rows


def build_apex_rows():
    rows = []
    # BASELINE: worst9=65 (constant across windows), med_22now=100 -- will be
    # DOMINATED by P2 (60,110) -> excluded from Pareto set, but must still
    # ALWAYS be reported.
    rows += mkrows('apex', BASE_TP, BASE_SL, dd_map={'_default': 65.0}, med_map={'22-now': 100.0})
    # P1: better DD, worse growth than baseline -- genuine tradeoff, stays Pareto.
    rows += mkrows('apex', 0.15, -0.90, dd_map={'_default': 55.0}, med_map={'22-now': 80.0})
    # P2: better DD AND better growth than baseline -- dominates baseline.
    rows += mkrows('apex', 0.20, -0.60, dd_map={'_default': 60.0}, med_map={'22-now': 110.0})
    # P3: dominated BY P2 (60<=62 and 110>=90) -- excluded from Pareto, not by baseline.
    rows += mkrows('apex', 0.10, -0.50, dd_map={'_default': 62.0}, med_map={'22-now': 90.0})
    # Q: DROPPED (p_coll>0 on one window)
    rows += mkrows('apex', 0.25, -0.50, dd_map={'_default': 58.0}, med_map={'22-now': 95.0},
                    p_coll_map={'22-now': 2.0})
    # R: INCOMPLETE (missing one window)
    rows += mkrows('apex', 0.20, -0.40, dd_map={'_default': 59.0}, med_map={'22-now': 97.0},
                    windows=[w for w in PHASE_B_WINDOWS if w != 'dip'])
    return rows


def _cells(profile, rows):
    all_cells = ab.merge_cells(rows)
    return {k: v for k, v in all_cells.items() if k[0] == profile}


def test_classify_cell():
    rows = build_core_rows()
    cells = _cells('core', rows)
    assert ab.classify_cell(cells[('core', 0.30, -0.70)])[0] == 'survivor'
    assert ab.classify_cell(cells[('core', 0.10, -0.60)])[0] == 'dropped'
    assert ab.classify_cell(cells[('core', 0.10, -0.50)])[0] == 'incomplete'
    print("[OK] test_classify_cell")


def test_annual_regression_gate():
    rows = build_core_rows()
    cells = _cells('core', rows)
    baseline = cells[('core', 0.30, -0.70)]
    assert ab.annual_regression_ok(cells[('core', 0.20, -0.70)], baseline) is True   # A
    assert ab.annual_regression_ok(cells[('core', 0.25, -0.70)], baseline) is False  # F (2023 +6pp)
    print("[OK] test_annual_regression_gate")


def test_core_lanes_and_finalists():
    rows = build_core_rows()
    cells = _cells('core', rows)
    survivors = {k: v for k, v in cells.items() if ab.classify_cell(v)[0] == 'survivor'}
    # G (dropped) and H (incomplete) must be excluded; the other 7 remain.
    assert set(k[1:] for k in survivors) == {
        (0.30, -0.70), (0.20, -0.70), (0.15, -0.60), (0.15, -0.90),
        (0.20, -0.90), (0.30, -0.60), (0.25, -0.70),
    }, survivors.keys()

    baseline = survivors[('core', 0.30, -0.70)]
    dd_entrants, growth_entrants, dd5y = ab.core_lane_membership(survivors, baseline)

    A, B, C, D, E, F = [('core',) + t for t in
                         ((0.20, -0.70), (0.15, -0.60), (0.15, -0.90),
                          (0.20, -0.90), (0.30, -0.60), (0.25, -0.70))]

    assert dd_entrants == {A, C, D}, dd_entrants
    assert growth_entrants == {B}, growth_entrants
    assert E not in dd_entrants and E not in growth_entrants   # non-entrant
    assert F not in dd_entrants and F not in growth_entrants   # regression-blocked

    # dd_5y ranking: C(50.0) < D(52.0) < A(58.0) < B(60.3). Natural top-3 by
    # dd_5y = [C, D, A] -- none are GROWTH, so B must displace slot 3 (index 2).
    finalists = ab.core_finalists(dd_entrants, growth_entrants, dd5y)
    assert finalists == [C, D, B], finalists
    print("[OK] test_core_lanes_and_finalists")


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
    assert set(k[1:] for k in survivors) == {
        (0.30, -0.70), (0.15, -0.90), (0.20, -0.60), (0.10, -0.50),
    }, survivors.keys()   # Q (dropped) and R (incomplete) excluded

    baseline = survivors[('apex', 0.30, -0.70)]
    pareto, worst9 = ab.apex_pareto(survivors)

    BASE, P1, P2, P3 = [('apex',) + t for t in
                         ((0.30, -0.70), (0.15, -0.90), (0.20, -0.60), (0.10, -0.50))]

    assert worst9[BASE] == 65.0 and worst9[P1] == 55.0 and worst9[P2] == 60.0 and worst9[P3] == 62.0
    # P2 dominates BASE (60<=65, 110>=100) and P3 (60<=62, 110>=90) -> both excluded.
    # P1 vs P2 is a genuine tradeoff (neither dominates) -> both stay.
    assert pareto == {P1, P2}, pareto

    finalists = ab.apex_finalists(pareto, worst9)
    assert finalists == [P1, P2], finalists   # ranked by worst9 ascending, NOT incl P3 despite P3 < BASE
    assert BASE not in finalists   # dominated -> excluded from finalists...
    assert BASE in survivors       # ...but still reachable for the "always reported" row
    print("[OK] test_apex_pareto_and_finalists")


def test_end_to_end_md_report():
    """Full main() pipeline against a temp CSV, with OUT_DIR monkeypatched so
    nothing touches the real experiments/tpsl_refine_2026_08/out/ directory.
    Checks: runs without error, output file exists, stays under the 180-line
    budget, and both profiles + both finalist sets appear."""
    rows = build_core_rows() + build_apex_rows()
    fieldnames = list(rows[0].keys())

    orig_out_dir = ab.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ab.OUT_DIR = tmp
            csv_path = os.path.join(tmp, 'phaseB_synthetic_test.csv')
            import csv as _csv
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                w = _csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)

            out_path = os.path.join(tmp, 'phaseB_summary_TEST.md')
            sys.argv = ['analyze_phaseB.py', '--glob', 'phaseB_synthetic_test.csv', '--out', out_path]
            ab.main()

            assert os.path.exists(out_path)
            with open(out_path, encoding='utf-8') as f:
                text = f.read()
            n_lines = text.count('\n') + 1
            assert n_lines <= 180, f"{n_lines} lines -- over budget"
            assert '## Profile: core' in text
            assert '## Profile: apex' in text
            assert '0.15/-0.60' in text  # B (growth finalist) tag rendered somewhere
            assert 'INCUMBENT' in text   # apex baseline-always-reported path fired
            print(f"[OK] test_end_to_end_md_report ({n_lines} lines)")
        finally:
            ab.OUT_DIR = orig_out_dir


if __name__ == '__main__':
    test_classify_cell()
    test_annual_regression_gate()
    test_core_lanes_and_finalists()
    test_core_no_entrants_is_graceful()
    test_apex_pareto_and_finalists()
    test_end_to_end_md_report()
    print("\n[ALL PASSED]")
