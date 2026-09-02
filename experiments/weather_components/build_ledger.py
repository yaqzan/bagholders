"""Component-ensemble ledger for weatherization verification.

One row per stored v{active} stock-day with the 6 ensemble members
(trend, bb, rsi, macd, stoch, technical_alignment), the overall score, and the
trailing-momentum proxy. The apex CALL payoff outcome is joined later by
verify_components.py via the 30dte_apex barrier set (read-only).

Read-only: stored scores + bulk_cache. No re-score / MC / recalc. In-sample
(data end < holdout cutoff 2026-06-15).
"""
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
START_YEAR, END_YEAR = 2016, 2027


def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, "
        "technical_alignment, pct_from_ema50, pct_from_ema200 "
        "FROM scores WHERE version_id={vid} AND overall IS NOT NULL "
        "AND trend IS NOT NULL AND bb IS NOT NULL AND rsi IS NOT NULL "
        "AND macd IS NOT NULL AND stoch IS NOT NULL "
        "AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=START_YEAR, end_year=END_YEAR, vid=VID, timeout_ms=300_000,
    )

    def fnum(v):
        return float(v) if v is not None else float("nan")

    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date":   [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
            "trend":   [int(r[3]) for r in rows],
            "bb":      [int(r[4]) for r in rows],
            "rsi":     [int(r[5]) for r in rows],
            "macd":    [int(r[6]) for r in rows],
            "stoch":   [int(r[7]) for r in rows],
            "ta":      [int(r[8]) for r in rows],
            "pct50":   [fnum(r[9]) for r in rows],
            "pct200":  [fnum(r[10]) for r in rows],
        }
    )


if __name__ == "__main__":
    path = materialize_polars(f"weather_comp_v{VID}_{START_YEAR}", _build)
    df = pl.read_parquet(path)
    print(f"\nweather_comp_v{VID}_{START_YEAR}: {df.height:,} rows  -> {path}")
    print(df.select(["overall", "trend", "bb", "rsi", "macd", "stoch", "ta"]).describe())
