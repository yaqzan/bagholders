#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_phaseC.py -- unit tests for phaseC_patch.py (cell-grid math, predicate
closures, diagnostics) and analyze_phaseC.py (screen logic, end-to-end md
report on a tiny SYNTHETIC CSV -- never real Phase C output, per the same
discipline test_analyze_phaseB.py documents for Phase B). Pure Python, no DB,
no monte_carlo import, no queue -- runs in well under a second. Predicate
tests use a lightweight duck-typed FakeMC double (plain attributes + a
faithful bisect on-or-before lookup) instead of importing the real
monte_carlo module, which requires live DB connectivity at import time.

Run directly: python experiments/tpsl_refine_2026_08/driver/test_phaseC.py
Or via pytest: pytest experiments/tpsl_refine_2026_08/driver/test_phaseC.py
"""
from __future__ import annotations

import bisect
import os
import sys
import tempfile
from datetime import date

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import phaseC_patch as pc   # noqa: E402
import analyze_phaseC as ac  # noqa: E402


# ---------------------------------------------------------------------------
# FakeMC -- duck-typed double exposing exactly the plain attributes/functions
# phaseC_patch.py's predicate factories read off `mc`. No DB, no
# monte_carlo.py import.
# ---------------------------------------------------------------------------
class FakeMC:
    MWDD_MCC_C = 0.0
    MWDD_MCC_W = 22.0
    MWDD_VIX_PANIC = 28.0
    RXDD_VIX_C = 24.0
    RXDD_VIX_W = 4.0

    @staticmethod
    def breadth_on_or_before(sorted_dates, bmap, d):
        idx = bisect.bisect_right(sorted_dates, d) - 1
        return bmap[sorted_dates[idx]] if idx >= 0 else None


D1, D2, D3, D4, D5 = (date(2022, 1, 3), date(2022, 1, 4), date(2022, 1, 5),
                       date(2022, 1, 6), date(2022, 1, 7))


# ---------------------------------------------------------------------------
# build_stress_cells / build_all_cells
# ---------------------------------------------------------------------------

def test_build_stress_cells_generic_interior_pair():
    """(0.30,-0.70) is well away from both SL clamp boundaries -> all 12
    offset combos survive minus the 1 true identity -> 11 distinct cells,
    never including the base pair itself."""
    cells = pc.build_stress_cells(0.30, -0.70)
    assert len(cells) == 11, cells
    assert len(set(cells)) == 11
    assert (0.30, -0.70) not in cells
    assert (0.35, -0.70) in cells   # tp+0.05, sl+0
    assert (0.30, -0.85) in cells   # tp+0, sl-0.15
    assert (0.30, -0.60) in cells   # tp+0, sl+0.10
    print("[OK] test_build_stress_cells_generic_interior_pair")


def test_build_stress_cells_deadhold_clamp_collision():
    """base sl=-1.00 (dead-hold floor): sl_off=-0.15 clamps back to -1.00,
    colliding with sl_off=0 for every tp_off (and with the true identity for
    tp_off=0) -> only 7 distinct cells survive, hand-derived (see this
    builder's investigation notes / final report)."""
    cells = pc.build_stress_cells(0.20, -1.00)
    expected = {
        (0.20, -0.90),
        (0.25, -1.00), (0.25, -0.90),
        (0.30, -1.00), (0.30, -0.90),
        (0.35, -1.00), (0.35, -0.90),
    }
    assert set(cells) == expected, (set(cells), expected)
    assert len(cells) == len(expected) == 7
    assert (0.20, -1.00) not in cells
    print("[OK] test_build_stress_cells_deadhold_clamp_collision")


def test_build_stress_cells_tightsl_clamp_collision():
    """base sl=-0.25 (tightest floor): sl_off=+0.10 clamps back to -0.25,
    colliding with sl_off=0 for every tp_off (and with the true identity for
    tp_off=0) -> symmetric 7-cell case to the dead-hold one above."""
    cells = pc.build_stress_cells(0.20, -0.25)
    expected = {
        (0.20, -0.40),
        (0.25, -0.25), (0.25, -0.40),
        (0.30, -0.25), (0.30, -0.40),
        (0.35, -0.25), (0.35, -0.40),
    }
    assert set(cells) == expected, (set(cells), expected)
    assert len(cells) == 7
    assert (0.20, -0.25) not in cells
    print("[OK] test_build_stress_cells_tightsl_clamp_collision")


def test_build_stress_cells_all_within_clamp_bounds():
    for tp_b, sl_b in [(0.10, -1.00), (1.10, -0.25), (0.30, -0.70)]:
        for ts, ss in pc.build_stress_cells(tp_b, sl_b):
            assert pc.SL_CLAMP_MIN - 1e-9 <= ss <= pc.SL_CLAMP_MAX + 1e-9, (tp_b, sl_b, ts, ss)
    print("[OK] test_build_stress_cells_all_within_clamp_bounds")


def test_build_all_cells_counts_and_shape():
    cells = pc.build_all_cells(list(pc.SOURCES), 0.30, -0.70)
    assert len(cells) == 22 + 11 + 11 + 11 == 55, len(cells)
    by_source = {}
    for source, thr, ts, ss in cells:
        by_source.setdefault(source, []).append((thr, ts, ss))
    assert len(by_source['breadth']) == 22
    assert {thr for thr, _, _ in by_source['breadth']} == {40, 50}
    assert len(by_source['mwdd_band']) == 11
    assert all(thr is None for thr, _, _ in by_source['mwdd_band'])
    assert len(by_source['rxdd_band']) == 11
    assert len(by_source['regime_down']) == 11
    # canonical order preserved regardless of input order
    cells_reordered = pc.build_all_cells(['regime_down', 'breadth', 'rxdd_band', 'mwdd_band'], 0.30, -0.70)
    assert [c[0] for c in cells_reordered[:1]] == ['breadth'], "output must follow SOURCES canonical order"
    print("[OK] test_build_all_cells_counts_and_shape")


def test_build_all_cells_single_source_subset():
    cells = pc.build_all_cells(['mwdd_band'], 0.30, -0.70)
    assert len(cells) == 11
    assert all(c[0] == 'mwdd_band' for c in cells)
    print("[OK] test_build_all_cells_single_source_subset")


# ---------------------------------------------------------------------------
# predicate closures
# ---------------------------------------------------------------------------

def test_band_membership():
    assert pc._band_membership(24.0, 24.0, 4.0) is True     # center
    assert pc._band_membership(20.0, 24.0, 4.0) is True      # lower boundary, inclusive
    assert pc._band_membership(28.0, 24.0, 4.0) is True      # upper boundary, inclusive
    assert pc._band_membership(19.99, 24.0, 4.0) is False
    assert pc._band_membership(28.01, 24.0, 4.0) is False
    assert pc._band_membership(None, 24.0, 4.0) is False
    assert pc._band_membership(24.0, 24.0, 0.0) is False     # width<=0 guard -> never fires
    print("[OK] test_band_membership")


def test_mwdd_predicate():
    maps = {
        'mcc_dates': [D1, D2, D3, D4], 'mcc_map': {D1: 0.0, D2: -22.0, D3: 22.5, D4: 0.0},
        'vix_dates': [D1, D4], 'vix_map': {D1: 15.0, D4: 30.0},
    }
    pred = pc.make_mwdd_predicate(FakeMC, maps)
    assert pred(None, None, D1) is True     # mcc=0 center, vix on-or-before=15 calm
    assert pred(None, None, D2) is True     # mcc=-22 boundary, vix on-or-before(D1)=15 calm
    assert pred(None, None, D3) is False    # mcc=22.5 out of band regardless of vix
    assert pred(None, None, D4) is False    # mcc=0 (in band) BUT vix=30 >= panic 28 -> excluded
    # keyword compatibility with is_stressed's real call site (count_map kwarg)
    assert pred(None, None, D1, count_map={'x': 1}) is True
    print("[OK] test_mwdd_predicate")


def test_mwdd_predicate_missing_mcc_is_false():
    maps = {'mcc_dates': [], 'mcc_map': {}, 'vix_dates': [], 'vix_map': {}}
    pred = pc.make_mwdd_predicate(FakeMC, maps)
    assert pred(None, None, D1) is False
    print("[OK] test_mwdd_predicate_missing_mcc_is_false")


def test_rxdd_predicate():
    maps = {'vix_dates': [D1, D2, D3, D4, D5],
             'vix_map': {D1: 20.0, D2: 24.0, D3: 28.0, D4: 28.1, D5: 10.0}}
    pred = pc.make_rxdd_predicate(FakeMC, maps)
    assert pred(None, None, D1) is True    # lower boundary
    assert pred(None, None, D2) is True    # center
    assert pred(None, None, D3) is True    # upper boundary
    assert pred(None, None, D4) is False   # just above
    assert pred(None, None, D5) is False   # well below
    print("[OK] test_rxdd_predicate")


def test_regime_down_predicate():
    maps = {'regime_dates': [D2, D3, D4], 'regime_mult_map': {D2: 0.95, D3: 1.0, D4: 1.05}}
    pred = pc.make_regime_down_predicate(FakeMC, maps)
    assert pred(None, None, D2) is True     # 0.95 < 1.0
    assert pred(None, None, D3) is False    # 1.0 is NOT < 1.0 (strict)
    assert pred(None, None, D4) is False    # 1.05 > 1.0
    assert pred(None, None, D1) is False    # before any coverage -> None -> False, not a crash
    print("[OK] test_regime_down_predicate")


def test_activate_source_dispatch():
    def _original(sorted_dates, bmap, d, count_map=None):
        return False
    mc = FakeMC()
    mc.is_stressed = _original
    mc.BREADTH_THRESHOLD = 40
    maps = {
        'mcc_dates': [D1], 'mcc_map': {D1: 0.0}, 'vix_dates': [D1], 'vix_map': {D1: 15.0},
        'regime_dates': [D1], 'regime_mult_map': {D1: 0.9},
    }

    pc.activate_source(mc, pc.FLAT_SOURCE, None, maps, _original, 40)
    assert mc.is_stressed is _original and mc.BREADTH_THRESHOLD == 40

    pc.activate_source(mc, 'breadth', 50, maps, _original, 40)
    assert mc.is_stressed is _original, "breadth reuses the engine's own unpatched is_stressed"
    assert mc.BREADTH_THRESHOLD == 50

    pc.activate_source(mc, 'mwdd_band', None, maps, _original, 40)
    assert mc.is_stressed is not _original
    assert mc.BREADTH_THRESHOLD == 40, "non-breadth sources restore the original threshold (unused, but clean)"
    mwdd_closure = mc.is_stressed

    pc.activate_source(mc, 'rxdd_band', None, maps, _original, 40)
    assert mc.is_stressed is not _original and mc.is_stressed is not mwdd_closure

    pc.activate_source(mc, 'regime_down', None, maps, _original, 40)
    assert mc.is_stressed is not _original

    try:
        pc.activate_source(mc, 'not_a_real_source', None, maps, _original, 40)
        assert False, "expected ValueError"
    except ValueError:
        pass
    print("[OK] test_activate_source_dispatch")


# ---------------------------------------------------------------------------
# stress_source_diagnostics
# ---------------------------------------------------------------------------

def test_stress_source_diagnostics():
    ctx = {'call_outcomes': {
        1: {'stressed': True, 'kind': 'tp'},
        2: {'stressed': False, 'kind': 'sl'},
        3: {'stressed': True, 'kind': 'hard', '_dte': '15'},
        4: {'stressed': False, 'kind': 'tp'},
    }}
    n, frac, n_dte, dte_frac = pc.stress_source_diagnostics(ctx)
    assert n == 4
    assert frac == 50.0
    assert n_dte == 1
    assert dte_frac == 25.0
    print("[OK] test_stress_source_diagnostics")


def test_stress_source_diagnostics_empty():
    n, frac, n_dte, dte_frac = pc.stress_source_diagnostics({'call_outcomes': {}})
    assert (n, frac, n_dte, dte_frac) == (0, None, 0, None)
    print("[OK] test_stress_source_diagnostics_empty")


# ---------------------------------------------------------------------------
# analyze_phaseC -- classify_cell / screen_pass / end-to-end md report on a
# synthetic CSV matching phaseC_run.py's CSV_FIELDS schema.
# ---------------------------------------------------------------------------
BASE_TP, BASE_SL = 0.30, -0.70


def mkrows(profile, source, thr, ts, ss, dd_map, med_map=None, p_coll_map=None,
           windows=ac.PHASE_C_WINDOWS):
    med_map = med_map or {}
    p_coll_map = p_coll_map or {}
    rows = []
    for w in windows:
        rows.append({
            'phase': 'C', 'profile': profile, 'window': w, 'tp': BASE_TP, 'sl': BASE_SL,
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
            'source': source, 'thr': '' if thr is None else str(thr),
            'tp_stress': ts, 'sl_stress': ss,
            'stressed_call_frac': 30.0, 'dte_routed_frac': 1.0,
        })
    return rows


def build_core_rows():
    rows = []
    rows += mkrows('core', 'flat_base', None, BASE_TP, BASE_SL,
                    dd_map={'5y': 60.0, '22-now': 55.0, '2022': 50.0, '2024': 38.0, '2020_crash': 70.0},
                    med_map={'5y': 500.0})
    # SCREEN-PASS: dd_5y 60->57 (>=1.5pp), med_5y 500->450 (>=0.75x=375)
    rows += mkrows('core', 'breadth', 40, 0.35, -0.70,
                    dd_map={'5y': 57.0, '22-now': 54.0, '2022': 48.0, '2024': 36.0, '2020_crash': 66.0},
                    med_map={'5y': 450.0})
    # NOT screen-pass: dd_5y only 60->59.9 (<1.5pp improvement)
    rows += mkrows('core', 'breadth', 50, 0.35, -0.70,
                    dd_map={'5y': 59.9, '22-now': 55.0, '2022': 50.0, '2024': 38.0, '2020_crash': 70.0},
                    med_map={'5y': 460.0})
    # NOT screen-pass: great DD (60->55) but med_5y gutted (500->100 < 375)
    rows += mkrows('core', 'mwdd_band', None, 0.30, -0.85,
                    dd_map={'5y': 55.0, '22-now': 53.0, '2022': 47.0, '2024': 35.0, '2020_crash': 64.0},
                    med_map={'5y': 100.0})
    # DROPPED: collapse on 2020_crash
    rows += mkrows('core', 'rxdd_band', None, 0.30, -0.60,
                    dd_map={'5y': 50.0, '22-now': 50.0, '2022': 45.0, '2024': 30.0, '2020_crash': 95.0},
                    med_map={'5y': 300.0}, p_coll_map={'2020_crash': 8.0})
    # INCOMPLETE: missing 2020_crash entirely
    rows += mkrows('core', 'regime_down', None, 0.30, -0.90,
                    dd_map={'5y': 52.0, '22-now': 51.0, '2022': 46.0, '2024': 32.0}, med_map={'5y': 320.0},
                    windows=[w for w in ac.PHASE_C_WINDOWS if w != '2020_crash'])
    return rows


def _cells(profile, rows):
    all_cells = ac.merge_cells(rows)
    return {k: v for k, v in all_cells.items() if k[0] == profile}


def test_classify_cell():
    cells = _cells('core', build_core_rows())
    assert ac.classify_cell(cells[('core', 'flat_base', '', 0.30, -0.70)])[0] == 'survivor'
    assert ac.classify_cell(cells[('core', 'breadth', '40', 0.35, -0.70)])[0] == 'survivor'
    assert ac.classify_cell(cells[('core', 'rxdd_band', '', 0.30, -0.60)])[0] == 'dropped'
    assert ac.classify_cell(cells[('core', 'regime_down', '', 0.30, -0.90)])[0] == 'incomplete'
    print("[OK] test_classify_cell")


def test_screen_pass_logic():
    cells = _cells('core', build_core_rows())
    baseline = cells[('core', 'flat_base', '', 0.30, -0.70)]
    assert ac.screen_pass(cells[('core', 'breadth', '40', 0.35, -0.70)], baseline) is True
    assert ac.screen_pass(cells[('core', 'breadth', '50', 0.35, -0.70)], baseline) is False   # dd improve too small
    assert ac.screen_pass(cells[('core', 'mwdd_band', '', 0.30, -0.85)], baseline) is False   # med ratio fails
    print("[OK] test_screen_pass_logic")


def test_end_to_end_md_report():
    """Full main() pipeline against a temp CSV, OUT_DIR monkeypatched so
    nothing touches the real experiments/tpsl_refine_2026_08/out/ directory.
    Checks: runs without error, output exists, stays under the 180-line
    budget, both the PASS and non-PASS cells render, dropped/incomplete are
    accounted for."""
    rows = build_core_rows()
    fieldnames = list(rows[0].keys())

    orig_out_dir = ac.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ac.OUT_DIR = tmp
            csv_path = os.path.join(tmp, 'phaseC_synthetic_test.csv')
            import csv as _csv
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                w = _csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)

            out_path = os.path.join(tmp, 'phaseC_summary_TEST.md')
            sys.argv = ['analyze_phaseC.py', '--glob', 'phaseC_synthetic_test.csv', '--out', out_path]
            ac.main()

            assert os.path.exists(out_path)
            with open(out_path, encoding='utf-8') as f:
                text = f.read()
            n_lines = text.count('\n') + 1
            assert n_lines <= 180, f"{n_lines} lines -- over budget"
            assert '## Profile: core' in text
            assert 'breadth@40 +0.35/-0.70' in text
            assert 'PASS' in text
            assert 'dropped' in text.lower()
            assert 'incomplete' in text.lower()
            print(f"[OK] test_end_to_end_md_report ({n_lines} lines)")
        finally:
            ac.OUT_DIR = orig_out_dir


