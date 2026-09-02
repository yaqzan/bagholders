"""
Monte Carlo — Max Position Sweep
=================================
Tests MAX_POSITIONS ∈ {8, 10, 12, 15, 20} across per-year windows (2021-2025)
and the continuous 5-year window. All other strategy parameters are locked at
the canonical values from monte_carlo.py.

Question: does raising max concurrent positions from 10 to 15 (or higher)
improve returns without breaching drawdown safety thresholds?

Usage: python monte_carlo_maxpos_sweep.py
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

# ---- Strategy constants (locked — same as canonical monte_carlo.py) ----------
STARTING_CASH      = 50_000.0
N_ITER             = 500
VOL_LOOKBACK       = 60
HOLD_DAYS          = 15
PREMIUM_MULT       = 1.82
DELTA              = 0.5

TP_OPTION_GAIN     =  0.30
SL_OPTION_LOSS     = -0.35
HARD_SELL_LOSS     = -0.50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_TP        = TP_OPTION_GAIN + SLIP_ENTRY + SLIP_TP
NET_SL        = SL_OPTION_LOSS + SLIP_ENTRY + SLIP_SL
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD

TP_SIGMA = TP_OPTION_GAIN * PREMIUM_MULT / DELTA
SL_SIGMA = abs(SL_OPTION_LOSS) * PREMIUM_MULT / DELTA

TIER_ALLOC = {
    'top':      0.15,   # 85+
    'mid':      0.12,   # 80-84
    'low':      0.12,   # 75-79
    'overflow': 0.05,   # 70-74
}
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
COLLAPSE_THRESHOLD = 0.20

# ---- Sweep parameter ---------------------------------------------------------
MAX_POS_VALUES = [8, 10, 12, 13, 14, 15, 20]

WINDOWS = [
    ('2021',       date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',       date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',       date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',       date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',       date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',         date(2021, 1, 1),  date(2026, 4, 15)),
    ('22-now',     date(2022, 1, 1),  date(2026, 4, 15)),  # Bear-start stress: worst-case entry
]

# Only run Realistic mode for the sweep (the decision-relevant mode)
# Conservative included for 2022 specifically since it's the floor constraint
COLLISION_MODES = ['conservative', 'realistic', 'optimistic']


def score_to_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


# ---- Data loading (identical to monte_carlo.py) ------------------------------

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


def resolve(kind, mode, rng):
    if kind == 'tp':   return 'tp',   NET_TP
    if kind == 'sl':   return 'sl',   NET_SL
    if kind == 'hard': return 'hard', NET_HARD_SELL
    if mode == 'conservative': return 'sl', NET_SL
    if mode == 'optimistic':   return 'tp', NET_TP
    return ('tp', NET_TP) if rng.random() < 0.5 else ('sl', NET_SL)


# ---- Portfolio simulation (parameterized max_pos) ----------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl', 'outcome']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome


def run_single_sim(trading_days, signals_by_date, outcomes, mode, rng, max_positions):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c = sl_c = hard_c = 0

    for day_idx, today in enumerate(trading_days):
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
        eligible = [(sym_id, score, key)
                    for sym_id, score, key in day_signals
                    if key in outcomes and sym_id not in open_syms]

        if not eligible:
            continue

        primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD]
        overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD]
        primary.sort(key=lambda x: (-x[1], rng.random()))
        overflow.sort(key=lambda x: (-x[1], rng.random()))

        for sym_id, score, key in primary + overflow:
            if len(positions) >= max_positions:
                break
            tier         = score_to_tier(score)
            alloc_frac   = TIER_ALLOC[tier]
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                continue
            kind = outcomes[key]['kind']
            outcome, pnl = resolve(kind, mode, rng)
            cash -= premium_cost
            positions.append(Position(sym_id, today, outcomes[key]['exit_bar'],
                                       premium_cost, pnl, outcome))

    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        hard_c += 1
    portfolio_value = cash

    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    total_trades = tp_c + sl_c + hard_c or 1
    return dict(
        final = portfolio_value,
        max_dd = max_dd,
        tp_rate   = tp_c / total_trades * 100,
        sl_rate   = sl_c / total_trades * 100,
        hard_rate = hard_c / total_trades * 100,
        trades    = total_trades,
    )


# ---- Window runner -----------------------------------------------------------

def run_window(label, d_start, d_end, version):
    """Load data once per window, then sweep max_pos × modes."""

    signals = load_signals(version, d_start, d_end)
    primary_n  = sum(1 for s in signals if s.overall >= PRIMARY_THRESHOLD)
    overflow_n = len(signals) - primary_n
    print(f"\n{'='*120}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})  |  Signals: {len(signals)} (75+={primary_n}, 70-74={overflow_n})")
    print('='*120)

    sym_ids = list({s.symbol_id for s in signals})
    ph      = load_price_history(sym_ids, d_start, d_end)

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

    print("Precomputing per-trade outcomes...", end=' ', flush=True)
    outcomes = precompute_outcomes(signals, ph)
    both_n = sum(1 for o in outcomes.values() if o['kind'] == 'both')
    print(f"done. trades={len(outcomes)}, collisions={both_n} ({both_n/max(len(outcomes),1)*100:.1f}%)")

    # Results: {max_pos: {mode: stats_dict}}
    window_results = {}

    for max_pos in MAX_POS_VALUES:
        window_results[max_pos] = {}
        for mode in COLLISION_MODES:
            finals = []; dds = []; tps = []; sls = []; hards = []; trades_list = []
            collapses = 0
            for it in range(N_ITER):
                rng = random.Random(1000 * hash(label) + it)
                r = run_single_sim(trading_days, signals_by_date, outcomes,
                                   mode, rng, max_pos)
                finals.append(r['final'])
                dds.append(r['max_dd'])
                tps.append(r['tp_rate'])
                sls.append(r['sl_rate'])
                hards.append(r['hard_rate'])
                trades_list.append(r['trades'])
                if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                    collapses += 1

            mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
            med_ret  = (statistics.median(finals) / STARTING_CASH - 1) * 100
            mean_dd  = statistics.mean(dds) * 100
            worst_dd = max(dds) * 100
            p_coll   = collapses / N_ITER * 100

            window_results[max_pos][mode] = dict(
                mean_ret=mean_ret, med_ret=med_ret,
                mean_dd=mean_dd, worst_dd=worst_dd,
                p_coll=p_coll,
                tp=statistics.mean(tps), sl=statistics.mean(sls), hard=statistics.mean(hards),
                mean_final=statistics.mean(finals),
                mean_trades=statistics.mean(trades_list),
            )

    # Print per-window comparison table (Realistic mode — the decision target)
    print(f"\n  {'MaxPos':>6}  {'TP%':>6}  {'SL%':>6}  {'Trades':>7}  "
          f"{'MeanRet':>16}  {'MedRet':>16}  {'WorstDD':>8}  {'MeanDD':>7}  {'P(col)':>7}")
    print('  ' + '-'*110)
    for mode in COLLISION_MODES:
        print(f"  {mode}:")
        for max_pos in MAX_POS_VALUES:
            r = window_results[max_pos][mode]
            marker = "  <-- current" if max_pos == 10 else ""
            print(f"  {max_pos:>6}  {r['tp']:>5.1f}%  {r['sl']:>5.1f}%  "
                  f"{r['mean_trades']:>7.0f}  {r['mean_ret']:>+15,.1f}%  "
                  f"{r['med_ret']:>+15,.1f}%  {r['worst_dd']:>7.1f}%  "
                  f"{r['mean_dd']:>6.1f}%  {r['p_coll']:>6.1f}%{marker}")
        print()

    return window_results


# ---- Main --------------------------------------------------------------------

def main():
    print('='*120)
    print("MONTE CARLO — MAX POSITIONS SWEEP")
    print('='*120)
    print(f"Strategy : 30 DTE | TP=+30% | SL=-35% | Hard=-50%@day15")
    print(f"Slippage : entry -1.0% | TP 0% | SL -1.3% | Hard -0.5%")
    print(f"           NET_TP={NET_TP:+.3f}  NET_SL={NET_SL:+.3f}  NET_HARD={NET_HARD_SELL:+.3f}")
    print(f"Alloc    : 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)")
    print(f"Sweep    : MAX_POSITIONS = {MAX_POS_VALUES}")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"TP sigma : {TP_SIGMA:.3f}  |  SL sigma: {SL_SIGMA:.3f}")
    print(f"Modes    : {', '.join(COLLISION_MODES)}")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}")

    all_results = {}
    for label, d_start, d_end in WINDOWS:
        all_results[label] = run_window(label, d_start, d_end, version)

    # ---- Grand summary tables ------------------------------------------------
    print('\n' + '='*120)
    print("GRAND SUMMARY — Realistic Mode (decision target)")
    print('='*120)

    # Mean Return
    header = f"{'Window':<8}  " + '  '.join(f"{'MaxPos='+str(mp):>18}" for mp in MAX_POS_VALUES)
    print(f"\n  MEAN RETURN")
    print(f"  {header}")
    print('  ' + '-'*108)
    for label, _, _ in WINDOWS:
        row = f"  {label:<8}  "
        for mp in MAX_POS_VALUES:
            r = all_results[label][mp]['realistic']
            row += f"{r['mean_ret']:>+17,.1f}%  "
        print(row)

    # Worst DD
    print(f"\n  WORST DRAWDOWN")
    print(f"  {header}")
    print('  ' + '-'*108)
    for label, _, _ in WINDOWS:
        row = f"  {label:<8}  "
        for mp in MAX_POS_VALUES:
            r = all_results[label][mp]['realistic']
            row += f"{r['worst_dd']:>17.1f}%  "
        print(row)

    # Mean DD
    print(f"\n  MEAN DRAWDOWN")
    print(f"  {header}")
    print('  ' + '-'*108)
    for label, _, _ in WINDOWS:
        row = f"  {label:<8}  "
        for mp in MAX_POS_VALUES:
            r = all_results[label][mp]['realistic']
            row += f"{r['mean_dd']:>17.1f}%  "
        print(row)

    # P(collapse)
    print(f"\n  P(COLLAPSE)")
    print(f"  {header}")
    print('  ' + '-'*108)
    for label, _, _ in WINDOWS:
        row = f"  {label:<8}  "
        for mp in MAX_POS_VALUES:
            r = all_results[label][mp]['realistic']
            row += f"{r['p_coll']:>17.1f}%  "
        print(row)

    # Mean Trades
    print(f"\n  MEAN TRADES PER WINDOW")
    print(f"  {header}")
    print('  ' + '-'*108)
    for label, _, _ in WINDOWS:
        row = f"  {label:<8}  "
        for mp in MAX_POS_VALUES:
            r = all_results[label][mp]['realistic']
            row += f"{r['mean_trades']:>17,.0f}  "
        print(row)

    # ---- Conservative mode summary (2022 floor constraint) -------------------
    print('\n' + '='*120)
    print("CONSERVATIVE MODE — 2022 Bear Floor Constraint")
    print('='*120)
    print(f"  {'MaxPos':>8}  {'MeanRet':>16}  {'WorstDD':>9}  {'MeanDD':>8}  {'P(col)':>8}  {'Trades':>8}  {'Safe?':>6}")
    print('  ' + '-'*80)
    for mp in MAX_POS_VALUES:
        r = all_results['2022'][mp]['conservative']
        safe = "YES" if r['worst_dd'] < 80 and r['p_coll'] == 0 else "NO"
        marker = "  <-- current" if mp == 10 else ""
        print(f"  {mp:>8}  {r['mean_ret']:>+15,.1f}%  {r['worst_dd']:>8.1f}%  "
              f"{r['mean_dd']:>7.1f}%  {r['p_coll']:>7.1f}%  {r['mean_trades']:>7,.0f}  "
              f"{safe:>6}{marker}")

    # ---- Bear-start stress test: 2022 to now (all 3 modes) ------------------
    print('\n' + '='*120)
    print("BEAR-START STRESS TEST — Jan 2022 → Apr 2026 (worst-case entry point)")
    print("  Starts in bear, must survive 2022 drawdown then compound through recovery/bull")
    print('='*120)
    print(f"  {'MaxPos':>8}  {'MeanRet (Cons)':>18}  {'MeanRet (Real)':>18}  {'MeanRet (Opt)':>17}  "
          f"{'WorstDD (Cons)':>15}  {'WorstDD (Real)':>15}  {'P(col)':>7}")
    print('  ' + '-'*115)
    for mp in MAX_POS_VALUES:
        rc = all_results['22-now'][mp]['conservative']
        rr = all_results['22-now'][mp]['realistic']
        ro = all_results['22-now'][mp]['optimistic']
        safe = "YES" if rc['worst_dd'] < 80 and rc['p_coll'] == 0 else "NO"
        marker = "  <-- current" if mp == 10 else ""
        print(f"  {mp:>8}  {rc['mean_ret']:>+17,.1f}%  {rr['mean_ret']:>+17,.1f}%  "
              f"{ro['mean_ret']:>+16,.1f}%  {rc['worst_dd']:>14.1f}%  "
              f"{rr['worst_dd']:>14.1f}%  {rr['p_coll']:>6.1f}%  {safe}{marker}")

    # ---- Delta vs MaxPos=10 (Realistic) --------------------------------------
    print('\n' + '='*120)
    print("DELTA vs MaxPos=10 — Realistic Mode")
    print('='*120)
    print(f"  {'Window':<8}  " + '  '.join(f"{'Δ@'+str(mp):>18}" for mp in MAX_POS_VALUES if mp != 10))
    print('  ' + '-'*90)
    for label, _, _ in WINDOWS:
        base_ret = all_results[label][10]['realistic']['mean_ret']
        row = f"  {label:<8}  "
        for mp in MAX_POS_VALUES:
            if mp == 10:
                continue
            r = all_results[label][mp]['realistic']
            delta = r['mean_ret'] - base_ret
            row += f"{delta:>+17,.1f}%  "
        print(row)


if __name__ == '__main__':
    main()
