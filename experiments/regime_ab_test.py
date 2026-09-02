"""
Regime A/B Test — Iterative regime weighting vs scoring accuracy
================================================================

Tests multiple regime configurations against actual barrier-touch win rates
WITHOUT modifying any DB data.

Methodology:
  1. Load qualifying Score peaks + weight_info['pre_regime'] values
  2. Fetch additional market data (HYG, LQD, TLT, ^SKEW) for F&G proxy
  3. For each variant: re-apply variant multiplier to pre_regime → re-qualify
     peaks (≥70 call / ≤25 put) → barrier walk → win rate table
  4. Print side-by-side comparison across all variants

Variants:
  A  no_regime       — multiplier = 1.0 everywhere (pure scoring, no regime filter)
  B  current         — stored MarketRegime.regime_multiplier (production)
  C  vix_only        — VIX tanh gradient only, no breadth, no F&G
  D  breadth_only    — inverted MarketBreadth score only, no VIX
  E  static_50_50    — fixed 50% VIX + 50% inverted breadth (no sigmoid dynamics)
  F  fear_greed      — VIX + inverted breadth + credit spread + safe haven (F&G proxy)
  G  wider_bands     — current composite but multiplier range 0.55 → 1.15
  H  asymmetric      — suppresses stress more (0.60 floor), neutral in bull (1.05 ceil)
  I  vix_dynamic     — current dynamic VIX weight (sigmoid) but re-derived from scratch

Fear & Greed proxy (variant F) adds 3 CNN F&G components not in production:
  - Credit spread  : HYG / LQD 20d momentum  (low = risk-off = fear → high F&G composite)
  - Safe haven     : TLT 20d return − SPY 20d return (positive = fear)
  - Tail risk      : ^SKEW z-score (high SKEW = tail risk demand = mild fear)

Usage:
    python experiments/regime_ab_test.py [days]
    python experiments/regime_ab_test.py 730        # 2-year lookback
    python experiments/regime_ab_test.py --no-fetch # skip yfinance (use cached)
"""

from __future__ import annotations

import io
import sys as _sys
if hasattr(_sys.stdout, 'buffer'):
    _sys.stdout = io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')

import json
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

# -- project imports ----------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.models.core import Score, MarketRegime, MarketBreadth, AlgorithmVersion
from database.models.technical import PriceHistory
from assess_scores import (
    _peak_side,
    _realized_vol_pct,
    _swing_walk,
    PERIODS,
    PERIOD_MAX_W,
)

# -- constants ----------------------------------------------------------------

DEFAULT_LOOKBACK = 1095   # 3 years
CALL_THRESHOLD   = 70     # score ≥ this → call candidate
PUT_THRESHOLD    = 25     # score ≤ this → put candidate
MIN_N_REPORT     = 15     # minimum peaks per bucket to report win rate

DISPLAY_PERIODS  = ['7d', '15d', '30d', '60d']

# -- multiplier band helpers --------------------------------------------------

def _apply_bands(composite: float, bands: List[Tuple]) -> float:
    """Linear interpolation over (lo, hi, mult_lo, mult_hi) bands."""
    for lo, hi, m_lo, m_hi in bands:
        if lo <= composite < hi:
            t = (composite - lo) / (hi - lo) if hi > lo else 0
            return round(m_lo + t * (m_hi - m_lo), 4)
    return 1.0


# Production multiplier bands
_BANDS_CURRENT = [
    (0,   15,  0.70, 0.70),
    (15,  30,  0.70, 0.78),
    (30,  45,  0.78, 0.88),
    (45,  60,  0.88, 1.00),
    (60,  75,  1.00, 1.05),
    (75, 101,  1.05, 1.10),
]

# Wider bands — more aggressive suppression and amplification
_BANDS_WIDER = [
    (0,   15,  0.55, 0.55),
    (15,  30,  0.55, 0.68),
    (30,  45,  0.68, 0.84),
    (45,  60,  0.84, 1.00),
    (60,  75,  1.00, 1.08),
    (75, 101,  1.08, 1.15),
]

# Wider suppression, capped boost — stronger bear filtering without bull inflation
# Floor: 0.60 (vs current 0.70, G's 0.55) — meaningful suppression in stress
# Ceiling: 1.06 (vs current 1.10, G's 1.15) — minimal boost, avoids score inflation
_BANDS_WIDER_CAPPED = [
    (0,   15,  0.60, 0.60),
    (15,  30,  0.60, 0.72),
    (30,  45,  0.72, 0.86),
    (45,  60,  0.86, 1.00),
    (60,  75,  1.00, 1.03),
    (75, 101,  1.03, 1.06),
]

