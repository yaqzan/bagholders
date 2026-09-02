"""
Allocation Sweep — 85+ Combined Tier
======================================
Tests merging 85-89 and 90+ into a single '85+' tier and sweeping
the per-tier allocation percentages. Also sweeps 80-84 and 75-79.

Motivation: v17 5y assessment shows 85-89 discrete band has higher EV
(WR15=77.3%, EV=0.158) than 90-94 (WR15=67.8%, EV=0.105). Combining
them into one tier removes the mis-weighting.

Scheme dimensions swept:
  top_alloc  (85+)  : 10%, 12%, 14%, 15%, 18%, 20%
  lower_alloc (80-84 = 75-79, enforced equal): 8%, 10%, 12%
  overflow (70-74): fixed at 5%

Runs realistic mode only, 300 iterations per combo.
Windows: 2022 (bear), 2024 (bull), 2025 (choppy), 5y.

Usage: python monte_carlo_alloc_sweep_85plus.py
"""

import sys
import io
import math
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.models.core import Score, AlgorithmVersion
from database.models.technical import PriceHistory

# ---- Fixed strategy constants (same as monte_carlo.py) ----------------------
STARTING_CASH      = 50_000.0
N_ITER             = 300
VOL_LOOKBACK       = 60
HOLD_DAYS          = 15
PREMIUM_MULT       = 1.82
DELTA              = 0.5

TP_OPTION_GAIN     =  0.30
SL_OPTION_LOSS     = -0.25
HARD_SELL_LOSS     = -0.50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000   # limit sell at TP — no transaction costs
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_TP        = TP_OPTION_GAIN + SLIP_ENTRY + SLIP_TP
NET_SL        = SL_OPTION_LOSS + SLIP_ENTRY + SLIP_SL
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD

TP_SIGMA = TP_OPTION_GAIN * PREMIUM_MULT / DELTA
SL_SIGMA = 0.25          * PREMIUM_MULT / DELTA

MAX_POSITIONS      = 10
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
COLLAPSE_THRESHOLD = 0.20

WINDOWS = [
    ('2022', date(2022, 1, 1),  date(2022, 12, 31)),
    ('2024', date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025', date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',   date(2021, 1, 1),  date(2026, 4, 15)),
]

# ---- Sweep grid --------------------------------------------------------------
TOP_ALLOCS   = [0.10, 0.12, 0.14, 0.15, 0.18, 0.20]   # 85+ tier
LOWER_ALLOCS = [0.08, 0.10, 0.12]                       # 80-84 = 75-79
OVERFLOW_ALLOC = 0.05

# Baseline for comparison: current split scheme
BASELINE = {
    'label':    'baseline (90+=15, 85-89=12, lower=10)',
    'top':      0.15,   # 90+
    'high':     0.12,   # 85-89
    'mid':      0.10,   # 80-84
    'low':      0.10,   # 75-79
    'overflow': 0.05,
    'merged':   False,
}


# ---- Score → tier mapping ---------------------------------------------------

def score_to_tier_merged(score):
    """Merged 85+ scheme: all >=85 get the same allocation."""
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'

def score_to_tier_split(score):
    """Original split: 90+ vs 85-89 separate."""
    if score >= 90: return 'top'
    if score >= 85: return 'high'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


# ---- Data loading (identical to monte_carlo.py) ----------------------------

def load_signals(version, d_start, d_end):
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


def compute_trade_outcome(sym_bars, signal_date):
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
    premium_pct = PREMIUM_MULT * vol / 100
    tp_level    = entry_price * (1 + TP_SIGMA * vol / 100)
    sl_level    = entry_price * (1 - SL_SIGMA * vol / 100)
    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None
    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_level
        sl_hit = lows[i]  <= sl_level
        if tp_hit and sl_hit:
            return dict(kind='both', exit_bar=i - base_idx,
                        premium_pct=premium_pct, vol=vol, entry=entry_price)
        if tp_hit:
            return dict(kind='tp', exit_bar=i - base_idx,
                        premium_pct=premium_pct, vol=vol, entry=entry_price)
        if sl_hit:
            return dict(kind='sl', exit_bar=i - base_idx,
                        premium_pct=premium_pct, vol=vol, entry=entry_price)
    return dict(kind='hard', exit_bar=HOLD_DAYS,
                premium_pct=premium_pct, vol=vol, entry=entry_price)


def precompute_outcomes(signals, ph):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        r = compute_trade_outcome(sym_bars, sig.date)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def resolve_realistic(kind, rng):
    if kind == 'tp':   return 'tp',   NET_TP
    if kind == 'sl':   return 'sl',   NET_SL
    if kind == 'hard': return 'hard', NET_HARD_SELL
    return ('tp', NET_TP) if rng.random() < 0.5 else ('sl', NET_SL)


# ---- Portfolio simulation ---------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl', 'outcome']
    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome


def run_single_sim(trading_days, signals_by_date, outcomes, tier_alloc,
                   tier_fn, rng):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}
    tp_c = sl_c = hard_c = 0

    for day_idx, today in enumerate(trading_days):
        # Age / close expired positions
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.premium_cost * (1 + p.option_pnl)
                if p.outcome == 'tp':   tp_c   += 1
                elif p.outcome == 'sl': sl_c   += 1
                else:                   hard_c += 1
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

        day_signals = signals_by_date.get(today, [])
        if not day_signals:
            continue

        open_syms = {p.sym_id for p in positions}
        eligible  = [(sym_id, score, key)
                     for sym_id, score, key in day_signals
                     if key in outcomes and sym_id not in open_syms]
        if not eligible:
            continue

        primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD]
        overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD]
        primary.sort(key=lambda x: (-x[1], rng.random()))
        overflow.sort(key=lambda x: (-x[1], rng.random()))

        for sym_id, score, key in primary + overflow:
            if len(positions) >= MAX_POSITIONS:
                break
            tier         = tier_fn(score)
            alloc_frac   = tier_alloc[tier]
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                continue
            kind = outcomes[key]['kind']
            outcome, pnl = resolve_realistic(kind, rng)
            cash -= premium_cost
            positions.append(Position(sym_id, today, outcomes[key]['exit_bar'],
                                       premium_cost, pnl, outcome))

    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        hard_c += 1
    portfolio_value = cash
    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    total    = tp_c + sl_c + hard_c or 1
    return dict(
        final     = portfolio_value,
        max_dd    = max_dd,
        tp_rate   = tp_c / total * 100,
        sl_rate   = sl_c / total * 100,
        hard_rate = hard_c / total * 100,
        trades    = total,
    )


