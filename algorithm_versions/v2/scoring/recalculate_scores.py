#! python3
"""Recalculate scores for stocks.
Called via trader.py: python trader.py recalculate [symbol] [days] [--trend] [--bb] [--rsi] [--macd] [--stoch] [--ta] [--weekly] [--overall] [--all]
Args can be in any order. Multiple flags supported. Defaults to --overall."""

import sys
from database.models.core import Stock, Score, WeeklyScore
from colorama import init, Fore
from tqdm import tqdm
from datetime import date, datetime, timedelta

init(autoreset=True, convert=True)

DEFAULT_LOOKBACK = 365
DAILY_COMPONENTS = {'trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'overall'}
ALL_MODES = DAILY_COMPONENTS | {'weekly'}


def _recalculate_weekly(stock, cutoff):
    scores = list(
        WeeklyScore.select()
        .where((WeeklyScore.symbol == stock.symbol) & (WeeklyScore.date >= cutoff))
        .order_by(WeeklyScore.date.asc())
    )
    updated, errors = 0, 0
    for ws in tqdm(scores, desc=f"{stock.symbol} weekly", leave=False):
        try:
            trend = stock.calculate_trend_score(ws.date, weekly=True)
            if trend is not None:
                ws.trend = trend

            rsi = stock.calculate_rsi_score(ws.date, trend_score=ws.trend, weekly=True)
            if rsi is not None:
                ws.rsi = rsi

            macd = stock.calculate_macd_score(ws.date, weekly=True)
            if macd is not None:
                ws.macd = macd

            if ws.rsi is not None and ws.macd is not None:
                ws.composite = ws.calculate_composite_score()

            ws.save()
            updated += 1
        except Exception as e:
            errors += 1
            print(Fore.RED + f"  {stock.symbol} weekly {ws.date}: {e}")
    return updated, errors


def recalculate(symbol, days=DEFAULT_LOOKBACK, components=None):
    if components is None:
        components = {'overall'}

    stock = Stock.get(Stock.symbol == symbol)
    cutoff = date.today() - timedelta(days=days)

    if 'weekly' in components:
        wu, we = _recalculate_weekly(stock, cutoff)
        print(f"  weekly: {wu} updated, {we} errors")

    daily = components & DAILY_COMPONENTS
    if not daily:
        return 0, 0, 0

    scores = list(
        Score.select()
        .where((Score.symbol == symbol) & (Score.date >= cutoff))
        .order_by(Score.date.asc())
    )

    label = '+'.join(sorted(daily))
    updated, skipped, errors = 0, 0, 0

    for score in tqdm(scores, desc=f"{symbol} {label}", leave=False):
        try:
            changed = False

            if 'trend' in daily:
                val = stock.calculate_trend_score(score.date)
                if val is not None:
                    score.trend = val
                    changed = True

            if 'rsi' in daily:
                val = stock.calculate_rsi_score(score.date, trend_score=score.trend)
                if val is not None:
                    score.rsi = val
                    changed = True

            if 'bb' in daily:
                val = stock.calculate_bollinger_bands_score(score.date, trend_score=score.trend)
                if val is not None:
                    score.bb = val
                    changed = True

            if 'macd' in daily:
                val = stock.calculate_macd_score(score.date)
                if val is not None:
                    score.macd = val
                    changed = True

            if 'stoch' in daily:
                val = stock.calculate_stoch_score(score.date)
                if val is not None:
                    score.stoch = val
                    changed = True

            if 'ta' in daily:
                val = score.calculate_technical_alignment_score()
                if val is not None:
                    score.technical_alignment = val
                    changed = True

            if 'overall' in daily or changed:
                if None not in (score.bb, score.trend, score.rsi, score.macd, score.stoch, score.technical_alignment):
                    score.overall = score.calculate_overall_score()
                    changed = True

            if changed:
                score.updated_at = datetime.now()
                score.save()
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            print(Fore.RED + f"  {symbol} {score.date}: {e}")

    return updated, skipped, errors


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
