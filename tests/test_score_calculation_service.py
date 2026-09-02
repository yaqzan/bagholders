import score_calculation_service as service


class FakeStock:
    symbol = "ZTS"

    def __init__(self):
        self.calls = []

    def calculate_scores(self, **kwargs):
        self.calls.append(("calculate_scores", kwargs))
        if kwargs.get("extra_score_engines"):
            return {"written": [{"key": "v57"}], "skipped": [], "errors": []}

    def calculate_scores_batched(self, **kwargs):
        self.calls.append(("calculate_scores_batched", kwargs))


class FakeLine:
    def __init__(self):
        self.events = []

    def register(self, key, label):
        self.events.append(("register", key, label))

    def start(self, key):
        self.events.append(("start", key))

    def complete(self, key):
        self.events.append(("complete", key))

    def error(self, key):
        self.events.append(("error", key))


def test_parse_update_score_version_args_accepts_flags_equals_and_env():
    parsed = service.parse_update_score_version_args(
        [
            "--score-versions",
            "v57,v58",
            "--score-version=v56",
            "--versions",
            "v55",
        ],
        env={"TRADER_SCORE_VERSIONS": "v54"},
    )

    assert parsed.raw_versions == ["v57,v58", "v56", "v55", "v54"]
    assert parsed.unknown_args == []


def test_parse_update_score_version_args_reports_unknown_and_missing_values():
    parsed = service.parse_update_score_version_args(["--bogus", "--score-versions"], env={})

    assert parsed.raw_versions == []
    assert parsed.unknown_args == ["--bogus", "--score-versions"]


def test_score_calculation_service_current_update_fast_path():
    stock = FakeStock()
    line = FakeLine()
    scoring = service.ScoreCalculationService(validate_versions=False)

    scoring.register_update_line_steps(line)
    result = scoring.calculate_for_update_stock(stock, full=False, line=line)

    assert stock.calls == [
        ("calculate_scores", {"full": False, "weekly": True, "silent": True}),
        ("calculate_scores", {"full": False, "silent": True}),
    ]
    assert ("register", "scores", "scores") in line.events
    assert ("start", "scores") in line.events
    assert ("complete", "scores") in line.events
    assert result.version_summary == {"written": [], "skipped": [], "errors": []}


def test_score_calculation_service_uses_cadence_defaults_when_requested(monkeypatch):
    monkeypatch.setattr(service, "_cadence_version_tokens", lambda: ["v55", "v37"])

    scoring = service.ScoreCalculationService(
        validate_versions=False,
        use_cadence_defaults=True,
    )

    assert scoring.algorithm_versions == ["v55", "v37"]
    assert scoring.version_source == "cadence"


def test_score_calculation_service_explicit_versions_override_cadence_defaults(monkeypatch):
    monkeypatch.setattr(service, "_cadence_version_tokens", lambda: ["v55", "v37"])

    scoring = service.ScoreCalculationService(
        ["v57"],
        validate_versions=False,
        use_cadence_defaults=True,
    )

    assert scoring.algorithm_versions == ["v57"]
    assert scoring.version_source == "explicit"


def test_score_calculation_service_full_update_uses_batched_daily_scores():
    stock = FakeStock()
    scoring = service.ScoreCalculationService(validate_versions=False)

    scoring.calculate_for_update_stock(stock, full=True)

    assert stock.calls == [
        ("calculate_scores", {"full": True, "weekly": True, "silent": True}),
        ("calculate_scores_batched", {"silent": True}),
    ]


def test_score_calculation_service_calculates_requested_versions(monkeypatch):
    captured = {}

    def fake_score_latest(symbol, versions):
        captured["symbol"] = symbol
        captured["versions"] = list(versions)
        return {"written": [{"key": "v57"}], "skipped": [], "errors": []}

    monkeypatch.setattr(service, "_score_latest_for_versions", fake_score_latest)
    scoring = service.ScoreCalculationService(["v57"], validate_versions=False)
    line = FakeLine()

    scoring.register_update_line_steps(line)
    result = scoring.calculate_for_update_stock(FakeStock(), full=False, line=line)

    assert captured == {"symbol": "ZTS", "versions": ["v57"]}
    assert result.version_summary["written"] == [{"key": "v57"}]
    assert ("register", "versions", "version scores") in line.events
    assert ("start", "versions") in line.events
    assert ("complete", "versions") in line.events


