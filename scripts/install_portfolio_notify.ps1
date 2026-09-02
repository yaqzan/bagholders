# Registers the two scheduled portfolio alert passes (execution-timing canon):
#   TraderPortfolioNotifyMorning  Mon-Fri 08:45 -- carry-over digest + pre-open sells
#   TraderPortfolioNotifyClose    Mon-Fri 15:30 -- buy-window alerts + live sells
# Both run scripts\portfolio_notify.ps1 (~1 min). Re-run this script to update.
param(
    [string]$MorningTime = "08:45",
    [string]$CloseTime = "15:30"
)

$ErrorActionPreference = "Stop"
$wrapper = Join-Path $PSScriptRoot "portfolio_notify.ps1"
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

foreach ($t in @(
        @{ Name = "TraderPortfolioNotifyMorning"; At = $MorningTime },
        @{ Name = "TraderPortfolioNotifyClose";   At = $CloseTime })) {
    $trigger = New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $t.At
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    Write-Output "Registered '$($t.Name)' (Mon-Fri $($t.At))."
}

Write-Output "Verify : Get-ScheduledTask -TaskName TraderPortfolioNotify* | Get-ScheduledTaskInfo"
Write-Output "Remove : Unregister-ScheduledTask -TaskName TraderPortfolioNotifyMorning,TraderPortfolioNotifyClose -Confirm:`$false"
