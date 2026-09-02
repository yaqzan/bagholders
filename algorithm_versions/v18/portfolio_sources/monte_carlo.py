"""
Monte Carlo — Canonical Optimal Strategy
=========================================
Runs the locked-in optimal strategy across per-year windows (2021-2025) and
the continuous 5-year window (Jan 2021 - Apr 2026). For each window, three
collision modes are simulated:

  Conservative : same-bar TP+SL touch -> SL wins        (lower bound)
  Realistic    : same-bar TP+SL touch -> 50/50 coin flip (mid estimate)
  Optimistic   : same-bar TP+SL touch -> TP wins        (upper bound)

Strategy (fixed):
  DTE       : 30 DTE (hold up to 15 trading bars)
  Calls TP  : +30% base / +35% when breadth_score <= 50  (h30->35 breadth-adaptive)
  Calls SL  : -35% base / -40% when breadth_score <= 50  (h35->40 breadth-adaptive)
  Puts TP   : +30% (fixed)                                (asym weekly + tight SL, 2026-04-17)
  Puts SL   : -20% (fixed)                                (tight SL enables positive EV cross-regime)
  Hard sell : -50% at day 15
  Slippage  : entry -1%, TP 0% (limit sell), SL -1.3%, hard -0.5% (per-exit)
  Call alloc: cascade  85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)
  Put  alloc: cascade  <=15=15%  16-20=12%  21-25=12%
  Max pos   : 14 concurrent positions (shared pool; calls fill first each day)
  Thresholds: calls 75+ primary, 70-74 overflow; puts <=25
  Same-sym  : one open position per symbol across sides (re-entry blocked)
  Start     : $50,000 per window, 500 MC iterations per (window x mode)

Usage: python monte_carlo.py
"""

import sys
import io
import math
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import bisect

from database.models.core import Score, AlgorithmVersion, MarketBreadth
from database.models.technical import PriceHistory

# ---- Strategy constants (locked) --------------------------------------------
STARTING_CASH      = 50_000.0
N_ITER             = 500
VOL_LOOKBACK       = 60
HOLD_DAYS          = 15          # trading bars
PREMIUM_MULT       = 1.82        # ATM 30-DTE premium ~ 1.82 * sigma_daily
DELTA              = 0.5

# Breadth-adaptive exits — same signal (breadth_score <= BREADTH_THRESHOLD)
# switches BOTH TP and SL into the "stressed" band.
TP_BASE            =  0.30       # TP in healthy breadth
TP_STRESS          =  0.35       # TP when breadth_score <= threshold
SL_BASE            = -0.35
SL_STRESS          = -0.40
HARD_SELL_LOSS     = -0.50
BREADTH_THRESHOLD  = 50          # breadth_score <= 50 -> stressed

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000   # limit sell at TP — no transaction costs
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_TP_BASE   = TP_BASE   + SLIP_ENTRY + SLIP_TP   # +0.290
NET_TP_STRESS = TP_STRESS + SLIP_ENTRY + SLIP_TP   # +0.340
NET_SL_BASE   = SL_BASE   + SLIP_ENTRY + SLIP_SL   # -0.373
NET_SL_STRESS = SL_STRESS + SLIP_ENTRY + SLIP_SL   # -0.423
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD  # -0.515

TP_SIGMA_BASE   = TP_BASE   * PREMIUM_MULT / DELTA       # 1.092
TP_SIGMA_STRESS = TP_STRESS * PREMIUM_MULT / DELTA       # 1.274
SL_SIGMA_BASE   = abs(SL_BASE)   * PREMIUM_MULT / DELTA  # 1.274
SL_SIGMA_STRESS = abs(SL_STRESS) * PREMIUM_MULT / DELTA  # 1.456

# Put-side fixed parameters (no breadth switch); validated 2026-04-17.
PUT_TP            =  0.30
PUT_SL            = -0.20
PUT_NET_TP        = PUT_TP + SLIP_ENTRY + SLIP_TP       # +0.290
PUT_NET_SL        = PUT_SL + SLIP_ENTRY + SLIP_SL       # -0.223
PUT_TP_SIGMA      = PUT_TP      * PREMIUM_MULT / DELTA  # 1.092
PUT_SL_SIGMA      = abs(PUT_SL) * PREMIUM_MULT / DELTA  # 0.728

