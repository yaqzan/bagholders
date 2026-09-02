"""Sigma-normalized PCD sweep — discrete and ramp variants on ret_10d_sigma.

Vol-fair discriminator: ret_10d / (sigma_pct/100 * sqrt(10)) where sigma_pct
is 60-day realized daily volatility. Same sigma definition the strategy's
TP/SL barriers use.

Evaluates 1y/3y/5y per variant against generic barrier. Then verifies top
candidates against option-aligned 30dte_opt barrier.
"""
from __future__ import annotations
import io, sys, math, time, sqlite3
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.fast_variant_runner import FastVariantRunner

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
WINDOWS = [('1y', 365), ('3y', 1095), ('5y', 1825)]
BUCKETS = ['<5', '<15', '<25']

# Need ret_10d AND sigma_pct per (symbol, date). Build once.
RET_SIGMA_PATH = ROOT / '.cache' / 'post_crash_v2' / 'ret_sigma_all_1825.parquet'


def build_ret_sigma_parquet():
    """Build ret_10d + sigma_pct per (symbol, date) covering all signal-eligible dates."""
    if RET_SIGMA_PATH.exists():
        return pl.read_parquet(RET_SIGMA_PATH)

    print('[build] building ret+sigma parquet ...', flush=True)
    from database.trader_database import DB
    cutoff = (date.today() - timedelta(days=1825 + 90)).isoformat()
    cur = DB.execute_sql(f"""
        SELECT symbol, date, close FROM price_history WHERE date >= '{cutoff}'
    """)
    rows = cur.fetchall()
    ph = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'close': [float(r[2]) for r in rows],
    }).sort(['symbol', 'date'])
    print(f'  loaded {len(ph):,} price rows', flush=True)

    ph = ph.with_columns([
        pl.col('close').pct_change(10).over('symbol').alias('ret_10d'),
        pl.col('close').pct_change(1).over('symbol').alias('ret_1d'),
    ])
    ph = ph.with_columns(
        # 60-day realized daily stdev (% scale, matches assess methodology)
        (pl.col('ret_1d').rolling_std(window_size=60).over('symbol') * 100).alias('sigma_pct_60'),
    )
    out = ph.select(['symbol', 'date', 'ret_10d', 'sigma_pct_60'])
    out = out.filter(pl.col('ret_10d').is_not_null() & pl.col('sigma_pct_60').is_not_null() & (pl.col('sigma_pct_60') > 0))
    out = out.with_columns(
        (pl.col('ret_10d') / (pl.col('sigma_pct_60') / 100.0 * math.sqrt(10))).alias('ret_10d_sigma')
    )
    print(f'  computed sigma-norm: {len(out):,} rows', flush=True)
    RET_SIGMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(RET_SIGMA_PATH)
    return out


