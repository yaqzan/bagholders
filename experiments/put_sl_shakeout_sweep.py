"""
Put SL Shakeout Sweep
=====================
Tests PUT_SL at -20% / -30% / -35% / -40% / -45% against the canonical MC
framework to determine if widening the put stop-loss materially improves
portfolio outcomes.

Motivation: put_shakeout_profile.py found that trades stopped out at 0.728s
(the current -20% SL trigger) have the following recovery rates:
  MILD  shakeout (0.73-1.0s): 88-95% recovery  -> EV(hold) >> EV(recycle)
  MOD   shakeout (1.0-1.5s) : 77-86% recovery  -> EV(hold) >  EV(recycle)
  EXTREME shakeout (>1.5s)  : 27-31% recovery  -> EV(recycle) wins

The natural SL threshold implied by shakeout data is ~1.5s adverse, which
corresponds to ~40-45% option premium loss at 30 DTE (1.82*sigma/0.5 * 1.5).

This sweep validates whether that wider SL improves MC portfolio outcomes.

Report per variant:
  - Raw put TP rate per window (underlying barrier hit)
  - Mean/Median return per window, all 3 collision modes
  - Worst DD-C (Conservative mode) per window
  - 80% floor pass/fail
  - Summary comparison table vs baseline (-20%)

Usage: python experiments/put_sl_shakeout_sweep.py
"""

import os
import sys
import io
import math
import random
import bisect
import statistics
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.models.core import Score, AlgorithmVersion, MarketBreadth, MarketRegime
from database.models.technical import PriceHistory

# ---- Constants (locked from monte_carlo.py) ---------------------------------
STARTING_CASH      = 50_000.0
N_ITER             = 200          # reduced for sweep speed; increase to 500 for final validation
VOL_LOOKBACK       = 60
HOLD_DAYS          = 15
PREMIUM_MULT       = 1.82
DELTA              = 0.5

TP_BASE            =  0.30
TP_STRESS          =  0.35
SL_BASE            = -0.35
SL_STRESS          = -0.40
HARD_SELL_LOSS     = -0.50
BREADTH_THRESHOLD  = 50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_TP_BASE   = TP_BASE   + SLIP_ENTRY + SLIP_TP
NET_TP_STRESS = TP_STRESS + SLIP_ENTRY + SLIP_TP
NET_SL_BASE   = SL_BASE   + SLIP_ENTRY + SLIP_SL
NET_SL_STRESS = SL_STRESS + SLIP_ENTRY + SLIP_SL
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD

TP_SIGMA_BASE   = TP_BASE   * PREMIUM_MULT / DELTA
TP_SIGMA_STRESS = TP_STRESS * PREMIUM_MULT / DELTA
SL_SIGMA_BASE   = abs(SL_BASE)   * PREMIUM_MULT / DELTA
SL_SIGMA_STRESS = abs(SL_STRESS) * PREMIUM_MULT / DELTA

PUT_TP         = 0.30
PUT_TP_SIGMA   = PUT_TP * PREMIUM_MULT / DELTA       # 1.092 (fixed)
PUT_NET_TP     = PUT_TP + SLIP_ENTRY + SLIP_TP       # +0.290

TIER_ALLOC = {
    'ultra':    0.25,
    'top':      0.15,
    'mid':      0.15,
    'low':      0.15,
    'overflow': 0.00,
}
PUT_TIER_ALLOC = {
    'put_top': 0.15,
    'put_mid': 0.12,
    'put_low': 0.12,
}
MAX_POSITIONS      = 14
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
PUT_THRESHOLD      = 25
COLLAPSE_THRESHOLD = 0.20

# Regime-aware allocation (locked from monte_carlo.py)
REGIME_SLOPE_UP       = 0.0
REGIME_SLOPE_DOWN     = 1.0
REGIME_SLOPE_PUT_UP   = -0.5
REGIME_SLOPE_PUT_DOWN = None
ALLOC_SCALE_FLOOR     = 0.25
ALLOC_SCALE_CEIL      = 1.75

# CT promotion (locked)
CT_PROMOTE       = True
CT_PUT_TREND_MIN = 80
CT_CALL_TREND_MAX = 20
CT_CALL_TIER     = 'ultra'
CT_PUT_TIER      = 'put_top'

# ---- Sweep variants ---------------------------------------------------------
PUT_SL_VALUES = [-0.20, -0.30, -0.35, -0.40, -0.45]

