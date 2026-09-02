#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase B job runner -- TP/SL blast-radius refinement (PREREG.md section 4).
Thin variant of phaseA_run.py: same architecture (loader cache, put-skip,
per-cell set_tpsl + fresh-pool-via-default-path, CSV append + atomic per-job
state, resumable, progress lines) -- only the grid, N_ITER, window set, and
one bonus CSV column differ. Reuses the SAME driver/state/meta.json version
pin Phase A resolved (PREREG section 7: "ALGORITHM_VERSION_PIN = the active
version id recorded at Phase A start").

    python experiments/tpsl_refine_2026_08/driver/phaseB_run.py \
        --job NAME --profile core|apex --windows LBL[,LBL...] \
        [--cells "tp:sl,tp:sl,..."] [--smoke]

Locked spec: experiments/tpsl_refine_2026_08/{PREREG,LESSONS,TASK}.md section 4.
Engine contract: .claude/skills/run-monte-carlo/SKILL.md.
Cell grid derivation: driver/build_phaseB_cells.py (run once; output hand-
copied into CORE_CELLS/APEX_CELLS below -- NOT re-derived at job-run time, so
a queue job never depends on out/phaseA_*.csv still being present/unchanged).

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
# experiments/tpsl_refine_2026_08/driver/phaseB_run.py). Explicit + asserted,
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
META_PATH = os.path.join(STATE_DIR, 'meta.json')   # SAME file Phase A wrote -- reused, not re-resolved

# Windows -- LOCKED, PREREG.md section 4 (all nine, exact labels; paired
# seeds = identical label strings, never invented/renamed).
PHASE_B_WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y', '2020_crash']

# Cell grids -- LOCKED, PREREG.md section 4 fill rule, derived by
# driver/build_phaseB_cells.py from the Phase A carried lists (given
# verbatim, not re-derived) + the mechanical +-0.05 fill + dedup + cap-30
# tie-break (drop fills of the worst-22-now-worst_dd-ranked carried parents
# first). Both profiles independently land at exactly 30/30. Do not edit by
# hand or recompute inline -- re-run build_phaseB_cells.py and re-copy if the
# carried lists ever need to change (they may not, per PREREG's lock).
CORE_CELLS = [
    (0.10, -1.00), (0.10, -0.90), (0.10, -0.60), (0.10, -0.50),
    (0.15, -1.00), (0.15, -0.95), (0.15, -0.90), (0.15, -0.85), (0.15, -0.80),
    (0.15, -0.75), (0.15, -0.70), (0.15, -0.65), (0.15, -0.60), (0.15, -0.55),
    (0.15, -0.50), (0.15, -0.45),
    (0.20, -1.00), (0.20, -0.90), (0.20, -0.75), (0.20, -0.70), (0.20, -0.65),
    (0.20, -0.60), (0.20, -0.50),
    (0.25, -1.00), (0.25, -0.90), (0.25, -0.80), (0.25, -0.70),
    (0.30, -0.80), (0.30, -0.70), (0.30, -0.60),
]
APEX_CELLS = [
    (0.10, -0.80), (0.10, -0.60), (0.10, -0.50), (0.10, -0.40), (0.10, -0.30),
    (0.15, -1.00), (0.15, -0.95), (0.15, -0.90), (0.15, -0.85), (0.15, -0.80),
    (0.15, -0.75), (0.15, -0.70), (0.15, -0.65), (0.15, -0.60), (0.15, -0.55),
    (0.15, -0.50), (0.15, -0.45), (0.15, -0.40), (0.15, -0.35), (0.15, -0.30),
    (0.15, -0.25),
    (0.20, -0.80), (0.20, -0.60), (0.20, -0.50), (0.20, -0.40), (0.20, -0.30),
    (0.25, -0.70), (0.25, -0.50),
    (0.30, -0.70), (0.30, -0.60),
]
assert len(CORE_CELLS) == 30 and len(set(CORE_CELLS)) == 30
assert len(APEX_CELLS) == 30 and len(set(APEX_CELLS)) == 30
assert (0.30, -0.70) in CORE_CELLS and (0.30, -0.70) in APEX_CELLS, "incumbent must be present"

SMOKE_CELLS = [(0.30, -0.70), (0.15, -0.90), (0.10, -0.30)]   # incumbent + 2 extremes, both grids' overlap
N_ITER_FULL = 300     # PREREG section 4
N_ITER_SMOKE = 20

CSV_FIELDS = [
    'phase', 'profile', 'window', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s',
    'realized_call_tp_pct',
]
# NOTE two departures from a byte-for-byte copy of phaseA's CSV_FIELDS, both
# documented here per the Phase B spec's "check what _simulate_window
# returns; if not free, skip" instruction:
#   - 'both_rate' carried over unchanged from Phase A (same reasoning: the 4
#     baked-outcome buckets tp/sl/hard/both should sum to 100%).
#   - 'realized_call_tp_pct' is NEW: mc._simulate_window's returned dict
#     (monte_carlo.py ~4679-4685) already aggregates result['call_tp'] --
#     mean, across the N iterations, of each iteration's REALIZED
#     (portfolio-simulated, position-cap-aware) call TP-hit rate -- at ZERO
#     extra cost (phaseA_run.py already reads other keys off this same dict;
#     this key was simply unread). This is DIFFERENT from the existing
#     'tp_rate' column, which is the deterministic baked-signal rate from
#     ctx['call_outcomes'] (see mc_patch.call_outcome_rates docstring) --
#     'realized_call_tp_pct' can differ from 'tp_rate' when position/tier
#     caps skip signals the baked classification counted.
#   call_sl / call_hard / put_tp / put_sl / put_hard were checked and NOT
#   added: run_single_sim computes call_sl/call_hard/put_* per-iteration
#   (monte_carlo.py:3731-3732) but _simulate_window's aggregation loop
#   (monte_carlo.py:4649-4654) only collects r['call_tp']/r['put_tp']/
#   r['call_trades']/r['put_trades'] into its returned dict -- call_sl/
#   call_hard/put_sl/put_hard are computed then silently discarded, NOT
#   free to recover without either a production-file edit (forbidden) or
#   duplicating _simulate_window's MP/seed/aggregation internals in the
#   driver (a rewrite, not a "thin variant"). Skipped per spec's fallback.
#   put_tp itself would additionally be a constant 0.0 in this sweep (puts
#   disabled via mc_patch.disable_puts -> put_outcomes={} -> put_trades=0 ->
#   put_tp=0/1*100=0.0 always) so it would carry no information even if free.


def parse_cells_arg(spec, locked_cells):
    """Parse an optional '--cells tp:sl,tp:sl,...' subset-spec against a
    profile's locked cell list. Returns the requested cells IN LOCKED-LIST
    ORDER (not spec order) so CSV/state output stays canonically ordered
    regardless of how a caller lists a subset. Raises SystemExit on any
    token not present in locked_cells (typo guard) -- no silent partial
    grids. `spec=None` (flag omitted) returns the full locked_cells list."""
    if spec is None:
        return list(locked_cells)
    locked_set = set(locked_cells)
    requested = set()
    for tok in spec.split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            tp_s, sl_s = tok.split(':')
            cell = (round(float(tp_s), 2), round(float(sl_s), 2))
        except ValueError:
            raise SystemExit(f"--cells token {tok!r} is not 'tp:sl' (e.g. '0.15:-0.90')")
        if cell not in locked_set:
            raise SystemExit(f"--cells token {tok!r} -> {cell} is not in this profile's locked "
                              f"Phase B grid -- not a typo-tolerant flag by design")
        requested.add(cell)
    return [c for c in locked_cells if c in requested]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job', required=True, help='job name -- output files are out/phaseB_<job>.csv etc')
    p.add_argument('--profile', required=True, choices=['core', 'apex'])
    p.add_argument('--windows', required=True,
                    help='comma-separated window labels -- must be a subset of the locked '
                         f'Phase B set {PHASE_B_WINDOWS} (paired-seed rule: never invent/rename)')
    p.add_argument('--cells', default=None,
                    help="optional 'tp:sl,tp:sl,...' subset of this profile's locked 30-cell grid "
                         '(default: all 30 -- windows-only sharding, mirrors Phase A)')
    p.add_argument('--smoke', action='store_true',
                    help='3 cells at N_ITER=20 instead of the full 30-cell x N=300 grid')
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

    window_labels = [w.strip() for w in args.windows.split(',') if w.strip()]
    bad = [w for w in window_labels if w not in PHASE_B_WINDOWS]
    if bad:
        raise SystemExit(f"unknown Phase B window label(s) {bad!r} -- must be a subset of "
                          f"the locked set {PHASE_B_WINDOWS}; never invent/rename labels (paired-seed rule)")

    locked_cells = CORE_CELLS if args.profile == 'core' else APEX_CELLS

    # 1) env BEFORE import -- frozen pins, profile overrides, version pin.
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(args.profile)
    version_meta = mc_patch.resolve_and_pin_version(META_PATH)   # reuses Phase A's pinned meta.json

    # 2) NOW import monte_carlo -- every env var above is already set, so
    # TIER_ALLOC/MAX_POSITIONS/GROSS_PREMIUM_CAP/CALL_PREMIUM_CAP/
    # NOMINAL_CAL_DTE/HOLD_CAL_DAYS/ALGORITHM_VERSION_PIN all bake correctly.
    import monte_carlo as mc

    # 3) post-import patches.
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (label, d0, d1) for label, d0, d1 in mc.WINDOWS}
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(f"window label(s) {missing!r} not in mc.WINDOWS {sorted(window_lookup)} "
                          f"-- engine WINDOWS list drifted from the locked Phase B set")

    cells = SMOKE_CELLS if args.smoke else parse_cells_arg(args.cells, locked_cells)
    n_iter = N_ITER_SMOKE if args.smoke else N_ITER_FULL
    mc.N_ITER = n_iter

    csv_path = os.path.join(OUT_DIR, f'phaseB_{args.job}.csv')
    parquet_path = os.path.join(OUT_DIR, f'phaseB_paths_{args.job}.parquet')
    state_path = os.path.join(STATE_DIR, f'phaseB_{args.job}.json')
    log_path = os.path.join(LOG_DIR, f'phaseB_{args.job}.log')

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

            mc_patch.set_tpsl(mc, tp, sl)   # stress=base (flat) -- Phase B too, PREREG section 4

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
                'phase': 'B', 'profile': args.profile, 'window': label,
                'tp': tp, 'sl': sl, 'n_iter': n_iter, 'n_call_signals': n_calls,
                'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
                'p10_ret': p10_ret, 'p90_ret': p90_ret,
                'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
                'p_coll': result.get('p_coll'),
                'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate,
                'both_rate': both_rate,
                'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
                'realized_call_tp_pct': result.get('call_tp'),
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
