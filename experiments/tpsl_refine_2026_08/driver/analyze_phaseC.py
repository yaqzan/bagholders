#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_phaseC.py -- MECHANICAL Phase C 5-window SCREEN (PREREG.md section 5's
own "Lane entry per section 3 (evaluated on the 9-window set for any cell that
passes the 5-window screen)" -- this file implements ONLY the 5-window
screen, never a full lane/finalist verdict; that needs the 9-window Phase-B-
style window set, which Phase C's own locked window set (2022, 2024, 22-now,
5y, 2020_crash) does not carry (no 2021/2023/2025 for the annual-regression
check, no GROWTH lane, no Apex Pareto). SCREEN-PASS here is a preliminary
directional filter for BOTH profiles uniformly -- Apex's true gate is still
the Phase D 2x-race harness, same caveat analyze_phaseB.py states for its own
Apex proxy.

Reads out/phaseC_<job>.csv for every job matching --glob (default
'phaseC_*.csv', excluding any path with 'validate' or 'smoke' in its
basename -- validate-only output lives in separate phaseC_validate_*.csv
files and must never be mixed into a science read). Do NOT run this against
real Phase C output from this builder session -- only unit-tested here
(test_phaseC.py) against a tiny synthetic CSV; the orchestrator runs it for
real once the queued Phase C jobs (submitted later, once Phase B's winner is
known) finish.

SCREEN-PASS rule (PREREG section 5 + the Phase C build spec, verbatim):
  dd_5y_cell <= dd_5y_base - 1.5          (>=1.5pp 5y WorstDD improvement)
  AND med_5y_cell >= 0.75 * med_5y_base   (5y median compound not gutted)
  AND p_coll == 0.0 on ALL 5 PHASE_C_WINDOWS (collapse=0, hard, both profiles)
Baseline = the SAME profile's 'flat_base' row (source='flat_base'), i.e. the
in-phase paired (tp_base,sl_base) flat cell PREREG CLARIFICATIONS mandates be
re-run every phase -- NEVER a remembered cert number. 22-now worst_dd delta
IS reported in the per-cell table (informational, matches PREREG's literal
table-columns list) but does NOT gate SCREEN-PASS -- the screen definition
names only dd_5y/med_5y/collapse; this mirrors analyze_phaseB.py's own
dip/2020_crash informational-only convention.

Noise floor (paired-seed, PREREG section 3): DD claims need >=1.5pp at N=500
to be non-noise; Phase C is N=300, provisional -- SCREEN-PASS here is a
"worth carrying to the 9-window follow-up" flag, never a ship claim.
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

# Locked window/source constants -- single source of truth, phaseC_patch.py
# (module import only: constants + pure functions, no DB/mc touched at import
# time -- see that file's own module docstring for the lazy-import discipline).
from phaseC_patch import PHASE_C_WINDOWS, SOURCES, FLAT_SOURCE, BREADTH_THRESHOLDS  # noqa: E402

DD_IMPROVE_MIN_PP = 1.5
MED_RATIO_MIN = 0.75
TOPN_PER_SOURCE = 6


