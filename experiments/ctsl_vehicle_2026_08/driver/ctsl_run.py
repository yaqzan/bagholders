#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ctsl_run.py -- The CTSL vehicle study (V1-V5).
(experiments/ctsl_vehicle_2026_08/PREREG.md, locked 3e2adc9f + AMENDMENT-1 1acca0aa)

======================================================================
CRIBBED (read-only) vs FRESHLY WRITTEN
======================================================================
Imported in place, never edited:
  frontier_2026_08/driver/frontier_run.py -- TIER_FUNDING_POINTS (the
    tier-native funding grid; 'ultra-only' IS this campaign's vehicle),
    SENTINEL_SHAPE_BASE, load_spy_hisa, run_spy_hisa.
  sentinel_guards_2026_08/driver/guardS_run.py (transitively flipR/phaseD) --
    LENS_ENV, LENS_STRESS_020, PHASE_D_WINDOWS_12, compute_fingerprint,
    max_score_date, min_score_funded, TPSL_META_PATH, SURVIVOR_FILE,
    _load_survivor_set, apply_survivor_filter.
  tpsl_refine_2026_08/driver/mc_patch.py -- apply_frozen_pins,
    resolve_and_pin_version, install_loader_cache, disable_puts,
    call_outcome_rates, atomic_write_json.

Freshly written here (NOT a copy of run_tier_cell -- it differs in three ways
that matter, so it is its own function rather than a patched import):
  run_v_cell()  -- adds (a) an explicit CTSL_ENABLED axis with a live assert,
    which no sibling driver has ever varied (every prior campaign left the
    score-stage lift at its shipped default and only toggled CT_PROMOTE);
    (b) EXACT router-sleeve accounting read from ctx['call_outcomes'][k]
    ['_dte']=='15' -- the deterministic routed key set -- rather than inferring
    it from the score==0 side effect; (c) its own output schema/tape dir so
    nothing is ever written into a closed campaign's directory.
  _run_orchestrator() -- same guard contract as frontier_run's (close-boundary
    + fingerprint + per-cell state resume + taint summary), respawning THIS
    file as the cell worker. Not importable from frontier_run: that function
    spawns os.path.abspath(frontier_run.__file__), which would run frontier's
    worker and write frontier's tapes.
  V1 composite/THIN analysis, V2 composition-weighted MISS_P derivation,
    V4 capacity join, V5 cube -- all fresh.

HARD RULE: never edits monte_carlo.py / strategy_config.py / any tracked
production file; never edits or writes into frontier_2026_08/ or
sentinel_guards_2026_08/ or tpsl_refine_2026_08/. Never git-commits.

Usage
-----
    py ctsl_run.py --selftest
    py ctsl_run.py --stage v1        --job v1     --n-iter 500
    py ctsl_run.py --stage v1-ctsloff --job v1c   --n-iter 500
    py ctsl_run.py --stage v1-report
Console output is ASCII-only throughout per this repo's convention.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from contextlib import redirect_stdout

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_THIS_DIR)
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)
_TPSL_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'tpsl_refine_2026_08', 'driver')
_FLIPR_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'realism_default_flip_2026_08', 'driver')
_GUARDS_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'sentinel_guards_2026_08', 'driver')
_FRONTIER_DRIVER_DIR = os.path.join(_EXPERIMENTS_DIR, 'frontier_2026_08', 'driver')

for _d in (_THIS_DIR, _TPSL_DRIVER_DIR, _FLIPR_DRIVER_DIR, _GUARDS_DRIVER_DIR,
           _FRONTIER_DRIVER_DIR, _REPO_ROOT):
    if _d not in sys.path:
        sys.path.insert(0, _d)

assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} (from __file__={__file__!r})")
assert os.path.isfile(os.path.join(_FRONTIER_DRIVER_DIR, 'frontier_run.py')), (
    f"frontier_run.py not found at {_FRONTIER_DRIVER_DIR!r} -- required read-only")

from guardS_run import (                                                     # noqa: E402
    LENS_ENV, LENS_STRESS_020, PHASE_D_WINDOWS_12, compute_fingerprint,
    max_score_date, min_score_funded, TPSL_META_PATH,
    SURVIVOR_FILE, _load_survivor_set, apply_survivor_filter,
)
from frontier_run import (                                                   # noqa: E402
    TIER_FUNDING_POINTS, SENTINEL_SHAPE_BASE, load_spy_hisa,
)


def _tee(msg, log_path):
    print(msg, flush=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
TAPES_DIR = os.path.join(OUT_DIR, 'tapes')
DD_LEDGER_DIR = os.path.join(_REPO_ROOT, '.cache', 'dd_ledger')

N_ITER_DEFAULT = 500

# The vehicle. AMENDMENT-1 item 3: 'promotions-only' has no clean config
# expression, so ultra-only IS the vehicle, at ~90% measured CT purity.
VEHICLE_TIER_POINT = 'ultra-only'

# V1 lens grid. (lens, universe) pairs -- 'canon' is a LABELED reference row
# per the post-flip doctrine banner, never a decision input.
V1_LENSES = [
    ('calibrated', 'full'),
    ('buffer',     'full'),
    ('canon',      'full'),
    ('calibrated', 'survivor'),
]
LENS_CANON = {'TP_FILL_MISS_P': '0.0', 'TP_FILL_GAP_AWARE': '0'}

# V2's DERIVED lens. The rate is not a guess and not a knob: it is
# v2_fill_honesty.py's composition-weighted never-fill rate for THIS vehicle
# (joined-only, both windows agreed to 4 dp), read from out/ctsl_v2_missp.csv
# at import so the number in the code can never drift from the measurement.
V2_MISSP_CSV = os.path.join(OUT_DIR, 'ctsl_v2_missp.csv')


def _derived_vehicle_missp(path=V2_MISSP_CSV):
    """The vehicle's own never-fill rate, averaged over the decision windows."""
    if not os.path.isfile(path):
        return None
    vals = [float(r['missp_joined_only']) for r in csv.DictReader(open(path, encoding='utf-8'))
            if r['window'] in DECISION_WINDOWS_EARLY]
    return round(sum(vals) / len(vals), 3) if vals else None


DECISION_WINDOWS_EARLY = ['22-now', '5y']   # referenced by the loader above

# PREREG V1 THIN rule.
THIN_TRADES = 30
ANECDOTE_TRADES = 10

# PREREG V1 sleeve composites: (label, weight on the vehicle).
SLEEVE_WEIGHTS = [('100spy', 0.00), ('85_15', 0.15), ('70_30', 0.30)]

DECISION_WINDOWS = ['22-now', '5y']

# AMENDMENT-1 item 2 materiality bar for the CTSL_ENABLED=0 diagnostic.
CTSL_MATERIAL_PP = 5.0


V_SCHEMA = [
    'stage', 'arm', 'tier_point', 'window', 'universe', 'lens',
    'ct_promote', 'ctsl_enabled', 'drop_frac', 'n_drop_signals', 'dte', 'hold_days', 'gross',
    'tp', 'sl', 'n_iter', 'n_call_signals',
    'mean_ret', 'med_ret', 'worst_dd', 'mean_dd', 'p_coll',
    'tp_rate', 'sl_rate', 'hard_rate', 'both_rate',
    'elapsed_prepare_s', 'elapsed_sim_s', 'n_calls_delisted',
    'tier_ultra', 'tier_top', 'tier_mid', 'tier_low',
    'max_positions', 'gross_cap', 'capital_ceiling', 'min_score_funded',
    'miss_p', 'gap_aware', 'avg_open_premium_base_pct', 'max_open_premium_base_pct',
    'mean_trades_per_path', 'median_trades_per_path', 'n_paths_with_trades',
    'n_ct_call_tape_rows', 'ct_row_share',
    'n_routed15_signals', 'n_routed15_rows', 'routed15_row_share', 'routed15_pnl_share',
    'spy_return_pct', 'hisa_return_pct',
]


def _lens_env(lens):
    if lens == 'calibrated':
        return dict(LENS_ENV['calibrated'])
    if lens == 'buffer':
        return dict(LENS_STRESS_020)
    if lens == 'canon':
        return dict(LENS_CANON)
    if lens == 'vehicle':
        rate = _derived_vehicle_missp()
        if rate is None:
            raise SystemExit(f"[STOP] lens 'vehicle' needs {V2_MISSP_CSV} -- run v2_fill_honesty.py first")
        return {'TP_FILL_MISS_P': str(rate), 'TP_FILL_GAP_AWARE': '1'}
    raise SystemExit(f"[STOP] unknown lens {lens!r}")


# V5 era windows. monte_carlo.WINDOWS stops at 10y; v74 scores run to 1986 and
# both eras are populated (dot-com 14,291 signals/942 syms; GFC 5,275/660 --
# counted before the battery was written). Added to mc.WINDOWS IN MEMORY inside
# the cell; monte_carlo.py on disk is never touched.
V5_EXTRA_WINDOWS = {
    'dotcom': ('2000-01-01', '2002-12-31'),
    'gfc':    ('2007-10-01', '2009-06-30'),
}


def _count_loaded_signals(window, mc, version_id):
    """The window's loaded>=70 call count, by direct SQL. Deliberately does NOT
    go through mc.load_signals / _prepare_window: those are wrapped by the
    per-process loader cache, and priming that cache before the drop knob is set
    is exactly the bug this replaced (see run_v_cell)."""
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)
    d0, d1 = {label: (a, b) for label, a, b in mc.WINDOWS}[window]
    row = DB.execute_sql(
        "SELECT COUNT(*) FROM scores WHERE version_id=%s AND overall >= %s "
        "AND date BETWEEN %s AND %s",
        (version_id, mc.OVERFLOW_THRESHOLD, str(d0), str(d1))).fetchone()
    return int(row[0])


