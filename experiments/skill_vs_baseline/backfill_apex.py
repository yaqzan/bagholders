"""0a: backfill the 30dte_apex option-aligned barrier set (10y) into the cache.

Additive — writes only barrier_set='30dte_apex' rows; existing sets untouched.
backfill_sets auto-rebuilds the DuckDB read mirror at the end. db-heavy
(reads price_history per symbol). Queue it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.barrier_cache import backfill_sets

if __name__ == "__main__":
    n = backfill_sets(barrier_sets=["30dte_apex"], lookback_days=3650, verbose=True)
    print(f"\n30dte_apex backfill complete: {n:,} rows", flush=True)
