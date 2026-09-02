"""
Score variant scoring functions for experimentation.

Each variant has the signature of compute_overall_score() PLUS extra raw kwargs
passed by simulator.py: raw_rsi, raw_stoch, raw_macd_hist, raw_price,
raw_ema50, raw_upper, raw_lower, raw_middle.

All variants delegate to compute_overall_score and then transform the result,
EXCEPT V6 which calls a copy of the function with modified weights.
"""
import numpy as np
from database.utils.scoring import compute_overall_score, calculate_weekly_adjustment


# ─────────────────────────────────────────────────────────────────────────────
# Helper: replicate the conf_ratio calculation from assess_scores._signal_context
# ─────────────────────────────────────────────────────────────────────────────
def _conf_ratio(is_high, raw_rsi, raw_stoch, raw_macd_hist,
                raw_price, raw_ema50, raw_upper, raw_lower, raw_middle):
    if raw_price is None:
        return 0.0
    points, max_pts = 0, 8
    ema50_pct = (raw_price - raw_ema50) / raw_ema50 * 100 if raw_ema50 else None
    if is_high:
        if raw_rsi is not None:
            points += 2 if raw_rsi > 70 else (1 if raw_rsi > 60 else 0)
        if raw_upper is not None and raw_lower is not None and raw_middle is not None:
            points += 2 if raw_price >= raw_upper else (1 if raw_price >= raw_middle else 0)
        if raw_stoch is not None:
            points += 1 if raw_stoch > 70 else 0
        if raw_macd_hist is not None:
            points += 1 if raw_macd_hist > 0 else 0
        if ema50_pct is not None:
            points += 2 if ema50_pct > 5 else (1 if ema50_pct > 0 else 0)
    else:
        if raw_rsi is not None:
            points += 2 if raw_rsi < 30 else (1 if raw_rsi < 40 else 0)
        if raw_upper is not None and raw_lower is not None and raw_middle is not None:
            points += 2 if raw_price <= raw_lower else (1 if raw_price <= raw_middle else 0)
        if raw_stoch is not None:
            points += 1 if raw_stoch < 30 else 0
        if raw_macd_hist is not None:
            points += 1 if raw_macd_hist < 0 else 0
        if ema50_pct is not None:
            points += 2 if ema50_pct < -5 else (1 if ema50_pct < 0 else 0)
    return points / max_pts


# ─────────────────────────────────────────────────────────────────────────────
# V1 — Raw-confirmation gate
# ─────────────────────────────────────────────────────────────────────────────
def variant_v1_raw_gate(trend, bb, rsi, macd, stoch, ta, **kw):
    """Cap |overall - 50| at 20 unless raw indicators confirm direction."""
    raw_rsi = kw.pop('raw_rsi', None)
    raw_stoch = kw.pop('raw_stoch', None)
    raw_price = kw.pop('raw_price', None)
    raw_ema50 = kw.pop('raw_ema50', None)
    raw_upper = kw.pop('raw_upper', None)
    raw_lower = kw.pop('raw_lower', None)
    raw_middle = kw.pop('raw_middle', None)
    raw_macd_hist = kw.pop('raw_macd_hist', None)

    bb_pct = kw.get('bb_pct')

    overall, _, _ = compute_overall_score(trend, bb, rsi, macd, stoch, ta, macdh_raw=raw_macd_hist, **kw)
    if overall is None:
        return None

    # HIGH side gate
    if overall > 70:
        rsi_ok = raw_rsi is not None and raw_rsi >= 60
        stoch_ok = raw_stoch is not None and raw_stoch >= 75
        bb_ok = bb_pct is not None and bb_pct >= 0.85
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 70  # cap at threshold-just-below-signal

    # LOW side gate
    if overall < 30:
        rsi_ok = raw_rsi is not None and raw_rsi <= 40
        stoch_ok = raw_stoch is not None and raw_stoch <= 25
        bb_ok = bb_pct is not None and bb_pct <= 0.15
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 30

    return int(max(0, min(100, overall)))


# ─────────────────────────────────────────────────────────────────────────────
# V2 — `conf` shrinkage
# ─────────────────────────────────────────────────────────────────────────────
def variant_v2_conf_shrink(trend, bb, rsi, macd, stoch, ta, **kw):
    """Multiply (overall - 50) by (0.6 + 0.4 * conf_ratio)."""
    raw_rsi = kw.pop('raw_rsi', None)
    raw_stoch = kw.pop('raw_stoch', None)
    raw_macd_hist = kw.pop('raw_macd_hist', None)
    raw_price = kw.pop('raw_price', None)
    raw_ema50 = kw.pop('raw_ema50', None)
    raw_upper = kw.pop('raw_upper', None)
    raw_lower = kw.pop('raw_lower', None)
    raw_middle = kw.pop('raw_middle', None)

    overall, _, _ = compute_overall_score(trend, bb, rsi, macd, stoch, ta, macdh_raw=raw_macd_hist, **kw)
    if overall is None:
        return None

    is_high = overall >= 50
    conf = _conf_ratio(is_high, raw_rsi, raw_stoch, raw_macd_hist,
                       raw_price, raw_ema50, raw_upper, raw_lower, raw_middle)
    shrink = 0.6 + 0.4 * conf  # [0.6, 1.0]
    new_overall = 50 + (overall - 50) * shrink
    return int(max(0, min(100, round(new_overall))))


