"""
Monte Carlo — Put-Specific TP/SL Sweep (2022 + 2025)
=====================================================

The previous puts run (monte_carlo_puts_2022.py) mirrored call TP/SL exactly:
TP=+30/+35 base/stress, SL=-35/-40. Variant B crushed 2022 (+16k%) but destroyed
2025 (-54%). Hypothesis: tightening the put SL lowers the breakeven TP rate
enough to flip 2025 positive while keeping 2022 edge.

Breakeven math (realistic slippage entry -1%, TP 0%, SL -1.3%):
  Put TP=30, SL=35:  Net TP=+29.0  Net SL=-37.3  BE = 56.3%  (v17 54% <= miss)
  Put TP=30, SL=25:  Net TP=+29.0  Net SL=-27.3  BE = 48.5%  (clearly profitable)
  Put TP=30, SL=20:  Net TP=+29.0  Net SL=-22.3  BE = 43.5%

Calls remain unchanged (breadth-adaptive). Puts use fixed (non-breadth) TP/SL.
Allocation follows put_tier_cascade_mirror (Variant B): <=15/16-20/21-25 -> 15/12/12%.
"""
from __future__ import annotations
import sys, io, math, random, statistics, bisect
from collections import defaultdict
from datetime import date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.models.core import Score, AlgorithmVersion, MarketBreadth
from database.models.technical import PriceHistory

# Calls - breadth-adaptive (same as monte_carlo.py)
CALL_TP_BASE, CALL_TP_STRESS = 0.30, 0.35
CALL_SL_BASE, CALL_SL_STRESS = -0.35, -0.40
HARD_SELL_LOSS = -0.50
BREADTH_THRESHOLD = 50

SLIP_ENTRY, SLIP_TP, SLIP_SL, SLIP_HARD = -0.010, 0.000, -0.013, -0.005

PREMIUM_MULT = 1.82
DELTA = 0.5
HOLD_DAYS = 15
VOL_LOOKBACK = 60

MAX_POSITIONS = 14
STARTING_CASH = 50_000.0
COLLAPSE_THRESHOLD = 0.20
N_ITER = 300

CALL_ALLOC = {'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.05}
CALL_PRIMARY_THRESH = 75
CALL_OVERFLOW_THRESH = 70

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']

# Put TP/SL sweep matrix
PUT_SWEEP = [
    # (label, put_tp, put_sl)
    ('TP30_SL35 (baseline)', 0.30, -0.35),
    ('TP30_SL30',            0.30, -0.30),
    ('TP30_SL25',            0.30, -0.25),
    ('TP30_SL20',            0.30, -0.20),
    ('TP25_SL25',            0.25, -0.25),
    ('TP25_SL20',            0.25, -0.20),
    ('TP20_SL20',            0.20, -0.20),
    ('TP20_SL15',            0.20, -0.15),
]


def call_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def put_tier(score):
    """Mirror of call cascade around 50."""
    if score <= 15: return 0.15
    if score <= 20: return 0.12
    if score <= 25: return 0.12
    return 0.0


def net_pnl(gross_tp, gross_sl):
    """Return (net_tp, net_sl) after slippage."""
    return gross_tp + SLIP_ENTRY + SLIP_TP, gross_sl + SLIP_ENTRY + SLIP_SL


NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD

# Call config (breadth-adaptive)
NET_CTP_BASE,   NET_CSL_BASE   = net_pnl(CALL_TP_BASE,   CALL_SL_BASE)
NET_CTP_STRESS, NET_CSL_STRESS = net_pnl(CALL_TP_STRESS, CALL_SL_STRESS)
CTP_SIG_BASE   = CALL_TP_BASE   * PREMIUM_MULT / DELTA
CTP_SIG_STRESS = CALL_TP_STRESS * PREMIUM_MULT / DELTA
CSL_SIG_BASE   = abs(CALL_SL_BASE)   * PREMIUM_MULT / DELTA
CSL_SIG_STRESS = abs(CALL_SL_STRESS) * PREMIUM_MULT / DELTA


# ---- Data loading (same shape as monte_carlo_puts_2022) ---------------------

def load_breadth_map(d_start, d_end):
    rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(
            MarketBreadth.date >= d_start - timedelta(days=60),
            MarketBreadth.date <= d_end,
            MarketBreadth.breadth_score.is_null(False),
        ).order_by(MarketBreadth.date).tuples()
    )
    m = {d: float(bs) for d, bs in rows}
    return sorted(m.keys()), m


