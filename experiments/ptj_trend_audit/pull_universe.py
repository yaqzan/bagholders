#! python3
"""Pull the survivorship-honest universe (ALL stocks incl delisted) 1997+ -> parquet.

Output: .cache/ptj_trend_audit/universe_1997.parquet
Schema: [symbol Utf8, date Date, close Float64, volume Float64?, delisted_date Date?]
Run via queue: db-heavy (chunked full price_history scan). Idempotent (skips if exists).
"""
import sys

_ROOT = r"C:\Development\Trader"
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

from pathlib import Path

import polars as pl

OUT = Path(_ROOT) / ".cache" / "ptj_trend_audit"
OUT.mkdir(parents=True, exist_ok=True)
DEST = OUT / "universe_1997.parquet"


def main():
    if DEST.exists():
        print("exists, skipping:", DEST)
        return
    from database.bulk_cache import chunked_query_by_year

    print("pulling ALL-stock closes from price_history (chunked by year, incl delisted)...")
    rows = chunked_query_by_year(
        "SELECT ph.symbol, ph.date, ph.close, ph.volume, s.delisted_date "
        "FROM price_history ph JOIN stocks s ON s.symbol = ph.symbol "
        "WHERE ph.close > 0 AND ph.date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=1997, end_year=2027,
    )
    print("pulled", len(rows), "rows")

    def _d(v):
        if v is None:
            return None
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [_d(r[1]) for r in rows],
            "close": [float(r[2]) for r in rows],
            "volume": [float(r[3]) if r[3] is not None else None for r in rows],
            "delisted_date": [_d(r[4]) for r in rows],
        },
        infer_schema_length=None,
    )
    df = df.with_columns(
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        pl.col("delisted_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
    ).sort(["symbol", "date"])
    df.write_parquet(DEST)
    n_syms = df.select(pl.col("symbol").n_unique()).item()
    n_del = (
        df.filter(pl.col("delisted_date").is_not_null())
        .select(pl.col("symbol").n_unique())
        .item()
    )
    print(
        "SUMMARY syms=%d delisted_syms=%d rows=%d dmin=%s dmax=%s"
        % (
            n_syms,
            n_del,
            df.height,
            df.select(pl.col("date").min()).item(),
            df.select(pl.col("date").max()).item(),
        )
    )
    print("wrote", DEST)


if __name__ == "__main__":
    main()
