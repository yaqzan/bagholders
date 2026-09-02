"""
Regime Impact Analysis
======================
Measures how VIX level, regime composite, market breadth, and the regime
multiplier correlate with actual signal win rates -- separately for calls
(HIGH score >=70) and puts (LOW score <=25).

Usage:
    python analyze_regime_impact.py [days]          # default 1825 (5y)
    python analyze_regime_impact.py 730             # 2y
    python analyze_regime_impact.py --symbol AAPL   # single symbol

Output tables
-------------
  1. Win rates by VIX level bucket  (calls vs puts)
  2. Win rates by VIX sub-score     (0-100 from regime engine)
  3. Win rates by regime composite  (STRESS / CAUTION / NEUTRAL / HEALTHY / BULL)
  4. Win rates by breadth score
  5. Win rates by regime multiplier band
  6. Regression summary: does the multiplier predict win rate?
  7. Counterfactual: what would WR look like with no regime adjustment (mult=1.0)?
"""

import sys
import math
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

# ── imports from project ────────────────────────────────────────────────────
from database.models.core import Score, MarketRegime, MarketBreadth, AlgorithmVersion
from database.models.technical import PriceHistory
from assess_scores import (
    extract_peaks,
    _peak_side,
    _realized_vol_pct,
    _swing_walk,
    PERIODS,
    PERIOD_MAX_W,
    SWING_REFERENCE_DAYS,
)

# ── Analysis periods to display ─────────────────────────────────────────────
DISPLAY_PERIODS = ['7d', '15d', '30d', '60d']

# ── VIX level buckets ───────────────────────────────────────────────────────
VIX_BUCKETS = [
    (0,   13,  '<13  (calm)'),
    (13,  17,  '13-17'),
    (17,  20,  '17-20'),
    (20,  25,  '20-25'),
    (25,  30,  '25-30'),
    (30,  40,  '30-40'),
    (40, 999,  '40+  (panic)'),
]

# ── VIX sub-score buckets (0-100 from regime engine) ────────────────────────
VIX_SCORE_BUCKETS = [
    (0,   20,  '0-20  (highest fear)'),
    (20,  40,  '20-40'),
    (40,  60,  '40-60 (neutral)'),
    (60,  80,  '60-80'),
    (80, 101,  '80-100 (lowest fear)'),
]

# ── Regime composite buckets ─────────────────────────────────────────────────
COMPOSITE_BUCKETS = [
    (0,   15,  'STRESS   (0-15)'),
    (15,  30,  'CAUTION  (15-30)'),
    (30,  45,  'CAUTION  (30-45)'),
    (45,  60,  'NEUTRAL  (45-60)'),
    (60,  75,  'HEALTHY  (60-75)'),
    (75, 101,  'BULL     (75+)'),
]

# ── Regime multiplier buckets ────────────────────────────────────────────────
MULT_BUCKETS = [
    (0.68, 0.75, '0.70 STRESS'),
    (0.75, 0.82, '0.78 CAUTION-LO'),
    (0.82, 0.92, '0.88 CAUTION-HI'),
    (0.92, 1.00, '1.00 NEUTRAL'),
    (1.00, 1.03, '1.05 HEALTHY'),
    (1.03, 1.11, '1.10 BULL'),
]

# ── Breadth score buckets ────────────────────────────────────────────────────
BREADTH_BUCKETS = [
    (0,   25,  '0-25  (weak)'),
    (25,  40,  '25-40'),
    (40,  55,  '40-55 (neutral)'),
    (55,  70,  '55-70'),
    (70, 101,  '70+   (strong)'),
]


# ── Barrier walk (reused from assess_scores) ─────────────────────────────────

def _peak_result(peak, price_rows):
    """Run barrier walk on one peak. Returns dict with swing results or None."""
    date_idx = {r[0]: i for i, r in enumerate(price_rows)}
    base_idx = date_idx.get(peak.date)
    if base_idx is None:
        return None
    dates  = [r[0] for r in price_rows]
    closes = [r[1] for r in price_rows]
    highs  = [r[2] for r in price_rows]
    lows   = [r[3] for r in price_rows]
    side = _peak_side(peak.overall)
    vol_pct = _realized_vol_pct(closes, base_idx)
    swing = _swing_walk(closes, base_idx, side, vol_pct, highs=highs, lows=lows, dates=dates)
    if swing is None:
        return None
    return {
        'symbol': peak.symbol_id,
        'date':   peak.date,
        'score':  peak.overall,
        'side':   side,
        'swing':  swing,
    }


# ── Price cache ──────────────────────────────────────────────────────────────

def _load_price_cache(symbols):
    price_cache = {}
    for sym in symbols:
        rows = list(
            PriceHistory.select(
                PriceHistory.date, PriceHistory.close,
                PriceHistory.high,  PriceHistory.low,
            )
            .where(PriceHistory.symbol == sym)
            .order_by(PriceHistory.date)
            .tuples()
        )
        price_cache[sym] = [(r[0], float(r[1]), float(r[2]), float(r[3])) for r in rows]
    return price_cache


# ── Regime / breadth cache ────────────────────────────────────────────────────

def _load_regime_cache(cutoff):
    """Returns {date: {vix_close, vix_10d_change, vix_score, composite, multiplier,
                        breadth, market_trend_score, spy_close, spy_ema50, spy_ema200}}"""
    regimes = list(
        MarketRegime.select(
            MarketRegime.date,
            MarketRegime.vix_close,
            MarketRegime.vix_10d_change,
            MarketRegime.vix_score,
            MarketRegime.regime_composite,
            MarketRegime.regime_multiplier,
            MarketRegime.market_trend_score,
            MarketRegime.spy_close,
            MarketRegime.spy_ema50,
            MarketRegime.spy_ema200,
        ).where(MarketRegime.date >= cutoff)
    )
    breadths = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(MarketBreadth.date >= cutoff)
    )
    brd_map = {b.date: float(b.breadth_score) for b in breadths if b.breadth_score is not None}

    cache = {}
    for r in regimes:
        cache[r.date] = {
            'vix_close':          float(r.vix_close)           if r.vix_close           is not None else None,
            'vix_10d_change':     float(r.vix_10d_change)      if r.vix_10d_change      is not None else None,
            'vix_score':          float(r.vix_score)            if r.vix_score           is not None else None,
            'composite':          float(r.regime_composite)     if r.regime_composite    is not None else None,
            'multiplier':         float(r.regime_multiplier)    if r.regime_multiplier   is not None else None,
            'market_trend_score': float(r.market_trend_score)  if r.market_trend_score  is not None else None,
            'spy_close':          float(r.spy_close)            if r.spy_close           is not None else None,
            'spy_ema50':          float(r.spy_ema50)            if r.spy_ema50           is not None else None,
            'spy_ema200':         float(r.spy_ema200)           if r.spy_ema200          is not None else None,
            'breadth':            brd_map.get(r.date),
        }
    return cache


