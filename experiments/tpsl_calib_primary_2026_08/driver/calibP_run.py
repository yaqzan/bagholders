#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase P (Primary) job runner -- calibrated-PRIMARY Core TP/SL boundary
verification (PREREG.md, locked commit 7e788f45). THIN VARIANT of
experiments/tpsl_refine_2026_08/driver/phaseD_run.py (same core mechanism:
loader-cache, put-skip, per-cell set_tpsl + fresh-prepare + fresh-pool-per-
cell, CSV append + atomic per-job state, resumable, progress lines) with TWO
differences:
  1. a caller-supplied --cells grid whose DEFAULT is this campaign's OWN
     locked 16-cell grid (TP in {0.05,0.075,0.10,0.15} x SL in
     {-0.80,-0.90,-0.95,-1.00}) -- NOT phaseD_run.py's baseline-prepend
     grid. (0.10,-1.00) and (0.10,-0.90) are this campaign's identity-check
     arms and are already members of the cross product, so unlike
     phaseD_run.py's parse_cells_arg this file never auto-injects an extra
     baseline cell (doing so would silently add the OLD tpsl_refine baseline
     (0.30,-0.70), which is off-spec for this grid);
  2. a first-class --mode switch (calib|buf20|survivor|canon) that sets the
     fill-realism env knobs BEFORE `import monte_carlo`, mirroring exactly
     how experiments/alloc_retune_2026_08/driver/allocC_run.py's --calibrated/
     --survivor-only modes set TP_FILL_MISS_P/TP_FILL_GAP_AWARE/
     MC_UNIVERSE_FILE pre-import (env-before-import is required: these are
     plain os.environ-derived module globals read ONCE per spawned MP
     worker's own fresh top-level import -- see mc_patch.py's module
     docstring and phaseD_run.py's own recon comment on TP_FILL_MISS_P).

    python experiments/tpsl_calib_primary_2026_08/driver/calibP_run.py \
        --job NAME [--cells "tp,sl;tp,sl;..."] [--mode calib|buf20|survivor|canon] \
        [--windows LBL[,LBL...]] [--n-iter N] [--smoke-n N]

--cells format is 'tp,sl;tp,sl;...' (comma inside a cell, semicolon between
cells -- SAME separator convention as phaseD_run.py's --cells, reused
deliberately since this campaign continues that one). Omit --cells entirely
for the full locked 16-cell grid (DEFAULT_CELLS below) -- a bare
`--job X` invocation is a complete, PREREG-correct run.

--mode (default 'calib'):
  calib    : TP_FILL_MISS_P=0.15, TP_FILL_GAP_AWARE=1  (PREREG section 3 primary read)
  buf20    : TP_FILL_MISS_P=0.20, TP_FILL_GAP_AWARE=1  (PREREG section 4.5 buf20 guard)
  canon    : no fill knobs at all -- default (non-calibrated) fill model
             (PREREG section 4.3 canon re-read guard)
  survivor : same knobs as calib, PLUS MC_UNIVERSE_FILE=survivor_universe_811.txt
             (PREREG section 4.4 survivor arm guard) -- cribs phaseD_run.py's
             --survivor-only universe-filter engagement proof verbatim (never
             trust the '[universe-filter]' stdout print alone -- the loader
             cache silently suppresses it on a cache-hit prepare).

--windows default is mode-dependent: 'canon' defaults to the full 12-window
canonical set (PHASE_D_WINDOWS_12, imported read-only from phaseD_run.py --
never hand-retyped); every other mode defaults to ['22-now','5y'] (PREREG
section 3's locked grid windows). An explicit --windows always overrides the
default for any mode; only window labels appears in monte_carlo.py's own
WINDOWS list are ever accepted (never invent/rename a label -- that would
silently break the paired-seed rule this whole identity-check strategy
depends on).

Paired seeds: seeds depend only on the window LABEL STRING + iteration index
(monte_carlo._simulate_window docstring), never on cell params -- so reusing
mc.WINDOWS's exact label strings (which this file does, via the same
window_lookup pattern phaseD_run.py/allocC_run.py use) is sufficient and
necessary for this campaign's identity-check tolerance (~0.5pp) against
experiments/tpsl_refine_2026_08/out/phaseD_core_calib.csv's (0.10,-0.90) and
(0.10,-1.00) rows. No special seed-pairing code is needed beyond that reuse.

ONE FRESH PREPARE + POOL PER (WINDOW, CELL): PREREG section 3 "ONE SUBPROCESS
PER CELL -- never share a process/pool across TP/SL cells (barriers bake at
`_prepare_window`; a shared pool silently reuses stale barriers)". Concretely
this means: mc_patch.set_tpsl(mc, tp, sl) THEN a fresh mc._prepare_window(...)
THEN mc._simulate_window(ctx) called WITHOUT an external pool= kwarg, for
EVERY (window, cell) pair -- exactly phaseD_run.py's own loop shape (unlike
allocC_run.py's Core sizing cells, which safely share one prepare/pool across
multiple cells THAT DON'T TOUCH TP/SL within a window, because sizing does
not affect the barrier walk). mc._simulate_window with no pool= kwarg builds
its own multiprocessing.Pool and tears it down at the end of that same call
(verified monte_carlo.py:4604 `_own_pool = pool is None`) -- so reuse-across-
cells is structurally impossible here; no manual pool bookkeeping needed.

