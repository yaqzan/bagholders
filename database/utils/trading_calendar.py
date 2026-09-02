"""
NYSE trading calendar utilities.
Hardcoded holiday list for 2023-2026. Update annually.
"""
from datetime import date, timedelta

NYSE_HOLIDAYS = {
    # 2023
    date(2023, 1, 2),   # New Year's (observed)
    date(2023, 1, 16),  # MLK Jr Day
    date(2023, 2, 20),  # Presidents Day
    date(2023, 4, 7),   # Good Friday
    date(2023, 5, 29),  # Memorial Day
    date(2023, 6, 19),  # Juneteenth
    date(2023, 7, 4),   # Independence Day
    date(2023, 9, 4),   # Labor Day
    date(2023, 11, 23), # Thanksgiving
    date(2023, 12, 25), # Christmas
    # 2024
    date(2024, 1, 1),   # New Year's
    date(2024, 1, 15),  # MLK Jr Day
    date(2024, 2, 19),  # Presidents Day
    date(2024, 3, 29),  # Good Friday
    date(2024, 5, 27),  # Memorial Day
    date(2024, 6, 19),  # Juneteenth
    date(2024, 7, 4),   # Independence Day
    date(2024, 9, 2),   # Labor Day
    date(2024, 11, 28), # Thanksgiving
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 1, 1),   # New Year's
    date(2025, 1, 20),  # MLK Jr Day
    date(2025, 2, 17),  # Presidents Day
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 26),  # Memorial Day
    date(2025, 6, 19),  # Juneteenth
    date(2025, 7, 4),   # Independence Day
    date(2025, 9, 1),   # Labor Day
    date(2025, 11, 27), # Thanksgiving
    date(2025, 12, 25), # Christmas
    # 2026
    date(2026, 1, 1),   # New Year's
    date(2026, 1, 19),  # MLK Jr Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}


def is_trading_day(d):
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def trading_days_between(start, end):
    """Count trading days exclusive of start, inclusive of end."""
    if end <= start:
        return 0
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        if is_trading_day(current):
            count += 1
        current += timedelta(days=1)
    return count


def last_trading_day(reference=None):
    """Most recent trading day on or before reference (defaults to today)."""
    d = reference or date.today()
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
