#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
floorMC_run.py -- Liquidity-floor MC Stage-3 A/B runner (floor_mc_2026_08
PREREG.md, locked commit 5a9b4949; AMENDED by AMENDMENT-1, commit e379a4ca,
2026-08-11, pre-outcome/selection-neutral -- see PREREG.md's own
"AMENDMENT-1" section for the full rationale). Build step 2 (step 1 is
build_exclusions.py, which this file reads read-only:
experiments/floor_mc_2026_08/out/floor_exclusions.parquet).

======================================================================
AMENDMENT-1 -- WHAT CHANGED FROM THE ORIGINAL LOCKED SPEC
======================================================================
The original tripwire fired (STOP, no arm run) on ALL 5 originally-locked
windows under the strictest reading, and even the most charitable reading
still failed on `22-now`. Root-caused to two spec errors, both corrected
pre-outcome (no arm/portfolio outcome had been seen -- prepare-stage counts
and n_iter=5 smokes only):

1. POPULATION: the floor-filter scope (not just the tripwire denominator) is
   now 75+ PRIMARY-tier signal events ONLY. A 70-74 overflow-tier candidate
   is left COMPLETELY UNTOUCHED by the floor in EVERY arm (including
   A1/A2) -- it is never removed from ctx['call_outcomes']/ctx['calls_by_date']
   regardless of whether it would pass or fail the floor thresholds.
   Rationale (confirmed live 2026-08-11 smoke tests #456/#457): the overflow
   tier carries TIER_ALLOC['overflow']=0.0 in shipped Core (never receives
   capital -- see strategy_config.py:1284) AND is entirely outside
   ledger_v2's build scope (built SCORE_MIN=75-only,
   experiments/flatfile_exploitation/ff_signals.py:94) -- so under the
   original (untiered) filter scope, ~65-73% of what got measured/filtered
   was a population that never trades regardless. Worse: `_do_calls()`'s
   `overflow.sort(key=lambda x: (-x[1], rng.random()))` draws one
   `rng.random()` PER overflow candidate remaining in the list regardless of
   its eventual (always-zero) allocation -- so removing overflow candidates
   via the floor would have shifted downstream RNG draws for a population
   that is a pure spectator, confounding any A0-vs-A1/A2 comparison with an
   artifact unrelated to genuine floor economics. Primary-tier (75+)
   removals DO shift downstream draws too, but that shift IS the treatment
   being measured, not an artifact -- so primary-tier filtering stays as
   originally specified, only the overflow tier is now excluded from the
   filter's reach entirely.
2. WINDOWS: `22-now` DROPPED (its 2022-01..2022-08 head predates the
   archive's actual start of 2022-08-05 by construction -- the same defect
   `2022` was already excluded for pre-lock). Amended window set:
   **{2023, 2024, 2025, dip}**. Identity-arm window: **2024** (was 22-now).
3. TRIPWIRE NUMERATOR: clarified (not changed in effect from what this file
   already computed) -- any-status ledger_v2 row counts as "joined" (a
   skip-reason row IS a determination; only a signal with NO row at all is
   unknown/unverifiable). Combined with fix #1's 75+-only denominator, this
   is exactly this file's own pre-amendment `rate_any_75plus_pct` column,
   which already read 98.11/99.74/99.52/99.20 for 2023/2024/2025/dip
   (win/22-now excluded) -- PASS on all 4 remaining windows.

Everything else (arms, MISS_P/GAP_AWARE per arm, N=500, Core-as-shipped
config, no TP/SL sweep, subprocess-per-cell mechanics, injection point)
is UNCHANGED from the original spec below.

