"""
DTE Recommendation Module

Infers the appropriate options DTE (days to expiration, CALENDAR days) for a
given signal context. DTE now aligns directly with assessment period buckets
(both are calendar days) — no trading↔calendar conversion needed.

Derived from unscaled cumulative win-rate curves on 3y assessment data
(9,439 HIGH peaks, post calendar-day conversion). Unscaled WR tells us what
fraction of eventual winners have triggered by day N — the right lens for DTE
because it answers "when does the winning move typically land?"

Data highlights per HIGH bucket (unscaled WR @ 30d / 60d / 90d):
  70+ : 73 / 80 / 82  — 30d captures 89% of eventual wins
  80+ : 74 / 82 / 84  — 30d captures 88%
  85+ : 80 / 87 / 88  — 30d captures 90%
  90+ : 84 / 94 / 97  — 30d captures 87%; 60d captures 97%
  95+ : 100 / 100 / 100 — resolves by day 15 (tiny n=8)

Diminishing returns inflection is at **30-35 DTE across all buckets**.
Higher-conviction signals resolve FASTER (95+ by day 15; 70+ spreads to 60d),
inverting the old "higher score = longer DTE" assumption.

Thesis holding windows:
  - BOUNCE:   7-14 DTE  — short-term reflexive mean reversion
  - REVERSAL: 14-21 DTE — volume-exhaustion signal, quick reversal
  - MOMENTUM: 21-35 DTE — score-driven move, default
  - TREND:    28-45 DTE — strong directional signal, widest range
"""

from datetime import date, timedelta
import numpy as np

import entry_filter


# ---------------------------------------------------------------------------
# Thesis DTE ranges
# ---------------------------------------------------------------------------

THESIS_RANGES = {
    'BOUNCE':    (7,  14),
    'REVERSAL':  (14, 21),
    'MOMENTUM':  (21, 35),
    'TREND':     (28, 45),
    'FILTERED':  (0,  0),   # entry_filter rejected — not tradeable
}

# entry_filter validated cells (calendar DTE) — NEEDS REVALIDATION (session 5)
#   HIGH score (calls): target ~42 cal DTE (session-4 LOW-gate window, now repurposed)
#   LOW  score (puts):  target ~60 cal DTE (session-4 HIGH-gate window, now repurposed)
EF_DTE_RANGES = {
    'long':  (28, 45),   # HIGH score call gate target ~42 cal DTE
    'short': (45, 75),   # LOW score put gate target ~60 cal DTE
}
EF_DTE_TARGET = {
    'long':  42,
    'short': 60,
}

# Score-based DTE targets (calendar days) within each thesis range.
# Anchored on unscaled-WR inflection points: for each bucket, pick a DTE that
# captures ~90% of eventual winners with minimal theta beyond that.
_SCORE_DTE_TARGET = [
    (95, 30),   # 95+ resolves by day 15 (n=8, small sample); follow 90+ shape
    (90, 35),   # 84% WR @30d rising to 94% @60d — 35 captures main wave + buffer
    (85, 30),   # 80% @30d, 87% @60d — 30 captures 90% of winners
    (80, 30),   # 74% @30d, 82% @60d — 30 captures 88%
    (75, 35),   # slower tail — stretch a week for safety
    (70, 35),
    (0,  21),   # sub-70 / low confidence — limit theta exposure
]


def _dte_target_for_score(score: int) -> int:
    for threshold, target in _SCORE_DTE_TARGET:
        if score >= threshold:
            return target
    return 10


