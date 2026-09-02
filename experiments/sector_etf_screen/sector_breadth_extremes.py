"""Sector ETF Breadth — extremes detection + scoring performance + marginal-effect-vs-production-breadth decomposition.

Phase A — Build daily sector breadth time series (2020-01-01 → today)
         using % of 11 SPDR sector ETFs above their EMA50.
Phase B — Identify breadth extremes via decile bins.
Phase C — Join v44 peaks to sector breadth bucket, run cohort z-tests.
Phase D — 2D decomposition: (production_breadth × sector_breadth) cross-tab to
         test whether sector breadth has marginal predictive power
         INDEPENDENT of production breadth_score.
Phase E — Daily time series export for visualization.

Read-only against production DB. Uses v44 cohort + ETF wide-format features
already cached. Holdout-locked.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
COHORT_PQ = LOCAL_CACHE / 'cohort_v44_1825.parquet'
ETF_WIDE_PQ = LOCAL_CACHE / 'etf_features_wide.parquet'

# 11 GICS SPDR sector ETFs (full coverage post-2026-05-07 backfill)
SECTOR_ETFS = ['XLK', 'XLV', 'XLY', 'XLI', 'XLB', 'XLRE',
               'XLF', 'XLE', 'XLP', 'XLU', 'XLC']

START_DATE = '2020-01-01'

# Cumulative tier groupings
CALL_TIERS = ['70+', '75+', '80+', '85+', '90+']
PUT_TIERS = ['<=25', '<=20', '<=15']


def tier_filter(df: pl.DataFrame, side: str, tier: str) -> pl.DataFrame:
    if side == 'CALL':
        thresh = int(tier.replace('+', ''))
        return df.filter((pl.col('signal_type') == 'CALL') & (pl.col('overall') >= thresh))
    else:
        thresh = int(tier.replace('<=', ''))
        return df.filter((pl.col('signal_type') == 'PUT') & (pl.col('overall') <= thresh))


def z_one_prop(p_cohort: float, p_base: float, n_cohort: int) -> float:
    if n_cohort == 0 or p_base == 0 or p_base == 1:
        return float('nan')
    se = math.sqrt(p_base * (1 - p_base) / n_cohort)
    if se == 0: return float('nan')
    return (p_cohort - p_base) / se


def cohort_zscore(df: pl.DataFrame, cohort_col: str, label: str) -> list[dict]:
    results = []
    for side, tiers in [('CALL', CALL_TIERS), ('PUT', PUT_TIERS)]:
        for tier in tiers:
            base = tier_filter(df, side, tier)
            if len(base) < 50: continue
            base_n = len(base)
            base_p = base['is_miss'].mean()
            cohort_vals = base[cohort_col].drop_nulls().unique().to_list()
            for v in cohort_vals:
                sub = base.filter(pl.col(cohort_col) == v)
                n = len(sub)
                if n < 50: continue
                p = sub['is_miss'].mean()
                z = z_one_prop(p, base_p, n)
                results.append({
                    'cohort_label': label, 'side': side, 'tier': tier,
                    'cohort_value': str(v), 'n': n,
                    'tp': round((1-p)*100, 2),
                    'tp_base': round((1-base_p)*100, 2),
                    'delta_pp': round((1-p-(1-base_p))*100, 2),
                    'z': round(z, 2),
                })
    return results


def phase_a_build_breadth_ts() -> pl.DataFrame:
    """Daily sector ETF breadth = % of 11 SPDRs above their own EMA50."""
    print('=' * 70)
    print('Phase A — Build daily sector ETF breadth time series')
    print('=' * 70)

    if not ETF_WIDE_PQ.exists():
        raise RuntimeError(f'Missing {ETF_WIDE_PQ} — run build_features.py')
    wide = pl.read_parquet(ETF_WIDE_PQ)
    wide = wide.filter(pl.col('date') >= START_DATE)

    # Build sector breadth: count of (XLx_pct_ema50 > 0) / count(non-null) per row
    avail_cols = [f'{e}_pct_ema50' for e in SECTOR_ETFS if f'{e}_pct_ema50' in wide.columns]
    print(f'  using {len(avail_cols)} sector ETFs: {[c.split("_")[0] for c in avail_cols]}')

    expr_pos = pl.lit(0.0)
    expr_known = pl.lit(0.0)
    for c in avail_cols:
        expr_pos = expr_pos + pl.when(pl.col(c) > 0).then(1.0).otherwise(0.0)
        expr_known = expr_known + pl.when(pl.col(c).is_not_null()).then(1.0).otherwise(0.0)

    # Also: % above EMA200 (slower-trend version)
    avail_200 = [f'{e}_pct_ema200' for e in SECTOR_ETFS if f'{e}_pct_ema200' in wide.columns]
    expr_pos_200 = pl.lit(0.0)
    expr_known_200 = pl.lit(0.0)
    for c in avail_200:
        expr_pos_200 = expr_pos_200 + pl.when(pl.col(c) > 0).then(1.0).otherwise(0.0)
        expr_known_200 = expr_known_200 + pl.when(pl.col(c).is_not_null()).then(1.0).otherwise(0.0)

    # Mean RSI across sectors
    avail_rsi = [f'{e}_rsi' for e in SECTOR_ETFS if f'{e}_rsi' in wide.columns]
    expr_rsi_sum = pl.lit(0.0)
    expr_rsi_n = pl.lit(0.0)
    for c in avail_rsi:
        expr_rsi_sum = expr_rsi_sum + pl.when(pl.col(c).is_not_null()).then(pl.col(c)).otherwise(0.0)
        expr_rsi_n = expr_rsi_n + pl.when(pl.col(c).is_not_null()).then(1.0).otherwise(0.0)

    breadth = wide.with_columns([
        (expr_pos / expr_known * 100).alias('sec_brd_ema50'),       # % sectors above EMA50
        (expr_pos_200 / expr_known_200 * 100).alias('sec_brd_ema200'),  # % above EMA200
        (expr_rsi_sum / expr_rsi_n).alias('sec_avg_rsi'),           # mean sector RSI
    ]).select(['date', 'sec_brd_ema50', 'sec_brd_ema200', 'sec_avg_rsi'])
    breadth = breadth.sort('date')

    n = len(breadth)
    dmin, dmax = breadth['date'].min(), breadth['date'].max()
    print(f'  {n:,} daily rows from {dmin} to {dmax}')

    e50 = breadth['sec_brd_ema50']
    print(f'  sec_brd_ema50:  mean={e50.mean():.1f}  std={e50.std():.1f}  '
          f'p10={e50.quantile(0.10):.1f}  p50={e50.quantile(0.50):.1f}  p90={e50.quantile(0.90):.1f}')
    e200 = breadth['sec_brd_ema200']
    print(f'  sec_brd_ema200: mean={e200.mean():.1f}  std={e200.std():.1f}  '
          f'p10={e200.quantile(0.10):.1f}  p50={e200.quantile(0.50):.1f}  p90={e200.quantile(0.90):.1f}')
    rsi = breadth['sec_avg_rsi']
    print(f'  sec_avg_rsi:    mean={rsi.mean():.1f}  std={rsi.std():.1f}  '
          f'p10={rsi.quantile(0.10):.1f}  p50={rsi.quantile(0.50):.1f}  p90={rsi.quantile(0.90):.1f}')

    breadth.write_csv(LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv')
    return breadth


def phase_b_extremes(breadth: pl.DataFrame):
    """Identify extreme periods for sector breadth and characterize them."""
    print()
    print('=' * 70)
    print('Phase B — Sector breadth extremes')
    print('=' * 70)

    # Decile bins on sec_brd_ema50
    p10 = breadth['sec_brd_ema50'].quantile(0.10)
    p25 = breadth['sec_brd_ema50'].quantile(0.25)
    p75 = breadth['sec_brd_ema50'].quantile(0.75)
    p90 = breadth['sec_brd_ema50'].quantile(0.90)

    print(f'  decile cuts on sec_brd_ema50: p10={p10:.0f}  p25={p25:.0f}  p75={p75:.0f}  p90={p90:.0f}')

    # Identify contiguous extreme periods (>=5 consecutive days)
    breadth_lo = breadth.filter(pl.col('sec_brd_ema50') <= p10)
    breadth_hi = breadth.filter(pl.col('sec_brd_ema50') >= p90)
    print(f'\n  total days at p10 (deeply weak):   {len(breadth_lo):,}')
    print(f'  total days at p90 (deeply strong): {len(breadth_hi):,}')

    # Group consecutive-day runs
    print('\n  Sample weak periods (sec_brd_ema50 <= p10, run length >=5 days):')
    breadth_with_lag = breadth.with_columns(
        (pl.col('sec_brd_ema50') <= p10).cast(pl.Int32).alias('is_lo')
    ).with_columns(
        (pl.col('is_lo') - pl.col('is_lo').shift(1).fill_null(0)).alias('chg')
    )
    runs = []
    current_start = None
    for row in breadth_with_lag.iter_rows(named=True):
        if row['is_lo'] == 1 and current_start is None:
            current_start = row['date']
        elif row['is_lo'] == 0 and current_start is not None:
            runs.append((current_start, row['date']))
            current_start = None
    if current_start is not None:
        runs.append((current_start, breadth_with_lag['date'][-1]))

    # Filter runs of >= 5 days
    long_runs_lo = []
    for start, end in runs:
        # Rough day count — use date diff
        from datetime import date as _date
        s = _date.fromisoformat(start) if isinstance(start, str) else start
        e = _date.fromisoformat(end) if isinstance(end, str) else end
        days = (e - s).days
        if days >= 5:
            long_runs_lo.append((start, end, days))

    print(f'  runs >=5 days at p10: {len(long_runs_lo)}')
    for s, e, d in long_runs_lo[:15]:
        print(f'    {s} -> {e} ({d} days)')

    return p10, p25, p75, p90


def phase_c_cohort(cohort: pl.DataFrame, breadth: pl.DataFrame,
                   p10, p25, p75, p90):
    """Join sector breadth at signal date, cross-tab signal performance."""
    print()
    print('=' * 70)
    print('Phase C — Signal performance during sector breadth extremes')
    print('=' * 70)

    df = cohort.join(breadth, on='date', how='left')
    n_with = df.filter(pl.col('sec_brd_ema50').is_not_null()).height
    print(f'  {n_with:,} signals joined to sector breadth')

    # Bucket
    df = df.with_columns(
        pl.when(pl.col('sec_brd_ema50').is_null()).then(pl.lit(None))
          .when(pl.col('sec_brd_ema50') <= p10).then(pl.lit('a_p10_extlow'))
          .when(pl.col('sec_brd_ema50') <= p25).then(pl.lit('b_p25_low'))
          .when(pl.col('sec_brd_ema50') <= p75).then(pl.lit('c_mid'))
          .when(pl.col('sec_brd_ema50') <= p90).then(pl.lit('d_p75_high'))
          .otherwise(pl.lit('e_p90_exthigh')).alias('sec_brd_bucket')
    )

    rows = cohort_zscore(df, 'sec_brd_bucket', 'sec_brd_ema50')
    print('\n  Cohort z-test by sector breadth bucket:')
    print(f'  {"side":4s} {"tier":5s} {"bucket":15s} {"N":>5s} {"TP%":>6s} '
          f'{"base":>6s} {"Δpp":>6s} {"z":>6s}')
    for r in sorted([r for r in rows if abs(r['z']) >= 1.5],
                    key=lambda r: -abs(r['z']))[:25]:
        print(f'  {r["side"]:4s} {r["tier"]:5s} {r["cohort_value"]:15s} '
              f'{r["n"]:>5d} {r["tp"]:>6.2f} {r["tp_base"]:>6.2f} '
              f'{r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')

    pl.DataFrame(rows).write_csv(LOCAL_CACHE / 'phaseC_sec_brd_cohorts.csv')
    return df


def phase_d_marginal_effect(df: pl.DataFrame):
    """2D analysis: does sector breadth have marginal effect AFTER conditioning on production breadth?

    Method: for each production_breadth bucket, recompute the sector_breadth cohort z-test.
    If the sector signal is purely a proxy for production breadth, the marginal-conditional
    z-scores should attenuate to noise. If sector has independent info, z-scores survive.
    """
    print()
    print('=' * 70)
    print('Phase D — Marginal effect: sector breadth | production breadth')
    print('=' * 70)

    if 'breadth_score' not in df.columns:
        print('  breadth_score not in cohort — skipping')
        return

    # Production breadth buckets (4-quantile)
    p25_prod = df['breadth_score'].quantile(0.25)
    p75_prod = df['breadth_score'].quantile(0.75)
    print(f'  production breadth quartile cuts: p25={p25_prod:.1f}  p75={p75_prod:.1f}')

    df = df.with_columns(
        pl.when(pl.col('breadth_score').is_null()).then(pl.lit(None))
          .when(pl.col('breadth_score') <= p25_prod).then(pl.lit('prod_lo'))
          .when(pl.col('breadth_score') <= p75_prod).then(pl.lit('prod_mid'))
          .otherwise(pl.lit('prod_hi')).alias('prod_brd_bucket')
    )

    # Pearson correlation between the two breadths at signal date
    import numpy as np
    sub = df.filter(pl.col('breadth_score').is_not_null() & pl.col('sec_brd_ema50').is_not_null())
    bs = sub['breadth_score'].to_numpy()
    sb = sub['sec_brd_ema50'].to_numpy()
    rho = float(np.corrcoef(bs, sb)[0, 1])
    print(f'  signal-level Pearson(production_breadth, sec_brd_ema50): {rho:.4f}')
    print(f'    → ~{int(rho**2*100)}% shared variance, ~{int((1-rho**2)*100)}% unique')

    # For each prod_brd bucket, run sector breadth cohort z-tests
    print('\n  Marginal sector breadth effect, conditional on production breadth bucket:')
    for prod_bucket in ['prod_lo', 'prod_mid', 'prod_hi']:
        sub_df = df.filter(pl.col('prod_brd_bucket') == prod_bucket)
        if len(sub_df) < 500:
            print(f'\n  {prod_bucket}: insufficient N={len(sub_df)}')
            continue
        rows = cohort_zscore(sub_df, 'sec_brd_bucket', f'sec_brd|{prod_bucket}')
        sig = [r for r in rows if abs(r['z']) >= 1.96]
        if sig:
            print(f'\n  {prod_bucket} (N_total={len(sub_df):,}): significant cells')
            print(f'    {"side":4s} {"tier":5s} {"sec_brd":15s} {"N":>5s} {"Δpp":>6s} {"z":>6s}')
            for r in sorted(sig, key=lambda r: -abs(r['z']))[:8]:
                print(f'    {r["side"]:4s} {r["tier"]:5s} {r["cohort_value"]:15s} '
                      f'{r["n"]:>5d} {r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')
        else:
            print(f'\n  {prod_bucket} (N_total={len(sub_df):,}): no significant cells (sector signal absorbed by production)')

    # Inverse: does production breadth still predict when sector breadth is fixed?
    print('\n  Inverse — production breadth marginal | sector breadth bucket:')
    for sec_bucket in ['a_p10_extlow', 'c_mid', 'e_p90_exthigh']:
        sub_df = df.filter(pl.col('sec_brd_bucket') == sec_bucket)
        if len(sub_df) < 500:
            print(f'\n  sec={sec_bucket}: N={len(sub_df)} (skipped)')
            continue
        rows = cohort_zscore(sub_df, 'prod_brd_bucket', f'prod_brd|{sec_bucket}')
        sig = [r for r in rows if abs(r['z']) >= 1.96]
        if sig:
            print(f'\n  sec={sec_bucket} (N_total={len(sub_df):,}): significant cells')
            for r in sorted(sig, key=lambda r: -abs(r['z']))[:5]:
                print(f'    {r["side"]:4s} {r["tier"]:5s} {r["cohort_value"]:15s} '
                      f'{r["n"]:>5d} {r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')


def phase_e_export(breadth: pl.DataFrame):
    """Export daily breadth time series for visualization."""
    print()
    print('=' * 70)
    print('Phase E — Daily time series export')
    print('=' * 70)
    out = LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv'
    print(f'  written: {out} ({len(breadth):,} daily rows from {breadth["date"].min()})')


def main():
    if not COHORT_PQ.exists():
        raise RuntimeError(f'Missing v44 cohort: {COHORT_PQ} — run build_v44_cohort.py')

    cohort = pl.read_parquet(COHORT_PQ)
    cohort = pre_cutoff_filter(cohort)
    print(f'[load] v44 cohort: {len(cohort):,} rows, {cohort["date"].min()} -> {cohort["date"].max()}')
    assert_no_holdout_leak(cohort, 'sector_breadth_extremes')

    breadth = phase_a_build_breadth_ts()
    p10, p25, p75, p90 = phase_b_extremes(breadth)
    df = phase_c_cohort(cohort, breadth, p10, p25, p75, p90)
    phase_d_marginal_effect(df)
    phase_e_export(breadth)

    print('\n[done]')


if __name__ == '__main__':
    main()
