"""
Earnings-proximal signal win rate.

Question
--------
When a stock produces a qualifying signal (>=75 for calls, <=25 for puts) within
3 trading days of a scheduled earnings release, does buying options into the
event still pay?

The concern is IV: ATM option premium is inflated going into earnings and
collapses afterwards. Even if direction is right, the IV crush can eat the P&L.

Methodology
-----------
- Find every EarningsDate row.
- For each, pick the most-extreme qualifying Score whose date is within
  [earnings_date - 3 trading days, earnings_date - 1 trading day] inclusive.
  "Before" is strict: a score ON the earnings date (AMC release) is excluded
  since we can't measure its post-earnings move without ambiguity.
- Entry = close on the first trading day strictly AFTER earnings_date. This
  isolates post-earnings price action (we do NOT count the earnings-day move
  in the win criterion).
- Run the standard barrier-touch walk from that post-earnings entry using the
  same (K, M) as assess_scores.py:
      Call side (score >= 50): K = 2.0 sigma target, M = 5.0 sigma stop
      Put  side (score <  50): K = 1.0 sigma target, M = 2.0 sigma stop
  Period scaling: sqrt(W / 30). sigma = 60-day realized daily stdev computed
  on the closes ending at the entry date (post-earnings bar).
- Report win rates at 7d / 15d / 30d / 60d and separately the earnings-day
  gap (close-to-close from score date to first post-earnings bar) in sigma
  units - that gap is the move that has to be paid for via pre-earnings IV.

IV payoff heuristic
-------------------
For ATM options at ~30 DTE, a typical pre-earnings IV premium compresses
about 30-50% post-announcement. An ATM option has delta ~0.5 and
premium ~= 0.4 * sigma_annualized * sqrt(T) as a fraction of spot. After
earnings, the option needs the underlying to move about 1 sigma_daily_pre-earnings
just to offset the IV crush. So if the post-earnings barrier-touch win rate is
near or below the random-walk baseline (M/(K+M): 71.4% calls, 66.7% puts), the
IV makes it NOT worth it. We print both numbers so the comparison is direct.
"""
from __future__ import annotations
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

from colorama import Fore, Style

from database.models.core import (
    AlgorithmVersion, EarningsDate, Score,
)
from database.models.technical import PriceHistory
from database.utils.trading_calendar import is_trading_day

# Mirror assess_scores.py constants so numbers are directly comparable.
SWING_K_HIGH = 1.0   # put: target = 1*sigma drop at 30d
SWING_M_HIGH = 2.0   # put: stop   = 2*sigma rise at 30d
SWING_K_LOW  = 2.0   # call: target = 2*sigma rise at 30d
SWING_M_LOW  = 5.0   # call: stop   = 5*sigma drop at 30d
SWING_REFERENCE_DAYS = 30
SWING_VOL_LOOKBACK   = 60

# Random-walk gambler's-ruin baseline (barrier-touch within infinite time).
RANDOM_WR_CALL = SWING_M_LOW  / (SWING_K_LOW  + SWING_M_LOW)  * 100  # ~71.43%
RANDOM_WR_PUT  = SWING_M_HIGH / (SWING_K_HIGH + SWING_M_HIGH) * 100  # ~66.67%

PERIODS = [('7d', 7), ('15d', 15), ('30d', 30), ('60d', 60)]
CALL_BUCKETS = [('95+', 95, 100), ('90-94', 90, 94), ('85-89', 85, 89),
                ('80-84', 80, 84), ('75-79', 75, 79)]
PUT_BUCKETS  = [('<=5', 0, 5), ('6-10', 6, 10), ('11-15', 11, 15),
                ('16-20', 16, 20), ('21-25', 21, 25)]

PRE_WINDOW_TRADING_DAYS = 3


