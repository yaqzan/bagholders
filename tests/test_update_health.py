"""Tests for update_health outcome classification (no DB, no Pushover sends)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import update_health  # noqa: E402

_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL {msg}")


def _patch(rows, uni):
    """Capture notify() calls and stub the DB signals. Returns (calls, restore)."""
    calls = []
    saved = (update_health.notify, update_health.rows_written_since, update_health.universe_size)
    update_health.notify = lambda title, msg, priority=0: calls.append((title, msg, priority))
    update_health.rows_written_since = lambda *a, **k: rows
    update_health.universe_size = lambda: uni

    def restore():
        update_health.notify, update_health.rows_written_since, update_health.universe_size = saved

    return calls, restore


def test_noop():
    print("[update_health: silent no-op]")
    os.environ.pop("TRADER_UPDATE_ALERT_OK", None)
    calls, restore = _patch(rows=0, uni=750)
    try:
        v = update_health.verify_and_alert(update_health.now(), command_name="update")
    finally:
        restore()
    check(v == "noop", "0 rows on a trading day -> verdict 'noop'")
    check(len(calls) == 1 and calls[0][2] == 1, "no-op fires exactly one priority-1 alert")
    check("FAILED" in calls[0][0], "alert flags it as a failure")


def test_partial():
    print("[update_health: partial run]")
    calls, restore = _patch(rows=100, uni=750)
    try:
        v = update_health.verify_and_alert(update_health.now())
    finally:
        restore()
    check(v == "partial", "100/750 rows -> verdict 'partial'")
    check(len(calls) == 1 and "PARTIAL" in calls[0][0], "partial fires a PARTIAL alert")


def test_ok_quiet():
    print("[update_health: healthy run stays quiet]")
    os.environ.pop("TRADER_UPDATE_ALERT_OK", None)
    calls, restore = _patch(rows=740, uni=750)
    try:
        v = update_health.verify_and_alert(update_health.now())
    finally:
        restore()
    check(v == "ok", "740/750 rows -> verdict 'ok'")
    check(len(calls) == 0, "healthy run sends NO alert by default")


def test_ok_optin_ping():
    print("[update_health: opt-in success ping]")
    os.environ["TRADER_UPDATE_ALERT_OK"] = "1"
    calls, restore = _patch(rows=740, uni=750)
    try:
        update_health.verify_and_alert(update_health.now())
    finally:
        restore()
        os.environ.pop("TRADER_UPDATE_ALERT_OK", None)
    check(len(calls) == 1 and "OK" in calls[0][0], "TRADER_UPDATE_ALERT_OK=1 -> one success ping")


def test_unverifiable_quiet():
    print("[update_health: DB-unverifiable never false-alarms]")
    calls, restore = _patch(rows=None, uni=750)
    try:
        v = update_health.verify_and_alert(update_health.now())
    finally:
        restore()
    check(v == "unknown" and len(calls) == 0, "row count None (DB hiccup) -> quiet, no false alarm")


def test_skip_fail_helpers():
    print("[update_health: skip/fail helpers]")
    calls, restore = _patch(rows=0, uni=0)
    try:
        update_health.alert_skipped("guard fired", urgent=True)
        update_health.alert_failed("boom")
    finally:
        restore()
    check(len(calls) == 2, "alert_skipped + alert_failed each notify once")
    check(calls[0][2] == 1 and calls[1][2] == 1, "urgent skip + failure are priority 1")


def test_disabled_env():
    print("[update_health: TRADER_UPDATE_ALERTS=0 disables sends]")
    os.environ["TRADER_UPDATE_ALERTS"] = "0"
    sent = []
    saved = update_health.__dict__.get("send_pushover")
    try:
        # notify() should short-circuit before importing/calling send_pushover
        update_health.notify("t", "m", 1)
        check(True, "notify() with alerts disabled does not raise")
    finally:
        os.environ.pop("TRADER_UPDATE_ALERTS", None)


def main() -> int:
    for t in (test_noop, test_partial, test_ok_quiet, test_ok_optin_ping,
              test_unverifiable_quiet, test_skip_fail_helpers, test_disabled_env):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            global _FAIL
            _FAIL += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