# ── Win-rate table builder ────────────────────────────────────────────────────

def _win_rates(entries):
    """entries: list of swing result dicts filtered to one stratum + side.
    Returns {period: win_rate_pct or None}"""
    out = {}
    for label in DISPLAY_PERIODS:
        per = [e['swing'][label] for e in entries if label in e.get('swing', {})]
        if len(per) < 5:
            out[label] = None
        else:
            wins = sum(1 for s in per if s['result'] == 'win')
            out[label] = round(wins / len(per) * 100, 1)
    return out


def _avg_ret(entries, period='30d'):
    per = [e['swing'][period] for e in entries if period in e.get('swing', {})]
    if len(per) < 5:
        return None
    return round(float(np.mean([s['exit_ret'] for s in per])), 2)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _wr_color(wr):
    if wr is None:
        return f"{Fore.WHITE}  -- {Style.RESET_ALL}"
    if wr >= 65:
        return f"{Fore.GREEN}{wr:5.1f}{Style.RESET_ALL}"
    if wr >= 52:
        return f"{Fore.YELLOW}{wr:5.1f}{Style.RESET_ALL}"
    return f"{Fore.RED}{wr:5.1f}{Style.RESET_ALL}"


def _ret_color(ret):
    if ret is None:
        return f"{Fore.WHITE}    -- {Style.RESET_ALL}"
    if ret > 1.0:
        return f"{Fore.GREEN}{ret:+6.2f}{Style.RESET_ALL}"
    if ret > 0:
        return f"{Fore.YELLOW}{ret:+6.2f}{Style.RESET_ALL}"
    return f"{Fore.RED}{ret:+6.2f}{Style.RESET_ALL}"


def _print_strat_table(title, buckets, bucket_fn, results_with_regime):
    """
    Generic stratification table.
    buckets      : [(lo, hi, label), ...]
    bucket_fn    : fn(regime_dict) -> value to bucket on (float or None)
    results_with_regime: list of (result_dict, regime_dict)
    """
    calls = [(r, reg) for r, reg in results_with_regime if r['side'] == 'low']  # low=calls
    puts  = [(r, reg) for r, reg in results_with_regime if r['side'] == 'high'] # high=puts

    col_w = 12
    hdr_period = ''.join(f"{'WR'+p:>{col_w}}" for p in DISPLAY_PERIODS)

    print(f"\n{Fore.CYAN}{'═'*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{title}{Style.RESET_ALL}")
    print(f"{'Bucket':<26}  {'Side':<6}  {'N':>5}  {hdr_period}  {'EV30d':>7}")
    print(f"{'─'*90}")

    for lo, hi, label in buckets:
        for side_label, side_pairs in [('CALL', calls), ('PUT', puts)]:
            grp = [r for r, reg in side_pairs
                   if reg is not None
                   and bucket_fn(reg) is not None
                   and lo <= bucket_fn(reg) < hi]
            n = len(grp)
            wrs = _win_rates(grp)
            ev = _avg_ret(grp, '30d')
            wr_str = ''.join(
                f"{_wr_color(wrs.get(p)):>{col_w}}" for p in DISPLAY_PERIODS
            )
            n_str = f"{Fore.WHITE}{n:5d}{Style.RESET_ALL}" if n >= 5 else f"{Fore.WHITE}{n:5d}*{Style.RESET_ALL}"
            side_col = f"{Fore.GREEN}{side_label}{Style.RESET_ALL}" if side_label == 'CALL' else f"{Fore.MAGENTA}{side_label}{Style.RESET_ALL}"
            print(f"{label:<26}  {side_col}  {n_str}  {wr_str}  {_ret_color(ev)}")
        print(f"{'─'*90}")


# ── Multiplier correlation ────────────────────────────────────────────────────

def _print_multiplier_correlation(results_with_regime):
    """Pearson correlation between regime multiplier and win (binary) at each period."""
    print(f"\n{Fore.CYAN}{'═'*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Multiplier <-> Win-rate Pearson Correlation{Style.RESET_ALL}")
    print(f"Positive = higher multiplier -> more wins (suppression in stress justified)")
    print(f"Negative = higher multiplier -> fewer wins (suppression harmful)")
    print(f"{'Period':<10}  {'CALL corr':>12}  {'PUT corr':>12}  {'ALL corr':>12}")
    print('─' * 50)

    calls = [(r, reg) for r, reg in results_with_regime
             if r['side'] == 'low' and reg and reg.get('multiplier') is not None]
    puts  = [(r, reg) for r, reg in results_with_regime
             if r['side'] == 'high' and reg and reg.get('multiplier') is not None]
    allp  = calls + puts

    for period in DISPLAY_PERIODS:
        def _pairs(grp):
            pairs = []
            for r, reg in grp:
                sw = r['swing'].get(period)
                if sw and sw.get('result'):
                    win = 1 if sw['result'] == 'win' else 0
                    pairs.append((reg['multiplier'], win))
            return pairs

        def _corr(pairs):
            if len(pairs) < 10:
                return None
            xs, ys = zip(*pairs)
            c = np.corrcoef(xs, ys)[0, 1]
            return round(c, 3) if not np.isnan(c) else None

        cc = _corr(_pairs(calls))
        pc = _corr(_pairs(puts))
        ac = _corr(_pairs(allp))

        def _fmt(c):
            if c is None:
                return f"{'--':>12}"
            col = Fore.GREEN if c > 0.05 else (Fore.RED if c < -0.05 else Fore.YELLOW)
            return f"{col}{c:>12.3f}{Style.RESET_ALL}"

        print(f"{period:<10}  {_fmt(cc)}  {_fmt(pc)}  {_fmt(ac)}")


