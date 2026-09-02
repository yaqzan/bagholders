#! python3
"""Recalculate scores for stocks.
Called via trader.py: python trader.py recalculate [symbol] [days] [--trend] [--bb] [--rsi] [--macd] [--stoch] [--ta] [--weekly] [--overall] [--all]
                                                   [--workers N] [--no-batch] [--force] [--full]
Args can be in any order. Multiple flags supported. Defaults to --overall.

With no symbol and no explicit day lookback, `trader recalculate` runs **staged** passes
(1d → 7d → 30d → 1y → 2y → 3y → 5y → 7y): each pass persists a disjoint date band; each pass
loads from its lookback through the slice upper bound so volume decay stays correct. With
`force=False`, already-computed dates are not redone.

--workers N   : parallel workers. Defaults to min(cpu_count, 8). Pass 1 to force serial.
--no-batch    : use the original per-row DB query path instead of the batched path.
--force       : recalculate all scores even if already computed for the current version.
--full        : extend progressive passes after 7y with 10y and 25y.
"""

from database.models.core import Stock, MarketRegime
from colorama import init, Fore
from datetime import date, timedelta
import multiprocessing
import os
from tqdm import tqdm

init(autoreset=True, convert=True)

DEFAULT_LOOKBACK = 365
DAILY_COMPONENTS = {'trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'overall'}
ALL_MODES = DAILY_COMPONENTS | {'weekly', 'dte'}

# Mirror of trader.py progressive passes (outer_days, inner_days) → band [today−outer, today−inner]
STAGED_DEPTHS = [
    (1, None), (7, 1), (30, 7), (365, 30), (730, 365), (1095, 730),
    (1825, 1095), (2555, 1825),
]
STAGED_LABELS = [
    'last 1d', 'days 2–7', 'days 8–30', 'days 31–365 (1y)',
    'years 1–2', 'years 2–3', 'years 3–5', 'years 5–7',
]

# Module-level regime map cache — populated once per process (main + each worker)
# to avoid re-querying MarketRegime per stock (was 470× identical queries).
_SHARED_REGIME_MAP = None


def _default_workers():
    """Auto-pick worker count: min(cpu_count, 8). Serial if cpu_count unknown."""
    try:
        n = os.cpu_count() or 1
    except Exception:
        n = 1
    return max(1, min(n, 8))


def _load_regime_map():
    """Load MarketRegime rows into {date: (multiplier, composite)}."""
    rows = list(
        MarketRegime.select(
            MarketRegime.date, MarketRegime.regime_multiplier, MarketRegime.regime_composite
        )
        .where(MarketRegime.regime_multiplier.is_null(False))
        .namedtuples()
    )
    return {
        r.date: (
            float(r.regime_multiplier),
            float(r.regime_composite) if r.regime_composite is not None else None,
        )
        for r in rows
    }


def recalculate(symbol, days=DEFAULT_LOOKBACK, components=None, use_batch=True,
                cutoff_end=None, force=False, inner_progress=True, regime_map=None):
    if components is None:
        components = {'overall'}

    stock = Stock.get_or_none(Stock.symbol == symbol)
    if not stock:
        print(Fore.RED + f"Stock {symbol} not found")
        return 0, 0, 0

    cutoff_start = date.today() - timedelta(days=days)

    weekly_u = weekly_e = 0
    if 'weekly' in components:
        weekly_u, weekly_e = stock.recalculate_weekly_scores(
            cutoff_start, show_progress=inner_progress, cutoff_end=cutoff_end)

    dte_u = dte_s = dte_e = 0
    if 'dte' in components:
        from recalculate_dte import recalculate_dte_for_stock
        dte_u, dte_s, dte_e = recalculate_dte_for_stock(
            symbol, days=days, cutoff_end=cutoff_end, force=force,
            show_progress=inner_progress,
        )

    daily = components & DAILY_COMPONENTS
    if not daily:
        return weekly_u + dte_u, dte_s, weekly_e + dte_e

    u, s, e = (
        stock.recalculate_scores_full(
            cutoff_start, daily, cutoff_end=cutoff_end, force=force,
            show_progress=inner_progress, regime_map=regime_map,
        )
        if use_batch
        else stock.recalculate_scores(
            cutoff_start, daily, show_progress=inner_progress)
    )
    return u + weekly_u + dte_u, s + dte_s, e + weekly_e + dte_e


def _worker_init():
    """Re-open the DB connection in each worker process and preload regime map."""
    global _SHARED_REGIME_MAP
    from database.trader_database import DB
    if DB.is_closed():
        DB.connect()
    _SHARED_REGIME_MAP = _load_regime_map()


def _worker(args):
    symbol, days, components, use_batch, cutoff_end, force = args
    try:
        u, s, e = recalculate(symbol, days, components, use_batch,
                               cutoff_end=cutoff_end, force=force,
                               inner_progress=False,
                               regime_map=_SHARED_REGIME_MAP)
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
        workers=None, use_batch=True, cutoff_end=None, force=False):
    """workers=None → auto (min(cpu_count, 8)). Pass 1 for serial."""
    global _SHARED_REGIME_MAP
    if components is None:
        components = {'overall'}
    if workers is None:
        workers = _default_workers()

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
          f"({days}d lookback, {mode_tag}, {workers_tag}){force_tag} ...")

    # Load regime map once for the whole run (serial path reuses this directly;
    # worker processes re-load via _worker_init since it isn't picklable across processes).
    _SHARED_REGIME_MAP = _load_regime_map()

    total_u, total_s, total_e = 0, 0, 0
    multi = len(stocks) > 1
    inner_progress = (not multi) and workers == 1

    if workers > 1:
        args_list = [(s.symbol, days, components, use_batch, cutoff_end, force)
                     for s in stocks]
        with multiprocessing.Pool(
            processes=workers,
            initializer=_worker_init,
        ) as pool:
            it = pool.imap_unordered(_worker, args_list)
            if multi:
                pbar = tqdm(
                    it, total=len(args_list), desc="Stocks", unit="stk",
                    mininterval=0.25, smoothing=0.05, dynamic_ncols=True,
                    ascii=True,
                )
            else:
                pbar = it
            for row in pbar:
                sym, u, s, e, err = row
                if multi:
                    pbar.set_postfix_str(sym, refresh=False)
                if err:
                    tqdm.write(Fore.RED + f"  {sym}: {err}")
                total_u += u
                total_s += s
                total_e += e
    else:
        if multi:
            pbar = tqdm(
                total=len(stocks), desc="Stocks", unit="stk",
                mininterval=0.25, smoothing=0.05, dynamic_ncols=True,
                ascii=True,
            )
            for stock in stocks:
                pbar.set_postfix_str(stock.symbol, refresh=False)
                u, s, e = recalculate(
                    stock.symbol, days, components, use_batch,
                    cutoff_end=cutoff_end, force=force,
                    inner_progress=inner_progress,
                    regime_map=_SHARED_REGIME_MAP,
                )
                total_u += u
                total_s += s
                total_e += e
                pbar.update(1)
            pbar.close()
        else:
            for stock in stocks:
                u, s, e = recalculate(
                    stock.symbol, days, components, use_batch,
                    cutoff_end=cutoff_end, force=force,
                    inner_progress=inner_progress,
                    regime_map=_SHARED_REGIME_MAP,
                )
                total_u += u
                total_s += s
                total_e += e

    print(Fore.GREEN + f"Done: {total_u} updated, {total_s} skipped, {total_e} errors")
