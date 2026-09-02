# Incident Runbook

**P3.8.** No broker/trade automation — alerts + manual execution only (gameplan.md §8
anti-goals). This doc is "stop the alerts, coherently, in under 5 minutes." Triage index
with exact commands, Symptom → Check → Fix → Verify, cross-referencing
[debug-pipeline](../skills/debug-pipeline/SKILL.md), [queue-ops](../skills/queue-ops/SKILL.md),
[portfolio-ops](../skills/portfolio-ops/SKILL.md), and [traps.md](traps.md).

**First move:** if capital is live and something feels off, pull the kill-switch before
investigating — costs nothing, pure sync-behavior, no data mutation:
```bash
trader portfolio pause     # suppresses BUY pushes + halts new model entries; exits/sweeps/sell alerts stay live
# ... investigate ...
trader portfolio resume    # restores new entries + BUY pushes
```
Contract detail at bottom ("The kill-switch").

---

## 1. Score-looks-wrong

**Symptom:** dashboard/API score looks implausible — stale, unexplained jump, frozen
symbol, mismatched vs today's price action.

Pipeline reasons (missing write, stale regime, blocked write) belong here. Formula
forensics on a score that DID compute (intraday swing/fakeout attribution, version
confusion) → [/debug-scores](../skills/debug-scores/SKILL.md) instead.

**Check — is today's score present/current?**
```bash
python trader.py score-missing            # recovers symbols missing today's score, or
                                           # scored before a later price re-pull
                                           # (pulled_at > Score.updated_at + 5min)
python trader.py score-missing AAPL MSFT
GET /api/stocks/<sym>                     # confirm today's date + active version
```

