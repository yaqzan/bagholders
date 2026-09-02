from datetime import date

from historic_peaks import HIGH_THRESHOLD, LOW_THRESHOLD, _barrier_wins


def test_historic_peak_thresholds_match_signal_boundaries():
    assert HIGH_THRESHOLD == 75
    assert LOW_THRESHOLD == 25


def test_one_day_reach_uses_intraday_target_even_when_close_loses():
    wins, reach = _barrier_wins(
        'HIGH',
        entry=100.0,
        vol_pct=3.0,
        fwd_bars=[(date(2026, 5, 5), 103.0, 96.0, 99.0)],
        peak_date=date(2026, 5, 4),
    )

    assert reach['1d'] == 1
    assert wins['1d'] == 0


def test_seven_day_open_window_non_winner_stays_pending():
    wins, reach = _barrier_wins(
        'HIGH',
        entry=100.0,
        vol_pct=3.0,
        fwd_bars=[(date(2026, 5, 10), 102.0, 99.0, 101.0)],
        peak_date=date(2026, 5, 4),
    )

    assert reach['7d'] is None
    assert wins['7d'] is None


def test_seven_day_open_window_counts_target_hit_as_win():
    wins, reach = _barrier_wins(
        'HIGH',
        entry=100.0,
        vol_pct=3.0,
        fwd_bars=[(date(2026, 5, 6), 103.5, 99.0, 102.0)],
        peak_date=date(2026, 5, 4),
    )

    assert reach['7d'] == 1
    assert wins['7d'] == 1


def test_seven_day_complete_window_non_winner_counts_as_loss():
    wins, reach = _barrier_wins(
        'HIGH',
        entry=100.0,
        vol_pct=3.0,
        fwd_bars=[
            (date(2026, 5, 10), 102.0, 99.0, 101.0),
            (date(2026, 5, 12), 102.0, 99.0, 101.0),
        ],
        peak_date=date(2026, 5, 4),
    )

    assert reach['7d'] == 0
    assert wins['7d'] == 0
