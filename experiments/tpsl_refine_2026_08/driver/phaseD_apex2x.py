#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase D Apex 2x-race read (PREREG.md section 6: "Apex finalist: full 2x-race
harness (P(2x), median cal-days, collapse) vs live cert").

Reuses experiments/concentration_2x/sweep.py's METRIC layer (monthly_windows,
compute_cell_metrics, _paths_from_result, CALENDAR_DAYS_PER_TRADING_DAY) —
imported read-only, never edited — but does NOT reuse its in-process
load-once-per-window DRIVER (run_window_inproc), because that driver shares
ONE `_prepare_window()` ctx across multiple sizing-only "cells" via
`_apply_cell_params`, which is INVALID here: TP/SL barriers bake AT prepare
time (PREREG section 7 / LESSONS.md), so two different (TP,SL) arms sharing
one ctx would mean the second arm silently simulates against the FIRST arm's
barriers (the exact "stale barrier" trap LESSONS.md documents for MP pools,
here at the prepare layer instead).

Fix: each (TP,SL) arm's TP/SL is set via env (TP_BASE_OV/TP_STRESS_OV/
SL_BASE_OV/SL_STRESS_OV — flat, stress=base) and every (arm, monthly-rolled
window) cell runs as its OWN subprocess, via the repo's existing shared
runner experiments/_mc_pinned_runner.run_one_window (already used by 4 other
Block-D evidence drivers incl run_p03_evidence.py, which is also where the
Apex env recipe below is cross-checked against). A fresh subprocess means a
fresh top-level `import monte_carlo`, so TP_BASE_OV etc are read correctly
at THAT process's import time — no in-process sigma-patching (mc_patch.
set_tpsl) is needed or used here. run_one_window is independently resumable
per (arm, window) cell (skip-if-JSON-exists) and timeout-guarded, so this
driver needs no extra checkpoint layer of its own.

WINDOW CONSTRUCTION: sweep.py's OWN defaults (step_months=1, horizon_days=730,
hist_start=HIST_START 2016-06-01) — this is deliberately NOT re-derived or
overridden, because it is exactly the invocation that produced the live Apex
cert PREREG.md section 1 cites (P(2x)=72%, med~191d, worst DD=76%,
collapse=0/12): experiments/apex_dte_dd/FINDINGS.md "Result 4 — N=300
full-roll confirm... 30-DTE, N=300 x full monthly roll (step-1, ~113
windows)" and "N=500 ship-gate confirm (full monthly roll, 56,500
paths/cell)" = 500 x 113. Using the same construction here keeps this run's
numbers comparable to that cert rather than an apples-to-oranges roll.

Usage:
    python experiments/tpsl_refine_2026_08/driver/phaseD_apex2x.py \
        --job NAME --cells "tp,sl;tp,sl;..." [--n-iter 500] [--cpu 6] \
        [--step-months 1] [--horizon-days 730] [--max-windows 0]

    # validation smoke (REQUIRED before any real run) — ONE tiny arm
    # (baseline only), ONE rolled window, N=50:
    python experiments/tpsl_refine_2026_08/driver/phaseD_apex2x.py --job smoke --probe

Baseline (0.30,-0.70) is ALWAYS auto-included as an arm (reuses phaseD_run.
parse_cells_arg — same separator convention: comma inside a cell, semicolon
between cells).

HARD RULE: no production file edits. monte_carlo.py is invoked as a bare
subprocess with env overrides (the documented interface,
.claude/skills/run-monte-carlo/SKILL.md) — never modified. This script never
git-commits anything.

Output: out/phaseD_apex2x_<job>.csv (one row per arm) + a compact
out/phaseD_apex2x_<job>_summary.md. Per-(arm,window) raw JSONs land under
out/phaseD_apex2x_results/<job>/<arm_tag>/<window_label>.json (resumable
state — see run_one_window's own skip-if-exists behavior).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import asdict
from datetime import date

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../driver
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))    # repo root
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} has no monte_carlo.py "
    f"(computed from __file__={__file__!r}) -- fix the dirname() chain above"
)

EXP_DIR = os.path.dirname(_THIS_DIR)
OUT_DIR = os.path.join(EXP_DIR, 'out')
RESULTS_ROOT = os.path.join(OUT_DIR, 'phaseD_apex2x_results')

