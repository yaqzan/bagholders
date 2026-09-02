from trader_database import BaseModel
from peewee import AutoField, ForeignKeyField, DeferredForeignKey, DateField
from datetime import date

class Position(BaseModel):
    id = AutoField(primary_key=True)
    symbol = DeferredForeignKey('Stock', column_name='symbol', field='symbol', backref='positions')
    buy_date = DateField(default=date.today)
    sell_date = DateField()

    @classmethod
    def build(cls, symbol):
        position, created = cls.get_or_create(symbol=symbol, sell_date=None)
        if created:
            print(f"Bought {symbol} position")
        return position

    @classmethod
    def sell(cls, symbol):
        position = cls.get(cls.symbol == symbol, cls.sell_date.is_null(True))
        if position:
            position.sell_date = date.today()
            position.save()
            print(f"Sold {symbol} position")

    @classmethod
    def active_positions(cls):
        return [position.symbol.symbol for position in cls.select(cls.symbol).where(cls.sell_date.is_null(True))]

    class Meta:
        table_name = 'positions'
        indexes = (
            (('sell_date',), False),     # For active positions (NULL sell_date)
            (('symbol', 'sell_date'), False), # For symbol + active status
            (('buy_date',), False),      # For buy date queries
        )

