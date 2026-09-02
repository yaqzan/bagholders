"""
Size the candidate ingest scopes before committing to one. Read-only.

Scopes:
  ALL       every Sharadar SEP equity ticker (21,934)
  SP500     tickers that were EVER S&P 500 members (sp500.csv PIT membership)
  SP500+U   the above union today's live universe
  U         live universe only (control -- what we already have)

Reports ticker count, price-row count, and per-deep-window ticker counts for each, plus
current MySQL price_history/indicators sizes for a growth-factor comparison.

  python size_ingest_scopes.py
"""
import csv
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")

WINDOWS = {
    "ltcm_1998": ("1998-07-01", "1998-10-31"),
    "dotcom_crash_2000_2002": ("2000-03-01", "2002-10-31"),
    "gfc_crash_2007_2009": ("2007-10-01", "2009-03-31"),
    "2007_now": ("2007-01-01", "2026-12-31"),
}


def sp500_ever():
    z = zipfile.ZipFile(os.path.join(DATA, "sp500.csv.zip"))
    m = z.namelist()[0]
    with z.open(m) as f:
        rd = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
        s = {r["ticker"].upper() for r in rd if r.get("ticker")}
    z.close()
    return s


def main():
    sp = sp500_ever()
    from database.models.core import Stock
    from database.trader_database import DB
    universe = {s.symbol.upper() for s in Stock.select(Stock.symbol)}

    scopes = {
        "ALL": None,                       # None = no filter
        "SP500": sp,
        "SP500+U": sp | universe,
        "U": universe,
    }
    rows = Counter()
    tkrs = defaultdict(set)
    win_tkrs = {s: defaultdict(set) for s in scopes}
    first_year = {}

    z = zipfile.ZipFile(os.path.join(DATA, "stocks.csv.zip"))
    m = z.namelist()[0]
    with z.open(m) as f:
        f.readline()
        tail = b""
        while True:
            buf = f.read(1 << 24)
            if not buf:
                break
            buf = tail + buf
            lines = buf.split(b"\n")
            tail = lines.pop()
            for ln in lines:
                if not ln:
                    continue
                p = ln.split(b",", 2)
                t = p[0].decode()
                d = p[1].decode()
                for name, filt in scopes.items():
                    if filt is not None and t not in filt:
                        continue
                    rows[name] += 1
                    tkrs[name].add(t)
                    for w, (a, b) in WINDOWS.items():
                        if a <= d <= b:
                            win_tkrs[name][w].add(t)
                if t not in first_year or d < first_year[t]:
                    first_year[t] = d
    z.close()

    out = {"sp500_ever": len(sp), "live_universe": len(universe), "scopes": {}}
    for name in scopes:
        out["scopes"][name] = {
            "tickers_with_prices": len(tkrs[name]),
            "price_rows": rows[name],
            "window_tickers": {w: len(s) for w, s in sorted(win_tkrs[name].items())},
        }

    # Current DB size, for the growth factor.
    cur = DB.execute_sql("SELECT COUNT(*) FROM price_history")
    ph = cur.fetchone()[0]
    cur = DB.execute_sql("SELECT COUNT(*) FROM indicators")
    ind = cur.fetchone()[0]
    cur = DB.execute_sql("SELECT COUNT(*) FROM scores")
    sc = cur.fetchone()[0]
    out["current_db"] = {"price_history": ph, "indicators": ind, "scores": sc}
    for name in scopes:
        net_new = rows[name] - (rows["U"] if name != "U" else 0)
        out["scopes"][name]["price_history_growth_factor"] = round(
            (ph + max(net_new, 0)) / ph, 2) if ph else None

    print(json.dumps(out, indent=2))
    p = os.path.join(DATA, "INGEST_SCOPE_SIZING.json")
    with open(p, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
