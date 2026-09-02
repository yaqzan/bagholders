#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
flipR_run.py -- MC-realism default-flip campaign, Phases R1 (re-baseline
battery) and R3 (Core exposure re-read), compute only (R2 adjudication and
R4 shipping are orchestrator work, not this file's).
(experiments/realism_default_flip_2026_08/PREREG.md, locked commit e49a3815)

======================================================================
WHAT THIS CAMPAIGN IS (PREREG, for context)
======================================================================
Every MC battery on record so far quoted CANON absolutes (TP_FILL_MISS_P=0,
GAP_AWARE=0) even though the calibrated-fill reading (0.15/ON) is the
realistic one. R1 builds the official post-flip baseline: {Core shipped,
Apex v6, Sentinel} x the 12 canonical windows x {calibrated, canon}, N=500
paired -- including Sentinel's FIRST EVER calibrated read. R3 asks whether,
once fills are calibrated, an explicit Core exposure cut (gross cap) beats
the floor_control finding that blind supply cuts improved calibrated DD
~+5pp -- i.e. is the honest lever just "trade less," and if so, does dialing
it via the actual exposure knob (not a liquidity filter) ship. Both are
PURE COMPUTE here -- this file never adjudicates R2's ranking-stability gate
or decides R3's ship lanes; it only produces the raw CSVs those decisions
read.

======================================================================
CRIBBED PATTERNS (read-only; "cribbing floorMC_run/calibP_run patterns" per
the build brief) -- what's DIRECTLY IMPORTED vs FRESHLY WRITTEN
======================================================================
DIRECTLY IMPORTED (unmodified, from experiments/tpsl_refine_2026_08/driver/
phaseD_run.py -- the SAME import list calibP_run.py already established the
precedent for): PHASE_D_WINDOWS_12 (the 12 canonical window labels),
SURVIVOR_FILE + _load_survivor_set (n_calls_delisted diagnostic, standard
phaseD schema column), _load_json + _tee (tiny utilities). mc_patch.py is
imported directly too, same as every driver in this program.

FRESHLY WRITTEN (this campaign's cell shape -- profile x window x lens for
R1, gross x mp x window for R3 -- doesn't match floor_mc's arm x window
shape closely enough to reuse its filtering functions the way floor_control
reused apply_floor; there is no equivalent "same mechanic, different input"
opportunity here): the PROFILE_ENV / LENS_ENV mapping tables, the cell-
worker (env-before-import -> import monte_carlo -> hard-verify -> prepare ->
simulate -> row, NO ctx post-filtering -- this campaign doesn't touch
call_outcomes/calls_by_date at all, unlike floor_mc/floor_control), and the
subprocess-per-cell orchestrator (same architecture, freshly written because
`os.path.abspath(__file__)` inside a reused function would resolve to the
WRONG script -- same reason floor_control couldn't reuse floorMC_run's own
_run_orchestrator, see that file's docstring for the full explanation).

======================================================================
PROFILE -> ENV MAPPING (build brief requirement 1)
======================================================================
Core shipped: NO overrides. strategy_config.STRATEGY_30DTE's own live
defaults apply untouched (verified live 2026-08-11: TP_BASE=0.10,
SL_BASE=-1.00, GROSS_PREMIUM_CAP=CALL_PREMIUM_CAP=0.50, MAX_POSITIONS=14,
TIER_ALLOC ultra/top/mid/low/overflow=.20/.15/.08/.03/0,
PRACTICAL_CAPITAL_CEILING=0.0 i.e. uncapped).

Apex v6: cribbed VERBATIM (every key) from experiments/alloc_retune_2026_08/
driver/allocC_apex2x.py's build_env_overrides(mc_patch, n=12, frac=0.06)
(lines 168-196) -- NOT mc_patch.APEX_ENV_DIFF (that constant is the OLDER
n10/f0.10 P0.3 recipe, now stale relative to the 2026-08-10 "Apex sizing
retune n10x10% -> n12x6% (profile v6)" ship -- verified live: mc_patch.py's
APEX_ENV_DIFF still reads TIER_*_OV='0.10'/MAX_POSITIONS_OVERRIDE='10',
which is the WRONG, pre-retune config for "Apex v6"). TIER_ULTRA/TOP/MID/
LOW_OV=0.06, TIER_OVERFLOW_OV=0, MAX_POSITIONS_OVERRIDE=MAX_POSITIONS_CALL=
12, GROSS_PREMIUM_CAP=CALL_PREMIUM_CAP=1.0, TP_BASE_OV=TP_STRESS_OV=0.10,
SL_BASE_OV=SL_STRESS_OV=-0.60, NOMINAL_CAL_DTE=30, HOLD_CAL_DAYS=27 (same
values as Core's own shipped defaults -- included anyway for exact fidelity
to the cribbed block, harmless), STARTING_CASH_OV=50000.0 (also a no-op vs
monte_carlo's own default, included for the same reason).

Sentinel: derived from algorithm_versions/portfolio_profiles.json's
"sentinel_v70_85plus_exp30_1m" profile params (read 2026-08-11). Every
differentiating field maps to an existing, already-verified-live env knob --
NO ambiguous field, so this profile is NOT stopped:
  tier_ultra=0.20, tier_top=0.15, tier_mid=0.0, tier_low=0.0 -> TIER_ULTRA_OV/
    TIER_TOP_OV/TIER_MID_OV/TIER_LOW_OV (same mechanism as Apex above).
  max_positions=14 -> MAX_POSITIONS_OVERRIDE/MAX_POSITIONS_CALL.
  gross_cap=0.30, call_cap=0.30 -> GROSS_PREMIUM_CAP/CALL_PREMIUM_CAP.
  capital_ceiling=1000000.0 -> PRACTICAL_CAPITAL_CEILING (monte_carlo.py:1192,
    env var name matches the internal constant exactly, no _OV suffix;
    consumed at monte_carlo.py:3305-3308 `_allocation_base`: `if
    PRACTICAL_CAPITAL_CEILING > 0: return min(value, PRACTICAL_CAPITAL_CEILING)`
    -- confirmed live, a real dollar-ceiling on the allocation base, not
    vestigial).
  "min-score 85" (build brief's expectation, matching the JSON's own
    description "85+-only (mid/low tiers off)" and CLAUDE.md's "Sentinel
    85+/30%/$1M"): there is NO literal MIN_SCORE/threshold env knob in the
    engine -- "85+" is achieved ENTIRELY via the tier zeroing above.
    monte_carlo.score_to_tier (verified live, line 1788): ultra=95-100,
    top=85-94, mid=80-84, low=75-79, overflow=<75. Zeroing mid+low leaves
    ONLY ultra+top funded = exactly the 85-100 band -- this IS the "min
    score 85" mechanism, computed programmatically per cell as
    `min_score_funded()` below (the lowest score_to_tier threshold with a
    nonzero live TIER_ALLOC entry), not asserted as a separate filter.
  put_cap/put_* = all 0/off -> already covered unconditionally by
    mc_patch.disable_puts(mc), same as every other campaign.
  dd_floor/dd_hi/dd_lo/sat_floor/sat_power/call_ref/put_ref: IDENTICAL
    across all 3 profiles' JSON params (0.4/0.55/0.35/0.55/0.5/16.0/4.0) --
    NOT profile-differentiating, so correctly left untouched (module
    defaults) for every profile, not just Sentinel.
  TP/SL, NOMINAL_CAL_DTE/HOLD_CAL_DAYS: Sentinel's JSON params carry NEITHER
    field (unlike Apex, which explicitly carries tp_base/sl_base=0.1/-0.6) --
    confirmed by the JSON's own "evidence.notes": profiles "differ only on
    exposure...+selectivity...+base ceiling -- NOT on cranking allocation."
    So Sentinel correctly inherits Core's shipped TP_BASE=0.10/SL_BASE=-1.00
    and NOMINAL_CAL_DTE=30/HOLD_CAL_DAYS=27 UNCHANGED -- zero override
    needed, exactly matching the build brief's stated expectation "TP 0.10/
    SL -1.00" with no injection required.

Every cell LOGS its engaged config read LIVE from the `mc` module post-
import (mc.TIER_ALLOC, mc.MAX_POSITIONS, mc.GROSS_PREMIUM_CAP,
mc.PRACTICAL_CAPITAL_CEILING, mc.TP_BASE/SL_BASE, mc.TP_FILL_MISS_P/
TP_FILL_GAP_AWARE) -- never the PROFILE_ENV dict's intended values -- so a
silent propagation failure would show up as a live/expected mismatch (hard
assertion) rather than a plausible-looking but wrong log line.

======================================================================
LENSES (build brief requirement 2, PREREG "calibrated"/"canon")
======================================================================
calibrated: TP_FILL_MISS_P=0.15, TP_FILL_GAP_AWARE=1 (the post-flip default
            being validated).
canon:      TP_FILL_MISS_P=0.0,  TP_FILL_GAP_AWARE=0  (today's PRE-flip
            default, becomes "the labeled optimistic variant" per PREREG
            section 1 -- a ROBUSTNESS ARM after the flip, not the primary
            read).
Set in os.environ BEFORE `import monte_carlo`, same discipline as every
driver in this program (TP_FILL_MISS_P/TP_FILL_GAP_AWARE are plain
os.environ-derived module globals read ONCE per fresh top-level import).

======================================================================
NO CTX POST-FILTERING -- unlike floor_mc/floor_control, this campaign never
touches ctx['call_outcomes']/ctx['calls_by_date'] after `_prepare_window`.
Every cell here varies only IMPORT-TIME ENV (profile config + lens knobs,
optionally gross/mp overrides for R3) -- prepare -> simulate directly.
======================================================================

HARD RULE: this file NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file, and NEVER edits anything under
experiments/tpsl_refine_2026_08/, experiments/alloc_retune_2026_08/, or any
other closed campaign (their files are read/imported READ-ONLY only, never
copied). This script never git-commits anything.

Windows/multiprocessing note (same as every driver in this program):
monte_carlo._simulate_window builds its OWN multiprocessing.Pool per call --
on Windows (spawn) every pool worker re-imports THIS FILE as a non-
`__main__` module. Everything with side effects MUST stay inside a
function, reached only through `if __name__ == '__main__':`. The eager
`from phaseD_run import ...` below is at module level but safe for the same
reason it is safe in every other driver that already does this (zero side
effects at import time, verified against phaseD_run.py's own source).

Locked spec: experiments/realism_default_flip_2026_08/PREREG.md (commit
e49a3815; DO NOT modify that file).

Usage
-----
    python experiments/realism_default_flip_2026_08/driver/flipR_run.py --selftest

    python experiments/realism_default_flip_2026_08/driver/flipR_run.py \\
        --stage r1 --job r1 [--n-iter 500] [--smoke-n N] \\
        [--profiles core,apex,sentinel] [--lenses calibrated,canon] [--windows ...]

    python experiments/realism_default_flip_2026_08/driver/flipR_run.py \\
        --stage r3 --job r3 [--n-iter 500] [--smoke-n N] [--windows ...]
        # fixed 4-config grid: {0.30/mp14, 0.40/mp14, 0.50/mp14 baseline, 0.40/mp10}

    python experiments/realism_default_flip_2026_08/driver/flipR_run.py \\
        --stage identity --job identity
        # fixed 4-cell grid: core-calibrated x {22-now,5y}, apex-calibrated x {22-now,5y}

Console output is ASCII-only throughout (no em-dash/smart-quote/unicode
arrows) per this repo's convention.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout

# --- repo-root + cross-experiment bootstrap -- explicit + asserted, never
# inferred from CWD (traps.md "Worktree PYTHONPATH trap"). Safe to re-run
# (idempotent) inside spawned cell-worker subprocesses. -----------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../realism_default_flip_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                                        # .../realism_default_flip_2026_08
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
    f"tpsl driver phaseD_run.py not found at {_TPSL_DRIVER_DIR!r} -- this campaign "
    f"requires reusing its PHASE_D_WINDOWS_12/SURVIVOR_FILE/_load_survivor_set read-only"
)

# phaseD_run.py -- READ-ONLY reuse (never edit that file): PHASE_D_WINDOWS_12/
# SURVIVOR_FILE/_load_survivor_set/_load_json only. Zero side effects at
# import time (verified against source -- its own DB/mc-touching code lives
# inside main(), guarded by `if __name__ == '__main__':`).
#
# NOTE: phaseD_run.py's OWN `_tee(msg, log_f)` takes an ALREADY-OPEN file
# HANDLE (it holds log_f open for the whole job, closing it once at the
# end) -- a DIFFERENT API from floorMC_run.py's/floorCTL_run.py's own
# `_tee(msg, log_path)`, which takes a PATH STRING and opens/closes it
# fresh per call (this file's own convention throughout, chosen for Windows
# cross-process file-access safety when a parent orchestrator and its
# spawned cell-worker children may both write the same log path -- see
# floorMC_run.py's module docstring for the original rationale). Importing
# phaseD_run's `_tee` directly here was tried and is WRONG for this file's
# calling convention (confirmed live 2026-08-11: AttributeError, 'str'
# object has no attribute 'write' -- every _tee(msg, log_path) call in this
# file passes a path, not a handle). So _tee is defined LOCALLY below,
# matching this file's own path-based calls, and is NOT imported from
# phaseD_run.py.
from phaseD_run import (                                                     # noqa: E402
    PHASE_D_WINDOWS_12, SURVIVOR_FILE, _load_survivor_set, _load_json,
)


def _tee(msg, log_path):
    """Print to real stdout (visible live via `trader queue logs`) AND
    append to log_path, opening/closing it fresh each call (never held open
    across a subprocess launch -- Windows cross-process file access safety).
    Local to THIS file -- see the import note above for why phaseD_run.py's
    own _tee (handle-based) is not reused here."""
    print(msg, flush=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TPSL_META_PATH = os.path.join(_TPSL_DRIVER_DIR, 'state', 'meta.json')   # REUSED, never written by this file

# ---------------------------------------------------------------------------
# LOCKED constants -- PREREG.md sections 3 and 5.
# ---------------------------------------------------------------------------
PROFILES = ['core', 'apex', 'sentinel']
LENSES = ['calibrated', 'canon']
IDENTITY_WINDOWS = ['22-now', '5y']         # PREREG section 3 "Identity checks"

R3_GROSS_MP_CELLS = [(0.30, 14), (0.40, 14), (0.50, 14), (0.40, 10)]   # PREREG section 5; (0.50,14) is the baseline

N_ITER_DEFAULT = 500

LENS_ENV = {
    'calibrated': {'TP_FILL_MISS_P': '0.15', 'TP_FILL_GAP_AWARE': '1'},
    'canon':      {'TP_FILL_MISS_P': '0.0',  'TP_FILL_GAP_AWARE': '0'},
}

# Profile -> explicit env overrides layered on TOP of frozen pins (mc_patch.
# apply_frozen_pins). Core = {} (no overrides). See module docstring
# "PROFILE -> ENV MAPPING" for the full citation/rationale of every value.
PROFILE_ENV = {
    'core': {},
    'apex': {   # cribbed VERBATIM from allocC_apex2x.py build_env_overrides(mc_patch, 12, 0.06), lines 168-196
        'STARTING_CASH_OV': '50000.0',
        'NOMINAL_CAL_DTE': '30',
        'HOLD_CAL_DAYS': '27',
        'TIER_ULTRA_OV': '0.060000',
        'TIER_TOP_OV': '0.060000',
        'TIER_MID_OV': '0.060000',
        'TIER_LOW_OV': '0.060000',
        'TIER_OVERFLOW_OV': '0',
        'MAX_POSITIONS_OVERRIDE': '12',
        'MAX_POSITIONS_CALL': '12',
        'GROSS_PREMIUM_CAP': '1.0',
        'CALL_PREMIUM_CAP': '1.0',
        'TP_BASE_OV': '0.1',
        'TP_STRESS_OV': '0.1',
        'SL_BASE_OV': '-0.6',
        'SL_STRESS_OV': '-0.6',
    },
    'sentinel': {   # derived from algorithm_versions/portfolio_profiles.json "sentinel_v70_85plus_exp30_1m"
        'TIER_ULTRA_OV': '0.20',
        'TIER_TOP_OV': '0.15',
        'TIER_MID_OV': '0.0',
        'TIER_LOW_OV': '0.0',
        'TIER_OVERFLOW_OV': '0',
        'MAX_POSITIONS_OVERRIDE': '14',
        'MAX_POSITIONS_CALL': '14',
        'GROSS_PREMIUM_CAP': '0.30',
        'CALL_PREMIUM_CAP': '0.30',
        'PRACTICAL_CAPITAL_CEILING': '1000000.0',
        # TP/SL and NOMINAL_CAL_DTE/HOLD_CAL_DAYS deliberately NOT overridden
        # -- Sentinel inherits Core's shipped values unchanged (see module
        # docstring).
    },
}

# Expected LIVE values per profile (post-import, pre-override-for-R3) --
# drives both the hard verification assertions and (via expected_for_cell)
# the R3 gross/mp override variant. TIER_ALLOC keys match
# monte_carlo.score_to_tier's bands exactly.
PROFILE_EXPECTED = {
    'core': {
        'TP_BASE': 0.10, 'SL_BASE': -1.00,
        'GROSS_PREMIUM_CAP': 0.50, 'CALL_PREMIUM_CAP': 0.50,
        'MAX_POSITIONS': 14,
        'TIER_ALLOC': {'ultra': 0.20, 'top': 0.15, 'mid': 0.08, 'low': 0.03, 'overflow': 0.0},
        'PRACTICAL_CAPITAL_CEILING': 0.0,
    },
    'apex': {
        'TP_BASE': 0.10, 'SL_BASE': -0.60,
        'GROSS_PREMIUM_CAP': 1.0, 'CALL_PREMIUM_CAP': 1.0,
        'MAX_POSITIONS': 12,
        'TIER_ALLOC': {'ultra': 0.06, 'top': 0.06, 'mid': 0.06, 'low': 0.06, 'overflow': 0.0},
        'PRACTICAL_CAPITAL_CEILING': 0.0,
    },
    'sentinel': {
        'TP_BASE': 0.10, 'SL_BASE': -1.00,
        'GROSS_PREMIUM_CAP': 0.30, 'CALL_PREMIUM_CAP': 0.30,
        'MAX_POSITIONS': 14,
        'TIER_ALLOC': {'ultra': 0.20, 'top': 0.15, 'mid': 0.0, 'low': 0.0, 'overflow': 0.0},
        'PRACTICAL_CAPITAL_CEILING': 1_000_000.0,
    },
}

_SCORE_TIER_THRESHOLDS = [('ultra', 95), ('top', 85), ('mid', 80), ('low', 75)]   # monte_carlo.score_to_tier, verified live


def min_score_funded(tier_alloc):
    """Lowest score_to_tier() threshold with a nonzero tier_alloc entry --
    the programmatic definition of "min score funded" for a profile (e.g.
    Sentinel's tier_mid=tier_low=0 -> only ultra(95)+top(85) funded -> 85).
    Returns None if every tier is zero (should not occur for our profiles)."""
    funded = [thresh for tier, thresh in _SCORE_TIER_THRESHOLDS if tier_alloc.get(tier, 0) > 0]
    return min(funded) if funded else None


def expected_for_cell(profile, gross_override=None, mp_override=None):
    """PROFILE_EXPECTED[profile], with R3's gross/mp override applied if
    given (R3 only ever overrides Core). Returns a fresh dict (never mutates
    PROFILE_EXPECTED)."""
    exp = dict(PROFILE_EXPECTED[profile])
    exp['TIER_ALLOC'] = dict(exp['TIER_ALLOC'])
    if gross_override is not None:
        exp['GROSS_PREMIUM_CAP'] = gross_override
        exp['CALL_PREMIUM_CAP'] = gross_override
    if mp_override is not None:
        exp['MAX_POSITIONS'] = mp_override
    return exp


def build_cell_env(profile, lens, gross_override=None, mp_override=None):
    """The full env dict for ONE cell (profile + lens + optional R3 gross/mp
    override), layered on top of mc_patch.apply_frozen_pins (applied
    separately, before this)."""
    env = {}
    env.update(PROFILE_ENV[profile])
    env.update(LENS_ENV[lens])
    if gross_override is not None:
        env['GROSS_PREMIUM_CAP'] = str(gross_override)
        env['CALL_PREMIUM_CAP'] = str(gross_override)
    if mp_override is not None:
        env['MAX_POSITIONS_OVERRIDE'] = str(mp_override)
        env['MAX_POSITIONS_CALL'] = str(mp_override)
    return env


# phaseD-style base schema (experiments/tpsl_refine_2026_08/driver/
# phaseD_run.py CSV_FIELDS, verbatim) PLUS this campaign's engaged-config
# columns (build brief: "self-logged config is the evidence") PLUS
# avg_open_premium_base_pct/max_open_premium_base_pct (realized deployment --
# R3's gross-engagement proof; these come directly from monte_carlo.py's own
# _simulate_window result dict, verified live: `result['avg_open_premium_
# base_pct'] = statistics.mean(vals)` at monte_carlo.py:4684/4692-4694,
# same column name allocC_calibrated_apex.csv already uses).
_BASE_SCHEMA = [
    'phase', 'mode', 'window', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s',
    'realized_call_tp_pct', 'n_calls_delisted',
    'tier_ultra', 'tier_top', 'tier_mid', 'tier_low', 'tier_overflow',
    'max_positions', 'gross_cap', 'call_cap', 'capital_ceiling', 'min_score_funded',
    'miss_p', 'gap_aware',
    'avg_open_premium_base_pct', 'max_open_premium_base_pct',
]
CSV_FIELDS_R1 = ['profile', 'lens'] + _BASE_SCHEMA
CSV_FIELDS_R3 = ['gross', 'mp'] + _BASE_SCHEMA
CSV_FIELDS_IDENTITY = CSV_FIELDS_R1   # identity cells are just profile/lens/window R1 cells


def r1_cells(profiles, lenses, windows):
    return [(profile, lens, window) for profile in profiles for lens in lenses for window in windows]


def r3_cells(windows):
    return [(gross, mp, window) for gross, mp in R3_GROSS_MP_CELLS for window in windows]


def identity_cells():
    return [('core', 'calibrated', w) for w in IDENTITY_WINDOWS] + \
           [('apex', 'calibrated', w) for w in IDENTITY_WINDOWS]


# ---------------------------------------------------------------------------
# --selftest -- pure logic checks, NO monte_carlo import, NO MySQL, NO B:/
# dependency.
# ---------------------------------------------------------------------------
def selftest() -> int:
    log = print
    log("=== flipR_run.py OFFLINE SELF-TESTS ===")

    # -- 1. min_score_funded matches monte_carlo.score_to_tier bands ---------
    assert min_score_funded(PROFILE_EXPECTED['core']['TIER_ALLOC']) == 75, \
        "Core: low=0.03 nonzero -> min funded score should be 75"
    assert min_score_funded(PROFILE_EXPECTED['apex']['TIER_ALLOC']) == 75, \
        "Apex v6: low=0.06 nonzero -> min funded score should be 75"
    assert min_score_funded(PROFILE_EXPECTED['sentinel']['TIER_ALLOC']) == 85, \
        "Sentinel: mid=low=0, top=0.15 nonzero -> min funded score should be 85"
    assert min_score_funded({'ultra': 0, 'top': 0, 'mid': 0, 'low': 0}) is None
    log("  [1] min_score_funded: Core=75, Apex v6=75, Sentinel=85 (all-zero -> None) OK")

    # -- 2. build_cell_env: Core has zero overrides, lens knobs always present -
    env_core_calib = build_cell_env('core', 'calibrated')
    assert env_core_calib == {'TP_FILL_MISS_P': '0.15', 'TP_FILL_GAP_AWARE': '1'}, env_core_calib
    env_core_canon = build_cell_env('core', 'canon')
    assert env_core_canon == {'TP_FILL_MISS_P': '0.0', 'TP_FILL_GAP_AWARE': '0'}, env_core_canon
    log("  [2] build_cell_env('core', ...): PROFILE_ENV contributes nothing, "
        "only lens knobs present OK")

    # -- 3. build_cell_env: Apex v6 carries every cribbed key + calibrated lens
    env_apex = build_cell_env('apex', 'calibrated')
    for k, v in PROFILE_ENV['apex'].items():
        assert env_apex[k] == v, (k, env_apex[k], v)
    assert env_apex['TP_FILL_MISS_P'] == '0.15' and env_apex['TP_FILL_GAP_AWARE'] == '1'
    assert env_apex['TIER_ULTRA_OV'] == '0.060000' and env_apex['MAX_POSITIONS_OVERRIDE'] == '12'
    assert env_apex['GROSS_PREMIUM_CAP'] == '1.0' and env_apex['TP_BASE_OV'] == '0.1' \
        and env_apex['SL_BASE_OV'] == '-0.6'
    log("  [3] build_cell_env('apex', 'calibrated'): every cribbed allocC_apex2x.py "
        "key present + lens knobs layered on top OK")

    # -- 4. build_cell_env: Sentinel carries the derived tier/gross/ceiling keys,
    #    NO tp/sl or dte keys (inherits Core's) --------------------------------
    env_sent = build_cell_env('sentinel', 'canon')
    assert env_sent['TIER_ULTRA_OV'] == '0.20' and env_sent['TIER_TOP_OV'] == '0.15'
    assert env_sent['TIER_MID_OV'] == '0.0' and env_sent['TIER_LOW_OV'] == '0.0'
    assert env_sent['GROSS_PREMIUM_CAP'] == '0.30' and env_sent['CALL_PREMIUM_CAP'] == '0.30'
    assert env_sent['PRACTICAL_CAPITAL_CEILING'] == '1000000.0'
    assert 'TP_BASE_OV' not in env_sent and 'SL_BASE_OV' not in env_sent, \
        "Sentinel must NOT override TP/SL -- it inherits Core's shipped 0.10/-1.00"
    assert 'NOMINAL_CAL_DTE' not in env_sent and 'HOLD_CAL_DAYS' not in env_sent
    log("  [4] build_cell_env('sentinel', ...): derived tier/gross/ceiling keys "
        "present, NO tp/sl/dte override (inherits Core's) OK")

    # -- 5. R3 gross/mp override layers correctly on top of the (empty) Core
    #    profile env, other profiles' env untouched by the override params --
    env_r3 = build_cell_env('core', 'calibrated', gross_override=0.30, mp_override=14)
    assert env_r3['GROSS_PREMIUM_CAP'] == '0.3' and env_r3['CALL_PREMIUM_CAP'] == '0.3'
    assert env_r3['MAX_POSITIONS_OVERRIDE'] == '14' and env_r3['MAX_POSITIONS_CALL'] == '14'
    exp_r3 = expected_for_cell('core', gross_override=0.30, mp_override=14)
    assert exp_r3['GROSS_PREMIUM_CAP'] == 0.30 and exp_r3['CALL_PREMIUM_CAP'] == 0.30
    assert exp_r3['MAX_POSITIONS'] == 14
    assert exp_r3['TIER_ALLOC'] == PROFILE_EXPECTED['core']['TIER_ALLOC'], \
        "R3 overrides gross/mp only -- TIER_ALLOC must stay Core's shipped cascade"
    # expected_for_cell must never mutate the module-level PROFILE_EXPECTED dict
    assert PROFILE_EXPECTED['core']['GROSS_PREMIUM_CAP'] == 0.50, \
        "expected_for_cell leaked a mutation into PROFILE_EXPECTED"
    log("  [5] R3 gross/mp override: env + expected values both correctly overridden, "
        "TIER_ALLOC/cascade untouched, PROFILE_EXPECTED never mutated OK")

    # -- 6. cell-list builders match the locked grids -------------------------
    ident = identity_cells()
    assert ident == [('core', 'calibrated', '22-now'), ('core', 'calibrated', '5y'),
                     ('apex', 'calibrated', '22-now'), ('apex', 'calibrated', '5y')], ident
    r1 = r1_cells(PROFILES, LENSES, PHASE_D_WINDOWS_12)
    assert len(r1) == 3 * 2 * 12 == 72, len(r1)
    assert len(set(r1)) == 72, "duplicate R1 cells"
    r3 = r3_cells(PHASE_D_WINDOWS_12)
    assert len(r3) == 4 * 12 == 48, len(r3)
    assert len(set(r3)) == 48, "duplicate R3 cells"
    assert (0.50, 14, '22-now') in r3, "R3 must include the (0.50,mp14) baseline cell"
    log("  [6] identity_cells()=4, r1_cells()=72 (3 profiles x 2 lenses x 12 windows), "
        "r3_cells()=48 (4 configs x 12 windows, baseline included), no duplicates OK")

    # -- 7. PHASE_D_WINDOWS_12 is exactly the 12-window canonical set (imported,
    #    never hand-retyped) ---------------------------------------------------
    assert len(PHASE_D_WINDOWS_12) == 12, PHASE_D_WINDOWS_12
    assert 'dip' in PHASE_D_WINDOWS_12 and '22-now' in PHASE_D_WINDOWS_12 and '5y' in PHASE_D_WINDOWS_12
    log("  [7] PHASE_D_WINDOWS_12 (imported from phaseD_run.py) has 12 windows, "
        "includes dip/22-now/5y OK")

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# CELL-WORKER -- runs exactly ONE cell in a fresh process: (profile, lens,
# window) for R1/identity, or (profile='core', lens='calibrated', window,
# gross_override, mp_override) for R3. Every side-effecting statement lives
# here or in functions it calls; only invoked from main() under
# `if __name__ == '__main__':`.
# ---------------------------------------------------------------------------
def run_one_cell(profile, lens, window, n_iter, out_csv, csv_fields, log_path, job,
                 gross_override=None, mp_override=None):
    if profile not in PROFILE_ENV:
        raise SystemExit(f"[STOP] unknown profile {profile!r} (expected one of {sorted(PROFILE_ENV)})")
    if lens not in LENS_ENV:
        raise SystemExit(f"[STOP] unknown lens {lens!r} (expected one of {sorted(LENS_ENV)})")

    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {TPSL_META_PATH} missing. This campaign pins ALGORITHM_VERSION to the SAME "
            f"id every closed campaign referenced by this one's identity gates used (id=74, "
            f"git_commit=f9fb7b934) -- required for phaseD_core_calib.csv/allocC_calibrated_"
            f"apex.csv bit-exactness to be meaningful. Refusing to auto-re-resolve (that would "
            f"WRITE into tpsl_refine_2026_08/driver/state/, violating the no-edit rule). Report "
            f"to orchestrator rather than improvising.")

    survivor_set = _load_survivor_set(SURVIVOR_FILE)   # diagnostic n_calls_delisted, standard phaseD schema column

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    # 1) env BEFORE import.
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in build_cell_env(profile, lens, gross_override, mp_override).items():
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    # 2) NOW import monte_carlo.
    import monte_carlo as mc

    # 3) post-import patches.
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)   # puts off on every profile (portfolio_profiles.json: put_cap=0.0 all three)

    # 4) hard verification -- read LIVE values off `mc`, compare against
    # expected_for_cell(...); fail fast rather than silently logging a
    # plausible-but-wrong config (module docstring "Every cell LOGS...").
    exp = expected_for_cell(profile, gross_override, mp_override)
    assert abs(mc.TP_BASE - exp['TP_BASE']) < 1e-9, \
        f"profile={profile}: mc.TP_BASE={mc.TP_BASE} != expected {exp['TP_BASE']}"
    assert abs(mc.SL_BASE - exp['SL_BASE']) < 1e-9, \
        f"profile={profile}: mc.SL_BASE={mc.SL_BASE} != expected {exp['SL_BASE']}"
    assert abs(mc.GROSS_PREMIUM_CAP - exp['GROSS_PREMIUM_CAP']) < 1e-9, \
        f"profile={profile}: mc.GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} != expected {exp['GROSS_PREMIUM_CAP']}"
    assert abs(mc.CALL_PREMIUM_CAP - exp['CALL_PREMIUM_CAP']) < 1e-9, \
        f"profile={profile}: mc.CALL_PREMIUM_CAP={mc.CALL_PREMIUM_CAP} != expected {exp['CALL_PREMIUM_CAP']}"
    live_mp = mc.MAX_POSITIONS_CALL if mc.MAX_POSITIONS_CALL is not None else mc.MAX_POSITIONS
    assert live_mp == exp['MAX_POSITIONS'], \
        f"profile={profile}: live MAX_POSITIONS(_CALL)={live_mp} != expected {exp['MAX_POSITIONS']}"
    for tier, expected_frac in exp['TIER_ALLOC'].items():
        live_frac = mc.TIER_ALLOC.get(tier)
        assert live_frac is not None and abs(live_frac - expected_frac) < 1e-9, \
            f"profile={profile}: mc.TIER_ALLOC[{tier!r}]={live_frac} != expected {expected_frac}"
    assert abs(mc.PRACTICAL_CAPITAL_CEILING - exp['PRACTICAL_CAPITAL_CEILING']) < 1e-6, \
        f"profile={profile}: mc.PRACTICAL_CAPITAL_CEILING={mc.PRACTICAL_CAPITAL_CEILING} " \
        f"!= expected {exp['PRACTICAL_CAPITAL_CEILING']}"
    exp_miss_p = float(LENS_ENV[lens]['TP_FILL_MISS_P'])
    exp_gap_aware = LENS_ENV[lens]['TP_FILL_GAP_AWARE'] == '1'
    assert abs(getattr(mc, 'TP_FILL_MISS_P', -1.0) - exp_miss_p) < 1e-9, \
        f"lens={lens}: TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != expected {exp_miss_p}"
    assert getattr(mc, 'TP_FILL_GAP_AWARE', None) is exp_gap_aware, \
        f"lens={lens}: TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != expected {exp_gap_aware}"
    assert abs(mc.LIQUIDITY_FLOOR - 0.0) < 1e-9, \
        f"mc.LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR} != 0.0 -- must stay inert (this campaign never uses it)"

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if window not in window_lookup:
        raise SystemExit(f"[STOP] window {window!r} not in mc.WINDOWS {sorted(window_lookup)} -- "
                         f"never invent/rename labels (paired-seed rule)")
    if window not in PHASE_D_WINDOWS_12:
        raise SystemExit(f"[STOP] window {window!r} not in PHASE_D_WINDOWS_12 {PHASE_D_WINDOWS_12} "
                         f"-- this campaign is scoped to the 12 canonical windows only")
    d_start, d_end = window_lookup[window]

    mc.N_ITER = n_iter

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    tag = f"gross={gross_override} mp={mp_override}" if gross_override is not None else ""
    _tee(f"CELL job={job} profile={profile} lens={lens} window={window} {tag} n_iter={n_iter} "
        f"pid={os.getpid()}", log_path)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']} "
        f"(pinned meta={TPSL_META_PATH})", log_path)
    # Required evidence lines (build brief: "Every cell must LOG its engaged
    # config (tier fracs, mp, gross, min score, TP/SL, MISS_P, GAP_AWARE) to
    # its log file -- self-logged config is the evidence"). ALL values read
    # LIVE off `mc`, not from PROFILE_ENV/expected -- this line is the actual
    # evidence, the assertions above are the guard.
    live_min_score = min_score_funded(mc.TIER_ALLOC)
    _tee(f"[ENGAGED-CONFIG] profile={profile} tier_ultra={mc.TIER_ALLOC.get('ultra')} "
        f"tier_top={mc.TIER_ALLOC.get('top')} tier_mid={mc.TIER_ALLOC.get('mid')} "
        f"tier_low={mc.TIER_ALLOC.get('low')} tier_overflow={mc.TIER_ALLOC.get('overflow')} "
        f"min_score_funded={live_min_score}", log_path)
    _tee(f"[ENGAGED-CONFIG] max_positions={live_mp} gross_cap={mc.GROSS_PREMIUM_CAP} "
        f"call_cap={mc.CALL_PREMIUM_CAP} capital_ceiling={mc.PRACTICAL_CAPITAL_CEILING} "
        f"tp_base={mc.TP_BASE} sl_base={mc.SL_BASE}", log_path)
    _tee(f"[ENGAGED-CONFIG] lens={lens} miss_p={getattr(mc, 'TP_FILL_MISS_P', None)} "
        f"gap_aware={getattr(mc, 'TP_FILL_GAP_AWARE', None)} "
        f"nominal_cal_dte={mc.NOMINAL_CAL_DTE} hold_cal_days={mc.HOLD_CAL_DAYS}", log_path)

    t0 = time.perf_counter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx = mc._prepare_window(window, d_start, d_end, version_meta['id'])
    t_prepare = time.perf_counter() - t0
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(buf.getvalue())

    call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
    n_calls_delisted = sum(1 for s in call_syms if str(s).upper() not in survivor_set)

    n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

    t1 = time.perf_counter()
    with open(log_path, 'a', encoding='utf-8') as log_f:
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

    avg_deploy = result.get('avg_open_premium_base_pct')
    max_deploy = result.get('max_open_premium_base_pct')
    _tee(f"[DEPLOYMENT] profile={profile} gross_cap={mc.GROSS_PREMIUM_CAP} "
        f"avg_open_premium_base_pct={avg_deploy} max_open_premium_base_pct={max_deploy}", log_path)

    row = {
        'phase': 'R', 'mode': lens, 'window': window,
        'tp': mc.TP_BASE, 'sl': mc.SL_BASE, 'n_iter': n_iter, 'n_call_signals': n_calls,
        'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
        'p10_ret': p10_ret, 'p90_ret': p90_ret,
        'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
        'p_coll': result.get('p_coll'),
        'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
        'realized_call_tp_pct': result.get('call_tp'),
        'n_calls_delisted': n_calls_delisted,
        'tier_ultra': mc.TIER_ALLOC.get('ultra'), 'tier_top': mc.TIER_ALLOC.get('top'),
        'tier_mid': mc.TIER_ALLOC.get('mid'), 'tier_low': mc.TIER_ALLOC.get('low'),
        'tier_overflow': mc.TIER_ALLOC.get('overflow'),
        'max_positions': live_mp, 'gross_cap': mc.GROSS_PREMIUM_CAP, 'call_cap': mc.CALL_PREMIUM_CAP,
        'capital_ceiling': mc.PRACTICAL_CAPITAL_CEILING, 'min_score_funded': live_min_score,
        'miss_p': getattr(mc, 'TP_FILL_MISS_P', None), 'gap_aware': getattr(mc, 'TP_FILL_GAP_AWARE', None),
        'avg_open_premium_base_pct': avg_deploy, 'max_open_premium_base_pct': max_deploy,
    }
    if 'profile' in csv_fields:
        row['profile'] = profile
        row['lens'] = lens
    if 'gross' in csv_fields:
        row['gross'] = gross_override
        row['mp'] = mp_override

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    csv_is_new = not os.path.exists(out_csv)
    with open(out_csv, 'a', newline='', encoding='utf-8') as csv_f:
        csv_w = csv.DictWriter(csv_f, fieldnames=csv_fields)
        if csv_is_new:
            csv_w.writeheader()
        csv_w.writerow(row)

    _tee(f"[DONE] profile={profile} lens={lens} window={window} {tag} n_iter={n_iter} "
        f"n_call_signals={n_calls} n_calls_delisted={n_calls_delisted} "
        f"prepare={t_prepare:.1f}s sim={t_sim:.1f}s | tp_rate={tp_rate:.1f}% sl_rate={sl_rate:.1f}% "
        f"hard_rate={hard_rate:.1f}% | worst_dd={result.get('worst_dd'):.2f}% "
        f"med_ret={result.get('med_ret'):+.2f}% p_coll={result.get('p_coll'):.2f}% "
        f"avg_deploy={avg_deploy} -> {out_csv}", log_path)
    return row


TWIN_FIELDS_COMPARE = [
    'n_call_signals', 'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate', 'realized_call_tp_pct',
    'n_calls_delisted', 'tier_ultra', 'tier_top', 'tier_mid', 'tier_low', 'tier_overflow',
    'max_positions', 'gross_cap', 'call_cap', 'capital_ceiling', 'min_score_funded',
    'miss_p', 'gap_aware', 'avg_open_premium_base_pct', 'max_open_premium_base_pct',
]   # deliberately EXCLUDES elapsed_prepare_s/elapsed_sim_s -- wall-clock timings
    # are legitimately non-deterministic between two independent subprocesses;
    # every OTHER field is a function of (seed, loaded data, config) only, per
    # monte_carlo's own seeded-bounded-fill design (reference_mc_3mode.md).


def run_twin_check(job, log_path, n_iter):
    """AMENDMENT-2 point 1 / AMENDMENT-3 point 3: the identity gate's SAME-RUN
    determinism half. Launches Core-calibrated-22-now TWICE, as two fully
    independent cell-worker subprocesses (same architecture/rationale as
    _run_orchestrator's own subprocess-per-cell launches -- env-before-import
    means each subprocess gets its own fresh `import monte_carlo`), each
    writing to its OWN csv (never sharing a row-append target, so there is no
    dedup/collision risk). Compares every deterministic field in
    TWIN_FIELDS_COMPARE for exact equality -- monte_carlo's seeded model means
    two runs of the IDENTICAL config against the IDENTICAL substrate must
    produce bit-identical results; any mismatch is a genuine finding (loader
    non-determinism, e.g. dict/set iteration order feeding into a
    parallelized reduction), not noise to average away."""
    csv_a = os.path.join(OUT_DIR, f'flipR_{job}_a.csv')
    csv_b = os.path.join(OUT_DIR, f'flipR_{job}_b.csv')
    for p in (csv_a, csv_b):
        if os.path.exists(p):
            os.remove(p)   # twin runs are never resumable/appendable -- always fresh

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"TWIN-CHECK job={job} cell=(core,calibrated,22-now) n_iter={n_iter} "
        f"-- two independent subprocesses, same config, must match bit-exactly", log_path)

    child_env = os.environ.copy()
    child_env['PYTHONIOENCODING'] = 'utf-8'
    rows = []
    for tag, out_csv in (('A', csv_a), ('B', csv_b)):
        cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker',
              '--profile', 'core', '--lens', 'calibrated', '--window', '22-now',
              '--n-iter', str(n_iter), '--out-csv', out_csv, '--csv-schema', 'r1',
              '--log-path', log_path, '--job', f'{job}_{tag.lower()}']
        t0 = time.time()
        _tee(f"[TWIN-{tag}] launching independent subprocess...", log_path)
        proc = subprocess.run(cmd, env=child_env, cwd=_REPO_ROOT)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise SystemExit(f"[STOP] twin subprocess {tag} FAILED (exit {proc.returncode}) after {elapsed:.1f}s")
        with open(out_csv, newline='', encoding='utf-8') as f:
            row = list(csv.DictReader(f))[0]
        rows.append(row)
        _tee(f"[TWIN-{tag}] OK ({elapsed:.1f}s) n_call_signals={row['n_call_signals']} "
            f"med_ret={row['med_ret']} worst_dd={row['worst_dd']}", log_path)

    row_a, row_b = rows
    mismatches = []
    for field in TWIN_FIELDS_COMPARE:
        va, vb = row_a.get(field), row_b.get(field)
        if va != vb:
            mismatches.append({'field': field, 'a': va, 'b': vb})

    if mismatches:
        _tee(f"[TWIN-CHECK-RESULT] FAIL -- {len(mismatches)} field(s) differ between the two "
            f"independent runs: {mismatches}", log_path)
    else:
        _tee(f"[TWIN-CHECK-RESULT] PASS -- all {len(TWIN_FIELDS_COMPARE)} deterministic fields "
            f"bit-exact across two independent subprocesses", log_path)

    return {'passed': not mismatches, 'mismatches': mismatches, 'row_a': row_a, 'row_b': row_b}


