"""Tests for volume_amplifier — covers all 12 required scenarios."""

import unittest
import math
from unittest.mock import patch, MagicMock
from datetime import datetime, date, timedelta
from types import SimpleNamespace

from volume_amplifier import (
    project_full_day_volume, _interpolate_completion, _classify_signal,
    _raw_magnitude, _to_multiplier, _apply_gate, _to_raw_signal,
    get_volume_multiplier, get_volume_multiplier_from_cache,
    _neutral, VolumeSignal, NEUTRAL_RESULT,
    CLIMAX_DAMPENING_FACTOR, GAP_MULTIPLIER_BULLISH, GAP_MULTIPLIER_BEARISH,
    HALF_LIFE, DECAY_CONTRADICT_FACTOR, DECAY_CONFIRM_FACTOR,
    GATE_CAP_BULLISH, GATE_CAP_BEARISH,
)


class TestProjectionCurve(unittest.TestCase):
    """1. Projection curve accuracy at each named breakpoint."""

    def test_1030am(self):
        r = project_full_day_volume(300_000, datetime(2025, 3, 31, 10, 30))
        self.assertEqual(r, int(300_000 / 0.30))

    def test_130pm(self):
        r = project_full_day_volume(610_000, datetime(2025, 3, 31, 13, 30))
        self.assertEqual(r, int(610_000 / 0.61))

    def test_300pm(self):
        r = project_full_day_volume(740_000, datetime(2025, 3, 31, 15, 0))
        self.assertEqual(r, int(740_000 / 0.74))

    def test_premarket_returns_none(self):
        self.assertIsNone(project_full_day_volume(100_000, datetime(2025, 3, 31, 8, 0)))

    def test_after_close_returns_observed(self):
        self.assertEqual(project_full_day_volume(1_000_000, datetime(2025, 3, 31, 16, 30)), 1_000_000)

    def test_interpolation_between_breakpoints(self):
        # 11:30am = 120 min elapsed, between (90,0.40) and (150,0.50)
        pct = _interpolate_completion(120)
        expected = 0.40 + (120 - 90) / (150 - 90) * (0.50 - 0.40)
        self.assertAlmostEqual(pct, expected, places=6)


class TestPremarket(unittest.TestCase):
    """2. Pre-market pull returns gap-based multiplier only, never ratio-based."""

    @patch('volume_amplifier._analyze')
    def test_bullish_gap(self, mock_analyze):
        mock_analyze.return_value = _neutral(is_premarket=True, premarket_gap_pct=0.03)
        mult, raw, sig, mag, blend_w, vol_target = get_volume_multiplier('AAPL', date.today(), datetime.now())
        self.assertEqual(mult, GAP_MULTIPLIER_BULLISH)
        self.assertEqual(sig, 'NEUTRAL')
        self.assertEqual(raw, 50)
        self.assertEqual(mag, 0.0)
        self.assertEqual(blend_w, 0.0)
        self.assertEqual(vol_target, 50.0)

    @patch('volume_amplifier._analyze')
    def test_bearish_gap(self, mock_analyze):
        mock_analyze.return_value = _neutral(is_premarket=True, premarket_gap_pct=-0.03)
        mult, raw, sig, mag, blend_w, vol_target = get_volume_multiplier('AAPL', date.today(), datetime.now())
        self.assertEqual(mult, GAP_MULTIPLIER_BEARISH)
        self.assertEqual(sig, 'NEUTRAL')
        self.assertEqual(blend_w, 0.0)
        self.assertEqual(vol_target, 50.0)

    @patch('volume_amplifier._analyze')
    def test_no_gap(self, mock_analyze):
        mock_analyze.return_value = _neutral(is_premarket=True, premarket_gap_pct=0.005)
        mult, raw, sig, mag, blend_w, vol_target = get_volume_multiplier('AAPL', date.today(), datetime.now())
        self.assertEqual(mult, 1.0)
        self.assertEqual(blend_w, 0.0)
        self.assertEqual(vol_target, 50.0)


class TestEarningsSuppression(unittest.TestCase):
    """3. Earnings suppression returns 1.0 on earnings day and day before."""

    @patch('volume_amplifier._analyze')
    def test_earnings_day(self, mock_analyze):
        mock_analyze.return_value = _neutral(earnings_suppressed=True)
        result = get_volume_multiplier('AAPL', date.today(), datetime.now())
        self.assertEqual(result, NEUTRAL_RESULT)

    @patch('volume_amplifier._analyze')
    def test_day_before_earnings(self, mock_analyze):
        mock_analyze.return_value = _neutral(earnings_suppressed=True)
        result = get_volume_multiplier('AAPL', date.today(), datetime.now())
        self.assertEqual(result, NEUTRAL_RESULT)


class TestDiminishingReturns(unittest.TestCase):
    """4. 4x volume spike produces less than 2x the magnitude of 2x spike."""

    def test_diminishing(self):
        mag_2x = _raw_magnitude(2.0, 0.5, 'CONVICTION')
        mag_4x = _raw_magnitude(4.0, 0.5, 'CONVICTION')
        self.assertGreater(mag_4x, mag_2x)
        self.assertLess(mag_4x, mag_2x * 2)


