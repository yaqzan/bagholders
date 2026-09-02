#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_analyze_allocC.py -- unit tests for analyze_allocC.py on tiny SYNTHETIC
rows (never real Confirm output -- the BUILD spec forbids this session from
analyzing real results). Pure Python, no DB, no monte_carlo import, no
queue -- runs in well under a second.

Required scenarios (per the BUILD spec): a crash-family-FLAG case, a
calibrated-inversion-FLAG case, and an apex P(2x)=98.9%-NO-SHIP case -- plus
a clean-pass/tiebreak case and a handful of smaller unit tests on the
individual check functions, mirroring experiments/tpsl_refine_2026_08/
driver/test_phaseD.py's structure and test_analyze_allocB.py's
CoreCell/ApexCell-based row construction (so the synthetic CSV schema can
never drift from what allocC_run.py/allocC_apex2x.py actually emit).

Run directly: python experiments/alloc_retune_2026_08/driver/test_analyze_allocC.py
Or via pytest: pytest experiments/alloc_retune_2026_08/driver/test_analyze_allocC.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import analyze_allocC as ac                                                  # noqa: E402
from allocC_run import (                                                     # noqa: E402
    CORE_CELLS, APEX_CELLS, PHASE_D_WINDOWS_12, FILL_PROBE_WINDOWS,
    CALIBRATED_WINDOWS, SURVIVOR_WINDOWS,
)
from allocA_run import CoreCell, ApexCell, CORE_SHAPES, CORE_BASELINE, APEX_BASELINE  # noqa: E402

BASE = CORE_BASELINE          # ('S0', 14, 0.50)
G050 = ('S9', 20, 0.50)
G065 = ('S9', 20, 0.65)
BASE_NAME = ac.core_cell_name(*BASE)
G050_NAME = ac.core_cell_name(*G050)
G065_NAME = ac.core_cell_name(*G065)


def core_cell(shape, max_pos, gross, is_baseline=False):
    ultra, top, mid, low = CORE_SHAPES[shape]
    return CoreCell(shape, ultra, top, mid, low, max_pos, gross, is_baseline)


def apex_cell(n, frac, is_baseline=False):
    return ApexCell(n, frac, is_baseline)


def mkrows(cell, dd_map, med_map=None, p_coll_map=None, windows=PHASE_D_WINDOWS_12, mode='default'):
    """One CSV-row-shaped dict per window in `windows`, using cell.row_fields()
    for the descriptor columns so the schema always matches allocC_run.py."""
    med_map = med_map or {}
    p_coll_map = p_coll_map or {}
    rows = []
    for w in windows:
        rows.append({
            'phase': 'C', 'mode': mode, 'profile': ('apex' if isinstance(cell, ApexCell) else 'core'),
            'window': w,
            **cell.row_fields(),
            'n_iter': 500, 'n_call_signals': 10, 'n_calls_delisted': 5,
            'mean_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'med_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'p10_ret': 0.0, 'p90_ret': 0.0,
            'worst_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'mean_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'p_coll': p_coll_map.get(w, p_coll_map.get('_default', 0.0)),
            'tp_rate': 50.0, 'sl_rate': 30.0, 'hard_rate': 20.0, 'both_rate': 0.0,
            'avg_open_premium_base_pct': 30.0, 'max_open_premium_base_pct': 45.0,
            'call_trades': 20.0, 'put_trades': 0.0, 'realized_call_tp_pct': 45.0,
            'elapsed_prepare_s': 0.1, 'elapsed_sim_s': 1.0,
        })
    return rows


def override_window_row(rows, profile, cell_name, window, **overrides):
    """rows + [a copy of the LAST existing (profile,cell_name,window) row with
    `overrides` layered on] -- exploits merge_mc_cells' 'last occurrence
    wins' rule to mutate just ONE (cell,window) cell of an otherwise-clean
    dataset without hand-rebuilding the whole row set per scenario."""
    base_row = None
    for r in rows:
        if r['profile'] == profile and r['cell_name'] == cell_name and r['window'] == window:
            base_row = r
    assert base_row is not None, f"no existing row for {(profile, cell_name, window)} to override"
    new_row = dict(base_row)
    new_row.update(overrides)
    return rows + [new_row]


