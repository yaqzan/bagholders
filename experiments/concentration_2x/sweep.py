#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Track B — Concentration grid x time-to-2x sweep harness.

WHAT THIS DOES
--------------
Replaces the production tier cascade with a FLAT (N concurrent positions x
alloc% each), top-conviction-first, score>=75 calls-only sizing, and measures a
NEW objective the system has never tracked: minimum *time-to-2x* (50k -> 100k),
collapse-TOLERANT.

Per (N positions, alloc%) cell it:
  1. Sets the flat-allocation sizing (all four call tiers equal to alloc%,
     overflow=0, MAX_POSITIONS=N, calls-only via put caps=0).
  2. Rolls the START DATE monthly across the 10y history (so the metric means
     "if I start at a RANDOM point, how fast", not "if I start in 2024").
  3. Runs the MC SIMULATE step per (cell x monthly-start window) with per-path
     instrumentation (per-iteration first-passage arrays).
  4. POOLS all paths across all monthly starts and computes:
        - median / P25 / P75 days-to-2x AMONG paths that reach 2x
        - P(reach 2x BEFORE a 50% drawdown)
        - P(reach 2x EVER within the window horizon)
        - P(collapse to <= 20% of start)
        - reference: median compound %, worst DD %

GROUNDING: 30 DTE, calls-only, v74 Apex. Portfolio-stage research — NO
ALGORITHM_VERSION bump, NO trader recalculate, NO scoring change. Uses existing
v74 score rows.

SPEED (load-once-per-window) — DEFAULT
--------------------------------------
The DOMINANT cost is PREPARE: signal load, every per-date map, and the call/put
barrier-outcome precompute. Those are IDENTICAL across the 27 cells of a window
(cells differ ONLY in flat tier-alloc% + MAX_POSITIONS, applied at the cheap
cascade/sizing step). The driver therefore:

    for window (OUTER):  ctx = monte_carlo._prepare_window(...)   # once
        for cell (INNER): monte_carlo._simulate_window(ctx, cell_params)  # many

ALL in ONE process. ~27x fewer precomputes -> ~10-30x faster than the legacy
subprocess-per-cell path. Bit-exact vs the legacy path because (a) seeds depend
only on the window label + iteration index (NOT cell params), (b) each iteration
spins a fresh random.Random(seed), and (c) cell_params are applied via
monte_carlo._apply_cell_params in-process AND propagated into MP workers
(Windows-spawn safe).

RESILIENCE (per-window checkpoint/resume) — DEFAULT
---------------------------------------------------
Each window's per-cell results are written to results/<stage>_partial/<window>.json
the moment that window completes. On (re)start the driver SKIPS windows already on
disk and resumes. A preemption costs at most ONE window. The final
results/sweep_<stage>.json is assembled from the per-window partials.

ENGINE SURFACE USED (all default-OFF / byte-identical when unset):
  monte_carlo._prepare_window / _simulate_window / _apply_cell_params  (in-process API)
  WIN_START / WIN_END / WIN_LABEL        (arbitrary single window -> monthly roll)
  STARTING_CASH_OV                       (2x target = 2 x this; import-time)
  N_ITER_OVERRIDE                        (import-time)
  MC_RETURN_PATHS=1                      (emit per-iter finals/dds/t2x_bars/t_50dd_bars)
  MC_NO_DB_PERSIST=1                     (never touch MySQL writes; sweep forces persist=False too)
  MC_NO_MP / MC_WORKERS                  (worker control)
  (legacy subprocess path) MC_RESULTS_JSON, TIER_*_OV, MAX_POSITIONS_OVERRIDE,
                           MAX_POSITIONS_CALL/PUT

STAGES
------
  --stage smoke   : 2 cells, N_ITER=10, 1 short monthly window   (logic shakedown)
  --stage coarse  : full grid + cascade ref, N_ITER=100, monthly roll
  --stage drill   : frontier cells (from --cells), N_ITER=500, monthly roll

  --legacy-subprocess : use the OLD per-cell subprocess path (validation only).

The metric-computation core (first-passage / survival / collapse on per-path
arrays) is unit-tested on SYNTHETIC equity curves in test_metrics.py — that test
runs with ZERO MySQL / ZERO MC.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# 10y history bounds (matches monte_carlo.WINDOWS '10y' row: 2016-06-01 .. 2026-04-15).
HIST_START = date(2016, 6, 1)
HIST_END = date(2026, 4, 15)