def max_score_date():
    """Live MAX(Score.date) across the whole scores table -- the AMENDMENT-2
    point 2 close-boundary guard's own probe. A single indexed ORDER BY DESC
    LIMIT 1 query (sub-second, no full-table scan) -- "genuinely light"
    enough to run directly inside an already-queued orchestrator process,
    same as mc_patch.resolve_and_pin_version's own incidental DB touch.
    Lazy-imports database.models.core (same successful bare pattern used
    for the 2026-08-11 live recon earlier this session: peewee connects
    transparently, no explicit DB.connect() needed)."""
    from database.models.core import Score
    row = (Score
          .select(Score.date)
          .order_by(Score.date.desc())
          .limit(1)
          .scalar())
    return row


def compute_fingerprint(windows, log_path):
    """AMENDMENT-3 point 2: substrate-fingerprint guard, "REQUIRED equipment
    for this and all future multi-cell batteries" -- per-window LOADED >=70
    count (i.e. len(ctx['call_outcomes']), the exact same population
    n_call_signals is drawn from) snapshotted at battery START and END.
    Cheap by design: Core+calibrated _prepare_window() ONLY (no simulate --
    the DB load/filter pass, not the N=500 MC), same config every other
    verify-* stage in this file already uses for a population read. Returns
    {window_label: n_call_signals}. A change between the start and end
    snapshot for a given window means every cell in THIS battery that used
    that window ran against a moving substrate -- distinct from (and a
    superset check on top of) the close-boundary guard's own max(Score.date)
    probe, which can miss a same-day row MUTATION (AMENDMENT-3's own root
    cause: MNST's replace-semantics re-backfill changed 22 rows' `overall`
    without necessarily advancing max(date))."""
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing (see run_one_cell's docstring note)")

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in LENS_ENV['calibrated'].items():
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}

    fp = {}
    for label in windows:
        if label not in window_lookup:
            _tee(f"[FINGERPRINT-GUARD] WARNING: window {label!r} not in mc.WINDOWS -- skipped", log_path)
            continue
        d_start, d_end = window_lookup[label]
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(buf.getvalue())
        fp[label] = len(ctx['call_outcomes'])
    return fp


