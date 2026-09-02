"""
Stock Split Handling Implementation Examples

Choose the approach that fits your needs from STOCK_SPLIT_ANALYSIS.md
"""

import yfinance as yf
from datetime import date, timedelta
from database import Stock, PriceHistory, Indicator
from colorama import Fore
from peewee import fn


class SplitHandler:
    """Utility class for detecting and handling stock splits"""
    
    @staticmethod
    def detect_split_yfinance(symbol):
        """
        Detect splits using yfinance split data.
        Returns list of splits with date and ratio.
        """
        ticker = yf.Ticker(symbol)
        splits = ticker.splits
        
        if splits.empty:
            return []
        
        split_events = []
        for split_date, split_ratio in splits.items():
            split_events.append({
                'date': split_date.date() if hasattr(split_date, 'date') else split_date,
                'ratio': float(split_ratio)
            })
        
        return split_events
    
    @staticmethod
    def detect_split_price_discontinuity(symbol, lookback_days=5):
        """
        Detect splits by analyzing price discontinuities.
        Returns potential split date and ratio, or None.
        """
        prices = (PriceHistory.select()
                 .where(PriceHistory.symbol == symbol)
                 .order_by(PriceHistory.date.desc())
                 .limit(lookback_days))
        
        if prices.count() < 2:
            return None
        
        prices_list = list(prices)
        
        for i in range(len(prices_list) - 1):
            prev_close = float(prices_list[i + 1].close)
            curr_close = float(prices_list[i].close)
            prev_volume = prices_list[i + 1].volume
            curr_volume = prices_list[i].volume
            
            # Check for large price drop (potential split)
            if prev_close > 0 and curr_close > 0:
                price_ratio = prev_close / curr_close
                
                # Likely split if: large price drop AND volume doesn't spike dramatically
                if price_ratio > 1.3:  # 30%+ drop
                    volume_ratio = curr_volume / prev_volume if prev_volume > 0 else 1
                    
                    # Volume should be similar (not a real crash)
                    if 0.5 < volume_ratio < 2.0:
                        return {
                            'date': prices_list[i].date,
                            'ratio': price_ratio,
                            'detected': True,
                            'method': 'price_discontinuity'
                        }
        
        return None
    
    @staticmethod
    def check_recent_splits(symbol, days=30):
        """Check if any splits occurred in the last N days"""
        splits = SplitHandler.detect_split_yfinance(symbol)
        recent_splits = [
            s for s in splits 
            if (date.today() - s['date']).days <= days
        ]
        return recent_splits


# ============================================================================
# OPTION 1: Simple - Use auto_adjust=True
# ============================================================================

def pull_price_history_adjusted(symbol, period='max'):
    """
    Modified pull_price_history to use adjusted prices.
    Change auto_adjust=False to auto_adjust=True
    """
    from trader import Trader
    import yfinance.shared as shared
    from time import sleep
    
    attempts = 0
    while attempts < 5:
        # KEY CHANGE: auto_adjust=True
        data = yf.download([symbol], period=period, interval='1d',
                          rounding=True, auto_adjust=True, progress=False)
        if len(data) > 0:
            break
        if 'YFPricesMissingError' in shared._ERRORS.get(symbol, {}):
            return
        attempts += 1
        print(Fore.YELLOW + '.', end='')
        sleep(1)
    
    if len(data) == 0:
        print(Fore.RED + f'No data found for {symbol}')
        return
    
    for index, row in data.iterrows():
        PriceHistory.build(
            symbol, index, 
            row['Open'][symbol], 
            row['High'][symbol], 
            row['Low'][symbol], 
            row['Close'][symbol], 
            row['Volume'][symbol]
        )
    print(Fore.GREEN + f'Adjusted price history updated for {symbol}')


def migrate_all_to_adjusted_prices():
    """
    One-time migration script to repull all stocks with adjusted prices.
    
    Usage: Call this once after switching to auto_adjust=True
    """
    stocks = Stock.select()
    
    for stock in stocks:
        print(f"Migrating {stock.symbol}...")
        
        # Delete existing price history
        PriceHistory.delete().where(PriceHistory.symbol == stock.symbol).execute()
        
        # Delete indicators (will be recalculated)
        Indicator.delete().where(Indicator.symbol == stock.symbol).execute()
        
        # Repull with adjusted prices
        pull_price_history_adjusted(stock.symbol, period='max')
        
        # Recalculate indicators
        stock.calculate_indicators_and_scores()
        
        print(Fore.GREEN + f"Completed {stock.symbol}")


# ============================================================================
# OPTION 4: Hybrid - Adjusted for calculations, raw for storage
# ============================================================================