# Asymmetric — more fear suppression, less bull amplification
_BANDS_ASYMMETRIC = [
    (0,   15,  0.60, 0.60),
    (15,  30,  0.60, 0.75),
    (30,  45,  0.75, 0.90),
    (45,  60,  0.90, 1.00),
    (60,  75,  1.00, 1.03),
    (75, 101,  1.03, 1.05),
]


def _apply_to_score(pre_regime: int, multiplier: float) -> int:
    """Apply regime multiplier symmetrically around 50 (same formula as production)."""
    if multiplier is None or multiplier == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * multiplier
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - multiplier)
    return int(max(0, min(100, round(adj))))


# -- Composite builders for each variant ------------------------------------

def _vix_gradient(vix_close, vix_10d_change) -> Optional[float]:
    """Production tanh VIX gradient (high VIX = high score = amplify signals)."""
    if vix_close is None or vix_10d_change is None:
        return None
    level = 50.0 + 50.0 * math.tanh((vix_close - 22.0) / 10.0)
    trend = 50.0 + 50.0 * math.tanh(vix_10d_change / 15.0)
    return max(0.0, min(100.0, level * 0.55 + trend * 0.45))


def _breadth_inverted(breadth_score) -> Optional[float]:
    """Weak breadth → high composite (signals more reliable in fearful markets)."""
    return (100.0 - float(breadth_score)) if breadth_score is not None else None


def _vix_sigmoid_weight(vix_close) -> float:
    """Dynamic VIX weight: sigmoid centred at 22, scale 0.25. Returns [0.05, 0.95]."""
    if vix_close is None:
        return 0.35
    raw = 1.0 / (1.0 + math.exp(-0.25 * (vix_close - 22.0)))
    return max(0.05, min(0.95, raw))


def _composite_current(reg, fg_cache=None) -> Optional[float]:
    """Current production composite: dynamic VIX/breadth weights via sigmoid."""
    vix_s = _vix_gradient(reg.get('vix_close'), reg.get('vix_10d_change'))
    brd_i = _breadth_inverted(reg.get('breadth'))
    vix_close = reg.get('vix_close')
    w_vix = _vix_sigmoid_weight(vix_close)
    w_brd = 1.0 - w_vix
    parts, total = [], 0.0
    if vix_s is not None:
        parts.append(vix_s * w_vix); total += w_vix
    if brd_i is not None:
        parts.append(brd_i * w_brd); total += w_brd
    if not parts:
        return None
    return max(0.0, min(100.0, sum(parts) / total))


def _composite_vix_only(reg, fg_cache=None) -> Optional[float]:
    return _vix_gradient(reg.get('vix_close'), reg.get('vix_10d_change'))


def _composite_breadth_only(reg, fg_cache=None) -> Optional[float]:
    return _breadth_inverted(reg.get('breadth'))


def _composite_static_5050(reg, fg_cache=None) -> Optional[float]:
    vix_s = _vix_gradient(reg.get('vix_close'), reg.get('vix_10d_change'))
    brd_i = _breadth_inverted(reg.get('breadth'))
    parts, total = [], 0.0
    if vix_s is not None:
        parts.append(vix_s * 0.5); total += 0.5
    if brd_i is not None:
        parts.append(brd_i * 0.5); total += 0.5
    if not parts:
        return None
    return max(0.0, min(100.0, sum(parts) / total))


def _composite_fear_greed(reg, fg_cache=None) -> Optional[float]:
    """Fear & Greed proxy: VIX (40%) + inverted breadth (30%) + credit (15%) + haven (10%) + skew (5%).

    Reads credit_score, haven_score, skew_score directly from the regime dict
    (populated by backfill_regime() into MarketRegime DB columns).
    Falls back to VIX+breadth only when F&G columns are absent (pre-backfill).
    """
    vix_s   = _vix_gradient(reg.get('vix_close'), reg.get('vix_10d_change'))
    brd_i   = _breadth_inverted(reg.get('breadth'))
    # F&G scores come from DB (populated by backfill_regime)
    credit_s = reg.get('credit_score')
    haven_s  = reg.get('haven_score')
    skew_s   = reg.get('skew_score')

    parts, total = [], 0.0
    if vix_s is not None:
        parts.append(vix_s * 0.40); total += 0.40
    if brd_i is not None:
        parts.append(brd_i * 0.30); total += 0.30
    if credit_s is not None:
        parts.append(credit_s * 0.15); total += 0.15
    if haven_s is not None:
        parts.append(haven_s * 0.10); total += 0.10
    if skew_s is not None:
        parts.append(skew_s * 0.05); total += 0.05
    if not parts or total < 0.10:
        return None
    return max(0.0, min(100.0, sum(parts) / total))


