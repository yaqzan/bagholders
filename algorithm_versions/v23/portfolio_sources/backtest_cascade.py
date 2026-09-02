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
from dataclasses import dataclass
from datetime import date, timedelta

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
MIN_DATE        = date(2020, 1, 1)

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
def load_signals(version_id: int, min_score: float) -> list:
    """All qualifying call-side score rows for the given algorithm version."""
    from database.models.core import AlgorithmVersion, Score
    version = AlgorithmVersion.get(AlgorithmVersion.id == version_id)
    rows = (Score
            .select(Score.symbol, Score.date, Score.overall, Score.trend)
            .where(
                (Score.version == version)
                & (Score.overall >= min_score)
                & (Score.date   >= MIN_DATE)
            )
            .order_by(Score.date, Score.overall.desc(), Score.symbol)
            .namedtuples())
    return list(rows)


def load_put_signals(version_id: int) -> list:
    """Put-side signals (overall <= PUT_THRESHOLD)."""
    from database.models.core import AlgorithmVersion, Score
    version = AlgorithmVersion.get(AlgorithmVersion.id == version_id)
    rows = (Score
            .select(Score.symbol, Score.date, Score.overall, Score.trend)
            .where(
                (Score.version == version)
                & (Score.overall <= PUT_THRESHOLD)
                & (Score.date   >= MIN_DATE)
            )
            .order_by(Score.date, Score.overall.asc(), Score.symbol)
            .namedtuples())
    return list(rows)


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
    """Return (sorted_dates, {date: regime_mult}) for regime-aware allocation."""
    from database.models.core import MarketRegime
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
    """Return regime_mult on-or-before d; 1.0 if no coverage."""
    if not sorted_dates:
        return 1.0
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0:
        return 1.0
    return rmap[sorted_dates[idx]]


def alloc_scale_for(regime_mult: float, is_put: bool = False) -> float:
    """alloc multiplier from regime: 1.0 + slope*(mult-1.0), clamped.

    Asymmetric: if REGIME_SLOPE_UP/DOWN (or _PUT_UP/_DOWN) are set, use them
    for BULL (delta>=0) vs STRESS (delta<0). Falls back to symmetric.
    """
    delta = regime_mult - 1.0
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
    side:        str = 'call'  # 'call' or 'put'


def compute_outcome(symbol: str, signal_date: date, score: float,
                    ph_rows: list, stressed: bool,
                    trend: float | None = None) -> 'TradeOutcome | None':
    """Walk OHLCV data from signal_date forward to determine trade outcome."""
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

    tp_sigma = TP_SIGMA_STRESS if stressed else TP_SIGMA_BASE
    sl_sigma = SL_SIGMA_STRESS if stressed else SL_SIGMA_BASE
    net_tp   = NET_TP_STRESS   if stressed else NET_TP_BASE
    net_sl   = NET_SL_STRESS   if stressed else NET_SL_BASE

    tp_price = entry_price * (1.0 + tp_sigma * sigma / 100.0)
    sl_price = entry_price * (1.0 - sl_sigma * sigma / 100.0)
    deadline = signal_date + timedelta(days=HOLD_CALENDAR_DAYS)
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
                                net_tp, bars_held, stressed)
        if low <= sl_price:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'sl', bar_date,
                                net_sl, bars_held, stressed)

        if bar_date >= deadline:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'hard', bar_date,
                                NET_HARD, bars_held, stressed)

    last = ph_rows[-1]
    return TradeOutcome(symbol, signal_date, score, tier,
                        entry_price, sigma, 'hard', last.date,
                        NET_HARD, len(ph_rows) - 1 - sig_i, stressed)


