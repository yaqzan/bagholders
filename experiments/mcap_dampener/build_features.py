"""Build mcap dampener calibration parquet.

Pulls v39 call peaks (overall >= 70) over 10y window, joins:
  - market_cap (in $B) from stocks table
  - 30dte_opt @ w=15 barrier outcome (from .cache/barrier_outcomes.db)

Filters to ≤ CALIBRATION_CUTOFF_DATE (Priority #11 holdout lock).

Output: .cache/mcap_dampener/calls_v39_3650.parquet

Notes:
  - Barrier outcomes only available 2021-03+; resolved subset is ~5y.
  - Pre-2021 score data is preserved (NULL outcome) for context.
"""
from __future__ import annotations
import io, sys, sqlite3, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'mcap_dampener'
OUT_DIR.mkdir(parents=True, exist_ok=True)

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

LOOKBACK_DAYS = 3650


def main():
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    print(f'[build] Active version: id={av.id} ({av.git_commit[:8]})', flush=True)

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')

    # --- v39 call peaks (>=70) over 10y, chunked by year to avoid timeout ---
    print(f'[build] querying v39 call peaks since {cutoff} (chunked)...', flush=True)
    t0 = time.time()
    rows = []
    start_year = int(cutoff[:4])
    end_year = date.today().year + 1
    for yr in range(start_year, end_year):
        y_start = f'{yr}-01-01' if yr != start_year else cutoff
        y_end = f'{yr}-12-31'
        ti = time.time()
        cur = DB.execute_sql(f"""
            SELECT s.symbol, s.date, s.overall
            FROM scores s
            WHERE s.version_id = {av.id}
              AND s.date >= '{y_start}' AND s.date <= '{y_end}'
              AND s.overall >= 70
        """)
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f'[build]   {yr}: {len(chunk):,} ({time.time()-ti:.1f}s)', flush=True)
    print(f'[build] total {len(rows):,} call peaks ({time.time()-t0:.1f}s)', flush=True)

    peaks = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'overall': [int(r[2]) for r in rows],
    })

    # --- Stock meta (sector, industry, market_cap) ---
    cur = DB.execute_sql("SELECT symbol, sector, industry, market_cap FROM stocks")
    meta_rows = cur.fetchall()
    meta = pl.DataFrame({
        'symbol': [r[0] for r in meta_rows],
        'sector': [r[1] or 'unknown' for r in meta_rows],
        'industry': [r[2] or 'unknown' for r in meta_rows],
        'market_cap': [float(r[3]) if r[3] is not None else None for r in meta_rows],
    })
    meta = meta.with_columns((pl.col('market_cap') / 1e9).alias('mcap_b'))
    print(f'[build] meta: {len(meta):,} stocks ({meta.filter(pl.col("mcap_b").is_not_null()).height} with mcap)', flush=True)

    peaks = peaks.join(meta.select(['symbol', 'sector', 'industry', 'mcap_b']), on='symbol', how='left')

    # --- Barrier outcomes (30dte_opt @ w=15, side='low') ---
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    barriers = pl.read_database(
        f"""SELECT symbol, date, result AS opt_result_15,
                   exit_return AS opt_exit_return_15,
                   mae_pct AS opt_mae_15,
                   mfe_pct AS opt_mfe_15
            FROM barrier_outcomes
            WHERE side='low' AND barrier_set='30dte_opt'
              AND date >= '{cutoff}' AND w_days = 15""",
        connection=conn,
    )
    conn.close()
    print(f'[build] barriers: {len(barriers):,} ({time.time()-t0:.1f}s)', flush=True)

    df = peaks.join(barriers, on=['symbol', 'date'], how='left')

    # --- Holdout gate ---
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, 'mcap_dampener/build_features')

    # --- Diagnostics ---
    dmin = df.select(pl.col('date').min()).item()
    dmax = df.select(pl.col('date').max()).item()
    print(f'[build] date range: {dmin} → {dmax}', flush=True)
    n_resolved = df.filter(pl.col('opt_result_15').is_not_null()).height
    print(f'[build] total rows: {len(df):,} | with resolved outcome: {n_resolved:,}', flush=True)
    n_with_mcap = df.filter(pl.col('mcap_b').is_not_null() & pl.col('opt_result_15').is_not_null()).height
    print(f'[build] rows with mcap AND resolved outcome: {n_with_mcap:,}', flush=True)

    # mcap distribution among resolved
    print(f'[build] mcap_b distribution (resolved):', flush=True)
    sub = df.filter(pl.col('mcap_b').is_not_null() & pl.col('opt_result_15').is_not_null())
    pcts = sub.select([
        pl.col('mcap_b').quantile(0.01).alias('p1'),
        pl.col('mcap_b').quantile(0.10).alias('p10'),
        pl.col('mcap_b').quantile(0.25).alias('p25'),
        pl.col('mcap_b').quantile(0.50).alias('p50'),
        pl.col('mcap_b').quantile(0.75).alias('p75'),
        pl.col('mcap_b').quantile(0.90).alias('p90'),
        pl.col('mcap_b').quantile(0.99).alias('p99'),
    ])
    print(pcts)

    out = OUT_DIR / f'calls_v{av.id}_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out)
    print(f'[build] wrote {out} ({out.stat().st_size/1e6:.1f} MB, {len(df):,} rows)', flush=True)


if __name__ == '__main__':
    main()
