#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase A job runner -- allocation re-sweep on the TP10 substrate (PREREG.md
section 1 grids, section 2 execution notes).

    python experiments/alloc_retune_2026_08/driver/allocA_run.py \
        --job NAME --profile core|apex --windows LBL[,LBL...] \
        [--gross F]   # REQUIRED for --profile core, FORBIDDEN for --profile apex
        [--smoke]

Locked spec: experiments/alloc_retune_2026_08/PREREG.md + TASK.md, inheriting
experiments/tpsl_refine_2026_08/{PREREG,LESSONS}.md sections 3/7 verbatim
unless overridden. Engine contract: .claude/skills/run-monte-carlo/SKILL.md.

HARD RULE: this file NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file, and NEVER edits anything under
experiments/tpsl_refine_2026_08/ (its mc_patch.py is IMPORTED, not copied).
All variant behavior is in-process patching of the imported `mc` module
object. This script never git-commits anything.

KEY DIFFERENCE vs the tpsl campaign: allocation cells do NOT change barriers
(TP/SL are FROZEN at the shipped values -- this campaign never calls
mc_patch.set_tpsl()). Because barrier precompute (_prepare_window) is proven
independent of TIER_ALLOC/MAX_POSITIONS/GROSS_PREMIUM_CAP/CALL_PREMIUM_CAP
(recon 2026-08-10: the only prepare-time reference to TIER_ALLOC is the
EARN_BOOST_CALL=1 print at monte_carlo.py:4300, and EARN_BOOST_CALL defaults
OFF and is never enabled here), all shape x MaxPos cells of a (window, gross)
pair share ONE _prepare_window ctx + ONE MP pool (the concentration_2x
pattern: prepare once per window, `_simulate_window(ctx, cell_params=...)`
many times).

