"""
Put gradient fix v2 — amplifier-room dampener (Option E).

Thesis derived from experiments/put_pipeline_sensitivity.py (v21, 5y, 114,608 puts):
- 100% of overall=0 signals had base_ws (post-components) > 25 — NOT bearish from
  components alone
- Weekly pushed 99.4% across 25, volume amplified remaining distance
- Volume contributes 12.7pp of deviation at =0 vs 0.5pp at 20-25 (25x spread)
- Capitulation dampener fires 0% at =0 — these are NOT true capitulation
- REJECTION volume (68% at =0) is the main amplifier

Fix: between the amplifier stages (weekly + volume) and the clamp, measure
"component bearishness room" = max(0, 50 - base_ws). When components are not
meaningfully bearish (room < 20 on the 0..50 scale), cap amplifier contribution
proportionally to the room available. This lets true capitulation (deep
components + amplifiers) reach 0, while blocking amplifier-only extremes
(modest components + strong amplifiers) from stacking to 0.

Formula (put side only, weighted_sum < base_ws after amplifiers):
    component_deviation = max(0.0, 50.0 - base_ws)       # 0..50
    room = min(1.0, component_deviation / 20.0)          # 0..1 at ws=30..50
    if weighted_sum < base_ws and room < 1.0:
        amp_contribution = weighted_sum - base_ws        # negative
        dampen = 0.5 + 0.5 * room                        # 0.5..1.0
        weighted_sum = base_ws + amp_contribution * dampen

At base_ws=50 (fully neutral): room=0, dampen=0.5 → amplifiers 50% effective
At base_ws=30 (mildly bearish): room=1, dampen=1.0 → full amplifier contribution
At base_ws=20 (clearly bearish): room=1, dampen=1.0 → unchanged

Applied BEFORE clamp and BEFORE regime/dampeners — those continue to operate
on the post-amplifier score as before.
"""
from __future__ import annotations
import numpy as np

from database.utils import scoring as scoring_mod
from simulator import ScoreSimulator


_orig_compute = scoring_mod.compute_overall_score


