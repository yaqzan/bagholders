"""Read-only live diagnosis of an in-progress recalculate run.

Opens its own MySQL connection (does NOT use the trader DB pool) and samples:
  - InnoDB row-lock waits (contention signal)
  - Active transactions per worker (state, lock_count, query)
  - Process list filtered to our DB user

Polls 3 times with 5s gap so transient writes vs sustained waits are
distinguishable. Pure SELECTs against information_schema; no impact on the
running workers.
"""
import sys, os, time, pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

CONN = pymysql.connect(
    host=config.DB_CONFIGS.get('host', 'localhost'),
    port=config.DB_CONFIGS.get('port', 3306),
    user=config.DB_CONFIGS.get('user', 'root'),
    password=config.DB_CONFIGS.get('password', ''),
    database=config.TRADER_DB_NAME,
    charset='utf8mb4',
    autocommit=True,
)
cur = CONN.cursor()


def q(sql):
    cur.execute(sql)
    return cur.fetchall(), [d[0] for d in cur.description] if cur.description else []


def hr(s):
    print('\n' + '-' * 90)
    print(s)
    print('-' * 90)


def sample(label):
    print('\n' + '=' * 90)
    print(f'SAMPLE: {label}  ({time.strftime("%H:%M:%S")})')
    print('=' * 90)

    # 1. Lock waits — global counter (works on every MySQL version)
    hr('1. Lock-wait counters (global, sampled)')
    rows, _ = q("""
        SHOW GLOBAL STATUS WHERE Variable_name IN
        ('Innodb_row_lock_current_waits', 'Innodb_row_lock_waits',
         'Innodb_row_lock_time', 'Innodb_row_lock_time_avg')
    """)
    for r in rows:
        print(f'  {r[0]:35s}  {r[1]}')

    # 2. Active transactions on the scores table
    hr('2. Active InnoDB transactions touching `scores`')
    rows, _ = q("""
        SELECT
          trx_id,
          trx_state,
          TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS age_s,
          trx_rows_locked,
          trx_rows_modified,
          LEFT(trx_query, 80) AS query_head
        FROM information_schema.innodb_trx
        WHERE trx_query LIKE '%scores%' OR trx_query LIKE '%INSERT%' OR trx_query LIKE '%UPDATE%'
        ORDER BY age_s DESC
    """)
    if not rows:
        print('  (no active transactions on scores)')
    else:
        print(f'  {"trx_id":>10s}  {"state":12s}  {"age":>4s}  {"locked":>7s}  {"mod":>6s}  query')
        for r in rows:
            print(f'  {r[0]:>10}  {r[1]:12s}  {r[2]:>4}  {r[3]:>7}  {r[4]:>6}  {r[5]}')

    # 3. Process list — what each connection is doing
    hr('3. Connections from our DB user (state breakdown)')
    rows, _ = q(f"""
        SELECT
          ID, USER, STATE, COMMAND, TIME,
          LEFT(IFNULL(INFO, ''), 60) AS info_head
        FROM information_schema.processlist
        WHERE USER = %s AND DB = %s
        ORDER BY TIME DESC
    """ % (f"'{config.DB_CONFIGS['user']}'", f"'{config.TRADER_DB_NAME}'"))
    print(f'  {"id":>8s}  {"command":>10s}  {"state":25s}  {"time":>5s}  query')
    state_count = {}
    for r in rows:
        st = r[2] or '(none)'
        state_count[st] = state_count.get(st, 0) + 1
        print(f'  {r[0]:>8}  {r[3]:>10}  {st:25s}  {r[4]:>5}  {r[5]}')
    print(f'\n  STATE breakdown:')
    for st, n in sorted(state_count.items(), key=lambda x: -x[1]):
        print(f'    {n:3d}  {st}')

    # 4. InnoDB row ops counters (delta sampled below if we sample twice)
    hr('4. InnoDB row-op counters (snapshot)')
    rows, _ = q("""
        SHOW GLOBAL STATUS WHERE Variable_name IN
        ('Innodb_rows_inserted', 'Innodb_rows_updated', 'Innodb_rows_read',
         'Innodb_row_lock_waits', 'Innodb_row_lock_time', 'Innodb_row_lock_current_waits')
    """)
    snap = {r[0]: int(r[1]) for r in rows}
    return snap


if __name__ == '__main__':
    snaps = []
    for i in range(3):
        snap = sample(f'iter {i+1}/3')
        snaps.append((time.time(), snap))
        if i < 2:
            time.sleep(5)

    # Compute deltas
    print('\n' + '=' * 90)
    print('DELTA between samples (per second)')
    print('=' * 90)
    for j in range(1, len(snaps)):
        dt = snaps[j][0] - snaps[j-1][0]
        a, b = snaps[j-1][1], snaps[j][1]
        print(f'\n  Δ over {dt:.1f}s:')
        for k in sorted(a.keys()):
            d = b[k] - a[k]
            rate = d / dt if dt > 0 else 0
            print(f'    {k:36s}  +{d:>10,d}  ({rate:>10,.1f} / sec)')
    cur.close()
    CONN.close()