# Horizon for each rolled start: how far forward a path runs to chase 2x.
# A 2-year horizon is long enough for the funded Apex sleeve to plausibly 2x in
# good tape while still leaving many starts that DON'T 2x (so P(2x ever) is
# informative). Each monthly start runs [start, min(start+horizon, HIST_END)].
HORIZON_DAYS = 730  # ~24 calendar months

TRADING_DAYS_PER_YEAR = 252.0
CALENDAR_DAYS_PER_TRADING_DAY = 365.25 / TRADING_DAYS_PER_YEAR  # ~1.449


# ----------------------------------------------------------------------------
# Cell definitions
# ----------------------------------------------------------------------------
@dataclass
class Cell:
    """A sizing configuration to test."""
    name: str
    kind: str                 # 'flat' | 'cascade'
    n_positions: int          # MAX_POSITIONS
    alloc_pct: Optional[float]  # flat alloc per position (fraction, e.g. 0.15); None for cascade
    # cascade override (only for kind='cascade'): dict of tier->frac
    cascade: Optional[dict] = None

    def gross_pct(self) -> Optional[float]:
        if self.kind != 'flat' or self.alloc_pct is None:
            return None
        return self.n_positions * self.alloc_pct


def build_grid() -> list[Cell]:
    """Track B grid: N in {1,2,3,4,5,7,10,14} x alloc in {10,15,20,25,33,50}%,
    gross-capped at 100% (drop cells whose N*alloc > 1.0), plus the production
    cascade reference cell."""
    Ns = [1, 2, 3, 4, 5, 7, 10, 14]
    allocs = [0.10, 0.15, 0.20, 0.25, 0.33, 0.50]
    cells: list[Cell] = []
    for n in Ns:
        for a in allocs:
            gross = n * a
            if gross > 1.0 + 1e-9:
                continue  # gross-capped at 100%
            cells.append(Cell(
                name=f"flat_n{n}_a{int(round(a*100))}",
                kind='flat', n_positions=n, alloc_pct=a,
            ))
    # Production cascade reference: ULTRA 20 / TOP 15 / MID 8 / LOW 3, MaxPos 14.
    cells.append(Cell(
        name="cascade_ref",
        kind='cascade', n_positions=14, alloc_pct=None,
        cascade={'ultra': 0.20, 'top': 0.15, 'mid': 0.08, 'low': 0.03, 'overflow': 0.0},
    ))
    return cells


# ----------------------------------------------------------------------------
# Monthly-rolling start windows
# ----------------------------------------------------------------------------
def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    # clamp day to 1 (we always start on the 1st of the month)
    return date(y, m, 1)


def monthly_windows(hist_start: date = HIST_START,
                    hist_end: date = HIST_END,
                    horizon_days: int = HORIZON_DAYS,
                    step_months: int = 1,
                    min_horizon_days: int = 180) -> list[tuple[str, date, date]]:
    """Roll the start date monthly across history. Each window runs
    [start, min(start + horizon, hist_end)]. Windows whose remaining runway is
    shorter than min_horizon_days are dropped (too short to chase 2x)."""
    from datetime import timedelta
    out: list[tuple[str, date, date]] = []
    cur = date(hist_start.year, hist_start.month, 1)
    if cur < hist_start:
        cur = _add_months(cur, 1)
    i = 0
    while cur <= hist_end:
        w_end = cur + timedelta(days=horizon_days)
        if w_end > hist_end:
            w_end = hist_end
        runway = (w_end - cur).days
        if runway >= min_horizon_days:
            out.append((f"roll_{cur.isoformat()}", cur, w_end))
        cur = _add_months(cur, step_months)
        i += 1
        if i > 1000:  # safety
            break
    return out