GROSS/CALL PREMIUM CAP MECHANISM (recon 2026-08-10, load-bearing -- read
before touching the --gross plumbing below):
  monte_carlo.py:1198-1200 --
    GROSS_PREMIUM_CAP = float(os.environ.get('GROSS_PREMIUM_CAP', _practical_default(...)))
    CALL_PREMIUM_CAP  = float(os.environ.get('CALL_PREMIUM_CAP',  _practical_default(...)))
  These are plain import-time module globals, consumed as LIVE free-variable
  reads inside run_single_sim (monte_carlo.py:3319-3321, nested function
  _premium_cap_remaining) -- run_single_sim is the PER-ITERATION function,
  which under MP executes INSIDE spawned workers via _mc_iter_worker.
  UNLIKE TP/SL (baked once into ctx['call_outcomes'] during _prepare_window,
  then pickled into workers via _make_window_pool's initargs -- so workers
  never need to re-read TP_SIGMA_BASE themselves), GROSS_PREMIUM_CAP/
  CALL_PREMIUM_CAP are NOT baked into ctx and are NOT in the set of keys
  _apply_cell_params/_mc_init_worker/_broadcast_cell_params support (verified:
  those three only ever touch TIER_ALLOC, PUT_TIER_ALLOC, MAX_POSITIONS,
  MAX_POSITIONS_CALL, MAX_POSITIONS_PUT -- monte_carlo.py:3892-3919,
  3776-3814, 3827-3846). On Windows (spawn), each MP worker does its OWN
  fresh `import monte_carlo`, reading os.environ AT THAT WORKER'S SPAWN TIME
  -- so a shared pool that outlives a change to GROSS_PREMIUM_CAP would feed
  already-alive workers a STALE cap with no broadcast mechanism to fix it
  (extending _apply_cell_params to cover this would be a production-file
  edit, which is forbidden). Resolution per PREREG section 2 ("if import-
  bound -> shard queue jobs by gross value, env fixed at submit"): this
  script pins ONE gross value for its ENTIRE process lifetime via --gross,
  setting os.environ['GROSS_PREMIUM_CAP'/'CALL_PREMIUM_CAP'] BEFORE
  `import monte_carlo` (so the parent's own import bakes the right value, and
  every worker spawned later in THIS process inherits the same env at spawn
  time and bakes the identical value on its own fresh import). No cell_params
  broadcast is needed or attempted for gross -- shape/MaxPos cells within one
  gross-job still share prepare+pool freely (they ARE covered by
  _apply_cell_params). Apex never sweeps gross (fixed 1.0 via
  mc_patch.APEX_ENV_DIFF, itself set before import) so --gross is forbidden
  for --profile apex.

APEX TP/SL PIN (recon 2026-08-10, load-bearing): mc_patch.apply_profile_env
('apex') sets APEX_ENV_DIFF, which deliberately excludes TP_BASE_OV/SL_BASE_OV
(tpsl campaign called set_tpsl() per-cell for every profile including Apex,
so the profile env never needed to carry TP/SL). This campaign NEVER calls
set_tpsl() (TP/SL frozen). Left alone, Apex would silently inherit whatever
monte_carlo.py's bare module default is -- which is strategy_config.py's
OPT_30DTE (TP_BASE=0.10, SL_BASE=-1.00), i.e. CORE's shipped pair, NOT Apex's.
The actual shipped Apex pin is TP=0.10 / SL=-0.60 (verified live 2026-08-10:
algorithm_versions/portfolio_profiles.json "apex" profile params:
tp_base=0.1, sl_base=-0.6, tp_stress=0.1, sl_stress=-0.6; commit 3f3c3706
"TP/SL joint retune -- Core TP+10/SL-100 scalp-and-dead-hold, Apex TP+10/
SL-60 pinned"). So this script explicitly sets TP_BASE_OV=0.10/TP_STRESS_OV=
0.10/SL_BASE_OV=-0.60/SL_STRESS_OV=-0.60 for profile=apex ONLY, as a FIXED
constant (never varied -- this is a pin, not a sweep; PREREG's "TP/SL frozen"
covers this correctly since it's the Apex baseline value, unmodified).

Windows/multiprocessing note (same as tpsl phaseA_run.py): monte_carlo
_simulate_window/_make_window_pool build multiprocessing.Pool objects -- on
Windows (spawn) that means every worker re-imports this script as a
non-`__main__` module. Everything with side effects (arg parsing, DB access,
the window/cell loop) MUST stay inside `main()`, guarded by
`if __name__ == '__main__':` at the bottom -- never at module level.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass

# --- repo-root + cross-experiment bootstrap. This file lives 3 levels under
# the repo root (experiments/alloc_retune_2026_08/driver/allocA_run.py); the
# reusable tpsl driver helpers (mc_patch.py) live in a SIBLING experiment
# directory, never copied here (PREREG section 2: "New driver files live
# HERE; never edit the tpsl drivers"). Explicit + asserted, never inferred
# from CWD -- see traps.md "Worktree PYTHONPATH trap". Safe to re-run
# (idempotent) inside spawned MP workers -- no side effects beyond sys.path
# and filesystem existence checks. -----------------------------------------
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

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TPSL_META_PATH = os.path.join(_TPSL_DRIVER_DIR, 'state', 'meta.json')

# ---------------------------------------------------------------------------
# Grids -- LOCKED, PREREG.md section 1. Do not edit without re-reading
# PREREG.md; the grid may not change after Phase A starts.
# ---------------------------------------------------------------------------

# (ultra, top, mid, low) -- overflow is ALWAYS 0 (PREREG section 1).
CORE_SHAPES = {
    'S0': (0.20, 0.15, 0.08, 0.03),   # SHIPPED
    'S1': (0.25, 0.15, 0.08, 0.03),
    'S2': (0.20, 0.15, 0.10, 0.05),
    'S3': (0.15, 0.12, 0.08, 0.05),
    'S4': (0.25, 0.20, 0.10, 0.03),
    'S5': (0.12, 0.12, 0.12, 0.12),
    'S6': (0.15, 0.15, 0.15, 0.15),
    'S7': (0.20, 0.20, 0.10, 0.00),
    'S8': (0.30, 0.15, 0.05, 0.00),
    'S9': (0.20, 0.10, 0.05, 0.03),
}
CORE_SHAPE_ORDER = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']
CORE_MAXPOS_GRID = [10, 12, 14, 16, 18, 20]
CORE_GROSS_GRID = [0.40, 0.50, 0.65, 0.80]
CORE_BASELINE = ('S0', 14, 0.50)   # shape, max_pos, gross

APEX_N_GRID = [6, 8, 10, 12, 14]
APEX_FRAC_GRID = [0.08, 0.10, 0.125, 0.15]
APEX_GROSS_RANGE = (0.60, 1.05)     # keep cells with n*frac in this range
APEX_BASELINE = (10, 0.10)          # n, frac (n*frac = 1.0, gross/call caps fixed 1.0)

PHASE_A_WINDOWS = ['2022', '2024', '22-now', '2020_crash']   # PREREG section 1

# SMOKE grid -- BUILD spec section 3, LOCKED for this dispatch. Each smoke
# core cell has a DISTINCT gross, so smoke runs as 3 separate --gross jobs
# (one cell matches per job) -- see cell-selection logic in main().
SMOKE_CORE_CELLS = [('S0', 14, 0.50), ('S5', 20, 0.80), ('S8', 10, 0.40)]
# Apex smoke pair: baseline (n10/frac0.10, gross=1.00) + one edge-of-grid
# corner (n12/frac0.08, gross=0.96) to exercise a MaxPos/frac combination far
# from baseline within the same smoke pass (builder's choice within the
# BUILD spec's "2 cells" count -- the spec fixed the count and window, not
# the specific pair).
SMOKE_APEX_CELLS = [(10, 0.10), (12, 0.08)]

N_ITER_FULL = 150
N_ITER_SMOKE = 20

CSV_FIELDS = [
    'phase', 'profile', 'window', 'cell_name', 'is_baseline',
    'shape_name', 'tier_ultra', 'tier_top', 'tier_mid', 'tier_low',
    'max_pos', 'gross', 'n_names', 'flat_frac',
    'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd', 'p_coll',
    'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'avg_open_premium_base_pct', 'max_open_premium_base_pct',
    'call_trades', 'put_trades',
    'elapsed_prepare_s', 'elapsed_sim_s',
]


# ---------------------------------------------------------------------------
# Cell definitions (pure, side-effect-free -- safe at module level under
# Windows-spawn re-import).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CoreCell:
    shape_name: str
    ultra: float
    top: float
    mid: float
    low: float
    max_pos: int
    gross: float
    is_baseline: bool

    @property
    def cell_name(self) -> str:
        return f"{self.shape_name}_mp{self.max_pos}_g{self.gross:.2f}"

    def cell_params(self) -> dict:
        return {
            'TIER_ALLOC': {'ultra': self.ultra, 'top': self.top, 'mid': self.mid,
                           'low': self.low, 'overflow': 0.0},
            'PUT_TIER_ALLOC': {'put_top': 0.0, 'put_mid': 0.0, 'put_low': 0.0},
            'MAX_POSITIONS': int(self.max_pos),
            'MAX_POSITIONS_CALL': int(self.max_pos),
            'MAX_POSITIONS_PUT': 0,
        }

    def row_fields(self) -> dict:
        return dict(cell_name=self.cell_name, shape_name=self.shape_name,
                    tier_ultra=self.ultra, tier_top=self.top, tier_mid=self.mid, tier_low=self.low,
                    max_pos=self.max_pos, gross=self.gross, n_names='', flat_frac='',
                    is_baseline=int(self.is_baseline))


@dataclass(frozen=True)
class ApexCell:
    n: int
    frac: float
    is_baseline: bool

    @property
    def cell_name(self) -> str:
        return f"n{self.n}_f{self.frac:.3f}"

    def cell_params(self) -> dict:
        a = float(self.frac)
        return {
            'TIER_ALLOC': {'ultra': a, 'top': a, 'mid': a, 'low': a, 'overflow': 0.0},
            'PUT_TIER_ALLOC': {'put_top': 0.0, 'put_mid': 0.0, 'put_low': 0.0},
            'MAX_POSITIONS': int(self.n),
            'MAX_POSITIONS_CALL': int(self.n),
            'MAX_POSITIONS_PUT': 0,
        }

    def row_fields(self) -> dict:
        return dict(cell_name=self.cell_name, shape_name='',
                    tier_ultra=self.frac, tier_top=self.frac, tier_mid=self.frac, tier_low=self.frac,
                    max_pos=self.n, gross=1.0, n_names=self.n, flat_frac=self.frac,
                    is_baseline=int(self.is_baseline))


def build_core_cells(gross: float) -> list:
    cells = []
    for shape_name in CORE_SHAPE_ORDER:
        ultra, top, mid, low = CORE_SHAPES[shape_name]
        for max_pos in CORE_MAXPOS_GRID:
            is_baseline = (shape_name, max_pos, _fround(gross)) == CORE_BASELINE
            cells.append(CoreCell(shape_name, ultra, top, mid, low, max_pos, gross, is_baseline))
    return cells


def build_apex_cells() -> list:
    lo, hi = APEX_GROSS_RANGE
    cells = []
    for n in APEX_N_GRID:
        for frac in APEX_FRAC_GRID:
            g = n * frac
            if not (lo - 1e-9 <= g <= hi + 1e-9):
                continue
            is_baseline = (n, frac) == APEX_BASELINE
            cells.append(ApexCell(n, frac, is_baseline))
    return cells


def _fround(x: float) -> float:
    """Round to the grid's own precision (2dp) so float-literal comparisons
    against CORE_BASELINE/CORE_GROSS_GRID are robust regardless of how the
    caller's --gross string parsed."""
    return round(float(x), 2)


def smoke_core_cells(gross: float) -> list:
    matched = [t for t in SMOKE_CORE_CELLS if abs(t[2] - gross) < 1e-9]
    if not matched:
        raise SystemExit(
            f"--smoke --gross {gross}: no smoke cell defined at this gross value "
            f"(SMOKE_CORE_CELLS gross values: {sorted({t[2] for t in SMOKE_CORE_CELLS})})")
    out = []
    for (shape_name, max_pos, g) in matched:
        ultra, top, mid, low = CORE_SHAPES[shape_name]
        is_baseline = (shape_name, max_pos, _fround(g)) == CORE_BASELINE
        out.append(CoreCell(shape_name, ultra, top, mid, low, max_pos, g, is_baseline))
    return out


def smoke_apex_cells() -> list:
    return [ApexCell(n, frac, (n, frac) == APEX_BASELINE) for (n, frac) in SMOKE_APEX_CELLS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job', required=True, help='job name -- output files are out/allocA_<job>.csv etc')
    p.add_argument('--profile', required=True, choices=['core', 'apex'])
    p.add_argument('--windows', required=True,
                    help='comma-separated ENGINE preset window labels (must exist in mc.WINDOWS) -- '
                         'never invented/renamed (paired-seed rule)')
    p.add_argument('--gross', type=float, default=None,
                    help='REQUIRED for --profile core (one gross value per job -- see module '
                         'docstring "GROSS/CALL PREMIUM CAP MECHANISM"); FORBIDDEN for --profile apex '
                         '(fixed 1.0 via mc_patch.APEX_ENV_DIFF)')
    p.add_argument('--smoke', action='store_true',
                    help='fixed smoke grid at N_ITER=20 instead of the full blast grid at N=150 '
                         '(BUILD spec section 3): core picks the ONE SMOKE_CORE_CELLS entry matching '
                         '--gross; apex runs the fixed 2-cell SMOKE_APEX_CELLS list')
    return p.parse_args()


def _load_json(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def _tee(msg, log_f):
    print(msg, flush=True)
    log_f.write(msg + '\n')
    log_f.flush()


def main():
    args = parse_args()

    if args.profile == 'core':
        if args.gross is None:
            raise SystemExit("--gross is REQUIRED for --profile core (PREREG section 2: gross is "
                              "sharded at the job/process level -- see module docstring)")
        if not args.smoke:
            grid_gross = {_fround(g) for g in CORE_GROSS_GRID}
            if _fround(args.gross) not in grid_gross:
                raise SystemExit(f"--gross {args.gross} not in the locked PREREG grid {CORE_GROSS_GRID}")
    else:
        if args.gross is not None:
            raise SystemExit("--gross is FORBIDDEN for --profile apex (fixed 1.0 via "
                              "mc_patch.APEX_ENV_DIFF -- Apex never sweeps gross per PREREG section 1)")

    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {TPSL_META_PATH} missing. PREREG section 2 pins this campaign's "
            f"ALGORITHM_VERSION to the SAME id the tpsl_refine_2026_08 campaign resolved. "
            f"Refusing to auto-re-resolve (mc_patch.resolve_and_pin_version's fallback path "
            f"would WRITE into the tpsl driver's state/ dir as a live-DB-resolve side effect, "
            f"which this campaign must never do). Report to orchestrator rather than improvising.")

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    # 1) env BEFORE import -- frozen pins, profile overrides, gross pin (core
    # only), Apex TP/SL pin (apex only), version pin. TP/SL for CORE is left
    # UNTOUCHED (no TP_BASE_OV/SL_BASE_OV set) so it resolves to strategy_
    # config's OPT_30DTE default (TP_BASE=0.10, SL_BASE=-1.00) -- the shipped
    # Core pair. This is "DO NOT TOUCH" TP/SL in its purest form: literally no
    # code path here ever assigns those env vars for profile=core.
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(args.profile)
    if args.profile == 'core':
        os.environ['GROSS_PREMIUM_CAP'] = repr(args.gross)
        os.environ['CALL_PREMIUM_CAP'] = repr(args.gross)
    else:
        # Apex TP/SL pin -- see module docstring "APEX TP/SL PIN". Not covered
        # by mc_patch.APEX_ENV_DIFF (that dict deliberately excludes TP/SL --
        # the tpsl campaign swept it per-cell via set_tpsl() instead). This
        # campaign never sweeps TP/SL, so the shipped Apex pin must be set
        # here as a frozen constant.
        os.environ['TP_BASE_OV'] = '0.10'
        os.environ['TP_STRESS_OV'] = '0.10'
        os.environ['SL_BASE_OV'] = '-0.60'
        os.environ['SL_STRESS_OV'] = '-0.60'
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    # 2) NOW import monte_carlo -- every env var above is already set.
    import monte_carlo as mc

    # 3) post-import patches (identical to tpsl phaseA_run.py).
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    # 4) hard verification -- fail fast rather than silently sweeping a wrong
    # config. This IS the G1 gross-cap mechanism proof for the smoke session
    # (belt-and-suspenders alongside the [CONFIG] echo below and the realized
    # avg/max_open_premium_base_pct columns in the CSV).
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
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(
            f"unknown window label(s) {missing!r} -- not in mc.WINDOWS "
            f"{sorted(window_lookup)}; never invent/rename labels (paired-seed rule)")

    if args.profile == 'core':
        cells = smoke_core_cells(args.gross) if args.smoke else build_core_cells(args.gross)
    else:
        cells = smoke_apex_cells() if args.smoke else build_apex_cells()
    n_iter = N_ITER_SMOKE if args.smoke else N_ITER_FULL
    mc.N_ITER = n_iter
    n_workers = int(os.environ.get('MC_WORKERS', '6'))

    csv_path = os.path.join(OUT_DIR, f'allocA_{args.job}.csv')
    parquet_path = os.path.join(OUT_DIR, f'allocA_paths_{args.job}.parquet')
    state_path = os.path.join(STATE_DIR, f'allocA_{args.job}.json')
    log_path = os.path.join(LOG_DIR, f'allocA_{args.job}.log')

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
                    'phase': 'A', 'profile': args.profile, 'window': label,
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
