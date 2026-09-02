#!/usr/bin/env python3
"""
Deterministic historical backtest — Cascade Allocation Strategy

Replays every actual scored signal from the database in chronological order.
For each signal, walks real OHLCV data forward to determine whether the trade
hit TP, SL, or the day-15 hard sell (breadth-adaptive barriers — see Exit rules below).

No Monte Carlo, no synthetic signals, no assumed win rates.
The TP/SL rates emerge from actual price history.
Every run produces the same equity curve (fully deterministic).

Portfolio mechanics (from CLAUDE.md):
  - Instruments: ATM 30d calls (score >= min_score) AND ATM 30d puts (score <= 25),
                 entered at close on signal date
  - Call cascade (% of current portfolio value):
        85+   → 15%   (90+ and 85-89 merged — 85-89 EV > 90-94)
        80-84 → 12%
        75-79 → 12%
        70-74 →  5%   (overflow — filled after all 75+ slots)
  - Put cascade:
        <=15  → 15%   (extreme put)
        16-20 → 12%
        21-25 → 12%
  - Max 14 concurrent positions (shared pool; calls fill each day first)
  - Re-entry blocked while same symbol already has an open position (either side)
  - Tiebreak: calls (score desc) before puts (score asc), then symbol ascending

Exit rules:
  Calls — breadth-adaptive (same signal drives BOTH TP and SL):
    breadth_score ≤ 50 ("stressed"):
      TP: intraday high ≥ entry × (1 + 1.274 × σ_daily)   (+35% premium)
      SL: intraday low  ≤ entry × (1 − 1.456 × σ_daily)   (−40% premium)
    breadth_score > 50 ("healthy"):
      TP: intraday high ≥ entry × (1 + 1.092 × σ_daily)   (+30% premium)
      SL: intraday low  ≤ entry × (1 − 1.274 × σ_daily)   (−35% premium)
  Puts — fixed (no breadth switch):
    TP: intraday low  ≤ entry × (1 − 1.092 × σ_daily)   (+30% premium)
    SL: intraday high ≥ entry × (1 + 0.728 × σ_daily)   (−20% premium; tight)

  Hard sell: first trading day on or after entry_date + 15 calendar days
  If TP and SL both breach on the same bar, TP wins (consistent with
  assess_scores.py convention: intraday high used for call win detection).

Net option P&L (per-exit slippage — entry −1%, TP 0% limit sell, SL −1.3%,
hard −0.5%):
  TP base    → +29.0%   TP stressed  → +34.0%
  SL base    → −37.3%   SL stressed  → −42.3%
  Hard       → −51.5%

σ_daily = 60-bar realized stdev of daily returns at signal date.

Data barrier: signals before 2020-01-01 are excluded regardless of DB history.

Usage:
  python backtest_cascade.py
  python backtest_cascade.py --capital 100000
  python backtest_cascade.py --min-score 75
  python backtest_cascade.py --from 2022-01-01
  python backtest_cascade.py --from 2022-01-01 --to 2022-12-31
  python backtest_cascade.py --version 14
"""

import argparse
import bisect
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Strategy constants — sourced from strategy_config.STRATEGY_15DTE.
# 15 DTE C1 SHIPPED 2026-04-28 (Phase 15B). Drift-guarded by
# tests/test_strategy_config_drift.py.
# ---------------------------------------------------------------------------
import strategy_config as _sc
_cfg = _sc.STRATEGY_15DTE
_opt = _cfg.option

INITIAL_CAPITAL     = 50_000.0
MAX_POSITIONS       = _cfg.MAX_POSITIONS    # 8 (vs 14 for 30 DTE — DD safety)
HOLD_CALENDAR_DAYS  = _cfg.HOLD_DAYS        # 7 (15 DTE half-DTE hard sell)

# Option-percentage TP/SL targets (drive the σ-barriers via PREMIUM_MULT/DELTA).
# Aliased to SHARED_OPTION — same as 30 DTE under current ship.
TP_BASE             = _opt.TP_BASE
TP_STRESS           = _opt.TP_STRESS
SL_BASE             = _opt.SL_BASE
SL_STRESS           = _opt.SL_STRESS

# Derived: σ-barriers (computed @property; auto-derives from PREMIUM_MULT=1.29
# for 15 DTE vs 1.82 for 30 DTE — produces tighter σ for the same option TP/SL).
TP_SIGMA_BASE       = _cfg.TP_SIGMA_BASE        # 0.903 = 0.35 * 1.29 / 0.5
TP_SIGMA_STRESS     = _cfg.TP_SIGMA_STRESS      # 1.032 = 0.40 * 1.29 / 0.5
SL_SIGMA_BASE       = _cfg.SL_SIGMA_BASE        # 0.774 = 0.30 * 1.29 / 0.5
SL_SIGMA_STRESS     = _cfg.SL_SIGMA_STRESS      # 0.903 = 0.35 * 1.29 / 0.5

BREADTH_THRESHOLD   = _opt.BREADTH_THRESHOLD

# Regime-aware allocation (asymmetric CUT_ONLY).
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

# Net option P&L after per-exit slippage — derived properties. Used as the
# legacy static-pricing fallback when OPTION_PRICING_AWARE=0; default path
# computes realized P&L bar-by-bar via option_pricing.option_pnl_pct.
NET_TP_BASE     = _opt.NET_TP_BASE         # +0.340
NET_TP_STRESS   = _opt.NET_TP_STRESS       # +0.390
NET_SL_BASE     = _opt.NET_SL_BASE         # -0.323
NET_SL_STRESS   = _opt.NET_SL_STRESS       # -0.373
NET_HARD        = _cfg.NET_HARD_SELL       # -0.465 (15 DTE: -45% gross)

# Option pricing (delta + theta + sampled vega) — ported from backtest_cascade.py
# 2026-04-30. Mirrors the 30 DTE shipping (commit 3432fb8) but with total_dte=15
# so theta scales correctly: at bar 7 (half DTE) θ ≈ 1 - sqrt(8/15) ≈ -0.27.
OPTION_PRICING_AWARE = os.environ.get('OPTION_PRICING_AWARE', '1') == '1'
DELTA               = _opt.DELTA
SLIP_ENTRY          = _opt.SLIP_ENTRY
SLIP_TP             = _opt.SLIP_TP
SLIP_SL             = _opt.SLIP_SL
SLIP_HARD           = -0.005
DEFAULT_TOTAL_DTE_15 = 15

# Dead-hold post-SL mechanism (Spec C, in flight 2026-04-30). See
# backtest_cascade.py and strategy_config.py for full description.
DEAD_HOLD_ENABLED      = (os.environ.get('DEAD_HOLD_ENABLED', '1' if _cfg.DEAD_HOLD_ENABLED else '0') == '1')
DEAD_HOLD_TRIGGER_PNL  = float(os.environ.get('DEAD_HOLD_TRIGGER_PNL', _cfg.DEAD_HOLD_TRIGGER_PNL))
DEAD_HOLD_POPOUT_PNL   = float(os.environ.get('DEAD_HOLD_POPOUT_PNL',  _cfg.DEAD_HOLD_POPOUT_PNL))

# Cascade allocation per score tier (display-key-mapped from semantic keys).
TIER_ALLOC = {
    '95+':    _cfg.TIER_ALLOC['ultra'],
    '85-94':  _cfg.TIER_ALLOC['top'],
    '80-84':  _cfg.TIER_ALLOC['mid'],
    '75-79':  _cfg.TIER_ALLOC['low'],
    '70-74':  _cfg.TIER_ALLOC['overflow'],
    'p<=15':  _cfg.PUT_TIER_ALLOC['put_top'],
    'p16-20': _cfg.PUT_TIER_ALLOC['put_mid'],
    'p21-25': _cfg.PUT_TIER_ALLOC['put_low'],
}

# Put-side fixed TP/SL (no breadth switch by default).
PUT_TP            = _opt.PUT_TP
PUT_SL            = _opt.PUT_SL
PUT_NET_TP        = _opt.PUT_NET_TP
PUT_NET_SL        = _opt.PUT_NET_SL
PUT_TP_SIGMA      = _cfg.PUT_TP_SIGMA       # 0.903 (15 DTE: 0.35 * 1.29 / 0.5)
PUT_SL_SIGMA      = _cfg.PUT_SL_SIGMA       # 0.516 (15 DTE: 0.20 * 1.29 / 0.5)
PUT_THRESHOLD     = _cfg.PUT_THRESHOLD

# Put SL hard-hold (Phase H1/H5: hold=0 ships).
PUT_SL_HOLD_BARS_DEFAULT = _opt.PUT_SL_HOLD_BARS_DEFAULT
PUT_SL_HOLD_BARS_MONDAY  = _opt.PUT_SL_HOLD_BARS_MONDAY

# Earnings-aware premium (variance-additive, shipped 2026-04-30). See
# monte_carlo_15dte.py "Earnings-aware premium" block for the full rationale.
# Signals that span an earnings event widen their σ-barriers (per-trade
# effective multiplier) so option-P&L semantics (TP=+30% on premium etc.)
# hold under the richer entry premium. NET_TP / NET_SL stay fixed
# (option-percentage P&L unchanged).
import math as _math  # local — only used for the variance-additive helper
PREMIUM_MULT_15 = 1.29
DTE_CAL_15      = 15
_N_PRIME_0      = 1.0 / _math.sqrt(2 * _math.pi)
_CAL_TO_TRADE   = 252.0 / 365.0
EARN_JUMP_PCT   = float(os.environ.get('EARN_JUMP_PCT', '9.3'))

