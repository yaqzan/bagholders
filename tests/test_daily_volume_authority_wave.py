import unittest

from database.utils.scoring import _apply_daily_volume_authority_wave


class DailyVolumeAuthorityWaveTests(unittest.TestCase):
    def test_conviction_lifts_target_zone(self):
        score, meta = _apply_daily_volume_authority_wave(
            75,
            vol_sig='CONVICTION',
            vol_mag=0.09,
            pct_from_ema50=0.0,
            wv_force1=0.005,
        )

        self.assertGreater(score, 75)
        self.assertIsNotNone(meta)
        self.assertEqual(meta['before'], 75)
        self.assertEqual(meta['after'], score)

    def test_non_conviction_is_unchanged(self):
        score, meta = _apply_daily_volume_authority_wave(
            75,
            vol_sig='REVERSAL',
            vol_mag=0.09,
            pct_from_ema50=0.0,
            wv_force1=0.005,
        )

        self.assertEqual(score, 75)
        self.assertIsNone(meta)

    def test_smooth_fade_preserves_90_plus_tier(self):
        score, meta = _apply_daily_volume_authority_wave(
            92,
            vol_sig='CONVICTION',
            vol_mag=0.75,
            pct_from_ema50=40.0,
            wv_force1=0.20,
            price_impulse_sigma=2.0,
            log_dv_expansion=1.5,
        )

        self.assertEqual(score, 92)
        self.assertIsNone(meta)

    def test_weekly_climax_has_less_authority_than_constructive_force(self):
        constructive_score, constructive_meta = _apply_daily_volume_authority_wave(
            75,
            vol_sig='CONVICTION',
            vol_mag=0.09,
            pct_from_ema50=0.0,
            wv_force1=0.005,
        )
        climax_score, climax_meta = _apply_daily_volume_authority_wave(
            75,
            vol_sig='CONVICTION',
            vol_mag=0.09,
            pct_from_ema50=0.0,
            wv_force1=0.20,
        )

        self.assertGreaterEqual(constructive_score, climax_score)
        self.assertIsNotNone(constructive_meta)
        self.assertIsNotNone(climax_meta)
        self.assertGreater(constructive_meta['authority'], climax_meta['authority'])


if __name__ == '__main__':
    unittest.main()
