"""Fine-grained PCD parameter grid — settles cutoff, ramp, tier-conditional,
and ret_5d-vs-ret_10d structural questions in one pass.

Per-variant: discrete cutoff (or narrow ramp), evaluated at 1y/3y/5y. Reports
WR15 lift per put tier and H5 sign-consistency for each.

Variants tested:
  - Cutoff sweep on ret_10d (discrete): -0.10, -0.12, -0.13, -0.15, -0.17, -0.20
  - Same cutoffs with narrow ramp (cutoff -> cutoff - 0.03)
  - Tier-conditional: cutoff=-0.10 for <=15, cutoff=-0.15 for <=25
  - Tier-conditional: cutoff=-0.12 for <=15, cutoff=-0.15 for <=25
  - ret_5d as discriminator (parallel to best ret_10d): cutoff=-0.10 discrete

This is a complete grid, not a pre-pruned set. Outputs the full response surface.
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

WINDOWS = [('1y', 365), ('3y', 1095), ('5y', 1825)]
BUCKETS = ['<5', '<15', '<25']


def evaluate(runner_df, barrier_df, ret_df, cfg):
    """cfg dict supports:
       feature: 'ret_10d' or 'ret_5d'
       cutoff_lo / cutoff_hi (ramp endpoints; same value = discrete)
       cutoff_lo_le15 / cutoff_hi_le15 (optional tier-conditional override for overall<=15)
       k, target, gate
    """
    df = runner_df.join(ret_df, on=['symbol', 'date'], how='left')
    feat = pl.col(cfg['feature'])
    k = cfg.get('k', 1.0)
    target = cfg.get('target', 30)
    gate = cfg.get('gate', 25)

    # Standard cutoff
    lo = cfg['cutoff_lo']
    hi = cfg['cutoff_hi']
    weakness = ((lo - feat) / max(lo - hi, 1e-9)).clip(0.0, 1.0)
    df = df.with_columns(weakness.alias('w_std'))

    # Tier-conditional override for <=15
    if 'cutoff_lo_le15' in cfg:
        lo15 = cfg['cutoff_lo_le15']
        hi15 = cfg['cutoff_hi_le15']
        w15 = ((lo15 - feat) / max(lo15 - hi15, 1e-9)).clip(0.0, 1.0)
        df = df.with_columns(w15.alias('w_le15'))
        # Merge: use w_le15 when overall<=15, w_std otherwise
        df = df.with_columns(
            pl.when(pl.col('overall') <= 15)
              .then(pl.col('w_le15'))
              .otherwise(pl.col('w_std'))
              .alias('weakness')
        )
    else:
        df = df.with_columns(pl.col('w_std').alias('weakness'))

    df = df.with_columns(
        pl.when((pl.col('overall') <= gate) & (pl.col('weakness') > 0) & feat.is_not_null())
          .then(pl.col('overall') + k * pl.col('weakness') * (target - pl.col('overall')))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )

    BUY = [95, 90, 85, 80, 75, 70]
    SELL = [30, 25, 20, 15, 10, 5]
    df_peaks = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df_peaks = df_peaks.sort(['symbol', 'date']).with_columns(
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
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
        pl.col('result').sum().alias('wins'),
    ]).filter(pl.col('w_days') == 15)
    out = {}
    for row in agg.iter_rows(named=True):
        n = row['n_resolved'] or 0
        wins = row['wins'] or 0
        out[row['bucket']] = (wins / n * 100 if n else None, n)
    return out


def main():
    print('[finegrid] loading ...', flush=True)
    runner = FastVariantRunner()
    runner.load(verbose=False)
    ret_df = pl.read_parquet(RET10D_PATH)
    print(f'[finegrid] {len(runner.df):,} scores / {len(ret_df):,} ret rows', flush=True)

    today = date.today()
    subs = {}
    for w_label, days in WINDOWS:
        cutoff = (today - timedelta(days=days)).isoformat()
        subs[w_label] = (
            runner.df.filter(pl.col('date') >= cutoff),
            runner.barrier_df.filter(pl.col('date') >= cutoff),
        )

    # Variants — fully enumerated grid
    cutoffs = [-0.10, -0.12, -0.13, -0.15, -0.17, -0.20]
    variants = []

    # Baseline
    variants.append({'label': 'baseline', 'feature': 'ret_10d',
                     'cutoff_lo': -999, 'cutoff_hi': -999.001, 'gate': 0})

    # Discrete ret_10d at each cutoff
    for c in cutoffs:
        variants.append({'label': f'r10_DISC_{int(-c*100)}', 'feature': 'ret_10d',
                         'cutoff_lo': c, 'cutoff_hi': c - 1e-5})

    # Narrow ramp ret_10d (3pp width)
    for c in cutoffs:
        variants.append({'label': f'r10_RAMP3_{int(-c*100)}', 'feature': 'ret_10d',
                         'cutoff_lo': c, 'cutoff_hi': c - 0.03})

    # Tier-conditional (broader cutoff for <=15)
    variants.append({'label': 'TC_le15-10_le25-15', 'feature': 'ret_10d',
                     'cutoff_lo': -0.15, 'cutoff_hi': -0.15001,
                     'cutoff_lo_le15': -0.10, 'cutoff_hi_le15': -0.10001})
    variants.append({'label': 'TC_le15-12_le25-15', 'feature': 'ret_10d',
                     'cutoff_lo': -0.15, 'cutoff_hi': -0.15001,
                     'cutoff_lo_le15': -0.12, 'cutoff_hi_le15': -0.12001})

    # ret_5d alternative discriminators (per original profile, sharp 5d drop also negative)
    variants.append({'label': 'r5_DISC_10', 'feature': 'ret_5d',
                     'cutoff_lo': -0.10, 'cutoff_hi': -0.10001})
    variants.append({'label': 'r5_DISC_15', 'feature': 'ret_5d',
                     'cutoff_lo': -0.15, 'cutoff_hi': -0.15001})

    # Run all variants on all windows
    results = {}
    for v in variants:
        v_results = {}
        for w_label, (df_w, b_w) in subs.items():
            t0 = time.time()
            v_results[w_label] = evaluate(df_w, b_w, ret_df, v)
            v_results[f'{w_label}_t'] = time.time() - t0
        results[v['label']] = v_results

    # Reference baseline at each window
    base = results['baseline']

    # Print table
    print('\n' + '=' * 145)
    print(f"{'Variant':<22} | {'tier':<5} | {'1y lift':>14} {'3y lift':>14} {'5y lift':>14} {'1y N':>7} {'5y N':>7}  H5  Total")
    print('-' * 145)

    for v in variants:
        label = v['label']
        if label == 'baseline':
            continue
        for tier in BUCKETS:
            cells_lift, cells_N = [], []
            sign = []
            for w_label, _ in WINDOWS:
                cur_wr, cur_n = results[label][w_label].get(tier, (None, 0))
                base_wr, base_n = base[w_label].get(tier, (None, 0))
                if cur_wr is None or base_wr is None:
                    cells_lift.append('   --   ')
                    cells_N.append('  --  ')
                    continue
                d = cur_wr - base_wr
                cells_lift.append(f'{d:+5.2f}pp')
                cells_N.append(f'{cur_n}')
                sign.append(d)

            # H5: all sign-consistent (allow tiny noise on the small 1y bucket)
            if not sign:
                h5 = '?'
            else:
                all_pos = all(d > -0.10 for d in sign)
                all_neg = all(d < 0.10 for d in sign)
                h5 = '✓' if all_pos else ('✗' if not all_neg else '~')
            tot = sum(sign) if sign else 0
            print(f"{label:<22} | {tier:<5} | "
                  f"{cells_lift[0]:>14} {cells_lift[1]:>14} {cells_lift[2]:>14} "
                  f"{cells_N[0]:>7} {cells_N[2]:>7}  {h5}  {tot:+5.2f}")
        print()

    # ── Composite scoring: which variant has best total lift across all 3 tiers x 3 windows AND H5-passes? ──
    print('\n' + '=' * 100)
    print('COMPOSITE RANKING — sum of lift across 9 cells (3 tiers x 3 windows), require H5 pass on all 3 tiers')
    print('-' * 100)
    print(f"{'Variant':<22}  {'sum_lift':>10} {'<5_5y':>8} {'<15_5y':>8} {'<25_5y':>8} {'all_H5_pass?':>14}")
    print('-' * 100)

    rankings = []
    for v in variants:
        label = v['label']
        if label == 'baseline':
            continue
        sum_lift = 0
        all_pass = True
        per_tier_5y = {}
        for tier in BUCKETS:
            sign = []
            for w_label, _ in WINDOWS:
                cur_wr, _ = results[label][w_label].get(tier, (None, 0))
                base_wr, _ = base[w_label].get(tier, (None, 0))
                if cur_wr is not None and base_wr is not None:
                    d = cur_wr - base_wr
                    sum_lift += d
                    sign.append(d)
                    if w_label == '5y':
                        per_tier_5y[tier] = d
            if sign and not all(d > -0.10 for d in sign):
                all_pass = False
        rankings.append((sum_lift, label, per_tier_5y, all_pass))

    rankings.sort(key=lambda x: -x[0])
    for sum_lift, label, t5y, h5_ok in rankings:
        print(f"{label:<22}  {sum_lift:>+10.2f}  "
              f"{t5y.get('<5', 0):>+7.2f}  {t5y.get('<15', 0):>+7.2f}  {t5y.get('<25', 0):>+7.2f}  "
              f"{'✓' if h5_ok else '✗':>14}")


if __name__ == '__main__':
    main()
