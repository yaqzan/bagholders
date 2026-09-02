"""Per-sector regime composite pre-test — mirror production MarketRegime architecture
applied per-sector and measure whether the multi-factor composite gives stronger cohort
z-scores than single-factor sector ETF state.

Components per (sector × date):
  - Sector breadth (35%): % of stocks in this sector with close > ema_50
  - Sector vol     (35%): sector ETF 60d realized vol, percentile-ranked over its history,
                          inverted (low vol = high regime score)
  - Sector trend   (30%): sector ETF vs EMA50 (50%) + sector ETF vs EMA200 (50%)

Composite ∈ [0, 100]. Buckets analogous to production:
  ≤30 STRESS, 30-45 CAUTION, 45-60 NEUTRAL, 60-75 HEALTHY, ≥75 BULL

Decision criterion:
  - Sector regime z > 5.5  → use composite (worth complexity)
  - Sector regime z 4-5.5  → similar to single-factor; stick simple
  - Sector regime z < 4    → composite over-smooths; stick simple

Read-only against MySQL. Output: cohort tables + decision recommendation.
"""
from __future__ import annotations
import io, sys, math, time
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

SECTOR_ETF_MAP = {
    'technology':            'XLK',
    'healthcare':            'XLV',
    'consumer-cyclical':     'XLY',
    'industrials':           'XLI',
    'basic-materials':       'XLB',
    'real-estate':           'XLRE',
    'financial-services':    'XLF',
    'energy':                'XLE',
    'consumer-defensive':    'XLP',
    'utilities':             'XLU',
    'communication-services':'XLC',
}

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


def cohort_zscore(df, cohort_col, label, min_n=50):
    out = []
    for side, tiers in [('CALL', CALL_TIERS), ('PUT', PUT_TIERS)]:
        for tier in tiers:
            base = tier_filter(df, side, tier)
            if len(base) < min_n: continue
            p_base = base['is_miss'].mean()
            for v in base[cohort_col].drop_nulls().unique().to_list():
                sub = base.filter(pl.col(cohort_col) == v)
                if len(sub) < min_n: continue
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


def build_per_sector_breadth():
    """% of stocks in each sector with close > ema_50, daily 2020+."""
    cache_pq = LOCAL_CACHE / 'per_sector_breadth.parquet'
    if cache_pq.exists():
        return pl.read_parquet(cache_pq)

    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')
    print(f'[sec_brd] querying per-sector breadth since {START_DATE}...', flush=True)

    cur = DB.execute_sql(f"""
        SELECT i.date, s.sector,
               SUM(CASE WHEN ph.close > i.ema_50 THEN 1 ELSE 0 END) AS above_ema50,
               COUNT(*) AS n_with_ema
        FROM indicators i
        JOIN price_history ph ON ph.symbol = i.symbol AND ph.date = i.date
        JOIN stocks s ON s.symbol = i.symbol
        WHERE i.date >= '{START_DATE}'
          AND i.ema_50 IS NOT NULL
          AND s.sector IS NOT NULL
        GROUP BY i.date, s.sector
        ORDER BY i.date, s.sector
    """)
    rows = cur.fetchall()
    print(f'[sec_brd] {len(rows):,} (date × sector) rows', flush=True)

    data = {'date': [], 'sector': [], 'sec_brd_pct_ema50': [], 'sec_brd_n': []}
    for d, sec, a, n in rows:
        data['date'].append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        data['sector'].append(sec)
        data['sec_brd_pct_ema50'].append(round(a/n*100, 2) if n > 0 else None)
        data['sec_brd_n'].append(int(n))

    df = pl.DataFrame(data)
    df.write_parquet(cache_pq)
    return df