# ---------------------------------------------------------------------------
# ORCHESTRATOR -- loops a stage's fixed cell list, launching one cell-worker
# subprocess per cell not already done (state-file resumable). Cannot reuse
# any sibling campaign's own orchestrator: `os.path.abspath(__file__)`
# inside a reused function resolves to THAT module's own path, not this
# file's -- same reason floor_control couldn't reuse floorMC_run's
# _run_orchestrator (see that file's docstring). Cells are (profile, lens,
# window) 3-tuples (R1/identity) or (profile, lens, window, gross, mp)
# 5-tuples (R3) -- uniform within any one job, never mixed.
#
# AMENDMENT-2 point 2 (close-boundary guard): max_score_date() is snapshotted
# before EVERY cell (not just battery start/end -- the spec's literal
# minimum -- because the marginal cost is one indexed query, and doing so
# gives EXACT attribution of which cell first ran after a boundary shift,
# rather than only "somewhere in this batch"). A change from the running
# baseline is logged as a loud [CLOSE-BOUNDARY-FLAG] and the affected cell
# keys are collected into `tainted_cells` (returned to the caller, who
# surfaces them in the final report) -- this file does NOT automatically
# discard/rerun a tainted cell's row (that is a judgment call reserved for
# the orchestrator per this campaign's "no interpretation" mandate), it only
# detects and flags, loudly, and keeps going (the coordinator's own words:
# "a run tonight is safe -- the guard is belt-and-suspenders").
# ---------------------------------------------------------------------------
def _run_orchestrator(job, stage, cells, csv_fields, n_iter, log_path, state_path, out_csv):
    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"ORCHESTRATOR job={job} stage={stage} n_cells={len(cells)} n_iter={n_iter}", log_path)

    baseline_score_date = max_score_date()
    battery_start_date = baseline_score_date
    _tee(f"[CLOSE-BOUNDARY-GUARD] battery START max(Score.date)={baseline_score_date}", log_path)
    tainted_cells = []

    # AMENDMENT-3 point 2: substrate-fingerprint guard, required equipment for
    # every multi-cell battery from here on. Distinct windows are read off
    # `cells` itself (index 2 in both the 3-tuple and 5-tuple cell shapes).
    distinct_windows = sorted({cell[2] for cell in cells})
    fp_start = compute_fingerprint(distinct_windows, log_path)
    _tee(f"[FINGERPRINT-GUARD] battery START per-window loaded>=70 counts: {fp_start}", log_path)

    t_job0 = time.time()
    for i, cell in enumerate(cells, 1):
        if len(cell) == 3:
            profile, lens, window = cell
            gross_override = mp_override = None
        else:
            profile, lens, window, gross_override, mp_override = cell
        key = tuple(cell)
        if key in done_set:
            _tee(f"[{i}/{len(cells)}] SKIP (already done) {cell}", log_path)
            continue

        current_score_date = max_score_date()
        if current_score_date != baseline_score_date:
            _tee(f"[CLOSE-BOUNDARY-FLAG] max(Score.date) changed {baseline_score_date} -> "
                f"{current_score_date} BEFORE cell {i}/{len(cells)} {cell} -- this cell and all "
                f"remaining cells in this job run under a NEWER score snapshot than cells "
                f"1..{i-1}. Flagging as tainted per AMENDMENT-2 point 2 (rerun candidate).", log_path)
            tainted_cells.append({'cell': cell, 'index': i, 'reason': 'close-boundary',
                                  'old_max_date': str(baseline_score_date),
                                  'new_max_date': str(current_score_date)})
            baseline_score_date = current_score_date   # new running baseline; keep going (belt-and-suspenders, not a stop)

        _tee(f"[{i}/{len(cells)}] LAUNCH profile={profile} lens={lens} window={window} "
            f"gross={gross_override} mp={mp_override} n_iter={n_iter}", log_path)

        child_env = os.environ.copy()
        child_env['PYTHONIOENCODING'] = 'utf-8'
        cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker',
              '--profile', profile, '--lens', lens, '--window', window, '--n-iter', str(n_iter),
              '--out-csv', out_csv, '--csv-schema', ('r1' if len(cell) == 3 else 'r3'),
              '--log-path', log_path, '--job', job]
        if gross_override is not None:
            cmd += ['--gross', str(gross_override), '--mp', str(mp_override)]
        t0 = time.time()
        proc = subprocess.run(cmd, env=child_env, cwd=_REPO_ROOT)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise SystemExit(f"[STOP] cell-worker FAILED (exit {proc.returncode}) cell={cell} "
                             f"after {elapsed:.1f}s -- see {log_path} tail. "
                             f"{i - 1}/{len(cells)} cells completed before this failure.")

        done_set.add(key)
        state['done_cells'] = [list(k) for k in sorted(done_set, key=lambda t: [str(x) for x in t])]
        state['job'] = job
        state['stage'] = stage
        state['n_iter'] = n_iter
        state['tainted_cells'] = tainted_cells
        import mc_patch
        mc_patch.atomic_write_json(state_path, state)
        _tee(f"[{i}/{len(cells)}] OK {cell} ({elapsed:.1f}s)", log_path)

    final_score_date = max_score_date()
    _tee(f"[CLOSE-BOUNDARY-GUARD] battery END max(Score.date)={final_score_date} "
        f"(started at {battery_start_date})", log_path)

    fp_end = compute_fingerprint(distinct_windows, log_path)
    _tee(f"[FINGERPRINT-GUARD] battery END per-window loaded>=70 counts: {fp_end}", log_path)
    fp_changed = {w: (fp_start.get(w), fp_end.get(w)) for w in distinct_windows if fp_start.get(w) != fp_end.get(w)}
    if fp_changed:
        _tee(f"[FINGERPRINT-GUARD-FLAG] {len(fp_changed)} window(s) changed loaded>=70 count "
            f"between battery start and end: {fp_changed}. Every cell in this battery that used "
            f"one of these windows is flagged as tainted per AMENDMENT-3 point 2 (rerun candidate).", log_path)
        for i, cell in enumerate(cells, 1):
            if cell[2] in fp_changed and tuple(cell) in done_set:
                tainted_cells.append({'cell': cell, 'index': i, 'reason': 'fingerprint',
                                      'window': cell[2], 'start_count': fp_changed[cell[2]][0],
                                      'end_count': fp_changed[cell[2]][1]})
    else:
        _tee("[FINGERPRINT-GUARD] no count change detected on any touched window -- 0 tainted windows", log_path)

    if tainted_cells:
        _tee(f"[TAINT-SUMMARY] {len(tainted_cells)} cell(s) flagged as tainted this run: "
            f"{tainted_cells}", log_path)
    else:
        _tee("[TAINT-SUMMARY] no boundary or fingerprint change detected -- 0 tainted cells", log_path)

    elapsed_job = time.time() - t_job0
    _tee(f"\n[ORCHESTRATOR DONE] job={job} stage={stage} total_done={len(done_set)}/{len(cells)} "
        f"wall={elapsed_job:.1f}s -> {out_csv}", log_path)

    # Persist fingerprint evidence into the state file for post-hoc reporting
    # (mirrors tainted_cells, which is already written into state each cell).
    state['fingerprint_start'] = fp_start
    state['fingerprint_end'] = fp_end
    state['fingerprint_changed'] = fp_changed
    state['tainted_cells'] = tainted_cells
    import mc_patch
    mc_patch.atomic_write_json(state_path, state)
    return tainted_cells


