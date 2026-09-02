#!/usr/bin/env python3
"""
Live Portfolio engine — transparent realization of the v70 Apex strategy.

The active portfolio starts on a fixed date with a fixed CAD capital and, every
``trader update``, the deterministic cascade backtest (backtest_cascade) is
re-evaluated over [start_date .. last completed market session] for the chosen
portfolio profile (default Apex). Because the backtest only ever uses bars that
already exist, re-running it each update reproduces *exactly* what the strategy
would be holding today (and the full equity history) with zero look-ahead.

This module materializes that deterministic run into three tables
(portfolio_runs / portfolio_positions / portfolio_equity_snapshots) so the
React Portfolio page can serve it cheaply, and so the ONLY push notifications
sent are position OPEN and CLOSE events (replacing the old score-list digest).

Trades are only actioned on completed market sessions — a signal produced
outside market hours is not actioned until that session closes (which is what
the deterministic close-bar entry models).

Wipe + restart:  python portfolio_engine.py reset
One-shot sync:   python portfolio_engine.py sync
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Fixed run parameters (per spec)
# ---------------------------------------------------------------------------
START_DATE = date(2026, 6, 1)          # portfolio inception
GO_LIVE_DATE = date(2026, 6, 5)        # first day notifications fire (June 1-4 = silent history)
START_CAPITAL_CAD = 50_000.0
DEFAULT_PROFILE = 'core'              # restructure 2026-06-17: Core = the former Apex params (long-run held compounder, default). Apex is now the opt-in fast-2x SPRINT.
MIN_SCORE = 70.0                       # include the 70-74 overflow tier (Core/old-Apex)
MARKET_CLOSE_HOUR_ET = 16              # a session is "complete" at/after 16:00 ET


# ---------------------------------------------------------------------------
# Time / session helpers
# ---------------------------------------------------------------------------
def _eastern_now():
    try:
        from task_queue.resources import eastern_now
        return eastern_now()
    except Exception:
        return datetime.now()


def last_completed_session(now=None, latest_data=None):
    """Most recent fully-closed trading session.

    During an open RTH session today, today's bar is not yet final -> the last
    completed session is the prior trading day. After the close (or on a
    weekend/holiday) today's bar is ELIGIBLE to be final -- but wall-clock
    alone is not proof of that (see _session_finalized): when the fallback
    below resolves `d` to TODAY specifically, it is additionally required to
    pass the data-driven finalization check; otherwise treat today as not yet
    complete, same as the pre-close branch. Only today is re-checked -- any
    prior day the wall-clock logic already resolved to is left untouched.
    Capped at the latest available price date so we never ask the backtest
    for a day with no data.
    """
    from database.utils.trading_calendar import is_trading_day, last_trading_day
    et = now or _eastern_now()
    today = et.date()
    if is_trading_day(today) and et.hour < MARKET_CLOSE_HOUR_ET:
        d = last_trading_day(today - timedelta(days=1))
    else:
        d = last_trading_day(today)
        if d == today and not _session_finalized(d):
            d = last_trading_day(today - timedelta(days=1))
    if latest_data is not None and d > latest_data:
        d = last_trading_day(latest_data)
    return d


def _session_finalized(d):
    """Data-driven proof that date `d`'s scores reflect a genuinely
    after-the-close computation, not just a wall-clock guess past 16:00
    (P0.2 fill-race fix, 2026-07-13; gameplan.md P0.2).

    Root cause this replaces: `trader update` runs on a discrete weekday grid
    (scripts/install_trader_update_via_queue.ps1) whose LAST intraday slot is
    15:45 with up to a 40-min execution ceiling -- so a run that STARTS before
    the close can still be writing today's Score rows (off price data fetched
    before the bell) well after wall-clock ticks past MARKET_CLOSE_HOUR_ET.
    The old logic trusted wall-clock alone, so THAT run's portfolio sync would
    treat today as complete and fill on a still-provisional score. This is
    exactly the documented ADUR 2026-06-10 incident: a 16:11 engine fill on a
    same-day 79 that the ~16:30 close pipeline corrected to 38 twenty minutes
    later (known-issues.md 2026-06-11 BDIV entry: "the engine's 16:00 session
    gate vs ~16:30 close-update; follow-up chip spawned" -- this is that chip).

    Two candidate markers were on the table (gameplan.md P0.2's own wording):
      (a) a close-update pipeline completion marker (e.g. post_market_daily.ps1's
          done.json), or
      (b) a late ScoreIntradayLog snapshot for D.
    (a) is REJECTED: post_market_daily.ps1 was deliberately split into
    independent phases on 2026-06-18 specifically so the portfolio sync fires
    right after scoring and does NOT wait on the options-pull / derived-tail
    phases -- the pre-split pipeline delayed confirms to 21:25 for a 16:00
    close (see that script's header comment). A done.json marker only exists
    after ALL phases finish, so gating fills on it would re-import the exact
    multi-hour delay regression that split was built to fix. It would also
    only exist when the pipeline runs via that script (not a bare `trader
    update`, not `trader portfolio sync`, not the API sync endpoint).
    (b) is what's implemented here. `score_intraday_logs` already appends one
    row per symbol on every `trader update` run (audit_intraday_scores=True
    default), `(run_key, logged_at)` is already an indexed pair, and
    `logged_at` defaults to `datetime.now()` at INSERT time -- so within one
    run_key, MIN(logged_at) is that run's start to within seconds, for free
    (no new column, no filesystem/cross-process coupling, no dependency on
    which CLI verb produced it).

    A run's OWN start -- not any individual row's write time -- is what must
    be at/after the close: the straddling 15:45 run can log individual rows
    well past 16:00 purely because its per-symbol loop is still running, so a
    row-level `logged_at >= close` check would still wrongly accept it. Only
    a run that itself BEGAN at/after MARKET_CLOSE_HOUR_ET (in practice the
    ~16:30 close pipeline) is evidence today's Score rows reflect the settled
    daily bar.

    No qualifying run yet -> False (defer, never force -- the same posture
    `_requalify_position`'s 'unknown' verdict takes for missing score rows;
    self-healing, since the very next pre-close-branch check the following
    morning resolves `d` to today via the untouched wall-clock path anyway,
    so a broken/empty log degrades to "one session late", never a deadlock).
    This only gates resolving `d == today`; any earlier day the wall-clock
    branch already picked is left alone, so a date predating this table
    (the audit log starts 2026-05-27) can never get wedged by it.
    """
    from database.models.core import ScoreIntradayLog
    from peewee import fn
    try:
        close_dt = datetime(d.year, d.month, d.day, MARKET_CLOSE_HOUR_ET, 0)
        runs = (ScoreIntradayLog
                .select(fn.MIN(ScoreIntradayLog.logged_at).alias('start'))
                .where(ScoreIntradayLog.date == d)
                .group_by(ScoreIntradayLog.run_key))
        return any(r.start is not None and r.start >= close_dt for r in runs)
    except Exception:
        return False


def _latest_price_date():
    from peewee import fn
    from database.models.technical import PriceHistory
    return PriceHistory.select(fn.MAX(PriceHistory.date)).scalar()


# ---------------------------------------------------------------------------
# FX (USD->CAD). Fetched once at run creation and frozen so the CAD curve pins
# the 50k start; positions are always displayed in native USD.
# ---------------------------------------------------------------------------
def _cad_per_usd():
    from database.models.core import ExchangeRate
    ExchangeRate.ensure_schema()
    rate = None
    try:
        import yfinance as yf
        hist = yf.Ticker('USDCAD=X').history(period='5d')
        if hist is not None and len(hist):
            rate = float(hist['Close'].dropna().iloc[-1])
    except Exception:
        rate = None
    if rate is None:
        cached = ExchangeRate.get_latest('USD', 'CAD')
        if cached:
            rate = float(cached)
    if rate is None:
        legacy = ExchangeRate.get_latest('CAD', 'USD')
        if legacy:
            rate = float(legacy) if float(legacy) > 1.0 else (1.0 / float(legacy) if float(legacy) > 0 else None)
    if rate is None or not (1.0 < rate < 2.0):
        rate = 1.37
    try:
        ExchangeRate.store(rate, 'USD', 'CAD')
    except Exception:
        pass
    return round(rate, 6)


# ---------------------------------------------------------------------------
# Option label helpers (representational — strike is ATM-rounded, expiration is
# the listed monthly OPEX near entry+DTE; P&L is driven by the underlying model)
# ---------------------------------------------------------------------------
def nearest_strike(price):
    p = float(price)
    if p <= 0:
        return round(p, 2)
    if p < 25:
        step = 0.5
    elif p < 100:
        step = 1.0
    elif p < 250:
        step = 2.5
    elif p < 1000:
        step = 5.0
    else:
        step = 10.0
    return round(round(p / step) * step, 2)


def _third_friday(year, month):
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7       # days to the first Friday (Fri=4)
    return first + timedelta(days=offset + 14)


def expiration_for(entry_date, dte):
    target = entry_date + timedelta(days=int(dte))
    exp = _third_friday(target.year, target.month)
    if exp < target - timedelta(days=10):
        ny = target.year + (1 if target.month == 12 else 0)
        nm = 1 if target.month == 12 else target.month + 1
        exp = _third_friday(ny, nm)
    return exp


def _strike_fmt(strike):
    s = float(strike)
    return f"{s:g}"


def position_label(symbol, expiration_date, strike, side):
    side_txt = 'Call' if side == 'call' else 'Put'
    if expiration_date is None:
        return f"{symbol} ${_strike_fmt(strike)} {side_txt}"
    return f"{symbol} {expiration_date.strftime('%b')} {expiration_date.day} ${_strike_fmt(strike)} {side_txt}"


def _mult_delta(dte):
    import strategy_config as sc
    if int(dte) == 15:
        return float(sc.STRATEGY_15DTE.PREMIUM_MULT), float(sc.STRATEGY_15DTE.option.DELTA)
    return float(sc.STRATEGY_30DTE.PREMIUM_MULT), float(sc.STRATEGY_30DTE.option.DELTA)


_REASON_LABEL = {
    'tp': 'Take profit', 'sl': 'Stop loss', 'hard': 'Hard sell (day 15)',
    'dh_pop': 'Recovery exit', 'dh_expiry': 'Expiry', 'dh_open': 'Expiry',
    'prem': 'Stop loss', 'realloc': 'Reallocated',
    'version_sweep': 'Strategy change', 'strategy_sweep': 'Strategy change',
}

MARKET_OPEN_ET = (9, 30)               # RTH open (alert wording: "at the open" vs "now")

# Execution-timing canon (entry_timing_v71, 2026-06-11): intraday scores are
# partial-day reads — 26% of morning-qualified signals fade by the close, while
# the ~15:45 read holds to the close 92% of the time with ~0.4% median price
# drift. Provisional BUY alerts therefore fire only inside the last-30-min
# execution window; SELL alerts stay live during market hours (price-barrier
# touches, not score reads). Next-open entry costs ~1.3pp WR vs the close fill.
#
# Push timing (2026-06-12): NO pushes outside [MORNING_ALERT_FROM_ET, close) —
# the 04:00/07:00 update hooks stay silent; pre-open actions (carry-over digest
# + sweep/deadline sells) push from 08:30 ET so they arrive when actionable.
# The heavy ~20-min updates finish their alert pass at unpredictable times, so
# the precise pushes come from the scheduled lightweight `trader portfolio
# notify` passes (08:45 + 15:30 ET — see scripts/install_portfolio_notify.ps1).
BUY_ALERT_FROM_ET = (15, 25)           # provisional-buy pushes: 15:25 ET → close
MORNING_ALERT_FROM_ET = (8, 30)        # earliest pre-open push (digest + sells)


def _contract_bits(p):
    """('Call'/'Put', '$<strike>', '<dte>DTE') for a position."""
    side_txt = 'Put' if p.side == 'put' else 'Call'
    strike = f"${float(p.strike):g}" if getattr(p, 'strike', None) is not None else ''
    return side_txt, strike, f"{int(p.dte)}DTE"


def format_open_notification(p):
    """(title, body) for an opened-position push. `p` exposes symbol /
    strike / side / dte / alloc_pct / premium_usd / entry_score."""
    side_txt, strike, dte = _contract_bits(p)
    alloc = f" ({p.alloc_pct:.1f}%)" if getattr(p, 'alloc_pct', None) is not None else ''
    line1 = f"{p.entry_score} · {dte} · {strike} {side_txt}".strip()
    line2 = f"${float(p.premium_usd):,.0f}{alloc}"
    return f"{_buy_emoji(getattr(p, 'side', None))} Opened {p.symbol}", f"{line1}\n{line2}"


def format_close_notification(p):
    """(title, body) for a closed-position push."""
    pnl_usd = float(getattr(p, 'pnl_usd', 0) or 0)
    pnl_pct = float(getattr(p, 'pnl_pct', 0) or 0) * 100.0
    emoji = _sell_emoji(getattr(p, 'exit_reason', None))
    reason = _REASON_LABEL.get(getattr(p, 'exit_reason', None), (getattr(p, 'exit_reason', None) or 'Closed'))
    side_txt, strike, dte = _contract_bits(p)
    sign = '+' if pnl_usd >= 0 else '-'
    line1 = f"{dte} · {strike} {side_txt}".strip()
    line2 = f"{reason} · {sign}${abs(pnl_usd):,.0f} ({pnl_pct:+.0f}%)"
    return f"{emoji} Closed {p.symbol}", f"{line1}\n{line2}"


def _pos_bits(p):
    """_contract_bits for an in-memory ledger dict."""
    side_txt = 'Put' if p.get('side') == 'put' else 'Call'
    strike = f"${float(p['strike']):g}" if p.get('strike') is not None else ''
    return side_txt, strike, f"{int(p.get('dte') or 30)}DTE"


# Emoji scheme: buys are circles by side (🟢 call / 🔴 put); sells are 💰 for
# take profit and ❌ for every other exit (SL, hard sell, dead-hold, sweeps).
def _sell_emoji(reason):
    return '\U0001F4B0' if reason == 'tp' else '\U0000274C'


def _buy_emoji(side):
    return '\U0001F534' if side == 'put' else '\U0001F7E2'


def format_sell_alert(p, reason, est_pnl, when):
    """Actionable live SELL push — fires DURING market hours / pre-open, ahead
    of the ledger's close-of-session actioning."""
    side_txt, strike, dte = _pos_bits(p)
    label = _REASON_LABEL.get(reason, reason or 'Exit')
    est = ''
    if est_pnl is not None:
        est = f" · est {est_pnl * 100.0:+.0f}% (${float(p['premium_usd']) * est_pnl:+,.0f})"
    line1 = f"{dte} · {strike} {side_txt}".strip()
    return f"{_sell_emoji(reason)} Sell {p['symbol']} {when}", f"{line1}\n{label}{est}"


def format_buy_alert(p, when):
    """Actionable live BUY push for a provisional entry that the strategy will
    open at today's close if the signal holds."""
    side_txt, strike, dte = _pos_bits(p)
    alloc = f" ({p['alloc_pct']:.1f}%)" if p.get('alloc_pct') is not None else ''
    line1 = f"{p['entry_score']} · {dte} · {strike} {side_txt}".strip()
    line2 = f"~${float(p['premium_usd']):,.0f}{alloc} · enters at today's close if the signal holds"
    return f"{_buy_emoji(p.get('side'))} Buy {p['symbol']} {when}", f"{line1}\n{line2}"


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------
def get_or_create_run():
    from database.models.portfolio import PortfolioRun, ensure_all_schema
    ensure_all_schema()
    run = PortfolioRun.get_active()
    if run is not None:
        return run
    fx = _cad_per_usd()
    usd = round(START_CAPITAL_CAD / fx, 2)
    version_id = None
    try:
        from database.models.core import AlgorithmVersion
        version_id = AlgorithmVersion.get_active_scores_version().id
    except Exception:
        version_id = None
    return PortfolioRun.create(
        name='v70 Apex Live',
        profile=DEFAULT_PROFILE,
        version_id=version_id,
        start_date=START_DATE,
        go_live_date=GO_LIVE_DATE,
        starting_capital_cad=round(START_CAPITAL_CAD, 2),
        starting_capital_usd=usd,
        cad_per_usd=fx,
    )


def reset():
    """Wipe the slate clean — delete the run + all positions/snapshots."""
    from database.models.portfolio import (
        PortfolioRun, PortfolioPosition, PortfolioEquitySnapshot, ensure_all_schema,
    )
    ensure_all_schema()
    PortfolioPosition.delete().execute()
    PortfolioEquitySnapshot.delete().execute()
    PortfolioRun.delete().execute()


# ---------------------------------------------------------------------------
# Version / strategy awareness
# ---------------------------------------------------------------------------
def _strategy_fingerprint(cfg, profile_key):
    """Stable hash of the active Apex config surface. A change here = a strategy
    change → open positions adopt the new barriers and a re-qualification sweep
    runs on the next market session."""
    import hashlib
    import json
    import strategy_config as sc
    s = sc.STRATEGY_30DTE
    o = s.option
    surface = {
        'profile': profile_key,
        'cfg': {k: cfg[k] for k in sorted(cfg)},
        'opt': [o.TP_BASE, o.TP_STRESS, o.SL_BASE, o.SL_STRESS, s.HARD_SELL_LOSS,
                o.SLIP_ENTRY, o.SLIP_TP, o.SLIP_SL, o.SLIP_HARD, o.BREADTH_THRESHOLD],
        'dh': [s.DEAD_HOLD_ENABLED, s.DEAD_HOLD_TRIGGER_PNL, s.DEAD_HOLD_POPOUT_PNL],
        'pos': [s.MAX_POSITIONS, s.MAX_POSITIONS_CALL, s.MAX_POSITIONS_PUT, s.PREMIUM_MULT,
                s.HOLD_DAYS, s.PRIMARY_THRESHOLD, s.OVERFLOW_THRESHOLD],
        'rxdd': [s.RXDD_ENABLED, s.RXDD_VIX_C, s.RXDD_VIX_W, s.RXDD_DEPTH, s.RXDD_DD_MIN],
        # Stage-3 call-alloc dampeners shipped after RXDD (getattr defaults mirror
        # backtest_cascade's stale-config degradation so a pre-mechanism strategy_config
        # in a long-running server can't crash the sync).
        'svr':  [getattr(s, 'SVR_ENABLED', False), getattr(s, 'SVR_LO_CUT', 0.60),
                 getattr(s, 'SVR_LO_FULL', 0.80), getattr(s, 'SVR_HI_FULL', 9.0),
                 getattr(s, 'SVR_HI_CUT', 99.0), getattr(s, 'SVR_FLOOR', 0.50)],
        'mwdd': [getattr(s, 'MWDD_ENABLED', False), getattr(s, 'MWDD_MCC_C', 0.0),
                 getattr(s, 'MWDD_MCC_W', 22.0), getattr(s, 'MWDD_DEPTH', 0.30),
                 getattr(s, 'MWDD_DD_MIN', 0.10), getattr(s, 'MWDD_VIX_PANIC', 28.0)],
        'tvdd': [getattr(s, 'TVDD_ENABLED', False), getattr(s, 'TVDD_TRIN_C', 1.15),
                 getattr(s, 'TVDD_TRIN_W', 0.30), getattr(s, 'TVDD_DEPTH', 0.30),
                 getattr(s, 'TVDD_DD_MIN', 0.13), getattr(s, 'TVDD_VIX_PANIC', 28.0)],
        'bdiv': [getattr(s, 'BDIV_ENABLED', False), getattr(s, 'BDIV_PROX_CUT', 0.020),
                 getattr(s, 'BDIV_PROX_FULL', 0.005), getattr(s, 'BDIV_GAP_C', 6.5),
                 getattr(s, 'BDIV_GAP_W', 2.5), getattr(s, 'BDIV_DEPTH', 0.40)],
        'spread_tilt': [getattr(s, 'SPREAD_TILT_ENABLED', False), getattr(s, 'SPREAD_TILT_LO', 26.9),
                        getattr(s, 'SPREAD_TILT_HI', 31.3), getattr(s, 'SPREAD_TILT_DEPTH', 0.40)],
        'liquidity_floor': [cfg.get('liquidity_floor', getattr(s, 'LIQUIDITY_FLOOR', 0.0))],
    }
    return hashlib.sha1(json.dumps(surface, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _flag(v):
    """Enable-flag coercion matching run_backtest ('1'/'true'/'yes' strings → bool)."""
    if isinstance(v, str):
        return v.lower() in ('1', 'true', 'yes')
    return bool(v)


def _min_call_threshold(cfg):
    """Lowest call score whose cascade tier has a non-zero allocation = the
    minimum 'qualifying' entry score under the current strategy."""
    ta = cfg.get('tier_alloc') or {}
    for score, key in ((70, '70-74'), (75, '75-79'), (80, '80-84'), (85, '85-94'), (95, '95+')):
        if float(ta.get(key, 0) or 0) > 0:
            return score
    return 75


def _entry_score_under(version_id, symbol, entry_date):
    from database.models.core import Score
    row = Score.get_or_none(
        (Score.symbol == symbol) & (Score.date == entry_date) & (Score.version == version_id)
    )
    return int(row.overall) if (row is not None and row.overall is not None) else None


def _latest_score_under(version_id, symbol, hi):
    """Most recent COMPLETED-session score (date <= hi) for the symbol under the
    given version. None when the version has no rows yet (mid-recalc)."""
    from database.models.core import Score
    row = (Score.select(Score.overall)
           .where((Score.symbol == symbol) & (Score.version == version_id)
                  & (Score.date <= hi) & (Score.overall.is_null(False)))
           .order_by(Score.date.desc())
           .first())
    return int(row.overall) if row is not None else None


def _requalify_position(version_id, p, min_thr, D):
    """Would the NEW strategy/scoring keep holding this position?

    'hold'    — entry-date score OR the latest completed-session score under the
                new version still clears the cascade threshold (the new strategy
                would have entered it, or would be buying it today → keep it).
    'sweep'   — affirmatively disqualified by the new scoring on both reads.
    'unknown' — the new version has no score rows for this symbol yet (recalc
                backfill in flight). NEVER exit on missing data — defer and
                re-check on the next sync. (Calls-only: Apex runs puts-off; a
                put re-qualification would need the <= mirror of min_thr.)
    """
    entry_sc = _entry_score_under(version_id, p['symbol'], p['entry_date'])
    if entry_sc is not None and entry_sc >= min_thr:
        return 'hold'
    latest_sc = _latest_score_under(version_id, p['symbol'], D)
    if latest_sc is not None and latest_sc >= min_thr:
        return 'hold'
    if entry_sc is None and latest_sc is None:
        return 'unknown'
    return 'sweep'


def _next_actionable_session(now=None):
    """First session that completes at/after `now` — the earliest close at which
    a sweep exit can honestly fill (never backdate to an already-closed bar)."""
    from database.utils.trading_calendar import is_trading_day
    et = now or _eastern_now()
    d = et.date()
    if is_trading_day(d) and et.hour < MARKET_CLOSE_HOUR_ET:
        return d
    return _next_trading_day(d)


def _next_trading_day(d):
    from database.utils.trading_calendar import is_trading_day
    d = d + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def _trading_days(lo, hi):
    from database.utils.trading_calendar import is_trading_day
    out = []
    d = lo
    while d <= hi:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Core sync — stateful FORWARD ledger (preserves realized history; sweeps
# disqualified holdings on a version/strategy change; adopts new barriers).
# ---------------------------------------------------------------------------
def sync(now=None, send_notifications=True, verbose=False, dry_run=False):
    """Advance the persisted portfolio ledger to the latest completed session.

    Unlike a stateless replay, this NEVER rewrites realized history: closed
    trades + past equity stay as they happened. Each session it (1) resolves
    open positions under the CURRENT strategy barriers (adopt-new-barriers) via
    the validated compute_outcome path, (2) on a version/strategy change,
    re-qualifies open positions against the new scoring and SWEEPS (exits at
    the session close — i.e. market hours) any whose entry signal no longer
    clears the cascade threshold, and (3) opens new entries from that session's
    scores under the current version/strategy."""
    run = get_or_create_run()
    latest = _latest_price_date()
    if latest is None:
        return run
    D = last_completed_session(now=now, latest_data=latest)
    if D < run.start_date:
        return run  # not started yet
    res = _advance(run, D, send_notifications=send_notifications, verbose=verbose,
                   now=now, dry_run=dry_run)
    return res if dry_run else run


def _row_to_pos(p):
    """DB PortfolioPosition row → in-memory ledger dict."""
    return {
        'pos_key': p.pos_key, 'symbol': p.symbol, 'side': p.side, 'tier': p.tier,
        'entry_score': p.entry_score, 'entry_version_id': p.entry_version_id,
        'dte': int(p.dte or 30), 'routed_15dte': bool(p.routed_15dte),
        'strike': float(p.strike) if p.strike is not None else None,
        'expiration_date': p.expiration_date,
        'entry_date': p.entry_date, 'entry_underlying': float(p.entry_underlying or 0),
        'sigma_daily': float(p.sigma_daily or 0), 'premium_pct': float(p.premium_pct or 0),
        'premium_usd': float(p.premium_usd or 0), 'entry_equity_usd': float(p.entry_equity_usd or 0),
        'alloc_pct': p.alloc_pct, 'premium_mult': float(p.premium_mult or _mult_delta(int(p.dte or 30))[0]),
        'delta': float(p.delta or _mult_delta(int(p.dte or 30))[1]), 'stressed': bool(p.stressed),
        'dead_hold_active': bool(p.dead_hold_active), 'hold_bars': int(p.hold_bars or 0),
        'tp_price': float(p.tp_price) if p.tp_price is not None else None,
        'sl_price': float(p.sl_price) if p.sl_price is not None else None,
        'deadline': p.deadline, 'last_open_date': p.last_open_date,
        'notified_open': bool(p.notified_open), 'notified_close': bool(p.notified_close),
        'sweep_pending': bool(getattr(p, 'sweep_pending', False)),
        'sweep_reason': getattr(p, 'sweep_reason', None),
        'sweep_action_date': getattr(p, 'sweep_action_date', None),
        'status': 'open',
    }


def _index_of(rows, d):
    for i, r in enumerate(rows):
        if r.date == d:
            return i
    return None


def _close_on(rows, d):
    i = _index_of(rows, d)
    return float(rows[i].close) if i is not None else None


def _bars_between(rows, d0, d1):
    """Trading bars between entry day d0 and day d1 within rows."""
    i0 = _index_of(rows, d0)
    i1 = _index_of(rows, d1)
    if i0 is None or i1 is None:
        return 0
    return max(0, i1 - i0)


def _resolve_outcome(p, ph, cfg, bc):
    """Resolve a position's exit under the CURRENT cfg (adopt-new-barriers) via
    the validated compute_outcome. Returns the TradeOutcome (or None)."""
    rows = ph.get(p['symbol'])
    if not rows:
        return None
    outcome_cfg = bc._dte_router_15dte_call_cfg(cfg) if p['routed_15dte'] else cfg
    try:
        if p['side'] == 'put':
            return bc.compute_put_outcome(p['symbol'], p['entry_date'], float(p['entry_score']),
                                          rows, trend=None, cfg=outcome_cfg)
        return bc.compute_outcome(p['symbol'], p['entry_date'], float(p['entry_score']),
                                  rows, p['stressed'], trend=None, cfg=outcome_cfg)
    except Exception:
        return None


def _mark_pnl(p, d, ph):
    """option_pnl_pct mark for position p as of session d's close.

    Mirrors compute_outcome's honest-theta basis (2026-06-09 calendar-hold
    standardization): when CALENDAR_HOLD, theta decays over NOMINAL_CAL_DTE
    CALENDAR days with held = calendar days since entry; else trading bars.
    A same-session mark (entry day == d) is a real mark, not a hard 0 — the
    intrinsic leg still prices any close revision (the old `bars <= 0 → 0.0`
    short-circuit was why same-day sweep exits recorded 0% P/L).
    """
    rows = ph.get(p['symbol'])
    if not rows:
        return 0.0
    close_d = _close_on(rows, d)
    if close_d is None or _index_of(rows, p['entry_date']) is None:
        return 0.0
    sigma = p['sigma_daily']
    entry_u = p['entry_underlying']
    if not sigma or sigma <= 0 or not entry_u or entry_u <= 0:
        return 0.0
    import backtest_cascade as bc
    from option_pricing import option_pnl_pct
    if bc.CALENDAR_HOLD:
        held, total_dte = max(0, (d - p['entry_date']).days), int(p.get('dte') or bc.NOMINAL_CAL_DTE)
    else:
        held, total_dte = _bars_between(rows, p['entry_date'], d), p['dte']
    ppct = p['premium_mult'] * sigma / 100.0
    return option_pnl_pct(p['side'], close_d, entry_u, held,
                          premium_pct=ppct, total_dte=total_dte, delta=p['delta'])


# ---------------------------------------------------------------------------
# Sprint watchdog (P3.1 Phase 1, 2026-07-13) — pure user-protection
# instrumentation for the Apex sprint's stop-at-2x discipline (gameplan.md
# P3.1). The sprint's entire value is conditional on stopping near 2x, and
# the engine has never enforced that (known-issues.md 2026-06-17: held
# continuously past 2x the pre-elbow config is negative-compound, -37.1% 5y /
# 79.6% DD). This is Phase 1 ONLY: detect + halt + notify. NO auto-rotate, NO
# profile switch (Phase 3, user-locked) — a human still decides what to do
# once alerted; the mechanism just makes sure they're told, once, and that
# the book stops adding new risk in the meantime.
# ---------------------------------------------------------------------------
def _sprint_watchdog_check(run, mtm_equity, d):
    """Advance the sprint watchdog's state machine for session `d`. Only
    active while run.profile == 'apex' (Core/Sentinel are held compounders,
    not a stop-at-2x framing) — a complete no-op otherwise, and a complete
    no-op on any object (incl. the parity harness's bare SimpleNamespace
    fake run, profile='core' there) that lacks these attributes, via getattr
    defaults throughout.

    Three independently-purposed fields, deliberately NOT collapsed into one:
      sprint_start_equity / sprint_start_date — the baseline. Auto-captured
        the first time this sees profile=='apex' with no baseline yet (MTM
        equity as of THAT session). For the live run (already mid-sprint
        since the 2026-06-22 profile switch per portfolio-ops GUARD 1), this
        means the tracked baseline is "whenever this watchdog first ran",
        not the historical switch date — a deliberately conservative choice:
        it can only push the 2x milestone LATER than a backdated baseline
        would, never trigger a surprise/premature alert. Auditable via
        sprint_start_date; `trader portfolio sprint-arm` can re-baseline
        explicitly if a different start is wanted.
      sprint_2x_hit_date — a PERMANENT single-fire latch: the session date
        MTM equity first reached >= 2x the baseline. Once set it is NEVER
        reset except by `trader portfolio sprint-arm` (a fresh baseline for
        the NEXT doubling). This is what "send ONE push" actually means —
        one push per baseline, ever — and it is intentionally NOT the same
        field the user clears to resume entries (see below).
      halt_new_entries — the engine-respected entry gate (_open_entries_for_day
        returns [] while set). User-clearable via `trader portfolio
        sprint-clear`, independent of sprint_2x_hit_date, specifically so
        clearing it can never immediately re-trigger the same milestone on
        the very next sync (equity is still >= 2x baseline the instant after
        a manual clear; gating re-trigger on sprint_2x_hit_date instead of
        this flag is what makes "clearable" actually mean something).
    sprint_2x_notified (push-delivery confirmation) is set by _persist only
    once send_pushover reports 'sent' — deliberately decoupled from both
    fields above so a failed/unsent push keeps retrying on later syncs
    without re-latching halt_new_entries (already true) or re-arming
    anything.
    """
    if getattr(run, 'profile', None) != 'apex':
        return
    if getattr(run, 'sprint_start_equity', None) is None:
        run.sprint_start_equity = round(float(mtm_equity), 2)
        run.sprint_start_date = d
        return
    if getattr(run, 'sprint_2x_hit_date', None) is not None:
        return   # already latched for this baseline -- never re-fires
    base = float(run.sprint_start_equity)
    if base > 0 and float(mtm_equity) >= 2.0 * base:
        run.sprint_2x_hit_date = d
        run.halt_new_entries = True


def _format_sprint_2x_alert(run, current_equity):
    """The ONE push fired when the sprint watchdog latches (queued by
    _advance while sprint_2x_hit_date is set and sprint_2x_notified isn't —
    so this also re-forms on retry if the first send attempt failed)."""
    base = float(run.sprint_start_equity or 0)
    return {
        'kind': 'sprint_2x', 'ref': 'sprint_2x', 'date': run.sprint_2x_hit_date,
        'title': '\U0001F3AF Sprint hit 2x - new entries paused',
        'body': (f"Equity ${float(current_equity):,.0f} >= 2x baseline "
                 f"(${base:,.0f} on {run.sprint_start_date}). New entries are "
                 f"paused; exits/sells stay fully live. Resume with "
                 f"`trader portfolio sprint-clear`, or `sprint-arm` to track "
                 f"the next 2x from here."),
    }


def _advance(run, D, send_notifications=True, verbose=False, now=None, dry_run=False):
    import backtest_cascade as bc
    from database.models.core import AlgorithmVersion
    from database.models.portfolio import PortfolioPosition, PortfolioEquitySnapshot
    from database.utils.trading_calendar import is_trading_day

    et = now or _eastern_now()
    today = et.date()

    version = AlgorithmVersion.get_active_scores_version()
    import portfolio_profiles
    cfg, _profile = portfolio_profiles.apply_profile_to_config({}, run.profile)
    calls_only = all(float((cfg.get('tier_alloc') or {}).get(k, 0) or 0) == 0 for k in ('p<=15', 'p16-20', 'p21-25'))
    fp = _strategy_fingerprint(cfg, run.profile)
    min_thr = _min_call_threshold(cfg)
    start_usd = float(run.starting_capital_usd)
    first_run = run.last_processed_date is None

    version_changed = (not first_run) and run.version_id is not None and run.version_id != version.id
    strategy_changed = (not first_run) and run.strategy_fingerprint is not None and run.strategy_fingerprint != fp
    transition = version_changed or strategy_changed

    process_from = run.start_date if first_run else _next_trading_day(run.last_processed_date)
    process_days = _trading_days(process_from, D)
    if not process_days and not transition:
        # same/last session already processed and no change — refresh marks for D only.
        process_days = []

    # ---- in-memory ledger ----
    ledger = [_row_to_pos(p) for p in PortfolioPosition.select().where(
        (PortfolioPosition.run_id == run.id) & (PortfolioPosition.status == 'open'))]
    cash = start_usd if first_run else float(run.cash_usd if run.cash_usd is not None else start_usd)
    peak = start_usd if first_run else float(run.peak_equity_usd or start_usd)

    # Live (not-yet-completed) session: today's partial bar + provisional scores
    # exist once `trader update` has written them during market hours — the
    # pending-action alert pass below uses them so buy/sell pushes fire DURING
    # the session (and pre-open) instead of only after the 16:00 close.
    live_day = today if (today > D and is_trading_day(today)) else None

    syms = {p['symbol'] for p in ledger} | _qualifying_symbols(version.id, run.start_date, live_day or D)
    ph = bc.load_price_history(syms, run.start_date) if syms else {}

    # Earnings effective-date map for vega (IV-crush) on earnings-spanning trades —
    # threaded into cfg so compute_outcome matches the backtest. Added AFTER the
    # fingerprint so it can't perturb strategy-change detection.
    cfg = dict(cfg)
    if syms:
        from collections import defaultdict
        from database.models.core import EarningsDate
        from iv_crush_model import compute_effective_date
        ed_rows = list(EarningsDate
                       .select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                       .where((EarningsDate.symbol.in_(list(syms)))
                              & (EarningsDate.date >= run.start_date - timedelta(days=10))
                              & (EarningsDate.date <= D + timedelta(days=bc.HOLD_CALENDAR_DAYS + 10)))
                       .order_by(EarningsDate.symbol, EarningsDate.date)
                       .tuples())
        em = defaultdict(list)
        for _sym, _d, _ct in ed_rows:
            em[_sym].append(compute_effective_date(_d, _ct))
        cfg['ern_map'] = dict(em)

    breadth_enabled = cfg.get('breadth_alloc_enabled', bc.BREADTH_ALLOC_ENABLED)
    b_dates, b_map = bc.load_breadth_map(run.start_date)
    r_dates, r_map = bc.load_regime_map(run.start_date, breadth_enabled=breadth_enabled)
    vix_dates, vix_map = [], {}
    try:
        vix_dates, vix_map, _ = bc.load_router_market_maps(run.start_date, D)
    except Exception:
        pass
    # MWDD / TVDD / BDIV per-date maps — mirrors run_cascade_backtest: loaded once per
    # sync, only when the mechanism is enabled; a load failure degrades that mechanism
    # to a no-op (empty map → scale 1.0) instead of blocking the sync. (SVR is
    # per-signal — computed inline in _open_entries_for_day, no map.)
    mcc_dates, mcc_map = [], {}
    if _flag(cfg.get('mwdd_enabled', bc.MWDD_ENABLED)):
        try:
            mcc_dates, mcc_map = bc.load_mcclellan_map(run.start_date)
        except Exception:
            mcc_dates, mcc_map = [], {}
    trin_dates, trin_map = [], {}
    if _flag(cfg.get('tvdd_enabled', bc.TVDD_ENABLED)):
        try:
            trin_dates, trin_map = bc.load_trin_map(run.start_date)
        except Exception:
            trin_dates, trin_map = [], {}
    bdiv_dates, bdiv_map = [], {}
    if _flag(cfg.get('bdiv_enabled', bc.BDIV_ENABLED)):
        try:
            bdiv_dates, bdiv_map = bc.load_bdiv_map(run.start_date)
        except Exception:
            bdiv_dates, bdiv_map = [], {}
    # SPREAD_TILT per-signal component-spread map — same parquet source as
    # run_cascade_backtest (stamped onto TradeOutcome.spread at build time), keyed
    # by (symbol_str, date). Empty map (load failure / disabled) → scale 1.0.
    spread_tilt_map = {}
    if _flag(cfg.get('spread_tilt_enabled', getattr(bc, 'SPREAD_TILT_ENABLED', False))):
        try:
            spread_tilt_map = bc._spread_tilt_load()
        except Exception:
            spread_tilt_map = {}
    alloc_params = {k: cfg[k] for k in (
        'breadth_alloc_enabled', 'f3f_call_thresh', 'f3f_call_floor', 'f3f_call_low',
        'f3f_put_thresh', 'f3f_put_floor', 'f3f_put_high') if k in cfg}

    # resolve each open position's exit under current cfg (adopt-new-barriers)
    for p in ledger:
        p['_outcome'] = _resolve_outcome(p, ph, cfg, bc)

    # ---- re-qualification on a version/strategy change ----
    # A holding is kept when the NEW scoring still qualifies it (entry-date
    # score OR latest completed-session score >= cascade threshold — "if the
    # new strat would hold it, don't sell"). Affirmatively disqualified
    # holdings are marked sweep_pending and exit at the close of the FIRST
    # session that completes AFTER detection (never backdated to an
    # already-closed bar, which recorded 0% P/L on same-day entries). Missing
    # score rows (recalc backfill in flight) DEFER the decision: the stored
    # version/fingerprint stays untouched so the transition re-fires on every
    # sync until each holding is resolvable — never exit on missing data.
    pending_requal = False
    rescued = []
    if transition:
        action_d = _next_actionable_session(et)
        sweep_reason = 'version_sweep' if version_changed else 'strategy_sweep'
        for p in ledger:
            verdict = _requalify_position(version.id, p, min_thr, D)
            if verdict == 'unknown':
                pending_requal = True
            if verdict == 'sweep' and not p.get('sweep_pending'):
                p['sweep_pending'] = True
                p['sweep_reason'] = sweep_reason
                p['sweep_action_date'] = action_d
            elif verdict == 'hold' and p.get('sweep_pending'):
                # scores backfilled and the holding re-qualified before its
                # exit was actioned — rescind the sweep.
                p['sweep_pending'] = False
                p['sweep_reason'] = None
                p['sweep_action_date'] = None
                rescued.append(p)
        if verbose and (pending_requal or rescued):
            print(f"  requalification: pending={pending_requal} rescued={[p['symbol'] for p in rescued]}")

    def _equity_mtm(d):
        return cash + sum(p['premium_usd'] * (1.0 + _mark_pnl(p, d, ph)) for p in ledger)

    closed_out = []
    new_open = []
    snaps = []

    for d in process_days:
        # 1. resolve exits landing on d (barrier exits take precedence over a
        #    same-day due sweep — the touch happens intraday, before the close
        #    the sweep would fill at)
        still = []
        for p in ledger:
            oc = p.get('_outcome')
            ed = oc.exit_date if (oc is not None and oc.outcome != 'open') else None
            clamp_floor = p.get('last_open_date') or p['entry_date']
            forced_now = False
            if ed is not None and ed < clamp_floor:
                ed, forced_now = D, True  # new barrier would have exited in the past → exit now
            sweep_due = (p.get('sweep_pending') and p.get('sweep_action_date') is not None
                         and p['sweep_action_date'] <= d)
            if ed is not None and ed == d:
                pnl = _mark_pnl(p, d, ph) if forced_now else float(oc.net_return)
                cash += p['premium_usd'] * (1.0 + pnl)
                p.update(status='closed', exit_date=d, exit_reason=oc.outcome, mark_date=d,
                         pnl_pct=pnl, pnl_usd=p['premium_usd'] * pnl,
                         current_value_usd=p['premium_usd'] * (1.0 + pnl),
                         current_underlying=getattr(oc, 'exit_price', None))
                closed_out.append(p)
            elif sweep_due:
                # re-qualification exit at this session's close — honest mark
                # P/L (intrinsic + theta), never a hard 0.
                pnl = _mark_pnl(p, d, ph)
                cash += p['premium_usd'] * (1.0 + pnl)
                p.update(status='closed', exit_date=d, mark_date=d,
                         exit_reason=p.get('sweep_reason') or 'strategy_sweep',
                         pnl_pct=pnl, pnl_usd=p['premium_usd'] * pnl,
                         current_value_usd=p['premium_usd'] * (1.0 + pnl),
                         current_underlying=_close_on(ph.get(p['symbol']), d))
                closed_out.append(p)
            else:
                still.append(p)
        ledger = still

        # 2. sprint watchdog (P3.1 Phase 1) -- checked BEFORE this session's own
        #    entries open so a same-day crossing also gates same-day entries.
        #    Uses MTM equity (matches PortfolioRun.equity_usd's own "latest MTM
        #    total" semantics) computed fresh here; deliberately NOT reused for
        #    the snaps.append(...) MTM below, which must keep including today's
        #    new entries byte-identically for parity (see that line).
        _sprint_watchdog_check(run, _equity_mtm(d), d)

        # 3. open new entries for session d (cost-basis equity drives sizing/DD,
        #    matching run_backtest; snapshots below use honest MTM)
        eq_cost = cash + sum(p['premium_usd'] for p in ledger)
        if eq_cost > peak:
            peak = eq_cost
        running_dd = (1.0 - eq_cost / peak) if peak > 0 else 0.0
        # Two independent gates OR'd together: the P3.1 sprint watchdog
        # (automatic, Apex-only, latched by _sprint_watchdog_check above) and
        # the P3.8 incident kill-switch (manual, any-profile, `trader
        # portfolio pause`/`resume` -- see .claude/docs/incident-runbook.md).
        # Either reason blocks new entries; clearing one never touches the
        # other (sprint-clear vs resume are deliberately separate commands).
        halt_new = bool((getattr(run, 'profile', None) == 'apex'
                         and getattr(run, 'halt_new_entries', False))
                        or getattr(run, 'sync_paused', False))
        for npos in _open_entries_for_day(d, version.id, calls_only, cfg, ledger, cash, eq_cost,
                                          running_dd, ph, b_dates, b_map, r_dates, r_map,
                                          vix_dates, vix_map, mcc_dates, mcc_map,
                                          trin_dates, trin_map, bdiv_dates, bdiv_map,
                                          alloc_params, breadth_enabled, bc, spread_tilt_map,
                                          halt_new_entries=halt_new):
            cash -= npos['premium_usd']
            npos['_outcome'] = _resolve_outcome(npos, ph, cfg, bc)
            npos['last_open_date'] = d
            ledger.append(npos)
            new_open.append(npos)

        snaps.append((d, _equity_mtm(d)))

    # final marks for still-open positions at D
    for p in ledger:
        p['last_open_date'] = D
        p['mark_date'] = D
        mpnl = _mark_pnl(p, D, ph)
        p['pnl_pct'] = mpnl
        p['pnl_usd'] = p['premium_usd'] * mpnl
        p['current_value_usd'] = p['premium_usd'] * (1.0 + mpnl)
        p['current_underlying'] = _close_on(ph.get(p['symbol']), D)
        p['hold_bars'] = _bars_between(ph.get(p['symbol']) or [], p['entry_date'], D)

    # live pending-action alerts (intraday / pre-open buy+sell pushes) — fire
    # ahead of the close-of-session actioning so the user can act DURING the
    # session (the old flow only ever notified after the 16:00 close).
    alerts, pending_meta = [], {}
    if (send_notifications or dry_run) and today >= run.go_live_date:
        try:
            alerts, pending_meta = _pending_actions(
                run, ledger, version.id, calls_only, cfg, ph,
                b_dates, b_map, r_dates, r_map, vix_dates, vix_map,
                mcc_dates, mcc_map, trin_dates, trin_map,
                bdiv_dates, bdiv_map,
                alloc_params, breadth_enabled, cash, peak, D,
                live_day, et, bc, spread_tilt_map)
        except Exception as e:
            if verbose or dry_run:
                print(f"  pending-action pass skipped: {e}")
            alerts, pending_meta = [], {}

    # sprint watchdog push (P3.1 Phase 1): queue the ONE alert, retried on
    # every sync until delivery is confirmed (_persist flips sprint_2x_notified
    # only once send_pushover actually reports 'sent'). Independent of the
    # entry-gate itself, which already latched inside the day-loop above
    # regardless of notification outcome -- so a failed send never reopens
    # entries and never blocks the halt from taking effect.
    if (getattr(run, 'sprint_2x_hit_date', None) is not None
            and not getattr(run, 'sprint_2x_notified', False)):
        alerts.append(_format_sprint_2x_alert(run, _equity_mtm(D)))

    if dry_run:
        print(f"[dry-run] D={D} live_day={live_day} transition={transition} "
              f"pending_requal={pending_requal} process_days={[x.isoformat() for x in process_days]}")
        for p in closed_out:
            print(f"[dry-run] close  {p['symbol']:8} {p.get('exit_reason'):14} on {p.get('exit_date')} "
                  f"pnl={float(p.get('pnl_pct') or 0) * 100:+.1f}%")
        for p in new_open:
            print(f"[dry-run] open   {p['symbol']:8} score={p['entry_score']} "
                  f"${float(p['premium_usd']):,.0f} on {p['entry_date']}")
        for p in ledger:
            if p.get('sweep_pending'):
                print(f"[dry-run] sweep_pending {p['symbol']:8} ({p.get('sweep_reason')}) "
                      f"exits at close of {p.get('sweep_action_date')}")
        for p in rescued:
            print(f"[dry-run] rescued {p['symbol']} — sweep rescinded")
        if getattr(run, 'profile', None) == 'apex':
            print(f"[dry-run] sprint: baseline={getattr(run, 'sprint_start_equity', None)} "
                  f"halt_new_entries={bool(getattr(run, 'halt_new_entries', False))} "
                  f"2x_hit={getattr(run, 'sprint_2x_hit_date', None)} "
                  f"notified={bool(getattr(run, 'sprint_2x_notified', False))}")
        print(f"[dry-run] sync_paused={bool(getattr(run, 'sync_paused', False))}"
              + (" -- BUY pushes suppressed, new entries halted; exits/sweeps/alerts unaffected"
                 if getattr(run, 'sync_paused', False) else ""))
        if not alerts:
            print("[dry-run] no pending alerts")
        for a in alerts:
            body = (a['body'] or '').replace('\n', ' / ')
            print(f"[dry-run] ALERT {a['kind']:12} {a['ref']:24} {a['title']} | {body}")
        return {
            'D': D.isoformat(), 'pending': pending_meta,
            'alerts': [{'kind': a['kind'], 'ref': a['ref'], 'title': a['title'],
                        'body': a['body']} for a in alerts],
        }

    _persist(run, ledger, closed_out, new_open, snaps, cash, peak, D, version, fp,
             send_notifications, pending_requal=pending_requal, alerts=alerts,
             rescued=rescued, process_days=process_days)


def _qualifying_symbols(version_id, lo, hi):
    """Symbols with any call score >= MIN_SCORE in [lo, hi] for the version."""
    from database.models.core import Score, AlgorithmVersion
    try:
        ver = AlgorithmVersion.get(AlgorithmVersion.id == version_id)
    except Exception:
        return set()
    q = (Score.select(Score.symbol)
         .where((Score.version == ver) & (Score.overall >= MIN_SCORE)
                & (Score.date >= lo) & (Score.date <= hi))
         .distinct())
    return {r.symbol for r in q}


def _open_entries_for_day(d, version_id, calls_only, cfg, ledger, cash, equity, running_dd,
                          ph, b_dates, b_map, r_dates, r_map, vix_dates, vix_map,
                          mcc_dates, mcc_map, trin_dates, trin_map, bdiv_dates, bdiv_map,
                          alloc_params, breadth_enabled, bc, spread_tilt_map=None,
                          halt_new_entries=False):
    """Port of run_backtest's per-day cascade entry loop, operating on the
    persisted ledger state. Returns a list of newly-opened position dicts.

    halt_new_entries: sprint watchdog (P3.1 Phase 1) -- True once the run's
    MTM equity has crossed 2x its tracked sprint baseline. No new entries
    open while set (returns [] immediately); everything else in the engine
    (exits, sweeps, dead-hold, alerts) is entirely unaffected -- this is the
    ONLY thing the watchdog gates, and it never touches EXISTING positions.
    """
    if halt_new_entries:
        return []
    from dte_router import value_on_or_before
    # LIQUIDITY_FLOOR: bc.load_signals() applies the SAME admission filter as
    # backtest_cascade (bit-exact, same function) -- cfg=cfg lets a profile
    # override reach it here too. At 0.0 (default) it's a no-op; for live
    # dates past the map's coverage the (symbol,date) lookup misses and the
    # filter passes the signal through unfiltered (coverage-gated, same as MC).
    sigs = list(bc.load_signals(version_id, MIN_SCORE, from_date=d, to_date=d, cfg=cfg))
    if not sigs:
        return []

    def _csort(s):
        trend = float(s.trend) if getattr(s, 'trend', None) is not None else None
        # CT priority mirrors run_cascade_backtest._sort_key: CT-tagged calls fill
        # AHEAD of higher-score plain signals ONLY below 95 (a 95+ already sorts
        # first by score; bc gives it ct_priority=1).
        ct = 0 if (bc.ct_tag(int(s.overall), trend, 'call') and int(s.overall) < 95) else 1
        return (ct, -int(s.overall), s.symbol)
    sigs.sort(key=_csort)

    open_syms = {p['symbol'] for p in ledger}
    call_open = sum(1 for p in ledger if p['side'] == 'call')
    call_pressure = sum(1 for s in sigs if s.symbol not in open_syms)

    tier_alloc = cfg.get('tier_alloc', bc.TIER_ALLOC)
    max_pos = cfg.get('max_positions', bc.MAX_POSITIONS)
    max_call = cfg.get('max_positions_call', bc.MAX_POSITIONS_CALL)
    practical_enabled = bool(cfg.get('practical_exposure_enabled', bc.PRACTICAL_EXPOSURE_ENABLED))
    ceiling = float(cfg.get('practical_capital_ceiling', bc.PRACTICAL_CAPITAL_CEILING) or 0.0)
    gross_cap = float(cfg.get('gross_premium_cap', bc.GROSS_PREMIUM_CAP) or 0.0)
    call_cap = float(cfg.get('call_premium_cap', bc.CALL_PREMIUM_CAP) or 0.0)
    opp_ref = float(cfg.get('opp_sat_call_ref', bc.OPP_SAT_CALL_REF) or 0.0)
    opp_pow = float(cfg.get('opp_sat_power', bc.OPP_SAT_POWER) or 0.0)
    opp_floor = float(cfg.get('opp_sat_floor', bc.OPP_SAT_FLOOR) or 0.0)
    dd_lo = float(cfg.get('dd_soft_band_lo', bc.DD_SOFT_BAND_LO) or 0.0)
    dd_hi = float(cfg.get('dd_soft_band_hi', bc.DD_SOFT_BAND_HI) or 0.0)
    dd_floor = float(cfg.get('dd_soft_call_floor', bc.DD_SOFT_CALL_FLOOR) or 1.0)
    rxdd_on = cfg.get('rxdd_enabled', bc.RXDD_ENABLED)
    if isinstance(rxdd_on, str):
        rxdd_on = rxdd_on.lower() in ('1', 'true', 'yes')
    svr_on = _flag(cfg.get('svr_enabled', bc.SVR_ENABLED))
    mwdd_on = _flag(cfg.get('mwdd_enabled', bc.MWDD_ENABLED))
    tvdd_on = _flag(cfg.get('tvdd_enabled', bc.TVDD_ENABLED))
    bdiv_on = _flag(cfg.get('bdiv_enabled', bc.BDIV_ENABLED))
    spread_tilt_on = _flag(cfg.get('spread_tilt_enabled', getattr(bc, 'SPREAD_TILT_ENABLED', False)))
    if spread_tilt_map is None:
        spread_tilt_map = {}

    base_value = min(equity, ceiling) if (practical_enabled and ceiling > 0) else equity
    open_premium = sum(p['premium_usd'] for p in ledger)
    call_premium = sum(p['premium_usd'] for p in ledger if p['side'] == 'call')

    def _sat_scale():
        if not practical_enabled or opp_ref <= 0 or call_pressure <= opp_ref:
            return 1.0
        return max(opp_floor, min(1.0, (opp_ref / max(1.0, float(call_pressure))) ** max(0.0, opp_pow)))

    reg_val = bc.regime_on_or_before(r_dates, r_map, d, breadth_enabled=breadth_enabled)
    reg_scale = bc.alloc_scale_for(reg_val, is_put=False, params=alloc_params)
    dd_scale = 1.0
    if dd_hi > dd_lo and running_dd > dd_lo:
        dd_scale = dd_floor if running_dd >= dd_hi else 1.0 - (running_dd - dd_lo) / (dd_hi - dd_lo) * (1.0 - dd_floor)
    rxdd_scale = 1.0
    if rxdd_on:
        rxdd_scale = bc._rxdd_call_scale(running_dd, value_on_or_before(vix_dates, vix_map, d),
                                         rxdd_on, float(cfg.get('rxdd_vix_c', bc.RXDD_VIX_C) or 0.0),
                                         float(cfg.get('rxdd_vix_w', bc.RXDD_VIX_W) or 0.0),
                                         float(cfg.get('rxdd_depth', bc.RXDD_DEPTH) or 0.0),
                                         float(cfg.get('rxdd_dd_min', bc.RXDD_DD_MIN) or 0.0))
    # MWDD / TVDD / BDIV — Stage-3 call-alloc dampeners, mirror of run_backtest (this
    # loop only opens calls, so the calls-only gate is implicit). Per-date lookups are
    # constant within a session → computed once here.
    mwdd_scale = 1.0
    if mwdd_on:
        mwdd_scale = bc._mwdd_call_scale(
            running_dd,
            value_on_or_before(mcc_dates, mcc_map, d) if mcc_dates else None,
            value_on_or_before(vix_dates, vix_map, d) if vix_dates else None,
            mwdd_on,
            float(cfg.get('mwdd_mcc_c', bc.MWDD_MCC_C) or 0.0),
            float(cfg.get('mwdd_mcc_w', bc.MWDD_MCC_W) or 0.0),
            float(cfg.get('mwdd_depth', bc.MWDD_DEPTH) or 0.0),
            float(cfg.get('mwdd_dd_min', bc.MWDD_DD_MIN) or 0.0),
            float(cfg.get('mwdd_vix_panic', bc.MWDD_VIX_PANIC) or 0.0))
    tvdd_scale = 1.0
    if tvdd_on:
        tvdd_scale = bc._tvdd_call_scale(
            running_dd,
            value_on_or_before(trin_dates, trin_map, d) if trin_dates else None,
            value_on_or_before(vix_dates, vix_map, d) if vix_dates else None,
            tvdd_on,
            float(cfg.get('tvdd_trin_c', bc.TVDD_TRIN_C) or 0.0),
            float(cfg.get('tvdd_trin_w', bc.TVDD_TRIN_W) or 0.0),
            float(cfg.get('tvdd_depth', bc.TVDD_DEPTH) or 0.0),
            float(cfg.get('tvdd_dd_min', bc.TVDD_DD_MIN) or 0.0),
            float(cfg.get('tvdd_vix_panic', bc.TVDD_VIX_PANIC) or 0.0))
    bdiv_scale = 1.0
    if bdiv_on:
        bdiv_scale = bc._bdiv_call_scale(
            value_on_or_before(bdiv_dates, bdiv_map, d) if bdiv_dates else None,
            bdiv_on,
            float(cfg.get('bdiv_prox_cut', bc.BDIV_PROX_CUT) or 0.0),
            float(cfg.get('bdiv_prox_full', bc.BDIV_PROX_FULL) or 0.0),
            float(cfg.get('bdiv_gap_c', bc.BDIV_GAP_C) or 0.0),
            float(cfg.get('bdiv_gap_w', bc.BDIV_GAP_W) or 0.0),
            float(cfg.get('bdiv_depth', bc.BDIV_DEPTH) or 0.0))
    # SVR is per-signal (computed inside the loop); params resolved once here.
    svr_params = (float(cfg.get('svr_lo_cut', bc.SVR_LO_CUT) or 0.0),
                  float(cfg.get('svr_lo_full', bc.SVR_LO_FULL) or 0.0),
                  float(cfg.get('svr_hi_full', bc.SVR_HI_FULL) or 0.0),
                  float(cfg.get('svr_hi_cut', bc.SVR_HI_CUT) or 0.0),
                  float(cfg.get('svr_floor', bc.SVR_FLOOR) or 1.0))
    # SPREAD_TILT is per-signal (score + the per-(symbol,date) component spread,
    # looked up from spread_tilt_map = the same parquet backtest_cascade stamps
    # onto TradeOutcome.spread); params resolved once here.
    spread_tilt_params = (float(cfg.get('spread_tilt_lo', getattr(bc, 'SPREAD_TILT_LO', 26.9)) or 0.0),
                          float(cfg.get('spread_tilt_hi', getattr(bc, 'SPREAD_TILT_HI', 31.3)) or 0.0),
                          float(cfg.get('spread_tilt_depth', getattr(bc, 'SPREAD_TILT_DEPTH', 0.40)) or 0.0))
    sat_scale = _sat_scale()
    nominal_cal_dte = int(cfg.get('nominal_cal_dte', 30) or 30)   # profile tenor (Apex elbow = 15)

    routed_today = 0
    opened = []
    for s in sigs:
        if s.symbol in open_syms:
            continue
        if max_call is not None and call_open >= max_call:
            continue
        if len(ledger) + len(opened) >= max_pos:
            break
        rows = ph.get(s.symbol)
        if not rows:
            continue
        sig_i = _index_of(rows, d)
        if sig_i is None:
            continue
        entry = float(rows[sig_i].close)
        if entry <= 0:
            continue
        closes = [float(rows[j].close) for j in range(max(0, sig_i - bc.VOL_BARS), sig_i + 1)]
        sigma = bc.realized_vol_pct(closes)
        if not sigma or sigma <= 0:
            continue
        trend = float(s.trend) if getattr(s, 'trend', None) is not None else None
        routed = bc._dte_router_call_eligible(s, vix_dates, vix_map, vix_map, routed_today)
        dte = bc.DTE_ROUTER_TARGET_DTE if routed else nominal_cal_dte
        pm, dl = _mult_delta(dte)
        pm = float(cfg.get('premium_mult', pm))   # honest-calendar premium (profile-derived)
        score = int(s.overall)
        tier = bc.CT_CALL_TIER if bc.ct_tag(score, trend, 'call') else bc.score_to_tier(score)
        alloc_frac = tier_alloc.get(tier, bc.TIER_ALLOC.get(tier, 0.0))
        # SVR: per-signal semivol_r band-pass — same compute_semivol_r(closes, idx) the
        # backtest stamps onto TradeOutcome.semivol_r at build time (identical rows from
        # load_price_history → identical value).
        svr_scale = 1.0
        if svr_on:
            svr_scale = bc._svr_call_scale(
                bc.compute_semivol_r([float(r.close) for r in rows], sig_i),
                svr_on, *svr_params)
        # SPREAD_TILT: per-signal 75-79-band component-disagreement haircut. spread is
        # the (symbol, date) value from spread_tilt_map — the same parquet run_backtest
        # stamps onto TradeOutcome.spread → identical scale by construction.
        spread_tilt_scale = 1.0
        if spread_tilt_on:
            spread_tilt_scale = bc._spread_tilt_call_scale(
                score, spread_tilt_map.get((s.symbol, rows[sig_i].date)),
                spread_tilt_on, *spread_tilt_params)
        # Multiplication order mirrors run_backtest (saw_scale, puts-only, elided as
        # exactly 1.0 on this calls-only path).
        base_premium = (alloc_frac * reg_scale * dd_scale * sat_scale * rxdd_scale
                        * svr_scale * mwdd_scale * tvdd_scale * bdiv_scale * spread_tilt_scale * base_value)
        if practical_enabled and (gross_cap > 0 or call_cap > 0):
            remaining = float('inf')
            if gross_cap > 0:
                remaining = min(remaining, base_value * gross_cap - (open_premium + sum(o['premium_usd'] for o in opened)))
            if call_cap > 0:
                remaining = min(remaining, base_value * call_cap - (call_premium + sum(o['premium_usd'] for o in opened)))
            if remaining <= 0:
                continue
            base_premium = min(base_premium, remaining)
        premium = base_premium
        if cash - sum(o['premium_usd'] for o in opened) < premium or premium < 10.0:
            continue
        stressed = bc.is_stressed(b_dates, b_map, d)
        entry_date = rows[sig_i].date
        if routed:
            routed_today += 1
        opened.append({
            'pos_key': f"{0}:{s.symbol}:{entry_date.isoformat()}:call",  # run id patched in _persist
            'symbol': s.symbol, 'side': 'call', 'tier': tier, 'entry_score': score,
            'entry_version_id': version_id, 'dte': dte, 'routed_15dte': bool(routed),
            'strike': nearest_strike(entry), 'expiration_date': expiration_for(entry_date, dte),
            'entry_date': entry_date, 'entry_underlying': entry, 'sigma_daily': sigma,
            'premium_pct': pm * sigma / 100.0, 'premium_usd': round(premium, 2),
            'entry_equity_usd': round(equity, 2),
            'alloc_pct': (premium / equity * 100.0) if equity > 0 else None,
            'premium_mult': pm, 'delta': dl, 'stressed': stressed, 'dead_hold_active': False,
            'tp_price': entry * (1.0 + float(cfg.get('tp_sigma_base', bc._cfg.TP_SIGMA_BASE)) * sigma / 100.0),
            'sl_price': entry * (1.0 - float(cfg.get('sl_sigma_base', bc._cfg.SL_SIGMA_BASE)) * sigma / 100.0),
            'deadline': entry_date + timedelta(days=int(cfg.get('hold_calendar_days', bc.HOLD_CALENDAR_DAYS))),
            'last_open_date': d, 'status': 'open',
        })
        open_syms.add(s.symbol)
        call_open += 1
    return opened


def _pending_actions(run, ledger, version_id, calls_only, cfg, ph,
                     b_dates, b_map, r_dates, r_map, vix_dates, vix_map,
                     mcc_dates, mcc_map, trin_dates, trin_map, bdiv_dates, bdiv_map,
                     alloc_params, breadth_enabled, cash, peak, D, live_day, et, bc,
                     spread_tilt_map=None):
    """Live action alerts, evaluated on EVERY update run so buy/sell pushes
    fire during market hours (and pre-open), not only after the 16:00 close.

    Certain / near-certain SELLS (one push per position, deduped for life):
      - sweep_pending holdings (version/strategy change) — any time of day;
      - positions whose CURRENT barriers already fired on today's partial bar
        (TP touch / SL / dead-hold popout / deadline): a touch on the live
        high/low can only persist into the completed bar, so the alert is safe;
      - hard-sell deadline reached (knowable pre-open, before any today-bar).
    PROVISIONAL BUYS: today's provisional scores run through the same cascade
    entry path the close-of-session actioning uses. Buy ALERTS fire only inside
    the BUY_ALERT_FROM_ET..close execution window (entry-timing canon: morning
    scores fade 26% of the time; the ~15:45 read is 92% close-faithful); if the
    signal fades by the close, the close run pushes a cancel notice instead of
    opening. The would-open list is still COMPUTED on every live run so the
    Allocator can render forming entries as provisional.
    MORNING DIGEST: one push listing carry-over buys from the last completed
    session — the ledger filled them at yesterday's close, so the user's first
    executable moment is this open (the 9:00 pre-open update surfaces them).
    Open entry runs ~1.3pp WR below the model's close fill (haircut, still +EV).

    Returns (alert dicts, pending-state meta); _persist sends the alerts and
    records the dedup rows; the meta feeds /api/portfolio/pending.
    """
    from database.models.portfolio import PortfolioPendingAlert

    today = et.date()
    market_open_now = (live_day is not None
                       and (et.hour, et.minute) >= MARKET_OPEN_ET
                       and et.hour < MARKET_CLOSE_HOUR_ET)
    in_buy_window = (market_open_now and (et.hour, et.minute) >= BUY_ALERT_FROM_ET)
    # pre-open pushes (digest + sweep/deadline sells) only from 08:30 ET so they
    # arrive when actionable; nothing pushes overnight or post-close — the
    # ledger's own open/close notifications via _persist are unaffected.
    pre_open_ok = (live_day is not None and not market_open_now
                   and MORNING_ALERT_FROM_ET <= (et.hour, et.minute) < MARKET_OPEN_ET)
    push_ok = market_open_now or pre_open_ok
    when = 'now' if market_open_now else 'at the open'
    meta = {'live_day': live_day.isoformat() if live_day else None,
            'in_buy_window': in_buy_window, 'market_open_now': market_open_now,
            'push_ok': push_ok,
            'would_open': [], 'would_exit': [], 'carryover': []}

    def _already(kind, ref, on_date=None):
        q = (PortfolioPendingAlert.select()
             .where((PortfolioPendingAlert.run_id == run.id)
                    & (PortfolioPendingAlert.kind == kind)
                    & (PortfolioPendingAlert.ref == ref)))
        if on_date is not None:
            q = q.where(PortfolioPendingAlert.date == on_date)
        return q.exists()

    alerts = []

    # -- sells (built first: a position being sold must not appear as a
    #    carry-over buy in the digest below) --
    exiting = set()
    for p in ledger:
        reason, est = None, None
        if p.get('sweep_pending'):
            reason = p.get('sweep_reason') or 'strategy_sweep'
            est = _mark_pnl(p, today, ph) or _mark_pnl(p, D, ph)
        elif live_day is not None:
            oc = p.get('_outcome')
            if (oc is not None and oc.outcome != 'open'
                    and oc.exit_date is not None and oc.exit_date <= today):
                reason = oc.outcome
                est = float(oc.net_return)
            elif p.get('deadline') and p['deadline'] <= today:
                reason = 'hard'
                est = _mark_pnl(p, today, ph) or _mark_pnl(p, D, ph)
        if reason is None:
            continue
        exiting.add(p['pos_key'])
        side_txt, strike, dte = _pos_bits(p)
        meta['would_exit'].append({
            'symbol': p['symbol'], 'side': p.get('side'), 'strike': p.get('strike'),
            'dte': int(p.get('dte') or 30), 'reason': _REASON_LABEL.get(reason, reason),
            'est_pnl_pct': (est * 100.0) if est is not None else None,
            'premium_usd': float(p['premium_usd']),
        })
        if not push_ok or _already('exit', p['pos_key']):
            continue
        title, body = format_sell_alert(p, reason, est, when)
        alerts.append({'kind': 'exit', 'ref': p['pos_key'], 'date': today,
                       'title': title, 'body': body})

    # -- morning digest: positions entered at the last completed session (the
    #    ledger filled them at that close; the user's first executable moment
    #    is this open). Skips positions already exiting and intraday buys the
    #    user was already alerted to during that session.
    if live_day is not None:
        carry = [p for p in ledger
                 if p['entry_date'] == D and p['pos_key'] not in exiting
                 and not _already('entry', p['symbol'], D)]
        for p in carry:
            meta['carryover'].append({
                'symbol': p['symbol'], 'side': p.get('side'), 'strike': p.get('strike'),
                'dte': int(p.get('dte') or 30), 'entry_score': p.get('entry_score'),
                'premium_usd': float(p['premium_usd']), 'entry_date': p['entry_date'].isoformat(),
            })
        if carry and push_ok and not _already('digest', 'open', today):
            lines = []
            for p in sorted(carry, key=lambda x: -float(x['premium_usd'])):
                side_txt, strike, dte = _pos_bits(p)
                lines.append(f"Buy {p['symbol']} ${float(p['premium_usd']):,.0f} — "
                             f"{p['entry_score']} · {dte} · {strike} {side_txt}")
            n = len(carry)
            lines.append("(model filled at yesterday's close; open entry avgs ~-1.3pp)")
            alerts.insert(0, {'kind': 'digest', 'ref': 'open', 'date': today,
                              'title': f"\U0001F514 {n} buy{'s' if n != 1 else ''} {when}",
                              'body': '\n'.join(lines)})

    # -- provisional buys (today's session live; needs today's scores + bar).
    #    The would-open list is computed on every live run (the Allocator
    #    renders it), but buy PUSHES fire only inside the execution window —
    #    the entry-timing canon: morning scores are partial-day reads that fade
    #    26% of the time; the ~15:45 read holds to the close 92% of the time. --
    # Two independent halt reasons, exposed separately in `meta` for
    # diagnosability but OR'd together for the actual gate: the P3.1 sprint
    # watchdog (automatic, Apex-only) and the P3.8 incident kill-switch
    # (manual, any-profile, `trader portfolio pause`/`resume`). Gating
    # would_open itself (not just the alert) keeps the preview honest -- it
    # never shows a candidate that could not actually fill right now.
    sprint_halt = bool(getattr(run, 'profile', None) == 'apex'
                       and getattr(run, 'halt_new_entries', False))
    paused = bool(getattr(run, 'sync_paused', False))
    new_entries_halted = sprint_halt or paused
    meta['sprint_halt_new_entries'] = sprint_halt
    meta['sync_paused'] = paused
    meta['new_entries_halted'] = new_entries_halted
    if live_day is not None:
        eq_cost = cash + sum(p['premium_usd'] for p in ledger)
        pk = max(peak, eq_cost)
        running_dd = (1.0 - eq_cost / pk) if pk > 0 else 0.0
        would_open = _open_entries_for_day(
            today, version_id, calls_only, cfg, ledger, cash, eq_cost,
            running_dd, ph, b_dates, b_map, r_dates, r_map,
            vix_dates, vix_map, mcc_dates, mcc_map, trin_dates, trin_map,
            bdiv_dates, bdiv_map, alloc_params, breadth_enabled, bc, spread_tilt_map,
            halt_new_entries=new_entries_halted)
        for npos in would_open:
            meta['would_open'].append({
                'symbol': npos['symbol'], 'side': npos.get('side'),
                'strike': npos.get('strike'), 'dte': int(npos.get('dte') or 30),
                'entry_score': npos.get('entry_score'), 'tier': npos.get('tier'),
                'premium_usd': float(npos['premium_usd']),
                'alloc_pct': npos.get('alloc_pct'),
            })
            if not in_buy_window or _already('entry', npos['symbol'], today):
                continue
            title, body = format_buy_alert(npos, when)
            alerts.append({'kind': 'entry', 'ref': npos['symbol'], 'date': today,
                           'title': title, 'body': body})

    # P3.8 kill-switch: suppress BUY-flavored pushes outright while paused --
    # covers the morning digest above too (a carry-over fill from BEFORE the
    # pause took effect, unrelated to the would_open gate). Exits/sweeps/sell
    # alerts built earlier in this function are never touched.
    if paused:
        alerts = [a for a in alerts if a['kind'] not in ('entry', 'digest')]

    return alerts, meta


def _record_alert(run_id, kind, ref, on_date, payload=None):
    from database.models.portfolio import PortfolioPendingAlert
    try:
        PortfolioPendingAlert.create(run_id=run_id, kind=kind, ref=ref,
                                     date=on_date, payload=payload)
    except Exception:
        pass  # unique-index race — already recorded


def _persist(run, ledger, closed_out, new_open, snaps, cash, peak, D, version, fp,
             send_notifications, pending_requal=False, alerts=None, rescued=None,
             process_days=None):
    from database.models.portfolio import (
        PortfolioPosition, PortfolioEquitySnapshot, PortfolioPendingAlert,
    )
    now = datetime.now()
    go_live = run.go_live_date
    alerts = list(alerts or [])
    rescued = rescued or []
    process_days = process_days or []

    # Rescinded sweeps: drop the lifetime 'exit' dedup row (a future genuine
    # exit must be able to alert) and push a keep-notice if a sell was alerted.
    for p in rescued:
        had = (PortfolioPendingAlert.delete()
               .where((PortfolioPendingAlert.run_id == run.id)
                      & (PortfolioPendingAlert.kind == 'exit')
                      & (PortfolioPendingAlert.ref == p['pos_key']))
               .execute())
        if had:
            alerts.append({'kind': 'exit_cancel', 'ref': p['pos_key'], 'date': now.date(),
                           'title': f"✅ Keep {p['symbol']}",
                           'body': 'Re-qualified under the new scoring — the sell is off.'})

    # Live-alert reconciliation: positions the user was already told to
    # buy/sell during the session must not push a duplicate Opened/Closed.
    # Exit suppression is (pos_key, date)-scoped: only a sell alert sent the
    # SAME session the exit lands covers the close — a stale alert (e.g. a
    # premarket bar extreme that didn't persist into the final RTH bar) must
    # not swallow the eventual real close push.
    exit_alerted = {(a.ref, a.date) for a in
                    PortfolioPendingAlert.select(PortfolioPendingAlert.ref, PortfolioPendingAlert.date)
                    .where((PortfolioPendingAlert.run_id == run.id)
                           & (PortfolioPendingAlert.kind == 'exit'))}
    entry_alerted = {(a.ref, a.date) for a in
                     PortfolioPendingAlert.select(PortfolioPendingAlert.ref, PortfolioPendingAlert.date)
                     .where((PortfolioPendingAlert.run_id == run.id)
                            & (PortfolioPendingAlert.kind == 'entry'))}

    def _save(p, status):
        # fix run-id prefix on freshly minted keys
        if p['pos_key'].startswith('0:'):
            p['pos_key'] = f"{run.id}:" + p['pos_key'][2:]
        row = PortfolioPosition.get_or_none(PortfolioPosition.pos_key == p['pos_key'])
        is_new = row is None
        if is_new:
            row = PortfolioPosition(pos_key=p['pos_key'], run_id=run.id)
            # a 'Buy' alert already pushed during the session covers the open
            row.notified_open = bool(p['entry_date'] < go_live
                                     or (p['symbol'], p['entry_date']) in entry_alerted)
            row.notified_close = False
        fields = dict(
            run_id=run.id, symbol=p['symbol'], side=p['side'], tier=p.get('tier'),
            entry_score=p.get('entry_score'), entry_version_id=p.get('entry_version_id'),
            dte=p['dte'], routed_15dte=bool(p.get('routed_15dte')),
            strike=p.get('strike'), expiration_date=p.get('expiration_date'),
            entry_date=p['entry_date'], entry_underlying=p['entry_underlying'],
            sigma_daily=p['sigma_daily'], premium_pct=p.get('premium_pct'),
            premium_usd=round(p['premium_usd'], 2), entry_equity_usd=round(p.get('entry_equity_usd') or 0, 2),
            alloc_pct=p.get('alloc_pct'), premium_mult=p.get('premium_mult'), delta=p.get('delta'),
            stressed=bool(p.get('stressed')), dead_hold_active=bool(p.get('dead_hold_active')),
            status=status, mark_date=p.get('mark_date'),
            current_underlying=p.get('current_underlying'),
            current_value_usd=round(p.get('current_value_usd') or 0, 2),
            pnl_usd=p.get('pnl_usd'), pnl_pct=p.get('pnl_pct'), hold_bars=p.get('hold_bars'),
            tp_price=p.get('tp_price'), sl_price=p.get('sl_price'), deadline=p.get('deadline'),
            last_open_date=p.get('last_open_date'),
            exit_date=p.get('exit_date'), exit_reason=p.get('exit_reason'),
            sweep_pending=bool(p.get('sweep_pending')),
            sweep_reason=p.get('sweep_reason'),
            sweep_action_date=p.get('sweep_action_date'),
        )
        for k, v in fields.items():
            setattr(row, k, v)
        if status == 'closed' and (p['pos_key'], p.get('exit_date')) in exit_alerted:
            # a 'Sell' alert already pushed during the exit session covers the close
            row.notified_close = True
        row.updated_at = now
        row.save()

    # Every position ends up either still in the ledger (open) or in closed_out;
    # new_open positions are already in one of those, so two passes cover all.
    for p in ledger:
        _save(p, 'open')
    for p in closed_out:
        _save(p, 'closed')

    # snapshots — only for processed days (history is frozen, never rewritten)
    start_usd = float(run.starting_capital_usd)
    for d, eq in snaps:
        snap = PortfolioEquitySnapshot.get_or_none(
            (PortfolioEquitySnapshot.run_id == run.id) & (PortfolioEquitySnapshot.date == d))
        if snap is None:
            snap = PortfolioEquitySnapshot(run_id=run.id, date=d)
        snap.equity_usd = round(float(eq), 2)
        snap.equity_cost_usd = round(float(eq), 2)
        snap.return_pct = (float(eq) / start_usd - 1.0) * 100.0 if start_usd else 0.0
        snap.open_count = sum(1 for p in ledger if p.get('last_open_date') and p['last_open_date'] >= d)
        snap.updated_at = now
        snap.save()

    open_value = sum(p['premium_usd'] * (1.0 + (p.get('pnl_pct') or 0)) for p in ledger)
    equity_usd = cash + open_value
    # peak stays cost-basis (sizing/DD continuity); displayed max DD comes from the MTM snapshot curve
    # max DD over the snapshot curve
    snaps_all = list(PortfolioEquitySnapshot.select().where(
        PortfolioEquitySnapshot.run_id == run.id).order_by(PortfolioEquitySnapshot.date))
    pk = start_usd
    mdd = 0.0
    for s in snaps_all:
        e = float(s.equity_usd)
        if e > pk:
            pk = e
        if pk > 0:
            mdd = max(mdd, 1.0 - e / pk)

    if not pending_requal:
        # While any holding's re-qualification is unresolvable (new version's
        # score rows still backfilling), keep the OLD version/fingerprint so
        # the transition re-fires on every sync until each holding resolves.
        run.version_id = version.id
        run.strategy_fingerprint = fp
    run.last_processed_date = D
    run.last_synced_at = now
    run.equity_usd = round(equity_usd, 2)
    run.cash_usd = round(cash, 2)
    run.peak_equity_usd = round(peak, 2)
    run.max_dd_pct = mdd * 100.0
    run.updated_at = now
    run.save()

    # Entry-cancel reconciliation: a provisional intraday 'Buy' alert whose
    # signal faded by the close (the session processed without opening it)
    # gets an explicit cancel notice — never leave the user holding an orphan.
    if process_days:
        opened_by_day = {}
        for p in new_open:
            opened_by_day.setdefault(p['entry_date'], set()).add(p['symbol'])
        cancelled_already = {(a.ref, a.date) for a in
                             PortfolioPendingAlert.select(PortfolioPendingAlert.ref, PortfolioPendingAlert.date)
                             .where((PortfolioPendingAlert.run_id == run.id)
                                    & (PortfolioPendingAlert.kind == 'entry_cancel'))}
        for ref, d in entry_alerted:
            if d in set(process_days) and ref not in opened_by_day.get(d, set()) \
                    and (ref, d) not in cancelled_already:
                alerts.append({'kind': 'entry_cancel', 'ref': ref, 'date': d,
                               'title': f"⚪ Buy {ref} cancelled",
                               'body': f"The {d.strftime('%b %d')} signal faded by the close — "
                                       "the strategy did not enter."})

    if send_notifications:
        if alerts:
            try:
                from notifications import send_pushover
            except Exception:
                send_pushover = None
            if send_pushover is not None:
                sprint_sent = False
                for a in alerts:
                    status, _ = send_pushover(a['title'], a['body'], priority=1)
                    if status == 'sent':
                        _record_alert(run.id, a['kind'], a['ref'], a['date'],
                                      payload=a.get('body'))
                        if a['kind'] == 'sprint_2x':
                            sprint_sent = True
                # sprint_2x_notified is deliberately separate from halt_new_entries
                # (already latched earlier regardless of delivery) -- this only
                # stops the retry-until-delivered alert queued above, and needs
                # its own save() since _advance's run.save() (via the fields dict
                # below) already ran before this send loop executes.
                if sprint_sent and not getattr(run, 'sprint_2x_notified', False):
                    run.sprint_2x_notified = True
                    run.save()
        _send_pending_notifications(run)


def _send_pending_notifications(run):
    from database.models.portfolio import PortfolioPosition
    go_live = run.go_live_date

    opens = list(PortfolioPosition.select().where(
        (PortfolioPosition.run_id == run.id)
        & (PortfolioPosition.notified_open == False)  # noqa: E712
        & (PortfolioPosition.entry_date >= go_live)
    ).order_by(PortfolioPosition.entry_date, PortfolioPosition.symbol))

    closes = list(PortfolioPosition.select().where(
        (PortfolioPosition.run_id == run.id)
        & (PortfolioPosition.status == 'closed')
        & (PortfolioPosition.notified_close == False)  # noqa: E712
        & (PortfolioPosition.exit_date.is_null(False))
        & (PortfolioPosition.exit_date >= go_live)
    ).order_by(PortfolioPosition.exit_date, PortfolioPosition.symbol))

    try:
        from notifications import send_pushover
    except Exception:
        return

    for p in opens:
        title, body = format_open_notification(p)
        status, _ = send_pushover(title, body, priority=1)
        if status == 'sent':
            p.notified_open = True
            p.save()

    for p in closes:
        title, body = format_close_notification(p)
        status, _ = send_pushover(title, body, priority=1)
        if status == 'sent':
            p.notified_close = True
            p.save()


def run_update(now=None, verbose=False):
    """Hook called from `trader update`/`close-update` (sends notifications)."""
    return sync(now=now, send_notifications=True, verbose=verbose)


def execution_window_status(now=None):
    """Execution-timing state for the Allocator banner (entry-timing canon).

    States: closed (non-trading day or post-close — scores FINAL), pre_open,
    provisional (open, before BUY_ALERT_FROM_ET — partial-day scores, 26% of
    morning signals fade), window (last ~30 min — scores near-final, buy now).
    """
    from database.utils.trading_calendar import is_trading_day
    et = now or _eastern_now()
    today = et.date()
    hm = (et.hour, et.minute)
    trading = is_trading_day(today)
    if not trading or et.hour >= MARKET_CLOSE_HOUR_ET:
        state = 'closed'
        next_d = _next_trading_day(today) if (not trading or et.hour >= MARKET_CLOSE_HOUR_ET) else today
    elif hm < MARKET_OPEN_ET:
        state, next_d = 'pre_open', today
    elif hm < BUY_ALERT_FROM_ET:
        state, next_d = 'provisional', today
    else:
        state, next_d = 'window', today

    def _secs_until(d, h, m):
        target = datetime(d.year, d.month, d.day, h, m, tzinfo=et.tzinfo)
        return max(0, int((target - et).total_seconds()))

    return {
        'state': state,
        'et_now': et.strftime('%H:%M'),
        'is_trading_day': trading,
        'window_from': f"{BUY_ALERT_FROM_ET[0]:02d}:{BUY_ALERT_FROM_ET[1]:02d}",
        'window_to': f"{MARKET_CLOSE_HOUR_ET:02d}:00",
        'window_date': next_d.isoformat(),
        'seconds_to_window': (_secs_until(next_d, *BUY_ALERT_FROM_ET)
                              if state in ('pre_open', 'provisional') else 0),
        'seconds_to_close': (_secs_until(today, MARKET_CLOSE_HOUR_ET, 0)
                             if state == 'window' else None),
    }


def build_pending_payload():
    """Dry-run pending view for `/api/portfolio/pending` — what the engine will
    buy at today's close (would_open), what to sell now (would_exit), and the
    carry-over buys from the last completed session, plus the execution-window
    state. Read-only: writes and pushes nothing."""
    res = sync(send_notifications=False, dry_run=True)
    pending = (res or {}).get('pending') if isinstance(res, dict) else {}
    return {
        'execution_window': execution_window_status(),
        'last_completed_session': (res or {}).get('D') if isinstance(res, dict) else None,
        'pending': pending or {},
        'generated_at': datetime.now().isoformat(timespec='seconds'),
    }


# ---------------------------------------------------------------------------
# API payload (read-only; serves the React page)
# ---------------------------------------------------------------------------
def build_state_payload(run=None, ensure_fresh=False):
    from database.models.portfolio import (
        PortfolioRun, PortfolioPosition, PortfolioEquitySnapshot, ensure_all_schema,
    )
    ensure_all_schema()
    if run is None:
        run = PortfolioRun.get_active()
    if run is None and ensure_fresh:
        run = sync(send_notifications=False)
    if run is None:
        return None

    if ensure_fresh:
        try:
            latest = _latest_price_date()
            target = last_completed_session(latest_data=latest) if latest else None
            if target is not None and (run.last_processed_date is None or run.last_processed_date < target):
                sync(send_notifications=False)
                run = PortfolioRun.get_active() or run
        except Exception:
            pass

    fx = float(run.cad_per_usd or 1.37)
    start_usd = float(run.starting_capital_usd or 0)
    equity_usd = float(run.equity_usd) if run.equity_usd is not None else start_usd
    cash_usd = float(run.cash_usd) if run.cash_usd is not None else equity_usd

    def _pos_json(p):
        return {
            'pos_key': p.pos_key,
            'symbol': p.symbol,
            'side': p.side,
            'label': position_label(p.symbol, p.expiration_date, p.strike, p.side),
            'tier': p.tier,
            'score': p.entry_score,
            'dte': p.dte,
            'routed_15dte': bool(p.routed_15dte),
            'strike': float(p.strike) if p.strike is not None else None,
            'expiration_date': p.expiration_date.isoformat() if p.expiration_date else None,
            'entry_date': p.entry_date.isoformat() if p.entry_date else None,
            'entry_underlying': float(p.entry_underlying) if p.entry_underlying is not None else None,
            'current_underlying': float(p.current_underlying) if p.current_underlying is not None else None,
            'alloc_pct': p.alloc_pct,
            'premium_usd': float(p.premium_usd) if p.premium_usd is not None else None,
            'current_value_usd': float(p.current_value_usd) if p.current_value_usd is not None else None,
            'pnl_usd': p.pnl_usd,
            'pnl_pct': p.pnl_pct,
            'hold_bars': p.hold_bars,
            'tp_price': float(p.tp_price) if p.tp_price is not None else None,
            'sl_price': float(p.sl_price) if p.sl_price is not None else None,
            'deadline': p.deadline.isoformat() if p.deadline else None,
            'exit_date': p.exit_date.isoformat() if p.exit_date else None,
            'exit_reason': p.exit_reason,
            'exit_reason_label': _REASON_LABEL.get(p.exit_reason, p.exit_reason) if p.exit_reason else None,
            'sweep_pending': bool(getattr(p, 'sweep_pending', False)),
            'sweep_action_date': (p.sweep_action_date.isoformat()
                                  if getattr(p, 'sweep_action_date', None) else None),
        }

    open_rows = list(PortfolioPosition.select().where(
        (PortfolioPosition.run_id == run.id) & (PortfolioPosition.status == 'open')
    ).order_by(PortfolioPosition.premium_usd.desc()))
    closed_rows = list(PortfolioPosition.select().where(
        (PortfolioPosition.run_id == run.id) & (PortfolioPosition.status == 'closed')
    ).order_by(PortfolioPosition.exit_date.desc(), PortfolioPosition.symbol).limit(60))

    open_positions = [_pos_json(p) for p in open_rows]
    closed_positions = [_pos_json(p) for p in closed_rows]

    open_value = sum((float(p.current_value_usd or 0) for p in open_rows))
    deployed = sum((float(p.premium_usd or 0) for p in open_rows))
    realized = sum((float(p.pnl_usd or 0) for p in closed_rows))

    snaps = list(PortfolioEquitySnapshot.select().where(
        PortfolioEquitySnapshot.run_id == run.id
    ).order_by(PortfolioEquitySnapshot.date))
    equity_curve = [{
        'date': s.date.isoformat(),
        'equity_usd': float(s.equity_usd),
        'return_pct': s.return_pct,
    } for s in snaps]

    return {
        'run': {
            'name': run.name,
            'profile': run.profile,
            'start_date': run.start_date.isoformat(),
            'go_live_date': run.go_live_date.isoformat(),
            'last_processed_date': run.last_processed_date.isoformat() if run.last_processed_date else None,
            'last_synced_at': run.last_synced_at.isoformat() if run.last_synced_at else None,
            'currency_default': 'CAD',
            'cad_per_usd': fx,
            'starting_capital_cad': float(run.starting_capital_cad),
            'starting_capital_usd': start_usd,
        },
        'summary': {
            'equity_usd': equity_usd,
            'cash_usd': cash_usd,
            'open_value_usd': open_value,
            'deployed_usd': deployed,
            'deployed_pct': (deployed / equity_usd * 100.0) if equity_usd else 0.0,
            'total_pnl_usd': equity_usd - start_usd,
            'total_return_pct': (equity_usd / start_usd - 1.0) * 100.0 if start_usd else 0.0,
            'realized_pnl_usd': realized,
            'max_dd_pct': run.max_dd_pct or 0.0,
            'open_count': len(open_rows),
            'closed_count': PortfolioPosition.select().where(
                (PortfolioPosition.run_id == run.id) & (PortfolioPosition.status == 'closed')
            ).count(),
        },
        'positions': open_positions,
        'closed': closed_positions,
        'equity_curve': equity_curve,
    }


def sprint_status():
    """`trader portfolio sprint-status` — print the P3.1 Phase 1 watchdog's
    current state. Read-only."""
    from database.models.portfolio import PortfolioRun
    run = PortfolioRun.get_active()
    if run is None:
        print('No active portfolio run.')
        return
    payload = build_state_payload(run)
    equity = float(payload['summary']['equity_usd']) if payload else None
    active = (run.profile == 'apex')
    print(f"Profile: {run.profile}  ({'watchdog ACTIVE' if active else 'watchdog dormant -- not apex'})")
    base = getattr(run, 'sprint_start_equity', None)
    if base is None:
        print("Sprint baseline: not set yet "
              + ("(arms automatically on the next sync)" if active else "(arms once profile=apex)"))
    else:
        base = float(base)
        ratio = (equity / base) if (equity is not None and base > 0) else None
        print(f"Sprint baseline: ${base:,.2f} as of {run.sprint_start_date}")
        if equity is not None:
            extra = f"  ({ratio:.2f}x baseline, target 2.00x)" if ratio is not None else ""
            print(f"Current equity:  ${equity:,.2f}{extra}")
    print(f"halt_new_entries: {bool(getattr(run, 'halt_new_entries', False))}"
          + ("  <- new entries are PAUSED; use `sprint-clear` to resume"
             if getattr(run, 'halt_new_entries', False) else ""))
    hit = getattr(run, 'sprint_2x_hit_date', None)
    if hit is None:
        print("2x milestone: not yet hit")
    else:
        print(f"2x milestone: hit {hit}  (push delivered: {bool(getattr(run, 'sprint_2x_notified', False))})")


def sprint_clear():
    """`trader portfolio sprint-clear` — resume new entries after a 2x halt.
    Does NOT touch the baseline or the one-time-alert latch (sprint_2x_hit_date)
    -- this sprint's milestone stays marked as already-fired so clearing can
    never immediately re-trigger the same alert/halt on the next sync. Use
    `sprint-arm` to start tracking a fresh milestone instead."""
    from database.models.portfolio import PortfolioRun
    run = PortfolioRun.get_active()
    if run is None:
        print('No active portfolio run.')
        return
    was = bool(getattr(run, 'halt_new_entries', False))
    run.halt_new_entries = False
    run.save()
    print(f"halt_new_entries: {was} -> False. New entries resume on the next sync "
          f"(exits/sweeps/alerts were never affected).")


def sprint_arm(now=None):
    """`trader portfolio sprint-arm` — (re-)arm the sprint watchdog: baseline
    = current MTM equity as of today, clears halt_new_entries AND the
    sprint_2x_hit_date/sprint_2x_notified latch, so the NEXT doubling fires a
    fresh push. Pure data reset on the run row -- no rotation, no profile
    change, no effect on existing positions."""
    from database.models.portfolio import PortfolioRun
    run = PortfolioRun.get_active()
    if run is None:
        print('No active portfolio run.')
        return
    payload = build_state_payload(run)
    equity = float(payload['summary']['equity_usd']) if payload else float(run.starting_capital_usd or 0)
    et = now or _eastern_now()
    run.sprint_start_equity = round(equity, 2)
    run.sprint_start_date = et.date()
    run.halt_new_entries = False
    run.sprint_2x_hit_date = None
    run.sprint_2x_notified = False
    run.save()
    print(f"Sprint watchdog armed: baseline ${equity:,.2f} as of {run.sprint_start_date}. "
          f"Next push (and entry-halt) fires at >= ${equity * 2:,.2f}.")
    if run.profile != 'apex':
        print(f"NOTE: profile is currently '{run.profile}' -- the watchdog is dormant "
              f"until the run is on 'apex'.")


def pause():
    """`trader portfolio pause` — P3.8 incident kill-switch. Suppresses
    BUY-flavored pushes (provisional entry + carry-over digest) and halts
    opening any new model entries. Exits/sweeps/dead-hold/sell alerts stay
    FULLY live. Pure sync-behavior flag: no position/equity/snapshot row is
    touched. Independent of (OR'd with) the P3.1 sprint watchdog's
    halt_new_entries -- see .claude/docs/incident-runbook.md."""
    from database.models.portfolio import PortfolioRun
    run = PortfolioRun.get_active()
    if run is None:
        print('No active portfolio run.')
        return
    was = bool(getattr(run, 'sync_paused', False))
    run.sync_paused = True
    run.save()
    print(f"sync_paused: {was} -> True. BUY pushes suppressed, new entries halted "
          f"on the next sync. Exits/sweeps/sell alerts remain fully live. "
          f"Resume with `trader portfolio resume`.")


def resume():
    """`trader portfolio resume` — clears the P3.8 kill-switch."""
    from database.models.portfolio import PortfolioRun
    run = PortfolioRun.get_active()
    if run is None:
        print('No active portfolio run.')
        return
    was = bool(getattr(run, 'sync_paused', False))
    run.sync_paused = False
    run.save()
    print(f"sync_paused: {was} -> False. New entries and BUY pushes restored "
          f"on the next sync.")


# ---------------------------------------------------------------------------
# Real-fill logging (P3.7, 2026-07-13) — the asymmetric-cost canon (mid-entry
# + limit-TP FREE, forced exits pay ~half-spread) is load-bearing across the
# whole engine and has never seen a real fill. This is the cheapest possible
# instrument that can falsify it: capture what the user ACTUALLY paid/
# received (there is no broker feed -- alerts + manual execution is the
# model), then tools/slippage_report.py compares it to the model's own mark.
# Pure data capture: never touches PortfolioPosition/equity/scoring.
# ---------------------------------------------------------------------------
def _resolve_fill_position(run, symbol, side, ts_date):
    """Best-effort auto-link a real fill to the PortfolioPosition it belongs
    to, so slippage_report.py has a modeled mark to compare against. Never
    blocks recording on failure -- returns None (unlinked) if no single
    clear candidate exists; record_fill still records the raw fill either
    way (never lose real data)."""
    from database.models.portfolio import PortfolioPosition
    if run is None:
        return None
    if side == 'buy':
        # opening fill -> the position that opened for this symbol on/before
        # ts_date, most recent first (an entry fill should still be open at
        # record time unless it already round-tripped same-session).
        q = (PortfolioPosition.select()
             .where((PortfolioPosition.run_id == run.id) & (PortfolioPosition.symbol == symbol)
                    & (PortfolioPosition.entry_date <= ts_date))
             .order_by(PortfolioPosition.entry_date.desc(), PortfolioPosition.id.desc()))
        return q.first()
    # closing fill -> prefer a position the engine already marked closed for
    # this symbol on/before ts_date; fall back to a still-open one (the user
    # may record their real exit before the engine has actioned it).
    q = (PortfolioPosition.select()
         .where((PortfolioPosition.run_id == run.id) & (PortfolioPosition.symbol == symbol)
                & (PortfolioPosition.status == 'closed')
                & (PortfolioPosition.exit_date.is_null(False))
                & (PortfolioPosition.exit_date <= ts_date))
         .order_by(PortfolioPosition.exit_date.desc(), PortfolioPosition.id.desc()))
    row = q.first()
    if row is not None:
        return row
    q = (PortfolioPosition.select()
         .where((PortfolioPosition.run_id == run.id) & (PortfolioPosition.symbol == symbol)
                & (PortfolioPosition.status == 'open'))
         .order_by(PortfolioPosition.entry_date.desc(), PortfolioPosition.id.desc()))
    return q.first()


def record_fill(symbol, price, qty, side='buy', ts=None, pos_key=None, notes=None):
    """`trader portfolio record-fill SYM PRICE QTY [--side buy|sell]
    [--ts YYYY-MM-DDTHH:MM] [--pos-key KEY]` -- P3.7. Records a REAL,
    manually-executed fill. PRICE is the premium PER CONTRACT (per-share
    quote, e.g. 2.35 for $2.35/share = $235/contract), matching how a broker
    fill confirmation is normally read; total dollar value is derived as
    price * qty * 100. Auto-links to the corresponding PortfolioPosition on
    a best-effort basis (_resolve_fill_position) so slippage_report.py has a
    modeled mark to compare against -- an unresolved link still records the
    raw fill (never lose real data); pass pos_key to link explicitly.
    Pure data capture -- never touches positions/equity/scoring."""
    from database.models.portfolio import PortfolioRun, PortfolioFill
    PortfolioFill.ensure_schema()
    run = PortfolioRun.get_active()
    side = (side or 'buy').lower()
    if side not in ('buy', 'sell'):
        print(f"Invalid side {side!r}; use buy or sell.")
        return None
    filled_at = ts or datetime.now()
    symbol = symbol.upper()
    price_f = float(price)
    qty_i = int(qty)
    fill_value = round(price_f * qty_i * 100.0, 2)

    resolved_key = pos_key
    if resolved_key is None:
        pos = _resolve_fill_position(run, symbol, side, filled_at.date())
        if pos is not None:
            resolved_key = pos.pos_key

    row = PortfolioFill.create(
        run_id=(run.id if run is not None else None), pos_key=resolved_key,
        symbol=symbol, side=side, price=round(price_f, 4), qty=qty_i,
        fill_value_usd=fill_value, filled_at=filled_at, notes=notes,
    )
    link_note = (f"linked to {resolved_key}" if resolved_key
                else "UNLINKED -- no matching position found; re-record with --pos-key to link")
    print(f"Recorded fill #{row.id}: {symbol} {side} {qty_i}x @ ${price_f:.2f} "
          f"(${fill_value:,.2f} total) at {filled_at.isoformat(timespec='minutes')} -- {link_note}")
    return row


def _cli_record_fill(rest):
    """Tiny hand-rolled parser for `record-fill SYM PRICE QTY [flags]` --
    kept independent of the `sub`-only dispatch the rest of `_cli` uses,
    since this is the one subcommand that takes positional args + flags."""
    if len(rest) < 3:
        print('Usage: trader portfolio record-fill SYM PRICE QTY [--side buy|sell] '
              '[--ts YYYY-MM-DDTHH:MM] [--pos-key KEY]')
        return
    symbol, price_raw, qty_raw = rest[0], rest[1], rest[2]
    opts = rest[3:]
    side, ts_raw, pos_key = 'buy', None, None
    i = 0
    while i < len(opts):
        tok = opts[i]
        if tok == '--side' and i + 1 < len(opts):
            side, i = opts[i + 1], i + 2
        elif tok == '--ts' and i + 1 < len(opts):
            ts_raw, i = opts[i + 1], i + 2
        elif tok == '--pos-key' and i + 1 < len(opts):
            pos_key, i = opts[i + 1], i + 2
        else:
            print(f"Unknown option {tok!r}. Usage: record-fill SYM PRICE QTY "
                  f"[--side buy|sell] [--ts YYYY-MM-DDTHH:MM] [--pos-key KEY]")
            return
    ts = None
    if ts_raw:
        try:
            ts = datetime.fromisoformat(ts_raw)
        except Exception:
            print(f"Invalid --ts {ts_raw!r}; use ISO format e.g. 2026-07-13T14:30 (no space).")
            return
    try:
        price_f = float(price_raw)
        qty_i = int(qty_raw)
    except Exception:
        print(f"PRICE must be a number and QTY an integer (got {price_raw!r}, {qty_raw!r}).")
        return
    record_fill(symbol, price_f, qty_i, side=side, ts=ts, pos_key=pos_key)


def _cli(argv):
    cmd = (argv[0] if argv else 'sync').lower()
    if cmd == 'reset':
        reset()
        print('Portfolio wiped. Re-syncing from', START_DATE.isoformat(), '...')
        run = sync(send_notifications=False, verbose=True)
    elif cmd == 'sync':
        run = sync(send_notifications=False, verbose=True)
    elif cmd == 'pending':
        # dry-run preview of the live action pass — prints would-be closes,
        # opens, sweeps, and alerts WITHOUT writing or pushing anything.
        run = sync(send_notifications=False, verbose=True, dry_run=True)
        return 0
    elif cmd == 'notify':
        # Scheduled lightweight alert pass (~1 min) — full sync WITH pushes.
        # Runs at precise times (08:45 + 15:30 ET via Task Scheduler) because
        # the heavy ~20-min updates finish their alert pass too early (15:00
        # run → ~15:20, before the buy window) or too late (15:45 run →
        # ~16:05, after the close). Time gates in _pending_actions decide
        # WHAT is pushable; this command just runs the pass on time.
        run = sync(send_notifications=True, verbose=True)
    elif cmd == 'status':
        from database.models.portfolio import PortfolioRun
        run = PortfolioRun.get_active()
    elif cmd == 'sprint-status':
        sprint_status()
        return 0
    elif cmd == 'sprint-clear':
        sprint_clear()
        return 0
    elif cmd == 'sprint-arm':
        sprint_arm()
        return 0
    elif cmd == 'pause':
        pause()
        return 0
    elif cmd == 'resume':
        resume()
        return 0
    elif cmd == 'record-fill':
        _cli_record_fill(argv[1:])
        return 0
    else:
        print(f"Unknown command {cmd!r}. Use: sync | reset | status | pending | notify | "
              f"sprint-status | sprint-clear | sprint-arm | pause | resume | record-fill")
        return 2
    if run is None:
        print('No active portfolio run.')
        return 0
    payload = build_state_payload(run)
    s = payload['summary'] if payload else {}
    print(f"Run: {run.name} ({run.profile}) | start {run.start_date} | go-live {run.go_live_date}")
    print(f"Last processed: {run.last_processed_date} | FX (CAD/USD): {run.cad_per_usd}")
    print(f"Equity: ${s.get('equity_usd', 0):,.2f} USD  "
          f"(${s.get('equity_usd', 0) * float(run.cad_per_usd):,.2f} CAD)  "
          f"return {s.get('total_return_pct', 0):+.2f}%")
    print(f"Open: {s.get('open_count', 0)} | Closed: {s.get('closed_count', 0)} | "
          f"Deployed {s.get('deployed_pct', 0):.1f}% | Max DD {s.get('max_dd_pct', 0):.1f}%")
    return 0


if __name__ == '__main__':
    import os
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    sys.exit(_cli(sys.argv[1:]))