def build_per_sector_vol_and_trend():
    """For each sector ETF: 60d realized vol + EMA50/EMA200 position daily."""
    cache_pq = LOCAL_CACHE / 'per_sector_vol_trend.parquet'
    if cache_pq.exists():
        return pl.read_parquet(cache_pq)

    from database.trader_database import DB
    print(f'[sec_vol] computing 60d realized vol + trend per sector ETF since {START_DATE}...', flush=True)

    etfs = list(SECTOR_ETF_MAP.values())
    syms_sql = "', '".join(etfs)
    cur = DB.execute_sql(f"""
        SELECT ph.symbol, ph.date, ph.close, i.ema_50, i.ema_200
        FROM price_history ph
        JOIN indicators i ON i.symbol = ph.symbol AND i.date = ph.date
        WHERE ph.symbol IN ('{syms_sql}')
          AND ph.date >= '2018-01-01'  -- need lead time for 60d window
        ORDER BY ph.symbol, ph.date
    """)
    rows = cur.fetchall()

    data = {'symbol': [], 'date': [], 'close': [], 'ema_50': [], 'ema_200': []}
    for sym, d, c, e50, e200 in rows:
        data['symbol'].append(sym)
        data['date'].append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        data['close'].append(float(c) if c else None)
        data['ema_50'].append(float(e50) if e50 else None)
        data['ema_200'].append(float(e200) if e200 else None)

    df = pl.DataFrame(data).sort(['symbol', 'date'])

    # Daily log return
    df = df.with_columns(
        (pl.col('close') / pl.col('close').shift(1).over('symbol')).log().alias('logret')
    )
    # 60d rolling stdev × sqrt(252) (annualized) per ETF
    df = df.with_columns(
        (pl.col('logret').rolling_std(window_size=60).over('symbol') * (252 ** 0.5)
         * 100).alias('vol_60d_ann_pct')
    )
    # Trend: % above EMA50 and % above EMA200
    df = df.with_columns([
        ((pl.col('close') - pl.col('ema_50')) / pl.col('ema_50') * 100).alias('pct_ema50'),
        ((pl.col('close') - pl.col('ema_200')) / pl.col('ema_200') * 100).alias('pct_ema200'),
    ])
    # Filter to start_date for output
    df = df.filter(pl.col('date') >= START_DATE)

    # Vol percentile rank within each ETF's history (using full available history per ETF)
    # Assign a rank ∈ [0, 100] per row over its symbol's full vol distribution
    def add_pct_rank(group: pl.DataFrame) -> pl.DataFrame:
        n = len(group)
        if n == 0:
            return group
        v = group['vol_60d_ann_pct'].to_numpy()
        import numpy as np
        order = np.argsort(np.argsort(v))
        # rank: 0 to n-1; convert to percentile 0..100
        rank_pct = (order / max(1, n-1)) * 100
        return group.with_columns(pl.Series('vol_pct_rank', rank_pct))

    df = df.group_by('symbol', maintain_order=True).map_groups(add_pct_rank)

    df = df.select(['symbol', 'date', 'close', 'pct_ema50', 'pct_ema200',
                    'vol_60d_ann_pct', 'vol_pct_rank'])
    df.write_parquet(cache_pq)
    return df


