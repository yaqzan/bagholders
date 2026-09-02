"""Backfill 6 missing SPDR sector ETFs idempotently.

Adds Stock entries + pulls full price history (yfinance period='max') +
computes indicators. Does NOT call calculate_scores — ETFs are not scored
by the v43 algorithm (no market_cap → MCD-skip, and the production scoring
loop already correctly excludes ETF entries based on sector=NULL pattern,
matching the existing XLF/XLE/XLP/XLU/XLC behavior).

Idempotent:
  - Stock.build is get_or_create
  - PriceHistory.bulk_build inserts new dates only
  - Indicator computation overwrites in-place

Production safety:
  - Adding new ETF entries does NOT modify scoring for any existing stock.
  - ETF_SYMBOLS in trader.py already updated to skip fundamental pulls.
  - `trader update` running concurrently is safe — it'll discover the new
    Stock rows on its next iteration and treat them as ETFs.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database.models.core import Stock
from database.models.technical import PriceHistory, Indicator

# 6 missing SPDR Select Sector ETFs
ETFS = [
    ('XLK',  'Technology Select Sector SPDR ETF'),
    ('XLV',  'Health Care Select Sector SPDR ETF'),
    ('XLY',  'Consumer Discretionary Select Sector SPDR ETF'),
    ('XLI',  'Industrial Select Sector SPDR ETF'),
    ('XLB',  'Materials Select Sector SPDR ETF'),
    ('XLRE', 'Real Estate Select Sector SPDR ETF'),
]


def backfill_one(symbol: str, name: str) -> dict:
    t0 = time.time()
    out = {'symbol': symbol}

    # 1. Stock entry (idempotent)
    stock, created = Stock.get_or_create(symbol=symbol, defaults={'name': name})
    if created or not stock.name:
        stock.name = name
        stock.save()
    out['stock_created'] = created
    out['stock_name'] = stock.name

    # 2. Price history — use Trader.pull_price_history (idempotent via bulk_build)
    from trader import Trader
    trader = Trader()
    have = PriceHistory.select().where(PriceHistory.symbol == symbol).count()
    if have < 100:
        # Fresh pull — period='max' gets all available history
        ok = trader.pull_price_history(symbol, period='max')
        if not ok:
            out['price_pull'] = 'FAILED'
            print(f'[{symbol}] price pull FAILED', flush=True)
            return out
    out['price_rows'] = PriceHistory.select().where(PriceHistory.symbol == symbol).count()

    # 3. Indicators — compute via Stock helper
    have_ind = Indicator.select().where(Indicator.symbol == symbol).count()
    if have_ind < 100:
        stock.calculate_indicators(full=True, silent=True)
    out['indicator_rows'] = Indicator.select().where(Indicator.symbol == symbol).count()

    # 4. (Skip scoring — ETFs are not scored)
    out['elapsed_s'] = round(time.time() - t0, 1)
    return out


def main():
    print(f'[backfill] starting for {len(ETFS)} ETFs', flush=True)
    results = []
    for sym, name in ETFS:
        print(f'[backfill] {sym}: {name}', flush=True)
        try:
            r = backfill_one(sym, name)
            results.append(r)
            print(f'[backfill]   OK created={r.get("stock_created")} '
                  f'prices={r.get("price_rows", 0)} ind={r.get("indicator_rows", 0)} '
                  f'in {r.get("elapsed_s", 0)}s', flush=True)
        except Exception as e:
            print(f'[backfill]   ERROR {type(e).__name__}: {e}', flush=True)
            import traceback
            traceback.print_exc()
            results.append({'symbol': sym, 'error': str(e)})

    print()
    print('[summary]')
    print(f'{"symbol":<6s} {"created":>8s} {"prices":>8s} {"indicators":>11s} {"elapsed_s":>10s}')
    for r in results:
        if 'error' in r:
            print(f'{r["symbol"]:<6s} ERROR: {r["error"]}')
        else:
            print(f'{r["symbol"]:<6s} {str(r.get("stock_created","-")):>8s} '
                  f'{r.get("price_rows", 0):>8d} {r.get("indicator_rows", 0):>11d} '
                  f'{r.get("elapsed_s", 0):>10.1f}')


if __name__ == '__main__':
    main()
