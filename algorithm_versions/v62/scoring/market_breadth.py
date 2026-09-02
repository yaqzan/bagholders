"""
Market Breadth Indicators
=========================
Computes the following from the tracked stock universe each trading day:

  • Cumulative Advance-Decline Line
  • McClellan Oscillator & Summation Index
  • Percent of stocks above EMA50 / EMA200
  • New 52-week Highs / New 52-week Lows
  • TRIN (Arms Index)
  • 10-day EMA of A/(A+D) — Zweig Breadth Thrust signal
  • Hindenburg Omen detection

All indicators are stored in the `market_breadth` table and a composite
`breadth_score` (0–100) is returned for use as a signal in the regime engine
(market_regime.py).

Pipeline position:
    after per-stock scores → before regime composite

Signal carry-forward (decay):
    Zweig Breadth Thrust and Hindenburg Omen are rare events that carry
    meaningful forward information beyond the day they fire.  Both use
    exponential half-life decay — the same pattern as volume_amplifier — so
    their influence fades smoothly rather than cutting off sharply.

    Zweig:      +15 boost on trigger day, half-life 7 days, max carry 20 days
    Hindenburg: -18 penalty on confirmation day, half-life 10 days, max carry 35 days
"""

import logging
import bisect
import csv
from datetime import date, datetime, timedelta
from math import tanh
from pathlib import Path

from database.models.core import MarketBreadth, Stock
from database.models.technical import PriceHistory, Indicator
from strategy_config import SCORING as _SCORING

log = logging.getLogger(__name__)

# ── Decay parameters ─────────────────────────────────────────────────────────

ZWEIG_INITIAL_BOOST   = 15.0   # points added to breadth_score on trigger day
ZWEIG_HALF_LIFE       = 7.0    # calendar days before boost halves
ZWEIG_MAX_CARRY       = 20     # calendar days after which boost is negligible

HIND_INITIAL_PENALTY  = 18.0   # points subtracted on confirmation day
HIND_HALF_LIFE        = 10.0   # calendar days before penalty halves
HIND_MAX_CARRY        = 35     # calendar days carry window

# ── Hindenburg threshold — scaled to universe size ────────────────────────────

HIND_MIN_PCT          = 3.0    # minimum % of issues required for both NH and NL
HIND_MIN_ABS          = 10     # minimum absolute count regardless of %
                                # whichever is higher wins, preventing trivial triggers
                                # in small universes AND in large ones

# ── Sector ETF breadth / Market Wave ─────────────────────────────────────────

SECTOR_ETF_SYMBOLS = ['XLK', 'XLV', 'XLY', 'XLI', 'XLB', 'XLRE', 'XLF', 'XLE', 'XLP', 'XLU', 'XLC']
SECTOR_ETF_START_DATE = date(1998, 12, 22)
SECTOR_ETF_MIN_AVAILABLE = 9

SECTOR_ETF_MARKET_WAVE_PARAMS = {
    # Empirical 1999-2026 constrained wave, calibrated on pre-2020 history.
    # Inputs are sector breadth level and breadth velocity, all normalized to
    # percentage terms so the 9/10/11-sector denominator shift is comparable.
    'level50_weight': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_level50_weight', 0.40),
    'level200_weight': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_level200_weight', 0.15),
    'd1_weight': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_d1_weight', 1.00),
    'd5_weight': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_d5_weight', 0.55),
    'd15_weight': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_d15_weight', 0.35),
    'range10_weight': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_range10_weight', 0.35),
    'range30_weight': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_range30_weight', 0.35),
    'center': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_center', 0.425),
    'scale': _SCORING.SECTOR_BREADTH_WAVE_PARAMS.get('market_wave_scale', 1.024),
}

SECTOR_ETF_DUAL_WAVE_PARAMS = {
    # Dashboard echo metrics mirror the shipped sector breadth score wave.
    **_SCORING.SECTOR_BREADTH_WAVE_PARAMS,
}

# ── EMA helpers ───────────────────────────────────────────────────────────────

def _ema_next(prev_ema: float, value: float, period: int) -> float:
    k = 2.0 / (period + 1)
    return value * k + prev_ema * (1 - k)


def _ema_seed(value: float) -> float:
    return value


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _market_wave_state(score: float | None) -> str | None:
    if score is None:
        return None
    if score < 20.0:
        return 'Severe Stress'
    if score < 35.0:
        return 'Stress'
    if score < 45.0:
        return 'Weak'
    if score < 55.0:
        return 'Neutral'
    if score < 65.0:
        return 'Firm'
    if score < 80.0:
        return 'Repair'
    return 'Strong Repair'


def _load_market_wave_score_cache(path_value: str | None) -> tuple[list[date], dict[date, float]]:
    if not path_value:
        return [], {}
    path = Path(path_value)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if not path.exists():
        return [], {}
    values: dict[date, float] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            for raw in csv.DictReader(fh):
                score = raw.get("market_wave_score") or raw.get("score")
                if not score:
                    continue
                values[date.fromisoformat(str(raw["date"])[:10])] = float(score)
    except (OSError, ValueError, KeyError):
        return [], {}
    return sorted(values), values