**Check — did `trader update` run, or is it queued/blocked?**
```bash
python trader.py queue status             # daemon [OK]/[DOWN]?
python trader.py queue list --all --limit 20
```
`trader queue run-update` falls back to running inline when the daemon is down
(zero-regression design) — a missing update means an actual process crash or a
`Global\TraderUpdate` named-mutex collision (two updates/recalcs launched the same way
can't overlap; one silently no-ops), not just "queue was down."

**Check — write refused by the scoring-lock guard?** Symptom:
`ScoringVersionMismatch: Refusing score write...` — live scoring config drifted from
the fingerprint recorded for the active version (classic trigger: `CALIBRATION_CUTOFF_DATE`
or another locked `SCORING` constant changed without re-capturing the lock).
```bash
python tools/check_scoring_version_integrity.py   # asserts live config matches the lock
```
If drift is INTENTIONAL, re-capture in the SAME commit, then commit
`algorithm_versions/scoring_locks.json`:
```python
from database.scoring_version_guard import capture_lock
from database.models.core import AlgorithmVersion
capture_lock(AlgorithmVersion.get_or_create_current(), note="why")
```
If UNINTENTIONAL, restore the config instead of re-locking — re-locking a genuine
regression bakes the bug in. **No `trader algorithm lock-scoring` CLI verb exists**
despite the error text — `capture_lock` is Python-only.

**Check — stuck on a stale regime multiplier?** `trader update` scores each stock on the
LAST-AVAILABLE `MarketRegime` row, then after all stocks score, computes fresh regime and
calls `reapply_regime_today(...)` to patch every `Score` row atomically. Confirm this ran
— logs to `ScoreIntradayLog` with `source='regime_reapply_today'`.
```bash
GET /api/market/regime      # latest MarketRegime
GET /api/market/breadth     # latest MarketBreadth (?days=N for history)
python trader.py breadth-backfill [days]   # oldest-to-newest, EMA chains need order
python trader.py regime-backfill [days]
```

**Fix — the actual score gap** (queue anything beyond a same-day single-symbol fix):
```bash
python trader.py update                          # today only, options OFF -- ~45min full-universe SCHEDULED job, queue it
python trader.py recalculate 1d                   # today's rows under the current version
python trader.py recalculate SYM_A SYM_B 30d       # targeted backfill
python trader.py queue submit --priority high --db heavy --cpu 4 -- python trader.py update
```

**Verify:** `python trader.py explain-scores SYM 1` (CORRECT/BAD_LUCK/MISS/PENDING) or
`GET /api/stocks/<sym>` shows today's date + version, value consistent with the regime/breadth check.

---

## 2. Pipeline-dead

**Symptom:** no scores landed today, `trader update` didn't run, post-market close
pipeline didn't complete (dashboard options stale, Portfolio confirms never fired).

**Check — the scheduled close pipeline, phase by phase.** `scripts/post_market_daily.ps1`
(Task Scheduler `Stock Daily Close Update`) runs **3 INDEPENDENT phases** since
2026-06-29 — a phase-1 crash does NOT skip phases 2/3:

| Phase | Command | Purpose |
|---|---|---|
| 1 | `trader.py update` (options OFF) | scores + live-Portfolio confirms — buy/sell pushes minutes after close |
| 2 | `trader.py pull-options` | options-chain pull for dashboard |
| 3 | `trader.py recalculate --tail-only --tail-window 5y` | derived tail: assess + temporal refresh |

```bash
python trader.py queue show <id>          # if daemon-healthy this ran as ONE
                                           # scheduled/exclusive/db=heavy job, dedup=trader-close
```
Worker also writes `.codex/runs/post_market_daily_<timestamp>/status.json` (`phase` +
`state`) and `failed.json` (per-phase failure list, e.g. `"scores+portfolio (exit 1)"`) —
read directly if the queue log is unhelpful. Script exits 1 if ANY phase failed, so a
nonzero exit alone doesn't say which phase broke.

**Fix — rerun just the failed phase(s):**
```bash
python trader.py update                                        # phase 1
python trader.py pull-options                                  # phase 2
python trader.py recalculate --tail-only --tail-window 5y      # phase 3
python trader.py queue submit --priority high --db heavy -- python trader.py update
```
Phase 1 is itself the ~45-min full-universe job and phase 3 is a 5y tail recalc — queue
both, don't run raw (traps.md "harness background runner is NOT the queue").

**Check — did it not even start?** Confirm Task Scheduler entry enabled and queue daemon
healthy (§3). A dead daemon with a submit-parse failure falls through to running inline
per the script's own router, so "queue down" alone rarely explains a fully-silent day —
look for the Task Scheduler task disabled/missing, or the box down (§4).

**Verify:**
```bash
GET /api/portfolio/state    # fresh last_synced_at
GET /api/stocks/<sym>       # today's score present
```
Dashboard options current; `trader portfolio pending` shows recent `last_completed_session`.

---

## 3. Queue-thrash

**Symptom:** task repeatedly killed+requeued, daemon heartbeat stuck, resources used but
nothing visibly running, or a heavy job bypassed the queue and collides with the scheduled update.

**Check — daemon health first:**
```bash
python trader.py queue status
```
`daemon:` line: `[OK] ...` vs `[DOWN] <reason> pid=... heartbeat=... ago`. Heartbeat stale
by tens of seconds during a heavy tick is noise; growing for minutes, or `[DOWN]` with no
pid, means the process died.

**Fix — daemon down:**
```bash
python trader.py queue daemon           # foreground; or installed task scripts/queue_daemon.ps1
python trader.py queue tick             # one reconcile pass then exit -- cron-mode fallback
```
Restarting the daemon does NOT kill running children — new instance adopts them next
tick. To pick up a CODE CHANGE to `task_queue/daemon.py` (e.g. market-hours-guard fix),
bounce the process — no hot-reload:
```powershell
# find + kill the daemon PROCESS ONLY (children survive, new daemon adopts them);
# Stop-ScheduledTask does NOT kill a detached daemon -- need Stop-Process on the pid:
Stop-Process -Id <daemon_pid> -Force
Start-ScheduledTask -TaskName TraderQueueDaemon
```
Prefer an idle window — restart briefly stops admission (queued waits, running unaffected).

**Check — genuine repeated preemption, or a bug?**
```bash
python trader.py queue show <id>        # resources, flags, attempts, pid, last 12 events, run.log tail
python trader.py queue logs <id> --lines 40 --stderr
```
Preemption mechanics: core scarcity throttles the lowest-priority strictly-lower running
victim to 1-core affinity (reversible); DB scarcity kills+requeues a **restartable**
lower-priority DB-holder (killing releases a MySQL connection). Eligibility uses BASE
priority, not aged/effective (a prior livelock fix — don't revert). Repeated cycles on the
SAME task id with no resource pressure is the anomaly to chase, not the mechanism.

**Check — orphaned processes after preemption** (queue shows the slot free but MySQL
still hammered): daemon's `kill_tree` (psutil parent-chain walk) can miss a deep
git-bash→bash→python cluster if an intermediate process reparented. Env-tag reaping
(`TRADER_QUEUE_TASK_ID`/`TRADER_QUEUE_ATTEMPT` + `_sweep_tagged_orphans()` every ~90s)
should catch this automatically; manual reap if orphans persist:
```bash
python trader.py queue hold <id>        # FIRST -- else the daemon respawns it
```
```powershell
# trace ParentProcessId to the top surviving process, then:
taskkill /PID <top-live-bash-pid> /T /F
```

**Check — heavy job bypassed the queue?** Symptom: `queue status` shows near-zero
cores/db used, box visibly saturated. Root cause: `ScoreSimulator`/`recalculate`/`assess`/
Monte Carlo/research-pack launched via the harness's raw `run_in_background` (or any
detached process) instead of `trader queue submit` — daemon has no visibility, can admit
a `scheduled` `trader update` on top, colliding on MySQL. No retroactive adoption — let it
finish (or kill it), then re-submit:
```bash
python trader.py queue submit --priority high --db heavy --cpu 4 \
  --dedup <stable-key> --reason "<why>" -- <the actual command>
```

**Check — cheap housekeeping scan:**
```bash
python trader.py queue audit --idle-hours 6.0 --show-failed   # read-only
python trader.py queue audit --idle-hours 6.0 --show-failed --fix   # requests-cancel idle/stale
```

**Verify:** `queue status` shows `[OK]` fresh heartbeat; `queue list` shows RUNTIME
increasing (not stuck); resources charged match what's running.

---

## 4. Box-dying

**Symptom:** machine unresponsive/degraded, or services that should be running
(backend, frontend, tunnel, queue daemon) are down — distinct from §2/§3 (box fine, one
component broken).

**Check — always-on services, cheaply, first:**
```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 5000   # trader-api
Test-NetConnection -ComputerName 127.0.0.1 -Port 3000   # trader-frontend
Get-Process -Name cloudflared -ErrorAction SilentlyContinue   # public tunnel
python trader.py queue status                            # queue daemon
```
`TraderServicesWatchdog` task already probes trader-api/trader-frontend/cloudflared
hourly + 2 min after logon and starts what's down (`scripts/services_watchdog.ps1`, log
`.cache\services_watchdog.log`) — check that log first, may have self-healed already.