def fnum(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_rows(glob_pattern, exclude_aux=True):
    paths = sorted(glob.glob(os.path.join(OUT_DIR, glob_pattern)))
    if exclude_aux:
        paths = [p for p in paths
                 if 'validate' not in os.path.basename(p).lower()
                 and 'smoke' not in os.path.basename(p).lower()]
    rows = []
    for p in paths:
        with open(p, newline='', encoding='utf-8') as f:
            rows.extend(csv.DictReader(f))
    return rows, paths


def cell_key(r):
    """(profile, source, thr, tp_stress, sl_stress) -- thr normalized to ''
    for non-breadth rows so it's a stable dict key regardless of blank vs
    absent CSV representation."""
    thr = r.get('thr', '') or ''
    return (r['profile'], r['source'], thr, fnum(r['tp_stress']), fnum(r['sl_stress']))


def merge_cells(rows):
    """cell_key -> {'windows': {label: row}}. A repeated (profile,window,
    source,thr,tp_stress,sl_stress) row (restartable-job re-run) resolves to
    the LAST occurrence (plain dict overwrite, file/row order = as read --
    same rule as analyze_phaseA.py/analyze_phaseB.py)."""
    cells = defaultdict(lambda: {'windows': {}})
    for r in rows:
        cells[cell_key(r)]['windows'][r['window']] = r
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
    row for all 5 PHASE_C_WINDOWS AND p_coll==0.0 on every one. dropped = has
    all 5 but p_coll>0 on at least one. incomplete = missing >=1 window's row
    outright (queue job not yet done / never scheduled for that window)."""
    wins = c['windows']
    missing = [w for w in PHASE_C_WINDOWS if w not in wins]
    if missing:
        return 'incomplete', missing
    p_colls = [fnum(wins[w].get('p_coll')) for w in PHASE_C_WINDOWS]
    if any(p is None for p in p_colls):
        return 'incomplete', [w for w, p in zip(PHASE_C_WINDOWS, p_colls) if p is None]
    if any(p > 0.0 for p in p_colls):
        return 'dropped', max(p_colls)
    return 'survivor', None


def dd(c, window):
    return fnum(c['windows'][window]['worst_dd'])


def med(c, window):
    return fnum(c['windows'][window]['med_ret'])


def pcoll_string(c):
    return '/'.join(f"{fnum(c['windows'][w].get('p_coll')):.0f}" for w in PHASE_C_WINDOWS)


def screen_pass(cell, baseline):
    dd5_c, dd5_b = dd(cell, '5y'), dd(baseline, '5y')
    med5_c, med5_b = med(cell, '5y'), med(baseline, '5y')
    if None in (dd5_c, dd5_b, med5_c, med5_b):
        return False
    return dd5_c <= dd5_b - DD_IMPROVE_MIN_PP and med5_c >= MED_RATIO_MIN * med5_b


def cell_tag(key):
    profile, source, thr, ts, ss = key
    thr_s = f"@{thr}" if thr not in ('', None) else ''
    return f"{source}{thr_s} {ts:+.2f}/{ss:+.2f}"


def fmt_pct(v):
    return '' if v is None else f"{v:+.1f}%"


def fmt_dd(v):
    return '' if v is None else f"{v:.1f}%"


def analyze_profile_source(f, profile, source, cells_ps, baseline):
    """One table: all SURVIVOR cells of this (profile, source), vs the
    profile's flat_base baseline. Ranked by dd_5y ascending (best DD first),
    capped at TOPN_PER_SOURCE, with any SCREEN-PASS cell outside the cap
    unioned in (so a real screen pass is never silently cut by the table
    cap)."""
    survivors, dropped, incomplete = {}, {}, {}
    for key, c in cells_ps.items():
        status, detail = classify_cell(c)
        if status == 'survivor':
            survivors[key] = c
        elif status == 'dropped':
            dropped[key] = detail
        else:
            incomplete[key] = detail

    f.write(f"\n### {profile} / {source}\n\n")
    f.write(f"cells: seen={len(cells_ps)} survivors={len(survivors)} "
            f"dropped(p_coll>0)={len(dropped)} incomplete={len(incomplete)}\n")
    if dropped:
        f.write("dropped: " + ", ".join(f"{cell_tag(k)}(p_coll={v:.1f}%)"
                                         for k, v in sorted(dropped.items())) + "\n")
    if not survivors:
        f.write("_(no survivors -- nothing to screen)_\n")
        return set()

    base_dd5, base_dd22, base_med5 = dd(baseline, '5y'), dd(baseline, '22-now'), med(baseline, '5y')
    passing = {k for k, c in survivors.items() if screen_pass(c, baseline)}

    ranked = sorted(survivors, key=lambda k: dd(survivors[k], '5y'))
    shown = list(ranked[:TOPN_PER_SOURCE])
    for k in passing:
        if k not in shown:
            shown.append(k)
    shown_set = set(shown)
    shown.sort(key=lambda k: dd(survivors[k], '5y'))

    f.write(f"\nbaseline (flat_base): dd_5y={fmt_dd(base_dd5)} dd_22now={fmt_dd(base_dd22)} "
            f"med_5y={fmt_pct(base_med5)}\n\n")
    f.write(f"| cell | dd_5y | delta | dd_22now | delta | med_5y | delta | p_coll ({'/'.join(PHASE_C_WINDOWS)}) | screen |\n")
    f.write("|---|---|---|---|---|---|---|---|---|\n")
    for k in shown:
        c = survivors[k]
        c_dd5, c_dd22, c_med5 = dd(c, '5y'), dd(c, '22-now'), med(c, '5y')
        mark = 'PASS' if k in passing else ''
        f.write(f"| {cell_tag(k)} | {fmt_dd(c_dd5)} | {c_dd5 - base_dd5:+.1f}pp | "
                f"{fmt_dd(c_dd22)} | {c_dd22 - base_dd22:+.1f}pp | {fmt_pct(c_med5)} | "
                f"{(c_med5 - base_med5):+.1f}pp | {pcoll_string(c)} | {mark} |\n")
    if len(ranked) > len(shown):
        f.write(f"\n_(+{len(ranked) - len(shown)} more survivor(s) not shown, none SCREEN-PASS)_\n")
    return passing


def analyze_profile(f, profile, cells_p):
    base_key = None
    for key in cells_p:
        if key[1] == FLAT_SOURCE:
            base_key = key
            break
    f.write(f"\n## Profile: {profile}\n\n")
    if base_key is None:
        f.write("**flat_base baseline row NOT FOUND for this profile -- entire profile SKIPPED "
                "(cannot compute any delta/screen without it).**\n")
        return
    status, _ = classify_cell(cells_p[base_key])
    if status != 'survivor':
        f.write(f"**flat_base baseline is NOT a survivor ({status}) -- entire profile SKIPPED.**\n")
        return
    baseline = cells_p[base_key]
    tp_b, sl_b = base_key[3], base_key[4]
    f.write(f"base pair (tp_base,sl_base) = ({tp_b:+.2f},{sl_b:+.2f})  "
            f"(from the flat_base row's tp_stress/sl_stress, which equal tp_base/sl_base by construction)\n")

    all_screen_pass = {}
    for source in SOURCES:
        cells_ps = {k: v for k, v in cells_p.items() if k[1] == source}
        if not cells_ps:
            f.write(f"\n### {profile} / {source}\n\n_(no rows for this source)_\n")
            continue
        passing = analyze_profile_source(f, profile, source, cells_ps, baseline)
        if passing:
            all_screen_pass[source] = passing

    f.write(f"\n### {profile} SCREEN-PASS summary\n\n")
    if not all_screen_pass:
        f.write("_(no cell in any source passed the 5-window screen against the in-phase flat baseline)_\n")
    else:
        for source, keys in all_screen_pass.items():
            tags = ', '.join(cell_tag(k) for k in sorted(keys, key=lambda k: dd(cells_p[k], '5y')))
            f.write(f"- **{source}**: {len(keys)} -- {tags}\n")
        f.write("\nNOTE: SCREEN-PASS is a 5-window (N=300) preliminary filter only. Lane entry "
                "(PREREG section 3) still requires re-running the passing cell(s) on the full "
                "9-window Phase-B-style set at N>=300 (`--windows` on a follow-up job), then the "
                "formal Phase D N=500x12-window confirm before any ship claim.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--glob', default='phaseC_*.csv')
    ap.add_argument('--include-aux', action='store_true',
                     help='include validate/smoke files in the glob (debugging only -- never for a real read)')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    out_path = args.out or os.path.join(OUT_DIR, 'phaseC_summary.md')
    rows, paths = load_rows(args.glob, exclude_aux=not args.include_aux)
    if not rows:
        raise SystemExit(f"no rows for glob={args.glob!r} under {OUT_DIR}")

    all_cells = merge_cells(rows)
    (tot_prep, tot_sim), by_profile = timing_totals(rows)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# Phase C summary -- 5-window SCREEN (PREREG.md section 5)\n\n")
        f.write(f"Source files ({len(paths)}): " + ", ".join(os.path.basename(p) for p in paths) + "\n\n")
        f.write(f"Total rows: {len(rows)}  |  Total cells: {len(all_cells)}  |  "
                f"windows={list(PHASE_C_WINDOWS)}  sources={list(SOURCES)} "
                f"(breadth thr in {list(BREADTH_THRESHOLDS)})\n\n")
        f.write(f"SCREEN-PASS = dd_5y <= base_dd_5y-{DD_IMPROVE_MIN_PP}pp AND "
                f"med_5y >= {MED_RATIO_MIN}x base_med_5y AND collapse=0 on all 5 windows. "
                "22-now dd delta is reported, NOT gating. N=300, provisional -- never a ship claim.\n")
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
              f"tighten TOPN_PER_SOURCE or table width", file=sys.stderr)


if __name__ == '__main__':
    main()
