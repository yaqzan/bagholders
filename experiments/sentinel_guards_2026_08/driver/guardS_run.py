#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
guardS_run.py -- Sentinel calibrated-positive guard battery: G-S1 (survivor),
G-S2 (buffer), G-S3 (trade-count floors, report-only).
(experiments/sentinel_guards_2026_08/PREREG.md, locked commit 9b2c1b47)

======================================================================
WHAT THIS BATTERY IS (PREREG, for context)
======================================================================
Object under test: R1's discovery (realism_default_flip_2026_08/out/flipR_r1.csv)
that Sentinel reads calibrated-POSITIVE on recent windows (22-now +37.4%/DD43.5,
5y +45.2%/DD46.2, 2023 +52.6%, collapse 0). The prior calibrated-positive config
(S9 mp20) died on the survivor check. This battery decides UNVERIFIED ->
(guard-verified | S9-pattern-dead). Arms, locked:
  G-S1 survivor : Sentinel x 12 windows x N=500, delisted-EXCLUDED universe.
  G-S2 buffer   : Sentinel x {22-now,5y,2023,2024,2025,dip} x N=500,
                  TP_FILL_MISS_P=0.20 + GAP_AWARE (stressed vs the 0.15 default).
  G-S3          : report-only trade-count floors, no pass/fail gate of its own
                  (feeds the ANECDOTE auto-downgrade on top of G-S1/G-S2).
No verdicts computed here -- the PASS/FAIL/downgrade mapping is locked in the
PREREG and applied by the orchestrator, not this script.

======================================================================
CRIBBED (read-only imports; "cribbing your flipR machinery" per the build
brief) vs FRESHLY WRITTEN
======================================================================
DIRECTLY IMPORTED from experiments/realism_default_flip_2026_08/driver/
flipR_run.py (which itself re-exports phaseD_run.py's own constants --
importing flipR_run pulls both transitively, one import surface):
  PROFILE_ENV['sentinel'] (the exact Sentinel tier/gross/ceiling mapping,
    already verified live against portfolio_profiles.json), LENS_ENV
    ('calibrated' = the 0.15/GAP post-flip default), PROFILE_EXPECTED,
    expected_for_cell, min_score_funded, TPSL_META_PATH, max_score_date
    (close-boundary probe), compute_fingerprint (substrate-fingerprint probe
    -- profile-invariant by construction: n_call_signals is drawn from the
    SAME loaded population regardless of which profile's TIER_ALLOC later
    re-buckets it, confirmed against R1's own data: n_call_signals is
    identical core/apex/sentinel for every window), _tee, PHASE_D_WINDOWS_12,
    SURVIVOR_FILE, _load_survivor_set, _load_json.
