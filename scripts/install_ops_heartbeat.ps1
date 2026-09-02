# Registers (or replaces) a Windows Task Scheduler entry that runs
# scripts\ops_heartbeat.ps1 every 30 minutes, 24/7 (health checks must catch
# an overnight-dead queue daemon or a missed nightly backup, not just
# market-hours problems). Runs DIRECTLY via Task Scheduler -- NOT through the
# task queue -- because the heartbeat itself is fast (a handful of file
# reads, one short-timeout SELECT 1, one `trader queue status` shell-out),
# well within the "genuinely light foreground check" bound (see CLAUDE.md
# "Long-Running Compute").
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_ops_heartbeat.ps1
#
# See .claude\docs\disaster-recovery.md for what this feeds (backup age) and
# scripts\ops_heartbeat.ps1 itself for the full check list.
param(
    [string]$TaskName = "TraderOpsHeartbeat"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$heartbeatScript = Join-Path $repo "scripts\ops_heartbeat.ps1"
if (-not (Test-Path $heartbeatScript)) { throw "heartbeat script not found: $heartbeatScript" }

$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$heartbeatScript`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $repo

# Daily trigger at midnight with a 30-min repetition over a 24h duration --
# self-renews every day (same pattern as a legacy-mode interval trigger
# elsewhere in this directory, but unconditional: no market-hours start/end
# bound, since this must run all day every day).
$midnight = [datetime]::Today
$trigger = New-ScheduledTaskTrigger -Daily -At $midnight
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $midnight `
        -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Days 1)).Repetition

$settings = New-ScheduledTaskSettingsSet -DontStopOnIdleEnd -MultipleInstances IgnoreNew `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings `
    -Description "Every 30 min, 24/7: pipeline health check (update-age vs market calendar, post-market marker, queue daemon heartbeat, MySQL reachability, disk free, backup age). Push on failure + one 08:30 ET all-green digest. See .claude\docs\disaster-recovery.md." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName':"
Write-Output "  every 30 min, 24/7 (MultipleInstances=IgnoreNew prevents overlap if one run is slow)"
Write-Output "  action: powershell $argument"
Write-Output ""
Write-Output "Verify   : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Output "Run now  : Start-ScheduledTask -TaskName $TaskName"
Write-Output "Remove   : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Output "Last run : Get-Content .cache\ops_heartbeat\last_run.json"
