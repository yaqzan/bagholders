"""
Market Regime — daily regime score that acts as a final multiplier on the
existing score pipeline.  Suppresses signals in stressed regimes, mildly
amplifies them in healthy regimes.

Pipeline position:
    weighted_sum → weekly_multiplier → volume_multiplier → regime_multiplier → overall

Three signal families (P/C deferred):
    Internal Breadth  40%
    VIX Level+Trend   35%
    Market Trend       25%

Weights are parameterised so a fourth signal can be added later without
restructuring.
"""

import logging
from datetime import date, datetime, timedelta

import yfinance as yf

from database.models.core import Score, MarketRegime, MarketBreadth, AlgorithmVersion, Stock

log = logging.getLogger(__name__)

# ── Signal weights (must sum to 1.0) ────────────────────────────────
# internal_breadth removed: score-based breadth overlaps with market_breadth
# and conflates signal quality with market structure.  Freed weight distributed
# to the three objective external signals.
SIGNAL_WEIGHTS = {
    'market_breadth': 0.35,  # price/volume breadth (TRIN, McClellan, H/L, EMA%)
    'vix':            0.35,  # fear gauge — most objective external signal
    'market_trend':   0.30,  # SPY vs EMA50/200 — partially captured by EMA% in breadth
}

# ── Multiplier bands (composite → multiplier, linear interpolation) ─
# Each entry: (lower_bound_inclusive, upper_bound_exclusive, mult_at_lower, mult_at_upper)
_MULTIPLIER_BANDS = [
    (0,  15, 0.70, 0.70),
    (15, 30, 0.70, 0.78),
    (30, 45, 0.78, 0.88),
    (45, 60, 0.88, 1.00),
    (60, 75, 1.00, 1.05),
    (75, 101, 1.05, 1.10),
]

MIN_COVERAGE_PCT = 0.80


# ── EMA helper ──────────────────────────────────────────────────────

def compute_ema(prices: list, period: int) -> float:
    k = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = price * k + ema * (1 - k)
    return ema


# ── Signal Family 1: Internal Breadth (40%) ─────────────────────────

def compute_internal_breadth(target_date, version=None):
    """Breadth from today's Score rows.  Returns score 0-100 or None."""
    q = Score.select().where(
        Score.date == target_date,
        Score.overall.is_null(False),
    )
    if version:
        q = q.where(Score.version == version)

    scores = list(q)
    total = len(scores)
    if total == 0:
        return None

    above_50 = sum(1 for s in scores if s.overall > 50)
    above_70 = sum(1 for s in scores if s.overall > 70)

    pct_above_50_score = (above_50 / total) * 100
    pct_above_70_score = (above_70 / total) * 100

    velocities = [s.score_velocity_7d for s in scores if s.score_velocity_7d is not None]
    if velocities:
        avg_vel = sum(velocities) / len(velocities)
        clamped = max(-20, min(20, avg_vel))
        velocity_score = (clamped + 20) / 40 * 100
    else:
        velocity_score = 50.0

    return round((pct_above_50_score + pct_above_70_score + velocity_score) / 3, 2)


# ── Signal Family 2: VIX Level + Trend (35%) ────────────────────────

import math as _math

# VIX gradient parameters — tuned to 5y win-rate data (2026-04-09).
# Level: tanh centred at VIX 22 (neutral), scale 10.
#   VIX 12 -> ~12   VIX 22 -> 50   VIX 32 -> ~88   VIX 45 -> ~99
# Direction: HIGH VIX = HIGH score (fear premium amplifies signals).
# Trend: rising VIX -> higher score (inverted from legacy bands).
#   +15% -> ~88   0% -> 50   -15% -> ~12
VIX_LEVEL_PIVOT = 22.0   # VIX value that maps to score 50 (neutral)
VIX_LEVEL_SCALE = 10.0   # steepness; score spans 12-99 across ~VIX 10-45
VIX_TREND_SCALE = 15.0   # 10d % change that maps to score ~88 / ~12


