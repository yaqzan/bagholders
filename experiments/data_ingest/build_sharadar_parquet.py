"""
Convert the Sharadar stocks bulk CSV into a ticker-sorted parquet.

`stocks.csv` is DATE-major (every consecutive row is a different ticker), so a per-ticker
ingest cannot stream it directly. This makes one sorted pass via DuckDB (spills to disk,
bounded memory) and leaves a compact parquet that both the reconciliation gate and the
ingest read by ticker group.

Also emits the adjustment factor per row: our price_history is yfinance auto_adjust=True
(split+dividend), Sharadar `close` is split-only and `closeadj` is split+dividend, so
adj_factor = closeadj/close scales OHLC into our convention.

  python build_sharadar_parquet.py             # keeps the 8.5GB temp CSV
  python build_sharadar_parquet.py --cleanup   # deletes it after
"""
import argparse
import os
import shutil
import sys
import time
import zipfile

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
TABLE = os.environ.get("SHARADAR_TABLE", "stocks")
ZIP = os.path.join(DATA, f"{TABLE}.csv.zip")
CSV = os.path.join(DATA, f"{TABLE}.csv")
PARQUET = os.path.join(DATA, f"{TABLE}_by_ticker.parquet")


def unzip():
    if os.path.exists(CSV):
        print(f"csv already extracted ({os.path.getsize(CSV) / 1e9:.1f} GB)")
        return
    t0 = time.time()
    print(f"extracting {TABLE}.csv ...", flush=True)
    with zipfile.ZipFile(ZIP) as z, z.open(z.namelist()[0]) as src, open(CSV, "wb") as dst:
        shutil.copyfileobj(src, dst, length=1 << 24)
    print(f"  {os.path.getsize(CSV) / 1e9:.1f} GB in {time.time() - t0:.0f}s")


def build():
    import duckdb
    t0 = time.time()
    con = duckdb.connect()
    con.execute("PRAGMA threads=6")
    con.execute("PRAGMA memory_limit='8GB'")
    con.execute(f"PRAGMA temp_directory='{os.path.join(DATA, 'duckdb_tmp')}'")
    print("sorting -> parquet ...", flush=True)
    con.execute(f"""
        COPY (
            SELECT
                ticker,
                CAST(date AS DATE)          AS date,
                open, high, low, close, volume,
                closeadj, closeunadj,
                CASE WHEN close > 0 THEN closeadj / close ELSE 1.0 END AS adj_factor
            FROM read_csv_auto('{CSV.replace(chr(92), '/')}', header=True)
            ORDER BY ticker, date
        ) TO '{PARQUET.replace(chr(92), '/')}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 200000)
    """)
    n = con.execute(f"SELECT COUNT(*) FROM '{PARQUET.replace(chr(92), '/')}'").fetchone()[0]
    con.close()
    print(f"  {n:,} rows -> {PARQUET} ({os.path.getsize(PARQUET) / 1e9:.2f} GB) "
          f"in {(time.time() - t0) / 60:.1f} min")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true",
                    help="delete the extracted CSV after the parquet is built")
    a = ap.parse_args()
    unzip()
    build()
    if a.cleanup and os.path.exists(CSV):
        os.remove(CSV)
        print("removed temp CSV")
    tmp = os.path.join(DATA, "duckdb_tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
