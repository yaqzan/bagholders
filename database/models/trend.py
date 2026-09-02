from trader_database import BaseModel
from peewee import fn, DateField, IntegerField, DateTimeField, DecimalField
from datetime import datetime, date, timedelta
from colorama import Fore

class Trend(BaseModel):
    """Cached market trend data for fast API responses"""
    date = DateField(primary_key=True)
    total_stocks = IntegerField()
    stocks_up = IntegerField()
    stocks_down = IntegerField()
    stocks_neutral = IntegerField()
    avg_score = DecimalField(max_digits=5, decimal_places=2)
    breadth_thrust = DecimalField(max_digits=5, decimal_places=4)
    over_75_count = IntegerField()
    over_80_count = IntegerField()
    under_25_count = IntegerField()
    under_30_count = IntegerField()
    under_20_count = IntegerField()
    over_70_count = IntegerField()
    updated_at = DateTimeField(default=datetime.now)

    @classmethod
    def build(cls, date, **kwargs):
        trend, created = cls.get_or_create(date=date)
        for key, value in kwargs.items():
            setattr(trend, key, value)
        trend.updated_at = datetime.now()
        trend.save()
        return trend, created

    @classmethod
    def calculate_trend_stats(cls, target_date):
        from database.models.core import Stock, Score
        scores_query = Score.select(Score, Stock).join(Stock, on=(Score.symbol == Stock.symbol)).where(Score.date == target_date)
        
        daily_stats = {
            'scores': [],
            'stocks_up': 0,
            'stocks_down': 0,
            'stocks_neutral': 0,
            'total_stocks': 0
        }

        for score in scores_query:
            if score.symbol.revenue is None:
                continue
            if score.overall is None:
                continue
            daily_stats['scores'].append(score.overall)
            daily_stats['total_stocks'] += 1
            daily_change = score.symbol.daily_percentage_change(target_date)
            
            if daily_change > 0:
                daily_stats['stocks_up'] += 1
            elif daily_change < 0:
                daily_stats['stocks_down'] += 1
            else:
                daily_stats['stocks_neutral'] += 1
        
        if daily_stats['total_stocks'] == 0 or len(daily_stats['scores']) == 0:
            return None
        
        scores = daily_stats['scores']
        avg_score = sum(scores) / len(scores) if scores else 0
        breadth_thrust = daily_stats['stocks_up'] / daily_stats['total_stocks'] if daily_stats['total_stocks'] > 0 else 0
        
        statistics = {
            'total_stocks': daily_stats['total_stocks'],
            'stocks_up': daily_stats['stocks_up'],
            'stocks_down': daily_stats['stocks_down'],
            'stocks_neutral': daily_stats['stocks_neutral'],
            'avg_score': round(avg_score, 2),
            'breadth_thrust': round(breadth_thrust, 4),
            'over_75_count': sum(1 for score in scores if score >= 75),
            'over_80_count': sum(1 for score in scores if score >= 80),
            'over_70_count': sum(1 for score in scores if score >= 70),
            'under_25_count': sum(1 for score in scores if score <= 25),
            'under_30_count': sum(1 for score in scores if score <= 30),
            'under_20_count': sum(1 for score in scores if score <= 20)
        }
        
        return cls.build(date=target_date, **statistics)

    @classmethod
    def update_trend_data(cls, full=False):
        last_trend_date = cls.select(fn.MAX(cls.date)).scalar() or date(2000, 1, 1)
        start_date, end_date = date.today(), last_trend_date
        processed_count = 0
        current_date = start_date
        while current_date >= end_date:
            if current_date.weekday() >= 5:  # 5 is Saturday, 6 is Sunday
                current_date -= timedelta(days=1)
                continue
            trend = cls.calculate_trend_stats(current_date)
            if trend:
                processed_count += 1
                print(Fore.GREEN + '.', end='')
            else:
                print(f"No data available for {current_date}")
            current_date -= timedelta(days=1)

        print(f"Completed. Processed {processed_count} dates")

    class Meta:
        table_name = 'trends'
        indexes = (
            (('date',), True),  # Primary key
            (('updated_at',), False),  # For cleanup queries
        )