def compute_vix_score(vix_close, vix_10d_change_pct):
    """VIX fear-premium gradient: high VIX -> high score (amplify signals).

    Both the level and trend axes use tanh for a smooth, continuous mapping.
    This replaced step-band logic after 5y OOS analysis showed the old bands
    suppressed exactly the regimes where signals performed best (2026-04-09).
    """
    if vix_close is None or vix_10d_change_pct is None:
        return None
    level_score = 50.0 + 50.0 * _math.tanh(
        (vix_close - VIX_LEVEL_PIVOT) / VIX_LEVEL_SCALE
    )
    trend_score = 50.0 + 50.0 * _math.tanh(
        vix_10d_change_pct / VIX_TREND_SCALE
    )
    level_score = max(0.0, min(100.0, level_score))
    trend_score = max(0.0, min(100.0, trend_score))
    return round(level_score * 0.55 + trend_score * 0.45, 2)


def _compute_vix_score_legacy(vix_close, vix_10d_change_pct):
    """Original band-based VIX scoring — kept for simulation comparison only."""
    _VIX_LEVEL_BANDS = [
        (0,  15, 90), (15, 20, 75), (20, 25, 55),
        (25, 30, 35), (30, 40, 15), (40, 999, 0),
    ]
    _VIX_TREND_BANDS = [
        (-999, -15, 90), (-15, -5, 70), (-5, 5, 50),
        (5, 15, 30),     (15, 999, 10),
    ]
    def _band(bands, v):
        for lo, hi, s in bands:
            if lo <= v < hi:
                return s
        return bands[-1][2]
    if vix_close is None or vix_10d_change_pct is None:
        return None
    return round(_band(_VIX_LEVEL_BANDS, vix_close) * 0.55 +
                 _band(_VIX_TREND_BANDS, vix_10d_change_pct) * 0.45, 2)


# ── Signal Family 3: Market Trend — SPY vs EMAs (25%) ───────────────

def _band_lookup(bands, v):
    for lo, hi, s in bands:
        if lo <= v < hi:
            return s
    return bands[-1][2]


_SPY_EMA50_BANDS = [
    (-999, -3, 15),
    (-3,    0, 35),
    (0,     3, 65),
    (3,   999, 85),
]

_SPY_EMA200_BANDS = [
    (-999, -5, 10),
    (-5,    0, 35),
    (0,     5, 65),
    (5,   999, 85),
]


def compute_market_trend_score(spy_close, spy_ema50, spy_ema200):
    if None in (spy_close, spy_ema50, spy_ema200):
        return None
    pct_from_50 = (spy_close - spy_ema50) / spy_ema50 * 100
    pct_from_200 = (spy_close - spy_ema200) / spy_ema200 * 100
    score_50 = _band_lookup(_SPY_EMA50_BANDS, pct_from_50)
    score_200 = _band_lookup(_SPY_EMA200_BANDS, pct_from_200)
    return round(score_50 * 0.50 + score_200 * 0.50, 2)


# ── Dynamic weights ─────────────────────────────────────────────────
#
# 5y OOS analysis (2026-04-09) showed:
#   1. Market-trend is noise (Pearson ~ 0, drop it).
#   2. Breadth (inverted) is the strongest predictor at all VIX levels.
#   3. VIX rises in importance sharply in fear regimes — at VIX 28+,
#      VIX alone explains ~21% of put outcomes (Pearson +0.21).
#   4. Log-VIX underperforms linear tanh because it compresses the calm zone
#      too aggressively; linear with pivot=22 is the winner.
#
# Dynamic weight formula:
#   vix_weight  = sigmoid shift from ~5% (calm) to ~95% (panic)
#   brd_weight  = 1 - vix_weight  (fully complementary, no trend)
#
#   sigmoid centred at VIX 22, scale 0.25 → transitions from breadth-
#   dominated at VIX 13 (~9%) to VIX-dominated at VIX 35 (~96%).

_VIX_WEIGHT_CENTRE = 22.0   # VIX where weights are 50/50
_VIX_WEIGHT_SCALE  = 0.25   # sigmoid steepness


