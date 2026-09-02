"""
One-time PRICE-ONLY pull for the live universe.

Deliberately not `trader update`. That command also scores, syncs the live Portfolio and
runs the alert pass, which can fire push notifications — unwanted on a day designated as a
maintenance/off day with the notify tasks guarded off. This pulls bars and nothing else.

Scoring is not skipped, just deferred: the 30y deep recalc that follows covers 1996->today,
so today's bar gets scored there on the final substrate rather than twice on two different
ones.

Uses a short period ('5d') so the write touches only recent dates — the deep history was
just rebuilt from Sharadar and must not be overwritten by a yfinance backfill. Only
non-delisted symbols are pulled; dead tickers have nothing new by definition, and
bulk_build filters post-delisting bars anyway.

  python pull_today_prices.py            # dry-run (counts only)
  python pull_today_prices.py --commit
"""
import argparse
import json
import os
import sys
import time

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
REPORT = os.path.join(REPO, "experiments", "data_ingest", "TODAY_PULL_REPORT.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--period", default="5d")
    a = ap.parse_args()

    from database.models.core import Stock
    from database.trader_database import DB
    import trader as trader_mod

    syms = sorted(s.symbol for s in
                  Stock.select(Stock.symbol).where(Stock.delisted_date.is_null()))
    before = DB.execute_sql("SELECT MAX(date), COUNT(*) FROM price_history").fetchone()
    print(f"live universe: {len(syms)} symbols   period={a.period}")
    print(f"before: max_date={before[0]}  rows={before[1]:,}")
    if not a.commit:
        print("DRY-RUN -- nothing pulled.")
        return

    t0 = time.time()
    client = trader_mod.Trader()
    ok, failed = client.pull_price_history_bulk([(s, a.period) for s in syms])
    # per-symbol retry is the documented follow-up for bulk failures
    retried = set()
    for s in sorted(failed):
        if client.pull_price_history(s, period=a.period):
            retried.add(s)

    after = DB.execute_sql("SELECT MAX(date), COUNT(*) FROM price_history").fetchone()
    today_rows = DB.execute_sql(
        "SELECT COUNT(*) FROM price_history WHERE date=CURDATE()").fetchone()[0]
    unadj = DB.execute_sql(
        "SELECT COUNT(*) FROM price_history WHERE date=CURDATE() "
        "AND close_unadj IS NOT NULL").fetchone()[0]
    out = {
        "symbols": len(syms), "period": a.period,
        "bulk_ok": len(ok), "bulk_failed": len(failed),
        "retry_recovered": len(retried),
        "still_failed": sorted(set(failed) - retried),
        "before": {"max_date": str(before[0]), "rows": before[1]},
        "after": {"max_date": str(after[0]), "rows": after[1]},
        "rows_dated_today": today_rows,
        "rows_today_with_close_unadj": unadj,
        "elapsed_minutes": round((time.time() - t0) / 60, 2),
    }
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    print(f"\nwrote {REPORT}")


if __name__ == "__main__":
    main()
