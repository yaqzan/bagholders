"""
experiments/put_rollup_variants.py
===================================
Three put-entry strategy variants MC experiment.

BASELINE : 30 DTE put entered at signal date (exact production behavior)
DELAYED  : No entry at signal date. Enter 15 DTE put at day+WIN_7D_BARS ONLY if
           the underlying fell >= WIN_7D_SIGMA*vol within WIN_7D_BARS trading bars.
ROLLUP   : BASELINE 30 DTE puts PLUS an additional 15 DTE put layer at
           day+WIN_7D_BARS when confirmed. Same-sym block prevents rollup if the
           baseline put is still open (normal case for early bars).

Fresh-entry bridge WR (cross_window_bridge.py, 5y v21):
  7d win -> 8 cal day fresh entry: puts=63% (N=32,720), clears BE=43.5% by +19.5pp

Usage: python experiments/put_rollup_variants.py
"""

import os
import sys
import io
import math
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import bisect

from database.models.core import Score, AlgorithmVersion, MarketBreadth, MarketRegime
from database.models.technical import PriceHistory

# ---- Strategy constants (mirrors monte_carlo.py) ----------------------------
STARTING_CASH       = 50_000.0
N_ITER              = 200
VOL_LOOKBACK        = 60
HOLD_DAYS           = 15           # 30 DTE hold window (trading bars)
PREMIUM_MULT        = 1.82         # ATM 30-DTE ~ 1.82 * sigma_daily
DELTA               = 0.5

TP_BASE             =  0.30
TP_STRESS           =  0.35
SL_BASE             = -0.35
SL_STRESS           = -0.40
HARD_SELL_LOSS      = -0.50
BREADTH_THRESHOLD   = 50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_TP_BASE    = TP_BASE   + SLIP_ENTRY + SLIP_TP    # +0.290
NET_TP_STRESS  = TP_STRESS + SLIP_ENTRY + SLIP_TP    # +0.340
NET_SL_BASE    = SL_BASE   + SLIP_ENTRY + SLIP_SL    # -0.373
NET_SL_STRESS  = SL_STRESS + SLIP_ENTRY + SLIP_SL    # -0.423
NET_HARD_SELL  = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD  # -0.515

TP_SIGMA_BASE   = TP_BASE   * PREMIUM_MULT / DELTA   # 1.092
TP_SIGMA_STRESS = TP_STRESS * PREMIUM_MULT / DELTA   # 1.274
SL_SIGMA_BASE   = abs(SL_BASE)   * PREMIUM_MULT / DELTA  # 1.274
SL_SIGMA_STRESS = abs(SL_STRESS) * PREMIUM_MULT / DELTA  # 1.456

# 30 DTE put parameters (BASELINE / ROLLUP baseline layer)
PUT_TP        =  0.30
PUT_SL        = -0.20
PUT_NET_TP    = PUT_TP + SLIP_ENTRY + SLIP_TP      # +0.290
PUT_NET_SL    = PUT_SL + SLIP_ENTRY + SLIP_SL      # -0.223
PUT_TP_SIGMA  = PUT_TP      * PREMIUM_MULT / DELTA  # 1.092
PUT_SL_SIGMA  = abs(PUT_SL) * PREMIUM_MULT / DELTA  # 0.728

# ---- 15 DTE put parameters (DELAYED / ROLLUP confirmation layer) -------------
PREMIUM_MULT_15  = 1.29           # ATM 15-DTE ~ 1.29 * sigma_daily
HOLD_DAYS_15     = 6              # trading bars held (~8 calendar days)

WIN_7D_BARS  = 5                  # trading bars for confirmation (~7 cal days)
WIN_7D_SIGMA = math.sqrt(7.0 / 30.0)   # 0.4830 — underlying fall barrier

