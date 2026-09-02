#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Confirm-phase Apex 2x-race read (PREREG.md section 1 "the 113-window 2x-race
harness for the Apex finalist"; TASK.md's locked Apex confirm rule). ADAPTED
from experiments/tpsl_refine_2026_08/driver/phaseD_apex2x.py: same metrics
layer (concentration_2x/sweep.py's monthly_windows / compute_cell_metrics /
_paths_from_result, imported read-only via phaseD_apex2x._import_c2x_sweep),
same per-(arm,window) subprocess mechanism (experiments._mc_pinned_runner.
run_one_window, resumable skip-if-json-exists), same reason for NOT reusing
concentration_2x's in-process load-once driver (TP/SL bakes at prepare time
-- but here the axis that varies is SIZING, so the analogous prepare-time
concern doesn't strictly apply; run_one_window's resumability + per-cell
timeout guard is reused anyway for architecture consistency and because a
~113-window x N=500 roll per arm is exactly the workload that mechanism
exists for).

WHAT'S DIFFERENT FROM phaseD_apex2x.py: the swept axis is Apex SIZING
(MAX_POSITIONS x flat tier fraction), not TP/SL -- TP/SL is FIXED at the
shipped pin (0.10/-0.60) for every arm here (PREREG section 2: "TP/SL frozen
at shipped values... this campaign never calls set_tpsl()"). And the
BASELINE ARM (n10/f0.10 @ TP=0.10/SL=-0.60) is BIT-IDENTICAL config to the
tpsl campaign's own #379 winner arm ("tp+0.10_sl-0.60"), whose 113 per-window
JSONs are ALREADY on disk under experiments/tpsl_refine_2026_08/out/
phaseD_apex2x_results/apex_confirm/tp+0.10_sl-0.60/ -- so this driver reads
them DIRECTLY (zero recompute, task spec) instead of ever subprocessing a
baseline arm of its own. Only the (up to 3) finalist SIZING arms actually run
a fresh subprocess-per-window roll.

Usage:
    python experiments/alloc_retune_2026_08/driver/allocC_apex2x.py \
        --job NAME --cells "n,frac;n,frac;..." [--n-iter 500] [--cpu 6] \
        [--step-months 1] [--horizon-days 730] [--max-windows 0]

    # validation smoke (REQUIRED before any real run) -- baseline read FULLY
    # from disk (zero recompute, all 113 windows) + ONE finalist sizing arm
    # (PROBE_CELL) via subprocess, ONE rolled window, N=50:
    python experiments/alloc_retune_2026_08/driver/allocC_apex2x.py --job smoke --probe

Baseline (n10/f0.10, APEX_BASELINE from allocA_run.py) is ALWAYS included as
an arm in every invocation -- read from disk, never subprocess-computed, and
NEVER capped by --max-windows/--probe (it costs nothing: file I/O only).

HARD RULE: no production file edits. monte_carlo.py is invoked as a bare
subprocess with env overrides for the finalist arms only (the documented
interface, .claude/skills/run-monte-carlo/SKILL.md) -- never modified. This
script never git-commits anything.

Output: out/allocC_apex2x_<job>.csv (one row per arm, baseline + finalists)
+ out/allocC_apex2x_<job>_summary.md. Per-(arm,window) raw JSONs for the
FINALIST arms land under out/allocC_apex2x_results/<job>/<arm_tag>/
<window_label>.json (resumable -- run_one_window's own skip-if-exists). The
baseline arm never writes here -- it reads the tpsl campaign's existing
directory in place.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import date

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../alloc_retune_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                                        # .../alloc_retune_2026_08
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)                                 # .../experiments
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)                               # repo root
_TPSL_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'tpsl_refine_2026_08', 'driver')

for _d in (_THIS_DIR, _TPSL_DRIVER_DIR, _REPO_ROOT):
    if _d not in sys.path:
        sys.path.insert(0, _d)

assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} has no monte_carlo.py "
    f"(computed from __file__={__file__!r}) -- fix the dirname() chain above"
)
assert os.path.isfile(os.path.join(_TPSL_DRIVER_DIR, 'phaseD_apex2x.py')), (
    f"tpsl driver phaseD_apex2x.py not found at {_TPSL_DRIVER_DIR!r} -- this file adapts it "
    f"by reusing its _import_c2x_sweep/arm_tag helpers read-only, never copying"
)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
RESULTS_ROOT = os.path.join(OUT_DIR, 'allocC_apex2x_results')

