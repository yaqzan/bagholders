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

def compute_and_apply_regime(pull_date=None, vix_df=None, spy_df=None):
    """Compute regime for pull_date and apply to all scored stocks.

    Returns the MarketRegime row, or None if regime was skipped.
    """
    if pull_date is None:
        pull_date = date.today()

    MarketRegime.ensure_schema()
    version = AlgorithmVersion.get_or_create_current()

    # Coverage gate: need 80% of tracked stocks scored today
    total_stocks = Stock.select().where(Stock.forward_pe.is_null(False)).count()
    scored_today = Score.select().where(
        Score.date == pull_date,
        Score.overall.is_null(False),
        Score.version == version,
    ).count()

    if total_stocks > 0 and scored_today / total_stocks < MIN_COVERAGE_PCT:
        log.warning(
            "Regime skipped: only %d/%d stocks scored (%.0f%%, need %.0f%%)",
            scored_today, total_stocks,
            scored_today / total_stocks * 100, MIN_COVERAGE_PCT * 100,
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

    regime, _ = MarketRegime.get_or_create(date=pull_date)
    regime.vix_close = vix_close
    regime.vix_10d_change = vix_10d_change
    regime.spy_close = spy_close
    regime.spy_ema50 = spy_ema50
    regime.spy_ema200 = spy_ema200
    regime.vix_score = vix_sc
    regime.market_trend_score = market_trend
    regime.regime_composite = composite
    regime.regime_multiplier = multiplier
    regime.updated_at = datetime.now()
    regime.save()

    # Pass 2: apply multiplier to all of today's scores
    _apply_regime_to_scores(pull_date, version, composite, multiplier)

    return regime


def _apply_regime_to_scores(pull_date, version, composite, multiplier):
    """Bulk-update Score.overall with the regime multiplier."""
    scores = list(Score.select().where(
        Score.date == pull_date,
        Score.overall.is_null(False),
        Score.version == version,
    ))
    for score in scores:
        score.regime_composite = composite
        score.regime_multiplier = multiplier
        score.overall = apply_regime_to_score(score.overall, multiplier)
        score.save()
    log.info(
        "Regime applied: composite=%.1f multiplier=%.4f to %d scores",
        composite or 0, multiplier or 1, len(scores),
    )


# ── Backfill ────────────────────────────────────────────────────────

def backfill_regime(days=365):
    """Backfill MarketRegime for historical dates using stored scores + yfinance history.

    Does NOT modify historical Score.overall.
    """
    from colorama import Fore, Style
    from tqdm import tqdm

    MarketRegime.ensure_schema()

    cutoff = date.today() - timedelta(days=days)
    version = AlgorithmVersion.get_or_create_current()

    scored_dates = [
        row.date for row in
        Score.select(Score.date)
        .where(Score.date >= cutoff, Score.overall.is_null(False), Score.version == version)
        .group_by(Score.date)
        .order_by(Score.date)
    ]
    if not scored_dates:
        print(f"{Fore.YELLOW}No scored dates found in lookback window.{Style.RESET_ALL}")
        return

    print(f"{Fore.CYAN}Fetching VIX and SPY history...{Style.RESET_ALL}")
    try:
        vix_df = yf.Ticker('^VIX').history(period=f'{days + 60}d')
        spy_df = yf.Ticker('SPY').history(period='max')
    except Exception as e:
        print(f"{Fore.RED}Failed to fetch market data: {e}{Style.RESET_ALL}")
        return

    if vix_df.empty or spy_df.empty:
        print(f"{Fore.RED}Empty market data returned.{Style.RESET_ALL}")
        return

    vix_df.index = vix_df.index.tz_localize(None)
    spy_df.index = spy_df.index.tz_localize(None)

    filled = 0
    for d in tqdm(scored_dates, desc="Backfilling regime"):
        # VIX metrics for this date
        vix_rows = vix_df[vix_df.index.date <= d].tail(11)
        if len(vix_rows) >= 2:
            vc = float(vix_rows['Close'].iloc[-1])
            if len(vix_rows) >= 11:
                v10 = float(vix_rows['Close'].iloc[0])
                v_chg = ((vc - v10) / v10 * 100) if v10 != 0 else None
            else:
                v_chg = None
        else:
            vc, v_chg = None, None

        # SPY metrics for this date
        spy_up_to = spy_df[spy_df.index.date <= d]
        if len(spy_up_to) >= 200:
            sc = float(spy_up_to['Close'].iloc[-1])
            closes_list = spy_up_to['Close'].tolist()
            se50 = round(compute_ema(closes_list, 50), 2)
            se200 = round(compute_ema(closes_list, 200), 2)
        else:
            sc, se50, se200 = None, None, None

        vix_sc = compute_vix_score(vc, v_chg)
        mkt = compute_market_trend_score(sc, se50, se200)

        # Breadth score for this historical date
        from market_breadth import compute_and_store_breadth
        breadth_row = compute_and_store_breadth(d)
        mb_score = float(breadth_row.breadth_score) if breadth_row and breadth_row.breadth_score is not None else None

        composite = compute_regime_composite(vix_sc, mkt, mb_score, vix_close=vc)
        mult = compute_regime_multiplier(composite)

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
        regime.updated_at = datetime.now()
        regime.save()
        filled += 1

    print(f"{Fore.GREEN}Backfill complete: {filled} days filled.{Style.RESET_ALL}")
