from trader_database import BaseModel
from database.utils.scoring import (
    normalize_score, calculate_theta_factor, calculate_option_return,
    calculate_weekly_composite, calculate_weekly_adjustment,
    compute_overall_score,
)
import math, json, bisect
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


class SplitEvent(BaseModel):
    """Tracks stock splits that have been applied to PriceHistory so we never double-adjust."""
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='split_events')
    split_date = DateField()
    ratio = DecimalField(max_digits=10, decimal_places=4)  # e.g. 2.0 for 2-for-1
    processed_at = DateTimeField(null=True)

    class Meta:
        table_name = 'split_events'
        indexes = ((('symbol', 'split_date'), True),)
        primary_key = CompositeKey('symbol', 'split_date')

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)

    @classmethod
    def processed_dates(cls, symbol):
        """Returns set of split_date strings already applied for this symbol."""
        return {str(e.split_date) for e in cls.select().where(cls.symbol == symbol)}


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
    pct_from_ema50 = FloatField(null=True)
    pct_from_ema200 = FloatField(null=True)
    bb_position = FloatField(null=True)
    score_velocity_7d = IntegerField(null=True)
    regime_composite = DecimalField(max_digits=5, decimal_places=2, null=True)
    regime_multiplier = DecimalField(max_digits=5, decimal_places=4, null=True)
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
        return ((float(target) - float(price)) / float(price)) * 100

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
        """Thin wrapper — fetches DB data then delegates to pure compute_overall_score()."""
        if None in (self.trend, self.bb, self.rsi, self.stoch, self.macd, self.technical_alignment):
            return None

        from database.models.technical import Indicator

        # BB position
        bb_pct = None
        ind = Indicator.get_or_none(
            (Indicator.symbol == self.symbol) & (Indicator.date == self.date))
        if ind and None not in (ind.upper_band, ind.lower_band):
            bw = float(ind.upper_band) - float(ind.lower_band)
            if bw > 0:
                ph = ind.price_history()
                if ph:
                    bb_pct = (float(ph.close) - float(ind.lower_band)) / bw

        # Weekly context
        ws = self.weekly_score
        pws = self.prev_weekly_score if ws else None
        ws_trend = ws.trend if ws else None
        ws_rsi   = ws.rsi   if ws and ws.rsi and ws.macd else None
        ws_macd  = ws.macd  if ws and ws.rsi and ws.macd else None

        # Volume signal
        vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = 1.0, 50, 'NEUTRAL', 0.0, 0.0, 50.0
        try:
            from volume_amplifier import get_volume_multiplier
            vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = get_volume_multiplier(
                symbol=self.symbol,
                today=self.date,
                pulled_at=datetime.now(),
                pre_volume_overall=None,
            )
        except Exception:
            pass

        overall, weight_info, vol_update = compute_overall_score(
            self.trend, self.bb, self.rsi, self.macd, self.stoch, self.technical_alignment,
            bb_pct=bb_pct,
            ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
            prev_ws_trend=pws.trend if pws else None,
            prev_ws_rsi=pws.rsi   if pws else None,
            prev_ws_macd=pws.macd if pws else None,
            vol_mult=vol_mult, vol_raw=vol_raw, vol_sig=vol_sig, vol_mag=vol_mag,
            blend_w=blend_w, vol_target=vol_target,
        )

        if vol_update and (vol_update['volume_signal'] != 'NEUTRAL'
                           or not self.volume_signal or self.volume_signal == 'NEUTRAL'):
            self.volume          = vol_update['volume']
            self.volume_signal   = vol_update['volume_signal']
            self.volume_magnitude = vol_update['volume_magnitude']

        self.weight_info = json.dumps(weight_info)
        return overall
    
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

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        try:
            DB.execute_sql('ALTER TABLE scores DROP COLUMN rsi_2')
        except Exception:
            pass
        for col, typedef in [
            ('pct_from_ema50',      'FLOAT NULL'),
            ('pct_from_ema200',     'FLOAT NULL'),
            ('bb_position',         'FLOAT NULL'),
            ('score_velocity_7d',   'INT NULL'),
            ('regime_composite',    'DECIMAL(5,2) NULL'),
            ('regime_multiplier',   'DECIMAL(5,4) NULL'),
        ]:
            try:
                DB.execute_sql(f'ALTER TABLE scores ADD COLUMN {col} {typedef}')
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
            from database.project_root import get_trader_project_root, trader_git_output
            try:
                root = get_trader_project_root()
                if not (root / ".git").exists():
                    commit = "unknown"
                else:
                    commit = trader_git_output(["rev-parse", "--short", "HEAD"]).decode().strip()
            except Exception:
                commit = "unknown"
            cls._current, created = cls.get_or_create(git_commit=commit)
            if created:
                try:
                    msg = trader_git_output(["log", "-1", "--format=%s", commit]).decode().strip()
                    cls._current.git_message = msg
                    cls._current.save()
                except Exception:
                    pass
        return cls._current

    @classmethod
    def get_active_scores_version(cls):
        """Version used for persisted Score rows in the API (may differ from git HEAD)."""
        max_id = (
            cls.select(fn.MAX(cls.id))
            .where(
                (cls.git_message.is_null(False))
                & (fn.TRIM(cls.git_message) != '')
            )
            .scalar()
        )
        if max_id is not None:
            return cls.get_by_id(max_id)
        v = cls.select().order_by(cls.id.desc()).first()
        return v if v is not None else cls.get_or_create_current()

    class Meta:
        table_name = 'algorithm_versions'


class MarketRegime(BaseModel):
    date = DateField(unique=True)
    vix_close = DecimalField(max_digits=6, decimal_places=2, null=True)
    vix_10d_change = DecimalField(max_digits=6, decimal_places=4, null=True)
    spy_close = DecimalField(max_digits=8, decimal_places=2, null=True)
    spy_ema50 = DecimalField(max_digits=8, decimal_places=2, null=True)
    spy_ema200 = DecimalField(max_digits=8, decimal_places=2, null=True)
    internal_breadth_score = DecimalField(max_digits=5, decimal_places=2, null=True)
    vix_score = DecimalField(max_digits=5, decimal_places=2, null=True)
    market_trend_score = DecimalField(max_digits=5, decimal_places=2, null=True)
    regime_composite = DecimalField(max_digits=5, decimal_places=2, null=True)
    regime_multiplier = DecimalField(max_digits=5, decimal_places=4, null=True)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'market_regime'

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)

    @classmethod
    def for_date(cls, d):
        return cls.get_or_none(cls.date == d)


