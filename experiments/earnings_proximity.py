"""
Earnings-proximity accuracy analysis
=====================================

Splits peaks into two groups and compares assessment metrics:

    NEAR-EARNINGS : peak date is 1–5 calendar days before an earnings announcement
    BASELINE      : all other peaks

Usage:
    python -m experiments.earnings_proximity              # 2y lookback, all symbols
    python -m experiments.earnings_proximity 1y           # 1y lookback
    python -m experiments.earnings_proximity 730          # raw days
    python -m experiments.earnings_proximity AAPL MSFT    # specific symbols
    python -m experiments.earnings_proximity AAPL 730     # symbol + lookback
    python -m experiments.earnings_proximity --window 1 7 # custom day-before window (default 1–5)
"""

import sys
from collections import defaultdict
from datetime import date, timedelta

from colorama import Fore, Style

from database.models.core import EarningsDate, AlgorithmVersion
from database.models.technical import PriceHistory
from assess_scores import (
    extract_peaks,
    assess_peaks_in_memory,
    print_diff_assessment,
    THRESHOLD_KEYS,
    PERIOD_LABELS,
)


# ── CLI parsing ─────────────────────────────────────────────────────────────

def _parse_args(argv):
    args = argv[1:]
    lookback_days = 730
    symbols = []
    win_min = 1   # inclusive days-before-earnings lower bound
    win_max = 5   # inclusive days-before-earnings upper bound

    i = 0
    while i < len(args):
        a = args[i]
        if a == '--window' and i + 2 < len(args):
            win_min = int(args[i + 1])
            win_max = int(args[i + 2])
            i += 3
            continue
        # lookback token: ends with d/y or is a bare integer
        s = a.strip().lower()
        if s.isdigit():
            lookback_days = int(s)
        elif len(s) >= 2 and s[:-1].isdigit():
            n, suf = int(s[:-1]), s[-1]
            if suf == 'd':
                lookback_days = n
            elif suf == 'w':
                lookback_days = n * 7
            elif suf == 'y':
                lookback_days = n * 365
            else:
                symbols.append(a.upper())
        else:
            symbols.append(a.upper())
        i += 1

    return lookback_days, symbols or None, win_min, win_max


# ── Earnings proximity helpers ───────────────────────────────────────────────

def _load_earnings_by_symbol(symbols, cutoff, lookahead=7):
    """Return dict[symbol -> sorted list of earnings dates] within [cutoff, today + lookahead]."""
    hi = date.today() + timedelta(days=lookahead)
    q = EarningsDate.select(EarningsDate.symbol, EarningsDate.date).where(
        EarningsDate.date >= cutoff,
        EarningsDate.date <= hi,
    )
    if symbols:
        q = q.where(EarningsDate.symbol.in_(symbols))

    by_sym = defaultdict(list)
    for row in q:
        by_sym[row.symbol_id].append(row.date)
    for sym in by_sym:
        by_sym[sym].sort()
    return by_sym


def days_to_next_earnings(peak_date, earnings_list):
    """Return calendar days from peak_date to the next earnings date on or after peak_date+1.
    Returns None if there is no upcoming earnings date in the list."""
    for ed in earnings_list:
        if ed > peak_date:
            return (ed - peak_date).days
    return None


def tag_earnings_proximity(peaks, earnings_by_sym, win_min, win_max):
    """Split peaks into (near_earnings, baseline) lists.

    near_earnings: days_to_next_earnings in [win_min, win_max]
    baseline     : everything else (including no upcoming earnings data)
    """
    near, baseline = [], []
    for p in peaks:
        ed_list = earnings_by_sym.get(p.symbol_id, [])
        dte = days_to_next_earnings(p.date, ed_list)
        if dte is not None and win_min <= dte <= win_max:
            near.append(p)
        else:
            baseline.append(p)
    return near, baseline


# ── Price history loader ─────────────────────────────────────────────────────

def _load_price_history(symbols, cutoff):
    lo = cutoff - timedelta(days=120)  # need back-history for vol calculation
    q = PriceHistory.select(
        PriceHistory.symbol, PriceHistory.date,
        PriceHistory.close, PriceHistory.high, PriceHistory.low,
    ).where(
        PriceHistory.date >= lo,
    )
    if symbols:
        q = q.where(PriceHistory.symbol.in_(symbols))

    ph_by_sym = defaultdict(list)
    for row in q.order_by(PriceHistory.symbol, PriceHistory.date):
        ph_by_sym[row.symbol_id].append(row)
    return ph_by_sym


# ── Compact per-group summary ────────────────────────────────────────────────

def _print_group_summary(label, data):
    stats = data['bucketed_stats']

    def _wr(k):
        v = stats[k].get('win_rate_30d')
        return v if v is not None else float('nan')

    def _ev(k):
        v = stats[k].get('avg_return_30d')
        return v if v is not None else float('nan')

    def _n(k):
        return stats[k].get('sample_count') or 0

    call_keys = [k for k in THRESHOLD_KEYS if '+' in k]
    put_keys  = [k for k in THRESHOLD_KEYS if '<'  in k]

    print(f"\n{Fore.CYAN}{label}{Style.RESET_ALL}  "
          f"(peaks: {data['n_peaks']}, priced: {data['n_results']})")
    print(f"  {'Bucket':<8}  {'WR30':>6}  {'EV30':>7}  {'N':>5}")
    print(f"  {'------':<8}  {'----':>6}  {'----':>7}  {'-':>5}")
    for k in call_keys + put_keys:
        wr = _wr(k)
        ev = _ev(k)
        n  = _n(k)
        if n == 0:
            print(f"  {k:<8}  {'--':>6}  {'--':>7}  {n:>5}")
            continue
        wr_str = f"{wr:>5.1f}%" if wr == wr else "  n/a "  # nan check
        wr_col = (Fore.GREEN if wr == wr and wr > 70
                  else Fore.RED if wr == wr and wr < 55
                  else Fore.YELLOW)
        ev_str = f"{ev:>+6.2f}%" if ev == ev else "    n/a"
        print(f"  {k:<8}  {wr_col}{wr_str}{Style.RESET_ALL}  {ev_str}  {n:>5}")


