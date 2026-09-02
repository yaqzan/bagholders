"""
Volume Amplifier — Score multiplier based on volume-price analysis.

MIGRATION (run before deploying):
    ALTER TABLE scores ADD COLUMN volume_signal VARCHAR(20) NULL;
    ALTER TABLE scores ADD COLUMN volume_magnitude FLOAT NULL;
"""

import math
from dataclasses import dataclass
from datetime import datetime, date, timedelta

# ── Tunable constants ─────────────────────────────────────────────

AVG_WINDOW_SHORT           = 50
AVG_WINDOW_LONG            = 200
MIN_HISTORY_DAYS           = 20
CLIMAX_RATIO_THRESHOLD     = 4.0
ELEVATED_RATIO_THRESHOLD   = 1.5
LOW_VOLUME_THRESHOLD       = 0.7
BODY_RATIO_CONVICTION      = 0.6
BODY_RATIO_DOJI            = 0.45
WICK_RATIO_REJECTION       = 2.0
WICK_DOMINANCE_THRESHOLD   = 0.75    # doji with one wick > 75% of total wick → REJECTION not ABSORPTION
WICK_RANGE_REJECTION_THRESHOLD = 0.50  # doji: dominant wick > 50% of full candle range → REJECTION despite mixed wicks
GAP_SIGNAL_THRESHOLD       = 0.02
GAP_MULTIPLIER_BULLISH     = 1.08
GAP_MULTIPLIER_BEARISH     = 0.92
CLIMAX_DAMPENING_FACTOR    = 0.4
MAX_AMPLIFICATION          = 0.55
MAX_SUPPRESSION            = 0.45
THIN_AIR_SUPPRESSION       = 0.30
DIRECTIONAL_GATE_WEAK      = 40
DIRECTIONAL_GATE_STRONG    = 60
GATE_CAP_BULLISH           = 1.15
GATE_CAP_BEARISH           = 0.85
INTRADAY_CONFIDENCE_WINDOW = 90
DECAY_CONTRADICT_FACTOR    = 1.8
DECAY_CONFIRM_FACTOR       = 0.7
DECAY_MAGNITUDE_EXTENSION  = 1.5    # extreme signals decay slower: hl *= (1 + mag * this)
DECAY_LOOKBACK_DAYS        = 10
DECAY_EXTREME_THRESHOLD    = 0.5    # signals above this skip contradiction penalty
RECENT_TREND_DAYS          = 5
ABSORPTION_STRONG_THRESHOLD = 2.5
ABSORPTION_STRONG_BOOST    = 1.35
GAP_ABSORPTION_THRESHOLD   = 0.015   # 1.5% gap flips ABSORPTION direction
REVERSAL_BLEND_MIN         = 0.25
REVERSAL_BLEND_MAX         = 0.45

HALF_LIFE = {
    'CONVICTION': 3.0,
    'ABSORPTION': 2.0,
    'REJECTION':  2.5,
    'CLIMAX':     2.0,
    'THIN_AIR':   1.5,
}

_COMPLETION_CURVE = [
    (0, 0.00), (30, 0.18), (60, 0.30), (90, 0.40), (150, 0.50),
    (210, 0.57), (240, 0.61), (300, 0.68), (330, 0.74), (390, 1.00),
]

NEUTRAL_RESULT = (1.0, 50, 'NEUTRAL', 0.0, 0.0, 50.0)


@dataclass
class VolumeSignal:
    signal_type: str
    direction: int
    raw_magnitude: float
    adjusted_magnitude: float
    ratio_50: float
    ratio_200: float
    percentile_50: float
    window_agreement: float
    regime_discount: float
    intraday_confidence: float
    projected_volume: int
    is_decayed_signal: bool
    days_since_signal: int
    earnings_suppressed: bool
    premarket_gap_pct: float
    is_premarket: bool = False


# ── Pure helpers ──────────────────────────────────────────────────

def _interpolate_completion(elapsed_minutes: float) -> float:
    if elapsed_minutes <= 0:
        return 0.0
    if elapsed_minutes >= 390:
        return 1.0
    for i in range(1, len(_COMPLETION_CURVE)):
        t1, p1 = _COMPLETION_CURVE[i - 1]
        t2, p2 = _COMPLETION_CURVE[i]
        if elapsed_minutes <= t2:
            return p1 + (elapsed_minutes - t1) / (t2 - t1) * (p2 - p1)
    return 1.0