Importing flipR_run.py executes its module-level code (its own sys.path
inserts + its own `from phaseD_run import ...`) but that code is verified
side-effect-free at import time (flipR_run.py's own docstring), and its
path-resolution asserts are __file__-relative so they resolve identically
regardless of which script imports it.

FRESHLY WRITTEN (this battery's shape -- arm x window, with a per-arm
post-prepare ctx filter (G-S1 only) and a per-arm lens (G-S2's stressed
0.20+GAP has no equivalent in flipR's LENS_ENV) -- does not fit flipR's own
run_one_cell/_run_orchestrator signatures closely enough to call them
directly, same "doesn't match closely enough" judgment flipR_run.py itself
made about floor_mc's functions):
  apply_survivor_filter() -- ctx['call_outcomes']/['calls_by_date'] post-
    filter mirroring floor_mc_2026_08's apply_floor() MUTATION PATTERN
    exactly (same two structures, same call_outcomes-is-load-bearing
    rationale, verified against monte_carlo.py source) but gating on symbol
    survivorship instead of a floor pass_set.
  LENS_STRESS_020 -- G-S2's TP_FILL_MISS_P=0.20/GAP_AWARE=1 lens (new value,
    not in flipR's LENS_ENV).
  Trade-tape / G-S3 mechanism -- monte_carlo.py ALREADY has an env-gated
    (default OFF) per-trade tape: `TRADE_TAPE_ENABLED = os.environ.get(
    'MC_TRADE_TAPE','0')=='1'` (monte_carlo.py:3127), which makes
    run_single_sim populate r['_tape'] (per-trade rows incl. `seed` and
    `entry_open_calls`/`entry_open_puts`, the exact per-path/per-entry
    concurrency snapshot G-S3 needs) and _simulate_window's own
    _dump_trade_tape() writes it to .cache/dd_ledger/tape_{window}.parquet
    (monte_carlo.py:3925-3990). Setting MC_TRADE_TAPE=1 in a cell's env is a
    CONFIG TOGGLE (same category as TP_FILL_MISS_P/GAP_AWARE), not new
    instrumentation -- monte_carlo.py is never edited. Because the tape file
    is named only by window label (no job/arm discriminant), this driver
    MOVES it out to out/tapes/{arm}_{window}.parquet immediately after each
    cell-worker subprocess returns, before the next cell (which may reuse
    the same window label under a different arm) can overwrite it. G-S3
    reads GENUINE per-path trade counts (group tape rows by `seed`, count)
    and reports the MEDIAN (not the engine's own pre-aggregated MEAN
    call_trades/put_trades, which the result dict always carries regardless
    of the tape toggle) plus median `entry_open_calls` across all trade rows
    as "median concurrent positions" (a per-trade snapshot of how many calls
    were already open at that trade's own entry -- the only concurrency
    figure the engine logs anywhere; puts are always 0 for Sentinel via
    mc_patch.disable_puts).
  Cell-worker / orchestrator -- fresh (arm, window) cell shape; same
  architecture (env-before-import -> import monte_carlo -> hard-verify ->
  prepare -> [survivor-filter if G-S1] -> simulate -> row) and the same
  subprocess-per-cell + state-file-resumable + fingerprint/close-boundary
  guard orchestrator pattern as flipR_run.py, freshly written for the same
  `os.path.abspath(__file__)`-must-resolve-to-THIS-file reason documented in
  every driver in this program.

HARD RULE: this file NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file, and NEVER edits flipR_run.py / phaseD_run.py (read
only, imported in place). This script never git-commits anything.

Usage
-----
    python guardS_run.py --selftest

    python guardS_run.py --stage gs1 --job gs1 [--n-iter 500]
    python guardS_run.py --stage gs2 --job gs2 [--n-iter 500]
    python guardS_run.py --stage gs3-report --job gs3
        # aggregates whatever tape parquets exist under out/tapes/ -- run
        # AFTER gs1/gs2 (no new simulation, pure file read)

Console output is ASCII-only throughout (no em-dash/smart-quote/unicode
arrows) per this repo's convention.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import redirect_stdout

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../sentinel_guards_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                                        # .../sentinel_guards_2026_08
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)                                 # .../experiments
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)                               # repo root
_TPSL_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'tpsl_refine_2026_08', 'driver')
_FLIPR_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'realism_default_flip_2026_08', 'driver')

for _d in (_THIS_DIR, _TPSL_DRIVER_DIR, _FLIPR_DRIVER_DIR, _REPO_ROOT):
    if _d not in sys.path:
        sys.path.insert(0, _d)

assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} has no monte_carlo.py "
    f"(computed from __file__={__file__!r}) -- fix the dirname() chain above"
)
assert os.path.isfile(os.path.join(_FLIPR_DRIVER_DIR, 'flipR_run.py')), (
    f"flipR_run.py not found at {_FLIPR_DRIVER_DIR!r} -- this battery requires "
    f"reusing it in-place (READ-ONLY); it must never be copied/duplicated"
)

from flipR_run import (                                                      # noqa: E402
    PROFILE_ENV, LENS_ENV, PROFILE_EXPECTED, expected_for_cell,
    min_score_funded, TPSL_META_PATH, max_score_date, compute_fingerprint,
    PHASE_D_WINDOWS_12, SURVIVOR_FILE, _load_survivor_set, _load_json,
)


