# 30-minute, 24/7 pipeline health check: update-age vs the real NYSE market
# calendar, the post-market close-pipeline done/failed marker, task-queue
# daemon heartbeat age, MySQL reachability, disk free (repo drive + backup
# drive), and backup age (reads scripts\backup_daily.ps1's own status marker
# -- see .claude\docs\disaster-recovery.md).
#
# Pushes on FAILURE (any red check) via the SAME channel `trader update` /
# close-update / portfolio already use (update_health.notify ->
# notifications.send_pushover) -- never a new alerting mechanism. Sends ONE
# all-green digest per day in the 08:30-09:00 ET window (includes days-to-go
# on the pre-registered 2026-12-15 OOS evaluation).
#
# This script is FAST (a handful of file reads, one short-timeout SELECT 1,
# one `trader queue status` shell-out) -- registered directly via Task
# Scheduler (scripts\install_ops_heartbeat.ps1), NOT through the task queue.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ops_heartbeat.ps1
#   ...-File scripts\ops_heartbeat.ps1 -DryRun                                    # compute + print, send no push
#   ...-File scripts\ops_heartbeat.ps1 -TestPostMarketRoot C:\nope-does-not-exist  # force post_market red (verification only -- use a valid DRIVE letter, an entirely nonexistent drive letter hits a different PS error path)
#   ...-File scripts\ops_heartbeat.ps1 -TestBackupStatusFile C:\nope.json         # force backup_age red (verification only)
param(
    [switch]$DryRun,
    [string]$TestPostMarketRoot,
    [string]$TestBackupStatusFile
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$repo = "C:\Development\Trader"
Set-Location $repo
# Task Scheduler reads the registry PATH fresh per run, and a leftover
# hermes-agent venv Scripts dir sits ahead of the real interpreter there --
# bare `python` resolves to that venv (missing trader/task_queue) instead of
# Python311, causing every scheduler-launched python call to silently
# ModuleNotFoundError. Force the real interpreter first for this process only.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runDir = Join-Path $repo ".codex\runs\ops_heartbeat_$stamp"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$log = Join-Path $runDir "run.log"

function Write-Json($path, $obj) {
  $obj | ConvertTo-Json -Depth 8 | Set-Content -Path $path -Encoding UTF8
}
function Log($msg) {
  # Write-Host, not Write-Output -- see backup_daily.ps1's Log for why
  # (avoids polluting any function's captured return value).
  $line = "[$(Get-Date -Format o)] $msg"
  Add-Content -Path $log -Value $line
  Write-Host $line
}

$statusDir = Join-Path $repo ".cache\ops_heartbeat"
New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
$lastRunFile = Join-Path $statusDir "last_run.json"
$digestSentMarker = Join-Path $statusDir "last_digest_date.txt"

# ---- ET clock + real NYSE trading-day check (reuse task_queue.resources /
# trader._is_nyse_closed_weekday -- do NOT reimplement a holiday calendar) ----
$etInfoPy = Join-Path $runDir "_et_info.py"
@'
import json
from task_queue import resources
import trader as _t
et = resources.eastern_now()
is_trading_day = (et.weekday() < 5) and not _t._is_nyse_closed_weekday(et.date())
print(json.dumps({
    "et_iso": et.isoformat(),
    "weekday": et.weekday(),
    "off_market": resources.is_off_market_now(),
    "is_trading_day": is_trading_day,
}))
'@ | Set-Content -Path $etInfoPy -Encoding UTF8
$etInfoRaw = & python $etInfoPy
if ($LASTEXITCODE -ne 0) { throw "failed to resolve ET clock via task_queue.resources: $etInfoRaw" }
$etInfo = $etInfoRaw | ConvertFrom-Json
$etNow = [datetime]::Parse($etInfo.et_iso)
Log "ET now: $($etInfo.et_iso)  off_market=$($etInfo.off_market)  is_trading_day=$($etInfo.is_trading_day)"

$checks = @()  # each: [ordered]@{ name; severity('green'|'yellow'|'red'); detail }
function Add-Check($name, $severity, $detail) {
  $script:checks += [ordered]@{ name = $name; severity = $severity; detail = $detail }
  Log "[$($severity.ToUpper())] $name -- $detail"
}

# ---- Check: MySQL reachable (reuses database.trader_database.DB -- the
# exact channel the whole app connects through) ----
$mysqlCheckPy = Join-Path $runDir "_mysql_check.py"
@'
import sys, time
try:
    from database.trader_database import DB
    t0 = time.time()
    DB.connect(reuse_if_open=True)
    DB.execute_sql("SELECT 1").fetchone()
    dt = time.time() - t0
    if not DB.is_closed():
        DB.close()
    print(f"OK {dt:.3f}")
    sys.exit(0)
except Exception as e:
    print(f"FAIL {type(e).__name__}: {e}")
    sys.exit(1)
'@ | Set-Content -Path $mysqlCheckPy -Encoding UTF8
$mysqlRaw = & python $mysqlCheckPy 2>&1
if ($LASTEXITCODE -eq 0) {
  Add-Check "mysql_reachable" "green" "$mysqlRaw"
} else {
  Add-Check "mysql_reachable" "red" "$mysqlRaw"
}

# ---- Check: disk free (repo/OS drive + the backup target drive) ----
# Backup-target selection MUST match backup_daily.ps1's own logic exactly
# (largest non-C fixed volume) so this check reports on the drive that
# actually matters.
$candidates = Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.DriveLetter -and $_.DriveLetter -ne 'C' }
$BackupDriveLetter = $null
if ($candidates) {
  $BackupDriveLetter = ($candidates | Sort-Object Size -Descending | Select-Object -First 1).DriveLetter
}
$BackupRoot = if ($BackupDriveLetter) { "${BackupDriveLetter}:\Backups\Trader" } else { "C:\Backups\Trader" }