**Fix — restart a specific service** (the ONLY supported way, traps.md — load-bearing):
```powershell
# MUST run via PowerShell tool with run_in_background: true, or it hangs the agent
# foreground forever (hidden persistent child holds stdout open). Do NOT use Bash
# `cmd.exe /c "server.bat ..."` -- prints the cmd banner, no-ops in ~0s exit 0 while
# OLD code stays loaded (false-success trap, worse than a hang).
& C:\Development\server.bat restart -Service trader-api
& C:\Development\server.bat start -Service trader-frontend
& C:\Development\server.bat start -Service cloudflared
```
`server.bat` lives at `C:\Development\server.bat` (parent dir, NOT in this repo),
dispatching to `C:\Development\server.ps1`. Restart `trader-api` after ANY edit to
`api.py`, `strategy_config.py`, `backtest_cascade.py`, or `portfolio_param_manifest.py`
— frontend hot-reloads on its own.

**Fix — queue daemon isn't `server.bat`-managed** (separate Task Scheduler entry,
`TraderQueueDaemon`):
```powershell
Stop-Process -Id <daemon_pid> -Force   # pid from queue status
Start-ScheduledTask -TaskName TraderQueueDaemon
```

**Check — disk space and MySQL reachability** (full disk or dead DB looks like
"everything broken" from the app layer):
```powershell
Get-Volume | Where-Object DriveType -eq Fixed | Select-Object DriveLetter, SizeRemaining, Size
Get-Service -Name MySQL* | Select-Object Name, Status
```

**Check — after a genuine reboot**, confirm every scheduled task re-registered and is
enabled (a task without `-StartWhenAvailable`, or scoped to a session that didn't log
back in, silently never fires):
```powershell
Get-ScheduledTask -TaskName TraderQueueDaemon, TraderServicesWatchdog, `
  TraderUpdateViaQueue, TraderPortfolioNotifyMorning, TraderPortfolioNotifyClose | `
  Select-Object TaskName, State
```

**Data safety:** nightly backup (`scripts/backup_daily.ps1`, idle-tier queue ~03:00,
dumps `score_intraday_logs`, `earnings_dates`, the four live-Portfolio tables, `stocks`,
`price_history`, `option_prices`, plus `.cache/task_queue.db` + `scoring_locks.json` to a
second fixed volume) is the P0.1 answer to full box death. Full restore procedure/RTO:
[disaster-recovery.md](disaster-recovery.md).

**Verify:** all `Test-NetConnection`/`Get-Process` checks pass; `GET http://127.0.0.1:5000/health`
returns 200; `trader queue status` shows `[OK]`; watchdog log's next hourly line reads "all up".

---

## The kill-switch — `trader portfolio pause` / `resume`

**Contract** (portfolio_engine.py, `PortfolioRun.sync_paused`): pure run-level
sync-behavior flag, no data mutation.

