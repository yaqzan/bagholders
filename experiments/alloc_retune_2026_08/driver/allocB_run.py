#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase B job runner -- allocation re-sweep refine stage (PREREG.md section 1
"Refine (N=300 x 9 win)" + the Phase B dispatch brief's exact carried/
neighbor cell lists). THIN VARIANT of allocA_run.py: identical architecture
(loader-cache, shared-prepare-per-window + reused pool for sizing cells,
cell-params via _apply_cell_params, put-skip, resumable per-job state JSON,
CSV append, per-iteration parquet dump, gross sharding) -- only the grid
(a curated list instead of a formula), N_ITER (300 not 150), and the window
set (9 labels not 4) differ. CoreCell/ApexCell/CORE_SHAPES/CORE_BASELINE/
APEX_BASELINE/CSV_FIELDS/_load_json/_tee/_fround are IMPORTED from
allocA_run.py, not re-defined -- see that module for their docstrings.

    python experiments/alloc_retune_2026_08/driver/allocB_run.py \
        --job NAME --profile core|apex --windows LBL[,LBL...] \
        [--gross F]   # REQUIRED for --profile core, FORBIDDEN for --profile apex
        [--smoke]

Locked spec: experiments/alloc_retune_2026_08/PREREG.md section 1 "Refine" +
TASK.md, inheriting experiments/tpsl_refine_2026_08/{PREREG,LESSONS}.md
sections 3/7 verbatim unless overridden. Engine contract:
.claude/skills/run-monte-carlo/SKILL.md.

HARD RULE: this file NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file, and NEVER edits anything under
experiments/tpsl_refine_2026_08/ (its mc_patch.py is IMPORTED, not copied).
All variant behavior is in-process patching of the imported `mc` module
object. This script never git-commits anything.

CELL GRID (LOCKED): CORE_CELLS / APEX_CELLS below are the mechanical output
of driver/build_phaseB_cells.py (run once; printed output hand-copied here --
NOT re-derived at job-run time, so a queue job never depends on
out/allocA_summary.md still being present/unchanged). Re-run
build_phaseB_cells.py and re-copy if the carried lists ever need to change
(they may not, per PREREG's lock). CORE = 15 carried + 1 flat-added baseline
(S0/mp14/g0.50, outside the {S9,S8,S3} neighbor-generation universe -- never
itself generates neighbors) + 24 new one-grid-step-per-axis neighbor fills,
capped at 40 total (11 fills dropped by worst-parent-first Phase A 22-now
DD ranking). APEX = 9 carried (all Phase A blast survivors) + 3 new fills,
12 total, well under the cap-25 ceiling (no truncation fired).

GROSS/CALL PREMIUM CAP MECHANISM -- unchanged from Phase A, see
allocA_run.py's module docstring for the full recon (import-time module
globals read LIVE inside spawned MP workers, NOT covered by
_apply_cell_params/_broadcast_cell_params -> shard by gross, env set before
import). Phase B's core cell list spans FOUR distinct gross values
(0.30, 0.40, 0.50, 0.65 -- 0.80 never survived the cap-40 truncation, see
build_phaseB_cells.py's dropped-cell log), one job per value, same as Phase
A's --gross plumbing verbatim (PHASE_B_CORE_GROSS_VALUES below, derived from
CORE_CELLS itself so it can never drift from the locked grid).

APEX TP/SL PIN -- unchanged from Phase A, see allocA_run.py's module
docstring "APEX TP/SL PIN". Set here identically (TP_BASE_OV=TP_STRESS_OV=
0.10, SL_BASE_OV=SL_STRESS_OV=-0.60) since this campaign never calls
set_tpsl() (TP/SL frozen for the whole alloc_retune_2026_08 campaign, both
phases).

Windows/multiprocessing note (same as allocA_run.py): monte_carlo
_simulate_window/_make_window_pool build multiprocessing.Pool objects -- on
Windows (spawn) every worker re-imports this script as a non-`__main__`
module. Everything with side effects (arg parsing, DB access, the
window/cell loop) MUST stay inside `main()`, guarded by
`if __name__ == '__main__':` at the bottom -- never at module level.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from contextlib import redirect_stdout

# --- repo-root + cross-experiment bootstrap -- IDENTICAL to allocA_run.py's
# (this file lives in the same driver/ dir). Explicit + asserted, never
# inferred from CWD -- see traps.md "Worktree PYTHONPATH trap". -------------
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
assert os.path.isfile(os.path.join(_TPSL_DRIVER_DIR, 'mc_patch.py')), (
    f"tpsl driver mc_patch.py not found at {_TPSL_DRIVER_DIR!r} -- PREREG section 2 "
    f"requires reusing it in-place; this experiment must never copy/duplicate it"
)

# allocA_run.py -- reused directly (thin variant): CoreCell/ApexCell
# dataclasses, CORE_SHAPES, CORE_BASELINE/APEX_BASELINE, CSV_FIELDS,
# _load_json/_tee/_fround. Module-level-only import (no monte_carlo side
# effects at import time -- allocA_run.py's own side-effecting code lives
# inside main(), guarded by __main__), same pattern test_analyze_allocA.py
# already uses.
from allocA_run import (                                                     # noqa: E402
    CoreCell, ApexCell, CORE_SHAPES, CORE_BASELINE, APEX_BASELINE,
    CSV_FIELDS, _load_json, _tee, _fround,
)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TPSL_META_PATH = os.path.join(_TPSL_DRIVER_DIR, 'state', 'meta.json')

# ---------------------------------------------------------------------------
# Cell grids -- LOCKED. Mechanical output of build_phaseB_cells.py, hand-
# copied. Do not edit without re-running that script and re-reading PREREG;
# the Phase B cell list may not change after Phase B starts.
# ---------------------------------------------------------------------------
CORE_CELLS = [
    ('S0', 14, 0.5), ('S3', 12, 0.4), ('S8', 10, 0.4), ('S8', 10, 0.5), ('S8', 10, 0.65),
    ('S8', 12, 0.4), ('S8', 12, 0.5), ('S8', 12, 0.65), ('S8', 14, 0.4), ('S8', 14, 0.5),
    ('S8', 14, 0.65), ('S8', 16, 0.4), ('S8', 16, 0.5), ('S8', 16, 0.65), ('S8', 18, 0.4),
    ('S8', 18, 0.5), ('S8', 18, 0.65), ('S8', 20, 0.4), ('S8', 20, 0.5), ('S8', 20, 0.65),
    ('S8', 22, 0.5), ('S8', 8, 0.5), ('S9', 10, 0.3), ('S9', 10, 0.4), ('S9', 10, 0.5),
    ('S9', 12, 0.3), ('S9', 12, 0.4), ('S9', 12, 0.5), ('S9', 14, 0.4), ('S9', 16, 0.3),
    ('S9', 16, 0.4), ('S9', 16, 0.5), ('S9', 18, 0.3), ('S9', 18, 0.4), ('S9', 18, 0.5),
    ('S9', 20, 0.3), ('S9', 20, 0.4), ('S9', 20, 0.5), ('S9', 20, 0.65), ('S9', 8, 0.4),
]
APEX_CELLS = [
    (10, 0.06), (10, 0.08), (10, 0.1), (12, 0.06), (12, 0.08), (4, 0.15),
    (6, 0.1), (6, 0.125), (6, 0.15), (8, 0.08), (8, 0.1), (8, 0.125),
]
assert len(CORE_CELLS) == 40 and len(set(CORE_CELLS)) == 40
assert len(APEX_CELLS) == 12 and len(set(APEX_CELLS)) == 12
assert CORE_BASELINE in CORE_CELLS, "core in-phase baseline S0/mp14/g0.50 missing"
assert APEX_BASELINE in APEX_CELLS, "apex baseline n10/f0.10 missing"

PHASE_B_CORE_GROSS_VALUES = sorted({_fround(g) for (_s, _mp, g) in CORE_CELLS})   # [0.3, 0.4, 0.5, 0.65]

PHASE_B_WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y', '2020_crash']

