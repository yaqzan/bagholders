"""Sector ETF breadth dual-wave scoring helper.

The helper is intentionally file/cache based so scoring remains independent of
MarketBreadth table writes during full recalculations.
"""
from __future__ import annotations

import bisect
import csv
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECTOR_BREADTH_PATH = ROOT / ".cache" / "sector_etf_screen" / "sector_breadth_daily_2020plus.csv"

_SERIES_CACHE: dict[tuple[Any, ...], "SectorBreadthWaveSeries"] = {}


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class SectorBreadthWaveSeries:
    def __init__(self, rows: list[dict], crash: dict[date, float], bull: dict[date, float]):
        self.dates = [r["date"] for r in rows]
        self.crash = crash
        self.bull = bull

    @classmethod
    def load(cls, params: dict, path: Path = DEFAULT_SECTOR_BREADTH_PATH) -> "SectorBreadthWaveSeries":
        rows = _load_rows(path)
        if not rows:
            return cls([], {}, {})

        _annotate_rows(rows)
        crash: dict[date, float] = {}
        bull: dict[date, float] = {}
        crash_echo = 0.0
        bull_wave = 0.0
        for row in rows:
            crash_seed = _crash_seed(row, params)
            relief = _repair_relief(row, params)
            crash_echo = clamp01(
                max(crash_seed, crash_echo * params["crash_decay"] * (1.0 - params["repair_k"] * relief))
            )
            bull_seed = _bull_seed(row, params)
            bull_wave = clamp01(max(bull_seed, bull_wave * params["bull_decay"]))
            crash[row["date"]] = crash_echo
            bull[row["date"]] = bull_wave
        return cls(rows, crash, bull)

    def values_on_or_before(self, d: date) -> tuple[float, float]:
        idx = bisect.bisect_right(self.dates, d) - 1
        if idx < 0:
            return 0.0, 0.0
        dd = self.dates[idx]
        return self.crash.get(dd, 0.0), self.bull.get(dd, 0.0)


def adjusted_score(
    score: float,
    side: str,
    signal_date: date,
    params: dict,
) -> tuple[int, dict[str, float]]:
    """Return dampened score and wave metadata for a signal-band score."""
    if not params.get("enabled", False):
        return int(round(score)), {}

    series = get_series(params)
    crash, bull = series.values_on_or_before(signal_date)
    new_score = float(score)
    meta = {"crash": float(crash), "bull": float(bull)}

    if side == "call":
        effective_crash = crash * (1.0 - params["bull_release_k"] * (bull ** params["bull_power"]))
        pressure = effective_crash ** params["crash_power"] if effective_crash > 0 else 0.0
        delta = params["crash_k"] * pressure * max(0.0, new_score - params["call_target"])
        new_score -= delta
        meta.update({"pressure": float(pressure), "delta": -float(delta)})
    elif side == "put" and bull > 0 and params["put_k"] > 0:
        pressure = bull ** params["bull_power"]
        delta = params["put_k"] * pressure * max(0.0, params["put_target"] - new_score)
        new_score += delta
        meta.update({"pressure": float(pressure), "delta": float(delta)})
    else:
        meta.update({"pressure": 0.0, "delta": 0.0})

    return int(round(max(0.0, min(100.0, new_score)))), meta


def get_series(params: dict) -> SectorBreadthWaveSeries:
    path = Path(params.get("path") or DEFAULT_SECTOR_BREADTH_PATH)
    key = (
        str(path),
        params.get("crash_decay"),
        params.get("repair_k"),
        params.get("seed_level"),
        params.get("seed_velocity"),
        params.get("seed_rsi"),
        params.get("relief_brd_start"),
        params.get("relief_brd_full"),
        params.get("relief_avg5_start"),
        params.get("relief_avg5_full"),
        params.get("bull_decay"),
        params.get("bull_velocity"),
        params.get("bull_level"),
        params.get("bull_wash_level"),
    )
    series = _SERIES_CACHE.get(key)
    if series is None:
        series = SectorBreadthWaveSeries.load(params, path)
        _SERIES_CACHE[key] = series
    return series


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        rdr = csv.DictReader(fh)
        for raw in rdr:
            try:
                brd = _float_or_none(raw.get("sec_brd_ema50") or raw.get("pct_above_ema50"))
                rsi = _float_or_none(raw.get("sec_avg_rsi") or raw.get("avg_rsi"))
                rows.append({"date": date.fromisoformat(raw["date"]), "brd": brd, "rsi": rsi})
            except (KeyError, TypeError, ValueError):
                continue
    rows.sort(key=lambda r: r["date"])
    return rows


def _annotate_rows(rows: list[dict]) -> None:
    brds = [r["brd"] for r in rows]
    for i, row in enumerate(rows):
        b = row["brd"]
        win5 = [x for x in brds[max(0, i - 4): i + 1] if x is not None]
        win20 = [x for x in brds[max(0, i - 19): i + 1] if x is not None]
        row["d5"] = b - brds[i - 5] if i >= 5 and b is not None and brds[i - 5] is not None else 0.0
        row["d10"] = b - brds[i - 10] if i >= 10 and b is not None and brds[i - 10] is not None else 0.0
        row["avg5"] = sum(win5) / len(win5) if win5 else 50.0
        row["min20"] = min(win20) if win20 else (b if b is not None else 50.0)


def _crash_seed(row: dict, params: dict) -> float:
    brd = float(row.get("brd") if row.get("brd") is not None else 50.0)
    rsi = float(row.get("rsi") if row.get("rsi") is not None else 50.0)
    d5 = float(row.get("d5") or 0.0)
    d10 = float(row.get("d10") or 0.0)
    low = clamp01((params["seed_level"] - brd) / max(1.0, params["seed_level"]))
    velocity = clamp01((max(-d5, -d10 * 0.75) - params["seed_velocity"]) / 40.0)
    oversold = clamp01((params["seed_rsi"] - rsi) / max(1.0, params["seed_rsi"] - 15.0))
    return max(low, velocity, oversold)


def _repair_relief(row: dict, params: dict) -> float:
    brd = float(row.get("brd") or 0.0)
    avg5 = float(row.get("avg5") or 0.0)
    brd_relief = clamp01(
        (brd - params["relief_brd_start"]) / max(1.0, params["relief_brd_full"] - params["relief_brd_start"])
    )
    avg_relief = clamp01(
        (avg5 - params["relief_avg5_start"]) / max(1.0, params["relief_avg5_full"] - params["relief_avg5_start"])
    )
    return min(brd_relief, avg_relief)


def _bull_seed(row: dict, params: dict) -> float:
    brd = float(row.get("brd") or 0.0)
    avg5 = float(row.get("avg5") or 0.0)
    d5 = float(row.get("d5") or 0.0)
    d10 = float(row.get("d10") or 0.0)
    min20 = float(row.get("min20") if row.get("min20") is not None else brd)
    thrust = clamp01((max(d5, d10 * 0.75) - params["bull_velocity"]) / 45.0)
    level = clamp01((max(brd, avg5) - params["bull_level"]) / max(1.0, 100.0 - params["bull_level"]))
    wash = clamp01((params["bull_wash_level"] - min20) / max(1.0, params["bull_wash_level"]))
    return max(level * 0.65, thrust * max(0.35, wash))


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
