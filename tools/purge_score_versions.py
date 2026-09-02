"""Purge score/DTE rows for retired (pre-honest-era) algorithm versions.

2026-06-12 context: the scores table held ~152M rows / ~120 GB and 95.3% of it
belonged to pre-v69 versions — inflated look-ahead-era output, or hybrid honest
re-recalcs whose conclusions are already distilled into research packs under
.cache/algorithm_versions/<vNN>/research_pack/. The AlgorithmVersion ledger rows,
assessment rows, and temporal stats are KEPT (they are the historical record);
only the bulk per-(symbol,date) rows go.

Delete strategy: DATE-WINDOWED deletes (`WHERE version_id = N AND date >= lo
AND date < hi`), NOT `DELETE ... LIMIT`. MySQL 5.7 rejects index hints on
single-table DELETE and its optimizer can pick a clustered-index crawl for the
LIMIT form (first attempt died on read_timeout doing exactly that); the
equality+range predicate reliably drives the version-leading index. Window size
adapts to keep each statement a few seconds long (lock-pressure relief), and
the purge runs on a dedicated long-timeout connection so a mis-sized window
cannot orphan a server-side zombie (the documented zombie-query trap).

InnoDB does not return disk space on DELETE — pass --optimize to rebuild the
tables afterwards (online DDL; post-purge the surviving tables are small, so
headroom needed is roughly the SURVIVING table size).

Usage (run via the task queue, off-market):
  python tools/purge_score_versions.py --below 69 --optimize
  python tools/purge_score_versions.py --below 69 --dry-run
"""
import argparse
import sys
import time
from datetime import date as date_cls, timedelta

# Defensive: queue runners on Windows can land on cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

TABLES = (
    # (table, version-leading index, delete strategy)
    #
    # scores: 'windowed' — the (version_id, date) composite index makes the
    #   equality+date-range DELETE a clean range plan (EXPLAIN-verified), so we
    #   walk adaptive date windows to keep statements seconds-long.
    # dte_recommendations: 'whole_version' — its version index has no date
    #   column, so date windows would re-scan the whole version per window.
    #   Largest pre-v69 version is ~1.05M rows; one DELETE per version via the
    #   multi-table FORCE INDEX form (single-table DELETE rejects index hints
    #   on 5.7, and the bare optimizer picks the DATE index) fits well inside
    #   the dedicated connection's timeout.
    ('scores', 'scores_version_date_IDX', 'windowed'),
    ('dte_recommendations', 'dterecommendation_version_id', 'whole_version'),
)

WINDOW_START_DAYS = 30
WINDOW_MIN_DAYS = 2
WINDOW_MAX_DAYS = 1080
TARGET_SECONDS = 8.0      # grow window below this ...
CEILING_SECONDS = 15.0    # ... shrink above this


def _log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def _connect(read_timeout=600):
    """Dedicated connection — the project default read_timeout=30 is tuned for
    indexed point queries, not multi-second bulk deletes."""
    import pymysql
    import config
    kwargs = dict(config.DB_CONFIGS)
    kwargs.pop('read_timeout', None)
    kwargs.pop('write_timeout', None)
    conn = pymysql.connect(database=config.TRADER_DB_NAME, autocommit=True,
                           read_timeout=read_timeout,
                           write_timeout=read_timeout, **kwargs)
    return conn


def _as_date(value):
    if isinstance(value, date_cls):
        return value
    return date_cls.fromisoformat(str(value)[:10])