function Add-DiskFreeCheck($driveLetter, $label, $minFreeGb) {
  # try/catch, not just -ErrorAction SilentlyContinue: an entirely nonexistent
  # drive letter (as opposed to a valid drive with a missing path) can raise a
  # TERMINATING parameter-binding error under this script's global
  # $ErrorActionPreference="Stop" that -ErrorAction alone does not catch --
  # confirmed live while testing this check (X: with no such volume at all).
  # No single check may be allowed to crash the whole heartbeat run.
  try {
    $vol = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
    $freeGb = [math]::Round($vol.SizeRemaining / 1GB, 1)
    $sev = if ($freeGb -ge $minFreeGb) { "green" } elseif ($freeGb -ge ($minFreeGb / 2)) { "yellow" } else { "red" }
    Add-Check "disk_free_$label" $sev "$freeGb GB free on ${driveLetter}: (want >= $minFreeGb GB)"
  } catch {
    Add-Check "disk_free_$label" "red" "volume ${driveLetter}: unreadable: $($_.Exception.Message)"
  }
}
Add-DiskFreeCheck "C" "os_repo" 20
if ($BackupDriveLetter -and $BackupDriveLetter -ne "C") {
  Add-DiskFreeCheck $BackupDriveLetter "backup_target" 50
}

# ---- Check: task-queue daemon heartbeat ----
try {
  $queueStatusRaw = (& python trader.py queue status 2>&1) -join "`n"
  if ($queueStatusRaw -match 'daemon:\s*\[OK\]\s*healthy\s+pid=(\d+)\s+heartbeat=(\d+)s ago') {
    $hbAge = [int]$Matches[2]
    $sev = if ($hbAge -le 120) { "green" } else { "red" }
    Add-Check "queue_daemon" $sev "pid=$($Matches[1]) heartbeat=${hbAge}s ago"
  } else {
    Add-Check "queue_daemon" "red" "daemon not healthy: $queueStatusRaw"
  }
} catch {
  Add-Check "queue_daemon" "red" "queue status check crashed: $($_.Exception.Message)"
}

# ---- Check: post-market close-pipeline done/failed marker ----
# Only require TODAY's marker once it's plausibly finished (close-update is
# scheduled ~16:30 ET; 17:00 gives buffer) on a trading day -- absence before
# that, or on a non-trading day, is expected and reported yellow, not red.
try {
  $pmScanRoot = if ($TestPostMarketRoot) { $TestPostMarketRoot } else { Join-Path $repo ".codex\runs" }
  $pmExpectedByNow = $etInfo.is_trading_day -and ($etNow.TimeOfDay -ge [timespan]"17:00:00")
  $pmDirs = @(Get-ChildItem -LiteralPath $pmScanRoot -Directory -Filter "post_market_daily_*" -ErrorAction Stop | Sort-Object Name -Descending)
  if ($pmDirs.Count -gt 0) {
    $latestPm = $pmDirs[0]
    $pmDateTag = if ($latestPm.Name -match 'post_market_daily_(\d{8})_') { $Matches[1] } else { "" }
    $isToday = ($pmDateTag -eq $etNow.ToString('yyyyMMdd'))
    $doneExists = Test-Path (Join-Path $latestPm.FullName "done.json")
    $failedExists = Test-Path (Join-Path $latestPm.FullName "failed.json")
    if ($failedExists -and $isToday) {
      Add-Check "post_market" "red" "today's run ($($latestPm.Name)) FAILED"
    } elseif ($doneExists -and $isToday) {
      Add-Check "post_market" "green" "today's run ($($latestPm.Name)) completed"
    } elseif ($pmExpectedByNow) {
      Add-Check "post_market" "red" "no successful run for today ($($etNow.ToString('yyyyMMdd'))) past 17:00 ET; latest on disk is $($latestPm.Name)"
    } else {
      Add-Check "post_market" "yellow" "today's run not due yet (latest on disk: $($latestPm.Name))"
    }
  } else {
    $sev = if ($pmExpectedByNow) { "red" } else { "yellow" }
    Add-Check "post_market" $sev "no post_market_daily run directories found under $pmScanRoot"
  }
} catch {
  # A missing/unreadable scan root (incl. a nonexistent drive) IS a real
  # finding worth surfacing -- always red, regardless of $pmExpectedByNow --
  # this is what the -TestPostMarketRoot forced-failure verification exercises.
  Add-Check "post_market" "red" "post-market scan root unreadable ($pmScanRoot): $($_.Exception.Message)"
}

