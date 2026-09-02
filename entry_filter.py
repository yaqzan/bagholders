"""Entry filter — gates score-based signals through OOS-validated criteria.

SCORING SEMANTICS:
  HIGH score (≥70) = bullish directional signal → CALL opportunity
  LOW  score (≤25) = bearish directional signal → PUT opportunity

  Mean-reversion is the primary mechanism but direction follows the score:
  a stock bouncing from a mean in an uptrend scores HIGH (call); a stock
  rolling over from a mean in a downtrend scores LOW (put).

⚠️  GATES BELOW ARE UNDER REVIEW (session 5):
  The session-4 validated gates were researched under an inverted framing
  (LOW=calls, HIGH=puts — the opposite of the correct scoring semantics).
  The measured win rates are real but the DIRECTION was backwards.
  These gates must be revalidated with the correct framing:
    HIGH scores → call gates (expect rise)
    LOW  scores → put gates  (expect drop)
  Until revalidated, the 'long'/'short' return values and the gate logic
  below reflect the OLD inverted framing and should NOT be used for live
  trading decisions without re-running the experiments.

Session-4 measured results (inverted framing, kept for reference):

  LOW score (long calls — NOW UNDERSTOOD AS: score was mis-framed):
    Strategy: score in {16-19} U {23-25}
              AND ret_20d_prior > 0  AND vol_ratio >= 1.0  AND macd_hist > 0
    Cell: K=2.0s target / M=5.0s stop / W=30 trading days
    Test (365d): N=174  p_win=71.8%  EV=+21.5%  (VALIDATED but framing wrong)

  HIGH score (long puts — NOW UNDERSTOOD AS: score was mis-framed):
    Strategy: band == '80-84'  AND vol_ratio >= 1.0  AND macd_hist < 0
              AND NOT (ret_20d_prior < -10 AND ext < 0)   ← ghost gate
    Cell: K=1.0s target / M=2.0s stop / W=42 trading days
    Test (365d): N=56  p_win=69.6%  EV=+7.0%  (MARGINAL, ghost gate +5.1pp OOS)

Public API:
    evaluate(symbol_id, date, score, features=None)
        -> (tradeable: bool, reason: str, side: 'long'|'short'|None)

If `features` is provided, the gate logic runs against the supplied dict
(used by integration tests / bulk replay). Otherwise the function reads
PriceHistory + Indicator at the given date and computes them itself.

DO NOT modify gate values without rerunning out-of-sample validation via
    python -m experiments.probe_maturity_acceleration --validate-low --days 1095
    python -m experiments.probe_maturity_acceleration --validate-high --days 1095
"""

from datetime import timedelta
from database.models.technical import PriceHistory, Indicator


SWEET_LOW_BANDS = {'16-19', '23-25'}


def _band(score):
    if score >= 90: return '90+'
    if score >= 85: return '85-89'
    if score >= 80: return '80-84'
    if score >= 75: return '75-79'
    if score >= 70: return '70-74'
    if score <= 5:  return '<=5'
    if score <= 15: return '6-15'
    if score <= 19: return '16-19'
    if score <= 22: return '20-22'
    return '23-25'


def _compute_features(symbol_id, date):
    """Read PriceHistory + Indicator and derive (ext, macdh, vol_ratio,
    ret_20d_prior). Returns None if data is insufficient."""
    earliest = date - timedelta(days=120)
    latest = date + timedelta(days=2)

    rows = list(PriceHistory.select()
                .where((PriceHistory.symbol == symbol_id) &
                       (PriceHistory.date >= earliest) &
                       (PriceHistory.date <= latest))
                .order_by(PriceHistory.date))
    if not rows:
        return None

    # snap to nearest trading bar on/before `date`
    idx = None
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].date <= date:
            idx = i
            break
    if idx is None or idx < 50:
        return None

    bar = rows[idx]
    ind = Indicator.get_or_none((Indicator.symbol == symbol_id) &
                                (Indicator.date == bar.date))
    if ind is None or ind.ema_50 is None:
        return None

    closes = [float(r.close) for r in rows]
    vols = [float(r.volume) if r.volume is not None else None for r in rows]

    ema50 = float(ind.ema_50)
    macdh = float(ind.macd_hist) if ind.macd_hist is not None else None

    ext = (closes[idx] - ema50) / ema50 * 100
    ret_20d_prior = ((closes[idx] - closes[idx - 20]) / closes[idx - 20] * 100
                     if idx >= 20 else None)

    if vols[idx] is not None and idx >= 50:
        recent = [v for v in vols[idx - 50:idx] if v is not None]
        avg50 = sum(recent) / len(recent) if recent else None
        vol_ratio = vols[idx] / avg50 if avg50 else None
    else:
        vol_ratio = None

    return {
        'ext': ext,
        'macdh': macdh,
        'vol_ratio': vol_ratio,
        'ret_20d_prior': ret_20d_prior,
    }


def evaluate(symbol_id, date, score, features=None):
    """Decide whether a (symbol, date, score) signal is tradeable.

    Returns (tradeable, reason, side) where side is 'long' for LOW hits,
    'short' for HIGH hits, or None when not tradeable.
    """
    if 25 < score < 70:
        return (False, 'score in neutral zone (26-69)', None)

    if features is None:
        features = _compute_features(symbol_id, date)
        if features is None:
            return (False, 'insufficient feature history', None)

    macdh = features.get('macdh')
    vol_ratio = features.get('vol_ratio')
    ret_20d_prior = features.get('ret_20d_prior')
    ext = features.get('ext')

    band = _band(score)

    # ── LOW score (put) gate — NEEDS REVALIDATION under correct framing ──
    if score <= 25:
        if band not in SWEET_LOW_BANDS:
            return (False, f'LOW band {band} not in sweet set', None)
        if ret_20d_prior is None or ret_20d_prior <= 0:
            return (False, 'ret_20d_prior missing or <= 0', None)
        if vol_ratio is None or vol_ratio < 1.0:
            return (False, 'vol_ratio missing or < 1.0', None)
        if macdh is None or macdh <= 0:
            return (False, 'macdh missing or <= 0', None)
        return (True, f'LOW sweet band {band}, 3 gates pass (NEEDS REVALIDATION)', 'short')

    # ── HIGH score (call) gate — NEEDS REVALIDATION under correct framing ──
    if band != '80-84':
        return (False, f'HIGH band {band} not productive (80-84 only)', None)
    if vol_ratio is None or vol_ratio < 1.0:
        return (False, 'vol_ratio missing or < 1.0', None)
    if macdh is None or macdh >= 0:
        return (False, 'macdh missing or >= 0', None)
    if ret_20d_prior is None or ext is None:
        return (False, 'ret_20d_prior or ext missing (ghost check)', None)
    if ret_20d_prior < -10 and ext < 0:
        return (False, 'ghost: already broken (ret_20d<-10 AND ext<0)', None)
    return (True, 'HIGH 80-84, gates + ghost-clean (NEEDS REVALIDATION)', 'long')