WINDOWS = [
    ('2021', date(2021, 1, 1), date(2021, 12, 31)),
    ('2022', date(2022, 1, 1), date(2022, 12, 31)),
    ('2023', date(2023, 1, 1), date(2023, 12, 31)),
    ('2024', date(2024, 1, 1), date(2024, 12, 31)),
    ('2025', date(2025, 1, 1), date(2025, 12, 31)),
    ('5y',   date(2021, 1, 1), date(2026, 4, 15)),
]
COLLISION_MODES = ['conservative', 'realistic', 'optimistic']


# ---- Helpers ----------------------------------------------------------------

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
        if delta >= 0:
            slope = REGIME_SLOPE_UP
        else:
            slope = REGIME_SLOPE_DOWN
    if slope == 0.0:
        return 1.0
    s = 1.0 + slope * delta
    return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, s))


def load_signals(version, d_start, d_end):
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
    return list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
        .where(
            Score.version == version,
            Score.date >= d_start,
            Score.date <= d_end,
            Score.overall <= PUT_THRESHOLD,
        )
        .order_by(Score.date, Score.overall.asc())
    )


def load_price_history(sym_ids, d_start, d_end):
    ph_start = d_start - timedelta(days=120)
    ph_end   = d_end   + timedelta(days=30)
    rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.close, PriceHistory.high, PriceHistory.low,
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


# ---- Outcome computation (parameterized by PUT_SL) --------------------------

def compute_call_outcome(sym_bars, signal_date, stressed):
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    entry = closes[base_idx]
    if entry <= 0:
        return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    tp_sigma = TP_SIGMA_STRESS if stressed else TP_SIGMA_BASE
    sl_sigma = SL_SIGMA_STRESS if stressed else SL_SIGMA_BASE
    net_tp   = NET_TP_STRESS   if stressed else NET_TP_BASE
    net_sl   = NET_SL_STRESS   if stressed else NET_SL_BASE

    tp_level = entry * (1 + tp_sigma * vol / 100)
    sl_level = entry * (1 - sl_sigma * vol / 100)

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
                vol=vol, entry=entry)


def compute_put_outcome_param(sym_bars, signal_date, put_sl):
    """Parameterized version: put_sl is the option premium SL (e.g. -0.20)."""
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    entry = closes[base_idx]
    if entry <= 0:
        return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    sl_sigma = abs(put_sl) * PREMIUM_MULT / DELTA
    net_sl   = put_sl + SLIP_ENTRY + SLIP_SL
    net_tp   = PUT_NET_TP

    tp_level = entry * (1 - PUT_TP_SIGMA * vol / 100)  # put wins if price falls
    sl_level = entry * (1 + sl_sigma      * vol / 100)  # put loses if price rises

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

    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl,
                vol=vol, entry=entry)


def precompute_call_outcomes(signals, ph, breadth_dates, breadth_map):
    outcomes = {}
    for sig in signals:
        bars = ph.get(sig.symbol_id)
        if not bars:
            continue
        stressed = is_stressed(breadth_dates, breadth_map, sig.date)
        r = compute_call_outcome(bars, sig.date, stressed)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def precompute_put_outcomes_param(signals, ph, put_sl):
    outcomes = {}
    for sig in signals:
        bars = ph.get(sig.symbol_id)
        if not bars:
            continue
        r = compute_put_outcome_param(bars, sig.date, put_sl)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


# ---- Portfolio simulation ----------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl', 'outcome', 'side']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome, side):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome
        self.side         = side


def resolve(kind, mode, rng, net_tp, net_sl):
    if kind == 'tp':   return 'tp',   net_tp
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', net_tp
    return ('tp', net_tp) if rng.random() < 0.5 else ('sl', net_sl)