class MarketBreadth(BaseModel):
    """Daily market breadth indicators computed from the tracked stock universe."""
    date = DateField(unique=True)

    # Raw counts
    advancing = IntegerField(null=True)
    declining = IntegerField(null=True)
    unchanged = IntegerField(null=True)
    total_issues = IntegerField(null=True)

    # Volume flows
    advancing_volume = DecimalField(max_digits=18, decimal_places=0, null=True)
    declining_volume = DecimalField(max_digits=18, decimal_places=0, null=True)

    # TRIN (Arms Index): (A/D ratio) / (Adv vol / Dec vol).  <1 bullish, >1 bearish.
    trin = DecimalField(max_digits=8, decimal_places=4, null=True)

    # 52-week new highs / new lows (within 3% of 252-day high/low)
    new_highs_52w = IntegerField(null=True)
    new_lows_52w = IntegerField(null=True)

    # Cumulative Advance-Decline Line
    ad_diff = IntegerField(null=True)          # today's A - D
    ad_line = DecimalField(max_digits=12, decimal_places=2, null=True)  # running cumulative

    # McClellan Oscillator / Summation Index
    ema19_ad_diff = DecimalField(max_digits=10, decimal_places=4, null=True)
    ema39_ad_diff = DecimalField(max_digits=10, decimal_places=4, null=True)
    mcclellan_oscillator = DecimalField(max_digits=10, decimal_places=4, null=True)
    mcclellan_summation = DecimalField(max_digits=14, decimal_places=4, null=True)

    # Zweig Breadth Thrust: 10-day EMA of A/(A+D)
    ad_ratio = DecimalField(max_digits=6, decimal_places=4, null=True)  # A / (A+D) today
    ema10_ad_ratio = DecimalField(max_digits=6, decimal_places=4, null=True)
    zweig_thrust_active = BooleanField(default=False)   # boost is still material (> 0.1pts)
    zweig_thrust_date = DateField(null=True)             # date the thrust originally fired
    zweig_boost = DecimalField(max_digits=5, decimal_places=2, null=True)  # current decayed boost value

    # Percent of stocks above moving averages
    pct_above_ema50 = DecimalField(max_digits=5, decimal_places=2, null=True)
    pct_above_ema200 = DecimalField(max_digits=5, decimal_places=2, null=True)

    # Hindenburg Omen
    hindenburg_omen = BooleanField(default=False)        # triggered today
    hindenburg_confirmed = BooleanField(default=False)   # 2+ omens in rolling 30 days
    hindenburg_penalty = DecimalField(max_digits=5, decimal_places=2, null=True)  # current decayed penalty

    # Composite breadth score fed into regime (0-100)
    breadth_score = DecimalField(max_digits=5, decimal_places=2, null=True)

    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'market_breadth'

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)
        # Migrations for new columns added after initial deploy
        for col, definition in [
            ('zweig_boost',        'DECIMAL(5,2) NULL'),
            ('hindenburg_penalty', 'DECIMAL(5,2) NULL'),
        ]:
            try:
                DB.execute_sql(f'ALTER TABLE market_breadth ADD COLUMN {col} {definition}')
            except Exception:
                pass

    @classmethod
    def latest(cls):
        return cls.select().order_by(cls.date.desc()).first()

    @classmethod
    def for_date(cls, d):
        return cls.get_or_none(cls.date == d)


class ScoreAssessmentRun(BaseModel):
    id = AutoField()
    run_at = DateTimeField(default=datetime.now)
    symbol = CharField(default='')
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
    exit_rule_tp = FloatField(null=True, default=0.20)
    exit_rule_sl = FloatField(null=True, default=0.07)

    class Meta:
        table_name = 'score_assessment_runs'
        indexes = (
            (('version', 'run_at'), False),
            (('version', 'symbol', 'lookback_days'), True),
        )

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        try:
            DB.execute_sql("UPDATE score_assessment_runs SET symbol = '' WHERE symbol IS NULL")
        except Exception:
            pass
        try:
            DB.execute_sql(
                "DELETE t1 FROM score_assessment_runs t1 "
                "INNER JOIN score_assessment_runs t2 "
                "ON t1.version_id = t2.version_id AND t1.symbol = t2.symbol "
                "AND t1.lookback_days = t2.lookback_days AND t1.id < t2.id"
            )
        except Exception:
            pass
        try:
            DB.execute_sql(
                "ALTER TABLE score_assessment_runs ADD UNIQUE INDEX uq_assess_v_sym_lb "
                "(version_id, symbol, lookback_days)"
            )
        except Exception:
            pass
        for col, typedef in [('exit_rule_tp', 'FLOAT NULL'), ('exit_rule_sl', 'FLOAT NULL')]:
            try:
                DB.execute_sql(f'ALTER TABLE score_assessment_runs ADD COLUMN {col} {typedef}')
            except Exception:
                pass


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
    # MAE (Maximum Adverse Excursion) — worst drawdown from entry within window.
    # Direction-aware: for buy signals uses daily lows, for sell signals uses daily highs.
    avg_mae_1d = FloatField(null=True)
    avg_mae_7d = FloatField(null=True)
    avg_mae_15d = FloatField(null=True)
    avg_mae_30d = FloatField(null=True)
    avg_mae_60d = FloatField(null=True)
    avg_mae_90d = FloatField(null=True)
    median_mae_1d = FloatField(null=True)
    median_mae_7d = FloatField(null=True)
    median_mae_15d = FloatField(null=True)
    median_mae_30d = FloatField(null=True)
    median_mae_60d = FloatField(null=True)
    median_mae_90d = FloatField(null=True)
    avg_mae_winner_30d = FloatField(null=True)
    avg_mae_winner_60d = FloatField(null=True)
    avg_mae_loser_30d = FloatField(null=True)
    avg_mae_loser_60d = FloatField(null=True)
    # MFE (Maximum Favorable Excursion) — best gain from entry within window.
    # Direction-aware: for buy signals uses daily highs, for sell signals uses daily lows.
    avg_mfe_1d = FloatField(null=True)
    avg_mfe_7d = FloatField(null=True)
    avg_mfe_15d = FloatField(null=True)
    avg_mfe_30d = FloatField(null=True)
    avg_mfe_60d = FloatField(null=True)
    avg_mfe_90d = FloatField(null=True)
    median_mfe_1d = FloatField(null=True)
    median_mfe_7d = FloatField(null=True)
    median_mfe_15d = FloatField(null=True)
    median_mfe_30d = FloatField(null=True)
    median_mfe_60d = FloatField(null=True)
    median_mfe_90d = FloatField(null=True)
    # Capture ratio = avg_return_Nd / avg_mfe_Nd. Null if avg_mfe is zero or null.
    capture_ratio_1d = FloatField(null=True)
    capture_ratio_7d = FloatField(null=True)
    capture_ratio_15d = FloatField(null=True)
    capture_ratio_30d = FloatField(null=True)
    capture_ratio_60d = FloatField(null=True)
    capture_ratio_90d = FloatField(null=True)
    # Shakeout detection: quantifies the win-rate dip at short horizons before recovery.
    shakeout_depth = FloatField(null=True)       # win_rate_7d - win_rate_60d (negative = shakeout)
    shakeout_recovery = IntegerField(null=True)  # first N in [7,15,30] where win_rate_Nd >= win_rate_60d

    @classmethod
    def ensure_schema(cls):
        """Migration 2026-04-01: add MAE/MFE/capture/shakeout columns + band IC table."""
        from database.trader_database import DB
        _new_float_cols = [
            'avg_mae_1d', 'avg_mae_7d', 'avg_mae_15d', 'avg_mae_30d', 'avg_mae_60d', 'avg_mae_90d',
            'median_mae_1d', 'median_mae_7d', 'median_mae_15d', 'median_mae_30d', 'median_mae_60d', 'median_mae_90d',
            'avg_mae_winner_30d', 'avg_mae_winner_60d', 'avg_mae_loser_30d', 'avg_mae_loser_60d',
            'avg_mfe_1d', 'avg_mfe_7d', 'avg_mfe_15d', 'avg_mfe_30d', 'avg_mfe_60d', 'avg_mfe_90d',
            'median_mfe_1d', 'median_mfe_7d', 'median_mfe_15d', 'median_mfe_30d', 'median_mfe_60d', 'median_mfe_90d',
            'capture_ratio_1d', 'capture_ratio_7d', 'capture_ratio_15d',
            'capture_ratio_30d', 'capture_ratio_60d', 'capture_ratio_90d',
            'shakeout_depth',
        ]
        for table in ['score_assessment_results', 'score_assessment_meta']:
            for col in _new_float_cols:
                try:
                    DB.execute_sql(f'ALTER TABLE {table} ADD COLUMN {col} FLOAT NULL')
                except Exception:
                    pass
            try:
                DB.execute_sql(f'ALTER TABLE {table} ADD COLUMN shakeout_recovery INT NULL')
            except Exception:
                pass
        DB.create_tables([ScoreAssessmentBandIC, ScoreAssessmentOptionResult, ScoreAssessmentTierResult], safe=True)
        # ScoreAssessmentOptionResult tier/gap columns (additive, safe to re-run)
        _opt_cols = [
            ('score', 'INT NULL'),
            ('iv_rank_1y', 'FLOAT NULL'), ('iv_rank_sample_days', 'INT NULL'),
            ('oi_tier', 'VARCHAR(10) NULL'), ('dte_tier', 'VARCHAR(10) NULL'),
            ('composite_tier', 'VARCHAR(5) NULL'),
            ('gap_days_in_window', 'INT NOT NULL DEFAULT 0'),
            ('low_coverage', 'TINYINT(1) NOT NULL DEFAULT 0'),
            ('iv_regime', 'VARCHAR(10) NULL'),
        ]
        for col, typedef in _opt_cols:
            try:
                DB.execute_sql(f'ALTER TABLE score_assessment_option_results ADD COLUMN {col} {typedef}')
            except Exception:
                pass

    class Meta:
        table_name = 'score_assessment_results'
        indexes = (
            (('run', 'bucket'), False),
        )


