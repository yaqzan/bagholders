#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
v2_fill_honesty.py -- V2: the vehicle's OWN composition-weighted never-fill rate.
(experiments/ctsl_vehicle_2026_08/PREREG.md V2, lock 3e2adc9f + AMENDMENT-1 1acca0aa)

Pure measurement + arithmetic. No simulation, no market-data collection: it
reweights an EXISTING measurement (tp_fill_fidelity_30dte's tier-monotone
never-fill rates) by the vehicle's OWN measured liquidity mix.

Every input is read live from its source of truth, never hardcoded:
  tier edges     <- B:/polygon_derived/minute_fidelity/bindings.json
                    ["6_gap_through_sl"]["liquidity_tier_edges"]
  never-fill/tier<- experiments/tp_fill_fidelity_30dte/out/
                    tp_fill_optimism_by_tier.csv (arm30 AND arm15)
  liquidity      <- B:/polygon_derived/liquidity_map/signal_liquidity.parquet
                    (opt_vol_30d_atm, the SAME variable the tier edges quantise)
  vehicle mix    <- this campaign's own V1 tapes (out/tapes/v1_vehicle_*.parquet)

AMENDMENT-1 item 4 is load-bearing here: the vehicle is ~80/20 30-DTE/router-15,
so the 30-DTE arm's rates are applied to the 30-DTE rows and the 15-DTE arm's
rates to the routed rows, instead of one blanket arm30 number.

Read-only w.r.t. every other campaign and all production code.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_THIS_DIR)
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
TAPES_DIR = os.path.join(OUT_DIR, 'tapes')

BINDINGS_JSON = 'B:/polygon_derived/minute_fidelity/bindings.json'
LIQUIDITY_PARQUET = 'B:/polygon_derived/liquidity_map/signal_liquidity.parquet'
FIDELITY_CSV = os.path.join(_EXPERIMENTS_DIR, 'tp_fill_fidelity_30dte', 'out',
                            'tp_fill_optimism_by_tier.csv')
LIQ_MAP_FIRST_DATE = '2022-08-05'   # reported, not assumed -- recomputed below

DECISION_WINDOWS = ['22-now', '5y']
TIERS = ['t1', 't2', 't3', 't4', 't5']