PUT_TP_15        =  0.30
PUT_SL_15        = -0.20
PUT_NET_TP_15    = PUT_TP_15 + SLIP_ENTRY + SLIP_TP    # +0.290
PUT_NET_SL_15    = PUT_SL_15 + SLIP_ENTRY + SLIP_SL    # -0.223
PUT_TP_SIGMA_15  = PUT_TP_15      * PREMIUM_MULT_15 / DELTA   # 0.774
PUT_SL_SIGMA_15  = abs(PUT_SL_15) * PREMIUM_MULT_15 / DELTA   # 0.516

# Hard sell at bar HOLD_DAYS_15 of a 15-DTE option.
# At bar 6 (8 cal days elapsed, 7 remaining): theta factor = sqrt(7/15) = 0.683
# Black-Scholes scaling: remaining value = sqrt(remaining/total) * initial
# Hard sell pct = -50% * (1 - sqrt(9/15)) / (1 - sqrt(15/15)) is wrong —
# scale relative to full-expiry: -50% * (1-sqrt(remaining_cal/15)) / (1-sqrt(0))
# Simpler: use same formula as monte_carlo.py footnote:
#   HARD_15 = -50% * (1 - sqrt(9/15)) / (1 - sqrt(0.5))
# = -50% * 0.2254 / 0.2929 ≈ -38.5%
_hard_15_pct = -0.50 * (1 - math.sqrt(9.0 / 15.0)) / (1 - math.sqrt(0.5))
NET_HARD_15  = _hard_15_pct + SLIP_ENTRY + SLIP_HARD   # ≈ -0.400

# Regime-aware allocation (asymmetric CUT_ONLY + mild put BULL cut)
REGIME_SLOPE_UP       = 0.0
REGIME_SLOPE_DOWN     = 1.0
REGIME_SLOPE_PUT_UP   = -0.5
REGIME_SLOPE_PUT_DOWN = None
ALLOC_SCALE_FLOOR     = 0.25
ALLOC_SCALE_CEIL      = 1.75

TIER_ALLOC = {
    'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00,
}
PUT_TIER_ALLOC = {
    'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12,
}
MAX_POSITIONS      = 14
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
PUT_THRESHOLD      = 25
COLLAPSE_THRESHOLD = 0.20

CT_PROMOTE        = True
CT_PUT_TREND_MIN  = 80
CT_CALL_TREND_MAX = 20
CT_CALL_TIER      = 'ultra'
CT_PUT_TIER       = 'put_top'

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']
PUT_MODES       = ['baseline', 'delayed', 'rollup']

WINDOWS = [
    ('2021', date(2021, 1,  1), date(2021, 12, 31)),
    ('2022', date(2022, 1,  1), date(2022, 12, 31)),
    ('2023', date(2023, 1,  1), date(2023, 12, 31)),
    ('2024', date(2024, 1,  1), date(2024, 12, 31)),
    ('2025', date(2025, 1,  1), date(2025, 12, 31)),
    ('5y',   date(2021, 1,  1), date(2026,  4, 15)),
]


# ---- Tier helpers -----------------------------------------------------------

def score_to_tier(score):
    if score >= 95: return 'ultra'
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def put_score_to_tier(score):
    if score <= 15: return 'put_top'
    if score <= 20: return 'put_mid'
    return 'put_low'


def ct_tag(overall, trend, side):
    if not CT_PROMOTE or trend is None:
        return None
    if side == 'put' and overall <= PUT_THRESHOLD and trend >= CT_PUT_TREND_MIN:
        return 'ct_put'
    if side == 'call' and overall >= OVERFLOW_THRESHOLD and trend <= CT_CALL_TREND_MAX:
        return 'ct_call'
    return None


# ---- Data loading -----------------------------------------------------------

def load_breadth_map(d_start, d_end):
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
    return rmap[sorted_dates[idx]] if idx >= 0 else 1.0