def breadth_at(sorted_dates, bmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    return bmap[sorted_dates[idx]] if idx >= 0 else None


def is_stressed(sorted_dates, bmap, d):
    b = breadth_at(sorted_dates, bmap, d)
    return b is not None and b <= BREADTH_THRESHOLD


def load_signals_range(version, d_start, d_end, score_filter):
    return list(
        Score.select(Score.symbol, Score.date, Score.overall)
        .where(Score.version == version, Score.date >= d_start, Score.date <= d_end, score_filter)
        .order_by(Score.date, Score.overall.desc())
    )


def load_price_history(sym_ids, d_start, d_end):
    rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.close, PriceHistory.high, PriceHistory.low
        ).where(
            PriceHistory.symbol.in_(sym_ids),
            PriceHistory.date >= d_start - timedelta(days=120),
            PriceHistory.date <= d_end + timedelta(days=30),
        ).order_by(PriceHistory.symbol, PriceHistory.date).tuples()
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


# ---- Outcome precompute -----------------------------------------------------

def compute_call_outcome(sym_bars, signal_date, stressed):
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    try: base_idx = dates.index(signal_date)
    except ValueError: return None
    entry = closes[base_idx]
    if entry <= 0: return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0: return None

    tp_sig = CTP_SIG_STRESS if stressed else CTP_SIG_BASE
    sl_sig = CSL_SIG_STRESS if stressed else CSL_SIG_BASE
    net_tp = NET_CTP_STRESS if stressed else NET_CTP_BASE
    net_sl = NET_CSL_STRESS if stressed else NET_CSL_BASE

    tp_lvl = entry * (1 + tp_sig * vol / 100)
    sl_lvl = entry * (1 - sl_sig * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    kind, exit_bar = 'hard', HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_lvl
        sl_hit = lows[i]  <= sl_lvl
        if tp_hit and sl_hit: kind, exit_bar = 'both', i - base_idx; break
        if tp_hit: kind, exit_bar = 'tp', i - base_idx; break
        if sl_hit: kind, exit_bar = 'sl', i - base_idx; break
    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl)


def compute_put_outcome(sym_bars, signal_date, put_tp, put_sl):
    """Put-side — TP/SL sigma derived from configurable put_tp/put_sl."""
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    try: base_idx = dates.index(signal_date)
    except ValueError: return None
    entry = closes[base_idx]
    if entry <= 0: return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0: return None

    tp_sig = put_tp         * PREMIUM_MULT / DELTA
    sl_sig = abs(put_sl)    * PREMIUM_MULT / DELTA
    net_tp, net_sl = net_pnl(put_tp, put_sl)

    tp_lvl = entry * (1 - tp_sig * vol / 100)  # price must FALL
    sl_lvl = entry * (1 + sl_sig * vol / 100)  # price must RISE

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    kind, exit_bar = 'hard', HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        tp_hit = lows[i]  <= tp_lvl
        sl_hit = highs[i] >= sl_lvl
        if tp_hit and sl_hit: kind, exit_bar = 'both', i - base_idx; break
        if tp_hit: kind, exit_bar = 'tp', i - base_idx; break
        if sl_hit: kind, exit_bar = 'sl', i - base_idx; break
    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl)


def resolve(kind, mode, rng, net_tp, net_sl):
    if kind == 'tp':   return 'tp',   net_tp
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', net_tp
    return ('tp', net_tp) if rng.random() < 0.5 else ('sl', net_sl)


# ---- Portfolio --------------------------------------------------------------

class Position:
    __slots__ = ['sym_id','entry_date','exit_bar','cost','pnl','outcome','side']
    def __init__(self, sym_id, entry_date, exit_bar, cost, pnl, outcome, side):
        self.sym_id=sym_id; self.entry_date=entry_date; self.exit_bar=exit_bar
        self.cost=cost; self.pnl=pnl; self.outcome=outcome; self.side=side


