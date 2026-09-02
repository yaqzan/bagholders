"""
De-duplicate SYMBOL_MAP: one Sharadar ticker must map to exactly ONE of our symbols.

THE BUG. 12 Sharadar tickers were claimed by two of our symbols each -- an old ticker and
its successor for the same company (ACE/CB = Chubb, DD/DWDP = DuPont, DPS/KDP = Keurig Dr
Pepper, SATS/ECHO = EchoStar, WYN/TNL = Travel+Leisure, ESV/VALPQ = Valaris, plus five
delisted pairs). The rebuild's reverse lookup {ticker: symbol} is one-to-one, so behaviour
depended on chunk layout:
  * same chunk  -> the later symbol overwrote the earlier; only one got bars (5 pairs)
  * split chunks-> BOTH got the identical bars (7 pairs) = the same company counted twice

Double-counting one company inflates breadth, distorts allocation, and lets a backtest hold
"two" positions in one business. That is worse than the missing bars, and it is live now.

CANONICAL CHOICE, in order:
  1. has score history   -- the symbol the system already tracks and the portfolio references
  2. not delisted        -- prefer the surviving/current ticker over a dead one
  3. in the pre-Sharadar universe -- continuity with existing rows
  4. equals the Sharadar ticker   -- last-resort tie-break

The loser is dropped from the map, its (duplicate) bars are deleted, and it is marked
delisted so it leaves live scoring. Its stocks row is kept, not deleted: historic scores
and portfolio rows may still reference it, and silently removing a symbol that appears in
past evidence is its own kind of corruption.

  python dedupe_symbol_map.py            # report only
  python dedupe_symbol_map.py --commit
"""
import argparse
import json
import os
import sys
from collections import defaultdict

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
SYMBOL_MAP = os.path.join(DATA, "SYMBOL_MAP.json")
OUT = os.path.join(REPO, "experiments", "data_ingest", "DEDUPE_REPORT.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    from database.models.core import Stock
    from database.trader_database import DB
    import polars as pl

    blob = json.load(open(SYMBOL_MAP))
    smap = blob["map"]

    backup = os.path.join(DATA, "backup_price_history_pre_rebuild.parquet")
    pre_universe = set()
    if os.path.exists(backup):
        pre_universe = set(pl.read_parquet(backup, columns=["symbol"])["symbol"].unique())

    claims = defaultdict(list)
    for sym, v in smap.items():
        claims[(v["ticker"], v["table"])].append(sym)
    dups = {t: ss for t, ss in claims.items() if len(ss) > 1}

    def score_count(s):
        return DB.execute_sql("SELECT COUNT(*) FROM scores WHERE symbol=%s",
                              (s,)).fetchone()[0]

    decisions = []
    losers = []
    for (tkr, tbl), syms in sorted(dups.items()):
        ranked = []
        for s in syms:
            st = Stock.get_or_none(Stock.symbol == s)
            ranked.append((
                -score_count(s),                       # 1. has scores
                1 if (st and st.delisted_date) else 0,  # 2. prefer not delisted
                0 if s in pre_universe else 1,          # 3. pre-Sharadar universe
                0 if s == tkr else 1,                   # 4. equals sharadar ticker
                s,
            ))
        ranked.sort()
        winner = ranked[0][-1]
        lost = [r[-1] for r in ranked[1:]]
        losers.extend(lost)
        decisions.append({"sharadar_ticker": tkr, "table": tbl, "claimants": syms,
                          "canonical": winner, "dropped": lost,
                          "why": {s: {"scores": -r[0], "delisted": bool(r[1]),
                                      "in_pre_universe": r[2] == 0}
                                  for r, s in ((r, r[-1]) for r in ranked)}})

    removed_bars = {}
    if a.commit:
        for s in losers:
            n = DB.execute_sql("SELECT COUNT(*) FROM price_history WHERE symbol=%s",
                               (s,)).fetchone()[0]
            if n:
                DB.execute_sql("DELETE FROM price_history WHERE symbol=%s", (s,))
                DB.execute_sql("DELETE FROM weekly_price_history WHERE symbol=%s", (s,))
                DB.execute_sql("DELETE FROM indicators WHERE symbol=%s", (s,))
                DB.execute_sql("DELETE FROM weekly_indicators WHERE symbol=%s", (s,))
            removed_bars[s] = n
            st = Stock.get_or_none(Stock.symbol == s)
            if st is not None and st.delisted_date is None:
                # an alias of a company represented elsewhere: keep the row, keep it out
                # of the live universe
                from datetime import date
                st.delisted_date = date(1900, 1, 1)
                st.data_source = "alias_deduped"
                st.save()
            smap.pop(s, None)
        blob["map"] = smap
        blob["deduped"] = {"dropped": losers}
        with open(SYMBOL_MAP, "w") as f:
            json.dump(blob, f, indent=2)

    rep = {"duplicate_tickers": len(dups), "dropped_symbols": sorted(losers),
           "map_size_after": len(smap), "removed_bars": removed_bars,
           "decisions": decisions}
    with open(OUT, "w") as f:
        json.dump(rep, f, indent=2)

    print(f"duplicate Sharadar tickers: {len(dups)}")
    print(f"{'ticker':<9}{'canonical':<11}{'dropped':<28}")
    for d in decisions:
        print(f"  {d['sharadar_ticker']:<9}{d['canonical']:<11}{','.join(d['dropped'])}")
    print(f"\nmap size after: {len(smap)}")
    if a.commit:
        tot = sum(removed_bars.values())
        print(f"deleted {tot:,} duplicate bars across {len(losers)} alias symbols")
    else:
        print("REPORT ONLY -- re-run with --commit")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
