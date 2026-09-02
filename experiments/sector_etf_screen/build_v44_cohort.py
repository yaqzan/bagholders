"""Build v44 cohort parquet — same as build_v43_cohort but for v44 (ICH ship, d8024b9)."""
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

VERSION_ID = 44
LOOKBACK_DAYS = 1825
RUNNER_CACHE = ROOT / '.cache' / 'runner'
LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)


def fetch_peaks() -> pl.DataFrame:
    cache_pq = LOCAL_CACHE / f'peaks_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists():
        df = pl.read_parquet(cache_pq)
        print(f'[peaks] cache hit: {len(df):,} rows', flush=True)
        return df

    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')
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


def main():
    peaks = fetch_peaks()

    # Stock metadata
    meta_pq = LOCAL_CACHE / 'stock_meta.parquet'
    meta = pl.read_parquet(meta_pq) if meta_pq.exists() else None
    if meta is None:
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

    # Barriers
    barriers_pq = RUNNER_CACHE / 'barriers_30opt_w15_1825.parquet'
    barriers = pl.read_parquet(barriers_pq).select(
        ['symbol', 'date', 'side', 'result', 'exit_return', 'entry_close', 'sigma_pct']
    )

    peaks = peaks.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('low'))
          .when(pl.col('overall') <= 25).then(pl.lit('high'))
          .otherwise(None).alias('side'),
        pl.when(pl.col('overall') >= 70).then(pl.lit('CALL'))
          .when(pl.col('overall') <= 25).then(pl.lit('PUT'))
          .otherwise(None).alias('signal_type')
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
    df = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')
    df = df.with_columns(
        pl.when(pl.col('result') == 1).then(pl.lit('TP'))
          .when(pl.col('result') == 0).then(pl.lit('SL'))
          .otherwise(pl.lit('EXP')).alias('outcome'),
        pl.when(pl.col('result') == 1).then(0).otherwise(1).alias('is_miss')
    )

    # Production breadth
    breadth_pq = RUNNER_CACHE / 'breadth_1825.parquet'
    if breadth_pq.exists():
        b = pl.read_parquet(breadth_pq)
        df = df.join(b, on='date', how='left')

    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, 'sector_etf_screen/build_v44_cohort')

    print(f'[joined] {len(df):,} rows', flush=True)
    print(f'[date range] {df["date"].min()} -> {df["date"].max()}', flush=True)
    print(df.group_by('signal_type').len())

    out_pq = LOCAL_CACHE / f'cohort_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out_pq)
    print(f'[save] {out_pq}', flush=True)


if __name__ == '__main__':
    main()
