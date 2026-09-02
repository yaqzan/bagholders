"""Inspect current MySQL settings and indexes relevant to recalc speed."""
import sys, os, pymysql
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

C = pymysql.connect(
    host=config.DB_CONFIGS.get('host', 'localhost'),
    port=config.DB_CONFIGS.get('port', 3306),
    user=config.DB_CONFIGS.get('user', 'root'),
    password=config.DB_CONFIGS.get('password', ''),
    database=config.TRADER_DB_NAME,
    autocommit=True,
)
cur = C.cursor()


def show(sql, label=None):
    cur.execute(sql)
    rows = cur.fetchall()
    if label:
        print(f'\n--- {label} ---')
    for r in rows:
        print('  ', r)


print('MYSQL VERSION:')
show("SELECT VERSION()")

print('\nWRITE-PATH SETTINGS:')
show("""SHOW VARIABLES WHERE Variable_name IN
        ('innodb_flush_log_at_trx_commit', 'sync_binlog', 'log_bin',
         'innodb_flush_method', 'innodb_log_file_size',
         'innodb_log_buffer_size', 'innodb_doublewrite',
         'innodb_io_capacity', 'innodb_io_capacity_max')""")

print('\nINDEXES ON `scores`:')
cur.execute("""SHOW INDEX FROM scores""")
seen_keys = {}
for r in cur.fetchall():
    key = r[2]  # Key_name
    seq = r[3]  # Seq_in_index
    col = r[4]  # Column_name
    seen_keys.setdefault(key, []).append((seq, col))
for key, cols in seen_keys.items():
    cols_sorted = sorted(cols)
    print(f'  {key:30s} ({", ".join(c for _, c in cols_sorted)})')

# Check if there's an index that helps the query:
# WHERE symbol = ? AND version_id = ? AND date >= ? ORDER BY date
print('\nQUERY PLAN for the per-stock Score load (real shape used by recalculate_scores_batched):')
cur.execute("""EXPLAIN
    SELECT * FROM scores
    WHERE symbol = 'AAPL' AND version_id = 26 AND date >= '2016-01-01'
    ORDER BY date ASC""")
cols = [d[0] for d in cur.description]
print('  ', cols)
for r in cur.fetchall():
    print('  ', r)

print('\nTABLE STATS:')
show(f"""SELECT TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = '{config.TRADER_DB_NAME}'
          AND TABLE_NAME = 'scores'""")

cur.close()
C.close()