def _tee(msg, log_path):
    """Local copy of flipR_run.py's own _tee (path-based, opens/closes fresh
    per call) -- not imported, for the identical Windows cross-process
    file-access-safety reason flipR_run.py's own docstring documents (a
    parent orchestrator and its spawned cell-worker children may both write
    the same log path)."""
    print(msg, flush=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


OUT_DIR = os.path.join(_EXP_DIR, 'out')
TAPES_DIR = os.path.join(OUT_DIR, 'tapes')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
DD_LEDGER_DIR = os.path.join(_REPO_ROOT, '.cache', 'dd_ledger')   # monte_carlo.py's own fixed tape output dir

R1_REFERENCE_CSV = os.path.join(_EXPERIMENTS_DIR, 'realism_default_flip_2026_08', 'out', 'flipR_r1.csv')

GS1_WINDOWS = list(PHASE_D_WINDOWS_12)                       # G-S1: all 12
GS2_WINDOWS = ['22-now', '5y', '2023', '2024', '2025', 'dip']   # G-S2: locked 6

LENS_STRESS_020 = {'TP_FILL_MISS_P': '0.20', 'TP_FILL_GAP_AWARE': '1'}   # G-S2's stressed lens

N_ITER_DEFAULT = 500

BASE_SCHEMA = [
    'arm', 'window', 'universe', 'lens', 'tp', 'sl', 'n_iter', 'n_call_signals',
    'n_call_signals_before_filter', 'n_delisted_dropped',
    'mean_ret', 'med_ret', 'worst_dd', 'mean_dd', 'p_coll',
    'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s', 'realized_call_tp_pct', 'n_calls_delisted',
    'tier_ultra', 'tier_top', 'tier_mid', 'tier_low', 'tier_overflow',
    'max_positions', 'gross_cap', 'call_cap', 'capital_ceiling', 'min_score_funded',
    'miss_p', 'gap_aware', 'avg_open_premium_base_pct', 'max_open_premium_base_pct',
]


def apply_survivor_filter(ctx, survivor_set):
    """Filter ctx['call_outcomes']/['calls_by_date'] IN PLACE to EXCLUDE any
    key whose symbol is NOT in survivor_set -- G-S1's "delisted-EXCLUDED
    universe." Mirrors floor_mc_2026_08's apply_floor() mutation pattern
    exactly (same two ctx structures, same call_outcomes-is-load-bearing
    rationale -- every eligibility check in run_single_sim gates via `key in
    call_outcomes`) but gates on symbol survivorship instead of a floor
    pass_set. Returns (ctx, n_before, n_after, n_dropped)."""
    def _is_survivor(k):
        return str(k[0]).upper() in survivor_set

    n_before = len(ctx['call_outcomes'])
    ctx['call_outcomes'] = {k: v for k, v in ctx['call_outcomes'].items() if _is_survivor(k)}
    new_cbd = defaultdict(list)
    for d, entries in ctx['calls_by_date'].items():
        kept = [e for e in entries if _is_survivor(e[2])]
        if kept:
            new_cbd[d] = kept
    ctx['calls_by_date'] = new_cbd
    n_after = len(ctx['call_outcomes'])
    return ctx, n_before, n_after, n_before - n_after


def selftest() -> int:
    log = print
    log("=== guardS_run.py OFFLINE SELF-TESTS ===")

    # -- 1. apply_survivor_filter: synthetic ctx, no DB/monte_carlo -----------
    ctx = {
        'call_outcomes': {('AAA', '2024-01-02'): 1, ('BBB', '2024-01-03'): 1, ('CCC', '2024-01-04'): 1},
        'calls_by_date': {
            '2024-01-02': [(1, 90, ('AAA', '2024-01-02'), 30, False)],
            '2024-01-03': [(2, 85, ('BBB', '2024-01-03'), 30, False)],
            '2024-01-04': [(3, 80, ('CCC', '2024-01-04'), 30, False), (4, 77, ('AAA', '2024-01-04'), 30, False)],
        },
    }
    survivor_set = {'AAA', 'CCC'}   # BBB is "delisted" (not a survivor)
    ctx2, n_before, n_after, n_dropped = apply_survivor_filter(ctx, survivor_set)
    assert n_before == 3 and n_after == 2 and n_dropped == 1, (n_before, n_after, n_dropped)
    assert ('BBB', '2024-01-03') not in ctx2['call_outcomes']
    assert ('AAA', '2024-01-02') in ctx2['call_outcomes'] and ('CCC', '2024-01-04') in ctx2['call_outcomes']
    assert '2024-01-03' not in ctx2['calls_by_date'], "BBB's only date should be dropped entirely (empty list pruned)"
    assert len(ctx2['calls_by_date']['2024-01-04']) == 2, \
        "both CCC and AAA are survivors -- 2024-01-04's two-entry list must stay intact"
    kept_keys_0104 = {e[2] for e in ctx2['calls_by_date']['2024-01-04']}
    assert kept_keys_0104 == {('CCC', '2024-01-04'), ('AAA', '2024-01-04')}
    log("  [1] apply_survivor_filter: drops non-survivor keys from both call_outcomes "
        "and calls_by_date, prunes now-empty date buckets, case-normalizes via SURVIVOR_FILE convention OK")

    # -- 2. cell lists match the locked grids ----------------------------------
    assert len(GS1_WINDOWS) == 12, GS1_WINDOWS
    assert set(GS1_WINDOWS) == set(PHASE_D_WINDOWS_12)
    assert GS2_WINDOWS == ['22-now', '5y', '2023', '2024', '2025', 'dip'], GS2_WINDOWS
    assert len(GS2_WINDOWS) == 6
    log("  [2] GS1_WINDOWS=12 (all PHASE_D_WINDOWS_12), GS2_WINDOWS=6 (locked subset) OK")

    # -- 3. LENS_STRESS_020 is distinct from flipR's own LENS_ENV -------------
    assert LENS_STRESS_020 == {'TP_FILL_MISS_P': '0.20', 'TP_FILL_GAP_AWARE': '1'}
    assert LENS_STRESS_020 != LENS_ENV['calibrated']
    log("  [3] LENS_STRESS_020 = 0.20/GAP, distinct from flipR's calibrated 0.15/GAP OK")

    # -- 4. sentinel PROFILE_ENV reused unchanged from flipR_run.py -----------
    assert PROFILE_ENV['sentinel']['GROSS_PREMIUM_CAP'] == '0.30'
    assert PROFILE_ENV['sentinel']['MAX_POSITIONS_OVERRIDE'] == '14'
    assert min_score_funded(PROFILE_EXPECTED['sentinel']['TIER_ALLOC']) == 85
    log("  [4] Sentinel PROFILE_ENV/PROFILE_EXPECTED reused unmodified from flipR_run.py OK")

    log("=== SELFTEST PASS ===")
    return 0


def _move_tape(window, arm, log_path):
    """Move .cache/dd_ledger/tape_{window}.parquet (if it exists -- it won't
    if MC_TRADE_TAPE wasn't set, or if a window had zero trades) to
    out/tapes/{arm}_{window}.parquet, uniquely scoped so the NEXT cell
    (possibly the same window under a different arm) cannot clobber it
    before G-S3 reads it. Returns the destination path or None."""
    src = os.path.join(DD_LEDGER_DIR, f'tape_{window}.parquet')
    if not os.path.isfile(src):
        _tee(f"[TAPE] no tape file produced for window={window} (0 trades, or MC_TRADE_TAPE unset) -- skipping move", log_path)
        return None
    os.makedirs(TAPES_DIR, exist_ok=True)
    dst = os.path.join(TAPES_DIR, f'{arm}_{window}.parquet')
    shutil.move(src, dst)
    _tee(f"[TAPE] moved {src} -> {dst}", log_path)
    return dst


def run_one_cell(arm, window, n_iter, out_csv, log_path, job):
    """arm in {'gs1','gs2'}. Sentinel profile always; lens/survivor-filter
    depend on arm. MC_TRADE_TAPE=1 always set (cheap -- see module docstring)
    so G-S3 can read genuine per-path data after the fact."""
    if arm not in ('gs1', 'gs2'):
        raise SystemExit(f"[STOP] unknown arm {arm!r}")

    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(
            f"[STOP] {TPSL_META_PATH} missing. This battery pins ALGORITHM_VERSION to the "
            f"SAME id every other campaign in this program used (id=74, git_commit=f9fb7b934). "
            f"Refusing to auto-re-resolve. Report to orchestrator rather than improvising.")

    survivor_set = _load_survivor_set(SURVIVOR_FILE)

    import mc_patch   # experiments/tpsl_refine_2026_08/driver/mc_patch.py (sys.path-added above)

    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in PROFILE_ENV['sentinel'].items():
        os.environ[k] = v
    lens_env = LENS_ENV['calibrated'] if arm == 'gs1' else LENS_STRESS_020
    for k, v in lens_env.items():
        os.environ[k] = v
    os.environ['MC_TRADE_TAPE'] = '1'
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc

    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)

    exp = expected_for_cell('sentinel')
    assert abs(mc.TP_BASE - exp['TP_BASE']) < 1e-9
    assert abs(mc.SL_BASE - exp['SL_BASE']) < 1e-9
    assert abs(mc.GROSS_PREMIUM_CAP - exp['GROSS_PREMIUM_CAP']) < 1e-9
    live_mp = mc.MAX_POSITIONS_CALL if mc.MAX_POSITIONS_CALL is not None else mc.MAX_POSITIONS
    assert live_mp == exp['MAX_POSITIONS'], f"live MAX_POSITIONS(_CALL)={live_mp} != expected {exp['MAX_POSITIONS']}"
    for tier, expected_frac in exp['TIER_ALLOC'].items():
        live_frac = mc.TIER_ALLOC.get(tier)
        assert live_frac is not None and abs(live_frac - expected_frac) < 1e-9
    assert abs(mc.PRACTICAL_CAPITAL_CEILING - exp['PRACTICAL_CAPITAL_CEILING']) < 1e-6
    exp_miss_p = float(lens_env['TP_FILL_MISS_P'])
    exp_gap_aware = lens_env['TP_FILL_GAP_AWARE'] == '1'
    assert abs(getattr(mc, 'TP_FILL_MISS_P', -1.0) - exp_miss_p) < 1e-9, \
        f"arm={arm}: TP_FILL_MISS_P={getattr(mc, 'TP_FILL_MISS_P', None)} != expected {exp_miss_p}"
    assert getattr(mc, 'TP_FILL_GAP_AWARE', None) is exp_gap_aware
    assert os.environ.get('MC_TRADE_TAPE') == '1'

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if window not in window_lookup or window not in PHASE_D_WINDOWS_12:
        raise SystemExit(f"[STOP] window {window!r} invalid for this battery")
    d_start, d_end = window_lookup[window]

    mc.N_ITER = n_iter

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"CELL job={job} arm={arm} window={window} n_iter={n_iter} pid={os.getpid()}", log_path)
    _tee(f"[CONFIG] algorithm_version: id={version_meta['id']} git_commit={version_meta['git_commit']}", log_path)
    live_min_score = min_score_funded(mc.TIER_ALLOC)
    _tee(f"[ENGAGED-CONFIG] profile=sentinel tier_ultra={mc.TIER_ALLOC.get('ultra')} "
        f"tier_top={mc.TIER_ALLOC.get('top')} tier_mid={mc.TIER_ALLOC.get('mid')} "
        f"tier_low={mc.TIER_ALLOC.get('low')} min_score_funded={live_min_score} "
        f"max_positions={live_mp} gross_cap={mc.GROSS_PREMIUM_CAP} capital_ceiling={mc.PRACTICAL_CAPITAL_CEILING}", log_path)
    _tee(f"[ENGAGED-CONFIG] arm={arm} universe={'delisted-excluded' if arm=='gs1' else 'full'} "
        f"miss_p={getattr(mc, 'TP_FILL_MISS_P', None)} gap_aware={getattr(mc, 'TP_FILL_GAP_AWARE', None)} "
        f"trade_tape={os.environ.get('MC_TRADE_TAPE')}", log_path)

    t0 = time.perf_counter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx = mc._prepare_window(window, d_start, d_end, version_meta['id'])
    t_prepare = time.perf_counter() - t0
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(buf.getvalue())

    n_before_filter = len(ctx['call_outcomes'])
    n_delisted_dropped = 0
    if arm == 'gs1':
        ctx, n_before_filter, n_after_filter, n_delisted_dropped = apply_survivor_filter(ctx, survivor_set)
        _tee(f"[SURVIVOR-FILTER] window={window} before={n_before_filter} after={n_after_filter} "
            f"dropped={n_delisted_dropped}", log_path)

    call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
    n_calls_delisted = sum(1 for s in call_syms if str(s).upper() not in survivor_set)
    if arm == 'gs1':
        assert n_calls_delisted == 0, \
            f"[STOP] survivor filter left {n_calls_delisted} delisted symbols in ctx -- filter bug"

    t1 = time.perf_counter()
    with open(log_path, 'a', encoding='utf-8') as log_f:
        with redirect_stdout(log_f):
            sim = mc._simulate_window(ctx)
    t_sim = time.perf_counter() - t1
    result = sim['seeded']

    tp_c = result.get('call_tp', None)
    n_calls = len(ctx['call_outcomes'])
    # tp/sl/hard/both rates recomputed the same way mc_patch.call_outcome_rates
    # does (avoids importing it just for 4 numbers already derivable, but same
    # source-of-truth outcome strings) -- kept minimal/local since this
    # battery reports realized_call_tp_pct straight off the result dict like
    # every other driver in this program.
    _tee(f"[DEPLOYMENT] arm={arm} window={window} gross_cap={mc.GROSS_PREMIUM_CAP} "
        f"avg_open_premium_base_pct={result.get('avg_open_premium_base_pct')} "
        f"max_open_premium_base_pct={result.get('max_open_premium_base_pct')}", log_path)
    _tee(f"[TRADES] arm={arm} window={window} mean_call_trades_per_path={result.get('call_trades')} "
        f"mean_put_trades_per_path={result.get('put_trades')} (engine's own pre-aggregated MEAN, "
        f"not median -- G-S3's real per-path median comes from the moved trade tape)", log_path)

    row = {
        'arm': arm, 'window': window, 'universe': 'delisted-excluded' if arm == 'gs1' else 'full',
        'lens': 'calibrated' if arm == 'gs1' else 'stress020',
        'tp': mc.TP_BASE, 'sl': mc.SL_BASE, 'n_iter': n_iter, 'n_call_signals': n_calls,
        'n_call_signals_before_filter': n_before_filter, 'n_delisted_dropped': n_delisted_dropped,
        'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
        'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'), 'p_coll': result.get('p_coll'),
        'tp_rate': None, 'sl_rate': None, 'hard_rate': None, 'both_rate': None,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
        'realized_call_tp_pct': result.get('call_tp'), 'n_calls_delisted': n_calls_delisted,
        'tier_ultra': mc.TIER_ALLOC.get('ultra'), 'tier_top': mc.TIER_ALLOC.get('top'),
        'tier_mid': mc.TIER_ALLOC.get('mid'), 'tier_low': mc.TIER_ALLOC.get('low'),
        'tier_overflow': mc.TIER_ALLOC.get('overflow'),
        'max_positions': live_mp, 'gross_cap': mc.GROSS_PREMIUM_CAP, 'call_cap': mc.CALL_PREMIUM_CAP,
        'capital_ceiling': mc.PRACTICAL_CAPITAL_CEILING, 'min_score_funded': live_min_score,
        'miss_p': getattr(mc, 'TP_FILL_MISS_P', None), 'gap_aware': getattr(mc, 'TP_FILL_GAP_AWARE', None),
        'avg_open_premium_base_pct': result.get('avg_open_premium_base_pct'),
        'max_open_premium_base_pct': result.get('max_open_premium_base_pct'),
    }
    # call/sl/hard/both rate via mc_patch (same source every driver uses)
    n_c, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)
    row['tp_rate'], row['sl_rate'], row['hard_rate'], row['both_rate'] = tp_rate, sl_rate, hard_rate, both_rate

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    csv_is_new = not os.path.exists(out_csv)
    with open(out_csv, 'a', newline='', encoding='utf-8') as csv_f:
        csv_w = csv.DictWriter(csv_f, fieldnames=BASE_SCHEMA)
        if csv_is_new:
            csv_w.writeheader()
        csv_w.writerow(row)

    tape_dst = _move_tape(window, arm, log_path)

    _tee(f"[DONE] arm={arm} window={window} n_iter={n_iter} n_call_signals={n_calls} "
        f"n_calls_delisted={n_calls_delisted} prepare={t_prepare:.1f}s sim={t_sim:.1f}s "
        f"med_ret={result.get('med_ret'):+.2f}% worst_dd={result.get('worst_dd'):.2f}% "
        f"p_coll={result.get('p_coll'):.2f}% tape={tape_dst} -> {out_csv}", log_path)
    return row


