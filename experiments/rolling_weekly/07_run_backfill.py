"""Production backfill driver — uses database.utils.rolling_weekly.bulk_backfill."""
from __future__ import annotations
import io
import os
import sys
import time
from datetime import date

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

from database.utils.rolling_weekly import bulk_backfill


def main():
    t0 = time.time()
    n = bulk_backfill(parallel=True, verbose=True)
    print(f'\n[OK] backfill complete: {n:,} rows in {time.time()-t0:.1f}s')

    # Sanity stats
    from database.models.technical import RollingWeeklyIndicator
    from peewee import fn
    print(f'\nSanity check:')
    print(f'  total rows: {RollingWeeklyIndicator.select().count():,}')
    print(f'  symbols: {RollingWeeklyIndicator.select(RollingWeeklyIndicator.symbol).distinct().count()}')
    mn = RollingWeeklyIndicator.select(fn.MIN(RollingWeeklyIndicator.date)).scalar()
    mx = RollingWeeklyIndicator.select(fn.MAX(RollingWeeklyIndicator.date)).scalar()
    print(f'  date range: {mn} -> {mx}')


if __name__ == '__main__':
    main()
