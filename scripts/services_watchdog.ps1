# Services watchdog -- revives dead server-controller children (they're hidden
# persistent processes with NO auto-restart: trader-frontend died 2026-06-12 AM,
# cloudflared died 2026-06-12 PM -- each took the site/public domain down until
# noticed). Runs hourly + at logon via 'TraderServicesWatchdog' (installer:
# scripts\install_services_watchdog.ps1).
#
# Design: probe each service cheaply FIRST and only invoke the controller for
# services that are actually down (quiet when healthy; also avoids capturing
# controller output -- its Start-Process children hold inherited pipes open,
# the known foreground-hang class).

$ErrorActionPreference = 'Continue'
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path
$log = 'C:\Development\Trader\.cache\services_watchdog.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

function Test-Port([int]$Port) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $ok = $c.ConnectAsync('127.0.0.1', $Port).Wait(3000) -and $c.Connected
        $c.Close()
        return $ok
    } catch { return $false }
}

function Test-Url([string]$Url, [int]$TimeoutSec = 6) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -Method Get
        return $resp.StatusCode -eq 200
    } catch { return $false }
}

# Each check carries its OWN revive: not everything comes back via server.ps1.
function Revive-Service([string]$Name) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\Development\server.ps1' start -Service $Name | Out-Null
}

# queue-daemon added 2026-07-29. It is the single most important process on the box --
# every recalc/sweep/update is admitted through it -- and it was the ONLY one with no
# auto-revive: its scheduled task is "At logon time" with Next Run Time = N/A, so it fires
# once per logon and never again. On 2026-07-29 it died at 14:16 while launching the
# scheduled update, left an ORPHAN task record holding both DB slots, and nothing ran for
# an hour. The ops heartbeat correctly went RED at 14:31 and 15:01 -- so there was a
# detector and no responder. This is the responder.
function Test-QueueDaemon {
    try {
        Push-Location 'C:\Development\Trader'
        $out = & python.exe 'trader.py' queue status 2>$null
        Pop-Location
        return ([string]$out -match '\[OK\] healthy')
    } catch { try { Pop-Location } catch {}; return $false }
}

$checks = @(
    @{ Name = 'trader-api';      Up = { Test-Port 5000 }; Revive = { Revive-Service 'trader-api' } },
    @{ Name = 'trader-frontend'; Up = { Test-Port 3000 }; Revive = { Revive-Service 'trader-frontend' } },
    # NOT a bare `Get-Process -Name cloudflared` existence check (pre-2026-07-29 bug):
    # this box runs cloudflared for MULTIPLE tunnels (trader's own "trading-api" tunnel
    # AND scribe's separate "scribe" tunnel process). Any one of them existing made the
    # old check pass even when trader's specific tunnel was dead -- confirmed 2026-07-29:
    # trader's tunnel died at 18:17 (signal terminated), scribe's cloudflared kept running,
    # and this watchdog fired hourly reporting "all up" for 5+ hours while
    # api.bagholders.ai returned 530. Probe the public endpoint directly instead --
    # the same URL server.ps1 itself uses to decide healthy/FAIL.
    @{ Name = 'cloudflare';      Up = { Test-Url 'https://api.bagholders.ai/health' }; Revive = { Revive-Service 'cloudflare' } },
    @{ Name = 'queue-daemon';    Up = { Test-QueueDaemon }; Revive = { & schtasks.exe /run /tn '\TraderQueueDaemon' | Out-Null } }
)

$down = @($checks | Where-Object { -not (& $_.Up) })
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

if (-not $down) {
    Add-Content $log "$stamp  all up"
} else {
    foreach ($svc in $down) {
        Add-Content $log "$stamp  $($svc.Name) DOWN -> starting"
        # no output capture (see header); every revive is idempotent
        & $svc.Revive
    }
    Start-Sleep -Seconds 45   # give services time to come up before re-probe
    foreach ($svc in $down) {
        $state = if (& $svc.Up) { 'recovered' } else { 'STILL DOWN' }
        # queue-daemon can legitimately need longer: a new instance only takes the SQLite
        # singleton once the dead owner's heartbeat goes stale, so one 'STILL DOWN' at 45s
        # is expected, not a failure. The next hourly pass confirms.
        Add-Content $log "$stamp  $($svc.Name) post-start: $state"
    }
}

# keep the log small
$item = Get-Item $log -ErrorAction SilentlyContinue
if ($item -and $item.Length -gt 500KB) {
    Get-Content $log -Tail 300 | Set-Content $log
}