def effective_premium_mult(sigma_pct: float, spans_earn: bool,
                           j_pct: float | None = None) -> float:
    """Per-trade effective premium multiplier. `j_pct` (per-stock cached
    earnings jump) used when supplied & positive, else falls back to
    universe EARN_JUMP_PCT."""
    if not spans_earn or sigma_pct <= 0:
        return PREMIUM_MULT_15
    j = j_pct if (j_pct is not None and j_pct > 0) else EARN_JUMP_PCT
    var_calm = (sigma_pct ** 2) * (DTE_CAL_15 * _CAL_TO_TRADE)
    return _N_PRIME_0 * _math.sqrt(var_calm + j ** 2) / sigma_pct


def _signal_spans_earnings(signal_date, ern_dates_for_sym) -> bool:
    if not ern_dates_for_sym:
        return False
    cutoff = signal_date + timedelta(days=DTE_CAL_15)
    for ed in ern_dates_for_sym:
        if signal_date < ed <= cutoff:
            return True
        if ed > cutoff:
            break
    return False

# Counter-trend cascade promotion (Path B / V2, shipped 2026-04-21).
CT_PROMOTE         = _cfg.CT_PROMOTE
CT_PUT_TREND_MIN   = _cfg.CT_PUT_TREND_MIN
CT_CALL_TREND_MAX  = _cfg.CT_CALL_TREND_MAX
CT_CALL_TIER       = '95+'    # display key for 'ultra' tier
CT_PUT_TIER        = 'p<=15'  # display key for 'put_top' tier

# Earnings-window put suppression — SHIPPED 2026-04-26.
EARN_SUPP_PUT          = _cfg.EARN_SUPP_PUT
EARN_SUPP_PUT_DAYS     = _cfg.EARN_SUPP_PUT_DAYS
EARN_SUPP_PUT_MIN_OV   = _cfg.EARN_SUPP_PUT_MIN_OV
EARN_SUPP_PUT_MAX_OV   = _cfg.EARN_SUPP_PUT_MAX_OV

# DD circuit breaker — 15 DTE C1: 0.60 (vs 30 DTE 0.68).
DD_CIRCUIT_BREAKER = _cfg.DD_CIRCUIT_BREAKER

# H3 — DD-soft band call alloc contraction. Disabled for 15 DTE by default
# (cfg fields are 0/0/1.0). Env-overridable for sweeps.
import os as _os_h3
DD_SOFT_BAND_LO    = float(_os_h3.environ.get('DD_SOFT_BAND_LO', str(_cfg.DD_SOFT_BAND_LO)))
DD_SOFT_BAND_HI    = float(_os_h3.environ.get('DD_SOFT_BAND_HI', str(_cfg.DD_SOFT_BAND_HI)))
DD_SOFT_CALL_FLOOR = float(_os_h3.environ.get('DD_SOFT_CALL_FLOOR', str(_cfg.DD_SOFT_CALL_FLOOR)))

# Data quality barrier — no signals before this date
MIN_DATE        = date(2016, 1, 1)

# Realized vol: 60 trading bars
VOL_BARS        = _cfg.VOL_LOOKBACK
MIN_VOL_BARS    = 20   # minimum bars to produce a vol estimate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def score_to_tier(score: float) -> str:
    if score >= 95:  return '95+'
    if score >= 85:  return '85-94'
    if score >= 80:  return '80-84'
    if score >= 75:  return '75-79'
    return '70-74'


def put_score_to_tier(score: float) -> str:
    if score <= 15: return 'p<=15'
    if score <= 20: return 'p16-20'
    return 'p21-25'  # 21-25


def ct_tag(overall: float, trend: float | None, side: str) -> str | None:
    """Return 'ct_call' / 'ct_put' / None per V2 thresholds.

    Mirrors monte_carlo.py::ct_tag. Trend missing -> no tag.
    """
    if not CT_PROMOTE or trend is None:
        return None
    if side == 'call' and overall >= 70 and trend <= CT_CALL_TREND_MAX:
        return 'ct_call'
    if side == 'put' and overall <= 25 and trend >= CT_PUT_TREND_MIN:
        return 'ct_put'
    return None


def realized_vol_pct(closes: list) -> float | None:
    """60-bar realized daily stdev as % (None if insufficient data)."""
    if len(closes) < MIN_VOL_BARS:
        return None
    arr  = np.array(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    return float(np.std(rets)) * 100.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_signals(version_id: int, min_score: float,
                 from_date=None, to_date=None, flagged_only: bool = False) -> list:
    """All qualifying call-side score rows for the given algorithm version."""
    from database.models.core import AlgorithmVersion, Score, Stock
    version = AlgorithmVersion.get(AlgorithmVersion.id == version_id)
    where = (
        (Score.version == version)
        & (Score.overall >= min_score)
        & (Score.date   >= (from_date or MIN_DATE))
    )
    if to_date:
        where &= (Score.date <= to_date)
    q = Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
    if flagged_only:
        q = q.join(Stock, on=(Score.symbol == Stock.symbol)).where(where & (Stock.flagged == True))
    else:
        q = q.where(where)
    return list(q.order_by(Score.date, Score.overall.desc(), Score.symbol).namedtuples())


def load_put_signals(version_id: int, max_put_score: float = None,
                     from_date=None, to_date=None, flagged_only: bool = False) -> list:
    """Put-side signals (overall <= max_put_score, default PUT_THRESHOLD).

    When EARN_SUPP_PUT (shipped 2026-04-26), drops puts in
    [EARN_SUPP_PUT_MIN_OV, EARN_SUPP_PUT_MAX_OV] when an EarningsDate falls
    in (signal_date, signal_date + EARN_SUPP_PUT_DAYS trading days].
    """
    from database.models.core import AlgorithmVersion, Score, Stock
    threshold = max_put_score if max_put_score is not None else PUT_THRESHOLD
    version = AlgorithmVersion.get(AlgorithmVersion.id == version_id)
    where = (
        (Score.version == version)
        & (Score.overall <= threshold)
        & (Score.date   >= (from_date or MIN_DATE))
    )
    if to_date:
        where &= (Score.date <= to_date)
    q = Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
    if flagged_only:
        q = q.join(Stock, on=(Score.symbol == Stock.symbol)).where(where & (Stock.flagged == True))
    else:
        q = q.where(where)
    sigs = list(q.order_by(Score.date, Score.overall.asc(), Score.symbol).namedtuples())

    if EARN_SUPP_PUT and sigs:
        sigs = _earnings_suppress_puts(sigs)
    return sigs


def _earnings_suppress_puts(sigs):
    """Filter put signals where an earnings event falls within the suppression window."""
    from database.models.core import EarningsDate
    from database.utils.trading_calendar import is_trading_day
    from collections import defaultdict
    syms = {s.symbol for s in sigs}
    if not syms:
        return sigs
    d_min = min(s.date for s in sigs)
    d_max = max(s.date for s in sigs)
    ed_rows = list(EarningsDate
                   .select(EarningsDate.symbol, EarningsDate.date)
                   .where((EarningsDate.symbol.in_(list(syms)))
                          & (EarningsDate.date >= d_min - timedelta(days=10))
                          & (EarningsDate.date <= d_max + timedelta(days=EARN_SUPP_PUT_DAYS * 2 + 7)))
                   .order_by(EarningsDate.symbol, EarningsDate.date)
                   .tuples())
    ed_map = defaultdict(list)
    for sym, d in ed_rows:
        ed_map[sym].append(d)

    def _fwd_n(d, n):
        out = d
        while n > 0:
            out += timedelta(days=1)
            if is_trading_day(out):
                n -= 1
        return out

    kept = []
    dropped = 0
    for s in sigs:
        ov = int(s.overall)
        if not (EARN_SUPP_PUT_MIN_OV <= ov <= EARN_SUPP_PUT_MAX_OV):
            kept.append(s); continue
        sym_ed = ed_map.get(s.symbol, [])
        if not sym_ed:
            kept.append(s); continue
        win_end = _fwd_n(s.date, EARN_SUPP_PUT_DAYS)
        if any(s.date < ed <= win_end for ed in sym_ed):
            dropped += 1
            continue
        kept.append(s)
    if dropped:
        print(f"  EARN_SUPP_PUT: dropped {dropped} puts in [{EARN_SUPP_PUT_MIN_OV},{EARN_SUPP_PUT_MAX_OV}] within {EARN_SUPP_PUT_DAYS} trd days of upcoming earnings ({len(kept)} remain)")
    return kept


def load_breadth_map(earliest: date):
    """Return (sorted_dates, {date: breadth_score}) for stress-detection."""
    from database.models.core import MarketBreadth
    rows = (MarketBreadth
            .select(MarketBreadth.date, MarketBreadth.breadth_score)
            .where(
                (MarketBreadth.date >= earliest - timedelta(days=60))
                & (MarketBreadth.breadth_score.is_null(False))
            )
            .order_by(MarketBreadth.date)
            .tuples())
    m = {d: float(bs) for d, bs in rows}
    return sorted(m.keys()), m


def is_stressed(sorted_dates, bmap, d) -> bool:
    """breadth_score on-or-before d <= threshold -> stressed regime."""
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0:
        return False
    return bmap[sorted_dates[idx]] <= BREADTH_THRESHOLD


def load_regime_map(earliest: date, breadth_enabled: bool | None = None):
    """Return (sorted_dates, {date: alloc_scalar}).

    When breadth_enabled (default = module-level BREADTH_ALLOC_ENABLED) is
    True, returns breadth_score per date (F3f knob). Otherwise returns
    regime_multiplier per date (legacy). The function name is preserved
    for call-site compatibility; semantics are determined by the flag and
    must match the consumer in alloc_scale_for.
    """
    if breadth_enabled is None:
        breadth_enabled = BREADTH_ALLOC_ENABLED
    from database.models.core import MarketRegime, MarketBreadth
    if breadth_enabled:
        rows = (MarketBreadth
                .select(MarketBreadth.date, MarketBreadth.breadth_score)
                .where(
                    (MarketBreadth.date >= earliest - timedelta(days=60))
                    & (MarketBreadth.breadth_score.is_null(False))
                )
                .order_by(MarketBreadth.date)
                .tuples())
        m = {d: float(brd) for d, brd in rows}
    else:
        rows = (MarketRegime
                .select(MarketRegime.date, MarketRegime.regime_multiplier)
                .where(
                    (MarketRegime.date >= earliest - timedelta(days=60))
                    & (MarketRegime.regime_multiplier.is_null(False))
                )
                .order_by(MarketRegime.date)
                .tuples())
        m = {d: float(mult) for d, mult in rows}
    return sorted(m.keys()), m


def regime_on_or_before(sorted_dates, rmap, d, breadth_enabled: bool | None = None) -> float:
    """Return alloc scalar on-or-before d; neutral default if no coverage."""
    if breadth_enabled is None:
        breadth_enabled = BREADTH_ALLOC_ENABLED
    neutral = 50.0 if breadth_enabled else 1.0
    if not sorted_dates:
        return neutral
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0:
        return neutral
    return rmap[sorted_dates[idx]]


def _breadth_alloc_scale(breadth: float, is_put: bool) -> float:
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


def alloc_scale_for(value: float, is_put: bool = False,
                    params: dict | None = None) -> float:
    """alloc multiplier, clamped [floor, ceil].

    When breadth_alloc_enabled (params['breadth_alloc_enabled'] or module
    default), `value` is a breadth_score and the F3f curves apply. params
    keys (all optional, fall back to module globals):
        breadth_alloc_enabled, f3f_call_thresh, f3f_call_floor, f3f_call_low,
        f3f_put_thresh, f3f_put_floor, f3f_put_high
    Otherwise `value` is a regime_multiplier and the legacy asymmetric slope
    logic applies.
    """
    p = params or {}
    breadth_enabled = p.get('breadth_alloc_enabled', BREADTH_ALLOC_ENABLED)
    if breadth_enabled:
        if value is None:
            return 1.0
        if is_put:
            pt = p.get('f3f_put_thresh', F3F_PUT_THRESH)
            pf = p.get('f3f_put_floor',  F3F_PUT_FLOOR)
            ph = p.get('f3f_put_high',   F3F_PUT_HIGH)
            if value <= pt:
                s = 1.0
            elif value >= ph:
                s = pf
            else:
                s = 1.0 - (value - pt) / (ph - pt) * (1.0 - pf)
        else:
            ct = p.get('f3f_call_thresh', F3F_CALL_THRESH)
            cf = p.get('f3f_call_floor',  F3F_CALL_FLOOR)
            cl = p.get('f3f_call_low',    F3F_CALL_LOW)
            if value >= ct:
                s = 1.0
            elif value <= cl:
                s = cf
            else:
                s = cf + (value - cl) / (ct - cl) * (1.0 - cf)
        return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, s))

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
    scale = 1.0 + slope * delta
    return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, scale))


