"""Build a full-history options-liquidity proxy + profile high-tier signal liquidity.

Proxy = trailing-30-bar average DAILY DOLLAR VOLUME of the underlying (close*volume from
PriceHistory, point-in-time). Options liquidity tracks underlying liquidity, so this is a
defensible full-history stand-in for "can I actually fill this option at scale?" (the
option_prices table only starts Feb-2025, so a direct option-volume filter can't cover the
backtest windows).

Outputs:
  - .cache parquet {symbol, date, liq30}  (consumed by monte_carlo LIQ_MIN_DV filter)
  - prints the 75+ call-signal liquidity distribution by tier + fraction below candidate floors
    (this answers "how concentrated is the edge in illiquid names?" and sets the sweep floors)

Run: PYTHONIOENCODING=utf-8 python -u experiments/fundability_recost/build_liq.py
"""
from __future__ import annotations
import sys, os
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from database.bulk_cache import materialize_polars, chunked_query_by_year  # noqa: E402

FLOORS = [5e6, 20e6, 50e6, 100e6, 250e6]  # candidate daily-$-volume floors


def _build_liq():
    rows = chunked_query_by_year(
        "SELECT symbol, date, close, volume FROM price_history "
        "WHERE date BETWEEN '{y_start}' AND '{y_end}' AND close IS NOT NULL AND volume IS NOT NULL",
        start_year=2015, end_year=2027,
    )
    df = pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date":   [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]) for r in rows],
        "close":  [float(r[2]) for r in rows],
        "volume": [float(r[3]) for r in rows],
    })
    df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    df = df.with_columns((pl.col("close") * pl.col("volume")).alias("dv")).sort(["symbol", "date"])
    df = df.with_columns(
        pl.col("dv").rolling_mean(window_size=30, min_periods=10).over("symbol").alias("liq30")
    )
    return df.select(["symbol", "date", "liq30"]).drop_nulls("liq30")


def main():
    path = materialize_polars("fundability_liq_dollar_vol_30d", _build_liq)
    L = pl.read_parquet(path)
    print(f"liq map: {L.height:,} (symbol,date) rows; {L['symbol'].n_unique()} symbols "
          f"({L['date'].min()}..{L['date'].max()})", flush=True)

    # --- profile: 75+ call signals' liquidity by tier (active scoring version) ---
    from database.models.core import AlgorithmVersion
    vid = AlgorithmVersion.get_active_scores_version().id
    print(f"active version id={vid}", flush=True)
    srows = chunked_query_by_year(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id={vid} AND overall >= 75 AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=2016, end_year=2027, vid=vid,
    )
    S = pl.DataFrame({
        "symbol":  [r[0] for r in srows],
        "date":    [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]) for r in srows],
        "overall": [int(r[2]) for r in srows],
    }).with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    S = S.with_columns(
        pl.when(pl.col("overall") >= 95).then(pl.lit("95+_ultra"))
          .when(pl.col("overall") >= 85).then(pl.lit("85-94_top"))
          .when(pl.col("overall") >= 80).then(pl.lit("80-84_mid"))
          .otherwise(pl.lit("75-79_low")).alias("tier")
    )
    J = S.join(L, on=["symbol", "date"], how="left")
    miss = J["liq30"].null_count()
    print(f"\n75+ call signals: {J.height:,}  (liq missing: {miss})", flush=True)
    print(f"{'tier':12s} {'n':>7s} {'p10$M':>8s} {'p25$M':>8s} {'med$M':>9s} "
          + " ".join(f'<{int(f/1e6)}M' for f in FLOORS), flush=True)
    for tier in ["95+_ultra", "85-94_top", "80-84_mid", "75-79_low", "ALL75+"]:
        sub = J if tier == "ALL75+" else J.filter(pl.col("tier") == tier)
        liq = sub["liq30"].drop_nulls()
        if liq.len() == 0:
            continue
        fr = [f"{100.0*(liq < f).sum()/liq.len():4.0f}%" for f in FLOORS]
        print(f"{tier:12s} {sub.height:>7d} {liq.quantile(0.10)/1e6:>8.1f} {liq.quantile(0.25)/1e6:>8.1f} "
              f"{liq.median()/1e6:>9.1f} " + " ".join(f'{x:>5s}' for x in fr), flush=True)
    print("\n(=='<NM' = fraction of that tier's signals on names trading < $N M/day -> illiquid options)", flush=True)


if __name__ == "__main__":
    main()