# ----------------------------------------------------------------------------
# Per-path metric computation (UNIT-TESTED on synthetic arrays in test_metrics.py)
# ----------------------------------------------------------------------------
@dataclass
class CellMetrics:
    cell: str
    n_paths: int = 0
    n_reach_2x: int = 0
    n_2x_before_50dd: int = 0
    n_collapse: int = 0
    # days-to-2x among reachers (calendar days)
    median_days_2x: Optional[float] = None
    p25_days_2x: Optional[float] = None
    p75_days_2x: Optional[float] = None
    # probabilities (0..1)
    p_2x_ever: float = 0.0
    p_2x_before_50dd: float = 0.0
    p_collapse: float = 0.0
    # reference
    median_compound_pct: Optional[float] = None
    worst_dd_pct: Optional[float] = None
    # provenance
    n_windows: int = 0
    n_iter_per_window: int = 0
    gross_pct: Optional[float] = None


def compute_cell_metrics(cell_name: str,
                         path_records: list[dict],
                         n_windows: int,
                         n_iter_per_window: int,
                         gross_pct: Optional[float],
                         bars_to_calendar: float = CALENDAR_DAYS_PER_TRADING_DAY) -> CellMetrics:
    """Aggregate POOLED per-path records into the time-to-2x metric set.

    Each path_record dict (one per MC iteration, pooled across all monthly-start
    windows) must carry:
      final          : final equity ($)
      max_dd         : max drawdown fraction over the path (0..1)
      t2x_bar        : trading-day idx of first 2x crossing, or None
      t_50dd_bar     : trading-day idx of first >=50% drawdown, or None
      starting_cash  : starting equity ($)

    "days-to-2x" = t2x_bar converted to CALENDAR days (the metric the brief
    asks for in DAYS). "2x before 50% DD" = a path reached 2x AND either never
    hit a 50% drawdown OR hit 2x strictly before its first 50% drawdown.
    "collapse" = final <= 20% of starting_cash.
    """
    m = CellMetrics(cell=cell_name, n_windows=n_windows,
                    n_iter_per_window=n_iter_per_window, gross_pct=gross_pct)
    m.n_paths = len(path_records)
    if m.n_paths == 0:
        return m

    days_2x: list[float] = []
    compounds: list[float] = []
    dds: list[float] = []

    for r in path_records:
        sc = r.get('starting_cash') or 0.0
        final = r.get('final')
        max_dd = r.get('max_dd')
        t2x = r.get('t2x_bar')
        t50 = r.get('t_50dd_bar')

        if final is not None and sc > 0:
            compounds.append((final / sc - 1.0) * 100.0)
        if max_dd is not None:
            dds.append(max_dd * 100.0)

        # collapse: final <= 20% of start
        if final is not None and sc > 0 and final <= sc * 0.20:
            m.n_collapse += 1

        reached = t2x is not None
        if reached:
            m.n_reach_2x += 1
            days_2x.append(t2x * bars_to_calendar)
            # 2x before 50% DD: never hit 50% dd, OR hit 2x strictly before it.
            if t50 is None or t2x < t50:
                m.n_2x_before_50dd += 1

    if days_2x:
        ds = sorted(days_2x)
        m.median_days_2x = statistics.median(ds)
        m.p25_days_2x = _quantile(ds, 0.25)
        m.p75_days_2x = _quantile(ds, 0.75)
    if compounds:
        m.median_compound_pct = statistics.median(compounds)
    if dds:
        m.worst_dd_pct = max(dds)

    m.p_2x_ever = m.n_reach_2x / m.n_paths
    m.p_2x_before_50dd = m.n_2x_before_50dd / m.n_paths
    m.p_collapse = m.n_collapse / m.n_paths
    return m


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation quantile on an already-sorted list."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])


# ----------------------------------------------------------------------------
# Cell -> in-process sizing params (load-once path) AND env (legacy subprocess path)
# ----------------------------------------------------------------------------
def cell_params(cell: Cell) -> dict:
    """Build the in-process `cell_params` dict consumed by
    monte_carlo._apply_cell_params. Calls-only: puts off via MAX_POSITIONS_PUT=0
    AND zero put tier allocs. Flat cell sets all four call tiers equal to alloc%,
    overflow=0; cascade cell uses its tier dict."""
    p = {
        'PUT_TIER_ALLOC': {'put_top': 0.0, 'put_mid': 0.0, 'put_low': 0.0},
        'MAX_POSITIONS': int(cell.n_positions),
        'MAX_POSITIONS_CALL': int(cell.n_positions),
        'MAX_POSITIONS_PUT': 0,
    }
    if cell.kind == 'flat':
        a = float(cell.alloc_pct)
        p['TIER_ALLOC'] = {'ultra': a, 'top': a, 'mid': a, 'low': a, 'overflow': 0.0}
    else:  # cascade reference
        c = cell.cascade or {}
        p['TIER_ALLOC'] = {
            'ultra':    float(c.get('ultra', 0.20)),
            'top':      float(c.get('top', 0.15)),
            'mid':      float(c.get('mid', 0.08)),
            'low':      float(c.get('low', 0.03)),
            'overflow': float(c.get('overflow', 0.0)),
        }
    return p


