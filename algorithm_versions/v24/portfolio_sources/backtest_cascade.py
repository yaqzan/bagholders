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
# Strategy constants
# ---------------------------------------------------------------------------
INITIAL_CAPITAL     = 50_000.0
MAX_POSITIONS       = 14           # upgraded from 10; MaxPos sweep 2026-04-16
HOLD_CALENDAR_DAYS  = 15           # hard sell: 15 calendar days from signal date

# Breadth-adaptive sigma barriers (60d realized daily σ units)
# Same breadth signal drives BOTH TP and SL:
#   breadth_score <= 50 ("stressed") -> widen both
#   breadth_score  > 50 ("healthy")  -> base band
# PREMIUM_MULT=1.82, DELTA=0.5  -> pct * 3.64 = sigma
TP_SIGMA_BASE       = 1.092        # +30% premium
TP_SIGMA_STRESS     = 1.274        # +35% premium
SL_SIGMA_BASE       = 1.274        # -35% premium
SL_SIGMA_STRESS     = 1.456        # -40% premium

BREADTH_THRESHOLD   = 50           # breadth_score <= 50 -> stressed

# Regime-aware allocation (shipped 2026-04-17 Phase 9-13, asymmetric CUT_ONLY):
# alloc_scale = 1.0 + slope * (regime_mult - 1.0), clamped [floor, ceil].
# slope_up=0 (no bull boost), slope_down=1.0 (stress-only contraction) beats
# symmetric SC100 by +58% compound on 22-now Realistic, with lower DD-C.
REGIME_SLOPE          = 1.0    # symmetric fallback (unused when UP/DOWN set)
REGIME_SLOPE_PUT      = 0.0
ALLOC_SCALE_FLOOR     = 0.25
ALLOC_SCALE_CEIL      = 1.75
REGIME_SLOPE_UP       = 0.0    # BULL regime_mult >= 1.0: no call boost
REGIME_SLOPE_DOWN     = 1.0    # STRESS regime_mult < 1.0: full call cut
REGIME_SLOPE_PUT_UP   = -0.5   # BULL: mild put cut (Phase 15 winner)
REGIME_SLOPE_PUT_DOWN = None   # STRESS: puts unchanged

# Breadth-driven allocation knob (F3f) — shipped 2026-04-24.
# Replaces composite-driven regime_multiplier scaling (Priority #6: composite
# was inverted in 2026-04-09, mislabeling narrow-bull as stress and
# narrow-stress as healthy). F3f anchors alloc directly on breadth_score.
# Validated 2026-04-24 in canonical 3-mode MC, N=150: +121% 5y Realistic
# compound vs production. All gates pass (DD-C max 64.5%, no Realistic loss
# > 25% vs B, 0% collapse). Set BREADTH_ALLOC_ENABLED=False to revert to
# the legacy regime-slope path.
BREADTH_ALLOC_ENABLED = True
F3F_CALL_THRESH       = 50.0   # breadth >= this -> no call cut
F3F_CALL_FLOOR        = 0.70   # min call alloc scale at deepest breadth
F3F_CALL_LOW          = 20.0   # breadth at which call floor is reached
F3F_PUT_THRESH        = 75.0   # breadth <= this -> no put cut
F3F_PUT_FLOOR         = 0.75   # min put alloc scale at highest breadth
F3F_PUT_HIGH          = 95.0   # breadth at which put floor is reached

# Net option P&L after per-exit slippage (entry −1%, TP 0% limit sell,
# SL −1.3%, hard −0.5%) — canonical values from monte_carlo.py
NET_TP_BASE     = +0.290   # +30% gross − 1.0% entry
NET_TP_STRESS   = +0.340   # +35% gross − 1.0% entry
NET_SL_BASE     = -0.373   # −35% gross − 1.0% entry − 1.3% SL exit
NET_SL_STRESS   = -0.423   # −40% gross − 1.0% entry − 1.3% SL exit
NET_HARD        = -0.515   # −50% gross − 1.0% entry − 0.5% hard exit

# Cascade allocation per score tier (2026-04-17 — 95+ ultra split, flat mid/low, no overflow)
TIER_ALLOC = {
    '95+':   0.25,   # ultra — WR15=90% on v17+v18, own high-conviction tier
    '85-94': 0.15,
    '80-84': 0.15,
    '75-79': 0.15,
    '70-74': 0.00,   # disabled — overflow >=2% breaches 80% DD floor
    # Put-side tiers (2026-04-17: asym weekly + tight put SL)
    'p<=15': 0.15,
    'p16-20':0.12,
    'p21-25':0.12,
}