def recommend_dte(
    symbol: str,
    score: int,
    volume_signal: str | None,
    volume_magnitude: float | None,
    pct_from_ema50: float,
    pct_from_ema200: float,
    bb_position: float,
    score_velocity_7d: int | None,
    signal_features: dict | None = None,
    target_date: date | None = None,
) -> dict:
    """
    Recommend an options DTE range based on signal context.

    Parameters
    ----------
    symbol            : Stock ticker (informational only)
    score             : Current overall score (0-100)
    volume_signal     : One of CONVICTION, ABSORPTION, REJECTION, CLIMAX, THIN_AIR, NEUTRAL (or None)
    volume_magnitude  : Adjusted magnitude 0.0-1.0 (or None if not computed)
    pct_from_ema50    : (price - ema50) / ema50 * 100. Negative = below EMA.
    pct_from_ema200   : (price - ema200) / ema200 * 100. Negative = below EMA.
    bb_position       : Price position within Bollinger Bands. 0 = lower band, 1 = upper band.
    score_velocity_7d : overall today minus overall ~7 trading days ago. None if unavailable.

    Returns
    -------
    dict with keys: dte_min, dte_max, dte_target, thesis, rationale, confidence
    """
    sig = (volume_signal or 'NEUTRAL').upper()
    mag = volume_magnitude if volume_magnitude is not None else 0.0
    vel = score_velocity_7d if score_velocity_7d is not None else 0

    # ------------------------------------------------------------------
    # entry_filter gate (session 4 OOS-validated)
    # ------------------------------------------------------------------
    # Build the feature dict entry_filter expects. macdh / vol_ratio /
    # ret_20d_prior come from the caller via signal_features kwarg below;
    # if not supplied we cannot evaluate the gate and treat it as
    # non-tradeable (same as the legacy "show recommendation but no badge"
    # behavior).
    ef_features = {
        'ext':           pct_from_ema50,
        'macdh':         signal_features.get('macdh')         if signal_features else None,
        'vol_ratio':     signal_features.get('vol_ratio')     if signal_features else None,
        'ret_20d_prior': signal_features.get('ret_20d_prior') if signal_features else None,
    }
    eval_date = target_date or date.today()
    ef_tradeable, ef_reason, ef_side = entry_filter.evaluate(
        symbol, eval_date, score, features=ef_features
    )

    # ------------------------------------------------------------------
    # Thesis classification (priority order)
    # ------------------------------------------------------------------
    thesis = None
    rationale_parts = []

    # 1. REVERSAL — climax exhaustion, near-term uncertain duration
    if sig == 'CLIMAX' and mag >= 0.6:
        thesis = 'REVERSAL'
        rationale_parts.append(f'CLIMAX signal (magnitude {mag:.2f}) indicates volume exhaustion — reversal is near-term')

    # 2. BOUNCE — structurally broken + mean-reversion signal
    if thesis is None and pct_from_ema50 <= -15 and sig in ('ABSORPTION', 'REJECTION'):
        thesis = 'BOUNCE'
        rationale_parts.append(
            f'Price {pct_from_ema50:.1f}% below EMA50 (structurally broken) with {sig} signal — '
            f'thesis is short-term dead-cat bounce to nearby mean reversion target'
        )

    # 3. TREND — 95+ signal near EMAs. Unscaled WR @30d is 100% (n=8) but tiny
    #    sample; we still reserve TREND's wider upper range for its EV upside.
    if thesis is None and score >= 95 and pct_from_ema50 >= -5 and vel >= 0:
        thesis = 'TREND'
        rationale_parts.append(
            f'Score {score} with price near EMAs ({pct_from_ema50:.1f}% from EMA50) — '
            f'strong directional conviction; 30-DTE captures typical resolution with upside to 45'
        )

    # 4. MOMENTUM — default for clear directional signals in mid-score range
    if thesis is None:
        thesis = 'MOMENTUM'
        if score >= 90:
            rationale_parts.append(f'Score {score} with directional signal — momentum thesis, 28-35 DTE range')
        elif score >= 85:
            rationale_parts.append(f'Score {score} — moderate momentum thesis, 21-28 DTE range')
        else:
            rationale_parts.append(f'Score {score} — ambiguous context, defaulting to MOMENTUM; consider waiting for higher score')

    # ------------------------------------------------------------------
    # DTE range and target
    # ------------------------------------------------------------------
    if ef_tradeable:
        # entry_filter validated cell overrides the legacy thesis ladder
        dte_min, dte_max = EF_DTE_RANGES[ef_side]
        dte_target = EF_DTE_TARGET[ef_side]
        rationale_parts.insert(
            0,
            f'entry_filter {ef_side.upper()} gate cleared ({ef_reason}) — '
            f'using OOS-validated {dte_target}-DTE cell'
        )
    else:
        dte_min, dte_max = THESIS_RANGES[thesis]
        dte_target = _dte_target_for_score(score)

        # Clamp target to thesis range
        dte_target = max(dte_min, min(dte_max, dte_target))

        # BOUNCE compression: if price is very far below both EMAs, favour shorter end
        if thesis == 'BOUNCE' and pct_from_ema50 <= -25:
            dte_target = dte_min
            rationale_parts.append(f'DTE compressed to minimum — price {pct_from_ema50:.1f}% below EMA50 limits realistic recovery window')

        # TREND expansion: high score + rising velocity justifies upper end
        if thesis == 'TREND' and vel >= 10:
            dte_target = dte_max
            rationale_parts.append(f'Rising score velocity ({vel:+d}) supports full {dte_max}-DTE coverage')

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------
    confidence = 'MEDIUM'

    # Force LOW conditions
    force_low = (
        score < 75
        or sig == 'THIN_AIR'
        or pct_from_ema50 < -25
        or (sig == 'NEUTRAL' and score < 85)
    )

    if force_low:
        confidence = 'LOW'
        if score < 75:
            rationale_parts.append(f'LOW confidence: score {score} below 75 — near-zero IC per assessment data')
        if sig == 'THIN_AIR':
            rationale_parts.append('LOW confidence: THIN_AIR signal suppresses direction clarity')
        if pct_from_ema50 < -25 and sig != 'THIN_AIR':
            rationale_parts.append(f'LOW confidence: extreme EMA distance ({pct_from_ema50:.1f}%) increases recovery uncertainty')
    elif (
        score >= 95
        and mag >= 0.5
        and sig != 'NEUTRAL'
        and abs(pct_from_ema50) <= 10
    ):
        confidence = 'HIGH'
    elif score >= 85 or (sig in ('CONVICTION', 'ABSORPTION') and mag >= 0.4):
        confidence = 'MEDIUM'
    else:
        confidence = 'LOW'

    return {
        'symbol': symbol,
        'dte_min': dte_min,
        'dte_max': dte_max,
        'dte_target': dte_target,
        'thesis': thesis,
        'rationale': ' | '.join(rationale_parts),
        'confidence': confidence,
        'tradeable': ef_tradeable,
        'filter_side': ef_side,
        'filter_reason': ef_reason,
        'inputs': {
            'score': score,
            'volume_signal': sig,
            'volume_magnitude': round(mag, 3),
            'pct_from_ema50': round(pct_from_ema50, 2),
            'pct_from_ema200': round(pct_from_ema200, 2),
            'bb_position': round(bb_position, 3),
            'score_velocity_7d': vel,
        },
    }