def _vix_dynamic_weight(vix_close):
    """Returns the VIX fraction of the composite [0.05, 0.95].
    Breadth fraction = 1 - this value.  Market trend is always 0.
    """
    if vix_close is None:
        return 0.35   # fallback when VIX data absent: moderate VIX weight
    raw = 1.0 / (1.0 + _math.exp(-_VIX_WEIGHT_SCALE * (vix_close - _VIX_WEIGHT_CENTRE)))
    return max(0.05, min(0.95, raw))


# ── Composite + Multiplier ──────────────────────────────────────────

def compute_regime_composite(vix_score, market_trend, market_breadth_score=None,
                             vix_close=None):
    """Dynamic-weighted composite of VIX (fear premium) + inverted breadth.

    Key changes from original design (2026-04-09):
      - Market trend dropped: empirically noise across all regimes.
      - Breadth inverted: weak breadth -> high score (signals more reliable).
      - VIX weight shifts dynamically via sigmoid on vix_close:
          calm (<18)  -> VIX ~10%, breadth ~90%
          mid  (18-28)-> VIX ~27-78%, breadth complement
          panic(28+)  -> VIX ~85-96%, breadth ~4-15%
    """
    # Invert breadth so that WEAK breadth = HIGH composite = amplify signals
    breadth_inverted = (100.0 - float(market_breadth_score)
                        if market_breadth_score is not None else None)

    w_vix = _vix_dynamic_weight(vix_close)
    w_brd = 1.0 - w_vix

    parts, total_w = [], 0.0
    if vix_score is not None:
        parts.append(float(vix_score) * w_vix); total_w += w_vix
    if breadth_inverted is not None:
        parts.append(breadth_inverted * w_brd);  total_w += w_brd

    if not parts or total_w == 0:
        return None
    composite = sum(parts) / total_w
    return round(max(0.0, min(100.0, composite)), 2)


def compute_regime_multiplier(composite):
    if composite is None:
        return 1.0
    for lo, hi, m_lo, m_hi in _MULTIPLIER_BANDS:
        if lo <= composite < hi:
            t = (composite - lo) / (hi - lo) if hi > lo else 0
            return round(m_lo + t * (m_hi - m_lo), 4)
    return 1.0


def apply_regime_to_score(overall, multiplier):
    """Apply regime multiplier with sell-signal inversion around 50."""
    if multiplier is None or multiplier == 1.0:
        return overall
    if overall >= 50:
        adjusted = 50 + (overall - 50) * multiplier
    else:
        adjusted = 50 + (overall - 50) * (2.0 - multiplier)
    return int(max(0, min(100, round(adjusted))))


# ── Fear & Greed proxy — computation ────────────────────────────────
#
# Three CNN Fear & Greed components not in the existing regime:
#   credit_spread : HYG 20d return − LQD 20d return (negative = risk-off = fear)
#   haven         : TLT 20d return − SPY 20d return  (positive = bonds > stocks = fear)
#   skew          : CBOE SKEW z-score vs 252-day rolling window (high = tail risk demand)
#
# All three map to a 0-100 fear score: higher = more fear = should amplify signals.
# Weights in fg_composite: VIX 40% + breadth 30% + credit 15% + haven 10% + skew 5%.

_FG_CREDIT_SCALE = 4.0   # 1% HYG/LQD spread shift → 4 pts credit_score shift from 50
_FG_HAVEN_SCALE  = 4.0   # 1% TLT outperformance over SPY → 4 pts haven_score shift
_FG_SKEW_SCALE   = 20.0  # 1 std-dev SKEW above mean → 20 pts skew_score above 50

FG_WEIGHTS = {
    'vix':     0.40,
    'breadth': 0.30,
    'credit':  0.15,
    'haven':   0.10,
    'skew':    0.05,
}


