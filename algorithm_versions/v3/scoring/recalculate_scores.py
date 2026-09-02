#! python3
"""Recalculate scores for stocks.
Called via trader.py: python trader.py recalculate [symbol] [days] [--trend] [--bb] [--rsi] [--macd] [--stoch] [--ta] [--weekly] [--overall] [--all]
Args can be in any order. Multiple flags supported. Defaults to --overall."""

from database.models.core import Stock, Score, WeeklyScore
from colorama import init, Fore
from tqdm import tqdm
from datetime import date, timedelta

init(autoreset=True, convert=True)

DEFAULT_LOOKBACK = 365
DAILY_COMPONENTS = {'trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'overall'}
ALL_MODES = DAILY_COMPONENTS | {'weekly'}


def recalculate(symbol, days=DEFAULT_LOOKBACK, components=None):
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

    return stock.recalculate_scores(cutoff, daily)


def run(symbol=None, days=DEFAULT_LOOKBACK, components=None):
    if components is None:
        components = {'overall'}

    if symbol:
        stocks = [Stock.get_or_none(Stock.symbol == symbol)]
        if not stocks[0]:
            print(Fore.RED + f"Stock {symbol} not found")
            return
    else:
        stocks = list(Stock.select().where(Stock.forward_pe.is_null(False)).order_by(Stock.symbol))

    label = '+'.join(sorted(components))
    print(Fore.CYAN + f"Recalculating [{label}] for {len(stocks)} stock(s) ({days}d lookback)...")

    total_u, total_s, total_e = 0, 0, 0
    for stock in tqdm(stocks, desc="Stocks", disable=len(stocks) == 1):
        u, s, e = recalculate(stock.symbol, days, components)
        total_u += u; total_s += s; total_e += e

    print(Fore.GREEN + f"Done: {total_u} updated, {total_s} skipped, {total_e} errors")
