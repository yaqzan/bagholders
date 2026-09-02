# Nightly (weekly-on-Sunday: full-DB) backup of the Trader MySQL database's
# irreplaceable tables, plus the task-queue state file and the scoring-locks
# fingerprint. Registered as a ~03:00 local Task Scheduler job whose ACTION is
# `python trader.py queue submit --priority idle ... -- powershell -File
# scripts\backup_daily.ps1` (idle-tier queue job, off_market window) -- this
# script is the queue PAYLOAD, never invoked directly by Task Scheduler, so
# mysqldump of the ~90M-row option_prices table always runs under the queue's
# CPU/DB admission (see CLAUDE.md "Long-Running Compute" / traps.md).
#
# See .claude/docs/disaster-recovery.md for the restore procedure, RTO, and
# the measured restore-drill timings.
#
# DB credentials are resolved dynamically via `config.py` (the SAME mechanism
# database/trader_database.py uses: config.DB_CONFIGS + config.TRADER_DB_NAME)
# -- NEVER hardcoded in this script. config.py lives outside this repo (a
# sibling project's untracked module on PYTHONPATH); we only ever read the
# DB-shaped keys out of it, in-memory, per run.
param(
  [switch]$Full,      # force a full whole-database dump regardless of day-of-week
  [switch]$DryRun      # print the plan (target dir, mode, tables) and exit 0
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$repo = "C:\Development\Trader"
Set-Location $repo
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

# ---- Config -------------------------------------------------------------
# The "irreplaceable" set per gameplan P0.1: the intraday score-swing audit
# log, earnings dates, the live-Portfolio forward ledger (4 tables), the
# stock universe, daily OHLCV, and option-chain history + prices.
#
# ORDER IS DELIBERATE: smallest/most-precious tables FIRST, the huge
# option_prices table LAST. A per-table dump is all-or-nothing (a mysqldump
# killed mid-write leaves no usable partial file), and this box's queue can
# legitimately preempt an idle-tier job for higher-priority work mid-run
# (observed live 2026-07-13: task-queue task #600 killed+requeued at ~6 min
# in by a normal-priority task needing the DB slot, auto-resumed after) -- so
# if a run gets cut short and retried more than once before finally
# completing, the live-money portfolio ledger and the intraday audit trail
# are captured within the first few seconds every attempt, not last.
$IrreplaceableTables = @(
  'score_intraday_logs', 'earnings_dates',
  'portfolio_runs', 'portfolio_positions', 'portfolio_pending_alerts',
  'portfolio_equity_snapshots', 'stocks',
  'options', 'price_history', 'option_prices'
)
$RetainDaily = 14
$RetainWeekly = 8

# Backup target: largest non-C fixed volume on this box (Get-Volume inventory,
# 2026-07-13: C=928GB/360GB-free OS; B="Home" 14.9TB; D="Archives" 342TB/
# 4.7TB-free -- picked D:). Flag if this ever falls back to C:\Backups (no
# other fixed volume found) -- that means the box only has one real disk and
# the "second disk" half of the 3-2-1 posture is not actually satisfied.
$candidates = Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.DriveLetter -and $_.DriveLetter -ne 'C' }
$BackupRoot = $null
if ($candidates) {
  $best = $candidates | Sort-Object Size -Descending | Select-Object -First 1
  $BackupRoot = "$($best.DriveLetter):\Backups\Trader"
} else {
  $BackupRoot = "C:\Backups\Trader"
  Write-Warning "backup_daily: NO non-C fixed volume found -- falling back to C:\Backups\Trader. This means backups live on the SAME physical disk as the working DB/repo; the 3-2-1 'second disk' requirement is NOT met. Flag for the user."
}

$mysqldump = "C:\Program Files\MySQL\MySQL Server 5.7\bin\mysqldump.exe"
if (-not (Test-Path $mysqldump)) { throw "mysqldump not found at $mysqldump -- update this path if MySQL was upgraded/moved." }

$isSunday = ((Get-Date).DayOfWeek -eq [System.DayOfWeek]::Sunday)
$mode = if ($Full -or $isSunday) { "weekly" } else { "daily" }
$dateTag = Get-Date -Format "yyyyMMdd"

