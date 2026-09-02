# Redundant hard trigger for the pre-registered 2026-12-15 OOS evaluation
# (experiments/holdout_oos_2026_12/PREREGISTRATION.md, gameplan.md row P1.6).
#
# Deliberately does NOT attempt to auto-run the real evaluation (run_oos_eval.py) -- that needs a
# human/agent to interpret verdicts and route H1 BLOCKs to FABLE/user per the pre-registered
# consequence. This just submits a harmless, self-explanatory marker task through the existing
# task queue so it shows up in `trader queue list --all` / `trader queue status`. Primary signal
# is Block A's heartbeat digest (which already carries a days-until-OOS line); this is the backstop.
#
# Invoked by the TraderOOSEvalDue2026 scheduled task (see install_reminder.ps1). Safe to run
# manually at any time to preview the marker it submits.

$ErrorActionPreference = "Stop"
Set-Location "C:\Development\Trader"
# See scripts\ops_heartbeat.ps1 for why: Task Scheduler's PATH puts a
# leftover hermes-agent venv ahead of the real interpreter, so bare `python`
# resolves to a venv without trader/task_queue installed.
$env:Path = "C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\Scripts;C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311;" + $env:Path

python trader.py queue submit --priority normal --db none `
    --dedup oos2026-due-ping `
    --reason "Pre-registered 2026-12-15 OOS eval is due -- see experiments/holdout_oos_2026_12/PREREGISTRATION.md" `
    -- echo "OOS-EVAL-DUE-2026-12-15: run experiments/holdout_oos_2026_12/run_oos_eval.py (see PREREGISTRATION.md)"
