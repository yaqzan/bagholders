"""
Rebuild weekly aggregates + daily/weekly indicators on the post-Sharadar bars.

The rebuild replaced every bar for 1,622 symbols, so everything derived from bars is now
stale: WeeklyPriceHistory, Indicator, WeeklyIndicator. Scores come after this (via
`trader recalculate --force --full --all`), because scoring reads indicators.

This deliberately does NOT reuse trader.py's split-fix path (trader.py:5535). That path
runs `client.pull_price_history(sym, period='max')` first, which would re-pull from
yfinance and overwrite the Sharadar bars we just installed -- undoing the repair. Same
three calls, no pull:

    PriceHistory._refresh_weekly_aggregates(sym, lo, hi)
    stock.calculate_indicators(full=True)
    stock.calculate_indicators(full=True, weekly=True)

Resumable: --skip-done consults a progress log, so an interrupted run continues.
Worker count is kept modest on purpose -- MySQL read_timeout is 30s and the rebuild
already hit error 2013 once (traps.md zombie-query cascade); indicator writes are
DB-bound, so more workers buys contention, not throughput.

  python rebuild_indicators.py --workers 8
  python rebuild_indicators.py --workers 8 --skip-done      # resume
"""
import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
PROGRESS = os.path.join(REPO, ".cache", "sharadar", "_indicator_progress.jsonl")
REPORT = os.path.join(REPO, "experiments", "data_ingest", "INDICATOR_REBUILD_REPORT.json")


def _one(sym):
    """Runs in a worker process; each gets its own DB connection via the models layer."""
    t0 = time.time()
    try:
        from database.models.core import Stock
        from database.models.technical import PriceHistory
        from database.trader_database import DB
        try:
            DB.connect(reuse_if_open=True)
        except Exception:
            pass
        st = Stock.get_or_none(Stock.symbol == sym)
        if st is None:
            return {"symbol": sym, "ok": False, "err": "no stocks row"}
        cur = DB.execute_sql(
            "SELECT MIN(`date`), MAX(`date`) FROM price_history WHERE `symbol`=%s", (sym,))
        lo, hi = cur.fetchone()
        if lo is None:
            return {"symbol": sym, "ok": False, "err": "no bars"}
        n_weekly = PriceHistory._refresh_weekly_aggregates(sym, lo, hi)
        st.calculate_indicators(full=True, silent=True)
        st.calculate_indicators(full=True, weekly=True, silent=True)
        return {"symbol": sym, "ok": True, "weekly": n_weekly,
                "secs": round(time.time() - t0, 1)}
    except Exception as e:
        return {"symbol": sym, "ok": False, "err": f"{type(e).__name__}: {e}"[:200],
                "secs": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-done", action="store_true")
    a = ap.parse_args()

    from database.models.core import Stock
    syms = sorted({s.symbol for s in Stock.select(Stock.symbol)})

    done = set()
    if a.skip_done and os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("ok"):
                        done.add(r["symbol"])
                except Exception:
                    pass
        syms = [s for s in syms if s not in done]
        print(f"resuming: {len(done)} already done, {len(syms)} remaining")
    if a.limit:
        syms = syms[:a.limit]

    print(f"rebuilding indicators for {len(syms):,} symbols on {a.workers} workers",
          flush=True)
    t0 = time.time()
    ok = err = 0
    fails = []
    os.makedirs(os.path.dirname(PROGRESS), exist_ok=True)
    with open(PROGRESS, "a") as plog, Pool(a.workers) as pool:
        for n, r in enumerate(pool.imap_unordered(_one, syms, chunksize=1), 1):
            plog.write(json.dumps(r) + "\n")
            plog.flush()
            if r["ok"]:
                ok += 1
            else:
                err += 1
                fails.append(r)
            if n % 25 == 0 or n == len(syms):
                el = time.time() - t0
                rate = n / el if el else 0
                eta = (len(syms) - n) / rate / 60 if rate else 0
                print(f"  {n}/{len(syms)}  ok={ok} err={err}  "
                      f"{rate:.2f}/s  eta {eta:.1f}m", flush=True)

    out = {"symbols": len(syms), "ok": ok, "failed": err,
           "elapsed_minutes": round((time.time() - t0) / 60, 2),
           "failures": fails[:60]}
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    print("\n" + json.dumps({k: v for k, v in out.items() if k != "failures"}, indent=2))
    if fails:
        print(f"\nfirst failures ({len(fails)} total):")
        for r in fails[:12]:
            print(f"  {r['symbol']:>8} {r.get('err')}")
    print(f"\nwrote {REPORT}")


if __name__ == "__main__":
    main()