def build_sector_regime(brd: pl.DataFrame, vt: pl.DataFrame) -> pl.DataFrame:
    """Combine per-sector breadth + per-sector ETF vol/trend into composite ∈ [0, 100]
    mirroring production regime weighting:
      0.35 × breadth + 0.35 × (100 - vol_pct_rank) + 0.30 × trend
    """
    cache_pq = LOCAL_CACHE / 'sector_regime_daily.parquet'

    # Rename vt.symbol → sector_etf and add the sector via reverse map BEFORE joining
    etf_to_sector = {v: k for k, v in SECTOR_ETF_MAP.items()}
    vt = vt.with_columns(pl.col('date').cast(pl.Utf8))
    vt = vt.with_columns(
        pl.col('symbol').map_elements(lambda s: etf_to_sector.get(s, None),
                                      return_dtype=pl.Utf8).alias('sector')
    ).rename({'symbol': 'sector_etf'})

    # brd has columns: date, sector, sec_brd_pct_ema50, sec_brd_n
    brd = brd.with_columns(pl.col('date').cast(pl.Utf8))

    # Join on (sector, date) — clean key, no symbol collision
    df = brd.join(vt, on=['sector', 'date'], how='inner')

    # Build trend score: average of pct_ema50 and pct_ema200, scaled
    # Production trend score: SPY > EMA50 contributes 0..100 as a sigmoid-like map.
    # Here use clipped linear: pct_ema50 ∈ [-10, +10] → score ∈ [0, 100]
    df = df.with_columns([
        ((pl.col('pct_ema50').clip(-10, 10) + 10) / 20 * 100).alias('trend_e50_score'),
        ((pl.col('pct_ema200').clip(-10, 10) + 10) / 20 * 100).alias('trend_e200_score'),
    ])
    df = df.with_columns(
        ((pl.col('trend_e50_score') + pl.col('trend_e200_score')) / 2).alias('trend_score')
    )

    # Vol score: invert percentile rank (low vol = high score)
    df = df.with_columns(
        (100 - pl.col('vol_pct_rank')).alias('vol_score')
    )

    # Composite (matches production weights)
    df = df.with_columns(
        (0.35 * pl.col('sec_brd_pct_ema50')
       + 0.35 * pl.col('vol_score')
       + 0.30 * pl.col('trend_score')).alias('sec_regime_composite')
    )

    df = df.with_columns(
        pl.when(pl.col('sec_regime_composite') <= 30).then(pl.lit('a_STRESS'))
          .when(pl.col('sec_regime_composite') <= 45).then(pl.lit('b_CAUTION'))
          .when(pl.col('sec_regime_composite') <= 60).then(pl.lit('c_NEUTRAL'))
          .when(pl.col('sec_regime_composite') <= 75).then(pl.lit('d_HEALTHY'))
          .otherwise(pl.lit('e_BULL')).alias('sec_regime_label'),
        pl.when(pl.col('sec_regime_composite') <= 20).then(pl.lit('q1_lo'))
          .when(pl.col('sec_regime_composite') <= 40).then(pl.lit('q2'))
          .when(pl.col('sec_regime_composite') <= 60).then(pl.lit('q3_mid'))
          .when(pl.col('sec_regime_composite') <= 80).then(pl.lit('q4'))
          .otherwise(pl.lit('q5_hi')).alias('sec_regime_quintile')
    )

    df.write_parquet(cache_pq)
    return df


