from trader_database import BaseModel
from database.utils.scoring import (
    normalize_score, calculate_theta_factor, calculate_option_return,
    calculate_weekly_composite, calculate_weekly_adjustment,
    compute_overall_score, EARN_BOOST_ENABLED, EARN_BOOST_WINDOW,
)
import math, json, bisect, re

_LEGAL_SUFFIX_RE = re.compile(
    # Require at least one comma or whitespace before suffix — prevents matching
    # suffix letters that appear inside words (e.g. "se" in "Chase").
    r'[,\s]\s*(?:'
    r'Co\.,?\s*Ltd\.?'          # "Co., Ltd." — Japanese style, must precede bare "Co."
    r'|Holdings?\b'             # Holdings / Holding
    r'|N\.V\.'                  # Dutch NV
    r'|p\.l\.c\.'               # British plc (dotted)
    r'|PLC\b|Plc\b'             # British plc (undotted)
    r'|A/S\b'                   # Danish A/S
    r'|SE\b'                    # European SE
    r'|AG\b'                    # German/Swiss AG
    r'|GmbH\b'                  # German GmbH
    r'|S\.A\.?'                 # French/Spanish SA
    r'|Inc\.?'                  # Inc / Inc.
    r'|Corp(?:oration)?\.?'     # Corp / Corporation
    r'|Limited\b'               # Limited
    r'|Ltd\.?'                  # Ltd / Ltd.
    r'|LLC\b|L\.L\.C\.'        # LLC
    r'|L\.P\.'                  # LP
    r'|&\s*Co\.?'               # & Co / & Co.
    r'|Co\.\b'                  # bare Co.
    r'|Company\b'               # Company
    r'|Group\b'                 # Group
    r'|plc\b'                   # lowercase plc
    r').*$',
    re.IGNORECASE,
)

def _clean_name(name: str) -> str:
    """Return a display-friendly company name from a yfinance longName.

    Strips exchange qualifiers (e.g. ' - New York Registered Shares') and
    common legal entity suffixes (Inc., Corp., N.V., Holdings, Group, etc.),
    keeping only the core brand.
    """
    if not name:
        return name
    # Strip exchange / share-class qualifiers: " - anything"
    name = re.sub(r'\s+-\s+.*$', '', name)
    # Strip legal suffixes (apply twice to catch chains like "Holding N.V.")
    for _ in range(2):
        cleaned = _LEGAL_SUFFIX_RE.sub('', name).strip(' ,')
        if cleaned == name:
            break
        name = cleaned
    # Strip trailing conjunctions left by "and Company" → "and" patterns
    name = re.sub(r'\s+and\s*$', '', name, flags=re.IGNORECASE).strip(' ,')
    return name
from peewee import fn, CharField, IntegerField, FloatField, DateTimeField, DateField, CompositeKey, DeferredForeignKey, DecimalField, BooleanField, TextField, AutoField, ForeignKeyField
from datetime import datetime, date, timedelta
from colorama import Fore
import pandas as pd
import numpy as np
import talib

