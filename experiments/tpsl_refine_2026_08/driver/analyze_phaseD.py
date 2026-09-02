#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_phaseD.py -- the formal Phase D read (PREREG.md section 6). MECHANICAL
evaluation: every rule below is either copied verbatim from PREREG.md section 3
(lanes, noise floors, Apex acceptance) or section 6 (the extra Phase D
evidence + ship rule). No judgment calls made here -- ambiguous/incomplete
evidence renders FLAG (present, don't ship), never a silent SHIP.

Reads (all under experiments/tpsl_refine_2026_08/out/, globbed and routed by
filename prefix -- see classify_file):
  phaseD_<job>.csv                    default (flat, N=500, up to 12 windows)
  phaseD_fillprobe_<job>.csv          TP_FILL_MISS_P=0.10 probe (22-now, 5y)
  phaseD_survivor_<job>.csv           MC_UNIVERSE_FILE survivor-only contrast (2022, 22-now)
  phaseD_apex2x_<job>.csv             Apex 2x-race harness (one row per arm)
  phaseD_cascade_parity_<job>.csv     backtest_cascade deterministic replay

Writes out/phaseD_summary.md (<=200 lines), one section per profile, one
verdict line (SHIP / FLAG / NO-SHIP) per non-baseline finalist cell.

Verdict logic (per finalist cell, both profiles):
  1. collapse>0 on ANY of the (up to) 12 default-mode windows -> NO-SHIP
     (hard requirement, PREREG section 3 "Always: collapse=0 on every window
     incl 2020_crash", section 1 Apex "collapse 0/12").
  2. CORE: neither LANE-DD nor LANE-GROWTH entered (PREREG section 3 exact
     thresholds, evaluated against the IN-PHASE Phase D baseline, never a
     remembered cert) -> NO-SHIP.
     APEX: 2x-race floors (P(2x)>=72%, worst DD<=76%, med days<=200,
     collapse 0/12) not ALL met, OR met but zero of the three strictly beats
     the live cert (72%/76%/191d) -- a "tie" per PREREG section 3's own
     wording -- -> NO-SHIP.
  3. Required extra evidence (fill-probe differential, survivor-only
     contrast, cascade-parity direction) MISSING for a cell that otherwise
     cleared step 2 -> FLAG (cannot confirm required evidence is green;
     incomplete evidence never defaults to SHIP).
  4. Fill-probe inversion (edge > noise floor in the default arm, gone/
     inverted under the probe) OR survivor-only evaporation (edge > noise
     floor by default, gone/inverted survivor-only -- edge is delisted-name-
     dependent, LESSONS.md survivorship-arm-semantics) OR cascade-parity
     direction disagreement -> FLAG.
  5. Otherwise -> SHIP.

Do NOT run this against real Phase D output from this authoring session --
only unit-tested here (test_phaseD.py) against synthetic rows; the
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

# Locked constants -- single source of truth, phaseD_run.py (module import
# only: constants + cheap self-asserts, no DB/mc touched).
from phaseD_run import (   # noqa: E402
    PHASE_D_WINDOWS_12, FILL_PROBE_WINDOWS, SURVIVOR_WINDOWS, BASELINE_CELL,
)

ANNUAL_WINDOWS = ('2021', '2022', '2023', '2024', '2025')
ANNUAL_REGRESSION_LIMIT_PP = 5.0          # PREREG section 3 (T5)
DD_NOISE_FLOOR_PP = 1.5                   # PREREG section 3 -- "DD claims need >=1.5pp at N=500"
COMPOUND_NOISE_FLOOR_PCT = 30.0           # PREREG section 3 -- "compound claims need >=30%"

# Apex acceptance, PREREG section 3 + the live cert PREREG section 1 cites.
APEX_P2X_FLOOR_PCT = 72.0
APEX_WORST_DD_CEIL_PCT = 76.0
APEX_MED_DAYS_CEIL = 200.0
CERT_P2X_PCT, CERT_WORST_DD_PCT, CERT_MED_DAYS = 72.0, 76.0, 191.0

TOPN_WINDOWS_SHOWN = 12   # Phase D has far fewer cells than Phase B -- full window table always fits budget


def fnum(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fbool(v):
    if isinstance(v, bool):
        return v
    if v in (None, ''):
        return None
    return str(v).strip().lower() in ('1', 'true', 'yes', 'y')


# ---------------------------------------------------------------------------
# File discovery + routing
# ---------------------------------------------------------------------------
# Order matters: more specific prefixes checked before the generic 'phaseD_'
# catch-all, since e.g. 'phaseD_fillprobe_X.csv' also starts with 'phaseD_'.
_FILE_KINDS = [
    ('cascade_parity', 'phaseD_cascade_parity_'),
    ('apex2x', 'phaseD_apex2x_'),
    ('fillprobe', 'phaseD_fillprobe_'),
    ('survivor', 'phaseD_survivor_'),
    ('default', 'phaseD_'),
]


def classify_file(basename):
    for kind, prefix in _FILE_KINDS:
        if basename.startswith(prefix):
            return kind
    return None


def load_all(out_dir=OUT_DIR, include_smoke=False):
    """Glob phaseD_*.csv under out_dir, route each file by classify_file(),
    return a dict of kind -> list[dict rows]. 'summary.md'/non-csv and
    unroutable files are ignored."""
    buckets = defaultdict(list)
    paths_by_kind = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(out_dir, 'phaseD_*.csv'))):
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
    """(profile, tp, sl) -> {'windows': {label: row}} for phaseD_run.py-shaped
    rows (default/fillprobe/survivor -- all share CSV_FIELDS). Repeated rows
    (restartable-job re-run) resolve to the LAST occurrence, same convention
    as analyze_phaseA/B.py."""
    cells = defaultdict(lambda: {'windows': {}})
    for r in rows:
        key = (r['profile'], fnum(r['tp']), fnum(r['sl']))
        cells[key]['windows'][r['window']] = r
    return cells


def merge_apex2x_rows(rows):
    """(tp, sl) -> row (one row per arm; phaseD_apex2x.py has no per-window
    granularity in its CSV -- it's already pooled)."""
    out = {}
    for r in rows:
        key = (fnum(r['tp']), fnum(r['sl']))
        out[key] = r   # last occurrence wins if the job was re-run
    return out


def merge_parity_rows(rows):
    """(profile, tp, sl) -> row (last occurrence wins)."""
    out = {}
    for r in rows:
        key = (r['profile'], fnum(r['tp']), fnum(r['sl']))
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
    EVERY window in `windows` AND p_coll==0.0 on every one."""
    bad = []
    for w in windows:
        p = pcoll(cell, w)
        if p is None or p > 0.0:
            bad.append(w)
    return (len(bad) == 0), bad


# ---------------------------------------------------------------------------
# CORE lane check (PREREG section 3, re-applied at Phase D's own in-phase
# baseline -- never a remembered cert number)
# ---------------------------------------------------------------------------
def annual_regression_ok(cell, baseline, limit_pp=ANNUAL_REGRESSION_LIMIT_PP):
    for w in ANNUAL_WINDOWS:
        c_dd, b_dd = dd(cell, w), dd(baseline, w)
        if c_dd is None or b_dd is None:
            return False, w
        if c_dd - b_dd > limit_pp:
            return False, w
    return True, None


def core_lane_check(cell, baseline):
    c_dd5y, c_dd22now, c_med5y = dd(cell, '5y'), dd(cell, '22-now'), med(cell, '5y')
    b_dd5y, b_dd22now, b_med5y = dd(baseline, '5y'), dd(baseline, '22-now'), med(baseline, '5y')
    if None in (c_dd5y, c_dd22now, c_med5y, b_dd5y, b_dd22now, b_med5y):
        return {'in_dd_lane': False, 'in_growth_lane': False, 'reg_ok': False,
                'reg_fail_window': None, 'incomplete': True,
                'd_dd5y': None, 'd_dd22now': None, 'ratio_med5y': None}
    reg_ok, reg_fail_window = annual_regression_ok(cell, baseline)
    in_dd = (c_dd5y <= b_dd5y - 1.5 and c_dd22now <= b_dd22now + 0.5
            and c_med5y >= 0.75 * b_med5y and reg_ok)
    in_growth = (c_med5y >= 1.5 * b_med5y and c_dd5y <= b_dd5y + 0.5
                and c_dd22now <= b_dd22now + 1.0 and reg_ok)
    ratio = (c_med5y / b_med5y) if b_med5y not in (0, None) else None
    return {'in_dd_lane': in_dd, 'in_growth_lane': in_growth, 'reg_ok': reg_ok,
           'reg_fail_window': reg_fail_window, 'incomplete': False,
           'd_dd5y': c_dd5y - b_dd5y, 'd_dd22now': c_dd22now - b_dd22now,
           'ratio_med5y': ratio}


# ---------------------------------------------------------------------------
# APEX acceptance check (PREREG section 3, on the 2x-race harness -- phaseD_
# apex2x.py's CSV, NOT the flat-confirm MC rows)
# ---------------------------------------------------------------------------
def apex_acceptance_check(arm_row):
    if arm_row is None:
        return {'accepted': False, 'incomplete': True, 'p2x': None, 'worst_dd': None,
               'med_days': None, 'p_coll_pct': None, 'n_strictly_better': 0}
    p2x_frac = fnum(arm_row.get('p_2x_ever'))
    p2x = p2x_frac * 100.0 if p2x_frac is not None else None
    worst_dd = fnum(arm_row.get('worst_dd_pct'))
    med_days = fnum(arm_row.get('median_days_2x'))
    p_coll_frac = fnum(arm_row.get('p_collapse'))
    p_coll_pct = p_coll_frac * 100.0 if p_coll_frac is not None else None

    if None in (p2x, worst_dd, med_days, p_coll_pct):
        return {'accepted': False, 'incomplete': True, 'p2x': p2x, 'worst_dd': worst_dd,
               'med_days': med_days, 'p_coll_pct': p_coll_pct, 'n_strictly_better': 0}

    floors_ok = (p2x >= APEX_P2X_FLOOR_PCT and worst_dd <= APEX_WORST_DD_CEIL_PCT
                and med_days <= APEX_MED_DAYS_CEIL and p_coll_pct == 0.0)
    n_better = sum([p2x > CERT_P2X_PCT, worst_dd < CERT_WORST_DD_PCT, med_days < CERT_MED_DAYS])
    return {'accepted': floors_ok and n_better >= 1, 'incomplete': False,
           'floors_ok': floors_ok, 'p2x': p2x, 'worst_dd': worst_dd, 'med_days': med_days,
           'p_coll_pct': p_coll_pct, 'n_strictly_better': n_better}


# ---------------------------------------------------------------------------
# Extra Phase D evidence checks (PREREG section 6)
# ---------------------------------------------------------------------------
def fillprobe_inversion_check(default_cells, fillprobe_cells, key, base_key,
                              windows=FILL_PROBE_WINDOWS, floor_pp=DD_NOISE_FLOOR_PP):
    """A finalist's worst_dd edge over baseline (baseline_dd - finalist_dd,
    positive = finalist improves DD) must not go from a REAL improvement
    (> noise floor) in the default arm to a non-improvement (<=0) under the
    TP_FILL_MISS_P=0.10 probe, on EITHER 22-now or 5y. Missing probe data for
    a cell that has a real default-arm edge is treated as unconfirmed (flag-
    worthy), never silently passed."""
    d_cell, d_base = default_cells.get(key), default_cells.get(base_key)
    p_cell, p_base = fillprobe_cells.get(key), fillprobe_cells.get(base_key)
    detail = {}
    flagged = False
    have_probe_data = p_cell is not None and p_base is not None
    for w in windows:
        d_c, d_b = (dd(d_cell, w), dd(d_base, w)) if d_cell and d_base else (None, None)
        if d_c is None or d_b is None:
            detail[w] = 'default-arm data missing'
            continue
        edge_default = d_b - d_c
        if not have_probe_data:
            detail[w] = {'edge_default': edge_default, 'edge_fillprobe': None}
            if edge_default > floor_pp:
                flagged = True   # real default edge but nothing to confirm it against
            continue
        p_c, p_b = dd(p_cell, w), dd(p_base, w)
        if p_c is None or p_b is None:
            detail[w] = {'edge_default': edge_default, 'edge_fillprobe': 'missing'}
            if edge_default > floor_pp:
                flagged = True
            continue
        edge_probe = p_b - p_c
        detail[w] = {'edge_default': edge_default, 'edge_fillprobe': edge_probe}
        if edge_default > floor_pp and edge_probe <= 0:
            flagged = True
    return flagged, detail


def survivor_only_contrast_check(default_cells, survivor_cells, key, base_key,
                                 windows=SURVIVOR_WINDOWS, floor_pp=DD_NOISE_FLOOR_PP):
    """Mirror of fillprobe_inversion_check against the survivor-only universe
    (see LESSONS.md survivorship-arm-semantics: this is a CONTRAST arm, not
    the honest one -- the honest arm is the default/no-filter run). Flag if a
    real default-arm DD edge is gone/inverted (< noise floor) once delisted
    names are excluded."""
    d_cell, d_base = default_cells.get(key), default_cells.get(base_key)
    s_cell, s_base = survivor_cells.get(key), survivor_cells.get(base_key)
    detail = {}
    flagged = False
    have_surv_data = s_cell is not None and s_base is not None
    for w in windows:
        d_c, d_b = (dd(d_cell, w), dd(d_base, w)) if d_cell and d_base else (None, None)
        if d_c is None or d_b is None:
            detail[w] = 'default-arm data missing'
            continue
        edge_default = d_b - d_c
        if not have_surv_data:
            detail[w] = {'edge_default': edge_default, 'edge_survivor_only': None}
            if edge_default > floor_pp:
                flagged = True
            continue
        s_c, s_b = dd(s_cell, w), dd(s_base, w)
        if s_c is None or s_b is None:
            detail[w] = {'edge_default': edge_default, 'edge_survivor_only': 'missing'}
            if edge_default > floor_pp:
                flagged = True
            continue
        edge_surv = s_b - s_c
        detail[w] = {'edge_default': edge_default, 'edge_survivor_only': edge_surv}
        if edge_default > floor_pp and edge_surv < floor_pp:
            flagged = True
    return flagged, detail


def survivor_only_collapse_check(survivor_cells, key, windows=SURVIVOR_WINDOWS):
    """A DIFFERENT failure mode than the edge-evaporation contrast above: this
    finalist's DEFAULT-arm collapse rate may be clean (0 on all 12 windows)
    while its SURVIVOR-ONLY arm collapses on 2022 and/or 22-now -- i.e. the
    candidate only survives because of how it behaves on delisted names
    specifically (diversification, or a barrier config that happens to catch
    a dying stock's crash favorably), and blows up once restricted to the
    survivor-only counterfactual. Returns (flagged: bool, bad_windows: list).
    Missing survivor-only data for this cell is NOT itself a collapse flag
    here (that incompleteness is caught by evidence_incomplete upstream) --
    this function only fires on data it actually has."""
    cell = survivor_cells.get(key)
    if cell is None:
        return False, []
    bad = [w for w in windows if pcoll(cell, w) is not None and pcoll(cell, w) > 0.0]
    return (len(bad) > 0), bad


def cascade_parity_direction_check(parity_rows, profile, tp, sl):
    """Flag if the deterministic replay's DD or return delta vs baseline has
    the OPPOSITE SIGN of the default-mode MC 5y read (passed in by the
    caller via mc_d_dd5y/mc_d_med5y on the returned dict -- this function
    only extracts the parity-side numbers; the caller does the sign
    comparison, since it already has the MC-side deltas from core_lane_check/
    apex numbers). Returns the parity row's own deltas vs its baseline row,
    or None if either row is missing (caller treats missing as 'unconfirmed')."""
    cell_row = parity_rows.get((profile, tp, sl))
    base_row = parity_rows.get((profile, BASELINE_CELL[0], BASELINE_CELL[1]))
    if cell_row is None or base_row is None:
        return None
    d_dd = fnum(cell_row.get('max_dd_pct')) - fnum(base_row.get('max_dd_pct'))
    d_ret = fnum(cell_row.get('total_return_pct')) - fnum(base_row.get('total_return_pct'))
    return {'d_max_dd_pct': -d_dd, 'd_total_return_pct': d_ret}   # negate DD delta so positive=finalist-better, matches MC edge sign convention


def sign_disagrees(mc_edge, parity_edge, floor=0.0):
    """True if both values are non-None, non-trivial, and have opposite signs
    (a genuine directional disagreement, not just noise around zero)."""
    if mc_edge is None or parity_edge is None:
        return False
    if abs(mc_edge) <= floor or abs(parity_edge) <= floor:
        return False
    return (mc_edge > 0) != (parity_edge > 0)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
def render_verdict(collapse_ok, gate_pass, gate_incomplete, evidence_incomplete,
                   fillprobe_flag, survivor_flag, parity_flag, survivor_collapse_flag=False,
                   gate_fail_reason='did not clear the primary gate'):
    if not collapse_ok:
        return 'NO-SHIP', 'collapse>0 on at least one canonical window (hard requirement)'
    if survivor_collapse_flag:
        return 'FLAG', ('collapse>0 in the survivor-only arm despite a clean default arm -- '
                        'candidate may depend on delisted-name behavior to avoid collapsing')
    if gate_incomplete:
        return 'FLAG', 'primary gate data incomplete (missing window/arm rows) -- cannot confirm'
    if not gate_pass:
        return 'NO-SHIP', gate_fail_reason
    reasons = []
    if evidence_incomplete:
        reasons.append('required extra evidence missing (fill-probe/survivor-only/parity)')
    if fillprobe_flag:
        reasons.append('fill-probe inversion (edge does not survive TP_FILL_MISS_P=0.10)')
    if survivor_flag:
        reasons.append('survivor-only edge evaporates (delisted-name-dependent)')
    if parity_flag:
        reasons.append('cascade-parity direction disagrees with the MC read')
    if reasons:
        return 'FLAG', '; '.join(reasons)
    return 'SHIP', 'clean lane/acceptance pass + all extra evidence green'


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def fmt_pct(v):
    return '' if v is None else f"{v:+.1f}%"


def fmt_dd(v):
    return '' if v is None else f"{v:.1f}%"


def cell_tag(tp, sl):
    return f"{tp:+.2f}/{sl:+.2f}"


def analyze_core(f, default_cells, fillprobe_cells, survivor_cells, parity_rows):
    profile = 'core'
    keys = [k for k in default_cells if k[0] == profile]
    base_key = (profile, BASELINE_CELL[0], BASELINE_CELL[1])
    f.write(f"\n## Profile: core\n\n")
    if base_key not in default_cells:
        f.write("**BASELINE (0.30/-0.70) missing from default-mode rows -- CORE analysis SKIPPED.**\n")
        return
    baseline = default_cells[base_key]
    base_clean, base_bad = collapse_clean(baseline)
    f.write(f"Baseline collapse-clean: {base_clean}" + (f" (bad={base_bad})" if not base_clean else "") + "\n")

    finalists = [k for k in keys if k != base_key]
    f.write(f"\nFinalist cells: {len(finalists)}\n\n")
    if not finalists:
        f.write("_(no non-baseline cells in default-mode rows -- nothing to confirm; if this is "
               "intentional, it is the STOP-rule CONFIRMED-OPTIMAL path: baseline re-cert only.)_\n")
        return

    f.write("| cell | collapse_ok | d_dd5y(pp) | d_dd22now(pp) | med5y ratio | reg_ok | lane | "
           "apex_na | fillprobe | survivor | parity | VERDICT | reason |\n")
    f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for key in sorted(finalists, key=lambda k: (k[1], k[2])):
        tp, sl = key[1], key[2]
        cell = default_cells[key]
        c_clean, c_bad = collapse_clean(cell)
        lane = core_lane_check(cell, baseline)
        gate_pass = lane['in_dd_lane'] or lane['in_growth_lane']
        fp_flag, fp_detail = fillprobe_inversion_check(default_cells, fillprobe_cells, key, base_key)
        sv_flag, sv_detail = survivor_only_contrast_check(default_cells, survivor_cells, key, base_key)
        sv_coll_flag, sv_coll_bad = survivor_only_collapse_check(survivor_cells, key)
        parity_d = cascade_parity_direction_check(parity_rows, profile, tp, sl)
        # ratio_med5y is a multiplicative ratio (e.g. 0.9 = -10%) -- convert to
        # PERCENTAGE POINTS before comparing against COMPOUND_NOISE_FLOOR_PCT
        # (30.0), which is itself a percentage. Comparing the raw ratio delta
        # (typically -1..+5) against a floor of 30 would never fire -- caught
        # in test_phaseD.py before this went anywhere near real data.
        mc_compound_edge_pct = ((lane['ratio_med5y'] - 1.0) * 100.0) if lane['ratio_med5y'] is not None else None
        parity_flag = (parity_d is not None and (
            sign_disagrees(lane['d_dd5y'] and -lane['d_dd5y'], parity_d['d_max_dd_pct'], DD_NOISE_FLOOR_PP)
            or sign_disagrees(mc_compound_edge_pct, parity_d['d_total_return_pct'], COMPOUND_NOISE_FLOOR_PCT)))
        # Explicit incompleteness signal: no fillprobe/survivor/parity rows at all for this cell
        # (fp_detail/sv_detail being non-None only means SOME window had default-arm data --
        # the per-cell presence check below is the actual gate for "can we confirm required evidence").
        evidence_incomplete = (key not in fillprobe_cells or base_key not in fillprobe_cells
                              or key not in survivor_cells or base_key not in survivor_cells
                              or parity_d is None)
        lane_str = ('DD+GROWTH' if lane['in_dd_lane'] and lane['in_growth_lane']
                   else 'DD' if lane['in_dd_lane'] else 'GROWTH' if lane['in_growth_lane'] else 'none')
        verdict, reason = render_verdict(c_clean, gate_pass, lane.get('incomplete', False),
                                         evidence_incomplete, fp_flag, sv_flag, parity_flag,
                                         survivor_collapse_flag=sv_coll_flag,
                                         gate_fail_reason='did not clear LANE-DD or LANE-GROWTH vs the in-phase baseline')
        ratio_str = '' if lane['ratio_med5y'] is None else f"{lane['ratio_med5y']:.2f}x"
        f.write(f"| {cell_tag(tp, sl)} | {c_clean} | {fmt_dd(lane['d_dd5y'])} | "
               f"{fmt_dd(lane['d_dd22now'])} | "
               f"{ratio_str} | "
               f"{lane['reg_ok']} | {lane_str} | -- | "
               f"{'FLAG' if fp_flag else 'ok'} | {'FLAG' if sv_flag else 'ok'} | "
               f"{'FLAG' if parity_flag else 'ok'} | **{verdict}** | {reason} |\n")

    f.write(f"\n### Per-window worst_dd vs baseline (core, all {len(PHASE_D_WINDOWS_12)} canonical windows)\n\n")
    f.write("| cell | " + " | ".join(PHASE_D_WINDOWS_12) + " |\n")
    f.write("|---|" + "---|" * len(PHASE_D_WINDOWS_12) + "\n")
    for key in [base_key] + sorted(finalists, key=lambda k: (k[1], k[2])):
        tp, sl = key[1], key[2]
        cell = default_cells[key]
        vals = " | ".join(fmt_dd(dd(cell, w)) for w in PHASE_D_WINDOWS_12)
        tag = cell_tag(tp, sl) + (' (BASE)' if key == base_key else '')
        f.write(f"| {tag} | {vals} |\n")


def analyze_apex(f, default_cells, fillprobe_cells, survivor_cells, parity_rows, apex2x_rows):
    profile = 'apex'
    keys = [k for k in default_cells if k[0] == profile]
    base_key = (profile, BASELINE_CELL[0], BASELINE_CELL[1])
    f.write(f"\n## Profile: apex\n\n")
    f.write("Primary gate is the 2x-race harness (phaseD_apex2x.py), NOT the flat 12-window MC "
           "table below (that table is informational -- collapse/DD sanity only; PREREG section 3: "
           "'Apex acceptance (final read on the 2x-race harness, Phase D)').\n")

    apex2x_finalists = [k for k in apex2x_rows if k != BASELINE_CELL]
    if not apex2x_finalists:
        f.write("\n_(no non-baseline arms in phaseD_apex2x_*.csv -- nothing to confirm.)_\n")
    else:
        f.write("\n| cell | collapse_ok(12w) | P(2x) | worst_dd | med_days | p_coll | "
               "floors_ok | n_strictly_better | fillprobe | survivor | parity | VERDICT | reason |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for key in sorted(apex2x_finalists, key=lambda k: (k[0], k[1])):
            tp, sl = key
            mc_key = (profile, tp, sl)
            cell = default_cells.get(mc_key)
            c_clean, c_bad = collapse_clean(cell) if cell else (False, ['no default-mode rows'])
            acc = apex_acceptance_check(apex2x_rows.get(key))
            fp_flag, _ = fillprobe_inversion_check(default_cells, fillprobe_cells, mc_key, base_key)
            sv_flag, _ = survivor_only_contrast_check(default_cells, survivor_cells, mc_key, base_key)
            sv_coll_flag, sv_coll_bad = survivor_only_collapse_check(survivor_cells, mc_key)
            parity_d = cascade_parity_direction_check(parity_rows, profile, tp, sl)
            # MC-side DD edge proxy for the parity direction check: the flat
            # 12-window table's own 5y worst_dd delta vs baseline (same
            # convention core_lane_check uses), NOT the 2x-race harness
            # numbers (those have no baseline-relative delta to compare
            # against a single-replay DD delta).
            base_cell_for_apex = default_cells.get(base_key)
            mc_dd_edge = None
            if cell is not None and base_cell_for_apex is not None:
                c5y, b5y = dd(cell, '5y'), dd(base_cell_for_apex, '5y')
                if c5y is not None and b5y is not None:
                    mc_dd_edge = b5y - c5y   # positive = finalist improves DD
            parity_flag = (parity_d is not None and sign_disagrees(
                mc_dd_edge, parity_d['d_max_dd_pct'], DD_NOISE_FLOOR_PP))
            evidence_incomplete = (mc_key not in fillprobe_cells or base_key not in fillprobe_cells
                                  or mc_key not in survivor_cells or base_key not in survivor_cells
                                  or parity_d is None)
            _gate_fail_reason = ('2x-race floors not met (see p2x/worst_dd/med_days/p_coll cols)'
                                if not acc.get('floors_ok', False) and not acc.get('incomplete')
                                else 'tie -- floors met but 0/3 strictly better than the live cert')
            verdict, reason = render_verdict(c_clean, acc['accepted'], acc.get('incomplete', False),
                                             evidence_incomplete, fp_flag, sv_flag, parity_flag,
                                             survivor_collapse_flag=sv_coll_flag,
                                             gate_fail_reason=_gate_fail_reason)
            med_days_str = '' if acc['med_days'] is None else f"{acc['med_days']:.0f}d"
            f.write(f"| {cell_tag(tp, sl)} | {c_clean} | {fmt_dd(acc['p2x'])} | "
                   f"{fmt_dd(acc['worst_dd'])} | {med_days_str} | "
                   f"{fmt_dd(acc['p_coll_pct'])} | {acc.get('floors_ok')} | {acc['n_strictly_better']}/3 | "
                   f"{'FLAG' if fp_flag else 'ok'} | {'FLAG' if sv_flag else 'ok'} | "
                   f"{'FLAG' if parity_flag else 'ok'} | **{verdict}** | {reason} |\n")

    if base_key in default_cells:
        f.write(f"\n### Per-window worst_dd vs baseline (apex, flat 12-window MC table -- informational)\n\n")
        f.write("| cell | " + " | ".join(PHASE_D_WINDOWS_12) + " |\n")
        f.write("|---|" + "---|" * len(PHASE_D_WINDOWS_12) + "\n")
        shown = [base_key] + sorted([k for k in keys if k != base_key], key=lambda k: (k[1], k[2]))
        for key in shown:
            tp, sl = key[1], key[2]
            cell = default_cells[key]
            vals = " | ".join(fmt_dd(dd(cell, w)) for w in PHASE_D_WINDOWS_12)
            tag = cell_tag(tp, sl) + (' (BASE)' if key == base_key else '')
            f.write(f"| {tag} | {vals} |\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--include-smoke', action='store_true')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    out_path = args.out or os.path.join(OUT_DIR, 'phaseD_summary.md')
    buckets, paths_by_kind = load_all(OUT_DIR, include_smoke=args.include_smoke)

    if not buckets.get('default'):
        raise SystemExit(f"no phaseD_*.csv default-mode rows found under {OUT_DIR}")

    default_cells = merge_mc_cells(buckets.get('default', []))
    fillprobe_cells = merge_mc_cells(buckets.get('fillprobe', []))
    survivor_cells = merge_mc_cells(buckets.get('survivor', []))
    apex2x_rows = merge_apex2x_rows(buckets.get('apex2x', []))
    parity_rows = merge_parity_rows(buckets.get('cascade_parity', []))

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# Phase D summary -- formal read + SHIP/FLAG/NO-SHIP verdict (PREREG.md section 6)\n\n")
        for kind in ('default', 'fillprobe', 'survivor', 'apex2x', 'cascade_parity'):
            ps = paths_by_kind.get(kind, [])
            f.write(f"- {kind}: {len(ps)} file(s)" + (f" ({', '.join(os.path.basename(p) for p in ps)})" if ps else " (NONE)") + "\n")
        f.write(f"\nNoise floors (PREREG section 3): DD claims need >={DD_NOISE_FLOOR_PP}pp, "
               f"compound claims need >={COMPOUND_NOISE_FLOOR_PCT}%. Apex cert reference: "
               f"P(2x)={CERT_P2X_PCT}% worst_dd={CERT_WORST_DD_PCT}% med_days={CERT_MED_DAYS}d.\n")

        analyze_core(f, default_cells, fillprobe_cells, survivor_cells, parity_rows)
        analyze_apex(f, default_cells, fillprobe_cells, survivor_cells, parity_rows, apex2x_rows)

    n_lines = sum(1 for _ in open(out_path, encoding='utf-8'))
    print(f"[WROTE] {out_path} ({n_lines} lines)")
    if n_lines > 200:
        print(f"[WARN] output is {n_lines} lines, over the 200-line budget", file=sys.stderr)


if __name__ == '__main__':
    main()