def compute_fg_scores(hyg_lqd_ret_diff, tlt_spy_ret_diff, skew_zscore):
    """Convert raw F&G metrics into 0-100 fear scores.

    Args:
        hyg_lqd_ret_diff  : HYG 20d return - LQD 20d return (%).  Negative = credit fear.
        tlt_spy_ret_diff  : TLT 20d return - SPY 20d return (%).  Positive = safe-haven demand.
        skew_zscore       : CBOE SKEW z-score (rolling 252d mean/std).  Positive = tail risk.

    Returns:
        (credit_score, haven_score, skew_score) each 0-100 or None.
    """
    credit_score = None
    if hyg_lqd_ret_diff is not None:
        # Negative spread momentum (HYG lags LQD) = fear = high score
        credit_score = round(max(0.0, min(100.0, 50.0 + (-hyg_lqd_ret_diff) * _FG_CREDIT_SCALE)), 2)

    haven_score = None
    if tlt_spy_ret_diff is not None:
        # Positive (TLT outperforms SPY) = bonds beat stocks = fear = high score
        haven_score = round(max(0.0, min(100.0, 50.0 + tlt_spy_ret_diff * _FG_HAVEN_SCALE)), 2)

    skew_score = None
    if skew_zscore is not None:
        # High z-score = elevated tail risk demand = mild fear amplifier
        skew_score = round(max(0.0, min(100.0, 50.0 + skew_zscore * _FG_SKEW_SCALE)), 2)

    return credit_score, haven_score, skew_score


def compute_fg_composite(vix_score, breadth_inverted, credit_score, haven_score, skew_score):
    """Compute F&G composite from up to 5 fear signals.

    Uses FG_WEIGHTS.  Falls back gracefully when any signal is absent
    (total weight renormalized to available signals).

    Returns composite 0-100 or None if no signals available.
    """
    parts = [
        ('vix',     vix_score,        FG_WEIGHTS['vix']),
        ('breadth', breadth_inverted,  FG_WEIGHTS['breadth']),
        ('credit',  credit_score,      FG_WEIGHTS['credit']),
        ('haven',   haven_score,       FG_WEIGHTS['haven']),
        ('skew',    skew_score,        FG_WEIGHTS['skew']),
    ]
    weighted_sum, total_w = 0.0, 0.0
    for _, val, w in parts:
        if val is not None:
            weighted_sum += float(val) * w
            total_w += w
    if total_w < 0.10:   # need at least one meaningful signal
        return None
    return round(max(0.0, min(100.0, weighted_sum / total_w)), 2)


# ── Market data fetch ───────────────────────────────────────────────

def fetch_vix_spy_data():
    """Pull VIX and SPY from yfinance.  Returns (vix_df, spy_df) or (None, None)."""
    try:
        vix = yf.Ticker('^VIX').history(period='60d')
        spy = yf.Ticker('SPY').history(period='2y')
        if vix.empty or spy.empty:
            return None, None
        return vix, spy
    except Exception as e:
        log.warning("Failed to fetch VIX/SPY data: %s", e)
        return None, None


def fetch_fg_data(period='60d'):
    """Fetch Fear & Greed proxy tickers: HYG, LQD, TLT, ^SKEW.

    Returns dict of {ticker: Series} or empty dict on failure.
    Uses per-ticker Ticker.history() for reliability (yf.download multi-level
    column handling is fragile across yfinance versions).
    """
    tickers = ['HYG', 'LQD', 'TLT', '^SKEW']
    result = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(period=period, auto_adjust=True)
            if df is not None and not df.empty and 'Close' in df.columns:
                result[t] = df['Close'].dropna()
            else:
                log.warning("F&G ticker %s returned empty data", t)
        except Exception as e:
            log.warning("F&G ticker %s failed: %s", t, e)
    return result


def _fg_series_to_date_map(series):
    """Convert a yfinance price Series to {date: float} with tz stripped."""
    if series is None:
        return {}
    idx = series.index
    if hasattr(idx, 'tz') and idx.tz is not None:
        idx = idx.tz_convert(None)  # tz_convert(None) works on both aware and naive
    return {(i.date() if hasattr(i, 'date') else i): float(v)
            for i, v in zip(idx, series)
            if v is not None and not _math.isnan(float(v))}