class ExchangeRate(BaseModel):
    """Cached daily exchange rates fetched during trader update."""
    date = DateField()
    from_currency = CharField(max_length=8)
    to_currency = CharField(max_length=8)
    rate = DecimalField(max_digits=12, decimal_places=6)
    fetched_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'exchange_rates'
        indexes = ((('date', 'from_currency', 'to_currency'), True),)

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)

    @classmethod
    def get_latest(cls, from_currency='CAD', to_currency='USD'):
        """Return the most recent cached rate, or None if table is empty."""
        try:
            row = (cls
                   .select()
                   .where(cls.from_currency == from_currency, cls.to_currency == to_currency)
                   .order_by(cls.date.desc())
                   .get())
            return float(row.rate)
        except cls.DoesNotExist:
            return None

    @classmethod
    def store(cls, rate, from_currency='CAD', to_currency='USD', for_date=None):
        if for_date is None:
            for_date = date.today()
        row, created = cls.get_or_create(
            date=for_date,
            from_currency=from_currency,
            to_currency=to_currency,
        )
        row.rate = rate
        row.fetched_at = datetime.now()
        row.save()


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
    
    def calculate_overall_score(self, regime_multiplier=None):
        """Thin wrapper — fetches DB data then delegates to pure compute_overall_score().

        regime_multiplier: if provided, used directly. If None, looked up from
        the MarketRegime table for this score's date (NULL in DB → 1.0).
        """
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

        # Regime multiplier — look up from MarketRegime if not provided
        if regime_multiplier is None:
            regime_row = MarketRegime.get_or_none(MarketRegime.date == self.date)
            if regime_row and regime_row.regime_multiplier is not None:
                regime_multiplier = float(regime_row.regime_multiplier)
                self.regime_composite = float(regime_row.regime_composite) if regime_row.regime_composite is not None else None
                self.regime_multiplier = regime_multiplier
            else:
                regime_multiplier = None  # compute_overall_score treats None as 1.0
        else:
            self.regime_multiplier = regime_multiplier

        # Earnings boost: days to next earnings within boost window
        d_to_ern = _days_to_next_earnings_for(self.symbol, self.date)

        overall, weight_info, vol_update = compute_overall_score(
            self.trend, self.bb, self.rsi, self.macd, self.stoch, self.technical_alignment,
            bb_pct=bb_pct,
            ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
            prev_ws_trend=pws.trend if pws else None,
            prev_ws_rsi=pws.rsi   if pws else None,
            prev_ws_macd=pws.macd if pws else None,
            vol_mult=vol_mult, vol_raw=vol_raw, vol_sig=vol_sig, vol_mag=vol_mag,
            blend_w=blend_w, vol_target=vol_target,
            macdh_raw=float(ind.macd_hist) if ind and ind.macd_hist is not None else None,
            regime_multiplier=regime_multiplier,
            days_to_earnings=d_to_ern,
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


class DteRecommendation(BaseModel):
    """Persisted output of dte_recommendation.recommend_dte().

    Keyed by (symbol, date, version) — same convention as Score so a new
    algorithm version writes its own DTE history without overwriting prior runs.
    """
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='dte_recommendations')
    date = DateField()
    version = DeferredForeignKey('AlgorithmVersion', backref='dte_rows')

    thesis = CharField()
    dte_min = IntegerField()
    dte_max = IntegerField()
    dte_target = IntegerField()
    confidence = CharField()

    tradeable = BooleanField(default=False)
    filter_side = CharField(null=True)        # 'long' | 'short' | None
    filter_reason = CharField(null=True)
    rationale = TextField(null=True)

    # Snapshot of inputs (for audit / display without recompute)
    score = IntegerField(null=True)
    volume_signal = CharField(null=True)
    volume_magnitude = FloatField(null=True)
    pct_from_ema50 = FloatField(null=True)
    pct_from_ema200 = FloatField(null=True)
    bb_position = FloatField(null=True)
    score_velocity_7d = IntegerField(null=True)

    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'dte_recommendations'
        primary_key = CompositeKey('symbol', 'date', 'version')
        indexes = (
            (('date',), False),
            (('symbol', 'date'), False),
        )

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)

    @classmethod
    def upsert_from_recommendation(cls, symbol, target_date, rec):
        """Persist a recommend_dte() result dict for (symbol, date, current version)."""
        version = AlgorithmVersion.get_or_create_current()
        inputs = rec.get('inputs') or {}
        defaults = {
            'thesis':           rec['thesis'],
            'dte_min':          rec['dte_min'],
            'dte_max':          rec['dte_max'],
            'dte_target':       rec['dte_target'],
            'confidence':       rec['confidence'],
            'tradeable':        bool(rec.get('tradeable')),
            'filter_side':      rec.get('filter_side'),
            'filter_reason':    rec.get('filter_reason'),
            'rationale':        rec.get('rationale'),
            'score':            inputs.get('score'),
            'volume_signal':    inputs.get('volume_signal'),
            'volume_magnitude': inputs.get('volume_magnitude'),
            'pct_from_ema50':   inputs.get('pct_from_ema50'),
            'pct_from_ema200':  inputs.get('pct_from_ema200'),
            'bb_position':      inputs.get('bb_position'),
            'score_velocity_7d': inputs.get('score_velocity_7d'),
            'updated_at':       datetime.now(),
        }
        row, created = cls.get_or_create(
            symbol=symbol, date=target_date, version=version, defaults=defaults
        )
        if not created:
            for k, v in defaults.items():
                setattr(row, k, v)
            row.save()
        return row, created

    def to_api_dict(self):
        return {
            'date': self.date.isoformat(),
            'thesis': self.thesis,
            'dte_min': self.dte_min,
            'dte_max': self.dte_max,
            'dte_target': self.dte_target,
            'confidence': self.confidence,
            'tradeable': bool(self.tradeable),
            'filter_side': self.filter_side,
            'filter_reason': self.filter_reason,
            'rationale': self.rationale,
            'inputs': {
                'score': self.score,
                'volume_signal': self.volume_signal,
                'volume_magnitude': self.volume_magnitude,
                'pct_from_ema50': self.pct_from_ema50,
                'pct_from_ema200': self.pct_from_ema200,
                'bb_position': self.bb_position,
                'score_velocity_7d': self.score_velocity_7d,
            },
        }


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
                version_file = root / "ALGORITHM_VERSION"
                if version_file.exists():
                    commit = version_file.read_text().strip()
                elif not (root / ".git").exists():
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
        """Version used for persisted Score rows in the API.

        Prefers the row matching the ``ALGORITHM_VERSION`` file pointer so
        ``trader revert`` flips the API's active version without rebuilding
        scores. Falls back to the highest-id row with a non-empty
        ``git_message``, then to the newest row.
        """
        from database.project_root import get_trader_project_root
        try:
            version_file = get_trader_project_root() / "ALGORITHM_VERSION"
            if version_file.exists():
                commit = version_file.read_text().strip()
                if commit:
                    pinned = cls.get_or_none(cls.git_commit == commit)
                    if pinned is not None:
                        return pinned
        except Exception:
            pass

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

    # ── Fear & Greed proxy signals (stored for A/B testing; not yet production) ──
    # Raw prices / metrics
    hyg_close       = DecimalField(max_digits=8, decimal_places=4, null=True)  # HYG ETF close
    lqd_close       = DecimalField(max_digits=8, decimal_places=4, null=True)  # LQD ETF close
    tlt_close       = DecimalField(max_digits=8, decimal_places=4, null=True)  # TLT ETF close
    skew_close      = DecimalField(max_digits=8, decimal_places=2, null=True)  # CBOE SKEW index
    hyg_lqd_ret_diff = DecimalField(max_digits=8, decimal_places=4, null=True) # HYG 20d ret - LQD 20d ret (neg = fear)
    tlt_spy_ret_diff = DecimalField(max_digits=8, decimal_places=4, null=True) # TLT 20d ret - SPY 20d ret (pos = fear)
    # Derived 0-100 fear scores (higher = more fear = amplify signals)
    credit_spread_score = DecimalField(max_digits=5, decimal_places=2, null=True)  # from HYG/LQD spread
    haven_score         = DecimalField(max_digits=5, decimal_places=2, null=True)  # from TLT/SPY return diff
    skew_score          = DecimalField(max_digits=5, decimal_places=2, null=True)  # from SKEW z-score
    # F&G composite + multiplier (VIX 40% + breadth 30% + credit 15% + haven 10% + skew 5%)
    fg_composite   = DecimalField(max_digits=5, decimal_places=2, null=True)
    fg_multiplier  = DecimalField(max_digits=5, decimal_places=4, null=True)

    class Meta:
        table_name = 'market_regime'

    # Column definitions for ensure_schema migration
    _FG_COLUMNS = [
        ('hyg_close',            'DECIMAL(8,4)'),
        ('lqd_close',            'DECIMAL(8,4)'),
        ('tlt_close',            'DECIMAL(8,4)'),
        ('skew_close',           'DECIMAL(8,2)'),
        ('hyg_lqd_ret_diff',     'DECIMAL(8,4)'),
        ('tlt_spy_ret_diff',     'DECIMAL(8,4)'),
        ('credit_spread_score',  'DECIMAL(5,2)'),
        ('haven_score',          'DECIMAL(5,2)'),
        ('skew_score',           'DECIMAL(5,2)'),
        ('fg_composite',         'DECIMAL(5,2)'),
        ('fg_multiplier',        'DECIMAL(5,4)'),
    ]

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)
        # Add F&G columns if they don't exist (safe ALTER TABLE)
        for col, col_type in cls._FG_COLUMNS:
            try:
                DB.execute_sql(f'ALTER TABLE market_regime ADD COLUMN {col} {col_type} NULL')
            except Exception:
                pass  # Column already exists

    @classmethod
    def for_date(cls, d):
        return cls.get_or_none(cls.date == d)

    @classmethod
    def latest_on_or_before(cls, d):
        """Return the most recent MarketRegime row with a non-null multiplier on or before date d."""
        return (cls.select()
                .where(cls.date <= d, cls.regime_multiplier.is_null(False))
                .order_by(cls.date.desc())
                .first())


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
    correlation_150d = FloatField(null=True)
    notes = TextField(null=True)
    git_commit = CharField(null=True)
    version = ForeignKeyField(AlgorithmVersion, backref='runs', null=True, on_delete='SET NULL')
    exit_rule_tp = FloatField(null=True, default=0.20)
    exit_rule_sl = FloatField(null=True, default=0.07)
    # DTE strategy variant: '30' (default — generic K=2.0σ barrier-touch) or '15' (15 DTE
    # option-aligned: K=0.903σ TP / M=0.774σ SL for calls, K=0.903σ / M=0.516σ for puts,
    # W=7 trading bars hard sell or W=15 cal days end hold). Added 2026-04-28 (Phase 16).
    dte_strategy = CharField(max_length=8, default='30')
    # Phase 17 (2026-04-29): metric variant. 'wr' = directional accuracy (generic
    # K=2.0/5.0 barriers, DTE-agnostic), 'tp' = option TP rate (option-aligned
    # barriers per DTE — Phase H5 for 30, Phase 15B C1 for 15). Each
    # (version, symbol, lookback_days, dte_strategy, metric) is a unique run.
    metric = CharField(max_length=4, default='wr')

    class Meta:
        table_name = 'score_assessment_runs'
        indexes = (
            (('version', 'run_at'), False),
            (('version', 'symbol', 'lookback_days', 'dte_strategy', 'metric'), True),
        )

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        try:
            DB.execute_sql("UPDATE score_assessment_runs SET symbol = '' WHERE symbol IS NULL")
        except Exception:
            pass
        # Add dte_strategy column FIRST so the legacy unique-key dedup uses it
        try:
            DB.execute_sql("ALTER TABLE score_assessment_runs ADD COLUMN dte_strategy VARCHAR(8) NOT NULL DEFAULT '30'")
        except Exception:
            pass
        try:
            DB.execute_sql("UPDATE score_assessment_runs SET dte_strategy='30' WHERE dte_strategy IS NULL OR dte_strategy=''")
        except Exception:
            pass
        # Phase 16 dedup — only runs cleanly when metric column doesn't yet exist
        # (pre-Phase-17 schema). Once metric is added below, this would unsafely
        # collapse (30,wr) ↔ (30,tp) rows. Wrapped in a column-existence check.
        try:
            cols = [r[0] for r in DB.execute_sql(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'score_assessment_runs'"
            ).fetchall()]
            if 'metric' not in cols:
                DB.execute_sql(
                    "DELETE t1 FROM score_assessment_runs t1 "
                    "INNER JOIN score_assessment_runs t2 "
                    "ON t1.version_id = t2.version_id AND t1.symbol = t2.symbol "
                    "AND t1.lookback_days = t2.lookback_days "
                    "AND t1.dte_strategy = t2.dte_strategy AND t1.id < t2.id"
                )
        except Exception:
            pass
        # Drop old unique index if present, then create the new composite one with dte_strategy
        try:
            DB.execute_sql("ALTER TABLE score_assessment_runs DROP INDEX uq_assess_v_sym_lb")
        except Exception:
            pass
        try:
            DB.execute_sql(
                "ALTER TABLE score_assessment_runs ADD UNIQUE INDEX uq_assess_v_sym_lb_dte "
                "(version_id, symbol, lookback_days, dte_strategy)"
            )
        except Exception:
            pass
        for col, typedef in [('exit_rule_tp', 'FLOAT NULL'), ('exit_rule_sl', 'FLOAT NULL')]:
            try:
                DB.execute_sql(f'ALTER TABLE score_assessment_runs ADD COLUMN {col} {typedef}')
            except Exception:
                pass
        # Phase 17 (2026-04-29): metric column ('wr' | 'tp'). Existing 30 DTE rows
        # are WR-style (generic 30dte barriers); existing 15 DTE rows are TP-style
        # (option-aligned 15dte_opt barriers — Phase 16 had no separate WR/TP split).
        try:
            DB.execute_sql("ALTER TABLE score_assessment_runs ADD COLUMN metric VARCHAR(4) NOT NULL DEFAULT 'wr'")
        except Exception:
            pass
        # Re-tag legacy 15 DTE rows from default 'wr' to actual 'tp' semantics
        try:
            DB.execute_sql("UPDATE score_assessment_runs SET metric='tp' WHERE dte_strategy='15' AND metric='wr'")
        except Exception:
            pass
        # Metric-aware dedup — drops rows that violate the (v, s, lb, dte, metric) uniqueness.
        try:
            DB.execute_sql(
                "DELETE t1 FROM score_assessment_runs t1 "
                "INNER JOIN score_assessment_runs t2 "
                "ON t1.version_id = t2.version_id AND t1.symbol = t2.symbol "
                "AND t1.lookback_days = t2.lookback_days "
                "AND t1.dte_strategy = t2.dte_strategy "
                "AND t1.metric = t2.metric "
                "AND t1.id < t2.id"
            )
        except Exception:
            pass
        # Replace dte_only unique index with the composite one including metric
        try:
            DB.execute_sql("ALTER TABLE score_assessment_runs DROP INDEX uq_assess_v_sym_lb_dte")
        except Exception:
            pass
        try:
            DB.execute_sql(
                "ALTER TABLE score_assessment_runs ADD UNIQUE INDEX uq_assess_v_sym_lb_dte_m "
                "(version_id, symbol, lookback_days, dte_strategy, metric)"
            )
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
    avg_mae_winner_1d = FloatField(null=True)
    avg_mae_winner_7d = FloatField(null=True)
    avg_mae_winner_15d = FloatField(null=True)
    avg_mae_winner_30d = FloatField(null=True)
    avg_mae_winner_60d = FloatField(null=True)
    avg_mae_winner_90d = FloatField(null=True)
    avg_mae_winner_150d = FloatField(null=True)
    avg_mae_loser_1d = FloatField(null=True)
    avg_mae_loser_7d = FloatField(null=True)
    avg_mae_loser_15d = FloatField(null=True)
    avg_mae_loser_30d = FloatField(null=True)
    avg_mae_loser_60d = FloatField(null=True)
    avg_mae_loser_90d = FloatField(null=True)
    avg_mae_loser_150d = FloatField(null=True)
    # Sigma-normalized winner/loser MAE (mae_pct / realized_vol_pct). Portable across stocks.
    avg_mae_winner_sigma_1d = FloatField(null=True)
    avg_mae_winner_sigma_7d = FloatField(null=True)
    avg_mae_winner_sigma_15d = FloatField(null=True)
    avg_mae_winner_sigma_30d = FloatField(null=True)
    avg_mae_winner_sigma_60d = FloatField(null=True)
    avg_mae_winner_sigma_90d = FloatField(null=True)
    avg_mae_winner_sigma_150d = FloatField(null=True)
    avg_mae_loser_sigma_1d = FloatField(null=True)
    avg_mae_loser_sigma_7d = FloatField(null=True)
    avg_mae_loser_sigma_15d = FloatField(null=True)
    avg_mae_loser_sigma_30d = FloatField(null=True)
    avg_mae_loser_sigma_60d = FloatField(null=True)
    avg_mae_loser_sigma_90d = FloatField(null=True)
    avg_mae_loser_sigma_150d = FloatField(null=True)
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
    # MFE percentiles — TP anchors: p25=75% of trades reach this, p75=25% reach this.
    mfe_p25_1d = FloatField(null=True)
    mfe_p25_7d = FloatField(null=True)
    mfe_p25_15d = FloatField(null=True)
    mfe_p25_30d = FloatField(null=True)
    mfe_p25_60d = FloatField(null=True)
    mfe_p25_90d = FloatField(null=True)
    mfe_p25_150d = FloatField(null=True)
    mfe_p75_1d = FloatField(null=True)
    mfe_p75_7d = FloatField(null=True)
    mfe_p75_15d = FloatField(null=True)
    mfe_p75_30d = FloatField(null=True)
    mfe_p75_60d = FloatField(null=True)
    mfe_p75_90d = FloatField(null=True)
    mfe_p75_150d = FloatField(null=True)
    mfe_p90_1d = FloatField(null=True)
    mfe_p90_7d = FloatField(null=True)
    mfe_p90_15d = FloatField(null=True)
    mfe_p90_30d = FloatField(null=True)
    mfe_p90_60d = FloatField(null=True)
    mfe_p90_90d = FloatField(null=True)
    mfe_p90_150d = FloatField(null=True)
    # MFE sigma-normalized — TP anchors in units of realized daily σ.
    # De-normalize at display time: mfe_sigma_pN_Wd * stock_σ = TP % for that stock.
    avg_mfe_sigma_1d = FloatField(null=True)
    avg_mfe_sigma_7d = FloatField(null=True)
    avg_mfe_sigma_15d = FloatField(null=True)
    avg_mfe_sigma_30d = FloatField(null=True)
    avg_mfe_sigma_60d = FloatField(null=True)
    avg_mfe_sigma_90d = FloatField(null=True)
    avg_mfe_sigma_150d = FloatField(null=True)
    median_mfe_sigma_1d = FloatField(null=True)
    median_mfe_sigma_7d = FloatField(null=True)
    median_mfe_sigma_15d = FloatField(null=True)
    median_mfe_sigma_30d = FloatField(null=True)
    median_mfe_sigma_60d = FloatField(null=True)
    median_mfe_sigma_90d = FloatField(null=True)
    median_mfe_sigma_150d = FloatField(null=True)
    mfe_sigma_p25_1d = FloatField(null=True)
    mfe_sigma_p25_7d = FloatField(null=True)
    mfe_sigma_p25_15d = FloatField(null=True)
    mfe_sigma_p25_30d = FloatField(null=True)
    mfe_sigma_p25_60d = FloatField(null=True)
    mfe_sigma_p25_90d = FloatField(null=True)
    mfe_sigma_p25_150d = FloatField(null=True)
    mfe_sigma_p75_1d = FloatField(null=True)
    mfe_sigma_p75_7d = FloatField(null=True)
    mfe_sigma_p75_15d = FloatField(null=True)
    mfe_sigma_p75_30d = FloatField(null=True)
    mfe_sigma_p75_60d = FloatField(null=True)
    mfe_sigma_p75_90d = FloatField(null=True)
    mfe_sigma_p75_150d = FloatField(null=True)
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

    # Swing/barrier metrics — vol-adjusted path-dependent exits matching entry_filter.py.
    # win_rate_{p} stores p_win (target hit before stop within W=p trading days).
    # avg_return_{p} stores side-adjusted EV (positive = trade direction worked).
    # avg_mae/mfe_{p} store side-adjusted excursions (units: % of entry price).
    # The columns below capture the residual barrier outcomes.
    swing_p_stop_1d = FloatField(null=True)
    swing_p_stop_7d = FloatField(null=True)
    swing_p_stop_15d = FloatField(null=True)
    swing_p_stop_30d = FloatField(null=True)
    swing_p_stop_60d = FloatField(null=True)
    swing_p_stop_90d = FloatField(null=True)
    swing_p_stop_150d = FloatField(null=True)
    swing_p_expire_1d = FloatField(null=True)
    swing_p_expire_7d = FloatField(null=True)
    swing_p_expire_15d = FloatField(null=True)
    swing_p_expire_30d = FloatField(null=True)
    swing_p_expire_60d = FloatField(null=True)
    swing_p_expire_90d = FloatField(null=True)
    swing_p_expire_150d = FloatField(null=True)
    swing_avg_win_pnl_1d = FloatField(null=True)
    swing_avg_win_pnl_7d = FloatField(null=True)
    swing_avg_win_pnl_15d = FloatField(null=True)
    swing_avg_win_pnl_30d = FloatField(null=True)
    swing_avg_win_pnl_60d = FloatField(null=True)
    swing_avg_win_pnl_90d = FloatField(null=True)
    swing_avg_win_pnl_150d = FloatField(null=True)
    swing_avg_stop_pnl_1d = FloatField(null=True)
    swing_avg_stop_pnl_7d = FloatField(null=True)
    swing_avg_stop_pnl_15d = FloatField(null=True)
    swing_avg_stop_pnl_30d = FloatField(null=True)
    swing_avg_stop_pnl_60d = FloatField(null=True)
    swing_avg_stop_pnl_90d = FloatField(null=True)
    swing_avg_stop_pnl_150d = FloatField(null=True)
    # 150d columns for all standard metrics
    avg_return_150d = FloatField(null=True)
    win_rate_150d = FloatField(null=True)
    median_return_150d = FloatField(null=True)
    avg_peak_150d = FloatField(null=True)
    median_peak_150d = FloatField(null=True)
    avg_mae_150d = FloatField(null=True)
    median_mae_150d = FloatField(null=True)
    avg_mfe_150d = FloatField(null=True)
    median_mfe_150d = FloatField(null=True)
    capture_ratio_150d = FloatField(null=True)
    # Unscaled win rates — fixed K/M barriers (no sqrt scaling), same W window
    win_rate_unscaled_1d  = FloatField(null=True)
    win_rate_unscaled_7d  = FloatField(null=True)
    win_rate_unscaled_15d = FloatField(null=True)
    win_rate_unscaled_30d = FloatField(null=True)
    win_rate_unscaled_60d = FloatField(null=True)
    win_rate_unscaled_90d = FloatField(null=True)
    win_rate_unscaled_150d = FloatField(null=True)

    @classmethod
    def ensure_schema(cls):
        """Migration 2026-04-01: add MAE/MFE/capture/shakeout columns + band IC table.
        Migration 2026-04-07: add swing_* barrier-outcome columns."""
        from database.trader_database import DB
        _new_float_cols = [
            'avg_mae_1d', 'avg_mae_7d', 'avg_mae_15d', 'avg_mae_30d', 'avg_mae_60d', 'avg_mae_90d',
            'median_mae_1d', 'median_mae_7d', 'median_mae_15d', 'median_mae_30d', 'median_mae_60d', 'median_mae_90d',
            'avg_mae_winner_1d', 'avg_mae_winner_7d', 'avg_mae_winner_15d',
            'avg_mae_winner_30d', 'avg_mae_winner_60d', 'avg_mae_winner_90d', 'avg_mae_winner_150d',
            'avg_mae_loser_1d', 'avg_mae_loser_7d', 'avg_mae_loser_15d',
            'avg_mae_loser_30d', 'avg_mae_loser_60d', 'avg_mae_loser_90d', 'avg_mae_loser_150d',
            'avg_mae_winner_sigma_1d', 'avg_mae_winner_sigma_7d', 'avg_mae_winner_sigma_15d',
            'avg_mae_winner_sigma_30d', 'avg_mae_winner_sigma_60d',
            'avg_mae_winner_sigma_90d', 'avg_mae_winner_sigma_150d',
            'avg_mae_loser_sigma_1d', 'avg_mae_loser_sigma_7d', 'avg_mae_loser_sigma_15d',
            'avg_mae_loser_sigma_30d', 'avg_mae_loser_sigma_60d',
            'avg_mae_loser_sigma_90d', 'avg_mae_loser_sigma_150d',
            'avg_mfe_1d', 'avg_mfe_7d', 'avg_mfe_15d', 'avg_mfe_30d', 'avg_mfe_60d', 'avg_mfe_90d',
            'median_mfe_1d', 'median_mfe_7d', 'median_mfe_15d', 'median_mfe_30d', 'median_mfe_60d', 'median_mfe_90d',
            # 2026-04-14: MFE percentiles for take-profit anchors
            'mfe_p25_1d', 'mfe_p25_7d', 'mfe_p25_15d', 'mfe_p25_30d', 'mfe_p25_60d', 'mfe_p25_90d', 'mfe_p25_150d',
            'mfe_p75_1d', 'mfe_p75_7d', 'mfe_p75_15d', 'mfe_p75_30d', 'mfe_p75_60d', 'mfe_p75_90d', 'mfe_p75_150d',
            'mfe_p90_1d', 'mfe_p90_7d', 'mfe_p90_15d', 'mfe_p90_30d', 'mfe_p90_60d', 'mfe_p90_90d', 'mfe_p90_150d',
            # 2026-04-14: MFE sigma-normalized (TP anchors in σ units)
            'avg_mfe_sigma_1d', 'avg_mfe_sigma_7d', 'avg_mfe_sigma_15d', 'avg_mfe_sigma_30d',
            'avg_mfe_sigma_60d', 'avg_mfe_sigma_90d', 'avg_mfe_sigma_150d',
            'median_mfe_sigma_1d', 'median_mfe_sigma_7d', 'median_mfe_sigma_15d', 'median_mfe_sigma_30d',
            'median_mfe_sigma_60d', 'median_mfe_sigma_90d', 'median_mfe_sigma_150d',
            'mfe_sigma_p25_1d', 'mfe_sigma_p25_7d', 'mfe_sigma_p25_15d', 'mfe_sigma_p25_30d',
            'mfe_sigma_p25_60d', 'mfe_sigma_p25_90d', 'mfe_sigma_p25_150d',
            'mfe_sigma_p75_1d', 'mfe_sigma_p75_7d', 'mfe_sigma_p75_15d', 'mfe_sigma_p75_30d',
            'mfe_sigma_p75_60d', 'mfe_sigma_p75_90d', 'mfe_sigma_p75_150d',
            'capture_ratio_1d', 'capture_ratio_7d', 'capture_ratio_15d',
            'capture_ratio_30d', 'capture_ratio_60d', 'capture_ratio_90d',
            'shakeout_depth',
            # 2026-04-07: swing barrier outcomes (60d and below)
            'swing_p_stop_1d', 'swing_p_stop_7d', 'swing_p_stop_15d',
            'swing_p_stop_30d', 'swing_p_stop_60d',
            'swing_p_expire_1d', 'swing_p_expire_7d', 'swing_p_expire_15d',
            'swing_p_expire_30d', 'swing_p_expire_60d',
            'swing_avg_win_pnl_1d', 'swing_avg_win_pnl_7d', 'swing_avg_win_pnl_15d',
            'swing_avg_win_pnl_30d', 'swing_avg_win_pnl_60d',
            'swing_avg_stop_pnl_1d', 'swing_avg_stop_pnl_7d', 'swing_avg_stop_pnl_15d',
            'swing_avg_stop_pnl_30d', 'swing_avg_stop_pnl_60d',
            # 2026-04-07: 90d and 150d periods added
            'swing_p_stop_90d', 'swing_p_stop_150d',
            'swing_p_expire_90d', 'swing_p_expire_150d',
            'swing_avg_win_pnl_90d', 'swing_avg_win_pnl_150d',
            'swing_avg_stop_pnl_90d', 'swing_avg_stop_pnl_150d',
            'avg_return_150d', 'win_rate_150d', 'median_return_150d',
            'avg_peak_150d', 'median_peak_150d',
            'avg_mae_150d', 'median_mae_150d',
            'avg_mfe_150d', 'median_mfe_150d',
            'capture_ratio_150d',
            # 2026-04-10: unscaled win rates (fixed K/M barriers, no sqrt scaling)
            'win_rate_unscaled_1d', 'win_rate_unscaled_7d', 'win_rate_unscaled_15d',
            'win_rate_unscaled_30d', 'win_rate_unscaled_60d', 'win_rate_unscaled_90d',
            'win_rate_unscaled_150d',
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
        for table in ['score_assessment_runs', 'score_assessment_meta']:
            try:
                DB.execute_sql(f'ALTER TABLE {table} ADD COLUMN correlation_150d FLOAT NULL')
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
    correlation_150d = FloatField(null=True)
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
    avg_mae_winner_1d = FloatField(null=True)
    avg_mae_winner_7d = FloatField(null=True)
    avg_mae_winner_15d = FloatField(null=True)
    avg_mae_winner_30d = FloatField(null=True)
    avg_mae_winner_60d = FloatField(null=True)
    avg_mae_winner_90d = FloatField(null=True)
    avg_mae_winner_150d = FloatField(null=True)
    avg_mae_loser_1d = FloatField(null=True)
    avg_mae_loser_7d = FloatField(null=True)
    avg_mae_loser_15d = FloatField(null=True)
    avg_mae_loser_30d = FloatField(null=True)
    avg_mae_loser_60d = FloatField(null=True)
    avg_mae_loser_90d = FloatField(null=True)
    avg_mae_loser_150d = FloatField(null=True)
    avg_mae_winner_sigma_1d = FloatField(null=True)
    avg_mae_winner_sigma_7d = FloatField(null=True)
    avg_mae_winner_sigma_15d = FloatField(null=True)
    avg_mae_winner_sigma_30d = FloatField(null=True)
    avg_mae_winner_sigma_60d = FloatField(null=True)
    avg_mae_winner_sigma_90d = FloatField(null=True)
    avg_mae_winner_sigma_150d = FloatField(null=True)
    avg_mae_loser_sigma_1d = FloatField(null=True)
    avg_mae_loser_sigma_7d = FloatField(null=True)
    avg_mae_loser_sigma_15d = FloatField(null=True)
    avg_mae_loser_sigma_30d = FloatField(null=True)
    avg_mae_loser_sigma_60d = FloatField(null=True)
    avg_mae_loser_sigma_90d = FloatField(null=True)
    avg_mae_loser_sigma_150d = FloatField(null=True)
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
    # MFE percentiles — TP anchors: p25=75% of trades reach this, p75=25% reach this.
    mfe_p25_1d = FloatField(null=True)
    mfe_p25_7d = FloatField(null=True)
    mfe_p25_15d = FloatField(null=True)
    mfe_p25_30d = FloatField(null=True)
    mfe_p25_60d = FloatField(null=True)
    mfe_p25_90d = FloatField(null=True)
    mfe_p25_150d = FloatField(null=True)
    mfe_p75_1d = FloatField(null=True)
    mfe_p75_7d = FloatField(null=True)
    mfe_p75_15d = FloatField(null=True)
    mfe_p75_30d = FloatField(null=True)
    mfe_p75_60d = FloatField(null=True)
    mfe_p75_90d = FloatField(null=True)
    mfe_p75_150d = FloatField(null=True)
    mfe_p90_1d = FloatField(null=True)
    mfe_p90_7d = FloatField(null=True)
    mfe_p90_15d = FloatField(null=True)
    mfe_p90_30d = FloatField(null=True)
    mfe_p90_60d = FloatField(null=True)
    mfe_p90_90d = FloatField(null=True)
    mfe_p90_150d = FloatField(null=True)
    # MFE sigma-normalized — TP anchors in units of realized daily σ.
    avg_mfe_sigma_1d = FloatField(null=True)
    avg_mfe_sigma_7d = FloatField(null=True)
    avg_mfe_sigma_15d = FloatField(null=True)
    avg_mfe_sigma_30d = FloatField(null=True)
    avg_mfe_sigma_60d = FloatField(null=True)
    avg_mfe_sigma_90d = FloatField(null=True)
    avg_mfe_sigma_150d = FloatField(null=True)
    median_mfe_sigma_1d = FloatField(null=True)
    median_mfe_sigma_7d = FloatField(null=True)
    median_mfe_sigma_15d = FloatField(null=True)
    median_mfe_sigma_30d = FloatField(null=True)
    median_mfe_sigma_60d = FloatField(null=True)
    median_mfe_sigma_90d = FloatField(null=True)
    median_mfe_sigma_150d = FloatField(null=True)
    mfe_sigma_p25_1d = FloatField(null=True)
    mfe_sigma_p25_7d = FloatField(null=True)
    mfe_sigma_p25_15d = FloatField(null=True)
    mfe_sigma_p25_30d = FloatField(null=True)
    mfe_sigma_p25_60d = FloatField(null=True)
    mfe_sigma_p25_90d = FloatField(null=True)
    mfe_sigma_p25_150d = FloatField(null=True)
    mfe_sigma_p75_1d = FloatField(null=True)
    mfe_sigma_p75_7d = FloatField(null=True)
    mfe_sigma_p75_15d = FloatField(null=True)
    mfe_sigma_p75_30d = FloatField(null=True)
    mfe_sigma_p75_60d = FloatField(null=True)
    mfe_sigma_p75_90d = FloatField(null=True)
    mfe_sigma_p75_150d = FloatField(null=True)
    capture_ratio_1d = FloatField(null=True)
    capture_ratio_7d = FloatField(null=True)
    capture_ratio_15d = FloatField(null=True)
    capture_ratio_30d = FloatField(null=True)
    capture_ratio_60d = FloatField(null=True)
    capture_ratio_90d = FloatField(null=True)
    shakeout_depth = FloatField(null=True)
    shakeout_recovery = IntegerField(null=True)
    # Unscaled win rates — fixed K/M barriers (no sqrt scaling), same W window
    win_rate_unscaled_1d  = FloatField(null=True)
    win_rate_unscaled_7d  = FloatField(null=True)
    win_rate_unscaled_15d = FloatField(null=True)
    win_rate_unscaled_30d = FloatField(null=True)
    win_rate_unscaled_60d = FloatField(null=True)
    win_rate_unscaled_90d = FloatField(null=True)
    win_rate_unscaled_150d = FloatField(null=True)
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


class BacktestTemporalStats(BaseModel):
    """Pre-computed cascade-backtest temporal breakdown per (algorithm version,
    dte_strategy). Rebuilt by `trader assess --force` after the 1y window completes."""
    version          = ForeignKeyField(AlgorithmVersion, backref='backtest_temporal',
                                       on_delete='CASCADE')
    computed_at      = DateTimeField(default=datetime.now)
    initial_capital  = FloatField(default=50000.0)
    summary_json     = TextField(null=True)      # {final_equity, total_return_pct, max_dd, ...}
    yearly_json      = TextField(null=True)      # JSON array of yearly rows
    monthly_json     = TextField(null=True)      # JSON array of (year, month) rows
    monthly_avg_json = TextField(null=True)      # JSON array: cross-year avg per calendar month
    # DTE strategy: '30' (default — Phase H5 30 DTE) or '15' (Phase 15B C1 15 DTE).
    # Each (version, dte_strategy) is a unique row. Added 2026-04-28 (Phase 16).
    dte_strategy     = CharField(max_length=8, default='30')

    class Meta:
        table_name = 'backtest_temporal_stats'
        indexes = ((('version', 'dte_strategy'), True),)

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        try:
            DB.create_tables([cls], safe=True)
        except Exception:
            pass
        # Add monthly_avg_json to existing tables that predate this field
        try:
            DB.execute_sql(
                'ALTER TABLE backtest_temporal_stats ADD COLUMN monthly_avg_json LONGTEXT'
            )
        except Exception:
            pass   # column already exists
        # Add dte_strategy column FIRST so the legacy unique-key migration uses it
        try:
            DB.execute_sql("ALTER TABLE backtest_temporal_stats ADD COLUMN dte_strategy VARCHAR(8) NOT NULL DEFAULT '30'")
        except Exception:
            pass
        try:
            DB.execute_sql("UPDATE backtest_temporal_stats SET dte_strategy='30' WHERE dte_strategy IS NULL OR dte_strategy=''")
        except Exception:
            pass
        # Drop legacy unique on version_id, add new composite unique on (version_id, dte_strategy)
        for idx_name in ('version_id', 'backtest_temporal_stats_version_id',
                         'backtesttemporalstats_version_id'):
            try:
                DB.execute_sql(f'ALTER TABLE backtest_temporal_stats DROP INDEX {idx_name}')
            except Exception:
                pass
        try:
            DB.execute_sql(
                'ALTER TABLE backtest_temporal_stats ADD UNIQUE INDEX uq_bts_version_dte '
                '(version_id, dte_strategy)'
            )
        except Exception:
            pass

    def summary(self):
        return json.loads(self.summary_json) if self.summary_json else {}

    def yearly(self):
        return json.loads(self.yearly_json) if self.yearly_json else []

    def monthly(self):
        return json.loads(self.monthly_json) if self.monthly_json else []

    def monthly_avg(self):
        return json.loads(self.monthly_avg_json) if self.monthly_avg_json else []


class HistoricPeak(BaseModel):
    """Cached table of peak signal events (score >=80 or <=20) per stock over the last 365 days.
    Multiple rows per stock (one per distinct signal event). Rebuilt nightly via
    historic_peaks.update_historic_peaks()."""
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='historic_peaks')
    peak_date = DateField()
    peak_score = IntegerField()
    peak_type = CharField()          # 'HIGH' (call) or 'LOW' (put)
    price_at_peak = DecimalField(max_digits=10, decimal_places=2, null=True)
    current_price = DecimalField(max_digits=10, decimal_places=2, null=True)
    # Sub-scores at peak date
    peak_trend = IntegerField(null=True)
    peak_bb = IntegerField(null=True)
    peak_rsi = IntegerField(null=True)
    peak_macd = IntegerField(null=True)
    peak_vol = IntegerField(null=True)
    peak_weekly = IntegerField(null=True)
    peak_volume_signal = CharField(null=True)
    peak_dte_thesis = CharField(null=True)
    peak_dte_min = IntegerField(null=True)
    peak_dte_max = IntegerField(null=True)
    peak_dte_target = IntegerField(null=True)
    # Best price outcome after peak (direction-aware: + means hypothesis worked)
    # HIGH (call): (max_high - price_at_peak) / price_at_peak x 100
    # LOW  (put):  (price_at_peak - min_low)  / price_at_peak x 100
    price_peak_pct = FloatField(null=True)
    price_peak_days_after = IntegerField(null=True)
    # Raw forward close return at each N-bar horizon (frontend does direction coloring).
    # In-progress: only the first not-yet-reached period carries a value (latest close);
    # subsequent periods are NULL.
    fwd_1d  = FloatField(null=True)
    fwd_7d  = FloatField(null=True)
    fwd_15d = FloatField(null=True)
    fwd_30d = FloatField(null=True)
    fwd_60d = FloatField(null=True)
    fwd_90d = FloatField(null=True)
    # Direction-adjusted intraday best within each N-bar window
    # HIGH (call): (max_high_in_window − price_at_peak) / price_at_peak × 100
    # LOW  (put):  (price_at_peak − min_low_in_window) / price_at_peak × 100
    # _days = 1-based bar index where that best occurred
    fwd_peak_1d       = FloatField(null=True)
    fwd_peak_days_1d  = IntegerField(null=True)
    fwd_peak_7d       = FloatField(null=True)
    fwd_peak_days_7d  = IntegerField(null=True)
    fwd_peak_15d      = FloatField(null=True)
    fwd_peak_days_15d = IntegerField(null=True)
    fwd_peak_30d      = FloatField(null=True)
    fwd_peak_days_30d = IntegerField(null=True)
    fwd_peak_60d      = FloatField(null=True)
    fwd_peak_days_60d = IntegerField(null=True)
    fwd_peak_90d      = FloatField(null=True)
    fwd_peak_days_90d = IntegerField(null=True)
    # Incremental peak gain between adjacent periods (fwd_peak_N - fwd_peak_(N-1))
    # Zero or near-zero means no new high was reached in the extended window.
    fwd_peak_delta_7d  = FloatField(null=True)   # fwd_peak_7d  - fwd_peak_1d
    fwd_peak_delta_15d = FloatField(null=True)   # fwd_peak_15d - fwd_peak_7d
    fwd_peak_delta_30d = FloatField(null=True)   # fwd_peak_30d - fwd_peak_15d
    fwd_peak_delta_60d = FloatField(null=True)   # fwd_peak_60d - fwd_peak_30d
    fwd_peak_delta_90d = FloatField(null=True)   # fwd_peak_90d - fwd_peak_60d
    # Barrier-touch win per period (same formula as assess_scores._swing_walk).
    # 1 = target hit before stop within window, 0 = stop/expire, NULL = insufficient vol data.
    win_1d  = IntegerField(null=True)
    win_7d  = IntegerField(null=True)
    win_15d = IntegerField(null=True)
    win_30d = IntegerField(null=True)
    win_60d = IntegerField(null=True)
    win_90d = IntegerField(null=True)
    # Stop-blind reach: 1 if K×σ×√(W/30) target touched at ANY point in window, stop ignored.
    # Measures pure signal accuracy independent of capital-recycling stop logic.
    reach_1d  = IntegerField(null=True)
    reach_7d  = IntegerField(null=True)
    reach_15d = IntegerField(null=True)
    reach_30d = IntegerField(null=True)
    reach_60d = IntegerField(null=True)
    reach_90d = IntegerField(null=True)
    # 60-day realized daily volatility (%) at signal date — used for bridge runway calc
    vol_pct   = FloatField(null=True)
    # Mixed signal: opposite-direction score reached after the peak
    mixed_signal_date = DateField(null=True)
    mixed_signal_score = IntegerField(null=True)
    mixed_signal_days_after = IntegerField(null=True)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'historic_peaks'
        indexes = (
            (('symbol', 'peak_date'), True),  # unique on (symbol, peak_date)
        )

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)
        # Migrate: old schema had UNIQUE on symbol alone; new schema uses
        # composite (symbol, peak_date).  Drop the old constraint if present.
        try:
            DB.execute_sql('ALTER TABLE historic_peaks DROP INDEX historicpeak_symbol')
        except Exception:
            pass
        # Migrate: add per-period forward return + peak columns if missing
        for col in ('fwd_1d', 'fwd_7d', 'fwd_15d', 'fwd_30d', 'fwd_60d', 'fwd_90d',
                    'fwd_peak_1d', 'fwd_peak_7d', 'fwd_peak_15d',
                    'fwd_peak_30d', 'fwd_peak_60d', 'fwd_peak_90d'):
            try:
                DB.execute_sql(f'ALTER TABLE historic_peaks ADD COLUMN {col} DOUBLE NULL')
            except Exception:
                pass
        for col in ('fwd_peak_days_1d', 'fwd_peak_days_7d', 'fwd_peak_days_15d',
                    'fwd_peak_days_30d', 'fwd_peak_days_60d', 'fwd_peak_days_90d'):
            try:
                DB.execute_sql(f'ALTER TABLE historic_peaks ADD COLUMN {col} INT NULL')
            except Exception:
                pass
        for col in ('fwd_peak_delta_7d', 'fwd_peak_delta_15d', 'fwd_peak_delta_30d',
                    'fwd_peak_delta_60d', 'fwd_peak_delta_90d'):
            try:
                DB.execute_sql(f'ALTER TABLE historic_peaks ADD COLUMN {col} DOUBLE NULL')
            except Exception:
                pass
        # Migrate: add per-period barrier-touch win columns
        for col in ('win_1d', 'win_7d', 'win_15d', 'win_30d', 'win_60d', 'win_90d'):
            try:
                DB.execute_sql(f'ALTER TABLE historic_peaks ADD COLUMN {col} INT NULL')
            except Exception:
                pass
        # Migrate: add stop-blind reach columns
        for col in ('reach_1d', 'reach_7d', 'reach_15d', 'reach_30d', 'reach_60d', 'reach_90d'):
            try:
                DB.execute_sql(f'ALTER TABLE historic_peaks ADD COLUMN {col} INT NULL')
            except Exception:
                pass
        # Migrate: add realized vol column for bridge runway calculation
        try:
            DB.execute_sql('ALTER TABLE historic_peaks ADD COLUMN vol_pct DOUBLE NULL')
        except Exception:
            pass


