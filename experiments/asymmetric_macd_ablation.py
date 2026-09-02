"""
Asymmetric MACD ablation sweep — zero MACD only on bearish setups
(where the pre-MACD weighted sum is < PUT_GATE), redistribute its weight
proportionally to the other components.

Rationale: component_ablation_sweep.py (1y) found that zeroing MACD lifts
put WR dramatically (<25 +2.9pp, <15 +6.1pp, with N nearly doubling) while
leaving call side largely intact (80+ +2.1pp, 75+ flat). MACD is a lagging
momentum indicator — on bearish setups it stays positive until breakdown
is confirmed, suppressing put scores. On calls it still carries real info.

This sweep tests gating the MACD zero to bearish direction only.

Gate: computed on a pre-MACD weighted_sum (zero MACD, redistribute weight,
measure). If pre_no_macd < PUT_GATE → MACD stays zeroed (bearish branch).
Else → MACD is used normally (bullish branch).

Gates tested: PUT_GATE ∈ [40, 45, 50] — where 50 is "zero MACD on every
below-neutral setup", stricter values gate more tightly.

Single 5y data load — re-runs diff-assess per gate.

Usage:
    python -m experiments.asymmetric_macd_ablation           # 5y
    python -m experiments.asymmetric_macd_ablation 3y
"""
from __future__ import annotations
import sys
import numpy as np
import database.utils.scoring as scoring_mod

_ORIGINAL_COMPUTE = scoring_mod.compute_overall_score

PUT_GATE: float = 50.0


def _weights(d: float):
    return {
        'trend': 18 + 10 * d,
        'bb':    18.0,
        'rsi':   25 - 9 * d,
        'macd':  25 - 6 * d,
        'stoch': 5.0,
        'ta':    9 + 6 * d,
    }


def _renormalize_zero(w: dict, key: str) -> dict:
    removed = w[key]
    w_new = dict(w)
    w_new[key] = 0.0
    remaining_total = sum(v for k, v in w.items() if k != key)
    if remaining_total > 0:
        scale = (remaining_total + removed) / remaining_total
        for k in w_new:
            if k != key:
                w_new[k] *= scale
    return w_new


def patched_compute_overall_score(
    trend, bb, rsi, macd, stoch, technical_alignment,
    *,
    bb_pct=None,
    pct_from_ema50=None,
    ws_trend=None, ws_rsi=None, ws_macd=None,
    prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
    vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
    blend_w=0.0, vol_target=50.0,
    macdh_raw=None,
    regime_multiplier=None,
):
    if None in (trend, bb, rsi, stoch, macd, technical_alignment):
        return None, {}, None

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

    d = trend_dominance
    w_full = _weights(d)
    w_no_macd = _renormalize_zero(w_full, 'macd')

    pre_no_macd = 50 + (
        (trend               - 50) * w_no_macd['trend'] +
        (bb                  - 50) * w_no_macd['bb'] +
        (rsi                 - 50) * w_no_macd['rsi'] +
        (stoch               - 50) * w_no_macd['stoch'] +
        (technical_alignment - 50) * w_no_macd['ta']
    ) / 100

    w = w_no_macd if pre_no_macd < PUT_GATE else w_full

    w_trend, w_bb, w_rsi, w_macd, w_stoch, w_ta = (
        w['trend'], w['bb'], w['rsi'], w['macd'], w['stoch'], w['ta']
    )

    _pre_rsi = 50 + (
        (trend               - 50) * w_trend +
        (bb                  - 50) * w_bb +
        (macd                - 50) * w_macd +
        (stoch               - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) / 100
    if _pre_rsi < 40:
        rsi = 50

    weighted_sum = 50 + (
        ((trend  - 50) * w_trend) +
        ((bb     - 50) * w_bb) +
        ((rsi    - 50) * w_rsi) +
        ((stoch  - 50) * w_stoch) +
        ((macd   - 50) * w_macd) +
        ((technical_alignment - 50) * w_ta)
    ) / 100

    weekly_detail = None
    if None not in (ws_rsi, ws_macd):
        adj, weekly_detail = scoring_mod.calculate_weekly_adjustment(
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
        if vol_sig == 'CONVICTION':
            if (weighted_sum < 35 and macd > 60) or (weighted_sum > 65 and macd < 40):
                vol_mult = 1.0 + (vol_mult - 1.0) * 0.25
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

    weight_info = {
        'trend': round(w_trend, 1), 'bb': round(w_bb, 1),
        'rsi':   round(w_rsi, 1),   'macd': round(w_macd, 1),
        'stoch': round(w_stoch, 1), 'ta':   round(w_ta, 1),
        'td':    round(trend_dominance, 3),
        'pre_regime': pre_regime_overall,
    }
    if weekly_detail:
        weight_info.update(weekly_detail)

    vol_update = {'volume': vol_raw, 'volume_signal': vol_sig, 'volume_magnitude': vol_mag} \
        if vol_sig != 'NEUTRAL' else None

    return overall, weight_info, vol_update


def main():
    global PUT_GATE

    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    gates = [40.0, 45.0, 50.0]

    scoring_mod.compute_overall_score = patched_compute_overall_score

    try:
        from simulator import ScoreSimulator
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for g in gates:
            PUT_GATE = g
            print("\n" + "=" * 100)
            print(f"  PUT_GATE = {g:.0f}   (MACD zeroed when pre_no_macd < {g:.0f})")
            print("=" * 100)
            scores = sim.simulate()
            sim.diff_assess(scores, db_version=17)
    finally:
        scoring_mod.compute_overall_score = _ORIGINAL_COMPUTE


if __name__ == '__main__':
    main()