def _market_wave_score_on_or_before(dates: list[date], values: dict[date, float], d: date) -> float | None:
    idx = bisect.bisect_right(dates, d) - 1
    if idx < 0:
        return None
    return values.get(dates[idx])


def _annotate_sector_etf_breadth_rows(rows: list[dict]) -> None:
    """Add Market Wave plus legacy crash echo / bull repair metrics to rows."""
    p = SECTOR_ETF_DUAL_WAVE_PARAMS
    wave = SECTOR_ETF_MARKET_WAVE_PARAMS
    cache_dates, cache_values = _load_market_wave_score_cache(p.get('path'))
    brds = [r['pct_above_ema50'] for r in rows]
    for i, row in enumerate(rows):
        b = row['pct_above_ema50']
        win5 = [x for x in brds[max(0, i - 4): i + 1] if x is not None]
        win10 = [x for x in brds[max(0, i - 9): i + 1] if x is not None]
        win20 = [x for x in brds[max(0, i - 19): i + 1] if x is not None]
        win30 = [x for x in brds[max(0, i - 29): i + 1] if x is not None]
        row['breadth_1d_change'] = b - brds[i - 1] if i >= 1 and b is not None and brds[i - 1] is not None else 0.0
        row['breadth_5d_change'] = b - brds[i - 5] if i >= 5 and b is not None and brds[i - 5] is not None else 0.0
        row['breadth_10d_change'] = b - brds[i - 10] if i >= 10 and b is not None and brds[i - 10] is not None else 0.0
        row['breadth_15d_change'] = b - brds[i - 15] if i >= 15 and b is not None and brds[i - 15] is not None else 0.0
        row['breadth_avg5'] = sum(win5) / len(win5) if win5 else None
        if win10:
            row['breadth_10d_position'] = (b - min(win10)) - (max(win10) - b)
        else:
            row['breadth_10d_position'] = 0.0
        if win30:
            row['breadth_30d_position'] = (b - min(win30)) - (max(win30) - b)
        else:
            row['breadth_30d_position'] = 0.0
        row['min20'] = min(win20) if win20 else b

        brd200 = row.get('pct_above_ema200')
        level50 = ((b if b is not None else 50.0) - 50.0) / 50.0
        level200 = ((brd200 if brd200 is not None else 50.0) - 50.0) / 50.0
        raw_wave = (
            wave['level50_weight'] * level50
            + wave['level200_weight'] * level200
            + wave['d1_weight'] * (row['breadth_1d_change'] / 100.0)
            + wave['d5_weight'] * (row['breadth_5d_change'] / 100.0)
            + wave['d15_weight'] * (row['breadth_15d_change'] / 100.0)
            + wave['range10_weight'] * (row['breadth_10d_position'] / 100.0)
            + wave['range30_weight'] * (row['breadth_30d_position'] / 100.0)
        )
        cached_score = _market_wave_score_on_or_before(cache_dates, cache_values, row['date'])
        score = (
            cached_score
            if cached_score is not None
            else max(0.0, min(100.0, 50.0 + 50.0 * tanh((raw_wave - wave['center']) / wave['scale'])))
        )
        row['market_wave_score'] = score
        row['market_wave_signed'] = 2.0 * (score - 50.0)
        row['market_wave_state'] = _market_wave_state(score)

    if p.get('mode') == 'direct_market_wave':
        for row in rows:
            score = float(row['market_wave_score'] if row['market_wave_score'] is not None else 50.0)
            stress = _clamp01(
                (p['stress_start'] - score)
                / max(1.0, p['stress_start'] - p['stress_full'])
            )
            repair = _clamp01(
                (score - p['repair_start'])
                / max(1.0, p['repair_full'] - p['repair_start'])
            )
            row['crash_echo'] = stress
            row['bull_wave'] = repair
            row['effective_crash_echo'] = stress
        return

    dual_source = p.get('source', 'sector_breadth')
    dual_values = [
        row['market_wave_score'] if dual_source == 'market_wave' else row['pct_above_ema50']
        for row in rows
    ]
    for i, row in enumerate(rows):
        b = dual_values[i]
        win5 = [x for x in dual_values[max(0, i - 4): i + 1] if x is not None]
        win20 = [x for x in dual_values[max(0, i - 19): i + 1] if x is not None]
        row['_dual_d5'] = b - dual_values[i - 5] if i >= 5 and b is not None and dual_values[i - 5] is not None else 0.0
        row['_dual_d10'] = b - dual_values[i - 10] if i >= 10 and b is not None and dual_values[i - 10] is not None else 0.0
        row['_dual_avg5'] = sum(win5) / len(win5) if win5 else 50.0
        row['_dual_min20'] = min(win20) if win20 else (b if b is not None else 50.0)

    crash_echo = 0.0
    bull_wave = 0.0
    for i, row in enumerate(rows):
        brd = float(dual_values[i] if dual_values[i] is not None else 50.0)
        rsi = float(row['avg_rsi'] if row['avg_rsi'] is not None else brd)
        d5 = float(row.get('_dual_d5') or 0.0)
        d10 = float(row.get('_dual_d10') or 0.0)
        avg5 = float(row.get('_dual_avg5') or 0.0)
        min20 = float(row.get('_dual_min20') if row.get('_dual_min20') is not None else brd)

        low = _clamp01((p['seed_level'] - brd) / max(1.0, p['seed_level']))
        velocity = _clamp01((max(-d5, -d10 * 0.75) - p['seed_velocity']) / 40.0)
        oversold = _clamp01((p['seed_rsi'] - rsi) / max(1.0, p['seed_rsi'] - 15.0))
        crash_seed = max(low, velocity, oversold)

        brd_relief = _clamp01((brd - p['relief_brd_start']) / max(1.0, p['relief_brd_full'] - p['relief_brd_start']))
        avg_relief = _clamp01((avg5 - p['relief_avg5_start']) / max(1.0, p['relief_avg5_full'] - p['relief_avg5_start']))
        relief = min(brd_relief, avg_relief)
        crash_echo = _clamp01(max(crash_seed, crash_echo * p['crash_decay'] * (1.0 - p['repair_k'] * relief)))

        bull_thrust = _clamp01((max(d5, d10 * 0.75) - p['bull_velocity']) / 45.0)
        bull_level = _clamp01((max(brd, avg5) - p['bull_level']) / max(1.0, 100.0 - p['bull_level']))
        bull_wash = _clamp01((p['bull_wash_level'] - min20) / max(1.0, p['bull_wash_level']))
        bull_seed = max(bull_level * 0.65, bull_thrust * max(0.35, bull_wash))
        bull_wave = _clamp01(max(bull_seed, bull_wave * p['bull_decay']))

        row['crash_echo'] = crash_echo
        row['bull_wave'] = bull_wave
        row['effective_crash_echo'] = _clamp01(
            crash_echo * (1.0 - p['bull_release_k'] * (bull_wave ** p['bull_power']))
        )


