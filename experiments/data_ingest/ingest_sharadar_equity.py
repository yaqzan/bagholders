"""
P2.A STEP 3 -- Sharadar delisted-equity ingest into our existing schema.

Supersedes the ingest_delisted_equity.py SCAFFOLD (that file assumed a Nasdaq-Data-Link
CSV export and an unverified column map; this one runs off the verified pull).

WHAT IT DOES
  scope   = tickers EVER in the S&P 500 (sp500.csv PIT membership) UNION today's live
            universe, intersected with Sharadar SEP metadata  -> 1,567 tickers.
  prices  = writes into `price_history` via PriceHistory.bulk_build (our table, our
            columns -- nothing new is created for prices).
  stocks  = creates `stocks` rows for names we don't track; sets `delisted_date` on the
            dead ones so core.py's four effective-end-date paths exclude them from LIVE
            scoring while keeping them in historical backtests.

ADDITIVE FOR NAMES WE ALREADY HAVE. For the 824 overlapping tickers only dates we do NOT
already hold are written, so every existing bar -- and therefore every previously computed
score and backtest number -- is left bit-identical. `--overwrite-existing` opts out.

NEW COLUMNS (nullable, additive, no behaviour change; created if absent):
  stocks.permaticker   INT          Sharadar's stable company id. Ticker strings get
                                    RECYCLED (2,857 SEP tickers carry a numeric suffix
                                    because the string was reused -- e.g. A1 = AstraZeneca
                                    AB, dead 1999, vs A = Agilent, live). Without a stable
                                    id we cannot re-join to Sharadar or audit a ticker
                                    change chain later.
  stocks.data_source   VARCHAR(32)  provenance ('sharadar' for rows this ingest created),
                                    so a later audit can tell vendor-sourced names from
                                    yfinance-sourced ones.

Adjustment: Sharadar `close` is split-only, `closeadj` is split+dividend, `closeunadj` is
as-traded. Our price_history is yfinance auto_adjust=True (split+dividend), so OHLC is
scaled by adj_factor = closeadj/close and close becomes closeadj. Verified AAPL 2000-01-03.

  python ingest_sharadar_equity.py                 # DRY-RUN (default)
  python ingest_sharadar_equity.py --commit
  python ingest_sharadar_equity.py --commit --scope all
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import zipfile
from collections import defaultdict
from datetime import date as _date

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
PARQUET = os.path.join(DATA, "stocks_by_ticker.parquet")
REPORT = os.path.join(REPO, "experiments", "data_ingest", "INGEST_REPORT.json")

DELISTED_GRACE_DAYS = 10


def ensure_columns(commit):
    """Additive, nullable columns -- same SHOW COLUMNS / ALTER TABLE pattern as
    Stock.ensure_schema()."""
    from database.trader_database import DB
    wanted = {
        "permaticker": "ALTER TABLE stocks ADD COLUMN permaticker INT NULL",
        "data_source": "ALTER TABLE stocks ADD COLUMN data_source VARCHAR(32) NULL",
    }
    added = []
    for col, ddl in wanted.items():
        cur = DB.execute_sql("SHOW COLUMNS FROM stocks LIKE %s", (col,))
        if cur.fetchone() is None:
            if commit:
                DB.execute_sql(ddl)
            added.append(col)
    return added


def load_meta():
    z = zipfile.ZipFile(os.path.join(DATA, "tickers.csv.zip"))
    with z.open(z.namelist()[0]) as f:
        meta = {r["ticker"].upper(): r
                for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8",
                                                         errors="replace"))
                if r["table"] == "SEP"}
    z.close()
    return meta


def load_sp500():
    z = zipfile.ZipFile(os.path.join(DATA, "sp500.csv.zip"))
    with z.open(z.namelist()[0]) as f:
        s = {r["ticker"].upper()
             for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8",
                                                      errors="replace"))
             if r.get("ticker")}
    z.close()
    return s


def _to_date(s):
    if not s:
        return None
    return _date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--scope", default="sp500+universe",
                    choices=["sp500+universe", "sp500", "all"])
    ap.add_argument("--overwrite-existing", action="store_true",
                    help="also rewrite bars we already hold (default: additive only)")
    ap.add_argument("--limit", type=int, default=0, help="first N tickers (smoke test)")
    a = ap.parse_args()

    import polars as pl
    from database.models.core import Stock
    from database.models.technical import PriceHistory
    from database.trader_database import DB

    meta = load_meta()
    universe = {s.symbol.upper() for s in Stock.select(Stock.symbol)}
    if a.scope == "all":
        scope = set(meta)
    elif a.scope == "sp500":
        scope = load_sp500() & set(meta)
    else:
        scope = (load_sp500() | universe) & set(meta)
    targets = sorted(scope)
    if a.limit:
        targets = targets[:a.limit]

    added_cols = ensure_columns(a.commit)
    print(f"{'COMMIT' if a.commit else 'DRY-RUN'}  scope={a.scope}  tickers={len(targets):,}")
    print(f"new columns {'added' if a.commit else 'WOULD add'}: {added_cols or 'none needed'}")

    file_max = _date(2026, 7, 28)  # stocks.csv max date, from the pull manifest
    lf = pl.scan_parquet(PARQUET).select(
        ["ticker", "date", "open", "high", "low", "close", "volume", "adj_factor"])

    stats = defaultdict(int)
    new_stock_rows, new_delisted = [], []
    t0 = time.time()
    CH = 50
    for i in range(0, len(targets), CH):
        chunk = targets[i:i + CH]
        df = lf.filter(pl.col("ticker").is_in(chunk)).collect()
        if df.height == 0:
            stats["tickers_no_prices"] += len(chunk)
            continue

        # dates we already hold, so the write stays additive
        existing = defaultdict(set)
        if not a.overwrite_existing:
            ph = ",".join(["%s"] * len(chunk))
            cur = DB.execute_sql(
                f"SELECT `symbol`, `date` FROM price_history WHERE `symbol` IN ({ph})",
                tuple(chunk))
            for sym, d in cur.fetchall():
                existing[sym].add(d)

        by_tkr = defaultdict(list)
        for t, d, o, h, l, c, v, fac in df.iter_rows():
            by_tkr[t].append((d, o, h, l, c, v, fac))

        for t in chunk:
            bars = by_tkr.get(t)
            if not bars:
                stats["tickers_no_prices"] += 1
                continue
            bars.sort()
            m = meta[t]
            last_bar = bars[-1][0]
            is_delisted = (m.get("isdelisted") == "Y"
                           or (file_max - last_bar).days > DELISTED_GRACE_DAYS)

            if t not in universe:
                stats["stocks_new"] += 1
                new_stock_rows.append(t)
                if is_delisted:
                    stats["stocks_new_delisted"] += 1
                    new_delisted.append(t)
                if a.commit:
                    st, _ = Stock.get_or_create(symbol=t, defaults={
                        "name": (m.get("name") or t)[:255]})
                    st.name = (m.get("name") or t)[:255]
                    st.sector = (m.get("sector") or None)
                    st.industry = (m.get("industry") or None)
                    try:
                        st.permaticker = int(m["permaticker"]) if m.get("permaticker") else None
                    except (ValueError, TypeError):
                        st.permaticker = None
                    st.data_source = "sharadar"
                    if is_delisted:
                        st.delisted_date = _to_date(m.get("lastpricedate")) or last_bar
                    st.save()
            else:
                stats["stocks_existing"] += 1

            have = existing.get(t, set())
            rows = []
            for d, o, h, l, c, v, fac in bars:
                if d in have:
                    stats["bars_skipped_already_held"] += 1
                    continue
                if c is None or fac is None:
                    stats["bars_skipped_null"] += 1
                    continue
                f = float(fac)
                rows.append(dict(date=d,
                                 open=(float(o) * f) if o is not None else None,
                                 high=(float(h) * f) if h is not None else None,
                                 low=(float(l) * f) if l is not None else None,
                                 close=float(c) * f,
                                 volume=int(v) if v is not None else 0))
            stats["bars_to_write"] += len(rows)
            if rows and a.commit:
                n_daily, n_weekly = PriceHistory.bulk_build(t, rows, refresh_weekly=True)
                stats["bars_written"] += n_daily
                stats["weekly_written"] += n_weekly

        done = min(i + CH, len(targets))
        el = time.time() - t0
        print(f"  {done}/{len(targets)}  bars_to_write={stats['bars_to_write']:,}  "
              f"{el / 60:.1f}m", flush=True)

    out = {
        "mode": "commit" if a.commit else "dry-run",
        "scope": a.scope,
        "targets": len(targets),
        "columns_added": added_cols,
        "overwrite_existing": a.overwrite_existing,
        "stats": dict(stats),
        "new_stock_rows_sample": new_stock_rows[:40],
        "new_delisted_sample": new_delisted[:40],
        "elapsed_minutes": round((time.time() - t0) / 60, 2),
    }
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\n" + json.dumps({k: v for k, v in out.items()
                             if k not in ("new_stock_rows_sample", "new_delisted_sample")},
                            indent=2))
    print(f"\nwrote {REPORT}")
    if not a.commit:
        print("DRY-RUN -- nothing written. Re-run with --commit.")


if __name__ == "__main__":
    main()