def _run_orchestrator(job, stage, cells, n_iter, log_path, state_path, out_csv):
    """cells: list of (arm, window) tuples. Same fingerprint + close-boundary
    guard pattern as flipR_run.py's own _run_orchestrator (imported utilities
    compute_fingerprint/max_score_date -- fresh loop here, see module
    docstring for why the loop itself isn't imported)."""
    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"ORCHESTRATOR job={job} stage={stage} n_cells={len(cells)} n_iter={n_iter}", log_path)

    baseline_score_date = max_score_date()
    _tee(f"[CLOSE-BOUNDARY-GUARD] battery START max(Score.date)={baseline_score_date}", log_path)
    tainted_cells = []

    distinct_windows = sorted({cell[1] for cell in cells})
    fp_start = compute_fingerprint(distinct_windows, log_path)
    _tee(f"[FINGERPRINT-GUARD] battery START per-window loaded>=70 counts: {fp_start}", log_path)

    t_job0 = time.time()
    for i, cell in enumerate(cells, 1):
        arm, window = cell
        key = tuple(cell)
        if key in done_set:
            _tee(f"[{i}/{len(cells)}] SKIP (already done) {cell}", log_path)
            continue

        current_score_date = max_score_date()
        if current_score_date != baseline_score_date:
            _tee(f"[CLOSE-BOUNDARY-FLAG] max(Score.date) changed {baseline_score_date} -> "
                f"{current_score_date} BEFORE cell {i}/{len(cells)} {cell}", log_path)
            tainted_cells.append({'cell': cell, 'index': i, 'reason': 'close-boundary',
                                  'old_max_date': str(baseline_score_date), 'new_max_date': str(current_score_date)})
            baseline_score_date = current_score_date

        _tee(f"[{i}/{len(cells)}] LAUNCH arm={arm} window={window} n_iter={n_iter}", log_path)
        child_env = os.environ.copy()
        child_env['PYTHONIOENCODING'] = 'utf-8'
        cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker',
              '--arm', arm, '--window', window, '--n-iter', str(n_iter),
              '--out-csv', out_csv, '--log-path', log_path, '--job', job]
        t0 = time.time()
        proc = subprocess.run(cmd, env=child_env, cwd=_REPO_ROOT)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise SystemExit(f"[STOP] cell-worker FAILED (exit {proc.returncode}) cell={cell} "
                             f"after {elapsed:.1f}s -- {i - 1}/{len(cells)} cells completed before this failure.")

        done_set.add(key)
        state['done_cells'] = [list(k) for k in sorted(done_set, key=lambda t: [str(x) for x in t])]
        state['job'] = job
        state['stage'] = stage
        state['n_iter'] = n_iter
        import mc_patch
        mc_patch.atomic_write_json(state_path, state)
        _tee(f"[{i}/{len(cells)}] OK {cell} ({elapsed:.1f}s)", log_path)

    final_score_date = max_score_date()
    _tee(f"[CLOSE-BOUNDARY-GUARD] battery END max(Score.date)={final_score_date}", log_path)

    fp_end = compute_fingerprint(distinct_windows, log_path)
    _tee(f"[FINGERPRINT-GUARD] battery END per-window loaded>=70 counts: {fp_end}", log_path)
    fp_changed = {w: (fp_start.get(w), fp_end.get(w)) for w in distinct_windows if fp_start.get(w) != fp_end.get(w)}
    if fp_changed:
        _tee(f"[FINGERPRINT-GUARD-FLAG] {len(fp_changed)} window(s) changed: {fp_changed}", log_path)
        for i, cell in enumerate(cells, 1):
            if cell[1] in fp_changed and tuple(cell) in done_set:
                tainted_cells.append({'cell': cell, 'index': i, 'reason': 'fingerprint',
                                      'window': cell[1], 'start_count': fp_changed[cell[1]][0], 'end_count': fp_changed[cell[1]][1]})
    else:
        _tee("[FINGERPRINT-GUARD] no count change detected -- 0 tainted windows", log_path)

    _tee(f"[TAINT-SUMMARY] {len(tainted_cells)} cell(s) flagged as tainted: {tainted_cells}"
        if tainted_cells else "[TAINT-SUMMARY] 0 tainted cells", log_path)

    elapsed_job = time.time() - t_job0
    _tee(f"\n[ORCHESTRATOR DONE] job={job} stage={stage} total_done={len(done_set)}/{len(cells)} "
        f"wall={elapsed_job:.1f}s -> {out_csv}", log_path)

    state['fingerprint_start'] = fp_start
    state['fingerprint_end'] = fp_end
    state['fingerprint_changed'] = fp_changed
    state['tainted_cells'] = tainted_cells
    import mc_patch
    mc_patch.atomic_write_json(state_path, state)
    return tainted_cells