def purge(conn, below, dry_run):
    with conn.cursor() as cur:
        cur.execute('SELECT id FROM algorithm_versions WHERE id < %s ORDER BY id',
                    (below,))
        version_ids = [r[0] for r in cur.fetchall()]
    _log(f'{len(version_ids)} versions below v{below}: {version_ids}')

    grand_total = 0
    for table, index, strategy in TABLES:
        for vid in version_ids:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT COUNT(*), MIN(date), MAX(date) FROM {table} '
                    f'FORCE INDEX ({index}) WHERE version_id = %s', (vid,))
                total, dmin, dmax = cur.fetchone()
            if not total:
                continue
            if dry_run:
                _log(f'DRY-RUN {table} v{vid}: would delete {total:,} rows '
                     f'({dmin} .. {dmax})')
                grand_total += total
                continue

            if strategy == 'whole_version':
                t0 = time.time()
                with conn.cursor() as cur:
                    cur.execute(
                        f'DELETE t FROM {table} AS t FORCE INDEX ({index}) '
                        f'WHERE t.version_id = %s', (vid,))
                    deleted = cur.rowcount
                grand_total += deleted
                _log(f'{table} v{vid}: DONE {deleted:,} rows '
                     f'(expected {total:,}) in {time.time() - t0:,.0f}s')
                time.sleep(1.0)
                continue

            lo = _as_date(dmin)
            end = _as_date(dmax) + timedelta(days=1)
            window = timedelta(days=WINDOW_START_DAYS)
            deleted = 0
            t_version = time.time()
            while lo < end:
                hi = min(lo + window, end)
                t0 = time.time()
                with conn.cursor() as cur:
                    cur.execute(
                        f'DELETE FROM {table} '
                        f'WHERE version_id = %s AND date >= %s AND date < %s',
                        (vid, lo, hi))
                    n = cur.rowcount
                elapsed = time.time() - t0
                deleted += n
                lo = hi
                # Adaptive window: keep each statement a few seconds long.
                if elapsed > CEILING_SECONDS:
                    window = max(window / 2, timedelta(days=WINDOW_MIN_DAYS))
                elif elapsed < TARGET_SECONDS and n < 60000:
                    window = min(window * 2, timedelta(days=WINDOW_MAX_DAYS))
                if n:
                    time.sleep(min(elapsed * 0.25, 2.0))  # lock-pressure relief
            grand_total += deleted
            _log(f'{table} v{vid}: DONE {deleted:,} rows '
                 f'(expected {total:,}) in {time.time() - t_version:,.0f}s')

    verb = 'would delete' if dry_run else 'deleted'
    _log(f'TOTAL {verb}: {grand_total:,} rows')

    if not dry_run:
        for table, index, _strategy in TABLES:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT COUNT(*) FROM {table} FORCE INDEX ({index}) '
                    f'WHERE version_id < %s', (below,))
                remaining = cur.fetchone()[0]
            status = 'OK' if remaining == 0 else 'INCOMPLETE'
            _log(f'verify {table}: {remaining:,} rows below v{below} [{status}]')
            if remaining:
                raise SystemExit(f'{table} purge incomplete — re-run')
    return grand_total


SCORES_FK_DDL = (
    'ALTER TABLE scores ADD CONSTRAINT fk_scores_version FOREIGN KEY '
    '(version_id) REFERENCES algorithm_versions(id) ON DELETE CASCADE',
    'ALTER TABLE scores ADD CONSTRAINT scores_FK FOREIGN KEY (symbol) '
    'REFERENCES stocks(symbol) ON DELETE CASCADE ON UPDATE CASCADE',
)