def cell_env(cell: Cell, n_iter: int, win_label: str, win_start: date,
             win_end: date, results_json: Path, starting_cash: float,
             workers: Optional[int]) -> dict:
    """LEGACY subprocess path env overlay for one (cell x window) MC run.

    Kept for `--legacy-subprocess` validation and exercised by test_metrics.py.
    Flat cell: all four call tiers (ultra/top/mid/low) set equal to alloc%,
    overflow=0, MAX_POSITIONS_OVERRIDE=N. Calls-only via put caps=0.
    Cascade cell: tier dict from cell.cascade, MaxPos from cell.n_positions.
    """
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    # Path / survival instrumentation
    env['MC_RETURN_PATHS'] = '1'
    env['MC_NO_DB_PERSIST'] = '1'
    env['MC_RESULTS_JSON'] = str(results_json)
    env['N_ITER_OVERRIDE'] = str(n_iter)
    env['STARTING_CASH_OV'] = str(starting_cash)
    # Custom monthly-roll window
    env['WIN_START'] = win_start.isoformat()
    env['WIN_END'] = win_end.isoformat()
    env['WIN_LABEL'] = win_label
    # Calls-only: cap concurrent puts at 0 (no puts ever fill) AND zero the put
    # tier allocs as belt-and-suspenders. v74 Apex is already puts-off, but we
    # pin it for a pure calls-only concentration test.
    env['MAX_POSITIONS_PUT'] = '0'
    env['PUT_TIER_TOP_OV'] = '0.0'
    env['PUT_TIER_MID_OV'] = '0.0'
    env['PUT_TIER_LOW_OV'] = '0.0'
    if workers is not None:
        if workers <= 1:
            env['MC_NO_MP'] = '1'
        else:
            env['MC_WORKERS'] = str(workers)

    env['MAX_POSITIONS_OVERRIDE'] = str(cell.n_positions)
    if cell.kind == 'flat':
        a = f"{cell.alloc_pct:.6f}"
        env['TIER_ULTRA_OV'] = a
        env['TIER_TOP_OV'] = a
        env['TIER_MID_OV'] = a
        env['TIER_LOW_OV'] = a
        env['TIER_OVERFLOW_OV'] = '0.0'   # score<75 disabled (calls>=75 only)
        env['MAX_POSITIONS_CALL'] = str(cell.n_positions)
    else:  # cascade reference
        c = cell.cascade or {}
        env['TIER_ULTRA_OV'] = f"{c.get('ultra', 0.20):.6f}"
        env['TIER_TOP_OV'] = f"{c.get('top', 0.15):.6f}"
        env['TIER_MID_OV'] = f"{c.get('mid', 0.08):.6f}"
        env['TIER_LOW_OV'] = f"{c.get('low', 0.03):.6f}"
        env['TIER_OVERFLOW_OV'] = f"{c.get('overflow', 0.0):.6f}"
        env['MAX_POSITIONS_CALL'] = str(cell.n_positions)
    return env


# ----------------------------------------------------------------------------
# Path extraction (shared by both driver paths)
# ----------------------------------------------------------------------------
def _paths_from_result(r: dict, starting_cash: float) -> list[dict]:
    """Turn a single window's MC result dict (with MC_RETURN_PATHS arrays) into a
    list of per-path records for compute_cell_metrics."""
    sc = r.get('starting_cash', starting_cash)
    finals = r.get('finals') or []
    dds = r.get('dds') or []
    t2x = r.get('t2x_bars') or []
    t50 = r.get('t_50dd_bars') or []
    out = []
    for i in range(len(finals)):
        out.append({
            'final': finals[i],
            'max_dd': dds[i] if i < len(dds) else None,
            't2x_bar': t2x[i] if i < len(t2x) else None,
            't_50dd_bar': t50[i] if i < len(t50) else None,
            'starting_cash': sc,
        })
    return out