class BarSeries(list):
    """List of OHLCV namedtuples with cached numpy views for JIT-friendly walks.

    Behaves like a list (existing code uses ph_rows[i].high etc.) but also
    exposes np_highs / np_lows / np_closes / np_ords for vectorized passes.
    Arrays are built once per symbol; subsequent compute_outcome calls reuse.
    """
    __slots__ = ('np_highs', 'np_lows', 'np_closes', 'np_ords')

    def __init__(self, rows):
        super().__init__(rows)
        if rows:
            import numpy as _np
            self.np_highs  = _np.array([float(r.high)  for r in rows], dtype=_np.float64)
            self.np_lows   = _np.array([float(r.low)   for r in rows], dtype=_np.float64)
            self.np_closes = _np.array([float(r.close) for r in rows], dtype=_np.float64)
            self.np_ords   = _np.array([r.date.toordinal() for r in rows], dtype=_np.int64)
        else:
            import numpy as _np
            self.np_highs = _np.empty(0, dtype=_np.float64)
            self.np_lows  = _np.empty(0, dtype=_np.float64)
            self.np_closes = _np.empty(0, dtype=_np.float64)
            self.np_ords  = _np.empty(0, dtype=_np.int64)


def load_price_history(symbols: set, earliest: date) -> dict:
    """Bulk-load OHLCV for all symbols.

    Returns {symbol: BarSeries} where BarSeries is a list of namedtuples
    extended with cached numpy arrays for JIT walks.
    Loads from (earliest − VOL_BARS trading days buffer) to cover vol lookback.
    """
    from database.models.core import Stock
    from database.models.technical import PriceHistory

    buffer_date = earliest - timedelta(days=VOL_BARS * 2)

    raw = defaultdict(list)
    syms = list(symbols)

    chunk = 100
    for start in range(0, len(syms), chunk):
        batch = syms[start:start + chunk]
        rows  = (PriceHistory
                 .select(PriceHistory.date,
                         PriceHistory.open,
                         PriceHistory.high,
                         PriceHistory.low,
                         PriceHistory.close,
                         Stock.symbol)
                 .join(Stock)
                 .where(
                     (Stock.symbol.in_(batch))
                     & (PriceHistory.date >= buffer_date)
                 )
                 .order_by(Stock.symbol, PriceHistory.date)
                 .namedtuples())
        for r in rows:
            raw[r.symbol].append(r)

    # Wrap each symbol's bars with cached numpy arrays
    return {sym: BarSeries(rows) for sym, rows in raw.items()}


# ---------------------------------------------------------------------------
# Trade outcome computation
# ---------------------------------------------------------------------------
@dataclass
class TradeOutcome:
    symbol:      str
    signal_date: date
    score:       float
    tier:        str
    entry_price: float
    sigma_daily: float   # 60-bar realized vol %
    outcome:     str     # 'tp' | 'sl' | 'hard'
    exit_date:   date
    net_return:  float   # breadth-adaptive NET_TP/SL or NET_HARD
    hold_bars:   int     # trading bars held
    stressed:    bool    # breadth regime at entry
    side:        str            = 'call'  # 'call' or 'put'
    exit_price:  float          = 0.0    # underlying price at exit bar (close, or barrier hit)
    tp_price:    float          = 0.0    # TP barrier price
    sl_price:    float          = 0.0    # SL barrier price
    deadline:    Optional[date] = None   # hard-sell deadline (signal_date + hold_days)


