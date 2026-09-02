#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
floorCTL_run.py -- C1 random-cut exposure-matched control
(floor_control_2026_08/PREREG.md, locked commit dc7192dd). Final stage of
the floor_mc_2026_08 program (A2 floor SHIP-ELIGIBLE per that campaign's
main battery + coordinator adjudication; ship deferred behind THIS control).

======================================================================
QUESTION (PREREG, for context only -- this file does not compute or report
the decision rule; that is explicitly the coordinator's own attribution)
======================================================================
How much of A2's WorstDD improvement is SELECTION (verified liquidity ->
better fills -> shed unreliability) vs merely TRADING LESS (A2's realized
75+ primary-tier supply is 45-53% below A0's, PREREG "As-seen evidence")?
C1 answers this by cutting the SAME AMOUNT of supply, but RANDOMLY rather
than by liquidity -- same exposure reduction, zero selection signal. Any
DD edge A2 has over the mean of 5 random cuts is attributable to selection,
not size.

======================================================================
MECHANISM (PREREG "Arm", LOCKED)
======================================================================
Per window in {2023, 2024, 2025, dip} (A2_SUPPLY_TARGET below): draw 5 fixed
seeded uniform-random subsets of that window's 75+ PRIMARY-tier candidate
population (never touches the 70-74 overflow tier -- AMENDMENT-1 semantics,
identical rationale: overflow carries TIER_ALLOC['overflow']=0.0 in shipped
Core and removing it would gratuitously shift `_do_calls()`'s
`overflow.sort(key=lambda x: (-x[1], rng.random()))` tiebreaker draws for a
population that is a pure spectator in every arm), each subset sized EXACTLY
to A2's own realized per-window supply (297/766/676/359 -- taken directly
from experiments/floor_mc_2026_08/out/floorMC_main.csv's A2 rows, verified
live 2026-08-11 to match the PREREG's stated targets exactly). MISS_P stays
0.15 (PREREG: "a random cut earns no fill improvement" -- C1 gets NONE of
A2's fill-fidelity benefit, only the same exposure cut), GAP_AWARE=1.
20 cells total (4 windows x 5 subset seeds), N=500 paired sim seeds each.
A0 and A2 are NOT re-run here -- PREREG: "comparison uses floorMC_main.csv
(same engine, same paired window labels)"; that comparison/attribution is
the coordinator's, not this file's.

======================================================================
REUSE STRATEGY -- why apply_floor() needs ZERO changes for a random subset
======================================================================
experiments/floor_mc_2026_08/driver/floorMC_run.py's `apply_floor(ctx,
pass_set, overall_by_key)` (AMENDMENT-1) already implements EXACTLY the
mechanic this control needs: given ANY set of (symbol,date) keys as
`pass_set`, it keeps every 70-74 overflow-tier candidate unconditionally and
keeps a 75+ primary-tier candidate iff its key is in `pass_set`. That
function has no opinion on how `pass_set` was constructed -- a
ledger-liquidity-derived set (A1/A2) and a uniform-random-draw set (C1) are
equally valid inputs to the exact same tier-gated filter. So this file
IMPORTS `apply_floor` and `overall_map_from_ctx` from floorMC_run.py
READ-ONLY (never edits or copies that file, per the coordinator's explicit
instruction) rather than reimplementing tier-gating logic that already
exists, is already tested (floorMC_run.py --selftest checks 5-8), and is
already proven correct against real data (the whole floor_mc_2026_08 main
battery + survivor guard + neighborhood check). Also reused read-only:
LOCKED_WINDOWS (the same 4 window labels), TPSL_META_PATH (the same pinned
ALGORITHM_VERSION, id=74 git_commit=f9fb7b934), PRIMARY_THRESHOLD_SCORE (75,
the same 75+/70-74 tier boundary), CSV_FIELDS (the base schema this file
appends `subset_seed` onto), and the `_load_json`/`_tee` utilities. Importing
floorMC_run.py triggers zero side effects at import time (guarded by
`if __name__ == '__main__':`) -- verified against its own source, the same
precedent it ALREADY relies on for its own `from phaseD_run import
SURVIVOR_FILE, _load_survivor_set` read-only reuse.

======================================================================
SUBSET DRAW -- reproducibility contract (PREREG: "deterministic")
======================================================================
1. Build the FULL 75+ primary-tier candidate list for a window from that
   cell's own freshly-prepared ctx (mc._prepare_window is deterministic
   given the same window/version -- no randomness in signal loading), then
   SORT it by (date, symbol) -- primary_candidates_sorted() below. Sorting
   makes the draw independent of dict/DB iteration order, which the raw
   ctx['call_outcomes'].keys() order is NOT guaranteed to be stable across
   runs/processes.
2. Draw with a FRESH `random.Random(subset_seed)` instance (subset_seed in
   1..5, PREREG) via `.sample(sorted_candidates, target_n)` -- isolated from
   the global `random` module state and from monte_carlo's own simulation
   RNG stream (which uses entirely separate random.Random instances keyed by
   window label + iteration index, monte_carlo.py's `_stable_label_seed`;
   the two RNG streams never interact).
3. Re-sort the drawn subset for a canonical, auditable form (does not change
   the subset's identity -- a uniform random SET's properties don't depend
   on presentation order).
This makes every (window, subset_seed) cell's subset a pure function of
(that window's real signal population, target_n, subset_seed) -- rerunning
any cell reproduces the identical subset (selftest check (d)), and it is
provably drawn from the 75+ population only (selftest check (b)).

======================================================================
ONE SUBPROCESS PER CELL -- same discipline as floorMC_run.py's whole
program, "per its pattern" per the coordinator's instruction, even though
(unlike the A0/A1/A2 arm axis) TP_FILL_MISS_P is CONSTANT across every C1
cell (0.15, never varies) -- only the drawn subset differs per cell. Kept as
subprocess-per-cell anyway for consistency/auditability/resumability with
the rest of this program, not because it is strictly required by an
import-time env axis this time.
======================================================================

HARD RULE: this file NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file, and NEVER edits anything under
experiments/floor_mc_2026_08/ (floorMC_run.py is imported READ-ONLY, never
copied/duplicated) or experiments/tpsl_refine_2026_08/ (mc_patch.py /
phaseD_run.py, transitively imported via floorMC_run.py, same rule). This
script never git-commits anything.

Windows/multiprocessing note (same as every driver in this program):
monte_carlo._simulate_window builds its OWN multiprocessing.Pool per call --
on Windows (spawn) every pool worker re-imports THIS FILE as a non-
`__main__` module. Everything with side effects (arg parsing, DB access,
subprocess spawning, the cell loop) MUST stay inside a function, reached
only through `if __name__ == '__main__':` -- never at module level. The
eager `import floorMC_run as _fmc` below IS at module level, but is safe for
the same reason floorMC_run.py's own eager `from phaseD_run import ...` is
safe: zero side effects at import time (verified against source).

Locked spec: experiments/floor_control_2026_08/PREREG.md (commit dc7192dd;
DO NOT modify that file).

Usage
-----
    python experiments/floor_control_2026_08/driver/floorCTL_run.py --selftest

    python experiments/floor_control_2026_08/driver/floorCTL_run.py \\
        --stage control --job c1 [--n-iter 500] [--smoke-n N]
        # fixed grid: 4 windows x 5 subset seeds = 20 cells -> out/floorCTL_c1.csv

Console output is ASCII-only throughout (no em-dash/smart-quote/unicode
arrows) per this repo's convention.
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import redirect_stdout

# --- repo-root + cross-experiment bootstrap -- explicit + asserted, never
# inferred from CWD (traps.md "Worktree PYTHONPATH trap"). Safe to re-run
# (idempotent) inside spawned cell-worker subprocesses. -----------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../floor_control_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                                        # .../floor_control_2026_08
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)                                 # .../experiments
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)                               # repo root
_FLOOR_MC_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'floor_mc_2026_08', 'driver')
_TPSL_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'tpsl_refine_2026_08', 'driver')

for _d in (_THIS_DIR, _FLOOR_MC_DRIVER_DIR, _TPSL_DRIVER_DIR, _REPO_ROOT):
    if _d not in sys.path:
        sys.path.insert(0, _d)

assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} has no monte_carlo.py "
    f"(computed from __file__={__file__!r}) -- fix the dirname() chain above"
)
assert os.path.isfile(os.path.join(_FLOOR_MC_DRIVER_DIR, 'floorMC_run.py')), (
    f"floor_mc driver not found at {_FLOOR_MC_DRIVER_DIR!r} -- this control campaign "
    f"REUSES its apply_floor/overall_map_from_ctx/LOCKED_WINDOWS/TPSL_META_PATH read-only "
    f"(never edits/copies that file)"
)

# floorMC_run.py -- READ-ONLY reuse (never edit that file): see module
# docstring "REUSE STRATEGY". Importing it triggers zero side effects at
# import time (its own DB/mc-touching code lives inside main(), guarded by
# `if __name__ == '__main__':`) -- the same precedent it already relies on
# for its own read-only import of phaseD_run.py.
import floorMC_run as _fmc                                                   # noqa: E402

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')

# ---------------------------------------------------------------------------
# LOCKED constants -- PREREG.md "Arm".
# ---------------------------------------------------------------------------
CONTROL_WINDOWS = list(_fmc.LOCKED_WINDOWS)   # {2023,2024,2025,dip} -- "same window labels as the main battery"
SUBSET_SEEDS = [1, 2, 3, 4, 5]                # PREREG "subset seeds 1..5"

# A2's realized per-window 75+ primary-tier supply -- PREREG's own stated
# targets (2023:297, 2024:766, 2025:676, dip:359), cross-verified live
# 2026-08-11 against experiments/floor_mc_2026_08/out/floorMC_main.csv's
# actual A2 rows' n_signals_after_filter column: exact match, all 4 windows.
A2_SUPPLY_TARGET = {'2023': 297, '2024': 766, '2025': 676, 'dip': 359}

CONTROL_MISS_P = '0.15'      # PREREG -- NOT 0.11; "the control earns no fill improvement"
CONTROL_GAP_AWARE = '1'

N_ITER_DEFAULT = 500          # PREREG "N=500 paired sim seeds"

CSV_FIELDS = list(_fmc.CSV_FIELDS) + ['subset_seed']   # PREREG: "same schema + subset_seed column"


def control_cells():
    """The locked 20-cell grid: 4 windows x 5 subset seeds."""
    return [(window, seed) for window in CONTROL_WINDOWS for seed in SUBSET_SEEDS]


# ---------------------------------------------------------------------------
# Subset draw -- pure, unit-testable (see --selftest), no monte_carlo/MySQL
# dependency of their own (caller passes in an already-prepared ctx-like
# dict / already-built candidate list).
# ---------------------------------------------------------------------------
def primary_candidates_sorted(ctx, overall_by_key):
    """Sorted (symbol,date) keys in ctx['call_outcomes'] whose overall score
    is >= PRIMARY_THRESHOLD_SCORE (75+) -- reuses floorMC_run's own tier
    boundary constant (imported, never re-guessed: _fmc.PRIMARY_THRESHOLD_
    SCORE == 75 == monte_carlo.PRIMARY_THRESHOLD). Sorted by (date, symbol)
    for a fully deterministic base population, independent of dict/DB
    iteration order -- see module docstring "SUBSET DRAW"."""
    keys = [k for k in ctx['call_outcomes'].keys()
           if overall_by_key.get(k, 0) >= _fmc.PRIMARY_THRESHOLD_SCORE]
    return sorted(keys, key=lambda k: (k[1], k[0]))


def draw_c1_subset(candidates_sorted, target_n, subset_seed):
    """Uniform-random subset of EXACTLY target_n keys from candidates_sorted,
    fully deterministic given (candidates_sorted, target_n, subset_seed).
    `random.Random(subset_seed)` is a FRESH, isolated RNG instance -- never
    touches the global `random` module state or monte_carlo's own simulation
    RNG stream (entirely separate random.Random instances, keyed by window
    label + iteration index -- monte_carlo.py's `_stable_label_seed`/
    `seeds = [1000 * _stable_label_seed(label) + it for it in
    range(N_ITER)]`; the two streams cannot interact). Raises SystemExit
    (never silently truncates) if target_n exceeds the available population.
    Output re-sorted for a canonical, auditable form."""
    if target_n > len(candidates_sorted):
        raise SystemExit(
            f"[STOP] target_n={target_n} > available 75+ candidates {len(candidates_sorted)} "
            f"-- cannot draw a subset this large. A2 IS itself a subset of this exact "
            f"population, so this should be structurally impossible; investigate (version "
            f"drift? wrong window?) before proceeding, do not improvise a smaller n.")
    rng = random.Random(subset_seed)
    return sorted(rng.sample(candidates_sorted, target_n), key=lambda k: (k[1], k[0]))


# ---------------------------------------------------------------------------
# --selftest -- pure logic checks, NO monte_carlo import, NO MySQL, NO B:/
# dependency (synthetic ctx only). Exercises the 4 checks the coordinator
# explicitly required: (a) subset size matches target, (b) subset drawn only
# from 75+ candidates, (c) different seeds differ, (d) same seed reproduces.
# ---------------------------------------------------------------------------
def selftest() -> int:
    from datetime import date
    log = print
    log("=== floorCTL_run.py OFFLINE SELF-TESTS ===")

    # -- 1. control_cells() is exactly the locked 20-cell grid ----------------
    cells = control_cells()
    assert len(cells) == 20, len(cells)
    assert set(w for w, s in cells) == set(CONTROL_WINDOWS), cells
    assert set(s for w, s in cells) == set(SUBSET_SEEDS), cells
    assert len(set(cells)) == 20, "duplicate cells in the locked grid"
    log("  [1] control_cells() produces the locked 20-cell grid (4 windows x 5 seeds), "
        "no duplicates OK")

    # -- synthetic ctx: 6 primary-tier (75+) + 2 overflow-tier (70-74) keys ---
    fake_ctx = {
        'call_outcomes': {
            ('AAA', date(2024, 1, 2)): {'kind': 'tp'}, ('BBB', date(2024, 1, 3)): {'kind': 'sl'},
            ('CCC', date(2024, 1, 4)): {'kind': 'tp'}, ('DDD', date(2024, 1, 5)): {'kind': 'hard'},
            ('EEE', date(2024, 1, 6)): {'kind': 'tp'}, ('FFF', date(2024, 1, 7)): {'kind': 'sl'},
            ('OVF', date(2024, 1, 10)): {'kind': 'tp'}, ('OVG', date(2024, 1, 11)): {'kind': 'tp'},
        },
        'calls_by_date': defaultdict(list, {
            date(2024, 1, 2): [('AAA', 80, ('AAA', date(2024, 1, 2)), None, False)],
            date(2024, 1, 3): [('BBB', 78, ('BBB', date(2024, 1, 3)), None, False)],
            date(2024, 1, 4): [('CCC', 76, ('CCC', date(2024, 1, 4)), None, False)],
            date(2024, 1, 5): [('DDD', 91, ('DDD', date(2024, 1, 5)), None, False)],
            date(2024, 1, 6): [('EEE', 82, ('EEE', date(2024, 1, 6)), None, False)],
            date(2024, 1, 7): [('FFF', 75, ('FFF', date(2024, 1, 7)), None, False)],
            date(2024, 1, 10): [('OVF', 72, ('OVF', date(2024, 1, 10)), None, False)],
            date(2024, 1, 11): [('OVG', 70, ('OVG', date(2024, 1, 11)), None, False)],
        }),
    }
    overall_by_key = _fmc.overall_map_from_ctx(fake_ctx)
    assert overall_by_key[('OVF', date(2024, 1, 10))] == 72 and overall_by_key[('OVG', date(2024, 1, 11))] == 70
    candidates = primary_candidates_sorted(fake_ctx, overall_by_key)

    # -- 2. (b) subset is drawn only from 75+ primary candidates --------------
    assert len(candidates) == 6, candidates   # AAA,BBB,CCC,DDD,EEE,FFF -- NOT OVF(72)/OVG(70)
    assert set(candidates) == {('AAA', date(2024, 1, 2)), ('BBB', date(2024, 1, 3)),
                               ('CCC', date(2024, 1, 4)), ('DDD', date(2024, 1, 5)),
                               ('EEE', date(2024, 1, 6)), ('FFF', date(2024, 1, 7))}
    assert ('OVF', date(2024, 1, 10)) not in candidates
    assert ('OVG', date(2024, 1, 11)) not in candidates
    log("  [2] (b) primary_candidates_sorted excludes 70-74 overflow-tier keys "
        "(OVF=72, OVG=70), keeps all 6 75+ keys OK")

    # -- 3. (a) subset size exactly matches target -----------------------------
    subset3 = draw_c1_subset(candidates, 3, subset_seed=1)
    assert len(subset3) == 3, subset3
    assert set(subset3) <= set(candidates), "subset must be a subset of the 75+ candidates"
    subset6 = draw_c1_subset(candidates, 6, subset_seed=1)   # the whole population
    assert len(subset6) == 6 and set(subset6) == set(candidates), subset6
    log("  [3] (a) draw_c1_subset returns EXACTLY target_n elements (partial and "
        "full-population cases), always a subset of the 75+ candidates OK")

    # -- 4. (d) same subset seed reproduces the same subset --------------------
    subset_a = draw_c1_subset(candidates, 3, subset_seed=7)
    subset_b = draw_c1_subset(candidates, 3, subset_seed=7)
    assert subset_a == subset_b, (subset_a, subset_b)
    log("  [4] (d) same subset_seed reproduces the identical subset (order included) OK")

    # -- 5. (c) two different subset seeds produce different subsets -----------
    subset_seed1 = draw_c1_subset(candidates, 3, subset_seed=1)
    subset_seed2 = draw_c1_subset(candidates, 3, subset_seed=2)
    assert subset_seed1 != subset_seed2, \
        "seeds 1 and 2 drew the identical subset -- suspicious, check RNG wiring"
    # Cross-check across all 5 real PREREG seeds pairwise, on a LARGER dummy
    # candidate pool (40 synthetic keys, draw 15) -- the 6-key fake_ctx above
    # is deliberately tiny for readability, but C(6,4)=15 possible subsets is
    # too small a space to guarantee 10 pairwise-distinct draws by chance
    # (a real collision was observed here during development, target_n=4 of
    # 6). The real campaign's populations are hundreds of candidates with
    # target_n in the hundreds (C(636,297) etc) -- astronomically collision-
    # free -- so this larger synthetic pool (C(40,15) ~ 4x10^10) is the
    # fairer stand-in for that regime, not the tiny 6-key fixture.
    big_pool = sorted((f'SYM{i:03d}', date(2024, 1, 1 + (i % 27))) for i in range(40))
    drawn = {s: draw_c1_subset(big_pool, 15, subset_seed=s) for s in SUBSET_SEEDS}
    for s1 in SUBSET_SEEDS:
        for s2 in SUBSET_SEEDS:
            if s1 != s2:
                assert drawn[s1] != drawn[s2], f"subset_seed={s1} and {s2} drew identical subsets"
    log("  [5] (c) all 5 PREREG subset seeds (1..5) pairwise draw different subsets "
        "(40-candidate pool, target_n=15, C(40,15)~4e10 -- collision-free by construction) OK")

    # -- 6. oversized target raises SystemExit, never silently truncates -------
    try:
        draw_c1_subset(candidates, 7, subset_seed=1)   # only 6 candidates available
        raise AssertionError("expected SystemExit for target_n > population size")
    except SystemExit:
        pass
    log("  [6] draw_c1_subset STOPs (SystemExit) rather than silently truncating when "
        "target_n exceeds the available population OK")

    # -- 7. apply_floor (imported from floor_mc, reused UNMODIFIED) correctly
    #    applies a C1 subset pass-set: keeps subset primary keys + ALL
    #    overflow keys untouched, drops non-subset primary keys -------------
    subset_pass = set(draw_c1_subset(candidates, 3, subset_seed=1))
    out_ctx, n_75_before, n_75_after, n_overflow = _fmc.apply_floor(
        copy.deepcopy(fake_ctx), subset_pass, overall_by_key)
    assert n_75_before == 6 and n_75_after == 3 and n_overflow == 2, \
        (n_75_before, n_75_after, n_overflow)
    expected_keys = subset_pass | {('OVF', date(2024, 1, 10)), ('OVG', date(2024, 1, 11))}
    assert set(out_ctx['call_outcomes'].keys()) == expected_keys, \
        (set(out_ctx['call_outcomes'].keys()), expected_keys)
    log("  [7] apply_floor (reused unmodified from floor_mc) applied to a C1 subset "
        "pass-set: keeps exactly the subset's 3 primary keys + both overflow keys "
        "untouched, drops the other 3 primary keys OK")

    # -- 8. target_n from A2_SUPPLY_TARGET never exceeds a plausible candidate
    #    population size sanity (static check only -- real population sizes
    #    are verified live per-cell in run_one_cell) --------------------------
    assert set(A2_SUPPLY_TARGET.keys()) == set(CONTROL_WINDOWS), A2_SUPPLY_TARGET
    assert all(n > 0 for n in A2_SUPPLY_TARGET.values()), A2_SUPPLY_TARGET
    log("  [8] A2_SUPPLY_TARGET covers exactly the 4 locked windows, all targets positive OK")

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# CELL-WORKER -- runs exactly ONE (window, subset_seed) cell in a fresh
# process. Every side-effecting statement lives here or in functions it
# calls; only invoked from main() under `if __name__ == '__main__':`.
# ---------------------------------------------------------------------------
def run_one_cell(window, subset_seed, n_iter, out_csv, log_path, job):
    if not os.path.exists(_fmc.TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {_fmc.TPSL_META_PATH} missing. This campaign pins ALGORITHM_VERSION to "
            f"the SAME id the tpsl_refine_2026_08/floor_mc_2026_08 campaigns resolved "
            f"(id=74, git_commit=f9fb7b934) -- required for a like-for-like comparison "
            f"against floorMC_main.csv. Refusing to auto-re-resolve (that would WRITE into "
            f"tpsl_refine_2026_08/driver/state/, violating the no-edit rule). Report to "
            f"orchestrator rather than improvising.")
    if window not in A2_SUPPLY_TARGET:
        raise SystemExit(f"[STOP] window {window!r} not in A2_SUPPLY_TARGET {sorted(A2_SUPPLY_TARGET)}")
    target_n = A2_SUPPLY_TARGET[window]

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    # 1) env BEFORE import -- FIXED control knobs (PREREG: MISS_P=0.15 NOT
    # 0.11, GAP_AWARE=1), constant across every C1 cell; only the drawn
    # subset varies per cell.
    mc_patch.apply_frozen_pins(max_workers=6)
    mc_patch.apply_profile_env('core')
    os.environ['TP_FILL_MISS_P'] = CONTROL_MISS_P
    os.environ['TP_FILL_GAP_AWARE'] = CONTROL_GAP_AWARE
    version_meta = mc_patch.resolve_and_pin_version(_fmc.TPSL_META_PATH)

    # 2) NOW import monte_carlo.
    import monte_carlo as mc

    # 3) post-import patches.
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)   # Core is calls-only (PUT_TIER_ALLOC all 0.0) -- matches every sibling campaign

    # 4) hard verification -- fail fast rather than silently running a wrong config.
    # Core AS SHIPPED, no TP/SL override (this control does not sweep TP/SL).
    assert abs(mc.GROSS_PREMIUM_CAP - 0.50) < 1e-9 and abs(mc.CALL_PREMIUM_CAP - 0.50) < 1e-9, \
        f"Core GROSS/CALL_PREMIUM_CAP != shipped 0.50 default (got {mc.GROSS_PREMIUM_CAP}/{mc.CALL_PREMIUM_CAP})"
    assert abs(mc.TP_BASE - 0.10) < 1e-9, f"mc.TP_BASE={mc.TP_BASE} != 0.10 -- Core AS SHIPPED expected"
    assert abs(mc.SL_BASE - (-1.00)) < 1e-9, f"mc.SL_BASE={mc.SL_BASE} != -1.00 -- Core AS SHIPPED expected"
    assert abs(getattr(mc, 'TP_FILL_MISS_P', -1.0) - 0.15) < 1e-9, \
        f"TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != 0.15 -- PREREG requires 0.15 NOT 0.11 " \
        f"(the control earns no fill improvement) -- env propagation FAILED"
    assert getattr(mc, 'TP_FILL_GAP_AWARE', False) is True, \
        f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} != True -- env propagation FAILED"
    assert abs(mc.LIQUIDITY_FLOOR - 0.0) < 1e-9, \
        f"mc.LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR} != 0.0 -- the EXISTING production liquidity knob " \
        f"must stay inert; this control's cut is applied entirely via ctx post-filtering, not that knob"

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if window not in window_lookup:
        raise SystemExit(f"[STOP] window {window!r} not in mc.WINDOWS {sorted(window_lookup)} -- "
                         f"never invent/rename labels (paired-seed rule)")
    d_start, d_end = window_lookup[window]

    mc.N_ITER = n_iter

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _fmc._tee(f"\n{'='*100}", log_path)
    _fmc._tee(f"CELL job={job} arm=C1 window={window} subset_seed={subset_seed} target_n={target_n} "
             f"n_iter={n_iter} pid={os.getpid()}", log_path)
    _fmc._tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']} "
             f"(pinned meta={_fmc.TPSL_META_PATH})", log_path)
    _fmc._tee(f"[CONFIG] TP_BASE={mc.TP_BASE} SL_BASE={mc.SL_BASE} GROSS_PREMIUM_CAP={mc.GROSS_PREMIUM_CAP} "
             f"MAX_POSITIONS={mc.MAX_POSITIONS} MAX_POSITIONS_CALL={mc.MAX_POSITIONS_CALL}", log_path)
    _fmc._tee(f"[CONFIG] TIER_ALLOC={mc.TIER_ALLOC}", log_path)
    _fmc._tee(f"[ENV] arm=C1 TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} "
             f"TP_FILL_GAP_AWARE={getattr(mc, 'TP_FILL_GAP_AWARE', None)} "
             f"LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR}", log_path)

    t0 = time.perf_counter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx = mc._prepare_window(window, d_start, d_end, version_meta['id'])
    t_prepare = time.perf_counter() - t0
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(buf.getvalue())

    n_raw_total = len(ctx['call_outcomes'])
    overall_by_key = _fmc.overall_map_from_ctx(ctx)
    candidates = primary_candidates_sorted(ctx, overall_by_key)
    if target_n > len(candidates):
        raise SystemExit(
            f"[STOP] window={window}: target_n={target_n} > available 75+ candidates "
            f"{len(candidates)} -- should be impossible (A2 is itself a subset of this "
            f"exact population); investigate version/window drift before proceeding.")
    subset = draw_c1_subset(candidates, target_n, subset_seed)
    subset_pass = set(subset)
    _fmc._tee(f"[SUBSET] window={window} subset_seed={subset_seed}: {len(candidates)} 75+ candidates "
             f"available, drew {len(subset)} (target_n={target_n})", log_path)

    ctx, n_75_before, n_75_after, n_overflow = _fmc.apply_floor(ctx, subset_pass, overall_by_key)
    assert n_75_before == len(candidates), \
        f"internal: n_75_before={n_75_before} != candidate count {len(candidates)}"
    assert n_75_after == target_n, \
        f"internal: n_75_after={n_75_after} != target_n={target_n} -- subset size invariant violated"
    n_total_after = len(ctx['call_outcomes'])
    assert n_total_after == n_75_after + n_overflow, (
        f"internal: n_total_after={n_total_after} != n_75_after={n_75_after} + n_overflow={n_overflow}")
    _fmc._tee(f"[FLOOR] arm=C1 window={window} subset_seed={subset_seed} (AMENDMENT-1 tier-gate: "
             f"overflow untouched): 75+ primary: before={n_75_before} after={n_75_after} "
             f"(target_n={target_n}) | 70-74 overflow: {n_overflow} (untouched, always) | "
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
        'phase': 'C', 'mode': 'calib', 'profile': 'core', 'window': window,
        'tp': mc.TP_BASE, 'sl': mc.SL_BASE, 'n_iter': n_iter, 'n_call_signals': n_calls,
        'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
        'p10_ret': p10_ret, 'p90_ret': p90_ret,
        'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
        'p_coll': result.get('p_coll'),
        'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
        'realized_call_tp_pct': result.get('call_tp'),
        'n_calls_delisted': None,   # no survivor universe in this control (out of scope)
        'arm': 'C1', 'n_signals_after_filter': n_75_after,
        'subset_seed': subset_seed,
    }

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    csv_is_new = not os.path.exists(out_csv)
    with open(out_csv, 'a', newline='', encoding='utf-8') as csv_f:
        csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
        if csv_is_new:
            csv_w.writeheader()
        csv_w.writerow(row)

    _fmc._tee(f"[DONE] arm=C1 window={window} subset_seed={subset_seed} n_iter={n_iter} "
             f"n_signals_after_filter={n_75_after} n_call_signals(total, incl overflow)={n_calls} "
             f"prepare={t_prepare:.1f}s sim={t_sim:.1f}s | tp_rate={tp_rate:.1f}% sl_rate={sl_rate:.1f}% "
             f"hard_rate={hard_rate:.1f}% | worst_dd={result.get('worst_dd'):.2f}% "
             f"med_ret={result.get('med_ret'):+.2f}% p_coll={result.get('p_coll'):.2f}% -> {out_csv}", log_path)
    return row


# ---------------------------------------------------------------------------
# ORCHESTRATOR -- loops the 20-cell grid, launching one cell-worker
# subprocess per (window, subset_seed) not already done (state-file
# resumable). Cannot reuse floorMC_run._run_orchestrator directly: that
# function re-invokes `os.path.abspath(__file__)` resolved from floorMC_
# run's OWN module namespace (floorMC_run.py itself), not this file -- so it
# would dispatch the wrong script. Structurally identical pattern, written
# fresh for this file's own (window, subset_seed) cell shape.
# ---------------------------------------------------------------------------
def _run_control_orchestrator(job, cells, n_iter, log_path, state_path, out_csv):
    state = _fmc._load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _fmc._tee(f"\n{'='*100}", log_path)
    _fmc._tee(f"ORCHESTRATOR job={job} stage=control cells={cells} n_iter={n_iter}", log_path)

    t_job0 = time.time()
    for i, (window, subset_seed) in enumerate(cells, 1):
        key = (window, subset_seed)
        if key in done_set:
            _fmc._tee(f"[{i}/{len(cells)}] SKIP (already done) window={window} subset_seed={subset_seed}", log_path)
            continue
        _fmc._tee(f"[{i}/{len(cells)}] LAUNCH window={window} subset_seed={subset_seed} n_iter={n_iter}", log_path)

        child_env = os.environ.copy()
        child_env['PYTHONIOENCODING'] = 'utf-8'
        cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker',
              '--window', window, '--subset-seed', str(subset_seed), '--n-iter', str(n_iter),
              '--out-csv', out_csv, '--log-path', log_path, '--job', job]
        t0 = time.time()
        proc = subprocess.run(cmd, env=child_env, cwd=_REPO_ROOT)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise SystemExit(f"[STOP] cell-worker FAILED (exit {proc.returncode}) window={window} "
                             f"subset_seed={subset_seed} after {elapsed:.1f}s -- see {log_path} tail. "
                             f"{i - 1}/{len(cells)} cells completed before this failure.")

        done_set.add(key)
        state['done_cells'] = [list(k) for k in sorted(done_set)]
        state['job'] = job
        state['n_iter'] = n_iter
        import mc_patch
        mc_patch.atomic_write_json(state_path, state)
        _fmc._tee(f"[{i}/{len(cells)}] OK window={window} subset_seed={subset_seed} ({elapsed:.1f}s)", log_path)

    elapsed_job = time.time() - t_job0
    _fmc._tee(f"\n[ORCHESTRATOR DONE] job={job} total_done={len(done_set)}/{len(cells)} "
             f"wall={elapsed_job:.1f}s -> {out_csv}", log_path)


def _paths_for_job(job):
    return (
        os.path.join(OUT_DIR, f'floorCTL_{job}.csv'),
        os.path.join(STATE_DIR, f'floorCTL_{job}.json'),
        os.path.join(LOG_DIR, f'{job}.log'),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--selftest', action='store_true', help='offline unit tests, no DB/monte_carlo dependency')
    p.add_argument('--stage', choices=['control'], default=None,
                   help='orchestrator stage to run (mutually exclusive with --cell-worker)')
    p.add_argument('--job', default=None,
                   help="job name -- output files are out/floorCTL_<job>.csv etc; default 'c1' "
                        "matches PREREG's required out/floorCTL_c1.csv")
    p.add_argument('--n-iter', type=int, default=N_ITER_DEFAULT, help=f'MC iterations per cell (default {N_ITER_DEFAULT})')
    p.add_argument('--smoke-n', type=int, default=None, help='override --n-iter for a cheap smoke run')
    # cell-worker (internal) args
    p.add_argument('--cell-worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--window', default=None, help=argparse.SUPPRESS)
    p.add_argument('--subset-seed', type=int, default=None, help=argparse.SUPPRESS)
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
        if not (args.window and args.subset_seed is not None and args.out_csv and args.log_path and args.job):
            raise SystemExit("--cell-worker requires --window --subset-seed --out-csv --log-path --job")
        run_one_cell(args.window, args.subset_seed, args.n_iter, args.out_csv, args.log_path, args.job)
        return

    if args.stage != 'control':
        raise SystemExit("specify --stage control or --selftest")
    job = args.job or 'c1'
    n_iter = args.smoke_n if args.smoke_n else args.n_iter
    cells = control_cells()

    out_csv, state_path, log_path = _paths_for_job(job)
    _run_control_orchestrator(job, cells, n_iter, log_path, state_path, out_csv)


if __name__ == '__main__':
    main()