# -- Variant registry --------------------------------------------------------

def _make_variant(label: str, composite_fn=None, bands=None, fixed_mult=None):
    """Returns a callable (reg_dict, fg_cache) -> multiplier (float)."""
    if fixed_mult is not None:
        return (label, lambda reg, fg=None: fixed_mult)
    if composite_fn is None:
        raise ValueError("need composite_fn or fixed_mult")
    _bands = bands or _BANDS_CURRENT

    def _fn(reg, fg=None):
        if reg is None:
            return 1.0
        comp = composite_fn(reg, fg)
        if comp is None:
            return 1.0
        return _apply_bands(comp, _bands)

    return (label, _fn)


VARIANTS = [
    _make_variant('A: no_regime',    fixed_mult=1.0),
    _make_variant('B: current',      composite_fn=_composite_current,    bands=_BANDS_CURRENT),
    _make_variant('C: vix_only',     composite_fn=_composite_vix_only,   bands=_BANDS_CURRENT),
    _make_variant('D: breadth_only', composite_fn=_composite_breadth_only, bands=_BANDS_CURRENT),
    _make_variant('E: static_50_50', composite_fn=_composite_static_5050, bands=_BANDS_CURRENT),
    _make_variant('F: fear_greed',   composite_fn=_composite_fear_greed,  bands=_BANDS_CURRENT),
    _make_variant('G: wider_bands',  composite_fn=_composite_current,    bands=_BANDS_WIDER),
    _make_variant('H: asymmetric',   composite_fn=_composite_current,    bands=_BANDS_ASYMMETRIC),
    _make_variant('I: wider_capped', composite_fn=_composite_current,    bands=_BANDS_WIDER_CAPPED),
]


# -- Data loading ------------------------------------------------------------

def _load_active_version():
    from peewee import fn as pf
    return (AlgorithmVersion.select()
            .where(AlgorithmVersion.git_message != '')
            .order_by(AlgorithmVersion.id.desc())
            .first())


def _load_peaks_with_pre_regime(lookback_days: int, version, start_date=None, end_date=None):
    """Load Score rows that qualify as peaks and have pre_regime in weight_info.

    Returns list of dicts:
      {symbol, date, overall, pre_regime, regime_mult_stored}
    """
    if start_date is None:
        start_date = date.today() - timedelta(days=lookback_days)
    q = (Score.select(
            Score.symbol, Score.date, Score.overall,
            Score.regime_multiplier, Score.weight_info,
         )
         .where(Score.date >= start_date,
                Score.overall.is_null(False))
         .order_by(Score.date))
    if end_date is not None:
        q = q.where(Score.date <= end_date)
    if version:
        q = q.where(Score.version == version)

    results = []
    missing_pre = 0
    for s in q:
        overall = int(s.overall)
        # Quick filter: only process peaks
        if not (overall >= CALL_THRESHOLD or overall <= PUT_THRESHOLD):
            continue

        pre_regime = None
        wi = s.weight_info
        if wi:
            try:
                d = json.loads(wi)
                if 'pre_regime' in d:
                    pre_regime = int(d['pre_regime'])
            except Exception:
                pass

        # Fallback: reverse-engineer pre_regime from overall + stored multiplier
        if pre_regime is None and s.regime_multiplier is not None:
            mult = float(s.regime_multiplier)
            if mult != 1.0:
                if overall >= 50:
                    pre_regime = round(50 + (overall - 50) / mult)
                else:
                    pre_regime = round(50 + (overall - 50) / (2.0 - mult))
            else:
                pre_regime = overall
            missing_pre += 1

        if pre_regime is None:
            pre_regime = overall  # no regime data, treat as pre_regime = overall

        results.append({
            'symbol':      s.symbol_id,
            'date':        s.date,
            'overall':     overall,
            'pre_regime':  max(0, min(100, pre_regime)),
            'stored_mult': float(s.regime_multiplier) if s.regime_multiplier else 1.0,
        })

    if missing_pre:
        print(f"  {Fore.YELLOW}{missing_pre} peaks used stored multiplier for pre_regime (no weight_info){Style.RESET_ALL}")

    return results


def _load_regime_cache(cutoff: date) -> Dict:
    """Returns {date: dict} with all regime + breadth + F&G fields.

    F&G fields are read from MarketRegime columns populated by backfill_regime().
    Falls back gracefully (None) when the backfill has not been run yet.
    """
    regimes = list(
        MarketRegime.select(
            MarketRegime.date, MarketRegime.vix_close,
            MarketRegime.vix_10d_change, MarketRegime.regime_composite,
            MarketRegime.regime_multiplier, MarketRegime.market_trend_score,
            MarketRegime.spy_close, MarketRegime.spy_ema50, MarketRegime.spy_ema200,
            MarketRegime.credit_spread_score, MarketRegime.haven_score,
            MarketRegime.skew_score, MarketRegime.fg_composite, MarketRegime.fg_multiplier,
        ).where(MarketRegime.date >= cutoff)
    )
    breadths = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(MarketBreadth.date >= cutoff)
    )
    brd_map = {b.date: float(b.breadth_score) for b in breadths if b.breadth_score is not None}

    def _f(v):
        return float(v) if v is not None else None

    cache = {}
    for r in regimes:
        # Read F&G columns — these will be None until backfill_regime() is run
        try:
            credit_s = _f(r.credit_spread_score)
            haven_s  = _f(r.haven_score)
            skew_s   = _f(r.skew_score)
            fg_comp  = _f(r.fg_composite)
            fg_mult  = _f(r.fg_multiplier)
        except Exception:
            credit_s = haven_s = skew_s = fg_comp = fg_mult = None

        cache[r.date] = {
            'date':               r.date,
            'vix_close':          _f(r.vix_close),
            'vix_10d_change':     _f(r.vix_10d_change),
            'composite':          _f(r.regime_composite),
            'multiplier':         _f(r.regime_multiplier),
            'market_trend_score': _f(r.market_trend_score),
            'spy_close':          _f(r.spy_close),
            'spy_ema50':          _f(r.spy_ema50),
            'spy_ema200':         _f(r.spy_ema200),
            'breadth':            brd_map.get(r.date),
            # F&G proxy (populated after backfill_regime())
            'credit_score':       credit_s,
            'haven_score':        haven_s,
            'skew_score':         skew_s,
            'fg_composite':       fg_comp,
            'fg_multiplier':      fg_mult,
        }
    return cache


def _load_price_cache(symbols, cutoff: date) -> Dict:
    rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.close, PriceHistory.high, PriceHistory.low,
        )
        .where(PriceHistory.symbol.in_(list(symbols)))
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .tuples()
    )
    cache = defaultdict(list)
    for sym, d, c, h, l in rows:
        cache[sym].append((d, float(c), float(h), float(l)))
    return dict(cache)


# -- Fear & Greed proxy market data ------------------------------------------
# F&G data is now stored in MarketRegime DB columns via backfill_regime().
# The _fetch_fear_greed_data function is kept for reference but is no longer
# called by main() — use 'trader regime-backfill 1825' to populate.

