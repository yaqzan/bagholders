"""
Monte Carlo — Asymmetric Weekly + TP30_SL20 Puts: Full Validation
==================================================================

Extends the 2022+2025 sweep to all calendar years (2022-2025) and a
22-now continuous compounding window. Three variants:

  A_calls_only       — sim scores, calls only (clean control)
  B_asym_mirror      — sim scores, puts with call TP/SL (TP30_SL35)
  C_asym_TP30_SL20   — sim scores, puts with tighter SL (the candidate)

Reuses cached simulated scores from monte_carlo_puts_asymmetric_sweep.py
(pickle at _sim_scores_asym_put1.50x.pkl). If cache missing, regenerates
(takes ~15 min).

Everything else matches monte_carlo_puts_asymmetric_sweep:
  Calls: breadth-adaptive TP=30/35, SL=35/40
  Put cascade: <=15/16-20/21-25 -> 15/12/12%
  MaxPos=14, $50k start, 200 iter/mode, 3 collision modes.
"""
from __future__ import annotations
import sys, io, math, random, statistics, bisect, os, pickle
from collections import defaultdict
from datetime import date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import database.utils.scoring as scoring_mod
from database.models.core import MarketBreadth
from database.models.technical import PriceHistory

# ---- Asymmetric weekly patch -----------------------------------------------
_ORIG_WEEKLY_ADJ = scoring_mod.calculate_weekly_adjustment
PUT_WEEKLY_SCALE = 1.5
CALL_WEEKLY_SCALE = 1.0


def asymmetric_weekly_adj(*args, **kwargs):
    total, detail = _ORIG_WEEKLY_ADJ(*args, **kwargs)
    if total is None:
        return total, detail
    total *= PUT_WEEKLY_SCALE if total < 0 else CALL_WEEKLY_SCALE
    if detail is not None:
        detail = dict(detail)
        detail['w_adj'] = round(total, 1)
    return total, detail


# ---- MC constants ----------------------------------------------------------
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
N_ITER = 200

CALL_ALLOC = {'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.05}
CALL_PRIMARY_THRESH = 75
CALL_OVERFLOW_THRESH = 70

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']

# Variants: (label, put_fn_or_None, put_tp, put_sl)
VARIANTS = [
    ('A_calls_only',      None,                  None,  None),
    ('B_asym_mirror',     'cascade',             0.30,  -0.35),
    ('C_asym_TP30_SL20',  'cascade',             0.30,  -0.20),
]

WINDOWS = [
    ('2022',    date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',    date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',    date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',    date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now',  date(2022, 1, 1),  date.today()),
]

CACHE_PATH = f"_sim_scores_asym_put{PUT_WEEKLY_SCALE:.2f}x.pkl"


def call_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def put_tier_cascade(score):
    if score <= 15: return 0.15
    if score <= 20: return 0.12
    if score <= 25: return 0.12
    return 0.0


def net_pnl(gross_tp, gross_sl):
    return gross_tp + SLIP_ENTRY + SLIP_TP, gross_sl + SLIP_ENTRY + SLIP_SL


NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD
NET_CTP_BASE, NET_CSL_BASE = net_pnl(CALL_TP_BASE, CALL_SL_BASE)
NET_CTP_STRESS, NET_CSL_STRESS = net_pnl(CALL_TP_STRESS, CALL_SL_STRESS)
CTP_SIG_BASE = CALL_TP_BASE * PREMIUM_MULT / DELTA
CTP_SIG_STRESS = CALL_TP_STRESS * PREMIUM_MULT / DELTA
CSL_SIG_BASE = abs(CALL_SL_BASE) * PREMIUM_MULT / DELTA
CSL_SIG_STRESS = abs(CALL_SL_STRESS) * PREMIUM_MULT / DELTA


# ---- Data helpers ----------------------------------------------------------

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


def is_stressed(sorted_dates, bmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0: return False
    b = bmap[sorted_dates[idx]]
    return b <= BREADTH_THRESHOLD


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
    if base_idx < lookback: return None
    rets = []
    for j in range(base_idx - lookback + 1, base_idx + 1):
        prev = closes[j - 1]
        if prev > 0:
            rets.append((closes[j] - prev) / prev)
    if len(rets) < lookback // 2: return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


# ---- Outcome precompute ----------------------------------------------------

def compute_call_outcome(sym_bars, signal_date, stressed):
    dates = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs = [b[2] for b in sym_bars]
    lows = [b[3] for b in sym_bars]
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
        sl_hit = lows[i] <= sl_lvl
        if tp_hit and sl_hit: kind, exit_bar = 'both', i - base_idx; break
        if tp_hit: kind, exit_bar = 'tp', i - base_idx; break
        if sl_hit: kind, exit_bar = 'sl', i - base_idx; break
    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl)


def compute_put_outcome(sym_bars, signal_date, put_tp, put_sl):
    dates = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs = [b[2] for b in sym_bars]
    lows = [b[3] for b in sym_bars]
    try: base_idx = dates.index(signal_date)
    except ValueError: return None
    entry = closes[base_idx]
    if entry <= 0: return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0: return None
    tp_sig = put_tp * PREMIUM_MULT / DELTA
    sl_sig = abs(put_sl) * PREMIUM_MULT / DELTA
    net_tp, net_sl = net_pnl(put_tp, put_sl)
    tp_lvl = entry * (1 - tp_sig * vol / 100)
    sl_lvl = entry * (1 + sl_sig * vol / 100)
    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    kind, exit_bar = 'hard', HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        tp_hit = lows[i] <= tp_lvl
        sl_hit = highs[i] >= sl_lvl
        if tp_hit and sl_hit: kind, exit_bar = 'both', i - base_idx; break
        if tp_hit: kind, exit_bar = 'tp', i - base_idx; break
        if sl_hit: kind, exit_bar = 'sl', i - base_idx; break
    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl)