# ----------------------------------------------------------------------------
# IN-PROCESS load-once-per-window driver (DEFAULT — fast + checkpointed)
# ----------------------------------------------------------------------------
def run_window_inproc(mc, version, wlabel: str, ws: date, we: date,
                      cells: list[Cell], n_iter: int, starting_cash: float,
                      workers: Optional[int] = None, verbose: bool = True) -> dict:
    """PREPARE this window once, then SIMULATE every cell against the prepared ctx
    reusing a SINGLE window-level MP pool across all cells.

    The dominant savings is twofold: PREPARE (signal load + barrier-outcome
    precompute) runs ONCE per window, AND the pool's workers are spawned + the ctx
    pickled into them ONCE per window (not once per cell). Each cell only broadcasts
    its tiny sizing knobs to the live workers and maps the seeds. Bit-exact vs the
    legacy path: seeds depend only on (label, iter); each iter spins a fresh
    random.Random(seed); pool.map preserves order.

    Returns {cell_name: [path_record, ...]} for this window. MC_RETURN_PATHS=1 and
    MC_NO_DB_PERSIST=1 are set in env before import; persist=False also forced."""
    import os as _os
    if verbose:
        print(f"  [PREPARE] {wlabel}  {ws} -> {we}", flush=True)
    ctx = mc._prepare_window(wlabel, ws, we, version)

    use_mp = _os.environ.get('MC_NO_MP', '0') != '1'
    n_workers = (workers if workers else (int(_os.environ.get('MC_WORKERS', '0'))
                                          or _os.cpu_count() or 4))
    n_workers = min(n_workers, mc.N_ITER) if mc.N_ITER else n_workers

    pool = None
    if use_mp and mc.N_ITER >= 16 and len(cells) > 1:
        # Reuse ONE pool across all cells of this window (the load-once win).
        pool = mc._make_window_pool(ctx, n_workers)

    out: dict[str, list[dict]] = {}
    try:
        for cell in cells:
            cp = cell_params(cell)
            if verbose:
                print(f"    [SIM] {cell.name}  N={mc.N_ITER}", flush=True)
            res = mc._simulate_window(ctx, cell_params=cp, persist=False, pool=pool)['seeded']
            out[cell.name] = _paths_from_result(res, starting_cash)
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    return out


# ----------------------------------------------------------------------------
# LEGACY subprocess driver (validation only)
# ----------------------------------------------------------------------------
def run_cell_subprocess(cell: Cell, windows: list[tuple[str, date, date]], n_iter: int,
                        starting_cash: float, workers: Optional[int],
                        verbose: bool = True) -> CellMetrics:
    """Run one cell across all monthly-roll windows via subprocess-per-(cell,window),
    pool paths, compute metrics. This is the ORIGINAL slow path, retained for
    bit-exact validation against the in-process driver."""
    pooled: list[dict] = []
    n_ok = 0
    for (wlabel, ws, we) in windows:
        rj = RESULTS_DIR / f"_run_{cell.name}_{wlabel}.json"
        env = cell_env(cell, n_iter, wlabel, ws, we, rj, starting_cash, workers)
        if verbose:
            print(f"  [{cell.name}] {wlabel}  {ws} -> {we}  (N={n_iter})", flush=True)
        proc = subprocess.run(
            [sys.executable, '-u', str(REPO / 'monte_carlo.py')],
            env=env, cwd=str(REPO),
            stdout=subprocess.DEVNULL if not verbose else None,
            stderr=subprocess.STDOUT if not verbose else None,
        )
        if proc.returncode != 0:
            print(f"    !! MC failed rc={proc.returncode} for {cell.name}/{wlabel}", flush=True)
            continue
        if not rj.exists():
            print(f"    !! no results json for {cell.name}/{wlabel}", flush=True)
            continue
        data = json.loads(rj.read_text())
        wkey = wlabel if wlabel in data else next(
            (k for k in data if k != '_meta'), None)
        if wkey is None or 'paths' not in data.get(wkey, {}):
            print(f"    !! no 'paths' in results for {cell.name}/{wlabel}", flush=True)
            continue
        p = data[wkey]['paths']
        pooled.extend(_paths_from_result(p, starting_cash))
        n_ok += 1
        # clean per-run sidecar to keep the dir small
        try:
            rj.unlink()
        except OSError:
            pass

    m = compute_cell_metrics(cell.name, pooled, n_windows=n_ok,
                             n_iter_per_window=n_iter, gross_pct=cell.gross_pct())
    return m


