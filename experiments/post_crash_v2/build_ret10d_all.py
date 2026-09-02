"""Build ret_10d feature for ALL (symbol, date) pairs in lookback window.

The post_crash_v2/features parquet only covers put peaks. The fast_variant_runner
needs the feature joined onto ALL scored rows (calls + puts + neutral) so that
re-bucketing after lift produces correct cumulative WR per tier.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.cache' / 'post_crash_v2' / 'ret10d_all_1825.parquet'
LOOKBACK_DAYS = 1825


def main():
    from database.trader_database import DB

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS + 30)).isoformat()
    print(f'[ret10d-all] cutoff: {cutoff}', flush=True)

    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT symbol, date, close FROM price_history WHERE date >= '{cutoff}'
    """)
    rows = cur.fetchall()
    ph = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'close': [float(r[2]) for r in rows],
    }).sort(['symbol', 'date'])
    print(f'[ret10d-all] loaded {len(ph):,} rows ({time.time()-t0:.1f}s)', flush=True)

    t0 = time.time()
    ph = ph.with_columns([
        pl.col('close').pct_change(10).over('symbol').alias('ret_10d'),
        pl.col('close').pct_change(5).over('symbol').alias('ret_5d'),
    ])
    out = ph.select(['symbol', 'date', 'ret_5d', 'ret_10d'])
    out = out.filter(pl.col('ret_10d').is_not_null())
    print(f'[ret10d-all] computed window features, {len(out):,} valid rows ({time.time()-t0:.1f}s)', flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(OUT)
    print(f'[ret10d-all] wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB)', flush=True)


if __name__ == '__main__':
    main()
