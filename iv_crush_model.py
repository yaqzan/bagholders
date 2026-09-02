"""
IV Crush Model — Earnings-Aware Option-Pricing Adjustment for MC
=================================================================

Wraps option P&L outcomes from `monte_carlo.py` / `backtest_cascade.py` with
a calibrated IV-crush adjustment that models:

  (a) Entry-side IV inflation: ATM options entered within ~5 calendar days
      BEFORE an earnings announcement carry elevated IV.  The MC's flat
      `PREMIUM_MULT × σ_realized` formula under-prices entry; in reality the
      buyer pays a premium 5-30% above this baseline.
  (b) Exit-side IV crush: positions HELD THROUGH earnings exit on a post-event
      IV that is empirically 12-22% below pre-event IV (scales with pre-IV
      magnitude).

Calibration source: `experiments/iv_crush_assessment.py` (N=20,870 ATM options
spanning 1,268 earnings events, Feb 2025 → Apr 2026 from option_prices DB).

  - Mean rel crush: +12.6% (median +15.2%)
  - Crush scales with pre-IV magnitude (post/pre ratio):
        pre_iv <0.40: ~0.94      0.40-0.60: ~0.86
        0.60-0.80:    ~0.83      0.80-1.20: ~0.80
        >1.20:        ~0.78
  - DTE effect smaller: 15-21d crushes 14.7%, 36-50d crushes 10.8%
  - Side effect tiny (~0.5pp): puts and calls crush nearly identically

Best-guess multiplicative model (from `iv_crush_followup.py`):
    post_iv ≈ pre_iv × (0.95 - 0.20 × pre_iv) × (1 + 0.04 × log(DTE/20))


Compound P&L formula
--------------------
For an option whose entry premium is `κ_pre × baseline` (pre-event inflation)
and whose exit value is `κ_post × baseline_at_exit` (post-event crush), the
realized P&L is:

    adj_pnl = (κ_post × (1 + baseline_pnl) - κ_pre) / κ_pre

When the trade does NOT span earnings, κ_pre = κ_post = 1.0 → adj_pnl = baseline_pnl.

Example (TP=+30%, baseline_pnl=+0.30, κ_pre=1.20, κ_post=0.85):
    adj_pnl = (0.85 × 1.30 - 1.20) / 1.20 = -0.079  (-8% vs +30% expected)

Example (SL=-20%, baseline_pnl=-0.20, κ_pre=1.20, κ_post=0.85):
    adj_pnl = (0.85 × 0.80 - 1.20) / 1.20 = -0.433  (-43% vs -20% expected)

The asymmetry is realistic: even when underlying hits the put TP barrier,
the option premium can have crushed enough that the net P&L is negative.

Use
---
1. Import: `from iv_crush_model import iv_adjust_outcome, IV_CRUSH_ENABLED`
2. After calling compute_trade_outcome / compute_put_outcome, call:
       outcome = iv_adjust_outcome(outcome, sym_id, signal_date, ern_dates_for_sym)
   (no-op if IV_CRUSH_ENABLED is False or no earnings span the trade window).

Env vars
--------
  IV_CRUSH_ENABLED      : '1' to enable (default '0' — off for backwards compat)
  IV_CRUSH_K_PRE_FULL   : κ_pre when 0 ≤ days_to_ern ≤ 1 (default 1.20)
  IV_CRUSH_K_PRE_HALF   : κ_pre when 2 ≤ days_to_ern ≤ 3 (default 1.10)
  IV_CRUSH_K_PRE_LIGHT  : κ_pre when 4 ≤ days_to_ern ≤ 5 (default 1.05)
  IV_CRUSH_K_POST_BASE  : κ_post intercept (default 0.95)
  IV_CRUSH_K_POST_SLOPE : κ_post slope on IV-proxy (default 0.20)
  IV_CRUSH_K_POST_FLOOR : κ_post lower bound (default 0.65)
"""
import os
import math
import csv
import random


# ── Configuration ─────────────────────────────────────────────────────────

IV_CRUSH_ENABLED      = os.environ.get('IV_CRUSH_ENABLED', '0') == '1'
IV_CRUSH_MODE         = os.environ.get('IV_CRUSH_MODE', 'deterministic').lower()
# 'deterministic' = κ_pre × κ_post compound (uniform crush per spanning trade)
# 'stochastic'    = sample option price_ratio from empirical CSV per spanning trade
IV_CRUSH_SAMPLES_CSV  = os.environ.get(
    'IV_CRUSH_SAMPLES_CSV',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'experiments', 'iv_crush_samples.csv')
)
IV_CRUSH_SEED         = int(os.environ.get('IV_CRUSH_SEED', '0'))   # 0 = non-deterministic

