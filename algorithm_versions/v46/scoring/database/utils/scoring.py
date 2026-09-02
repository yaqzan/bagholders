import math
import os
import json
from pathlib import Path
import numpy as np
from colorama import init, Fore

init(autoreset=True, convert=True)

MOVING_AVERAGE_PERIOD = 20
MOMENTUM_LOOKBACK_DAYS = 3


MIS_STRESS_CALL_DAMPEN = 0.25  # softener strength on call regime compression


# ── Earnings meta-score boost (Phase 3C ship 2026-04-28) ─────────────────────
# Score-stage multiplier on (overall - 50) when signal is within
# EARN_BOOST_WINDOW trading days of an upcoming earnings event. Boost
# magnitude is calibrated per (cohort × bucket) using a log-smoothed
# strength derived from the empirical WR15 lift table (computed by
# experiments/v27_optimization/phase_tp3b_calibration.py).
#
# Apply ordering: AFTER all other score-stage transforms (regime, capitulation,
# exhaustion, ext-focal, wcf_lift). This is the final step before clamping.
#
# Calls always boost (admit boundary into 70+ if applicable). Puts only boost
# when already in qualifying range (overall <= 25) — empirical lift cells for
# put 26-30 cohorts showed quality-dilution risk.
#
# Per-trade evidence (5y, calls): pre1 80-84 +22.3pp WR15, pre1 75-79 +17.7pp,
# pre3 80-84 +17.1pp, etc. Calibration places signals in the bucket whose
# baseline WR matches their cohort-conditional WR.
#
# Validated 2026-04-28 in canonical 3-mode MC (N=200, 8 windows): 22-now
# Realistic +10,876% over shipped baseline; all DD-C ≤ 80%; 0% collapse;
# only single-window regression -7.9% on 2021 (within ±25% gate).
EARN_BOOST_ENABLED        = os.environ.get('EARN_BOOST_ENABLED', '1') == '1'
EARN_BOOST_WINDOW         = int(os.environ.get('EARN_BOOST_WINDOW', '5'))
# v35 recalibration (2026-05-04): MAX 0.50→0.55, LIFT_NORM_CALL 22.3→14.0.
# Lift table relocated to experiments/v34_calibration/lift_table_v34.json
# (built on v34 pre_boost scores). 5y per-trade gate: 95+ +4.20pp / 90+
# +4.18pp / 85+ +0.86pp / 80+ +0.98pp WR15. H1-H5 PASS multi-window.
# See experiments/v34_calibration/ for sweep history.
EARN_BOOST_MAX            = float(os.environ.get('EARN_BOOST_MAX', '0.55'))
EARN_BOOST_LIFT_NORM_CALL = float(os.environ.get('EARN_BOOST_LIFT_NORM_CALL', '14.0'))
EARN_BOOST_LIFT_NORM_PUT  = float(os.environ.get('EARN_BOOST_LIFT_NORM_PUT',  '16.3'))
EARN_BOOST_MIN_N          = int(os.environ.get('EARN_BOOST_MIN_N', '10'))
EARN_BOOST_PUT_ADMIT      = os.environ.get('EARN_BOOST_PUT_ADMIT', '0') == '1'

# ── Continuation boost (v33 ship 2026-05-03) ─────────────────────────────────
# Elevates sub-threshold calls (70 ≤ overall ≤ 74) with strong prior-winner
# support to exactly 75 (the minimum qualifying threshold).
# V2 Path B calibration: TAU=40, MAG_EXP=0.70, SIG_NORM=3.0, SIG_MIN=0.20.
# 97 promotions over 5y; promoted-cohort TP% 62.2% vs 75+ baseline 61.02%.
# Q1 ship gate PASS (+0.01pp on 75+ tier TP%).
# No-cascade: weight_info['pre_boost'] snapshots the score before this step
# so future prior lookups read the raw pre-boost score, not the lifted one.
# Gate: only fires when CWCF dampener did NOT fire (don't re-promote a score
# that was deliberately pushed below 75 by the weekly non-confirmation check).
CONT_BOOST_ENABLED  = os.environ.get('CONT_BOOST_ENABLED',  '1') == '1'
CONT_BOOST_SIG_MIN  = float(os.environ.get('CONT_BOOST_SIG_MIN',  '0.20'))
CONT_BOOST_TAU      = float(os.environ.get('CONT_BOOST_TAU',      '40.0'))
CONT_BOOST_MAG_EXP  = float(os.environ.get('CONT_BOOST_MAG_EXP',  '0.70'))
CONT_BOOST_SIG_NORM = float(os.environ.get('CONT_BOOST_SIG_NORM',  '3.0'))


