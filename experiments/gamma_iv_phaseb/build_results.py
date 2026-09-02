"""
Gamma x IV Phase B -- results collector (see DESIGN.md).

Reads the 4 real arms' sweep outputs (base/gamma/iv/gammaiv, cells flat_n4_a25
+ cascade_ref, clipped 13-window grid, N_ITER=300) plus the runtime IV
hit/miss coverage files emitted by monte_carlo.py's _prepare_window, and
writes experiments/gamma_iv_phaseb/results/phaseb_results.{json,txt}.

Also includes the archived full-span goff/gon 2-arm run as REFERENCE-ONLY
rows (different window grid -- 38 windows back to 2016-06-01, N_ITER=150 --
NOT paired with this run's clipped grid; included for context only).

This script reports numbers only. It does not render a PASS/FAIL verdict --
that is FABLE's call against the pre-registered PASS bars in DESIGN.md.

Usage: python experiments/gamma_iv_phaseb/build_results.py
ASCII-only output.
"""
import glob
import json
import os

REPO = r"C:\Development\Trader"
SWEEP_RESULTS = os.path.join(REPO, "experiments", "concentration_2x", "results")
HERE = os.path.join(REPO, "experiments", "gamma_iv_phaseb")
RESULTS = os.path.join(HERE, "results")

CELLS = ["flat_n4_a25", "cascade_ref"]
ARMS = ["base", "gamma", "iv", "gammaiv"]
METRIC_KEYS = [
    "n_paths", "p_2x_ever", "p_2x_before_50dd", "p_collapse",
    "median_days_2x", "median_compound_pct", "worst_dd_pct", "n_windows",
]


def load(path):
    with open(path, 'r') as f:
        return json.load(f)


def cell_metrics(agg, cell):
    for c in agg.get('cells', []):
        if c.get('cell') == cell:
            return c
    return None


def load_arm(arm):
    path = os.path.join(SWEEP_RESULTS, f"sweep_drill_phaseb_{arm}.json")
    if not os.path.exists(path):
        return None, path
    return load(path), path


def coverage_for_arm(arm):
    """Sum per-window ivwin_mc_*.json files under results/coverage_<arm>/.
    Returns (total_hits, total_misses, coverage_pct_or_None, per_window_list)."""
    cov_dir = os.path.join(RESULTS, f"coverage_{arm}")
    files = sorted(glob.glob(os.path.join(cov_dir, "ivwin_mc_*.json")))
    per_window = []
    th = tm = 0
    for fp in files:
        try:
            d = load(fp)
        except Exception:
            continue
        per_window.append(d)
        th += d.get('hits', 0)
        tm += d.get('misses', 0)
    tot = th + tm
    pct = (100.0 * th / tot) if tot else None
    return th, tm, pct, per_window


def fmt(v, pct=False, d=1):
    if v is None:
        return "   -   "
    return f"{v*100:.{d}f}%" if pct else f"{v:.{d}f}"