# ── JA4: SPY weekly composite helpers ──────────────────────────────────────
# Applied asymmetrically: puts use a blended composite (75% current + 25% SPY_wk)
# to suppress false put signals on recovery days before VIX/breadth normalize.
# Calls (pre_regime ≥ 50) always use the unmodified regime_multiplier.
# Validated 2026-04-19 (5y, 79k peaks): put <15 WR15 +0.7pp, calls +0.0pp.

_JA4_SPY_WK_WEIGHT = 0.25   # fraction of composite replaced by SPY_wk


def _load_spy_wk_composite_map():
    """Load all SPY WeeklyScore composites as {date: float}.

    WeeklyScore rows are stored on Mondays; use _spy_wk_on_or_before() to
    forward-fill to any calendar day.
    """
    rows = list(
        WeeklyScore.select(WeeklyScore.date, WeeklyScore.composite)
        .where(WeeklyScore.symbol == 'SPY', WeeklyScore.composite.is_null(False))
        .order_by(WeeklyScore.date.asc())
    )
    return {r.date: float(r.composite) for r in rows}


def _spy_wk_on_or_before(sorted_dates, date_map, target):
    """Forward-fill: return the most recent SPY_wk value on or before target."""
    idx = bisect.bisect_right(sorted_dates, target) - 1
    if idx < 0:
        return None
    return date_map[sorted_dates[idx]]


