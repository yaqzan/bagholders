"""Extract v32 scores 50+ (calls) from MySQL — wider universe for Path B retest.

Currently miss_ledger components are filtered at >=70 OR <=25.
For Path B sub-threshold-only boost to test the 60-74 promotion universe
fairly, we need scores 50+ to capture all potential promotion candidates.

Output: .cache/continuation_boost/components_v32_50plus.parquet
"""
from __future__ import annotations
import io, sys, time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

OUT = ROOT / '.cache' / 'continuation_boost' / 'components_v32_50plus.parquet'
LOOKBACK_DAYS = 1825


def main():
    print(f'Extract v32 scores >= 50 (5y)')
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB
    av = AlgorithmVersion.get_active_scores_version()
    print(f'  active version: id={av.id}  commit={av.git_commit}')

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'  cutoff date: {cutoff}')

    print(f'  querying ...')
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.trend
        FROM scores s
        WHERE s.version_id = {av.id}
          AND s.date >= '{cutoff}'
          AND s.overall >= 50
        ORDER BY s.symbol, s.date
    """)
    rows = cur.fetchall()
    print(f'  fetched {len(rows):,} rows in {time.time()-t0:.1f}s')

    df = pl.DataFrame(rows, schema=['symbol', 'date', 'overall', 'trend'], orient='row')
    print(f'  shape: {df.shape}')
    print(f'  overall distribution:')
    hist = df.with_columns((pl.col('overall') // 5 * 5).alias('bucket')).group_by('bucket').agg(pl.len().alias('count')).sort('bucket')
    for r in hist.iter_rows(named=True):
        print(f'    {r["bucket"]:>3}-{r["bucket"]+4:<3}: {r["count"]:>6,}')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT)
    print(f'\n[done] wrote {OUT.name} ({len(df):,} rows)')


if __name__ == '__main__':
    main()