Locked spec: experiments/tpsl_calib_primary_2026_08/PREREG.md (commit
7e788f45; DO NOT modify that file). Engine contract:
.claude/skills/run-monte-carlo/SKILL.md.

HARD RULE: this file NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file, and NEVER edits anything under
experiments/tpsl_refine_2026_08/ or experiments/alloc_retune_2026_08/ (their
mc_patch.py / phaseD_run.py are IMPORTED READ-ONLY, never copied). All
variant behavior is in-process patching of the imported `mc` module object or
os.environ set BEFORE `import monte_carlo`. This script never git-commits
anything.

VERSION PIN: reuses (read-only) the SAME driver/state/meta.json the
tpsl_refine_2026_08 campaign resolved and pinned (algorithm_version id=74,
git_commit=f9fb7b934, resolved 2026-08-10) -- required for the identity
check to be meaningful (a version drift would change signals/outcomes
wholesale, not just fill knobs). This file NEVER writes that meta.json; it
hard-stops if the file is missing rather than silently re-resolving (which
would write into tpsl_refine_2026_08/driver/state/, violating the no-edit
rule above).

OUTPUT: single csv/log/state file SET per --job, regardless of --mode (NOT
mode-suffixed the way phaseD_run.py/allocC_run.py branch their output
filenames by mode) -- out/calibP_<job>.csv, logs/<job>.log,
driver/state/calibP_<job>.json. The 'mode' column travels with every row so
concatenating modes under the same job name (not done by this campaign's own
SMOKE/core16 jobs, both --mode calib) stays self-describing. Rows append as
each cell completes: a crash loses at most one in-flight cell.

Windows/multiprocessing note (same as phaseD_run.py/allocC_run.py):
monte_carlo._simulate_window builds its own multiprocessing.Pool per call --
on Windows (spawn) every worker re-imports this script as a non-`__main__`
module. Everything with side effects (arg parsing, DB access, the cell loop)
MUST stay inside `main()`, guarded by `if __name__ == '__main__':` at the
bottom -- never at module level. Module-level imports here (including `from
phaseD_run import ...`) are side-effect-free, verified against that module's
own docstring/structure, so they are safe at module level (same choice
allocC_run.py already made for its own `from phaseD_run import ...`).

Console output is ASCII-only throughout (no em-dash/smart-quote/unicode
arrows) per this repo's convention.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from contextlib import redirect_stdout

