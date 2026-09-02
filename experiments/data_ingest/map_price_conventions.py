"""
CONTAMINATION MAP -- which (symbol, year) cells of price_history are on which price
convention, measured against Sharadar as the external reference.

Motivation: pulled_at shows 84 distinct backfill seam dates, not one, so a fixed-cut test
understates the problem. Sharadar publishes both conventions for every bar, which lets us
classify our stored data per period instead of guessing:

  close     = split-adjusted, NO dividends
  closeadj  = split + dividend adjusted   (what yfinance auto_adjust=True gives us)

Per symbol per calendar year we take the median of our_close/closeadj and
our_close/close and label the cell:

  div_adj         our data is split+dividend adjusted   (the intended convention)
  split_adj_only  split-adjusted but dividends MISSING  (the deep-backfill defect, D1)
  scaled_xN       a constant factor N away from split-adjusted -> a split that was never
                  back-applied to stored bars (D2; NFLX x10, NOW x5)
  unknown         matches neither within tolerance

A symbol carrying >=2 distinct labels across its years is SPLICED: mixed conventions
inside one series, which is indefensible regardless of which convention one prefers.

Output: .cache/sharadar/CONVENTION_MAP.json  -- includes a per-year universe summary,
which is the input the analysis audit needs ("in year Y, what share of the universe was
on the wrong convention?").

Read-only.  python map_price_conventions.py
"""
import json
import os
import sys
from collections import defaultdict

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
PARQUET = os.path.join(DATA, "stocks_by_ticker.parquet")
OUT = os.path.join(DATA, "CONVENTION_MAP.json")

TOL = 0.01
MIN_DAYS = 40


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def classify(r_adj, r_close):
    if r_adj is not None and abs(r_adj - 1) < TOL:
        return "div_adj"
    if r_close is not None and abs(r_close - 1) < TOL:
        return "split_adj_only"
    if r_close:
        for n in (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50, 70, 100):
            if abs(r_close - n) / n < 0.02 or abs(r_close - 1.0 / n) * n < 0.02:
                return f"scaled_x{n}" if r_close > 1 else f"scaled_1_over_{n}"
    return "unknown"


def main():
    import polars as pl
    from database.models.core import Stock
    from database.trader_database import DB

    universe = sorted({s.symbol.upper() for s in Stock.select(Stock.symbol)})
    lf = pl.scan_parquet(PARQUET).select(["ticker", "date", "close", "closeadj"])

    per_symbol = {}
    year_counts = defaultdict(lambda: defaultdict(int))
    CH = 100
    for i in range(0, len(universe), CH):
        chunk = universe[i:i + CH]
        sh = lf.filter(pl.col("ticker").is_in(chunk)).collect()
        if sh.height == 0:
            continue
        s_by = defaultdict(dict)
        for t, d, c, ca in sh.iter_rows():
            s_by[t][d] = (c, ca)

        ph = ",".join(["%s"] * len(chunk))
        cur = DB.execute_sql(
            f"SELECT `symbol`, `date`, `close` FROM price_history WHERE `symbol` IN ({ph})",
            tuple(chunk))
        ours = defaultdict(list)
        for sym, d, c in cur.fetchall():
            if c is not None and float(c) > 0:
                ours[sym].append((d, float(c)))

        for t in chunk:
            if t not in s_by or t not in ours:
                continue
            by_year_adj = defaultdict(list)
            by_year_close = defaultdict(list)
            for d, oc in ours[t]:
                row = s_by[t].get(d)
                if not row:
                    continue
                c, ca = row
                y = d.year
                if ca:
                    by_year_adj[y].append(oc / ca)
                if c:
                    by_year_close[y].append(oc / c)
            years = {}
            for y in sorted(by_year_adj):
                if len(by_year_adj[y]) < MIN_DAYS:
                    continue
                ra = med(by_year_adj[y])
                rc = med(by_year_close.get(y, []))
                lab = classify(ra, rc)
                years[y] = {"label": lab,
                            "r_vs_closeadj": round(ra, 4) if ra else None,
                            "r_vs_close": round(rc, 4) if rc else None}
                year_counts[y][lab] += 1
            if not years:
                continue
            labels = {v["label"] for v in years.values()}
            per_symbol[t] = {
                "years": years,
                "labels": sorted(labels),
                "spliced": len(labels) > 1,
                "all_div_adj": labels == {"div_adj"},
            }
        print(f"  {min(i + CH, len(universe))}/{len(universe)}", flush=True)

    spliced = [t for t, r in per_symbol.items() if r["spliced"]]
    clean = [t for t, r in per_symbol.items() if r["all_div_adj"]]
    summary = {}
    for y in sorted(year_counts):
        tot = sum(year_counts[y].values())
        summary[y] = {
            "symbols": tot,
            "div_adj": year_counts[y].get("div_adj", 0),
            "pct_wrong_convention": round(
                100 * (tot - year_counts[y].get("div_adj", 0)) / tot, 1) if tot else 0,
            "labels": dict(sorted(year_counts[y].items(), key=lambda kv: -kv[1])),
        }

    out = {
        "judged_symbols": len(per_symbol),
        "clean_all_div_adj": len(clean),
        "spliced_mixed_convention": len(spliced),
        "spliced_pct": round(100 * len(spliced) / len(per_symbol), 1) if per_symbol else 0,
        "per_year_universe_summary": summary,
        "spliced_symbols": sorted(spliced),
        "per_symbol": per_symbol,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\njudged {len(per_symbol)} symbols")
    print(f"  clean (div_adj throughout): {len(clean)}")
    print(f"  MIXED convention (spliced): {len(spliced)} ({out['spliced_pct']}%)")
    print("\nper-year share of universe NOT on the intended div-adjusted convention:")
    print(f"  {'year':<6}{'symbols':>8}{'div_adj':>9}{'% wrong':>9}   labels")
    for y, s in summary.items():
        print(f"  {y:<6}{s['symbols']:>8}{s['div_adj']:>9}{s['pct_wrong_convention']:>8.1f}%   "
              f"{dict(list(s['labels'].items())[:3])}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