def _install_extra_windows(mc, log_path):
    import datetime as _dt
    have = {w[0] for w in mc.WINDOWS}
    added = []
    for lab, (d0, d1) in V5_EXTRA_WINDOWS.items():
        if lab not in have:
            mc.WINDOWS = list(mc.WINDOWS) + [
                (lab, _dt.date.fromisoformat(d0), _dt.date.fromisoformat(d1))]
            added.append(lab)
    if added:
        _tee(f"[V5-WINDOWS] added in-memory: {added} (monte_carlo.py untouched)", log_path)


def run_v_cell(stage, arm, window, n_iter, out_csv, log_path, job,
               universe='full', lens='calibrated', dte=None, gross=None,
               ct_promote=True, ctsl_enabled=True, tier_point=VEHICLE_TIER_POINT,
               miss_p=None, drop_frac=None):
    """One (arm x window x lens x universe) cell, in its own process."""
    if not os.path.exists(TPSL_META_PATH):
        raise SystemExit(f"[STOP] {TPSL_META_PATH} missing")

    survivor_set = _load_survivor_set(SURVIVOR_FILE) if universe == 'survivor' else None

    import mc_patch
    mc_patch.apply_frozen_pins(max_workers=6)

    # Sentinel shape, minus its tier cascade -- the tier funding IS the axis.
    for k, v in SENTINEL_SHAPE_BASE.items():
        if k not in ('TIER_ULTRA_OV', 'TIER_TOP_OV', 'TIER_MID_OV', 'TIER_LOW_OV'):
            os.environ[k] = v
    pt = TIER_FUNDING_POINTS[tier_point]
    os.environ['TIER_ULTRA_OV'] = str(pt['ultra'])
    os.environ['TIER_TOP_OV'] = str(pt['top'])
    os.environ['TIER_MID_OV'] = str(pt['mid'])
    os.environ['TIER_LOW_OV'] = str(pt['low'])

    for k, v in _lens_env(lens).items():
        os.environ[k] = v
    if miss_p is not None:                      # V5 cube overrides the lens rate
        os.environ['TP_FILL_MISS_P'] = str(miss_p)
        os.environ['TP_FILL_GAP_AWARE'] = '1'
    # The random-drop control is mutually exclusive with the (retired, OFF)
    # liquidity floor -- pinned explicitly rather than inherited.
    os.environ['LIQUIDITY_FLOOR'] = '0'
    os.environ['LIQUIDITY_RANDOM_DROP'] = '0'
    if dte is not None:
        os.environ['NOMINAL_CAL_DTE'] = str(dte)
        os.environ['HOLD_CAL_DAYS'] = str(dte - 3)
    if gross is not None:
        os.environ['GROSS_PREMIUM_CAP'] = str(gross)
        os.environ['CALL_PREMIUM_CAP'] = str(gross)

    # BOTH counter-trend switches are set EXPLICITLY on every cell, never left
    # to inherit the shipped default (traps.md: "a bare {} baseline silently
    # inherits the shipped ON state").
    os.environ['CT_PROMOTE'] = '1' if ct_promote else '0'
    os.environ['CTSL_ENABLED'] = '1' if ctsl_enabled else '0'
    os.environ['MC_TRADE_TAPE'] = '1'
    version_meta = mc_patch.resolve_and_pin_version(TPSL_META_PATH)

    import monte_carlo as mc
    mc_patch.install_loader_cache(mc)
    mc_patch.disable_puts(mc)
    assert mc.CT_PROMOTE == ct_promote, f"[STOP] mc.CT_PROMOTE={mc.CT_PROMOTE} != {ct_promote}"
    assert mc.CTSL_ENABLED == ctsl_enabled, f"[STOP] mc.CTSL_ENABLED={mc.CTSL_ENABLED} != {ctsl_enabled}"
    assert mc.LIQUIDITY_FLOOR == 0, f"[STOP] LIQUIDITY_FLOOR={mc.LIQUIDITY_FLOOR} must be 0"
    _install_extra_windows(mc, log_path)

    # V5 signal-drop: the engine knob is an absolute COUNT K, not a fraction, so
    # K is derived from this window's own loaded>=70 count (the same count the
    # fingerprint guard reports) and then asserted live. Set on the module AFTER
    # import because _apply_liquidity_random_drop_filter reads the global at call
    # time, and prepare (where the filter runs) is parent-side, not in a worker.
    n_drop = 0
    if drop_frac:
        # K comes from a DIRECT count, deliberately NOT from compute_fingerprint.
        # Calling that helper inside a cell silently destroys both V5 axes: it
        # re-applies LENS_ENV['calibrated'] to os.environ (so every MP worker,
        # which re-reads TP_FILL_MISS_P from the environment on re-import, runs
        # at 0.15 no matter what this cell asked for) AND it runs its own
        # _prepare_window through the shared loader cache, so the undropped
        # signal list is already memoised before the drop is set. Symptom when
        # it bit: every (drop x MISS_P) cell of a window returned bit-identical
        # med/DD/trades. It belongs in the ORCHESTRATOR (parent, never simulates)
        # and nowhere near a cell.
        n_sig = _count_loaded_signals(window, mc, version_meta['id'])
        n_drop = int(round(drop_frac * n_sig))
        mc.LIQUIDITY_RANDOM_DROP = n_drop
        os.environ['LIQUIDITY_RANDOM_DROP'] = str(n_drop)
        assert mc.LIQUIDITY_RANDOM_DROP == n_drop
        _tee(f"[V5-DROP] window={window} loaded>=70={n_sig} drop_frac={drop_frac} "
            f"-> K={n_drop} (seed={mc.LIQUIDITY_RANDOM_DROP_SEED})", log_path)
    # Re-assert the two cube knobs survived every helper call above.
    assert abs(mc.TP_FILL_MISS_P - float(os.environ['TP_FILL_MISS_P'])) < 1e-12, (
        f"[STOP] TP_FILL_MISS_P drifted: module={mc.TP_FILL_MISS_P} env={os.environ['TP_FILL_MISS_P']}")

    window_lookup = {label: (d0, d1) for label, d0, d1 in mc.WINDOWS}
    if window not in window_lookup:
        raise SystemExit(f"[STOP] window {window!r} not in mc.WINDOWS")
    d_start, d_end = window_lookup[window]
    mc.N_ITER = n_iter

    _tee(f"\n{'='*100}", log_path)
    _tee(f"V-CELL job={job} stage={stage} arm={arm} tier_point={tier_point} window={window} "
        f"universe={universe} lens={lens} dte={dte} gross={gross} ct_promote={ct_promote} "
        f"ctsl_enabled={ctsl_enabled} n_iter={n_iter} pid={os.getpid()}", log_path)
    live_min_score = min_score_funded(mc.TIER_ALLOC)
    _tee(f"[ENGAGED-CONFIG] tier_ultra={mc.TIER_ALLOC.get('ultra')} tier_top={mc.TIER_ALLOC.get('top')} "
        f"tier_mid={mc.TIER_ALLOC.get('mid')} tier_low={mc.TIER_ALLOC.get('low')} "
        f"min_score_funded={live_min_score} max_positions={mc.MAX_POSITIONS_CALL or mc.MAX_POSITIONS} "
        f"gross_cap={mc.GROSS_PREMIUM_CAP} nominal_cal_dte={mc.NOMINAL_CAL_DTE} "
        f"hold_cal_days={mc.HOLD_CAL_DAYS} miss_p={getattr(mc,'TP_FILL_MISS_P',None)} "
        f"gap_aware={getattr(mc,'TP_FILL_GAP_AWARE',None)} CT_PROMOTE(live)={mc.CT_PROMOTE} "
        f"CTSL_ENABLED(live)={mc.CTSL_ENABLED} CTSL_CALL_TREND_MAX={mc.CTSL_CALL_TREND_MAX} "
        f"CT_CALL_TREND_MAX={mc.CT_CALL_TREND_MAX} CT_CALL_TIER={mc.CT_CALL_TIER} "
        f"DTE_ROUTER_ENABLED={mc.DTE_ROUTER_ENABLED} DTE_ROUTER_DAY_CAP={mc.DTE_ROUTER_DAY_CAP} "
        f"DTE_ROUTER_ALLOC_SCORE_CAP={mc.DTE_ROUTER_ALLOC_SCORE_CAP} "
        f"SPREAD_TILT_ENABLED={mc.SPREAD_TILT_ENABLED}", log_path)

    t0 = time.perf_counter()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ctx = mc._prepare_window(window, d_start, d_end, version_meta['id'])
    t_prepare = time.perf_counter() - t0
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(buf.getvalue())

    if universe == 'survivor':
        ctx, n_bf, n_af, n_dropped = apply_survivor_filter(ctx, survivor_set)
        _tee(f"[SURVIVOR-FILTER] window={window} before={n_bf} after={n_af} dropped={n_dropped}", log_path)

    call_syms = {k[0] for k in ctx['call_outcomes'].keys()}
    n_calls_delisted = sum(1 for s in call_syms if survivor_set is not None and str(s).upper() not in survivor_set)
    if universe == 'survivor':
        assert n_calls_delisted == 0, f"[STOP] survivor filter left {n_calls_delisted} delisted symbols"

    # AMENDMENT-1 item 4: the EXACT routed-15 key set, straight from the ctx the
    # sim will consume (monte_carlo.py:4416 tags routed outcomes with _dte='15').
    routed_keys = {k for k, o in ctx['call_outcomes'].items() if str(o.get('_dte', '')) == '15'}
    _tee(f"[ROUTER-DISCLOSURE] window={window} n_routed15_signals={len(routed_keys)} "
        f"of n_call_signals={len(ctx['call_outcomes'])}", log_path)

    t1 = time.perf_counter()
    with open(log_path, 'a', encoding='utf-8') as log_f:
        with redirect_stdout(log_f):
            sim = mc._simulate_window(ctx)
    t_sim = time.perf_counter() - t1
    result = sim['seeded']

    n_c, tp_rate, sl_rate, hard_rate, both_rate = mc_patch.call_outcome_rates(ctx)

    tape_src = os.path.join(DD_LEDGER_DIR, f'tape_{window}.parquet')
    mean_tpp = median_tpp = n_paths = n_ct_rows = None
    ct_row_share = n_routed_rows = routed_row_share = routed_pnl_share = None
    tape_dst = None
    if os.path.isfile(tape_src):
        os.makedirs(TAPES_DIR, exist_ok=True)
        tape_dst = os.path.join(TAPES_DIR, f'{job}_{arm}_{window}_{universe}_{lens}.parquet')
        shutil.move(tape_src, tape_dst)
        try:
            import polars as pl
            df = pl.read_parquet(tape_dst)
            if len(df) > 0:
                per_seed = df.group_by('seed').len()['len'].to_list()
                n_paths = len(per_seed)
                mean_tpp = statistics.mean(per_seed)
                median_tpp = statistics.median(per_seed)
                if 'ct' in df.columns and df['ct'].dtype != pl.Null:
                    n_ct_rows = int((df['ct'] == 'ct_call').sum())
                    ct_row_share = round(n_ct_rows / len(df), 6)
                else:
                    n_ct_rows, ct_row_share = 0, 0.0
                if routed_keys:
                    rk = pl.DataFrame({'sym_id': [k[0] for k in routed_keys],
                                       'entry_date': [str(k[1]) for k in routed_keys]})
                    df2 = df.with_columns(
                        (pl.col('premium_cost') * pl.col('option_pnl')).alias('_dpnl'))
                    hit = df2.join(rk, on=['sym_id', 'entry_date'], how='inner')
                    n_routed_rows = len(hit)
                    routed_row_share = round(n_routed_rows / len(df), 6)
                    tot = df2['_dpnl'].sum()
                    routed_pnl_share = round(hit['_dpnl'].sum() / tot, 6) if tot else None
                else:
                    n_routed_rows, routed_row_share, routed_pnl_share = 0, 0.0, 0.0
        except Exception as e:
            _tee(f"[TAPE] WARNING failed to aggregate {tape_dst}: {e}", log_path)

    if ct_promote is False:
        assert (n_ct_rows in (None, 0)), \
            f"[STOP] CT_PROMOTE=0 but tape has {n_ct_rows} ct_call rows -- off-switch did not engage"
        _tee(f"[CT-OFF-ASSERT] window={window} n_ct_call_tape_rows={n_ct_rows} -- PASS", log_path)

    spy_hisa = load_spy_hisa().get(window, {})

    row = {
        'stage': stage, 'arm': arm, 'tier_point': tier_point, 'window': window,
        'universe': universe, 'lens': lens, 'ct_promote': ct_promote, 'ctsl_enabled': ctsl_enabled,
        'drop_frac': drop_frac, 'n_drop_signals': n_drop,
        'dte': mc.NOMINAL_CAL_DTE, 'hold_days': mc.HOLD_CAL_DAYS, 'gross': mc.GROSS_PREMIUM_CAP,
        'tp': mc.TP_BASE, 'sl': mc.SL_BASE, 'n_iter': n_iter,
        'n_call_signals': len(ctx['call_outcomes']),
        'mean_ret': result.get('mean_ret'), 'med_ret': result.get('med_ret'),
        'worst_dd': result.get('worst_dd'), 'mean_dd': result.get('mean_dd'),
        'p_coll': result.get('p_coll'),
        'tp_rate': tp_rate, 'sl_rate': sl_rate, 'hard_rate': hard_rate, 'both_rate': both_rate,
        'elapsed_prepare_s': round(t_prepare, 3), 'elapsed_sim_s': round(t_sim, 3),
        'n_calls_delisted': n_calls_delisted,
        'tier_ultra': mc.TIER_ALLOC.get('ultra'), 'tier_top': mc.TIER_ALLOC.get('top'),
        'tier_mid': mc.TIER_ALLOC.get('mid'), 'tier_low': mc.TIER_ALLOC.get('low'),
        'max_positions': mc.MAX_POSITIONS_CALL or mc.MAX_POSITIONS,
        'gross_cap': mc.GROSS_PREMIUM_CAP, 'capital_ceiling': mc.PRACTICAL_CAPITAL_CEILING,
        'min_score_funded': live_min_score,
        'miss_p': getattr(mc, 'TP_FILL_MISS_P', None), 'gap_aware': getattr(mc, 'TP_FILL_GAP_AWARE', None),
        'avg_open_premium_base_pct': result.get('avg_open_premium_base_pct'),
        'max_open_premium_base_pct': result.get('max_open_premium_base_pct'),
        'mean_trades_per_path': mean_tpp, 'median_trades_per_path': median_tpp,
        'n_paths_with_trades': n_paths,
        'n_ct_call_tape_rows': n_ct_rows, 'ct_row_share': ct_row_share,
        'n_routed15_signals': len(routed_keys), 'n_routed15_rows': n_routed_rows,
        'routed15_row_share': routed_row_share, 'routed15_pnl_share': routed_pnl_share,
        'spy_return_pct': spy_hisa.get('spy_return_pct'), 'hisa_return_pct': spy_hisa.get('hisa_return_pct'),
    }

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    csv_is_new = not os.path.exists(out_csv)
    with open(out_csv, 'a', newline='', encoding='utf-8') as csv_f:
        w = csv.DictWriter(csv_f, fieldnames=V_SCHEMA)
        if csv_is_new:
            w.writeheader()
        w.writerow(row)

    _tee(f"[DONE] arm={arm} window={window} universe={universe} lens={lens} "
        f"med_ret={result.get('med_ret'):+.2f}% worst_dd={result.get('worst_dd'):.2f}% "
        f"p_coll={result.get('p_coll'):.2f}% med_trades={median_tpp} ct_share={ct_row_share} "
        f"routed15_rows={n_routed_rows} routed15_pnl_share={routed_pnl_share} "
        f"spy={row['spy_return_pct']} -> {out_csv}", log_path)
    return row


