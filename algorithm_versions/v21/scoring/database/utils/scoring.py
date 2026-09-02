import math
import numpy as np
from colorama import init, Fore

init(autoreset=True, convert=True)

MOVING_AVERAGE_PERIOD = 20
MOMENTUM_LOOKBACK_DAYS = 3


def compute_overall_score(
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
):
    """
    Pure scoring function — no DB calls.

    Returns (overall: int, weight_info: dict, vol_update: dict | None)

    regime_multiplier: float from MarketRegime table (0.70–1.10). Applied
    symmetrically around 50 as the final step before clamping. None or 1.0
    means no adjustment (e.g. dates before regime was backfilled).

    weight_info includes 'pre_regime' (the overall before regime application)
    so callers can re-apply a different multiplier without recomputing the
    full pipeline.

    vol_update is non-None only when the caller should persist a volume signal
    (i.e. when vol_sig != 'NEUTRAL'); it contains:
        {'volume': vol_raw, 'volume_signal': vol_sig, 'volume_magnitude': vol_mag}
    """
    if None in (trend, bb, rsi, stoch, macd, technical_alignment):
        return None, {}, None

    # ── Dynamic weighting ────────────────────────────────────────────
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

    d      = trend_dominance
    # V6 weights: cap trend at 28 (was 35), give the 7pts back to RSI/MACD.
    # Rationale: at trend=99 with raw RSI/Stoch neutral, the old w_trend=35
    # alone added ~17pts to overall, conflating "strong uptrend" with
    # "overbought" and producing HIGH (short) signals on continuation setups.
    # Validated on full universe (515 stocks, 365d): 70+ Cap30 0.311 → 0.349,
    # 90+ WR 38.9% → 62.5%.
    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 -  9 * d
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    # ── Asymmetric MACD: zero MACD on bearish setups ─────────────────────────
    # MACD is a lagging momentum indicator: on bearish setups it stays positive
    # until breakdown is confirmed, suppressing put scores. When the pre-MACD
    # score is < 45, zero MACD's weight and redistribute proportionally to the
    # remaining components. Validated 2026-04-17 (5y full universe):
    # put <25 WR15 +4.3pp, <15 WR15 +6.5pp; call side entirely unchanged.
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

    # ── Asymmetric RSI: zero RSI when base signal is deeply bearish ──────────
    # When trend+BB+MACD+Stoch+TA collectively score < 40, oversold RSI is
    # contradicting the put thesis (component scores oversold as HIGH = bounce
    # zone, fighting the genuine downtrend). Neutralize it.
    # Validated: put <15 WR +6.2pp, put <5 WR +26pp; call 75+ WR +2.5pp.
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

    # ── Weekly adjustment ────────────────────────────────────────────
    weekly_detail = None
    if None not in (ws_rsi, ws_macd):
        adj, weekly_detail = calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd,
            prev_ws_trend, prev_ws_rsi, prev_ws_macd,
        )
        weighted_sum += adj

    # ── Volume amplification ─────────────────────────────────────────
    vol_boost = 1.0 + 0.6 * trend_strength

    # Fix 1 — EMA extension dampening: reduce vol_boost when price is extended
    # from EMA50. A stock at +23% EMA50 is not a fresh entry; amplifying CONVICTION
    # there risks inflating the score past what the setup warrants.
    # Activates only when pct_from_ema50 is provided by the caller.
    if pct_from_ema50 is not None:
        ext = abs(pct_from_ema50)
        if ext > 15.0:
            ema_damp = 1.0 - 0.5 * min(1.0, (ext - 15.0) / 25.0)
            vol_boost *= ema_damp

    if blend_w > 0:
        blend_w = min(blend_w * vol_boost, 0.60)
        # Fix 3 — REVERSAL blend dampening: when ABSORPTION/REJECTION blends the
        # score toward an extreme vol_target but MACD contradicts that direction and
        # the pre-blend score is not already in extreme territory, halve the blend
        # weight. Prevents maximum ABSORPTION from overriding a moderate pre-vol
        # score when momentum (MACD) disagrees with the reversal thesis.
        if abs(weighted_sum - 50) < 25:
            if (vol_target < 35 and macd > 60) or (vol_target > 65 and macd < 40):
                blend_w *= 0.50
        weighted_sum = weighted_sum * (1 - blend_w) + vol_target * blend_w
    elif vol_mult != 1.0:
        # Fix 2 — CONVICTION-MACD contradiction gate: when CONVICTION amplifies
        # a score but MACD directionally contradicts that score, cap the
        # amplification to 25% of its normal magnitude.
        # Prevents a large up-day (CONVICTION bullish) from making an already-
        # bearish score even more bearish when MACD is also bullish.
        if vol_sig == 'CONVICTION':
            if (weighted_sum < 35 and macd > 60) or (weighted_sum > 65 and macd < 40):
                vol_mult = 1.0 + (vol_mult - 1.0) * 0.25
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    pre_regime_overall = int(max(0, min(100, weighted_sum)))

    # ── Regime adjustment ───────────────────────────────────────────
    # Applied symmetrically around 50: scores ≥50 compressed/expanded
    # toward 50 in stressed/healthy regimes; scores <50 likewise via
    # the (2.0 - multiplier) mirror.  None or 1.0 = no adjustment.
    #
    # JA4 (2026-04-19): put-side uses a SPY_wk-blended multiplier
    # (75% current composite + 25% SPY_wk) to suppress false put signals
    # on market recovery days before VIX/breadth fully normalize.
    # Calls (pre_regime ≥ 50) always use the standard regime_multiplier.
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

    # Capitulation gradient dampener: score=0 + pct_from_ema50 < -10% = active
    # capitulation, not a fresh put setup. Gradient lift ext=10%→5, 15%→10,
    # 20%+→20. Validated 5y: <5 WR15 65.8%→71.2%, <10 68.1%→72.6%, <15 67.8%→71.0%.
    _cap_dampened = False
    if overall == 0 and pct_from_ema50 is not None and pct_from_ema50 < -10.0:
        ext = abs(pct_from_ema50)
        overall = min(20, int(round(5.0 + (ext - 10.0))))
        _cap_dampened = True

    # Exhaustion gradient dampener: at deeply bearish scores (<=9), MACDh turning
    # positive indicates bounce setup, not structural weakness. Lift toward 10
    # proportional to (a) how bearish the score is and (b) how positive macdh is.
    # Evidence from experiments/low_extreme_pattern.py (v20, 5y): at 0-4, rows
    # with macdh>=0 carry WR30 66.9% vs 73.2% baseline (and 73.8% at 5-9 bucket).
    # Redirects bounce-prone extreme puts into the 5-9 sweet spot.
    _exh_damp = 0.0
    if overall <= 9 and macdh_raw is not None:
        score_weight = (9 - overall) / 9.0              # 1.0 at 0, 0 at 9
        mh_pos = max(0.0, float(np.tanh(macdh_raw / 0.05)))  # 0 for negative mh
        _exh_damp = score_weight * mh_pos * 0.5         # cap pull at 50%
        if _exh_damp > 0.0:
            overall = int(round(overall * (1 - _exh_damp) + 10.0 * _exh_damp))

    # Ext-focal gradient dampener: puts (<=25) with price ABOVE EMA50 are
    # profit-taking pullbacks in uptrends, not breakdown setups. 5y fine-grained
    # sweep (experiments/ext_focal_sweep.py, v20): WR30 declines monotonically
    # from 70.3% at ext=-10..-4 to 60.7% at ext>+10 for s<=25. Lift proportional
    # to how low the score is AND how far above EMA50 the price is.
    _ext_damp = 0.0
    if overall <= 25 and pct_from_ema50 is not None and pct_from_ema50 > 0.0:
        ext_ramp = min(1.0, pct_from_ema50 / 10.0)       # 0 at ext=0, 1 at ext>=10
        score_weight = (25 - overall) / 25.0             # 1.0 at 0, 0 at 25
        _ext_damp = 0.5 * ext_ramp * score_weight        # k=0.5
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