# Pre-earnings entry-side IV inflation (κ_pre).
# These represent the premium markup vs the baseline 1.82×σ formula at various
# distances from earnings.  Calibrated rough-eye against the empirical pre/post
# ratios: high-IV stocks have larger inflation; the values below are blended
# averages aligned with the 0.40-0.60 IV bucket (the modal regime).
K_PRE_FULL   = float(os.environ.get('IV_CRUSH_K_PRE_FULL',  '1.15'))   # 0-1 days
K_PRE_HALF   = float(os.environ.get('IV_CRUSH_K_PRE_HALF',  '1.07'))   # 2-3 days
K_PRE_LIGHT  = float(os.environ.get('IV_CRUSH_K_PRE_LIGHT', '1.03'))   # 4-5 days
K_PRE_TRACE  = float(os.environ.get('IV_CRUSH_K_PRE_TRACE', '1.01'))   # 6-7 days

# Post-earnings crush coefficients (κ_post = base − slope × IV_proxy, floored).
K_POST_BASE  = float(os.environ.get('IV_CRUSH_K_POST_BASE',  '0.95'))
K_POST_SLOPE = float(os.environ.get('IV_CRUSH_K_POST_SLOPE', '0.15'))
K_POST_FLOOR = float(os.environ.get('IV_CRUSH_K_POST_FLOOR', '0.75'))


# ── Core helpers ──────────────────────────────────────────────────────────

def kappa_pre(days_to_ern):
    """Pre-earnings IV inflation factor.  days_to_ern in calendar days
    (entry to nearest forward earnings).  Returns 1.0 if no event in window.
    """
    if days_to_ern is None or days_to_ern < 0:
        return 1.0
    if days_to_ern <= 1:   return K_PRE_FULL
    if days_to_ern <= 3:   return K_PRE_HALF
    if days_to_ern <= 5:   return K_PRE_LIGHT
    if days_to_ern <= 7:   return K_PRE_TRACE
    return 1.0


def kappa_post(vol_pct_daily):
    """Post-earnings crush factor.  vol_pct_daily is realized 60d daily σ in %.
    Converts to annualized IV proxy, applies linear-decreasing crush.
    """
    if vol_pct_daily is None or vol_pct_daily <= 0:
        return 1.0
    iv_proxy = (vol_pct_daily / 100.0) * math.sqrt(252.0)  # annualized fractional
    iv_proxy = max(0.20, min(2.0, iv_proxy))
    return max(K_POST_FLOOR, K_POST_BASE - K_POST_SLOPE * iv_proxy)


def adjust_pnl(baseline_pnl, k_pre, k_post):
    """Compound P&L under entry inflation + exit crush.
    adj = (κ_post × (1 + baseline) - κ_pre) / κ_pre
    Floor at -1.0 (option can't go below worthless).
    """
    if k_pre == 1.0 and k_post == 1.0:
        return baseline_pnl
    adj = (k_post * (1.0 + baseline_pnl) - k_pre) / k_pre
    return max(-1.0, adj)


# ── Stochastic sampling (empirical CSV) ───────────────────────────────────

# Stratified pools of empirical price_ratio = post_price / pre_price.
# Keyed by (side: 'CALL'|'PUT', dte_band: '15-21'|'22-35'|'36-50').
_SAMPLES = None  # type: dict | None
_SAMPLES_RNG = None


def _dte_band(dte):
    if dte < 15:        return '15-21'
    if dte <= 21:       return '15-21'
    if dte <= 35:       return '22-35'
    if dte <= 50:       return '36-50'
    return '36-50'


def _load_samples(path=IV_CRUSH_SAMPLES_CSV):
    """Load iv_crush_samples.csv and stratify into (side, dte_band) pools."""
    global _SAMPLES, _SAMPLES_RNG
    if _SAMPLES is not None:
        return _SAMPLES
    pools = {}
    if not os.path.exists(path):
        _SAMPLES = pools
        return pools
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                side = r['option_type'].strip().upper()
                dte = int(r['dte'])
                pre_p = float(r['pre_price'])
                post_p = float(r['post_price'])
                if pre_p <= 0:
                    continue
                # Filter outliers (data errors, microstructure)
                ratio = post_p / pre_p
                if ratio < 0.0 or ratio > 10.0:
                    continue
                band = _dte_band(dte)
                pools.setdefault((side, band), []).append(ratio)
            except (ValueError, KeyError):
                continue
    _SAMPLES = pools
    _SAMPLES_RNG = random.Random(IV_CRUSH_SEED) if IV_CRUSH_SEED else random.Random()
    return pools