# ---------------------------------------------------------------------------
# Signal context helper — computes inputs from PriceHistory + Score tables
# ---------------------------------------------------------------------------

def get_signal_context(symbol: str, target_date: date | None = None) -> dict | None:
    """
    Compute all inputs for recommend_dte() from existing database tables.

    Fetches the last 200 days of PriceHistory to compute EMA50, EMA200,
    Bollinger Bands (20-day), and raw BB position. Reads the current Score
    record for volume_signal, volume_magnitude, and overall. Derives
    score_velocity_7d by comparing today's overall to the score ~7 trading
    days ago.

    Returns None if insufficient price history or no score record found.
    """
    from database import PriceHistory, Score

    if target_date is None:
        target_date = date.today()

    # Fetch last 200 trading days of price history
    rows = (
        PriceHistory.select()
        .where(PriceHistory.symbol == symbol.upper())
        .where(PriceHistory.date <= target_date)
        .order_by(PriceHistory.date.desc())
        .limit(200)
        .namedtuples()
    )
    rows = list(rows)

    if len(rows) < 50:
        return None  # Not enough history for reliable indicators

    # Reverse to chronological order
    rows = list(reversed(rows))
    closes = np.array([float(r.close) for r in rows])

    # EMA50
    ema50 = _ema(closes, 50)
    # EMA200 — may be unreliable if < 200 bars, but provide best estimate
    ema200 = _ema(closes, min(200, len(closes)))

    price = closes[-1]
    pct_from_ema50 = (price - ema50) / ema50 * 100 if ema50 else 0.0
    pct_from_ema200 = (price - ema200) / ema200 * 100 if ema200 else 0.0

    # Bollinger Bands (20-day SMA ± 2σ)
    period = 20
    if len(closes) >= period:
        window = closes[-period:]
        sma = np.mean(window)
        std = np.std(window, ddof=1)
        lower_bb = sma - 2 * std
        upper_bb = sma + 2 * std
        band_range = upper_bb - lower_bb
        bb_position = float(np.clip((price - lower_bb) / band_range, 0.0, 1.0)) if band_range > 0 else 0.5
    else:
        bb_position = 0.5

    # Current Score record
    score_record = Score.latest(symbol.upper(), target_date)
    if score_record is None:
        # Try the most recent score on or before target_date
        score_record = (
            Score.select()
            .where(Score.symbol == symbol.upper())
            .where(Score.date <= target_date)
            .order_by(Score.date.desc())
            .first()
        )
    if score_record is None:
        return None

    overall = score_record.overall
    volume_signal = score_record.volume_signal
    volume_magnitude = float(score_record.volume_magnitude) if score_record.volume_magnitude is not None else None

    # Score velocity: overall now vs ~7 trading days ago
    score_velocity_7d = None
    prior_score = (
        Score.select()
        .where(Score.symbol == symbol.upper())
        .where(Score.date < target_date)
        .order_by(Score.date.desc())
        .offset(6)   # skip 6 → 7th most recent prior date ≈ 7 trading days ago
        .first()
    )
    if prior_score is not None:
        score_velocity_7d = overall - prior_score.overall

    # entry_filter features (macdh, vol_ratio, ret_20d_prior).
    # macdh comes from Indicator on the matching score date; vol_ratio = today
    # volume vs trailing 50d avg; ret_20d_prior = 20-bar return ending today.
    signal_features = _entry_filter_features(rows, symbol.upper(), score_record.date)

    return {
        'symbol': symbol.upper(),
        'score': overall,
        'volume_signal': volume_signal,
        'volume_magnitude': volume_magnitude,
        'pct_from_ema50': round(pct_from_ema50, 2),
        'pct_from_ema200': round(pct_from_ema200, 2),
        'bb_position': round(bb_position, 3),
        'score_velocity_7d': score_velocity_7d,
        'signal_features': signal_features,
        'target_date': score_record.date,
    }