def test_validate_glob_excludes_aux_files():
    """load_rows must exclude phaseC_validate_*.csv / *smoke* by default --
    mixing injection-validation rows into a real science read would silently
    corrupt the baseline/screen math (validate rows use VALIDATE_TP/SL, N=50,
    a different window set)."""
    orig_out_dir = ac.OUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ac.OUT_DIR = tmp
            rows = build_core_rows()
            fieldnames = list(rows[0].keys())
            import csv as _csv
            for name in ('phaseC_real_job.csv', 'phaseC_validate_coreN.csv', 'phaseC_smoke_job.csv'):
                with open(os.path.join(tmp, name), 'w', newline='', encoding='utf-8') as f:
                    w = _csv.DictWriter(f, fieldnames=fieldnames)
                    w.writeheader()
                    w.writerows(rows)
            _, paths = ac.load_rows('phaseC_*.csv', exclude_aux=True)
            names = sorted(os.path.basename(p) for p in paths)
            assert names == ['phaseC_real_job.csv'], names
        finally:
            ac.OUT_DIR = orig_out_dir
    print("[OK] test_validate_glob_excludes_aux_files")


if __name__ == '__main__':
    test_build_stress_cells_generic_interior_pair()
    test_build_stress_cells_deadhold_clamp_collision()
    test_build_stress_cells_tightsl_clamp_collision()
    test_build_stress_cells_all_within_clamp_bounds()
    test_build_all_cells_counts_and_shape()
    test_build_all_cells_single_source_subset()
    test_band_membership()
    test_mwdd_predicate()
    test_mwdd_predicate_missing_mcc_is_false()
    test_rxdd_predicate()
    test_regime_down_predicate()
    test_activate_source_dispatch()
    test_stress_source_diagnostics()
    test_stress_source_diagnostics_empty()
    test_classify_cell()
    test_screen_pass_logic()
    test_end_to_end_md_report()
    test_validate_glob_excludes_aux_files()
    print("\n[ALL PASSED]")