# ── Main ─────────────────────────────────────────────────────────────────────

def run(argv=None):
    lookback_days, symbols, win_min, win_max = _parse_args(argv or sys.argv)
    cutoff = date.today() - timedelta(days=lookback_days)

    print(f"\n{Fore.CYAN}Earnings Proximity Analysis{Style.RESET_ALL}")
    print(f"  Lookback   : {lookback_days}d  (since {cutoff})")
    print(f"  Window     : {win_min}–{win_max} calendar days before earnings")
    if symbols:
        print(f"  Symbols    : {', '.join(symbols)}")

    # Resolve active version
    version = AlgorithmVersion.get_active_scores_version()
    if not version:
        print(f"{Fore.RED}No active algorithm version found.{Style.RESET_ALL}")
        return
    print(f"  Version    : v{version.id} ({version.git_commit})")

    # Load peaks
    print(f"\nLoading peaks...")
    peaks = extract_peaks(
        symbol=symbols[0] if symbols and len(symbols) == 1 else None,
        lookback_days=lookback_days,
        version=version,
    )
    if symbols and len(symbols) > 1:
        sym_set = set(s.upper() for s in symbols)
        peaks = [p for p in peaks if p.symbol_id in sym_set]

    if not peaks:
        print(f"{Fore.YELLOW}No peaks found for this version/window.{Style.RESET_ALL}")
        return

    print(f"  Total peaks: {len(peaks)}")

    # Load earnings dates
    all_syms = list({p.symbol_id for p in peaks})
    earnings_by_sym = _load_earnings_by_symbol(
        all_syms, cutoff, lookahead=win_max + 2,
    )
    syms_with_earnings = len(earnings_by_sym)
    print(f"  Symbols with earnings data: {syms_with_earnings} / {len(all_syms)}")

    # Split
    near, baseline = tag_earnings_proximity(peaks, earnings_by_sym, win_min, win_max)
    print(f"\n  Near-earnings peaks ({win_min}–{win_max}d before) : {len(near)}")
    print(f"  Baseline peaks                               : {len(baseline)}")

    if len(near) < 10:
        print(f"\n{Fore.YELLOW}Warning: only {len(near)} near-earnings peaks — results may be noisy.{Style.RESET_ALL}")

    # Load price history once for all symbols
    print(f"\nLoading price history...")
    ph_by_sym = _load_price_history(all_syms, cutoff)

    # Assess both groups
    print(f"\nAssessing baseline ({len(baseline)} peaks)...")
    baseline_data = assess_peaks_in_memory(baseline, ph_by_sym, lookback_days)

    print(f"Assessing near-earnings ({len(near)} peaks)...")
    near_data = assess_peaks_in_memory(near, ph_by_sym, lookback_days)

    # Print results
    _print_group_summary("BASELINE (no imminent earnings)", baseline_data)
    _print_group_summary(f"NEAR-EARNINGS ({win_min}–{win_max}d before)", near_data)

    print(f"\n{Fore.CYAN}--- Diff: Near-Earnings vs Baseline ---{Style.RESET_ALL}")
    print_diff_assessment(baseline_data, near_data,
                          label_old='Baseline',
                          label_new=f'Near-Earnings ({win_min}-{win_max}d)')

    # Day-by-day breakdown
    print(f"\n{Fore.CYAN}--- Day-by-day breakdown ---{Style.RESET_ALL}")
    print(f"  {'Days-before':>12}  {'Peaks':>6}  {'WR30 (all buckets avg)':>22}")
    for d_before in range(win_min, win_max + 1):
        day_peaks = []
        for p in near:
            ed_list = earnings_by_sym.get(p.symbol_id, [])
            dte = days_to_next_earnings(p.date, ed_list)
            if dte == d_before:
                day_peaks.append(p)
        if not day_peaks:
            continue
        d_data = assess_peaks_in_memory(day_peaks, ph_by_sym, lookback_days)
        wr30_vals = [
            s.get('win_rate_30d')
            for s in d_data['bucketed_stats'].values()
            if (s.get('sample_count') or 0) >= 5 and s.get('win_rate_30d') is not None
        ]
        avg_wr30 = sum(wr30_vals) / len(wr30_vals) if wr30_vals else None
        wr_str = f"{avg_wr30:.1f}%" if avg_wr30 is not None else "n/a"
        wr_col = (Fore.GREEN if avg_wr30 and avg_wr30 > 70
                  else Fore.RED if avg_wr30 and avg_wr30 < 55
                  else Fore.YELLOW)
        print(f"  {d_before:>12}d  {len(day_peaks):>6}  {wr_col}{wr_str:>22}{Style.RESET_ALL}")


if __name__ == '__main__':
    run()