def mk_apex2x_row(n, frac, is_baseline, p_2x_ever, worst_dd_pct, median_days_2x, p_collapse):
    return {
        'arm_tag': f"n{n}_f{frac:.3f}", 'n': n, 'frac': frac, 'is_baseline': is_baseline,
        'n_windows_ok': 113, 'n_windows_total': 113, 'n_iter_per_window': 500,
        'n_paths': 113 * 500, 'p_2x_ever': p_2x_ever, 'p_2x_before_50dd': p_2x_ever * 0.9,
        'p_collapse': p_collapse, 'median_days_2x': median_days_2x,
        'p25_days_2x': median_days_2x * 0.7, 'p75_days_2x': median_days_2x * 1.3,
        'median_compound_pct': 100.0, 'worst_dd_pct': worst_dd_pct, 'gross_pct': float(n) * frac,
    }


# ---------------------------------------------------------------------------
# Shared "clean" Core dataset: baseline + BOTH finalists (g050, g065), all
# gates passing, all crash-family reads within tolerance, all three
# batteries non-inverting. Scenario tests clone this and override exactly
# one (cell, window) cell to isolate the ONE failure mode under test.
# ---------------------------------------------------------------------------
def build_clean_default_rows():
    rows = []
    rows += mkrows(core_cell(*BASE, is_baseline=True),
                   dd_map={'2018': 60.0, '2020': 60.0, '2020_crash': 70.0, '2021': 58.0,
                            '2022': 58.0, '2023': 58.0, '2024': 58.0, 'dip': 30.0,
                            '22-now': 58.0, '2025': 58.0, '5y': 60.0, '10y': 65.0},
                   med_map={'5y': 500.0})
    rows += mkrows(core_cell(*G050),
                   dd_map={'2018': 61.0, '2020': 61.0, '2020_crash': 72.0, '2021': 56.0,
                            '2022': 56.0, '2023': 56.0, '2024': 56.0, 'dip': 28.0,
                            '22-now': 54.0, '2025': 56.0, '5y': 55.0, '10y': 66.0},
                   med_map={'5y': 450.0})
    rows += mkrows(core_cell(*G065),
                   dd_map={'2018': 61.5, '2020': 61.0, '2020_crash': 72.5, '2021': 56.5,
                            '2022': 56.5, '2023': 56.5, '2024': 56.5, 'dip': 28.5,
                            '22-now': 54.5, '2025': 56.5, '5y': 56.0, '10y': 66.5},
                   med_map={'5y': 480.0})
    return rows


def build_clean_fillprobe_rows():
    rows = []
    rows += mkrows(core_cell(*BASE, is_baseline=True), dd_map={'22-now': 59.0, '5y': 61.0},
                   windows=FILL_PROBE_WINDOWS, mode='fill_probe')
    rows += mkrows(core_cell(*G050), dd_map={'22-now': 55.0, '5y': 56.0},
                   windows=FILL_PROBE_WINDOWS, mode='fill_probe')
    rows += mkrows(core_cell(*G065), dd_map={'22-now': 55.5, '5y': 56.5},
                   windows=FILL_PROBE_WINDOWS, mode='fill_probe')
    return rows


def build_clean_calibrated_rows():
    rows = []
    rows += mkrows(core_cell(*BASE, is_baseline=True), dd_map={'22-now': 58.0, '5y': 62.0},
                   windows=CALIBRATED_WINDOWS, mode='calibrated')
    rows += mkrows(core_cell(*G050), dd_map={'22-now': 56.0, '5y': 57.0},
                   windows=CALIBRATED_WINDOWS, mode='calibrated')
    rows += mkrows(core_cell(*G065), dd_map={'22-now': 56.5, '5y': 57.5},
                   windows=CALIBRATED_WINDOWS, mode='calibrated')
    return rows


