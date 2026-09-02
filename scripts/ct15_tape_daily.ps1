# Daily driver for the ct15-paper-sleeve instrument (.horizon/ct15-paper-sleeve/).
# Runs the real-quote paper-tape driver for TODAY's session (local clock date --
# matches the other install_*.ps1 scripts in this directory). The driver's own
# is_trading_day() guard no-ops cleanly on weekends/holidays, so this wrapper
# does not need its own trading-day check.
#
# Light job -- runs directly (NOT via trader queue submit), same class as
# scripts\portfolio_notify.ps1 (single-day signal lookup + a handful of
# option_prices reads; see .horizon\ct15-paper-sleeve\TASK.md "daily
# increment is light. Queue any heavy backfill.").
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ct15_tape_daily.ps1
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo   # defensive -- immune to a lost/omitted WorkingDirectory even
                      # without the hidden_run.vbs cwd anchor (traps.md 2026-08-11)

$python = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe"
$driver = Join-Path $repo ".horizon\ct15-paper-sleeve\driver\ct15_tape.py"
$today = (Get-Date).ToString("yyyy-MM-dd")

& $python $driver --date $today
exit $LASTEXITCODE