def sample_price_ratio(side, dte):
    """Sample an empirical post-earnings IV haircut for a spanning trade.

    The source CSV stores post/pre option price ratios. Values above 1.0 are
    dominated by underlying movement, which callers model separately. Treat
    this helper as an IV multiplier only: it can reduce option value or leave it
    unchanged, never boost post-earnings P&L.

    Falls back to deterministic kappa_post-style multiplier (0.85) if pool empty.
    """
    pools = _load_samples()
    side = side.upper()
    band = _dte_band(dte)
    pool = pools.get((side, band))
    if pool is None or not pool:
        # Fallback: try same side, all bands; then any
        pool = []
        for (s, _), p in pools.items():
            if s == side:
                pool.extend(p)
        if not pool:
            for p in pools.values():
                pool.extend(p)
    if not pool:
        return 0.85   # fallback if CSV missing entirely
    ratio = _SAMPLES_RNG.choice(pool)
    return max(0.0, min(1.0, ratio))


# ── Trade-spanning logic ──────────────────────────────────────────────────

def is_amc(call_time):
    """Returns True if the announcement is After Market Close (price reaction
    shows up on the NEXT trading day's open gap, not the same-date bar).

    Threshold: 16:00 ET (market close). Intraday (09:30-16:00) earnings are
    rare and treated as same-day (reaction in that day's intraday move).

    Args:
        call_time: HH:MM:SS string from EarningsDate.call_time, or None
                   (treated as BMO/same-day for safety)
    """
    if not call_time:
        return False
    try:
        h, m = str(call_time).split(':')[:2]
        return int(h) * 60 + int(m) >= 16 * 60
    except (ValueError, AttributeError):
        return False


def compute_effective_date(announcement_date, call_time, trading_days=None):
    """Return the trading day on which the earnings reaction appears.

    BMO/intraday: same as announcement_date (price moves that day).
    AMC: next trading day (overnight move shows up on next session's open).

    If `trading_days` is provided (sorted list of date objects), the AMC
    shift uses the actual market calendar. Otherwise falls back to weekday
    arithmetic (skip Sat=5 / Sun=6), which is correct for ~99% of cases
    (misses NYSE holidays — acceptable for batch score-stage usage).
    """
    if not is_amc(call_time):
        return announcement_date

    if trading_days:
        import bisect
        idx = bisect.bisect_right(trading_days, announcement_date)
        if idx < len(trading_days):
            return trading_days[idx]
        # Past the end of the calendar — fall through to weekday arithmetic

    # Weekday-based fallback: skip weekends.
    from datetime import timedelta
    eff = announcement_date + timedelta(days=1)
    while eff.weekday() >= 5:   # 5=Sat, 6=Sun
        eff += timedelta(days=1)
    return eff


def shift_to_effective_dates(rows, trading_days):
    """Given an iterable of (symbol, announcement_date, call_time) rows,
    return a dict {symbol: [effective_date, ...]} (sorted, deduplicated).

    Used to build the per-symbol earnings map consumed by find_spanning_earnings
    and the score-stage earnings-window logic. AMC events are shifted to the
    next trading day so spans-detection reflects when the price reaction
    actually appears.
    """
    from collections import defaultdict
    out = defaultdict(set)
    for row in rows:
        # Accept (sym, date) or (sym, date, call_time) tuples
        if len(row) == 2:
            sym, ad = row
            ct = None
        else:
            sym, ad, ct = row[0], row[1], row[2]
        eff = compute_effective_date(ad, ct, trading_days)
        out[sym].add(eff)
    return {s: sorted(d) for s, d in out.items()}


def find_spanning_earnings(entry_date, exit_date, ern_dates_for_sym):
    """Return the FIRST earnings event in (entry_date, exit_date], or None.
    `ern_dates_for_sym` is a sorted list of date objects (typically EFFECTIVE
    dates from `shift_to_effective_dates`, so AMC events appear on the
    reaction-day, not the announcement-day).
    """
    if not ern_dates_for_sym:
        return None
    for ed in ern_dates_for_sym:
        if entry_date < ed <= exit_date:
            return ed
        if ed > exit_date:
            break
    return None