def project_full_day_volume(observed_volume: int, pulled_at: datetime) -> int | None:
    """Project cumulative intraday volume to full-day estimate.
    Returns None if pre-market, observed_volume if after close."""
    h, m = pulled_at.hour, pulled_at.minute
    if h < 9 or (h == 9 and m < 30):
        return None
    if h > 16 or (h == 16 and m >= 0):
        return observed_volume
    elapsed = (h - 9) * 60 + (m - 30)
    pct = _interpolate_completion(elapsed)
    return int(observed_volume / pct) if pct > 0 else None


def _intraday_confidence(pulled_at: datetime) -> float:
    elapsed = (pulled_at.hour - 9) * 60 + (pulled_at.minute - 30)
    return min(1.0, max(0.0, elapsed / INTRADAY_CONFIDENCE_WINDOW))


def _classify_signal(ratio_50, body_ratio, upper_wick, lower_wick, body,
                     candle_direction, recent_closes, today_open=None,
                     prev_close=None):
    """Returns (signal_type, direction). First match wins."""
    if ratio_50 >= CLIMAX_RATIO_THRESHOLD and body_ratio >= 0.5:
        return 'CLIMAX', -candle_direction

    if ratio_50 >= ELEVATED_RATIO_THRESHOLD and body_ratio >= BODY_RATIO_CONVICTION:
        return 'CONVICTION', candle_direction

    if ratio_50 >= ELEVATED_RATIO_THRESHOLD and body_ratio < BODY_RATIO_DOJI:
        wick_total = upper_wick + lower_wick
        dominant_wick = max(upper_wick, lower_wick)
        if wick_total > 0 and dominant_wick / wick_total > WICK_DOMINANCE_THRESHOLD:
            return 'REJECTION', 1 if lower_wick > upper_wick else -1
        # Mixed-wick doji: dominant wick still exceeds half the full candle range → directional rejection
        candle_range = body + wick_total
        if candle_range > 0 and dominant_wick / candle_range > WICK_RANGE_REJECTION_THRESHOLD:
            return 'REJECTION', 1 if lower_wick > upper_wick else -1
        if len(recent_closes) < 2:
            return 'NEUTRAL', 0
        gap_pct = ((today_open - prev_close) / prev_close
                   if prev_close and prev_close != 0 and today_open else 0)
        if abs(gap_pct) >= GAP_ABSORPTION_THRESHOLD:
            return 'ABSORPTION', -1 if gap_pct > 0 else 1
        if recent_closes[0] > recent_closes[-1]:
            return 'ABSORPTION', -1
        elif recent_closes[0] < recent_closes[-1]:
            return 'ABSORPTION', 1
        return 'NEUTRAL', 0

    if ratio_50 >= ELEVATED_RATIO_THRESHOLD and (
        upper_wick > body * WICK_RATIO_REJECTION or
        lower_wick > body * WICK_RATIO_REJECTION
    ):
        return 'REJECTION', 1 if lower_wick > upper_wick else -1

    if ratio_50 < LOW_VOLUME_THRESHOLD and body_ratio >= BODY_RATIO_CONVICTION:
        return 'THIN_AIR', -candle_direction

    return 'NEUTRAL', 0


def _raw_magnitude(ratio_50, percentile_50, signal_type):
    if signal_type == 'THIN_AIR':
        return min(1.0, max(0.0, (LOW_VOLUME_THRESHOLD - ratio_50) / LOW_VOLUME_THRESHOLD))

    capped = min(ratio_50, 4.0)
    mag = math.sqrt(max(0.0, (capped - 1.0) / 3.0))
    bonus = max(0.0, percentile_50 - 0.7) / 0.3 * 0.2
    mag = min(1.0, mag + bonus)

    if signal_type == 'CLIMAX':
        mag *= CLIMAX_DAMPENING_FACTOR

    return mag


def _to_multiplier(signal_type, direction, magnitude):
    if signal_type == 'THIN_AIR':
        return 1.0 - magnitude * THIN_AIR_SUPPRESSION
    if direction == 1:
        return 1.0 + magnitude * MAX_AMPLIFICATION
    if direction == -1:
        return 1.0 - magnitude * MAX_SUPPRESSION
    return 1.0


def _apply_gate(multiplier, direction, pre_volume_overall):
    if pre_volume_overall is None:
        return multiplier
    if pre_volume_overall < DIRECTIONAL_GATE_WEAK and direction == 1:
        multiplier = min(multiplier, GATE_CAP_BULLISH)
    if pre_volume_overall > DIRECTIONAL_GATE_STRONG and direction == -1:
        multiplier = max(multiplier, GATE_CAP_BEARISH)
    return multiplier


def _to_raw_signal(direction, magnitude):
    if direction == 1:
        return int(50 + magnitude * 50)
    if direction == -1:
        return int(50 - magnitude * 50)
    return 50