def test_score_calculation_service_inlines_loaded_version_engines():
    stock = FakeStock()
    scoring = service.ScoreCalculationService(validate_versions=False)
    scoring.algorithm_versions = ["v57"]
    scoring._score_engines = ["engine57"]
    line = FakeLine()

    scoring.register_update_line_steps(line)
    result = scoring.calculate_for_update_stock(stock, full=False, line=line)

    assert stock.calls == [
        ("calculate_scores", {"full": False, "weekly": True, "silent": True}),
        ("calculate_scores", {
            "full": False,
            "silent": True,
            "extra_score_engines": ["engine57"],
        }),
    ]
    assert result.version_summary["written"] == [{"key": "v57"}]
    assert ("start", "versions") in line.events
    assert ("complete", "versions") in line.events


def test_score_calculation_service_full_update_uses_version_fallback(monkeypatch):
    captured = {}

    def fake_score_latest(symbol, versions):
        captured["symbol"] = symbol
        captured["versions"] = list(versions)
        return {"written": [{"key": "v57"}], "skipped": [], "errors": []}

    monkeypatch.setattr(service, "_score_latest_for_versions", fake_score_latest)
    stock = FakeStock()
    scoring = service.ScoreCalculationService(["v57"], validate_versions=False)
    scoring._score_engines = ["engine57"]

    result = scoring.calculate_for_update_stock(stock, full=True)

    assert stock.calls == [
        ("calculate_scores", {"full": True, "weekly": True, "silent": True}),
        ("calculate_scores_batched", {"silent": True}),
    ]
    assert captured == {"symbol": "ZTS", "versions": ["v57"]}
    assert result.version_summary["written"] == [{"key": "v57"}]


def test_score_calculation_service_keeps_current_score_when_version_scoring_fails(monkeypatch):
    def broken_score_latest(symbol, versions):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(service, "_score_latest_for_versions", broken_score_latest)
    scoring = service.ScoreCalculationService(["v57"], validate_versions=False)
    line = FakeLine()

    result = scoring.calculate_for_update_stock(FakeStock(), full=False, line=line)

    assert result.version_errors == [
        {"key": "version-service", "error": "RuntimeError: snapshot unavailable"}
    ]
    assert ("complete", "scores") in line.events
    assert ("error", "versions") in line.events


def test_score_calculation_service_resolves_versions_once_and_reuses_primary_score(monkeypatch):
    captured = {}

    monkeypatch.setattr(service, "_validate_runtime_versions", lambda versions: list(versions))
    monkeypatch.setattr(service, "_resolve_versions", lambda versions: ["row57"])
    monkeypatch.setattr(service, "_latest_current_score", lambda symbol: {"symbol": symbol, "date": "today"})

    def fake_score_primary(primary, versions):
        captured["primary"] = primary
        captured["versions"] = list(versions)
        return {"written": [{"key": "v57"}], "skipped": [], "errors": []}

    monkeypatch.setattr(service, "_score_primary_for_versions", fake_score_primary)
    scoring = service.ScoreCalculationService(["v57"])

    result = scoring.calculate_for_update_stock(FakeStock(), full=False)

    assert captured == {
        "primary": {"symbol": "ZTS", "date": "today"},
        "versions": ["row57"],
    }
    assert result.version_summary["written"] == [{"key": "v57"}]


def test_score_calculation_service_records_intraday_audit_after_update(monkeypatch):
    captured = []

    def fake_record(symbol, **kwargs):
        captured.append((symbol, kwargs))
        return True

    monkeypatch.setattr(service, "_record_intraday_score", fake_record)
    scoring = service.ScoreCalculationService(validate_versions=False)

    scoring.calculate_for_update_stock(FakeStock(), full=False)

    assert len(captured) == 1
    symbol, kwargs = captured[0]
    assert symbol == "ZTS"
    assert kwargs["source"] == "trader_update"
    assert kwargs["run_key"] == scoring.update_run_key


def test_score_calculation_service_audit_failure_does_not_fail_update(monkeypatch):
    def broken_record(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(service, "_record_intraday_score", broken_record)
    scoring = service.ScoreCalculationService(validate_versions=False)

    result = scoring.calculate_for_update_stock(FakeStock(), full=False)

    assert result.version_summary == {"written": [], "skipped": [], "errors": []}
