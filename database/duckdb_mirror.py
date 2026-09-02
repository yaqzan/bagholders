"""DuckDB analytical sidecar.

Mirrors selected MySQL tables (price_history, scores, indicators, market_breadth,
market_regime, weekly_indicators, weekly_price_history) into a single DuckDB
file via Parquet exports. Experiment scripts can then run analytical queries at
columnar/vectorized speed instead of going round-trip through MySQL.

Architecture:
    MySQL (system of record, write path)
        |
        |  nightly export via export_to_duckdb()
        v
    DuckDB file (.cache/trader_analytics.duckdb)
        ^
        |  experiment scripts read from here
        |
    Read-only paths (assess_scores, simulator, MC variant sweeps)

Use:
    from database.duckdb_mirror import get_duck

    duck = get_duck()
    df = duck.execute('SELECT * FROM price_history WHERE symbol = ? AND date >= ?',
                       ['AAPL', '2024-01-01']).pl()  # Polars dataframe
"""
from __future__ import annotations
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / '.cache'
CACHE_DIR.mkdir(exist_ok=True)
DUCKDB_FILE = CACHE_DIR / 'trader_analytics.duckdb'


_TABLES_TO_MIRROR = [
    # (mysql_table, duckdb_table, optional column_filter)
    ('price_history', 'price_history', None),
    ('scores', 'scores', None),
    ('indicators', 'indicators', None),
    ('weekly_price_history', 'weekly_price_history', None),
    ('weekly_indicators', 'weekly_indicators', None),
    ('weekly_scores', 'weekly_scores', None),
    ('market_breadth', 'market_breadth', None),
    ('market_regime', 'market_regime', None),
    ('algorithm_versions', 'algorithm_versions', None),
    ('stocks', 'stocks', None),
    ('earnings_dates', 'earnings_dates', None),
]


def get_duck(read_only=True):
    """Return a DuckDB connection. Reads from the mirror file."""
    if not DUCKDB_FILE.exists():
        raise RuntimeError(f"DuckDB mirror not built. Run: python -m database.duckdb_mirror build")
    return duckdb.connect(str(DUCKDB_FILE), read_only=read_only)


def build(verbose=True):
    """Export MySQL tables to DuckDB via Parquet.

    Pulls each table in chunks via Peewee, writes to Parquet, then loads
    into DuckDB. Handles tables of any size.
    """
    from database.trader_database import DB
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet_dir = CACHE_DIR / 'parquet'
    parquet_dir.mkdir(exist_ok=True)

    # Drop and rebuild DuckDB file for clean state
    if DUCKDB_FILE.exists():
        DUCKDB_FILE.unlink()
    duck = duckdb.connect(str(DUCKDB_FILE))

    for mysql_t, duck_t, _ in _TABLES_TO_MIRROR:
        if verbose:
            print(f"  Exporting {mysql_t} -> {duck_t} ...", flush=True)
        # Try to pull all rows; for very large tables this could be split into chunks.
        try:
            cur = DB.execute_sql(f"SELECT COUNT(*) FROM {mysql_t}")
            n_total = cur.fetchone()[0]
        except Exception as e:
            print(f"    skip ({e})")
            continue
        if n_total == 0:
            if verbose:
                print(f"    (empty)")
            continue

        # Stream in chunks
        chunk_size = 200000
        offset = 0
        parquet_path = parquet_dir / f'{mysql_t}.parquet'

        # First chunk to seed schema
        cur = DB.execute_sql(f"SELECT * FROM {mysql_t} LIMIT {chunk_size}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if not rows:
            continue
        table = pa.table({c: [r[i] for r in rows] for i, c in enumerate(cols)})
        pq.write_table(table, parquet_path)
        offset = len(rows)

        while offset < n_total:
            cur = DB.execute_sql(f"SELECT * FROM {mysql_t} LIMIT {chunk_size} OFFSET {offset}")
            rows = cur.fetchall()
            if not rows:
                break
            table = pa.table({c: [r[i] for r in rows] for i, c in enumerate(cols)})
            # Append: read existing, concat, rewrite (pyarrow doesn't append)
            existing = pq.read_table(parquet_path)
            combined = pa.concat_tables([existing, table])
            pq.write_table(combined, parquet_path)
            offset += len(rows)
            if verbose and offset % (chunk_size * 5) == 0:
                print(f"    {offset:,}/{n_total:,}")

        # Load Parquet into DuckDB
        duck.execute(f"CREATE OR REPLACE TABLE {duck_t} AS SELECT * FROM read_parquet('{parquet_path}')")
        if verbose:
            n = duck.execute(f"SELECT COUNT(*) FROM {duck_t}").fetchone()[0]
            sz_mb = parquet_path.stat().st_size / 1e6
            print(f"    {n:,} rows ({sz_mb:.1f} MB Parquet)")

    # Index hints — DuckDB doesn't need user indexes (it auto-indexes via zonemaps),
    # but we can ATTACH the SQLite barrier_outcomes for unified queries.
    bo_path = CACHE_DIR / 'barrier_outcomes.db'
    if bo_path.exists():
        duck.execute(f"INSTALL sqlite; LOAD sqlite;")
        duck.execute(f"ATTACH '{bo_path}' AS bocache (TYPE sqlite, READ_ONLY);")
        if verbose:
            n = duck.execute("SELECT COUNT(*) FROM bocache.barrier_outcomes").fetchone()[0]
            print(f"  Attached SQLite barrier_outcomes: {n:,} rows")

    duck.close()
    if verbose:
        print(f"\nDuckDB mirror built at {DUCKDB_FILE}")


def stats():
    if not DUCKDB_FILE.exists():
        print("DuckDB mirror not built.")
        return
    duck = duckdb.connect(str(DUCKDB_FILE), read_only=True)
    tables = duck.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
    print(f"DuckDB mirror at {DUCKDB_FILE}")
    print(f"Size: {DUCKDB_FILE.stat().st_size / 1e6:.1f} MB")
    print(f"Tables: {len(tables)}")
    for (t,) in tables:
        n = duck.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n:,} rows")
    duck.close()


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'
    if cmd == 'build':
        build()
        stats()
    elif cmd == 'stats':
        stats()
    else:
        print(f"Unknown command: {cmd}. Use 'build' or 'stats'.")