def evaluate(scores_df, barrier_df, ret_df, cfg):
    df = scores_df.join(ret_df, on=['symbol', 'date'], how='left')
    feat = pl.col(cfg['feature'])
    k = cfg.get('k', 1.0)
    target = cfg.get('target', 30)
    gate = cfg.get('gate', 25)
    lo = cfg['cutoff_lo']; hi = cfg['cutoff_hi']
    weakness = ((lo - feat) / max(lo - hi, 1e-9)).clip(0.0, 1.0)
    df = df.with_columns(weakness.alias('weakness'))
    df = df.with_columns(
        pl.when((pl.col('overall') <= gate) & (pl.col('weakness') > 0) & feat.is_not_null())
          .then(pl.col('overall') + k * pl.col('weakness') * (target - pl.col('overall')))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64).alias('new_overall')
    )

    BUY = [95, 90, 85, 80, 75, 70]
    SELL = [30, 25, 20, 15, 10, 5]
    df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    joined = df_peaks.join(barrier_df, on=['symbol', 'date', 'side'], how='inner')

    bucket_results = []
    for t in BUY:
        bucket_results.append(joined.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket')))
    for t in SELL:
        bucket_results.append(joined.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket')))
    bucketed = pl.concat(bucket_results, how='vertical')
    agg = bucketed.group_by(['bucket', 'w_days']).agg([
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n'),
        pl.col('result').sum().alias('wins'),
    ]).filter(pl.col('w_days') == 15)
    out = {}
    for row in agg.iter_rows(named=True):
        n = row['n'] or 0; wins = row['wins'] or 0
        out[row['bucket']] = (wins / n * 100 if n else None, n)
    return out


def main():
    print('[sigma-sweep] loading runner ...', flush=True)
    runner = FastVariantRunner()
    runner.load(verbose=False)
    ret_df = build_ret_sigma_parquet()
    print(f'[sigma-sweep] {len(runner.df):,} scores / {len(ret_df):,} feature rows', flush=True)

    today = date.today()
    subs_gen = {}
    for w_label, days in WINDOWS:
        c = (today - timedelta(days=days)).isoformat()
        subs_gen[w_label] = (
            runner.df.filter(pl.col('date') >= c),
            runner.barrier_df.filter(pl.col('date') >= c),
        )

    # Variants
    sigma_cuts = [-1.0, -1.25, -1.5, -1.75, -2.0, -2.5]
    variants = [{'label': 'baseline', 'feature': 'ret_10d_sigma',
                 'cutoff_lo': -999, 'cutoff_hi': -999.001, 'gate': 0}]
    for s in sigma_cuts:
        variants.append({'label': f'SIG_DISC_{abs(int(s*100))}', 'feature': 'ret_10d_sigma',
                         'cutoff_lo': s, 'cutoff_hi': s - 1e-5})
    # Narrow ramp (0.5sigma width)
    for s in sigma_cuts:
        variants.append({'label': f'SIG_RAMP05_{abs(int(s*100))}', 'feature': 'ret_10d_sigma',
                         'cutoff_lo': s, 'cutoff_hi': s - 0.5})
    # Tier-conditional: stricter cutoff for <=15
    variants.append({'label': 'SIG_TC_le15-1.0_le25-1.5', 'feature': 'ret_10d_sigma',
                     'cutoff_lo': -1.5, 'cutoff_hi': -1.50001,
                     'cutoff_lo_le15': -1.0, 'cutoff_hi_le15': -1.00001})
    # Compare also: raw ret_10d at -10 and -15 (for direct apples-to-apples reference vs sigma)
    # Need to add ret_10d to the join — use the raw column from ret_df
    variants.append({'label': 'RAW_DISC_10', 'feature': 'ret_10d',
                     'cutoff_lo': -0.10, 'cutoff_hi': -0.10001})
    variants.append({'label': 'RAW_DISC_15', 'feature': 'ret_10d',
                     'cutoff_lo': -0.15, 'cutoff_hi': -0.15001})

    # Run grid
    base = None
    results = {}
    for v in variants:
        # Tier-conditional support inline
        per_w = {}
        for w_label, (sd, bd) in subs_gen.items():
            t0 = time.time()
            if 'cutoff_lo_le15' in v:
                # Two-pass: lift le15 with stricter cut, then le25 with normal cut
                lo15 = v['cutoff_lo_le15']; hi15 = v['cutoff_hi_le15']
                lo25 = v['cutoff_lo']; hi25 = v['cutoff_hi']
                df = sd.join(ret_df, on=['symbol', 'date'], how='left')
                feat = pl.col(v['feature'])
                w15 = ((lo15 - feat) / max(lo15 - hi15, 1e-9)).clip(0.0, 1.0)
                w25 = ((lo25 - feat) / max(lo25 - hi25, 1e-9)).clip(0.0, 1.0)
                df = df.with_columns([w15.alias('w_15'), w25.alias('w_25')])
                df = df.with_columns(
                    pl.when((pl.col('overall') <= 15) & (pl.col('w_15') > 0))
                      .then(pl.col('overall') + 1.0 * pl.col('w_15') * (30 - pl.col('overall')))
                      .when((pl.col('overall') <= 25) & (pl.col('w_25') > 0))
                      .then(pl.col('overall') + 1.0 * pl.col('w_25') * (30 - pl.col('overall')))
                      .otherwise(pl.col('overall'))
                      .round().clip(0, 100).cast(pl.Int64).alias('new_overall')
                )
                # Aggregate inline (duplicating logic for tier-conditional)
                df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
                df_peaks = df_peaks.with_columns(
                    pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
                )
                joined = df_peaks.join(bd, on=['symbol', 'date', 'side'], how='inner')
                bucket_results = []
                for thr in [95, 90, 85, 80, 75, 70]:
                    bucket_results.append(joined.filter(pl.col('new_overall') >= thr).with_columns(pl.lit(f'{thr}+').alias('bucket')))
                for thr in [30, 25, 20, 15, 10, 5]:
                    bucket_results.append(joined.filter(pl.col('new_overall') <= thr).with_columns(pl.lit(f'<{thr}').alias('bucket')))
                bucketed = pl.concat(bucket_results, how='vertical')
                agg = bucketed.group_by(['bucket', 'w_days']).agg([
                    pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n'),
                    pl.col('result').sum().alias('wins'),
                ]).filter(pl.col('w_days') == 15)
                rr = {}
                for row in agg.iter_rows(named=True):
                    n = row['n'] or 0; wins = row['wins'] or 0
                    rr[row['bucket']] = (wins / n * 100 if n else None, n)
                per_w[w_label] = rr
            else:
                per_w[w_label] = evaluate(sd, bd, ret_df, v)
        results[v['label']] = per_w
        if v['label'] == 'baseline':
            base = per_w

    # Print
    print('\n' + '=' * 145)
    print(f"{'Variant':<25} | {'tier':<4} | {'1y lift':>11} {'3y lift':>11} {'5y lift':>11} {'1y N':>6} {'5y N':>7}  H5  Total  Type")
    print('-' * 145)

    summary_rows = []
    for v in variants:
        label = v['label']
        if label == 'baseline':
            continue
        sum_lift = 0.0
        all_h5 = True
        per_5y = {}
        for tier in BUCKETS:
            sign = []
            cells_l, cells_n = [], []
            for w_label, _ in WINDOWS:
                cur_wr, cur_n = results[label][w_label].get(tier, (None, 0))
                base_wr, _ = base[w_label].get(tier, (None, 0))
                if cur_wr is None or base_wr is None:
                    cells_l.append('--'); cells_n.append('--'); continue
                d = cur_wr - base_wr
                cells_l.append(f'{d:+5.2f}pp')
                cells_n.append(f'{cur_n}')
                sign.append(d)
                sum_lift += d
                if w_label == '5y':
                    per_5y[tier] = d
            if sign and not all(d > -0.10 for d in sign):
                all_h5 = False
            type_marker = 'sigma' if 'SIG' in label else 'raw'
            mark = '✓' if (sign and all(d > -0.10 for d in sign)) else '✗'
            print(f"{label:<25} | {tier:<4} | "
                  f"{cells_l[0]:>11} {cells_l[1]:>11} {cells_l[2]:>11} "
                  f"{cells_n[0]:>6} {cells_n[2]:>7}  {mark}  {sum(sign) if sign else 0:+5.2f}  {type_marker}")
        summary_rows.append((sum_lift, label, per_5y, all_h5))
        print()

    # Composite
    print('\n' + '=' * 110)
    print('COMPOSITE RANKING (sum across 9 cells, must pass H5 on all 3 tiers)')
    print('-' * 110)
    print(f"{'Variant':<25}  {'sum_lift':>10}  {'<5_5y':>8} {'<15_5y':>8} {'<25_5y':>8}  {'H5?':>4}  {'5y N drop %':>13}")
    print('-' * 110)
    summary_rows.sort(key=lambda x: -x[0])
    base_n25 = base['5y'].get('<25', (None, 0))[1]
    for sum_lift, label, t5y, h5 in summary_rows:
        n25_5y = results[label]['5y'].get('<25', (None, 0))[1]
        n_drop = (n25_5y - base_n25) / base_n25 * 100 if base_n25 else 0
        print(f"{label:<25}  {sum_lift:>+10.2f}  "
              f"{t5y.get('<5', 0):>+7.2f}  {t5y.get('<15', 0):>+7.2f}  {t5y.get('<25', 0):>+7.2f}  "
              f"{'✓' if h5 else '✗':>4}  {n_drop:>+12.1f}%")


if __name__ == '__main__':
    main()