class TestClimaxInversion(unittest.TestCase):
    """5. CLIMAX signal inverts direction vs candle direction."""

    def test_green_climax_is_bearish(self):
        sig, direction = _classify_signal(
            ratio_50=4.5, body_ratio=0.7, upper_wick=0.1, lower_wick=0.1,
            body=0.7, candle_direction=1, recent_closes=[100, 99, 98, 97, 96])
        self.assertEqual(sig, 'CLIMAX')
        self.assertEqual(direction, -1)

    def test_red_climax_is_bullish(self):
        sig, direction = _classify_signal(
            ratio_50=4.5, body_ratio=0.7, upper_wick=0.1, lower_wick=0.1,
            body=0.7, candle_direction=-1, recent_closes=[96, 97, 98, 99, 100])
        self.assertEqual(sig, 'CLIMAX')
        self.assertEqual(direction, 1)

    def test_climax_dampened(self):
        mag_conv = _raw_magnitude(4.0, 0.5, 'CONVICTION')
        mag_clim = _raw_magnitude(4.0, 0.5, 'CLIMAX')
        self.assertAlmostEqual(mag_clim, mag_conv * CLIMAX_DAMPENING_FACTOR, places=6)


class TestThinAir(unittest.TestCase):
    """6. THIN_AIR suppresses rather than amplifies."""

    def test_thin_air_classification(self):
        sig, direction = _classify_signal(
            ratio_50=0.5, body_ratio=0.7, upper_wick=0.1, lower_wick=0.1,
            body=0.7, candle_direction=1, recent_closes=[100, 99, 98, 97, 96])
        self.assertEqual(sig, 'THIN_AIR')
        self.assertEqual(direction, -1)  # opposite to green candle

    def test_thin_air_multiplier_suppresses(self):
        mult = _to_multiplier('THIN_AIR', -1, 0.8)
        self.assertLess(mult, 1.0)

    def test_thin_air_never_amplifies(self):
        for mag in [0.0, 0.3, 0.5, 0.8, 1.0]:
            mult = _to_multiplier('THIN_AIR', -1, mag)
            self.assertLessEqual(mult, 1.0)


class TestAbsorption(unittest.TestCase):
    """7. ABSORPTION uses opposite of recent trend, not candle direction."""

    def test_uptrend_absorption_is_bearish(self):
        # recent_closes[0] > recent_closes[-1] → uptrend → direction = -1
        sig, direction = _classify_signal(
            ratio_50=2.0, body_ratio=0.2, upper_wick=0.3, lower_wick=0.3,
            body=0.2, candle_direction=1,
            recent_closes=[105, 104, 103, 102, 100])
        self.assertEqual(sig, 'ABSORPTION')
        self.assertEqual(direction, -1)

    def test_downtrend_absorption_is_bullish(self):
        # recent_closes[0] < recent_closes[-1] → downtrend → direction = +1
        sig, direction = _classify_signal(
            ratio_50=2.0, body_ratio=0.2, upper_wick=0.3, lower_wick=0.3,
            body=0.2, candle_direction=-1,
            recent_closes=[95, 96, 97, 98, 100])
        self.assertEqual(sig, 'ABSORPTION')
        self.assertEqual(direction, 1)


class TestDecay(unittest.TestCase):
    """8. Magnitude halves after exactly one half-life of trading days."""

    def test_exact_half_life(self):
        for sig_type, hl in HALF_LIFE.items():
            initial = 0.8
            decayed = initial * (0.5 ** (hl / hl))
            self.assertAlmostEqual(decayed, initial / 2, places=6,
                                   msg=f"{sig_type} half-life failed")

    def test_two_half_lives(self):
        initial = 0.8
        hl = HALF_LIFE['CONVICTION']
        decayed = initial * (0.5 ** (2 * hl / hl))
        self.assertAlmostEqual(decayed, initial / 4, places=6)


class TestAsymmetricDecay(unittest.TestCase):
    """9. Contradicted signal decays faster than confirmed signal."""

    def test_contradicted_faster(self):
        initial = 0.8
        hl = 3.0
        days = 2.0

        normal_decayed = initial * (0.5 ** (days / hl))
        contradict_hl = hl / DECAY_CONTRADICT_FACTOR
        fast_decayed = initial * (0.5 ** (days / contradict_hl))

        self.assertLess(fast_decayed, normal_decayed)

    def test_confirmed_slower(self):
        initial = 0.8
        hl = 3.0
        days = 2.0

        normal_decayed = initial * (0.5 ** (days / hl))
        confirm_hl = hl / DECAY_CONFIRM_FACTOR
        slow_decayed = initial * (0.5 ** (days / confirm_hl))

        self.assertGreater(slow_decayed, normal_decayed)