def compute_outcome(symbol: str, signal_date: date, score: float,
                    ph_rows: list, stressed: bool,
                    trend: float | None = None,
                    cfg: dict | None = None,
                    spans_earn: bool = False,
                    j_pct: float | None = None) -> 'TradeOutcome | None':
    """Walk OHLCV data from signal_date forward to determine trade outcome.

    `spans_earn`: True when an earnings event falls in the option's 15 cal-day
    life. Bumps the σ-barriers
    (and tp_price/sl_price) via the variance-additive entry-premium formula.
    """
    cfg = cfg or {}
    breadth_adaptive = cfg.get('breadth_adaptive', True)
    hold_days = cfg.get('hold_calendar_days', HOLD_CALENDAR_DAYS)
    net_hard  = cfg.get('net_hard', NET_HARD)

    date_idx = {r.date: i for i, r in enumerate(ph_rows)}

    sig_i = date_idx.get(signal_date)
    if sig_i is None:
        return None

    entry_price = float(ph_rows[sig_i].close)
    if entry_price <= 0:
        return None

    vol_start = max(0, sig_i - VOL_BARS)
    closes    = [float(ph_rows[j].close) for j in range(vol_start, sig_i + 1)]
    sigma     = realized_vol_pct(closes)
    if not sigma or sigma <= 0:
        return None

    if breadth_adaptive and stressed:
        tp_sigma = cfg.get('tp_sigma_stress', TP_SIGMA_STRESS)
        sl_sigma = cfg.get('sl_sigma_stress', SL_SIGMA_STRESS)
        net_tp   = cfg.get('net_tp_stress',   NET_TP_STRESS)
        net_sl   = cfg.get('net_sl_stress',   NET_SL_STRESS)
        tp_pct, sl_pct = TP_STRESS, SL_STRESS
    else:
        tp_sigma = cfg.get('tp_sigma_base', TP_SIGMA_BASE)
        sl_sigma = cfg.get('sl_sigma_base', SL_SIGMA_BASE)
        net_tp   = cfg.get('net_tp_base',   NET_TP_BASE)
        net_sl   = cfg.get('net_sl_base',   NET_SL_BASE)
        tp_pct, sl_pct = TP_BASE, SL_BASE

    # Earnings-aware premium: when the trade spans earnings, recompute the
    # σ-barriers from the per-trade effective premium multiplier so the
    # +30%-on-premium / −20%-on-premium semantics hold under the richer entry.
    if spans_earn:
        prem_mult = effective_premium_mult(sigma, True, j_pct)
        tp_sigma  = tp_pct        * prem_mult / 0.5   # DELTA = 0.5
        sl_sigma  = abs(sl_pct)   * prem_mult / 0.5

    tp_price = entry_price * (1.0 + tp_sigma * sigma / 100.0)
    sl_price = entry_price * (1.0 - sl_sigma * sigma / 100.0)
    deadline = signal_date + timedelta(days=hold_days)
    tier     = CT_CALL_TIER if ct_tag(score, trend, 'call') else score_to_tier(score)

    # Per-trade effective premium percentage (variance-additive when spans_earn).
    eff_prem_mult = effective_premium_mult(sigma, spans_earn, j_pct)
    premium_pct   = eff_prem_mult * sigma / 100.0

    sym_ed = (cfg.get('ern_map') or {}).get(symbol, [])
    def _vega_for(exit_date):
        # Vega only applies when an earnings event actually falls inside the
        # trade's realized window. Reuses spans_earn flag as a fast precheck
        # but verifies against true exit_date for correctness.
        if not spans_earn or not sym_ed:
            return 1.0
        try:
            from iv_crush_model import find_spanning_earnings
            from option_pricing import sample_vega_ratio
        except ImportError:
            return 1.0
        if find_spanning_earnings(signal_date, exit_date, sym_ed) is None:
            return 1.0
        import random, hashlib
        seed = int.from_bytes(hashlib.md5(
            f"{symbol}_{signal_date.toordinal()}".encode()).digest()[:4], 'little')
        rng = random.Random(seed)
        return sample_vega_ratio('CALL', DEFAULT_TOTAL_DTE_15, rng)

    def _option_aware_pnl(kind_code, bars_held, fire_idx, vega_ratio=1.0):
        """kind_code: 0=hard, 1=tp (mid-bar), 2=sl (bimodal — intraday at
        sl_price, gap-through mid of (low, open))."""
        from option_pricing import option_pnl_pct
        bar = ph_rows[fire_idx]
        u_high = float(bar.high) if hasattr(bar, 'high') else float(bar.close)
        u_low  = float(bar.low)  if hasattr(bar, 'low')  else float(bar.close)
        u_open = float(bar.open) if hasattr(bar, 'open') else u_low
        if kind_code == 2:
            # Call SL: bimodal. Intraday: open > sl_price.
            if u_open > sl_price:
                u_fill = sl_price
            else:
                u_fill = (u_low + u_open) / 2.0
        else:
            u_fill = (u_high + u_low) / 2.0
        gross = option_pnl_pct('call', u_fill, entry_price, bars_held,
                               premium_pct=premium_pct,
                               total_dte=DEFAULT_TOTAL_DTE_15,
                               vega_ratio=vega_ratio,
                               delta=DELTA)
        if kind_code == 1:   return gross + SLIP_ENTRY + SLIP_TP
        if kind_code == 2:   return gross + SLIP_ENTRY + SLIP_SL
        return gross + SLIP_ENTRY + SLIP_HARD

    # JIT path: bypassed when OPTION_PRICING_AWARE=1 (default) so theta + vega
    # are applied at fire time. Set OPTION_PRICING_AWARE=0 for legacy static-pricing JIT.
    if hasattr(ph_rows, 'np_highs') and not OPTION_PRICING_AWARE:
        from database.barrier_walk_numba import walk_call_option_outcome
        kind_code, bars_held, exit_px = walk_call_option_outcome(
            ph_rows.np_highs, ph_rows.np_lows, ph_rows.np_closes, ph_rows.np_ords,
            sig_i, tp_price, sl_price, deadline.toordinal()
        )
        if kind_code == 1:
            return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                                'tp', ph_rows[sig_i + bars_held].date, net_tp, bars_held, stressed,
                                exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if kind_code == 2:
            return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                                'sl', ph_rows[sig_i + bars_held].date, net_sl, bars_held, stressed,
                                exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if kind_code == 0:
            return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                                'hard', ph_rows[sig_i + bars_held].date, net_hard, bars_held, stressed,
                                exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        # kind_code == 3: exhausted, open
        return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                            'open', deadline, 0.0, bars_held, stressed,
                            exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)

    # Pure-Python fallback (also used when OPTION_PRICING_AWARE=1).
    for j in range(sig_i + 1, len(ph_rows)):
        bar      = ph_rows[j]
        bar_date = bar.date
        high     = float(bar.high)
        low      = float(bar.low)
        bars_held = j - sig_i
        if high >= tp_price:
            tp_pnl = _option_aware_pnl(1, bars_held, j, _vega_for(bar_date)) if OPTION_PRICING_AWARE else net_tp
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'tp', bar_date,
                                tp_pnl, bars_held, stressed,
                                exit_price=tp_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if low <= sl_price:
            sl_pnl = _option_aware_pnl(2, bars_held, j, _vega_for(bar_date)) if OPTION_PRICING_AWARE else net_sl
            # Dead-hold check (15 DTE calls).
            if DEAD_HOLD_ENABLED and sl_pnl <= DEAD_HOLD_TRIGGER_PNL:
                from option_pricing import option_pnl_pct
                for k in range(j + 1, len(ph_rows)):
                    bar_k = ph_rows[k]
                    bars_k = k - sig_i
                    high_k = float(bar_k.high)
                    open_k = float(bar_k.open) if hasattr(bar_k, 'open') else high_k
                    high_pnl_k = option_pnl_pct('call', high_k, entry_price, bars_k,
                                                premium_pct=premium_pct,
                                                total_dte=DEFAULT_TOTAL_DTE_15,
                                                vega_ratio=_vega_for(bar_k.date),
                                                delta=DELTA)
                    if high_pnl_k >= DEAD_HOLD_POPOUT_PNL:
                        open_pnl_k = option_pnl_pct('call', open_k, entry_price, bars_k,
                                                    premium_pct=premium_pct,
                                                    total_dte=DEFAULT_TOTAL_DTE_15,
                                                    vega_ratio=_vega_for(bar_k.date),
                                                    delta=DELTA)
                        fill_pnl = max(DEAD_HOLD_POPOUT_PNL, open_pnl_k)
                        return TradeOutcome(symbol, signal_date, score, tier,
                                            entry_price, sigma, 'dh_pop', bar_k.date,
                                            fill_pnl, bars_k, stressed,
                                            exit_price=high_k,
                                            tp_price=tp_price, sl_price=sl_price, deadline=deadline)
                    if bar_k.date >= deadline:
                        close_k = float(bar_k.close)
                        close_pnl = option_pnl_pct('call', close_k, entry_price, bars_k,
                                                   premium_pct=premium_pct,
                                                   total_dte=DEFAULT_TOTAL_DTE_15,
                                                   vega_ratio=_vega_for(bar_k.date),
                                                   delta=DELTA)
                        return TradeOutcome(symbol, signal_date, score, tier,
                                            entry_price, sigma, 'dh_expiry', bar_k.date,
                                            close_pnl, bars_k, stressed,
                                            exit_price=close_k,
                                            tp_price=tp_price, sl_price=sl_price, deadline=deadline)
                last = ph_rows[-1]
                last_bars = len(ph_rows) - 1 - sig_i
                last_pnl = option_pnl_pct('call', float(last.close), entry_price, last_bars,
                                          premium_pct=premium_pct,
                                          total_dte=DEFAULT_TOTAL_DTE_15,
                                          vega_ratio=_vega_for(last.date),
                                          delta=DELTA)
                return TradeOutcome(symbol, signal_date, score, tier,
                                    entry_price, sigma, 'dh_open', last.date,
                                    last_pnl, last_bars, stressed,
                                    exit_price=float(last.close),
                                    tp_price=tp_price, sl_price=sl_price, deadline=deadline)
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'sl', bar_date,
                                sl_pnl, bars_held, stressed,
                                exit_price=sl_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if bar_date >= deadline:
            hard_pnl = _option_aware_pnl(0, bars_held, j, _vega_for(bar_date)) if OPTION_PRICING_AWARE else net_hard
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'hard', bar_date,
                                hard_pnl, bars_held, stressed,
                                exit_price=float(bar.close),
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)

    last = ph_rows[-1]
    return TradeOutcome(symbol, signal_date, score, tier,
                        entry_price, sigma, 'open', deadline,
                        0.0, len(ph_rows) - 1 - sig_i, stressed,
                        exit_price=float(last.close),
                        tp_price=tp_price, sl_price=sl_price, deadline=deadline)


