"""Fix v43 coverage: per-process recalculate (fresh MySQL connection each time).

Workers running long-lived MySQL connections were getting silent (0,'') errors
once their connection broke. This version uses subprocess per stock — each
trader.py invocation gets a fresh connection, no persistence issue. ~8s per
stock with no MySQL drift.

Concurrency: 3 parallel subprocesses (compromise — less than recalc-2's 8w
that hit MySQL pressure, more than serial that took 22s/stock).
"""
from __future__ import annotations
import io, sys, time, subprocess, os
from concurrent.futures import ProcessPoolExecutor, as_completed
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, '.')


def recalc_one(sym: str) -> tuple[str, bool, str]:
    """Run trader.py recalculate <sym> --full as a subprocess.

    NO --force flag: per-stock recalc still happens (since these stocks have
    no v43 rows yet, missing-date check populates all dates) but the
    post-process pipeline (historic-update + assess) is skipped — that's
    what was timing out the subprocesses earlier.
    """
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        result = subprocess.run(
            ['python', 'trader.py', 'recalculate', sym, '--full'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=240, env=env,
        )
        if result.returncode != 0:
            return sym, False, f'rc={result.returncode}: {result.stderr[-200:]}'
        return sym, True, ''
    except subprocess.TimeoutExpired:
        return sym, False, 'subprocess timeout'
    except Exception as exc:
        return sym, False, str(exc)[:200]


def main():
    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=180000')

    print('[fix] finding stocks missing v43 coverage...', flush=True)
    cur = DB.execute_sql('''
        SELECT DISTINCT s39.symbol
        FROM scores s39
        LEFT JOIN (
            SELECT DISTINCT symbol FROM scores WHERE version_id = 43
        ) s43 ON s43.symbol = s39.symbol
        WHERE s39.version_id = 39 AND s43.symbol IS NULL
    ''')
    missing = [r[0] for r in cur.fetchall()]
    print(f'[fix] missing {len(missing)} stocks', flush=True)
    if not missing:
        return
    DB.close()  # close before spawning subprocesses

    workers = 3
    print(f'[fix] subprocess-per-stock, {workers} parallel...', flush=True)

    t_start = time.time()
    ok = bad = 0
    n_done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(recalc_one, sym): sym for sym in missing}
        for fut in as_completed(futures):
            n_done += 1
            sym, success, err = fut.result()
            if success:
                ok += 1
            else:
                bad += 1
                print(f'[fix]   {sym}: ERROR {err[:120]}', flush=True)
            if n_done % 20 == 0 or n_done == len(missing):
                elapsed = time.time() - t_start
                eta = elapsed / n_done * (len(missing) - n_done)
                print(f'[fix]   [{n_done}/{len(missing)}] ok={ok} bad={bad} '
                      f'({elapsed:.0f}s, eta {eta:.0f}s)', flush=True)

    print(f'[fix] DONE: {ok} ok / {bad} failed in {time.time()-t_start:.0f}s', flush=True)

    # Verify
    DB.connect()
    cur = DB.execute_sql('SELECT COUNT(DISTINCT symbol) FROM scores WHERE version_id = 43')
    print(f'[fix] v43 distinct symbols now: {cur.fetchone()[0]}', flush=True)


if __name__ == '__main__':
    main()
