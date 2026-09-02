"""Data backbone for the verify_value initiative. Read-only, year-chunked pulls.

Builds (idempotent, cached under .cache/experiment_data/):
  - vv_v74_70plus    : v74 70+ cohort w/ overall + 5 components + ta
                       (Phase 1 band decomposition + Phase 2 components-logistic)
  - vv_v{70,71,73}_70plus : each lineage member's 70+ overall (Phase 2 ensemble)

v72 is intentionally excluded: it's the WCF score-gate ramp, per-trade NEUTRAL vs
v71 on every tradable bucket -> ~duplicate ensemble member (correlated error,
no diversification). Lineage members = {v70, v71, v73, v74}.

Apex outcomes are NOT pre-cached here: peaks_to_swing_results reads the
barrier_outcomes cache directly in the analysis scripts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import polars as pl
from database.bulk_cache import materialize_polars, chunked_query_by_year

START_YEAR, END_YEAR = 2016, 2027


def _build_components(vid):
    def _b():
        rows = chunked_query_by_year(
            "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, technical_alignment "
            "FROM scores WHERE version_id={vid} AND overall>=70 "
            "AND date BETWEEN '{y_start}' AND '{y_end}'",
            start_year=START_YEAR, end_year=END_YEAR, vid=vid, timeout_ms=290_000,
        )
        return pl.DataFrame({
            "symbol":  [r[0] for r in rows],
            "date":    [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
            "trend":   [float(r[3]) for r in rows],
            "bb":      [float(r[4]) for r in rows],
            "rsi":     [float(r[5]) for r in rows],
            "macd":    [float(r[6]) for r in rows],
            "stoch":   [float(r[7]) for r in rows],
            "ta":      [float(r[8]) for r in rows],
        })
    return materialize_polars(f"vv_v{vid}_70plus", _b)


def _build_overall(vid):
    def _b():
        rows = chunked_query_by_year(
            "SELECT symbol, date, overall FROM scores "
            "WHERE version_id={vid} AND overall>=70 AND date BETWEEN '{y_start}' AND '{y_end}'",
            start_year=START_YEAR, end_year=END_YEAR, vid=vid, timeout_ms=290_000,
        )
        return pl.DataFrame({
            "symbol":  [r[0] for r in rows],
            "date":    [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            f"o{vid}": [int(r[2]) for r in rows],
        })
    return materialize_polars(f"vv_v{vid}_70plus_overall", _b)


def main():
    print("=== building v74 70+ components cohort ===", flush=True)
    p = _build_components(74)
    print(f"  -> {p}", flush=True)
    for vid in (70, 71, 73):
        print(f"=== building v{vid} 70+ overall ===", flush=True)
        p = _build_overall(vid)
        print(f"  -> {p}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
