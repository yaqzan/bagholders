# Scheduled lightweight portfolio ALERT PASS (~1 min) -- execution-timing canon.
#
# The heavy ~20-min "Stock Daily Update" runs finish their portfolio alert pass
# at unpredictable times (15:00 run -> ~15:20, BEFORE the 15:25 buy window;
# 15:45 run -> ~16:05, AFTER the close). This wrapper runs `trader portfolio
# notify` -- a full ledger sync WITH pushes -- at precise times so alerts arrive
# when actionable:
#   08:45 ET  morning pass: carry-over buy digest + pre-open sells (sweeps /
#             hard-sell deadlines) -- 45 min before the open
#   15:30 ET  close pass: provisional buy alerts on the 15:00-run scores (the
#             92%-close-faithful vintage) + live sells -- ~28 min before close
# Time gates in portfolio_engine._pending_actions decide WHAT is pushable
# (BUY_ALERT_FROM_ET / MORNING_ALERT_FROM_ET); this script just runs on time.
#
# Install both scheduled tasks with scripts\install_portfolio_notify.ps1.

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# ---- MAINTENANCE-MODE GUARD ------------------------------------------------
# Skip while .cache\MAINTENANCE_MODE exists (scripts\maintenance_mode.py). Exit 0 so the
# scheduler records a clean run rather than a failure cascade.
if (Test-Path (Join-Path (Split-Path -Parent $PSScriptRoot) ".cache\MAINTENANCE_MODE")) {
    Write-Host "MAINTENANCE MODE active -- skipping ($(Get-Date -Format o))"
    exit 0
}


# Repo root is the parent of this scripts\ directory.
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

python trader.py portfolio notify @args
exit $LASTEXITCODE