def _load_sector_etf_breadth_rows(end_date: date | None = None) -> list[dict]:
    """Build sector ETF breadth history from persisted prices/indicators."""
    from database.trader_database import DB

    if end_date is None:
        end_date = date.today()

    placeholders = ', '.join(['%s'] * len(SECTOR_ETF_SYMBOLS))
    cur = DB.execute_sql(
        f"""
        SELECT i.symbol, i.date, ph.close, i.ema_50, i.ema_200, i.rsi
        FROM indicators i
        JOIN price_history ph ON ph.symbol = i.symbol AND ph.date = i.date
        WHERE i.symbol IN ({placeholders})
          AND i.date >= %s
          AND i.date <= %s
        ORDER BY i.date ASC, i.symbol ASC
        """,
        [*SECTOR_ETF_SYMBOLS, SECTOR_ETF_START_DATE, end_date],
    )

    by_date = {}
    for sym, row_date, close, ema50, ema200, rsi in cur.fetchall():
        if row_date is None:
            continue
        d = row_date if hasattr(row_date, 'isoformat') else datetime.strptime(str(row_date), '%Y-%m-%d').date()
        bucket = by_date.setdefault(
            d,
            {
                'date': d,
                'ema50_known': 0,
                'ema50_above': 0,
                'ema200_known': 0,
                'ema200_above': 0,
                'rsi_sum': 0.0,
                'rsi_n': 0,
            },
        )
        try:
            close_f = float(close) if close is not None else None
            ema50_f = float(ema50) if ema50 is not None else None
            ema200_f = float(ema200) if ema200 is not None else None
            rsi_f = float(rsi) if rsi is not None else None
        except (TypeError, ValueError):
            continue

        if close_f is not None and ema50_f not in (None, 0):
            bucket['ema50_known'] += 1
            if close_f > ema50_f:
                bucket['ema50_above'] += 1
        if close_f is not None and ema200_f not in (None, 0):
            bucket['ema200_known'] += 1
            if close_f > ema200_f:
                bucket['ema200_above'] += 1
        if rsi_f is not None:
            bucket['rsi_sum'] += rsi_f
            bucket['rsi_n'] += 1

    rows = []
    for bucket in by_date.values():
        if bucket['ema50_known'] < SECTOR_ETF_MIN_AVAILABLE:
            continue
        rows.append({
            'date': bucket['date'],
            'pct_above_ema50': bucket['ema50_above'] / bucket['ema50_known'] * 100.0,
            'pct_above_ema200': (
                bucket['ema200_above'] / bucket['ema200_known'] * 100.0
                if bucket['ema200_known']
                else None
            ),
            'avg_rsi': bucket['rsi_sum'] / bucket['rsi_n'] if bucket['rsi_n'] else None,
            'source': 'live_db',
            'issues': bucket['ema50_known'],
        })

    rows.sort(key=lambda r: r['date'])
    _annotate_sector_etf_breadth_rows(rows)
    return rows


