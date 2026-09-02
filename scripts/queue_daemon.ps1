# Foreground wrapper for the task-queue daemon. Used by the scheduled-task
# installer, or run by hand to start the scheduler:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\queue_daemon.ps1
#
# The daemon is crash-safe and single-instance (a named mutex + SQLite CAS), so
# starting it twice is harmless — the second exits immediately.

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

# Optional tuning (uncomment / adjust on this box):
# $env:TRADER_QUEUE_CORE_BUDGET     = "7"          # schedulable cores (default cpu_count-1)
# $env:TRADER_QUEUE_DB_BUDGET       = "2"          # ~one heavy-DB job at a time
# $env:TRADER_QUEUE_PREEMPT_MODE    = "throttle"   # throttle | suspend | off
# $env:TRADER_QUEUE_UPDATE_WATCH_MIN = "60"        # alert if no update in N min during market hours
# $env:TRADER_QUEUE_RETENTION_DAYS  = "7"          # prune terminal tasks + artifacts after N days

# ---- RESOURCE POLICY ------------------------------------------------------
# Two independent flag files, both under .cache\ , both reversible by deletion.
#
# 1) RESEARCH_PRIORITY (standing policy, piloted 2026-07-29)
#    By default the daemon reserves headroom for the scheduled `trader update` and, during
#    RTH, forbids any non-CRITICAL job from outranking it. On this box research and
#    cross-agent compute are the higher-value work, and the update is cheap and re-runs on
#    a 45-minute cadence — so it is treated as the LOWER-priority item. It still runs; it
#    just no longer preempts.
#      MARKET_GUARD=0  drop the RTH rule that forces everything to yield to the update.
#                      `trader update` sits at the SCHEDULED tier (20), research at HIGH
#                      (10), so with the guard off the natural ordering already puts
#                      research first — no demotion of the update is needed.
#      CORE_BUDGET     stop reserving a core as "update headroom" (keep 1 for OS+daemon).
#      LOW_CORE_CAP    raise from cpu/4; the reserve existed to keep room free for the
#                      update, which no longer has priority.
#
#    DB_BUDGET IS DELIBERATELY LEFT ALONE. It is a MySQL-capacity limit, not a reservation
#    for the update, so raising it recovers nothing the policy freed — it only re-opens the
#    read_timeout zombie-query cascade in traps.md. This is the one constraint that stays.
#
# 2) MAINTENANCE_MODE (temporary, scripts\maintenance_mode.py)
#    Pauses the trading-day pipeline outright for a full-box repair. Implies everything in
#    (1). Separate flag so a repair can end without silently reverting the standing policy.
$cpu = [Environment]::ProcessorCount
$researchFlag = Join-Path $repo ".cache\RESEARCH_PRIORITY"
$maintFlag    = Join-Path $repo ".cache\MAINTENANCE_MODE"
if ((Test-Path $researchFlag) -or (Test-Path $maintFlag)) {
    $env:TRADER_QUEUE_MARKET_GUARD  = "0"
    $env:TRADER_QUEUE_CORE_BUDGET   = [string]($cpu - 1)
    $env:TRADER_QUEUE_LOW_CORE_CAP  = [string][int]([Math]::Max(1, $cpu * 3 / 4))
    $mode = if (Test-Path $maintFlag) { "MAINTENANCE" } else { "RESEARCH-PRIORITY" }
    Write-Host "${mode}: market guard OFF, cores=$($env:TRADER_QUEUE_CORE_BUDGET), low-cap=$($env:TRADER_QUEUE_LOW_CORE_CAP), db budget unchanged (MySQL-bound)"
}

python trader.py queue daemon @args
exit $LASTEXITCODE
