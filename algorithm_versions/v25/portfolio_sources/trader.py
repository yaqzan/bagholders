#! python3
import yfinance as yf
import defopt
from colorama import init, Fore, Style
from database import Stock, PriceHistory, Indicator, Score, Option, OptionPrice, Position, Trend, SplitEvent, WeeklyScore
from database.models.technical import WeeklyPriceHistory, WeeklyIndicator
from database.trader_database import DB
from talib import RSI, MACD, BBANDS, STOCH
from datetime import datetime, timedelta, date
from peewee import fn, JOIN
from fileHelper import date_to_string, string_to_date, date_in_string
import numpy as np
from Page import Page
from time import sleep
from tqdm import tqdm
import threading
from decimal import Decimal
import yfinance.shared as shared
from datetime import date
#---------------------------------------
init(autoreset=True, convert=True)
#---------------------------------------
RETRY_COUNT = 50

# ETFs and trusts — no fundamentals (eps, calendar, quoteSummary) on yfinance.
# Skipping fundamental + calendar lookups for these avoids yfinance 404 noise
# during `trader update`. Add new entries when a non-company ticker is tracked.
ETF_SYMBOLS = {
    'DRAM', 'EWY', 'FBTC', 'IAU', 'QQQ', 'SMH', 'SOXL', 'SPY',
    'TNA', 'TQQQ', 'UFO', 'URA', 'XLC',
}


class _StockUpdateLine:
    """Single-line progress for one symbol; uses \\r redraw for loading/done states."""

    _SPIN = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')

    def __init__(self, symbol):
        self.symbol = symbol
        self.steps = []
        self._si = 0
        self._last_len = 0

    def register(self, key, label):
        self.steps.append({'key': key, 'label': label, 'status': 'pending'})
        return self

    def begin(self):
        self._render()

    def start(self, key):
        for s in self.steps:
            if s['key'] == key:
                s['status'] = 'active'
        self._render()

    def complete(self, key):
        for s in self.steps:
            if s['key'] == key:
                s['status'] = 'ok'
        self._render()

    def error(self, key):
        for s in self.steps:
            if s['key'] == key:
                s['status'] = 'err'
        self._render()

    def pulse(self):
        self._render()

    def _render(self):
        spin = self._SPIN[self._si % len(self._SPIN)]
        self._si += 1
        parts = []
        for s in self.steps:
            if s['status'] == 'pending':
                parts.append(f"{s['label']} ·")
            elif s['status'] == 'active':
                parts.append(f"{s['label']} {spin}")
            elif s['status'] == 'ok':
                parts.append(f"{s['label']} ✓")
            elif s['status'] == 'err':
                parts.append(f"{s['label']} ✗")
        text = f"{self.symbol}  " + '  '.join(parts)
        pad = max(0, self._last_len - len(text))
        print(f'\r{text}{" " * pad}', end='', flush=True)
        self._last_len = len(text)

    def finalize(self):
        print()