def main():
    t0 = time.time()
    print('=' * 72)
    print('Per-sector regime composite pre-test')
    print('=' * 72)

    print(f'\n[1/5] Building per-sector breadth ({time.time()-t0:.0f}s)...')
    brd = build_per_sector_breadth()
    n_sectors = brd['sector'].n_unique()
    n_dates = brd['date'].n_unique()
    print(f'  {n_sectors} sectors × {n_dates} dates = {len(brd):,} rows')

    print(f'\n[2/5] Building per-sector vol+trend ({time.time()-t0:.0f}s)...')
    vt = build_per_sector_vol_and_trend()
    print(f'  {vt["symbol"].n_unique()} ETFs × {vt["date"].n_unique()} dates = {len(vt):,} rows')

    print(f'\n[3/5] Building sector regime composite ({time.time()-t0:.0f}s)...')
    sr = build_sector_regime(brd, vt)
    print(f'  {len(sr):,} (sector × date) regime rows')
    print(f'  composite stats: mean={sr["sec_regime_composite"].mean():.1f}  '
          f'std={sr["sec_regime_composite"].std():.1f}  '
          f'p10={sr["sec_regime_composite"].quantile(0.1):.1f}  '
          f'p90={sr["sec_regime_composite"].quantile(0.9):.1f}')
    print(f'  regime label distribution:')
    label_counts = sr.group_by('sec_regime_label').len().sort('sec_regime_label')
    for r in label_counts.iter_rows(named=True):
        print(f'    {r["sec_regime_label"]}: {r["len"]:,}')

    print(f'\n[4/5] Joining to v44 cohort ({time.time()-t0:.0f}s)...')
    cohort_pq = LOCAL_CACHE / 'cohort_v44_1825.parquet'
    cohort = pl.read_parquet(cohort_pq)
    cohort = pre_cutoff_filter(cohort)
    cohort = cohort.with_columns(pl.col('date').cast(pl.Utf8))

    # Cohort has stock.symbol + sector. Join sector regime on (sector, date)
    sr_keys = sr.select(['date', 'sector', 'sec_regime_composite',
                         'sec_regime_label', 'sec_regime_quintile',
                         'sec_brd_pct_ema50', 'vol_score', 'trend_score',
                         'pct_ema50', 'vol_pct_rank']).rename({
        'pct_ema50': 'sec_etf_pct_ema50',
    })
    df = cohort.join(sr_keys, on=['date', 'sector'], how='left')

    # Also bring in ETF wide for sec_etf_rsi (per stock's sector)
    etf_wide_pq = LOCAL_CACHE / 'etf_features_wide.parquet'
    etf_wide = pl.read_parquet(etf_wide_pq).with_columns(pl.col('date').cast(pl.Utf8))
    df = df.join(etf_wide, on='date', how='left')

    # Build sec_etf_rsi based on stock's sector
    expr_rsi = pl.lit(None, dtype=pl.Float64)
    for sec, etf in SECTOR_ETF_MAP.items():
        if f'{etf}_rsi' in df.columns:
            expr_rsi = pl.when(pl.col('sector') == sec).then(pl.col(f'{etf}_rsi')).otherwise(expr_rsi)
    df = df.with_columns(expr_rsi.alias('sec_etf_rsi'))

    n_with_regime = df.filter(pl.col('sec_regime_composite').is_not_null()).height
    print(f'  joined: {n_with_regime:,}/{len(df):,} signals have sector regime')

    print(f'\n[5/5] Cohort z-tests ({time.time()-t0:.0f}s)...')

    # 1) Sector regime label
    print('\n  --- Sector regime LABEL (5 buckets) ---')
    rows = cohort_zscore(df, 'sec_regime_label', 'sec_regime_label')
    sig = sorted([r for r in rows if abs(r['z']) >= 1.96], key=lambda r: -abs(r['z']))
    print(f'  {"side":4s} {"tier":5s} {"label":11s} {"N":>5s} {"TP%":>6s} '
          f'{"Δpp":>6s} {"z":>6s}')
    for r in sig[:20]:
        print(f'  {r["side"]:4s} {r["tier"]:5s} {r["value"]:11s} '
              f'{r["n"]:>5d} {r["tp"]:>6.2f} {r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')
    pl.DataFrame(rows).write_csv(LOCAL_CACHE / 'sec_regime_label_cohorts.csv')

    # 2) Sector regime quintile
    print('\n  --- Sector regime QUINTILE (5 buckets, even split) ---')
    rows = cohort_zscore(df, 'sec_regime_quintile', 'sec_regime_quintile')
    sig = sorted([r for r in rows if abs(r['z']) >= 1.96], key=lambda r: -abs(r['z']))
    print(f'  {"side":4s} {"tier":5s} {"q":7s} {"N":>5s} {"TP%":>6s} {"Δpp":>6s} {"z":>6s}')
    for r in sig[:20]:
        print(f'  {r["side"]:4s} {r["tier"]:5s} {r["value"]:7s} '
              f'{r["n"]:>5d} {r["tp"]:>6.2f} {r["delta_pp"]:>+6.2f} {r["z"]:>+6.2f}')
    pl.DataFrame(rows).write_csv(LOCAL_CACHE / 'sec_regime_quintile_cohorts.csv')

    # 3) Comparison vs single-factor sec_etf_pct_ema50
    print('\n  --- Comparison: best z by feature ---')
    df_b = df.with_columns(
        pl.when(pl.col('sec_etf_pct_ema50').is_null()).then(pl.lit(None))
          .when(pl.col('sec_etf_pct_ema50') <= -5).then(pl.lit('vlow'))
          .when(pl.col('sec_etf_pct_ema50') <= -1).then(pl.lit('low'))
          .when(pl.col('sec_etf_pct_ema50') <= 1).then(pl.lit('neut'))
          .when(pl.col('sec_etf_pct_ema50') <= 5).then(pl.lit('high'))
          .otherwise(pl.lit('vhigh')).alias('sec_etf_pct_ema50_b'),
        pl.when(pl.col('sec_etf_rsi').is_null()).then(pl.lit(None))
          .when(pl.col('sec_etf_rsi') < 30).then(pl.lit('a_lt30'))
          .when(pl.col('sec_etf_rsi') < 45).then(pl.lit('b_30_45'))
          .when(pl.col('sec_etf_rsi') < 55).then(pl.lit('c_45_55'))
          .when(pl.col('sec_etf_rsi') < 70).then(pl.lit('d_55_70'))
          .otherwise(pl.lit('e_gt70')).alias('sec_etf_rsi_b')
    )

    feature_results = []
    for feat in ['sec_regime_label', 'sec_regime_quintile',
                 'sec_etf_pct_ema50_b', 'sec_etf_rsi_b']:
        rows = cohort_zscore(df_b, feat, feat)
        if not rows: continue
        max_abs_z = max(abs(r['z']) for r in rows if not math.isnan(r['z']))
        n_sig = sum(1 for r in rows if abs(r['z']) >= 1.96 and not math.isnan(r['z']))
        n_sig_strong = sum(1 for r in rows if abs(r['z']) >= 3 and not math.isnan(r['z']))
        feature_results.append({
            'feature': feat,
            'max_abs_z': round(max_abs_z, 2),
            'n_cells_z2': n_sig,
            'n_cells_z3': n_sig_strong,
            'top_cell': max(rows, key=lambda r: abs(r['z']) if not math.isnan(r['z']) else 0),
        })

    print(f'\n  {"feature":25s} {"max|z|":>7s} {"#cells |z|>=2":>14s} {"#cells |z|>=3":>14s}')
    for fr in sorted(feature_results, key=lambda x: -x['max_abs_z']):
        print(f'  {fr["feature"]:25s} {fr["max_abs_z"]:>7.2f} {fr["n_cells_z2"]:>14d} {fr["n_cells_z3"]:>14d}')

    print('\n  Top cells per feature:')
    for fr in feature_results:
        c = fr['top_cell']
        print(f'    {fr["feature"]:25s}: {c["side"]:4s} {c["tier"]:5s} {c["value"]:12s} '
              f'N={c["n"]:>4d} Δpp={c["delta_pp"]:>+6.2f} z={c["z"]:>+6.2f}')

    # 4) Marginal test — does sector_regime add info AFTER conditioning on sec_etf_pct_ema50?
    print('\n  --- Marginal test: sec_regime | sec_etf_pct_ema50 bucket ---')
    for etf_bucket in ['low', 'neut', 'high', 'vhigh']:
        sub = df_b.filter(pl.col('sec_etf_pct_ema50_b') == etf_bucket)
        if len(sub) < 500: continue
        rows = cohort_zscore(sub, 'sec_regime_quintile', f'sec_reg|etf={etf_bucket}', min_n=80)
        sig = [r for r in rows if abs(r['z']) >= 1.96]
        if sig:
            print(f'\n  sec_etf_pct_ema50 = {etf_bucket} (N_total={len(sub):,}):')
            for r in sorted(sig, key=lambda r: -abs(r['z']))[:5]:
                print(f'    {r["side"]:4s} {r["tier"]:5s} {r["value"]:10s} '
                      f'N={r["n"]:>4d} Δ={r["delta_pp"]:>+6.2f} z={r["z"]:>+6.2f}')
        else:
            print(f'\n  sec_etf_pct_ema50 = {etf_bucket} (N={len(sub):,}): no significant marginal regime cells')

    print(f'\n[done] {time.time()-t0:.1f}s total')


if __name__ == '__main__':
    main()