# ── Post-Crash put Dampener (PCD ship 2026-05-05) ────────────────────────────
# Lifts put scores OUT of the put bucket when the underlying recently fell more
# than 1.0 stock-sigmas over the last 10 trading bars. Vol-fair: ret_10d is
# normalized by 60-day realized daily volatility × sqrt(10), so the threshold
# means the same thing across high-vol and low-vol stocks.
#
# Per-trade evidence (experiments/post_crash_v2/, 5y v36, generic + option-aligned barriers):
#   <25 cohort underperforms put baseline by -7.15pp WR15 at the option barrier
#   (z=-6.88 on N=2,767). Sign-consistent across all 6 years and both barrier sets.
#
# H1-H5 ship gate (option-aligned barrier 30dte_opt at W=15d, vs v36 baseline):
#   <5 +2.95pp / <15 +3.22pp / <25 +1.56pp WR15 (5y, sign-consistent 1y/3y/5y);
#   calls 95+/90+/85+/80+/75+/70+ unchanged (gate is overall <= 25);
#   ~30% of put peaks at <=25 displaced (designed; precedent: v27 WCF dropped 75%).
#
# Calibration (experiments/post_crash_v2/sweep_pcd_sigma.py + verify_sigma_opt.py):
#   GATE=25, RET10D_SIGMA=-1.0, TARGET=30 (any T>=26 produces identical bucket
#   assignments since lift removes peak from all put buckets). Sweeps showed:
#   - sigma cutoff -0.75 fails H5 (1y <5 turns negative on small N)
#   - sigma cutoff -1.5 reduces lift magnitude without proportional N retention
#   - raw ret_10d cutoff -10% gives ~equivalent empirical lift but breaks vol-fairness
#
# Why sigma-normalized over raw: a -10% 10d move is ~3sigma for low-vol KO but
# normal noise for high-vol PLTR. Stock-vol confound check: raw ret_10d <= -10%
# cohort underperformance is z=-5.03 in low-vol stocks vs z=-1.63 in high-vol
# (not statistically significant). Sigma-norm correctly identifies "crashed" across
# all stocks regardless of base volatility. Aligns philosophically with the rest
# of the strategy where TP/SL barriers are sigma-defined.
#
# Apply order: AFTER continuation boost, BEFORE earnings boost — so the boost
# can't amplify a peak the dampener has already displaced.
PCD_ENABLED         = os.environ.get('PCD_ENABLED', '1') == '1'
PCD_GATE            = int(os.environ.get('PCD_GATE', '25'))
PCD_RET10D_SIGMA    = float(os.environ.get('PCD_RET10D_SIGMA', '-1.0'))
PCD_TARGET          = int(os.environ.get('PCD_TARGET', '30'))


