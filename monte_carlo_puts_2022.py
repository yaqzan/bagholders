"""
Monte Carlo — 2022 Bear Window with Put Variants
=================================================

Tests whether adding put option signals to the 2022 bear-year strategy
improves returns vs. the calls-only baseline.

Context: The asymmetric weekly sweep (2026-04-17) showed puts improve
monotonically with weekly_scale on the put side — <15 WR30 hits 69.3% at
scale 1.5x (vs 64.1% baseline) and <5 WR30 hits 70.6% at scale 1.75x.
Before shipping the weekly change, this script checks whether the
*current v17 DB puts* (baseline calibration, not scaled) are already
productive in a bear-year portfolio context.

Variants tested:
  A. Calls only              (control — matches monte_carlo.py)
  B. Calls + Puts (all <=25) (cascade mirrors calls)
  C. Calls + Puts (<=15 only, 5% alloc)
  D. Calls + Puts (<=10 only, 5% alloc)

All variants use identical call handling (85+=15% / 80-84=12% / 75-79=12% /
70-74=5% overflow, MaxPos=14, breadth-adaptive TP/SL).
Puts enter the same slot pool (share MaxPos=14 with calls).

Strategy (identical to monte_carlo.py for call side):
  DTE       : 30
  TP        : +30% base / +35% stressed  (option premium)
  SL        : -35% base / -40% stressed  (option premium)
  Hard sell : -50% at day 15
  Slippage  : entry -1%, TP 0%, SL -1.3%, hard -0.5%

Put outcome semantics (mirror of call):
  Call TP = underlying rises tp_sigma   -> put TP = underlying falls tp_sigma
  Call SL = underlying falls sl_sigma   -> put SL = underlying rises sl_sigma
  Net option P&L on each outcome is identical to call side (same slippage,
  same delta=0.5 assumption). ATM put premium scales with sigma the same way.

Signal tiers:
  Calls:  top=85+, mid=80-84, low=75-79, overflow=70-74
  Puts (B):  top=<=15, mid=16-20, low=21-25  (mirror of call thresholds around 50)
  Puts (C/D): single reserved tier at 5% alloc
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

# ---- Shared constants -------------------------------------------------------
STARTING_CASH      = 50_000.0
N_ITER             = 300
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

MAX_POSITIONS      = 14
COLLAPSE_THRESHOLD = 0.20

# Call allocation (same as monte_carlo.py)
CALL_ALLOC = {'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.05}
CALL_PRIMARY_THRESH  = 75
CALL_OVERFLOW_THRESH = 70

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']


def call_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


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


def breadth_on_or_before(sorted_dates, bmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    return bmap[sorted_dates[idx]] if idx >= 0 else None


def is_stressed(sorted_dates, bmap, d):
    b = breadth_on_or_before(sorted_dates, bmap, d)
    return b is not None and b <= BREADTH_THRESHOLD


def load_signals_range(version, d_start, d_end, score_filter):
    """score_filter: peewee expression (e.g. Score.overall >= 70)."""
    return list(
        Score.select(Score.symbol, Score.date, Score.overall)
        .where(
            Score.version == version,
            Score.date >= d_start,
            Score.date <= d_end,
            score_filter,
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


# ---- Per-trade outcome ------------------------------------------------------
# For calls: TP = high hits entry*(1 + tp_sigma*vol); SL = low hits entry*(1 - sl_sigma*vol)
# For puts:  TP = low  hits entry*(1 - tp_sigma*vol); SL = high hits entry*(1 + sl_sigma*vol)

def compute_trade_outcome(sym_bars, signal_date, stressed, is_put):
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

    if is_put:
        tp_level = entry * (1 - tp_sigma * vol / 100)  # price must FALL
        sl_level = entry * (1 + sl_sigma * vol / 100)  # price must RISE
    else:
        tp_level = entry * (1 + tp_sigma * vol / 100)
        sl_level = entry * (1 - sl_sigma * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        if is_put:
            tp_hit = lows[i]  <= tp_level
            sl_hit = highs[i] >= sl_level
        else:
            tp_hit = highs[i] >= tp_level
            sl_hit = lows[i]  <= sl_level
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', i - base_idx; break
        if tp_hit:
            kind, exit_bar = 'tp',   i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl',   i - base_idx; break

    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl)


def precompute_outcomes(signals, ph, breadth_dates, breadth_map, is_put):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        stressed = is_stressed(breadth_dates, breadth_map, sig.date)
        r = compute_trade_outcome(sym_bars, sig.date, stressed, is_put)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def resolve(kind, mode, rng, net_tp, net_sl):
    if kind == 'tp':   return 'tp',   net_tp
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', net_tp
    return ('tp', net_tp) if rng.random() < 0.5 else ('sl', net_sl)


# ---- Portfolio sim ----------------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl', 'outcome', 'side']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome, side):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome
        self.side         = side  # 'call' | 'put'


def run_single_sim(trading_days, call_signals_by_date, put_signals_by_date,
                   call_outcomes, put_outcomes, put_tier_fn, mode, rng):
    """
    put_tier_fn(score) -> alloc fraction (0.0 to skip the put)
    Put signals are added to the same eligible pool; calls always fill first
    within each day (primary call, overflow call, then put cascade).
    """
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
                    if   p.outcome == 'tp': tp_c += 1
                    elif p.outcome == 'sl': sl_c += 1
                    else:                   hard_c += 1
                else:
                    if   p.outcome == 'tp': tp_p += 1
                    elif p.outcome == 'sl': sl_p += 1
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

        open_syms = {p.sym_id for p in positions}

        # Calls — primary then overflow
        calls = [(sid, sc, k) for sid, sc, k in call_signals_by_date.get(today, [])
                 if k in call_outcomes and sid not in open_syms]
        primary  = [e for e in calls if e[1] >= CALL_PRIMARY_THRESH]
        overflow = [e for e in calls if e[1] <  CALL_PRIMARY_THRESH]
        primary.sort(key=lambda x: (-x[1], rng.random()))
        overflow.sort(key=lambda x: (-x[1], rng.random()))

        for sym_id, score, key in primary + overflow:
            if len(positions) >= MAX_POSITIONS:
                break
            alloc_frac = CALL_ALLOC[call_tier(score)]
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                continue
            o = call_outcomes[key]
            outcome, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
            cash -= premium_cost
            positions.append(Position(sym_id, today, o['exit_bar'],
                                       premium_cost, pnl, outcome, 'call'))
            open_syms.add(sym_id)

        # Puts — iterate if put_tier_fn is not None
        if put_tier_fn is not None and len(positions) < MAX_POSITIONS:
            puts = [(sid, sc, k) for sid, sc, k in put_signals_by_date.get(today, [])
                    if k in put_outcomes and sid not in open_syms]
            # Sort: more extreme (lower score) first
            puts.sort(key=lambda x: (x[1], rng.random()))
            for sym_id, score, key in puts:
                if len(positions) >= MAX_POSITIONS:
                    break
                alloc_frac = put_tier_fn(score)
                if alloc_frac <= 0:
                    continue
                premium_cost = portfolio_value * alloc_frac
                if premium_cost > cash or premium_cost <= 0:
                    continue
                o = put_outcomes[key]
                outcome, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= premium_cost
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'put'))
                open_syms.add(sym_id)

    # Close remainder
    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else:                hard_p += 1
    portfolio_value = cash

    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd = max(max_dd, final_dd)

    call_trades = tp_c + sl_c + hard_c
    put_trades  = tp_p + sl_p + hard_p

    return dict(
        final = portfolio_value,
        max_dd = max_dd,
        call_tp_rate = (tp_c / call_trades * 100) if call_trades else 0,
        call_sl_rate = (sl_c / call_trades * 100) if call_trades else 0,
        put_tp_rate  = (tp_p / put_trades * 100) if put_trades else 0,
        put_sl_rate  = (sl_p / put_trades * 100) if put_trades else 0,
        call_trades  = call_trades,
        put_trades   = put_trades,
    )


# ---- Variants ---------------------------------------------------------------

def put_tier_cascade_mirror(score):
    """Mirror of call cascade around 50:
       <=15 -> top (15%)  |  16-20 -> mid (12%)  |  21-25 -> low (12%)
    """
    if score <= 15: return 0.15
    if score <= 20: return 0.12
    if score <= 25: return 0.12
    return 0.0


def put_tier_15_only(score):
    if score <= 15: return 0.05
    return 0.0


def put_tier_10_only(score):
    if score <= 10: return 0.05
    return 0.0


def put_tier_extreme_alloc(score):
    """More aggressive extreme-only allocation."""
    if score <= 10: return 0.12
    if score <= 15: return 0.08
    return 0.0


VARIANTS = [
    ('A_calls_only',             None,                        "Calls only (baseline)"),
    ('B_full_put_cascade',       put_tier_cascade_mirror,     "Calls + puts <=25 (full cascade)"),
    ('C_puts_le15_5pct',         put_tier_15_only,            "Calls + puts <=15 only @ 5%"),
    ('D_puts_le10_5pct',         put_tier_10_only,            "Calls + puts <=10 only @ 5%"),
    ('E_puts_extreme_tiered',    put_tier_extreme_alloc,      "Calls + puts: <=10@12%, 11-15@8%"),
]


# ---- Runner -----------------------------------------------------------------

def run_window(label, d_start, d_end):
    print('='*100)
    print(f"MC - {label} Window with Put Variants  ({d_start} -> {d_end})")
    print('='*100)
    print(f"Strategy : 30 DTE | breadth-adaptive | MaxPos={MAX_POSITIONS} | $50k start | {N_ITER} iter/mode")
    print(f"  TP={TP_BASE:+.0%}/{TP_STRESS:+.0%}  SL={SL_BASE:+.0%}/{SL_STRESS:+.0%}")
    print(f"  Slippage: entry -1% | TP 0% | SL -1.3% | hard -0.5%")
    print(f"  Calls cascade: 85+=15% 80-84=12% 75-79=12% 70-74=5%")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")

    # Load signals
    call_signals = load_signals_range(version, d_start, d_end, Score.overall >= CALL_OVERFLOW_THRESH)
    put_signals  = load_signals_range(version, d_start, d_end, Score.overall <= 25)

    print(f"\nCall signals: {len(call_signals)}  |  Put signals: {len(put_signals)}")

    sym_ids = list({s.symbol_id for s in call_signals} | {s.symbol_id for s in put_signals})
    ph = load_price_history(sym_ids, d_start, d_end)

    breadth_dates, breadth_map = load_breadth_map(d_start, d_end)
    if call_signals:
        stressed = sum(1 for s in call_signals if is_stressed(breadth_dates, breadth_map, s.date))
        print(f"Breadth stressed on {stressed/len(call_signals)*100:.1f}% of call signal days")

    # Trading day universe
    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    call_signals_by_date = defaultdict(list)
    for sig in call_signals:
        key = (sig.symbol_id, sig.date)
        call_signals_by_date[sig.date].append((sig.symbol_id, sig.overall, key))

    put_signals_by_date = defaultdict(list)
    for sig in put_signals:
        key = (sig.symbol_id, sig.date)
        put_signals_by_date[sig.date].append((sig.symbol_id, sig.overall, key))

    print("Precomputing call outcomes...", end=' ', flush=True)
    call_outcomes = precompute_outcomes(call_signals, ph, breadth_dates, breadth_map, is_put=False)
    print(f"{len(call_outcomes)}")

    print("Precomputing put outcomes... ", end=' ', flush=True)
    put_outcomes = precompute_outcomes(put_signals, ph, breadth_dates, breadth_map, is_put=True)
    print(f"{len(put_outcomes)}")

    # Sanity: report per-bucket TP rates on precomputed outcomes
    print("\nRaw outcome TP rates (pre-portfolio, pre-collision-split):")
    for bucket_label, bucket_signals in [
        ('Calls 70-74', [s for s in call_signals if s.overall < 75]),
        ('Calls 75-79', [s for s in call_signals if 75 <= s.overall < 80]),
        ('Calls 80-84', [s for s in call_signals if 80 <= s.overall < 85]),
        ('Calls 85+',   [s for s in call_signals if s.overall >= 85]),
        ('Puts <=25',   [s for s in put_signals  if s.overall <= 25]),
        ('Puts <=20',   [s for s in put_signals  if s.overall <= 20]),
        ('Puts <=15',   [s for s in put_signals  if s.overall <= 15]),
        ('Puts <=10',   [s for s in put_signals  if s.overall <= 10]),
    ]:
        outs = [(call_outcomes if 'Call' in bucket_label else put_outcomes).get((s.symbol_id, s.date))
                for s in bucket_signals]
        outs = [o for o in outs if o is not None]
        if not outs:
            continue
        tp = sum(1 for o in outs if o['kind'] == 'tp')
        sl = sum(1 for o in outs if o['kind'] == 'sl')
        both = sum(1 for o in outs if o['kind'] == 'both')
        hard = sum(1 for o in outs if o['kind'] == 'hard')
        total = len(outs)
        print(f"  {bucket_label:<14} N={total:>5}  TP={tp/total*100:>5.1f}%  "
              f"SL={sl/total*100:>5.1f}%  Both={both/total*100:>4.1f}%  "
              f"Hard={hard/total*100:>4.1f}%")

    # Run variants x modes
    print("\n" + '='*110)
    print(f"{'Variant':<28} {'Mode':<13}  {'CallTP%':>7}  {'PutTP%':>7}  "
          f"{'CTrd':>5}  {'PTrd':>5}  {'MeanRet':>13}  {'MedRet':>13}  {'WorstDD':>8}  {'MeanDD':>7}  {'P(col)':>7}")
    print('-'*110)

    all_results = {}
    for vlabel, put_fn, vdesc in VARIANTS:
        all_results[vlabel] = {}
        for mode in COLLISION_MODES:
            finals = []; dds = []; coll = 0
            ctp = []; ptp = []; ctrd = []; ptrd = []
            for it in range(N_ITER):
                rng = random.Random(1000 * hash(vlabel + mode) + it)
                r = run_single_sim(
                    trading_days, call_signals_by_date, put_signals_by_date,
                    call_outcomes, put_outcomes, put_fn, mode, rng,
                )
                finals.append(r['final'])
                dds.append(r['max_dd'])
                ctp.append(r['call_tp_rate']); ptp.append(r['put_tp_rate'])
                ctrd.append(r['call_trades']); ptrd.append(r['put_trades'])
                if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                    coll += 1

            mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
            med_ret  = (statistics.median(finals) / STARTING_CASH - 1) * 100
            mean_dd  = statistics.mean(dds) * 100
            worst_dd = max(dds) * 100
            p_col    = coll / N_ITER * 100

            all_results[vlabel][mode] = dict(
                mean_ret=mean_ret, med_ret=med_ret,
                mean_dd=mean_dd, worst_dd=worst_dd, p_col=p_col,
                call_tp=statistics.mean(ctp), put_tp=statistics.mean(ptp),
                call_trades=statistics.mean(ctrd), put_trades=statistics.mean(ptrd),
            )
            r = all_results[vlabel][mode]
            print(f"{vlabel:<28} {mode:<13}  {r['call_tp']:>6.1f}%  {r['put_tp']:>6.1f}%  "
                  f"{r['call_trades']:>5.1f}  {r['put_trades']:>5.1f}  "
                  f"{r['mean_ret']:>+12.1f}%  {r['med_ret']:>+12.1f}%  "
                  f"{r['worst_dd']:>7.1f}%  {r['mean_dd']:>6.1f}%  {r['p_col']:>6.1f}%")
        print()

    # Summary
    print('='*110)
    print("SUMMARY - Realistic mode (mean return) + WorstDD per variant")
    print('='*110)
    baseline = all_results['A_calls_only']['realistic']['mean_ret']
    print(f"{'Variant':<28}  {'MeanRet':>12}  {'vs calls-only':>16}  {'WorstDD':>10}")
    print('-'*75)
    for vlabel, _, vdesc in VARIANTS:
        r = all_results[vlabel]['realistic']
        delta_pp = r['mean_ret'] - baseline
        arrow = '+' if delta_pp > 0 else ''
        print(f"{vlabel:<28}  {r['mean_ret']:>+11.1f}%  {arrow}{delta_pp:>+14.1f}pp  {r['worst_dd']:>9.1f}%")

    print('\n(Conservative floor and Optimistic ceiling in the detailed table above.)')
    print(f"\nDescriptions:")
    for vlabel, _, vdesc in VARIANTS:
        print(f"  {vlabel:<28} = {vdesc}")


WINDOWS = [
    ('2022', date(2022, 1, 1), date(2022, 12, 31)),
    ('2025', date(2025, 1, 1), date(2025, 12, 31)),
]


if __name__ == '__main__':
    for label, d_start, d_end in WINDOWS:
        run_window(label, d_start, d_end)
        print("\n\n")