def compute_put_outcome(symbol: str, signal_date: date, score: float,
                        ph_rows: list,
                        trend: float | None = None) -> 'TradeOutcome | None':
    """Put trade: win = underlying falls PUT_TP_SIGMA sigmas; stop = rises PUT_SL_SIGMA."""
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

    tp_price = entry_price * (1.0 - PUT_TP_SIGMA * sigma / 100.0)
    sl_price = entry_price * (1.0 + PUT_SL_SIGMA * sigma / 100.0)
    deadline = signal_date + timedelta(days=HOLD_CALENDAR_DAYS)
    tier     = CT_PUT_TIER if ct_tag(score, trend, 'put') else put_score_to_tier(score)

    for j in range(sig_i + 1, len(ph_rows)):
        bar = ph_rows[j]
        bars_held = j - sig_i
        high = float(bar.high); low = float(bar.low)
        if low <= tp_price:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'tp', bar.date,
                                PUT_NET_TP, bars_held, False, 'put')
        if high >= sl_price:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'sl', bar.date,
                                PUT_NET_SL, bars_held, False, 'put')
        if bar.date >= deadline:
            return TradeOutcome(symbol, signal_date, score, tier,
                                entry_price, sigma, 'hard', bar.date,
                                NET_HARD, bars_held, False, 'put')

    last = ph_rows[-1]
    return TradeOutcome(symbol, signal_date, score, tier,
                        entry_price, sigma, 'hard', last.date,
                        NET_HARD, len(ph_rows) - 1 - sig_i, False, 'put')


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
                 regime_map: dict | None = None) -> dict:
    """
    outcomes_by_date: {signal_date: [TradeOutcome, ...]}
                      each list pre-sorted: score desc, symbol asc.
    trading_days:     sorted list of all trading dates in the window.
    """
    cash: float             = initial_capital
    open_pos: list[OpenPosition] = []
    equity_curve: list      = []   # [(date, equity), ...]
    trade_log: list         = []
    peak_equity: float      = initial_capital
    max_dd: float           = 0.0

    for today in trading_days:
        # 1. Close positions whose exit_date has arrived
        remaining = []
        for pos in open_pos:
            if pos.outcome.exit_date <= today:
                proceeds = pos.premium * (1.0 + pos.outcome.net_return)
                cash    += proceeds
                trade_log.append({
                    'entry_date': pos.outcome.signal_date,
                    'exit_date':  pos.outcome.exit_date,
                    'symbol':     pos.outcome.symbol,
                    'score':      pos.outcome.score,
                    'tier':       pos.outcome.tier,
                    'sigma':      pos.outcome.sigma_daily,
                    'premium':    pos.premium,
                    'outcome':    pos.outcome.outcome,
                    'hold_bars':  pos.outcome.hold_bars,
                    'pnl':        proceeds - pos.premium,
                    'pnl_pct':    pos.outcome.net_return,
                    'stressed':   pos.outcome.stressed,
                    'side':       pos.outcome.side,
                })
            else:
                remaining.append(pos)
        open_pos = remaining

        # 2. Mark-to-market (open positions marked at cost; realistic for options)
        equity = cash + sum(p.premium for p in open_pos)

        # 3. Open new trades for today's signals
        open_syms = {p.outcome.symbol for p in open_pos}
        for outcome in outcomes_by_date.get(today, []):
            if len(open_pos) >= MAX_POSITIONS:
                break
            if outcome.symbol in open_syms:
                continue                      # re-entry block

            reg_mult = (regime_on_or_before(regime_dates, regime_map, today)
                        if regime_dates else 1.0)
            is_put = getattr(outcome, 'side', 'call') == 'put'
            reg_scale = alloc_scale_for(reg_mult, is_put=is_put)
            premium = TIER_ALLOC[outcome.tier] * reg_scale * equity
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

    # Settle any remaining open positions at hard-sell P&L
    for pos in open_pos:
        proceeds = pos.premium * (1.0 + NET_HARD)
        cash    += proceeds
        trade_log.append({
            'entry_date': pos.outcome.signal_date,
            'exit_date':  trading_days[-1] if trading_days else pos.outcome.signal_date,
            'symbol':     pos.outcome.symbol,
            'score':      pos.outcome.score,
            'tier':       pos.outcome.tier,
            'sigma':      pos.outcome.sigma_daily,
            'premium':    pos.premium,
            'outcome':    'hard',
            'hold_bars':  pos.outcome.hold_bars,
            'pnl':        proceeds - pos.premium,
            'pnl_pct':    NET_HARD,
            'stressed':   pos.outcome.stressed,
            'side':       pos.outcome.side,
        })

    return {
        'equity_curve': equity_curve,
        'trade_log':    trade_log,
        'final_equity': cash,
        'max_dd':       max_dd,
        'initial':      initial_capital,
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
                    help='Override start date YYYY-MM-DD (must be >= 2020-01-01)')
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

    # --- Load signals (calls + puts) ---
    print("Loading signals from database...")
    raw = load_signals(args.version, args.min_score)
    put_raw = load_put_signals(args.version)
    if not raw and not put_raw:
        print("No qualifying signals found.")
        return

    if from_date:
        raw     = [s for s in raw     if s.date >= from_date]
        put_raw = [s for s in put_raw if s.date >= from_date]
    if to_date:
        raw     = [s for s in raw     if s.date <= to_date]
        put_raw = [s for s in put_raw if s.date <= to_date]
    if not raw and not put_raw:
        print("No signals in window.")
        return

    symbols = {s.symbol for s in raw} | {s.symbol for s in put_raw}
    all_sigs = raw + put_raw
    start_date = min(s.date for s in all_sigs)
    end_date   = max(s.date for s in all_sigs)

    print(f"  {len(raw):,} call signals, {len(put_raw):,} put signals across {len(symbols):,} symbols")
    print(f"  Window: {start_date} → {end_date}")

    # --- Load price history ---
    print(f"Loading price history for {len(symbols):,} symbols...")
    ph = load_price_history(symbols, start_date)

    # --- Load breadth (for TP/SL regime switch) ---
    print("Loading market breadth...")
    b_dates, b_map = load_breadth_map(start_date)
    print(f"  {len(b_map):,} breadth dates loaded")

    r_dates, r_map = load_regime_map(start_date)
    if REGIME_SLOPE_UP is not None or REGIME_SLOPE_DOWN is not None:
        print(f"  {len(r_map):,} regime dates loaded  "
              f"(slope_c up={REGIME_SLOPE_UP} dn={REGIME_SLOPE_DOWN}, slope_p={REGIME_SLOPE_PUT})")
    else:
        print(f"  {len(r_map):,} regime dates loaded  "
              f"(slope_c={REGIME_SLOPE}, slope_p={REGIME_SLOPE_PUT})")

    # --- Pre-compute outcomes for every signal ---
    print("Computing trade outcomes from real price data...")
    outcomes_by_date: dict = defaultdict(list)
    n_skipped = 0
    n_stressed = 0

    n_ct_call = 0
    n_ct_put = 0
    for sig in raw:
        rows     = ph.get(sig.symbol, [])
        stressed = is_stressed(b_dates, b_map, sig.date)
        trend    = float(sig.trend) if sig.trend is not None else None
        outcome  = compute_outcome(sig.symbol, sig.date, float(sig.overall),
                                   rows, stressed, trend=trend)
        if outcome is None:
            n_skipped += 1
            continue
        if stressed:
            n_stressed += 1
        if outcome.tier == CT_CALL_TIER and ct_tag(float(sig.overall), trend, 'call'):
            n_ct_call += 1
        outcomes_by_date[sig.date].append(outcome)

    n_put_outcomes = 0
    for sig in put_raw:
        rows    = ph.get(sig.symbol, [])
        trend   = float(sig.trend) if sig.trend is not None else None
        outcome = compute_put_outcome(sig.symbol, sig.date, float(sig.overall),
                                      rows, trend=trend)
        if outcome is None:
            n_skipped += 1
            continue
        n_put_outcomes += 1
        if outcome.tier == CT_PUT_TIER and ct_tag(float(sig.overall), trend, 'put'):
            n_ct_put += 1
        outcomes_by_date[sig.date].append(outcome)

    # Deterministic tiebreak:
    #   1. Side: calls first, puts second
    #   2. CT-tagged signals fill ahead of score-sorted queue (ct_call -> '95+', ct_put -> 'p<=15')
    #   3. Score: calls desc, puts asc
    #   4. Symbol asc
    def _sort_key(o):
        side_order = 0 if o.side == 'call' else 1
        ct_priority = 0 if (o.side == 'call' and o.tier == CT_CALL_TIER and o.score < 95) \
                          or (o.side == 'put' and o.tier == CT_PUT_TIER and o.score > 15) \
                       else 1
        score_key = -o.score if o.side == 'call' else o.score
        return (side_order, ct_priority, score_key, o.symbol)
    for d in outcomes_by_date:
        outcomes_by_date[d].sort(key=_sort_key)

    total_outcomes = sum(len(v) for v in outcomes_by_date.values())
    n_call_outcomes = total_outcomes - n_put_outcomes
    stress_pct = (n_stressed / n_call_outcomes * 100) if n_call_outcomes else 0.0
    print(f"  {total_outcomes:,} outcomes computed  ({n_call_outcomes:,} calls, {n_put_outcomes:,} puts)  |  "
          f"{n_skipped:,} skipped (insufficient price/vol data)")
    print(f"  breadth-stressed call entries (brd<=50): {n_stressed:,} "
          f"({stress_pct:.1f}%) -> TP=+35% / SL=-40%")

    # --- Build sorted trading calendar ---
    # Union of all dates across all price histories in the backtest window,
    # extended past end_date to allow positions opened late to settle.
    settle_end = end_date + timedelta(days=HOLD_CALENDAR_DAYS + 10)
    all_dates  = sorted({
        r.date
        for rows in ph.values()
        for r in rows
        if start_date <= r.date <= settle_end
    })

    print(f"Running backtest over {len(all_dates):,} trading days...")
    result = run_backtest(outcomes_by_date, all_dates, args.capital,
                          regime_dates=r_dates, regime_map=r_map)

    print_report(result, start_date, end_date, args.min_score)


if __name__ == '__main__':
    main()