TIER_ALLOC = {
    'top':      0.15,   # 85+  (merged — 85-89 EV exceeds 90-94, so treated equally)
    'mid':      0.12,   # 80-84
    'low':      0.12,   # 75-79
    'overflow': 0.05,   # 70-74 (only after all 75+ slots filled)
}
PUT_TIER_ALLOC = {
    'put_top': 0.15,    # <=15  (extreme put)
    'put_mid': 0.12,    # 16-20
    'put_low': 0.12,    # 21-25
}
MAX_POSITIONS      = 14
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
PUT_THRESHOLD      = 25
COLLAPSE_THRESHOLD = 0.20

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']


def score_to_tier(score):
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


def breadth_on_or_before(sorted_dates, bmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    return bmap[sorted_dates[idx]] if idx >= 0 else None


def is_stressed(sorted_dates, bmap, d):
    b = breadth_on_or_before(sorted_dates, bmap, d)
    return b is not None and b <= BREADTH_THRESHOLD


def load_signals(version, d_start, d_end):
    """Call signals: overall >= OVERFLOW_THRESHOLD (70)."""
    return list(
        Score.select(Score.symbol, Score.date, Score.overall)
        .where(
            Score.version == version,
            Score.date >= d_start,
            Score.date <= d_end,
            Score.overall >= OVERFLOW_THRESHOLD,
        )
        .order_by(Score.date, Score.overall.desc())
    )


def load_put_signals(version, d_start, d_end):
    """Put signals: overall <= PUT_THRESHOLD (25)."""
    return list(
        Score.select(Score.symbol, Score.date, Score.overall)
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

def compute_trade_outcome(sym_bars, signal_date, stressed):
    """
    Returns dict with keys:
      kind     : 'tp' | 'sl' | 'hard' | 'both'
      exit_bar : int (trading bars from signal_date)
      net_tp, net_sl : per-trade net P&L (breadth-adaptive at entry)
      premium_pct, vol, entry
    """
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

    premium_pct = PREMIUM_MULT * vol / 100
    tp_level    = entry_price * (1 + tp_sigma * vol / 100)
    sl_level    = entry_price * (1 - sl_sigma * vol / 100)

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
            kind, exit_bar = 'tp', i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl', i - base_idx; break

    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl,
                premium_pct=premium_pct, vol=vol, entry=entry_price)


def compute_put_outcome(sym_bars, signal_date):
    """Put trade: win = underlying falls PUT_TP_SIGMA sigmas; stop = rises PUT_SL_SIGMA."""
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

    tp_level = entry_price * (1 - PUT_TP_SIGMA * vol / 100)
    sl_level = entry_price * (1 + PUT_SL_SIGMA * vol / 100)

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
            kind, exit_bar = 'tp', i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl', i - base_idx; break

    return dict(kind=kind, exit_bar=exit_bar, net_tp=PUT_NET_TP, net_sl=PUT_NET_SL,
                vol=vol, entry=entry_price)


def precompute_outcomes(signals, ph, breadth_dates, breadth_map):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        stressed = is_stressed(breadth_dates, breadth_map, sig.date)
        r = compute_trade_outcome(sym_bars, sig.date, stressed)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def precompute_put_outcomes(signals, ph):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        r = compute_put_outcome(sym_bars, sig.date)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def resolve(kind, mode, rng, net_tp, net_sl):
    """Return ('tp' | 'sl' | 'hard', option_pnl) given collision kind + mode."""
    if kind == 'tp':   return 'tp',   net_tp
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    # kind == 'both'
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', net_tp
    # realistic -> coin flip
    return ('tp', net_tp) if rng.random() < 0.5 else ('sl', net_sl)


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
                   puts_by_date, put_outcomes, mode, rng):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c  = sl_c  = hard_c  = 0
    tp_p  = sl_p  = hard_p  = 0

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

        # Calls first (primary + overflow)
        day_calls = calls_by_date.get(today, [])
        if day_calls:
            eligible = [(sid, sc, k) for sid, sc, k in day_calls
                        if k in call_outcomes and sid not in open_syms]
            primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD]
            overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD]
            primary.sort(key=lambda x: (-x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            for sym_id, score, key in primary + overflow:
                if len(positions) >= MAX_POSITIONS:
                    break
                alloc_frac   = TIER_ALLOC[score_to_tier(score)]
                premium_cost = portfolio_value * alloc_frac
                if premium_cost > cash or premium_cost <= 0:
                    continue
                o = call_outcomes[key]
                outcome, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= premium_cost
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'call'))
                open_syms.add(sym_id)

        # Puts (any remaining slots)
        if len(positions) < MAX_POSITIONS:
            day_puts = puts_by_date.get(today, [])
            if day_puts:
                pe = [(sid, sc, k) for sid, sc, k in day_puts
                      if k in put_outcomes and sid not in open_syms]
                pe.sort(key=lambda x: (x[1], rng.random()))  # lowest score first (strongest put)
                for sym_id, score, key in pe:
                    if len(positions) >= MAX_POSITIONS:
                        break
                    alloc_frac   = PUT_TIER_ALLOC[put_score_to_tier(score)]
                    premium_cost = portfolio_value * alloc_frac
                    if premium_cost > cash or premium_cost <= 0:
                        continue
                    o = put_outcomes[key]
                    outcome, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                    cash -= premium_cost
                    positions.append(Position(sym_id, today, o['exit_bar'],
                                               premium_cost, pnl, outcome, 'put'))
                    open_syms.add(sym_id)

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

    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph      = load_price_history(sym_ids, d_start, d_end)

    breadth_dates, breadth_map = load_breadth_map(d_start, d_end)
    if call_sigs and breadth_dates:
        n_str = sum(1 for s in call_sigs if is_stressed(breadth_dates, breadth_map, s.date))
        print(f"Breadth map: {len(breadth_map)} dates  |  stressed call signals: {n_str/len(call_sigs)*100:.1f}%")

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    calls_by_date = defaultdict(list)
    for sig in call_sigs:
        key = (sig.symbol_id, sig.date)
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, key))
    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        key = (sig.symbol_id, sig.date)
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, key))

    print("Precomputing call outcomes...", end=' ', flush=True)
    call_outcomes = precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    both_n = sum(1 for o in call_outcomes.values() if o['kind'] == 'both')
    tp_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'tp')
    sl_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'sl')
    hard_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'hard')
    ct = len(call_outcomes) or 1
    print(f"N={len(call_outcomes)}  TP={tp_only/ct*100:.1f}%  SL={sl_only/ct*100:.1f}%  "
          f"Both={both_n/ct*100:.1f}%  Hard={hard_only/ct*100:.1f}%")

    print("Precomputing put outcomes... ", end=' ', flush=True)
    put_outcomes = precompute_put_outcomes(put_sigs, ph)
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

    results = {}
    for mode in COLLISION_MODES:
        finals=[]; dds=[]; ctps=[]; ptps=[]; ctrd=[]; ptrd=[]; collapses=0
        for it in range(N_ITER):
            rng = random.Random(1000 * hash(label) + it)
            r = run_single_sim(trading_days, calls_by_date, call_outcomes,
                               puts_by_date, put_outcomes, mode, rng)
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

    return results


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
    print(f"C Alloc  : 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)")
    print(f"P Alloc  : <=15=15%  16-20=12%  21-25=12%  (shared 14-slot pool with calls)")
    print(f"MaxPos   : {MAX_POSITIONS}  (upgraded from 10; MaxPos sweep 2026-04-16, monte_carlo_maxpos_sweep.py)")
    print(f"         : Primary threshold: {PRIMARY_THRESHOLD}+  |  Overflow: {OVERFLOW_THRESHOLD}-74")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"Sigma    : TP base={TP_SIGMA_BASE:.3f}/stressed={TP_SIGMA_STRESS:.3f}  "
          f"SL base={SL_SIGMA_BASE:.3f}/stressed={SL_SIGMA_STRESS:.3f}")
    print(f"Modes    : {', '.join(COLLISION_MODES)}")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}")

    all_results = {}
    for label, d_start, d_end in WINDOWS:
        all_results[label] = run_window(label, d_start, d_end, version)

    # Final summary table
    print('\n' + '='*110)
    print("SUMMARY - Mean Return by Window x Collision Mode")
    print('='*110)
    print(f"{'Window':<8}  " + '  '.join(f"{m+' MeanRet':>22}" for m in COLLISION_MODES))
    print('-'*110)
    for label, _, _ in WINDOWS:
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
    for label, _, _ in WINDOWS:
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
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for mode in COLLISION_MODES:
            r = all_results[label][mode]
            row += f"{r['p_coll']:>21.1f}%     "
        print(row)


if __name__ == '__main__':
    main()