def parse_cells(spec: Optional[str], grid: list[Cell]) -> list[Cell]:
    if not spec:
        return grid
    wanted = {x.strip() for x in spec.split(',') if x.strip()}
    by_name = {c.name: c for c in grid}
    out = []
    for w in wanted:
        if w in by_name:
            out.append(by_name[w])
        else:
            print(f"  (warning) unknown cell '{w}' — skipped", flush=True)
    return out


# ----------------------------------------------------------------------------
# Checkpoint helpers
# ----------------------------------------------------------------------------
def _partial_dir(stage: str) -> Path:
    d = RESULTS_DIR / f"{stage}_partial"
    d.mkdir(exist_ok=True)
    return d


def _partial_path(stage: str, wlabel: str) -> Path:
    # Window label can contain ':' on some OSes? roll_<iso-date> uses '-', safe.
    return _partial_dir(stage) / f"{wlabel}.json"


# ----------------------------------------------------------------------------
# Assembly: pool per-window partials -> per-cell metrics
# ----------------------------------------------------------------------------
def assemble_from_partials(stage: str, cells: list[Cell], windows: list[tuple[str, date, date]],
                           n_iter: int, starting_cash: float) -> list[dict]:
    """Read every window partial, pool per-cell paths across windows, compute
    CellMetrics per cell. A partial that's missing a cell just contributes nothing
    for that cell (n_windows reflects how many windows actually had it)."""
    by_cell_paths: dict[str, list[dict]] = {c.name: [] for c in cells}
    by_cell_nwin: dict[str, int] = {c.name: 0 for c in cells}
    pdir = _partial_dir(stage)
    for (wlabel, _, _) in windows:
        pp = pdir / f"{wlabel}.json"
        if not pp.exists():
            continue
        try:
            data = json.loads(pp.read_text())
        except Exception as e:
            print(f"  (warning) bad partial {pp}: {e}", flush=True)
            continue
        cell_paths = data.get('cells', {})
        for c in cells:
            recs = cell_paths.get(c.name)
            if recs:
                by_cell_paths[c.name].extend(recs)
                by_cell_nwin[c.name] += 1
    results = []
    for c in cells:
        m = compute_cell_metrics(c.name, by_cell_paths[c.name],
                                 n_windows=by_cell_nwin[c.name],
                                 n_iter_per_window=n_iter, gross_pct=c.gross_pct())
        results.append(asdict(m))
    return results