# ─────────────────────────────────────────────────────────────────────────────
# V6 — Lower max w_trend (28 instead of 35)
# ─────────────────────────────────────────────────────────────────────────────
def variant_v6_low_trend_weight(trend, bb, rsi, macd, stoch, ta, **kw):
    """Re-implement compute_overall_score with w_trend capped at 28 (was 35)."""
    # Drop raw kwargs (not used here)
    for k in ('raw_rsi', 'raw_stoch', 'raw_macd_hist', 'raw_price',
              'raw_ema50', 'raw_upper', 'raw_lower', 'raw_middle'):
        kw.pop(k, None)

    bb_pct = kw.get('bb_pct')
    ws_trend = kw.get('ws_trend')
    ws_rsi = kw.get('ws_rsi')
    ws_macd = kw.get('ws_macd')
    prev_ws_trend = kw.get('prev_ws_trend')
    prev_ws_rsi = kw.get('prev_ws_rsi')
    prev_ws_macd = kw.get('prev_ws_macd')
    vol_mult = kw.get('vol_mult', 1.0)
    blend_w = kw.get('blend_w', 0.0)
    vol_target = kw.get('vol_target', 50.0)

    if None in (trend, bb, rsi, stoch, macd, ta):
        return None

    trend_bias = float(np.tanh((trend - 50) * 0.06))
    trend_strength = abs(trend_bias)
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
    # CHANGED: 18 → 28 instead of 18 → 35 (range 10 instead of 17)
    w_trend = 18 + 10 * d
    w_bb = 18
    # Redistribute the freed weight to RSI/MACD (mean-reversion oscillators)
    w_rsi = 25 - 9 * d   # was 25 - 13*d
    w_macd = 25 - 6 * d  # was 25 - 10*d
    w_stoch = 5
    w_ta = 9 + 6 * d

    weighted_sum = 50 + (
        ((trend - 50) * w_trend) +
        ((bb - 50) * w_bb) +
        ((rsi - 50) * w_rsi) +
        ((stoch - 50) * w_stoch) +
        ((macd - 50) * w_macd) +
        ((ta - 50) * w_ta)
    ) / 100

    if None not in (ws_rsi, ws_macd):
        adj, _ = calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd, prev_ws_trend, prev_ws_rsi, prev_ws_macd)
        weighted_sum += adj

    vol_boost = 1.0 + 0.6 * trend_strength
    if blend_w > 0:
        bw = min(blend_w * vol_boost, 0.60)
        weighted_sum = weighted_sum * (1 - bw) + vol_target * bw
    elif vol_mult != 1.0:
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    return int(max(0, min(100, weighted_sum)))


# ─────────────────────────────────────────────────────────────────────────────
# V_ALL — V1 + V2 + V6 stacked
# ─────────────────────────────────────────────────────────────────────────────
def variant_v_all(trend, bb, rsi, macd, stoch, ta, **kw):
    """Apply V6 weights → V2 conf shrink → V1 raw gate."""
    raw_rsi = kw.get('raw_rsi')
    raw_stoch = kw.get('raw_stoch')
    raw_macd_hist = kw.get('raw_macd_hist')
    raw_price = kw.get('raw_price')
    raw_ema50 = kw.get('raw_ema50')
    raw_upper = kw.get('raw_upper')
    raw_lower = kw.get('raw_lower')
    raw_middle = kw.get('raw_middle')
    bb_pct = kw.get('bb_pct')

    # Step 1: V6 base scoring (low trend weight)
    overall = variant_v6_low_trend_weight(trend, bb, rsi, macd, stoch, ta, **kw)
    if overall is None:
        return None

    # Step 2: V2 conf shrinkage
    is_high = overall >= 50
    conf = _conf_ratio(is_high, raw_rsi, raw_stoch, raw_macd_hist,
                       raw_price, raw_ema50, raw_upper, raw_lower, raw_middle)
    shrink = 0.6 + 0.4 * conf
    overall = 50 + (overall - 50) * shrink

    # Step 3: V1 raw-confirmation gate
    if overall > 70:
        rsi_ok = raw_rsi is not None and raw_rsi >= 60
        stoch_ok = raw_stoch is not None and raw_stoch >= 75
        bb_ok = bb_pct is not None and bb_pct >= 0.85
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 70
    if overall < 30:
        rsi_ok = raw_rsi is not None and raw_rsi <= 40
        stoch_ok = raw_stoch is not None and raw_stoch <= 25
        bb_ok = bb_pct is not None and bb_pct <= 0.15
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 30

    return int(max(0, min(100, round(overall))))


# ─────────────────────────────────────────────────────────────────────────────
# Pairwise combos (binary-search narrowing)
# ─────────────────────────────────────────────────────────────────────────────