def extract_fg_metrics_for_date(target_date, fg_data_maps, sorted_trading_dates,
                                skew_history=None):
    """Compute raw F&G metrics for a single date from pre-loaded price maps.

    Args:
        target_date        : date to compute for
        fg_data_maps       : {ticker: {date: price}} for HYG, LQD, TLT (and optionally ^SKEW)
        sorted_trading_dates: sorted list of all dates in the maps (for 20d lookback)
        skew_history       : list of (date, skew_value) sorted ascending (for z-score)

    Returns:
        dict with keys: hyg_close, lqd_close, tlt_close, skew_close,
                        hyg_lqd_ret_diff, tlt_spy_ret_diff, skew_zscore
        Any value may be None if data absent.
    """
    out = {k: None for k in ('hyg_close', 'lqd_close', 'tlt_close', 'skew_close',
                              'hyg_lqd_ret_diff', 'tlt_spy_ret_diff', 'skew_zscore')}

    hyg_map = fg_data_maps.get('HYG', {})
    lqd_map = fg_data_maps.get('LQD', {})
    tlt_map = fg_data_maps.get('TLT', {})
    spy_map = fg_data_maps.get('SPY', {})
    skew_map = fg_data_maps.get('^SKEW', {})

    # Current closes (look backwards up to 5 trading days to handle weekends/holidays)
    def _latest_price(price_map, d, max_lag=5):
        for lag in range(max_lag + 1):
            candidate = d - timedelta(days=lag)
            v = price_map.get(candidate)
            if v is not None:
                return v
        return None

    out['hyg_close']  = _latest_price(hyg_map, target_date)
    out['lqd_close']  = _latest_price(lqd_map, target_date)
    out['tlt_close']  = _latest_price(tlt_map, target_date)
    out['skew_close'] = _latest_price(skew_map, target_date)

    # 20-trading-day return: find the date 20 entries before target in sorted_trading_dates
    # We use calendar-day cutoff (≈28 cal days back) as a proxy to avoid dependency on full sorted list
    lookback_cal = 30   # 20 trading days ≈ 28-30 calendar days
    cutoff_date = target_date - timedelta(days=lookback_cal)

    def _ret_20d(price_map, d, cutoff):
        p_now = _latest_price(price_map, d)
        p_old = _latest_price(price_map, cutoff, max_lag=7)
        if p_now and p_old and p_old > 0:
            return (p_now - p_old) / p_old * 100.0
        return None

    hyg_ret = _ret_20d(hyg_map, target_date, cutoff_date)
    lqd_ret = _ret_20d(lqd_map, target_date, cutoff_date)
    tlt_ret = _ret_20d(tlt_map, target_date, cutoff_date)
    spy_ret = _ret_20d(spy_map, target_date, cutoff_date)

    if hyg_ret is not None and lqd_ret is not None:
        out['hyg_lqd_ret_diff'] = round(hyg_ret - lqd_ret, 4)
    if tlt_ret is not None and spy_ret is not None:
        out['tlt_spy_ret_diff'] = round(tlt_ret - spy_ret, 4)

    # SKEW z-score: 252 trading-day rolling (≈ 1 year)
    if skew_history:
        # skew_history: [(date, value), ...] sorted ascending
        # Find values in the 252-day window ending at target_date
        window_cutoff = target_date - timedelta(days=370)   # ≈ 252 trading days
        window_vals = [v for d, v in skew_history if window_cutoff <= d <= target_date]
        if len(window_vals) >= 30:   # need enough history
            mu = sum(window_vals) / len(window_vals)
            var = sum((x - mu) ** 2 for x in window_vals) / len(window_vals)
            std = var ** 0.5
            curr_skew = out['skew_close']
            if curr_skew is not None and std > 0:
                out['skew_zscore'] = round((curr_skew - mu) / std, 4)

    return out


def _extract_vix_metrics(vix_df):
    """Returns (vix_close, vix_10d_change_pct) from a VIX DataFrame."""
    if vix_df is None or len(vix_df) < 11:
        return None, None
    vix_close = float(vix_df['Close'].iloc[-1])
    vix_10d_ago = float(vix_df['Close'].iloc[-11])
    if vix_10d_ago == 0:
        return vix_close, None
    vix_10d_change = ((vix_close - vix_10d_ago) / vix_10d_ago) * 100
    return vix_close, round(vix_10d_change, 4)


def _extract_spy_metrics(spy_df):
    """Returns (spy_close, spy_ema50, spy_ema200)."""
    if spy_df is None or len(spy_df) < 200:
        return None, None, None
    closes = spy_df['Close'].tolist()
    spy_close = float(closes[-1])
    spy_ema50 = compute_ema(closes, 50)
    spy_ema200 = compute_ema(closes, 200)
    return spy_close, round(spy_ema50, 2), round(spy_ema200, 2)