# SMOKE grid -- 2 cells x N=20 per profile (BUILD spec section "SMOKE"; the
# "1 window" part of that spec is a CALLER choice via --windows, exactly
# like allocA_run.py -- --smoke never overrides --windows here, only the
# cell list and N_ITER, so this file stays a true thin variant). Builder's
# choice of WHICH 2 cells within the fixed count: one already-carried cell
# (proves the thin variant reproduces a known-good Phase A result) + one
# genuinely NEW neighbor cell born only from Phase B's own grid-extension
# (proves neighbor-list generation actually reached past the Phase A blast
# bounds and runs cleanly through the engine). Both core smoke cells share
# gross=0.40 so ONE smoke job covers both (no need to spread across multiple
# --gross smoke invocations). Intended smoke invocation: --windows 22-now.
SMOKE_CORE_CELLS = [('S9', 10, 0.40), ('S9', 8, 0.40)]     # carried (#1 by 22-now dd) + its mp-8 extension
SMOKE_APEX_CELLS = [(10, 0.10), (10, 0.06)]                # baseline (carried) + its frac-0.06 extension

N_ITER_FULL = 300     # PREREG section 1 "Refine (N=300 x 9 win)"
N_ITER_SMOKE = 20


# ---------------------------------------------------------------------------
# Cell-list -> CoreCell/ApexCell instances (pure, side-effect-free).
# ---------------------------------------------------------------------------
def build_core_cells(gross: float) -> list:
    g = _fround(gross)
    cells = []
    for shape_name, max_pos, cg in CORE_CELLS:
        if _fround(cg) != g:
            continue
        ultra, top, mid, low = CORE_SHAPES[shape_name]
        is_baseline = (shape_name, max_pos, g) == CORE_BASELINE
        cells.append(CoreCell(shape_name, ultra, top, mid, low, max_pos, g, is_baseline))
    if not cells:
        raise SystemExit(f"--gross {gross} ({g}) matches no Phase B core cell -- "
                          f"valid values: {PHASE_B_CORE_GROSS_VALUES}")
    return cells


def build_apex_cells() -> list:
    cells = []
    for n, frac in APEX_CELLS:
        is_baseline = (n, frac) == APEX_BASELINE
        cells.append(ApexCell(n, frac, is_baseline))
    return cells


def smoke_core_cells(gross: float) -> list:
    g = _fround(gross)
    matched = [t for t in SMOKE_CORE_CELLS if _fround(t[2]) == g]
    if not matched:
        raise SystemExit(
            f"--smoke --gross {gross}: no smoke cell defined at this gross value "
            f"(SMOKE_CORE_CELLS gross values: {sorted({_fround(t[2]) for t in SMOKE_CORE_CELLS})})")
    out = []
    for (shape_name, max_pos, cg) in matched:
        ultra, top, mid, low = CORE_SHAPES[shape_name]
        is_baseline = (shape_name, max_pos, _fround(cg)) == CORE_BASELINE
        out.append(CoreCell(shape_name, ultra, top, mid, low, max_pos, cg, is_baseline))
    return out


