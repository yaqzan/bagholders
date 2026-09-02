"""Atomic re-tag v45→v46 to recover from procedural error.

State after recalc v1 + partial recalc v2:
  - v45 has all 1.7M rows with v46 formula (recalc v1 wrote v46 formula but tagged v45)
  - v46 has ~900k rows with v46 formula (recalc v2 wrote fresh from indicators, partial 346/772)

Goal: clean v46 dataset using recalc v1's complete work (all 772 stocks).

Steps (each in batches to fit under 30s MySQL timeout):
  1. DELETE all v46 rows (partial, supersede with v1's complete data)
  2. UPDATE v45 → v46 WHERE updated_at >= recalc_v1_start
     (this preserves the 11,834 "skipped" rows as v45 with v45 formula)

Run: python experiments/weekly_volume/retag_v45_to_v46.py
"""
from __future__ import annotations
import sys, time

sys.path.insert(0, '.')

from database.trader_database import DB

V45_ID = 45
V46_ID = 46
RECALC_V1_START = '2026-05-08 12:36:00'   # conservative — recalc v1 first wrote ~12:36
BATCH = 5000


def run_batch(sql: str, max_iters: int = 5000) -> int:
    """Run a DELETE/UPDATE with LIMIT BATCH repeatedly until 0 rows affected.
    Retries on connection timeouts (auto-reconnects via peewee)."""
    total = 0
    consecutive_errors = 0
    for i in range(max_iters):
        try:
            DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=25000')
            t0 = time.time()
            cur = DB.execute_sql(sql)
            affected = cur.rowcount
            total += affected
            elapsed = time.time() - t0
            if i == 0 or (i + 1) % 5 == 0 or affected == 0:
                print(f'  [iter {i+1}] {affected:,} rows in {elapsed:.1f}s  (total: {total:,})', flush=True)
            consecutive_errors = 0
            if affected == 0:
                break
        except Exception as e:
            consecutive_errors += 1
            print(f'  [iter {i+1}] ERROR: {type(e).__name__}: {str(e)[:100]}  (consecutive errors: {consecutive_errors})', flush=True)
            if consecutive_errors >= 5:
                print(f'  [abort] 5 consecutive errors — stopping')
                break
            try:
                DB.close()
                DB.connect()
            except Exception:
                pass
            time.sleep(2)
    return total


def main():
    print('=' * 80)
    print('Re-tag v45 → v46 (recovery from procedural error)')
    print('=' * 80)

    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=25000')

    print(f'\n[1/2] DELETE FROM scores WHERE version_id = {V46_ID}  (batched, LIMIT {BATCH})')
    deleted = run_batch(
        f'DELETE FROM scores WHERE version_id = {V46_ID} LIMIT {BATCH}'
    )
    print(f'  total deleted: {deleted:,}')

    print(f'\n[2/2] UPDATE scores SET version_id = {V46_ID}'
          f" WHERE version_id = {V45_ID} AND updated_at >= '{RECALC_V1_START}'  (batched, LIMIT {BATCH})")
    updated = run_batch(
        f"UPDATE scores SET version_id = {V46_ID} "
        f"WHERE version_id = {V45_ID} AND updated_at >= '{RECALC_V1_START}' "
        f"LIMIT {BATCH}"
    )
    print(f'  total re-tagged: {updated:,}')

    print('\n[verify] AlgorithmVersion table:')
    cur = DB.execute_sql('SELECT id, git_commit FROM algorithm_versions WHERE id IN (45, 46)')
    for r in cur.fetchall():
        print(f'  id={r[0]} commit={r[1]}')

    print('\n[verify] Score row counts (sampled — full count may timeout):')
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=25000')
    try:
        cur = DB.execute_sql(f'SELECT COUNT(*) FROM scores WHERE version_id = {V46_ID}')
        print(f'  v46: {cur.fetchone()[0]:,}')
    except Exception as e:
        print(f'  v46 count failed: {e}')
    try:
        cur = DB.execute_sql(f'SELECT COUNT(*) FROM scores WHERE version_id = {V45_ID}')
        print(f'  v45: {cur.fetchone()[0]:,}  (expected ~11,834 = recalc-v1 skipped rows)')
    except Exception as e:
        print(f'  v45 count failed: {e}')

    print('\n[done] Re-tag complete. Run `trader assess --force` next.')


if __name__ == '__main__':
    main()