class Trader():
    def __init__(self):
        pass

    def update_name(self, symbol):
        from database.models.core import _clean_name
        stock_info = yf.Ticker(symbol)
        info = stock_info.info
        if not info.get('longName') and not info.get('shortName'):
            print(Fore.RED + f"Stock {symbol} not found")
            return
        stock = Stock.get_or_none(Stock.symbol == symbol)
        if not stock:
            print(Fore.RED + f"Stock {symbol} not in DB")
            return
        stock.name = _clean_name(info.get('longName') or info.get('shortName'))
        stock.save()
        print(Fore.GREEN + f"{symbol}: {stock.name}")

    @staticmethod
    def _rename_stock_child_tables():
        """Base tables (not `stocks`) that have a `symbol` column — FK children and loose refs like score_assessment_runs."""
        cursor = DB.execute_sql(
            """
            SELECT DISTINCT c.TABLE_NAME
            FROM INFORMATION_SCHEMA.COLUMNS c
            INNER JOIN INFORMATION_SCHEMA.TABLES t
                ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
            WHERE c.TABLE_SCHEMA = DATABASE()
              AND c.COLUMN_NAME = 'symbol'
              AND c.TABLE_NAME <> 'stocks'
              AND t.TABLE_TYPE = 'BASE TABLE'
            ORDER BY c.TABLE_NAME
            """
        )
        return [row[0] for row in cursor.fetchall()]

    def rename_stock(self, old_symbol, new_symbol):
        """Move all rows from old_symbol to new_symbol. Uses FK check bypass so CASCADE on
        delete is never triggered (we UPDATE in place, not delete/recreate)."""
        old_symbol = old_symbol.strip().upper()
        new_symbol = new_symbol.strip().upper()
        if old_symbol == new_symbol:
            print(Fore.YELLOW + 'Old and new symbols are the same')
            return
        if not Stock.get_or_none(Stock.symbol == old_symbol):
            print(Fore.RED + f'Stock {old_symbol} not found')
            return
        if Stock.get_or_none(Stock.symbol == new_symbol):
            print(Fore.RED + f'Target {new_symbol} already exists; delete or merge it first')
            return
        try:
            with DB.atomic():
                DB.execute_sql('SET FOREIGN_KEY_CHECKS=0')
                try:
                    for t in self._rename_stock_child_tables():
                        DB.execute_sql(
                            f'UPDATE `{t}` SET `symbol` = %s WHERE `symbol` = %s',
                            (new_symbol, old_symbol),
                        )
                    DB.execute_sql(
                        'UPDATE `stocks` SET `symbol` = %s WHERE `symbol` = %s',
                        (new_symbol, old_symbol),
                    )
                finally:
                    DB.execute_sql('SET FOREIGN_KEY_CHECKS=1')
        except Exception as e:
            print(Fore.RED + f'Rename failed: {e}')
            return
        print(Fore.GREEN + f'Renamed {old_symbol} -> {new_symbol}')

    def pull_stock(self, symbol):
        from database.models.core import _clean_name
        stock_info = yf.Ticker(symbol)
        info = stock_info.info
        if not info.get('longName') and not info.get('shortName'):
            print(Fore.RED + f"Stock {symbol} not found")
            return
        stock = Stock.build(symbol, _clean_name(info.get('longName') or info.get('shortName')))
        stock.pull_stock_data(stock_info)
        stock.pull_historicals(stock_info)
        print(Fore.GREEN + stock.name)
        threading.Thread(target=self.pull_options, args=(stock.symbol,)).start()
        self.pull_price_history(symbol)
        stock.calculate_indicators_and_scores()

    def pull_price_history(self, symbol, period='max', start=None, line=None, price_key='price', prefetched=False):
        if line:
            line.start(price_key)
        if prefetched:
            if line:
                line.complete(price_key)
            return True
        attempts = 0
        dl_kwargs = dict(interval='1d', rounding=True, auto_adjust=True, progress=False)
        if start is not None:
            dl_kwargs['start'] = start
        else:
            dl_kwargs['period'] = period
        while attempts < 5:
            data = yf.download([symbol], **dl_kwargs)
            if len(data) > 0: break
            sym_errs = shared._ERRORS.get(symbol)
            if sym_errs and 'YFPricesMissingError' in sym_errs:
                if line:
                    line.error(price_key)
                return False
            attempts += 1
            if line:
                line.pulse()
            else:
                print(Fore.YELLOW + '.', end='', flush=True)
            sleep(1)

        if len(data) == 0:
            if line:
                line.error(price_key)
            else:
                print(Fore.RED + f'No data found for {symbol}')
            return False

        rows = []
        for index, row in data.iterrows():
            d = index.date() if hasattr(index, 'date') else index
            rows.append({
                'date': d,
                'open': row['Open'][symbol],
                'high': row['High'][symbol],
                'low': row['Low'][symbol],
                'close': row['Close'][symbol],
                'volume': row['Volume'][symbol],
            })
        PriceHistory.bulk_build(symbol, rows)
        if line:
            line.complete(price_key)
        else:
            print(Fore.GREEN + f'Price history updated for {symbol}')
        return True

    def pull_price_history_bulk(self, symbol_period_pairs, batch_size=100):
        """Bulk-fetch price history via a single yfinance call per batch.

        symbol_period_pairs: iterable of (symbol, period_str). Groups by period
        (so '1d' pulls don't mix with 'max'), chunks each group into batches of
        batch_size, and writes PriceHistory rows for every bar returned.

        Returns (succeeded, failed) as sets of symbols. Failed symbols include
        batches that returned empty AND any symbol in a batch whose Close
        column came back all-NaN. Callers should retry failed symbols via the
        per-symbol pull_price_history path.
        """
        from collections import defaultdict
        import pandas as pd

        groups = defaultdict(list)
        for symbol, period in symbol_period_pairs:
            groups[period].append(symbol)

        succeeded = set()
        failed = set()

        for period, symbols in groups.items():
            # Drop batch_size for wide history pulls — a 100-symbol 'max' batch
            # is ~20MB+ of response and Yahoo will time out. Keep short periods
            # at the full batch size since their payloads are small.
            eff_batch = batch_size if period in ('1d', '5d', '1mo', '3mo') else min(batch_size, 25)

            for i in range(0, len(symbols), eff_batch):
                batch = symbols[i:i + eff_batch]
                # Single-symbol batches: defer to the per-symbol path so we
                # don't have to special-case flat-vs-MultiIndex columns.
                if len(batch) == 1:
                    if self.pull_price_history(batch[0], period=period):
                        succeeded.add(batch[0])
                    else:
                        failed.add(batch[0])
                    continue

                dl_kwargs = dict(
                    interval='1d', rounding=True, auto_adjust=True,
                    progress=False, group_by='ticker', threads=True,
                    period=period,
                )

                data = None
                for attempt in range(3):
                    try:
                        data = yf.download(batch, **dl_kwargs)
                    except Exception:
                        data = None
                    if data is not None and not data.empty:
                        break
                    sleep(1)

                if data is None or data.empty or not isinstance(data.columns, pd.MultiIndex):
                    # Whole batch failed — punt every symbol to individual retry.
                    failed.update(batch)
                    continue

                top_level = set(data.columns.get_level_values(0))
                for symbol in batch:
                    if symbol not in top_level:
                        failed.add(symbol)
                        continue
                    try:
                        sym_df = data[symbol].dropna(subset=['Close'])
                    except KeyError:
                        failed.add(symbol)
                        continue
                    if sym_df.empty:
                        failed.add(symbol)
                        continue
                    rows = []
                    for index, row in sym_df.iterrows():
                        d = index.date() if hasattr(index, 'date') else index
                        rows.append({
                            'date': d,
                            'open': row['Open'],
                            'high': row['High'],
                            'low': row['Low'],
                            'close': row['Close'],
                            'volume': row['Volume'],
                        })
                    PriceHistory.bulk_build(symbol, rows)
                    succeeded.add(symbol)

        return succeeded, failed

    def pull_premarket_data(self, symbol, line=None, premkt_key='premkt'):
        if line:
            line.start(premkt_key)
        ticker = yf.Ticker(symbol)
        attempts = 0
        while attempts < 5:
            premarket_data = ticker.history(start=date.today(), end=None, interval="1m", prepost=True)
            if not premarket_data.empty:
                break
            attempts += 1
            if line:
                line.pulse()
            else:
                print(Fore.YELLOW + '.', end='', flush=True)
            sleep(1)
        if premarket_data.empty:
            if line:
                line.error(premkt_key)
            else:
                print(Fore.RED + f'No pre-market data found for {symbol}')
            return False
        latest_data = premarket_data.iloc[-1]
        PriceHistory.build(
            symbol, date.today(), latest_data['Open'], latest_data['High'], latest_data['Low'], latest_data['Close'], latest_data['Volume']
        )
        if line:
            line.complete(premkt_key)
        else:
            print(Fore.GREEN + f'Latest pre-market price updated for {symbol}')
        return True

    @staticmethod
    def _split_already_reflected(symbol, split_date, ratio):
        """
        Check whether stored prices are already adjusted for this split.
        Logic: if prices are continuous across the split date (pre/post ≈ 1.0),
        data is already adjusted. A large gap indicates unadjusted data.
        Skips detection for tiny splits (< 1.15x) — the gap is indistinguishable
        from normal daily volatility and the scoring impact is negligible.
        Returns True if no re-pull is needed.
        """
        if ratio < 1.15:
            return True  # too small to detect reliably; impact is minimal
        pre = (PriceHistory.select()
               .where((PriceHistory.symbol == symbol) & (PriceHistory.date < split_date))
               .order_by(PriceHistory.date.desc())
               .first())
        post = (PriceHistory.select()
                .where((PriceHistory.symbol == symbol) & (PriceHistory.date >= split_date))
                .order_by(PriceHistory.date.asc())
                .first())
        if not pre or not post:
            return True  # missing data either side — assume fine, skip
        discontinuity = float(pre.close) / float(post.close)
        # Already adjusted = prices continuous (pre/post ≈ 1.0)
        # Not adjusted = large gap (pre/post ≈ ratio)
        return abs(discontinuity - 1.0) < 0.15

    def check_and_apply_splits(self, symbol, recent_only=False):
        """
        Detect stock splits (via yfinance) not yet recorded in SplitEvent.
        For each unprocessed split, checks whether stored prices already reflect
        the adjustment before deciding to re-pull. Records all splits found so
        future runs skip them immediately.
        Returns True if a re-pull was performed (caller should force full recalc).

        recent_only: if True, only consider splits within the past 365 days
                     (used during normal `update` runs to avoid re-pulling on
                     ancient splits that are already priced in).
        """
        SplitEvent.ensure_schema()
        try:
            ticker = yf.Ticker(symbol)
            splits = ticker.splits
        except Exception:
            return False

        if splits is None or splits.empty:
            return False

        earliest = PriceHistory.select(fn.MIN(PriceHistory.date)).where(PriceHistory.symbol == symbol).scalar()
        if not earliest:
            return False

        processed = SplitEvent.processed_dates(symbol)
        cutoff = (date.today() - timedelta(days=365)) if recent_only else None
        unprocessed = [
            (ts.date() if hasattr(ts, 'date') else ts, float(ratio))
            for ts, ratio in splits.items()
            if float(ratio) > 0
            and (ts.date() if hasattr(ts, 'date') else ts) > earliest
            and str(ts.date() if hasattr(ts, 'date') else ts) not in processed
            and (cutoff is None or (ts.date() if hasattr(ts, 'date') else ts) >= cutoff)
        ]

        if not unprocessed:
            if symbol not in ETF_SYMBOLS:
                try:
                    cal = ticker.calendar
                    if cal and 'Split Date' in cal:
                        upcoming = cal['Split Date']
                        if upcoming:
                            print(Fore.YELLOW + f'  [split] {symbol}: upcoming split announced on {upcoming}')
                except Exception:
                    pass
            return False

        needs_repull = [
            (split_date, ratio)
            for split_date, ratio in unprocessed
            if not self._split_already_reflected(symbol, split_date, ratio)
        ]

        # Register all unprocessed splits as seen regardless of whether we re-pull
        all_split_dates = {
            (ts.date() if hasattr(ts, 'date') else ts): float(ratio)
            for ts, ratio in splits.items()
            if float(ratio) > 0
        }
        for split_date, ratio in all_split_dates.items():
            SplitEvent.get_or_create(
                symbol=symbol,
                split_date=split_date,
                defaults={'ratio': ratio, 'processed_at': datetime.now()}
            )

        if not needs_repull:
            return False

        # Re-pull needed: wipe and rebuild from scratch
        PriceHistory.delete().where(PriceHistory.symbol == symbol).execute()
        Indicator.delete().where(Indicator.symbol == symbol).execute()
        WeeklyPriceHistory.delete().where(WeeklyPriceHistory.symbol == symbol).execute()
        WeeklyIndicator.delete().where(WeeklyIndicator.symbol == symbol).execute()
        Score.delete().where(Score.symbol == symbol).execute()
        WeeklyScore.delete().where(WeeklyScore.symbol == symbol).execute()

        self.pull_price_history(symbol, period='max')

        for split_date, ratio in needs_repull:
            print(Fore.YELLOW + f'  [split] {symbol}: re-pulled for {ratio}-for-1 split on {split_date}')

        return True

    def pull_options(self, symbol, line=None, options_key='options'):
        if line:
            line.start(options_key)
        try:
            today = date.today()
            current_hour, current_minute = datetime.now().hour, datetime.now().minute
            if current_hour < 9 or (current_hour == 9 and current_minute < 30):
                today -= timedelta(days=1)
            while today.weekday() > 4:
                today -= timedelta(days=1)
            ticker = yf.Ticker(symbol)
            for expiration in ticker.options:
                chain = ticker.option_chain(expiration)
                for index, row in chain.calls.iterrows():
                    option = Option.build(symbol, row['strike'], expiration, 'call')
                    OptionPrice.build(option, today, row['lastPrice'], row['volume'], row['openInterest'], row['impliedVolatility'])
                for index, row in chain.puts.iterrows():
                    option = Option.build(symbol, row['strike'], expiration, 'put')
                    OptionPrice.build(option, today, row['lastPrice'], row['volume'], row['openInterest'], row['impliedVolatility'])
            if line:
                line.complete(options_key)
            else:
                print(Fore.GREEN + f'Options pulled for {symbol}')
        except Exception:
            if line:
                line.error(options_key)
            else:
                raise

    def update_metadata(self, symbols=None):
        """Refresh stock metadata only — no price pull, no indicators, no scores.

        Calls yf.Ticker(sym).info for each non-ETF stock and writes back via
        Stock.pull_stock_data(). Intended for a weekly weekend run; metadata
        (sector, industry, market cap, earnings dates, etc.) doesn't change
        intraday so daily refreshes are wasted yfinance calls.

        symbols: optional iterable of tickers to limit the sweep. Default
        sweeps every Stock row except those in ETF_SYMBOLS.
        """
        if symbols:
            target = [s.upper() for s in symbols]
            stocks = list(Stock.select().where(Stock.symbol.in_(target)))
        else:
            stocks = list(Stock.select().order_by(Stock.symbol))

        stocks = [s for s in stocks if s.symbol not in ETF_SYMBOLS]
        if not stocks:
            print(Fore.YELLOW + 'No stocks to refresh.')
            return

        print(Fore.CYAN + f'Refreshing metadata for {len(stocks)} stock(s)...')

        def refresh(symbol):
            try:
                stock = Stock.get(Stock.symbol == symbol)
                stock.pull_stock_data(yf.Ticker(symbol))
                return True
            except Exception as e:
                print(Fore.YELLOW + f'  {symbol}: {e}')
                return False

        failed = []
        for stock in tqdm(stocks, desc='metadata', ncols=80):
            if not refresh(stock.symbol):
                failed.append(stock.symbol)

        if failed:
            print(Fore.YELLOW + f'Retrying {len(failed)} failed stock(s)...')
            still_failed = [s for s in failed if not refresh(s)]
            if still_failed:
                print(Fore.RED + f'Still failed after retry: {", ".join(still_failed)}')
            else:
                print(Fore.GREEN + 'All retried stocks succeeded')
        else:
            print(Fore.GREEN + 'Metadata refresh complete.')

    def update_stocks(self, full=False, with_options=False, only_remainder=False):
        def determine_yf_period(last_pull_date):
            if not last_pull_date: return 'max'
            days = (date.today() - last_pull_date).days
            # Exclude weekends
            if days == 3 and last_pull_date.weekday() == 4 and date.today().weekday() == 0:
                return '1d'
            periods = [(1, '1d'), (5, '5d'), (30, '1mo'), (90, '3mo'), (180, '6mo'), (365, '1y')]
            for max_days, period in periods:
                if days <= max_days:
                    return period
            return 'max'
        current_hour, current_minute = datetime.now().hour, datetime.now().minute
        premarket = True if current_hour >= 4  and (current_hour < 9 or (current_hour == 9 and current_minute < 30)) else False
        ph_last = (
            PriceHistory
            .select(PriceHistory.symbol, fn.MAX(PriceHistory.pulled_at).alias('last_pulled'))
            .group_by(PriceHistory.symbol)
            .alias('ph_last')
        )
        stocks = (
            Stock
            .select(Stock.symbol)
            .join(ph_last, JOIN.LEFT_OUTER, on=(Stock.symbol == ph_last.c.symbol))
            .order_by(fn.COALESCE(ph_last.c.last_pulled, datetime(1970, 1, 1)))
        )
        if only_remainder:
            already_pulled_symbols = [x.symbol for x in PriceHistory.select().where(PriceHistory.date == date.today())]
            stocks = stocks.where(Stock.symbol.not_in(already_pulled_symbols))

        # Materialize the query once so we can do a bulk price pass before the
        # per-symbol loop without re-running the query.
        stock_list = list(stocks)

        # Precompute each symbol's target period from its last PriceHistory date
        # in a single query, to avoid N+1 lookups in the loop below.
        last_dates = dict(
            PriceHistory
            .select(PriceHistory.symbol, fn.MAX(PriceHistory.date))
            .group_by(PriceHistory.symbol)
            .tuples()
        )
        period_by_symbol = {
            s.symbol: determine_yf_period(last_dates.get(s.symbol))
            for s in stock_list
        }

        # Bulk-fetch prices up front for the non-premarket path. Premarket uses
        # 1m intraday bars via a different endpoint, so skip bulk there.
        prefetched_ok = set()
        if not premarket and stock_list:
            pairs = [(s.symbol, period_by_symbol[s.symbol]) for s in stock_list]
            prefetched_ok, bulk_failed = self.pull_price_history_bulk(pairs, batch_size=100)
            print(Fore.CYAN + f"Bulk price pull: {len(prefetched_ok)} ok, {len(bulk_failed)} deferred to per-symbol pass")

        def process_stock(stock):
            is_etf = stock.symbol in ETF_SYMBOLS
            period = period_by_symbol.get(stock.symbol) or determine_yf_period(
                PriceHistory.select(fn.MAX(PriceHistory.date)).where(PriceHistory.symbol == stock.symbol).scalar()
            )
            line = _StockUpdateLine(stock.symbol)
            if full and not is_etf:
                line.register('meta', 'profile')
            if premarket:
                if period != '1d':
                    line.register('price', 'history')
                line.register('premkt', 'pre-market')
            else:
                if with_options:
                    line.register('options', 'options')
                line.register('price', 'history')
            line.register('indicators', 'indicators')
            line.register('scores', 'scores')
            line.begin()

            if full and not is_etf:
                line.start('meta')
                # Re-fetch the full stock row so all fields (e.g. flagged) are loaded
                # before pull_stock_data calls self.save() — the outer query only
                # selects Stock.symbol for ordering efficiency.
                stock = Stock.get(Stock.symbol == stock.symbol)
                stock.pull_stock_data(yf.Ticker(stock.symbol))
                line.complete('meta')

            fetch_ok = True
            if premarket:
                if period != '1d':
                    if self.pull_price_history(stock.symbol, period, line=line, price_key='price') is False:
                        fetch_ok = False
                if self.pull_premarket_data(stock.symbol, line=line, premkt_key='premkt') is False:
                    fetch_ok = False
            else:
                opts_thread = None
                if with_options:
                    opts_thread = threading.Thread(target=self.pull_options, args=(stock.symbol, line))
                    opts_thread.start()
                already_pulled = stock.symbol in prefetched_ok
                if self.pull_price_history(stock.symbol, period, line=line, price_key='price', prefetched=already_pulled) is False:
                    fetch_ok = False
                if opts_thread is not None:
                    opts_thread.join()
            # Detect and apply any unprocessed splits; if applied, force full recalc
            # recent_only=True: ignore splits older than 1 year during normal updates
            split_applied = self.check_and_apply_splits(stock.symbol, recent_only=True)
            recalc_full = full or split_applied
            line.start('indicators')
            stock.calculate_indicators(recalc_full, silent=True)
            stock.calculate_indicators(recalc_full, weekly=True, silent=True)
            line.complete('indicators')
            line.start('scores')
            stock.calculate_scores(full=recalc_full, weekly=True, silent=True)
            if recalc_full:
                stock.calculate_scores_batched(silent=True)
            else:
                stock.calculate_scores(full=False, silent=True)
            line.complete('scores')
            line.finalize()
            return fetch_ok

        failed_symbols = []
        for stock in stock_list:
            if not process_stock(stock):
                failed_symbols.append(stock.symbol)

        # Retry pass: yfinance fetches that failed (transient rate-limits, timeouts)
        # get a second chance after the main loop finishes.
        if failed_symbols:
            print(Fore.YELLOW + f"Retrying {len(failed_symbols)} stock(s) that failed yfinance fetch: {', '.join(failed_symbols)}")
            still_failed = []
            for symbol in failed_symbols:
                stock = Stock.get_or_none(Stock.symbol == symbol)
                if not stock:
                    continue
                if not process_stock(stock):
                    still_failed.append(symbol)
            if still_failed:
                print(Fore.RED + f"Still failed after retry: {', '.join(still_failed)}")
            else:
                print(Fore.GREEN + "All retried stocks succeeded")

        # Compute today's regime (breadth + VIX + SPY → composite → multiplier)
        # Stored for next update's scoring pass to pick up automatically
        from market_regime import compute_regime
        from database.models.core import reapply_regime_today
        regime = compute_regime(pull_date=date.today())
        if regime:
            comp = float(regime.regime_composite) if regime.regime_composite else 0
            mult = float(regime.regime_multiplier) if regime.regime_multiplier else 1
            print(Fore.CYAN + f"Regime computed: composite={comp:.1f}  multiplier={mult:.4f}")
            # If this is the first regime compute for today (premarket scores used
            # yesterday's fallback multiplier), patch today's scores immediately.
            if getattr(regime, '_created', False):
                updated, skipped = reapply_regime_today(
                    regime_multiplier=mult,
                    regime_composite=comp,
                )
                print(Fore.CYAN + f"Regime re-applied to today's scores: {updated} updated, {skipped} skipped")
        else:
            print(Fore.YELLOW + "Regime skipped (coverage threshold not met)")

        # Fetch and cache today's CAD/USD exchange rate
        _fetch_and_cache_exchange_rate()

    def print_scores(self, stocks=None):
        target_date = PriceHistory.select(fn.MAX(PriceHistory.date)).scalar()
        ratings = {}
        cherry_picked = False
        if not stocks: 
            cherry_picked = True
            stocks = Stock.select().where(Stock.revenue.is_null(False))
        for stock in stocks:
            score = Score.latest(stock.symbol, target_date)
            if score:
                ratings[stock] = score.output_hash()

        missing_ratings = [stock.symbol for stock, rating in ratings.items() if not rating]
        portfolio_symbols = Position.active_positions()
        sorted_ratings = sorted([x for x in ratings.items() if x[1] and (x[1]['OVR'] >= 70 or x[1]['OVR'] <= 30) or x[0].symbol in portfolio_symbols or x[0].flagged], key=lambda x: x[1]['OVR'] if x[1]['OVR'] > 50 else 100 - x[1]['OVR'], reverse=True)
        for stock, rating in sorted_ratings:
            Stock.print_stock_ratings(stock.ticker_line(), rating, held_position = stock.symbol in portfolio_symbols, flagged = stock.flagged)
        if missing_ratings and not cherry_picked:
            print(Fore.RED + 'Missing ratings for: ' + ', '.join(missing_ratings))

    def calculate_portfolio_growth(sef, growth_percentages):
        """
        Calculate the total portfolio growth given a list of individual stock growth percentages.
        
        :param growth_percentages: List of growth percentages for each stock
        :return: Total portfolio growth percentage
        """
        # Convert percentages to decimals
        growth_decimals = [g / 100 for g in growth_percentages]
        
        # Calculate the average growth
        average_growth = sum(growth_decimals) / len(growth_decimals)
        
        # Convert back to percentage
        total_growth_percentage = average_growth * 100
        
        return total_growth_percentage

    def calculate_overall_return(self, percentage_changes):
        overall_return = 1.0
        for pct_change in percentage_changes:
            overall_return *= (1 + pct_change / 100)
        
        overall_return = (overall_return - 1) * 100
        return overall_return

    def calculate_percentage_change(self, buy_price, sell_price):
        return ((sell_price - buy_price) / buy_price) * 100

    def calculate_average(self, values):
        return sum(values) / len(values) if values else 0  # Avoid division by zero

    def pull_indices(self):
        page = Page('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
        for symbol in [tr.td.text.strip() for tr in page.html.find('table', {'id': 'constituents'}).tbody.find_all('tr')[1:]]:
            if Stock.get_or_none(symbol=symbol):
                continue
            self.pull_stock(symbol)

        page = Page('https://en.wikipedia.org/wiki/Nasdaq-100')
        for symbol in [tr.find_all('td')[1].text.strip() for tr in page.html.find_all('table')[4].tbody.find_all('tr')[1:]]:
            if Stock.get_or_none(symbol=symbol):
                continue
            self.pull_stock(symbol)


    # Usage examples:
    # diagnose_score_timeseries('AAPL')  # Last 20 days
    # diagnose_score_timeseries('TSLA', days=30)  # Last 30 days
    # diagnose_score_timeseries('MSFT', start_date=date(2025, 9, 1), end_date=date(2025, 10, 9))
    def diagnose_score_timeseries(self, symbol, start_date=None, end_date=None, days=20):
        """
        Show score evolution over time to diagnose erratic fluctuations.
        
        Args:
            symbol: Stock ticker
            start_date: Start date (optional, defaults to 'days' back from end_date)
            end_date: End date (optional, defaults to most recent)
            days: Number of days to look back if start_date not specified
        """
        from colorama import Fore, Style
        from datetime import timedelta
        
        stock = Stock.get_or_none(Stock.symbol == symbol.upper())
        if not stock:
            print(f"{Fore.RED}Stock {symbol} not found{Style.RESET_ALL}")
            return None
        
        # Get date range
        if end_date is None:
            latest = Score.select().where(Score.symbol == symbol.upper()).order_by(Score.date.desc()).first()
            if not latest:
                print(f"{Fore.RED}No scores found for {symbol}{Style.RESET_ALL}")
                return None
            end_date = latest.date
        
        if start_date is None:
            start_date = end_date - timedelta(days=days)
        
        # Fetch all scores in range
        scores = list(
            Score.select()
            .where(
                (Score.symbol == symbol.upper()) &
                (Score.date >= start_date) &
                (Score.date <= end_date)
            )
            .order_by(Score.date.asc())
        )
        
        if not scores:
            print(f"{Fore.RED}No scores found in date range{Style.RESET_ALL}")
            return None
        
        # Also get indicators and price history
        indicators = {
            ind.date: ind for ind in
            Indicator.select()
            .where(
                (Indicator.symbol == symbol.upper()) &
                (Indicator.date >= start_date) &
                (Indicator.date <= end_date)
            )
        }
        
        price_history = {
            ph.date: ph for ph in
            PriceHistory.select()
            .where(
                (PriceHistory.symbol == symbol.upper()) &
                (PriceHistory.date >= start_date) &
                (PriceHistory.date <= end_date)
            )
        }
        
        # ==================== HEADER ====================
        print(f"\n{'='*100}")
        print(f"{Fore.CYAN}SCORE TIME-SERIES DIAGNOSTIC: {symbol.upper()}{Style.RESET_ALL}")
        print(f"Period: {start_date} to {end_date} ({len(scores)} trading days)")
        print(f"{'='*100}\n")
        
        # ==================== SUMMARY STATISTICS ====================
        print(f"{Fore.YELLOW}█ SUMMARY STATISTICS{Style.RESET_ALL}\n")
        
        component_stats = {
            'Overall': [s.overall for s in scores if s.overall is not None],
            'Trend': [s.trend for s in scores if s.trend is not None],
            'MACD': [s.macd for s in scores if s.macd is not None],
            'RSI': [s.rsi for s in scores if s.rsi is not None],
            'BB': [s.bb for s in scores if s.bb is not None],
            'Tech Align': [s.technical_alignment for s in scores if s.technical_alignment is not None],
            'Stoch': [s.stoch for s in scores if s.stoch is not None]
        }
        
        print(f"  {'Component':<12} {'Min':>5} {'Max':>5} {'Avg':>6} {'StdDev':>7} {'Range':>6} {'Volatility':>11}")
        print(f"  {'-'*65}")
        
        for name, values in component_stats.items():
            if values:
                min_val = min(values)
                max_val = max(values)
                avg_val = sum(values) / len(values)
                
                # Calculate std dev
                variance = sum((x - avg_val) ** 2 for x in values) / len(values)
                std_dev = variance ** 0.5
                
                # Range and volatility
                range_val = max_val - min_val
                volatility = (std_dev / avg_val * 100) if avg_val > 0 else 0
                
                # Color code volatility
                if volatility > 30:
                    vol_color = Fore.RED
                elif volatility > 20:
                    vol_color = Fore.YELLOW
                else:
                    vol_color = Fore.GREEN
                
                print(f"  {name:<12} {min_val:>5.0f} {max_val:>5.0f} {avg_val:>6.1f} {std_dev:>7.1f} {range_val:>6.0f} {vol_color}{volatility:>10.1f}%{Style.RESET_ALL}")
        
        print()
        
        # ==================== DAY-BY-DAY BREAKDOWN ====================
        print(f"{Fore.YELLOW}█ DAY-BY-DAY SCORE EVOLUTION{Style.RESET_ALL}\n")
        
        print(f"  {'Date':<12} {'Price':>8} {'Chg%':>6} | {'OVR':>4} {'Δ':>4} | {'Trd':>4} {'MAC':>4} {'RSI':>4} {'BB':>4} {'Stc':>4} | {'Notes'}")
        print(f"  {'-'*115}")
        
        prev_score = None
        prev_price = None
        
        for i, score in enumerate(scores):
            date = score.date
            ph = price_history.get(date)
            ind = indicators.get(date)
            
            # Price data
            if ph:
                price = float(ph.close)
                if prev_price:
                    price_change = ((price - prev_price) / prev_price) * 100
                    price_color = Fore.GREEN if price_change > 0 else Fore.RED if price_change < 0 else ''
                else:
                    price_change = 0
                    price_color = ''
            else:
                price = 0
                price_change = 0
                price_color = ''
            
            # Score change
            if prev_score and score.overall is not None and prev_score.overall is not None:
                score_delta = score.overall - prev_score.overall
                delta_color = Fore.GREEN if score_delta > 0 else Fore.RED if score_delta < 0 else Style.DIM
            else:
                score_delta = 0
                delta_color = Style.DIM
            
            # Component scores
            ovr = score.overall if score.overall is not None else '--'
            trend = score.trend if score.trend is not None else '--'
            macd = score.macd if score.macd is not None else '--'
            rsi = score.rsi if score.rsi is not None else '--'
            bb = score.bb if score.bb is not None else '--'
            stoch = score.stoch if score.stoch is not None else '--'
            
            # Notes about significant changes or patterns
            notes = []
            
            # Check for large score swings
            if abs(score_delta) > 15:
                notes.append(f"LARGE SWING ({score_delta:+d})")
            
            # Check for component extremes
            if isinstance(trend, int) and trend < 30:
                notes.append("⚠ Weak trend")
            if isinstance(macd, int) and macd < 30:
                notes.append("⚠ Weak MACD")
            if isinstance(bb, int) and bb < 20:
                notes.append("🚨 BB SLIDE RISK")
            
            # Check for whipsaws (opposite moves)
            if prev_price and price_change * score_delta < 0 and abs(score_delta) > 5:
                notes.append("⚡ Score/Price divergence")
            
            # MACD regime changes
            if ind and prev_score:
                prev_ind = indicators.get(prev_score.date)
                if prev_ind and ind.macd is not None and prev_ind.macd is not None:
                    curr_regime = 'bull' if float(ind.macd) > 0 else 'bear'
                    prev_regime = 'bull' if float(prev_ind.macd) > 0 else 'bear'
                    if curr_regime != prev_regime:
                        notes.append(f"MACD → {curr_regime}")
            
            notes_str = " | ".join(notes[:2]) if notes else ""  # Limit to 2 notes
            
            print(f"  {date} ${price:>7.2f} {price_color}{price_change:>5.1f}%{Style.RESET_ALL} |"
                f" {ovr:>4} {delta_color}{score_delta:>+4}{Style.RESET_ALL} |"
                f" {trend:>4} {macd:>4} {rsi:>4} {bb:>4} {stoch:>4} |"
                f" {notes_str}")
            
            prev_score = score
            prev_price = price
        
        print()
        
        # ==================== VOLATILITY ANALYSIS ====================
        print(f"{Fore.YELLOW}█ VOLATILITY ANALYSIS{Style.RESET_ALL}\n")
        
        # Find largest single-day changes
        largest_changes = []
        for i in range(1, len(scores)):
            curr = scores[i]
            prev = scores[i-1]
            
            if curr.overall is not None and prev.overall is not None:
                change = curr.overall - prev.overall
                if abs(change) > 10:  # Significant change threshold
                    largest_changes.append({
                        'date': curr.date,
                        'change': change,
                        'from': prev.overall,
                        'to': curr.overall,
                        'components': {
                            'trend': (curr.trend or 0) - (prev.trend or 0),
                            'macd': (curr.macd or 0) - (prev.macd or 0),
                            'rsi': (curr.rsi or 0) - (prev.rsi or 0),
                            'bb': (curr.bb or 0) - (prev.bb or 0)
                        }
                    })
        
        largest_changes.sort(key=lambda x: abs(x['change']), reverse=True)
        
        if largest_changes:
            print(f"  Largest Score Changes (threshold: ±10 points):\n")
            for change in largest_changes[:5]:  # Top 5
                print(f"  {change['date']}: {change['from']} → {change['to']} ({change['change']:+d})")
                print(f"    Component changes: Trend {change['components']['trend']:+d}, "
                    f"MACD {change['components']['macd']:+d}, "
                    f"RSI {change['components']['rsi']:+d}, "
                    f"BB {change['components']['bb']:+d}")
                print()
        else:
            print(f"  {Fore.GREEN}No large single-day changes detected (all < ±10){Style.RESET_ALL}\n")
        
        # ==================== PATTERNS & RECOMMENDATIONS ====================
        print(f"{Fore.YELLOW}█ PATTERN DETECTION & RECOMMENDATIONS{Style.RESET_ALL}\n")
        
        overall_scores = [s.overall for s in scores if s.overall is not None]
        if overall_scores:
            overall_volatility = (
                (sum((x - sum(overall_scores)/len(overall_scores)) ** 2 for x in overall_scores) / len(overall_scores)) ** 0.5
                / (sum(overall_scores)/len(overall_scores)) * 100
            )
            
            if overall_volatility > 25:
                print(f"  {Fore.RED}⚠ HIGH VOLATILITY ({overall_volatility:.1f}%){Style.RESET_ALL}")
                print(f"    Scores are fluctuating significantly day-to-day")
                print(f"    Possible causes:")
                print(f"      • Stock is in choppy/sideways market")
                print(f"      • Indicators are giving conflicting signals")
                print(f"      • Short-term noise overwhelming trend signals")
                print(f"    Recommendations:")
                print(f"      • Consider smoothing scores (3-5 day MA)")
                print(f"      • Increase weight on Trend vs MACD/RSI")
                print(f"      • Wait for clearer directional signal")
            elif overall_volatility > 15:
                print(f"  {Fore.YELLOW}⚡ MODERATE VOLATILITY ({overall_volatility:.1f}%){Style.RESET_ALL}")
                print(f"    Some day-to-day fluctuation, but within normal range")
                print(f"    This is typical for swing trading timeframes")
            else:
                print(f"  {Fore.GREEN}✓ LOW VOLATILITY ({overall_volatility:.1f}%){Style.RESET_ALL}")
                print(f"    Scores are stable - strong directional signal")
        
        print(f"\n{'='*100}\n")
        
        return {
            'symbol': symbol,
            'period': f"{start_date} to {end_date}",
            'num_days': len(scores),
            'stats': component_stats,
            'largest_changes': largest_changes[:5],
            'overall_volatility': overall_volatility if overall_scores else None
        }


    def evaluate_macd_scoring(self, symbol, start_date=None, end_date=None, days=30):
        """
        Comprehensive evaluation of MACD trend-aware scoring.
        Compares old vs new, analyzes skepticism triggers, and validates logic.
        """
        from colorama import Fore, Style
        from datetime import timedelta
        
        stock = Stock.get_or_none(Stock.symbol == symbol.upper())
        if not stock:
            print(f"{Fore.RED}Stock {symbol} not found{Style.RESET_ALL}")
            return None
        
        # Get date range
        if end_date is None:
            latest = Score.select().where(Score.symbol == symbol.upper()).order_by(Score.date.desc()).first()
            if not latest:
                print(f"{Fore.RED}No scores found for {symbol}{Style.RESET_ALL}")
                return None
            end_date = latest.date
        
        if start_date is None:
            start_date = end_date - timedelta(days=days)
        
        # Fetch scores and indicators
        scores = list(
            Score.select()
            .where(
                (Score.symbol == symbol.upper()) &
                (Score.date >= start_date) &
                (Score.date <= end_date)
            )
            .order_by(Score.date.asc())
        )
        
        indicators = {
            ind.date: ind for ind in
            Indicator.select()
            .where(
                (Indicator.symbol == symbol.upper()) &
                (Indicator.date >= start_date) &
                (Indicator.date <= end_date)
            )
        }
        
        price_history = {
            ph.date: ph for ph in
            PriceHistory.select()
            .where(
                (PriceHistory.symbol == symbol.upper()) &
                (PriceHistory.date >= start_date) &
                (PriceHistory.date <= end_date)
            )
        }
        
        # ==================== HEADER ====================
        print(f"\n{'='*120}")
        print(f"{Fore.CYAN}MACD TREND-AWARE SCORING EVALUATION: {symbol.upper()}{Style.RESET_ALL}")
        print(f"Period: {start_date} to {end_date} ({len(scores)} days)")
        print(f"{'='*120}\n")
        
        # ==================== VOLATILITY METRICS ====================
        print(f"{Fore.YELLOW}█ VOLATILITY METRICS{Style.RESET_ALL}\n")
        
        macd_scores = [s.macd for s in scores if s.macd is not None]
        
        if macd_scores:
            avg = sum(macd_scores) / len(macd_scores)
            variance = sum((x - avg) ** 2 for x in macd_scores) / len(macd_scores)
            std_dev = variance ** 0.5
            volatility_pct = (std_dev / avg * 100) if avg > 0 else 0
            
            print(f"  MACD Score Statistics:")
            print(f"    Min:            {min(macd_scores)}")
            print(f"    Max:            {max(macd_scores)}")
            print(f"    Average:        {avg:.1f}")
            print(f"    Std Deviation:  {std_dev:.1f}")
            print(f"    Range:          {max(macd_scores) - min(macd_scores)}")
            
            if volatility_pct > 50:
                print(f"    {Fore.RED}Volatility:     {volatility_pct:.1f}% (VERY HIGH){Style.RESET_ALL}")
            elif volatility_pct > 35:
                print(f"    {Fore.YELLOW}Volatility:     {volatility_pct:.1f}% (HIGH){Style.RESET_ALL}")
            elif volatility_pct > 20:
                print(f"    {Fore.YELLOW}Volatility:     {volatility_pct:.1f}% (MODERATE){Style.RESET_ALL}")
            else:
                print(f"    {Fore.GREEN}Volatility:     {volatility_pct:.1f}% (HEALTHY){Style.RESET_ALL}")
            
            # Calculate day-to-day changes
            changes = [abs(macd_scores[i] - macd_scores[i-1]) for i in range(1, len(macd_scores))]
            avg_change = sum(changes) / len(changes) if changes else 0
            max_change = max(changes) if changes else 0
            large_changes = sum(1 for c in changes if c > 20)
            
            print(f"\n  Day-to-Day Changes:")
            print(f"    Average change: {avg_change:.1f} points")
            print(f"    Max change:     {max_change:.0f} points")
            print(f"    Large changes (>20): {large_changes} days ({large_changes/len(changes)*100:.0f}%)")
            
            if large_changes > len(changes) * 0.15:
                print(f"    {Fore.RED}⚠ Too many large swings - scoring too volatile{Style.RESET_ALL}")
            elif large_changes > len(changes) * 0.08:
                print(f"    {Fore.YELLOW}⚡ Some volatility present{Style.RESET_ALL}")
            else:
                print(f"    {Fore.GREEN}✓ Stable scoring{Style.RESET_ALL}")
        
        print()
        
        # ==================== SKEPTICISM ANALYSIS ====================
        print(f"{Fore.YELLOW}█ REGIME SKEPTICISM ANALYSIS{Style.RESET_ALL}\n")
        
        skepticism_triggers = {
            'bearish_false_hope': 0,
            'bullish_false_momentum': 0,
            'bullish_weakening': 0,
            'bullish_weak_momentum': 0,
            'none': 0
        }
        
        regime_counts = {
            'bullish': 0,
            'bearish': 0,
            'mixed': 0
        }
        
        for score in scores:
            ind = indicators.get(score.date)
            if not ind or ind.macd is None or ind.macd_signal is None:
                continue
            
            macd = float(ind.macd)
            signal = float(ind.macd_signal)
            
            if macd > 0 and signal > 0:
                regime = 'bullish'
            elif macd < 0 and signal < 0:
                regime = 'bearish'
            else:
                regime = 'mixed'
            
            regime_counts[regime] += 1
        
        print(f"  Regime Distribution:")
        total = sum(regime_counts.values())
        if total > 0:
            for regime, count in regime_counts.items():
                pct = count / total * 100
                print(f"    {regime.capitalize():<10} {count:>3} days ({pct:>5.1f}%)")
        
        print(f"\n  Note: Skepticism triggers can only be detected by re-running")
        print(f"  calculations with debug=True. Current stored scores don't include")
        print(f"  skepticism metadata.")
        
        print()
        
        # ==================== MACD REGIME vs TREND CONFLICTS ====================
        print(f"{Fore.YELLOW}█ MACD REGIME vs TREND CONFLICTS{Style.RESET_ALL}\n")
        
        conflicts = []
        
        for score in scores:
            ind = indicators.get(score.date)
            if not ind or ind.macd is None:
                continue
            
            macd = float(ind.macd)
            signal = float(ind.macd_signal) if ind.macd_signal else 0
            
            if macd > 0 and signal > 0:
                macd_regime = 'bullish'
            elif macd < 0 and signal < 0:
                macd_regime = 'bearish'
            else:
                macd_regime = 'mixed'
            
            if score.trend >= 60:
                trend_direction = 'uptrend'
            elif score.trend <= 40:
                trend_direction = 'downtrend'
            else:
                trend_direction = 'sideways'
            
            # Identify conflicts
            if trend_direction == 'uptrend' and macd_regime == 'bearish':
                conflicts.append({
                    'date': score.date,
                    'type': 'uptrend + bearish MACD',
                    'trend': score.trend,
                    'macd_score': score.macd,
                    'macd_value': macd
                })
            elif trend_direction == 'downtrend' and macd_regime == 'bullish':
                conflicts.append({
                    'date': score.date,
                    'type': 'downtrend + bullish MACD',
                    'trend': score.trend,
                    'macd_score': score.macd,
                    'macd_value': macd
                })
        
        if conflicts:
            print(f"  Found {len(conflicts)} conflicts:\n")
            for c in conflicts[:10]:  # Show first 10
                print(f"    {c['date']}: {c['type']}")
                print(f"      Trend: {c['trend']}, MACD Score: {c['macd_score']}, MACD Value: {c['macd_value']:.3f}")
            if len(conflicts) > 10:
                print(f"    ... and {len(conflicts) - 10} more")
        else:
            print(f"  {Fore.GREEN}✓ No major conflicts detected{Style.RESET_ALL}")
        
        print()
        
        # ==================== EXTREME SCORE ANALYSIS ====================
        print(f"{Fore.YELLOW}█ EXTREME SCORE ANALYSIS{Style.RESET_ALL}\n")
        
        very_high = [s for s in scores if s.macd and s.macd >= 90]
        very_low = [s for s in scores if s.macd and s.macd <= 15]
        
        print(f"  Very High Scores (≥90): {len(very_high)}")
        if very_high:
            for s in very_high[:5]:
                ind = indicators.get(s.date)
                ph = price_history.get(s.date)
                print(f"    {s.date}: MACD={s.macd}, Trend={s.trend}, Price=${float(ph.close):.2f}" if ph else f"    {s.date}: MACD={s.macd}")
        
        print(f"\n  Very Low Scores (≤15): {len(very_low)}")
        if very_low:
            for s in very_low[:5]:
                ind = indicators.get(s.date)
                ph = price_history.get(s.date)
                print(f"    {s.date}: MACD={s.macd}, Trend={s.trend}, Price=${float(ph.close):.2f}" if ph else f"    {s.date}: MACD={s.macd}")
        
        print()
        
        # ==================== FALSE SIGNAL DETECTION ====================
        print(f"{Fore.YELLOW}█ FALSE SIGNAL DETECTION{Style.RESET_ALL}\n")
        
        false_signals = []
        
        for i in range(1, len(scores) - 1):
            curr = scores[i]
            prev = scores[i-1]
            next_s = scores[i+1]
            
            if not all([curr.macd, prev.macd, next_s.macd]):
                continue
            
            # Detect spikes (large increase followed by large decrease)
            if curr.macd - prev.macd > 20 and next_s.macd - curr.macd < -20:
                ph = price_history.get(curr.date)
                false_signals.append({
                    'date': curr.date,
                    'prev_score': prev.macd,
                    'spike_score': curr.macd,
                    'next_score': next_s.macd,
                    'price_change': ((float(ph.close) - float(price_history.get(prev.date).close)) / float(price_history.get(prev.date).close) * 100) if ph and price_history.get(prev.date) else None
                })
        
        if false_signals:
            print(f"  {Fore.RED}⚠ Found {len(false_signals)} potential false signals (spike then crash):{Style.RESET_ALL}\n")
            for fs in false_signals:
                print(f"    {fs['date']}: {fs['prev_score']} → {fs['spike_score']} → {fs['next_score']}")
                if fs['price_change'] is not None:
                    print(f"      Price change: {fs['price_change']:+.1f}%")
            print(f"\n  {Fore.YELLOW}These spikes suggest the scoring is too reactive to noise.{Style.RESET_ALL}")
        else:
            print(f"  {Fore.GREEN}✓ No obvious false signals detected{Style.RESET_ALL}")
        
        print()
        
        # ==================== RECOMMENDATIONS ====================
        print(f"{Fore.YELLOW}█ RECOMMENDATIONS{Style.RESET_ALL}\n")
        
        recommendations = []
        
        if volatility_pct > 40:
            recommendations.append("• Volatility is very high - consider adding 3-day smoothing")
        
        if large_changes > len(changes) * 0.15:
            recommendations.append("• Too many large day-to-day swings - tighten skepticism filters")
        
        if false_signals:
            recommendations.append(f"• {len(false_signals)} false signals detected - strengthen regime skepticism")
        
        if len(conflicts) > len(scores) * 0.3:
            recommendations.append("• Many MACD/Trend conflicts - trend-primary logic is working as designed")
        
        if not recommendations:
            recommendations.append(f"{Fore.GREEN}✓ MACD scoring looks healthy! No major issues detected.{Style.RESET_ALL}")
        
        for rec in recommendations:
            print(f"  {rec}")
        
        print(f"\n{'='*120}\n")
        
        return {
            'symbol': symbol,
            'volatility': volatility_pct,
            'avg_change': avg_change,
            'large_changes': large_changes,
            'conflicts': len(conflicts),
            'false_signals': len(false_signals),
            'regime_distribution': regime_counts
        }


    # Usage:
    # evaluate_macd_scoring('AMD')
    # evaluate_macd_scoring('AAPL', days=60)
    # evaluate_macd_scoring('TSLA', start_date=date(2025, 8, 1), end_date=date(2025, 9, 1))

def _cmd_tp_stop(command, args):
    """
    trader tp   [premium]   — take-profit targets, breadth-adaptive for calls
    trader stop [premium]   — stop-loss targets, breadth-adaptive for calls

    Premium is the option price paid (e.g. trader tp 3.50 → shows target sell price).
    Omit premium to see percentages only.

    Call targets adjust with current breadth score (same logic as the Dashboard):
        Stressed (breadth ≤ 50):  CALL TP = +35%,  CALL SL = -40%
        Calm     (breadth > 50):  CALL TP = +30%,  CALL SL = -35%
    Put targets are fixed (not breadth-adaptive):
        PUT TP = +30%,  PUT SL = -20%
    """
    from colorama import Fore, Style, init as colorama_init
    colorama_init()

    premium = None
    if args:
        try:
            premium = float(args.strip().replace(',', ''))
        except ValueError:
            print(f"{Fore.RED}Invalid premium: {args!r}{Style.RESET_ALL}")
            return

    # Fetch latest breadth score from DB
    breadth_score = None
    try:
        from database.models.core import MarketBreadth
        row = (MarketBreadth.select(MarketBreadth.breadth_score, MarketBreadth.date)
               .where(MarketBreadth.breadth_score.is_null(False))
               .order_by(MarketBreadth.date.desc())
               .first())
        if row:
            breadth_score = float(row.breadth_score)
    except Exception:
        pass

    stressed = breadth_score is not None and breadth_score <= 50

    # Call TP/SL — breadth-adaptive (matches Dashboard "Call TP / SL" row)
    call_tp_pct = 35.0 if stressed else 30.0
    call_sl_pct = 40.0 if stressed else 35.0

    # Put TP/SL — fixed (matches Dashboard "Put TP / SL" row)
    put_tp_pct = 30.0
    put_sl_pct = 20.0

    # Regime header
    if breadth_score is not None:
        if stressed:
            regime_str = (f"{Fore.YELLOW}Breadth {breadth_score:.0f} - STRESSED (<=50)"
                          f"  wider targets active{Style.RESET_ALL}")
        else:
            regime_str = (f"{Fore.GREEN}Breadth {breadth_score:.0f} - CALM (>50)"
                          f"  base targets active{Style.RESET_ALL}")
    else:
        regime_str = f"{Fore.YELLOW}Breadth unavailable — using base targets{Style.RESET_ALL}"

    print(f"\n  {regime_str}\n")

    if command == 'tp':
        call_pct  = call_tp_pct
        put_pct   = put_tp_pct
        sign      = '+'
        call_mult = 1 + call_tp_pct / 100
        put_mult  = 1 + put_tp_pct  / 100
        label     = 'TP'
        c_color   = Fore.GREEN
        p_color   = Fore.CYAN
    else:
        call_pct  = call_sl_pct
        put_pct   = put_sl_pct
        sign      = '-'
        call_mult = 1 - call_sl_pct / 100
        put_mult  = 1 - put_sl_pct  / 100
        label     = 'SL'
        c_color   = Fore.RED
        p_color   = Fore.MAGENTA

    if premium:
        call_tgt = premium * call_mult
        put_tgt  = premium * put_mult
        print(f"  {c_color}CALL {label}  {sign}{call_pct:.0f}%  ->  ${call_tgt:.2f}{Style.RESET_ALL}")
        print(f"  {p_color}PUT  {label}  {sign}{put_pct:.0f}%  ->  ${put_tgt:.2f}  (fixed){Style.RESET_ALL}")
    else:
        print(f"  {c_color}CALL {label}  {sign}{call_pct:.0f}%{Style.RESET_ALL}")
        print(f"  {p_color}PUT  {label}  {sign}{put_pct:.0f}%  (fixed){Style.RESET_ALL}")
    print()


def _fetch_and_cache_exchange_rate(from_currency='CAD', to_currency='USD'):
    """Fetch live exchange rate and cache it in the DB. Returns the rate or None on failure."""
    try:
        import requests
        resp = requests.get(
            f"https://open.er-api.com/v6/latest/{from_currency}",
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = float(data['rates'][to_currency])
        from database.models.core import ExchangeRate
        ExchangeRate.ensure_schema()
        ExchangeRate.store(rate, from_currency=from_currency, to_currency=to_currency)
        return rate
    except Exception:
        return None


def _get_cad_usd_rate():
    """Return CAD/USD rate: DB cache first, live fetch as fallback, then hardcoded default."""
    from database.models.core import ExchangeRate
    try:
        ExchangeRate.ensure_schema()
        cached = ExchangeRate.get_latest('CAD', 'USD')
        if cached:
            return cached, False  # (rate, is_fallback)
    except Exception:
        pass
    # Try a live fetch (may be slow/timeout — only happens if cache is empty)
    rate = _fetch_and_cache_exchange_rate()
    if rate:
        return rate, False
    return 0.73, True  # hardcoded fallback


def _cmd_alloc(args):
    """
    trader alloc <portfolio_cad>

    Prints today's call and put signals with cascade allocation amounts in USD,
    using the v18 MC-validated strategy. Calls fill slots first by conviction;
    puts fill remaining slots. Same-symbol block across sides.

    CASCADE (% of portfolio equity, pre-scale):
        Calls  95+:25%  85-94:15%  80-84:15%  75-79:15%  70-74:0% (disabled)
        Puts   <=15:15%  16-20:12%  21-25:12%

    EXITS (30 DTE, hard sell day 15):
        Calls  TP=+30% / SL=-35%   (breadth-adaptive: TP=+35% / SL=-40% when breadth<=50)
        Puts   TP=+30% / SL=-20%   (fixed — DO NOT widen; tighter SL is required for alpha)

    F3f BREADTH-DRIVEN ALLOC SCALING (applied here to $ amounts; shipped 2026-04-24):
        Calls  scale = 1.0 if breadth >= 50, linear down to 0.70 at breadth = 20
        Puts   scale = 1.0 if breadth <= 75, linear down to 0.75 at breadth = 95
        Final clamp: [0.25, 1.75]
        Cost basis = tier % × portfolio × scale.
        Replaces legacy regime_multiplier slope path (Priority #6 fix).

    MAX 14 concurrent positions (shared pool). USD shown; CAD converted at cached rate.
    """
    from colorama import Fore, Style, init as colorama_init
    colorama_init()

    # --- parse portfolio value ---
    if not args:
        print("Usage: trader alloc <portfolio_value_cad>")
        print("Example: trader alloc 50000")
        return
    try:
        portfolio_cad = float(args.strip().replace(',', ''))
        if portfolio_cad < 500:   # treat bare numbers < 500 as thousands (e.g. 75 => 75000)
            portfolio_cad *= 1000
    except ValueError:
        print(f"Invalid portfolio value: {args!r}")
        return

    # --- get CAD/USD rate from DB cache (populated by trader update) ---
    cad_usd_rate, is_fallback = _get_cad_usd_rate()
    if is_fallback:
        print(f"{Fore.YELLOW}Warning: no cached CAD/USD rate found, using {cad_usd_rate:.4f}. Run 'trader update' to populate.{Style.RESET_ALL}")

    portfolio_usd = portfolio_cad * cad_usd_rate

    # --- fetch today's qualifying scores from DB ---
    from database.models.core import Score, AlgorithmVersion
    from peewee import fn

    version = AlgorithmVersion.get_active_scores_version()
    latest_date = (Score.select(fn.MAX(Score.date))
                   .where(Score.version == version)
                   .scalar())
    if not latest_date:
        print("No scores found in DB.")
        return

    # call signals: >= 70, sorted descending (75+ first, then 70-74 overflow)
    call_signals_75 = list(
        Score.select()
        .where(Score.version == version,
               Score.date == latest_date,
               Score.overall >= 75)
        .order_by(Score.overall.desc())
    )
    call_signals_70 = list(
        Score.select()
        .where(Score.version == version,
               Score.date == latest_date,
               Score.overall >= 70,
               Score.overall < 75)
        .order_by(Score.overall.desc())
    )
    all_call_signals = call_signals_75 + call_signals_70  # 75+ first, then 70-74

    # put signals: <= 25, sorted ascending (most extreme = lowest score first)
    put_signals = list(
        Score.select()
        .where(Score.version == version,
               Score.date == latest_date,
               Score.overall <= 25)
        .order_by(Score.overall.asc())
    )

    # --- tier definitions (v18 ultra-split cascade, shipped 2026-04-17) ---
    CALL_TIERS = [
        (95, float('inf'), 0.25, '95+'),    # ultra — WR15=90%, own tier
        (85, 95,           0.15, '85-94'),
        (80, 85,           0.15, '80-84'),
        (75, 80,           0.15, '75-79'),
        (70, 75,           0.00, '70-74'),  # disabled — overflow breaches 80% DD floor
    ]
    PUT_TIERS = [
        (None, 15, 0.15, '<=15'),
        (16,   20, 0.12, '16-20'),
        (21,   25, 0.12, '21-25'),
    ]
    MAX_POSITIONS = 14

    # --- F3f breadth-driven alloc scaling (shipped 2026-04-24) ---
    # Replaces the legacy regime_multiplier-based slope (Phase 13/15 CUT_ONLY).
    # See backtest_cascade.alloc_scale_for / volume_amplifier docstrings for
    # the full justification + canonical-MC validation summary.
    F3F_CALL_THRESH = 50.0   # breadth >= this -> no call cut
    F3F_CALL_FLOOR  = 0.70   # min call alloc scale at deepest breadth
    F3F_CALL_LOW    = 20.0   # breadth at which call floor is reached
    F3F_PUT_THRESH  = 75.0   # breadth <= this -> no put cut
    F3F_PUT_FLOOR   = 0.75   # min put alloc scale at highest breadth
    F3F_PUT_HIGH    = 95.0   # breadth at which put floor is reached
    SCALE_FLOOR, SCALE_CEIL = 0.25, 1.75

    # today's breadth + regime (regime_mult kept only for diagnostic display)
    breadth_score, regime_mult = None, 1.0
    try:
        from database.models.core import MarketBreadth, MarketRegime
        br = (MarketBreadth.select(MarketBreadth.breadth_score)
              .where(MarketBreadth.breadth_score.is_null(False))
              .order_by(MarketBreadth.date.desc()).first())
        if br: breadth_score = float(br.breadth_score)
        rg = (MarketRegime.select(MarketRegime.regime_multiplier)
              .where(MarketRegime.regime_multiplier.is_null(False))
              .order_by(MarketRegime.date.desc()).first())
        if rg: regime_mult = float(rg.regime_multiplier)
    except Exception:
        pass

    stressed = breadth_score is not None and breadth_score <= 50
    call_tp, call_sl = (35, 40) if stressed else (30, 35)

    def _f3f_scale(brd, is_put):
        """F3f curve: breadth -> alloc scale. Mirrors backtest_cascade._breadth_alloc_scale."""
        if brd is None:
            return 1.0
        if is_put:
            if brd <= F3F_PUT_THRESH: s = 1.0
            elif brd >= F3F_PUT_HIGH: s = F3F_PUT_FLOOR
            else: s = 1.0 - (brd - F3F_PUT_THRESH) / (F3F_PUT_HIGH - F3F_PUT_THRESH) * (1.0 - F3F_PUT_FLOOR)
        else:
            if brd >= F3F_CALL_THRESH: s = 1.0
            elif brd <= F3F_CALL_LOW:  s = F3F_CALL_FLOOR
            else: s = F3F_CALL_FLOOR + (brd - F3F_CALL_LOW) / (F3F_CALL_THRESH - F3F_CALL_LOW) * (1.0 - F3F_CALL_FLOOR)
        return max(SCALE_FLOOR, min(SCALE_CEIL, s))

    call_scale = _f3f_scale(breadth_score, is_put=False)
    put_scale  = _f3f_scale(breadth_score, is_put=True)

    def get_call_tier(score):
        for lo, hi, pct, label in CALL_TIERS:
            if lo <= score < hi:
                return pct, label
        return 0.15, '75-79'

    def get_put_tier(score):
        for lo, hi, pct, label in PUT_TIERS:
            lo_ok = lo is None or score >= lo
            if lo_ok and score <= hi:
                return pct, label
        return 0.12, '21-25'

    def sym_str(s):
        return s.symbol_id if hasattr(s, 'symbol_id') and isinstance(s.symbol_id, str) else str(s.symbol)

    # --- header ---
    print()
    print(f"  Portfolio: {Fore.CYAN}CAD ${portfolio_cad:>10,.0f}{Style.RESET_ALL}"
          f"  =>  {Fore.CYAN}USD ${portfolio_usd:>10,.0f}{Style.RESET_ALL}"
          f"  (rate: {cad_usd_rate:.4f})")
    print(f"  Signals date: {latest_date}  |  30 DTE  |  hard sell day 15  |  max {MAX_POSITIONS} positions (shared)")
    print()

    # --- today's guideline (v18 shipped strategy) ---
    brd_col = Fore.YELLOW if stressed else Fore.GREEN
    brd_lbl = "STRESSED" if stressed else "CALM"
    brd_txt = f"{breadth_score:.0f} {brd_lbl}" if breadth_score is not None else "n/a"
    reg_lbl = "BULL" if regime_mult >= 1.02 else "STRESS" if regime_mult <= 0.95 else "NEUTRAL"
    reg_col = Fore.GREEN if regime_mult >= 1.02 else Fore.YELLOW if regime_mult <= 0.95 else Fore.CYAN

    print(f"  {Fore.WHITE}{Style.BRIGHT}GUIDELINE (v18 + F3f alloc, shipped 2026-04-24){Style.RESET_ALL}")
    print(f"    Breadth:      {brd_col}{brd_txt}{Style.RESET_ALL}"
          f"    Regime mult:  {reg_col}{regime_mult:.3f} ({reg_lbl}){Style.RESET_ALL}  (display only — alloc uses breadth)")
    print(f"    Call exits:   {Fore.GREEN}TP=+{call_tp}%  SL=-{call_sl}%{Style.RESET_ALL}"
          f"   (adaptive: +35/-40 when breadth<=50)")
    print(f"    Put exits:    {Fore.RED}TP=+30%  SL=-20%{Style.RESET_ALL}   (fixed — do NOT widen)")
    print(f"    F3f scale:    calls x{call_scale:.2f}   puts x{put_scale:.2f}"
          f"   (calls 1.0->0.70 as brd 50->20; puts 1.0->0.75 as brd 75->95)")
    print(f"    Cost basis:   tier % x portfolio x F3f scale, clamped [0.25, 1.75]")
    print(f"    Fill order:   calls first by |score-50|, then puts; same-symbol block across sides")
    print()

    # allocation tiers summary (post-F3f scaling, as actually deployed below)
    print(f"  {'CALL tiers (F3f x' + f'{call_scale:.2f}' + '):':<22}  {'PUT tiers (F3f x' + f'{put_scale:.2f}' + '):'}")
    call_lines = [(f"{label}:", portfolio_usd * pct * call_scale,
                   "  (ultra)" if label == '95+' else
                   "  (disabled)" if label == '70-74' else "")
                  for _, _, pct, label in CALL_TIERS]
    put_lines  = [(f"{label}:", portfolio_usd * pct * put_scale, "")
                  for _, _, pct, label in PUT_TIERS]
    for i in range(max(len(call_lines), len(put_lines))):
        cl = call_lines[i] if i < len(call_lines) else ("", 0, "")
        pl = put_lines[i]  if i < len(put_lines)  else ("", 0, "")
        call_col = f"{cl[0]:<8}  ${cl[1]:>8,.0f} USD{cl[2]}" if cl[0] else ""
        put_col  = f"{pl[0]:<8}  ${pl[1]:>8,.0f} USD{pl[2]}" if pl[0] else ""
        print(f"    {call_col:<38}  {put_col}")
    print()

    # --- shared slot state ---
    positions = 0
    cash_remaining = portfolio_usd
    n_calls = 0
    n_puts = 0

    # --- CALLS section ---
    print(f"  {Fore.GREEN}CALLS{Style.RESET_ALL}  "
          f"({len(call_signals_75)} signal(s) >=75  +  {len(call_signals_70)} overflow 70-74)")
    if all_call_signals:
        print(f"  {'SYMBOL':<8}  {'SCORE':>5}  {'TIER':<8}  {'USD':>10}")
        print(f"  {'-'*36}")
        for s in all_call_signals:
            if positions >= MAX_POSITIONS:
                break
            score = float(s.overall)
            pct, label = get_call_tier(score)
            cost_usd = portfolio_usd * pct * call_scale
            if cost_usd <= 0 or cost_usd > cash_remaining:
                continue

            if score >= 92:
                color = Fore.GREEN + Style.BRIGHT
            elif score >= 85:
                color = Fore.GREEN
            elif score >= 80:
                color = Fore.YELLOW
            elif score >= 75:
                color = Fore.WHITE
            else:
                color = Fore.CYAN  # 70-74 overflow

            print(f"  {color}{sym_str(s):<8}  {score:>5.1f}  {label:<8}  {f'${cost_usd:,.0f}':>10}{Style.RESET_ALL}")
            cash_remaining -= cost_usd
            positions += 1
            n_calls += 1
        print(f"  {'-'*36}")
    else:
        print(f"  {Fore.YELLOW}  (no call signals today){Style.RESET_ALL}")
    print()

    # --- PUTS section ---
    print(f"  {Fore.RED}PUTS{Style.RESET_ALL}  "
          f"({len(put_signals)} signal(s) <=25, fills remaining slots)")
    if put_signals:
        print(f"  {'SYMBOL':<8}  {'SCORE':>5}  {'TIER':<8}  {'USD':>10}")
        print(f"  {'-'*36}")
        for s in put_signals:
            if positions >= MAX_POSITIONS:
                break
            score = float(s.overall)
            pct, label = get_put_tier(score)
            cost_usd = portfolio_usd * pct * put_scale
            if cost_usd <= 0 or cost_usd > cash_remaining:
                continue

            if score <= 8:
                color = Fore.RED + Style.BRIGHT
            elif score <= 15:
                color = Fore.RED
            elif score <= 20:
                color = Fore.YELLOW
            else:
                color = Fore.WHITE

            print(f"  {color}{sym_str(s):<8}  {score:>5.1f}  {label:<8}  {f'${cost_usd:,.0f}':>10}{Style.RESET_ALL}")
            cash_remaining -= cost_usd
            positions += 1
            n_puts += 1
        print(f"  {'-'*36}")
    else:
        print(f"  {Fore.YELLOW}  (no put signals today){Style.RESET_ALL}")
    print()

    # --- summary ---
    deployed = portfolio_usd - cash_remaining
    print(f"  {'Deployed':<12}  {f'${deployed:,.0f}':>10}")
    print(f"  {'Remaining':<12}  {f'${cash_remaining:,.0f}':>10}")
    slots_used = f"{n_calls}C + {n_puts}P = {positions}/{MAX_POSITIONS} slots"
    print(f"\n  {Fore.CYAN}{slots_used}{Style.RESET_ALL}")
    print()


def _cmd_backtest(args):
    """
    trader backtest [OPTIONS]

    Simulate the optimal cascade allocation strategy from a start date.
    Calls fire on score ≥ --min-score (70); puts fire on score ≤ 25.
    Calls are prioritized for slot allocation; puts fill remaining slots.
    Lists every trade (entry + exit) with dollar allocations, then shows
    open positions and a summary.

    Portfolio options:
      --from      YYYY-MM-DD   start date (default 2026-04-01)
      --to        YYYY-MM-DD   end date, inclusive (default: today)
      --capital   N            starting capital in CAD, < 500 treated as thousands
                               e.g. 25 → CAD $25,000 → converted to USD  (default 25)
      --min-score N            signal score floor (default 70)
      --version   V            algorithm version id/hash (default: active)
      --calls-only             disable put signals (calls only)

    Strategy options (option-level gross %):
      --tp        N            base take-profit on option premium, gross % (default 30)
                               widens to 35% when breadth_score ≤ 50 (adaptive)
      --fixed-tp               disable breadth-adaptive TP, use fixed --tp value
      --sl        N            base stop-loss on option premium, gross % (default 35)
                               widens to 40% when breadth_score ≤ 50 (adaptive)
      --fixed-sl               disable breadth-adaptive SL, use fixed --sl value
      --hard      N            hard-sell day from entry (default 15)
      --open   N            max concurrent positions   (default 10)

    The underlying σ triggers are derived automatically from the option %:
        TP σ = tp% / 100 × 3.64   (30 DTE ATM, delta 0.5, premium 1.82σ)
        SL σ = sl% / 100 × 3.64
    Net P&L after realistic per-exit slippage:
        TP net = gross% − 1.5%    SL net = −(gross% + 2.3%)    Hard net = −(gross% + 1.5%)
    """
    from collections import defaultdict
    import numpy as np
    from database.models.core import AlgorithmVersion, MarketBreadth, MarketRegime

    # ── Parse args ────────────────────────────────────────────────────────────
    start_str     = '2026-04-01'
    end_str       = None
    capital_cad   = 25_000.0
    min_score_arg = 70.0
    version_token = None
    tp_pct        = 30.0    # option gross TP % (base — may widen per breadth)
    tp_stress_pct = 35.0    # option gross TP % when breadth weak
    sl_pct        = 35.0    # option gross SL % (base — may widen per breadth)
    sl_stress_pct = 40.0    # option gross SL % when breadth weak
    hard_day      = 15      # hard-sell calendar day from entry
    max_pos       = 14      # max concurrent positions
    quick         = False   # -q / --quick: summary only, hide trade log
    fixed_tp      = False   # --fixed-tp: disable breadth-adaptive TP
    fixed_sl      = False   # --fixed-sl: disable breadth-adaptive SL
    calls_only    = False   # --calls-only: skip put signals entirely

    def _float_arg(tok):
        return float(tok.replace(',', ''))

    if args:
        tokens = args.split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == '--from' and i + 1 < len(tokens):
                start_str = tokens[i + 1]; i += 2
            elif tok == '--to' and i + 1 < len(tokens):
                end_str = tokens[i + 1]; i += 2
            elif tok == '--capital' and i + 1 < len(tokens):
                try:
                    capital_cad = _float_arg(tokens[i + 1])
                    if capital_cad < 500:
                        capital_cad *= 1_000
                except ValueError: pass
                i += 2
            elif tok == '--min-score' and i + 1 < len(tokens):
                try: min_score_arg = _float_arg(tokens[i + 1])
                except ValueError: pass
                i += 2
            elif tok == '--version' and i + 1 < len(tokens):
                version_token = tokens[i + 1]; i += 2
            elif tok == '--tp' and i + 1 < len(tokens):
                try: tp_pct = _float_arg(tokens[i + 1])
                except ValueError: pass
                i += 2
            elif tok == '--sl' and i + 1 < len(tokens):
                try: sl_pct = _float_arg(tokens[i + 1])
                except ValueError: pass
                i += 2
            elif tok == '--hard' and i + 1 < len(tokens):
                try: hard_day = int(tokens[i + 1])
                except ValueError: pass
                i += 2
            elif tok == '--open' and i + 1 < len(tokens):
                try: max_pos = int(tokens[i + 1])
                except ValueError: pass
                i += 2
            elif tok in ('--quick', '-q'):
                quick = True; i += 1
            elif tok == '--fixed-tp':
                fixed_tp = True; i += 1
            elif tok == '--fixed-sl':
                fixed_sl = True; i += 1
            elif tok == '--calls-only':
                calls_only = True; i += 1
            else:
                i += 1

    try:
        start_date = date.fromisoformat(start_str)
    except ValueError:
        print(Fore.RED + f'Invalid date: {start_str!r}')
        return

    today_d = date.today()

    if end_str:
        try:
            end_date = date.fromisoformat(end_str)
        except ValueError:
            print(Fore.RED + f'Invalid --to date: {end_str!r}')
            return
    else:
        end_date = today_d

    # ── CAD → USD conversion (mirrors trader alloc) ───────────────────────────
    cad_usd_rate, is_fallback = _get_cad_usd_rate()
    if is_fallback:
        print(f"{Fore.YELLOW}Warning: no cached CAD/USD rate found, using {cad_usd_rate:.4f}. Run 'trader update' to populate.{Style.RESET_ALL}")
    capital = capital_cad * cad_usd_rate

    # ── Derive strategy constants from option-level %s ────────────────────────
    # 30 DTE ATM: premium ≈ 1.82σ, delta ≈ 0.5  →  σ_trigger = gross% / 100 × (1.82/0.5)
    _SIGMA_MULT        = 3.64   # 1.82 / 0.5
    TP_SIGMA_BASE      = tp_pct  / 100 * _SIGMA_MULT
    TP_SIGMA_STRESS    = tp_stress_pct / 100 * _SIGMA_MULT
    SL_SIGMA_BASE      = sl_pct  / 100 * _SIGMA_MULT
    SL_SIGMA_STRESS    = sl_stress_pct / 100 * _SIGMA_MULT
    # Net P&L = gross − slippage (entry 1.0%; TP 0% limit sell; SL 1.3%; Hard 0.5%)
    NET_TP_BASE        = +(tp_pct  / 100 - 0.010)
    NET_TP_STRESS      = +(tp_stress_pct / 100 - 0.010)
    NET_SL_BASE        = -(sl_pct  / 100 + 0.023)
    NET_SL_STRESS      = -(sl_stress_pct / 100 + 0.023)
    NET_HARD           = -(50.0    / 100 + 0.015)   # hard-sell always at ~−50% option value
    MAX_POSITIONS      = max_pos
    HOLD_CALENDAR_DAYS = hard_day
    VOL_BARS           = 60
    MIN_VOL_BARS       = 20
    BREADTH_THRESHOLD  = 50.0  # breadth_score ≤ this → stress (for both TP and SL)

    # Regime-aware allocation (shipped 2026-04-17 Phase 9-13, asymmetric CUT_ONLY):
    # alloc_scale = 1.0 + slope * (regime_mult - 1.0), clamped [0.25, 1.75].
    # slope_up = 0 (no bull boost), slope_down = 1.0 (stress contraction only).
    REGIME_SLOPE          = 1.0
    REGIME_SLOPE_PUT      = 0.0
    ALLOC_SCALE_FLOOR     = 0.25
    ALLOC_SCALE_CEIL      = 1.75
    REGIME_SLOPE_UP       = 0.0
    REGIME_SLOPE_DOWN     = 1.0
    REGIME_SLOPE_PUT_UP   = -0.5   # Phase 15: mild put cut in BULL (+18% compound)
    REGIME_SLOPE_PUT_DOWN = None

    # Breadth-driven allocation knob (F3f) — shipped 2026-04-24.
    # Replaces composite-driven regime_multiplier scaling. See
    # backtest_cascade.py for full justification + canonical MC validation.
    BREADTH_ALLOC_ENABLED = True
    F3F_CALL_THRESH       = 50.0
    F3F_CALL_FLOOR        = 0.70
    F3F_CALL_LOW          = 20.0
    F3F_PUT_THRESH        = 75.0
    F3F_PUT_FLOOR         = 0.75
    F3F_PUT_HIGH          = 95.0

    TIER_ALLOC = {
        '95+':   0.25,   # ultra
        '85-94': 0.15,
        '80-84': 0.15,
        '75-79': 0.15,
        '70-74': 0.00,   # disabled
        # Put-side tiers (2026-04-17: asym weekly + tight put SL)
        'p<=15': 0.15,
        'p16-20':0.12,
        'p21-25':0.12,
    }

    # Put-side fixed TP/SL (no breadth switch)
    PUT_TP_SIGMA  = 1.092
    PUT_SL_SIGMA  = 0.728
    PUT_NET_TP    = +0.290   # +30% gross − 1.0% entry
    PUT_NET_SL    = -0.223   # −20% gross − 1.0% entry − 1.3% SL exit
    PUT_THRESHOLD = 25.0
    # Put SL hard-hold (shipped 2026-04-23, day-normalized): suppress SL check for
    # first N trading bars; TP active throughout. Mon=4 absorbs weekend gap so the
    # ~6 calendar-day shakeout coverage is consistent across entry weekdays.
    PUT_SL_HOLD_BARS_DEFAULT = 3
    PUT_SL_HOLD_BARS_MONDAY  = 4

    # Counter-trend cascade promotion (Path B / V2, shipped 2026-04-21).
    # ct_put = (overall<=25 AND TREND>=CT_PUT_TREND_MIN) -> override tier 'p<=15' (15%)
    # ct_call = (overall>=70 AND TREND<=CT_CALL_TREND_MAX) -> override tier '95+' (25%)
    CT_PROMOTE        = True
    CT_PUT_TREND_MIN  = 80
    CT_CALL_TREND_MAX = 20
    CT_CALL_TIER      = '95+'    # maps to monte_carlo.py 'ultra' (25%)
    CT_PUT_TIER       = 'p<=15'  # maps to monte_carlo.py 'put_top' (15%)

    def _tier(s):
        if s >= 95: return '95+'
        if s >= 85: return '85-94'
        if s >= 80: return '80-84'
        if s >= 75: return '75-79'
        return '70-74'

    def _put_tier(s):
        if s <= 15: return 'p<=15'
        if s <= 20: return 'p16-20'
        return 'p21-25'

    def _ct_tag(overall, trend, side):
        """Return 'ct_call' / 'ct_put' / None per V2 thresholds."""
        if not CT_PROMOTE or trend is None:
            return None
        if side == 'call' and overall >= 70 and trend <= CT_CALL_TREND_MAX:
            return 'ct_call'
        if side == 'put' and overall <= 25 and trend >= CT_PUT_TREND_MIN:
            return 'ct_put'
        return None

    def _vol(closes):
        if len(closes) < MIN_VOL_BARS: return None
        arr  = np.array(closes, dtype=float)
        rets = np.diff(arr) / arr[:-1]
        return float(np.std(rets)) * 100.0

    # ── Resolve algorithm version ─────────────────────────────────────────────
    if version_token:
        from assess_scores import resolve_algorithm_version
        version = resolve_algorithm_version(version_token)
        if not version:
            print(Fore.RED + f'Version {version_token!r} not found.')
            return
    else:
        version = AlgorithmVersion.get_active_scores_version()

    ver_str = version.git_commit[:8] if version else '?'

    # ── Load breadth data for adaptive TP/SL ──────────────────────────────────
    breadth_map = {}  # date → breadth_score
    if not (fixed_tp and fixed_sl):
        try:
            brows = list(
                MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
                    .where(
                        (MarketBreadth.date >= start_date - timedelta(days=7))
                        & (MarketBreadth.breadth_score.is_null(False))
                    )
                    .order_by(MarketBreadth.date)
                    .namedtuples()
            )
            for br in brows:
                breadth_map[br.date] = float(br.breadth_score)
        except Exception:
            pass  # fallback to fixed SL if breadth unavailable

    def _breadth_on_or_before(d):
        """Return breadth_score for date d, falling back to most recent prior."""
        if d in breadth_map:
            return breadth_map[d]
        for offset in range(1, 8):
            prev = d - timedelta(days=offset)
            if prev in breadth_map:
                return breadth_map[prev]
        return None

    def _is_stress_date(d):
        """True iff breadth_score on-or-before d is ≤ threshold (stressed regime)."""
        bs = _breadth_on_or_before(d)
        return bs is not None and bs <= BREADTH_THRESHOLD

    # ── Load alloc-scalar map: breadth_score (F3f) or regime_multiplier ──────
    regime_map = {}  # date → breadth_score (F3f) or regime_multiplier (legacy)
    try:
        if BREADTH_ALLOC_ENABLED:
            from database.models.core import MarketBreadth
            brrows = list(
                MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
                    .where(
                        (MarketBreadth.date >= start_date - timedelta(days=7))
                        & (MarketBreadth.breadth_score.is_null(False))
                    )
                    .order_by(MarketBreadth.date)
                    .namedtuples()
            )
            for br in brrows:
                regime_map[br.date] = float(br.breadth_score)
        else:
            rrows = list(
                MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier)
                    .where(
                        (MarketRegime.date >= start_date - timedelta(days=7))
                        & (MarketRegime.regime_multiplier.is_null(False))
                    )
                    .order_by(MarketRegime.date)
                    .namedtuples()
            )
            for rr in rrows:
                regime_map[rr.date] = float(rr.regime_multiplier)
    except Exception:
        pass

    _NEUTRAL_VALUE = 50.0 if BREADTH_ALLOC_ENABLED else 1.0

    def _regime_on_or_before(d):
        if d in regime_map:
            return regime_map[d]
        for offset in range(1, 8):
            prev = d - timedelta(days=offset)
            if prev in regime_map:
                return regime_map[prev]
        return _NEUTRAL_VALUE

    def _alloc_scale(d, is_put=False):
        value = _regime_on_or_before(d)

        if BREADTH_ALLOC_ENABLED:
            # F3f breadth knob
            if value is None:
                s = 1.0
            elif is_put:
                if value <= F3F_PUT_THRESH:
                    s = 1.0
                elif value >= F3F_PUT_HIGH:
                    s = F3F_PUT_FLOOR
                else:
                    s = 1.0 - (value - F3F_PUT_THRESH) / (F3F_PUT_HIGH - F3F_PUT_THRESH) * (1.0 - F3F_PUT_FLOOR)
            else:
                if value >= F3F_CALL_THRESH:
                    s = 1.0
                elif value <= F3F_CALL_LOW:
                    s = F3F_CALL_FLOOR
                else:
                    s = F3F_CALL_FLOOR + (value - F3F_CALL_LOW) / (F3F_CALL_THRESH - F3F_CALL_LOW) * (1.0 - F3F_CALL_FLOOR)
            return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, s))

        # Legacy regime_multiplier path
        delta = value - 1.0
        if is_put:
            if delta >= 0 and REGIME_SLOPE_PUT_UP is not None:
                slope = REGIME_SLOPE_PUT_UP
            elif delta < 0 and REGIME_SLOPE_PUT_DOWN is not None:
                slope = REGIME_SLOPE_PUT_DOWN
            else:
                slope = REGIME_SLOPE_PUT if REGIME_SLOPE_PUT is not None else REGIME_SLOPE
        else:
            if delta >= 0 and REGIME_SLOPE_UP is not None:
                slope = REGIME_SLOPE_UP
            elif delta < 0 and REGIME_SLOPE_DOWN is not None:
                slope = REGIME_SLOPE_DOWN
            else:
                slope = REGIME_SLOPE
        if slope == 0.0:
            return 1.0
        scale = 1.0 + slope * delta
        return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, scale))

    def _tp_for_date(d):
        """Return (TP_SIGMA, NET_TP, tp_gross_pct) for a given signal date."""
        if fixed_tp:
            return TP_SIGMA_BASE, NET_TP_BASE, tp_pct
        if _is_stress_date(d):
            return TP_SIGMA_STRESS, NET_TP_STRESS, tp_stress_pct
        return TP_SIGMA_BASE, NET_TP_BASE, tp_pct

    def _sl_for_date(d):
        """Return (SL_SIGMA, NET_SL, sl_gross_pct) for a given signal date."""
        if fixed_sl:
            return SL_SIGMA_BASE, NET_SL_BASE, sl_pct
        if _is_stress_date(d):
            return SL_SIGMA_STRESS, NET_SL_STRESS, sl_stress_pct
        return SL_SIGMA_BASE, NET_SL_BASE, sl_pct

    # ── Load qualifying signals ───────────────────────────────────────────────
    print(Fore.CYAN + f'\nLoading signals {start_date} → {end_date}…' + Style.RESET_ALL)
    raw = list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
             .where(
                 (Score.version  == version)
                 & (Score.overall >= min_score_arg)
                 & (Score.date   >= start_date)
                 & (Score.date   <= end_date)
             )
             .order_by(Score.date, Score.overall.desc(), Score.symbol)
             .namedtuples()
    )

    put_raw = [] if calls_only else list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
             .where(
                 (Score.version  == version)
                 & (Score.overall <= PUT_THRESHOLD)
                 & (Score.date   >= start_date)
                 & (Score.date   <= end_date)
             )
             .order_by(Score.date, Score.overall.asc(), Score.symbol)
             .namedtuples()
    )

    if not raw and not put_raw:
        print(Fore.YELLOW + 'No qualifying signals in this date range.')
        return

    symbols = {s.symbol for s in raw} | {s.symbol for s in put_raw}
    print(f'  {len(raw):,} call signals + {len(put_raw):,} put signals '
          f'across {len(symbols):,} symbols')

    # ── Load price history (with vol lookback buffer) ─────────────────────────
    buffer_date = start_date - timedelta(days=VOL_BARS * 2)
    settle_end  = end_date + timedelta(days=HOLD_CALENDAR_DAYS + 5)

    print(Fore.CYAN + 'Loading price history…' + Style.RESET_ALL)
    ph = defaultdict(list)
    sym_list = list(symbols)
    for i in range(0, len(sym_list), 100):
        batch = sym_list[i:i + 100]
        rows = (PriceHistory
                .select(PriceHistory.date, PriceHistory.open,
                        PriceHistory.high, PriceHistory.low,
                        PriceHistory.close, Stock.symbol)
                .join(Stock)
                .where(
                    (Stock.symbol.in_(batch))
                    & (PriceHistory.date >= buffer_date)
                    & (PriceHistory.date <= settle_end)
                )
                .order_by(Stock.symbol, PriceHistory.date)
                .namedtuples())
        for r in rows:
            ph[r.symbol].append(r)

    # ── Compute trade outcomes ────────────────────────────────────────────────
    def _outcome(symbol, signal_date, score, ph_rows, trend=None):
        date_idx    = {r.date: k for k, r in enumerate(ph_rows)}
        sig_i       = date_idx.get(signal_date)
        if sig_i is None: return None
        entry_price = float(ph_rows[sig_i].close)
        if entry_price <= 0: return None
        vol_start   = max(0, sig_i - VOL_BARS)
        closes      = [float(ph_rows[j].close) for j in range(vol_start, sig_i + 1)]
        sigma       = _vol(closes)
        if not sigma or sigma <= 0: return None
        tp_sigma, net_tp, tp_gross = _tp_for_date(signal_date)
        sl_sigma, net_sl, sl_gross = _sl_for_date(signal_date)
        tp_price    = entry_price * (1.0 + tp_sigma * sigma / 100.0)
        sl_price    = entry_price * (1.0 - sl_sigma * sigma / 100.0)
        deadline    = signal_date + timedelta(days=HOLD_CALENDAR_DAYS)
        tier        = CT_CALL_TIER if _ct_tag(score, trend, 'call') else _tier(score)
        for j in range(sig_i + 1, len(ph_rows)):
            bar       = ph_rows[j]
            bar_date  = bar.date
            bars_held = j - sig_i
            if float(bar.high) >= tp_price:
                return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                            entry_price=entry_price, sigma_daily=sigma,
                            tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                            outcome='tp',   exit_date=bar_date, net_return=net_tp,
                            hold_bars=bars_held, current_price=None,
                            tp_gross=tp_gross)
            if float(bar.low) <= sl_price:
                return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                            entry_price=entry_price, sigma_daily=sigma,
                            tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                            outcome='sl',   exit_date=bar_date, net_return=net_sl,
                            hold_bars=bars_held, current_price=None,
                            sl_gross=sl_gross)
            if bar_date >= deadline:
                return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                            entry_price=entry_price, sigma_daily=sigma,
                            tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                            outcome='hard', exit_date=bar_date, net_return=NET_HARD,
                            hold_bars=bars_held, current_price=None)
        # Still open — no resolution yet
        last = ph_rows[-1]
        return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                    entry_price=entry_price, sigma_daily=sigma,
                    tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                    outcome='open', exit_date=None, net_return=None,
                    hold_bars=len(ph_rows) - 1 - sig_i,
                    current_price=float(last.close), side='call')

    def _put_outcome(symbol, signal_date, score, ph_rows, trend=None):
        """Put trade: win = price falls PUT_TP_SIGMA·σ; stop = rises PUT_SL_SIGMA·σ."""
        date_idx    = {r.date: k for k, r in enumerate(ph_rows)}
        sig_i       = date_idx.get(signal_date)
        if sig_i is None: return None
        entry_price = float(ph_rows[sig_i].close)
        if entry_price <= 0: return None
        vol_start   = max(0, sig_i - VOL_BARS)
        closes      = [float(ph_rows[j].close) for j in range(vol_start, sig_i + 1)]
        sigma       = _vol(closes)
        if not sigma or sigma <= 0: return None
        tp_price = entry_price * (1.0 - PUT_TP_SIGMA * sigma / 100.0)
        sl_price = entry_price * (1.0 + PUT_SL_SIGMA * sigma / 100.0)
        deadline = signal_date + timedelta(days=HOLD_CALENDAR_DAYS)
        tier     = CT_PUT_TIER if _ct_tag(score, trend, 'put') else _put_tier(score)
        sl_hold  = PUT_SL_HOLD_BARS_MONDAY if signal_date.weekday() == 0 else PUT_SL_HOLD_BARS_DEFAULT
        for j in range(sig_i + 1, len(ph_rows)):
            bar       = ph_rows[j]
            bar_date  = bar.date
            bars_held = j - sig_i
            if float(bar.low) <= tp_price:
                return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                            entry_price=entry_price, sigma_daily=sigma,
                            tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                            outcome='tp', exit_date=bar_date, net_return=PUT_NET_TP,
                            hold_bars=bars_held, current_price=None, side='put')
            if bars_held > sl_hold and float(bar.high) >= sl_price:
                return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                            entry_price=entry_price, sigma_daily=sigma,
                            tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                            outcome='sl', exit_date=bar_date, net_return=PUT_NET_SL,
                            hold_bars=bars_held, current_price=None, side='put')
            if bar_date >= deadline:
                return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                            entry_price=entry_price, sigma_daily=sigma,
                            tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                            outcome='hard', exit_date=bar_date, net_return=NET_HARD,
                            hold_bars=bars_held, current_price=None, side='put')
        last = ph_rows[-1]
        return dict(symbol=symbol, signal_date=signal_date, score=score, tier=tier,
                    entry_price=entry_price, sigma_daily=sigma,
                    tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                    outcome='open', exit_date=None, net_return=None,
                    hold_bars=len(ph_rows) - 1 - sig_i,
                    current_price=float(last.close), side='put')

    print(Fore.CYAN + 'Computing outcomes…' + Style.RESET_ALL)
    outcomes_by_date = defaultdict(list)
    n_skipped = 0
    n_ct_call = 0
    n_ct_put  = 0
    for sig in raw:
        trend = float(sig.trend) if sig.trend is not None else None
        o = _outcome(sig.symbol, sig.date, float(sig.overall),
                     ph.get(sig.symbol, []), trend=trend)
        if o is None:
            n_skipped += 1
        else:
            if _ct_tag(float(sig.overall), trend, 'call'):
                n_ct_call += 1
            outcomes_by_date[sig.date].append(o)
    for sig in put_raw:
        trend = float(sig.trend) if sig.trend is not None else None
        o = _put_outcome(sig.symbol, sig.date, float(sig.overall),
                         ph.get(sig.symbol, []), trend=trend)
        if o is None:
            n_skipped += 1
        else:
            if _ct_tag(float(sig.overall), trend, 'put'):
                n_ct_put += 1
            outcomes_by_date[sig.date].append(o)
    # Sort per date: side (calls first), CT-promoted ahead of score-sorted, then score, then symbol.
    # CT priority only applies to signals that wouldn't naturally be in their override tier
    # (call score < 95 promoted to '95+'; put score > 15 promoted to 'p<=15').
    def _sort_key(o):
        side       = o.get('side', 'call')
        side_order = 0 if side == 'call' else 1
        score      = o['score']
        if side == 'call':
            ct_priority = 0 if (o['tier'] == CT_CALL_TIER and score < 95) else 1
            score_key   = -score
        else:
            ct_priority = 0 if (o['tier'] == CT_PUT_TIER and score > 15) else 1
            score_key   = score
        return (side_order, ct_priority, score_key, o['symbol'])
    for d in outcomes_by_date:
        outcomes_by_date[d].sort(key=_sort_key)

    # ── Portfolio simulation ──────────────────────────────────────────────────
    all_dates = sorted({
        r.date for rows in ph.values() for r in rows
        if start_date <= r.date <= end_date
    })

    cash        = capital
    open_pos    = []   # [{'outcome': dict, 'premium': float}]
    trade_log   = []
    equity_curve = []
    peak_equity = capital
    max_dd      = 0.0

    for d in all_dates:
        # Close positions whose exit date has arrived
        remaining = []
        for pos in open_pos:
            o = pos['outcome']
            if o['outcome'] != 'open' and o['exit_date'] <= d:
                proceeds = pos['premium'] * (1.0 + o['net_return'])
                cash    += proceeds
                trade_log.append({**o, 'premium': pos['premium'],
                                  'pnl': proceeds - pos['premium']})
            else:
                remaining.append(pos)
        open_pos  = remaining
        equity    = cash + sum(p['premium'] for p in open_pos)
        open_syms = {p['outcome']['symbol'] for p in open_pos}

        for o in outcomes_by_date.get(d, []):
            if len(open_pos) >= MAX_POSITIONS: break
            if o['symbol'] in open_syms: continue
            is_put  = o.get('side') == 'put'
            scale   = _alloc_scale(d, is_put=is_put)
            premium = TIER_ALLOC[o['tier']] * scale * equity
            if cash < premium or premium < 1.0: continue
            cash    -= premium
            open_pos.append({'outcome': o, 'premium': premium})
            open_syms.add(o['symbol'])
            equity = cash + sum(p['premium'] for p in open_pos)

        if equity > peak_equity: peak_equity = equity
        dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0
        if dd > max_dd: max_dd = dd
        equity_curve.append((d, equity))

    # Collect still-open positions
    open_positions = []
    for pos in open_pos:
        o   = pos['outcome']
        cur = float(ph.get(o['symbol'], [{'close': None}])[-1].close
                    if ph.get(o['symbol']) else None) \
              if ph.get(o['symbol']) else None
        cur = float(ph[o['symbol']][-1].close) if ph.get(o['symbol']) else None
        open_positions.append({**o, 'premium': pos['premium'], 'current_price': cur})

    final_equity = cash + sum(p['premium'] for p in open_pos)
    ret          = final_equity / capital - 1
    n_tp         = sum(1 for t in trade_log if t['outcome'] == 'tp')
    n_sl         = sum(1 for t in trade_log if t['outcome'] == 'sl')
    n_hard       = sum(1 for t in trade_log if t['outcome'] == 'hard')
    n_tot        = len(trade_log)
    tp_rate      = n_tp / n_tot if n_tot else 0.0
    be           = abs(NET_SL_BASE) / (NET_TP_BASE + abs(NET_SL_BASE))   # break-even TP rate (base regime)

    # Call/put split
    call_trades  = [t for t in trade_log if t.get('side', 'call') == 'call']
    put_trades   = [t for t in trade_log if t.get('side') == 'put']
    n_call       = len(call_trades)
    n_put        = len(put_trades)
    n_call_tp    = sum(1 for t in call_trades if t['outcome'] == 'tp')
    n_call_sl    = sum(1 for t in call_trades if t['outcome'] == 'sl')
    n_put_tp     = sum(1 for t in put_trades  if t['outcome'] == 'tp')
    n_put_sl     = sum(1 for t in put_trades  if t['outcome'] == 'sl')
    call_tp_rate = n_call_tp / n_call if n_call else 0.0
    put_tp_rate  = n_put_tp  / n_put  if n_put  else 0.0
    put_be       = abs(PUT_NET_SL) / (PUT_NET_TP + abs(PUT_NET_SL))

    # ── Helpers ───────────────────────────────────────────────────────────────
    W = 96

    def section(title):
        print()
        print(Fore.WHITE + Style.BRIGHT + f'  {title}')
        print(f'  {"─" * (W - 2)}' + Style.RESET_ALL)

    def outcome_fmt(outcome):
        if outcome == 'tp':
            return Fore.GREEN  + Style.BRIGHT + 'TP  '
        if outcome == 'sl':
            return Fore.RED    + Style.BRIGHT + 'SL  '
        if outcome == 'hard':
            return Fore.YELLOW + Style.BRIGHT + 'HARD'
        return Fore.CYAN       + Style.BRIGHT + 'OPEN'

    # ═══════════════════════════════════════════════════════════════════════════
    print()
    print(Fore.WHITE + Style.BRIGHT + '═' * W)
    def _marker(val, default, fmt=''):
        """Highlight value in yellow if it differs from the canonical default."""
        s = f'{val:{fmt}}'
        return (Fore.YELLOW + Style.BRIGHT + s + Style.RESET_ALL + Fore.WHITE + Style.BRIGHT
                if val != default else s)

    n_stress_days = sum(1 for d, bs in breadth_map.items() if bs <= BREADTH_THRESHOLD and start_date <= d <= end_date)
    n_breadth_days = sum(1 for d in breadth_map if start_date <= d <= end_date)
    tp_mode = 'fixed' if fixed_tp else f'h{tp_pct:.0f}→{tp_stress_pct:.0f} (breadth ≤ {BREADTH_THRESHOLD:.0f})'
    sl_mode = 'fixed' if fixed_sl else f'h{sl_pct:.0f}→{sl_stress_pct:.0f} (breadth ≤ {BREADTH_THRESHOLD:.0f})'

    print(f'  PORTFOLIO BACKTEST  ·  {start_date} → {end_date}  ·  v{ver_str}')
    print(f'  Capital CAD ${capital_cad:,.0f} → USD ${capital:,.0f}  (rate: {cad_usd_rate:.4f})'
          f'  ·  Signals ≥ {min_score_arg:.0f}  ·  Max {_marker(MAX_POSITIONS, 14)} positions')
    print(f'  TP {tp_mode}  '
          f'SL {sl_mode}  '
          f'Hard day-{_marker(HOLD_CALENDAR_DAYS, 15)} (−{abs(NET_HARD)*100:.1f}% net)')
    if not fixed_tp and n_breadth_days:
        print(f'  TP base: {tp_pct:.0f}% (+{NET_TP_BASE*100:.1f}% net, {TP_SIGMA_BASE:.3f}σ)  '
              f'stress: {tp_stress_pct:.0f}% (+{NET_TP_STRESS*100:.1f}% net, {TP_SIGMA_STRESS:.3f}σ)')
    else:
        print(f'  TP {_marker(tp_pct, 30.0, ".0f")}% gross (+{NET_TP_BASE*100:.1f}% net, {TP_SIGMA_BASE:.3f}σ)')
    if not fixed_sl and n_breadth_days:
        print(f'  SL base: {sl_pct:.0f}% (−{abs(NET_SL_BASE)*100:.1f}% net, {SL_SIGMA_BASE:.3f}σ)  '
              f'stress: {sl_stress_pct:.0f}% (−{abs(NET_SL_STRESS)*100:.1f}% net, {SL_SIGMA_STRESS:.3f}σ)  '
              f'[{n_stress_days}/{n_breadth_days} stress days]')
    else:
        print(f'  SL {_marker(sl_pct, 35.0, ".0f")}% gross (−{abs(NET_SL_BASE)*100:.1f}% net, {SL_SIGMA_BASE:.3f}σ)')
    print(f'  Tiers: 95+→25%  85-94→15%  80-84→15%  75-79→15%  70-74→0%'
          + (f'  {Fore.YELLOW + Style.BRIGHT}[custom params highlighted in yellow]{Style.RESET_ALL + Fore.WHITE + Style.BRIGHT}'
             if any([tp_pct != 30, sl_pct != 35, hard_day != 15, max_pos != 14]) else ''))
    print('═' * W + Style.RESET_ALL)

    if not quick:
        # ── Closed trades ─────────────────────────────────────────────────────
        section(f'CLOSED TRADES ({n_tot})')
        if trade_log:
            print(f'  {"SYMBOL":<9}  {"TIER":<7}  {"SCORE":>5}  '
                  f'{"ENTRY":>10}  {"EXIT":>10}  {"BARS":>4}  '
                  f'{"ALLOC":>9}  {"RES":>4}  {"P&L $":>9}  {"P&L%":>6}  '
                  f'{"ENTRY $":>8}  σ%')
            print(f'  {"─" * 95}')
            for t in sorted(trade_log, key=lambda x: (x['signal_date'], x['symbol'])):
                oc    = outcome_fmt(t['outcome'])
                psign = '+' if t['pnl'] >= 0 else ''
                ppsign = '+' if t['net_return'] and t['net_return'] >= 0 else ''
                pct   = (t['net_return'] or 0) * 100
                side_tag = 'P' if t.get('side') == 'put' else 'C'
                sym_disp = f'{side_tag}:{t["symbol"]}'
                print(
                    f'  {oc}{sym_disp:<9}{Style.RESET_ALL}  {t["tier"]:<7}  {t["score"]:>5.1f}  '
                    f'{str(t["signal_date"]):>10}  {str(t["exit_date"]):>10}  {t["hold_bars"]:>4}  '
                    f'${t["premium"]:>8,.0f}  {oc}{t["outcome"].upper()[:4]}{Style.RESET_ALL}  '
                    f'{psign}${t["pnl"]:>8,.0f}  {ppsign}{pct:>5.1f}%  '
                    f'${t["entry_price"]:>7.2f}  {t["sigma_daily"]:.2f}'
                )
        else:
            print(f'  {Fore.YELLOW}No closed trades yet.{Style.RESET_ALL}')

        # ── Open positions ────────────────────────────────────────────────────
        section(f'OPEN POSITIONS ({len(open_positions)})')
        if open_positions:
            print(f'  {"SYMBOL":<9}  {"TIER":<7}  {"SCORE":>5}  '
                  f'{"ENTRY":>10}  {"ALLOC":>9}  {"ENTRY $":>8}  '
                  f'{"CUR $":>8}  {"TP $":>8}  {"SL $":>8}  HARD-SELL')
            print(f'  {"─" * 95}')
            for p in sorted(open_positions, key=lambda x: (x['signal_date'], x['symbol'])):
                cur = p.get('current_price')
                cur_str = f'${cur:>7.2f}' if cur else '       -'
                side_tag = 'P' if p.get('side') == 'put' else 'C'
                sym_disp = f'{side_tag}:{p["symbol"]}'
                col = Fore.MAGENTA if p.get('side') == 'put' else Fore.CYAN
                print(
                    f'  {col}{sym_disp:<9}{Style.RESET_ALL}  {p["tier"]:<7}  {p["score"]:>5.1f}  '
                    f'{str(p["signal_date"]):>10}  ${p["premium"]:>8,.0f}  '
                    f'${p["entry_price"]:>7.2f}  {Fore.CYAN}{cur_str}{Style.RESET_ALL}  '
                    f'{Fore.GREEN}${p["tp_price"]:>7.2f}{Style.RESET_ALL}  '
                    f'{Fore.RED}${p["sl_price"]:>7.2f}{Style.RESET_ALL}  '
                    f'{p["deadline"]}'
                )
        else:
            print(f'  {Fore.GREEN}No open positions.{Style.RESET_ALL}')

    # ── Summary ───────────────────────────────────────────────────────────────
    section('SUMMARY')
    eq_color  = Fore.GREEN if ret >= 0 else Fore.RED
    dd_color  = Fore.RED if max_dd > 0.50 else Fore.YELLOW if max_dd > 0.25 else Fore.GREEN
    margin    = tp_rate - be
    mg_color  = Fore.GREEN if margin >= 0 else Fore.RED

    final_equity_cad = final_equity / cad_usd_rate
    print(f'  Starting capital:  CAD ${capital_cad:>12,.2f}')
    print(f'  Current equity:    {eq_color}CAD ${final_equity_cad:>12,.2f}  '
          f'({"+" if ret >= 0 else ""}{ret*100:.2f}%){Style.RESET_ALL}')
    print(f'  Max drawdown:      {dd_color}{max_dd*100:.1f}%{Style.RESET_ALL}')
    if n_tot:
        print()
        print(f'  Closed:  {n_tot}  '
              f'({Fore.CYAN}{n_call} calls{Style.RESET_ALL}  '
              f'{Fore.MAGENTA}{n_put} puts{Style.RESET_ALL})  '
              f'({Fore.GREEN}TP {n_tp}{Style.RESET_ALL}  '
              f'{Fore.RED}SL {n_sl}{Style.RESET_ALL}  '
              f'{Fore.YELLOW}Hard {n_hard}{Style.RESET_ALL})')
        print(f'  TP rate: {mg_color}{tp_rate*100:.1f}%{Style.RESET_ALL}'
              f'  [break-even {be*100:.1f}%  '
              f'margin {mg_color}{"+" if margin >= 0 else ""}{margin*100:.1f}pp{Style.RESET_ALL}]')
        if n_call:
            cmg = call_tp_rate - be
            cmg_color = Fore.GREEN if cmg >= 0 else Fore.RED
            print(f'  Calls:   {Fore.GREEN}TP {n_call_tp}{Style.RESET_ALL}  '
                  f'{Fore.RED}SL {n_call_sl}{Style.RESET_ALL}  '
                  f'rate {cmg_color}{call_tp_rate*100:.1f}%{Style.RESET_ALL}  '
                  f'[BE {be*100:.1f}%  {cmg_color}{"+" if cmg >= 0 else ""}{cmg*100:.1f}pp{Style.RESET_ALL}]')
        if n_put:
            pmg = put_tp_rate - put_be
            pmg_color = Fore.GREEN if pmg >= 0 else Fore.RED
            print(f'  Puts:    {Fore.GREEN}TP {n_put_tp}{Style.RESET_ALL}  '
                  f'{Fore.RED}SL {n_put_sl}{Style.RESET_ALL}  '
                  f'rate {pmg_color}{put_tp_rate*100:.1f}%{Style.RESET_ALL}  '
                  f'[BE {put_be*100:.1f}%  {pmg_color}{"+" if pmg >= 0 else ""}{pmg*100:.1f}pp{Style.RESET_ALL}]')
    print(f'  Open:    {len(open_positions)}')
    if n_ct_call or n_ct_put:
        print(f'  CT promo: {n_ct_call} ct_call → {CT_CALL_TIER} · {n_ct_put} ct_put → {CT_PUT_TIER}')
    if n_skipped:
        print(f'  {Fore.YELLOW}Skipped (no price/vol data): {n_skipped}{Style.RESET_ALL}')

    # ── Strategy used ─────────────────────────────────────────────────────────
    be = abs(NET_SL_BASE) / (NET_TP_BASE + abs(NET_SL_BASE))

    def _cv(val, default):
        s = str(val)
        return (Fore.YELLOW + Style.BRIGHT + s + Style.RESET_ALL) if val != default else s

    section('STRATEGY')
    print(f'  30 DTE ATM  ·  calls ≥ {min_score_arg:.0f}  puts ≤ {PUT_THRESHOLD:.0f}  ·  '
          f'Call TP {tp_mode}  SL {sl_mode}  ·  Put TP 30% SL 20% (fixed)  ·  '
          f'hard day-{_cv(HOLD_CALENDAR_DAYS, 15)}')
    print(f'  Call cascade 95+→25%  85-94→15%  80-84→15%  75-79→15%  70-74→0%  ·  '
          f'Put cascade ≤15→15%  16-20→12%  21-25→12%  ·  '
          f'max {_cv(MAX_POSITIONS, 14)} pos (calls prioritized)  ·  BE {be*100:.1f}%')

    # ── Equity curve (ASCII sparkline) ────────────────────────────────────────
    if len(equity_curve) >= 2:
        section('EQUITY CURVE')
        vals   = [v / cad_usd_rate for _, v in equity_curve]
        lo, hi = min(vals), max(vals)
        HEIGHT = 6
        WIDTH  = min(len(vals), 72)
        step   = max(1, len(vals) // WIDTH)
        sample = [vals[min(i * step, len(vals) - 1)] for i in range(WIDTH)]

        if hi > lo:
            norm = [round((v - lo) / (hi - lo) * (HEIGHT - 1)) for v in sample]
            for row in range(HEIGHT - 1, -1, -1):
                if row == HEIGHT - 1:
                    label = f'  CAD ${hi:>8,.0f}  '
                elif row == 0:
                    label = f'  CAD ${lo:>8,.0f}  '
                else:
                    label = f'  {" " * 15}'
                line = ''
                for col_norm in norm:
                    if col_norm >= row:
                        line += Style.BRIGHT + ('█' if col_norm == row else '▀') + Style.RESET_ALL
                    else:
                        line += ' '
                print(Fore.GREEN + label + line + Style.RESET_ALL)
            print(f'  {" " * 15}{"─" * WIDTH}')
            start_lbl = str(start_date)
            end_lbl   = str(today_d)
            gap       = max(0, WIDTH - len(start_lbl) - len(end_lbl))
            print(f'  {" " * 15}{start_lbl}{" " * gap}{end_lbl}')
    print()


def main(command=None, args=None):
    """Trader

    :param str command: what to do with trader. Can be 'pull', 'calculate-indicators, indices', 'rename-stock', ...
    :param str args: argument for command, typically a stock symbol to pull
    """
    from database.project_root import chdir_trader_project
    chdir_trader_project()
    client = Trader()

    if command == 'pull' and args:
        for arg in args.split(' '):
            client.pull_stock(arg.strip().upper())
    elif command == 'pull-options':
        client.update_stocks(full=False, with_options=True)
    elif command in ['index', 'indices']:
        client.pull_indices()
    elif command == 'update':
        # Scheduler entry point: pull price history + indicators + today's scores.
        # Always overwrites today's scores regardless of existing row.
        # historic-peaks rebuild is now a separate `trader historic-update` call —
        # schedule it once after the post-close run rather than on every update.
        client.update_stocks(full=False)
        Trend.update_trend_data(full=False)
    elif command == 'update-remainder':
        client.update_stocks(only_remainder=True)
        Trend.update_trend_data(full=False)
    elif command == 'meta-update':
        # Metadata-only refresh: sector, industry, EPS, earnings dates, etc.
        # Intended as a weekly weekend job — metadata doesn't move intraday.
        # Optional positional arg: space-separated symbols to limit the sweep.
        syms = args.split() if args else None
        client.update_metadata(symbols=syms)
    elif command == 'rebuild':
        # Cold start: full yfinance pull (period='max') + full indicator rebuild
        # + batched full score rebuild for every stock. Rare, expensive.
        client.update_stocks(full=True)
        Trend.update_trend_data(full=True)
        from historic_peaks import update_historic_peaks
        update_historic_peaks()
    elif command == 'trends':
        Trend.update_trend_data(full=args == 'full')
    elif command == 'results':
        # diagnose_score_timeseries('AAPL')  # Last 20 days
        # diagnose_score_timeseries('TSLA', days=30)  # Last 30 days
        # diagnose_score_timeseries('MSFT', start_date=date(2025, 9, 1), end_date=date(2025, 10, 9))
        client.diagnose_score_timeseries(args.upper(), start_date=date(2025, 10, 12), end_date=date(2025, 10, 15))
    elif command == 'test':
        client.diagnose_stock_score(args.upper(), target_date=date.today())
    elif command == 'buy':
        for arg in args.split(' '):
            Position.build(arg.upper())
    elif command == 'refresh-names':
        from database.models.core import _clean_name
        symbols = [s.symbol for s in Stock.select(Stock.symbol).order_by(Stock.symbol)]
        target = [s.upper() for s in args.split()] if args else symbols
        print(f"Re-pulling names for {len(target)} stock(s)...")
        for sym in target:
            info = yf.Ticker(sym).info
            raw = info.get('longName') or info.get('shortName')
            if not raw:
                print(Fore.RED + f"  {sym}: not found")
                continue
            cleaned = _clean_name(raw)
            Stock.update(name=cleaned).where(Stock.symbol == sym).execute()
            print(f"  {sym}: {cleaned}")
        print(Fore.GREEN + "Done.")
    elif command == 'rename-stock' and args:
        parts = args.split()
        if len(parts) < 2:
            print(Fore.RED + 'Usage: rename-stock OLD_SYMBOL NEW_SYMBOL')
        else:
            client.rename_stock(parts[0], parts[1])
    elif command == 'flag':
        for arg in args.split(' '):
            stock = Stock.get_or_none(symbol=arg.upper())
            if stock:
                stock.flag()
                print(Fore.GREEN + f"Flagged {arg}")
            else:
                print(Fore.RED + f"Stock {arg} not found")
    elif command == 'unflag':
        for arg in args.split(' '):
            stock = Stock.get_or_none(symbol=arg.upper())
            if stock:
                stock.unflag()
                print(Fore.GREEN + f"Unflagged {arg}")
            else:
                print(Fore.RED + f"Stock {arg} not found")
    elif command == 'sell':
        if args == 'everything':
            Position.delete().execute()
        else:
            for arg in args.split(' '):
                Position.sell(arg.upper())
    elif command in ('recalculate', 'backfill'):
        if command == 'backfill':
            print(Fore.YELLOW + "Warning: 'backfill' is deprecated — use 'recalculate' instead.")
        from recalculate_scores import ALL_MODES, run as recalculate_run
        from assess_scores import parse_lookback_arg

        _DEFAULT_YEARS = 5   # default lookback
        _FULL_YEARS    = 10  # --full extends to 10yr

        symbol, components = None, set()
        days_explicit = None        # set when user passes a lookback like 1d/40d/3y
        workers   = None            # None → auto (min(cpu_count, 8))
        do_force  = False
        do_full   = False

        if args:
            arg_list = args.split(' ')
            i = 0
            while i < len(arg_list):
                arg = arg_list[i]
                stripped = arg.lstrip('-')
                if stripped == 'all':
                    components = ALL_MODES.copy()
                elif stripped == 'force':
                    do_force = True
                elif stripped == 'full':
                    do_full = True
                elif stripped == 'workers' and i + 1 < len(arg_list):
                    i += 1
                    try:
                        workers = int(arg_list[i])
                    except ValueError:
                        pass
                elif stripped in ALL_MODES:
                    components.add(stripped)
                else:
                    parsed_days = parse_lookback_arg(arg)
                    if parsed_days is not None:
                        days_explicit = parsed_days
                    else:
                        symbol = arg.upper()
                i += 1

        if not components:
            components = ALL_MODES.copy()

        today = date.today()

        if days_explicit is not None:
            days_back = days_explicit
            window_label = f"{days_back}d"
        else:
            num_years  = _FULL_YEARS if do_full else _DEFAULT_YEARS
            start_date = date(today.year - num_years, 1, 1)
            days_back  = (today - start_date).days + 1
            window_label = f"{num_years}yr ({start_date} to {today})"

        mode_label = "force-recompute" if do_force else "missing-only"
        print(Fore.CYAN + f"Recalculating {window_label} [{mode_label}] ...")
        recalculate_run(symbol, days_back, components,
                        workers=workers, use_batch=True, force=do_force)
        print(Fore.GREEN + "Done.")

        # Historic peaks + assessment tail only on --force (full recompute).
        # Missing-only runs shouldn't pay the cost; assessment would be noise
        # if nothing actually changed.
        if do_force:
            print(Fore.CYAN + '\n── historic-update ──' + Style.RESET_ALL)
            from historic_peaks import update_historic_peaks
            update_historic_peaks()

            from assess_scores import run as assess_run
            _ASSESS_WINDOWS = [('1y', 365), ('2y', 730), ('3y', 1095), ('5y', 1825), ('10y', 3650)]
            for win_label, win_days in _ASSESS_WINDOWS:
                print(Fore.CYAN + f'\n── assess {win_label} ──' + Style.RESET_ALL)
                assess_run(symbol, win_days, force=True)

            print(Fore.CYAN + '\n── backtest-temporal ──' + Style.RESET_ALL)
            try:
                from backtest_cascade import compute_and_store_temporal
                compute_and_store_temporal()
                print('  Temporal stats stored.')
            except Exception as _e:
                print(Fore.YELLOW + f'  Warning: backtest-temporal failed: {_e}' + Style.RESET_ALL)
    elif command == 'simulate':
        from simulator import run as sim_run
        from recalculate_scores import DEFAULT_LOOKBACK as SIM_DEFAULT_LB
        syms, days, do_assess, do_compare, do_diff_assess = None, SIM_DEFAULT_LB, False, False, False
        if args:
            tokens = args.split()
            sym_list = []
            for tok in tokens:
                if tok == '--assess':       do_assess      = True
                elif tok == '--compare':    do_compare     = True
                elif tok == '--diff-assess': do_diff_assess = True
                else:
                    try:
                        days = int(tok)
                    except ValueError:
                        sym_list.append(tok.upper())
            syms = sym_list if sym_list else None
        sim_run(symbols=syms, days=days, do_assess=do_assess, do_compare=do_compare,
                do_diff_assess=do_diff_assess)
    elif command == 'assess':
        from assess_scores import (
            run as assess_run, compare as assess_compare, meta as assess_meta,
            parse_lookback_arg, DEFAULT_LOOKBACK as ASSESS_LOOKBACK,
            resolve_algorithm_version,
        )
        # All windows up to 10y, run automatically when no explicit lookback given
        _ASSESS_WINDOWS = [('1y', 365), ('2y', 730), ('3y', 1095), ('5y', 1825), ('10y', 3650)]
        symbol, days, do_compare, do_meta, extra_args = None, None, False, False, []
        version_token = None
        do_force = False
        do_regime_adjust = False
        if args:
            tokens = args.split()
            i = 0
            while i < len(tokens):
                arg = tokens[i]
                if arg == '--compare':
                    do_compare = True
                    i += 1
                elif arg == '--meta':
                    do_meta = True
                    i += 1
                elif arg == '--force':
                    do_force = True
                    i += 1
                elif arg == '--regime-adjust':
                    do_regime_adjust = True
                    i += 1
                elif arg == '--version':
                    if i + 1 >= len(tokens):
                        print(Fore.RED + '--version requires a value (numeric id, v3, or git commit hash).')
                        return
                    version_token = tokens[i + 1]
                    i += 2
                elif do_compare or do_meta:
                    extra_args.append(arg)
                    i += 1
                else:
                    lb = parse_lookback_arg(arg)
                    if lb is not None:
                        days = lb
                    else:
                        symbol = arg.upper()
                    i += 1
        if do_meta:
            assess_meta(extra_args[0] if extra_args else None)
        elif do_compare:
            assess_compare(*extra_args[:2] if extra_args else [None, None])
        else:
            assess_version = None
            if version_token is not None:
                assess_version = resolve_algorithm_version(version_token)
                if assess_version is None:
                    return
            if days is not None:
                # Explicit lookback: run just that one window
                assess_run(symbol, days, version=assess_version, force=do_force,
                           regime_adjust=do_regime_adjust)
            else:
                # No explicit lookback: run all windows up to 5y automatically
                for win_label, win_days in _ASSESS_WINDOWS:
                    print(Fore.CYAN + f'\n── assess {win_label} ({win_days}d) ──' + Style.RESET_ALL)
                    assess_run(symbol, win_days, version=assess_version, force=do_force,
                               regime_adjust=do_regime_adjust)
                    if win_label == '1y':
                        print(Fore.CYAN + '\n── historic-update (post-1y) ──' + Style.RESET_ALL)
                        from historic_peaks import update_historic_peaks
                        update_historic_peaks()

                if do_force:
                    print(Fore.CYAN + '\n── backtest-temporal ──' + Style.RESET_ALL)
                    try:
                        from backtest_cascade import compute_and_store_temporal
                        compute_and_store_temporal(version=assess_version)
                        print('  Temporal stats stored.')
                    except Exception as _e:
                        print(Fore.YELLOW + f'  Warning: backtest-temporal failed: {_e}' + Style.RESET_ALL)
    elif command == 'regime-backfill':
        from market_regime import backfill_regime
        days = 365
        if args:
            try:
                days = int(args)
            except ValueError:
                pass
        backfill_regime(days=days)
    elif command == 'breadth-backfill':
        from market_breadth import backfill_breadth
        days = 365
        if args:
            try:
                days = int(args)
            except ValueError:
                pass
        backfill_breadth(days=days)
    elif command == 'option-health':
        from assess_scores import option_collection_health
        option_collection_health(args.upper() if args else None)
    elif command == 'option-coverage':
        from database.models.options import Option, OptionPrice
        from database.utils.trading_calendar import trading_days_between
        from peewee import fn as _fn
        Option.ensure_schema()
        today = date.today()
        windows = [(30, 'coverage_30d'), (90, 'coverage_90d'), (180, 'coverage_180d')]
        recent_opts = (
            OptionPrice.select(OptionPrice.option)
            .where(OptionPrice.date >= today - timedelta(days=7))
            .distinct()
        )
        options = list(Option.select().where(Option.id.in_(recent_opts)))
        print(f"Computing coverage for {len(options)} options with recent data...")
        for opt in tqdm(options, desc="Coverage"):
            for window_days, field in windows:
                start = today - timedelta(days=window_days)
                expected = trading_days_between(start, today)
                if expected <= 0:
                    continue
                actual = (OptionPrice.select()
                    .where((OptionPrice.option == opt.id) & (OptionPrice.date > start) & (OptionPrice.date <= today))
                    .count())
                setattr(opt, field, round(actual / expected, 4))
            opt.coverage_updated_at = datetime.now()
            opt.save()
        print(Fore.GREEN + f"Coverage updated for {len(options)} options")
    elif command == 'version':
        from assess_scores import list_versions, version_notes, version_revert
        if not args:
            list_versions()
        else:
            parts = args.split(' ', 2)
            sub = parts[0]
            if sub == 'notes' and len(parts) >= 3:
                version_notes(int(parts[1]), parts[2])
            elif sub == 'revert' and len(parts) >= 2:
                version_revert(int(parts[1]))
            else:
                list_versions()
    elif command == 'revert':
        # Point ALGORITHM_VERSION at a prior version's commit hash.
        # Accepts: v3, 3, or git commit hash / unique prefix.
        from revert_version import run as revert_run
        revert_run(args.strip() if args else None)
    elif command == 'explain-scores':
        from assess_scores import explain_score_accuracy, parse_lookback_arg, DEFAULT_LOOKBACK as ES_DEFAULT_LB
        syms, days = [], ES_DEFAULT_LB
        if args:
            for tok in args.split():
                parsed = parse_lookback_arg(tok)
                if parsed is not None:
                    days = parsed
                else:
                    syms.append(tok.upper())
        explain_score_accuracy(symbols=syms if syms else None, days=days)
    elif command == 'historic-update':
        from historic_peaks import update_historic_peaks, WINDOW_DAYS
        window = WINDOW_DAYS
        if args:
            try:
                window = int(args)
            except ValueError:
                pass
        update_historic_peaks(window_days=window)
    elif command == 'fix-split':
        symbols = []
        dry_run = False
        force = False
        if args:
            for tok in args.split():
                if tok == '--dry-run':
                    dry_run = True
                elif tok == '--force':
                    force = True
                else:
                    symbols.append(tok.upper())
        if not symbols:
            print(Fore.RED + 'Usage: trader fix-split SYMBOL [SYMBOL2 …] [--dry-run] [--force]')
            print(Fore.YELLOW + '  --force: wipe and repull even if split already marked processed (fixes corrupted data)')
        else:
            for sym in symbols:
                stock = Stock.get_or_none(Stock.symbol == sym)
                if not stock:
                    print(Fore.RED + f'{sym}: not found in DB — run "trader pull {sym}" first')
                    continue
                print(Fore.CYAN + f'{sym}: checking for splits …')
                if dry_run:
                    ticker = yf.Ticker(sym)
                    splits = ticker.splits
                    if splits is None or splits.empty:
                        print(f'  · no splits found on Yahoo')
                    else:
                        processed = SplitEvent.processed_dates(sym)
                        for ts, r in splits.items():
                            d = ts.date() if hasattr(ts, 'date') else ts
                            is_processed = str(d) in processed
                            already = client._split_already_reflected(sym, d, float(r))
                            status = 'processed+reflected' if (is_processed and already) else \
                                     'processed/data corrupted' if (is_processed and not already) else \
                                     'already reflected' if already else 'NEEDS REPULL'
                            print(f'  · {float(r)}-for-1 on {d} — {status}')
                    continue
                if force:
                    # Wipe all data and repull regardless of processed state
                    print(Fore.YELLOW + f'  {sym}: --force: wiping all price history and re-pulling …')
                    from database.models.technical import WeeklyPriceHistory, WeeklyIndicator
                    PriceHistory.delete().where(PriceHistory.symbol == sym).execute()
                    Indicator.delete().where(Indicator.symbol == sym).execute()
                    WeeklyPriceHistory.delete().where(WeeklyPriceHistory.symbol == sym).execute()
                    WeeklyIndicator.delete().where(WeeklyIndicator.symbol == sym).execute()
                    Score.delete().where(Score.symbol == sym).execute()
                    WeeklyScore.delete().where(WeeklyScore.symbol == sym).execute()
                    client.pull_price_history(sym, period='max')
                    repulled = True
                else:
                    repulled = client.check_and_apply_splits(sym)
                if repulled:
                    print(Fore.YELLOW + f'  {sym}: recalculating indicators + scores …')
                    stock.calculate_indicators(full=True, silent=True)
                    stock.calculate_indicators(full=True, weekly=True, silent=True)
                    stock.calculate_scores(full=True, weekly=True, silent=True)
                    stock.calculate_scores_batched(silent=True)
                    print(Fore.GREEN + f'  {sym}: split fix complete')
                else:
                    print(f'  {sym}: no unprocessed splits needing repull (use --force to override)')
    elif command == 'montecarlo':
        import subprocess, sys as _sys
        mc_args = args.split() if args else []
        subprocess.run([_sys.executable, 'monte_carlo_sizing.py'] + mc_args)
    elif command == 'window-optimize':
        import subprocess, sys as _sys
        mc_args = args.split() if args else []
        subprocess.run([_sys.executable, 'monte_carlo_window_optimizer.py'] + mc_args)
    elif command == 'multihorizon':
        import subprocess, sys as _sys
        mc_args = args.split() if args else []
        subprocess.run([_sys.executable, 'monte_carlo_multihorizon.py'] + mc_args)
    elif command == 'backtest':
        _cmd_backtest(args)
    elif command == 'alloc':
        _cmd_alloc(args)
    elif command in ('tp', 'stop'):
        _cmd_tp_stop(command, args)
    else:
        breakpoint()

def calculate_macd_score_simple_debug(self, target_date, trend_direction, trend_score):
    """
    Debug version that shows the logic flow.
    """
    from colorama import Fore, Style
    
    score = self.calculate_macd_score_simple(target_date, trend_direction, trend_score)
    
    if score is None:
        print(f"{Fore.RED}No MACD data available{Style.RESET_ALL}")
        return None
    
    # Get values for display
    ind = self.indicator_by_date(target_date)
    macd = float(ind.macd)
    signal = float(ind.macd_signal)
    hist = float(ind.macd_hist)
    
    macd_regime = 'bullish' if (macd > 0 and signal > 0) else 'bearish' if (macd < 0 and signal < 0) else 'mixed'
    
    print(f"\n=== Simple MACD Score ===")
    print(f"Date: {target_date}")
    print(f"Trend: {trend_direction} ({trend_score})")
    print(f"MACD: {macd:.3f}, Signal: {signal:.3f}, Hist: {hist:.3f}")
    print(f"Regime: {macd_regime}")
    
    # Show strategy
    if trend_direction == 'uptrend':
        print(f"Strategy: {Fore.GREEN}MOMENTUM{Style.RESET_ALL} (buy strength)")
    elif trend_direction == 'downtrend':
        print(f"Strategy: {Fore.RED}AVOID{Style.RESET_ALL} (capital preservation)")
    else:
        print(f"Strategy: {Fore.YELLOW}MEAN REVERSION{Style.RESET_ALL} (buy dips)")
    
    print(f"\nFinal Score: {score}")
    
    return score

if __name__ == '__main__':
    import sys
    argv = sys.argv[1:]
    command = argv[0] if argv else None
    args = ' '.join(argv[1:]) if len(argv) > 1 else None
    main(command, args)
