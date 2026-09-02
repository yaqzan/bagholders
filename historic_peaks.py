"""
historic_peaks.py -- builds and refreshes the historic_peaks cache table.

For each tracked stock, finds distinct signal events (score >=75 or <=25)
in the last WINDOW_DAYS calendar days.  Consecutive qualifying days within
MIN_EVENT_GAP are clustered into a single event; the most extreme score in
each cluster is kept.

Run via:
    python trader.py historic-update
or automatically during `trader update`.
"""

from datetime import date, datetime, time, timedelta
from colorama import Fore
from collections import defaultdict
import math

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo('America/New_York')
except Exception:
    _ET = None  # fall back to local time if tzdata unavailable

# Cutoff (ET) past which today's Score rows are considered "post-close" and
# safe to include in the rebuild. Market closes 16:00 ET; we add a 30 min
# settling buffer because Yahoo's daily bar can revise volume on late prints
# (the COHR=84 ghost on 2026-05-06 was caused by a 16:35 rebuild capturing
# a 15:00 intraday score that flipped to 59 by 19:00 close).
POST_CLOSE_HOUR_ET = 16
POST_CLOSE_MIN_ET  = 30


def _is_post_close():
    """True if current ET time is past the post-close cutoff."""
    if _ET is None:
        return datetime.now().time() >= time(POST_CLOSE_HOUR_ET, POST_CLOSE_MIN_ET)
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:  # Sat/Sun: any time of day is "post-close"
        return True
    return now_et.time() >= time(POST_CLOSE_HOUR_ET, POST_CLOSE_MIN_ET)


WINDOW_DAYS     = 365  # look-back window for peak detection
HIGH_THRESHOLD  = 75   # score must be >= this to qualify as a HIGH (call) peak
LOW_THRESHOLD   = 25   # score must be <= this to qualify as a LOW (put) peak
MIN_EVENT_GAP   = 7    # days gap required to start a new event for the same stock
MIXED_HIGH_GATE = 75   # after a HIGH peak, a score >= this triggers mixed-signal note
MIXED_LOW_GATE  = 25   # after a LOW peak, a score <= this triggers mixed-signal note

# Barrier-touch parameters — mirror assess_scores.py constants
_SWING_K_HIGH = 1.0          # PUT  side target = 1σ drop  @ 30d
_SWING_M_HIGH = 2.0          # PUT  side stop   = 2σ rise  @ 30d
_SWING_K_LOW  = 2.0          # CALL side target = 2σ rise  @ 30d
_SWING_M_LOW  = 5.0          # CALL side stop   = 5σ drop  @ 30d
_SWING_REF_DAYS = 30
_SWING_VOL_LOOKBACK = 60
# Periods are CALENDAR days (matches DTE options semantics). 1d stays special:
# always the next trading bar direction check regardless of calendar gap.
_WIN_PERIODS = [('1d', 1), ('7d', 7), ('15d', 15), ('30d', 30), ('60d', 60), ('90d', 90)]


def _realized_vol(closes):
    """Daily realized vol (% per day) from a list of close prices. Returns None if < 30 values."""
    if len(closes) < max(5, _SWING_VOL_LOOKBACK // 2):
        return None
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    if len(rets) < max(5, _SWING_VOL_LOOKBACK // 2):
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


def _barrier_wins(peak_type, entry, vol_pct, fwd_bars, peak_date):
    """Compute barrier-touch wins for each period (CALENDAR-day windows).

    Returns two dicts:
      results       — target hit BEFORE stop (stop-aware, used for portfolio sim accuracy)
      reach_results — target hit at ANY point during window (stop-blind, used for signal accuracy)

    None for open periods that have not hit a target/stop yet, or for periods
    with insufficient forward history. Wins resolve immediately once the target
    is touched; losses from expiry resolve only after the window is complete.
    """
    null_both = ({label: None for label, _ in _WIN_PERIODS},
                 {label: None for label, _ in _WIN_PERIODS})
    if vol_pct is None or vol_pct <= 0 or entry <= 0 or not fwd_bars:
        return null_both

    side = 'low' if peak_type == 'HIGH' else 'high'  # matches _peak_side in assess
    K_base = _SWING_K_HIGH if side == 'high' else _SWING_K_LOW
    M_base = _SWING_M_HIGH if side == 'high' else _SWING_M_LOW

    last_date = fwd_bars[-1][0]
    results = {}
    reach_results = {}
    for label, W in _WIN_PERIODS:
        period_bars = fwd_bars
        window_complete = True
        if label == '1d':
            # 1d resolves from the next trading bar only, but still uses the
            # same intraday barrier semantics as the longer Historic windows.
            period_bars = fwd_bars[:1]
        else:
            cutoff = peak_date + timedelta(days=W)
            window_complete = last_date > cutoff
            period_bars = [row for row in fwd_bars if row[0] <= cutoff]
            if not period_bars:
                results[label] = None
                reach_results[label] = None
                continue

        scale = math.sqrt(W / _SWING_REF_DAYS)
        k = K_base * scale
        m = M_base * scale
        if side == 'high':
            t_win  = entry * (1 - k * vol_pct / 100)
            t_stop = entry * (1 + m * vol_pct / 100)
        else:
            t_win  = entry * (1 + k * vol_pct / 100)
            t_stop = entry * (1 - m * vol_pct / 100)

        outcome = None
        reach = 0
        trade_resolved = False
        for d, h, l, c in period_bars:
            if side == 'high':
                hit_win  = l <= t_win
                hit_stop = h >= t_stop
            else:
                hit_win  = h >= t_win
                hit_stop = l <= t_stop

            if hit_win:
                reach = 1  # target reached at some point regardless of stop

            if not trade_resolved:
                # stop-aware outcome: first trigger wins
                if hit_win and hit_stop:
                    outcome = 1 if (side == 'high' and c <= t_win) or (side == 'low' and c >= t_win) else 0
                    trade_resolved = True
                elif hit_win:
                    outcome = 1
                    trade_resolved = True
                elif hit_stop:
                    outcome = 0
                    trade_resolved = True

            if trade_resolved and reach:
                break  # both decided — no need to scan further

        if outcome is None and window_complete:
            outcome = 0
        reach_outcome = reach if (reach or window_complete) else None
        results[label] = outcome
        reach_results[label] = reach_outcome

    return results, reach_results


def _week_start(d):
    return d - timedelta(days=d.weekday())


def _extremity(score):
    """Distance from 50; used to rank peaks (higher = more extreme)."""
    return abs(score - 50)


def update_historic_peaks(window_days: int = WINDOW_DAYS, verbose: bool = True,
                          allow_intraday: bool = False) -> int:
    """Rebuild the historic_peaks table for all tracked stocks.

    Returns the number of rows written.
    """
    from database.models.core import Stock, Score, WeeklyScore, AlgorithmVersion, HistoricPeak, DteRecommendation
    from database.models.technical import PriceHistory

    HistoricPeak.ensure_schema()

    version = AlgorithmVersion.get_active_scores_version()
    today = date.today()

    # Post-close gate: today's Score row is unstable until full-session OHLCV
    # has settled (volume signal, weekly composite refresh). Excluding today
    # from the rebuild before close keeps a 15:00 intraday score from being
    # snapshotted as a "peak" that later gets overwritten by the close run.
    # Pass allow_intraday=True only when running an explicit ad-hoc rebuild.
    end_date = today if (allow_intraday or _is_post_close()) else today - timedelta(days=1)
    cutoff = end_date - timedelta(days=window_days)

    if verbose:
        gate_note = "" if end_date == today else f" (pre-close — excluding today)"
        print(Fore.CYAN + f"[historic_peaks] scanning {window_days}d window ({cutoff} -> {end_date}){gate_note} ...")

    # -- 1. Load all qualifying extreme scores in the window -----------------
    # Two AND-range queries, no JOIN, no SQL ORDER BY. The previous joined
    # OR-form let the optimizer flip to a Stock-driven plan after bulk-insert
    # statistics drift (probing scores by symbol-only PK prefix = millions of
    # cold row visits, blowing the 30s read_timeout and leaving server-side
    # zombie queries — 2026-06-11 v72-ship incident). Each range below is
    # served deterministically by scores_version_overall_date_IDX; Stock is
    # stitched from a dict and rows are sorted in Python (same pattern and
    # rationale as the mixed-signal query below).
    _q_base = ((Score.version == version)
               & (Score.date >= cutoff)
               & (Score.date <= end_date))
    rows = list(Score.select().where(_q_base & (Score.overall >= HIGH_THRESHOLD)))
    rows += list(Score.select().where(_q_base & (Score.overall <= LOW_THRESHOLD)))
    _stock_by_sym = {s.symbol: s for s in Stock.select()}
    # preserve the old INNER JOIN semantics: drop scores with no Stock row
    rows = [r for r in rows if r.symbol_id in _stock_by_sym]
    rows.sort(key=lambda r: (r.symbol_id, r.date))

    if verbose:
        print(Fore.CYAN + f"[historic_peaks] {len(rows)} qualifying score rows across all stocks")

    # -- 2. Load mixed-signal candidates -------------------------------------
    # Mixed-signal detection fires only when a forward score is
    # >= MIXED_HIGH_GATE (75) or <= MIXED_LOW_GATE (25). Loading every score
    # in the window (~200k rows) was timing out at 30s on filesort + transfer.
    # Narrowed to extremes only (~3k rows, 700x faster). Behavior identical:
    # rows in (25, 75) would be skipped by the consumer loop anyway.
    # No SQL ORDER BY — sort per-symbol in Python after grouping (microseconds).
    mixed_rows = list(
        Score.select(Score.symbol, Score.date, Score.overall)
        .where(
            Score.version == version,
            Score.date >= cutoff,
            Score.date <= end_date,
            (Score.overall >= MIXED_HIGH_GATE) | (Score.overall <= MIXED_LOW_GATE),
        )
    )
    symbol_scores: dict[str, list] = defaultdict(list)
    for r in mixed_rows:
        symbol_scores[r.symbol_id].append((r.date, r.overall))
    for sym in symbol_scores:
        symbol_scores[sym].sort(key=lambda t: t[0])

    # -- 3. Cluster qualifying scores into events per stock ------------------
    #   Consecutive qualifying days within MIN_EVENT_GAP are one event.
    #   Pick the most extreme score per event.
    sym_events: dict[str, list] = defaultdict(list)  # symbol -> [[row, ...], ...]
    for row in rows:
        sym = row.symbol_id
        events = sym_events[sym]
        if not events or (row.date - events[-1][-1].date).days > MIN_EVENT_GAP:
            events.append([row])
        else:
            events[-1].append(row)

    # Best row per event (most extreme score)
    peaks = []  # list of (score_row, stock_obj)
    for sym, events in sym_events.items():
        for event in events:
            best_row = max(event, key=lambda r: _extremity(r.overall))
            peaks.append((best_row, _stock_by_sym[best_row.symbol_id]))

    if verbose:
        n_stocks = len(sym_events)
        print(Fore.CYAN + f"[historic_peaks] {len(peaks)} events across {n_stocks} stocks")

    if not peaks:
        if verbose:
            print(Fore.YELLOW + "[historic_peaks] no peaks found -- nothing to write")
        return 0

    # -- 4. Batch-load PriceHistory for all qualifying symbols ---------------
    #   Load extra history before cutoff for realized-vol calculation (60 trading days ≈ 90 calendar).
    all_syms = list(sym_events.keys())
    vol_buffer = timedelta(days=90)
    ph_rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.high, PriceHistory.low, PriceHistory.close,
        )
        .where(
            PriceHistory.symbol_id.in_(all_syms),
            PriceHistory.date >= cutoff - vol_buffer,
            PriceHistory.date <= today,
        )
        .order_by(PriceHistory.symbol, PriceHistory.date)
    )
    ph_by_sym: dict[str, list] = defaultdict(list)
    for r in ph_rows:
        ph_by_sym[r.symbol_id].append((r.date, float(r.high), float(r.low), float(r.close)))

    # -- 5. Load current prices (latest score per symbol) --------------------
    current_scores = {}
    for sym in all_syms:
        latest = (
            Score.select(Score.price)
            .where(Score.version == version, Score.symbol_id == sym)
            .order_by(Score.date.desc())
            .first()
        )
        if latest:
            current_scores[sym] = latest.price

    # -- 6. Clear table and write all event peaks ----------------------------
    # DELETE all rows (including stale-version rows from prior algorithm bumps
    # that left orphans behind) — this rebuild is the source of truth for the
    # active version. The (symbol, peak_date) unique index would otherwise
    # collide with a prior-version row at the same key.
    HistoricPeak.delete().execute()

    written = 0
    for score_row, stock_obj in peaks:
        sym = score_row.symbol_id
        peak_score = score_row.overall
        peak_date  = score_row.date
        peak_type  = 'HIGH' if peak_score >= HIGH_THRESHOLD else 'LOW'
        price_at_peak = float(score_row.price) if score_row.price else None

        # Weekly composite for peak date
        ws = WeeklyScore.get_or_none(
            WeeklyScore.symbol == sym,
            WeeklyScore.date == _week_start(peak_date),
        )
        peak_weekly = ws.composite if ws else None

        # DTE info at peak
        dte_row = None
        try:
            dte_row = DteRecommendation.get_or_none(
                DteRecommendation.symbol == sym,
                DteRecommendation.date == peak_date,
                DteRecommendation.version == version,
            )
        except Exception:
            pass

        # -- Price peak outcome: best move in the hypothesis direction -------
        price_peak_pct = None
        price_peak_days_after = None
        # Future rows sorted by date (already ordered from query)
        ph_future = [(d, h, l, c) for d, h, l, c in ph_by_sym.get(sym, []) if d > peak_date]
        if price_at_peak and price_at_peak > 0 and ph_future:
            if peak_type == 'HIGH':
                best_val, best_day = max((h, d) for d, h, l, c in ph_future)
                price_peak_pct = round((best_val - price_at_peak) / price_at_peak * 100, 2)
                price_peak_days_after = (best_day - peak_date).days
            else:
                best_val, best_day = min((l, d) for d, h, l, c in ph_future)
                price_peak_pct = round((price_at_peak - best_val) / price_at_peak * 100, 2)
                price_peak_days_after = (best_day - peak_date).days

        # -- Per-period forward returns + intraday best ----------------------------
        # Periods are CALENDAR days from peak_date. A period is "complete" when
        # today has moved past peak_date + N cal days (window has fully closed in
        # wall-clock time). The first still-open horizon shows in-progress data;
        # subsequent horizons remain None.
        _FWD_PERIODS = [('fwd_1d', 1), ('fwd_7d', 7), ('fwd_15d', 15),
                        ('fwd_30d', 30), ('fwd_60d', 60), ('fwd_90d', 90)]
        fwd_returns: dict = {}
        fwd_peaks:   dict = {}
        fwd_pk_days: dict = {}

        if price_at_peak and price_at_peak > 0 and ph_future:
            shown_inprogress = False

            for label, n_cal in _FWD_PERIODS:
                suffix   = label[4:]              # '1d', '7d', …
                pk_label = f'fwd_peak_{suffix}'
                dy_label = f'fwd_peak_days_{suffix}'

                cutoff = peak_date + timedelta(days=n_cal)
                complete = today >= cutoff
                # Bars within the calendar window (or 1d: first bar only)
                if label == 'fwd_1d':
                    bars = ph_future[:1]
                else:
                    bars = [row for row in ph_future if row[0] <= cutoff]

                if not bars:
                    fwd_returns[label] = None
                    fwd_peaks[pk_label]  = None
                    fwd_pk_days[dy_label] = None
                    continue

                if not complete:
                    if shown_inprogress:
                        fwd_returns[label] = None
                        fwd_peaks[pk_label]  = None
                        fwd_pk_days[dy_label] = None
                        continue
                    shown_inprogress = True

                # Close return at end of window (last in-window bar)
                fwd_returns[label] = round(
                    (bars[-1][3] - price_at_peak) / price_at_peak * 100, 2
                )

                # Direction-adjusted intraday best within window
                if peak_type == 'HIGH':
                    best_i   = max(range(len(bars)), key=lambda i: bars[i][1])
                    best_val = bars[best_i][1]
                    fwd_peaks[pk_label] = round(
                        (best_val - price_at_peak) / price_at_peak * 100, 2
                    )
                else:
                    best_i   = min(range(len(bars)), key=lambda i: bars[i][2])
                    best_val = bars[best_i][2]
                    fwd_peaks[pk_label] = round(
                        (price_at_peak - best_val) / price_at_peak * 100, 2
                    )
                # Calendar days from peak to the best bar
                fwd_pk_days[dy_label] = (bars[best_i][0] - peak_date).days
        else:
            for label, _ in _FWD_PERIODS:
                suffix = label[4:]
                fwd_returns[label]               = None
                fwd_peaks[f'fwd_peak_{suffix}']  = None
                fwd_pk_days[f'fwd_peak_days_{suffix}'] = None

        # -- Incremental peak deltas between adjacent periods ----------------
        def _sub(a, b):
            return round(a - b, 2) if (a is not None and b is not None) else None

        fwd_deltas = {
            'fwd_peak_delta_7d':  _sub(fwd_peaks.get('fwd_peak_7d'),  fwd_peaks.get('fwd_peak_1d')),
            'fwd_peak_delta_15d': _sub(fwd_peaks.get('fwd_peak_15d'), fwd_peaks.get('fwd_peak_7d')),
            'fwd_peak_delta_30d': _sub(fwd_peaks.get('fwd_peak_30d'), fwd_peaks.get('fwd_peak_15d')),
            'fwd_peak_delta_60d': _sub(fwd_peaks.get('fwd_peak_60d'), fwd_peaks.get('fwd_peak_30d')),
            'fwd_peak_delta_90d': _sub(fwd_peaks.get('fwd_peak_90d'), fwd_peaks.get('fwd_peak_60d')),
        }

        # -- Mixed-signal detection ------------------------------------------
        mixed_date = mixed_score = mixed_days = None
        timeline = symbol_scores.get(sym, [])
        for d, ovr in timeline:
            if d <= peak_date:
                continue
            if peak_type == 'HIGH' and ovr <= MIXED_LOW_GATE:
                mixed_date = d
                mixed_score = ovr
                mixed_days = (d - peak_date).days
                break
            elif peak_type == 'LOW' and ovr >= MIXED_HIGH_GATE:
                mixed_date = d
                mixed_score = ovr
                mixed_days = (d - peak_date).days
                break

        current_price = current_scores.get(sym)

        # -- Barrier-touch win per period -----------------------------------
        ph_all = ph_by_sym.get(sym, [])
        # Find peak_date index in full price history (includes vol-buffer)
        _peak_idx = None
        for _i, (d, h, l, c) in enumerate(ph_all):
            if d == peak_date:
                _peak_idx = _i
                break
        win_flags = {label: None for label, _ in _WIN_PERIODS}
        reach_flags = {label: None for label, _ in _WIN_PERIODS}
        vol_pct = None
        if _peak_idx is not None and price_at_peak and price_at_peak > 0:
            # Realized vol from closes up to and including peak date
            vol_closes = [c for _, _, _, c in ph_all[max(0, _peak_idx - _SWING_VOL_LOOKBACK):_peak_idx + 1]]
            vol_pct = _realized_vol(vol_closes)
            # Forward bars after peak (date, high, low, close)
            fwd_bars = [(d, h, l, c) for d, h, l, c in ph_all[_peak_idx + 1:]]
            win_flags, reach_flags = _barrier_wins(peak_type, price_at_peak, vol_pct, fwd_bars, peak_date)

        HistoricPeak.create(
            symbol              = sym,
            algorithm_version   = version,
            peak_date           = peak_date,
            peak_score          = peak_score,
            peak_type           = peak_type,
            price_at_peak       = score_row.price,
            current_price       = current_price,
            peak_trend          = score_row.trend,
            peak_bb             = score_row.bb,
            peak_rsi            = score_row.rsi,
            peak_macd           = score_row.macd,
            peak_vol            = score_row.volume,
            peak_weekly         = peak_weekly,
            peak_volume_signal  = score_row.volume_signal,
            peak_dte_thesis     = dte_row.thesis     if dte_row else None,
            peak_dte_min        = dte_row.dte_min    if dte_row else None,
            peak_dte_max        = dte_row.dte_max    if dte_row else None,
            peak_dte_target     = dte_row.dte_target if dte_row else None,
            price_peak_pct      = price_peak_pct,
            price_peak_days_after = price_peak_days_after,
            fwd_1d              = fwd_returns.get('fwd_1d'),
            fwd_7d              = fwd_returns.get('fwd_7d'),
            fwd_15d             = fwd_returns.get('fwd_15d'),
            fwd_30d             = fwd_returns.get('fwd_30d'),
            fwd_60d             = fwd_returns.get('fwd_60d'),
            fwd_90d             = fwd_returns.get('fwd_90d'),
            fwd_peak_1d         = fwd_peaks.get('fwd_peak_1d'),
            fwd_peak_days_1d    = fwd_pk_days.get('fwd_peak_days_1d'),
            fwd_peak_7d         = fwd_peaks.get('fwd_peak_7d'),
            fwd_peak_days_7d    = fwd_pk_days.get('fwd_peak_days_7d'),
            fwd_peak_15d        = fwd_peaks.get('fwd_peak_15d'),
            fwd_peak_days_15d   = fwd_pk_days.get('fwd_peak_days_15d'),
            fwd_peak_30d        = fwd_peaks.get('fwd_peak_30d'),
            fwd_peak_days_30d   = fwd_pk_days.get('fwd_peak_days_30d'),
            fwd_peak_60d        = fwd_peaks.get('fwd_peak_60d'),
            fwd_peak_days_60d   = fwd_pk_days.get('fwd_peak_days_60d'),
            fwd_peak_90d        = fwd_peaks.get('fwd_peak_90d'),
            fwd_peak_days_90d   = fwd_pk_days.get('fwd_peak_days_90d'),
            fwd_peak_delta_7d   = fwd_deltas['fwd_peak_delta_7d'],
            fwd_peak_delta_15d  = fwd_deltas['fwd_peak_delta_15d'],
            fwd_peak_delta_30d  = fwd_deltas['fwd_peak_delta_30d'],
            fwd_peak_delta_60d  = fwd_deltas['fwd_peak_delta_60d'],
            fwd_peak_delta_90d  = fwd_deltas['fwd_peak_delta_90d'],
            win_1d              = win_flags.get('1d'),
            win_7d              = win_flags.get('7d'),
            win_15d             = win_flags.get('15d'),
            win_30d             = win_flags.get('30d'),
            win_60d             = win_flags.get('60d'),
            win_90d             = win_flags.get('90d'),
            reach_1d            = reach_flags.get('1d'),
            reach_7d            = reach_flags.get('7d'),
            reach_15d           = reach_flags.get('15d'),
            reach_30d           = reach_flags.get('30d'),
            reach_60d           = reach_flags.get('60d'),
            reach_90d           = reach_flags.get('90d'),
            mixed_signal_date   = mixed_date,
            mixed_signal_score  = mixed_score,
            mixed_signal_days_after = mixed_days,
            vol_pct             = vol_pct,
            updated_at          = datetime.now(),
        )
        written += 1

    if verbose:
        print(Fore.GREEN + f"[historic_peaks] done -- {written} events written")

    return written
