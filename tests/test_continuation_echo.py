from __future__ import annotations

from datetime import date, timedelta

from database.utils import scoring


def test_cont_prior_signal_uses_fine_grain_w7_window():
    signal_date = date(2026, 1, 31)
    prior_date = signal_date - timedelta(days=7)

    echo = scoring.compute_cont_prior_signal(
        signal_date,
        [(prior_date, 75)],
        {prior_date: {7: 1}},
    )

    assert echo > 0.0


def test_cont_prior_signal_penalizes_early_fizzler():
    signal_date = date(2026, 1, 31)
    prior_date = signal_date - timedelta(days=60)

    echo = scoring.compute_cont_prior_signal(
        signal_date,
        [(prior_date, 80)],
        {prior_date: {7: 1, 15: 1, 30: 0, 60: 0}},
    )

    assert echo < 0.0


def test_continuation_echo_lifts_only_when_it_reaches_tradable_gate():
    base_args = (70, 70, 70, 70, 70, 70)
    weekly_kwargs = {
        "ws_trend": 60,
        "ws_rsi": 60,
        "ws_macd": 60,
        "prev_ws_trend": 60,
        "prev_ws_rsi": 60,
        "prev_ws_macd": 60,
    }

    no_lift, no_lift_info, _ = scoring.compute_overall_score(
        *base_args,
        prior_signal=0.03,
        **weekly_kwargs,
    )
    lifted, lifted_info, _ = scoring.compute_overall_score(
        *base_args,
        prior_signal=0.10,
        **weekly_kwargs,
    )

    assert no_lift == 74
    assert "cont_lift" not in no_lift_info
    assert lifted == 75
    assert lifted_info["pre_boost"] == 74
    assert lifted_info["cont_lift"] == 1.0
