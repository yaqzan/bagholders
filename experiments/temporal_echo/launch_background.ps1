param(
    [int]$LookbackDays = 1825,
    [int]$Variants = 120,
    [int]$MinGap = 1,
    [int]$MaxGap = 90,
    [ValidateSet("random", "drill")]
    [string]$Mode = "random",
    [double]$MaxWrDrop = [double]::NaN,
    [double]$MaxOptDrop = [double]::NaN,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RunRoot = Join-Path $Root ".cache\temporal_echo\runs"
New-Item -ItemType Directory -Force -Path $RunRoot | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $RunRoot $Stamp
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$LogPath = Join-Path $RunDir "run.log"
$ErrPath = Join-Path $RunDir "run.err.log"

$ArgsList = @(
    "-u", "experiments/temporal_echo/run_pipeline.py",
    "--lookback-days", "$LookbackDays",
    "--variants", "$Variants",
    "--min-gap", "$MinGap",
    "--max-gap", "$MaxGap",
    "--mode", "$Mode",
    "--run-dir", "$RunDir"
)
if (-not [double]::IsNaN($MaxWrDrop)) { $ArgsList += @("--max-wr-drop", "$MaxWrDrop") }
if (-not [double]::IsNaN($MaxOptDrop)) { $ArgsList += @("--max-opt-drop", "$MaxOptDrop") }
if ($Force) { $ArgsList += "--force" }

$p = Start-Process -FilePath "python" -ArgumentList $ArgsList -WorkingDirectory $Root -RedirectStandardOutput $LogPath -RedirectStandardError $ErrPath -WindowStyle Hidden -PassThru
$p.Id | Out-File -Encoding ascii (Join-Path $RunDir "pid.txt")
@{
    pid = $p.Id
    command = "python " + ($ArgsList -join " ")
    cwd = $Root
    run_log = $LogPath
    err_log = $ErrPath
    started_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $RunDir "launch_process.json")

$Monitor = @"
`$pidPath = '$($RunDir.Replace("'", "''"))\pid.txt'
`$runDir = '$($RunDir.Replace("'", "''"))'
`$pidValue = [int](Get-Content `$pidPath)
while (`$true) {
  `$done = Test-Path (Join-Path `$runDir 'done.json')
  `$failed = Test-Path (Join-Path `$runDir 'failed.json')
  if (`$done -or `$failed) { break }
  `$proc = Get-Process -Id `$pidValue -ErrorAction SilentlyContinue
  if (`$null -eq `$proc) {
    @{
      state='failed'
      finished_at=(Get-Date).ToUniversalTime().ToString('o')
      failure_reason='process exited without terminal artifact'
    } | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path `$runDir 'failed.json')
    break
  }
  Start-Sleep -Seconds 30
}
"@
$MonitorPath = Join-Path $RunDir "monitor.ps1"
$Monitor | Out-File -Encoding utf8 $MonitorPath
$mp = Start-Process -FilePath "powershell" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $MonitorPath) -WorkingDirectory $Root -WindowStyle Hidden -PassThru
$mp.Id | Out-File -Encoding ascii (Join-Path $RunDir "monitor_pid.txt")

Write-Output $RunDir