# Read-only reuse (never copied): allocA_run's APEX_BASELINE (n,frac) =
# (10, 0.10), the single source of truth already used by allocA/B/allocC_run;
# phaseD_apex2x's small helpers (metrics-layer importer + its OWN tp/sl-style
# arm_tag, aliased so it can't collide with THIS file's n/frac arm_tag below).
from allocA_run import APEX_BASELINE                                         # noqa: E402
from phaseD_apex2x import _import_c2x_sweep, arm_tag as _tpsl_tp_sl_arm_tag   # noqa: E402

STARTING_CASH = 50_000.0   # matches sweep.py's own default AND monte_carlo's STARTING_CASH_OV default

# TP/SL is FIXED for every arm in this campaign (PREREG section 2: frozen at
# the shipped values). This IS the shipped Apex pin (algorithm_versions/
# portfolio_profiles.json "apex": tp_base=0.1, sl_base=-0.6) -- the same pin
# allocA_run.py/allocB_run.py/allocC_run.py set for their own Apex flat-MC
# arms, and the same (tp,sl) the tpsl campaign's #379 winner arm used.
APEX_FIXED_TP = 0.10
APEX_FIXED_SL = -0.60

# BASELINE ARM = ZERO RECOMPUTE (task spec). The tpsl campaign's own #379
# arm ("tp+0.10_sl-0.60" -- n10/f0.10 sizing via mc_patch.APEX_ENV_DIFF's
# defaults, at this exact fixed TP/SL) is bit-identical config to this
# campaign's baseline sizing arm. Its 113 per-window JSONs already exist on
# disk; read them directly instead of ever re-running monte_carlo.py for it.
TPSL_BASELINE_JOB = 'apex_confirm'
BASELINE_ARM_TAG_TPSL = _tpsl_tp_sl_arm_tag(APEX_FIXED_TP, APEX_FIXED_SL)     # 'tp+0.10_sl-0.60'
BASELINE_RESULTS_DIR = os.path.join(_EXPERIMENTS_DIR, 'tpsl_refine_2026_08', 'out',
                                    'phaseD_apex2x_results', TPSL_BASELINE_JOB,
                                    BASELINE_ARM_TAG_TPSL)

PROBE_CELL = (10, 0.06)   # --probe's ONE subprocess-computed finalist sizing arm (Phase B's top Pareto pick)

CSV_FIELDS = [
    'arm_tag', 'n', 'frac', 'is_baseline', 'n_windows_ok', 'n_windows_total',
    'n_iter_per_window', 'n_paths', 'p_2x_ever', 'p_2x_before_50dd',
    'p_collapse', 'median_days_2x', 'p25_days_2x', 'p75_days_2x',
    'median_compound_pct', 'worst_dd_pct', 'gross_pct',
]


def arm_tag(n, frac):
    """Matches allocA_run.ApexCell.cell_name's format exactly (e.g.
    'n10_f0.060') so arm identities read consistently across the whole
    alloc_retune_2026_08 campaign (flat-MC CSVs use the same convention)."""
    return f"n{n}_f{frac:.3f}"


