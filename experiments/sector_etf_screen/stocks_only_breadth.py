"""Compute a stocks-only breadth time series in cache (research-only, no production change).

Mirrors the production market_breadth._get_daily_breadth aggregation but filters the
universe to Stock.sector IS NOT NULL — i.e., excludes the ~43 ETFs (sector SPDRs,
broad indices, leveraged 3x, commodities, bonds, international, sub-industries) that
the production breadth currently mixes in.

Re-runs the H_INDEP marginal-effect test with the cleaner production-equivalent so we
can verify whether sector breadth's independent signal survives de-contamination of
the production substrate.

Read-only against MySQL. Outputs to .cache/sector_etf_screen/.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
START_DATE = '2020-01-01'

# Cumulative tier groupings
CALL_TIERS = ['70+', '75+', '80+', '85+', '90+']
PUT_TIERS = ['<=25', '<=20', '<=15']


def tier_filter(df, side, tier):
    if side == 'CALL':
        thresh = int(tier.replace('+', ''))
        return df.filter((pl.col('signal_type') == 'CALL') & (pl.col('overall') >= thresh))
    thresh = int(tier.replace('<=', ''))
    return df.filter((pl.col('signal_type') == 'PUT') & (pl.col('overall') <= thresh))


def z_one_prop(p, p_base, n):
    if n == 0 or p_base in (0, 1): return float('nan')
    se = math.sqrt(p_base * (1 - p_base) / n)
    return (p - p_base) / se if se > 0 else float('nan')


def cohort_zscore(df, cohort_col, label):
    out = []
    for side, tiers in [('CALL', CALL_TIERS), ('PUT', PUT_TIERS)]:
        for tier in tiers:
            base = tier_filter(df, side, tier)
            if len(base) < 50: continue
            p_base = base['is_miss'].mean()
            for v in base[cohort_col].drop_nulls().unique().to_list():
                sub = base.filter(pl.col(cohort_col) == v)
                if len(sub) < 50: continue
                p = sub['is_miss'].mean()
                z = z_one_prop(p, p_base, len(sub))
                out.append({
                    'label': label, 'side': side, 'tier': tier,
                    'value': str(v), 'n': len(sub),
                    'tp': round((1-p)*100, 2),
                    'delta_pp': round((1-p-(1-p_base))*100, 2),
                    'z': round(z, 2),
                })
    return out


def compute_stocks_only_breadth():
    """Daily stocks-only breadth = % above EMA50 across Stock.sector IS NOT NULL universe."""
    cache_pq = LOCAL_CACHE / 'stocks_only_breadth_daily.parquet'
    if cache_pq.exists():
        return pl.read_parquet(cache_pq)

    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')

    print(f'[stocks-only] querying daily breadth for sectored stocks since {START_DATE}...', flush=True)

    # For each date: count stocks-only (sector NOT NULL) where close > ema_50
    cur = DB.execute_sql(f"""
        SELECT i.date,
               SUM(CASE WHEN ph.close > i.ema_50 THEN 1 ELSE 0 END) AS above_ema50,
               SUM(CASE WHEN ph.close > i.ema_200 THEN 1 ELSE 0 END) AS above_ema200,
               COUNT(*) AS n_with_ema
        FROM indicators i
        JOIN price_history ph ON ph.symbol = i.symbol AND ph.date = i.date
        JOIN stocks s ON s.symbol = i.symbol
        WHERE i.date >= '{START_DATE}'
          AND i.ema_50 IS NOT NULL
          AND s.sector IS NOT NULL
        GROUP BY i.date
        ORDER BY i.date
    """)
    rows = cur.fetchall()
    print(f'[stocks-only] {len(rows):,} daily rows', flush=True)

    data = {'date': [], 'so_above_ema50': [], 'so_above_ema200': [],
            'so_n': [], 'so_pct_ema50': [], 'so_pct_ema200': []}
    for d, a50, a200, n in rows:
        data['date'].append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        data['so_above_ema50'].append(int(a50))
        data['so_above_ema200'].append(int(a200))
        data['so_n'].append(int(n))
        data['so_pct_ema50'].append(round(a50/n*100, 2) if n > 0 else None)
        data['so_pct_ema200'].append(round(a200/n*100, 2) if n > 0 else None)

    df = pl.DataFrame(data)
    df.write_parquet(cache_pq)
    return df


def main():
    so = compute_stocks_only_breadth()
    print(f'\n[stocks-only breadth] N={len(so)}, range {so["date"].min()} → {so["date"].max()}')
    print(f'  so_pct_ema50: mean={so["so_pct_ema50"].mean():.1f}  std={so["so_pct_ema50"].std():.1f}  '
          f'p10={so["so_pct_ema50"].quantile(0.1):.1f}  p90={so["so_pct_ema50"].quantile(0.9):.1f}')
    print(f'  universe size median: {so["so_n"].median():.0f} stocks')

    # Load sector breadth + production breadth from existing cache
    sec_pq = LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv'
    sec = pl.read_csv(sec_pq)

    cohort_pq = LOCAL_CACHE / 'cohort_v44_1825.parquet'
    cohort = pl.read_parquet(cohort_pq)
    cohort = pre_cutoff_filter(cohort)

    # Cast `date` column types so joins work (csv reads as str)
    so = so.with_columns(pl.col('date').cast(pl.Utf8))
    sec = sec.with_columns(pl.col('date').cast(pl.Utf8))

    # Join the three breadth signals on date for correlation analysis
    daily = so.join(sec, on='date', how='inner')
    # Production breadth from cohort (it's per-row, so dedupe per date)
    prod_daily = cohort.select(['date', 'breadth_score']).unique('date')
    daily = daily.join(prod_daily.with_columns(pl.col('date').cast(pl.Utf8)), on='date', how='left')

    daily = daily.filter(
        pl.col('breadth_score').is_not_null() &
        pl.col('so_pct_ema50').is_not_null() &
        pl.col('sec_brd_ema50').is_not_null()
    )
    print(f'\n[joined daily breadth signals] N={len(daily)} days with all 3 measures')

    # Correlations
    import numpy as np
    bs = daily['breadth_score'].cast(pl.Float64).to_numpy().astype('float64')
    so_e50 = daily['so_pct_ema50'].cast(pl.Float64).to_numpy().astype('float64')
    sec_e50 = daily['sec_brd_ema50'].cast(pl.Float64).to_numpy().astype('float64')

    rho_prod_so = float(np.corrcoef(bs, so_e50)[0, 1])
    rho_prod_sec = float(np.corrcoef(bs, sec_e50)[0, 1])
    rho_so_sec = float(np.corrcoef(so_e50, sec_e50)[0, 1])

    print(f'\n  Pearson correlations (daily, signal-date level):')
    print(f'    production_breadth   ↔ stocks_only_pct_ema50:  {rho_prod_so:+.4f}')
    print(f'    production_breadth   ↔ sector_breadth_ema50:   {rho_prod_sec:+.4f}')
    print(f'    stocks_only_pct_ema50 ↔ sector_breadth_ema50:  {rho_so_sec:+.4f}  ← cleaner test of orthogonality')

    # The independence test re-run with stocks-only as the production-equivalent
    print('\n' + '='*70)
    print('Marginal effect — sector breadth | STOCKS-ONLY breadth (de-contaminated)')
    print('='*70)

    # Re-attach to cohort
    df = cohort.join(so, on='date', how='left').join(sec, on='date', how='left')

    # Bucket each signal
    p10_so = daily['so_pct_ema50'].quantile(0.10)
    p25_so = daily['so_pct_ema50'].quantile(0.25)
    p75_so = daily['so_pct_ema50'].quantile(0.75)
    p90_so = daily['so_pct_ema50'].quantile(0.90)

    p10_sec = daily['sec_brd_ema50'].quantile(0.10)
    p25_sec = daily['sec_brd_ema50'].quantile(0.25)
    p75_sec = daily['sec_brd_ema50'].quantile(0.75)
    p90_sec = daily['sec_brd_ema50'].quantile(0.90)

    print(f'\n  stocks_only deciles: p10={p10_so:.1f} p25={p25_so:.1f} p75={p75_so:.1f} p90={p90_so:.1f}')
    print(f'  sector_brd  deciles: p10={p10_sec:.1f} p25={p25_sec:.1f} p75={p75_sec:.1f} p90={p90_sec:.1f}')

    df = df.with_columns(
        pl.when(pl.col('so_pct_ema50').is_null()).then(pl.lit(None))
          .when(pl.col('so_pct_ema50') <= p25_so).then(pl.lit('so_lo'))
          .when(pl.col('so_pct_ema50') <= p75_so).then(pl.lit('so_mid'))
          .otherwise(pl.lit('so_hi')).alias('so_bucket'),
        pl.when(pl.col('sec_brd_ema50').is_null()).then(pl.lit(None))
          .when(pl.col('sec_brd_ema50') <= p10_sec).then(pl.lit('a_p10_extlow'))
          .when(pl.col('sec_brd_ema50') <= p25_sec).then(pl.lit('b_p25_low'))
          .when(pl.col('sec_brd_ema50') <= p75_sec).then(pl.lit('c_mid'))
          .when(pl.col('sec_brd_ema50') <= p90_sec).then(pl.lit('d_p75_high'))
          .otherwise(pl.lit('e_p90_exthigh')).alias('sec_brd_bucket')
    )

    # Conditional-on-stocks-only-MID: does sector breadth still predict?
    print('\n  Conditional cohort z-tests, sec_brd | stocks_only_breadth:')
    for so_bucket in ['so_lo', 'so_mid', 'so_hi']:
        sub = df.filter(pl.col('so_bucket') == so_bucket)
        if len(sub) < 500:
            print(f'\n  {so_bucket}: insufficient N={len(sub)}')
            continue
        rows = cohort_zscore(sub, 'sec_brd_bucket', f'sec|{so_bucket}')
        sig = [r for r in rows if abs(r['z']) >= 1.96]
        if sig:
            print(f'\n  {so_bucket} (N_total={len(sub):,}): significant cells')
            print(f'    {"side":4s} {"tier":5s} {"sec_brd":15s} {"N":>5s} {"Δpp":>6s} {"z":>6s}')
            for r in sorted(sig, key=lambda r: -abs(r['z']))[:8]:
                print(f'    {r["side"]:4s} {r["tier"]:5s} {r["value"]:15s} {r["n"]:>5d} {r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')
        else:
            print(f'\n  {so_bucket} (N_total={len(sub):,}): no significant cells (sector signal absorbed by stocks-only breadth)')

    # Inverse: SO breadth marginal | sec breadth bucket
    print('\n  Inverse — stocks_only marginal | sec_brd bucket:')
    for sec_bucket in ['a_p10_extlow', 'c_mid', 'e_p90_exthigh']:
        sub = df.filter(pl.col('sec_brd_bucket') == sec_bucket)
        if len(sub) < 500:
            print(f'\n  sec={sec_bucket}: N={len(sub)} (skipped)')
            continue
        rows = cohort_zscore(sub, 'so_bucket', f'so|{sec_bucket}')
        sig = [r for r in rows if abs(r['z']) >= 1.96]
        if sig:
            print(f'\n  sec={sec_bucket} (N_total={len(sub):,}): significant cells')
            for r in sorted(sig, key=lambda r: -abs(r['z']))[:5]:
                print(f'    {r["side"]:4s} {r["tier"]:5s} {r["value"]:15s} {r["n"]:>5d} {r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')

    print('\n[done]')


if __name__ == '__main__':
    main()
