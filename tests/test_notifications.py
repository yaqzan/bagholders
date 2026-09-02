from notifications import (
    ScoreNotification,
    classify_score_transition,
    exit_marker,
    format_score_digest,
    is_score_still_alertable,
    side_for_score,
    _exit_reason_for_bar,
    _opportunity_key_from_event,
    _realized_vol_pct,
    _storage_line,
    _target_prices,
)
from portfolio_allocation import allocation_breakdown_for_score


def test_call_gate_crossing_is_digested():
    assert classify_score_transition(79, 80) == ("call", 80, False, "score_call_80")


def test_existing_call_signal_does_not_repeat_digest():
    assert classify_score_transition(80, 82) is None


def test_new_urgent_call_crossing_is_immediate():
    assert classify_score_transition(82, 85) == ("call", 85, True, "score_call_85")


def test_put_gate_crossing_is_immediate():
    assert classify_score_transition(11, 10) == ("put", 10, True, "score_put_10")


def test_new_urgent_put_crossing_is_immediate():
    assert classify_score_transition(12, 9) == ("put", 10, True, "score_put_10")


def test_score_side_matches_alert_gates():
    assert side_for_score(80) == "call"
    assert side_for_score(10) == "put"
    assert side_for_score(50) is None


def test_reopen_gate_uses_same_side_threshold():
    assert is_score_still_alertable("call", 80)
    assert not is_score_still_alertable("call", 79)
    assert is_score_still_alertable("put", 10)
    assert not is_score_still_alertable("put", 11)


def test_opportunity_key_ties_back_to_notification_event():
    assert _opportunity_key_from_event("2026-05-11:1:score_call_80:NVDA:call:80") == (
        "alert:2026-05-11:1:score_call_80:NVDA:call:80"
    )


def test_exit_markers_for_tp_sl():
    assert exit_marker("tp") == "💰"
    assert exit_marker("take_profit") == "💰"
    assert exit_marker("sl") == "🛑"
    assert exit_marker("stop_loss") == "🛑"


def test_realized_vol_pct_uses_daily_returns():
    sigma = _realized_vol_pct([100.0, 101.0, 99.0, 102.0])
    assert sigma is not None
    assert round(sigma, 2) == 2.06


def test_call_target_prices_move_up_for_tp_down_for_sl():
    tp, sl, tp_pct, sl_pct = _target_prices(
        entry_price=100.0,
        sigma_daily=2.0,
        side="call",
        stressed=False,
    )
    assert tp > 100.0
    assert sl < 100.0
    assert tp_pct > 0
    assert sl_pct < 0


def test_put_target_prices_move_down_for_tp_up_for_sl():
    tp, sl, tp_pct, sl_pct = _target_prices(
        entry_price=100.0,
        sigma_daily=2.0,
        side="put",
        stressed=False,
    )
    assert tp < 100.0
    assert sl > 100.0
    assert tp_pct < 0
    assert sl_pct > 0


def test_exit_reason_for_bar_prioritizes_tp_when_both_hit():
    assert _exit_reason_for_bar("call", 110.0, 90.0, 105.0, 95.0) == "tp"
    assert _exit_reason_for_bar("put", 110.0, 90.0, 95.0, 105.0) == "tp"


def test_put_stop_loss_respects_hold_gate():
    assert _exit_reason_for_bar(
        "put",
        106.0,
        99.0,
        95.0,
        105.0,
        put_sl_hold_active=True,
    ) is None


def test_digest_groups_calls_and_puts():
    events = [
        ScoreNotification("AAA", "call", 80, 123.45, 10.0, 70, 80, False, "score_call_80"),
        ScoreNotification("BBB", "put", 9, 67.89, 12.0, 12, 10, True, "score_put_10"),
    ]
    assert format_score_digest(events) == (
        '<font color="#10b981">AAA 🟢 80  $123.45  10%</font>\n'
        '<font color="#ef4444">BBB 🔻 9  $67.89  12%</font>'
    )


def test_storage_line_is_ascii_safe():
    event = ScoreNotification("AAA", "call", 80, 123.45, 10.0, 70, 80, False, "score_call_80")
    assert _storage_line(event) == "AAA 80H $123.45 10%"


def test_digest_matches_dashboard_buy_opportunity_sort():
    events = [
        ScoreNotification("PUT10", "put", 10, 10.0, 12.0, 30, 10, False, "score_put_10"),
        ScoreNotification("CALL80", "call", 80, 80.0, 10.0, 70, 80, False, "score_call_80"),
        ScoreNotification("PUT05", "put", 5, 5.0, 12.0, 30, 10, False, "score_put_10"),
        ScoreNotification("CALL90", "call", 90, 90.0, 15.0, 70, 80, False, "score_call_80"),
    ]
    assert format_score_digest(events).splitlines() == [
        '<font color="#10b981">CALL90 🟢 90  $90.00  15%</font>',
        '<font color="#10b981">CALL80 🟢 80  $80.00  10%</font>',
        '<font color="#ef4444">PUT05 🔻 5  $5.00  12%</font>',
        '<font color="#ef4444">PUT10 🔻 10  $10.00  12%</font>',
    ]


def test_shared_allocation_helper_includes_low_call_tier():
    breakdown = allocation_breakdown_for_score(
        76,
        "call",
        strategy="30dte",
        breadth=50.0,
        signal_date=None,
    )
    assert breakdown is not None
    assert breakdown.tier.label == "75-79"
    assert round(breakdown.effective_pct, 1) == 10.0


def test_shared_allocation_helper_applies_saw_put_scale():
    breakdown = allocation_breakdown_for_score(
        10,
        "put",
        strategy="30dte",
        breadth=32.0,
        signal_date=None,
        sector_breadth=72.0,
    )
    assert breakdown is not None
    assert breakdown.tier.label == "<=15"
    assert round(breakdown.saw_put_scale, 2) == 0.55
    assert round(breakdown.effective_pct, 1) == 6.6