def _fetch_fear_greed_data(lookback_days: int) -> Dict[date, dict]:
    """Fetch HYG, LQD, TLT, SPY, ^SKEW from yfinance and build per-date fear signals.

    Returns {date: {credit_score, haven_score, skew_score}} where all are 0-100
    (higher = more fear/risk-off = expected to amplify signals like high VIX).

    CNN F&G components added:
      credit_score  : HYG/LQD 20d momentum → low = risk-off = HIGH fear score
      haven_score   : TLT 20d return - SPY 20d return → positive = fear = HIGH score
      skew_score    : ^SKEW z-score (rolling 252d) → high SKEW = tail risk = mild fear
    """
    try:
        import yfinance as yf
    except ImportError:
        print(f"  {Fore.RED}yfinance not available — Fear & Greed proxy disabled{Style.RESET_ALL}")
        return {}

    period_str = f'{lookback_days + 120}d'
    print(f"  Fetching Fear & Greed proxy data (HYG, LQD, TLT, SPY, ^SKEW)...")

    tickers_data = {}
    try:
        raw = yf.download(['HYG', 'LQD', 'TLT', 'SPY', '^SKEW'],
                          period=period_str, auto_adjust=True, progress=False)
        closes = raw['Close'] if 'Close' in raw.columns.get_level_values(0) else raw
        for ticker in ['HYG', 'LQD', 'TLT', 'SPY', '^SKEW']:
            col = ticker if ticker in closes.columns else None
            if col is None:
                # Try multi-level column
                try:
                    series = closes[ticker].dropna()
                    tickers_data[ticker] = series
                except Exception:
                    pass
            else:
                tickers_data[ticker] = closes[col].dropna()
    except Exception as e:
        print(f"  {Fore.YELLOW}F&G fetch warning: {e}{Style.RESET_ALL}")
        # Try individual fetches
        for t in ['HYG', 'LQD', 'TLT', 'SPY', '^SKEW']:
            try:
                df = yf.Ticker(t).history(period=period_str)
                if not df.empty:
                    tickers_data[t] = df['Close']
            except Exception:
                pass

    if not tickers_data:
        print(f"  {Fore.RED}Could not fetch F&G data — variant F will use no-regime fallback{Style.RESET_ALL}")
        return {}

    def _prices(ticker) -> Optional[List]:
        s = tickers_data.get(ticker)
        if s is None or len(s) == 0:
            return None
        s.index = s.index.tz_localize(None) if hasattr(s.index, 'tz') and s.index.tz else s.index
        return [(idx.date(), float(v)) for idx, v in s.items() if not math.isnan(v)]

    hyg = _prices('HYG') or []
    lqd = _prices('LQD') or []
    tlt = _prices('TLT') or []
    spy = _prices('SPY') or []
    skew = _prices('^SKEW') or []

    def _to_dict(rows):
        return {d: v for d, v in rows}

    hyg_d, lqd_d, tlt_d, spy_d, skew_d = _to_dict(hyg), _to_dict(lqd), _to_dict(tlt), _to_dict(spy), _to_dict(skew)

    # Build sorted date list covering all series
    all_dates = sorted(set(hyg_d) | set(lqd_d) | set(tlt_d) | set(spy_d) | set(skew_d))

    # Rolling return: (price_today - price_N_ago) / price_N_ago
    def _ret(price_map, sorted_dates, idx, window=20):
        if idx < window:
            return None
        prev_d = sorted_dates[idx - window]
        curr_d = sorted_dates[idx]
        p0 = price_map.get(prev_d)
        p1 = price_map.get(curr_d)
        if p0 and p1 and p0 > 0:
            return (p1 - p0) / p0 * 100.0
        return None

    # SKEW: rolling 252-day z-score; high SKEW = tail risk
    skew_vals = [skew_d[d] for d in all_dates if d in skew_d]
    skew_mean = np.mean(skew_vals) if len(skew_vals) >= 20 else 130.0
    skew_std  = np.std(skew_vals)  if len(skew_vals) >= 20 else 10.0

    result: Dict[date, dict] = {}
    for i, d in enumerate(all_dates):
        # Credit spread: HYG/LQD ratio momentum (lower = risk-off = fear)
        hyg_ret  = _ret(hyg_d, all_dates, i, 20)
        lqd_ret  = _ret(lqd_d, all_dates, i, 20)
        if hyg_ret is not None and lqd_ret is not None:
            # HYG underperforms LQD when credit spreads widen (fear)
            spread_momentum = hyg_ret - lqd_ret   # negative = fear
            # Map to 0-100 fear score: -10% spread = 100 (extreme fear), +5% = 0 (greed)
            credit_score = max(0.0, min(100.0, 50.0 + (-spread_momentum) * 4.0))
        else:
            credit_score = None

        # Safe haven: TLT 20d outperformance vs SPY 20d (positive = fear)
        tlt_ret = _ret(tlt_d, all_dates, i, 20)
        spy_ret = _ret(spy_d, all_dates, i, 20)
        if tlt_ret is not None and spy_ret is not None:
            haven_diff = tlt_ret - spy_ret  # positive = bonds beat stocks = fear
            # Map: +10% TLT outperformance = 90 (fear), -10% = 10 (greed)
            haven_score = max(0.0, min(100.0, 50.0 + haven_diff * 4.0))
        else:
            haven_score = None

        # SKEW z-score: high SKEW = tail risk demand = fear
        if d in skew_d and skew_std > 0:
            z = (skew_d[d] - skew_mean) / skew_std
            # Map: z = +2 → score 90 (fear), z = -2 → score 10 (low tail risk)
            skew_score = max(0.0, min(100.0, 50.0 + z * 20.0))
        else:
            skew_score = None

        if any(x is not None for x in [credit_score, haven_score, skew_score]):
            result[d] = {
                'credit_score': credit_score,
                'haven_score':  haven_score,
                'skew_score':   skew_score,
            }

    n_ok = sum(1 for v in result.values() if v.get('credit_score') is not None)
    print(f"  F&G proxy: {n_ok} dates with credit spread data, "
          f"{sum(1 for v in result.values() if v.get('haven_score') is not None)} with safe haven, "
          f"{sum(1 for v in result.values() if v.get('skew_score') is not None)} with SKEW")
    return result


# -- Barrier walk ------------------------------------------------------------

def _run_barrier(symbol: str, peak_date: date, score: int, price_rows: List) -> Optional[dict]:
    """Run barrier walk for one peak. Returns dict or None."""
    if not price_rows:
        return None
    date_idx = {r[0]: i for i, r in enumerate(price_rows)}
    base_idx = date_idx.get(peak_date)
    if base_idx is None:
        return None
    dates  = [r[0] for r in price_rows]
    closes = [r[1] for r in price_rows]
    highs  = [r[2] for r in price_rows]
    lows   = [r[3] for r in price_rows]
    side   = _peak_side(score)
    vol_pct = _realized_vol_pct(closes, base_idx)
    swing = _swing_walk(closes, base_idx, side, vol_pct, highs=highs, lows=lows, dates=dates)
    if swing is None:
        return None
    return {
        'symbol': symbol,
        'date':   peak_date,
        'score':  score,
        'side':   side,
        'swing':  swing,
    }