def parse_cells_arg(spec):
    """Parse '--cells n,frac;n,frac;...' (semicolon between cells, comma
    inside a cell -- same separator convention as phaseD_run.parse_cells_arg,
    adapted for sizing (n int, frac float) instead of (tp,sl). Returns ONLY
    the finalist arms the caller wants SUBPROCESS-computed -- baseline is
    NEVER subprocess-computed (always read from disk in main(), regardless
    of what --cells names), so it is intentionally never injected here the
    way phaseD_run.parse_cells_arg injects BASELINE_CELL. Duplicates removed
    (first occurrence wins). `spec=None`/empty returns [] -- a baseline-
    reuse-only invocation (zero finalist arms) is a valid, complete run."""
    cells = []
    if spec:
        for tok in spec.split(';'):
            tok = tok.strip()
            if not tok:
                continue
            parts = tok.split(',')
            if len(parts) != 2:
                raise SystemExit(f"--cells token {tok!r} is not 'n,frac' (e.g. '10,0.06') -- "
                                 f"comma inside a cell, semicolon between cells")
            try:
                n, frac = int(float(parts[0])), round(float(parts[1]), 4)
            except ValueError:
                raise SystemExit(f"--cells token {tok!r} has a non-numeric n or frac")
            cells.append((n, frac))
    seen = set()
    out = []
    for c in cells:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def build_env_overrides(mc_patch, n, frac):
    """Sizing arm env recipe (task spec, cross-checked against phaseD_apex2x's
    build_env_overrides + mc_patch.APEX_ENV_DIFF + concentration_2x's
    cell_env): frozen pins + the Apex DTE shape (NOMINAL_CAL_DTE/HOLD_CAL_DAYS,
    fixed -- never swept here) + this arm's OWN sizing via TIER_*_OV/
    MAX_POSITIONS_OVERRIDE/MAX_POSITIONS_CALL + EXPLICIT premium caps (
    concentration_2x's caveat: PRACTICAL_EXPOSURE_ENABLED would otherwise
    silently default an unset cap to Core's 0.50) + the FIXED TP/SL pin via
    the *_OV envs monte_carlo.py reads at import time -- NOT mc_patch.
    set_tpsl's post-import monkeypatch, which would not reach a subprocess."""
    env = dict(mc_patch.FROZEN_PINS)
    env['STARTING_CASH_OV'] = str(STARTING_CASH)
    env['NOMINAL_CAL_DTE'] = mc_patch.APEX_ENV_DIFF['NOMINAL_CAL_DTE']
    env['HOLD_CAL_DAYS'] = mc_patch.APEX_ENV_DIFF['HOLD_CAL_DAYS']
    a = f"{frac:.6f}"
    env['TIER_ULTRA_OV'] = a
    env['TIER_TOP_OV'] = a
    env['TIER_MID_OV'] = a
    env['TIER_LOW_OV'] = a
    env['TIER_OVERFLOW_OV'] = '0'
    env['MAX_POSITIONS_OVERRIDE'] = str(n)
    env['MAX_POSITIONS_CALL'] = str(n)
    env['GROSS_PREMIUM_CAP'] = '1.0'
    env['CALL_PREMIUM_CAP'] = '1.0'
    env['TP_BASE_OV'] = str(APEX_FIXED_TP)
    env['TP_STRESS_OV'] = str(APEX_FIXED_TP)
    env['SL_BASE_OV'] = str(APEX_FIXED_SL)
    env['SL_STRESS_OV'] = str(APEX_FIXED_SL)
    return env


def run_arm(run_one_window, c2x_sweep, env_base, windows, n_iter, pin_commit,
           results_dir, timeout_s, cpu):
    """Run every (window) cell for ONE finalist sizing arm (its n/frac
    already baked into env_base) via subprocess, pool per-iteration paths
    across all windows that completed. Returns (pooled_paths, n_windows_ok).
    Ported verbatim from phaseD_apex2x.py's run_arm (mechanism is identical
    regardless of what varies inside env_base)."""
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


