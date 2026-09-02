# Weekly forward_ranks catch-up (P0.D, gameplan-2026H2). Submits the idempotent
# era_conditioning ledger catch-up through the task queue so admission control
# applies; the script itself is day-atomic + resume-safe, so a missed week is
# healed by the next run. The ~Oct-2026 OSK forward_ranks read (PL-6a) needs the
# ledger <=7 days stale from install through January.
#
# Installed by scripts\install_forward_ranks_weekly.ps1 (per-user, no elevation).

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

# Maintenance-mode guard (same idiom as trader_update_via_queue.ps1): skip
# cleanly during a declared full-box repair; next week's run catches up.
if (Test-Path (Join-Path $repo ".cache\MAINTENANCE_MODE")) {
    Write-Host "MAINTENANCE MODE active -- skipping forward_ranks weekly submit ($(Get-Date -Format o))"
    exit 0
}

python trader.py queue submit --priority high --db heavy --cpu 2 --restartable `
    --dedup forward-ranks-weekly `
    --env PYTHONUTF8=1 --env PYTHONIOENCODING=utf-8 `
    --reason "P0.D weekly forward_ranks catch-up (era_conditioning ledger; ~Oct OSK read needs <=7d staleness)" `
    -- python experiments/era_conditioning/build_forward_ranks.py
exit $LASTEXITCODE
