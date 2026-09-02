# Disaster Recovery — Backups & Restore

Gameplan P0.1. Companion to [task-queue.md](task-queue.md) (job scheduling/admission) and
[traps.md](traps.md) section 1 (infra gotchas). Covers: what's backed up, where, how to
restore, measured timings, RTO for full box death.

**Owner surfaces:** `scripts/backup_daily.ps1`, `scripts/install_backup_daily.ps1`, this
doc. Registered nightly job: Task Scheduler `TraderBackupDaily` (03:00 local) ->
`python trader.py queue submit --priority idle --db heavy --window off_market` ->
`scripts/backup_daily.ps1` (the queue payload).

---

## 1. What's backed up

**Nightly (per-table mysqldump + gzip)** — the irreplaceable set: data that can't be
regenerated, or whose only other source (yfinance, a paid options vendor) is gone
(delisted symbols), costs real money to re-buy, or silently drifts (adjusted-close
revisions). Order deliberate — smallest/most-precious first, huge `option_prices` last
(a per-table dump is all-or-nothing, and the queue can preempt an idle-tier job mid-run
for higher-priority work, so a repeatedly-interrupted run still captures the live-money
ledger + audit trail in the first few seconds):

| Table | Why irreplaceable |
|---|---|
| `score_intraday_logs` | Only record of intraday score evolution (per-run audit snapshot); unreconstructable. |
| `earnings_dates` | Point-in-time EPS/earnings calendar; live providers don't serve historical as-of state. |
| `portfolio_runs`, `portfolio_positions`, `portfolio_pending_alerts`, `portfolio_equity_snapshots` | Live-Portfolio forward ledger — the money-tracking record, sole source of open/close pushes. |
| `stocks` | Tracked universe (symbol metadata, delisted flags). |
| `options`, `option_prices` | Historical option chain + pricing (~90M rows in `option_prices`). Re-buying from a vendor (Polygon/historicaloptiondata) costs ~$79-$2k depending on depth ([data-acquisition.md](data-acquisition.md)); delisted-era chains may not be re-purchasable at all. |
| `price_history` | Daily OHLCV. Re-fetchable from yfinance for still-trading symbols, but yfinance drops delisted history and adjusted-close silently revises — our snapshot is survivorship-honest. |

**NOT in the nightly set (deliberately excluded — regenerable):** `scores`, `indicators`,
`weekly_scores`, `weekly_indicators`, `weekly_price_history`. Fully reconstructable from
`price_history` + `indicators` via `trader recalculate --force --full` (~25 min for 10y,
traps.md) once irreplaceables are restored. `scores` alone is 12.2M rows / 7.1GB data +
2.8GB index — backing it up nightly would multiply dump size for zero net protection.

**Weekly (Sunday, or `-Full`):** one whole-database mysqldump, every table including
derived ones, as a convenience fast-path (faster than irreplaceables + a 10y recalculate,
though the latter always works as fallback).

**Also copied verbatim every run (plain file copy, not mysqldump):**
- `.cache/task_queue.db` — task-queue's SQLite state (in-flight/recent job history).
- `algorithm_versions/scoring_locks.json` — scoring-version-guard fingerprint (traps.md "Scoring-lock cutoff drift").

**NOT covered (external dependency — flag for the user):** `config.py` (imported by
`database/trader_database.py` for MySQL `DB_CONFIGS`/`TRADER_DB_NAME`) lives OUTSIDE this
repo at `C:\Development\Archivist\archiver\config.py`, on the box's global `PYTHONPATH`.
Untracked, not in this repo's source control, holds that other project's own credentials
too. **Not backed up by `backup_daily.ps1`.** On full box death this file must be
recreated independently. Recommend either (a) bring `config.py`'s
`DB_CONFIGS`/`TRADER_DB_NAME` under the same backup umbrella, or (b) record the DB
connection shape (currently `127.0.0.1:3306`/`root`, no password) somewhere durable and box-independent.

## 2. Where backups live