def _entry_filter_features(price_rows, symbol: str, target_date: date) -> dict:
    """Build the (ext, macdh, vol_ratio, ret_20d_prior) feature dict that
    entry_filter.evaluate() expects. `price_rows` is the chronological
    PriceHistory list already loaded by get_signal_context."""
    from database import Indicator

    if len(price_rows) < 21:
        return {'ext': None, 'macdh': None, 'vol_ratio': None, 'ret_20d_prior': None}

    closes = [float(r.close) for r in price_rows]
    vols = [float(r.volume) if r.volume is not None else None for r in price_rows]
    last_idx = len(price_rows) - 1

    ret_20d_prior = ((closes[last_idx] - closes[last_idx - 20]) / closes[last_idx - 20] * 100
                     if last_idx >= 20 and closes[last_idx - 20] else None)

    vol_ratio = None
    if vols[last_idx] is not None and last_idx >= 50:
        recent = [v for v in vols[last_idx - 50:last_idx] if v is not None]
        if recent:
            avg50 = sum(recent) / len(recent)
            if avg50:
                vol_ratio = vols[last_idx] / avg50

    ind = Indicator.get_or_none((Indicator.symbol == symbol) & (Indicator.date == target_date))
    macdh = float(ind.macd_hist) if (ind and ind.macd_hist is not None) else None

    return {
        'ext': None,                 # entry_filter ignores ext on the LOW gate; HIGH ghost gate uses pct_from_ema50 — caller passes it explicitly
        'macdh': macdh,
        'vol_ratio': vol_ratio,
        'ret_20d_prior': ret_20d_prior,
    }


def _ema(prices: np.ndarray, period: int) -> float:
    """Compute EMA of the last element using standard ewm (Wilder-style multiplier)."""
    if len(prices) < period:
        return float(np.mean(prices))
    k = 2.0 / (period + 1)
    ema = float(np.mean(prices[:period]))
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema
