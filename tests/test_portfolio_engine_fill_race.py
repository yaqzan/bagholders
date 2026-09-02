"""Synthetic test for the P0.2 fill-race fix (portfolio_engine.last_completed_session).

Proves the ADUR 2026-06-10 incident class is closed: an engine pass running
at/after wall-clock 16:00 ET must NOT treat "today" as the completed/fillable
session while today's ScoreIntradayLog shows only a run that itself STARTED
before the close (the straddling 15:45 grid slot -- up to a 40-min execution
ceiling, so it can still be writing rows past 16:00 off data fetched before
the bell) -- and MUST treat today as fillable once a run that itself started
AT/AFTER the close has logged its own snapshot (in practice the ~16:30 close
pipeline). No live DB is touched: ScoreIntradayLog.select is monkeypatched to
return canned per-run_key MIN(logged_at) rows, so this runs standalone with
no DB connection required.

Run: python -m pytest tests/test_portfolio_engine_fill_race.py -v
"""
from __future__ import annotations

from datetime import date, datetime

import database.models.core as core
import portfolio_engine as pe

# A real, known-good weekday pair with no NYSE holiday nearby.
D = date(2026, 7, 8)        # Wednesday
D_PREV = date(2026, 7, 7)   # last trading day before D (Tuesday)


class _Row:
    def __init__(self, start):
        self.start = start


class _FakeQuery:
    """Chainable stand-in for the ScoreIntradayLog.select(...).where(...)
    .group_by(...) query built by _session_finalized. Ignores the actual
    filter args -- the fixed `rows` list already represents the per-run_key
    MIN(logged_at) result the real GROUP BY would have produced."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def where(self, *a, **k):
        return self

    def group_by(self, *a, **k):
        return self

    def __iter__(self):
        return iter(self._rows)


def _patch_runs(monkeypatch, run_starts):
    """run_starts: one datetime per simulated run_key's MIN(logged_at)."""
    q = _FakeQuery([_Row(s) for s in run_starts])
    monkeypatch.setattr(core.ScoreIntradayLog, "select", classmethod(lambda cls, *a, **k: q))


# ---------------------------------------------------------------------------
# _session_finalized -- direct unit coverage of the data-driven marker itself
# ---------------------------------------------------------------------------
def test_session_finalized_false_when_only_preclose_run_logged(monkeypatch):
    # The straddling 15:45 run: its run_key started at 15:45, before the
    # 16:00 close, even though (unmodeled here) some of its individual rows
    # may have been WRITTEN past 16:00 -- the run's START is what matters.
    _patch_runs(monkeypatch, [datetime(D.year, D.month, D.day, 15, 45, 0)])
    assert pe._session_finalized(D) is False


def test_session_finalized_true_when_postclose_run_logged(monkeypatch):
    # The close pipeline: its run_key started at 16:30, at/after the close.
    _patch_runs(monkeypatch, [datetime(D.year, D.month, D.day, 16, 30, 0)])
    assert pe._session_finalized(D) is True


def test_session_finalized_false_when_no_rows_at_all(monkeypatch):
    _patch_runs(monkeypatch, [])
    assert pe._session_finalized(D) is False


def test_session_finalized_true_if_any_qualifying_run_present(monkeypatch):
    # Mixed: an earlier straddling run AND a later genuine post-close run
    # both logged something for D -- one qualifying run_key is enough.
    _patch_runs(monkeypatch, [
        datetime(D.year, D.month, D.day, 15, 45, 0),
        datetime(D.year, D.month, D.day, 16, 30, 0),
    ])
    assert pe._session_finalized(D) is True


def test_session_finalized_exact_close_boundary_qualifies(monkeypatch):
    # A run starting exactly at MARKET_CLOSE_HOUR_ET:00 counts (>=, not >).
    _patch_runs(monkeypatch, [datetime(D.year, D.month, D.day, pe.MARKET_CLOSE_HOUR_ET, 0, 0)])
    assert pe._session_finalized(D) is True


