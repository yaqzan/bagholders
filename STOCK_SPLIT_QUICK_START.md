# Stock Split Handling - Quick Start Guide

## TL;DR

**Current Problem:** You're using `auto_adjust=False`, so stock splits create price discontinuities that break your indicators.

**Quick Fix (Recommended):** Change one line in `trader.py`:
```python
# Line 52 - Change this:
data = yf.download([symbol], period=period, interval='1d', rounding=True, auto_adjust=False)

# To this:
data = yf.download([symbol], period=period, interval='1d', rounding=True, auto_adjust=True)
```

Then run a one-time migration to repull all price history.

---

## Your Options (Ranked by Simplicity)

### ✅ Option 1: Use Adjusted Prices (Simplest - Recommended if you don't need raw prices)
- **Change:** 1 line of code (`auto_adjust=True`)
- **Migration:** Repull all price history once
- **Pros:** Zero complexity, automatic handling
- **Cons:** Lose raw (unadjusted) prices
- **Best for:** Most use cases where raw prices aren't needed

### Option 4: Hybrid Approach (Best if you need raw prices)
- **Change:** Modify `calculate_indicators()` to fetch adjusted prices on-the-fly
- **Migration:** None needed (keeps existing raw prices)
- **Pros:** Keep raw prices, indicators always correct
- **Cons:** Slight performance overhead
- **Best for:** If you need raw prices for display/tracking

### Option 3: Detect and Backfill (Most control)
- **Change:** Add split detection + backfill logic
- **Migration:** Detect splits, adjust affected periods
- **Pros:** Full control, can handle retroactively
- **Cons:** Most complex, risk of errors
- **Best for:** Advanced use cases with specific requirements

### Option 2: Store Both (Maximum flexibility)
- **Change:** Database schema + dual storage
- **Migration:** Major database changes
- **Pros:** Full flexibility
- **Cons:** High complexity, more storage
- **Best for:** Long-term if you need both raw and adjusted

---

## Implementation Steps

### If Choosing Option 1 (Recommended):

1. **Make the change:**
   ```python
   # In trader.py, line 52
   data = yf.download([symbol], period=period, interval='1d', rounding=True, auto_adjust=True)
   ```

2. **Run migration** (one-time):
   ```python
   from stock_split_implementations import migrate_all_to_adjusted_prices
   migrate_all_to_adjusted_prices()
   ```

3. **Done!** All future pulls will use adjusted prices automatically.

### If Choosing Option 4 (Hybrid):

1. **Keep** `auto_adjust=False` in `pull_price_history()`
2. **Modify** `calculate_indicators()` to fetch adjusted prices (see `stock_split_implementations.py`)
3. **No migration needed** - raw prices stay as-is

### If Choosing Option 3 (Detect & Backfill):

1. Add split detection to your update routine
2. When split detected, adjust historical prices
3. Recalculate indicators
4. See `stock_split_implementations.py` for detection logic

---

## Testing Split Detection

You can test split detection on any stock:

```python
from stock_split_implementations import validate_split_detection

# Test on Apple (has had multiple splits)
validate_split_detection("AAPL")

# Test on any stock
validate_split_detection("TSLA")
```

---

## Questions to Answer

Before choosing, ask yourself:

1. **Do I need raw (unadjusted) prices?**
   - For tax reporting? Position tracking? Display?
   - **If NO** → Use Option 1
   - **If YES** → Use Option 4 or Option 2

2. **How much complexity can I handle?**
   - **Low** → Option 1
   - **Medium** → Option 4
   - **High** → Option 3 or Option 2

3. **Do I need to handle historical splits retroactively?**
   - **No** → Option 1 or Option 4
   - **Yes** → Option 3

---

## Files Created

1. **STOCK_SPLIT_ANALYSIS.md** - Detailed analysis of all options
2. **stock_split_implementations.py** - Ready-to-use code examples
3. **STOCK_SPLIT_QUICK_START.md** - This file (quick reference)

---

## Recommendation

**Start with Option 1** unless you have a specific need for raw prices. It's the simplest and most reliable approach.

You can always migrate to a more complex solution later if needed.