class ScoreAssessmentMeta(BaseModel):
    """Pre-computed aggregate assessment per version+bucket — Trend-style cache for fast API reads."""
    version = ForeignKeyField(AlgorithmVersion, backref='meta_results', on_delete='CASCADE')
    bucket = CharField()
    run_count = IntegerField(default=0)
    total_peaks = IntegerField(default=0)
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
    correlation_1d = FloatField(null=True)
    correlation_7d = FloatField(null=True)
    correlation_15d = FloatField(null=True)
    correlation_30d = FloatField(null=True)
    correlation_60d = FloatField(null=True)
    correlation_90d = FloatField(null=True)
    avg_mae_1d = FloatField(null=True)
    avg_mae_7d = FloatField(null=True)
    avg_mae_15d = FloatField(null=True)
    avg_mae_30d = FloatField(null=True)
    avg_mae_60d = FloatField(null=True)
    avg_mae_90d = FloatField(null=True)
    median_mae_1d = FloatField(null=True)
    median_mae_7d = FloatField(null=True)
    median_mae_15d = FloatField(null=True)
    median_mae_30d = FloatField(null=True)
    median_mae_60d = FloatField(null=True)
    median_mae_90d = FloatField(null=True)
    avg_mae_winner_30d = FloatField(null=True)
    avg_mae_winner_60d = FloatField(null=True)
    avg_mae_loser_30d = FloatField(null=True)
    avg_mae_loser_60d = FloatField(null=True)
    avg_mfe_1d = FloatField(null=True)
    avg_mfe_7d = FloatField(null=True)
    avg_mfe_15d = FloatField(null=True)
    avg_mfe_30d = FloatField(null=True)
    avg_mfe_60d = FloatField(null=True)
    avg_mfe_90d = FloatField(null=True)
    median_mfe_1d = FloatField(null=True)
    median_mfe_7d = FloatField(null=True)
    median_mfe_15d = FloatField(null=True)
    median_mfe_30d = FloatField(null=True)
    median_mfe_60d = FloatField(null=True)
    median_mfe_90d = FloatField(null=True)
    capture_ratio_1d = FloatField(null=True)
    capture_ratio_7d = FloatField(null=True)
    capture_ratio_15d = FloatField(null=True)
    capture_ratio_30d = FloatField(null=True)
    capture_ratio_60d = FloatField(null=True)
    capture_ratio_90d = FloatField(null=True)
    shakeout_depth = FloatField(null=True)
    shakeout_recovery = IntegerField(null=True)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'score_assessment_meta'
        primary_key = CompositeKey('version', 'bucket')


class ScoreAssessmentBandIC(BaseModel):
    """Intra-band Information Coefficient — pearson correlation of score vs return
    within a NON-OVERLAPPING score band (e.g. 75-79, not 75+).
    Stored separately from cumulative bucket rows to avoid semantic confusion:
    every field on a bucket row reflects the full bucket population, IC does not."""
    id = AutoField()
    run = ForeignKeyField(ScoreAssessmentRun, backref='band_ics', on_delete='CASCADE')
    band = CharField()          # e.g. "75-79", "90-94", "0-5"
    sample_count = IntegerField(default=0)
    ic_1d = FloatField(null=True)
    ic_7d = FloatField(null=True)
    ic_15d = FloatField(null=True)
    ic_30d = FloatField(null=True)
    ic_60d = FloatField(null=True)
    ic_90d = FloatField(null=True)

    class Meta:
        table_name = 'score_assessment_band_ic'
        indexes = ((('run', 'band'), True),)


class ScoreAssessmentOptionResult(BaseModel):
    """Per-peak option backtest result. One row per eligible peak with ATM call data."""
    id = AutoField()
    run = ForeignKeyField(ScoreAssessmentRun, backref='option_results', on_delete='CASCADE')
    peak_date = DateField()
    symbol = CharField()
    score = IntegerField(null=True)
    bucket = CharField()              # tightest qualifying bucket, e.g. '90+' for score=92
    underlying_entry = DecimalField(max_digits=10, decimal_places=2)
    selected_strike = DecimalField(max_digits=10, decimal_places=2)
    selected_expiry = DateField()
    days_to_expiry_at_entry = IntegerField()
    entry_iv = DecimalField(max_digits=5, decimal_places=4, null=True)
    entry_option_price = DecimalField(max_digits=8, decimal_places=2)
    entry_open_interest = IntegerField()
    exit_option_price = DecimalField(max_digits=8, decimal_places=2, null=True)
    exit_day = IntegerField(null=True)
    exit_reason = CharField(null=True)      # 'TP', 'SL', 'TIME', 'OPTION_SL'
    option_return = FloatField(null=True)
    iv_at_exit = DecimalField(max_digits=5, decimal_places=4, null=True)
    iv_change = FloatField(null=True)
    underlying_return_at_exit = FloatField(null=True)
    eligible = BooleanField(default=True)
    exclusion_reason = CharField(null=True)
    iv_regime = CharField(null=True)        # 'LOW', 'MEDIUM', 'HIGH' (30-day percentile)
    # Tier fields (Part 2)
    iv_rank_1y = FloatField(null=True)
    iv_rank_sample_days = IntegerField(null=True)
    oi_tier = CharField(null=True)          # 'THIN', 'NORMAL', 'LIQUID'
    dte_tier = CharField(null=True)         # 'SHORT', 'MEDIUM', 'LONG'
    composite_tier = CharField(null=True)   # 'A', 'B', 'C', 'D'
    # Gap quality (Part 2)
    gap_days_in_window = IntegerField(default=0)
    low_coverage = BooleanField(default=False)

    class Meta:
        table_name = 'score_assessment_option_results'
        indexes = (
            (('run', 'bucket'), False),
            (('run', 'symbol', 'peak_date'), True),
        )


