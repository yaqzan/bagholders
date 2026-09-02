# Post-market daily pipeline: scores + portfolio confirms FIRST (so the live
# Portfolio alerts fire minutes after the close), THEN the options-chain pull,
# THEN the derived tail recalc (assess / temporal refresh).
#
# ORDERING (2026-06-18) -----------------------------------------------------------
# Phase 1 is `trader update` (options OFF), NOT `close-update`. The portfolio
# engine is the sole source of position open/close pushes, and it prices options
# with its own closed-form model — it reads nothing from option_prices. Folding
# the multi-hour options pull into the same pass (the old `close-update`) made
# the portfolio sync — hence every confirm / pre-close sell — wait HOURS behind
# the options pull (observed 2026-06-17: confirms fired at 21:25 for a 16:00
# close). Splitting it so the confirms fire right after scoring, then pulling
# options separately, keeps the dashboard's option_prices fresh without ever
# delaying the alerts. `update` + `pull-options` == the old `close-update`.
#
# QUEUE ROUTING (added 2026-06-05) ------------------------------------------------
# When the queue daemon is healthy, the WHOLE pipeline runs as ONE
# `scheduled`-priority, exclusive, heavy-DB queue job (this script re-invokes
# itself with -Worker). That gives the close proper CPU + MySQL admission and a
# clean heavy-DB slot, so it no longer collides on the tight-timeout MySQL with
# overnight research sweeps / recalcs (the cause of the multi-hour "stalls").
# When the daemon is down it runs INLINE — zero regression vs the pre-queue
# behavior. This mirrors the Model-B routing that
# scripts\trader_update_via_queue.ps1 gives `trader update`, but keeps all
# phases here (queue run-update --close would route close-update only and drop
# the options pull + derived tail).
param([switch]$Worker)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$repo = "C:\Development\Trader"
Set-Location $repo
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed. This was the
# actual cause of the 2026-08-07 16:30 failure (scores+portfolio /
# pull-options / derived-tail all ModuleNotFoundError'd identically).
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

if (-not $Worker) {
  # ---- Router mode: enqueue the whole pipeline if the daemon is up ----
  $self = $PSCommandPath
  $daemonHealthy = $false
  try {
    $st = (& python trader.py queue status 2>&1) -join "`n"
    if ($LASTEXITCODE -eq 0 -and $st -match 'healthy') { $daemonHealthy = $true }
  } catch { $daemonHealthy = $false }

  if ($daemonHealthy) {
    $submit = (& python trader.py queue submit `
        --priority scheduled --exclusive --db heavy --cpu 8 `
        --dedup trader-close --by task-scheduler `
        --reason "post-market close pipeline (close-update + derived tail)" `
        -- powershell -NoProfile -ExecutionPolicy Bypass -File $self -Worker 2>&1) -join "`n"
    Write-Output $submit
    $m = [regex]::Match($submit, 'task #(\d+)')
    if ($m.Success) {
      $id = $m.Groups[1].Value
      # Block until the job is terminal so the scheduled task surfaces its real
      # exit code (queue wait: 0=done, 1=failed/cancelled, 2=timeout).
      & python trader.py queue wait $id --timeout 6h
      exit $LASTEXITCODE
    }
    Write-Warning "post_market_daily: could not parse queue task id from submit output; running inline."
  } else {
    Write-Output "post_market_daily: queue daemon down; running close pipeline inline (no regression)."
  }
  # fall through to the inline worker body (daemon down OR submit-parse failure)
}

# ---- Worker body: the actual two-phase close pipeline ----
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runDir = Join-Path $repo ".codex\runs\post_market_daily_$stamp"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$log = Join-Path $runDir "run.log"
$status = Join-Path $runDir "status.json"
$done = Join-Path $runDir "done.json"
$failed = Join-Path $runDir "failed.json"

function Write-Json($path, $obj) {
  $obj | ConvertTo-Json -Depth 8 | Set-Content -Path $path -Encoding UTF8
}

# Phases are INDEPENDENT (fix 2026-06-29). A transient crash in Phase 1
# (`trader update` — e.g. the yfinance history-fetch cascade / "(0,'')" crash that
# killed the 6/26 run) must NOT skip Phase 2's options pull or Phase 3's tail: the
# options pull needs nothing from the score update, yet the old `throw` after
# Phase 1 starved option_prices for the whole day (root cause of stale options
# since 6/25). Each phase now runs regardless; failures are collected and the task
# still exits 1 so the scheduler surfaces the error.
$failures = @()

function Invoke-Phase($name, [scriptblock]$body) {
  Write-Json $status @{ phase=$name; state="running"; updated_at=(Get-Date).ToString("o"); run_log=$log }
  try {
    & $body
    if ($LASTEXITCODE -ne 0) { $script:failures += "$name (exit $LASTEXITCODE)" }
  } catch {
    $script:failures += "$name ($($_.Exception.Message))"
  }
}

# Phase 1: scores + portfolio sync (options OFF) — fires the live-Portfolio
# confirms / pre-close sells minutes after the close.
Invoke-Phase "scores+portfolio" { python -u trader.py update *>> $log }

# Phase 2: options-chain pull for the dashboard. INDEPENDENT of Phase 1 — never
# let a Phase-1 crash starve option_prices again.
Invoke-Phase "pull-options" { python -u trader.py pull-options *>> $log }

# Phase 3: derived tail (assess / temporal refresh).
Invoke-Phase "derived-tail" { python -u trader.py recalculate --tail-only --tail-window 5y *>> $log }

if ($failures.Count -gt 0) {
  $msg = ($failures -join '; ')
  Write-Json $status @{ phase="failed"; state="failed"; finished_at=(Get-Date).ToString("o"); error=$msg; run_log=$log }
  Write-Json $failed @{ finished_at=(Get-Date).ToString("o"); exit_code=1; error=$msg; run_log=$log; status_json=$status }
  exit 1
}
Write-Json $status @{ phase="done"; state="completed"; finished_at=(Get-Date).ToString("o"); run_log=$log }
Write-Json $done @{ finished_at=(Get-Date).ToString("o"); exit_code=0; run_log=$log; status_json=$status }