def breadth_on_or_before(sorted_dates, bmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    return bmap[sorted_dates[idx]] if idx >= 0 else None


def is_stressed(sorted_dates, bmap, d):
    b = breadth_on_or_before(sorted_dates, bmap, d)
    return b is not None and b <= BREADTH_THRESHOLD


def alloc_scale_for(regime_mult, is_put=False):
    delta = regime_mult - 1.0
    if is_put:
        if delta >= 0 and REGIME_SLOPE_PUT_UP is not None:
            slope = REGIME_SLOPE_PUT_UP
        elif delta < 0 and REGIME_SLOPE_PUT_DOWN is not None:
            slope = REGIME_SLOPE_PUT_DOWN
        else:
            slope = 0.0
    else:
        slope = REGIME_SLOPE_UP if delta >= 0 else REGIME_SLOPE_DOWN
    if slope == 0.0:
        return 1.0
    s = 1.0 + slope * delta
    return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, s))


def load_signals(version, d_start, d_end):
    return list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
        .where(
            Score.version == version,
            Score.date >= d_start, Score.date <= d_end,
            Score.overall >= OVERFLOW_THRESHOLD,
        )
        .order_by(Score.date, Score.overall.desc())
    )


def load_put_signals(version, d_start, d_end):
    return list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
        .where(
            Score.version == version,
            Score.date >= d_start, Score.date <= d_end,
            Score.overall <= PUT_THRESHOLD,
        )
        .order_by(Score.date, Score.overall.asc())
    )


def load_price_history(sym_ids, d_start, d_end):
    rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.close, PriceHistory.high, PriceHistory.low
        )
        .where(
            PriceHistory.symbol.in_(sym_ids),
            PriceHistory.date >= d_start - timedelta(days=120),
            PriceHistory.date <= d_end + timedelta(days=30),
        )
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .tuples()
    )
    ph = defaultdict(list)
    for sym_id, d, c, h, l in rows:
        ph[sym_id].append((d, float(c), float(h), float(l)))
    return ph


# ---- Per-trade outcome computation ------------------------------------------

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


def compute_call_outcome(sym_bars, signal_date, stressed):
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
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
    net_tp   = NET_TP_STRESS   if stressed else NET_TP_BASE
    net_sl   = NET_SL_STRESS   if stressed else NET_SL_BASE

    tp_level = entry_price * (1 + tp_sigma * vol / 100)
    sl_level = entry_price * (1 - sl_sigma * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_level
        sl_hit = lows[i]  <= sl_level
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', i - base_idx; break
        if tp_hit:
            kind, exit_bar = 'tp',   i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl',   i - base_idx; break

    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl,
                net_hard=NET_HARD_SELL, vol=vol, entry=entry_price)


