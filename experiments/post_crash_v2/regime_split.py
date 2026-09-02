"""Regime-split the post-crash cohort.

For the strongest under-performing cohort (ret_10d in [-25, -15] OR combined
post-crash), split by:
  - VIX regime: low (<18), mid (18-25), high (>25)
  - Breadth regime: weak (<35), mid (35-65), healthy (>65)
  - Composite regime: STRESS (<30), NEUTRAL (30-60), HEALTHY+ (>60)
  - Year cohort (2021/2022/2023/2024/2025/2026)

If the under-performance concentrates in low-VIX/healthy-breadth/HEALTHY-composite
or specific years, that is the discriminator.

If it holds uniformly across all regime splits, the dampener is unconditional.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / '.cache' / 'post_crash_v2' / 'features_v36_1825.parquet'


def z_two_proportions(wr_a, n_a, wr_b, n_b):
    if n_a == 0 or n_b == 0:
        return 0.0
    p_a, p_b = wr_a / 100.0, wr_b / 100.0
    p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    return (p_a - p_b) / se if se > 0 else 0.0


def split_table(df, tier_pred, tier_label, cohort_pred, cohort_label,
                regime_buckets, regime_axis_name):
    """For each regime bucket, compare cohort WR15 vs not-cohort WR15 within that regime."""
    print(f'\n--- TIER {tier_label}, COHORT={cohort_label}, REGIME AXIS={regime_axis_name} ---')
    tier_df = df.filter(tier_pred & pl.col('result_15').is_not_null())
    print(f'  tier total N = {len(tier_df):,}')
    print(f'  {"regime":<28} {"N_cohort":>9} {"N_other":>9} {"WR_cohort":>11} {"WR_other":>10} {"lift":>7} {"z":>7}')
    for label, pred in regime_buckets:
        sub = tier_df.filter(pred)
        cohort = sub.filter(cohort_pred)
        other = sub.filter(~cohort_pred)
        n_c, n_o = len(cohort), len(other)
        if n_c < 20:
            print(f'  {label:<28} {n_c:>9} {n_o:>9}  (skipped, n_cohort<20)')
            continue
        wr_c = cohort['result_15'].sum() / n_c * 100
        wr_o = other['result_15'].sum() / n_o * 100 if n_o > 0 else 0
        lift = wr_c - wr_o
        z = z_two_proportions(wr_c, n_c, wr_o, n_o)
        mark = ''
        if abs(z) >= 3.0: mark = ' ***'
        elif abs(z) >= 2.0: mark = ' **'
        elif abs(z) >= 1.5: mark = ' *'
        print(f'  {label:<28} {n_c:>9} {n_o:>9} {wr_c:>10.1f}% {wr_o:>9.1f}% {lift:>+6.2f} {z:>+7.2f}{mark}')


def main():
    df = pl.read_parquet(PATH)
    df = df.filter(pl.col('result_15').is_not_null())
    print(f'[regime] {len(df):,} put-peak rows')

    # --- Define cohorts ---
    cohort_combined = (pl.col('ret_5d') < -0.10) & (pl.col('sigma_expansion') > 1.5)
    cohort_ret10d_drop = (pl.col('ret_10d') < -0.15) & (pl.col('ret_10d') >= -0.25)
    cohort_ret10d_lt15 = (pl.col('ret_10d') < -0.15)  # broader: any ret_10d below -15%

    # --- Define regime buckets ---
    vix_buckets = [
        ('VIX < 18 (calm)',          pl.col('vix_close') < 18),
        ('VIX 18-25 (mid)',          (pl.col('vix_close') >= 18) & (pl.col('vix_close') < 25)),
        ('VIX 25-35 (stress)',       (pl.col('vix_close') >= 25) & (pl.col('vix_close') < 35)),
        ('VIX >= 35 (panic)',        pl.col('vix_close') >= 35),
    ]
    breadth_buckets = [
        ('Breadth < 30 (very weak)', pl.col('breadth_score') < 30),
        ('Breadth 30-50',            (pl.col('breadth_score') >= 30) & (pl.col('breadth_score') < 50)),
        ('Breadth 50-70',            (pl.col('breadth_score') >= 50) & (pl.col('breadth_score') < 70)),
        ('Breadth >= 70 (healthy)',  pl.col('breadth_score') >= 70),
    ]
    comp_buckets = [
        ('Composite < 30 (STRESS)',  pl.col('regime_composite') < 30),
        ('Composite 30-50 (CAUTN)',  (pl.col('regime_composite') >= 30) & (pl.col('regime_composite') < 50)),
        ('Composite 50-70 (NEUT/H)', (pl.col('regime_composite') >= 50) & (pl.col('regime_composite') < 70)),
        ('Composite >= 70 (HEALTHY)',pl.col('regime_composite') >= 70),
    ]
    year_buckets = [
        ('Y2021', pl.col('date').str.starts_with('2021')),
        ('Y2022', pl.col('date').str.starts_with('2022')),
        ('Y2023', pl.col('date').str.starts_with('2023')),
        ('Y2024', pl.col('date').str.starts_with('2024')),
        ('Y2025', pl.col('date').str.starts_with('2025')),
        ('Y2026', pl.col('date').str.starts_with('2026')),
    ]

    # --- Run splits for the COMBINED post-crash cohort ---
    print('\n' + '#' * 116)
    print('# COHORT: COMBINED (ret_5d < -10% AND sigma_expansion > 1.5)')
    print('#' * 116)
    for tier_pred, tier_label in [(pl.col('overall') <= 25, '<=25'),
                                  (pl.col('overall') <= 15, '<=15')]:
        split_table(df, tier_pred, tier_label, cohort_combined, 'COMBINED',
                    vix_buckets, 'VIX')
        split_table(df, tier_pred, tier_label, cohort_combined, 'COMBINED',
                    breadth_buckets, 'Breadth')
        split_table(df, tier_pred, tier_label, cohort_combined, 'COMBINED',
                    comp_buckets, 'Composite')
        split_table(df, tier_pred, tier_label, cohort_combined, 'COMBINED',
                    year_buckets, 'Year')

    # --- Also run split for ret_10d in (-25, -15] which had highest single-axis z ---
    print('\n' + '#' * 116)
    print('# COHORT: ret_10d in [-25%, -15%] (the in-progress-drop zone)')
    print('#' * 116)
    for tier_pred, tier_label in [(pl.col('overall') <= 25, '<=25'),
                                  (pl.col('overall') <= 15, '<=15')]:
        split_table(df, tier_pred, tier_label, cohort_ret10d_drop, 'ret10d in [-25,-15]',
                    vix_buckets, 'VIX')
        split_table(df, tier_pred, tier_label, cohort_ret10d_drop, 'ret10d in [-25,-15]',
                    comp_buckets, 'Composite')
        split_table(df, tier_pred, tier_label, cohort_ret10d_drop, 'ret10d in [-25,-15]',
                    year_buckets, 'Year')


if __name__ == '__main__':
    main()
