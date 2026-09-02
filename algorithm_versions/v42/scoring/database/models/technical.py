from trader_database import BaseModel, DB
import math
from fileHelper import is_int
from peewee import ForeignKeyField, DeferredForeignKey, IntegerField, DateTimeField, DateField, CompositeKey, DecimalField
from datetime import datetime, timedelta
from decimal import Decimal


def _clean_num(v):
    """NaN → None; pass-through otherwise. talib emits np.nan for warmup periods."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


_BULK_CHUNK = 500

class PriceHistory(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='price_history')
    date = DateField()
    open = DecimalField(max_digits=10, decimal_places=2)
    high = DecimalField(max_digits=10, decimal_places=2)
    low = DecimalField(max_digits=10, decimal_places=2)
    close = DecimalField(max_digits=10, decimal_places=2)
    volume = IntegerField()
    pulled_at = DateTimeField(null=True)

    @classmethod
    def build(cls, symbol, date, open, high, low, close, volume):
        price, created = cls.get_or_create(symbol = symbol, date = date)
        price.open = open
        price.high = high
        price.low = low
        price.close = close
        price.volume = volume
        price.pulled_at = datetime.now()
        price.save()

        # Update weekly price history incrementally
        cls._update_weekly_price_history(symbol, date, open, high, low, close, volume)

        return price

    @classmethod
    def bulk_build(cls, symbol, rows, refresh_weekly=True):
        """Bulk upsert daily price rows and (optionally) refresh affected weekly aggregates.

        rows: iterable of dicts with keys date, open, high, low, close, volume.
        Returns (n_daily_written, n_weekly_written). Replaces N×(get_or_create+save)
        + N×(weekly get_or_create+save) round-trips with a handful of bulk statements.
        """
        rows = [r for r in rows if r is not None]
        if not rows:
            return 0, 0
        now = datetime.now()
        payload = [
            {
                'symbol': symbol,
                'date': r['date'],
                'open': r['open'],
                'high': r['high'],
                'low': r['low'],
                'close': r['close'],
                'volume': int(r['volume']) if r['volume'] is not None else 0,
                'pulled_at': now,
            }
            for r in rows
        ]
        with DB.atomic():
            for i in range(0, len(payload), _BULK_CHUNK):
                cls.insert_many(payload[i:i + _BULK_CHUNK]).on_conflict_replace().execute()

        n_weekly = 0
        if refresh_weekly:
            dates = [r['date'] for r in rows]
            n_weekly = cls._refresh_weekly_aggregates(symbol, min(dates), max(dates))
        return len(payload), n_weekly

    @classmethod
    def _refresh_weekly_aggregates(cls, symbol, date_lo, date_hi):
        """Recompute WeeklyPriceHistory rows for every week intersecting [date_lo, date_hi].

        Pulls all daily rows in the affected week range with a single SELECT, aggregates
        in Python, and bulk-inserts via on_conflict_replace. Correct even when partial
        weeks were already in DB before this run.
        """
        wk_lo = date_lo - timedelta(days=date_lo.weekday())
        # Range covers the full last week
        wk_hi_exclusive = (date_hi - timedelta(days=date_hi.weekday())) + timedelta(days=7)
        daily = list(
            cls.select(cls.date, cls.open, cls.high, cls.low, cls.close, cls.volume)
            .where(
                (cls.symbol == symbol)
                & (cls.date >= wk_lo)
                & (cls.date < wk_hi_exclusive)
            )
            .order_by(cls.date.asc())
            .dicts()
        )
        if not daily:
            return 0
        from collections import defaultdict
        by_week = defaultdict(list)
        for d in daily:
            wk = d['date'] - timedelta(days=d['date'].weekday())
            by_week[wk].append(d)

        now = datetime.now()
        weekly_rows = []
        for wk, days in by_week.items():
            # days already sorted by date ascending
            weekly_rows.append({
                'symbol': symbol,
                'date': wk,
                'open': days[0]['open'],
                'high': max(x['high'] for x in days),
                'low': min(x['low'] for x in days),
                'close': days[-1]['close'],
                'volume': sum(int(x['volume']) for x in days),
                'pulled_at': now,
            })
        with DB.atomic():
            for i in range(0, len(weekly_rows), _BULK_CHUNK):
                WeeklyPriceHistory.insert_many(
                    weekly_rows[i:i + _BULK_CHUNK]
                ).on_conflict_replace().execute()
        return len(weekly_rows)
    
    @staticmethod
    def _update_weekly_price_history(symbol, date, open, high, low, close, volume):
        """Update weekly price history incrementally from daily price"""
        from datetime import timedelta
        from decimal import Decimal
        
        def get_week_start(date_obj):
            days_since_monday = date_obj.weekday()
            return date_obj - timedelta(days=days_since_monday)
        
        week_start = get_week_start(date)
        is_monday = date.weekday() == 0
        
        # Get or create weekly record
        weekly, created = WeeklyPriceHistory.get_or_create(symbol=symbol, date=week_start)
        
        if is_monday or created:
            # Monday or new week: reset with daily values
            weekly.open = Decimal(str(open))
            weekly.high = Decimal(str(high))
            weekly.low = Decimal(str(low))
            weekly.close = Decimal(str(close))
            weekly.volume = int(volume)
        else:
            # Update incrementally: high/low/close/volume
            if float(high) > float(weekly.high):
                weekly.high = Decimal(str(high))
            if float(low) < float(weekly.low):
                weekly.low = Decimal(str(low))
            weekly.close = Decimal(str(close))
            weekly.volume = int(weekly.volume) + int(volume)
        
        weekly.pulled_at = datetime.now()
        weekly.save()
    
    def stock(self):
        from database.models.core import Stock
        return Stock.get_by_id(self.symbol)

    def indicator(self):
        return Indicator.get_or_none(Indicator.symbol == self.symbol, Indicator.date == self.date)

    class Meta:
        table_name = 'price_history'
        indexes = (
            (('symbol', 'date'), True),  # Primary key
            (('symbol',), False),        # For symbol-specific queries
            (('date',), False),          # For date-based queries
            (('symbol', 'date', 'close'), False), # For price lookups
            (('date', 'close'), False),  # For date + price queries
        )
        primary_key = CompositeKey('symbol', 'date')

class Indicator(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='indicators')
    date = DateField()
    rsi = DecimalField(max_digits=6, decimal_places=2, null=True)
    rsi_ma = DecimalField(max_digits=6, decimal_places=2, null=True)
    rsi_ema = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch_rsi = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch_rsi_signal = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch_signal = DecimalField(max_digits=6, decimal_places=2, null=True)
    macd = DecimalField(max_digits=7, decimal_places=3, null=True)
    macd_signal = DecimalField(max_digits=7, decimal_places=3, null=True)
    macd_hist = DecimalField(max_digits=7, decimal_places=3, null=True)
    upper_band = DecimalField(max_digits=9, decimal_places=3, null=True)
    middle_band = DecimalField(max_digits=9, decimal_places=3, null=True)
    lower_band = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_9 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_9 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_21 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_21 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_50 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_50 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_200 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_200 = DecimalField(max_digits=9, decimal_places=3, null=True)
    peak = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_20 = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_60 = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_120 = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_180 = DecimalField(max_digits=9, decimal_places=2, null=True)
    obv = IntegerField(null=True)

    @classmethod
    def build(cls, symbol, date, rsi=None, rsi_ma=None, rsi_ema=None, stoch_rsi=None, stoch_rsi_signal=None, stoch=None, stoch_signal=None, macd=None, macd_signal=None, macd_hist=None,
              upper_band=None, middle_band=None, lower_band=None, obv=None, ma_9=None, ema_9=None, ma_21=None, ema_21=None, ma_50=None, ema_50=None, ma_200=None, ema_200=None,
              peak=None, peak_20=None, peak_60=None, peak_120=None, peak_180=None):
        indicator, created = cls.get_or_create(symbol=symbol, date=date)
        indicator.rsi = None if isinstance(rsi, float) and math.isnan(rsi) else rsi
        indicator.rsi_ma = None if isinstance(rsi_ma, float) and math.isnan(rsi_ma) else rsi_ma
        indicator.rsi_ema = None if isinstance(rsi_ema, float) and math.isnan(rsi_ema) else rsi_ema
        indicator.stoch_rsi = None if isinstance(stoch_rsi, float) and math.isnan(stoch_rsi) else stoch_rsi
        indicator.stoch_rsi_signal = None if isinstance(stoch_rsi_signal, float) and math.isnan(stoch_rsi_signal) else stoch_rsi_signal
        indicator.stoch = None if isinstance(stoch, float) and math.isnan(stoch) else stoch
        indicator.stoch_signal = None if isinstance(stoch_signal, float) and math.isnan(stoch_signal) else stoch_signal
        indicator.macd = None if isinstance(macd, float) and math.isnan(macd) else macd
        indicator.macd_signal = None if isinstance(macd_signal, float) and math.isnan(macd_signal) else macd_signal
        indicator.macd_hist = None if isinstance(macd_hist, float) and math.isnan(macd_hist) else macd_hist
        indicator.upper_band = None if isinstance(upper_band, float) and math.isnan(upper_band) else upper_band
        indicator.middle_band = None if isinstance(middle_band, float) and math.isnan(middle_band) else middle_band
        indicator.lower_band = None if isinstance(lower_band, float) and math.isnan(lower_band) else lower_band
        indicator.obv = None if isinstance(obv, float) and math.isnan(obv) else obv
        indicator.ma_9 = None if isinstance(ma_9, float) and math.isnan(ma_9) else ma_9
        indicator.ema_9 = None if isinstance(ema_9, float) and math.isnan(ema_9) else ema_9
        indicator.ma_21 = None if isinstance(ma_21, float) and math.isnan(ma_21) else ma_21
        indicator.ema_21 = None if isinstance(ema_21, float) and math.isnan(ema_21) else ema_21
        indicator.ma_50 = None if isinstance(ma_50, float) and math.isnan(ma_50) else ma_50
        indicator.ema_50 = None if isinstance(ema_50, float) and math.isnan(ema_50) else ema_50
        indicator.ma_200 = None if isinstance(ma_200, float) and math.isnan(ma_200) else ma_200
        indicator.ema_200 = None if isinstance(ema_200, float) and math.isnan(ema_200) else ema_200
        indicator.peak = None if isinstance(peak, float) and math.isnan(peak) else peak
        indicator.peak_20 = None if isinstance(peak_20, float) and math.isnan(peak_20) else peak_20
        indicator.peak_60 = None if isinstance(peak_60, float) and math.isnan(peak_60) else peak_60
        indicator.peak_120 = None if isinstance(peak_120, float) and math.isnan(peak_120) else peak_120
        indicator.peak_180 = None if isinstance(peak_180, float) and math.isnan(peak_180) else peak_180
        indicator.save()
        return indicator, created

    _BULK_FIELDS = (
        'rsi', 'rsi_ma', 'rsi_ema', 'stoch_rsi', 'stoch_rsi_signal',
        'stoch', 'stoch_signal', 'macd', 'macd_signal', 'macd_hist',
        'upper_band', 'middle_band', 'lower_band', 'obv',
        'ma_9', 'ema_9', 'ma_21', 'ema_21', 'ma_50', 'ema_50', 'ma_200', 'ema_200',
        'peak', 'peak_20', 'peak_60', 'peak_120', 'peak_180',
    )

    @classmethod
    def bulk_build(cls, symbol, rows):
        """Bulk upsert indicator rows. rows: iterable of dicts with 'date' + any of _BULK_FIELDS.

        NaN values are coerced to None. Replaces N×(get_or_create+save) with chunked
        insert_many statements. Returns count written.
        """
        rows = [r for r in rows if r is not None]
        if not rows:
            return 0
        payload = []
        for r in rows:
            row = {'symbol': symbol, 'date': r['date']}
            for f in cls._BULK_FIELDS:
                if f in r:
                    v = _clean_num(r[f])
                    # OBV is an integer column; cast safely.
                    if f == 'obv' and v is not None:
                        v = int(v)
                    row[f] = v
            payload.append(row)
        with DB.atomic():
            for i in range(0, len(payload), _BULK_CHUNK):
                cls.insert_many(payload[i:i + _BULK_CHUNK]).on_conflict_replace().execute()
        return len(payload)

    def stock(self):
        from database.models.core import Stock
        return Stock.get_by_id(self.symbol)

    def previous_indicator(self):
        query = Indicator.select().where(Indicator.date < self.date).order_by(Indicator.date.desc()).limit(1)
        return query.get() if query.exists() else None

    def price_history(self):
        return PriceHistory.get_or_none(PriceHistory.symbol == self.symbol, PriceHistory.date == self.date)

    class Meta:
        table_name = 'indicators'
        indexes = (
            (('symbol', 'date'), True), 
            (('symbol',), False),
            (('date',), False),
        )
        primary_key = CompositeKey('symbol', 'date')

class WeeklyPriceHistory(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='weekly_price_history')
    date = DateField()
    open = DecimalField(max_digits=10, decimal_places=2)
    high = DecimalField(max_digits=10, decimal_places=2)
    low = DecimalField(max_digits=10, decimal_places=2)
    close = DecimalField(max_digits=10, decimal_places=2)
    volume = IntegerField()
    pulled_at = DateTimeField(null=True)

    @classmethod
    def build(cls, symbol, date, open, high, low, close, volume):
        price, created = cls.get_or_create(symbol = symbol, date = date)
        price.open = open
        price.high = high
        price.low = low
        price.close = close
        price.volume = volume
        price.pulled_at = datetime.now()
        price.save()
        return price
    
    def stock(self):
        from database.models.core import Stock
        return Stock.get_by_id(self.symbol)

    def indicator(self):
        return WeeklyIndicator.get_or_none(WeeklyIndicator.symbol == self.symbol, WeeklyIndicator.date == self.date)

    class Meta:
        table_name = 'weekly_price_history'
        indexes = (
            (('symbol', 'date'), True),
            (('symbol',), False),
            (('date',), False),
            (('symbol', 'date', 'close'), False),
            (('date', 'close'), False),
        )
        primary_key = CompositeKey('symbol', 'date')

class WeeklyIndicator(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='weekly_indicators')
    date = DateField()
    rsi = DecimalField(max_digits=6, decimal_places=2, null=True)
    rsi_ma = DecimalField(max_digits=6, decimal_places=2, null=True)
    rsi_ema = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch_rsi = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch_rsi_signal = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch = DecimalField(max_digits=6, decimal_places=2, null=True)
    stoch_signal = DecimalField(max_digits=6, decimal_places=2, null=True)
    macd = DecimalField(max_digits=7, decimal_places=3, null=True)
    macd_signal = DecimalField(max_digits=7, decimal_places=3, null=True)
    macd_hist = DecimalField(max_digits=7, decimal_places=3, null=True)
    upper_band = DecimalField(max_digits=9, decimal_places=3, null=True)
    middle_band = DecimalField(max_digits=9, decimal_places=3, null=True)
    lower_band = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_9 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_9 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_21 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_21 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_50 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_50 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ma_200 = DecimalField(max_digits=9, decimal_places=3, null=True)
    ema_200 = DecimalField(max_digits=9, decimal_places=3, null=True)
    peak = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_20 = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_60 = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_120 = DecimalField(max_digits=9, decimal_places=2, null=True)
    peak_180 = DecimalField(max_digits=9, decimal_places=2, null=True)
    obv = IntegerField(null=True)

    @classmethod
    def build(cls, symbol, date, rsi=None, rsi_ma=None, rsi_ema=None, stoch_rsi=None, stoch_rsi_signal=None, stoch=None, stoch_signal=None, macd=None, macd_signal=None, macd_hist=None,
              upper_band=None, middle_band=None, lower_band=None, obv=None, ma_9=None, ema_9=None, ma_21=None, ema_21=None, ma_50=None, ema_50=None, ma_200=None, ema_200=None,
              peak=None, peak_20=None, peak_60=None, peak_120=None, peak_180=None):
        indicator, created = cls.get_or_create(symbol=symbol, date=date)
        indicator.rsi = None if isinstance(rsi, float) and math.isnan(rsi) else rsi
        indicator.rsi_ma = None if isinstance(rsi_ma, float) and math.isnan(rsi_ma) else rsi_ma
        indicator.rsi_ema = None if isinstance(rsi_ema, float) and math.isnan(rsi_ema) else rsi_ema
        indicator.stoch_rsi = None if isinstance(stoch_rsi, float) and math.isnan(stoch_rsi) else stoch_rsi
        indicator.stoch_rsi_signal = None if isinstance(stoch_rsi_signal, float) and math.isnan(stoch_rsi_signal) else stoch_rsi_signal
        indicator.stoch = None if isinstance(stoch, float) and math.isnan(stoch) else stoch
        indicator.stoch_signal = None if isinstance(stoch_signal, float) and math.isnan(stoch_signal) else stoch_signal
        indicator.macd = None if isinstance(macd, float) and math.isnan(macd) else macd
        indicator.macd_signal = None if isinstance(macd_signal, float) and math.isnan(macd_signal) else macd_signal
        indicator.macd_hist = None if isinstance(macd_hist, float) and math.isnan(macd_hist) else macd_hist
        indicator.upper_band = None if isinstance(upper_band, float) and math.isnan(upper_band) else upper_band
        indicator.middle_band = None if isinstance(middle_band, float) and math.isnan(middle_band) else middle_band
        indicator.lower_band = None if isinstance(lower_band, float) and math.isnan(lower_band) else lower_band
        indicator.obv = None if isinstance(obv, float) and math.isnan(obv) else obv
        indicator.ma_9 = None if isinstance(ma_9, float) and math.isnan(ma_9) else ma_9
        indicator.ema_9 = None if isinstance(ema_9, float) and math.isnan(ema_9) else ema_9
        indicator.ma_21 = None if isinstance(ma_21, float) and math.isnan(ma_21) else ma_21
        indicator.ema_21 = None if isinstance(ema_21, float) and math.isnan(ema_21) else ema_21
        indicator.ma_50 = None if isinstance(ma_50, float) and math.isnan(ma_50) else ma_50
        indicator.ema_50 = None if isinstance(ema_50, float) and math.isnan(ema_50) else ema_50
        indicator.ma_200 = None if isinstance(ma_200, float) and math.isnan(ma_200) else ma_200
        indicator.ema_200 = None if isinstance(ema_200, float) and math.isnan(ema_200) else ema_200
        indicator.peak = None if isinstance(peak, float) and math.isnan(peak) else peak
        indicator.peak_20 = None if isinstance(peak_20, float) and math.isnan(peak_20) else peak_20
        indicator.peak_60 = None if isinstance(peak_60, float) and math.isnan(peak_60) else peak_60
        indicator.peak_120 = None if isinstance(peak_120, float) and math.isnan(peak_120) else peak_120
        indicator.peak_180 = None if isinstance(peak_180, float) and math.isnan(peak_180) else peak_180
        indicator.save()
        return indicator, created

    _BULK_FIELDS = Indicator._BULK_FIELDS

    @classmethod
    def bulk_build(cls, symbol, rows):
        """Bulk upsert weekly indicator rows. See Indicator.bulk_build."""
        rows = [r for r in rows if r is not None]
        if not rows:
            return 0
        payload = []
        for r in rows:
            row = {'symbol': symbol, 'date': r['date']}
            for f in cls._BULK_FIELDS:
                if f in r:
                    v = _clean_num(r[f])
                    if f == 'obv' and v is not None:
                        v = int(v)
                    row[f] = v
            payload.append(row)
        with DB.atomic():
            for i in range(0, len(payload), _BULK_CHUNK):
                cls.insert_many(payload[i:i + _BULK_CHUNK]).on_conflict_replace().execute()
        return len(payload)

    def stock(self):
        from database.models.core import Stock
        return Stock.get_by_id(self.symbol)

    def previous_indicator(self):
        query = WeeklyIndicator.select().where(WeeklyIndicator.date < self.date).order_by(WeeklyIndicator.date.desc()).limit(1)
        return query.get() if query.exists() else None

    def price_history(self):
        return WeeklyPriceHistory.get_or_none(WeeklyPriceHistory.symbol == self.symbol, WeeklyPriceHistory.date == self.date)

    class Meta:
        table_name = 'weekly_indicators'
        indexes = (
            (('symbol', 'date'), True), 
            (('symbol',), False),
            (('date',), False),
        )
        primary_key = CompositeKey('symbol', 'date')

class Historical(BaseModel):
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='historicals')
    date = DateField()
    pe = DecimalField(max_digits=10, decimal_places=2)
    fcf = IntegerField()
    margins = DecimalField(max_digits=10, decimal_places=2)
    roe = DecimalField(max_digits=10, decimal_places=2)
    debt_equity = DecimalField(max_digits=10, decimal_places=2)

    @classmethod
    def build(cls, symbol, date):
        price, created = cls.get_or_create(symbol = symbol, date = date)
        return price

    class Meta:
        table_name = 'historicals'
        indexes = (
            (('symbol', 'date'), True),
            (('symbol',), False),
            (('date',), False),
        )
        primary_key = CompositeKey('symbol', 'date')


class RollingWeeklyIndicator(BaseModel):
    """Daily-keyed rolling weekly indicators.

    For each (symbol, daily_date), holds RSI(14) / MACD(12,26,9) computed on a
    14-bar non-overlapping 5-day ladder ending at date - 1 trading day. The
    ladder excludes today's daily bar entirely, which makes these indicators
    immune to intra-day partial-bar instability and to the calendar-week
    boundary that drives the COHR-class Monday whiplash.

    See experiments/rolling_weekly/FINDINGS.md for the validation experiment
    that motivated this table. The ladder length is 200 bars (1000 trading
    days, ~4y) for full RSI(14)+MACD(12,26,9) convergence and partial EMA200.

    The lookup keyed on `date` (not week_start) — every trading day has its own
    rolling weekly indicator row. The relationship to the existing
    WeeklyIndicator table is that this row replaces "the WeeklyIndicator the
    score function looks up at scoring time"; the legacy WeeklyIndicator table
    stays populated for backward-compat display paths.

    Population: bulk backfill via experiments/rolling_weekly/02_build_rolling_indicators.py
    or its production equivalent in `trader update`. Daily incremental: each
    cron cycle adds one row per stock for today's date. Today's value depends
    only on completed prior-day data, so it's stable from 4pm settlement onward.
    """
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol',
                                 backref='rolling_weekly_indicators')
    date = DateField()  # daily date — the score date this indicator serves
    # Current ladder (most recent bar ends at date-1)
    w_rsi    = DecimalField(max_digits=6, decimal_places=2, null=True)
    w_macd   = DecimalField(max_digits=8, decimal_places=4, null=True)
    w_macd_h = DecimalField(max_digits=8, decimal_places=4, null=True)
    # Previous ladder (most recent bar ends at date-6) — for momentum_bias
    prev_w_rsi    = DecimalField(max_digits=6, decimal_places=2, null=True)
    prev_w_macd   = DecimalField(max_digits=8, decimal_places=4, null=True)
    prev_w_macd_h = DecimalField(max_digits=8, decimal_places=4, null=True)
    updated_at = DateTimeField(default=datetime.now, null=True)

    class Meta:
        table_name = 'rolling_weekly_indicators'
        indexes = (
            (('symbol', 'date'), True),  # unique per (symbol, date)
        )

    @classmethod
    def ensure_schema(cls):
        DB.create_tables([cls], safe=True)
        # Idempotent column-add migrations for any future schema bumps.
        # (No additional ALTER TABLE statements needed at initial ship.)

    @classmethod
    def bulk_build(cls, rows):
        """Bulk upsert rolling indicator rows. rows: iterable of dicts with
        symbol, date, w_rsi, w_macd, w_macd_h, prev_w_rsi, prev_w_macd,
        prev_w_macd_h. NaN/None coerced; replaces N×save() with chunked inserts.
        Returns count written."""
        rows = [r for r in rows if r is not None]
        if not rows:
            return 0
        payload = []
        for r in rows:
            row = {
                'symbol': r['symbol'],
                'date': r['date'],
                'w_rsi':    _clean_num(r.get('w_rsi')),
                'w_macd':   _clean_num(r.get('w_macd')),
                'w_macd_h': _clean_num(r.get('w_macd_h')),
                'prev_w_rsi':    _clean_num(r.get('prev_w_rsi')),
                'prev_w_macd':   _clean_num(r.get('prev_w_macd')),
                'prev_w_macd_h': _clean_num(r.get('prev_w_macd_h')),
                'updated_at': datetime.now(),
            }
            payload.append(row)
        with DB.atomic():
            for i in range(0, len(payload), _BULK_CHUNK):
                cls.insert_many(payload[i:i + _BULK_CHUNK]).on_conflict_replace().execute()
        return len(payload)

