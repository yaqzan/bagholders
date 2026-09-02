#! python3
import yfinance as yf
import defopt
from colorama import init, Fore
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
        stock_info = yf.Ticker(symbol)
        if 'shortName' not in stock_info.info:
            print(Fore.RED + f"Stock {symbol} not found")
            return
        stock = Stock.build(symbol, stock_info.info['shortName'])
        stock.name = stock_info.info['shortName']
        stock.save()
        print(Fore.GREEN + f"Name updated for {symbol}")

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
        stock_info = yf.Ticker(symbol)
        if 'shortName' not in stock_info.info:
            print(Fore.RED + f"Stock {symbol} not found")
            return
        stock = Stock.build(symbol, stock_info.info['shortName'])
        stock.pull_stock_data(stock_info)
        stock.pull_historicals(stock_info)
        print(Fore.GREEN + stock.name)
        threading.Thread(target=self.pull_options, args=(stock.symbol,)).start()
        self.pull_price_history(symbol)
        stock.calculate_indicators_and_scores()

    def pull_price_history(self, symbol, period='max', start=None, line=None, price_key='price'):
        if line:
            line.start(price_key)
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
                return
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
            return

        for index, row in data.iterrows():
            PriceHistory.build(symbol, index, row['Open'][symbol], row['High'][symbol], row['Low'][symbol], row['Close'][symbol], row['Volume'][symbol])
        if line:
            line.complete(price_key)
        else:
            print(Fore.GREEN + f'Price history updated for {symbol}')

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
            return
        latest_data = premarket_data.iloc[-1]
        PriceHistory.build(
            symbol, date.today(), latest_data['Open'], latest_data['High'], latest_data['Low'], latest_data['Close'], latest_data['Volume']
        )
        if line:
            line.complete(premkt_key)
        else:
            print(Fore.GREEN + f'Latest pre-market price updated for {symbol}')

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
        for stock in stocks:
            last_pull_date = PriceHistory.select(fn.MAX(PriceHistory.date)).where(PriceHistory.symbol == stock.symbol).scalar()
            period = determine_yf_period(last_pull_date)
            line = _StockUpdateLine(stock.symbol)
            if full:
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

            if full:
                line.start('meta')
                # Re-fetch the full stock row so all fields (e.g. flagged) are loaded
                # before pull_stock_data calls self.save() — the outer query only
                # selects Stock.symbol for ordering efficiency.
                stock = Stock.get(Stock.symbol == stock.symbol)
                stock.pull_stock_data(yf.Ticker(stock.symbol))
                line.complete('meta')

            if premarket:
                if period != '1d':
                    self.pull_price_history(stock.symbol, period, line=line, price_key='price')
                self.pull_premarket_data(stock.symbol, line=line, premkt_key='premkt')
            else:
                opts_thread = None
                if with_options:
                    opts_thread = threading.Thread(target=self.pull_options, args=(stock.symbol, line))
                    opts_thread.start()
                self.pull_price_history(stock.symbol, period, line=line, price_key='price')
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

        # Pass 2: compute and apply market regime multiplier
        from market_regime import compute_and_apply_regime
        regime = compute_and_apply_regime(pull_date=date.today())
        if regime:
            comp = float(regime.regime_composite) if regime.regime_composite else 0
            mult = float(regime.regime_multiplier) if regime.regime_multiplier else 1
            print(Fore.CYAN + f"Regime applied: composite={comp:.1f}  multiplier={mult:.4f}")
        else:
            print(Fore.YELLOW + "Regime skipped (coverage threshold not met)")

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
        client.update_stocks(full=args == 'full')
        Trend.update_trend_data(full=args == 'full')
        from historic_peaks import update_historic_peaks
        update_historic_peaks()
    elif command == 'update-remainder':
        client.update_stocks(only_remainder=True)
        Trend.update_trend_data(full=args == 'full')
    elif command in ['score', 'scores']:
        stocks = Stock.select().where(Stock.forward_pe.is_null(False))
        if args and not date_in_string(args):
            stocks = stocks.where(Stock.symbol.in_([symbol.strip() for symbol in args.upper().split(',')]))
        for stock in stocks:
            print(stock.symbol)
            cutoff_date = string_to_date(args).date() if date_in_string(args) else None
            if args == 'full':
                cutoff_date = date(1900,1,1)
            stock.calculate_scores(cutoff_date=cutoff_date, weekly=True)
            stock.calculate_scores(cutoff_date=cutoff_date)
        client.print_scores(stocks)
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
        from recalculate_scores import ALL_MODES, DEFAULT_LOOKBACK, run as recalculate_run
        from assess_scores import parse_lookback_arg

        # Progressive pass windows (cumulative days, newest-first). Each pass only
        # persists its disjoint band; prior bands are skipped when force=False.
        _DEFAULT_PASS_DAYS = [1, 7, 30, 365, 730, 1095, 1825, 2555]  # 1d→7y
        _FULL_PASS_DAYS    = [3650, 9125]  # +10y, +25y beyond default
        _PASS_LABELS = {
            1: '1d', 7: '7d', 30: '30d', 365: '1y', 730: '2y', 1095: '3y',
            1825: '5y', 2555: '7y', 3650: '10y', 9125: '25y',
        }

        symbol, components = None, set()
        days_explicit = None        # set only when user passes a specific --days N
        workers   = 1
        use_batch = True
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
                elif stripped == 'no-batch':
                    use_batch = False
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
            # Explicit window: force=True by default so all scores in the window
            # are recalculated regardless of whether they already exist for this version.
            # The user specified a window deliberately — skip logic would silently do nothing.
            recalculate_run(symbol, days_explicit, components,
                            workers=workers, use_batch=use_batch, force=True)
        else:
            # Progressive slice-based passes: each pass covers only its new date range.
            # Recent data is surfaced first; later passes extend further back without
            # redoing already-computed current-version scores.
            all_pass_days = _DEFAULT_PASS_DAYS + (_FULL_PASS_DAYS if do_full else [])
            slices = []          # (days_lookback, cutoff_end_date, label)
            prev_end = today
            for days in all_pass_days:
                label = _PASS_LABELS.get(days, f"{days}d")
                slices.append((days, prev_end, label))
                prev_end = today - timedelta(days=days)

            total_u, total_s, total_e = 0, 0, 0
            for idx, (pass_days, cutoff_end, label) in enumerate(slices, 1):
                print(Fore.CYAN + f"Pass {idx}/{len(slices)}: {label} window …")
                recalculate_run(symbol, pass_days, components,
                                workers=workers, use_batch=use_batch,
                                cutoff_end=cutoff_end, force=do_force)
            print(Fore.GREEN + "All passes complete.")
        # Rebuild historic peaks so the dashboard reflects the updated scores
        from historic_peaks import update_historic_peaks
        update_historic_peaks()
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
        symbol, days, do_compare, do_meta, extra_args = None, ASSESS_LOOKBACK, False, False, []
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
            assess_run(symbol, days, version=assess_version, force=do_force,
                       regime_adjust=do_regime_adjust)
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