def _round_sector_etf_breadth(row: dict) -> dict:
    def r(value, dp=2):
        return round(float(value), dp) if value is not None else None

    return {
        'date': row['date'],
        'pct_above_ema50': r(row.get('pct_above_ema50')),
        'pct_above_ema200': r(row.get('pct_above_ema200')),
        'avg_rsi': r(row.get('avg_rsi')),
        'breadth_1d_change': r(row.get('breadth_1d_change')),
        'breadth_5d_change': r(row.get('breadth_5d_change')),
        'breadth_10d_change': r(row.get('breadth_10d_change')),
        'breadth_15d_change': r(row.get('breadth_15d_change')),
        'breadth_avg5': r(row.get('breadth_avg5')),
        'breadth_10d_position': r(row.get('breadth_10d_position')),
        'breadth_30d_position': r(row.get('breadth_30d_position')),
        'market_wave_score': r(row.get('market_wave_score')),
        'market_wave_signed': r(row.get('market_wave_signed')),
        'market_wave_state': row.get('market_wave_state'),
        'crash_echo': r(row.get('crash_echo'), 4),
        'bull_wave': r(row.get('bull_wave'), 4),
        'effective_crash_echo': r(row.get('effective_crash_echo'), 4),
        'source': row.get('source'),
        'issues': row.get('issues'),
    }


def compute_sector_etf_breadth(target_date: date | None = None, rows: list[dict] | None = None) -> dict | None:
    """Return latest sector ETF breadth row on or before target_date."""
    if target_date is None:
        target_date = date.today()
    if rows is None:
        rows = _load_sector_etf_breadth_rows(end_date=target_date)
    for row in reversed(rows):
        if row['date'] <= target_date:
            return _round_sector_etf_breadth(row)
    return None


# ── Raw breadth data from PriceHistory ────────────────────────────────────────

def _get_daily_breadth(target_date: date):
    """
    For target_date, compare each stock's close to the prior trading day close.
    Returns dict with advancing, declining, unchanged, advancing_volume,
    declining_volume, new_highs_52w, new_lows_52w, pct_above_ema50, pct_above_ema200.

    ETF EXCLUSION (shipped 2026-05-08): breadth universe restricted to Stock
    entries with non-null `sector`. ETFs in the DB have NULL sector (~43
    entries: sector SPDRs, broad indices, leveraged 3x like TQQQ/SOXL/LABD,
    international, commodity, bond ETFs). Including them was distorting
    advancing/declining counts and TRIN volume, especially via the 3x leveraged
    products which moved 3× the underlying in either direction.

    Filter pre-fetches the sectored-symbols set once; downstream queries
    naturally inherit the filter via `symbols_list`.
    """
    sectored_syms = {
        row.symbol for row in
        Stock.select(Stock.symbol).where(
            Stock.sector.is_null(False)
            & (Stock.delisted_date.is_null() | (Stock.delisted_date >= target_date))
        )
    }

    today_rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.close, PriceHistory.volume,
        )
        .where(
            (PriceHistory.date == target_date) &
            (PriceHistory.symbol.in_(sectored_syms))
        )
    )
    if not today_rows:
        return None

    symbols_today = {r.symbol_id: r for r in today_rows}
    symbols_list = list(symbols_today.keys())

    # Most recent prior close per symbol (look back up to 10 calendar days)
    lookback = target_date - timedelta(days=10)
    prior_rows = list(
        PriceHistory.select(PriceHistory.symbol, PriceHistory.close)
        .where(
            PriceHistory.symbol.in_(symbols_list),
            PriceHistory.date >= lookback,
            PriceHistory.date < target_date,
        )
        .order_by(PriceHistory.symbol_id, PriceHistory.date.desc())
    )
    prior_close = {}
    for row in prior_rows:
        sym = row.symbol_id
        if sym not in prior_close:
            prior_close[sym] = float(row.close)

    # 52-week high/low — aggregate in SQL to avoid loading 118k rows per date
    from peewee import fn as _fn
    start_52w = target_date - timedelta(days=365)
    agg_rows = list(
        PriceHistory.select(
            PriceHistory.symbol,
            _fn.MAX(PriceHistory.high).alias('max_high'),
            _fn.MIN(PriceHistory.low).alias('min_low'),
        )
        .where(
            PriceHistory.symbol.in_(symbols_list),
            PriceHistory.date >= start_52w,
            PriceHistory.date <= target_date,
        )
        .group_by(PriceHistory.symbol)
        .tuples()
    )
    highs_52w = {sym_id: float(max_h) for sym_id, max_h, _ in agg_rows}
    lows_52w  = {sym_id: float(min_l) for sym_id, _, min_l in agg_rows}

    # % above EMA50 / EMA200 from Indicator table
    ind_rows = list(
        Indicator.select(Indicator.symbol, Indicator.ema_50, Indicator.ema_200)
        .where(
            Indicator.symbol.in_(symbols_list),
            Indicator.date == target_date,
            Indicator.ema_50.is_null(False),
        )
    )
    ind_map = {r.symbol_id: r for r in ind_rows}

    advancing = declining = unchanged = 0
    adv_vol = dec_vol = 0.0
    new_highs = new_lows = 0
    above_ema50 = above_ema200 = with_ema = 0

    for sym, today in symbols_today.items():
        close = float(today.close)
        vol = float(today.volume) if today.volume else 0.0
        pc = prior_close.get(sym)

        if pc is None:
            unchanged += 1
        elif close > pc:
            advancing += 1
            adv_vol += vol
        elif close < pc:
            declining += 1
            dec_vol += vol
        else:
            unchanged += 1

        # 52-week proximity: within 3% of annual extreme counts
        if sym in highs_52w and highs_52w[sym] > 0:
            if close >= highs_52w[sym] * 0.97:
                new_highs += 1
        if sym in lows_52w and lows_52w[sym] > 0:
            if close <= lows_52w[sym] * 1.03:
                new_lows += 1

        ind = ind_map.get(sym)
        if ind:
            with_ema += 1
            if ind.ema_50 and close > float(ind.ema_50):
                above_ema50 += 1
            if ind.ema_200 and close > float(ind.ema_200):
                above_ema200 += 1

    total = advancing + declining + unchanged
    pct_ema50 = round(above_ema50 / with_ema * 100, 2) if with_ema else None
    pct_ema200 = round(above_ema200 / with_ema * 100, 2) if with_ema else None

    return {
        'advancing': advancing,
        'declining': declining,
        'unchanged': unchanged,
        'total_issues': total,
        'advancing_volume': adv_vol,
        'declining_volume': dec_vol,
        'new_highs_52w': new_highs,
        'new_lows_52w': new_lows,
        'pct_above_ema50': pct_ema50,
        'pct_above_ema200': pct_ema200,
    }