# --- repo-root + cross-experiment bootstrap -- explicit + asserted, never
# inferred from CWD (traps.md "Worktree PYTHONPATH trap"). Safe to re-run
# (idempotent) inside spawned MP workers. -----------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../tpsl_calib_primary_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                                        # .../tpsl_calib_primary_2026_08
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
    f"tpsl driver mc_patch.py not found at {_TPSL_DRIVER_DIR!r} -- this campaign "
    f"requires reusing it in-place (READ-ONLY); it must never be copied/duplicated"
)
assert os.path.isfile(os.path.join(_TPSL_DRIVER_DIR, 'phaseD_run.py')), (
    f"tpsl driver phaseD_run.py not found at {_TPSL_DRIVER_DIR!r} -- this file "
    f"PORTS its LOCKED 12-window canonical set + survivor-file path/loader by "
    f"importing them read-only from there, never copying"
)

# phaseD_run.py -- READ-ONLY reuse (never edit that file): the LOCKED 12-window
# canonical set (for --mode canon's window default), the survivor-file path +
# loader (for --mode survivor), and two tiny helpers (_load_json/_tee) that
# allocC_run.py already established the precedent of importing this way from a
# sibling driver module. Importing phaseD_run triggers zero side effects at
# import time (its own DB/mc-touching code lives inside main(), guarded by
# `if __name__ == '__main__':`).
from phaseD_run import (                                                     # noqa: E402
    PHASE_D_WINDOWS_12, SURVIVOR_FILE, _load_survivor_set, _load_json, _tee,
)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TPSL_META_PATH = os.path.join(_TPSL_DRIVER_DIR, 'state', 'meta.json')   # REUSED, never written by this file

# ---------------------------------------------------------------------------
# LOCKED grid -- PREREG.md section 3 "Core grid: TP in {0.05, 0.075, 0.10,
# 0.15} x SL in {-0.80, -0.90, -0.95, -1.00} = 16 cells". TP outer / SL inner,
# both in the exact ascending order PREREG lists them. (0.10,-0.90) and
# (0.10,-1.00) are the identity-check arms and fall out of the cross product
# naturally (index 9 and 11 respectively of the 16) -- not special-cased.
# ---------------------------------------------------------------------------
_TP_GRID = (0.05, 0.075, 0.10, 0.15)
_SL_GRID = (-0.80, -0.90, -0.95, -1.00)
DEFAULT_CELLS = [(tp, sl) for tp in _TP_GRID for sl in _SL_GRID]
IDENTITY_CHECK_CELLS = [(0.10, -0.90), (0.10, -1.00)]   # PREREG section 3 -- must reproduce phaseD_core_calib.csv

DEFAULT_WINDOWS = ['22-now', '5y']          # PREREG section 3 -- calib/buf20/survivor default
CANON_DEFAULT_WINDOWS = list(PHASE_D_WINDOWS_12)   # PREREG section 4.3 -- canon re-read default (full 12)

MODES = ('calib', 'buf20', 'survivor', 'canon')

N_ITER_DEFAULT = 500   # PREREG section 3

# Schema matches experiments/tpsl_refine_2026_08/out/phaseD_core_calib.csv
# EXACTLY (same column names, same order) per this campaign's Build spec
# ("schema matching phaseD_core_calib.csv; profile column = core"). 'phase'
# uses 'P' (this campaign's own single-phase tag, paralleling phaseD_run.py's
# 'D') so a future concatenation of the two campaigns' CSVs stays
# self-describing via the 'phase' column rather than relying on filename.
CSV_FIELDS = [
    'phase', 'mode', 'profile', 'window', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s',
    'realized_call_tp_pct', 'n_calls_delisted',
]


def _paths_for_job(job):
    """(csv_path, parquet_path, state_path, log_path) for this job. UNLIKE
    phaseD_run.py/allocC_run.py, NOT mode-branched (Build spec: single
    'calibP_<job>.csv' / '<job>.log' regardless of --mode) -- the 'mode'
    column disambiguates rows if a job name is ever reused across modes."""
    return (
        os.path.join(OUT_DIR, f'calibP_{job}.csv'),
        os.path.join(OUT_DIR, f'calibP_paths_{job}.parquet'),
        os.path.join(STATE_DIR, f'calibP_{job}.json'),
        os.path.join(LOG_DIR, f'{job}.log'),
    )