**Target volume: largest non-C fixed volume**, picked dynamically via `Get-Volume` every
run (no hardcoded drive letter). Measured 2026-07-13: C (OS, 928.7GB/359.8GB free),
D (Archives, 342,738.8GB/4,703.6GB free — picked), B (Home, 14,903.8GB/6,165.9GB free).

**Current backup root: `D:\Backups\Trader\`.** Fallback if no non-C fixed volume exists:
`C:\Backups\Trader` with a warning — that puts backups on the SAME disk as the working
DB/repo, breaking the "second disk" half of 3-2-1; treat as a flagged incident, not a silent degrade.

```
D:\Backups\Trader\
  daily\<yyyyMMdd>\
    score_intraday_logs.sql.gz, earnings_dates.sql.gz, portfolio_runs.sql.gz,
    portfolio_positions.sql.gz, portfolio_pending_alerts.sql.gz,
    portfolio_equity_snapshots.sql.gz, stocks.sql.gz, options.sql.gz,
    price_history.sql.gz, option_prices.sql.gz, task_queue.db, scoring_locks.json,
    manifest.json          <- per-table byte size, dump seconds, approx row count
  weekly\<yyyyMMdd>\
    trader_full.sql.gz, task_queue.db, scoring_locks.json, manifest.json
  _status\
    last_attempt.json      <- every run, success or failure (heartbeat reads this)
    last_success.json      <- last run with zero table failures