def main():
    ap = argparse.ArgumentParser(description="Track B concentration x time-to-2x sweep")
    ap.add_argument('--stage', choices=['smoke', 'coarse', 'drill'], required=True)
    ap.add_argument('--cells', default='', help="comma list of cell names (drill); default=frontier-from-coarse not auto-selected here")
    ap.add_argument('--out', default='', help="output JSON path (default auto by stage)")
    ap.add_argument('--workers', type=int, default=None, help="MC workers (default: MC auto)")
    ap.add_argument('--starting-cash', type=float, default=50_000.0)
    ap.add_argument('--step-months', type=int, default=1)
    ap.add_argument('--horizon-days', type=int, default=HORIZON_DAYS)
    ap.add_argument('--hist-start', default='',
                    help="override HIST_START (YYYY-MM-DD) to clip the monthly-rolling "
                         "window grid (e.g. panel-limited A/B; default=module HIST_START)")
    ap.add_argument('--legacy-subprocess', action='store_true',
                    help="use the OLD subprocess-per-cell path (validation only)")
    ap.add_argument('--n-iter', type=int, default=0,
                    help="override N_ITER for the stage (validation: e.g. 20)")
    ap.add_argument('--no-resume', action='store_true',
                    help="ignore existing per-window partials and recompute all")
    ap.add_argument('--max-windows', type=int, default=0,
                    help="cap the number of windows (validation/debug; 0=all)")
    ap.add_argument('--tag', default='',
                    help="suffix for the partial-checkpoint dir + default out path, "
                         "so independent arms (e.g. different GROSS_PREMIUM_CAP env) "
                         "never share/resume each other's partials")
    args = ap.parse_args()

    grid = build_grid()
    _hist_start = date.fromisoformat(args.hist_start) if args.hist_start else HIST_START

    if args.stage == 'smoke':
        # 2 cells, N=10, 1 short window (logic shakedown — tiny real-data run).
        cells = [c for c in grid if c.name in ('flat_n5_a15', 'cascade_ref')]
        n_iter = 10
        windows = [('smoke_2024', date(2024, 1, 1), date(2024, 12, 31))]
    elif args.stage == 'coarse':
        cells = grid
        n_iter = 100
        windows = monthly_windows(hist_start=_hist_start, step_months=args.step_months,
                                  horizon_days=args.horizon_days)
    else:  # drill
        cells = parse_cells(args.cells, grid)
        if not cells:
            print("drill stage requires --cells <names>", file=sys.stderr)
            sys.exit(2)
        n_iter = 500
        windows = monthly_windows(hist_start=_hist_start, step_months=args.step_months,
                                  horizon_days=args.horizon_days)

    if args.n_iter > 0:
        n_iter = args.n_iter

    if args.max_windows and args.max_windows > 0:
        windows = windows[:args.max_windows]

    stage_label = f"{args.stage}_{args.tag}" if args.tag else args.stage
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"sweep_{stage_label}.json"
    mode = 'LEGACY-subprocess' if args.legacy_subprocess else 'IN-PROCESS load-once'
    print(f"STAGE={args.stage}  mode={mode}  cells={len(cells)}  windows={len(windows)}  "
          f"N_ITER={n_iter}  start_cash=${args.starting_cash:,.0f}", flush=True)

    # ---------------- LEGACY subprocess path (validation only) ----------------
    if args.legacy_subprocess:
        print(f"  total MC runs = {len(cells) * len(windows)}", flush=True)
        results: list[dict] = []
        for ci, cell in enumerate(cells, 1):
            print(f"\n[{ci}/{len(cells)}] cell={cell.name}  kind={cell.kind}  "
                  f"N={cell.n_positions}  gross={cell.gross_pct()}", flush=True)
            m = run_cell_subprocess(cell, windows, n_iter, args.starting_cash, args.workers)
            results.append(asdict(m))
            print(f"    -> 2x_ever={m.p_2x_ever:.3f}  2x_before_50dd={m.p_2x_before_50dd:.3f}  "
                  f"collapse={m.p_collapse:.3f}  med_days_2x={m.median_days_2x}  "
                  f"med_compound={m.median_compound_pct}  worstDD={m.worst_dd_pct}",
                  flush=True)
        payload = {
            'stage': args.stage, 'mode': 'legacy', 'n_iter': n_iter,
            'starting_cash': args.starting_cash, 'horizon_days': args.horizon_days,
            'step_months': args.step_months, 'n_windows': len(windows),
            'window_span': [windows[0][1].isoformat(), windows[-1][2].isoformat()] if windows else None,
            'cells': results,
        }
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"\n[WROTE] {out_path}", flush=True)
        return

    # ---------------- IN-PROCESS load-once + checkpoint/resume (default) ----------------
    # Set import-time engine env BEFORE importing monte_carlo: STARTING_CASH and
    # N_ITER are read at module load. MC_RETURN_PATHS / MC_NO_DB_PERSIST are read
    # at runtime but set here for clarity. Worker control too.
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    os.environ['STARTING_CASH_OV'] = repr(args.starting_cash)
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    os.environ['MC_RETURN_PATHS'] = '1'
    os.environ['MC_NO_DB_PERSIST'] = '1'
    if args.workers is not None:
        if args.workers <= 1:
            os.environ['MC_NO_MP'] = '1'
        else:
            os.environ['MC_WORKERS'] = str(args.workers)

    sys.path.insert(0, str(REPO))
    import monte_carlo as mc  # noqa: E402  (env must be set first)
    # Guard: confirm the import-time globals match the requested sweep config.
    assert abs(mc.STARTING_CASH - args.starting_cash) < 1e-6, \
        f"STARTING_CASH mismatch: {mc.STARTING_CASH} != {args.starting_cash}"
    assert mc.N_ITER == n_iter, f"N_ITER mismatch: {mc.N_ITER} != {n_iter}"

    version = mc.AlgorithmVersion.get_active_scores_version()
    print(f"  Algorithm version: {version.git_commit} (id={version.id})", flush=True)

    pdir = _partial_dir(stage_label)
    n_done = sum(1 for (wl, _, _) in windows if (pdir / f"{wl}.json").exists())
    if args.no_resume:
        print(f"  --no-resume: recomputing all {len(windows)} windows", flush=True)
    elif n_done:
        print(f"  RESUME: {n_done}/{len(windows)} windows already on disk -> skipping those", flush=True)

    for wi, (wlabel, ws, we) in enumerate(windows, 1):
        pp = pdir / f"{wlabel}.json"
        if pp.exists() and not args.no_resume:
            print(f"[{wi}/{len(windows)}] SKIP {wlabel} (partial exists)", flush=True)
            continue
        print(f"\n[{wi}/{len(windows)}] WINDOW {wlabel}  {ws} -> {we}", flush=True)
        win_paths = run_window_inproc(mc, version, wlabel, ws, we, cells,
                                      n_iter, args.starting_cash, workers=args.workers)
        # Atomic-ish write: tmp then replace, so a kill mid-write never leaves a
        # half-written partial that resume would trust.
        tmp = pp.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({
            'window': wlabel, 'start': ws.isoformat(), 'end': we.isoformat(),
            'n_iter': n_iter, 'cells': win_paths,
        }))
        os.replace(tmp, pp)
        # brief per-window summary
        for c in cells:
            recs = win_paths.get(c.name, [])
            m = compute_cell_metrics(c.name, recs, n_windows=1,
                                     n_iter_per_window=n_iter, gross_pct=c.gross_pct())
            print(f"    {c.name:<16} 2x_ever={m.p_2x_ever:.3f} 2x<50dd={m.p_2x_before_50dd:.3f} "
                  f"coll={m.p_collapse:.3f} medD2x={m.median_days_2x} "
                  f"medCmp={m.median_compound_pct} wDD={m.worst_dd_pct}", flush=True)

    # Assemble final from all partials present.
    results = assemble_from_partials(stage_label, cells, windows, n_iter, args.starting_cash)
    payload = {
        'stage': args.stage, 'tag': args.tag or None, 'mode': 'inproc', 'n_iter': n_iter,
        'starting_cash': args.starting_cash, 'horizon_days': args.horizon_days,
        'step_months': args.step_months, 'n_windows': len(windows),
        # provenance: the deployment caps the arm actually ran under (env-driven;
        # workers inherit these on spawn). 50% = shipped sprint; 100% = full-deploy arm.
        'gross_premium_cap': getattr(mc, 'GROSS_PREMIUM_CAP', None),
        'call_premium_cap': getattr(mc, 'CALL_PREMIUM_CAP', None),
        'practical_exposure_enabled': getattr(mc, 'PRACTICAL_EXPOSURE_ENABLED', None),
        # DTE provenance: the honest engine models an N-calendar-day option via
        # NOMINAL_CAL_DTE (premium + sigma barriers scale by sqrt(DTE/30); theta
        # over NOMINAL_CAL_DTE cal days). 30/27 = the 30DTE Apex; 15/13 = 15DTE.
        'calendar_hold': getattr(mc, 'CALENDAR_HOLD', None),
        'nominal_cal_dte': getattr(mc, 'NOMINAL_CAL_DTE', None),
        'hold_cal_days': getattr(mc, 'HOLD_CAL_DAYS', None),
        'window_span': [windows[0][1].isoformat(), windows[-1][2].isoformat()] if windows else None,
        'cells': results,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\n[WROTE] {out_path}  ({len(results)} cells from "
          f"{sum(1 for (wl,_,_) in windows if (pdir/f'{wl}.json').exists())} window partials)",
          flush=True)


if __name__ == '__main__':
    main()
