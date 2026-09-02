"""
Delete score rows that no longer have a price bar behind them.

The rebuild replaced price_history from Sharadar, which starts 1997-12-31. The previous
deep backfill reached 1995, so ~207,936 score rows now sit on dates where no price bar
exists any more — plus others orphaned by the de-duplication and the non-Sharadar re-pull.
281,098 rows in total.

Why this matters more than it looks: those scores were computed from the OLD, contaminated
prices. Leaving them behind means a backtest reaching before 1998 reads signals derived from
data we just deleted for being wrong, with nothing to reconcile them against. They are worse
than missing rows, because missing rows are visible and these are not.

Scope: the ACTIVE scoring version only, and only rows with no (symbol, date) match in
price_history. Weekly scores get the same treatment against weekly_price_history.

  python purge_orphan_scores.py            # count only
  python purge_orphan_scores.py --commit
"""
import argparse
import json
import os
import sys
import time

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
REPORT = os.path.join(REPO, "experiments", "data_ingest", "ORPHAN_PURGE_REPORT.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--version", type=int, default=None)
    a = ap.parse_args()

    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    vid = a.version
    if vid is None:
        av = AlgorithmVersion.get_active_scores_version()
        vid = getattr(av, "id", av)
    print(f"active scoring version: {vid}")

    # weekly_scores is NOT version-keyed (cols: id,symbol,date,rsi,macd,composite,trend),
    # so its orphan check runs without a version filter.
    pairs = [
        ("scores", "price_history", True),
        ("weekly_scores", "weekly_price_history", False),
    ]
    out = {"version": vid, "committed": a.commit, "tables": {}}
    for tbl, ref, versioned in pairs:
        try:
            n = DB.execute_sql(
                f"SELECT COUNT(*) FROM {tbl} s LEFT JOIN {ref} p "
                f"ON p.symbol=s.symbol AND p.date=s.date "
                f"WHERE {'s.version_id=%s AND ' if versioned else ''}p.symbol IS NULL",
                (vid,) if versioned else ()).fetchone()[0]
        except Exception as e:
            out["tables"][tbl] = {"error": str(e)[:160]}
            print(f"  {tbl}: SKIPPED ({type(e).__name__})")
            continue
        rec = {"orphans": n}
        print(f"  {tbl}: {n:,} orphaned rows")
        if a.commit and n:
            t0 = time.time()
            deleted = 0
            # MySQL rejects LIMIT on a multi-table DELETE, and `scores` has a composite PK
            # (symbol,date,version_id) with no surrogate id -- so chunk PER SYMBOL, which
            # bounds each statement to a few thousand rows, well inside the 30s timeout.
            syms = [r[0] for r in DB.execute_sql(
                f"SELECT DISTINCT s.symbol FROM {tbl} s LEFT JOIN {ref} p "
                f"ON p.symbol=s.symbol AND p.date=s.date "
                f"WHERE {'s.version_id=%s AND ' if versioned else ''}p.symbol IS NULL",
                (vid,) if versioned else ()).fetchall()]
            print(f"    {len(syms)} symbols carry orphans", flush=True)
            for n, sym in enumerate(syms, 1):
                params = (vid, sym) if versioned else (sym,)
                cur = DB.execute_sql(
                    f"DELETE s FROM {tbl} s LEFT JOIN {ref} p "
                    f"ON p.symbol=s.symbol AND p.date=s.date "
                    f"WHERE {'s.version_id=%s AND ' if versioned else ''}s.symbol=%s "
                    f"AND p.symbol IS NULL", params)
                deleted += cur.rowcount if hasattr(cur, "rowcount") else 0
                if n % 50 == 0 or n == len(syms):
                    print(f"    {n}/{len(syms)} symbols, {deleted:,} rows", flush=True)
            rec.update(deleted=deleted, secs=round(time.time() - t0, 1))
        out["tables"][tbl] = rec

    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    if not a.commit:
        print("COUNT ONLY -- re-run with --commit")


if __name__ == "__main__":
    main()