def load_baseline_from_disk(c2x_sweep, results_dir, windows, starting_cash):
    """BASELINE ARM = ZERO RECOMPUTE (task spec). Read every window's
    already-computed roll_<date>.json directly off disk instead of invoking
    run_one_window/subprocess. Returns (pooled_paths, n_windows_ok,
    missing_labels, n_iter_seen). STOPS loudly (never silently substitutes)
    if the directory is missing/empty or every window is unusable -- the
    task explicitly requires this to fail fast and report, not improvise."""
    if not os.path.isdir(results_dir):
        raise SystemExit(
            f"[STOP] baseline arm dir missing: {results_dir!r}. Task spec: 'the tpsl campaign's "
            f"#379 arm (tp+0.10_sl-0.60 at n10/f0.10) is bit-identical config... read them "
            f"directly as the baseline arm... if the path is empty/missing, STOP and report.' "
            f"Report to orchestrator -- do not recompute or substitute another arm.")
    pooled = []
    n_ok = 0
    missing = []
    bad_n_iter = []
    n_iter_seen = None
    for (wlabel, ws, we) in windows:
        p = os.path.join(results_dir, f'{wlabel}.json')
        if not os.path.isfile(p):
            missing.append(wlabel)
            continue
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        wdata = data.get(wlabel)
        if wdata is None or 'paths' not in wdata:
            missing.append(wlabel)
            continue
        paths = wdata['paths']
        required_keys = ('finals', 'dds', 't2x_bars', 't_50dd_bars', 'starting_cash')
        if any(k not in paths for k in required_keys):
            raise SystemExit(
                f"[STOP] baseline JSON {p!r} 'paths' is missing one of {required_keys} -- schema "
                f"does not match what compute_cell_metrics/_paths_from_result consume. Report to "
                f"orchestrator, do not improvise a schema-compat shim.")
        meta_n_iter = (data.get('_meta') or {}).get('n_iter')
        if meta_n_iter is not None:
            if n_iter_seen is None:
                n_iter_seen = int(meta_n_iter)
            elif int(meta_n_iter) != n_iter_seen:
                bad_n_iter.append((wlabel, meta_n_iter))
                continue
        pooled.extend(c2x_sweep._paths_from_result(paths, starting_cash))
        n_ok += 1
    if not pooled:
        raise SystemExit(
            f"[STOP] baseline arm dir {results_dir!r} exists but yielded ZERO usable window JSONs "
            f"(missing={len(missing)} bad_n_iter={len(bad_n_iter)} of {len(windows)} expected) -- "
            f"report to orchestrator, do not recompute or improvise.")
    if missing:
        print(f"  [warn] baseline arm: {len(missing)}/{len(windows)} window(s) missing on disk: "
              f"{missing[:5]}{'...' if len(missing) > 5 else ''}", flush=True)
    if bad_n_iter:
        print(f"  [warn] baseline arm: {len(bad_n_iter)} window(s) had a DIFFERENT n_iter than the "
              f"rest ({n_iter_seen}) and were EXCLUDED from pooling: {bad_n_iter[:5]}"
              f"{'...' if len(bad_n_iter) > 5 else ''}", flush=True)
    return pooled, n_ok, missing, n_iter_seen


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--job', required=True, help='job name -- namespaces out/allocC_apex2x_<job>.csv etc')
    p.add_argument('--cells', default=None,
                    help="'n,frac;n,frac;...' finalist SIZING arm(s) to subprocess-compute (e.g. "
                         "'10,0.06'). Baseline n10/f0.10 is ALWAYS included but NEVER subprocess-"
                         "computed -- read from disk regardless of what's passed here.")
    p.add_argument('--n-iter', type=int, default=500, help='per-window N_ITER for finalist arms (ship-tier default 500)')
    p.add_argument('--cpu', type=int, default=6, help='MC_WORKERS per subprocess (frozen-pin default 6)')
    p.add_argument('--timeout-s', type=int, default=3600, help='per-(arm,window) subprocess wall ceiling')
    p.add_argument('--step-months', type=int, default=1,
                    help='monthly-roll step (default 1 = the SAME construction the tpsl baseline used)')
    p.add_argument('--horizon-days', type=int, default=730,
                    help='per-start horizon in calendar days (default 730, matches sweep.py HORIZON_DAYS)')
    p.add_argument('--hist-start', default='', help='override roll history start (YYYY-MM-DD); default sweep.py HIST_START')
    p.add_argument('--max-windows', type=int, default=0,
                    help='cap rolled windows for FINALIST (subprocess) arms only (0=all); baseline '
                         'always reads its full on-disk set regardless -- it costs nothing')
    p.add_argument('--probe', action='store_true',
                    help='VALIDATION mode: baseline read FULLY from disk (zero recompute, all '
                         'on-disk windows) + ONE finalist sizing arm (PROBE_CELL) via subprocess, '
                         'N=50, 1 rolled window -- proves the sizing-env path + pooled metrics + '
                         'baseline-JSON reuse all work end to end. Overrides --cells/--n-iter/--max-windows.')
    return p.parse_args()


