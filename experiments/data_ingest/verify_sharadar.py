"""
Verify the Sharadar pull actually delivers what P2.A bought it for:
survivorship-bias-free coverage across the dot-com and GFC windows.

Read-only. Streams the zipped CSVs in .cache/sharadar/ and does ONE light DB read
(the current Stock symbol list) to size the delisted/not-in-universe unlock.
Writes PULL_VERIFICATION.json next to the data.

  python verify_sharadar.py
"""
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")

# The windows the deep-crash screens actually run on (known-issues.md / deep_crash_screen).
WINDOWS = {
    "ltcm_1998": ("1998-07-01", "1998-10-31"),
    "dotcom_crash_2000_2002": ("2000-03-01", "2002-10-31"),
    "gfc_crash_2007_2009": ("2007-10-01", "2009-03-31"),
    "covid_2020": ("2020-02-01", "2020-04-30"),
}


def _open_member(name):
    z = zipfile.ZipFile(os.path.join(DATA, name))
    m = z.namelist()[0]
    f = z.open(m)
    header = f.readline().decode().rstrip("\r\n").split(",")
    return z, f, header


def scan_tickers():
    z, f, hdr = _open_member("tickers.csv.zip")
    i = {c: n for n, c in enumerate(hdr)}
    import csv
    import io
    rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
    tot = Counter()
    delisted_by_decade = Counter()
    equities = set()
    delisted_equities = set()
    for row in rd:
        if not row:
            continue
        tbl = row[i["table"]]
        tot[tbl] += 1
        if tbl != "SEP" and tbl.lower() != "stocks" and tbl.lower() != "sep":
            continue
        tkr = row[i["ticker"]]
        equities.add(tkr)
        if row[i["isdelisted"]] == "Y":
            delisted_equities.add(tkr)
            lpd = row[i["lastpricedate"]]
            if lpd:
                delisted_by_decade[lpd[:3] + "0s"] += 1
    z.close()
    return {
        "rows_by_table": dict(tot),
        "equity_tickers": len(equities),
        "equity_delisted": len(delisted_equities),
        "delisted_last_price_by_decade": dict(sorted(delisted_by_decade.items())),
    }, equities, delisted_equities


def scan_sp500():
    z, f, hdr = _open_member("sp500.csv.zip")
    i = {c: n for n, c in enumerate(hdr)}
    import csv
    import io
    rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
    actions = Counter()
    dates = []
    members = set()
    for row in rd:
        if not row:
            continue
        actions[row[i["action"]]] += 1
        dates.append(row[i["date"]])
        members.add(row[i["ticker"]])
    z.close()
    return {
        "action_counts": dict(actions),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "distinct_tickers_ever": len(members),
    }


def scan_actions():
    z, f, hdr = _open_member("actions.csv.zip")
    i = {c: n for n, c in enumerate(hdr)}
    import csv
    import io
    rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
    kinds = Counter()
    splits_by_decade = Counter()
    dates = []
    for row in rd:
        if not row:
            continue
        a = row[i["action"]]
        kinds[a] += 1
        d = row[i["date"]]
        dates.append(d)
        if a == "split":
            splits_by_decade[d[:3] + "0s"] += 1
    z.close()
    return {
        "action_counts": dict(kinds.most_common()),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "splits_by_decade": dict(sorted(splits_by_decade.items())),
    }


def scan_stocks():
    """Single streaming pass over ~46M rows. Byte-level split: the SEP schema is
    fixed-width in FIELD COUNT and contains no quoted commas."""
    z = zipfile.ZipFile(os.path.join(DATA, "stocks.csv.zip"))
    m = z.namelist()[0]
    rows_by_year = Counter()
    tickers_by_window = defaultdict(set)
    all_tickers = set()
    dmin, dmax = "9999-99-99", "0000-00-00"
    n = 0
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
                tkr = p[0].decode()
                d = p[1].decode()
                n += 1
                rows_by_year[d[:4]] += 1
                all_tickers.add(tkr)
                if d < dmin:
                    dmin = d
                if d > dmax:
                    dmax = d
                for w, (a, b) in WINDOWS.items():
                    if a <= d <= b:
                        tickers_by_window[w].add(tkr)
    z.close()
    return {
        "rows": n,
        "date_min": dmin,
        "date_max": dmax,
        "distinct_tickers": len(all_tickers),
        "rows_by_year": dict(sorted(rows_by_year.items())),
        "distinct_tickers_by_window": {w: len(s) for w, s in sorted(tickers_by_window.items())},
    }, all_tickers, tickers_by_window


def main():
    out = {}
    print("scanning tickers...", flush=True)
    out["tickers"], equities, delisted = scan_tickers()
    print(json.dumps(out["tickers"], indent=2))

    print("scanning sp500...", flush=True)
    out["sp500"] = scan_sp500()
    print(json.dumps(out["sp500"], indent=2))

    print("scanning actions...", flush=True)
    out["actions"] = scan_actions()
    print(json.dumps(out["actions"], indent=2))

    print("scanning stocks (~46M rows, one pass)...", flush=True)
    out["stocks"], all_tkr, by_window = scan_stocks()
    print(json.dumps(out["stocks"], indent=2))

    # The headline: how much of this is NEW relative to today's live universe.
    from database.models.core import Stock
    universe = {s.symbol.upper() for s in Stock.select(Stock.symbol)}
    out["universe_delta"] = {
        "live_universe": len(universe),
        "sharadar_price_tickers": len(all_tkr),
        "not_in_live_universe": len(all_tkr - universe),
        "live_universe_missing_from_sharadar": len(universe - all_tkr),
        "new_names_by_window": {
            w: len(s - universe) for w, s in sorted(by_window.items())
        },
        "window_coverage_live_vs_total": {
            w: {"total": len(s), "already_live": len(s & universe)}
            for w, s in sorted(by_window.items())
        },
    }
    print(json.dumps(out["universe_delta"], indent=2))

    p = os.path.join(DATA, "PULL_VERIFICATION.json")
    with open(p, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