# ── MCD — Mcap Dampener (ship 2026-05-07) ───────────────────────────────────
# Score-stage continuous dampener for calls in [70, 84] with mid/small market
# cap. Empirical cohort signal: at fixed score, call TP rate scales monotonically
# with mcap_b — large_50-200B 75+ TP=65.8% vs micro_lt2B 75+ TP=57.6% (8.2pp
# spread across 6 mcap bins, ~5,940 5y signals; year-stable across 2022-2025).
# See experiments/sector_mcap_cohort/FINDINGS.md for the cohort-mining trail.
#
# Mechanism (asymmetric, calls-only, log-mcap input, dual power-law weakness):
#
#   weakness = mcap_factor^MCD_MCAP_POWER  *  score_factor^MCD_SCORE_POWER
#       mcap_factor  = clip((LOG_HI - log10(mcap_b)) / (LOG_HI - LOG_LO), 0, 1)
#       score_factor = clip((overall - GATE_LO) / (GATE_HI - GATE_LO), 0, 1)
#   overall -= MCD_ALPHA * weakness * (overall - MCD_TARGET)
#
# - log_mcap input (continuous): caps span ~4 orders of magnitude ($300M to $5T);
#   linear-in-log normalizes that range. log10(mcap_b in $B) typically lands in
#   [-1, +4] — the ramp [LOG_LO, LOG_HI] is set in $B units (0.5 = $3.16B, 1.9 = $79B).
#
# - SCORE_POWER=1.5 (mild quadratic): concentrates dampening at higher scores
#   (80-84 mid-caps with strongest over-confidence) and barely touches 70-72
#   small-caps (preserves the natural-wave gradient at the bottom of the gate).
#   Validates user intuition that signal is real but smaller magnitude at lower
#   tiers — sweep showed score_power=1.5 in 19 of top-20 candidates.
#
# - MCAP_POWER=0.7 (slight concavity): keeps mcap_factor closer to 1 across the
#   small-to-mid cap range, then drops faster near LOG_HI. Balances aggressive
#   small-cap displacement with smooth mid-cap rolloff.
#
# - TARGET=61 (well below GATE_LO=70): drift target ensures dampened mid/small-
#   cap 80-84 signals exit the qualifying universe entirely (typical drop 9-18
#   points). Untouched signals are capped at GATE_HI=84 so 85+ tier is preserved
#   by construction.
#
# H1-H5 ship gate (option-aligned barrier 30dte_opt at W=15d, vs v39 baseline):
#   75+ cumulative: 5y +2.73pp, 10y +2.96pp (sign-consistent both windows)
#   Per-bucket: 70-74 +0.06pp (CALIBRATED), 75-79 +3.66pp (favorable over-correction),
#               80-84 -0.24pp (CALIBRATED), 85-89/90+ untouched (gate <= 84)
#   Gradient gaps preserved: [5.09, 2.41, 2.83, 6.09] (all >= 2.41pp)
#   N drop on 75+: -44.9% (within v27 WCF precedent ~75% drop)
#   Spillover on 80+/85+/90+: 0.00pp (top tiers untouched by construction)
#
# Calibration (experiments/mcap_dampener/, 15,195 total variants):
#   v1 linear (3,429 variants): +1.92pp 5y at LO=0.0/HI=2.2/α=0.30/T=65/mp=1/sp=1
#   v2 power-law (3,456 variants): +2.11pp 5y at LO=0.7/HI=1.8/α=0.95/T=65/mp=0.5/sp=2
#   v3 dense Bayesian (8,310 variants): champion +2.73pp 5y on stable basin
#
# Apply order: AFTER PCD (puts-only), BEFORE PESS (puts-only) and EARN_BOOST.
# Calls-only mechanism (gate 70-84) so order vs PESS doesn't matter mechanically;
# placement before EARN_BOOST ensures dampened scores aren't re-amplified.
MCD_ENABLED       = os.environ.get('MCD_ENABLED', '1') == '1'
MCD_GATE_LO       = int(os.environ.get('MCD_GATE_LO', '70'))
MCD_GATE_HI       = int(os.environ.get('MCD_GATE_HI', '84'))
MCD_LOG_LO        = float(os.environ.get('MCD_LOG_LO', '0.50'))   # $3.16B
MCD_LOG_HI        = float(os.environ.get('MCD_LOG_HI', '1.90'))   # $79.43B
MCD_ALPHA         = float(os.environ.get('MCD_ALPHA', '0.80'))
MCD_TARGET        = int(os.environ.get('MCD_TARGET', '61'))
MCD_MCAP_POWER    = float(os.environ.get('MCD_MCAP_POWER', '0.70'))
MCD_SCORE_POWER   = float(os.environ.get('MCD_SCORE_POWER', '1.50'))