def compute_put_outcome_30(sym_bars, signal_date):
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
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

    tp_level = entry_price * (1 - PUT_TP_SIGMA  * vol / 100)
    sl_level = entry_price * (1 + PUT_SL_SIGMA  * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        tp_hit = lows[i]  <= tp_level
        sl_hit = highs[i] >= sl_level
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', i - base_idx; break
        if tp_hit:
            kind, exit_bar = 'tp',   i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl',   i - base_idx; break

    return dict(kind=kind, exit_bar=exit_bar,
                net_tp=PUT_NET_TP, net_sl=PUT_NET_SL, net_hard=NET_HARD_SELL,
                vol=vol, entry=entry_price)


def compute_put_outcome_15(sym_bars, rollup_date):
    """15 DTE put entered at rollup_date close."""
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    try:
        base_idx = dates.index(rollup_date)
    except ValueError:
        return None
    entry_price = closes[base_idx]
    if entry_price <= 0:
        return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    tp_level = entry_price * (1 - PUT_TP_SIGMA_15 * vol / 100)
    sl_level = entry_price * (1 + PUT_SL_SIGMA_15 * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS_15)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = HOLD_DAYS_15
    for i in range(base_idx + 1, end_idx):
        tp_hit = lows[i]  <= tp_level
        sl_hit = highs[i] >= sl_level
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', i - base_idx; break
        if tp_hit:
            kind, exit_bar = 'tp',   i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl',   i - base_idx; break

    return dict(kind=kind, exit_bar=exit_bar,
                net_tp=PUT_NET_TP_15, net_sl=PUT_NET_SL_15, net_hard=NET_HARD_15,
                vol=vol, entry=entry_price)


# ---- Precompute helpers -----------------------------------------------------

def precompute_call_outcomes(signals, ph, breadth_dates, breadth_map):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        stressed = is_stressed(breadth_dates, breadth_map, sig.date)
        r = compute_call_outcome(sym_bars, sig.date, stressed)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def precompute_put_outcomes_30(signals, ph):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        r = compute_put_outcome_30(sym_bars, sig.date)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def precompute_rollup_data(put_signals, ph):
    """
    For each put signal check if underlying fell WIN_7D_SIGMA*vol within
    WIN_7D_BARS trading bars. If confirmed, compute 15 DTE outcome from
    bar WIN_7D_BARS.

    Returns:
        rollup_outcomes  : {(sym_id, rollup_date): 15 DTE outcome dict}
        signal_to_rollup : {(sym_id, signal_date): rollup_date | None}
    """
    rollup_outcomes  = {}
    signal_to_rollup = {}

    for sig in put_signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            signal_to_rollup[(sig.symbol_id, sig.date)] = None
            continue

        dates  = [b[0] for b in sym_bars]
        closes = [b[1] for b in sym_bars]
        lows   = [b[3] for b in sym_bars]

        try:
            base_idx = dates.index(sig.date)
        except ValueError:
            signal_to_rollup[(sig.symbol_id, sig.date)] = None
            continue

        entry_price = closes[base_idx]
        if entry_price <= 0:
            signal_to_rollup[(sig.symbol_id, sig.date)] = None
            continue

        vol = realized_vol(closes, base_idx)
        if vol is None or vol <= 0:
            signal_to_rollup[(sig.symbol_id, sig.date)] = None
            continue

        rollup_bar_idx = base_idx + WIN_7D_BARS
        if rollup_bar_idx >= len(dates):
            signal_to_rollup[(sig.symbol_id, sig.date)] = None
            continue

        rollup_date = dates[rollup_bar_idx]

        # 7d win: any intraday low within WIN_7D_BARS bars drops >= WIN_7D_SIGMA*vol
        win_level = entry_price * (1 - WIN_7D_SIGMA * vol / 100)
        confirmed = any(
            lows[i] <= win_level
            for i in range(base_idx + 1, base_idx + 1 + WIN_7D_BARS)
            if i < len(dates)
        )

        if not confirmed:
            signal_to_rollup[(sig.symbol_id, sig.date)] = None
            continue

        signal_to_rollup[(sig.symbol_id, sig.date)] = rollup_date

        # Compute 15 DTE outcome (keep first/best per (sym_id, rollup_date))
        key = (sig.symbol_id, rollup_date)
        if key not in rollup_outcomes:
            r = compute_put_outcome_15(sym_bars, rollup_date)
            if r is not None:
                rollup_outcomes[key] = r

    return rollup_outcomes, signal_to_rollup


# ---- Position / resolve -----------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost',
                 'option_pnl', 'outcome', 'side', 'net_hard']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost,
                 option_pnl, outcome, side, net_hard=None):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome
        self.side         = side
        self.net_hard     = net_hard if net_hard is not None else NET_HARD_SELL


def resolve_v(kind, mode, rng, net_tp, net_sl, net_hard):
    if kind == 'tp':   return 'tp',   net_tp
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', net_hard
    # 'both' — collision
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', net_tp
    return ('tp', net_tp) if rng.random() < 0.5 else ('sl', net_sl)


# ---- Portfolio simulation ---------------------------------------------------

