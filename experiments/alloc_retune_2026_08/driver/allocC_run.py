#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase C (Confirm) job runner -- allocation re-sweep, N=500 x up to 12
canonical windows (PREREG.md section 1 "Confirm" + TASK.md's LOCKED
confirm-phase decision rules). THIN VARIANT of allocB_run.py (same
architecture: loader-cache, put-skip, shared-prepare-per-window + reused
pool for sizing cells via `_apply_cell_params`, gross sharding for Core,
Apex TP/SL pin, resumable per-job state JSON, CSV append, per-iteration
parquet dump) with THREE additions ported from
experiments/tpsl_refine_2026_08/driver/phaseD_run.py:
  1. a LOCKED cell grid (the Phase B finalists, not a formula) at N_ITER=500
     over up to the full 12-window canonical set (imported from phaseD_run.py
     -- single source of truth, never hand-retyped);
  2. FOUR run modes sharing this same cell grid + architecture, each writing
     to a DISTINCT csv/state/log file set: 'default' (all 12 windows),
     'fill_probe' (TP_FILL_MISS_P=0.10, battery (a)), 'calibrated'
     (TP_FILL_MISS_P=0.15 + TP_FILL_GAP_AWARE=1, battery (b)), and
     'survivor_only' (MC_UNIVERSE_FILE=survivor_universe_811.txt, battery
     (c)) -- the last three restricted to the BUILD spec's named windows;
  3. the survivor-only engagement PROPERTY check (never trust the engine's
     '[universe-filter]' stdout print alone -- LESSONS.md's loader-cache
     trap) and the `n_calls_delisted` CSV column, logged in every mode.

    python experiments/alloc_retune_2026_08/driver/allocC_run.py \
        --job NAME --profile core|apex --windows LBL[,LBL...] \
        [--gross F]   # REQUIRED for --profile core, FORBIDDEN for --profile apex
        [--fill-probe | --calibrated | --survivor-only]   # omit for 'default' mode
        [--smoke]

Locked spec: experiments/alloc_retune_2026_08/PREREG.md section 1 "Confirm" +
TASK.md's locked confirm-phase decision rules, inheriting
experiments/tpsl_refine_2026_08/{PREREG,LESSONS}.md sections 3/6/7 verbatim
unless overridden. Engine contract: .claude/skills/run-monte-carlo/SKILL.md.

HARD RULE: this file NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file, and NEVER edits anything under
experiments/tpsl_refine_2026_08/ (its mc_patch.py / phaseD_run.py are
IMPORTED, never copied). All variant behavior is in-process patching of the
imported `mc` module object or os.environ set BEFORE `import monte_carlo`.
This script never git-commits anything.

