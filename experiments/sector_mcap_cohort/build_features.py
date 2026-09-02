"""Sector/mcap cohort feature build for v39 (10y window, ≤cutoff).

Joins:
  - v39 score components + outcome from existing miss_ledger style pull
  - Stock metadata (sector, industry, market_cap)
  - Available barrier outcomes (30dte_opt @ w=15d) from runner cache
  - Breadth (small)

Outputs:
  .cache/sector_mcap_cohort/cohort_v39_3650.parquet

Notes:
  - Barrier outcomes are only available 2021-03-12+ (~5y), so 10y score data
    will yield ~5y of resolved-outcome rows. This is honest and matches the
    barrier_outcomes.db cache extent. Pre-2021 score signals are dropped at
    the inner join with barriers.
  - Holdout gate: drops anything past CALIBRATION_CUTOFF_DATE.
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

LOOKBACK_DAYS = 3650  # 10y target — but effective coverage limited by barrier_outcomes.db
RUNNER_CACHE = ROOT / '.cache' / 'runner'
LOCAL_CACHE = ROOT / '.cache' / 'sector_mcap_cohort'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)


def fetch_v39_components(refresh: bool = False) -> pl.DataFrame:
    """Pull v39 score peaks (>=70 or <=25) for full 10y window."""
    cache_pq = LOCAL_CACHE / f'components_v39_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists() and not refresh:
        t0 = time.time()
        df = pl.read_parquet(cache_pq)
        print(f'[features] components cache hit: {len(df):,} rows in {time.time()-t0:.1f}s', flush=True)
        return df

    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')
    cutoff_back = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[features] querying v39 peaks since {cutoff_back} (chunked by year)...', flush=True)
    t0 = time.time()
    rows = []
    # Chunk by year to keep individual queries small
    start_year = int(cutoff_back[:4])
    end_year = date.today().year + 1
    for yr in range(start_year, end_year):
        y_start = f'{yr}-01-01'
        y_end = f'{yr}-12-31'
        # First-year clamp to cutoff_back
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
            WHERE s.version_id = 39
              AND s.date >= '{y_start}' AND s.date <= '{y_end}'
              AND s.overall IS NOT NULL
              AND ((s.overall >= 70) OR (s.overall <= 25))
        """)
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f'[features]   {yr}: {len(chunk):,} rows in {time.time()-ti:.1f}s', flush=True)
    print(f'[features] total {len(rows):,} component rows in {time.time()-t0:.1f}s', flush=True)

    cols = ['symbol', 'date', 'overall',
            'bb', 'trend', 'rsi', 'macd', 'stoch', 'ta',
            'vol_sig', 'vol_mag',
            'ema50_pct', 'bb_pos',
            'reg_comp', 'reg_mult']
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
    print(f'[features] cached {len(df):,} rows', flush=True)
    return df


def fetch_stock_meta(refresh: bool = False) -> pl.DataFrame:
    """Pull symbol -> sector, industry, market_cap from stocks table."""
    cache_pq = LOCAL_CACHE / 'stock_meta.parquet'
    if cache_pq.exists() and not refresh:
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
    print(f'[features] stock_meta: {len(df):,} stocks ({df.filter(pl.col("sector") != "unknown").height} with sector)', flush=True)
    return df


def main(refresh: bool = False):
    # 1. Component scores (10y v39)
    comp = fetch_v39_components(refresh=refresh)

    # 2. Stock metadata
    meta = fetch_stock_meta(refresh=refresh)

    # 3. Barrier outcomes (30 DTE option-aligned, w=15d) — from runner cache (5y span)
    barriers_pq = RUNNER_CACHE / 'barriers_30opt_w15_1825.parquet'
    if not barriers_pq.exists():
        raise RuntimeError(f'Missing {barriers_pq} — run fast_variant_runner first')
    barriers = pl.read_parquet(barriers_pq).select(
        ['symbol', 'date', 'side', 'result', 'exit_return',
         'entry_close', 'sigma_pct']
    )
    print(f'[features] barriers cache: {len(barriers):,} rows', flush=True)

    # 4. Breadth (optional)
    breadth_pq = RUNNER_CACHE / 'breadth_1825.parquet'
    breadth = pl.read_parquet(breadth_pq) if breadth_pq.exists() else None

    # 5. Side from overall
    comp = comp.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('low'))
          .when(pl.col('overall') <= 25).then(pl.lit('high'))
          .otherwise(None).alias('side'),
        pl.when(pl.col('overall') >= 70).then(pl.lit('CALL'))
          .when(pl.col('overall') <= 25).then(pl.lit('PUT'))
          .otherwise(None).alias('signal_type')
    )

    # 6. Score buckets
    comp = comp.with_columns(
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
        # Cumulative tier groupings for cohort rollups
        pl.when(pl.col('overall') >= 75).then(pl.lit('75+'))
          .when(pl.col('overall') >= 70).then(pl.lit('70-74'))
          .when(pl.col('overall') <= 15).then(pl.lit('<=15'))
          .when(pl.col('overall') <= 25).then(pl.lit('16-25'))
          .otherwise(None).alias('tier_group')
    )

    # 7. Join meta
    df = comp.join(meta, on='symbol', how='left')

    # 8. Market cap bins (in $B)
    df = df.with_columns(
        (pl.col('market_cap') / 1e9).alias('mcap_b')
    )
    df = df.with_columns(
        pl.when(pl.col('mcap_b').is_null()).then(pl.lit('unknown'))
          .when(pl.col('mcap_b') < 2).then(pl.lit('micro_lt2B'))
          .when(pl.col('mcap_b') < 10).then(pl.lit('small_2-10B'))
          .when(pl.col('mcap_b') < 50).then(pl.lit('mid_10-50B'))
          .when(pl.col('mcap_b') < 200).then(pl.lit('large_50-200B'))
          .when(pl.col('mcap_b') < 1000).then(pl.lit('xl_200B-1T'))
          .otherwise(pl.lit('mega_1T+')).alias('mcap_bin')
    )

    # 9. Inner-join with barriers (drops pre-2021 signals)
    df = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')
    if breadth is not None:
        df = df.join(breadth, on='date', how='left')
    print(f'[features] joined: {len(df):,} rows after barrier join', flush=True)

    # 10. Outcome label
    df = df.with_columns(
        pl.when(pl.col('result') == 1).then(pl.lit('TP'))
          .when(pl.col('result') == 0).then(pl.lit('SL'))
          .otherwise(pl.lit('EXP')).alias('outcome'),
        pl.when(pl.col('result') == 1).then(0).otherwise(1).alias('is_miss')
    )

    # 11. Holdout gate — assert no leak past calibration cutoff
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, 'sector_mcap_cohort/build_features')

    # 12. Date range diagnostic
    dmin = df.select(pl.col('date').min()).item()
    dmax = df.select(pl.col('date').max()).item()
    print(f'[features] effective date range: {dmin} → {dmax}', flush=True)
    print(f'[features] sector x signal_type counts:', flush=True)
    print(df.group_by(['sector', 'signal_type']).len().sort(['sector', 'signal_type']))
    print(f'[features] mcap_bin counts:', flush=True)
    print(df.group_by('mcap_bin').len().sort('mcap_bin'))

    # 13. Save
    out_pq = LOCAL_CACHE / f'cohort_v39_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out_pq)
    print(f'[features] wrote {out_pq} ({len(df):,} rows, {len(df.columns)} cols)', flush=True)


if __name__ == '__main__':
    refresh = '--refresh' in sys.argv
    main(refresh=refresh)
