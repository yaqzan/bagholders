"""Ext-Negative Floor Lift sweep — calibrate post-crash dampener.

Tests a put-side floor lift parameterized by EMA50 extension depth.
For each variant: applies lift to v31 baseline scores, re-buckets, computes
WR15/WR30/Ret30/N per cumulative call/put bucket. Reports vs baseline.

Lift formula:
    if overall <= score_gate AND ext < ext_gate:
        depth = clip(0, 1, (ext_gate - ext) / ext_range)
        strength = depth * K
        overall += strength * (lift_target - overall)

Reuses the parquet caches built by fast_variant_runner.
"""
from __future__ import annotations
import io, sys, sqlite3, json, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
RUNNER_CACHE = ROOT / '.cache' / 'runner'
LOCAL_CACHE = ROOT / '.cache' / 'post_crash'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 1825
BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30),
           ('60d', 60), ('90d', 90), ('150d', 150)]


def load_data(version_id, refresh=False):
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    scores_pq = RUNNER_CACHE / f'scores_v{version_id}_{LOOKBACK_DAYS}.parquet'
    barriers_pq = RUNNER_CACHE / f'barriers_{LOOKBACK_DAYS}.parquet'
    ext_pq = LOCAL_CACHE / f'ext_{LOOKBACK_DAYS}.parquet'

    # Reuse fast_variant_runner cache
    if scores_pq.exists() and not refresh:
        t0 = time.time()
        scores = pl.read_parquet(scores_pq)
        print(f'[load] scores parquet: {len(scores):,} rows in {time.time()-t0:.1f}s', flush=True)
    else:
        # Trigger build via runner
        from experiments.fast_variant_runner import FastVariantRunner
        r = FastVariantRunner(version_id=version_id, lookback_days=LOOKBACK_DAYS)
        r.load(refresh=refresh)
        scores = r.df
        print(f'[load] scores via runner: {len(scores):,} rows', flush=True)

    if barriers_pq.exists() and not refresh:
        t0 = time.time()
        barriers = pl.read_parquet(barriers_pq)
        print(f'[load] barriers parquet: {len(barriers):,} rows in {time.time()-t0:.1f}s', flush=True)
    else:
        raise RuntimeError('barriers parquet missing - run fast_variant_runner first')

    # Ext data: build once, cache as parquet
    if ext_pq.exists() and not refresh:
        t0 = time.time()
        ext_df = pl.read_parquet(ext_pq)
        print(f'[load] ext parquet: {len(ext_df):,} rows in {time.time()-t0:.1f}s', flush=True)
    else:
        from database.models.core import Stock
        DB = Stock._meta.database
        t0 = time.time()
        cur = DB.execute_sql(f"""
            SELECT i.symbol, i.date, i.ema_50, p.close
            FROM indicators i
            JOIN price_history p ON p.symbol = i.symbol AND p.date = i.date
            WHERE i.date >= '{cutoff}'
              AND i.ema_50 IS NOT NULL AND i.ema_50 > 0
        """)
        rows = cur.fetchall()
        syms, dates, exts = [], [], []
        for sym, d, ema, close in rows:
            if ema is None or float(ema) == 0:
                continue
            syms.append(sym)
            dates.append(d.isoformat() if hasattr(d, 'isoformat') else d)
            exts.append(float((float(close) - float(ema)) / float(ema) * 100.0))
        ext_df = pl.DataFrame({'symbol': syms, 'date': dates, 'ext': exts})
        ext_df.write_parquet(ext_pq)
        print(f'[load] built ext parquet: {len(ext_df):,} rows in {time.time()-t0:.1f}s', flush=True)

    # Pre-join scores + ext (one-time)
    scores = scores.join(ext_df, on=['symbol', 'date'], how='left')
    null_ext = scores.filter(pl.col('ext').is_null()).height
    print(f'[load] scores after ext join: {len(scores):,} (null ext: {null_ext})', flush=True)
    # Drop scores without ext (pre-EMA50 window)
    scores = scores.filter(pl.col('ext').is_not_null())
    return scores, barriers


def apply_ext_neg_floor(scores, k, ext_gate, ext_range, score_gate, lift_target):
    """Apply ext-negative floor lift formula. Returns scores with new_overall column."""
    # depth in [0, 1] grows as ext gets more negative beyond ext_gate
    depth = ((ext_gate - pl.col('ext')) / ext_range).clip(0.0, 1.0)
    strength = depth * k

    fires = (
        (pl.col('overall') <= score_gate)
        & (pl.col('ext') < ext_gate)
        & (depth > 0)
    )
    new_overall_expr = pl.col('overall') + strength * (lift_target - pl.col('overall'))

    return scores.with_columns(
        pl.when(fires)
          .then(new_overall_expr)
          .otherwise(pl.col('overall'))
          .round()
          .clip(0, 100)
          .cast(pl.Int64)
          .alias('new_overall')
    )


def aggregate(scores, barriers):
    """Bucket peaks, join barriers, compute per-bucket WR/Ret stats."""
    df_peaks = scores.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    joined = df_peaks.join(barriers, on=['symbol', 'date', 'side'], how='inner')

    bucket_results = []
    for t in BUY_THRESHOLDS:
        sub = joined.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket'))
        bucket_results.append(sub)
    for t in SELL_THRESHOLDS:
        sub = joined.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket'))
        bucket_results.append(sub)
    bucketed = pl.concat(bucket_results, how='vertical')

    agg = bucketed.group_by(['bucket', 'w_days']).agg([
        pl.len().alias('n'),
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
        pl.col('result').sum().alias('wins'),
        pl.col('exit_return').mean().alias('avg_return'),
    ])

    stats = {}
    for row in agg.iter_rows(named=True):
        bk = row['bucket']
        w = row['w_days']
        label = next((l for l, w_ in PERIODS if w_ == w), None)
        if label is None: continue
        n = row['n_resolved'] or 0
        wins = row['wins'] or 0
        s = stats.setdefault(bk, {})
        s[f'wr_{label}'] = (wins / n * 100) if n else None
        s[f'ret_{label}'] = row['avg_return']
        if w == 30:
            s['n'] = n
        elif 'n' not in s and w == 15:
            s['n'] = n
    return stats


