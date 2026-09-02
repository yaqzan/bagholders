"""
Monte Carlo — Canonical Optimal Strategy
=========================================
Runs the locked-in optimal strategy across per-year windows (2021-2025) and
the continuous 5-year window (Jan 2021 - Apr 2026).

Per-iteration MC dispersion:
  - Random fill within the trigger bar: U_fire ~ Uniform(open, close)
  - Vega ratio sampled empirically from iv_crush_samples.csv when the trade
    spans an earnings event; else 1.0
  - Same-bar TP+SL collision: 50/50 coin flip per iteration (preserves the
    Realistic-mode behavior; per-iter RNG provides natural dispersion)
  Replaces the prior 3-mode (Conservative/Realistic/Optimistic) system —
  the seed-driven distribution IS the variance band.

Realized P&L on fire is computed via `option_pricing.option_pnl_pct` with
delta + theta + vega closed form (validated +24% RMSE vs delta-only legacy
on N=13,712 real option_prices bars; see experiments/option_pricing_validation.py).

Strategy (fixed):
  DTE       : 30 DTE (hold up to 15 trading bars)
  Calls TP  : +30% base / +35% when breadth_score <= 50  (h30->35 breadth-adaptive)
  Calls SL  : -35% base / -40% when breadth_score <= 50  (h35->40 breadth-adaptive)
  Puts TP   : +30% (fixed)                                (asym weekly + tight SL, 2026-04-17)
  Puts SL   : -20% (fixed)                                (tight SL enables positive EV cross-regime)
  Hard sell : -50% at day 15
  Slippage  : entry -1%, TP 0% (limit sell), SL -1.3%, hard -0.5% (per-exit)
  Call alloc: cascade  95+=25%  85-94=15%  80-84=15%  75-79=15%  70-74=0% (disabled)
  Put  alloc: cascade  <=15=15%  16-20=12%  21-25=12%
  Max pos   : 14 concurrent positions (shared pool; calls fill first each day)
  Thresholds: calls 75+ primary, 70-74 disabled; puts <=25
  Same-sym  : one open position per symbol across sides (re-entry blocked)
  Start     : $50,000 per window, 500 MC iterations per (window x mode)

Usage: python monte_carlo.py
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
# Values flow from strategy_config.py (single source of truth). Module-level
# names persist for back-compat with code that does `from monte_carlo import
# TP_BASE`. Sweeps mutate via env vars; the dataclass is frozen and never
# touched. See CLAUDE.md "Shipping a Portfolio Strategy Change" — the 7-
# location duplication that motivated this collapse, plus the drift test
# at tests/test_strategy_config_drift.py that guards against regressions.
import strategy_config as _sc
_cfg = _sc.STRATEGY_30DTE   # this module is the 30 DTE engine
_opt = _cfg.option

STARTING_CASH      = 50_000.0
N_ITER             = 500
# (overridden later by N_ITER_OVERRIDE env var if set)
VOL_LOOKBACK       = _cfg.VOL_LOOKBACK
HOLD_DAYS          = _cfg.HOLD_DAYS
PREMIUM_MULT       = _cfg.PREMIUM_MULT
DELTA              = _opt.DELTA

# Breadth-adaptive exits — same signal (breadth_score <= BREADTH_THRESHOLD)
# switches BOTH TP and SL into the "stressed" band.
TP_BASE            = _opt.TP_BASE
TP_STRESS          = _opt.TP_STRESS
SL_BASE            = _opt.SL_BASE
SL_STRESS          = _opt.SL_STRESS
HARD_SELL_LOSS     = _cfg.HARD_SELL_LOSS
BREADTH_THRESHOLD  = _opt.BREADTH_THRESHOLD

SLIP_ENTRY = _opt.SLIP_ENTRY
SLIP_TP    = _opt.SLIP_TP
SLIP_SL    = _opt.SLIP_SL
SLIP_HARD  = _opt.SLIP_HARD

# Derived: net P&L after slippage (computed by @property on dataclass).
NET_TP_BASE   = _opt.NET_TP_BASE       # +0.340
NET_TP_STRESS = _opt.NET_TP_STRESS     # +0.390
NET_SL_BASE   = _opt.NET_SL_BASE       # -0.323
NET_SL_STRESS = _opt.NET_SL_STRESS     # -0.373
NET_HARD_SELL = _cfg.NET_HARD_SELL     # -0.415

# Derived: underlying σ thresholds for TP/SL barrier-touch detection.
TP_SIGMA_BASE   = _cfg.TP_SIGMA_BASE       # 1.274
TP_SIGMA_STRESS = _cfg.TP_SIGMA_STRESS     # 1.456
SL_SIGMA_BASE   = _cfg.SL_SIGMA_BASE       # 1.092
SL_SIGMA_STRESS = _cfg.SL_SIGMA_STRESS     # 1.274

# Put-side fixed parameters (no breadth switch by default).
PUT_TP            = _opt.PUT_TP
PUT_SL            = _opt.PUT_SL
PUT_NET_TP        = _opt.PUT_NET_TP        # +0.340
PUT_NET_SL        = _opt.PUT_NET_SL        # -0.223
PUT_TP_SIGMA      = _cfg.PUT_TP_SIGMA      # 1.274
PUT_SL_SIGMA      = _cfg.PUT_SL_SIGMA      # 0.728

# Put breadth-adaptive exits (disabled by default; mode in {'none','invert','same'}).
PUT_BREADTH_MODE       = _opt.PUT_BREADTH_MODE
PUT_BREADTH_THRESHOLD  = _opt.PUT_BREADTH_THRESHOLD
PUT_TP_STRESS          = _opt.PUT_TP_STRESS
PUT_SL_STRESS          = _opt.PUT_SL_STRESS

# Put SL hard-hold: suppress SL check for first N trading bars after entry.
# Hold=0 ships (Phase H1/H5 winner under bug-fixed MC).
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
def _env_optional_int(name):
    v = os.environ.get(name, '').strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None
MAX_POSITIONS_CALL = _env_optional_int('MAX_POSITIONS_CALL')
MAX_POSITIONS_PUT  = _env_optional_int('MAX_POSITIONS_PUT')

# H3 — DD-soft band call alloc contraction (shipped 2026-05-04, see strategy_config).
# When running portfolio DD ∈ [LO, HI], scale call alloc linearly from 1.0
# down to FLOOR. Above HI = full floor. Below LO = no effect (1.0×).
# 30 DTE ships LO=0.40, HI=0.60, FLOOR=0.50 (mild — only fires deep tail).
DD_SOFT_BAND_LO     = float(os.environ.get('DD_SOFT_BAND_LO', str(_cfg.DD_SOFT_BAND_LO)))
DD_SOFT_BAND_HI     = float(os.environ.get('DD_SOFT_BAND_HI', str(_cfg.DD_SOFT_BAND_HI)))
DD_SOFT_CALL_FLOOR  = float(os.environ.get('DD_SOFT_CALL_FLOOR', str(_cfg.DD_SOFT_CALL_FLOOR)))

# Cascade allocation per score tier (calls + puts). Copied from strategy_config
# into mutable module-level dicts so sweeps can edit them in-place; the dataclass
# itself is frozen.
TIER_ALLOC         = dict(_cfg.TIER_ALLOC)
PUT_TIER_ALLOC     = dict(_cfg.PUT_TIER_ALLOC)
MAX_POSITIONS      = _cfg.MAX_POSITIONS
PRIMARY_THRESHOLD  = _cfg.PRIMARY_THRESHOLD
OVERFLOW_THRESHOLD = _cfg.OVERFLOW_THRESHOLD

# DD circuit breaker — pause new entries when running portfolio DD exceeds
# this threshold. Default from strategy_config (0.68 for 30 DTE). Env
# override preserved for sweeps. Phase OP1 attempted 0.60 ship; OP1b full
# N=300×8-window validation reversed it (5y compound -5.4%, 5y DD +2.5pp).
# See known-issues.md "Phase OP1 — REVERTED under OP1b validation".
DD_CIRCUIT_BREAKER = float(os.environ.get('DD_CIRCUIT_BREAKER', str(_cfg.DD_CIRCUIT_BREAKER)))

# Design B — premium-based liquidation stop. End-of-bar check on
# option_pnl_pct (delta + theta only at trigger time; vega applied at resolve).
# Disabled by default. Tested values: -0.40, -0.50, -0.60.
PREM_STOP_LOSS = float(os.environ.get('PREM_STOP_LOSS', '0.0'))

# Dead-hold post-SL mechanism (Spec C, in flight 2026-04-30). When SL fires
# AND sampled-vega realized pnl ≤ trigger, override with pre-computed dead-hold
# outcome (popout-or-expiry walk under vega=1.0). See backtest_cascade.py and
# strategy_config.py for design rationale. Walk done at outcome-detection time
# (cheap, deterministic per signal); resolve() applies the override per-iter.
DEAD_HOLD_ENABLED      = (os.environ.get('DEAD_HOLD_ENABLED', '1' if _cfg.DEAD_HOLD_ENABLED else '0') == '1')
DEAD_HOLD_TRIGGER_PNL  = float(os.environ.get('DEAD_HOLD_TRIGGER_PNL', _cfg.DEAD_HOLD_TRIGGER_PNL))
DEAD_HOLD_POPOUT_PNL   = float(os.environ.get('DEAD_HOLD_POPOUT_PNL',  _cfg.DEAD_HOLD_POPOUT_PNL))

# Reallocation — when pool capped and a high-conviction signal arrives, displace
# an existing position by the configured strategy. Disabled by default.
# Strategies: entry_score_low, current_pnl_high, current_pnl_low,
# days_held_high, sigma_distance_low.
REALLOC_STRATEGY      = os.environ.get('REALLOC_STRATEGY', '')   # '' = off
REALLOC_MIN_ADVANTAGE = float(os.environ.get('REALLOC_MIN_ADVANTAGE', '5.0'))

# Regime-aware allocation: alloc_scale = 1.0 + slope * (regime_mult - 1.0).
# Shipped 2026-04-17 asymmetric CUT_ONLY (slope_up=0, slope_down=1.0):
# beats SC100 by +58% compound on 22-now Realistic with lower DD-C.
# Bull-side boost was actively hurting; alpha is entirely stress-side contraction.
REGIME_SLOPE          = _cfg.REGIME_SLOPE
REGIME_SLOPE_PUT      = _cfg.REGIME_SLOPE_PUT
ALLOC_SCALE_FLOOR     = _cfg.ALLOC_SCALE_FLOOR
ALLOC_SCALE_CEIL      = _cfg.ALLOC_SCALE_CEIL
REGIME_SLOPE_UP       = _cfg.REGIME_SLOPE_UP
REGIME_SLOPE_DOWN     = _cfg.REGIME_SLOPE_DOWN
REGIME_SLOPE_PUT_UP   = _cfg.REGIME_SLOPE_PUT_UP
REGIME_SLOPE_PUT_DOWN = _cfg.REGIME_SLOPE_PUT_DOWN

# Breadth-driven allocation knob (F3f) — shipped 2026-04-24.
# Anchors alloc directly on MarketBreadth.breadth_score. Asymmetric:
# cut calls at low breadth, cut puts at high breadth.
BREADTH_ALLOC_ENABLED = _cfg.BREADTH_ALLOC_ENABLED
F3F_CALL_THRESH       = float(os.environ.get('F3F_CALL_THRESH', str(_cfg.F3F_CALL_THRESH)))
F3F_CALL_FLOOR        = float(os.environ.get('F3F_CALL_FLOOR', str(_cfg.F3F_CALL_FLOOR)))
F3F_CALL_LOW          = float(os.environ.get('F3F_CALL_LOW',   str(_cfg.F3F_CALL_LOW)))
F3F_PUT_THRESH        = _cfg.F3F_PUT_THRESH
F3F_PUT_FLOOR         = _cfg.F3F_PUT_FLOOR
F3F_PUT_HIGH          = _cfg.F3F_PUT_HIGH

# Stress-side F3f cut for puts (research, env-gated). Mirrors the call F3f
# but in the stress direction: scale put allocation DOWN when breadth is
# very weak (extreme bear tape). 0 = disabled (production default).
# Targets Phase 1B correlated-DD days (brd ≤ 25, p95 of put-fire density).
F3F_PUT_STRESS_THRESH = float(os.environ.get('F3F_PUT_STRESS_THRESH', '0'))   # breadth below which cut activates
F3F_PUT_STRESS_FLOOR  = float(os.environ.get('F3F_PUT_STRESS_FLOOR',  '0.70')) # min scale at extreme stress
F3F_PUT_STRESS_LOW    = float(os.environ.get('F3F_PUT_STRESS_LOW',    '10'))   # breadth at which floor is reached

PUT_THRESHOLD      = int(os.environ.get('PUT_THRESHOLD_OVERRIDE', str(_cfg.PUT_THRESHOLD)))
COLLAPSE_THRESHOLD = _cfg.COLLAPSE_THRESHOLD

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
# Drops puts in [MIN_OV, MAX_OV] when an EarningsDate falls in
# (signal_date, signal_date + DAYS trading days]. Defaults from
# strategy_config; env overrides preserved for sweeps.
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

# Daily put cap (portfolio-stage, env-gated). Cap puts admitted per calendar day,
# sorted by conviction (lower overall = more bearish = higher priority). 0 = disabled.
# Directly targets Phase 1B finding: 2.1% of days have >=50 concurrent puts,
# driving correlated-SL DD spikes. Median is only 11/day; cap fires on tail days.
MAX_PUTS_PER_DAY       = int(os.environ.get('MAX_PUTS_PER_DAY', '0'))

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
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]

# Removed 2026-04-29: 3-mode collision system replaced by per-iter seeded
# random fill within the trigger bar. Single MC mode now ('seeded').
# COLLISION_MODES kept as a back-compat label for external scripts that
# iterate over modes; only 'seeded' is meaningful.
COLLISION_MODES = ['seeded']


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
        # Stress-side cut: reduce put allocation in extreme bear tape
        if F3F_PUT_STRESS_THRESH > 0 and breadth < F3F_PUT_STRESS_THRESH:
            if breadth <= F3F_PUT_STRESS_LOW:
                return F3F_PUT_STRESS_FLOOR
            frac = (F3F_PUT_STRESS_THRESH - breadth) / max(1.0, F3F_PUT_STRESS_THRESH - F3F_PUT_STRESS_LOW)
            return 1.0 - frac * (1.0 - F3F_PUT_STRESS_FLOOR)
        # Bull-side cut: reduce put allocation in strong bull tape (existing)
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
    """Call signals: overall >= OVERFLOW_THRESHOLD (70)."""
    return list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
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
    ed_rows = list(EarningsDate.select(
            EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                   .where(EarningsDate.symbol.in_(list(syms)),
                          EarningsDate.date >= d_start - timedelta(days=10),
                          EarningsDate.date <= d_end + timedelta(days=days*2 + 7))
                   .order_by(EarningsDate.symbol, EarningsDate.date)
                   .tuples())
    from iv_crush_model import compute_effective_date
    ed_map = defaultdict(list)
    for sym, d, ct in ed_rows:
        ed_map[sym].append(compute_effective_date(d, ct))   # AMC shifted forward

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
            PriceHistory.close, PriceHistory.high, PriceHistory.low,
            PriceHistory.open
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
    for sym_id, d, c, h, l, o in rows:
        ph[sym_id].append((d, float(c), float(h), float(l), float(o)))
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

def compute_trade_outcome(sym_bars, signal_date, stressed):
    """
    Returns dict with keys:
      kind     : 'tp' | 'sl' | 'hard' | 'both'
      exit_bar : int (trading bars from signal_date)
      side     : 'call'
      stressed : bool (for slippage band selection at resolve-time)
      premium_pct, vol, entry, signal_date
      fire_open, fire_close, fire_high, fire_low : OHLC of trigger bar (for
                 random-fill resolution in resolve()). For 'hard' kind, these
                 are the day-15 close (no underlying-move sampling).

    NOTE: trigger detection still uses σ-barriers on the underlying (cheap,
    JIT-compatible). The realized P&L on fire is computed in resolve() via
    `option_pricing.option_pnl_pct` with theta + vega applied at random fill,
    not the static SL/TP barrier values. This replaces the old static
    `realized_sl_pnl = base_sl_pct` assumption.
    """
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    opens  = [b[4] for b in sym_bars]

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

    tp_sigma = TP_SIGMA_STRESS if stressed else TP_SIGMA_BASE
    sl_sigma = SL_SIGMA_STRESS if stressed else SL_SIGMA_BASE

    premium_pct = PREMIUM_MULT * vol / 100
    tp_level    = entry_price * (1 + tp_sigma * vol / 100)
    sl_level    = entry_price * (1 - sl_sigma * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    prem_stop = PREM_STOP_LOSS
    if prem_stop < 0.0:
        from option_pricing import option_pnl_pct as _opn
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
        # Design B premium stop — end-of-bar check on option pnl (vega=1.0
        # at trigger; sampled vega applied in resolve()).
        if prem_stop < 0.0:
            bars_held = i - base_idx
            cur_pnl = _opn('call', closes[i], entry_price, bars_held,
                           premium_pct, vega_ratio=1.0, delta=DELTA)
            if cur_pnl <= prem_stop:
                kind, exit_bar = 'prem', bars_held
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

    # Pre-compute dead-hold outcome for SL fires. Walk forward from the fire
    # bar with vega=1.0 — vega-aware re-walk is deferred for now (see code
    # comment in resolve() on the approximation).
    if kind == 'sl' and DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        dh = _compute_dead_hold_call(
            highs, lows, closes, opens, sl_fire_idx, end_idx,
            entry_price, premium_pct, base_idx)
        out['dead_hold'] = dh
    return out


def _compute_dead_hold_call(highs, lows, closes, opens, fire_idx, end_idx,
                             entry, premium_pct, base_idx):
    """Walk forward from fire_idx+1 looking for an intraday-high popout.
    Returns (kind_str, exit_bar, exit_pnl) where:
      kind_str: 'dh_pop' | 'dh_expiry' | 'dh_open'
      exit_bar: bars from base_idx
      exit_pnl: realized option pnl at exit (vega=1.0 approximation)
    """
    from option_pricing import option_pnl_pct
    for k in range(fire_idx + 1, end_idx):
        bars_k = k - base_idx
        high_k = highs[k]; open_k = opens[k]
        high_pnl = option_pnl_pct('call', high_k, entry, bars_k,
                                  premium_pct, vega_ratio=1.0, delta=DELTA)
        if high_pnl >= DEAD_HOLD_POPOUT_PNL:
            open_pnl = option_pnl_pct('call', open_k, entry, bars_k,
                                      premium_pct, vega_ratio=1.0, delta=DELTA)
            fill_pnl = max(DEAD_HOLD_POPOUT_PNL, open_pnl)
            return ('dh_pop', bars_k, fill_pnl)
    # No popout within hold window — exit at end-of-window close
    last_k = min(end_idx - 1, fire_idx + (end_idx - fire_idx - 1))
    if last_k <= fire_idx:
        return ('dh_open', fire_idx - base_idx, -1.0)
    bars_last = last_k - base_idx
    last_pnl = option_pnl_pct('call', closes[last_k], entry, bars_last,
                              premium_pct, vega_ratio=1.0, delta=DELTA)
    return ('dh_expiry', bars_last, last_pnl)


def _compute_dead_hold_put(highs, lows, closes, opens, fire_idx, end_idx,
                            entry, premium_pct, base_idx):
    """Put-side dead-hold walk — checks intraday LOW for popout."""
    from option_pricing import option_pnl_pct
    for k in range(fire_idx + 1, end_idx):
        bars_k = k - base_idx
        low_k = lows[k]; open_k = opens[k]
        low_pnl = option_pnl_pct('put', low_k, entry, bars_k,
                                 premium_pct, vega_ratio=1.0, delta=DELTA)
        if low_pnl >= DEAD_HOLD_POPOUT_PNL:
            open_pnl = option_pnl_pct('put', open_k, entry, bars_k,
                                      premium_pct, vega_ratio=1.0, delta=DELTA)
            fill_pnl = max(DEAD_HOLD_POPOUT_PNL, open_pnl)
            return ('dh_pop', bars_k, fill_pnl)
    last_k = min(end_idx - 1, fire_idx + (end_idx - fire_idx - 1))
    if last_k <= fire_idx:
        return ('dh_open', fire_idx - base_idx, -1.0)
    bars_last = last_k - base_idx
    last_pnl = option_pnl_pct('put', closes[last_k], entry, bars_last,
                              premium_pct, vega_ratio=1.0, delta=DELTA)
    return ('dh_expiry', bars_last, last_pnl)


def _put_sl_hold_bars(signal_date):
    """Return number of trading bars to suppress SL check after entry.
    Monday entries get 4 bars (same calendar-day coverage as Tue-Fri 3-bar hold).
    """
    return PUT_SL_HOLD_BARS_MONDAY if signal_date.weekday() == 0 else PUT_SL_HOLD_BARS_DEFAULT


def compute_put_outcome(sym_bars, signal_date, put_stressed=False):
    """Put trade: win = underlying falls; stop = rises. Breadth-adaptive when put_stressed=True.

    Trigger uses σ-barriers on underlying high/low. Realized P&L is computed
    in resolve() via option_pricing.option_pnl_pct with theta + vega applied
    at random fill within the trigger bar (not static SL/TP barrier values).
    """
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    opens  = [b[4] for b in sym_bars]

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
        sl_pct = PUT_SL_STRESS
    else:
        sl_pct = PUT_SL
    tp_pct = PUT_TP if not (put_stressed and PUT_BREADTH_MODE != 'none') else PUT_TP_STRESS
    tp_sigma = tp_pct * PREMIUM_MULT / DELTA
    tp_level = entry_price * (1 - tp_sigma * vol / 100)

    # SL schedule: list of (max_bar_inclusive, sl_pct) — first whose max_bar
    # >= current_bar wins. Static mode = single (HOLD_DAYS, sl_pct) entry.
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
    premium_pct = PREMIUM_MULT * vol / 100
    kind = 'hard'; exit_bar = HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    fire_sl_level = None
    prem_stop = PREM_STOP_LOSS
    if prem_stop < 0.0:
        from option_pricing import option_pnl_pct as _opn
    for i in range(base_idx + 1, end_idx):
        bar    = i - base_idx          # 1-indexed
        sl_pct_t   = _sl_for_bar(bar)
        sl_sigma_t = abs(sl_pct_t) * PREMIUM_MULT / DELTA
        sl_level_t = entry_price * (1 + sl_sigma_t * vol / 100)
        tp_hit = lows[i]  <= tp_level
        sl_hit = (highs[i] >= sl_level_t) and (bar > sl_hold)
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if tp_hit:
            kind, exit_bar = 'tp',   bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if sl_hit:
            kind, exit_bar = 'sl',   bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        # Design B premium stop (puts) — end-of-bar; suppressed during sl_hold.
        if prem_stop < 0.0 and bar > sl_hold:
            cur_pnl = _opn('put', closes[i], entry_price, bar,
                           premium_pct, vega_ratio=1.0, delta=DELTA)
            if cur_pnl <= prem_stop:
                kind, exit_bar = 'prem', bar
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                fire_sl_level = sl_level_t
                break

    if kind == 'hard':
        last_idx = base_idx + HOLD_DAYS
        if last_idx >= len(dates):
            last_idx = len(dates) - 1
        fire_o = opens[last_idx]; fire_c = closes[last_idx]
        fire_h = highs[last_idx]; fire_l = lows[last_idx]
        # Last bar's SL level (used only for diagnostic — hard fires don't sample)
        last_bar_idx = last_idx - base_idx
        fire_sl_level = entry_price * (1 + abs(_sl_for_bar(last_bar_idx)) * PREMIUM_MULT / DELTA * vol / 100)

    out = dict(kind=kind, exit_bar=exit_bar, side='put', stressed=put_stressed,
               premium_pct=premium_pct, vol=vol, entry=entry_price,
               signal_date=signal_date,
               fire_open=fire_o, fire_close=fire_c,
               fire_high=fire_h, fire_low=fire_l,
               fire_tp_level=tp_level, fire_sl_level=fire_sl_level)

    # Pre-compute dead-hold outcome for SL fires.
    if kind == 'sl' and DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        out['dead_hold'] = _compute_dead_hold_put(
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
    a vega ratio at fire time.
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
                        ern_map=None, trading_days=None):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        stressed = is_stressed(breadth_dates, breadth_map, sig.date)
        r = compute_trade_outcome(sym_bars, sig.date, stressed)
        if r is None:
            continue
        ern_for_sym = (ern_map or {}).get(sig.symbol_id, [])
        _attach_earnings_span(r, ern_for_sym, trading_days)
        outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def precompute_put_outcomes(signals, ph, breadth_dates=None, breadth_map=None,
                            ern_map=None, trading_days=None):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        put_stressed = False
        if PUT_BREADTH_MODE != 'none' and breadth_dates is not None and breadth_map is not None:
            put_stressed = _is_put_stressed(breadth_dates, breadth_map, sig.date)
        r = compute_put_outcome(sym_bars, sig.date, put_stressed=put_stressed)
        if r is None:
            continue
        ern_for_sym = (ern_map or {}).get(sig.symbol_id, [])
        _attach_earnings_span(r, ern_for_sym, trading_days)
        outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


# Module-level guard for one-time vega sample pool load
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
    """Return ('tp' | 'sl' | 'hard' | 'both', net_option_pnl).

    Random-fill semantics, bounded by which barrier actually triggered (so
    a TP-only fire cannot resolve below TP, and an SL-only fire cannot
    resolve above SL — matching real limit/stop order semantics):

      kind='tp', call : fill ~ Uniform(tp_level, high)         limit-or-better
      kind='tp', put  : fill ~ Uniform(low, tp_level)          limit-or-better
      kind='sl', call : conditional bimodal — see below        stop-or-gap
      kind='sl', put  : conditional bimodal — see below        stop-or-gap
      kind='both'     : fill ~ Uniform(low, high)              path ambiguous
      kind='hard'     : fill = day-15 close                    deterministic

    SL bimodal fill (calibrated 2026-04-30 from sl_fill_bias_audit on
    124,872 real bars; mean(pos)=0.698, median=0.992 confirmed bimodal):

      Calls (sl_level < entry):
        intraday trigger (open > sl_level): fill = sl_level deterministically
            — opened above barrier, dropped to it; stop fills at the barrier.
        gap-through (open <= sl_level): fill ~ Uniform(low, open)
            — gapped below SL at open; stop fills somewhere in the gap region.

      Puts (sl_level > entry):
        intraday trigger (open < sl_level): fill = sl_level deterministically
            — opened below barrier, rose to it; stop fills at the barrier.
        gap-through (open >= sl_level): fill ~ Uniform(open, high)
            — gapped above SL at open; stop fills somewhere in the gap region.

    Replaces uniform [low, sl_level] / [sl_level, high] which over-estimated
    SL realized loss by ~5-15% of premium per fire (the audit measured a
    +0.198 bias of mean fill position vs uniform 0.5).

    Realized option P&L is computed via option_pricing.option_pnl_pct with
    delta + theta + (sampled empirical vega when spans-earnings).

    Slippage:
      - tp/both-favorable: SLIP_TP (~0, limit fill)
      - sl/both-adverse:   SLIP_SL (~-1.3%, stop fill)
      - hard:              SLIP_HARD (~-0.5%)
    """
    from option_pricing import option_pnl_pct, sample_vega_ratio, DEFAULT_TOTAL_DTE

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
        vega_ratio = sample_vega_ratio(side_name, dte=DEFAULT_TOTAL_DTE, rng=rng)

    # Hard exit: forced day-15 close, deterministic.
    if kind == 'hard':
        u_fire = outcome['fire_close']
        pnl = option_pnl_pct(side, u_fire, entry, bars_held, premium_pct,
                             total_dte=DEFAULT_TOTAL_DTE, vega_ratio=vega_ratio)
        return 'hard', pnl + SLIP_ENTRY + SLIP_HARD

    # Design B premium-based stop — fire at close (deterministic), apply
    # sampled vega for spans-earnings consistency with σ-fires.
    if kind == 'prem':
        u_fire = outcome['fire_close']
        pnl = option_pnl_pct(side, u_fire, entry, bars_held, premium_pct,
                             total_dte=DEFAULT_TOTAL_DTE, vega_ratio=vega_ratio)
        return 'prem', pnl + SLIP_ENTRY + SLIP_SL

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
        # Bimodal: intraday trigger fills at barrier; gap-through samples gap.
        if side == 'call':
            # call SL: sl_level < entry; intraday trigger when open > sl_level
            if op is not None and op > sl_lvl:
                u_lo = u_hi = sl_lvl                 # intraday — fill at barrier
            else:
                gap_open = op if op is not None else sl_lvl
                u_lo, u_hi = lo, gap_open            # gap-through — Uniform(low, open)
        else:                          # put
            # put SL: sl_level > entry; intraday trigger when open < sl_level
            if op is not None and op < sl_lvl:
                u_lo = u_hi = sl_lvl                 # intraday — fill at barrier
            else:
                gap_open = op if op is not None else sl_lvl
                u_lo, u_hi = gap_open, hi            # gap-through — Uniform(open, high)
        slip = SLIP_SL
    else:                              # 'both' — path ambiguous
        u_lo, u_hi = lo, hi
        slip = None                    # set after computing P&L sign

    # Defensive: ensure ordering even with floating-point edge cases
    if u_hi < u_lo:
        u_lo, u_hi = u_hi, u_lo
    u_fire = u_lo + rng.random() * (u_hi - u_lo) if u_hi > u_lo else u_lo

    pnl = option_pnl_pct(side, u_fire, entry, bars_held, premium_pct,
                         total_dte=DEFAULT_TOTAL_DTE, vega_ratio=vega_ratio)

    # Dead-hold override: if this is an SL fire and the realized pnl is too
    # deep to actually liquidate (no liquid bid on a deep-OTM option), use the
    # pre-computed popout-or-expiry walk instead of recycling capital here.
    # Approximation: dead-hold walk uses vega=1.0 even when sampled vega < 1.0
    # for earnings-spanning trades — vega-aware re-walk per-iter is deferred.
    # The mechanism is primarily a measurement-realism fix for non-earnings
    # collapses (e.g., DeepSeek-shock APH/PWR), where vega=1.0 is correct.
    if (DEAD_HOLD_ENABLED and kind == 'sl'
            and pnl <= DEAD_HOLD_TRIGGER_PNL
            and 'dead_hold' in outcome):
        dh_kind, dh_bars, dh_pnl = outcome['dead_hold']
        return dh_kind, dh_pnl + SLIP_ENTRY   # no exit slippage at popout limit

    if slip is None:
        slip = SLIP_TP if pnl >= 0 else SLIP_SL
    return kind, pnl + SLIP_ENTRY + slip


# ---- Portfolio simulation ---------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl',
                 'outcome', 'side', 'score', 'tier', 'ct', 'ern',
                 'entry_value', 'entry_peak', 'entry_dd',
                 'entry_open_calls', 'entry_open_puts']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome, side,
                 score=None, tier=None, ct=None, ern=None,
                 entry_value=None, entry_peak=None, entry_dd=None,
                 entry_open_calls=None, entry_open_puts=None):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome
        self.side         = side  # 'call' or 'put'
        # Tape metadata (None unless MC_TRADE_TAPE collection is active)
        self.score            = score
        self.tier             = tier
        self.ct               = ct
        self.ern              = ern
        self.entry_value      = entry_value
        self.entry_peak       = entry_peak
        self.entry_dd         = entry_dd
        self.entry_open_calls = entry_open_calls
        self.entry_open_puts  = entry_open_puts


# DD-ledger trade-tape collection (env-flagged; off by default).
# When MC_TRADE_TAPE=1, run_single_sim records:
#   - one row per closed trade with entry context + outcome + dollar PnL
#   - one row per seed with worst-DD episode bounds (start_date, end_date, magnitude)
# Output is collected in run_window and written to .cache/dd_ledger/tape_{label}.parquet
TRADE_TAPE_ENABLED = os.environ.get('MC_TRADE_TAPE', '0') == '1'


def run_single_sim(trading_days, calls_by_date, call_outcomes,
                   puts_by_date, put_outcomes, rng,
                   regime_dates=None, regime_map=None,
                   collect_tape=False):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c  = sl_c  = hard_c  = 0
    tp_p  = sl_p  = hard_p  = 0

    # Trade-tape collection (per-trade rows + DD-episode bounds)
    trade_rows = []  if collect_tape else None
    # Daily portfolio path — needed post-walk to identify worst DD episode bounds
    daily_path = []  if collect_tape else None
    # Track current peak-to-trough episode in-line; flush when a new peak is set
    cur_episode_start_idx = 0  if collect_tape else None
    cur_episode_trough_idx = 0  if collect_tape else None
    cur_episode_trough_val = STARTING_CASH  if collect_tape else None
    episodes = []  if collect_tape else None  # list of (start_idx, trough_idx, peak_val, trough_val)

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
                if collect_tape:
                    trade_rows.append((
                        p.sym_id, p.entry_date, today, p.side, p.score, p.tier,
                        p.ct, p.ern, p.premium_cost, p.option_pnl, p.outcome,
                        p.entry_value, p.entry_peak, p.entry_dd,
                        p.entry_open_calls, p.entry_open_puts,
                    ))
            else:
                keep.append(p)
        positions = keep

        portfolio_value = cash + sum(p.premium_cost for p in positions)
        if portfolio_value > peak_value:
            # Flush completed episode (if any non-trivial drawdown occurred)
            if collect_tape and cur_episode_trough_val < peak_value:
                episodes.append((cur_episode_start_idx, cur_episode_trough_idx,
                                 peak_value, cur_episode_trough_val))
            peak_value = portfolio_value
            if collect_tape:
                cur_episode_start_idx  = day_idx
                cur_episode_trough_idx = day_idx
                cur_episode_trough_val = portfolio_value
        else:
            if collect_tape and portfolio_value < cur_episode_trough_val:
                cur_episode_trough_idx = day_idx
                cur_episode_trough_val = portfolio_value
        dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if collect_tape:
            daily_path.append((today, portfolio_value, peak_value, dd))
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
            # H3: DD-soft-band call alloc contraction
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
            if collect_tape:
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'call',
                                           score=score, tier=tier, ct=ct, ern=ern,
                                           entry_value=portfolio_value, entry_peak=peak_value,
                                           entry_dd=dd, entry_open_calls=call_open,
                                           entry_open_puts=put_open))
            else:
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
            alloc_frac   = PUT_TIER_ALLOC[tier] * reg_scale_p
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                return False
            o = put_outcomes[key]
            outcome, pnl = resolve(o, rng)
            cash -= premium_cost
            if collect_tape:
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'put',
                                           score=score, tier=tier, ct=ct, ern=False,
                                           entry_value=portfolio_value, entry_peak=peak_value,
                                           entry_dd=dd, entry_open_calls=call_open,
                                           entry_open_puts=put_open))
            else:
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

        # DD circuit breaker — skip ALL new entries when running DD exceeds threshold.
        # Existing positions continue to resolve normally (TP/SL/Hard exits fire as usual).
        if DD_CIRCUIT_BREAKER > 0.0 and dd > DD_CIRCUIT_BREAKER:
            # Recovery safety valve: when breaker fires AND there are no open
            # positions, recovery is impossible (no winning trades possible →
            # DD stays > threshold forever). Reset peak to current equity.
            if not positions:
                peak_value = portfolio_value
                dd = 0.0
                # Don't continue — fall through and let new entries open
            else:
                continue

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

    last_idx = len(trading_days) - 1
    last_day = trading_days[-1] if trading_days else None
    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else:                hard_p += 1
        if collect_tape:
            trade_rows.append((
                p.sym_id, p.entry_date, last_day, p.side, p.score, p.tier,
                p.ct, p.ern, p.premium_cost, NET_HARD_SELL, 'hard',
                p.entry_value, p.entry_peak, p.entry_dd,
                p.entry_open_calls, p.entry_open_puts,
            ))
    portfolio_value = cash

    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    if collect_tape:
        # Flush final episode
        if cur_episode_trough_val < peak_value:
            episodes.append((cur_episode_start_idx, cur_episode_trough_idx,
                             peak_value, cur_episode_trough_val))
        # If final value is below peak (final_dd > 0), record it as episode too
        if portfolio_value < peak_value and last_idx >= 0:
            episodes.append((cur_episode_start_idx, last_idx, peak_value, portfolio_value))
    ct = tp_c + sl_c + hard_c or 1
    pt = tp_p + sl_p + hard_p or 1
    result = dict(
        final = portfolio_value,
        max_dd = max_dd,
        call_tp = tp_c / ct * 100, call_sl = sl_c / ct * 100, call_hard = hard_c / ct * 100,
        put_tp  = tp_p / pt * 100, put_sl  = sl_p / pt * 100, put_hard  = hard_p / pt * 100,
        call_trades = tp_c + sl_c + hard_c,
        put_trades  = tp_p + sl_p + hard_p,
    )
    if collect_tape:
        # Only return top-3 worst episodes per seed (sorted by magnitude desc)
        ranked = sorted(episodes, key=lambda e: (e[2] - e[3]) / e[2] if e[2] > 0 else 0, reverse=True)
        result['_tape']     = trade_rows
        result['_episodes'] = ranked[:3]
    return result


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


def _mc_iter_worker(seed):
    """Run one MC iteration. Receives seed — pulls heavy data from worker globals."""
    rng = random.Random(seed)
    return run_single_sim(
        _MP_STATE['trading_days'],  _MP_STATE['calls_by_date'], _MP_STATE['call_outcomes'],
        _MP_STATE['puts_by_date'],  _MP_STATE['put_outcomes'],  rng,
        _MP_STATE['regime_dates'],  _MP_STATE['regime_map'],
        collect_tape=TRADE_TAPE_ENABLED,
    )


# ---- Trade-tape parquet dump (DD-ledger feed) -------------------------------

def _dump_trade_tape(label, seeds, results, trading_days):
    """Write per-trade tape + per-seed DD episodes to .cache/dd_ledger/."""
    from pathlib import Path
    out_dir = Path(__file__).resolve().parent / '.cache' / 'dd_ledger'
    out_dir.mkdir(parents=True, exist_ok=True)

    trade_cols = ('seed', 'sym_id', 'entry_date', 'exit_date', 'side', 'score', 'tier',
                  'ct', 'ern', 'premium_cost', 'option_pnl', 'outcome',
                  'entry_value', 'entry_peak', 'entry_dd',
                  'entry_open_calls', 'entry_open_puts')
    rows = []
    for seed, r in zip(seeds, results):
        tape = r.get('_tape')
        if not tape:
            continue
        for t in tape:
            rows.append((seed,) + t)

    ep_cols = ('seed', 'window_label', 'rank',
               'start_date', 'trough_date', 'peak_value', 'trough_value', 'magnitude',
               'final_value', 'max_dd')
    ep_rows = []
    n_days = len(trading_days)
    def _idx_to_date(i):
        if 0 <= i < n_days:
            d = trading_days[i]
            return d.isoformat() if hasattr(d, 'isoformat') else str(d)
        return None
    for seed, r in zip(seeds, results):
        eps = r.get('_episodes', [])
        for rank, (s_idx, t_idx, peak, trough) in enumerate(eps):
            mag = (peak - trough) / peak if peak > 0 else 0
            ep_rows.append((seed, label, rank,
                            _idx_to_date(s_idx), _idx_to_date(t_idx),
                            peak, trough, mag,
                            r['final'], r['max_dd']))

    try:
        import polars as pl
    except ImportError:
        # Fallback: write JSON
        import json
        out_json = out_dir / f'tape_{label}.json'
        with open(out_json, 'w') as f:
            json.dump({'trades': [list(r) for r in rows], 'episodes': ep_rows,
                       'cols': {'trades': trade_cols, 'episodes': ep_cols}}, f, default=str)
        print(f"  [tape] wrote {len(rows):,} trades + {len(ep_rows)} episodes -> {out_json}")
        return

    # Polars DataFrames
    if rows:
        # Convert dates to ISO strings for parquet portability
        rows_str = [(r[0], r[1],
                     r[2].isoformat() if hasattr(r[2], 'isoformat') else str(r[2]),
                     r[3].isoformat() if hasattr(r[3], 'isoformat') else str(r[3]),
                     ) + r[4:] for r in rows]
        df = pl.DataFrame(rows_str, schema=list(trade_cols), orient='row')
        out_pq = out_dir / f'tape_{label}.parquet'
        df.write_parquet(out_pq)
        print(f"  [tape] wrote {len(df):,} trades -> {out_pq}")

    if ep_rows:
        df_ep = pl.DataFrame(ep_rows, schema=list(ep_cols), orient='row')
        out_pq = out_dir / f'episodes_{label}.parquet'
        df_ep.write_parquet(out_pq)
        print(f"  [tape] wrote {len(df_ep):,} episodes -> {out_pq}")


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

    if MAX_PUTS_PER_DAY > 0 and put_sigs:
        from collections import defaultdict as _dd
        by_date = _dd(list)
        for s in put_sigs:
            by_date[s.date].append(s)
        kept, total_dropped, capped_days = [], 0, 0
        for date_sigs in by_date.values():
            date_sigs.sort(key=lambda s: s.overall)  # lower overall = more bearish = higher priority
            kept.extend(date_sigs[:MAX_PUTS_PER_DAY])
            dropped = max(0, len(date_sigs) - MAX_PUTS_PER_DAY)
            total_dropped += dropped
            if dropped > 0:
                capped_days += 1
        put_sigs = sorted(kept, key=lambda s: (s.date, s.overall))
        if total_dropped > 0:
            print(f"  MAX_PUTS_PER_DAY={MAX_PUTS_PER_DAY}: dropped {total_dropped} puts on {capped_days} high-density days; remaining: {len(put_sigs)}")

    regime_dates, regime_map = load_regime_map(d_start, d_end)
    if BREADTH_ALLOC_ENABLED:
        stress_info = (f" | STRESS cut: <{F3F_PUT_STRESS_THRESH:.0f}→{F3F_PUT_STRESS_FLOOR:.2f} at {F3F_PUT_STRESS_LOW:.0f}"
                       if F3F_PUT_STRESS_THRESH > 0 else "")
        print(f"Alloc map (F3f breadth): {len(regime_map)} dates  |  "
              f"call: 1.0 if brd>=  {F3F_CALL_THRESH:.0f} else linear to {F3F_CALL_FLOOR:.2f} at {F3F_CALL_LOW:.0f}  |  "
              f"put: 1.0 if brd<={F3F_PUT_THRESH:.0f} else linear to {F3F_PUT_FLOOR:.2f} at {F3F_PUT_HIGH:.0f}{stress_info}")
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
        ern_rows = list(EarningsDate.select(
                EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                        .where(EarningsDate.symbol.in_(list(cal_syms)),
                               EarningsDate.date >= d_start - timedelta(days=10),
                               EarningsDate.date <= d_end + timedelta(days=EARN_BOOST_CALL_DAYS*2 + 7))
                        .order_by(EarningsDate.symbol, EarningsDate.date)
                        .tuples())
        from iv_crush_model import compute_effective_date as _eff
        ern_by_sym = defaultdict(list)
        for sym, d, ct in ern_rows:
            ern_by_sym[sym].append(_eff(d, ct))   # AMC shifted forward

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

    # Build earnings map (effective dates — AMC shifted to next trading day) for:
    #   - find_spanning_earnings → vega application in option_pricing-aware resolve()
    #   - EARN_SUPP_PUT (portfolio-stage put suppression)
    #   - EARN_BOOST_CALL (portfolio-stage call cascade promotion)
    # Always loaded — option-aware MC needs it for vega sampling regardless of
    # the legacy IV_CRUSH_ENABLED flag (which now only controls the post-hoc
    # iv_adjust_outcome wrapper, no longer relevant to the inline path).
    ern_map_for_iv = None
    all_syms = {s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs}
    if all_syms:
        iv_ern_rows = list(EarningsDate.select(
                EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                           .where(EarningsDate.symbol.in_(list(all_syms)),
                                  EarningsDate.date >= d_start - timedelta(days=10),
                                  EarningsDate.date <= d_end + timedelta(days=HOLD_DAYS*2 + 14))
                           .order_by(EarningsDate.symbol, EarningsDate.date)
                           .tuples())
        from iv_crush_model import compute_effective_date
        ern_map_for_iv = defaultdict(list)
        for sym, d, ct in iv_ern_rows:
            eff = compute_effective_date(d, ct, trading_days)
            ern_map_for_iv[sym].append(eff)
        # Sort + dedupe (AMC shift may collide an AMC event with a later BMO event)
        for sym in list(ern_map_for_iv.keys()):
            ern_map_for_iv[sym] = sorted(set(ern_map_for_iv[sym]))
        amc_count = sum(1 for _, _, ct in iv_ern_rows if ct and str(ct) >= '16:00:00')
        print(f"  Earnings map: {len(ern_map_for_iv)} symbols, {len(iv_ern_rows)} events ({amc_count} AMC shifted to next trading day)")

    print("Precomputing call outcomes...", end=' ', flush=True)
    call_outcomes = precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map,
                                        ern_map=ern_map_for_iv, trading_days=trading_days)
    both_n = sum(1 for o in call_outcomes.values() if o['kind'] == 'both')
    tp_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'tp')
    sl_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'sl')
    hard_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'hard')
    ct = len(call_outcomes) or 1
    print(f"N={len(call_outcomes)}  TP={tp_only/ct*100:.1f}%  SL={sl_only/ct*100:.1f}%  "
          f"Both={both_n/ct*100:.1f}%  Hard={hard_only/ct*100:.1f}%")

    print("Precomputing put outcomes... ", end=' ', flush=True)
    put_outcomes = precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map,
                                           ern_map=ern_map_for_iv, trading_days=trading_days)
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
    # Each iteration is independent (different RNG, different intraday-fill
    # realization). Per-iter RNG dispersion replaces the old 3-mode
    # Conservative/Realistic/Optimistic system — single seeded run gives
    # the variance distribution.
    USE_MP = os.environ.get('MC_NO_MP', '0') != '1'
    n_workers = int(os.environ.get('MC_WORKERS', '0')) or os.cpu_count() or 4

    pool = None
    if USE_MP and N_ITER >= 16:
        pool = multiprocessing.Pool(
            processes=min(n_workers, N_ITER),
            initializer=_mc_init_worker,
            initargs=(trading_days, calls_by_date, call_outcomes,
                      puts_by_date, put_outcomes, regime_dates, regime_map),
        )

    finals=[]; dds=[]; ctps=[]; ptps=[]; ctrd=[]; ptrd=[]; collapses=0
    seeds = [1000 * hash(label) + it for it in range(N_ITER)]
    if pool is not None:
        rs = pool.map(_mc_iter_worker, seeds, chunksize=max(1, N_ITER // (n_workers * 4)))
    else:
        rs = []
        for s in seeds:
            rng = random.Random(s)
            rs.append(run_single_sim(trading_days, calls_by_date, call_outcomes,
                                     puts_by_date, put_outcomes, rng,
                                     regime_dates, regime_map,
                                     collect_tape=TRADE_TAPE_ENABLED))
    for r in rs:
        finals.append(r['final']); dds.append(r['max_dd'])
        ctps.append(r['call_tp']); ptps.append(r['put_tp'])
        ctrd.append(r['call_trades']); ptrd.append(r['put_trades'])
        if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
            collapses += 1

    # Dump trade tape + DD episodes to parquet (one file per window)
    if TRADE_TAPE_ENABLED:
        _dump_trade_tape(label, seeds, rs, trading_days)

    mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
    med_ret  = (statistics.median(finals) / STARTING_CASH - 1) * 100
    mean_dd  = statistics.mean(dds) * 100
    worst_dd = max(dds) * 100
    p_coll   = collapses / N_ITER * 100

    result = dict(
        mean_ret=mean_ret, med_ret=med_ret,
        mean_dd=mean_dd, worst_dd=worst_dd, p_coll=p_coll,
        call_tp=statistics.mean(ctps), put_tp=statistics.mean(ptps),
        call_trades=statistics.mean(ctrd), put_trades=statistics.mean(ptrd),
        mean_final=statistics.mean(finals),
    )
    print(f"{'seeded':<13}  {result['call_tp']:>4.1f}%  {result['put_tp']:>4.1f}%  "
          f"{result['call_trades']:>5.1f}  {result['put_trades']:>5.1f}  "
          f"{mean_ret:>+13.1f}%  {med_ret:>+13.1f}%  "
          f"{worst_dd:>7.1f}%  {mean_dd:>6.1f}%  {p_coll:>6.1f}%")

    if pool is not None:
        pool.close()
        pool.join()

    # Return results dict keyed by single mode for back-compat with downstream
    # summary tables that loop over keys. 'seeded' is the only mode now.
    return {'seeded': result}


# ---- Main -------------------------------------------------------------------

def main():
    print('='*100)
    print("MONTE CARLO - Canonical Optimal Strategy")
    print('='*100)
    print(f"Strategy : 30 DTE | breadth-adaptive (brd<=50) | Hard=-50%@day15")
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
    if DD_SOFT_BAND_HI > DD_SOFT_BAND_LO:
        print(f"DD soft  : H3 — scale call alloc 1.0->{DD_SOFT_CALL_FLOOR:.2f} linearly when DD in [{DD_SOFT_BAND_LO:.2f}, {DD_SOFT_BAND_HI:.2f}] (calls only; shipped 2026-05-04)")
    print(f"         : Primary threshold: {PRIMARY_THRESHOLD}+  |  Overflow: {OVERFLOW_THRESHOLD}-74")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"Sigma    : TP base={TP_SIGMA_BASE:.3f}/stressed={TP_SIGMA_STRESS:.3f}  "
          f"SL base={SL_SIGMA_BASE:.3f}/stressed={SL_SIGMA_STRESS:.3f}")
    print(f"Modes    : seeded (single mode; per-iter random fill within bar replaces 3-mode collision)")
    if CT_PROMOTE:
        print(f"CT       : ENABLED  put_trend>={CT_PUT_TREND_MIN}->{CT_PUT_TIER}  "
              f"call_trend<={CT_CALL_TREND_MAX}->{CT_CALL_TIER}")
    else:
        print(f"CT       : OFF (baseline)")

    pin = os.environ.get('ALGORITHM_VERSION_PIN', '').strip()
    if pin:
        # Match by exact commit or unique prefix
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
    print('\n' + '='*100)
    print("SUMMARY - Mean Return / Worst DD / P(collapse) by Window")
    print('='*100)
    print(f"{'Window':<10} {'MeanRet':>20} {'MedRet':>16} {'WorstDD':>10} {'MeanDD':>9} {'P(coll)':>9}")
    print('-'*100)
    for label, _, _ in _windows:
        r = all_results[label]['seeded']
        print(f"{label:<10} {r['mean_ret']:>+18,.1f}%  "
              f"{r['med_ret']:>+14,.1f}%  "
              f"{r['worst_dd']:>9.1f}%  "
              f"{r['mean_dd']:>8.1f}%  "
              f"{r['p_coll']:>8.1f}%")


if __name__ == '__main__':
    main()