def calculate_indicators_with_adjusted_prices(stock, full=False):
    """
    Modified calculate_indicators that uses adjusted prices from yfinance
    while keeping raw prices in database.
    """
    from database.models.technical import Indicator
    import numpy as np
    import talib
    
    # Fetch adjusted prices for calculations
    ticker = yf.Ticker(stock.symbol)
    adjusted_data = ticker.history(period='max', auto_adjust=True)
    
    if adjusted_data.empty or len(adjusted_data) < 14:
        return
    
    # Convert to arrays for TA-Lib
    close_prices = adjusted_data['Close'].values
    highs = adjusted_data['High'].values
    lows = adjusted_data['Low'].values
    volumes = adjusted_data['Volume'].values
    dates = adjusted_data.index
    
    # Get matching dates from PriceHistory to ensure alignment
    db_prices = PriceHistory.select().where(
        PriceHistory.symbol == stock.symbol
    ).order_by(PriceHistory.date.asc())
    
    db_dates = {ph.date: ph for ph in db_prices}
    
    # Calculate indicators
    rsi_values = talib.RSI(close_prices, timeperiod=14)
    fastk, fastd = talib.STOCHRSI(close_prices, timeperiod=14, 
                                   fastk_period=3, fastd_period=3, fastd_matype=0)
    slowk, slowd = talib.STOCH(highs, lows, close_prices, 
                               fastk_period=14, slowk_period=3, 
                               slowk_matype=0, slowd_period=3, slowd_matype=0)
    macd, macd_signal, macd_hist = talib.MACD(close_prices, 
                                              fastperiod=12, slowperiod=26, signalperiod=9)
    upperband, middleband, lowerband = talib.BBANDS(close_prices, timeperiod=20, 
                                                     nbdevup=2, nbdevdn=2, matype=0)
    obv_values = talib.OBV(close_prices, volumes)
    
    # Store indicators, matching to database dates
    for idx, dt in enumerate(dates):
        db_date = dt.date() if hasattr(dt, 'date') else dt
        
        # Only store if we have price history for this date
        if db_date in db_dates:
            Indicator.build(
                symbol=stock.symbol,
                date=db_date,
                rsi=rsi_values[idx] if not np.isnan(rsi_values[idx]) else None,
                stoch_rsi=fastk[idx] if not np.isnan(fastk[idx]) else None,
                stoch_rsi_signal=fastd[idx] if not np.isnan(fastd[idx]) else None,
                stoch=slowk[idx] if not np.isnan(slowk[idx]) else None,
                stoch_signal=slowd[idx] if not np.isnan(slowd[idx]) else None,
                macd=macd[idx] if not np.isnan(macd[idx]) else None,
                macd_signal=macd_signal[idx] if not np.isnan(macd_signal[idx]) else None,
                macd_hist=macd_hist[idx] if not np.isnan(macd_hist[idx]) else None,
                upper_band=upperband[idx] if not np.isnan(upperband[idx]) else None,
                middle_band=middleband[idx] if not np.isnan(middleband[idx]) else None,
                lower_band=lowerband[idx] if not np.isnan(lowerband[idx]) else None,
                obv=obv_values[idx] if not np.isnan(obv_values[idx]) else None,
            )


# ============================================================================
# OPTION 3: Detect and Backfill (if you want manual control)
# ============================================================================

def adjust_price_history_for_split(symbol, split_date, split_ratio):
    """
    Adjust all historical prices before split_date by split_ratio.
    
    Args:
        symbol: Stock symbol
        split_date: Date of the split
        split_ratio: Ratio to adjust by (e.g., 2.0 for 2-for-1 split)
    
    Note: For 2-for-1 split, prices before split should be divided by 2.
    For 3-for-1 split, prices should be divided by 3.
    """
    prices_to_adjust = (PriceHistory.select()
                       .where(
                           (PriceHistory.symbol == symbol) &
                           (PriceHistory.date < split_date)
                       )
                       .order_by(PriceHistory.date.desc()))
    
    adjusted_count = 0
    for price in prices_to_adjust:
        # Adjust prices (multiply by inverse of split ratio)
        # If split is 2-for-1, ratio is 2, so we multiply by 1/2
        adjustment_factor = 1.0 / split_ratio
        
        price.open = price.open * adjustment_factor
        price.high = price.high * adjustment_factor
        price.low = price.low * adjustment_factor
        price.close = price.close * adjustment_factor
        price.volume = int(price.volume * split_ratio)  # Volume increases
        price.save()
        adjusted_count += 1
    
    print(Fore.GREEN + f"Adjusted {adjusted_count} price records for {symbol} split on {split_date}")
    
    # Recalculate all indicators after adjustment
    stock = Stock.get(symbol=symbol)
    stock.calculate_indicators(full=True)
    stock.calculate_scores(weekly=True)
    stock.calculate_scores()


def check_and_handle_splits(symbol):
    """
    Check for recent splits and handle them automatically.
    This would be called during your update_stocks() routine.
    """
    # Check for splits in last 30 days
    recent_splits = SplitHandler.check_recent_splits(symbol, days=30)
    
    if recent_splits:
        print(Fore.YELLOW + f"Detected {len(recent_splits)} split(s) for {symbol}")
        
        for split in recent_splits:
            # Check if we've already adjusted for this split
            # (you might want to track this in a table)
            
            # Adjust historical prices
            adjust_price_history_for_split(
                symbol=symbol,
                split_date=split['date'],
                split_ratio=split['ratio']
            )
            
            print(Fore.GREEN + f"Adjusted for {symbol} split: {split['ratio']}-for-1 on {split['date']}")
    
    return len(recent_splits) > 0


# ============================================================================
# Helper: Validate split detection
# ============================================================================

def validate_split_detection(symbol):
    """
    Compare yfinance split detection vs price discontinuity detection.
    Useful for validating your detection logic.
    """
    yf_splits = SplitHandler.detect_split_yfinance(symbol)
    price_split = SplitHandler.detect_split_price_discontinuity(symbol)
    
    print(f"\n=== Split Detection Validation for {symbol} ===")
    print(f"\nyfinance splits:")
    for split in yf_splits:
        print(f"  {split['date']}: {split['ratio']}-for-1")
    
    print(f"\nPrice discontinuity detection:")
    if price_split:
        print(f"  {price_split['date']}: {price_split['ratio']:.2f}-for-1 (detected)")
    else:
        print(f"  No split detected")
    
    return yf_splits, price_split


if __name__ == "__main__":
    # Example usage
    test_symbol = "AAPL"  # Apple has had splits
    
    # Validate detection
    validate_split_detection(test_symbol)
    
    # Check for recent splits
    splits = SplitHandler.check_recent_splits(test_symbol, days=365)
    print(f"\nRecent splits: {splits}")


