from datetime import date

import recalculate_scores as recalc


def test_yearly_chunks_splits_cross_year_window():
    chunks = list(recalc._yearly_chunks(date(2024, 11, 15), date(2026, 2, 3)))

    assert chunks == [
        (date(2024, 11, 15), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 2, 3)),
    ]


def test_normalize_score_version_tokens_accepts_lists_commas_and_semicolons():
    assert recalc._normalize_score_version_tokens(["57,v58", "v57; 59", ""]) == [
        "v57",
        "v58",
        "v59",
    ]


def test_default_workers_uses_available_cpu_with_env_cap(monkeypatch):
    monkeypatch.setattr(recalc.os, "cpu_count", lambda: 16)
    monkeypatch.delenv("TRADER_RECALC_MAX_WORKERS", raising=False)

    assert recalc._default_workers() == 15

    monkeypatch.setenv("TRADER_RECALC_MAX_WORKERS", "6")
    assert recalc._default_workers() == 6


def test_db_tuning_preflight_skips_short_daily_score_recalc():
    assert recalc.should_run_db_tuning_preflight(1, recalc.DAILY_COMPONENTS) is False
    assert recalc.should_run_db_tuning_preflight(30, {"overall"}) is False


def test_db_tuning_preflight_keeps_large_or_derived_recalc():
    assert recalc.should_run_db_tuning_preflight(31, {"overall"}) is True
    assert recalc.should_run_db_tuning_preflight(1, {"overall", "weekly"}) is True
