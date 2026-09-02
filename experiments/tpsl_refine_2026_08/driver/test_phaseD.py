#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_phaseD.py -- unit tests for analyze_phaseD.py on tiny SYNTHETIC rows
(never real Phase D output -- this session must not run the analyzer on real
data, per the Phase D build spec). Pure Python, no DB, no monte_carlo
import, no queue -- runs in well under a second.

Required scenarios (per the Phase D build spec): a clean-pass case (SHIP), a
fill-probe-inversion case (FLAG), a survivor-only-collapse case (FLAG), and
an Apex-tie case (NO-SHIP) -- plus a handful of smaller unit tests on the
individual check functions.

Run directly: python experiments/tpsl_refine_2026_08/driver/test_phaseD.py
Or via pytest: pytest experiments/tpsl_refine_2026_08/driver/test_phaseD.py
"""
from __future__ import annotations

import io
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import analyze_phaseD as ad   # noqa: E402
from phaseD_run import PHASE_D_WINDOWS_12, FILL_PROBE_WINDOWS, SURVIVOR_WINDOWS, BASELINE_CELL   # noqa: E402

BASE_TP, BASE_SL = BASELINE_CELL


def mkrows(profile, tp, sl, dd_map, med_map=None, p_coll_map=None, windows=PHASE_D_WINDOWS_12,
          mode='default'):
    """One CSV-shaped row dict per window in `windows` (matches phaseD_run.py
    CSV_FIELDS). dd_map/med_map/p_coll_map: window -> value, with a
    '_default' key for unlisted windows."""
    med_map = med_map or {}
    p_coll_map = p_coll_map or {}
    rows = []
    for w in windows:
        rows.append({
            'phase': 'D', 'mode': mode, 'profile': profile, 'window': w, 'tp': tp, 'sl': sl,
            'n_iter': 500, 'n_call_signals': 10,
            'mean_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'med_ret': med_map.get(w, med_map.get('_default', 0.0)),
            'p10_ret': 0.0, 'p90_ret': 0.0,
            'worst_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'mean_dd': dd_map.get(w, dd_map.get('_default', 0.0)),
            'p_coll': p_coll_map.get(w, p_coll_map.get('_default', 0.0)),
            'tp_rate': 50.0, 'sl_rate': 30.0, 'hard_rate': 20.0, 'both_rate': 0.0,
            'elapsed_prepare_s': 0.1, 'elapsed_sim_s': 1.0,
            'realized_call_tp_pct': 45.0, 'n_calls_delisted': 5,
        })
    return rows


def mk_parity_row(profile, tp, sl, is_baseline, max_dd_pct, total_return_pct):
    return {
        'job': 'test', 'profile': profile, 'sizing_note': 'core-cascade-native',
        'arm_tag': f"tp{tp:+.2f}_sl{sl:+.2f}", 'tp': tp, 'sl': sl, 'is_baseline': is_baseline,
        'from_date': '2022-01-01', 'to_date': '2026-04-15', 'capital': 50000.0,
        'final_equity': 50000.0 * (1 + total_return_pct / 100.0),
        'total_return_pct': total_return_pct, 'max_dd_pct': max_dd_pct,
        'n_trades': 100, 'n_tp': 50, 'n_sl': 30, 'n_hard': 20, 'elapsed_s': 1.0,
    }


def mk_apex2x_row(tp, sl, p_2x_ever, worst_dd_pct, median_days_2x, p_collapse):
    return {
        'arm_tag': f"tp{tp:+.2f}_sl{sl:+.2f}", 'tp': tp, 'sl': sl,
        'is_baseline': (tp, sl) == (BASE_TP, BASE_SL),
        'n_windows_ok': 113, 'n_windows_total': 113, 'n_iter_per_window': 500,
        'n_paths': 113 * 500, 'p_2x_ever': p_2x_ever, 'p_2x_before_50dd': p_2x_ever * 0.9,
        'p_collapse': p_collapse, 'median_days_2x': median_days_2x,
        'p25_days_2x': median_days_2x * 0.7, 'p75_days_2x': median_days_2x * 1.3,
        'median_compound_pct': 100.0, 'worst_dd_pct': worst_dd_pct, 'gross_pct': 1.0,
    }


def find_row_line(text, cell_tag):
    """The single markdown table row line containing this cell's tag (e.g.
    '0.15/-0.90') -- table rows are written as one f.write() call each, so
    this is a reliable line-level lookup."""
    for line in text.splitlines():
        if line.startswith(f"| {cell_tag} |"):
            return line
    return None


# ---------------------------------------------------------------------------
# Scenario 1: clean pass -> SHIP
# ---------------------------------------------------------------------------
FINALIST_A = (0.15, -0.90)


def _clean_pass_default_rows():
    rows = []
    rows += mkrows('core', BASE_TP, BASE_SL,
                    dd_map={'_default': 60.0, '5y': 60.0, '22-now': 55.0,
                             '2021': 58.0, '2022': 58.0, '2023': 58.0, '2024': 58.0, '2025': 58.0},
                    med_map={'5y': 500.0})
    rows += mkrows('core', *FINALIST_A,
                    dd_map={'_default': 54.0, '5y': 55.0, '22-now': 54.0,
                             '2021': 56.0, '2022': 56.0, '2023': 56.0, '2024': 56.0, '2025': 56.0},
                    med_map={'5y': 450.0})
    return rows


def _clean_pass_fillprobe_rows():
    rows = []
    rows += mkrows('core', BASE_TP, BASE_SL, dd_map={'22-now': 58.0, '5y': 63.0},
                    windows=FILL_PROBE_WINDOWS, mode='fill_probe')
    rows += mkrows('core', *FINALIST_A, dd_map={'22-now': 55.0, '5y': 59.0},
                    windows=FILL_PROBE_WINDOWS, mode='fill_probe')
    return rows


def _clean_pass_survivor_rows():
    rows = []
    rows += mkrows('core', BASE_TP, BASE_SL, dd_map={'2022': 59.0, '22-now': 58.0},
                    windows=SURVIVOR_WINDOWS, mode='survivor_only')
    rows += mkrows('core', *FINALIST_A, dd_map={'2022': 56.0, '22-now': 55.0},
                    windows=SURVIVOR_WINDOWS, mode='survivor_only')
    return rows


def _clean_pass_parity_rows():
    return [
        mk_parity_row('core', BASE_TP, BASE_SL, True, 60.0, 400.0),
        mk_parity_row('core', *FINALIST_A, False, 54.0, 380.0),
    ]


def test_clean_pass_ships():
    default_cells = ad.merge_mc_cells(_clean_pass_default_rows())
    fillprobe_cells = ad.merge_mc_cells(_clean_pass_fillprobe_rows())
    survivor_cells = ad.merge_mc_cells(_clean_pass_survivor_rows())
    parity_rows = ad.merge_parity_rows(_clean_pass_parity_rows())

    key = ('core',) + FINALIST_A
    baseline = default_cells[('core', BASE_TP, BASE_SL)]
    lane = ad.core_lane_check(default_cells[key], baseline)
    assert lane['in_dd_lane'] is True, lane   # sanity: this IS a real LANE-DD entrant

    buf = io.StringIO()
    ad.analyze_core(buf, default_cells, fillprobe_cells, survivor_cells, parity_rows)
    text = buf.getvalue()
    line = find_row_line(text, ad.cell_tag(*FINALIST_A))
    assert line is not None, text
    assert '**SHIP**' in line, line
    print("[OK] test_clean_pass_ships")


# ---------------------------------------------------------------------------
# Scenario 2: fill-probe inversion -> FLAG
# ---------------------------------------------------------------------------
def _fillprobe_invert_default_rows():
    rows = []
    rows += mkrows('core', BASE_TP, BASE_SL,
                    dd_map={'_default': 60.0, '5y': 60.0, '22-now': 55.0,
                             '2021': 58.0, '2022': 58.0, '2023': 58.0, '2024': 58.0, '2025': 58.0},
                    med_map={'5y': 500.0})
    # Real default-arm edge on BOTH 22-now (3.0pp) and 5y (5.0pp) -- both well above the 1.5pp floor.
    rows += mkrows('core', *FINALIST_A,
                    dd_map={'_default': 54.0, '5y': 55.0, '22-now': 52.0,
                             '2021': 56.0, '2022': 56.0, '2023': 56.0, '2024': 56.0, '2025': 56.0},
                    med_map={'5y': 450.0})
    return rows


def _fillprobe_invert_fillprobe_rows():
    rows = []
    rows += mkrows('core', BASE_TP, BASE_SL, dd_map={'22-now': 58.0, '5y': 63.0},
                    windows=FILL_PROBE_WINDOWS, mode='fill_probe')
    # 22-now INVERTS under the probe: finalist (60.0) now WORSE than baseline (58.0).
    # 5y stays non-inverted (informational -- the check fires on EITHER window).
    rows += mkrows('core', *FINALIST_A, dd_map={'22-now': 60.0, '5y': 59.0},
                    windows=FILL_PROBE_WINDOWS, mode='fill_probe')
    return rows


def test_fillprobe_inversion_flags():
    default_cells = ad.merge_mc_cells(_fillprobe_invert_default_rows())
    fillprobe_cells = ad.merge_mc_cells(_fillprobe_invert_fillprobe_rows())
    survivor_cells = ad.merge_mc_cells(_clean_pass_survivor_rows())   # reuse clean (non-flagging) survivor arm
    parity_rows = ad.merge_parity_rows(_clean_pass_parity_rows())     # reuse clean (non-flagging) parity

    key, base_key = ('core',) + FINALIST_A, ('core', BASE_TP, BASE_SL)
    flagged, detail = ad.fillprobe_inversion_check(default_cells, fillprobe_cells, key, base_key)
    assert flagged is True, detail
    assert detail['22-now']['edge_default'] > ad.DD_NOISE_FLOOR_PP
    assert detail['22-now']['edge_fillprobe'] <= 0

    buf = io.StringIO()
    ad.analyze_core(buf, default_cells, fillprobe_cells, survivor_cells, parity_rows)
    text = buf.getvalue()
    line = find_row_line(text, ad.cell_tag(*FINALIST_A))
    assert line is not None, text
    assert '**FLAG**' in line, line
    assert 'fill-probe inversion' in line, line
    print("[OK] test_fillprobe_inversion_flags")


# ---------------------------------------------------------------------------
# Scenario 3: survivor-only collapse -> FLAG
# ---------------------------------------------------------------------------
def _survivor_collapse_rows():
    rows = []
    rows += mkrows('core', BASE_TP, BASE_SL, dd_map={'2022': 59.0, '22-now': 58.0},
                    windows=SURVIVOR_WINDOWS, mode='survivor_only')
    # Finalist COLLAPSES (p_coll>0) on 2022 in the survivor-only arm, despite
    # a perfectly clean (p_coll=0 on all 12) default arm.
    rows += mkrows('core', *FINALIST_A, dd_map={'2022': 56.0, '22-now': 55.0},
                    p_coll_map={'2022': 4.0}, windows=SURVIVOR_WINDOWS, mode='survivor_only')
    return rows


def test_survivor_only_collapse_flags():
    default_cells = ad.merge_mc_cells(_clean_pass_default_rows())     # clean, collapse-free default arm
    fillprobe_cells = ad.merge_mc_cells(_clean_pass_fillprobe_rows())  # clean, non-inverting probe
    survivor_cells = ad.merge_mc_cells(_survivor_collapse_rows())
    parity_rows = ad.merge_parity_rows(_clean_pass_parity_rows())

    key = ('core',) + FINALIST_A
    flagged, bad = ad.survivor_only_collapse_check(survivor_cells, key)
    assert flagged is True
    assert '2022' in bad

    buf = io.StringIO()
    ad.analyze_core(buf, default_cells, fillprobe_cells, survivor_cells, parity_rows)
    text = buf.getvalue()
    line = find_row_line(text, ad.cell_tag(*FINALIST_A))
    assert line is not None, text
    assert '**FLAG**' in line, line
    assert 'survivor-only arm' in line, line
    print("[OK] test_survivor_only_collapse_flags")


# ---------------------------------------------------------------------------
# Scenario 4: Apex tie -> NO-SHIP
# ---------------------------------------------------------------------------
APEX_FINALIST = (0.10, -0.60)


def _apex_tie_default_rows():
    rows = []
    rows += mkrows('apex', BASE_TP, BASE_SL, dd_map={'_default': 65.0}, med_map={'_default': 50.0})
    rows += mkrows('apex', *APEX_FINALIST, dd_map={'_default': 63.0}, med_map={'_default': 55.0})
    return rows


def test_apex_tie_no_ships():
    default_cells = ad.merge_mc_cells(_apex_tie_default_rows())
    fillprobe_cells = ad.merge_mc_cells([])
    survivor_cells = ad.merge_mc_cells([])
    parity_rows = ad.merge_parity_rows([])
    # Exact tie against the live cert: P(2x)=72.0% (==floor, not >), worst_dd=76.0%
    # (==ceiling, not <), median_days=191.0 (==cert, not <), collapse=0 -- floors_ok
    # True but n_strictly_better=0 -> PREREG section 3's own "tie -> no ship".
    apex2x_rows = ad.merge_apex2x_rows([
        mk_apex2x_row(BASE_TP, BASE_SL, 0.65, 82.0, 250.0, 0.0),
        mk_apex2x_row(*APEX_FINALIST, 0.72, 76.0, 191.0, 0.0),
    ])

    acc = ad.apex_acceptance_check(apex2x_rows[APEX_FINALIST])
    assert acc['floors_ok'] is True, acc
    assert acc['n_strictly_better'] == 0, acc
    assert acc['accepted'] is False, acc

    buf = io.StringIO()
    ad.analyze_apex(buf, default_cells, fillprobe_cells, survivor_cells, parity_rows, apex2x_rows)
    text = buf.getvalue()
    line = find_row_line(text, ad.cell_tag(*APEX_FINALIST))
    assert line is not None, text
    assert '**NO-SHIP**' in line, line
    assert 'tie' in line, line
    print("[OK] test_apex_tie_no_ships")


# ---------------------------------------------------------------------------
# Smaller unit tests on individual helpers
# ---------------------------------------------------------------------------
def test_collapse_clean():
    rows = mkrows('core', BASE_TP, BASE_SL, dd_map={'_default': 50.0},
                  p_coll_map={'2020_crash': 2.0})
    cells = ad.merge_mc_cells(rows)
    cell = cells[('core', BASE_TP, BASE_SL)]
    clean, bad = ad.collapse_clean(cell)
    assert clean is False
    assert bad == ['2020_crash']
    print("[OK] test_collapse_clean")


def test_collapse_clean_missing_window_fails():
    rows = mkrows('core', BASE_TP, BASE_SL, dd_map={'_default': 50.0},
                  windows=[w for w in PHASE_D_WINDOWS_12 if w != '10y'])
    cells = ad.merge_mc_cells(rows)
    cell = cells[('core', BASE_TP, BASE_SL)]
    clean, bad = ad.collapse_clean(cell)
    assert clean is False
    assert bad == ['10y']
    print("[OK] test_collapse_clean_missing_window_fails")


def test_annual_regression_gate():
    default_cells = ad.merge_mc_cells(_fillprobe_invert_default_rows())
    baseline = default_cells[('core', BASE_TP, BASE_SL)]
    ok, fail_w = ad.annual_regression_ok(default_cells[('core',) + FINALIST_A], baseline)
    assert ok is True, fail_w
    # Now regress 2023 by +6pp (over the 5pp limit).
    bad_rows = mkrows('core', *FINALIST_A, dd_map={'2023': 64.0, '_default': 54.0})
    bad_cells = ad.merge_mc_cells(bad_rows)
    ok2, fail_w2 = ad.annual_regression_ok(bad_cells[('core',) + FINALIST_A], baseline)
    assert ok2 is False
    assert fail_w2 == '2023'
    print("[OK] test_annual_regression_gate")


def test_sign_disagrees():
    assert ad.sign_disagrees(5.0, 6.0) is False     # same sign
    assert ad.sign_disagrees(5.0, -6.0) is True      # opposite sign, both above floor
    assert ad.sign_disagrees(0.5, -6.0, floor=1.5) is False   # mc_edge below floor -- not a real signal
    assert ad.sign_disagrees(None, -6.0) is False    # missing data never flags
    print("[OK] test_sign_disagrees")


def test_apex_acceptance_strictly_better_accepts():
    row = mk_apex2x_row(*APEX_FINALIST, 0.80, 70.0, 150.0, 0.0)   # clears all 3 floors AND all 3 strictly-better
    acc = ad.apex_acceptance_check(row)
    assert acc['floors_ok'] is True
    assert acc['n_strictly_better'] == 3
    assert acc['accepted'] is True
    print("[OK] test_apex_acceptance_strictly_better_accepts")


def test_apex_acceptance_missing_row_incomplete():
    acc = ad.apex_acceptance_check(None)
    assert acc['incomplete'] is True
    assert acc['accepted'] is False
    print("[OK] test_apex_acceptance_missing_row_incomplete")


if __name__ == '__main__':
    test_clean_pass_ships()
    test_fillprobe_inversion_flags()
    test_survivor_only_collapse_flags()
    test_apex_tie_no_ships()
    test_collapse_clean()
    test_collapse_clean_missing_window_fails()
    test_annual_regression_gate()
    test_sign_disagrees()
    test_apex_acceptance_strictly_better_accepts()
    test_apex_acceptance_missing_row_incomplete()
    print("\n[ALL PASSED]")
