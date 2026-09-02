# Registers per-user Task Scheduler jobs for the W5DTE forward paper tape
# (.horizon/w5dte-paper-tape/TASK.md; experiments/w5dte_ev/OWNER_SPEC.md lock #3).
#
# Two recurring jobs, no end date (this is an ongoing paper instrument, not a one-time
# pull like scripts/install_vendor_topups.ps1's vendor top-ups -- modeled on that script's
# registration pattern, not its lapse-driven EndBoundary):
#
#   TraderW5dteTapeEntry     Mon + Tue  17:40 local  ->  w5dte_tape.py --entry
#   TraderW5dteTapeOutcomes  Fri        17:50 local  ->  w5dte_tape.py --outcomes
#
# Both invoke the explicit Python 3.11 interpreter directly (py-launcher trap,
# CLAUDE.md "py.exe launcher defaults" -- a bare `python` on PATH or the `py` launcher can
# silently resolve to a different/newer interpreter than this repo's pinned 3.11), with
# working directory pinned to the repo root so w5dte_tape.py's own sys.path repo-root pin
# assertion (`assert os.path.isfile(REPO_ROOT / "CLAUDE.md")`) passes regardless of the
# Task Scheduler service's own default working directory.
#
# Registered per-user (LogonType Interactive, RunLevel Limited) exactly like
# TraderPolygonFinalTopup / TraderSharadarFinalTopup -- no elevation required, which
# matters because `schtasks /change` on an elevated task is not obtainable
# non-interactively in this repo (CLAUDE.md "Maintenance mode").
#
# PAPER ONLY. This task only ever runs w5dte_tape.py, which itself only ever writes under
# experiments/w5dte_tape/ and .horizon/w5dte-paper-tape/ -- zero production impact.
#
# Usage (NOT run by the build session that wrote this file -- the orchestrator installs
# after auditing the driver script):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\..\experiments\w5dte_tape\install_tape_task.ps1
#   (or, from the repo root:)
#   powershell -NoProfile -ExecutionPolicy Bypass -File experiments\w5dte_tape\install_tape_task.ps1
#
# Verify after installing:
#   Get-ScheduledTask | Where-Object { $_.TaskName -like 'TraderW5dteTape*' }
#   schtasks /run /tn TraderW5dteTapeEntry
#   schtasks /run /tn TraderW5dteTapeOutcomes
#
# Uninstall:
#   Unregister-ScheduledTask -TaskName TraderW5dteTapeEntry -Confirm:$false
#   Unregister-ScheduledTask -TaskName TraderW5dteTapeOutcomes -Confirm:$false

$ErrorActionPreference = "Stop"

$repo       = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe  = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe"
$driver     = Join-Path $repo "experiments\w5dte_tape\w5dte_tape.py"

if (-not (Test-Path $pythonExe)) { throw "python.exe not found at $pythonExe" }
if (-not (Test-Path $driver))    { throw "driver not found: $driver" }

function Register-TapeTask {
    param([string]$TaskName, [string]$ModeFlag, [string[]]$DaysOfWeek, [string]$At, [string]$Description)

    $action = New-ScheduledTaskAction -Execute $pythonExe `
        -Argument "`"$driver`" $ModeFlag" `
        -WorkingDirectory $repo

    # Recurring, no EndBoundary -- this is an ongoing forward instrument, not a one-time
    # pull (unlike scripts/install_vendor_topups.ps1's deliberately lapse-bounded jobs).
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DaysOfWeek -At $At

    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    # StartWhenAvailable: if the box is off/asleep at the trigger time, run at next wake
    # rather than silently skipping a week's entry/outcomes pass.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description $Description | Out-Null
    Write-Host "registered $TaskName  days=$($DaysOfWeek -join ',')  at=$At  mode=$ModeFlag"
}

Register-TapeTask -TaskName "TraderW5dteTapeEntry" -ModeFlag "--entry" `
    -DaysOfWeek @("Monday", "Tuesday") -At "17:40" `
    -Description "W5DTE forward paper tape -- Mon/Tue post-close entry pass. Paper only, zero production impact. See .horizon/w5dte-paper-tape/TASK.md"

Register-TapeTask -TaskName "TraderW5dteTapeOutcomes" -ModeFlag "--outcomes" `
    -DaysOfWeek @("Friday") -At "17:50" `
    -Description "W5DTE forward paper tape -- Friday post-close outcomes pass. Paper only, zero production impact. See .horizon/w5dte-paper-tape/TASK.md"

Write-Host ""
Write-Host "verify with: Get-ScheduledTask | Where-Object { `$_.TaskName -like 'TraderW5dteTape*' }"
Write-Host "dry-run now: & `"$pythonExe`" `"$driver`" --entry --dry-run"
Write-Host "dry-run now: & `"$pythonExe`" `"$driver`" --outcomes --dry-run"
