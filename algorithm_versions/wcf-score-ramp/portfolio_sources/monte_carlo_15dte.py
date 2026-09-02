"""
Monte Carlo — 15 DTE Variant (Phase 15B C1 SHIPPED, parallel to monte_carlo.py)
================================================================================
Validated 2026-04-28 via Phase 15A + 15B sweeps. Top config (C1) cleanly clears
the 80% DD-C floor on every window AND beats 30 DTE H5 on 5y compound by 342×.

DTE-specific constants:
  HOLD_DAYS      : 15 -> 7    (15 DTE option, hard sell at half-DTE = bar 7)
  PREMIUM_MULT   : 1.82 -> 1.29  (15 DTE ATM premium per sigma_daily)
  HARD_SELL_LOSS : -0.40 -> -0.45  (theta scaling: 15 DTE day-7 ~ -46% empirical)

Phase 15B C1 SHIPPED config (DD-reduction levers):
  MAX_POSITIONS      : 8      (was 14 — concurrent exposure cap)
  F3F_PUT_FLOOR      : 0.40   (was 0.50 — stronger weakness contraction)
  F3F_CALL_FLOOR     : 0.40   (was 0.50 — stronger weakness contraction)

Derived sigma-multipliers (calls TP=0.35/SL=-0.30, puts TP=0.35/SL=-0.20):
  Calls TP -> 0.903 sigma underlying  (vs 1.274 sigma at 30 DTE)
  Calls SL -> 0.774 sigma             (vs 1.092 sigma)
  Puts  TP -> 0.903 sigma             (vs 1.274 sigma)
  Puts  SL -> 0.516 sigma             (vs 0.728 sigma)

Phase 15B validation (N=150, all 8 windows, vs 30 DTE H5):
  2021: +82.3k% (+518% vs 30DTE), DD-C 73.1% ✓
  2022: +76.8k% (+730%), DD-C 69.3% ✓
  2023: +46.2k% (+148%), DD-C 59.0% ✓
  2024: +1.14M% (+95%), DD-C 65.5% ✓
  2025: +4.76k% (-65%), DD-C 71.0% ✓
  dip:  +421%   (+11%), DD-C 72.0% ✓
  22-now: +204T% (+50× vs 30DTE), DD-C 69.3% ✓
  5y:    +140 quadrillion% (+342× vs 30DTE), DD-C 73.7% ✓

All Conservative DD-C ≤ 73.7% (clears 80% floor by 6.3pp). Zero collapse on every cell.
Single regression: 2025 (-65% vs 30DTE), accepted because 22-now / 5y compound
advantages dominate, and DD-C profile is structurally safer than 30 DTE.

Run: python monte_carlo_15dte.py
"""

import os
import sys
import io
import math
import random
import statistics
import multiprocessing
from collections import defaultdict
from datetime import date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import bisect

from database.models.core import Score, AlgorithmVersion, MarketBreadth, MarketRegime, EarningsDate
from database.models.technical import PriceHistory
from database.utils.trading_calendar import is_trading_day as _is_trading_day

# ---- Strategy constants ----------------------------------------------------
# Values flow from strategy_config.STRATEGY_15DTE (single source of truth).
# Module-level names persist for back-compat with code that does
# `from monte_carlo_15dte import HOLD_DAYS`. Sweeps mutate via env vars;
# the dataclass is frozen and never touched. See tests/test_strategy_config_drift.py.
import strategy_config as _sc
_cfg = _sc.STRATEGY_15DTE   # this module is the 15 DTE engine
_opt = _cfg.option

STARTING_CASH      = 50_000.0
N_ITER             = 500
# (overridden later by N_ITER_OVERRIDE env var if set)
VOL_LOOKBACK       = _cfg.VOL_LOOKBACK
HOLD_DAYS          = _cfg.HOLD_DAYS
PREMIUM_MULT       = _cfg.PREMIUM_MULT
DELTA              = _opt.DELTA

# ── Earnings-aware premium (variance-additive, shipped 2026-04-30) ──────────
# Signals that span an earnings event in the option's 15 calendar-day life
# price the entry premium with the variance-additive form:
#   premium_pct = N'(0) * sqrt(sigma^2 * DTE_cal * 252/365 + j^2)
# where j = EARN_JUMP_PCT (one-time earnings move magnitude, % of spot).
# Calibrated j=9.3 from `experiments/earnings_pricing_calibrate_15dte.py`
# (test RMSE 1.51pp vs prior 1.76pp on earnings-window cohort, bias near zero).
# Outside earnings windows the formula collapses to ~1.284*sigma -> matches
# the legacy 1.29 within rounding.
# tp_sigma / sl_sigma are also derived from the per-trade effective multiplier
# so the option-P&L semantics (TP=+30% on premium, SL=-20% on premium) hold.
# Spot-check 2026-04-30 vs real option_prices: var-additive Pareto-improves
# the EARN-IN cohort (RMSE -18%, bias +1.17pp -> -0.16pp) while staying within
# noise on every other cohort (NO-EARN, MID/HIGH IV, BULL/NEUTRAL regime).
DTE_CAL_15    = 15

# Dead-hold post-SL mechanism (Spec C, in flight 2026-04-30). When SL fires
# AND sampled-vega realized pnl ≤ trigger, override with pre-computed dead-hold
# walk (vega=1.0 approximation). See backtest_cascade.py and strategy_config.py.
DEAD_HOLD_ENABLED      = (os.environ.get('DEAD_HOLD_ENABLED', '1' if _cfg.DEAD_HOLD_ENABLED else '0') == '1')
DEAD_HOLD_TRIGGER_PNL  = float(os.environ.get('DEAD_HOLD_TRIGGER_PNL', _cfg.DEAD_HOLD_TRIGGER_PNL))
DEAD_HOLD_POPOUT_PNL   = float(os.environ.get('DEAD_HOLD_POPOUT_PNL',  _cfg.DEAD_HOLD_POPOUT_PNL))
_N_PRIME_0    = 1.0 / math.sqrt(2 * math.pi)   # 0.3989
_CAL_TO_TRADE = 252.0 / 365.0                   # 0.6904
EARN_JUMP_PCT = float(os.environ.get('EARN_JUMP_PCT', '9.3'))

def effective_premium_mult(sigma_pct: float, spans_earn: bool,
                           j_pct: float | None = None) -> float:
    """Per-trade effective premium multiplier.

    premium_pct_decimal = mult * (sigma_pct / 100)

    spans_earn=False -> returns PREMIUM_MULT (1.29).
    spans_earn=True  -> variance-additive form (richer premium reflects
                        the implied earnings-jump variance).
    `j_pct`: per-stock earnings-jump magnitude from earnings_jumps cache.
             None or <=0 falls back to universe EARN_JUMP_PCT (9.3).
    """
    if not spans_earn or sigma_pct <= 0:
        return PREMIUM_MULT
    j = j_pct if (j_pct is not None and j_pct > 0) else EARN_JUMP_PCT
    var_calm = (sigma_pct ** 2) * (DTE_CAL_15 * _CAL_TO_TRADE)
    var_jump = j ** 2
    return _N_PRIME_0 * math.sqrt(var_calm + var_jump) / sigma_pct


def _signal_spans_earnings(signal_date, ern_dates_for_sym) -> bool:
    """True if any earnings effective_date lies in (signal_date, signal_date + DTE_CAL_15 cal days].
    `ern_dates_for_sym` should be a sorted list of EFFECTIVE earnings dates
    (AMC-shifted via iv_crush_model.compute_effective_date).
    """
    if not ern_dates_for_sym:
        return False
    cutoff = signal_date + timedelta(days=DTE_CAL_15)
    for ed in ern_dates_for_sym:
        if signal_date < ed <= cutoff:
            return True
        if ed > cutoff:
            break
    return False

# Breadth-adaptive exits — option-side TP/SL come from SHARED_OPTION,
# which is aliased between 30 DTE and 15 DTE in strategy_config (both
# ship with TP=0.35 / SL=-0.30 on premium). HARD_SELL_LOSS is DTE-specific
# (-0.45 here, scaled for the 15 DTE day-7 hard sell).
# Env-var overrides allow Bayesian sweeps to vary these in MP workers (which
# re-import this module on spawn and would otherwise revert to strategy_config
# defaults). Same fix as monte_carlo.py — see experiments/v32_optim/.
TP_BASE            = float(os.environ.get('TP_BASE_OV',        _opt.TP_BASE))
TP_STRESS          = float(os.environ.get('TP_STRESS_OV',      _opt.TP_STRESS))
SL_BASE            = float(os.environ.get('SL_BASE_OV',        _opt.SL_BASE))
SL_STRESS          = float(os.environ.get('SL_STRESS_OV',      _opt.SL_STRESS))
HARD_SELL_LOSS     = float(os.environ.get('HARD_SELL_LOSS_OV', _cfg.HARD_SELL_LOSS))
BREADTH_THRESHOLD  = int(os.environ.get('BREADTH_THRESHOLD_OV', _opt.BREADTH_THRESHOLD))

SLIP_ENTRY = _opt.SLIP_ENTRY
SLIP_TP    = _opt.SLIP_TP
SLIP_SL    = _opt.SLIP_SL
SLIP_HARD  = _opt.SLIP_HARD

# Derived: net P&L after slippage. Recomputed from env-overridden TP/SL above
# (NOT from _opt directly) so sweeps that vary TP/SL get consistent net values
# in MP workers.
NET_TP_BASE   = TP_BASE   + _opt.SLIP_ENTRY + _opt.SLIP_TP
NET_TP_STRESS = TP_STRESS + _opt.SLIP_ENTRY + _opt.SLIP_TP
NET_SL_BASE   = SL_BASE   + _opt.SLIP_ENTRY + _opt.SLIP_SL
NET_SL_STRESS = SL_STRESS + _opt.SLIP_ENTRY + _opt.SLIP_SL
NET_HARD_SELL = HARD_SELL_LOSS + _opt.SLIP_ENTRY + _opt.SLIP_HARD

# Derived: underlying σ thresholds for TP/SL barrier-touch detection.
# 15 DTE PREMIUM_MULT=1.29. Recomputed from env-overridden TP/SL.
TP_SIGMA_BASE   = TP_BASE       * _cfg.PREMIUM_MULT / _opt.DELTA
TP_SIGMA_STRESS = TP_STRESS     * _cfg.PREMIUM_MULT / _opt.DELTA
SL_SIGMA_BASE   = abs(SL_BASE)   * _cfg.PREMIUM_MULT / _opt.DELTA
SL_SIGMA_STRESS = abs(SL_STRESS) * _cfg.PREMIUM_MULT / _opt.DELTA

# Put-side fixed parameters (no breadth switch by default).
PUT_TP            = float(os.environ.get('PUT_TP_OV', _opt.PUT_TP))
PUT_SL            = float(os.environ.get('PUT_SL_OV', _opt.PUT_SL))
PUT_NET_TP        = _opt.PUT_NET_TP
PUT_NET_SL        = _opt.PUT_NET_SL
PUT_TP_SIGMA      = abs(PUT_TP) * _cfg.PREMIUM_MULT / _opt.DELTA
PUT_SL_SIGMA      = abs(PUT_SL) * _cfg.PREMIUM_MULT / _opt.DELTA

# Put breadth-adaptive exits (disabled by default).
PUT_BREADTH_MODE       = _opt.PUT_BREADTH_MODE
PUT_BREADTH_THRESHOLD  = _opt.PUT_BREADTH_THRESHOLD
PUT_TP_STRESS          = _opt.PUT_TP_STRESS
PUT_SL_STRESS          = _opt.PUT_SL_STRESS

# Put SL hard-hold (Phase H1/H5: hold=0 ships).
PUT_SL_HOLD_BARS_DEFAULT = _opt.PUT_SL_HOLD_BARS_DEFAULT
PUT_SL_HOLD_BARS_MONDAY  = _opt.PUT_SL_HOLD_BARS_MONDAY