# ── Main entry point ────────────────────────────────────────────────

def compute_regime(pull_date=None, vix_df=None, spy_df=None, fg_data=None):
    """Compute regime for pull_date and store the MarketRegime row.

    Does NOT mutate any Score rows — regime is applied inside
    compute_overall_score() during the scoring pass.

    Args:
        fg_data: optional dict {ticker: Series} from fetch_fg_data().
                 If None, fetches automatically. Pass {} to skip F&G.

    Returns the MarketRegime row, or None if regime was skipped.
    """
    if pull_date is None:
        pull_date = date.today()

    MarketRegime.ensure_schema()

    from database.models.technical import PriceHistory
    # Coverage gate: need 80% of tracked stocks with PriceHistory for this date
    total_stocks = Stock.select().where(Stock.forward_pe.is_null(False)).count()
    ph_today = PriceHistory.select().where(PriceHistory.date == pull_date).count()

    if total_stocks > 0 and ph_today / total_stocks < MIN_COVERAGE_PCT:
        log.warning(
            "Regime skipped: only %d/%d stocks have PriceHistory (%.0f%%, need %.0f%%)",
            ph_today, total_stocks,
            ph_today / total_stocks * 100, MIN_COVERAGE_PCT * 100,
        )
        return None

    # Fetch market data if not provided (backfill passes pre-fetched data)
    if vix_df is None or spy_df is None:
        vix_df, spy_df = fetch_vix_spy_data()

    vix_close, vix_10d_change = _extract_vix_metrics(vix_df)
    spy_close, spy_ema50, spy_ema200 = _extract_spy_metrics(spy_df)

    vix_sc = compute_vix_score(vix_close, vix_10d_change)
    market_trend = compute_market_trend_score(spy_close, spy_ema50, spy_ema200)

    # Breadth indicators — compute and store, then pull score
    from market_breadth import compute_and_store_breadth
    breadth_row = compute_and_store_breadth(pull_date)
    market_breadth_score = float(breadth_row.breadth_score) if breadth_row and breadth_row.breadth_score is not None else None

    composite = compute_regime_composite(vix_sc, market_trend, market_breadth_score, vix_close=vix_close)
    multiplier = compute_regime_multiplier(composite)

    # ── Fear & Greed proxy signals ──────────────────────────────────
    if fg_data is None:
        fg_data = fetch_fg_data(period='60d')

    fg_maps = {t: _fg_series_to_date_map(s) for t, s in fg_data.items()}
    # Include SPY in fg_maps for the TLT/SPY return diff
    if spy_df is not None and not spy_df.empty:
        spy_series = spy_df['Close'] if 'Close' in spy_df.columns else spy_df
        fg_maps['SPY'] = _fg_series_to_date_map(spy_series)

    # Build SKEW history for z-score (from available SKEW data)
    skew_series = fg_data.get('^SKEW')
    skew_history = []
    if skew_series is not None:
        skew_map = _fg_series_to_date_map(skew_series)
        skew_history = sorted(skew_map.items())

    fg_raw = extract_fg_metrics_for_date(pull_date, fg_maps, [], skew_history)
    credit_score, haven_score, skew_score = compute_fg_scores(
        fg_raw.get('hyg_lqd_ret_diff'),
        fg_raw.get('tlt_spy_ret_diff'),
        fg_raw.get('skew_zscore'),
    )
    breadth_inv = (100.0 - market_breadth_score) if market_breadth_score is not None else None
    fg_comp = compute_fg_composite(vix_sc, breadth_inv, credit_score, haven_score, skew_score)
    fg_mult = compute_regime_multiplier(fg_comp) if fg_comp is not None else None

    regime, created = MarketRegime.get_or_create(date=pull_date)
    regime.vix_close = vix_close
    regime.vix_10d_change = vix_10d_change
    regime.spy_close = spy_close
    regime.spy_ema50 = spy_ema50
    regime.spy_ema200 = spy_ema200
    regime.vix_score = vix_sc
    regime.market_trend_score = market_trend
    regime.regime_composite = composite
    regime.regime_multiplier = multiplier
    # F&G fields
    regime.hyg_close = fg_raw.get('hyg_close')
    regime.lqd_close = fg_raw.get('lqd_close')
    regime.tlt_close = fg_raw.get('tlt_close')
    regime.skew_close = fg_raw.get('skew_close')
    regime.hyg_lqd_ret_diff = fg_raw.get('hyg_lqd_ret_diff')
    regime.tlt_spy_ret_diff = fg_raw.get('tlt_spy_ret_diff')
    regime.credit_spread_score = credit_score
    regime.haven_score = haven_score
    regime.skew_score = skew_score
    regime.fg_composite = fg_comp
    regime.fg_multiplier = fg_mult
    regime.updated_at = datetime.now()
    regime.save()

    log.info(
        "Regime computed: composite=%.1f multiplier=%.4f  fg_composite=%s fg_multiplier=%s",
        composite or 0, multiplier or 1,
        f"{fg_comp:.1f}" if fg_comp else "n/a",
        f"{fg_mult:.4f}" if fg_mult else "n/a",
    )
    regime._created = created  # expose to caller: True = first compute for this date
    return regime