# ── Counterfactual: what if mult=1.0 (no regime adjustment)? ─────────────────

def _print_counterfactual(results_with_regime):
    """
    For signals where a regime adjustment was applied (mult ≠ 1.0), compare:
      - actual  win rates  (scores as-stored, post-adjustment)
      - baseline win rates (same peaks, no filtering change -- shows EV as-is)

    Also shows VIX bins where the current multiplier <1.0 (suppressed) but
    win rates are actually HIGHER than average -- i.e. suppression hurts.
    """
    print(f"\n{Fore.CYAN}{'═'*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Suppression Impact: Win Rate in Stressed (mult<1) vs Healthy (mult>=1) Regimes{Style.RESET_ALL}")
    print(f"{'Side':<6}  {'Regime':<18}  {'N':>5}  " +
          ''.join(f"{'WR'+p:>10}" for p in DISPLAY_PERIODS) + f"  {'EV30d':>7}")
    print('─' * 80)

    for side_label, side_key in [('CALL', 'low'), ('PUT', 'high')]:
        side_color = Fore.GREEN if side_label == 'CALL' else Fore.MAGENTA
        grp = [(r, reg) for r, reg in results_with_regime
               if r['side'] == side_key and reg and reg.get('multiplier') is not None]

        stressed  = [r for r, reg in grp if reg['multiplier'] < 0.95]
        neutral   = [r for r, reg in grp if 0.95 <= reg['multiplier'] < 1.03]
        healthy   = [r for r, reg in grp if reg['multiplier'] >= 1.03]

        for regime_label, subset in [
            ('stressed  (<0.95)', stressed),
            ('neutral  (0.95-1.03)', neutral),
            ('healthy   (>=1.03)', healthy),
        ]:
            n = len(subset)
            wrs = _win_rates(subset)
            ev = _avg_ret(subset, '30d')
            wr_str = ''.join(f"{_wr_color(wrs.get(p)):>10}" for p in DISPLAY_PERIODS)
            n_str = f"{n:5d}" if n >= 5 else f"{n:5d}*"
            print(f"{side_color}{side_label:<6}{Style.RESET_ALL}  "
                  f"{regime_label:<18}  {n_str}  {wr_str}  {_ret_color(ev)}")
        print('─' * 80)


# ── VIX direction: does rising VIX help or hurt? ─────────────────────────────

def _print_vix_trend_table(results_with_regime):
    """
    Split by VIX 10-day change direction: rising vs falling VIX.
    Fetches vix_10d_change from MarketRegime directly.
    """
    print(f"\n{Fore.CYAN}{'═'*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}VIX Trend (10d change) vs Win Rate{Style.RESET_ALL}")
    print(f"{'Trend bucket':<28}  {'Side':<6}  {'N':>5}  " +
          ''.join(f"{'WR'+p:>10}" for p in DISPLAY_PERIODS) + f"  {'EV30d':>7}")
    print('─' * 80)

    TREND_BUCKETS = [
        (-999, -15,  'Falling fast  (<-15%)'),
        (-15,   -5,  'Falling       (-15 to -5%)'),
        (-5,     5,  'Flat          (-5 to +5%)'),
        (5,     15,  'Rising        (+5 to +15%)'),
        (15,   999,  'Spiking       (>+15%)'),
    ]

    calls = [(r, reg) for r, reg in results_with_regime if r['side'] == 'low']
    puts  = [(r, reg) for r, reg in results_with_regime if r['side'] == 'high']

    for lo, hi, label in TREND_BUCKETS:
        for side_label, side_pairs in [('CALL', calls), ('PUT', puts)]:
            grp = [r for r, reg in side_pairs
                   if reg and reg.get('vix_10d_change') is not None
                   and lo <= reg['vix_10d_change'] < hi]
            wrs = _win_rates(grp)
            ev = _avg_ret(grp, '30d')
            wr_str = ''.join(f"{_wr_color(wrs.get(p)):>10}" for p in DISPLAY_PERIODS)
            side_col = Fore.GREEN if side_label == 'CALL' else Fore.MAGENTA
            n = len(grp)
            print(f"{label:<28}  {side_col}{side_label:<6}{Style.RESET_ALL}  {n:5d}  {wr_str}  {_ret_color(ev)}")
        print('─' * 80)


# ── Component gradient functions (simulation only) ───────────────────────────

# Gradient params — tuned in simulation, shipped to market_regime.py after validation
_VIX_PIVOT       = 22.0   # VIX level that maps to score 50 (linear version)
_VIX_LEVEL_SCALE = 10.0   # steepness for linear level gradient
_VIX_TREND_SCALE = 15.0   # steepness for 10d % change (shared by both VIX variants)
_SPY_EMA50_SCALE  = 5.0   # % from EMA50 => score transition width
_SPY_EMA200_SCALE = 8.0   # % from EMA200 (slower, wider)

# Log-VIX gradient params
# ln(18) ≈ 2.89: score 50 at VIX 18 (top of the "normal" range)
# scale 0.35 in log-space: VIX 25 -> score ~80, VIX 35 -> score ~96
_VIX_LOG_PIVOT = math.log(18.0)  # = 2.89
_VIX_LOG_SCALE = 0.35            # log-space steepness


def _g_vix(vix_close, vix_10d_change):
    """Linear tanh VIX gradient — high VIX -> high score (fear premium)."""
    if vix_close is None or vix_10d_change is None:
        return None
    level = 50.0 + 50.0 * math.tanh((vix_close - _VIX_PIVOT) / _VIX_LEVEL_SCALE)
    trend = 50.0 + 50.0 * math.tanh(vix_10d_change / _VIX_TREND_SCALE)
    return max(0.0, min(100.0, level * 0.55 + trend * 0.45))


