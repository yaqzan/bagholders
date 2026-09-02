#! python3
import logging
import yfinance as yf
import defopt
from colorama import init, Fore, Style

# Silence yfinance's internal logger — "$SYM: possibly delisted; no price data found"
# noise during premarket/empty-bar fetches clutters the per-stock progress lines.
# Failed fetches are still surfaced via _StockUpdateLine.error() in red.
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
from database import Stock, PriceHistory, Indicator, Score, Option, OptionPrice, Position, Trend, SplitEvent, WeeklyScore
from database.models.technical import WeeklyPriceHistory, WeeklyIndicator
from database.trader_database import DB
from talib import RSI, MACD, BBANDS, STOCH
from datetime import datetime, timedelta, date
from peewee import fn, JOIN, Case
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
    'ASHR', 'BOIL', 'DRAM', 'EEM', 'EWY', 'EWZ', 'FBTC', 'FXI',
    'GLD', 'HYG', 'IAU', 'IBIT', 'IEF', 'IGV', 'IWM', 'KWEB',
    'LABD', 'QQQ', 'SLV', 'SMH', 'SOXL', 'SOXX', 'SPY', 'SVIX', 'TLT',
    'TNA', 'TQQQ', 'UFO', 'URA', 'XLB', 'XLC', 'XLE', 'XLF', 'XLI',
    'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY',
}


