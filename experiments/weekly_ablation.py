"""
Weekly composite ablation — simulates scoring WITHOUT the weekly RSI/MACD
adjustment and diff-assesses the result against stored v17 DB scores.

Usage:
    python experiments/weekly_ablation.py          # 5y default
    python experiments/weekly_ablation.py 3y
"""
from __future__ import annotations
import sys
from database.utils.scoring import compute_overall_score


_ALLOWED = {'bb_pct', 'pct_from_ema50', 'ws_trend', 'ws_rsi', 'ws_macd',
            'prev_ws_trend', 'prev_ws_rsi', 'prev_ws_macd',
            'vol_mult', 'vol_raw', 'vol_sig', 'vol_mag', 'blend_w',
            'vol_target', 'macdh_raw', 'regime_multiplier'}


def scoring_fn_no_weekly(trend, bb, rsi, macd, stoch, ta, **kwargs):
    kw = {k: v for k, v in kwargs.items() if k in _ALLOWED}
    for k in ('ws_trend', 'ws_rsi', 'ws_macd',
              'prev_ws_trend', 'prev_ws_rsi', 'prev_ws_macd'):
        kw[k] = None
    overall, _, _ = compute_overall_score(trend, bb, rsi, macd, stoch, ta, **kw)
    return overall


def main():
    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    from simulator import ScoreSimulator
    sim = ScoreSimulator(symbols=None, lookback_days=days,
                         scoring_fn=scoring_fn_no_weekly)
    scores = sim.simulate()
    sim.diff_assess(scores, db_version=17)


if __name__ == '__main__':
    main()