def _g_vix_log(vix_close, vix_10d_change):
    """Log-VIX gradient — uses ln(VIX) to match its skewed distribution.
    VIX is log-normally distributed; this gives finer discrimination at 15-25
    and appropriate saturation at panic extremes (35+).
    Score 50 at VIX 18, ~80 at VIX 25, ~96 at VIX 35.
    """
    if vix_close is None or vix_10d_change is None or vix_close <= 0:
        return None
    level = 50.0 + 50.0 * math.tanh((math.log(vix_close) - _VIX_LOG_PIVOT) / _VIX_LOG_SCALE)
    trend = 50.0 + 50.0 * math.tanh(vix_10d_change / _VIX_TREND_SCALE)
    return max(0.0, min(100.0, level * 0.55 + trend * 0.45))


def _g_vix_legacy(vix_close, vix_10d_change):
    """Legacy band-based VIX score (original, wrong direction) for baseline comparison."""
    from market_regime import _compute_vix_score_legacy
    return _compute_vix_score_legacy(vix_close, vix_10d_change)


def _g_breadth_inv(breadth_score):
    """Breadth inverted — weak breadth -> high score (signals more reliable)."""
    if breadth_score is None:
        return None
    # breadth_score is already a smooth 0-100 composite; linear inversion is exact
    return 100.0 - breadth_score


def _g_trend_inv(spy_close, spy_ema50, spy_ema200):
    """Market trend inverted gradient — below EMA -> high score (stress = better signals)."""
    if None in (spy_close, spy_ema50, spy_ema200):
        return None
    pct50  = (spy_close - spy_ema50)  / spy_ema50  * 100.0
    pct200 = (spy_close - spy_ema200) / spy_ema200 * 100.0
    # Negative pct (below EMA) -> high score; above EMA -> low score
    s50  = max(0.0, min(100.0, 50.0 - 50.0 * math.tanh(pct50  / _SPY_EMA50_SCALE)))
    s200 = max(0.0, min(100.0, 50.0 - 50.0 * math.tanh(pct200 / _SPY_EMA200_SCALE)))
    return s50 * 0.50 + s200 * 0.50


def _components(reg):
    """Return (vix_g, breadth_inv, trend_inv) gradient scores for one reg dict.
    Any can be None if data is missing."""
    return (
        _g_vix(reg.get('vix_close'), reg.get('vix_10d_change')),
        _g_breadth_inv(reg.get('breadth')),
        _g_trend_inv(reg.get('spy_close'), reg.get('spy_ema50'), reg.get('spy_ema200')),
    )


def _weighted_composite(vix_g, brd_g, trd_g, weights):
    """Compute composite from three gradient components and a weight triple (w_v, w_b, w_t)."""
    w_v, w_b, w_t = weights
    parts, total = [], 0.0
    if vix_g is not None and w_v > 0:
        parts.append(vix_g * w_v); total += w_v
    if brd_g is not None and w_b > 0:
        parts.append(brd_g * w_b); total += w_b
    if trd_g is not None and w_t > 0:
        parts.append(trd_g * w_t); total += w_t
    if total == 0:
        return None
    return sum(parts) / total


# ── Full optimization analysis ────────────────────────────────────────────────

def _build_enriched(results_with_regime, vix_fn=None):
    """Return list of (result, reg, vix_g, brd_g, trd_g) — one entry per peak with regime data.
    vix_fn: optional callable(vix_close, vix_10d_change) -> score; defaults to _g_vix."""
    if vix_fn is None:
        vix_fn = _g_vix
    enriched = []
    for r, reg in results_with_regime:
        if reg is None:
            continue
        vix_g = vix_fn(reg.get('vix_close'), reg.get('vix_10d_change'))
        brd_g = _g_breadth_inv(reg.get('breadth'))
        trd_g = _g_trend_inv(reg.get('spy_close'), reg.get('spy_ema50'), reg.get('spy_ema200'))
        enriched.append((r, reg, vix_g, brd_g, trd_g))
    return enriched


def _pearson_mult_win(enriched, period, side, weights):
    """Pearson(composite_multiplier, win) for given side ('low'=call/'high'=put) and weight triple."""
    from market_regime import compute_regime_multiplier
    pairs = []
    for r, reg, vix_g, brd_g, trd_g in enriched:
        if r['side'] != side:
            continue
        comp = _weighted_composite(vix_g, brd_g, trd_g, weights)
        if comp is None:
            continue
        mult = compute_regime_multiplier(comp)
        sw = r['swing'].get(period)
        if sw and sw.get('result'):
            pairs.append((mult, 1 if sw['result'] == 'win' else 0))
    if len(pairs) < 20:
        return None
    xs, ys = zip(*pairs)
    c = np.corrcoef(xs, ys)[0, 1]
    return round(c, 4) if not np.isnan(c) else None


def _isolation_corr(enriched, period, side, weights):
    """Pearson(composite_mult, win) for given weight triple."""
    from market_regime import compute_regime_multiplier
    pairs = []
    for r, reg, vix_g, brd_g, trd_g in enriched:
        if r['side'] != side:
            continue
        comp = _weighted_composite(vix_g, brd_g, trd_g, weights)
        if comp is None:
            continue
        mult = compute_regime_multiplier(comp)
        sw = r['swing'].get(period)
        if sw and sw.get('result'):
            pairs.append((mult, 1 if sw['result'] == 'win' else 0))
    if len(pairs) < 20:
        return None
    xs, ys = zip(*pairs)
    c = np.corrcoef(xs, ys)[0, 1]
    return round(c, 4) if not np.isnan(c) else None