def compute_put_outcome(symbol: str, signal_date: date, score: float,
                        ph_rows: list,
                        trend: float | None = None,
                        cfg: dict | None = None,
                        spans_earn: bool = False,
                        j_pct: float | None = None) -> 'TradeOutcome | None':
    """Put trade: win = underlying falls PUT_TP_SIGMA sigmas; stop = rises PUT_SL_SIGMA.

    `spans_earn`: True when an earnings event falls in the option's 15 cal-day
    life. Bumps the σ-barriers.
    """
    cfg = cfg or {}
    hold_days       = cfg.get('hold_calendar_days',      HOLD_CALENDAR_DAYS)
    put_tp_sigma    = cfg.get('put_tp_sigma',             PUT_TP_SIGMA)
    put_sl_sigma = cfg.get('put_sl_sigma', PUT_SL_SIGMA)
    put_net_tp   = cfg.get('put_net_tp',   PUT_NET_TP)
    put_net_sl   = cfg.get('put_net_sl',   PUT_NET_SL)
    net_hard     = cfg.get('net_hard',     NET_HARD)

    date_idx = {r.date: i for i, r in enumerate(ph_rows)}
    sig_i = date_idx.get(signal_date)
    if sig_i is None:
        return None
    entry_price = float(ph_rows[sig_i].close)
    if entry_price <= 0:
        return None
    vol_start = max(0, sig_i - VOL_BARS)
    closes    = [float(ph_rows[j].close) for j in range(vol_start, sig_i + 1)]
    sigma     = realized_vol_pct(closes)
    if not sigma or sigma <= 0:
        return None

    if spans_earn:
        prem_mult    = effective_premium_mult(sigma, True, j_pct)
        put_tp_sigma = PUT_TP        * prem_mult / 0.5
        put_sl_sigma = abs(PUT_SL)   * prem_mult / 0.5

    tp_price = entry_price * (1.0 - put_tp_sigma * sigma / 100.0)
    sl_price = entry_price * (1.0 + put_sl_sigma * sigma / 100.0)
    deadline = signal_date + timedelta(days=hold_days)
    tier     = CT_PUT_TIER if ct_tag(score, trend, 'put') else put_score_to_tier(score)
    hold_default = cfg.get('put_sl_hold_default', PUT_SL_HOLD_BARS_DEFAULT)
    hold_monday  = cfg.get('put_sl_hold_monday',  PUT_SL_HOLD_BARS_MONDAY)
    sl_hold  = hold_monday if signal_date.weekday() == 0 else hold_default

    eff_prem_mult = effective_premium_mult(sigma, spans_earn, j_pct)
    premium_pct   = eff_prem_mult * sigma / 100.0

    sym_ed = (cfg.get('ern_map') or {}).get(symbol, [])
    def _vega_for(exit_date):
        if not spans_earn or not sym_ed:
            return 1.0
        try:
            from iv_crush_model import find_spanning_earnings
            from option_pricing import sample_vega_ratio
        except ImportError:
            return 1.0
        if find_spanning_earnings(signal_date, exit_date, sym_ed) is None:
            return 1.0
        import random, hashlib
        seed = int.from_bytes(hashlib.md5(
            f"{symbol}_{signal_date.toordinal()}".encode()).digest()[:4], 'little')
        rng = random.Random(seed)
        return sample_vega_ratio('PUT', DEFAULT_TOTAL_DTE_15, rng)

    def _put_option_aware_pnl(kind_code, bars_held, fire_idx, vega_ratio=1.0):
        """kind_code: 0=hard, 1=tp (mid-bar), 2=sl (bimodal — intraday at
        sl_price, gap-through mid of (open, high))."""
        from option_pricing import option_pnl_pct
        bar = ph_rows[fire_idx]
        u_high = float(bar.high) if hasattr(bar, 'high') else float(bar.close)
        u_low  = float(bar.low)  if hasattr(bar, 'low')  else float(bar.close)
        u_open = float(bar.open) if hasattr(bar, 'open') else u_high
        if kind_code == 2:
            # Put SL: bimodal. Intraday: open < sl_price.
            if u_open < sl_price:
                u_fill = sl_price
            else:
                u_fill = (u_open + u_high) / 2.0
        else:
            u_fill = (u_high + u_low) / 2.0
        gross = option_pnl_pct('put', u_fill, entry_price, bars_held,
                               premium_pct=premium_pct,
                               total_dte=DEFAULT_TOTAL_DTE_15,
                               vega_ratio=vega_ratio,
                               delta=DELTA)
        if kind_code == 1:   return gross + SLIP_ENTRY + SLIP_TP
        if kind_code == 2:   return gross + SLIP_ENTRY + SLIP_SL
        return gross + SLIP_ENTRY + SLIP_HARD

    # JIT path: bypassed when OPTION_PRICING_AWARE=1 (default).
    if hasattr(ph_rows, 'np_highs') and not OPTION_PRICING_AWARE:
        from database.barrier_walk_numba import walk_put_option_outcome
        kind_code, bars_held, exit_px = walk_put_option_outcome(
            ph_rows.np_highs, ph_rows.np_lows, ph_rows.np_closes, ph_rows.np_ords,
            sig_i, tp_price, sl_price, deadline.toordinal(), sl_hold
        )
        if kind_code == 1:
            return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                                'tp', ph_rows[sig_i + bars_held].date, put_net_tp, bars_held, False, 'put',
                                exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if kind_code == 2:
            return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                                'sl', ph_rows[sig_i + bars_held].date, put_net_sl, bars_held, False, 'put',
                                exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if kind_code == 0:
            return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                                'hard', ph_rows[sig_i + bars_held].date, net_hard, bars_held, False, 'put',
                                exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        # kind_code == 3: exhausted
        return TradeOutcome(symbol, signal_date, score, tier, entry_price, sigma,
                            'open', deadline, 0.0, bars_held, False, 'put',
                            exit_price=exit_px, tp_price=tp_price, sl_price=sl_price, deadline=deadline)

    # Pure-Python fallback (also used when OPTION_PRICING_AWARE=1).
    for j in range(sig_i + 1, len(ph_rows)):
        bar = ph_rows[j]
        bars_held = j - sig_i
        high = float(bar.high); low = float(bar.low)
        if low <= tp_price:
            tp_pnl = _put_option_aware_pnl(1, bars_held, j, _vega_for(bar.date)) if OPTION_PRICING_AWARE else put_net_tp
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'tp', bar.date,
                                tp_pnl, bars_held, False, 'put',
                                exit_price=tp_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if high >= sl_price and bars_held > sl_hold:
            sl_pnl = _put_option_aware_pnl(2, bars_held, j, _vega_for(bar.date)) if OPTION_PRICING_AWARE else put_net_sl
            # Dead-hold check (15 DTE puts).
            if DEAD_HOLD_ENABLED and sl_pnl <= DEAD_HOLD_TRIGGER_PNL:
                from option_pricing import option_pnl_pct
                for k in range(j + 1, len(ph_rows)):
                    bar_k = ph_rows[k]
                    bars_k = k - sig_i
                    low_k = float(bar_k.low)
                    open_k = float(bar_k.open) if hasattr(bar_k, 'open') else low_k
                    low_pnl_k = option_pnl_pct('put', low_k, entry_price, bars_k,
                                                premium_pct=premium_pct,
                                                total_dte=DEFAULT_TOTAL_DTE_15,
                                                vega_ratio=_vega_for(bar_k.date),
                                                delta=DELTA)
                    if low_pnl_k >= DEAD_HOLD_POPOUT_PNL:
                        open_pnl_k = option_pnl_pct('put', open_k, entry_price, bars_k,
                                                    premium_pct=premium_pct,
                                                    total_dte=DEFAULT_TOTAL_DTE_15,
                                                    vega_ratio=_vega_for(bar_k.date),
                                                    delta=DELTA)
                        fill_pnl = max(DEAD_HOLD_POPOUT_PNL, open_pnl_k)
                        return TradeOutcome(symbol, signal_date, score, tier,
                                            entry_price, sigma, 'dh_pop', bar_k.date,
                                            fill_pnl, bars_k, False, 'put',
                                            exit_price=low_k,
                                            tp_price=tp_price, sl_price=sl_price, deadline=deadline)
                    if bar_k.date >= deadline:
                        close_k = float(bar_k.close)
                        close_pnl = option_pnl_pct('put', close_k, entry_price, bars_k,
                                                   premium_pct=premium_pct,
                                                   total_dte=DEFAULT_TOTAL_DTE_15,
                                                   vega_ratio=_vega_for(bar_k.date),
                                                   delta=DELTA)
                        return TradeOutcome(symbol, signal_date, score, tier,
                                            entry_price, sigma, 'dh_expiry', bar_k.date,
                                            close_pnl, bars_k, False, 'put',
                                            exit_price=close_k,
                                            tp_price=tp_price, sl_price=sl_price, deadline=deadline)
                last = ph_rows[-1]
                last_bars = len(ph_rows) - 1 - sig_i
                last_pnl = option_pnl_pct('put', float(last.close), entry_price, last_bars,
                                          premium_pct=premium_pct,
                                          total_dte=DEFAULT_TOTAL_DTE_15,
                                          vega_ratio=_vega_for(last.date),
                                          delta=DELTA)
                return TradeOutcome(symbol, signal_date, score, tier,
                                    entry_price, sigma, 'dh_open', last.date,
                                    last_pnl, last_bars, False, 'put',
                                    exit_price=float(last.close),
                                    tp_price=tp_price, sl_price=sl_price, deadline=deadline)
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'sl', bar.date,
                                sl_pnl, bars_held, False, 'put',
                                exit_price=sl_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if bar.date >= deadline:
            hard_pnl = _put_option_aware_pnl(0, bars_held, j, _vega_for(bar.date)) if OPTION_PRICING_AWARE else net_hard
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'hard', bar.date,
                                hard_pnl, bars_held, False, 'put',
                                exit_price=float(bar.close),
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)

    # Price data exhausted before deadline — position is still open.
    last = ph_rows[-1]
    return TradeOutcome(symbol, signal_date, score, tier,
                        entry_price, sigma, 'open', deadline,
                        0.0, len(ph_rows) - 1 - sig_i, False, 'put',
                        exit_price=float(last.close),
                        tp_price=tp_price, sl_price=sl_price, deadline=deadline)


# ---------------------------------------------------------------------------
# Portfolio simulation (deterministic)
# ---------------------------------------------------------------------------
@dataclass
class OpenPosition:
    outcome:   TradeOutcome
    premium:   float        # dollar amount allocated (tier % × equity at entry)
    entry_eq:  float        # portfolio equity at entry (for reference)