# ---- Check: update-age vs the real market calendar ----
# score_intraday_logs is written 1:1 with every scoring pass (see CLAUDE.md
# "Intraday score audit log") -- MAX(logged_at) is a direct, cheap freshness
# signal for the whole update pipeline, not just the close.
$updateAgePy = Join-Path $runDir "_update_age.py"
@'
import json
from database.trader_database import DB
DB.connect(reuse_if_open=True)
row = DB.execute_sql("SELECT MAX(logged_at) FROM score_intraday_logs").fetchone()
if not DB.is_closed():
    DB.close()
print(json.dumps({"max_logged_at": str(row[0]) if row and row[0] else None}))
'@ | Set-Content -Path $updateAgePy -Encoding UTF8
$updateAgeRaw = & python $updateAgePy
if ($LASTEXITCODE -eq 0 -and $updateAgeRaw) {
  $updateAgeInfo = $updateAgeRaw | ConvertFrom-Json
  if ($updateAgeInfo.max_logged_at) {
    $lastUpdate = [datetime]::Parse($updateAgeInfo.max_logged_at)
    $ageMin = [math]::Round(($etNow - $lastUpdate).TotalMinutes, 1)
    # Active window: the documented intraday cadence runs 04:00-15:45 ET on
    # trading days (scripts\install_trader_update_via_queue.ps1's $Times:
    # 04:00,07:00,08:00,09:00,09:45,10:30,11:15,12:00,12:45,13:30,14:15,15:00,
    # 15:45). Most gaps are 45-60min, but there is ONE known 180min gap
    # (04:00->07:00, the pre-market window) -- a flat 90min tolerance would
    # false-positive every trading day between ~05:30-07:00 ET. 220min covers
    # that known gap + a completion/latency buffer while still catching a
    # genuinely stalled pipeline within a few hours, not a single missed slot
    # (this check's job is "is the pipeline dead", not "did slot N fire").
    $activeWindow = $etInfo.is_trading_day -and ($etNow.TimeOfDay -ge [timespan]"04:00:00") -and ($etNow.TimeOfDay -lt [timespan]"16:15:00")
    if ($activeWindow -and $ageMin -gt 220) {
      Add-Check "update_age" "red" "last score write $ageMin min ago (>220min during the trading-day update window)"
    } elseif ($activeWindow) {
      Add-Check "update_age" "green" "last score write $ageMin min ago"
    } else {
      Add-Check "update_age" "yellow" "last score write $ageMin min ago (outside the trading-day update window)"
    }
  } else {
    Add-Check "update_age" "red" "score_intraday_logs has zero rows"
  }
} else {
  Add-Check "update_age" "red" "update-age query failed: $updateAgeRaw"
}

# ---- Check: backup age (reads backup_daily.ps1's own _status marker) ----
$backupStatusFile = if ($TestBackupStatusFile) { $TestBackupStatusFile } else { Join-Path $BackupRoot "_status\last_success.json" }
if (Test-Path $backupStatusFile) {
  try {
    $bs = Get-Content $backupStatusFile -Raw | ConvertFrom-Json
    $finishedAt = [datetime]::Parse($bs.finished_at)
    $ageHours = [math]::Round(((Get-Date) - $finishedAt).TotalHours, 1)
    # >26h means yesterday's 03:00 nightly run never landed a success (24h
    # cadence + 2h buffer for a delayed/preempted-and-retried run).
    $sev = if ($ageHours -le 26) { "green" } elseif ($ageHours -le 48) { "yellow" } else { "red" }
    Add-Check "backup_age" $sev "last successful backup $ageHours h ago (mode=$($bs.mode), $($bs.total_mb) MB)"
  } catch {
    Add-Check "backup_age" "red" "backup status file unreadable: $($_.Exception.Message)"
  }
} else {
  Add-Check "backup_age" "red" "no successful-backup marker found at $backupStatusFile"
}