def variant_v16_gate_plus_lowtrend(trend, bb, rsi, macd, stoch, ta, **kw):
    """V6 weights + V1 raw gate (drops V2 conf shrink)."""
    raw_rsi = kw.get('raw_rsi')
    raw_stoch = kw.get('raw_stoch')
    bb_pct = kw.get('bb_pct')

    overall = variant_v6_low_trend_weight(trend, bb, rsi, macd, stoch, ta, **kw)
    if overall is None:
        return None

    if overall > 70:
        rsi_ok = raw_rsi is not None and raw_rsi >= 60
        stoch_ok = raw_stoch is not None and raw_stoch >= 75
        bb_ok = bb_pct is not None and bb_pct >= 0.85
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 70
    if overall < 30:
        rsi_ok = raw_rsi is not None and raw_rsi <= 40
        stoch_ok = raw_stoch is not None and raw_stoch <= 25
        bb_ok = bb_pct is not None and bb_pct <= 0.15
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 30
    return int(max(0, min(100, round(overall))))


def variant_v26_shrink_plus_lowtrend(trend, bb, rsi, macd, stoch, ta, **kw):
    """V6 weights + V2 conf shrink (drops V1 raw gate)."""
    raw_rsi = kw.get('raw_rsi')
    raw_stoch = kw.get('raw_stoch')
    raw_macd_hist = kw.get('raw_macd_hist')
    raw_price = kw.get('raw_price')
    raw_ema50 = kw.get('raw_ema50')
    raw_upper = kw.get('raw_upper')
    raw_lower = kw.get('raw_lower')
    raw_middle = kw.get('raw_middle')

    overall = variant_v6_low_trend_weight(trend, bb, rsi, macd, stoch, ta, **kw)
    if overall is None:
        return None
    is_high = overall >= 50
    conf = _conf_ratio(is_high, raw_rsi, raw_stoch, raw_macd_hist,
                       raw_price, raw_ema50, raw_upper, raw_lower, raw_middle)
    shrink = 0.6 + 0.4 * conf
    new_overall = 50 + (overall - 50) * shrink
    return int(max(0, min(100, round(new_overall))))


def variant_v12_gate_plus_shrink(trend, bb, rsi, macd, stoch, ta, **kw):
    """V1 raw gate + V2 conf shrink (drops V6 low_trend; uses default weights)."""
    raw_rsi = kw.get('raw_rsi')
    raw_stoch = kw.get('raw_stoch')
    raw_macd_hist = kw.get('raw_macd_hist')
    raw_price = kw.get('raw_price')
    raw_ema50 = kw.get('raw_ema50')
    raw_upper = kw.get('raw_upper')
    raw_lower = kw.get('raw_lower')
    raw_middle = kw.get('raw_middle')
    bb_pct = kw.get('bb_pct')

    # Strip extras before delegating to default compute_overall_score
    base_kw = {k: v for k, v in kw.items() if not k.startswith('raw_')}
    overall, _, _ = compute_overall_score(trend, bb, rsi, macd, stoch, ta, macdh_raw=raw_macd_hist, **base_kw)
    if overall is None:
        return None

    # V2 shrink
    is_high = overall >= 50
    conf = _conf_ratio(is_high, raw_rsi, raw_stoch, raw_macd_hist,
                       raw_price, raw_ema50, raw_upper, raw_lower, raw_middle)
    shrink = 0.6 + 0.4 * conf
    overall = 50 + (overall - 50) * shrink

    # V1 gate
    if overall > 70:
        rsi_ok = raw_rsi is not None and raw_rsi >= 60
        stoch_ok = raw_stoch is not None and raw_stoch >= 75
        bb_ok = bb_pct is not None and bb_pct >= 0.85
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 70
    if overall < 30:
        rsi_ok = raw_rsi is not None and raw_rsi <= 40
        stoch_ok = raw_stoch is not None and raw_stoch <= 25
        bb_ok = bb_pct is not None and bb_pct <= 0.15
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 30
    return int(max(0, min(100, round(overall))))


# ─────────────────────────────────────────────────────────────────────────────
# V1'_elite — V6 base + raw gate ONLY at elite scores (≥88)
# Leaves the broad 70-87 pool alone (where V1 caused drag) and only filters
# elite signals (where V1's raw confirmation meaningfully fixed the 38.9% WR
# problem in the 90+ bucket).
# ─────────────────────────────────────────────────────────────────────────────
def variant_v6_plus_v1_elite(trend, bb, rsi, macd, stoch, ta, **kw):
    """V6 weights + V1 raw gate applied ONLY when overall ≥ 88 (or ≤ 12)."""
    raw_rsi = kw.get('raw_rsi')
    raw_stoch = kw.get('raw_stoch')
    bb_pct = kw.get('bb_pct')

    overall = variant_v6_low_trend_weight(trend, bb, rsi, macd, stoch, ta, **kw)
    if overall is None:
        return None

    # Elite-only HIGH gate
    if overall >= 88:
        rsi_ok = raw_rsi is not None and raw_rsi >= 60
        stoch_ok = raw_stoch is not None and raw_stoch >= 75
        bb_ok = bb_pct is not None and bb_pct >= 0.85
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 87  # demote to non-elite, keep in 70+ pool

    # Elite-only LOW gate (mirror)
    if overall <= 12:
        rsi_ok = raw_rsi is not None and raw_rsi <= 40
        stoch_ok = raw_stoch is not None and raw_stoch <= 25
        bb_ok = bb_pct is not None and bb_pct <= 0.15
        if not (rsi_ok or stoch_ok or bb_ok):
            overall = 13

    return int(max(0, min(100, round(overall))))


