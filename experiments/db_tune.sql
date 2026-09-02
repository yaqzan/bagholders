-- Reusable MySQL tuning for `trader recalculate --force` runs.
-- All changes are SESSION-OR-GLOBAL safe; no my.ini edit required.
-- Apply this file via: mysql -u root trader < experiments/db_tune.sql
-- Settings persist until the MySQL service restarts.

-- 1. Reduce fsync overhead on commit.
--    Default = 1: fsync log to disk on every commit (~10-20ms on consumer SSD).
--    Setting 2: writes to OS log buffer instantly; OS flushes to disk every ~1s.
--    On power-failure during recalc, up to ~1s of redo can be lost — acceptable
--    because recalc is idempotent and can be re-run with --force.
SET GLOBAL innodb_flush_log_at_trx_commit = 2;

-- 2. Tell InnoDB the storage is SSD-class.
--    Default 200 assumes spinning rust; 2000+ matches consumer SSD.
--    Affects background page flushing (purge, merge, dirty-page writeback).
SET GLOBAL innodb_io_capacity = 2000;
SET GLOBAL innodb_io_capacity_max = 4000;

-- 3. Verify
SELECT @@innodb_flush_log_at_trx_commit, @@innodb_io_capacity, @@innodb_io_capacity_max;

-- ── To restore defaults after a recalc (or just restart MySQL) ────────────
-- SET GLOBAL innodb_flush_log_at_trx_commit = 1;
-- SET GLOBAL innodb_io_capacity = 200;
-- SET GLOBAL innodb_io_capacity_max = 2000;

-- ── Run AFTER assessment completes (one-time, permanent) ──────────────────
-- Eliminates ~25 min of filesort overhead per recalc on the per-stock Score load.
-- ALTER TABLE scores ADD INDEX scores_sym_ver_date (symbol, version_id, date);