def parse_cells_arg(spec):
    """Parse '--cells tp,sl;tp,sl;...'. Returns a list of (tp, sl) float
    tuples rounded to 3dp (this grid needs it -- 0.075), de-duplicated
    (first occurrence wins, order preserved). spec=None/empty returns the
    full DEFAULT_CELLS grid -- UNLIKE phaseD_run.py's parse_cells_arg, this
    NEVER auto-prepends a baseline cell (see module docstring point 1)."""
    if not spec:
        return list(DEFAULT_CELLS)
    cells = []
    for tok in spec.split(';'):
        tok = tok.strip()
        if not tok:
            continue
        parts = tok.split(',')
        if len(parts) != 2:
            raise SystemExit(f"--cells token {tok!r} is not 'tp,sl' (e.g. '0.10,-0.90') -- "
                             f"comma inside a cell, semicolon between cells")
        try:
            tp, sl = round(float(parts[0]), 3), round(float(parts[1]), 3)
        except ValueError:
            raise SystemExit(f"--cells token {tok!r} has a non-numeric tp or sl")
        cells.append((tp, sl))
    seen = set()
    out = []
    for c in cells:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--job', required=True, help='job name -- output files are out/calibP_<job>.csv etc')
    p.add_argument('--cells', default=None,
                    help="'tp,sl;tp,sl;...' cell list (e.g. '0.05,-1.0;0.15,-1.0'). "
                         "Omit for the full locked 16-cell grid (PREREG section 3).")
    p.add_argument('--mode', default='calib', choices=list(MODES),
                    help="calib (TP_FILL_MISS_P=0.15+GAP_AWARE, default) | buf20 (0.20+GAP_AWARE) | "
                         "survivor (calib knobs + MC_UNIVERSE_FILE=survivor_universe_811.txt) | "
                         "canon (no fill knobs)")
    p.add_argument('--windows', default=None,
                    help="comma-separated window labels. Default: ['22-now','5y'] for calib/buf20/"
                         "survivor, the full 12-window canonical set for canon. Never invent/rename "
                         "labels (paired-seed rule) -- must be a subset of monte_carlo.py's WINDOWS.")
    p.add_argument('--n-iter', type=int, default=N_ITER_DEFAULT,
                    help=f'MC iterations per cell (default {N_ITER_DEFAULT})')
    p.add_argument('--smoke-n', type=int, default=None,
                    help='override --n-iter for a cheap smoke run (e.g. 25)')
    return p.parse_args()


def _apply_mode_env(mode):
    """Set the fill-realism env knobs BEFORE `import monte_carlo` (module
    docstring point 2). Mutates os.environ in place; returns nothing."""
    if mode == 'calib':
        os.environ['TP_FILL_MISS_P'] = '0.15'
        os.environ['TP_FILL_GAP_AWARE'] = '1'
    elif mode == 'buf20':
        os.environ['TP_FILL_MISS_P'] = '0.20'
        os.environ['TP_FILL_GAP_AWARE'] = '1'
    elif mode == 'canon':
        pass   # no fill knobs -- default (non-calibrated) fill model
    elif mode == 'survivor':
        os.environ['TP_FILL_MISS_P'] = '0.15'
        os.environ['TP_FILL_GAP_AWARE'] = '1'
        if not os.path.isfile(SURVIVOR_FILE):
            raise SystemExit(f"--mode survivor: {SURVIVOR_FILE!r} does not exist -- STOP, "
                             f"do not improvise a substitute universe file")
        os.environ['MC_UNIVERSE_FILE'] = SURVIVOR_FILE
    else:
        raise ValueError(f"unknown mode {mode!r}")   # unreachable -- argparse choices already gate this