def _compute_put_regime_mult(composite, spy_wk):
    """JA4: blended put multiplier = apply_bands(75%*composite + 25%*SPY_wk).

    Returns None if either input is unavailable (caller falls back to standard
    regime_multiplier unchanged).
    """
    if composite is None or spy_wk is None:
        return None
    # Lazy import to break circular dependency (market_regime imports from core)
    from market_regime import compute_regime_multiplier as _crm
    blended = max(0.0, min(100.0,
                           (1.0 - _JA4_SPY_WK_WEIGHT) * float(composite)
                           + _JA4_SPY_WK_WEIGHT * float(spy_wk)))
    return _crm(blended)


# ── Mis-stress detector (Priority #6/#8 ship 2026-04-26) ─────────────────────
# The 2026-04-09 regime composite inversion mislabels narrow-bull tape (low VIX +
# weak breadth) as STRESS. Score-stage consequence: calls compressed (mult ~0.7),
# puts amplified ((2-mult) ~1.3). Per-trade analysis of 2023 v21:
#   - Pre-regime calls >=70 = 5108  ->  Post-regime = 2419  (53% lost via compression)
#   - Pre-regime puts <=25 = 8754   ->  Post-regime = 14528 (66% manufactured via amp)
# These ~2700 alpha-rich calls (option-TP ~64.8%) traded for ~5800 zero-EV puts
# (option-TP ~43.5%) is a structural reshaping that hurts strategy directly.
#
# Detector requires BOTH (a) gap between SPY weekly composite and regime
# composite, AND (b) current-day objective bull state. Bull-objective gate
# filters out SPY-weekly lag artifacts that would otherwise dilute bull-year
# (2024) and bear-year (2022) cohorts. Validated 2026-04-25 via Phase-A-style
# sweep: cd=0.25 gives 22-now CALL75+ N +5.6%, WR +0.2pp, 2024 +0.1pp, 2022
# near-no-op (+1 call). See .claude/docs/scoring-algorithm.md.