def build_clean_survivor_rows():
    rows = []
    rows += mkrows(core_cell(*BASE, is_baseline=True), dd_map={'2022': 59.0, '22-now': 58.0},
                   windows=SURVIVOR_WINDOWS, mode='survivor_only')
    rows += mkrows(core_cell(*G050), dd_map={'2022': 55.0, '22-now': 54.0},
                   windows=SURVIVOR_WINDOWS, mode='survivor_only')
    rows += mkrows(core_cell(*G065), dd_map={'2022': 55.5, '22-now': 54.5},
                   windows=SURVIVOR_WINDOWS, mode='survivor_only')
    return rows


def find_row_line(text, cell_name):
    """The single markdown table row line for this cell (Core's table starts
    every finalist row with '| <cell_name> |')."""
    for line in text.splitlines():
        if line.startswith(f"| {cell_name} |"):
            return line
    return None


# ---------------------------------------------------------------------------
# Scenario 1: clean pass -> both finalists SHIP; tiebreak picks the
# higher-gross (g065) variant since ITS crash-family reads are also within
# the 3pp tolerance.
# ---------------------------------------------------------------------------
def test_clean_pass_both_ship_and_tiebreak_picks_g065():
    default_cells = ac.merge_mc_cells(build_clean_default_rows())
    fillprobe_cells = ac.merge_mc_cells(build_clean_fillprobe_rows())
    calibrated_cells = ac.merge_mc_cells(build_clean_calibrated_rows())
    survivor_cells = ac.merge_mc_cells(build_clean_survivor_rows())

    buf = io.StringIO()
    ac.analyze_core(buf, default_cells, fillprobe_cells, calibrated_cells, survivor_cells)
    text = buf.getvalue()

    g050_line = find_row_line(text, G050_NAME)
    g065_line = find_row_line(text, G065_NAME)
    assert g050_line is not None and '**SHIP**' in g050_line, g050_line
    assert g065_line is not None and '**SHIP**' in g065_line, g065_line
    assert f"**{G065_NAME}**" in text and 'within the 3pp tolerance' in text, text
    print("[OK] test_clean_pass_both_ship_and_tiebreak_picks_g065")


# ---------------------------------------------------------------------------
# Scenario 2 (REQUIRED): crash-family-FLAG case. g065's 2020_crash regresses
# by +5.0pp (over the 3.0pp limit) while its primary gate + both batteries
# stay clean -- isolates the crash-family reason specifically.
# ---------------------------------------------------------------------------
def test_crash_family_flags():
    default_rows = override_window_row(build_clean_default_rows(), 'core', G065_NAME, '2020_crash',
                                       worst_dd=77.5)   # base=70.0 -> regression +7.5pp (>3pp)
    default_cells = ac.merge_mc_cells(default_rows)
    fillprobe_cells = ac.merge_mc_cells(build_clean_fillprobe_rows())
    calibrated_cells = ac.merge_mc_cells(build_clean_calibrated_rows())
    survivor_cells = ac.merge_mc_cells(build_clean_survivor_rows())

    key, base_key = ('core', G065_NAME), ('core', BASE_NAME)
    flagged, detail = ac.crash_family_check(default_cells[key], default_cells[base_key])
    assert flagged is True, detail
    assert detail['2020_crash'] > ac.CRASH_FAMILY_REGRESSION_LIMIT_PP

    buf = io.StringIO()
    ac.analyze_core(buf, default_cells, fillprobe_cells, calibrated_cells, survivor_cells)
    text = buf.getvalue()
    g065_line = find_row_line(text, G065_NAME)
    assert g065_line is not None, text
    assert '**FLAG**' in g065_line, g065_line
    assert 'crash-family regression' in g065_line, g065_line
    # g050 is untouched -- must still SHIP (verdicts are independent per row).
    g050_line = find_row_line(text, G050_NAME)
    assert g050_line is not None and '**SHIP**' in g050_line, g050_line
    # Tiebreak must now default to g050 (g065 is crash-family-flagged).
    assert f"**{G050_NAME}**" in text and 'defaults to g0.50' in text, text
    print("[OK] test_crash_family_flags")