def run_single(trading_days, call_by_date, put_by_date, call_outs, put_outs, mode, rng):
    cash = STARTING_CASH
    positions = []
    peak = STARTING_CASH
    max_dd = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}
    tp_c=sl_c=hard_c=0; tp_p=sl_p=hard_p=0

    for day_idx, today in enumerate(trading_days):
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.cost * (1 + p.pnl)
                if p.side == 'call':
                    if p.outcome=='tp': tp_c+=1
                    elif p.outcome=='sl': sl_c+=1
                    else: hard_c+=1
                else:
                    if p.outcome=='tp': tp_p+=1
                    elif p.outcome=='sl': sl_p+=1
                    else: hard_p+=1
            else:
                keep.append(p)
        positions = keep

        port = cash + sum(p.cost for p in positions)
        if port > peak: peak = port
        dd = (peak - port) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd
        if port <= STARTING_CASH * COLLAPSE_THRESHOLD: break

        open_syms = {p.sym_id for p in positions}

        # Calls first
        calls = [(sid, sc, k) for sid, sc, k in call_by_date.get(today, [])
                 if k in call_outs and sid not in open_syms]
        primary  = [e for e in calls if e[1] >= CALL_PRIMARY_THRESH]
        overflow = [e for e in calls if e[1] <  CALL_PRIMARY_THRESH]
        primary.sort(key=lambda x: (-x[1], rng.random()))
        overflow.sort(key=lambda x: (-x[1], rng.random()))
        for sym_id, score, key in primary + overflow:
            if len(positions) >= MAX_POSITIONS: break
            frac = CALL_ALLOC[call_tier(score)]
            cost = port * frac
            if cost > cash or cost <= 0: continue
            o = call_outs[key]
            oc, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
            cash -= cost
            positions.append(Position(sym_id, today, o['exit_bar'], cost, pnl, oc, 'call'))
            open_syms.add(sym_id)

        # Puts - score ascending (most extreme first)
        if len(positions) < MAX_POSITIONS:
            puts = [(sid, sc, k) for sid, sc, k in put_by_date.get(today, [])
                    if k in put_outs and sid not in open_syms]
            puts.sort(key=lambda x: (x[1], rng.random()))
            for sym_id, score, key in puts:
                if len(positions) >= MAX_POSITIONS: break
                frac = put_tier(score)
                if frac <= 0: continue
                cost = port * frac
                if cost > cash or cost <= 0: continue
                o = put_outs[key]
                oc, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= cost
                positions.append(Position(sym_id, today, o['exit_bar'], cost, pnl, oc, 'put'))
                open_syms.add(sym_id)

    # Close remainder at hard-sell
    for p in positions:
        cash += p.cost * (1 + NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else: hard_p += 1

    portfolio = cash
    final_dd = (peak - portfolio) / peak if peak > 0 else 0
    max_dd = max(max_dd, final_dd)

    ct = tp_c + sl_c + hard_c
    pt = tp_p + sl_p + hard_p
    return dict(
        final=portfolio, max_dd=max_dd,
        call_tp=(tp_c/ct*100) if ct else 0,
        put_tp=(tp_p/pt*100) if pt else 0,
        call_trades=ct, put_trades=pt,
    )


# ---- Runner -----------------------------------------------------------------

def run_window(label, d_start, d_end):
    print('=' * 110)
    print(f"Put TP/SL Sweep - {label} Window  ({d_start} -> {d_end})")
    print('=' * 110)
    print(f"Calls: breadth-adaptive TP={CALL_TP_BASE:+.0%}/{CALL_TP_STRESS:+.0%} SL={CALL_SL_BASE:+.0%}/{CALL_SL_STRESS:+.0%}")
    print(f"Put allocation cascade: <=15/16-20/21-25 -> 15/12/12%  (same as Variant B)")
    print(f"MaxPos={MAX_POSITIONS}  $50k start  {N_ITER} iter/mode")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm: {version.git_commit}")

    call_sigs = load_signals_range(version, d_start, d_end, Score.overall >= CALL_OVERFLOW_THRESH)
    put_sigs  = load_signals_range(version, d_start, d_end, Score.overall <= 25)
    print(f"\nCall signals: {len(call_sigs)}  |  Put signals: {len(put_sigs)}")

    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph = load_price_history(sym_ids, d_start, d_end)
    breadth_dates, breadth_map = load_breadth_map(d_start, d_end)

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    call_by_date = defaultdict(list)
    for s in call_sigs:
        call_by_date[s.date].append((s.symbol_id, s.overall, (s.symbol_id, s.date)))
    put_by_date = defaultdict(list)
    for s in put_sigs:
        put_by_date[s.date].append((s.symbol_id, s.overall, (s.symbol_id, s.date)))

    print("Precomputing call outcomes...", end=' ', flush=True)
    call_outs = {}
    for s in call_sigs:
        bars = ph.get(s.symbol_id)
        if not bars: continue
        stressed = is_stressed(breadth_dates, breadth_map, s.date)
        r = compute_call_outcome(bars, s.date, stressed)
        if r: call_outs[(s.symbol_id, s.date)] = r
    print(f"{len(call_outs)}")

    # For each (put_tp, put_sl) combo, precompute put outcomes and run 3 modes
    print("\n" + '=' * 118)
    print(f"{'Put variant':<22} {'Mode':<13}  {'PutTP%':>7}  {'PTrd':>5}  {'CallTP%':>7}  {'CTrd':>5}  "
          f"{'MeanRet':>13}  {'MedRet':>13}  {'WorstDD':>8}  {'P(col)':>7}")
    print('-' * 118)

    all_results = {}
    for vlabel, put_tp, put_sl in PUT_SWEEP:
        net_tp, net_sl = net_pnl(put_tp, put_sl)
        be = abs(net_sl) / (net_tp + abs(net_sl)) * 100

        put_outs = {}
        for s in put_sigs:
            bars = ph.get(s.symbol_id)
            if not bars: continue
            r = compute_put_outcome(bars, s.date, put_tp, put_sl)
            if r: put_outs[(s.symbol_id, s.date)] = r

        # Raw TP rate for this put variant
        raw_tp = sum(1 for o in put_outs.values() if o['kind'] == 'tp')
        raw_sl = sum(1 for o in put_outs.values() if o['kind'] == 'sl')
        raw_both = sum(1 for o in put_outs.values() if o['kind'] == 'both')
        total = len(put_outs)
        raw_tp_pct = raw_tp / total * 100 if total else 0

        print(f"\n  [{vlabel}] BE={be:.1f}%  RawTP={raw_tp_pct:.1f}%  SL={raw_sl/total*100:.1f}%  "
              f"Both={raw_both/total*100:.1f}%  N={total}")

        all_results[vlabel] = {}
        for mode in COLLISION_MODES:
            finals=[]; dds=[]; coll=0; ctp=[]; ptp=[]; ctrd=[]; ptrd=[]
            for it in range(N_ITER):
                rng = random.Random(1000 * hash(vlabel + mode) + it)
                r = run_single(trading_days, call_by_date, put_by_date,
                               call_outs, put_outs, mode, rng)
                finals.append(r['final']); dds.append(r['max_dd'])
                ctp.append(r['call_tp']); ptp.append(r['put_tp'])
                ctrd.append(r['call_trades']); ptrd.append(r['put_trades'])
                if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                    coll += 1

            mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
            med_ret  = (statistics.median(finals) / STARTING_CASH - 1) * 100
            worst_dd = max(dds) * 100
            p_col    = coll / N_ITER * 100
            all_results[vlabel][mode] = dict(
                mean_ret=mean_ret, med_ret=med_ret,
                worst_dd=worst_dd, p_col=p_col,
                call_tp=statistics.mean(ctp), put_tp=statistics.mean(ptp),
                call_trades=statistics.mean(ctrd), put_trades=statistics.mean(ptrd),
            )
            r = all_results[vlabel][mode]
            print(f"  {vlabel:<20} {mode:<13}  {r['put_tp']:>6.1f}%  {r['put_trades']:>5.1f}  "
                  f"{r['call_tp']:>6.1f}%  {r['call_trades']:>5.1f}  "
                  f"{r['mean_ret']:>+12.1f}%  {r['med_ret']:>+12.1f}%  "
                  f"{r['worst_dd']:>7.1f}%  {r['p_col']:>6.1f}%")

    # Summary
    print('\n' + '=' * 95)
    print(f"SUMMARY {label} - Realistic mode")
    print('=' * 95)
    baseline = all_results['TP30_SL35 (baseline)']['realistic']['mean_ret']
    print(f"{'Variant':<22}  {'MeanRet':>13}  {'vs baseline':>13}  {'WorstDD':>9}  {'PutTP%':>7}")
    print('-' * 75)
    for vlabel, _, _ in PUT_SWEEP:
        r = all_results[vlabel]['realistic']
        delta = r['mean_ret'] - baseline
        print(f"{vlabel:<22}  {r['mean_ret']:>+12.1f}%  {delta:>+12.1f}pp  "
              f"{r['worst_dd']:>8.1f}%  {r['put_tp']:>6.1f}%")

    return all_results


WINDOWS = [
    ('2022', date(2022, 1, 1), date(2022, 12, 31)),
    ('2025', date(2025, 1, 1), date(2025, 12, 31)),
]


if __name__ == '__main__':
    for label, d_start, d_end in WINDOWS:
        run_window(label, d_start, d_end)
        print("\n\n")
