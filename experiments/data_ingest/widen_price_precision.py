"""
Widen price columns from DECIMAL(10,2) to DECIMAL(18,6).

WHY. Deep history is heavily split-adjusted, so adjusted prices go sub-dollar: 89,771
bars below $1.00, 42,817 below $0.50, 9,672 below $0.10, and 3 stored as exactly 0.00.
At 2 decimal places a $0.03 NVDA bar (3,687 such bars) quantises to ~33% per tick --
daily returns computed off that are noise, not signal.

This is a PRE-EXISTING schema limit, but the Sharadar repair made it bite harder: the old
deep segment was split-adjusted-only (higher prices), and correcting it to split+dividend
pushes deep prices DOWN into the quantisation floor. Fixing the convention without
widening the column would trade one silent distortion for another.

TIMEOUTS. `ALTER TABLE ... MODIFY COLUMN` on 7.1M rows rebuilds the table ("copy to tmp
table") and blows the 30s client read_timeout. The client giving up does NOT stop the
server -- a blind retry queues a SECOND conflicting ALTER on the same table, which is the
cascade in traps.md. So this script:
  1. waits for any in-flight ALTER on the target table to finish (polling PROCESSLIST),
  2. uses its own connection with a long read_timeout,
  3. skips columns already at the target type, so it is safe to re-run.

  python widen_price_precision.py
"""
import sys
import time

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)

TARGET = "decimal(18,6)"
TABLES = {
    "price_history": ["open", "high", "low", "close"],
    "weekly_price_history": ["open", "high", "low", "close"],
}


def connect():
    import config
    import pymysql
    kw = dict(config.DB_CONFIGS)
    kw["read_timeout"] = 3600      # a table rebuild is legitimately long
    kw["write_timeout"] = 3600
    kw["database"] = config.TRADER_DB_NAME
    return pymysql.connect(**kw)


def inflight_alter(cur, table):
    cur.execute("SHOW FULL PROCESSLIST")
    for row in cur.fetchall():
        info = (row[7] or "")
        if "ALTER TABLE" in info.upper() and table in info:
            return row[0], row[5], row[6]
    return None


def main():
    conn = connect()
    cur = conn.cursor()
    for table, cols in TABLES.items():
        # 1. let any server-side ALTER we already triggered finish
        while True:
            hit = inflight_alter(cur, table)
            if not hit:
                break
            tid, secs, state = hit
            print(f"  waiting on in-flight ALTER id={tid} {secs}s state={state!r}",
                  flush=True)
            time.sleep(15)

        cur.execute(f"SHOW COLUMNS FROM {table}")
        types = {r[0]: r[1].lower() for r in cur.fetchall()}
        for c in cols:
            if types.get(c) == TARGET:
                print(f"  {table}.{c} already {TARGET} -- skip", flush=True)
                continue
            t0 = time.time()
            print(f"  ALTER {table}.{c}  {types.get(c)} -> {TARGET} ...", flush=True)
            cur.execute(f"ALTER TABLE {table} MODIFY COLUMN `{c}` DECIMAL(18,6)")
            conn.commit()
            print(f"    done in {time.time() - t0:.0f}s", flush=True)

    for table, cols in TABLES.items():
        cur.execute(f"SHOW COLUMNS FROM {table}")
        types = {r[0]: r[1].lower() for r in cur.fetchall()}
        print(f"{table}: " + ", ".join(f"{c}={types.get(c)}" for c in cols))
    conn.close()


if __name__ == "__main__":
    main()