# ── Hindenburg Omen detection ─────────────────────────────────────────────────

def _check_hindenburg(target_date: date, new_highs: int, new_lows: int,
                       total: int, mcclellan_osc: float,
                       pct_above_ema50: float) -> tuple[bool, bool]:
    """
    Hindenburg Omen criteria (adapted to our universe):
      1. Both new 52-week highs AND new 52-week lows exceed the threshold
         (max of HIND_MIN_PCT% of total issues, HIND_MIN_ABS absolute count)
      2. McClellan Oscillator is negative
      3. Market is in an uptrend (>50% stocks above EMA50)
      4. New highs do NOT exceed 2× new lows

    With 470 stocks: threshold = max(3.0% × 470, 10) = max(14.1, 10) = ~14 stocks
    on each side required.  Prevents trivial daily spread from firing it.

    Returns (triggered_today, confirmed) where confirmed = 2+ omens in 30 days.
    """
    triggered = False
    if total and total > 0:
        threshold_count = max(HIND_MIN_ABS, total * HIND_MIN_PCT / 100)
        if (
            new_highs >= threshold_count
            and new_lows >= threshold_count
            and (mcclellan_osc is not None and mcclellan_osc < 0)
            and (pct_above_ema50 is not None and pct_above_ema50 > 50)
            and new_highs <= 2 * new_lows
        ):
            triggered = True

    confirmed = False
    if triggered:
        thirty_days_ago = target_date - timedelta(days=30)
        prior_omens = MarketBreadth.select().where(
            MarketBreadth.date >= thirty_days_ago,
            MarketBreadth.date < target_date,
            MarketBreadth.hindenburg_omen == True,
        ).count()
        confirmed = prior_omens >= 1

    return triggered, confirmed


# ── Zweig Breadth Thrust detection (trigger-day only) ────────────────────────

def _check_zweig_thrust_trigger(target_date: date, current_ema10: float) -> bool:
    """
    Returns True only on the day the Zweig Breadth Thrust actually fires:
    EMA10 of A/(A+D) is now above 61.5% AND was below 40% within the prior
    10 trading days (≈14 calendar days).

    Carry-forward decay is handled in compute_and_store_breadth, not here.
    """
    if current_ema10 is None or current_ema10 < 0.615:
        return False

    lookback = target_date - timedelta(days=14)
    old_rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.ema10_ad_ratio)
        .where(
            MarketBreadth.date >= lookback,
            MarketBreadth.date < target_date,
            MarketBreadth.ema10_ad_ratio.is_null(False),
        )
        .order_by(MarketBreadth.date.asc())
    )
    for row in old_rows:
        if row.ema10_ad_ratio is not None and float(row.ema10_ad_ratio) < 0.40:
            return True

    return False


# ── Decay carry-forward helpers ───────────────────────────────────────────────

def _compute_zweig_decay(target_date: date, triggered_today: bool) -> tuple[float, date | None]:
    """
    Returns (zweig_boost, trigger_date_or_None).
    Boost decays from ZWEIG_INITIAL_BOOST with ZWEIG_HALF_LIFE.
    """
    trigger_date = target_date if triggered_today else None

    if not triggered_today:
        # Look for the most recent trigger within the carry window
        prior = (
            MarketBreadth.select(MarketBreadth.zweig_thrust_date)
            .where(
                MarketBreadth.zweig_thrust_date.is_null(False),
                MarketBreadth.date >= target_date - timedelta(days=ZWEIG_MAX_CARRY),
                MarketBreadth.date < target_date,
            )
            .order_by(MarketBreadth.date.desc())
            .first()
        )
        if prior and prior.zweig_thrust_date:
            trigger_date = prior.zweig_thrust_date

    if trigger_date is None:
        return 0.0, None

    days_elapsed = (target_date - trigger_date).days
    if days_elapsed > ZWEIG_MAX_CARRY:
        return 0.0, None

    boost = ZWEIG_INITIAL_BOOST * (0.5 ** (days_elapsed / ZWEIG_HALF_LIFE))
    return round(boost, 2), trigger_date