def _paths_for_job(job):
    return (
        os.path.join(OUT_DIR, f'flipR_{job}.csv'),
        os.path.join(STATE_DIR, f'flipR_{job}.json'),
        os.path.join(LOG_DIR, f'{job}.log'),
    )


DRIFT_VERIFY_FIELDS = [
    'window', 'configured_start', 'configured_end', 'n_call_signals',
    'min_signal_date', 'max_signal_date',
    'n_dated_2026_08_11', 'n_dated_2026_08_10',
    'ref_n_call_signals', 'delta_vs_ref',
]

# The 2026-08-10 reference n_call_signals (from phaseD_core_calib.csv /
# allocC_calibrated_apex.csv, both profile-invariant for n_call_signals
# since profile doesn't affect which raw call signals load -- only tier
# allocation of them) -- read 2026-08-11, cited for the delta arithmetic
# AMENDMENT-2 point 1 asks this task to reconcile.
REF_N_CALL_SIGNALS = {'22-now': 19257, '5y': 25701}


def run_verify_drift(job, log_path, out_csv):
    """AMENDMENT-2 task 1: verify the drift arithmetic with evidence, not
    assumption. Prepares Core-calibrated ctx for 22-now and 5y (the same
    config the identity gate uses) and reports, per window: the window's
    CONFIGURED [start,end] as read LIVE from mc.WINDOWS (never assumed from
    memory -- a prior value could itself be stale, exactly the trap this
    whole amendment is about), the actual min/max signal date PRESENT in the
    loaded population, total n_call_signals, and counts of signals dated
    exactly 2026-08-11 and 2026-08-10 (the coordinator's claimed +4/+4
    counts). This function does NOT assume the coordinator's story is
    correct -- it reports what is actually in the data, including the case
    where the window's own hard [start,end] boundary makes an August-2026-
    dated signal structurally impossible to load at all (which would falsify
    the story as literally stated, not merely fail to observe it)."""
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing (see run_one_cell's docstring note)")

    from datetime import date as _date

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in LENS_ENV['calibrated'].items():   # Core (no profile override) + calibrated, matching the identity cells exactly
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"VERIFY-DRIFT job={job} algorithm_version id={version_meta['id']} "
        f"git_commit={version_meta['git_commit']}", log_path)
    _tee(f"[CONFIG] TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} "
        f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} (Core, no profile override)", log_path)

    rows = []
    signal_sets = {}   # window -> set of (symbol, date) keys, for the aged-out cross-check below
    for label in ('22-now', '5y'):
        d_start, d_end = window_lookup[label]
        _tee(f"[WINDOW-DEF] label={label} configured_start={d_start} configured_end={d_end} "
            f"(read LIVE from mc.WINDOWS just now -- not assumed from memory)", log_path)

        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(buf.getvalue())

        keys = list(ctx['call_outcomes'].keys())
        signal_sets[label] = set(keys)
        dates_present = sorted(set(k[1] for k in keys))
        min_date = dates_present[0] if dates_present else None
        max_date = dates_present[-1] if dates_present else None
        n_total = len(keys)
        n_0811 = sum(1 for k in keys if k[1] == _date(2026, 8, 11))
        n_0810 = sum(1 for k in keys if k[1] == _date(2026, 8, 10))
        ref_n = REF_N_CALL_SIGNALS.get(label)
        delta = n_total - ref_n if ref_n is not None else None

        _tee(f"[SIGNAL-POP] window={label} n_call_signals={n_total} (2026-08-10 ref={ref_n}, "
            f"delta={delta}) min_signal_date={min_date} max_signal_date={max_date} "
            f"n_dated_2026-08-11={n_0811} n_dated_2026-08-10={n_0810} prepare={t_prepare:.1f}s", log_path)

        rows.append({
            'window': label, 'configured_start': str(d_start), 'configured_end': str(d_end),
            'n_call_signals': n_total, 'min_signal_date': str(min_date), 'max_signal_date': str(max_date),
            'n_dated_2026_08_11': n_0811, 'n_dated_2026_08_10': n_0810,
            'ref_n_call_signals': ref_n, 'delta_vs_ref': delta,
        })

    # 5y "aged-out" cross-check: signals present in 22-now's population that
    # are ALSO within 5y's OWN configured [start,end] range but are ABSENT
    # from 5y's actual loaded population -- would only be non-empty if 5y's
    # own start boundary excludes something 22-now's wider range still
    # includes. This is a STRUCTURAL cross-check computable from data already
    # fetched above, requiring no saved historical population.
    d5_start, d5_end = window_lookup['5y']
    in_22now_within_5y_range = {k for k in signal_sets['22-now'] if d5_start <= k[1] <= d5_end}
    missing_from_5y = sorted(in_22now_within_5y_range - signal_sets['5y'], key=lambda k: (k[1], k[0]))
    _tee(f"[AGED-OUT-CROSSCHECK] {len(missing_from_5y)} (symbol,date) pair(s) fall inside 5y's "
        f"OWN configured range {d5_start}..{d5_end} per 22-now's population, but are ABSENT from "
        f"5y's own loaded population: {missing_from_5y}", log_path)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=DRIFT_VERIFY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    _tee(f"[WRITE] {out_csv} ({len(rows)} rows)", log_path)
    return rows, missing_from_5y