def calculate_weekly_composite(rsi, macd, trend=None):
    if None in (rsi, macd):
        return None
    if trend is None:
        composite = rsi * 0.55 + macd * 0.45
    else:
        osc_avg = (rsi + macd) / 2.0
        gap = abs(trend - osc_avg)
        dampening = 1.0 - 0.5 * min(1.0, gap / 50.0)
        w_trend = 0.35 * dampening
        extra = 0.35 * (1.0 - dampening)
        w_rsi  = 0.35 + extra * 0.5
        w_macd = 0.30 + extra * 0.5
        composite = trend * w_trend + rsi * w_rsi + macd * w_macd
    return int(round(max(0, min(100, composite))))

def calculate_weekly_adjustment(trend, rsi, macd, prev_trend=None, prev_rsi=None, prev_macd=None):
    """
    Additive adjustment from weekly context applied to daily overall score.
    Returns (total_adjustment, detail_dict) for storage in weight_info.
    """
    if None in (trend, rsi, macd):
        return 0.0, None

    composite = calculate_weekly_composite(rsi, macd, trend) or 50

    # 1. BASE BIAS: how far weekly is from neutral, max ±15 pts
    deviation = (composite - 50) / 50
    base_bias = 15 * float(np.tanh(deviation * 1.5))

    # 2. AGREEMENT AMPLIFIER: scales with directional alignment and strength
    signals = [(s - 50) / 50 for s in [trend, rsi, macd]]
    avg_signal = sum(signals) / len(signals)
    if abs(avg_signal) > 0.01:
        consistency = sum(max(0, s * np.sign(avg_signal)) for s in signals) / len(signals)
    else:
        consistency = 0
    agreement = 0.8 + 0.6 * consistency
    base_bias *= agreement

    # 3. MOMENTUM: week-over-week delta detects regime shifts early
    momentum_bias = 0.0
    if None not in (prev_trend, prev_rsi, prev_macd):
        prev_composite = calculate_weekly_composite(prev_rsi, prev_macd, prev_trend) or 50
        delta = composite - prev_composite
        momentum_bias = 8 * float(np.tanh(delta / 15))

    total = base_bias + momentum_bias

    # Asymmetric scaling: amplify put-direction signals (total < 0) by 1.5x.
    # Validated 2026-04-17 — lifts <25 WR30 +1.6pp and <15 WR30 +5.2pp
    # with call side unchanged. See CLAUDE.md §"Asymmetric weekly".
    PUT_WEEKLY_SCALE = 1.5
    if total < 0:
        total *= PUT_WEEKLY_SCALE

    detail = {
        'w_comp': round(composite, 1),
        'w_bias': round(base_bias, 1),
        'w_mom': round(momentum_bias, 1),
        'w_adj': round(total, 1)
    }
    return total, detail