```

**Retention:** 14 daily dirs, 8 weekly dirs (pruned end of every run, oldest-first by
`yyyyMMdd` name).

**Credentials:** resolved dynamically at run time via the same mechanism
`database/trader_database.py` uses (`config.DB_CONFIGS` + `config.TRADER_DB_NAME`) —
written to a temp `.py` under `.codex\runs\backup_daily_*\` and invoked as
`python <file>.py` (never `python -c "..."` — PowerShell's native-exe re-quoting was found
to silently strip embedded double-quotes when run via `powershell -File`, i.e. exactly
how Task Scheduler/queue launcher invoke it, even though the same snippet works typed
interactively). Password (if set) passed via `MYSQL_PWD` env var, never a CLI flag, never
logged. No secret hardcoded in `backup_daily.ps1`.

**Known failure mode — backup volume unwritable (seen 2026-08-20/21):** when D:\Archive's
DrivePool write-funnel seizes (whole-volume Access-denied while free space looks fine —
see global CLAUDE.md), the run died at the FIRST write to D: (`_status\last_attempt.json`,
before any logging), exit 1 in ~2s, EMPTY `run.log`, only a phase=`starting` `status.json`
on C: remained. Script now guards that write and fails loudly into log + `failed.json`.
Diagnosis: failed run dir contains ONLY `status.json` -> target volume write failure, not
mysqldump/DB trouble. Check DrivePool state, don't chase ACLs.

## 3. Restore commands

All commands use this box's default connection (`host=127.0.0.1 port=3306 user=root`, no
password). `$mysql`/`$mysqldump` = `C:\Program Files\MySQL\MySQL Server 5.7\bin\{mysql,mysqldump}.exe`.

**Decompress a `.sql.gz` (pure .NET, no external gzip):**
```powershell
$inStream = [System.IO.File]::OpenRead("<path>.sql.gz")
$gz = New-Object System.IO.Compression.GZipStream($inStream, [System.IO.Compression.CompressionMode]::Decompress)
$outStream = [System.IO.File]::Create("<path>_restored.sql")
$gz.CopyTo($outStream); $outStream.Close(); $gz.Close(); $inStream.Close()
```

**Restore one table into a target schema:**
```powershell
& $mysql -h 127.0.0.1 -P 3306 -u root -e "CREATE SCHEMA IF NOT EXISTS <target_schema>;"
cmd /c "`"$mysql`" -h 127.0.0.1 -P 3306 -u root <target_schema> < `"<path>_restored.sql`""
```
(`cmd /c ... < file` deliberate — streams via cmd.exe's native redirection instead of
loading the whole decompressed SQL into PowerShell memory via `Get-Content -Raw`, which
matters at GB scale, e.g. `option_prices`.)

**Verify row counts (EXACT, not `information_schema` — see Gotcha):**
```powershell
& $mysql -h 127.0.0.1 -P 3306 -u root -N -B -e "SELECT COUNT(*) FROM <target_schema>.<table>;"
```

**Tear down a scratch schema:**
```powershell
& $mysql -h 127.0.0.1 -P 3306 -u root -e "DROP SCHEMA <target_schema>;"
```

**Gotcha confirmed live during the drill:** `information_schema.tables.table_rows` (what
the manifest's `approx_rows` uses, for speed) can be substantially wrong for InnoDB
tables with frequent small appends — `score_intraday_logs` read `20,887` via
`information_schema` vs TRUE exact count `325,668` (~15.6x undercount, stale InnoDB
stats on an append-heavy audit table). **Never trust manifest `approx_rows` for restore
verification — always run exact `COUNT(*)` on both source and restored, compare directly.**

### Restoring the FULL database from a weekly dump
```powershell
$inStream = [System.IO.File]::OpenRead("D:\Backups\Trader\weekly\<date>\trader_full.sql.gz")
$gz = New-Object System.IO.Compression.GZipStream($inStream, [System.IO.Compression.CompressionMode]::Decompress)
$outStream = [System.IO.File]::Create("C:\restore\trader_full.sql")
$gz.CopyTo($outStream); $outStream.Close(); $gz.Close(); $inStream.Close()
& $mysql -h 127.0.0.1 -P 3306 -u root -e "CREATE SCHEMA IF NOT EXISTS trader;"
cmd /c "`"$mysql`" -h 127.0.0.1 -P 3306 -u root trader < `"C:\restore\trader_full.sql`""
```
Then layer any DAILY irreplaceable dumps newer than the weekly (same per-table restore,
same `trader` target) to bring `option_prices`/`score_intraday_logs`/etc. current to the
last successful daily run.

## 4. Restore drill — RUN 2026-07-13 (timed, scratch schema)

Restored `score_intraday_logs` + `earnings_dates` (both in the nightly irreplaceable set)
into a throwaway `trader_restore_test` schema, verified against live `trader`, dropped.
**Live `trader` schema never touched.**

Source exact counts (captured before drill): `score_intraday_logs` 325,668;
`earnings_dates` 18,272.

Measured timings (2026-07-13 ~06:50 ET, against first real nightly backup artifacts):

| Step | Time |
|---|---|
| `CREATE SCHEMA trader_restore_test` | 0.1s |
| Decompress `score_intraday_logs.sql.gz` (103.9 MB gz -> 774 MB raw) | 1.2s |
| Restore `score_intraday_logs` (774 MB SQL, 325,668 rows incl. 3 LONGTEXT JSON cols) | 56.7s |
| Decompress `earnings_dates.sql.gz` (0.3 MB gz) | <0.1s |
| Restore `earnings_dates` (18,272 rows) | 0.7s |
| Verification: 4x exact `COUNT(*)` | 1.2s |
| `DROP SCHEMA trader_restore_test` | 0.5s |
| **Total drill wall time** | **~60s** |

Verification — ALL EXACT MATCHES: `score_intraday_logs` 325,668==325,668;
`earnings_dates` 18,272==18,272. Data-equality fingerprints beyond counts:
`score_intraday_logs` `(MAX(logged_at), MIN(date), MAX(date), SUM(overall))` =
`(2026-07-13 04:18:42, 2026-05-27, 2026-07-13, 16052117.00)` identical both sides;
`earnings_dates` `(MIN(date), MAX(date), SUM(eps_estimate), COUNT(DISTINCT symbol))` =
`(2001-10-26, 2026-11-02, -3827.140, 761)` identical both sides. Post-drop `SHOW SCHEMAS`
confirmed `trader_restore_test` gone, `trader` untouched.

Restore throughput: ~13.7 MB raw SQL/s (~5,700 rows/s on the JSON-heavy audit table —
lighter tables faster per row).

### First real backup run (reference, 2026-07-13)
Task-queue task #600 (`--priority idle --db heavy --window off_market`). **Total: 1,302 MB
gz in 4,056.7s (~68 min), exit 0.**

| Table | gz size | dump time |
|---|---|---|
| `option_prices` (89.9M rows) | 1,111.5 MB | 3,394.6s (~57 min) |
| `score_intraday_logs` | 103.9 MB | 481.8s |
| `price_history` | 68.7 MB | 105.2s |
| `options` | 17.6 MB | 64.1s |
| `earnings_dates` | 0.3 MB | 1.3s |
| `stocks` | 0.1 MB | 1.2s |
| 4x portfolio ledger tables | <0.01 MB each | ~1.1s each |

`option_prices` is ~85% of bytes and ~84% of runtime — why the backup runs idle-priority
and why it's dumped last. Mid-run this first backup was preempted once (killed+requeued
by a normal-priority task needing the heavy-DB slot) and auto-resumed cleanly —
`--restartable` working as designed.

### Restore-mechanics dry run (syntax validation, 2026-07-13 05:2x ET)
Before timing the real drill, the same command sequence (dump -> gzip -> `CREATE SCHEMA`
-> restore -> exact `COUNT(*)` -> `DROP SCHEMA`) was validated on a trivial table
(`algorithm_versions`, 69 rows): all steps exit 0, restored count 69==69. Zero surprises in the timed drill.

## 5. RTO statement

**Restore-drill RTO (small/medium tables into a scratch schema): ~60s measured** (section
4) — a targeted single-table recovery ("someone truncated `earnings_dates`") is
minutes-scale, dominated by locating the artifact, not the restore.

**Extrapolated full-restore estimate:** scaling ~13.7 MB-raw-SQL/s to the full 1.3 GB-gz
nightly set (~9-10 GB raw, `option_prices`-dominated): **~15-25 min pure MySQL load**,
plus decompress (~1-2 min, measured 774MB in 1.2s) — **under 1 hour** with
locate/verify overhead. (InnoDB secondary-index rebuild adds variance; >2h is an anomaly.)

**Full box-death RTO (bare box -> serving dashboard), estimated from measured components + documented costs:**

| Step | Estimate | Basis |
|---|---|---|
| Provision OS + Python 3.11, Node.js, MySQL 5.7 | 30-60 min | Manual/one-time, not measured |
| `git clone` the Trader repo | <5 min | Network-dependent |
| `pip install -r trader_api_requirements.txt`; `npm install` | 5-15 min | Standard install |
| Recreate `config.py` (external dep, section 1) | Unknown — **flagged, not measured** | Depends on user's own record of it |
| Restore latest weekly full dump | Scaled from section 4 timings to full-DB size | `trader_full.sql.gz` dominates |
| Layer daily irreplaceable dumps newer than weekly | Minutes (small per-table except `option_prices`) | |
| `trader recalculate --force --full` (only if derived tables stale) | ~25 min for 10y (traps.md) | Only if restoring older weekly + freshness gap |
| `C:\Development\server.bat start -Service all` -> `:3000` dashboard (backend `:5000`) | <2 min | `server.ps1`'s `Wait-Http` health-check bounds this to 120s |

**Overall RTO target: well under 1 day**, dominated by `option_prices` restore (also
dominates the nightly backup runtime) and by recreating `config.py` (the one unbounded
step). Meets gameplan P0.1 bar ("verified restore + documented RTO <1 day").

## 6. Bare-box -> serving dashboard sequence

1. Provision a Windows box; install Python 3.11 (`PYTHONUTF8=1` env var, see CLAUDE.md
   header), Node.js, MySQL 5.7 Server, Git.
2. `git clone <repo-url> C:\Development\Trader`.
3. `cd C:\Development\Trader && pip install -r trader_api_requirements.txt`.
4. `npm install` (root `package.json` — React 18 + Tailwind + Chart.js dashboard frontend).
5. **Recreate `config.py`** on `PYTHONPATH` (currently
   `C:\Development\Archivist\archiver\config.py` — flagged gap, section 1) with at least
   `DB_CONFIGS = {...}` and `TRADER_DB_NAME = 'trader'` for the new MySQL instance.
6. Install/start MySQL 5.7; `trader` schema + tables come from the restore in step 7
   (mysqldump output includes `CREATE TABLE`). If restoring irreplaceables-only with no
   weekly dump, first bring up the derived-table schema via each model's
   `ensure_schema()` (or the collective form in `C:\Development\server.ps1`'s
   `Write-TraderApiServer`: `Stock.ensure_schema()`, `Score.ensure_schema()`,
   `Option.ensure_schema()`, `OptionPrice.ensure_schema()`, etc.) before restoring data.
7. Restore the latest weekly full dump (section 3), then layer daily irreplaceable dumps newer than it.
8. If derived tables (`scores`/`indicators`/`weekly_*`) are stale vs. restored
   `price_history` (weekly dump several days old), run `python trader.py recalculate --force --full`.
9. Copy `.cache\task_queue.db` and `algorithm_versions\scoring_locks.json` from the
   backup into place (same relative paths in the fresh checkout).
10. Start the dashboard: `C:\Development\server.bat start -Service all` (or
    `-Service trader-api` / `-Service trader-frontend` individually) — Flask on `:5000`,
    React dev server on `:3000`, built-in health-check wait. Verify:
    `C:\Development\server.bat health -Service trader-api` -> `GET http://127.0.0.1:5000/health` should be 200.
