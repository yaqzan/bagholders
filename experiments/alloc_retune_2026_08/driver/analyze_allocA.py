#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_allocA.py -- MECHANICAL Phase A prune (PREREG.md section 1, "Prune
per tpsl section 2 mechanics"). No judgment calls: every rule below is a
direct port of experiments/tpsl_refine_2026_08/driver/analyze_phaseA.py,
generalized from a 2-axis (tp,sl) grid to alloc's 3-axis Core grid
(shape x MaxPos x gross) and 2-axis Apex grid (n x flat_frac).

Reads out/allocA_<job>.csv for every job matching --glob (default
'allocA_*.csv', auto-excluding filenames containing 'smoke'), writes --out
(default out/allocA_summary.md). The orchestrator runs this once all real
Phase A jobs finish; this session only unit-tests it against synthetic rows
(never real Phase A output -- see the BUILD spec's "Do NOT analyze real
results").

Prune: drop any cell with p_coll>0 on any canonical window it has data for.
Carry = Pareto-nondominated(22-now WorstDD, 22-now med_ret) [apex:
worst-of-4-windows WorstDD instead of 22-now WorstDD] U top-12 by 22-now
WorstDD U {baseline + its one-grid-step-per-axis neighbors} -- capped at 15
by 22-now WorstDD.

NEIGHBOR DEFINITION (generalization note -- tpsl's 2-axis grid had an
unambiguous "4 neighbors" reading; alloc's grid has 3 axes for Core (one of
them, shape, is a named/indexed list, not numeric) and 2 for Apex. This
analyzer defines "neighbor" as ONE grid step along EACH axis, holding the
OTHER axes at the baseline value -- the direct mechanical extension of
tpsl's own TP/SL neighbor rule (tp+-1 step at base sl, sl+-1 step at base
tp) to N axes. At a grid boundary (e.g. Core baseline shape S0 sits at
index 0, so it has only ONE shape-direction neighbor, not two) or when a
candidate neighbor cell was filtered out of the actual grid (Apex's n*frac
in [0.60,1.05] range check drops some one-step neighbors of the n10/f0.10
baseline), the missing neighbor is simply absent -- never invented.
"""
import argparse
import csv
import glob
import os
from collections import defaultdict

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(_THIS_DIR), 'out')

# Grids -- mirrors allocA_run.py's LOCKED PREREG section 1 grids exactly.
CORE_SHAPE_ORDER = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']
CORE_MAXPOS_GRID = [10, 12, 14, 16, 18, 20]
CORE_GROSS_GRID = [0.40, 0.50, 0.65, 0.80]
CORE_BASELINE = ('S0', 14, 0.50)

APEX_N_GRID = [6, 8, 10, 12, 14]
APEX_FRAC_GRID = [0.08, 0.10, 0.125, 0.15]
APEX_BASELINE = (10, 0.10)

CANON_WINDOWS = ('2022', '2024', '22-now', '2020_crash')   # PREREG section 1
CAP_PER_PROFILE, TOPN_TABLE, TOP12_BY_DD = 15, 20, 12


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


def core_neighbor_names(existing_names):
    """One grid step per axis (shape, max_pos, gross), holding others at the
    baseline value; only candidates present in `existing_names` survive.
    Always includes the baseline name itself if present."""
    base_shape, base_mp, base_gross = CORE_BASELINE
    candidates = set()
    si = CORE_SHAPE_ORDER.index(base_shape)
    for j in (si - 1, si + 1):
        if 0 <= j < len(CORE_SHAPE_ORDER):
            candidates.add((CORE_SHAPE_ORDER[j], base_mp, base_gross))
    mi = CORE_MAXPOS_GRID.index(base_mp)
    for j in (mi - 1, mi + 1):
        if 0 <= j < len(CORE_MAXPOS_GRID):
            candidates.add((base_shape, CORE_MAXPOS_GRID[j], base_gross))
    gi = CORE_GROSS_GRID.index(base_gross)
    for j in (gi - 1, gi + 1):
        if 0 <= j < len(CORE_GROSS_GRID):
            candidates.add((base_shape, base_mp, CORE_GROSS_GRID[j]))
    candidates.add(CORE_BASELINE)
    names = {core_cell_name(*t) for t in candidates}
    return {n for n in names if n in existing_names}


def apex_neighbor_names(existing_names):
    base_n, base_frac = APEX_BASELINE
    candidates = set()
    ni = APEX_N_GRID.index(base_n)
    for j in (ni - 1, ni + 1):
        if 0 <= j < len(APEX_N_GRID):
            candidates.add((APEX_N_GRID[j], base_frac))
    fi = APEX_FRAC_GRID.index(base_frac)
    for j in (fi - 1, fi + 1):
        if 0 <= j < len(APEX_FRAC_GRID):
            candidates.add((base_n, APEX_FRAC_GRID[j]))
    candidates.add(APEX_BASELINE)
    names = {apex_cell_name(*t) for t in candidates}
    return {n for n in names if n in existing_names}


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
    (profile,window,cell_name) -- e.g. a rare crash-mid-write duplicate row
    from a restartable job -- resolves to the LAST occurrence (plain dict
    overwrite, file/row order = as read)."""
    cells = defaultdict(lambda: {'windows': {}})
    for r in rows:
        key = (r['profile'], r['cell_name'])
        cells[key]['windows'][r['window']] = r
    return cells


