"""
Monte Carlo — VIX vs Breadth Decomposition
===========================================
Decomposes the h35→40@50 regime-SL strategy to test which signal source
(VIX, breadth, or their combination) drives the SL-switching benefit.

The regime composite blends VIX score + inverted breadth score with dynamic
weights.  This sweep isolates each component and tests alternative weightings
to determine where the predictive power actually lives.

Signal sources tested:
  1. Production composite (h35→40@50 baseline)
  2. VIX score alone (from MarketRegime.vix_score)
  3. Inverted breadth alone (100 - MarketBreadth.breadth_score)
  4. Raw VIX level thresholds (VIX close ≥ 20/22/25/28)
  5. Reweighted composites: 70/30, 50/50, 30/70 VIX/breadth splits
  6. Threshold variations on VIX-only and breadth-only signals

Primary question: does VIX or breadth do the heavy lifting in predicting
when wider SL protects capital?

Windows:
  - 2022→now  (continuous, worst-case entry — PRIMARY FOCUS)
  - Individual years 2022-2025 for context
  - Cross-window summary

Usage: python monte_carlo_vix_breadth_decomp.py
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

# ---- Strategy constants (locked from canonical MC) ---------------------------
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
# Signal source definitions — each returns a per-date dict of 0-100 scores
# ==============================================================================

def load_regime_raw(d_start, d_end):
    """Load raw regime data: composite, vix_score, vix_close, vix_10d_change."""
    rows = list(
        MarketRegime.select(
            MarketRegime.date, MarketRegime.regime_composite,
            MarketRegime.vix_score, MarketRegime.vix_close,
            MarketRegime.vix_10d_change,
        )
        .where(
            MarketRegime.date >= d_start - timedelta(days=60),
            MarketRegime.date <= d_end,
        )
        .order_by(MarketRegime.date)
        .tuples()
    )
    data = {}
    for d, comp, vscore, vclose, v10d in rows:
        data[d] = {
            'composite': float(comp) if comp is not None else None,
            'vix_score': float(vscore) if vscore is not None else None,
            'vix_close': float(vclose) if vclose is not None else None,
            'vix_10d':   float(v10d) if v10d is not None else None,
        }
    return data


def load_breadth_raw(d_start, d_end):
    """Load breadth_score from MarketBreadth table."""
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
    """
    Build per-date signal maps for each source.
    All maps: date -> float (0-100 scale, higher = more stressed/fearful).

    Returns dict of {source_name: {date: score}}.
    """
    maps = {}

    # 1. Production composite (already stored)
    maps['composite'] = {d: v['composite'] for d, v in regime_raw.items()
                         if v['composite'] is not None}

    # 2. VIX score alone (already stored, 0-100, high = fear)
    maps['vix_score'] = {d: v['vix_score'] for d, v in regime_raw.items()
                         if v['vix_score'] is not None}

    # 3. Inverted breadth alone (high = weak breadth = stressed)
    maps['brd_inv'] = {d: 100.0 - breadth_raw[d] for d in breadth_raw}

    # 4. Raw VIX close (not 0-100 scale — used with level thresholds directly)
    maps['vix_close'] = {d: v['vix_close'] for d, v in regime_raw.items()
                         if v['vix_close'] is not None}

    # 5. Reweighted composites: blend vix_score and brd_inv at different ratios
    # Get dates where both signals exist
    common_dates = set(maps['vix_score'].keys()) & set(maps['brd_inv'].keys())

    for vw, bw, name in [(0.70, 0.30, 'vix70_brd30'),
                          (0.50, 0.50, 'vix50_brd50'),
                          (0.30, 0.70, 'vix30_brd70'),
                          (1.00, 0.00, 'vix100'),
                          (0.00, 1.00, 'brd100')]:
        m = {}
        for d in common_dates:
            m[d] = maps['vix_score'][d] * vw + maps['brd_inv'][d] * bw
        maps[name] = m

    return maps


# ==============================================================================
# Rule definitions
# ==============================================================================
# All rules use the h35→40 structure (35% base, 40% stress).
# The difference is WHICH signal source they read and at WHAT threshold.

RULES = [
    # ── Fixed baseline ──
    {'label': 'fixed_35',         'source': None,        'threshold': None,
     'desc': '35% fixed (ref)'},

    # ── Production composite ──
    {'label': 'comp@45',          'source': 'composite', 'threshold': 45,
     'desc': 'composite ≥45'},
    {'label': 'comp@50',          'source': 'composite', 'threshold': 50,
     'desc': 'composite ≥50 ★'},
    {'label': 'comp@55',          'source': 'composite', 'threshold': 55,
     'desc': 'composite ≥55'},
    {'label': 'comp@60',          'source': 'composite', 'threshold': 60,
     'desc': 'composite ≥60'},

    # ── VIX score (0-100, high=fear) ──
    {'label': 'vix_sc@40',        'source': 'vix_score', 'threshold': 40,
     'desc': 'vix_score ≥40'},
    {'label': 'vix_sc@50',        'source': 'vix_score', 'threshold': 50,
     'desc': 'vix_score ≥50'},
    {'label': 'vix_sc@60',        'source': 'vix_score', 'threshold': 60,
     'desc': 'vix_score ≥60'},
    {'label': 'vix_sc@70',        'source': 'vix_score', 'threshold': 70,
     'desc': 'vix_score ≥70'},

    # ── Raw VIX level ──
    {'label': 'vix≥18',           'source': 'vix_close', 'threshold': 18,
     'desc': 'VIX close ≥18'},
    {'label': 'vix≥20',           'source': 'vix_close', 'threshold': 20,
     'desc': 'VIX close ≥20'},
    {'label': 'vix≥22',           'source': 'vix_close', 'threshold': 22,
     'desc': 'VIX close ≥22'},
    {'label': 'vix≥25',           'source': 'vix_close', 'threshold': 25,
     'desc': 'VIX close ≥25'},
    {'label': 'vix≥28',           'source': 'vix_close', 'threshold': 28,
     'desc': 'VIX close ≥28'},

    # ── Inverted breadth (0-100, high=weak breadth=stressed) ──
    {'label': 'brd_inv@40',       'source': 'brd_inv',   'threshold': 40,
     'desc': 'inv_breadth ≥40'},
    {'label': 'brd_inv@50',       'source': 'brd_inv',   'threshold': 50,
     'desc': 'inv_breadth ≥50'},
    {'label': 'brd_inv@55',       'source': 'brd_inv',   'threshold': 55,
     'desc': 'inv_breadth ≥55'},
    {'label': 'brd_inv@60',       'source': 'brd_inv',   'threshold': 60,
     'desc': 'inv_breadth ≥60'},
    {'label': 'brd_inv@70',       'source': 'brd_inv',   'threshold': 70,
     'desc': 'inv_breadth ≥70'},

    # ── Reweighted composites (all at threshold 50) ──
    {'label': 'vix70_b30@50',     'source': 'vix70_brd30', 'threshold': 50,
     'desc': '70%VIX+30%brd ≥50'},
    {'label': 'vix50_b50@50',     'source': 'vix50_brd50', 'threshold': 50,
     'desc': '50%VIX+50%brd ≥50'},
    {'label': 'vix30_b70@50',     'source': 'vix30_brd70', 'threshold': 50,
     'desc': '30%VIX+70%brd ≥50'},
    {'label': 'vix100@50',        'source': 'vix100',      'threshold': 50,
     'desc': '100%VIX ≥50'},
    {'label': 'brd100@50',        'source': 'brd100',      'threshold': 50,
     'desc': '100%brd_inv ≥50'},

    # ── Reweighted at different thresholds ──
    {'label': 'vix100@45',        'source': 'vix100',      'threshold': 45,
     'desc': '100%VIX ≥45'},
    {'label': 'vix100@55',        'source': 'vix100',      'threshold': 55,
     'desc': '100%VIX ≥55'},
    {'label': 'brd100@45',        'source': 'brd100',      'threshold': 45,
     'desc': '100%brd_inv ≥45'},
    {'label': 'brd100@55',        'source': 'brd100',      'threshold': 55,
     'desc': '100%brd_inv ≥55'},
]

SL_BASE   = 0.35
SL_STRESS = 0.40


def score_to_tier(score):
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def get_signal_for_date(signal_map, sorted_dates, d):
    """Get the signal value for a date using most-recent-available lookup."""
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx >= 0:
        return signal_map[sorted_dates[idx]]
    return None


def sl_pct_to_sigma(sl_pct):
    return sl_pct * PREMIUM_MULT / DELTA


def net_sl_from_pct(sl_pct):
    return -sl_pct + SLIP_ENTRY + SLIP_SL


# ---- Data loading (shared) ---------------------------------------------------

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


def precompute_rule_outcomes(bar_data, signal_maps, rule):
    """For a specific rule, evaluate every signal's outcome using that rule's signal source."""
    outcomes = {}
    sl_pcts = []

    if rule['source'] is None:
        # Fixed rule — constant SL
        sl_sigma = sl_pct_to_sigma(SL_BASE)
        net_sl = net_sl_from_pct(SL_BASE)
        for key, bd in bar_data.items():
            kind, exit_bar = evaluate_outcome(bd['bars'], sl_sigma)
            outcomes[key] = {
                'kind': kind, 'exit_bar': exit_bar,
                'premium_pct': bd['premium_pct'],
                'net_sl': net_sl,
            }
            sl_pcts.append(SL_BASE)
    else:
        smap = signal_maps[rule['source']]
        sorted_dates = sorted(smap.keys())
        threshold = rule['threshold']

        for key, bd in bar_data.items():
            _, sig_date = key
            signal_val = get_signal_for_date(smap, sorted_dates, sig_date)

            # Determine SL based on signal vs threshold
            if signal_val is not None and signal_val >= threshold:
                sl_pct = SL_STRESS
            else:
                sl_pct = SL_BASE

            sl_pcts.append(sl_pct)
            sl_sigma = sl_pct_to_sigma(sl_pct)
            kind, exit_bar = evaluate_outcome(bd['bars'], sl_sigma)
            outcomes[key] = {
                'kind': kind, 'exit_bar': exit_bar,
                'premium_pct': bd['premium_pct'],
                'net_sl': net_sl_from_pct(sl_pct),
            }

    avg_sl = sum(sl_pcts) / len(sl_pcts) * 100 if sl_pcts else 0
    stress_frac = sum(1 for p in sl_pcts if p >= SL_STRESS - 0.001) / len(sl_pcts) * 100 if sl_pcts else 0
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
            outcome, pnl = resolve(o['kind'], mode, rng, o['net_sl'])
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


# ---- Window runner -----------------------------------------------------------

def run_window(label, d_start, d_end, version, signal_maps, is_primary=False):
    print(f"\n{'='*150}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})"
          + ("  *** WORST-CASE ENTRY ***" if is_primary else ""))
    print('='*150)

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

    # Print signal source stats for this window
    print("\nSignal source statistics (dates in window):")
    for src_name in ['composite', 'vix_score', 'brd_inv', 'vix_close']:
        if src_name not in signal_maps:
            continue
        vals = [v for d, v in signal_maps[src_name].items()
                if d_start <= d <= d_end and v is not None]
        if vals:
            sorted_v = sorted(vals)
            mn = sum(vals) / len(vals)
            p10 = sorted_v[int(len(sorted_v) * 0.10)]
            p50 = sorted_v[int(len(sorted_v) * 0.50)]
            p90 = sorted_v[int(len(sorted_v) * 0.90)]
            above_50 = sum(1 for v in vals if v >= 50) / len(vals) * 100
            print(f"  {src_name:>12}: mean={mn:5.1f}  p10={p10:5.1f}  p50={p50:5.1f}  "
                  f"p90={p90:5.1f}  ≥50: {above_50:4.0f}%  (N={len(vals)})")

    window_results = {}

    # Progress header
    print(f"\n{'#':>3}  {'Rule':<16}  {'Description':<24}  {'AvgSL':>6}  {'Str%':>5}  "
          f"{'— Realistic —':^36}  {'— Conservative —':^20}")
    print(f"{'':3}  {'':16}  {'':24}  {'':6}  {'':5}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}  {'TP%':>6}  {'SL%':>6}  "
          f"{'MeanRet':>14}  {'WorstDD':>8}")
    print('─' * 150)

    for rule_idx, rule in enumerate(RULES):
        outcomes, avg_sl, stress_frac = precompute_rule_outcomes(
            bar_data, signal_maps, rule)

        rule_results = {}
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
        print(f"{rule_idx+1:>3}  {rule['label']:<16}  {rule['desc']:<24}  {avg_sl:>5.1f}%  "
              f"{stress_frac:>4.0f}%  "
              f"{rl['mean_ret']:>+13,.1f}%  {rl['worst_dd']:>7.1f}%  "
              f"{rl['tp']:>5.1f}%  {rl['sl']:>5.1f}%  "
              f"{cl['mean_ret']:>+13,.1f}%  {cl['worst_dd']:>7.1f}%")

    # ---- Ranked results (Realistic) ------------------------------------------
    print(f"\n{'─'*150}")
    print(f"RANKED — {label} — Realistic (sorted by mean return)")
    print(f"{'─'*150}")
    print(f"{'Rk':>3}  {'Rule':<16}  {'Description':<24}  {'AvgSL':>6}  {'Str%':>5}  "
          f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  "
          f"{'TP%':>6}  {'SL%':>6}  {'P(c)':>5}")
    print('─'*150)

    sorted_rules = sorted(RULES, key=lambda r: -window_results[r['label']]['realistic']['mean_ret'])
    for rank, rule in enumerate(sorted_rules, 1):
        r = window_results[rule['label']]['realistic']
        sf = window_results[rule['label']]['_stress_frac']
        marker = " ★" if rule['label'] == 'comp@50' else ""
        marker2 = " ◆" if rule['label'] == 'fixed_35' else ""
        print(f"{rank:>3}  {rule['label']:<16}  {rule['desc']:<24}  "
              f"{window_results[rule['label']]['_avg_sl']:>5.1f}%  {sf:>4.0f}%  "
              f"{r['mean_ret']:>+13,.1f}%  {r['med_ret']:>+13,.1f}%  "
              f"{r['worst_dd']:>7.1f}%  {r['mean_dd']:>6.1f}%  "
              f"{r['tp']:>5.1f}%  {r['sl']:>5.1f}%  "
              f"{r['p_coll']:>4.1f}%{marker}{marker2}")

    # ---- Year-by-year equity curve (primary window only) ---------------------
    if is_primary:
        for mode in ['conservative', 'realistic']:
            print(f"\n{'─'*150}")
            print(f"EQUITY CURVE — {label} — {mode.capitalize()} — "
                  f"$50k start, year-end portfolio values")
            print(f"{'─'*150}")
            header = f"{'Rk':>3}  {'Rule':<16}  {'Description':<24}  "
            for yr in SNAPSHOT_YEARS:
                header += f"{'$'+str(yr):>14}  "
            header += f"{'TotalRet':>14}"
            print(header)
            print('─'*150)

            sorted_by_final = sorted(RULES,
                key=lambda r: -window_results[r['label']][mode].get('mean_final', 0))

            for rank, rule in enumerate(sorted_by_final, 1):
                rm = window_results[rule['label']][mode]
                ye = rm.get('year_equity', {})
                marker = " ★" if rule['label'] == 'comp@50' else ""
                marker2 = " ◆" if rule['label'] == 'fixed_35' else ""
                row = f"{rank:>3}  {rule['label']:<16}  {rule['desc']:<24}  "
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
                row += f"{rm['mean_ret']:>+13,.1f}%{marker}{marker2}"
                print(row)

    # ---- Delta vs comp@50 (the baseline hybrid) --------------------------------
    baseline = window_results.get('comp@50', {}).get('realistic', {})
    if baseline:
        print(f"\n{'─'*130}")
        print(f"DELTA vs comp@50 — {label} — Realistic")
        print(f"{'─'*130}")
        print(f"{'Rule':<16}  {'Description':<24}  {'ΔRet':>14}  {'ΔDD':>8}  "
              f"{'ΔTP':>7}  {'Str%':>6}  {'Source':>10}")
        print('─'*130)

        for rule in sorted_rules:
            if rule['label'] == 'comp@50':
                continue
            r = window_results[rule['label']]['realistic']
            d_ret = r['mean_ret'] - baseline['mean_ret']
            d_dd  = r['worst_dd'] - baseline['worst_dd']
            d_tp  = r['tp'] - baseline['tp']
            sf    = window_results[rule['label']]['_stress_frac']
            better = "▲" if d_ret > 0 else "▼"
            src = rule['source'] or 'fixed'
            print(f"{rule['label']:<16}  {rule['desc']:<24}  {d_ret:>+13,.1f}%  "
                  f"{d_dd:>+7.1f}%  {d_tp:>+6.1f}%  {sf:>5.0f}%  {src:>10}  {better}")

    return window_results


# ---- SIGNAL SOURCE SUMMARY ---------------------------------------------------

def print_source_summary(all_results, year_labels):
    """Group rules by signal source family and show which family wins."""
    families = {
        'fixed':     [r for r in RULES if r['source'] is None],
        'composite': [r for r in RULES if r['source'] == 'composite'],
        'vix_score': [r for r in RULES if r['source'] == 'vix_score'],
        'vix_level': [r for r in RULES if r['source'] == 'vix_close'],
        'breadth':   [r for r in RULES if r['source'] == 'brd_inv'],
        'reweight':  [r for r in RULES if r['source'] and
                      r['source'] not in ('composite','vix_score','vix_close','brd_inv')],
    }

    print('\n' + '='*130)
    print("SIGNAL SOURCE FAMILY SUMMARY — Best rule per family, Realistic mode")
    print('='*130)
    print(f"{'Family':<12}  {'Best Rule':<16}  {'Desc':<24}  ", end='')
    for l in year_labels:
        print(f"{l:>14}  ", end='')
    print(f"{'AvgRk':>6}")
    print('─'*130)

    # For each family, find the best rule by 2022→now return
    family_bests = []
    for fname, rules in families.items():
        if not rules:
            continue
        best = max(rules,
            key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
        # Compute avg rank across windows
        ranks = []
        for label in year_labels:
            sorted_all = sorted(RULES,
                key=lambda r: -all_results[label][r['label']]['realistic']['mean_ret'])
            for rank, r in enumerate(sorted_all, 1):
                if r['label'] == best['label']:
                    ranks.append(rank)
                    break
        avg_rank = sum(ranks) / len(ranks)
        family_bests.append((fname, best, avg_rank))

    family_bests.sort(key=lambda x: x[2])

    for fname, best, avg_rank in family_bests:
        row = f"{fname:<12}  {best['label']:<16}  {best['desc']:<24}  "
        for label in year_labels:
            ret = all_results[label][best['label']]['realistic']['mean_ret']
            row += f"{ret:>+13,.1f}%  "
        row += f"  {avg_rank:>4.1f}"
        print(row)

    # ---- Per-family: all rules ranked ----
    for fname, rules in families.items():
        if len(rules) <= 1:
            continue
        print(f"\n{'─'*110}")
        print(f"  {fname.upper()} family — all rules, 2022→now Realistic")
        print(f"{'─'*110}")
        print(f"  {'Rule':<16}  {'Desc':<24}  {'Str%':>5}  {'MeanRet':>14}  "
              f"{'WorstDD':>8}  {'2022 Ret':>12}  {'2024 Ret':>12}")
        print(f"  {'─'*106}")

        sorted_fam = sorted(rules,
            key=lambda r: -all_results['2022→now'][r['label']]['realistic']['mean_ret'])
        for rule in sorted_fam:
            r22now = all_results['2022→now'][rule['label']]['realistic']
            sf = all_results['2022→now'][rule['label']]['_stress_frac']
            r22 = all_results.get('2022', {}).get(rule['label'], {}).get('realistic', {})
            r24 = all_results.get('2024', {}).get(rule['label'], {}).get('realistic', {})
            ret22 = r22.get('mean_ret', 0)
            ret24 = r24.get('mean_ret', 0)
            print(f"  {rule['label']:<16}  {rule['desc']:<24}  {sf:>4.0f}%  "
                  f"{r22now['mean_ret']:>+13,.1f}%  {r22now['worst_dd']:>7.1f}%  "
                  f"{ret22:>+11,.1f}%  {ret24:>+11,.1f}%")


# ---- Main --------------------------------------------------------------------

def main():
    print('='*150)
    print("MONTE CARLO — VIX vs Breadth Decomposition")
    print('='*150)
    print(f"Strategy : h35→40 (35% base, 40% stress) — signal source varies per rule")
    print(f"Slippage : entry -1.0% | TP 0% (limit sell) | SL -1.3% | Hard -0.5%")
    print(f"           NET_TP={NET_TP:+.3f}  NET_HARD={NET_HARD_SELL:+.3f}")
    print(f"Alloc    : 85+=15%  80-84=12%  75-79=12%  70-74=5% (overflow)")
    print(f"MaxPos   : {MAX_POSITIONS}  |  Threshold: {PRIMARY_THRESHOLD}+/{OVERFLOW_THRESHOLD}-74")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"TP sigma : {TP_SIGMA:.3f}")
    print(f"Rules    : {len(RULES)} total")
    print(f"Windows  : {len(WINDOWS)}")
    print(f"Question : Does VIX or breadth drive the SL-switching benefit?")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nAlgorithm version: {version.git_commit}")

    # Load all signal data once
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
        print(f"  {name:>14}: {len(m)} dates")

    all_results = {}

    # Run primary window first (2022→now)
    primary_label, d_start, d_end = WINDOWS[0]
    all_results[primary_label] = run_window(
        primary_label, d_start, d_end, version, signal_maps, is_primary=True)

    # Run individual years for context
    for label, d_start, d_end in WINDOWS[1:]:
        all_results[label] = run_window(
            label, d_start, d_end, version, signal_maps, is_primary=False)

    # ---- Cross-window summary ----
    year_labels = [l for l, _, _ in WINDOWS]

    # ---- Cross-window return grid (Realistic) ----
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

    header = f"{'Rule':<16}  {'Desc':<24}  "
    for l in year_labels:
        header += f"{l:>14}  "
    header += f"{'AvgRk':>6}"
    print(header)
    print('─'*170)

    for rl, avg_rank in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        row = f"{rl:<16}  {rule['desc']:<24}  "
        for label in year_labels:
            ret = all_results[label][rl]['realistic']['mean_ret']
            row += f"{ret:>+13,.1f}%  "
        row += f"  {avg_rank:>4.1f}"
        marker = " ★" if rl == 'comp@50' else ""
        marker2 = " ◆" if rl == 'fixed_35' else ""
        print(row + marker + marker2)

    # ---- Worst DD grid ----
    print('\n' + '='*130)
    print("CROSS-WINDOW — Realistic Mode — Worst Drawdown")
    print('='*130)
    header = f"{'Rule':<16}  {'Desc':<24}  "
    for l in year_labels:
        header += f"{l:>8}  "
    header += f"{'MaxAll':>8}"
    print(header)
    print('─'*130)

    for rl, _ in avg_ranks:
        rule = next(r for r in RULES if r['label'] == rl)
        row = f"{rl:<16}  {rule['desc']:<24}  "
        dds = []
        for label in year_labels:
            dd = all_results[label][rl]['realistic']['worst_dd']
            dds.append(dd)
            row += f"{dd:>7.1f}%  "
        row += f"{max(dds):>7.1f}%"
        print(row)

    # ---- Signal source family summary ----
    print_source_summary(all_results, year_labels)

    # ---- VERDICT ----
    print('\n' + '='*130)
    print("VERDICT")
    print('='*130)

    # Find best per source type for 2022→now
    best_overall = max(RULES,
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_vix_only = max([r for r in RULES if r['source'] in ('vix_score', 'vix_close', 'vix100')],
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_brd_only = max([r for r in RULES if r['source'] in ('brd_inv', 'brd100')],
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])
    best_composite = max([r for r in RULES if r['source'] == 'composite'],
        key=lambda r: all_results['2022→now'][r['label']]['realistic']['mean_ret'])

    for tag, rule in [('OVERALL BEST', best_overall),
                      ('BEST COMPOSITE', best_composite),
                      ('BEST VIX-ONLY', best_vix_only),
                      ('BEST BREADTH-ONLY', best_brd_only)]:
        r = all_results['2022→now'][rule['label']]['realistic']
        c = all_results['2022→now'][rule['label']]['conservative']
        r22 = all_results.get('2022', {}).get(rule['label'], {}).get('realistic', {})
        sf = all_results['2022→now'][rule['label']]['_stress_frac']
        print(f"\n  {tag}: {rule['label']}  ({rule['desc']})")
        print(f"    2022→now Realistic: ret={r['mean_ret']:+,.1f}%  DD={r['worst_dd']:.1f}%  "
              f"TP={r['tp']:.1f}%  stress={sf:.0f}%")
        print(f"    2022→now Conservative: ret={c['mean_ret']:+,.1f}%  DD={c['worst_dd']:.1f}%")
        if r22:
            print(f"    2022 bear Realistic: ret={r22.get('mean_ret',0):+,.1f}%  "
                  f"TP={r22.get('tp',0):.1f}%")

    print()


if __name__ == '__main__':
    main()