def run_single_sim_v(trading_days, calls_by_date, call_outcomes,
                     puts_by_date, put_outcomes_30,
                     rollup_puts_by_date, rollup_outcomes,
                     mode, rng, put_mode,
                     regime_dates=None, regime_map=None):
    """
    put_mode:
      'baseline' — 30 DTE puts at signal date only
      'delayed'  — 15 DTE puts at rollup_date only (confirmed signals)
      'rollup'   — baseline 30 DTE + rollup 15 DTE layer (if slot available)
    """
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c = sl_c = hard_c = 0
    tp_p = sl_p = hard_p = 0

    for day_idx, today in enumerate(trading_days):
        # Close expired positions
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.premium_cost * (1 + p.option_pnl)
                if p.side == 'call':
                    if   p.outcome == 'tp': tp_c   += 1
                    elif p.outcome == 'sl': sl_c   += 1
                    else:                   hard_c += 1
                else:
                    if   p.outcome == 'tp': tp_p   += 1
                    elif p.outcome == 'sl': sl_p   += 1
                    else:                   hard_p += 1
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

        open_syms  = {p.sym_id for p in positions}
        call_open  = sum(1 for p in positions if p.side == 'call')

        # ---- Calls ----------------------------------------------------------
        day_calls = calls_by_date.get(today, [])
        if day_calls:
            eligible = [(sid, sc, k, ct) for sid, sc, k, ct in day_calls
                        if k in call_outcomes and sid not in open_syms]
            primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD or e[3] == 'ct_call']
            overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD and e[3] != 'ct_call']
            primary.sort(key=lambda x: (0 if x[3] == 'ct_call' else 1, -x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            reg_mult    = regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = alloc_scale_for(reg_mult, is_put=False)
            for sym_id, score, key, ct in primary + overflow:
                if len(positions) >= MAX_POSITIONS: break
                tier         = CT_CALL_TIER if ct == 'ct_call' else score_to_tier(score)
                alloc_frac   = TIER_ALLOC[tier] * reg_scale_c
                premium_cost = portfolio_value * alloc_frac
                if premium_cost > cash or premium_cost <= 0: continue
                o = call_outcomes[key]
                outcome, pnl = resolve_v(o['kind'], mode, rng, o['net_tp'], o['net_sl'], o['net_hard'])
                cash -= premium_cost
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'call', o['net_hard']))
                open_syms.add(sym_id)
                call_open += 1

        # ---- Puts -----------------------------------------------------------
        if len(positions) < MAX_POSITIONS:
            reg_mult    = regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_p = alloc_scale_for(reg_mult, is_put=True)

            # BASELINE or ROLLUP: enter 30 DTE puts at signal date
            if put_mode in ('baseline', 'rollup'):
                day_puts = puts_by_date.get(today, [])
                if day_puts:
                    pe = [(sid, sc, k, ct) for sid, sc, k, ct in day_puts
                          if k in put_outcomes_30 and sid not in open_syms]
                    pe.sort(key=lambda x: (0 if x[3] == 'ct_put' else 1, x[1], rng.random()))
                    for sym_id, score, key, ct in pe:
                        if len(positions) >= MAX_POSITIONS: break
                        tier         = CT_PUT_TIER if ct == 'ct_put' else put_score_to_tier(score)
                        alloc_frac   = PUT_TIER_ALLOC[tier] * reg_scale_p
                        premium_cost = portfolio_value * alloc_frac
                        if premium_cost > cash or premium_cost <= 0: continue
                        o = put_outcomes_30[key]
                        outcome, pnl = resolve_v(o['kind'], mode, rng, o['net_tp'], o['net_sl'], o['net_hard'])
                        cash -= premium_cost
                        positions.append(Position(sym_id, today, o['exit_bar'],
                                                   premium_cost, pnl, outcome, 'put', o['net_hard']))
                        open_syms.add(sym_id)

            # DELAYED or ROLLUP: enter 15 DTE puts at rollup_date (confirmed only)
            # same-sym block: if baseline put still open, rollup skipped naturally
            if put_mode in ('delayed', 'rollup') and len(positions) < MAX_POSITIONS:
                day_rollups = rollup_puts_by_date.get(today, [])
                if day_rollups:
                    re = [(sid, sc, k, ct) for sid, sc, k, ct in day_rollups
                          if k in rollup_outcomes and sid not in open_syms]
                    re.sort(key=lambda x: (0 if x[3] == 'ct_put' else 1, x[1], rng.random()))
                    for sym_id, score, key, ct in re:
                        if len(positions) >= MAX_POSITIONS: break
                        tier         = CT_PUT_TIER if ct == 'ct_put' else put_score_to_tier(score)
                        alloc_frac   = PUT_TIER_ALLOC[tier] * reg_scale_p
                        premium_cost = portfolio_value * alloc_frac
                        if premium_cost > cash or premium_cost <= 0: continue
                        o = rollup_outcomes[key]
                        outcome, pnl = resolve_v(o['kind'], mode, rng, o['net_tp'], o['net_sl'], o['net_hard'])
                        cash -= premium_cost
                        positions.append(Position(sym_id, today, o['exit_bar'],
                                                   premium_cost, pnl, outcome, 'put', o['net_hard']))
                        open_syms.add(sym_id)

    # End-of-window liquidation at each position's own net_hard
    for p in positions:
        cash += p.premium_cost * (1 + p.net_hard)
        if p.side == 'call': hard_c += 1
        else:                hard_p += 1
    portfolio_value = cash

    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    ct = tp_c + sl_c + hard_c or 1
    pt = tp_p + sl_p + hard_p or 1
    return dict(
        final=portfolio_value, max_dd=max_dd,
        call_tp=tp_c/ct*100,  call_sl=sl_c/ct*100,  call_hard=hard_c/ct*100,
        put_tp=tp_p/pt*100,   put_sl=sl_p/pt*100,   put_hard=hard_p/pt*100,
        call_trades=tp_c+sl_c+hard_c,
        put_trades=tp_p+sl_p+hard_p,
    )