# Put SL period-stepped mode (research, env-gated 2026-04-27).
# Anchors stop-loss to MAE_winner per period — winners' adverse excursion grows
# with hold time, so a wider SL late in the hold cuts fewer winners.
# 'static' = use PUT_SL throughout (current production)
# 'stepped' = use PUT_SL_STEPPED bands  e.g., [(5,-0.08),(10,-0.13),(15,-0.18)]
#             active band = first whose max_bar >= current_bar.
# v27 5y MAE_winner sigma: 7d=-0.32, 15d=-0.39, 30d=-0.51 -> option SL @1x:
#             bar 1-5  -> ~-9%
#             bar 6-10 -> ~-11%
#             bar 11-15-> ~-14%
PUT_SL_MODE      = os.environ.get('PUT_SL_MODE', 'static')   # 'static' | 'stepped'
PUT_SL_STEPPED   = [(5, -0.09), (10, -0.11), (15, -0.14)]   # default principled MAE-anchored

# Put-vs-call signal priority (research, env-gated 2026-04-27).
# 'calls_first'   - production default: walk calls first, puts get residual slots
# 'puts_first'    - reverse: puts first, calls get residual
# 'merged'        - unify queue, sort by abs(score-50) (conviction), fill in conviction order
# 'wr_merged'     - unify queue, sort by per-tier empirical priority (assessment-driven, 2026-04-28).
#                   Requires WR_PRIORITY_TABLE to be populated by the experiment harness;
#                   each signal gets priority = WR_PRIORITY_TABLE[tier_key]. Higher = better.
#                   Falls back to WR_PRIORITY_FALLBACK for unknown tiers (e.g. overflow).
# Rationale: v28 PutTP rates now match or beat call tier WRs; raw abs(score-50) priority
# under-weights puts vs their empirical edge.
PUT_PRIORITY = os.environ.get('PUT_PRIORITY', 'calls_first')   # 'calls_first' | 'puts_first' | 'merged' | 'wr_merged'

# WR-merged priority table — populated by experiment harness (Phase Pri-1+).
# Maps tier_key (str) -> priority float. Tier keys are the same as TIER_ALLOC /
# PUT_TIER_ALLOC keys: 'ultra', 'top', 'mid', 'low', 'overflow' (calls);
# 'put_top', 'put_mid', 'put_low' (puts). Plus 'ct_call' / 'ct_put' override keys
# (CT-tagged signals always rank ahead via the (0 vs 1) sort prefix, but their
# value within the CT block uses these table entries when present).
WR_PRIORITY_TABLE: dict = {}
WR_PRIORITY_FALLBACK: float = 50.0

# Separate per-side MaxPos caps (None = share the global MAX_POSITIONS pool).
MAX_POSITIONS_CALL = None
MAX_POSITIONS_PUT  = None

# Cascade allocation per score tier (calls + puts). Copied from strategy_config
# into mutable module-level dicts so sweeps can edit them in-place.
# Env overrides for MP-worker sweeps — same pattern as monte_carlo.py.
def _env_alloc(name, default):
    v = os.environ.get(name, '')
    return float(v) if v else default

TIER_ALLOC = {
    'ultra':    _env_alloc('TIER_ULTRA_OV',    _cfg.TIER_ALLOC.get('ultra', 0.0)),
    'top':      _env_alloc('TIER_TOP_OV',      _cfg.TIER_ALLOC.get('top', 0.0)),
    'mid':      _env_alloc('TIER_MID_OV',      _cfg.TIER_ALLOC.get('mid', 0.0)),
    'low':      _env_alloc('TIER_LOW_OV',      _cfg.TIER_ALLOC.get('low', 0.0)),
    'overflow': _env_alloc('TIER_OVERFLOW_OV', _cfg.TIER_ALLOC.get('overflow', 0.0)),
}
PUT_TIER_ALLOC = {
    'put_top': _env_alloc('PUT_TIER_TOP_OV', _cfg.PUT_TIER_ALLOC.get('put_top', 0.0)),
    'put_mid': _env_alloc('PUT_TIER_MID_OV', _cfg.PUT_TIER_ALLOC.get('put_mid', 0.0)),
    'put_low': _env_alloc('PUT_TIER_LOW_OV', _cfg.PUT_TIER_ALLOC.get('put_low', 0.0)),
}
MAX_POSITIONS      = _cfg.MAX_POSITIONS    # 8 for 15 DTE (vs 14 for 30 DTE)
PRIMARY_THRESHOLD  = _cfg.PRIMARY_THRESHOLD
OVERFLOW_THRESHOLD = _cfg.OVERFLOW_THRESHOLD

# Regime-aware allocation (asymmetric CUT_ONLY shipped 2026-04-17).
REGIME_SLOPE          = _cfg.REGIME_SLOPE
REGIME_SLOPE_PUT      = _cfg.REGIME_SLOPE_PUT
ALLOC_SCALE_FLOOR     = _cfg.ALLOC_SCALE_FLOOR
ALLOC_SCALE_CEIL      = _cfg.ALLOC_SCALE_CEIL
REGIME_SLOPE_UP       = _cfg.REGIME_SLOPE_UP
REGIME_SLOPE_DOWN     = _cfg.REGIME_SLOPE_DOWN
REGIME_SLOPE_PUT_UP   = _cfg.REGIME_SLOPE_PUT_UP
REGIME_SLOPE_PUT_DOWN = _cfg.REGIME_SLOPE_PUT_DOWN

# Breadth-driven allocation knob (F3f). 15 DTE C1 uses tighter floors
# (0.40 vs 30 DTE 0.50) for stronger weak-tape contraction.
BREADTH_ALLOC_ENABLED = _cfg.BREADTH_ALLOC_ENABLED
F3F_CALL_THRESH       = _cfg.F3F_CALL_THRESH
F3F_CALL_FLOOR        = _cfg.F3F_CALL_FLOOR
F3F_CALL_LOW          = _cfg.F3F_CALL_LOW
F3F_PUT_THRESH        = _cfg.F3F_PUT_THRESH
F3F_PUT_FLOOR         = _cfg.F3F_PUT_FLOOR
F3F_PUT_HIGH          = _cfg.F3F_PUT_HIGH

# ------------------------------------------------------------------------
# SAW Put U-curve — sector ETF breadth-driven put alloc scaler.
# 30 DTE shipped Region B (mid=72/hw=18/floor=0.55/ceil=1.35/power=3.0).
# 15 DTE wired 2026-05-08 for calibration sweep — STRATEGY_15DTE default
# is _ENABLED=False so this is a no-op until the sweep ships winning config.
# Env-overridable for sweep workers (Windows MP-spawn quirk — module-globals
# don't propagate to workers, so sweep params must come via env at import).
# ------------------------------------------------------------------------
SAW_PUT_UCURVE_ENABLED   = int(os.environ.get('SAW_PUT_UCURVE_ENABLED', '1' if _cfg.SAW_PUT_UCURVE_ENABLED else '0'))
SAW_PUT_UCURVE_SHAPE     = os.environ.get('SAW_PUT_UCURVE_SHAPE',  _cfg.SAW_PUT_UCURVE_SHAPE)
SAW_PUT_UCURVE_MIDPOINT  = float(os.environ.get('SAW_PUT_UCURVE_MIDPOINT',  str(_cfg.SAW_PUT_UCURVE_MIDPOINT)))
SAW_PUT_UCURVE_HALFWIDTH = float(os.environ.get('SAW_PUT_UCURVE_HALFWIDTH', str(_cfg.SAW_PUT_UCURVE_HALFWIDTH)))
SAW_PUT_UCURVE_FLOOR     = float(os.environ.get('SAW_PUT_UCURVE_FLOOR',     str(_cfg.SAW_PUT_UCURVE_FLOOR)))
SAW_PUT_UCURVE_CEIL      = float(os.environ.get('SAW_PUT_UCURVE_CEIL',      str(_cfg.SAW_PUT_UCURVE_CEIL)))
SAW_PUT_UCURVE_POWER     = float(os.environ.get('SAW_PUT_UCURVE_POWER',     str(_cfg.SAW_PUT_UCURVE_POWER)))
SAW_PUT_UCURVE_K         = float(os.environ.get('SAW_PUT_UCURVE_K',         str(_cfg.SAW_PUT_UCURVE_K)))

PUT_THRESHOLD      = int(os.environ.get('PUT_THRESHOLD_OVERRIDE', str(_cfg.PUT_THRESHOLD)))
COLLAPSE_THRESHOLD = _cfg.COLLAPSE_THRESHOLD

# H3 — DD-soft band call alloc contraction. Disabled for 15 DTE by default
# (LO=HI=0, FLOOR=1.0) — not validated under bounded-fill MC for this strategy.
# Env-overridable for sweeps.
DD_SOFT_BAND_LO    = float(os.environ.get('DD_SOFT_BAND_LO', str(_cfg.DD_SOFT_BAND_LO)))
DD_SOFT_BAND_HI    = float(os.environ.get('DD_SOFT_BAND_HI', str(_cfg.DD_SOFT_BAND_HI)))
DD_SOFT_CALL_FLOOR = float(os.environ.get('DD_SOFT_CALL_FLOOR', str(_cfg.DD_SOFT_CALL_FLOOR)))

# Design D — asymmetric put-only score-stage flip (research, env-gated).
# When DESIGN_D_PUT_FLIP=1, put-side overall is recomputed from pre_regime
# using the flipped apply (drops the (2.0-mult) mirror, applies mult directly).
# Per-trade 5y diff-assess (experiments/design_d_put_flip.py): <25 WR15 +3.6pp,
# WR30 +4.5pp; ~60% fewer puts (concentration). Calls byte-identical.
DESIGN_D_PUT_FLIP  = os.environ.get('DESIGN_D_PUT_FLIP', '0') == '1'
N_ITER_OVERRIDE    = int(os.environ.get('N_ITER_OVERRIDE', '0'))
if N_ITER_OVERRIDE > 0:
    N_ITER = N_ITER_OVERRIDE
WINDOWS_OVERRIDE   = os.environ.get('WINDOWS_OVERRIDE', '')  # comma list e.g. '5y' or '2024,2025,5y'

# Earnings-window put suppression — SHIPPED 2026-04-26.
# Defaults from strategy_config; env overrides preserved for sweeps.
EARN_SUPP_PUT          = os.environ.get('EARN_SUPP_PUT', '1' if _cfg.EARN_SUPP_PUT else '0') == '1'
EARN_SUPP_PUT_DAYS     = int(os.environ.get('EARN_SUPP_PUT_DAYS', str(_cfg.EARN_SUPP_PUT_DAYS)))
EARN_SUPP_PUT_MIN_OV   = int(os.environ.get('EARN_SUPP_PUT_MIN_OV', str(_cfg.EARN_SUPP_PUT_MIN_OV)))
EARN_SUPP_PUT_MAX_OV   = int(os.environ.get('EARN_SUPP_PUT_MAX_OV', str(_cfg.EARN_SUPP_PUT_MAX_OV)))

# Regime-gated PUT_THRESHOLD tightening (research, env-gated).
# When PUT_TIGHTEN_BREADTH_LE is set (>0), drops put signals where
# overall > PUT_TIGHTEN_THRESH AND breadth_score on signal date <= the
# given threshold. Selectively concentrates puts to <=PUT_TIGHTEN_THRESH
# only during stressed-breadth tape, leaving normal-tape volume unchanged.
# Hypothesis: combines the live-observed "max put 20 in stress" anecdote
# with the put_concentration_sweep finding that D+PT20 wins +119% on
# 5-month dip but loses 5y compound. Untested 2026-04-27.
PUT_TIGHTEN_BREADTH_LE = int(os.environ.get('PUT_TIGHTEN_BREADTH_LE', '0'))  # 0 = disabled (drop when breadth <= LE)
PUT_TIGHTEN_BREADTH_GE = int(os.environ.get('PUT_TIGHTEN_BREADTH_GE', '0'))  # 0 = disabled (drop when breadth >= GE)
PUT_TIGHTEN_THRESH     = int(os.environ.get('PUT_TIGHTEN_THRESH', '20'))     # tighten to this when gate fires