def _print_vix_variant_comparison(results_with_regime):
    """Compare three VIX scoring approaches: legacy bands, linear tanh, log tanh."""
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}VIX Variant Comparison — VIX alone at 100% weight{Style.RESET_ALL}")
    print(f"Addresses: does log-VIX outperform linear tanh given VIX's skewed distribution?")
    print(f"\n{'VIX variant':<32}  {'CALL 30d':>10}  {'CALL 60d':>10}  {'PUT 30d':>10}  {'PUT 60d':>10}  {'AVG':>8}")
    print('─' * 85)

    variants = [
        ('Legacy bands (original, wrong dir)', _g_vix_legacy),
        ('Linear tanh  (pivot=22, scale=10)',  _g_vix),
        ('Log tanh     (pivot=ln(18), s=0.35)',_g_vix_log),
    ]

    def _fc(c):
        if c is None: return f"{'--':>10}"
        col = Fore.GREEN if c > 0.02 else (Fore.RED if c < -0.02 else Fore.YELLOW)
        return f"{col}{c:>10.4f}{Style.RESET_ALL}"

    best_variant = None
    best_avg = -99
    for name, vix_fn in variants:
        en = _build_enriched(results_with_regime, vix_fn=vix_fn)
        c30c = _isolation_corr(en, '30d', 'low',  (1, 0, 0))
        c60c = _isolation_corr(en, '60d', 'low',  (1, 0, 0))
        c30p = _isolation_corr(en, '30d', 'high', (1, 0, 0))
        c60p = _isolation_corr(en, '60d', 'high', (1, 0, 0))
        valid = [x for x in [c30c, c60c, c30p, c60p] if x is not None]
        avg = round(sum(valid) / len(valid), 4) if valid else None
        if avg and avg > best_avg:
            best_avg = avg
            best_variant = (name, vix_fn)
        avg_col = Fore.GREEN if avg and avg > 0.02 else (Fore.RED if avg and avg < -0.02 else Fore.YELLOW)
        avg_str = f"{avg_col}{avg:>8.4f}{Style.RESET_ALL}" if avg else f"{'--':>8}"
        print(f"  {name:<30}  {_fc(c30c)}  {_fc(c60c)}  {_fc(c30p)}  {_fc(c60p)}  {avg_str}")

    if best_variant:
        print(f"\n  {Fore.GREEN}Winner: {best_variant[0]}{Style.RESET_ALL}")
    return best_variant


def _print_component_isolation(enriched):
    """Show each component alone at 100% weight — tells us which is most predictive."""
    from market_regime import compute_regime_multiplier
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Component Isolation — Pearson(multiplier, win) at 30d & 60d{Style.RESET_ALL}")
    print(f"Each component tested alone at 100% weight.")
    print(f"Positive = component correctly predicts wins.  Negative = inverted.")
    print(f"\n{'Component':<30}  {'CALL 30d':>10}  {'CALL 60d':>10}  {'PUT 30d':>10}  {'PUT 60d':>10}  {'AVG':>8}")
    print('─' * 80)

    components = [
        ('VIX gradient (inverted)',        (1, 0, 0)),
        ('Breadth inverted (100-score)',    (0, 1, 0)),
        ('Market trend inverted (gradient)',(0, 0, 1)),
        ('Equal weights (1/3 each)',        (1/3, 1/3, 1/3)),
        ('Legacy composite (stored)',       None),    # uses stored composite directly
    ]

    for name, weights in components:
        if weights is None:
            # Use stored regime multiplier directly
            rows = {}
            for period in ['30d', '60d']:
                for side in ['low', 'high']:
                    pairs = []
                    for r, reg, *_ in enriched:
                        if r['side'] != side:
                            continue
                        mult = reg.get('multiplier')
                        if mult is None:
                            continue
                        sw = r['swing'].get(period)
                        if sw and sw.get('result'):
                            pairs.append((mult, 1 if sw['result'] == 'win' else 0))
                    if len(pairs) >= 20:
                        xs, ys = zip(*pairs)
                        c = np.corrcoef(xs, ys)[0, 1]
                        rows[(period, side)] = round(c, 4) if not np.isnan(c) else None
                    else:
                        rows[(period, side)] = None
        else:
            rows = {}
            for period in ['30d', '60d']:
                for side in ['low', 'high']:
                    rows[(period, side)] = _isolation_corr(enriched, period, side, weights)

        c30_call = rows.get(('30d', 'low'))
        c60_call = rows.get(('60d', 'low'))
        c30_put  = rows.get(('30d', 'high'))
        c60_put  = rows.get(('60d', 'high'))
        valid = [x for x in [c30_call, c60_call, c30_put, c60_put] if x is not None]
        avg = round(sum(valid) / len(valid), 4) if valid else None

        def _fc(c):
            if c is None: return f"{'--':>10}"
            col = Fore.GREEN if c > 0.02 else (Fore.RED if c < -0.02 else Fore.YELLOW)
            return f"{col}{c:>10.4f}{Style.RESET_ALL}"

        avg_col = (Fore.GREEN if avg and avg > 0.02 else
                   Fore.RED   if avg and avg < -0.02 else Fore.YELLOW)
        avg_str = f"{avg_col}{avg:>8.4f}{Style.RESET_ALL}" if avg else f"{'--':>8}"
        print(f"  {name:<28}  {_fc(c30_call)}  {_fc(c60_call)}  {_fc(c30_put)}  {_fc(c60_put)}  {avg_str}")


def _grid_search_weights(enriched, period='30d', step=0.10):
    """Grid search weight combinations (w_vix, w_breadth, w_trend), summing to 1.
    Returns sorted list of (avg_corr, w_vix, w_breadth, w_trend, cc, cp)."""
    n = round(1.0 / step)
    results = []
    for iv in range(0, n + 1):
        for ib in range(0, n + 1 - iv):
            it = n - iv - ib
            w = (iv / n, ib / n, it / n)
            cc = _isolation_corr(enriched, period, 'low',  w)
            cp = _isolation_corr(enriched, period, 'high', w)
            valid = [x for x in [cc, cp] if x is not None]
            avg = sum(valid) / len(valid) if valid else None
            if avg is not None:
                results.append((avg, w[0], w[1], w[2], cc, cp))
    results.sort(reverse=True)
    return results