def find_nearest_forward_earnings(entry_date, ern_dates_for_sym, max_days=7):
    """Return the nearest forward earnings date within max_days, or None.
    Used for κ_pre even when the trade doesn't span (entry inflation alone
    bleeds when trade closes pre-earnings via the inflated entry premium).
    """
    if not ern_dates_for_sym:
        return None
    for ed in ern_dates_for_sym:
        if ed < entry_date:
            continue
        delta = (ed - entry_date).days
        if delta <= max_days:
            return ed
        return None
    return None


# ── Outcome adjustment ────────────────────────────────────────────────────

def iv_adjust_outcome(outcome, signal_date, exit_date, vol_pct, ern_dates_for_sym, side='CALL'):
    """Adjust an outcome dict's net_tp / net_sl in-place for IV crush.

    Args:
        outcome:    dict from compute_trade_outcome / compute_put_outcome
                    (must have keys 'kind', 'exit_bar', 'net_tp', 'net_sl').
        signal_date: entry date.
        exit_date:   exit date (computed from signal_date + exit_bar bars,
                     using a trading-day calendar provided by caller).
        vol_pct:     realized 60d daily vol in %.
        ern_dates_for_sym: sorted list of earnings dates for this symbol.

    Returns:
        Adjusted outcome dict (NEW dict if changes, else original).

    Logic:
        - If trade SPANS earnings: apply both κ_pre (using days from signal to
          earnings) and κ_post (using vol_pct).
        - If trade is PRE-EARNINGS only (entry within ~7 days, exit before
          earnings): apply κ_pre only — entry inflation bleeds even on
          intraday TP exits.
        - Else: no adjustment.
    """
    if not IV_CRUSH_ENABLED:
        return outcome
    if not ern_dates_for_sym:
        return outcome

    spanning_ed = find_spanning_earnings(signal_date, exit_date, ern_dates_for_sym)
    nearest_ed  = find_nearest_forward_earnings(signal_date, ern_dates_for_sym)

    spans = spanning_ed is not None
    if not spans and nearest_ed is None:
        return outcome

    # Stochastic mode: sample a price_ratio for spanning trades from empirical CSV.
    # The price_ratio is the option's post/pre value change due to earnings;
    # multiplied into (1 + baseline_pnl) to give realized P&L through earnings.
    # For non-spanning pre-earnings trades, fall back to deterministic κ_pre.
    if IV_CRUSH_MODE == 'stochastic' and spans:
        # Strategy uses 30 DTE options; HOLD_DAYS=15 = half the option life.
        ratio = sample_price_ratio(side, dte=30)
        new_tp = ratio * (1.0 + outcome.get('net_tp', 0.0)) - 1.0
        new_sl = ratio * (1.0 + outcome.get('net_sl', 0.0)) - 1.0
        new_tp = max(-1.0, new_tp)
        new_sl = max(-1.0, new_sl)
        new = dict(outcome)
        new['net_tp'] = new_tp
        new['net_sl'] = new_sl
        new['_iv_ratio'] = ratio
        new['_iv_spans'] = True
        return new

    # Deterministic mode (or stochastic mode for pre-only trades):
    if spans:
        d2e = (spanning_ed - signal_date).days
        k_pre  = kappa_pre(d2e)
        k_post = kappa_post(vol_pct)
    else:
        d2e = (nearest_ed - signal_date).days
        k_pre  = kappa_pre(d2e)
        k_post = 1.0

    if k_pre == 1.0 and k_post == 1.0:
        return outcome

    new_tp = adjust_pnl(outcome.get('net_tp', 0.0), k_pre, k_post)
    new_sl = adjust_pnl(outcome.get('net_sl', 0.0), k_pre, k_post)
    new = dict(outcome)
    new['net_tp'] = new_tp
    new['net_sl'] = new_sl
    new['_iv_kpre']  = k_pre
    new['_iv_kpost'] = k_post
    new['_iv_spans'] = spans
    return new


# ── Trading-calendar helper for caller convenience ───────────────────────

def estimate_exit_date(signal_date, exit_bar, calendar_dates):
    """Walk forward `exit_bar` trading days from signal_date in `calendar_dates`
    (a sorted list of trading dates).  Falls back to calendar-day arithmetic
    if signal_date isn't found.
    """
    try:
        idx = calendar_dates.index(signal_date)
        new_idx = min(idx + exit_bar, len(calendar_dates) - 1)
        return calendar_dates[new_idx]
    except (ValueError, AttributeError):
        # Fall back: assume 5 trading days = 7 calendar days
        from datetime import timedelta
        return signal_date + timedelta(days=int(round(exit_bar * 7 / 5)))