# ---------------------------------------------------------------------------
# Scenario 3 (REQUIRED): calibrated-inversion-FLAG case. g065 has a real
# default-arm 22-now edge (58.0-54.5=3.5pp, above the 1.5pp floor) but the
# calibrated (0.15+GAP_AWARE) battery shows the finalist WORSE than baseline
# on 22-now (inversion) -- crash-family and fill-probe stay clean.
# ---------------------------------------------------------------------------
def test_calibrated_inversion_flags():
    default_cells = ac.merge_mc_cells(build_clean_default_rows())
    fillprobe_cells = ac.merge_mc_cells(build_clean_fillprobe_rows())
    calibrated_rows = override_window_row(build_clean_calibrated_rows(), 'core', G065_NAME, '22-now',
                                          worst_dd=60.0)   # baseline calibrated 22-now=58.0 -> edge = 58-60 = -2 (inverted)
    calibrated_cells = ac.merge_mc_cells(calibrated_rows)
    survivor_cells = ac.merge_mc_cells(build_clean_survivor_rows())

    key, base_key = ('core', G065_NAME), ('core', BASE_NAME)
    flagged, detail = ac.edge_inversion_check(default_cells, calibrated_cells, key, base_key, CALIBRATED_WINDOWS)
    assert flagged is True, detail
    assert detail['22-now']['edge_default'] > ac.DD_NOISE_FLOOR_PP
    assert detail['22-now']['edge_battery'] <= 0

    buf = io.StringIO()
    ac.analyze_core(buf, default_cells, fillprobe_cells, calibrated_cells, survivor_cells)
    text = buf.getvalue()
    g065_line = find_row_line(text, G065_NAME)
    assert g065_line is not None, text
    assert '**FLAG**' in g065_line, g065_line
    assert 'calibrated' in g065_line and 'inversion' in g065_line, g065_line
    g050_line = find_row_line(text, G050_NAME)
    assert g050_line is not None and '**SHIP**' in g050_line, g050_line
    print("[OK] test_calibrated_inversion_flags")


# ---------------------------------------------------------------------------
# Scenario 4 (REQUIRED): apex P(2x)=98.9%-NO-SHIP case. Every other floor
# clears (worst_dd=60.0<65.4, med_days=150<=200, collapse=0) but P(2x)=98.9%
# is just under the 99.0% floor -- a "so close" case that must still render
# NO-SHIP, not SHIP or a soft FLAG (TASK.md: "Anything less = NO-SHIP").
# ---------------------------------------------------------------------------
def test_apex_p2x_989_no_ships():
    apex2x_rows = ac.merge_apex2x_rows([
        mk_apex2x_row(*APEX_BASELINE, True, 0.994, 65.4, 127.5, 0.0),
        mk_apex2x_row(10, 0.06, False, 0.989, 60.0, 150.0, 0.0),
    ])
    acc = ac.apex2x_acceptance_check(apex2x_rows[(10, 0.06)])
    assert acc['incomplete'] is False
    assert acc['p2x'] == 98.9
    assert acc['accepted'] is False, acc   # 98.9 < 99.0 floor

    buf = io.StringIO()
    ac.analyze_apex(buf, {}, apex2x_rows)
    text = buf.getvalue()
    line = find_row_line(text, 'n10_f0.060')
    assert line is not None, text
    assert '**NO-SHIP**' in line, line
    assert '98.9%' in line, line
    print("[OK] test_apex_p2x_989_no_ships")


# ---------------------------------------------------------------------------
# Smaller unit tests on individual helpers
# ---------------------------------------------------------------------------
def test_collapse_clean():
    rows = mkrows(core_cell(*BASE, is_baseline=True), dd_map={'_default': 50.0},
                  p_coll_map={'2020_crash': 2.0})
    cells = ac.merge_mc_cells(rows)
    clean, bad = ac.collapse_clean(cells[('core', BASE_NAME)])
    assert clean is False and bad == ['2020_crash']
    print("[OK] test_collapse_clean")


def test_collapse_clean_missing_window_fails():
    rows = mkrows(core_cell(*BASE, is_baseline=True), dd_map={'_default': 50.0},
                  windows=[w for w in PHASE_D_WINDOWS_12 if w != '10y'])
    cells = ac.merge_mc_cells(rows)
    clean, bad = ac.collapse_clean(cells[('core', BASE_NAME)])
    assert clean is False and bad == ['10y']
    print("[OK] test_collapse_clean_missing_window_fails")