VERIFY_MNST_FIELDS = ['window', 'symbol', 'date', 'overall', 'is_mnst', 'in_declarations_arm30', 'in_ledger_v2']

DECLARATIONS_ARM30_PATH = os.path.join(_REPO_ROOT, 'experiments', 'tp_fill_fidelity_30dte', 'out', 'declarations_arm30.parquet')
LEDGER_V2_PATH = r'B:\polygon_derived\ledger_v2\ledger.parquet'


def run_verify_mnst_loader(job, log_path, out_csv):
    """Coordinator follow-up: does MNST's raw >=70 population (12 rows in
    22-now's date range, per the raw scores-table confirmation query)
    survive load_signals()'s post-threshold eligibility filters, or does the
    loader drop most of them (earnings-window suppression etc)? Same loader
    path as run_verify_drift (Core, calibrated lens, no profile override) --
    _prepare_window() -> ctx['call_outcomes']/['calls_by_date'], the exact
    structures that determine n_call_signals.

    ALSO (coordinator's branch-3 contingency, run unconditionally since it's
    cheap given the ctx is already prepared): the top-10 most-recent non-MNST
    LOADED 75+ candidates per window that are absent from BOTH Aug-10-era
    artifacts (declarations_arm30.parquet, ledger_v2/ledger.parquet) -- the
    suspect list for a follow-up round if MNST turns out not to (fully)
    explain the drift. 75+ only, matching both artifacts' own SCORE_MIN=75
    build scope (see floor_mc_2026_08's own documented 70-74 blind spot)."""
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing (see run_one_cell's docstring note)")

    import polars as pl

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in LENS_ENV['calibrated'].items():
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"VERIFY-MNST-LOADER job={job} algorithm_version id={version_meta['id']} "
        f"git_commit={version_meta['git_commit']}", log_path)

    decl = pl.read_parquet(DECLARATIONS_ARM30_PATH)
    decl_keys = set(zip(decl['symbol'].to_list(), decl['signal_date'].to_list()))
    ledger = pl.read_parquet(LEDGER_V2_PATH)
    ledger_keys = set(zip(ledger['symbol'].to_list(), ledger['entry_date'].to_list()))
    _tee(f"[ARTIFACTS] declarations_arm30 keys={len(decl_keys)} ledger_v2 keys={len(ledger_keys)}", log_path)

    rows = []
    for label in ('22-now', '5y'):
        d_start, d_end = window_lookup[label]
        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(buf.getvalue())

        n_total = len(ctx['call_outcomes'])
        overall_by_key = {}
        for _d, entries in ctx['calls_by_date'].items():
            for sid, overall, key, ct, ern in entries:
                overall_by_key[key] = overall

        mnst_keys = sorted((k for k in ctx['call_outcomes'].keys() if k[0] == 'MNST'), key=lambda k: k[1])
        _tee(f"[LOADED-POP] window={label} n_call_signals={n_total} n_MNST_loaded={len(mnst_keys)} "
            f"prepare={t_prepare:.1f}s", log_path)
        for sym, d in mnst_keys:
            ov = overall_by_key.get((sym, d))
            _tee(f"[LOADED-MNST] window={label} symbol={sym} date={d} overall={ov}", log_path)
            rows.append({'window': label, 'symbol': sym, 'date': str(d), 'overall': ov,
                        'is_mnst': True, 'in_declarations_arm30': (sym, d) in decl_keys,
                        'in_ledger_v2': (sym, d) in ledger_keys})
        if not mnst_keys:
            _tee(f"[LOADED-MNST] window={label}: 0 MNST rows survived the loader", log_path)

        # Suspect list: 75+, non-MNST, loaded today, absent from BOTH artifacts,
        # most-recent-date first, top 10.
        absent_75p = []
        for k in ctx['call_outcomes'].keys():
            sym, d = k
            if sym == 'MNST':
                continue
            ov = overall_by_key.get(k, 0)
            if ov < 75:
                continue
            if k not in decl_keys and k not in ledger_keys:
                absent_75p.append((sym, d, ov))
        absent_75p.sort(key=lambda t: t[1], reverse=True)
        top10 = absent_75p[:10]
        _tee(f"[SUSPECT-LIST] window={label}: {len(absent_75p)} non-MNST 75+ loaded candidates "
            f"absent from both artifacts; top 10 most recent:", log_path)
        for sym, d, ov in top10:
            _tee(f"  [SUSPECT] window={label} symbol={sym} date={d} overall={ov}", log_path)
            rows.append({'window': label, 'symbol': sym, 'date': str(d), 'overall': ov,
                        'is_mnst': False, 'in_declarations_arm30': False, 'in_ledger_v2': False})

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=VERIFY_MNST_FIELDS)
        w.writeheader()
        w.writerows(rows)
    _tee(f"[WRITE] {out_csv} ({len(rows)} rows)", log_path)
    return rows