# Weak-weekly put filter (research, env-gated 2026-04-27).
# Per-trade evidence (experiments/put_wadj_cross_buckets.py, 5y): puts with
# weight_info.w_adj > -13 (weak weekly drag) carry WR15 -8 to -15pp below
# strong-weekly siblings across every put bucket. The score=5 dip (66.9% WR
# vs neighbors 73-75%) is half-composed of weak-weekly REJECTION puts at
# 52% WR (N=75 / 5y). When WEAK_WEEKLY_PUT_DROP=1, drop puts where overall
# in [MIN_OV, MAX_OV] AND w_adj > WADJ_GT.
# Score-stage interpretation: weak weekly = thesis not confirmed across
# timeframes; if extreme score reached only via stress regime amplification,
# it's a stress-noise artifact rather than a confirmed bearish setup.
WEAK_WEEKLY_PUT_DROP   = os.environ.get('WEAK_WEEKLY_PUT_DROP', '0') == '1'
WEAK_WEEKLY_PUT_MIN_OV = int(os.environ.get('WEAK_WEEKLY_PUT_MIN_OV', '0'))
WEAK_WEEKLY_PUT_MAX_OV = int(os.environ.get('WEAK_WEEKLY_PUT_MAX_OV', '10'))
WEAK_WEEKLY_PUT_WADJ   = float(os.environ.get('WEAK_WEEKLY_PUT_WADJ', '-13.0'))
WEAK_WEEKLY_PUT_VSIG_REJ_ONLY = os.environ.get('WEAK_WEEKLY_PUT_VSIG_REJ_ONLY', '0') == '1'

# Weak-weekly CALL filter — disabled by default for 15 DTE (not validated).
# Schema parity with monte_carlo.py for drift-guard. Read from strategy_config.
WEAK_WEEKLY_CALL_DROP     = os.environ.get('WEAK_WEEKLY_CALL_DROP', '1' if _cfg.WEAK_WEEKLY_CALL_DROP else '0') == '1'
WEAK_WEEKLY_CALL_MIN_OV   = int(os.environ.get('WEAK_WEEKLY_CALL_MIN_OV', str(_cfg.WEAK_WEEKLY_CALL_MIN_OV)))
WEAK_WEEKLY_CALL_MAX_OV   = int(os.environ.get('WEAK_WEEKLY_CALL_MAX_OV', str(_cfg.WEAK_WEEKLY_CALL_MAX_OV)))
WEAK_WEEKLY_CALL_WADJ     = float(os.environ.get('WEAK_WEEKLY_CALL_WADJ', str(_cfg.WEAK_WEEKLY_CALL_WADJ_LT)))
WEAK_WEEKLY_CALL_STOCH_GE = int(os.environ.get('WEAK_WEEKLY_CALL_STOCH_GE', str(_cfg.WEAK_WEEKLY_CALL_STOCH_GE)))

# Earnings-window call PROMOTION (research, env-gated).
# Per-trade A/B (experiments/earn_call_pretest.py, 5y) shows calls firing within
# 3 trd days BEFORE earnings have +10..+16pp WR15 uplift over the population:
#   75-79 PRE-ern: 88.3% WR15 (N=265) vs other 72.2%  -> +16.1pp
#   80-84 PRE-ern: 85.5% WR15 (N=62)  vs other 71.8%  -> +13.7pp
#   70-74 PRE-ern: 80.8% WR15 (N=992) vs other 70.8%  -> +10.0pp (overflow disabled)
# Knob promotes signals in [min_ov, max_ov] to the named tier when earnings
# falls in (D, D + days trading days].
EARN_BOOST_CALL          = os.environ.get('EARN_BOOST_CALL', '0') == '1'
EARN_BOOST_CALL_DAYS     = int(os.environ.get('EARN_BOOST_CALL_DAYS', '3'))
EARN_BOOST_CALL_MIN_OV   = int(os.environ.get('EARN_BOOST_CALL_MIN_OV', '75'))
EARN_BOOST_CALL_MAX_OV   = int(os.environ.get('EARN_BOOST_CALL_MAX_OV', '79'))
EARN_BOOST_CALL_TIER     = os.environ.get('EARN_BOOST_CALL_TIER', 'top')   # 'ultra'=25%, 'top'=15%, 'mid'=15%, 'low'=15%

# Counter-trend (CT) cascade promotion — Path B follow-up to v22 revert.
# Tag put signals with TREND>=70 (counter-trend reversal) and call signals
# with TREND<=30 (counter-trend bounce). Promote tagged signals to the
# highest-conviction tier and fill them ahead of the score-sorted queue.
# Source: experiments/x_conf_counter_trend.py — CT-PUT bucket WR15=81.5%
# (n=232, 2y), +14.8pp over put baseline. Preserves score distribution
# (no recalculate, no version bump).
CT_PROMOTE         = os.environ.get('CT_PROMOTE', '1') == '1'
CT_PUT_TREND_MIN   = int(os.environ.get('CT_PUT_TREND_MIN', '80'))
CT_CALL_TREND_MAX  = int(os.environ.get('CT_CALL_TREND_MAX', '20'))  # set to -1 to disable ct_call
CT_CALL_TIER       = os.environ.get('CT_CALL_TIER', 'ultra')         # ultra=25%, top=15%, mid=15%, low=15%
CT_PUT_TIER        = os.environ.get('CT_PUT_TIER', 'put_top')        # put_top=15%, put_mid=12%, put_low=12%


def ct_tag(overall, trend, side):
    """Return 'ct_call' / 'ct_put' / None for a signal at (overall, trend)."""
    if not CT_PROMOTE or trend is None:
        return None
    if side == 'put' and overall <= PUT_THRESHOLD and trend >= CT_PUT_TREND_MIN:
        return 'ct_put'
    if side == 'call' and overall >= OVERFLOW_THRESHOLD and trend <= CT_CALL_TREND_MAX:
        return 'ct_call'
    return None

WINDOWS = [
    ('2018',       date(2018, 1, 1),  date(2018, 12, 31)),   # parity w/ monte_carlo.py (honest-v70 10y)
    ('2020',       date(2020, 1, 1),  date(2020, 12, 31)),   # COVID crash + V-recovery
    ('2020_crash', date(2020, 2, 1),  date(2020, 4, 30)),    # sharp COVID drawdown (collapse-binding)
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
    ('10y',    date(2016, 6, 1),  date(2026, 4, 15)),        # full honest-v70 history incl crashes
]

COLLISION_MODES = ['seeded']  # option-pricing-aware MC uses single seeded mode (variance from random fill + sampled vega)


def score_to_tier(score):
    if score >= 95: return 'ultra'
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def put_score_to_tier(score):
    if score <= 15: return 'put_top'
    if score <= 20: return 'put_mid'
    return 'put_low'  # 21-25


# ---- Data loading -----------------------------------------------------------

def load_breadth_map(d_start, d_end):
    """Return (sorted_dates, {date: breadth_score}) — most-recent-on-or-before lookup."""
    rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(
            MarketBreadth.date >= d_start - timedelta(days=60),
            MarketBreadth.date <= d_end,
            MarketBreadth.breadth_score.is_null(False),
        )
        .order_by(MarketBreadth.date)
        .tuples()
    )
    m = {d: float(bs) for d, bs in rows}
    return sorted(m.keys()), m


def load_regime_map(d_start, d_end):
    """Return (sorted_dates, {date: alloc_scalar}) — most-recent-on-or-before lookup.

    When BREADTH_ALLOC_ENABLED (default), returns breadth_score per date
    (F3f knob). Otherwise returns regime_multiplier per date (legacy).
    The function name is preserved for call-site compatibility; the value
    semantics are determined by BREADTH_ALLOC_ENABLED at the alloc_scale_for
    consumer.
    """
    if BREADTH_ALLOC_ENABLED:
        rows = list(
            MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
            .where(
                MarketBreadth.date >= d_start - timedelta(days=60),
                MarketBreadth.date <= d_end,
                MarketBreadth.breadth_score.is_null(False),
            )
            .order_by(MarketBreadth.date)
            .tuples()
        )
        m = {d: float(brd) for d, brd in rows}
    else:
        rows = list(
            MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier)
            .where(
                MarketRegime.date >= d_start - timedelta(days=60),
                MarketRegime.date <= d_end,
                MarketRegime.regime_multiplier.is_null(False),
            )
            .order_by(MarketRegime.date)
            .tuples()
        )
        m = {d: float(mult) for d, mult in rows}
    return sorted(m.keys()), m


def regime_on_or_before(sorted_dates, rmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx >= 0:
        return rmap[sorted_dates[idx]]
    # No coverage: neutral default. For breadth that's 50.0 (no scaling);
    # for regime_mult that's 1.0 (no scaling). Both produce alloc_scale=1.0.
    return 50.0 if BREADTH_ALLOC_ENABLED else 1.0


def _breadth_alloc_scale(breadth, is_put):
    """F3f curve: breadth -> alloc scale. See F3F_* constants for shape."""
    if breadth is None:
        return 1.0
    if is_put:
        if breadth <= F3F_PUT_THRESH:
            return 1.0
        if breadth >= F3F_PUT_HIGH:
            return F3F_PUT_FLOOR
        return 1.0 - (breadth - F3F_PUT_THRESH) / (F3F_PUT_HIGH - F3F_PUT_THRESH) * (1.0 - F3F_PUT_FLOOR)
    else:
        if breadth >= F3F_CALL_THRESH:
            return 1.0
        if breadth <= F3F_CALL_LOW:
            return F3F_CALL_FLOOR
        return F3F_CALL_FLOOR + (breadth - F3F_CALL_LOW) / (F3F_CALL_THRESH - F3F_CALL_LOW) * (1.0 - F3F_CALL_FLOOR)


# ------------------------------------------------------------------------
# SAW Put U-curve helpers (mirrors monte_carlo.py:589-643).
# Both engines read the same .cache/sector_etf_screen/sector_breadth_daily_2020plus.csv.
# ------------------------------------------------------------------------
_SAW_SEC_BRD_MAP   = None
_SAW_SEC_BRD_DATES = None

def _saw_load_sec_brd():
    global _SAW_SEC_BRD_MAP, _SAW_SEC_BRD_DATES
    if _SAW_SEC_BRD_MAP is not None:
        return
    pq_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '.cache', 'sector_etf_screen',
                           'sector_breadth_daily_2020plus.csv')
    m = {}
    if os.path.exists(pq_path):
        import csv as _csv
        from datetime import date as _d
        with open(pq_path, 'r') as f:
            rdr = _csv.DictReader(f)
            for row in rdr:
                try:
                    m[_d.fromisoformat(row['date'])] = float(row['sec_brd_ema50'])
                except (KeyError, ValueError):
                    continue
    _SAW_SEC_BRD_MAP = m
    _SAW_SEC_BRD_DATES = sorted(m.keys())

def saw_sec_brd_on_or_before(d):
    _saw_load_sec_brd()
    if not _SAW_SEC_BRD_DATES:
        return 50.0
    idx = bisect.bisect_right(_SAW_SEC_BRD_DATES, d) - 1
    if idx >= 0:
        return _SAW_SEC_BRD_MAP[_SAW_SEC_BRD_DATES[idx]]
    return 50.0

def saw_put_ucurve_scale(d):
    """U-curve scale at signal date d. Returns 1.0 when disabled."""
    if not SAW_PUT_UCURVE_ENABLED:
        return 1.0
    brd = saw_sec_brd_on_or_before(d)
    floor = SAW_PUT_UCURVE_FLOOR
    ceil  = SAW_PUT_UCURVE_CEIL
    if SAW_PUT_UCURVE_SHAPE == 'sigmoid':
        lo_thresh = SAW_PUT_UCURVE_MIDPOINT - SAW_PUT_UCURVE_HALFWIDTH
        hi_thresh = SAW_PUT_UCURVE_MIDPOINT + SAW_PUT_UCURVE_HALFWIDTH
        import math as _m
        try:
            sig_lo = 1.0 / (1.0 + _m.exp(-(lo_thresh - brd) / max(0.5, SAW_PUT_UCURVE_K)))
        except OverflowError:
            sig_lo = 0.0 if (lo_thresh - brd) < 0 else 1.0
        try:
            sig_hi = 1.0 / (1.0 + _m.exp(-(brd - hi_thresh) / max(0.5, SAW_PUT_UCURVE_K)))
        except OverflowError:
            sig_hi = 0.0 if (brd - hi_thresh) < 0 else 1.0
        activation = min(1.0, sig_lo + sig_hi)
        return floor + activation * (ceil - floor)
    else:
        d_norm = abs(brd - SAW_PUT_UCURVE_MIDPOINT) / max(1.0, SAW_PUT_UCURVE_HALFWIDTH)
        if d_norm > 1.0: d_norm = 1.0
        return floor + (d_norm ** SAW_PUT_UCURVE_POWER) * (ceil - floor)