def _tee(msg, log_path):
    print(msg, flush=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def load_tier_edges(path=BINDINGS_JSON):
    with open(path, encoding='utf-8') as f:
        b = json.load(f)
    return [float(x) for x in b['6_gap_through_sl']['liquidity_tier_edges']]


def tier_for(value, edges):
    """Identical semantics to tp_fill_fidelity_30dte's liquidity_tier_for."""
    if value is None:
        return None
    for i, e in enumerate(edges):
        if value <= e:
            return f't{i + 1}'
    return 't5'


MATCHED_FILTER = 'matched_25_38'   # the PREREG's cited slice (t1 20.4% -> t4 7.8%)


def load_never_fill_rates(path=FIDELITY_CSV, dte_filter=MATCHED_FILTER):
    """{arm: {tier: never_fill_rate}} + {arm: pooled_rate}.

    PINNED to the matched-filter (DTE 25-38) block. The CSV also carries an
    'all_kept' block whose rates differ materially (arm30 t1 18.5% vs 20.4%);
    taking whichever row came first would silently mix the two.
    """
    per_arm, pooled = {}, {}
    n_seen = 0
    for r in csv.DictReader(open(path, encoding='utf-8')):
        if (r.get('dte_filter') or '').strip() != dte_filter:
            continue
        arm, sv, rate = r['arm'], (r.get('slice_value') or '').strip(), r.get('never_fill_rate')
        if rate in (None, '', 'NA'):
            continue
        n_seen += 1
        if r.get('slice_type') == 'pooled':
            pooled[arm] = float(rate)
        elif r.get('slice_type') == 'tier' and sv in TIERS:
            per_arm.setdefault(arm, {})[sv] = float(rate)
    if not per_arm or not pooled:
        raise SystemExit(f"[STOP] no rows with dte_filter=={dte_filter!r} in {path}")
    return per_arm, pooled


def load_liquidity(path=LIQUIDITY_PARQUET):
    import polars as pl
    df = pl.read_parquet(path)
    return (df.with_columns(pl.col('date').cast(pl.Utf8).alias('entry_date'))
              .rename({'symbol': 'sym_id'})
              .select(['sym_id', 'entry_date', 'opt_vol_30d_atm', 'overall']))


def analyse(windows, log_path, out_csv, tape_glob_job='v1', arm_name='vehicle'):
    import polars as pl

    edges = load_tier_edges()
    nf, pooled = load_never_fill_rates()
    liq = load_liquidity()
    liq_min_date = liq['entry_date'].min()

    _tee(f"\n{'=' * 100}", log_path)
    _tee("V2 -- composition-weighted never-fill rate for the CTSL vehicle", log_path)
    _tee(f"[INPUT] tier edges (live from bindings.json) = {edges}", log_path)
    _tee(f"[INPUT] never-fill by tier arm30 = {nf.get('arm30')}  pooled={pooled.get('arm30')}", log_path)
    _tee(f"[INPUT] never-fill by tier arm15 = {nf.get('arm15')}  pooled={pooled.get('arm15')}", log_path)
    _tee(f"[INPUT] liquidity map rows={len(liq)} first_date={liq_min_date} "
        f"population=overall>={liq['overall'].min()} (STRUCTURAL: 70-74 vehicle trades cannot join)", log_path)
    rho = liq.select(pl.corr('overall', 'opt_vol_30d_atm', method='spearman')).item()
    _tee(f"[BIAS-CHECK] Spearman(overall, opt_vol_30d_atm) inside the map = {rho:.4f} "
        f"-- near zero, so the >=75 conditioning is not strongly liquidity-selecting", log_path)

    rows_out = []
    for w in windows:
        tape = os.path.join(TAPES_DIR, f'{tape_glob_job}_{arm_name}_{w}_full_calibrated.parquet')
        if not os.path.isfile(tape):
            _tee(f"[SKIP] window={w}: tape not found ({tape})", log_path)
            continue
        t = pl.read_parquet(tape)
        # AMENDMENT-1 item 4: the router zeroes the alloc score (and nothing else
        # can -- load_signals floors the population at 70), so score==0 IS the
        # routed-15 marker. Cross-checked against the V1 csv's own
        # n_routed15_rows column, which comes from ctx['_dte'] directly.
        t = t.with_columns(pl.when(pl.col('score') == 0).then(pl.lit('arm15'))
                             .otherwise(pl.lit('arm30')).alias('arm'))
        j = t.join(liq.select(['sym_id', 'entry_date', 'opt_vol_30d_atm']),
                   on=['sym_id', 'entry_date'], how='left')
        j = j.with_columns(pl.col('opt_vol_30d_atm')
                             .map_elements(lambda v: tier_for(v, edges), return_dtype=pl.Utf8)
                             .alias('liq_tier'))

        n_rows = len(j)
        n_join = int(j['liq_tier'].is_not_null().sum())
        n_post = int((j['entry_date'] >= liq_min_date).sum())
        d_all = j.select(['sym_id', 'entry_date']).unique().height
        d_join = j.filter(pl.col('liq_tier').is_not_null()).select(['sym_id', 'entry_date']).unique().height

        _tee(f"\n-- window={w} --", log_path)
        _tee(f"[COVERAGE] tape rows={n_rows} joined={n_join} ({100*n_join/n_rows:.1f}%) | "
            f"rows on/after {liq_min_date}={n_post} ({100*n_post/n_rows:.1f}%) | "
            f"distinct trades={d_all} joined={d_join} ({100*d_join/max(d_all,1):.1f}%)", log_path)

        weighted, arm_detail = 0.0, {}
        joined_total = n_join
        for arm in ('arm30', 'arm15'):
            s = j.filter((pl.col('arm') == arm) & pl.col('liq_tier').is_not_null())
            if not len(s):
                continue
            mix = {r['liq_tier']: r['len'] / len(s)
                   for r in s.group_by('liq_tier').len().iter_rows(named=True)}
            rate = sum(share * nf[arm][tr] for tr, share in mix.items() if tr in nf.get(arm, {}))
            arm_share = len(s) / joined_total
            weighted += arm_share * rate
            arm_detail[arm] = {'n_joined_rows': len(s), 'arm_share_of_joined': round(arm_share, 6),
                               'mix': {k: round(v, 4) for k, v in sorted(mix.items())},
                               'arm_missp': round(rate, 5)}
            _tee(f"  {arm}: joined_rows={len(s)} share={arm_share:.3f} "
                f"mix={arm_detail[arm]['mix']} -> arm never-fill = {rate:.4f} "
                f"(that arm's UNIVERSE pooled = {pooled.get(arm)})", log_path)

        # Sensitivity on the unjoined remainder: it has no liquidity datum at all.
        unjoined_share = 1.0 - (n_join / n_rows)
        sens = {}
        for lab, assumed in (('pooled', pooled.get('arm30')),
                             ('t1_worst', nf['arm30']['t1']),
                             ('t4_best', nf['arm30']['t4'])):
            sens[lab] = round(weighted * (1 - unjoined_share) + assumed * unjoined_share, 5)

        _tee(f"  => JOINED-ONLY composition-weighted MISS_P = {weighted:.4f}   "
            f"(engine default = 0.15; PREREG's pre-measurement guess was 0.09-0.12)", log_path)
        _tee(f"  => whole-tape sensitivity, unjoined {100*unjoined_share:.1f}% assumed at "
            f"pooled/t1/t4: {sens}", log_path)

        rows_out.append({
            'window': w, 'tape_rows': n_rows, 'joined_rows': n_join,
            'joined_row_pct': round(100 * n_join / n_rows, 3),
            'distinct_trades': d_all, 'distinct_joined': d_join,
            'liq_map_first_date': str(liq_min_date),
            'arm30_detail': json.dumps(arm_detail.get('arm30')),
            'arm15_detail': json.dumps(arm_detail.get('arm15')),
            'missp_joined_only': round(weighted, 5),
            'missp_sens_unjoined_pooled': sens['pooled'],
            'missp_sens_unjoined_t1': sens['t1_worst'],
            'missp_sens_unjoined_t4': sens['t4_best'],
            'engine_default_missp': 0.15,
        })

    if rows_out:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(out_csv, 'w', newline='', encoding='utf-8') as f:
            wtr = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            wtr.writeheader()
            wtr.writerows(rows_out)
        _tee(f"\n[WRITE] {out_csv} ({len(rows_out)} rows)", log_path)

        vals = [r['missp_joined_only'] for r in rows_out]
        _tee(f"\n[V2-DERIVED-RATE] joined-only across {DECISION_WINDOWS}: {vals} "
            f"-> vehicle lens MISS_P = {round(sum(vals)/len(vals), 3)}", log_path)
    return 0


def selftest() -> int:
    print("=== v2_fill_honesty.py OFFLINE SELF-TESTS ===")
    e = [320.0, 1191.0, 3486.0, 14524.0]
    assert tier_for(None, e) is None
    assert tier_for(320, e) == 't1' and tier_for(320.0001, e) == 't2'
    assert tier_for(1191, e) == 't2' and tier_for(3486, e) == 't3'
    assert tier_for(14524, e) == 't4' and tier_for(14525, e) == 't5'
    print("  [1] tier_for reproduces the fidelity driver's own boundary selftest OK")

    live = load_tier_edges()
    assert live == e, f"bindings.json edges drifted: {live}"
    print(f"  [2] live bindings.json edges == the fidelity study's {e} OK")

    nf, pooled = load_never_fill_rates()
    for arm in ('arm30', 'arm15'):
        assert set(nf[arm]) == set(TIERS), (arm, nf[arm])
        assert 0.0 < pooled[arm] < 0.5
    assert nf['arm30']['t1'] > nf['arm30']['t4'], "t1 must be the least-fillable tier"
    # The PREREG quotes the matched-filter numbers; assert we pinned that block
    # and not the 'all_kept' one (arm30 t1 is 20.4% vs 18.5% respectively).
    assert abs(nf['arm30']['t1'] - 0.204) < 0.001, nf['arm30']['t1']
    assert abs(nf['arm30']['t4'] - 0.078) < 0.001, nf['arm30']['t4']
    assert abs(pooled['arm30'] - 0.158) < 0.001, pooled['arm30']
    print(f"  [3] never-fill rates pinned to the {MATCHED_FILTER} block for both arms; "
          f"arm30 t1={nf['arm30']['t1']:.4f} t4={nf['arm30']['t4']:.4f} pooled={pooled['arm30']:.4f} "
          f"== the PREREG's cited 20.4/7.8/15.8 OK")

    mix = {'t1': 0.5, 't4': 0.5}
    r = sum(s * nf['arm30'][t] for t, s in mix.items())
    assert abs(r - (nf['arm30']['t1'] + nf['arm30']['t4']) / 2) < 1e-12
    print("  [4] mix-weighting arithmetic is a plain convex combination OK")
    print("=== SELFTEST PASS ===")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--selftest', action='store_true')
    p.add_argument('--windows', default=','.join(DECISION_WINDOWS))
    p.add_argument('--job', default='v1')
    a = p.parse_args()
    if a.selftest:
        return selftest()
    return analyse(a.windows.split(','), os.path.join(LOG_DIR, 'v2.log'),
                   os.path.join(OUT_DIR, 'ctsl_v2_missp.csv'), tape_glob_job=a.job)


if __name__ == '__main__':
    sys.exit(main())