def fmt_delta(val, base, fmt='5.1f', unit='%'):
    if val is None or base is None:
        return '   --   '
    d = val - base
    return f'{val:{fmt}}{unit} ({d:+5.2f})'


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'[sweep] active version: id={av.id}', flush=True)

    scores, barriers = load_data(av.id)

    # Baseline: no lift
    base_scores = scores.with_columns(pl.col('overall').alias('new_overall'))
    base_stats = aggregate(base_scores, barriers)
    print(f'\n[sweep] baseline computed (n_<25 = {base_stats.get("<25", {}).get("n", "?")})', flush=True)

    # Sweep variants
    # Format: (label, K, ext_gate, ext_range, score_gate, lift_target)
    variants = [
        ('baseline',          0.0,  -25.0, 25.0, 25, 35),
        # AGGRESSIVE — push deep cliff out of put territory entirely
        ('AGGR_T50_K1',       1.0,  -30.0, 20.0, 25, 50),  # at ext=-50: 100% lift to 50
        ('AGGR_T50_K1_G35',   1.0,  -35.0, 15.0, 25, 50),  # narrower cliff
        ('AGGR_T50_K1_G40',   1.0,  -40.0, 10.0, 25, 50),  # only at extreme
        # Wider gate — lift puts from -15% extension onward
        ('WIDE_G15_K05',      0.5,  -15.0, 25.0, 25, 35),
        ('WIDE_G20_K05',      0.5,  -20.0, 25.0, 25, 40),
        ('WIDE_G15_K07',      0.7,  -15.0, 25.0, 25, 35),
        # Combined: wide AND aggressive
        ('COMBO_WIDE_AGGR',   1.0,  -15.0, 35.0, 25, 50),
        ('COMBO_W20_T50',     0.7,  -20.0, 30.0, 25, 50),
        # Reduced sub-bucket — only deep puts
        ('DEEP_PUTS_K1',      1.0,  -25.0, 25.0, 15, 50),  # only puts <=15
        ('DEEP_PUTS_K07',     0.7,  -30.0, 20.0, 15, 40),
    ]

    print('\n' + '=' * 145)
    print(f'{"variant":<15} {"<5 WR15":>14} {"<10 WR15":>14} {"<15 WR15":>14} {"<25 WR15":>14} {"70+ WR15":>14} {"75+ WR15":>14} {"80+ WR15":>14} {"<25 N":>10} {"time":>6}')
    print('-' * 145)

    base_n25 = base_stats.get('<25', {}).get('n', 0) or 0

    for label, k, eg, er, sg, lt in variants:
        t0 = time.time()
        if k == 0.0:
            stats = base_stats
        else:
            scored = apply_ext_neg_floor(scores, k, eg, er, sg, lt)
            stats = aggregate(scored, barriers)
        elapsed = time.time() - t0

        def cell(bucket, period='15d'):
            v = stats.get(bucket, {}).get(f'wr_{period}')
            b = base_stats.get(bucket, {}).get(f'wr_{period}')
            return fmt_delta(v, b)

        n25 = stats.get('<25', {}).get('n', 0) or 0
        n_pct = (n25 / base_n25 * 100) if base_n25 else 0

        print(f'{label:<15} {cell("<5"):>14} {cell("<10"):>14} {cell("<15"):>14} {cell("<25"):>14} {cell("70+"):>14} {cell("75+"):>14} {cell("80+"):>14} {n25:>5} ({n_pct:>3.0f}%) {elapsed:>5.1f}s')

    # Detailed view for a few key variants
    print('\n' + '=' * 100)
    print('DETAILED — best variants per-bucket WR15 / Ret30 / N')
    print('-' * 100)
    detail = ['baseline', 'AGGR_T50_K1', 'COMBO_WIDE_AGGR', 'DEEP_PUTS_K1', 'WIDE_G15_K07']
    detail_data = {}
    for label, k, eg, er, sg, lt in variants:
        if label not in detail:
            continue
        if k == 0.0:
            stats = base_stats
        else:
            scored = apply_ext_neg_floor(scores, k, eg, er, sg, lt)
            stats = aggregate(scored, barriers)
        detail_data[label] = stats

    bucket_order = ['<5', '<10', '<15', '<20', '<25', '70+', '75+', '80+', '85+']
    for bk in bucket_order:
        print(f'\nBucket {bk}:')
        print(f'  {"variant":<15} {"WR15":>10} {"WR30":>10} {"Ret30":>10} {"N":>8}')
        for v in detail:
            s = detail_data[v].get(bk, {})
            wr15 = s.get('wr_15d')
            wr30 = s.get('wr_30d')
            ret30 = s.get('ret_30d')
            n = s.get('n', 0)
            wr15s = f'{wr15:.1f}%' if wr15 is not None else '--'
            wr30s = f'{wr30:.1f}%' if wr30 is not None else '--'
            ret30s = f'{ret30:+.3f}' if ret30 is not None else '--'
            print(f'  {v:<15} {wr15s:>10} {wr30s:>10} {ret30s:>10} {n:>8}')


if __name__ == '__main__':
    main()
