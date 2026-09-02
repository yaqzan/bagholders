# Weekly OOS skill-cache refresh -- keeps the parquet caches the pre-registered 2026-12-15 OOS
# evaluation reads (experiments/holdout_oos_2026_12/PREREGISTRATION.md, December pre-step 1 /
# known-gap note) from rotting between now and the eval date:
#   .cache/experiment_data/skill_v{73,74,active}_allscores_2016.parquet   (H1/H2/H5 n_window)
#   .cache/experiment_data/price_feats_raw_2015.parquet                   (H1 momentum t_mom)
#
# Submits through the existing task queue (a parquet-cache build NEVER runs raw, per CLAUDE.md
# Long-Running Compute rules). --window off_market + --priority low means a firing during market
# hours just defers to after the close and never contends with `trader update`; the stable
# --dedup key means an already-queued/running refresh suppresses a duplicate (completed runs do
# NOT block the next week's submit -- the dedup index only covers active states).
#
# Invoked by the TraderSkillOOSCacheRefresh scheduled task (see install_refresh_task.ps1).
# Safe to run manually any time to trigger an off-market refresh.


# ---- MAINTENANCE-MODE GUARD ------------------------------------------------
# Skip while .cache\MAINTENANCE_MODE exists (scripts\maintenance_mode.py). Exit 0 so the
# scheduler records a clean run rather than a failure cascade.
if (Test-Path (Join-Path "C:\Development\Trader" ".cache\MAINTENANCE_MODE")) {
    Write-Host "MAINTENANCE MODE active -- skipping ($(Get-Date -Format o))"
    exit 0
}
$ErrorActionPreference = "Stop"
Set-Location "C:\Development\Trader"
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

python trader.py queue submit --priority low --window off_market --db heavy --cpu 1 `
    --restartable --timeout 2h --ttl 72h `
    --env PYTHONIOENCODING=utf-8 `
    --dedup skill-oos-cache-refresh `
    --by TraderSkillOOSCacheRefresh `
    --reason "Weekly OOS skill-cache refresh (holdout_oos_2026_12 December pre-step 1: keep H1/H2/H5 OOS n_window + H1 momentum parquet fresh)" `
    -- python experiments/skill_vs_baseline/rebuild_skill_caches.py