# ---- Window runner ----------------------------------------------------------

def run_window_variants(label, d_start, d_end, version):
    print(f"\n{'='*130}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print('='*130)

    call_sigs = load_signals(version, d_start, d_end)
    put_sigs  = load_put_signals(version, d_start, d_end)
    primary_n = sum(1 for s in call_sigs if s.overall >= PRIMARY_THRESHOLD)
    print(f"Call signals: {len(call_sigs)} (75+={primary_n})  |  Put signals <=25: {len(put_sigs)}")

    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph      = load_price_history(sym_ids, d_start, d_end)

    breadth_dates, breadth_map = load_breadth_map(d_start, d_end)
    regime_dates,  regime_map  = load_regime_map(d_start, d_end)

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    print("Precomputing call outcomes...", end=' ', flush=True)
    call_outcomes = precompute_call_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    print(f"N={len(call_outcomes)}")

    print("Precomputing 30 DTE put outcomes...", end=' ', flush=True)
    put_outcomes_30 = precompute_put_outcomes_30(put_sigs, ph)
    print(f"N={len(put_outcomes_30)}")

    print("Precomputing rollup (7d confirm + 15 DTE)...", end=' ', flush=True)
    rollup_outcomes, signal_to_rollup = precompute_rollup_data(put_sigs, ph)
    confirmed_n = sum(1 for v in signal_to_rollup.values() if v is not None)
    n_put = len(put_sigs) or 1
    print(f"Confirmed={confirmed_n}/{len(put_sigs)} ({confirmed_n/n_put*100:.1f}%)  "
          f"15 DTE outcomes={len(rollup_outcomes)}")

    # Count raw 15 DTE TP/SL/hard distribution
    if rollup_outcomes:
        ru_tp   = sum(1 for o in rollup_outcomes.values() if o['kind'] == 'tp')
        ru_sl   = sum(1 for o in rollup_outcomes.values() if o['kind'] == 'sl')
        ru_both = sum(1 for o in rollup_outcomes.values() if o['kind'] == 'both')
        ru_hard = sum(1 for o in rollup_outcomes.values() if o['kind'] == 'hard')
        rt = len(rollup_outcomes)
        print(f"  15 DTE raw: TP={ru_tp/rt*100:.1f}%  SL={ru_sl/rt*100:.1f}%  "
              f"Both={ru_both/rt*100:.1f}%  Hard={ru_hard/rt*100:.1f}%")

    # Build date-indexed signal lists
    calls_by_date = defaultdict(list)
    for sig in call_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = ct_tag(sig.overall, sig.trend, 'call')
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct))

    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = ct_tag(sig.overall, sig.trend, 'put')
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct))

    # Rollup entries: deduplicate by (sym_id, rollup_date) — keep most bearish score
    _rollup_best = {}   # (sym_id, rollup_date) -> (sym_id, score, rollup_key, ct)
    for sig in put_sigs:
        rollup_date = signal_to_rollup.get((sig.symbol_id, sig.date))
        if rollup_date is None:
            continue
        rollup_key = (sig.symbol_id, rollup_date)
        if rollup_key not in rollup_outcomes:
            continue
        ct  = ct_tag(sig.overall, sig.trend, 'put')
        existing = _rollup_best.get(rollup_key)
        if existing is None or sig.overall < existing[1]:
            _rollup_best[rollup_key] = (sig.symbol_id, sig.overall, rollup_key, ct)

    rollup_puts_by_date = defaultdict(list)
    for rollup_key, entry in _rollup_best.items():
        rollup_puts_by_date[rollup_key[1]].append(entry)   # key[1] = rollup_date

    delayed_n = sum(len(v) for v in rollup_puts_by_date.values())
    print(f"Rollup entries across {len(rollup_puts_by_date)} dates: {delayed_n} unique (sym, rollup_date) pairs")

    # Header
    hdr = (f"\n{'Variant':<10}  {'Mode':<13}  {'CTP%':>5}  {'PTP%':>5}  "
           f"{'CTrd':>5}  {'PTrd':>5}  {'MeanRet':>14}  {'WorstDD':>8}  {'P(col)':>7}")
    print(hdr)
    print('-'*100)

    all_results = {}
    for put_mode in PUT_MODES:
        variant_results = {}
        for mode in COLLISION_MODES:
            finals=[]; dds=[]; ctps=[]; ptps=[]; ctrd_l=[]; ptrd_l=[]; collapses=0
            for it in range(N_ITER):
                rng = random.Random(1000 * hash(label) + 7 * hash(put_mode) + it)
                r = run_single_sim_v(
                    trading_days, calls_by_date, call_outcomes,
                    puts_by_date, put_outcomes_30,
                    rollup_puts_by_date, rollup_outcomes,
                    mode, rng, put_mode,
                    regime_dates, regime_map,
                )
                finals.append(r['final']); dds.append(r['max_dd'])
                ctps.append(r['call_tp']); ptps.append(r['put_tp'])
                ctrd_l.append(r['call_trades']); ptrd_l.append(r['put_trades'])
                if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                    collapses += 1

            mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
            worst_dd = max(dds) * 100
            p_coll   = collapses / N_ITER * 100

            variant_results[mode] = dict(
                mean_ret=mean_ret, worst_dd=worst_dd, p_coll=p_coll,
                call_tp=statistics.mean(ctps), put_tp=statistics.mean(ptps),
                call_trades=statistics.mean(ctrd_l), put_trades=statistics.mean(ptrd_l),
            )
            vr = variant_results[mode]
            print(f"{put_mode:<10}  {mode:<13}  {vr['call_tp']:>4.1f}%  {vr['put_tp']:>4.1f}%  "
                  f"{vr['call_trades']:>5.1f}  {vr['put_trades']:>5.1f}  "
                  f"{mean_ret:>+13.1f}%  {worst_dd:>7.1f}%  {p_coll:>6.1f}%")
        all_results[put_mode] = variant_results

    return all_results