REVERSE_DIFF_FIELDS = ['window', 'symbol', 'date']


def run_reverse_diff(job, log_path, out_csv):
    """Coordinator round 5: the REVERSE diff. Aug-10-era 75+ identity set
    (declarations_arm30.parquet keys UNION ledger_v2 keys, any status) minus
    TODAY'S loaded population, per window, restricted to that window's own
    date range (a key outside the window's [start,end] is not a candidate
    for that window's n_call_signals at all, so it is correctly excluded,
    not merely "ignored"). Grouped by symbol. Same loader path as
    run_verify_drift/run_verify_mnst_loader (Core, calibrated lens, no
    profile override)."""
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing (see run_one_cell's docstring note)")

    import polars as pl
    from collections import defaultdict

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in LENS_ENV['calibrated'].items():
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"REVERSE-DIFF job={job} algorithm_version id={version_meta['id']} "
        f"git_commit={version_meta['git_commit']}", log_path)

    decl = pl.read_parquet(DECLARATIONS_ARM30_PATH)
    decl_keys = set(zip(decl['symbol'].to_list(), decl['signal_date'].to_list()))
    ledger = pl.read_parquet(LEDGER_V2_PATH)   # ALL rows (any status) -- a non-kept row is still a
                                                # real 75+ SCORE signal as of Aug-10, just one with no
                                                # option contract; the score's existence is what this
                                                # diff cares about, not FF-1 contract-buildability.
    ledger_keys = set(zip(ledger['symbol'].to_list(), ledger['entry_date'].to_list()))
    aug10_union = decl_keys | ledger_keys
    _tee(f"[ARTIFACTS] declarations_arm30={len(decl_keys)} ledger_v2(any status)={len(ledger_keys)} "
        f"union={len(aug10_union)}", log_path)

    rows = []
    for label in ('22-now', '5y'):
        d_start, d_end = window_lookup[label]
        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(buf.getvalue())

        loaded_keys = set(ctx['call_outcomes'].keys())
        aug10_in_range = {k for k in aug10_union if d_start <= k[1] <= d_end}
        missing = aug10_in_range - loaded_keys

        by_symbol = defaultdict(list)
        for sym, d in missing:
            by_symbol[sym].append(d)

        # Task 4: distinct-symbol counts. ledger_v2's OWN symbol universe
        # (any status, so "attempted" regardless of contract-buildability) is
        # scoped to the archive (2022-08-05+) by construction (verified
        # earlier this campaign: entry_date min 2022-08-05); no extra date
        # filtering needed to express "2022-08+" here.
        loaded_symbols = {k[0] for k in loaded_keys}
        ledger_symbol_universe = set(ledger['symbol'].to_list())
        ledger_symbols_absent_from_loaded = sorted(ledger_symbol_universe - loaded_symbols)
        _tee(f"[SYMBOL-COUNTS] window={label}: {len(loaded_symbols)} distinct symbols in today's "
            f"loaded population | ledger_v2 symbol universe (any status, 2022-08+)={len(ledger_symbol_universe)} "
            f"| ledger symbols ENTIRELY absent from loaded set={len(ledger_symbols_absent_from_loaded)}: "
            f"{ledger_symbols_absent_from_loaded}", log_path)

        _tee(f"[REVERSE-DIFF] window={label} n_loaded={len(loaded_keys)} "
            f"aug10_union_in_range={len(aug10_in_range)} MISSING={len(missing)} "
            f"distinct_symbols_missing={len(by_symbol)}", log_path)
        for sym in sorted(by_symbol):
            dates = sorted(by_symbol[sym])
            _tee(f"[MISSING-SYMBOL] window={label} symbol={sym} n={len(dates)} dates={dates}", log_path)
            for d in dates:
                rows.append({'window': label, 'symbol': sym, 'date': str(d)})

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=REVERSE_DIFF_FIELDS)
        w.writeheader()
        w.writerows(rows)
    _tee(f"[WRITE] {out_csv} ({len(rows)} rows)", log_path)
    return rows