def timing_totals(rows):
    """NOTE: elapsed_prepare_s is charged to only ONE row per (job,window) by
    the driver (marginal-cost convention -- prepare is shared across all
    cells of a window), so a plain sum here is the correct total wall time,
    unlike if every row carried the full window prepare cost."""
    by_profile = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        by_profile[r['profile']][0] += fnum(r.get('elapsed_prepare_s')) or 0.0
        by_profile[r['profile']][1] += fnum(r.get('elapsed_sim_s')) or 0.0
    tot = [sum(v[0] for v in by_profile.values()), sum(v[1] for v in by_profile.values())]
    return tot, by_profile


def p_coll_any(cell):
    vals = [fnum(cell['windows'][w].get('p_coll')) for w in CANON_WINDOWS if w in cell['windows']]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def pareto_nondominated(points):
    """points: key -> (dd, med_ret); lower dd better, higher med_ret better."""
    out = set(points)
    for a, (dda, ma) in points.items():
        for b, (ddb, mb) in points.items():
            if a != b and ddb <= dda and mb >= ma and (ddb < dda or mb > ma):
                out.discard(a)
                break
    return out


def analyze_profile(profile, cells):
    """cells: cell_name -> {'windows': {label: row}} (already filtered to one profile)."""
    survivors, dropped = {}, []
    for name, c in cells.items():
        p_any = p_coll_any(c)
        (dropped.append((name, p_any)) if (p_any is not None and p_any > 0) else survivors.__setitem__(name, c))

    now_metrics = {}
    for name, c in survivors.items():
        row = c['windows'].get('22-now')
        if row is None:
            continue
        dd, med = fnum(row.get('worst_dd')), fnum(row.get('med_ret'))
        if dd is not None and med is not None:
            now_metrics[name] = (dd, med)

    if profile == 'apex':
        pareto_points = {}
        for name, (dd_now, med_now) in now_metrics.items():
            dds = [fnum(survivors[name]['windows'][w]['worst_dd']) for w in CANON_WINDOWS
                   if w in survivors[name]['windows'] and fnum(survivors[name]['windows'][w].get('worst_dd')) is not None]
            if dds:
                pareto_points[name] = (max(dds), med_now)
    else:
        pareto_points = dict(now_metrics)

    pareto_set = pareto_nondominated(pareto_points) if pareto_points else set()
    top12 = set(sorted(now_metrics, key=lambda k: now_metrics[k][0])[:TOP12_BY_DD])
    neighbor_fn = apex_neighbor_names if profile == 'apex' else core_neighbor_names
    baseline_group = neighbor_fn(set(survivors))

    carried = {k for k in (pareto_set | top12 | baseline_group) if k in now_metrics}
    if len(carried) > CAP_PER_PROFILE:
        carried = set(sorted(carried, key=lambda k: now_metrics[k][0])[:CAP_PER_PROFILE])

    return dict(survivors=survivors, dropped=dropped, now_metrics=now_metrics,
                pareto_set=pareto_set, top12=top12, baseline_group=baseline_group, carried=carried)


def fmt_pct(v):
    return '' if v is None else f"{v:+.1f}%"


def fmt_dd(v):
    return '' if v is None else f"{v:.1f}%"


