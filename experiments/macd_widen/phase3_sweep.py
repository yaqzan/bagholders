"""
MACD Phase 3 — Cliff + Directional Ramp Above (CORRECTED).

Phase 2 hybrids fired barely (bearish_setup peaked at 0.33 in the 45-50 zone
and was 0 above pre_no_macd=50). Phase 3 fixes by making the "above-cliff"
ramp stretch the full LO->HI range, multiplied by a contradicting-MACD gate.

Final shape:
    if pre_no_macd < LO:                              # cliff (preserve current ship)
        g = 1.0
    else:                                              # directional ramp ABOVE cliff
        ramp        = clip((HI - pre_no_macd) / (HI - LO), 0, 1)
        contradict  = clip((macd - 50) / MACD_K, 0, 1)
        g           = STRENGTH * ramp * contradict

This way:
  - LO=45 (cliff edge): ramp=1.0, full mute if MACD bullish enough
  - HI=60: ramp=0 at pre_no_macd>=60, no extra mute above
  - In between: linear ramp × contradicting gate
  - MACD bearish (<=50): contradict=0 -> no mute (preserves aligned-bearish MACD)

Decision rule (vs binary45):
  - 95+/90+/85+/80+ within +-0.5pp WR15 (no call bleed)
  - Put WR15 strictly improved or +-0.3pp tolerance
  - Put N within +-15%
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import database.utils.scoring as scoring_mod

_ORIGINAL_COMPUTE = scoring_mod.compute_overall_score
MIS_STRESS_CALL_DAMPEN = scoring_mod.MIS_STRESS_CALL_DAMPEN

LO = 45.0
HI = 60.0
MACD_K = 15.0
STRENGTH = 1.0


def _gate_strength(pre_no_macd: float, macd: float) -> float:
    if pre_no_macd < LO:
        return 1.0
    if pre_no_macd >= HI:
        return 0.0
    ramp = (HI - pre_no_macd) / (HI - LO)
    contradict = max(0.0, min(1.0, (macd - 50.0) / MACD_K))
    return min(1.0, STRENGTH * ramp * contradict)


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

    _scale_no_macd = 100.0 / (100.0 - w_macd)
    _pre_no_macd = 50 + (
        (trend               - 50) * w_trend +
        (bb                  - 50) * w_bb +
        (rsi                 - 50) * w_rsi +
        (stoch               - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) * _scale_no_macd / 100

    g = _gate_strength(_pre_no_macd, macd)
    if g > 0.0:
        w_macd_new = w_macd * (1.0 - g)
        scale = (100.0 - w_macd_new) / (100.0 - w_macd)
        w_trend *= scale
        w_bb    *= scale
        w_rsi   *= scale
        w_stoch *= scale
        w_ta    *= scale
        w_macd   = w_macd_new

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
    _eff_mult = (put_regime_multiplier
                 if (overall < 50 and put_regime_multiplier is not None)
                 else regime_multiplier)
    if (mis_stress > 0
            and overall >= 50
            and _eff_mult is not None):
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

    if overall < 28 and weekly_detail is not None:
        _wadj_value = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_value > -17.0:
            _wcf_weakness = max(0.0, min(1.0, (_wadj_value + 17.0) / 17.0))
            if _wcf_weakness > 0:
                _wcf_lift = 0.95 * _wcf_weakness * (50 - overall)
                overall = int(min(100, round(overall + _wcf_lift)))

    if overall >= 75 and weekly_detail is not None:
        _wadj_value = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_value < 1.0:
            _cwcf_weakness = max(0.0, min(1.0, (1.0 - _wadj_value) / 1.0))
            if _cwcf_weakness > 0:
                _cwcf_dampen = 0.95 * _cwcf_weakness * (overall - 55)
                overall = int(max(0, round(overall - _cwcf_dampen)))

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
    global LO, HI, MACD_K, STRENGTH

    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    # First param "binary" reproduces production cliff (LO=45, HI=45.001 -> ramp never fires above)
    # Then sweep HI ∈ {55, 60, 65}, MACD_K ∈ {10, 15, 20}, STRENGTH ∈ {0.5, 1.0}
    variants = [
        ('binary45',          45.0, 45.0001, 15.0, 1.0),  # cliff-only reference
        ('hi55_mk15_s10',     45.0, 55.0,    15.0, 1.0),
        ('hi55_mk15_s05',     45.0, 55.0,    15.0, 0.5),
        ('hi60_mk15_s10',     45.0, 60.0,    15.0, 1.0),
        ('hi60_mk15_s05',     45.0, 60.0,    15.0, 0.5),
        ('hi60_mk10_s10',     45.0, 60.0,    10.0, 1.0),
        ('hi60_mk20_s10',     45.0, 60.0,    20.0, 1.0),
        ('hi65_mk15_s10',     45.0, 65.0,    15.0, 1.0),
    ]

    scoring_mod.compute_overall_score = patched_compute_overall_score
    try:
        from simulator import ScoreSimulator
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)
        from database.models.core import AlgorithmVersion
        active_v = AlgorithmVersion.get_active_scores_version()
        print(f"[setup] active DB version: id={active_v.id}", flush=True)

        for label, lo, hi, mk, st in variants:
            LO, HI, MACD_K, STRENGTH = lo, hi, mk, st
            print("\n" + "=" * 100, flush=True)
            print(f"  {label}  LO={lo} HI={hi} MACD_K={mk} STRENGTH={st}", flush=True)
            print("=" * 100, flush=True)
            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=active_v.id)
            sys.stdout.flush()
    finally:
        scoring_mod.compute_overall_score = _ORIGINAL_COMPUTE


if __name__ == '__main__':
    main()