# ── ICH — Ichimoku Kijun-sen state dampener (ship pending) ───────────────────
# Score-stage continuous dampener using weekly Kijun-sen distance as the
# indicator. Captures bearish-Ichimoku peaks that other mechanisms (MCD mcap,
# CWCF wadj, CWWD, CSWC) don't catch. 35% cohort overlap with v43 MCD but
# produces orthogonal WR signal: within MCD-not-fired cohort, bearish-Ichimoku
# still loses 4.60pp WR15 vs bullish-Ichimoku at 75+.
#
# Indicator: kijun_pct = (close - kijun_26w) / kijun_26w * 100.
# Computed from WeeklyPriceHistory using last COMPLETED weekly bar (lookback
# 7 calendar days) to avoid partial-week instability that drove the COHR-class
# whiplash issue (Priority #7). Phase D measured 3× more stable than wadj.
#
# Mechanism (calls + puts, both fire on bearish kijun = below 26w midpoint):
#
#   For overall in [ICH_GATE_CALL_LO, ICH_GATE_CALL_HI]:
#     score_grad = ramp((overall - LO) / (HI - LO))
#     ind_grad   = ramp(max(0, -kijun_pct) / KIJ_SAT_CALL)
#     overall   -= K_CALL * score_grad * ind_grad * (overall - TARGET_CALL)
#
#   For overall in [..., ICH_GATE_PUT_HI]:
#     score_grad = ramp((HI - overall) / (HI - LO))
#     ind_grad   = ramp(max(0, -kijun_pct) / KIJ_SAT_PUT)
#     overall   += K_PUT * score_grad * ind_grad * (TARGET_PUT - overall)
#
# Apply order: AFTER MCD (calls) and PCD (puts), BEFORE PESS and EARN_BOOST.
# So dampened scores aren't re-amplified by the earnings boost.
#
# Per-trade evidence (5y v43 baseline, option-aligned barrier 30dte_opt @ w=15d):
#   95+: +2.77pp WR15 / -3.8% N
#   90+: +1.14pp WR15 / -9.9% N
#   85+: +0.58pp WR15 / -5.8% N
#   <25 puts: +0.34pp WR15 / -9.0% N
# H1 strict PASS (≥+0.5pp on 95+/90+/85+); H3 PASS (max -9.9% on calls);
# H4 PASS (puts neutral or better); H5 sign-flips on 95+/90+ at 1y are
# small-N noise (1y N=5-26).
#
# Calibration: experiments/weekly_avwap/phase_e_refinement.py (200 variants
# constrained on H1 strict + H3 strict). Phase E Rank #3 winner — same
# config wins on both v39 and v43 baselines (robustness check).
ICH_ENABLED        = os.environ.get('ICH_ENABLED', '1') == '1'
# Phase H Rank #3 calibration (refined Phase G with multi-tier alpha objective).
# Asymmetric-K (K_CALL_POWER > 1) concentrates dampening at higher tiers
# where signal is strongest. Phase H tightened around Phase G basins and
# pushed K_POWER higher; result is +0.31pp on 90+ and +0.38pp on 85+ vs
# Phase G with cleaner H3 N stability on puts (-14.9% vs -19.8%).
ICH_GATE_CALL_LO   = int(os.environ.get('ICH_GATE_CALL_LO', '69'))
ICH_GATE_CALL_HI   = int(os.environ.get('ICH_GATE_CALL_HI', '90'))
ICH_K_CALL         = float(os.environ.get('ICH_K_CALL', '0.359'))
# K_CALL_POWER > 1 = power-law on score_norm; 1.0 = uniform K (Phase E shape).
# 2.68 = Phase H Rank #3 — concentrates dampening more aggressively at top
# tiers while still preserving 75-79 N.
ICH_K_CALL_POWER   = float(os.environ.get('ICH_K_CALL_POWER', '2.68'))
ICH_KIJ_SAT_CALL   = float(os.environ.get('ICH_KIJ_SAT_CALL', '18.4'))
ICH_TARGET_CALL    = float(os.environ.get('ICH_TARGET_CALL', '63.8'))
ICH_GATE_PUT_LO    = int(os.environ.get('ICH_GATE_PUT_LO', '10'))
ICH_GATE_PUT_HI    = int(os.environ.get('ICH_GATE_PUT_HI', '27'))
ICH_K_PUT          = float(os.environ.get('ICH_K_PUT', '0.278'))
ICH_KIJ_SAT_PUT    = float(os.environ.get('ICH_KIJ_SAT_PUT', '8.8'))
ICH_TARGET_PUT     = float(os.environ.get('ICH_TARGET_PUT', '33.4'))
# Indicator ramp shapes — call uses linear, put uses log (Phase G architecture).
ICH_IND_RAMP_CALL  = os.environ.get('ICH_IND_RAMP_CALL', 'linear')
ICH_IND_RAMP_PUT   = os.environ.get('ICH_IND_RAMP_PUT',  'log')