def run_scheme(label, tier_alloc, tier_fn, trading_days, signals_by_date, outcomes):
    results = []
    rng = random.Random(42)
    for _ in range(N_ITER):
        r = run_single_sim(trading_days, signals_by_date, outcomes,
                           tier_alloc, tier_fn, rng)
        results.append(r)
    finals   = [r['final'] for r in results]
    dds      = [r['max_dd'] for r in results]
    mean_ret = (statistics.mean(finals) - STARTING_CASH) / STARTING_CASH * 100
    med_ret  = (statistics.median(finals) - STARTING_CASH) / STARTING_CASH * 100
    worst_dd = max(dds) * 100
    mean_dd  = statistics.mean(dds) * 100
    tp_rate  = statistics.mean(r['tp_rate'] for r in results)
    collapse = sum(1 for f in finals if f <= STARTING_CASH * COLLAPSE_THRESHOLD)
    return dict(label=label, mean_ret=mean_ret, med_ret=med_ret,
                worst_dd=worst_dd, mean_dd=mean_dd, tp_rate=tp_rate,
                collapse=collapse)


# ---- Main -------------------------------------------------------------------

def main():
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit} (v{version.id})")
    print(f"Mode: realistic only | N_ITER={N_ITER} | MaxPos={MAX_POSITIONS}")
    print(f"TP={TP_OPTION_GAIN*100:.0f}% SL={abs(SL_OPTION_LOSS)*100:.0f}% Hard={abs(HARD_SELL_LOSS)*100:.0f}%@day15")
    print()

    for win_label, d_start, d_end in WINDOWS:
        print(f"\n{'='*110}")
        print(f"WINDOW: {win_label}  ({d_start} -> {d_end})")
        print('='*110)

        signals  = load_signals(version, d_start, d_end)
        sym_ids  = list({s.symbol_id for s in signals})
        ph       = load_price_history(sym_ids, d_start, d_end)
        outcomes = precompute_outcomes(signals, ph)

        ph_dates = set()
        for bars in ph.values():
            for b in bars:
                if d_start <= b[0] <= d_end + timedelta(days=20):
                    ph_dates.add(b[0])
        trading_days = sorted(ph_dates)

        signals_by_date = defaultdict(list)
        for sig in signals:
            key = (sig.symbol_id, sig.date)
            signals_by_date[sig.date].append((sig.symbol_id, sig.overall, key))

        valid = sum(1 for k in outcomes)
        print(f"Signals: {len(signals)} | Valid outcomes: {valid} | Trading days: {len(trading_days)}")

        # Count signal density by tier
        n85p  = sum(1 for s in signals if s.overall >= 85)
        n8084 = sum(1 for s in signals if 80 <= s.overall < 85)
        n7579 = sum(1 for s in signals if 75 <= s.overall < 80)
        n7074 = sum(1 for s in signals if 70 <= s.overall < 75)
        print(f"  85+={n85p}  80-84={n8084}  75-79={n7579}  70-74={n7074}")
        print()

        all_results = []

        # 1) Baseline (current split scheme)
        bsl = BASELINE
        bsl_alloc = {'top': bsl['top'], 'high': bsl['high'],
                     'mid': bsl['mid'], 'low': bsl['low'], 'overflow': bsl['overflow']}
        r = run_scheme(bsl['label'], bsl_alloc, score_to_tier_split,
                       trading_days, signals_by_date, outcomes)
        all_results.append(r)

        # 2) Merged 85+ sweep
        for top in TOP_ALLOCS:
            for lower in LOWER_ALLOCS:
                lbl = f"merged  85+={int(top*100):2d}%  80-84={int(lower*100):2d}%  75-79={int(lower*100):2d}%"
                alloc = {'top': top, 'mid': lower, 'low': lower, 'overflow': OVERFLOW_ALLOC}
                r = run_scheme(lbl, alloc, score_to_tier_merged,
                               trading_days, signals_by_date, outcomes)
                all_results.append(r)

        # Sort by mean return descending
        all_results.sort(key=lambda x: -x['mean_ret'])

        # Print
        hdr = f"{'Scheme':<48} {'MeanRet':>14} {'MedRet':>14} {'TP%':>7} {'WorstDD':>9} {'MeanDD':>9} {'P(col)':>8}"
        print(hdr)
        print('-' * 115)
        for i, r in enumerate(all_results):
            marker = ' *' if r['label'].startswith('baseline') else ''
            print(f"{r['label']:<48}{marker} {r['mean_ret']:>13,.1f}%"
                  f" {r['med_ret']:>13,.1f}%"
                  f" {r['tp_rate']:>6.1f}%"
                  f" {r['worst_dd']:>8.1f}%"
                  f" {r['mean_dd']:>8.1f}%"
                  f" {r['collapse']:>7}  ")

    print("\nDone.")


if __name__ == '__main__':
    main()
EOF
