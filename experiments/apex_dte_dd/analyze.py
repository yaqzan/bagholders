#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compare the apex-sprint concentration frontier across DTE arms.

Reads the concentration_2x sweep JSONs (one per DTE arm, written by
experiments/concentration_2x/sweep.py --tag <arm>) and prints a per-cell
comparison on the user's metrics: P(2x), median days-to-2x, median compound,
worst DD, collapse%. Then applies the collapse<=10% gate and ranks.

Usage: python experiments/apex_dte_dd/analyze.py [--collapse-max 0.10] \
         [--arms dte30,dte15,dte15s70]
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
RES = REPO / "experiments" / "concentration_2x" / "results"

ARM_LABEL = {
    'dte30':    '30DTE  SL-.70 (Core sprint)',
    'dte15':    '15DTE  SL-.865 (LIVE apex)',
    'dte15s70': '15DTE  SL-.70  (pure-DTE)',
}

def load(tag: str) -> dict:
    """Return {cell_name: metrics_dict} for an arm, or {} if missing."""
    p = RES / f"sweep_drill_{tag}.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    rows = d.get('results') or d.get('cells') or []
    out = {}
    for r in rows:
        name = r.get('cell') or r.get('name')
        if name:
            out[name] = r
    return out

def g(m, *keys, default=None):
    for k in keys:
        if k in m and m[k] is not None:
            return m[k]
    return default

def fmt_pct(x):
    return f"{x*100:5.1f}%" if isinstance(x, (int, float)) else "   -- "

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--collapse-max', type=float, default=0.10)
    ap.add_argument('--arms', default='dte30,dte15,dte15s70')
    args = ap.parse_args()
    arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    data = {a: load(a) for a in arms}

    # union of cells, ordered by the standard frontier
    order = ['flat_n2_a50', 'flat_n3_a33', 'flat_n4_a25', 'flat_n5_a20',
             'flat_n7_a14', 'flat_n10_a10', 'cascade_ref']
    cells = [c for c in order if any(c in data[a] for a in arms)]
    extra = sorted({c for a in arms for c in data[a]} - set(cells))
    cells += extra

    print(f"\n{'='*104}\nAPEX SPRINT FRONTIER  (collapse gate <= {args.collapse_max*100:.0f}%)  "
          f"metric: P(2x within 2y) / median cal-days-to-2x / median compound / worst DD / collapse\n{'='*104}")
    rows_for_gate = []
    for cell in cells:
        print(f"\n  {cell}")
        for a in arms:
            m = data[a].get(cell)
            if not m:
                print(f"    {ARM_LABEL.get(a,a):30s}  (no data)")
                continue
            p2x   = g(m, 'p_2x_ever', 'p_2x')
            dd2x  = g(m, 'median_days_2x', 'med_days_2x')
            comp  = g(m, 'median_compound_pct', 'med_compound_pct')
            wdd   = g(m, 'worst_dd_pct')
            coll  = g(m, 'p_collapse', 'p_col')
            collf = coll/100.0 if (isinstance(coll,(int,float)) and coll > 1.0) else coll  # tolerate 0..1 or 0..100
            wddf  = wdd/100.0 if (isinstance(wdd,(int,float)) and wdd > 1.5) else wdd
            ok = (collf is not None and collf <= args.collapse_max)
            mark = ' ' if ok else 'x'  # x = fails collapse gate
            d2x_s = f"{dd2x:5.0f}d" if isinstance(dd2x,(int,float)) else "  -- "
            comp_s = f"{comp:+8.0f}%" if isinstance(comp,(int,float)) else "    -- "
            print(f"   {mark}{ARM_LABEL.get(a,a):30s}  P2x {fmt_pct(p2x)}  {d2x_s}  comp {comp_s}  "
                  f"DD {fmt_pct(wddf)}  coll {fmt_pct(collf)}")
            if ok:
                rows_for_gate.append((cell, a, p2x or 0, dd2x or 9e9, comp or 0, wddf or 0, collf or 0))

    if rows_for_gate:
        print(f"\n{'='*104}\nPASS collapse<=%.0f%% — ranked by median compound (desc), then days-to-2x (asc):\n{'='*104}" % (args.collapse_max*100))
        for cell,a,p2x,d2x,comp,wdd,coll in sorted(rows_for_gate, key=lambda r:(-r[4], r[3])):
            print(f"  {cell:14s} {ARM_LABEL.get(a,a):30s}  comp {comp:+8.0f}%  P2x {p2x*100:5.1f}%  "
                  f"{d2x:5.0f}d  DD {wdd*100:4.0f}%  coll {coll*100:4.1f}%")
        print("\nRanked by DD (asc) among the same pass-set:")
        for cell,a,p2x,d2x,comp,wdd,coll in sorted(rows_for_gate, key=lambda r:(r[5],)):
            print(f"  {cell:14s} {ARM_LABEL.get(a,a):30s}  DD {wdd*100:4.0f}%  comp {comp:+8.0f}%  "
                  f"P2x {p2x*100:5.1f}%  {d2x:5.0f}d  coll {coll*100:4.1f}%")
    else:
        print("\n(no arms parsed yet — results files not present)")

if __name__ == '__main__':
    main()
