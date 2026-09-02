"""
Monte Carlo — Regime-Conditioned Take Profit Sweep
===================================================
Tests whether conditioning the take-profit level on market sentiment
(VIX, breadth, regime composite) outperforms the locked-in fixed TP=+30%.

All other optimal-strategy parameters are held constant:
  MaxPos=14, cascade 15/12/12/5, breadth-adaptive SL (h35→40 via brd_inv@50),
  hard sell=−50% @ day 15, per-exit slippage, 3 collision modes, 500 iters.

Rule shape (13 total):
  base TP  = 30%   (locked default — applied when regime signal is below threshold)
  stress TP ∈ {20%, 25%, 35%, 40%}  (swapped in when regime signal ≥ threshold)

Three signal sources (using the winning thresholds from vix/breadth decomp):
  composite ≥ 50     (production regime_composite)
  brd_inv   ≥ 50     (100 − breadth_score; winner for SL switching)
  vix_score ≥ 70     (best VIX-family threshold)

Windows (starting at 2022 — worst-case bear-entry compounding test):
  2022→now   (primary, continuous)
  2022       (isolated bear year)
  2023       (recovery)
  2024       (bull)
  2025       (choppy)

Question answered: does a regime-aware TP beat a fixed +30% TP from the
worst-case 2022 entry point, and if so, which signal source does the work?

Usage: python monte_carlo_regime_tp_sweep.py
"""

import sys
import io
import math
import bisect
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.models.core import Score, AlgorithmVersion, MarketRegime, MarketBreadth
from database.models.technical import PriceHistory

# ---- Strategy constants (locked from canonical MC) --------------------------
STARTING_CASH      = 50_000.0
N_ITER             = 500
VOL_LOOKBACK       = 60
HOLD_DAYS          = 15          # trading bars
PREMIUM_MULT       = 1.82        # ATM 30-DTE premium ~ 1.82 * sigma_daily
DELTA              = 0.5

HARD_SELL_LOSS     = -0.50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000   # limit sell at TP — no transaction costs
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD   # -0.515

# Locked TP base + breadth-adaptive SL bands
TP_BASE    = 0.30    # locked base TP
SL_BASE    = 0.35    # SL when breadth is healthy (brd_inv < 50)
SL_STRESS  = 0.40    # SL when breadth weak   (brd_inv ≥ 50)
SL_THRESHOLD = 50    # brd_inv threshold for the SL swap

TIER_ALLOC = {
    'top':      0.15,   # 85+
    'mid':      0.12,   # 80-84
    'low':      0.12,   # 75-79
    'overflow': 0.05,   # 70-74
}
MAX_POSITIONS      = 14
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
COLLAPSE_THRESHOLD = 0.20

