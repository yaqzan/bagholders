"""
Maintenance mode: pause the trading-day pipeline so a repair can use the whole box,
then restore EXACTLY what was paused.

The restore path is the point. Scheduled-task loss is risk R2 in the 2026-H2 plan
("backups/heartbeat/December-reminder silently dead"), so this never toggles from a
hard-coded list at resume time -- it records each task's real state at pause time to
.cache/MAINTENANCE_MODE and puts every task back the way it found it.

MECHANISM: repo-level guards, not schtasks. `schtasks /change` returns "Access is denied"
without elevation, and elevation is not obtainable non-interactively. Instead each wrapper
the scheduler invokes checks for .cache/MAINTENANCE_MODE and exits 0. That is strictly
better here: the pause is version-controlled, needs no admin, and unwinds by deleting one
file -- there is no OS state left behind to forget. The tasks stay Ready and keep firing;
they just no-op. Guarded wrappers:
  scripts/trader_update_via_queue.ps1
  scripts/portfolio_notify.ps1
  experiments/holdout_oos_2026_12/refresh_skill_caches_task.ps1

PAUSED (trading-day pipeline only):
  TraderUpdateViaQueue          the score/data pull cadence (04:00, 07:00, ... 15:45)
  TraderPortfolioNotifyMorning  08:45 ET position alerts
  TraderPortfolioNotifyClose    15:30 ET position alerts
  TraderSkillOOSCacheRefresh    cache refresh; competes for CPU, nothing depends on it today

LEFT RUNNING ON PURPOSE:
  TraderQueueDaemon      the scheduler itself -- the repair runs through it
  TraderOpsHeartbeat     the detector that would tell us the box went sideways
  TraderBackupDaily      never pause backups, least of all during a destructive rebuild
  TraderServicesWatchdog only revives api/frontend/tunnel; cannot resurrect tasks
  TraderOOSEvalDue2026   the December OOS reminder -- pre-registered, never touched
  TraderSlippageReportWeekly  weekly + light

  python maintenance_mode.py --pause
  python maintenance_mode.py --status
  python maintenance_mode.py --resume
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

REPO = r"C:\Development\Trader"
STATE = os.path.join(REPO, ".cache", "MAINTENANCE_MODE")

PAUSE = [
    "TraderUpdateViaQueue",
    "TraderPortfolioNotifyMorning",
    "TraderPortfolioNotifyClose",
    "TraderSkillOOSCacheRefresh",
]


def _sch(*args):
    return subprocess.run(["schtasks.exe", *args], capture_output=True, text=True)


def task_states():
    out = {}
    r = _sch("/query", "/fo", "csv", "/nh")
    for line in r.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 3:
            continue
        name = parts[0].lstrip("\\").strip('"')
        if not name.lower().startswith("trader"):
            continue
        # /query reports Ready|Running|Disabled
        out[name] = parts[2].strip('"')
    return out


def pause():
    if os.path.exists(STATE):
        sys.exit(f"already in maintenance mode ({STATE}) -- run --resume first")
    before = task_states()
    if not before:
        sys.exit("no Trader* scheduled tasks found -- refusing to guess")
    actions = []
    for t in PAUSE:
        if t not in before:
            actions.append({"task": t, "action": "absent"})
            continue
        # Try the clean route first; fall back to the wrapper guard (which is already in
        # place) when the shell is not elevated. Either way the pull does not run.
        r = _sch("/change", "/tn", t, "/disable")
        actions.append({"task": t, "was": before[t],
                        "action": "disabled" if r.returncode == 0 else "guarded_by_wrapper",
                        "detail": None if r.returncode == 0 else (r.stderr or "").strip()[:120]})
    state = {
        "entered_at": datetime.now().isoformat(timespec="seconds"),
        "reason": "Sharadar price_history convention rebuild -- full-box capacity",
        "tasks_before": before,
        "actions": actions,
        "queue": "scripts/queue_daemon.ps1 reads this file: MARKET_GUARD=0, "
                 "CORE_BUDGET=cpu-1, LOW_CORE_CAP=3/4 cpu. Restart the daemon to apply.",
    }
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)
    print(json.dumps(state, indent=2))
    print(f"\nMAINTENANCE MODE ON -> {STATE}")
    print("Restart the queue daemon for the resource changes to take effect.")


def resume():
    if not os.path.exists(STATE):
        sys.exit("not in maintenance mode (no state file) -- nothing to restore")
    state = json.load(open(STATE))
    before = state["tasks_before"]
    now = task_states()
    restored = []
    # The wrapper guards key off the state file alone, so removing it is what actually
    # resumes the pipeline; the loop below only repairs any task we managed to disable.
    for t, was in before.items():
        cur = now.get(t)
        if cur is None:
            restored.append({"task": t, "result": "MISSING NOW -- investigate"})
            continue
        want_enabled = (was.lower() != "disabled")
        is_enabled = (cur.lower() != "disabled")
        if want_enabled and not is_enabled:
            r = _sch("/change", "/tn", t, "/enable")
            restored.append({"task": t, "was": was, "result":
                             "re-enabled" if r.returncode == 0 else f"FAILED rc={r.returncode}"})
        elif not want_enabled and is_enabled:
            r = _sch("/change", "/tn", t, "/disable")
            restored.append({"task": t, "was": was, "result":
                             "re-disabled" if r.returncode == 0 else f"FAILED rc={r.returncode}"})
        else:
            restored.append({"task": t, "was": was, "result": "already correct"})
    os.remove(STATE)
    print(json.dumps(restored, indent=2))
    print("\nMAINTENANCE MODE OFF -- state file removed.")
    print("Restart the queue daemon to drop back to normal budgets.")
    print("Then run today's catch-up pull:  python trader.py update")


def status():
    on = os.path.exists(STATE)
    print(f"maintenance mode: {'ON' if on else 'OFF'}")
    if on:
        print(json.dumps(json.load(open(STATE)), indent=2))
    print("\ncurrent Trader* task states:")
    for t, s in sorted(task_states().items()):
        print(f"  {t:<32} {s}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pause", action="store_true")
    g.add_argument("--resume", action="store_true")
    g.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.pause:
        pause()
    elif a.resume:
        resume()
    else:
        status()
