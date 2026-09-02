"""Profile F3F scaling factor distribution under v46 substrate (5y).

Question: F3F was calibrated under v25-era contaminated breadth distribution.
Post-v45 (ETF de-contamination) and v46 (WVD-Wave score-stage modulator), is
the F3F curve still well-calibrated for the breadth distribution that signals
actually see today?

Method:
  1. Load v46 score parquet (.cache/regime_bands/v45_1825d.parquet — contains v46)
  2. Join MarketBreadth.breadth_score on signal date
  3. Filter to qualifying signals (calls overall≥70, puts ≤25)
  4. Compute current F3F scale per signal (30 DTE config)
  5. Report:
     - breadth distribution at qualifying-signal dates (calls vs puts separately)
     - F3F scale distribution: % at floor (0.50), % at 1.0, % in transition
     - per-tier breakdown (95+, 90-94, 85-89, 80-84, 75-79, 70-74, <30/<25/<20/<15/<10)
     - WR (option-aligned barrier 30dte_opt @ w=15d) by F3F-scale bin to detect miscalibration

If the current F3F curve is allocating heavily to a low-WR breadth band, or
flooring at a band where signals are still alpha-positive, that signals
recalibration headroom.
"""
from __future__ import annotations
import io, sys, time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PARQ = ROOT / '.cache' / 'regime_bands' / 'v45_1825d.parquet'

# Current 30 DTE production config
F3F_CALL_THRESH = 50.0
F3F_CALL_FLOOR  = 0.50
F3F_CALL_LOW    = 30.0
F3F_PUT_THRESH  = 75.0
F3F_PUT_FLOOR   = 0.50
F3F_PUT_HIGH    = 95.0


def f3f_scale(breadth: float, is_put: bool) -> float:
    if breadth is None:
        return 1.0
    if is_put:
        if breadth <= F3F_PUT_THRESH:
            return 1.0
        if breadth >= F3F_PUT_HIGH:
            return F3F_PUT_FLOOR
        return 1.0 - (breadth - F3F_PUT_THRESH) / (F3F_PUT_HIGH - F3F_PUT_THRESH) * (1.0 - F3F_PUT_FLOOR)
    else:
        if breadth >= F3F_CALL_THRESH:
            return 1.0
        if breadth <= F3F_CALL_LOW:
            return F3F_CALL_FLOOR
        return F3F_CALL_FLOOR + (breadth - F3F_CALL_LOW) / (F3F_CALL_THRESH - F3F_CALL_LOW) * (1.0 - F3F_CALL_FLOOR)