def test_annual_regression_gate():
    default_cells = ac.merge_mc_cells(build_clean_default_rows())
    baseline = default_cells[('core', BASE_NAME)]
    ok, fail_w = ac.annual_regression_ok(default_cells[('core', G050_NAME)], baseline)
    assert ok is True, fail_w
    bad_rows = override_window_row(build_clean_default_rows(), 'core', G050_NAME, '2023', worst_dd=64.0)
    bad_cells = ac.merge_mc_cells(bad_rows)
    ok2, fail_w2 = ac.annual_regression_ok(bad_cells[('core', G050_NAME)], baseline)
    assert ok2 is False and fail_w2 == '2023'
    print("[OK] test_annual_regression_gate")


def test_crash_family_check_within_tolerance():
    default_cells = ac.merge_mc_cells(build_clean_default_rows())
    flagged, detail = ac.crash_family_check(default_cells[('core', G050_NAME)], default_cells[('core', BASE_NAME)])
    assert flagged is False, detail
    for w in ac.CRASH_FAMILY_WINDOWS:
        assert detail[w] <= ac.CRASH_FAMILY_REGRESSION_LIMIT_PP
    print("[OK] test_crash_family_check_within_tolerance")


def test_crash_family_check_missing_data_flags():
    rows = [r for r in build_clean_default_rows() if not (r['cell_name'] == G050_NAME and r['window'] == '2018')]
    cells = ac.merge_mc_cells(rows)
    flagged, detail = ac.crash_family_check(cells[('core', G050_NAME)], cells[('core', BASE_NAME)])
    assert flagged is True
    assert detail['2018'] == 'missing'
    print("[OK] test_crash_family_check_missing_data_flags")


def test_edge_inversion_check_clean_and_inverted():
    default_cells = ac.merge_mc_cells(build_clean_default_rows())
    fillprobe_cells = ac.merge_mc_cells(build_clean_fillprobe_rows())
    key, base_key = ('core', G050_NAME), ('core', BASE_NAME)
    flagged, detail = ac.edge_inversion_check(default_cells, fillprobe_cells, key, base_key, FILL_PROBE_WINDOWS)
    assert flagged is False, detail

    inverted_fp = override_window_row(build_clean_fillprobe_rows(), 'core', G050_NAME, '5y', worst_dd=63.0)
    fp_cells2 = ac.merge_mc_cells(inverted_fp)
    flagged2, detail2 = ac.edge_inversion_check(default_cells, fp_cells2, key, base_key, FILL_PROBE_WINDOWS)
    assert flagged2 is True, detail2
    print("[OK] test_edge_inversion_check_clean_and_inverted")


def test_survivor_only_collapse_check():
    rows = mkrows(core_cell(*G050), dd_map={'2022': 55.0, '22-now': 54.0},
                  p_coll_map={'2022': 4.0}, windows=SURVIVOR_WINDOWS, mode='survivor_only')
    cells = ac.merge_mc_cells(rows)
    flagged, bad = ac.survivor_only_collapse_check(cells, ('core', G050_NAME))
    assert flagged is True and bad == ['2022']
    print("[OK] test_survivor_only_collapse_check")


def test_core_tiebreak_both_directions():
    winner, why = ac.core_tiebreak({G050_NAME: False, G065_NAME: False})
    assert winner == G065_NAME and 'within the 3pp tolerance' in why
    winner2, why2 = ac.core_tiebreak({G050_NAME: False, G065_NAME: True})
    assert winner2 == G050_NAME and 'defaults to g0.50' in why2
    # missing key -> treated as flagged (defensive default) -> g050 wins.
    winner3, why3 = ac.core_tiebreak({G050_NAME: False})
    assert winner3 == G050_NAME
    print("[OK] test_core_tiebreak_both_directions")