def _is_off_market_hours():
    """Returns True when we're safely outside market hours window.

    Market hours = 9:30 AM - 4:00 PM ET, Mon-Fri (excl holidays — heuristic
    only checks weekday + clock; holidays during weekday market hours are
    treated as market-hours, which is conservative).
    Pre-open buffer = 2 hours before open (7:30 AM ET) to avoid heavy work
    during morning prep.

    Used by `trader recalculate --auto` to pick 10y (off-hours, safe) or
    5y (in/near market, fast).
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        # Pre-3.9 fallback (shouldn't happen — we require 3.11)
        return True
    et = ZoneInfo('America/New_York')
    now_et = datetime.now(et)
    # Weekend = always off-hours
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return True
    # Time check (ET clock minutes since midnight)
    minutes_since_midnight = now_et.hour * 60 + now_et.minute
    market_open      = 9 * 60 + 30   # 9:30 AM = 570
    market_close     = 16 * 60       # 4:00 PM = 960
    pre_open_buffer  = market_open - 120  # 7:30 AM = 450 (2h pre-open)
    # In market hours OR within 2h pre-open buffer = NOT safely off-hours
    if pre_open_buffer <= minutes_since_midnight <= market_close:
        return False
    return True


def _run_rebuild_parquets(exp_filter=None, do_all=False):
    """Rebuild .cache/<exp>/*.parquet from experiments/*/build_features.py.

    Each builder writes parquets tagged with the active AlgorithmVersion id
    (e.g. `calls_v46_1825.parquet`). Staleness check: an experiment is "fresh"
    if its .cache directory contains a `*_v{active_id}_*.parquet` file.

    Args:
      exp_filter: build only this experiment (e.g. 'weekly_volume')
      do_all: rebuild even if parquet for active version already exists
    """
    import subprocess, os
    from pathlib import Path
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    expected_tag = f'_v{av.id}_'

    root = Path(__file__).parent
    exp_dir = root / 'experiments'
    cache_dir = root / '.cache'

    builders = sorted(exp_dir.glob('*/build_features.py'))
    if exp_filter:
        builders = [b for b in builders if b.parent.name == exp_filter]
        if not builders:
            print(Fore.YELLOW + f"  [warn] no build_features.py found for exp='{exp_filter}'")
            return

    print(Fore.CYAN + f'\n── rebuild-parquets (active version v{av.id}, expected tag `{expected_tag}`) ──' + Style.RESET_ALL)
    print(f'  scanning {len(builders)} experiment(s) under experiments/*/build_features.py')

    skipped = 0
    built = 0
    failed = 0
    for b in builders:
        exp_name = b.parent.name
        exp_cache = cache_dir / exp_name
        is_fresh = False
        if exp_cache.exists():
            for p in exp_cache.glob('*.parquet'):
                if expected_tag in p.name:
                    is_fresh = True
                    break

        if is_fresh and not do_all:
            print(f'  [skip]  {exp_name:<28s} (parquet for v{av.id} already exists)')
            skipped += 1
            continue

        print(f'  [build] {exp_name:<28s} running build_features.py ...')
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        try:
            r = subprocess.run(
                ['python', '-u', str(b)],
                cwd=str(root), env=env,
                capture_output=True, text=True, timeout=900,
            )
            if r.returncode == 0:
                built += 1
                print(f'  [done]  {exp_name}')
            else:
                failed += 1
                print(Fore.RED + f'  [FAIL]  {exp_name}: exit {r.returncode}' + Style.RESET_ALL)
                tail_lines = (r.stdout or '').split('\n')[-5:] + (r.stderr or '').split('\n')[-5:]
                for ln in tail_lines:
                    if ln.strip():
                        print(f'    {ln}')
        except subprocess.TimeoutExpired:
            failed += 1
            print(Fore.RED + f'  [FAIL]  {exp_name}: timed out (15min)' + Style.RESET_ALL)
        except Exception as e:
            failed += 1
            print(Fore.RED + f'  [FAIL]  {exp_name}: {type(e).__name__}: {e}' + Style.RESET_ALL)

    print(f'\n  rebuild-parquets summary: built={built} skipped={skipped} failed={failed}')


class _StockUpdateLine:
    """Single-line progress for one symbol; uses \\r redraw for loading/done states."""

    _SPIN = ('|', '/', '-', '\\')

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
                parts.append(f"{s['label']} .")
            elif s['status'] == 'active':
                parts.append(f"{s['label']} {spin}")
            elif s['status'] == 'ok':
                parts.append(f"{s['label']} ok")
            elif s['status'] == 'err':
                parts.append(f"{s['label']} ERR")
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

    def pull_premarket_bulk(self, symbols, batch_size=100):
        """Bulk-fetch the latest premarket 1m bar for many symbols in one call.

        Writes one PriceHistory row per symbol (today's date) using the last
        bar in the returned 1m series. Symbols with no premarket trades come
        back empty and are silently skipped — yesterday's PriceHistory + Score
        remain valid until the during-market update overwrites them.

        Returns (got_data, no_data) sets. Failed batches (network/timeout) are
        retried per-symbol via pull_premarket_data by the caller.
        """
        from collections import defaultdict
        import pandas as pd

        got_data = set()
        no_data = set()
        batch_failed = set()
        today = date.today()

        for i in range(0, len(symbols), batch_size):
            batch = list(symbols[i:i + batch_size])
            if not batch:
                continue
            if len(batch) == 1:
                # Single-symbol path: defer to the existing per-symbol method
                # so we don't have to special-case flat-vs-MultiIndex columns.
                if self.pull_premarket_data(batch[0]):
                    got_data.add(batch[0])
                else:
                    no_data.add(batch[0])
                continue

            dl_kwargs = dict(
                interval='1m', period='1d', prepost=True,
                rounding=True, auto_adjust=True,
                progress=False, group_by='ticker', threads=True,
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
                batch_failed.update(batch)
                continue

            top_level = set(data.columns.get_level_values(0))
            for symbol in batch:
                if symbol not in top_level:
                    no_data.add(symbol)
                    continue
                try:
                    sym_df = data[symbol].dropna(subset=['Close'])
                except KeyError:
                    no_data.add(symbol)
                    continue
                if sym_df.empty:
                    no_data.add(symbol)
                    continue
                last = sym_df.iloc[-1]
                PriceHistory.build(
                    symbol, today, last['Open'], last['High'], last['Low'], last['Close'], last['Volume']
                )
                got_data.add(symbol)

        return got_data, no_data, batch_failed

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

        # Earnings ghost cleanup (runs after every metadata refresh).
        # Yahoo/yfinance periodically inserts a duplicate near-future row for the
        # same quarter (different scrape source / corrected date). The metadata
        # pull above just refreshed reported_eps for everything that has now
        # reported, so this is the right moment to: (a) drop clear ghosts (one
        # sibling now has EPS), (b) drop stale-past + today-cadence + unclear
        # pairs via the cadence anchor. Recent-past pairs (1..7d, EPS pending)
        # are still skipped here — next week's run picks them up.
        try:
            from experiments.earnings_ghost_cleanup import run_cleanup
            print(Fore.CYAN + 'Running earnings ghost cleanup...')
            res = run_cleanup(
                apply_clear=True,
                apply_stale=True,
                apply_today=True,
                apply_unclear=True,
                apply_recent=False,
                verbose=False,
            )
            parts = res['parts']
            print(Fore.GREEN + (
                f'  cleared {res["deleted"]}: '
                f'clear={len(parts["clear"])} '
                f'ambig_stale={len(parts["ambig_stale"])} '
                f'ambig_today={len(parts["ambig_today"])} '
                f'unclear={len(parts["unclear_resolved"])} '
                f'(skipped recent-past={len(parts["ambig_recent"])})'
            ))
        except Exception as e:
            print(Fore.YELLOW + f'  ghost cleanup skipped: {e}')

    def update_stocks(self, full=False, with_options=False, only_remainder=False,
                      include_delisted=False, only_delisted=False, symbols=None,
                      score_notification_previous=None, score_notification_immediate=False):
        Stock.ensure_schema()

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
        # Order like the dashboard's default stock list: strong calls first,
        # then strong puts, then neutral names by distance from 50.
        from database.models.core import AlgorithmVersion as _AV
        _active_ver = _AV.get_active_scores_version()
        _dashboard_ver = _active_ver
        _dashboard_date = (
            Score
            .select(fn.MAX(Score.date))
            .where(Score.version == _dashboard_ver, Score.overall.is_null(False))
            .scalar()
        )
        if not _dashboard_date:
            _prev_ver = (
                _AV
                .select()
                .where(
                    (_AV.id < _active_ver.id)
                    & (~(_AV.git_commit.startswith('shadow/')))
                )
                .order_by(_AV.id.desc())
                .first()
            )
            if _prev_ver:
                _dashboard_ver = _prev_ver
                _dashboard_date = (
                    Score
                    .select(fn.MAX(Score.date))
                    .where(Score.version == _dashboard_ver, Score.overall.is_null(False))
                    .scalar()
                )

        stocks = Stock.select(Stock.symbol)
        if _dashboard_date:
            _dashboard_scores = (
                Score
                .select(Score.symbol, Score.overall)
                .where(
                    Score.version == _dashboard_ver,
                    Score.date == _dashboard_date,
                    Score.overall.is_null(False),
                )
                .alias('dashboard_scores')
            )
            _overall = _dashboard_scores.c.overall
            _dashboard_sort_value = Case(None, (
                (_overall >= 75, 200 + _overall),
                (_overall <= 25, 200 - _overall),
                (_overall >= 50, _overall),
            ), 100 - _overall)
            stocks = (
                stocks
                .join(_dashboard_scores, JOIN.LEFT_OUTER, on=(Stock.symbol == _dashboard_scores.c.symbol))
                .order_by(fn.COALESCE(_dashboard_sort_value, -1).desc(), Stock.symbol)
            )
        else:
            stocks = stocks.order_by(Stock.symbol)
        if only_delisted:
            stocks = stocks.where(Stock.delisted_date.is_null(False))
        elif not include_delisted:
            stocks = stocks.where(Stock.delisted_date.is_null())
        if symbols:
            target_symbols = sorted({s.strip().upper() for s in symbols if s and s.strip()})
            stocks = stocks.where(Stock.symbol.in_(target_symbols))
        if only_remainder:
            already_pulled_symbols = [x.symbol for x in PriceHistory.select().where(PriceHistory.date == date.today())]
            stocks = stocks.where(Stock.symbol.not_in(already_pulled_symbols))

        # Materialize the query once so we can do a bulk price pass before the
        # per-symbol loop without re-running the query.
        stock_list = list(stocks)
        if only_delisted:
            print(Fore.CYAN + f"Updating {len(stock_list)} historical/delisted stock(s) without options")
        elif include_delisted:
            print(Fore.CYAN + f"Updating {len(stock_list)} stock(s), including historical/delisted rows")

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

        # Bulk-fetch prices up front. During market hours: daily bars via
        # pull_price_history_bulk. Premarket: latest 1m bar via
        # pull_premarket_bulk so we make ~5 batched calls instead of ~470 single
        # calls. Symbols with no premarket trades are no-ops (yesterday's close
        # + score persist until the during-market update overwrites them).
        prefetched_ok = set()
        premkt_got_data = set()
        premkt_no_data = set()
        premkt_batch_failed = set()
        if premarket and stock_list:
            symbols = [s.symbol for s in stock_list]
            premkt_got_data, premkt_no_data, premkt_batch_failed = self.pull_premarket_bulk(
                symbols, batch_size=100
            )
            print(Fore.CYAN + f"Bulk premarket pull: {len(premkt_got_data)} traded, "
                              f"{len(premkt_no_data)} no trades, "
                              f"{len(premkt_batch_failed)} deferred to per-symbol")
            # Premarket also needs daily history for symbols whose period != '1d'
            # (rare — only stocks with stale or missing PriceHistory).
            stale_pairs = [
                (s.symbol, period_by_symbol[s.symbol])
                for s in stock_list
                if period_by_symbol[s.symbol] != '1d'
            ]
            if stale_pairs:
                stale_ok, _ = self.pull_price_history_bulk(stale_pairs, batch_size=100)
                prefetched_ok |= stale_ok
        elif not premarket and stock_list:
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
                if period != '1d' and stock.symbol not in prefetched_ok:
                    if self.pull_price_history(stock.symbol, period, line=line, price_key='price') is False:
                        fetch_ok = False
                elif period != '1d':
                    line.start('price'); line.complete('price')
                if stock.symbol in premkt_got_data:
                    line.start('premkt'); line.complete('premkt')
                elif stock.symbol in premkt_no_data:
                    # No premarket trades — yesterday's close persists; not an error.
                    line.start('premkt'); line.complete('premkt')
                else:
                    # Batch-failed; retry per-symbol.
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
            try:
                stock.calculate_scores(full=recalc_full, weekly=True, silent=True)
                if recalc_full:
                    stock.calculate_scores_batched(silent=True)
                else:
                    stock.calculate_scores(full=False, silent=True)
            except Exception as e:
                line.error('scores')
                line.finalize()
                print(Fore.RED + f'  {stock.symbol} scoring failed ({type(e).__name__}): {e}')
                try:
                    DB.close()
                except Exception:
                    pass
                return False
            line.complete('scores')
            if score_notification_immediate:
                try:
                    from notifications import process_single_score_notification
                    process_single_score_notification(
                        stock.symbol,
                        previous_score=(score_notification_previous or {}).get(stock.symbol),
                        target_date=date.today(),
                        verbose=True,
                    )
                except Exception as e:
                    print(Fore.YELLOW + f"  {stock.symbol} urgent push skipped: {e}")
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

        # Experimental sidecar: write today-only sector ETF staging scores after
        # production scores and any same-day regime reapply are finalized.
        try:
            from sector_etf_staging import write_today_sector_etf_staging_scores
            write_today_sector_etf_staging_scores(target_date=date.today(), verbose=True)
        except Exception as e:
            print(Fore.YELLOW + f"Sector ETF staging scores skipped: {e}")

        # Fetch and cache today's CAD/USD exchange rate
        _fetch_and_cache_exchange_rate()

        # Refresh per-trade barrier_outcomes cache for the trailing window.
        # Outcomes only become definitive once forward price data accumulates,
        # so this is post-close work — skip it during intraday runs to avoid
        # a multi-minute stall on what is nightly-scope computation.
        _is_post_close = current_hour >= 16
        if _is_post_close:
            try:
                from database.barrier_cache import refresh_recent
                n = refresh_recent(days=160, verbose=False)
                print(Fore.CYAN + f"Barrier cache refreshed: {n:,} trailing rows")
            except Exception as e:
                print(Fore.YELLOW + f"Barrier cache refresh skipped: {e}")
        else:
            print(Fore.CYAN + "Barrier cache refresh deferred (intraday — runs post-close)")

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
    trader tp      [premium]   — take-profit targets, breadth-adaptive for calls
    trader stop|sl [premium]   — stop-loss targets, breadth-adaptive for calls

    Premium is the option price paid (e.g. trader tp 3.50 → shows target sell price).
    Omit premium to see percentages only.

    Call targets adjust with current breadth score (same logic as the Dashboard).
    Per-DTE thresholds (option configs split 2026-05-04 — see strategy_config.py):
      30 DTE  (post-v32_optim ship):
        Stressed (breadth ≤ 40):  CALL TP = +42%,  CALL SL = -40%
        Calm     (breadth > 40):  CALL TP = +33%,  CALL SL = -27%
      15 DTE  (Phase 15B C1, prior values):
        Stressed (breadth ≤ 50):  CALL TP = +40%,  CALL SL = -35%
        Calm     (breadth > 50):  CALL TP = +35%,  CALL SL = -30%
    Put targets are fixed (not breadth-adaptive):
        PUT TP = +35%,  PUT SL = -20%
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

    # Sourced from strategy_config (single source of truth — eliminates the
    # H5 ship's missed update of these CLI display values).
    import strategy_config as _scfg_tp
    _tp_cfg = _scfg_tp.STRATEGY_30DTE
    _tp_opt = _tp_cfg.option
    stressed = breadth_score is not None and breadth_score <= _tp_opt.BREADTH_THRESHOLD

    # Call TP/SL — breadth-adaptive (matches Dashboard "Call TP / SL" row)
    call_tp_pct = (_tp_opt.TP_STRESS if stressed else _tp_opt.TP_BASE) * 100
    call_sl_pct = abs(_tp_opt.SL_STRESS if stressed else _tp_opt.SL_BASE) * 100

    # Put TP/SL — fixed (matches Dashboard "Put TP / SL" row)
    put_tp_pct = _tp_opt.PUT_TP * 100
    put_sl_pct = abs(_tp_opt.PUT_SL) * 100

    # Regime header
    if breadth_score is not None:
        thr = _tp_opt.BREADTH_THRESHOLD
        if stressed:
            regime_str = (f"{Fore.YELLOW}Breadth {breadth_score:.0f} - STRESSED (<={thr})"
                          f"  wider targets active{Style.RESET_ALL}")
        else:
            regime_str = (f"{Fore.GREEN}Breadth {breadth_score:.0f} - CALM (>{thr})"
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

    # Dead-hold rule — supersedes the SL when option gaps past trigger
    if _tp_cfg.DEAD_HOLD_ENABLED:
        _dh_t = int(round(_tp_cfg.DEAD_HOLD_TRIGGER_PNL * 100))
        _dh_p = int(round(_tp_cfg.DEAD_HOLD_POPOUT_PNL * 100))
        _dh_p_mult = 1.0 + _tp_cfg.DEAD_HOLD_POPOUT_PNL  # e.g. 0.75 for popout=-25%
        print()
        print(f"  {Fore.YELLOW}DEAD-HOLD (post-SL recovery rule):{Style.RESET_ALL}")
        print(f"    If the SL fires AND option pnl ≤ {_dh_t}% (gap-through), DO NOT sell at the SL.")
        if premium:
            _popout_price = premium * _dh_p_mult
            print(f"    Instead set a stop-limit sell at {Fore.GREEN}${_popout_price:.2f}{Style.RESET_ALL} "
                  f"({_dh_p_mult:.2f}× entry premium = popout to {_dh_p}% pnl).")
        else:
            print(f"    Instead set a stop-limit sell at {Fore.GREEN}{_dh_p_mult:.2f}× entry premium{Style.RESET_ALL} "
                  f"(popout to {_dh_p}% option pnl).")
        print(f"    Anchor is FIXED at entry — does not move with SL drop depth.")
        print(f"    At hard-sell day (Day {_tp_cfg.HOLD_DAYS}), force exit at close-bar value.")
        print(f"    Slot stays occupied through the dead-hold window.")
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
    trader alloc <portfolio_cad> [--strategy 30dte|15dte] [--staging sector-etf]

    Prints today's call and put signals with cascade allocation amounts in USD.
    Calls fill slots first by conviction; puts fill remaining slots. Same-symbol
    block across sides. Default strategy is 30 DTE (production).

    Pass --strategy 15dte to use the Phase 15B C1 winner — parallel 15 DTE
    strategy with DD circuit breaker, MaxPos=8, F3F floors=0.40. See
    .claude/docs/trading-strategy.md "15 DTE Variant (C1)" for full details.

    CASCADE (% of portfolio equity, pre-scale) — sourced from strategy_config
    at runtime, so these auto-update when the dataclass is edited:
        30 DTE (v32_optim ship 2026-05-04 — mid cut, monotonic puts):
            Calls  95+:20%  85-94:15%  80-84:10%  75-79:10%  70-74:0% (disabled)
            Puts   <=15:12%  16-20:10%  21-25:8%   (now monotonic by put strength)
        15 DTE (v15_optim ship 2026-05-05 — top-heavy + minimal puts):
            Calls  95+:18%  85-94:17%  80-84:12%  75-79:8%   70-74:0% (disabled)
            Puts   <=15:8%   16-20:8%   21-25:8%   (all at floor for DD safety)
        Live values printed at bottom of output reflect the actual loaded config.

    EXITS:
        30 DTE (hard sell day 15) — v32_optim ship 2026-05-04:
            Calls  TP=+33% / SL=-27%   (adaptive: TP=+42% / SL=-40% when breadth<=40)
            Puts   TP=+35% / SL=-20%
            Hard sell  -40%
        15 DTE (hard sell day 7) — v15_optim ship 2026-05-05:
            Calls  TP=+35% / SL=-30%   (adaptive: TP=+40% / SL=-35% when breadth<=50)
            Puts   TP=+35% / SL=-20%
            Hard sell  -45% (theta scaling for half-DTE)
            DD CIRCUIT BREAKER @ 60% (pause new entries when running DD > 60%)

        DEAD-HOLD POST-SL (both DTEs, ship 2026-05-01):
            When the SL fires AND realized OPTION pnl is at -50% or worse,
            DO NOT SELL. The standard SL exit only applies when pnl > -50%.
            Hold forward bar-by-bar.

            POPOUT IS ON OPTION PRICE, NOT STOCK PRICE.
            Live execution: at the moment SL fires, set a stop-limit sell at
            0.75 × entry premium (the price equivalent to -25% option pnl).
            E.g. entry $5.00 → stop-limit at $3.75. The limit fills on any
            subsequent bar where the option intraday high reaches $3.75
            (calls) or low reaches $3.75 (puts).

            Why option price not stock price: option recovery depends on
            stock movement + time decay + IV change. Same underlying recovery
            recovers a deep-ITM option much faster than a near-ATM option;
            and IV crush after earnings can prevent option recovery even if
            the stock recovers. The MC walks the option pnl per bar via the
            delta+theta+vega closed-form model — your stop-limit on the
            option price approximates this exactly.

            At hard-sell day (Day 15 30 DTE / Day 7 15 DTE), force exit at
            close-bar option value regardless of pnl.

            The position keeps occupying its MaxPos slot throughout the
            dead-hold window. New signals must wait until it resolves.

            Why: SL fires that gap straight through to -50% or worse have
            mean-reversion expectancy; selling at -50%+ locks the loss while
            holding for the popout to -25% recovers ~half the SL loss in
            most paths. Validated N=300 bounded-fill MC. The shipped
            v32_optim and v15_optim campaign results both incorporate
            dead-hold (DEAD_HOLD_ENABLED defaults True in both MC engines).

    F3f BREADTH-DRIVEN ALLOC SCALING:
        30 DTE — floors 0.50 (Phase H4)
        15 DTE — floors 0.40 (Phase 15B C1, stronger weakness contraction)
        Calls  scale = 1.0 if breadth >= 50, linear down to floor at breadth = 20
        Puts   scale = 1.0 if breadth <= 75, linear down to floor at breadth = 95
        Final clamp: [0.25, 1.75]
        Cost basis = tier % × portfolio × scale.

    MAX positions:
        30 DTE: 14 concurrent (shared pool)
        15 DTE: 8 concurrent (shared pool, tighter cap for DD safety)

    USD shown; CAD converted at cached rate.
    """
    from colorama import Fore, Style, init as colorama_init
    colorama_init()

    # --- parse args: portfolio value [--strategy 30dte|15dte] [--staging sector-etf] ---
    if not args:
        print("Usage: trader alloc <portfolio_value_cad> [--strategy 30dte|15dte] [--staging sector-etf]")
        print("Example: trader alloc 50000")
        print("Example: trader alloc 50000 --strategy 15dte")
        print("Example: trader alloc 50000 --staging sector-etf")
        return
    tokens = args.strip().split()
    strategy = '30dte'
    staging_variant = None
    portfolio_arg = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == '--strategy':
            if i + 1 >= len(tokens):
                print(f"{Fore.RED}--strategy requires a value (30dte or 15dte){Style.RESET_ALL}")
                return
            strategy = tokens[i + 1].lower()
            if strategy not in ('30dte', '15dte'):
                print(f"{Fore.RED}--strategy must be 30dte or 15dte, got: {strategy}{Style.RESET_ALL}")
                return
            i += 2
        elif tok == '--staging':
            if i + 1 >= len(tokens):
                print(f"{Fore.RED}--staging requires a value (sector-etf){Style.RESET_ALL}")
                return
            staging_variant = tokens[i + 1].lower()
            if staging_variant != 'sector-etf':
                print(f"{Fore.RED}--staging must be sector-etf, got: {staging_variant}{Style.RESET_ALL}")
                return
            i += 2
        else:
            portfolio_arg = tok
            i += 1
    if portfolio_arg is None:
        print("Usage: trader alloc <portfolio_value_cad> [--strategy 30dte|15dte] [--staging sector-etf]")
        return
    try:
        portfolio_cad = float(portfolio_arg.replace(',', ''))
        if portfolio_cad < 500:   # treat bare numbers < 500 as thousands (e.g. 75 => 75000)
            portfolio_cad *= 1000
    except ValueError:
        print(f"Invalid portfolio value: {portfolio_arg!r}")
        return
    is_15dte = (strategy == '15dte')

    # --- get CAD/USD rate from DB cache (populated by trader update) ---
    cad_usd_rate, is_fallback = _get_cad_usd_rate()
    if is_fallback:
        print(f"{Fore.YELLOW}Warning: no cached CAD/USD rate found, using {cad_usd_rate:.4f}. Run 'trader update' to populate.{Style.RESET_ALL}")

    portfolio_usd = portfolio_cad * cad_usd_rate

    # --- fetch today's qualifying scores from DB ---
    from database.models.core import Score, AlgorithmVersion, StagingScore
    from peewee import fn

    active_version = AlgorithmVersion.get_active_scores_version()
    score_model = Score
    version = active_version
    staging_version = None
    if staging_variant == 'sector-etf':
        from sector_etf_staging import get_sector_etf_staging_version
        staging_version = get_sector_etf_staging_version(active_version, create=False)
        if staging_version is None:
            print(Fore.RED + "No sector-etf staging score version found. Run 'trader update' first.")
            return
        score_model = StagingScore
        version = staging_version
    version_field = score_model.staging_version if staging_version is not None else score_model.version
    latest_date = (score_model.select(fn.MAX(score_model.date))
                   .where(version_field == version)
                   .scalar())
    if not latest_date:
        print("No scores found in DB.")
        return
    if staging_variant == 'sector-etf':
        active_latest = (Score.select(fn.MAX(Score.date))
                         .where(Score.version == active_version)
                         .scalar())
        if active_latest and latest_date != active_latest:
            print(Fore.YELLOW + f"Warning: sector-etf staging date {latest_date} differs from active date {active_latest}")
        active_count = (Score.select()
                        .where(
                            Score.version == active_version,
                            Score.date == latest_date,
                            Score.overall.is_null(False),
                        )
                        .count())
        staging_count = (StagingScore.select()
                         .where(StagingScore.staging_version == staging_version, StagingScore.date == latest_date)
                         .count())
        if staging_count < active_count:
            print(Fore.YELLOW + f"Warning: sector-etf staging coverage {staging_count}/{active_count} for {latest_date}")

    # call signals: >= 70, sorted descending (75+ first, then 70-74 overflow)
    call_signals_75 = list(
        score_model.select()
        .where(version_field == version,
               score_model.date == latest_date,
               score_model.overall >= 75)
        .order_by(score_model.overall.desc())
    )
    call_signals_70 = list(
        score_model.select()
        .where(version_field == version,
               score_model.date == latest_date,
               score_model.overall >= 70,
               score_model.overall < 75)
        .order_by(score_model.overall.desc())
    )
    all_call_signals = call_signals_75 + call_signals_70  # 75+ first, then 70-74

    # put signals: <= 25, sorted ascending (most extreme = lowest score first)
    put_signals = list(
        score_model.select()
        .where(version_field == version,
               score_model.date == latest_date,
               score_model.overall <= 25)
        .order_by(score_model.overall.asc())
    )

    # Earnings-window put suppression (shipped 2026-04-26): drop puts in [16, 20]
    # when an EarningsDate falls in (latest_date, latest_date + 5 trd days].
    if put_signals:
        from database.models.core import EarningsDate as _ED
        from database.utils.trading_calendar import is_trading_day as _itd
        # Sourced from strategy_config (uses 30 DTE values; both DTEs share
        # these defaults under current ship). Overrideable via env if needed.
        import strategy_config as _scfg_supp
        _supp_cfg = _scfg_supp.STRATEGY_30DTE
        _SUPP_DAYS = _supp_cfg.EARN_SUPP_PUT_DAYS
        _SUPP_MIN  = _supp_cfg.EARN_SUPP_PUT_MIN_OV
        _SUPP_MAX  = _supp_cfg.EARN_SUPP_PUT_MAX_OV
        # Compute window end (5 trd days forward from latest_date)
        _win_end = latest_date
        _n = _SUPP_DAYS
        while _n > 0:
            _win_end += timedelta(days=1)
            if _itd(_win_end): _n -= 1
        # Bulk lookup earnings in window for all put symbols
        _put_syms = [s.symbol_id for s in put_signals]
        _ed_rows = list(_ED.select(_ED.symbol, _ED.date)
                        .where((_ED.symbol.in_(_put_syms)) & (_ED.date > latest_date) & (_ED.date <= _win_end))
                        .tuples())
        _ed_syms = {sym for sym, _d in _ed_rows}
        _orig_n = len(put_signals)
        put_signals = [s for s in put_signals
                       if not (_SUPP_MIN <= int(s.overall) <= _SUPP_MAX and s.symbol_id in _ed_syms)]
        _dropped = _orig_n - len(put_signals)
        if _dropped:
            print(Fore.YELLOW + f'  EARN_SUPP_PUT: dropped {_dropped} put signals in [{_SUPP_MIN},{_SUPP_MAX}] with earnings within {_SUPP_DAYS} trd days')

    # --- tier definitions sourced from strategy_config (DTE-aware) ---
    import strategy_config as _scfg
    _alloc_cfg = _scfg.STRATEGY_15DTE if is_15dte else _scfg.STRATEGY_30DTE
    _t = _alloc_cfg.TIER_ALLOC
    _pt = _alloc_cfg.PUT_TIER_ALLOC
    CALL_TIERS = [
        (95, float('inf'), _t['ultra'],    '95+'),
        (85, 95,           _t['top'],      '85-94'),
        (80, 85,           _t['mid'],      '80-84'),
        (75, 80,           _t['low'],      '75-79'),
        (70, 75,           _t['overflow'], '70-74'),
    ]
    PUT_TIERS = [
        (None, 15, _pt['put_top'], '<=15'),
        (16,   20, _pt['put_mid'], '16-20'),
        (21,   25, _pt['put_low'], '21-25'),
    ]

    # --- DTE-strategy specific knobs sourced from strategy_config ---
    MAX_POSITIONS      = _alloc_cfg.MAX_POSITIONS
    F3F_CALL_FLOOR     = _alloc_cfg.F3F_CALL_FLOOR
    F3F_PUT_FLOOR      = _alloc_cfg.F3F_PUT_FLOOR
    DD_CIRCUIT_BREAKER = getattr(_alloc_cfg, 'DD_CIRCUIT_BREAKER', 0.60)
    DD_SOFT_BAND_LO    = _alloc_cfg.DD_SOFT_BAND_LO
    DD_SOFT_BAND_HI    = _alloc_cfg.DD_SOFT_BAND_HI
    DD_SOFT_CALL_FLOOR = _alloc_cfg.DD_SOFT_CALL_FLOOR
    DTE_LABEL          = '15 DTE' if is_15dte else '30 DTE'
    HARD_SELL_PCT      = int(round(abs(_alloc_cfg.HARD_SELL_LOSS) * 100))
    HOLD_DAYS_LBL      = f'day {_alloc_cfg.HOLD_DAYS}'

    # --- F3f breadth-driven alloc scaling (shipped 2026-04-24) ---
    F3F_CALL_THRESH = _alloc_cfg.F3F_CALL_THRESH
    F3F_CALL_LOW    = _alloc_cfg.F3F_CALL_LOW
    F3F_PUT_THRESH  = _alloc_cfg.F3F_PUT_THRESH
    F3F_PUT_HIGH    = _alloc_cfg.F3F_PUT_HIGH
    SCALE_FLOOR     = _alloc_cfg.ALLOC_SCALE_FLOOR
    SCALE_CEIL      = _alloc_cfg.ALLOC_SCALE_CEIL

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

    _opt_alloc = _alloc_cfg.option
    stressed = breadth_score is not None and breadth_score <= _opt_alloc.BREADTH_THRESHOLD
    # Display percentages from strategy_config (TP/SL are stored as decimals;
    # multiply by 100 for the call_tp/call_sl display ints).
    call_tp = int(round((_opt_alloc.TP_STRESS if stressed else _opt_alloc.TP_BASE) * 100))
    call_sl = int(round(abs(_opt_alloc.SL_STRESS if stressed else _opt_alloc.SL_BASE) * 100))

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
    print(f"  Signals date: {latest_date}  |  {DTE_LABEL}  |  hard sell {HOLD_DAYS_LBL}  |  max {MAX_POSITIONS} positions (shared)")
    if staging_variant == 'sector-etf':
        print(f"  {Fore.MAGENTA}SCORING: sector-etf STAGING s{version.id} based on active {active_version.production_label} (db:{active_version.id}); dashboard remains active scoring{Style.RESET_ALL}")
    if is_15dte:
        print(f"  {Fore.MAGENTA}*** 15 DTE C1 strategy active *** "
              f"DD circuit breaker @ 60% (pause new entries when running DD > 60%){Style.RESET_ALL}")
    print()

    # --- today's guideline (current production: v21 scoring + v18 strategy + F3f alloc) ---
    brd_col = Fore.YELLOW if stressed else Fore.GREEN
    brd_lbl = "STRESSED" if stressed else "CALM"
    brd_txt = f"{breadth_score:.0f} {brd_lbl}" if breadth_score is not None else "n/a"
    reg_lbl = "BULL" if regime_mult >= 1.02 else "STRESS" if regime_mult <= 0.95 else "NEUTRAL"
    reg_col = Fore.GREEN if regime_mult >= 1.02 else Fore.YELLOW if regime_mult <= 0.95 else Fore.CYAN

    strategy_label = "v15_optim (15 DTE, ship 2026-05-05)" if is_15dte else "v32_optim (30 DTE, ship 2026-05-04)"
    print(f"  {Fore.WHITE}{Style.BRIGHT}GUIDELINE (v27+ scoring + {strategy_label} + F3f alloc){Style.RESET_ALL}")
    print(f"    Breadth:      {brd_col}{brd_txt}{Style.RESET_ALL}"
          f"    Regime mult:  {reg_col}{regime_mult:.3f} ({reg_lbl}){Style.RESET_ALL}  (display only - alloc uses breadth)")
    # Read live stress-band TP/SL + breadth threshold from already-loaded config
    _stress_tp = int(round(_alloc_cfg.option.TP_STRESS * 100))
    _stress_sl = int(round(abs(_alloc_cfg.option.SL_STRESS) * 100))
    _brd_thr   = _alloc_cfg.option.BREADTH_THRESHOLD
    _put_tp    = int(round(_alloc_cfg.option.PUT_TP * 100))
    _put_sl    = int(round(abs(_alloc_cfg.option.PUT_SL) * 100))
    print(f"    Call exits:   {Fore.GREEN}TP=+{call_tp}%  SL=-{call_sl}%{Style.RESET_ALL}"
          f"   (adaptive: +{_stress_tp}/-{_stress_sl} when breadth<={_brd_thr})")
    print(f"    Put exits:    {Fore.RED}TP=+{_put_tp}%  SL=-{_put_sl}%{Style.RESET_ALL}   (PUT_SL fixed; hold=0)")
    print(f"    Hard sell:    {Fore.YELLOW}-{HARD_SELL_PCT}%{Style.RESET_ALL}   {HOLD_DAYS_LBL}")
    if is_15dte:
        print(f"    F3f floors:   {Fore.CYAN}call={F3F_CALL_FLOOR:.2f} / put={F3F_PUT_FLOOR:.2f}{Style.RESET_ALL}"
              f"   (stronger contraction; 15 DTE C1)")
    print(f"    DD breaker:   {Fore.YELLOW}{int(DD_CIRCUIT_BREAKER*100)}%{Style.RESET_ALL}   (pause new entries when running portfolio DD > {int(DD_CIRCUIT_BREAKER*100)}%; mirror of monte_carlo.py)")

    # SAW Put U-curve display (30 DTE shipped 2026-05-08; 15 DTE shipped 2026-05-09)
    if getattr(_alloc_cfg, 'SAW_PUT_UCURVE_ENABLED', False):
        _saw_shape = _alloc_cfg.SAW_PUT_UCURVE_SHAPE
        _saw_mid = _alloc_cfg.SAW_PUT_UCURVE_MIDPOINT
        _saw_hw  = _alloc_cfg.SAW_PUT_UCURVE_HALFWIDTH
        _saw_fl  = _alloc_cfg.SAW_PUT_UCURVE_FLOOR
        _saw_ce  = _alloc_cfg.SAW_PUT_UCURVE_CEIL
        _saw_pk  = _alloc_cfg.SAW_PUT_UCURVE_POWER
        _saw_k   = getattr(_alloc_cfg, 'SAW_PUT_UCURVE_K', 5.0)
        # Effect description depends on whether amplification is enabled
        if _saw_ce > 1.0:
            _saw_eff = (f"sector-breadth dampener on put alloc; contracts to {_saw_fl:.2f}x "
                        f"in mid-zone bad zone (~{_saw_mid:.0f}+/-{_saw_hw:.0f}), "
                        f"amplifies to {_saw_ce:.2f}x at extremes (mean-reversion alpha)")
        else:
            _saw_eff = (f"sector-breadth dampener on put alloc; PURE CONTRACTION "
                        f"to {_saw_fl:.2f}x in mid-zone (~{_saw_mid:.0f}+/-{_saw_hw:.0f}), "
                        f"no extreme-breadth amplification")
        # Shape-specific param display
        if _saw_shape == 'sigmoid':
            _saw_params = f"{_saw_shape} mid={_saw_mid:.0f} hw={_saw_hw:.0f} floor={_saw_fl:.2f} ceil={_saw_ce:.2f} K={_saw_k:.1f}"
        else:
            _saw_params = f"{_saw_shape} mid={_saw_mid:.0f} hw={_saw_hw:.0f} floor={_saw_fl:.2f} ceil={_saw_ce:.2f} power={_saw_pk:.1f}"
        print(f"    SAW Put U-curve: {Fore.GREEN}ENABLED{Style.RESET_ALL}   "
              f"({_saw_params}; {_saw_eff})")
    else:
        print(f"    SAW Put U-curve: {Fore.RED}disabled{Style.RESET_ALL}")
    if DD_SOFT_BAND_HI > DD_SOFT_BAND_LO:
        print(f"    DD soft-band: {Fore.YELLOW}{int(DD_SOFT_BAND_LO*100)}%-{int(DD_SOFT_BAND_HI*100)}%{Style.RESET_ALL}   (scale call alloc 1.0x->{DD_SOFT_CALL_FLOOR:.2f}x linearly within band; calls only; H3 ship 2026-05-04)")
    # CTSL - Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08)
    if _alloc_cfg.CTSL_ENABLED:
        print(f"    CTSL:         {Fore.YELLOW}enabled{Style.RESET_ALL}   (score-stage lift on CT signals; "
              f"call: tm<={_alloc_cfg.CTSL_CALL_TREND_MAX} -> target {_alloc_cfg.CTSL_CALL_TARGET:.0f} "
              f"floor {_alloc_cfg.CTSL_CALL_TIER_FLOOR:.0f}; "
              f"put: tm>={_alloc_cfg.CTSL_PUT_TREND_MIN} -> target {_alloc_cfg.CTSL_PUT_TARGET:+.1f}; "
              f"stacks ADDITIVELY on CT_PROMOTE; Stage 3 5y DD -0.40pp, ship 2026-05-08)")
    else:
        print(f"    CTSL:         {Fore.RED}disabled{Style.RESET_ALL}")
    # Dead-hold post-SL execution rule (read live from active DTE config)
    if _alloc_cfg.DEAD_HOLD_ENABLED:
        _dh_t = int(round(_alloc_cfg.DEAD_HOLD_TRIGGER_PNL * 100))
        _dh_p = int(round(_alloc_cfg.DEAD_HOLD_POPOUT_PNL * 100))
        # Compute the absolute popout multiplier: what fraction of entry premium
        # the OPTION must reach for popout to fire. e.g. -25% pnl = 0.75x entry.
        _dh_p_mult = 1.0 + _alloc_cfg.DEAD_HOLD_POPOUT_PNL
        print(f"    Dead-hold:    {Fore.YELLOW}trigger={_dh_t}% / popout={_dh_p}%{Style.RESET_ALL}   "
              f"(when SL fires AND option pnl <= {_dh_t}%, do NOT sell - hold forward.")
        print(f"                  Stop-limit sell at {_dh_p_mult:.2f}x ENTRY premium "
              f"(anchor set at entry, FIXED - does not move with the SL drop).")
        print(f"                  e.g. entry $5.00 -> limit $3.75 (regardless of how deep the SL dropped).")
        print(f"                  Fills on intraday popout to >={_dh_p}% option pnl. "
              f"Hard-sell day forces close. Slot occupied through window. ship 2026-05-01)")
    # F3F display: read live thresholds and floors per active DTE
    _f3 = _alloc_cfg
    print(f"    F3f scale:    calls x{call_scale:.2f}   puts x{put_scale:.2f}"
          f"   (calls 1.0->{_f3.F3F_CALL_FLOOR:.2f} as brd {int(_f3.F3F_CALL_THRESH)}->{int(_f3.F3F_CALL_LOW)};"
          f" puts 1.0->{_f3.F3F_PUT_FLOOR:.2f} as brd {int(_f3.F3F_PUT_THRESH)}->{int(_f3.F3F_PUT_HIGH)})")
    # Weak-weekly call filter (D variant, 30 DTE only - disabled on 15 DTE)
    if _alloc_cfg.WEAK_WEEKLY_CALL_DROP:
        _wwc_min = _alloc_cfg.WEAK_WEEKLY_CALL_MIN_OV
        _wwc_max = _alloc_cfg.WEAK_WEEKLY_CALL_MAX_OV
        _wwc_w   = _alloc_cfg.WEAK_WEEKLY_CALL_WADJ_LT
        _wwc_s   = _alloc_cfg.WEAK_WEEKLY_CALL_STOCH_GE
        _stoch_clause = f" AND stoch>={_wwc_s}" if _wwc_s > 0 else ""
        print(f"    Wadj filter:  {Fore.YELLOW}drop calls in [{_wwc_min},{_wwc_max}] when w_adj<{_wwc_w:.0f}{_stoch_clause}{Style.RESET_ALL}"
              f"   (~5/year filtered; v36/v37 wadj-neg residue at 70-74; ship 2026-05-05)")
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


def _cmd_staging(args):
    """Manage inactive staging score versions."""
    from colorama import Fore, Style
    from database.models.core import AlgorithmVersion, StagingScore, StagingVersion
    from peewee import fn

    parts = args.strip().split() if args else []
    sub = parts[0] if parts else 'list'

    if sub == 'list':
        StagingVersion.ensure_schema()
        active_version = AlgorithmVersion.get_active_scores_version()
        rows = list(StagingVersion.select().order_by(StagingVersion.updated_at.desc(), StagingVersion.id.desc()).limit(20))
        if not rows:
            print(f"{Fore.YELLOW}No staging versions found.{Style.RESET_ALL}")
            return
        print(f"\n{Fore.CYAN}=== Staging Versions ==={Style.RESET_ALL}")
        for sv in rows:
            base = sv.base_version
            score_count = StagingScore.select().where(StagingScore.staging_version == sv).count()
            latest_date = (StagingScore.select(fn.MAX(StagingScore.date))
                           .where(StagingScore.staging_version == sv)
                           .scalar())
            active_marker = ' active-base' if sv.base_version_id == active_version.id and sv.status == 'active' else ''
            promoted = f" -> v{sv.promoted_version_id}" if sv.promoted_version_id else ''
            print(
                f"s{sv.id:<3} {sv.variant:<12} base={base.production_label:<4} "
                f"(db:{sv.base_version_id:<3}) "
                f"{sv.status:<8}{active_marker:<13} rows={score_count:<5} "
                f"latest={latest_date or '-'}{promoted}  {sv.label or ''}"
            )
        print()
        return

    if sub == 'migrate-legacy':
        delete_legacy = '--delete-legacy' in parts
        result = StagingVersion.migrate_legacy_algorithm_rows(delete_legacy=delete_legacy)
        print(f"\n{Fore.CYAN}=== Legacy Staging Migration ==={Style.RESET_ALL}")
        if not result['migrated']:
            print(f"{Fore.YELLOW}No legacy inactive AlgorithmVersion rows found.{Style.RESET_ALL}")
            return
        for row in result['migrated']:
            action = 'migrated+deleted' if delete_legacy else 'migrated'
            print(
                f"{Fore.GREEN}{action}:{Style.RESET_ALL} "
                f"legacy v{row['legacy_version_id']} -> staging s{row['staging_version_id']} "
                f"(base v{row['base_version_id']}, rows={row['score_rows']})"
            )
        if not delete_legacy:
            print(f"{Fore.YELLOW}Legacy production-table rows were preserved. Re-run with --delete-legacy to collapse them after review.{Style.RESET_ALL}")
        print()
        return

    print("Usage: trader staging [list|migrate-legacy [--delete-legacy]]")


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

    Strategy options (option-level gross %; defaults come from strategy_config.py):
      --tp        N            base take-profit on option premium, gross %
                               30 DTE default 33; widens to 42 when breadth ≤ 40 (post-v32_optim)
                               15 DTE default 35; widens to 40 when breadth ≤ 50
      --fixed-tp               disable breadth-adaptive TP, use fixed --tp value
      --sl        N            base stop-loss on option premium, gross %
                               30 DTE default 27; widens to 40 when breadth ≤ 40
                               15 DTE default 30; widens to 35 when breadth ≤ 50
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
    # Defaults sourced from strategy_config.STRATEGY_30DTE (single source of
    # truth). The --dte 15 token below switches to STRATEGY_15DTE values for
    # any options not explicitly overridden by other args.
    import strategy_config as _scfg_bt
    _bt_default = _scfg_bt.STRATEGY_30DTE
    _bt_opt = _bt_default.option

    start_str     = '2026-04-01'
    end_str       = None
    capital_cad   = 25_000.0
    min_score_arg = 70.0
    version_token = None
    tp_pct        = _bt_opt.TP_BASE * 100             # call TP % (base)
    tp_stress_pct = _bt_opt.TP_STRESS * 100           # call TP % stressed
    sl_pct        = abs(_bt_opt.SL_BASE) * 100        # call SL % (base)
    sl_stress_pct = abs(_bt_opt.SL_STRESS) * 100      # call SL % stressed
    hard_day      = _bt_default.HOLD_DAYS             # hard-sell calendar day
    max_pos       = _bt_default.MAX_POSITIONS         # max concurrent positions
    quick         = False
    fixed_tp      = False
    fixed_sl      = False
    calls_only    = False
    _dte_strategy_local = '30'

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
            elif tok == '--dte' and i + 1 < len(tokens):
                # Phase 16 (2026-04-28): switch to 15 DTE C1 strategy.
                _dte_strategy_local = tokens[i + 1]
                if _dte_strategy_local not in ('15', '30'):
                    print(Fore.RED + f'--dte must be 15 or 30, got: {_dte_strategy_local}'); return
                if _dte_strategy_local == '15':
                    # Switch defaults to 15 DTE C1. TP/SL on premium are now distinct
                    # per DTE as of v32_optim ship 2026-05-04 — 30 DTE has its own
                    # OptionStrategyConfig instance with TP=0.33/SL=-0.27/breadth=40,
                    # while 15 DTE keeps SHARED_OPTION with TP=0.35/SL=-0.30/breadth=50.
                    _bt_default = _scfg_bt.STRATEGY_15DTE
                    hard_day = _bt_default.HOLD_DAYS
                    max_pos  = _bt_default.MAX_POSITIONS
                i += 2
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
    # 15 DTE ATM: premium ≈ 1.29σ, delta ≈ 0.5  →  σ_trigger = gross% / 100 × (1.29/0.5)
    if _dte_strategy_local == '15':
        _SIGMA_MULT    = 2.58       # 1.29 / 0.5
        _HARD_PCT      = 45.0       # 15 DTE day-7 hard sell ~ -45% (theta scaling)
    else:
        _SIGMA_MULT    = 3.64       # 1.82 / 0.5
        _HARD_PCT      = 40.0       # 30 DTE day-15 hard sell -40% (Phase H5)
    TP_SIGMA_BASE      = tp_pct  / 100 * _SIGMA_MULT
    TP_SIGMA_STRESS    = tp_stress_pct / 100 * _SIGMA_MULT
    SL_SIGMA_BASE      = sl_pct  / 100 * _SIGMA_MULT
    SL_SIGMA_STRESS    = sl_stress_pct / 100 * _SIGMA_MULT
    # Net P&L = gross − slippage (entry 1.0%; TP 0% limit sell; SL 1.3%; Hard 0.5%)
    NET_TP_BASE        = +(tp_pct  / 100 - 0.010)
    NET_TP_STRESS      = +(tp_stress_pct / 100 - 0.010)
    NET_SL_BASE        = -(sl_pct  / 100 + 0.023)
    NET_SL_STRESS      = -(sl_stress_pct / 100 + 0.023)
    NET_HARD           = -(_HARD_PCT / 100 + 0.015)
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

    # Breadth-driven allocation knob (F3f) — sourced from strategy_config
    # (single source of truth). Phase 8/9 ship (2026-05-01) raised
    # F3F_CALL_LOW from 20→30 for 30 DTE.
    _scfg_f3f = _scfg_bt.STRATEGY_15DTE if _dte_strategy_local == '15' else _scfg_bt.STRATEGY_30DTE
    BREADTH_ALLOC_ENABLED = _scfg_f3f.BREADTH_ALLOC_ENABLED
    F3F_CALL_THRESH       = _scfg_f3f.F3F_CALL_THRESH
    F3F_CALL_FLOOR        = _scfg_f3f.F3F_CALL_FLOOR
    F3F_CALL_LOW          = _scfg_f3f.F3F_CALL_LOW
    F3F_PUT_THRESH        = _scfg_f3f.F3F_PUT_THRESH
    F3F_PUT_FLOOR         = _scfg_f3f.F3F_PUT_FLOOR
    F3F_PUT_HIGH          = _scfg_f3f.F3F_PUT_HIGH

    # Cascade allocation — sourced from strategy_config (single source of truth).
    # Mirrors backtest_cascade.py:144 mapping. trader.py uses score-range keys
    # for display ('95+' etc.); strategy_config uses semantic keys ('ultra' etc.).
    # Map at the boundary so any strategy_config edit propagates here automatically.
    _scfg_for_alloc = _scfg_bt.STRATEGY_15DTE if _dte_strategy_local == '15' else _scfg_bt.STRATEGY_30DTE
    TIER_ALLOC = {
        '95+':    _scfg_for_alloc.TIER_ALLOC['ultra'],
        '85-94':  _scfg_for_alloc.TIER_ALLOC['top'],
        '80-84':  _scfg_for_alloc.TIER_ALLOC['mid'],
        '75-79':  _scfg_for_alloc.TIER_ALLOC['low'],
        '70-74':  _scfg_for_alloc.TIER_ALLOC['overflow'],
        # Put-side tiers
        'p<=15':  _scfg_for_alloc.PUT_TIER_ALLOC['put_top'],
        'p16-20': _scfg_for_alloc.PUT_TIER_ALLOC['put_mid'],
        'p21-25': _scfg_for_alloc.PUT_TIER_ALLOC['put_low'],
    }

    # DD circuit breaker — sourced from strategy_config (was hardcoded 0.68).
    # OP2 ship on 2026-05-01 lowered to 0.60 for 30 DTE.
    DD_CIRCUIT_BREAKER = getattr(_scfg_f3f, 'DD_CIRCUIT_BREAKER', 0.60)

    # H3 — DD-soft band call alloc contraction (shipped 2026-05-04 for 30 DTE).
    DD_SOFT_BAND_LO    = _scfg_f3f.DD_SOFT_BAND_LO
    DD_SOFT_BAND_HI    = _scfg_f3f.DD_SOFT_BAND_HI
    DD_SOFT_CALL_FLOOR = _scfg_f3f.DD_SOFT_CALL_FLOOR

    # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08).
    # Mirrors monte_carlo.py / backtest_cascade.py wiring for the deterministic
    # backtest path. Disabled on 15 DTE per registry.
    CTSL_ENABLED = _scfg_f3f.CTSL_ENABLED

    # Put-side fixed TP/SL — sourced from strategy_config option-level params.
    PUT_TP_SIGMA  = _scfg_f3f.PUT_TP_SIGMA
    PUT_SL_SIGMA  = _scfg_f3f.PUT_SL_SIGMA
    PUT_NET_TP    = _scfg_f3f.option.PUT_NET_TP
    PUT_NET_SL    = _scfg_f3f.option.PUT_NET_SL
    PUT_THRESHOLD = float(_scfg_f3f.PUT_THRESHOLD)
    # Put SL hard-hold — sourced from option config.
    PUT_SL_HOLD_BARS_DEFAULT = _scfg_f3f.option.PUT_SL_HOLD_BARS_DEFAULT
    PUT_SL_HOLD_BARS_MONDAY  = _scfg_f3f.option.PUT_SL_HOLD_BARS_MONDAY

    # Counter-trend cascade promotion (Path B / V2, shipped 2026-04-21).
    # ct_put = (overall<=25 AND TREND>=CT_PUT_TREND_MIN) -> override tier 'p<=15' (15%)
    # ct_call = (overall>=70 AND TREND<=CT_CALL_TREND_MAX) -> override tier '95+' (25%)
    CT_PROMOTE        = True
    CT_PUT_TREND_MIN  = 80
    CT_CALL_TREND_MAX = 20
    CT_CALL_TIER      = '95+'    # maps to monte_carlo.py 'ultra' (25%)
    CT_PUT_TIER       = 'p<=15'  # maps to monte_carlo.py 'put_top' (15%)

    # EARN_SUPP_PUT — RETIRED 2026-05-06 (replaced by score-stage PESS in v39).
    # The score itself now lifts puts in [16,20] with earnings ≤5 trd days
    # OUT of put-qualifying range, so the cascade-stage filter is redundant.
    EARN_SUPP_PUT          = False
    EARN_SUPP_PUT_DAYS     = 5
    EARN_SUPP_PUT_MIN_OV   = 16
    EARN_SUPP_PUT_MAX_OV   = 20

    # Weak-weekly call filter — SHIPPED 2026-05-05 (D variant, 30 DTE only).
    # Drops calls in [70, 84] when w_adj < 0 AND stoch >= 35.
    # 30 DTE: enabled. 15 DTE: disabled (not validated under bounded-fill MC).
    # See experiments/call_wadj_70_filter/FINDINGS.md.
    # Pre-existing bug fix 2026-05-08: was `_alloc_cfg` (only defined in
    # _cmd_alloc) — now correctly reads from `_scfg_f3f` (the per-DTE config
    # in _cmd_backtest). Surfaced by CTSL smoke test.
    WEAK_WEEKLY_CALL_DROP     = _scfg_f3f.WEAK_WEEKLY_CALL_DROP
    WEAK_WEEKLY_CALL_MIN_OV   = _scfg_f3f.WEAK_WEEKLY_CALL_MIN_OV
    WEAK_WEEKLY_CALL_MAX_OV   = _scfg_f3f.WEAK_WEEKLY_CALL_MAX_OV
    WEAK_WEEKLY_CALL_WADJ_LT  = _scfg_f3f.WEAK_WEEKLY_CALL_WADJ_LT
    WEAK_WEEKLY_CALL_STOCH_GE = _scfg_f3f.WEAK_WEEKLY_CALL_STOCH_GE

    # ── 15 DTE C1 strategy override (Phase 16, 2026-04-28) ───────────────────
    # When --dte 15 is passed, override key constants to match
    # monte_carlo_15dte.py / backtest_cascade_15dte.py defaults.
    DD_CIRCUIT_BREAKER_LOCAL = DD_CIRCUIT_BREAKER   # 0.68 for 30 DTE; overridden below for 15 DTE
    if _dte_strategy_local == '15':
        # F3F floors already sourced from STRATEGY_15DTE via _scfg_f3f above.
        # 15 DTE option-aligned sigma barriers are already sourced from
        # STRATEGY_15DTE via _scfg_f3f above.
        # Hard sell scaled for 15 DTE day-7 (~ -46% empirical theta scaling)
        # Net hard = -0.45 - 0.010 entry - 0.005 hard exit = -0.465
        DD_CIRCUIT_BREAKER_LOCAL = 0.60   # pause new entries when DD > 60%

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
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                     Score.weight_info, Score.stoch)
             .where(
                 (Score.version  == version)
                 & (Score.overall >= min_score_arg)
                 & (Score.date   >= start_date)
                 & (Score.date   <= end_date)
             )
             .order_by(Score.date, Score.overall.desc(), Score.symbol)
             .namedtuples()
    )

    # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08).
    # Apply BEFORE WEAK_WEEKLY_CALL_DROP / EARN_SUPP_PUT so subsequent filters
    # see post-CTSL overall (matches monte_carlo.py + backtest_cascade.py).
    if CTSL_ENABLED and raw:
        from backtest_cascade import _apply_ctsl_to_signals as _ctsl_apply
        raw = _ctsl_apply(raw, 'call')
        raw.sort(key=lambda s: (s.date, -int(s.overall), s.symbol))

    # Weak-weekly call filter (shipped 2026-05-05, 30 DTE D variant)
    if WEAK_WEEKLY_CALL_DROP and raw:
        import json as _json_wc
        kept_c = []
        dropped_c = 0
        for s in raw:
            ov = int(s.overall)
            if not (WEAK_WEEKLY_CALL_MIN_OV <= ov <= WEAK_WEEKLY_CALL_MAX_OV):
                kept_c.append(s); continue
            wadj = None
            wi_raw = getattr(s, 'weight_info', None)
            if wi_raw:
                try:
                    wi = _json_wc.loads(wi_raw) if isinstance(wi_raw, str) else wi_raw
                    wa = wi.get('w_adj')
                    if wa is None: wa = wi.get('weekly_adj')
                    if wa is not None: wadj = float(wa)
                except Exception:
                    wadj = None
            if wadj is None or wadj >= WEAK_WEEKLY_CALL_WADJ_LT:
                kept_c.append(s); continue
            if WEAK_WEEKLY_CALL_STOCH_GE > 0:
                stoch_v = getattr(s, 'stoch', None)
                if stoch_v is None or int(stoch_v) < WEAK_WEEKLY_CALL_STOCH_GE:
                    kept_c.append(s); continue
            dropped_c += 1
        if dropped_c:
            stoch_str = f' AND stoch>={WEAK_WEEKLY_CALL_STOCH_GE}' if WEAK_WEEKLY_CALL_STOCH_GE > 0 else ''
            print(f'  WEAK_WEEKLY_CALL_DROP: dropped {dropped_c} calls in [{WEAK_WEEKLY_CALL_MIN_OV},{WEAK_WEEKLY_CALL_MAX_OV}] AND w_adj<{WEAK_WEEKLY_CALL_WADJ_LT}{stoch_str} ({len(kept_c):,} remain)')
        raw = kept_c

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

    # CTSL — apply put-side dampener mirror.
    if CTSL_ENABLED and put_raw:
        from backtest_cascade import _apply_ctsl_to_signals as _ctsl_apply
        put_raw = _ctsl_apply(put_raw, 'put')
        put_raw.sort(key=lambda s: (s.date, int(s.overall), s.symbol))

    # Earnings-window put suppression (shipped 2026-04-26)
    if EARN_SUPP_PUT and put_raw:
        from database.models.core import EarningsDate
        from database.utils.trading_calendar import is_trading_day
        syms_p = {s.symbol for s in put_raw}
        d_min = min(s.date for s in put_raw); d_max = max(s.date for s in put_raw)
        ed_rows = list(EarningsDate
                       .select(EarningsDate.symbol, EarningsDate.date)
                       .where((EarningsDate.symbol.in_(list(syms_p)))
                              & (EarningsDate.date >= d_min - timedelta(days=10))
                              & (EarningsDate.date <= d_max + timedelta(days=EARN_SUPP_PUT_DAYS * 2 + 7)))
                       .tuples())
        ed_map = defaultdict(list)
        for sym, d in ed_rows:
            ed_map[sym].append(d)
        def _fwd_n(d, n):
            out = d
            while n > 0:
                out += timedelta(days=1)
                if is_trading_day(out): n -= 1
            return out
        kept_p = []
        dropped_p = 0
        for s in put_raw:
            ov = int(s.overall)
            if not (EARN_SUPP_PUT_MIN_OV <= ov <= EARN_SUPP_PUT_MAX_OV):
                kept_p.append(s); continue
            sym_ed = ed_map.get(s.symbol, [])
            if not sym_ed:
                kept_p.append(s); continue
            win_end = _fwd_n(s.date, EARN_SUPP_PUT_DAYS)
            if any(s.date < ed <= win_end for ed in sym_ed):
                dropped_p += 1; continue
            kept_p.append(s)
        if dropped_p:
            print(f'  EARN_SUPP_PUT: dropped {dropped_p} puts in [{EARN_SUPP_PUT_MIN_OV},{EARN_SUPP_PUT_MAX_OV}] within {EARN_SUPP_PUT_DAYS} trd days of upcoming earnings ({len(kept_p):,} remain)')
        put_raw = kept_p

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

        # 15 DTE C1 — DD circuit breaker (Phase 16, 2026-04-28).
        # When running portfolio DD > DD_CIRCUIT_BREAKER_LOCAL (0.60 for 15 DTE),
        # pause new entries. Existing positions still resolve normally.
        cur_dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0
        if DD_CIRCUIT_BREAKER_LOCAL > 0.0 and cur_dd > DD_CIRCUIT_BREAKER_LOCAL:
            if equity > peak_equity: peak_equity = equity
            if cur_dd > max_dd: max_dd = cur_dd
            equity_curve.append((d, equity))
            continue

        for o in outcomes_by_date.get(d, []):
            if len(open_pos) >= MAX_POSITIONS: break
            if o['symbol'] in open_syms: continue
            is_put  = o.get('side') == 'put'
            scale   = _alloc_scale(d, is_put=is_put)
            # H3: DD-soft-band call alloc contraction (calls only)
            dd_scale = 1.0
            if (not is_put) and DD_SOFT_BAND_HI > DD_SOFT_BAND_LO and cur_dd > DD_SOFT_BAND_LO:
                if cur_dd >= DD_SOFT_BAND_HI:
                    dd_scale = DD_SOFT_CALL_FLOOR
                else:
                    t = (cur_dd - DD_SOFT_BAND_LO) / (DD_SOFT_BAND_HI - DD_SOFT_BAND_LO)
                    dd_scale = 1.0 - t * (1.0 - DD_SOFT_CALL_FLOOR)
            premium = TIER_ALLOC[o['tier']] * scale * dd_scale * equity
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
        print(f'  TP {_marker(tp_pct, _bt_opt.TP_BASE * 100, ".0f")}% gross (+{NET_TP_BASE*100:.1f}% net, {TP_SIGMA_BASE:.3f}σ)')
    if not fixed_sl and n_breadth_days:
        print(f'  SL base: {sl_pct:.0f}% (−{abs(NET_SL_BASE)*100:.1f}% net, {SL_SIGMA_BASE:.3f}σ)  '
              f'stress: {sl_stress_pct:.0f}% (−{abs(NET_SL_STRESS)*100:.1f}% net, {SL_SIGMA_STRESS:.3f}σ)  '
              f'[{n_stress_days}/{n_breadth_days} stress days]')
    else:
        print(f'  SL {_marker(sl_pct, abs(_bt_opt.SL_BASE) * 100, ".0f")}% gross (−{abs(NET_SL_BASE)*100:.1f}% net, {SL_SIGMA_BASE:.3f}σ)')
    # Tier display from strategy_config (same DTE the backtest is using).
    _tdisp = _bt_default.TIER_ALLOC
    print(f'  Tiers: '
          f"95+→{int(_tdisp['ultra']*100)}%  "
          f"85-94→{int(_tdisp['top']*100)}%  "
          f"80-84→{int(_tdisp['mid']*100)}%  "
          f"75-79→{int(_tdisp['low']*100)}%  "
          f"70-74→{int(_tdisp['overflow']*100)}%"
          + (f'  {Fore.YELLOW + Style.BRIGHT}[custom params highlighted in yellow]{Style.RESET_ALL + Fore.WHITE + Style.BRIGHT}'
             if any([
                 tp_pct        != _bt_opt.TP_BASE * 100,
                 sl_pct        != abs(_bt_opt.SL_BASE) * 100,
                 hard_day      != _bt_default.HOLD_DAYS,
                 max_pos       != _bt_default.MAX_POSITIONS,
             ]) else ''))
    print(f'  DD breaker: {int(DD_CIRCUIT_BREAKER*100)}% (pause new entries when running DD > {int(DD_CIRCUIT_BREAKER*100)}%)')
    if DD_SOFT_BAND_HI > DD_SOFT_BAND_LO:
        print(f'  DD soft-band: scale call alloc 1.0×→{DD_SOFT_CALL_FLOOR:.2f}× linearly when running DD ∈ [{int(DD_SOFT_BAND_LO*100)}%, {int(DD_SOFT_BAND_HI*100)}%] (calls only)')
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
    _dte_label = '15 DTE C1' if _dte_strategy_local == '15' else '30 DTE'
    _hard_default = 7 if _dte_strategy_local == '15' else 15
    _max_default  = 8 if _dte_strategy_local == '15' else 14
    print(f'  {_dte_label} ATM  ·  calls ≥ {min_score_arg:.0f}  puts ≤ {PUT_THRESHOLD:.0f}  ·  '
          f'Call TP {tp_mode}  SL {sl_mode}  ·  Put TP 35% SL 20% (fixed; hold=0)  ·  '
          f'hard day-{_cv(HOLD_CALENDAR_DAYS, _hard_default)} −{abs(NET_HARD)*100:.0f}% net')
    # Cascade values dynamic from loaded TIER_ALLOC dict — auto-tracks
    # strategy_config edits without code changes here.
    print(f'  Call cascade '
          f"95+→{TIER_ALLOC['95+']*100:.0f}%  "
          f"85-94→{TIER_ALLOC['85-94']*100:.0f}%  "
          f"80-84→{TIER_ALLOC['80-84']*100:.0f}%  "
          f"75-79→{TIER_ALLOC['75-79']*100:.0f}%  "
          f"70-74→{TIER_ALLOC['70-74']*100:.0f}%  ·  "
          f'Put cascade '
          f"≤15→{TIER_ALLOC['p<=15']*100:.0f}%  "
          f"16-20→{TIER_ALLOC['p16-20']*100:.0f}%  "
          f"21-25→{TIER_ALLOC['p21-25']*100:.0f}%  ·  "
          f'max {_cv(MAX_POSITIONS, _max_default)} pos (calls prioritized)  ·  BE {be*100:.1f}%')
    if _dte_strategy_local == '15':
        print(f'  {Fore.MAGENTA}*** 15 DTE C1 strategy *** '
              f'F3F floors=0.40 (call/put) · DD circuit breaker @ 60%{Style.RESET_ALL}')

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
        try:
            from notifications import capture_current_scores
            _notify_date, _notify_version, _notify_previous = capture_current_scores(target_date=date.today())
        except Exception as e:
            print(Fore.YELLOW + f"Push notification snapshot skipped: {e}")
            _notify_date, _notify_version, _notify_previous = None, None, None
        client.update_stocks(
            full=False,
            score_notification_previous=_notify_previous,
            score_notification_immediate=True,
        )
        Trend.update_trend_data(full=False)
        try:
            from notifications import process_opportunity_exits, process_score_notifications
            process_opportunity_exits(
                target_date=date.today(),
                verbose=True,
            )
            process_score_notifications(
                previous_scores=_notify_previous,
                target_date=_notify_date or date.today(),
                verbose=True,
            )
        except Exception as e:
            print(Fore.YELLOW + f"Push notifications skipped: {e}")
    elif command == 'update-remainder':
        client.update_stocks(only_remainder=True)
        Trend.update_trend_data(full=False)
    elif command == 'notify-scores':
        from notifications import process_score_notifications
        tokens = args.split() if args else []
        dry_run = '--dry-run' in tokens
        unknown_flags = [t for t in tokens if t.startswith('--') and t not in ('--dry-run',)]
        if unknown_flags:
            print(Fore.RED + f"Unknown flag(s): {', '.join(unknown_flags)}")
            print(Fore.YELLOW + 'Usage: trader notify-scores [--dry-run]')
            return
        process_score_notifications(dry_run=dry_run, verbose=True)
    elif command == 'notify-exits':
        from notifications import process_opportunity_exits
        tokens = args.split() if args else []
        dry_run = '--dry-run' in tokens
        unknown_flags = [t for t in tokens if t.startswith('--') and t not in ('--dry-run',)]
        if unknown_flags:
            print(Fore.RED + f"Unknown flag(s): {', '.join(unknown_flags)}")
            print(Fore.YELLOW + 'Usage: trader notify-exits [--dry-run]')
            return
        process_opportunity_exits(dry_run=dry_run, verbose=True)
    elif command in ('update-delisted', 'legacy-oos-update'):
        # Legacy OOS helper: run the normal update pipeline for historical-only
        # symbols that the scheduler skips via Stock.delisted_date. This pulls
        # price history, applies split checks, rebuilds indicators/scores, then
        # refreshes trends. Options are intentionally excluded.
        tokens = args.split() if args else []
        full = '--full' in tokens
        only_remainder = '--only-remainder' in tokens
        unknown_flags = [t for t in tokens if t.startswith('--') and t not in ('--full', '--only-remainder')]
        if unknown_flags:
            print(Fore.RED + f"Unknown flag(s): {', '.join(unknown_flags)}")
            print(Fore.YELLOW + 'Usage: trader update-delisted [--full] [--only-remainder] [SYM ...]')
            return
        symbols = [t.upper() for t in tokens if not t.startswith('--')]
        client.update_stocks(
            full=full,
            with_options=False,
            only_remainder=only_remainder,
            only_delisted=True,
            symbols=symbols or None,
        )
        Trend.update_trend_data(full=full)
    elif command == 'score-missing':
        # Recover stocks that have price history for the latest market date but
        # are missing indicators and/or a current score.  Mirrors the
        # indicators→scores portion of update_stocks without re-pulling price
        # history (the bulk pull already ran; we just need to finish the pipeline).
        #
        # Three cases, all handled in one pass:
        #   1. Indicators absent  → calculate_indicators then calculate_scores
        #   2. Score absent/null  → calculate_scores only
        #   3. Score stale        → score exists but was computed before the latest
        #                           price pull (10am score left after 1pm re-pull
        #                           hung on scoring).  Detected via
        #                           PriceHistory.pulled_at > Score.updated_at + 5min.
        # Usage: trader score-missing
        #        trader score-missing AAPL MSFT VPG   (limit to specific symbols)
        from database.models.core import AlgorithmVersion
        from database.models.technical import PriceHistory as _PH, Indicator as _Ind
        from datetime import timedelta as _td

        _STALE_BUFFER = _td(minutes=5)
        _version = AlgorithmVersion.get_active_scores_version()

        _target_date = _PH.select(fn.MAX(_PH.date)).scalar()
        if not _target_date:
            print(Fore.RED + 'No price history found.')
        else:
            print(Fore.CYAN + f'Target date: {_target_date}  (active version: {_version.git_commit})')

            # Source of truth: every symbol with price history for the target date.
            _ph_pulled = {
                r[0]: r[1]
                for r in _PH.select(_PH.symbol, _PH.pulled_at)
                .where(_PH.date == _target_date)
                .tuples()
            }

            _has_indicators = set(
                r[0] for r in _Ind.select(_Ind.symbol)
                .where(_Ind.date == _target_date)
                .tuples()
            )

            _sc_info = {
                r[0]: (r[1], r[2])  # symbol → (updated_at, overall)
                for r in Score.select(Score.symbol, Score.updated_at, Score.overall)
                .where(Score.version == _version, Score.date == _target_date)
                .tuples()
            }

            _candidates = (
                [s.strip().upper() for s in args.split()] if args
                else sorted(_ph_pulled.keys())
            )

            _needs_indicators, _needs_scores = [], []
            for _sym in _candidates:
                if _sym not in _ph_pulled:
                    continue  # no price history at all — nothing to derive from
                if _sym not in _has_indicators:
                    _needs_indicators.append(_sym)
                    _needs_scores.append(_sym)
                    continue
                _ph_t = _ph_pulled[_sym]
                _sc = _sc_info.get(_sym)
                if _sc is None or _sc[1] is None:
                    _needs_scores.append(_sym)
                elif _ph_t and _sc[0] and _ph_t > _sc[0] + _STALE_BUFFER:
                    _needs_scores.append((_sym))

            _all_work = sorted(set(_needs_indicators + _needs_scores))

            if not _all_work:
                print(Fore.GREEN + f'All {len(_candidates)} stock(s) have current indicators and scores for {_target_date}.')
            else:
                if _needs_indicators:
                    print(Fore.YELLOW + f'  Missing indicators ({len(_needs_indicators)}): ' + ', '.join(_needs_indicators))
                print(Fore.CYAN + f'  Needs scoring ({len(_needs_scores)}): ' + ', '.join(_needs_scores))
                print()
                _ok, _fail = 0, 0
                for _sym in _all_work:
                    _stock = Stock.get_or_none(Stock.symbol == _sym)
                    if not _stock:
                        print(Fore.YELLOW + f'  {_sym}: not in DB')
                        continue
                    _step = '?'
                    try:
                        if _sym in _needs_indicators:
                            _step = 'indicators'
                            _stock.calculate_indicators(False, silent=True)
                            _stock.calculate_indicators(False, weekly=True, silent=True)
                        _step = 'scores'
                        _stock.calculate_scores(full=False, weekly=True, silent=True)
                        _stock.calculate_scores(full=False, silent=True)
                        print(Fore.GREEN + f'  {_sym} ✓')
                        _ok += 1
                    except Exception as _e:
                        print(Fore.RED + f'  {_sym} ✗ [{_step}]  {type(_e).__name__}: {_e}')
                        _fail += 1
                        try:
                            DB.close()
                        except Exception:
                            pass
                print(Fore.CYAN + f'\n{_ok} recovered, {_fail} failed.')
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
        do_auto   = False           # market-hours-aware lookback (10y off-hours, 5y in/near market)
        do_rebuild_parquets = False  # --rebuild-parquets: refresh experiments/*/build_features.py outputs

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
                elif stripped == 'auto':
                    do_auto = True
                elif stripped == 'rebuild-parquets':
                    do_rebuild_parquets = True
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

        # --auto: pick 10y if safely off-hours (post-close, pre-pre-open, weekends),
        # otherwise 5y. Helps avoid 4h+ recalcs colliding with morning prep.
        # Explicit --full overrides --auto. --auto without --full is the smart default.
        if do_auto and not do_full:
            do_full = _is_off_market_hours()
            mode_str = '10y (safely off-hours)' if do_full else '5y (within market hours / 2h pre-open buffer)'
            print(Fore.CYAN + f"--auto: {mode_str}")

        if not components:
            # Default scope: scoring components only. weekly_scores and dte
            # recommendations are derived pipelines that don't depend on the
            # scoring formula, so a `--force` run after a version bump should
            # not pay their cost. Use `--all` to opt into rebuilding them.
            from recalculate_scores import DAILY_COMPONENTS
            components = DAILY_COMPONENTS.copy()

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
        # Phase 16 (2026-04-28): runs BOTH 30 DTE and 15 DTE assessments + temporals
        # so the dashboard stays in sync for both strategy variants.
        if do_force:
            print(Fore.CYAN + '\n── historic-update ──' + Style.RESET_ALL)
            from historic_peaks import update_historic_peaks
            update_historic_peaks()

            from assess_scores import run_all_windows as assess_run_all
            _RECALC_WINDOWS = [365, 730, 1095, 1825, 3650]

            # Assess combos are decided by strategy_config.assess_combos() —
            # under current ship (both DTEs alias SHARED_OPTION) this returns
            # [('30','wr'), ('30','tp')]; the (15,'tp') run is mathematically
            # identical to (30,'tp') and the API serves it from the (30,'tp')
            # row. If a future ship diverges 15 DTE option params, identity
            # breaks and ('15','tp') is added back automatically.
            #
            # See assess_scores.py "Phase 17" + strategy_config.assess_combos()
            # for the design rationale.
            import strategy_config as _strategy_cfg
            _ASSESS_COMBOS = _strategy_cfg.assess_combos()
            for _dte_s, _metric in _ASSESS_COMBOS:
                print(Fore.CYAN + Style.BRIGHT
                      + f'\n══════════════════════════════════════════════════════════════'
                      + f'\n  ASSESS — DTE strategy: {_dte_s}, metric: {_metric}'
                      + f'\n══════════════════════════════════════════════════════════════'
                      + Style.RESET_ALL)
                assess_run_all(symbol, _RECALC_WINDOWS, force=True,
                               dte_strategy=_dte_s, metric=_metric)

            # Temporal backtest stats run ONCE per DTE strategy (independent
            # of the assess combo loop, so dedupe of (15,'tp') doesn't skip
            # the 15 DTE temporal computation). Was previously inside the
            # loop which duplicated the 30 DTE temporal pass.
            print(Fore.CYAN + '\n── backtest-temporal (30 DTE H5) ──' + Style.RESET_ALL)
            try:
                from backtest_cascade import compute_and_store_temporal
                compute_and_store_temporal()
                print('  30 DTE temporal stats stored.')
            except Exception as _e:
                print(Fore.YELLOW + f'  Warning: 30 DTE backtest-temporal failed: {_e}' + Style.RESET_ALL)

            print(Fore.CYAN + '\n── backtest-temporal (15 DTE C1) ──' + Style.RESET_ALL)
            try:
                from backtest_cascade_15dte import compute_and_store_temporal as _temp15
                _temp15(dte_strategy='15')
                print('  15 DTE temporal stats stored.')
            except Exception as _e:
                print(Fore.YELLOW + f'  Warning: 15 DTE backtest-temporal failed: {_e}' + Style.RESET_ALL)

            # Optional: rebuild experiment parquets so follow-up sweeps see v{N}
            # data immediately (no manual `trader rebuild-parquets` step).
            # Default OFF — explicit flag only. Adds 5–15 min depending on
            # active experiments under experiments/*/build_features.py.
            if do_rebuild_parquets:
                _run_rebuild_parquets()
    elif command == 'rebuild-parquets':
        # Rebuild .cache/<exp>/*.parquet from experiments/*/build_features.py.
        # Skips experiments whose cache already contains a parquet tagged with
        # the active AlgorithmVersion id (use `--all` to force-rebuild).
        # Use `--exp <name>` to rebuild a specific experiment only.
        exp_filter = None
        do_all_p = False
        if args:
            arg_list = args.split(' ')
            i = 0
            while i < len(arg_list):
                a = arg_list[i]
                if a == '--all':
                    do_all_p = True
                elif a == '--exp' and i + 1 < len(arg_list):
                    i += 1
                    exp_filter = arg_list[i]
                elif a.startswith('--exp='):
                    exp_filter = a.split('=', 1)[1]
                i += 1
        _run_rebuild_parquets(exp_filter=exp_filter, do_all=do_all_p)
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
        # DTE strategy selection (Phase 16, 2026-04-28):
        #   No --dte flag → run BOTH 30 DTE and 15 DTE (the user's expected default)
        #   --dte 30      → only 30 DTE
        #   --dte 15      → only 15 DTE
        #   --dte both    → both (explicit, same as no flag)
        # Metric selection (Phase 17, 2026-04-29):
        #   No --metric flag → run BOTH wr and tp (so both dashboard tabs stay in sync)
        #   --metric wr     → directional Win Rate only
        #   --metric tp     → option Take Profit % only
        #   --metric both   → both (explicit)
        dte_strategies_to_run = None   # filled in below
        dte_explicit = None
        metric_explicit = None
        metrics_to_run = None
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
                elif arg == '--dte':
                    if i + 1 >= len(tokens):
                        print(Fore.RED + '--dte requires a value (15, 30, or both).')
                        return
                    dte_explicit = tokens[i + 1]
                    if dte_explicit not in ('15', '30', 'both'):
                        print(Fore.RED + f'--dte must be 15, 30, or both, got: {dte_explicit}')
                        return
                    i += 2
                elif arg == '--metric':
                    if i + 1 >= len(tokens):
                        print(Fore.RED + '--metric requires a value (wr, tp, or both).')
                        return
                    metric_explicit = tokens[i + 1]
                    if metric_explicit not in ('wr', 'tp', 'both'):
                        print(Fore.RED + f'--metric must be wr, tp, or both, got: {metric_explicit}')
                        return
                    i += 2
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
            # Resolve which DTE strategies to run.
            # Default (no --dte): run BOTH 30 DTE and 15 DTE so the dashboard stays in sync.
            # --dte 30 / --dte 15 / --dte both: explicit selection.
            if dte_explicit is None or dte_explicit == 'both':
                dte_strategies_to_run = ['30', '15']
            else:
                dte_strategies_to_run = [dte_explicit]
            if metric_explicit is None or metric_explicit == 'both':
                metrics_to_run = ['wr', 'tp']
            else:
                metrics_to_run = [metric_explicit]

            # Build the (dte, metric) combo list using assess_combos() from
            # strategy_config so shared-option dedup is applied consistently:
            # - WR emitted once under '30' (DTE-agnostic)
            # - TP% emitted once per unique OptionStrategyConfig instance;
            #   when both DTEs alias SHARED_OPTION, only ('30','tp') is emitted
            #   and the API serves dte=15&metric=tp from that single run.
            import strategy_config as _strategy_cfg
            if dte_explicit is None or dte_explicit == 'both':
                _full_combos = _strategy_cfg.assess_combos()
                if metric_explicit is not None and metric_explicit != 'both':
                    _combos = [(d, m) for d, m in _full_combos if m == metric_explicit]
                else:
                    _combos = list(_full_combos)
            else:
                # Single explicit DTE: WR canonical under '30', TP% under requested DTE.
                _combos = []
                if metric_explicit is None or metric_explicit in ('wr', 'both'):
                    _combos.append(('30', 'wr'))
                if metric_explicit is None or metric_explicit in ('tp', 'both'):
                    _combos.append((dte_explicit, 'tp'))

            _historic_done = False
            for _dte_s, _metric in _combos:
                if len(_combos) > 1:
                    print(Fore.CYAN + Style.BRIGHT
                          + f'\n══════════════════════════════════════════════════════════════'
                          + f'\n  DTE strategy: {_dte_s}, metric: {_metric}'
                          + f'\n══════════════════════════════════════════════════════════════'
                          + Style.RESET_ALL)

                if days is not None:
                    # Explicit lookback: run just that one window
                    assess_run(symbol, days, version=assess_version, force=do_force,
                               regime_adjust=do_regime_adjust,
                               dte_strategy=_dte_s, metric=_metric)
                else:
                    if do_regime_adjust:
                        for win_label, win_days in _ASSESS_WINDOWS:
                            print(Fore.CYAN + f'\n── assess {win_label} ({win_days}d) ──' + Style.RESET_ALL)
                            assess_run(symbol, win_days, version=assess_version, force=do_force,
                                       regime_adjust=do_regime_adjust,
                                       dte_strategy=_dte_s, metric=_metric)
                    else:
                        from assess_scores import run_all_windows as assess_run_all
                        assess_run_all(symbol, [d for _, d in _ASSESS_WINDOWS],
                                       version=assess_version, force=do_force,
                                       dte_strategy=_dte_s, metric=_metric)
                        # historic_peaks is DTE+metric-agnostic — only run once per invocation
                        if not _historic_done:
                            print(Fore.CYAN + '\n── historic-update (post-assess) ──' + Style.RESET_ALL)
                            from historic_peaks import update_historic_peaks
                            update_historic_peaks()
                            _historic_done = True

            # backtest-temporal is DTE-specific but metric-agnostic. Run once per DTE
            # in dte_strategies_to_run, decoupled from the combo loop so that
            # SHARED_OPTION dedup (which removes '15' from combos) doesn't silently
            # skip the 15 DTE temporal backtest.
            if do_force and days is None and not do_regime_adjust:
                for _dte_s in dte_strategies_to_run:
                    if _dte_s == '15':
                        print(Fore.CYAN + '\n── backtest-temporal (15 DTE C1) ──' + Style.RESET_ALL)
                        try:
                            from backtest_cascade_15dte import compute_and_store_temporal as _temp15
                            _temp15(version=assess_version, dte_strategy='15')
                            print('  15 DTE temporal stats stored.')
                        except Exception as _e:
                            print(Fore.YELLOW + f'  Warning: 15 DTE backtest-temporal failed: {_e}' + Style.RESET_ALL)
                    else:
                        print(Fore.CYAN + '\n── backtest-temporal (30 DTE H5) ──' + Style.RESET_ALL)
                        try:
                            from backtest_cascade import compute_and_store_temporal
                            compute_and_store_temporal(version=assess_version)
                            print('  30 DTE temporal stats stored.')
                        except Exception as _e:
                            print(Fore.YELLOW + f'  Warning: 30 DTE backtest-temporal failed: {_e}' + Style.RESET_ALL)
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
        allow_intraday = False
        if args:
            for tok in args.split():
                if tok == '--intraday':
                    allow_intraday = True
                else:
                    try:
                        window = int(tok)
                    except ValueError:
                        pass
        update_historic_peaks(window_days=window, allow_intraday=allow_intraday)
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
    elif command == 'staging':
        _cmd_staging(args)
    elif command in ('tp', 'stop', 'sl'):
        _cmd_tp_stop(command, args)
    elif command == 'temporal-refresh':
        # Refresh BacktestTemporalStats for both DTEs (~5 min total).
        # Required after any portfolio-stage strategy change (cascade alloc,
        # F3F, DD breaker, DD soft-band, dead-hold, EARN_SUPP_PUT, etc.) that
        # affects the deterministic backtest path. Per-bucket assess WR/TP%
        # is unchanged by portfolio changes, so a full `trader assess --force`
        # is overkill — this command runs only the temporal/calendar pass.
        print(Fore.CYAN + '── backtest-temporal (30 DTE) ──' + Style.RESET_ALL)
        try:
            from backtest_cascade import compute_and_store_temporal as _t30
            _t30()
            print('  30 DTE temporal stats stored.')
        except Exception as _e:
            print(Fore.YELLOW + f'  30 DTE failed: {_e}' + Style.RESET_ALL)
        print(Fore.CYAN + '\n── backtest-temporal (15 DTE) ──' + Style.RESET_ALL)
        try:
            from backtest_cascade_15dte import compute_and_store_temporal as _t15
            _t15(dte_strategy='15')
            print('  15 DTE temporal stats stored.')
        except Exception as _e:
            print(Fore.YELLOW + f'  15 DTE failed: {_e}' + Style.RESET_ALL)
    elif command == 'mc-run':
        _cmd_mc_run(args)
    else:
        breakpoint()