def run_single_sim(trading_days, calls_by_date, call_outcomes,
                   puts_by_date, put_outcomes, mode, rng,
                   regime_dates, regime_map):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}
    tp_c = sl_c = hard_c = 0
    tp_p = sl_p = hard_p = 0

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

        open_syms  = {p.sym_id for p in positions}
        call_open  = sum(1 for p in positions if p.side == 'call')
        put_open   = sum(1 for p in positions if p.side == 'put')

        # Calls first
        day_calls = calls_by_date.get(today, [])
        if day_calls:
            eligible = [(sid, sc, k, ct) for sid, sc, k, ct in day_calls
                        if k in call_outcomes and sid not in open_syms]
            primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD or e[3] == 'ct_call']
            overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD and e[3] != 'ct_call']
            primary.sort(key=lambda x: (0 if x[3] == 'ct_call' else 1, -x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            reg_mult    = regime_on_or_before(regime_dates, regime_map, today)
            reg_scale_c = alloc_scale_for(reg_mult, is_put=False)
            for sym_id, score, key, ct in primary + overflow:
                if len(positions) >= MAX_POSITIONS:
                    break
                tier         = CT_CALL_TIER if ct == 'ct_call' else score_to_tier(score)
                alloc_frac   = TIER_ALLOC[tier] * reg_scale_c
                premium_cost = portfolio_value * alloc_frac
                if premium_cost > cash or premium_cost <= 0:
                    continue
                o = call_outcomes[key]
                outcome, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= premium_cost
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'call'))
                open_syms.add(sym_id)
                call_open += 1

        # Puts
        remaining = MAX_POSITIONS - len(positions)
        if remaining > 0:
            day_puts = puts_by_date.get(today, [])
            if day_puts:
                pe = [(sid, sc, k, ct) for sid, sc, k, ct in day_puts
                      if k in put_outcomes and sid not in open_syms]
                pe.sort(key=lambda x: (0 if x[3] == 'ct_put' else 1, x[1], rng.random()))
                reg_mult    = regime_on_or_before(regime_dates, regime_map, today)
                reg_scale_p = alloc_scale_for(reg_mult, is_put=True)
                for sym_id, score, key, ct in pe:
                    if len(positions) >= MAX_POSITIONS:
                        break
                    tier         = CT_PUT_TIER if ct == 'ct_put' else put_score_to_tier(score)
                    alloc_frac   = PUT_TIER_ALLOC[tier] * reg_scale_p
                    premium_cost = portfolio_value * alloc_frac
                    if premium_cost > cash or premium_cost <= 0:
                        continue
                    o = put_outcomes[key]
                    outcome, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                    cash -= premium_cost
                    positions.append(Position(sym_id, today, o['exit_bar'],
                                               premium_cost, pnl, outcome, 'put'))
                    open_syms.add(sym_id)
                    put_open += 1

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
        final      = portfolio_value,
        max_dd     = max_dd,
        call_tp    = tp_c / ct * 100,
        put_tp     = tp_p / pt * 100,
        put_sl_pct = sl_p / pt * 100,
        call_trades = ct,
        put_trades  = pt,
    )


# ---- Per-SL window runner ---------------------------------------------------

def run_window_for_sl(label, d_start, d_end, version, put_sl,
                      call_sigs, put_sigs, ph,
                      breadth_dates, breadth_map,
                      regime_dates, regime_map,
                      trading_days, calls_by_date, puts_by_date,
                      call_outcomes):
    """Run MC for a single (window, PUT_SL) combination."""
    # Re-compute put outcomes with this SL sigma
    put_outcomes = precompute_put_outcomes_param(put_sigs, ph, put_sl)

    sl_sigma    = abs(put_sl) * PREMIUM_MULT / DELTA
    pt_tp  = sum(1 for o in put_outcomes.values() if o['kind'] == 'tp')
    pt_sl  = sum(1 for o in put_outcomes.values() if o['kind'] == 'sl')
    pt_both= sum(1 for o in put_outcomes.values() if o['kind'] == 'both')
    pt_hard= sum(1 for o in put_outcomes.values() if o['kind'] == 'hard')
    pt     = len(put_outcomes) or 1
    raw_tp_pct  = pt_tp  / pt * 100
    raw_sl_pct  = pt_sl  / pt * 100
    raw_both_pct= pt_both / pt * 100
    raw_hard_pct= pt_hard / pt * 100

    results = {}
    for mode in COLLISION_MODES:
        finals = []; dds = []; ptps = []; ctps = []; collapses = 0
        for it in range(N_ITER):
            rng = random.Random(1000 * hash(label) + it)
            r = run_single_sim(trading_days, calls_by_date, call_outcomes,
                               puts_by_date, put_outcomes, mode, rng,
                               regime_dates, regime_map)
            finals.append(r['final'])
            dds.append(r['max_dd'])
            ptps.append(r['put_tp'])
            ctps.append(r['call_tp'])
            if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                collapses += 1

        mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
        worst_dd = max(dds) * 100
        mean_dd  = statistics.mean(dds) * 100
        p_coll   = collapses / N_ITER * 100
        results[mode] = dict(
            mean_ret  = mean_ret,
            worst_dd  = worst_dd,
            mean_dd   = mean_dd,
            p_coll    = p_coll,
            put_tp    = statistics.mean(ptps),
            call_tp   = statistics.mean(ctps),
            mean_final= statistics.mean(finals),
        )

    return results, raw_tp_pct, raw_sl_pct, raw_both_pct, raw_hard_pct, sl_sigma