# ---------------------------------------------------------------------------
# Orchestrator -- same guard contract as frontier_run's, respawning THIS file.
# ---------------------------------------------------------------------------
def _load_json(path, default):
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return default


def _paths_for_job(job):
    return (os.path.join(OUT_DIR, f'ctsl_{job}.csv'),
            os.path.join(STATE_DIR, f'ctsl_{job}.json'),
            os.path.join(LOG_DIR, f'{job}.log'))


def _run_orchestrator(job, stage, cells, n_iter, log_path, state_path, out_csv):
    state = _load_json(state_path, {'done_cells': []})
    done_set = {tuple(c) for c in state.get('done_cells', [])}

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
                                  'old_max_date': str(baseline_score_date),
                                  'new_max_date': str(current_score_date)})
            baseline_score_date = current_score_date

        _tee(f"[{i}/{len(cells)}] LAUNCH {key}", log_path)
        child_env = os.environ.copy()
        child_env['PYTHONIOENCODING'] = 'utf-8'
        child_env['PYTHONUTF8'] = '1'
        # NOTE: the per-cell report tag goes through --cell-stage, NOT --stage.
        # --stage is choices-restricted to the orchestrator's stage names; a
        # cell tag like 'v1c' is not one of them and argparse exits 2 (the
        # v1-ctsloff battery died on cell 1/12 exactly this way before the fix).
        cmd = [sys.executable, os.path.abspath(__file__), '--cell-worker',
               '--cell-stage', cell['stage'], '--arm', cell['arm'], '--window', cell['window'],
               '--n-iter', str(n_iter), '--universe', cell.get('universe', 'full'),
               '--lens', cell.get('lens', 'calibrated'),
               '--tier-point', cell.get('tier_point', VEHICLE_TIER_POINT),
               '--out-csv', out_csv, '--log-path', log_path, '--job', job]
        if cell.get('dte') is not None:
            cmd += ['--dte', str(cell['dte'])]
        if cell.get('gross') is not None:
            cmd += ['--gross', str(cell['gross'])]
        if cell.get('ct_promote') is False:
            cmd += ['--no-ct-promote']
        if cell.get('ctsl_enabled') is False:
            cmd += ['--no-ctsl']
        if cell.get('drop_frac') is not None:
            cmd += ['--drop-frac', str(cell['drop_frac'])]
        if cell.get('miss_p') is not None:
            cmd += ['--miss-p', str(cell['miss_p'])]
        t0 = time.time()
        proc = subprocess.run(cmd, env=child_env, cwd=_REPO_ROOT)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            raise SystemExit(f"[STOP] cell-worker FAILED (exit {proc.returncode}) cell={key} "
                             f"after {elapsed:.1f}s -- {i - 1}/{len(cells)} cells done before this.")

        done_set.add(key)
        state['done_cells'] = [list(k) for k in sorted(done_set, key=lambda t: [str(x) for x in t])]
        state.update({'job': job, 'stage': stage, 'n_iter': n_iter})
        import mc_patch
        mc_patch.atomic_write_json(state_path, state)
        _tee(f"[{i}/{len(cells)}] OK {key} ({elapsed:.1f}s)", log_path)

    final_score_date = max_score_date()
    _tee(f"[CLOSE-BOUNDARY-GUARD] battery END max(Score.date)={final_score_date}", log_path)
    fp_end = compute_fingerprint(distinct_windows, log_path)
    _tee(f"[FINGERPRINT-GUARD] battery END per-window loaded>=70 counts: {fp_end}", log_path)
    fp_changed = {w: (fp_start.get(w), fp_end.get(w)) for w in distinct_windows
                  if fp_start.get(w) != fp_end.get(w)}
    if fp_changed:
        _tee(f"[FINGERPRINT-GUARD-FLAG] {len(fp_changed)} window(s) changed: {fp_changed}", log_path)
        for i, cell in enumerate(cells, 1):
            if cell['window'] in fp_changed and cell['key'] in done_set:
                tainted_cells.append({'cell': list(cell['key']), 'index': i, 'reason': 'fingerprint',
                                      'window': cell['window']})
    else:
        _tee("[FINGERPRINT-GUARD] no count change detected -- 0 tainted windows", log_path)

    _tee((f"[TAINT-SUMMARY] {len(tainted_cells)} cell(s) flagged tainted: {tainted_cells}")
        if tainted_cells else "[TAINT-SUMMARY] 0 tainted cells", log_path)
    _tee(f"\n[ORCHESTRATOR DONE] job={job} stage={stage} total_done={len(done_set)}/{len(cells)} "
        f"wall={time.time()-t_job0:.1f}s -> {out_csv}", log_path)

    state.update({'fingerprint_start': fp_start, 'fingerprint_end': fp_end,
                  'fingerprint_changed': fp_changed, 'tainted_cells': tainted_cells})
    import mc_patch
    mc_patch.atomic_write_json(state_path, state)
    return tainted_cells