# ── Core analysis ─────────────────────────────────────────────────

def _neutral(**kw):
    defaults = dict(
        signal_type='NEUTRAL', direction=0, raw_magnitude=0.0,
        adjusted_magnitude=0.0, ratio_50=0.0, ratio_200=0.0,
        percentile_50=0.0, window_agreement=1.0, regime_discount=1.0,
        intraday_confidence=1.0, projected_volume=0,
        is_decayed_signal=False, days_since_signal=0,
        earnings_suppressed=False, premarket_gap_pct=0.0, is_premarket=False,
    )
    defaults.update(kw)
    return VolumeSignal(**defaults)


def _analyze(symbol, today, pulled_at):
    """Steps 1-7: full analysis returning VolumeSignal with all intermediate state."""
    from database.models.core import Stock, Score
    from database.models.technical import PriceHistory

    today_ph = PriceHistory.get_or_none(
        (PriceHistory.symbol == symbol) & (PriceHistory.date == today))

    # No today candle yet (premarket before yfinance row exists, or volume==0):
    # skip candle analysis entirely but still run the decay block so prior
    # day's signal carries forward.
    no_candle = not today_ph or today_ph.volume == 0

    if not no_candle:
        t_open  = float(today_ph.open)
        t_high  = float(today_ph.high)
        t_low   = float(today_ph.low)
        t_close = float(today_ph.close)
        t_vol   = int(today_ph.volume)

        # Use the time the price data was actually pulled, not when the score
        # is being calculated — avoids false premarket/projection results when
        # scores are recalculated after the fact.
        effective_pa = today_ph.pulled_at if today_ph.pulled_at else pulled_at

        is_historical = today < date.today()
        is_premarket = (not is_historical and
                        (effective_pa.hour < 9 or (effective_pa.hour == 9 and effective_pa.minute < 30)))
    else:
        is_historical = today < date.today()
        is_premarket = not is_historical  # treat no-candle as premarket for labelling

    # ── Step 3: Earnings suppression (checked before volume work) ──
    try:
        stock = Stock.get(Stock.symbol == symbol)
    except Stock.DoesNotExist:
        return _neutral()

    try:
        from database.models.core import EarningsDate
        nearby_earnings = EarningsDate.select().where(
            (EarningsDate.symbol == symbol) &
            (EarningsDate.date >= today - timedelta(days=1)) &
            (EarningsDate.date <= today + timedelta(days=1))
        ).exists()
        if nearby_earnings:
            return _neutral(earnings_suppressed=True)
    except Exception:
        pass

    # ── Step 1: Pre-market / no-candle → skip unreliable candle analysis,
    #    fall through to decay so prior day's signal carries forward ──
    premarket_gap = 0.0
    if no_candle or is_premarket:
        prev = (PriceHistory.select()
                .where((PriceHistory.symbol == symbol) & (PriceHistory.date < today))
                .order_by(PriceHistory.date.desc()).first())
        if not no_candle and prev and float(prev.close) != 0:
            premarket_gap = (t_open - float(prev.close)) / float(prev.close)
        sig_type, direction = 'NEUTRAL', 0
        raw_mag, adj_mag = 0.0, 0.0
        r50, r200, pct50 = 0.0, 0.0, 0.0
        w_agree, reg_disc, confidence = 1.0, 1.0, 0.0
        proj_vol = 0
    else:
        # ── Step 2: Volume projection ──
        if is_historical:
            proj_vol, confidence = t_vol, 1.0
        else:
            proj = project_full_day_volume(t_vol, effective_pa)
            if proj is None:
                return _neutral()
            proj_vol = proj
            confidence = _intraday_confidence(effective_pa)

        # ── Step 4: Dual window baseline ──
        history = list(
            PriceHistory.select()
            .where((PriceHistory.symbol == symbol) & (PriceHistory.date < today))
            .order_by(PriceHistory.date.desc())
            .limit(AVG_WINDOW_LONG))

        if len(history) < MIN_HISTORY_DAYS:
            return _neutral()

        vols = [int(h.volume) for h in history]
        short = vols[:AVG_WINDOW_SHORT] if len(vols) >= AVG_WINDOW_SHORT else vols

        avg_50  = sum(short) / len(short)
        avg_200 = sum(vols) / len(vols)
        if avg_50 == 0 or avg_200 == 0:
            return _neutral()

        r50  = proj_vol / avg_50
        r200 = proj_vol / avg_200

        w_agree = 1.0 - min(1.0, abs(r50 - r200) / max(r50, r200, 0.001))
        if len(short) < AVG_WINDOW_SHORT:
            w_agree *= 0.7

        pct50 = sum(1 for v in short if v < proj_vol) / len(short)
        reg_disc = min(1.0, r200 / r50) if r50 > 1.0 else 1.0

        # ── Step 5: Candle classification ──
        c_range = t_high - t_low
        if c_range == 0:
            return _neutral(ratio_50=r50, ratio_200=r200,
                            percentile_50=pct50, projected_volume=proj_vol)

        body    = abs(t_close - t_open)
        body_r  = body / c_range
        u_wick  = t_high - max(t_open, t_close)
        l_wick  = min(t_open, t_close) - t_low
        c_dir   = 1 if t_close >= t_open else -1

        recent_closes = [float(h.close) for h in history[:RECENT_TREND_DAYS]]
        prev_close = float(history[0].close) if history else None

        sig_type, direction = _classify_signal(
            r50, body_r, u_wick, l_wick, body, c_dir, recent_closes,
            today_open=t_open, prev_close=prev_close)

        # ── Step 6: Magnitude ──
        if sig_type == 'NEUTRAL':
            raw_mag, adj_mag = 0.0, 0.0
        else:
            raw_mag = _raw_magnitude(r50, pct50, sig_type)
            adj_mag = raw_mag * w_agree * reg_disc * confidence
            if sig_type == 'ABSORPTION' and r50 >= ABSORPTION_STRONG_THRESHOLD:
                adj_mag = min(1.0, adj_mag * ABSORPTION_STRONG_BOOST)

    # ── Step 7: Decay ──
    priors = list(
        Score.select()
        .where((Score.symbol == symbol) &
               (Score.date < today) &
               (Score.date >= today - timedelta(days=DECAY_LOOKBACK_DAYS)))
        .order_by(Score.date.desc()))

    dec_mag, dec_dir, dec_type = 0.0, 0, 'NEUTRAL'
    is_dec, days_since = False, 0
    first_stored_pm = None

    for p in priors:
        ps = getattr(p, 'volume_signal', None)
        pm = getattr(p, 'volume_magnitude', None)
        if not ps or ps == 'NEUTRAL' or pm is None or pm == 0:
            continue

        if first_stored_pm is not None and float(pm) <= first_stored_pm:
            continue

        days_el = (today - p.date).days
        base_hl = HALF_LIFE.get(ps, 2.0)
        hl = base_hl * (1.0 + float(pm) * DECAY_MAGNITUDE_EXTENSION)
        p_dir = 1 if p.volume > 50 else -1

        sig_ph = PriceHistory.get_or_none(
            (PriceHistory.symbol == symbol) & (PriceHistory.date == p.date))
        nxt_ph = (PriceHistory.select()
                  .where((PriceHistory.symbol == symbol) & (PriceHistory.date > p.date))
                  .order_by(PriceHistory.date.asc()).first())

        eff_hl = hl
        if sig_ph and nxt_ph and float(sig_ph.close) > 0:
            ret = (float(nxt_ph.close) - float(sig_ph.close)) / float(sig_ph.close)
            if (p_dir == 1 and ret < 0) or (p_dir == -1 and ret > 0):
                if float(pm) < DECAY_EXTREME_THRESHOLD:
                    eff_hl = hl / DECAY_CONTRADICT_FACTOR
            elif (p_dir == 1 and ret > 0) or (p_dir == -1 and ret < 0):
                eff_hl = hl / DECAY_CONFIRM_FACTOR

        d_mag = float(pm) * (0.5 ** (days_el / eff_hl))

        if first_stored_pm is None:
            first_stored_pm = float(pm)
            dec_mag, dec_dir, dec_type = d_mag, p_dir, ps
            is_dec, days_since = True, days_el
        elif d_mag > dec_mag:
            dec_mag, dec_dir, dec_type = d_mag, p_dir, ps
            days_since = days_el
            break
        else:
            break

    # Merge: today's fresh signal vs decayed prior
    f_mag, f_dir, f_type = adj_mag, direction, sig_type
    f_is_dec, f_days = False, 0

    if sig_type != 'NEUTRAL' and is_dec:
        if direction == dec_dir and dec_mag > adj_mag:
            f_mag, f_type = dec_mag, dec_type
            f_is_dec, f_days = True, days_since
        # else: today wins (same dir but stronger, or conflicting → today wins)
    elif sig_type == 'NEUTRAL' and is_dec:
        f_mag, f_dir, f_type = dec_mag, dec_dir, dec_type
        f_is_dec, f_days = True, days_since

    # Only flag as premarket when decay couldn't find a prior signal
    still_premarket = is_premarket and f_type == 'NEUTRAL'

    return VolumeSignal(
        signal_type=f_type, direction=f_dir,
        raw_magnitude=raw_mag, adjusted_magnitude=f_mag,
        ratio_50=r50, ratio_200=r200, percentile_50=pct50,
        window_agreement=w_agree, regime_discount=reg_disc,
        intraday_confidence=confidence, projected_volume=proj_vol,
        is_decayed_signal=f_is_dec, days_since_signal=f_days,
        earnings_suppressed=False,
        premarket_gap_pct=premarket_gap if is_premarket else 0.0,
        is_premarket=still_premarket,
    )


