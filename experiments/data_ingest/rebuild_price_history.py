"""
P2.A -- rebuild price_history from Sharadar on ONE convention, end to end.

WHY A REBUILD AND NOT AN ADDITIVE INGEST
pulled_at shows 84 distinct backfill seams; measured against Sharadar, our stored series
switch conventions mid-stream (split-adjusted-only in deep-backfill segments, split+
dividend-adjusted in rolling-update segments, and a few names uniformly off by an
un-back-applied split factor). Mixed conventions inside one series cannot be repaired
additively -- the existing bars are the problem.

WHICH CONVENTION, AND WHY IT IS NOT A FREE CHOICE
We standardise on `closeadj` = split + DIVIDEND adjusted. Not because it is the best
convention for an options backtest -- it very likely is not, since strikes and payoffs
reference as-traded prices (see traps.md "price_history.close is SPLIT+DIVIDEND ADJUSTED;
option strikes are AS-TRADED") -- but because the LIVE path (`trader update` ->
yfinance auto_adjust=True) writes dividend-adjusted bars every day. Rebuilding to any
other convention would re-open a seam on the very next update. Moving the whole system to
as-traded is a separate, larger decision that must change the live pull first.

WHAT IT DOES, per in-scope symbol:
  1. snapshot existing rows to parquet (reversible -- this is destructive otherwise)
  2. DELETE the symbol's price_history rows
  3. INSERT Sharadar's full history, OHLC scaled by closeadj/close, close = closeadj,
     AND close_unadj = closeunadj (as-traded)
  4. weekly aggregates are NOT refreshed here -- the mandatory recompute chain does it

STORING BOTH CONVENTIONS (the reason there is a new column)
Sharadar publishes close (split-adjusted), closeadj (split+dividend) and closeunadj
(as-traded) for every bar, so the adjusted-vs-as-traded tension does not need a winner:
  price_history.close        split+dividend adjusted -> indicators/scoring. Scale-free
                             measures need continuity across splits; as-traded would show
                             a -90% "crash" on every split date.
  price_history.close_unadj  AS-TRADED -> option strike/moneyness/premium work, which
                             references the price actually printed.
This permanently closes traps.md "price_history.close is SPLIT+DIVIDEND ADJUSTED; option
strikes are AS-TRADED" (project_polygon_adjusted_close_bug: 31.4% of rows drift) without
a second rebuild + recompute later. For a 15-DTE horizon the dividend convention is worth
~0.08% per hold, so this column matters for STRIKE SELECTION, not for barrier labels.

Scope = S&P-500-ever UNION today's live universe, resolved to Sharadar tickers via
SYMBOL_MAP.json (which is price-validated -- string matching alone silently swaps
recycled tickers, e.g. Computer Associates -> a California muni-bond ETF).

Downstream (NOT done here): indicators, weekly indicators, scores, breadth, regime all
derive from these bars and must be recomputed.

  python rebuild_price_history.py                 # DRY-RUN
  python rebuild_price_history.py --commit
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
EQ_PARQUET = os.path.join(DATA, "stocks_by_ticker.parquet")
FUND_PARQUET = os.path.join(DATA, "funds_by_ticker.parquet")
SYMBOL_MAP = os.path.join(DATA, "SYMBOL_MAP.json")
BACKUP = os.path.join(DATA, "backup_price_history_pre_rebuild.parquet")
REPORT = os.path.join(REPO, "experiments", "data_ingest", "REBUILD_REPORT.json")
# small enough that one INSERT stays far inside the 30s client read_timeout
INSERT_CHUNK = 1000


def load_ticker_meta():
    """Sharadar ticker metadata for BOTH equities (SEP) and funds (SFP)."""
    import csv
    import io
    import zipfile
    z = zipfile.ZipFile(os.path.join(DATA, "tickers.csv.zip"))
    with z.open(z.namelist()[0]) as f:
        meta = {}
        for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace")):
            if r["table"] in ("SEP", "SFP"):
                meta.setdefault(r["ticker"].upper(), r)
    z.close()
    return meta


def _as_date(s):
    from datetime import date as _d
    if not s or len(s) < 10:
        return None
    try:
        return _d(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-backup", action="store_true")
    a = ap.parse_args()

    import polars as pl
    from database.models.core import Stock
    from database.models.technical import PriceHistory
    from database.trader_database import DB

    cur = DB.execute_sql("SHOW COLUMNS FROM price_history LIKE 'close_unadj'")
    if cur.fetchone() is None:
        print("adding price_history.close_unadj (DECIMAL, nullable) ...")
        if a.commit:
            DB.execute_sql("ALTER TABLE price_history ADD COLUMN close_unadj "
                           "DECIMAL(18,6) NULL")
    # price_history.symbol has an FK onto stocks.symbol, so every new S&P name needs its
    # stocks row BEFORE any bar is inserted (this is what failed task #151: the first
    # chunk hit "A" = Agilent, an S&P name we don't track, and the FK rejected it).
    for col, ddl in (("permaticker", "ALTER TABLE stocks ADD COLUMN permaticker INT NULL"),
                     ("data_source",
                      "ALTER TABLE stocks ADD COLUMN data_source VARCHAR(32) NULL")):
        cur = DB.execute_sql("SHOW COLUMNS FROM stocks LIKE %s", (col,))
        if cur.fetchone() is None:
            print(f"adding stocks.{col} ...")
            if a.commit:
                DB.execute_sql(ddl)

    if not os.path.exists(SYMBOL_MAP):
        sys.exit(f"missing {SYMBOL_MAP} -- run build_symbol_map.py first")
    smap = json.load(open(SYMBOL_MAP))["map"]     # our_symbol -> {ticker, table}
    targets = sorted(smap)
    if a.limit:
        targets = targets[:a.limit]

    eq = pl.scan_parquet(EQ_PARQUET)
    fu = pl.scan_parquet(FUND_PARQUET) if os.path.exists(FUND_PARQUET) else None

    # ---- 1. reversible snapshot of everything we are about to destroy ----------
    if a.commit and not a.skip_backup and not os.path.exists(BACKUP):
        print("snapshotting existing price_history for in-scope symbols ...", flush=True)
        rows = []
        CH = 200
        for i in range(0, len(targets), CH):
            ch = targets[i:i + CH]
            ph = ",".join(["%s"] * len(ch))
            cur = DB.execute_sql(
                f"SELECT `symbol`,`date`,`open`,`high`,`low`,`close`,`volume`,`pulled_at` "
                f"FROM price_history WHERE `symbol` IN ({ph})", tuple(ch))
            for r in cur.fetchall():
                rows.append((r[0], r[1],
                             float(r[2]) if r[2] is not None else None,
                             float(r[3]) if r[3] is not None else None,
                             float(r[4]) if r[4] is not None else None,
                             float(r[5]) if r[5] is not None else None,
                             int(r[6]) if r[6] is not None else None,
                             r[7]))
        pl.DataFrame(rows, schema=["symbol", "date", "open", "high", "low", "close",
                                   "volume", "pulled_at"],
                     orient="row").write_parquet(BACKUP)
        print(f"  backed up {len(rows):,} rows -> {BACKUP}")

    tmeta = load_ticker_meta()
    existing = {r[0] for r in DB.execute_sql("SELECT symbol FROM stocks").fetchall()}

    now = datetime.now()
    stats = defaultdict(int)
    per_symbol = {}
    t0 = time.time()
    CH = 50
    for i in range(0, len(targets), CH):
        chunk = targets[i:i + CH]
        want_eq = [s for s in chunk if smap[s]["table"] == "SEP"]
        want_fu = [s for s in chunk if smap[s]["table"] == "SFP"]

        src = defaultdict(list)
        if want_eq:
            tks = [smap[s]["ticker"] for s in want_eq]
            df = eq.filter(pl.col("ticker").is_in(tks)).select(
                ["ticker", "date", "open", "high", "low", "close", "volume",
                 "closeadj", "closeunadj"]).collect()
            back = {smap[s]["ticker"]: s for s in want_eq}
            for t, d, o, h, l, c, v, ca, cu in df.iter_rows():
                src[back[t]].append((d, o, h, l, c, v, ca, cu))
        if want_fu and fu is not None:
            tks = [smap[s]["ticker"] for s in want_fu]
            df = fu.filter(pl.col("ticker").is_in(tks)).select(
                ["ticker", "date", "open", "high", "low", "close", "volume",
                 "closeadj", "closeunadj"]).collect()
            back = {smap[s]["ticker"]: s for s in want_fu}
            for t, d, o, h, l, c, v, ca, cu in df.iter_rows():
                src[back[t]].append((d, o, h, l, c, v, ca, cu))

        # --- stocks rows first (FK parent) --------------------------------------
        for sym in chunk:
            if not src.get(sym):
                continue
            m = tmeta.get(smap[sym]["ticker"], {})
            if sym not in existing:
                stats["stocks_created"] += 1
                if (m.get("isdelisted") == "Y"):
                    stats["stocks_created_delisted"] += 1
                if a.commit:
                    st, _ = Stock.get_or_create(
                        symbol=sym, defaults={"name": (m.get("name") or sym)[:255]})
                    st.name = (m.get("name") or sym)[:255]
                    st.sector = m.get("sector") or None
                    st.industry = m.get("industry") or None
                    st.data_source = "sharadar"
                    try:
                        st.permaticker = int(m["permaticker"]) if m.get("permaticker") else None
                    except (ValueError, TypeError):
                        st.permaticker = None
                    if m.get("isdelisted") == "Y":
                        st.delisted_date = _as_date(m.get("lastpricedate"))
                    st.save()
                    existing.add(sym)
            else:
                # Names we already track: keep OUR name/sector, add provenance, and mark
                # the dead ones so core.py's effective-end-date paths drop them from LIVE
                # scoring (they have no recent bars anyway -- this just makes it explicit
                # and stops the live pull chasing them).
                stats["stocks_existing"] += 1
                if a.commit:
                    st = Stock.get_or_none(Stock.symbol == sym)
                    if st is not None:
                        st.data_source = "sharadar"
                        try:
                            st.permaticker = (int(m["permaticker"])
                                              if m.get("permaticker") else None)
                        except (ValueError, TypeError):
                            pass
                        if m.get("isdelisted") == "Y" and st.delisted_date is None:
                            st.delisted_date = _as_date(m.get("lastpricedate"))
                            stats["existing_marked_delisted"] += 1
                        st.save()

        for sym in chunk:
            bars = src.get(sym)
            if not bars:
                stats["symbols_no_source"] += 1
                continue
            bars.sort()
            rows = []
            for d, o, h, l, c, v, ca, cu in bars:
                if not c or not ca or c <= 0:
                    stats["bars_skipped"] += 1
                    continue
                f = ca / c
                rows.append(dict(date=d,
                                 open=(o * f) if o is not None else None,
                                 high=(h * f) if h is not None else None,
                                 low=(l * f) if l is not None else None,
                                 close=ca,
                                 close_unadj=cu,
                                 volume=int(v) if v is not None else 0))
            if not rows:
                stats["symbols_no_source"] += 1
                continue
            per_symbol[sym] = {"bars": len(rows),
                               "from": str(rows[0]["date"]), "to": str(rows[-1]["date"]),
                               "sharadar": smap[sym]["ticker"],
                               "table": smap[sym]["table"]}
            stats["symbols_rebuilt"] += 1
            stats["bars_to_write"] += len(rows)

            if a.commit:
                # Write path is deliberately NOT PriceHistory.bulk_build here.
                # bulk_build uses insert_many(...).on_conflict_replace() = REPLACE INTO,
                # which is DELETE+INSERT per row plus FK/index churn -- and we already
                # delete the symbol's rows first, so there is nothing to conflict with.
                # Task #153 died on MySQL 2013 (read_timeout=30s) because that REPLACE
                # plus a 5,000-row ON DUPLICATE KEY UPDATE for close_unadj ran inside one
                # transaction. Plain INSERTs in small chunks, with close_unadj carried in
                # the SAME statement, do the same work in one pass and keep every
                # statement well inside the timeout.
                cols = ("`symbol`,`date`,`open`,`high`,`low`,`close`,`close_unadj`,"
                        "`volume`,`pulled_at`")
                with DB.atomic():
                    DB.execute_sql("DELETE FROM price_history WHERE `symbol`=%s", (sym,))
                    for k in range(0, len(rows), INSERT_CHUNK):
                        batch = rows[k:k + INSERT_CHUNK]
                        vals = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(batch))
                        params = []
                        for r in batch:
                            params.extend((sym, r["date"], r["open"], r["high"], r["low"],
                                           r["close"], r["close_unadj"], r["volume"], now))
                        DB.execute_sql(
                            f"INSERT INTO price_history ({cols}) VALUES {vals}",
                            tuple(params))
                        stats["bars_written"] += len(batch)

        print(f"  {min(i + CH, len(targets))}/{len(targets)}  "
              f"bars={stats['bars_to_write']:,}  {(time.time() - t0) / 60:.1f}m", flush=True)

    out = {"mode": "commit" if a.commit else "dry-run",
           "convention": "closeadj (split+dividend), matching the live yfinance "
                         "auto_adjust=True path",
           "targets": len(targets),
           "stats": dict(stats),
           "backup": BACKUP if a.commit and not a.skip_backup else None,
           "elapsed_minutes": round((time.time() - t0) / 60, 2),
           "per_symbol": per_symbol}
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    print("\n" + json.dumps({k: v for k, v in out.items() if k != "per_symbol"}, indent=2))
    print(f"\nwrote {REPORT}")
    if not a.commit:
        print("DRY-RUN -- nothing written.")


if __name__ == "__main__":
    main()