def _print_weight_optimization(enriched):
    """Grid search and print best weight combinations."""
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Weight Optimization — grid search (10% steps, avg of CALL+PUT Pearson at 30d){Style.RESET_ALL}")
    print(f"Components: VIX-gradient | Breadth-inverted | Trend-inverted")
    print(f"\n{'Rank':<6}  {'w_VIX':>7}  {'w_Brd':>7}  {'w_Trd':>7}  {'CALL 30d':>10}  {'PUT 30d':>10}  {'AVG':>8}")
    print('─' * 65)

    best = _grid_search_weights(enriched, period='30d', step=0.10)
    for rank, (avg, wv, wb, wt, cc, cp) in enumerate(best[:15], 1):
        def _fc(c):
            if c is None: return f"{'--':>10}"
            col = Fore.GREEN if c > 0.02 else (Fore.RED if c < -0.02 else Fore.YELLOW)
            return f"{col}{c:>10.4f}{Style.RESET_ALL}"
        avg_col = Fore.GREEN if avg > 0.02 else (Fore.RED if avg < -0.02 else Fore.YELLOW)
        print(f"  #{rank:<4}  {wv:>7.2f}  {wb:>7.2f}  {wt:>7.2f}  {_fc(cc)}  {_fc(cp)}  "
              f"{avg_col}{avg:>8.4f}{Style.RESET_ALL}")

    # Also show 60d top weights
    print(f"\n  (same search at 60d horizon)")
    print(f"  {'w_VIX':>7}  {'w_Brd':>7}  {'w_Trd':>7}  {'CALL 60d':>10}  {'PUT 60d':>10}  {'AVG':>8}")
    print('  ' + '─' * 60)
    best60 = _grid_search_weights(enriched, period='60d', step=0.10)
    for avg, wv, wb, wt, cc, cp in best60[:5]:
        def _fc(c):
            if c is None: return f"{'--':>10}"
            col = Fore.GREEN if c > 0.02 else (Fore.RED if c < -0.02 else Fore.YELLOW)
            return f"{col}{c:>10.4f}{Style.RESET_ALL}"
        avg_col = Fore.GREEN if avg > 0.02 else (Fore.RED if avg < -0.02 else Fore.YELLOW)
        print(f"  {wv:>7.2f}  {wb:>7.2f}  {wt:>7.2f}  {_fc(cc)}  {_fc(cp)}  "
              f"{avg_col}{avg:>8.4f}{Style.RESET_ALL}")

    return best[0] if best else None


def _print_dynamic_weights(enriched):
    """For each VIX regime, find the locally optimal weights — shows if dynamic weighting helps."""
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Dynamic Weighting — optimal static weights per VIX regime{Style.RESET_ALL}")
    print(f"If weights differ substantially across regimes, dynamic weighting is worth implementing.")

    VIX_REGIMES = [
        ('Low VIX   (<18)',   0,  18),
        ('Mid VIX   (18-28)', 18, 28),
        ('High VIX  (28+)',   28, 999),
    ]

    print(f"\n{'Regime':<22}  {'N calls':>8}  {'Best weights (V/B/T)':>22}  {'CALL 30d':>10}  {'PUT 30d':>10}  {'AVG':>8}")
    print('─' * 85)

    for label, lo, hi in VIX_REGIMES:
        sub = [(r, reg, vg, bg, tg) for r, reg, vg, bg, tg in enriched
               if reg.get('vix_close') is not None and lo <= reg['vix_close'] < hi]
        n_calls = sum(1 for r, *_ in sub if r['side'] == 'low')
        best = _grid_search_weights(sub, period='30d', step=0.10)
        if not best:
            print(f"  {label:<20}  {n_calls:>8}  {'insufficient data':>22}")
            continue
        avg, wv, wb, wt, cc, cp = best[0]
        w_str = f"{wv:.0%}/{wb:.0%}/{wt:.0%}"
        def _fc(c):
            if c is None: return f"{'--':>10}"
            col = Fore.GREEN if c > 0.02 else (Fore.RED if c < -0.02 else Fore.YELLOW)
            return f"{col}{c:>10.4f}{Style.RESET_ALL}"
        avg_col = Fore.GREEN if avg > 0.02 else (Fore.RED if avg < -0.02 else Fore.YELLOW)
        print(f"  {label:<20}  {n_calls:>8}  {w_str:>22}  {_fc(cc)}  {_fc(cp)}  "
              f"{avg_col}{avg:>8.4f}{Style.RESET_ALL}")


def _print_best_composite_winrates(enriched, best_weights):
    """Show win-rate stratification with the globally optimal gradient composite."""
    from market_regime import compute_regime_multiplier
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    _, wv, wb, wt, _, _ = best_weights
    print(f"{Fore.CYAN}Win Rates — Optimal Composite  (VIX={wv:.0%} / Breadth={wb:.0%} / Trend={wt:.0%}){Style.RESET_ALL}")
    print(f"{'Composite bucket':<26}  {'Side':<6}  {'N':>5}  " +
          ''.join(f"{'WR'+p:>10}" for p in DISPLAY_PERIODS) + f"  {'EV30d':>7}")
    print('─' * 90)

    BUCKETS = [
        (0,  30,  'CAUTION  (<30)'),
        (30, 45,  'CAUTION  (30-45)'),
        (45, 60,  'NEUTRAL  (45-60)'),
        (60, 75,  'HEALTHY  (60-75)'),
        (75, 101, 'BULL     (75+)'),
    ]
    weights = (wv, wb, wt)
    for lo, hi, label in BUCKETS:
        for side_label, side_key in [('CALL', 'low'), ('PUT', 'high')]:
            grp = []
            for r, reg, vg, bg, tg in enriched:
                if r['side'] != side_key:
                    continue
                comp = _weighted_composite(vg, bg, tg, weights)
                if comp is not None and lo <= comp < hi:
                    grp.append(r)
            n = len(grp)
            wrs = _win_rates(grp)
            ev  = _avg_ret(grp, '30d')
            wr_str = ''.join(f"{_wr_color(wrs.get(p)):>10}" for p in DISPLAY_PERIODS)
            side_col = Fore.GREEN if side_label == 'CALL' else Fore.MAGENTA
            print(f"{label:<26}  {side_col}{side_label:<6}{Style.RESET_ALL}  {n:5d}  {wr_str}  {_ret_color(ev)}")
        print('─' * 90)


def _print_full_optimization(results_with_regime):
    """Master function: VIX variant comparison -> component isolation ->
    grid search -> dynamic weights -> final win-rate table."""
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}OPTIMIZATION MODE{Style.RESET_ALL}")

    # Step 0: find the best VIX variant (legacy / linear tanh / log tanh)
    best_vix = _print_vix_variant_comparison(results_with_regime)
    winning_vix_fn = best_vix[1] if best_vix else _g_vix

    # All subsequent steps use the winning VIX function
    enriched = _build_enriched(results_with_regime, vix_fn=winning_vix_fn)
    n_valid = len(enriched)
    print(f"\n{Fore.CYAN}Using VIX variant: {best_vix[0] if best_vix else 'linear tanh'}"
          f"  ({n_valid} peaks with full regime data){Style.RESET_ALL}")
    print(f"Gradient components: VIX-inverted | Breadth-inverted | Trend-inverted (SPY/EMA gradient)")

    _print_component_isolation(enriched)
    best = _print_weight_optimization(enriched)
    _print_dynamic_weights(enriched)
    if best:
        _print_best_composite_winrates(enriched, best)