def main():
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 100)
    out("Gamma x IV Phase B -- 4-arm A/B results (real Polygon-IV panel, clipped 2022-08-01+ grid)")
    out("=" * 100)
    out("")
    out("COVERAGE DEGRADATION (mandatory statement, see DESIGN.md): panel "
        ".cache/polygon_iv/iv_ledger_polygon.parquet covers 2022-08-01..2026-05-15 only")
    out("(Polygon Developer plan 4yr lookback cap). 2021 and H1-2022, and hence 2020_crash / any")
    out("2021 window / the 5y-from-2021 / 10y windows, are UNAVAILABLE for IV coverage. The decisive")
    out("stress window that IS covered is the 2022-08..2022-10 bear leg (window roll_2022-08-01).")
    out("2022 total panel rows = 687 (thin vs 2024=2,527 / 2025=2,858) -- 2022 windows will show")
    out("materially lower coverage % than 2024+ windows.")
    out("")

    results = {"cells": {}, "reference_only": {}, "coverage": {}, "windows": None}

    for cell in CELLS:
        out("#" * 100)
        out(f"CELL: {cell}")
        out("#" * 100)
        results["cells"][cell] = {}
        header = (f"{'arm':<10} {'N(paths)':>10} {'P(2x)':>8} {'P(2x<50dd)':>11} "
                  f"{'collapse':>9} {'medD2x':>7} {'medCompound':>12} {'worstDD':>9} {'n_win':>6}")
        out(header)
        out("-" * len(header))
        base_row = None
        for arm in ARMS:
            agg, path = load_arm(arm)
            if agg is None:
                out(f"{arm:<10} MISSING -- expected {path}")
                results["cells"][cell][arm] = None
                continue
            cm = cell_metrics(agg, cell)
            if cm is None:
                out(f"{arm:<10} MISSING cell {cell} in {path}")
                results["cells"][cell][arm] = None
                continue
            results["cells"][cell][arm] = cm
            if results["windows"] is None:
                results["windows"] = {"n_windows": agg.get("n_windows"),
                                      "window_span": agg.get("window_span"),
                                      "n_iter": agg.get("n_iter")}
            if arm == "base":
                base_row = cm
            row = (f"{arm:<10} {cm.get('n_paths', 0):>10,} "
                   f"{fmt(cm.get('p_2x_ever'), True):>8} "
                   f"{fmt(cm.get('p_2x_before_50dd'), True):>11} "
                   f"{fmt(cm.get('p_collapse'), True):>9} "
                   f"{fmt(cm.get('median_days_2x'), False, 0):>7} "
                   f"{fmt(cm.get('median_compound_pct'), False, 0):>12} "
                   f"{fmt(cm.get('worst_dd_pct'), False, 1):>9} "
                   f"{cm.get('n_windows', 0):>6}")
            # Delta vs base (factual arithmetic only, no verdict).
            if base_row is not None and arm != "base":
                dc = cm.get('median_compound_pct')
                bc_ = base_row.get('median_compound_pct')
                dd = cm.get('worst_dd_pct')
                bd = base_row.get('worst_dd_pct')
                if dc is not None and bc_ is not None and bc_ != 0:
                    row += f"   [compound_ratio_vs_base={dc / bc_:.2f}x"
                if dd is not None and bd is not None:
                    row += f"  dd_delta_vs_base={dd - bd:+.1f}pp]"
            out(row)
        out("")

    out("#" * 100)
    out("IV COVERAGE (per-arm, summed across per-window runtime hit/miss counters)")
    out("#" * 100)
    cov_header = f"{'arm':<10} {'hits':>8} {'misses':>8} {'coverage%':>10} {'n_windows_reporting':>20}"
    out(cov_header)
    out("-" * len(cov_header))
    for arm in ARMS:
        th, tm, pct, per_window = coverage_for_arm(arm)
        results["coverage"][arm] = {"hits": th, "misses": tm, "coverage_pct": pct,
                                    "per_window": per_window}
        pct_s = f"{pct:.1f}%" if pct is not None else "  n/a  "
        out(f"{arm:<10} {th:>8} {tm:>8} {pct_s:>10} {len(per_window):>20}")
    out("")
    out("Per-window IV coverage (iv / gammaiv arms; base / gamma never touch the panel):")
    for arm in ("iv", "gammaiv"):
        th, tm, pct, per_window = coverage_for_arm(arm)
        if not per_window:
            out(f"  [{arm}] no coverage files found under results/coverage_{arm}/")
            continue
        out(f"  [{arm}]")
        for d in sorted(per_window, key=lambda x: x.get('window', '')):
            w = d.get('window', '?')
            h, m = d.get('hits', 0), d.get('misses', 0)
            t = h + m
            p = (100.0 * h / t) if t else 0.0
            out(f"    {w:<20} hits={h:>5} misses={m:>5} coverage={p:5.1f}%")
    out("")

    out("#" * 100)
    out("REFERENCE-ONLY: archived full-span 2-arm GAMMA_AWARE run (2016-06-01 grid, 38 windows,")
    out("N_ITER=150) -- NOT paired with this run's clipped 13-window grid. Context only.")
    out("#" * 100)
    for tag, fname in (("goff", "sweep_drill_goff.json"), ("gon", "sweep_drill_gon.json")):
        p = os.path.join(SWEEP_RESULTS, fname)
        if not os.path.exists(p):
            out(f"  [{tag}] MISSING {p}")
            continue
        agg = load(p)
        results["reference_only"][tag] = {}
        for cell in CELLS:
            cm = cell_metrics(agg, cell)
            results["reference_only"][tag][cell] = cm
            if cm:
                out(f"  [{tag}] {cell:<14} P(2x)={fmt(cm.get('p_2x_ever'), True)} "
                    f"P(2x<50dd)={fmt(cm.get('p_2x_before_50dd'), True)} "
                    f"collapse={fmt(cm.get('p_collapse'), True)} "
                    f"medCompound={fmt(cm.get('median_compound_pct'), False, 0)} "
                    f"worstDD={fmt(cm.get('worst_dd_pct'), False, 1)}")
    out("")
    out("=" * 100)

    os.makedirs(RESULTS, exist_ok=True)
    txt_path = os.path.join(RESULTS, "phaseb_results.txt")
    json_path = os.path.join(RESULTS, "phaseb_results.json")
    with open(txt_path, 'w', encoding='ascii', errors='replace') as f:
        f.write("\n".join(lines) + "\n")
    with open(json_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n[WROTE] {txt_path}")
    print(f"[WROTE] {json_path}")


if __name__ == "__main__":
    main()
