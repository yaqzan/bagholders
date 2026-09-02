"""Multi-window H5 check on top PCD variants — 1y, 3y, 5y must be sign-consistent.

Reuses the 5y score parquet but date-filters to construct 1y and 3y subsets.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.fast_variant_runner import FastVariantRunner

RET10D_PATH = ROOT / '.cache' / 'post_crash_v2' / 'ret10d_all_1825.parquet'

WINDOWS = [
    ('1y', 365),
    ('3y', 1095),
    ('5y', 1825),
]

VARIANTS = [
    # label, k, ramp_lo, ramp_hi, score_gate, lift_target
    ('DISC_15',    1.0,  -0.15, -0.15001, 25, 30),  # discrete @-15
    ('DISC_15_T35',1.0,  -0.15, -0.15001, 25, 35),  # discrete @-15, push out further
    ('K10_T35',    1.0,  -0.15, -0.25,    25, 35),  # ramp -15..-25, target 35
    ('K10_T40',    1.0,  -0.15, -0.25,    25, 40),  # ramp, target 40
    ('K08_T30',    0.80, -0.15, -0.25,    25, 30),  # mild ramp
]


def evaluate_pcd(runner_df, barrier_df, k, ramp_lo, ramp_hi, score_gate, lift_target, ret10d_df):
    df = runner_df.join(ret10d_df, on=['symbol', 'date'], how='left')
    if k == 0.0:
        df = df.with_columns(pl.col('overall').cast(pl.Int64).alias('new_overall'))
    else:
        weakness = ((ramp_lo - pl.col('ret_10d')) / (ramp_lo - ramp_hi)).clip(0.0, 1.0)
        df = df.with_columns(weakness.alias('weakness_pcd'))
        df = df.with_columns(
            pl.when((pl.col('overall') <= score_gate)
                    & (pl.col('weakness_pcd') > 0)
                    & pl.col('ret_10d').is_not_null())
              .then(pl.col('overall') + k * pl.col('weakness_pcd') * (lift_target - pl.col('overall')))
              .otherwise(pl.col('overall'))
              .round().clip(0, 100).cast(pl.Int64)
              .alias('new_overall')
        )

    # Inline aggregator (matches FastVariantRunner._aggregate_from_df)
    BUY = [95, 90, 85, 80, 75, 70]
    SELL = [30, 25, 20, 15, 10, 5]
    df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.sort(['symbol', 'date']).with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    joined = df_peaks.join(barrier_df, on=['symbol', 'date', 'side'], how='inner')
    bucket_results = []
    for t in BUY:
        sub = joined.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket'))
        bucket_results.append(sub)
    for t in SELL:
        sub = joined.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket'))
        bucket_results.append(sub)
    bucketed = pl.concat(bucket_results, how='vertical')
    agg = bucketed.group_by(['bucket', 'w_days']).agg([
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
        pl.col('result').sum().alias('wins'),
    ])
    out = {}
    for row in agg.iter_rows(named=True):
        if row['w_days'] != 15: continue
        n = row['n_resolved'] or 0
        wins = row['wins'] or 0
        out[row['bucket']] = (wins / n * 100 if n else None, n)
    return out


def main():
    print('[multi] loading runner ...', flush=True)
    runner = FastVariantRunner()
    runner.load(verbose=False)
    ret10d = pl.read_parquet(RET10D_PATH)
    print(f'[multi] {len(runner.df):,} score rows / {len(runner.barrier_df):,} barrier rows / {len(ret10d):,} ret10d rows', flush=True)

    today = date.today()

    # Pre-compute 1y/3y/5y subsets for both score and barrier dataframes
    # IMPORTANT — barrier rows must align too (only barrier outcomes for in-window signal dates)
    subs = {}
    for w_label, days in WINDOWS:
        cutoff = (today - timedelta(days=days)).isoformat()
        df_w = runner.df.filter(pl.col('date') >= cutoff)
        b_w = runner.barrier_df.filter(pl.col('date') >= cutoff)
        subs[w_label] = (df_w, b_w)
        print(f'  {w_label}: {len(df_w):,} score rows, {len(b_w):,} barrier rows', flush=True)

    # Run baseline + variants per window
    print('\n' + '=' * 132)
    print(f"{'Variant':<14} {'tier':<5} | {'1y':<22} {'3y':<22} {'5y':<22}  H5 sign-consistent?")
    print('-' * 132)

    BUCKETS = ['<5', '<15', '<25']

    for variant in VARIANTS:
        label = variant[0]
        # Run baseline + variant on each window
        per_window = {}
        for w_label, (df_w, b_w) in subs.items():
            base = evaluate_pcd(df_w, b_w, 0.0, -0.15, -0.25, 25, 30, ret10d)
            cur = evaluate_pcd(df_w, b_w, *variant[1:], ret10d)
            per_window[w_label] = (base, cur)

        for bucket in BUCKETS:
            cells = []
            deltas = []
            for w_label, _ in WINDOWS:
                base, cur = per_window[w_label]
                base_wr, base_n = base.get(bucket, (None, 0))
                cur_wr, cur_n = cur.get(bucket, (None, 0))
                if base_wr is None or cur_wr is None:
                    cells.append(f"{'--':<22}")
                    continue
                d = cur_wr - base_wr
                deltas.append(d)
                cells.append(f"{cur_wr:5.1f}% (b{base_wr:4.1f} d{d:+5.2f}, N{cur_n})")

            sign_consistent = all(d > 0 for d in deltas) or all(d < 0 for d in deltas) if deltas else False
            mark = 'YES ✓' if sign_consistent and all(d > 0 for d in deltas) else \
                   ('YES (neg)' if sign_consistent else 'NO ✗')
            print(f"{label:<14} {bucket:<5} | {cells[0]:<22} {cells[1]:<22} {cells[2]:<22}  {mark}")
        print()


if __name__ == '__main__':
    main()
