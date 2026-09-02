from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime

import database.models.core as core
import database.trader_database as trader_database


class FakeVersion:
    id = 60


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def where(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeScoreRow:
    def __init__(self):
        self.overall = 9
        self.weight_info = '{"pre_regime": 12, "pcd_active": 1}'
        self.regime_multiplier = None
        self.regime_composite = None
        self.volume = 50
        self.volume_signal = "THIN_AIR"
        self.volume_magnitude = 0.0
        self.updated_at = datetime(2026, 5, 26, 7, 15, 26)
        self.calculate_calls = []
        self.saved = 0

    def calculate_overall_score(self, **kwargs):
        self.calculate_calls.append(kwargs)
        self.weight_info = '{"pre_regime": 12, "pre_boost": 30, "pcd_active": 1}'
        return 30

    def save(self):
        self.saved += 1


def test_reapply_regime_today_replays_full_score_modifiers(monkeypatch):
    target_date = date(2026, 5, 26)
    row = FakeScoreRow()

    monkeypatch.setattr(
        core.AlgorithmVersion,
        "get_active_scores_version",
        classmethod(lambda cls: FakeVersion()),
    )
    monkeypatch.setattr(
        core.Score,
        "select",
        classmethod(lambda cls: FakeQuery([row])),
    )
    monkeypatch.setattr(core, "_load_spy_wk_composite_map", lambda: {target_date: 68.0})
    monkeypatch.setattr(core, "_spy_wk_on_or_before", lambda dates, m, d: 68.0)
    monkeypatch.setattr(core, "_compute_put_regime_mult", lambda composite, spy_wk: 0.9086)
    monkeypatch.setattr(core, "_load_mis_stress_map", lambda: {target_date: 0.25})
    monkeypatch.setattr(trader_database.DB, "atomic", lambda: nullcontext())

    updated, skipped = core.reapply_regime_today(
        regime_multiplier=0.8607,
        regime_composite=42.1,
        target_date=target_date,
    )

    assert (updated, skipped) == (1, 0)
    assert row.overall == 30
    assert row.regime_multiplier == 0.8607
    assert row.regime_composite == 42.1
    assert row.saved == 1
    assert row.calculate_calls == [
        {
            "regime_multiplier": 0.8607,
            "regime_composite": 42.1,
            "put_regime_multiplier": 0.9086,
            "mis_stress": 0.25,
        }
    ]
