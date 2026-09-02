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
    ws_trend=None, ws_rsi=None, ws_macd=None,
    prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
    vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
    blend_w=0.0, vol_target=50.0,
):
    """
    Pure scoring function — no DB calls.

    Returns (overall: int, weight_info: dict, vol_update: dict | None)

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
    if blend_w > 0:
        blend_w = min(blend_w * vol_boost, 0.60)
        weighted_sum = weighted_sum * (1 - blend_w) + vol_target * blend_w
    elif vol_mult != 1.0:
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    overall = int(max(0, min(100, weighted_sum)))

    weight_info = {
        'trend': round(w_trend, 1), 'bb': round(w_bb, 1),
        'rsi':   round(w_rsi, 1),   'macd': round(w_macd, 1),
        'stoch': round(w_stoch, 1), 'ta':   round(w_ta, 1),
        'td':    round(trend_dominance, 3),
    }
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