def run_backtest(outcomes_by_date: dict,
                 trading_days: list,
                 initial_capital: float,
                 regime_dates: list | None = None,
                 regime_map: dict | None = None,
                 cfg: dict | None = None) -> dict:
    """
    outcomes_by_date: {signal_date: [TradeOutcome, ...]}
                      each list pre-sorted: score desc, symbol asc.
    trading_days:     sorted list of all trading dates in the window.
    """
    cfg        = cfg or {}
    max_pos    = cfg.get('max_positions', MAX_POSITIONS)
    tier_alloc = cfg.get('tier_alloc',   TIER_ALLOC)
    net_hard   = cfg.get('net_hard',     NET_HARD)

    # F3f / legacy alloc-scale params extracted once; passed to alloc_scale_for
    # on every signal.  Lookup keys mirror the module globals; missing keys fall
    # back to module defaults inside alloc_scale_for.
    _alloc_params = {k: cfg[k] for k in (
        'breadth_alloc_enabled',
        'f3f_call_thresh', 'f3f_call_floor', 'f3f_call_low',
        'f3f_put_thresh',  'f3f_put_floor',  'f3f_put_high',
    ) if k in cfg}
    _breadth_enabled = _alloc_params.get('breadth_alloc_enabled', BREADTH_ALLOC_ENABLED)

    cash: float             = initial_capital
    open_pos: list[OpenPosition] = []
    equity_curve: list      = []   # [(date, equity), ...]
    trade_log: list         = []
    peak_equity: float      = initial_capital
    max_dd: float           = 0.0

    for today in trading_days:
        # 1. Close positions whose exit_date has arrived (skip still-open outcomes)
        remaining = []
        for pos in open_pos:
            if pos.outcome.outcome != 'open' and pos.outcome.exit_date <= today:
                proceeds = pos.premium * (1.0 + pos.outcome.net_return)
                cash    += proceeds
                trade_log.append({
                    'entry_date':  pos.outcome.signal_date,
                    'exit_date':   pos.outcome.exit_date,
                    'symbol':      pos.outcome.symbol,
                    'score':       pos.outcome.score,
                    'tier':        pos.outcome.tier,
                    'sigma':       pos.outcome.sigma_daily,
                    'premium':     pos.premium,
                    'outcome':     pos.outcome.outcome,
                    'hold_bars':   pos.outcome.hold_bars,
                    'pnl':         proceeds - pos.premium,
                    'pnl_pct':     pos.outcome.net_return,
                    'stressed':    pos.outcome.stressed,
                    'side':        pos.outcome.side,
                    'entry_price': pos.outcome.entry_price,
                    'exit_price':  pos.outcome.exit_price,
                })
            else:
                remaining.append(pos)
        open_pos = remaining

        # 2. Mark-to-market (open positions marked at cost; realistic for options)
        equity = cash + sum(p.premium for p in open_pos)

        # 2b. DD circuit breaker (15 DTE C1, Phase 15B 2026-04-28).
        # When running portfolio DD > DD_CIRCUIT_BREAKER (0.60), pause new entries.
        # Existing positions still resolve normally; entries resume when DD recovers.
        cur_dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0
        if DD_CIRCUIT_BREAKER > 0.0 and cur_dd > DD_CIRCUIT_BREAKER:
            # Recovery safety valve: when breaker fires AND there are NO open
            # positions, the portfolio cannot recover (no exposure → no winning
            # trades → equity flat → DD stays > threshold forever). Reset peak
            # to current equity so the strategy can resume after a deep DD.
            # The breaker did its job: prevented compounding losses on existing
            # exposure. With no exposure, the breaker should release.
            if not open_pos:
                peak_equity = equity
                # Recompute cur_dd after reset; will be 0, breaker won't trip
                cur_dd = 0.0
            else:
                # Still have open positions — let breaker keep blocking
                if cur_dd > max_dd:
                    max_dd = cur_dd
                equity_curve.append((today, equity))
                continue

        # 3. Open new trades for today's signals
        open_syms = {p.outcome.symbol for p in open_pos}
        for outcome in outcomes_by_date.get(today, []):
            if len(open_pos) >= max_pos:
                break
            if outcome.symbol in open_syms:
                continue                      # re-entry block

            reg_mult = (regime_on_or_before(regime_dates, regime_map, today,
                                            breadth_enabled=_breadth_enabled)
                        if regime_dates else (50.0 if _breadth_enabled else 1.0))
            is_put = getattr(outcome, 'side', 'call') == 'put'
            reg_scale = alloc_scale_for(reg_mult, is_put=is_put, params=_alloc_params)
            alloc_frac = tier_alloc.get(outcome.tier, TIER_ALLOC.get(outcome.tier, 0.0))
            # H3: DD-soft-band call alloc contraction (calls only; disabled for 15 DTE by default)
            dd_scale = 1.0
            if (not is_put) and DD_SOFT_BAND_HI > DD_SOFT_BAND_LO and cur_dd > DD_SOFT_BAND_LO:
                if cur_dd >= DD_SOFT_BAND_HI:
                    dd_scale = DD_SOFT_CALL_FLOOR
                else:
                    t = (cur_dd - DD_SOFT_BAND_LO) / (DD_SOFT_BAND_HI - DD_SOFT_BAND_LO)
                    dd_scale = 1.0 - t * (1.0 - DD_SOFT_CALL_FLOOR)
            premium = alloc_frac * reg_scale * dd_scale * equity
            if cash < premium or premium < 10.0:
                continue

            cash    -= premium
            open_pos.append(OpenPosition(outcome=outcome,
                                         premium=premium,
                                         entry_eq=equity))
            open_syms.add(outcome.symbol)
            equity = cash + sum(p.premium for p in open_pos)

        # 4. Drawdown tracking
        if equity > peak_equity:
            peak_equity = equity
        dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

        equity_curve.append((today, equity))

    # Remaining open positions — do NOT force-close. Mark at cost (premium paid).
    # Return them separately so callers can display as open holdings rather than
    # injecting artificial hard-sell P&L into closed-trade statistics.
    open_holdings = []
    for pos in open_pos:
        open_holdings.append({
            'entry_date':    pos.outcome.signal_date,
            'hard_sell_date': pos.outcome.deadline,
            'symbol':        pos.outcome.symbol,
            'score':         pos.outcome.score,
            'tier':          pos.outcome.tier,
            'sigma':         pos.outcome.sigma_daily,
            'premium':       pos.premium,
            'side':          pos.outcome.side,
            'entry_price':   pos.outcome.entry_price,
            'current_price': pos.outcome.exit_price,
            'tp_price':      pos.outcome.tp_price,
            'sl_price':      pos.outcome.sl_price,
            'hold_bars':     pos.outcome.hold_bars,
        })

    return {
        'equity_curve':  equity_curve,
        'trade_log':     trade_log,
        'final_equity':  cash + sum(p.premium for p in open_pos),   # mark at cost
        'max_dd':        max_dd,
        'initial':       initial_capital,
        'open_holdings': open_holdings,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report(result: dict, start_date: date, end_date: date, min_score: float):
    trades   = result['trade_log']
    initial  = result['initial']
    terminal = result['final_equity']
    n_days   = (end_date - start_date).days
    years    = n_days / 365.25
    cagr     = (terminal / initial) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    call_trades = [t for t in trades if t.get('side', 'call') == 'call']
    put_trades  = [t for t in trades if t.get('side') == 'put']
    n_tp   = sum(1 for t in trades if t['outcome'] == 'tp')
    n_sl   = sum(1 for t in trades if t['outcome'] == 'sl')
    n_hard = sum(1 for t in trades if t['outcome'] == 'hard')
    n_tot  = len(trades)

    print()
    print("=" * 72)
    print(" HISTORICAL BACKTEST — Cascade Allocation Strategy")
    print("=" * 72)
    print(f"  Signal threshold : score >= {min_score:.0f} (call side only)")
    print(f"  Period:   {start_date} → {end_date}  ({n_days:,}d  /  {years:.1f}y)")
    print(f"  Capital:  ${initial:>10,.0f} → ${terminal:>12,.0f}")
    print(f"  CAGR:     {cagr*100:.1f}%")
    print(f"  Max DD:   {result['max_dd']*100:.1f}%")

    if n_tot == 0:
        print("  No trades executed.")
        return

    print()
    print(f"  Trades:  {n_tot:,} total  ({len(call_trades):,} calls, {len(put_trades):,} puts)")
    print(f"    TP:    {n_tp:>5,}  ({n_tp/n_tot*100:.1f}%)")
    print(f"    SL:    {n_sl:>5,}  ({n_sl/n_tot*100:.1f}%)")
    print(f"    Hard:  {n_hard:>5,}  ({n_hard/n_tot*100:.1f}%)")
    if put_trades:
        p_tp = sum(1 for t in put_trades if t['outcome'] == 'tp')
        p_sl = sum(1 for t in put_trades if t['outcome'] == 'sl')
        c_tp = sum(1 for t in call_trades if t['outcome'] == 'tp')
        c_sl = sum(1 for t in call_trades if t['outcome'] == 'sl')
        print(f"    Calls: TP={c_tp/len(call_trades)*100:.1f}%  SL={c_sl/len(call_trades)*100:.1f}%")
        print(f"    Puts:  TP={p_tp/len(put_trades)*100:.1f}%  SL={p_sl/len(put_trades)*100:.1f}%")

    # Break-even context (calm vs stressed regime differ slightly)
    be_calm = abs(NET_SL_BASE)   / (NET_TP_BASE   + abs(NET_SL_BASE))
    be_str  = abs(NET_SL_STRESS) / (NET_TP_STRESS + abs(NET_SL_STRESS))
    observed_tp = n_tp / n_tot
    n_stress_trades = sum(1 for t in trades if t.get('stressed'))
    print(f"  Break-even TP rate: calm={be_calm*100:.1f}%  stressed={be_str*100:.1f}%  |  "
          f"Observed: {observed_tp*100:.1f}%  |  "
          f"Stressed entries: {n_stress_trades/n_tot*100:.1f}%")

    # By tier
    print()
    print(f"  {'Tier':<8}  {'Trades':>6}  {'TP%':>6}  {'SL%':>6}  "
          f"{'Alloc':>6}  {'Avg hold':>9}")
    print("  " + "-" * 52)
    for tier in TIER_ALLOC:
        tt = [t for t in trades if t['tier'] == tier]
        n  = len(tt)
        if n == 0:
            continue
        tp_r = sum(1 for t in tt if t['outcome'] == 'tp') / n
        sl_r = sum(1 for t in tt if t['outcome'] == 'sl') / n
        avg_hold = sum(t['hold_bars'] for t in tt) / n
        print(f"  {tier:<8}  {n:>6,}  {tp_r*100:>5.1f}%  {sl_r*100:>5.1f}%  "
              f"{TIER_ALLOC[tier]*100:>5.0f}%  {avg_hold:>7.1f}d")

    # By year
    print()
    print(f"  {'Year':<6}  {'Trades':>6}  {'TP%':>6}  {'Year-end equity':>16}")
    print("  " + "-" * 42)
    eq_by_date = dict(result['equity_curve'])
    years_seen = sorted({t['exit_date'].year for t in trades})
    prev_eq    = initial
    for yr in years_seen:
        yr_trades = [t for t in trades if t['exit_date'].year == yr]
        n_yr      = len(yr_trades)
        tp_yr     = sum(1 for t in yr_trades if t['outcome'] == 'tp')
        tp_rate   = tp_yr / n_yr if n_yr > 0 else 0.0
        # Year-end equity: last equity_curve entry for this year
        yr_eq = next(
            (eq for d, eq in reversed(result['equity_curve']) if d.year == yr),
            prev_eq
        )
        print(f"  {yr:<6}  {n_yr:>6,}  {tp_rate*100:>5.1f}%  ${yr_eq:>15,.0f}")
        prev_eq = yr_eq


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description='Deterministic historical backtest — cascade allocation')
    ap.add_argument('--capital',   type=float, default=INITIAL_CAPITAL,
                    help='Starting capital (default $50,000)')
    ap.add_argument('--version',   type=int,   default=14,
                    help='AlgorithmVersion id (default 14)')
    ap.add_argument('--min-score', type=float, default=70.0,
                    help='Minimum score threshold (default 70)')
    ap.add_argument('--from',      dest='from_date', default=None,
                    help='Override start date YYYY-MM-DD (must be >= 2016-01-01)')
    ap.add_argument('--to',        dest='to_date',   default=None,
                    help='Override end date YYYY-MM-DD (inclusive)')
    args = ap.parse_args()

    from_date = None
    if args.from_date:
        from_date = date.fromisoformat(args.from_date)
        if from_date < MIN_DATE:
            print(f"Warning: --from clamped to {MIN_DATE} (data quality barrier)")
            from_date = MIN_DATE

    to_date = None
    if args.to_date:
        to_date = date.fromisoformat(args.to_date)

    print("Loading signals from database...")
    result = run_cascade_backtest(args.version, min_score=args.min_score,
                                  from_date=from_date, to_date=to_date,
                                  initial=args.capital, verbose=True)
    if not result:
        print("No qualifying signals found.")
        return

    print_report(result, result['start_date'], result['end_date'], args.min_score)


