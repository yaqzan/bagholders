#! python3
"""
Backfill all missing weekly price history, indicators, and scores.
"""
from database.models.core import Stock, WeeklyScore
from database.models.technical import PriceHistory, WeeklyPriceHistory, WeeklyIndicator
from colorama import init, Fore
from tqdm import tqdm
from datetime import timedelta
from decimal import Decimal

init(autoreset=True, convert=True)

def get_week_start(date_obj):
    return date_obj - timedelta(days=date_obj.weekday())

def backfill_weekly_price_history():
    """Aggregate daily prices into weekly prices for all stocks"""
    print(Fore.CYAN + "Step 1: Backfilling weekly price history...")
    
    stocks = list(Stock.select())
    total_weeks_created = 0
    
    for stock in tqdm(stocks, desc="Weekly prices"):
        daily_prices = list(PriceHistory.select().where(
            PriceHistory.symbol == stock.symbol
        ).order_by(PriceHistory.date.asc()))
        
        if not daily_prices:
            continue
        
        # Group by week
        weeks = {}
        for price in daily_prices:
            week_start = get_week_start(price.date)
            if week_start not in weeks:
                weeks[week_start] = []
            weeks[week_start].append(price)
        
        # Create/update weekly records
        for week_start, prices in weeks.items():
            # Check if weekly record exists
            existing = WeeklyPriceHistory.get_or_none(
                WeeklyPriceHistory.symbol == stock.symbol,
                WeeklyPriceHistory.date == week_start
            )
            if existing:
                continue
            
            # Aggregate: open from first day, close from last, high/low from all
            prices_sorted = sorted(prices, key=lambda p: p.date)
            WeeklyPriceHistory.create(
                symbol=stock.symbol,
                date=week_start,
                open=prices_sorted[0].open,
                high=max(p.high for p in prices_sorted),
                low=min(p.low for p in prices_sorted),
                close=prices_sorted[-1].close,
                volume=sum(int(p.volume) for p in prices_sorted)
            )
            total_weeks_created += 1
    
    print(Fore.GREEN + f"Created {total_weeks_created} weekly price records")

def backfill_weekly_indicators():
    """Calculate weekly indicators for all weeks with price data"""
    print(Fore.CYAN + "Step 2: Backfilling weekly indicators...")
    
    stocks = list(Stock.select())
    total_calculated = 0
    
    for stock in tqdm(stocks, desc="Weekly indicators"):
        # Check existing weekly indicators
        existing_dates = set(
            wi.date for wi in WeeklyIndicator.select(WeeklyIndicator.date).where(
                WeeklyIndicator.symbol == stock.symbol
            )
        )
        
        # Get all weekly price dates
        weekly_dates = [
            wp.date for wp in WeeklyPriceHistory.select(WeeklyPriceHistory.date).where(
                WeeklyPriceHistory.symbol == stock.symbol
            )
        ]
        
        # Find missing
        missing = set(weekly_dates) - existing_dates
        if not missing:
            continue
        
        # Calculate indicators (uses Stock.calculate_indicators with weekly=True)
        try:
            stock.calculate_indicators(full=True, weekly=True)
            total_calculated += len(missing)
        except Exception as e:
            print(Fore.RED + f"Error calculating indicators for {stock.symbol}: {e}")
    
    print(Fore.GREEN + f"Calculated weekly indicators for {total_calculated} weeks")

def backfill_weekly_scores():
    """Calculate weekly scores for all weeks with indicators"""
    print(Fore.CYAN + "Step 3: Backfilling weekly scores...")
    
    stocks = list(Stock.select())
    total_created = 0
    
    for stock in tqdm(stocks, desc="Weekly scores"):
        # Get all weekly indicator dates
        weekly_inds = list(WeeklyIndicator.select(WeeklyIndicator.date).where(
            WeeklyIndicator.symbol == stock.symbol
        ).order_by(WeeklyIndicator.date.desc()))
        
        for wi in weekly_inds:
            week_start = wi.date
            
            # Check if weekly score exists and is complete
            ws = WeeklyScore.get_or_none(
                WeeklyScore.symbol == stock.symbol,
                WeeklyScore.date == week_start
            )
            
            if ws and ws.composite is not None:
                continue
            
            # Create or update weekly score
            if not ws:
                ws, _ = WeeklyScore.build(stock.symbol, week_start)
            
            try:
                # Calculate weekly RSI and MACD
                if ws.rsi is None:
                    weekly_trend = stock.calculate_trend_score(week_start, weekly=True)
                    ws.rsi = stock.calculate_rsi_score(week_start, trend_score=weekly_trend, weekly=True)
                
                if ws.macd is None:
                    ws.macd = stock.calculate_macd_score(week_start, weekly=True)
                
                # Calculate composite
                if ws.rsi is not None and ws.macd is not None:
                    ws.composite = ws.calculate_composite_score()
                    ws.save()
                    total_created += 1
            except Exception as e:
                print(Fore.RED + f"Error for {stock.symbol} {week_start}: {e}")
    
    print(Fore.GREEN + f"Created/updated {total_created} weekly scores")

def main():
    print(Fore.CYAN + "=" * 50)
    print(Fore.CYAN + "Weekly Data Backfill")
    print(Fore.CYAN + "=" * 50)
    
    backfill_weekly_price_history()
    backfill_weekly_indicators()
    backfill_weekly_scores()
    
    print(Fore.GREEN + "\n" + "=" * 50)
    print(Fore.GREEN + "Backfill complete!")
    print(Fore.GREEN + "=" * 50)

if __name__ == '__main__':
    main()