def resolve(kind, mode, rng, net_tp, net_sl):
    if kind == 'tp': return 'tp', net_tp
    if kind == 'sl': return 'sl', net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic': return 'tp', net_tp
    return ('tp', net_tp) if rng.random() < 0.5 else ('sl', net_sl)


# ---- Portfolio sim ---------------------------------------------------------

class Position:
    __slots__ = ['sym_id','entry_date','exit_bar','cost','pnl','outcome','side']
    def __init__(self, sym_id, entry_date, exit_bar, cost, pnl, outcome, side):
        self.sym_id=sym_id; self.entry_date=entry_date; self.exit_bar=exit_bar
        self.cost=cost; self.pnl=pnl; self.outcome=outcome; self.side=side


def run_single(trading_days, call_by_date, put_by_date, call_outs, put_outs, put_fn, mode, rng):
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

        calls = [(sid, sc, k) for sid, sc, k in call_by_date.get(today, [])
                 if k in call_outs and sid not in open_syms]
        primary = [e for e in calls if e[1] >= CALL_PRIMARY_THRESH]
        overflow = [e for e in calls if e[1] < CALL_PRIMARY_THRESH]
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

        if put_fn is not None and len(positions) < MAX_POSITIONS:
            puts = [(sid, sc, k) for sid, sc, k in put_by_date.get(today, [])
                    if k in put_outs and sid not in open_syms]
            puts.sort(key=lambda x: (x[1], rng.random()))
            for sym_id, score, key in puts:
                if len(positions) >= MAX_POSITIONS: break
                frac = put_fn(score)
                if frac <= 0: continue
                cost = port * frac
                if cost > cash or cost <= 0: continue
                o = put_outs[key]
                oc, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= cost
                positions.append(Position(sym_id, today, o['exit_bar'], cost, pnl, oc, 'put'))
                open_syms.add(sym_id)

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


# ---- Main ------------------------------------------------------------------

