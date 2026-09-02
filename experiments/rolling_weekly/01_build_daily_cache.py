"""
Pull daily close cache for the universe of stocks that have v40 scores.

Need full 5y window of scoring + ~4y of buffer for EMA50/EMA200 convergence on
weekly bars (50 weekly bars × 5 trading days = 250 days; 200 weekly = 1000 days).

For pragmatic compute, we cap at 200 weekly bars of lookback (~1000 trading days
≈ 4y) which covers EMA50 fully and EMA200 partially. Stocks without enough
history get NaN for EMA200-dependent fields (matches existing system behavior).

Output: .cache/rolling_weekly/daily.parquet
"""
from __future__ import annotations
import io
import sys
import time
from datetime import date, timedelta
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

CACHE_DIR = Path('.cache/rolling_weekly')
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT = CACHE_DIR / 'daily.parquet'

# 5y of scoring + 4y buffer = 9y total
SCORING_LOOKBACK_DAYS = 1825   # 5y for evaluation
BUFFER_DAYS           = 1500   # extra ~4y for EMA convergence on weekly ladder


def main():
    from database.trader_database import DB
    from database.models.core import AlgorithmVersion, Score
    from peewee import fn

    v = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: v{v.id}/{v.git_commit[:8]}', flush=True)

    # Universe = symbols with at least one v40 score in the 5y window
    cutoff_score = (date.today() - timedelta(days=SCORING_LOOKBACK_DAYS)).isoformat()
    cutoff_daily = (date.today() - timedelta(days=SCORING_LOOKBACK_DAYS + BUFFER_DAYS)).isoformat()

    print(f'Scoring cutoff: {cutoff_score}', flush=True)
    print(f'Daily cutoff (with buffer): {cutoff_daily}', flush=True)

    print(f'\nFetching universe of v{v.id}-scored symbols...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT DISTINCT symbol FROM scores
        WHERE version_id = {v.id} AND date >= '{cutoff_score}'
    """)
    sym_rows = cur.fetchall()
    universe = sorted(set(r[0] for r in sym_rows if r[0]))
    print(f'  {len(universe):,} symbols ({time.time()-t0:.1f}s)', flush=True)

    print(f'\nFetching daily prices for {len(universe):,} stocks since {cutoff_daily}...', flush=True)
    t0 = time.time()
    placeholders = ','.join("'%s'" % s.replace("'", "''") for s in universe)
    cur = DB.execute_sql(f"""
        SELECT symbol, date, open, high, low, close, volume
        FROM price_history
        WHERE symbol IN ({placeholders})
          AND date >= '{cutoff_daily}'
        ORDER BY symbol, date
    """)
    rows = cur.fetchall()
    print(f'  fetched {len(rows):,} rows in {time.time()-t0:.1f}s', flush=True)

    print(f'\nBuilding Polars DataFrame...', flush=True)
    t0 = time.time()
    df = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'open':   [float(r[2]) if r[2] is not None else None for r in rows],
        'high':   [float(r[3]) if r[3] is not None else None for r in rows],
        'low':    [float(r[4]) if r[4] is not None else None for r in rows],
        'close':  [float(r[5]) if r[5] is not None else None for r in rows],
        'volume': [int(r[6])   if r[6] is not None else 0    for r in rows],
    })
    df = df.sort(['symbol', 'date'])
    print(f'  built {len(df):,} rows in {time.time()-t0:.1f}s', flush=True)

    df.write_parquet(OUT)
    print(f'\n[OK] saved {OUT}', flush=True)
    print(f'  symbols: {df["symbol"].n_unique()}')
    print(f'  date range: {df["date"].min()} -> {df["date"].max()}')


if __name__ == '__main__':
    main()
