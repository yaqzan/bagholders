# Registers (or replaces) a Windows Task Scheduler entry that enqueues the
# weekly P3.7 realized-slippage report (tools\slippage_report.py) as an
# idle-tier queue job. The scheduled action is the near-instant
# `trader queue submit` call itself; the report runs later under the queue's
# admission. Exact submit line and its rationale are documented in
# .claude\docs\incident-runbook.md "The real-fill loop" (Block B wrote the
# tool + doc and deferred scheduler registration to this scripts\ surface).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_slippage_report.ps1
#   ...-File scripts\install_slippage_report.ps1 -At 03:30
#
# Sunday 03:30 local mirrors the nightly backup's off-hours slot (backup runs
# 03:00 daily; this is small/read-only so the 30-min offset is mere courtesy).
param(
    [string]$At = "03:30",
    [string]$TaskName = "TraderSlippageReportWeekly"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$tool = Join-Path $repo "tools\slippage_report.py"
if (-not (Test-Path $tool)) { throw "slippage report tool not found: $tool" }

# Verbatim from incident-runbook.md "The real-fill loop" (weekly schedule section).
$argument = "trader.py queue submit --priority idle --db light --cpu 1 --restartable " +
            "--dedup slippage-report-weekly --window off_market " +
            "--reason `"weekly P3.7 realized-fill vs modeled-mark report`" " +
            "--by task-scheduler " +
            "-- python tools/slippage_report.py --json"

$action = New-ScheduledTaskAction -Execute "python" -Argument $argument -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At ([datetime]::Today.Add([timespan]::Parse($At)))

$settings = New-ScheduledTaskSettingsSet -DontStopOnIdleEnd -MultipleInstances IgnoreNew `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings `
    -Description "Weekly (Sun $At local): enqueue tools\slippage_report.py --json at idle priority via the task queue. Report-only P3.7 realized-fill vs modeled-mark comparison; see .claude\docs\incident-runbook.md 'The real-fill loop'." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName':"
Write-Output "  weekly, Sunday at $At local (StartWhenAvailable)"
Write-Output "  action: python $argument"
Write-Output ""
Write-Output "Verify : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Output "Run now: Start-ScheduledTask -TaskName $TaskName"
Write-Output "Remove : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