# ── Cache-based analysis (no DB calls) ───────────────────────────

def _analyze_from_cache(today, ph_list_desc, prior_vol_signals, earnings_dates_set):
    """
    Identical logic to _analyze() but uses pre-loaded data instead of DB queries.

    ph_list_desc:
        List of price-history-like objects with .date, .open, .high, .low,
        .close, .volume, .pulled_at sorted in DESCENDING date order.
        Must include today's row AND the prior 200 days.

    prior_vol_signals:
        Dict keyed by date → (signal_type: str, magnitude: float, volume_raw: int)
        representing already-computed volume signals for prior score rows.
        Used for the decay step.  Keys only need to cover the decay window
        (DECAY_LOOKBACK_DAYS prior to today).

    earnings_dates_set:
        Set of datetime.date values with earnings within ±1 day of today.
    """
    if not ph_list_desc:
        return _neutral()

    # Today's row must be first (descending order)
    today_ph = ph_list_desc[0] if ph_list_desc[0].date == today else None
    if today_ph is None:
        for ph in ph_list_desc:
            if ph.date == today:
                today_ph = ph
                break
    if today_ph is None or int(today_ph.volume) == 0:
        return _neutral()

    t_open  = float(today_ph.open)
    t_high  = float(today_ph.high)
    t_low   = float(today_ph.low)
    t_close = float(today_ph.close)
    t_vol   = int(today_ph.volume)

    effective_pa = today_ph.pulled_at if getattr(today_ph, 'pulled_at', None) else datetime.now()

    is_historical = today < date.today()
    is_premarket = (not is_historical and
                    (effective_pa.hour < 9 or (effective_pa.hour == 9 and effective_pa.minute < 30)))

    # Earnings suppression
    for ed in (today - timedelta(days=1), today, today + timedelta(days=1)):
        if ed in earnings_dates_set:
            return _neutral(earnings_suppressed=True)

    # history = prior rows (exclude today)
    history = [ph for ph in ph_list_desc if ph.date < today]

    premarket_gap = 0.0
    if is_premarket:
        prev = history[0] if history else None
        if prev and float(prev.close) != 0:
            premarket_gap = (t_open - float(prev.close)) / float(prev.close)
        sig_type, direction = 'NEUTRAL', 0
        raw_mag, adj_mag = 0.0, 0.0
        r50, r200, pct50 = 0.0, 0.0, 0.0
        w_agree, reg_disc, confidence = 1.0, 1.0, 0.0
        proj_vol = 0
    else:
        # Volume projection
        if is_historical:
            proj_vol, confidence = t_vol, 1.0
        else:
            proj = project_full_day_volume(t_vol, effective_pa)
            if proj is None:
                return _neutral()
            proj_vol = proj
            confidence = _intraday_confidence(effective_pa)

        # Dual window baseline
        hist200 = history[:AVG_WINDOW_LONG]
        if len(hist200) < MIN_HISTORY_DAYS:
            return _neutral()

        vols  = [int(h.volume) for h in hist200]
        short = vols[:AVG_WINDOW_SHORT] if len(vols) >= AVG_WINDOW_SHORT else vols

        avg_50  = sum(short) / len(short)
        avg_200 = sum(vols) / len(vols)
        if avg_50 == 0 or avg_200 == 0:
            return _neutral()

        r50  = proj_vol / avg_50
        r200 = proj_vol / avg_200

        w_agree = 1.0 - min(1.0, abs(r50 - r200) / max(r50, r200, 0.001))
        if len(short) < AVG_WINDOW_SHORT:
            w_agree *= 0.7

        pct50    = sum(1 for v in short if v < proj_vol) / len(short)
        reg_disc = min(1.0, r200 / r50) if r50 > 1.0 else 1.0

        c_range = t_high - t_low
        if c_range == 0:
            return _neutral(ratio_50=r50, ratio_200=r200,
                            percentile_50=pct50, projected_volume=proj_vol)

        body   = abs(t_close - t_open)
        body_r = body / c_range
        u_wick = t_high - max(t_open, t_close)
        l_wick = min(t_open, t_close) - t_low
        c_dir  = 1 if t_close >= t_open else -1

        recent_closes = [float(h.close) for h in hist200[:RECENT_TREND_DAYS]]
        prev_close_val = float(hist200[0].close) if hist200 else None

        sig_type, direction = _classify_signal(
            r50, body_r, u_wick, l_wick, body, c_dir, recent_closes,
            today_open=t_open, prev_close=prev_close_val)

        if sig_type == 'NEUTRAL':
            raw_mag, adj_mag = 0.0, 0.0
        else:
            raw_mag = _raw_magnitude(r50, pct50, sig_type)
            adj_mag = raw_mag * w_agree * reg_disc * confidence
            if sig_type == 'ABSORPTION' and r50 >= ABSORPTION_STRONG_THRESHOLD:
                adj_mag = min(1.0, adj_mag * ABSORPTION_STRONG_BOOST)

    # Decay — using prior_vol_signals dict instead of Score DB queries
    # Build a sorted (desc) list of prior signal entries within the decay window
    cutoff_decay = today - timedelta(days=DECAY_LOOKBACK_DAYS)
    prior_entries = sorted(
        [(d, sig, mag, vol_raw) for d, (sig, mag, vol_raw) in prior_vol_signals.items()
         if cutoff_decay <= d < today],
        key=lambda x: x[0], reverse=True
    )

    # Build a date → close map from ph_list_desc for decay direction check
    ph_close_map = {ph.date: float(ph.close) for ph in ph_list_desc}
    ph_dates_sorted_asc = sorted(ph_close_map.keys())

    dec_mag, dec_dir, dec_type = 0.0, 0, 'NEUTRAL'
    is_dec, days_since = False, 0
    first_stored_pm = None

    for p_date, ps, pm, p_vol_raw in prior_entries:
        if not ps or ps == 'NEUTRAL' or pm is None or pm == 0:
            continue
        if first_stored_pm is not None and float(pm) <= first_stored_pm:
            continue

        days_el  = (today - p_date).days
        base_hl  = HALF_LIFE.get(ps, 2.0)
        hl       = base_hl * (1.0 + float(pm) * DECAY_MAGNITUDE_EXTENSION)
        p_dir    = 1 if p_vol_raw > 50 else -1

        sig_close = ph_close_map.get(p_date)
        # next trading day after p_date
        nxt_close = None
        import bisect
        idx = bisect.bisect_right(ph_dates_sorted_asc, p_date)
        if idx < len(ph_dates_sorted_asc):
            nxt_close = ph_close_map[ph_dates_sorted_asc[idx]]

        eff_hl = hl
        if sig_close and nxt_close and sig_close > 0:
            ret = (nxt_close - sig_close) / sig_close
            if (p_dir == 1 and ret < 0) or (p_dir == -1 and ret > 0):
                if float(pm) < DECAY_EXTREME_THRESHOLD:
                    eff_hl = hl / DECAY_CONTRADICT_FACTOR
            elif (p_dir == 1 and ret > 0) or (p_dir == -1 and ret < 0):
                eff_hl = hl / DECAY_CONFIRM_FACTOR

        d_mag = float(pm) * (0.5 ** (days_el / eff_hl))

        if first_stored_pm is None:
            first_stored_pm = float(pm)
            dec_mag, dec_dir, dec_type = d_mag, p_dir, ps
            is_dec, days_since = True, days_el
        elif d_mag > dec_mag:
            dec_mag, dec_dir, dec_type = d_mag, p_dir, ps
            days_since = days_el
            break
        else:
            break

    # Merge fresh signal vs decayed prior
    f_mag, f_dir, f_type = adj_mag, direction, sig_type
    f_is_dec, f_days = False, 0

    if sig_type != 'NEUTRAL' and is_dec:
        if direction == dec_dir and dec_mag > adj_mag:
            f_mag, f_type = dec_mag, dec_type
            f_is_dec, f_days = True, days_since
    elif sig_type == 'NEUTRAL' and is_dec:
        f_mag, f_dir, f_type = dec_mag, dec_dir, dec_type
        f_is_dec, f_days = True, days_since

    still_premarket = is_premarket and f_type == 'NEUTRAL'

    return VolumeSignal(
        signal_type=f_type, direction=f_dir,
        raw_magnitude=raw_mag, adjusted_magnitude=f_mag,
        ratio_50=r50, ratio_200=r200, percentile_50=pct50,
        window_agreement=w_agree, regime_discount=reg_disc,
        intraday_confidence=confidence, projected_volume=proj_vol,
        is_decayed_signal=f_is_dec, days_since_signal=f_days,
        earnings_suppressed=False,
        premarket_gap_pct=premarket_gap if is_premarket else 0.0,
        is_premarket=still_premarket,
    )


