"""Score-stage put floor gated on weekly strength.

The user's insight: removing entirely (entry filter WW_B) is one end of the
gradient; partial rebucketing (dyn_a) is the other. Test the middle and
strong-end of the gradient at the score stage.

Mechanism: compute a 'weekly_strength' from the weekly_adj magnitude. Use
that to determine a MINIMUM allowed put score. When weekly is weak, the
score cannot dip below 25 (signal exits put range entirely — same effect
as entry filter). When weekly is strong, no floor (current behavior).

Three variants (varying lift-target and gating slope):

  V_grad_05:   target=26, k=0.5        soft graded
  V_grad_10:   target=26, k=1.0        full graded (weak weekly -> hard floor at 26)
  V_hard:      binary floor when wadj > -10                     binary

Per-trade diff-assess against active DB v26.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import database.utils.scoring as scoring_mod

_ORIGINAL_COMPUTE = scoring_mod.compute_overall_score

VARIANT = 'baseline'
MIS_STRESS_CALL_DAMPEN = scoring_mod.MIS_STRESS_CALL_DAMPEN


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
    put_regime_multiplier=None,
    mis_stress=0.0,
    **_extra,
):
    if None in (trend, bb, rsi, stoch, macd, technical_alignment):
        return None, {}, None

    # Replicate v26 production scoring exactly
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
    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 -  9 * d
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    PUT_MACD_GATE = 45.0
    _scale_no_macd = 100.0 / (100.0 - w_macd)
    _pre_no_macd = 50 + (
        (trend - 50) * w_trend + (bb - 50) * w_bb + (rsi - 50) * w_rsi +
        (stoch - 50) * w_stoch + (technical_alignment - 50) * w_ta
    ) * _scale_no_macd / 100
    if _pre_no_macd < PUT_MACD_GATE:
        w_trend *= _scale_no_macd; w_bb *= _scale_no_macd
        w_rsi *= _scale_no_macd; w_stoch *= _scale_no_macd
        w_ta *= _scale_no_macd; w_macd = 0.0

    _pre_rsi = 50 + (
        (trend - 50) * w_trend + (bb - 50) * w_bb + (macd - 50) * w_macd +
        (stoch - 50) * w_stoch + (technical_alignment - 50) * w_ta
    ) / 100
    if _pre_rsi < 40:
        rsi = 50

    weighted_sum = 50 + (
        ((trend - 50) * w_trend) + ((bb - 50) * w_bb) + ((rsi - 50) * w_rsi) +
        ((stoch - 50) * w_stoch) + ((macd - 50) * w_macd) +
        ((technical_alignment - 50) * w_ta)
    ) / 100

    weekly_detail = None
    wadj_value = 0.0
    if None not in (ws_rsi, ws_macd):
        adj, weekly_detail = scoring_mod.calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd,
            prev_ws_trend, prev_ws_rsi, prev_ws_macd,
        )
        weighted_sum += adj
        if weekly_detail:
            wadj_value = float(weekly_detail.get('w_adj', 0.0))

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

    _eff_mult = (put_regime_multiplier
                 if (overall < 50 and put_regime_multiplier is not None)
                 else regime_multiplier)
    if (mis_stress > 0 and overall >= 50 and _eff_mult is not None):
        _eff_mult = 1.0 + (_eff_mult - 1.0) * (1.0 - mis_stress * MIS_STRESS_CALL_DAMPEN)

    if _eff_mult is not None and _eff_mult != 1.0:
        if overall >= 50:
            adjusted = 50 + (overall - 50) * _eff_mult
        else:
            adjusted = 50 + (overall - 50) * (2.0 - _eff_mult)
        overall = int(max(0, min(100, round(adjusted))))

    if overall == 0 and pct_from_ema50 is not None and pct_from_ema50 < -10.0:
        ext = abs(pct_from_ema50)
        overall = min(20, int(round(5.0 + (ext - 10.0))))

    if overall <= 9 and macdh_raw is not None:
        score_weight = (9 - overall) / 9.0
        mh_pos = max(0.0, float(np.tanh(macdh_raw / 0.05)))
        _exh_damp = score_weight * mh_pos * 0.5
        if _exh_damp > 0.0:
            overall = int(round(overall * (1 - _exh_damp) + 10.0 * _exh_damp))

    if overall <= 25 and pct_from_ema50 is not None and pct_from_ema50 > 0.0:
        ext_ramp = min(1.0, pct_from_ema50 / 10.0)
        score_weight = (25 - overall) / 25.0
        _ext_damp = 0.5 * ext_ramp * score_weight
        if _ext_damp > 0.0:
            lift = _ext_damp * (25 - overall)
            overall = int(min(100, round(overall + lift)))

    # ── NEW: weekly-gated put floor ──
    # When weekly is weak (wadj close to 0), require score to clear put range.
    # When weekly is strong (wadj <= -13), no floor.
    if VARIANT != 'baseline' and overall < 25 and weekly_detail is not None:
        # Weakness: 0 at wadj <= -13 (strong), 1 at wadj >= 0 (weak/no drag)
        weakness = max(0.0, min(1.0, (wadj_value + 13.0) / 13.0))

        if VARIANT == 'grad_05':
            # Soft: lift toward 26 by k=0.5 * weakness
            target = 26
            if weakness > 0:
                gap = target - overall
                lift = 0.5 * weakness * gap
                overall = int(min(100, round(overall + lift)))
        elif VARIANT == 'grad_10':
            # Full graded: lift toward 26 fully proportional to weakness
            target = 26
            if weakness > 0:
                gap = target - overall
                lift = weakness * gap
                overall = int(min(100, round(overall + lift)))
        elif VARIANT == 'hard':
            # Binary: when weak (wadj > -10), force score to 26+
            if wadj_value > -10.0:
                overall = max(overall, 26)
        elif VARIANT == 'mid':
            # Match WW_B threshold (-13) and lift target 26
            if wadj_value > -13.0:
                # Lift proportional to how shallow the score is
                # Deep score (=0) gets full lift; near-25 score gets minimal lift
                if overall <= 15:
                    overall = 26  # full lift for [0,15] (matches WW_B range)

    weight_info = {
        'trend': round(w_trend, 1), 'bb': round(w_bb, 1),
        'rsi': round(w_rsi, 1), 'macd': round(w_macd, 1),
        'stoch': round(w_stoch, 1), 'ta': round(w_ta, 1),
        'td': round(trend_dominance, 3),
        'pre_regime': pre_regime_overall,
    }
    if weekly_detail:
        weight_info.update(weekly_detail)

    vol_update = {'volume': vol_raw, 'volume_signal': vol_sig, 'volume_magnitude': vol_mag} \
        if vol_sig != 'NEUTRAL' else None

    return overall, weight_info, vol_update


def main():
    global VARIANT

    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    variants = [
        'baseline',  # = current production
        'grad_05',   # k=0.5 graded lift
        'grad_10',   # k=1.0 full graded lift
        'mid',       # mirror of WW_B at score stage
    ]

    scoring_mod.compute_overall_score = patched_compute_overall_score

    try:
        from simulator import ScoreSimulator
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for v in variants:
            VARIANT = v
            print("\n" + "=" * 100, flush=True)
            print(f"  VARIANT: {v}", flush=True)
            print("=" * 100, flush=True)
            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=26)
            sys.stdout.flush()
    finally:
        scoring_mod.compute_overall_score = _ORIGINAL_COMPUTE


if __name__ == '__main__':
    main()
