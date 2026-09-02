#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
frontier_run.py -- The selectivity frontier under honest fills (F0-F5).
(experiments/frontier_2026_08/PREREG.md, locked commit a095d2ae)

======================================================================
CRIBBED (read-only) vs FRESHLY WRITTEN
======================================================================
Imports directly from experiments/sentinel_guards_2026_08/driver/guardS_run.py
(which itself transitively re-exports flipR_run.py's own names -- one import
surface, per that file's own established pattern): PROFILE_ENV, LENS_ENV,
PROFILE_EXPECTED, expected_for_cell, min_score_funded, TPSL_META_PATH,
max_score_date, compute_fingerprint, PHASE_D_WINDOWS_12, SURVIVOR_FILE,
_load_survivor_set, apply_survivor_filter, LENS_STRESS_020 (buf20).

Freshly written (this campaign's shape has no precedent in either sibling
driver -- min-score/max-score ctx filters at ARBITRARY thresholds not aligned
to score_to_tier's fixed boundaries, and a put-enablement path no other
driver in this program has touched):
  overall_map_from_ctx()   -- same {key: overall} builder floor_mc's own
    function computes (independently re-derived here, not imported, since
    floorMC_run.py's own module docstring establishes each driver writes its
    own -- see that file's "doesn't match closely enough" precedent).
  apply_min_score_filter() / apply_max_score_filter() -- ctx post-filter
    mirroring apply_survivor_filter's/apply_floor's exact mutation pattern
    (same two ctx structures, same call_outcomes-is-load-bearing rationale)
    but gating on an arbitrary overall threshold instead of symbol
    survivorship or a floor pass_set.
  SPY/HISA reference table builder -- price_history adjusted-close pull,
    fresh (no other driver in this program computes an index benchmark).
  F0 tape decomposition, F5 liquidity-proxy rank correlation -- fresh
    report-only DB/file-read functions, no simulation.
  F4 put-enablement -- PUT_TIER_TOP_OV/PUT_TIER_MID_OV/PUT_TIER_LOW_OV env
    overrides ALREADY EXIST (monte_carlo.py:1338-1341, _env_alloc() same
    mechanism as TIER_ULTRA_OV etc.) and ARE the clean config/env path the
    PREREG asks to investigate first -- confirmed live 2026-08-12. The ONLY
    additional step needed is to NOT call mc_patch.disable_puts(mc) (which
    unconditionally stubs load_put_signals to return [], overriding any
    PUT_TIER_ALLOC value regardless of env) -- every OTHER driver in this
    program calls disable_puts() unconditionally; this file's F4 path is the
    sole exception, config/env only, zero code edits to monte_carlo.py or
    strategy_config.py.
  "Put-Sentinel shape" operationalization (not numerically specified in the
    PREREG beyond the name): PUT_TIER_TOP_OV=0.20 (mirrors Sentinel's own
    ultra=0.20 -- the single most-selective/extreme tier funded, others
    zeroed), PUT_TIER_MID_OV=PUT_TIER_LOW_OV=0, same mp14/gross0.30/TP0.10/
    SL-1.00/30-DTE as calls-Sentinel. put_score_to_tier (monte_carlo.py:1803-
    1806) gives 'put_top' for score<=15 NATIVELY (no extra filter needed for
    the max-score=15 arm); max-score=12 needs an explicit ctx max-score
    filter (analogous construction to apply_max_score_filter above).
  F3 DTE lever HOLD_CAL_DAYS construction: every existing profile in this
    program (Core/Apex/Sentinel) uses NOMINAL_CAL_DTE=30/HOLD_CAL_DAYS=27, a
    fixed -3-day buffer. F3's DTE axis {21,45,60} applies the SAME -3 offset
    (21->18, 45->42, 60->57) -- a construction choice, stated here plainly,
    not asserted as PREREG-specified.

HARD RULE: never edits monte_carlo.py / strategy_config.py / any tracked
production file, never edits guardS_run.py / flipR_run.py / phaseD_run.py
(read-only, imported in place). Never git-commits anything.

Usage
-----
    python frontier_run.py --selftest
    python frontier_run.py --stage spy-hisa
    python frontier_run.py --stage f0 --job f0
    python frontier_run.py --stage f1 --job f1 --n-iter 500
    python frontier_run.py --stage f2-survivor --thresholds 78,88 --job f2surv --n-iter 500
    python frontier_run.py --stage f2-buf20    --thresholds 78,88 --job f2buf --n-iter 500
    python frontier_run.py --stage f3 --thresholds 78 --job f3 --n-iter 500
    python frontier_run.py --stage f4 --job f4 --n-iter 500
    python frontier_run.py --stage f5 --job f5

Console output is ASCII-only throughout per this repo's convention.
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

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_THIS_DIR)
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)
_TPSL_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'tpsl_refine_2026_08', 'driver')
_FLIPR_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'realism_default_flip_2026_08', 'driver')
_GUARDS_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'sentinel_guards_2026_08', 'driver')

for _d in (_THIS_DIR, _TPSL_DRIVER_DIR, _FLIPR_DRIVER_DIR, _GUARDS_DRIVER_DIR, _REPO_ROOT):
    if _d not in sys.path:
        sys.path.insert(0, _d)

assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} (from __file__={__file__!r})")
assert os.path.isfile(os.path.join(_GUARDS_DRIVER_DIR, 'guardS_run.py')), (
    f"guardS_run.py not found at {_GUARDS_DRIVER_DIR!r} -- required read-only")

from guardS_run import (                                                     # noqa: E402
    PROFILE_ENV, LENS_ENV, PROFILE_EXPECTED, expected_for_cell, min_score_funded,
    TPSL_META_PATH, max_score_date, compute_fingerprint, PHASE_D_WINDOWS_12,
    SURVIVOR_FILE, _load_survivor_set, apply_survivor_filter, LENS_STRESS_020,
)


