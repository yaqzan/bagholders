#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Aggregate the Stage-3 gate MC_RESULTS_JSON dumps into one table.

Reads each cell's per-window dump (med_ret, worst_dd, p_coll) and INDEPENDENTLY
recomputes collapse% from the per-iteration `finals` array (final <= 20% of
$50k start) as a cross-check on the engine's reported p_coll. Headlines 5y and
22-now. Flags any cell with collapse>0 on any window.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
START_CASH = 50_000.0
COLLAPSE_FRAC = 0.20  # final <= 20% of start

CELLS = [
    ("flat_n10_a10", "flat_n10_a10.json"),
    ("flat_n4_a25",  "flat_n4_a25.json"),
    ("flat_n3_a33",  "flat_n3_a33.json"),
    ("baseline_apex", "baseline_apex.json"),
]
WINDOWS = ["2020", "2020_crash", "2021", "2022", "2023", "2024",
           "2025", "dip", "22-now", "5y"]


def load_cell(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def recompute_collapse(win_dump: dict):
    """Independent collapse% from per-iter finals (final <= 20% of start)."""
    paths = win_dump.get("paths")
    if not paths:
        return None, None
    finals = paths.get("finals") or []
    sc = paths.get("starting_cash", START_CASH) or START_CASH
    if not finals:
        return None, None
    n = len(finals)
    coll = sum(1 for f in finals if f is not None and f <= sc * COLLAPSE_FRAC)
    return coll / n * 100.0, n


def fmt_pct(v, width=14):
    if v is None:
        return f"{'n/a':>{width}}"
    # compact scientific for huge compounds
    if abs(v) >= 1e6:
        return f"{v:>{width}.3e}"
    return f"{v:>+{width},.1f}"


def main():
    print("=" * 130)
    print("STAGE-3 GATE — fast-2x concentration candidates vs production-Apex baseline")
    print("v74 Apex sleeve | 30 DTE | calls-only | SPREAD_TILT OFF | N=500 | collapse = final <= 20% of $50k")
    print("=" * 130)

    cell_data = {}
    for name, fn in CELLS:
        cell_data[name] = load_cell(HERE / fn)

    any_collapse = {name: False for name, _ in CELLS}

    for name, fn in CELLS:
        d = cell_data[name]
        print(f"\n### {name}" + ("" if d else "   [NO RESULTS YET]"))
        if not d:
            continue
        meta = d.get("_meta", {})
        print(f"   meta: hold_cal_days={meta.get('hold_cal_days')} "
              f"nominal_cal_dte={meta.get('nominal_cal_dte')} n_iter={meta.get('n_iter')}")
        print(f"   {'window':<12} {'medCompound%':>16} {'WorstDD%':>10} "
              f"{'collapse%(eng)':>14} {'collapse%(recompute)':>22} {'nPaths':>8}")
        for w in WINDOWS:
            r = d.get(w)
            if not r:
                print(f"   {w:<12} {'(missing)':>16}")
                continue
            med = r.get("med_ret")
            wdd = r.get("worst_dd")
            pcoll = r.get("p_coll")
            rcoll, npaths = recompute_collapse(r)
            if (pcoll and pcoll > 0) or (rcoll and rcoll > 0):
                any_collapse[name] = True
            collstr_e = f"{pcoll:>13.2f}%" if pcoll is not None else f"{'n/a':>14}"
            collstr_r = f"{rcoll:>21.2f}%" if rcoll is not None else f"{'n/a':>22}"
            print(f"   {w:<12} {fmt_pct(med,16)} {wdd:>9.1f}% "
                  f"{collstr_e} {collstr_r} {npaths if npaths else '-':>8}")

    # ---- compact comparison table (the deliverable) ----
    print("\n" + "=" * 130)
    print("COMPACT TABLE — medCompound% / WorstDD% / collapse% per (cell x window)")
    print("=" * 130)
    hdr = f"{'window':<12}"
    for name, _ in CELLS:
        hdr += f" | {name:^34}"
    print(hdr)
    sub = f"{'':<12}"
    for _ in CELLS:
        sub += f" | {'medCmp%':>11} {'wDD%':>7} {'coll%':>6} "
    print(sub)
    print("-" * len(hdr))
    for w in WINDOWS:
        row = f"{w:<12}"
        for name, _ in CELLS:
            d = cell_data[name]
            r = d.get(w) if d else None
            if not r:
                row += f" | {'--':>11} {'--':>7} {'--':>6} "
                continue
            med = r.get("med_ret")
            wdd = r.get("worst_dd")
            pcoll = r.get("p_coll")
            meds = (f"{med:>11.2e}" if med is not None and abs(med) >= 1e6
                    else (f"{med:>+11.1f}" if med is not None else f"{'n/a':>11}"))
            wdds = f"{wdd:>7.1f}" if wdd is not None else f"{'n/a':>7}"
            colls = f"{pcoll:>6.2f}" if pcoll is not None else f"{'n/a':>6}"
            row += f" | {meds} {wdds} {colls} "
        print(row)

    # ---- verdicts ----
    print("\n" + "=" * 130)
    print("VERDICTS (T6 collapse=0 on EVERY window = binding pass/fail for a collapse-safe profile)")
    print("=" * 130)
    for name, _ in CELLS:
        d = cell_data[name]
        if not d:
            print(f"  {name:<16} : NO RESULTS")
            continue
        # headline 5y + 22-now
        def hl(w):
            r = d.get(w)
            if not r:
                return "n/a"
            med = r.get("med_ret"); wdd = r.get("worst_dd"); pc = r.get("p_coll")
            ms = f"{med:.2e}%" if med is not None and abs(med) >= 1e6 else (f"{med:+.1f}%" if med is not None else "n/a")
            return f"medCmp {ms} / DD {wdd:.1f}% / coll {pc:.2f}%"
        safe = not any_collapse[name]
        verdict = "COLLAPSE-SAFE on all windows: YES" if safe else "COLLAPSE-SAFE: *** NO — DISQUALIFIED ***"
        print(f"  {name:<16} : {verdict}")
        print(f"  {'':<16}   5y     -> {hl('5y')}")
        print(f"  {'':<16}   22-now -> {hl('22-now')}")


if __name__ == "__main__":
    main()
