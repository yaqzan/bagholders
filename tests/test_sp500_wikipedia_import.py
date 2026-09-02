from datetime import date

import pandas as pd

from experiments.legacy_oos.sp500_wikipedia_import import (
    dedupe_for_stock_import,
    normalize_symbol,
    parse_removal_events,
)


def test_normalize_symbol_for_trader_yfinance_style():
    assert normalize_symbol("brk.b") == "BRK-B"
    assert normalize_symbol(" msft ") == "MSFT"
    assert normalize_symbol(None) == ""


def test_parse_removal_events_filters_window_and_missing_removed_symbol():
    table = pd.DataFrame(
        {
            "effective date": ["2014-12-31", "2015-01-27", "2019-12-23", "2020-01-01"],
            "added ticker": ["AAA", "FMC", "LYV", "PAYC"],
            "added security": ["A", "FMC Corp", "Live Nation", "Paycom"],
            "removed ticker": ["OLD", "COV", "AMG", "WCG"],
            "removed security": ["Old Co", "Covidien", "Affiliated Managers Group", "WellCare"],
            "reason": ["before window", "acquired", "market cap", "exclusive end"],
        }
    )

    events = parse_removal_events(table, date(2015, 1, 1), date(2020, 1, 1))

    assert [(event.removed_at, event.symbol, event.name) for event in events] == [
        (date(2015, 1, 27), "COV", "Covidien"),
        (date(2019, 12, 23), "AMG", "Affiliated Managers Group"),
    ]


def test_dedupe_for_stock_import_keeps_earliest_removal():
    table = pd.DataFrame(
        {
            "effective date": ["2016-01-01", "2017-01-01"],
            "added ticker": ["AAA", "BBB"],
            "added security": ["A", "B"],
            "removed ticker": ["XYZ", "XYZ"],
            "removed security": ["X first", "X second"],
            "reason": ["first", "second"],
        }
    )
    events = parse_removal_events(table, date(2015, 1, 1), date(2020, 1, 1))

    deduped = dedupe_for_stock_import(events)

    assert len(deduped) == 1
    assert deduped[0].removed_at == date(2016, 1, 1)
    assert deduped[0].name == "X first"
