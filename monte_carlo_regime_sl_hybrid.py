"""
Monte Carlo — Worst-Case Entry + Hybrid Regime SL
==================================================
Tests regime-dependent SL strategies starting from the worst possible entry
point (Jan 1, 2022 — start of bear market) through to present.

Includes:
  - Fixed SL baselines (25%-45%)
  - Best linear rules from prior sweep (calm25_str45, calm45_str25)
  - NEW: Hybrid threshold-switch rules — use base SL normally, switch to wider
    SL when regime composite exceeds a threshold.  Zero cost in calm markets.

Primary question: which strategy best protects initial capital through the
2022 bear while still compounding aggressively through 2023-2026?

The year-by-year equity curve within the continuous run shows how $50k
progresses through bear → recovery → bull → chop — the full gauntlet.

Windows:
  - 2022→now  (continuous, worst-case entry — PRIMARY FOCUS)
  - Individual years 2022-2026 for per-year context
  - Year-by-year equity snapshots within the continuous window

Usage: python monte_carlo_regime_sl_hybrid.py
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

DEFAULT_COMPOSITE  = 50.0

WINDOWS = [
    ('2022→now', date(2022, 1, 1),  date(2026, 4, 16)),   # PRIMARY: worst-case entry
    ('2022',     date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',     date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',     date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',     date(2025, 1, 1),  date(2025, 12, 31)),
    ('2026',     date(2026, 1, 1),  date(2026, 4, 16)),
]

COLLISION_MODES = ['conservative', 'realistic', 'optimistic']

# Snapshot years for the continuous window equity curve
SNAPSHOT_YEARS = [2022, 2023, 2024, 2025, 2026]


# ---- Rule definitions --------------------------------------------------------
# Three types: 'fixed', 'linear', 'hybrid'
#   fixed  : constant SL regardless of regime
#   linear : sl = sl_calm + (sl_stress - sl_calm) * composite / 100
#   hybrid : sl = sl_base if composite < threshold, else sl_stress

RULES = [
    # ── Fixed baselines ──
    {'label': 'fixed_25',       'type': 'fixed', 'sl': 0.25},
    {'label': 'fixed_30',       'type': 'fixed', 'sl': 0.30},
    {'label': 'fixed_35',       'type': 'fixed', 'sl': 0.35},
    {'label': 'fixed_40',       'type': 'fixed', 'sl': 0.40},
    {'label': 'fixed_45',       'type': 'fixed', 'sl': 0.45},

    # ── Best linear rules from prior sweep ──
    {'label': 'lin_25→45',      'type': 'linear', 'sl_calm': 0.25, 'sl_stress': 0.45},
    {'label': 'lin_45→25',      'type': 'linear', 'sl_calm': 0.45, 'sl_stress': 0.25},

    # ── Hybrid: base=35% (production), switch to wider SL at threshold ──
    {'label': 'h35→40@50',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.40, 'threshold': 50},
    {'label': 'h35→40@55',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.40, 'threshold': 55},
    {'label': 'h35→40@60',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.40, 'threshold': 60},
    {'label': 'h35→40@65',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.40, 'threshold': 65},

    {'label': 'h35→45@50',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.45, 'threshold': 50},
    {'label': 'h35→45@55',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.45, 'threshold': 55},
    {'label': 'h35→45@60',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.45, 'threshold': 60},
    {'label': 'h35→45@65',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.45, 'threshold': 65},

    {'label': 'h35→50@55',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.50, 'threshold': 55},
    {'label': 'h35→50@60',      'type': 'hybrid', 'sl_base': 0.35, 'sl_stress': 0.50, 'threshold': 60},

    # ── Hybrid: base=30%, switch to 45% ──
    {'label': 'h30→45@55',      'type': 'hybrid', 'sl_base': 0.30, 'sl_stress': 0.45, 'threshold': 55},
    {'label': 'h30→45@60',      'type': 'hybrid', 'sl_base': 0.30, 'sl_stress': 0.45, 'threshold': 60},

    # ── Hybrid: base=25%, switch to 45% ──
    {'label': 'h25→45@55',      'type': 'hybrid', 'sl_base': 0.25, 'sl_stress': 0.45, 'threshold': 55},
    {'label': 'h25→45@60',      'type': 'hybrid', 'sl_base': 0.25, 'sl_stress': 0.45, 'threshold': 60},
]


def score_to_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def rule_sl_pct(rule, composite):
    """Compute SL% for a given rule and regime composite."""
    if composite is None:
        composite = DEFAULT_COMPOSITE

    if rule['type'] == 'fixed':
        return rule['sl']

    if rule['type'] == 'linear':
        t = max(0.0, min(100.0, composite)) / 100.0
        return rule['sl_calm'] + (rule['sl_stress'] - rule['sl_calm']) * t

    if rule['type'] == 'hybrid':
        if composite >= rule['threshold']:
            return rule['sl_stress']
        return rule['sl_base']

    return 0.35  # fallback


def sl_pct_to_sigma(sl_pct):
    return sl_pct * PREMIUM_MULT / DELTA


def net_sl_from_pct(sl_pct):
    return -sl_pct + SLIP_ENTRY + SLIP_SL


def rule_description(rule):
    """Short human-readable description."""
    if rule['type'] == 'fixed':
        return f"{rule['sl']*100:.0f}% fixed"
    if rule['type'] == 'linear':
        return f"lin {rule['sl_calm']*100:.0f}%→{rule['sl_stress']*100:.0f}%"
    if rule['type'] == 'hybrid':
        return f"{rule['sl_base']*100:.0f}%→{rule['sl_stress']*100:.0f}% @{rule['threshold']}"
    return "?"


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

def precompute_bar_data(signals, ph):
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
        sigma_frac = vol / 100
        for i in range(base_idx + 1, end_idx):
            high_sigma = (highs[i] / entry - 1) / sigma_frac
            low_sigma  = (lows[i]  / entry - 1) / sigma_frac
            bars.append((high_sigma, low_sigma))

        result[(sig.symbol_id, sig.date)] = {
            'vol': vol, 'entry': entry, 'premium_pct': premium_pct, 'bars': bars,
        }
    return result


def evaluate_outcome(bars, sl_sigma):
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


def precompute_rule_outcomes(bar_data, regime_map, sorted_regime_dates, rule):
    """For a specific rule, evaluate every signal's outcome."""
    outcomes = {}
    sl_pcts = []
    for key, bd in bar_data.items():
        _, sig_date = key
        composite = get_regime_for_date(regime_map, sorted_regime_dates, sig_date)
        sl_pct = rule_sl_pct(rule, composite)
        sl_pcts.append(sl_pct)
        sl_sigma = sl_pct_to_sigma(sl_pct)
        kind, exit_bar = evaluate_outcome(bd['bars'], sl_sigma)
        outcomes[key] = {
            'kind': kind, 'exit_bar': exit_bar,
            'premium_pct': bd['premium_pct'],
            'net_sl': net_sl_from_pct(sl_pct),
        }
    avg_sl = sum(sl_pcts) / len(sl_pcts) * 100 if sl_pcts else 0
    # Fraction of trades that used the stress SL (for hybrids)
    if rule['type'] == 'hybrid' and sl_pcts:
        stress_frac = sum(1 for p in sl_pcts if p >= rule['sl_stress'] - 0.001) / len(sl_pcts) * 100
    else:
        stress_frac = 0
    return outcomes, avg_sl, stress_frac