# ── WVD-Wave — score-stage inverted-U modulator on weekly volume force1 ──────
# (Phase Wave ship 2026-05-08, retires the failed dampener-only architecture)
#
# Cohort signal at calls 75+ (v45 baseline, 30dte_opt @ w=15):
#   Q1 (force1 < -0.05): WR15 = 65.2%  (z=-0.3, mild lag)
#   Q3 (force1 ~  0):    WR15 = 75.0%  (z=+3.26, +8.96pp PEAK)
#   Q5 (force1 > +0.06): WR15 = 60.7%  (z=-1.9, climax exhaustion)
#
# Inverted-U: best at moderate-positive (anti-climax accumulation), worst at
# extreme-positive (climax). Pure dampener architecture failed portfolio MC
# (compound -69% / DD +7.3pp at 5y) because it dampened the high-quality Q3
# cohort. Wave-modulator's CLIMAX_THRESH=0.05 SHIELDS the Q3 sweet spot —
# only Q5 climax (force1 > 0.05) gets dampened.
#
# CALL side only (puts handled by ICH/PCD/PESS):
#   score_norm  = clip((overall - GATE_LO)/(GATE_HI - GATE_LO), 0, 1)
#   bell_lift   = exp(-((wv_force1 - PEAK)/WIDTH)^2)
#   excess      = max(0, wv_force1 - CLIMAX_THRESH)
#   dampen_grad = tanh(excess / CLIMAX_SAT)
#   overall   += K_LIFT   * score_norm^SCORE_POWER * bell_lift   * (TARGET_LIFT   - overall)
#   overall   -= K_DAMPEN * score_norm^SCORE_POWER * dampen_grad * (overall - TARGET_DAMPEN)
#
# Apply order: AFTER ICH, BEFORE PESS / EARN_BOOST.
#
# Calibration: experiments/weekly_volume/wave_sweep.py (4608 variants, top-1).
# Per-trade gate (5y v45, multi-window): 75+ +1.11pp / 90+ +7.45pp WR15.
# 6/6 cells positive on 75+ and 90+ across (1y/3y/5y × TP15/TP30).
# Portfolio MC N=300 × 22-now+5y vs baseline:
#   22-now Worst DD: 72.0% → 68.4% (-3.6pp)
#   22-now Median compound: +25%
#   5y Mean compound: +1.40e24% → +5.27e24% (3.77×, +277%)
#   5y Median compound: +25%
#   P(coll) = 0% all cells.
WVD_WAVE_ENABLED       = os.environ.get('WVD_WAVE_ENABLED', '1') == '1'
WVD_WAVE_GATE_LO       = int(os.environ.get('WVD_WAVE_GATE_LO',       '70'))
WVD_WAVE_GATE_HI       = int(os.environ.get('WVD_WAVE_GATE_HI',       '85'))
WVD_WAVE_SCORE_POWER   = float(os.environ.get('WVD_WAVE_SCORE_POWER', '1.0'))
WVD_WAVE_PEAK          = float(os.environ.get('WVD_WAVE_PEAK',        '0.0'))
WVD_WAVE_WIDTH         = float(os.environ.get('WVD_WAVE_WIDTH',       '0.08'))
WVD_WAVE_K_LIFT        = float(os.environ.get('WVD_WAVE_K_LIFT',      '0.15'))
WVD_WAVE_TARGET_LIFT   = float(os.environ.get('WVD_WAVE_TARGET_LIFT', '82.0'))
WVD_WAVE_CLIMAX_THRESH = float(os.environ.get('WVD_WAVE_CLIMAX_THRESH', '0.05'))
WVD_WAVE_CLIMAX_SAT    = float(os.environ.get('WVD_WAVE_CLIMAX_SAT',  '0.15'))
WVD_WAVE_K_DAMPEN      = float(os.environ.get('WVD_WAVE_K_DAMPEN',    '0.40'))
WVD_WAVE_TARGET_DAMPEN = float(os.environ.get('WVD_WAVE_TARGET_DAMPEN', '55.0'))


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
    if overall < 28 and weekly_detail is not None:
        _wadj_value = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_value > -17.0:
            _wcf_weakness = max(0.0, min(1.0, (_wadj_value + 17.0) / 17.0))
            if _wcf_weakness > 0:
                _wcf_lift = 0.95 * _wcf_weakness * (50 - overall)
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
    if overall >= 75 and weekly_detail is not None:
        _wadj_c = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_c < 1.0:
            _cwcf_weakness = max(0.0, min(1.0, (1.0 - _wadj_c) / 1.0))
            if _cwcf_weakness > 0:
                _cwcf_dampen = 0.95 * _cwcf_weakness * (overall - 55)
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
    if 70 <= overall < 75 and weekly_detail is not None:
        _wadj_cwwd = float(weekly_detail.get('w_adj', 0.0))
        if _wadj_cwwd < 0.0:
            _stoch_grad_cwwd = max(0.0, min(1.0, (stoch - 25.0) / 35.0))
            _wadj_grad_cwwd  = max(0.0, min(1.0, -_wadj_cwwd / 5.0))
            _cwwd_weakness   = _stoch_grad_cwwd * _wadj_grad_cwwd
            if _cwwd_weakness > 0.0:
                _cwwd_dampen = 0.95 * _cwwd_weakness * (overall - 55)
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
    if overall >= 75 and weekly_detail is not None:
        _wadj_sw = float(weekly_detail.get('w_adj', 0.0))
        if 1.0 <= _wadj_sw < 14.0:
            # Gradient: 0 when stoch=35 (neutral), 1 when stoch=0 (maximally bearish)
            _stoch_w = max(0.0, (35.0 - stoch) / 35.0)
            # Gradient: 0 when wadj=14, 1 when wadj=1 (CWCF handles <1)
            _wadj_sw_w = max(0.0, min(1.0, (14.0 - _wadj_sw) / 13.0))
            _cswc_weakness = _stoch_w * _wadj_sw_w
            if _cswc_weakness > 0.0:
                _cswc_dampen = 0.50 * _cswc_weakness * (overall - 55)
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
            and 70 <= overall <= 74):
        _cont_lift = 75 - overall
        overall = 75

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
    if (16 <= overall <= 20
            and days_to_earnings is not None
            and 1 <= days_to_earnings <= 7):
        # Score gradient: peaks 1.0 at overall in [16,20], fades width=3
        # outside.  Strict-zone formulation for the in-bound range.
        if overall <= 17:
            _score_grad_pess = max(0.0, (overall - 13) / 3.0)  # 14:0.33, 15:0.67, 16:1, 17:1
            _score_grad_pess = min(1.0, _score_grad_pess)
        else:  # 18-20
            _score_grad_pess = max(0.0, (23 - overall) / 3.0)  # 18-20:1.0+, clip
            _score_grad_pess = min(1.0, _score_grad_pess)
        # Proximity: 1.0 at d=1-5, linear fade to 0 at d=8
        if days_to_earnings <= 5:
            _proximity_pess = 1.0
        else:
            _proximity_pess = max(0.0, (8 - days_to_earnings) / 3.0)
        _pess_weakness = _score_grad_pess * _proximity_pess
        if _pess_weakness > 0.0:
            _pess_lift = 0.95 * _pess_weakness * (28 - overall)
            overall = int(max(0, min(100, round(overall + _pess_lift))))

    # Earnings meta-score boost (Phase 3C ship 2026-04-28). Final transform.
    # Pulls (overall - 50) outward by a calibrated, log-smoothed multiplier
    # when signal is within EARN_BOOST_WINDOW trading days of upcoming earnings.
    # See compute_earnings_boost_strength() and module-level docs.
    _ern_boost_strength = compute_earnings_boost_strength(overall, days_to_earnings)
    if _ern_boost_strength > 0:
        boosted = 50 + (overall - 50) * (1.0 + _ern_boost_strength)
        overall = int(max(0, min(100, round(boosted))))

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

