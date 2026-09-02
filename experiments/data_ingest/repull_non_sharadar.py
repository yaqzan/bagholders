"""
Single-pass yfinance re-pull for the symbols Sharadar cannot supply.

Ten symbols are outside the Sharadar rebuild: four foreign listings a US-only vendor will
never carry (ENA.V, HPS-A.TO, PINV.TO, VNP.TO), and six that either have no Sharadar match
or FAILED identity validation against one (COL vs Rockwell Collins, HAR vs Harman — both
rejected on median return divergence, i.e. our stored series is not that company).

They still hold the pre-repair data, so they may carry the same convention splice we just
removed everywhere else. yfinance is the only source available, so the goal is not a
verified convention -- it is INTERNAL CONSISTENCY: delete and re-pull the whole series in
ONE call, so a single adjustment basis covers every bar. The defect we are repairing came
precisely from stitching pulls taken at different times.

PROVENANCE, stated rather than assumed: yfinance has no as-traded feed. `close_unadj` is
therefore written only for fresh bars (where adjusted == as-traded because no split or
dividend has intervened) and left NULL for history. These symbols are tagged
data_source='yfinance_single_pass' so nobody later reads a NULL close_unadj as "as-traded
unavailable in principle" rather than "this vendor cannot supply it".

  python repull_non_sharadar.py            # dry-run
  python repull_non_sharadar.py --commit
"""
import argparse
import json
import os
import sys
import time

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
REPORT = os.path.join(REPO, "experiments", "data_ingest", "REPULL_REPORT.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    from database.models.core import Stock
    from database.models.technical import PriceHistory, WeeklyPriceHistory
    from database.trader_database import DB
    import trader as trader_mod

    smap = set(json.load(open(os.path.join(DATA, "SYMBOL_MAP.json")))["map"])
    have = {r[0] for r in DB.execute_sql("SELECT DISTINCT symbol FROM price_history").fetchall()}
    targets = sorted(have - smap)
    print(f"{'COMMIT' if a.commit else 'DRY-RUN'}: {len(targets)} non-Sharadar symbols")
    print(f"  {targets}")

    client = trader_mod.Trader()   # trader.py:771

    out = {}
    for sym in targets:
        before = DB.execute_sql("SELECT COUNT(*), MIN(date), MAX(date) FROM price_history "
                                "WHERE symbol=%s", (sym,)).fetchone()
        rec = {"before_bars": before[0], "before_range": [str(before[1]), str(before[2])]}
        if a.commit:
            t0 = time.time()
            with DB.atomic():
                DB.execute_sql("DELETE FROM price_history WHERE symbol=%s", (sym,))
                DB.execute_sql("DELETE FROM weekly_price_history WHERE symbol=%s", (sym,))
                DB.execute_sql("DELETE FROM indicators WHERE symbol=%s", (sym,))
                DB.execute_sql("DELETE FROM weekly_indicators WHERE symbol=%s", (sym,))
            ok = client.pull_price_history(sym, period="max")
            after = DB.execute_sql("SELECT COUNT(*), MIN(date), MAX(date) FROM price_history "
                                   "WHERE symbol=%s", (sym,)).fetchone()
            rec.update(pulled=bool(ok), after_bars=after[0],
                       after_range=[str(after[1]), str(after[2])],
                       secs=round(time.time() - t0, 1))
            st = Stock.get_or_none(Stock.symbol == sym)
            if st is not None:
                st.data_source = "yfinance_single_pass"
                st.save()
            if after[0]:
                lo, hi = after[1], after[2]
                PriceHistory._refresh_weekly_aggregates(sym, lo, hi)
                st.calculate_indicators(full=True, silent=True)
                st.calculate_indicators(full=True, weekly=True, silent=True)
        out[sym] = rec
        print(f"  {sym:<10} {rec}", flush=True)

    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {REPORT}")
    if not a.commit:
        print("DRY-RUN -- nothing written.")


if __name__ == "__main__":
    main()