def normalize_score(value, min_good, max_good, reverse=False):
    if value is None:
        return None

    if reverse:
        if value <= min_good:
            return 100
        elif value >= max_good:
            return 0
        else:
            score = 100 - ((value - min_good) / (max_good - min_good) * 100)
    else:
        if value >= max_good:
            return 100
        elif value <= min_good:
            return 0
        else:
            score = (value - min_good) / (max_good - min_good) * 100

    return int(max(0, min(100, score)))

def calculate_theta_factor(days_remaining: int, original_dte: int) -> float:
    if days_remaining <= 0:
        return 0
        
    time_ratio = days_remaining / original_dte
    power = 1.5 + (1.0 * (30 / (30 + original_dte)))
    theta_factor = time_ratio ** power
    theta_factor = 0.5 + (theta_factor * 0.5)
    
    return theta_factor

def calculate_option_return(initial_cost,current_price, strike_price, dte, volatility, original_dte):
    intrinsic_value = current_price - strike_price
    time_value = (
        current_price * volatility * 0.25 * 
        math.sqrt(dte / 30) * 
        calculate_theta_factor(dte, original_dte)
    )
    current_value = max(intrinsic_value, intrinsic_value + time_value)
    percent_return = max(-100, ((current_value - initial_cost) / initial_cost) * 100)
    
    return percent_return

