# Registers the daily ct15-paper-sleeve scheduled task (.horizon\ct15-paper-sleeve\
# TASK.md): Mon-Fri 17:30 local -- post-close-pipeline, after trader close-update's
# option pull has had time to land today's option_prices rows.
# Runs scripts\ct15_tape_daily.ps1 (~seconds; a handful of Score/option_prices
# reads for the day's CT signals, see NOTES.md).
#
# Follows scripts\install_portfolio_notify.ps1's Register-ScheduledTask shape,
# but launches via scripts\hidden_run.vbs (wscript.exe //B //Nologo) -- the
# CURRENT canonical launcher for every Trader* scheduled task (see
# scripts\fix_scheduled_task_windows.ps1, and traps.md "Task Scheduler
# cwd=System32 + silent launchers" -- hidden_run.vbs anchors child cwd to the
# repo root so a lost/omitted WorkingDirectory can never break a repo-relative
# action). No console flash, no elevation needed (matches
# install_vendor_topups.ps1's per-user/Limited precedent).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_ct15_tape.ps1
#   ...-File scripts\install_ct15_tape.ps1 -At 17:30
param(
    [string]$At = "17:30",
    [string]$TaskName = "TraderCt15TapeDaily"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $repo "scripts\hidden_run.vbs"
$wrapper = Join-Path $repo "scripts\ct15_tape_daily.ps1"
if (-not (Test-Path $launcher)) { throw "launcher not found: $launcher" }
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$tokens = @(
    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $wrapper
)
$quoted = ($tokens | ForEach-Object { '"' + $_ + '"' }) -join ' '
$vbsArgs = '//B //Nologo "' + $launcher + '" ' + $quoted

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $vbsArgs
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Mon-Fri $At local: run .horizon\ct15-paper-sleeve\driver\ct15_tape.py for today's session (real-quote CT15 paper sleeve, both arms). See .horizon\ct15-paper-sleeve\TASK.md." `
    | Out-Null

Write-Output "Registered '$TaskName' (Mon-Fri $At local) via hidden_run.vbs -> ct15_tape_daily.ps1"
Write-Output ""
Write-Output "Verify    : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Output "Test-fire : schtasks /run /tn $TaskName"
Write-Output "Remove    : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