def get_volume_multiplier_from_cache(today, ph_list_desc, prior_vol_signals,
                                     earnings_dates_set, pre_volume_overall=None):
    """
    Cache-based equivalent of get_volume_multiplier().
    Returns (multiplier, raw_signal, signal_type, adjusted_magnitude, blend_weight, volume_target).

    ph_list_desc:     descending-sorted PriceHistory-like rows (needs 200+ prior days + today)
    prior_vol_signals: {date: (signal_type, magnitude, volume_raw)} for decay window
    earnings_dates_set: set of date values where earnings occurred (±1 day window)
    """
    try:
        sig = _analyze_from_cache(today, ph_list_desc, prior_vol_signals, earnings_dates_set)

        if sig.is_premarket:
            g = sig.premarket_gap_pct
            if g > GAP_SIGNAL_THRESHOLD:
                return GAP_MULTIPLIER_BULLISH, 50, 'NEUTRAL', 0.0, 0.0, 50.0
            elif g < -GAP_SIGNAL_THRESHOLD:
                return GAP_MULTIPLIER_BEARISH, 50, 'NEUTRAL', 0.0, 0.0, 50.0
            return NEUTRAL_RESULT

        if sig.earnings_suppressed:
            return NEUTRAL_RESULT

        raw = _to_raw_signal(sig.direction, sig.adjusted_magnitude)

        if sig.signal_type in _REVERSAL_SIGNALS and sig.adjusted_magnitude > 0:
            ratio_scale = min(1.0, (sig.ratio_50 - ELEVATED_RATIO_THRESHOLD)
                              / (CLIMAX_RATIO_THRESHOLD - ELEVATED_RATIO_THRESHOLD))
            effective_blend = REVERSAL_BLEND_MIN + ratio_scale * (REVERSAL_BLEND_MAX - REVERSAL_BLEND_MIN)
            blend_w  = sig.adjusted_magnitude * effective_blend
            vol_target = 50.0 + sig.direction * sig.adjusted_magnitude * 50.0
            return 1.0, raw, sig.signal_type, sig.adjusted_magnitude, blend_w, vol_target

        mult = _to_multiplier(sig.signal_type, sig.direction, sig.adjusted_magnitude)
        mult = _apply_gate(mult, sig.direction, pre_volume_overall)
        return mult, raw, sig.signal_type, sig.adjusted_magnitude, 0.0, 50.0

    except Exception:
        return NEUTRAL_RESULT


