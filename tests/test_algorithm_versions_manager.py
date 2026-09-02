from algorithm_versions import manager
from algorithm_versions import runtime


def test_normalize_version_key_accepts_numeric_and_prefixed():
    assert manager.normalize_version_key("58") == "v58"
    assert manager.normalize_version_key(58) == "v58"
    assert manager.normalize_version_key("v58") == "v58"
    assert manager.normalize_version_key("candidate-x") == "candidate-x"


def test_diff_dicts_reports_nested_changes():
    left = {
        "SCORING": {"A": 1, "B": 2},
        "portfolio": {"tiers": {"top": 0.1}},
    }
    right = {
        "SCORING": {"A": 1, "B": 3, "C": 4},
        "portfolio": {"tiers": {"top": 0.2}},
    }
    diff = manager.diff_dicts(left, right)

    assert {"key": "SCORING.C", "value": 4} in diff["added"]
    assert {"key": "SCORING.B", "from": 2, "to": 3} in diff["changed"]
    assert {"key": "portfolio.tiers.top", "from": 0.1, "to": 0.2} in diff["changed"]
    assert diff["removed"] == []


def test_cache_path_uses_repo_cache_algorithm_versions(tmp_path):
    path = manager.cache_path_for("58", tmp_path)
    assert path == tmp_path / ".cache" / "algorithm_versions" / "v58"


def test_classify_snapshot_message_marks_baseline_metadata_only():
    result = manager.classify_snapshot_message("Pre-versioning baseline", "baseline")

    assert result["category"] == "legacy_metadata_only"
    assert result["scoring_algorithm_snapshot"] is False


def test_classify_snapshot_message_identifies_scoring_commits():
    result = manager.classify_snapshot_message(
        "v58 scoring: continuation echo dampener",
        "3cfc4dc2",
    )

    assert result["category"] == "scoring_algorithm"
    assert result["scoring_algorithm_snapshot"] is True


def test_parse_version_tokens_dedupes_and_normalizes():
    assert runtime.parse_version_tokens(["57,v58", "58;v56"]) == ["v57", "v58", "v56"]


def test_normalize_compute_result_accepts_legacy_scalar():
    overall, weight_info, vol_update = runtime._normalize_compute_result(74)

    assert overall == 74
    assert weight_info == {}
    assert vol_update is None


def test_extract_static_uppercase_constants_handles_legacy_portfolio_math(tmp_path):
    source = tmp_path / "monte_carlo.py"
    source.write_text(
        "\n".join(
            [
                "HOLD_DAYS = 15",
                "PREMIUM_MULT = 1.82",
                "DELTA = 0.5",
                "TP_BASE = 0.30",
                "TP_SIGMA_BASE = TP_BASE * PREMIUM_MULT / DELTA",
                "MAX_POSITIONS_CALL = None",
                "TIER_ALLOC = {'ultra': 0.15, 'high': 0.12}",
                "TP_DYNAMIC = float('1.0')",
            ]
        ),
        encoding="utf-8",
    )

    result = manager.extract_static_uppercase_constants(source)

    assert result["constants"]["HOLD_DAYS"] == 15
    assert result["constants"]["TP_SIGMA_BASE"] == 1.092
    assert result["constants"]["MAX_POSITIONS_CALL"] is None
    assert result["constants"]["TIER_ALLOC"] == {"ultra": 0.15, "high": 0.12}
    assert result["unsupported"] == [
        {
            "name": "TP_DYNAMIC",
            "line": 8,
            "reason": "unsupported expression Call",
        }
    ]


def test_build_portfolio_config_uses_structured_strategy_snapshot(tmp_path):
    manager.write_json(
        tmp_path / "manifest.json",
        {"silo_key": "v99", "algorithm_version": {"id": 99}},
    )
    manager.write_json(
        tmp_path / "portfolio_snapshot.json",
        {
            "strategies": {
                "30dte": {"HOLD_DAYS": 15, "PREMIUM_MULT": 1.82},
                "15dte": {"HOLD_DAYS": 7, "PREMIUM_MULT": 1.29},
            },
            "assess_combos": [["30", "tp"]],
            "mechanism_registry": [],
        },
    )

    config = manager.build_portfolio_config(tmp_path)

    assert config["silo_key"] == "v99"
    assert config["strategies"]["30dte"]["source_mode"] == "strategy_config"
    assert config["strategies"]["30dte"]["fields"]["HOLD_DAYS"] == 15
    assert config["strategies"]["15dte"]["fields"]["PREMIUM_MULT"] == 1.29


def test_build_portfolio_config_backfills_legacy_source_constants(tmp_path):
    manager.write_json(
        tmp_path / "manifest.json",
        {"silo_key": "v18", "algorithm_version": {"id": 18}},
    )
    manager.write_json(
        tmp_path / "portfolio_snapshot.json",
        {
            "strategies": {"30dte": None, "15dte": None},
            "assess_combos": None,
            "mechanism_registry": [],
            "extraction_error": "strategy_config.py is not present at this git ref",
        },
    )
    sources = tmp_path / "portfolio_sources"
    sources.mkdir()
    (sources / "monte_carlo.py").write_text(
        "\n".join(
            [
                "HOLD_DAYS = 15",
                "PREMIUM_MULT = 1.82",
                "DELTA = 0.5",
                "PUT_TP = 0.30",
                "PUT_TP_SIGMA = PUT_TP * PREMIUM_MULT / DELTA",
            ]
        ),
        encoding="utf-8",
    )

    config = manager.build_portfolio_config(tmp_path)

    d30 = config["strategies"]["30dte"]
    d15 = config["strategies"]["15dte"]
    assert d30["status"] == "available"
    assert d30["source_mode"] == "legacy_source_constants"
    assert d30["fields"]["PUT_TP_SIGMA"] == 1.092
    assert d30["field_sources"]["PUT_TP_SIGMA"] == "portfolio_sources/monte_carlo.py"
    assert d15["status"] == "unavailable"