def main():
    from database.trader_database import DB
    from database.models.core import MarketBreadth
    from datetime import date, timedelta

    print(f'[load] v46 score parquet from {PARQ}', flush=True)
    t0 = time.time()
    df = pl.read_parquet(PARQ)
    print(f'  rows={len(df):,}  ({time.time()-t0:.0f}s)', flush=True)

    # Pull MarketBreadth time series
    print('[load] MarketBreadth daily...', flush=True)
    t0 = time.time()
    mb = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(MarketBreadth.breadth_score.is_null(False))
        .order_by(MarketBreadth.date)
        .dicts()
    )
    df_mb = pl.DataFrame({
        'date': [str(r['date'])[:10] for r in mb],
        'breadth_score': [float(r['breadth_score']) for r in mb],
    })
    print(f'  rows={len(df_mb):,}  ({time.time()-t0:.0f}s)', flush=True)

    df = df.join(df_mb, on='date', how='inner')
    print(f'[joined] rows with breadth = {len(df):,}', flush=True)

    # Tag side / qualifying
    df = df.with_columns([
        pl.when(pl.col('overall') >= 70).then(pl.lit('call'))
          .when(pl.col('overall') <= 25).then(pl.lit('put'))
          .otherwise(pl.lit('neutral')).alias('side'),
    ])
    df_q = df.filter(pl.col('side') != 'neutral')
    print(f'[qualifying] calls={len(df_q.filter(pl.col("side")=="call")):,}  puts={len(df_q.filter(pl.col("side")=="put")):,}')

    # Compute F3F scale
    df_q = df_q.with_columns([
        pl.struct(['breadth_score', 'side']).map_elements(
            lambda r: f3f_scale(r['breadth_score'], r['side'] == 'put'),
            return_dtype=pl.Float64,
        ).alias('f3f_scale'),
    ])

    # Tier tagging
    def tier_for(row):
        ov = row['overall']
        side = row['side']
        if side == 'call':
            if ov >= 95: return '95+'
            if ov >= 90: return '90-94'
            if ov >= 85: return '85-89'
            if ov >= 80: return '80-84'
            if ov >= 75: return '75-79'
            return '70-74'
        else:  # put
            if ov <= 5: return '<5'
            if ov <= 10: return '<10'
            if ov <= 15: return '<15'
            if ov <= 20: return '<20'
            return '<25'

    df_q = df_q.with_columns([
        pl.struct(['overall', 'side']).map_elements(tier_for, return_dtype=pl.Utf8).alias('tier'),
    ])

    # ---- Report 1: Breadth distribution at qualifying-signal dates ----
    print('\n=== Breadth distribution at qualifying-signal dates ===')
    print('\n[CALLS] (overall ≥ 70)')
    calls = df_q.filter(pl.col('side') == 'call')
    bs = calls['breadth_score']
    pcts = [bs.quantile(p) for p in [0.05, 0.25, 0.50, 0.75, 0.95]]
    print(f'  N={len(calls):,}  mean={bs.mean():.1f}  p5={pcts[0]:.0f}  p25={pcts[1]:.0f}  p50={pcts[2]:.0f}  p75={pcts[3]:.0f}  p95={pcts[4]:.0f}')

    print('\n[PUTS] (overall ≤ 25)')
    puts = df_q.filter(pl.col('side') == 'put')
    bs = puts['breadth_score']
    pcts = [bs.quantile(p) for p in [0.05, 0.25, 0.50, 0.75, 0.95]]
    print(f'  N={len(puts):,}  mean={bs.mean():.1f}  p5={pcts[0]:.0f}  p25={pcts[1]:.0f}  p50={pcts[2]:.0f}  p75={pcts[3]:.0f}  p95={pcts[4]:.0f}')

    # ---- Report 2: F3F scale distribution ----
    print('\n=== F3F scale distribution at qualifying-signal dates ===')
    for sd in ['call', 'put']:
        sub = df_q.filter(pl.col('side') == sd)
        scale = sub['f3f_scale']
        n_floor = (scale <= F3F_CALL_FLOOR + 1e-6).sum()
        n_full  = (scale >= 1.0 - 1e-6).sum()
        n_mid   = len(sub) - n_floor - n_full
        print(f'\n[{sd.upper()}] (N={len(sub):,})')
        print(f'  At floor ({F3F_CALL_FLOOR:.2f}): {n_floor:,} ({100*n_floor/len(sub):.1f}%)')
        print(f'  At full  (1.00): {n_full:,} ({100*n_full/len(sub):.1f}%)')
        print(f'  In transition  : {n_mid:,} ({100*n_mid/len(sub):.1f}%)')
        print(f'  Mean scale     : {scale.mean():.3f}')

    # ---- Report 3: WR by F3F scale bin ----
    print('\n=== WR (option-aligned barrier 30dte_opt @ w=15) by F3F scale bin ===')
    print('Bins: 0.50 (floor), 0.50-0.625, 0.625-0.75, 0.75-0.875, 0.875-1.0, 1.00 (full)')

    bin_edges = [0.49, 0.51, 0.625, 0.75, 0.875, 0.999, 1.001]
    bin_labels = ['0.50_floor', '0.50-0.625', '0.625-0.75', '0.75-0.875', '0.875-0.999', '1.00_full']

    for sd, wr_col in [('call', 'opt_result_15_call'), ('put', 'opt_result_15_put')]:
        print(f'\n[{sd.upper()}]')
        sub = df_q.filter(
            (pl.col('side') == sd) & pl.col(wr_col).is_not_null()
        )
        if len(sub) == 0:
            print('  no resolved signals')
            continue
        # cut into bins
        for lo, hi, lbl in zip(bin_edges[:-1], bin_edges[1:], bin_labels):
            bsub = sub.filter((pl.col('f3f_scale') > lo) & (pl.col('f3f_scale') <= hi))
            if len(bsub) == 0:
                print(f'  {lbl:>14s}: N=0')
                continue
            wr = float(bsub[wr_col].mean()) * 100
            print(f'  {lbl:>14s}: N={len(bsub):>7,}  WR15={wr:5.2f}%')

    # ---- Report 4: WR by breadth decile (ground truth — what alpha should drive F3F) ----
    print('\n=== WR by breadth decile (ground-truth signal — what F3F SHOULD respond to) ===')
    for sd, wr_col in [('call', 'opt_result_15_call'), ('put', 'opt_result_15_put')]:
        print(f'\n[{sd.upper()}]')
        sub = df_q.filter(
            (pl.col('side') == sd) & pl.col(wr_col).is_not_null()
        )
        if len(sub) == 0:
            print('  no resolved signals')
            continue
        # Decile cuts on breadth
        deciles = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        for lo, hi in zip(deciles[:-1], deciles[1:]):
            bsub = sub.filter((pl.col('breadth_score') >= lo) & (pl.col('breadth_score') < hi))
            if len(bsub) == 0:
                print(f'  brd [{lo:>3}-{hi:>3}): N=0')
                continue
            wr = float(bsub[wr_col].mean()) * 100
            n = len(bsub)
            cur_scale = f3f_scale((lo+hi)/2, sd == 'put')
            print(f'  brd [{lo:>3}-{hi:>3}): N={n:>7,}  WR15={wr:5.2f}%  current F3F scale={cur_scale:.2f}')


if __name__ == '__main__':
    main()