def alloc_scale_for(value, is_put=False):
    """Return [floor..ceil] clamped scale factor.

    When BREADTH_ALLOC_ENABLED (default), `value` is a breadth_score and the
    F3f curves apply. Otherwise `value` is a regime_multiplier and the legacy
    asymmetric REGIME_SLOPE_* logic applies.
    """
    if BREADTH_ALLOC_ENABLED:
        s = _breadth_alloc_scale(value, is_put)
        if s < ALLOC_SCALE_FLOOR: return ALLOC_SCALE_FLOOR
        if s > ALLOC_SCALE_CEIL:  return ALLOC_SCALE_CEIL
        return s

    # Legacy regime_multiplier path
    delta = value - 1.0
    if is_put:
        if delta >= 0 and REGIME_SLOPE_PUT_UP is not None:
            slope = REGIME_SLOPE_PUT_UP
        elif delta < 0 and REGIME_SLOPE_PUT_DOWN is not None:
            slope = REGIME_SLOPE_PUT_DOWN
        else:
            slope = REGIME_SLOPE_PUT if REGIME_SLOPE_PUT is not None else REGIME_SLOPE
    else:
        if delta >= 0 and REGIME_SLOPE_UP is not None:
            slope = REGIME_SLOPE_UP
        elif delta < 0 and REGIME_SLOPE_DOWN is not None:
            slope = REGIME_SLOPE_DOWN
        else:
            slope = REGIME_SLOPE
    if slope == 0.0:
        return 1.0
    s = 1.0 + slope * delta
    if s < ALLOC_SCALE_FLOOR: return ALLOC_SCALE_FLOOR
    if s > ALLOC_SCALE_CEIL:  return ALLOC_SCALE_CEIL
    return s


def breadth_on_or_before(sorted_dates, bmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    return bmap[sorted_dates[idx]] if idx >= 0 else None


def is_stressed(sorted_dates, bmap, d):
    b = breadth_on_or_before(sorted_dates, bmap, d)
    return b is not None and b <= BREADTH_THRESHOLD


def load_signals(version, d_start, d_end):
    """Call signals: overall >= OVERFLOW_THRESHOLD (70).

    Includes weight_info + stoch so portfolio-stage filters
    (WEAK_WEEKLY_CALL_DROP) can read wadj/stoch without a second DB pass.
    """
    return list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                     Score.weight_info, Score.stoch)
        .where(
            Score.version == version,
            Score.date >= d_start,
            Score.date <= d_end,
            Score.overall >= OVERFLOW_THRESHOLD,
        )
        .order_by(Score.date, Score.overall.desc())
    )


def load_put_signals(version, d_start, d_end):
    """Put signals: overall <= PUT_THRESHOLD (25).

    When DESIGN_D_PUT_FLIP=1, broaden the pre-filter (load <= 35 + recover
    pre_regime via weight_info), apply the flipped put-side formula
    `adj = 50 + (pre_regime - 50) * mult` (no `2.0 - mult` mirror), and
    re-filter to <= PUT_THRESHOLD.
    """
    if not DESIGN_D_PUT_FLIP:
        # Always include weight_info + volume_signal so portfolio-stage filters
        # (WEAK_WEEKLY_PUT_DROP) can read w_adj/vsig without a second DB pass.
        return list(
            Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                         Score.weight_info, Score.volume_signal)
            .where(
                Score.version == version,
                Score.date >= d_start,
                Score.date <= d_end,
                Score.overall <= PUT_THRESHOLD,
            )
            .order_by(Score.date, Score.overall.asc())
        )

    # Design D path — broaden filter, recover pre_regime, re-apply flipped, re-filter.
    import json as _json
    rows = list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                     Score.regime_multiplier, Score.weight_info)
        .where(
            Score.version == version,
            Score.date >= d_start,
            Score.date <= d_end,
            Score.overall <= 40,  # broaden — D can lift some up out of <=25 and pull others down in
        )
    )
    flipped = []
    for s in rows:
        ov = int(s.overall)
        pre = None
        if s.weight_info:
            try:
                wi = _json.loads(s.weight_info) if isinstance(s.weight_info, str) else s.weight_info
                if 'pre_regime' in wi:
                    pre = int(wi['pre_regime'])
            except Exception:
                pre = None
        mult = float(s.regime_multiplier) if s.regime_multiplier is not None else 1.0
        if pre is None:
            # Recover pre_regime from overall via inverse of B_current
            if mult == 1.0:
                pre = ov
            else:
                if ov >= 50:
                    pre = round(50 + (ov - 50) / mult)
                else:
                    pre = round(50 + (ov - 50) / (2.0 - mult))
        pre = max(0, min(100, pre))
        if pre >= 50:
            continue   # call path; D doesn't apply here, and it's not a put anyway
        # Design D apply on put path: drop the (2.0 - mult) mirror
        if mult == 1.0:
            new_ov = pre
        else:
            new_ov = int(max(0, min(100, round(50 + (pre - 50) * mult))))
        if new_ov > PUT_THRESHOLD:
            continue
        # Mutate the score's overall in-place; keep symbol/date/trend
        s.overall = new_ov
        flipped.append(s)
    flipped.sort(key=lambda s: (s.date, s.overall))
    return flipped


def _earnings_suppress_filter(signals, d_start, d_end, days, min_ov, max_ov, side):
    """Drop signals where any earnings_date falls in (signal_date, signal_date + days trading days].

    side: 'put' filters scores with overall in [min_ov, max_ov]; 'call' similar.
    Returns (kept, dropped_count).
    """
    syms = {s.symbol_id for s in signals}
    if not syms:
        return signals, 0
    ed_rows = list(EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
                   .where(EarningsDate.symbol.in_(list(syms)),
                          EarningsDate.date >= d_start - timedelta(days=10),
                          EarningsDate.date <= d_end + timedelta(days=days*2 + 7))
                   .order_by(EarningsDate.symbol, EarningsDate.date)
                   .tuples())
    ed_map = defaultdict(list)
    for sym, d in ed_rows:
        ed_map[sym].append(d)

    def _fwd_n(d, n):
        out = d
        while n > 0:
            out += timedelta(days=1)
            if _is_trading_day(out):
                n -= 1
        return out

    kept, dropped = [], 0
    for s in signals:
        ov = int(s.overall)
        # bucket gating
        if not (min_ov <= ov <= max_ov):
            kept.append(s); continue
        sym_ed = ed_map.get(s.symbol_id, [])
        if not sym_ed:
            kept.append(s); continue
        # earnings in (signal_date, signal_date + days trading days]?
        win_end = _fwd_n(s.date, days)
        if any(s.date < ed <= win_end for ed in sym_ed):
            dropped += 1
            continue
        kept.append(s)
    return kept, dropped


def load_price_history(sym_ids, d_start, d_end):
    ph_start = d_start - timedelta(days=120)
    ph_end   = d_end   + timedelta(days=30)
    rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.close, PriceHistory.high, PriceHistory.low
        )
        .where(
            PriceHistory.symbol.in_(sym_ids),
            PriceHistory.date >= ph_start,
            PriceHistory.date <= ph_end,
        )
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .tuples()
    )
    ph = defaultdict(list)
    for sym_id, d, c, h, l in rows:
        ph[sym_id].append((d, float(c), float(h), float(l)))
    return ph


def realized_vol(closes, base_idx, lookback=VOL_LOOKBACK):
    if base_idx < lookback:
        return None
    rets = []
    for j in range(base_idx - lookback + 1, base_idx + 1):
        prev = closes[j - 1]
        if prev > 0:
            rets.append((closes[j] - prev) / prev)
    if len(rets) < lookback // 2:
        return None
    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


# ---- Per-trade outcome (collision-aware) ------------------------------------

