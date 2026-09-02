from datetime import date
import sys
from types import SimpleNamespace

import algorithm_versions.runtime as runtime
import score_calculation_service
import trader
from database.models.core import AlgorithmVersion


class FakeVersion:
    def __init__(self, version_id):
        self.id = version_id
        self.production_label = f"v{version_id}"


class FakeEngine:
    def __init__(self, version_id):
        self.version_id = version_id
        self.key = f"v{version_id}"


class FakePrimaryScore:
    def __init__(self, symbol, score_date):
        self.symbol_id = symbol
        self.date = score_date


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 5, 15)


class FixedHolidayDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 5, 25)


def _patch_runtime(monkeypatch, calls):
    monkeypatch.setattr(
        AlgorithmVersion,
        "get_or_create_current",
        classmethod(lambda cls: FakeVersion(58)),
    )
    monkeypatch.setattr(
        runtime,
        "resolve_versions",
        lambda versions: [FakeVersion(int(str(v).lstrip("v"))) for v in versions],
    )
    monkeypatch.setattr(
        runtime,
        "load_scoring_engine",
        lambda version: FakeEngine(version.id),
    )

    def fake_score_primary_for_engines(primary, engines):
        calls.append((primary.symbol_id, primary.date, [engine.version_id for engine in engines]))
        return {
            "written": [{"key": engine.key, "date": primary.date.isoformat()} for engine in engines],
            "skipped": [],
            "errors": [],
        }

    monkeypatch.setattr(runtime, "score_primary_for_engines", fake_score_primary_for_engines)


def test_post_update_score_refresh_refreshes_today_and_fills_prior_missing(monkeypatch):
    calls = []
    _patch_runtime(monkeypatch, calls)
    may14 = date(2026, 5, 14)
    may15 = date(2026, 5, 15)
    monkeypatch.setattr(
        trader,
        "_load_post_update_primary_scores",
        lambda current_version, start_date, end_date: [
            FakePrimaryScore("AAPL", may14),
            FakePrimaryScore("AAPL", may15),
        ],
    )
    monkeypatch.setattr(
        trader,
        "_load_existing_sidecar_score_keys",
        lambda symbols, dates, version_ids: {
            ("AAPL", may14, 53),
            ("AAPL", may15, 44),
            ("AAPL", may15, 53),
        },
    )

    monkeypatch.setattr(trader, "date", FixedDate)

    ran = trader._run_post_update_score_refresh(["v44", "v53"])

    assert ran is True
    assert calls == [
        ("AAPL", may14, [44]),
        ("AAPL", may15, [44, 53]),
    ]


def test_post_update_score_refresh_still_refreshes_today_when_rows_exist(monkeypatch):
    calls = []
    _patch_runtime(monkeypatch, calls)
    may15 = date(2026, 5, 15)
    monkeypatch.setattr(
        trader,
        "_load_post_update_primary_scores",
        lambda current_version, start_date, end_date: [FakePrimaryScore("AAPL", may15)],
    )
    monkeypatch.setattr(
        trader,
        "_load_existing_sidecar_score_keys",
        lambda symbols, dates, version_ids: {
            ("AAPL", may15, 44),
            ("AAPL", may15, 53),
        },
    )

    monkeypatch.setattr(trader, "date", FixedDate)

    ran = trader._run_post_update_score_refresh(["v44", "v53"])

    assert ran is True
    assert calls == [("AAPL", may15, [44, 53])]


def test_post_update_score_refresh_skips_without_versions():
    assert trader._run_post_update_score_refresh([]) is False


def test_post_update_score_refresh_skips_on_nyse_holiday(monkeypatch):
    monkeypatch.setattr(trader, "date", FixedHolidayDate)

    assert trader._run_post_update_score_refresh(["v44"]) is False