def main():
    args = parse_args()
    mode = args.mode

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {TPSL_META_PATH} missing. This campaign pins ALGORITHM_VERSION to the SAME "
            f"id the tpsl_refine_2026_08 campaign resolved (required for the identity check to be "
            f"meaningful). Refusing to auto-re-resolve (that would WRITE into "
            f"tpsl_refine_2026_08/driver/state/, violating the no-edit rule). Report to "
            f"orchestrator rather than improvising.")

    cells = parse_cells_arg(args.cells)
    n_iter = args.smoke_n if args.smoke_n else args.n_iter

    if args.windows:
        window_labels = [w.strip() for w in args.windows.split(',') if w.strip()]
    else:
        window_labels = list(CANON_DEFAULT_WINDOWS) if mode == 'canon' else list(DEFAULT_WINDOWS)

    # Diagnostic survivor set -- loaded regardless of mode (n_calls_delisted
    # is populated in every mode, phaseD_run.py's own convention). Loaded
    # BEFORE any env/import step so a missing file fails fast, before any
    # compute is spent.
    survivor_set = _load_survivor_set(SURVIVOR_FILE)

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    # 1) env BEFORE import -- frozen pins, profile (core is a no-op, kept for
    # structural parity with phaseD_run.py/allocC_run.py), mode-specific
    # fill knobs, version pin. TP/SL is NOT set here -- it is swept per-cell
    # via mc_patch.set_tpsl() immediately before each cell's _prepare_window
    # (see module docstring "ONE FRESH PREPARE + POOL PER..." section).
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env('core')
    _apply_mode_env(mode)
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)   # reuses tpsl_refine's pin, read-only

    # 2) NOW import monte_carlo -- every env var above is already set.
    import monte_carlo as mc

    # 3) post-import patches (identical to phaseD_run.py/allocC_run.py).
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    # 4) hard verification -- fail fast rather than silently sweeping a wrong
    # config. TP_BASE/SL_BASE are NOT asserted here (this campaign SWEEPS
    # them per-cell, unlike allocC_run.py's frozen-TP/SL Confirm phase).
    assert abs(mc.GROSS_PREMIUM_CAP - 0.50) < 1e-9 and abs(mc.CALL_PREMIUM_CAP - 0.50) < 1e-9, \
        f"Core GROSS/CALL_PREMIUM_CAP != the shipped 0.50 default (got {mc.GROSS_PREMIUM_CAP}/{mc.CALL_PREMIUM_CAP})"
    if mode == 'calib':
        assert abs(getattr(mc, 'TP_FILL_MISS_P', 0.0) - 0.15) < 1e-9, \
            f"TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != 0.15 -- calib env propagation FAILED"
        assert getattr(mc, 'TP_FILL_GAP_AWARE', False) is True, \
            f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != True -- calib env propagation FAILED"
    elif mode == 'buf20':
        assert abs(getattr(mc, 'TP_FILL_MISS_P', 0.0) - 0.20) < 1e-9, \
            f"TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != 0.20 -- buf20 env propagation FAILED"
        assert getattr(mc, 'TP_FILL_GAP_AWARE', False) is True, \
            f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != True -- buf20 env propagation FAILED"
    elif mode == 'canon':
        assert abs(getattr(mc, 'TP_FILL_MISS_P', 0.0) - 0.0) < 1e-9, \
            f"TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != 0.0 -- canon must have no fill knobs"
        assert getattr(mc, 'TP_FILL_GAP_AWARE', False) is False, \
            f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != False -- canon must have no fill knobs"
    elif mode == 'survivor':
        assert abs(getattr(mc, 'TP_FILL_MISS_P', 0.0) - 0.15) < 1e-9, \
            f"TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != 0.15 -- survivor env propagation FAILED"
        assert getattr(mc, 'TP_FILL_GAP_AWARE', False) is True, \
            f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != True -- survivor env propagation FAILED"
        assert getattr(mc, 'MC_UNIVERSE_FILE', None) == SURVIVOR_FILE, \
            f"MC_UNIVERSE_FILE={getattr(mc, 'MC_UNIVERSE_FILE', None)!r} != {SURVIVOR_FILE!r} -- env propagation FAILED"

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(f"window label(s) {missing!r} not in mc.WINDOWS {sorted(window_lookup)} "
                         f"-- never invent/rename labels (paired-seed rule)")

    mc.N_ITER = n_iter

    csv_path, parquet_path, state_path, log_path = _paths_for_job(args.job)

    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}   # (mode, window, tp, sl)

    csv_is_new = not os.path.exists(csv_path)
    csv_f = open(csv_path, 'a', newline='', encoding='utf-8')
    csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if csv_is_new:
        csv_w.writeheader()
        csv_f.flush()

    # Per-iteration parquet accumulator (optional -- CSV is authoritative;
    # med_ret/p10_ret/p90_ret are computed in-process regardless of polars).
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
    _tee(f"JOB {args.job}  mode={mode}  profile=core  windows={window_labels}  n_iter={n_iter}  "
         f"cells={cells}", log_f)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} "
         f"git_commit={version_meta['git_commit']} (pinned meta={TPSL_META_PATH})", log_f)
    _tee(f"[CONFIG] MAX_POSITIONS={mc.MAX_POSITIONS} MAX_POSITIONS_CALL={mc.MAX_POSITIONS_CALL} "
         f"MAX_POSITIONS_PUT={mc.MAX_POSITIONS_PUT}", log_f)
    _tee(f"[CONFIG] TIER_ALLOC={mc.TIER_ALLOC}  PUT_TIER_ALLOC={mc.PUT_TIER_ALLOC}", log_f)
    _tee(f"[CONFIG] GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP} "
         f"PRACTICAL_EXPOSURE_ENABLED={mc.PRACTICAL_EXPOSURE_ENABLED}", log_f)
    _tee(f"[CONFIG] CALENDAR_HOLD={mc.CALENDAR_HOLD} NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE} "
         f"HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS}", log_f)
    _tee(f"[CONFIG] MC_WORKERS={os.environ.get('MC_WORKERS')} MC_NO_DB_PERSIST={os.environ.get('MC_NO_DB_PERSIST')} "
         f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR}", log_f)
    # Required evidence line (Build spec: "echo the effective env (MISS_P,
    # GAP_AWARE) into the log header -- self-logged env is required
    # evidence"). Kept as its own line with a stable [ENV] tag so it is
    # trivially greppable regardless of what else is in the header.
    _tee(f"[ENV] TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)}  "
         f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)}  "
         f"MC_UNIVERSE_FILE={getattr(mc, 'MC_UNIVERSE_FILE', None)!r}", log_f)
    _tee(f"[GRID] identity-check cells (must reproduce phaseD_core_calib.csv): {IDENTITY_CHECK_CELLS}", log_f)
    _tee(f"{'='*100}", log_f)

    total_cells = len(window_labels) * len(cells)
    i_cell = 0
    cells_run_now = 0
    t_job0 = time.time()
    delisted_by_window = {}   # label -> n_calls_delisted, computed once per window (mode-invariant within one run)

    for label in window_labels:
        d_start, d_end = window_lookup[label]
        for tp, sl in cells:
            i_cell += 1
            key = (mode, label, tp, sl)
            if key in done_set:
                print(f"[{i_cell}/{total_cells}] SKIP (already done) job={args.job} mode={mode} "
                      f"window={label} tp={tp} sl={sl}", flush=True)
                continue
            cells_run_now += 1

            mc_patch.set_tpsl(mc, tp, sl)   # flat: stress=base -- this campaign is a flat verification only

            t0 = time.perf_counter()
            # Capture prepare's stdout into an in-memory buffer (not just the
            # log file) so --mode survivor can grep it programmatically for
            # the '[universe-filter]' engagement line BEFORE trusting this
            # cell's results (cribbed verbatim from phaseD_run.py).
            buf = io.StringIO()
            with redirect_stdout(buf):
                ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
            t_prepare = time.perf_counter() - t0
            captured = buf.getvalue()
            log_f.write(captured)
            log_f.flush()

            if mode == 'survivor' and '[universe-filter]' not in captured:
                # Property check (ported verbatim from phaseD_run.py): the
                # print can be suppressed by the loader-memoization cache on
                # a cache-hit prepare; prove engagement directly on the
                # loaded data instead of trusting stdout scraping alone.
                _syms = {k[0] for k in ctx['call_outcomes'].keys()}
                _n_out = sum(1 for s in _syms if str(s).upper() not in survivor_set)
                if _n_out > 0:
                    csv_f.close()
                    log_f.close()
                    raise SystemExit(
                        f"FATAL: --mode survivor set MC_UNIVERSE_FILE={SURVIVOR_FILE!r} but the "
                        f"prepare for job={args.job} window={label} tp={tp} sl={sl} shows NO "
                        f"'[universe-filter]' engagement line AND {_n_out} loaded symbols are "
                        f"OUTSIDE the survivor file -- the filter did not fire. STOP (the survivor "
                        f"arm must be PROVABLY engaged, never silently trusted). Captured prepare "
                        f"output: {log_path}; do not improvise.")
                _tee(f"[universe-check] window={label} tp={tp} sl={sl}: engagement proven via "
                     f"direct property on a cache-hit prepare ({len(_syms)} call-signal symbols, "
                     f"0 outside survivor file; engine print suppressed by loader cache)", log_f)

            if label not in delisted_by_window:
                call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
                n_delisted = sum(1 for s in call_syms if str(s).upper() not in survivor_set)
                delisted_by_window[label] = n_delisted
                _tee(f"[universe-check] window={label} mode={mode}: "
                     f"{len(call_syms)} distinct call-signal symbols, "
                     f"{n_delisted} NOT in survivor_universe_811.txt "
                     f"({'expected ~0 in survivor mode' if mode == 'survivor' else 'expected >0 -- proves honest universe includes delisted names'})",
                     log_f)
            n_calls_delisted = delisted_by_window[label]

            n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

            t1 = time.perf_counter()
            with redirect_stdout(log_f):
                sim = mc._simulate_window(ctx)   # no external pool -> fresh pool, own teardown (never shared across cells)
            t_sim = time.perf_counter() - t1
            result = sim['seeded']

            finals = result.get('finals')
            p10_ret = p90_ret = None
            if finals:
                rets_pct = sorted((f / mc.STARTING_CASH - 1.0) * 100.0 for f in finals)
                p10_ret = mc_patch.pct(rets_pct, 0.10)
                p90_ret = mc_patch.pct(rets_pct, 0.90)
                for i_iter, r in enumerate(rets_pct):
                    path_rows.append({'mode': mode, 'profile': 'core', 'window': label,
                                      'tp': tp, 'sl': sl, 'iter': i_iter, 'ret': r})

            row = {
                'phase': 'P', 'mode': mode, 'profile': 'core', 'window': label,
                'tp': tp, 'sl': sl, 'n_iter': n_iter, 'n_call_signals': n_calls,
                'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
                'p10_ret': p10_ret, 'p90_ret': p90_ret,
                'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
                'p_coll': result.get('p_coll'),
                'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate,
                'both_rate': both_rate,
                'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
                'realized_call_tp_pct': result.get('call_tp'),
                'n_calls_delisted': n_calls_delisted,
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
            state['mode'] = mode
            state['windows'] = window_labels
            state['cells'] = [list(c) for c in cells]
            state['n_iter'] = n_iter
            state['algorithm_version'] = version_meta
            mc_patch.atomic_write_json(state_path, state)

            net_sl = mc.NET_SL_BASE
            print(f"[{i_cell}/{total_cells}] job={args.job} mode={mode} window={label} "
                  f"tp={tp:+.3f} sl={sl:+.2f} net_sl={net_sl:+.3f} n={n_iter} ncalls={n_calls} "
                  f"n_delisted={n_calls_delisted} prepare={t_prepare:.1f}s sim={t_sim:.1f}s | "
                  f"tp_rate={tp_rate:.1f}% sl_rate={sl_rate:.1f}% hard_rate={hard_rate:.1f}% | "
                  f"worst_dd={result.get('worst_dd'):.1f}% med_ret={result.get('med_ret'):+.1f}% "
                  f"p_coll={result.get('p_coll'):.1f}%", flush=True)

    csv_f.close()
    elapsed_job = time.time() - t_job0
    _tee(f"\n[DONE] job={args.job} mode={mode} cells_run_this_invocation={cells_run_now} "
         f"total_done={len(done_set)}/{total_cells} wall={elapsed_job:.1f}s "
         f"-> {csv_path}", log_f)
    log_f.close()


if __name__ == '__main__':
    main()
