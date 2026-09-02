# Registers (or replaces) a Windows Task Scheduler entry that keeps the
# task-queue daemon running: starts it at logon and restarts it on failure.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_queue_daemon.ps1
#
# The daemon is single-instance (named mutex + SQLite CAS), so this coexists
# safely with a manual `trader queue daemon`.

param(
    [string]$TaskName = "TraderQueueDaemon"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $repo "scripts\queue_daemon.ps1"
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`"" `
    -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -AtLogOn

# Run forever; restart on failure; never let TS start a second instance.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -DontStopOnIdleEnd

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Persistent Trader task-queue daemon (restart on failure)" -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName' (starts at logon, restarts on failure)."
Write-Output ""
Write-Output "Start now : Start-ScheduledTask -TaskName $TaskName"
Write-Output "Status    : python trader.py queue status"
Write-Output "Remove    : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Output ""
Write-Output "To run whether or not you're logged in, re-register with -User SYSTEM (or a"
Write-Output "service account) and a -AtStartup trigger; SYSTEM must have access to the repo + MySQL."
