# Registers TraderOOSEvalDue2026 -- the redundant hard trigger for the pre-registered 2026-12-15
# OOS evaluation (experiments/holdout_oos_2026_12/PREREGISTRATION.md, gameplan.md row P1.6).
#
# Fires daily at 08:00 local time, 2026-12-15 through ~2026-12-29 (a 14-day repeating window, not
# a single fragile one-shot) AND sets -StartWhenAvailable so a single missed occurrence (box off
# exactly at trigger time) still catches up. Each firing runs oos_due_ping.ps1, which submits a
# harmless echo marker through the existing task queue -- it does NOT attempt to auto-run the real
# evaluation. Mirrors the house pattern in scripts/install_portfolio_notify.ps1 (Register-
# ScheduledTask + a thin wrapper script), scoped under this experiment's own directory per the
# DECEMBER-PREREGISTRATION task's "new files only under experiments/holdout_oos_2026_12/" rule.
#
# Idempotent -- re-run this script any time to re-register/update (-Force overwrites cleanly).
# Undo: schtasks /delete /tn TraderOOSEvalDue2026 /f
#   (or: Unregister-ScheduledTask -TaskName TraderOOSEvalDue2026 -Confirm:$false)

$ErrorActionPreference = "Stop"
$wrapper = Join-Path $PSScriptRoot "oos_due_ping.ps1"
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$startAt = Get-Date -Year 2026 -Month 12 -Day 15 -Hour 8 -Minute 0 -Second 0
$trigger = New-ScheduledTaskTrigger -Once -At $startAt `
    -RepetitionInterval (New-TimeSpan -Days 1) -RepetitionDuration (New-TimeSpan -Days 14)

Register-ScheduledTask -TaskName "TraderOOSEvalDue2026" -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Write-Output "Registered 'TraderOOSEvalDue2026' -- daily 08:00, 2026-12-15 through ~2026-12-29."
Write-Output "Verify : Get-ScheduledTask -TaskName TraderOOSEvalDue2026 | Get-ScheduledTaskInfo"
Write-Output "Remove : Unregister-ScheduledTask -TaskName TraderOOSEvalDue2026 -Confirm:`$false"