# ─────────────────────────────────────────────────────────────────────────────
# V_GRAD — Gradient sign-correction (Option B) + gradient trend weight
#
# Fixes the same root cause as V_SIGN_FLIP (oscillator components have inverted
# sign) but uses a smooth gradient instead of a hard 100-x reflection.
#
# 1. Oscillator contributions: sign comes from RAW indicator (smooth tanh),
#    magnitude comes from COMPONENT score. The component knows the *strength*
#    of the setup (breakouts, divergences, floor/ceiling); the raw knows the
#    *direction*. At neutral raw readings the contribution naturally → 0,
#    which is C's confirmation-gate behavior baked into B's formula.
#
# 2. Trend weight: V6 hard-capped w_trend at 28. V_GRAD instead floats trend
#    weight between ~half and ~full based on whether raw oscillators confirm
#    trend direction. ASTS 2025-11-20 (oversold raws + weakening trend) →
#    alignment ≈ 0 → trend gets half weight → can't override the oscillator
#    headwind. AMAT continuation (uptrend + already-extended raws) → alignment
#    ≈ 1 → trend gets full weight.
#
# 3. The V6 osc_divergence + bb_pct dynamic guards in compute_overall_score
#    were tuned to the OLD (inverted) convention. Under B the oscillator
#    terms are sign-correct, so we drop the guards entirely in V_GRAD.
# ─────────────────────────────────────────────────────────────────────────────
def variant_v_grad(trend, bb, rsi, macd, stoch, ta, **kw):
    """Gradient sign-correction + gradient trend weight."""
    raw_rsi   = kw.pop('raw_rsi', None)
    raw_stoch = kw.pop('raw_stoch', None)
    raw_macd_hist = kw.pop('raw_macd_hist', None)
    raw_price = kw.pop('raw_price', None)
    raw_ema50 = kw.pop('raw_ema50', None)
    raw_upper = kw.pop('raw_upper', None)
    raw_lower = kw.pop('raw_lower', None)
    raw_middle = kw.pop('raw_middle', None)

    bb_pct = kw.get('bb_pct')
    ws_trend = kw.get('ws_trend')
    ws_rsi = kw.get('ws_rsi')
    ws_macd = kw.get('ws_macd')
    prev_ws_trend = kw.get('prev_ws_trend')
    prev_ws_rsi = kw.get('prev_ws_rsi')
    prev_ws_macd = kw.get('prev_ws_macd')
    vol_mult = kw.get('vol_mult', 1.0)
    blend_w = kw.get('blend_w', 0.0)
    vol_target = kw.get('vol_target', 50.0)

    if None in (trend, bb, rsi, stoch, macd, ta):
        return None
    if raw_rsi is None or raw_stoch is None or bb_pct is None:
        # Fall back to V6 if raw data missing (shouldn't happen in practice)
        return variant_v6_low_trend_weight(trend, bb, rsi, macd, stoch, ta,
                                           bb_pct=bb_pct, **{k: v for k, v in kw.items()
                                                              if not k.startswith('raw_')})

    # ── 1. Trend dominance (same shape as compute_overall_score) ─────
    trend_bias = float(np.tanh((trend - 50) * 0.06))
    trend_strength = abs(trend_bias)
    trend_dominance = trend_strength ** 0.7
    d = trend_dominance

    # ── 2. Trend-weight gradient: scale by raw-oscillator alignment ──
    # σ controls steepness — 15 means tanh saturates by RSI ±25 from neutral
    SIGMA_RSI = 15.0
    SIGMA_STOCH = 15.0
    SIGMA_BB = 0.15  # bb_pct is 0-1, so 0.15 ≈ same fraction-of-range as RSI 15

    raw_rsi_dir   = float(np.tanh((raw_rsi   - 50.0) / SIGMA_RSI))
    raw_stoch_dir = float(np.tanh((raw_stoch - 50.0) / SIGMA_STOCH))
    raw_bb_dir    = float(np.tanh((bb_pct    - 0.5)  / SIGMA_BB))
    osc_dir = (raw_rsi_dir + raw_stoch_dir + raw_bb_dir) / 3.0

    trend_dir = float(np.tanh((trend - 50.0) / 15.0))
    alignment = max(0.0, trend_dir * osc_dir)  # 0 if opposed, 1 if aligned

    # Pre-V6 was 18 + 17*d. V_GRAD floats it: half-weight when opposed, full when aligned.
    w_trend = 18.0 + 17.0 * d * (0.5 + 0.5 * alignment)
    w_bb    = 18.0
    w_rsi   = 25.0 -  9.0 * d
    w_macd  = 25.0 -  6.0 * d
    w_stoch =  5.0
    w_ta    =  9.0 +  6.0 * d

    # ── 3. Oscillator terms: magnitude from component, direction from raw ──
    rsi_mag   = abs(rsi   - 50)
    stoch_mag = abs(stoch - 50)
    bb_mag    = abs(bb    - 50)

    # Trend-strength gate: oscillator extensions only count to the extent
    # there's a real trend to revert from. Encodes V6's accidental quality
    # filter ("strong trend dominance + extension") deliberately.
    # Floor 0.25 keeps a baseline so sideways-extreme setups still register
    # at reduced strength but rarely cross HIGH/LOW thresholds alone.
    osc_gate = 0.25 + 0.75 * trend_strength

    rsi_term   = rsi_mag   * raw_rsi_dir   * osc_gate
    stoch_term = stoch_mag * raw_stoch_dir * osc_gate
    bb_term    = bb_mag    * raw_bb_dir    * osc_gate

    # Trend, MACD, TA keep their original (already sign-correct) contribution
    weighted_sum = 50.0 + (
        ((trend - 50) * w_trend) +
        (bb_term       * w_bb) +
        (rsi_term      * w_rsi) +
        ((stoch_term)  * w_stoch) +
        ((macd - 50)   * w_macd) +
        ((ta - 50)     * w_ta)
    ) / 100.0

    # ── 4. Weekly adjustment ─────────────────────────────────────────
    if None not in (ws_rsi, ws_macd):
        adj, _ = calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd, prev_ws_trend, prev_ws_rsi, prev_ws_macd)
        weighted_sum += adj

    # ── 5. Volume amplification ──────────────────────────────────────
    vol_boost = 1.0 + 0.6 * trend_strength
    if blend_w > 0:
        bw = min(blend_w * vol_boost, 0.60)
        weighted_sum = weighted_sum * (1 - bw) + vol_target * bw
    elif vol_mult != 1.0:
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    return int(max(0, min(100, round(weighted_sum))))