# ---------------------------------------------------------------------------
# last_completed_session -- the actual fill gate sync()/_advance() consume.
# ---------------------------------------------------------------------------
def test_1605_pass_does_not_fill_unfinalized_today(monkeypatch):
    """The ADUR 2026-06-10 incident class: an engine pass at 16:05, with only
    the straddling pre-close run's snapshot on record for today, must
    resolve the completed session to YESTERDAY, not today -- i.e. it must
    NOT fill today on a score that no genuinely after-the-close run has
    confirmed yet."""
    _patch_runs(monkeypatch, [datetime(D.year, D.month, D.day, 15, 45, 0)])
    now = datetime(D.year, D.month, D.day, 16, 5, 0)
    result = pe.last_completed_session(now=now, latest_data=D)
    assert result == D_PREV, (
        f"expected fallback to {D_PREV} (today not yet finalized), got {result} "
        f"-- this is the ADUR-class race: filling today on an unconfirmed score"
    )


def test_1611_pass_does_not_fill_on_the_documented_adur_timeline(monkeypatch):
    """Reproduces the literal ADUR timeline: engine pass at 16:11 (the
    incident's own timestamp per known-issues.md), only the pre-close run on
    record yet (the ~16:31 close-update correction hasn't landed). Must
    defer to yesterday instead of filling on the still-provisional score."""
    _patch_runs(monkeypatch, [datetime(D.year, D.month, D.day, 15, 45, 0)])
    now = datetime(D.year, D.month, D.day, 16, 11, 0)
    result = pe.last_completed_session(now=now, latest_data=D)
    assert result == D_PREV


def test_pass_fills_once_postclose_run_confirms_today(monkeypatch):
    """Once a genuinely after-the-close run (e.g. the ~16:30 close pipeline)
    has logged its own snapshot for today, today correctly becomes fillable
    -- even though the CALLER's own wall-clock is still just 16:05. The gate
    is data-driven, not "wait N minutes"."""
    _patch_runs(monkeypatch, [datetime(D.year, D.month, D.day, 16, 30, 0)])
    now = datetime(D.year, D.month, D.day, 16, 5, 0)
    result = pe.last_completed_session(now=now, latest_data=D)
    assert result == D


def test_preclose_branch_unaffected_by_finalization_state(monkeypatch):
    """Regression guard: before 16:00 ET, the ORIGINAL wall-clock branch
    alone decides (today's session isn't even eligible yet) -- finalization
    state must be irrelevant. Even a fully-finalized log for today must not
    pull today's session forward before the close."""
    _patch_runs(monkeypatch, [datetime(D.year, D.month, D.day, 16, 30, 0)])
    now = datetime(D.year, D.month, D.day, 14, 0, 0)
    result = pe.last_completed_session(now=now, latest_data=D)
    assert result == D_PREV


def test_finalization_check_not_consulted_for_a_prior_day(monkeypatch):
    """Regression guard: when wall-clock/weekday resolution already lands on
    a day strictly before `today` (e.g. Monday morning resolving to Friday),
    _session_finalized must not even be consulted -- only `d == today` is
    re-checked. An empty/broken log for that prior day must not defer it,
    which is also what keeps this change inert for any date predating the
    audit log's 2026-05-27 start."""
    calls = []
    orig = pe._session_finalized

    def _spy(d):
        calls.append(d)
        return orig(d)

    monkeypatch.setattr(pe, "_session_finalized", _spy)
    _patch_runs(monkeypatch, [])   # would fail finalization if ever consulted

    # Monday 2026-07-13, before the close -> last_trading_day(Sunday - 1) ->
    # Friday 2026-07-10, entirely via the pre-close branch (untouched code).
    now = datetime(2026, 7, 13, 9, 0, 0)
    result = pe.last_completed_session(now=now)
    assert result == date(2026, 7, 10)
    assert calls == [], "the pre-close branch must not consult _session_finalized at all"


def test_finalization_failure_defers_rather_than_fills(monkeypatch):
    """A query failure (e.g. table missing on a fresh box) must fail SAFE --
    defer today, never force a fill on an unconfirmed session."""
    def _boom(cls, *a, **k):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(core.ScoreIntradayLog, "select", classmethod(_boom))
    assert pe._session_finalized(D) is False
    now = datetime(D.year, D.month, D.day, 16, 5, 0)
    result = pe.last_completed_session(now=now, latest_data=D)
    assert result == D_PREV