WINDOWS = [
    ('2022→now', date(2022, 1, 1),  date(2026, 4, 16)),   # PRIMARY: worst-case entry
    ('2022',     date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',     date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',     date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',     date(2025, 1, 1),  date(2025, 12, 31)),
]

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']
SNAPSHOT_YEARS = [2022, 2023, 2024, 2025, 2026]


# ==============================================================================
# Rule definitions
# ==============================================================================
# Each rule pairs a stress-detection signal with a stressed-regime TP %.
# Non-stressed regime ALWAYS uses TP_BASE = 0.30.
# SL is always breadth-adaptive h35→40 (locked, not varied).

RULES = [
    # ── Fixed baseline (no TP switching) ──
    {'label': 'fixed_TP30',   'tp_source': None,        'tp_threshold': None,
     'tp_stress': 0.30, 'desc': 'Fixed TP=30% (baseline)'},

    # ── Composite-driven TP swaps (threshold @ 50) ──
    {'label': 'comp_TP30/20', 'tp_source': 'composite', 'tp_threshold': 50,
     'tp_stress': 0.20, 'desc': 'comp≥50 → TP=20%'},
    {'label': 'comp_TP30/25', 'tp_source': 'composite', 'tp_threshold': 50,
     'tp_stress': 0.25, 'desc': 'comp≥50 → TP=25%'},
    {'label': 'comp_TP30/35', 'tp_source': 'composite', 'tp_threshold': 50,
     'tp_stress': 0.35, 'desc': 'comp≥50 → TP=35%'},
    {'label': 'comp_TP30/40', 'tp_source': 'composite', 'tp_threshold': 50,
     'tp_stress': 0.40, 'desc': 'comp≥50 → TP=40%'},

    # ── Breadth-driven TP swaps (threshold @ 50, using brd_inv) ──
    {'label': 'brd_TP30/20',  'tp_source': 'brd_inv',   'tp_threshold': 50,
     'tp_stress': 0.20, 'desc': 'brd_inv≥50 → TP=20%'},
    {'label': 'brd_TP30/25',  'tp_source': 'brd_inv',   'tp_threshold': 50,
     'tp_stress': 0.25, 'desc': 'brd_inv≥50 → TP=25%'},
    {'label': 'brd_TP30/35',  'tp_source': 'brd_inv',   'tp_threshold': 50,
     'tp_stress': 0.35, 'desc': 'brd_inv≥50 → TP=35%'},
    {'label': 'brd_TP30/40',  'tp_source': 'brd_inv',   'tp_threshold': 50,
     'tp_stress': 0.40, 'desc': 'brd_inv≥50 → TP=40%'},

    # ── VIX-driven TP swaps (vix_score threshold @ 70) ──
    {'label': 'vix_TP30/20',  'tp_source': 'vix_score', 'tp_threshold': 70,
     'tp_stress': 0.20, 'desc': 'vix_score≥70 → TP=20%'},
    {'label': 'vix_TP30/25',  'tp_source': 'vix_score', 'tp_threshold': 70,
     'tp_stress': 0.25, 'desc': 'vix_score≥70 → TP=25%'},
    {'label': 'vix_TP30/35',  'tp_source': 'vix_score', 'tp_threshold': 70,
     'tp_stress': 0.35, 'desc': 'vix_score≥70 → TP=35%'},
    {'label': 'vix_TP30/40',  'tp_source': 'vix_score', 'tp_threshold': 70,
     'tp_stress': 0.40, 'desc': 'vix_score≥70 → TP=40%'},
]


def score_to_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def tp_pct_to_sigma(tp_pct):
    return tp_pct * PREMIUM_MULT / DELTA


def sl_pct_to_sigma(sl_pct):
    return sl_pct * PREMIUM_MULT / DELTA


def net_tp_from_pct(tp_pct):
    return tp_pct + SLIP_ENTRY + SLIP_TP


def net_sl_from_pct(sl_pct):
    return -sl_pct + SLIP_ENTRY + SLIP_SL


# ==============================================================================
# Signal-map loaders
# ==============================================================================

def load_regime_raw(d_start, d_end):
    rows = list(
        MarketRegime.select(
            MarketRegime.date, MarketRegime.regime_composite,
            MarketRegime.vix_score, MarketRegime.vix_close,
        )
        .where(
            MarketRegime.date >= d_start - timedelta(days=60),
            MarketRegime.date <= d_end,
        )
        .order_by(MarketRegime.date)
        .tuples()
    )
    data = {}
    for d, comp, vscore, vclose in rows:
        data[d] = {
            'composite': float(comp)   if comp   is not None else None,
            'vix_score': float(vscore) if vscore is not None else None,
            'vix_close': float(vclose) if vclose is not None else None,
        }
    return data


def load_breadth_raw(d_start, d_end):
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
    return {d: float(bs) for d, bs in rows}


def build_signal_maps(regime_raw, breadth_raw):
    maps = {
        'composite': {d: v['composite'] for d, v in regime_raw.items() if v['composite'] is not None},
        'vix_score': {d: v['vix_score'] for d, v in regime_raw.items() if v['vix_score'] is not None},
        'vix_close': {d: v['vix_close'] for d, v in regime_raw.items() if v['vix_close'] is not None},
        'brd_inv':   {d: 100.0 - breadth_raw[d] for d in breadth_raw},
    }
    return maps


def get_signal_for_date(signal_map, sorted_dates, d):
    """Most-recent-available lookup (signal published on-or-before d)."""
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx >= 0:
        return signal_map[sorted_dates[idx]]
    return None


# ---- Price / bar data (shared with decomp script) ---------------------------

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


def precompute_bar_data(signals, ph):
    """Per-signal sigma-normalized bar tuples (high_sigma, low_sigma)."""
    result = {}
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

        premium_pct = PREMIUM_MULT * vol / 100
        end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
        if end_idx <= base_idx + 1:
            continue

        sigma_frac = vol / 100
        bars = []
        for i in range(base_idx + 1, end_idx):
            high_sigma = (highs[i] / entry - 1) / sigma_frac
            low_sigma  = (lows[i]  / entry - 1) / sigma_frac
            bars.append((high_sigma, low_sigma))

        result[(sig.symbol_id, sig.date)] = {
            'vol': vol, 'entry': entry, 'premium_pct': premium_pct, 'bars': bars,
        }
    return result


def evaluate_outcome(bars, tp_sigma, sl_sigma):
    for i, (high_s, low_s) in enumerate(bars):
        tp_hit = high_s >= tp_sigma
        sl_hit = low_s  <= -sl_sigma
        if tp_hit and sl_hit:
            return 'both', i + 1
        if tp_hit:
            return 'tp', i + 1
        if sl_hit:
            return 'sl', i + 1
    return 'hard', HOLD_DAYS


def precompute_rule_outcomes(bar_data, signal_maps, rule):
    """
    For each signal, determine:
      - per-trade TP pct (rule-driven via tp_source/tp_threshold, else TP_BASE)
      - per-trade SL pct (always breadth-driven: brd_inv≥50 → SL_STRESS else SL_BASE)
    Then evaluate outcome with those σ barriers.
    """
    outcomes = {}
    tp_pcts  = []
    sl_pcts  = []
    tp_stress_count = sl_stress_count = 0

    brd_map = signal_maps['brd_inv']
    brd_dates = sorted(brd_map.keys())

    tp_map = None
    tp_dates = None
    if rule['tp_source'] is not None:
        tp_map = signal_maps[rule['tp_source']]
        tp_dates = sorted(tp_map.keys())

    tp_threshold = rule['tp_threshold']
    tp_stress    = rule['tp_stress']

    for key, bd in bar_data.items():
        _, sig_date = key

        # ---- TP selection -------------------------------------------------
        if tp_map is None:
            tp_pct = TP_BASE
        else:
            sig_val = get_signal_for_date(tp_map, tp_dates, sig_date)
            if sig_val is not None and sig_val >= tp_threshold:
                tp_pct = tp_stress
                tp_stress_count += 1
            else:
                tp_pct = TP_BASE

        # ---- SL selection (always breadth-adaptive) -----------------------
        brd_val = get_signal_for_date(brd_map, brd_dates, sig_date)
        if brd_val is not None and brd_val >= SL_THRESHOLD:
            sl_pct = SL_STRESS
            sl_stress_count += 1
        else:
            sl_pct = SL_BASE

        tp_sigma = tp_pct_to_sigma(tp_pct)
        sl_sigma = sl_pct_to_sigma(sl_pct)
        kind, exit_bar = evaluate_outcome(bd['bars'], tp_sigma, sl_sigma)
        outcomes[key] = {
            'kind': kind, 'exit_bar': exit_bar,
            'premium_pct': bd['premium_pct'],
            'net_tp': net_tp_from_pct(tp_pct),
            'net_sl': net_sl_from_pct(sl_pct),
        }
        tp_pcts.append(tp_pct)
        sl_pcts.append(sl_pct)

    n = len(outcomes) or 1
    return outcomes, dict(
        avg_tp       = sum(tp_pcts) / n * 100,
        avg_sl       = sum(sl_pcts) / n * 100,
        tp_stress_frac = tp_stress_count / n * 100,
        sl_stress_frac = sl_stress_count / n * 100,
    )


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


def resolve(kind, mode, rng, net_tp, net_sl):
    if kind == 'tp':   return 'tp',   net_tp
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', net_tp
    return ('tp', net_tp) if rng.random() < 0.5 else ('sl', net_sl)


def run_single_sim(trading_days, signals_by_date, outcomes, mode, rng):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}
    tp_c = sl_c = hard_c = 0

    year_snapshots = {}
    prev_day = None
    prev_portfolio_value = STARTING_CASH

    for day_idx, today in enumerate(trading_days):
        if prev_day is not None and today.year != prev_day.year:
            year_snapshots[prev_day.year] = prev_portfolio_value
        prev_day = today

        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.premium_cost * (1 + p.option_pnl)
                if   p.outcome == 'tp':  tp_c   += 1
                elif p.outcome == 'sl':  sl_c   += 1
                else:                    hard_c += 1
            else:
                keep.append(p)
        positions = keep

        portfolio_value = cash + sum(p.premium_cost for p in positions)
        prev_portfolio_value = portfolio_value

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
        eligible = [(s, sc, k) for s, sc, k in day_signals
                    if k in outcomes and s not in open_syms]
        if not eligible:
            continue

        primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD]
        overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD]
        primary.sort(key=lambda x: (-x[1], rng.random()))
        overflow.sort(key=lambda x: (-x[1], rng.random()))

        for sym_id, score, key in primary + overflow:
            if len(positions) >= MAX_POSITIONS:
                break
            tier         = score_to_tier(score)
            alloc_frac   = TIER_ALLOC[tier]
            premium_cost = portfolio_value * alloc_frac
            if premium_cost > cash or premium_cost <= 0:
                continue
            o = outcomes[key]
            outcome, pnl = resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
            cash -= premium_cost
            positions.append(Position(sym_id, today, o['exit_bar'],
                                      premium_cost, pnl, outcome))

    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        hard_c += 1
    portfolio_value = cash

    if prev_day is not None:
        year_snapshots[prev_day.year] = portfolio_value

    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    total    = tp_c + sl_c + hard_c or 1
    return dict(
        final      = portfolio_value,
        max_dd     = max_dd,
        tp_rate    = tp_c   / total * 100,
        sl_rate    = sl_c   / total * 100,
        hard_rate  = hard_c / total * 100,
        trades     = total,
        year_snapshots = year_snapshots,
    )


