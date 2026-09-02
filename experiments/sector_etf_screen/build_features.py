"""Sector ETF screen — augment v39 cohort parquet with ETF technical features.

Reads the existing 34k-peak cohort table from sector_mcap_cohort and joins:
  - SPY features (market backdrop, 100% coverage) for Phase 0
  - Sector ETF features (XLF/XLE/XLP/XLU/XLC + SMH for tech proxy) per Stock.sector
  - Reference broad ETFs (QQQ, IWM, TLT, GLD, HYG) for ETF-as-breadth basket

Per-ETF features computed at signal date:
  - pct_above_ema50  = (close - ema_50) / ema_50 * 100
  - pct_above_ema200 = (close - ema_200) / ema_200 * 100
  - rsi              = raw RSI(14)
  - ret_5d           = 5-trading-day return
  - ret_20d          = 20-trading-day return

Read-only against production DB. Writes only to .cache/sector_etf_screen/.
Holdout-gated to CALIBRATION_CUTOFF_DATE.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

# --- Config ----------------------------------------------------------------
SOURCE_PQ = ROOT / '.cache' / 'sector_etf_screen' / 'cohort_v43_1825.parquet'
LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)

# All ETFs we want features for — full SPDR sector set + broad indices + cross-asset
ALL_ETFS = ['SPY', 'QQQ', 'IWM',
            'XLK', 'XLV', 'XLY', 'XLI', 'XLB', 'XLRE',  # backfilled 2026-05-07
            'XLF', 'XLE', 'XLP', 'XLU', 'XLC',
            'SMH', 'SOXX',
            'TLT', 'GLD', 'SLV', 'HYG']

# Sector → primary SPDR ETF mapping (full GICS-11 coverage post-backfill)
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


def fetch_etf_features() -> pl.DataFrame:
    """Pull (symbol, date, close, ema_50, ema_200, rsi) for all ETFs in our basket
    and compute per-row features. Returns long-format df with one row per (etf, date).
    """
    cache_pq = LOCAL_CACHE / 'etf_features_long.parquet'
    if cache_pq.exists():
        df = pl.read_parquet(cache_pq)
        print(f'[etf] cache hit: {len(df):,} rows', flush=True)
        return df

    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')

    syms_sql = "', '".join(ALL_ETFS)
    print(f'[etf] querying close+indicators for {len(ALL_ETFS)} ETFs...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT i.symbol, i.date, ph.close, i.ema_50, i.ema_200, i.rsi, i.macd
        FROM indicators i
        JOIN price_history ph ON ph.symbol = i.symbol AND ph.date = i.date
        WHERE i.symbol IN ('{syms_sql}')
        ORDER BY i.symbol, i.date
    """)
    rows = cur.fetchall()
    print(f'[etf] {len(rows):,} rows in {time.time()-t0:.1f}s', flush=True)

    data = {'etf': [], 'date': [], 'close': [], 'ema_50': [], 'ema_200': [],
            'rsi': [], 'macd': []}
    for sym, d, close, e50, e200, r, m in rows:
        data['etf'].append(sym)
        data['date'].append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        data['close'].append(float(close) if close is not None else None)
        data['ema_50'].append(float(e50) if e50 is not None else None)
        data['ema_200'].append(float(e200) if e200 is not None else None)
        data['rsi'].append(float(r) if r is not None else None)
        data['macd'].append(float(m) if m is not None else None)

    df = pl.DataFrame(data)
    # Compute pct features
    df = df.with_columns([
        ((pl.col('close') - pl.col('ema_50')) / pl.col('ema_50') * 100).alias('pct_ema50'),
        ((pl.col('close') - pl.col('ema_200')) / pl.col('ema_200') * 100).alias('pct_ema200'),
    ])
    # 5d / 20d return — compute per-etf
    df = df.sort(['etf', 'date'])
    df = df.with_columns([
        ((pl.col('close') / pl.col('close').shift(5).over('etf')) - 1.0).alias('ret_5d'),
        ((pl.col('close') / pl.col('close').shift(20).over('etf')) - 1.0).alias('ret_20d'),
    ])

    df.write_parquet(cache_pq)
    print(f'[etf] cached features: {len(df):,} rows', flush=True)
    return df


