# Registers TraderSkillOOSCacheRefresh -- the recurring refresh for the parquet caches the
# pre-registered 2026-12-15 OOS evaluation reads (PREREGISTRATION.md December pre-step 1 /
# known-gap note: caches built once and never invalidated => OOS n_window=0 => INSUFFICIENT_N).
#
# Fires WEEKLY, Saturdays 10:00 local (market closed; the wrapper additionally submits with
# --window off_market so even a manual weekday run is safe). Weekly rather than monthly on
# purpose: a missed firing (box off) self-heals within 7 days via -StartWhenAvailable + the next
# occurrence, so the caches are never more than ~a week stale going into December -- at the cost
# of ~15-25 min/week of low-priority off-market queue work. The December pre-step (final rebuild
# through the eval date) still applies; this just makes it a no-op-sized delta instead of a
# 6-month surprise.
#
# Each firing runs refresh_skill_caches_task.ps1, which only SUBMITS the rebuild through the
# task queue (dedup key skill-oos-cache-refresh) -- the queue does the actual work by priority.
# Mirrors the house pattern of install_reminder.ps1 / scripts/install_portfolio_notify.ps1.
#
# Idempotent -- re-run any time to re-register/update (-Force overwrites cleanly).
# Undo: Unregister-ScheduledTask -TaskName TraderSkillOOSCacheRefresh -Confirm:$false

$ErrorActionPreference = "Stop"
$wrapper = Join-Path $PSScriptRoot "refresh_skill_caches_task.ps1"
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 10:00

Register-ScheduledTask -TaskName "TraderSkillOOSCacheRefresh" -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Write-Output "Registered 'TraderSkillOOSCacheRefresh' -- weekly, Saturdays 10:00 local."
Write-Output "Verify : Get-ScheduledTask -TaskName TraderSkillOOSCacheRefresh | Get-ScheduledTaskInfo"
Write-Output "Remove : Unregister-ScheduledTask -TaskName TraderSkillOOSCacheRefresh -Confirm:`$false"