def _compute_hindenburg_decay(target_date: date, confirmed_today: bool) -> float:
    """
    Returns hindenburg_penalty (positive float; caller subtracts it).
    Penalty decays from HIND_INITIAL_PENALTY with HIND_HALF_LIFE.
    """
    penalty_date = target_date if confirmed_today else None

    if not confirmed_today:
        prior = (
            MarketBreadth.select(MarketBreadth.date)
            .where(
                MarketBreadth.hindenburg_confirmed == True,
                MarketBreadth.date >= target_date - timedelta(days=HIND_MAX_CARRY),
                MarketBreadth.date < target_date,
            )
            .order_by(MarketBreadth.date.desc())
            .first()
        )
        if prior:
            penalty_date = prior.date

    if penalty_date is None:
        return 0.0

    days_elapsed = (target_date - penalty_date).days
    if days_elapsed > HIND_MAX_CARRY:
        return 0.0

    penalty = HIND_INITIAL_PENALTY * (0.5 ** (days_elapsed / HIND_HALF_LIFE))
    return round(penalty, 2)


# ── Composite breadth score (0-100) ──────────────────────────────────────────

def _compute_breadth_score(
    trin, mcclellan_osc, mcclellan_sum, ad_diff, ad_line,
    new_highs, new_lows, total, pct_above_ema50, pct_above_ema200,
    zweig_boost: float, hind_penalty: float, prev_mcclellan_sum_10d,
):
    """
    Weighted composite of continuous breadth readings (0-100), then additive
    decay adjustments for event-driven signals.

    Weights reflect signal quality:
      - McClellan Oscillator (28%): best leading indicator of breadth direction
      - McClellan Summation trend (20%): confirms sustained breadth shifts
      - New 52w Highs vs Lows (20%): participation quality, not just direction
      - TRIN (15%): volume flow — who's getting the money
      - % above EMA50 (12%): trend backdrop, lagging but stable
      - % above EMA200 (5%): very slow-moving, de-emphasised

    Event adjustments applied after weighting (additive, not multiplicative):
      - zweig_boost:  +0 to +15 decaying from trigger date
      - hind_penalty: -0 to -18 decaying from confirmation date
    """
    components = {}

    # TRIN (Arms Index) — 15%
    # <1.0 = bullish volume flow, >1.0 = bearish; extreme readings very informative
    if trin is not None:
        if trin < 0.5:
            components['trin'] = 90
        elif trin < 0.8:
            components['trin'] = 75
        elif trin < 1.2:
            components['trin'] = 50
        elif trin < 2.0:
            components['trin'] = 30
        else:
            components['trin'] = 10

    # McClellan Oscillator — 28%
    # Short-term momentum of breadth; most responsive leading indicator
    if mcclellan_osc is not None:
        if mcclellan_osc > 100:
            components['mcclellan'] = 90
        elif mcclellan_osc > 50:
            components['mcclellan'] = 75
        elif mcclellan_osc > 0:
            components['mcclellan'] = 60
        elif mcclellan_osc > -50:
            components['mcclellan'] = 40
        elif mcclellan_osc > -100:
            components['mcclellan'] = 25
        else:
            components['mcclellan'] = 10

    # McClellan Summation 10-day trend — 20%
    # Confirms whether breadth momentum is building or fading
    if mcclellan_sum is not None and prev_mcclellan_sum_10d is not None:
        sum_change = float(mcclellan_sum) - float(prev_mcclellan_sum_10d)
        if sum_change > 200:
            components['mcclellan_sum'] = 80
        elif sum_change > 50:
            components['mcclellan_sum'] = 65
        elif sum_change > -50:
            components['mcclellan_sum'] = 50
        elif sum_change > -200:
            components['mcclellan_sum'] = 35
        else:
            components['mcclellan_sum'] = 20

    # New 52-week Highs vs Lows — 20%
    # Measures participation quality: are the movers making real highs or new lows?
    if total and total > 0:
        nh_nl_ratio = new_highs / (new_highs + new_lows + 1)
        if nh_nl_ratio > 0.75:
            components['new_hl'] = 80
        elif nh_nl_ratio > 0.55:
            components['new_hl'] = 65
        elif nh_nl_ratio > 0.45:
            components['new_hl'] = 50
        elif nh_nl_ratio > 0.25:
            components['new_hl'] = 35
        else:
            components['new_hl'] = 20

    # % above EMA50 — 12%
    # Trend backdrop: useful but slow-moving and partially redundant with EMA200
    if pct_above_ema50 is not None:
        p = float(pct_above_ema50)
        if p > 80:
            components['pct_ema50'] = 85
        elif p > 60:
            components['pct_ema50'] = 70
        elif p > 40:
            components['pct_ema50'] = 50
        elif p > 20:
            components['pct_ema50'] = 35
        else:
            components['pct_ema50'] = 15

    # % above EMA200 — 5%
    # Very slow-moving structural signal; minimal weight to avoid double-counting
    if pct_above_ema200 is not None:
        p = float(pct_above_ema200)
        if p > 70:
            components['pct_ema200'] = 85
        elif p > 50:
            components['pct_ema200'] = 65
        elif p > 30:
            components['pct_ema200'] = 50
        elif p > 10:
            components['pct_ema200'] = 35
        else:
            components['pct_ema200'] = 15

    if not components:
        return 50.0

    weights = {
        'trin':          0.15,
        'mcclellan':     0.28,
        'mcclellan_sum': 0.20,
        'new_hl':        0.20,
        'pct_ema50':     0.12,
        'pct_ema200':    0.05,
    }

    total_w = sum(weights[k] for k in components)
    if total_w == 0:
        return 50.0
    score = sum(components[k] * weights[k] for k in components) / total_w

    # Event-driven carry-forward adjustments (additive, decayed)
    score += zweig_boost
    score -= hind_penalty

    return round(max(0.0, min(100.0, score)), 2)