11. (Optional) Bring up the Cloudflare tunnel (`-Service cloudflare`) for public access at
    `api.bagholders.ai`, and re-register the Task Scheduler jobs (queue daemon,
    `TraderUpdateViaQueue`, `TraderPortfolioNotify*`, `TraderBackupDaily`, and (once
    built) `TraderOpsHeartbeat`) via each `scripts\install_*.ps1` — see
    [task-queue.md](task-queue.md) for those.

## 7. Cloud copy — USER-CONFIG REQUIRED

**Not implemented.** Current posture (nightly local backup on largest non-C fixed volume,
14-day/8-week retention) protects against DB corruption, accidental deletion, bad
recalc/migration — NOT against the box dying/being stolen, or a ransomware/local-disk
event touching both `C:` and `D:`. A true 3-2-1 posture needs an off-box copy.

Deliberately not wired up autonomously — requires an account/credential decision only the
user can make (provider, bucket/remote, retention cost) and crosses the
entering-credentials boundary. Two ready-to-fill stubs:

**Option A — `robocopy` to a mapped network drive/NAS** (no cloud account if a NAS is on
the LAN already):
```powershell
# Run after backup_daily.ps1 completes (chained second queue submit, or a second Task
# Scheduler trigger +30 min). Mirrors daily+weekly trees; /MIR deletes remote files no
# longer present locally, enforcing the same retention as the local copy (intentional,
# as long as local retention (section 2) is trusted).
robocopy "D:\Backups\Trader\daily" "\\<nas-host>\<share>\Trader\daily" /MIR /Z /R:3 /W:10
robocopy "D:\Backups\Trader\weekly" "\\<nas-host>\<share>\Trader\weekly" /MIR /Z /R:3 /W:10
```
Fill in `<nas-host>`/`<share>`; if auth needed, map as a persistent credentialed drive via
Windows credential manager (`cmdkey`/`net use`) by hand, once — never put a password in this script.

**Option B — `rclone` to an object-storage remote** (S3/B2/GCS/etc.):
```powershell
# One-time setup (interactive, run by the user -- never scripted):
#   rclone config   # creates a named remote, e.g. "trader-backup-remote"
# Then, after backup_daily.ps1 completes:
rclone sync "D:\Backups\Trader\daily"  "trader-backup-remote:trader-backups/daily"  --fast-list
rclone sync "D:\Backups\Trader\weekly" "trader-backup-remote:trader-backups/weekly" --fast-list
```
`rclone config` stores credentials in rclone's own encrypted config — the user runs that step.

**To wire either in:** once the user picks A or B and completes one-time credential
setup, add the call as a fourth phase in `scripts\backup_daily.ps1` (after
manifest/retention) or as a separate `scripts\backup_offsite.ps1` chained via its own
`--dedup` queue submit so a flaky remote never blocks local backup's retention pruning.
The "second disk" half of P0.1's 3-2-1 requirement is done (section 2); "cloud copy" needs the user's provider choice.
