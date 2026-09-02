"""
Monte Carlo — Regime-Dependent Stop Loss Sweep
===============================================
Tests whether varying the stop-loss % based on the market regime composite
improves portfolio outcomes vs a fixed SL.

The regime composite (0-100) captures market conditions:
  Low composite  (~0-30) : calm / complacent / healthy market
  Mid composite  (~30-60): neutral
  High composite (~60-100): fear / stress / volatile

Historical averages confirm direction:
  2022 bear  avg=57.7 (highest)
  2023 recov avg=42.3
  2024 bull  avg=43.3
  2021 bull  avg=43.0

For each linear rule, SL is interpolated between two endpoints:
  sl = sl_calm + (sl_stress - sl_calm) * composite / 100

Two hypotheses tested:
  H1 "Wide in stress": volatile markets cause more shakeouts → wider SL
     in stress lets winners survive noise  (sl_calm < sl_stress)
  H2 "Tight in stress": stressed markets have better signal quality →
     can afford tighter SL + faster capital recycling  (sl_calm > sl_stress)

Windows: 2021-2026 individually + 5y continuous (each from $50k)
Collision: Conservative / Realistic / Optimistic
Iterations: 500 per (window × rule × mode)

Usage: python monte_carlo_regime_sl.py
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

from database.models.core import Score, AlgorithmVersion, MarketRegime
from database.models.technical import PriceHistory

# ---- Strategy constants (fixed from canonical MC) ----------------------------
STARTING_CASH      = 50_000.0
N_ITER             = 500
VOL_LOOKBACK       = 60
HOLD_DAYS          = 15          # trading bars
PREMIUM_MULT       = 1.82        # ATM 30-DTE premium ~ 1.82 * sigma_daily
DELTA              = 0.5

TP_OPTION_GAIN     =  0.30
HARD_SELL_LOSS     = -0.50

SLIP_ENTRY = -0.010
SLIP_TP    =  0.000   # limit sell at TP — no transaction costs
SLIP_SL    = -0.013
SLIP_HARD  = -0.005

NET_TP        = TP_OPTION_GAIN + SLIP_ENTRY + SLIP_TP     # +0.290
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD   # -0.515

TP_SIGMA = TP_OPTION_GAIN * PREMIUM_MULT / DELTA           # 1.092 sigma

TIER_ALLOC = {
    'top':      0.15,   # 85+
    'mid':      0.12,   # 80-84
    'low':      0.12,   # 75-79
    'overflow': 0.05,   # 70-74
}
MAX_POSITIONS      = 10
PRIMARY_THRESHOLD  = 75
OVERFLOW_THRESHOLD = 70
COLLAPSE_THRESHOLD = 0.20

DEFAULT_COMPOSITE  = 50.0   # fallback when no regime data

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('2026',   date(2026, 1, 1),  date(2026, 4, 16)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 16)),
]

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']


# ---- Regime-SL Rules ---------------------------------------------------------
# (label, sl_calm, sl_stress)
# sl_calm   = SL option loss % when regime composite ≈ 0  (calm market)
# sl_stress = SL option loss % when regime composite ≈ 100 (stressed market)
# Linear interpolation: sl = sl_calm + (sl_stress - sl_calm) * composite / 100

RULES = [
    # ── Fixed baselines (for direct comparison) ──
    ('fixed_25',      0.25, 0.25),
    ('fixed_30',      0.30, 0.30),
    ('fixed_35',      0.35, 0.35),   # current production
    ('fixed_40',      0.40, 0.40),
    ('fixed_45',      0.45, 0.45),

    # ── H1: Wide in stress (sl_calm < sl_stress) ──
    # Stressed = noisier → wider stops to survive shakeouts
    ('calm20_str40',  0.20, 0.40),
    ('calm20_str45',  0.20, 0.45),
    ('calm25_str40',  0.25, 0.40),
    ('calm25_str45',  0.25, 0.45),
    ('calm30_str45',  0.30, 0.45),
    ('calm30_str50',  0.30, 0.50),

    # ── H2: Tight in stress (sl_calm > sl_stress) ──
    # Stressed = better signals → tighter SL, faster recycling
    ('calm40_str20',  0.40, 0.20),
    ('calm40_str25',  0.40, 0.25),
    ('calm45_str25',  0.45, 0.25),
    ('calm45_str30',  0.45, 0.30),
    ('calm35_str20',  0.35, 0.20),
    ('calm50_str25',  0.50, 0.25),
]


def score_to_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def compute_sl_pct(composite, sl_calm, sl_stress):
    """Linear interpolation of SL% based on regime composite."""
    if composite is None:
        composite = DEFAULT_COMPOSITE
    t = max(0.0, min(100.0, composite)) / 100.0
    return sl_calm + (sl_stress - sl_calm) * t


def sl_pct_to_sigma(sl_pct):
    return sl_pct * PREMIUM_MULT / DELTA


def net_sl_from_pct(sl_pct):
    return -sl_pct + SLIP_ENTRY + SLIP_SL


# ---- Data loading ------------------------------------------------------------

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


def load_regime_map(d_start, d_end):
    """Load regime composite data with sorted dates for fast bisect lookup."""
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.regime_composite)
        .where(
            MarketRegime.date >= d_start - timedelta(days=60),
            MarketRegime.date <= d_end,
            MarketRegime.regime_composite.is_null(False),
        )
        .order_by(MarketRegime.date)
        .tuples()
    )
    regime_map = {}
    for d, comp in rows:
        regime_map[d] = float(comp)
    sorted_dates = sorted(regime_map.keys())
    return regime_map, sorted_dates


def get_regime_for_date(regime_map, sorted_dates, d):
    """Get regime composite for date d via bisect (most recent on or before d)."""
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx >= 0:
        return regime_map[sorted_dates[idx]]
    return DEFAULT_COMPOSITE


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


# ---- Bar data precomputation -------------------------------------------------
# Precompute once per window: per-bar sigma-normalized high/low for each signal.
# Then for any SL rule, evaluate outcome by walking these bars.

def precompute_bar_data(signals, ph):
    """Per signal: entry, vol, premium_pct, and bar-level sigma-normalized extremes."""
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

        bars = []
        sigma_frac = vol / 100  # decimal
        for i in range(base_idx + 1, end_idx):
            high_sigma = (highs[i] / entry - 1) / sigma_frac
            low_sigma  = (lows[i]  / entry - 1) / sigma_frac
            bars.append((high_sigma, low_sigma))

        result[(sig.symbol_id, sig.date)] = {
            'vol': vol, 'entry': entry, 'premium_pct': premium_pct, 'bars': bars,
        }
    return result


def evaluate_outcome(bars, sl_sigma):
    """Walk bars and return (kind, exit_bar). TP_SIGMA is global constant."""
    for i, (high_s, low_s) in enumerate(bars):
        tp_hit = high_s >= TP_SIGMA
        sl_hit = low_s  <= -sl_sigma
        if tp_hit and sl_hit:
            return 'both', i + 1
        if tp_hit:
            return 'tp', i + 1
        if sl_hit:
            return 'sl', i + 1
    return 'hard', HOLD_DAYS


def precompute_rule_outcomes(bar_data, regime_map, sorted_regime_dates, sl_calm, sl_stress):
    """For a specific rule, evaluate every signal's outcome + per-trade net_sl."""
    outcomes = {}
    sl_pcts = []
    for key, bd in bar_data.items():
        _, sig_date = key
        composite = get_regime_for_date(regime_map, sorted_regime_dates, sig_date)
        sl_pct = compute_sl_pct(composite, sl_calm, sl_stress)
        sl_pcts.append(sl_pct)
        sl_sigma = sl_pct_to_sigma(sl_pct)
        kind, exit_bar = evaluate_outcome(bd['bars'], sl_sigma)
        outcomes[key] = {
            'kind': kind, 'exit_bar': exit_bar,
            'premium_pct': bd['premium_pct'],
            'net_sl': net_sl_from_pct(sl_pct),
        }
    avg_sl = sum(sl_pcts) / len(sl_pcts) * 100 if sl_pcts else 0
    return outcomes, avg_sl