# ---------------------------------------------------------------------------
# Cell builders
# ---------------------------------------------------------------------------
def build_v1_cells():
    cells = []
    for lens, universe in V1_LENSES:
        for w in PHASE_D_WINDOWS_12:
            cells.append({'stage': 'v1', 'arm': 'vehicle', 'window': w, 'lens': lens,
                          'universe': universe, 'ct_promote': True, 'ctsl_enabled': True,
                          'tier_point': VEHICLE_TIER_POINT,
                          'key': ('v1', 'vehicle', w, universe, lens)})
    return cells


def build_v2_cells():
    """PREREG V2: rerun the vehicle at its OWN derived never-fill rate -- the
    honest lens. 12 windows + the survivor arm on the decision windows."""
    cells = [{'stage': 'v2', 'arm': 'vehicle', 'window': w, 'lens': 'vehicle',
              'universe': 'full', 'ct_promote': True, 'ctsl_enabled': True,
              'tier_point': VEHICLE_TIER_POINT,
              'key': ('v2', 'vehicle', w, 'full', 'vehicle')}
             for w in PHASE_D_WINDOWS_12]
    cells += [{'stage': 'v2', 'arm': 'vehicle', 'window': w, 'lens': 'vehicle',
               'universe': 'survivor', 'ct_promote': True, 'ctsl_enabled': True,
               'tier_point': VEHICLE_TIER_POINT,
               'key': ('v2', 'vehicle', w, 'survivor', 'vehicle')}
              for w in DECISION_WINDOWS]
    return cells