# ── Public API ────────────────────────────────────────────────────

_REVERSAL_SIGNALS = {'ABSORPTION', 'CLIMAX'}


def get_volume_multiplier(symbol, today, pulled_at, pre_volume_overall=None):
    """
    Returns (multiplier, raw_signal, signal_type, adjusted_magnitude,
             blend_weight, volume_target).

    For continuation signals (CONVICTION, THIN_AIR, REJECTION):
        blend_weight = 0 → caller uses multiplier model
    For reversal signals (ABSORPTION, CLIMAX):
        blend_weight > 0 → caller uses blend model:
        final = weighted_sum * (1 - blend_weight) + volume_target * blend_weight
    """
    try:
        sig = _analyze(symbol, today, pulled_at)

        if sig.is_premarket:
            g = sig.premarket_gap_pct
            if g > GAP_SIGNAL_THRESHOLD:
                return GAP_MULTIPLIER_BULLISH, 50, 'NEUTRAL', 0.0, 0.0, 50.0
            elif g < -GAP_SIGNAL_THRESHOLD:
                return GAP_MULTIPLIER_BEARISH, 50, 'NEUTRAL', 0.0, 0.0, 50.0
            return NEUTRAL_RESULT

        if sig.earnings_suppressed:
            return NEUTRAL_RESULT

        raw = _to_raw_signal(sig.direction, sig.adjusted_magnitude)

        if sig.signal_type in _REVERSAL_SIGNALS and sig.adjusted_magnitude > 0:
            ratio_scale = min(1.0, (sig.ratio_50 - ELEVATED_RATIO_THRESHOLD)
                              / (CLIMAX_RATIO_THRESHOLD - ELEVATED_RATIO_THRESHOLD))
            effective_blend = REVERSAL_BLEND_MIN + ratio_scale * (REVERSAL_BLEND_MAX - REVERSAL_BLEND_MIN)
            blend_w = sig.adjusted_magnitude * effective_blend
            vol_target = 50.0 + sig.direction * sig.adjusted_magnitude * 50.0
            return 1.0, raw, sig.signal_type, sig.adjusted_magnitude, blend_w, vol_target

        mult = _to_multiplier(sig.signal_type, sig.direction, sig.adjusted_magnitude)
        mult = _apply_gate(mult, sig.direction, pre_volume_overall)
        return mult, raw, sig.signal_type, sig.adjusted_magnitude, 0.0, 50.0

    except Exception:
        return NEUTRAL_RESULT