# ---- Portfolio simulation ----------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'exit_bar', 'premium_cost', 'option_pnl', 'outcome']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome


def resolve(kind, mode, rng, net_sl):
    """Resolve collision kind into (outcome_label, pnl). net_sl is per-trade."""
    if kind == 'tp':   return 'tp',   NET_TP
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    # kind == 'both'
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', NET_TP
    return ('tp', NET_TP) if rng.random() < 0.5 else ('sl', net_sl)


def run_single_sim(trading_days, signals_by_date, outcomes, mode, rng):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}
    tp_c = sl_c = hard_c = 0

    for day_idx, today in enumerate(trading_days):
        # Close expiring positions
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
            outcome, pnl = resolve(o['kind'], mode, rng, o['net_sl'])
            cash -= premium_cost
            positions.append(Position(sym_id, today, o['exit_bar'],
                                      premium_cost, pnl, outcome))

    # Close remainder
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


# ---- Window runner -----------------------------------------------------------

def run_window(label, d_start, d_end, version):
    print(f"\n{'='*130}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print('='*130)

    signals = load_signals(version, d_start, d_end)
    primary_n  = sum(1 for s in signals if s.overall >= PRIMARY_THRESHOLD)
    overflow_n = len(signals) - primary_n
    print(f"Signals: {len(signals):,} (75+={primary_n:,}, 70-74={overflow_n:,})")

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

    regime_map, sorted_regime_dates = load_regime_map(d_start, d_end)
    regime_dates_in_window = len([d for d in sorted_regime_dates if d_start <= d <= d_end])
    print(f"Regime data: {regime_dates_in_window} dates in window")

    # Regime distribution on signal dates
    composites = [get_regime_for_date(regime_map, sorted_regime_dates, sig.date)
                  for sig in signals]
    if composites:
        avg_comp = sum(composites) / len(composites)
        p10 = sorted(composites)[int(len(composites) * 0.10)]
        p90 = sorted(composites)[int(len(composites) * 0.90)]
        print(f"Regime on signal dates: mean={avg_comp:.1f}  "
              f"p10={p10:.1f}  p90={p90:.1f}  "
              f"min={min(composites):.1f}  max={max(composites):.1f}")

    window_results = {}

    # Progress header
    print(f"\n{'#':>3}  {'Rule':<16}  {'SL range':<20}  {'AvgSL':>6}  "
          f"{'— Realistic —':^44}  {'— Conservative —':^20}")
    print(f"{'':3}  {'':16}  {'':20}  {'':6}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}  {'TP%':>6}  {'SL%':>6}  {'Hrd%':>5}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}")
    print('─' * 130)

    for rule_idx, (rule_label, sl_calm, sl_stress) in enumerate(RULES):
        outcomes, avg_sl = precompute_rule_outcomes(
            bar_data, regime_map, sorted_regime_dates, sl_calm, sl_stress)

        rule_results = {}
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

            rule_results[mode] = dict(
                mean_ret  = (statistics.mean(finals) / STARTING_CASH - 1) * 100,
                med_ret   = (statistics.median(finals) / STARTING_CASH - 1) * 100,
                mean_dd   = statistics.mean(dds) * 100,
                worst_dd  = max(dds) * 100,
                p_coll    = collapses / N_ITER * 100,
                tp        = statistics.mean(tps),
                sl        = statistics.mean(sls_),
                hard      = statistics.mean(hards),
                mean_final = statistics.mean(finals),
            )

        window_results[rule_label] = rule_results
        window_results[rule_label]['_avg_sl'] = avg_sl

        # Print progress row
        rl = rule_results['realistic']
        cl = rule_results['conservative']
        is_adaptive = sl_calm != sl_stress
        sl_range = (f"{sl_calm*100:.0f}%→{sl_stress*100:.0f}%"
                    if is_adaptive else f"{sl_calm*100:.0f}% fixed")
        print(f"{rule_idx+1:>3}  {rule_label:<16}  {sl_range:<20}  {avg_sl:>5.1f}%  "
              f"{rl['mean_ret']:>+13,.1f}%  {rl['worst_dd']:>7.1f}%  "
              f"{rl['tp']:>5.1f}%  {rl['sl']:>5.1f}%  {rl['hard']:>4.1f}%  "
              f"{cl['mean_ret']:>+13,.1f}%  {cl['worst_dd']:>7.1f}%")

    # ---- Detailed results table (Realistic) sorted by return -----------------
    print(f"\n{'─'*130}")
    print(f"RANKED RESULTS — {label} — Realistic Mode (sorted by mean return)")
    print(f"{'─'*130}")
    print(f"{'Rank':>4}  {'Rule':<16}  {'SL range':<20}  {'AvgSL':>6}  "
          f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  "
          f"{'TP%':>6}  {'SL%':>6}  {'Hrd%':>5}  {'P(col)':>7}")
    print('─'*130)

    sorted_rules = sorted(RULES, key=lambda r: -window_results[r[0]]['realistic']['mean_ret'])
    for rank, (rl_name, sl_calm, sl_stress) in enumerate(sorted_rules, 1):
        r = window_results[rl_name]['realistic']
        is_adaptive = sl_calm != sl_stress
        sl_range = (f"{sl_calm*100:.0f}%→{sl_stress*100:.0f}%"
                    if is_adaptive else f"{sl_calm*100:.0f}% fixed")
        avg_sl = window_results[rl_name]['_avg_sl']
        marker = " ★" if rl_name == 'fixed_35' else ""
        print(f"{rank:>4}  {rl_name:<16}  {sl_range:<20}  {avg_sl:>5.1f}%  "
              f"{r['mean_ret']:>+13,.1f}%  {r['med_ret']:>+13,.1f}%  "
              f"{r['worst_dd']:>7.1f}%  {r['mean_dd']:>6.1f}%  "
              f"{r['tp']:>5.1f}%  {r['sl']:>5.1f}%  {r['hard']:>4.1f}%  "
              f"{r['p_coll']:>6.1f}%{marker}")

    # ---- Delta vs fixed_35 baseline ------------------------------------------
    baseline = window_results.get('fixed_35', {}).get('realistic', {})
    if baseline:
        print(f"\n{'─'*100}")
        print(f"DELTA vs fixed_35 — {label} — Realistic Mode")
        print(f"{'─'*100}")
        print(f"{'Rule':<16}  {'ΔMeanRet':>14}  {'ΔWorstDD':>9}  {'ΔTP%':>7}  {'Direction':<24}")
        print('─'*100)
        for rl_name, sl_calm, sl_stress in sorted_rules:
            if rl_name == 'fixed_35':
                continue
            r = window_results[rl_name]['realistic']
            d_ret = r['mean_ret'] - baseline['mean_ret']
            d_dd  = r['worst_dd'] - baseline['worst_dd']
            d_tp  = r['tp'] - baseline['tp']
            direction = "H1:wide_stress" if sl_calm < sl_stress else (
                        "H2:tight_stress" if sl_calm > sl_stress else "fixed")
            better = "▲" if d_ret > 0 else "▼"
            print(f"{rl_name:<16}  {d_ret:>+13,.1f}%  {d_dd:>+8.1f}%  {d_tp:>+6.1f}%  "
                  f"{direction:<24} {better}")

    return window_results


# ---- Main --------------------------------------------------------------------

def main():
    print('='*130)
    print("MONTE CARLO — Regime-Dependent Stop Loss Sweep")
    print('='*130)
    print(f"Strategy : 30 DTE | TP=+30% | Hard=-50%@day15 | SL=regime-dependent (swept)")
    print(f"Slippage : entry -1.0% | TP 0% (limit sell) | SL -1.3% | Hard -0.5%")
    print(f"           NET_TP={NET_TP:+.3f}  NET_HARD={NET_HARD_SELL:+.3f}")
    print(f"Alloc    : 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)")
    print(f"MaxPos   : {MAX_POSITIONS}  |  Threshold: {PRIMARY_THRESHOLD}+/{OVERFLOW_THRESHOLD}-74")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"TP sigma : {TP_SIGMA:.3f}")
    n_fixed = sum(1 for _, a, b in RULES if a == b)
    n_adapt = sum(1 for _, a, b in RULES if a != b)
    print(f"Rules    : {len(RULES)} total ({n_fixed} fixed baselines + {n_adapt} adaptive)")
    print(f"Windows  : {len(WINDOWS)}")
    print(f"Total configs: {len(RULES)} rules × {len(WINDOWS)} windows × "
          f"{len(COLLISION_MODES)} modes = {len(RULES)*len(WINDOWS)*len(COLLISION_MODES)}")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}")

    all_results = {}
    for label, d_start, d_end in WINDOWS:
        all_results[label] = run_window(label, d_start, d_end, version)

    # ==== CROSS-WINDOW SUMMARIES =============================================

    year_labels = [l for l, _, _ in WINDOWS]

    # ---- Mean Return grid (Realistic) ----------------------------------------
    print('\n' + '='*140)
    print("CROSS-WINDOW SUMMARY — Realistic Mode — Mean Return")
    print('='*140)

    # Compute consistency ranking: average rank across all windows
    rule_ranks = {r[0]: [] for r in RULES}
    for label in year_labels:
        ranked = sorted(RULES, key=lambda r: -all_results[label][r[0]]['realistic']['mean_ret'])
        for rank, (rl, _, _) in enumerate(ranked):
            rule_ranks[rl].append(rank + 1)

    avg_ranks = [(rl, sum(ranks)/len(ranks)) for rl, ranks in rule_ranks.items()]
    avg_ranks.sort(key=lambda x: x[1])

    header = f"{'Rule':<16}  " + '  '.join(f"{l:>14}" for l in year_labels) + f"  {'AvgRank':>8}"
    print(header)
    print('─'*140)

    for rl, avg_rank in avg_ranks:
        row = f"{rl:<16}  "
        for label in year_labels:
            ret = all_results[label][rl]['realistic']['mean_ret']
            row += f"{ret:>+13,.1f}%  "
        row += f"  {avg_rank:>6.1f}"
        marker = "  ★ prod" if rl == 'fixed_35' else ""
        print(row + marker)

    # ---- Worst DD grid (Realistic) -------------------------------------------
    print('\n' + '='*130)
    print("CROSS-WINDOW SUMMARY — Realistic Mode — Worst Drawdown")
    print('='*130)
    header = f"{'Rule':<16}  " + '  '.join(f"{l:>8}" for l in year_labels) + f"  {'MaxAcross':>10}"
    print(header)
    print('─'*130)

    for rl, _ in avg_ranks:
        row = f"{rl:<16}  "
        dds = []
        for label in year_labels:
            dd = all_results[label][rl]['realistic']['worst_dd']
            dds.append(dd)
            row += f"{dd:>7.1f}%  "
        row += f"  {max(dds):>8.1f}%"
        print(row)

    # ---- TP Rate grid (Realistic) --------------------------------------------
    print('\n' + '='*130)
    print("CROSS-WINDOW SUMMARY — Realistic Mode — TP Rate")
    print('='*130)
    header = f"{'Rule':<16}  " + '  '.join(f"{l:>8}" for l in year_labels)
    print(header)
    print('─'*130)

    for rl, _ in avg_ranks:
        row = f"{rl:<16}  "
        for label in year_labels:
            tp = all_results[label][rl]['realistic']['tp']
            row += f"{tp:>7.1f}%  "
        print(row)

    # ---- 2022 Bear Year Conservative deep-dive --------------------------------
    print('\n' + '='*100)
    print("2022 BEAR YEAR — Conservative Mode (lower-bound estimate)")
    print('='*100)
    print(f"{'Rank':>4}  {'Rule':<16}  {'MeanRet':>14}  {'WorstDD':>8}  "
          f"{'TP%':>6}  {'SL%':>6}  {'P(col)':>7}")
    print('─'*100)

    sorted_2022 = sorted(RULES,
                         key=lambda r: -all_results['2022'][r[0]]['conservative']['mean_ret'])
    for rank, (rl, _, _) in enumerate(sorted_2022, 1):
        r = all_results['2022'][rl]['conservative']
        marker = " ★" if rl == 'fixed_35' else ""
        print(f"{rank:>4}  {rl:<16}  {r['mean_ret']:>+13,.1f}%  {r['worst_dd']:>7.1f}%  "
              f"{r['tp']:>5.1f}%  {r['sl']:>5.1f}%  {r['p_coll']:>6.1f}%{marker}")

    # ---- Break-even analysis per rule ----------------------------------------
    print('\n' + '='*100)
    print("BREAK-EVEN TP RATE — per rule (at mean effective SL)")
    print('='*100)
    print(f"{'Rule':<16}  {'AvgSL%':>7}  {'NetSL':>7}  {'BE_TP':>7}  "
          f"{'2022_TP':>8}  {'2022_margin':>12}  {'Worst_TP':>9}  {'Worst_margin':>13}")
    print('─'*100)

    for rl, _ in avg_ranks:
        # Use the 5y avg SL as representative
        avg_sl = all_results['5y'][rl]['_avg_sl']
        net_sl = abs(net_sl_from_pct(avg_sl / 100))
        be_tp = net_sl / (NET_TP + net_sl) * 100 if (NET_TP + net_sl) > 0 else 99

        tp_2022 = all_results['2022'][rl]['realistic']['tp']
        margin_2022 = tp_2022 - be_tp

        # Find worst TP rate across all windows
        worst_tp = min(all_results[l][rl]['realistic']['tp'] for l in year_labels)
        worst_margin = worst_tp - be_tp

        print(f"{rl:<16}  {avg_sl:>6.1f}%  {-net_sl*100:>+6.1f}%  {be_tp:>6.1f}%  "
              f"{tp_2022:>7.1f}%  {margin_2022:>+11.1f}pp  "
              f"{worst_tp:>8.1f}%  {worst_margin:>+12.1f}pp")

    # ---- Final recommendation ------------------------------------------------
    print('\n' + '='*130)
    print("TOP 5 CONSISTENCY RANKING — Best average rank across all windows (Realistic)")
    print('='*130)

    for i, (rl, avg_rank) in enumerate(avg_ranks[:5]):
        sl_calm  = next(r[1] for r in RULES if r[0] == rl)
        sl_stress = next(r[2] for r in RULES if r[0] == rl)
        is_adaptive = sl_calm != sl_stress
        sl_desc = (f"calm={sl_calm*100:.0f}%→stress={sl_stress*100:.0f}%"
                   if is_adaptive else f"fixed {sl_calm*100:.0f}%")

        # Key metrics
        cons_2022_ret = all_results['2022'][rl]['conservative']['mean_ret']
        cons_2022_dd  = all_results['2022'][rl]['conservative']['worst_dd']
        real_5y_ret   = all_results['5y'][rl]['realistic']['mean_ret']
        real_5y_dd    = all_results['5y'][rl]['realistic']['worst_dd']

        print(f"  #{i+1}  {rl:<16}  {sl_desc:<28}  avg_rank={avg_rank:.1f}")
        print(f"       5y_real: ret={real_5y_ret:>+,.1f}%  DD={real_5y_dd:.1f}%  |  "
              f"2022_cons: ret={cons_2022_ret:>+,.1f}%  DD={cons_2022_dd:.1f}%")

    # ---- Adaptive vs Fixed verdict -------------------------------------------
    print('\n' + '='*130)
    print("ADAPTIVE vs FIXED — Does regime-dependent SL beat the best fixed SL?")
    print('='*130)

    # Best fixed rule by avg rank
    fixed_rules = [(rl, r) for rl, r in avg_ranks if any(
        rr[0] == rl and rr[1] == rr[2] for rr in RULES)]
    adaptive_rules = [(rl, r) for rl, r in avg_ranks if any(
        rr[0] == rl and rr[1] != rr[2] for rr in RULES)]

    if fixed_rules and adaptive_rules:
        best_fixed = fixed_rules[0]
        best_adaptive = adaptive_rules[0]

        print(f"Best fixed:    {best_fixed[0]:<16}  avg_rank={best_fixed[1]:.1f}")
        print(f"Best adaptive: {best_adaptive[0]:<16}  avg_rank={best_adaptive[1]:.1f}")
        print()

        # Per-window comparison
        print(f"{'Window':<8}  {'Best Fixed':>14}  {'Best Adaptive':>14}  {'Δ':>14}  {'Winner':<10}")
        print('─'*70)
        adaptive_wins = 0
        for label in year_labels:
            f_ret = all_results[label][best_fixed[0]]['realistic']['mean_ret']
            a_ret = all_results[label][best_adaptive[0]]['realistic']['mean_ret']
            delta = a_ret - f_ret
            winner = "ADAPTIVE" if delta > 0 else "FIXED"
            if delta > 0:
                adaptive_wins += 1
            print(f"{label:<8}  {f_ret:>+13,.1f}%  {a_ret:>+13,.1f}%  {delta:>+13,.1f}%  {winner:<10}")

        print(f"\nAdaptive wins {adaptive_wins}/{len(year_labels)} windows")


if __name__ == '__main__':
    main()
