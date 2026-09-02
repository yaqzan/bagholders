"""
RSI Smooth Gate Sweep — Priority #12 gate C.

Current production gate ([scoring.py:116](../database/utils/scoring.py#L116)):

    if _pre_rsi < 40:
        rsi = 50

This is a hard binary discontinuity at _pre_rsi=40. The Priority #12 audit
catalogues it as smoothing candidate C with the proposed form:

    pull = tanh(max(0, 40 - _pre_rsi) / k)   # 0 if _pre_rsi >= 40, ramps to 1
    rsi = rsi_orig + (50 - rsi_orig) * pull

Sweep dimensions tested below:
  - shape: 'binary' (current), 'tanh_ramp' (one-sided, anchored at threshold),
           'sigmoid' (continuous around midpoint, no hard threshold)
  - k:     sharpness parameter (smaller = sharper, larger = more gradual)
  - thr:   activation threshold for tanh_ramp; midpoint for sigmoid

Goal: find a smooth shape that preserves or improves per-trade WR15 across the
six call buckets (70+/75+/80+/85+/90+/95+) AND the six put buckets, while
giving each gate decision a continuous gradient instead of a step function.

Mirrors the proven monkey-patch pattern from put_rsi_gate_redist.py.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import math
import numpy as np
import database.utils.scoring as scoring_mod

_ORIGINAL_COMPUTE = scoring_mod.compute_overall_score
MIS_STRESS_CALL_DAMPEN = scoring_mod.MIS_STRESS_CALL_DAMPEN

# Variant config (set by main loop, read by patched fn)
SHAPE = 'binary'   # 'binary' | 'tanh_ramp' | 'sigmoid' | 'off'
K = 4.0
THRESHOLD = 40.0


def _gate_pull(pre_rsi: float) -> float:
    """Return scalar in [0,1]: how strongly to pull rsi toward 50.

    binary    : 1.0 if pre_rsi < THRESHOLD else 0.0  (current production)
    tanh_ramp : tanh(max(0, THRESHOLD - pre_rsi) / K)  -- preserves threshold
    sigmoid   : 1 / (1 + exp((pre_rsi - THRESHOLD) / K))  -- no hard threshold
    off       : 0.0 (gate disabled)
    """
    if SHAPE == 'off':
        return 0.0
    if SHAPE == 'binary':
        return 1.0 if pre_rsi < THRESHOLD else 0.0
    if SHAPE == 'tanh_ramp':
        gap = max(0.0, THRESHOLD - pre_rsi)
        return math.tanh(gap / K)
    if SHAPE == 'sigmoid':
        # Numerically stable form
        z = (pre_rsi - THRESHOLD) / K
        if z > 50: return 0.0
        if z < -50: return 1.0
        return 1.0 / (1.0 + math.exp(z))
    raise ValueError(f"unknown SHAPE: {SHAPE}")


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

    PUT_MACD_GATE = 45.0
    _scale_no_macd = 100.0 / (100.0 - w_macd)
    _pre_no_macd = 50 + (
        (trend               - 50) * w_trend +
        (bb                  - 50) * w_bb +
        (rsi                 - 50) * w_rsi +
        (stoch               - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) * _scale_no_macd / 100
    if _pre_no_macd < PUT_MACD_GATE:
        w_trend *= _scale_no_macd
        w_bb    *= _scale_no_macd
        w_rsi   *= _scale_no_macd
        w_stoch *= _scale_no_macd
        w_ta    *= _scale_no_macd
        w_macd   = 0.0

    # ── Smoothed RSI gate (this is the experimental variant) ─────────────
    _pre_rsi = 50 + (
        (trend               - 50) * w_trend +
        (bb                  - 50) * w_bb +
        (macd                - 50) * w_macd +
        (stoch               - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) / 100
    pull = _gate_pull(_pre_rsi)
    if pull > 0.0:
        rsi = rsi + (50.0 - rsi) * pull
    # NOTE: w_rsi is NOT redistributed (matches current production behavior;
    # redistribution was tested + falsified in put_rsi_gate_redist.py).

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

    # WCF lift (v27, ad02704) — mirrors production exactly
    if overall < 28 and weekly_detail is not None:
        _wadj_value = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_value > -17.0:
            _wcf_weakness = max(0.0, min(1.0, (_wadj_value + 17.0) / 17.0))
            if _wcf_weakness > 0:
                _wcf_lift = 0.95 * _wcf_weakness * (50 - overall)
                overall = int(min(100, round(overall + _wcf_lift)))

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
    global SHAPE, K, THRESHOLD

    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    # (label, shape, k, threshold) — trimmed first-pass screen
    variants = [
        ('off',       'off',       0.0, 40.0),  # gate disabled (sanity floor)
        ('binary40',  'binary',    0.0, 40.0),  # current production (reference)
        ('tanh_k3',   'tanh_ramp', 3.0, 40.0),  # near-binary smoothing
        ('tanh_k6',   'tanh_ramp', 6.0, 40.0),  # moderate
        ('tanh_k12',  'tanh_ramp',12.0, 40.0),  # gradual
    ]

    scoring_mod.compute_overall_score = patched_compute_overall_score
    try:
        from simulator import ScoreSimulator
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)
        from database.models.core import AlgorithmVersion
        active_v = AlgorithmVersion.get_active_scores_version()
        print(f"[setup] active DB version: id={active_v.id}", flush=True)

        for label, shape, k, thr in variants:
            SHAPE, K, THRESHOLD = shape, k, thr
            print("\n" + "=" * 100, flush=True)
            print(f"  {label}  shape={shape} k={k} threshold={thr}", flush=True)
            print("=" * 100, flush=True)
            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=active_v.id)
            sys.stdout.flush()
    finally:
        scoring_mod.compute_overall_score = _ORIGINAL_COMPUTE


if __name__ == '__main__':
    main()