# ── Main entry point ──────────────────────────────────────────────────────────

def compute_and_store_breadth(target_date: date = None, sector_etf_rows: list[dict] | None = None) -> 'MarketBreadth | None':
    """
    Compute all breadth indicators for target_date and persist to market_breadth.
    Returns the MarketBreadth row, or None if insufficient data.
    """
    if target_date is None:
        target_date = date.today()

    MarketBreadth.ensure_schema()

    raw = _get_daily_breadth(target_date)
    if raw is None:
        log.warning("Breadth: no price data for %s", target_date)
        return None

    advancing    = raw['advancing']
    declining    = raw['declining']
    unchanged    = raw['unchanged']
    total        = raw['total_issues']
    adv_vol      = raw['advancing_volume']
    dec_vol      = raw['declining_volume']
    new_highs    = raw['new_highs_52w']
    new_lows     = raw['new_lows_52w']
    pct_ema50    = raw['pct_above_ema50']
    pct_ema200   = raw['pct_above_ema200']

    # TRIN (Arms Index)
    trin = None
    if advancing > 0 and declining > 0 and adv_vol > 0 and dec_vol > 0:
        trin = round((advancing / declining) / (adv_vol / dec_vol), 4)

    # A-D diff and ratio
    ad_diff  = advancing - declining
    ad_ratio = round(advancing / (advancing + declining), 4) if (advancing + declining) > 0 else 0.5

    # Pull prior breadth row for EMA chaining
    prev = (
        MarketBreadth.select()
        .where(MarketBreadth.date < target_date)
        .order_by(MarketBreadth.date.desc())
        .first()
    )

    # Cumulative A-D Line
    prior_ad_line = float(prev.ad_line) if prev and prev.ad_line is not None else 0.0
    ad_line = prior_ad_line + ad_diff

    # EMA19 and EMA39 of A-D diff (McClellan inputs)
    if prev and prev.ema19_ad_diff is not None:
        ema19 = _ema_next(float(prev.ema19_ad_diff), ad_diff, 19)
    else:
        ema19 = _ema_seed(float(ad_diff))

    if prev and prev.ema39_ad_diff is not None:
        ema39 = _ema_next(float(prev.ema39_ad_diff), ad_diff, 39)
    else:
        ema39 = _ema_seed(float(ad_diff))

    mcclellan_osc = round(ema19 - ema39, 4)

    # McClellan Summation Index
    prior_sum     = float(prev.mcclellan_summation) if prev and prev.mcclellan_summation is not None else 0.0
    mcclellan_sum = prior_sum + mcclellan_osc

    # EMA10 of A/(A+D) — Zweig input
    if prev and prev.ema10_ad_ratio is not None:
        ema10 = _ema_next(float(prev.ema10_ad_ratio), ad_ratio, 10)
    else:
        ema10 = _ema_seed(ad_ratio)
    ema10 = round(ema10, 4)

    # Hindenburg Omen — detection then decay
    hind_triggered, hind_confirmed = _check_hindenburg(
        target_date, new_highs, new_lows, total, mcclellan_osc, pct_ema50
    )
    hind_penalty = _compute_hindenburg_decay(target_date, hind_confirmed)

    # Zweig Breadth Thrust — detection then decay
    zweig_fired_today = _check_zweig_thrust_trigger(target_date, ema10)
    zweig_boost, zweig_date = _compute_zweig_decay(target_date, zweig_fired_today)
    zweig_active = zweig_boost > 0.1   # active while boost is still material

    # Prior 10-day summation for McClellan Summation trend calculation
    ten_days_ago = target_date - timedelta(days=14)
    old_sum_row = (
        MarketBreadth.select()
        .where(
            MarketBreadth.date >= ten_days_ago,
            MarketBreadth.date < target_date,
            MarketBreadth.mcclellan_summation.is_null(False),
        )
        .order_by(MarketBreadth.date.asc())
        .first()
    )
    prev_mcclellan_sum_10d = float(old_sum_row.mcclellan_summation) if old_sum_row else None

    breadth_score = _compute_breadth_score(
        trin, mcclellan_osc, mcclellan_sum, ad_diff, ad_line,
        new_highs, new_lows, total, pct_ema50, pct_ema200,
        zweig_boost, hind_penalty, prev_mcclellan_sum_10d,
    )
    sector_etf = compute_sector_etf_breadth(target_date, rows=sector_etf_rows)

    row, _ = MarketBreadth.get_or_create(date=target_date)
    row.advancing          = advancing
    row.declining          = declining
    row.unchanged          = unchanged
    row.total_issues       = total
    row.advancing_volume   = int(adv_vol)
    row.declining_volume   = int(dec_vol)
    row.trin               = trin
    row.new_highs_52w      = new_highs
    row.new_lows_52w       = new_lows
    row.ad_diff            = ad_diff
    row.ad_line            = round(ad_line, 2)
    row.ema19_ad_diff      = round(ema19, 4)
    row.ema39_ad_diff      = round(ema39, 4)
    row.mcclellan_oscillator = mcclellan_osc
    row.mcclellan_summation  = round(mcclellan_sum, 4)
    row.ad_ratio           = ad_ratio
    row.ema10_ad_ratio     = ema10
    row.zweig_thrust_active = zweig_active
    row.zweig_thrust_date  = zweig_date
    row.zweig_boost        = zweig_boost
    row.pct_above_ema50    = pct_ema50
    row.pct_above_ema200   = pct_ema200
    row.hindenburg_omen    = hind_triggered
    row.hindenburg_confirmed = hind_confirmed
    row.hindenburg_penalty = hind_penalty
    row.breadth_score      = breadth_score
    if sector_etf:
        row.sector_etf_asof_date = sector_etf['date']
        row.sector_etf_pct_above_ema50 = sector_etf['pct_above_ema50']
        row.sector_etf_pct_above_ema200 = sector_etf['pct_above_ema200']
        row.sector_etf_avg_rsi = sector_etf['avg_rsi']
        row.sector_etf_breadth_1d_change = sector_etf['breadth_1d_change']
        row.sector_etf_breadth_5d_change = sector_etf['breadth_5d_change']
        row.sector_etf_breadth_10d_change = sector_etf['breadth_10d_change']
        row.sector_etf_breadth_15d_change = sector_etf['breadth_15d_change']
        row.sector_etf_breadth_avg5 = sector_etf['breadth_avg5']
        row.sector_etf_breadth_10d_position = sector_etf['breadth_10d_position']
        row.sector_etf_breadth_30d_position = sector_etf['breadth_30d_position']
        row.sector_etf_market_wave_score = sector_etf['market_wave_score']
        row.sector_etf_market_wave_signed = sector_etf['market_wave_signed']
        row.sector_etf_market_wave_state = sector_etf['market_wave_state']
        row.sector_etf_crash_echo = sector_etf['crash_echo']
        row.sector_etf_bull_wave = sector_etf['bull_wave']
        row.sector_etf_effective_crash_echo = sector_etf['effective_crash_echo']
        row.sector_etf_source = sector_etf['source']
        row.sector_etf_issues = sector_etf['issues']
    else:
        row.sector_etf_asof_date = None
        row.sector_etf_pct_above_ema50 = None
        row.sector_etf_pct_above_ema200 = None
        row.sector_etf_avg_rsi = None
        row.sector_etf_breadth_1d_change = None
        row.sector_etf_breadth_5d_change = None
        row.sector_etf_breadth_10d_change = None
        row.sector_etf_breadth_15d_change = None
        row.sector_etf_breadth_avg5 = None
        row.sector_etf_breadth_10d_position = None
        row.sector_etf_breadth_30d_position = None
        row.sector_etf_market_wave_score = None
        row.sector_etf_market_wave_signed = None
        row.sector_etf_market_wave_state = None
        row.sector_etf_crash_echo = None
        row.sector_etf_bull_wave = None
        row.sector_etf_effective_crash_echo = None
        row.sector_etf_source = None
        row.sector_etf_issues = None
    row.updated_at         = datetime.now()
    row.save()

    log.info(
        "Breadth[%s]: A=%d D=%d TRIN=%.2f McClellan=%.1f "
        "Zweig=%.1fpts Hind=%.1fpts score=%.1f",
        target_date, advancing, declining,
        trin or 0, mcclellan_osc, zweig_boost, hind_penalty, breadth_score,
    )
    return row


