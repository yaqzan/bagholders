"""Build v43 cohort parquet — same shape as sector_mcap_cohort/cohort_v39_3650.parquet
but for the active MCD-shipped version (id=43, e083032).

Joins:
  - v43 scores at qualifying tiers (overall >= 70 or <= 25)
  - barrier_outcomes (30dte_opt @ w=15d) from runner cache
  - Stock metadata (sector, industry, market_cap)
  - Production breadth_score on signal date

Read-only against production DB. Holdout-gated.
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

VERSION_ID = 43
LOOKBACK_DAYS = 1825  # 5y; barrier cache only covers ~5y
RUNNER_CACHE = ROOT / '.cache' / 'runner'
LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)


def fetch_v43_peaks() -> pl.DataFrame:
    cache_pq = LOCAL_CACHE / f'peaks_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists():
        df = pl.read_parquet(cache_pq)
        print(f'[peaks] cache hit: {len(df):,} rows', flush=True)
        return df

    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')
    cutoff_back = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[peaks] querying v{VERSION_ID} peaks since {cutoff_back} (chunked by year)...', flush=True)

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
        print(f'[peaks]   {yr}: {len(chunk):,} rows in {time.time()-ti:.1f}s', flush=True)
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


def fetch_stock_meta() -> pl.DataFrame:
    cache_pq = LOCAL_CACHE / 'stock_meta.parquet'
    if cache_pq.exists():
        return pl.read_parquet(cache_pq)
    from database.trader_database import DB
    cur = DB.execute_sql("SELECT symbol, sector, industry, market_cap FROM stocks")
    rows = cur.fetchall()
    data = {'symbol': [], 'sector': [], 'industry': [], 'market_cap': []}
    for sym, sec, ind, mc in rows:
        data['symbol'].append(sym)
        data['sector'].append(sec or 'unknown')
        data['industry'].append(ind or 'unknown')
        data['market_cap'].append(float(mc) if mc is not None else None)
    df = pl.DataFrame(data)
    df.write_parquet(cache_pq)
    return df


def main():
    peaks = fetch_v43_peaks()
    meta = fetch_stock_meta()

    # Load barriers (option-aligned, w=15d)
    barriers_pq = RUNNER_CACHE / 'barriers_30opt_w15_1825.parquet'
    if not barriers_pq.exists():
        raise RuntimeError(f'Missing {barriers_pq} — run fast_variant_runner first')
    barriers = pl.read_parquet(barriers_pq).select(
        ['symbol', 'date', 'side', 'result', 'exit_return', 'entry_close', 'sigma_pct']
    )
    print(f'[barriers] {len(barriers):,} rows', flush=True)

    # Side from overall
    peaks = peaks.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('low'))
          .when(pl.col('overall') <= 25).then(pl.lit('high'))
          .otherwise(None).alias('side'),
        pl.when(pl.col('overall') >= 70).then(pl.lit('CALL'))
          .when(pl.col('overall') <= 25).then(pl.lit('PUT'))
          .otherwise(None).alias('signal_type')
    )

    # Score buckets
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
          .otherwise(None).alias('score_bin'),
        pl.when(pl.col('overall') >= 75).then(pl.lit('75+'))
          .when(pl.col('overall') >= 70).then(pl.lit('70-74'))
          .when(pl.col('overall') <= 15).then(pl.lit('<=15'))
          .when(pl.col('overall') <= 25).then(pl.lit('16-25'))
          .otherwise(None).alias('tier_group')
    )

    # Join meta + mcap bin
    df = peaks.join(meta, on='symbol', how='left')
    df = df.with_columns((pl.col('market_cap') / 1e9).alias('mcap_b'))
    df = df.with_columns(
        pl.when(pl.col('mcap_b').is_null()).then(pl.lit('unknown'))
          .when(pl.col('mcap_b') < 2).then(pl.lit('micro_lt2B'))
          .when(pl.col('mcap_b') < 10).then(pl.lit('small_2-10B'))
          .when(pl.col('mcap_b') < 50).then(pl.lit('mid_10-50B'))
          .when(pl.col('mcap_b') < 200).then(pl.lit('large_50-200B'))
          .when(pl.col('mcap_b') < 1000).then(pl.lit('xl_200B-1T'))
          .otherwise(pl.lit('mega_1T+')).alias('mcap_bin')
    )

    # Inner join barriers
    df = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')

    # Join production breadth_score
    breadth_pq = RUNNER_CACHE / 'breadth_1825.parquet'
    if breadth_pq.exists():
        breadth = pl.read_parquet(breadth_pq)
        df = df.join(breadth, on='date', how='left')
        print(f'[breadth] joined: breadth_score coverage = '
              f'{df.filter(pl.col("breadth_score").is_not_null()).height:,}/{len(df):,}',
              flush=True)
    df = df.with_columns(
        pl.when(pl.col('result') == 1).then(pl.lit('TP'))
          .when(pl.col('result') == 0).then(pl.lit('SL'))
          .otherwise(pl.lit('EXP')).alias('outcome'),
        pl.when(pl.col('result') == 1).then(0).otherwise(1).alias('is_miss')
    )

    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, 'sector_etf_screen/build_v43_cohort')

    print(f'[joined] {len(df):,} rows after barriers + holdout', flush=True)
    print(f'[diag] date range: {df["date"].min()} -> {df["date"].max()}', flush=True)
    print(df.group_by('signal_type').len())

    out_pq = LOCAL_CACHE / f'cohort_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out_pq)
    print(f'[save] {out_pq}', flush=True)


if __name__ == '__main__':
    main()