# ─────────────────────────────────────────────────────────────────────────────
# V_GRAD2 — Oscillators as VALIDATORS (Fork 1B)
#
# V_GRAD added oscillators as primary contributors to the weighted sum, with a
# soft trend-strength gate. That over-fired (3.5x V3 volume at 70+) because in
# the corrected sign convention, oscillator extension on its own can push
# overall to HIGH without trend confirmation. The bug was accidentally
# encoding "trend dominance is required for HIGH" via the inverted sign drag;
# V_GRAD2 encodes that requirement deliberately.
#
# Structural change vs V_GRAD:
#   - Weighted sum uses ONLY trend, MACD, TA (rescaled to sum to 100). These
#     are the "trend-quality" signals.
#   - Oscillator components (RSI, Stoch, BB) are NOT in the weighted sum.
#     Instead they contribute via a directional confluence bonus/penalty:
#     when raw oscillators agree with trend direction, +N pts (confirmation);
#     when they oppose, −M pts (veto). Asymmetric: penalty > bonus, because
#     the bug case (trend up + raw oversold = ASTS) needs strong suppression.
#
# Result expected: signals only fire when trend (+macd) is doing the heavy
# lifting AND oscillators don't actively veto. Volume should land near V3.
# ─────────────────────────────────────────────────────────────────────────────
def variant_v_grad2(trend, bb, rsi, macd, stoch, ta, **kw):
    """Oscillators as validators (bonus/penalty), not primary contributors."""
    raw_rsi   = kw.pop('raw_rsi', None)
    raw_stoch = kw.pop('raw_stoch', None)
    kw.pop('raw_macd_hist', None)
    kw.pop('raw_price', None)
    kw.pop('raw_ema50', None)
    kw.pop('raw_upper', None)
    kw.pop('raw_lower', None)
    kw.pop('raw_middle', None)

    bb_pct = kw.get('bb_pct')
    ws_trend = kw.get('ws_trend')
    ws_rsi = kw.get('ws_rsi')
    ws_macd = kw.get('ws_macd')
    prev_ws_trend = kw.get('prev_ws_trend')
    prev_ws_rsi = kw.get('prev_ws_rsi')
    prev_ws_macd = kw.get('prev_ws_macd')
    vol_mult = kw.get('vol_mult', 1.0)
    blend_w = kw.get('blend_w', 0.0)
    vol_target = kw.get('vol_target', 50.0)

    if None in (trend, macd, ta):
        return None

    # ── 1. Trend dominance ──────────────────────────────────────────
    trend_bias = float(np.tanh((trend - 50) * 0.06))
    trend_strength = abs(trend_bias)
    d = trend_strength ** 0.7

    # ── 2. Weights — trend/macd/ta only, rescaled to sum to 100 ────
    # Rescaled because oscillators no longer contribute to the weighted sum;
    # the remaining 3 components need to span the full -50..+50 range so
    # confirmation gating can produce HIGH/LOW signals when aligned.
    w_trend_raw = 18.0 + 17.0 * d   # 18..35
    w_macd_raw  = 25.0 -  6.0 * d   # 19..25
    w_ta_raw    =  9.0 +  6.0 * d   #  9..15
    total = w_trend_raw + w_macd_raw + w_ta_raw   # ~52..75
    w_trend = w_trend_raw * 100.0 / total
    w_macd  = w_macd_raw  * 100.0 / total
    w_ta    = w_ta_raw    * 100.0 / total

    non_osc_pts = (
        ((trend - 50) * w_trend) +
        ((macd  - 50) * w_macd)  +
        ((ta    - 50) * w_ta)
    ) / 100.0

    # ── 3. Multiplicative oscillator confirmation gate ──────────────
    # The non-oscillator contribution is multiplied by a confirmation
    # factor based on how well raw oscillators agree with trend direction.
    # - Fully aligned: full credit (1.0)
    # - Neutral oscillators: partial credit (0.4) — trend can push, but slowly
    # - Fully opposed: zero credit (0) — oscillators veto trend completely
    # This is the structural enforcement of "trend dominance + oscillator
    # confirmation required for HIGH/LOW".
    SIGMA_RSI = 15.0
    SIGMA_STOCH = 15.0
    SIGMA_BB = 0.15

    osc_dirs = []
    if raw_rsi is not None:
        osc_dirs.append(float(np.tanh((raw_rsi - 50.0) / SIGMA_RSI)))
    if raw_stoch is not None:
        osc_dirs.append(float(np.tanh((raw_stoch - 50.0) / SIGMA_STOCH)))
    if bb_pct is not None:
        osc_dirs.append(float(np.tanh((bb_pct - 0.5) / SIGMA_BB)))

    if osc_dirs:
        osc_dir_avg = sum(osc_dirs) / len(osc_dirs)
        trend_dir = float(np.tanh((trend - 50.0) / 15.0))
        agreement = osc_dir_avg * trend_dir   # -1 fully opposed, +1 fully aligned
        # Hard confirmation: no floor. Both trend AND oscillators must
        # actively confirm in the same direction. Sideways trend OR neutral
        # oscillators OR opposed → contribution clamped to 0 → no signal.
        confirmation = max(0.0, agreement)
    else:
        confirmation = 0.0

    weighted_sum = 50.0 + non_osc_pts * confirmation

    # ── 4. Weekly adjustment ────────────────────────────────────────
    if None not in (ws_rsi, ws_macd):
        adj, _ = calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd, prev_ws_trend, prev_ws_rsi, prev_ws_macd)
        weighted_sum += adj

    # ── 5. Volume amplification ─────────────────────────────────────
    vol_boost = 1.0 + 0.6 * trend_strength
    if blend_w > 0:
        bw = min(blend_w * vol_boost, 0.60)
        weighted_sum = weighted_sum * (1 - bw) + vol_target * bw
    elif vol_mult != 1.0:
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    return int(max(0, min(100, round(weighted_sum))))


