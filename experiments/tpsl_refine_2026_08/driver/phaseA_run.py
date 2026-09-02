#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase A job runner -- TP/SL blast-radius refinement (PREREG.md section 2).

    python experiments/tpsl_refine_2026_08/driver/phaseA_run.py \
        --job NAME --profile core|apex --windows LBL[,LBL...] [--smoke]

Locked spec: experiments/tpsl_refine_2026_08/{PREREG,LESSONS,TASK}.md.
Engine contract: .claude/skills/run-monte-carlo/SKILL.md.

HARD RULE: this file and driver/mc_patch.py NEVER edit monte_carlo.py /
strategy_config.py / any tracked production file. All variant behavior is
in-process patching of the imported `mc` module object. This script never
git-commits anything.

Windows/multiprocessing note: monte_carlo._simulate_window builds its own
fresh multiprocessing.Pool per call (see mc_patch.apply_frozen_pins
docstring) -- on Windows (spawn) that means every worker re-imports this
script as a non-`__main__` module. Everything with side effects (arg parsing,
DB access, the cell loop) MUST stay inside `main()`, guarded by
`if __name__ == '__main__':` at the bottom -- never at module level.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from contextlib import redirect_stdout

# --- repo-root bootstrap (this file lives 3 levels under the repo root:
# experiments/tpsl_refine_2026_08/driver/phaseA_run.py). Explicit + asserted,
# never inferred from CWD -- see traps.md "Worktree PYTHONPATH trap": pin
# sys.path and verify __file__ resolves where expected rather than trusting
# ambient state. Safe to re-run (idempotent) inside spawned MP workers. -------
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

EXP_DIR = os.path.dirname(_THIS_DIR)                       # experiments/tpsl_refine_2026_08
OUT_DIR = os.path.join(EXP_DIR, 'out')
LOG_DIR = os.path.join(EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
META_PATH = os.path.join(STATE_DIR, 'meta.json')

# Grid -- LOCKED, PREREG.md section 2. TP-major so windows/cells print in a
# stable, readable order. Do not edit without re-reading PREREG.md; the grid
# may not change after Phase A starts.
TP_GRID = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.65, 0.85, 1.10]
SL_GRID = [-0.30, -0.40, -0.50, -0.60, -0.70, -0.80, -0.90, -1.00]
FULL_CELLS = [(tp, sl) for tp in TP_GRID for sl in SL_GRID]        # 80 cells
SMOKE_CELLS = [(0.30, -0.70), (0.15, -0.30), (1.10, -1.00)]        # 3 cells
N_ITER_FULL = 150
N_ITER_SMOKE = 20

CSV_FIELDS = [
    'phase', 'profile', 'window', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s',
]
# NOTE 'both_rate' is ONE column beyond PREREG's literal CSV list (which asks
# for tp_rate/sl_rate/hard_rate only) -- added so the 4 baked-outcome buckets
# (tp/sl/hard/both -- 'both' = same-bar TP+SL collision) sum to 100% instead of
# silently leaving a gap; see mc_patch.call_outcome_rates docstring.


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job', required=True, help='job name -- output files are out/phaseA_<job>.csv etc')
    p.add_argument('--profile', required=True, choices=['core', 'apex'])
    p.add_argument('--windows', required=True,
                    help='comma-separated ENGINE preset window labels (must exist in mc.WINDOWS) -- never invented/renamed (paired-seed rule)')
    p.add_argument('--smoke', action='store_true',
                    help='3 extreme cells at N_ITER=20 instead of the full 80-cell x N=150 grid')
    return p.parse_args()


def _load_json(path, default):
    if os.path.exists(path):
        import json
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def _tee(msg, log_f):
    print(msg, flush=True)
    log_f.write(msg + '\n')
    log_f.flush()