# PREREG V3 axes. 0.30 gross / 30 DTE is the anchor (already measured in V1/V2),
# so the grid below is the 5 NON-anchor combinations only.
V3_GROSS_AXIS = [0.30, 0.45, 0.60]
V3_DTE_AXIS = [30, 45]


def build_v3_cells():
    cells = []
    for g in V3_GROSS_AXIS:
        for d in V3_DTE_AXIS:
            if g == 0.30 and d == 30:
                continue                      # the anchor -- V2's own row
            for w in DECISION_WINDOWS:
                cells.append({'stage': 'v3', 'arm': f'g{g}_d{d}', 'window': w,
                              'lens': 'vehicle', 'universe': 'full', 'dte': d, 'gross': g,
                              'ct_promote': True, 'ctsl_enabled': True,
                              'tier_point': VEHICLE_TIER_POINT,
                              'key': ('v3', f'g{g}_d{d}', w, 'full', 'vehicle')})
    return cells


def build_v3_survivor_cells(arms):
    """PREREG V3: 'labels stick only after survivor'. Run ONLY for arms that
    already cleared the raw lane -- passed in explicitly, never guessed."""
    return [{'stage': 'v3s', 'arm': a, 'window': w, 'lens': 'vehicle',
             'universe': 'survivor',
             'dte': int(a.split('_d')[1]), 'gross': float(a.split('_d')[0][1:]),
             'ct_promote': True, 'ctsl_enabled': True, 'tier_point': VEHICLE_TIER_POINT,
             'key': ('v3s', a, w, 'survivor', 'vehicle')}
            for a in arms for w in DECISION_WINDOWS]


# PREREG V5 cube. drop {15/30/50%} x MISS_P {vehicle-rate, 0.25, 0.40}, plus
# the (0-drop, vehicle-rate) cube anchor, on the two eras + the two modern
# decision windows. The PIT-mcap existence-floor axis is NOT implemented --
# see FINDINGS.md; it is reported NOT RUN rather than approximated.
V5_DROPS = [0.15, 0.30, 0.50]
V5_MISSPS = [None, 0.25, 0.40]     # None = the V2-derived vehicle rate
V5_WINDOWS = ['dotcom', 'gfc'] + DECISION_WINDOWS


def build_v5_cells():
    cells = []
    for w in V5_WINDOWS:
        cells.append({'stage': 'v5', 'arm': 'cube_anchor', 'window': w, 'lens': 'vehicle',
                      'universe': 'full', 'ct_promote': True, 'ctsl_enabled': True,
                      'tier_point': VEHICLE_TIER_POINT,
                      'key': ('v5', 'cube_anchor', w, 'full', 'vehicle')})
        for d in V5_DROPS:
            for m in V5_MISSPS:
                arm = f"drop{int(d*100)}_mp{'veh' if m is None else int(m*100)}"
                cells.append({'stage': 'v5', 'arm': arm, 'window': w, 'lens': 'vehicle',
                              'universe': 'full', 'drop_frac': d, 'miss_p': m,
                              'ct_promote': True, 'ctsl_enabled': True,
                              'tier_point': VEHICLE_TIER_POINT,
                              'key': ('v5', arm, w, 'full', 'vehicle')})
    return cells


def build_v1_ctsloff_cells():
    return [{'stage': 'v1c', 'arm': 'ctsl_off', 'window': w, 'lens': 'calibrated',
             'universe': 'full', 'ct_promote': True, 'ctsl_enabled': False,
             'tier_point': VEHICLE_TIER_POINT,
             'key': ('v1c', 'ctsl_off', w, 'full', 'calibrated')}
            for w in PHASE_D_WINDOWS_12]


# ---------------------------------------------------------------------------
# V1 report -- raw tables + sleeve composites + THIN flags (no verdicts here;
# verdicts are read off the PREREG's locked rules in FINDINGS.md)
# ---------------------------------------------------------------------------
def _read_rows(*jobs):
    rows = []
    for j in jobs:
        p = os.path.join(OUT_DIR, f'ctsl_{j}.csv')
        if os.path.isfile(p):
            rows += list(csv.DictReader(open(p, encoding='utf-8')))
    return rows


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def thin_flag(med_trades):
    if med_trades is None:
        return 'NA'
    if med_trades < ANECDOTE_TRADES:
        return 'ANECDOTE'
    if med_trades < THIN_TRADES:
        return 'THIN'
    return ''