def _descriptor(name, c):
    """Human-readable cell descriptor pulled from any available window row
    (shape/tier/max_pos/gross are window-invariant for a given cell_name)."""
    row = next(iter(c['windows'].values()))
    if row.get('shape_name'):
        return (f"{row['shape_name']} ({fnum(row['tier_ultra']):.2f}/{fnum(row['tier_top']):.2f}/"
                f"{fnum(row['tier_mid']):.2f}/{fnum(row['tier_low']):.2f}) mp={row['max_pos']} "
                f"gross={fnum(row['gross']):.2f}")
    return f"n={row['n_names']} frac={fnum(row['flat_frac']):.3f} gross={fnum(row['gross']):.2f}"


def write_table(f, title, keys, cells, now_metrics):
    f.write(f"\n### {title} ({len(keys)})\n\n")
    if not keys:
        f.write("_(none)_\n")
        return
    f.write("| cell | 22-now dd/med | 2022 dd | 2024 dd | 2020_crash dd | p_coll any | |\n")
    f.write("|---|---|---|---|---|---|---|\n")
    ranked = sorted(keys, key=lambda k: now_metrics.get(k, (float('inf'), 0))[0])
    for name in ranked[:TOPN_TABLE]:
        c = cells[name]
        dd, med = now_metrics.get(name, (None, None))
        w2022 = fnum(c['windows']['2022']['worst_dd']) if '2022' in c['windows'] else None
        w2024 = fnum(c['windows']['2024']['worst_dd']) if '2024' in c['windows'] else None
        wcrash = fnum(c['windows']['2020_crash']['worst_dd']) if '2020_crash' in c['windows'] else None
        p_any = p_coll_any(c)
        is_base = any(fnum(r.get('is_baseline')) == 1.0 for r in c['windows'].values())
        mark = '<- BASELINE' if is_base else ''
        f.write(f"| {_descriptor(name, c)} | {fmt_dd(dd)} / {fmt_pct(med)} | {fmt_dd(w2022)} | "
                f"{fmt_dd(w2024)} | {fmt_dd(wcrash)} | {p_any if p_any is not None else ''} | {mark} |\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--glob', default='allocA_*.csv')
    ap.add_argument('--include-smoke', action='store_true')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    out_path = args.out or os.path.join(OUT_DIR, 'allocA_summary.md')
    rows, paths = load_rows(args.glob, exclude_smoke=not args.include_smoke)
    if not rows:
        raise SystemExit(f"no rows for glob={args.glob!r} under {OUT_DIR}")

    all_cells = merge_cells(rows)
    (tot_prep, tot_sim), by_profile = timing_totals(rows)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# Phase A summary -- mechanical prune (PREREG.md section 1)\n\n")
        f.write(f"Source files ({len(paths)}): " + ", ".join(os.path.basename(p) for p in paths) + "\n\n")
        f.write(f"Total rows: {len(rows)}  |  Total (profile,cell) combinations: {len(all_cells)}\n\n")
        f.write("## Timing totals\n\n")
        f.write(f"- ALL: prepare={tot_prep:.1f}s sim={tot_sim:.1f}s combined={tot_prep + tot_sim:.1f}s\n")
        for profile, (p, s) in sorted(by_profile.items()):
            f.write(f"- {profile}: prepare={p:.1f}s sim={s:.1f}s combined={p + s:.1f}s\n")

        for profile in sorted({k[0] for k in all_cells}):
            cells_p = {k[1]: v for k, v in all_cells.items() if k[0] == profile}
            res = analyze_profile(profile, cells_p)
            f.write(f"\n## Profile: {profile}\n\n")
            f.write(f"Cells seen: {len(cells_p)}  |  Dropped (p_coll>0 on any window): {len(res['dropped'])}  |  "
                    f"Survivors: {len(res['survivors'])}  |  Ranked (has 22-now): {len(res['now_metrics'])}\n")
            if res['dropped']:
                f.write("\nDropped: " + ", ".join(
                    f"{name}(p_coll={p:.1f}%)" for name, p in res['dropped']) + "\n")
            write_table(f, "Top-20 by 22-now WorstDD", res['now_metrics'].keys(), res['survivors'], res['now_metrics'])
            write_table(f, "Pareto-nondominated set", res['pareto_set'], res['survivors'], res['now_metrics'])
            write_table(f, "CARRIED to Phase B (Pareto U top-12 U baseline+neighbors, capped 15)",
                        res['carried'], res['survivors'], res['now_metrics'])

    print(f"[WROTE] {out_path}  ({len(rows)} rows, {len(paths)} file(s), {len(all_cells)} cells)")


if __name__ == '__main__':
    main()
