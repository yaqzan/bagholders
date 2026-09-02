"""Point-in-time partial-week weekly reconstruction for the weekly-transition blend.

Production port of `experiments/weekly_pit/build_pit_weekly.py` (validated against
live `score_intraday_logs` at mean|Δ|=0.28 on w_comp, 97.5% within ±2).

Reconstructs the *current partial week* weekly component scores (trend/rsi/macd)
that the v60 scorer SHOULD see at each (symbol, signal_date), using ONLY daily
PriceHistory bars up to and INCLUDING signal_date (NO future bars).  This is the
"state 2" weekly context in the three-state diagnosis:

  state 1 (last-completed week, date-(weekday()+7))  — honest but ~1wk stale; the
          convention the LIVE single-row scorer uses (`Score.weekly_score`).
  state 2 (current PARTIAL week, Mon->signal_date)   — honest AND fresh (this module).
  state 3 (current COMPLETE week, full Mon-Fri row)  — LOOK-AHEAD on a mid-week signal;
          what the batch/recalc paths historically used.

The weekly-transition blend (`compute_overall_score`) combines a state-1
completed-week adjustment with a state-2 partial-week adjustment by fraction of
trading bars elapsed this week, removing the look-ahead while smoothing the
Monday weekly cliff.

Faithfulness strategy (no re-implementation of score internals): rebuild the
weekly OHLCV series with the current week truncated to signal_date, run the
IDENTICAL talib calls production uses (RSI 14, MACD 12/26/9, EMA 21/50/200), wrap
the result in indicator-/price-like rows, and call the REAL production score
functions (`Stock.calculate_{trend,rsi,macd}_score(..., weekly=True)`) through
their `_ind_cache` / `_ph_cache` injection ports.

Weekly OHLCV aggregation matches technical.py `_refresh_weekly_aggregates`:
  week_start = date - timedelta(days=date.weekday())   # Monday
  open=first daily open, high=max, low=min, close=last daily close, volume=sum.
"""
import math
from datetime import timedelta

import numpy as np
import talib

# Tail length (in WEEKLY bars) kept for the talib recursion.  EMA200 needs 200
# bars to fully converge; 320 (~6y) gives clean warmup.  Matches the validated
# experiment constant (residual ~0.28 on w_comp is the EMA200 warmup-truncation
# noise, not a logic bug).
MAX_WEEKS_TAIL = 320

# Minimum weekly bars before a partial-week reconstruction is attempted.  Below
# this the talib RSI/MACD/EMA can't produce a stable current value, so the
# blend gracefully falls back to the completed-week adjustment.
MIN_WEEKS = 30


# ---------------------------------------------------------------------------
# Lightweight stand-ins for WeeklyIndicator / WeeklyPriceHistory rows so we can
# feed the REAL production score functions via _ind_cache / _ph_cache.
# ---------------------------------------------------------------------------
class _IndRow:
    __slots__ = ("date", "rsi", "macd", "macd_signal", "macd_hist",
                 "ema_21", "ema_50", "ema_200", "_ph")

    def __init__(self, d):
        self.date = d
        self.rsi = self.macd = self.macd_signal = self.macd_hist = None
        self.ema_21 = self.ema_50 = self.ema_200 = None
        self._ph = None

    def price_history(self):
        return self._ph


class _PhRow:
    __slots__ = ("date", "open", "high", "low", "close", "volume")

    def __init__(self, d, o, h, l, c, v):
        self.date = d
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