def main():
    args = parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    import mc_patch   # driver/mc_patch.py -- FROZEN_PINS + APEX_ENV_DIFF only, read-only reuse
    from experiments._mc_pinned_runner import resolve_pinned_version, run_one_window  # noqa: E402
    c2x_sweep = _import_c2x_sweep()

    if args.probe:
        finalist_cells = [PROBE_CELL]
        n_iter = 50
        max_windows = 1
        print("[PROBE MODE] baseline full-disk-reuse (all on-disk windows, zero recompute) + ONE "
              "finalist sizing arm via subprocess, N=50, 1 rolled window -- validation run", flush=True)
    else:
        finalist_cells = parse_cells_arg(args.cells)
        n_iter = args.n_iter
        max_windows = args.max_windows or 0

    hist_start = date.fromisoformat(args.hist_start) if args.hist_start else c2x_sweep.HIST_START
    all_windows = c2x_sweep.monthly_windows(hist_start=hist_start, step_months=args.step_months,
                                            horizon_days=args.horizon_days)
    if not all_windows:
        raise SystemExit("monthly_windows() produced zero windows -- check --hist-start/--horizon-days")
    subprocess_windows = all_windows[:max_windows] if max_windows else all_windows

    pin_commit, pin_id = resolve_pinned_version()
    print(f"[allocC_apex2x] job={args.job} finalist_cells={finalist_cells} n_iter={n_iter} "
         f"n_windows_all={len(all_windows)} n_windows_subprocess={len(subprocess_windows)} "
         f"(step_months={args.step_months} horizon_days={args.horizon_days} hist_start={hist_start}) "
         f"pinned_version={pin_commit} (id={pin_id})", flush=True)

    results_job_dir = os.path.join(RESULTS_ROOT, args.job)
    all_rows = []
    t0 = time.time()

    # --- BASELINE (n10/f0.10, APEX_BASELINE) -- ZERO RECOMPUTE, always full disk reuse. ---
    b_n, b_frac = APEX_BASELINE
    b_tag = arm_tag(b_n, b_frac)
    print(f"\n[ARM] {b_tag}  (is_baseline=True, ZERO RECOMPUTE -- reading "
         f"{BASELINE_RESULTS_DIR!r})", flush=True)
    b_pooled, b_n_ok, b_missing, b_n_iter_seen = load_baseline_from_disk(
        c2x_sweep, BASELINE_RESULTS_DIR, all_windows, STARTING_CASH)
    if not args.probe and b_n_iter_seen is not None and b_n_iter_seen != n_iter:
        print(f"  [warn] baseline on-disk n_iter={b_n_iter_seen} != requested finalist n_iter="
             f"{n_iter} -- 'same N=500 so results pool comparably' assumption may not hold; "
             f"proceeding (numbers still valid per-arm, just flagging the mismatch)", flush=True)
    b_metrics = c2x_sweep.compute_cell_metrics(b_tag, b_pooled, n_windows=b_n_ok,
                                               n_iter_per_window=b_n_iter_seen or 0,
                                               gross_pct=float(b_n) * b_frac,
                                               bars_to_calendar=c2x_sweep.CALENDAR_DAYS_PER_TRADING_DAY)
    b_rec = asdict(b_metrics)
    b_rec.update(arm_tag=b_tag, n=b_n, frac=b_frac, is_baseline=True,
                n_windows_ok=b_n_ok, n_windows_total=len(all_windows))
    all_rows.append(b_rec)
    print(f"[ARM DONE] {b_tag} n_windows_ok={b_n_ok}/{len(all_windows)} n_paths={b_metrics.n_paths} "
         f"p_2x_ever={b_metrics.p_2x_ever:.3f} p_2x_before_50dd={b_metrics.p_2x_before_50dd:.3f} "
         f"p_collapse={b_metrics.p_collapse:.3f} med_days_2x={b_metrics.median_days_2x} "
         f"med_compound={b_metrics.median_compound_pct} worst_dd={b_metrics.worst_dd_pct}", flush=True)

    # --- FINALIST sizing arms -- subprocess-computed. ---
    for n, frac in finalist_cells:
        tag = arm_tag(n, frac)
        print(f"\n[ARM] {tag}  (is_baseline=False, subprocess n_iter={n_iter} "
             f"windows={len(subprocess_windows)})", flush=True)
        env_base = build_env_overrides(mc_patch, n, frac)
        arm_dir = os.path.join(results_job_dir, tag)
        pooled, n_ok = run_arm(run_one_window, c2x_sweep, env_base, subprocess_windows, n_iter,
                               pin_commit, arm_dir, args.timeout_s, args.cpu)
        m = c2x_sweep.compute_cell_metrics(tag, pooled, n_windows=n_ok, n_iter_per_window=n_iter,
                                           gross_pct=float(n) * frac,
                                           bars_to_calendar=c2x_sweep.CALENDAR_DAYS_PER_TRADING_DAY)
        rec = asdict(m)
        rec.update(arm_tag=tag, n=n, frac=frac, is_baseline=False,
                  n_windows_ok=n_ok, n_windows_total=len(subprocess_windows))
        all_rows.append(rec)
        print(f"[ARM DONE] {tag} n_windows_ok={n_ok}/{len(subprocess_windows)} n_paths={m.n_paths} "
             f"p_2x_ever={m.p_2x_ever:.3f} p_2x_before_50dd={m.p_2x_before_50dd:.3f} "
             f"p_collapse={m.p_collapse:.3f} med_days_2x={m.median_days_2x} "
             f"med_compound={m.median_compound_pct} worst_dd={m.worst_dd_pct}", flush=True)

    elapsed = time.time() - t0

    csv_path = os.path.join(OUT_DIR, f'allocC_apex2x_{args.job}.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for rec in all_rows:
            w.writerow({k: rec.get(k) for k in CSV_FIELDS})
    print(f"\n[WROTE] {csv_path}", flush=True)

    md_path = os.path.join(OUT_DIR, f'allocC_apex2x_{args.job}_summary.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# Confirm Apex 2x-race read -- job={args.job}\n\n")
        f.write(f"Finalist N_ITER={n_iter}  windows_all={len(all_windows)} "
               f"windows_subprocess={len(subprocess_windows)} (step_months={args.step_months}, "
               f"horizon_days={args.horizon_days}, hist_start={hist_start})  "
               f"pinned_version={pin_commit} (id={pin_id})  wall={elapsed:.0f}s\n\n")
        f.write(f"Baseline (n10/f0.10 @ tp={APEX_FIXED_TP:+.2f}/sl={APEX_FIXED_SL:+.2f}) is the tpsl "
               f"campaign's #379 arm ({BASELINE_ARM_TAG_TPSL}), read from disk -- ZERO RECOMPUTE, "
               f"n_iter_on_disk={b_n_iter_seen}.\n\n")
        f.write("TASK.md locked Apex confirm rule: P(2x ever)>=99.0%% AND worst DD strictly better "
               "than 65.4%% AND med days<=200 AND collapse 0 (per finalist; absolute floors, not a "
               "baseline delta). This file reports numbers only -- the SHIP/FLAG/NO-SHIP verdict is "
               "rendered by analyze_allocC.py.\n\n")
        f.write("| arm | baseline | n_win_ok | n_paths | P(2x ever) | P(2x<50dd) | p_collapse | "
               "med days-to-2x | med compound | worst DD |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for rec in sorted(all_rows, key=lambda r: (not r['is_baseline'], r['n'], r['frac'])):
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
        fin_rows = [r for r in all_rows if not r['is_baseline']]
        base_row = next(r for r in all_rows if r['is_baseline'])
        fin_ok = bool(fin_rows) and fin_rows[0]['n_paths'] == n_iter * fin_rows[0]['n_windows_ok'] \
            and fin_rows[0]['n_windows_ok'] >= 1 and fin_rows[0]['n_paths'] > 0
        base_ok = base_row['n_windows_ok'] >= 100 and base_row['n_paths'] > 0   # expect ~113; tolerant floor
        ok = fin_ok and base_ok
        print(f"\n[PROBE RESULT] {'OK' if ok else 'FAIL'} -- "
             f"finalist: n_paths={fin_rows[0]['n_paths'] if fin_rows else None} "
             f"(expected {n_iter}*{fin_rows[0]['n_windows_ok'] if fin_rows else 0}) | "
             f"baseline: n_windows_ok={base_row['n_windows_ok']} n_paths={base_row['n_paths']} "
             f"p_2x_ever={base_row['p_2x_ever']}", flush=True)
        if not ok:
            raise SystemExit("[PROBE RESULT] FAIL -- either the subprocess sizing-env path or the "
                             "baseline-disk-reuse path did not produce the expected path counts; see "
                             "out/allocC_apex2x_results/ and the per-window .log files")


if __name__ == '__main__':
    main()