def smoke_apex_cells() -> list:
    return [ApexCell(n, frac, (n, frac) == APEX_BASELINE) for (n, frac) in SMOKE_APEX_CELLS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job', required=True, help='job name -- output files are out/allocB_<job>.csv etc')
    p.add_argument('--profile', required=True, choices=['core', 'apex'])
    p.add_argument('--windows', required=True,
                    help=f'comma-separated window labels -- must be a subset of the locked Phase B set '
                         f'{PHASE_B_WINDOWS} (paired-seed rule: never invent/rename labels)')
    p.add_argument('--gross', type=float, default=None,
                    help='REQUIRED for --profile core (one gross value per job -- see module docstring '
                         '"GROSS/CALL PREMIUM CAP MECHANISM"); FORBIDDEN for --profile apex '
                         '(fixed 1.0 via mc_patch.APEX_ENV_DIFF). Valid core values: '
                         f'{PHASE_B_CORE_GROSS_VALUES}')
    p.add_argument('--smoke', action='store_true',
                    help='fixed smoke grid at N_ITER=20 instead of the full Phase B grid at N=300 '
                         '(--windows still applies as normal -- pass e.g. --windows 22-now for a '
                         'single-window smoke): core picks the SMOKE_CORE_CELLS entries matching '
                         '--gross; apex runs the fixed 2-cell SMOKE_APEX_CELLS list')
    return p.parse_args()


def main():
    args = parse_args()

    if args.profile == 'core':
        if args.gross is None:
            raise SystemExit("--gross is REQUIRED for --profile core (PREREG section 2: gross is "
                              "sharded at the job/process level -- see module docstring)")
        if not args.smoke and _fround(args.gross) not in PHASE_B_CORE_GROSS_VALUES:
            raise SystemExit(f"--gross {args.gross} not in the locked Phase B core grid "
                              f"{PHASE_B_CORE_GROSS_VALUES}")
    else:
        if args.gross is not None:
            raise SystemExit("--gross is FORBIDDEN for --profile apex (fixed 1.0 via "
                              "mc_patch.APEX_ENV_DIFF -- Apex never sweeps gross per PREREG section 1)")

    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {TPSL_META_PATH} missing. PREREG section 2 pins this campaign's "
            f"ALGORITHM_VERSION to the SAME id Phase A resolved (reusing the tpsl "
            f"campaign's meta.json, unchanged from allocA_run.py). Refusing to "
            f"auto-re-resolve. Report to orchestrator rather than improvising.")

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    # 1) env BEFORE import -- identical ordering/rationale to allocA_run.py.
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(args.profile)
    if args.profile == 'core':
        os.environ['GROSS_PREMIUM_CAP'] = repr(args.gross)
        os.environ['CALL_PREMIUM_CAP'] = repr(args.gross)
    else:
        # Apex TP/SL pin -- see allocA_run.py module docstring "APEX TP/SL PIN".
        os.environ['TP_BASE_OV'] = '0.10'
        os.environ['TP_STRESS_OV'] = '0.10'
        os.environ['SL_BASE_OV'] = '-0.60'
        os.environ['SL_STRESS_OV'] = '-0.60'
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    # 2) NOW import monte_carlo -- every env var above is already set.
    import monte_carlo as mc

    # 3) post-import patches (identical to allocA_run.py / tpsl phaseA_run.py).
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    # 4) hard verification -- fail fast rather than silently sweeping a wrong
    # config (same G1 gross-cap + Apex TP/SL proof as allocA_run.py).
    if args.profile == 'core':
        assert abs(mc.GROSS_PREMIUM_CAP - args.gross) < 1e-9, \
            f"GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} != requested --gross {args.gross} -- env-before-import propagation FAILED"
        assert abs(mc.CALL_PREMIUM_CAP - args.gross) < 1e-9, \
            f"CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP} != requested --gross {args.gross} -- env-before-import propagation FAILED"
        assert abs(mc.TP_BASE - 0.10) < 1e-9 and abs(mc.SL_BASE - (-1.00)) < 1e-9, \
            f"Core TP_BASE/SL_BASE drifted from the shipped default (got {mc.TP_BASE}/{mc.SL_BASE}) -- TP/SL must stay untouched"
    else:
        assert abs(mc.GROSS_PREMIUM_CAP - 1.0) < 1e-9 and abs(mc.CALL_PREMIUM_CAP - 1.0) < 1e-9, \
            f"Apex GROSS/CALL_PREMIUM_CAP != 1.0 (got {mc.GROSS_PREMIUM_CAP}/{mc.CALL_PREMIUM_CAP})"
        assert abs(mc.TP_BASE - 0.10) < 1e-9 and abs(mc.SL_BASE - (-0.60)) < 1e-9, \
            f"Apex TP_BASE/SL_BASE != the shipped pin 0.10/-0.60 (got {mc.TP_BASE}/{mc.SL_BASE})"

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    window_labels = [w.strip() for w in args.windows.split(',') if w.strip()]
    bad = [w for w in window_labels if w not in PHASE_B_WINDOWS]
    if bad:
        raise SystemExit(f"window label(s) {bad!r} not in the locked Phase B set {PHASE_B_WINDOWS} -- "
                          f"never invent/rename labels (paired-seed rule)")
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(
            f"unknown window label(s) {missing!r} -- not in mc.WINDOWS "
            f"{sorted(window_lookup)}; engine WINDOWS list drifted from the locked Phase B set")

    if args.profile == 'core':
        cells = smoke_core_cells(args.gross) if args.smoke else build_core_cells(args.gross)
    else:
        cells = smoke_apex_cells() if args.smoke else build_apex_cells()
    n_iter = N_ITER_SMOKE if args.smoke else N_ITER_FULL
    mc.N_ITER = n_iter
    n_workers = int(os.environ.get('MC_WORKERS', '6'))

    csv_path = os.path.join(OUT_DIR, f'allocB_{args.job}.csv')
    parquet_path = os.path.join(OUT_DIR, f'allocB_paths_{args.job}.parquet')
    state_path = os.path.join(STATE_DIR, f'allocB_{args.job}.json')
    log_path = os.path.join(LOG_DIR, f'allocB_{args.job}.log')

    state = _load_json(state_path, {'done_pairs': []})
    done_set = {tuple(p) for p in state.get('done_pairs', [])}   # (window, cell_name)

    csv_is_new = not os.path.exists(csv_path)
    csv_f = open(csv_path, 'a', newline='', encoding='utf-8')
    csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if csv_is_new:
        csv_w.writeheader()
        csv_f.flush()

    path_rows = []
    pl = None
    try:
        import polars as _pl
        pl = _pl
        if os.path.exists(parquet_path):
            path_rows = pl.read_parquet(parquet_path).to_dicts()
    except ImportError:
        print("[warn] polars unavailable -- per-iteration parquet dump DISABLED for this job. "
              "med_ret/p10_ret/p90_ret in the CSV are UNAFFECTED.", flush=True)

    log_f = open(log_path, 'a', encoding='utf-8')

    _tee(f"\n{'='*100}", log_f)
    _tee(f"JOB {args.job}  profile={args.profile}  windows={window_labels}  gross={args.gross}  "
         f"smoke={args.smoke}  n_iter={n_iter}  cells={len(cells)}", log_f)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} "
         f"git_commit={version_meta['git_commit']} (pinned from {TPSL_META_PATH})", log_f)
    _tee(f"[CONFIG] MAX_POSITIONS_CALL cells: {sorted({c.max_pos if hasattr(c,'max_pos') else c.n for c in cells})}", log_f)
    _tee(f"[CONFIG] GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP}  CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP}  "
         f"PUT_PREMIUM_CAP={mc.PUT_PREMIUM_CAP}  PRACTICAL_EXPOSURE_ENABLED={mc.PRACTICAL_EXPOSURE_ENABLED}", log_f)
    _tee(f"[CONFIG] TP_BASE={mc.TP_BASE}  TP_STRESS={mc.TP_STRESS}  SL_BASE={mc.SL_BASE}  "
         f"SL_STRESS={mc.SL_STRESS}  <- FROZEN, never patched by this campaign", log_f)
    _tee(f"[CONFIG] CALENDAR_HOLD={mc.CALENDAR_HOLD} NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE} "
         f"HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS}", log_f)
    _tee(f"[CONFIG] MC_WORKERS={os.environ.get('MC_WORKERS')} MC_NO_DB_PERSIST={os.environ.get('MC_NO_DB_PERSIST')} "
         f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR}", log_f)
    _tee(f"[CONFIG] cell_names={[c.cell_name for c in cells]}", log_f)
    _tee(f"{'='*100}", log_f)

    total_pairs = len(window_labels) * len(cells)
    i_pair = 0
    pairs_run_now = 0
    t_job0 = time.time()

    for label in window_labels:
        d_start, d_end = window_lookup[label]
        todo = [c for c in cells if (label, c.cell_name) not in done_set]
        if not todo:
            i_pair += len(cells)
            print(f"[window {label}] SKIP (all {len(cells)} cells already done)", flush=True)
            continue

        t0 = time.perf_counter()
        with redirect_stdout(log_f):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)
        print(f"[window {label}] PREPARE done in {t_prepare:.1f}s  n_call_signals={n_calls}  "
              f"tp_rate={tp_rate}  sl_rate={sl_rate}  hard_rate={hard_rate}  "
              f"(shared by {len(todo)} cell(s) this window)", flush=True)

        use_mp = os.environ.get('MC_NO_MP', '0') != '1'
        pool = None
        if use_mp and mc.N_ITER >= 16 and len(todo) > 1:
            pool = mc._make_window_pool(ctx, n_workers)

        prepare_charged = False
        try:
            for cell in cells:
                i_pair += 1
                key = (label, cell.cell_name)
                if key in done_set:
                    print(f"[{i_pair}/{total_pairs}] SKIP (done) job={args.job} window={label} "
                          f"cell={cell.cell_name}", flush=True)
                    continue
                pairs_run_now += 1
                cp = cell.cell_params()

                t1 = time.perf_counter()
                with redirect_stdout(log_f):
                    sim = mc._simulate_window(ctx, cell_params=cp, persist=False, pool=pool)
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
                                           'cell_name': cell.cell_name, 'iter': i_iter, 'ret': r})

                row_prepare_s = round(t_prepare, 3) if not prepare_charged else 0.0
                prepare_charged = True

                row = {
                    'phase': 'B', 'profile': args.profile, 'window': label,
                    **cell.row_fields(),
                    'n_iter': n_iter, 'n_call_signals': n_calls,
                    'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
                    'p10_ret': p10_ret, 'p90_ret': p90_ret,
                    'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
                    'p_coll': result.get('p_coll'),
                    'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
                    'avg_open_premium_base_pct': result.get('avg_open_premium_base_pct'),
                    'max_open_premium_base_pct': result.get('max_open_premium_base_pct'),
                    'call_trades': result.get('call_trades'), 'put_trades': result.get('put_trades'),
                    'elapsed_prepare_s': row_prepare_s, 'elapsed_sim_s': round(t_sim, 3),
                }
                csv_w.writerow(row)
                csv_f.flush()

                if pl is not None:
                    try:
                        pl.DataFrame(path_rows).write_parquet(parquet_path)
                    except Exception as e:
                        print(f"[warn] parquet write failed ({e}); continuing (CSV is authoritative)", flush=True)

                done_set.add(key)
                state['done_pairs'] = [list(k) for k in sorted(done_set)]
                state['job'] = args.job
                state['profile'] = args.profile
                state['windows'] = window_labels
                state['gross'] = args.gross
                state['smoke'] = bool(args.smoke)
                state['n_iter'] = n_iter
                state['algorithm_version'] = version_meta
                mc_patch.atomic_write_json(state_path, state)

                print(f"[{i_pair}/{total_pairs}] job={args.job} window={label} cell={cell.cell_name} "
                      f"base={'*' if cell.is_baseline else ' '} n={n_iter} ncalls={n_calls} "
                      f"prepare={row_prepare_s:.1f}s sim={t_sim:.1f}s | "
                      f"worst_dd={result.get('worst_dd'):.1f}% med_ret={result.get('med_ret'):+.1f}% "
                      f"p_coll={result.get('p_coll'):.1f}% | avg_prem%={result.get('avg_open_premium_base_pct')} "
                      f"max_prem%={result.get('max_open_premium_base_pct')} put_trades={result.get('put_trades')}",
                      flush=True)
        finally:
            if pool is not None:
                pool.close()
                pool.join()

    csv_f.close()
    elapsed_job = time.time() - t_job0
    _tee(f"\n[DONE] job={args.job} pairs_run_this_invocation={pairs_run_now} "
         f"total_done={len(done_set)}/{total_pairs} wall={elapsed_job:.1f}s -> {csv_path}", log_f)
    log_f.close()


if __name__ == '__main__':
    main()