def run_gs3_report(job, log_path, out_csv):
    """Report-only: aggregate whatever tape parquets exist under out/tapes/
    into per-(arm,window) median trades/path and median concurrent calls at
    entry. Pure file read -- no monte_carlo import, no DB, no new simulation."""
    import polars as pl
    import statistics as stats

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"GS3-REPORT job={job} tapes_dir={TAPES_DIR}", log_path)

    if not os.path.isdir(TAPES_DIR):
        _tee(f"[STOP] {TAPES_DIR} does not exist -- run gs1/gs2 first", log_path)
        raise SystemExit(1)

    rows = []
    for fname in sorted(os.listdir(TAPES_DIR)):
        if not fname.endswith('.parquet'):
            continue
        arm_window = fname[:-len('.parquet')]
        arm, window = arm_window.split('_', 1)
        path = os.path.join(TAPES_DIR, fname)
        df = pl.read_parquet(path)
        n_rows = len(df)
        if n_rows == 0:
            _tee(f"[GS3] {fname}: 0 trade rows -- skipping", log_path)
            continue
        per_seed_counts = df.group_by('seed').len()['len'].to_list()
        n_paths = len(per_seed_counts)
        mean_trades = stats.mean(per_seed_counts)
        median_trades = stats.median(per_seed_counts)
        concurrent_calls = df['entry_open_calls'].drop_nulls().to_list()
        mean_conc = stats.mean(concurrent_calls) if concurrent_calls else None
        median_conc = stats.median(concurrent_calls) if concurrent_calls else None

        thin_flag = 'ANECDOTE' if median_trades < 10 else ('THIN' if median_trades < 30 else 'OK')

        _tee(f"[GS3] arm={arm} window={window} n_trade_rows={n_rows} n_paths_with_trades={n_paths} "
            f"mean_trades_per_path={mean_trades:.2f} median_trades_per_path={median_trades:.2f} "
            f"mean_concurrent_calls_at_entry={mean_conc} median_concurrent_calls_at_entry={median_conc} "
            f"flag={thin_flag}", log_path)

        rows.append({
            'arm': arm, 'window': window, 'n_trade_rows': n_rows, 'n_paths_with_trades': n_paths,
            'mean_trades_per_path': mean_trades, 'median_trades_per_path': median_trades,
            'mean_concurrent_calls_at_entry': mean_conc, 'median_concurrent_calls_at_entry': median_conc,
            'flag': thin_flag,
        })

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['arm', 'window', 'n_trade_rows', 'n_paths_with_trades', 'mean_trades_per_path',
                     'median_trades_per_path', 'mean_concurrent_calls_at_entry', 'median_concurrent_calls_at_entry', 'flag']
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    _tee(f"[WRITE] {out_csv} ({len(rows)} rows)", log_path)
    return rows