def _cmd_mc_run(args):
    """Run Monte Carlo and persist results to the `monte_carlo_run` table.

    Usage:
        trader mc-run                    # 30 DTE, default N (500), all 8 windows
        trader mc-run --dte 15           # 15 DTE
        trader mc-run --n 300             # override N_ITER
        trader mc-run --windows 22-now,5y # subset of windows
        trader mc-run --smoke             # alias: N=100, 22-now only

    Same outputs as running monte_carlo.py / monte_carlo_15dte.py directly,
    plus DB persistence (also enabled when the scripts are run directly).
    Each window upserts on (version × dte × window × params_hash).
    """
    import os as _os
    import subprocess as _sp
    import sys as _sys

    dte = '30'
    n_iter = None
    windows = None
    smoke = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--dte' and i + 1 < len(args):
            dte = args[i + 1]; i += 2; continue
        if a == '--n' and i + 1 < len(args):
            n_iter = args[i + 1]; i += 2; continue
        if a == '--windows' and i + 1 < len(args):
            windows = args[i + 1]; i += 2; continue
        if a == '--smoke':
            smoke = True; i += 1; continue
        i += 1

    if dte not in ('30', '15'):
        print(Fore.RED + f"--dte must be 30 or 15 (got {dte!r})" + Style.RESET_ALL)
        return
    if smoke:
        n_iter = n_iter or '100'
        windows = windows or '22-now'

    script = 'monte_carlo.py' if dte == '30' else 'monte_carlo_15dte.py'
    env = _os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    if n_iter:
        env['N_ITER_OVERRIDE'] = str(n_iter)
    if windows:
        env['WINDOWS_OVERRIDE'] = windows

    print(Fore.CYAN + f'── monte-carlo {dte} DTE'
                   + (f' (N={n_iter})' if n_iter else '')
                   + (f' [{windows}]' if windows else ' [all 8 windows]')
                   + ' ──' + Style.RESET_ALL)
    print(Fore.YELLOW + '  Persisting to monte_carlo_run table on each window completion.' + Style.RESET_ALL)
    rc = _sp.call([_sys.executable, '-u', script], env=env)
    if rc == 0:
        print(Fore.GREEN + '  MC run complete. View on the Monte Carlo tab in Assessment.' + Style.RESET_ALL)
    else:
        print(Fore.RED + f'  MC run exited with code {rc}' + Style.RESET_ALL)

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