# Put-side fixed TP/SL (no breadth switch)
PUT_TP            =  0.30
PUT_SL            = -0.20
PUT_NET_TP        = +0.290   # +30% − 1.0% entry
PUT_NET_SL        = -0.223   # −20% − 1.0% entry − 1.3% SL exit
PUT_TP_SIGMA      = 1.092    # TP in sigma units (mirror call magnitude)
PUT_SL_SIGMA      = 0.728    # SL in sigma units (tighter than calls)
PUT_THRESHOLD     = 25       # puts fire on score <= 25

# Put SL hard-hold: suppress SL check for first N trading bars after entry.
PUT_SL_HOLD_BARS_DEFAULT = 3   # Tue-Fri entries
PUT_SL_HOLD_BARS_MONDAY  = 4   # Monday entries: corrects calendar-day coverage gap

# Counter-trend cascade promotion (Path B / V2, shipped 2026-04-21).
# ct_put = (overall<=25 AND TREND>=CT_PUT_TREND_MIN) -> override tier to 'p<=15' (15%)
# ct_call = (overall>=70 AND TREND<=CT_CALL_TREND_MAX) -> override tier to '95+' (25%)
# Tagged signals fill ahead of score-sorted queue at their override tier.
CT_PROMOTE         = True
CT_PUT_TREND_MIN   = 80
CT_CALL_TREND_MAX  = 20
CT_CALL_TIER       = '95+'    # maps to monte_carlo.py 'ultra' (25%)
CT_PUT_TIER        = 'p<=15'  # maps to monte_carlo.py 'put_top' (15%)

# Data quality barrier — no signals before this date
MIN_DATE        = date(2016, 1, 1)

# Realized vol: 60 trading bars (consistent with assess_scores._realized_vol_pct)
VOL_BARS        = 60
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
    """Put-side signals (overall <= max_put_score, default PUT_THRESHOLD)."""
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
    return list(q.order_by(Score.date, Score.overall.asc(), Score.symbol).namedtuples())


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


def load_regime_map(earliest: date):
    """Return (sorted_dates, {date: alloc_scalar}).

    When BREADTH_ALLOC_ENABLED (default), returns breadth_score per date
    (F3f knob). Otherwise returns regime_multiplier per date (legacy).
    The function name is preserved for call-site compatibility; the value
    semantics are determined by BREADTH_ALLOC_ENABLED at the alloc_scale_for
    consumer.
    """
    from database.models.core import MarketRegime, MarketBreadth
    if BREADTH_ALLOC_ENABLED:
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


def regime_on_or_before(sorted_dates, rmap, d) -> float:
    """Return alloc scalar on-or-before d; neutral default if no coverage."""
    if not sorted_dates:
        return 50.0 if BREADTH_ALLOC_ENABLED else 1.0
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0:
        return 50.0 if BREADTH_ALLOC_ENABLED else 1.0
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


def alloc_scale_for(value: float, is_put: bool = False) -> float:
    """alloc multiplier, clamped [floor, ceil].

    When BREADTH_ALLOC_ENABLED (default), `value` is a breadth_score and the
    F3f curves apply (see F3F_* constants). Otherwise `value` is a
    regime_multiplier and the legacy asymmetric slope logic applies.
    """
    if BREADTH_ALLOC_ENABLED:
        s = _breadth_alloc_scale(value, is_put)
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


def load_price_history(symbols: set, earliest: date) -> dict:
    """Bulk-load OHLCV for all symbols.

    Returns {symbol: [(date, open, high, low, close), ...]} sorted by date.
    Loads from (earliest − VOL_BARS trading days buffer) to cover vol lookback.
    """
    from database.models.core import Stock
    from database.models.technical import PriceHistory

    # Buffer: extra calendar days to cover VOL_BARS trading bars before earliest
    buffer_date = earliest - timedelta(days=VOL_BARS * 2)

    ph = defaultdict(list)
    syms = list(symbols)

    # Batch in chunks of 100 to keep the IN clause manageable
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
            ph[r.symbol].append(r)

    return dict(ph)


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
                    cfg: dict | None = None) -> 'TradeOutcome | None':
    """Walk OHLCV data from signal_date forward to determine trade outcome."""
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
    else:
        tp_sigma = cfg.get('tp_sigma_base', TP_SIGMA_BASE)
        sl_sigma = cfg.get('sl_sigma_base', SL_SIGMA_BASE)
        net_tp   = cfg.get('net_tp_base',   NET_TP_BASE)
        net_sl   = cfg.get('net_sl_base',   NET_SL_BASE)

    tp_price = entry_price * (1.0 + tp_sigma * sigma / 100.0)
    sl_price = entry_price * (1.0 - sl_sigma * sigma / 100.0)
    deadline = signal_date + timedelta(days=hold_days)
    tier     = CT_CALL_TIER if ct_tag(score, trend, 'call') else score_to_tier(score)

    for j in range(sig_i + 1, len(ph_rows)):
        bar      = ph_rows[j]
        bar_date = bar.date
        high     = float(bar.high)
        low      = float(bar.low)
        bars_held = j - sig_i

        if high >= tp_price:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'tp', bar_date,
                                net_tp, bars_held, stressed,
                                exit_price=tp_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if low <= sl_price:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'sl', bar_date,
                                net_sl, bars_held, stressed,
                                exit_price=sl_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)

        if bar_date >= deadline:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'hard', bar_date,
                                net_hard, bars_held, stressed,
                                exit_price=float(bar.close),
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)

    # Price data exhausted before deadline — position is still open.
    last = ph_rows[-1]
    return TradeOutcome(symbol, signal_date, score, tier,
                        entry_price, sigma, 'open', deadline,
                        0.0, len(ph_rows) - 1 - sig_i, stressed,
                        exit_price=float(last.close),
                        tp_price=tp_price, sl_price=sl_price, deadline=deadline)