# ── Backfill ────────────────────────────────────────────────────────

def backfill_regime(days=365):
    """Backfill MarketRegime for historical dates using PriceHistory + yfinance history.

    Fetches VIX, SPY, HYG, LQD, TLT, ^SKEW in a single bulk download, then
    iterates over each PriceHistory date and computes + stores all regime fields
    including the new Fear & Greed proxy signals.

    Does NOT modify historical Score.overall — regime is baked into scores
    when they are (re)calculated via compute_overall_score().
    """
    from colorama import Fore, Style
    from tqdm import tqdm
    from database.models.technical import PriceHistory

    MarketRegime.ensure_schema()

    cutoff = date.today() - timedelta(days=days)
    fetch_period = f'{days + 90}d'   # extra buffer for 20d lookback at start of window

    # Use PriceHistory dates (not Score dates) — regime doesn't depend on scores
    ph_dates = [
        row.date for row in
        PriceHistory.select(PriceHistory.date)
        .where(PriceHistory.date >= cutoff)
        .group_by(PriceHistory.date)
        .order_by(PriceHistory.date)
    ]
    if not ph_dates:
        print(f"{Fore.YELLOW}No PriceHistory dates found in lookback window.{Style.RESET_ALL}")
        return

    # ── Bulk market data fetch ───────────────────────────────────────
    print(f"{Fore.CYAN}Fetching VIX, SPY, HYG, LQD, TLT, ^SKEW history ({fetch_period})...{Style.RESET_ALL}")
    try:
        vix_df = yf.Ticker('^VIX').history(period=fetch_period)
        spy_df = yf.Ticker('SPY').history(period='max')
    except Exception as e:
        print(f"{Fore.RED}Failed to fetch VIX/SPY: {e}{Style.RESET_ALL}")
        return

    if vix_df.empty or spy_df.empty:
        print(f"{Fore.RED}Empty VIX/SPY data.{Style.RESET_ALL}")
        return

    def _strip_tz(df):
        if hasattr(df.index, 'tz') and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df

    vix_df = _strip_tz(vix_df)
    spy_df = _strip_tz(spy_df)

    # Fetch F&G tickers — best-effort, continue even if some fail
    print(f"{Fore.CYAN}Fetching F&G proxy tickers (HYG, LQD, TLT, ^SKEW)...{Style.RESET_ALL}")
    fg_raw_data = fetch_fg_data(period=fetch_period)
    fg_maps = {t: _fg_series_to_date_map(s) for t, s in fg_raw_data.items()}

    # Build SPY date map for TLT/SPY return diff (use already-fetched SPY)
    fg_maps['SPY'] = {
        (idx.date() if hasattr(idx, 'date') else idx): float(v)
        for idx, v in spy_df['Close'].items()
        if not _math.isnan(float(v))
    }

    # Pre-build sorted SKEW history for z-score computation
    skew_map = fg_maps.get('^SKEW', {})
    skew_history = sorted(skew_map.items())   # [(date, value), ...]

    # Report coverage
    for ticker in ['HYG', 'LQD', 'TLT', '^SKEW']:
        n = len(fg_maps.get(ticker, {}))
        color = Fore.GREEN if n > 100 else Fore.YELLOW
        print(f"  {color}{ticker}: {n} dates{Style.RESET_ALL}")

    # ── Per-date computation ─────────────────────────────────────────
    from market_breadth import compute_and_store_breadth

    filled = 0
    for d in tqdm(ph_dates, desc="Backfilling regime"):
        # VIX metrics
        vix_rows = vix_df[vix_df.index.date <= d].tail(11)
        if len(vix_rows) >= 2:
            vc = float(vix_rows['Close'].iloc[-1])
            v_chg = ((vc - float(vix_rows['Close'].iloc[0])) / float(vix_rows['Close'].iloc[0]) * 100
                     if len(vix_rows) >= 11 and float(vix_rows['Close'].iloc[0]) != 0 else None)
        else:
            vc, v_chg = None, None

        # SPY metrics
        spy_up_to = spy_df[spy_df.index.date <= d]
        if len(spy_up_to) >= 200:
            sc = float(spy_up_to['Close'].iloc[-1])
            closes_list = spy_up_to['Close'].tolist()
            se50  = round(compute_ema(closes_list, 50), 2)
            se200 = round(compute_ema(closes_list, 200), 2)
        else:
            sc, se50, se200 = None, None, None

        vix_sc = compute_vix_score(vc, v_chg)
        mkt    = compute_market_trend_score(sc, se50, se200)

        # Breadth
        breadth_row = compute_and_store_breadth(d)
        mb_score = float(breadth_row.breadth_score) if breadth_row and breadth_row.breadth_score is not None else None

        composite = compute_regime_composite(vix_sc, mkt, mb_score, vix_close=vc)
        mult = compute_regime_multiplier(composite)

        # F&G proxy signals
        fg_m = extract_fg_metrics_for_date(d, fg_maps, [], skew_history)
        credit_s, haven_s, skew_s = compute_fg_scores(
            fg_m.get('hyg_lqd_ret_diff'),
            fg_m.get('tlt_spy_ret_diff'),
            fg_m.get('skew_zscore'),
        )
        breadth_inv = (100.0 - mb_score) if mb_score is not None else None
        fg_comp = compute_fg_composite(vix_sc, breadth_inv, credit_s, haven_s, skew_s)
        fg_mult = compute_regime_multiplier(fg_comp) if fg_comp is not None else None

        regime, _ = MarketRegime.get_or_create(date=d)
        regime.vix_close = vc
        regime.vix_10d_change = v_chg
        regime.spy_close = sc
        regime.spy_ema50 = se50
        regime.spy_ema200 = se200
        regime.vix_score = vix_sc
        regime.market_trend_score = mkt
        regime.regime_composite = composite
        regime.regime_multiplier = mult
        regime.hyg_close = fg_m.get('hyg_close')
        regime.lqd_close = fg_m.get('lqd_close')
        regime.tlt_close = fg_m.get('tlt_close')
        regime.skew_close = fg_m.get('skew_close')
        regime.hyg_lqd_ret_diff = fg_m.get('hyg_lqd_ret_diff')
        regime.tlt_spy_ret_diff = fg_m.get('tlt_spy_ret_diff')
        regime.credit_spread_score = credit_s
        regime.haven_score = haven_s
        regime.skew_score = skew_s
        regime.fg_composite = fg_comp
        regime.fg_multiplier = fg_mult
        regime.updated_at = datetime.now()
        regime.save()
        filled += 1

    print(f"{Fore.GREEN}Backfill complete: {filled} days filled.{Style.RESET_ALL}")
    # Coverage summary
    from database.trader_database import DB
    for col in ('credit_spread_score', 'haven_score', 'skew_score', 'fg_composite'):
        try:
            row = DB.execute_sql(f'SELECT COUNT(*) FROM market_regime WHERE {col} IS NOT NULL').fetchone()
            print(f"  {col}: {row[0]} non-null rows")
        except Exception:
            pass
