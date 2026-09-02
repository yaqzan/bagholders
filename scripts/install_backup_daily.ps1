# Registers (or replaces) a Windows Task Scheduler entry that enqueues the
# nightly Trader DB backup via the task queue (idle priority, heavy-DB grant,
# off_market-only window). The scheduled action is the `queue submit` call
# ITSELF -- a near-instant SQLite row insert -- not the backup work; the
# actual mysqldump/gzip of the irreplaceable tables (weekly-on-Sunday: the
# whole database) runs later, asynchronously, inside the always-on queue
# daemon. This keeps the ~90M-row option_prices dump off Task Scheduler's own
# execution-time-limit clock and under the queue's CPU/DB admission (see
# CLAUDE.md "Long-Running Compute" / traps.md "Harness background runner IS
# NOT the queue").
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_backup_daily.ps1
#   ...-File scripts\install_backup_daily.ps1 -At 03:30
#
# NOTE ON TIME ZONE: Task Scheduler triggers fire on the box's LOCAL clock
# (matches the other install_*.ps1 scripts in this directory).
#
# See .claude\docs\disaster-recovery.md for the restore procedure + RTO.
param(
    [string]$At = "03:00",
    [string]$TaskName = "TraderBackupDaily"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$backupScript = Join-Path $repo "scripts\backup_daily.ps1"
if (-not (Test-Path $backupScript)) { throw "backup script not found: $backupScript" }

$argument = "trader.py queue submit --priority idle --db heavy --cpu 2 --window off_market " +
            "--restartable --dedup trader-backup-daily --timeout 3h " +
            "--reason `"nightly backup: irreplaceable tables (weekly: full DB) + queue state + scoring locks`" " +
            "--by task-scheduler " +
            "-- powershell -NoProfile -ExecutionPolicy Bypass -File `"$backupScript`""

$action = New-ScheduledTaskAction -Execute "python" -Argument $argument -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.Add([timespan]::Parse($At)))

# ExecutionTimeLimit here bounds the SUBMIT call only (a fast DB insert) --
# NOT the backup job itself, which is bounded by the --timeout 3h passed to
# the queued task above and runs independently of this scheduled task.
$settings = New-ScheduledTaskSettingsSet -DontStopOnIdleEnd -MultipleInstances IgnoreNew `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings `
    -Description "Nightly at $At local: enqueue scripts\backup_daily.ps1 (mysqldump of the irreplaceable tables; full DB dump on Sunday) at idle priority via the task queue. See .claude\docs\disaster-recovery.md." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName':"
Write-Output "  daily at $At local (StartWhenAvailable: a missed 03:00 while asleep/off still fires on next wake)"
Write-Output "  action: python $argument"
Write-Output ""
Write-Output "Verify   : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Output "Run now  : Start-ScheduledTask -TaskName $TaskName"
Write-Output "Remove   : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Output "Backup status: Get-Content (backup root)\_status\last_success.json"
