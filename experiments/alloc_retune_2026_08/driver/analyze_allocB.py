#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_allocB.py -- MECHANICAL Phase B lane evaluation, generalizing
experiments/tpsl_refine_2026_08/driver/analyze_phaseB.py's (tp,sl) lane logic
to alloc's Core (shape x MaxPos x gross) and Apex (n x flat_frac) cell
spaces. No judgment calls: every rule below is copied verbatim from
experiments/tpsl_refine_2026_08/PREREG.md section 3 (the INHERITED lanes,
per experiments/alloc_retune_2026_08/PREREG.md "inherits ... wholesale") --
only the cell KEY representation differs (alloc uses the CSV's own
`cell_name` string, e.g. 'S9_mp10_g0.40' / 'n10_f0.100', not a raw (tp,sl)
tuple -- Core has 3 axes, Apex 2, so no uniform tuple works for both).

Reads out/allocB_<job>.csv for every job matching --glob (default
'allocB_*.csv'), writes --out (default out/allocB_summary.md). Do NOT run
this against real Phase B output from this session -- only unit-tested here
(test_analyze_allocB.py) against tiny synthetic CSV rows; the orchestrator
runs it for real once the queued jobs finish (BUILD spec: "do NOT analyze
real results").

Baseline = the row with is_baseline==1 for that profile (the CSV column
allocA_run.CoreCell/ApexCell.row_fields() already stamps -- S0/mp14/g0.50 for
core, n10/f0.10 for apex), i.e. IN-PHASE, never a remembered cert number.

Hard filter (both profiles, PREREG-inherited "collapse=0 on every window"):
a cell is a SURVIVOR only if it has a row for ALL 9 PHASE_B_WINDOWS (imported
from allocB_run.py -- single source of truth) and p_coll==0.0 on every one.
Anything else (missing window data, or p_coll>0 on any window) is excluded
from every ranking/lane/Pareto computation below (but still counted in the
cell-accounting section).

CORE LANE-DD:     dd_5y <= base_dd_5y - 1.5
               AND dd_22now <= base_dd_22now + 0.5
               AND med_5y >= 0.75 * base_med_5y
               AND no window in {2021,2022,2023,2024,2025} regresses
                   worst_dd by >5pp vs baseline (T5; regression = entrant_dd
                   - baseline_dd; only entrant_dd MORE than 5pp WORSE fails).
CORE LANE-GROWTH: med_5y >= 1.5 * base_med_5y
               AND dd_5y <= base_dd_5y + 0.5
               AND dd_22now <= base_dd_22now + 1.0
               AND same 5-annual-window regression check.
CORE finalists: lane entrants (DD union GROWTH) ranked by dd_5y ascending;
top-3. If LANE-GROWTH has >=1 entrant and NONE of the top-3 (by dd_5y) is a
LANE-GROWTH entrant, the best (lowest dd_5y) LANE-GROWTH entrant displaces
slot 3 (index 2).

APEX (Phase B proxy only -- the TRUE Apex objective is the Phase D 2x-race
harness, not computable from these MC-sweep CSVs): among survivors,
Pareto-nondominated on (worst-of-9-windows worst_dd [lower better], 22-now
med_ret [higher better]). Finalists = top-3 by worst-of-9 worst_dd WITHIN the
Pareto set (not all survivors). The baseline (n10/f0.10) row is always
additionally reported regardless of whether it lands in the Pareto set or
finalist list.

dip / 2020_crash deltas vs baseline are reported alongside every entrant/
finalist table for BOTH profiles as INFORMATIONAL ONLY -- PREREG section 3
names only the 5 annual windows for the regression check; dip/2020_crash are
part of the Phase B window set for coverage, not the Phase B gate.

Noise floor (paired-seed, inherited PREREG section 3): DD claims need
>=1.5pp at N=500 to be non-noise; this file's numbers are N=300 and
PROVISIONAL -- single-window DD noise at N=300 is ~5-8pp. No claim from this
file is a ship decision; Phase C/D (N=500 x 12 windows) is the confirm gate.
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

# Locked grid/window constants -- single source of truth, allocB_run.py
# (module import only: constants + cheap self-asserts, no DB/mc touched --
# same reuse pattern test_analyze_allocA.py already uses for allocA_run).
from allocB_run import CORE_CELLS, APEX_CELLS, PHASE_B_WINDOWS   # noqa: E402

ANNUAL_WINDOWS = ('2021', '2022', '2023', '2024', '2025')
ANNUAL_REGRESSION_LIMIT_PP = 5.0
INFO_WINDOWS = ('dip', '2020_crash')   # informational-only deltas, both profiles
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


LOCKED_NAMES = {
    'core': {core_cell_name(*t) for t in CORE_CELLS},
    'apex': {apex_cell_name(*t) for t in APEX_CELLS},
}


def load_rows(pattern, exclude_smoke):
    paths = sorted(glob.glob(os.path.join(OUT_DIR, pattern)))
    if exclude_smoke:
        paths = [p for p in paths if 'smoke' not in os.path.basename(p).lower()]
    rows = []
    for p in paths:
        with open(p, newline='', encoding='utf-8') as f:
            rows.extend(csv.DictReader(f))
    return rows, paths


def merge_cells(rows):
    """(profile, cell_name) -> {'windows': {label: row}}. A repeated
    (profile,window,cell_name) row (restartable-job re-run) resolves to the
    LAST occurrence (plain dict overwrite, file/row order = as read -- same
    rule as analyze_allocA.py)."""
    cells = defaultdict(lambda: {'windows': {}})
    for r in rows:
        key = (r['profile'], r['cell_name'])
        cells[key]['windows'][r['window']] = r
    return cells


def timing_totals(rows):
    by_profile = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        by_profile[r['profile']][0] += fnum(r.get('elapsed_prepare_s')) or 0.0
        by_profile[r['profile']][1] += fnum(r.get('elapsed_sim_s')) or 0.0
    tot = [sum(v[0] for v in by_profile.values()), sum(v[1] for v in by_profile.values())]
    return tot, by_profile


def classify_cell(c):
    """Returns ('survivor'|'dropped'|'incomplete', detail). survivor = has a
    row for all 9 PHASE_B_WINDOWS AND p_coll==0.0 on every one. dropped = has
    all 9 but p_coll>0 on at least one. incomplete = missing >=1 window's row
    outright (queue job not yet done / never scheduled) or an unparsable
    p_coll on a present row."""
    wins = c['windows']
    missing = [w for w in PHASE_B_WINDOWS if w not in wins]
    if missing:
        return 'incomplete', missing
    p_colls = [fnum(wins[w].get('p_coll')) for w in PHASE_B_WINDOWS]
    if any(p is None for p in p_colls):
        return 'incomplete', [w for w, p in zip(PHASE_B_WINDOWS, p_colls) if p is None]
    if any(p > 0.0 for p in p_colls):
        return 'dropped', max(p_colls)
    return 'survivor', None


def dd(c, window):
    return fnum(c['windows'][window]['worst_dd'])


def med(c, window):
    return fnum(c['windows'][window]['med_ret'])


def find_baseline_key(cells_p):
    """The row with is_baseline==1 (any window row -- the flag is
    window-invariant per cell). Returns None if absent; raises if >1 distinct
    cell claims it (a data-integrity problem, never silently picked)."""
    found = []
    for key, c in cells_p.items():
        if any(fnum(r.get('is_baseline')) == 1.0 for r in c['windows'].values()):
            found.append(key)
    if len(found) > 1:
        raise SystemExit(f"multiple baseline-flagged cells for one profile: {found} -- data integrity problem")
    return found[0] if found else None


def annual_delta_string(cell, baseline):
    parts = []
    for w in ANNUAL_WINDOWS:
        d = dd(cell, w) - dd(baseline, w)
        parts.append(f"{d:+.1f}")
    return '/'.join(parts)


def info_delta_string(cell, baseline):
    parts = []
    for w in INFO_WINDOWS:
        d = dd(cell, w) - dd(baseline, w)
        parts.append(f"{w}:{d:+.1f}pp")
    return ' '.join(parts)


def annual_regression_ok(cell, baseline, limit_pp=ANNUAL_REGRESSION_LIMIT_PP):
    for w in ANNUAL_WINDOWS:
        if dd(cell, w) - dd(baseline, w) > limit_pp:
            return False
    return True


def pareto_nondominated(points):
    """points: key -> (x, y); lower x better, higher y better (same generic
    definition as analyze_allocA.py / analyze_phaseB.py)."""
    out = set(points)
    for a, (xa, ya) in points.items():
        for b, (xb, yb) in points.items():
            if a != b and xb <= xa and yb >= ya and (xb < xa or yb > ya):
                out.discard(a)
                break
    return out


# ---------------------------------------------------------------------------
# CORE lane logic (verbatim port of analyze_phaseB.core_lane_membership /
# core_finalists -- generic over the survivors/baseline dict shape, so the
# key-type change from (tp,sl) to cell_name string needs no logic edits).
# ---------------------------------------------------------------------------

def core_lane_membership(survivors, baseline):
    """Returns (dd_entrants: set, growth_entrants: set, dd5y: {cell: float})
    over `survivors`, evaluated against `baseline`. Baseline itself is never
    an entrant of either lane (self-comparison always fails the strict
    pp/multiplier margins)."""
    base_dd_5y = dd(baseline, '5y')
    base_dd_22now = dd(baseline, '22-now')
    base_med_5y = med(baseline, '5y')

    dd_entrants, growth_entrants, dd5y = set(), set(), {}
    for key, c in survivors.items():
        c_dd5y, c_dd22now, c_med5y = dd(c, '5y'), dd(c, '22-now'), med(c, '5y')
        dd5y[key] = c_dd5y
        reg_ok = annual_regression_ok(c, baseline)

        if (c_dd5y <= base_dd_5y - 1.5 and c_dd22now <= base_dd_22now + 0.5
                and c_med5y >= 0.75 * base_med_5y and reg_ok):
            dd_entrants.add(key)
        if (c_med5y >= 1.5 * base_med_5y and c_dd5y <= base_dd_5y + 0.5
                and c_dd22now <= base_dd_22now + 1.0 and reg_ok):
            growth_entrants.add(key)
    return dd_entrants, growth_entrants, dd5y


def core_finalists(entrants, growth_entrants, dd5y):
    """lane entrants ranked by dd_5y ascending, top-3; if >=1 GROWTH entrant
    exists and none of the top-3 are a GROWTH entrant, the best (lowest
    dd_5y) GROWTH entrant displaces slot 3 (index 2)."""
    ranked = sorted(entrants, key=lambda k: (dd5y[k], k))
    top3 = ranked[:3]
    if growth_entrants and not any(k in growth_entrants for k in top3):
        best_growth = min(growth_entrants, key=lambda k: (dd5y[k], k))
        if len(top3) >= 3:
            top3[2] = best_growth
        else:
            top3.append(best_growth)
    return top3


# ---------------------------------------------------------------------------
# APEX proxy logic (verbatim port of analyze_phaseB's apex_worst9 / _pareto /
# _finalists).
# ---------------------------------------------------------------------------

def apex_worst9(c):
    return max(dd(c, w) for w in PHASE_B_WINDOWS)


def apex_pareto(survivors):
    """Pareto-nondominated on (worst-of-9 worst_dd [lower better], 22-now
    med_ret [higher better]). Returns (pareto_set, worst9: {cell: float})."""
    worst9 = {key: apex_worst9(c) for key, c in survivors.items()}
    points = {key: (worst9[key], med(c, '22-now')) for key, c in survivors.items()}
    pareto = pareto_nondominated(points) if points else set()
    return pareto, worst9


def apex_finalists(pareto_set, worst9):
    ranked = sorted(pareto_set, key=lambda k: (worst9[k], k))
    return ranked[:3]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt_pct(v):
    return '' if v is None else f"{v:+.1f}%"


def fmt_dd(v):
    return '' if v is None else f"{v:.1f}%"


def _descriptor(name, c):
    """Human-readable cell descriptor pulled from any available window row
    (shape/tier/max_pos/gross are window-invariant for a given cell_name) --
    same convention as analyze_allocA.py's _descriptor."""
    row = next(iter(c['windows'].values()))
    if row.get('shape_name'):
        return (f"{row['shape_name']} ({fnum(row['tier_ultra']):.2f}/{fnum(row['tier_top']):.2f}/"
                f"{fnum(row['tier_mid']):.2f}/{fnum(row['tier_low']):.2f}) mp={row['max_pos']} "
                f"gross={fnum(row['gross']):.2f}")
    return f"n={row['n_names']} frac={fnum(row['flat_frac']):.3f}"


def write_core(f, survivors, base_key, baseline, dd_entrants, growth_entrants, dd5y, finalists):
    base_dd5y, base_dd22now, base_med5y = dd(baseline, '5y'), dd(baseline, '22-now'), med(baseline, '5y')
    f.write(f"\nBaseline {_descriptor(base_key, baseline)}: dd_5y={fmt_dd(base_dd5y)} "
            f"dd_22now={fmt_dd(base_dd22now)} med_5y={fmt_pct(base_med5y)}\n")

    entrants = dd_entrants | growth_entrants
    f.write(f"\n### Lane entrants ({len(entrants)}: DD={len(dd_entrants)} GROWTH={len(growth_entrants)}), "
            f"top-{TOPN_TABLE} by dd_5y, finalists marked\n\n")
    if not entrants:
        f.write("_(none -- no cell entered either lane against the in-phase baseline)_\n")
    else:
        f.write("| cell | lane | dd_5y | med_5y | dd_22now | annual dd delta (21/22/23/24/25,pp) | info (dip/crash) | final |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        ranked = sorted(entrants, key=lambda k: (dd5y[k], k))[:TOPN_TABLE]
        for key in ranked:
            c = survivors[key]
            lane = ('DD+GROWTH' if key in dd_entrants and key in growth_entrants
                    else 'DD' if key in dd_entrants else 'GROWTH')
            mark = '<-FINAL' if key in finalists else ''
            f.write(f"| {_descriptor(key, c)} | {lane} | {fmt_dd(dd(c,'5y'))} | {fmt_pct(med(c,'5y'))} | "
                    f"{fmt_dd(dd(c,'22-now'))} | {annual_delta_string(c, baseline)} | "
                    f"{info_delta_string(c, baseline)} | {mark} |\n")

    f.write(f"\n### Finalists ({len(finalists)})\n\n")
    if not finalists:
        f.write("_(none)_\n")
    else:
        for key in finalists:
            c = survivors[key]
            lane = ('DD+GROWTH' if key in dd_entrants and key in growth_entrants
                    else 'DD' if key in dd_entrants else 'GROWTH')
            f.write(f"- {_descriptor(key, c)}  lane={lane}  dd_5y={fmt_dd(dd(c,'5y'))}  "
                    f"med_5y={fmt_pct(med(c,'5y'))}  dd_22now={fmt_dd(dd(c,'22-now'))}\n")


def write_apex(f, survivors, base_key, baseline, pareto, worst9, finalists):
    base_worst9 = apex_worst9(baseline)
    base_med22now = med(baseline, '22-now')
    f.write(f"\nBaseline {_descriptor(base_key, baseline)}: worst-of-9={fmt_dd(base_worst9)} "
            f"med_22now={fmt_pct(base_med22now)} dd_5y={fmt_dd(dd(baseline,'5y'))} (always reported)\n")

    f.write(f"\n### Pareto set ({len(pareto)}) + top-{TOPN_TABLE} by worst-of-9 dd (all survivors)\n\n")
    f.write("| cell | worst-of-9 dd | med_22now | dd_5y | annual dd delta (21/22/23/24/25,pp) | info (dip/crash) | pareto | final |\n")
    f.write("|---|---|---|---|---|---|---|---|\n")
    ranked_all = sorted(survivors, key=lambda k: (worst9[k], k))[:TOPN_TABLE]
    for key in ranked_all:
        c = survivors[key]
        mark = '<-FINAL' if key in finalists else ''
        f.write(f"| {_descriptor(key, c)} | {fmt_dd(worst9[key])} | {fmt_pct(med(c,'22-now'))} | "
                f"{fmt_dd(dd(c,'5y'))} | {annual_delta_string(c, baseline)} | "
                f"{info_delta_string(c, baseline)} | {'Y' if key in pareto else ''} | {mark} |\n")

    f.write(f"\n### Finalists ({len(finalists)}, top-3 by worst-of-9 dd WITHIN the Pareto set) "
            f"+ baseline (always reported)\n\n")
    shown = list(finalists)
    if base_key in survivors and base_key not in shown:
        shown.append(base_key)
    if not shown:
        f.write("_(no survivors -- nothing to report)_\n")
    for key in shown:
        c = survivors[key]
        tag = ('<-BASELINE' if key == base_key and key not in finalists else
               '<-BASELINE+FINAL' if key == base_key else '')
        f.write(f"- {_descriptor(key, c)}  worst-of-9={fmt_dd(worst9[key])}  "
                f"med_22now={fmt_pct(med(c,'22-now'))}  {tag}\n")


def analyze_profile(f, profile, cells_p):
    survivors, dropped, incomplete = {}, {}, {}
    for key, c in cells_p.items():
        status, detail = classify_cell(c)
        if status == 'survivor':
            survivors[key] = c
        elif status == 'dropped':
            dropped[key] = detail
        else:
            incomplete[key] = detail

    locked = LOCKED_NAMES.get(profile, set())
    seen_names = {k[1] for k in cells_p}
    missing_entirely = sorted(locked - seen_names)

    f.write(f"\n## Profile: {profile}\n\n")
    f.write(f"Cell accounting: locked_grid={len(locked)}  seen={len(cells_p)}  "
            f"survivors={len(survivors)}  dropped(p_coll>0)={len(dropped)}  "
            f"incomplete(missing window rows)={len(incomplete)}  "
            f"missing_entirely(no rows at all)={len(missing_entirely)}\n")
    if dropped:
        f.write("Dropped: " + ", ".join(f"{k[1]}(p_coll={v:.1f}%)" for k, v in
                                         sorted(dropped.items())) + "\n")
    if incomplete:
        f.write("Incomplete: " + ", ".join(f"{k[1]}(missing={v})" for k, v in
                                            sorted(incomplete.items())) + "\n")
    if missing_entirely:
        f.write("Missing entirely: " + ", ".join(missing_entirely) + "\n")

    base_key = find_baseline_key(cells_p)
    if base_key is None or base_key not in survivors:
        f.write(f"\n**BASELINE not a survivor for {profile} (missing, incomplete, or collapsed) "
                f"-- lane/Pareto analysis SKIPPED.**\n")
        return
    baseline = survivors[base_key]

    if profile == 'core':
        dd_entrants, growth_entrants, dd5y = core_lane_membership(survivors, baseline)
        finalists = core_finalists(dd_entrants, growth_entrants, dd5y)
        write_core(f, survivors, base_key, baseline, dd_entrants, growth_entrants, dd5y, finalists)
    elif profile == 'apex':
        pareto, worst9 = apex_pareto(survivors)
        finalists = apex_finalists(pareto, worst9)
        write_apex(f, survivors, base_key, baseline, pareto, worst9, finalists)
    else:
        f.write(f"_(unknown profile {profile!r} -- no lane rule defined, skipped)_\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--glob', default='allocB_*.csv')
    ap.add_argument('--include-smoke', action='store_true')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    out_path = args.out or os.path.join(OUT_DIR, 'allocB_summary.md')
    rows, paths = load_rows(args.glob, exclude_smoke=not args.include_smoke)
    if not rows:
        raise SystemExit(f"no rows for glob={args.glob!r} under {OUT_DIR}")

    all_cells = merge_cells(rows)
    (tot_prep, tot_sim), by_profile = timing_totals(rows)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# Phase B summary -- mechanical lane evaluation (tpsl PREREG.md section 3, inherited)\n\n")
        f.write(f"Source files ({len(paths)}): " + ", ".join(os.path.basename(p) for p in paths) + "\n\n")
        f.write(f"Total rows: {len(rows)}  |  Total (profile,cell) combinations: {len(all_cells)}\n\n")
        f.write("Noise floor (inherited PREREG section 3): N=300 single-window DD noise is "
                "~=5-8pp, provisional -- these are CANDIDATE reads, not ship claims; "
                "confirm only happens at Phase D (N=500 x 12 windows).\n")
        f.write("\n## Timing totals\n\n")
        f.write(f"- ALL: prepare={tot_prep:.1f}s sim={tot_sim:.1f}s combined={tot_prep + tot_sim:.1f}s\n")
        for profile, (p, s) in sorted(by_profile.items()):
            f.write(f"- {profile}: prepare={p:.1f}s sim={s:.1f}s combined={p + s:.1f}s\n")

        for profile in sorted({k[0] for k in all_cells}):
            cells_p = {k: v for k, v in all_cells.items() if k[0] == profile}
            analyze_profile(f, profile, cells_p)

    n_lines = sum(1 for _ in open(out_path, encoding='utf-8'))
    print(f"[WROTE] {out_path}  ({len(rows)} rows, {len(paths)} file(s), "
          f"{len(all_cells)} cells, {n_lines} lines)")
    if n_lines > 180:
        print(f"[WARN] output is {n_lines} lines, over the 180-line budget -- "
              f"tighten TOPN_TABLE or table width", file=sys.stderr)


if __name__ == '__main__':
    main()