def compute_trade_outcome(sym_bars, signal_date, stressed, spans_earn=False, j_pct=None):
    """
    Returns dict with keys:
      kind     : 'tp' | 'sl' | 'hard' | 'both'
      exit_bar : int (trading bars from signal_date)
      side     : 'call'
      stressed : bool (for slippage band selection at resolve-time)
      premium_pct, vol, entry, signal_date
      fire_open, fire_close, fire_high, fire_low : OHLC of trigger bar (for
                 random-fill resolution in resolve()). For 'hard' kind, these
                 are the day-7 close (no underlying-move sampling).
      fire_tp_level, fire_sl_level : trigger barrier prices (for bounded-fill
                 sampling in resolve())

    NOTE: trigger detection still uses σ-barriers on the underlying (cheap).
    The realized P&L on fire is computed in resolve() via
    `option_pricing.option_pnl_pct` (total_dte=15) with theta + sampled vega
    applied at random fill, not the static SL/TP barrier values.

    `spans_earn`: True when an earnings event falls in the option's 15-cal-day
    life. Bumps the entry premium (and σ-barriers derived from it) via the
    variance-additive formula with per-stock `j_pct` from earnings_jumps cache.
    """
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    opens  = [b[4] for b in sym_bars] if sym_bars and len(sym_bars[0]) >= 5 else closes

    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None

    entry_price = closes[base_idx]
    if entry_price <= 0:
        return None

    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    # Per-trade effective premium multiplier (variance-additive when spans_earn).
    prem_mult = effective_premium_mult(vol, spans_earn, j_pct)
    tp_pct = TP_STRESS if stressed else TP_BASE
    sl_pct = SL_STRESS if stressed else SL_BASE
    tp_sigma = tp_pct      * prem_mult / DELTA
    sl_sigma = abs(sl_pct) * prem_mult / DELTA

    premium_pct = prem_mult * vol / 100
    tp_level    = entry_price * (1 + tp_sigma * vol / 100)
    sl_level    = entry_price * (1 - sl_sigma * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_level
        sl_hit = lows[i]  <= sl_level
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', i - base_idx
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            break
        if tp_hit:
            kind, exit_bar = 'tp', i - base_idx
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            break
        if sl_hit:
            kind, exit_bar = 'sl', i - base_idx
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            break

    if kind == 'hard':
        last_idx = base_idx + HOLD_DAYS
        if last_idx >= len(dates):
            last_idx = len(dates) - 1
        fire_o = opens[last_idx]; fire_c = closes[last_idx]
        fire_h = highs[last_idx]; fire_l = lows[last_idx]

    out = dict(kind=kind, exit_bar=exit_bar, side='call', stressed=stressed,
               premium_pct=premium_pct, vol=vol, entry=entry_price,
               signal_date=signal_date,
               fire_open=fire_o, fire_close=fire_c,
               fire_high=fire_h, fire_low=fire_l,
               fire_tp_level=tp_level, fire_sl_level=sl_level)

    if kind == 'sl' and DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        out['dead_hold'] = _compute_dh_call_15(
            highs, lows, closes, opens, sl_fire_idx, end_idx,
            entry_price, premium_pct, base_idx)
    return out


def _compute_dh_call_15(highs, lows, closes, opens, fire_idx, end_idx,
                         entry, premium_pct, base_idx):
    """15 DTE call-side dead-hold walk under vega=1.0."""
    from option_pricing import option_pnl_pct
    for k in range(fire_idx + 1, end_idx):
        bars_k = k - base_idx
        high_k = highs[k]; open_k = opens[k]
        high_pnl = option_pnl_pct('call', high_k, entry, bars_k, premium_pct,
                                  total_dte=DTE_CAL_15, vega_ratio=1.0, delta=DELTA)
        if high_pnl >= DEAD_HOLD_POPOUT_PNL:
            open_pnl = option_pnl_pct('call', open_k, entry, bars_k, premium_pct,
                                      total_dte=DTE_CAL_15, vega_ratio=1.0, delta=DELTA)
            return ('dh_pop', bars_k, max(DEAD_HOLD_POPOUT_PNL, open_pnl))
    last_k = end_idx - 1
    if last_k <= fire_idx:
        return ('dh_open', fire_idx - base_idx, -1.0)
    bars_last = last_k - base_idx
    last_pnl = option_pnl_pct('call', closes[last_k], entry, bars_last, premium_pct,
                              total_dte=DTE_CAL_15, vega_ratio=1.0, delta=DELTA)
    return ('dh_expiry', bars_last, last_pnl)


def _compute_dh_put_15(highs, lows, closes, opens, fire_idx, end_idx,
                        entry, premium_pct, base_idx):
    """15 DTE put-side dead-hold walk under vega=1.0."""
    from option_pricing import option_pnl_pct
    for k in range(fire_idx + 1, end_idx):
        bars_k = k - base_idx
        low_k = lows[k]; open_k = opens[k]
        low_pnl = option_pnl_pct('put', low_k, entry, bars_k, premium_pct,
                                 total_dte=DTE_CAL_15, vega_ratio=1.0, delta=DELTA)
        if low_pnl >= DEAD_HOLD_POPOUT_PNL:
            open_pnl = option_pnl_pct('put', open_k, entry, bars_k, premium_pct,
                                      total_dte=DTE_CAL_15, vega_ratio=1.0, delta=DELTA)
            return ('dh_pop', bars_k, max(DEAD_HOLD_POPOUT_PNL, open_pnl))
    last_k = end_idx - 1
    if last_k <= fire_idx:
        return ('dh_open', fire_idx - base_idx, -1.0)
    bars_last = last_k - base_idx
    last_pnl = option_pnl_pct('put', closes[last_k], entry, bars_last, premium_pct,
                              total_dte=DTE_CAL_15, vega_ratio=1.0, delta=DELTA)
    return ('dh_expiry', bars_last, last_pnl)


def _put_sl_hold_bars(signal_date):
    """Return number of trading bars to suppress SL check after entry.
    Monday entries get 4 bars (same calendar-day coverage as Tue-Fri 3-bar hold).
    """
    return PUT_SL_HOLD_BARS_MONDAY if signal_date.weekday() == 0 else PUT_SL_HOLD_BARS_DEFAULT


def compute_put_outcome(sym_bars, signal_date, put_stressed=False, spans_earn=False, j_pct=None):
    """Put trade: win = underlying falls; stop = rises. Breadth-adaptive when put_stressed=True.

    Trigger uses σ-barriers on underlying high/low. Realized P&L is computed
    in resolve() via option_pricing.option_pnl_pct (total_dte=15) with theta +
    sampled vega applied at random fill within the trigger bar (not static
    SL/TP barrier values).

    `spans_earn`: True when earnings spans the option's life — bumps entry
    premium and σ-barriers via the variance-additive formula with per-stock
    j_pct from earnings_jumps cache.
    """
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    opens  = [b[4] for b in sym_bars] if sym_bars and len(sym_bars[0]) >= 5 else closes

    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    entry_price = closes[base_idx]
    if entry_price <= 0:
        return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    if put_stressed and PUT_BREADTH_MODE != 'none':
        tp_pct = PUT_TP_STRESS
        sl_pct = PUT_SL_STRESS
    else:
        tp_pct = PUT_TP
        sl_pct = PUT_SL
    prem_mult = effective_premium_mult(vol, spans_earn, j_pct)
    tp_sigma  = tp_pct * prem_mult / DELTA
    tp_level  = entry_price * (1 - tp_sigma * vol / 100)

    # SL schedule: list of (max_bar_inclusive, sl_pct).
    if PUT_SL_MODE == 'stepped':
        sl_schedule = list(PUT_SL_STEPPED)
    else:
        sl_schedule = [(HOLD_DAYS, sl_pct)]

    def _sl_for_bar(b):
        for max_b, p in sl_schedule:
            if b <= max_b:
                return p
        return sl_schedule[-1][1]

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    sl_hold = _put_sl_hold_bars(signal_date)
    premium_pct = prem_mult * vol / 100
    kind = 'hard'; exit_bar = HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    fire_sl_level = None
    for i in range(base_idx + 1, end_idx):
        bar    = i - base_idx          # 1-indexed
        sl_pct_t   = _sl_for_bar(bar)
        sl_sigma_t = abs(sl_pct_t) * prem_mult / DELTA
        sl_level_t = entry_price * (1 + sl_sigma_t * vol / 100)
        tp_hit = lows[i]  <= tp_level
        sl_hit = (highs[i] >= sl_level_t) and (bar > sl_hold)
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if tp_hit:
            kind, exit_bar = 'tp', bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if sl_hit:
            kind, exit_bar = 'sl', bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break

    if kind == 'hard':
        last_idx = base_idx + HOLD_DAYS
        if last_idx >= len(dates):
            last_idx = len(dates) - 1
        fire_o = opens[last_idx]; fire_c = closes[last_idx]
        fire_h = highs[last_idx]; fire_l = lows[last_idx]
        last_bar_idx = last_idx - base_idx
        fire_sl_level = entry_price * (1 + abs(_sl_for_bar(last_bar_idx)) * prem_mult / DELTA * vol / 100)

    out = dict(kind=kind, exit_bar=exit_bar, side='put', stressed=put_stressed,
               premium_pct=premium_pct, vol=vol, entry=entry_price,
               signal_date=signal_date,
               fire_open=fire_o, fire_close=fire_c,
               fire_high=fire_h, fire_low=fire_l,
               fire_tp_level=tp_level, fire_sl_level=fire_sl_level)
    if kind == 'sl' and DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        out['dead_hold'] = _compute_dh_put_15(
            highs, lows, closes, opens, sl_fire_idx, end_idx,
            entry_price, premium_pct, base_idx)
    return out


def _is_put_stressed(sorted_dates, bmap, d):
    """Returns True if the put-breadth switch should fire on date d."""
    if PUT_BREADTH_MODE == 'none':
        return False
    b = breadth_on_or_before(sorted_dates, bmap, d)
    if b is None:
        return False
    if PUT_BREADTH_MODE == 'invert':
        return b >= PUT_BREADTH_THRESHOLD    # strong breadth = headwind for puts
    if PUT_BREADTH_MODE == 'same':
        return b <= PUT_BREADTH_THRESHOLD    # same logic as calls
    return False


def _attach_earnings_span(outcome, ern_for_sym, trading_days):
    """Set outcome['spans_earnings']=True if any earnings event in
    (signal_date, exit_date]. Used by resolve() to decide whether to sample
    a vega ratio at fire time. Mirrors monte_carlo.py semantics with the same
    iv_crush_model helpers but at total_dte=15.
    """
    outcome['spans_earnings'] = False
    if not ern_for_sym or not trading_days:
        return outcome
    try:
        from iv_crush_model import estimate_exit_date, find_spanning_earnings
    except ImportError:
        return outcome
    exit_d = estimate_exit_date(outcome['signal_date'],
                                outcome.get('exit_bar', HOLD_DAYS),
                                trading_days)
    spans = find_spanning_earnings(outcome['signal_date'], exit_d, ern_for_sym)
    outcome['spans_earnings'] = spans is not None
    return outcome


def precompute_outcomes(signals, ph, breadth_dates, breadth_map,
                        ern_map=None, j_map=None, trading_days=None):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        stressed = is_stressed(breadth_dates, breadth_map, sig.date)
        ern_for_sym = ern_map.get(sig.symbol_id) if ern_map else None
        spans = _signal_spans_earnings(sig.date, ern_for_sym)
        j_pct = j_map.get(sig.symbol_id) if j_map else None
        r = compute_trade_outcome(sym_bars, sig.date, stressed, spans_earn=spans, j_pct=j_pct)
        if r is not None:
            _attach_earnings_span(r, ern_for_sym, trading_days)
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def precompute_put_outcomes(signals, ph, breadth_dates=None, breadth_map=None,
                            ern_map=None, j_map=None, trading_days=None):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        put_stressed = False
        if PUT_BREADTH_MODE != 'none' and breadth_dates is not None and breadth_map is not None:
            put_stressed = _is_put_stressed(breadth_dates, breadth_map, sig.date)
        ern_for_sym = ern_map.get(sig.symbol_id) if ern_map else None
        spans = _signal_spans_earnings(sig.date, ern_for_sym)
        j_pct = j_map.get(sig.symbol_id) if j_map else None
        r = compute_put_outcome(sym_bars, sig.date, put_stressed=put_stressed,
                                spans_earn=spans, j_pct=j_pct)
        if r is not None:
            _attach_earnings_span(r, ern_for_sym, trading_days)
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


# Vega sample pool — loaded lazily on first access in resolve(). The pool itself
# lives in option_pricing._VEGA_CACHE (shared with monte_carlo.py); calling
# `_load_samples()` once at process start primes it before any rng.randrange().
_VEGA_INIT = False

def _ensure_vega_pool_loaded():
    global _VEGA_INIT
    if _VEGA_INIT:
        return
    try:
        from iv_crush_model import _load_samples
        _load_samples()
    except Exception:
        pass
    _VEGA_INIT = True


def resolve(outcome, rng):
    """Return ('tp' | 'sl' | 'hard', net_option_pnl) for 15 DTE option-aware MC.

    Random-fill semantics, bounded by which barrier actually triggered (mirrors
    monte_carlo.py / commit 3432fb8 — limit-or-better for TP, stop-or-worse for SL):

      kind='tp', call : fill ~ Uniform(tp_level, high)         limit-or-better
      kind='tp', put  : fill ~ Uniform(low, tp_level)          limit-or-better
      kind='sl', call : conditional bimodal — see monte_carlo.resolve()
      kind='sl', put  : conditional bimodal — see monte_carlo.resolve()
      kind='both'     : fill ~ Uniform(low, high)              path ambiguous
      kind='hard'     : fill = day-7 close                     deterministic

    SL bimodal (calibrated 2026-04-30): intraday trigger fills at sl_level;
    gap-through samples Uniform within the (low, open) gap region for calls
    or (open, high) for puts. Replaces uniform [low, sl_level] which the
    sl_fill_bias_audit showed was systematically pessimistic by ~+0.2 of
    range vs real bars.

    Realized option P&L computed via option_pricing.option_pnl_pct with
    total_dte=15 (theta scales harder than 30 DTE — bar 7 ≈ -27%, bar 14 ≈ -73%).
    """
    from option_pricing import option_pnl_pct, sample_vega_ratio

    kind = outcome['kind']
    side = outcome.get('side', 'call')
    spans = outcome.get('spans_earnings', False)
    bars_held = outcome.get('exit_bar', HOLD_DAYS)
    entry = outcome['entry']
    premium_pct = outcome['premium_pct']

    vega_ratio = 1.0
    if spans:
        _ensure_vega_pool_loaded()
        side_name = 'CALL' if side == 'call' else 'PUT'
        vega_ratio = sample_vega_ratio(side_name, dte=DTE_CAL_15, rng=rng)

    # Hard exit: forced day-HOLD_DAYS close, deterministic.
    if kind == 'hard':
        u_fire = outcome['fire_close']
        pnl = option_pnl_pct(side, u_fire, entry, bars_held, premium_pct,
                             total_dte=DTE_CAL_15, vega_ratio=vega_ratio,
                             delta=DELTA)
        return 'hard', pnl + SLIP_ENTRY + SLIP_HARD

    lo = outcome['fire_low']
    hi = outcome['fire_high']
    op = outcome.get('fire_open')
    tp_lvl = outcome.get('fire_tp_level')
    sl_lvl = outcome.get('fire_sl_level')

    # Pick fill range bounded by trigger semantics
    if kind == 'tp':
        if side == 'call':
            u_lo, u_hi = tp_lvl, hi    # at or above TP barrier (limit-or-better)
        else:                          # put
            u_lo, u_hi = lo, tp_lvl    # at or below TP barrier
        slip = SLIP_TP
    elif kind == 'sl':
        # Bimodal: intraday trigger at sl_level; gap-through samples gap region.
        if side == 'call':
            if op is not None and op > sl_lvl:
                u_lo = u_hi = sl_lvl
            else:
                gap_open = op if op is not None else sl_lvl
                u_lo, u_hi = lo, gap_open
        else:                          # put
            if op is not None and op < sl_lvl:
                u_lo = u_hi = sl_lvl
            else:
                gap_open = op if op is not None else sl_lvl
                u_lo, u_hi = gap_open, hi
        slip = SLIP_SL
    else:                              # 'both' — path ambiguous
        u_lo, u_hi = lo, hi
        slip = None                    # set after computing P&L sign

    if u_hi < u_lo:
        u_lo, u_hi = u_hi, u_lo
    u_fire = u_lo + rng.random() * (u_hi - u_lo) if u_hi > u_lo else u_lo

    pnl = option_pnl_pct(side, u_fire, entry, bars_held, premium_pct,
                         total_dte=DTE_CAL_15, vega_ratio=vega_ratio,
                         delta=DELTA)

    # Dead-hold override (see monte_carlo.py for design rationale).
    if (DEAD_HOLD_ENABLED and kind == 'sl'
            and pnl <= DEAD_HOLD_TRIGGER_PNL
            and 'dead_hold' in outcome):
        dh_kind, dh_bars, dh_pnl = outcome['dead_hold']
        # Asymmetric cost: popout = limit fill (no exit spread); expiry/open are
        # forced day-7 closes (liquidity taker) and pay the hard-sell spread.
        dh_exit_slip = 0.0 if dh_kind == 'dh_pop' else SLIP_HARD
        return dh_kind, dh_pnl + SLIP_ENTRY + dh_exit_slip

    if kind == 'both':
        # Slippage band depends on which side of entry the fill landed.
        if side == 'call':
            slip = SLIP_TP if u_fire >= entry else SLIP_SL
        else:
            slip = SLIP_TP if u_fire <= entry else SLIP_SL
        return 'tp' if pnl > 0 else 'sl', pnl + SLIP_ENTRY + slip

    return kind, pnl + SLIP_ENTRY + slip


# ---- Portfolio simulation ---------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl', 'outcome', 'side']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome, side):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome
        self.side         = side  # 'call' or 'put'


def run_single_sim(trading_days, calls_by_date, call_outcomes,
                   puts_by_date, put_outcomes, mode, rng,
                   regime_dates=None, regime_map=None):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c  = sl_c  = hard_c  = 0
    tp_p  = sl_p  = hard_p  = 0

    cap_call = MAX_POSITIONS_CALL if MAX_POSITIONS_CALL is not None else MAX_POSITIONS
    cap_put  = MAX_POSITIONS_PUT  if MAX_POSITIONS_PUT  is not None else MAX_POSITIONS
    side_capped = (MAX_POSITIONS_CALL is not None) or (MAX_POSITIONS_PUT is not None)

    for day_idx, today in enumerate(trading_days):
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.premium_cost * (1 + p.option_pnl)
                if p.side == 'call':
                    if   p.outcome == 'tp':   tp_c   += 1
                    elif p.outcome == 'sl':   sl_c   += 1
                    else:                     hard_c += 1
                else:
                    if   p.outcome == 'tp':   tp_p   += 1
                    elif p.outcome == 'sl':   sl_p   += 1
                    else:                     hard_p += 1
            else:
                keep.append(p)
        positions = keep

        portfolio_value = cash + sum(p.premium_cost for p in positions)
        if portfolio_value > peak_value:
            peak_value = portfolio_value
        dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if portfolio_value <= STARTING_CASH * COLLAPSE_THRESHOLD:
            break

        open_syms = {p.sym_id for p in positions}

        call_open = sum(1 for p in positions if p.side == 'call')
        put_open  = sum(1 for p in positions if p.side == 'put')

        reg_mult = regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
        reg_scale_c = alloc_scale_for(reg_mult, is_put=False)
        reg_scale_p = alloc_scale_for(reg_mult, is_put=True)

        def _try_fill_call(sym_id, score, key, ct, ern):
            """Attempt to open a call position. Returns True if filled."""
            nonlocal cash, call_open
            if len(positions) >= MAX_POSITIONS:
                return False
            if side_capped and call_open >= cap_call:
                return False
            if ct == 'ct_call':
                tier = CT_CALL_TIER
            elif ern and EARN_BOOST_CALL:
                tier = EARN_BOOST_CALL_TIER
            else:
                tier = score_to_tier(score)
            # H3: DD-soft-band call alloc contraction (disabled by default for 15 DTE)
            dd_scale = 1.0
            if DD_SOFT_BAND_HI > DD_SOFT_BAND_LO and dd > DD_SOFT_BAND_LO:
                if dd >= DD_SOFT_BAND_HI:
                    dd_scale = DD_SOFT_CALL_FLOOR
                else:
                    t = (dd - DD_SOFT_BAND_LO) / (DD_SOFT_BAND_HI - DD_SOFT_BAND_LO)
                    dd_scale = 1.0 - t * (1.0 - DD_SOFT_CALL_FLOOR)
            alloc_frac   = TIER_ALLOC[tier] * reg_scale_c * dd_scale
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                return False
            o = call_outcomes[key]
            outcome, pnl = resolve(o, rng)
            cash -= premium_cost
            positions.append(Position(sym_id, today, o['exit_bar'],
                                       premium_cost, pnl, outcome, 'call'))
            open_syms.add(sym_id)
            call_open += 1
            return True

        def _try_fill_put(sym_id, score, key, ct):
            """Attempt to open a put position. Returns True if filled."""
            nonlocal cash, put_open
            if len(positions) >= MAX_POSITIONS:
                return False
            if side_capped and put_open >= cap_put:
                return False
            tier = CT_PUT_TIER if ct == 'ct_put' else put_score_to_tier(score)
            alloc_frac   = PUT_TIER_ALLOC[tier] * reg_scale_p * saw_put_ucurve_scale(today)
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                return False
            o = put_outcomes[key]
            outcome, pnl = resolve(o, rng)
            cash -= premium_cost
            positions.append(Position(sym_id, today, o['exit_bar'],
                                       premium_cost, pnl, outcome, 'put'))
            open_syms.add(sym_id)
            put_open += 1
            return True

        def _do_calls():
            day_calls = calls_by_date.get(today, [])
            if not day_calls:
                return
            eligible = [(sid, sc, k, ct, ern) for sid, sc, k, ct, ern in day_calls
                        if k in call_outcomes and sid not in open_syms]
            primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD or e[3] == 'ct_call']
            overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD and e[3] != 'ct_call']
            primary.sort(key=lambda x: (0 if x[3] == 'ct_call' else 1, -x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            for sym_id, score, key, ct, ern in primary + overflow:
                if len(positions) >= MAX_POSITIONS:
                    break
                if side_capped and call_open >= cap_call:
                    break
                _try_fill_call(sym_id, score, key, ct, ern)

        def _do_puts():
            day_puts = puts_by_date.get(today, [])
            if not day_puts:
                return
            pe = [(sid, sc, k, ct) for sid, sc, k, ct in day_puts
                  if k in put_outcomes and sid not in open_syms]
            pe.sort(key=lambda x: (0 if x[3] == 'ct_put' else 1, x[1], rng.random()))
            for sym_id, score, key, ct in pe:
                if len(positions) >= MAX_POSITIONS:
                    break
                if side_capped and put_open >= cap_put:
                    break
                _try_fill_put(sym_id, score, key, ct)

        if PUT_PRIORITY == 'merged':
            # Unified queue: sort all signals by abs(score-50) (conviction).
            # CT-tagged signals always fill first regardless of raw conviction.
            day_calls = calls_by_date.get(today, [])
            day_puts  = puts_by_date.get(today, [])
            merged = []
            for sid, sc, k, ct, ern in day_calls:
                if k in call_outcomes and sid not in open_syms:
                    is_ct = ct == 'ct_call'
                    merged.append((0 if is_ct else 1, -abs(sc - 50), 'call', sid, sc, k, ct, ern))
            for sid, sc, k, ct in day_puts:
                if k in put_outcomes and sid not in open_syms:
                    is_ct = ct == 'ct_put'
                    merged.append((0 if is_ct else 1, -abs(sc - 50), 'put',  sid, sc, k, ct, None))
            merged.sort(key=lambda x: (x[0], x[1], rng.random()))
            for _ct_pri, _conv, side, sym_id, score, key, ct, ern in merged:
                if len(positions) >= MAX_POSITIONS:
                    break
                if side == 'call':
                    if sym_id in open_syms:    # may have been filled this loop on the put side
                        continue
                    _try_fill_call(sym_id, score, key, ct, ern)
                else:
                    if sym_id in open_syms:
                        continue
                    _try_fill_put(sym_id, score, key, ct)
        elif PUT_PRIORITY == 'wr_merged':
            # Unified queue: sort all signals by per-tier empirical priority
            # from WR_PRIORITY_TABLE (assessment-driven). Higher table value =
            # earlier in queue. CT-tagged signals still take their CT_*_TIER's
            # priority; if absent in the table, fall back to WR_PRIORITY_FALLBACK.
            day_calls = calls_by_date.get(today, [])
            day_puts  = puts_by_date.get(today, [])
            tbl = WR_PRIORITY_TABLE
            fb  = WR_PRIORITY_FALLBACK
            merged = []
            for sid, sc, k, ct, ern in day_calls:
                if k in call_outcomes and sid not in open_syms:
                    if ct == 'ct_call':
                        tier = CT_CALL_TIER
                    elif ern and EARN_BOOST_CALL:
                        tier = EARN_BOOST_CALL_TIER
                    else:
                        tier = score_to_tier(sc)
                    pri = tbl.get(tier, fb)
                    merged.append((-pri, 'call', sid, sc, k, ct, ern))
            for sid, sc, k, ct in day_puts:
                if k in put_outcomes and sid not in open_syms:
                    tier = CT_PUT_TIER if ct == 'ct_put' else put_score_to_tier(sc)
                    pri = tbl.get(tier, fb)
                    merged.append((-pri, 'put',  sid, sc, k, ct, None))
            merged.sort(key=lambda x: (x[0], rng.random()))
            for _pri, side, sym_id, score, key, ct, ern in merged:
                if len(positions) >= MAX_POSITIONS:
                    break
                if sym_id in open_syms:
                    continue
                if side == 'call':
                    _try_fill_call(sym_id, score, key, ct, ern)
                else:
                    _try_fill_put(sym_id, score, key, ct)
        elif PUT_PRIORITY == 'puts_first':
            _do_puts()
            _do_calls()
        else:  # 'calls_first' — production default
            _do_calls()
            _do_puts()

    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else:                hard_p += 1
    portfolio_value = cash

    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    ct = tp_c + sl_c + hard_c or 1
    pt = tp_p + sl_p + hard_p or 1
    return dict(
        final = portfolio_value,
        max_dd = max_dd,
        call_tp = tp_c / ct * 100, call_sl = sl_c / ct * 100, call_hard = hard_c / ct * 100,
        put_tp  = tp_p / pt * 100, put_sl  = sl_p / pt * 100, put_hard  = hard_p / pt * 100,
        call_trades = tp_c + sl_c + hard_c,
        put_trades  = tp_p + sl_p + hard_p,
    )


# ---- Multiprocessing worker (must be at module scope for pickling) ----------
#
# Heavy data (calls_by_date, call_outcomes, puts_by_date, put_outcomes,
# trading_days, regime_dates, regime_map) is pickled ONCE per worker via
# `initializer`. Per-task args are tiny (just mode + seed). This avoids
# re-pickling the large dicts on every iteration.

_MP_STATE = {}


def _mc_init_worker(trading_days, calls_by_date, call_outcomes,
                    puts_by_date, put_outcomes, regime_dates, regime_map):
    """Pool initializer — runs once per worker process."""
    _MP_STATE['trading_days']  = trading_days
    _MP_STATE['calls_by_date'] = calls_by_date
    _MP_STATE['call_outcomes'] = call_outcomes
    _MP_STATE['puts_by_date']  = puts_by_date
    _MP_STATE['put_outcomes']  = put_outcomes
    _MP_STATE['regime_dates']  = regime_dates
    _MP_STATE['regime_map']    = regime_map


def _mc_iter_worker(args):
    """Run one MC iteration. Receives (mode, seed) — pulls heavy data from worker globals."""
    mode, seed = args
    rng = random.Random(seed)
    return run_single_sim(
        _MP_STATE['trading_days'],  _MP_STATE['calls_by_date'], _MP_STATE['call_outcomes'],
        _MP_STATE['puts_by_date'],  _MP_STATE['put_outcomes'],  mode, rng,
        _MP_STATE['regime_dates'],  _MP_STATE['regime_map']
    )


# ---- Window runner ----------------------------------------------------------

def run_window(label, d_start, d_end, version):
    print(f"\n{'='*110}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print('='*110)

    call_sigs = load_signals(version, d_start, d_end)
    put_sigs  = load_put_signals(version, d_start, d_end)
    primary_n  = sum(1 for s in call_sigs if s.overall >= PRIMARY_THRESHOLD)
    overflow_n = len(call_sigs) - primary_n
    print(f"Call signals: {len(call_sigs)}  (75+={primary_n}, 70-74={overflow_n})  |  Put signals (<=25): {len(put_sigs)}")

    if WEAK_WEEKLY_CALL_DROP and call_sigs:
        import json as _json_c
        n_before_c = len(call_sigs)
        kept_c = []
        dropped_c = 0
        for s in call_sigs:
            ov = int(s.overall)
            if not (WEAK_WEEKLY_CALL_MIN_OV <= ov <= WEAK_WEEKLY_CALL_MAX_OV):
                kept_c.append(s); continue
            wadj = None
            wi_raw = getattr(s, 'weight_info', None)
            if wi_raw:
                try:
                    wi = _json_c.loads(wi_raw) if isinstance(wi_raw, str) else wi_raw
                    wa = wi.get('w_adj')
                    if wa is None: wa = wi.get('weekly_adj')
                    if wa is not None: wadj = float(wa)
                except Exception:
                    wadj = None
            if wadj is None or wadj >= WEAK_WEEKLY_CALL_WADJ:
                kept_c.append(s); continue
            if WEAK_WEEKLY_CALL_STOCH_GE > 0:
                stoch_v = getattr(s, 'stoch', None)
                if stoch_v is None or int(stoch_v) < WEAK_WEEKLY_CALL_STOCH_GE:
                    kept_c.append(s); continue
            dropped_c += 1
        call_sigs = kept_c
        stoch_str = f" AND stoch>={WEAK_WEEKLY_CALL_STOCH_GE}" if WEAK_WEEKLY_CALL_STOCH_GE > 0 else ""
        print(f"  WEAK_WEEKLY_CALL_DROP=ON: dropped {dropped_c}/{n_before_c} calls "
              f"(overall in [{WEAK_WEEKLY_CALL_MIN_OV},{WEAK_WEEKLY_CALL_MAX_OV}] AND w_adj<{WEAK_WEEKLY_CALL_WADJ}{stoch_str}); "
              f"remaining calls: {len(call_sigs)}")

    if EARN_SUPP_PUT:
        put_sigs, dropped = _earnings_suppress_filter(
            put_sigs, d_start, d_end,
            EARN_SUPP_PUT_DAYS, EARN_SUPP_PUT_MIN_OV, EARN_SUPP_PUT_MAX_OV, side='put')
        print(f"  EARN_SUPP_PUT=ON: dropped {dropped} puts (overall in [{EARN_SUPP_PUT_MIN_OV},{EARN_SUPP_PUT_MAX_OV}], earnings <= {EARN_SUPP_PUT_DAYS} trd days post-signal); remaining puts: {len(put_sigs)}")

    if WEAK_WEEKLY_PUT_DROP:
        import json as _json
        n_before = len(put_sigs)
        kept = []
        dropped_n = 0
        for s in put_sigs:
            ov = int(s.overall)
            if not (WEAK_WEEKLY_PUT_MIN_OV <= ov <= WEAK_WEEKLY_PUT_MAX_OV):
                kept.append(s)
                continue
            wadj = None
            wi_raw = getattr(s, 'weight_info', None)
            if wi_raw:
                try:
                    wi = _json.loads(wi_raw) if isinstance(wi_raw, str) else wi_raw
                    wa = wi.get('w_adj')
                    if wa is None: wa = wi.get('weekly_adj')
                    if wa is not None:
                        wadj = float(wa)
                except Exception:
                    wadj = None
            if wadj is None or wadj <= WEAK_WEEKLY_PUT_WADJ:
                # strong (or unknown) weekly drag -> keep
                kept.append(s)
                continue
            if WEAK_WEEKLY_PUT_VSIG_REJ_ONLY:
                vsig = getattr(s, 'volume_signal', None)
                if vsig != 'REJECTION':
                    kept.append(s)
                    continue
            dropped_n += 1
        put_sigs = kept
        rej_str = " AND vsig=REJECTION" if WEAK_WEEKLY_PUT_VSIG_REJ_ONLY else ""
        print(f"  WEAK_WEEKLY_PUT_DROP=ON: dropped {dropped_n}/{n_before} puts "
              f"(overall in [{WEAK_WEEKLY_PUT_MIN_OV},{WEAK_WEEKLY_PUT_MAX_OV}] AND w_adj > {WEAK_WEEKLY_PUT_WADJ}{rej_str}); "
              f"remaining puts: {len(put_sigs)}")

    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph      = load_price_history(sym_ids, d_start, d_end)

    breadth_dates, breadth_map = load_breadth_map(d_start, d_end)
    if call_sigs and breadth_dates:
        n_str = sum(1 for s in call_sigs if is_stressed(breadth_dates, breadth_map, s.date))
        print(f"Breadth map: {len(breadth_map)} dates  |  stressed call signals: {n_str/len(call_sigs)*100:.1f}%")

    if (PUT_TIGHTEN_BREADTH_LE > 0 or PUT_TIGHTEN_BREADTH_GE > 0) and put_sigs and breadth_dates:
        n_before = len(put_sigs)
        kept = []
        dropped_n = 0
        for s in put_sigs:
            ov = int(s.overall)
            if ov <= PUT_TIGHTEN_THRESH:
                kept.append(s)
                continue
            brd = breadth_on_or_before(breadth_dates, breadth_map, s.date)
            if brd is None:
                kept.append(s)
                continue
            drop = False
            if PUT_TIGHTEN_BREADTH_LE > 0 and brd <= PUT_TIGHTEN_BREADTH_LE:
                drop = True
            if PUT_TIGHTEN_BREADTH_GE > 0 and brd >= PUT_TIGHTEN_BREADTH_GE:
                drop = True
            if drop:
                dropped_n += 1
            else:
                kept.append(s)
        put_sigs = kept
        gate_str = []
        if PUT_TIGHTEN_BREADTH_LE > 0: gate_str.append(f"brd<={PUT_TIGHTEN_BREADTH_LE}")
        if PUT_TIGHTEN_BREADTH_GE > 0: gate_str.append(f"brd>={PUT_TIGHTEN_BREADTH_GE}")
        print(f"  PUT_TIGHTEN: dropped {dropped_n}/{n_before} puts (overall in ({PUT_TIGHTEN_THRESH},{PUT_THRESHOLD}] AND ({' OR '.join(gate_str)})); remaining puts: {len(put_sigs)}")

    regime_dates, regime_map = load_regime_map(d_start, d_end)
    if BREADTH_ALLOC_ENABLED:
        print(f"Alloc map (F3f breadth): {len(regime_map)} dates  |  "
              f"call: 1.0 if brd>=  {F3F_CALL_THRESH:.0f} else linear to {F3F_CALL_FLOOR:.2f} at {F3F_CALL_LOW:.0f}  |  "
              f"put: 1.0 if brd<={F3F_PUT_THRESH:.0f} else linear to {F3F_PUT_FLOOR:.2f} at {F3F_PUT_HIGH:.0f}")
    else:
        active_slope = REGIME_SLOPE_UP is not None or REGIME_SLOPE_DOWN is not None or REGIME_SLOPE != 0.0
        if active_slope or REGIME_SLOPE_PUT not in (None, 0.0):
            if REGIME_SLOPE_UP is not None or REGIME_SLOPE_DOWN is not None:
                print(f"Regime map: {len(regime_map)} dates  |  slope_up={REGIME_SLOPE_UP} slope_dn={REGIME_SLOPE_DOWN} put_slope={REGIME_SLOPE_PUT}")
            else:
                print(f"Regime map: {len(regime_map)} dates  |  slope={REGIME_SLOPE} put_slope={REGIME_SLOPE_PUT}")

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    # Earnings-window tag for calls (when EARN_BOOST_CALL=1).
    ern_call_lookup = set()  # set of (sym_id, signal_date) that are PRE-earnings within window
    if EARN_BOOST_CALL:
        cal_syms = {s.symbol_id for s in call_sigs}
        ern_rows = list(EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
                        .where(EarningsDate.symbol.in_(list(cal_syms)),
                               EarningsDate.date >= d_start - timedelta(days=10),
                               EarningsDate.date <= d_end + timedelta(days=EARN_BOOST_CALL_DAYS*2 + 7))
                        .order_by(EarningsDate.symbol, EarningsDate.date)
                        .tuples())
        ern_by_sym = defaultdict(list)
        for sym, d in ern_rows:
            ern_by_sym[sym].append(d)

        def _fwd_n(d, n):
            out = d
            while n > 0:
                out += timedelta(days=1)
                if _is_trading_day(out):
                    n -= 1
            return out

        for sig in call_sigs:
            ov = int(sig.overall)
            if not (EARN_BOOST_CALL_MIN_OV <= ov <= EARN_BOOST_CALL_MAX_OV):
                continue
            sym_ed = ern_by_sym.get(sig.symbol_id, [])
            if not sym_ed:
                continue
            win_end = _fwd_n(sig.date, EARN_BOOST_CALL_DAYS)
            if any(sig.date < ed <= win_end for ed in sym_ed):
                ern_call_lookup.add((sig.symbol_id, sig.date))

    calls_by_date = defaultdict(list)
    ct_call_n = 0
    ern_boost_n = 0
    for sig in call_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = ct_tag(sig.overall, sig.trend, 'call')
        if ct: ct_call_n += 1
        ern = (sig.symbol_id, sig.date) in ern_call_lookup
        if ern: ern_boost_n += 1
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct, ern))
    if EARN_BOOST_CALL:
        print(f"  EARN_BOOST_CALL=ON: tagged {ern_boost_n} calls in [{EARN_BOOST_CALL_MIN_OV},{EARN_BOOST_CALL_MAX_OV}] within {EARN_BOOST_CALL_DAYS} trd days BEFORE earnings -> tier='{EARN_BOOST_CALL_TIER}' (alloc={TIER_ALLOC[EARN_BOOST_CALL_TIER]:.0%})")
    puts_by_date = defaultdict(list)
    ct_put_n = 0
    for sig in put_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = ct_tag(sig.overall, sig.trend, 'put')
        if ct: ct_put_n += 1
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct))
    if CT_PROMOTE:
        print(f"CT-tagged signals: ct_call={ct_call_n} (call overall>={OVERFLOW_THRESHOLD} & trend<={CT_CALL_TREND_MAX})  "
              f"ct_put={ct_put_n} (put overall<={PUT_THRESHOLD} & trend>={CT_PUT_TREND_MIN})")

    # Earnings-effective-date map for variance-additive premium pricing.
    ern_map_for_premium = None
    j_map_for_premium = None
    all_syms = {s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs}
    if all_syms:
        ern_rows_p = list(EarningsDate.select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                          .where(EarningsDate.symbol.in_(list(all_syms)),
                                 EarningsDate.date >= d_start - timedelta(days=DTE_CAL_15 + 5),
                                 EarningsDate.date <= d_end + timedelta(days=DTE_CAL_15 + 7))
                          .order_by(EarningsDate.symbol, EarningsDate.date)
                          .tuples())
        from iv_crush_model import compute_effective_date as _eff_date
        ern_map_for_premium = defaultdict(list)
        for sym, d, ct in ern_rows_p:
            ern_map_for_premium[sym].append(_eff_date(d, ct, trading_days))
        for sym in list(ern_map_for_premium.keys()):
            ern_map_for_premium[sym] = sorted(set(ern_map_for_premium[sym]))
        # Per-stock earnings-jump magnitudes from earnings_jumps cache. Symbols
        # with <3 cached events fall back to universe EARN_JUMP_PCT (handled
        # in effective_premium_mult).
        from database.earnings_jump_cache import load_per_stock_jumps
        j_map_for_premium = load_per_stock_jumps(all_syms)
        n_call_span = sum(1 for s in call_sigs if _signal_spans_earnings(
            s.date, ern_map_for_premium.get(s.symbol_id)))
        n_put_span = sum(1 for s in put_sigs if _signal_spans_earnings(
            s.date, ern_map_for_premium.get(s.symbol_id)))
        n_per_stock = len(j_map_for_premium)
        n_fallback = len(all_syms) - n_per_stock
        if j_map_for_premium:
            j_vals = sorted(j_map_for_premium.values())
            j_p50 = j_vals[len(j_vals) // 2]
        else:
            j_p50 = EARN_JUMP_PCT
        print(f"  Earnings-aware premium  per-stock j: {n_per_stock} syms (median {j_p50:.2f}%) "
              f"+ {n_fallback} fallback @ {EARN_JUMP_PCT:.2f}%")
        print(f"  earnings-span: {n_call_span}/{len(call_sigs)} calls, "
              f"{n_put_span}/{len(put_sigs)} puts -> variance-additive")

    print("Precomputing call outcomes...", end=' ', flush=True)
    call_outcomes = precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map,
                                        ern_map=ern_map_for_premium,
                                        j_map=j_map_for_premium,
                                        trading_days=trading_days)
    both_n = sum(1 for o in call_outcomes.values() if o['kind'] == 'both')
    tp_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'tp')
    sl_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'sl')
    hard_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'hard')
    ct = len(call_outcomes) or 1
    print(f"N={len(call_outcomes)}  TP={tp_only/ct*100:.1f}%  SL={sl_only/ct*100:.1f}%  "
          f"Both={both_n/ct*100:.1f}%  Hard={hard_only/ct*100:.1f}%")

    print("Precomputing put outcomes... ", end=' ', flush=True)
    put_outcomes = precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map,
                                           ern_map=ern_map_for_premium,
                                           j_map=j_map_for_premium,
                                           trading_days=trading_days)
    pt_tp = sum(1 for o in put_outcomes.values() if o['kind'] == 'tp')
    pt_sl = sum(1 for o in put_outcomes.values() if o['kind'] == 'sl')
    pt_both = sum(1 for o in put_outcomes.values() if o['kind'] == 'both')
    pt_hard = sum(1 for o in put_outcomes.values() if o['kind'] == 'hard')
    pt = len(put_outcomes) or 1
    print(f"N={len(put_outcomes)}  TP={pt_tp/pt*100:.1f}%  SL={pt_sl/pt*100:.1f}%  "
          f"Both={pt_both/pt*100:.1f}%  Hard={pt_hard/pt*100:.1f}%")

    print(f"\n{'Mode':<13}  {'CTP%':>5}  {'PTP%':>5}  {'CTrd':>6}  {'PTrd':>6}  "
          f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  {'P(col)':>7}")
    print('-'*120)

    # Multiprocessing across iterations: ~8x speedup on 8-core CPU.
    # Each iteration is independent (different RNG, different collision
    # realizations); per-iteration cost is interpreter-bound Python so
    # GIL-aware multiprocessing wins.
    USE_MP = os.environ.get('MC_NO_MP', '0') != '1'
    n_workers = int(os.environ.get('MC_WORKERS', '0')) or os.cpu_count() or 4

    # Pool created once across all 3 collision modes — workers stay alive,
    # heavy state (outcomes dicts) pickled once via initializer.
    pool = None
    if USE_MP and N_ITER >= 16:
        pool = multiprocessing.Pool(
            processes=min(n_workers, N_ITER),
            initializer=_mc_init_worker,
            initargs=(trading_days, calls_by_date, call_outcomes,
                      puts_by_date, put_outcomes, regime_dates, regime_map),
        )

    results = {}
    for mode in COLLISION_MODES:
        finals=[]; dds=[]; ctps=[]; ptps=[]; ctrd=[]; ptrd=[]; collapses=0
        seeds = [1000 * hash(label) + it for it in range(N_ITER)]
        if pool is not None:
            args_list = [(mode, s) for s in seeds]
            rs = pool.map(_mc_iter_worker, args_list, chunksize=max(1, N_ITER // (n_workers * 4)))
        else:
            rs = []
            for s in seeds:
                rng = random.Random(s)
                rs.append(run_single_sim(trading_days, calls_by_date, call_outcomes,
                                         puts_by_date, put_outcomes, mode, rng,
                                         regime_dates, regime_map))
        for r in rs:
            finals.append(r['final']); dds.append(r['max_dd'])
            ctps.append(r['call_tp']); ptps.append(r['put_tp'])
            ctrd.append(r['call_trades']); ptrd.append(r['put_trades'])
            if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                collapses += 1

        mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
        med_ret  = (statistics.median(finals) / STARTING_CASH - 1) * 100
        mean_dd  = statistics.mean(dds) * 100
        worst_dd = max(dds) * 100
        p_coll   = collapses / N_ITER * 100

        finals_sorted = sorted(finals)
        def _pct15(seq, q):
            if not seq:
                return None
            i = max(0, min(len(seq) - 1, int(round(q * (len(seq) - 1)))))
            return seq[i]
        p25_final = _pct15(finals_sorted, 0.25)
        p75_final = _pct15(finals_sorted, 0.75)
        p25_ret_pct = (p25_final / STARTING_CASH - 1) * 100 if p25_final is not None else None
        p75_ret_pct = (p75_final / STARTING_CASH - 1) * 100 if p75_final is not None else None
        med_dd_pct  = statistics.median(dds) * 100 if dds else None

        results[mode] = dict(
            mean_ret=mean_ret, med_ret=med_ret,
            mean_dd=mean_dd, worst_dd=worst_dd, p_coll=p_coll,
            call_tp=statistics.mean(ctps), put_tp=statistics.mean(ptps),
            call_trades=statistics.mean(ctrd), put_trades=statistics.mean(ptrd),
            mean_final=statistics.mean(finals),
        )
        r = results[mode]
        print(f"{mode:<13}  {r['call_tp']:>4.1f}%  {r['put_tp']:>4.1f}%  "
              f"{r['call_trades']:>5.1f}  {r['put_trades']:>5.1f}  "
              f"{mean_ret:>+13.1f}%  {med_ret:>+13.1f}%  "
              f"{worst_dd:>7.1f}%  {mean_dd:>6.1f}%  {p_coll:>6.1f}%")

        # Persist this window+mode to DB. Best-effort.
        if os.environ.get('MC_NO_DB_PERSIST', '0') != '1':
            try:
                import sys as _sys
                from database.utils.mc_persist import (
                    persist_mc_window_result, canonical_params_15dte,
                )
                params_snapshot = canonical_params_15dte(_sys.modules[__name__])
                persist_mc_window_result(
                    version=version,
                    dte_strategy='15',
                    window_label=label,
                    window_start=d_start,
                    window_end=d_end,
                    n_iter=N_ITER,
                    engine=mode,                    # 'seeded' for current arch
                    engine_version='bounded-fill',
                    result=results[mode],
                    params=params_snapshot,
                    starting_cash=STARTING_CASH,
                    p25_ret_pct=p25_ret_pct,
                    p75_ret_pct=p75_ret_pct,
                    med_dd_pct=med_dd_pct,
                )
            except Exception as _e:
                print(f"  [warn] MC persist skipped: {_e}")

    if pool is not None:
        pool.close()
        pool.join()

    return results


# ---- Main -------------------------------------------------------------------

def main():
    print('='*100)
    print("MONTE CARLO - 15 DTE VARIANT")
    print('='*100)
    print(f"Strategy : 15 DTE | breadth-adaptive (brd<=50) | Hard={HARD_SELL_LOSS:+.0%}@day{HOLD_DAYS}")
    print(f"  Calls TP: +30% base / +35% stressed  (h30->35 Regime-TP sweep 2026-04-16)")
    print(f"  Calls SL: -35% base / -40% stressed  (h35->40 VIX/breadth decomp 2026-04-16)")
    print(f"  Puts TP : {PUT_TP:+.0%} fixed        (asym weekly+tight SL, 2026-04-17)")
    print(f"  Puts SL : {PUT_SL:+.0%} fixed")
    print(f"Slippage : entry -1.0% | TP 0% (limit sell) | SL -1.3% | Hard -0.5%")
    print(f"  NET_CTP: base={NET_TP_BASE:+.3f} stressed={NET_TP_STRESS:+.3f}")
    print(f"  NET_CSL: base={NET_SL_BASE:+.3f} stressed={NET_SL_STRESS:+.3f}")
    print(f"  NET_PTP: {PUT_NET_TP:+.3f}  NET_PSL: {PUT_NET_SL:+.3f}  NET_HD: {NET_HARD_SELL:+.3f}")
    print(f"C Alloc  : 95+=25%  85-94=15%  80-84=15%  75-79=15%  70-74=0% (disabled)")
    print(f"P Alloc  : <=15=15%  16-20=12%  21-25=12%  (shared 14-slot pool with calls)")
    print(f"MaxPos   : {MAX_POSITIONS}  (upgraded from 10; MaxPos sweep 2026-04-16, monte_carlo_maxpos_sweep.py)")
    if BREADTH_ALLOC_ENABLED:
        print(f"Alloc    : F3f breadth knob (shipped 2026-04-24, +121% 5y compound vs composite-driven)")
        print(f"           Calls scale 1.0 if brd>={F3F_CALL_THRESH:.0f} else linear to {F3F_CALL_FLOOR:.2f} at brd={F3F_CALL_LOW:.0f}")
        print(f"           Puts  scale 1.0 if brd<={F3F_PUT_THRESH:.0f} else linear to {F3F_PUT_FLOOR:.2f} at brd={F3F_PUT_HIGH:.0f}")
    else:
        print(f"Alloc    : Legacy regime_multiplier slope (CUT_ONLY: up={REGIME_SLOPE_UP} dn={REGIME_SLOPE_DOWN} put_up={REGIME_SLOPE_PUT_UP})")
    print(f"         : Primary threshold: {PRIMARY_THRESHOLD}+  |  Overflow: {OVERFLOW_THRESHOLD}-74")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"Sigma    : TP base={TP_SIGMA_BASE:.3f}/stressed={TP_SIGMA_STRESS:.3f}  "
          f"SL base={SL_SIGMA_BASE:.3f}/stressed={SL_SIGMA_STRESS:.3f}")
    print(f"Modes    : {', '.join(COLLISION_MODES)}")
    if CT_PROMOTE:
        print(f"CT       : ENABLED  put_trend>={CT_PUT_TREND_MIN}->{CT_PUT_TIER}  "
              f"call_trend<={CT_CALL_TREND_MAX}->{CT_CALL_TIER}")
    else:
        print(f"CT       : OFF (baseline)")

    pin = os.environ.get('ALGORITHM_VERSION_PIN', '').strip()
    if pin:
        try:
            version = AlgorithmVersion.get(AlgorithmVersion.git_commit == pin)
        except AlgorithmVersion.DoesNotExist:
            cands = list(AlgorithmVersion.select().where(
                AlgorithmVersion.git_commit.startswith(pin)))
            if len(cands) != 1:
                raise SystemExit(f"ALGORITHM_VERSION_PIN={pin!r}: {len(cands)} matches")
            version = cands[0]
        print(f"\nAlgorithm version (PINNED): {version.git_commit} (id={version.id})")
    else:
        version = AlgorithmVersion.get_active_scores_version()
        print(f"\nAlgorithm version: {version.git_commit}")

    all_results = {}
    _windows = WINDOWS
    if WINDOWS_OVERRIDE:
        wanted = {x.strip() for x in WINDOWS_OVERRIDE.split(',') if x.strip()}
        _windows = [w for w in WINDOWS if w[0] in wanted]
    for label, d_start, d_end in _windows:
        all_results[label] = run_window(label, d_start, d_end, version)

    # Final summary table
    print('\n' + '='*110)
    print("SUMMARY - Mean Return by Window x Collision Mode")
    print('='*110)
    print(f"{'Window':<8}  " + '  '.join(f"{m+' MeanRet':>22}" for m in COLLISION_MODES))
    print('-'*110)
    for label, _, _ in _windows:
        row = f"{label:<8}  "
        for mode in COLLISION_MODES:
            r = all_results[label][mode]
            row += f"{r['mean_ret']:>+18,.1f}%     "
        print(row)

    print('\n' + '='*110)
    print("SUMMARY - Worst Drawdown by Window x Collision Mode")
    print('='*110)
    print(f"{'Window':<8}  " + '  '.join(f"{m+' WorstDD':>22}" for m in COLLISION_MODES))
    print('-'*110)
    for label, _, _ in _windows:
        row = f"{label:<8}  "
        for mode in COLLISION_MODES:
            r = all_results[label][mode]
            row += f"{r['worst_dd']:>21.1f}%     "
        print(row)

    print('\n' + '='*110)
    print("SUMMARY - P(collapse) by Window x Collision Mode")
    print('='*110)
    print(f"{'Window':<8}  " + '  '.join(f"{m+' P(coll)':>22}" for m in COLLISION_MODES))
    print('-'*110)
    for label, _, _ in _windows:
        row = f"{label:<8}  "
        for mode in COLLISION_MODES:
            r = all_results[label][mode]
            row += f"{r['p_coll']:>21.1f}%     "
        print(row)


if __name__ == '__main__':
    main()
