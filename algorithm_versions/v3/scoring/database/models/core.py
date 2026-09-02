from trader_database import BaseModel
from database.utils.scoring import (
    normalize_score, calculate_theta_factor, calculate_option_return,
    calculate_weekly_composite, calculate_weekly_adjustment
)
import math, json
from peewee import fn, CharField, IntegerField, FloatField, DateTimeField, DateField, CompositeKey, DeferredForeignKey, DecimalField, BooleanField, TextField, AutoField, ForeignKeyField
from datetime import datetime, date, timedelta
from colorama import Fore
import pandas as pd
import numpy as np
import talib

class EarningsDate(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='earnings_dates')
    date = DateField()
    call_time = CharField(null=True)
    eps_estimate = DecimalField(max_digits=12, decimal_places=4, null=True)
    reported_eps = DecimalField(max_digits=12, decimal_places=4, null=True)
    surprise_pct = DecimalField(max_digits=8, decimal_places=4, null=True)

    class Meta:
        table_name = 'earnings_dates'
        indexes = ((('symbol', 'date'), True),)

    @classmethod
    def build(cls, symbol, date, **kwargs):
        record, created = cls.get_or_create(symbol=symbol, date=date)
        for k, v in kwargs.items():
            setattr(record, k, v)
        record.save()
        return record, created

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        try:
            DB.execute_sql('ALTER TABLE earnings_dates ADD COLUMN call_time VARCHAR(255)')
        except:
            pass


class WeeklyScore(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='weekly_scores')
    date = DateField()  # Always Monday (week_start)
    trend = IntegerField(null=True)
    rsi = IntegerField(null=True)
    macd = IntegerField(null=True)
    composite = IntegerField(null=True)
    
    class Meta:
        table_name = 'weekly_scores'
        indexes = ((('symbol', 'date'), True),)
    
    @classmethod
    def build(cls, symbol, date):
        score, created = cls.get_or_create(symbol=symbol, date=date)
        return score, created
    
    def calculate_composite_score(self):
        if self.rsi is None or self.macd is None:
            return None
        return calculate_weekly_composite(self.rsi, self.macd, self.trend)

class Score(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='scores')
    date = DateField()
    version = DeferredForeignKey('AlgorithmVersion', backref='score_rows')
    bb = IntegerField()
    trend = IntegerField()
    volume = IntegerField()
    rsi = IntegerField()
    macd = IntegerField()
    stoch = IntegerField()
    ma20 = IntegerField()
    high_30 = DecimalField(max_digits=10, decimal_places=2)
    high_60 = DecimalField(max_digits=10, decimal_places=2)
    high_90 = DecimalField(max_digits=10, decimal_places=2)
    high_30_days = IntegerField()
    high_60_days = IntegerField()
    high_90_days = IntegerField()
    high_30_biggest_drop = DecimalField(max_digits=10, decimal_places=2)
    high_60_biggest_drop = DecimalField(max_digits=10, decimal_places=2)
    high_90_biggest_drop = DecimalField(max_digits=10, decimal_places=2)
    technical_alignment = IntegerField()
    overall = IntegerField()
    updated_at = DateTimeField(default=datetime.now)
    daily_change = DecimalField(max_digits=10, decimal_places=2)
    price = DecimalField(max_digits=10, decimal_places=2)
    price_target_growth = DecimalField(max_digits=10, decimal_places=2, null=True)
    next_earnings = IntegerField(null=True)
    price_target = DecimalField(max_digits=10, decimal_places=2, null=True)
    growth_score = DecimalField(max_digits=10, decimal_places=2, null=True)
    volume_signal = CharField(null=True)
    volume_magnitude = FloatField(null=True)
    weight_info = TextField(null=True)

    @classmethod
    def build(cls, symbol, date):
        version = AlgorithmVersion.get_or_create_current()
        score, created = cls.get_or_create(symbol=symbol, date=date, version=version)
        return score, created

    @classmethod
    def latest(cls, symbol, target_date):
        version = AlgorithmVersion.get_or_create_current()
        return cls.get_or_none(symbol=symbol, date=target_date, version=version)

    def calculate_next_earnings_days(self):
        if not self.symbol.next_earnings: return None
        days = (self.symbol.next_earnings - datetime.now().date()).days
        if days < 0: return 100 + days
        return days

    def calculate_price_target(self):
        return self.symbol.target_mean_price

    def calculate_price_target_growth(self):
        price = self.price
        if not price or price == 0: return None
        target = self.calculate_price_target()
        if not target: return None
        return ((target - price) / price) * 100

    def calculate_growth_score(self):
        """Calculate combined growth score: (eps_growth * 0.6) + (pe_growth_rate * 0.4)"""
        stock = self.symbol
        if not stock.eps or stock.eps == 0 or not stock.forward_eps: return None
        if not stock.pe or stock.pe == 0 or not stock.forward_pe: return None
        growth = ((stock.forward_eps - stock.eps) / stock.eps) * 100
        growth_rate = ((stock.pe - stock.forward_pe) / stock.pe) * 100
        return round((growth * 0.6) + (growth_rate * 0.4), 2)

    @staticmethod
    def next_earnings_color(value):
        if value < 7: return Fore.RED
        if value < 14: return Fore.YELLOW
        if value > 45: return Fore.WHITE
        return Fore.GREEN

    @staticmethod
    def price_target_color(value):
        if not value: return Fore.WHITE
        if value >= 50: return Fore.GREEN
        if value >= 20: return Fore.WHITE
        if value >= 5: return Fore.YELLOW
        return Fore.RED

    @classmethod
    def score_line(self, value, max_length=3):
        white_space = ' ' * (max_length - len(f"{value}"))
        color = Fore.RED
        if value >= 75: color = Fore.GREEN
        elif value > 50: color = Fore.LIGHTGREEN_EX
        elif value == 50: color = Fore.WHITE
        elif value > 25: color = Fore.LIGHTRED_EX
        return color + f"{white_space}{value}"

    @property
    def weekly_score(self):
        week_start = self.date - timedelta(days=self.date.weekday() + 7)
        return WeeklyScore.get_or_none(
            (WeeklyScore.symbol == self.symbol) & 
            (WeeklyScore.date == week_start)
        )

    @property
    def prev_weekly_score(self):
        week_start = self.date - timedelta(days=self.date.weekday() + 14)
        return WeeklyScore.get_or_none(
            (WeeklyScore.symbol == self.symbol) &
            (WeeklyScore.date == week_start)
        )

    def extra_details(self):
        output_str, price_target_string, next_earnings_string = '', '', ''
        price_target_color, next_earnings_color = Fore.WHITE, Fore.WHITE
        percentage_gain = self.price_target_growth
        if percentage_gain:
            plus_or_minus = '+' if percentage_gain > 0 else ''
            price_target_color = self.price_target_color(percentage_gain)
            price_target_string = f"{plus_or_minus}{round(percentage_gain)}%" if percentage_gain else ''
        whitespace = ' ' * (5 - len(f"{price_target_string}"))
        output_str = price_target_color + f"{whitespace}{price_target_string} "
        next_earnings = self.next_earnings
        if next_earnings is not None:
            next_earnings_color = self.next_earnings_color(next_earnings)
            if next_earnings == 0:
                next_earnings_string = 'Today'
            elif next_earnings == 1:
                next_earnings_string = 'Tomorrow'
            else:
                next_earnings_string = f'{next_earnings} days'
        white_space = ' ' * (8 - len(f"{next_earnings_string}"))
        output_str += next_earnings_color + f"{white_space}{next_earnings_string} "

        output_str += Fore.BLUE + f" {self.updated_at.strftime('%#m/%#d %#I:%M')}"
        return output_str

    def output_hash(self):
        from datetime import timedelta
        def get_week_start(date_obj):
            return date_obj - timedelta(days=date_obj.weekday())
        weekly_score = WeeklyScore.get_or_none(WeeklyScore.symbol == self.symbol, WeeklyScore.date == get_week_start(self.date))
        return {
            'OVR': self.overall,
            'W': weekly_score.composite if weekly_score else None,
            'TREND': self.trend,
            'BB': self.bb,
            'MACD': self.macd,
            'RSI': self.rsi,
            'X': self.technical_alignment,
            '+': self.extra_details(),
        }
    
    def calculate_overall_score(self):
        if None in (self.trend, self.bb, self.rsi, self.stoch, self.macd, self.technical_alignment):
            return None

        from database.models.technical import Indicator

        bb_pct = None
        ind = Indicator.get_or_none(
            (Indicator.symbol == self.symbol) & (Indicator.date == self.date))
        if ind and None not in (ind.upper_band, ind.lower_band):
            bw = float(ind.upper_band) - float(ind.lower_band)
            if bw > 0:
                ph = ind.price_history()
                if ph:
                    bb_pct = (float(ph.close) - float(ind.lower_band)) / bw

        # Context-aware dynamic weighting: strong trends → trend dominates,
        # sideways → oscillators dominate. Two guards re-enable oscillators:
        # 1) BB overextension in the trend direction
        # 2) Oscillators strongly contradicting the trend (reversal warning)
        trend_bias = float(np.tanh((self.trend - 50) * 0.06))
        trend_strength = abs(trend_bias)
        trend_dominance = trend_strength ** 0.7

        if bb_pct is not None:
            # Bullish extension: price above upper band in uptrend, scaled by trend_bias
            bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
            bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
            extension = bull_ext + bear_ext
            trend_dominance *= (1.0 - 0.5 * extension)

        osc_avg = (self.rsi + self.macd) / 2.0
        # Divergence: oscillators contradicting the trend direction, scaled continuously
        bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
        bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
        osc_divergence = bull_div + bear_div
        if bb_pct is not None:
            bb_amplifier = abs(bb_pct - 0.5)
            osc_divergence = min(1.0, osc_divergence * (1.0 + bb_amplifier))
        trend_dominance *= (1.0 - 0.7 * osc_divergence)

        #              sideways(d=0)  trending(d=1)
        # trend:       18             35            (+17)
        # bb:          18             18            (  0)
        # rsi:         25             12            (-13)
        # macd:        25             15            (-10)
        # stoch:        5              5            (  0)
        # ta:           9             15            (+ 6)
        d = trend_dominance
        w_trend = 18 + 17 * d
        w_bb    = 18
        w_rsi   = 25 - 13 * d
        w_macd  = 25 - 10 * d
        w_stoch =  5
        w_ta    =  9 +  6 * d

        weighted_sum = 50 + (
            ((self.trend - 50) * w_trend) +
            ((self.bb - 50) * w_bb) +
            ((self.rsi - 50) * w_rsi) +
            ((self.stoch - 50) * w_stoch) +
            ((self.macd - 50) * w_macd) +
            ((self.technical_alignment - 50) * w_ta)
        ) / 100

        weekly_detail = None
        ws = self.weekly_score
        if ws and ws.rsi and ws.macd:
            pws = self.prev_weekly_score
            adj, weekly_detail = calculate_weekly_adjustment(
                ws.trend, ws.rsi, ws.macd,
                pws.trend if pws else None,
                pws.rsi if pws else None,
                pws.macd if pws else None,
            )
            weighted_sum += adj

        try:
            from volume_amplifier import get_volume_multiplier
            vol_mult, raw_vol, sig_type, adj_mag, blend_w, vol_target = get_volume_multiplier(
                symbol=self.symbol,
                today=self.date,
                pulled_at=datetime.now(),
                pre_volume_overall=int(weighted_sum)
            )
            vol_boost = 1.0 + 0.6 * trend_strength
            if blend_w > 0:
                blend_w = min(blend_w * vol_boost, 0.60)
                weighted_sum = weighted_sum * (1 - blend_w) + vol_target * blend_w
            else:
                deviation = (vol_mult - 1.0) * vol_boost
                weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)
            if sig_type != 'NEUTRAL' or not self.volume_signal or self.volume_signal == 'NEUTRAL':
                self.volume = raw_vol
                self.volume_signal = sig_type
                self.volume_magnitude = adj_mag
        except Exception:
            pass

        info = {
            'trend': round(w_trend, 1), 'bb': round(w_bb, 1),
            'rsi': round(w_rsi, 1), 'macd': round(w_macd, 1),
            'stoch': round(w_stoch, 1), 'ta': round(w_ta, 1),
            'td': round(trend_dominance, 3)
        }
        if weekly_detail:
            info.update(weekly_detail)
        self.weight_info = json.dumps(info)

        return int(max(0, min(100, weighted_sum)))
    
    def calculate_technical_alignment_score(self):
        scores = [self.bb, self.rsi, self.macd]
        
        if None in scores:
            return None
            
        total = len(scores)

        bullish  = sum(s >= 50 for s in scores)
        bearish  = total - bullish
        agreement = max(bullish, bearish) / total              # 0.33 → 1.0

        avg_distance   = sum(s - 50 for s in scores) / total   # −50 → +50
        signal_strength = min(abs(avg_distance) / 50, 1.0)     #   0 → 1

        # turbo gives  +0 → +50  % lift when agreement & strength both max out ---
        turbo = 1 + 0.5 * signal_strength * agreement          # 1.00 → 1.50

        score = 50 + avg_distance * signal_strength * (agreement ** 1.5) * turbo
        return round(max(0, min(100, score)))

    def calculate_return_scores(self, prices, volatilities):
        strike_price = prices[0]
        
        def calculate_option_value(current_price, volatility, days_remaining, days):
            intrinsic_value = max(0, current_price - strike_price)
            time_value = (
                current_price * volatility * 0.4 * 
                math.sqrt(days_remaining / 252) * 
                calculate_theta_factor(days_remaining, days)
            )
            return round(max(intrinsic_value, intrinsic_value + time_value), 2)
        
        for days in [30, 60, 90]:
            if days > len(prices): break
            initial_cost = round(strike_price * volatilities[0] * 0.4 * math.sqrt(days / 252), 4)
            options_returns = []
            previous_value = initial_cost
            running_high = -100.00
            high_days, biggest_drop = 0, 0.0
            
            for i in range(1, days):
                current_price = prices[i]
                days_remaining = days - i
                current_value = calculate_option_value(current_price, volatilities[i], days_remaining, days)
                if current_value == 0:
                    break
                daily_return = round(((current_value - previous_value) / previous_value) * 100, 4)
                
                if not options_returns:
                    current_return = daily_return
                else:
                    current_return = round(((1 + options_returns[-1]/100) * (1 + daily_return/100) - 1) * 100, 4)
                
                options_returns.append(current_return)
                if current_return > running_high:
                    running_high = current_return
                    high_days = i
                if (current_value - previous_value) < biggest_drop:
                    biggest_drop = current_value - previous_value
                previous_value = current_value
            setattr(self, f'high_{days}', round(running_high, 2))
            setattr(self, f'high_{days}_days', high_days)
            setattr(self, f'high_{days}_biggest_drop', round(biggest_drop, 2))
        try:
            self.save()
        except Exception as e:
            print(e)

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        try:
            DB.execute_sql('ALTER TABLE scores DROP COLUMN rsi_2')
        except Exception:
            pass
        try:
            DB.execute_sql(
                "INSERT IGNORE INTO algorithm_versions (id, git_commit, git_message) "
                "VALUES (1, 'baseline', 'Pre-versioning baseline')"
            )
            DB.execute_sql(
                "ALTER TABLE scores "
                "ADD COLUMN version_id INT NOT NULL DEFAULT 1, "
                "DROP PRIMARY KEY, "
                "ADD PRIMARY KEY (symbol, date, version_id), "
                "ADD CONSTRAINT fk_scores_version FOREIGN KEY (version_id) "
                "REFERENCES algorithm_versions(id) ON DELETE CASCADE"
            )
        except Exception:
            pass

    class Meta:
        table_name = 'scores'
        primary_key = CompositeKey('symbol', 'date', 'version')
        indexes = (
            (('date',), False),
            (('date', 'overall'), False),
            (('overall',), False),
            (('symbol', 'date', 'overall'), False),
        )