def swap(conn, below, dry_run):
    """Copy-swap purge: copy the SURVIVING rows (version_id >= below) into a
    fresh table, atomically RENAME, drop the old one.

    Why not DELETE: the clustered PK is (symbol, date, version_id), so every
    version's rows interleave on every page — deleting 95% of the table
    version-by-version rewrites each 16K page dozens of times (measured
    ~1,500 rows/s ≈ 22h for the 2026-06-12 purge). Copying the 5% survivors
    is minutes, and the new table is compact (no OPTIMIZE needed).

    Run OFF-MARKET ONLY: rows written to the old table between the copy and
    the RENAME would be lost. A count-parity check right before the RENAME
    aborts the swap if a writer raced us.
    """
    for table, index, _strategy in TABLES:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id FROM algorithm_versions WHERE id >= %s ORDER BY id',
                (below,))
            keep_ids = [r[0] for r in cur.fetchall()]
            expected = {}
            for vid in keep_ids:
                cur.execute(
                    f'SELECT COUNT(*) FROM {table} FORCE INDEX ({index}) '
                    f'WHERE version_id = %s', (vid,))
                expected[vid] = cur.fetchone()[0]
        keep_total = sum(expected.values())
        _log(f'{table}: keeping {keep_total:,} rows across versions '
             f'{[v for v in keep_ids if expected[v]]}')
        if dry_run:
            continue

        new_t, old_t = f'{table}_new', f'{table}_purged_old'
        with conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS {new_t}')
            cur.execute(f'CREATE TABLE {new_t} LIKE {table}')
            for vid in keep_ids:
                if not expected[vid]:
                    continue
                t0 = time.time()
                cur.execute(
                    f'INSERT INTO {new_t} SELECT * FROM {table} '
                    f'FORCE INDEX ({index}) WHERE version_id = %s', (vid,))
                _log(f'{table} v{vid}: copied {cur.rowcount:,} rows '
                     f'in {time.time() - t0:,.0f}s')

            # Parity check immediately before the swap — abort if a writer
            # raced us between the copy and now.
            for vid in keep_ids:
                cur.execute(
                    f'SELECT COUNT(*) FROM {table} FORCE INDEX ({index}) '
                    f'WHERE version_id = %s', (vid,))
                live = cur.fetchone()[0]
                cur.execute(
                    f'SELECT COUNT(*) FROM {new_t} WHERE version_id = %s',
                    (vid,))
                copied = cur.fetchone()[0]
                if live != copied:
                    raise SystemExit(
                        f'ABORT before rename: {table} v{vid} live={live:,} '
                        f'!= copied={copied:,} (writer raced the swap?)')

            cur.execute(f'RENAME TABLE {table} TO {old_t}, {new_t} TO {table}')
            _log(f'{table}: RENAME swap done')
            t0 = time.time()
            cur.execute(f'DROP TABLE {old_t}')
            _log(f'{table}: old table dropped in {time.time() - t0:,.0f}s')

            if table == 'scores':
                # CREATE TABLE LIKE copies indexes but not FK constraints;
                # re-add with original names (freed by the DROP above).
                # FOREIGN_KEY_CHECKS=0 makes the ADD an instant INPLACE
                # metadata change — rows came from a table that enforced both
                # FKs, so re-validation is redundant.
                cur.execute('SET FOREIGN_KEY_CHECKS=0')
                for ddl in SCORES_FK_DDL:
                    cur.execute(ddl)
                cur.execute('SET FOREIGN_KEY_CHECKS=1')
                _log('scores: FK constraints restored')
            cur.execute(f'ANALYZE TABLE {table}')
            _log(f'{table}: analyzed')


def optimize_tables():
    """Rebuild purged tables to return disk space. 12h timeout — OPTIMIZE on a
    big tablespace runs long and must not orphan a zombie."""
    conn = _connect(read_timeout=43200)
    try:
        for table, _index, _strategy in TABLES:
            _log(f'OPTIMIZE TABLE {table} (online rebuild — may take a while)')
            t0 = time.time()
            with conn.cursor() as cur:
                cur.execute(f'OPTIMIZE TABLE {table}')
                for row in cur.fetchall():
                    # InnoDB reports "doing recreate + analyze instead" — that
                    # IS the rebuild working, not an error.
                    _log(f'  {row}')
            _log(f'OPTIMIZE {table} finished in {time.time() - t0:,.0f}s')
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--below', type=int, required=True,
                    help='purge rows with version_id strictly below this id')
    ap.add_argument('--optimize', action='store_true',
                    help='rebuild tables after purge to reclaim disk space')
    ap.add_argument('--swap', action='store_true',
                    help='copy-swap strategy: copy survivors to a new table, '
                         'RENAME, drop old (fast path for high deletion '
                         'ratios; OFF-MARKET ONLY — see swap() docstring)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    from database.models.core import AlgorithmVersion

    active = AlgorithmVersion.get_active_scores_version()
    if active is None or active.id < args.below:
        raise SystemExit(
            f'refusing: --below {args.below} would purge at/above the active '
            f'version (active={getattr(active, "id", None)})')

    conn = _connect(read_timeout=43200)
    try:
        if args.swap:
            swap(conn, args.below, args.dry_run)
        else:
            purge(conn, args.below, args.dry_run)
    finally:
        conn.close()
    if args.optimize and not args.dry_run and not args.swap:
        optimize_tables()
    _log('all done')


if __name__ == '__main__':
    main()