def test_apex2x_acceptance_strict_boundary_and_incomplete():
    # Exactly AT the ceiling (65.4) must NOT accept -- "strictly better than".
    row_at_ceiling = mk_apex2x_row(10, 0.06, False, 0.995, 65.4, 150.0, 0.0)
    acc = ac.apex2x_acceptance_check(row_at_ceiling)
    assert acc['accepted'] is False, acc
    # Clears all four floors with margin.
    row_clear = mk_apex2x_row(10, 0.06, False, 0.995, 60.0, 150.0, 0.0)
    acc2 = ac.apex2x_acceptance_check(row_clear)
    assert acc2['accepted'] is True, acc2
    # Missing row -> incomplete, never a silent accept.
    acc3 = ac.apex2x_acceptance_check(None)
    assert acc3['incomplete'] is True and acc3['accepted'] is False
    print("[OK] test_apex2x_acceptance_strict_boundary_and_incomplete")


def test_end_to_end_md_report():
    """Full main() pipeline against temp CSVs for all 5 file kinds, with
    OUT_DIR monkeypatched so nothing touches the real
    experiments/alloc_retune_2026_08/out/ directory. Checks: runs without
    error, output exists, stays under the 200-line budget, both profiles +
    verdict markers appear."""
    default_rows = build_clean_default_rows()
    default_rows += mkrows(apex_cell(*APEX_BASELINE, is_baseline=True), dd_map={'_default': 60.0})
    default_rows += mkrows(apex_cell(10, 0.06), dd_map={'_default': 55.0})
    fillprobe_rows = build_clean_fillprobe_rows()
    calibrated_rows = build_clean_calibrated_rows()
    survivor_rows = build_clean_survivor_rows()
    apex2x_rows = [
        mk_apex2x_row(*APEX_BASELINE, True, 0.994, 65.4, 127.5, 0.0),
        mk_apex2x_row(10, 0.06, False, 0.995, 55.0, 140.0, 0.0),
        mk_apex2x_row(12, 0.06, False, 0.989, 60.0, 160.0, 0.0),
        mk_apex2x_row(10, 0.08, False, 0.996, 50.0, 130.0, 0.0),
    ]

    orig_out_dir = ac.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ac.OUT_DIR = tmp
            import csv as _csv

            def _write(name, rows):
                if not rows:
                    return
                p = os.path.join(tmp, name)
                with open(p, 'w', newline='', encoding='utf-8') as f:
                    w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)

            # analyze_allocC.py globs allocC_*.csv and merges every file's rows
            # by (profile,cell_name,window) -- sharding across files (as the
            # real gross-sharded jobs would produce) is irrelevant to the
            # analyzer, so one file carrying every default-mode row is fine.
            _write('allocC_core_g050.csv', default_rows)
            _write('allocC_fillprobe_core.csv', fillprobe_rows)
            _write('allocC_calibrated_core.csv', calibrated_rows)
            _write('allocC_survivor_core.csv', survivor_rows)
            _write('allocC_apex2x_apex_confirm.csv', apex2x_rows)

            out_path = os.path.join(tmp, 'allocC_summary_TEST.md')
            sys.argv = ['analyze_allocC.py', '--out', out_path]
            ac.main()

            assert os.path.exists(out_path)
            with open(out_path, encoding='utf-8') as f:
                text = f.read()
            n_lines = text.count('\n') + 1
            assert n_lines <= 200, f"{n_lines} lines -- over budget"
            assert '## Profile: core' in text
            assert '## Profile: apex' in text
            assert '**SHIP**' in text
            assert 'Tiebreak recommendation' in text
            print(f"[OK] test_end_to_end_md_report ({n_lines} lines)")
        finally:
            ac.OUT_DIR = orig_out_dir


if __name__ == '__main__':
    test_clean_pass_both_ship_and_tiebreak_picks_g065()
    test_crash_family_flags()
    test_calibrated_inversion_flags()
    test_apex_p2x_989_no_ships()
    test_collapse_clean()
    test_collapse_clean_missing_window_fails()
    test_annual_regression_gate()
    test_crash_family_check_within_tolerance()
    test_crash_family_check_missing_data_flags()
    test_edge_inversion_check_clean_and_inverted()
    test_survivor_only_collapse_check()
    test_core_tiebreak_both_directions()
    test_apex2x_acceptance_strict_boundary_and_incomplete()
    test_end_to_end_md_report()
    print("\n[ALL PASSED]")