def compute_put_outcome(symbol: str, signal_date: date, score: float,
                        ph_rows: list,
                        trend: float | None = None,
                        cfg: dict | None = None) -> 'TradeOutcome | None':
    """Put trade: win = underlying falls PUT_TP_SIGMA sigmas; stop = rises PUT_SL_SIGMA."""
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

    tp_price = entry_price * (1.0 - put_tp_sigma * sigma / 100.0)
    sl_price = entry_price * (1.0 + put_sl_sigma * sigma / 100.0)
    deadline = signal_date + timedelta(days=hold_days)
    tier     = CT_PUT_TIER if ct_tag(score, trend, 'put') else put_score_to_tier(score)
    hold_default = cfg.get('put_sl_hold_default', PUT_SL_HOLD_BARS_DEFAULT)
    hold_monday  = cfg.get('put_sl_hold_monday',  PUT_SL_HOLD_BARS_MONDAY)
    sl_hold  = hold_monday if signal_date.weekday() == 0 else hold_default

    for j in range(sig_i + 1, len(ph_rows)):
        bar = ph_rows[j]
        bars_held = j - sig_i
        high = float(bar.high); low = float(bar.low)
        if low <= tp_price:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'tp', bar.date,
                                put_net_tp, bars_held, False, 'put',
                                exit_price=tp_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if high >= sl_price and bars_held > sl_hold:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'sl', bar.date,
                                put_net_sl, bars_held, False, 'put',
                                exit_price=sl_price,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline)
        if bar.date >= deadline:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'hard', bar.date,
                                net_hard, bars_held, False, 'put',
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

        # 3. Open new trades for today's signals
        open_syms = {p.outcome.symbol for p in open_pos}
        for outcome in outcomes_by_date.get(today, []):
            if len(open_pos) >= max_pos:
                break
            if outcome.symbol in open_syms:
                continue                      # re-entry block

            reg_mult = (regime_on_or_before(regime_dates, regime_map, today)
                        if regime_dates else 1.0)
            is_put = getattr(outcome, 'side', 'call') == 'put'
            reg_scale = alloc_scale_for(reg_mult, is_put=is_put)
            alloc_frac = tier_alloc.get(outcome.tier, TIER_ALLOC.get(outcome.tier, 0.0))
            premium = alloc_frac * reg_scale * equity
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
    r_dates, r_map = load_regime_map(start_date)
    _log(f"  {len(b_map):,} breadth dates, {len(r_map):,} regime dates")

    _log("Computing trade outcomes...")
    outcomes_by_date: dict = defaultdict(list)
    n_skipped = 0;  n_stressed = 0;  n_put_outcomes = 0

    for sig in raw:
        rows     = ph.get(sig.symbol, [])
        stressed = is_stressed(b_dates, b_map, sig.date)
        trend    = float(sig.trend) if sig.trend is not None else None
        outcome  = compute_outcome(sig.symbol, sig.date, float(sig.overall),
                                   rows, stressed, trend=trend, cfg=cfg)
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
                                      rows, trend=trend, cfg=cfg)
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


def compute_and_store_temporal(version=None, initial: float = 50_000.0) -> dict:
    """Run the full cascade backtest (single pass) and persist temporal stats.

    Called by `trader assess --force`.  Returns the stored temporal dict.
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
    BacktestTemporalStats.delete().where(BacktestTemporalStats.version == version).execute()
    BacktestTemporalStats.create(
        version          = version,
        initial_capital  = initial,
        summary_json     = _json.dumps(summary),
        yearly_json      = _json.dumps(temporal['yearly']),
        monthly_json     = _json.dumps(temporal['monthly']),
        monthly_avg_json = _json.dumps(temporal['monthly_avg']),
    )

    return {**temporal, 'summary': summary}


if __name__ == '__main__':
    main()
