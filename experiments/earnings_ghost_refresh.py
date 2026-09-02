"""Refresh yfinance earnings for symbols whose ambiguous pair was SKIPPED
because the older row is 1..STALE_PAST_DAYS past with no reported_eps yet.

After the re-pull, EPS typically populates on the row that was the real
report — turning the pair into a clear ghost (the OTHER row is the duplicate).
The follow-up `run_cleanup()` then removes those clear ghosts safely.

For pairs where neither row gets EPS after the pull (e.g. yfinance still has
both placeholders), the cadence-anchored resolver runs as a fallback when
--apply-stale-after is passed.

Usage:
    python experiments/earnings_ghost_refresh.py                    # dry-run
    python experiments/earnings_ghost_refresh.py --apply            # pull + delete clear ghosts
    python experiments/earnings_ghost_refresh.py --apply --fallback # also cadence-resolve any leftover recent-past
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
from tqdm import tqdm

from database.models.core import Stock, EarningsDate
from experiments.earnings_ghost_cleanup import (
    skipped_recent_past_symbols,
    classify_pairs,
    run_cleanup,
)


def refresh_symbol(symbol):
    """Pull yfinance metadata for one symbol — refreshes EarningsDate rows.

    Returns (ok, error_msg, deleted_ids).
    """
    try:
        stock = Stock.get(Stock.symbol == symbol)
        before_ids = {
            r.id for r in EarningsDate.select(EarningsDate.id)
            .where(EarningsDate.symbol == symbol)
        }
        stock.pull_stock_data(yf.Ticker(symbol))
        stock.save()
        after_ids = {
            r.id for r in EarningsDate.select(EarningsDate.id)
            .where(EarningsDate.symbol == symbol)
        }
        return True, None, sorted(before_ids - after_ids)
    except Stock.DoesNotExist:
        return False, 'no Stock row', []
    except Exception as e:
        return False, str(e), []


def duplicate_window_symbols(window_days=30, only_unreported=True):
    """Symbols with at least one same-symbol earnings pair within window_days."""
    by_sym = {}
    for row in (
        EarningsDate
        .select(EarningsDate.symbol, EarningsDate.date, EarningsDate.reported_eps)
        .order_by(EarningsDate.symbol, EarningsDate.date)
    ):
        by_sym.setdefault(row.symbol_id, []).append(row)

    syms = set()
    for sym, rows in by_sym.items():
        for i, r1 in enumerate(rows):
            for r2 in rows[i + 1:]:
                gap = (r2.date - r1.date).days
                if gap > window_days:
                    break
                if only_unreported and r1.reported_eps is not None and r2.reported_eps is not None:
                    continue
                syms.add(sym)
                break
            if sym in syms:
                break
    return sorted(syms)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='Actually pull yfinance + delete clear ghosts (default: dry-run pull, no delete)')
    parser.add_argument('--fallback', action='store_true',
                        help='After pull, also delete any remaining recent-past pairs via cadence (RISKY)')
    parser.add_argument('--duplicate-window', action='store_true',
                        help='Refresh symbols with same-symbol earnings rows within --window-days')
    parser.add_argument('--window-days', type=int, default=30,
                        help='Duplicate-window size in calendar days (default: 30)')
    parser.add_argument('--include-reported-pairs', action='store_true',
                        help='When using --duplicate-window, include pairs where both rows already have reported EPS')
    parser.add_argument('--symbols', nargs='*', default=None,
                        help='Override symbol list (default: auto-detect skipped recent-past)')
    args = parser.parse_args()

    if args.symbols:
        syms = args.symbols
    elif args.duplicate_window:
        syms = duplicate_window_symbols(
            window_days=args.window_days,
            only_unreported=not args.include_reported_pairs,
        )
    else:
        syms = skipped_recent_past_symbols()
    if not syms:
        print('No symbols to refresh.')
        return

    print(f'Refreshing yfinance metadata for {len(syms)} symbols...')

    failed = []
    deleted_by_symbol = {}
    for sym in tqdm(syms, ncols=80):
        ok, err, deleted_ids = refresh_symbol(sym)
        if not ok:
            failed.append((sym, err))
        if deleted_ids:
            deleted_by_symbol[sym] = deleted_ids
        time.sleep(0.05)  # be polite to yfinance

    if failed:
        print(f'\n{len(failed)} failed:')
        for sym, err in failed[:20]:
            print(f'  {sym}: {err}')
    if deleted_by_symbol:
        print('\nRows pruned during source refresh:')
        for sym, deleted_ids in sorted(deleted_by_symbol.items()):
            print(f'  {sym}: {deleted_ids}')

    print('\nRe-classifying pairs after refresh...')
    parts = classify_pairs()
    print(f'  CLEAR ghosts now: {len(parts["clear"])}')
    print(f'  AMBIG stale-past: {len(parts["ambig_stale"])}')
    print(f'  AMBIG today-cadence: {len(parts["ambig_today"])}')
    print(f'  AMBIG recent-past (still skipped): {len(parts["ambig_recent"])}')
    print(f'  UNCLEAR resolved: {len(parts["unclear_resolved"])}')

    if not args.apply:
        print('\n[dry-run] Pass --apply to delete clear ghosts surfaced by the refresh.')
        return

    res = run_cleanup(
        apply_clear=True,
        apply_stale=True,
        apply_today=True,
        apply_unclear=True,
        apply_recent=args.fallback,
        verbose=False,
    )
    print(f'\nDeleted {res["deleted"]} rows.')

    # Show what's left in the recent-past bucket
    leftover = classify_pairs()['ambig_recent']
    if leftover:
        print(f'\n{len(leftover)} recent-past pairs remain:')
        for sym, r1, r2 in leftover[:20]:
            print(f'  {sym}: {r1.date} (eps={r1.reported_eps}) vs {r2.date} (eps={r2.reported_eps})')


if __name__ == '__main__':
    main()
