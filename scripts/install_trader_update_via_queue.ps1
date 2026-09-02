# Registers (or replaces) a Windows Task Scheduler entry that routes the
# scheduled `trader update` through the task queue (Model B). Run from an
# elevated PowerShell if you want it to run whether or not you're logged in.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_trader_update_via_queue.ps1
#   ...-File scripts\install_trader_update_via_queue.ps1 -IntervalMinutes 45 -StartTime 09:30 -EndTime 16:00
#
# NOTE ON TIME ZONE: Task Scheduler triggers fire on the box's LOCAL clock. The
# defaults assume the machine runs on US/Eastern (matching NYSE hours). If your
# box is on another zone, pass -StartTime/-EndTime in LOCAL time.

param(
    # Canonical cadence (default): discrete weekday triggers matching the
    # execution-timing canon -- premarket data runs (04:00/07/08/09) feed the
    # 08:45 alert pass; the 45-min market grid is offset so 15:00 finishes
    # ~15:20 and feeds the 15:30 buy pass (a 15:30-grid run would collide).
    [string[]]$Times = @('04:00','07:00','08:00','09:00','09:45','10:30',
                         '11:15','12:00','12:45','13:30','14:15','15:00','15:45'),
    # Legacy interval mode: pass -IntervalMinutes > 0 to use a flat repetition
    # grid instead of $Times (NOT recommended -- see note above).
    [int]$IntervalMinutes = 0,
    [string]$StartTime = "09:30",
    [string]$EndTime = "16:00",
    [string]$TaskName = "TraderUpdateViaQueue",
    [switch]$Close
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $repo "scripts\trader_update_via_queue.ps1"
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$wrapperArg = if ($Close) { " --close" } else { "" }
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`"$wrapperArg"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $repo

if ($IntervalMinutes -gt 0) {
    # Legacy: weekday trigger that repeats every $IntervalMinutes across the window.
    $start = [datetime]::Today.Add([timespan]::Parse($StartTime))
    $end = [datetime]::Today.Add([timespan]::Parse($EndTime))
    $duration = $end - $start
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $start
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $start `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
            -RepetitionDuration $duration).Repetition
    $triggers = @($trigger)
    $scheduleLabel = "every $IntervalMinutes min, $StartTime-$EndTime (local), Mon-Fri"
} else {
    # Canonical: discrete weekday triggers (no catch-up storms on wake).
    $triggers = $Times | ForEach-Object {
        New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
            -At ([datetime]::Today.Add([timespan]::Parse($_)))
    }
    $scheduleLabel = "at $($Times -join ', ') (local), Mon-Fri"
}

$settings = New-ScheduledTaskSettingsSet -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 40)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Description "Route trader update through the task queue (Model B) -- canonical cadence; 15:00 run feeds the 15:30 buy-alert pass" -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName':"
Write-Output "  $scheduleLabel"
Write-Output "  NOTE: disable the old raw 'Stock Daily Update' task if still enabled."
Write-Output "  NOTE: keep 'Stock Daily Close Update' (16:30) -- it runs the post-market pipeline."
Write-Output "  action: powershell $argument"
Write-Output ""
Write-Output "Verify : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Output "Run now: Start-ScheduledTask -TaskName $TaskName"
Write-Output "Remove : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Output ""
Write-Output "MultipleInstances=IgnoreNew + the queue's exclusive dedup + the Global\TraderUpdate"
Write-Output "mutex together guarantee no overlapping update run."
