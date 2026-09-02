"""
Monte Carlo — Stop Loss Sweep (3-Mode Canonical)
================================================
Sweeps SL_OPTION_LOSS from -15% to -60% under the 3-mode canonical framework.
Uses realistic per-exit slippage. Optimization target: Realistic mode mean return.
Conservative mode floor constraint: worst_DD < 80%.

All other parameters locked to the canonical optimal:
  DTE       : 30 DTE (15 trading bars)
  TP        : +30% on premium
  Hard sell : -50% at day 15
  Slippage  : entry -1%, TP 0% (limit sell), SL -1.3%, hard -0.5%
  Alloc     : 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)
  MaxPos    : 10 concurrent positions
  Start     : $50,000 per window, 200 MC iterations

SL values swept: 15%, 20%, 25%, 30%, 35%, 40%, 50%, 60%

Key diagnostic: Bar-1 SL% — if high (>50%), the SL is inside daily noise (artifact).
Genuine adverse-move SL exits cluster at bars 2-7.

Usage: python monte_carlo_sl_sweep.py
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

# ── Fixed strategy constants ──────────────────────────────────────────────────
STARTING_CASH     = 50_000.0
N_ITER            = 200
VOL_LOOKBACK      = 60
HOLD_DAYS         = 15          # trading bars (30 DTE)
PREMIUM_MULT      = 1.82        # ATM 30-DTE premium ≈ 1.82 × σ_daily
DELTA             = 0.5

TP_OPTION_GAIN    =  0.30
HARD_SELL_LOSS    = -0.50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000   # limit sell at TP — no transaction costs
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_TP        = TP_OPTION_GAIN + SLIP_ENTRY + SLIP_TP    # +0.290
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD  # -0.515
TP_SIGMA      = TP_OPTION_GAIN * PREMIUM_MULT / DELTA    # 1.092σ

TIER_ALLOC = {
    'top':      0.15,   # 85+  (merged tier)
    'mid':      0.12,   # 80-84
    'low':      0.12,   # 75-79
    'overflow': 0.05,   # 70-74 (overflow after 75+ slots filled)
}
MAX_POSITIONS      = 10
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
COLLAPSE_THRESHOLD = 0.20

# ── Sweep configuration ───────────────────────────────────────────────────────
SL_SWEEP = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']

WINDOWS = [
    ('2021', date(2021,  1,  1), date(2021, 12, 31)),
    ('2022', date(2022,  1,  1), date(2022, 12, 31)),
    ('2023', date(2023,  1,  1), date(2023, 12, 31)),
    ('2024', date(2024,  1,  1), date(2024, 12, 31)),
    ('2025', date(2025,  1,  1), date(2025, 12, 31)),
    ('5y',   date(2021,  1,  1), date(2026,  4, 15)),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def score_to_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


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
        if closes[j - 1] > 0:
            rets.append((closes[j] - closes[j - 1]) / closes[j - 1])
    if len(rets) < lookback // 2:
        return None
    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


# ── Raw trade precompute (TP-invariant — single pass per window) ──────────────

def precompute_raw_trades(signals, ph):
    """
    For each signal, precompute:
      entry       - close price on signal date
      vol         - 60-day realized daily σ (%)
      premium_pct - ATM 30-DTE premium as fraction of stock price
      tp_level    - absolute price level that triggers TP
      fwd_bars    - list of (high, low) for each of the next HOLD_DAYS bars

    TP level is SL-independent (fixed TP_SIGMA), so this only needs to run once
    per window.  Per-SL outcomes are derived cheaply by scanning fwd_bars.
    """
    raw = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        dates  = [b[0] for b in sym_bars]
        closes = [b[1] for b in sym_bars]
        highs  = [b[2] for b in sym_bars]
        lows   = [b[3] for b in sym_bars]

        try:
            base_idx = dates.index(sig.date)
        except ValueError:
            continue

        entry = closes[base_idx]
        if entry <= 0:
            continue
        vol = realized_vol(closes, base_idx)
        if vol is None or vol <= 0:
            continue

        end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
        if end_idx <= base_idx + 1:
            continue

        raw[(sig.symbol_id, sig.date)] = dict(
            entry       = entry,
            vol         = vol,
            premium_pct = PREMIUM_MULT * vol / 100,
            tp_level    = entry * (1 + TP_SIGMA * vol / 100),
            fwd_bars    = [(highs[i], lows[i]) for i in range(base_idx + 1, end_idx)],
        )
    return raw


# ── Per-SL outcome computation ────────────────────────────────────────────────

def compute_outcomes_for_sl(raw_trades, sl_pct):
    """
    Walk each signal's forward bars to determine TP/SL/hard outcome for a
    specific SL%.  Returns:
      outcomes : {(sym_id, date): {kind, exit_bar, premium_pct, net_sl}}
      diag     : diagnostics dict (bar-1 SL%, mean SL exit bar, trade counts)
    """
    sl_sigma = sl_pct * PREMIUM_MULT / DELTA
    net_sl   = -sl_pct + SLIP_ENTRY + SLIP_SL   # e.g. -0.25-0.010-0.013 = -0.273

    outcomes = {}
    bar1_sl  = 0
    sl_bars  = []

    for key, t in raw_trades.items():
        sl_level = t['entry'] * (1 - sl_sigma * t['vol'] / 100)
        result   = None

        for i, (h, l) in enumerate(t['fwd_bars']):
            bar    = i + 1
            tp_hit = h >= t['tp_level']
            sl_hit = l <= sl_level
            if tp_hit and sl_hit:
                result = dict(kind='both', exit_bar=bar,
                              premium_pct=t['premium_pct'], net_sl=net_sl)
                break
            if tp_hit:
                result = dict(kind='tp', exit_bar=bar,
                              premium_pct=t['premium_pct'], net_sl=net_sl)
                break
            if sl_hit:
                if bar == 1:
                    bar1_sl += 1
                sl_bars.append(bar)
                result = dict(kind='sl', exit_bar=bar,
                              premium_pct=t['premium_pct'], net_sl=net_sl)
                break

        if result is None:
            result = dict(kind='hard', exit_bar=HOLD_DAYS,
                          premium_pct=t['premium_pct'], net_sl=net_sl)
        outcomes[key] = result

    tp_n   = sum(1 for o in outcomes.values() if o['kind'] == 'tp')
    both_n = sum(1 for o in outcomes.values() if o['kind'] == 'both')
    sl_n   = sum(1 for o in outcomes.values() if o['kind'] == 'sl')
    hard_n = sum(1 for o in outcomes.values() if o['kind'] == 'hard')

    diag = dict(
        sl_sigma     = sl_sigma,
        net_sl       = net_sl,
        be_rate      = abs(net_sl) / (NET_TP + abs(net_sl)) * 100,
        tp_n=tp_n, both_n=both_n, sl_n=sl_n, hard_n=hard_n,
        bar1_sl_pct  = bar1_sl / max(sl_n, 1) * 100,
        mean_sl_bar  = sum(sl_bars) / len(sl_bars) if sl_bars else 0,
    )
    return outcomes, diag


# ── Portfolio simulation ──────────────────────────────────────────────────────

def resolve(kind, mode, net_sl, rng):
    """Resolve a trade outcome given collision kind and mode."""
    if kind == 'tp':   return 'tp',   NET_TP
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    # kind == 'both'
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', NET_TP
    return ('tp', NET_TP) if rng.random() < 0.5 else ('sl', net_sl)


class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl', 'outcome']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome


def run_single_sim(trading_days, signals_by_date, outcomes, mode, rng):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}
    tp_c = sl_c = hard_c = 0

    for day_idx, today in enumerate(trading_days):
        # Close matured positions
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
        eligible  = [
            (sym_id, score, key)
            for sym_id, score, key in day_signals
            if key in outcomes and sym_id not in open_syms
        ]
        if not eligible:
            continue

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
            o             = outcomes[key]
            outcome, pnl  = resolve(o['kind'], mode, o['net_sl'], rng)
            cash         -= premium_cost
            positions.append(Position(sym_id, today, o['exit_bar'],
                                      premium_cost, pnl, outcome))

    # Hard-sell any remaining open positions
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
        tp_rate   = tp_c   / total * 100,
        sl_rate   = sl_c   / total * 100,
        hard_rate = hard_c / total * 100,
        trades    = total,
    )


# ── Window sweep ──────────────────────────────────────────────────────────────

def run_window_sweep(label, d_start, d_end, version):
    print(f"\n{'='*135}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print('='*135)

    signals   = load_signals(version, d_start, d_end)
    primary_n = sum(1 for s in signals if s.overall >= PRIMARY_THRESHOLD)
    print(f"Signals: {len(signals)}  (75+={primary_n}, 70-74={len(signals) - primary_n})")

    sym_ids     = list({s.symbol_id for s in signals})
    ph          = load_price_history(sym_ids, d_start, d_end)

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

    print("Precomputing raw trade data...", end=' ', flush=True)
    raw_trades = precompute_raw_trades(signals, ph)
    print(f"done. valid={len(raw_trades)}")

    # Column headers
    print(
        f"\n{'SL':>4}  {'SLσ':>6}  {'BE':>5}  {'Bar1%':>6}  {'AvgBar':>7}  "
        f"{'Mode':<13}  {'TP%':>6}  {'SL%':>6}  {'Hard%':>5}  "
        f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  {'P(col)':>6}  Safe"
    )
    print('-'*148)

    results = {}  # sl_pct -> {mode -> metrics dict}

    for sl_pct in SL_SWEEP:
        outcomes, diag = compute_outcomes_for_sl(raw_trades, sl_pct)
        results[sl_pct] = {}
        first_row = True

        for mode in COLLISION_MODES:
            finals = []; dds = []; tps = []; sls = []; hards = []; collapses = 0
            for it in range(N_ITER):
                rng = random.Random(1000 * hash(label) + it)
                r   = run_single_sim(trading_days, signals_by_date, outcomes, mode, rng)
                finals.append(r['final'])
                dds.append(r['max_dd'])
                tps.append(r['tp_rate'])
                sls.append(r['sl_rate'])
                hards.append(r['hard_rate'])
                if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                    collapses += 1

            mean_ret  = (statistics.mean(finals) / STARTING_CASH - 1) * 100
            med_ret   = (statistics.median(finals) / STARTING_CASH - 1) * 100
            mean_dd   = statistics.mean(dds) * 100
            worst_dd  = max(dds) * 100
            p_coll    = collapses / N_ITER * 100
            tp_mean   = statistics.mean(tps)
            sl_mean   = statistics.mean(sls)
            hard_mean = statistics.mean(hards)

            results[sl_pct][mode] = dict(
                mean_ret=mean_ret, med_ret=med_ret,
                mean_dd=mean_dd, worst_dd=worst_dd,
                p_coll=p_coll, tp=tp_mean, sl=sl_mean, hard=hard_mean,
                net_sl=diag['net_sl'], sl_sigma=diag['sl_sigma'],
                be_rate=diag['be_rate'],
            )

            safe = '✓' if worst_dd < 80 and p_coll < 5 else '✗'

            if first_row:
                sl_label = (f"{sl_pct*100:>3.0f}%  {diag['sl_sigma']:>6.3f}σ  "
                            f"{diag['be_rate']:>4.1f}%  {diag['bar1_sl_pct']:>5.1f}%  "
                            f"{diag['mean_sl_bar']:>6.1f}")
                first_row = False
            else:
                sl_label = ' ' * 42

            print(
                f"{sl_label}  {mode:<13}  {tp_mean:>5.1f}%  {sl_mean:>5.1f}%  "
                f"{hard_mean:>4.1f}%  "
                f"{mean_ret:>+13,.0f}%  {med_ret:>+13,.0f}%  "
                f"{worst_dd:>7.1f}%  {mean_dd:>6.1f}%  {p_coll:>5.1f}%  {safe}"
            )

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print('='*135)
    print("MONTE CARLO — STOP LOSS SWEEP (3-Mode Canonical)")
    print('='*135)
    print(f"Fixed: 30 DTE | TP=+30% (NET={NET_TP:+.3f}) | Hard=-50%@day15 (NET={NET_HARD_SELL:+.3f})")
    print(f"Slippage: entry {SLIP_ENTRY:.1%} | TP {SLIP_TP:.1%} | SL {SLIP_SL:.1%} | Hard {SLIP_HARD:.1%}")
    print(f"Alloc: 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow after 75+ slots)")
    print(f"MaxPos: {MAX_POSITIONS}  |  Start: ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}/mode")
    print(f"SL sweep: {[f'{x:.0%}' for x in SL_SWEEP]}")
    print(f"Optimization: Realistic mean return  |  Floor: Conservative worst_DD < 80%")
    print(f"Bar-1 SL% diagnostic: >50% = SL inside daily noise (artifact territory)")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}")

    all_results = {}  # label -> sl_pct -> mode -> metrics
    for label, d_start, d_end in WINDOWS:
        all_results[label] = run_window_sweep(label, d_start, d_end, version)

    # ── Summary tables ────────────────────────────────────────────────────────
    col_w   = 13
    sl_hdrs = [f"SL={x:.0%}" for x in SL_SWEEP]
    hdr_row = f"{'Window':<8}  " + "".join(f"{h:>{col_w}}" for h in sl_hdrs)

    for title, mode, field in [
        ("REALISTIC   — Mean Return",       'realistic',    'mean_ret'),
        ("REALISTIC   — Worst DrawDown",    'realistic',    'worst_dd'),
        ("CONSERVATIVE — Mean Return",      'conservative', 'mean_ret'),
        ("CONSERVATIVE — Worst DrawDown",   'conservative', 'worst_dd'),
    ]:
        print(f"\n{'='*135}")
        print(f"SUMMARY — {title}")
        print('='*135)
        print(hdr_row)
        print('-'*135)
        for label, _, _ in WINDOWS:
            row = f"{label:<8}  "
            for sl_pct in SL_SWEEP:
                val  = all_results[label][sl_pct][mode][field]
                flag = ' (!)' if field == 'worst_dd' and val >= 80 else '    '
                row += f"{val:>+{col_w-5},.0f}%{flag}"
            print(row)

    # ── Optimal SL per window ─────────────────────────────────────────────────
    print(f"\n{'='*135}")
    print("OPTIMAL SL — Realistic max return, floor: Conservative worst_DD < 80% & P(collapse)=0%")
    print('='*135)
    for label, _, _ in WINDOWS:
        best_sl  = None
        best_ret = -1e18
        for sl_pct in SL_SWEEP:
            cons = all_results[label][sl_pct]['conservative']
            real = all_results[label][sl_pct]['realistic']
            if cons['worst_dd'] < 80 and cons['p_coll'] == 0 and real['mean_ret'] > best_ret:
                best_ret = real['mean_ret']
                best_sl  = sl_pct
        if best_sl:
            r = all_results[label][best_sl]
            diag_be = r['realistic']['be_rate']
            print(
                f"  {label:<6}  Optimal SL={best_sl:.0%}"
                f"  Realistic: ret={r['realistic']['mean_ret']:>+10,.0f}%  DD={r['realistic']['worst_dd']:.1f}%  TP%={r['realistic']['tp']:.1f}%"
                f"  Conservative: ret={r['conservative']['mean_ret']:>+10,.0f}%  DD={r['conservative']['worst_dd']:.1f}%"
                f"  BE={diag_be:.1f}%"
            )
        else:
            print(f"  {label:<6}  No safe SL found (all exceed DD floor)")

    # ── Cross-window consistency check ───────────────────────────────────────
    print(f"\n{'='*135}")
    print("CROSS-WINDOW CONSISTENCY — Realistic TP rate by Window x SL%")
    print("(Higher TP rate = wider SL = signal has more room; BE rate shown for reference)")
    print('='*135)
    print(hdr_row)
    print('-'*135)
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for sl_pct in SL_SWEEP:
            tp = all_results[label][sl_pct]['realistic']['tp']
            row += f"{tp:>+{col_w-1}.1f}% "
        print(row)

    print(f"\nBreak-even TP rates per SL%:")
    for sl_pct in SL_SWEEP:
        net_sl  = -sl_pct + SLIP_ENTRY + SLIP_SL
        be_rate = abs(net_sl) / (NET_TP + abs(net_sl)) * 100
        sl_sig  = sl_pct * PREMIUM_MULT / DELTA
        print(f"  SL={sl_pct:.0%}  SLσ={sl_sig:.3f}  NET_SL={net_sl:+.3f}  BE={be_rate:.1f}%")


if __name__ == '__main__':
    main()
