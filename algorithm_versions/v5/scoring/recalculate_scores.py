#! python3
"""Recalculate scores for stocks.
Called via trader.py: python trader.py recalculate [symbol] [days] [--trend] [--bb] [--rsi] [--macd] [--stoch] [--ta] [--weekly] [--overall] [--all]
                                                   [--workers N] [--no-batch]
Args can be in any order. Multiple flags supported. Defaults to --overall.

--workers N   : parallel workers (default: 1, i.e. serial). Use 4-8 on a typical machine.
--no-batch    : use the original per-row DB query path instead of the batched path.
"""

from database.models.core import Stock, Score, WeeklyScore
from colorama import init, Fore
from tqdm import tqdm
from datetime import date, timedelta
import multiprocessing
import os

init(autoreset=True, convert=True)

DEFAULT_LOOKBACK = 365
DAILY_COMPONENTS = {'trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'overall'}
ALL_MODES = DAILY_COMPONENTS | {'weekly'}


def recalculate(symbol, days=DEFAULT_LOOKBACK, components=None, use_batch=True):
    if components is None:
        components = {'overall'}

    stock = Stock.get(Stock.symbol == symbol)
    cutoff = date.today() - timedelta(days=days)

    if 'weekly' in components:
        wu, we = stock.recalculate_weekly_scores(cutoff)
        print(f"  weekly: {wu} updated, {we} errors")

    daily = components & DAILY_COMPONENTS
    if not daily:
        return 0, 0, 0

    if use_batch:
        return stock.recalculate_scores_batched(cutoff, daily)
    else:
        return stock.recalculate_scores(cutoff, daily)


# ---------------------------------------------------------------------------
# Multiprocessing worker
# ---------------------------------------------------------------------------

def _worker_init():
    """Re-open the DB connection in each worker process."""
    from database.trader_database import DB
    if DB.is_closed():
        DB.connect()


def _worker(args):
    symbol, days, components, use_batch = args
    try:
        u, s, e = recalculate(symbol, days, components, use_batch)
        return symbol, u, s, e, None
    except Exception as exc:
        return symbol, 0, 0, 1, str(exc)


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def run(symbol=None, days=DEFAULT_LOOKBACK, components=None,
        workers=1, use_batch=True):
    if components is None:
        components = {'overall'}

    if symbol:
        stocks = [Stock.get_or_none(Stock.symbol == symbol)]
        if not stocks[0]:
            print(Fore.RED + f"Stock {symbol} not found")
            return
    else:
        stocks = list(Stock.select().order_by(Stock.symbol))

    label = '+'.join(sorted(components))
    mode_tag = 'batch' if use_batch else 'per-row'
    workers_tag = f'{workers}w' if workers > 1 else 'serial'
    print(Fore.CYAN + f"Recalculating [{label}] for {len(stocks)} stock(s) "
          f"({days}d lookback, {mode_tag}, {workers_tag}) …")

    total_u, total_s, total_e = 0, 0, 0

    if workers > 1:
        args_list = [(s.symbol, days, components, use_batch) for s in stocks]
        with multiprocessing.Pool(
            processes=workers,
            initializer=_worker_init,
        ) as pool:
            for sym, u, s, e, err in tqdm(
                pool.imap_unordered(_worker, args_list),
                total=len(args_list), desc="Stocks",
            ):
                if err:
                    print(Fore.RED + f"  {sym}: {err}")
                total_u += u; total_s += s; total_e += e
    else:
        for stock in tqdm(stocks, desc="Stocks", disable=len(stocks) == 1):
            u, s, e = recalculate(stock.symbol, days, components, use_batch)
            total_u += u; total_s += s; total_e += e

    print(Fore.GREEN + f"Done: {total_u} updated, {total_s} skipped, {total_e} errors")
