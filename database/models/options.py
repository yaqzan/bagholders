from trader_database import BaseModel
from fileHelper import is_int
from peewee import AutoField, ForeignKeyField, DeferredForeignKey, IntegerField, FloatField, DateTimeField, DateField, CharField, DecimalField, CompositeKey
from datetime import datetime

class Option(BaseModel):
    id = AutoField(primary_key=True)
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='options')
    strike_price = DecimalField(max_digits=10, decimal_places=2)
    expiration_date = DateField()
    option_type = CharField()  # 'CALL' or 'PUT'

    coverage_30d = FloatField(null=True)
    coverage_90d = FloatField(null=True)
    coverage_180d = FloatField(null=True)
    coverage_updated_at = DateTimeField(null=True)
    last_gap_days = IntegerField(null=True)
    last_gap_detected_at = DateTimeField(null=True)

    @classmethod
    def build(cls, symbol, strike_price, expiration_date, option_type):
        option, created = cls.get_or_create(symbol=symbol, strike_price=strike_price, expiration_date=expiration_date, option_type=option_type)
        return option

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        _new_cols = [
            ('coverage_30d', 'FLOAT NULL'),
            ('coverage_90d', 'FLOAT NULL'),
            ('coverage_180d', 'FLOAT NULL'),
            ('coverage_updated_at', 'DATETIME NULL'),
            ('last_gap_days', 'INT NULL'),
            ('last_gap_detected_at', 'DATETIME NULL'),
        ]
        for col, typedef in _new_cols:
            try:
                DB.execute_sql(f'ALTER TABLE options ADD COLUMN {col} {typedef}')
            except Exception:
                pass
        _indexes = [
            ('idx_opt_sym_type_exp_strike', '(symbol, option_type, expiration_date, strike_price)'),
            ('idx_opt_type_exp', '(option_type, expiration_date)'),
        ]
        for idx_name, cols in _indexes:
            try:
                DB.execute_sql(f'CREATE INDEX {idx_name} ON options {cols}')
            except Exception:
                pass

    class Meta:
        table_name = 'options'
        indexes = (
            (('symbol', 'strike_price', 'expiration_date', 'option_type'), True),
            (('symbol', 'expiration_date', 'option_type'), True),
            (('symbol',), False),
            (('expiration_date',), False)
        )

class OptionPrice(BaseModel):
    option = ForeignKeyField(Option, backref='prices')
    date = DateField()
    price = DecimalField(max_digits=8, decimal_places=2)
    volume = IntegerField()
    open_interest = IntegerField()
    iv = DecimalField(max_digits=5, decimal_places=4, null=True)
    updated_at = DateTimeField(default=datetime.now)
    
    @classmethod
    def build(cls, option, date, price, volume, open_interest, implied_volatility):
        try:
            option_price, created = cls.get_or_create(option=option, date=date)
        except Exception as e:
            print(f"Error creating option price: {e}")
        option_price.price = price
        option_price.volume = volume if is_int(volume) else 0
        option_price.open_interest = open_interest if is_int(open_interest) else 0
        option_price.iv = implied_volatility
        option_price.updated_at = datetime.now()
        option_price.save()

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        _indexes = [
            ('idx_op_date_option', '(date, option_id)'),
            # Covering index for forward walk: build last, ~1.5-2GB on 62M rows.
            ('idx_op_covering', '(option_id, date, price, iv, open_interest)'),
        ]
        for idx_name, cols in _indexes:
            try:
                DB.execute_sql(f'CREATE INDEX {idx_name} ON option_prices {cols}')
            except Exception:
                pass

    class Meta:
        table_name = 'option_prices'
        primary_key = CompositeKey('option', 'date')
        indexes = (
            (('option', 'date'), True),
            (('option',), False),
            (('date',), False),
            (('date', 'volume'), False)
        )

