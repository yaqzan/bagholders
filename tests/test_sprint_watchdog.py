"""Unit tests for the P3.1 Phase 1 sprint watchdog (portfolio_engine.py).

Pure state-machine coverage -- _sprint_watchdog_check only mutates plain
attributes on its `run` argument (no DB access), so this runs standalone
with types.SimpleNamespace stand-ins, no DB connection required.

Run: python -m pytest tests/test_sprint_watchdog.py -v
"""
from __future__ import annotations

import types
from datetime import date

import portfolio_engine as pe

D0 = date(2026, 7, 1)
D1 = date(2026, 7, 2)
D2 = date(2026, 7, 3)


def _run(profile='apex', **overrides):
    base = dict(
        profile=profile,
        sprint_start_equity=None, sprint_start_date=None,
        halt_new_entries=False,
        sprint_2x_hit_date=None, sprint_2x_notified=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_noop_when_not_apex():
    """Core/Sentinel are held compounders -- the stop-at-2x framing must not
    apply to them at all, not even to capture a baseline."""
    run = _run(profile='core')
    pe._sprint_watchdog_check(run, 100_000.0, D0)
    assert run.sprint_start_equity is None
    assert run.halt_new_entries is False


def test_first_apex_observation_captures_baseline():
    run = _run()
    pe._sprint_watchdog_check(run, 50_000.0, D0)
    assert run.sprint_start_equity == 50_000.0
    assert run.sprint_start_date == D0
    assert run.halt_new_entries is False
    assert run.sprint_2x_hit_date is None


def test_below_2x_does_not_trigger():
    run = _run(sprint_start_equity=50_000.0, sprint_start_date=D0)
    pe._sprint_watchdog_check(run, 99_999.99, D1)
    assert run.halt_new_entries is False
    assert run.sprint_2x_hit_date is None


def test_at_exactly_2x_triggers():
    run = _run(sprint_start_equity=50_000.0, sprint_start_date=D0)
    pe._sprint_watchdog_check(run, 100_000.0, D1)
    assert run.halt_new_entries is True
    assert run.sprint_2x_hit_date == D1


def test_above_2x_triggers():
    run = _run(sprint_start_equity=50_000.0, sprint_start_date=D0)
    pe._sprint_watchdog_check(run, 130_000.0, D1)
    assert run.halt_new_entries is True
    assert run.sprint_2x_hit_date == D1


def test_single_fire_latch_does_not_move_on_repeat_calls():
    """Once hit, sprint_2x_hit_date must never change on later sessions --
    the ONE-push guarantee depends on this being a permanent latch."""
    run = _run(sprint_start_equity=50_000.0, sprint_start_date=D0)
    pe._sprint_watchdog_check(run, 100_000.0, D1)
    assert run.sprint_2x_hit_date == D1
    pe._sprint_watchdog_check(run, 250_000.0, D2)   # equity kept climbing
    assert run.sprint_2x_hit_date == D1, "must not re-latch to a later date"
    assert run.halt_new_entries is True


def test_clearing_halt_does_not_resurrect_on_next_check():
    """Reproduces (as a regression guard) the re-trigger bug this design was
    built to avoid: a user clearing halt_new_entries (simulated inline,
    matching what sprint_clear() does) while equity is STILL >= 2x baseline
    must not cause the very next watchdog check to immediately re-set it --
    otherwise "clearable via CLI" would be meaningless."""
    run = _run(sprint_start_equity=50_000.0, sprint_start_date=D0,
               halt_new_entries=True, sprint_2x_hit_date=D1)
    run.halt_new_entries = False   # what `trader portfolio sprint-clear` does
    pe._sprint_watchdog_check(run, 150_000.0, D2)   # still way above 2x
    assert run.halt_new_entries is False, "clearing must stick -- no re-trigger"
    assert run.sprint_2x_hit_date == D1, "the original latch must be untouched"


def test_arm_semantics_reset_everything():
    """Matches what sprint_arm() does field-by-field (re-baseline + clear
    both latches) so the NEXT doubling can fire fresh from the new base."""
    run = _run(sprint_start_equity=50_000.0, sprint_start_date=D0,
               halt_new_entries=True, sprint_2x_hit_date=D1, sprint_2x_notified=True)
    # emulate sprint_arm()'s field resets directly (no DB access in this module)
    run.sprint_start_equity = 100_000.0
    run.sprint_start_date = D2
    run.halt_new_entries = False
    run.sprint_2x_hit_date = None
    run.sprint_2x_notified = False

    pe._sprint_watchdog_check(run, 150_000.0, D2)
    assert run.halt_new_entries is False   # 150k < 2x(100k) = 200k

    pe._sprint_watchdog_check(run, 200_000.0, D2)
    assert run.halt_new_entries is True
    assert run.sprint_2x_hit_date == D2


def test_open_entries_short_circuits_when_halted():
    """_open_entries_for_day must return [] immediately when halted -- before
    touching any of its other (deliberately garbage/empty) arguments. Proves
    the gate is unconditional and evaluated first, so a halted sprint can
    never open a new position regardless of what the cascade would otherwise
    have picked."""
    result = pe._open_entries_for_day(
        d=D0, version_id=None, calls_only=True, cfg={}, ledger=[], cash=0.0,
        equity=0.0, running_dd=0.0, ph={}, b_dates=[], b_map={}, r_dates=[],
        r_map={}, vix_dates=[], vix_map={}, mcc_dates=[], mcc_map={},
        trin_dates=[], trin_map={}, bdiv_dates=[], bdiv_map={},
        alloc_params={}, breadth_enabled=False, bc=None,
        spread_tilt_map=None, halt_new_entries=True,
    )
    assert result == []


def test_format_sprint_2x_alert_shape():
    run = _run(sprint_start_equity=50_000.0, sprint_start_date=D0, sprint_2x_hit_date=D1)
    alert = pe._format_sprint_2x_alert(run, 101_234.0)
    assert alert['kind'] == 'sprint_2x'
    assert alert['ref'] == 'sprint_2x'
    assert alert['date'] == D1
    assert 'sprint-clear' in alert['body']
    assert '101,234' in alert['body']
    assert '50,000' in alert['body']