MIS_STRESS_FULL = 30.0          # gap (spy_wk - composite) >= this -> ms = 1.0
MIS_STRESS_CALL_DAMPEN = 0.25   # softener strength on call regime compression


# ── Earnings calendar helpers (Phase 3C ship) ────────────────────────────────

def _load_earnings_by_symbol(symbols, d_min=None, d_max=None):
    """Return {symbol: sorted_list_of_earnings_dates} for a symbol set.

    Pre-loads the earnings calendar for the score-window so per-signal lookup
    is a binary search instead of a DB round-trip.
    """
    if not EARN_BOOST_ENABLED or not symbols:
        return {}
    syms = list(symbols)
    rows = list(
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
        .where(EarningsDate.symbol.in_(syms))
        .tuples()
    )
    by_sym = {}
    for sym, d in rows:
        by_sym.setdefault(sym, []).append(d)
    for sym in by_sym:
        by_sym[sym].sort()
    return by_sym


def _days_to_next_earnings(by_sym, symbol, signal_date, window=None):
    """Smallest non-negative calendar-day delta to next earnings within window."""
    if window is None:
        window = EARN_BOOST_WINDOW
    eds = by_sym.get(symbol)
    if not eds:
        return None
    idx = bisect.bisect_left(eds, signal_date)
    if idx >= len(eds):
        return None
    delta = (eds[idx] - signal_date).days
    if 0 <= delta <= window:
        return delta
    return None


