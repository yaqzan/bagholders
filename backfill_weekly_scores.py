#! python3
from database.models.core import Stock, Score
from database.models.technical import WeeklyIndicator
from colorama import init, Fore
from tqdm import tqdm
from datetime import timedelta

init(autoreset=True, convert=True)

def get_week_start(date_obj):
    """Get Monday of the week for a given date"""
    days_since_monday = date_obj.weekday()
    return date_obj - timedelta(days=days_since_monday)

def backfill_weekly_scores(symbol=None, full=False):
    """Calculate weekly scores for all stocks"""
    stocks = [Stock.get(Stock.symbol == symbol)] if symbol else Stock.select()
    
    for stock in tqdm(stocks, desc="Calculating weekly scores"):
        weekly_indicators = WeeklyIndicator.select(WeeklyIndicator.date).where(
            WeeklyIndicator.symbol == stock.symbol
        ).order_by(WeeklyIndicator.date.desc())
        
        if not weekly_indicators.exists():
            print(Fore.YELLOW + f"No weekly indicators for {stock.symbol}")
            continue
        
        for indicator in weekly_indicators:
            week_start = indicator.date
            score, created = Score.build(stock.symbol, week_start)
            
            # Calculate weekly scores using the same methods but with weekly=True
            # We'll need to update the methods to support weekly, but for now
            # let's use a helper approach
            try:
                weekly_trend = stock.calculate_trend_score(week_start, weekly=True)
                if weekly_trend is None:
                    continue

                score.rsi = stock.calculate_rsi_score(week_start, trend_score=weekly_trend, weekly=True)
                score.macd = stock.calculate_macd_score(week_start, weekly=True)
                
                # Calculate composite (only needs RSI + MACD, BB used for squeeze context)
                if None not in (score.rsi, score.macd):
                    score.composite = score.calculate_composite_score()
                
                score.save()
                
                if not full and not created:
                    break
            except Exception as e:
                print(Fore.RED + f"Error calculating weekly scores for {stock.symbol} on {week_start}: {e}")
                continue
        
        print(Fore.GREEN + f"Completed weekly scores for {stock.symbol}")

if __name__ == '__main__':
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else None
    full = '--full' in sys.argv
    backfill_weekly_scores(symbol, full)

