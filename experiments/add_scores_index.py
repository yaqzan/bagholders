"""Add the covering index `scores(symbol, version_id, date)` after assessment finishes.

Run this once, after a long-running assess completes, to eliminate the filesort
in the per-stock Score load that the EXPLAIN found:

    Using intersect(fk_scores_version, PRIMARY); Using filesort

With the new covering index, MySQL can satisfy WHERE symbol=? AND version_id=?
AND date>=? ORDER BY date directly from the index, no sort.

Cost: ~600 MB of additional index space on a 28.8M-row table; ~1-3 minutes
to build via online DDL (ALGORITHM=INPLACE, LOCK=NONE).

Idempotent — checks for existing index first.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config, pymysql

INDEX_NAME = 'scores_sym_ver_date'

C = pymysql.connect(
    host=config.DB_CONFIGS.get('host', 'localhost'),
    user=config.DB_CONFIGS.get('user', 'root'),
    password=config.DB_CONFIGS.get('password', ''),
    database=config.TRADER_DB_NAME,
    autocommit=True,
)
cur = C.cursor()

cur.execute(f"""SELECT 1 FROM information_schema.statistics
                WHERE table_schema = %s AND table_name = 'scores' AND index_name = %s
                LIMIT 1""",
            (config.TRADER_DB_NAME, INDEX_NAME))
if cur.fetchone():
    print(f"Index `{INDEX_NAME}` already exists. Nothing to do.")
    sys.exit(0)

print(f"Creating index `{INDEX_NAME}` on scores(symbol, version_id, date) ...")
print("Online DDL — does not block reads/writes. Expect 1-3 min.")
t0 = time.time()
cur.execute(f"""ALTER TABLE scores
                 ADD INDEX {INDEX_NAME} (symbol, version_id, date),
                 ALGORITHM=INPLACE, LOCK=NONE""")
dt = time.time() - t0
print(f"Done in {dt:.1f}s.")

print("\nNew query plan for the per-stock Score load:")
cur.execute("""EXPLAIN
    SELECT * FROM scores
    WHERE symbol = 'AAPL' AND version_id = 26 AND date >= '2016-01-01'
    ORDER BY date ASC""")
cols = [d[0] for d in cur.description]
for r in cur.fetchall():
    for c, v in zip(cols, r):
        print(f"  {c:15s} = {v}")

C.close()