# -- Per-variant scoring + assessment ----------------------------------------

def _score_bucket(score: int) -> Optional[str]:
    if score >= 90: return '90+'
    if score >= 85: return '85-89'
    if score >= 80: return '80-84'
    if score >= 75: return '75-79'
    if score >= 70: return '70-74'
    if score <= 5:  return '<5'
    if score <= 15: return '<15'
    if score <= 25: return '<25'
    return None


def _run_variant(
    variant_label: str,
    variant_fn: Callable,
    peaks_raw: List[dict],
    regime_cache: Dict,
    price_cache: Dict,
    fg_cache: Dict,
) -> Dict:
    """Apply variant multiplier to pre_regime scores, re-qualify, run barrier, return results."""
    results = defaultdict(list)  # bucket → [result_dict, ...]
    n_skipped = 0

    for pk in peaks_raw:
        reg = regime_cache.get(pk['date'])
        mult = variant_fn(reg, fg_cache)

        new_score = _apply_to_score(pk['pre_regime'], mult)

        # Re-qualify as call or put with new score
        bucket = _score_bucket(new_score)
        if bucket is None:
            n_skipped += 1
            continue

        price_rows = price_cache.get(pk['symbol'], [])
        r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
        if r is None:
            n_skipped += 1
            continue

        results[bucket].append(r)

    return dict(results)


# -- Win-rate helpers ---------------------------------------------------------

def _win_rate(entries: List, period: str) -> Optional[float]:
    per = [e['swing'][period] for e in entries if period in e.get('swing', {})]
    if len(per) < MIN_N_REPORT:
        return None
    wins = sum(1 for s in per if s['result'] == 'win')
    return round(wins / len(per) * 100, 1)


def _avg_ret(entries: List, period: str = '30d') -> Optional[float]:
    per = [e['swing'][period] for e in entries if period in e.get('swing', {})]
    if len(per) < MIN_N_REPORT:
        return None
    return round(float(np.mean([s['exit_ret'] for s in per])), 2)


# -- Output formatting --------------------------------------------------------

def _wr_fmt(wr):
    if wr is None:
        return f"  --  "
    if wr >= 65: col = Fore.GREEN
    elif wr >= 52: col = Fore.YELLOW
    else: col = Fore.RED
    return f"{col}{wr:5.1f}{Style.RESET_ALL}"


def _delta_fmt(delta):
    if delta is None:
        return "   -- "
    if delta > 0.5: col = Fore.GREEN; sign = '+'
    elif delta < -0.5: col = Fore.RED; sign = ''
    else: col = Fore.WHITE; sign = '+' if delta >= 0 else ''
    return f"{col}{sign}{delta:.1f}{Style.RESET_ALL}"


# -- Main comparison table ----------------------------------------------------

CALL_BUCKETS = ['90+', '85-89', '80-84', '75-79', '70-74']
PUT_BUCKETS  = ['<5', '<15', '<25']
ALL_BUCKETS  = CALL_BUCKETS + PUT_BUCKETS


def _print_variant_table(variant_results: Dict[str, Dict], baseline_key: str = 'A: no_regime',
                         period: str = '15d'):
    """Print side-by-side WR table for a given period: variants as columns, buckets as rows."""
    variants = list(variant_results.keys())
    baseline = variant_results.get(baseline_key, {})

    col_w = 14
    hdr = f"{'Bucket':<10}  {'N(B)':>6}  " + ''.join(f"{v[:col_w]:>{col_w}}" for v in variants)
    print(f"\n{Fore.CYAN}{'='*110}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}WR {period} — all variants  (delta vs {baseline_key}){Style.RESET_ALL}")
    print(hdr)
    print('-' * 110)

    for bucket in ALL_BUCKETS:
        ref_entries = variant_results.get('B: current', {}).get(bucket, [])
        n = len(ref_entries)
        base_entries = baseline.get(bucket, [])
        base_wr = _win_rate(base_entries, period)

        row = f"{bucket:<10}  {n:>6}  "
        for v in variants:
            entries = variant_results.get(v, {}).get(bucket, [])
            wr = _win_rate(entries, period)
            if v == baseline_key:
                row += f"{_wr_fmt(wr):>14}"
            else:
                delta = (wr - base_wr) if (wr is not None and base_wr is not None) else None
                wr_str = _wr_fmt(wr)
                d_str  = _delta_fmt(delta)
                row += f"  {wr_str}/{d_str}"
        print(row)

    print('-' * 110)