# ---- Main -------------------------------------------------------------------

def main():
    print('='*130)
    print("PUT ROLLUP VARIANTS — Three put-entry strategy comparison")
    print('='*130)
    print(f"BASELINE  : 30 DTE put at signal date (production)")
    print(f"DELAYED   : 15 DTE put at day+{WIN_7D_BARS} bars ONLY if 7d barrier confirmed "
          f"(>={WIN_7D_SIGMA:.4f}σ = {WIN_7D_SIGMA*100:.2f}% fall)")
    print(f"ROLLUP    : BASELINE + 15 DTE put at day+{WIN_7D_BARS} bars when confirmed (same-sym block applies)")
    print(f"")
    print(f"30 DTE    : TP={PUT_TP:+.0%} / SL={PUT_SL:+.0%}  sigma TP={PUT_TP_SIGMA:.3f} SL={PUT_SL_SIGMA:.3f}")
    print(f"15 DTE    : TP={PUT_TP_15:+.0%} / SL={PUT_SL_15:+.0%}  sigma TP={PUT_TP_SIGMA_15:.3f} SL={PUT_SL_SIGMA_15:.3f}")
    print(f"Net 30 DTE: TP={PUT_NET_TP:+.3f} / SL={PUT_NET_SL:+.3f} / Hard={NET_HARD_SELL:+.3f} (bar {HOLD_DAYS})")
    print(f"Net 15 DTE: TP={PUT_NET_TP_15:+.3f} / SL={PUT_NET_SL_15:+.3f} / Hard={NET_HARD_15:+.3f} (bar {HOLD_DAYS_15})")
    print(f"Start     : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"BE (both) : 43.5%  |  Bridge WR (puts 7d->8cal): 63% (N=32,720)")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}\n")

    all_window_results = {}
    for label, d_start, d_end in WINDOWS:
        all_window_results[label] = run_window_variants(label, d_start, d_end, version)

    # ---- Summary: Realistic mode, all windows x variants -------------------
    print('\n' + '='*130)
    print("SUMMARY — Realistic Mode: MeanRet / WorstDD / PutTP% / PTrades  by Window x Variant")
    print('='*130)
    col_w = 38
    print(f"{'Window':<8}  " + '  '.join(f"{m:<{col_w}}" for m in PUT_MODES))
    print('-'*130)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for put_mode in PUT_MODES:
            r = all_window_results[label][put_mode]['realistic']
            cell = f"{r['mean_ret']:>+12,.1f}%  DD={r['worst_dd']:>5.1f}%  PTP={r['put_tp']:>4.1f}%"
            row += f"{cell:<{col_w}}  "
        print(row)

    # ---- Delta table vs BASELINE (Realistic) --------------------------------
    print('\n' + '='*130)
    print("DELTA vs BASELINE — Realistic Mode: MeanRet Δ / WorstDD Δ / PTP% Δ")
    print('='*130)
    print(f"{'Window':<8}  " + '  '.join(f"{m:<{col_w}}" for m in PUT_MODES))
    print('-'*130)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        base = all_window_results[label]['baseline']['realistic']
        for put_mode in PUT_MODES:
            r = all_window_results[label][put_mode]['realistic']
            d_ret = r['mean_ret'] - base['mean_ret']
            d_dd  = r['worst_dd'] - base['worst_dd']
            d_ptp = r['put_tp']   - base['put_tp']
            cell = f"Δret={d_ret:>+10,.1f}%  ΔDD={d_dd:>+5.1f}pp  ΔPTP={d_ptp:>+4.1f}pp"
            row += f"{cell:<{col_w}}  "
        print(row)


if __name__ == '__main__':
    main()