CRIBS (read-only, never edited, never copied):
  - experiments/tpsl_calib_primary_2026_08/driver/calibP_run.py -- the
    subprocess-per-cell / loader-cache / CSV-append / atomic-state /
    resumable pattern this file follows end to end.
  - experiments/tpsl_refine_2026_08/driver/mc_patch.py -- imported directly
    (apply_frozen_pins, apply_profile_env, resolve_and_pin_version,
    install_loader_cache, disable_puts, call_outcome_rates, pct,
    atomic_write_json). set_tpsl() is NOT used here -- this campaign runs
    Core AS SHIPPED, no TP/SL sweep (PREREG section 3 "no TP/SL overrides --
    current strategy_config values").

======================================================================
WHY THIS FILE SPAWNS AN OS-LEVEL SUBPROCESS PER (ARM, WINDOW) CELL
======================================================================
calibP_run.py/phaseD_run.py sweep TP/SL, which monte_carlo.py re-reads LIVE
via mc_patch.set_tpsl() -- a safe POST-import monkeypatch, so one long-lived
process can loop over many cells. This campaign instead sweeps
TP_FILL_MISS_P, which monte_carlo.py reads EXACTLY ONCE, as a plain
`float(os.environ.get('TP_FILL_MISS_P', '0.0'))` module global AT IMPORT TIME
(monte_carlo.py:417; TP_FILL_GAP_AWARE likewise at :100). Because Python
caches `sys.modules['monte_carlo']` after the first import, changing
os.environ['TP_FILL_MISS_P'] between arms inside one long-lived process would
be a SILENT NO-OP on every arm after the first -- exactly the trap PREREG
section 3 calls out ("MISS_P differs PER ARM -- env must be set in each
(arm, window) subprocess BEFORE monte_carlo is imported there, never
globally for the whole job. One subprocess per (arm, window); never share a
pool across arms").

So this script has TWO invocation shapes IN THE SAME FILE:
  1. ORCHESTRATOR (default): `--stage {tripwire,identity,main}`. Loops over
     that stage's fixed (arm, window) cell list, and for every cell not
     already in this job's state file, launches `python floorMC_run.py
     --cell-worker --arm ARM --window WINDOW ...` as a genuinely fresh OS
     process (subprocess.run, inherited env + per-arm overrides applied to a
     COPY of os.environ before the child ever starts -- never mutates the
     orchestrator's own os.environ, so the NEXT cell's child still starts
     from a clean base). Cells run STRICTLY SEQUENTIALLY (wait for each
     child before starting the next) -- this is a single `trader queue
     submit` task; parallel children would multiply DB load the `--db light`
     admission didn't reserve, and 15-25 cells at ~20-25s each is already
     "well under 1 hour" run serially (PREREG section 7).
  2. CELL-WORKER (`--cell-worker`, internal -- not meant to be invoked by a
     human): does the actual env-set -> import monte_carlo -> prepare ->
     floor-filter -> simulate -> one CSV row, for EXACTLY one (arm, window).
     Every side-effecting statement here is reached only via main(), guarded
     by `if __name__ == '__main__':` at the bottom (Windows spawn note
     below).

TRIPWIRE (`--stage tripwire`) is neither of the above -- it never calls
_simulate_window (no N_ITER cost, no fill-realism knobs matter), only
_prepare_window per window, so there's no import-time env axis to isolate
across windows; it runs single-process, looping windows in-process.

Windows/multiprocessing note (same as phaseD_run.py/calibP_run.py):
monte_carlo._simulate_window builds its OWN multiprocessing.Pool per call --
on Windows (spawn) every pool worker re-imports THIS FILE as a non-`__main__`
module. Everything with side effects (arg parsing, DB access, subprocess
spawning, the cell loop) MUST stay inside a function, reached only through
`if __name__ == '__main__':` -- never at module level.

======================================================================
THE FLOOR INJECTION POINT
======================================================================
Verified against monte_carlo.py source, 2026-08-11: `ctx = mc._prepare_window
(...)` returns a dict including 'call_outcomes' ({(symbol,date): outcome})
and 'calls_by_date' ({date: [(symbol, overall, (symbol,date), ct, ern), ...]}).
EVERY eligibility check in run_single_sim that decides whether a candidate
can open a position gates through `key in call_outcomes` (verified: lines
~3271-3277 call_pressure/put_pressure, ~3571 _do_calls eligible=, ~3615/3645
PUT_PRIORITY=merged/wr_merged branches -- all read `k in call_outcomes`,
never calls_by_date membership alone). So filtering ctx['call_outcomes'] to
only the keys that PASS a given arm's floor is sufficient and complete;
ctx['calls_by_date'] is ALSO filtered here for internal consistency
(harmless -- not required for correctness, see _apply_floor docstring).

This filtering happens AFTER `_prepare_window` returns (barriers are already
baked -- the floor never perturbs HOW a kept signal's outcome was computed,
only WHICH signals remain tradeable) and BEFORE `_simulate_window` is called
on that same ctx. Every ctx is fresh per (arm, window) cell (a brand new
_prepare_window() call, never reused across cells) so there is no
stale-filter carryover risk.

KEY DTYPE (verified live 2026-08-11): monte_carlo.py's `sig.symbol_id` is a
Python `str` (the ticker) -- NOT a numeric id. Stock.symbol is Stock's
CharField primary key, and Score.symbol is a DeferredForeignKey onto it, so
peewee's `.symbol_id` FK-id accessor already IS the ticker string (the classic
"peewee FK-per-row trap" from this repo's memory notes, here confirmed to
mean the join key is free -- no Stock lookup table needed). `sig.date` is a
native `datetime.date`. polars' `.to_list()` on a `pl.Date` column also
yields native `datetime.date` (verified live) -- so
`(row['ticker'], row['entry_date'])` tuples built from floor_exclusions.parquet
compare equal to monte_carlo's own `(symbol_id, date)` keys with NO cast.

======================================================================
ARMS (PREREG section 3, LOCKED; windows/identity-window AMENDED by
AMENDMENT-1 -- see top-of-file note)
======================================================================
  A0       : no floor.                          TP_FILL_MISS_P=0.15  GAP_AWARE=1
  A1       : floor {premium>=0.25, volume>=5}.   TP_FILL_MISS_P=0.125 GAP_AWARE=1
  A2       : floor {premium>=0.50, volume>=10}.  TP_FILL_MISS_P=0.11  GAP_AWARE=1
  IDENTITY : all-pass floor (thresholds 0/0, ledger-missing signals INCLUDED),
             MISS_P=0.15, GAP_AWARE=1 -- must reproduce A0 bit-exactly on
             window 2024 (AMENDMENT-1 point 2; was 22-now pre-amendment). Its
             pass-set is the union of (every key in floor_exclusions.parquet)
             and (every key actually in that cell's own ctx) -- the union
             with the live ctx keyset is what makes "ledger-missing signals
             INCLUDED" true BY CONSTRUCTION rather than by hoping
             floor_exclusions.parquet happens to have full coverage.

ALL arms (including A1/A2/IDENTITY): the floor filter -- whatever its
pass-set -- only ever REMOVES a 75+ PRIMARY-tier candidate (AMENDMENT-1
point 1). A 70-74 overflow-tier candidate is unconditionally kept in
ctx['call_outcomes']/ctx['calls_by_date'] in every arm, never consulted
against any pass-set at all (see apply_floor()'s tier gate).

Core profile as shipped: no TP/SL override (verified via assertion against
mc.TP_BASE/mc.SL_BASE after import), no cascade/MaxPos/exposure override --
mc_patch.apply_profile_env('core') is a documented no-op beyond the frozen
pins, so strategy_config.STRATEGY_30DTE's own live defaults apply untouched.

Locked spec: experiments/floor_mc_2026_08/PREREG.md (commit 5a9b4949; DO NOT
modify that file). HARD RULE (mirrors calibP_run.py verbatim): this file
NEVER edits monte_carlo.py / strategy_config.py / any tracked production
file, and NEVER edits anything under tpsl_refine_2026_08/,
tpsl_calib_primary_2026_08/, alloc_retune_2026_08/, liquidity_floor_2026_08/
(their driver modules are imported/read READ-ONLY only). This script never
git-commits anything.

Usage
-----
    python experiments/floor_mc_2026_08/driver/floorMC_run.py --selftest

    python experiments/floor_mc_2026_08/driver/floorMC_run.py \\
        --stage tripwire --job tripwire

    python experiments/floor_mc_2026_08/driver/floorMC_run.py \\
        --stage identity --job identity [--n-iter 500]

    python experiments/floor_mc_2026_08/driver/floorMC_run.py \\
        --stage main --job main [--arms A0,A1,A2] \\
        [--windows 2023,2024,2025,dip] [--n-iter 500] [--smoke-n N]

    python experiments/floor_mc_2026_08/driver/floorMC_run.py \\
        --stage survivor --job survivor [--n-iter 500]
        # fixed grid: SURVIVOR_ARMS (A0,A2) x LOCKED_WINDOWS, DELISTED-EXCLUDED
        # universe (MC_UNIVERSE_FILE=survivor_universe_811.txt) on every cell.

    python experiments/floor_mc_2026_08/driver/floorMC_run.py \\
        --stage neighborhood --job neighborhood [--n-iter 500]
        # fixed grid: NEIGHBORHOOD_ARM (A2) x NEIGHBORHOOD_WINDOWS x
        # NEIGHBORHOOD_TPSL_CELLS -- TP/SL injected per cell via mc_patch.set_tpsl.

Console output is ASCII-only throughout (no em-dash/smart-quote/unicode
arrows) per this repo's convention.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import redirect_stdout

# --- repo-root + cross-experiment bootstrap -- explicit + asserted, never
# inferred from CWD (traps.md "Worktree PYTHONPATH trap"). Safe to re-run
# (idempotent) inside spawned cell-worker subprocesses. -----------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../floor_mc_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                                        # .../floor_mc_2026_08
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
    f"tpsl driver phaseD_run.py not found at {_TPSL_DRIVER_DIR!r} -- the SURVIVOR GUARD "
    f"requires reusing its SURVIVOR_FILE path + _load_survivor_set loader read-only "
    f"(same import calibP_run.py already established the precedent for)"
)

# phaseD_run.py -- READ-ONLY reuse (never edit that file): the survivor-file
# path + set loader, imported the same way calibP_run.py already does from
# this sibling driver module. Importing phaseD_run triggers zero side effects
# at import time (its own DB/mc-touching code lives inside main(), guarded by
# `if __name__ == '__main__':`).
from phaseD_run import SURVIVOR_FILE, _load_survivor_set                     # noqa: E402

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TPSL_META_PATH = os.path.join(_TPSL_DRIVER_DIR, 'state', 'meta.json')   # REUSED, never written by this file
EXCLUSIONS_PATH = os.path.join(OUT_DIR, 'floor_exclusions.parquet')     # built by build_exclusions.py

# ---------------------------------------------------------------------------
# LOCKED constants -- PREREG.md section 3, windows AMENDED by AMENDMENT-1
# point 2 (22-now dropped: its 2022-01..2022-08 head predates the archive).
# ---------------------------------------------------------------------------
LOCKED_WINDOWS = ['2023', '2024', '2025', 'dip']
IDENTITY_WINDOW = '2024'   # AMENDMENT-1 point 2 (was 22-now)
LOCKED_ARMS = ['A0', 'A1', 'A2']

# ---------------------------------------------------------------------------
# Coordinator follow-up (2026-08-11, post-adjudication): SURVIVOR GUARD +
# TP/SL NEIGHBORHOOD, both scoped to arm A2 (the only arm that passed
# LANE-DD). Guard/neighborhood mechanics per PREREG sections 4-5, windows per
# AMENDMENT-1 point 2 ({2024, dip} replaces {22-now, 2024}).
# ---------------------------------------------------------------------------
SURVIVOR_ARMS = ['A0', 'A2']                    # coordinator directive -- both cells of the A2-vs-A0 contrast
NEIGHBORHOOD_ARM = 'A2'                         # coordinator directive -- winning arm's floor + env only
NEIGHBORHOOD_WINDOWS = ['2024', 'dip']          # PREREG section 5, AMENDMENT-1 point 2
NEIGHBORHOOD_TPSL_CELLS = [(0.10, -1.00), (0.10, -0.90), (0.10, -0.80), (0.075, -0.90)]   # PREREG section 5

ARM_FILL_ENV = {
    'A0':       {'TP_FILL_MISS_P': '0.15',  'TP_FILL_GAP_AWARE': '1'},
    'A1':       {'TP_FILL_MISS_P': '0.125', 'TP_FILL_GAP_AWARE': '1'},
    'A2':       {'TP_FILL_MISS_P': '0.11',  'TP_FILL_GAP_AWARE': '1'},
    'IDENTITY': {'TP_FILL_MISS_P': '0.15',  'TP_FILL_GAP_AWARE': '1'},   # same knobs as A0 (PREREG identity spec)
}
ARM_FILL_MISS_P_EXPECTED = {a: float(v['TP_FILL_MISS_P']) for a, v in ARM_FILL_ENV.items()}

N_ITER_DEFAULT = 500   # PREREG section 3

# phaseD-style schema (experiments/tpsl_refine_2026_08/driver/phaseD_run.py
# CSV_FIELDS, verbatim) PLUS 'arm' and 'n_signals_after_filter' (build brief
# section "Build" item 2).
#
# AMENDMENT-1 changes what these two count-columns MEAN (not their names):
#   n_call_signals         = len(ctx['call_outcomes']) AFTER filtering, i.e.
#                             the TOTAL tradeable-candidate population fed to
#                             _simulate_window -- 75+ survivors PLUS every
#                             70-74 overflow candidate (always present,
#                             AMENDMENT-1 point 1). This is what
#                             mc_patch.call_outcome_rates(ctx) actually counts.
#   n_signals_after_filter = the 75+ PRIMARY-tier survivor count ONLY (per
#                             AMENDMENT-1's explicit "report n_signals_after_
#                             filter ... computed over the 75+ primary tier
#                             only" instruction) -- the number the floor's
#                             REAL effect on tradeable supply is measured
#                             from, since the untouched overflow tier would
#                             otherwise mask/dilute the true primary-tier cut.
# So the two columns now legitimately diverge (n_call_signals >
# n_signals_after_filter whenever any overflow candidates exist), unlike the
# pre-amendment runner where they were always equal. n_calls_delisted is None
# for every non-survivor cell (identity/main/neighborhood -- no survivor
# universe file loaded, matching phaseD_run.py's own convention of only
# populating it when the diagnostic is meaningful); populated for real
# (distinct call-signal symbols NOT in SURVIVOR_FILE, expected ~0 -- the
# universe filter already excludes them) by --survivor cells (SURVIVOR GUARD
# follow-up, coordinator directive 2026-08-11).
CSV_FIELDS = [
    'phase', 'mode', 'profile', 'window', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'p10_ret', 'p90_ret', 'worst_dd', 'mean_dd',
    'p_coll', 'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s',
    'realized_call_tp_pct', 'n_calls_delisted',
    'arm', 'n_signals_after_filter',
]

TRIPWIRE_CSV_FIELDS = [
    'window', 'n_events_all70', 'n_joined_any_all70', 'rate_any_all70_pct',
    'n_joined_kept_all70', 'rate_kept_all70_pct',
    'n_events_75plus', 'n_joined_any_75plus', 'rate_any_75plus_pct',
    'n_joined_kept_75plus', 'rate_kept_75plus_pct',
    'pass_90pct_primary',
]
TRIPWIRE_MISS_FIELDS = ['window', 'ticker', 'entry_date', 'overall']
TRIPWIRE_PRIMARY_THRESHOLD_PCT = 90.0   # PREREG section 3 TRIPWIRE; AMENDMENT-1 gates on (75+ primary-tier denom x any-status numerator) -- see _run_tripwire()


# ---------------------------------------------------------------------------
# tiny utilities (deliberately NOT imported from phaseD_run.py, unlike
# SURVIVOR_FILE/_load_survivor_set above -- this campaign has no canon-mode/
# 12-window-canonical-set need, so pulling in phaseD's fill_probe/canon
# machinery would be unused surface area; these three functions are trivial
# to keep local).
# ---------------------------------------------------------------------------
def _load_json(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def _tee(msg, log_path):
    """Print to real stdout (visible live via `trader queue logs`) AND
    append to log_path, opening/closing it fresh each call (never held open
    across a subprocess launch -- Windows cross-process file access safety,
    see module docstring)."""
    print(msg, flush=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def _paths_for_job(job):
    return (
        os.path.join(OUT_DIR, f'floorMC_{job}.csv'),
        os.path.join(STATE_DIR, f'floorMC_{job}.json'),
        os.path.join(LOG_DIR, f'{job}.log'),
    )


# ---------------------------------------------------------------------------
# Floor pass-set construction + ctx filtering -- pure, unit-testable (see
# --selftest), no monte_carlo/MySQL dependency of their own (caller passes
# in an already-loaded floor_df and/or ctx-like dict).
# ---------------------------------------------------------------------------
def pass_set_for_arm(arm, floor_df, ctx_keys=()):
    """Returns a python set of (ticker, entry_date) keys that PASS this arm's
    floor, or None for 'A0' (caller must skip filtering entirely for A0 --
    this function is never called with arm='A0' by the real driver, only by
    --selftest, where it documents the "no filtering" contract).

    floor_df: a polars DataFrame with columns ticker/entry_date/in_ledger/
    pass_A1/pass_A2 (the exact schema build_exclusions.py writes).

    ctx_keys: iterable of (ticker, entry_date) keys currently in the cell's
    ctx['call_outcomes'] -- ONLY consulted for arm='IDENTITY' (union safety
    net so "ledger-missing signals INCLUDED" holds even for a key entirely
    absent from floor_df, e.g. a 70-74 overflow-tier signal outside
    ledger_v2's SCORE_MIN=75 build scope -- see module docstring).
    """
    if arm == 'A0':
        return None
    if arm == 'A1':
        keep = floor_df.filter(floor_df['pass_A1'])
    elif arm == 'A2':
        keep = floor_df.filter(floor_df['pass_A2'])
    elif arm == 'IDENTITY':
        keep = floor_df   # thresholds 0/0 + ledger-missing INCLUDED = every row passes
    else:
        raise ValueError(f"unknown arm {arm!r} (expected A0/A1/A2/IDENTITY)")
    pass_set = set(zip(keep['ticker'].to_list(), keep['entry_date'].to_list()))
    if arm == 'IDENTITY':
        pass_set |= set(ctx_keys)
    return pass_set


PRIMARY_THRESHOLD_SCORE = 75   # AMENDMENT-1 point 1 -- "score >= 75"; matches monte_carlo.PRIMARY_THRESHOLD


def overall_map_from_ctx(ctx):
    """{(symbol,date) key -> overall score}, recovered from
    ctx['calls_by_date'] entries (symbol_id, overall, key, ct, ern) 5-tuples
    (monte_carlo.py:4293) -- index [1] is `overall`, index [2] is `key`. Every
    key in ctx['call_outcomes'] has a corresponding calls_by_date entry (both
    are built from the same call_sigs list in _prepare_window), so this map
    fully covers apply_floor()'s tier-gate lookup."""
    overall_by_key = {}
    for _d, entries in ctx['calls_by_date'].items():
        for _sid, overall, key, _ct, _ern in entries:
            overall_by_key[key] = overall
    return overall_by_key


def apply_floor(ctx, pass_set, overall_by_key):
    """Filter ctx['call_outcomes'] and ctx['calls_by_date'] IN PLACE (ctx is
    always a freshly-returned per-cell dict here, never shared/reused across
    cells -- see module docstring).

    AMENDMENT-1 point 1 (tier gate): the floor filter's reach is 75+
    PRIMARY-tier candidates ONLY. A key whose overall_by_key score is < 75
    (the 70-74 overflow tier) is ALWAYS kept, in EVERY arm, regardless of
    pass_set membership -- it is never even consulted against pass_set.
    Removing an overflow candidate would shift `_do_calls()`'s
    `overflow.sort(key=lambda x: (-x[1], rng.random()))` RNG tiebreaker draws
    for a population that carries TIER_ALLOC['overflow']=0.0 in shipped Core
    (never receives capital) -- a pure artifact, not the treatment.

    pass_set=None means "no floor at all" (A0) -- ctx returned unchanged
    (still tier-classified for the returned counts, no filtering applied).

    call_outcomes is the load-bearing structure (every eligibility check in
    run_single_sim gates via `key in call_outcomes`, verified against
    monte_carlo.py source 2026-08-11); calls_by_date is ALSO filtered for
    internal ctx consistency, not because anything reads its membership
    directly.

    Returns (ctx, n_75plus_before, n_75plus_after, n_overflow). n_overflow is
    the same before and after by construction (asserted) -- it is the
    untouched-population count, kept in the return tuple as a cheap
    self-check callers can log/report.
    """
    def _is_primary(k):
        return overall_by_key.get(k, 0) >= PRIMARY_THRESHOLD_SCORE

    n_75plus_before = sum(1 for k in ctx['call_outcomes'] if _is_primary(k))
    n_overflow = len(ctx['call_outcomes']) - n_75plus_before

    if pass_set is None:
        return ctx, n_75plus_before, n_75plus_before, n_overflow

    def _keep(k):
        return (not _is_primary(k)) or (k in pass_set)   # overflow always kept; primary gated on pass_set

    ctx['call_outcomes'] = {k: v for k, v in ctx['call_outcomes'].items() if _keep(k)}
    new_cbd = defaultdict(list)
    for d, entries in ctx['calls_by_date'].items():
        kept_entries = [e for e in entries if _keep(e[2])]
        if kept_entries:
            new_cbd[d] = kept_entries
    ctx['calls_by_date'] = new_cbd

    n_75plus_after = sum(1 for k in ctx['call_outcomes'] if _is_primary(k))
    n_overflow_after = len(ctx['call_outcomes']) - n_75plus_after
    assert n_overflow_after == n_overflow, (
        f"AMENDMENT-1 violation: overflow-tier count changed from {n_overflow} to "
        f"{n_overflow_after} -- the floor must never touch 70-74 candidates")
    return ctx, n_75plus_before, n_75plus_after, n_overflow


def _load_floor_df():
    import polars as pl
    if not os.path.isfile(EXCLUSIONS_PATH):
        raise SystemExit(f"[STOP] {EXCLUSIONS_PATH!r} missing -- run "
                         f"build_exclusions.py first, do not improvise a substitute")
    return pl.read_parquet(EXCLUSIONS_PATH)


# ---------------------------------------------------------------------------
# --selftest -- pure logic checks, NO monte_carlo import, NO MySQL, NO
# B:/ dependency (synthetic frames only). Mirrors the repo convention
# (build_ledger_v2.py --selftest) of validating the pure/testable core before
# spending any queue time on the real (DB + MC) path.
# ---------------------------------------------------------------------------
def selftest() -> int:
    import polars as pl
    from datetime import date

    log = print
    log("=== floorMC_run.py OFFLINE SELF-TESTS ===")

    floor_df = pl.DataFrame({
        'ticker':     ['AAA', 'BBB', 'CCC', 'DDD'],
        'entry_date': [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
        'in_ledger':  [True,  True,  False, True],
        'pass_A1':    [True,  False, False, True],
        'pass_A2':    [True,  False, False, False],
    })

    # -- 1. A0 -> None (no filtering contract) --------------------------------
    assert pass_set_for_arm('A0', floor_df) is None
    log("  [1] A0 pass_set is None (no filtering) OK")

    # -- 2. A1/A2 pass-sets match the precomputed boolean columns exactly ----
    a1 = pass_set_for_arm('A1', floor_df)
    assert a1 == {('AAA', date(2024, 1, 2)), ('DDD', date(2024, 1, 5))}, a1
    a2 = pass_set_for_arm('A2', floor_df)
    assert a2 == {('AAA', date(2024, 1, 2))}, a2
    log("  [2] A1/A2 pass-sets match precomputed pass_A1/pass_A2 columns OK")

    # -- 3. IDENTITY: every floor_df row passes, PLUS union with ctx_keys for
    #    a key entirely ABSENT from floor_df (the 70-74-overflow-tier case) --
    ctx_keys = {('AAA', date(2024, 1, 2)), ('ZZZ', date(2024, 1, 9))}   # ZZZ not in floor_df at all
    ident = pass_set_for_arm('IDENTITY', floor_df, ctx_keys=ctx_keys)
    assert ident == {('AAA', date(2024, 1, 2)), ('BBB', date(2024, 1, 3)),
                     ('CCC', date(2024, 1, 4)), ('DDD', date(2024, 1, 5)),
                     ('ZZZ', date(2024, 1, 9))}, ident
    log("  [3] IDENTITY pass-set = all floor_df rows UNION live ctx keys "
        "(absent-key auto-pass) OK")

    # fake_ctx used by tests 4-8: a REALISTIC mix -- 3 primary-tier (75+) keys
    # (AAA passes both A1/A2 in floor_df; BBB is in floor_df but fails both;
    # ZZZ is a 75+ signal entirely ABSENT from floor_df, e.g. an
    # unbuildable-chain gap) PLUS one 70-74 OVERFLOW-tier key (OVF, score 72)
    # that -- realistically, matching live ledger_v2 -- has NO floor_df row
    # at all (ledger_v2 is SCORE_MIN=75-only, AMENDMENT-1 point 1).
    fake_ctx = {
        'call_outcomes': {('AAA', date(2024, 1, 2)): {'kind': 'tp'},
                          ('BBB', date(2024, 1, 3)): {'kind': 'sl'},
                          ('ZZZ', date(2024, 1, 9)): {'kind': 'hard'},
                          ('OVF', date(2024, 1, 10)): {'kind': 'tp'}},
        'calls_by_date': defaultdict(list, {
            date(2024, 1, 2): [('AAA', 80, ('AAA', date(2024, 1, 2)), None, False)],
            date(2024, 1, 3): [('BBB', 78, ('BBB', date(2024, 1, 3)), None, False)],
            date(2024, 1, 9): [('ZZZ', 76, ('ZZZ', date(2024, 1, 9)), None, False)],
            date(2024, 1, 10): [('OVF', 72, ('OVF', date(2024, 1, 10)), None, False)],
        }),
    }
    import copy
    overall_by_key = overall_map_from_ctx(fake_ctx)
    assert overall_by_key == {('AAA', date(2024, 1, 2)): 80, ('BBB', date(2024, 1, 3)): 78,
                              ('ZZZ', date(2024, 1, 9)): 76, ('OVF', date(2024, 1, 10)): 72}, overall_by_key
    log("  [4] overall_map_from_ctx recovers overall scores from calls_by_date OK")

    # -- 5. apply_floor: A0 (pass_set=None) is a true no-op, preserves dict
    #    insertion order (RNG-order safety), tier counts correct -------------
    ctx0 = copy.deepcopy(fake_ctx)
    out_ctx, n_75_before, n_75_after, n_ovf = apply_floor(ctx0, None, overall_by_key)
    assert n_75_before == n_75_after == 3 and n_ovf == 1, (n_75_before, n_75_after, n_ovf)
    assert list(out_ctx['call_outcomes'].keys()) == list(fake_ctx['call_outcomes'].keys()), \
        "A0 no-op must preserve dict insertion order exactly (RNG-order safety)"
    log("  [5] apply_floor(ctx, None, ...) is a true no-op, order-preserving, "
        "tier counts (3 primary / 1 overflow) correct OK")

    # -- 6. apply_floor: IDENTITY pass_set (built via step 3's pattern)
    #    reproduces the UNFILTERED ctx bit-exactly (the actual identity-check
    #    property), including for the overflow key even though it's absent
    #    from floor_df (covered by the tier gate AND the union safety net) --
    ctx1 = copy.deepcopy(fake_ctx)
    ident_full = pass_set_for_arm('IDENTITY', floor_df, ctx_keys=set(ctx1['call_outcomes'].keys()))
    out_ctx1, n_75_b1, n_75_a1, n_ovf1 = apply_floor(ctx1, ident_full, overall_by_key)
    assert n_75_b1 == n_75_a1 == 3 and n_ovf1 == 1, (n_75_b1, n_75_a1, n_ovf1)
    assert out_ctx1['call_outcomes'] == fake_ctx['call_outcomes']
    assert dict(out_ctx1['calls_by_date']) == dict(fake_ctx['calls_by_date'])
    log("  [6] apply_floor with a real IDENTITY pass-set reproduces the "
        "unfiltered ctx bit-exactly (call_outcomes AND calls_by_date, incl. "
        "the overflow key) OK")

    # -- 7. apply_floor: a real (A1-like) restrictive filter drops the right
    #    PRIMARY-tier keys from BOTH call_outcomes and calls_by_date, keeps
    #    survivors intact ---------------------------------------------------
    ctx2 = copy.deepcopy(fake_ctx)
    small_pass = {('AAA', date(2024, 1, 2))}   # only AAA passes
    out_ctx2, n_75_b2, n_75_a2, n_ovf2 = apply_floor(ctx2, small_pass, overall_by_key)
    assert n_75_b2 == 3 and n_75_a2 == 1 and n_ovf2 == 1, (n_75_b2, n_75_a2, n_ovf2)
    assert set(out_ctx2['calls_by_date'].keys()) == {date(2024, 1, 2), date(2024, 1, 10)}
    assert out_ctx2['calls_by_date'][date(2024, 1, 2)] == fake_ctx['calls_by_date'][date(2024, 1, 2)]
    log("  [7] apply_floor with a restrictive primary-tier pass-set drops "
        "excluded PRIMARY keys from call_outcomes AND calls_by_date, keeps "
        "survivors intact OK")

    # -- 8. AMENDMENT-1's explicitly-required check: an overflow-tier (70-74)
    #    candidate survives REAL A2 filtering even though (realistically,
    #    matching live ledger_v2) it has NO floor_df row at all -- i.e. it
    #    would FAIL the floor outright if it were subject to it (the
    #    coverage rule's "no ledger row -> FAIL" clause), but AMENDMENT-1
    #    exempts the overflow tier from the floor's reach entirely ----------
    ctx3 = copy.deepcopy(fake_ctx)
    a2_pass = pass_set_for_arm('A2', floor_df)   # real production code path, NOT hand-built
    assert ('OVF', date(2024, 1, 10)) not in a2_pass, \
        "test setup error: OVF must be absent from floor_df (realistic -- ledger_v2 is 75+-only)"
    out_ctx3, n_75_b3, n_75_a3, n_ovf3 = apply_floor(ctx3, a2_pass, overall_by_key)
    assert ('OVF', date(2024, 1, 10)) in out_ctx3['call_outcomes'], \
        "AMENDMENT-1 VIOLATION: overflow-tier candidate OVF did not survive A2 filtering"
    assert n_ovf3 == 1, n_ovf3   # overflow count unchanged by the A2 filter
    assert ('BBB', date(2024, 1, 3)) not in out_ctx3['call_outcomes'], \
        "primary-tier BBB (fails A2 in floor_df) should still be DROPPED -- tier gate must not over-protect"
    log("  [8] AMENDMENT-1 check: an overflow-tier candidate with NO ledger_v2 "
        "row survives A2 filtering (primary-tier candidates in the same cell "
        "still correctly drop) OK")

    # -- 9. cell list builders are exactly the AMENDED locked grids ----------
    ident_cells = identity_cells()
    assert ident_cells == [('A0', '2024'), ('IDENTITY', '2024')], ident_cells
    main_cells = main_cells_grid(LOCKED_ARMS, LOCKED_WINDOWS)
    assert len(main_cells) == 12, len(main_cells)   # 3 arms x 4 windows (AMENDMENT-1: 22-now dropped)
    assert main_cells[0] == ('A0', '2023') and main_cells[-1] == ('A2', 'dip'), main_cells
    assert len(set(main_cells)) == 12, "duplicate cells in the locked grid"
    assert '22-now' not in LOCKED_WINDOWS, "AMENDMENT-1 point 2: 22-now must be dropped"
    log("  [9] identity_cells()/main_cells_grid() produce the AMENDED locked "
        "2-cell (window=2024) and 12-cell (4 windows) grids, no duplicates, "
        "22-now absent OK")

    # -- 10. coordinator follow-up grids: SURVIVOR GUARD (8 cells) + TP/SL
    #    NEIGHBORHOOD (8 cells) ------------------------------------------------
    surv_cells = survivor_cells()
    assert len(surv_cells) == 8, len(surv_cells)
    assert set(a for a, w in surv_cells) == {'A0', 'A2'}, "survivor guard must be A0 and A2 only, never A1"
    assert set(w for a, w in surv_cells) == set(LOCKED_WINDOWS), surv_cells
    assert len(set(surv_cells)) == 8, "duplicate cells in the survivor grid"

    nbhd_cells = neighborhood_cells()
    assert len(nbhd_cells) == 8, len(nbhd_cells)   # 4 tp/sl cells x 2 windows
    assert all(arm == 'A2' for arm, *_ in nbhd_cells), "neighborhood check must be A2 only"
    assert set(w for _, w, _, _ in nbhd_cells) == {'2024', 'dip'}, nbhd_cells
    assert set((tp, sl) for _, _, tp, sl in nbhd_cells) == set(NEIGHBORHOOD_TPSL_CELLS), nbhd_cells
    assert (0.10, -1.00) in [(tp, sl) for _, _, tp, sl in nbhd_cells], \
        "the shipped-Core anchor cell (0.10,-1.00) must be a neighborhood member"
    assert len(set(nbhd_cells)) == 8, "duplicate cells in the neighborhood grid"
    log("  [10] survivor_cells() (A0+A2 x 4 windows = 8) and neighborhood_cells() "
        "(A2 x 2 windows x 4 tp/sl cells = 8) match the coordinator's directive, "
        "no duplicates OK")

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# Cell-list builders (pure).
# ---------------------------------------------------------------------------
def identity_cells():
    return [('A0', IDENTITY_WINDOW), ('IDENTITY', IDENTITY_WINDOW)]   # AMENDMENT-1 point 2: window=2024


def main_cells_grid(arms, windows):
    return [(arm, window) for arm in arms for window in windows]


def survivor_cells():
    """SURVIVOR GUARD (coordinator directive): A0 and A2, the 4 locked
    windows -- 2 x 4 = 8 (arm, window) cells, same shape as main_cells_grid.
    Distinguished from a normal main-battery cell only by the `survivor=True`
    flag the orchestrator passes to each cell-worker (MC_UNIVERSE_FILE
    engagement), not by cell shape."""
    return main_cells_grid(SURVIVOR_ARMS, LOCKED_WINDOWS)


def neighborhood_cells():
    """TP/SL NEIGHBORHOOD (coordinator directive): arm A2 floor + A2 env,
    fixed, crossed with 4 (tp, sl) cells x 2 windows = 8 (arm, window, tp, sl)
    4-tuples. PREREG section 5 cells, AMENDMENT-1 point 2 windows."""
    return [(NEIGHBORHOOD_ARM, window, tp, sl)
            for window in NEIGHBORHOOD_WINDOWS for tp, sl in NEIGHBORHOOD_TPSL_CELLS]


# ---------------------------------------------------------------------------
# CELL-WORKER -- runs exactly ONE (arm, window[, tp, sl]) cell in a fresh
# process, optionally under the survivor-restricted universe.
# Every side-effecting line lives here or in functions it calls; only
# invoked from main() under `if __name__ == '__main__':`.
# ---------------------------------------------------------------------------
def run_one_cell(arm, window, n_iter, out_csv, log_path, job, survivor=False, tp=None, sl=None):
    if arm not in ARM_FILL_ENV:
        raise SystemExit(f"[STOP] unknown arm {arm!r} (expected one of {sorted(ARM_FILL_ENV)})")
    if (tp is None) != (sl is None):
        raise SystemExit(f"[STOP] --tp/--sl must be given together or not at all (got tp={tp!r} sl={sl!r})")

    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {TPSL_META_PATH} missing. This campaign pins ALGORITHM_VERSION to the SAME "
            f"id the tpsl_refine_2026_08 campaign resolved (id=74, git_commit=f9fb7b934 as of "
            f"2026-08-10) -- required so every arm/window in this battery scores against the "
            f"identical version. Refusing to auto-re-resolve (that would WRITE into "
            f"tpsl_refine_2026_08/driver/state/, violating the no-edit rule). Report to "
            f"orchestrator rather than improvising.")

    # Loaded BEFORE any env/import step so a missing file fails fast, before
    # any compute is spent -- only needed for survivor cells (SURVIVOR GUARD).
    survivor_set = _load_survivor_set(SURVIVOR_FILE) if survivor else None

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    # 1) env BEFORE import.
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env('core')
    for k, v in ARM_FILL_ENV[arm].items():
        os.environ[k] = v
    if survivor:
        if not os.path.isfile(SURVIVOR_FILE):
            raise SystemExit(f"[STOP] --survivor: {SURVIVOR_FILE!r} does not exist -- STOP, "
                             f"do not improvise a substitute universe file")
        os.environ['MC_UNIVERSE_FILE'] = SURVIVOR_FILE
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    # 2) NOW import monte_carlo.
    import monte_carlo as mc

    # 3) post-import patches.
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)   # Core is calls-only (PUT_TIER_ALLOC all 0.0) -- matches every sibling campaign

    # 3b) TP/SL NEIGHBORHOOD injection ONLY -- same mechanism calibP_run.py
    # uses (mc_patch.set_tpsl, a POST-import monkeypatch of the mc module
    # object), NOT the TP_BASE_OV/SL_BASE_OV pre-import env route (that route
    # exists in monte_carlo.py but calibP_run.py/phaseD_run.py both actually
    # use set_tpsl -- verified against their source). MUST run before
    # _prepare_window: barriers bake there.
    if tp is not None:
        mc_patch.set_tpsl(mc, tp, sl)   # stress=base (flat), same as calibP_run.py's own flat verification

    # 4) hard verification -- fail fast rather than silently running a wrong config.
    assert abs(mc.GROSS_PREMIUM_CAP - 0.50) < 1e-9 and abs(mc.CALL_PREMIUM_CAP - 0.50) < 1e-9, \
        f"Core GROSS/CALL_PREMIUM_CAP != shipped 0.50 default (got {mc.GROSS_PREMIUM_CAP}/{mc.CALL_PREMIUM_CAP})"
    if tp is None:
        assert abs(mc.TP_BASE - 0.10) < 1e-9, \
            f"mc.TP_BASE={mc.TP_BASE} != 0.10 -- PREREG requires Core AS SHIPPED when no --tp given"
        assert abs(mc.SL_BASE - (-1.00)) < 1e-9, \
            f"mc.SL_BASE={mc.SL_BASE} != -1.00 -- PREREG requires Core AS SHIPPED when no --sl given"
    else:
        assert abs(mc.TP_BASE - tp) < 1e-9, \
            f"mc.TP_BASE={mc.TP_BASE} != injected tp={tp} -- set_tpsl propagation FAILED"
        assert abs(mc.SL_BASE - sl) < 1e-9, \
            f"mc.SL_BASE={mc.SL_BASE} != injected sl={sl} -- set_tpsl propagation FAILED"
    expected_miss_p = ARM_FILL_MISS_P_EXPECTED[arm]
    assert abs(getattr(mc, 'TP_FILL_MISS_P', -1.0) - expected_miss_p) < 1e-9, \
        f"arm={arm}: TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != {expected_miss_p} -- env propagation FAILED"
    assert getattr(mc, 'TP_FILL_GAP_AWARE', False) is True, \
        f"arm={arm}: TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != True -- env propagation FAILED"
    assert abs(mc.LIQUIDITY_FLOOR - 0.0) < 1e-9, \
        f"mc.LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR} != 0.0 -- the EXISTING production liquidity knob " \
        f"must stay inert; THIS campaign's floor is applied entirely via ctx post-filtering, not that knob"
    if survivor:
        assert getattr(mc, 'MC_UNIVERSE_FILE', None) == SURVIVOR_FILE, \
            f"MC_UNIVERSE_FILE={getattr(mc, 'MC_UNIVERSE_FILE', None)!r} != {SURVIVOR_FILE!r} -- env propagation FAILED"

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if window not in window_lookup:
        raise SystemExit(f"[STOP] window {window!r} not in mc.WINDOWS {sorted(window_lookup)} -- "
                         f"never invent/rename labels (paired-seed rule)")
    d_start, d_end = window_lookup[window]

    mc.N_ITER = n_iter

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"CELL job={job} arm={arm} window={window} tp={tp} sl={sl} survivor={survivor} "
        f"n_iter={n_iter} pid={os.getpid()}", log_path)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']} "
        f"(pinned meta={TPSL_META_PATH})", log_path)
    _tee(f"[CONFIG] TP_BASE={mc.TP_BASE} SL_BASE={mc.SL_BASE} GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} "
        f"MAX_POSITIONS={mc.MAX_POSITIONS} MAX_POSITIONS_CALL={mc.MAX_POSITIONS_CALL}", log_path)
    _tee(f"[CONFIG] TIER_ALLOC={mc.TIER_ALLOC}", log_path)
    # Required evidence line (build brief: "Self-log the effective env (MISS_P,
    # GAP_AWARE) per subprocess into logs/").
    _tee(f"[ENV] arm={arm} TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} "
        f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} "
        f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR} "
        f"MC_UNIVERSE_FILE={getattr(mc, 'MC_UNIVERSE_FILE', None)!r}", log_path)

    t0 = time.perf_counter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx = mc._prepare_window(window, d_start, d_end, version_meta['id'])
    t_prepare = time.perf_counter() - t0
    captured = buf.getvalue()
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(captured)

    # SURVIVOR GUARD engagement proof -- ported verbatim (mechanism, not text)
    # from phaseD_run.py's --survivor-only path: the '[universe-filter]' print
    # inside load_signals can be suppressed by the loader-memoization cache on
    # a cache-hit prepare (the cached signal list was already filtered by the
    # cache-MISS prepare that populated it), so engagement is proven directly
    # on the loaded data whenever the print is absent -- never silently
    # trusted. n_calls_delisted is then computed unconditionally (cross-check
    # regardless of which path proved engagement), matching phaseD's own
    # "populated in ALL survivor-mode cells" convention.
    n_calls_delisted = None
    if survivor:
        if '[universe-filter]' not in captured:
            _syms = {k[0] for k in ctx['call_outcomes'].keys()}
            _n_out = sum(1 for s in _syms if str(s).upper() not in survivor_set)
            if _n_out > 0:
                raise SystemExit(
                    f"FATAL: --survivor set MC_UNIVERSE_FILE={SURVIVOR_FILE!r} but the prepare "
                    f"for job={job} arm={arm} window={window} shows NO '[universe-filter]' "
                    f"engagement line AND {_n_out} loaded symbols are OUTSIDE the survivor file "
                    f"-- the filter did not fire. STOP (the survivor arm must be PROVABLY "
                    f"engaged, never silently trusted). Captured prepare output: {log_path}; "
                    f"do not improvise.")
            _tee(f"[universe-check] arm={arm} window={window}: engagement proven via direct "
                f"property on a cache-hit prepare ({len(_syms)} call-signal symbols, 0 outside "
                f"survivor file; engine print suppressed by loader cache)", log_path)
        call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
        n_calls_delisted = sum(1 for s in call_syms if str(s).upper() not in survivor_set)
        _tee(f"[universe-check] arm={arm} window={window}: {len(call_syms)} distinct call-signal "
            f"symbols, {n_calls_delisted} NOT in survivor_universe_811.txt "
            f"(expected ~0 under --survivor)", log_path)

    n_raw_total = len(ctx['call_outcomes'])
    overall_by_key = overall_map_from_ctx(ctx)
    if arm == 'A0':
        pass_set = None
    else:
        floor_df = _load_floor_df()
        pass_set = pass_set_for_arm(arm, floor_df, ctx_keys=ctx['call_outcomes'].keys())
    ctx, n_75_before, n_75_after, n_overflow = apply_floor(ctx, pass_set, overall_by_key)
    assert n_75_before + n_overflow == n_raw_total, (
        f"internal: n_75_before={n_75_before} + n_overflow={n_overflow} != raw ctx count {n_raw_total}")
    n_total_after = len(ctx['call_outcomes'])
    assert n_total_after == n_75_after + n_overflow, (
        f"internal: n_total_after={n_total_after} != n_75_after={n_75_after} + n_overflow={n_overflow}")
    _tee(f"[FLOOR] arm={arm} window={window} (AMENDMENT-1: overflow tier untouched): "
        f"75+ primary: before={n_75_before} after={n_75_after} "
        f"(dropped {n_75_before - n_75_after}, "
        f"{100.0 * (n_75_before - n_75_after) / n_75_before if n_75_before else 0.0:.1f}%) | "
        f"70-74 overflow: {n_overflow} (untouched, always) | "
        f"total ctx population: before={n_raw_total} after={n_total_after}", log_path)

    n_calls, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)
    assert n_calls == n_total_after, f"internal: call_outcome_rates n={n_calls} != post-filter total {n_total_after}"

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

    row = {
        'phase': 'F', 'mode': 'calib', 'profile': 'core', 'window': window,
        'tp': mc.TP_BASE, 'sl': mc.SL_BASE, 'n_iter': n_iter, 'n_call_signals': n_calls,
        'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
        'p10_ret': p10_ret, 'p90_ret': p90_ret,
        'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
        'p_coll': result.get('p_coll'),
        'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
        'realized_call_tp_pct': result.get('call_tp'),
        'n_calls_delisted': n_calls_delisted,   # None unless survivor=True (see CSV_FIELDS comment)
        # AMENDMENT-1: n_call_signals = TOTAL ctx population (75+ survivors + all overflow,
        # untouched) = n_calls above; n_signals_after_filter = 75+ PRIMARY-tier survivors ONLY
        # (the coordinator's explicit amended-report request) -- these now legitimately differ.
        'arm': arm, 'n_signals_after_filter': n_75_after,
    }

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    csv_is_new = not os.path.exists(out_csv)
    with open(out_csv, 'a', newline='', encoding='utf-8') as csv_f:
        csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
        if csv_is_new:
            csv_w.writeheader()
        csv_w.writerow(row)

    _tee(f"[DONE] arm={arm} window={window} tp={mc.TP_BASE} sl={mc.SL_BASE} survivor={survivor} "
        f"n_iter={n_iter} n_signals_after_filter(75+ only)={n_75_after} "
        f"n_call_signals(total, incl overflow)={n_calls} n_calls_delisted={n_calls_delisted} "
        f"prepare={t_prepare:.1f}s sim={t_sim:.1f}s | tp_rate={tp_rate:.1f}% sl_rate={sl_rate:.1f}% "
        f"hard_rate={hard_rate:.1f}% | worst_dd={result.get('worst_dd'):.2f}% "
        f"med_ret={result.get('med_ret'):+.2f}% p_coll={result.get('p_coll'):.2f}% -> {out_csv}", log_path)
    return row


# ---------------------------------------------------------------------------
# TRIPWIRE -- prepare-only (no _simulate_window, no N_ITER cost), single
# process, loops the 5 locked windows in-process (no import-time env axis
# varies across windows, so no subprocess isolation is needed here).
# ---------------------------------------------------------------------------
def _run_tripwire(job, windows, out_csv, misses_csv, log_path):
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing (see run_one_cell's docstring note)")

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env('core')
    for k, v in ARM_FILL_ENV['A0'].items():   # irrelevant to a prepare-only pass, set for consistency only
        os.environ[k] = v
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    missing = [w for w in windows if w not in window_lookup]
    if missing:
        raise SystemExit(f"[STOP] window label(s) {missing!r} not in mc.WINDOWS {sorted(window_lookup)} "
                         f"-- STOP per build brief step 3 (verify labels exist before running)")

    floor_df = _load_floor_df()
    # AMENDMENT-1 point 1+3 (resolves the pre-amendment ambiguity this file's
    # own history had to compute 4 ways): PRIMARY GATE = denominator is 75+
    # PRIMARY-tier signal events ONLY; numerator is ANY-STATUS ledger_v2
    # presence (a skip-reason row IS a determination). This is
    # rate_any_75plus_pct below. The all-70/kept-only breakdowns are kept
    # alongside purely as diagnostic continuity with the pre-amendment run
    # (they are NOT gating anything here).
    any_keys = set(zip(floor_df['ticker'].to_list(), floor_df['entry_date'].to_list()))
    kept_keys = set(zip(floor_df.filter(floor_df['in_ledger'])['ticker'].to_list(),
                        floor_df.filter(floor_df['in_ledger'])['entry_date'].to_list()))

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"TRIPWIRE job={job} windows={windows}", log_path)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']}", log_path)
    _tee(f"[CONFIG] floor_exclusions.parquet: {len(any_keys)} any-status rows, {len(kept_keys)} in_ledger=True rows", log_path)

    rows = []
    miss_rows = []
    for label in windows:
        d_start, d_end = window_lookup[label]
        t0 = time.perf_counter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = mc._prepare_window(label, d_start, d_end, version_meta['id'])
        t_prepare = time.perf_counter() - t0
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(buf.getvalue())

        # overall score per key, recovered from calls_by_date entries
        # (symbol_id, overall, key, ct, ern) -- needed only for the 75+-only breakdown.
        overall_by_key = {}
        for _d, entries in ctx['calls_by_date'].items():
            for sid, overall, key, ct, ern in entries:
                overall_by_key[key] = overall

        event_keys_all = set(ctx['call_outcomes'].keys())
        event_keys_75p = {k for k in event_keys_all if overall_by_key.get(k, 0) >= 75}

        joined_any_all = event_keys_all & any_keys
        joined_kept_all = event_keys_all & kept_keys
        joined_any_75p = event_keys_75p & any_keys
        joined_kept_75p = event_keys_75p & kept_keys

        n_all = len(event_keys_all)
        n_75p = len(event_keys_75p)
        rate_any_all = 100.0 * len(joined_any_all) / n_all if n_all else float('nan')
        rate_kept_all = 100.0 * len(joined_kept_all) / n_all if n_all else float('nan')
        rate_any_75p = 100.0 * len(joined_any_75p) / n_75p if n_75p else float('nan')
        rate_kept_75p = 100.0 * len(joined_kept_75p) / n_75p if n_75p else float('nan')
        passed = rate_any_75p >= TRIPWIRE_PRIMARY_THRESHOLD_PCT   # AMENDMENT-1 primary gate

        rows.append({
            'window': label, 'n_events_all70': n_all,
            'n_joined_any_all70': len(joined_any_all), 'rate_any_all70_pct': round(rate_any_all, 2),
            'n_joined_kept_all70': len(joined_kept_all), 'rate_kept_all70_pct': round(rate_kept_all, 2),
            'n_events_75plus': n_75p,
            'n_joined_any_75plus': len(joined_any_75p), 'rate_any_75plus_pct': round(rate_any_75p, 2),
            'n_joined_kept_75plus': len(joined_kept_75p), 'rate_kept_75plus_pct': round(rate_kept_75p, 2),
            'pass_90pct_primary': passed,
        })

        # AMENDMENT-1: sample drawn from the amended (75+-only) miss population,
        # not the overflow-tier-dominated all70 population -- this is the
        # population the tripwire and floor now actually operate on.
        non_joining = sorted(event_keys_75p - any_keys, key=lambda k: (k[1], k[0]))[:10]
        for sym, d in non_joining:
            miss_rows.append({'window': label, 'ticker': sym, 'entry_date': d,
                              'overall': overall_by_key.get((sym, d))})

        _tee(f"[TRIPWIRE] window={label} prepare={t_prepare:.1f}s n_events_75plus={n_75p} "
            f"rate_any_75plus={rate_any_75p:.2f}% rate_kept_75plus={rate_kept_75p:.2f}% | "
            f"[diag] n_events_all70={n_all} rate_any_all70={rate_any_all:.2f}% "
            f"rate_kept_all70={rate_kept_all:.2f}% | PRIMARY(75+/any-status)={'PASS' if passed else 'FAIL'} "
            f"(threshold {TRIPWIRE_PRIMARY_THRESHOLD_PCT:.0f}%)", log_path)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=TRIPWIRE_CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(misses_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=TRIPWIRE_MISS_FIELDS)
        w.writeheader()
        w.writerows(miss_rows)

    any_fail = [r['window'] for r in rows if not r['pass_90pct_primary']]
    _tee(f"\n[TRIPWIRE SUMMARY] {len(rows)} windows checked (AMENDMENT-1 population/windows). "
        f"{'ALL PASS' if not any_fail else 'FAIL: ' + ','.join(any_fail)} "
        f"(primary metric = rate_any_75plus_pct >= {TRIPWIRE_PRIMARY_THRESHOLD_PCT:.0f}%)", log_path)
    _tee(f"[WRITE] {out_csv} ({len(rows)} rows)", log_path)
    _tee(f"[WRITE] {misses_csv} ({len(miss_rows)} sample miss rows)", log_path)
    return rows


# ---------------------------------------------------------------------------
# ORCHESTRATOR -- loops a stage's fixed cell list, launching one cell-worker
# subprocess per cell not already done (state-file resumable). Cells are
# either 2-tuples (arm, window) -- identity/main/survivor -- or 4-tuples
# (arm, window, tp, sl) -- neighborhood. `survivor` is a single job-wide flag
# (not per-cell) passed through to every cell-worker when the whole job is a
# SURVIVOR GUARD run.
# ---------------------------------------------------------------------------
def _run_orchestrator(job, stage, cells, n_iter, log_path, state_path, out_csv, survivor=False):
    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"ORCHESTRATOR job={job} stage={stage} survivor={survivor} cells={cells} n_iter={n_iter}", log_path)

    t_job0 = time.time()
    for i, cell in enumerate(cells, 1):
        if len(cell) == 2:
            arm, window = cell
            tp = sl = None
        else:
            arm, window, tp, sl = cell
        key = tuple(cell)
        if key in done_set:
            _tee(f"[{i}/{len(cells)}] SKIP (already done) arm={arm} window={window} tp={tp} sl={sl}", log_path)
            continue
        _tee(f"[{i}/{len(cells)}] LAUNCH arm={arm} window={window} tp={tp} sl={sl} survivor={survivor} "
            f"n_iter={n_iter}", log_path)

        child_env = os.environ.copy()
        child_env['PYTHONIOENCODING'] = 'utf-8'
        cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker',
              '--arm', arm, '--window', window, '--n-iter', str(n_iter),
              '--out-csv', out_csv, '--log-path', log_path, '--job', job]
        if survivor:
            cmd += ['--survivor']
        if tp is not None:
            cmd += ['--tp', str(tp), '--sl', str(sl)]
        t0 = time.time()
        proc = subprocess.run(cmd, env=child_env, cwd=_REPO_ROOT)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise SystemExit(f"[STOP] cell-worker FAILED (exit {proc.returncode}) arm={arm} "
                             f"window={window} tp={tp} sl={sl} after {elapsed:.1f}s -- see {log_path} tail. "
                             f"{i - 1}/{len(cells)} cells completed before this failure.")

        done_set.add(key)
        state['done_cells'] = [list(k) for k in sorted(done_set)]
        state['job'] = job
        state['stage'] = stage
        state['survivor'] = survivor
        state['n_iter'] = n_iter
        import mc_patch
        mc_patch.atomic_write_json(state_path, state)
        _tee(f"[{i}/{len(cells)}] OK arm={arm} window={window} tp={tp} sl={sl} ({elapsed:.1f}s)", log_path)

    elapsed_job = time.time() - t_job0
    _tee(f"\n[ORCHESTRATOR DONE] job={job} stage={stage} total_done={len(done_set)}/{len(cells)} "
        f"wall={elapsed_job:.1f}s -> {out_csv}", log_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--selftest', action='store_true', help='offline unit tests, no DB/monte_carlo/B:/ dependency')
    p.add_argument('--stage', choices=['tripwire', 'identity', 'main', 'survivor', 'neighborhood'], default=None,
                   help='orchestrator stage to run (mutually exclusive with --cell-worker)')
    p.add_argument('--job', default=None, help='job name -- output files are out/floorMC_<job>.csv etc')
    p.add_argument('--arms', default=None, help='comma list, default A0,A1,A2 (only for --stage main)')
    p.add_argument('--windows', default=None,
                   help='comma list, default the AMENDMENT-1 4 locked windows (2023,2024,2025,dip) '
                        '(--stage main/survivor only; identity/neighborhood use their own fixed window(s))')
    p.add_argument('--n-iter', type=int, default=N_ITER_DEFAULT, help=f'MC iterations per cell (default {N_ITER_DEFAULT})')
    p.add_argument('--smoke-n', type=int, default=None, help='override --n-iter for a cheap smoke run')
    # cell-worker (internal) args -- also settable at the orchestrator level for --stage survivor
    p.add_argument('--cell-worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--arm', default=None, help=argparse.SUPPRESS)
    p.add_argument('--window', default=None, help=argparse.SUPPRESS)
    p.add_argument('--out-csv', default=None, help=argparse.SUPPRESS)
    p.add_argument('--log-path', default=None, help=argparse.SUPPRESS)
    p.add_argument('--survivor', action='store_true',
                   help='DELISTED-EXCLUDED universe (MC_UNIVERSE_FILE=survivor_universe_811.txt). '
                        'Cell-worker: apply to this one cell. Orchestrator (--stage main): apply to '
                        'every cell in the job.')
    p.add_argument('--tp', type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument('--sl', type=float, default=None, help=argparse.SUPPRESS)
    return p.parse_args()


def main():
    args = parse_args()

    if args.selftest:
        raise SystemExit(selftest())

    for d in (OUT_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    if args.cell_worker:
        if not (args.arm and args.window and args.out_csv and args.log_path and args.job):
            raise SystemExit("--cell-worker requires --arm --window --out-csv --log-path --job")
        run_one_cell(args.arm, args.window, args.n_iter, args.out_csv, args.log_path, args.job,
                    survivor=args.survivor, tp=args.tp, sl=args.sl)
        return

    if args.stage is None:
        raise SystemExit("specify --stage {tripwire,identity,main,survivor,neighborhood} or --selftest")
    job = args.job or args.stage
    n_iter = args.smoke_n if args.smoke_n else args.n_iter
    windows = [w.strip() for w in args.windows.split(',')] if args.windows else list(LOCKED_WINDOWS)

    if args.stage == 'tripwire':
        out_csv = os.path.join(OUT_DIR, f'floorMC_{job}.csv')
        misses_csv = os.path.join(OUT_DIR, f'floorMC_{job}_misses_sample.csv')
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        _run_tripwire(job, windows, out_csv, misses_csv, log_path)
        return

    survivor = False
    if args.stage == 'identity':
        cells = identity_cells()
    elif args.stage == 'main':
        arms = [a.strip() for a in args.arms.split(',')] if args.arms else list(LOCKED_ARMS)
        cells = main_cells_grid(arms, windows)
    elif args.stage == 'survivor':
        cells = survivor_cells()   # fixed grid: SURVIVOR_ARMS x LOCKED_WINDOWS -- coordinator directive, not overridable via --arms/--windows
        survivor = True
    else:   # neighborhood
        cells = neighborhood_cells()   # fixed grid: NEIGHBORHOOD_ARM x NEIGHBORHOOD_WINDOWS x NEIGHBORHOOD_TPSL_CELLS

    out_csv, state_path, log_path = _paths_for_job(job)
    _run_orchestrator(job, args.stage, cells, n_iter, log_path, state_path, out_csv, survivor=survivor)


if __name__ == '__main__':
    main()