def _print_detailed_variant(label: str, results: Dict):
    """Print WR7/15/30/60 + EV30 for one variant across all buckets."""
    print(f"\n{Fore.CYAN}{'-'*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{label}{Style.RESET_ALL}")
    print(f"{'Bucket':<10}  {'N':>5}  " + ''.join(f"{'WR'+p:>8}" for p in DISPLAY_PERIODS) + f"  {'EV30d':>7}")
    print('-' * 65)

    for bucket in ALL_BUCKETS:
        entries = results.get(bucket, [])
        n = len(entries)
        if n == 0:
            print(f"{bucket:<10}  {n:>5}  {'no data':>40}")
            continue
        row = f"{bucket:<10}  {n:>5}  "
        for p in DISPLAY_PERIODS:
            wr = _win_rate(entries, p)
            row += f"{_wr_fmt(wr):>8}"
        ev = _avg_ret(entries, '30d')
        row += f"  {Fore.GREEN if ev and ev > 0 else Fore.RED}{ev if ev is not None else '--':>7}{Style.RESET_ALL}"
        print(row)


def _print_fg_contribution(fg_cache: Dict, regime_cache: Dict):
    """Show how often F&G signals meaningfully shift the composite vs VIX+breadth alone."""
    if not fg_cache:
        print(f"\n{Fore.YELLOW}No F&G data — skipping contribution analysis{Style.RESET_ALL}")
        return

    dates_with_fg = [d for d in fg_cache if fg_cache[d].get('credit_score') is not None]
    dates_with_reg = [d for d in dates_with_fg if d in regime_cache]

    if not dates_with_reg:
        print(f"\n{Fore.YELLOW}No overlapping regime/F&G dates — skipping{Style.RESET_ALL}")
        return

    deltas = []
    for d in dates_with_reg:
        reg = regime_cache[d]
        comp_base = _composite_current(reg)
        comp_fg   = _composite_fear_greed(reg, fg_cache)
        if comp_base is not None and comp_fg is not None:
            deltas.append(comp_fg - comp_base)

    if not deltas:
        return

    avg_delta = np.mean(deltas)
    std_delta = np.std(deltas)
    gt2 = sum(1 for d in deltas if d > 2.0)
    lt2 = sum(1 for d in deltas if d < -2.0)

    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Fear & Greed Proxy — Composite Impact vs Current (VIX + Breadth){Style.RESET_ALL}")
    print(f"  Dates analysed  : {len(deltas)}")
    print(f"  Mean D composite: {avg_delta:+.2f}  (positive = F&G adds more fear signal)")
    print(f"  Std D composite : {std_delta:.2f}")
    print(f"  >+2 pts (more fearful)  : {gt2} dates  ({gt2/len(deltas)*100:.0f}%)")
    print(f"  <-2 pts (less fearful)  : {lt2} dates  ({lt2/len(deltas)*100:.0f}%)")


def _print_pearson_summary(variant_results: Dict, baseline_key: str = 'A: no_regime'):
    """Pearson(variant_multiplier, win) is implicit in WR tables, but let's show N clearly."""
    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Peak Counts per Variant (qualifying N after re-scoring){Style.RESET_ALL}")
    print(f"{'Variant':<22}  {'Call total':>12}  {'Put total':>12}  {'Grand total':>12}")
    print('-' * 65)

    for v, results in variant_results.items():
        n_call = sum(len(results.get(b, [])) for b in CALL_BUCKETS)
        n_put  = sum(len(results.get(b, [])) for b in PUT_BUCKETS)
        print(f"  {v:<20}  {n_call:>12}  {n_put:>12}  {n_call+n_put:>12}")


# -- Entry point -------------------------------------------------------------