class ScoreAssessmentTierResult(BaseModel):
    """Per-run, per-bucket, per-tier-type aggregation of option backtest results."""
    id = AutoField()
    run = ForeignKeyField(ScoreAssessmentRun, backref='tier_results', on_delete='CASCADE')
    bucket = CharField()
    tier_type = CharField()     # 'IV_RANK', 'OI_TIER', 'DTE_TIER', 'COMPOSITE'
    tier_value = CharField()    # 'LOW'/'MEDIUM'/'HIGH', 'THIN'/'NORMAL'/'LIQUID', etc.
    sample_count = IntegerField(default=0)
    option_avg_return = FloatField(null=True)
    option_median_return = FloatField(null=True)
    option_win_rate = FloatField(null=True)
    option_profit_factor = FloatField(null=True)
    avg_entry_iv = FloatField(null=True)
    avg_dte_at_entry = FloatField(null=True)
    exit_tp_pct = FloatField(null=True)
    exit_sl_pct = FloatField(null=True)
    exit_time_pct = FloatField(null=True)
    exit_optsl_pct = FloatField(null=True)
    confidence = CharField(null=True)       # 'HIGH' N>=30, 'LOW' 15<=N<30, 'INSUFFICIENT' N<15

    class Meta:
        table_name = 'score_assessment_tier_results'
        indexes = (
            (('run', 'bucket', 'tier_type', 'tier_value'), True),
        )


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

    def calculate_indicators_and_scores(self, full=False, silent=False):
        self.calculate_indicators(full, silent=silent)
        self.calculate_indicators(full, weekly=True, silent=silent)
        self.calculate_scores(full=full, weekly=True, silent=silent)
        self.calculate_scores(full=full, silent=silent)

    def calculate_indicators(self, full=False, weekly=False, show_progress=False, silent=False):
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

        timeframe = 'weekly' if weekly else 'daily'

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

        iter_range = range(len(close_prices) - 1, -1, -1)
        if show_progress:
            from tqdm import tqdm
            iter_range = tqdm(
                iter_range,
                desc=f"{self.symbol} {timeframe} indicators",
                total=len(close_prices),
                leave=False,
            )
        for i in iter_range:
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
        if not silent:
            print(Fore.GREEN + f'{timeframe.capitalize()} indicators calculated for {self.symbol}')

    def calculate_scores(self, cutoff_date=None, full=False, weekly=False, silent=False):
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
                # EMA distances and BB position — stored for DTE recommendation in API
                try:
                    ind = Indicator.get_or_none(Indicator.symbol == self.symbol, Indicator.date == indicator.date)
                    if ind and ind.ema_50 and score.price:
                        price = float(score.price)
                        ema_50 = float(ind.ema_50)
                        score.pct_from_ema50 = round((price - ema_50) / ema_50 * 100, 2)
                        if ind.ema_200:
                            ema_200 = float(ind.ema_200)
                            score.pct_from_ema200 = round((price - ema_200) / ema_200 * 100, 2)
                        if ind.upper_band and ind.lower_band:
                            upper, lower = float(ind.upper_band), float(ind.lower_band)
                            band_range = upper - lower
                            if band_range > 0:
                                score.bb_position = round(max(0.0, min(1.0, (price - lower) / band_range)), 3)
                except Exception:
                    pass
                # Score velocity: delta vs ~7 trading days ago
                try:
                    prior = (Score.select(Score.overall)
                             .where(Score.symbol == self.symbol, Score.date < indicator.date,
                                    Score.version == AlgorithmVersion.get_or_create_current())
                             .order_by(Score.date.desc())
                             .offset(6)
                             .first())
                    if prior and prior.overall is not None and score.overall is not None:
                        score.score_velocity_7d = score.overall - prior.overall
                except Exception:
                    pass
                score.save()
            
            if not created:
                if not full and (not cutoff_date or indicator.date < cutoff_date):
                    break
        
        timeframe = 'Weekly' if weekly else 'Daily'
        if not silent:
            print(Fore.GREEN + f'{timeframe} scores calculated for {self.symbol}')

    def calculate_scores_batched(self, silent=False):
        """
        Batch (full) replacement for calculate_scores(full=True, weekly=False).

        Replaces ~15 per-row DB round-trips with a handful of bulk fetches:
        - 4 bulk selects upfront (Indicator, PriceHistory, WeeklyScore, EarningsDate)
        - Volume signals forward-walked in ascending date order (decay needs this)
        - EMA/BB distances read directly from the pre-fetched indicator row
          (no redundant second Indicator.get_or_none per row)
        - Score velocity tracked via an in-memory deque (no Score offset query)

        For full=False (daily update, 1-2 new rows), the existing calculate_scores()
        is already fast — this method is only worth calling when full=True.
        """
        from database.models.technical import Indicator, PriceHistory
        from collections import deque
        from volume_amplifier import get_volume_multiplier_from_cache

        version = AlgorithmVersion.get_or_create_current()

        # ── 1. Bulk data load ───────────────────────────────────────────────
        ind_rows = list(
            Indicator.select()
            .where(Indicator.symbol == self.symbol)
            .order_by(Indicator.date.asc())
        )
        if not ind_rows:
            return

        ph_rows_asc = list(
            PriceHistory.select()
            .where(PriceHistory.symbol == self.symbol)
            .order_by(PriceHistory.date.asc())
        )
        ph_map = {p.date: p for p in ph_rows_asc}
        ph_rows_desc = list(reversed(ph_rows_asc))
        ph_dates_asc = [p.date for p in ph_rows_asc]

        ws_map = {
            ws.date: ws
            for ws in WeeklyScore.select()
            .where(WeeklyScore.symbol == self.symbol)
            .order_by(WeeklyScore.date.asc())
        }

        earnings_set = set(
            e.date for e in EarningsDate.select(EarningsDate.date)
            .where(EarningsDate.symbol == self.symbol)
        )

        ind_dates_asc = [i.date for i in ind_rows]

        # Prev-close map for daily_change
        prev_close_map = {}
        for i, p in enumerate(ph_rows_asc):
            prev_close_map[p.date] = ph_rows_asc[i - 1].close if i > 0 else p.close

        # Latest price (used for score.price on all rows, matching existing behaviour)
        latest_price = ph_rows_asc[-1].close if ph_rows_asc else None

        # ── 2. Forward-walk in ascending date order ─────────────────────────
        prior_vol_signals = {}   # date → (sig_type, magnitude, vol_raw)
        recent_overalls   = deque(maxlen=10)  # tracks last N overalls for velocity

        def _week_start(d):
            return d - timedelta(days=d.weekday())

        for ind in ind_rows:
            d = ind.date
            try:
                score, _ = Score.build(self.symbol, d)

                # ind_cache for this date — O(log n) bisect + O(k) slice
                cutoff_idx = bisect.bisect_right(ind_dates_asc, d)
                ind_cache  = list(reversed(ind_rows[:cutoff_idx]))

                # ── Component scores ────────────────────────────────────────
                trend = self.calculate_trend_score(
                    d, _ind_cache=ind_cache, _ph_cache=ph_map)
                score.trend = trend
                score.rsi   = self.calculate_rsi_score(
                    d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ph_map)
                score.macd  = self.calculate_macd_score(
                    d, _ind_cache=ind_cache, _ph_cache=ph_map)
                score.bb    = self.calculate_bollinger_bands_score(
                    d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ph_map)
                score.stoch = self.calculate_stoch_score(
                    d, _ind_cache=ind_cache, _ph_cache=ph_map)

                ph = ph_map.get(d)
                cur  = float(ph.close) if ph else None
                prev = float(prev_close_map.get(d, cur or 0))
                score.daily_change = (
                    round(((cur - prev) / prev) * 100, 2) if cur and prev and prev != 0 else 0
                )
                score.price = cur or 0
                score.next_earnings        = score.calculate_next_earnings_days()
                score.price_target         = score.calculate_price_target()
                score.price_target_growth  = score.calculate_price_target_growth()

                if None in (score.bb, score.trend, score.rsi, score.macd, score.stoch):
                    score.delete_instance()
                    continue

                score.technical_alignment = score.calculate_technical_alignment_score()

                # ── BB position ─────────────────────────────────────────────
                bb_pct = None
                if None not in (ind.upper_band, ind.lower_band) and ph:
                    bw = float(ind.upper_band) - float(ind.lower_band)
                    if bw > 0:
                        bb_pct = (float(ph.close) - float(ind.lower_band)) / bw

                # ── Weekly context ───────────────────────────────────────────
                ws  = ws_map.get(_week_start(d))
                pws = ws_map.get(_week_start(d) - timedelta(weeks=1))
                ws_rsi  = ws.rsi  if ws and ws.rsi and ws.macd else None
                ws_macd = ws.macd if ws and ws.rsi and ws.macd else None

                # ── Volume signal (cache, forward-walk) ─────────────────────
                ph_idx = bisect.bisect_right(ph_dates_asc, d)
                ph_slice_desc = ph_rows_desc[len(ph_rows_asc) - ph_idx:]
                vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = \
                    get_volume_multiplier_from_cache(
                        d, ph_slice_desc, prior_vol_signals, earnings_set)

                # ── Overall score ────────────────────────────────────────────
                overall, weight_info, vol_update = compute_overall_score(
                    score.trend, score.bb, score.rsi, score.macd,
                    score.stoch, score.technical_alignment,
                    bb_pct=bb_pct,
                    ws_trend=ws.trend if ws else None,
                    ws_rsi=ws_rsi, ws_macd=ws_macd,
                    prev_ws_trend=pws.trend if pws else None,
                    prev_ws_rsi=pws.rsi   if pws else None,
                    prev_ws_macd=pws.macd if pws else None,
                    vol_mult=vol_mult, vol_raw=vol_raw,
                    vol_sig=vol_sig,   vol_mag=vol_mag,
                    blend_w=blend_w,   vol_target=vol_target,
                )

                if overall is None:
                    score.delete_instance()
                    continue

                score.overall     = overall
                score.weight_info = json.dumps(weight_info)

                if vol_update and (vol_update['volume_signal'] != 'NEUTRAL'
                                   or not score.volume_signal
                                   or score.volume_signal == 'NEUTRAL'):
                    score.volume           = vol_update['volume']
                    score.volume_signal    = vol_update['volume_signal']
                    score.volume_magnitude = vol_update['volume_magnitude']

                prior_vol_signals[d] = (
                    score.volume_signal or 'NEUTRAL',
                    score.volume_magnitude or 0.0,
                    score.volume or 50,
                )

                # ── EMA distances — read directly from ind, no extra query ──
                try:
                    if ind.ema_50 and score.price:
                        price = float(score.price)
                        score.pct_from_ema50 = round(
                            (price - float(ind.ema_50)) / float(ind.ema_50) * 100, 2)
                        if ind.ema_200:
                            score.pct_from_ema200 = round(
                                (price - float(ind.ema_200)) / float(ind.ema_200) * 100, 2)
                        if ind.upper_band and ind.lower_band:
                            upper, lower = float(ind.upper_band), float(ind.lower_band)
                            br = upper - lower
                            if br > 0:
                                score.bb_position = round(
                                    max(0.0, min(1.0, (price - lower) / br)), 3)
                except Exception:
                    pass

                # ── Score velocity — from deque, no Score offset query ───────
                try:
                    if len(recent_overalls) >= 7 and score.overall is not None:
                        oldest = recent_overalls[0]
                        if oldest is not None:
                            score.score_velocity_7d = score.overall - oldest
                except Exception:
                    pass

                recent_overalls.append(score.overall)
                score.updated_at = datetime.now()
                score.save()

            except Exception as e:
                print(Fore.RED + f"  {self.symbol} {d}: {e}")

        if not silent:
            print(Fore.GREEN + f'Daily scores calculated for {self.symbol} (batched)')

    def recalculate_weekly_scores(self, cutoff, persist_lo=None, persist_hi_exclusive=None,
                                  show_progress=True):
        scores = list(
            WeeklyScore.select()
            .where((WeeklyScore.symbol == self.symbol) & (WeeklyScore.date >= cutoff))
            .order_by(WeeklyScore.date.asc())
        )
        from tqdm import tqdm
        persist_lo_eff = persist_lo if persist_lo is not None else cutoff
        updated, errors = 0, 0
        it = tqdm(scores, desc=f"{self.symbol} weekly", leave=False) if show_progress else scores
        for ws in it:
            try:
                trend = self.calculate_trend_score(ws.date, weekly=True)
                if trend is not None: ws.trend = trend
                rsi = self.calculate_rsi_score(ws.date, trend_score=ws.trend, weekly=True)
                if rsi is not None: ws.rsi = rsi
                macd = self.calculate_macd_score(ws.date, weekly=True)
                if macd is not None: ws.macd = macd
                if ws.rsi is not None and ws.macd is not None:
                    ws.composite = ws.calculate_composite_score()
                if ws.date < persist_lo_eff or (
                    persist_hi_exclusive is not None and ws.date >= persist_hi_exclusive
                ):
                    continue
                ws.save()
                updated += 1
            except Exception as e:
                errors += 1
                print(Fore.RED + f"  {self.symbol} weekly {ws.date}: {e}")
        return updated, errors

    def recalculate_scores(self, cutoff, components, persist_lo=None, persist_hi_exclusive=None,
                           show_progress=True):
        from tqdm import tqdm
        version = AlgorithmVersion.get_active_scores_version()
        scores = list(
            Score.select()
            .where((Score.symbol == self.symbol) & (Score.date >= cutoff) & (Score.version == version))
            .order_by(Score.date.asc())
        )
        label = '+'.join(sorted(components))
        updated, skipped, errors = 0, 0, 0
        persist_lo_eff = persist_lo if persist_lo is not None else cutoff
        it = tqdm(scores, desc=f"{self.symbol} {label}", leave=False) if show_progress else scores
        for score in it:
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
                in_win = score.date >= persist_lo_eff and (
                    persist_hi_exclusive is None or score.date < persist_hi_exclusive
                )
                if changed and in_win:
                    score.updated_at = datetime.now()
                    score.save()
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                print(Fore.RED + f"  {self.symbol} {score.date}: {e}")
        return updated, skipped, errors

    def backfill_scores(self, cutoff=None, fill_lo=None, fill_hi_exclusive=None, show_progress=True):
        """Fill Score rows where indicators exist but the active version has no row.

        fill_lo / fill_hi_exclusive: optional inclusive/exclusive bounds on *which* missing dates
        to insert this call (staged backfill). When both None, every missing date ≥ cutoff is filled.

        Uses recalculate_scores_batched (indicator/price caches, volume forward-walk) and only persists
        dates in the missing set; other dates in range are recomputed in-memory for correct decay.
        """
        from database.models.technical import Indicator, PriceHistory, WeeklyIndicator, WeeklyPriceHistory
        version = AlgorithmVersion.get_active_scores_version()

        ph_q = PriceHistory.select(PriceHistory.date).where(PriceHistory.symbol == self.symbol)
        ind_q = Indicator.select(Indicator.date).where(Indicator.symbol == self.symbol)
        if cutoff:
            ph_q = ph_q.where(PriceHistory.date >= cutoff)
            ind_q = ind_q.where(Indicator.date >= cutoff)
        ph_dates_in_range = set(p.date for p in ph_q)
        ind_dates_in_range = set(i.date for i in ind_q)
        if ph_dates_in_range - ind_dates_in_range:
            self.calculate_indicators(full=True, show_progress=show_progress)

        wph_q = WeeklyPriceHistory.select(WeeklyPriceHistory.date).where(WeeklyPriceHistory.symbol == self.symbol)
        wind_q = WeeklyIndicator.select(WeeklyIndicator.date).where(WeeklyIndicator.symbol == self.symbol)
        if cutoff:
            wph_q = wph_q.where(WeeklyPriceHistory.date >= cutoff)
            wind_q = wind_q.where(WeeklyIndicator.date >= cutoff)
        wph_dates_in_range = set(p.date for p in wph_q)
        wind_dates_in_range = set(i.date for i in wind_q)
        if wph_dates_in_range - wind_dates_in_range:
            self.calculate_indicators(full=True, weekly=True, show_progress=show_progress)

        ind_query = Indicator.select(Indicator.date).where(Indicator.symbol == self.symbol)
        if cutoff:
            ind_query = ind_query.where(Indicator.date >= cutoff)
        indicator_dates = set(i.date for i in ind_query)

        score_query = Score.select(Score.date).where(Score.symbol == self.symbol, Score.version == version)
        if cutoff:
            score_query = score_query.where(Score.date >= cutoff)
        scored_dates = set(s.date for s in score_query)

        missing = sorted(indicator_dates - scored_dates)
        if fill_lo is not None or fill_hi_exclusive is not None:
            lo_eff = fill_lo if fill_lo is not None else cutoff
            missing = [
                d for d in missing
                if (lo_eff is None or d >= lo_eff)
                and (fill_hi_exclusive is None or d < fill_hi_exclusive)
            ]
        if not missing:
            return 0, 0

        for d in missing:
            Score.build(self.symbol, d)
        ver = AlgorithmVersion.get_or_create_current()

        bf_components = {'trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'overall'}
        updated, _skipped, errors = self.recalculate_scores_batched(
            cutoff,
            bf_components,
            show_progress=show_progress,
            save_only_dates=set(missing),
            score_version=ver,
        )
        return updated, errors

    # ── Batch recalculation (no per-row DB queries) ───────────────────────────

    def recalculate_scores_batched(self, cutoff, components, cutoff_end=None, force=False):
        """
        Drop-in replacement for recalculate_scores() that replaces ~N×10 per-row
        DB queries with a handful of bulk fetches per stock.

        For --overall only: pre-loads Indicator, WeeklyScore, PriceHistory, and
        EarningsDate, then forward-walks dates computing volume signals in order
        (decay depends on prior computed signals).

        For component flags (trend/rsi/bb/macd/stoch/ta): delegates to the
        existing per-row calculators using pre-loaded indicator + price caches to
        eliminate the sliding-window and ind.price_history() round-trips.

        cutoff_end: upper bound for slice-based progressive passes.
        force: if True, recalculates all scores even if already computed for this
               version. If False, skips scores with overall already set (allows
               progressive passes to skip prior-pass work efficiently).
        """
        from collections import deque
        from database.models.technical import Indicator, PriceHistory
        from tqdm import tqdm

        version = score_version if score_version is not None else AlgorithmVersion.get_active_scores_version()
        persist_lo_eff = persist_lo if persist_lo is not None else cutoff

        # ── 1. Bulk-load all data ───────────────────────────────────────────
        score_q = (Score.select()
                   .where((Score.symbol == self.symbol) & (Score.date >= cutoff) &
                          (Score.version == version)))
        if cutoff_end is not None:
            score_q = score_q.where(Score.date <= cutoff_end)
        scores = list(score_q.order_by(Score.date.asc()))
        if not scores:
            return 0, 0, 0

        # Indicator map (date → Indicator row) — used for bb_pct in overall
        ind_rows = list(
            Indicator.select()
            .where(Indicator.symbol == self.symbol)
            .order_by(Indicator.date.asc())
        )
        ind_map = {i.date: i for i in ind_rows}

        # Price history — descending list for volume amplifier + ascending map
        ph_rows_asc = list(
            PriceHistory.select()
            .where(PriceHistory.symbol == self.symbol)
            .order_by(PriceHistory.date.asc())
        )
        ph_map = {p.date: p for p in ph_rows_asc}
        ph_rows_desc = list(reversed(ph_rows_asc))

        prev_close_map = {}
        if save_only_dates is not None:
            for i, p in enumerate(ph_rows_asc):
                prev_close_map[p.date] = ph_rows_asc[i - 1].close if i > 0 else p.close
        recent_overalls = deque(maxlen=10) if save_only_dates is not None else None

        # Weekly scores map: week_start → WeeklyScore
        ws_rows = list(
            WeeklyScore.select()
            .where(WeeklyScore.symbol == self.symbol)
            .order_by(WeeklyScore.date.asc())
        )
        ws_map = {ws.date: ws for ws in ws_rows}

        # Earnings dates set (all time — suppression window is ±1 day)
        earnings_set = set(
            e.date for e in EarningsDate.select(EarningsDate.date)
            .where(EarningsDate.symbol == self.symbol)
        )

        # ── 2. Seed prior_vol_signals from DB rows before this slice ───────
        # Gives correct decay context at slice boundaries (cheap: 10-row query).
        seed_rows = list(
            Score.select(Score.date, Score.volume_signal, Score.volume_magnitude, Score.volume)
            .where(
                (Score.symbol == self.symbol) &
                (Score.date < cutoff) &
                (Score.version == version) &
                Score.volume_signal.is_null(False)
            )
            .order_by(Score.date.desc())
            .limit(10)
        )
        prior_vol_signals = {}
        for s in reversed(seed_rows):
            if s.volume_signal:
                prior_vol_signals[s.date] = (
                    s.volume_signal,
                    float(s.volume_magnitude or 0),
                    float(s.volume or 50),
                )

        def _week_start(d):
            return d - timedelta(days=d.weekday())

        updated, skipped, errors = 0, 0, 0
        label = '+'.join(sorted(components))

        # Pre-compute ascending date list for O(log n) bisect slicing
        ind_dates_asc = [i.date for i in ind_rows]   # ind_rows is already asc

        it = tqdm(scores, desc=f"{self.symbol} {label} [batch]", leave=False) if show_progress else scores
        for score in it:
            try:
                # ── Version-aware skip ─────────────────────────────────────
                # If not forced and overall is already computed for this version,
                # seed the volume decay chain and skip (no recalculation needed).
                if not force and 'overall' in components and score.overall is not None:
                    prior_vol_signals[score.date] = (
                        score.volume_signal or 'NEUTRAL',
                        float(score.volume_magnitude or 0),
                        float(score.volume or 50),
                    )
                    skipped += 1
                    continue

                changed = False

                # ── Component recalculation (if requested) ─────────────────
                if components & {'trend', 'rsi', 'bb', 'macd', 'stoch', 'ta'}:
                    # Build ind_cache: sorted desc up to score.date (bisect = O(log n))
                    cutoff_idx = bisect.bisect_right(ind_dates_asc, score.date)
                    ind_cache = list(reversed(ind_rows[:cutoff_idx]))

                    if 'trend' in components:
                        val = self.calculate_trend_score(
                            score.date, _ind_cache=ind_cache, _ph_cache=ph_map)
                        if val is not None: score.trend = val; changed = True
                    if 'rsi' in components:
                        val = self.calculate_rsi_score(
                            score.date, trend_score=score.trend,
                            _ind_cache=ind_cache, _ph_cache=ph_map)
                        if val is not None: score.rsi = val; changed = True
                    if 'bb' in components:
                        val = self.calculate_bollinger_bands_score(
                            score.date, trend_score=score.trend,
                            _ind_cache=ind_cache, _ph_cache=ph_map)
                        if val is not None: score.bb = val; changed = True
                    if 'macd' in components:
                        val = self.calculate_macd_score(
                            score.date, _ind_cache=ind_cache, _ph_cache=ph_map)
                        if val is not None: score.macd = val; changed = True
                    if 'stoch' in components:
                        val = self.calculate_stoch_score(
                            score.date, _ind_cache=ind_cache, _ph_cache=ph_map)
                        if val is not None: score.stoch = val; changed = True
                    if 'ta' in components:
                        val = score.calculate_technical_alignment_score()
                        if val is not None: score.technical_alignment = val; changed = True

                # ── Overall recalculation ──────────────────────────────────
                if 'overall' in components or changed:
                    if None in (score.bb, score.trend, score.rsi,
                                score.macd, score.stoch, score.technical_alignment):
                        if save_only_dates and score.date in save_only_dates:
                            try:
                                score.delete_instance()
                            except Exception:
                                pass
                        skipped += 1
                        continue

                    # BB position from pre-loaded indicator
                    bb_pct = None
                    ind = ind_map.get(score.date)
                    if ind and None not in (ind.upper_band, ind.lower_band):
                        bw = float(ind.upper_band) - float(ind.lower_band)
                        if bw > 0:
                            ph = ph_map.get(score.date)
                            if ph:
                                bb_pct = (float(ph.close) - float(ind.lower_band)) / bw

                    # Weekly context
                    ws  = ws_map.get(_week_start(score.date))
                    pws = ws_map.get(_week_start(score.date) - timedelta(weeks=1))
                    ws_trend = ws.trend if ws else None
                    ws_rsi   = ws.rsi   if ws and ws.rsi and ws.macd else None
                    ws_macd  = ws.macd  if ws and ws.rsi and ws.macd else None

                    # Volume signal from cache (no DB)
                    from volume_amplifier import get_volume_multiplier_from_cache
                    ph_slice_desc = [p for p in ph_rows_desc if p.date <= score.date]
                    vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = \
                        get_volume_multiplier_from_cache(
                            score.date, ph_slice_desc, prior_vol_signals,
                            earnings_set, pre_volume_overall=None,
                        )

                    overall, weight_info, vol_update = compute_overall_score(
                        score.trend, score.bb, score.rsi, score.macd,
                        score.stoch, score.technical_alignment,
                        bb_pct=bb_pct,
                        ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
                        prev_ws_trend=pws.trend if pws else None,
                        prev_ws_rsi=pws.rsi   if pws else None,
                        prev_ws_macd=pws.macd if pws else None,
                        vol_mult=vol_mult, vol_raw=vol_raw,
                        vol_sig=vol_sig,   vol_mag=vol_mag,
                        blend_w=blend_w,   vol_target=vol_target,
                    )

                    if overall is None:
                        if save_only_dates and score.date in save_only_dates:
                            try:
                                score.delete_instance()
                            except Exception:
                                pass
                        skipped += 1
                        continue

                    score.overall = overall
                    score.weight_info = json.dumps(weight_info)
                    if vol_update and (vol_update['volume_signal'] != 'NEUTRAL'
                                       or not score.volume_signal
                                       or score.volume_signal == 'NEUTRAL'):
                        score.volume           = vol_update['volume']
                        score.volume_signal    = vol_update['volume_signal']
                        score.volume_magnitude = vol_update['volume_magnitude']

                    # Record this signal for downstream decay
                    prior_vol_signals[score.date] = (
                        score.volume_signal or 'NEUTRAL',
                        score.volume_magnitude or 0.0,
                        score.volume or 50,
                    )
                    changed = True

                    if recent_overalls is not None and score.overall is not None:
                        try:
                            if len(recent_overalls) >= 7:
                                oldest = recent_overalls[0]
                                if oldest is not None:
                                    score.score_velocity_7d = score.overall - oldest
                        except Exception:
                            pass
                        recent_overalls.append(score.overall)

                    if save_only_dates is not None and score.date in save_only_dates:
                        ph = ph_map.get(score.date)
                        cur = float(ph.close) if ph else None
                        prev = float(prev_close_map.get(score.date, cur or 0))
                        score.daily_change = (
                            round(((cur - prev) / prev) * 100, 2)
                            if cur and prev and prev != 0 else 0
                        )
                        score.price = cur or 0
                        score.name = self.name
                        score.next_earnings = score.calculate_next_earnings_days()
                        score.price_target = score.calculate_price_target()
                        score.price_target_growth = score.calculate_price_target_growth()
                        try:
                            ind_e = ind_map.get(score.date)
                            if ind_e and ind_e.ema_50 and score.price:
                                price = float(score.price)
                                score.pct_from_ema50 = round(
                                    (price - float(ind_e.ema_50)) / float(ind_e.ema_50) * 100, 2)
                                if ind_e.ema_200:
                                    ema_200 = float(ind_e.ema_200)
                                    score.pct_from_ema200 = round(
                                        (price - ema_200) / ema_200 * 100, 2)
                                if ind_e.upper_band and ind_e.lower_band:
                                    upper, lower = float(ind_e.upper_band), float(ind_e.lower_band)
                                    br = upper - lower
                                    if br > 0:
                                        score.bb_position = round(
                                            max(0.0, min(1.0, (price - lower) / br)), 3)
                        except Exception:
                            pass

                if save_only_dates is not None:
                    in_win = score.date in save_only_dates
                else:
                    lo_ok = True if persist_lo_eff is None else score.date >= persist_lo_eff
                    hi_ok = True if persist_hi_exclusive is None else score.date < persist_hi_exclusive
                    in_win = lo_ok and hi_ok
                if changed and in_win:
                    score.updated_at = datetime.now()
                    score.save()
                    updated += 1
                else:
                    skipped += 1

            except Exception as e:
                errors += 1
                print(Fore.RED + f"  {self.symbol} {score.date}: {e}")

        return updated, skipped, errors

    def recalculate_scores_full(self, cutoff_start, components, cutoff_end=None,
                                 force=False, use_batch=True):
        """
        Unified fill-gaps + recalculate for a date slice [cutoff_start, cutoff_end].

        Designed for progressive slice-based passes (recent data first). Each pass
        only processes its own non-overlapping date range, and skips dates that
        already have a current-version score (unless force=True).

        Steps:
          1. Ensure daily + weekly indicators exist for all price-history dates in slice.
          2. Create Score shells for indicator dates missing a current-version row.
          3. Run batched recalculate on the slice (skips already-computed rows when
             force=False, so progressive passes don't redo prior work).
        """
        from database.models.technical import Indicator, PriceHistory, WeeklyIndicator, WeeklyPriceHistory

        version = AlgorithmVersion.get_or_create_current()
        today = date.today()
        eff_end = cutoff_end if cutoff_end is not None else today

        # ── 1. Daily indicator gap check ────────────────────────────────────
        ph_dates = set(
            p.date for p in PriceHistory.select(PriceHistory.date)
            .where((PriceHistory.symbol == self.symbol) &
                   (PriceHistory.date >= cutoff_start) & (PriceHistory.date <= eff_end))
        )
        ind_dates = set(
            i.date for i in Indicator.select(Indicator.date)
            .where((Indicator.symbol == self.symbol) &
                   (Indicator.date >= cutoff_start) & (Indicator.date <= eff_end))
        )
        if ph_dates - ind_dates:
            self.calculate_indicators(full=True, show_progress=False)
            ind_dates = set(
                i.date for i in Indicator.select(Indicator.date)
                .where((Indicator.symbol == self.symbol) &
                       (Indicator.date >= cutoff_start) & (Indicator.date <= eff_end))
            )

        # ── 2. Weekly indicator gap check ───────────────────────────────────
        wph_dates = set(
            p.date for p in WeeklyPriceHistory.select(WeeklyPriceHistory.date)
            .where((WeeklyPriceHistory.symbol == self.symbol) &
                   (WeeklyPriceHistory.date >= cutoff_start) & (WeeklyPriceHistory.date <= eff_end))
        )
        wind_dates = set(
            i.date for i in WeeklyIndicator.select(WeeklyIndicator.date)
            .where((WeeklyIndicator.symbol == self.symbol) &
                   (WeeklyIndicator.date >= cutoff_start) & (WeeklyIndicator.date <= eff_end))
        )
        if wph_dates - wind_dates:
            self.calculate_indicators(full=True, weekly=True, show_progress=False)

        # ── 3. Create Score shells for dates without a current-version row ─────
        # When force=True every indicator date gets a shell (get_or_create is safe
        # on existing rows) so the batched pass can overwrite them all.
        if force:
            missing = sorted(ind_dates)
        else:
            scored_dates = set(
                s.date for s in Score.select(Score.date)
                .where((Score.symbol == self.symbol) & (Score.version == version) &
                       (Score.date >= cutoff_start) & (Score.date <= eff_end))
            )
            missing = sorted(ind_dates - scored_dates)
        if missing:
            ph_rows = list(
                PriceHistory.select(PriceHistory.date, PriceHistory.close)
                .where(PriceHistory.symbol == self.symbol)
                .order_by(PriceHistory.date.asc())
            )
            price_map = {p.date: float(p.close) for p in ph_rows}
            prev_close = {p.date: float(ph_rows[i - 1].close) if i > 0 else float(p.close)
                          for i, p in enumerate(ph_rows)}
            now = datetime.now()
            for d in missing:
                cur  = price_map.get(d)
                prev = prev_close.get(d)
                dc   = round(((cur - prev) / prev) * 100, 2) if cur and prev and prev != 0 else 0
                Score.get_or_create(
                    symbol=self.symbol, date=d, version=version,
                    defaults={'price': cur or 0, 'daily_change': dc,
                              'name': self.name, 'updated_at': now},
                )

        # ── 4. Batched recalculate for this slice ────────────────────────────
        if use_batch:
            return self.recalculate_scores_batched(cutoff_start, components,
                                                    cutoff_end=cutoff_end, force=force)
        return self.recalculate_scores(cutoff_start, components)

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
    
    def calculate_rsi_score(self, target_date, trend_score=None, lookback=30, weekly=False,
                            _ind_cache=None, _ph_cache=None):
        """
        RSI score (0-100). Smooth base + breakout-driven signal.
        Breakout from oversold/overbought is the primary driver, not raw RSI position.

        _ind_cache: pre-loaded indicator rows sorted DESC by date (≤ target_date).
        _ph_cache:  dict[date → PriceHistory-like] for fast price lookups.
        """
        from database.models.technical import Indicator, WeeklyIndicator
        import math
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        indicators_query = self.weekly_indicators if weekly else self.indicators

        if _ind_cache is not None:
            indicators = _ind_cache[:lookback + 5]
        else:
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
                ph = _ph_cache.get(ind.date) if _ph_cache is not None else ind.price_history()
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
    def calculate_stoch_score(self, target_date, momentum_lookback=3, weekly=False,
                              _ind_cache=None, _ph_cache=None):
        """Calculate Stochastic score (0-100) where 100 is best. Incorporates momentum when current or recent values were in extreme zones."""
        from database.models.technical import Indicator, WeeklyIndicator
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        if _ind_cache is not None:
            indicator = _ind_cache[0] if _ind_cache else None
        else:
            indicator = self.indicator_by_date(target_date, weekly=weekly)
        if not indicator or indicator.stoch is None:
            return None
        if _ind_cache is not None:
            stoch_values = [float(ind.stoch) for ind in _ind_cache[:momentum_lookback + 1] if ind.stoch is not None]
        else:
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

    def calculate_bollinger_bands_score(self, target_date, lookback=10, weekly=False, trend_score=None,
                                        _ind_cache=None, _ph_cache=None):
        """
        Trend-aware BB score (0-100) for swing trading.
        Uptrend: middle BB pullback = buy zone. Downtrend: middle BB bounce = sell zone.

        _ind_cache: pre-loaded indicator rows sorted DESC by date (≤ target_date).
        _ph_cache:  dict[date → PriceHistory-like] for fast price lookups.
        """
        from database.models.technical import Indicator, WeeklyIndicator
        IndicatorModel = WeeklyIndicator if weekly else Indicator
        if _ind_cache is not None:
            indicators = _ind_cache[:lookback + 1]
        else:
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
        ph = (_ph_cache.get(current.date) if _ph_cache is not None
              else current.price_history())
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
                p = (_ph_cache.get(ind.date) if _ph_cache is not None
                     else ind.price_history())
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
            p = (_ph_cache.get(ind.date) if _ph_cache is not None
                 else ind.price_history())
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
    
    def calculate_macd_score(self, target_date, lookback_months=9, debug=False, weekly=False,
                             _ind_cache=None, _ph_cache=None):
        """
        MACD score (0-100) based on histogram dynamics: velocity, momentum phase,
        and contrarian histogram position. Front-runs the histogram peak.

        _ind_cache: pre-loaded indicator rows sorted DESC by date (≤ target_date).
        """
        from database.models.technical import Indicator, WeeklyIndicator
        from datetime import timedelta
        IndicatorModel = WeeklyIndicator if weekly else Indicator

        lookback_days = int(lookback_months * 30.4)
        start_date = target_date - timedelta(
            weeks=lookback_days if weekly else 0,
            days=0 if weekly else lookback_days
        )

        if _ind_cache is not None:
            rows = [i for i in _ind_cache
                    if i.date >= start_date
                    and i.macd is not None and i.macd_signal is not None]
        else:
            indicators_query = self.weekly_indicators if weekly else self.indicators
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
    
    def calculate_trend_score(self, target_date, lookback=20, weekly=False,
                              _ind_cache=None, _ph_cache=None):
        """
        Swing trading trend score (0-100) blending short-term EMAs with macro structure.
        Returns: 0 = strong downtrend, 50 = neutral, 100 = strong uptrend

        _ind_cache: pre-loaded indicator rows sorted DESC by date (≤ target_date).
        _ph_cache:  dict[date → PriceHistory-like] for fast price lookups.
        """
        from database.models.technical import Indicator, WeeklyIndicator
        IndicatorModel = WeeklyIndicator if weekly else Indicator

        if _ind_cache is not None:
            recent_indicators = _ind_cache[:lookback + 1]
        else:
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

        _ph = (_ph_cache.get(current.date) if _ph_cache is not None
               else current.price_history())
        if _ph is None:
            return None
        current_price = float(_ph.close)
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

