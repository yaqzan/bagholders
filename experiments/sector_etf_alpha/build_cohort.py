"""Build v46 cohort parquet for sector ETF alpha investigation.

Differences from sector_etf_screen/build_v44_cohort.py + build_features.py:
  - VERSION_ID = 46 (active version after WVD-Wave ship)
  - Uses GENERIC barriers (.cache/runner/barriers_1825.parquet) at w_days {1, 7, 15, 30}
    NOT the option-aligned 30dte_opt @ w=15. WR7 is the new Stage 1 primary metric.
  - Joins sector ETF features from .cache/sector_etf_screen/etf_features_wide.parquet (reuse)
  - Adds stock relative-strength features: stock_ret_5d / stock_ret_10d / stock_ret_20d
    (computed from PriceHistory, joined on signal_date)
  - Holdout filter mandatory

Output: .cache/sector_etf_alpha/cohort_v46_1825.parquet
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

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

VERSION_ID = 46
LOOKBACK_DAYS = 1825
RUNNER_CACHE = ROOT / '.cache' / 'runner'
SCREEN_CACHE = ROOT / '.cache' / 'sector_etf_screen'
LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_alpha'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)

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


def fetch_peaks() -> pl.DataFrame:
    cache_pq = LOCAL_CACHE / f'peaks_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists():
        df = pl.read_parquet(cache_pq)
        print(f'[peaks] cache hit: {len(df):,} rows', flush=True)
        return df

    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=600000')
    cutoff_back = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = []
    start_year = int(cutoff_back[:4])
    end_year = date.today().year + 1
    t0 = time.time()
    for yr in range(start_year, end_year):
        y_start = f'{yr}-01-01'
        y_end = f'{yr}-12-31'
        if yr == start_year:
            y_start = cutoff_back
        ti = time.time()
        cur = DB.execute_sql(f"""
            SELECT s.symbol, s.date, s.overall,
                   s.bb, s.trend, s.rsi, s.macd, s.stoch, s.technical_alignment,
                   s.volume_signal, s.volume_magnitude,
                   s.pct_from_ema50, s.bb_position,
                   s.regime_composite, s.regime_multiplier
            FROM scores s
            WHERE s.version_id = {VERSION_ID}
              AND s.date >= '{y_start}' AND s.date <= '{y_end}'
              AND s.overall IS NOT NULL
              AND ((s.overall >= 70) OR (s.overall <= 25))
        """)
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f'[peaks]   {yr}: {len(chunk):,} in {time.time()-ti:.1f}s', flush=True)
    print(f'[peaks] total {len(rows):,} rows in {time.time()-t0:.1f}s', flush=True)

    cols = ['symbol', 'date', 'overall', 'bb', 'trend', 'rsi', 'macd', 'stoch', 'ta',
            'vol_sig', 'vol_mag', 'ema50_pct', 'bb_pos', 'reg_comp', 'reg_mult']
    data = {c: [] for c in cols}
    for r in rows:
        sym, d, ov, bb, tr, rs, mc, st, ta, vs, vm, e50, bbp, rc, rm = r
        data['symbol'].append(sym)
        data['date'].append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        data['overall'].append(int(ov))
        data['bb'].append(int(bb) if bb is not None else None)
        data['trend'].append(int(tr) if tr is not None else None)
        data['rsi'].append(int(rs) if rs is not None else None)
        data['macd'].append(int(mc) if mc is not None else None)
        data['stoch'].append(int(st) if st is not None else None)
        data['ta'].append(int(ta) if ta is not None else None)
        data['vol_sig'].append(vs or 'NEUTRAL')
        data['vol_mag'].append(float(vm) if vm is not None else 0.0)
        data['ema50_pct'].append(float(e50) if e50 is not None else None)
        data['bb_pos'].append(float(bbp) if bbp is not None else None)
        data['reg_comp'].append(float(rc) if rc is not None else None)
        data['reg_mult'].append(float(rm) if rm is not None else None)

    df = pl.DataFrame(data)
    df.write_parquet(cache_pq)
    return df


def fetch_stock_returns(peaks: pl.DataFrame) -> pl.DataFrame:
    """Compute stock_ret_5d / 10d / 20d at signal date for each (sym, date) in peaks.

    Pull (sym, date, close) from PriceHistory for unique syms in peaks, then
    self-join shifted closes to compute returns. Output: (symbol, date, ret_5d, ret_10d, ret_20d).
    """
    cache_pq = LOCAL_CACHE / f'stock_returns_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists():
        return pl.read_parquet(cache_pq)

    syms = peaks['symbol'].unique().to_list()
    print(f'[stock_ret] fetching close history for {len(syms):,} unique symbols', flush=True)
    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=600000')

    cutoff_back = (date.today() - timedelta(days=LOOKBACK_DAYS + 60)).isoformat()
    # Chunk symbol IN clause to avoid query size limits
    CHUNK = 100
    rows = []
    t0 = time.time()
    for i in range(0, len(syms), CHUNK):
        chunk = syms[i:i+CHUNK]
        sym_list = "', '".join(chunk)
        cur = DB.execute_sql(f"""
            SELECT symbol, date, close
            FROM price_history
            WHERE symbol IN ('{sym_list}')
              AND date >= '{cutoff_back}'
        """)
        rows.extend(cur.fetchall())
        if (i // CHUNK) % 5 == 0:
            print(f'[stock_ret]   chunk {i//CHUNK+1}/{(len(syms)+CHUNK-1)//CHUNK}: {len(rows):,} rows so far', flush=True)
    print(f'[stock_ret] {len(rows):,} price rows in {time.time()-t0:.1f}s', flush=True)

    ph = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]) for r in rows],
        'close': [float(r[2]) for r in rows],
    }).sort(['symbol', 'date'])

    ph = ph.with_columns([
        (pl.col('close') / pl.col('close').shift(5).over('symbol') - 1.0).alias('stock_ret_5d'),
        (pl.col('close') / pl.col('close').shift(10).over('symbol') - 1.0).alias('stock_ret_10d'),
        (pl.col('close') / pl.col('close').shift(20).over('symbol') - 1.0).alias('stock_ret_20d'),
    ])
    out = ph.select(['symbol', 'date', 'stock_ret_5d', 'stock_ret_10d', 'stock_ret_20d'])
    out.write_parquet(cache_pq)
    print(f'[stock_ret] saved: {len(out):,} rows', flush=True)
    return out


def main():
    peaks = fetch_peaks()
    stock_ret = fetch_stock_returns(peaks)

    # Stock metadata
    meta_pq = SCREEN_CACHE / 'stock_meta.parquet'
    if not meta_pq.exists():
        from database.trader_database import DB
        cur = DB.execute_sql("SELECT symbol, sector, industry, market_cap FROM stocks")
        rows = cur.fetchall()
        d = {'symbol': [], 'sector': [], 'industry': [], 'market_cap': []}
        for sym, sec, ind, mc in rows:
            d['symbol'].append(sym)
            d['sector'].append(sec or 'unknown')
            d['industry'].append(ind or 'unknown')
            d['market_cap'].append(float(mc) if mc is not None else None)
        meta = pl.DataFrame(d)
        meta.write_parquet(meta_pq)
    meta = pl.read_parquet(meta_pq)

    # Multi-W barriers (generic, K=2σ HIGH / K=1σ LOW per assess_scores)
    barriers_long = pl.read_parquet(RUNNER_CACHE / 'barriers_1825.parquet')
    # We need W in {1, 7, 15, 30}
    keep_w = [1, 7, 15, 30]
    bar = barriers_long.filter(pl.col('w_days').is_in(keep_w))
    bar = bar.select(['symbol', 'date', 'side', 'w_days', 'result', 'mae_pct', 'mfe_pct', 'exit_return'])

    peaks = peaks.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('low'))
          .when(pl.col('overall') <= 25).then(pl.lit('high'))
          .otherwise(None).alias('side'),
        pl.when(pl.col('overall') >= 70).then(pl.lit('CALL'))
          .when(pl.col('overall') <= 25).then(pl.lit('PUT'))
          .otherwise(None).alias('signal_type'),
    )
    peaks = peaks.with_columns(
        pl.when(pl.col('overall') >= 95).then(pl.lit('95+'))
          .when(pl.col('overall') >= 90).then(pl.lit('90-94'))
          .when(pl.col('overall') >= 85).then(pl.lit('85-89'))
          .when(pl.col('overall') >= 80).then(pl.lit('80-84'))
          .when(pl.col('overall') >= 75).then(pl.lit('75-79'))
          .when(pl.col('overall') >= 70).then(pl.lit('70-74'))
          .when(pl.col('overall') <= 5).then(pl.lit('0-5'))
          .when(pl.col('overall') <= 10).then(pl.lit('6-10'))
          .when(pl.col('overall') <= 15).then(pl.lit('11-15'))
          .when(pl.col('overall') <= 20).then(pl.lit('16-20'))
          .when(pl.col('overall') <= 25).then(pl.lit('21-25'))
          .otherwise(None).alias('score_bin')
    )

    df = peaks.join(meta, on='symbol', how='left')
    df = df.with_columns((pl.col('market_cap') / 1e9).alias('mcap_b'))
    df = df.join(stock_ret, on=['symbol', 'date'], how='left')

    # Pivot the multi-W barriers WIDE: one row per (sym, date, side) with wr_1, wr_7, wr_15, wr_30
    bar_wide = bar.pivot(values='result', index=['symbol', 'date', 'side'], on='w_days', aggregate_function='first')
    rename_map = {str(w): f'wr_{w}' for w in keep_w}
    bar_wide = bar_wide.rename(rename_map)

    # Also pivot mfe_pct and mae_pct for diagnostics
    mfe_wide = bar.pivot(values='mfe_pct', index=['symbol', 'date', 'side'], on='w_days', aggregate_function='first').rename({str(w): f'mfe_{w}' for w in keep_w})
    mae_wide = bar.pivot(values='mae_pct', index=['symbol', 'date', 'side'], on='w_days', aggregate_function='first').rename({str(w): f'mae_{w}' for w in keep_w})

    df = df.join(bar_wide, on=['symbol', 'date', 'side'], how='inner')
    df = df.join(mfe_wide, on=['symbol', 'date', 'side'], how='left')
    df = df.join(mae_wide, on=['symbol', 'date', 'side'], how='left')

    # Production breadth
    breadth_pq = RUNNER_CACHE / 'breadth_1825.parquet'
    if breadth_pq.exists():
        b = pl.read_parquet(breadth_pq)
        df = df.join(b, on='date', how='left')

    # ETF features wide (reuse from sector_etf_screen)
    etf_wide_pq = SCREEN_CACHE / 'etf_features_wide.parquet'
    etf_wide = pl.read_parquet(etf_wide_pq)
    df = df.join(etf_wide, on='date', how='left')

    # Map sector -> ETF -> per-stock sector features
    sector_to_etf_pl = pl.DataFrame(
        {'sector': list(SECTOR_ETF_MAP.keys()),
         'sector_etf': [v for v in SECTOR_ETF_MAP.values()]}
    )
    df = df.join(sector_to_etf_pl, on='sector', how='left')

    feat_keys = ['pct_ema50', 'pct_ema200', 'rsi', 'macd', 'ret_5d', 'ret_20d']
    for k in feat_keys:
        expr = pl.lit(None, dtype=pl.Float64)
        for etf in SECTOR_ETF_MAP.values():
            col_name = f'{etf}_{k}'
            if col_name in df.columns:
                expr = pl.when(pl.col('sector_etf') == etf).then(pl.col(col_name)).otherwise(expr)
        df = df.with_columns(expr.alias(f'sec_etf_{k}'))

    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, 'sector_etf_alpha/build_cohort')

    print(f'[joined] {len(df):,} rows', flush=True)
    print(f'[date range] {df["date"].min()} -> {df["date"].max()}', flush=True)
    print(df.group_by('signal_type').len())
    n_with_sec_etf = df.filter(pl.col('sec_etf_pct_ema50').is_not_null()).height
    print(f'[diag] sec_etf coverage: {n_with_sec_etf:,} / {len(df):,} = {100*n_with_sec_etf/len(df):.1f}%', flush=True)
    n_with_stock_ret = df.filter(pl.col('stock_ret_10d').is_not_null()).height
    print(f'[diag] stock_ret_10d coverage: {n_with_stock_ret:,} / {len(df):,} = {100*n_with_stock_ret/len(df):.1f}%', flush=True)

    out_pq = LOCAL_CACHE / f'cohort_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out_pq)
    print(f'[save] {out_pq} ({len(df):,} rows, {len(df.columns)} cols)', flush=True)


if __name__ == '__main__':
    main()
