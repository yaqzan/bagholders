# Registers (or replaces) the weekly forward_ranks catch-up task (P0.D).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_forward_ranks_weekly.ps1
#
# Registered per-user (LogonType Interactive, RunLevel Limited) exactly like
# TraderOOSEvalDue2026 / the vendor top-up tasks -- no elevation required.
# Saturday 10:30 local: off-market, after TraderSkillOOSCacheRefresh (Sat 10:00),
# so the two weekend maintenance jobs don't contend for the db budget at once.
param(
    [string]$TaskName = "TraderForwardRanksWeekly",
    [string]$At = "10:30"
)

$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repo "scripts\forward_ranks_weekly.ps1"
if (-not (Test-Path $runner)) { throw "runner not found: $runner" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $repo
$trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At ([datetime]::Today.Add([timespan]::Parse($At)))
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
# StartWhenAvailable: a box asleep at Sat 10:30 runs it at next wake instead of
# silently skipping the week (the ledger tolerates that, but why spend the slack).
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Weekly forward_ranks ledger catch-up via the task queue (P0.D, gameplan-2026H2; feeds the ~Oct-2026 + Jan-2027 OSK reads). See experiments/era_conditioning/." | Out-Null
Write-Host "registered $TaskName -> Saturdays $At (local)"
Write-Host "verify with: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