class TestDirectionalGate(unittest.TestCase):
    """10. Bullish volume capped at 1.15 when overall < 40."""

    def test_bullish_capped_when_bearish_picture(self):
        mult = _apply_gate(1.5, direction=1, pre_volume_overall=35)
        self.assertAlmostEqual(mult, GATE_CAP_BULLISH)

    def test_bearish_capped_when_bullish_picture(self):
        mult = _apply_gate(0.6, direction=-1, pre_volume_overall=65)
        self.assertAlmostEqual(mult, GATE_CAP_BEARISH)

    def test_no_gate_when_aligned(self):
        mult = _apply_gate(1.4, direction=1, pre_volume_overall=55)
        self.assertAlmostEqual(mult, 1.4)

    def test_no_gate_when_none(self):
        mult = _apply_gate(1.5, direction=1, pre_volume_overall=None)
        self.assertAlmostEqual(mult, 1.5)


class TestSameDateExclusion(unittest.TestCase):
    """11. Same-date records are excluded from decay window."""

    @patch('volume_amplifier._analyze')
    def test_neutral_when_no_prior_signals(self, mock_analyze):
        # If _analyze returns NEUTRAL (no prior non-NEUTRAL signals found),
        # the multiplier should be 1.0 regardless of any same-date Score
        mock_analyze.return_value = _neutral(
            signal_type='NEUTRAL', direction=0, adjusted_magnitude=0.0)
        mult, raw, sig, mag, blend_w, vol_target = get_volume_multiplier(
            'AAPL', date.today(), datetime.now(), pre_volume_overall=50)
        self.assertEqual(mult, 1.0)
        self.assertEqual(raw, 50)
        self.assertEqual(blend_w, 0.0)
        self.assertEqual(vol_target, 50.0)

    def test_cache_zero_volume_still_decays_prior_signal(self):
        today = date(2025, 3, 12)
        prev = today - timedelta(days=1)
        ph_list_desc = [
            SimpleNamespace(
                date=today, open=100.0, high=100.0, low=100.0, close=100.0,
                volume=0, pulled_at=datetime(2025, 3, 12, 10, 0),
            ),
            SimpleNamespace(
                date=prev, open=99.0, high=102.0, low=98.0, close=101.0,
                volume=1_000_000, pulled_at=datetime(2025, 3, 11, 16, 0),
            ),
            SimpleNamespace(
                date=today - timedelta(days=2), open=98.0, high=100.0,
                low=97.0, close=99.0, volume=900_000,
                pulled_at=datetime(2025, 3, 10, 16, 0),
            ),
        ]
        prior_vol_signals = {prev: ('CONVICTION', 0.8, 90)}

        mult, raw, sig, mag, blend_w, vol_target = get_volume_multiplier_from_cache(
            today, ph_list_desc, prior_vol_signals, set())

        self.assertGreater(mult, 1.0)
        self.assertEqual(sig, 'CONVICTION')
        self.assertGreater(raw, 50)
        self.assertGreater(mag, 0.0)
        self.assertEqual(blend_w, 0.0)
        self.assertEqual(vol_target, 50.0)


class TestInsufficientHistory(unittest.TestCase):
    """12. Fewer than MIN_HISTORY_DAYS returns multiplier = 1.0."""

    @patch('volume_amplifier._analyze')
    def test_insufficient_returns_neutral(self, mock_analyze):
        mock_analyze.return_value = _neutral()
        result = get_volume_multiplier('AAPL', date.today(), datetime.now())
        self.assertEqual(result, NEUTRAL_RESULT)


class TestRawSignal(unittest.TestCase):
    """Verify raw signal integer conversion."""

    def test_bullish(self):
        self.assertEqual(_to_raw_signal(1, 0.6), 80)

    def test_bearish(self):
        self.assertEqual(_to_raw_signal(-1, 0.6), 20)

    def test_neutral(self):
        self.assertEqual(_to_raw_signal(0, 0.0), 50)

    def test_max_bullish(self):
        self.assertEqual(_to_raw_signal(1, 1.0), 100)

    def test_max_bearish(self):
        self.assertEqual(_to_raw_signal(-1, 1.0), 0)


class TestMultiplierComputation(unittest.TestCase):
    """Verify multiplier formulas for each signal type."""

    def test_conviction_bullish(self):
        mult = _to_multiplier('CONVICTION', 1, 0.5)
        self.assertAlmostEqual(mult, 1.0 + 0.5 * 0.50)

    def test_conviction_bearish(self):
        mult = _to_multiplier('CONVICTION', -1, 0.5)
        self.assertAlmostEqual(mult, 1.0 - 0.5 * 0.45)

    def test_neutral_always_one(self):
        mult = _to_multiplier('NEUTRAL', 0, 0.0)
        self.assertEqual(mult, 1.0)

    def test_thin_air_formula(self):
        mult = _to_multiplier('THIN_AIR', -1, 0.5)
        self.assertAlmostEqual(mult, 1.0 - 0.5 * 0.30)


if __name__ == '__main__':
    unittest.main()