def _load_effective_earnings_dates(symbol):
    """Load all earnings dates for `symbol` and shift AMC events forward to
    the next trading day (so the date represents when the price reaction
    actually appears, not the announcement date).

    Used by score-stage EARN_BOOST and volume-amplifier earnings-window
    suppression so both fire on the reaction-day rather than treating
    BMO/AMC announcements identically. AMC shift is necessary because for
    a stock reporting AMC on date X, options entered at X close are still
    pre-reaction — the gap shows up at X+1 open.
    """
    from iv_crush_model import compute_effective_date
    rows = list(
        EarningsDate.select(EarningsDate.date, EarningsDate.call_time)
        .where(EarningsDate.symbol == symbol)
        .order_by(EarningsDate.date.asc())
        .tuples()
    )
    return sorted(compute_effective_date(d, ct) for d, ct in rows)


def _days_to_earn_from_sorted(sorted_dates, signal_date, window=None):
    """Same as _days_to_next_earnings but operates on a pre-sorted list (per-stock)."""
    if not EARN_BOOST_ENABLED or not sorted_dates:
        return None
    if window is None:
        window = EARN_BOOST_WINDOW
    idx = bisect.bisect_left(sorted_dates, signal_date)
    if idx >= len(sorted_dates):
        return None
    delta = (sorted_dates[idx] - signal_date).days
    if 0 <= delta <= window:
        return delta
    return None


def _days_to_next_earnings_for(symbol, signal_date):
    """Single-stock earnings lookup for the live-scoring (Score.calculate_overall_score) path.

    Uses effective_date (AMC events shifted to next trading day) so days_to_earn
    reflects when the price reaction actually appears. Hits DB once per call.
    """
    if not EARN_BOOST_ENABLED:
        return None
    sorted_eff = _load_effective_earnings_dates(symbol)
    return _days_to_earn_from_sorted(sorted_eff, signal_date)


def _load_mis_stress_map(regime_map=None):
    """Build {date: mis_stress_strength in [0, 1]} from market_regime + SPY_wk.

    regime_map (optional): pre-loaded {date: (mult, composite)} for caller speed.
    If None, fetches from MarketRegime.

    Output: 0.0 on real-stress days (composite low AND spy_wk also low), 0.0 on
    objectively-non-bull days, scales with (spy_wk - composite) gap on
    objectively-bull-mislabeled-stress days.
    """
    spy_wk_raw = _load_spy_wk_composite_map()
    spy_wk_dates = sorted(spy_wk_raw.keys())

    # Pull MarketRegime fields needed for objective-bull gate + composite.
    rows = list(
        MarketRegime.select(
            MarketRegime.date, MarketRegime.regime_composite,
            MarketRegime.vix_close, MarketRegime.spy_close,
            MarketRegime.spy_ema200,
        )
        .where(MarketRegime.regime_multiplier.is_null(False))
        .order_by(MarketRegime.date.asc())
        .namedtuples()
    )
    spy_close_dates = [r.date for r in rows if r.spy_close is not None]
    spy_close_map = {r.date: float(r.spy_close) for r in rows if r.spy_close is not None}
    spy_close_idx = {d: i for i, d in enumerate(spy_close_dates)}

    out = {}
    for r in rows:
        comp = float(r.regime_composite) if r.regime_composite is not None else None
        if comp is None or r.spy_close is None or r.spy_ema200 is None or r.vix_close is None:
            continue
        s_wk = _spy_wk_on_or_before(spy_wk_dates, spy_wk_raw, r.date)
        if s_wk is None:
            continue
        i = spy_close_idx.get(r.date)
        if i is None or i < 10:
            continue
        spy_10d = (spy_close_map[r.date]
                   / spy_close_map[spy_close_dates[i - 10]] - 1.0) * 100.0
        bull_obj = (
            float(r.spy_close) > float(r.spy_ema200)
            and float(r.vix_close) < 20.0
            and spy_10d > 0
        )
        if not bull_obj:
            out[r.date] = 0.0
            continue
        gap = max(0.0, s_wk - comp)
        out[r.date] = min(1.0, gap / MIS_STRESS_FULL)
    return out


