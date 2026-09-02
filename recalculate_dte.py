#! python3
"""Recalculate persisted DTE recommendations.

Walks Score rows for the active algorithm version, computes recommend_dte()
per (symbol, date), and upserts a DteRecommendation row.

Idempotent: rows that already exist for the current version are skipped
unless force=True is passed.

Same staged-pass interface as recalculate_scores.run() so trader.py's
`recalculate` command can dispatch to either path with one shared loop.
"""
from datetime import date, timedelta
from colorama import Fore
from tqdm import tqdm

from database import Score, DteRecommendation, Stock
from database.models.core import AlgorithmVersion
from dte_recommendation import recommend_dte, get_signal_context


DEFAULT_LOOKBACK = 365


def _ensure_table():
    DteRecommendation.ensure_schema()


def recalculate_dte_for_stock(symbol: str, days: int = DEFAULT_LOOKBACK,
                              cutoff_end: date | None = None, force: bool = False,
                              show_progress: bool = True):
    """Compute + persist DteRecommendation for `symbol` over the date band
    [today − days, cutoff_end). Skips dates that already have a row for the
    current version unless force=True.

    Returns (updated, skipped, errors).
    """
    _ensure_table()
    version = AlgorithmVersion.get_or_create_current()

    today = date.today()
    cutoff_start = today - timedelta(days=days)
    upper = cutoff_end if cutoff_end is not None else today + timedelta(days=1)

    score_rows = list(
        Score.select(Score.date, Score.overall)
        .where(
            (Score.symbol == symbol)
            & (Score.version == version)
            & (Score.date >= cutoff_start)
            & (Score.date < upper)
            & (Score.overall.is_null(False))
        )
        .order_by(Score.date.asc())
    )
    if not score_rows:
        return 0, 0, 0

    if not force:
        existing = {
            r.date for r in DteRecommendation.select(DteRecommendation.date).where(
                (DteRecommendation.symbol == symbol)
                & (DteRecommendation.version == version)
                & (DteRecommendation.date >= cutoff_start)
                & (DteRecommendation.date < upper)
            )
        }
    else:
        existing = set()

    updated = skipped = errors = 0
    iterator = score_rows
    if show_progress:
        iterator = tqdm(score_rows, desc=f"{symbol} dte", unit="d",
                        mininterval=0.25, dynamic_ncols=True, leave=False)

    for row in iterator:
        if row.date in existing:
            skipped += 1
            continue
        try:
            ctx = get_signal_context(symbol, target_date=row.date)
            if ctx is None:
                skipped += 1
                continue
            rec = recommend_dte(**ctx)
            DteRecommendation.upsert_from_recommendation(symbol, row.date, rec)
            updated += 1
        except Exception as exc:
            errors += 1
            if show_progress:
                tqdm.write(Fore.RED + f"  {symbol} {row.date}: {exc}")

    return updated, skipped, errors


def run(symbol: str | None = None, days: int = DEFAULT_LOOKBACK,
        cutoff_end: date | None = None, force: bool = False, workers: int = 1):
    """Bulk DTE recalc — symbol=None walks the full Stock universe.

    `workers` is accepted for parity with recalculate_scores.run() but the
    DTE pipeline is fast enough that we run it serially; per-stock work is
    one query + a small computation per date.
    """
    _ensure_table()

    if symbol:
        stocks = [Stock.get_or_none(Stock.symbol == symbol)]
        if not stocks[0]:
            print(Fore.RED + f"Stock {symbol} not found")
            return
    else:
        stocks = list(Stock.select().order_by(Stock.symbol))

    force_tag = ' [force]' if force else ''
    print(Fore.CYAN + f"Recalculating [dte] for {len(stocks)} stock(s) "
          f"({days}d lookback){force_tag} …")

    total_u = total_s = total_e = 0
    multi = len(stocks) > 1

    if multi:
        pbar = tqdm(total=len(stocks), desc="Stocks", unit="stk",
                    mininterval=0.25, dynamic_ncols=True)
    for stock in stocks:
        if multi:
            pbar.set_postfix_str(stock.symbol, refresh=False)
        u, s, e = recalculate_dte_for_stock(
            stock.symbol, days=days, cutoff_end=cutoff_end, force=force,
            show_progress=not multi,
        )
        total_u += u
        total_s += s
        total_e += e
        if multi:
            pbar.update(1)
    if multi:
        pbar.close()

    print(Fore.GREEN + f"Done: {total_u} updated, {total_s} skipped, {total_e} errors")
