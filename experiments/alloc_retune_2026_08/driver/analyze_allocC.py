#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_allocC.py -- the formal Confirm-phase read. MECHANICAL evaluation:
every rule below is copied VERBATIM from experiments/alloc_retune_2026_08/
TASK.md's locked confirm-phase decision rules (written 2026-08-10 BEFORE any
N=500 confirm number was seen). No judgment calls made here -- ambiguous/
incomplete evidence renders FLAG (present, don't ship), never a silent SHIP
(same house convention as experiments/tpsl_refine_2026_08/driver/
analyze_phaseD.py, which this file's structure mirrors).

CORE rule (TASK.md, verbatim): "CORE candidate ships autonomously ONLY if at
N=500x12 vs in-phase baseline: dd_5y <= base-1.5pp AND med_5y >= 0.75x base
AND dd_22now <= base+0.5pp AND no {2021..2025} window regresses >5pp AND
collapse 0/12 AND crash-family (2018/2020/2020_crash/10y) regression <=3pp
each (worse = FLAG, present don't ship) AND fill-probe 0.10 + calibrated
0.15+GAP_AWARE differentials non-inverting AND survivor-robust. Between the
two Core finalists: higher-gross variant wins only if its crash-family reads
are within the 3pp tolerance; else the g0.50 variant."

APEX rule (TASK.md, verbatim): "APEX candidate ships ONLY if its 2x-race (vs
the tpsl #379 baseline arm...): P(2x ever) >= 99.0% AND worst DD strictly
better than 65.4% AND med days <= 200 AND collapse 0. Anything less =
NO-SHIP." These are ABSOLUTE floors (pre-registered from the tpsl campaign's
own already-known cert numbers for this exact arm -- P(2x)=99.4%,
worst_dd=65.4%, med_days=127.5d, collapse 0%, FINDINGS.md), not a fresh
baseline-relative delta -- the reused baseline row is still loaded/shown for
provenance/sanity, but the finalist verdict never depends on recomputing it.

Reads (all under experiments/alloc_retune_2026_08/out/, globbed and routed
by filename prefix -- see classify_file):
  allocC_<job>.csv                    default (all 12 canonical windows)
  allocC_fillprobe_<job>.csv          battery (a): TP_FILL_MISS_P=0.10 (22-now, 5y)
  allocC_calibrated_<job>.csv         battery (b): TP_FILL_MISS_P=0.15 + GAP_AWARE=1 (22-now, 5y)
  allocC_survivor_<job>.csv           battery (c): MC_UNIVERSE_FILE survivor-only (2022, 22-now)
  allocC_apex2x_<job>.csv             Apex 2x-race harness (one row per arm, baseline + finalists)

Writes out/allocC_summary.md (<=200 lines), one section per profile, one
explicit VERDICT line (SHIP / FLAG / NO-SHIP) per non-baseline finalist cell,
plus the Core two-finalist tiebreak recommendation.

Do NOT run this against real Confirm output from this authoring session --
only unit-tested here (test_analyze_allocC.py) against synthetic rows; the
orchestrator runs it for real once the queued jobs land.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import defaultdict

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(_THIS_DIR), 'out')
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Locked constants -- single source of truth, allocC_run.py (module import
# only: constants + cheap self-asserts, no DB/mc touched -- same reuse
# pattern test_analyze_allocA.py/test_analyze_allocB.py already use).
from allocC_run import (                                                     # noqa: E402
    CORE_CELLS, APEX_CELLS, PHASE_D_WINDOWS_12, FILL_PROBE_WINDOWS,
    CALIBRATED_WINDOWS, SURVIVOR_WINDOWS,
)
from allocA_run import CORE_BASELINE, APEX_BASELINE                          # noqa: E402

ANNUAL_WINDOWS = ('2021', '2022', '2023', '2024', '2025')
ANNUAL_REGRESSION_LIMIT_PP = 5.0                  # TASK.md "no {2021..2025} window regresses >5pp"
CRASH_FAMILY_WINDOWS = ('2018', '2020', '2020_crash', '10y')
CRASH_FAMILY_REGRESSION_LIMIT_PP = 3.0            # TASK.md "crash-family... regression <=3pp each"
DD_NOISE_FLOOR_PP = 1.5                           # inherited tpsl PREREG section 3, N=500 DD floor

# Apex absolute floors, TASK.md verbatim (see module docstring).
APEX_P2X_FLOOR_PCT = 99.0
APEX_WORST_DD_CEIL_PCT = 65.4     # strict less-than ("strictly better than")
APEX_MED_DAYS_CEIL = 200.0

TOPN_TABLE = 12


def fnum(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def core_cell_name(shape, max_pos, gross):
    return f"{shape}_mp{max_pos}_g{gross:.2f}"


def apex_cell_name(n, frac):
    return f"n{n}_f{frac:.3f}"


# ---------------------------------------------------------------------------
# File discovery + routing -- order matters: more specific prefixes checked
# before the generic 'allocC_' catch-all (e.g. 'allocC_fillprobe_X.csv' also
# starts with 'allocC_').
# ---------------------------------------------------------------------------
_FILE_KINDS = [
    ('apex2x', 'allocC_apex2x_'),
    ('fillprobe', 'allocC_fillprobe_'),
    ('calibrated', 'allocC_calibrated_'),
    ('survivor', 'allocC_survivor_'),
    ('default', 'allocC_'),
]


def classify_file(basename):
    for kind, prefix in _FILE_KINDS:
        if basename.startswith(prefix):
            return kind
    return None


def load_all(out_dir=OUT_DIR, include_smoke=False):
    buckets = defaultdict(list)
    paths_by_kind = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(out_dir, 'allocC_*.csv'))):
        base = os.path.basename(p)
        if not include_smoke and 'smoke' in base.lower():
            continue
        kind = classify_file(base)
        if kind is None:
            continue
        with open(p, newline='', encoding='utf-8') as f:
            buckets[kind].extend(csv.DictReader(f))
        paths_by_kind[kind].append(p)
    return buckets, paths_by_kind


def merge_mc_cells(rows):
    """(profile, cell_name) -> {'windows': {label: row}}. Repeated rows
    (restartable-job re-run) resolve to the LAST occurrence, same convention
    as analyze_allocA.py/analyze_allocB.py/analyze_phaseD.py."""
    cells = defaultdict(lambda: {'windows': {}})
    for r in rows:
        key = (r['profile'], r['cell_name'])
        cells[key]['windows'][r['window']] = r
    return cells


def merge_apex2x_rows(rows):
    """(n, frac) -> row (one row per arm; allocC_apex2x.py has no per-window
    granularity in its CSV -- it's already pooled). Last occurrence wins."""
    out = {}
    for r in rows:
        key = (int(float(r['n'])), round(float(r['frac']), 4))
        out[key] = r
    return out


# ---------------------------------------------------------------------------
# Shared window accessors
# ---------------------------------------------------------------------------
def dd(c, window):
    row = c['windows'].get(window)
    return fnum(row['worst_dd']) if row else None


def med(c, window):
    row = c['windows'].get(window)
    return fnum(row['med_ret']) if row else None


def pcoll(c, window):
    row = c['windows'].get(window)
    return fnum(row['p_coll']) if row else None


def collapse_clean(cell, windows=PHASE_D_WINDOWS_12):
    """(clean: bool, bad_windows: list[str]) -- clean requires a row for
    EVERY window AND p_coll==0.0 on every one."""
    bad = []
    for w in windows:
        p = pcoll(cell, w)
        if p is None or p > 0.0:
            bad.append(w)
    return (len(bad) == 0), bad


# ---------------------------------------------------------------------------
# CORE gate check (TASK.md's single-lane formula -- dd_5y/dd_22now/med_5y/
# annual-regression, no growth-lane alternative unlike the inherited tpsl
# lanes) + the NEW crash-family tolerance check.
# ---------------------------------------------------------------------------
def annual_regression_ok(cell, baseline, limit_pp=ANNUAL_REGRESSION_LIMIT_PP):
    for w in ANNUAL_WINDOWS:
        c_dd, b_dd = dd(cell, w), dd(baseline, w)
        if c_dd is None or b_dd is None:
            return False, w
        if c_dd - b_dd > limit_pp:
            return False, w
    return True, None


def core_gate_check(cell, baseline):
    c_dd5y, c_dd22now, c_med5y = dd(cell, '5y'), dd(cell, '22-now'), med(cell, '5y')
    b_dd5y, b_dd22now, b_med5y = dd(baseline, '5y'), dd(baseline, '22-now'), med(baseline, '5y')
    if None in (c_dd5y, c_dd22now, c_med5y, b_dd5y, b_dd22now, b_med5y):
        return {'gate_pass': False, 'reg_ok': False, 'reg_fail_window': None, 'incomplete': True,
               'd_dd5y': None, 'd_dd22now': None, 'ratio_med5y': None}
    reg_ok, reg_fail_window = annual_regression_ok(cell, baseline)
    gate_pass = (c_dd5y <= b_dd5y - 1.5 and c_dd22now <= b_dd22now + 0.5
                and c_med5y >= 0.75 * b_med5y and reg_ok)
    ratio = (c_med5y / b_med5y) if b_med5y not in (0, None) else None
    return {'gate_pass': gate_pass, 'reg_ok': reg_ok, 'reg_fail_window': reg_fail_window,
           'incomplete': False, 'd_dd5y': c_dd5y - b_dd5y, 'd_dd22now': c_dd22now - b_dd22now,
           'ratio_med5y': ratio}


def crash_family_check(cell, baseline, limit_pp=CRASH_FAMILY_REGRESSION_LIMIT_PP):
    """(flagged: bool, detail: {window: regression_pp_float | 'missing'}).
    regression = entrant_dd - baseline_dd (positive = worse). Flags if ANY
    crash-family window regresses more than limit_pp, OR data is missing for
    any of them (never silently pass on incomplete data -- in practice a
    missing crash-family window already implies collapse_clean() is False
    for this cell, which gates the verdict to NO-SHIP before this detail is
    ever consulted; this function still reports the flag honestly either
    way)."""
    detail = {}
    flagged = False
    for w in CRASH_FAMILY_WINDOWS:
        c_dd, b_dd = dd(cell, w), dd(baseline, w)
        if c_dd is None or b_dd is None:
            detail[w] = 'missing'
            flagged = True
            continue
        reg = c_dd - b_dd
        detail[w] = reg
        if reg > limit_pp:
            flagged = True
    return flagged, detail


# ---------------------------------------------------------------------------
# Generic edge-inversion check -- ONE function serves all three Core
# batteries (fill-probe, calibrated, survivor-only contrast): a finalist's
# real default-arm DD edge over baseline (baseline_dd - finalist_dd, positive
# = finalist improves DD) must not go from > DD_NOISE_FLOOR_PP in the default
# arm to <=0 under the battery, on ANY of the battery's windows. Missing
# battery data for a cell with a real default-arm edge is treated as
# unconfirmed (flag-worthy), never silently passed -- ported from
# tpsl_refine_2026_08/driver/analyze_phaseD.py's fillprobe_inversion_check/
# survivor_only_contrast_check (which had two near-identical copies; alloc's
# 3-battery Confirm collapses them into one generic function).
# ---------------------------------------------------------------------------
def edge_inversion_check(default_cells, battery_cells, key, base_key, windows, floor_pp=DD_NOISE_FLOOR_PP):
    d_cell, d_base = default_cells.get(key), default_cells.get(base_key)
    b_cell, b_base = battery_cells.get(key), battery_cells.get(base_key)
    detail = {}
    flagged = False
    have_battery_data = b_cell is not None and b_base is not None
    for w in windows:
        d_c, d_b = (dd(d_cell, w), dd(d_base, w)) if d_cell and d_base else (None, None)
        if d_c is None or d_b is None:
            detail[w] = 'default-arm data missing'
            continue
        edge_default = d_b - d_c
        if not have_battery_data:
            detail[w] = {'edge_default': edge_default, 'edge_battery': None}
            if edge_default > floor_pp:
                flagged = True
            continue
        b_c, b_b = dd(b_cell, w), dd(b_base, w)
        if b_c is None or b_b is None:
            detail[w] = {'edge_default': edge_default, 'edge_battery': 'missing'}
            if edge_default > floor_pp:
                flagged = True
            continue
        edge_battery = b_b - b_c
        detail[w] = {'edge_default': edge_default, 'edge_battery': edge_battery}
        if edge_default > floor_pp and edge_battery <= 0:
            flagged = True
    return flagged, detail


def survivor_only_collapse_check(survivor_cells, key, windows=SURVIVOR_WINDOWS):
    """A DIFFERENT failure mode than edge-evaporation: the finalist's
    DEFAULT-arm collapse rate is clean (0 on all 12 windows) while its
    SURVIVOR-ONLY arm collapses on 2022 and/or 22-now -- the candidate only
    survives because of delisted-name behavior specifically. Missing
    survivor-only data is NOT itself a collapse flag here (caught by
    evidence_incomplete upstream) -- this fires only on data it actually
    has. Ported from analyze_phaseD.py's survivor_only_collapse_check."""
    cell = survivor_cells.get(key)
    if cell is None:
        return False, []
    bad = [w for w in windows if pcoll(cell, w) is not None and pcoll(cell, w) > 0.0]
    return (len(bad) > 0), bad


# ---------------------------------------------------------------------------
# CORE verdict + two-finalist tiebreak
# ---------------------------------------------------------------------------
def render_core_verdict(collapse_ok, gate, crash_flagged, fp_flagged, cal_flagged,
                        sv_flagged, sv_coll_flagged, evidence_incomplete):
    if not collapse_ok:
        return 'NO-SHIP', 'collapse>0 on at least one of the 12 canonical windows (hard requirement)'
    if sv_coll_flagged:
        return 'FLAG', ('collapse>0 in the survivor-only arm despite a clean default arm -- '
                        'candidate may depend on delisted-name behavior to avoid collapsing')
    if gate['incomplete']:
        return 'FLAG', 'primary gate data incomplete (missing 5y/22-now/annual window rows) -- cannot confirm'
    if not gate['gate_pass']:
        return 'NO-SHIP', 'did not clear the dd_5y/dd_22now/med_5y/annual-regression gate vs the in-phase baseline'
    reasons = []
    if evidence_incomplete:
        reasons.append('required extra evidence missing (fill-probe/calibrated/survivor-only)')
    if crash_flagged:
        reasons.append(f'crash-family regression >{CRASH_FAMILY_REGRESSION_LIMIT_PP}pp on '
                       f'>=1 of {CRASH_FAMILY_WINDOWS}')
    if fp_flagged:
        reasons.append('fill-probe (TP_FILL_MISS_P=0.10) inversion')
    if cal_flagged:
        reasons.append('calibrated (0.15+GAP_AWARE) inversion')
    if sv_flagged:
        reasons.append('survivor-only edge evaporates (delisted-name-dependent)')
    if reasons:
        return 'FLAG', '; '.join(reasons)
    return 'SHIP', 'clean gate pass + crash-family within tolerance + probes/survivor green'


def core_tiebreak(crash_flags):
    """TASK.md's locked two-finalist tiebreak, verbatim: 'higher-gross
    variant wins only if its crash-family reads are within the 3pp
    tolerance; else the g0.50 variant.' crash_flags: {cell_name: bool}
    (True = crash-family flagged / outside tolerance). Returns
    (winner_cell_name, reason)."""
    g050_name = core_cell_name('S9', 20, 0.50)
    g065_name = core_cell_name('S9', 20, 0.65)
    if not crash_flags.get(g065_name, True):
        return g065_name, 'higher-gross (g0.65) crash-family reads are within the 3pp tolerance'
    return g050_name, 'g0.65 crash-family tolerance exceeded (or data missing) -> defaults to g0.50'


# ---------------------------------------------------------------------------
# APEX 2x-race acceptance (absolute floors vs the reused tpsl baseline arm)
# ---------------------------------------------------------------------------
def apex2x_acceptance_check(arm_row):
    if arm_row is None:
        return {'accepted': False, 'incomplete': True, 'p2x': None, 'worst_dd': None,
               'med_days': None, 'p_coll_pct': None}
    p2x_frac = fnum(arm_row.get('p_2x_ever'))
    p2x = p2x_frac * 100.0 if p2x_frac is not None else None
    worst_dd = fnum(arm_row.get('worst_dd_pct'))
    med_days = fnum(arm_row.get('median_days_2x'))
    p_coll_frac = fnum(arm_row.get('p_collapse'))
    p_coll_pct = p_coll_frac * 100.0 if p_coll_frac is not None else None
    if None in (p2x, worst_dd, med_days, p_coll_pct):
        return {'accepted': False, 'incomplete': True, 'p2x': p2x, 'worst_dd': worst_dd,
               'med_days': med_days, 'p_coll_pct': p_coll_pct}
    accepted = (p2x >= APEX_P2X_FLOOR_PCT and worst_dd < APEX_WORST_DD_CEIL_PCT
               and med_days <= APEX_MED_DAYS_CEIL and p_coll_pct == 0.0)
    return {'accepted': accepted, 'incomplete': False, 'p2x': p2x, 'worst_dd': worst_dd,
           'med_days': med_days, 'p_coll_pct': p_coll_pct}


def render_apex_verdict(acc):
    if acc['incomplete']:
        return 'FLAG', '2x-race data incomplete (missing arm row) -- cannot confirm'
    if acc['accepted']:
        return 'SHIP', 'P(2x)>=99.0% AND worst_dd<65.4% AND med_days<=200 AND collapse 0 all clear'
    return 'NO-SHIP', 'did not clear one or more of P(2x)>=99.0% / worst_dd<65.4% / med_days<=200 / collapse 0'


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def fmt_dd_abs(v):
    return '' if v is None else f"{v:.1f}%"


def fmt_delta_pp(v):
    return '' if v is None else f"{v:+.1f}pp"


def fmt_pct1(v):
    return '' if v is None else f"{v:.1f}%"


def fmt_days(v):
    return '' if v is None else f"{v:.0f}d"


def fmt_crash_detail(detail):
    parts = []
    for w in CRASH_FAMILY_WINDOWS:
        v = detail.get(w)
        parts.append(f"{v:+.1f}" if isinstance(v, (int, float)) else str(v))
    return '/'.join(parts)


def analyze_core(f, default_cells, fillprobe_cells, calibrated_cells, survivor_cells):
    profile = 'core'
    base_name = core_cell_name(*CORE_BASELINE)
    base_key = (profile, base_name)
    f.write("\n## Profile: core\n\n")
    if base_key not in default_cells:
        f.write("**BASELINE (S0/mp14/g0.50) missing from default-mode rows -- CORE analysis SKIPPED.**\n")
        return
    baseline = default_cells[base_key]
    base_clean, base_bad = collapse_clean(baseline)
    f.write(f"Baseline collapse-clean: {base_clean}" + (f" (bad={base_bad})" if not base_clean else "") + "\n")

    finalist_names = [core_cell_name(s, mp, g) for (s, mp, g, is_b) in CORE_CELLS if not is_b]
    f.write(f"\nFinalist cells: {len(finalist_names)}\n\n")
    f.write("| cell | collapse_ok | d_dd5y | d_dd22now | med5y ratio | reg_ok | "
           "crash-family (2018/2020/2020_crash/10y,pp) | fillprobe | calibrated | survivor | VERDICT | reason |\n")
    f.write("|---|---|---|---|---|---|---|---|---|---|---|---|\n")

    crash_flags, verdicts = {}, {}
    for name in finalist_names:
        key = (profile, name)
        cell = default_cells.get(key)
        if cell is None:
            f.write(f"| {name} | -- | | | | -- | -- | -- | -- | -- | **FLAG** | "
                   f"cell missing from default-mode rows entirely -- cannot confirm |\n")
            crash_flags[name] = True
            verdicts[name] = 'FLAG'
            continue
        c_clean, c_bad = collapse_clean(cell)
        gate = core_gate_check(cell, baseline)
        crash_flagged, crash_detail = crash_family_check(cell, baseline)
        crash_flags[name] = crash_flagged
        fp_flagged, _ = edge_inversion_check(default_cells, fillprobe_cells, key, base_key, FILL_PROBE_WINDOWS)
        cal_flagged, _ = edge_inversion_check(default_cells, calibrated_cells, key, base_key, CALIBRATED_WINDOWS)
        sv_flagged, _ = edge_inversion_check(default_cells, survivor_cells, key, base_key, SURVIVOR_WINDOWS)
        sv_coll_flagged, _ = survivor_only_collapse_check(survivor_cells, key)
        evidence_incomplete = (key not in fillprobe_cells or base_key not in fillprobe_cells
                              or key not in calibrated_cells or base_key not in calibrated_cells
                              or key not in survivor_cells or base_key not in survivor_cells)
        verdict, reason = render_core_verdict(c_clean, gate, crash_flagged, fp_flagged, cal_flagged,
                                              sv_flagged, sv_coll_flagged, evidence_incomplete)
        verdicts[name] = verdict
        ratio_str = '' if gate['ratio_med5y'] is None else f"{gate['ratio_med5y']:.2f}x"
        f.write(f"| {name} | {c_clean} | {fmt_delta_pp(gate['d_dd5y'])} | {fmt_delta_pp(gate['d_dd22now'])} | "
               f"{ratio_str} | {gate['reg_ok']} | {fmt_crash_detail(crash_detail)} | "
               f"{'FLAG' if fp_flagged else 'ok'} | {'FLAG' if cal_flagged else 'ok'} | "
               f"{'FLAG' if sv_flagged else 'ok'} | **{verdict}** | {reason} |\n")

    if len(finalist_names) == 2:
        winner, why = core_tiebreak(crash_flags)
        f.write(f"\n**Tiebreak recommendation** (TASK.md locked rule): **{winner}** -- {why}. "
               f"Finalist verdicts: {', '.join(f'{n}={verdicts.get(n)}' for n in finalist_names)}.\n")

    f.write(f"\n### Per-window worst_dd vs baseline (core, all {len(PHASE_D_WINDOWS_12)} canonical windows)\n\n")
    f.write("| cell | " + " | ".join(PHASE_D_WINDOWS_12) + " |\n")
    f.write("|---|" + "---|" * len(PHASE_D_WINDOWS_12) + "\n")
    for name, key in [(base_name, base_key)] + [(n, (profile, n)) for n in finalist_names]:
        cell = default_cells.get(key)
        tag = name + (' (BASE)' if key == base_key else '')
        if cell is None:
            f.write(f"| {tag} | " + " | ".join(['--'] * len(PHASE_D_WINDOWS_12)) + " |\n")
            continue
        vals = " | ".join(fmt_dd_abs(dd(cell, w)) for w in PHASE_D_WINDOWS_12)
        f.write(f"| {tag} | {vals} |\n")


def analyze_apex(f, default_cells, apex2x_rows):
    profile = 'apex'
    base_n, base_frac = APEX_BASELINE
    base_key = (profile, apex_cell_name(base_n, base_frac))
    f.write("\n## Profile: apex\n\n")
    f.write("Primary gate is the 2x-race harness (allocC_apex2x.py), NOT the flat 12-window MC table "
           "below (informational only -- collapse/DD sanity; TASK.md: Apex ships ONLY on the "
           "2x-race's absolute floors).\n")

    finalist_ns = [(n, frac) for (n, frac, is_b) in APEX_CELLS if not is_b]
    base_row = apex2x_rows.get((base_n, base_frac))
    f.write("\n| arm | P(2x) | worst_dd | med_days | p_coll | VERDICT | reason |\n")
    f.write("|---|---|---|---|---|---|---|\n")
    if base_row is not None:
        b_acc = apex2x_acceptance_check(base_row)
        f.write(f"| {apex_cell_name(base_n, base_frac)} (BASELINE, disk-reused tpsl #379 arm) | "
               f"{fmt_pct1(b_acc['p2x'])} | {fmt_pct1(b_acc['worst_dd'])} | {fmt_days(b_acc['med_days'])} | "
               f"{fmt_pct1(b_acc['p_coll_pct'])} | -- | reference only, not a candidate |\n")
    else:
        f.write("| (baseline arm missing from allocC_apex2x_*.csv) | | | | | -- | cannot even sanity-check |\n")

    for (n, frac) in finalist_ns:
        tag = apex_cell_name(n, frac)
        acc = apex2x_acceptance_check(apex2x_rows.get((n, frac)))
        verdict, reason = render_apex_verdict(acc)
        f.write(f"| {tag} | {fmt_pct1(acc['p2x'])} | {fmt_pct1(acc['worst_dd'])} | "
               f"{fmt_days(acc['med_days'])} | {fmt_pct1(acc['p_coll_pct'])} | **{verdict}** | {reason} |\n")

    if base_key in default_cells:
        f.write(f"\n### Per-window worst_dd, flat 12-window MC table (apex, informational only)\n\n")
        f.write("| cell | " + " | ".join(PHASE_D_WINDOWS_12) + " |\n")
        f.write("|---|" + "---|" * len(PHASE_D_WINDOWS_12) + "\n")
        shown = [(apex_cell_name(base_n, base_frac), base_key)] + \
            [(apex_cell_name(n, frac), (profile, apex_cell_name(n, frac))) for (n, frac) in finalist_ns]
        for name, key in shown:
            cell = default_cells.get(key)
            tag = name + (' (BASE)' if key == base_key else '')
            if cell is None:
                f.write(f"| {tag} | " + " | ".join(['--'] * len(PHASE_D_WINDOWS_12)) + " |\n")
                continue
            vals = " | ".join(fmt_dd_abs(dd(cell, w)) for w in PHASE_D_WINDOWS_12)
            f.write(f"| {tag} | {vals} |\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--include-smoke', action='store_true')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    out_path = args.out or os.path.join(OUT_DIR, 'allocC_summary.md')
    buckets, paths_by_kind = load_all(OUT_DIR, include_smoke=args.include_smoke)

    if not buckets.get('default'):
        raise SystemExit(f"no allocC_*.csv default-mode rows found under {OUT_DIR}")

    default_cells = merge_mc_cells(buckets.get('default', []))
    fillprobe_cells = merge_mc_cells(buckets.get('fillprobe', []))
    calibrated_cells = merge_mc_cells(buckets.get('calibrated', []))
    survivor_cells = merge_mc_cells(buckets.get('survivor', []))
    apex2x_rows = merge_apex2x_rows(buckets.get('apex2x', []))

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# Confirm summary -- formal read + SHIP/FLAG/NO-SHIP verdict (TASK.md locked confirm rules)\n\n")
        for kind in ('default', 'fillprobe', 'calibrated', 'survivor', 'apex2x'):
            ps = paths_by_kind.get(kind, [])
            f.write(f"- {kind}: {len(ps)} file(s)" +
                   (f" ({', '.join(os.path.basename(p) for p in ps)})" if ps else " (NONE)") + "\n")
        f.write(f"\nCore gate: dd_5y<=base-1.5pp AND dd_22now<=base+0.5pp AND med_5y>=0.75x base AND no "
               f"{{2021..2025}} window regresses >{ANNUAL_REGRESSION_LIMIT_PP}pp AND collapse 0/12 AND "
               f"crash-family regression <={CRASH_FAMILY_REGRESSION_LIMIT_PP}pp each AND "
               f"fill-probe/calibrated non-inverting AND survivor-robust.\n")
        f.write(f"Apex gate (absolute floors vs the reused tpsl baseline arm): "
               f"P(2x ever)>={APEX_P2X_FLOOR_PCT}% AND worst_dd<{APEX_WORST_DD_CEIL_PCT}% (strict) AND "
               f"med_days<={APEX_MED_DAYS_CEIL} AND collapse 0.\n")
        f.write(f"Noise floor (inherited tpsl PREREG section 3): DD-edge claims need "
               f">={DD_NOISE_FLOOR_PP}pp to be non-noise for the fill-probe/calibrated/survivor checks.\n")

        analyze_core(f, default_cells, fillprobe_cells, calibrated_cells, survivor_cells)
        analyze_apex(f, default_cells, apex2x_rows)

    n_lines = sum(1 for _ in open(out_path, encoding='utf-8'))
    print(f"[WROTE] {out_path} ({n_lines} lines)")
    if n_lines > 200:
        print(f"[WARN] output is {n_lines} lines, over the 200-line budget", file=sys.stderr)


if __name__ == '__main__':
    main()