def prior_trading_day(d):
    d = d - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_trading_day(d):
    d = d + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def realized_vol_pct(closes, base_idx, lookback=SWING_VOL_LOOKBACK):
    if base_idx < lookback:
        return None
    rets = []
    for j in range(base_idx - lookback + 1, base_idx + 1):
        prev = closes[j - 1]
        if prev <= 0:
            continue
        rets.append((closes[j] - prev) / prev)
    if len(rets) < max(5, lookback // 2):
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


def barrier_walk(dates, closes, highs, lows, base_idx, side):
    """Run barrier-touch walk from base_idx, return dict[period_label]->'win'|'stop'|'expire'.

    base_idx is the entry bar (close is entry price). Walk begins at base_idx+1.
    """
    vol = realized_vol_pct(closes, base_idx)
    if vol is None or vol <= 0:
        return None, None
    entry = closes[base_idx]
    if entry <= 0:
        return None, None

    base_date = dates[base_idx]
    K_base = SWING_K_HIGH if side == 'high' else SWING_K_LOW
    M_base = SWING_M_HIGH if side == 'high' else SWING_M_LOW

    fwd_end = min(len(closes), base_idx + 1 + 150)
    if fwd_end <= base_idx + 1:
        return None, vol

    out = {}
    for label, W in PERIODS:
        cutoff = base_date + timedelta(days=W)
        # need at least one bar past cutoff to distinguish expire from truncation
        if dates[fwd_end - 1] <= cutoff:
            continue
        scale = math.sqrt(W / SWING_REFERENCE_DAYS)
        k = K_base * scale
        m = M_base * scale
        if side == 'high':
            t_win  = entry * (1 - k * vol / 100)
            t_stop = entry * (1 + m * vol / 100)
        else:
            t_win  = entry * (1 + k * vol / 100)
            t_stop = entry * (1 - m * vol / 100)

        result = 'expire'
        for j in range(base_idx + 1, fwd_end):
            if dates[j] > cutoff:
                break
            hi, lo = highs[j], lows[j]
            if side == 'high':
                win_hit  = lo <= t_win
                stop_hit = hi >= t_stop
            else:
                win_hit  = hi >= t_win
                stop_hit = lo <= t_stop
            if win_hit and stop_hit:
                # same-bar collision: break by close direction
                cls = closes[j]
                if side == 'high':
                    result = 'win' if cls < entry else 'stop'
                else:
                    result = 'win' if cls > entry else 'stop'
                break
            if win_hit:
                result = 'win'
                break
            if stop_hit:
                result = 'stop'
                break
        out[label] = result
    return out, vol


def load_price_index(symbols):
    """Return {symbol: (dates[], closes[], highs[], lows[], date_to_idx)}."""
    idx = {}
    for sym in symbols:
        rows = list(
            PriceHistory.select(PriceHistory.date, PriceHistory.close,
                                PriceHistory.high, PriceHistory.low)
            .where(PriceHistory.symbol == sym)
            .order_by(PriceHistory.date)
        )
        if not rows:
            continue
        dates  = [r.date for r in rows]
        closes = [float(r.close) for r in rows]
        highs  = [float(r.high) for r in rows]
        lows   = [float(r.low) for r in rows]
        idx[sym] = (dates, closes, highs, lows, {d: i for i, d in enumerate(dates)})
    return idx


def bucket_of(score):
    if score >= 50:
        for name, lo, hi in CALL_BUCKETS:
            if lo <= score <= hi:
                return ('call', name)
    else:
        for name, lo, hi in PUT_BUCKETS:
            if lo <= score <= hi:
                return ('put', name)
    return (None, None)


def main():
    # Optional first arg: restrict to last N days (default: all history)
    lookback_days = None
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            lookback_days = int(float(tok[:-1]) * 365)
        elif tok.endswith('d'):
            lookback_days = int(tok[:-1])
        elif tok.isdigit():
            lookback_days = int(tok)

    cutoff_date = None
    if lookback_days:
        cutoff_date = date.today() - timedelta(days=lookback_days)

    version = AlgorithmVersion.get_active_scores_version()
    print(f"{Fore.CYAN}Earnings-proximal signal win rate{Style.RESET_ALL}")
    print(f"Algorithm version: {version.git_commit if version else 'unknown'}")
    if cutoff_date:
        print(f"Lookback: earnings on or after {cutoff_date}")
    else:
        print("Lookback: all history")
    print(f"Pre-earnings window: up to {PRE_WINDOW_TRADING_DAYS} trading days before")
    print(f"Entry: close of first trading day AFTER earnings")
    print()

    # Load all earnings dates
    er_q = EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
    if cutoff_date:
        er_q = er_q.where(EarningsDate.date >= cutoff_date)
    earnings = list(er_q)
    print(f"Earnings rows loaded: {len(earnings)}")

    # Pull every score within pre-window + buffer, one query per symbol (cheap).
    # Build a (symbol -> [(date, overall)]) map to scan.
    symbols_with_er = sorted({e.symbol_id for e in earnings})
    print(f"Unique symbols: {len(symbols_with_er)}")

    # Load all qualifying scores up-front for the symbols of interest.
    score_q = (Score
               .select(Score.symbol, Score.date, Score.overall)
               .where(Score.version == version)
               .where(Score.symbol.in_(symbols_with_er))
               .where((Score.overall >= 75) | (Score.overall <= 25)))
    if cutoff_date:
        # earnings >= cutoff and score within 10 trading days of earnings
        score_q = score_q.where(Score.date >= cutoff_date - timedelta(days=30))

    scores_by_sym = defaultdict(list)
    for s in score_q:
        scores_by_sym[s.symbol_id].append((s.date, int(s.overall)))
    for sym in scores_by_sym:
        scores_by_sym[sym].sort()

    # Load price history once for affected symbols
    print("Loading price history...")
    price_idx = load_price_index(symbols_with_er)
    print(f"Price history loaded for {len(price_idx)} symbols")

    # Iterate earnings events, pick best qualifying pre-earnings score
    # (most extreme = furthest from 50). Keep at most one signal per earnings event.
    stats = defaultdict(lambda: {
        'n': 0,
        'periods': defaultdict(lambda: {'win': 0, 'stop': 0, 'expire': 0}),
        'earn_gap_sigma': [],  # close at entry vs close at score_date, in sigma-of-entry units
        'score_date_offset': [],  # trading days before earnings
    })

    processed = 0
    skipped_no_score = 0
    skipped_no_price = 0
    skipped_no_post_earn_bar = 0
    skipped_no_walk = 0

    for er in earnings:
        sym = er.symbol_id
        er_date = er.date

        # Build the pre-window (N trading days strictly before er_date)
        window_end = prior_trading_day(er_date)
        window_start = window_end
        for _ in range(PRE_WINDOW_TRADING_DAYS - 1):
            window_start = prior_trading_day(window_start)

        # Find qualifying scores in [window_start, window_end]
        sym_scores = scores_by_sym.get(sym, [])
        in_window = [(d, s) for (d, s) in sym_scores
                     if window_start <= d <= window_end]
        if not in_window:
            skipped_no_score += 1
            continue

        # Most extreme (furthest from 50)
        in_window.sort(key=lambda t: abs(t[1] - 50), reverse=True)
        score_date, score_val = in_window[0]

        pidx = price_idx.get(sym)
        if not pidx:
            skipped_no_price += 1
            continue
        dates, closes, highs, lows, d2i = pidx

        # Entry = first trading day in price history strictly after er_date
        entry_idx = None
        for i, d in enumerate(dates):
            if d > er_date:
                entry_idx = i
                break
        if entry_idx is None:
            skipped_no_post_earn_bar += 1
            continue

        side = 'low' if score_val >= 50 else 'high'  # low = call, high = put
        walk, vol = barrier_walk(dates, closes, highs, lows, entry_idx, side)
        if walk is None:
            skipped_no_walk += 1
            continue

        # Earnings gap: close on entry bar vs close on score date (both pre and post)
        score_i = d2i.get(score_date)
        gap_sigma = None
        if score_i is not None and vol is not None and vol > 0 and closes[score_i] > 0:
            gap_pct = (closes[entry_idx] - closes[score_i]) / closes[score_i] * 100
            if side == 'low':
                gap_sigma = gap_pct / vol  # favorable when rise
            else:
                gap_sigma = -gap_pct / vol  # favorable when drop

        group, bucket = bucket_of(score_val)
        if group is None:
            continue

        key = (group, bucket)
        stats[key]['n'] += 1
        for label, result in walk.items():
            stats[key]['periods'][label][result] += 1
        if gap_sigma is not None:
            stats[key]['earn_gap_sigma'].append(gap_sigma)
        # trading-day offset between score and earnings
        offset = 0
        d = score_date
        while d < er_date:
            d = next_trading_day(d)
            offset += 1
        stats[key]['score_date_offset'].append(offset)
        processed += 1

    print()
    print(f"{Fore.GREEN}Processed signals: {processed}{Style.RESET_ALL}")
    print(f"Skipped - no qualifying score in window: {skipped_no_score}")
    print(f"Skipped - no price history:              {skipped_no_price}")
    print(f"Skipped - no post-earnings bar:          {skipped_no_post_earn_bar}")
    print(f"Skipped - barrier walk failed:           {skipped_no_walk}")
    print()

    # Print win-rate tables
    def print_bucket_table(title, bucket_specs, group_key, random_wr):
        print(f"{Fore.CYAN}=== {title} ===  (random-walk WR ~ {random_wr:.1f}%){Style.RESET_ALL}")
        header = f"{'bucket':<8} {'N':>5}"
        for lbl, _ in PERIODS:
            header += f"  {'WR_'+lbl:>7}"
        header += f"  {'EarnGap_sigma':>14}  {'avgOffsetDays':>13}"
        print(header)
        print('-' * len(header))

        total_n = 0
        total_periods = {lbl: {'win': 0, 'stop': 0, 'expire': 0} for lbl, _ in PERIODS}
        total_gap = []
        total_off = []

        for name, _, _ in bucket_specs:
            s = stats.get((group_key, name))
            if not s or s['n'] == 0:
                print(f"{name:<8} {'0':>5}  " + '  '.join(['    -  '] * len(PERIODS))
                      + f"  {'-':>14}  {'-':>13}")
                continue
            row = f"{name:<8} {s['n']:>5}"
            for lbl, _ in PERIODS:
                p = s['periods'][lbl]
                resolved = p['win'] + p['stop']
                if resolved == 0:
                    row += f"  {'    -  ':>7}"
                    continue
                wr = p['win'] / resolved * 100
                row += f"  {wr:>6.1f}%"
                total_periods[lbl]['win']    += p['win']
                total_periods[lbl]['stop']   += p['stop']
                total_periods[lbl]['expire'] += p['expire']
            gap_str = f"{mean(s['earn_gap_sigma']):>+7.2f}" if s['earn_gap_sigma'] else '   -  '
            off_str = f"{mean(s['score_date_offset']):>4.1f}"   if s['score_date_offset'] else ' -  '
            row += f"  {gap_str:>14}  {off_str:>13}"
            total_n += s['n']
            total_gap.extend(s['earn_gap_sigma'])
            total_off.extend(s['score_date_offset'])
            print(row)

        # Totals row
        if total_n > 0:
            row = f"{'TOTAL':<8} {total_n:>5}"
            for lbl, _ in PERIODS:
                p = total_periods[lbl]
                resolved = p['win'] + p['stop']
                if resolved == 0:
                    row += f"  {'    -  ':>7}"
                else:
                    wr = p['win'] / resolved * 100
                    row += f"  {wr:>6.1f}%"
            gap_str = f"{mean(total_gap):>+7.2f}" if total_gap else '   -  '
            off_str = f"{mean(total_off):>4.1f}"   if total_off else ' -  '
            row += f"  {gap_str:>14}  {off_str:>13}"
            print('-' * len(header))
            print(f"{Fore.YELLOW}{row}{Style.RESET_ALL}")
        print()

    print_bucket_table("CALLS (score >= 75)", CALL_BUCKETS, 'call', RANDOM_WR_CALL)
    print_bucket_table("PUTS (score <= 25)",  PUT_BUCKETS,  'put',  RANDOM_WR_PUT)

    # IV verdict: calls vs the ~71.4% random-walk baseline, puts vs ~66.7%.
    print(f"{Fore.CYAN}=== IV payoff read ==={Style.RESET_ALL}")
    print("Compare bucket WR_30d against the random-walk barrier-touch baseline.")
    print(f"  Call random baseline: {RANDOM_WR_CALL:.1f}%  ")
    print(f"  Put  random baseline: {RANDOM_WR_PUT:.1f}%  ")
    print()
    print("The random baseline is the WR a coin-flipping entry achieves on the same")
    print("K/M barriers in infinite time. To overcome IV crush on ATM options, you")
    print("need WR well above the random baseline AND an EarnGap_sigma that is")
    print("not already heavily in your favor (if the gap is big you've missed the move")
    print("on the option since pre-earnings IV already priced it in).")
    print()
    print("Rules of thumb:")
    print("  WR_30d > random+10pp   : signal survives IV crush comfortably.")
    print("  WR_30d ~ random        : coin-flip-equivalent; IV crush eats EV.")
    print("  WR_30d < random        : anti-signal; do NOT trade options over earnings.")


if __name__ == '__main__':
    main()
