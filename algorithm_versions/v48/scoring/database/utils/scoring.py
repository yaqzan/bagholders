import math
import os
import json
from pathlib import Path
import numpy as np
from colorama import init, Fore

from strategy_config import SCORING as _S

init(autoreset=True, convert=True)

# ─── Module-level scoring constants (env-overridable, sourced from SCORING) ──
#
# All knobs touching `compute_overall_score` and weekly helpers live here.
# Defaults flow from `strategy_config.SCORING` (the single source of truth);
# environment variable override is preserved per the same pattern used in
# monte_carlo.py / backtest_cascade.py so Stage 1 sweeps can hot-swap any
# value without editing scoring.py.
#
# `tests/test_strategy_config_drift.py::check_scoring()` enforces that each
# binding below equals its SCORING default. Edit SCORING in strategy_config.py
# AND update the corresponding default here in lockstep, OR drift-guard fails.
#
# Field names match SCORING.<FIELD> exactly — drift-guard pairs by name.

def _envf(name: str, default: float) -> float:
    return float(os.environ.get(name, default))

def _envi(name: str, default: int) -> int:
    return int(os.environ.get(name, default))

def _envb(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw == '1' or raw.lower() == 'true'

def _envs(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Misc legacy
MOVING_AVERAGE_PERIOD       = _envi('MOVING_AVERAGE_PERIOD',  _S.MOVING_AVERAGE_PERIOD)
MOMENTUM_LOOKBACK_DAYS      = _envi('MOMENTUM_LOOKBACK_DAYS', _S.MOMENTUM_LOOKBACK_DAYS)

# Component weighting (compute_overall_score)
W_TREND_BASE                = _envf('W_TREND_BASE',           _S.W_TREND_BASE)
W_TREND_SLOPE               = _envf('W_TREND_SLOPE',          _S.W_TREND_SLOPE)
W_BB_BASE                   = _envf('W_BB_BASE',              _S.W_BB_BASE)
W_RSI_BASE                  = _envf('W_RSI_BASE',             _S.W_RSI_BASE)
W_RSI_SLOPE                 = _envf('W_RSI_SLOPE',            _S.W_RSI_SLOPE)
W_MACD_BASE                 = _envf('W_MACD_BASE',            _S.W_MACD_BASE)
W_MACD_SLOPE                = _envf('W_MACD_SLOPE',           _S.W_MACD_SLOPE)
W_STOCH_BASE                = _envf('W_STOCH_BASE',           _S.W_STOCH_BASE)
W_TA_BASE                   = _envf('W_TA_BASE',              _S.W_TA_BASE)
W_TA_SLOPE                  = _envf('W_TA_SLOPE',             _S.W_TA_SLOPE)
TREND_BIAS_SCALE            = _envf('TREND_BIAS_SCALE',       _S.TREND_BIAS_SCALE)
TREND_DOMINANCE_POWER       = _envf('TREND_DOMINANCE_POWER',  _S.TREND_DOMINANCE_POWER)
BB_DOMINANCE_DAMP           = _envf('BB_DOMINANCE_DAMP',      _S.BB_DOMINANCE_DAMP)
OSC_DOMINANCE_DAMP          = _envf('OSC_DOMINANCE_DAMP',     _S.OSC_DOMINANCE_DAMP)

# Asymmetric MACD / RSI gates
PUT_MACD_GATE               = _envf('PUT_MACD_GATE',          _S.PUT_MACD_GATE)
PUT_RSI_ZERO_GATE           = _envf('PUT_RSI_ZERO_GATE',      _S.PUT_RSI_ZERO_GATE)

# Volume amplification
VOL_BOOST_TREND_SCALE       = _envf('VOL_BOOST_TREND_SCALE',  _S.VOL_BOOST_TREND_SCALE)
VOL_EMA_DAMP_THRESH         = _envf('VOL_EMA_DAMP_THRESH',    _S.VOL_EMA_DAMP_THRESH)
VOL_EMA_DAMP_K              = _envf('VOL_EMA_DAMP_K',         _S.VOL_EMA_DAMP_K)
VOL_EMA_DAMP_RANGE          = _envf('VOL_EMA_DAMP_RANGE',     _S.VOL_EMA_DAMP_RANGE)
BLEND_W_CAP                 = _envf('BLEND_W_CAP',            _S.BLEND_W_CAP)
BLEND_REVERSAL_HALF         = _envf('BLEND_REVERSAL_HALF',    _S.BLEND_REVERSAL_HALF)
BLEND_REVERSAL_GATE         = _envf('BLEND_REVERSAL_GATE',    _S.BLEND_REVERSAL_GATE)
BLEND_VOL_TARGET_LO         = _envf('BLEND_VOL_TARGET_LO',    _S.BLEND_VOL_TARGET_LO)
BLEND_VOL_TARGET_HI         = _envf('BLEND_VOL_TARGET_HI',    _S.BLEND_VOL_TARGET_HI)
BLEND_MACD_LO               = _envf('BLEND_MACD_LO',          _S.BLEND_MACD_LO)
BLEND_MACD_HI               = _envf('BLEND_MACD_HI',          _S.BLEND_MACD_HI)
CONVICTION_MACD_CAP         = _envf('CONVICTION_MACD_CAP',    _S.CONVICTION_MACD_CAP)

# Mis-stress softener (Priority #6/#8)
MIS_STRESS_CALL_DAMPEN      = _envf('MIS_STRESS_CALL_DAMPEN', _S.MIS_STRESS_CALL_DAMPEN)

# Capitulation gradient dampener
CAP_GATE_SCORE              = _envi('CAP_GATE_SCORE',         _S.CAP_GATE_SCORE)
CAP_EMA_THRESH              = _envf('CAP_EMA_THRESH',         _S.CAP_EMA_THRESH)
CAP_BASE_LIFT               = _envf('CAP_BASE_LIFT',          _S.CAP_BASE_LIFT)
CAP_RAMP_OFFSET             = _envf('CAP_RAMP_OFFSET',        _S.CAP_RAMP_OFFSET)
CAP_LIFT_CAP                = _envi('CAP_LIFT_CAP',           _S.CAP_LIFT_CAP)

# Exhaustion gradient dampener
EXH_GATE                    = _envi('EXH_GATE',               _S.EXH_GATE)
EXH_GATE_DENOM              = _envf('EXH_GATE_DENOM',         _S.EXH_GATE_DENOM)
EXH_MACDH_SCALE             = _envf('EXH_MACDH_SCALE',        _S.EXH_MACDH_SCALE)
EXH_K                       = _envf('EXH_K',                  _S.EXH_K)
EXH_TARGET                  = _envf('EXH_TARGET',             _S.EXH_TARGET)

# Ext-focal gradient dampener
EXT_FOCAL_GATE              = _envi('EXT_FOCAL_GATE',         _S.EXT_FOCAL_GATE)
EXT_FOCAL_RAMP              = _envf('EXT_FOCAL_RAMP',         _S.EXT_FOCAL_RAMP)
EXT_FOCAL_K                 = _envf('EXT_FOCAL_K',            _S.EXT_FOCAL_K)

# WCF lift (puts toward 50 when weekly non-confirming)
WCF_LIFT_GATE               = _envi('WCF_LIFT_GATE',          _S.WCF_LIFT_GATE)
WCF_LIFT_WADJ_CUTOFF        = _envf('WCF_LIFT_WADJ_CUTOFF',   _S.WCF_LIFT_WADJ_CUTOFF)
WCF_LIFT_K                  = _envf('WCF_LIFT_K',             _S.WCF_LIFT_K)
WCF_LIFT_TARGET             = _envi('WCF_LIFT_TARGET',        _S.WCF_LIFT_TARGET)

# CWCF — Call WCF mirror dampener
CWCF_DAMPEN_GATE            = _envi('CWCF_DAMPEN_GATE',       _S.CWCF_DAMPEN_GATE)
CWCF_DAMPEN_WADJ_CUTOFF     = _envf('CWCF_DAMPEN_WADJ_CUTOFF', _S.CWCF_DAMPEN_WADJ_CUTOFF)
CWCF_DAMPEN_WADJ_RANGE      = _envf('CWCF_DAMPEN_WADJ_RANGE', _S.CWCF_DAMPEN_WADJ_RANGE)
CWCF_DAMPEN_K               = _envf('CWCF_DAMPEN_K',          _S.CWCF_DAMPEN_K)
CWCF_DAMPEN_TARGET          = _envi('CWCF_DAMPEN_TARGET',     _S.CWCF_DAMPEN_TARGET)

# CWWD — Call Weak-Weekly Dampener
CWWD_DAMPEN_GATE_LO         = _envi('CWWD_DAMPEN_GATE_LO',    _S.CWWD_DAMPEN_GATE_LO)
CWWD_DAMPEN_GATE_HI         = _envi('CWWD_DAMPEN_GATE_HI',    _S.CWWD_DAMPEN_GATE_HI)
CWWD_DAMPEN_STOCH_FLOOR     = _envf('CWWD_DAMPEN_STOCH_FLOOR', _S.CWWD_DAMPEN_STOCH_FLOOR)
CWWD_DAMPEN_STOCH_RANGE     = _envf('CWWD_DAMPEN_STOCH_RANGE', _S.CWWD_DAMPEN_STOCH_RANGE)
CWWD_DAMPEN_WADJ_RANGE      = _envf('CWWD_DAMPEN_WADJ_RANGE', _S.CWWD_DAMPEN_WADJ_RANGE)
CWWD_DAMPEN_K               = _envf('CWWD_DAMPEN_K',          _S.CWWD_DAMPEN_K)
CWWD_DAMPEN_TARGET          = _envi('CWWD_DAMPEN_TARGET',     _S.CWWD_DAMPEN_TARGET)

# CSWC — Stoch-Weekly Contradiction
CSWC_DAMPEN_GATE            = _envi('CSWC_DAMPEN_GATE',       _S.CSWC_DAMPEN_GATE)
CSWC_DAMPEN_WADJ_LO         = _envf('CSWC_DAMPEN_WADJ_LO',    _S.CSWC_DAMPEN_WADJ_LO)
CSWC_DAMPEN_WADJ_HI         = _envf('CSWC_DAMPEN_WADJ_HI',    _S.CSWC_DAMPEN_WADJ_HI)
CSWC_DAMPEN_STOCH_RANGE     = _envf('CSWC_DAMPEN_STOCH_RANGE', _S.CSWC_DAMPEN_STOCH_RANGE)
CSWC_DAMPEN_WADJ_DENOM      = _envf('CSWC_DAMPEN_WADJ_DENOM', _S.CSWC_DAMPEN_WADJ_DENOM)
CSWC_DAMPEN_K               = _envf('CSWC_DAMPEN_K',          _S.CSWC_DAMPEN_K)
CSWC_DAMPEN_TARGET          = _envi('CSWC_DAMPEN_TARGET',     _S.CSWC_DAMPEN_TARGET)

# SCW — Stoch Conviction Wave
SCW_ENABLED                 = _envb('SCW_ENABLED',            _S.SCW_ENABLED)
SCW_GATE_CALL               = _envi('SCW_GATE_CALL',          _S.SCW_GATE_CALL)
SCW_MAX_PENALTY             = _envf('SCW_MAX_PENALTY',        _S.SCW_MAX_PENALTY)
SCW_STOCH_POWER             = _envf('SCW_STOCH_POWER',        _S.SCW_STOCH_POWER)
SCW_DECAY_POWER             = _envf('SCW_DECAY_POWER',        _S.SCW_DECAY_POWER)
SCW_WEEKLY_HI               = _envf('SCW_WEEKLY_HI',          _S.SCW_WEEKLY_HI)

# Continuation boost
CONT_BOOST_ENABLED          = _envb('CONT_BOOST_ENABLED',     _S.CONT_BOOST_ENABLED)
CONT_BOOST_SIG_MIN          = _envf('CONT_BOOST_SIG_MIN',     _S.CONT_BOOST_SIG_MIN)
CONT_BOOST_TAU              = _envf('CONT_BOOST_TAU',         _S.CONT_BOOST_TAU)
CONT_BOOST_MAG_EXP          = _envf('CONT_BOOST_MAG_EXP',     _S.CONT_BOOST_MAG_EXP)
CONT_BOOST_SIG_NORM         = _envf('CONT_BOOST_SIG_NORM',    _S.CONT_BOOST_SIG_NORM)
CONT_BOOST_GATE_LO          = _envi('CONT_BOOST_GATE_LO',     _S.CONT_BOOST_GATE_LO)
CONT_BOOST_GATE_HI          = _envi('CONT_BOOST_GATE_HI',     _S.CONT_BOOST_GATE_HI)
CONT_BOOST_PROMOTE_TARGET   = _envi('CONT_BOOST_PROMOTE_TARGET', _S.CONT_BOOST_PROMOTE_TARGET)

# PCD — Post-Crash put Dampener
PCD_ENABLED                 = _envb('PCD_ENABLED',            _S.PCD_ENABLED)
PCD_GATE                    = _envi('PCD_GATE',               _S.PCD_GATE)
PCD_RET10D_SIGMA            = _envf('PCD_RET10D_SIGMA',       _S.PCD_RET10D_SIGMA)
PCD_TARGET                  = _envi('PCD_TARGET',             _S.PCD_TARGET)

# MCD — Mcap Dampener
MCD_ENABLED                 = _envb('MCD_ENABLED',            _S.MCD_ENABLED)
MCD_GATE_LO                 = _envi('MCD_GATE_LO',            _S.MCD_GATE_LO)
MCD_GATE_HI                 = _envi('MCD_GATE_HI',            _S.MCD_GATE_HI)
MCD_LOG_LO                  = _envf('MCD_LOG_LO',             _S.MCD_LOG_LO)
MCD_LOG_HI                  = _envf('MCD_LOG_HI',             _S.MCD_LOG_HI)
MCD_ALPHA                   = _envf('MCD_ALPHA',              _S.MCD_ALPHA)
MCD_TARGET                  = _envi('MCD_TARGET',             _S.MCD_TARGET)
MCD_MCAP_POWER              = _envf('MCD_MCAP_POWER',         _S.MCD_MCAP_POWER)
MCD_SCORE_POWER             = _envf('MCD_SCORE_POWER',        _S.MCD_SCORE_POWER)

# ICH — Ichimoku Kijun-sen state dampener
ICH_ENABLED                 = _envb('ICH_ENABLED',            _S.ICH_ENABLED)
ICH_GATE_CALL_LO            = _envi('ICH_GATE_CALL_LO',       _S.ICH_GATE_CALL_LO)
ICH_GATE_CALL_HI            = _envi('ICH_GATE_CALL_HI',       _S.ICH_GATE_CALL_HI)
ICH_K_CALL                  = _envf('ICH_K_CALL',             _S.ICH_K_CALL)
ICH_K_CALL_POWER            = _envf('ICH_K_CALL_POWER',       _S.ICH_K_CALL_POWER)
ICH_KIJ_SAT_CALL            = _envf('ICH_KIJ_SAT_CALL',       _S.ICH_KIJ_SAT_CALL)
ICH_TARGET_CALL             = _envf('ICH_TARGET_CALL',        _S.ICH_TARGET_CALL)
ICH_GATE_PUT_LO             = _envi('ICH_GATE_PUT_LO',        _S.ICH_GATE_PUT_LO)
ICH_GATE_PUT_HI             = _envi('ICH_GATE_PUT_HI',        _S.ICH_GATE_PUT_HI)
ICH_K_PUT                   = _envf('ICH_K_PUT',              _S.ICH_K_PUT)
ICH_KIJ_SAT_PUT             = _envf('ICH_KIJ_SAT_PUT',        _S.ICH_KIJ_SAT_PUT)
ICH_TARGET_PUT              = _envf('ICH_TARGET_PUT',         _S.ICH_TARGET_PUT)
ICH_IND_RAMP_CALL           = _envs('ICH_IND_RAMP_CALL',      _S.ICH_IND_RAMP_CALL)
ICH_IND_RAMP_PUT            = _envs('ICH_IND_RAMP_PUT',       _S.ICH_IND_RAMP_PUT)

# WVD-Wave inverted-U modulator on weekly volume force1
WVD_WAVE_ENABLED            = _envb('WVD_WAVE_ENABLED',       _S.WVD_WAVE_ENABLED)
WVD_WAVE_GATE_LO            = _envi('WVD_WAVE_GATE_LO',       _S.WVD_WAVE_GATE_LO)
WVD_WAVE_GATE_HI            = _envi('WVD_WAVE_GATE_HI',       _S.WVD_WAVE_GATE_HI)
WVD_WAVE_SCORE_POWER        = _envf('WVD_WAVE_SCORE_POWER',   _S.WVD_WAVE_SCORE_POWER)
WVD_WAVE_PEAK               = _envf('WVD_WAVE_PEAK',          _S.WVD_WAVE_PEAK)
WVD_WAVE_WIDTH              = _envf('WVD_WAVE_WIDTH',         _S.WVD_WAVE_WIDTH)
WVD_WAVE_K_LIFT             = _envf('WVD_WAVE_K_LIFT',        _S.WVD_WAVE_K_LIFT)
WVD_WAVE_TARGET_LIFT        = _envf('WVD_WAVE_TARGET_LIFT',   _S.WVD_WAVE_TARGET_LIFT)
WVD_WAVE_CLIMAX_THRESH      = _envf('WVD_WAVE_CLIMAX_THRESH', _S.WVD_WAVE_CLIMAX_THRESH)
WVD_WAVE_CLIMAX_SAT         = _envf('WVD_WAVE_CLIMAX_SAT',    _S.WVD_WAVE_CLIMAX_SAT)
WVD_WAVE_K_DAMPEN           = _envf('WVD_WAVE_K_DAMPEN',      _S.WVD_WAVE_K_DAMPEN)
WVD_WAVE_TARGET_DAMPEN      = _envf('WVD_WAVE_TARGET_DAMPEN', _S.WVD_WAVE_TARGET_DAMPEN)

# PESS — Put Earnings Score Suppression
PESS_GATE_LO                = _envi('PESS_GATE_LO',           _S.PESS_GATE_LO)
PESS_GATE_HI                = _envi('PESS_GATE_HI',           _S.PESS_GATE_HI)
PESS_DAYS_MIN               = _envi('PESS_DAYS_MIN',          _S.PESS_DAYS_MIN)
PESS_DAYS_MAX               = _envi('PESS_DAYS_MAX',          _S.PESS_DAYS_MAX)
PESS_FADE_WIDTH             = _envf('PESS_FADE_WIDTH',        _S.PESS_FADE_WIDTH)
PESS_PROXIMITY_FULL_DAY     = _envi('PESS_PROXIMITY_FULL_DAY', _S.PESS_PROXIMITY_FULL_DAY)
PESS_PROXIMITY_FADE_END     = _envi('PESS_PROXIMITY_FADE_END', _S.PESS_PROXIMITY_FADE_END)
PESS_K                      = _envf('PESS_K',                 _S.PESS_K)
PESS_TARGET                 = _envi('PESS_TARGET',            _S.PESS_TARGET)

# Earnings meta-score boost
EARN_BOOST_ENABLED          = _envb('EARN_BOOST_ENABLED',     _S.EARN_BOOST_ENABLED)
EARN_BOOST_WINDOW           = _envi('EARN_BOOST_WINDOW',      _S.EARN_BOOST_WINDOW)
EARN_BOOST_MAX              = _envf('EARN_BOOST_MAX',         _S.EARN_BOOST_MAX)
EARN_BOOST_LIFT_NORM_CALL   = _envf('EARN_BOOST_LIFT_NORM_CALL', _S.EARN_BOOST_LIFT_NORM_CALL)
EARN_BOOST_LIFT_NORM_PUT    = _envf('EARN_BOOST_LIFT_NORM_PUT',  _S.EARN_BOOST_LIFT_NORM_PUT)
EARN_BOOST_MIN_N            = _envi('EARN_BOOST_MIN_N',       _S.EARN_BOOST_MIN_N)
EARN_BOOST_PUT_ADMIT        = _envb('EARN_BOOST_PUT_ADMIT',   _S.EARN_BOOST_PUT_ADMIT)

# Weekly adjustment (calculate_weekly_adjustment)
WEEKLY_BASE_BIAS_MAX        = _envf('WEEKLY_BASE_BIAS_MAX',         _S.WEEKLY_BASE_BIAS_MAX)
WEEKLY_BASE_BIAS_DEV_SCALE  = _envf('WEEKLY_BASE_BIAS_DEV_SCALE',   _S.WEEKLY_BASE_BIAS_DEV_SCALE)
WEEKLY_AGREEMENT_BASE       = _envf('WEEKLY_AGREEMENT_BASE',        _S.WEEKLY_AGREEMENT_BASE)
WEEKLY_AGREEMENT_AMP        = _envf('WEEKLY_AGREEMENT_AMP',         _S.WEEKLY_AGREEMENT_AMP)
WEEKLY_MOMENTUM_MAX         = _envf('WEEKLY_MOMENTUM_MAX',          _S.WEEKLY_MOMENTUM_MAX)
WEEKLY_MOMENTUM_DELTA_SCALE = _envf('WEEKLY_MOMENTUM_DELTA_SCALE',  _S.WEEKLY_MOMENTUM_DELTA_SCALE)
WEEKLY_PUT_SCALE            = _envf('WEEKLY_PUT_SCALE',             _S.WEEKLY_PUT_SCALE)
WEEKLY_AGREEMENT_AVG_THRESH = _envf('WEEKLY_AGREEMENT_AVG_THRESH',  _S.WEEKLY_AGREEMENT_AVG_THRESH)

# Weekly composite (calculate_weekly_composite)
WCOMP_NO_TREND_RSI          = _envf('WCOMP_NO_TREND_RSI',           _S.WCOMP_NO_TREND_RSI)
WCOMP_NO_TREND_MACD         = _envf('WCOMP_NO_TREND_MACD',          _S.WCOMP_NO_TREND_MACD)
WCOMP_TREND_BASE            = _envf('WCOMP_TREND_BASE',             _S.WCOMP_TREND_BASE)
WCOMP_RSI_BASE              = _envf('WCOMP_RSI_BASE',               _S.WCOMP_RSI_BASE)
WCOMP_MACD_BASE             = _envf('WCOMP_MACD_BASE',              _S.WCOMP_MACD_BASE)
WCOMP_DAMPEN_HALF_GAP       = _envf('WCOMP_DAMPEN_HALF_GAP',        _S.WCOMP_DAMPEN_HALF_GAP)
WCOMP_DAMPEN_K              = _envf('WCOMP_DAMPEN_K',               _S.WCOMP_DAMPEN_K)


def compute_ret_10d_sigma(closes_asc, target_idx):
    """Compute 10-day return normalized by 60-day realized daily vol.

    closes_asc: ascending list/array of daily close prices
    target_idx: index in closes_asc for the signal date

    Returns ret_10d / (sigma_pct/100 * sqrt(10)) or None if insufficient data
    (need >= 60 prior bars for sigma, >= 10 for ret_10d).
    """
    if PCD_ENABLED is False or target_idx < 60:
        return None
    try:
        close_today = float(closes_asc[target_idx])
        close_10ago = float(closes_asc[target_idx - 10])
    except (IndexError, TypeError, ValueError):
        return None
    if close_10ago <= 0 or close_today <= 0:
        return None
    ret_10d = (close_today - close_10ago) / close_10ago

    # 60-day stdev of daily returns ending at target_idx
    daily_rets = []
    for i in range(target_idx - 60 + 1, target_idx + 1):
        if i < 1:
            return None
        prev = closes_asc[i - 1]
        cur = closes_asc[i]
        if prev is None or cur is None or float(prev) <= 0:
            return None
        daily_rets.append((float(cur) - float(prev)) / float(prev))
    if len(daily_rets) < 60:
        return None
    sigma_d = float(np.std(daily_rets, ddof=1))  # daily stdev (decimal)
    if sigma_d <= 0:
        return None
    return ret_10d / (sigma_d * math.sqrt(10))


def build_ret10d_sigma_map(ph_rows_asc):
    """Build {date: ret_10d_sigma} map for all dates in price history.

    ph_rows_asc: ascending list of PriceHistory rows (must have .date and .close).
    Returns dict; dates with insufficient lookback get value None.
    """
    if not ph_rows_asc or len(ph_rows_asc) < 61:
        return {}
    closes = [float(p.close) if p.close is not None else None for p in ph_rows_asc]
    out = {}
    for idx in range(60, len(ph_rows_asc)):
        out[ph_rows_asc[idx].date] = compute_ret_10d_sigma(closes, idx)
    return out


def build_kijun_pct_map(weekly_rows_asc, ph_rows_asc):
    """Build {date: kijun_pct} for each daily scoring date.

    Ichimoku Kijun-sen = midpoint of 26-week high+low. We use the LAST COMPLETED
    weekly bar (look up at scoring_date - 7 calendar days) to avoid partial-week
    bias that drove COHR-class whiplash (Priority #7).

    weekly_rows_asc: ascending list of WeeklyPriceHistory rows (.date, .high, .low)
    ph_rows_asc: ascending list of PriceHistory rows (.date, .close)
    Returns dict {daily_date: kijun_pct}; entries with insufficient history are None.
    kijun_pct = (close - kijun) / kijun * 100   (positive = above kijun = bullish weekly)
    """
    import bisect
    from datetime import timedelta as _timedelta

    if not weekly_rows_asc or len(weekly_rows_asc) < 26 or not ph_rows_asc:
        return {}

    # 1) Compute kijun-sen for each weekly bar (rolling 26-bar high/low midpoint).
    weekly_dates = []
    weekly_kijun = []
    for i in range(25, len(weekly_rows_asc)):
        window = weekly_rows_asc[i - 25:i + 1]   # 26 bars inclusive
        try:
            highs = [float(w.high) for w in window if w.high is not None]
            lows  = [float(w.low)  for w in window if w.low  is not None]
        except (TypeError, ValueError):
            weekly_dates.append(weekly_rows_asc[i].date)
            weekly_kijun.append(None)
            continue
        if len(highs) < 26 or len(lows) < 26:
            weekly_dates.append(weekly_rows_asc[i].date)
            weekly_kijun.append(None)
            continue
        weekly_dates.append(weekly_rows_asc[i].date)
        weekly_kijun.append((max(highs) + min(lows)) / 2.0)

    # 2) For each daily date, find latest weekly bar with date <= (daily_date - 7 days)
    out = {}
    for ph in ph_rows_asc:
        dd = ph.date
        if dd is None or ph.close is None:
            continue
        try:
            close = float(ph.close)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            out[dd] = None
            continue
        lookup_date = dd - _timedelta(days=7)
        idx = bisect.bisect_right(weekly_dates, lookup_date) - 1
        if idx < 0 or idx >= len(weekly_kijun):
            out[dd] = None
            continue
        kijun = weekly_kijun[idx]
        if kijun is None or kijun <= 0:
            out[dd] = None
            continue
        out[dd] = (close - kijun) / kijun * 100.0
    return out


def build_wv_force1_map(weekly_rows_asc, ph_rows_asc):
    """Build {daily_date: wv_force1} for each daily scoring date.

    wv_force1 = ((close - prev_close) / prev_close) × (vol_curr / mean_4w_vol_prior)

    Computed from the LAST COMPLETED weekly bar (look up at scoring_date - 7
    calendar days) — same convention as build_kijun_pct_map to avoid the
    partial-week-bar bias that drove COHR-class whiplash.

    Captures the inverted-U signal underlying WVD-Wave: moderate positive
    force = sustained accumulation (good); extreme positive = climax (bad).

    weekly_rows_asc: ascending list of WeeklyPriceHistory rows (.date, .close, .volume)
    ph_rows_asc: ascending list of PriceHistory rows (.date)
    Returns dict {daily_date: wv_force1}; entries with insufficient history are None.
    """
    import bisect
    from datetime import timedelta as _timedelta

    if not weekly_rows_asc or len(weekly_rows_asc) < 5 or not ph_rows_asc:
        return {}

    weekly_dates = [w.date for w in weekly_rows_asc]
    weekly_closes = []
    weekly_vols   = []
    for w in weekly_rows_asc:
        try:
            weekly_closes.append(float(w.close) if w.close is not None else None)
            weekly_vols.append(float(w.volume) if w.volume is not None else None)
        except (TypeError, ValueError):
            weekly_closes.append(None)
            weekly_vols.append(None)

    out = {}
    for ph in ph_rows_asc:
        dd = ph.date
        if dd is None:
            continue
        lookup_date = dd - _timedelta(days=7)
        idx = bisect.bisect_right(weekly_dates, lookup_date) - 1
        if idx < 4:   # need at least 5 weekly bars (current + 4 prior)
            out[dd] = None
            continue
        c_curr = weekly_closes[idx]
        c_prev = weekly_closes[idx - 1]
        v_curr = weekly_vols[idx]
        if (c_curr is None or c_prev is None or v_curr is None
                or c_prev <= 0 or v_curr <= 0):
            out[dd] = None
            continue
        prior4 = [weekly_vols[idx - i] for i in range(1, 5)]
        if any(v is None or v <= 0 for v in prior4):
            out[dd] = None
            continue
        avg_v4 = sum(prior4) / 4.0
        if avg_v4 <= 0:
            out[dd] = None
            continue
        delta_pct = (c_curr - c_prev) / c_prev
        out[dd] = float(delta_pct * (v_curr / avg_v4))
    return out


def _classify_prior_sig(w7, w15, w30, w60):
    """W60-dominant prior contribution sign per Q3 continuation analysis.

    W60 known win     → +1.0  (sustained move confirmed)
    W60 known loss    → -0.3  (trend died by 60d; early-fizzler pattern → -0.4)
    W30 win, W60 open → +0.5  (partial confirmation)
    W30 loss, W60 open → -0.2 (mid-trend death; early-stall → -0.3)
    Both W30+W60 open → 0.0  (too recent — no signal)
    """
    if w60 is not None:
        if w60 == 1:
            return +1.0
        if (w7 is not None and w15 is not None and w30 is not None
                and w7 == 1 and w15 == 1 and w30 == 0):
            return -0.4
        return -0.3
    if w30 is not None:
        if w30 == 1:
            return +0.5
        if w7 is not None and w15 is not None and w7 == 1 and w15 == 1:
            return -0.3
        return -0.2
    return 0.0


def compute_cont_prior_signal(signal_date, prior_score_pairs, barrier_wins_by_date):
    """Compute continuation prior_signal ∈ [-1, +1] for a call signal on signal_date.

    prior_score_pairs: iterable of (prior_date, prior_overall) — candidates for priors.
    barrier_wins_by_date: {prior_date: {w_days: result}} loaded from barrier_outcomes cache.

    Returns a float; 0.0 when no qualifying priors found.
    """
    total = 0.0
    for pd_, pov in prior_score_pairs:
        gap = (signal_date - pd_).days
        if gap < 7 or gap > 60:
            continue
        if pov is None or pov < 70:
            continue
        wins = barrier_wins_by_date.get(pd_, {})
        r7  = wins.get(7)  if gap >= 7  else None
        r15 = wins.get(15) if gap >= 15 else None
        r30 = wins.get(30) if gap >= 30 else None
        r60 = wins.get(60) if gap >= 60 else None
        sig = _classify_prior_sig(r7, r15, r30, r60)
        if sig == 0.0:
            continue
        conviction = abs(pov - 50)
        magnitude  = (conviction / 50.0) ** CONT_BOOST_MAG_EXP
        decay      = math.exp(-gap / CONT_BOOST_TAU)
        total      += decay * magnitude * sig
    if total == 0.0:
        return 0.0
    return math.tanh(total / CONT_BOOST_SIG_NORM)


_EARN_BOOST_STRENGTH_MAP = None  # lazy-loaded


def _load_earn_boost_strength_map():
    """Load + parse the calibrated lift table; returns {(side, cohort, bucket): strength}.

    side: 'low' = call side; 'high' = put side (matches assess_scores conventions).
    cohort: 'pre1' | 'pre3' | 'pre7'.
    bucket: '95+' | '90-94' | '85-89' | '80-84' | '75-79' | '70-74' | '0-5' | '6-10' | '11-15' | '16-20' | '21-25'.
    strength: float in [0, 1], log-smoothed from empirical WR15 lift.
    """
    global _EARN_BOOST_STRENGTH_MAP
    if _EARN_BOOST_STRENGTH_MAP is not None:
        return _EARN_BOOST_STRENGTH_MAP
    # v35 recalibration (2026-05-04): switched to v34 pre-boost lift table.
    # v27 table preserved at experiments/v27_optimization/phase_tp3b_lift_table.json
    # for historical reference / regression testing.
    repo_root = Path(__file__).resolve().parents[2]
    table_path = repo_root / 'experiments' / 'v34_calibration' / 'lift_table_v34.json'
    if not table_path.exists():
        _EARN_BOOST_STRENGTH_MAP = {}
        return _EARN_BOOST_STRENGTH_MAP
    with open(table_path) as f:
        raw = json.load(f)
    log_norm_call = math.log(1 + EARN_BOOST_LIFT_NORM_CALL)
    log_norm_put  = math.log(1 + EARN_BOOST_LIFT_NORM_PUT)
    smap = {}
    for k, v in raw.items():
        side, cohort, bucket = k.split('|')
        lift, n = v
        if n < EARN_BOOST_MIN_N or lift <= 0:
            continue
        log_norm = log_norm_call if side == 'low' else log_norm_put
        strength = min(1.0, math.log(1 + lift) / log_norm)
        smap[(side, cohort, bucket)] = strength
    _EARN_BOOST_STRENGTH_MAP = smap
    return smap


def _ern_cohort(d_to_ern):
    if d_to_ern is None or d_to_ern < 0 or d_to_ern > EARN_BOOST_WINDOW:
        return None
    if d_to_ern <= 1: return 'pre1'
    if d_to_ern <= 3: return 'pre3'
    if d_to_ern <= 7: return 'pre7'
    return None


def _ern_bucket(overall):
    if overall >= 95: return '95+'
    if overall >= 90: return '90-94'
    if overall >= 85: return '85-89'
    if overall >= 80: return '80-84'
    if overall >= 75: return '75-79'
    if overall >= 70: return '70-74'
    if overall <= 5:  return '0-5'
    if overall <= 10: return '6-10'
    if overall <= 15: return '11-15'
    if overall <= 20: return '16-20'
    if overall <= 25: return '21-25'
    return None


def compute_earnings_boost_strength(overall, days_to_earnings):
    """Return the boost coefficient (0 if no boost) for a (current overall, days_to_earnings) pair.

    Final boost mult = 1 + EARN_BOOST_MAX * proximity * strength.
    """
    if not EARN_BOOST_ENABLED or days_to_earnings is None:
        return 0.0
    cohort = _ern_cohort(days_to_earnings)
    if cohort is None:
        return 0.0
    bucket = _ern_bucket(overall)
    if bucket is None:
        return 0.0
    side = 'low' if overall >= 50 else 'high'
    # Put admit constraint
    if side == 'high' and not EARN_BOOST_PUT_ADMIT and overall > 25:
        return 0.0
    smap = _load_earn_boost_strength_map()
    strength = smap.get((side, cohort, bucket), 0.0)
    if strength <= 0.0:
        return 0.0
    log_w_plus_1 = math.log(EARN_BOOST_WINDOW + 1)
    proximity = math.log(EARN_BOOST_WINDOW + 1 - days_to_earnings) / log_w_plus_1
    return EARN_BOOST_MAX * proximity * strength


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
    mis_stress=0.0,
    days_to_earnings=None,
    prior_signal=None,
    ret_10d_sigma=None,
    mcap_b=None,
    kijun_pct=None,
    wv_force1=None,
    **_extra,
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
    trend_bias      = float(np.tanh((trend - 50) * TREND_BIAS_SCALE))
    trend_strength  = abs(trend_bias)
    trend_dominance = trend_strength ** TREND_DOMINANCE_POWER

    if bb_pct is not None:
        bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
        bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
        trend_dominance *= (1.0 - BB_DOMINANCE_DAMP * (bull_ext + bear_ext))

    osc_avg = (rsi + macd) / 2.0
    bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
    bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
    osc_divergence = bull_div + bear_div
    if bb_pct is not None:
        osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
    trend_dominance *= (1.0 - OSC_DOMINANCE_DAMP * osc_divergence)

    d      = trend_dominance
    # V6 weights: cap trend at 28 (was 35), give the 7pts back to RSI/MACD.
    # Rationale: at trend=99 with raw RSI/Stoch neutral, the old w_trend=35
    # alone added ~17pts to overall, conflating "strong uptrend" with
    # "overbought" and producing HIGH (short) signals on continuation setups.
    # Validated on full universe (515 stocks, 365d): 70+ Cap30 0.311 → 0.349,
    # 90+ WR 38.9% → 62.5%.
    w_trend = W_TREND_BASE + W_TREND_SLOPE * d
    w_bb    = W_BB_BASE
    w_rsi   = W_RSI_BASE  + W_RSI_SLOPE  * d
    w_macd  = W_MACD_BASE + W_MACD_SLOPE * d
    w_stoch = W_STOCH_BASE
    w_ta    = W_TA_BASE   + W_TA_SLOPE   * d

    # ── Asymmetric MACD: zero MACD on bearish setups ─────────────────────────
    # MACD is a lagging momentum indicator: on bearish setups it stays positive
    # until breakdown is confirmed, suppressing put scores. When the pre-MACD
    # score is < PUT_MACD_GATE, zero MACD's weight and redistribute proportionally
    # to the remaining components. Validated 2026-04-17 (5y full universe):
    # put <25 WR15 +4.3pp, <15 WR15 +6.5pp; call side entirely unchanged.
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
    if _pre_rsi < PUT_RSI_ZERO_GATE:
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
    vol_boost = 1.0 + VOL_BOOST_TREND_SCALE * trend_strength

    # Fix 1 — EMA extension dampening: reduce vol_boost when price is extended
    # from EMA50. A stock at +23% EMA50 is not a fresh entry; amplifying CONVICTION
    # there risks inflating the score past what the setup warrants.
    # Activates only when pct_from_ema50 is provided by the caller.
    if pct_from_ema50 is not None:
        ext = abs(pct_from_ema50)
        if ext > VOL_EMA_DAMP_THRESH:
            ema_damp = 1.0 - VOL_EMA_DAMP_K * min(1.0, (ext - VOL_EMA_DAMP_THRESH) / VOL_EMA_DAMP_RANGE)
            vol_boost *= ema_damp

    if blend_w > 0:
        blend_w = min(blend_w * vol_boost, BLEND_W_CAP)
        # Fix 3 — REVERSAL blend dampening: when ABSORPTION/REJECTION blends the
        # score toward an extreme vol_target but MACD contradicts that direction and
        # the pre-blend score is not already in extreme territory, halve the blend
        # weight. Prevents maximum ABSORPTION from overriding a moderate pre-vol
        # score when momentum (MACD) disagrees with the reversal thesis.
        if abs(weighted_sum - 50) < BLEND_REVERSAL_GATE:
            if ((vol_target < BLEND_VOL_TARGET_LO and macd > BLEND_MACD_HI)
                    or (vol_target > BLEND_VOL_TARGET_HI and macd < BLEND_MACD_LO)):
                blend_w *= BLEND_REVERSAL_HALF
        weighted_sum = weighted_sum * (1 - blend_w) + vol_target * blend_w
    elif vol_mult != 1.0:
        # Fix 2 — CONVICTION-MACD contradiction gate: when CONVICTION amplifies
        # a score but MACD directionally contradicts that score, cap the
        # amplification to CONVICTION_MACD_CAP of its normal magnitude.
        # Prevents a large up-day (CONVICTION bullish) from making an already-
        # bearish score even more bearish when MACD is also bullish.
        if vol_sig == 'CONVICTION':
            if ((weighted_sum < BLEND_VOL_TARGET_LO and macd > BLEND_MACD_HI)
                    or (weighted_sum > BLEND_VOL_TARGET_HI and macd < BLEND_MACD_LO)):
                vol_mult = 1.0 + (vol_mult - 1.0) * CONVICTION_MACD_CAP
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

    # Mis-stress softener (Priority #6/#8 ship 2026-04-26):
    # On objectively-bull-mislabeled-stress days, soften regime compression on
    # call side. Pulls _eff_mult toward 1.0 by (mis_stress * MIS_STRESS_CALL_DAMPEN).
    # Preserves regime mechanics on real-stress days (mis_stress=0) and on puts
    # (gated on overall >= 50). See .claude/docs/scoring-algorithm.md for derivation.
    # Validated 2026-04-25 (5y diff-assess + Phase-A sweep): 22-now CALL75+ N +5.6%,
    # WR15 +0.2pp; 2024 +0.1pp; 2022 near-no-op (+1 call).
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

    # Capitulation gradient dampener: score=0 + pct_from_ema50 < -10% = active
    # capitulation, not a fresh put setup. Gradient lift ext=10%→5, 15%→10,
    # 20%+→20. Validated 5y: <5 WR15 65.8%→71.2%, <10 68.1%→72.6%, <15 67.8%→71.0%.
    _cap_dampened = False
    if overall == CAP_GATE_SCORE and pct_from_ema50 is not None and pct_from_ema50 < CAP_EMA_THRESH:
        ext = abs(pct_from_ema50)
        overall = min(CAP_LIFT_CAP, int(round(CAP_BASE_LIFT + (ext - CAP_RAMP_OFFSET))))
        _cap_dampened = True

    # Exhaustion gradient dampener: at deeply bearish scores (<=9), MACDh turning
    # positive indicates bounce setup, not structural weakness. Lift toward 10
    # proportional to (a) how bearish the score is and (b) how positive macdh is.
    # Evidence from experiments/low_extreme_pattern.py (v20, 5y): at 0-4, rows
    # with macdh>=0 carry WR30 66.9% vs 73.2% baseline (and 73.8% at 5-9 bucket).
    # Redirects bounce-prone extreme puts into the 5-9 sweet spot.
    _exh_damp = 0.0
    if overall <= EXH_GATE and macdh_raw is not None:
        score_weight = (EXH_GATE - overall) / EXH_GATE_DENOM      # 1.0 at 0, 0 at EXH_GATE
        mh_pos = max(0.0, float(np.tanh(macdh_raw / EXH_MACDH_SCALE)))  # 0 for negative mh
        _exh_damp = score_weight * mh_pos * EXH_K
        if _exh_damp > 0.0:
            overall = int(round(overall * (1 - _exh_damp) + EXH_TARGET * _exh_damp))

    # Ext-focal gradient dampener: puts (<=25) with price ABOVE EMA50 are
    # profit-taking pullbacks in uptrends, not breakdown setups. 5y fine-grained
    # sweep (experiments/ext_focal_sweep.py, v20): WR30 declines monotonically
    # from 70.3% at ext=-10..-4 to 60.7% at ext>+10 for s<=25. Lift proportional
    # to how low the score is AND how far above EMA50 the price is.
    _ext_damp = 0.0
    if overall <= EXT_FOCAL_GATE and pct_from_ema50 is not None and pct_from_ema50 > 0.0:
        ext_ramp = min(1.0, pct_from_ema50 / EXT_FOCAL_RAMP)       # 0 at ext=0, 1 at ext>=ramp
        score_weight = (EXT_FOCAL_GATE - overall) / EXT_FOCAL_GATE  # 1.0 at 0, 0 at gate
        _ext_damp = EXT_FOCAL_K * ext_ramp * score_weight
        if _ext_damp > 0.0:
            lift = _ext_damp * (EXT_FOCAL_GATE - overall)
            overall = int(min(100, round(overall + lift)))

    # Weekly-confirmation floor lift (Priority #13 ship 2026-04-27).
    # When a put score reaches extreme territory (overall < 28) but the weekly
    # didn't strongly confirm the bearish thesis (w_adj > -17), lift the score
    # toward 50 — out of put trading range. The mechanism mirrors the existing
    # gradient dampeners (capitulation, exhaustion, ext-focal) but uses the
    # weekly's own magnitude as the discriminator instead of price location.
    #
    # Per-trade evidence (experiments/put_wadj_cross_buckets.py, 5y): puts
    # with w_adj > -13 carry 8-15pp lower WR15 across every put bucket; the
    # =5 dip splits Q1/Q4 by w_adj at 84.1% / 58.8% (Δ=−25.2pp on N=69/68).
    # Pattern is mirrored on calls at smaller magnitude (3-9pp).
    #
    # Phase 4 Bayesian (1300+ variants via fast_variant_runner) found:
    #   K=0.95, wadj_cutoff=-17, score_gate=28, lift_target=50, linear-clip
    # weakness — best WR lift at zero call regression and largest put N
    # reduction (toward call:put parity). vs current production (no lift):
    #   <10 WR15 +4.0pp, <15 WR15 +3.0pp, <25 WR15 +5.9pp, 70+ unchanged,
    #   put N 65k -> 16k (-75%, ratio 5:1 -> 1.4:1 vs calls).
    #
    # Linear-clip weakness > tanh smoothness (tanh leaks lift into strong-
    # weekly territory; linear clip cleanly defines "wadj <= -17 = strong").
    # Multi-stage / score_floor / call-mirror variants tested in Phase 5a
    # (put_floor_phase5a.py) — all redundant or harmful given this lift.
    # Calls untouched (gated on overall < 28 = put territory).
    _wcf_lift = 0.0
    if overall < WCF_LIFT_GATE and weekly_detail is not None:
        _wadj_value = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_value > WCF_LIFT_WADJ_CUTOFF:
            _wcf_denom = -WCF_LIFT_WADJ_CUTOFF if WCF_LIFT_WADJ_CUTOFF != 0 else 1.0
            _wcf_weakness = max(0.0, min(1.0, (_wadj_value - WCF_LIFT_WADJ_CUTOFF) / _wcf_denom))
            if _wcf_weakness > 0:
                _wcf_lift = WCF_LIFT_K * _wcf_weakness * (WCF_LIFT_TARGET - overall)
                overall = int(min(100, round(overall + _wcf_lift)))

    # Call-side WCF-mirror dampener — pulls call scores down toward 55 when overall >= 75
    # AND weekly is non-confirming (wadj < 1, i.e. weakly bullish or bearish). Mirror of
    # the v27 put WCF lift on the call side.
    #
    # Per-trade evidence (experiments/miss_ledger/, 5y v31):
    #   wadj-neg cohort (wadj < 0) miss rate 52.5% vs cohort baseline 41.4% on calls 70+
    #   (lift 1.27, z=+10.1 — largest single-feature miss driver in the analysis).
    #   Compounded with vsig=CONVICTION: 56.1% miss; with vmag=mid: 57.5%.
    #
    # H1-H5 ship gate (per experiments/miss_ledger/call_wcf_mirror_sweep.py, 5y/3y/1y vs
    # 30dte_opt option-aligned barriers at w=15d):
    #   85+ TP% +0.95pp (N -5%), 80+ +1.09pp (N -7%), 75+ +1.14pp (N -10%);
    #   95+/90+ within ±1pp on small N=43/200 baseline; puts unchanged (gate=75).
    #   Multi-window 75+: 1y +0.32pp, 3y +1.49pp, 5y +1.14pp — sign-consistent.
    #
    # Calibration (32 variants, fast_variant_runner via call_wcf_mirror_sweep.py):
    #   K=0.95, wadj_cutoff=+1, score_gate=75, lift_target=55. Wider cutoffs (5-17)
    #   give larger per-trade lift but collapse N by 27-90% — that's a stricter gate,
    #   not real alpha. Narrow cut=1 surgically targets the wadj-neg miss cohort.
    #
    # Applied AFTER put-WCF lift, BEFORE earnings boost — so dampened calls don't get
    # re-boosted into 80+ via the earnings amplifier.
    _cwcf_dampen = 0.0
    if overall >= CWCF_DAMPEN_GATE and weekly_detail is not None:
        _wadj_c = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_c < CWCF_DAMPEN_WADJ_CUTOFF:
            _cwcf_weakness = max(0.0, min(1.0, (CWCF_DAMPEN_WADJ_CUTOFF - _wadj_c) / CWCF_DAMPEN_WADJ_RANGE))
            if _cwcf_weakness > 0:
                _cwcf_dampen = CWCF_DAMPEN_K * _cwcf_weakness * (overall - CWCF_DAMPEN_TARGET)
                overall = int(max(0, round(overall - _cwcf_dampen)))

    # Call Weak-Weekly Dampener (CWWD) — extends CWCF below 75 (v38 ship 2026-05-06).
    # CWCF gates on overall>=75 with wadj<1; CWWD covers the 70-74 range with
    # wadj<0 (narrower cutoff for the shallower zone). Gradient on stoch
    # increases dampening when stoch contradicts the call thesis (overbought
    # but weekly bearish) — true 2D gradient, no hard threshold.
    #
    # Per-trade evidence (v36/v37 miss-ledger, byte-identical across versions):
    #   CALL 70+ wadj<0 cohort: miss 52.7% (vs 41.1% baseline), z=+9.2 — the
    #   highest single-feature miss z-score in the entire ledger.
    #   The 70-74 isolated cohort: 1,501 signals at 52.9% miss = 47.1% TP,
    #   10.5pp below the 70-74 baseline (57.6%).
    # CWCF doesn't reach this zone (gate=75); CWWD does.
    #
    # Calibration (experiments/cwwd_v38/sweep.py, 14 variants on v37 5y):
    #   ALPHA=0.95, WADJ_K=5, STOCH_GE_FLOOR=25, STOCH_RAMP=35.
    #   gradient over thresholds (per ship guidance) — stoch=25 → no dampen,
    #   stoch=60+ → full dampen; wadj=0 → no dampen, wadj≤-5 → full dampen.
    #
    # Per-trade gate (sub-75 H1 fix): 70+ TP +0.62pp / N -4.6%; 75+/80+/85+/90+/95+
    # byte-identical (zero spillover); puts byte-identical (gate is calls only);
    # multi-window 1y +0.62 / 3y +0.74 / 5y +0.62 — sign-consistent.
    #
    # Replaces the WEAK_WEEKLY_CALL_DROP portfolio-stage filter (shipped 2026-05-05,
    # retired with this ship) — encodes the same judgment in the score itself
    # so the dashboard doesn't show signals the strategy will silently skip.
    _cwwd_dampen = 0.0
    if CWWD_DAMPEN_GATE_LO <= overall < CWWD_DAMPEN_GATE_HI and weekly_detail is not None:
        _wadj_cwwd = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_cwwd < 0.0:
            _stoch_grad_cwwd = max(0.0, min(1.0, (stoch - CWWD_DAMPEN_STOCH_FLOOR) / CWWD_DAMPEN_STOCH_RANGE))
            _wadj_grad_cwwd  = max(0.0, min(1.0, -_wadj_cwwd / CWWD_DAMPEN_WADJ_RANGE))
            _cwwd_weakness   = _stoch_grad_cwwd * _wadj_grad_cwwd
            if _cwwd_weakness > 0.0:
                _cwwd_dampen = CWWD_DAMPEN_K * _cwwd_weakness * (overall - CWWD_DAMPEN_TARGET)
                overall = int(max(0, round(overall - _cwwd_dampen)))

    # Stoch-Weekly Contradiction dampener — gradient form (call-side, Priority #4 extension).
    # When overall >= 75 AND stoch is below-neutral AND weekly is weakly positive,
    # pull score toward 55. Gradient (no hard stoch gate): dampening scales continuously
    # from zero at stoch=35 (neutral boundary) to full at stoch=0. CWCF handles wadj<1;
    # this covers the weakly-positive wadj=[1,14) residue when stoch also contradicts.
    #
    # v36 recalibration (2026-05-05): K 0.30→0.50, wg 12→14. The v34 K=0.30 ship
    # left +4.7pp residual miss-lift in the CSWC zone on the v35 ledger
    # (CALL 75+ wadj∈[1,12) ∧ stoch<35: 43.0% miss vs 38.3% baseline, N=2,082).
    # v35 K-resweep (stoch_wadj_v35_resweep.py, 144 variants, 44 pass H1-H5):
    #   sn=35 wg=14 K=0.50 lifts 5y TP%: 85+ +0.66pp, 80+ +1.91pp, 75+ +0.88pp,
    #   90+ +2.19pp, 95+ +1.77pp. Sign-consistent 1y/3y/5y on all primary tiers.
    #   N drops within H3 ±15% (75+ -9.6%, 80+ -13.8%, 85+ -13.6%). Puts unchanged.
    _cswc_dampen = 0.0
    if overall >= CSWC_DAMPEN_GATE and weekly_detail is not None:
        _wadj_sw = float(weekly_detail.get('w_adj', 0.0))
        if CSWC_DAMPEN_WADJ_LO <= _wadj_sw < CSWC_DAMPEN_WADJ_HI:
            # Gradient: 0 when stoch=range (neutral), 1 when stoch=0 (maximally bearish)
            _stoch_w = max(0.0, (CSWC_DAMPEN_STOCH_RANGE - stoch) / CSWC_DAMPEN_STOCH_RANGE)
            # Gradient: 0 when wadj=HI, 1 when wadj=LO (CWCF handles <LO)
            _wadj_sw_w = max(0.0, min(1.0, (CSWC_DAMPEN_WADJ_HI - _wadj_sw) / CSWC_DAMPEN_WADJ_DENOM))
            _cswc_weakness = _stoch_w * _wadj_sw_w
            if _cswc_weakness > 0.0:
                _cswc_dampen = CSWC_DAMPEN_K * _cswc_weakness * (overall - CSWC_DAMPEN_TARGET)
                overall = int(max(0, round(overall - _cswc_dampen)))

    # Continuation boost (v33 ship 2026-05-03).
    # prior_signal pre-computed by batch scorer from barrier_outcomes cache.
    # No-cascade: _pre_boost_overall snapshots the score here so future prior
    # lookups can use weight_info['pre_boost'] instead of the final Score.overall.
    # Applies to all sub-threshold calls (70-74) regardless of CWCF history —
    # the v2 experimental TP% (62.2%) was measured on raw DB overalls which include
    # CWCF-dampened scores, so the gate is consistent with the experimental evidence.
    _pre_boost_overall = overall
    _cont_lift = 0
    if (CONT_BOOST_ENABLED
            and prior_signal is not None
            and prior_signal >= CONT_BOOST_SIG_MIN
            and CONT_BOOST_GATE_LO <= overall <= CONT_BOOST_GATE_HI):
        _cont_lift = CONT_BOOST_PROMOTE_TARGET - overall
        overall = CONT_BOOST_PROMOTE_TARGET

    # Post-Crash put Dampener (PCD ship 2026-05-05).
    # Lift puts OUT of the put bucket when the underlying recently fell more than
    # PCD_RET10D_SIGMA stock-sigmas over the last 10 trading bars. Vol-fair via
    # 60-day realized sigma. Apply BEFORE earnings boost so the boost can't
    # re-amplify a displaced peak. Calls untouched (gate <= PCD_GATE = 25).
    # See module-level PCD constants for derivation + ship gate evidence.
    _pcd_active = False
    if (PCD_ENABLED
            and overall <= PCD_GATE
            and ret_10d_sigma is not None
            and ret_10d_sigma <= PCD_RET10D_SIGMA):
        if PCD_TARGET > overall:
            overall = PCD_TARGET
            _pcd_active = True

    # MCD — Mcap Dampener (ship 2026-05-07).
    # Asymmetric calls-only score-stage dampener using log10(mcap_b) as a
    # continuous confidence-shifter. Drifts mid/small-cap calls in [70, 84]
    # toward TARGET=61 with magnitude controlled by two power-law factors:
    # mcap_factor (small-cap concentration) × score_factor (high-score
    # concentration). Large-caps >= $79B and signals at 70-72 essentially
    # untouched (preserves natural-wave gradient at the bottom of gate range).
    # ETFs and stocks without market_cap data are skipped via `mcap_b is not
    # None` gate — the cohort signal was calibrated on individual stocks; ETFs
    # have different volatility/structure characteristics and aren't part of
    # the "mcap structural confidence" cohort.
    # See module-level MCD_* constants for derivation + ship gate evidence.
    _mcd_dampen = 0.0
    if (MCD_ENABLED
            and MCD_GATE_LO <= overall <= MCD_GATE_HI
            and mcap_b is not None
            and mcap_b > 0
            and MCD_LOG_HI > MCD_LOG_LO):
        log_mcap = math.log10(max(0.001, float(mcap_b)))
        if log_mcap < MCD_LOG_HI:
            mcap_raw = (MCD_LOG_HI - log_mcap) / (MCD_LOG_HI - MCD_LOG_LO)
            mcap_raw = max(0.0, min(1.0, mcap_raw))
            mcap_factor = mcap_raw ** MCD_MCAP_POWER
            score_raw = (overall - MCD_GATE_LO) / (MCD_GATE_HI - MCD_GATE_LO)
            score_raw = max(0.0, min(1.0, score_raw))
            score_factor = score_raw ** MCD_SCORE_POWER
            weakness = mcap_factor * score_factor
            if weakness > 0.0:
                _mcd_dampen = MCD_ALPHA * weakness * (overall - MCD_TARGET)
                overall = int(max(0, min(100, round(overall - _mcd_dampen))))

    # ICH — Ichimoku Kijun-sen state dampener (Phase G ship-pending).
    # Score-stage continuous dampener using bearish weekly Kijun-sen state
    # (kijun_pct < 0 = price below 26w midpoint).  Captures peaks orthogonal
    # to MCD: 35% cohort overlap but produces +4.60pp WR15 marginal lift on
    # the MCD-not-fired sub-cohort at 75+.
    #
    # CALL side (asymmetric-K, Phase G):
    #   score_norm  = max(0, (overall - GATE_CALL_LO) / (GATE_CALL_HI - GATE_CALL_LO))
    #                 # NO upper clip — score_norm continues past 1.0 above GATE_HI
    #   K_eff       = K_CALL_BASE × score_norm ** K_CALL_POWER
    #   ind_grad    = ramp(max(0, -kijun_pct), KIJ_SAT_CALL)   # linear by default
    #   overall    -= K_eff × ind_grad × (overall - LIFT_TARGET_CALL)
    #
    # The power-law on score_norm concentrates dampening at top tiers where
    # the bearish-Ichimoku signal is strongest (Phase G empirical: 95+ ΔWR
    # +5.77pp vs 85+ +0.51pp). Backward-compat: K_POWER=1.0 = uniform K.
    #
    # PUT side (Phase C Rank #1 architecture, log ramp):
    #   score_grad  = ramp((GATE_PUT_HI - overall) / (GATE_PUT_HI - GATE_PUT_LO))
    #   ind_grad    = ramp(max(0, -kijun_pct), KIJ_SAT_PUT)   # log by default
    #   overall    += K_PUT × score_grad × ind_grad × (LIFT_TARGET_PUT - overall)
    #
    # Apply order: AFTER MCD (calls) and PCD (puts), BEFORE PESS and EARN_BOOST.
    # See module-level ICH_* constants for derivation + ship gate evidence.
    _ich_call_d = 0.0
    _ich_put_l  = 0.0
    if (ICH_ENABLED
            and kijun_pct is not None
            and kijun_pct < 0.0):
        _ich_ind_dist = -float(kijun_pct)   # positive when below kijun

        def _ich_ramp(x, sat, shape):
            if sat <= 0:
                return 0.0
            xc = max(0.0, x)
            if shape == 'log':
                return max(0.0, min(1.0, math.log1p(xc) / math.log1p(sat)))
            return max(0.0, min(1.0, xc / sat))

        # CALL side: asymmetric-K (power-law on score_norm) × indicator ramp
        if (ICH_GATE_CALL_HI > ICH_GATE_CALL_LO
                and overall >= ICH_GATE_CALL_LO):
            _score_range_c = max(1, ICH_GATE_CALL_HI - ICH_GATE_CALL_LO)
            _score_norm_c = max(0.0, (overall - ICH_GATE_CALL_LO) / _score_range_c)
            _k_eff_c = ICH_K_CALL * (_score_norm_c ** ICH_K_CALL_POWER)
            _ig_c = _ich_ramp(_ich_ind_dist, ICH_KIJ_SAT_CALL, ICH_IND_RAMP_CALL)
            if _k_eff_c > 0.0 and _ig_c > 0.0:
                _ich_call_d = _k_eff_c * _ig_c * (overall - ICH_TARGET_CALL)
                if _ich_call_d > 0:
                    overall = int(max(0, min(100, round(overall - _ich_call_d))))

        # PUT side: log ramp on both score zone and indicator (Phase C Rank #1)
        if (ICH_GATE_PUT_HI > ICH_GATE_PUT_LO
                and overall <= ICH_GATE_PUT_HI):
            _sg_p = _ich_ramp(ICH_GATE_PUT_HI - overall,
                              ICH_GATE_PUT_HI - ICH_GATE_PUT_LO,
                              ICH_IND_RAMP_PUT)
            _ig_p = _ich_ramp(_ich_ind_dist, ICH_KIJ_SAT_PUT, ICH_IND_RAMP_PUT)
            _w_p = _sg_p * _ig_p
            if _w_p > 0.0:
                _ich_put_l = ICH_K_PUT * _w_p * (ICH_TARGET_PUT - overall)
                if _ich_put_l > 0:
                    overall = int(max(0, min(100, round(overall + _ich_put_l))))

    # WVD-Wave — score-stage inverted-U modulator on weekly volume force1.
    # (Phase Wave ship 2026-05-08, replaces failed dampener-only architecture.)
    # Captures full Q1/Q3/Q5 cohort signal:
    #   - LIFT moderate-positive force (Q3 anti-climax accumulation, peak +8.96pp)
    #   - DAMPEN extreme-positive force (Q5 climax exhaustion)
    # CLIMAX_THRESH=0.05 shields the Q3 sweet spot from dampening.
    # Calls only — puts handled by ICH/PCD/PESS. Apply order: AFTER ICH, BEFORE PESS.
    # See module-level WVD_WAVE_* constants for derivation + ship gate evidence.
    _wvd_lift   = 0.0
    _wvd_dampen = 0.0
    if (WVD_WAVE_ENABLED
            and wv_force1 is not None
            and overall >= WVD_WAVE_GATE_LO):
        _wvd_score_range = max(1, WVD_WAVE_GATE_HI - WVD_WAVE_GATE_LO)
        _wvd_score_norm  = max(0.0, min(1.0,
            (overall - WVD_WAVE_GATE_LO) / _wvd_score_range))
        _wvd_score_w     = _wvd_score_norm ** WVD_WAVE_SCORE_POWER

        # Gaussian LIFT centered at moderate-force (Q3 anti-climax cohort)
        _wvd_delta = float(wv_force1) - WVD_WAVE_PEAK
        _wvd_bell  = math.exp(-(_wvd_delta / WVD_WAVE_WIDTH) ** 2)

        # Smooth DAMPEN ramp on excess force above climax threshold (Q5 only)
        _wvd_excess      = max(0.0, float(wv_force1) - WVD_WAVE_CLIMAX_THRESH)
        _wvd_dampen_grad = math.tanh(_wvd_excess / WVD_WAVE_CLIMAX_SAT)

        _wvd_lift   = (WVD_WAVE_K_LIFT   * _wvd_score_w * _wvd_bell
                        * (WVD_WAVE_TARGET_LIFT - overall))
        _wvd_dampen = (WVD_WAVE_K_DAMPEN * _wvd_score_w * _wvd_dampen_grad
                        * (overall - WVD_WAVE_TARGET_DAMPEN))
        overall = int(max(0, min(100,
            round(overall + _wvd_lift - _wvd_dampen))))

    # PESS — Put Earnings Score Suppression (v39 ship 2026-05-06).
    # Score-stage replacement for the EARN_SUPP_PUT cascade-stage filter.
    # Lifts puts in [16, 20] near upcoming earnings OUT of the put-qualifying
    # universe (target=28).  Applied BEFORE EARN_BOOST so the lifted score
    # doesn't re-amplify on the put side (EARN_BOOST gate is overall<=25).
    #
    # Per-trade evidence (filter pretest): 16-20 cohort regresses -3.1pp
    # WR15 at d=3 (N=735); -0.7pp aggregate at d=5 (N=4910).  At v37
    # post-PCD, only ~65 puts/year remain in this cohort (most got pushed
    # out by EARN_BOOST or PCD); per-trade <25 cumulative impact is small
    # (+0.06pp) but portfolio impact via slot displacement is +44.7%
    # compound (per the canonical N=1000 MC validation that originally
    # shipped EARN_SUPP_PUT).  Same-mechanism replacement.
    #
    # Calibration (experiments/pess_v39/sweep.py, 11 variants on v37 5y):
    #   ALPHA=0.95, TARGET=28, score peak [16,20] with fade width 3,
    #   proximity full d=1-5 fading to d=7.  Gradient over thresholds.
    #
    # Retires EARN_SUPP_PUT cascade-stage filter (shipped 2026-04-26).
    _pess_lift = 0.0
    if (PESS_GATE_LO <= overall <= PESS_GATE_HI
            and days_to_earnings is not None
            and PESS_DAYS_MIN <= days_to_earnings <= PESS_DAYS_MAX):
        # Score gradient: peaks 1.0 at overall in [LO,HI], fades width=PESS_FADE_WIDTH
        # on each edge using the boundary - fade_width as the ramp anchor.
        _pess_mid_lo = PESS_GATE_LO + 1   # interior split point: at GATE_LO+1, both branches converge
        if overall <= _pess_mid_lo:
            _score_grad_pess = max(0.0, (overall - (PESS_GATE_LO - PESS_FADE_WIDTH)) / PESS_FADE_WIDTH)
            _score_grad_pess = min(1.0, _score_grad_pess)
        else:  # upper half of gate
            _score_grad_pess = max(0.0, ((PESS_GATE_HI + PESS_FADE_WIDTH) - overall) / PESS_FADE_WIDTH)
            _score_grad_pess = min(1.0, _score_grad_pess)
        # Proximity: 1.0 at d <= PESS_PROXIMITY_FULL_DAY, linear fade to 0 at d >= PESS_PROXIMITY_FADE_END
        if days_to_earnings <= PESS_PROXIMITY_FULL_DAY:
            _proximity_pess = 1.0
        else:
            _prox_range = max(1, PESS_PROXIMITY_FADE_END - PESS_PROXIMITY_FULL_DAY)
            _proximity_pess = max(0.0, (PESS_PROXIMITY_FADE_END - days_to_earnings) / _prox_range)
        _pess_weakness = _score_grad_pess * _proximity_pess
        if _pess_weakness > 0.0:
            _pess_lift = PESS_K * _pess_weakness * (PESS_TARGET - overall)
            overall = int(max(0, min(100, round(overall + _pess_lift))))

    # Earnings meta-score boost (Phase 3C ship 2026-04-28). Final transform.
    # Pulls (overall - 50) outward by a calibrated, log-smoothed multiplier
    # when signal is within EARN_BOOST_WINDOW trading days of upcoming earnings.
    # See compute_earnings_boost_strength() and module-level docs.
    _ern_boost_strength = compute_earnings_boost_strength(overall, days_to_earnings)
    if _ern_boost_strength > 0:
        boosted = 50 + (overall - 50) * (1.0 + _ern_boost_strength)
        overall = int(max(0, min(100, round(boosted))))

    # SCW — Stoch Conviction Wave (v47 ship 2026-05-11).
    # Final call-side timing dampener: low stochastic phase contradicts call
    # entries across the score surface, but the penalty decays sharply as
    # score conviction rises. Calibrated on v46 WR7 miss-ledger sweep:
    # call_mp15_sp1.0_dc4.0_w18, with 85+/90+/95+ preserved after rounding.
    _scw_dampen = 0.0
    if (SCW_ENABLED
            and overall >= SCW_GATE_CALL
            and weekly_detail is not None
            and SCW_WEEKLY_HI > 0):
        _wadj_scw = float(weekly_detail.get('w_adj', 0.0))
        _scw_stoch_wave = max(0.0, min(1.0, (50.0 - float(stoch)) / 50.0)) ** SCW_STOCH_POWER
        _scw_weekly_gap = max(0.0, min(1.0, (SCW_WEEKLY_HI - _wadj_scw) / SCW_WEEKLY_HI))
        _scw_conviction_decay = max(0.0, min(1.0, (100.0 - float(overall)) / 30.0)) ** SCW_DECAY_POWER
        _scw_dampen = SCW_MAX_PENALTY * _scw_stoch_wave * _scw_weekly_gap * _scw_conviction_decay
        if _scw_dampen > 0.0:
            overall = int(max(0, min(100, round(overall - _scw_dampen))))

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
    if _wcf_lift > 0.5:
        weight_info['wcf_lift'] = round(_wcf_lift, 2)
    if _cwcf_dampen > 0.5:
        weight_info['cwcf_dampen'] = round(_cwcf_dampen, 2)
    if _cwwd_dampen > 0.5:
        weight_info['cwwd_dampen'] = round(_cwwd_dampen, 2)
    if _pess_lift > 0.5:
        weight_info['pess_lift'] = round(_pess_lift, 2)
    if _cswc_dampen > 0.5:
        weight_info['cswc_dampen'] = round(_cswc_dampen, 2)
    if _scw_dampen > 0.5:
        weight_info['scw_dampen'] = round(_scw_dampen, 2)
    weight_info['pre_boost'] = _pre_boost_overall
    if _cont_lift > 0:
        weight_info['cont_lift'] = _cont_lift
        if prior_signal is not None:
            weight_info['cont_sig'] = round(float(prior_signal), 3)
    if _pcd_active:
        weight_info['pcd_active'] = 1
        if ret_10d_sigma is not None:
            weight_info['pcd_r10sigma'] = round(float(ret_10d_sigma), 3)
    if _mcd_dampen > 0.5:
        weight_info['mcd_dampen'] = round(_mcd_dampen, 2)
        if mcap_b is not None:
            weight_info['mcd_mcap_b'] = round(float(mcap_b), 2)
    if _ich_call_d > 0.5:
        weight_info['ich_call_dampen'] = round(_ich_call_d, 2)
    if _ich_put_l > 0.5:
        weight_info['ich_put_lift'] = round(_ich_put_l, 2)
    if (_ich_call_d > 0.5 or _ich_put_l > 0.5) and kijun_pct is not None:
        weight_info['kijun_pct'] = round(float(kijun_pct), 2)
    if abs(_wvd_lift) > 0.5:
        weight_info['wvd_lift'] = round(_wvd_lift, 2)
    if abs(_wvd_dampen) > 0.5:
        weight_info['wvd_dampen'] = round(_wvd_dampen, 2)
    if (abs(_wvd_lift) > 0.5 or abs(_wvd_dampen) > 0.5) and wv_force1 is not None:
        weight_info['wv_force1'] = round(float(wv_force1), 4)
    if _ern_boost_strength > 0:
        weight_info['ern_boost'] = round(_ern_boost_strength, 3)
        if days_to_earnings is not None:
            weight_info['days_to_ern'] = int(days_to_earnings)
    if put_regime_multiplier is not None:
        weight_info['put_regime_mult'] = round(put_regime_multiplier, 4)
    if mis_stress > 0:
        weight_info['mis_stress'] = round(float(mis_stress), 3)
    if weekly_detail:
        weight_info.update(weekly_detail)

    vol_update = {'volume': vol_raw, 'volume_signal': vol_sig, 'volume_magnitude': vol_mag} \
        if vol_sig != 'NEUTRAL' else None

    return overall, weight_info, vol_update

def calculate_weekly_composite(rsi, macd, trend=None):
    if None in (rsi, macd):
        return None
    if trend is None:
        composite = rsi * WCOMP_NO_TREND_RSI + macd * WCOMP_NO_TREND_MACD
    else:
        osc_avg = (rsi + macd) / 2.0
        gap = abs(trend - osc_avg)
        dampening = 1.0 - WCOMP_DAMPEN_K * min(1.0, gap / WCOMP_DAMPEN_HALF_GAP)
        w_trend = WCOMP_TREND_BASE * dampening
        extra = WCOMP_TREND_BASE * (1.0 - dampening)
        w_rsi  = WCOMP_RSI_BASE  + extra * 0.5
        w_macd = WCOMP_MACD_BASE + extra * 0.5
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

    # 1. BASE BIAS: how far weekly is from neutral, max ±WEEKLY_BASE_BIAS_MAX pts
    deviation = (composite - 50) / 50
    base_bias = WEEKLY_BASE_BIAS_MAX * float(np.tanh(deviation * WEEKLY_BASE_BIAS_DEV_SCALE))

    # 2. AGREEMENT AMPLIFIER: scales with directional alignment and strength
    signals = [(s - 50) / 50 for s in [trend, rsi, macd]]
    avg_signal = sum(signals) / len(signals)
    if abs(avg_signal) > WEEKLY_AGREEMENT_AVG_THRESH:
        consistency = sum(max(0, s * np.sign(avg_signal)) for s in signals) / len(signals)
    else:
        consistency = 0
    agreement = WEEKLY_AGREEMENT_BASE + WEEKLY_AGREEMENT_AMP * consistency
    base_bias *= agreement

    # 3. MOMENTUM: week-over-week delta detects regime shifts early
    momentum_bias = 0.0
    if None not in (prev_trend, prev_rsi, prev_macd):
        prev_composite = calculate_weekly_composite(prev_rsi, prev_macd, prev_trend) or 50
        delta = composite - prev_composite
        momentum_bias = WEEKLY_MOMENTUM_MAX * float(np.tanh(delta / WEEKLY_MOMENTUM_DELTA_SCALE))

    total = base_bias + momentum_bias

    # Asymmetric scaling: amplify put-direction signals (total < 0) by WEEKLY_PUT_SCALE.
    # Validated 2026-04-17 — lifts <25 WR30 +1.6pp and <15 WR30 +5.2pp
    # with call side unchanged. See CLAUDE.md §"Asymmetric weekly".
    if total < 0:
        total *= WEEKLY_PUT_SCALE

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