# ─────────────────────────────────────────────────────────────────────────────
# V_SIGN_FLIP — Flip RSI / Stoch / BB component sign so they match the
# "high overall = expect drop / mean-reversion short" convention used by
# trend, MACD, and assess_scores._signal_context.
#
# Bug fingerprint (see CLAUDE.md "Open investigation"):
#   ASTS 2025-11-20: raw RSI=31.6, Stoch=9.2, BB=below_lower, EMA50=-21.8%
#   → components RSI=73, Stoch=99, BB=72 (HIGH because oversold raw)
#   → overall 70 H ("expect drop")
#   → stock then rallied +92.3% in 30d.
#
# The component scorers use the convention "good buy zone → high score",
# while compute_overall_score adds them as if "high component → expect drop".
# Reflecting RSI/Stoch/BB around 50 is algebraically equivalent to flipping
# the sign in the component formulas (the breakout pushes, divergence pushes,
# and trend floor/ceiling all mirror cleanly under x → 100 - x).
# Trend and MACD already match the doc convention so are left untouched.
# Built on V6 weights per CLAUDE.md recommendation.
# ─────────────────────────────────────────────────────────────────────────────
def variant_v_sign_flip(trend, bb, rsi, macd, stoch, ta, **kw):
    """V6 base + flip RSI/Stoch/BB components (100 - x)."""
    if None in (rsi, stoch, bb):
        return None
    return variant_v6_low_trend_weight(
        trend, 100 - bb, 100 - rsi, macd, 100 - stoch, ta, **kw
    )


# ─────────────────────────────────────────────────────────────────────────────
# Ablation variants — neutralize one component to 50 (zero contribution).
#
# If removing a component IMPROVES WR/EV vs DB → that component was dragging
# accuracy down.  If it HURTS → the component is contributing positively.
#
# Notes:
#   • The removed component's weight (e.g. RSI ≈ 16-25%) is still divided by
#     the same /100 denominator, so extreme scores become slightly harder to
#     reach.  This is expected — we're removing information, not redistributing.
#   • Run via:  python -m experiments.run_experiments --variants ABLATE_RSI ...
# ─────────────────────────────────────────────────────────────────────────────

def _ablate(trend, bb, rsi, macd, stoch, ta, **kw):
    """Shared kwarg-stripping helper used by all ablation variants."""
    raw_macd_hist = kw.pop('raw_macd_hist', None)
    for k in ('raw_rsi', 'raw_stoch', 'raw_price', 'raw_ema50',
              'raw_upper', 'raw_lower', 'raw_middle'):
        kw.pop(k, None)
    return raw_macd_hist, kw


def ablation_no_rsi(trend, bb, rsi, macd, stoch, ta, **kw):
    """RSI set to 50 (neutral). Tests whether RSI hurts or helps signal accuracy."""
    raw_macd_hist, kw = _ablate(trend, bb, rsi, macd, stoch, ta, **kw)
    overall, _, _ = compute_overall_score(
        trend, bb, 50, macd, stoch, ta, macdh_raw=raw_macd_hist, **kw)
    return overall