def main():
    args = parse_args()
    import mc_patch   # local module, driver/mc_patch.py -- safe: monte_carlo not yet imported

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    # 1) env BEFORE import -- frozen pins, profile overrides, version pin.
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(args.profile)
    version_meta = mc_patch.resolve_and_pin_version(META_PATH)

    # 2) NOW import monte_carlo -- every env var above is already set, so
    # TIER_ALLOC/MAX_POSITIONS/GROSS_PREMIUM_CAP/CALL_PREMIUM_CAP/
    # NOMINAL_CAL_DTE/HOLD_CAL_DAYS/ALGORITHM_VERSION_PIN all bake correctly.
    import monte_carlo as mc

    # 3) post-import patches.
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (label, d0, d1) for label, d0, d1 in mc.WINDOWS}
    window_labels = [w.strip() for w in args.windows.split(',') if w.strip()]
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(
            f"unknown window label(s) {missing!r} -- not in mc.WINDOWS "
            f"{sorted(window_lookup)}; never invent/rename labels (paired-seed rule)")

    cells = SMOKE_CELLS if args.smoke else FULL_CELLS
    n_iter = N_ITER_SMOKE if args.smoke else N_ITER_FULL
    mc.N_ITER = n_iter

    csv_path = os.path.join(OUT_DIR, f'phaseA_{args.job}.csv')
    parquet_path = os.path.join(OUT_DIR, f'phaseA_paths_{args.job}.parquet')
    state_path = os.path.join(STATE_DIR, f'phaseA_{args.job}.json')
    log_path = os.path.join(LOG_DIR, f'phaseA_{args.job}.log')

    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}   # (window, tp, sl)

    csv_is_new = not os.path.exists(csv_path)
    csv_f = open(csv_path, 'a', newline='', encoding='utf-8')
    csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if csv_is_new:
        csv_w.writeheader()
        csv_f.flush()

    # Per-iteration parquet accumulator. Parquet has no cheap append, so seed
    # from any existing file (earlier cells from a prior/interrupted run of
    # THIS job) and rewrite the whole file after each cell.
    path_rows = []
    pl = None
    try:
        import polars as _pl
        pl = _pl
        if os.path.exists(parquet_path):
            path_rows = pl.read_parquet(parquet_path).to_dicts()
    except ImportError:
        print("[warn] polars unavailable -- per-iteration parquet dump DISABLED "
              "for this job. med_ret/p10_ret/p90_ret in the CSV are UNAFFECTED "
              "(computed in-process from result['finals'] regardless of polars).",
              flush=True)

    log_f = open(log_path, 'a', encoding='utf-8')

    _tee(f"\n{'='*100}", log_f)
    _tee(f"JOB {args.job}  profile={args.profile}  windows={window_labels}  "
         f"smoke={args.smoke}  n_iter={n_iter}  cells={len(cells)}", log_f)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} "
         f"git_commit={version_meta['git_commit']} (pinned meta={META_PATH})", log_f)
    _tee(f"[CONFIG] MAX_POSITIONS={mc.MAX_POSITIONS} "
         f"MAX_POSITIONS_CALL={mc.MAX_POSITIONS_CALL} "
         f"MAX_POSITIONS_PUT={mc.MAX_POSITIONS_PUT}", log_f)
    _tee(f"[CONFIG] TIER_ALLOC={mc.TIER_ALLOC}  PUT_TIER_ALLOC={mc.PUT_TIER_ALLOC}", log_f)
    _tee(f"[CONFIG] GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} "
         f"CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP} "
         f"PRACTICAL_EXPOSURE_ENABLED={mc.PRACTICAL_EXPOSURE_ENABLED}", log_f)
    _tee(f"[CONFIG] CALENDAR_HOLD={mc.CALENDAR_HOLD} NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE} "
         f"HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS}", log_f)
    _tee(f"[CONFIG] MC_WORKERS={os.environ.get('MC_WORKERS')} "
         f"MC_NO_DB_PERSIST={os.environ.get('MC_NO_DB_PERSIST')} "
         f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR}", log_f)
    _tee(f"{'='*100}", log_f)

    total_cells = len(window_labels) * len(cells)
    i_cell = 0
    cells_run_now = 0
    t_job0 = time.time()

    for label in window_labels:
        _, d_start, d_end = window_lookup[label]
        for tp, sl in cells:
            i_cell += 1
            key = (label, tp, sl)
            if key in done_set:
                print(f"[{i_cell}/{total_cells}] SKIP (already done) job={args.job} "
                      f"window={label} tp={tp} sl={sl}", flush=True)
                continue
            cells_run_now += 1

            mc_patch.set_tpsl(mc, tp, sl)   # stress=base (flat) -- Phase A only

            t0 = time.perf_counter()
            with redirect_stdout(log_f):
                ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
            t_prepare = time.perf_counter() - t0

            n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

            t1 = time.perf_counter()
            with redirect_stdout(log_f):
                sim = mc._simulate_window(ctx)   # no external pool -> fresh pool, own teardown
            t_sim = time.perf_counter() - t1
            result = sim['seeded']

            finals = result.get('finals')
            p10_ret = p90_ret = None
            if finals:
                rets_pct = sorted((f / mc.STARTING_CASH - 1.0) * 100.0 for f in finals)
                p10_ret = mc_patch.pct(rets_pct, 0.10)
                p90_ret = mc_patch.pct(rets_pct, 0.90)
                for i_iter, r in enumerate(rets_pct):
                    path_rows.append({'profile': args.profile, 'window': label,
                                       'tp': tp, 'sl': sl, 'iter': i_iter, 'ret': r})

            row = {
                'phase': 'A', 'profile': args.profile, 'window': label,
                'tp': tp, 'sl': sl, 'n_iter': n_iter, 'n_call_signals': n_calls,
                'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
                'p10_ret': p10_ret, 'p90_ret': p90_ret,
                'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
                'p_coll': result.get('p_coll'),
                'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate,
                'both_rate': both_rate,
                'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
            }
            csv_w.writerow(row)
            csv_f.flush()

            if pl is not None:
                try:
                    pl.DataFrame(path_rows).write_parquet(parquet_path)
                except Exception as e:
                    print(f"[warn] parquet write failed ({e}); continuing (CSV is authoritative)", flush=True)

            done_set.add(key)
            state['done_cells'] = [list(k) for k in sorted(done_set)]
            state['job'] = args.job
            state['profile'] = args.profile
            state['windows'] = window_labels
            state['smoke'] = bool(args.smoke)
            state['n_iter'] = n_iter
            state['algorithm_version'] = version_meta
            mc_patch.atomic_write_json(state_path, state)

            net_csl = mc.NET_SL_BASE
            print(f"[{i_cell}/{total_cells}] job={args.job} window={label} tp={tp:+.2f} sl={sl:+.2f} "
                  f"net_csl={net_csl:+.3f} n={n_iter} ncalls={n_calls} "
                  f"prepare={t_prepare:.1f}s sim={t_sim:.1f}s | "
                  f"tp_rate={tp_rate:.1f}% sl_rate={sl_rate:.1f}% hard_rate={hard_rate:.1f}% | "
                  f"worst_dd={result.get('worst_dd'):.1f}% med_ret={result.get('med_ret'):+.1f}% "
                  f"p_coll={result.get('p_coll'):.1f}%", flush=True)

    csv_f.close()
    elapsed_job = time.time() - t_job0
    _tee(f"\n[DONE] job={args.job} cells_run_this_invocation={cells_run_now} "
         f"total_done={len(done_set)}/{total_cells} wall={elapsed_job:.1f}s "
         f"-> {csv_path}", log_f)
    log_f.close()


if __name__ == '__main__':
    main()