def main():
    args = sys.argv[1:]
    lookback_days = DEFAULT_LOOKBACK
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    for arg in args:
        if arg.startswith('--start='):
            start_date = date.fromisoformat(arg.split('=', 1)[1])
        elif arg.startswith('--end='):
            end_date = date.fromisoformat(arg.split('=', 1)[1])
        elif arg.isdigit():
            lookback_days = int(arg)

    cutoff = start_date if start_date else (date.today() - timedelta(days=lookback_days))
    end_cutoff = end_date  # None = no upper bound

    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    if start_date or end_date:
        window_str = f"{cutoff} to {end_cutoff or 'today'}"
        print(f"{Fore.CYAN}Regime A/B Test — date window {window_str}{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}Regime A/B Test — {lookback_days}d lookback  (cutoff {cutoff}){Style.RESET_ALL}")
    print(f"  Variants: {', '.join(v[0] for v in VARIANTS)}")
    print(f"  F&G signals (variant F) read from MarketRegime DB columns.")
    print(f"  Run 'trader regime-backfill 1825' first to populate F&G columns.")
    print(f"{Fore.CYAN}{'='*90}{Style.RESET_ALL}")

    # 1. Active algorithm version
    version = _load_active_version()
    print(f"\nAlgorithm version: {version.git_commit[:8] if version else 'unknown'} "
          f"({version.git_message[:60] if version else ''})")

    # 2. Load peaks with pre_regime
    print(f"\nLoading peaks...")
    peaks_raw = _load_peaks_with_pre_regime(lookback_days, version, start_date=cutoff, end_date=end_cutoff)
    symbols = {p['symbol'] for p in peaks_raw}
    print(f"  {len(peaks_raw)} qualifying peaks  ({len(symbols)} symbols)")

    if not peaks_raw:
        print(f"{Fore.RED}No peaks found. Run trader update + recalculate first.{Style.RESET_ALL}")
        return

    # 3. Load regime + price data (F&G signals come from DB, no live fetch needed)
    # Use the peak start date for regime/price lookups; price data extends past end_date
    # so forward-return windows (up to 90d) are correctly covered.
    print(f"\nLoading market context...")
    regime_cache = _load_regime_cache(cutoff)
    print(f"  {len(regime_cache)} regime dates")

    n_fg = sum(1 for r in regime_cache.values() if r.get('credit_score') is not None)
    if n_fg == 0:
        print(f"  {Fore.YELLOW}No F&G data in DB — variant F will use VIX+breadth only."
              f"  Run: trader regime-backfill 1825{Style.RESET_ALL}")
    else:
        print(f"  {Fore.GREEN}{n_fg} dates with F&G proxy data (credit/haven/skew){Style.RESET_ALL}")

    print(f"  Loading price history for {len(symbols)} symbols...")
    price_cache = _load_price_cache(symbols, cutoff)

    # 4. Run each variant (F&G data embedded in regime_cache, no separate fg_cache needed)
    print(f"\nRunning variants...")
    variant_results: Dict[str, Dict] = {}
    for v_label, v_fn in VARIANTS:
        print(f"  {v_label}...", end=' ', flush=True)
        res = _run_variant(v_label, v_fn, peaks_raw, regime_cache, price_cache, None)
        variant_results[v_label] = res
        total_peaks = sum(len(v) for v in res.values())
        print(f"{total_peaks} peaks")

    # 5. Output
    _print_pearson_summary(variant_results)
    _print_variant_table(variant_results, period='15d')  # WR15 — primary optimization target
    _print_variant_table(variant_results, period='30d')  # WR30 — consistency check

    # Detailed per-variant breakdown for key variants
    for v_label in ['A: no_regime', 'B: current', 'G: wider_bands', 'I: wider_capped']:
        if v_label in variant_results:
            _print_detailed_variant(v_label, variant_results[v_label])

    # F&G contribution summary
    n_credit = sum(1 for r in regime_cache.values() if r.get('credit_score') is not None)
    n_haven  = sum(1 for r in regime_cache.values() if r.get('haven_score') is not None)
    n_skew   = sum(1 for r in regime_cache.values() if r.get('skew_score') is not None)
    if n_credit > 0:
        print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}F&G Proxy — DB Coverage{Style.RESET_ALL}")
        print(f"  credit_spread_score : {n_credit} dates")
        print(f"  haven_score         : {n_haven} dates")
        print(f"  skew_score          : {n_skew} dates")

    print(f"\n{Fore.CYAN}{'='*90}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Interpretation guide{Style.RESET_ALL}")
    print(f"  B > A  : regime adjustment improves win rates (current production is helping)")
    print(f"  F > B  : Fear & Greed proxy adds predictive value beyond VIX+breadth")
    print(f"  G > B  : wider multiplier bands improve discrimination")
    print(f"  H > B  : asymmetric suppression (more in stress, less in bull) is better")
    print(f"  D > C  : breadth alone outperforms VIX alone (breadth is the key driver)")
    print(f"  E vs B : static 50/50 weights vs dynamic sigmoid (is the sigmoid helping?)")
    print(f"\n  Focus on 90+ CALL and <25 PUT buckets — those drive P&L in Monte Carlo.")
    print(f"  N shifts between variants reflect regime gates changing which peaks qualify.")
    print(f"  A variant N is the maximum possible (no regime gate drops any peak).")
    print(f"{Fore.CYAN}{'='*90}{Style.RESET_ALL}\n")


if __name__ == '__main__':
    main()