class AlgorithmVersion(BaseModel):
    id = AutoField()
    git_commit = CharField(unique=True)
    git_message = CharField(null=True)
    notes = TextField(null=True)
    created_at = DateTimeField(default=datetime.now)
    _current = None

    @classmethod
    def get_or_create_current(cls):
        if cls._current is None:
            import subprocess
            try:
                commit = subprocess.check_output(
                    ['git', 'rev-parse', '--short', 'HEAD'], stderr=subprocess.DEVNULL
                ).decode().strip()
            except Exception:
                commit = 'unknown'
            cls._current, created = cls.get_or_create(git_commit=commit)
            if created:
                try:
                    msg = subprocess.check_output(
                        ['git', 'log', '-1', '--format=%s', commit], stderr=subprocess.DEVNULL
                    ).decode().strip()
                    cls._current.git_message = msg
                    cls._current.save()
                except Exception:
                    pass
        return cls._current

    class Meta:
        table_name = 'algorithm_versions'


class ScoreAssessmentRun(BaseModel):
    id = AutoField()
    run_at = DateTimeField(default=datetime.now)
    symbol = CharField(null=True)
    lookback_days = IntegerField()
    total_peaks = IntegerField(default=0)
    correlation_1d = FloatField(null=True)
    correlation_7d = FloatField(null=True)
    correlation_15d = FloatField(null=True)
    correlation_30d = FloatField(null=True)
    correlation_60d = FloatField(null=True)
    correlation_90d = FloatField(null=True)
    notes = TextField(null=True)
    git_commit = CharField(null=True)
    version = ForeignKeyField(AlgorithmVersion, backref='runs', null=True, on_delete='SET NULL')

    class Meta:
        table_name = 'score_assessment_runs'


class ScoreAssessmentResult(BaseModel):
    id = AutoField()
    run = ForeignKeyField(ScoreAssessmentRun, backref='results', on_delete='CASCADE')
    bucket = CharField()
    sample_count = IntegerField(default=0)
    avg_score = FloatField(null=True)
    avg_return_1d = FloatField(null=True)
    avg_return_7d = FloatField(null=True)
    avg_return_15d = FloatField(null=True)
    avg_return_30d = FloatField(null=True)
    avg_return_60d = FloatField(null=True)
    avg_return_90d = FloatField(null=True)
    win_rate_1d = FloatField(null=True)
    win_rate_7d = FloatField(null=True)
    win_rate_15d = FloatField(null=True)
    win_rate_30d = FloatField(null=True)
    win_rate_60d = FloatField(null=True)
    win_rate_90d = FloatField(null=True)
    median_return_1d = FloatField(null=True)
    median_return_7d = FloatField(null=True)
    median_return_15d = FloatField(null=True)
    median_return_30d = FloatField(null=True)
    median_return_60d = FloatField(null=True)
    median_return_90d = FloatField(null=True)
    avg_peak_1d = FloatField(null=True)
    avg_peak_7d = FloatField(null=True)
    avg_peak_15d = FloatField(null=True)
    avg_peak_30d = FloatField(null=True)
    avg_peak_60d = FloatField(null=True)
    avg_peak_90d = FloatField(null=True)
    median_peak_1d = FloatField(null=True)
    median_peak_7d = FloatField(null=True)
    median_peak_15d = FloatField(null=True)
    median_peak_30d = FloatField(null=True)
    median_peak_60d = FloatField(null=True)
    median_peak_90d = FloatField(null=True)

    class Meta:
        table_name = 'score_assessment_results'


