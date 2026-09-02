"""update_health.py — outcome detection + alerting for `trader update`.

Windows Task Scheduler treats "fired" and "succeeded" identically: a run that
crashes mid-way, trips an early guard (version-integrity mismatch, bad args,
overlap lock), or finishes too fast having done nothing all look fine to it. This
module adds real outcome alerting through the app's existing Pushover channel
(`notifications.send_pushover`), so you hear about the runs that didn't stick:

  * FAILED  — the update raised / crashed                         (priority 1)
  * NO-OP   — exited OK but wrote 0 score rows on a trading day    (priority 1)
  * PARTIAL — wrote far fewer rows than the active universe         (priority 1)
  * SKIPPED — a silent guard fired on a trading day (version
              mismatch / bad args / overlap)                        (priority 0/1)

Holiday/weekend skips are EXPECTED and never alert. The "did it actually do
work?" signal is the count of `score_intraday_logs` rows this run appended today
(cheap + indexed, written 1:1 with scoring) compared to the active stock universe.

Alerting is best-effort — it must NEVER break an update. Disable entirely with
`TRADER_UPDATE_ALERTS=0`. Opt into a success ping with `TRADER_UPDATE_ALERT_OK=1`.
"""
from __future__ import annotations

import os
from datetime import datetime

# Below this fraction of the active universe, a run that exited 0 is "partial".
PARTIAL_FRACTION = 0.50


def now() -> datetime:
    return datetime.now()


def _enabled() -> bool:
    return os.environ.get("TRADER_UPDATE_ALERTS", "1").strip().lower() not in ("0", "false", "no", "off")


def notify(title: str, message: str, priority: int = 0) -> None:
    """Best-effort Pushover send. Never raises; logs what it did."""
    if not _enabled():
        print(f"[update-alert disabled] {title}: {message}")
        return
    try:
        from notifications import send_pushover

        status, _ = send_pushover(title, message, priority=priority)
        print(f"[update-alert {status}] {title}: {message}")
    except Exception as e:  # alerting must never break an update
        print(f"[update-alert error] {title}: {message} ({e})")


def alert_skipped(reason: str, *, command_name: str = "update", urgent: bool = False) -> None:
    notify(f"⚠ trader {command_name} did nothing", f"{reason}", priority=1 if urgent else 0)


def alert_failed(reason: str, *, command_name: str = "update") -> None:
    notify(f"❌ trader {command_name} FAILED", reason, priority=1)


def rows_written_since(run_start: datetime, target_date=None) -> int | None:
    """How many score rows this run appended today, via the intraday audit log.
    None if the query fails (so we never alert on our own DB hiccup)."""
    try:
        from datetime import date as _date

        from database.models.core import ScoreIntradayLog

        target_date = target_date or _date.today()
        return (
            ScoreIntradayLog.select()
            .where((ScoreIntradayLog.date == target_date) & (ScoreIntradayLog.logged_at >= run_start))
            .count()
        )
    except Exception:
        return None


def universe_size() -> int | None:
    try:
        from database.models.core import Stock

        return Stock.select().where(Stock.delisted_date.is_null()).count()
    except Exception:
        return None


def verify_and_alert(run_start: datetime, *, command_name: str = "update") -> str:
    """Post-success check: did the run actually write scores? Alerts on a silent
    no-op or a partial run. Returns 'ok' | 'noop' | 'partial' | 'unknown'."""
    rows = rows_written_since(run_start)
    if rows is None:
        return "unknown"  # couldn't verify -> stay quiet (no false alarms)
    uni = universe_size() or 0
    dur = (now() - run_start).total_seconds()
    durs = f" in {dur:.0f}s" if dur >= 0 else ""
    if rows == 0:
        alert_failed(
            f"exited OK but wrote 0 score rows{durs} — silent no-op "
            f"(an early guard likely fired). Universe={uni}.",
            command_name=command_name,
        )
        return "noop"
    if uni and rows < PARTIAL_FRACTION * uni:
        notify(
            f"⚠ trader {command_name} PARTIAL",
            f"wrote only {rows}/{uni} score rows{durs} — partial / early finish.",
            priority=1,
        )
        return "partial"
    if os.environ.get("TRADER_UPDATE_ALERT_OK", "0").strip().lower() in ("1", "true", "yes", "on"):
        notify(f"✅ trader {command_name} OK", f"wrote {rows}/{uni} score rows{durs}.", priority=-1)
    return "ok"