# ---- Window runner ----------------------------------------------------------

def run_window(label, d_start, d_end, version, signal_maps, is_primary=False):
    print(f"\n{'='*160}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})"
          + ("  *** WORST-CASE ENTRY ***" if is_primary else ""))
    print('='*160)

    signals = load_signals(version, d_start, d_end)
    primary_n  = sum(1 for s in signals if s.overall >= PRIMARY_THRESHOLD)
    print(f"Signals: {len(signals):,} (75+={primary_n:,}, 70-74={len(signals)-primary_n:,})")

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

    print("Precomputing bar data...", end=' ', flush=True)
    bar_data = precompute_bar_data(signals, ph)
    print(f"done ({len(bar_data):,} valid trades)")

    # Signal source stats for this window
    print("\nSignal source statistics (dates in window):")
    for src_name in ['composite', 'vix_score', 'brd_inv']:
        m = signal_maps.get(src_name, {})
        vals = [v for d, v in m.items() if d_start <= d <= d_end and v is not None]
        if vals:
            sv = sorted(vals)
            mn = sum(vals) / len(vals)
            p10 = sv[int(len(sv) * 0.10)]
            p50 = sv[int(len(sv) * 0.50)]
            p90 = sv[int(len(sv) * 0.90)]
            over = {}
            for th in [50, 60, 70]:
                over[th] = sum(1 for v in vals if v >= th) / len(vals) * 100
            print(f"  {src_name:>10}: mean={mn:5.1f}  p10={p10:5.1f}  p50={p50:5.1f}  "
                  f"p90={p90:5.1f}  ≥50:{over[50]:4.0f}%  ≥60:{over[60]:4.0f}%  "
                  f"≥70:{over[70]:4.0f}%  (N={len(vals)})")

    window_results = {}

    print(f"\n{'#':>3}  {'Rule':<16}  {'Description':<26}  "
          f"{'AvgTP':>6}  {'TPstr%':>6}  {'AvgSL':>6}  {'SLstr%':>6}  "
          f"{'— Realistic —':^34}  {'— Conservative —':^20}")
    print(f"{'':3}  {'':16}  {'':26}  {'':6}  {'':6}  {'':6}  {'':6}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}  {'TP%':>5}  {'SL%':>5}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}")
    print('─' * 160)

    for rule_idx, rule in enumerate(RULES):
        outcomes, stats = precompute_rule_outcomes(bar_data, signal_maps, rule)

        rule_results = {}
        year_snap_by_mode = {mode: [] for mode in COLLISION_MODES}

        for mode in COLLISION_MODES:
            finals = []; dds = []; tps = []; sls_ = []; hards = []; collapses = 0
            for it in range(N_ITER):
                rng = random.Random(1000 * hash(label) + it)
                r = run_single_sim(trading_days, signals_by_date, outcomes, mode, rng)
                finals.append(r['final'])
                dds.append(r['max_dd'])
                tps.append(r['tp_rate'])
                sls_.append(r['sl_rate'])
                hards.append(r['hard_rate'])
                if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
                    collapses += 1
                if is_primary:
                    year_snap_by_mode[mode].append(r['year_snapshots'])

            rule_results[mode] = dict(
                mean_ret   = (statistics.mean(finals) / STARTING_CASH - 1) * 100,
                med_ret    = (statistics.median(finals) / STARTING_CASH - 1) * 100,
                mean_dd    = statistics.mean(dds) * 100,
                worst_dd   = max(dds) * 100,
                p_coll     = collapses / N_ITER * 100,
                tp         = statistics.mean(tps),
                sl         = statistics.mean(sls_),
                hard       = statistics.mean(hards),
                mean_final = statistics.mean(finals),
            )

            if is_primary and year_snap_by_mode[mode]:
                year_means = {}
                for yr in SNAPSHOT_YEARS:
                    vals = [snap.get(yr) for snap in year_snap_by_mode[mode] if yr in snap]
                    if vals:
                        year_means[yr] = statistics.mean(vals)
                rule_results[mode]['year_equity'] = year_means

        window_results[rule['label']] = rule_results
        window_results[rule['label']]['_stats'] = stats

        rl = rule_results['realistic']
        cl = rule_results['conservative']
        print(f"{rule_idx+1:>3}  {rule['label']:<16}  {rule['desc']:<26}  "
              f"{stats['avg_tp']:>5.1f}%  {stats['tp_stress_frac']:>5.0f}%  "
              f"{stats['avg_sl']:>5.1f}%  {stats['sl_stress_frac']:>5.0f}%  "
              f"{rl['mean_ret']:>+13,.1f}%  {rl['worst_dd']:>7.1f}%  "
              f"{rl['tp']:>4.1f}%  {rl['sl']:>4.1f}%  "
              f"{cl['mean_ret']:>+13,.1f}%  {cl['worst_dd']:>7.1f}%")

    # ---- Ranked results (Realistic) -----------------------------------------
    print(f"\n{'─'*160}")
    print(f"RANKED — {label} — Realistic (sorted by mean return)")
    print(f"{'─'*160}")
    print(f"{'Rk':>3}  {'Rule':<16}  {'Description':<26}  "
          f"{'AvgTP':>6}  {'TPstr%':>6}  "
          f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  "
          f"{'TP%':>5}  {'SL%':>5}  {'P(c)':>5}")
    print('─'*160)

    sorted_rules = sorted(RULES,
        key=lambda r: -window_results[r['label']]['realistic']['mean_ret'])
    for rank, rule in enumerate(sorted_rules, 1):
        r = window_results[rule['label']]['realistic']
        s = window_results[rule['label']]['_stats']
        marker = " ◆" if rule['label'] == 'fixed_TP30' else ""
        print(f"{rank:>3}  {rule['label']:<16}  {rule['desc']:<26}  "
              f"{s['avg_tp']:>5.1f}%  {s['tp_stress_frac']:>5.0f}%  "
              f"{r['mean_ret']:>+13,.1f}%  {r['med_ret']:>+13,.1f}%  "
              f"{r['worst_dd']:>7.1f}%  {r['mean_dd']:>6.1f}%  "
              f"{r['tp']:>4.1f}%  {r['sl']:>4.1f}%  {r['p_coll']:>4.1f}%{marker}")

    # ---- Equity curve (primary window only) ---------------------------------
    if is_primary:
        for mode in ['conservative', 'realistic']:
            print(f"\n{'─'*160}")
            print(f"EQUITY CURVE — {label} — {mode.capitalize()} — "
                  f"$50k start, year-end portfolio values")
            print(f"{'─'*160}")
            header = f"{'Rk':>3}  {'Rule':<16}  {'Description':<26}  "
            for yr in SNAPSHOT_YEARS:
                header += f"{'$'+str(yr):>14}  "
            header += f"{'TotalRet':>14}"
            print(header)
            print('─'*160)

            sorted_by_final = sorted(RULES,
                key=lambda r: -window_results[r['label']][mode].get('mean_final', 0))
            for rank, rule in enumerate(sorted_by_final, 1):
                rm = window_results[rule['label']][mode]
                ye = rm.get('year_equity', {})
                marker = " ◆" if rule['label'] == 'fixed_TP30' else ""
                row = f"{rank:>3}  {rule['label']:<16}  {rule['desc']:<26}  "
                for yr in SNAPSHOT_YEARS:
                    val = ye.get(yr, 0)
                    if   val >= 1e9: row += f"${val/1e9:>11,.1f}B  "
                    elif val >= 1e6: row += f"${val/1e6:>11,.1f}M  "
                    elif val >= 1e3: row += f"${val/1e3:>11,.1f}K  "
                    else:            row += f"${val:>12,.0f}  "
                row += f"{rm['mean_ret']:>+13,.1f}%{marker}"
                print(row)

    # ---- Delta vs fixed_TP30 ------------------------------------------------
    baseline = window_results.get('fixed_TP30', {}).get('realistic', {})
    if baseline:
        print(f"\n{'─'*140}")
        print(f"DELTA vs fixed_TP30 — {label} — Realistic")
        print(f"{'─'*140}")
        print(f"{'Rule':<16}  {'Description':<26}  "
              f"{'ΔRet':>14}  {'ΔDD':>8}  {'ΔTP':>7}  {'TPstr%':>6}  {'Source':>10}")
        print('─'*140)
        for rule in sorted_rules:
            if rule['label'] == 'fixed_TP30':
                continue
            r = window_results[rule['label']]['realistic']
            d_ret = r['mean_ret'] - baseline['mean_ret']
            d_dd  = r['worst_dd'] - baseline['worst_dd']
            d_tp  = r['tp'] - baseline['tp']
            s     = window_results[rule['label']]['_stats']
            better = "▲" if d_ret > 0 else "▼"
            src = rule['tp_source'] or 'fixed'
            print(f"{rule['label']:<16}  {rule['desc']:<26}  "
                  f"{d_ret:>+13,.1f}%  {d_dd:>+7.1f}%  {d_tp:>+6.1f}%  "
                  f"{s['tp_stress_frac']:>5.0f}%  {src:>10}  {better}")

    return window_results