if ($DryRun) {
  Write-Output "backup_daily DRY RUN: mode=$mode backup_root=$BackupRoot date=$dateTag"
  Write-Output "tables (daily set): $($IrreplaceableTables -join ', ')"
  Write-Output "weekly mode dumps the WHOLE trader database instead of the table list above."
  exit 0
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runDir = Join-Path $repo ".codex\runs\backup_daily_$stamp"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$log = Join-Path $runDir "run.log"
$status = Join-Path $runDir "status.json"
$done = Join-Path $runDir "done.json"
$failed = Join-Path $runDir "failed.json"

# Stable, predictable paths for scripts/ops_heartbeat.ps1 to cheap-check backup
# age without scanning .codex\runs history.
$statusDir = Join-Path $BackupRoot "_status"
New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
$lastAttemptFile = Join-Path $statusDir "last_attempt.json"
$lastSuccessFile = Join-Path $statusDir "last_success.json"

function Write-Json($path, $obj) {
  $obj | ConvertTo-Json -Depth 8 | Set-Content -Path $path -Encoding UTF8
}
function Log($msg) {
  # Write-Host (NOT Write-Output): several call sites are inside functions
  # whose return value is captured (e.g. $r = Invoke-MysqldumpToGz ...) --
  # Write-Output would join the log line into that function's output stream
  # and silently turn a scalar/hashtable return into an array. Write-Host
  # goes straight to the console, never the pipeline; the log FILE (via
  # Add-Content) remains the durable record either way.
  $line = "[$(Get-Date -Format o)] $msg"
  Add-Content -Path $log -Value $line
  Write-Host $line
}

$runStart = Get-Date
Write-Json $status @{ phase="starting"; state="running"; mode=$mode; backup_root=$BackupRoot; started_at=$runStart.ToString("o"); run_log=$log }
# This is the FIRST write to the backup volume and it sits OUTSIDE the main
# try/catch below -- when D:\Archive's DrivePool write-funnel seizes up
# (2026-08-20/21: whole-volume Access-denied while free space looked fine),
# an unguarded throw here killed the run in ~2s with an EMPTY run.log.
# Fail fast, but leave a breadcrumb first.
try {
  Write-Json $lastAttemptFile @{ started_at=$runStart.ToString("o"); mode=$mode; state="running" }
} catch {
  Log "FATAL: backup root $BackupRoot is NOT writable: $($_.Exception.Message)"
  Log "If this is D:\Archive under StableBit DrivePool, check pool/disk state (GUI or elevated dpcmd.exe); do NOT chase ACL fixes."
  Write-Json $failed @{ finished_at=(Get-Date).ToString("o"); exit_code=1; error="backup root not writable: $($_.Exception.Message)"; run_log=$log }
  try { Write-Json $lastAttemptFile @{ started_at=$runStart.ToString("o"); finished_at=(Get-Date).ToString("o"); mode=$mode; state="failed"; error="backup root not writable" } } catch { }
  exit 1
}

# ---- Resolve DB credentials the way database/trader_database.py does ----
# NOTE: the python snippets below are written to TEMP .py FILES and run as
# `python <file>.py`, never passed inline via `python -c "<string>"`. Passing
# a multi-line string with embedded double-quotes through PowerShell's native-
# exe argument re-quoting is unreliable (confirmed: it silently strips `"`
# characters when this script runs via `powershell -File`, as Task Scheduler
# does, even though the identical snippet works fine typed interactively) --
# a temp .py file sidesteps command-line quoting entirely.
$resolveConnPy = Join-Path $runDir "_resolve_db_conn.py"
@'
import config, json
kwargs = dict(config.DB_CONFIGS)
print(json.dumps({
    "host": kwargs.get("host") or "127.0.0.1",
    "port": int(kwargs.get("port", 3306)),
    "user": kwargs.get("user", "root"),
    "password": kwargs.get("password", kwargs.get("passwd", "")),
    "db": config.TRADER_DB_NAME,
}))
'@ | Set-Content -Path $resolveConnPy -Encoding UTF8

function Get-TraderDbConn {
  $json = & python $resolveConnPy
  if ($LASTEXITCODE -ne 0) { throw "failed to resolve DB credentials via config.py (exit $LASTEXITCODE): $json" }
  return $json | ConvertFrom-Json
}

try {
  $conn = Get-TraderDbConn
  Log "resolved DB conn: host=$($conn.host) port=$($conn.port) user=$($conn.user) db=$($conn.db) (password set: $([bool]$conn.password))"
} catch {
  Log "FATAL: could not resolve DB credentials: $($_.Exception.Message)"
  Write-Json $failed @{ finished_at=(Get-Date).ToString("o"); exit_code=1; error="credential resolution failed: $($_.Exception.Message)"; run_log=$log }
  Write-Json $lastAttemptFile @{ started_at=$runStart.ToString("o"); finished_at=(Get-Date).ToString("o"); mode=$mode; state="failed"; error="credential resolution failed" }
  exit 1
}

$targetDir = Join-Path $BackupRoot ("$mode\$dateTag")
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
Log "target dir: $targetDir (mode=$mode)"

# ---- mysqldump helper: dump -> raw .sql -> gzip via .NET -> delete raw ----
# (Start-Process with file-redirected stdout/stderr avoids the classic
# pipe-deadlock trap of manually pumping a child process's streams; gzip via
# System.IO.Compression.GZipStream needs no external gzip.exe on this box.)
function Invoke-MysqldumpToGz($argList, $outGzFile, $label) {
  $t0 = Get-Date
  $rawTmp = "$outGzFile.tmp.sql"
  $errFile = "$outGzFile.tmp.err"
  $prevPwd = $env:MYSQL_PWD
  try {
    if ($conn.password) { $env:MYSQL_PWD = $conn.password } else { Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue }
    $p = Start-Process -FilePath $mysqldump -ArgumentList $argList `
          -RedirectStandardOutput $rawTmp -RedirectStandardError $errFile `
          -NoNewWindow -PassThru -Wait
  } finally {
    if ($prevPwd) { $env:MYSQL_PWD = $prevPwd } else { Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue }
  }
  $errText = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { "" }
  if ($errText) { Add-Content -Path $log -Value "[$label stderr] $errText" }
  Remove-Item $errFile -ErrorAction SilentlyContinue
  if ($p.ExitCode -ne 0) {
    Remove-Item $rawTmp -ErrorAction SilentlyContinue
    throw "mysqldump failed for $label (exit $($p.ExitCode)): $errText"
  }

  $inStream = [System.IO.File]::OpenRead($rawTmp)
  $outStream = [System.IO.File]::Create($outGzFile)
  $gz = New-Object System.IO.Compression.GZipStream($outStream, [System.IO.Compression.CompressionLevel]::Optimal)
  try { $inStream.CopyTo($gz) } finally { $gz.Close(); $outStream.Close(); $inStream.Close() }
  Remove-Item $rawTmp -ErrorAction SilentlyContinue

  $rawSizeMb = 0
  $gzSizeMb = [math]::Round((Get-Item $outGzFile).Length / 1MB, 1)
  $elapsed = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
  Log "  $label -> $outGzFile ($gzSizeMb MB gz, ${elapsed}s)"
  return @{ table = $label; gz_bytes = (Get-Item $outGzFile).Length; seconds = $elapsed }
}

$failures = @()
$manifestTables = @()

# ---- Row-count snapshot (approximate, information_schema -- fast, no COUNT(*) scan) ----
$rowCounts = @{}
try {
  $rowCountsPy = Join-Path $runDir "_row_counts.py"
  @'
from database.trader_database import DB
import json
DB.connect()
rows = DB.execute_sql(
    "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema=%s",
    ("trader",),
).fetchall()
print(json.dumps({r[0]: int(r[1]) for r in rows}))
DB.close()
'@ | Set-Content -Path $rowCountsPy -Encoding UTF8
  $rcJson = & python $rowCountsPy
  if ($LASTEXITCODE -eq 0) { $rowCounts = $rcJson | ConvertFrom-Json }
} catch { Log "row-count snapshot skipped: $($_.Exception.Message)" }

if ($mode -eq "weekly") {
  Log "FULL weekly dump of database '$($conn.db)' starting..."
  try {
    $argList = @('-h', $conn.host, '-P', "$($conn.port)", '-u', $conn.user,
                 '--single-transaction', '--quick', '--hex-blob', '--routines', '--triggers',
                 $conn.db)
    $outGz = Join-Path $targetDir "trader_full.sql.gz"
    $r = Invoke-MysqldumpToGz $argList $outGz "trader_full(all tables)"
    $manifestTables += $r
  } catch {
    Log "FAIL trader_full: $($_.Exception.Message)"
    $failures += "trader_full ($($_.Exception.Message))"
  }
} else {
  Log "daily per-table dump of $($IrreplaceableTables.Count) irreplaceable tables starting..."
  foreach ($table in $IrreplaceableTables) {
    try {
      $argList = @('-h', $conn.host, '-P', "$($conn.port)", '-u', $conn.user,
                   '--single-transaction', '--quick', '--hex-blob', '--routines', '--triggers',
                   $conn.db, $table)
      $outGz = Join-Path $targetDir "$table.sql.gz"
      $r = Invoke-MysqldumpToGz $argList $outGz $table
      if ($rowCounts.PSObject.Properties[$table]) { $r.approx_rows = $rowCounts.$table }
      $manifestTables += $r
    } catch {
      Log "FAIL $table : $($_.Exception.Message)"
      $failures += "$table ($($_.Exception.Message))"
    }
  }
}

# ---- Non-DB irreplaceables: task-queue state + scoring-locks fingerprint ----
try {
  $tqSrc = Join-Path $repo ".cache\task_queue.db"
  if (Test-Path $tqSrc) {
    Copy-Item $tqSrc (Join-Path $targetDir "task_queue.db") -Force
    Log "  copied task_queue.db"
  } else {
    Log "  WARN task_queue.db not found at $tqSrc -- skipped"
  }
} catch {
  Log "FAIL task_queue.db copy: $($_.Exception.Message)"
  $failures += "task_queue.db copy ($($_.Exception.Message))"
}
try {
  $slSrc = Join-Path $repo "algorithm_versions\scoring_locks.json"
  if (Test-Path $slSrc) {
    Copy-Item $slSrc (Join-Path $targetDir "scoring_locks.json") -Force
    Log "  copied scoring_locks.json"
  } else {
    Log "  WARN scoring_locks.json not found at $slSrc -- skipped"
  }
} catch {
  Log "FAIL scoring_locks.json copy: $($_.Exception.Message)"
  $failures += "scoring_locks.json copy ($($_.Exception.Message))"
}

# ---- Manifest ----
$manifest = @{
  mode = $mode
  date_tag = $dateTag
  started_at = $runStart.ToString("o")
  finished_at = (Get-Date).ToString("o")
  backup_root = $BackupRoot
  target_dir = $targetDir
  tables = $manifestTables
  failures = $failures
}
Write-Json (Join-Path $targetDir "manifest.json") $manifest

# ---- Retention pruning (both trees, every run) ----
function Prune-Old($parentDir, $keep) {
  if (-not (Test-Path $parentDir)) { return }
  $dirs = Get-ChildItem $parentDir -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending
  if ($dirs.Count -gt $keep) {
    $toRemove = $dirs | Select-Object -Skip $keep
    foreach ($d in $toRemove) {
      Log "pruning old backup: $($d.FullName)"
      Remove-Item $d.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
}
try {
  Prune-Old (Join-Path $BackupRoot "daily") $RetainDaily
  Prune-Old (Join-Path $BackupRoot "weekly") $RetainWeekly
} catch {
  Log "WARN retention pruning error (non-fatal): $($_.Exception.Message)"
}

$totalBytes = ($manifestTables | ForEach-Object { $_.gz_bytes } | Measure-Object -Sum).Sum
$totalMb = [math]::Round(($totalBytes / 1MB), 1)
$elapsedTotal = [math]::Round(((Get-Date) - $runStart).TotalSeconds, 1)

if ($failures.Count -gt 0) {
  $msg = ($failures -join '; ')
  Log "backup_daily FAILED: $msg"
  Write-Json $status @{ phase="failed"; state="failed"; mode=$mode; finished_at=(Get-Date).ToString("o"); error=$msg; run_log=$log; total_mb=$totalMb }
  Write-Json $failed @{ finished_at=(Get-Date).ToString("o"); exit_code=1; error=$msg; run_log=$log; status_json=$status }
  Write-Json $lastAttemptFile @{ started_at=$runStart.ToString("o"); finished_at=(Get-Date).ToString("o"); mode=$mode; state="failed"; error=$msg; total_mb=$totalMb; target_dir=$targetDir }
  exit 1
}

Log "backup_daily OK: mode=$mode total=${totalMb}MB elapsed=${elapsedTotal}s target=$targetDir"
Write-Json $status @{ phase="done"; state="completed"; mode=$mode; finished_at=(Get-Date).ToString("o"); run_log=$log; total_mb=$totalMb }
Write-Json $done @{ finished_at=(Get-Date).ToString("o"); exit_code=0; run_log=$log; status_json=$status; total_mb=$totalMb }
$successPayload = @{ started_at=$runStart.ToString("o"); finished_at=(Get-Date).ToString("o"); mode=$mode; state="success"; total_mb=$totalMb; elapsed_s=$elapsedTotal; target_dir=$targetDir; tables=($manifestTables | ForEach-Object { $_.table }) }
Write-Json $lastAttemptFile $successPayload
Write-Json $lastSuccessFile $successPayload
exit 0