def run_v1_report(log_path, out_csv):
    rows = _read_rows('v1', 'v1c')
    if not rows:
        raise SystemExit("[STOP] no v1 rows found -- run --stage v1 first")
    _tee(f"\n{'='*100}\nV1 REPORT (raw tables; verdicts live in the PREREG's locked rules)", log_path)

    order = {w: i for i, w in enumerate(PHASE_D_WINDOWS_12)}
    comp_rows = []
    for lens, universe in V1_LENSES:
        sel = sorted([r for r in rows if r['stage'] == 'v1' and r['lens'] == lens
                      and r['universe'] == universe], key=lambda r: order.get(r['window'], 99))
        if not sel:
            continue
        _tee(f"\n-- lens={lens} universe={universe} --", log_path)
        _tee(f"{'window':<11}{'med%':>10}{'DD%':>8}{'coll%':>7}{'medTrd':>8}{'ctShr':>7}"
            f"{'r15Shr':>8}{'r15PnL':>8}{'SPY%':>10}{'HISA%':>8}  flag", log_path)
        for r in sel:
            mt = _f(r['median_trades_per_path'])
            _tee(f"{r['window']:<11}{_f(r['med_ret']):>+10.1f}{_f(r['worst_dd']):>8.1f}"
                f"{_f(r['p_coll']):>7.1f}{(mt if mt is not None else -1):>8.0f}"
                f"{(_f(r['ct_row_share']) or 0)*100:>6.1f}%"
                f"{(_f(r['routed15_row_share']) or 0)*100:>7.1f}%"
                f"{(_f(r['routed15_pnl_share']) or 0)*100:>7.1f}%"
                f"{(_f(r['spy_return_pct']) or 0):>+10.1f}{(_f(r['hisa_return_pct']) or 0):>8.1f}"
                f"  {thin_flag(mt)}", log_path)

        # PREREG V1 sleeve composites -- same-window arithmetic ONLY.
        _tee(f"   sleeve composites (arithmetic, same window; NO correlation modeling):", log_path)
        _tee(f"{'window':<11}" + ''.join(f"{lab:>12}" for lab, _ in SLEEVE_WEIGHTS)
            + f"{'vehDD%':>9}{'note':>6}", log_path)
        for r in sel:
            spy = _f(r['spy_return_pct'])
            veh = _f(r['med_ret'])
            if spy is None or veh is None:
                continue
            line = f"{r['window']:<11}"
            for lab, wt in SLEEVE_WEIGHTS:
                comp = wt * veh + (1 - wt) * spy
                line += f"{comp:>+12.1f}"
                comp_rows.append({'lens': lens, 'universe': universe, 'window': r['window'],
                                  'sleeve': lab, 'weight_vehicle': wt, 'vehicle_med_ret': veh,
                                  'spy_return_pct': spy, 'composite_return_pct': round(comp, 4),
                                  'vehicle_worst_dd': _f(r['worst_dd']),
                                  'composite_dd_proxy': round(wt * (_f(r['worst_dd']) or 0), 4),
                                  'median_trades_per_path': _f(r['median_trades_per_path']),
                                  'thin_flag': thin_flag(_f(r['median_trades_per_path']))})
            _tee(line + f"{_f(r['worst_dd']):>9.1f}{thin_flag(_f(r['median_trades_per_path'])):>6}", log_path)

    # AMENDMENT-1 item 2: CTSL materiality read.
    base = {r['window']: r for r in rows if r['stage'] == 'v1'
            and r['lens'] == 'calibrated' and r['universe'] == 'full'}
    off = {r['window']: r for r in rows if r['stage'] == 'v1c'}
    if off:
        _tee(f"\n-- AMENDMENT-1 diagnostic: CTSL_ENABLED=0 vs vehicle (calibrated, full) --", log_path)
        _tee(f"{'window':<11}{'veh med%':>11}{'ctsloff%':>11}{'delta pp':>10}"
            f"{'vehDD':>8}{'offDD':>8}{'medTrd':>8}", log_path)
        for w in PHASE_D_WINDOWS_12:
            if w in base and w in off:
                b, o = _f(base[w]['med_ret']), _f(off[w]['med_ret'])
                _tee(f"{w:<11}{b:>+11.1f}{o:>+11.1f}{o-b:>+10.1f}"
                    f"{_f(base[w]['worst_dd']):>8.1f}{_f(off[w]['worst_dd']):>8.1f}"
                    f"{_f(off[w]['median_trades_per_path']) or -1:>8.0f}", log_path)
        deltas = {w: _f(off[w]['med_ret']) - _f(base[w]['med_ret'])
                  for w in DECISION_WINDOWS if w in base and w in off}
        material = all(abs(d) >= CTSL_MATERIAL_PP for d in deltas.values()) and len(deltas) == len(DECISION_WINDOWS)
        _tee(f"   locked rule: |delta| >= {CTSL_MATERIAL_PP}pp on BOTH decision windows -> MATERIAL. "
            f"deltas={ {k: round(v,2) for k,v in deltas.items()} } -> "
            f"{'MATERIAL' if material else 'INERT within this vehicle'}", log_path)

    if comp_rows:
        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        with open(out_csv, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(comp_rows[0].keys()))
            w.writeheader()
            w.writerows(comp_rows)
        _tee(f"\n[V1-REPORT] composites -> {out_csv}", log_path)
    return 0


# PREREG V3 lane (LOCKED): "median +5.0pp both windows, DD not worse >2.0pp,
# collapse 0, survivor-robust -- labels stick only after survivor."
V3_LANE_MED_PP = 5.0
V3_LANE_DD_PP = 2.0


def v3_lane(anchor, arm):
    """Returns (passes_raw_lane, reasons). anchor/arm are {window: row} dicts."""
    reasons = []
    ok = True
    for w in DECISION_WINDOWS:
        if w not in anchor or w not in arm:
            return False, [f'{w}: missing cell']
        dmed = _f(arm[w]['med_ret']) - _f(anchor[w]['med_ret'])
        ddd = _f(arm[w]['worst_dd']) - _f(anchor[w]['worst_dd'])
        coll = _f(arm[w]['p_coll'])
        if dmed < V3_LANE_MED_PP:
            ok = False; reasons.append(f'{w}: med {dmed:+.1f}pp < +{V3_LANE_MED_PP}')
        if ddd > V3_LANE_DD_PP:
            ok = False; reasons.append(f'{w}: DD {ddd:+.1f}pp worse than +{V3_LANE_DD_PP}')
        if coll and coll > 0:
            ok = False; reasons.append(f'{w}: collapse {coll:.1f}% != 0')
    return ok, reasons


def run_v3_report(log_path):
    rows = _read_rows('v2', 'v3', 'v3s')
    if not rows:
        raise SystemExit("[STOP] no v2/v3 rows -- run --stage v2 then --stage v3 first")
    anchor = {r['window']: r for r in rows if r['stage'] == 'v2'
              and r['universe'] == 'full' and r['window'] in DECISION_WINDOWS}
    _tee(f"\n{'='*100}\nV3 REPORT -- levers vs the V2 honest-lens anchor "
        f"(gross 0.30 / DTE 30, lens=vehicle)", log_path)
    _tee(f"anchor: " + '  '.join(
        f"{w} med={_f(anchor[w]['med_ret']):+.1f}% DD={_f(anchor[w]['worst_dd']):.1f}%"
        for w in DECISION_WINDOWS if w in anchor), log_path)
    _tee(f"\n{'arm':<12}{'window':<9}{'med%':>9}{'dMed pp':>9}{'DD%':>8}{'dDD pp':>8}"
        f"{'coll%':>7}{'medTrd':>8}{'maxOpen%':>10}", log_path)
    arms = sorted({r['arm'] for r in rows if r['stage'] == 'v3'})
    passers = []
    for a in arms:
        cur = {r['window']: r for r in rows if r['stage'] == 'v3' and r['arm'] == a}
        for w in DECISION_WINDOWS:
            if w not in cur:
                continue
            r = cur[w]
            _tee(f"{a:<12}{w:<9}{_f(r['med_ret']):>+9.1f}"
                f"{_f(r['med_ret'])-_f(anchor[w]['med_ret']):>+9.1f}"
                f"{_f(r['worst_dd']):>8.1f}{_f(r['worst_dd'])-_f(anchor[w]['worst_dd']):>+8.1f}"
                f"{_f(r['p_coll']):>7.1f}{_f(r['median_trades_per_path']) or -1:>8.0f}"
                f"{_f(r['max_open_premium_base_pct']) or 0:>10.1f}", log_path)
        ok, why = v3_lane(anchor, cur)
        _tee(f"  -> {a}: raw lane {'PASS' if ok else 'FAIL'}"
            f"{'' if ok else '  (' + '; '.join(why) + ')'}", log_path)
        if ok:
            passers.append(a)
    _tee(f"\n[V3-RAW-LANE] passers (survivor check owed before any label sticks): "
        f"{passers or 'NONE'}", log_path)

    surv = {(r['arm'], r['window']): r for r in rows if r['stage'] == 'v3s'}
    if surv:
        _tee(f"\n-- survivor confirmation --", log_path)
        for a in passers:
            for w in DECISION_WINDOWS:
                r = surv.get((a, w))
                if r:
                    _tee(f"{a:<12}{w:<9} survivor med={_f(r['med_ret']):+.1f}% "
                        f"DD={_f(r['worst_dd']):.1f}% coll={_f(r['p_coll']):.1f}% "
                        f"medTrd={_f(r['median_trades_per_path']) or -1:.0f}", log_path)
    else:
        _tee("\n[V3-SURVIVOR] not run yet -- every label above is RAW-ONLY", log_path)
    return 0