def test_close_update_uses_update_pipeline_with_options_and_cadence(monkeypatch):
    calls = []
    fake_scoring = SimpleNamespace(
        algorithm_versions=["v44", "v53"],
        version_source="cadence",
        has_algorithm_versions=True,
    )

    class FakeClient:
        def update_stocks(self, **kwargs):
            calls.append(("update_stocks", kwargs))

    def fake_parse(tokens):
        calls.append(("parse", list(tokens)))
        return SimpleNamespace(raw_versions=[], unknown_args=[])

    def fake_scoring_service(raw_versions, *, use_cadence_defaults):
        calls.append(("scoring_service", list(raw_versions), use_cadence_defaults))
        return fake_scoring

    monkeypatch.setattr(score_calculation_service, "parse_update_score_version_args", fake_parse)
    monkeypatch.setattr(score_calculation_service, "ScoreCalculationService", fake_scoring_service)
    monkeypatch.setattr(
        trader,
        "_run_post_update_score_refresh",
        lambda versions: calls.append(("post_refresh", list(versions))),
    )
    monkeypatch.setattr(
        trader.Trend,
        "update_trend_data",
        lambda **kwargs: calls.append(("trend_update", kwargs)),
    )
    monkeypatch.setitem(
        sys.modules,
        "notifications",
        SimpleNamespace(
            capture_current_scores=lambda **kwargs: (
                calls.append(("capture", kwargs)) or (FixedDate.today(), None, {"AAPL": 80})
            ),
            process_opportunity_exits=lambda **kwargs: calls.append(("exits", kwargs)),
            process_score_notifications=lambda **kwargs: calls.append(("notifications", kwargs)),
        ),
    )
    # The portfolio engine is now the sole source of update-time notifications.
    monkeypatch.setitem(
        sys.modules,
        "portfolio_engine",
        SimpleNamespace(
            run_update=lambda **kwargs: calls.append(("portfolio", kwargs)),
        ),
    )
    monkeypatch.setattr(trader, "date", FixedDate)

    trader._run_update_command(FakeClient(), None, with_options=True, command_name="close-update")

    update_call = next(call for call in calls if call[0] == "update_stocks")
    assert update_call[1]["full"] is False
    assert update_call[1]["with_options"] is True
    assert update_call[1]["score_notification_previous"] == {"AAPL": 80}
    assert update_call[1]["score_notification_immediate"] is False
    assert update_call[1]["scoring_service"] is fake_scoring
    assert ("scoring_service", [], True) in calls
    assert ("post_refresh", ["v44", "v53"]) in calls
    assert ("trend_update", {"full": False}) in calls
    # Portfolio engine drives update-time notifications now (open/close only).
    portfolio_call = next(call for call in calls if call[0] == "portfolio")
    assert portfolio_call is not None
    call_names = [call[0] for call in calls]
    assert call_names.index("update_stocks") < call_names.index("portfolio")


def test_update_command_skips_all_work_on_nyse_holiday(monkeypatch):
    calls = []

    class FakeClient:
        def update_stocks(self, **kwargs):
            calls.append(("update_stocks", kwargs))

    def fake_parse(tokens):
        calls.append(("parse", list(tokens)))
        return SimpleNamespace(raw_versions=[], unknown_args=[])

    def fail_scoring_service(*args, **kwargs):
        raise AssertionError("ScoreCalculationService should not be built on NYSE holidays")

    monkeypatch.setattr(score_calculation_service, "parse_update_score_version_args", fake_parse)
    monkeypatch.setattr(score_calculation_service, "ScoreCalculationService", fail_scoring_service)
    monkeypatch.setattr(trader, "date", FixedHolidayDate)
    monkeypatch.setattr(
        trader,
        "_run_post_update_score_refresh",
        lambda versions: calls.append(("post_refresh", list(versions))),
    )
    monkeypatch.setattr(
        trader.Trend,
        "update_trend_data",
        lambda **kwargs: calls.append(("trend_update", kwargs)),
    )

    trader._run_update_command(FakeClient(), None)

    assert calls == [("parse", [])]


def test_score_version_gapfill_refreshes_today_and_walks_back_to_anchor(monkeypatch):
    calls = []
    _patch_runtime(monkeypatch, calls)
    may12 = date(2026, 5, 12)
    may13 = date(2026, 5, 13)
    may14 = date(2026, 5, 14)
    may15 = date(2026, 5, 15)

    def fake_primary_scores(current_version, start_date, end_date):
        scores = [
            FakePrimaryScore("AAPL", may12),
            FakePrimaryScore("AAPL", may13),
            FakePrimaryScore("AAPL", may14),
            FakePrimaryScore("AAPL", may15),
        ]
        return [score for score in scores if start_date <= score.date <= end_date]

    monkeypatch.setattr(trader, "_load_post_update_primary_scores", fake_primary_scores)
    monkeypatch.setattr(
        trader,
        "_load_latest_sidecar_score_dates",
        lambda symbols, version_ids, before_date: {
            ("AAPL", 44): may12,
            ("AAPL", 53): may14,
        },
    )
    monkeypatch.setattr(trader, "date", FixedDate)

    ran = trader._run_score_version_gapfill(["v44", "v53"])

    assert ran is True
    assert calls == [
        ("AAPL", may13, [44]),
        ("AAPL", may14, [44]),
        ("AAPL", may15, [44, 53]),
    ]


def test_score_version_gapfill_does_not_backfill_prior_without_anchor(monkeypatch):
    calls = []
    _patch_runtime(monkeypatch, calls)
    may14 = date(2026, 5, 14)
    may15 = date(2026, 5, 15)

    def fake_primary_scores(current_version, start_date, end_date):
        scores = [
            FakePrimaryScore("AAPL", may14),
            FakePrimaryScore("AAPL", may15),
        ]
        return [score for score in scores if start_date <= score.date <= end_date]

    monkeypatch.setattr(trader, "_load_post_update_primary_scores", fake_primary_scores)
    monkeypatch.setattr(
        trader,
        "_load_latest_sidecar_score_dates",
        lambda symbols, version_ids, before_date: {},
    )
    monkeypatch.setattr(trader, "date", FixedDate)

    ran = trader._run_score_version_gapfill(["v44", "v53"])

    assert ran is True
    assert calls == [("AAPL", may15, [44, 53])]