COMPUTE_U_FIELDS = ['symbol']   # just the U membership list -- detailed per-symbol breakdown done separately (direct SQL, U is expected small)


def run_compute_u(job, log_path, out_csv):
    """Coordinator round 6 task 1 (the set-membership half only -- U itself).
    U = symbols with scores(version=74, overall>=70) inside
    [2021-01-01..2026-04-24] (Set A, direct SQL) that contribute ZERO rows
    to TODAY'S loaded populations (Set B = union of 22-now's and 5y's loaded
    symbol sets, same loader path as every verify-* stage). U = A - B.
    Per-member zone/band/stocks/price_history breakdown is done separately
    (direct SQL, once U's membership -- expected small -- is known; no need
    to pay for another _prepare_window just to compute a handful of
    aggregate queries)."""
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing (see run_one_cell's docstring note)")

    from datetime import date as _date

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in LENS_ENV['calibrated'].items():
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"COMPUTE-U job={job} algorithm_version id={version_meta['id']} "
        f"git_commit={version_meta['git_commit']}", log_path)

    loaded_symbols_union = set()
    for label in ('22-now', '5y'):
        d_start, d_end = window_lookup[label]
        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(buf.getvalue())
        window_syms = {k[0] for k in ctx['call_outcomes'].keys()}
        loaded_symbols_union |= window_syms
        _tee(f"[LOADED] window={label} n_loaded_keys={len(ctx['call_outcomes'])} "
            f"n_distinct_symbols={len(window_syms)} prepare={t_prepare:.1f}s", log_path)

    _tee(f"[SET-B] union of both windows' loaded symbols: {len(loaded_symbols_union)}", log_path)

    from database.trader_database import DB as _db
    _db.connect(reuse_if_open=True)
    cur = _db.execute_sql(
        "SELECT DISTINCT symbol FROM scores WHERE version_id=74 AND overall>=70 "
        "AND date BETWEEN '2021-01-01' AND '2026-04-24'")
    set_a = {r[0] for r in cur.fetchall()}
    _tee(f"[SET-A] distinct symbols with >=70 scores in [2021-01-01..2026-04-24] (v74): {len(set_a)}", log_path)

    u = sorted(set_a - loaded_symbols_union)
    _tee(f"[SET-U] A - B = {len(u)} symbols: {u}", log_path)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COMPUTE_U_FIELDS)
        w.writeheader()
        for sym in u:
            w.writerow({'symbol': sym})
    _tee(f"[WRITE] {out_csv} ({len(u)} rows)", log_path)
    return u


COMPUTE_V_FIELDS = [
    'window', 'symbol', 'date', 'overall',
    'ph_exact_date_match', 'ph_close_on_date', 'ph_row_count_total',
    'ph_min_date', 'ph_max_date', 'ph_bars_before_60', 'ph_bars_after_within_hold',
    'ph_pulled_at_max', 'stocks_created_at', 'stocks_delisted_date',
]