def reapply_regime_today(regime_multiplier, regime_composite=None, target_date=None):
    """Re-apply a freshly-computed regime multiplier to all of today's Score rows.

    Called by `trader update` immediately after `compute_regime()` creates a new
    MarketRegime row for today.  Reads `weight_info['pre_regime']` from each row
    and recomputes `overall` using the same symmetric formula as compute_overall_score()
    — no indicator re-fetch required.

    Returns (updated, skipped) counts.
    """
    if target_date is None:
        target_date = date.today()

    version = AlgorithmVersion.get_active_scores_version()
    rows = list(
        Score.select()
        .where(Score.date == target_date, Score.version == version.id)
    )

    if not rows:
        return 0, 0

    import json as _json
    from database.trader_database import DB

    # JA4: compute blended put multiplier for today using SPY_wk
    _put_mult = None
    if regime_composite is not None:
        _spy_wk_map = _load_spy_wk_composite_map()
        if _spy_wk_map:
            _spy_wk_sorted = sorted(_spy_wk_map.keys())
            _spy_wk_today = _spy_wk_on_or_before(_spy_wk_sorted, _spy_wk_map, target_date)
            _put_mult = _compute_put_regime_mult(regime_composite, _spy_wk_today)

    # Mis-stress softener strength for today (if computable)
    _ms_today = _load_mis_stress_map().get(target_date, 0.0)

    updated = 0
    skipped = 0
    pending = []

    from database.utils.scoring import MIS_STRESS_CALL_DAMPEN

    for s in rows:
        try:
            wi = _json.loads(s.weight_info) if s.weight_info else {}
        except Exception:
            wi = {}

        pre = wi.get('pre_regime')
        if pre is None:
            skipped += 1
            continue

        pre = int(pre)
        # JA4: use blended put multiplier for bearish scores
        eff_mult = (_put_mult if (pre < 50 and _put_mult is not None) else regime_multiplier)

        # Mis-stress softener (calls only)
        if (_ms_today > 0 and pre >= 50 and eff_mult is not None):
            eff_mult = 1.0 + (eff_mult - 1.0) * (1.0 - _ms_today * MIS_STRESS_CALL_DAMPEN)

        if eff_mult is not None and eff_mult != 1.0:
            if pre >= 50:
                adjusted = 50 + (pre - 50) * eff_mult
            else:
                adjusted = 50 + (pre - 50) * (2.0 - eff_mult)
            new_overall = int(max(0, min(100, round(adjusted))))
        else:
            new_overall = pre

        if s.overall == new_overall and s.regime_multiplier == regime_multiplier:
            skipped += 1
            continue

        s.overall = new_overall
        s.regime_multiplier = regime_multiplier  # always store standard multiplier
        if regime_composite is not None:
            s.regime_composite = regime_composite
        pending.append(s)
        updated += 1

    if pending:
        with DB.atomic():
            for s in pending:
                s.save()

    return updated, skipped


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

    def unflag(self):
        self.flagged = False
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
        self.name = _clean_name(ticker.info.get('longName') or ticker.info.get('shortName'))
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

    def calculate_scores(self, cutoff_date=None, full=False, weekly=False, silent=False,
                          regime_multiplier=None, regime_composite=None):
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
                score.overall = score.calculate_overall_score(regime_multiplier=regime_multiplier)
                if regime_composite is not None:
                    score.regime_composite = regime_composite
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

    def calculate_scores_batched(self, silent=False, regime_multiplier=None, regime_composite=None):
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

        # AMC events shifted to next trading day so EARN_BOOST proximity +
        # volume-amplifier earnings-window logic see the date when the price
        # reaction actually appears, not the announcement date.
        _earn_dates_sorted = _load_effective_earnings_dates(self.symbol)
        earnings_set = set(_earn_dates_sorted)

        # Regime multiplier map: date → (multiplier, composite)
        # If caller provides a fixed multiplier (e.g. stale regime for today),
        # use it for all dates; otherwise bulk-load from MarketRegime table.
        if regime_multiplier is not None:
            _regime_map = None  # use caller-provided value for all dates
        else:
            _regime_rows = list(
                MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier, MarketRegime.regime_composite)
                .where(MarketRegime.regime_multiplier.is_null(False))
                .namedtuples()
            )
            _regime_map = {r.date: (float(r.regime_multiplier), float(r.regime_composite) if r.regime_composite is not None else None) for r in _regime_rows}
            # Fallback: if today has no regime row yet (premarket), inject the most
            # recent prior row so scores aren't scored with an implicit 1.0 multiplier.
            today = date.today()
            if today not in _regime_map and _regime_rows:
                latest = max(_regime_rows, key=lambda r: r.date)
                _regime_map[today] = (float(latest.regime_multiplier), float(latest.regime_composite) if latest.regime_composite is not None else None)

        # JA4: SPY weekly composite lookup for asymmetric put multiplier
        _spy_wk_map = _load_spy_wk_composite_map()
        _spy_wk_sorted = sorted(_spy_wk_map.keys())

        # Mis-stress softener map (Priority #6/#8 ship 2026-04-26)
        _mis_stress_map = _load_mis_stress_map()

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
                _ph_d = ph_map.get(d)
                _pct_ema_rsi = (
                    (float(_ph_d.close) - float(ind.ema_50)) / float(ind.ema_50) * 100
                    if _ph_d and ind.ema_50 is not None else None
                )
                score.rsi   = self.calculate_rsi_score(
                    d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ph_map,
                    macdh_raw=float(ind.macd_hist) if ind.macd_hist is not None else None,
                    pct_from_ema50=_pct_ema_rsi)
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

                # ── EMA50 extension (for vol_boost dampening in scoring) ─────
                _pct_from_ema50 = None
                if ind.ema_50 and ph and ph.close:
                    _pct_from_ema50 = (float(ph.close) - float(ind.ema_50)) / float(ind.ema_50) * 100

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

                # ── Regime multiplier for this date ─────────────────────────
                if _regime_map is not None:
                    _rm_pair = _regime_map.get(d)
                    _r_mult = _rm_pair[0] if _rm_pair else None
                    _r_comp = _rm_pair[1] if _rm_pair else None
                else:
                    _r_mult = regime_multiplier
                    _r_comp = regime_composite

                # JA4: blended put multiplier
                _spy_wk = _spy_wk_on_or_before(_spy_wk_sorted, _spy_wk_map, d)
                _put_mult = _compute_put_regime_mult(_r_comp, _spy_wk)

                # Mis-stress softener strength (0 outside narrow-bull-mislabel)
                _mis_stress = _mis_stress_map.get(d, 0.0)

                # ── Overall score ────────────────────────────────────────────
                _d_to_ern = _days_to_earn_from_sorted(_earn_dates_sorted, d)
                overall, weight_info, vol_update = compute_overall_score(
                    score.trend, score.bb, score.rsi, score.macd,
                    score.stoch, score.technical_alignment,
                    bb_pct=bb_pct,
                    pct_from_ema50=_pct_from_ema50,
                    ws_trend=ws.trend if ws else None,
                    ws_rsi=ws_rsi, ws_macd=ws_macd,
                    prev_ws_trend=pws.trend if pws else None,
                    prev_ws_rsi=pws.rsi   if pws else None,
                    prev_ws_macd=pws.macd if pws else None,
                    vol_mult=vol_mult, vol_raw=vol_raw,
                    vol_sig=vol_sig,   vol_mag=vol_mag,
                    blend_w=blend_w,   vol_target=vol_target,
                    macdh_raw=float(ind.macd_hist) if ind and ind.macd_hist is not None else None,
                    regime_multiplier=_r_mult,
                    put_regime_multiplier=_put_mult,
                    mis_stress=_mis_stress,
                    days_to_earnings=_d_to_ern,
                )

                if overall is None:
                    score.delete_instance()
                    continue

                score.overall     = overall
                score.regime_composite = _r_comp
                score.regime_multiplier = _r_mult
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
                                  show_progress=True, cutoff_end=None):
        wq = (WeeklyScore.select()
              .where((WeeklyScore.symbol == self.symbol) & (WeeklyScore.date >= cutoff)))
        if cutoff_end is not None:
            wq = wq.where(WeeklyScore.date <= cutoff_end)
        scores = list(wq.order_by(WeeklyScore.date.asc()))
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

    def recalculate_scores_batched(self, cutoff, components, cutoff_end=None, force=False,
                                   score_version=None, persist_lo=None, persist_hi_exclusive=None,
                                   save_only_dates=None, show_progress=False,
                                   regime_map=None, spy_wk_map=None, mis_stress_map=None):
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
        score_version: Score rows to load/update; default active algorithm version.
        save_only_dates: if set, only persist changes for these dates (others run for decay context).
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

        # No work needed: progressive passes would only skip; avoid loading full history.
        if (
            save_only_dates is None
            and not force
            and 'overall' in components
            and all(s.overall is not None for s in scores)
        ):
            return 0, len(scores), 0

        # Bulk loads are windowed to [cutoff - LOAD_BUFFER_DAYS, cutoff_end].
        # Volume amplifier walks at most 260 prior trading days (_VOL_SLICE_CAP);
        # 365 calendar-day buffer covers that plus weekends/holidays plus any
        # indicator rolling-window lookback in calculate_*_score.
        _LOAD_BUFFER_DAYS = 365
        _ind_lo = cutoff - timedelta(days=_LOAD_BUFFER_DAYS)

        # Indicator map (date → Indicator row) — used for bb_pct in overall
        _ind_q = Indicator.select().where(
            (Indicator.symbol == self.symbol) & (Indicator.date >= _ind_lo)
        )
        if cutoff_end is not None:
            _ind_q = _ind_q.where(Indicator.date <= cutoff_end)
        ind_rows = list(_ind_q.order_by(Indicator.date.asc()))
        ind_map = {i.date: i for i in ind_rows}

        # Price history — descending list for volume amplifier + ascending map
        _ph_q = PriceHistory.select().where(
            (PriceHistory.symbol == self.symbol) & (PriceHistory.date >= _ind_lo)
        )
        if cutoff_end is not None:
            _ph_q = _ph_q.where(PriceHistory.date <= cutoff_end)
        ph_rows_asc = list(_ph_q.order_by(PriceHistory.date.asc()))
        ph_map = {p.date: p for p in ph_rows_asc}
        ph_rows_desc = list(reversed(ph_rows_asc))

        prev_close_map = {}
        if save_only_dates is not None:
            for i, p in enumerate(ph_rows_asc):
                prev_close_map[p.date] = ph_rows_asc[i - 1].close if i > 0 else p.close
        recent_overalls = deque(maxlen=10) if save_only_dates is not None else None

        # Weekly scores map: week_start → WeeklyScore.
        # The score loop only reads `current week` and `prior week`, so a 30d
        # buffer is plenty; same cutoff_end clamp as above.
        _ws_lo = cutoff - timedelta(days=30)
        _ws_q = WeeklyScore.select().where(
            (WeeklyScore.symbol == self.symbol) & (WeeklyScore.date >= _ws_lo)
        )
        if cutoff_end is not None:
            _ws_q = _ws_q.where(WeeklyScore.date <= cutoff_end)
        ws_rows = list(_ws_q.order_by(WeeklyScore.date.asc()))
        ws_map = {ws.date: ws for ws in ws_rows}

        # Earnings dates set (all time — suppression window is ±1 day)
        # AMC events shifted to next trading day so EARN_BOOST proximity +
        # volume-amplifier earnings-window logic see the date when the price
        # reaction actually appears, not the announcement date.
        _earn_dates_sorted = _load_effective_earnings_dates(self.symbol)
        earnings_set = set(_earn_dates_sorted)

        # Regime multiplier map: date → (multiplier, composite). Accept a pre-loaded
        # map from the caller to avoid re-querying per stock in a full-universe run.
        if regime_map is not None:
            _regime_map = regime_map
        else:
            _regime_rows = list(
                MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier, MarketRegime.regime_composite)
                .where(MarketRegime.regime_multiplier.is_null(False))
                .namedtuples()
            )
            _regime_map = {r.date: (float(r.regime_multiplier), float(r.regime_composite) if r.regime_composite is not None else None) for r in _regime_rows}
            # Fallback: if today has no regime row yet (premarket), inject the most
            # recent prior row so scores aren't scored with an implicit 1.0 multiplier.
            today = date.today()
            if today not in _regime_map and _regime_rows:
                latest = max(_regime_rows, key=lambda r: r.date)
                _regime_map[today] = (float(latest.regime_multiplier), float(latest.regime_composite) if latest.regime_composite is not None else None)

        # JA4: SPY weekly composite lookup for asymmetric put multiplier.
        # Accept pre-loaded map from caller (recalculate_scores_full loads once
        # for the entire universe run to avoid a query per stock).
        _spy_wk_map_r = spy_wk_map if spy_wk_map is not None else _load_spy_wk_composite_map()
        _spy_wk_sorted_r = sorted(_spy_wk_map_r.keys())

        # Mis-stress softener map (Priority #6/#8 ship 2026-04-26).
        # Accept pre-loaded map from caller; falls back to building per-call.
        _mis_stress_map_r = (mis_stress_map if mis_stress_map is not None
                             else _load_mis_stress_map())

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
        # Fallback: when the current version has no prior rows (e.g. a freshly
        # shipped algorithm version), seed from the most recent volume signals
        # across ANY version so decay context isn't lost at the boundary.
        if not seed_rows:
            seed_rows = list(
                Score.select(Score.date, Score.volume_signal, Score.volume_magnitude, Score.volume)
                .where(
                    (Score.symbol == self.symbol) &
                    (Score.date < cutoff) &
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
        ph_dates_asc = [p.date for p in ph_rows_asc]
        _N_PH = len(ph_rows_asc)
        # Volume amplifier uses 200-day rolling windows; cap slice at 260 for safety.
        _VOL_SLICE_CAP = 260

        pending_saves = []  # collected score rows modified in this pass, flushed in bulk at end

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
                        _cur_ind = ind_cache[0] if ind_cache else None
                        _ph_r = ph_map.get(score.date)
                        _pct_ema_r = (
                            (float(_ph_r.close) - float(_cur_ind.ema_50)) / float(_cur_ind.ema_50) * 100
                            if _ph_r and _cur_ind and _cur_ind.ema_50 is not None else None
                        )
                        val = self.calculate_rsi_score(
                            score.date, trend_score=score.trend,
                            _ind_cache=ind_cache, _ph_cache=ph_map,
                            macdh_raw=float(_cur_ind.macd_hist) if _cur_ind and _cur_ind.macd_hist is not None else None,
                            pct_from_ema50=_pct_ema_r)
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
                    _pct_from_ema50_r = None
                    ind = ind_map.get(score.date)
                    ph  = ph_map.get(score.date)
                    if ind and None not in (ind.upper_band, ind.lower_band):
                        bw = float(ind.upper_band) - float(ind.lower_band)
                        if bw > 0 and ph:
                            bb_pct = (float(ph.close) - float(ind.lower_band)) / bw
                    if ind and ind.ema_50 and ph and ph.close:
                        _pct_from_ema50_r = (float(ph.close) - float(ind.ema_50)) / float(ind.ema_50) * 100

                    # Weekly context
                    ws  = ws_map.get(_week_start(score.date))
                    pws = ws_map.get(_week_start(score.date) - timedelta(weeks=1))
                    ws_trend = ws.trend if ws else None
                    ws_rsi   = ws.rsi   if ws and ws.rsi and ws.macd else None
                    ws_macd  = ws.macd  if ws and ws.rsi and ws.macd else None

                    # Volume signal from cache (no DB). Use bisect + bounded slice
                    # so this is O(log N + cap) per iteration instead of O(N).
                    from volume_amplifier import get_volume_multiplier_from_cache
                    _k = bisect.bisect_right(ph_dates_asc, score.date)
                    _desc_start = _N_PH - _k
                    ph_slice_desc = ph_rows_desc[_desc_start:_desc_start + _VOL_SLICE_CAP]
                    vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = \
                        get_volume_multiplier_from_cache(
                            score.date, ph_slice_desc, prior_vol_signals,
                            earnings_set, pre_volume_overall=None,
                        )

                    # Regime multiplier for this date
                    _rm_pair = _regime_map.get(score.date)
                    _r_mult = _rm_pair[0] if _rm_pair else None
                    _r_comp = _rm_pair[1] if _rm_pair else None

                    # JA4: blended put multiplier
                    _spy_wk_r = _spy_wk_on_or_before(_spy_wk_sorted_r, _spy_wk_map_r, score.date)
                    _put_mult_r = _compute_put_regime_mult(_r_comp, _spy_wk_r)

                    # Mis-stress softener strength
                    _mis_stress_r = _mis_stress_map_r.get(score.date, 0.0)

                    # Days to next earnings for boost lookup
                    _d_to_ern_r = _days_to_earn_from_sorted(_earn_dates_sorted, score.date)

                    overall, weight_info, vol_update = compute_overall_score(
                        score.trend, score.bb, score.rsi, score.macd,
                        score.stoch, score.technical_alignment,
                        bb_pct=bb_pct,
                        pct_from_ema50=_pct_from_ema50_r,
                        ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
                        prev_ws_trend=pws.trend if pws else None,
                        prev_ws_rsi=pws.rsi   if pws else None,
                        prev_ws_macd=pws.macd if pws else None,
                        vol_mult=vol_mult, vol_raw=vol_raw,
                        vol_sig=vol_sig,   vol_mag=vol_mag,
                        blend_w=blend_w,   vol_target=vol_target,
                        macdh_raw=float(ind.macd_hist) if ind and ind.macd_hist is not None else None,
                        regime_multiplier=_r_mult,
                        put_regime_multiplier=_put_mult_r,
                        mis_stress=_mis_stress_r,
                        days_to_earnings=_d_to_ern_r,
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
                    score.regime_composite = _r_comp
                    score.regime_multiplier = _r_mult
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
                    pending_saves.append(score)
                    updated += 1
                else:
                    skipped += 1

            except Exception as e:
                errors += 1
                print(Fore.RED + f"  {self.symbol} {score.date}: {e}")

        if pending_saves:
            # Score uses a CompositeKey so Peewee's bulk_update is not available.
            # Use a single INSERT ... ON DUPLICATE KEY UPDATE per batch to reduce
            # MySQL round-trips from N (one per save()) down to ceil(N/500).
            from database.trader_database import DB
            _upd_cols = [
                'overall', 'weight_info',
                'trend', 'bb', 'rsi', 'macd', 'stoch', 'technical_alignment',
                'volume', 'volume_signal', 'volume_magnitude',
                'score_velocity_7d',
                'regime_composite', 'regime_multiplier',
                'updated_at',
                'daily_change', 'price',
                'pct_from_ema50', 'pct_from_ema200', 'bb_position',
                'next_earnings', 'price_target', 'price_target_growth',
            ]
            _all_cols = ['symbol', 'date', 'version_id'] + _upd_cols
            _ph       = ', '.join(['%s'] * len(_all_cols))
            _upd_sql  = ', '.join(f'`{c}`=VALUES(`{c}`)' for c in _upd_cols)
            _sql = (
                f"INSERT INTO `scores` ({', '.join(f'`{c}`' for c in _all_cols)}) "
                f"VALUES ({_ph}) "
                f"ON DUPLICATE KEY UPDATE {_upd_sql}"
            )

            def _row(s):
                return (
                    s.symbol_id, s.date, s.version_id,
                    s.overall,
                    s.weight_info,
                    s.trend,
                    s.bb,
                    s.rsi,
                    s.macd,
                    s.stoch,
                    s.technical_alignment,
                    s.volume,
                    s.volume_signal,
                    s.volume_magnitude,
                    s.score_velocity_7d,
                    float(s.regime_composite)    if s.regime_composite    is not None else None,
                    float(s.regime_multiplier)   if s.regime_multiplier   is not None else None,
                    s.updated_at,
                    float(s.daily_change)        if s.daily_change        is not None else None,
                    float(s.price)               if s.price               is not None else None,
                    s.pct_from_ema50,
                    s.pct_from_ema200,
                    s.bb_position,
                    s.next_earnings,
                    float(s.price_target)        if s.price_target        is not None else None,
                    float(s.price_target_growth) if s.price_target_growth is not None else None,
                )

            _BATCH = 500
            try:
                for i in range(0, len(pending_saves), _BATCH):
                    _batch = pending_saves[i:i + _BATCH]
                    _params = [v for s in _batch for v in _row(s)]
                    _sql_b  = _sql.replace(f'VALUES ({_ph})',
                        'VALUES ' + ', '.join([f'({_ph})'] * len(_batch)))
                    DB.execute_sql(_sql_b, _params)
            except Exception as e:
                # Fall back to per-row saves on bulk failure.
                print(Fore.YELLOW + f"  {self.symbol}: bulk upsert fell back to per-row ({e})")
                for s in pending_saves:
                    try:
                        s.save()
                    except Exception as ee:
                        errors += 1
                        print(Fore.RED + f"  {self.symbol} {s.date}: {ee}")

        return updated, skipped, errors

    def recalculate_scores_full(self, cutoff_start, components, cutoff_end=None,
                                 force=False, use_batch=True, show_progress=False,
                                 regime_map=None, spy_wk_map=None, mis_stress_map=None):
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
            # Window: shells need only `cutoff_start - 1` for prev_close lookups.
            # 30d buffer absorbs holidays/weekends comfortably.
            _shell_lo = cutoff_start - timedelta(days=30)
            ph_rows = list(
                PriceHistory.select(PriceHistory.date, PriceHistory.close)
                .where((PriceHistory.symbol == self.symbol) &
                       (PriceHistory.date >= _shell_lo))
                .order_by(PriceHistory.date.asc())
            )
            price_map = {p.date: float(p.close) for p in ph_rows}
            prev_close = {p.date: float(ph_rows[i - 1].close) if i > 0 else float(p.close)
                          for i, p in enumerate(ph_rows)}
            now = datetime.now()
            # Bulk-insert all shells in one INSERT IGNORE instead of N get_or_creates
            # (each get_or_create was 2 DB round-trips; this is 1 regardless of N).
            from database.trader_database import DB
            _shell_rows = []
            for d in missing:
                cur  = price_map.get(d)
                prev = prev_close.get(d)
                dc   = round(((cur - prev) / prev) * 100, 2) if cur and prev and prev != 0 else 0
                _shell_rows.append({
                    'symbol': self.symbol, 'date': d, 'version': version,
                    'price': cur or 0, 'daily_change': dc,
                    'updated_at': now,
                })
            _BATCH = 500
            for i in range(0, len(_shell_rows), _BATCH):
                Score.insert_many(_shell_rows[i:i + _BATCH]).on_conflict_ignore().execute()

        # ── 4. Batched recalculate for this slice ────────────────────────────
        if use_batch:
            # Use caller-supplied maps when available (single load per process);
            # fall back to per-call load only when invoked directly without hoisting.
            _spy_wk_map = spy_wk_map if spy_wk_map is not None else _load_spy_wk_composite_map()
            _mis_stress_map = mis_stress_map if mis_stress_map is not None else _load_mis_stress_map()
            return self.recalculate_scores_batched(
                cutoff_start, components,
                cutoff_end=cutoff_end, force=force, score_version=version,
                show_progress=show_progress, regime_map=regime_map,
                spy_wk_map=_spy_wk_map,
                mis_stress_map=_mis_stress_map,
            )
        return self.recalculate_scores(
            cutoff_start, components, show_progress=show_progress)

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
                            _ind_cache=None, _ph_cache=None, macdh_raw=None, pct_from_ema50=None):
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

        # Momentum confirmation softening (EMA50-gated):
        # When MACDh contradicts the trend AND price is near/above EMA50, raise the
        # center so RSI isn't over-penalised as overbought. The EMA50 gate prevents
        # firing on deeply-depressed stocks (EMA50 < -8%) where rising MACDh is
        # often a dead-cat bounce, not a genuine recovery.
        # Max lift: 4 pts at full MACDh contradiction + price right at EMA50.
        if (macdh_raw is not None and pct_from_ema50 is not None
                and abs(trend_bias) > 0.15):
            macdh_dir = float(np.tanh(macdh_raw / 0.05))   # -1 (falling) → +1 (rising)
            # ema_proximity: 0 when price is ≥8% below EMA50, 1 when at/above EMA50
            ema_proximity = max(0.0, min(1.0, (pct_from_ema50 + 8.0) / 8.0))
            if trend_bias < 0 and macdh_dir > 0:            # bearish trend, rising momentum
                center += 4.0 * macdh_dir * abs(trend_bias) * ema_proximity
            elif trend_bias > 0 and macdh_dir < 0:          # bullish trend, falling momentum
                center -= 4.0 * abs(macdh_dir) * trend_bias * ema_proximity

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