def run_v5_report(log_path):
    """PREREG V5 reading rule (LOCKED): 'an era conclusion counts only if
    invariant across the cube' -- operationalised as sign-stability of the
    median return across every (drop x MISS_P) cell of that window."""
    rows = [r for r in _read_rows('v5') if r['stage'] == 'v5']
    if not rows:
        raise SystemExit("[STOP] no v5 rows -- run --stage v5 first")
    _tee(f"\n{'='*100}\nV5 REPORT -- era-honesty cube", log_path)
    _tee("NOTE: the PIT-mcap existence-floor axis of the PREREG's cube is NOT RUN "
        "(no point-in-time existence filter exists in the engine; approximating it "
        "would have been a fabricated axis). The cube below is drop x MISS_P only.", log_path)
    for w in V5_WINDOWS:
        sel = [r for r in rows if r['window'] == w]
        if not sel:
            continue
        _tee(f"\n-- window={w} -- ({len(sel)} cells)", log_path)
        _tee(f"{'arm':<16}{'med%':>10}{'DD%':>8}{'coll%':>7}{'medTrd':>8}{'nDrop':>8}{'missP':>8}", log_path)
        meds = []
        for r in sorted(sel, key=lambda r: r['arm']):
            m = _f(r['med_ret'])
            meds.append(m)
            _tee(f"{r['arm']:<16}{m:>+10.1f}{_f(r['worst_dd']):>8.1f}{_f(r['p_coll']):>7.1f}"
                f"{_f(r['median_trades_per_path']) or -1:>8.0f}"
                f"{_f(r['n_drop_signals']) or 0:>8.0f}{_f(r['miss_p']) or 0:>8.3f}", log_path)
        pos = sum(1 for m in meds if m > 0)
        invariant = (pos == len(meds)) or (pos == 0)
        colls = [_f(r['p_coll']) or 0 for r in sel]
        _tee(f"  -> sign across the cube: {pos}/{len(meds)} cells positive; "
            f"median range [{min(meds):+.1f}, {max(meds):+.1f}]; max collapse {max(colls):.1f}% "
            f"-> era conclusion {'INVARIANT (counts)' if invariant else 'NOT INVARIANT (does not count)'}",
            log_path)
    return 0


