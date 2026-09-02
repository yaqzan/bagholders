#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_phaseA.py -- MECHANICAL Phase A prune (PREREG.md section 2). No
judgment calls: every rule below is copied verbatim from PREREG. Reads
out/phaseA_<job>.csv for every job matching --glob (default 'phaseA_*.csv',
auto-excluding filenames containing 'smoke'), writes --out (default
out/phaseA_summary.md). The orchestrator runs this once all real Phase A jobs
finish; this session only unit-smokes it against the smoke CSVs.

Prune: drop any cell with p_coll>0 on any canonical window it has data for.
Carry = Pareto-nondominated(22-now WorstDD, 22-now med_ret) [apex:
worst-of-4-windows WorstDD instead of 22-now WorstDD] U top-12 by 22-now
WorstDD U {incumbent (0.30,-0.70)} U its 4 grid neighbors -- capped at 15 by
22-now WorstDD.
"""
import argparse
import csv
import glob
import os
from collections import defaultdict

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(_THIS_DIR), 'out')

TP_GRID = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.65, 0.85, 1.10]
SL_GRID = [-0.30, -0.40, -0.50, -0.60, -0.70, -0.80, -0.90, -1.00]
INCUMBENT = (0.30, -0.70)
CANON_WINDOWS = ('2022', '2024', '22-now', '2020_crash')   # PREREG section 2
CAP_PER_PROFILE, TOPN_TABLE, TOP12_BY_DD = 15, 20, 12


def incumbent_neighbors():
    ti, si = TP_GRID.index(INCUMBENT[0]), SL_GRID.index(INCUMBENT[1])
    out = set()
    if ti > 0: out.add((TP_GRID[ti - 1], INCUMBENT[1]))
    if ti < len(TP_GRID) - 1: out.add((TP_GRID[ti + 1], INCUMBENT[1]))
    if si > 0: out.add((INCUMBENT[0], SL_GRID[si - 1]))
    if si < len(SL_GRID) - 1: out.add((INCUMBENT[0], SL_GRID[si + 1]))
    return out


def fnum(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
    """(profile, tp, sl) -> {'windows': {label: row}}. A repeated
    (profile,window,tp,sl) -- e.g. a rare crash-mid-write duplicate row from a
    restartable job -- resolves to the LAST occurrence (plain dict overwrite,
    file/row order = as read)."""
    cells = defaultdict(lambda: {'windows': {}})
    for r in rows:
        key = (r['profile'], fnum(r['tp']), fnum(r['sl']))
        cells[key]['windows'][r['window']] = r
    return cells


def timing_totals(rows):
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
    survivors, dropped = {}, []
    for key, c in cells.items():
        p_any = p_coll_any(c)
        (dropped.append((key, p_any)) if (p_any is not None and p_any > 0) else survivors.__setitem__(key, c))

    now_metrics = {}
    for key, c in survivors.items():
        row = c['windows'].get('22-now')
        if row is None:
            continue
        dd, med = fnum(row.get('worst_dd')), fnum(row.get('med_ret'))
        if dd is not None and med is not None:
            now_metrics[key] = (dd, med)

    if profile == 'apex':
        pareto_points = {}
        for key, (dd_now, med_now) in now_metrics.items():
            dds = [fnum(survivors[key]['windows'][w]['worst_dd']) for w in CANON_WINDOWS
                   if w in survivors[key]['windows'] and fnum(survivors[key]['windows'][w].get('worst_dd')) is not None]
            if dds:
                pareto_points[key] = (max(dds), med_now)
    else:
        pareto_points = dict(now_metrics)

    pareto_set = pareto_nondominated(pareto_points) if pareto_points else set()
    top12 = set(sorted(now_metrics, key=lambda k: now_metrics[k][0])[:TOP12_BY_DD])
    neigh = {INCUMBENT} | incumbent_neighbors()
    incumbent_group = {k for k in survivors if (k[1], k[2]) in neigh}

    carried = {k for k in (pareto_set | top12 | incumbent_group) if k in now_metrics}
    if len(carried) > CAP_PER_PROFILE:
        carried = set(sorted(carried, key=lambda k: now_metrics[k][0])[:CAP_PER_PROFILE])

    return dict(survivors=survivors, dropped=dropped, now_metrics=now_metrics,
                pareto_set=pareto_set, top12=top12, incumbent_group=incumbent_group, carried=carried)


def fmt_pct(v):
    return '' if v is None else f"{v:+.1f}%"


def fmt_dd(v):
    return '' if v is None else f"{v:.1f}%"


def write_table(f, title, keys, cells, now_metrics):
    f.write(f"\n### {title} ({len(keys)})\n\n")
    if not keys:
        f.write("_(none)_\n")
        return
    f.write("| tp | sl | 22-now dd/med | 2022 dd | 2024 dd | 2020_crash dd | p_coll any | |\n")
    f.write("|---|---|---|---|---|---|---|---|\n")
    ranked = sorted(keys, key=lambda k: now_metrics.get(k, (float('inf'), 0))[0])
    for key in ranked[:TOPN_TABLE]:
        _, tp, sl = key
        c = cells[key]
        dd, med = now_metrics.get(key, (None, None))
        w2022 = fnum(c['windows']['2022']['worst_dd']) if '2022' in c['windows'] else None
        w2024 = fnum(c['windows']['2024']['worst_dd']) if '2024' in c['windows'] else None
        wcrash = fnum(c['windows']['2020_crash']['worst_dd']) if '2020_crash' in c['windows'] else None
        p_any = p_coll_any(c)
        mark = '<- INCUMBENT' if (tp, sl) == INCUMBENT else ''
        f.write(f"| {tp:+.2f} | {sl:+.2f} | {fmt_dd(dd)} / {fmt_pct(med)} | {fmt_dd(w2022)} | "
                f"{fmt_dd(w2024)} | {fmt_dd(wcrash)} | {p_any if p_any is not None else ''} | {mark} |\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--glob', default='phaseA_*.csv')
    ap.add_argument('--include-smoke', action='store_true')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    out_path = args.out or os.path.join(OUT_DIR, 'phaseA_summary.md')
    rows, paths = load_rows(args.glob, exclude_smoke=not args.include_smoke)
    if not rows:
        raise SystemExit(f"no rows for glob={args.glob!r} under {OUT_DIR}")

    all_cells = merge_cells(rows)
    (tot_prep, tot_sim), by_profile = timing_totals(rows)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# Phase A summary -- mechanical prune (PREREG.md section 2)\n\n")
        f.write(f"Source files ({len(paths)}): " + ", ".join(os.path.basename(p) for p in paths) + "\n\n")
        f.write(f"Total rows: {len(rows)}  |  Total (profile,tp,sl) cells: {len(all_cells)}\n\n")
        f.write("## Timing totals\n\n")
        f.write(f"- ALL: prepare={tot_prep:.1f}s sim={tot_sim:.1f}s combined={tot_prep + tot_sim:.1f}s\n")
        for profile, (p, s) in sorted(by_profile.items()):
            f.write(f"- {profile}: prepare={p:.1f}s sim={s:.1f}s combined={p + s:.1f}s\n")

        for profile in sorted({k[0] for k in all_cells}):
            cells_p = {k: v for k, v in all_cells.items() if k[0] == profile}
            res = analyze_profile(profile, cells_p)
            f.write(f"\n## Profile: {profile}\n\n")
            f.write(f"Cells seen: {len(cells_p)}  |  Dropped (p_coll>0 on any window): {len(res['dropped'])}  |  "
                    f"Survivors: {len(res['survivors'])}  |  Ranked (has 22-now): {len(res['now_metrics'])}\n")
            if res['dropped']:
                f.write("\nDropped: " + ", ".join(
                    f"tp={k[1]:+.2f}/sl={k[2]:+.2f}(p_coll={p:.1f}%)" for k, p in res['dropped']) + "\n")
            write_table(f, "Top-20 by 22-now WorstDD", res['now_metrics'].keys(), res['survivors'], res['now_metrics'])
            write_table(f, "Pareto-nondominated set", res['pareto_set'], res['survivors'], res['now_metrics'])
            write_table(f, "CARRIED to Phase B (Pareto U top-12 U incumbent+neighbors, capped 15)",
                        res['carried'], res['survivors'], res['now_metrics'])

    print(f"[WROTE] {out_path}  ({len(rows)} rows, {len(paths)} file(s), {len(all_cells)} cells)")


if __name__ == '__main__':
    main()