- **Suppresses:** BUY-flavored pushes (provisional entry alerts, carry-over morning
  digest) AND halts new model entries (same entry-gate `_open_entries_for_day` uses for
  the P3.1 sprint watchdog, OR'd with this flag).
- **Never touches:** open positions, exits, sweeps, dead-hold pop/expiry, sell/exit
  pushes — stay fully live while paused.
- **Never mutates data:** no position/equity/snapshot row written — only `sync_paused`.

```bash
trader portfolio pause              # suppress BUY pushes + halt new entries
trader portfolio pending            # verify: would_open unaffected but meta.sync_paused=true, no buy-kind ALERT lines
trader portfolio resume             # restore new entries + BUY pushes
trader portfolio pending            # verify: meta.sync_paused=false, alerts resume
```

**Verified end-to-end 2026-07-13** (P3.8 ship commit): pause → `pending` showed
`sync_paused: true`, zero buy-kind alerts despite qualifying signals in `would_open` →
resume → `sync_paused: false`, buy alerts resumed under normal execution-window rules.

**Not `sprint-clear`** ([portfolio-ops](../skills/portfolio-ops/SKILL.md) / gameplan.md
P3.1): pause/resume is the manual, universal, any-profile kill-switch (`sync_paused`);
sprint watchdog's `halt_new_entries` is a SEPARATE automatic Apex-only flag latching at 2x
equity, cleared with `trader portfolio sprint-clear`. Both OR'd to gate new entries;
clearing one doesn't touch the other.

---

## The real-fill loop — `record-fill` + `tools/slippage_report.py`

**P3.7.** Asymmetric-cost canon (mid-entry + limit-TP modeled FREE; forced exits —
SL/hard-sell/dead-hold-pop/sweep — modeled at ~half-spread: `SLIP_ENTRY=SLIP_TP=0.0` /
`SLIP_SL=SLIP_HARD=-0.015` in `strategy_config.py`) is load-bearing across sizing and every
ship gate's EV math, and has never seen a real fill (alerts + manual execution, no broker
feed). Cheapest falsification instrument: record actual fills, compare to model's mark.

**Recording a fill** (right after executing a real trade — PRICE is premium PER CONTRACT,
e.g. `2.35` for a $2.35/share = $235/contract fill):
```bash
trader portfolio record-fill AAPL 2.35 5 --side buy                       # opening, ts=now
trader portfolio record-fill AAPL 3.10 5 --side sell --ts 2026-07-20T15:58 # closing, explicit ts
```
Auto-links to matching `PortfolioPosition` best-effort (closest entry/exit date, same
symbol) — confirmation line says `linked to <pos_key>` or `UNLINKED` (never silently
dropped); pass `--pos-key <key>` to link after the fact (`GET /api/portfolio/state` for the list).

**Running the report** (report-only — never mutates a position, never tunes a SLIP_*
constant, always exits 0):
```bash
python tools/slippage_report.py               # stdout summary
python tools/slippage_report.py --rows         # + per-fill detail
python tools/slippage_report.py --json         # + timestamped snapshot under .cache/slippage_report/
```
Refuses a verdict below **N=30** usable fills (gameplan.md P3.7) — below that, raw
category means shown as directional context only, labeled "INSUFFICIENT DATA,
REPORT-ONLY, NO CONCLUSION DRAWN." At/above N=30, compares forced-exit mean vs
entry/TP mean (positive = worse than modeled, regardless of buy/sell direction), states
CONSISTENT / CONTRADICTS / AMBIGUOUS. A materially-worse-than-modeled free side, or a
materially-smaller forced-exit penalty, is the pre-registered trigger to re-open
execution-conditional levers (the 70-74 overflow tier, etc.) — escalate for review, never silently re-tune.

**Weekly schedule — documented, not registered.** Not wired into an installer script;
one-liner documented here for whoever owns that surface, or ad hoc:
```bash
trader queue submit --priority idle --db light --cpu 1 --restartable \
  --dedup slippage-report-weekly --window off_market \
  --reason "weekly P3.7 realized-fill vs modeled-mark report" \
  -- python tools/slippage_report.py --json
```
`--priority idle` + `--window off_market` per gameplan.md P3.7 ("weekly idle queue") —
small-table, read-only, never time-sensitive. `--restartable` because idempotent. Pattern
to copy for the actual trigger: `scripts/install_trader_update_via_queue.ps1`'s
`New-ScheduledTaskTrigger -Weekly` shape (e.g. `-DaysOfWeek Sunday -At 03:30`, mirroring
the nightly backup's off-hours slot).

## Self-update

Hit an incident this doc didn't cover? Add a section in the same
Symptom → Check → Fix → Verify shape, and append the underlying trap to
[traps.md](traps.md) in the same session.