def ablation_no_stoch(trend, bb, rsi, macd, stoch, ta, **kw):
    """Stoch set to 50 (neutral). Tests whether Stoch hurts or helps signal accuracy."""
    raw_macd_hist, kw = _ablate(trend, bb, rsi, macd, stoch, ta, **kw)
    overall, _, _ = compute_overall_score(
        trend, bb, rsi, macd, 50, ta, macdh_raw=raw_macd_hist, **kw)
    return overall


def ablation_no_ta(trend, bb, rsi, macd, stoch, ta, **kw):
    """Technical Alignment set to 50 (neutral). Tests whether TA (X) hurts or helps."""
    raw_macd_hist, kw = _ablate(trend, bb, rsi, macd, stoch, ta, **kw)
    overall, _, _ = compute_overall_score(
        trend, bb, rsi, macd, stoch, 50, macdh_raw=raw_macd_hist, **kw)
    return overall


# ─────────────────────────────────────────────────────────────────────────────
# RSI_ASYM — Asymmetric RSI weight by side
#
# Ablation showed RSI helps at ≥85 (call side) but drags the LOW (put) bucket:
# oversold stocks score RSI HIGH (bounce zone), which partially cancels the
# bearish signal from trend/BB/MACD. Fix: halve RSI weight when the base signal
# (before RSI) is already bearish.
#
# Implemented directly inside the scoring formula to avoid re-implementing the
# full weight logic — we compute a quick pre-RSI estimate, then adjust w_rsi.
# ─────────────────────────────────────────────────────────────────────────────
def variant_rsi_asym(trend, bb, rsi, macd, stoch, ta, **kw):
    """Halve RSI weight when pre-RSI base signal is bearish (< 45)."""
    raw_macd_hist = kw.pop('raw_macd_hist', None)
    for k in ('raw_rsi', 'raw_stoch', 'raw_price', 'raw_ema50',
              'raw_upper', 'raw_lower', 'raw_middle'):
        kw.pop(k, None)

    if None in (trend, bb, rsi, macd, stoch, ta):
        return None

    bb_pct = kw.get('bb_pct')

    # Replicate the trend-dominance calculation from compute_overall_score
    trend_bias = float(np.tanh((trend - 50) * 0.06))
    trend_strength = abs(trend_bias)
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

    if raw_macd_hist is not None and trend_strength > 0.15:
        macdh_dir = float(np.tanh(raw_macd_hist / 0.05))
        alignment = macdh_dir * float(np.sign(trend_bias))
        trend_dominance *= (0.60 + 0.40 * (0.5 + 0.5 * alignment))

    d = trend_dominance
    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 -  9 * d
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    # Quick pre-RSI estimate using trend, bb, macd, stoch, ta
    pre_rsi_sum = 50 + (
        (trend  - 50) * w_trend +
        (bb     - 50) * w_bb +
        (macd   - 50) * w_macd +
        (stoch  - 50) * w_stoch +
        (ta     - 50) * w_ta
    ) / 100

    # Halve RSI weight when base signal is bearish — oversold RSI contradicts puts
    if pre_rsi_sum < 45:
        w_rsi *= 0.5

    # Now delegate to compute_overall_score with the adjusted rsi component.
    # We can't pass w_rsi directly, so we adjust the RSI input instead:
    # target contribution = (rsi - 50) * w_rsi_new = (rsi_adj - 50) * w_rsi_orig
    # → rsi_adj = 50 + (rsi - 50) * (w_rsi_new / w_rsi_orig) = 50 + (rsi - 50) * 0.5
    rsi_adj = 50 + (rsi - 50) * 0.5
    overall, _, _ = compute_overall_score(
        trend, bb, rsi_adj, macd, stoch, ta, macdh_raw=raw_macd_hist, **kw)
    return overall


def _bear_base(trend, bb, macd, stoch, ta, bb_pct, raw_macd_hist, kw):
    """Compute pre-RSI score estimate; returns (pre_rsi_sum, trend_dominance)."""
    trend_bias = float(np.tanh((trend - 50) * 0.06))
    trend_strength = abs(trend_bias)
    trend_dominance = trend_strength ** 0.7

    if bb_pct is not None:
        bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
        bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
        trend_dominance *= (1.0 - 0.5 * (bull_ext + bear_ext))

    osc_avg = (macd + 50) / 2.0  # approximate without rsi
    bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
    bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
    osc_divergence = bull_div + bear_div
    if bb_pct is not None:
        osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
    trend_dominance *= (1.0 - 0.7 * osc_divergence)

    if raw_macd_hist is not None and trend_strength > 0.15:
        macdh_dir = float(np.tanh(raw_macd_hist / 0.05))
        alignment = macdh_dir * float(np.sign(trend_bias))
        trend_dominance *= (0.60 + 0.40 * (0.5 + 0.5 * alignment))

    d = trend_dominance
    w_trend = 18 + 10 * d
    w_bb    = 18
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    pre = 50 + (
        (trend - 50) * w_trend +
        (bb    - 50) * w_bb +
        (macd  - 50) * w_macd +
        (stoch - 50) * w_stoch +
        (ta    - 50) * w_ta
    ) / 100
    return pre, trend_dominance