# ── Backfill ──────────────────────────────────────────────────────────────────

def backfill_breadth(days: int = 365):
    """Backfill MarketBreadth for all dates with price data in the lookback window.
    Processes oldest-to-newest so EMA chains build correctly.
    """
    from colorama import Fore, Style
    from tqdm import tqdm

    MarketBreadth.ensure_schema()

    cutoff = date.today() - timedelta(days=days)
    dated = list(
        PriceHistory.select(PriceHistory.date)
        .where(PriceHistory.date >= cutoff)
        .group_by(PriceHistory.date)
        .order_by(PriceHistory.date.asc())   # oldest first — required for EMA chaining
        .tuples()
    )
    dates = [row[0] for row in dated]
    if not dates:
        print(f"{Fore.YELLOW}No price data found.{Style.RESET_ALL}")
        return

    print(f"{Fore.CYAN}Backfilling breadth for {len(dates)} dates (oldest->newest)...{Style.RESET_ALL}")
    sector_etf_rows = _load_sector_etf_breadth_rows(end_date=max(dates))

    ok = 0
    for d in tqdm(dates, desc="Breadth backfill"):
        row = compute_and_store_breadth(d, sector_etf_rows=sector_etf_rows)
        if row:
            ok += 1
    print(f"{Fore.GREEN}Breadth backfill complete: {ok}/{len(dates)} days filled.{Style.RESET_ALL}")
