"""Build features parquet for dynamic TP/SL pre-flight.

For each (date, symbol, side) v46 peak in 5y holdout-locked window, capture:
  - overall, side ('low'=call, 'high'=put per assess_scores convention)
  - tier_alloc (0.20/0.15/0.10/0.10/0/0.12/0.10/0.08 per score bucket)
  - barrier outcome (result, exit_bars, fire_type) at 30dte_opt W=15
  - sigma_pct (entry-time vol)

Output: .cache/experiment_data/dynamic_tpsl_v46_5y.parquet
"""
from __future__ import annotations
import sys, time
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl
from database.bulk_cache import materialize_polars, chunked_query_by_year, cache_path
from database.barrier_cache import get_conn
from experiments._holdout import CUTOFF, pre_cutoff_filter

# Match active 30 DTE STRATEGY_30DTE.TIER_ALLOC + PUT_TIER_ALLOC
def tier_alloc_for_score(s: int) -> float:
    if s >= 95: return 0.20    # ultra
    if s >= 85: return 0.15    # top
    if s >= 80: return 0.10    # mid
    if s >= 75: return 0.10    # low
    if s >= 70: return 0.0     # overflow disabled
    # puts (s<=25)
    if s <= 15: return 0.12    # put_top
    if s <= 20: return 0.10    # put_mid
    if s <= 25: return 0.08    # put_low
    return 0.0

# Side per assess_scores: score>=50 -> 'low' (call: win=rise); score<50 -> 'high' (put: win=drop)
def side_for_score(s: int) -> str:
    return 'low' if s >= 50 else 'high'


def _build_peaks() -> pl.DataFrame:
    print(f"  pulling v46 peaks (5y, holdout cutoff {CUTOFF.isoformat()})", flush=True)
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall "
        "FROM scores WHERE version_id={vid} "
        "AND date BETWEEN '{y_start}' AND '{y_end}' "
        "AND (overall >= 75 OR overall <= 25)",
        start_year=2021, end_year=2027, vid=46,
    )
    df = pl.DataFrame(
        {'symbol': [r[0] for r in rows],
         'date':   [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]) for r in rows],
         'overall':[int(r[2]) for r in rows]},
        schema={'symbol': pl.Utf8, 'date': pl.Utf8, 'overall': pl.Int32},
    )
    df = pre_cutoff_filter(df)
    print(f"  v46 5y peaks pre-cutoff: {len(df):,}", flush=True)
    return df


def _build_outcomes(peak_dates: set[str]) -> pl.DataFrame:
    """Bulk pull barrier_outcomes for 30dte_opt W=15 from SQLite."""
    print(f"  pulling barrier_outcomes 30dte_opt W=15 from SQLite", flush=True)
    t0 = time.perf_counter()
    conn = get_conn()
    cur = conn.execute(
        "SELECT symbol, date, side, result, exit_bars, fire_type, sigma_pct, exit_return "
        "FROM barrier_outcomes "
        "WHERE barrier_set='30dte_opt' AND w_days=15"
    )
    rows = cur.fetchall()
    conn.close()
    df = pl.DataFrame(
        {'symbol': [r[0] for r in rows],
         'date':   [r[1] for r in rows],
         'side':   [r[2] for r in rows],
         'result': [r[3] for r in rows],
         'exit_bars': [r[4] for r in rows],
         'fire_type': [r[5] for r in rows],
         'sigma_pct': [r[6] for r in rows],
         'exit_return': [r[7] for r in rows]},
        schema={'symbol': pl.Utf8, 'date': pl.Utf8, 'side': pl.Utf8,
                'result': pl.Int32, 'exit_bars': pl.Int32, 'fire_type': pl.Int32,
                'sigma_pct': pl.Float64, 'exit_return': pl.Float64},
    )
    print(f"  outcomes loaded in {time.perf_counter()-t0:.1f}s: {len(df):,} rows", flush=True)
    return df


def build() -> pl.DataFrame:
    peaks = _build_peaks()
    peaks = peaks.with_columns(
        side=pl.col('overall').map_elements(side_for_score, return_dtype=pl.Utf8),
        tier_alloc=pl.col('overall').map_elements(tier_alloc_for_score, return_dtype=pl.Float64),
    )
    # Pull all outcomes once and join (avoids per-peak SQLite roundtrips)
    outcomes = _build_outcomes(set(peaks['date'].to_list()))
    df = peaks.join(outcomes, on=['symbol', 'date', 'side'], how='left')
    n_total = len(df)
    n_resolved = df.filter(pl.col('result').is_not_null()).height
    n_expire = df.filter(pl.col('result').is_null() & pl.col('fire_type').is_not_null()).height
    n_no_outcome = df.filter(pl.col('fire_type').is_null()).height
    print(f"  joined: {n_total:,} peaks ({n_resolved:,} resolved, "
          f"{n_expire:,} expired, {n_no_outcome:,} no outcome)", flush=True)
    return df


if __name__ == '__main__':
    path = materialize_polars('dynamic_tpsl_v46_5y', build)
    print(f"\nDONE: {path}")
    df = pl.read_parquet(path)
    print(f"Shape: {df.shape}")
    print("\nBy side:")
    print(df.group_by('side').agg(
        pl.len().alias('n'),
        pl.col('result').mean().alias('TP_rate'),
        pl.col('exit_bars').mean().alias('avg_exit_bars'),
    ))
