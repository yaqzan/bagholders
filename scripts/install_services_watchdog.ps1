# Registers 'TraderServicesWatchdog': runs scripts\services_watchdog.ps1
# hourly + 2 min after logon. Probes trader-api (:5000), trader-frontend
# (:3000), and cloudflared, and starts only what's down (idempotent).
param(
    [int]$IntervalMinutes = 60,
    [string]$TaskName = "TraderServicesWatchdog"
)

$ErrorActionPreference = "Stop"
$wrapper = Join-Path $PSScriptRoot "services_watchdog.ps1"
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""

$start = (Get-Date).AddMinutes(5)
$tHourly = New-ScheduledTaskTrigger -Once -At $start `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
# scope the logon trigger to the current user (an all-users logon trigger
# requires elevation to register)
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$tLogon.Delay = 'PT2M'

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($tHourly, $tLogon) `
    -Settings $settings -Description "Revive dead trader services (api/frontend/cloudflare tunnel) -- probe first, start only what's down" -Force | Out-Null

$startLbl = $start.ToString('HH:mm')
Write-Output "Registered '$TaskName' (every $IntervalMinutes min from $startLbl + at logon)."
Write-Output "Log    : C:\Development\Trader\.cache\services_watchdog.log"
Write-Output "Remove : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