# ---- Check: symbols persistently failing their price pull ----
# trader.py records every FINAL pull failure to .cache\pull_failures.json and clears the
# entry on the next success. Without this check a symbol with a wrong vendor ticker goes
# stale forever in silence -- which is exactly what BF.B / BRK.B / SATS did until the
# 2026-07-29 Sharadar rebuild happened to expose it. One day of failure is noise (a
# transient vendor blip); several consecutive days is a broken mapping.
$pullFailFile = Join-Path $repo ".cache\pull_failures.json"
if (Test-Path $pullFailFile) {
  try {
    $pf = Get-Content $pullFailFile -Raw | ConvertFrom-Json
    $syms = @($pf.PSObject.Properties)
    if ($syms.Count -eq 0) {
      Add-Check "pull_failures" "green" "no symbols failing their price pull"
    } else {
      $worst = ($syms | ForEach-Object { [int]$_.Value.consecutive_days } | Measure-Object -Maximum).Maximum
      $names = ($syms | ForEach-Object { "$($_.Name)->$($_.Value.vendor_ticker)" }) -join ", "
      $sev = if ($worst -ge 3) { "red" } elseif ($worst -ge 2) { "yellow" } else { "yellow" }
      Add-Check "pull_failures" $sev "$($syms.Count) symbol(s) failing price pull (worst $worst consecutive days): $names"
    }
  } catch {
    Add-Check "pull_failures" "yellow" "pull-failure ledger unreadable: $($_.Exception.Message)"
  }
} else {
  Add-Check "pull_failures" "green" "no pull-failure ledger (nothing has failed)"
}

# ---- Push notification (reuses update_health.notify -> notifications.send_pushover
# -- the SAME channel trader update/close-update/portfolio already use) ----
# Payload passed via a JSON FILE, never inline -c/-Argument text: PowerShell's
# native-exe argument re-quoting was found (backup_daily.ps1) to silently
# strip embedded double-quotes when a script runs via `powershell -File`, the
# exact way Task Scheduler invokes this script.
$sendNotifyPy = Join-Path $runDir "_send_notify.py"
@'
import sys, json
with open(sys.argv[1], "r", encoding="utf-8") as f:
    payload = json.load(f)
from update_health import notify
notify(payload["title"], payload["message"], priority=payload.get("priority", 0))
'@ | Set-Content -Path $sendNotifyPy -Encoding UTF8

function Send-HeartbeatNotify($title, $message, $priority) {
  $payloadPath = Join-Path $runDir "_notify_payload_$([guid]::NewGuid().ToString('N')).json"
  Write-Json $payloadPath @{ title = $title; message = $message; priority = $priority }
  $result = & python $sendNotifyPy $payloadPath 2>&1
  Log "notify: $result"
}

$redChecks = @($checks | Where-Object { $_.severity -eq 'red' })
$overallOk = ($redChecks.Count -eq 0)
$oosDate = [datetime]"2026-12-15"
$daysToOos = [math]::Round(($oosDate - (Get-Date)).TotalDays, 0)
$summaryText = ($checks | ForEach-Object { "[$($_.severity.ToUpper())] $($_.name): $($_.detail)" }) -join "`n"

Write-Json $lastRunFile @{
  ran_at = (Get-Date).ToString("o")
  et = $etInfo.et_iso
  overall_ok = $overallOk
  checks = $checks
}

if (-not $DryRun) {
  if ($redChecks.Count -gt 0) {
    $title = "trader ops-heartbeat: $($redChecks.Count) check(s) FAILED"
    $msg = ($redChecks | ForEach-Object { "$($_.name): $($_.detail)" }) -join "`n"
    Send-HeartbeatNotify $title $msg 1
  }
  # One all-green digest per calendar day, only inside the 08:30-09:00 ET
  # window slot (this job runs every 30 min, so exactly one run per day lands
  # in that window under normal operation).
  $todayStr = $etNow.ToString('yyyy-MM-dd')
  $alreadySentToday = (Test-Path $digestSentMarker) -and ((Get-Content $digestSentMarker -Raw).Trim() -eq $todayStr)
  $in0830Window = ($etNow.TimeOfDay -ge [timespan]"08:30:00") -and ($etNow.TimeOfDay -lt [timespan]"09:00:00")
  if ($overallOk -and $in0830Window -and -not $alreadySentToday) {
    $title = "trader ops-heartbeat: all green"
    $msg = "$summaryText`n`nDays to Dec-2026 OOS eval (2026-12-15): $daysToOos"
    Send-HeartbeatNotify $title $msg -1
    Set-Content -Path $digestSentMarker -Value $todayStr -Encoding UTF8
  }
} else {
  Log "-DryRun: skipping all push notifications"
}

Log "overall: $(if ($overallOk) { 'ALL GREEN' } else { "$($redChecks.Count) FAILED: $(($redChecks | ForEach-Object { $_.name }) -join ', ')" })"
if (-not $overallOk) { exit 1 }
exit 0