class Stock(BaseModel):
    symbol = CharField(primary_key=True) 
    name = CharField()
    created_at = DateTimeField(default=datetime.now)
    industry = CharField(null=True)
    sector = CharField(null=True)
    next_earnings = DateTimeField(null=True)
    earnings_call_time = CharField(null=True)  # BMO, AMC, TAS
    eps = DecimalField(max_digits=12, decimal_places=4, null=True)
    forward_eps = DecimalField(max_digits=12, decimal_places=4, null=True) 
    eps_growth = DecimalField(max_digits=8, decimal_places=4, null=True)
    pe = DecimalField(max_digits=12, decimal_places=4, null=True)
    forward_pe = DecimalField(max_digits=12, decimal_places=4, null=True)
    peg = DecimalField(max_digits=8, decimal_places=4, null=True)
    price_to_book = DecimalField(max_digits=12, decimal_places=4, null=True)
    price_to_sales = DecimalField(max_digits=12, decimal_places=4, null=True)
    profit_margin = DecimalField(max_digits=8, decimal_places=4, null=True)
    operating_margin = DecimalField(max_digits=8, decimal_places=4, null=True)
    roe = DecimalField(max_digits=8, decimal_places=4, null=True)
    roa = DecimalField(max_digits=8, decimal_places=4, null=True)
    revenue = DecimalField(max_digits=16, decimal_places=2, null=True)
    revenue_per_share = DecimalField(max_digits=12, decimal_places=4, null=True)
    revenue_growth = DecimalField(max_digits=8, decimal_places=4, null=True)
    gross_profits = DecimalField(max_digits=16, decimal_places=2, null=True)
    debt_to_equity = DecimalField(max_digits=12, decimal_places=4, null=True)
    operating_cash_flow = DecimalField(max_digits=16, decimal_places=2, null=True)
    capital_expenditures = DecimalField(max_digits=16, decimal_places=2, null=True)
    free_cash_flow = DecimalField(max_digits=16, decimal_places=2, null=True)
    dividend_per_share = DecimalField(max_digits=10, decimal_places=4, null=True)
    dividend_yield = DecimalField(max_digits=8, decimal_places=4, null=True)
    dividend_payout_ratio = DecimalField(max_digits=8, decimal_places=4, null=True)
    market_cap = DecimalField(max_digits=20, decimal_places=2, null=True)
    enterprise_value = DecimalField(max_digits=20, decimal_places=2, null=True)
    working_capital = DecimalField(max_digits=16, decimal_places=2, null=True)
    effective_tax_rate = DecimalField(max_digits=8, decimal_places=4, null=True)
    total_debt = DecimalField(max_digits=16, decimal_places=2, null=True)
    beta = DecimalField(max_digits=8, decimal_places=4, null=True)
    target_low_price = DecimalField(max_digits=12, decimal_places=2, null=True)
    target_high_price = DecimalField(max_digits=12, decimal_places=2, null=True)
    target_mean_price = DecimalField(max_digits=12, decimal_places=2, null=True)
    target_median_price = DecimalField(max_digits=12, decimal_places=2, null=True)
    number_of_analysts = IntegerField(null=True)
    flagged = BooleanField(default=False)

    @classmethod
    def build(cls, symbol, name=None):
        stock, created = cls.get_or_create(symbol = symbol)
        if name: 
            stock.name = name
            stock.save()
        return stock

    def flag(self):
        self.flagged = True
        self.save()

    def pull_stock_data(self, ticker, full=True):
        if "Earnings Date" in ticker.calendar and ticker.calendar["Earnings Date"]: 
            self.next_earnings = ticker.calendar["Earnings Date"][0]
            earnings_dates = ticker.earnings_dates
            if earnings_dates is not None and not earnings_dates.empty:
                idx = earnings_dates.index
                now_cmp = (
                    pd.Timestamp.now(tz=idx.tz)
                    if idx.tz is not None
                    else pd.Timestamp.now()
                )
                for ts, row in earnings_dates.iterrows():
                    ed = ts.date() if hasattr(ts, 'date') else pd.Timestamp(ts).date()
                    ct = ts.strftime('%H:%M:%S') if hasattr(ts, 'strftime') else None
                    eps_est = row.get('EPS Estimate')
                    reported = row.get('Reported EPS')
                    surprise = row.get('Surprise(%)')
                    EarningsDate.build(
                        symbol=self.symbol, date=ed, call_time=ct,
                        eps_estimate=float(eps_est) if pd.notna(eps_est) else None,
                        reported_eps=float(reported) if pd.notna(reported) else None,
                        surprise_pct=float(surprise) if pd.notna(surprise) else None,
                    )
                future_earnings = earnings_dates[earnings_dates.index > now_cmp]
                if not future_earnings.empty:
                    next_earnings_date = future_earnings.index[0]
                    self.next_earnings = next_earnings_date
                    self.earnings_call_time = future_earnings.index[0].strftime('%H:%M:%S')
                else:
                    past_earnings = earnings_dates[earnings_dates.index <= now_cmp]
                    if not past_earnings.empty:
                        self.earnings_call_time = past_earnings.index[0].strftime('%H:%M:%S')
        if not self.earnings_call_time:
            self.earnings_call_time = ticker.info.get('earningsCallTime')
        self.name = ticker.info.get('shortName')
        self.industry = ticker.info.get('industryKey')
        self.sector = ticker.info.get('sectorKey')
        self.pe = ticker.info.get('trailingPE')
        self.forward_pe = ticker.info.get('forwardPE')
        self.peg = ticker.info.get('pegRatio')
        self.price_to_book = ticker.info.get('priceToBook')
        self.price_to_sales = ticker.info.get('priceToSalesTrailing12Months')
        self.profit_margin = ticker.info.get('profitMargins')
        self.operating_margin = ticker.info.get('operatingMargins')
        self.roe = ticker.info.get('returnOnEquity')
        self.roa = ticker.info.get('returnOnAssets')
        self.revenue = ticker.info.get('totalRevenue')
        self.revenue_per_share = ticker.info.get('revenuePerShare')
        self.revenue_growth = ticker.info.get('revenueGrowth')
        self.debt_to_equity = ticker.info.get('debtToEquity')
        self.eps = ticker.info.get('trailingEps')
        self.forward_eps = ticker.info.get('forwardEps')
        self.eps_growth = ticker.info.get('earningsQuarterlyGrowth')
        self.market_cap = ticker.info.get('marketCap')
        self.enterprise_value = ticker.info.get('enterpriseValue')
        self.beta = ticker.info.get('beta')
        self.dividend_per_share = ticker.info.get('dividendRate')
        self.dividend_yield = ticker.info.get('dividendYield')
        if self.revenue and ticker.info.get('grossMargins'):
            self.gross_profits = self.revenue * ticker.info.get('grossMargins')
        if self.dividend_per_share and self.eps:
            self.dividend_payout_ratio = self.dividend_per_share / self.eps
        self.target_high_price = ticker.info.get('targetHighPrice')
        self.target_low_price = ticker.info.get('targetLowPrice')
        self.target_mean_price = ticker.info.get('targetMeanPrice')
        self.target_median_price = ticker.info.get('targetMedianPrice')
        self.number_of_analysts = ticker.info.get('numberOfAnalystOpinions')

        financial_data = ticker.financials
        if not financial_data.empty:
            latest_financials = financial_data.iloc[:, 0]  # Most recent quarter/year
            self.revenue = self.revenue or latest_financials.get('Total Revenue') or 0.0
            self.gross_profits = self.gross_profits or latest_financials.get('Gross Profit') or 0.0
            self.working_capital = (
                latest_financials.get('Total Current Assets', 0) -
                latest_financials.get('Total Current Liabilities', 0)
            )
        
        cash_flow_data = ticker.cashflow
        if not cash_flow_data.empty:
            latest_cash_flow = cash_flow_data.iloc[:, 0]  # Most recent quarter/year
            self.operating_cash_flow = latest_cash_flow.get('Operating Cash Flow')
            self.capital_expenditures = latest_cash_flow.get('Capital Expenditure')
            self.free_cash_flow = (
                self.operating_cash_flow + abs(self.capital_expenditures)
                if self.operating_cash_flow and self.capital_expenditures
                else None
            )
        
        balance_sheet = ticker.balance_sheet
        if not balance_sheet.empty:
            latest_balance = balance_sheet.iloc[:, 0]  # Most recent quarter/year
            self.total_debt = (
                latest_balance.get('Long Term Debt', 0) +
                latest_balance.get('Short Term Debt', 0)
            )
            total_equity = latest_balance.get('Total Stockholder Equity')
            if total_equity and total_equity != 0:
                self.debt_to_equity = self.total_debt / total_equity
        
        # Tax rate calculation
        income_stmt = ticker.income_stmt
        if not income_stmt.empty:
            latest_income = income_stmt.iloc[:, 0]  # Most recent quarter/year
            pretax_income = latest_income.get('Pretax Income')
            income_tax = latest_income.get('Income Tax Expense')
            if pretax_income and income_tax and pretax_income != 0:
                self.effective_tax_rate = income_tax / pretax_income

        for field in self._meta.fields.values():
            value = getattr(self, field.name)
            if pd.isna(value):
                setattr(self, field.name, value)
            elif isinstance(value, (int, float)) and math.isinf(value):
                setattr(self, field.name, None)
        try:
            self.save()
        except Exception as e:
            print(e)

    def calculate_indicators_and_scores(self, full=False):
        self.calculate_indicators(full)
        self.calculate_indicators(full, weekly=True)
        self.calculate_scores(full, weekly=True)
        self.calculate_scores(full)

    def calculate_indicators(self, full=False, weekly=False):
        from database.models.technical import PriceHistory, Indicator, WeeklyPriceHistory, WeeklyIndicator
        PriceModel = WeeklyPriceHistory if weekly else PriceHistory
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        prices = PriceModel.select().where(PriceModel.symbol == self.symbol).order_by(PriceModel.date.asc())
        close_prices, highs, lows,volumes, dates = [], [], [], [], []
        for price in prices:
            close_prices.append(float(price.close))
            highs.append(float(price.high))
            lows.append(float(price.low))
            volumes.append(float(price.volume))
            dates.append(price.date)

        if len(close_prices) < 14:
            return  # Skip if not enough data

        close_arr = np.array(close_prices)
        high_arr = np.array(highs)
        low_arr = np.array(lows)
        rsi_values = talib.RSI(np.array(close_prices), timeperiod=14)
        fastk, fastd = talib.STOCHRSI(np.array(close_prices), timeperiod=14, fastk_period=3, fastd_period=3, fastd_matype=0)
        slowk, slowd = talib.STOCH(high_arr, low_arr, close_arr, fastk_period=14, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
        macd, macd_signal, macd_hist = talib.MACD(np.array(close_prices), fastperiod=12, slowperiod=26, signalperiod=9)
        upperband, middleband, lowerband = talib.BBANDS(np.array(close_prices), timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
        obv_values = talib.OBV(np.array(close_prices), np.array(volumes))
        rsi_ma = talib.SMA(rsi_values, timeperiod=9)
        rsi_ema = talib.EMA(rsi_values, timeperiod=9)
        ma_9 = talib.SMA(np.array(close_prices), timeperiod=9)
        ema_9 = talib.EMA(np.array(close_prices), timeperiod=9)
        ma_21 = talib.SMA(np.array(close_prices), timeperiod=21)
        ema_21 = talib.EMA(np.array(close_prices), timeperiod=21)
        ma_50 = talib.SMA(np.array(close_prices), timeperiod=50)
        ema_50 = talib.EMA(np.array(close_prices), timeperiod=50)
        ma_200 = talib.SMA(np.array(close_prices), timeperiod=200)
        ema_200 = talib.EMA(np.array(close_prices), timeperiod=200)
        
        for i in range(len(close_prices)-1, -1, -1):
            indicator, created = IndicatorModel.build(
                symbol=self.symbol,
                date=dates[i],
                rsi=rsi_values[i] if i >= 13 else None,  # First 14 values will be None for RSI
                rsi_ma=rsi_ma[i] if i >= 21 else None,  # First 22 values will be None for RSI MA
                rsi_ema=rsi_ema[i] if i >= 21 else None,  # First 22 values will be None for RSI EMA
                stoch_rsi=fastk[i] if i >= 13 else None,  # First 14 values will be None for Stoch RSI
                stoch_rsi_signal=fastd[i] if i >= 13 else None,
                stoch=slowk[i] if i >= 13 else None,
                stoch_signal=slowd[i] if i >= 13 else None,
                macd=macd[i] if i >= 25 else None,  # First 26 values will be None for MACD
                macd_signal=macd_signal[i] if i >= 25 else None,
                macd_hist=macd_hist[i] if i >= 25 else None,
                upper_band=upperband[i] if i >= 19 else None,  # First 20 values will be None for Bollinger Bands
                middle_band=middleband[i] if i >= 19 else None,
                lower_band=lowerband[i] if i >= 19 else None,
                obv=int(obv_values[i]) if i >= 1 else None,  # First value of OBV is None
                ma_9 = ma_9[i] if i >= 8 else None,
                ema_9 = ema_9[i] if i >= 8 else None,
                ma_21 = ma_21[i] if i >= 20 else None,
                ema_21 = ema_21[i] if i >= 20 else None,
                ma_50 = ma_50[i] if i >= 49 else None,
                ema_50 = ema_50[i] if i >= 49 else None,
                ma_200 = ma_200[i] if i >= 199 else None,
                ema_200 = ema_200[i] if i >= 199 else None,
                peak = max(close_prices[0:i+1]),
                peak_20 = max(close_prices[max(0, i-19):i+1]),
                peak_60 = max(close_prices[max(0, i-59):i+1]),
                peak_120 = max(close_prices[max(0, i-119):i+1]),
                peak_180 = max(close_prices[max(0, i-179):i+1]),
            )
            if not full and not created:
                break
        timeframe = 'weekly' if weekly else 'daily'
        print(Fore.GREEN + f'{timeframe.capitalize()} indicators calculated for {self.symbol}')

    def calculate_scores(self, cutoff_date=None, weekly=False):
        from database.models.technical import Indicator, WeeklyIndicator
        
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        ScoreModel = WeeklyScore if weekly else Score
        
        for indicator in IndicatorModel.select(IndicatorModel.date).where(IndicatorModel.symbol == self.symbol).order_by(IndicatorModel.date.desc()):
            score, created = ScoreModel.build(self.symbol, indicator.date)
            
            trend = self.calculate_trend_score(indicator.date, weekly=weekly)
            
            score.trend = trend
            score.rsi = self.calculate_rsi_score(indicator.date, trend_score=trend, weekly=weekly)
            score.macd = self.calculate_macd_score(indicator.date, weekly=weekly)
            
            if weekly:
                if None in (score.trend, score.rsi, score.macd):
                    continue
                score.composite = score.calculate_composite_score()
                score.save()
            else:
                score.bb = self.calculate_bollinger_bands_score(indicator.date, trend_score=trend)
                score.stoch = self.calculate_stoch_score(indicator.date)
                score.daily_change = self.calculate_daily_change(indicator.date)
                score.price = self.current_price()
                score.name = self.name
                score.next_earnings = score.calculate_next_earnings_days()
                score.price_target = score.calculate_price_target()
                score.price_target_growth = score.calculate_price_target_growth()
                if None in (score.bb, score.trend, score.rsi, score.macd, score.stoch):
                    score.delete_instance()
                    return
                score.technical_alignment = score.calculate_technical_alignment_score()
                score.overall = score.calculate_overall_score()
                score.updated_at = datetime.now()
                score.save()
            
            if not created:
                if not cutoff_date or indicator.date < cutoff_date: break
        
        timeframe = 'Weekly' if weekly else 'Daily'
        print(Fore.GREEN + f'{timeframe} scores calculated for {self.symbol}')

    def recalculate_weekly_scores(self, cutoff):
        scores = list(
            WeeklyScore.select()
            .where((WeeklyScore.symbol == self.symbol) & (WeeklyScore.date >= cutoff))
            .order_by(WeeklyScore.date.asc())
        )
        from tqdm import tqdm
        updated, errors = 0, 0
        for ws in tqdm(scores, desc=f"{self.symbol} weekly", leave=False):
            try:
                trend = self.calculate_trend_score(ws.date, weekly=True)
                if trend is not None: ws.trend = trend
                rsi = self.calculate_rsi_score(ws.date, trend_score=ws.trend, weekly=True)
                if rsi is not None: ws.rsi = rsi
                macd = self.calculate_macd_score(ws.date, weekly=True)
                if macd is not None: ws.macd = macd
                if ws.rsi is not None and ws.macd is not None:
                    ws.composite = ws.calculate_composite_score()
                ws.save()
                updated += 1
            except Exception as e:
                errors += 1
                print(Fore.RED + f"  {self.symbol} weekly {ws.date}: {e}")
        return updated, errors

    def recalculate_scores(self, cutoff, components):
        from tqdm import tqdm
        version = AlgorithmVersion.get_or_create_current()
        scores = list(
            Score.select()
            .where((Score.symbol == self.symbol) & (Score.date >= cutoff) & (Score.version == version))
            .order_by(Score.date.asc())
        )
        label = '+'.join(sorted(components))
        updated, skipped, errors = 0, 0, 0
        for score in tqdm(scores, desc=f"{self.symbol} {label}", leave=False):
            try:
                changed = False
                if 'trend' in components:
                    val = self.calculate_trend_score(score.date)
                    if val is not None: score.trend = val; changed = True
                if 'rsi' in components:
                    val = self.calculate_rsi_score(score.date, trend_score=score.trend)
                    if val is not None: score.rsi = val; changed = True
                if 'bb' in components:
                    val = self.calculate_bollinger_bands_score(score.date, trend_score=score.trend)
                    if val is not None: score.bb = val; changed = True
                if 'macd' in components:
                    val = self.calculate_macd_score(score.date)
                    if val is not None: score.macd = val; changed = True
                if 'stoch' in components:
                    val = self.calculate_stoch_score(score.date)
                    if val is not None: score.stoch = val; changed = True
                if 'ta' in components:
                    val = score.calculate_technical_alignment_score()
                    if val is not None: score.technical_alignment = val; changed = True
                if 'overall' in components or changed:
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
                print(Fore.RED + f"  {self.symbol} {score.date}: {e}")
        return updated, skipped, errors

    def backfill_scores(self, cutoff=None):
        from database.models.technical import Indicator, PriceHistory
        from tqdm import tqdm
        version = AlgorithmVersion.get_or_create_current()

        ind_query = Indicator.select(Indicator.date).where(Indicator.symbol == self.symbol)
        if cutoff:
            ind_query = ind_query.where(Indicator.date >= cutoff)
        indicator_dates = set(i.date for i in ind_query)

        score_query = Score.select(Score.date).where(Score.symbol == self.symbol, Score.version == version)
        if cutoff:
            score_query = score_query.where(Score.date >= cutoff)
        scored_dates = set(s.date for s in score_query)

        missing = sorted(indicator_dates - scored_dates)
        if not missing:
            return 0, 0

        price_map = {p.date: float(p.close) for p in
            PriceHistory.select(PriceHistory.date, PriceHistory.close)
            .where(PriceHistory.symbol == self.symbol)}

        filled, errors = 0, 0
        for d in tqdm(missing, desc=f"{self.symbol} backfill", leave=False):
            try:
                score, _ = Score.build(self.symbol, d)
                trend = self.calculate_trend_score(d)
                score.trend = trend
                score.rsi = self.calculate_rsi_score(d, trend_score=trend)
                score.macd = self.calculate_macd_score(d)
                score.bb = self.calculate_bollinger_bands_score(d, trend_score=trend)
                score.stoch = self.calculate_stoch_score(d)
                score.daily_change = 0
                score.price = price_map.get(d, 0)
                score.name = self.name
                score.next_earnings = score.calculate_next_earnings_days()
                score.price_target = score.calculate_price_target()
                score.price_target_growth = score.calculate_price_target_growth()
                if None in (score.bb, score.trend, score.rsi, score.macd, score.stoch):
                    score.delete_instance()
                    continue
                score.technical_alignment = score.calculate_technical_alignment_score()
                score.overall = score.calculate_overall_score()
                score.updated_at = datetime.now()
                score.save()
                filled += 1
            except Exception as e:
                errors += 1
                print(Fore.RED + f"  {self.symbol} {d}: {e}")
        return filled, errors

    def pull_historicals(self, ticker):
        from database.models.technical import Historical
        financials = ticker.financials
        balance_sheet = ticker.balance_sheet
        cash_flow = ticker.cashflow

        # Calculate historical ratios for each period
        for period in financials.columns:
            historical = Historical.build(self.symbol, pd.to_datetime(period).date())
            financial_data = financials[period]
            balance_data = balance_sheet.get(period, None)
            cash_flow_data = cash_flow.get(period, None)
            
            if financial_data.get('Net Income', 0) > 0:
                historical.pe = self.market_cap / financial_data['Net Income']

            if financial_data.get('Total Revenue', 0) > 0 and not pd.isna(financial_data.get('Operating Income', 0)) and financial_data.get('Operating Income', 0) > 0:
                historical.margins = financial_data.get('Operating Income', 0) / financial_data['Total Revenue']

            if cash_flow_data is not None and not pd.isna(cash_flow_data.get('Operating Cash Flow', 0)):
                historical.fcf = cash_flow_data.get('Operating Cash Flow', 0) + abs(cash_flow_data.get('Capital Expenditure', 0))

            if balance_data is not None:
                if 'Stockholders Equity' in balance_data and 'Net Income' in financial_data:
                    equity = balance_data['Stockholders Equity']
                    if equity > 0:
                        historical.roe = financial_data['Net Income'] / equity

                total_debt = balance_data.get('Long Term Debt', 0) + balance_data.get('Short Term Debt', 0)
                equity = balance_data.get('Stockholders Equity', 0)
                if not pd.isna(equity) and not pd.isna(total_debt) and equity != 0:
                    historical.debt_equity = total_debt / equity
            
            # for field in historical._meta.fields.values():
            #     value = getattr(historical, field.name)
            #     if pd.isna(value):  # or use pd.isnull(value)
            #         setattr(historical, field.name, None)
            
            try:
                historical.save()
            except Exception as e:
                print(e)

    def current_price(self):
        from database.models.technical import PriceHistory
        return self.price_history.order_by(PriceHistory.date.desc()).limit(1).get().close

    def current_volume(self):
        from database.models.technical import PriceHistory
        return self.price_history.order_by(PriceHistory.date.desc()).limit(1).get().volume
    
    def indicator_by_date(self, target_date, weekly=False):
        from database.models.technical import Indicator, WeeklyIndicator
        model = WeeklyIndicator if weekly else Indicator
        return model.get_or_none(symbol=self.symbol, date=target_date)
    
    def price_history_by_date(self, target_date, weekly=False):
        from database.models.technical import PriceHistory, WeeklyPriceHistory
        model = WeeklyPriceHistory if weekly else PriceHistory
        return model.get_or_none(symbol=self.symbol, date=target_date)
    
    def score_by_date(self, target_date):
        return Score.get_or_none(symbol=self.symbol, date=target_date)

    def percentage_change(self, from_date=None, to_date=None):
        from database.models.technical import PriceHistory
        query = self.price_history.where(PriceHistory.date >= from_date) if from_date else self.price_history
        first_price = query.order_by(PriceHistory.date.asc()).first().close
        query = query.where(PriceHistory.date <= to_date) if to_date else query
        last_price = query.order_by(PriceHistory.date.desc()).first().close

        return ((last_price - first_price) / first_price) * 100

    def daily_percentage_change(self, target_date):
        from database.models.technical import PriceHistory
        prices = [float(price.close) for price in self.price_history.where(PriceHistory.date <= target_date).order_by(PriceHistory.date.desc()).limit(2)]
        if len(prices) == 1 or prices[0] == prices[1]:
            return 0.0
        return round(((prices[0] - prices[1]) / prices[1]) * 100, 2)

    def price_direction(self, target_date):
        from database.models.technical import PriceHistory
        prices = [float(price.close) for price in self.price_history.where(PriceHistory.date <= target_date).order_by(PriceHistory.date.desc()).limit(2)]
        if len(prices) == 1 or prices[0] == prices[1]:
            return 0
        return 1 if prices[0] > prices[1] else -1

    def get_prices_dates_volatilities_lists(self, lookback = 30):
        from database.models.technical import PriceHistory
        price_history = self.price_history.order_by(PriceHistory.date.asc())
        prices = [float(ph.close) for ph in price_history]
        dates = [ph.date for ph in price_history]
        returns = [(prices[i] / prices[i-1] - 1) for i in range(1, len(prices))]
        volatilities = [None]

        for i in range(1, len(returns) + 1):
            if i < lookback - 1:
                volatilities.append(None)  # or None, depending on your preference
                continue
            window_returns = returns[max(0, i - lookback + 1):i + 1]
            mean_return = sum(window_returns) / len(window_returns)
            squared_diff = sum((r - mean_return) ** 2 for r in window_returns)
            variance = squared_diff / (len(window_returns) - 1)
            daily_volatility = math.sqrt(variance)
            annual_volatility = daily_volatility * math.sqrt(252)
            
            volatilities.append(round(annual_volatility, 2))
        
        return prices, dates, volatilities

    def average_volume(self, from_date=None, to_date=None, last_n_days=20) -> float:
        from database.models.technical import PriceHistory
        query = self.price_history
        if from_date:
            query = query.where(PriceHistory.date >= from_date)
        if to_date:
            query = query.where(PriceHistory.date <= to_date)
        query = query.order_by(PriceHistory.date.desc()).limit(last_n_days)
        volumes = [ph.volume for ph in query]
        avg_volume = sum(volumes) / len(volumes) if query.count() > 0 else None
        return float(avg_volume) if avg_volume is not None else 0.0

    def industry_average(self, field):
        return Stock.select(fn.AVG(getattr(Stock, field))).where(Stock.sector == self.sector).scalar()

    def industry_max(self, field):
        return Stock.select(fn.MAX(getattr(Stock, field))).where(Stock.sector == self.sector).scalar()

    def industry_min(self, field):
        return Stock.select(fn.MIN(getattr(Stock, field))).where(Stock.sector == self.sector).scalar()

    def pe_score(self):
        if not self.pe or not self.industry_average('pe') or self.pe < 0:
            return 0
        pe_ratio = self.pe / self.industry_average('pe')
        return int(max(0, min(100, 100 - (pe_ratio - 0.5) * 50)))

    def calculate_scaling_factor(self, up_to_date=None, last_n_days=20, decay=0.94):
        from database.models.technical import PriceHistory
        price_history = np.array([float(ph.close) for ph in self.price_history.where(PriceHistory.date <= up_to_date).order_by(PriceHistory.date.desc()).limit(last_n_days)])
        daily_changes = np.log(price_history[1:] / price_history[:-1])
        weights = np.array([decay**i for i in range(len(daily_changes))])
        weights = weights / weights.sum()
        
        weighted_variance = np.sum(weights * (daily_changes - np.mean(daily_changes))**2)
        weighted_vol = np.sqrt(weighted_variance * 252)
        
        k = 1 / weighted_vol if weighted_vol != 0 else 1
        return np.clip(k, 0.1, 10)

    def moving_average_volume(self, window_size: int, target_date: date) -> float:
        from database.models.technical import PriceHistory
        query = self.price_history.where(PriceHistory.date <= target_date).order_by(PriceHistory.date.asc())
        volumes = list(query.select(PriceHistory.volume))
        
        if not volumes:
            return 0.0  # Handle no data available
        
        if len(volumes) < window_size:
            window = volumes
        else:
            window = volumes[-window_size:]
        
        return sum(v.volume for v in window) / len(window)
        
    # hash_map is a dictionary of strings mapping to a percentage
    @classmethod
    def print_stock_percentages(self, symbol, hash_map):
        white_space = ' ' * (8 - len(symbol))
        output = Fore.GREEN + symbol + white_space

        for key, value in hash_map.items():
            output += Fore.WHITE + f"{key}: "
            print_value = value
            if type(value) == float:
                print_value = f'{value:.2f}%'
                output += Fore.GREEN if value > 0 else Fore.RED
            elif type(value) == str:
                output += Fore.YELLOW
            white_space = ' ' * (8 - len(print_value))
            output += f"{print_value}{white_space}"
        print(output)

     # hash_map is a dictionary of strings mapping to a float value from 1 to 10
    @classmethod
    def print_stock_ratings(self, symbol, hash_map, held_position = False, flagged = False):
        color = Fore.MAGENTA if held_position else Fore.LIGHTGREEN_EX if flagged else Fore.WHITE
        # color = Fore.MAGENTA if held_position else Fore.WHITE
        output = color + f"{symbol}   "
        for key, value in hash_map.items():
            if key in ['+']: continue
            output += Fore.WHITE + f"{key}: {Score.score_line(value)}  "
        
        output += Fore.WHITE + '|' + hash_map['+']
        print(output)

    def ticker_line(self):
        symbol_white_space = ' ' * max(0, (6 - len(self.symbol)))
        percentage_change = self.daily_percentage_change(date.today())
        ticker_white_space = ' ' * max(0, (6 - len(f"{percentage_change:.2f}")))
        color = Fore.GREEN if percentage_change > 0 else Fore.RED
        plus_or_minus = '+' if percentage_change > 0 else ''
        ticker_white_space += '' if plus_or_minus else ' '
        return f"{self.symbol}{symbol_white_space}{ticker_white_space}{color}({plus_or_minus}{percentage_change:.2f}%){Fore.RESET}"

    def calculate_daily_change(self, target_date):
        from database.models.technical import PriceHistory
        current_price = self.current_price()
        prev_price = self.price_history.where(PriceHistory.date < target_date).order_by(PriceHistory.date.desc()).first()
        if not prev_price: return 0.0
        return ((current_price - prev_price.close) / prev_price.close) * 100
    
    def calculate_rsi_score_gpt(self, target_date, lookback=30):
        """
        RSI-centric buy score (0-100).
        Prioritizes fresh cross UP through 30 (bullish) and DOWN through 70 (bearish) with exponential decay.
        Falls back to a position-based gradient otherwise.
        Adds small 50-line bias and short-term momentum as multipliers.
        """
        from database.models.technical import Indicator

        indicators = list(
            self.indicators
                .where(Indicator.date <= target_date)
                .order_by(Indicator.date.desc())
                .limit(lookback + 5)
        )
        if len(indicators) < 5:
            return None

        # Build reverse-chronological RSI array (0 = today)
        rsi = [float(ind.rsi) if ind.rsi is not None else None for ind in indicators]
        cur = rsi[0]
        if cur is None:
            return None

        # ---------- helpers ----------
        def find_last_cross_and_streak():
            """
            Returns (cross_type, age, pre_streak):
            cross_type: 'up30', 'down70', or None
            age: bars since cross (0=today)
            pre_streak: how many consecutive days BEFORE the cross stayed extreme
            """
            for i in range(0, min(len(rsi) - 1, lookback)):
                today, yday = rsi[i], rsi[i+1]
                if today is None or yday is None:
                    continue
                # UP through 30
                if yday < 30 <= today:
                    # count how long we were <30 before yesterday
                    streak = 0
                    j = i + 1
                    while j + 1 < len(rsi) and rsi[j] is not None and rsi[j] < 30:
                        streak += 1
                        j += 1
                    return 'up30', i, streak
                # DOWN through 70
                if yday > 70 >= today:
                    streak = 0
                    j = i + 1
                    while j + 1 < len(rsi) and rsi[j] is not None and rsi[j] > 70:
                        streak += 1
                        j += 1
                    return 'down70', i, streak
            return None, None, 0

        def gradient_base(val):
            """
            Smooth position score when no recent cross effect dominates.
            - Best near 30, worst near 70
            - <30: do NOT auto-award; give modest values so breakouts matter most
            """
            if val is None:
                return 0.0
            if val < 30:
                # 0..30 -> 0..40 (kept modest so cross↑ gets to do the heavy lifting)
                return max(0.0, min(40.0, (val / 30.0) * 40.0))
            if val <= 70:
                # 30..70 -> 100..0 linear
                return max(0.0, min(100.0, 100.0 * (1.0 - (val - 30.0) / 40.0)))
            # >70: 0..15 taper
            return max(0.0, 15.0 - (val - 70.0) * (15.0 / 30.0))

        # ---------- 1) base from gradient ----------
        base = gradient_base(cur)

        # ---------- 2) cross effect with exponential decay ----------
        cross_type, age, pre_streak = find_last_cross_and_streak()
        if cross_type is not None and age is not None:
            # decay: half-ish after ~5 bars; tweak k for faster/slower fade
            k = 5.0
            decay = pow(2.718281828, -age / k)

            # pre_streak adds a small intensity boost (deep/long extremes matter more)
            strength = 1.0 + min(pre_streak, 10) / 20.0  # up to +50%

            if cross_type == 'up30':
                # pull base toward 100; large day-0 influence, then decay
                weight = 0.75
                adjust = (100.0 - base) * weight * decay * strength
                base += adjust

            elif cross_type == 'down70':
                # pull base toward 0
                weight = 0.75
                adjust = base * weight * decay * strength
                base -= adjust

        # ---------- 3) light modifiers (multiplicative) ----------
        # 50-line bias: below 50 = slightly friendlier for buys; above = slightly worse
        fifty_mult = 1.05 if cur < 50 else 0.95
        base *= fifty_mult

        # short-term momentum: 3 bars ago
        if len(rsi) >= 4 and rsi[3] is not None:
            delta = cur - rsi[3]
            # map roughly [-10, +10] → [0.92, 1.08]
            if delta >= 0:
                base *= min(1.08, 1.0 + (delta / 10.0) * 0.08)
            else:
                base *= max(0.92, 1.0 + (delta / 10.0) * 0.08)

        # ---------- 4) (optional later) divergence as a tiny ±5–8% multiplier ----------
        # Keep off by default until you’ve validated behavior; reintroduce carefully.

        # ---------- 5) clamp ----------
        return int(max(0, min(100, round(base))))
    def calculate_rsi_score(self, target_date, trend_score=None, lookback=30, weekly=False):
        """
        RSI score (0-100). Smooth base + breakout-driven signal.
        Breakout from oversold/overbought is the primary driver, not raw RSI position.
        """
        from database.models.technical import Indicator, WeeklyIndicator
        import math
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        indicators_query = self.weekly_indicators if weekly else self.indicators

        indicators = list(
            indicators_query
                .where(IndicatorModel.date <= target_date)
                .order_by(IndicatorModel.date.desc())
                .limit(lookback + 5)
        )

        if len(indicators) < 5:
            return None

        rsi_vals = [float(ind.rsi) if ind.rsi is not None else None for ind in indicators]
        current_rsi = rsi_vals[0]
        if current_rsi is None:
            return None

        # Continuous trend bias: -1 (strong downtrend) → 0 (sideways) → +1 (strong uptrend)
        trend_bias = float(np.tanh(((trend_score or 50) - 50) * 0.06))

        # === 1. BASE SCORE (trend-aware center) ===
        # Center shifts smoothly: 40 (downtrend) → 45 (sideways) → 50 (uptrend)
        center = 45 + 5 * trend_bias
        score = 50 + 30 * np.tanh((center - current_rsi) * 0.06)

        # === 2. BREAKOUT PUSH (additive) ===
        # RSI 30/70 are standard TA levels — kept as breakout triggers
        last_cross_type = None
        last_cross_age = None
        streak_strength = 0

        for i in range(0, min(len(rsi_vals) - 1, lookback)):
            today_rsi = rsi_vals[i]
            prev_rsi = rsi_vals[i + 1]
            if today_rsi is None or prev_rsi is None:
                continue

            if prev_rsi < 30 <= today_rsi:
                last_cross_type = 'up30'
                last_cross_age = i
                j = i + 1
                while j < len(rsi_vals) and rsi_vals[j] is not None and rsi_vals[j] < 30:
                    j += 1
                streak_strength = min(j - i - 1, 15)
                break

            if prev_rsi > 70 >= today_rsi:
                last_cross_type = 'down70'
                last_cross_age = i
                j = i + 1
                while j < len(rsi_vals) and rsi_vals[j] is not None and rsi_vals[j] > 70:
                    j += 1
                streak_strength = min(j - i - 1, 15)
                break

        if last_cross_type is not None and last_cross_age is not None:
            decay = math.exp(-last_cross_age / 5.0)
            strength = 1.0 + (streak_strength / 15.0)

            # Continuous: bullish breakout amplified by positive trend, dampened by negative
            if last_cross_type == 'up30':
                trend_mod = 1.0 + 0.15 * trend_bias
            else:
                trend_mod = 1.0 - 0.15 * trend_bias

            signal = min(decay * strength * trend_mod, 1.5)
            if last_cross_type == 'up30':
                score += 35 * signal
            else:
                score -= 35 * signal

        # === 2b. MOMENTUM RECOVERY PUSH ===
        if last_cross_type is None and len(rsi_vals) >= 2 and rsi_vals[1] is not None:
            v1 = current_rsi - rsi_vals[1]
            # Smooth velocity gate: ramps 0→1 over delta 3→7 instead of hard cutoff at 5
            vel_gate = max(0, min(1, (abs(v1) - 3) / 4))

            if v1 > 0 and current_rsi < 70 and rsi_vals[1] < 50:
                depth = max(0.5, min(2.0, (50 - rsi_vals[1]) / 10))
                push = min(35, v1 * depth * vel_gate * (1.0 + 0.2 * trend_bias))
                score += push
            elif v1 < 0 and current_rsi > 30 and rsi_vals[1] > 50:
                depth = max(0.5, min(2.0, (rsi_vals[1] - 50) / 10))
                push = min(35, abs(v1) * depth * vel_gate * (1.0 - 0.2 * trend_bias))
                score -= push

        # === 3. DIVERGENCE PUSH (additive) ===
        if len(indicators) >= 10:
            price_data, rsi_data = [], []
            for ind in indicators[:10]:
                ph = ind.price_history()
                if ph and ind.rsi is not None:
                    price_data.append((float(ph.high), float(ph.low)))
                    rsi_data.append(float(ind.rsi))

            if len(price_data) >= 8:
                p_lows = [(i, price_data[i][1]) for i in range(1, len(price_data)-1)
                          if price_data[i][1] < price_data[i-1][1]
                          and price_data[i][1] < price_data[i+1][1]]
                r_lows = [(i, rsi_data[i]) for i in range(1, len(rsi_data)-1)
                          if rsi_data[i] < rsi_data[i-1] and rsi_data[i] < rsi_data[i+1]]

                if len(p_lows) >= 2 and len(r_lows) >= 2:
                    if p_lows[0][1] < p_lows[1][1] and r_lows[0][1] > r_lows[1][1]:
                        mag = (r_lows[0][1] - r_lows[1][1]) / max(1, r_lows[1][1]) * 100
                        score += min(15, mag * 3)

                p_highs = [(i, price_data[i][0]) for i in range(1, len(price_data)-1)
                           if price_data[i][0] > price_data[i-1][0]
                           and price_data[i][0] > price_data[i+1][0]]
                r_highs = [(i, rsi_data[i]) for i in range(1, len(rsi_data)-1)
                           if rsi_data[i] > rsi_data[i-1] and rsi_data[i] > rsi_data[i+1]]

                if len(p_highs) >= 2 and len(r_highs) >= 2:
                    if p_highs[0][1] > p_highs[1][1] and r_highs[0][1] < r_highs[1][1]:
                        mag = (r_highs[1][1] - r_highs[0][1]) / max(1, r_highs[0][1]) * 100
                        score -= min(15, mag * 3)

        # === 4. TREND-STRENGTH FLOOR / CEILING ===
        if trend_score is not None:
            bull_str = max(0, (trend_score - 50) / 50)
            bear_str = max(0, (50 - trend_score) / 50)
            # Soft RSI range gates: taper at edges instead of hard cutoff
            if bull_str > 0:
                rsi_gate = min(1, max(0, (current_rsi - 40) / 5)) * min(1, max(0, (80 - current_rsi) / 5))
                if rsi_gate > 0:
                    floor = 35 + 15 * bull_str
                    if last_cross_type == 'down70' and last_cross_age is not None:
                        floor -= 20 * math.exp(-last_cross_age / 5.0)
                    score = score * (1 - rsi_gate) + max(score, floor) * rsi_gate
            if bear_str > 0:
                rsi_gate = min(1, max(0, (current_rsi - 20) / 5)) * min(1, max(0, (60 - current_rsi) / 5))
                if rsi_gate > 0:
                    ceiling = 65 - 15 * bear_str
                    if last_cross_type == 'up30' and last_cross_age is not None:
                        ceiling += 20 * math.exp(-last_cross_age / 5.0)
                    score = score * (1 - rsi_gate) + min(score, ceiling) * rsi_gate

        return int(max(0, min(100, round(score))))
    def calculate_stoch_score(self, target_date, momentum_lookback=3, weekly=False):
        """Calculate Stochastic score (0-100) where 100 is best. Incorporates momentum when current or recent values were in extreme zones."""
        from database.models.technical import Indicator, WeeklyIndicator
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        indicator = self.indicator_by_date(target_date, weekly=weekly)
        if not indicator or indicator.stoch is None:
            return None
        indicators_query = self.weekly_indicators if weekly else self.indicators
        stoch_values = [float(ind.stoch) for ind in indicators_query.where(IndicatorModel.date <= target_date).order_by(IndicatorModel.date.desc()).limit(momentum_lookback + 1) if ind.stoch is not None]
        if len(stoch_values) == 0: return None
        
        current_stoch = stoch_values[0]

        # Smooth base: oversold(0)→100, neutral(50)→50, overbought(100)→0
        base_score = 50 + 50 * float(np.tanh((50 - current_stoch) * 0.04))

        if len(stoch_values) < 2:
            return int(max(0, min(100, round(base_score))))

        # Continuous extreme zone intensity (0 = not extreme, 1 = deeply extreme)
        overbought_intensity = max(max(0, (s - 70) / 30) for s in stoch_values)
        oversold_intensity = max(max(0, (30 - s) / 30) for s in stoch_values)
        extreme_intensity = max(overbought_intensity, oversold_intensity)

        if extreme_intensity < 0.05:
            return int(max(0, min(100, round(base_score))))

        stoch_momentum = stoch_values[0] - stoch_values[momentum_lookback] if len(stoch_values) > momentum_lookback else 0
        recent_velocity = stoch_values[0] - stoch_values[1]
        consistency = min(2, abs(recent_velocity) * 0.2)

        momentum_adjustment = 0
        if overbought_intensity > oversold_intensity:
            if stoch_momentum < 0:
                momentum_adjustment = min(8, (abs(stoch_momentum) * 1.5 + consistency) * overbought_intensity)
            else:
                momentum_adjustment = max(-5, -stoch_momentum * overbought_intensity)
        else:
            if stoch_momentum > 0:
                momentum_adjustment = min(8, (stoch_momentum * 1.5 + consistency) * oversold_intensity)
            else:
                momentum_adjustment = max(-3, stoch_momentum * oversold_intensity)

        return int(max(0, min(100, round(base_score + momentum_adjustment))))

    def calculate_stochastic_rsi_score(self, target_date):
        """
        Calculate a stochastic RSI-based score for a stock between 0 and 100.
        100 is best, 0 is worst.
        """
        indicator = self.indicator_by_date(target_date)
        
        if not indicator:
            return None

        if indicator.stoch_rsi is None or indicator.stoch_rsi_signal is None:
            return None
        
        score = 100 - (indicator.stoch_rsi / 100) * 100

        return int(max(0, min(score, 100)))

    def calculate_bollinger_bands_score(self, target_date, lookback=10, weekly=False, trend_score=None):
        """
        Trend-aware BB score (0-100) for swing trading.
        Uptrend: middle BB pullback = buy zone. Downtrend: middle BB bounce = sell zone.
        """
        from database.models.technical import Indicator, WeeklyIndicator
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        indicators_query = self.weekly_indicators if weekly else self.indicators
        indicators = list(
            indicators_query
            .where(IndicatorModel.date <= target_date)
            .order_by(IndicatorModel.date.desc())
            .limit(lookback + 1)
        )

        if len(indicators) < 5:
            return None

        current = indicators[0]
        ph = current.price_history()
        price = float(ph.close) if ph and ph.close else None
        if None in (current.upper_band, current.lower_band, current.middle_band, price):
            return None

        upper = float(current.upper_band)
        lower = float(current.lower_band)
        middle = float(current.middle_band)
        band_range = upper - lower
        if band_range == 0:
            return None

        # 0 = lower band, 1 = upper band
        bb_pct = (price - lower) / band_range
        dist_from_middle = (price - middle) / middle * 100

        if trend_score is None:
            trend_score = 50

        # Continuous trend bias: -1 (strong downtrend) → 0 (sideways) → +1 (strong uptrend)
        trend_bias = float(np.tanh((trend_score - 50) * 0.06))
        trend_strength = abs(trend_bias)

        # === 1. TREND-AWARE BASE SCORE (70%) ===
        # Ideal bb_pct shifts smoothly: 0.35 (downtrend) → 0.50 (sideways) → 0.65 (uptrend)
        ideal = 0.50 + 0.15 * trend_bias
        sensitivity = 2.5 + 0.5 * trend_strength
        base = 50 + 40 * np.tanh((ideal - bb_pct) * sensitivity)

        # === 1b. V-RECOVERY / V-DROP DETECTION ===
        if len(indicators) >= 3:
            recent_pcts = []
            for ind in indicators[1:min(11, len(indicators))]:
                p = ind.price_history()
                if p and None not in (ind.upper_band, ind.lower_band):
                    bw = float(ind.upper_band) - float(ind.lower_band)
                    if bw > 0:
                        recent_pcts.append((float(p.close) - float(ind.lower_band)) / bw)
            if recent_pcts:
                # Bullish recovery: scale by how bullish the trend is
                bull_gate = max(0, trend_bias * 2)  # 0 at neutral, ~1 at trend=60+
                if bull_gate > 0 and bb_pct > 0.45:
                    min_pct = min(recent_pcts)
                    recovery = bb_pct - min_pct
                    if min_pct < 0.25 and recovery > 0.3:
                        bonus = min(25, recovery * 35) * bull_gate
                        if bb_pct > 1.0:
                            bonus *= 0.7
                        base = max(base, 50 + bonus)
                # Bearish drop: scale by how bearish the trend is
                bear_gate = max(0, -trend_bias * 2)
                if bear_gate > 0 and bb_pct < 0.55:
                    max_pct = max(recent_pcts)
                    drop = max_pct - bb_pct
                    if max_pct > 0.75 and drop > 0.3:
                        penalty = min(25, drop * 35) * bear_gate
                        if bb_pct < 0.0:
                            penalty *= 0.7
                        base = min(base, 50 - penalty)

        # === 1c. TREND-STRENGTH FLOOR ===
        if trend_score > 50 and bb_pct > 0.7:
            ts = max(0, (trend_score - 50) / 50)
            base = max(base, 30 + 15 * ts)

        # === 2. BOLLINGER SLIDE DETECTION (20%) ===
        slide_score = 50
        check_bars = min(len(indicators), lookback)
        proximity_lower, proximity_upper = 0.0, 0.0

        for ind in indicators[:check_bars]:
            p = ind.price_history()
            if not p or None in (ind.upper_band, ind.lower_band):
                continue
            cp = float(p.close)
            bw = float(ind.upper_band) - float(ind.lower_band)
            if bw == 0:
                continue
            pct = (cp - float(ind.lower_band)) / bw
            proximity_lower += max(0, 1.0 - pct / 0.25)
            proximity_upper += max(0, (pct - 0.75) / 0.25)

        if check_bars > 0:
            slide_lower = proximity_lower / check_bars
            slide_upper = proximity_upper / check_bars
            if slide_lower > slide_upper:
                severity = 25 + 20 * max(0, -trend_bias)
                slide_score = 50 - severity * float(np.tanh(slide_lower * 3))
            elif slide_upper > slide_lower:
                severity = 25 + 15 * max(0, trend_bias)
                slide_score = 50 - severity * float(np.tanh(slide_upper * 3))
            else:
                slide_score = 55

        # === 3. SQUEEZE DETECTION (10%) ===
        squeeze_score = 50
        if len(indicators) >= 5:
            widths = []
            for ind in indicators[:check_bars]:
                if None in (ind.upper_band, ind.lower_band):
                    continue
                widths.append(float(ind.upper_band) - float(ind.lower_band))
            if len(widths) >= 5:
                avg_width = sum(widths) / len(widths)
                width_ratio = widths[0] / avg_width if avg_width > 0 else 1.0
                squeeze = max(0, (0.8 - width_ratio) / 0.3)
                expansion = max(0, (width_ratio - 1.3) / 0.3)
                squeeze_score = 50 + 10 * squeeze * trend_bias - 10 * expansion

        raw_score = base * 0.70 + slide_score * 0.20 + squeeze_score * 0.10
        return int(max(0, min(100, round(raw_score))))
    
    def calculate_macd_score(self, target_date, lookback_months=9, debug=False, weekly=False):
        """
        MACD score (0-100) based on histogram dynamics: velocity, momentum phase,
        and contrarian histogram position. Front-runs the histogram peak.
        """
        from database.models.technical import Indicator, WeeklyIndicator
        from datetime import timedelta
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        indicators_query = self.weekly_indicators if weekly else self.indicators

        lookback_days = int(lookback_months * 30.4)
        start_date = target_date - timedelta(
            weeks=lookback_days if weekly else 0,
            days=0 if weekly else lookback_days
        )

        rows = list(
            indicators_query
            .where(
                (IndicatorModel.date <= target_date) &
                (IndicatorModel.date >= start_date) &
                (IndicatorModel.macd.is_null(False)) &
                (IndicatorModel.macd_signal.is_null(False))
            )
            .order_by(IndicatorModel.date.desc())
        )

        if len(rows) < 10:
            return None

        hists = []
        for r in rows:
            if r.macd_hist is not None:
                hists.append(float(r.macd_hist))
            elif r.macd is not None and r.macd_signal is not None:
                hists.append(float(r.macd) - float(r.macd_signal))

        if len(hists) < 6:
            return None

        velocities = [hists[i] - hists[i + 1] for i in range(len(hists) - 1)]
        accelerations = [velocities[i] - velocities[i + 1] for i in range(len(velocities) - 1)]

        smooth_n = min(3, len(velocities))
        avg_velocity = sum(velocities[:smooth_n]) / smooth_n

        accel_n = min(3, len(accelerations))
        avg_accel = sum(accelerations[:accel_n]) / accel_n if accel_n > 0 else 0

        def normalize_tanh(value, series, sensitivity=2.0):
            if len(series) < 5:
                return 0
            sorted_s = sorted(series)
            q1 = sorted_s[len(sorted_s) // 4]
            q3 = sorted_s[3 * len(sorted_s) // 4]
            iqr = q3 - q1
            if iqr == 0:
                return 0
            return float(np.tanh(value / iqr * sensitivity))

        # === 1. VELOCITY (35%) ===
        vel_norm = normalize_tanh(avg_velocity, velocities, 1.5)
        velocity_score = 50 + 45 * vel_norm

        # === 2. MOMENTUM PHASE (40%) ===
        # Combines velocity direction + acceleration to detect cycle position.
        # Peaking (vel>0, accel<0) = highest; Bottoming (vel<0, accel>0) = lowest.
        vel_norm_phase = normalize_tanh(avg_velocity, velocities, 1.2)
        accel_norm_phase = normalize_tanh(avg_accel, accelerations, 1.2)

        if vel_norm_phase > 0 and accel_norm_phase < 0:
            # Peaking: growth decelerating → at/near histogram pinnacle
            phase_raw = 0.5 + 0.5 * vel_norm_phase + 0.3 * abs(accel_norm_phase)
        elif vel_norm_phase > 0 and accel_norm_phase >= 0:
            # Building: accelerating growth → approaching peak
            phase_raw = 0.3 + 0.4 * vel_norm_phase + 0.2 * accel_norm_phase
        elif vel_norm_phase <= 0 and accel_norm_phase > 0:
            # Bottoming: decline decelerating → trough forming, early recovery
            phase_raw = -0.3 + 0.4 * abs(accel_norm_phase) + 0.2 * abs(vel_norm_phase)
        else:
            # Declining: accelerating decline → past peak, falling
            phase_raw = -0.5 - 0.3 * abs(vel_norm_phase) - 0.2 * abs(accel_norm_phase)

        phase_score = 50 + 45 * max(-1, min(1, phase_raw))

        # === 3. HISTOGRAM POSITION (25%, contrarian) ===
        # High histogram = overextended = lower score; deeply negative = washed out = higher score
        hist_norm = normalize_tanh(hists[0], hists, 1.5)
        hist_score = 50 - 40 * hist_norm

        score = velocity_score * 0.35 + phase_score * 0.40 + hist_score * 0.25
        return int(max(0, min(100, round(score))))
    
    def calculate_trend_score(self, target_date, lookback=20, weekly=False):
        """
        Swing trading trend score (0-100) blending short-term EMAs with macro structure.
        Returns: 0 = strong downtrend, 50 = neutral, 100 = strong uptrend
        """
        from database.models.technical import Indicator, WeeklyIndicator
        IndicatorModel = WeeklyIndicator if weekly else Indicator

        indicators_query = self.weekly_indicators if weekly else self.indicators
        recent_indicators = list(
            indicators_query
            .where(IndicatorModel.date <= target_date)
            .order_by(IndicatorModel.date.desc())
            .limit(lookback + 1)
        )

        if len(recent_indicators) < lookback + 1:
            return None

        current = recent_indicators[0]
        if None in (current.ema_21, current.ema_50):
            return None

        current_price = float(current.price_history().close)
        ema_21 = float(current.ema_21)
        ema_50 = float(current.ema_50)
        ema_200 = float(current.ema_200) if current.ema_200 is not None else None

        past_short = recent_indicators[min(10, lookback)]
        past_long = recent_indicators[lookback]
        if past_short.ema_21 is None:
            return None
        past_ema_21 = float(past_short.ema_21)
        past_ema_50 = float(past_long.ema_50) if past_long.ema_50 is not None else None

        # === 1. PRICE POSITION (30%) ===
        # Blend price vs EMA 50 (short-term) with price vs EMA 200 (long-term)
        price_vs_50 = (current_price - ema_50) / ema_50 * 100
        pos_short = 50 + 50 * np.tanh(price_vs_50 * 0.3)
        if ema_200 is not None:
            price_vs_200 = (current_price - ema_200) / ema_200 * 100
            pos_long = 50 + 50 * np.tanh(price_vs_200 * 0.15)
            position_score = pos_short * 0.6 + pos_long * 0.4
        else:
            position_score = pos_short

        # === 2. MA ALIGNMENT (25%) ===
        alignment_pct = (ema_21 - ema_50) / ema_50 * 100
        alignment_score = 50 + 50 * np.tanh(alignment_pct * 0.4)

        # === 3. TREND MOMENTUM (20%) ===
        # Blend 10-day EMA 21 slope with 20-day EMA 50 slope
        if past_ema_21 == 0:
            mom_short = 50
        else:
            slope_21 = (ema_21 - past_ema_21) / past_ema_21 * 100
            mom_short = 50 + 50 * np.tanh(slope_21 * 0.5)
        if past_ema_50 is not None and past_ema_50 != 0:
            slope_50 = (ema_50 - past_ema_50) / past_ema_50 * 100
            mom_long = 50 + 50 * np.tanh(slope_50 * 0.8)
            momentum_score = mom_short * 0.5 + mom_long * 0.5
        else:
            momentum_score = mom_short

        # === 4. MACRO STRUCTURE (25%) ===
        # EMA 50 vs EMA 200 — very slow-moving, anchors score during pullbacks
        if ema_200 is not None and ema_200 != 0:
            macro_pct = (ema_50 - ema_200) / ema_200 * 100
            macro_score = 50 + 50 * np.tanh(macro_pct * 0.2)
        else:
            macro_score = 50

        raw_score = (position_score * 0.30 + alignment_score * 0.25
                     + momentum_score * 0.20 + macro_score * 0.25)
        return int(max(0, min(100, round(raw_score))))


    def calculate_fundamental_score(self) -> float:
        """Calculate fundamental score (0-10) based on multiple components"""
        
        growth_score = self.calculate_growth_score()
        valuation_score = self.calculate_valuation_score()
        profitability_score = self.calculate_profitability_score()
        financial_health_score = self.calculate_financial_health_score()
        market_score = self.calculate_market_score()
        price_target_score = self.calculate_price_target_score()
        final_score = (
            growth_score * 0.27 +
            valuation_score * 0.22 +
            profitability_score * 0.18 +
            financial_health_score * 0.14 +
            market_score * 0.09 +
            price_target_score * 0.10
        )

        return round(final_score, 2)

    def calculate_growth_score(self) -> float:
        """Calculate growth score (0-10) based on multiple components"""
        growth_scores = {
            'eps_growth': normalize_score(self.eps_growth, 0, self.industry_max('eps_growth') or 0.25),
            'revenue_growth': normalize_score(self.revenue_growth, 0, self.industry_max('revenue_growth') or 0.20),
            'margin_growth': normalize_score(self.operating_margin, 0.10, self.industry_max('operating_margin') or 0.30)
        }
        growth_score = (
            sum(score for score in growth_scores.values() if score is not None) / 
            len([score for score in growth_scores.values() if score is not None])
            if any(score is not None for score in growth_scores.values()) else 0
        )
        return growth_score

    def calculate_valuation_score(self) -> float:
        """Calculate valuation score (0-10) based on multiple components"""
        valuation_scores = {
            'pe': normalize_score(self.pe, 5, self.industry_max('pe') or 30, reverse=True),
            'peg': normalize_score(self.peg, 0.5, self.industry_max('peg') or 2, reverse=True),
            'price_to_book': normalize_score(self.price_to_book, 1, self.industry_max('price_to_book') or 5, reverse=True),
            'price_to_sales': normalize_score(self.price_to_sales, 1, self.industry_max('price_to_sales') or 10, reverse=True)
        }
        valuation_score = (
            sum(score for score in valuation_scores.values() if score is not None) /
            len([score for score in valuation_scores.values() if score is not None])
            if any(score is not None for score in valuation_scores.values()) else 0
        )
        return valuation_score

    def calculate_profitability_score(self) -> float:
        """Calculate profitability score (0-10) based on multiple components"""
        profitability_scores = {
            'roe': normalize_score(self.roe, self.industry_min('roe') or 0.10, self.industry_max('roe') or 0.25),
            'roa': normalize_score(self.roa, self.industry_min('roa') or 0.05, self.industry_max('roa') or 0.15),
            'profit_margin': normalize_score(self.profit_margin, self.industry_min('profit_margin') or 0.05, self.industry_max('profit_margin') or 0.20)
        }
        profitability_score = (
            sum(score for score in profitability_scores.values() if score is not None) / 
            len([score for score in profitability_scores.values() if score is not None])
            if any(score is not None for score in profitability_scores.values()) else 0
        )
        return profitability_score

    def calculate_financial_health_score(self) -> float:
        """Calculate financial health score (0-10) based on multiple components"""
        financial_health_scores = {
            'debt_to_equity': normalize_score(self.debt_to_equity, 0, self.industry_max('debt_to_equity') or 2, reverse=True),
            'fcf': normalize_score(self.free_cash_flow, 0, self.revenue * 0.1 if self.revenue else None)
        }
        financial_health_score = (
            sum(score for score in financial_health_scores.values() if score is not None) / 
            len([score for score in financial_health_scores.values() if score is not None])
            if any(score is not None for score in financial_health_scores.values()) else 0
        )
        return financial_health_score

    def calculate_market_score(self) -> float:
        """Calculate market position score (0-10) based on multiple components"""
        market_scores = {
            'market_cap': normalize_score(self.market_cap, self.industry_min('market_cap') or 1e9, self.industry_max('market_cap') or 100e9),
            'beta': normalize_score(self.beta, self.industry_min('beta') or 0.5, self.industry_max('beta') or 1.5, reverse=True)
        }
        market_score = (
            sum(score for score in market_scores.values() if score is not None) / 
            len([score for score in market_scores.values() if score is not None])
            if any(score is not None for score in market_scores.values()) else 0
        )
        return market_score

    def calculate_price_target_score(self) -> float:
        """
        Calculate score based on price targets and analyst coverage
        Returns score 0-10
        """
        if not all([self.target_mean_price, self.current_price(), self.number_of_analysts]):
            return 0
            
        # Calculate potential upside/downside
        potential_return = (self.target_mean_price - self.current_price()) / self.current_price()
        
        # Score based on potential return (-50% to +100% mapped to 0-10)
        return_score = normalize_score(potential_return, -0.5, 1.0)
        
        # Analyst coverage score (0-20 analysts mapped to 0-10)
        coverage_score = normalize_score(self.number_of_analysts, 0, 20)
        
        # Consensus strength score (how tight the range is)
        if self.target_high_price and self.target_low_price and self.target_mean_price:
            range_percentage = (self.target_high_price - self.target_low_price) / self.target_mean_price
            consensus_score = normalize_score(range_percentage, 1.0, 0.2, reverse=True)
        else:
            consensus_score = 0

        # Weight the components
        final_score = (
            return_score * 0.5 +          # 50% weight on potential return
            coverage_score * 0.3 +        # 30% weight on analyst coverage
            consensus_score * 0.2         # 20% weight on consensus strength
        )

        return round(final_score, 2)

    def graham_value(self):
        eps = self.eps or 0
        growth_rate = self.revenue_growth or 0
        if eps <= 0 or growth_rate <= 0:
            return 0.0
        return eps * (8.5 + 2 * growth_rate)

    def growth_adjusted_pe_valuation(self):
        eps = self.eps or 0
        growth_rate = self.revenue_growth or 0
        pe = self.pe or 0
        
        if eps <= 0 or growth_rate <= 0 or pe <= 0:
            return 0.0
        
        return eps * pe * growth_rate

    def comprehensive_valuation(self):
        # Basic financial health score (0-1)
        financial_health = (
            min(1,  self.roa/20 if  self.roa else 0) * 0.3 +  # ROE weight
            min(1, self.roa/10 if self.roa else 0) * 0.3 +  # ROA weight
            min(1, self.profit_margin if self.profit_margin else 0) * 0.4  # Margin weight
        )
        
        # Debt risk factor (0-1, lower is better)
        debt_risk = 1 - min(1, self.debt_to_equity/200 if self.debt_to_equity else 0)

        # Combined weighted valuation
        valuation = (
            self.graham_value() * 0.4 +  # Conservative base
            self.growth_adjusted_pe_valuation() * 0.3 +     # Growth consideration
            self.current_price() * 0.3   # Market sentiment
        )
        
        # Apply financial health and risk adjustments
        valuation *= financial_health
        valuation *= debt_risk
        return valuation
    
    class Meta:
        table_name = 'stocks'
        indexes = (
            (('symbol',), True),         # Primary key
            (('flagged',), False),       # For flagged stock filtering
            (('revenue',), False),       # For revenue-based queries
            (('sector',), False),        # For sector-based queries
            (('industry',), False),      # For industry-based queries
        )

