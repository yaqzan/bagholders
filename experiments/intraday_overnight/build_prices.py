"""Bulk OHLC pull (one DB round-trip) for the intraday-vs-overnight study.

Every existing price parquet in this repo pulls only CLOSE (+high/low); NONE has OPEN, which is
exactly what the overnight (prevclose->open) / intraday (open->close) decomposition needs. So pull
open/high/low/close for the full universe once, cache to parquet, and do all analysis in-memory.

db = heavy (full price_history scan) -> queue behind any running trader update.
"""
import os, sys, time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

import polars as pl
from database.trader_database import DB

OUT = os.path.join(_ROOT, ".cache", "intraday_overnight")
START = os.environ.get("IO_START", "2015-01-01")


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    sql = (
        "SELECT symbol, date, open, high, low, close, volume "
        "FROM price_history WHERE date >= '%s' AND open IS NOT NULL AND close IS NOT NULL "
        "ORDER BY symbol, date" % START
    )
    rows = DB.execute_sql(sql).fetchall()
    print("pulled price rows:", len(rows), "in %.1fs" % (time.time() - t0), flush=True)
    if not rows:
        print("NO ROWS"); return
    df = pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date":   [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
        "open":   [float(r[2]) for r in rows],
        "high":   [float(r[3]) if r[3] is not None else None for r in rows],
        "low":    [float(r[4]) if r[4] is not None else None for r in rows],
        "close":  [float(r[5]) for r in rows],
        "volume": [int(r[6]) if r[6] is not None else 0 for r in rows],
    })
    out = os.path.join(OUT, "ohlc.parquet")
    df.write_parquet(out)
    nsym = df["symbol"].n_unique()
    print("DONE %s N=%d symbols=%d range=%s..%s time=%.0fs"
          % (out, df.height, nsym, df["date"].min(), df["date"].max(), time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
