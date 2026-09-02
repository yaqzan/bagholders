# Model-B action wrapper for the scheduled `trader update`.
#
# Point your Windows Task Scheduler "trader update" entry at THIS script instead
# of launching `trader.py update` directly. It routes update through the task
# queue (priority=scheduled, preempts low-priority work) when the queue daemon is
# healthy, and runs update INLINE — exactly today's behavior — when the daemon is
# down. So the trading-day pipeline never depends on the queue being up.
#
# Pass-through args are forwarded, e.g.:
#   powershell -NoProfile -ExecutionPolicy Bypass -File trader_update_via_queue.ps1 --close
#
# Install a scheduled task automatically with scripts\install_trader_update_via_queue.ps1.

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# Repo root is the parent of this scripts\ directory.
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

# ---- MAINTENANCE-MODE GUARD ------------------------------------------------
# Skip the pull entirely while .cache\MAINTENANCE_MODE exists (scripts\maintenance_mode.py).
# The guard lives here rather than in trader.py because this wrapper is the only thing the
# scheduled task invokes, so blocking it needs no change to the scoring pipeline -- and no
# admin rights, unlike disabling the task itself (schtasks /change returns Access Denied).
# Exit 0 so the scheduler records a clean run instead of a failure cascade.
if (Test-Path (Join-Path $repo ".cache\MAINTENANCE_MODE")) {
    Write-Host "MAINTENANCE MODE active -- skipping scheduled trader update ($(Get-Date -Format o))"
    exit 0
}

# ---- RESEARCH-CADENCE GUARD (owner-directed 2026-08-11; self-expires 2026-12-15) ----
# While .cache\RESEARCH_CADENCE exists, thin the intraday update cadence to two
# anchors -- the 04:00 and 09:45 triggers -- so research compute owns the box until
# the December OOS evaluation. The post-close pipeline (\Stock_Daily_Close_Update ->
# scripts\post_market_daily.ps1, 16:30) is a SEPARATE task and script, never touched
# by this guard: paper-ledger (G2) scoring, confirms, and the options pull keep
# running daily. Same no-admin idiom as the maintenance guard above. --close
# pass-throughs are never blocked (belt and suspenders). On/after 2026-12-15 the
# flag is ignored and full cadence resumes automatically.
$cadenceFlag = Join-Path $repo ".cache\RESEARCH_CADENCE"
if ((Test-Path $cadenceFlag) -and (($args -join ' ') -notmatch '--close')) {
    if ((Get-Date) -ge [datetime]'2026-12-15') {
        Write-Host "RESEARCH_CADENCE expired (2026-12-15) -- full cadence resumed; delete .cache\RESEARCH_CADENCE to silence this note"
    } else {
        $m = (Get-Date).Hour * 60 + (Get-Date).Minute
        $inWindow = (($m -ge 225) -and ($m -le 255)) -or (($m -ge 570) -and ($m -le 600))
        if (-not $inWindow) {
            Write-Host "RESEARCH_CADENCE active -- skipping intraday trader update outside 04:00/09:45 anchors ($(Get-Date -Format o))"
            exit 0
        }
    }
}

# Optional: enable the daemon's missing-update watchdog (alerts if an expected
# update never arrives during market hours). Uncomment to turn it on.
# $env:TRADER_QUEUE_UPDATE_WATCH_MIN = "60"

# --cpu 3, not 8. Measured 2026-07-29 (task #182): the per-stock loop at trader.py:1684 is
# SERIAL, and the worker ran at ~90% of ONE core for the whole 18m19s run. The old grant of 8
# reserved seven cores that sat idle while research work queued behind it — the opposite of
# the RESEARCH_PRIORITY policy. 3 leaves headroom for the threaded options pulls and the
# post-update phases without hoarding.
#
# NOTE: raising this does NOT make the update faster; only parallelising the loop would, and
# that multiplies MySQL load against a deliberately tight db budget (2). The update's real
# cost to the box is the db=heavy hold for its full duration, not its cores.
python trader.py queue run-update --cpu 3 @args
exit $LASTEXITCODE
