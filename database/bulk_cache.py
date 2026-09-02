"""On-demand parquet cache for experiment bulk loads.

Formalizes the pattern already in `experiments/mcap_dampener/build_features.py`
and `experiments/weekly_avwap/build_features.py`: pull from MySQL once, cache
to parquet, sweep variants in-memory thereafter.

This is NOT a global snapshot of the production DB. Each experiment caches
exactly what it needs, where it needs it. Cached parquets are queryable via
both polars and DuckDB (which reads parquet natively at memory-mapped speed).

Why on-demand instead of a nightly global snapshot:
  - Score table is 30 GB; PriceHistory is similar. Snapshotting everything
    nightly costs minutes per cycle for data most experiments don't use.
  - Most experiments need a narrow slice (one version × specific date range
    × specific score range). Per-experiment caches are smaller and faster.
  - Stays out of the cron path → no during-day staleness risk on the
    production scoring loop.

Canonical usage in an experiment build_features.py:

    from database.bulk_cache import materialize_polars, chunked_query_by_year

    def _build():
        rows = chunked_query_by_year(
            "SELECT symbol, date, overall FROM scores "
            "WHERE version_id={vid} AND date BETWEEN '{y_start}' AND '{y_end}' "
            "AND overall >= 70",
            start_year=2016, end_year=2027, vid=46,
        )
        return pl.DataFrame(rows, schema=['symbol', 'date', 'overall'])

    parquet_path = materialize_polars('my_experiment_peaks_v46_10y', _build)
    # ... sweep variants now read from parquet_path via pl.read_parquet() or duckdb
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Callable, Iterable

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
EXP_CACHE = ROOT / '.cache' / 'experiment_data'
EXP_CACHE.mkdir(parents=True, exist_ok=True)


def cache_path(name: str) -> Path:
    """Resolve a logical name to a parquet path under .cache/experiment_data/."""
    if '/' in name or '\\' in name:
        raise ValueError(f"name must be a flat identifier, got {name!r}")
    return EXP_CACHE / f"{name}.parquet"


def is_fresh(path: Path, *deps: Path) -> bool:
    """True iff path exists and is at least as new as every dep that exists.

    Dep semantics: pass paths to upstream files whose change should invalidate
    the cache (e.g. .cache/barrier_outcomes.db, an ALGORITHM_VERSION file).
    Missing deps are ignored — only existing deps gate freshness.
    """
    if not path.exists():
        return False
    p_mtime = path.stat().st_mtime
    for d in deps:
        if d.exists() and d.stat().st_mtime > p_mtime:
            return False
    return True


def materialize_polars(
    name: str,
    build_fn: Callable[[], pl.DataFrame],
    deps: Iterable[Path] = (),
    force: bool = False,
    verbose: bool = True,
) -> Path:
    """Cache a polars-DataFrame-producing function as parquet.

    On cache hit (file exists and is at least as new as every dep), skip
    `build_fn` entirely. On miss, call `build_fn` and write its result.

    Returns the parquet path. Caller reads via `pl.read_parquet(path)` or
    `duckdb.read_parquet(str(path))` depending on how the sweep is structured.

    The cache is intentionally simple: one parquet per logical name. If you
    rerun an experiment with different parameters (different version, different
    lookback), give it a different name. Stale parquets must be deleted by the
    caller — we don't garbage-collect.
    """
    path = cache_path(name)
    deps = tuple(deps)
    if not force and is_fresh(path, *deps):
        if verbose:
            size_mb = path.stat().st_size / 1e6
            print(f"  [bulk_cache] {name}: hit ({size_mb:.1f} MB)", flush=True)
        return path
    if verbose:
        print(f"  [bulk_cache] {name}: building...", flush=True)
    t0 = time.perf_counter()
    df = build_fn()
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"build_fn must return polars.DataFrame, got {type(df).__name__}"
        )
    df.write_parquet(path, compression='snappy')
    if verbose:
        size_mb = path.stat().st_size / 1e6
        print(
            f"  [bulk_cache] {name}: built in {time.perf_counter()-t0:.1f}s "
            f"({size_mb:.1f} MB, {len(df):,} rows)",
            flush=True,
        )
    return path


def chunked_query_by_year(
    sql_template: str,
    start_year: int,
    end_year: int,
    timeout_ms: int = 300_000,
    verbose: bool = True,
    **fmt_kwargs,
) -> list[tuple]:
    """Run a year-chunked SQL query against MySQL to dodge MAX_EXECUTION_TIME.

    sql_template uses Python str.format() placeholders. Two are required:
      {y_start}: ISO date (YYYY-MM-DD) of chunk start
      {y_end}:   ISO date (YYYY-MM-DD) of chunk end (inclusive)
    Pass any other parameters via **fmt_kwargs (substituted on every chunk).

    Example:
        chunked_query_by_year(
            "SELECT symbol, date, overall FROM scores "
            "WHERE version_id={vid} AND date BETWEEN '{y_start}' AND '{y_end}'",
            start_year=2016, end_year=2027, vid=46,
        )
    """
    from database.trader_database import DB
    DB.execute_sql(f'SET SESSION MAX_EXECUTION_TIME={int(timeout_ms)}')
    rows: list[tuple] = []
    for yr in range(start_year, end_year):
        y_start = f'{yr}-01-01'
        y_end   = f'{yr}-12-31'
        sql = sql_template.format(y_start=y_start, y_end=y_end, **fmt_kwargs)
        t0 = time.perf_counter()
        cur = DB.execute_sql(sql)
        chunk = cur.fetchall()
        rows.extend(chunk)
        if verbose:
            print(
                f"  [bulk_cache] chunk {yr}: {len(chunk):,} rows "
                f"({time.perf_counter()-t0:.1f}s)",
                flush=True,
            )
    return rows