def compute_temporal_stats(trade_log: list, equity_curve: list, initial: float,
                           vacuum_month_sim: dict | None = None) -> dict:
    """Year-by-year, month-by-month, and cross-year monthly-average breakdown.

    Single pass over trade_log buckets everything into year_trades and
    month_trades simultaneously.  monthly_avg aggregates each calendar month
    (1-12) across all years — used as the bottom summary row in the heatmap.
    """
    if not trade_log:
        return {'yearly': [], 'monthly': [], 'monthly_avg': []}

    # ── Single pass: bucket by year AND (year, month) at the same time ──────
    year_trades  = defaultdict(list)
    month_trades = defaultdict(list)   # (year, month) -> [trades]

    for t in trade_log:
        d = t['exit_date']
        year_trades[d.year].append(t)
        month_trades[(d.year, d.month)].append(t)

    # Build an equity lookup by (year, month) — one scan of equity_curve
    eq_by_year_month = {}
    for d, eq in equity_curve:
        eq_by_year_month[(d.year, d.month)] = eq   # last entry per (yr, mo) wins

    eq_by_year = {}
    for (yr, _mo), eq in eq_by_year_month.items():
        # Keep the latest month's equity as year-end equity
        if yr not in eq_by_year or _mo > max(
            m for (y, m) in eq_by_year_month if y == yr
        ):
            eq_by_year[yr] = eq

    # Simpler: one more pass to get year-end equity correctly
    eq_by_year = {}
    for d, eq in equity_curve:
        eq_by_year[d.year] = eq   # last entry per year wins (equity_curve is sorted by date)

    def _rates(tlist):
        n      = len(tlist)
        n_tp   = sum(1 for t in tlist if t['outcome'] == 'tp')
        n_sl   = sum(1 for t in tlist if t['outcome'] == 'sl')
        n_hard = sum(1 for t in tlist if t['outcome'] == 'hard')
        return n, n_tp, n_sl, n_hard

    # ── Year-by-year ─────────────────────────────────────────────────────────
    yearly  = []
    prev_eq = initial
    for yr in sorted(year_trades):
        yr_t  = year_trades[yr]
        calls = [t for t in yr_t if t.get('side', 'call') == 'call']
        puts  = [t for t in yr_t if t.get('side') == 'put']
        n, n_tp, n_sl, n_hard = _rates(yr_t)

        yr_eq  = eq_by_year.get(yr, prev_eq)
        yr_ret = round((yr_eq / prev_eq - 1.0) * 100, 1) if prev_eq > 0 else 0.0

        c_n  = len(calls);  c_tp = sum(1 for t in calls if t['outcome'] == 'tp')
        p_n  = len(puts);   p_tp = sum(1 for t in puts  if t['outcome'] == 'tp')

        yearly.append({
            'year':         yr,
            'n_trades':     n,
            'tp_count':     n_tp,
            'sl_count':     n_sl,
            'hard_count':   n_hard,
            'tp_rate':      round(n_tp / n * 100, 1) if n else None,
            'call_n':       c_n,
            'call_tp_rate': round(c_tp / c_n * 100, 1) if c_n else None,
            'put_n':        p_n,
            'put_tp_rate':  round(p_tp / p_n * 100, 1) if p_n else None,
            'equity_end':   round(yr_eq, 2),
            'return_pct':   yr_ret,
        })
        prev_eq = yr_eq

    # ── Month-by-month ───────────────────────────────────────────────────────
    monthly = []
    # Also accumulate per-calendar-month buckets for the cross-year average
    cal_month_trades = defaultdict(list)   # 1-12 -> [all trades for that month across years]

    for (yr, mo) in sorted(month_trades):
        mt    = month_trades[(yr, mo)]
        calls = [t for t in mt if t.get('side', 'call') == 'call']
        puts  = [t for t in mt if t.get('side') == 'put']
        n, n_tp, _, _ = _rates(mt)
        c_n  = len(calls);  c_tp = sum(1 for t in calls if t['outcome'] == 'tp')
        p_n  = len(puts);   p_tp = sum(1 for t in puts  if t['outcome'] == 'tp')
        mo_eq = eq_by_year_month.get((yr, mo))
        mo_ret = None
        if vacuum_month_sim is not None:
            mo_ret = vacuum_month_sim.get((yr, mo))
        elif mo_eq is not None and initial and initial > 0:
            mo_ret = round((mo_eq / initial - 1.0) * 100, 1)

        monthly.append({
            'year':         yr,
            'month':        mo,
            'n_trades':     n,
            'tp_count':     n_tp,
            'tp_rate':      round(n_tp / n * 100, 1) if n else None,
            'call_n':       c_n,
            'call_tp_rate': round(c_tp / c_n * 100, 1) if c_n else None,
            'put_n':        p_n,
            'put_tp_rate':  round(p_tp / p_n * 100, 1) if p_n else None,
            'equity_end':   round(mo_eq, 2) if mo_eq is not None else None,
            'return_pct':   mo_ret,
        })
        cal_month_trades[mo].extend(mt)

    # ── Cross-year monthly average (one row per calendar month) ──────────────
    monthly_avg = []
    for mo in range(1, 13):
        mt = cal_month_trades[mo]
        if not mt:
            monthly_avg.append({'month': mo, 'n_trades': 0, 'years_sampled': 0,
                                 'tp_rate': None, 'call_tp_rate': None, 'put_tp_rate': None})
            continue
        calls = [t for t in mt if t.get('side', 'call') == 'call']
        puts  = [t for t in mt if t.get('side') == 'put']
        n, n_tp, _, _ = _rates(mt)
        c_n  = len(calls);  c_tp = sum(1 for t in calls if t['outcome'] == 'tp')
        p_n  = len(puts);   p_tp = sum(1 for t in puts  if t['outcome'] == 'tp')
        years_sampled = len({t['exit_date'].year for t in mt})
        monthly_avg.append({
            'month':         mo,
            'n_trades':      n,
            'years_sampled': years_sampled,
            'tp_rate':       round(n_tp / n * 100, 1) if n else None,
            'call_tp_rate':  round(c_tp / c_n * 100, 1) if c_n else None,
            'put_tp_rate':   round(p_tp / p_n * 100, 1) if p_n else None,
        })

    return {'yearly': yearly, 'monthly': monthly, 'monthly_avg': monthly_avg}


