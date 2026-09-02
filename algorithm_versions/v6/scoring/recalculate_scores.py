#! python3
"""Recalculate scores for stocks.
Called via trader.py: python trader.py recalculate [symbol] [days] [--trend] [--bb] [--rsi] [--macd] [--stoch] [--ta] [--weekly] [--overall] [--all]
                                                   [--workers N] [--no-batch] [--force] [--full]
Args can be in any order. Multiple flags supported. Defaults to --overall.

With no symbol and no explicit day lookback, runs **staged** recalculation (7d → 30d → 1y → 2y → 3y):
each stage persists a disjoint date band; every stage walks from the 3y cutoff forward so volume
decay state stays correct. Stages do not write the same score dates twice.

--workers N   : parallel workers (default: 1, i.e. serial). Use 4-8 on a typical machine.
--no-batch    : use the original per-row DB query path instead of the batched path.
--force       : recalculate all scores even if already computed for the current version.
--full        : extend progressive passes from 3y to 5y, 10y, 25y.
"""

from database.models.core import Stock
from colorama import init, Fore
from datetime import date, timedelta
import multiprocessing
import sys

init(autoreset=True, convert=True)

DEFAULT_LOOKBACK = 365
DAILY_COMPONENTS = {'trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'overall'}
ALL_MODES = DAILY_COMPONENTS | {'weekly'}

# (outer_days, inner_days) → persist dates in [today−outer, today−inner) with inner None ⇒ no upper bound
STAGED_DEPTHS = [(7, None), (30, 7), (365, 30), (730, 365), (1095, 730)]
STAGED_LABELS = [
    'last 7d',
    'days 8–30',
    'days 31–365 (1y)',
    'years 1–2',
    'years 2–3',
]

def recalculate(symbol, days=DEFAULT_LOOKBACK, components=None, use_batch=True,
                cutoff_end=None, force=False):
    if components is None:
        components = {'overall'}

    stock = Stock.get_or_none(Stock.symbol == symbol)
    if not stock:
        print(Fore.RED + f"Stock {symbol} not found")
        return 0, 0, 0

    cutoff_start = date.today() - timedelta(days=days)

    weekly_u = weekly_e = 0
    if 'weekly' in components:
        weekly_u, weekly_e = stock.recalculate_weekly_scores(cutoff_start)

    daily = components & DAILY_COMPONENTS
    if not daily:
        return weekly_u, 0, weekly_e

    u, s, e = (stock.recalculate_scores_full(cutoff_start, daily,
                                               cutoff_end=cutoff_end, force=force)
               if use_batch else stock.recalculate_scores(cutoff_start, daily))
    return u + weekly_u, s, e + weekly_e


def _worker_init():
    """Re-open the DB connection in each worker process."""
    from database.trader_database import DB
    if DB.is_closed():
        DB.connect()


def _worker(args):
    symbol, days, components, use_batch, cutoff_end, force = args
    try:
        u, s, e = recalculate(symbol, days, components, use_batch,
                               cutoff_end=cutoff_end, force=force)
        return symbol, u, s, e, None
    except Exception as exc:
        return symbol, 0, 0, 1, str(exc)


def _run_one_stage(stocks, load_cutoff, persist_lo, persist_hi_exclusive, components,
                   workers, use_batch, stage_desc, show_inner):
    label = '+'.join(sorted(components))
    mode_tag = 'batch' if use_batch else 'per-row'
    workers_tag = f'{workers}w' if workers > 1 else 'serial'
    hi_note = ''
    if persist_hi_exclusive is not None:
        hi_note = f", persist < {persist_hi_exclusive}"
    print(Fore.CYAN + f"  {stage_desc} [{label}] ({mode_tag}, {workers_tag}) "
          f"load≥{load_cutoff}{hi_note}")

def run(symbol=None, days=DEFAULT_LOOKBACK, components=None,
        workers=1, use_batch=True, cutoff_end=None, force=False):
    if components is None:
        components = {'overall'}

    if symbol:
        stocks = [Stock.get_or_none(Stock.symbol == symbol)]
        if not stocks[0]:
            print(Fore.RED + f"Stock {symbol} not found")
            return
    else:
        stocks = list(Stock.select().order_by(Stock.symbol))

    label      = '+'.join(sorted(components))
    mode_tag   = 'batch' if use_batch else 'per-row'
    workers_tag = f'{workers}w' if workers > 1 else 'serial'
    force_tag  = ' [force]' if force else ''
    print(Fore.CYAN + f"Recalculating [{label}] for {len(stocks)} stock(s) "
          f"({days}d lookback, {mode_tag}, {workers_tag}){force_tag} …")

    total_u, total_s, total_e = 0, 0, 0
    show_inner = len(stocks) == 1 and workers == 1
    n = len(stocks)

    if workers > 1:
        args_list = [(s.symbol, days, components, use_batch, cutoff_end, force)
                     for s in stocks]
        with multiprocessing.Pool(
            processes=workers,
            initializer=_worker_init,
        ) as pool:
            for sym, u, s, e, err in tqdm(
                pool.imap_unordered(_worker, args_list),
                total=len(args_list), desc="Stocks",
            ):
                if err:
                    _clear_progress_line()
                    print(Fore.RED + f"  {sym}: {err}")
                total_u += u
                total_s += s
                total_e += e
        _clear_progress_line()
    else:
        for stock in tqdm(stocks, desc="Stocks", disable=len(stocks) == 1):
            u, s, e = recalculate(stock.symbol, days, components, use_batch,
                                   cutoff_end=cutoff_end, force=force)
            total_u += u; total_s += s; total_e += e

    print(Fore.GREEN + f"Done: {total_u} updated, {total_s} skipped, {total_e} errors")