def selftest() -> int:
    log = print
    log("=== ctsl_run.py OFFLINE SELF-TESTS ===")

    assert VEHICLE_TIER_POINT == 'ultra-only'
    assert TIER_FUNDING_POINTS['ultra-only'] == {'ultra': 0.20, 'top': 0.0, 'mid': 0.0, 'low': 0.0}
    log("  [1] vehicle == frontier's 'ultra-only' tier point, values unchanged OK")

    assert _lens_env('calibrated') == {'TP_FILL_MISS_P': '0.15', 'TP_FILL_GAP_AWARE': '1'}
    assert _lens_env('buffer') == {'TP_FILL_MISS_P': '0.20', 'TP_FILL_GAP_AWARE': '1'}
    assert _lens_env('canon') == {'TP_FILL_MISS_P': '0.0', 'TP_FILL_GAP_AWARE': '0'}
    try:
        _lens_env('nope')
        raise AssertionError("unknown lens should raise")
    except SystemExit:
        pass
    log("  [2] lens env mapping (calibrated/buffer/canon) + unknown-lens STOP OK")

    c1 = build_v1_cells()
    assert len(c1) == len(V1_LENSES) * len(PHASE_D_WINDOWS_12) == 48, len(c1)
    assert len({c['key'] for c in c1}) == 48
    assert all(c['ctsl_enabled'] is True and c['ct_promote'] is True for c in c1)
    c2 = build_v1_ctsloff_cells()
    assert len(c2) == 12 and all(c['ctsl_enabled'] is False for c in c2)
    assert len({c['key'] for c in c2}) == 12
    assert not ({c['key'] for c in c1} & {c['key'] for c in c2})
    log("  [3] V1 = 48 unique cells (4 lenses x 12 windows) + 12 CTSL-off, no key collision OK")

    assert thin_flag(None) == 'NA' and thin_flag(9) == 'ANECDOTE'
    assert thin_flag(10) == 'THIN' and thin_flag(29) == 'THIN' and thin_flag(30) == ''
    log("  [4] THIN/ANECDOTE boundaries match PREREG (<30 THIN, <10 ANECDOTE) OK")

    assert [w for _, w in SLEEVE_WEIGHTS] == [0.00, 0.15, 0.30]
    veh, spy = 100.0, 20.0
    assert abs((0.30 * veh + 0.70 * spy) - 44.0) < 1e-9
    log("  [5] sleeve weights {100spy, 85/15, 70/30} + composite arithmetic OK")

    assert TAPES_DIR.startswith(_EXP_DIR) and OUT_DIR.startswith(_EXP_DIR)
    assert 'frontier_2026_08' not in TAPES_DIR and 'sentinel_guards' not in OUT_DIR
    log("  [6] every write path is inside THIS campaign dir (closed dirs untouched) OK")

    assert set(V_SCHEMA) >= {'ctsl_enabled', 'ct_promote', 'n_routed15_signals',
                             'n_routed15_rows', 'routed15_row_share', 'routed15_pnl_share',
                             'ct_row_share', 'spy_return_pct', 'hisa_return_pct'}
    assert len(V_SCHEMA) == len(set(V_SCHEMA))
    log("  [7] schema carries the AMENDMENT-1 router columns + CTSL axis, no dupes OK")

    assert DECISION_WINDOWS == ['22-now', '5y'] and CTSL_MATERIAL_PP == 5.0
    assert len(PHASE_D_WINDOWS_12) == 12 and '2020_crash' in PHASE_D_WINDOWS_12
    log("  [8] decision windows, CTSL materiality bar, 12-window grid incl 2020_crash OK")

    c2 = build_v2_cells()
    assert len(c2) == 12 + 2 and len({c['key'] for c in c2}) == 14
    assert all(c['lens'] == 'vehicle' for c in c2)
    c3 = build_v3_cells()
    assert len(c3) == (len(V3_GROSS_AXIS) * len(V3_DTE_AXIS) - 1) * 2 == 10, len(c3)
    assert not any(c['gross'] == 0.30 and c['dte'] == 30 for c in c3), "anchor must be excluded"
    assert {c['arm'] for c in c3} == {'g0.3_d30', 'g0.3_d45', 'g0.45_d30',
                                      'g0.45_d45', 'g0.6_d30', 'g0.6_d45'} - {'g0.3_d30'}
    assert max(V3_GROSS_AXIS) == 0.60, "PREREG bans sizing beyond 0.60"
    log(f"  [9] V2 = 14 cells (12 full + 2 survivor) at the derived lens; V3 = {len(c3)} "
        f"cells, anchor excluded, gross capped at 0.60 OK")

    cs = build_v3_survivor_cells(['g0.45_d45'])
    assert len(cs) == 2 and cs[0]['gross'] == 0.45 and cs[0]['dte'] == 45
    assert all(c['universe'] == 'survivor' for c in cs)
    log("  [10] v3-survivor cells parse arm labels back to (gross, dte) correctly OK")

    anchor = {'22-now': {'med_ret': '100', 'worst_dd': '25', 'p_coll': '0'},
              '5y':     {'med_ret': '200', 'worst_dd': '25', 'p_coll': '0'}}
    good = {'22-now': {'med_ret': '106', 'worst_dd': '26', 'p_coll': '0'},
            '5y':     {'med_ret': '210', 'worst_dd': '25', 'p_coll': '0'}}
    assert v3_lane(anchor, good)[0] is True
    thin = {'22-now': {'med_ret': '104', 'worst_dd': '25', 'p_coll': '0'},
            '5y':     {'med_ret': '210', 'worst_dd': '25', 'p_coll': '0'}}
    assert v3_lane(anchor, thin)[0] is False          # +4.0pp < +5.0pp on one window
    ddbad = {'22-now': {'med_ret': '110', 'worst_dd': '27.5', 'p_coll': '0'},
             '5y':     {'med_ret': '210', 'worst_dd': '25', 'p_coll': '0'}}
    assert v3_lane(anchor, ddbad)[0] is False         # DD +2.5pp worse than +2.0pp
    collbad = {'22-now': {'med_ret': '110', 'worst_dd': '25', 'p_coll': '0.4'},
               '5y':     {'med_ret': '210', 'worst_dd': '25', 'p_coll': '0'}}
    assert v3_lane(anchor, collbad)[0] is False       # any collapse fails
    assert v3_lane(anchor, {'22-now': good['22-now']})[0] is False   # missing window fails
    log("  [11] V3 lane rule: +5.0pp BOTH windows, DD not worse >2.0pp, collapse 0, "
        "missing cell = FAIL OK")

    c5 = build_v5_cells()
    assert len(c5) == len(V5_WINDOWS) * (1 + len(V5_DROPS) * len(V5_MISSPS)) == 40, len(c5)
    assert len({c['key'] for c in c5}) == 40
    assert V5_DROPS == [0.15, 0.30, 0.50] and V5_MISSPS == [None, 0.25, 0.40]
    assert {'dotcom', 'gfc'} < set(V5_WINDOWS) and set(DECISION_WINDOWS) < set(V5_WINDOWS)
    anch = [c for c in c5 if c['arm'] == 'cube_anchor']
    assert len(anch) == 4 and all('drop_frac' not in c for c in anch)
    assert any(c['arm'] == 'drop50_mp40' for c in c5)
    log(f"  [12] V5 cube = {len(c5)} cells ({len(V5_WINDOWS)} windows x "
        f"[1 anchor + {len(V5_DROPS)}x{len(V5_MISSPS)}]), dot-com + GFC present OK")

    assert set(V5_EXTRA_WINDOWS) == {'dotcom', 'gfc'}
    assert V5_EXTRA_WINDOWS['dotcom'] == ('2000-01-01', '2002-12-31')
    import datetime as _dt

    class _FakeMC:
        WINDOWS = [('2018', _dt.date(2018, 1, 1), _dt.date(2018, 12, 31))]
    fm = _FakeMC()
    _install_extra_windows(fm, os.path.join(LOG_DIR, '_selftest.log'))
    labels = [w[0] for w in fm.WINDOWS]
    assert labels == ['2018', 'dotcom', 'gfc'], labels
    assert all(isinstance(w[1], _dt.date) for w in fm.WINDOWS)
    _install_extra_windows(fm, os.path.join(LOG_DIR, '_selftest.log'))
    assert [w[0] for w in fm.WINDOWS] == labels, "second call must be idempotent"
    log("  [13] era windows install as real dates, are idempotent, and never mutate "
        "monte_carlo.py on disk OK")

    log("=== SELFTEST PASS ===")
    return 0


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--selftest', action='store_true')
    p.add_argument('--stage', default=None,
                   choices=['v1', 'v1-ctsloff', 'v1-report', 'v2', 'v3', 'v3-survivor',
                            'v3-report', 'v5', 'v5-report'])
    p.add_argument('--arms', default=None, help='comma list of v3 arms for --stage v3-survivor')
    p.add_argument('--job', default=None)
    p.add_argument('--n-iter', type=int, default=N_ITER_DEFAULT)
    p.add_argument('--windows', default=None, help='comma list of window overrides')
    p.add_argument('--cell-worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--cell-stage', default=None, help=argparse.SUPPRESS)
    p.add_argument('--arm', default=None, help=argparse.SUPPRESS)
    p.add_argument('--window', default=None, help=argparse.SUPPRESS)
    p.add_argument('--universe', default='full', help=argparse.SUPPRESS)
    p.add_argument('--lens', default='calibrated', help=argparse.SUPPRESS)
    p.add_argument('--tier-point', default=VEHICLE_TIER_POINT, help=argparse.SUPPRESS)
    p.add_argument('--dte', type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument('--gross', type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument('--no-ct-promote', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--no-ctsl', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--drop-frac', type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument('--miss-p', type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument('--out-csv', default=None, help=argparse.SUPPRESS)
    p.add_argument('--log-path', default=None, help=argparse.SUPPRESS)
    return p.parse_args()


def main():
    a = parse_args()
    if a.selftest:
        return selftest()

    if a.cell_worker:
        if not a.cell_stage:
            raise SystemExit("[STOP] --cell-worker requires --cell-stage")
        run_v_cell(a.cell_stage, a.arm, a.window, a.n_iter, a.out_csv, a.log_path, a.job,
                   universe=a.universe, lens=a.lens, dte=a.dte, gross=a.gross,
                   ct_promote=not a.no_ct_promote, ctsl_enabled=not a.no_ctsl,
                   tier_point=a.tier_point, miss_p=a.miss_p, drop_frac=a.drop_frac)
        return 0

    if not a.stage:
        raise SystemExit("[STOP] --stage required (or --selftest)")
    job = a.job or a.stage.replace('-', '')
    out_csv, state_path, log_path = _paths_for_job(job)

    if a.stage == 'v1-report':
        return run_v1_report(log_path, os.path.join(OUT_DIR, 'ctsl_v1_composites.csv'))
    if a.stage == 'v3-report':
        return run_v3_report(log_path)
    if a.stage == 'v5-report':
        return run_v5_report(log_path)

    if a.stage == 'v1':
        cells = build_v1_cells()
    elif a.stage == 'v1-ctsloff':
        cells = build_v1_ctsloff_cells()
    elif a.stage == 'v2':
        cells = build_v2_cells()
    elif a.stage == 'v3':
        cells = build_v3_cells()
    elif a.stage == 'v5':
        cells = build_v5_cells()
    else:   # v3-survivor
        if not a.arms:
            raise SystemExit("[STOP] --stage v3-survivor requires --arms (raw-lane passers only)")
        cells = build_v3_survivor_cells(a.arms.split(','))
    if a.windows:
        keep = set(a.windows.split(','))
        cells = [c for c in cells if c['window'] in keep]
    _run_orchestrator(job, a.stage, cells, a.n_iter, log_path, state_path, out_csv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