# ---- Portfolio simulation (with year-by-year snapshots) ----------------------

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
    if kind == 'tp':   return 'tp',   NET_TP
    if kind == 'sl':   return 'sl',   net_sl
    if kind == 'hard': return 'hard', NET_HARD_SELL
    if mode == 'conservative': return 'sl', net_sl
    if mode == 'optimistic':   return 'tp', NET_TP
    return ('tp', NET_TP) if rng.random() < 0.5 else ('sl', net_sl)


def run_single_sim(trading_days, signals_by_date, outcomes, mode, rng):
    """Run one MC iteration. Returns results + year-end equity snapshots."""
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}
    tp_c = sl_c = hard_c = 0

    # Year-end snapshots: {year: portfolio_value}
    year_snapshots = {}
    prev_day = None
    prev_portfolio_value = STARTING_CASH

    for day_idx, today in enumerate(trading_days):
        # Record portfolio value at year transitions
        if prev_day is not None and today.year != prev_day.year:
            year_snapshots[prev_day.year] = prev_portfolio_value

        prev_day = today

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
            outcome, pnl = resolve(o['kind'], mode, rng, o['net_sl'])
            cash -= premium_cost
            positions.append(Position(sym_id, today, o['exit_bar'],
                                      premium_cost, pnl, outcome))

    # Close remainder
    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        hard_c += 1
    portfolio_value = cash

    # Final year snapshot
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


