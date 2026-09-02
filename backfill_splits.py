#! python3
"""
One-time script to retroactively fix price history for all stocks that have had
stock splits while auto_adjust=False was active.

For each stock:
  - Builds split list from split_events (DB) plus any Yahoo dates not already stored
  - For each known split (DB + Yahoo) after your earliest bar, if prices are not
    yet split-adjusted (or after a full wipe when repair is needed):
      1. Deletes affected rows (from cutoff_date onward if --lookback, else full symbol)
      2. Re-pulls price history with split-adjusted closes
      3. Recalculates indicators and scores
      4. Upserts split_events

Run once after deploying the split-handling changes. Safe to re-run (idempotent).

--lookback: limit how far back to go (e.g. 3y, 2y, 365d). Omit for full history.
"""

from colorama import init, Fore
from datetime import datetime, date, timedelta
from database import Stock, PriceHistory, Indicator, Score, WeeklyScore, SplitEvent
from peewee import fn
from database.models.technical import WeeklyPriceHistory, WeeklyIndicator
from trader import Trader

init(autoreset=True, convert=True)


def _parse_lookback(lookback: str):
    """Parse lookback string like '3y', '365d', '2y'. Returns timedelta or None."""
    if not lookback:
        return None
    s = lookback.strip().lower()
    if s.isdigit():
        return timedelta(days=int(s))
    if len(s) >= 2 and s[:-1].isdigit():
        n = int(s[:-1])
        suf = s[-1]
        if suf == 'd':
            return timedelta(days=n)
        if suf == 'w':
            return timedelta(weeks=n)
        if suf == 'y':
            return timedelta(days=n * 365)
    raise ValueError(f"Cannot parse lookback '{lookback}'. Use e.g. '3y', '365d', '2y'.")


def backfill_splits(*symbols: str, dry_run: bool = False, lookback: str = None):
    trader = Trader()
    SplitEvent.ensure_schema()
    stocks = [Stock.get(Stock.symbol == s) for s in symbols] if symbols else list(Stock.select())
    total = len(stocks)
    affected = 0

    cutoff_date = None
    if lookback:
        delta = _parse_lookback(lookback)
        cutoff_date = date.today() - delta
        print(Fore.CYAN + f'Lookback: {lookback} → processing from {cutoff_date} onward')

    for i, stock in enumerate(stocks, 1):
        sym = stock.symbol
        print(f'[{i}/{total}] {sym} ', end='', flush=True)

        try:
            events = Trader.fetch_split_events(sym)
        except Exception as e:
            print(Fore.RED + f'error fetching splits: {e}')
            continue

        if not events:
            print('· no splits')
            continue

        earliest = PriceHistory.select(fn.MIN(PriceHistory.date)).where(PriceHistory.symbol == sym).scalar()
        if not earliest:
            print('· no price history')
            continue
        if isinstance(earliest, datetime):
            earliest = earliest.date()

        # Do not skip splits already in split_events — we still need to verify prices
        # (imported rows or stale data) via already_reflected / needs_repull.
        unprocessed = [
            (d, float(ratio))
            for d, ratio in events
            if float(ratio) > 0
            and d > earliest
            and (cutoff_date is None or d >= cutoff_date)
        ]

        if not unprocessed:
            print('· no splits after data start date')
            continue

        # Check which splits are not yet reflected in stored prices
        def already_reflected(split_date, ratio):
            if ratio < 1.15:
                return True  # too small to detect reliably; impact is minimal
            pre = (PriceHistory.select()
                   .where((PriceHistory.symbol == sym) & (PriceHistory.date < split_date))
                   .order_by(PriceHistory.date.desc()).first())
            post = (PriceHistory.select()
                    .where((PriceHistory.symbol == sym) & (PriceHistory.date >= split_date))
                    .order_by(PriceHistory.date.asc()).first())
            if not pre or not post:
                return True
            discontinuity = float(pre.close) / float(post.close)
            # Already adjusted = prices continuous (pre/post ≈ 1.0)
            return abs(discontinuity - 1.0) < 0.15

        needs_repull = [(d, r) for d, r in unprocessed if not already_reflected(d, r)]
        already_ok   = [(d, r) for d, r in unprocessed if already_reflected(d, r)]

        all_split_dates = {d: float(ratio) for d, ratio in events if float(ratio) > 0}

        if already_ok and not needs_repull:
            for d, r in already_ok:
                print(f'  · {r}-for-1 on {d} — already reflected in prices, registered')
            if not dry_run:
                for split_date, ratio in all_split_dates.items():
                    SplitEvent.get_or_create(symbol=sym, split_date=split_date,
                                             defaults={'ratio': ratio, 'processed_at': datetime.now()})
            affected += 1
            continue

        labels = ', '.join(f'{r}-for-1 on {d}' for d, r in needs_repull)
        print(Fore.YELLOW + f'  needs re-pull: {labels}')
        if already_ok:
            ok_labels = ', '.join(f'{r}-for-1 on {d}' for d, r in already_ok)
            print(f'  already reflected: {ok_labels}')

        if dry_run:
            affected += 1
            continue

        # Wipe downstream data (from cutoff_date if set, else all)
        if cutoff_date:
            PriceHistory.delete().where((PriceHistory.symbol == sym) & (PriceHistory.date >= cutoff_date)).execute()
            Indicator.delete().where((Indicator.symbol == sym) & (Indicator.date >= cutoff_date)).execute()
            WeeklyPriceHistory.delete().where((WeeklyPriceHistory.symbol == sym) & (WeeklyPriceHistory.date >= cutoff_date)).execute()
            WeeklyIndicator.delete().where((WeeklyIndicator.symbol == sym) & (WeeklyIndicator.date >= cutoff_date)).execute()
            Score.delete().where((Score.symbol == sym) & (Score.date >= cutoff_date)).execute()
            WeeklyScore.delete().where((WeeklyScore.symbol == sym) & (WeeklyScore.date >= cutoff_date)).execute()
        else:
            PriceHistory.delete().where(PriceHistory.symbol == sym).execute()
            Indicator.delete().where(Indicator.symbol == sym).execute()
            WeeklyPriceHistory.delete().where(WeeklyPriceHistory.symbol == sym).execute()
            WeeklyIndicator.delete().where(WeeklyIndicator.symbol == sym).execute()
            Score.delete().where(Score.symbol == sym).execute()
            WeeklyScore.delete().where(WeeklyScore.symbol == sym).execute()

        # Re-pull adjusted history (from cutoff_date if set, else full)
        if cutoff_date:
            trader.pull_price_history(sym, start=cutoff_date)
        else:
            trader.pull_price_history(sym, period='max')

        # Recalculate
        stock.calculate_indicators(full=True, silent=True)
        stock.calculate_indicators(full=True, weekly=True, silent=True)
        stock.calculate_scores(full=True, weekly=True, silent=True)
        stock.calculate_scores_batched(silent=True)  # fast batch path for daily scores

        for split_date, ratio in all_split_dates.items():
            SplitEvent.get_or_create(
                symbol=sym,
                split_date=split_date,
                defaults={'ratio': ratio, 'processed_at': datetime.now()}
            )

        print(Fore.GREEN + f'  done — {len(needs_repull)} split(s) applied, full recalc complete')
        affected += 1

    print()
    if dry_run:
        print(Fore.CYAN + f'Dry run complete. {affected}/{total} stocks would be updated.')
    else:
        print(Fore.GREEN + f'Backfill complete. {affected}/{total} stocks updated.')


if __name__ == '__main__':
    import defopt
    defopt.run(backfill_splits)