GROSS/CALL PREMIUM CAP MECHANISM + APEX TP/SL PIN: UNCHANGED from
allocA_run.py/allocB_run.py -- see allocA_run.py's module docstring for the
full recon (import-time module globals read LIVE inside spawned MP workers,
NOT covered by _apply_cell_params/_broadcast_cell_params -> shard Core by
gross, env set before import; Apex TP/SL is a frozen pin, never swept, set
explicitly here because mc_patch.APEX_ENV_DIFF deliberately excludes it).
Confirm's Core finalists span TWO gross values (0.50, 0.65 -- see
CONFIRM_CORE_GROSS_VALUES below), same one-gross-per-process sharding as
Phase A/B. Apex's 4 cells (baseline + 3 finalists) all share gross=1.0 fixed
via mc_patch.APEX_ENV_DIFF, so no sharding is needed for Apex (one job covers
all 4 cells, matching Phase A/B's Apex jobs).

TP/SL is FROZEN for the whole alloc_retune_2026_08 campaign (PREREG section 2:
"TP/SL frozen at shipped values -- cells never touch set_tpsl beyond the
shipped pair"). Confirm's echo-verify therefore asserts the SAME shipped
values Phase A/B asserted: Core resolves to strategy_config's now-shipped
TP+10/SL-100 default (TP_BASE=0.10, SL_BASE=-1.00 -- verified live 2026-08-10,
post the tpsl_refine_2026_08 ship) by NEVER setting TP_BASE_OV/SL_BASE_OV;
Apex is explicitly pinned TP_BASE_OV=0.10/SL_BASE_OV=-0.60 (its own shipped
pin, `algorithm_versions/portfolio_profiles.json` "apex" profile). Neither
value is swept by ANY mode here -- fill_probe/calibrated only touch fill-model
knobs, survivor_only only touches the universe filter.

BATTERY ENV KNOBS (verified against monte_carlo.py source, 2026-08-10):
  - TP_FILL_MISS_P    (monte_carlo.py:417, plain `os.environ.get(...)` at
    import time, consumed inside `resolve()` -- executes inside spawned MP
    workers via each worker's OWN fresh import; setting it in THIS process's
    os.environ before any Pool is created is necessary+sufficient, per
    tpsl_refine_2026_08/LESSONS.md's already-proven recon).
  - TP_FILL_GAP_AWARE (monte_carlo.py:100, `os.environ.get(..., '0') == '1'`,
    same import-time/worker-inherited mechanism).
  - MC_UNIVERSE_FILE  (consumed only inside `_prepare_window` -> load_signals,
    parent process, single-threaded -- no worker-propagation concern; see
    phaseD_run.py's own docstring, ported verbatim).

Windows/multiprocessing note (same as allocA_run.py/allocB_run.py): monte_
carlo._simulate_window/_make_window_pool build multiprocessing.Pool objects
-- on Windows (spawn) every worker re-imports this script as a non-`__main__`
module. Everything with side effects (arg parsing, DB access, the
window/cell loop) MUST stay inside `main()`, guarded by
`if __name__ == '__main__':` at the bottom -- never at module level.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from contextlib import redirect_stdout

# --- repo-root + cross-experiment bootstrap -- IDENTICAL to allocA_run.py's/
# allocB_run.py's (this file lives in the same driver/ dir). Explicit +
# asserted, never inferred from CWD -- see traps.md "Worktree PYTHONPATH
# trap". -----------------------------------------------------------------
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
assert os.path.isfile(os.path.join(_TPSL_DRIVER_DIR, 'phaseD_run.py')), (
    f"tpsl driver phaseD_run.py not found at {_TPSL_DRIVER_DIR!r} -- Confirm is "
    f"required to PORT its survivor-engagement check + window/survivor constants "
    f"by importing them read-only from there, never copying"
)

# allocA_run.py -- reused directly (thin variant, same pattern allocB_run.py
# already established): CoreCell/ApexCell dataclasses, CORE_SHAPES,
# CORE_BASELINE/APEX_BASELINE, _load_json/_tee/_fround. Module-level-only
# import (no monte_carlo side effects at import time).
from allocA_run import (                                                     # noqa: E402
    CoreCell, ApexCell, CORE_SHAPES, CORE_BASELINE, APEX_BASELINE,
    _load_json, _tee, _fround,
)
# phaseD_run.py -- READ-ONLY reuse of its LOCKED, source-verified 12-window
# canonical set + battery window whitelists + survivor-file path/loader (never
# copied/retyped -- PREREG section 2 "New driver files live HERE; never edit
# the tpsl drivers"). Importing this module triggers zero side effects at
# import time (its own DB/mc-touching code lives inside main(), guarded by
# `if __name__ == '__main__':`) -- same safe-import pattern
# test_analyze_allocA.py/test_analyze_allocB.py already rely on for allocA_run.
from phaseD_run import (                                                     # noqa: E402
    PHASE_D_WINDOWS_12, FILL_PROBE_WINDOWS, SURVIVOR_WINDOWS, SURVIVOR_FILE,
    _load_survivor_set,
)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TPSL_META_PATH = os.path.join(_TPSL_DRIVER_DIR, 'state', 'meta.json')

# ---------------------------------------------------------------------------
# LOCKED Confirm cell grid -- PREREG.md section 1 "Confirm (N=500 x 12 win):
# <=4 finalists/profile + baseline". Hand-copied from the dispatch brief,
# itself the mechanical read of out/allocB_summary.md (Phase B DONE + READ,
# 2026-08-10 ~20:15): Core LANE-DD entrants S9/mp20/g0.50 (dd_5y 30.2 vs base
# 40.4) + S9/mp20/g0.65 (31.3 at 0.99x med; crash-family +5.9pp on 2020_crash
# flagged to watch at Phase B -- THE reason Confirm's crash-family <=3pp
# tolerance check exists); Apex Pareto finalists n10f0.06 / n12f0.06 /
# n10f0.08. Baseline is the SHIPPED config, always an arm, never a remembered
# cert number (PREREG "Baseline arms are RE-RUN inside every phase").
# 4th tuple element = is_baseline (mirrors allocA/B's own is_baseline
# threading, but explicit here since Confirm's grid is a fixed list, not
# formula-derived from CORE_BASELINE/APEX_BASELINE comparison).
# ---------------------------------------------------------------------------
CORE_CELLS = [
    ('S0', 14, 0.50, True),    # SHIPPED baseline (in-phase re-run)
    ('S9', 20, 0.50, False),   # Phase B finalist 1 (g0.50)
    ('S9', 20, 0.65, False),   # Phase B finalist 2 (g0.65)
]
APEX_CELLS = [
    (10, 0.10, True),    # SHIPPED baseline (in-phase re-run)
    (10, 0.06, False),   # Phase B Pareto finalist 1
    (12, 0.06, False),   # Phase B Pareto finalist 2
    (10, 0.08, False),   # Phase B Pareto finalist 3
]
assert sum(1 for *_r, b in CORE_CELLS if b) == 1, "exactly one Core baseline cell expected"
assert sum(1 for *_r, b in APEX_CELLS if b) == 1, "exactly one Apex baseline cell expected"
assert (CORE_CELLS[0][0], CORE_CELLS[0][1], _fround(CORE_CELLS[0][2])) == CORE_BASELINE, \
    "Core CELLS[0] must be the same (shape,max_pos,gross) as allocA_run.CORE_BASELINE"
assert (APEX_CELLS[0][0], APEX_CELLS[0][1]) == APEX_BASELINE, \
    "Apex CELLS[0] must be the same (n,frac) as allocA_run.APEX_BASELINE"

CONFIRM_CORE_GROSS_VALUES = sorted({_fround(g) for (_s, _mp, g, _b) in CORE_CELLS})   # [0.50, 0.65]

# Battery (b) "calibrated" shares its window restriction with battery (a)
# "fill-probe" (both are 22-now,5y per the BUILD spec) -- named as its OWN
# constant (not a bare alias of FILL_PROBE_WINDOWS) so the two batteries'
# window sets can never accidentally re-couple if one changes later.
CALIBRATED_WINDOWS = ['22-now', '5y']

MODE_WINDOWS = {
    'default': PHASE_D_WINDOWS_12,
    'fill_probe': FILL_PROBE_WINDOWS,
    'calibrated': CALIBRATED_WINDOWS,
    'survivor_only': SURVIVOR_WINDOWS,
}
MODE_TAG = {
    'default': 'allocC',
    'fill_probe': 'allocC_fillprobe',
    'calibrated': 'allocC_calibrated',
    'survivor_only': 'allocC_survivor',
}

# SMOKE grid -- BUILD spec section 3, LOCKED for this dispatch. Core smoke:
# BOTH cells share gross=0.50 (baseline + finalist 1) so ONE smoke job
# exercises the shared-prepare-across-multiple-cells path too (not just a
# single cell) -- same "share gross so one job covers both" trick
# allocB_run.py's own smoke grid used. Apex smoke defined for architecture
# symmetry/consistency (mirrors allocA/B's per-profile --smoke support) but
# is NOT part of the BUILD spec's 3 required smoke checks -- Apex flat-MC
# uses no new mechanism (no gross sharding, no wider window whitelist than
# what Core's smoke already proves), so a dedicated Confirm smoke for it is
# intentionally not dispatched by the builder.
SMOKE_CORE_CELLS = [('S0', 14, 0.50, True), ('S9', 20, 0.50, False)]
SMOKE_APEX_CELLS = [(10, 0.10, True), (10, 0.06, False)]

N_ITER_FULL = 500     # PREREG section 1 "Confirm (N=500 x 12 win)"
N_ITER_SMOKE = 50      # BUILD spec section "SMOKE"

CSV_FIELDS = [
    'phase', 'mode', 'profile', 'window', 'cell_name', 'is_baseline',
    'shape_name', 'tier_ultra', 'tier_top', 'tier_mid', 'tier_low',
    'max_pos', 'gross', 'n_names', 'flat_frac',
    'n_iter', 'n_call_signals', 'n_calls_delisted',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd', 'p_coll',
    'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'avg_open_premium_base_pct', 'max_open_premium_base_pct',
    'call_trades', 'put_trades', 'realized_call_tp_pct',
    'elapsed_prepare_s', 'elapsed_sim_s',
]
# 'mode': 'default' | 'fill_probe' | 'calibrated' | 'survivor_only' -- each
# writes its OWN csv/state/log file set (_paths_for_mode below), but the
# column travels WITH the row too (phaseD_run.py convention, ported).
# 'n_calls_delisted': count of DISTINCT call-signal symbols in this window's
# baked ctx['call_outcomes'] that are NOT in SURVIVOR_FILE's allow-list --
# computed ONCE per window (identical across every cell of that window, since
# signal LOADING does not depend on sizing) and repeated on every row of that
# window. Populated in ALL FOUR modes (phaseD_run.py's own convention,
# ported): nonzero in 'default'/'fill_probe'/'calibrated' proves the honest
# universe includes delisted names; expected to collapse toward 0 in
# 'survivor_only' mode (the universe filter already excludes them).


def _paths_for_mode(job, mode):
    """(csv_path, parquet_path, state_path, log_path) for this job+mode.
    Ported from phaseD_run.py's _paths_for_mode: each mode gets a DISTINCT
    file set so a --fill-probe/--calibrated/--survivor-only run of job X
    never shares (and can never corrupt) job X's default-mode resumable
    state."""
    tag = MODE_TAG[mode]
    return (
        os.path.join(OUT_DIR, f'{tag}_{job}.csv'),
        os.path.join(OUT_DIR, f'{tag}_paths_{job}.parquet'),
        os.path.join(STATE_DIR, f'{tag}_{job}.json'),
        os.path.join(LOG_DIR, f'{tag}_{job}.log'),
    )


# ---------------------------------------------------------------------------
# Cell-list -> CoreCell/ApexCell instances (pure, side-effect-free).
# ---------------------------------------------------------------------------
def build_core_cells(gross):
    g = _fround(gross)
    cells = []
    for shape_name, max_pos, cg, is_baseline in CORE_CELLS:
        if _fround(cg) != g:
            continue
        ultra, top, mid, low = CORE_SHAPES[shape_name]
        cells.append(CoreCell(shape_name, ultra, top, mid, low, max_pos, g, is_baseline))
    if not cells:
        raise SystemExit(f"--gross {gross} ({g}) matches no Confirm core cell -- "
                          f"valid values: {CONFIRM_CORE_GROSS_VALUES}")
    return cells


def build_apex_cells():
    return [ApexCell(n, frac, is_baseline) for (n, frac, is_baseline) in APEX_CELLS]


def smoke_core_cells(gross):
    g = _fround(gross)
    matched = [t for t in SMOKE_CORE_CELLS if _fround(t[2]) == g]
    if not matched:
        raise SystemExit(
            f"--smoke --gross {gross}: no smoke cell defined at this gross value "
            f"(SMOKE_CORE_CELLS gross values: {sorted({_fround(t[2]) for t in SMOKE_CORE_CELLS})})")
    out = []
    for (shape_name, max_pos, cg, is_baseline) in matched:
        ultra, top, mid, low = CORE_SHAPES[shape_name]
        out.append(CoreCell(shape_name, ultra, top, mid, low, max_pos, cg, is_baseline))
    return out


def smoke_apex_cells():
    return [ApexCell(n, frac, is_baseline) for (n, frac, is_baseline) in SMOKE_APEX_CELLS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--job', required=True, help='job name -- output files are out/allocC[_<mode>]_<job>.csv etc')
    p.add_argument('--profile', required=True, choices=['core', 'apex'])
    p.add_argument('--windows', default=None,
                    help="comma-separated ENGINE preset window labels, must be a subset of this "
                         "mode's allowed set (default mode: the 12 canonical windows; --fill-probe/"
                         "--calibrated: 22-now,5y; --survivor-only: 2022,22-now). Omit for the full "
                         "allowed set. Never invent/rename labels (paired-seed rule).")
    p.add_argument('--gross', type=float, default=None,
                    help='REQUIRED for --profile core (one gross value per job -- see module '
                         'docstring "GROSS/CALL PREMIUM CAP MECHANISM"); FORBIDDEN for --profile apex '
                         f'(fixed 1.0 via mc_patch.APEX_ENV_DIFF). Valid core values: {CONFIRM_CORE_GROSS_VALUES}')
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument('--fill-probe', action='store_true',
                            help='battery (a): TP_FILL_MISS_P=0.10, windows restricted to 22-now,5y')
    mode_group.add_argument('--calibrated', action='store_true',
                            help='battery (b): TP_FILL_MISS_P=0.15 + TP_FILL_GAP_AWARE=1, windows '
                                 'restricted to 22-now,5y')
    mode_group.add_argument('--survivor-only', action='store_true',
                            help='battery (c): MC_UNIVERSE_FILE=survivor_universe_811.txt, windows '
                                 'restricted to 2022,22-now')
    p.add_argument('--smoke', action='store_true',
                    help='fixed smoke grid at N_ITER=50 instead of the full Confirm grid at N=500 '
                         '(BUILD spec "SMOKE"): core picks the SMOKE_CORE_CELLS entries matching '
                         '--gross; apex runs the fixed SMOKE_APEX_CELLS list')
    return p.parse_args()


def main():
    args = parse_args()
    mode = ('fill_probe' if args.fill_probe else 'calibrated' if args.calibrated
            else 'survivor_only' if args.survivor_only else 'default')
    allowed_windows = MODE_WINDOWS[mode]

    if args.profile == 'core':
        if args.gross is None:
            raise SystemExit("--gross is REQUIRED for --profile core (PREREG section 2: gross is "
                              "sharded at the job/process level -- see module docstring)")
        if not args.smoke and _fround(args.gross) not in CONFIRM_CORE_GROSS_VALUES:
            raise SystemExit(f"--gross {args.gross} not in the locked Confirm core grid "
                              f"{CONFIRM_CORE_GROSS_VALUES}")
    else:
        if args.gross is not None:
            raise SystemExit("--gross is FORBIDDEN for --profile apex (fixed 1.0 via "
                              "mc_patch.APEX_ENV_DIFF -- Apex never sweeps gross per PREREG section 1)")

    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {TPSL_META_PATH} missing. PREREG section 2 pins this campaign's "
            f"ALGORITHM_VERSION to the SAME id Phase A resolved (reusing the tpsl campaign's "
            f"meta.json, unchanged from allocA_run.py/allocB_run.py). Refusing to auto-re-resolve. "
            f"Report to orchestrator rather than improvising.")

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    # Diagnostic survivor set -- loaded regardless of mode (cheap; see
    # CSV_FIELDS comment on n_calls_delisted), and BEFORE any env/import step
    # so a missing file fails fast, before any compute is spent.
    survivor_set = _load_survivor_set(SURVIVOR_FILE)

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    # 1) env BEFORE import -- frozen pins, profile overrides, gross pin (core
    # only), Apex TP/SL pin (apex only), mode-specific battery env, version
    # pin. TP/SL for CORE is left UNTOUCHED (no TP_BASE_OV/SL_BASE_OV set) so
    # it resolves to strategy_config's now-shipped default (TP_BASE=0.10,
    # SL_BASE=-1.00). No mode here ever sets TP_BASE_OV/SL_BASE_OV -- TP/SL
    # is frozen for the whole campaign (PREREG section 2).
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env(args.profile)
    if args.profile == 'core':
        os.environ['GROSS_PREMIUM_CAP'] = repr(args.gross)
        os.environ['CALL_PREMIUM_CAP'] = repr(args.gross)
    else:
        # Apex TP/SL pin -- see module docstring. Not covered by
        # mc_patch.APEX_ENV_DIFF (that dict deliberately excludes TP/SL).
        os.environ['TP_BASE_OV'] = '0.10'
        os.environ['TP_STRESS_OV'] = '0.10'
        os.environ['SL_BASE_OV'] = '-0.60'
        os.environ['SL_STRESS_OV'] = '-0.60'

    if mode == 'fill_probe':
        os.environ['TP_FILL_MISS_P'] = '0.10'
    elif mode == 'calibrated':
        os.environ['TP_FILL_MISS_P'] = '0.15'
        os.environ['TP_FILL_GAP_AWARE'] = '1'
    elif mode == 'survivor_only':
        if not os.path.isfile(SURVIVOR_FILE):
            raise SystemExit(f"--survivor-only: {SURVIVOR_FILE!r} does not exist -- STOP, "
                             f"do not improvise a substitute universe file")
        os.environ['MC_UNIVERSE_FILE'] = SURVIVOR_FILE

    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    # 2) NOW import monte_carlo -- every env var above is already set.
    import monte_carlo as mc

    # 3) post-import patches (identical to allocA_run.py/allocB_run.py).
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    # 4) hard verification -- fail fast rather than silently sweeping a wrong
    # config. Core/Apex TP-SL asserts are the SAME allocA/B pin mechanism,
    # echo-verified here per the BUILD spec ("echo must show TP_BASE=0.1
    # SL_BASE=-1.0" for core, "frozen env TP/SL pins 0.10/-0.60" for apex).
    if args.profile == 'core':
        assert abs(mc.GROSS_PREMIUM_CAP - args.gross) < 1e-9, \
            f"GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} != requested --gross {args.gross} -- env-before-import propagation FAILED"
        assert abs(mc.CALL_PREMIUM_CAP - args.gross) < 1e-9, \
            f"CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP} != requested --gross {args.gross} -- env-before-import propagation FAILED"
        assert abs(mc.TP_BASE - 0.10) < 1e-9 and abs(mc.SL_BASE - (-1.00)) < 1e-9, \
            f"Core TP_BASE/SL_BASE drifted from the shipped default (got {mc.TP_BASE}/{mc.SL_BASE}) -- TP/SL must stay frozen"
    else:
        assert abs(mc.GROSS_PREMIUM_CAP - 1.0) < 1e-9 and abs(mc.CALL_PREMIUM_CAP - 1.0) < 1e-9, \
            f"Apex GROSS/CALL_PREMIUM_CAP != 1.0 (got {mc.GROSS_PREMIUM_CAP}/{mc.CALL_PREMIUM_CAP})"
        assert abs(mc.TP_BASE - 0.10) < 1e-9 and abs(mc.SL_BASE - (-0.60)) < 1e-9, \
            f"Apex TP_BASE/SL_BASE != the shipped pin 0.10/-0.60 (got {mc.TP_BASE}/{mc.SL_BASE})"
    if mode == 'fill_probe':
        assert abs(getattr(mc, 'TP_FILL_MISS_P', 0.0) - 0.10) < 1e-9, \
            f"TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != 0.10 -- fill-probe env propagation FAILED"
    elif mode == 'calibrated':
        assert abs(getattr(mc, 'TP_FILL_MISS_P', 0.0) - 0.15) < 1e-9, \
            f"TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != 0.15 -- calibrated env propagation FAILED"
        assert getattr(mc, 'TP_FILL_GAP_AWARE', False) is True, \
            f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != True -- calibrated env propagation FAILED"
    elif mode == 'survivor_only':
        assert getattr(mc, 'MC_UNIVERSE_FILE', None) == SURVIVOR_FILE, \
            f"MC_UNIVERSE_FILE={getattr(mc, 'MC_UNIVERSE_FILE', None)!r} != {SURVIVOR_FILE!r} -- env propagation FAILED"

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if args.windows:
        window_labels = [w.strip() for w in args.windows.split(',') if w.strip()]
        bad = [w for w in window_labels if w not in allowed_windows]
        if bad:
            raise SystemExit(f"window label(s) {bad!r} not allowed for mode={mode!r} -- must be a "
                             f"subset of {allowed_windows}; never invent/rename labels (paired-seed "
                             f"rule), and mode-restricted sets are a PREREG/BUILD-spec lock, not a "
                             f"default you can override")
    else:
        window_labels = list(allowed_windows)
    missing = [w for w in window_labels if w not in window_lookup]
    if missing:
        raise SystemExit(f"unknown window label(s) {missing!r} -- not in mc.WINDOWS "
                         f"{sorted(window_lookup)}; engine WINDOWS list drifted from the locked set")

    if args.profile == 'core':
        cells = smoke_core_cells(args.gross) if args.smoke else build_core_cells(args.gross)
    else:
        cells = smoke_apex_cells() if args.smoke else build_apex_cells()
    n_iter = N_ITER_SMOKE if args.smoke else N_ITER_FULL
    mc.N_ITER = n_iter
    n_workers = int(os.environ.get('MC_WORKERS', '6'))

    csv_path, parquet_path, state_path, log_path = _paths_for_mode(args.job, mode)

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
    _tee(f"JOB {args.job}  mode={mode}  profile={args.profile}  windows={window_labels}  gross={args.gross}  "
         f"smoke={args.smoke}  n_iter={n_iter}  cells={[c.cell_name for c in cells]}", log_f)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} "
         f"git_commit={version_meta['git_commit']} (pinned from {TPSL_META_PATH})", log_f)
    _tee(f"[CONFIG] MAX_POSITIONS_CALL cells: {sorted({c.max_pos if hasattr(c,'max_pos') else c.n for c in cells})}", log_f)
    _tee(f"[CONFIG] GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP}  CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP}  "
         f"PUT_PREMIUM_CAP={mc.PUT_PREMIUM_CAP}  PRACTICAL_EXPOSURE_ENABLED={mc.PRACTICAL_EXPOSURE_ENABLED}", log_f)
    _tee(f"[CONFIG] TP_BASE={mc.TP_BASE}  TP_STRESS={mc.TP_STRESS}  SL_BASE={mc.SL_BASE}  "
         f"SL_STRESS={mc.SL_STRESS}  <- FROZEN, never patched by this campaign", log_f)
    _tee(f"[CONFIG] mode-specific: TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} "
         f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} "
         f"MC_UNIVERSE_FILE={getattr(mc, 'MC_UNIVERSE_FILE', None)!r}", log_f)
    _tee(f"[CONFIG] MC_WORKERS={os.environ.get('MC_WORKERS')} MC_NO_DB_PERSIST={os.environ.get('MC_NO_DB_PERSIST')} "
         f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR}", log_f)
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

        # Capture prepare's stdout into an in-memory buffer (ALSO written to
        # the log file) so survivor_only mode can grep it for the
        # '[universe-filter]' engagement line -- ported from phaseD_run.py.
        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        captured = buf.getvalue()
        log_f.write(captured)
        log_f.flush()

        if mode == 'survivor_only' and '[universe-filter]' not in captured:
            # Property check (ported verbatim from phaseD_run.py): the print
            # can be suppressed by the loader-memoization cache on a
            # cache-hit prepare; prove engagement directly on the loaded
            # data instead of trusting stdout scraping alone.
            _syms = {k[0] for k in ctx['call_outcomes'].keys()}
            _n_out = sum(1 for s in _syms if str(s).upper() not in survivor_set)
            if _n_out > 0:
                csv_f.close()
                log_f.close()
                raise SystemExit(
                    f"FATAL: --survivor-only set MC_UNIVERSE_FILE={SURVIVOR_FILE!r} but the "
                    f"prepare for job={args.job} window={label} shows NO '[universe-filter]' "
                    f"engagement line AND {_n_out} loaded symbols are OUTSIDE the survivor file "
                    f"-- the filter did not fire. STOP (the survivor arm must be PROVABLY "
                    f"engaged, never silently trusted). Captured prepare output: {log_path}; "
                    f"do not improvise.")
            _tee(f"[universe-check] window={label}: engagement proven via direct property on a "
                 f"cache-hit prepare ({len(_syms)} call-signal symbols, 0 outside survivor file; "
                 f"engine print suppressed by loader cache)", log_f)

        call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
        n_calls_delisted = sum(1 for s in call_syms if str(s).upper() not in survivor_set)
        _tee(f"[universe-check] window={label} mode={mode}: {len(call_syms)} distinct call-signal "
             f"symbols, {n_calls_delisted} NOT in survivor_universe_811.txt "
             f"({'expected >0 -- proves the honest universe includes delisted names' if mode != 'survivor_only' else 'expect ~0 in survivor_only mode'})",
             log_f)

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
                    print(f"[{i_pair}/{total_pairs}] SKIP (done) job={args.job} mode={mode} "
                          f"window={label} cell={cell.cell_name}", flush=True)
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
                        path_rows.append({'mode': mode, 'profile': args.profile, 'window': label,
                                           'cell_name': cell.cell_name, 'iter': i_iter, 'ret': r})

                row_prepare_s = round(t_prepare, 3) if not prepare_charged else 0.0
                prepare_charged = True

                row = {
                    'phase': 'C', 'mode': mode, 'profile': args.profile, 'window': label,
                    **cell.row_fields(),
                    'n_iter': n_iter, 'n_call_signals': n_calls, 'n_calls_delisted': n_calls_delisted,
                    'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
                    'p10_ret': p10_ret, 'p90_ret': p90_ret,
                    'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
                    'p_coll': result.get('p_coll'),
                    'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
                    'avg_open_premium_base_pct': result.get('avg_open_premium_base_pct'),
                    'max_open_premium_base_pct': result.get('max_open_premium_base_pct'),
                    'call_trades': result.get('call_trades'), 'put_trades': result.get('put_trades'),
                    'realized_call_tp_pct': result.get('call_tp'),
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
                state['mode'] = mode
                state['profile'] = args.profile
                state['windows'] = window_labels
                state['gross'] = args.gross
                state['smoke'] = bool(args.smoke)
                state['n_iter'] = n_iter
                state['algorithm_version'] = version_meta
                mc_patch.atomic_write_json(state_path, state)

                print(f"[{i_pair}/{total_pairs}] job={args.job} mode={mode} window={label} "
                      f"cell={cell.cell_name} base={'*' if cell.is_baseline else ' '} n={n_iter} "
                      f"ncalls={n_calls} n_delisted={n_calls_delisted} prepare={row_prepare_s:.1f}s "
                      f"sim={t_sim:.1f}s | worst_dd={result.get('worst_dd'):.1f}% "
                      f"med_ret={result.get('med_ret'):+.1f}% p_coll={result.get('p_coll'):.1f}% | "
                      f"avg_prem%={result.get('avg_open_premium_base_pct')} put_trades={result.get('put_trades')}",
                      flush=True)
        finally:
            if pool is not None:
                pool.close()
                pool.join()

    csv_f.close()
    elapsed_job = time.time() - t_job0
    _tee(f"\n[DONE] job={args.job} mode={mode} pairs_run_this_invocation={pairs_run_now} "
         f"total_done={len(done_set)}/{total_pairs} wall={elapsed_job:.1f}s -> {csv_path}", log_f)
    log_f.close()


if __name__ == '__main__':
    main()