# ---- Window runner -----------------------------------------------------------

def run_window(label, d_start, d_end, version, is_primary=False):
    print(f"\n{'='*140}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})"
          + ("  *** WORST-CASE ENTRY ***" if is_primary else ""))
    print('='*140)

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

    regime_map, sorted_regime_dates = load_regime_map(d_start, d_end)
    regime_dates_in_window = len([d for d in sorted_regime_dates if d_start <= d <= d_end])
    print(f"Regime data: {regime_dates_in_window} dates")

    composites = [get_regime_for_date(regime_map, sorted_regime_dates, sig.date)
                  for sig in signals]
    if composites:
        sorted_c = sorted(composites)
        avg_comp = sum(composites) / len(composites)
        p10 = sorted_c[int(len(sorted_c) * 0.10)]
        p90 = sorted_c[int(len(sorted_c) * 0.90)]
        pct_above_60 = sum(1 for c in composites if c >= 60) / len(composites) * 100
        print(f"Regime composite: mean={avg_comp:.1f}  p10={p10:.1f}  p90={p90:.1f}  "
              f">{'>'}60: {pct_above_60:.0f}%")

    window_results = {}

    # Progress header
    print(f"\n{'#':>3}  {'Rule':<16}  {'Description':<22}  {'AvgSL':>6}  "
          f"{'— Realistic —':^44}  {'— Conservative —':^20}")
    print(f"{'':3}  {'':16}  {'':22}  {'':6}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}  {'TP%':>6}  {'SL%':>6}  {'Hrd%':>5}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}")
    print('─' * 140)

    for rule_idx, rule in enumerate(RULES):
        outcomes, avg_sl, stress_frac = precompute_rule_outcomes(
            bar_data, regime_map, sorted_regime_dates, rule)

        rule_results = {}
        # Also collect per-iter year snapshots for the primary window
        all_year_snapshots = {mode: [] for mode in COLLISION_MODES}

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
                    all_year_snapshots[mode].append(r['year_snapshots'])

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

            # Aggregate year snapshots
            if is_primary and all_year_snapshots[mode]:
                year_means = {}
                for yr in SNAPSHOT_YEARS:
                    vals = [snap.get(yr) for snap in all_year_snapshots[mode]
                            if yr in snap]
                    if vals:
                        year_means[yr] = statistics.mean(vals)
                rule_results[mode]['year_equity'] = year_means

        window_results[rule['label']] = rule_results
        window_results[rule['label']]['_avg_sl'] = avg_sl
        window_results[rule['label']]['_stress_frac'] = stress_frac

        # Print progress row
        rl = rule_results['realistic']
        cl = rule_results['conservative']
        desc = rule_description(rule)
        print(f"{rule_idx+1:>3}  {rule['label']:<16}  {desc:<22}  {avg_sl:>5.1f}%  "
              f"{rl['mean_ret']:>+13,.1f}%  {rl['worst_dd']:>7.1f}%  "
              f"{rl['tp']:>5.1f}%  {rl['sl']:>5.1f}%  {rl['hard']:>4.1f}%  "
              f"{cl['mean_ret']:>+13,.1f}%  {cl['worst_dd']:>7.1f}%")

    # ---- Ranked results (Realistic) ------------------------------------------
    print(f"\n{'─'*140}")
    print(f"RANKED — {label} — Realistic (sorted by mean return)")
    print(f"{'─'*140}")
    print(f"{'Rk':>3}  {'Rule':<16}  {'Description':<22}  {'AvgSL':>6}  "
          f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  "
          f"{'TP%':>6}  {'SL%':>6}  {'Hrd%':>5}  {'P(c)':>5}")
    print('─'*140)

    sorted_rules = sorted(RULES, key=lambda r: -window_results[r['label']]['realistic']['mean_ret'])
    for rank, rule in enumerate(sorted_rules, 1):
        r = window_results[rule['label']]['realistic']
        desc = rule_description(rule)
        marker = " ★" if rule['label'] == 'fixed_35' else ""
        print(f"{rank:>3}  {rule['label']:<16}  {desc:<22}  "
              f"{window_results[rule['label']]['_avg_sl']:>5.1f}%  "
              f"{r['mean_ret']:>+13,.1f}%  {r['med_ret']:>+13,.1f}%  "
              f"{r['worst_dd']:>7.1f}%  {r['mean_dd']:>6.1f}%  "
              f"{r['tp']:>5.1f}%  {r['sl']:>5.1f}%  {r['hard']:>4.1f}%  "
              f"{r['p_coll']:>4.1f}%{marker}")

    # ---- Year-by-year equity curve (primary window only) ---------------------
    if is_primary:
        for mode in COLLISION_MODES:
            print(f"\n{'─'*140}")
            print(f"EQUITY CURVE — {label} — {mode.capitalize()} — "
                  f"$50k start, year-end portfolio values")
            print(f"{'─'*140}")
            header = f"{'Rk':>3}  {'Rule':<16}  {'Description':<22}  "
            for yr in SNAPSHOT_YEARS:
                header += f"{'$'+str(yr):>14}  "
            header += f"{'TotalRet':>14}"
            print(header)
            print('─'*140)

            # Sort by final value in this mode
            sorted_by_final = sorted(RULES,
                key=lambda r: -window_results[r['label']][mode].get('mean_final', 0))

            for rank, rule in enumerate(sorted_by_final, 1):
                rm = window_results[rule['label']][mode]
                ye = rm.get('year_equity', {})
                desc = rule_description(rule)
                marker = " ★" if rule['label'] == 'fixed_35' else ""
                row = f"{rank:>3}  {rule['label']:<16}  {desc:<22}  "
                for yr in SNAPSHOT_YEARS:
                    val = ye.get(yr, 0)
                    if val >= 1_000_000_000:
                        row += f"${val/1e9:>11,.1f}B  "
                    elif val >= 1_000_000:
                        row += f"${val/1e6:>11,.1f}M  "
                    elif val >= 1_000:
                        row += f"${val/1e3:>11,.1f}K  "
                    else:
                        row += f"${val:>12,.0f}  "
                row += f"{rm['mean_ret']:>+13,.1f}%{marker}"
                print(row)

    # ---- Delta vs fixed_35 ---------------------------------------------------
    baseline = window_results.get('fixed_35', {}).get('realistic', {})
    if baseline:
        print(f"\n{'─'*120}")
        print(f"DELTA vs fixed_35 — {label} — Realistic")
        print(f"{'─'*120}")
        print(f"{'Rule':<16}  {'Description':<22}  {'ΔRet':>14}  {'ΔDD':>8}  "
              f"{'ΔTP':>7}  {'StressFrac':>11}")
        print('─'*120)

        for rule in sorted_rules:
            if rule['label'] == 'fixed_35':
                continue
            r = window_results[rule['label']]['realistic']
            d_ret = r['mean_ret'] - baseline['mean_ret']
            d_dd  = r['worst_dd'] - baseline['worst_dd']
            d_tp  = r['tp'] - baseline['tp']
            sf    = window_results[rule['label']]['_stress_frac']
            desc = rule_description(rule)
            better = "▲" if d_ret > 0 else "▼"
            sf_str = f"{sf:.0f}% stress" if sf > 0 else "—"
            print(f"{rule['label']:<16}  {desc:<22}  {d_ret:>+13,.1f}%  "
                  f"{d_dd:>+7.1f}%  {d_tp:>+6.1f}%  {sf_str:>11}  {better}")

    return window_results