def compute_overall_score_v2(
    trend, bb, rsi, macd, stoch, technical_alignment,
    *,
    bb_pct=None, pct_from_ema50=None,
    ws_trend=None, ws_rsi=None, ws_macd=None,
    prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
    vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
    blend_w=0.0, vol_target=50.0,
    macdh_raw=None,
    regime_multiplier=None,
    put_regime_multiplier=None,
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

    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 -  9 * d
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    PUT_MACD_GATE = 45.0
    _scale_no_macd = 100.0 / (100.0 - w_macd)
    _pre_no_macd = 50 + (
        (trend - 50) * w_trend + (bb - 50) * w_bb +
        (rsi - 50) * w_rsi + (stoch - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) * _scale_no_macd / 100
    if _pre_no_macd < PUT_MACD_GATE:
        w_trend *= _scale_no_macd
        w_bb    *= _scale_no_macd
        w_rsi   *= _scale_no_macd
        w_stoch *= _scale_no_macd
        w_ta    *= _scale_no_macd
        w_macd   = 0.0

    _pre_rsi = 50 + (
        (trend - 50) * w_trend + (bb - 50) * w_bb +
        (macd - 50) * w_macd + (stoch - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) / 100
    if _pre_rsi < 40:
        rsi = 50

    weighted_sum = 50 + (
        ((trend  - 50) * w_trend) + ((bb - 50) * w_bb) +
        ((rsi    - 50) * w_rsi)   + ((stoch - 50) * w_stoch) +
        ((macd   - 50) * w_macd)  + ((technical_alignment - 50) * w_ta)
    ) / 100

    # ── Capture base_ws for amplifier-room check ──────────────────────────
    base_ws = weighted_sum

    # ── Weekly adjustment (unchanged from v21) ────────────────────────────
    weekly_detail = None
    if None not in (ws_rsi, ws_macd):
        adj, weekly_detail = scoring_mod.calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd,
            prev_ws_trend, prev_ws_rsi, prev_ws_macd,
        )
        weighted_sum += adj

    # ── Volume amplification (unchanged from v21) ─────────────────────────
    vol_boost = 1.0 + 0.6 * trend_strength
    if pct_from_ema50 is not None:
        ext = abs(pct_from_ema50)
        if ext > 15.0:
            ema_damp = 1.0 - 0.5 * min(1.0, (ext - 15.0) / 25.0)
            vol_boost *= ema_damp

    if blend_w > 0:
        blend_w_eff = min(blend_w * vol_boost, 0.60)
        if abs(weighted_sum - 50) < 25:
            if (vol_target < 35 and macd > 60) or (vol_target > 65 and macd < 40):
                blend_w_eff *= 0.50
        weighted_sum = weighted_sum * (1 - blend_w_eff) + vol_target * blend_w_eff
    elif vol_mult != 1.0:
        if vol_sig == 'CONVICTION':
            if (weighted_sum < 35 and macd > 60) or (weighted_sum > 65 and macd < 40):
                vol_mult = 1.0 + (vol_mult - 1.0) * 0.25
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    # ── v2: Amplifier-room dampener (put side only) ───────────────────────
    # Cap amplifier contribution when components alone don't provide enough
    # bearishness to justify an extreme final score.
    amp_room_applied = False
    amp_dampen = 1.0
    if weighted_sum < base_ws:
        component_deviation = max(0.0, 50.0 - base_ws)
        room = min(1.0, component_deviation / 20.0)
        if room < 1.0:
            amp_dampen = 0.5 + 0.5 * room
            amp_contribution = weighted_sum - base_ws  # negative
            weighted_sum = base_ws + amp_contribution * amp_dampen
            amp_room_applied = True

    pre_regime_overall = int(max(0, min(100, weighted_sum)))

    overall = pre_regime_overall
    _eff_mult = (put_regime_multiplier
                 if (overall < 50 and put_regime_multiplier is not None)
                 else regime_multiplier)
    if _eff_mult is not None and _eff_mult != 1.0:
        if overall >= 50:
            adjusted = 50 + (overall - 50) * _eff_mult
        else:
            adjusted = 50 + (overall - 50) * (2.0 - _eff_mult)
        overall = int(max(0, min(100, round(adjusted))))

    _cap_dampened = False
    if overall == 0 and pct_from_ema50 is not None and pct_from_ema50 < -10.0:
        ext = abs(pct_from_ema50)
        overall = min(20, int(round(5.0 + (ext - 10.0))))
        _cap_dampened = True

    _exh_damp = 0.0
    if overall <= 9 and macdh_raw is not None:
        score_weight = (9 - overall) / 9.0
        mh_pos = max(0.0, float(np.tanh(macdh_raw / 0.05)))
        _exh_damp = score_weight * mh_pos * 0.5
        if _exh_damp > 0.0:
            overall = int(round(overall * (1 - _exh_damp) + 10.0 * _exh_damp))

    _ext_damp = 0.0
    if overall <= 25 and pct_from_ema50 is not None and pct_from_ema50 > 0.0:
        ext_ramp = min(1.0, pct_from_ema50 / 10.0)
        score_weight = (25 - overall) / 25.0
        _ext_damp = 0.5 * ext_ramp * score_weight
        if _ext_damp > 0.0:
            lift = _ext_damp * (25 - overall)
            overall = int(min(100, round(overall + lift)))

    weight_info = {
        'trend': round(w_trend, 1), 'bb': round(w_bb, 1),
        'rsi':   round(w_rsi, 1),   'macd': round(w_macd, 1),
        'stoch': round(w_stoch, 1), 'ta':   round(w_ta, 1),
        'td':    round(trend_dominance, 3),
        'pre_regime': pre_regime_overall,
    }
    if amp_room_applied:
        weight_info['amp_dampen'] = round(amp_dampen, 3)
        weight_info['base_ws'] = round(base_ws, 1)
    if _cap_dampened:
        weight_info['cap_dampened'] = True
    if _exh_damp > 0.01:
        weight_info['exh_damp'] = round(_exh_damp, 3)
    if _ext_damp > 0.01:
        weight_info['ext_damp'] = round(_ext_damp, 3)
    if put_regime_multiplier is not None:
        weight_info['put_regime_mult'] = round(put_regime_multiplier, 4)
    if weekly_detail:
        weight_info.update(weekly_detail)

    vol_update = {'volume': vol_raw, 'volume_signal': vol_sig, 'volume_magnitude': vol_mag} \
        if vol_sig != 'NEUTRAL' else None

    return overall, weight_info, vol_update


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=1825)
    parser.add_argument('--symbols', nargs='*', default=None)
    args = parser.parse_args()

    print("Put gradient fix v2: amplifier-room dampener (Option E)")
    print(f"Lookback: {args.days}d | Symbols: {'ALL' if not args.symbols else len(args.symbols)}")
    print("=" * 90)

    from database.models.core import AlgorithmVersion
    active_ver = AlgorithmVersion.get_active_scores_version()
    print(f"Active DB version: v{active_ver.id} ({active_ver.git_commit[:8]})")

    scoring_mod.compute_overall_score = compute_overall_score_v2

    sim = ScoreSimulator(symbols=args.symbols, lookback_days=args.days)
    sim_scores = sim.simulate()
    print(f"Simulated {len(sim_scores)} scores")

    sim.diff_assess(sim_scores, db_version=active_ver)


if __name__ == '__main__':
    main()