def pivot_etf_wide(etf_long: pl.DataFrame) -> pl.DataFrame:
    """Convert long-format ETF features to wide: one row per date, columns
    like 'SPY_pct_ema50', 'SPY_rsi', 'XLF_pct_ema50', ..."""
    cache_pq = LOCAL_CACHE / 'etf_features_wide.parquet'
    if cache_pq.exists():
        return pl.read_parquet(cache_pq)

    feat_cols = ['close', 'pct_ema50', 'pct_ema200', 'rsi', 'macd', 'ret_5d', 'ret_20d']
    pieces = []
    for etf in etf_long['etf'].unique():
        sub = etf_long.filter(pl.col('etf') == etf).select(['date'] + feat_cols)
        renamed = sub.rename({c: f'{etf}_{c}' for c in feat_cols})
        pieces.append(renamed)

    # Reduce join
    out = pieces[0]
    for p in pieces[1:]:
        out = out.join(p, on='date', how='outer_coalesce')
    out = out.sort('date')
    out.write_parquet(cache_pq)
    print(f'[etf] wide cache: {len(out):,} dates × {len(out.columns)-1} feature cols', flush=True)
    return out


def main():
    if not SOURCE_PQ.exists():
        raise RuntimeError(f'Missing source cohort parquet: {SOURCE_PQ}')

    print(f'[load] reading source: {SOURCE_PQ}', flush=True)
    coh = pl.read_parquet(SOURCE_PQ)
    print(f'[load] source cohort: {len(coh):,} rows, date range '
          f'{coh["date"].min()} -> {coh["date"].max()}', flush=True)

    # Build ETF features
    etf_long = fetch_etf_features()
    etf_wide = pivot_etf_wide(etf_long)

    # Join wide features to cohort by date
    coh = coh.join(etf_wide, on='date', how='left')

    # Map sector -> ETF -> per-stock sector features
    sector_to_etf_pl = pl.DataFrame(
        {'sector': list(SECTOR_ETF_MAP.keys()),
         'sector_etf': [v if v is not None else 'NONE' for v in SECTOR_ETF_MAP.values()]}
    )
    coh = coh.join(sector_to_etf_pl, on='sector', how='left')

    # Build sec_etf_* cols per row by selecting the right ETF column
    # Polars doesn't have easy column-selection-by-name-from-row, so do it via when/then chain
    feat_keys = ['pct_ema50', 'pct_ema200', 'rsi', 'ret_5d', 'ret_20d']
    for k in feat_keys:
        # For each sector ETF, pick its column when sector_etf matches; else null
        expr = pl.lit(None, dtype=pl.Float64)
        for etf in ['XLK', 'XLV', 'XLY', 'XLI', 'XLB', 'XLRE',
                    'XLF', 'XLE', 'XLP', 'XLU', 'XLC']:
            col_name = f'{etf}_{k}'
            if col_name in coh.columns:
                expr = pl.when(pl.col('sector_etf') == etf).then(pl.col(col_name)).otherwise(expr)
        coh = coh.with_columns(expr.alias(f'sec_etf_{k}'))

    # Holdout filter
    coh = pre_cutoff_filter(coh)
    assert_no_holdout_leak(coh, 'sector_etf_screen/build_features')

    # Coverage diagnostics
    n_total = len(coh)
    n_with_sector_etf = coh.filter(pl.col('sec_etf_pct_ema50').is_not_null()).height
    n_with_spy = coh.filter(pl.col('SPY_pct_ema50').is_not_null()).height

    print(f'[diag] total signals: {n_total:,}', flush=True)
    print(f'[diag] SPY coverage: {n_with_spy:,} ({100*n_with_spy/n_total:.1f}%)', flush=True)
    print(f'[diag] sector ETF coverage: {n_with_sector_etf:,} ({100*n_with_sector_etf/n_total:.1f}%)', flush=True)
    print(f'[diag] sector breakdown:', flush=True)
    print(coh.group_by(['sector', 'sector_etf']).len().sort('len', descending=True))

    out_pq = LOCAL_CACHE / 'cohort_v43_etf.parquet'
    coh.write_parquet(out_pq)
    print(f'[save] {out_pq} ({len(coh):,} rows, {len(coh.columns)} cols)', flush=True)


if __name__ == '__main__':
    main()