# ---- Main --------------------------------------------------------------------

def main():
    print('='*140)
    print("MONTE CARLO — Worst-Case Entry + Hybrid Regime SL")
    print('='*140)
    print(f"Strategy : 30 DTE | TP=+30% | Hard=-50%@day15 | SL=rule-dependent")
    print(f"Slippage : entry -1.0% | TP 0% (limit sell) | SL -1.3% | Hard -0.5%")
    print(f"           NET_TP={NET_TP:+.3f}  NET_HARD={NET_HARD_SELL:+.3f}")
    print(f"Alloc    : 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)")
    print(f"MaxPos   : {MAX_POSITIONS}  |  Threshold: {PRIMARY_THRESHOLD}+/{OVERFLOW_THRESHOLD}-74")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"TP sigma : {TP_SIGMA:.3f}")

    n_fixed  = sum(1 for r in RULES if r['type'] == 'fixed')
    n_linear = sum(1 for r in RULES if r['type'] == 'linear')
    n_hybrid = sum(1 for r in RULES if r['type'] == 'hybrid')
    print(f"Rules    : {len(RULES)} total ({n_fixed} fixed + {n_linear} linear + {n_hybrid} hybrid)")
    print(f"Windows  : {len(WINDOWS)}")
    print(f"Focus    : 2022→now worst-case entry scenario")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}")

    all_results = {}

    # Run primary window first (2022→now)
    primary_label, d_start, d_end = WINDOWS[0]
    all_results[primary_label] = run_window(primary_label, d_start, d_end, version,
                                             is_primary=True)

    # Run individual years for context
    for label, d_start, d_end in WINDOWS[1:]:
        all_results[label] = run_window(label, d_start, d_end, version, is_primary=False)

    # ==== CROSS-WINDOW SUMMARIES =============================================

    year_labels = [l for l, _, _ in WINDOWS]

    # ---- Cross-window return grid (Realistic) --------------------------------
    print('\n' + '='*160)
    print("CROSS-WINDOW — Realistic Mode — Mean Return")
    print('='*160)

    # Consistency ranking
    rule_ranks = {r['label']: [] for r in RULES}
    for label in year_labels:
        ranked = sorted(RULES, key=lambda r: -all_results[label][r['label']]['realistic']['mean_ret'])
        for rank, rule in enumerate(ranked):
            rule_ranks[rule['label']].append(rank + 1)

    avg_ranks = [(rl, sum(ranks)/len(ranks)) for rl, ranks in rule_ranks.items()]
    avg_ranks.sort(key=lambda x: x[1])

    header = f"{'Rule':<16}  {'Desc':<22}  "
    for l in year_labels:
        header += f"{l:>14}  "
    header += f"{'AvgRk':>6}"
    print(header)
    print('─'*160)

    for rl, avg_rank in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        desc = rule_description(rule)
        row = f"{rl:<16}  {desc:<22}  "
        for label in year_labels:
            ret = all_results[label][rl]['realistic']['mean_ret']
            row += f"{ret:>+13,.1f}%  "
        row += f"  {avg_rank:>4.1f}"
        marker = "  ★" if rl == 'fixed_35' else ""
        print(row + marker)

    # ---- Worst DD grid -------------------------------------------------------
    print('\n' + '='*140)
    print("CROSS-WINDOW — Realistic Mode — Worst Drawdown")
    print('='*140)
    header = f"{'Rule':<16}  {'Desc':<22}  "
    for l in year_labels:
        header += f"{l:>8}  "
    header += f"{'MaxAll':>8}"
    print(header)
    print('─'*140)

    for rl, _ in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        desc = rule_description(rule)
        row = f"{rl:<16}  {desc:<22}  "
        dds = []
        for label in year_labels:
            dd = all_results[label][rl]['realistic']['worst_dd']
            dds.append(dd)
            row += f"{dd:>7.1f}%  "
        row += f"{max(dds):>7.1f}%"
        print(row)

    # ---- 2022 Conservative (the bear floor) ----------------------------------
    print('\n' + '='*120)
    print("2022 BEAR YEAR — Conservative Mode (lower bound)")
    print('='*120)
    print(f"{'Rk':>3}  {'Rule':<16}  {'Description':<22}  {'MeanRet':>14}  "
          f"{'WorstDD':>8}  {'TP%':>6}  {'P(col)':>7}")
    print('─'*120)

    sorted_2022 = sorted(RULES,
        key=lambda r: -all_results['2022'][r['label']]['conservative']['mean_ret'])
    for rank, rule in enumerate(sorted_2022, 1):
        r = all_results['2022'][rule['label']]['conservative']
        desc = rule_description(rule)
        marker = " ★" if rule['label'] == 'fixed_35' else ""
        print(f"{rank:>3}  {rule['label']:<16}  {desc:<22}  {r['mean_ret']:>+13,.1f}%  "
              f"{r['worst_dd']:>7.1f}%  {r['tp']:>5.1f}%  {r['p_coll']:>6.1f}%{marker}")

    # ---- 2022→now Conservative equity curve ----------------------------------
    print('\n' + '='*140)
    print("2022→now CONTINUOUS — Conservative Mode — Year-end equity from $50k")
    print("(This is the absolute floor: worst possible entry + worst collision assumption)")
    print('='*140)

    sorted_by_cons = sorted(RULES,
        key=lambda r: -all_results['2022→now'][r['label']]['conservative'].get('mean_final', 0))
    header = f"{'Rk':>3}  {'Rule':<16}  {'Desc':<22}  "
    for yr in SNAPSHOT_YEARS:
        header += f"{'$'+str(yr):>14}  "
    header += f"{'TotalRet':>14}"
    print(header)
    print('─'*140)

    for rank, rule in enumerate(sorted_by_cons, 1):
        rm = all_results['2022→now'][rule['label']]['conservative']
        ye = rm.get('year_equity', {})
        desc = rule_description(rule)
        marker = " ★" if rule['label'] == 'fixed_35' else ""
        row = f"{rank:>3}  {rule['label']:<16}  {desc:<22}  "
        for yr in SNAPSHOT_YEARS:
            val = ye.get(yr, 0)
            if val >= 1_000_000_000:
                row += f"${val/1e9:>11,.1f}B  "
            elif val >= 1_000_000:
                row += f"${val/1e6:>11,.1f}M  "
            elif val >= 1_000:
                row += f"${val/1e3:>11,.1f}K  "
            else:
                row += f"${val:>12,.0f}  "
        row += f"{rm['mean_ret']:>+13,.1f}%{marker}"
        print(row)

    # ---- Break-even analysis -------------------------------------------------
    print('\n' + '='*120)
    print("BREAK-EVEN — per rule")
    print('='*120)
    print(f"{'Rule':<16}  {'Desc':<22}  {'AvgSL%':>7}  {'NetSL':>7}  {'BE_TP':>7}  "
          f"{'2022_TP':>8}  {'Margin':>8}  {'WorstTP':>8}  {'Margin':>8}")
    print('─'*120)

    for rl, _ in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        desc = rule_description(rule)
        avg_sl_val = all_results['2022→now'][rl]['_avg_sl']
        net_sl_val = abs(net_sl_from_pct(avg_sl_val / 100))
        be_tp = net_sl_val / (NET_TP + net_sl_val) * 100 if (NET_TP + net_sl_val) > 0 else 99

        tp_2022 = all_results['2022'][rl]['realistic']['tp']
        margin_2022 = tp_2022 - be_tp

        worst_tp = min(all_results[l][rl]['realistic']['tp'] for l in year_labels)
        worst_margin = worst_tp - be_tp

        print(f"{rl:<16}  {desc:<22}  {avg_sl_val:>6.1f}%  {-net_sl_val*100:>+6.1f}%  "
              f"{be_tp:>6.1f}%  {tp_2022:>7.1f}%  {margin_2022:>+7.1f}pp  "
              f"{worst_tp:>7.1f}%  {worst_margin:>+7.1f}pp")

    # ---- Final recommendation ------------------------------------------------
    print('\n' + '='*140)
    print("TOP 10 — Sorted by 2022→now Realistic return (the worst-case entry metric)")
    print('='*140)

    sorted_by_primary = sorted(RULES,
        key=lambda r: -all_results['2022→now'][r['label']]['realistic']['mean_ret'])

    for i, rule in enumerate(sorted_by_primary[:10]):
        rl = rule['label']
        desc = rule_description(rule)
        r_primary   = all_results['2022→now'][rl]['realistic']
        c_primary   = all_results['2022→now'][rl]['conservative']
        r_2022      = all_results['2022'][rl]['realistic']
        c_2022      = all_results['2022'][rl]['conservative']

        print(f"\n  #{i+1}  {rl:<16}  {desc}")
        print(f"       2022→now real:  ret={r_primary['mean_ret']:>+,.1f}%  DD={r_primary['worst_dd']:.1f}%")
        print(f"       2022→now cons:  ret={c_primary['mean_ret']:>+,.1f}%  DD={c_primary['worst_dd']:.1f}%")
        print(f"       2022 bear real: ret={r_2022['mean_ret']:>+,.1f}%  DD={r_2022['worst_dd']:.1f}%  "
              f"TP={r_2022['tp']:.1f}%")
        print(f"       2022 bear cons: ret={c_2022['mean_ret']:>+,.1f}%  DD={c_2022['worst_dd']:.1f}%  "
              f"TP={c_2022['tp']:.1f}%")

    # ---- Hybrid vs Fixed vs Linear verdict -----------------------------------
    print('\n' + '='*140)
    print("VERDICT — Best in each category (2022→now Realistic)")
    print('='*140)

    best_fixed  = max([r for r in RULES if r['type'] == 'fixed'],
                      key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_linear = max([r for r in RULES if r['type'] == 'linear'],
                      key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_hybrid = max([r for r in RULES if r['type'] == 'hybrid'],
                      key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])

    for cat, rule in [('FIXED', best_fixed), ('LINEAR', best_linear), ('HYBRID', best_hybrid)]:
        rl = rule['label']
        desc = rule_description(rule)
        rp = all_results['2022→now'][rl]['realistic']
        cp = all_results['2022→now'][rl]['conservative']
        r22 = all_results['2022'][rl]['conservative']
        print(f"\n  Best {cat}: {rl}  ({desc})")
        print(f"    2022→now real:  ret={rp['mean_ret']:>+,.1f}%  DD={rp['worst_dd']:.1f}%")
        print(f"    2022→now cons:  ret={cp['mean_ret']:>+,.1f}%  DD={cp['worst_dd']:.1f}%")
        print(f"    2022 cons:      ret={r22['mean_ret']:>+,.1f}%  DD={r22['worst_dd']:.1f}%")

    # Per-year head-to-head
    print(f"\n{'Window':<12}  {'Best Fixed':<16} {'ret':>14}  "
          f"{'Best Linear':<16} {'ret':>14}  {'Best Hybrid':<16} {'ret':>14}")
    print('─'*120)
    for label in year_labels:
        bf_ret = all_results[label][best_fixed['label']]['realistic']['mean_ret']
        bl_ret = all_results[label][best_linear['label']]['realistic']['mean_ret']
        bh_ret = all_results[label][best_hybrid['label']]['realistic']['mean_ret']
        best_val = max(bf_ret, bl_ret, bh_ret)
        bf_mark = " ◄" if bf_ret == best_val else ""
        bl_mark = " ◄" if bl_ret == best_val else ""
        bh_mark = " ◄" if bh_ret == best_val else ""
        print(f"{label:<12}  {best_fixed['label']:<16} {bf_ret:>+13,.1f}%{bf_mark}  "
              f"{best_linear['label']:<16} {bl_ret:>+13,.1f}%{bl_mark}  "
              f"{best_hybrid['label']:<16} {bh_ret:>+13,.1f}%{bh_mark}")


if __name__ == '__main__':
    main()