def _tee(msg, log_path):
    print(msg, flush=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TAPES_DIR = os.path.join(OUT_DIR, 'tapes')
DD_LEDGER_DIR = os.path.join(_REPO_ROOT, '.cache', 'dd_ledger')

GUARDS_TAPES_DIR = os.path.join(_EXPERIMENTS_DIR, 'sentinel_guards_2026_08', 'out', 'tapes')
R1_REFERENCE_CSV = os.path.join(_EXPERIMENTS_DIR, 'realism_default_flip_2026_08', 'out', 'flipR_r1.csv')
GS1_REFERENCE_CSV = os.path.join(_EXPERIMENTS_DIR, 'sentinel_guards_2026_08', 'out', 'guardS_gs1.csv')

N_ITER_DEFAULT = 500

F1_THRESHOLDS = [78, 80, 82, 85, 88]
F2_CARRY_WINDOWS_DECISION = ['22-now', '5y']
F2_BUF_WINDOWS = ['22-now', '5y', '2023', '2024', '2025', 'dip']
F3_DTE_AXIS = [21, 45, 60]        # 30 = anchor (F1 threshold=carrier row)
F3_GROSS_AXIS = [0.20, 0.45]      # 0.30 = anchor
F3_WINDOWS = ['22-now', '5y']
F4_MAX_SCORES = [15, 12]
F4_WINDOWS = ['2023', '2024', '2025', 'dip', '22-now', '5y']

SENTINEL_SHAPE_BASE = dict(PROFILE_ENV['sentinel'])   # cascade 0.2/0.15/0/0, mp14, gross0.30 -- unmodified

PUT_SENTINEL_ENV = {
    'PUT_TIER_TOP_OV': '0.20', 'PUT_TIER_MID_OV': '0', 'PUT_TIER_LOW_OV': '0',
}

# ---------------------------------------------------------------------------
# AMENDMENT-1 (2026-08-12, commit f02a6005) -- tier-native threshold mechanism.
# F1's ctx-removal min-score filter FAILED its identity anchor: real Sentinel
# keeps every >=70 candidate in ctx (their DENSITY feeds
# monte_carlo.py:3277-3280's call_pressure/put_pressure, consumed by
# _opportunity_saturation_scale and potentially other pressure-dependent
# dampeners at monte_carlo.py:3338-3344) and achieves selectivity purely via
# TIER FUNDING (TIER_ALLOC[tier]==0 -> premium_cost<=0 -> _try_fill_call
# rejects, but the candidate never leaves ctx). score_to_tier's actual band
# edges, read from source (monte_carlo.py:1795-1800):
#   ultra >= 95   top 85-94   mid 80-84   low 75-79   overflow < 75
# These are the ONLY threshold points expressible via tier funding (78/80/82/
#88 from the original F1 grid are NOT tier boundaries and cannot be
# reproduced this way without reintroducing the same ctx-contamination bug).
# TIER_FUNDING_POINTS generalizes Core's OWN shipped cascade (0.20/0.15/0.08/
# 0.03, strategy_config.py) by progressively zeroing the lowest surviving
# tier -- NOT invented percentages. 'zero-low+mid' is BIT-IDENTICAL to
# Sentinel's own PROFILE_ENV['sentinel'] cascade (0.20/0.15/0/0) -- this is
# the identity-anchor point, using literally the same TIER_ALLOC values R1
# used, not merely "close."
TIER_FUNDING_ORDER = ['fund-all', 'zero-low', 'zero-low+mid', 'ultra-only']
TIER_FUNDING_POINTS = {
    'fund-all':     {'ultra': 0.20, 'top': 0.15, 'mid': 0.08, 'low': 0.03},   # = Core's cascade
    'zero-low':     {'ultra': 0.20, 'top': 0.15, 'mid': 0.08, 'low': 0.0},
    'zero-low+mid': {'ultra': 0.20, 'top': 0.15, 'mid': 0.0,  'low': 0.0},    # == Sentinel (identity anchor)
    'ultra-only':   {'ultra': 0.20, 'top': 0.0,  'mid': 0.0,  'low': 0.0},
}
TIER_FUNDING_MIN_SCORE_LABEL = {   # for reporting only -- the "min-score" each point corresponds to
    'fund-all': 75, 'zero-low': 80, 'zero-low+mid': 85, 'ultra-only': 95,
}

# F4's put axis, corrected the same way: put_score_to_tier's edges
# (monte_carlo.py:1803-1806) are put_top<=15, put_mid<=20, put_low<=25 --
# ONLY put_top<=15 was ever tier-native in the original {15,12} grid (12 has
# no boundary and would need the same banned ctx-filter). Replaced with a
# 2-point tier-native put grid, values chosen by mirroring the call side's
# degrading cascade pattern (0.20/0.15/0.08 -> 0.20/0.15/0.10 for 3 put
# tiers) -- a construction choice, stated plainly, not a PREREG number.
PUT_TIER_FUNDING_ORDER = ['put-top-only', 'put-fund-all']
PUT_TIER_FUNDING_POINTS = {
    'put-top-only': {'put_top': 0.20, 'put_mid': 0.0,  'put_low': 0.0},
    'put-fund-all': {'put_top': 0.20, 'put_mid': 0.15, 'put_low': 0.10},
}


# ---------------------------------------------------------------------------
# ctx post-filters (fresh; mirror apply_survivor_filter's exact pattern)
# ---------------------------------------------------------------------------
def overall_map_from_ctx(ctx):
    """{(symbol,date) key -> overall score}, from ctx['calls_by_date'] entries
    (symbol_id, overall, key, ct, ern) 5-tuples -- same source floor_mc's own
    overall_map_from_ctx reads, independently re-derived here (see module
    docstring)."""
    overall_by_key = {}
    for _d, entries in ctx['calls_by_date'].items():
        for _sid, overall, key, _ct, _ern in entries:
            overall_by_key[key] = overall
    return overall_by_key


def apply_min_score_filter(ctx, threshold, overall_by_key):
    """Keep only keys with overall >= threshold. Mirrors apply_survivor_filter/
    apply_floor's mutation pattern exactly. Returns (ctx, n_before, n_after)."""
    def _keep(k):
        return overall_by_key.get(k, -1) >= threshold
    n_before = len(ctx['call_outcomes'])
    ctx['call_outcomes'] = {k: v for k, v in ctx['call_outcomes'].items() if _keep(k)}
    new_cbd = defaultdict(list)
    for d, entries in ctx['calls_by_date'].items():
        kept = [e for e in entries if _keep(e[2])]
        if kept:
            new_cbd[d] = kept
    ctx['calls_by_date'] = new_cbd
    return ctx, n_before, len(ctx['call_outcomes'])


def apply_max_score_filter(ctx, threshold, overall_by_key):
    """Keep only keys with overall <= threshold (F4's max-score=12 sub-case).
    Same pattern, inverted comparator."""
    def _keep(k):
        v = overall_by_key.get(k, 999)
        return v <= threshold
    n_before = len(ctx['call_outcomes'])
    ctx['call_outcomes'] = {k: v for k, v in ctx['call_outcomes'].items() if _keep(k)}
    new_cbd = defaultdict(list)
    for d, entries in ctx['calls_by_date'].items():
        kept = [e for e in entries if _keep(e[2])]
        if kept:
            new_cbd[d] = kept
    ctx['calls_by_date'] = new_cbd
    return ctx, n_before, len(ctx['call_outcomes'])


def selftest() -> int:
    log = print
    log("=== frontier_run.py OFFLINE SELF-TESTS ===")

    ctx = {
        'call_outcomes': {('AAA', '2024-01-02'): 1, ('BBB', '2024-01-03'): 1, ('CCC', '2024-01-04'): 1},
        'calls_by_date': {
            '2024-01-02': [(1, 90, ('AAA', '2024-01-02'), 30, False)],
            '2024-01-03': [(2, 80, ('BBB', '2024-01-03'), 30, False)],
            '2024-01-04': [(3, 76, ('CCC', '2024-01-04'), 30, False)],
        },
    }
    omap = overall_map_from_ctx(ctx)
    assert omap == {('AAA', '2024-01-02'): 90, ('BBB', '2024-01-03'): 80, ('CCC', '2024-01-04'): 76}
    log("  [1] overall_map_from_ctx: correct {key:overall} OK")

    ctx1 = {k: dict(v) if isinstance(v, dict) else v for k, v in ctx.items()}
    ctx1 = {'call_outcomes': dict(ctx['call_outcomes']), 'calls_by_date': {d: list(e) for d, e in ctx['calls_by_date'].items()}}
    ctx1, n_before, n_after = apply_min_score_filter(ctx1, 80, omap)
    assert n_before == 3 and n_after == 2, (n_before, n_after)
    assert ('CCC', '2024-01-04') not in ctx1['call_outcomes']
    assert '2024-01-04' not in ctx1['calls_by_date']
    log("  [2] apply_min_score_filter(threshold=80): drops the 76-scored key, keeps 90/80 OK")

    ctx2 = {'call_outcomes': dict(ctx['call_outcomes']), 'calls_by_date': {d: list(e) for d, e in ctx['calls_by_date'].items()}}
    ctx2, n_before2, n_after2 = apply_max_score_filter(ctx2, 80, omap)
    assert n_before2 == 3 and n_after2 == 2, (n_before2, n_after2)
    assert ('AAA', '2024-01-02') not in ctx2['call_outcomes']
    assert ('BBB', '2024-01-03') in ctx2['call_outcomes'] and ('CCC', '2024-01-04') in ctx2['call_outcomes']
    log("  [3] apply_max_score_filter(threshold=80): drops the 90-scored key, keeps 80/76 OK")

    assert F1_THRESHOLDS == [78, 80, 82, 85, 88]
    assert len(F1_THRESHOLDS) * len(PHASE_D_WINDOWS_12) == 60
    assert F3_DTE_AXIS == [21, 45, 60] and F3_GROSS_AXIS == [0.20, 0.45]
    assert F4_MAX_SCORES == [15, 12] and len(F4_WINDOWS) == 6
    log("  [4] F1/F3/F4 grid constants match PREREG counts OK")

    assert PUT_SENTINEL_ENV['PUT_TIER_TOP_OV'] == '0.20'
    assert SENTINEL_SHAPE_BASE['GROSS_PREMIUM_CAP'] == '0.30' and SENTINEL_SHAPE_BASE['MAX_POSITIONS_OVERRIDE'] == '14'
    log("  [5] PUT_SENTINEL_ENV / SENTINEL_SHAPE_BASE constants OK")

    # -- 6. AMENDMENT-1 tier-funding points: 'zero-low+mid' == Sentinel's own
    #    cascade exactly (the identity-anchor point), grid is 4 points, put
    #    grid is 2 points, both orders match their dicts' keys ---------------
    assert TIER_FUNDING_POINTS['zero-low+mid'] == {'ultra': 0.20, 'top': 0.15, 'mid': 0.0, 'low': 0.0}
    assert set(TIER_FUNDING_ORDER) == set(TIER_FUNDING_POINTS.keys()) and len(TIER_FUNDING_ORDER) == 4
    assert TIER_FUNDING_MIN_SCORE_LABEL['zero-low+mid'] == 85 and TIER_FUNDING_MIN_SCORE_LABEL['ultra-only'] == 95
    assert set(PUT_TIER_FUNDING_ORDER) == set(PUT_TIER_FUNDING_POINTS.keys()) and len(PUT_TIER_FUNDING_ORDER) == 2
    assert PUT_TIER_FUNDING_POINTS['put-top-only'] == {'put_top': 0.20, 'put_mid': 0.0, 'put_low': 0.0}
    log("  [6] TIER_FUNDING_POINTS: zero-low+mid == Sentinel's literal cascade, "
        "4 call points + 2 put points, labels match score_to_tier edges OK")

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# SPY / HISA reference table
# ---------------------------------------------------------------------------
def run_spy_hisa(log_path, out_csv):
    """For each PHASE_D_WINDOWS_12 window: SPY buy-and-hold return over the
    window's exact [start,end] (price_history adjusted `close`, first/last
    row on or nearest those dates) and HISA 4%/yr compounded over the same
    span (calendar days / 365.25). Quick db-light read, no MC import."""
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    import monte_carlo as mc
    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee("SPY-HISA reference table build", log_path)

    rows = []
    for label in PHASE_D_WINDOWS_12:
        d_start, d_end = window_lookup[label]
        cur = DB.execute_sql(
            "SELECT date, close FROM price_history WHERE symbol='SPY' AND date>=%s "
            "ORDER BY date ASC LIMIT 1", (d_start,))
        r0 = cur.fetchone()
        cur = DB.execute_sql(
            "SELECT date, close FROM price_history WHERE symbol='SPY' AND date<=%s "
            "ORDER BY date DESC LIMIT 1", (d_end,))
        r1 = cur.fetchone()
        if not r0 or not r1:
            _tee(f"[SPY-HISA] window={label}: MISSING SPY price_history row(s) in range -- r0={r0} r1={r1}", log_path)
            rows.append({'window': label, 'start_date': str(d_start), 'end_date': str(d_end),
                        'spy_start_close': None, 'spy_end_close': None, 'spy_actual_start': None,
                        'spy_actual_end': None, 'spy_return_pct': None, 'span_days': None, 'hisa_return_pct': None})
            continue
        spy_start_date, spy_start_close = r0
        spy_end_date, spy_end_close = r1
        spy_ret_pct = (float(spy_end_close) / float(spy_start_close) - 1.0) * 100.0
        span_days = (d_end - d_start).days
        hisa_ret_pct = ((1.04) ** (span_days / 365.25) - 1.0) * 100.0
        _tee(f"[SPY-HISA] window={label} [{d_start}..{d_end}] spy=[{spy_start_date}:{spy_start_close} -> "
            f"{spy_end_date}:{spy_end_close}] spy_return={spy_ret_pct:+.2f}% span_days={span_days} "
            f"hisa_return={hisa_ret_pct:+.2f}%", log_path)
        rows.append({
            'window': label, 'start_date': str(d_start), 'end_date': str(d_end),
            'spy_start_close': float(spy_start_close), 'spy_end_close': float(spy_end_close),
            'spy_actual_start': str(spy_start_date), 'spy_actual_end': str(spy_end_date),
            'spy_return_pct': spy_ret_pct, 'span_days': span_days, 'hisa_return_pct': hisa_ret_pct,
        })

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['window', 'start_date', 'end_date', 'spy_start_close', 'spy_end_close',
                     'spy_actual_start', 'spy_actual_end', 'spy_return_pct', 'span_days', 'hisa_return_pct']
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    _tee(f"[WRITE] {out_csv} ({len(rows)} rows)", log_path)
    return rows


def load_spy_hisa():
    path = os.path.join(OUT_DIR, 'spy_hisa_reference.csv')
    with open(path, newline='', encoding='utf-8') as f:
        return {r['window']: r for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# F0 -- tape decomposition (report-only)
# ---------------------------------------------------------------------------
def run_f0(log_path, out_csv_bands, out_csv_liq):
    """85+ trades: sentinel_guards' own gs1_5y.parquet tape (broadest single-
    file coverage of the 5y span, avoids cross-window double counting).
    75-84 population: fresh scores DB pull, same date span as the 5y window,
    version_id=74. Both enriched with scores' own component columns
    (bb/ma20/trend/volume/rsi/macd/stoch/technical_alignment) and
    regime_composite/regime_multiplier (already-stored columns). Liquidity
    tier: dollar-volume terciles (price_history.volume*close) within the
    UNION of both populations -- a first-pass proxy, explicitly NOT the F5-
    validated one (F5 runs after this)."""
    import polars as pl
    import statistics as stats
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee("F0 tape decomposition", log_path)

    tape_path = os.path.join(GUARDS_TAPES_DIR, 'gs1_5y.parquet')
    if not os.path.isfile(tape_path):
        _tee(f"[STOP] {tape_path} missing -- sentinel_guards_2026_08 must have run first", log_path)
        raise SystemExit(1)
    tape = pl.read_parquet(tape_path)
    trades85 = tape.select(['sym_id', 'entry_date', 'score', 'tier']).unique()
    _tee(f"[F0] gs1_5y.parquet: {len(tape)} trade rows, {len(trades85)} distinct (symbol,date,score,tier) "
        f"85+ trade tuples", log_path)

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    import monte_carlo as mc
    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    d_start, d_end = window_lookup['5y']

    cur = DB.execute_sql(
        "SELECT symbol, date, overall, bb, ma20, trend, volume, rsi, macd, stoch, "
        "technical_alignment, regime_composite, regime_multiplier FROM scores "
        "WHERE version_id=74 AND overall BETWEEN 75 AND 84 AND date BETWEEN %s AND %s",
        (str(d_start), str(d_end)))
    rows_75_84 = cur.fetchall()
    _tee(f"[F0] 75-84 population, 5y span [{d_start}..{d_end}]: {len(rows_75_84)} scored rows", log_path)

    cols_75_84 = ['symbol', 'date', 'overall', 'bb', 'ma20', 'trend', 'volume', 'rsi', 'macd', 'stoch',
                 'technical_alignment', 'regime_composite', 'regime_multiplier']
    df_75_84 = pl.DataFrame([dict(zip(cols_75_84, r)) for r in rows_75_84]) if rows_75_84 else pl.DataFrame(schema={c: pl.Float64 for c in cols_75_84})

    # Enrich the 85+ trade tuples with the same component columns via a
    # targeted per-row SQL join (small, distinct set -- same "quick direct
    # query" pattern used throughout this program for targeted lookups).
    enriched_85 = []
    for row in trades85.iter_rows(named=True):
        sym, d, score, tier = row['sym_id'], row['entry_date'], row['score'], row['tier']
        cur = DB.execute_sql(
            "SELECT bb, ma20, trend, volume, rsi, macd, stoch, technical_alignment, "
            "regime_composite, regime_multiplier FROM scores WHERE version_id=74 AND symbol=%s AND date=%s",
            (sym, d))
        r = cur.fetchone()
        if r is None:
            continue
        subband = '88+' if score >= 88 else '85-87'
        enriched_85.append({
            'symbol': sym, 'date': d, 'overall': score, 'tier': tier, 'subband': subband,
            'bb': r[0], 'ma20': r[1], 'trend': r[2], 'volume': r[3], 'rsi': r[4], 'macd': r[5],
            'stoch': r[6], 'technical_alignment': r[7], 'regime_composite': r[8], 'regime_multiplier': r[9],
        })
    df_85 = pl.DataFrame(enriched_85)
    _tee(f"[F0] enriched {len(df_85)}/{len(trades85)} 85+ trade tuples with component/regime data "
        f"(missing = score row not found, e.g. version drift)", log_path)

    # Liquidity: dollar-volume from price_history for the UNION of both populations' (symbol,date) keys.
    all_keys = [(r['symbol'], r['date']) for r in enriched_85] + [(str(r[0]), str(r[1])) for r in rows_75_84]
    dv = {}
    for sym, d in set(all_keys):
        cur = DB.execute_sql("SELECT close, volume FROM price_history WHERE symbol=%s AND date=%s", (sym, d))
        r = cur.fetchone()
        if r and r[0] is not None and r[1] is not None:
            dv[(sym, d)] = float(r[0]) * float(r[1])
    dv_vals = sorted(dv.values())
    _tee(f"[F0] dollar-volume resolved for {len(dv)}/{len(set(all_keys))} distinct (symbol,date) keys", log_path)
    if dv_vals:
        t1 = dv_vals[len(dv_vals) // 3]
        t2 = dv_vals[2 * len(dv_vals) // 3]
    else:
        t1 = t2 = 0

    def _liq_tier(sym, d):
        v = dv.get((sym, d))
        if v is None:
            return 'unknown'
        if v <= t1:
            return 'low'
        if v <= t2:
            return 'mid'
        return 'high'

    # Band-level decomposition table.
    band_rows = []
    for band_name, records in (
        ('88+', [r for r in enriched_85 if r['subband'] == '88+']),
        ('85-87', [r for r in enriched_85 if r['subband'] == '85-87']),
        ('75-84', [{'symbol': str(r[0]), 'date': str(r[1]), 'overall': r[2], 'bb': r[3], 'ma20': r[4],
                   'trend': r[5], 'volume': r[6], 'rsi': r[7], 'macd': r[8], 'stoch': r[9],
                   'technical_alignment': r[10], 'regime_composite': r[11], 'regime_multiplier': r[12]}
                  for r in rows_75_84]),
    ):
        if not records:
            band_rows.append({'band': band_name, 'n': 0})
            continue
        liq_counts = defaultdict(int)
        for r in records:
            liq_counts[_liq_tier(r['symbol'], r['date'])] += 1
        n = len(records)
        row = {'band': band_name, 'n': n}
        for comp in ('bb', 'ma20', 'trend', 'volume', 'rsi', 'macd', 'stoch', 'technical_alignment',
                    'regime_composite', 'regime_multiplier'):
            vals = [float(r[comp]) for r in records if r.get(comp) is not None]
            row[f'mean_{comp}'] = stats.mean(vals) if vals else None
        for tier in ('low', 'mid', 'high', 'unknown'):
            row[f'liq_{tier}_pct'] = liq_counts.get(tier, 0) / n * 100.0
        band_rows.append(row)
        _tee(f"[F0-BAND] band={band_name} n={n} liq_low%={row.get('liq_low_pct'):.1f} "
            f"liq_mid%={row.get('liq_mid_pct'):.1f} liq_high%={row.get('liq_high_pct'):.1f} "
            f"liq_unknown%={row.get('liq_unknown_pct'):.1f} mean_trend={row.get('mean_trend')} "
            f"mean_regime_composite={row.get('mean_regime_composite')}", log_path)

    os.makedirs(os.path.dirname(out_csv_bands), exist_ok=True)
    fieldnames = ['band', 'n'] + [f'mean_{c}' for c in ('bb', 'ma20', 'trend', 'volume', 'rsi', 'macd',
                 'stoch', 'technical_alignment', 'regime_composite', 'regime_multiplier')] + \
                [f'liq_{t}_pct' for t in ('low', 'mid', 'high', 'unknown')]
    with open(out_csv_bands, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in band_rows:
            w.writerow({k: r.get(k) for k in fieldnames})
    _tee(f"[WRITE] {out_csv_bands} ({len(band_rows)} rows)", log_path)

    # Anomaly check: tier vs score mismatch (flagged in the prior campaign's raw tape read).
    mismatches = [r for r in enriched_85 if (r['tier'] == 'ultra' and r['overall'] < 95) or
                 (r['tier'] == 'top' and not (85 <= r['overall'] <= 94))]
    _tee(f"[F0-ANOMALY] {len(mismatches)} tape rows where tier label does not match score_to_tier(overall) "
        f"bands (ultra should be >=95, top should be 85-94): "
        f"{[(m['symbol'], m['date'], m['overall'], m['tier']) for m in mismatches[:20]]}"
        f"{' ...(truncated)' if len(mismatches) > 20 else ''}", log_path)

    return band_rows, mismatches


def run_f0_rerun(log_path, out_csv_bands):
    """AMENDMENT-1 point 3: rerun F0's component table with CTSL-promoted
    trades (tape ct=='ct_call') pulled into their OWN band, bucketed by
    MECHANISM rather than raw score -- the original run's 85-87/88+ bands
    were contaminated by promoted trades whose funding tier had nothing to
    do with their raw score. Same source data (gs1_5y.parquet + fresh 75-84
    scores pull) as run_f0, re-bucketed."""
    import polars as pl
    import statistics as stats
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee("F0-RERUN component table, CTSL-promoted trades as their own bucket", log_path)

    tape_path = os.path.join(GUARDS_TAPES_DIR, 'gs1_5y.parquet')
    if not os.path.isfile(tape_path):
        _tee(f"[STOP] {tape_path} missing", log_path)
        raise SystemExit(1)
    tape = pl.read_parquet(tape_path)
    trades85 = tape.select(['sym_id', 'entry_date', 'score', 'tier', 'ct']).unique()
    _tee(f"[F0-RERUN] gs1_5y.parquet: {len(trades85)} distinct (symbol,date,score,tier,ct) 85+ trade tuples", log_path)

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    import monte_carlo as mc
    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    d_start, d_end = window_lookup['5y']

    cur = DB.execute_sql(
        "SELECT symbol, date, overall, bb, ma20, trend, volume, rsi, macd, stoch, "
        "technical_alignment, regime_composite, regime_multiplier FROM scores "
        "WHERE version_id=74 AND overall BETWEEN 75 AND 84 AND date BETWEEN %s AND %s",
        (str(d_start), str(d_end)))
    rows_75_84 = cur.fetchall()
    _tee(f"[F0-RERUN] 75-84 population: {len(rows_75_84)} scored rows", log_path)

    enriched_85 = []
    for row in trades85.iter_rows(named=True):
        sym, d, score, tier, ct = row['sym_id'], row['entry_date'], row['score'], row['tier'], row['ct']
        cur = DB.execute_sql(
            "SELECT bb, ma20, trend, volume, rsi, macd, stoch, technical_alignment, "
            "regime_composite, regime_multiplier FROM scores WHERE version_id=74 AND symbol=%s AND date=%s",
            (sym, d))
        r = cur.fetchone()
        if r is None:
            continue
        if ct == 'ct_call':
            mech_band = 'ctsl_promoted'
        elif score >= 88:
            mech_band = 'score_88+'
        else:
            mech_band = 'score_85-87'
        enriched_85.append({
            'symbol': sym, 'date': d, 'overall': score, 'tier': tier, 'ct': ct, 'mech_band': mech_band,
            'bb': r[0], 'ma20': r[1], 'trend': r[2], 'volume': r[3], 'rsi': r[4], 'macd': r[5],
            'stoch': r[6], 'technical_alignment': r[7], 'regime_composite': r[8], 'regime_multiplier': r[9],
        })
    n_promoted = sum(1 for r in enriched_85 if r['mech_band'] == 'ctsl_promoted')
    _tee(f"[F0-RERUN] enriched {len(enriched_85)}/{len(trades85)} tuples; "
        f"{n_promoted} ({n_promoted/len(enriched_85)*100:.1f}%) are CTSL-promoted (ct=='ct_call')", log_path)

    all_keys = [(r['symbol'], r['date']) for r in enriched_85] + [(str(r[0]), str(r[1])) for r in rows_75_84]
    dv = {}
    for sym, d in set(all_keys):
        cur = DB.execute_sql("SELECT close, volume FROM price_history WHERE symbol=%s AND date=%s", (sym, d))
        r = cur.fetchone()
        if r and r[0] is not None and r[1] is not None:
            dv[(sym, d)] = float(r[0]) * float(r[1])
    dv_vals = sorted(dv.values())
    t1 = dv_vals[len(dv_vals) // 3] if dv_vals else 0
    t2 = dv_vals[2 * len(dv_vals) // 3] if dv_vals else 0

    def _liq_tier(sym, d):
        v = dv.get((sym, d))
        if v is None:
            return 'unknown'
        if v <= t1:
            return 'low'
        if v <= t2:
            return 'mid'
        return 'high'

    band_rows = []
    for band_name, records in (
        ('ctsl_promoted', [r for r in enriched_85 if r['mech_band'] == 'ctsl_promoted']),
        ('score_88+', [r for r in enriched_85 if r['mech_band'] == 'score_88+']),
        ('score_85-87', [r for r in enriched_85 if r['mech_band'] == 'score_85-87']),
        ('75-84', [{'symbol': str(r[0]), 'date': str(r[1]), 'overall': r[2], 'bb': r[3], 'ma20': r[4],
                   'trend': r[5], 'volume': r[6], 'rsi': r[7], 'macd': r[8], 'stoch': r[9],
                   'technical_alignment': r[10], 'regime_composite': r[11], 'regime_multiplier': r[12]}
                  for r in rows_75_84]),
    ):
        if not records:
            band_rows.append({'band': band_name, 'n': 0})
            continue
        liq_counts = defaultdict(int)
        for r in records:
            liq_counts[_liq_tier(r['symbol'], r['date'])] += 1
        n = len(records)
        row = {'band': band_name, 'n': n}
        for comp in ('bb', 'ma20', 'trend', 'volume', 'rsi', 'macd', 'stoch', 'technical_alignment',
                    'regime_composite', 'regime_multiplier'):
            vals = [float(r[comp]) for r in records if r.get(comp) is not None]
            row[f'mean_{comp}'] = stats.mean(vals) if vals else None
        for tier in ('low', 'mid', 'high', 'unknown'):
            row[f'liq_{tier}_pct'] = liq_counts.get(tier, 0) / n * 100.0
        mean_overall = stats.mean([float(r['overall']) for r in records if r.get('overall') is not None])
        row['mean_overall'] = mean_overall
        band_rows.append(row)
        _tee(f"[F0-RERUN-BAND] band={band_name} n={n} mean_overall={mean_overall:.2f} mean_trend={row.get('mean_trend')} "
            f"mean_regime_composite={row.get('mean_regime_composite')} liq_low%={row.get('liq_low_pct'):.1f} "
            f"liq_mid%={row.get('liq_mid_pct'):.1f} liq_high%={row.get('liq_high_pct'):.1f}", log_path)

    os.makedirs(os.path.dirname(out_csv_bands), exist_ok=True)
    fieldnames = ['band', 'n', 'mean_overall'] + [f'mean_{c}' for c in ('bb', 'ma20', 'trend', 'volume', 'rsi',
                 'macd', 'stoch', 'technical_alignment', 'regime_composite', 'regime_multiplier')] + \
                [f'liq_{t}_pct' for t in ('low', 'mid', 'high', 'unknown')]
    with open(out_csv_bands, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in band_rows:
            w.writerow({k: r.get(k) for k in fieldnames})
    _tee(f"[WRITE] {out_csv_bands} ({len(band_rows)} rows)", log_path)
    return band_rows


# ---------------------------------------------------------------------------
# AMENDMENT-1 tier-native cell worker -- covers F1', F1'', F2' (survivor/
# buf20 on the tier grid), F3' (levers on F2' passers), F4' (tier-native put
# funding). FULL ctx always (no min/max-score ctx filter -- that mechanism is
# retired for anything claiming to be "Sentinel shape"). Selectivity is
# ALWAYS via TIER_ALLOC/PUT_TIER_ALLOC funding zeroing, exactly R1's own
# mechanism, generalized to the other tier-quantized points.
# ---------------------------------------------------------------------------
TIER_SCHEMA = [
    'phase', 'tier_point', 'put_tier_point', 'window', 'universe', 'lens',
    'dte', 'hold_days', 'gross', 'puts_enabled', 'ct_promote',
    'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'worst_dd', 'mean_dd', 'p_coll',
    'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s', 'n_calls_delisted',
    'tier_ultra', 'tier_top', 'tier_mid', 'tier_low',
    'put_tier_top', 'put_tier_mid', 'put_tier_low',
    'max_positions', 'gross_cap', 'capital_ceiling', 'min_score_funded',
    'miss_p', 'gap_aware', 'avg_open_premium_base_pct', 'max_open_premium_base_pct',
    'mean_trades_per_path', 'median_trades_per_path', 'n_paths_with_trades',
    'n_ct_call_tape_rows', 'spy_return_pct', 'hisa_return_pct',
]


def run_tier_cell(phase, tier_point, window, n_iter, out_csv, log_path, job,
                  universe='full', lens='calibrated', dte=None, gross=None,
                  puts_enabled=False, put_tier_point=None, ct_promote=True):
    """tier_point: key into TIER_FUNDING_POINTS, or None to mean "use
    PROFILE_ENV['sentinel'] literally, unmodified" (the identity-anchor path
    -- byte-identical env construction to flipR_run.py/guardS_run.py's own
    Sentinel cells, not merely equivalent numbers)."""
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing")

    survivor_set = _load_survivor_set(SURVIVOR_FILE) if universe == 'survivor' else None

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    if tier_point is None:
        for k, v in SENTINEL_SHAPE_BASE.items():
            os.environ[k] = v
    else:
        for k, v in SENTINEL_SHAPE_BASE.items():
            if k not in ('TIER_ULTRA_OV', 'TIER_TOP_OV', 'TIER_MID_OV', 'TIER_LOW_OV'):
                os.environ[k] = v
        pt = TIER_FUNDING_POINTS[tier_point]
        os.environ['TIER_ULTRA_OV'] = str(pt['ultra'])
        os.environ['TIER_TOP_OV'] = str(pt['top'])
        os.environ['TIER_MID_OV'] = str(pt['mid'])
        os.environ['TIER_LOW_OV'] = str(pt['low'])
    lens_env = LENS_ENV['calibrated'] if lens == 'calibrated' else LENS_STRESS_020
    for k, v in lens_env.items():
        os.environ[k] = v
    if dte is not None:
        os.environ['NOMINAL_CAL_DTE'] = str(dte)
        os.environ['HOLD_CAL_DAYS'] = str(dte - 3)
    if gross is not None:
        os.environ['GROSS_PREMIUM_CAP'] = str(gross)
        os.environ['CALL_PREMIUM_CAP'] = str(gross)
    if puts_enabled and put_tier_point is not None:
        # F4' is a PURE put arm ("put-Sentinel shape", not a call+put hybrid)
        # -- zero the call side entirely so only puts fund, mirroring how
        # Sentinel's own call-only shape zeros mid/low. Without this, every
        # F4' cell would silently ALSO run Sentinel's full 0.20/0.15 call
        # cascade underneath the put measurement (caught before running,
        # never executed with the bug live).
        os.environ['TIER_ULTRA_OV'] = '0'
        os.environ['TIER_TOP_OV'] = '0'
        os.environ['TIER_MID_OV'] = '0'
        os.environ['TIER_LOW_OV'] = '0'
        # NOTE the real env names are PUT_TIER_TOP_OV/PUT_TIER_MID_OV/
        # PUT_TIER_LOW_OV (monte_carlo.py:1339-1341) -- 'TIER' in the middle.
        # An earlier version of this line built f'{k.upper()}_OV' from keys
        # 'put_top'/'put_mid'/'put_low', producing PUT_TOP_OV (wrong, missing
        # TIER) -- silently a no-op env var, so every put allocation stayed
        # at its 0.0 default. Caught via PUT_TIER_ALLOC self-logging showing
        # all-zero on a 'put-fund-all' cell; fixed with an explicit map.
        _PUT_ENV_NAME = {'put_top': 'PUT_TIER_TOP_OV', 'put_mid': 'PUT_TIER_MID_OV', 'put_low': 'PUT_TIER_LOW_OV'}
        for k, v in PUT_TIER_FUNDING_POINTS[put_tier_point].items():
            os.environ[_PUT_ENV_NAME[k]] = str(v)
        # SECOND blocker found only by direct diagnostic (PUT_TIER_ALLOC alone
        # was not sufficient): MAX_POSITIONS_PUT is a SEPARATE hard position
        # cap (monte_carlo.py:578, env name has NO _OV suffix, unlike the
        # tier knobs), defaulting to strategy_config's shipped 0 (puts off).
        # With cap_put=0, _try_fill_put's at_cap check is always true
        # (put_open>=0), so every put attempt fails regardless of allocation
        # -- confirmed live: PUT_TIER_ALLOC correctly non-zero, 58,594 put
        # signals loaded, yet 0 trades, until this was set. Mirrors
        # Sentinel's own call-side MAX_POSITIONS=14 (calls are zeroed for
        # this pure-put arm, so 14 slots go entirely to puts).
        os.environ['MAX_POSITIONS_PUT'] = '14'
    os.environ['CT_PROMOTE'] = '1' if ct_promote else '0'
    os.environ['MC_TRADE_TAPE'] = '1'
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    if not puts_enabled:
        mc_patch.disable_puts(mc)
    assert mc.CT_PROMOTE == ct_promote, f"[STOP] mc.CT_PROMOTE={mc.CT_PROMOTE} != requested {ct_promote}"

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if window not in window_lookup:
        raise SystemExit(f"[STOP] window {window!r} not in mc.WINDOWS")
    d_start, d_end = window_lookup[window]
    mc.N_ITER = n_iter

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"TIER-CELL job={job} phase={phase} tier_point={tier_point} put_tier_point={put_tier_point} "
        f"window={window} universe={universe} lens={lens} dte={dte} gross={gross} "
        f"puts_enabled={puts_enabled} ct_promote={ct_promote} n_iter={n_iter} pid={os.getpid()}", log_path)
    live_min_score = min_score_funded(mc.TIER_ALLOC)
    _tee(f"[ENGAGED-CONFIG] tier_ultra={mc.TIER_ALLOC.get('ultra')} tier_top={mc.TIER_ALLOC.get('top')} "
        f"tier_mid={mc.TIER_ALLOC.get('mid')} tier_low={mc.TIER_ALLOC.get('low')} "
        f"min_score_funded={live_min_score} max_positions={mc.MAX_POSITIONS_CALL or mc.MAX_POSITIONS} "
        f"gross_cap={mc.GROSS_PREMIUM_CAP} nominal_cal_dte={mc.NOMINAL_CAL_DTE} hold_cal_days={mc.HOLD_CAL_DAYS} "
        f"miss_p={getattr(mc,'TP_FILL_MISS_P',None)} gap_aware={getattr(mc,'TP_FILL_GAP_AWARE',None)} "
        f"CT_PROMOTE(live)={mc.CT_PROMOTE}", log_path)
    if puts_enabled:
        _tee(f"[ENGAGED-CONFIG] PUT_TIER_ALLOC live={mc.PUT_TIER_ALLOC}", log_path)

    t0 = time.perf_counter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx = mc._prepare_window(window, d_start, d_end, version_meta['id'])
    t_prepare = time.perf_counter() - t0
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(buf.getvalue())

    n_delisted_dropped = None
    if universe == 'survivor':
        ctx, n_bf, n_af, n_delisted_dropped = apply_survivor_filter(ctx, survivor_set)
        _tee(f"[SURVIVOR-FILTER] window={window} before={n_bf} after={n_af} dropped={n_delisted_dropped}", log_path)

    call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
    n_calls_delisted = sum(1 for s in call_syms if survivor_set is not None and str(s).upper() not in survivor_set)
    if universe == 'survivor':
        assert n_calls_delisted == 0, f"[STOP] survivor filter left {n_calls_delisted} delisted symbols"

    t1 = time.perf_counter()
    with open(log_path, 'a', encoding='utf-8') as log_f:
        with redirect_stdout(log_f):
            sim = mc._simulate_window(ctx)
    t_sim = time.perf_counter() - t1
    result = sim['seeded']

    n_c, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

    tape_src = os.path.join(DD_LEDGER_DIR, f'tape_{window}.parquet')
    mean_tpp = median_tpp = n_paths = n_ct_call_rows = None
    tape_dst = None
    if os.path.isfile(tape_src):
        os.makedirs(TAPES_DIR, exist_ok=True)
        tape_dst = os.path.join(TAPES_DIR, f'{job}_{window}.parquet')
        shutil.move(tape_src, tape_dst)
        try:
            import polars as pl
            import statistics as stats
            df = pl.read_parquet(tape_dst)
            if len(df) > 0:
                per_seed = df.group_by('seed').len()['len'].to_list()
                n_paths = len(per_seed)
                mean_tpp = stats.mean(per_seed)
                median_tpp = stats.median(per_seed)
                n_ct_call_rows = int((df['ct'] == 'ct_call').sum()) if 'ct' in df.columns else None
        except Exception as e:
            _tee(f"[TAPE] WARNING failed to aggregate {tape_dst}: {e}", log_path)

    if ct_promote is False:
        assert (n_ct_call_rows is None or n_ct_call_rows == 0), \
            f"[STOP] CT_PROMOTE=0 but tape still has {n_ct_call_rows} ct_call rows -- off-switch did not engage"
        _tee(f"[CTSL-OFF-ASSERT] window={window} n_ct_call_tape_rows={n_ct_call_rows} (must be 0/None) -- PASS", log_path)

    spy_hisa = load_spy_hisa().get(window, {})

    row = {
        'phase': phase, 'tier_point': tier_point, 'put_tier_point': put_tier_point, 'window': window,
        'universe': universe, 'lens': lens, 'dte': mc.NOMINAL_CAL_DTE, 'hold_days': mc.HOLD_CAL_DAYS,
        'gross': mc.GROSS_PREMIUM_CAP, 'puts_enabled': puts_enabled, 'ct_promote': ct_promote,
        'tp': mc.TP_BASE, 'sl': mc.SL_BASE, 'n_iter': n_iter, 'n_call_signals': len(ctx['call_outcomes']),
        'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
        'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'), 'p_coll': result.get('p_coll'),
        'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
        'n_calls_delisted': n_calls_delisted,
        'tier_ultra': mc.TIER_ALLOC.get('ultra'), 'tier_top': mc.TIER_ALLOC.get('top'),
        'tier_mid': mc.TIER_ALLOC.get('mid'), 'tier_low': mc.TIER_ALLOC.get('low'),
        'put_tier_top': mc.PUT_TIER_ALLOC.get('put_top') if puts_enabled else None,
        'put_tier_mid': mc.PUT_TIER_ALLOC.get('put_mid') if puts_enabled else None,
        'put_tier_low': mc.PUT_TIER_ALLOC.get('put_low') if puts_enabled else None,
        'max_positions': mc.MAX_POSITIONS_CALL or mc.MAX_POSITIONS, 'gross_cap': mc.GROSS_PREMIUM_CAP,
        'capital_ceiling': mc.PRACTICAL_CAPITAL_CEILING, 'min_score_funded': live_min_score,
        'miss_p': getattr(mc, 'TP_FILL_MISS_P', None), 'gap_aware': getattr(mc, 'TP_FILL_GAP_AWARE', None),
        'avg_open_premium_base_pct': result.get('avg_open_premium_base_pct'),
        'max_open_premium_base_pct': result.get('max_open_premium_base_pct'),
        'mean_trades_per_path': mean_tpp, 'median_trades_per_path': median_tpp, 'n_paths_with_trades': n_paths,
        'n_ct_call_tape_rows': n_ct_call_rows,
        'spy_return_pct': spy_hisa.get('spy_return_pct'), 'hisa_return_pct': spy_hisa.get('hisa_return_pct'),
    }

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    csv_is_new = not os.path.exists(out_csv)
    with open(out_csv, 'a', newline='', encoding='utf-8') as csv_f:
        csv_w = csv.DictWriter(csv_f, fieldnames=TIER_SCHEMA)
        if csv_is_new:
            csv_w.writeheader()
        csv_w.writerow(row)

    _tee(f"[DONE] phase={phase} tier_point={tier_point} window={window} universe={universe} lens={lens} "
        f"ct_promote={ct_promote} n_call_signals={row['n_call_signals']} med_ret={result.get('med_ret'):+.2f}% "
        f"worst_dd={result.get('worst_dd'):.2f}% p_coll={result.get('p_coll'):.2f}% "
        f"median_trades_per_path={median_tpp} n_ct_call_tape_rows={n_ct_call_rows} spy={row['spy_return_pct']} "
        f"tape={tape_dst} -> {out_csv}", log_path)
    return row


# ---------------------------------------------------------------------------
# Generic cell worker -- covers F1, F2 (survivor/buf20), F3, F4.
# ---------------------------------------------------------------------------
FULL_SCHEMA = [
    'phase', 'threshold_kind', 'threshold', 'window', 'universe', 'lens',
    'dte', 'hold_days', 'gross', 'puts_enabled',
    'tp', 'sl', 'n_iter', 'n_call_signals', 'n_call_signals_before_filter',
    'mean_ret', 'med_ret', 'worst_dd', 'mean_dd', 'p_coll',
    'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s', 'n_calls_delisted',
    'max_positions', 'gross_cap', 'capital_ceiling', 'min_score_funded',
    'miss_p', 'gap_aware', 'avg_open_premium_base_pct', 'max_open_premium_base_pct',
    'mean_trades_per_path', 'median_trades_per_path', 'n_paths_with_trades',
    'spy_return_pct', 'hisa_return_pct',
]


def run_one_cell(phase, threshold_kind, threshold, window, n_iter, out_csv, log_path, job,
                 universe='full', lens='calibrated', dte=None, gross=None, puts_enabled=False,
                 max_score_filter=None):
    """Universal cell worker.
    phase: 'f1'|'f2s'|'f2b'|'f3'|'f4' (report tag only).
    threshold_kind: 'min_score'|'max_score'|'carrier' (which filter `threshold` drives).
    universe: 'full'|'survivor'.
    lens: 'calibrated'|'buf20'.
    dte/gross: F3 overrides (None = Sentinel shape's own 30/0.30).
    puts_enabled: F4 -- skips disable_puts(), sets PUT_SENTINEL_ENV.
    max_score_filter: F4's max-score=12 sub-case, applied ON TOP of the
      native put_score_to_tier<=15 population (an additional ctx filter).
    """
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing")

    survivor_set = _load_survivor_set(SURVIVOR_FILE) if universe == 'survivor' else None

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)
    for k, v in SENTINEL_SHAPE_BASE.items():
        os.environ[k] = v
    lens_env = LENS_ENV['calibrated'] if lens == 'calibrated' else LENS_STRESS_020
    for k, v in lens_env.items():
        os.environ[k] = v
    if dte is not None:
        os.environ['NOMINAL_CAL_DTE'] = str(dte)
        os.environ['HOLD_CAL_DAYS'] = str(dte - 3)
    if gross is not None:
        os.environ['GROSS_PREMIUM_CAP'] = str(gross)
        os.environ['CALL_PREMIUM_CAP'] = str(gross)
    if puts_enabled:
        for k, v in PUT_SENTINEL_ENV.items():
            os.environ[k] = v
    os.environ['MC_TRADE_TAPE'] = '1'
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    if not puts_enabled:
        mc_patch.disable_puts(mc)

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if window not in window_lookup:
        raise SystemExit(f"[STOP] window {window!r} not in mc.WINDOWS")
    d_start, d_end = window_lookup[window]
    mc.N_ITER = n_iter

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"CELL job={job} phase={phase} threshold_kind={threshold_kind} threshold={threshold} "
        f"window={window} universe={universe} lens={lens} dte={dte} gross={gross} "
        f"puts_enabled={puts_enabled} n_iter={n_iter} pid={os.getpid()}", log_path)
    live_min_score = min_score_funded(mc.TIER_ALLOC)
    _tee(f"[ENGAGED-CONFIG] tier_ultra={mc.TIER_ALLOC.get('ultra')} tier_top={mc.TIER_ALLOC.get('top')} "
        f"tier_mid={mc.TIER_ALLOC.get('mid')} tier_low={mc.TIER_ALLOC.get('low')} "
        f"min_score_funded_via_tiers={live_min_score} max_positions={mc.MAX_POSITIONS_CALL or mc.MAX_POSITIONS} "
        f"gross_cap={mc.GROSS_PREMIUM_CAP} nominal_cal_dte={mc.NOMINAL_CAL_DTE} hold_cal_days={mc.HOLD_CAL_DAYS} "
        f"miss_p={getattr(mc,'TP_FILL_MISS_P',None)} gap_aware={getattr(mc,'TP_FILL_GAP_AWARE',None)}", log_path)
    if puts_enabled:
        _tee(f"[ENGAGED-CONFIG] PUT_TIER_ALLOC live={mc.PUT_TIER_ALLOC}", log_path)

    t0 = time.perf_counter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx = mc._prepare_window(window, d_start, d_end, version_meta['id'])
    t_prepare = time.perf_counter() - t0
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(buf.getvalue())

    n_before_filter = len(ctx['call_outcomes'])
    if threshold_kind == 'min_score' and threshold is not None:
        omap = overall_map_from_ctx(ctx)
        ctx, n_before_filter, _ = apply_min_score_filter(ctx, threshold, omap)
    if max_score_filter is not None:
        omap = overall_map_from_ctx(ctx)
        ctx, _, _ = apply_max_score_filter(ctx, max_score_filter, omap)

    n_delisted_dropped = None
    if universe == 'survivor':
        ctx, n_bf, n_af, n_delisted_dropped = apply_survivor_filter(ctx, survivor_set)
        _tee(f"[SURVIVOR-FILTER] window={window} before={n_bf} after={n_af} dropped={n_delisted_dropped}", log_path)

    call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
    n_calls_delisted = sum(1 for s in call_syms if survivor_set is None or str(s).upper() not in survivor_set) \
        if survivor_set is not None else 0
    if universe == 'survivor':
        assert n_calls_delisted == 0, f"[STOP] survivor filter left {n_calls_delisted} delisted symbols"

    t1 = time.perf_counter()
    with open(log_path, 'a', encoding='utf-8') as log_f:
        with redirect_stdout(log_f):
            sim = mc._simulate_window(ctx)
    t_sim = time.perf_counter() - t1
    result = sim['seeded']

    n_c, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

    tape_src = os.path.join(DD_LEDGER_DIR, f'tape_{window}.parquet')
    mean_tpp = median_tpp = n_paths = None
    tape_dst = None
    if os.path.isfile(tape_src):
        os.makedirs(TAPES_DIR, exist_ok=True)
        tape_dst = os.path.join(TAPES_DIR, f'{job}_{window}.parquet')
        shutil.move(tape_src, tape_dst)
        try:
            import polars as pl
            import statistics as stats
            df = pl.read_parquet(tape_dst)
            if len(df) > 0:
                per_seed = df.group_by('seed').len()['len'].to_list()
                n_paths = len(per_seed)
                mean_tpp = stats.mean(per_seed)
                median_tpp = stats.median(per_seed)
        except Exception as e:
            _tee(f"[TAPE] WARNING failed to aggregate {tape_dst}: {e}", log_path)

    spy_hisa = load_spy_hisa().get(window, {})

    row = {
        'phase': phase, 'threshold_kind': threshold_kind, 'threshold': threshold, 'window': window,
        'universe': universe, 'lens': lens, 'dte': mc.NOMINAL_CAL_DTE, 'hold_days': mc.HOLD_CAL_DAYS,
        'gross': mc.GROSS_PREMIUM_CAP, 'puts_enabled': puts_enabled,
        'tp': mc.TP_BASE, 'sl': mc.SL_BASE, 'n_iter': n_iter, 'n_call_signals': len(ctx['call_outcomes']),
        'n_call_signals_before_filter': n_before_filter,
        'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
        'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'), 'p_coll': result.get('p_coll'),
        'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
        'n_calls_delisted': n_calls_delisted,
        'max_positions': mc.MAX_POSITIONS_CALL or mc.MAX_POSITIONS, 'gross_cap': mc.GROSS_PREMIUM_CAP,
        'capital_ceiling': mc.PRACTICAL_CAPITAL_CEILING, 'min_score_funded': live_min_score,
        'miss_p': getattr(mc, 'TP_FILL_MISS_P', None), 'gap_aware': getattr(mc, 'TP_FILL_GAP_AWARE', None),
        'avg_open_premium_base_pct': result.get('avg_open_premium_base_pct'),
        'max_open_premium_base_pct': result.get('max_open_premium_base_pct'),
        'mean_trades_per_path': mean_tpp, 'median_trades_per_path': median_tpp, 'n_paths_with_trades': n_paths,
        'spy_return_pct': spy_hisa.get('spy_return_pct'), 'hisa_return_pct': spy_hisa.get('hisa_return_pct'),
    }

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    csv_is_new = not os.path.exists(out_csv)
    with open(out_csv, 'a', newline='', encoding='utf-8') as csv_f:
        csv_w = csv.DictWriter(csv_f, fieldnames=FULL_SCHEMA)
        if csv_is_new:
            csv_w.writeheader()
        csv_w.writerow(row)

    _tee(f"[DONE] phase={phase} threshold={threshold} window={window} universe={universe} lens={lens} "
        f"n_call_signals={row['n_call_signals']} med_ret={result.get('med_ret'):+.2f}% "
        f"worst_dd={result.get('worst_dd'):.2f}% p_coll={result.get('p_coll'):.2f}% "
        f"median_trades_per_path={median_tpp} spy={row['spy_return_pct']} tape={tape_dst} -> {out_csv}", log_path)
    return row


# ---------------------------------------------------------------------------
# Orchestrator -- generic cell-list based, same fingerprint/close-boundary
# guard pattern as flipR_run.py/guardS_run.py.
# ---------------------------------------------------------------------------
def _load_json(path, default):
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return default


def _run_orchestrator(job, stage, cells, n_iter, log_path, state_path, out_csv):
    """cells: list of dicts with keys matching run_one_cell's kwargs plus a
    'window' key (used for fingerprint distinct-window scoping) and a 'key'
    tuple (used for state-file dedup/resume)."""
    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee(f"ORCHESTRATOR job={job} stage={stage} n_cells={len(cells)} n_iter={n_iter}", log_path)

    baseline_score_date = max_score_date()
    _tee(f"[CLOSE-BOUNDARY-GUARD] battery START max(Score.date)={baseline_score_date}", log_path)
    tainted_cells = []

    distinct_windows = sorted({c['window'] for c in cells})
    fp_start = compute_fingerprint(distinct_windows, log_path)
    _tee(f"[FINGERPRINT-GUARD] battery START per-window loaded>=70 counts: {fp_start}", log_path)

    t_job0 = time.time()
    for i, cell in enumerate(cells, 1):
        key = cell['key']
        if key in done_set:
            _tee(f"[{i}/{len(cells)}] SKIP (already done) {key}", log_path)
            continue

        current_score_date = max_score_date()
        if current_score_date != baseline_score_date:
            _tee(f"[CLOSE-BOUNDARY-FLAG] max(Score.date) changed {baseline_score_date} -> "
                f"{current_score_date} BEFORE cell {i}/{len(cells)} {key}", log_path)
            tainted_cells.append({'cell': list(key), 'index': i, 'reason': 'close-boundary',
                                  'old_max_date': str(baseline_score_date), 'new_max_date': str(current_score_date)})
            baseline_score_date = current_score_date

        _tee(f"[{i}/{len(cells)}] LAUNCH {key}", log_path)
        child_env = os.environ.copy()
        child_env['PYTHONIOENCODING'] = 'utf-8'
        if cell.get('mechanism') == 'tier':
            cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker-tier',
                  '--phase', cell['phase'],
                  '--tier-point', cell['tier_point'] if cell.get('tier_point') is not None else '',
                  '--window', cell['window'], '--n-iter', str(n_iter),
                  '--universe', cell.get('universe', 'full'), '--lens', cell.get('lens', 'calibrated'),
                  '--out-csv', out_csv, '--log-path', log_path, '--job', job]
            if cell.get('dte') is not None:
                cmd += ['--dte', str(cell['dte'])]
            if cell.get('gross') is not None:
                cmd += ['--gross', str(cell['gross'])]
            if cell.get('puts_enabled'):
                cmd += ['--puts-enabled']
            if cell.get('put_tier_point') is not None:
                cmd += ['--put-tier-point', cell['put_tier_point']]
            if cell.get('ct_promote') is False:
                cmd += ['--no-ct-promote']
        else:
            cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker',
                  '--phase', cell['phase'], '--threshold-kind', cell['threshold_kind'],
                  '--threshold', str(cell['threshold']) if cell['threshold'] is not None else '',
                  '--window', cell['window'], '--n-iter', str(n_iter),
                  '--universe', cell.get('universe', 'full'), '--lens', cell.get('lens', 'calibrated'),
                  '--out-csv', out_csv, '--log-path', log_path, '--job', job]
            if cell.get('dte') is not None:
                cmd += ['--dte', str(cell['dte'])]
            if cell.get('gross') is not None:
                cmd += ['--gross', str(cell['gross'])]
            if cell.get('puts_enabled'):
                cmd += ['--puts-enabled']
            if cell.get('max_score_filter') is not None:
                cmd += ['--max-score-filter', str(cell['max_score_filter'])]
        t0 = time.time()
        proc = subprocess.run(cmd, env=child_env, cwd=_REPO_ROOT)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise SystemExit(f"[STOP] cell-worker FAILED (exit {proc.returncode}) cell={key} "
                             f"after {elapsed:.1f}s -- {i - 1}/{len(cells)} cells completed before this failure.")

        done_set.add(key)
        state['done_cells'] = [list(k) for k in sorted(done_set, key=lambda t: [str(x) for x in t])]
        state['job'] = job
        state['stage'] = stage
        state['n_iter'] = n_iter
        import mc_patch
        mc_patch.atomic_write_json(state_path, state)
        _tee(f"[{i}/{len(cells)}] OK {key} ({elapsed:.1f}s)", log_path)

    final_score_date = max_score_date()
    _tee(f"[CLOSE-BOUNDARY-GUARD] battery END max(Score.date)={final_score_date}", log_path)

    fp_end = compute_fingerprint(distinct_windows, log_path)
    _tee(f"[FINGERPRINT-GUARD] battery END per-window loaded>=70 counts: {fp_end}", log_path)
    fp_changed = {w: (fp_start.get(w), fp_end.get(w)) for w in distinct_windows if fp_start.get(w) != fp_end.get(w)}
    if fp_changed:
        _tee(f"[FINGERPRINT-GUARD-FLAG] {len(fp_changed)} window(s) changed: {fp_changed}", log_path)
        for i, cell in enumerate(cells, 1):
            if cell['window'] in fp_changed and cell['key'] in done_set:
                tainted_cells.append({'cell': list(cell['key']), 'index': i, 'reason': 'fingerprint',
                                      'window': cell['window'], 'start_count': fp_changed[cell['window']][0],
                                      'end_count': fp_changed[cell['window']][1]})
    else:
        _tee("[FINGERPRINT-GUARD] no count change detected -- 0 tainted windows", log_path)

    _tee((f"[TAINT-SUMMARY] {len(tainted_cells)} cell(s) flagged as tainted: {tainted_cells}")
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


def _paths_for_job(job):
    return (
        os.path.join(OUT_DIR, f'frontier_{job}.csv'),
        os.path.join(STATE_DIR, f'frontier_{job}.json'),
        os.path.join(LOG_DIR, f'{job}.log'),
    )


# ---------------------------------------------------------------------------
# F5 -- liquidity-proxy validation (report-only)
# ---------------------------------------------------------------------------
def run_f5(log_path, out_csv):
    """2022-08+ overlap: within-era percentile of underlying dollar-volume vs
    measured option liquidity (FF-3' opt_vol_30d_atm + ledger entry_volume).
    Rank correlation (Spearman) overall + by tier + by score band."""
    import polars as pl

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _tee(f"\n{'='*100}", log_path)
    _tee("F5 liquidity-proxy validation", log_path)

    ledger_path = r'B:\polygon_derived\ledger_v2\ledger.parquet'
    if not os.path.isfile(ledger_path):
        _tee(f"[STOP] {ledger_path} missing -- cannot validate liquidity proxy without the option-liquidity side", log_path)
        raise SystemExit(1)
    ledger = pl.read_parquet(ledger_path)
    _tee(f"[F5] ledger_v2 loaded: {len(ledger)} rows, columns={ledger.columns}", log_path)

    opt_liq_col = None
    for cand in ('entry_volume', 'opt_vol_30d_atm', 'volume'):
        if cand in ledger.columns:
            opt_liq_col = cand
            break
    if opt_liq_col is None:
        _tee(f"[STOP] no recognizable option-liquidity column in ledger_v2 (looked for entry_volume/"
            f"opt_vol_30d_atm/volume, found {ledger.columns}) -- reporting columns and stopping", log_path)
        raise SystemExit(1)
    _tee(f"[F5] using option-liquidity column: {opt_liq_col}", log_path)

    date_col = 'entry_date' if 'entry_date' in ledger.columns else 'signal_date'
    tier_col = 'tier' if 'tier' in ledger.columns else None
    score_col = 'overall' if 'overall' in ledger.columns else ('score' if 'score' in ledger.columns else None)
    sym_col = 'symbol'

    from database.trader_database import DB
    DB.connect(reuse_if_open=True)

    keys = list(zip(ledger[sym_col].to_list(), ledger[date_col].to_list()))
    dv = {}
    for sym, d in set(keys):
        cur = DB.execute_sql("SELECT close, volume FROM price_history WHERE symbol=%s AND date=%s", (sym, str(d)))
        r = cur.fetchone()
        if r and r[0] is not None and r[1] is not None:
            dv[(sym, str(d))] = float(r[0]) * float(r[1])
    _tee(f"[F5] resolved underlying dollar-volume for {len(dv)}/{len(set(keys))} distinct (symbol,date) keys", log_path)

    dollar_vol_list = []
    opt_liq_list = []
    tier_list = []
    score_list = []
    for row in ledger.iter_rows(named=True):
        k = (row[sym_col], str(row[date_col]))
        if k not in dv:
            continue
        ov = row.get(opt_liq_col)
        if ov is None:
            continue
        dollar_vol_list.append(dv[k])
        opt_liq_list.append(float(ov))
        tier_list.append(row.get(tier_col) if tier_col else None)
        score_list.append(row.get(score_col) if score_col else None)

    n = len(dollar_vol_list)
    _tee(f"[F5] matched sample size for rank correlation: n={n}", log_path)
    if n < 10:
        _tee(f"[F5] STOP -- sample too small (n={n}) for a meaningful Spearman correlation", log_path)
        raise SystemExit(1)

    def spearman(xs, ys):
        n = len(xs)
        rx = _rank(xs)
        ry = _rank(ys)
        d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
        return 1 - (6 * d2) / (n * (n ** 2 - 1))

    def _rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    overall_rho = spearman(dollar_vol_list, opt_liq_list)
    _tee(f"[F5] OVERALL Spearman rho(dollar_volume, {opt_liq_col}) = {overall_rho:.4f} n={n}", log_path)

    rows_out = [{'cut': 'overall', 'value': 'ALL', 'n': n, 'spearman_rho': overall_rho}]

    if tier_col:
        by_tier = defaultdict(lambda: ([], []))
        for dvv, ov, t in zip(dollar_vol_list, opt_liq_list, tier_list):
            if t is None:
                continue
            by_tier[t][0].append(dvv)
            by_tier[t][1].append(ov)
        for t, (xs, ys) in sorted(by_tier.items()):
            if len(xs) >= 10:
                rho = spearman(xs, ys)
                _tee(f"[F5] BY-TIER tier={t} n={len(xs)} rho={rho:.4f}", log_path)
                rows_out.append({'cut': 'tier', 'value': t, 'n': len(xs), 'spearman_rho': rho})
            else:
                _tee(f"[F5] BY-TIER tier={t} n={len(xs)} -- too small, skipped", log_path)

    if score_col:
        bands = [(75, 79), (80, 84), (85, 87), (88, 94), (95, 100)]
        for lo, hi in bands:
            xs, ys = [], []
            for dvv, ov, sc in zip(dollar_vol_list, opt_liq_list, score_list):
                if sc is None:
                    continue
                if lo <= sc <= hi:
                    xs.append(dvv)
                    ys.append(ov)
            if len(xs) >= 10:
                rho = spearman(xs, ys)
                _tee(f"[F5] BY-SCORE-BAND [{lo}-{hi}] n={len(xs)} rho={rho:.4f}", log_path)
                rows_out.append({'cut': 'score_band', 'value': f'{lo}-{hi}', 'n': len(xs), 'spearman_rho': rho})
            else:
                _tee(f"[F5] BY-SCORE-BAND [{lo}-{hi}] n={len(xs)} -- too small, skipped", log_path)

    rank_faithful = overall_rho >= 0.6
    tier_rhos = [r['spearman_rho'] for r in rows_out if r['cut'] == 'tier']
    monotone = all(tier_rhos[i] <= tier_rhos[i + 1] + 1e-9 for i in range(len(tier_rhos) - 1)) if len(tier_rhos) > 1 else None
    _tee(f"[F5-BAR-CHECK] overall_rho>=0.6: {rank_faithful}  monotone_across_tiers: {monotone} "
        f"(bar is BOTH per PREREG; this line reports the raw components, no verdict stated)", log_path)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['cut', 'value', 'n', 'spearman_rho'])
        w.writeheader()
        w.writerows(rows_out)
    _tee(f"[WRITE] {out_csv} ({len(rows_out)} rows)", log_path)
    return rows_out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--selftest', action='store_true')
    p.add_argument('--stage', default=None,
                   choices=['spy-hisa', 'f0', 'f0-rerun', 'f1', 'f1p', 'f1pp',
                            'f2-survivor', 'f2-buf20', 'f2p-survivor', 'f2p-buf20',
                            'f3', 'f3p', 'f4', 'f4p', 'f5'])
    p.add_argument('--job', default=None)
    p.add_argument('--n-iter', type=int, default=N_ITER_DEFAULT)
    p.add_argument('--thresholds', default=None, help='comma list of thresholds for f2/f3 (legacy ctx-filter stages)')
    p.add_argument('--tier-points', default=None, help='comma list of TIER_FUNDING_POINTS names for f2p/f3p stages')
    p.add_argument('--windows', default=None, help='comma list of window overrides')
    # cell-worker (internal, legacy ctx-filter mechanism)
    p.add_argument('--cell-worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--phase', default=None, help=argparse.SUPPRESS)
    p.add_argument('--threshold-kind', default=None, help=argparse.SUPPRESS)
    p.add_argument('--threshold', default='', help=argparse.SUPPRESS)
    p.add_argument('--window', default=None, help=argparse.SUPPRESS)
    p.add_argument('--universe', default='full', help=argparse.SUPPRESS)
    p.add_argument('--lens', default='calibrated', help=argparse.SUPPRESS)
    p.add_argument('--dte', type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument('--gross', type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument('--puts-enabled', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--max-score-filter', type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument('--out-csv', default=None, help=argparse.SUPPRESS)
    p.add_argument('--log-path', default=None, help=argparse.SUPPRESS)
    # cell-worker-tier (internal, AMENDMENT-1 tier-native mechanism)
    p.add_argument('--cell-worker-tier', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--tier-point', default='', help=argparse.SUPPRESS)
    p.add_argument('--put-tier-point', default=None, help=argparse.SUPPRESS)
    p.add_argument('--no-ct-promote', action='store_true', help=argparse.SUPPRESS)
    return p.parse_args()


def main():
    args = parse_args()
    if args.selftest:
        raise SystemExit(selftest())

    for d in (OUT_DIR, TAPES_DIR, LOG_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)

    if args.cell_worker:
        threshold = float(args.threshold) if args.threshold not in ('', None) else None
        if threshold is not None and threshold == int(threshold):
            threshold = int(threshold)
        run_one_cell(args.phase, args.threshold_kind, threshold, args.window, args.n_iter,
                    args.out_csv, args.log_path, args.job, universe=args.universe, lens=args.lens,
                    dte=args.dte, gross=args.gross, puts_enabled=args.puts_enabled,
                    max_score_filter=args.max_score_filter)
        return

    if args.cell_worker_tier:
        tier_point = args.tier_point if args.tier_point not in ('', None) else None
        run_tier_cell(args.phase, tier_point, args.window, args.n_iter, args.out_csv, args.log_path,
                     args.job, universe=args.universe, lens=args.lens, dte=args.dte, gross=args.gross,
                     puts_enabled=args.puts_enabled, put_tier_point=args.put_tier_point,
                     ct_promote=not args.no_ct_promote)
        return

    if args.stage is None:
        raise SystemExit("specify --stage or --selftest")
    job = args.job or args.stage

    if args.stage == 'spy-hisa':
        out_csv = os.path.join(OUT_DIR, 'spy_hisa_reference.csv')
        log_path = os.path.join(LOG_DIR, 'spy_hisa.log')
        run_spy_hisa(log_path, out_csv)
        return

    if args.stage in ('f0', 'f0-rerun'):
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        out_bands = os.path.join(OUT_DIR, f'frontier_{job}_bands.csv')
        if args.stage == 'f0-rerun':
            run_f0_rerun(log_path, out_bands)
        else:
            run_f0(log_path, out_bands, None)
        return

    if args.stage == 'f5':
        log_path = os.path.join(LOG_DIR, f'{job}.log')
        out_csv = os.path.join(OUT_DIR, f'frontier_{job}.csv')
        run_f5(log_path, out_csv)
        return

    thresholds = [int(t.strip()) for t in args.thresholds.split(',')] if args.thresholds else None
    tier_points = [t.strip() for t in args.tier_points.split(',')] if args.tier_points else None
    windows_override = [w.strip() for w in args.windows.split(',')] if args.windows else None

    cells = []
    if args.stage == 'f1':
        for th in F1_THRESHOLDS:
            for w in PHASE_D_WINDOWS_12:
                cells.append({'phase': 'f1', 'threshold_kind': 'min_score', 'threshold': th,
                              'window': w, 'universe': 'full', 'lens': 'calibrated',
                              'key': ('f1', th, w)})
    elif args.stage == 'f1p':
        for tp in TIER_FUNDING_ORDER:
            for w in PHASE_D_WINDOWS_12:
                cells.append({'mechanism': 'tier', 'phase': 'f1p', 'tier_point': tp,
                              'window': w, 'universe': 'full', 'lens': 'calibrated', 'ct_promote': True,
                              'key': ('f1p', tp, w)})
    elif args.stage == 'f1pp':
        for w in PHASE_D_WINDOWS_12:
            cells.append({'mechanism': 'tier', 'phase': 'f1pp', 'tier_point': 'zero-low+mid',
                          'window': w, 'universe': 'full', 'lens': 'calibrated', 'ct_promote': False,
                          'key': ('f1pp', w)})
    elif args.stage == 'f2-survivor':
        for th in (thresholds or []):
            for w in PHASE_D_WINDOWS_12:
                cells.append({'phase': 'f2s', 'threshold_kind': 'min_score', 'threshold': th,
                              'window': w, 'universe': 'survivor', 'lens': 'calibrated',
                              'key': ('f2s', th, w)})
    elif args.stage == 'f2-buf20':
        for th in (thresholds or []):
            for w in F2_BUF_WINDOWS:
                cells.append({'phase': 'f2b', 'threshold_kind': 'min_score', 'threshold': th,
                              'window': w, 'universe': 'full', 'lens': 'buf20',
                              'key': ('f2b', th, w)})
    elif args.stage == 'f2p-survivor':
        for tp in (tier_points or []):
            for w in PHASE_D_WINDOWS_12:
                cells.append({'mechanism': 'tier', 'phase': 'f2ps', 'tier_point': tp,
                              'window': w, 'universe': 'survivor', 'lens': 'calibrated', 'ct_promote': True,
                              'key': ('f2ps', tp, w)})
    elif args.stage == 'f2p-buf20':
        for tp in (tier_points or []):
            for w in F2_BUF_WINDOWS:
                cells.append({'mechanism': 'tier', 'phase': 'f2pb', 'tier_point': tp,
                              'window': w, 'universe': 'full', 'lens': 'buf20', 'ct_promote': True,
                              'key': ('f2pb', tp, w)})
    elif args.stage == 'f3':
        for th in (thresholds or []):
            for w in (windows_override or F3_WINDOWS):
                for dte in F3_DTE_AXIS:
                    cells.append({'phase': 'f3', 'threshold_kind': 'min_score', 'threshold': th,
                                  'window': w, 'universe': 'full', 'lens': 'calibrated', 'dte': dte,
                                  'key': ('f3dte', th, w, dte)})
                for gross in F3_GROSS_AXIS:
                    cells.append({'phase': 'f3', 'threshold_kind': 'min_score', 'threshold': th,
                                  'window': w, 'universe': 'full', 'lens': 'calibrated', 'gross': gross,
                                  'key': ('f3gross', th, w, gross)})
    elif args.stage == 'f3p':
        for tp in (tier_points or []):
            for w in (windows_override or F3_WINDOWS):
                for dte in F3_DTE_AXIS:
                    cells.append({'mechanism': 'tier', 'phase': 'f3p', 'tier_point': tp,
                                  'window': w, 'universe': 'full', 'lens': 'calibrated', 'dte': dte,
                                  'ct_promote': True, 'key': ('f3pdte', tp, w, dte)})
                for gross in F3_GROSS_AXIS:
                    cells.append({'mechanism': 'tier', 'phase': 'f3p', 'tier_point': tp,
                                  'window': w, 'universe': 'full', 'lens': 'calibrated', 'gross': gross,
                                  'ct_promote': True, 'key': ('f3pgross', tp, w, gross)})
    elif args.stage == 'f4':
        for ms in F4_MAX_SCORES:
            for w in F4_WINDOWS:
                max_filter = 12 if ms == 12 else None   # 15 is native to put_score_to_tier; 12 needs the extra filter
                cells.append({'phase': 'f4', 'threshold_kind': 'max_score', 'threshold': ms,
                              'window': w, 'universe': 'full', 'lens': 'calibrated', 'puts_enabled': True,
                              'max_score_filter': max_filter, 'key': ('f4', ms, w)})
    elif args.stage == 'f4p':
        for ptp in PUT_TIER_FUNDING_ORDER:
            for w in F4_WINDOWS:
                cells.append({'mechanism': 'tier', 'phase': 'f4p', 'tier_point': None,
                              'window': w, 'universe': 'full', 'lens': 'calibrated', 'puts_enabled': True,
                              'put_tier_point': ptp, 'ct_promote': True, 'key': ('f4p', ptp, w)})

    if not cells:
        raise SystemExit(f"[STOP] --stage {args.stage} produced ZERO cells (check --thresholds/--tier-points) -- nothing to run")

    out_csv, state_path, log_path = _paths_for_job(job)
    _run_orchestrator(job, args.stage, cells, args.n_iter, log_path, state_path, out_csv)


if __name__ == '__main__':
    main()
