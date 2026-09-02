from datetime import date

import trader


def _daily_row(open_=100.0, high=101.0, low=99.0, close=100.0, volume=0):
    return {
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    }


def test_nyse_closed_weekday_calendar_covers_future_holidays():
    assert trader._is_nyse_closed_weekday(date(2026, 5, 25)) is True
    assert trader._is_nyse_closed_weekday(date(2026, 7, 3)) is True
    assert trader._is_nyse_closed_weekday(date(2026, 4, 3)) is True
    assert trader._is_nyse_closed_weekday(date(2026, 5, 22)) is False


def test_us_market_holiday_filter_keeps_foreign_suffix_symbols():
    kept, skipped = trader._filter_symbols_for_us_market_holiday(
        ["AAPL", "SPY", "ENA.V", "HPS-A.TO"],
        date(2026, 5, 25),
    )

    assert kept == ["ENA.V", "HPS-A.TO"]
    assert skipped == ["AAPL", "SPY"]


def test_us_market_holiday_filter_noops_on_regular_trading_day():
    symbols = ["AAPL", "ENA.V"]

    kept, skipped = trader._filter_symbols_for_us_market_holiday(
        symbols,
        date(2026, 5, 22),
    )

    assert kept == symbols
    assert skipped == []


def test_zero_volume_us_holiday_bar_is_rejected():
    row = trader._build_valid_price_history_row(
        "AAPL",
        date(2026, 5, 25),
        _daily_row(volume=0),
    )

    assert row is None


def test_zero_volume_us_bar_is_allowed_on_regular_trading_day():
    row = trader._build_valid_price_history_row(
        "AAPL",
        date(2026, 5, 22),
        _daily_row(volume=0),
    )

    assert row["date"] == date(2026, 5, 22)
    assert row["volume"] == 0


def test_foreign_suffix_bar_is_rejected_on_us_holiday():
    row = trader._build_valid_price_history_row(
        "ENA.V",
        date(2026, 5, 25),
        _daily_row(volume=18870),
    )

    assert row is None


def test_premarket_single_symbol_skips_us_holiday_without_yfinance(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 25)

    def fail_ticker(symbol):
        raise AssertionError(f"Ticker should not be called for {symbol}")

    monkeypatch.setattr(trader, "date", FixedDate)
    monkeypatch.setattr(trader.yf, "Ticker", fail_ticker)

    assert trader.Trader().pull_premarket_data("AAPL") is True


def test_premarket_bulk_skips_us_holiday_without_yfinance(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 25)

    def fail_download(*args, **kwargs):
        raise AssertionError("yf.download should not be called for US symbols on NYSE holidays")

    monkeypatch.setattr(trader, "date", FixedDate)
    monkeypatch.setattr(trader.yf, "download", fail_download)

    got_data, no_data, batch_failed = trader.Trader().pull_premarket_bulk(["AAPL", "SPY", "ENA.V"])

    assert got_data == set()
    assert no_data == {"AAPL", "SPY", "ENA.V"}
    assert batch_failed == set()