def _paths_for_job(job):
    return (
        os.path.join(OUT_DIR, f'guardS_{job}.csv'),
        os.path.join(STATE_DIR, f'guardS_{job}.json'),
        os.path.join(LOG_DIR, f'{job}.log'),
    )


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--selftest', action='store_true')
    p.add_argument('--stage', choices=['gs1', 'gs2', 'gs3-report'], default=None)
    p.add_argument('--job', default=None)
    p.add_argument('--n-iter', type=int, default=N_ITER_DEFAULT)
    p.add_argument('--cell-worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--arm', default=None, help=argparse.SUPPRESS)
    p.add_argument('--window', default=None, help=argparse.SUPPRESS)
    p.add_argument('--out-csv', default=None, help=argparse.SUPPRESS)
    p.add_argument('--log-path', default=None, help=argparse.SUPPRESS)
    return p.parse_args()


def main():
    args = parse_args()
    if args.selftest:
        raise SystemExit(selftest())

    for d in (OUT_DIR, TAPES_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    if args.cell_worker:
        if not (args.arm and args.window and args.out_csv and args.log_path and args.job):
            raise SystemExit("--cell-worker requires --arm --window --out-csv --log-path --job")
        run_one_cell(args.arm, args.window, args.n_iter, args.out_csv, args.log_path, args.job)
        return

    if args.stage is None:
        raise SystemExit("specify --stage {gs1,gs2,gs3-report} or --selftest")
    job = args.job or args.stage

    if args.stage == 'gs3-report':
        out_csv = os.path.join(OUT_DIR, f'guardS_{job}.csv')
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        run_gs3_report(job, log_path, out_csv)
        return

    windows = GS1_WINDOWS if args.stage == 'gs1' else GS2_WINDOWS
    arm = args.stage
    cells = [(arm, w) for w in windows]
    out_csv, state_path, log_path = _paths_for_job(job)
    _run_orchestrator(job, args.stage, cells, args.n_iter, log_path, state_path, out_csv)


if __name__ == '__main__':
    main()