# ---------------------------------------------------------------------------
# Shared backtest pipeline  (called by main() AND compute_and_store_temporal)
# ---------------------------------------------------------------------------
def run_cascade_backtest(version_id: int,
                         min_score: float = 70.0,
                         max_put_score: float = None,
                         from_date=None,
                         to_date=None,
                         initial: float = INITIAL_CAPITAL,
                         verbose: bool = True,
                         calls_only: bool = False,
                         flagged_only: bool = False,
                         cfg: dict | None = None) -> dict:
    """Single-pass full cascade backtest pipeline.

    Loads signals, price history, breadth/regime maps, computes trade outcomes,
    runs the portfolio simulation, and returns the complete result dict
    (equity_curve, trade_log, final_equity, max_dd, initial).

    Shared by main() (CLI) and compute_and_store_temporal() so the heavy work
    is never duplicated across callers.
    """
    def _log(*args, **kwargs):
        if verbose:
            print(*args, **kwargs)

    raw     = load_signals(version_id, min_score, from_date=from_date, to_date=to_date, flagged_only=flagged_only)
    put_raw = [] if calls_only else load_put_signals(version_id, max_put_score, from_date=from_date, to_date=to_date, flagged_only=flagged_only)

    if not raw and not put_raw:
        return {}

    symbols    = {s.symbol for s in raw} | {s.symbol for s in put_raw}
    all_sigs   = raw + put_raw
    start_date = min(s.date for s in all_sigs)
    end_date   = max(s.date for s in all_sigs)

    _log(f"  {len(raw):,} call signals, {len(put_raw):,} put signals "
         f"across {len(symbols):,} symbols")
    _log(f"  Window: {start_date} → {end_date}")

    _log("Loading price history...")
    ph = load_price_history(symbols, start_date)

    _log("Loading market breadth & regime...")
    b_dates, b_map = load_breadth_map(start_date)
    _breadth_alloc = (cfg or {}).get('breadth_alloc_enabled', BREADTH_ALLOC_ENABLED)
    r_dates, r_map = load_regime_map(start_date, breadth_enabled=_breadth_alloc)
    _log(f"  {len(b_map):,} breadth dates, {len(r_map):,} alloc-map dates "
         f"({'F3f breadth' if _breadth_alloc else 'legacy regime_mult'})")

    # Earnings-effective-date map for variance-additive premium pricing.
    ern_map_for_premium = None
    j_map_for_premium: dict = {}
    from database.models.core import EarningsDate
    from iv_crush_model import compute_effective_date as _eff_date
    from database.earnings_jump_cache import load_per_stock_jumps
    all_syms = {s.symbol for s in raw} | {s.symbol for s in put_raw}
    if all_syms:
        ern_rows_p = list(EarningsDate.select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                          .where(EarningsDate.symbol.in_(list(all_syms)),
                                 EarningsDate.date >= start_date - timedelta(days=DTE_CAL_15 + 5),
                                 EarningsDate.date <= end_date + timedelta(days=DTE_CAL_15 + 7))
                          .order_by(EarningsDate.symbol, EarningsDate.date)
                          .tuples())
        ern_map_for_premium = defaultdict(list)
        for sym, d, ct in ern_rows_p:
            ern_map_for_premium[sym].append(_eff_date(d, ct))
        for sym in list(ern_map_for_premium.keys()):
            ern_map_for_premium[sym] = sorted(set(ern_map_for_premium[sym]))
        # Per-stock cached earnings-jump magnitudes; symbols not in the result
        # have <3 cached events and fall back to universe EARN_JUMP_PCT in the helper.
        j_map_for_premium = load_per_stock_jumps(all_syms)
        _log(f"  Earnings-aware premium  per-stock j: {len(j_map_for_premium)} syms "
             f"+ {len(all_syms) - len(j_map_for_premium)} fallback @ {EARN_JUMP_PCT:.2f}%")
        _log(f"  earnings-effective map: {len(ern_map_for_premium)} syms")

    def _spans(sig):
        if ern_map_for_premium is None:
            return False
        return _signal_spans_earnings(sig.date, ern_map_for_premium.get(sig.symbol))

    def _jpct(sig):
        return j_map_for_premium.get(sig.symbol)

    # Thread the earnings-effective-date map through cfg so compute_outcome /
    # compute_put_outcome can sample vega_ratio per (symbol, signal_date) when
    # OPTION_PRICING_AWARE=1. Reuses the same map already built for premium pricing.
    cfg = dict(cfg or {})
    if ern_map_for_premium is not None and 'ern_map' not in cfg:
        cfg['ern_map'] = dict(ern_map_for_premium)

    _log("Computing trade outcomes...")
    outcomes_by_date: dict = defaultdict(list)
    n_skipped = 0;  n_stressed = 0;  n_put_outcomes = 0

    for sig in raw:
        rows     = ph.get(sig.symbol, [])
        stressed = is_stressed(b_dates, b_map, sig.date)
        trend    = float(sig.trend) if sig.trend is not None else None
        outcome  = compute_outcome(sig.symbol, sig.date, float(sig.overall),
                                   rows, stressed, trend=trend, cfg=cfg,
                                   spans_earn=_spans(sig), j_pct=_jpct(sig))
        if outcome is None:
            n_skipped += 1
            continue
        if stressed:
            n_stressed += 1
        outcomes_by_date[sig.date].append(outcome)

    for sig in put_raw:
        rows    = ph.get(sig.symbol, [])
        trend   = float(sig.trend) if sig.trend is not None else None
        outcome = compute_put_outcome(sig.symbol, sig.date, float(sig.overall),
                                      rows, trend=trend, cfg=cfg,
                                      spans_earn=_spans(sig), j_pct=_jpct(sig))
        if outcome is None:
            n_skipped += 1
            continue
        n_put_outcomes += 1
        outcomes_by_date[sig.date].append(outcome)

    def _sort_key(o):
        side_order  = 0 if o.side == 'call' else 1
        ct_priority = 0 if (o.side == 'call' and o.tier == CT_CALL_TIER and o.score < 95) \
                          or (o.side == 'put'  and o.tier == CT_PUT_TIER  and o.score > 15) \
                       else 1
        score_key   = -o.score if o.side == 'call' else o.score
        return (side_order, ct_priority, score_key, o.symbol)

    for d in outcomes_by_date:
        outcomes_by_date[d].sort(key=_sort_key)

    total_outcomes   = sum(len(v) for v in outcomes_by_date.values())
    n_call_outcomes  = total_outcomes - n_put_outcomes
    stress_pct       = (n_stressed / n_call_outcomes * 100) if n_call_outcomes else 0.0
    _log(f"  {total_outcomes:,} outcomes  ({n_call_outcomes:,} calls, {n_put_outcomes:,} puts)  "
         f"| {n_skipped:,} skipped  | stressed calls: {n_stressed:,} ({stress_pct:.1f}%)")

    hold_days  = (cfg or {}).get('hold_calendar_days', HOLD_CALENDAR_DAYS)
    settle_end = end_date + timedelta(days=hold_days + 10)
    if to_date is not None:
        settle_end = min(settle_end, to_date)
    all_dates  = sorted({
        r.date
        for rows in ph.values()
        for r in rows
        if start_date <= r.date <= settle_end
    })

    _log(f"Running backtest over {len(all_dates):,} trading days...")
    result = run_backtest(outcomes_by_date, all_dates, initial,
                          regime_dates=r_dates, regime_map=r_map, cfg=cfg)
    result['start_date']      = start_date
    result['end_date']        = end_date
    result['min_score']       = min_score
    result['outcomes_by_date'] = outcomes_by_date
    result['all_dates']       = all_dates
    result['regime_dates']    = r_dates
    result['regime_map']      = r_map
    return result


def compute_and_store_temporal(version=None, initial: float = 50_000.0,
                                dte_strategy: str = '15') -> dict:
    """Run the full cascade backtest (single pass) and persist temporal stats.

    Called by `trader assess --force`. dte_strategy='30' (default) writes
    BacktestTemporalStats with the 30 DTE H5 strategy. dte_strategy='15' writes
    a separate row using the 15 DTE C1 strategy from monte_carlo_15dte.py.
    Returns the stored temporal dict.
    """
    import json as _json
    from database.models.core import BacktestTemporalStats, AlgorithmVersion

    if version is None:
        version = AlgorithmVersion.get_active_scores_version()

    result = run_cascade_backtest(version.id, min_score=70.0, initial=initial,
                                  verbose=False)
    if not result:
        return {}

    trade_log    = result['trade_log']
    equity_curve = result['equity_curve']
    final_eq     = equity_curve[-1][1] if equity_curve else initial
    max_dd_pct   = result['max_dd'] * 100

    # ── Per-month vacuum $50k simulation ─────────────────────────────────────
    # Each month run in isolation from a fresh $50k start, through the cascade
    # allocator with the same regime map. Return is the profit margin of that
    # month's signals standalone.
    outcomes_by_date = result.get('outcomes_by_date') or {}
    all_dates        = result.get('all_dates') or []
    r_dates          = result.get('regime_dates') or []
    r_map            = result.get('regime_map') or {}

    VACUUM_START = 50_000.0
    vacuum_month_sim: dict = {}
    months_present = sorted({(t['exit_date'].year, t['exit_date'].month)
                             for t in trade_log})
    for (yr, mo) in months_present:
        month_start = date(yr, mo, 1)
        if mo == 12:
            month_end = date(yr, 12, 31)
        else:
            month_end = date(yr, mo + 1, 1) - timedelta(days=1)
        settle_end = month_end + timedelta(days=HOLD_CALENDAR_DAYS + 10)

        mo_outs = {d: outs for d, outs in outcomes_by_date.items()
                   if month_start <= d <= month_end}
        if not mo_outs:
            continue
        mo_days = [d for d in all_dates if month_start <= d <= settle_end]
        if not mo_days:
            continue

        vac = run_backtest(mo_outs, mo_days, VACUUM_START,
                           regime_dates=r_dates, regime_map=r_map)
        vac_eq = vac['equity_curve'][-1][1] if vac['equity_curve'] else VACUUM_START
        vacuum_month_sim[(yr, mo)] = round((vac_eq / VACUUM_START - 1.0) * 100, 1)

    temporal = compute_temporal_stats(trade_log, equity_curve, initial,
                                      vacuum_month_sim=vacuum_month_sim)

    n_call = sum(1 for t in trade_log if t.get('side', 'call') == 'call')
    n_put  = sum(1 for t in trade_log if t.get('side') == 'put')
    summary = {
        'initial':          initial,
        'final_equity':     round(final_eq, 2),
        'total_return_pct': round((final_eq / initial - 1.0) * 100, 2) if initial else 0.0,
        'max_dd':           round(max_dd_pct, 1),
        'n_trades':         len(trade_log),
        'n_call_trades':    n_call,
        'n_put_trades':     n_put,
    }

    BacktestTemporalStats.ensure_schema()
    BacktestTemporalStats.delete().where(
        BacktestTemporalStats.version == version,
        BacktestTemporalStats.dte_strategy == dte_strategy,
    ).execute()
    BacktestTemporalStats.create(
        version          = version,
        initial_capital  = initial,
        summary_json     = _json.dumps(summary),
        yearly_json      = _json.dumps(temporal['yearly']),
        monthly_json     = _json.dumps(temporal['monthly']),
        monthly_avg_json = _json.dumps(temporal['monthly_avg']),
        dte_strategy     = dte_strategy,
    )

    return {**temporal, 'summary': summary}


if __name__ == '__main__':
    main()