def load_or_simulate():
    if os.path.exists(CACHE_PATH):
        print(f"Loading cached simulated scores from {CACHE_PATH}")
        with open(CACHE_PATH, 'rb') as f:
            return pickle.load(f)

    scoring_mod.calculate_weekly_adjustment = asymmetric_weekly_adj
    earliest = min(w[1] for w in WINDOWS)
    lookback = (date.today() - earliest).days + 30
    print(f"Simulator lookback: {lookback} days")
    from simulator import ScoreSimulator
    sim = ScoreSimulator(symbols=None, lookback_days=lookback, scoring_fn=None)
    sim_scores = sim.simulate()
    scoring_mod.calculate_weekly_adjustment = _ORIG_WEEKLY_ADJ
    with open(CACHE_PATH, 'wb') as f:
        pickle.dump(sim_scores, f)
    print(f"Cached to {CACHE_PATH}")
    return sim_scores


def run_window(label, d_start, d_end, all_sig_records):
    print("\n" + "=" * 118)
    print(f"Window {label}  ({d_start} -> {d_end})")
    print("=" * 118)

    call_sigs = [(sid, d, sc) for sid, d, sc in all_sig_records
                 if d_start <= d <= d_end and sc >= CALL_OVERFLOW_THRESH]
    put_sigs = [(sid, d, sc) for sid, d, sc in all_sig_records
                if d_start <= d <= d_end and sc <= 25]
    print(f"Signals: {len(call_sigs)} calls | {len(put_sigs)} puts")

    sym_ids = list({sid for sid, _, _ in call_sigs} | {sid for sid, _, _ in put_sigs})
    ph = load_price_history(sym_ids, d_start, d_end)
    breadth_dates, breadth_map = load_breadth_map(d_start, d_end)

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    call_by_date = defaultdict(list)
    for sid, d, sc in call_sigs:
        call_by_date[d].append((sid, sc, (sid, d)))
    put_by_date = defaultdict(list)
    for sid, d, sc in put_sigs:
        put_by_date[d].append((sid, sc, (sid, d)))

    print("Precomputing call outcomes... ", end=' ', flush=True)
    call_outs = {}
    for sid, d, sc in call_sigs:
        bars = ph.get(sid)
        if not bars: continue
        stressed = is_stressed(breadth_dates, breadth_map, d)
        r = compute_call_outcome(bars, d, stressed)
        if r: call_outs[(sid, d)] = r
    print(len(call_outs))

    # Precompute put outcomes per (put_tp, put_sl) combo used
    put_outs_by_cfg = {}
    for _, putfn_name, put_tp, put_sl in VARIANTS:
        if putfn_name is None: continue
        key = (put_tp, put_sl)
        if key in put_outs_by_cfg: continue
        outs = {}
        for sid, d, sc in put_sigs:
            bars = ph.get(sid)
            if not bars: continue
            r = compute_put_outcome(bars, d, put_tp, put_sl)
            if r: outs[(sid, d)] = r
        put_outs_by_cfg[key] = outs
        raw_tp = sum(1 for o in outs.values() if o['kind'] == 'tp')
        print(f"  Put outcomes TP={put_tp:+.0%} SL={put_sl:+.0%}: N={len(outs)}  RawTP={raw_tp/len(outs)*100:.1f}%")

    print("\n" + "-" * 118)
    print(f"{'Variant':<22} {'Mode':<13}  {'CTP%':>5}  {'PTP%':>5}  {'CTrd':>5}  {'PTrd':>5}  "
          f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  {'P(col)':>7}")
    print('-' * 118)

    results = {}
    for vlabel, putfn_name, put_tp, put_sl in VARIANTS:
        put_outs = {} if putfn_name is None else put_outs_by_cfg[(put_tp, put_sl)]
        put_fn = None if putfn_name is None else put_tier_cascade
        results[vlabel] = {}
        for mode in COLLISION_MODES:
            finals=[]; dds=[]; coll=0; ctp=[]; ptp=[]; ctrd=[]; ptrd=[]
            for it in range(N_ITER):
                rng = random.Random(1000 * hash(label + vlabel + mode) + it)
                r = run_single(trading_days, call_by_date, put_by_date,
                               call_outs, put_outs, put_fn, mode, rng)
                finals.append(r['final']); dds.append(r['max_dd'])
                ctp.append(r['call_tp']); ptp.append(r['put_tp'])
                ctrd.append(r['call_trades']); ptrd.append(r['put_trades'])
                if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD: coll += 1

            mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
            med_ret = (statistics.median(finals) / STARTING_CASH - 1) * 100
            mean_dd = statistics.mean(dds) * 100
            worst_dd = max(dds) * 100
            p_col = coll / N_ITER * 100
            results[vlabel][mode] = dict(
                mean_ret=mean_ret, med_ret=med_ret,
                worst_dd=worst_dd, mean_dd=mean_dd, p_col=p_col,
                call_tp=statistics.mean(ctp), put_tp=statistics.mean(ptp),
                call_trades=statistics.mean(ctrd), put_trades=statistics.mean(ptrd),
            )
            r = results[vlabel][mode]
            print(f"{vlabel:<22} {mode:<13}  {r['call_tp']:>4.1f}%  {r['put_tp']:>4.1f}%  "
                  f"{r['call_trades']:>5.1f}  {r['put_trades']:>5.1f}  "
                  f"{r['mean_ret']:>+13.1f}%  {r['med_ret']:>+13.1f}%  "
                  f"{r['worst_dd']:>7.1f}%  {r['mean_dd']:>6.1f}%  {r['p_col']:>6.1f}%")
        print()

    return results


def main():
    print("=" * 118)
    print(f"MC - Asymmetric weekly (put_scale={PUT_WEEKLY_SCALE:.2f}x) + TP30_SL20 puts: FULL VALIDATION")
    print("=" * 118)

    sim_scores = load_or_simulate()
    print(f"Loaded {len(sim_scores)} simulated scores\n")

    all_sig_records = [(sym, d, overall) for (sym, d), overall in sim_scores.items()]

    summary = {}
    for label, d_start, d_end in WINDOWS:
        summary[label] = run_window(label, d_start, d_end, all_sig_records)

    # Cross-window summary
    print("\n\n" + "=" * 130)
    print("CROSS-WINDOW SUMMARY — Realistic mode (MeanRet / WorstDD per window)")
    print("=" * 130)
    header = f"{'Variant':<22}"
    for w, _, _ in WINDOWS:
        header += f"  {w:>25}"
    print(header)
    print('-' * 130)
    for vlabel, _, _, _ in VARIANTS:
        row = f"{vlabel:<22}"
        for w, _, _ in WINDOWS:
            r = summary[w][vlabel]['realistic']
            cell = f"{r['mean_ret']:+10.1f}% DD{r['worst_dd']:5.1f}%"
            row += f"  {cell:>25}"
        print(row)

    # Deltas vs calls_only
    print("\n" + "=" * 130)
    print("Delta vs calls_only (Realistic) — shows pure puts contribution")
    print("=" * 130)
    header = f"{'Variant':<22}"
    for w, _, _ in WINDOWS:
        header += f"  {w:>18}"
    print(header)
    print('-' * 130)
    for vlabel, _, _, _ in VARIANTS:
        if vlabel == 'A_calls_only': continue
        row = f"{vlabel:<22}"
        for w, _, _ in WINDOWS:
            a = summary[w]['A_calls_only']['realistic']['mean_ret']
            v = summary[w][vlabel]['realistic']['mean_ret']
            row += f"  {v - a:+17.1f}pp"
        print(row)

    # Safety floor check (Conservative + Realistic WorstDD < 80%)
    print("\n" + "=" * 130)
    print("Safety floor — WorstDD < 80% in both Realistic and Conservative?")
    print("=" * 130)
    for vlabel, _, _, _ in VARIANTS:
        fails = []
        for w, _, _ in WINDOWS:
            for mode in ('realistic', 'conservative'):
                dd = summary[w][vlabel][mode]['worst_dd']
                if dd > 80.0:
                    fails.append(f"{w}/{mode}={dd:.1f}%")
        verdict = "PASS" if not fails else "FAIL: " + ", ".join(fails)
        print(f"  {vlabel:<22}  {verdict}")


if __name__ == '__main__':
    main()