# ── Gradient VIX simulation ──────────────────────────────────────────────────

def _simulate_gradient_composite(reg):
    """Recompute regime composite for one date using the new gradient VIX scoring.
    Returns (new_composite, new_multiplier) or (None, None) if data missing."""
    from market_regime import (
        compute_vix_score, compute_regime_composite,
        compute_regime_multiplier, SIGNAL_WEIGHTS,
    )
    vix_close  = reg.get('vix_close')
    vix_trend  = reg.get('vix_10d_change')
    breadth    = reg.get('breadth')
    mkt_trend  = reg.get('market_trend_score')

    new_vix_score = compute_vix_score(vix_close, vix_trend)
    new_composite = compute_regime_composite(new_vix_score, mkt_trend, breadth)
    new_mult      = compute_regime_multiplier(new_composite)
    return new_composite, new_mult


def _print_simulation_comparison(results_with_regime):
    """Side-by-side: old (band) vs new (gradient) VIX scoring.
    Shows Pearson correlation and win-rate table stratified by new composite."""
    from market_regime import _compute_vix_score_legacy, compute_regime_composite, compute_regime_multiplier

    # ── Recompute legacy composite for parity check ──
    enriched = []
    for r, reg in results_with_regime:
        if reg is None:
            continue
        # Legacy composite using old band scoring
        old_vix = _compute_vix_score_legacy(reg.get('vix_close'), reg.get('vix_10d_change'))
        old_comp = compute_regime_composite(old_vix, reg.get('market_trend_score'), reg.get('breadth'))
        old_mult = compute_regime_multiplier(old_comp)

        new_comp, new_mult = _simulate_gradient_composite(reg)
        enriched.append((r, reg, old_mult, new_mult, old_comp, new_comp))

    # ── Pearson correlation: old vs new multiplier -> win ──
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Simulation: Band VIX (legacy) vs Gradient VIX (proposed) -- Pearson corr{Style.RESET_ALL}")
    print(f"Positive = higher multiplier -> more wins  (regime adjustment is HELPFUL)")
    print(f"Negative = higher multiplier -> fewer wins (regime adjustment is HARMFUL)")
    print(f"\n{'Period':<10}  {'OLD CALL':>10}  {'NEW CALL':>10}  {'OLD PUT':>10}  {'NEW PUT':>10}")
    print('─' * 55)

    for period in DISPLAY_PERIODS:
        def _corr(grp, mult_key):
            pairs = []
            for r, reg, old_m, new_m, old_c, new_c in grp:
                m = old_m if mult_key == 'old' else new_m
                if m is None:
                    continue
                sw = r['swing'].get(period)
                if sw and sw.get('result'):
                    pairs.append((m, 1 if sw['result'] == 'win' else 0))
            if len(pairs) < 10:
                return None
            xs, ys = zip(*pairs)
            c = np.corrcoef(xs, ys)[0, 1]
            return round(c, 3) if not np.isnan(c) else None

        calls = [x for x in enriched if x[0]['side'] == 'low']
        puts  = [x for x in enriched if x[0]['side'] == 'high']

        def _fmt(c):
            if c is None:
                return f"{'--':>10}"
            col = Fore.GREEN if c > 0.02 else (Fore.RED if c < -0.02 else Fore.YELLOW)
            return f"{col}{c:>10.3f}{Style.RESET_ALL}"

        print(f"{period:<10}  {_fmt(_corr(calls,'old'))}  {_fmt(_corr(calls,'new'))}"
              f"  {_fmt(_corr(puts,'old'))}  {_fmt(_corr(puts,'new'))}")

    # ── Win-rate table stratified by NEW composite ──
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Simulation: Win Rates by NEW Composite (gradient VIX){Style.RESET_ALL}")
    print(f"Compare to Table 3 (old composite) -- improvement = lower composite -> higher WR{Style.RESET_ALL}")
    print(f"{'Bucket':<26}  {'Side':<6}  {'N':>5}  " +
          ''.join(f"{'WR'+p:>10}" for p in DISPLAY_PERIODS) + f"  {'EV30d':>7}")
    print('─' * 90)

    SIM_COMPOSITE_BUCKETS = [
        (0,  15,  'STRESS   (0-15)'),
        (15, 30,  'CAUTION  (15-30)'),
        (30, 45,  'CAUTION  (30-45)'),
        (45, 60,  'NEUTRAL  (45-60)'),
        (60, 75,  'HEALTHY  (60-75)'),
        (75, 101, 'BULL     (75+)'),
    ]
    for lo, hi, label in SIM_COMPOSITE_BUCKETS:
        for side_label, side_key in [('CALL', 'low'), ('PUT', 'high')]:
            grp = [r for r, reg, om, nm, oc, nc in enriched
                   if r['side'] == side_key and nc is not None and lo <= nc < hi]
            n = len(grp)
            wrs = _win_rates(grp)
            ev  = _avg_ret(grp, '30d')
            wr_str = ''.join(f"{_wr_color(wrs.get(p)):>10}" for p in DISPLAY_PERIODS)
            side_col = Fore.GREEN if side_label == 'CALL' else Fore.MAGENTA
            print(f"{label:<26}  {side_col}{side_label:<6}{Style.RESET_ALL}  {n:5d}  {wr_str}  {_ret_color(ev)}")
        print('─' * 90)

    # ── Delta summary: which buckets improve? ──
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Delta summary: new composite WR30 vs old composite WR30 (CALL side){Style.RESET_ALL}")
    print(f"  Positive delta = gradient VIX correctly re-routes peaks into better-aligned buckets")

    for lo, hi, label in SIM_COMPOSITE_BUCKETS:
        old_grp = [r for r, reg, om, nm, oc, nc in enriched
                   if r['side'] == 'low' and oc is not None and lo <= oc < hi]
        new_grp = [r for r, reg, om, nm, oc, nc in enriched
                   if r['side'] == 'low' and nc is not None and lo <= nc < hi]
        old_wr = _win_rates(old_grp).get('30d')
        new_wr = _win_rates(new_grp).get('30d')
        old_n, new_n = len(old_grp), len(new_grp)
        if old_wr is None and new_wr is None:
            continue
        delta = (new_wr - old_wr) if (old_wr and new_wr) else None
        d_str = (f"{Fore.GREEN}+{delta:.1f}pp{Style.RESET_ALL}" if delta and delta > 0
                 else f"{Fore.RED}{delta:.1f}pp{Style.RESET_ALL}" if delta
                 else '--')
        print(f"  {label:<22} old N={old_n:4d} WR={old_wr or '--':>5}  "
              f"new N={new_n:4d} WR={new_wr or '--':>5}  delta={d_str}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    lookback_days = 1825  # 5 years default
    symbol = None
    do_simulate = False
    do_optimize = False

    i = 0
    while i < len(args):
        if args[i] == '--symbol' and i + 1 < len(args):
            symbol = args[i + 1].upper()
            i += 2
        elif args[i] == '--simulate':
            do_simulate = True
            i += 1
        elif args[i] == '--optimize':
            do_optimize = True
            i += 1
        elif args[i].isdigit():
            lookback_days = int(args[i])
            i += 1
        else:
            i += 1

    print(f"\n{Fore.CYAN}Regime Impact Analysis -- {lookback_days}d lookback"
          + (f"  [{symbol}]" if symbol else "") + f"{Style.RESET_ALL}")
    print(f"Loading peaks...")

    version = AlgorithmVersion.get_or_none(
        AlgorithmVersion.git_message != '',
    )
    # Use active version (highest id with non-empty git_message)
    from peewee import fn
    version = (AlgorithmVersion.select()
               .where(AlgorithmVersion.git_message != '')
               .order_by(AlgorithmVersion.id.desc())
               .first())

    peaks = extract_peaks(symbol=symbol, lookback_days=lookback_days,
                          version=version if version else None)
    print(f"  {len(peaks)} peaks extracted")

    if not peaks:
        print(f"{Fore.RED}No peaks found. Run trader update first.{Style.RESET_ALL}")
        return

    # Load price data
    symbols = set(p.symbol_id for p in peaks)
    print(f"  Loading price history for {len(symbols)} symbols...")
    price_cache = _load_price_cache(symbols)

    # Compute barrier results
    print(f"  Running barrier walk on all peaks...")
    cutoff = date.today() - timedelta(days=lookback_days)
    regime_cache = _load_regime_cache(cutoff)

    results_with_regime = []
    skipped = 0
    for peak in peaks:
        rows = price_cache.get(peak.symbol_id, [])
        r = _peak_result(peak, rows)
        if r is None:
            skipped += 1
            continue
        reg = regime_cache.get(peak.date)
        results_with_regime.append((r, reg))

    total = len(results_with_regime)
    n_calls = sum(1 for r, _ in results_with_regime if r['side'] == 'low')
    n_puts  = sum(1 for r, _ in results_with_regime if r['side'] == 'high')
    n_no_regime = sum(1 for _, reg in results_with_regime if reg is None)

    print(f"  {total} peaks with barrier results  ({n_calls} calls / {n_puts} puts)")
    if n_no_regime:
        print(f"  {Fore.YELLOW}{n_no_regime} peaks have no regime data (run regime-backfill){Style.RESET_ALL}")
    print(f"  {skipped} peaks skipped (insufficient price/vol data)")

    # ── Table 1: VIX level ──────────────────────────────────────────────────
    _print_strat_table(
        "Table 1 -- Win Rate by VIX Level (raw close)",
        VIX_BUCKETS,
        lambda reg: reg.get('vix_close'),
        results_with_regime,
    )

    # ── Table 2: VIX sub-score ───────────────────────────────────────────────
    _print_strat_table(
        "Table 2 -- Win Rate by VIX Sub-Score (0-100, low=fear)",
        VIX_SCORE_BUCKETS,
        lambda reg: reg.get('vix_score'),
        results_with_regime,
    )

    # ── Table 3: Regime composite ────────────────────────────────────────────
    _print_strat_table(
        "Table 3 -- Win Rate by Regime Composite",
        COMPOSITE_BUCKETS,
        lambda reg: reg.get('composite'),
        results_with_regime,
    )

    # ── Table 4: Breadth score ───────────────────────────────────────────────
    _print_strat_table(
        "Table 4 -- Win Rate by Market Breadth Score",
        BREADTH_BUCKETS,
        lambda reg: reg.get('breadth'),
        results_with_regime,
    )

    # ── Table 5: Regime multiplier band ─────────────────────────────────────
    _print_strat_table(
        "Table 5 -- Win Rate by Regime Multiplier Band",
        MULT_BUCKETS,
        lambda reg: reg.get('multiplier'),
        results_with_regime,
    )

    # ── Table 6: VIX trend (rising vs falling) ───────────────────────────────
    _print_vix_trend_table(results_with_regime)

    # ── Table 7: Multiplier <-> win correlation ────────────────────────────────
    _print_multiplier_correlation(results_with_regime)

    # ── Table 8: Suppression impact ─────────────────────────────────────────
    _print_counterfactual(results_with_regime)

    # ── Simulation: gradient VIX vs legacy bands ────────────────────────────
    if do_simulate:
        _print_simulation_comparison(results_with_regime)

    # ── Optimization: component isolation + weight grid search + dynamic ────
    if do_optimize:
        _print_full_optimization(results_with_regime)

    # ── Summary interpretation ───────────────────────────────────────────────
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Interpretation guide{Style.RESET_ALL}")
    print("  If CALL win rates are HIGHER in high-VIX buckets -> current suppression hurts calls")
    print("  If PUT  win rates are HIGHER in high-VIX buckets -> current suppression hurts puts")
    print("  If multiplier correlation is NEGATIVE -> regime adjustment is counter-productive")
    print("  If both sides show no relationship with VIX -> remove VIX from regime entirely")
    print("  Run with --simulate to compare gradient VIX vs legacy band scoring in-memory")
    print("  Run with --optimize for component isolation + weight grid search + dynamic weighting")
    print(f"  * = N<5, treat as noise\n")


if __name__ == '__main__':
    main()