def explain_volume_signal(symbol, target_date, pre_volume_overall=None):
    """Print human-readable breakdown of every calculation step."""
    from colorama import Fore, Style

    sig = _analyze(symbol, target_date, datetime.now())

    print(f"\n{'='*70}")
    print(f"{Fore.CYAN}VOLUME AMPLIFIER: {symbol} on {target_date}{Style.RESET_ALL}")
    print(f"{'='*70}")

    if sig.is_premarket:
        g = sig.premarket_gap_pct
        m = GAP_MULTIPLIER_BULLISH if g > GAP_SIGNAL_THRESHOLD else (
            GAP_MULTIPLIER_BEARISH if g < -GAP_SIGNAL_THRESHOLD else 1.0)
        print(f"\n  Pre-market | Gap: {g:+.2%} | Multiplier: {m}")
        print(f"{'='*70}\n")
        return

    if sig.earnings_suppressed:
        print(f"\n  {Fore.YELLOW}EARNINGS SUPPRESSION — multiplier = 1.0{Style.RESET_ALL}")
        print(f"{'='*70}\n")
        return

    raw = _to_raw_signal(sig.direction, sig.adjusted_magnitude)
    is_reversal = sig.signal_type in _REVERSAL_SIGNALS and sig.adjusted_magnitude > 0

    dir_label = {1: '+1 bullish', -1: '-1 bearish', 0: '0 neutral'}.get(sig.direction, '?')

    print(f"\n  Projected Volume:  {sig.projected_volume:>12,}")
    print(f"  Ratio (50d):       {sig.ratio_50:>12.2f}")
    print(f"  Ratio (200d):      {sig.ratio_200:>12.2f}")
    print(f"  Percentile (50d):  {sig.percentile_50:>11.1%}")

    print(f"\n  Signal:            {sig.signal_type}")
    print(f"  Direction:         {dir_label}")
    print(f"  Raw Magnitude:     {sig.raw_magnitude:.4f}")

    print(f"\n  Confidence Factors:")
    print(f"    Window Agreement:  {sig.window_agreement:.3f}")
    print(f"    Regime Discount:   {sig.regime_discount:.3f}")
    print(f"    Intraday Conf.:    {sig.intraday_confidence:.3f}")
    print(f"  Adjusted Magnitude:  {sig.adjusted_magnitude:.4f}")

    if sig.is_decayed_signal:
        print(f"\n  {Fore.YELLOW}Carried from {sig.days_since_signal}d ago via decay{Style.RESET_ALL}")

    if is_reversal:
        ratio_scale = min(1.0, (sig.ratio_50 - ELEVATED_RATIO_THRESHOLD)
                          / (CLIMAX_RATIO_THRESHOLD - ELEVATED_RATIO_THRESHOLD))
        effective_blend = REVERSAL_BLEND_MIN + ratio_scale * (REVERSAL_BLEND_MAX - REVERSAL_BLEND_MIN)
        blend_w = sig.adjusted_magnitude * effective_blend
        vol_target = 50.0 + sig.direction * sig.adjusted_magnitude * 50.0
        print(f"\n  {Fore.MAGENTA}Reversal Blend Model:{Style.RESET_ALL}")
        print(f"    Ratio Scale:     {ratio_scale:.3f} (blend range {REVERSAL_BLEND_MIN}-{REVERSAL_BLEND_MAX})")
        print(f"    Effective Blend: {effective_blend:.4f}")
        print(f"    Blend Weight:    {blend_w:.4f}")
        print(f"    Volume Target:   {vol_target:.1f}")
        if pre_volume_overall is not None:
            blended = pre_volume_overall * (1 - blend_w) + vol_target * blend_w
            print(f"    Example:         {pre_volume_overall} -> {blended:.1f}")
    else:
        mult = _to_multiplier(sig.signal_type, sig.direction, sig.adjusted_magnitude)
        gated = _apply_gate(mult, sig.direction, pre_volume_overall)
        print(f"\n  Multiplier:        {mult:.4f}")
        if pre_volume_overall is not None and gated != mult:
            print(f"  After Gate:        {gated:.4f}  (pre-vol overall={pre_volume_overall})")

    print(f"  Raw Signal:        {raw}")
    print(f"{'='*70}\n")