# Import the FINALIST cell parser from the sibling driver (baseline-auto-include
# + 'tp,sl;tp,sl' parsing) -- pure function, no DB/mc side effects at import
# time, so reusing it here is safe and avoids a second copy of the same logic.
from phaseD_run import parse_cells_arg, BASELINE_CELL   # noqa: E402

STARTING_CASH = 50_000.0   # matches both sweep.py's own default AND monte_carlo's STARTING_CASH_OV default

CSV_FIELDS = [
    'arm_tag', 'tp', 'sl', 'is_baseline', 'n_windows_ok', 'n_windows_total',
    'n_iter_per_window', 'n_paths', 'p_2x_ever', 'p_2x_before_50dd',
    'p_collapse', 'median_days_2x', 'p25_days_2x', 'p75_days_2x',
    'median_compound_pct', 'worst_dd_pct', 'gross_pct',
]


def _import_c2x_sweep():
    """Import experiments/concentration_2x/sweep.py as module name 'sweep'
    (read-only reuse of its metrics layer: monthly_windows, compute_cell_metrics,
    _paths_from_result, CALENDAR_DAYS_PER_TRADING_DAY, HIST_START). Namespaced
    import + a path-identity assert so an accidental same-named module
    elsewhere on sys.path is caught loudly instead of silently miscomputing
    every metric in this file."""
    c2x_dir = os.path.join(_REPO_ROOT, 'experiments', 'concentration_2x')
    if c2x_dir not in sys.path:
        sys.path.insert(0, c2x_dir)
    import sweep as c2x_sweep   # noqa
    resolved = os.path.abspath(c2x_sweep.__file__)
    expected = os.path.abspath(os.path.join(c2x_dir, 'sweep.py'))
    assert resolved == expected, (
        f"imported 'sweep' module resolved to {resolved!r}, expected {expected!r} -- "
        f"a same-named module earlier on sys.path shadowed concentration_2x/sweep.py; "
        f"STOP, do not proceed with the wrong metrics implementation")
    return c2x_sweep


def arm_tag(tp, sl):
    return f"tp{tp:+.2f}_sl{sl:+.2f}"


def build_env_overrides(mc_patch, tp, sl):
    """Apex sizing (mc_patch.APEX_ENV_DIFF -- NOMINAL_CAL_DTE/HOLD_CAL_DAYS/
    TIER_*_OV/MAX_POSITIONS*/GROSS_PREMIUM_CAP/CALL_PREMIUM_CAP, verbatim from
    the shipped P0.3 recipe) + the frozen pins (MC_NO_DB_PERSIST/LIQUIDITY_FLOOR/
    PYTHONIOENCODING/MC_RETURN_PATHS) + this arm's flat (stress=base) TP/SL via
    the *_OV envs monte_carlo.py reads at import time (verified 2026-08-10:
    monte_carlo.py:383-386 `TP_BASE = float(os.environ.get('TP_BASE_OV', ...))`
    etc) -- NOT mc_patch.set_tpsl's post-import monkeypatch, which only affects
    an already-imported module in THIS process and would not reach a subprocess."""
    env = dict(mc_patch.FROZEN_PINS)
    env.update(mc_patch.APEX_ENV_DIFF)
    env['STARTING_CASH_OV'] = str(STARTING_CASH)
    env['TP_BASE_OV'] = str(tp)
    env['TP_STRESS_OV'] = str(tp)     # flat -- Phase D confirmation only, no regime conditioning
    env['SL_BASE_OV'] = str(sl)
    env['SL_STRESS_OV'] = str(sl)
    return env


def run_arm(run_one_window, c2x_sweep, env_base, windows, n_iter, pin_commit,
           results_dir, timeout_s, cpu):
    """Run every (window) cell for ONE arm (its TP/SL already baked into
    env_base), pool per-iteration paths across all windows that completed,
    and compute CellMetrics. Returns (CellMetrics, n_windows_ok)."""
    pooled = []
    n_ok = 0
    for (wlabel, ws, we) in windows:
        win_env = dict(env_base)
        win_env['WIN_START'] = ws.isoformat()
        win_env['WIN_END'] = we.isoformat()
        win_env['WIN_LABEL'] = wlabel
        out_path = os.path.join(results_dir, f'{wlabel}.json')
        res = run_one_window(win_env, wlabel, n_iter, out_path, pin_commit,
                             timeout_s=timeout_s, cpu=cpu)
        if res is None:
            continue
        wdata = res.get(wlabel)
        if wdata is None or 'paths' not in wdata:
            print(f"    [warn] window={wlabel}: no 'paths' in result (MC_RETURN_PATHS "
                  f"must not have taken effect) -- skipped", flush=True)
            continue
        pooled.extend(c2x_sweep._paths_from_result(wdata['paths'], STARTING_CASH))
        n_ok += 1
    return pooled, n_ok


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--job', required=True, help='job name -- namespaces out/phaseD_apex2x_<job>.csv etc')
    p.add_argument('--cells', default=None,
                    help="'tp,sl;tp,sl;...' Apex finalist(s). Baseline (0.30,-0.70) is ALWAYS "
                         "included as an extra arm even if omitted.")
    p.add_argument('--n-iter', type=int, default=500, help='per-window N_ITER (ship-tier default 500)')
    p.add_argument('--cpu', type=int, default=6, help='MC_WORKERS per subprocess (frozen-pin default 6)')
    p.add_argument('--timeout-s', type=int, default=3600, help='per-(arm,window) subprocess wall ceiling')
    p.add_argument('--step-months', type=int, default=1,
                    help='monthly-roll step (default 1 = the full roll that produced the live cert)')
    p.add_argument('--horizon-days', type=int, default=730,
                    help='per-start horizon in calendar days (default 730, matches sweep.py HORIZON_DAYS)')
    p.add_argument('--hist-start', default='', help='override roll history start (YYYY-MM-DD); default sweep.py HIST_START')
    p.add_argument('--max-windows', type=int, default=0, help='cap number of rolled windows (0=all; debug/validation)')
    p.add_argument('--probe', action='store_true',
                    help='VALIDATION mode: baseline-only, N=50, 1 rolled window -- proves the '
                         'subprocess env path + t2x pooling work end-to-end. Overrides --cells/--n-iter/--max-windows.')
    return p.parse_args()


def main():
    args = parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    import mc_patch   # driver/mc_patch.py -- FROZEN_PINS + APEX_ENV_DIFF only, read-only reuse
    from experiments._mc_pinned_runner import resolve_pinned_version, run_one_window  # noqa: E402
    c2x_sweep = _import_c2x_sweep()

    if args.probe:
        cells = [BASELINE_CELL]
        n_iter = 50
        max_windows = 1
        print("[PROBE MODE] baseline-only, N=50, 1 rolled window -- validation run", flush=True)
    else:
        cells = parse_cells_arg(args.cells)
        n_iter = args.n_iter
        max_windows = args.max_windows or 0

    hist_start = date.fromisoformat(args.hist_start) if args.hist_start else c2x_sweep.HIST_START
    windows = c2x_sweep.monthly_windows(hist_start=hist_start, step_months=args.step_months,
                                        horizon_days=args.horizon_days)
    if max_windows:
        windows = windows[:max_windows]
    if not windows:
        raise SystemExit("monthly_windows() produced zero windows -- check --hist-start/--horizon-days")

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[phaseD_apex2x] job={args.job} cells={cells} n_iter={n_iter} "
         f"n_windows={len(windows)} (step_months={args.step_months} horizon_days={args.horizon_days} "
         f"hist_start={hist_start}) pinned_version={pin_commit} (id={pin_id})", flush=True)

    results_job_dir = os.path.join(RESULTS_ROOT, args.job)
    all_rows = []
    t0 = time.time()
    for tp, sl in cells:
        tag = arm_tag(tp, sl)
        print(f"\n[ARM] {tag}  (is_baseline={(tp, sl) == BASELINE_CELL})", flush=True)
        env_base = build_env_overrides(mc_patch, tp, sl)
        arm_dir = os.path.join(results_job_dir, tag)
        pooled, n_ok = run_arm(run_one_window, c2x_sweep, env_base, windows, n_iter,
                               pin_commit, arm_dir, args.timeout_s, args.cpu)
        m = c2x_sweep.compute_cell_metrics(tag, pooled, n_windows=n_ok, n_iter_per_window=n_iter,
                                           gross_pct=1.0, bars_to_calendar=c2x_sweep.CALENDAR_DAYS_PER_TRADING_DAY)
        rec = asdict(m)
        rec['arm_tag'] = tag
        rec['tp'] = tp
        rec['sl'] = sl
        rec['is_baseline'] = (tp, sl) == BASELINE_CELL
        rec['n_windows_ok'] = n_ok
        rec['n_windows_total'] = len(windows)
        all_rows.append(rec)
        print(f"[ARM DONE] {tag} n_windows_ok={n_ok}/{len(windows)} n_paths={m.n_paths} "
             f"p_2x_ever={m.p_2x_ever:.3f} p_2x_before_50dd={m.p_2x_before_50dd:.3f} "
             f"p_collapse={m.p_collapse:.3f} med_days_2x={m.median_days_2x} "
             f"med_compound={m.median_compound_pct} worst_dd={m.worst_dd_pct}", flush=True)

    elapsed = time.time() - t0

    csv_path = os.path.join(OUT_DIR, f'phaseD_apex2x_{args.job}.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for rec in all_rows:
            w.writerow({k: rec.get(k) for k in CSV_FIELDS})
    print(f"\n[WROTE] {csv_path}", flush=True)

    md_path = os.path.join(OUT_DIR, f'phaseD_apex2x_{args.job}_summary.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# Phase D Apex 2x-race read -- job={args.job}\n\n")
        f.write(f"N_ITER={n_iter}  windows={len(windows)} (step_months={args.step_months}, "
               f"horizon_days={args.horizon_days}, hist_start={hist_start})  "
               f"pinned_version={pin_commit} (id={pin_id})  wall={elapsed:.0f}s\n\n")
        f.write("cf. live cert (PREREG.md section 1, section 3 acceptance): P(2x)>=72% AND "
               "worst DD<=76% AND median cal-days-to-2x<=200 AND collapse 0/12, with >=1 of "
               "the first three strictly better than the live cert. This file reports numbers "
               "only -- the SHIP/FLAG/NO-SHIP verdict is rendered by analyze_phaseD.py.\n\n")
        f.write("| arm | baseline | n_win_ok | n_paths | P(2x ever) | P(2x<50dd) | p_collapse | "
               "med days-to-2x | med compound | worst DD |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for rec in sorted(all_rows, key=lambda r: (not r['is_baseline'], r['tp'])):
            def _fmt(v, suffix=''):
                return '' if v is None else f"{v:.1f}{suffix}"
            f.write(f"| {rec['arm_tag']} | {'Y' if rec['is_baseline'] else ''} | "
                   f"{rec['n_windows_ok']}/{rec['n_windows_total']} | {rec['n_paths']} | "
                   f"{_fmt((rec['p_2x_ever'] or 0) * 100, '%')} | "
                   f"{_fmt((rec['p_2x_before_50dd'] or 0) * 100, '%')} | "
                   f"{_fmt((rec['p_collapse'] or 0) * 100, '%')} | "
                   f"{_fmt(rec['median_days_2x'])} | "
                   f"{_fmt(rec['median_compound_pct'], '%')} | "
                   f"{_fmt(rec['worst_dd_pct'], '%')} |\n")
    print(f"[WROTE] {md_path}", flush=True)

    if args.probe:
        base = all_rows[0]
        ok = base['n_paths'] == n_iter * base['n_windows_ok'] and base['n_windows_ok'] >= 1 and base['n_paths'] > 0
        print(f"\n[PROBE RESULT] {'OK' if ok else 'FAIL'} -- n_paths={base['n_paths']} "
             f"(expected {n_iter}*{base['n_windows_ok']}={n_iter * base['n_windows_ok']}) "
             f"p_2x_ever={base['p_2x_ever']} p_collapse={base['p_collapse']} "
             f"med_days_2x={base['median_days_2x']} worst_dd_pct={base['worst_dd_pct']}", flush=True)
        if not ok:
            raise SystemExit("[PROBE RESULT] FAIL -- subprocess env path or t2x pooling did not "
                             "produce the expected path count; see out/phaseD_apex2x_results/ "
                             "and the per-window .log files for the raw subprocess output")


if __name__ == '__main__':
    main()
