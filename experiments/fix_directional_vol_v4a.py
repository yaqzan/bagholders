"""
Iteration 4a: raise blend cap to 0.60 (matches ABSORPTION path).
"""
import math
import numpy as np
from database.utils.scoring import calculate_weekly_adjustment

BLEND_CAP = 0.60  # was 0.45 in v3


def scoring_fn(
    trend, bb, rsi, macd, stoch, technical_alignment,
    *,
    bb_pct=None, pct_from_ema50=None,
    ws_trend=None, ws_rsi=None, ws_macd=None,
    prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
    vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
    blend_w=0.0, vol_target=50.0,
    macdh_raw=None, regime_multiplier=None,
    **_ignored,
):
    if None in (trend, bb, rsi, stoch, macd, technical_alignment):
        return None

    trend_bias      = float(np.tanh((trend - 50) * 0.06))
    trend_strength  = abs(trend_bias)
    trend_dominance = trend_strength ** 0.7

    if bb_pct is not None:
        bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
        bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
        trend_dominance *= (1.0 - 0.5 * (bull_ext + bear_ext))

    osc_avg = (rsi + macd) / 2.0
    bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
    bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
    osc_divergence = bull_div + bear_div
    if bb_pct is not None:
        osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
    trend_dominance *= (1.0 - 0.7 * osc_divergence)

    momentum_confirmation = 1.0
    if macdh_raw is not None and trend_strength > 0.15:
        macdh_dir = float(np.tanh(macdh_raw / 0.05))
        alignment = macdh_dir * float(np.sign(trend_bias))
        momentum_confirmation = 0.5 + 0.5 * alignment
        trend_dominance *= (0.60 + 0.40 * momentum_confirmation)

    d = trend_dominance
    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 -  9 * d
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    _pre_rsi = 50 + (
        (trend - 50) * w_trend + (bb - 50) * w_bb +
        (macd - 50) * w_macd + (stoch - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) / 100
    if _pre_rsi < 40:
        rsi = 50

    weighted_sum = 50 + (
        ((trend - 50) * w_trend) + ((bb - 50) * w_bb) +
        ((rsi - 50) * w_rsi) + ((stoch - 50) * w_stoch) +
        ((macd - 50) * w_macd) + ((technical_alignment - 50) * w_ta)
    ) / 100

    if None not in (ws_rsi, ws_macd):
        adj, _ = calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd,
            prev_ws_trend, prev_ws_rsi, prev_ws_macd,
        )
        weighted_sum += adj

    vol_boost = 1.0 + 0.6 * trend_strength
    if pct_from_ema50 is not None:
        ext = abs(pct_from_ema50)
        if ext > 15.0:
            ema_damp = 1.0 - 0.5 * min(1.0, (ext - 15.0) / 25.0)
            vol_boost *= ema_damp

    if blend_w > 0:
        blend_w = min(blend_w * vol_boost, 0.60)
        if abs(weighted_sum - 50) < 25:
            if (vol_target < 35 and macd > 60) or (vol_target > 65 and macd < 40):
                blend_w *= 0.50
        weighted_sum = weighted_sum * (1 - blend_w) + vol_target * blend_w
    elif vol_mult != 1.0:
        if vol_sig in ('CONVICTION', 'REJECTION'):
            vol_dir = 1 if vol_mult > 1.0 else -1
            target  = 100.0 if vol_dir == 1 else 0.0
            mag_eff = vol_mag
            if vol_sig == 'CONVICTION':
                if (vol_dir == 1 and macd < 40 and weighted_sum < 65) or \
                   (vol_dir == -1 and macd > 60 and weighted_sum > 35):
                    mag_eff *= 0.25
            bw = min(mag_eff * 0.55 * vol_boost, BLEND_CAP)
            weighted_sum = weighted_sum * (1 - bw) + target * bw
        else:
            deviation = (vol_mult - 1.0) * vol_boost
            weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    pre_regime_overall = int(max(0, min(100, weighted_sum)))
    overall = pre_regime_overall
    if regime_multiplier is not None and regime_multiplier != 1.0:
        if overall >= 50:
            adjusted = 50 + (overall - 50) * regime_multiplier
        else:
            adjusted = 50 + (overall - 50) * (2.0 - regime_multiplier)
        overall = int(max(0, min(100, round(adjusted))))

    return overall


if __name__ == '__main__':
    import sys
    from simulator import run
    syms = sys.argv[1:]
    print(f"[v4a: blend cap 0.60] {len(syms)} stocks")
    run(symbols=syms, days=730, do_diff_assess=True, scoring_fn=scoring_fn)
