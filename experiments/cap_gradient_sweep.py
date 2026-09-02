"""
Capitulation gradient dampener — diff-assess sweep.

Tests gradient formulas for the score=0 + pct_from_ema50 < -X%
capitulation lift, without touching scoring.py or the DB.

Variants tested:
  binary_20   — binary lift: score=0 + ext<-10% → 20  (reference for comparison)
  grad_1pt    — score=0 + ext<-10%: lift_to = min(20, round(5 + (ext-10)))
                  ext=10%→5, ext=15%→10, ext=20%→15, ext=25%+→20
  grad_0pt5   — shallower: lift_to = min(20, round(2 + (ext-10)*0.75))
                  ext=10%→2, ext=20%→9, ext=30%→19
  grad_strict — threshold at -15%: lift_to = min(20, round(5 + (ext-15)))
                  ext=15%→5, ext=20%→10, ext=25%→15, ext=30%+→20

Usage:
    python experiments/cap_gradient_sweep.py [days]
    default days = 1825 (5y)
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.utils.scoring import compute_overall_score


def _make_fn(threshold: float, lift_fn):
    """
    Returns a scoring_fn that calls compute_overall_score normally, then
    applies the gradient lift on score=0 when pct_from_ema50 < -threshold.

    Note: scoring_fn receives raw_price + raw_ema50 (not pct_from_ema50),
    so we compute it here.
    """
    def fn(trend, bb, rsi, macd, stoch, ta, *,
           raw_price=None, raw_ema50=None, bb_pct=None,
           ws_trend=None, ws_rsi=None, ws_macd=None,
           prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
           vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
           blend_w=0.0, vol_target=50.0, raw_rsi=None, raw_stoch=None,
           raw_macd_hist=None, raw_upper=None, raw_lower=None,
           raw_middle=None, regime_multiplier=None, **_):
        pct_ema = None
        if raw_price is not None and raw_ema50 and raw_ema50 != 0:
            pct_ema = (raw_price - raw_ema50) / raw_ema50 * 100

        overall, _, _ = compute_overall_score(
            trend, bb, rsi, macd, stoch, ta,
            bb_pct=bb_pct,
            pct_from_ema50=pct_ema,
            ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
            prev_ws_trend=prev_ws_trend, prev_ws_rsi=prev_ws_rsi,
            prev_ws_macd=prev_ws_macd,
            vol_mult=vol_mult, vol_raw=vol_raw,
            vol_sig=vol_sig, vol_mag=vol_mag,
            blend_w=blend_w, vol_target=vol_target,
            macdh_raw=raw_macd_hist,
            regime_multiplier=regime_multiplier,
        )
        if overall is None:
            return None

        if overall == 0 and pct_ema is not None and pct_ema < -threshold:
            ext = abs(pct_ema)
            overall = lift_fn(ext)

        return overall
    return fn


VARIANTS = {
    'binary_20':   _make_fn(10.0, lambda e: 20),
    'grad_1pt':    _make_fn(10.0, lambda e: min(20, int(round(5.0 + (e - 10.0))))),
    'grad_0pt5':   _make_fn(10.0, lambda e: min(20, int(round(2.0 + (e - 10.0) * 0.75)))),
    'grad_strict': _make_fn(15.0, lambda e: min(20, int(round(5.0 + (e - 15.0))))),
}


def main():
    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        days = int(float(tok[:-1]) * 365) if tok.endswith('y') else int(tok)

    from simulator import ScoreSimulator
    from assess_scores import print_diff_assessment

    # Load data once — all variants share the same StockContext cache
    base_sim = ScoreSimulator(lookback_days=days)

    for name, fn in VARIANTS.items():
        print(f"\n{'='*100}")
        print(f"  Variant: {name}")
        print('='*100)
        # Run variant (same data, different scoring_fn)
        variant_sim = ScoreSimulator(lookback_days=days, scoring_fn=fn)
        scores = variant_sim.simulate()
        base_sim.diff_assess(scores)


if __name__ == '__main__':
    main()
