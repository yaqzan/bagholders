"""
monte_carlo_trail.py  -- Trailing stop sweep with opportunity-cost swap

Sweeps TRAIL_SIGMA (None=baseline fixed-TP, 0.7, 0.9, 1.1, 1.3) and
OPP_SWAP (False/True) over key windows.

Trailing mechanics
------------------
  When the underlying first touches the TP level (tp_level = entry * (1 + TP_SIGMA*vol/100)):
    - Position enters trail mode instead of closing
    - High-water mark (HWM) of underlying is tracked bar-by-bar
    - Trail fires when: bar_low <= HWM * (1 - trail_sigma * vol / 100)
    - Hard sell still applies at day 15 (HOLD_DAYS)
    - SL fires normally before TP can be reached

  Trail exit P&L uses delta approximation:
    underlying_gain = (exit_price - entry) / entry
    option_pnl_gross = DELTA * underlying_gain / premium_pct_frac
    net = option_pnl_gross + SLIP_ENTRY + SLIP_TRAIL

Opportunity cost swap (OPP_SWAP=True)
--------------------------------------
  Each day, if positions == MAX_POSITIONS AND a fresh 75+ signal wants a slot:
    - Find the lowest-scored trailing position (already past TP, in trail mode)
    - If best_new_score > lowest_trail_score: swap them
    - Closed trail gets current-trail-stop P&L (conservative: as if trail fired today)
    - Rationale: fresh primary win rate (~67-82%) > post-TP continuation rate (~50%)

Collision handling: fixed 'realistic' mode (coin-flip) to keep sweep fast.

Usage:
    python monte_carlo_trail.py          # 2022/2024/2025 windows
    python monte_carlo_trail.py --full   # also runs 5y
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

# ---- Strategy constants (match canonical MC) --------------------------------
STARTING_CASH  = 50_000.0
N_ITER         = 100
VOL_LOOKBACK   = 60
HOLD_DAYS      = 15
PREMIUM_MULT   = 1.82
DELTA          = 0.5

TP_OPTION_GAIN = 0.30
SL_OPTION_LOSS = -0.25
HARD_SELL_LOSS = -0.50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000   # limit sell at TP — no transaction costs
SLIP_SL    = -0.013
SLIP_HARD  = -0.005
SLIP_TRAIL = -0.008   # trail exit: limit order on adverse, between TP and SL quality

NET_TP        = TP_OPTION_GAIN + SLIP_ENTRY + SLIP_TP
NET_SL        = SL_OPTION_LOSS + SLIP_ENTRY + SLIP_SL
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD

TP_SIGMA = TP_OPTION_GAIN * PREMIUM_MULT / DELTA   # 1.092 sigma -- underlying move to hit TP
SL_SIGMA = 0.25           * PREMIUM_MULT / DELTA   # 0.910 sigma

TIER_ALLOC = {
    'top':      0.15,
    'high':     0.12,
    'mid':      0.10,
    'low':      0.10,
    'overflow': 0.05,
}
MAX_POSITIONS      = 10
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
COLLAPSE_THRESHOLD = 0.20

# Sweep params (CLAUDE.md: trail must be >= 0.74 sigma to avoid cutting at MAE trough)
TRAIL_SIGMAS  = [None, 0.7, 0.9, 1.1, 1.3]
OPP_SWAP_OPTS = [False, True]

WINDOWS_DEFAULT = [
    ('2022', date(2022, 1, 1), date(2022, 12, 31)),
    ('2024', date(2024, 1, 1), date(2024, 12, 31)),
    ('2025', date(2025, 1, 1), date(2025, 12, 31)),
]
WINDOW_5Y = ('5y', date(2021, 1, 1), date(2026, 4, 15))


def score_to_tier(score):
    if score >= 90: return 'top'
    if score >= 85: return 'high'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


# ---- Data loading -----------------------------------------------------------

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
    # Extra buffer: 120d vol lookback + 45d for trail walk after signal
    rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.close, PriceHistory.high, PriceHistory.low
        )
        .where(
            PriceHistory.symbol.in_(sym_ids),
            PriceHistory.date >= d_start - timedelta(days=120),
            PriceHistory.date <= d_end   + timedelta(days=45),
        )
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .tuples()
    )
    # Ordered list per symbol (for vol calc)
    ph_list = defaultdict(list)
    # Date-keyed dict per symbol (for fast bar lookup in trail walk)
    ph_date = defaultdict(dict)
    for sym_id, d, c, h, l in rows:
        ph_list[sym_id].append((d, float(c), float(h), float(l)))
        ph_date[sym_id][d] = (float(c), float(h), float(l))
    return ph_list, ph_date


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


# ---- Per-trade base outcome (identical to canonical MC) ---------------------

def compute_base_outcome(sym_bars, signal_date):
    """Compute TP/SL/hard/both outcome (pre-trail). Returns None if insufficient data."""
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

    premium_pct = PREMIUM_MULT * vol / 100   # fraction of stock price
    tp_level    = entry * (1 + TP_SIGMA * vol / 100)
    sl_level    = entry * (1 - SL_SIGMA * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_level
        sl_hit = lows[i]  <= sl_level
        kind   = None
        if tp_hit and sl_hit: kind = 'both'
        elif tp_hit:           kind = 'tp'
        elif sl_hit:           kind = 'sl'
        if kind:
            return dict(kind=kind, exit_bar=i - base_idx,
                        entry=entry, vol=vol, premium_pct=premium_pct,
                        tp_level=tp_level, sl_level=sl_level)

    return dict(kind='hard', exit_bar=HOLD_DAYS,
                entry=entry, vol=vol, premium_pct=premium_pct,
                tp_level=tp_level, sl_level=sl_level)


def precompute_base_outcomes(signals, ph_list):
    outcomes = {}
    for sig in signals:
        bars = ph_list.get(sig.symbol_id)
        if not bars:
            continue
        r = compute_base_outcome(bars, sig.date)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


# ---- Position ---------------------------------------------------------------

class Position:
    __slots__ = [
        'sym_id', 'entry_date', 'entry_bar', 'original_score',
        'premium_cost', 'premium_pct', 'vol', 'entry_price',
        'tp_level', 'sl_level',
        'base_exit_bar', 'base_kind',
        # trail state
        'in_trail', 'hwm', 'trail_entry_bar',
    ]

    def __init__(self, sym_id, entry_date, entry_bar, original_score,
                 premium_cost, premium_pct, vol, entry_price,
                 tp_level, sl_level, base_exit_bar, base_kind):
        self.sym_id         = sym_id
        self.entry_date     = entry_date
        self.entry_bar      = entry_bar
        self.original_score = original_score
        self.premium_cost   = premium_cost
        self.premium_pct    = premium_pct
        self.vol            = vol
        self.entry_price    = entry_price
        self.tp_level       = tp_level
        self.sl_level       = sl_level
        self.base_exit_bar  = base_exit_bar
        self.base_kind      = base_kind
        self.in_trail       = False
        self.hwm            = None
        self.trail_entry_bar = None


# ---- P&L helpers ------------------------------------------------------------

def _trail_pnl(pos, exit_price):
    """Net option P&L for a trail exit at exit_price."""
    underlying_gain = (exit_price - pos.entry_price) / pos.entry_price
    gross = DELTA * underlying_gain / pos.premium_pct
    return gross + SLIP_ENTRY + SLIP_TRAIL


def _trail_stop_price(pos, trail_sigma):
    """Current trail stop level given HWM."""
    return pos.hwm * (1 - trail_sigma * pos.vol / 100)


# ---- Core simulation --------------------------------------------------------

def run_single_sim(trading_days, signals_by_date, base_outcomes, ph_date,
                   mode, rng, trail_sigma, opp_swap):
    cash        = STARTING_CASH
    positions   = []
    peak_value  = STARTING_CASH
    max_dd      = 0.0

    tp_c = sl_c = hard_c = trail_c = swap_c = 0
    extra_gains = []   # option_pnl_trail - NET_TP for each trail exit
    blocked_c   = 0    # fresh signals blocked by full slots

    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    for day_idx, today in enumerate(trading_days):
        # ---- 1. Process open positions ---------------------------------------
        keep = []
        for p in positions:
            bars_elapsed = day_idx - p.entry_bar

            if not p.in_trail:
                # Base outcome fires at this bar?
                if bars_elapsed < p.base_exit_bar:
                    keep.append(p)
                    continue

                # Resolve collision
                if p.base_kind == 'both':
                    outcome = ('tp' if rng.random() < 0.5 else 'sl') if mode == 'realistic' else \
                              ('sl' if mode == 'conservative' else 'tp')
                else:
                    outcome = p.base_kind

                if outcome == 'tp' and trail_sigma is not None:
                    # Enter trail mode: HWM starts at tp_level
                    p.in_trail       = True
                    p.hwm            = p.tp_level
                    p.trail_entry_bar = day_idx
                    keep.append(p)
                elif outcome == 'tp':
                    cash += p.premium_cost * (1 + NET_TP)
                    tp_c += 1
                elif outcome == 'sl':
                    cash += p.premium_cost * (1 + NET_SL)
                    sl_c += 1
                else:
                    cash += p.premium_cost * (1 + NET_HARD_SELL)
                    hard_c += 1

            else:
                # Trail mode: walk today's bar
                bar = ph_date[p.sym_id].get(today)
                if bar is None:
                    keep.append(p)
                    continue

                c_today, h_today, l_today = bar
                stop = _trail_stop_price(p, trail_sigma)

                # Hard sell at day 15
                if bars_elapsed >= HOLD_DAYS:
                    pnl = _trail_pnl(p, c_today)
                    cash += p.premium_cost * (1 + pnl)
                    hard_c += 1
                    extra_gains.append(pnl - NET_TP)
                    continue

                # Update HWM
                if h_today > p.hwm:
                    p.hwm = h_today
                    stop  = _trail_stop_price(p, trail_sigma)

                # Trail triggered?
                if l_today <= stop:
                    exit_price = min(stop, l_today)
                    pnl = _trail_pnl(p, exit_price)
                    cash += p.premium_cost * (1 + pnl)
                    trail_c += 1
                    extra_gains.append(pnl - NET_TP)
                    continue

                keep.append(p)

        positions = keep

        # ---- 2. Mark-to-market, drawdown ------------------------------------
        portfolio_value = cash + sum(p.premium_cost for p in positions)
        if portfolio_value > peak_value:
            peak_value = portfolio_value
        dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
        if dd > max_dd:
            max_dd = dd

        if portfolio_value <= STARTING_CASH * COLLAPSE_THRESHOLD:
            break

        # ---- 3. Opportunity cost swap ---------------------------------------
        if opp_swap and trail_sigma is not None and len(positions) >= MAX_POSITIONS:
            trailing = [p for p in positions if p.in_trail]
            if trailing:
                open_syms     = {p.sym_id for p in positions}
                today_sigs    = signals_by_date.get(today, [])
                fresh_primary = sorted(
                    [(sym, sc, key) for sym, sc, key in today_sigs
                     if key in base_outcomes and sym not in open_syms
                     and sc >= PRIMARY_THRESHOLD],
                    key=lambda x: -x[1]
                )
                if fresh_primary:
                    best_new_score = fresh_primary[0][1]
                    worst_trail = min(trailing, key=lambda p: p.original_score)
                    if best_new_score > worst_trail.original_score:
                        # Close trail at current stop level
                        bar = ph_date[worst_trail.sym_id].get(today)
                        if bar is not None:
                            stop = _trail_stop_price(worst_trail, trail_sigma)
                            exit_p = min(stop, bar[2])   # conservative: lower of stop or today's low
                            pnl = _trail_pnl(worst_trail, exit_p)
                            cash += worst_trail.premium_cost * (1 + pnl)
                            extra_gains.append(pnl - NET_TP)
                            positions.remove(worst_trail)
                            trail_c  += 1
                            swap_c   += 1

        # ---- 4. Open new positions ------------------------------------------
        day_signals = signals_by_date.get(today, [])
        if not day_signals:
            continue

        open_syms = {p.sym_id for p in positions}
        eligible  = [(sym, sc, key) for sym, sc, key in day_signals
                     if key in base_outcomes and sym not in open_syms]

        primary  = sorted([e for e in eligible if e[1] >= PRIMARY_THRESHOLD],
                          key=lambda x: (-x[1], rng.random()))
        overflow = sorted([e for e in eligible if e[1] <  PRIMARY_THRESHOLD],
                          key=lambda x: (-x[1], rng.random()))

        for sym_id, score, key in primary + overflow:
            if len(positions) >= MAX_POSITIONS:
                # Track if a fresh primary signal was blocked
                if score >= PRIMARY_THRESHOLD:
                    blocked_c += 1
                break
            tier         = score_to_tier(score)
            alloc_frac   = TIER_ALLOC[tier]
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                continue
            oc = base_outcomes[key]
            cash -= premium_cost
            positions.append(Position(
                sym_id      = sym_id,
                entry_date  = today,
                entry_bar   = day_idx,
                original_score = score,
                premium_cost   = premium_cost,
                premium_pct    = oc['premium_pct'],
                vol            = oc['vol'],
                entry_price    = oc['entry'],
                tp_level       = oc['tp_level'],
                sl_level       = oc['sl_level'],
                base_exit_bar  = oc['exit_bar'],
                base_kind      = oc['kind'],
            ))

    # ---- Close remainder at hard sell ---------------------------------------
    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        hard_c += 1

    portfolio_value = cash
    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    total_trades = tp_c + sl_c + hard_c + trail_c or 1

    return dict(
        final      = portfolio_value,
        max_dd     = max_dd,
        tp_rate    = tp_c    / total_trades * 100,
        sl_rate    = sl_c    / total_trades * 100,
        hard_rate  = hard_c  / total_trades * 100,
        trail_rate = trail_c / total_trades * 100,
        trades     = total_trades,
        avg_trail_extra = (sum(extra_gains) / len(extra_gains) * 100) if extra_gains else 0.0,
        n_swaps    = swap_c,
        blocked    = blocked_c,
    )


# ---- Window runner ----------------------------------------------------------

def run_window(label, d_start, d_end, version):
    print(f"\n{'='*110}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print('='*110)

    signals = load_signals(version, d_start, d_end)
    print(f"Signals: {len(signals)}  "
          f"(75+={sum(1 for s in signals if s.overall >= PRIMARY_THRESHOLD)}, "
          f"70-74={sum(1 for s in signals if s.overall < PRIMARY_THRESHOLD)})")

    sym_ids = list({s.symbol_id for s in signals})
    ph_list, ph_date = load_price_history(sym_ids, d_start, d_end)

    ph_dates = {d for bars in ph_list.values() for d, *_ in bars
                if d_start <= d <= d_end + timedelta(days=20)}
    trading_days = sorted(ph_dates)

    signals_by_date = defaultdict(list)
    for sig in signals:
        key = (sig.symbol_id, sig.date)
        signals_by_date[sig.date].append((sig.symbol_id, sig.overall, key))

    print("Precomputing base outcomes...", end=' ', flush=True)
    base_outcomes = precompute_base_outcomes(signals, ph_list)
    tp_n   = sum(1 for o in base_outcomes.values() if o['kind'] == 'tp')
    sl_n   = sum(1 for o in base_outcomes.values() if o['kind'] == 'sl')
    both_n = sum(1 for o in base_outcomes.values() if o['kind'] == 'both')
    hard_n = sum(1 for o in base_outcomes.values() if o['kind'] == 'hard')
    tot    = len(base_outcomes) or 1
    print(f"done. {len(base_outcomes)} trades  "
          f"TP={tp_n/tot*100:.1f}%  SL={sl_n/tot*100:.1f}%  "
          f"Both={both_n/tot*100:.1f}%  Hard={hard_n/tot*100:.1f}%")

    # Header
    print()
    print(f"{'Trail':>6}  {'Swap':>5} | "
          f"{'MeanRet%':>12}  {'MedRet%':>12}  {'WorstDD%':>9}  {'MeanDD%':>8} | "
          f"{'TP%':>6}  {'SL%':>6}  {'Trail%':>7}  {'Hard%':>6} | "
          f"{'AvgXtra%':>9}  {'AvgSwaps':>9}  {'Blocked':>8}")
    print('-' * 130)

    results = {}
    for trail_sigma in TRAIL_SIGMAS:
        for opp_swap in OPP_SWAP_OPTS:
            # baseline (no trail) only needs one opp_swap=False run
            if trail_sigma is None and opp_swap:
                continue

            label_t = f"{trail_sigma:.1f}s" if trail_sigma else "None "
            label_s = "Y" if opp_swap else "N"

            finals = []; dds = []; tps = []; sls = []; hards = []; trails = []
            extras = []; swaps_l = []; blocked_l = []

            for it in range(N_ITER):
                rng = random.Random(42 + it * 997)
                r = run_single_sim(
                    trading_days, signals_by_date, base_outcomes, ph_date,
                    'realistic', rng, trail_sigma, opp_swap
                )
                finals.append(r['final'])
                dds.append(r['max_dd'])
                tps.append(r['tp_rate'])
                sls.append(r['sl_rate'])
                hards.append(r['hard_rate'])
                trails.append(r['trail_rate'])
                extras.append(r['avg_trail_extra'])
                swaps_l.append(r['n_swaps'])
                blocked_l.append(r['blocked'])

            mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
            med_ret  = (statistics.median(finals) / STARTING_CASH - 1) * 100
            worst_dd = max(dds) * 100
            mean_dd  = statistics.mean(dds) * 100
            avg_xtra = statistics.mean(extras)
            avg_swps = statistics.mean(swaps_l)
            avg_blkd = statistics.mean(blocked_l)

            key = (trail_sigma, opp_swap)
            results[key] = dict(mean_ret=mean_ret, med_ret=med_ret,
                                worst_dd=worst_dd, mean_dd=mean_dd)

            print(f"{label_t:>6}  {label_s:>5} | "
                  f"{mean_ret:>+11.1f}%  {med_ret:>+11.1f}%  "
                  f"{worst_dd:>8.1f}%  {mean_dd:>7.1f}% | "
                  f"{statistics.mean(tps):>5.1f}%  {statistics.mean(sls):>5.1f}%  "
                  f"{statistics.mean(trails):>6.1f}%  {statistics.mean(hards):>5.1f}% | "
                  f"{avg_xtra:>+8.2f}%  {avg_swps:>9.1f}  {avg_blkd:>8.1f}")

    return results


# ---- Main -------------------------------------------------------------------

def main():
    run_5y = '--full' in sys.argv
    windows = WINDOWS_DEFAULT + ([WINDOW_5Y] if run_5y else [])

    print('=' * 110)
    print("MONTE CARLO -- Trailing Stop Sweep with Opportunity Cost Swap")
    print('=' * 110)
    print(f"Strategy : 30 DTE | TP=+30% -> trail | SL=-25% | Hard=-50%@day15")
    print(f"Slippage : entry={SLIP_ENTRY*100:.1f}%  TP={SLIP_TP*100:.1f}%  "
          f"SL={SLIP_SL*100:.1f}%  Hard={SLIP_HARD*100:.1f}%  Trail={SLIP_TRAIL*100:.1f}%")
    print(f"           NET_TP={NET_TP:+.3f}  NET_SL={NET_SL:+.3f}  NET_HARD={NET_HARD_SELL:+.3f}")
    print(f"Alloc    : 90+=15%  85-89=12%  80-84=10%  75-79=10%  70-74=5%")
    print(f"MaxPos   : {MAX_POSITIONS}  |  Threshold: {PRIMARY_THRESHOLD}+  |  Collision: realistic")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"TP sigma : {TP_SIGMA:.3f}  |  SL sigma: {SL_SIGMA:.3f}")
    print(f"Trail    : {TRAIL_SIGMAS}  (None = baseline, no trail)")
    print(f"OppSwap  : {OPP_SWAP_OPTS}  (swap lowest-scored trailing pos for fresh higher score)")
    print()
    print("Columns: Trail=trail_sigma  Swap=opp_cost_swap  AvgXtra=avg option pnl delta vs NET_TP")
    print("         Blocked=avg fresh primary signals blocked/day by full trail slots")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: v{version.id} ({version.git_commit})")

    all_results = {}
    for label, d_start, d_end in windows:
        all_results[label] = run_window(label, d_start, d_end, version)

    # Summary: baseline vs best trail per window
    print(f"\n{'='*110}")
    print("SUMMARY -- baseline vs best trail (mean return)")
    print('='*110)
    for label, d_start, d_end in windows:
        res = all_results.get(label, {})
        baseline = res.get((None, False))
        if not baseline:
            continue
        best_trail = max(
            ((k, v) for k, v in res.items() if k[0] is not None),
            key=lambda x: x[1]['mean_ret'],
            default=(None, None)
        )
        bline_str = f"baseline: {baseline['mean_ret']:>+10,.1f}%  DD={baseline['worst_dd']:.1f}%"
        if best_trail[0]:
            t, s = best_trail[0]
            v = best_trail[1]
            best_str = (f"best_trail({t:.1f}s, swap={'Y' if s else 'N'}): "
                        f"{v['mean_ret']:>+10,.1f}%  DD={v['worst_dd']:.1f}%")
            delta = v['mean_ret'] - baseline['mean_ret']
            print(f"  {label:<6} {bline_str}  |  {best_str}  |  delta={delta:>+8,.1f}pp")
        else:
            print(f"  {label:<6} {bline_str}")


if __name__ == '__main__':
    main()