# ---- Family / summary utilities --------------------------------------------

def print_source_summary(all_results, year_labels):
    families = {
        'fixed':     [r for r in RULES if r['tp_source'] is None],
        'composite': [r for r in RULES if r['tp_source'] == 'composite'],
        'breadth':   [r for r in RULES if r['tp_source'] == 'brd_inv'],
        'vix':       [r for r in RULES if r['tp_source'] == 'vix_score'],
    }

    print('\n' + '='*140)
    print("SIGNAL SOURCE FAMILY SUMMARY — Best rule per family, Realistic mode")
    print('='*140)
    print(f"{'Family':<12}  {'Best Rule':<16}  {'Desc':<26}  ", end='')
    for l in year_labels:
        print(f"{l:>14}  ", end='')
    print(f"{'AvgRk':>6}")
    print('─'*140)

    family_bests = []
    for fname, rules in families.items():
        if not rules:
            continue
        best = max(rules,
            key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
        ranks = []
        for label in year_labels:
            sorted_all = sorted(RULES,
                key=lambda r: -all_results[label][r['label']]['realistic']['mean_ret'])
            for rank, r in enumerate(sorted_all, 1):
                if r['label'] == best['label']:
                    ranks.append(rank)
                    break
        family_bests.append((fname, best, sum(ranks) / len(ranks)))

    family_bests.sort(key=lambda x: x[2])
    for fname, best, avg_rank in family_bests:
        row = f"{fname:<12}  {best['label']:<16}  {best['desc']:<26}  "
        for label in year_labels:
            ret = all_results[label][best['label']]['realistic']['mean_ret']
            row += f"{ret:>+13,.1f}%  "
        row += f"  {avg_rank:>4.1f}"
        print(row)

    for fname, rules in families.items():
        if len(rules) <= 1:
            continue
        print(f"\n{'─'*120}")
        print(f"  {fname.upper()} family — all rules, 2022→now Realistic")
        print(f"{'─'*120}")
        print(f"  {'Rule':<16}  {'Desc':<26}  {'TPstr%':>6}  "
              f"{'MeanRet':>14}  {'WorstDD':>8}  {'2022 Ret':>12}  {'2024 Ret':>12}")
        print(f"  {'─'*116}")
        sorted_fam = sorted(rules,
            key=lambda r: -all_results['2022→now'][r['label']]['realistic']['mean_ret'])
        for rule in sorted_fam:
            r22now = all_results['2022→now'][rule['label']]['realistic']
            s = all_results['2022→now'][rule['label']]['_stats']
            r22 = all_results.get('2022', {}).get(rule['label'], {}).get('realistic', {})
            r24 = all_results.get('2024', {}).get(rule['label'], {}).get('realistic', {})
            ret22 = r22.get('mean_ret', 0)
            ret24 = r24.get('mean_ret', 0)
            print(f"  {rule['label']:<16}  {rule['desc']:<26}  {s['tp_stress_frac']:>5.0f}%  "
                  f"{r22now['mean_ret']:>+13,.1f}%  {r22now['worst_dd']:>7.1f}%  "
                  f"{ret22:>+11,.1f}%  {ret24:>+11,.1f}%")


# ---- Main -------------------------------------------------------------------

def main():
    print('='*160)
    print("MONTE CARLO — Regime-Conditioned Take-Profit Sweep")
    print('='*160)
    print(f"Strategy : TP base={TP_BASE:.0%} | stress TP varies per rule | "
          f"SL h{int(SL_BASE*100)}→{int(SL_STRESS*100)} via brd_inv@{SL_THRESHOLD} (locked)")
    print(f"Slippage : entry -1.0% | TP 0% (limit sell) | SL -1.3% | Hard -0.5%")
    print(f"Alloc    : 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)")
    print(f"MaxPos   : {MAX_POSITIONS}  |  Threshold: {PRIMARY_THRESHOLD}+ primary / {OVERFLOW_THRESHOLD}-74 overflow")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"Rules    : {len(RULES)} total (1 fixed + 3 sources x 4 stress TP values)")
    print(f"Windows  : {len(WINDOWS)}  (2022→now primary + per-year context)")
    print(f"Question : Does regime-aware TP beat fixed TP=30% from 2022 bear entry,")
    print(f"           and which signal source (VIX/breadth/composite) drives it?")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}")

    global_start = min(d for _, d, _ in WINDOWS)
    global_end   = max(d for _, _, d in WINDOWS)

    print("\nLoading regime data...", end=' ', flush=True)
    regime_raw = load_regime_raw(global_start, global_end)
    print(f"{len(regime_raw)} dates")

    print("Loading breadth data...", end=' ', flush=True)
    breadth_raw = load_breadth_raw(global_start, global_end)
    print(f"{len(breadth_raw)} dates")

    print("Building signal maps...", end=' ', flush=True)
    signal_maps = build_signal_maps(regime_raw, breadth_raw)
    print("done")
    for name, m in signal_maps.items():
        print(f"  {name:>10}: {len(m)} dates")

    all_results = {}

    primary_label, d_start, d_end = WINDOWS[0]
    all_results[primary_label] = run_window(
        primary_label, d_start, d_end, version, signal_maps, is_primary=True)

    for label, d_start, d_end in WINDOWS[1:]:
        all_results[label] = run_window(
            label, d_start, d_end, version, signal_maps, is_primary=False)

    # ---- Cross-window return grid ------------------------------------------
    year_labels = [l for l, _, _ in WINDOWS]

    print('\n' + '='*170)
    print("CROSS-WINDOW — Realistic Mode — Mean Return")
    print('='*170)

    rule_ranks = {r['label']: [] for r in RULES}
    for label in year_labels:
        ranked = sorted(RULES,
            key=lambda r: -all_results[label][r['label']]['realistic']['mean_ret'])
        for rank, rule in enumerate(ranked):
            rule_ranks[rule['label']].append(rank + 1)
    avg_ranks = [(rl, sum(ranks)/len(ranks)) for rl, ranks in rule_ranks.items()]
    avg_ranks.sort(key=lambda x: x[1])

    header = f"{'Rule':<16}  {'Desc':<26}  "
    for l in year_labels:
        header += f"{l:>14}  "
    header += f"{'AvgRk':>6}"
    print(header)
    print('─'*170)

    for rl, avg_rank in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        row = f"{rl:<16}  {rule['desc']:<26}  "
        for label in year_labels:
            ret = all_results[label][rl]['realistic']['mean_ret']
            row += f"{ret:>+13,.1f}%  "
        row += f"  {avg_rank:>4.1f}"
        marker = " ◆" if rl == 'fixed_TP30' else ""
        print(row + marker)

    # ---- Worst DD grid -----------------------------------------------------
    print('\n' + '='*140)
    print("CROSS-WINDOW — Realistic Mode — Worst Drawdown")
    print('='*140)
    header = f"{'Rule':<16}  {'Desc':<26}  "
    for l in year_labels:
        header += f"{l:>8}  "
    header += f"{'MaxAll':>8}"
    print(header)
    print('─'*140)
    for rl, _ in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        row = f"{rl:<16}  {rule['desc']:<26}  "
        dds = []
        for label in year_labels:
            dd = all_results[label][rl]['realistic']['worst_dd']
            dds.append(dd)
            row += f"{dd:>7.1f}%  "
        row += f"{max(dds):>7.1f}%"
        print(row)

    # ---- Conservative floor grid (safety gate) -----------------------------
    print('\n' + '='*140)
    print("CROSS-WINDOW — Conservative Mode — Worst Drawdown (safety gate, <80% = pass)")
    print('='*140)
    header = f"{'Rule':<16}  {'Desc':<26}  "
    for l in year_labels:
        header += f"{l:>8}  "
    header += f"{'MaxAll':>8}  {'Pass?':>6}"
    print(header)
    print('─'*140)
    for rl, _ in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        row = f"{rl:<16}  {rule['desc']:<26}  "
        dds = []
        for label in year_labels:
            dd = all_results[label][rl]['conservative']['worst_dd']
            dds.append(dd)
            row += f"{dd:>7.1f}%  "
        mx = max(dds)
        row += f"{mx:>7.1f}%  {'YES' if mx < 80 else 'NO':>6}"
        print(row)

    # ---- Family summary ----------------------------------------------------
    print_source_summary(all_results, year_labels)

    # ---- Verdict -----------------------------------------------------------
    print('\n' + '='*140)
    print("VERDICT — 2022→now (worst-case bear entry)")
    print('='*140)

    baseline = all_results['2022→now']['fixed_TP30']['realistic']
    baseline_c = all_results['2022→now']['fixed_TP30']['conservative']
    print(f"\n  FIXED_TP30 (baseline):")
    print(f"    Realistic     : ret={baseline['mean_ret']:+,.1f}%  DD={baseline['worst_dd']:.1f}%  "
          f"TP={baseline['tp']:.1f}%")
    print(f"    Conservative  : ret={baseline_c['mean_ret']:+,.1f}%  DD={baseline_c['worst_dd']:.1f}%")

    best_overall = max(RULES,
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_comp = max([r for r in RULES if r['tp_source'] == 'composite'],
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_brd  = max([r for r in RULES if r['tp_source'] == 'brd_inv'],
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_vix  = max([r for r in RULES if r['tp_source'] == 'vix_score'],
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])

    for tag, rule in [('OVERALL BEST',     best_overall),
                      ('BEST COMPOSITE',   best_comp),
                      ('BEST BREADTH',     best_brd),
                      ('BEST VIX',         best_vix)]:
        r = all_results['2022→now'][rule['label']]['realistic']
        c = all_results['2022→now'][rule['label']]['conservative']
        r22 = all_results.get('2022', {}).get(rule['label'], {}).get('realistic', {})
        s = all_results['2022→now'][rule['label']]['_stats']
        d_ret = r['mean_ret'] - baseline['mean_ret']
        d_dd  = r['worst_dd'] - baseline['worst_dd']
        print(f"\n  {tag}: {rule['label']}  ({rule['desc']})")
        print(f"    2022→now Realistic   : ret={r['mean_ret']:+,.1f}%  "
              f"({d_ret:+,.1f}% vs baseline)  DD={r['worst_dd']:.1f}%  "
              f"({d_dd:+.1f}% vs baseline)  TP={r['tp']:.1f}%  TPstress={s['tp_stress_frac']:.0f}%")
        print(f"    2022→now Conservative: ret={c['mean_ret']:+,.1f}%  DD={c['worst_dd']:.1f}%")
        if r22:
            print(f"    2022 bear Realistic  : ret={r22.get('mean_ret',0):+,.1f}%  "
                  f"TP={r22.get('tp',0):.1f}%")
    print()


if __name__ == '__main__':
    main()