def run_compute_v(job, log_path, out_csv):
    """Coordinator round 7: V = every (symbol,date) with a v74 score>=70
    that load_signals() itself keeps (i.e. is in ctx['calls_by_date']) but
    that precompute_outcomes()/compute_trade_outcome() DROPS before it
    reaches ctx['call_outcomes'] -- computed EXACTLY the way monte_carlo
    computes it internally (calls_by_date keys minus call_outcomes keys),
    not approximated via the external Aug-10 artifacts (which have their
    own, different scope/build logic -- see prior rounds). PCH 2026-01-30 is
    the known validation case and must appear.

    For each V member, direct SQL against price_history/stocks supplies the
    diagnostic fields needed to attribute WHICH of the 5 enumerated
    conditions failed (module docstring / report has the sourced list):
      ph_exact_date_match : does a price_history row exist for exactly
                             (symbol, date)? (condition D1/C1)
      ph_close_on_date     : that row's close (condition D3, entry_price<=0)
      ph_bars_before_60    : COUNT of price_history rows for this symbol
                              strictly before `date` (condition D4 needs
                              >=60 -- VOL_LOOKBACK, strategy_config.py:1520)
      ph_bars_after_within_hold : COUNT of price_history rows strictly after
                              `date`, up to date+27 calendar days
                              (HOLD_CAL_DAYS) -- condition D5 needs >=1
      ph_min_date/ph_max_date/ph_row_count_total : the symbol's FULL
                              price_history footprint (for front/back-
                              truncation classification)
      ph_pulled_at_max      : MAX(pulled_at) for this symbol (price_history
                              DOES carry a nullable `pulled_at` datetime
                              column -- confirmed live via SHOW COLUMNS)
      stocks_created_at / stocks_delisted_date : universe-timing context
    """
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing (see run_one_cell's docstring note)")

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in LENS_ENV['calibrated'].items():
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"COMPUTE-V job={job} algorithm_version id={version_meta['id']} "
        f"git_commit={version_meta['git_commit']} HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS} "
        f"VOL_LOOKBACK={mc.VOL_LOOKBACK}", log_path)

    from database.trader_database import DB as _db
    _db.connect(reuse_if_open=True)

    v_by_window = {}
    overall_by_window_key = {}   # (window, (symbol,date)) -> overall
    for label in ('22-now', '5y'):
        d_start, d_end = window_lookup[label]
        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(buf.getvalue())

        overall_by_key = {}
        all_keys = set()
        for _d, entries in ctx['calls_by_date'].items():
            for sid, overall, key, ct, ern in entries:
                overall_by_key[key] = overall
                all_keys.add(key)
        loaded_keys = set(ctx['call_outcomes'].keys())
        v_keys = sorted(all_keys - loaded_keys, key=lambda k: (k[1], k[0]))
        v_by_window[label] = v_keys
        _tee(f"[COMPUTE-V] window={label} n_calls_by_date_keys={len(all_keys)} "
            f"n_call_outcomes_keys={len(loaded_keys)} n_V={len(v_keys)} prepare={t_prepare:.1f}s", log_path)
        for sym, d in v_keys:
            ov = overall_by_key.get((sym, d))
            overall_by_window_key[(label, (sym, d))] = ov
            _tee(f"[V-MEMBER] window={label} symbol={sym} date={d} overall={ov}", log_path)

    # Direct SQL diagnostics for every distinct V member (union across windows,
    # de-duplicated by (symbol,date) -- PCH's 2026-01-30 will appear once even
    # though it is a V-member of both windows).
    from datetime import timedelta as _td
    all_v_union = sorted({k for keys in v_by_window.values() for k in keys}, key=lambda k: (k[1], k[0]))
    _tee(f"[COMPUTE-V] distinct (symbol,date) V members across both windows: {len(all_v_union)}", log_path)

    all_v_rows = []
    for sym, d in all_v_union:
        d_str = d.isoformat()
        hold_end = (d + _td(days=mc.HOLD_CAL_DAYS)).isoformat()

        cur = _db.execute_sql(
            "SELECT close FROM price_history WHERE symbol=%s AND date=%s", (sym, d_str))
        row = cur.fetchone()
        ph_exact = row is not None
        ph_close = float(row[0]) if row and row[0] is not None else None

        cur = _db.execute_sql(
            "SELECT COUNT(*), MIN(date), MAX(date), MAX(pulled_at) FROM price_history WHERE symbol=%s", (sym,))
        n_total, min_d, max_d, pulled_max = cur.fetchone()

        cur = _db.execute_sql(
            "SELECT COUNT(*) FROM price_history WHERE symbol=%s AND date < %s", (sym, d_str))
        n_before = cur.fetchone()[0]

        cur = _db.execute_sql(
            "SELECT COUNT(*) FROM price_history WHERE symbol=%s AND date > %s AND date <= %s",
            (sym, d_str, hold_end))
        n_after = cur.fetchone()[0]

        cur = _db.execute_sql("SELECT created_at, delisted_date FROM stocks WHERE symbol=%s", (sym,))
        srow = cur.fetchone()
        s_created, s_delisted = (srow if srow else (None, None))

        for label in ('22-now', '5y'):
            if (sym, d) in v_by_window[label]:
                all_v_rows.append({
                    'window': label, 'symbol': sym, 'date': d_str,
                    'overall': overall_by_window_key.get((label, (sym, d))),
                    'ph_exact_date_match': ph_exact, 'ph_close_on_date': ph_close,
                    'ph_row_count_total': n_total, 'ph_min_date': str(min_d), 'ph_max_date': str(max_d),
                    'ph_bars_before_60': n_before, 'ph_bars_after_within_hold': n_after,
                    'ph_pulled_at_max': str(pulled_max), 'stocks_created_at': str(s_created),
                    'stocks_delisted_date': str(s_delisted),
                })
        _tee(f"[V-DIAG] symbol={sym} date={d_str} ph_exact_match={ph_exact} ph_close={ph_close} "
            f"ph_total={n_total} ph_min={min_d} ph_max={max_d} bars_before={n_before} "
            f"bars_after_within_hold({hold_end})={n_after} pulled_at_max={pulled_max} "
            f"stocks_created_at={s_created} delisted_date={s_delisted}", log_path)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COMPUTE_V_FIELDS)
        w.writeheader()
        w.writerows(all_v_rows)
    _tee(f"[WRITE] {out_csv} ({len(all_v_rows)} rows)", log_path)
    return v_by_window


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--selftest', action='store_true', help='offline unit tests, no DB/monte_carlo dependency')
    p.add_argument('--stage', choices=['r1', 'r3', 'identity', 'twin', 'verify-drift', 'verify-mnst', 'reverse-diff', 'compute-u', 'compute-v'], default=None,
                   help='orchestrator stage to run (mutually exclusive with --cell-worker)')
    p.add_argument('--job', default=None, help='job name -- output files are out/flipR_<job>.csv etc')
    p.add_argument('--profiles', default=None, help='comma list, default core,apex,sentinel (--stage r1 only)')
    p.add_argument('--lenses', default=None, help='comma list, default calibrated,canon (--stage r1 only)')
    p.add_argument('--windows', default=None,
                   help='comma list, default the 12 canonical PHASE_D_WINDOWS_12 (--stage r1/r3 only)')
    p.add_argument('--n-iter', type=int, default=N_ITER_DEFAULT, help=f'MC iterations per cell (default {N_ITER_DEFAULT})')
    p.add_argument('--smoke-n', type=int, default=None, help='override --n-iter for a cheap smoke run')
    p.add_argument('--smoke-cells', default=None,
                   help="'profile,lens,window;...' explicit cell list for a --stage r1-shaped smoke run")
    # cell-worker (internal) args
    p.add_argument('--cell-worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--profile', default=None, help=argparse.SUPPRESS)
    p.add_argument('--lens', default=None, help=argparse.SUPPRESS)
    p.add_argument('--window', default=None, help=argparse.SUPPRESS)
    p.add_argument('--gross', type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument('--mp', type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument('--csv-schema', choices=['r1', 'r3'], default='r1', help=argparse.SUPPRESS)
    p.add_argument('--out-csv', default=None, help=argparse.SUPPRESS)
    p.add_argument('--log-path', default=None, help=argparse.SUPPRESS)
    return p.parse_args()


def main():
    args = parse_args()

    if args.selftest:
        raise SystemExit(selftest())

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    if args.cell_worker:
        if not (args.profile and args.lens and args.window and args.out_csv and args.log_path and args.job):
            raise SystemExit("--cell-worker requires --profile --lens --window --out-csv --log-path --job")
        csv_fields = CSV_FIELDS_R3 if args.csv_schema == 'r3' else CSV_FIELDS_R1
        run_one_cell(args.profile, args.lens, args.window, args.n_iter, args.out_csv, csv_fields,
                    args.log_path, args.job, gross_override=args.gross, mp_override=args.mp)
        return

    if args.stage is None:
        raise SystemExit("specify --stage {r1,r3,identity,twin,verify-drift,verify-mnst,reverse-diff,compute-u,compute-v} or --selftest")
    job = args.job or args.stage
    n_iter = args.smoke_n if args.smoke_n else args.n_iter
    windows = [w.strip() for w in args.windows.split(',')] if args.windows else list(PHASE_D_WINDOWS_12)

    if args.stage == 'twin':
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        result = run_twin_check(job, log_path, n_iter)
        raise SystemExit(0 if result['passed'] else 1)

    if args.stage == 'verify-drift':
        out_csv = os.path.join(OUT_DIR, f'flipR_{job}.csv')
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        run_verify_drift(job, log_path, out_csv)
        return

    if args.stage == 'verify-mnst':
        out_csv = os.path.join(OUT_DIR, f'flipR_{job}.csv')
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        run_verify_mnst_loader(job, log_path, out_csv)
        return

    if args.stage == 'reverse-diff':
        out_csv = os.path.join(OUT_DIR, f'flipR_{job}.csv')
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        run_reverse_diff(job, log_path, out_csv)
        return

    if args.stage == 'compute-u':
        out_csv = os.path.join(OUT_DIR, f'flipR_{job}.csv')
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        run_compute_u(job, log_path, out_csv)
        return

    if args.stage == 'compute-v':
        out_csv = os.path.join(OUT_DIR, f'flipR_{job}.csv')
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        run_compute_v(job, log_path, out_csv)
        return

    if args.stage == 'identity':
        cells = identity_cells()
        csv_fields = CSV_FIELDS_IDENTITY
    elif args.stage == 'r3':
        cells = r3_cells(windows)
        # r3_cells() yields (gross, mp, window) 3-tuples -- expand to this
        # orchestrator's uniform (profile, lens, window, gross, mp) 5-tuple
        # shape (R3 is always profile=core, lens=calibrated per PREREG section 5).
        cells = [('core', 'calibrated', window, gross, mp) for gross, mp, window in cells]
        csv_fields = CSV_FIELDS_R3
    else:   # r1
        if args.smoke_cells:
            cells = []
            for tok in args.smoke_cells.split(';'):
                tok = tok.strip()
                if not tok:
                    continue
                profile, lens, window = [x.strip() for x in tok.split(',')]
                cells.append((profile, lens, window))
        else:
            profiles = [p.strip() for p in args.profiles.split(',')] if args.profiles else list(PROFILES)
            lenses = [l.strip() for l in args.lenses.split(',')] if args.lenses else list(LENSES)
            cells = r1_cells(profiles, lenses, windows)
        csv_fields = CSV_FIELDS_R1

    out_csv, state_path, log_path = _paths_for_job(job)
    _run_orchestrator(job, args.stage, cells, csv_fields, n_iter, log_path, state_path, out_csv)


if __name__ == '__main__':
    main()
