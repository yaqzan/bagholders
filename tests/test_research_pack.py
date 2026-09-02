from algorithm_versions.research_pack import (
    DEFAULT_PARAMS,
    PRIMARY_UTILITY_PERIOD,
    build_utility_from_rows,
    merge_portfolio_windows_payload,
    parse_portfolio_windows,
    portfolio_window_readiness,
    score_lookback_readiness,
)


def row(n, wr):
    return {"sample_count": n, "win_rate_7d": wr}


def test_default_utility_period_is_wr15():
    wr_rows = {
        "75+": {"sample_count": 100, "win_rate_7d": 20.0, "win_rate_15d": 80.0},
    }

    payload = build_utility_from_rows(wr_rows, None)

    assert payload["period"] == PRIMARY_UTILITY_PERIOD
    assert payload["sides"]["call"]["bands"][0]["wr"] == 0.8


def test_utility_uses_independent_call_bands_not_cumulative_counts():
    wr_rows = {
        "75+": row(100, 80.0),
        "80+": row(40, 90.0),
        "85+": row(10, 100.0),
        "90+": row(0, None),
        "95+": row(0, None),
    }

    payload = build_utility_from_rows(wr_rows, None, period="7d")

    call_bands = {band["band"]: band for band in payload["sides"]["call"]["bands"]}
    assert call_bands["75-79"]["n"] == 60
    assert call_bands["75-79"]["wr"] == 44 / 60
    assert call_bands["80-84"]["n"] == 30
    assert call_bands["80-84"]["wr"] == 26 / 30
    assert call_bands["85-89"]["n"] == 10
    assert call_bands["85-89"]["wr"] == 1.0
    assert payload["sides"]["call"]["n"] == 100


def test_utility_uses_independent_put_bands_not_cumulative_counts():
    wr_rows = {
        "<25": row(100, 76.0),
        "<20": row(80, 80.0),
        "<15": row(30, 90.0),
        "<10": row(10, 100.0),
        "<5": row(2, 100.0),
    }

    payload = build_utility_from_rows(wr_rows, None, period="7d")

    put_bands = {band["band"]: band for band in payload["sides"]["put"]["bands"]}
    assert put_bands["21-25"]["n"] == 20
    assert put_bands["21-25"]["wr"] == 12 / 20
    assert put_bands["16-20"]["n"] == 50
    assert put_bands["16-20"]["wr"] == 37 / 50
    assert put_bands["0-5"]["n"] == 2
    assert put_bands["0-5"]["wr"] == 1.0
    assert payload["sides"]["put"]["n"] == 100


def test_tp_shadow_is_optional_and_wr_utility_still_scores():
    wr_rows = {
        "75+": row(100, 80.0),
        "80+": row(0, None),
        "85+": row(0, None),
        "90+": row(0, None),
        "95+": row(0, None),
    }

    payload = build_utility_from_rows(wr_rows, None, period="7d")

    assert payload["sides"]["call"]["utility_wr"] > 0
    assert payload["sides"]["call"]["utility_wrtp"] == 0
    assert payload["sides"]["call"]["bands"][0]["utility_wrtp"] is None


def test_top_call_band_gets_configured_allocation_weight():
    wr_rows = {"95+": row(10, 100.0)}

    payload = build_utility_from_rows(wr_rows, None, period="7d")

    top = {band["band"]: band for band in payload["sides"]["call"]["bands"]}["95-100"]
    assert top["weight"] == DEFAULT_PARAMS.weight_for("call", "95-100")
    assert top["weight"] > 1.0


def test_score_lookback_readiness_marks_coverage_limited_10y():
    readiness = score_lookback_readiness(
        {"min_date": "2020-12-31", "max_date": "2026-05-13"},
        [1825, 3650],
    )

    assert readiness["1825"]["ready"] is True
    assert readiness["3650"]["ready"] is False


def test_portfolio_window_readiness_flags_missing_march_2020_coverage():
    readiness = portfolio_window_readiness({"min_date": "2020-12-31", "max_date": "2026-05-13"})

    assert readiness["covid_crash_2020"]["ready"] is False
    assert "after window start" in readiness["covid_crash_2020"]["missing"][0]
    assert readiness["22_now"]["ready"] is True


def test_parse_portfolio_windows_includes_calendar_years():
    windows = parse_portfolio_windows("year_2020,year_2025,year_2026_ytd")

    assert [(window.name, window.start, window.end) for window in windows] == [
        ("year_2020", "2020-01-01", "2020-12-31"),
        ("year_2025", "2025-01-01", "2025-12-31"),
        ("year_2026_ytd", "2026-01-01", None),
    ]


def test_partial_portfolio_window_merge_keeps_existing_windows():
    existing = {
        "windows": [
            {"window": {"name": "22_now"}, "metrics": {"complete": True, "log10_equity_multiple": 1}},
        ],
    }
    partial = {
        "windows": [
            {"window": {"name": "year_2022"}, "metrics": {"complete": True, "log10_equity_multiple": 2}},
        ],
    }

    merged = merge_portfolio_windows_payload(existing, partial)
    by_name = {row["window"]["name"]: row for row in merged["windows"]}

    assert by_name["22_now"]["metrics"]["log10_equity_multiple"] == 1
    assert by_name["year_2022"]["metrics"]["log10_equity_multiple"] == 2