# ---- Main -------------------------------------------------------------------

def main():
    print('=' * 110)
    print("PUT SL SHAKEOUT SWEEP — put_sl in {-20%, -30%, -35%, -40%, -45%}")
    print('=' * 110)
    print(f"Motivation: shakeout profile shows MILD (0.73-1.0s) shakeouts recover 88-95% -> current -20% SL cuts winners")
    print(f"Implied optimal SL from shakeout data: ~1.5s adverse = ~40-45% option loss")
    print(f"N_ITER={N_ITER}  STARTING_CASH=${STARTING_CASH:,.0f}  HOLD_DAYS={HOLD_DAYS}")
    print(f"Call exits: breadth-adaptive TP=30/35% SL=35/40% (locked, unchanged)")
    print(f"Put TP: {PUT_TP:+.0%} fixed, TP_SIGMA={PUT_TP_SIGMA:.3f}s (locked)")
    print()

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}  id={version.id}")
    print()

    # Pre-load data per window (shared across SL variants)
    window_data = {}
    for label, d_start, d_end in WINDOWS:
        print(f"Loading data for window {label} ({d_start} -> {d_end})...", flush=True)
        call_sigs = load_signals(version, d_start, d_end)
        put_sigs  = load_put_signals(version, d_start, d_end)
        sym_ids   = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
        ph        = load_price_history(sym_ids, d_start, d_end)
        bd, bm    = load_breadth_map(d_start, d_end)
        rd, rm    = load_regime_map(d_start, d_end)

        ph_dates = set()
        for bars in ph.values():
            for b in bars:
                if d_start <= b[0] <= d_end + timedelta(days=20):
                    ph_dates.add(b[0])
        trading_days = sorted(ph_dates)

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

        print(f"  call_sigs={len(call_sigs)} put_sigs={len(put_sigs)} trading_days={len(trading_days)}")

        call_outcomes = precompute_call_outcomes(call_sigs, ph, bd, bm)
        print(f"  call_outcomes={len(call_outcomes)}")

        window_data[label] = dict(
            d_start=d_start, d_end=d_end,
            call_sigs=call_sigs, put_sigs=put_sigs, ph=ph,
            bd=bd, bm=bm, rd=rd, rm=rm,
            trading_days=trading_days,
            calls_by_date=calls_by_date, puts_by_date=puts_by_date,
            call_outcomes=call_outcomes,
        )

    print()
    print('=' * 110)
    print("RUNNING SWEEP")
    print('=' * 110)

    # Results store: [put_sl][window][mode]
    all_results = {}

    for put_sl in PUT_SL_VALUES:
        sl_label = f"SL={put_sl:+.0%}"
        sl_sigma  = abs(put_sl) * PREMIUM_MULT / DELTA
        net_sl    = put_sl + SLIP_ENTRY + SLIP_SL
        be_tp_rate = abs(net_sl) / (PUT_NET_TP + abs(net_sl)) * 100
        print(f"\n{'='*110}")
        print(f"  PUT_SL={put_sl:+.0%}  SL_SIGMA={sl_sigma:.3f}s  NET_SL={net_sl:+.3f}  BE_TP={be_tp_rate:.1f}%")
        print(f"{'='*110}")

        all_results[put_sl] = {}

        for label, d_start, d_end in WINDOWS:
            wd = window_data[label]
            print(f"\n  Window {label}:", end=' ', flush=True)

            res, raw_tp, raw_sl, raw_both, raw_hard, sl_sig = run_window_for_sl(
                label, d_start, d_end, version, put_sl,
                wd['call_sigs'], wd['put_sigs'], wd['ph'],
                wd['bd'], wd['bm'], wd['rd'], wd['rm'],
                wd['trading_days'], wd['calls_by_date'], wd['puts_by_date'],
                wd['call_outcomes'],
            )
            all_results[put_sl][label] = res
            all_results[put_sl][label]['_raw'] = dict(
                tp=raw_tp, sl=raw_sl, both=raw_both, hard=raw_hard)

            print(f"raw put TP={raw_tp:.1f}% SL={raw_sl:.1f}% Both={raw_both:.1f}% Hard={raw_hard:.1f}%")
            # Print per-mode line
            for mode in COLLISION_MODES:
                r = res[mode]
                floor_flag = ' ***BREACH***' if r['worst_dd'] > 80.0 and mode == 'conservative' else ''
                print(f"    {mode:<13}  PutTP={r['put_tp']:.1f}%  "
                      f"MeanRet={r['mean_ret']:>+12,.1f}%  WorstDD={r['worst_dd']:.1f}%{floor_flag}")

    # ---- Summary Table ---------------------------------------------------------
    print()
    print('=' * 130)
    print("SUMMARY — Raw Put TP% by variant × window  (underlying barrier hit)")
    print('=' * 130)
    hdr = f"{'Window':<8}  " + '  '.join(f"{'SL='+f'{v:+.0%}':>10}" for v in PUT_SL_VALUES)
    print(hdr)
    print('-' * 130)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for put_sl in PUT_SL_VALUES:
            raw = all_results[put_sl][label].get('_raw', {})
            row += f"{raw.get('tp', 0):>9.1f}%  "
        print(row)

    print()
    print('=' * 130)
    print("SUMMARY — Portfolio Put TP% (realistic mode)")
    print('=' * 130)
    print(hdr)
    print('-' * 130)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for put_sl in PUT_SL_VALUES:
            r = all_results[put_sl][label].get('realistic', {})
            row += f"{r.get('put_tp', 0):>9.1f}%  "
        print(row)

    print()
    print('=' * 130)
    print("SUMMARY — Realistic Mean Return%  (baseline = SL=-20%)")
    print('=' * 130)
    print(hdr)
    print('-' * 130)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        base = all_results[PUT_SL_VALUES[0]][label].get('realistic', {}).get('mean_ret', 0)
        for put_sl in PUT_SL_VALUES:
            r = all_results[put_sl][label].get('realistic', {})
            val = r.get('mean_ret', 0)
            marker = ' *' if put_sl != PUT_SL_VALUES[0] and val > base else '  '
            row += f"{val:>+9,.0f}%{marker} "
        print(row)

    print()
    print('=' * 130)
    print("SUMMARY — Conservative WorstDD%  (floor = 80%)")
    print('=' * 130)
    print(hdr)
    print('-' * 130)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for put_sl in PUT_SL_VALUES:
            r = all_results[put_sl][label].get('conservative', {})
            dd = r.get('worst_dd', 0)
            flag = '!' if dd > 80.0 else ' '
            row += f"{dd:>8.1f}%{flag} "
        print(row)

    print()
    print('=' * 130)
    print("SUMMARY — Floor compliance (Conservative WorstDD <= 80%):  PASS / FAIL")
    print('=' * 130)
    print(hdr)
    print('-' * 130)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for put_sl in PUT_SL_VALUES:
            r = all_results[put_sl][label].get('conservative', {})
            dd = r.get('worst_dd', 0)
            tag = 'PASS' if dd <= 80.0 else 'FAIL'
            row += f"{tag:>10}  "
        print(row)

    print()
    print("INTERPRETATION GUIDE:")
    print("  Raw put TP% = fraction of trades where underlying hit TP_SIGMA (1.092s) within HOLD_DAYS")
    print("    Higher raw TP% is expected as SL widens (fewer premature exits, more time to reach TP)")
    print("  Portfolio PutTP% differs from raw due to cascade allocation timing and position capacity")
    print("  BE TP rate by SL variant:")
    for put_sl in PUT_SL_VALUES:
        sl_sigma = abs(put_sl) * PREMIUM_MULT / DELTA
        net_sl   = put_sl + SLIP_ENTRY + SLIP_SL
        be       = abs(net_sl) / (PUT_NET_TP + abs(net_sl)) * 100
        print(f"    SL={put_sl:+.0%}  SL_SIGMA={sl_sigma:.3f}s  BE={be:.1f}%")
    print()
    print("  The shakeout analysis predicts:")
    print("    SL=-20% (0.728s): cutting 88-95% recoverable MILD shakeouts -> EV(hold) >> EV(recycle)")
    print("    SL=-35% (1.274s): fewer false stops, still clear of the 1.5s MILD/EXTREME boundary")
    print("    SL=-40% (1.456s): approximately the MILD/MOD boundary; cuts MOD shakeouts only")
    print("    SL=-45% (1.638s): past the MOD/EXTREME boundary; allows most shakeouts to recover")
    print()
    print("  If the sweep shows widening SL improves portfolio PutTP and Realistic return WITHOUT")
    print("  breaching the 80% Conservative DD floor -> new optimal SL identified.")
    print("  If widening SL worsens 22-now or breaks DD floor -> current -20% justified by velocity.")


if __name__ == '__main__':
    main()