def week_start(d):
    """Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def trading_bars_elapsed(week_start_date, signal_date, daily_dates_in_week):
    """Count actual trading bars from this week's Monday through signal_date
    inclusive.  Holiday/short-week safe — counts real bars, not calendar days."""
    return sum(1 for dd in daily_dates_in_week
               if week_start_date <= dd <= signal_date)


def transition_t(bars_elapsed):
    """t = trading-bars-elapsed-this-week / 5, clamped [0,1].
    Mon (1 bar) -> 0.2, Fri (5 bars) -> 1.0.  Short/holiday weeks reach 1.0
    early, which is correct: fewer bars means the partial week is 'more done'."""
    return max(0.0, min(1.0, bars_elapsed / 5.0))


def _aggregate_weekly(daily_asc, upto):
    """daily_asc: list of (date,open,high,low,close,volume) ASC, date<=upto.
    Returns weekly _PhRow list ASC.  The week containing `upto` is truncated to
    bars with date<=upto (current week is PARTIAL when upto is mid-week)."""
    by_week = {}
    order = []
    for d, o, h, l, c, v in daily_asc:
        if d > upto:
            continue
        wk = week_start(d)
        if wk not in by_week:
            by_week[wk] = []
            order.append(wk)
        by_week[wk].append((d, o, h, l, c, v))
    out = []
    for wk in order:
        days = by_week[wk]
        o = days[0][1]
        h = max(x[2] for x in days)
        l = min(x[3] for x in days)
        c = days[-1][4]
        v = sum(int(x[5]) for x in days)
        out.append(_PhRow(wk, o, h, l, c, v))
    return out


def _build_ind_cache(weekly_ph):
    """Run the SAME talib calls production uses over the weekly close series and
    attach to indicator-like rows (ASC).  Mirrors core.calculate_indicators
    (weekly=True) column gating exactly."""
    n = len(weekly_ph)
    if n < 14:
        return []
    close = np.array([float(p.close) for p in weekly_ph])

    rsi = talib.RSI(close, timeperiod=14)
    macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    ema_21 = talib.EMA(close, timeperiod=21)
    ema_50 = talib.EMA(close, timeperiod=50)
    ema_200 = talib.EMA(close, timeperiod=200)

    rows = []
    for i in range(n):
        r = _IndRow(weekly_ph[i].date)
        r._ph = weekly_ph[i]
        if i >= 13:
            r.rsi = float(rsi[i]) if not math.isnan(rsi[i]) else None
        if i >= 25:
            r.macd = float(macd[i]) if not math.isnan(macd[i]) else None
            r.macd_signal = float(macd_signal[i]) if not math.isnan(macd_signal[i]) else None
            r.macd_hist = float(macd_hist[i]) if not math.isnan(macd_hist[i]) else None
        if i >= 20:
            r.ema_21 = float(ema_21[i]) if not math.isnan(ema_21[i]) else None
        if i >= 49:
            r.ema_50 = float(ema_50[i]) if not math.isnan(ema_50[i]) else None
        if i >= 199:
            r.ema_200 = float(ema_200[i]) if not math.isnan(ema_200[i]) else None
        rows.append(r)
    return rows


def _weekly_scores_for_target(stock, target_week_start, ind_cache_asc):
    """Compute (trend, rsi, macd) weekly component scores AS OF target_week_start
    using the production score functions.  ind_cache_asc may contain only bars
    with date <= target_week_start (caller truncates)."""
    asc = [r for r in ind_cache_asc if r.date <= target_week_start]
    if not asc or asc[-1].date != target_week_start:
        return None, None, None
    desc = list(reversed(asc))
    ph_cache = {r.date: r._ph for r in asc if r._ph is not None}

    trend = stock.calculate_trend_score(
        target_week_start, weekly=True, _ind_cache=desc, _ph_cache=ph_cache)
    rsi = stock.calculate_rsi_score(
        target_week_start, trend_score=trend, weekly=True,
        _ind_cache=desc, _ph_cache=ph_cache)
    macd = stock.calculate_macd_score(
        target_week_start, weekly=True, _ind_cache=desc, _ph_cache=ph_cache)
    return trend, rsi, macd


def reconstruct_partial_week_weekly(stock, daily_asc_through_date, signal_date):
    """PRIMARY entry point.  Reconstruct the current PARTIAL-week (state 2)
    weekly component scores point-in-time (no future bars).

    stock: Stock ORM row (used only for its calculate_*_score methods).
    daily_asc_through_date: list of (date,o,h,l,c,v) ASC, all bars <= signal_date.
    signal_date: the scoring date.

    Returns (pit_trend, pit_rsi, pit_macd) as ints, or None if insufficient
    weekly history (caller falls back to completed-week only)."""
    wk = week_start(signal_date)
    weekly_ph = _aggregate_weekly(daily_asc_through_date, signal_date)
    if len(weekly_ph) < MIN_WEEKS:
        return None
    weekly_ph = weekly_ph[-MAX_WEEKS_TAIL:]
    ind_cache = _build_ind_cache(weekly_ph)
    if not ind_cache:
        return None
    if ind_cache[-1].date != wk:
        # The partial current-week bar did not materialize (no daily bars this
        # week yet, e.g. holiday Monday) — no fresh partial context to blend.
        return None
    t, r, m = _weekly_scores_for_target(stock, wk, ind_cache)
    if None in (t, r, m):
        return None
    return int(t), int(r), int(m)


# ---------------------------------------------------------------------------
# Efficient per-symbol map builder (batch / recalc / simulator paths).
# ---------------------------------------------------------------------------
def build_pit_weekly_map(stock, ph_rows_asc, target_dates=None):
    """Build {signal_date -> (pit_trend, pit_rsi, pit_macd, t)} for every target
    date, point-in-time (no future bars), efficiently.

    Optimization: the completed-week weekly OHLCV prefix is identical for every
    signal date in the same week.  We aggregate ALL completed weeks once, then
    per signal date only append the single partial current-week bar (truncated
    Mon->date) before running talib + the production score functions.

    stock: Stock ORM row.
    ph_rows_asc: list of PriceHistory rows (ORM or namedtuple) ASC by date, each
                 exposing .date/.open/.high/.low/.close/.volume.
    target_dates: iterable of dates to compute; None -> every daily bar's date.

    Returns dict.  Dates with insufficient weekly history are omitted (caller
    treats a missing entry as 'no partial context' -> completed-week-only)."""
    if not ph_rows_asc:
        return {}

    # Normalize daily bars to plain tuples once (ORM attr access is slow in loops).
    daily = []
    daily_dates = []
    for p in ph_rows_asc:
        c = p.close
        if c is None:
            continue
        d = p.date
        o = float(p.open) if p.open is not None else float(c)
        h = float(p.high) if p.high is not None else float(c)
        l = float(p.low) if p.low is not None else float(c)
        v = int(p.volume) if p.volume is not None else 0
        daily.append((d, o, h, l, float(c), v))
        daily_dates.append(d)

    if len(daily) < 5:
        return {}

    all_dates = [t[0] for t in daily]   # daily is ASC by date
    if target_dates is None:
        targets = list(all_dates)
    else:
        tset = set(target_dates)
        targets = [d for d in all_dates if d in tset]
    if not targets:
        return {}

    # Pre-aggregate the master COMPLETED weekly OHLCV series ONCE (one pass over
    # daily).  by_week keeps per-week daily tuples for the partial-bar build;
    # complete_weeks[i] is the fully-aggregated _PhRow for week_order[i].
    by_week = {}
    week_order = []
    for tup in daily:
        wk = week_start(tup[0])
        bucket = by_week.get(wk)
        if bucket is None:
            by_week[wk] = [tup]
            week_order.append(wk)
        else:
            bucket.append(tup)

    complete_weeks = []          # _PhRow per fully-complete week, ASC
    week_index = {}              # wk -> index into week_order / complete_weeks
    for i, wk in enumerate(week_order):
        days = by_week[wk]
        week_index[wk] = i
        complete_weeks.append(_PhRow(
            wk, days[0][1], max(x[2] for x in days), min(x[3] for x in days),
            days[-1][4], sum(int(x[5]) for x in days)))

    import bisect as _bisect

    out = {}
    for d in targets:
        cur_wk = week_start(d)
        ci = week_index.get(cur_wk)
        if ci is None:
            continue
        cur_days_all = by_week[cur_wk]
        # Bars this week through d (real bars, holiday-safe) — cur_days_all is ASC.
        cur_days = [x for x in cur_days_all if x[0] <= d]
        if not cur_days:
            continue
        t = transition_t(len(cur_days))

        # Partial current-week bar (Mon->d) overrides the complete current week.
        partial = _PhRow(
            cur_wk, cur_days[0][1], max(x[2] for x in cur_days),
            min(x[3] for x in cur_days), cur_days[-1][4],
            sum(int(x[5]) for x in cur_days))

        # weekly series = completed-week prefix (PURE SLICE, no re-aggregation)
        # tail-capped to MAX_WEEKS_TAIL ending at the partial current week.
        lo = max(0, ci - (MAX_WEEKS_TAIL - 1))
        weekly_ph = complete_weeks[lo:ci] + [partial]
        if len(weekly_ph) < MIN_WEEKS:
            continue
        ind_cache = _build_ind_cache(weekly_ph)
        if not ind_cache or ind_cache[-1].date != cur_wk:
            continue
        tr, rs, mc = _weekly_scores_for_target(stock, cur_wk, ind_cache)
        if None in (tr, rs, mc):
            continue
        out[d] = (int(tr), int(rs), int(mc), round(t, 3))

    return out