# ─────────────────────────────────────────────────────────────────────────────
# RSI_ZERO_BEAR — Zero RSI contribution when base signal is bearish
#
# More aggressive than RSI_ASYM (0.5x): completely neutralize RSI on the put
# path. When trend/BB/MACD/Stoch/TA are collectively bearish, oversold RSI
# is noise — zero it out rather than just halving it.
# ─────────────────────────────────────────────────────────────────────────────
def variant_rsi_zero_bear(trend, bb, rsi, macd, stoch, ta, **kw):
    """Zero RSI contribution (rsi=50) when pre-RSI base is bearish (< 45)."""
    raw_macd_hist = kw.pop('raw_macd_hist', None)
    for k in ('raw_rsi', 'raw_stoch', 'raw_price', 'raw_ema50',
              'raw_upper', 'raw_lower', 'raw_middle'):
        kw.pop(k, None)
    if None in (trend, bb, rsi, macd, stoch, ta):
        return None
    bb_pct = kw.get('bb_pct')
    pre, _ = _bear_base(trend, bb, macd, stoch, ta, bb_pct, raw_macd_hist, kw)
    rsi_in = 50 if pre < 45 else rsi
    overall, _, _ = compute_overall_score(
        trend, bb, rsi_in, macd, stoch, ta, macdh_raw=raw_macd_hist, **kw)
    return overall


# ─────────────────────────────────────────────────────────────────────────────
# RSI_FLIP_BEAR — Flip RSI direction when base signal is bearish
#
# Instead of reducing RSI weight, invert its contribution for put setups.
# Oversold RSI (raw RSI < 30 → component score HIGH) becomes a CONFIRMING
# signal for puts: the stock is deeply oversold AND the broader indicators
# are bearish → more likely to continue lower, not bounce.
# Component RSI is reflected: rsi_adj = 100 - rsi
# ─────────────────────────────────────────────────────────────────────────────
def variant_rsi_zero_bear_40(trend, bb, rsi, macd, stoch, ta, **kw):
    """Zero RSI when pre-RSI base is deeply bearish (< 40). Tighter than _BEAR_45."""
    raw_macd_hist = kw.pop('raw_macd_hist', None)
    for k in ('raw_rsi', 'raw_stoch', 'raw_price', 'raw_ema50',
              'raw_upper', 'raw_lower', 'raw_middle'):
        kw.pop(k, None)
    if None in (trend, bb, rsi, macd, stoch, ta):
        return None
    bb_pct = kw.get('bb_pct')
    pre, _ = _bear_base(trend, bb, macd, stoch, ta, bb_pct, raw_macd_hist, kw)
    rsi_in = 50 if pre < 40 else rsi
    overall, _, _ = compute_overall_score(
        trend, bb, rsi_in, macd, stoch, ta, macdh_raw=raw_macd_hist, **kw)
    return overall


def variant_rsi_flip_bear(trend, bb, rsi, macd, stoch, ta, **kw):
    """Flip RSI to (100-rsi) when pre-RSI base signal is bearish (< 45)."""
    raw_macd_hist = kw.pop('raw_macd_hist', None)
    for k in ('raw_rsi', 'raw_stoch', 'raw_price', 'raw_ema50',
              'raw_upper', 'raw_lower', 'raw_middle'):
        kw.pop(k, None)
    if None in (trend, bb, rsi, macd, stoch, ta):
        return None
    bb_pct = kw.get('bb_pct')
    pre, _ = _bear_base(trend, bb, macd, stoch, ta, bb_pct, raw_macd_hist, kw)
    rsi_in = (100 - rsi) if pre < 45 else rsi
    overall, _, _ = compute_overall_score(
        trend, bb, rsi_in, macd, stoch, ta, macdh_raw=raw_macd_hist, **kw)
    return overall


# Variant registry
VARIANTS = {
    'V1_raw_gate':       variant_v1_raw_gate,
    'V2_conf_shrink':    variant_v2_conf_shrink,
    'V6_low_trend':      variant_v6_low_trend_weight,
    'V_ALL':             variant_v_all,
    'V16_gate_lowtrend': variant_v16_gate_plus_lowtrend,
    'V26_shrink_lowtrend': variant_v26_shrink_plus_lowtrend,
    'V12_gate_shrink':   variant_v12_gate_plus_shrink,
    'V6_v1elite':        variant_v6_plus_v1_elite,
    'V_SIGN_FLIP':       variant_v_sign_flip,
    'V_GRAD':            variant_v_grad,
    'V_GRAD2':           variant_v_grad2,
    # Ablation experiments
    'ABLATE_RSI':        ablation_no_rsi,
    'ABLATE_STOCH':      ablation_no_stoch,
    'ABLATE_TA':         ablation_no_ta,
    # Targeted fix: asymmetric RSI weight by side
    'RSI_ASYM':          variant_rsi_asym,
    'RSI_ZERO_BEAR':     variant_rsi_zero_bear,
    'RSI_FLIP_BEAR':     variant_rsi_flip_bear,
    'RSI_ZERO_BEAR_40':  variant_rsi_zero_bear_40,
}
