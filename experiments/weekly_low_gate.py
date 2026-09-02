"""
Weekly-composite LOW gate. When weighted_sum < 35 and weekly composite
(computed from ws_trend/rsi/macd) is >= 50 (contradicts bearish thesis),
pull score back toward 50 proportional to how positive weekly is.

Evidence (v14 DB 2y, drop>rise classifier on 7,424 LOW signals):
  ws<50  -> WR 45.6% (N=4818)
  ws>=50 -> WR 37.5% (N=2532)
  spread: 8.1pp
"""
import numpy as np
from database.utils.scoring import calculate_weekly_adjustment, calculate_weekly_composite


def scoring_fn(
    trend, bb, rsi, macd, stoch, technical_alignment,
    *, bb_pct=None, pct_from_ema50=None,
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
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    # ── Weekly-composite LOW gate ─────────────────────────────────────
    # Deep bearish score + weekly contradicting = oversold bounce setup.
    # Pull toward 50 proportional to how bullish the weekly is.
    if weighted_sum < 35 and None not in (ws_rsi, ws_macd):
        wk_comp = calculate_weekly_composite(ws_rsi, ws_macd, ws_trend) or 50
        if wk_comp >= 55:
            # weekly_bull: 0 at wk=55, 1 at wk>=75
            weekly_bull = min(1.0, max(0.0, (wk_comp - 55) / 20.0))
            exhaustion  = min(1.0, max(0.0, (35 - weighted_sum) / 25.0))
            damp_weight = 0.35 * weekly_bull * exhaustion  # max 0.35
            weighted_sum = weighted_sum * (1 - damp_weight) + 50.0 * damp_weight

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
    import sys, random
    from simulator import run
    from database.models.core import Stock
    argv = sys.argv[1:]
    if argv and argv[0].isdigit():
        n = int(argv[0])
        all_syms = [s.symbol for s in Stock.select(Stock.symbol).where(Stock.forward_pe.is_null(False))]
        random.seed(42)
        syms = random.sample(all_syms, min(n, len(all_syms)))
    else:
        syms = argv
    print(f"[weekly_low_gate] {len(syms)} stocks")
    run(symbols=syms, days=730, do_diff_assess=True, scoring_fn=scoring_fn)
