"""Shared live portfolio allocation helpers.

These helpers cover per-signal tier sizing and live allocation scalers. They do
not decide portfolio admission, max-position fill, cash exhaustion, or same-
symbol blocking; `trader alloc` owns those full-cascade decisions.
"""

from __future__ import annotations

import bisect
import csv
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class AllocationTier:
    side: str
    key: str
    label: str
    base_frac: float


@dataclass(frozen=True)
class AllocationBreakdown:
    tier: AllocationTier
    f3f_scale: float
    saw_put_scale: float
    dd_scale: float
    breadth_score: Optional[float]
    sector_breadth: Optional[float]
    sector_breadth_date: Optional[date]

    @property
    def base_pct(self) -> float:
        return self.tier.base_frac * 100.0

    @property
    def effective_frac(self) -> float:
        return self.tier.base_frac * self.f3f_scale * self.saw_put_scale * self.dd_scale

    @property
    def effective_pct(self) -> float:
        return self.effective_frac * 100.0


def strategy_config_for(strategy: str = "30dte"):
    import strategy_config

    return strategy_config.STRATEGY_15DTE if strategy == "15dte" else strategy_config.STRATEGY_30DTE


def tier_for_score(score: float, side: str, cfg) -> Optional[AllocationTier]:
    if side == "call":
        if score >= 95:
            return AllocationTier(side, "ultra", "95+", cfg.TIER_ALLOC["ultra"])
        if score >= 85:
            return AllocationTier(side, "top", "85-94", cfg.TIER_ALLOC["top"])
        if score >= 80:
            return AllocationTier(side, "mid", "80-84", cfg.TIER_ALLOC["mid"])
        if score >= 75:
            return AllocationTier(side, "low", "75-79", cfg.TIER_ALLOC["low"])
        if score >= 70:
            return AllocationTier(side, "overflow", "70-74", cfg.TIER_ALLOC["overflow"])
        return None

    if side == "put":
        if score <= 15:
            return AllocationTier(side, "put_top", "<=15", cfg.PUT_TIER_ALLOC["put_top"])
        if score <= 20:
            return AllocationTier(side, "put_mid", "16-20", cfg.PUT_TIER_ALLOC["put_mid"])
        if score <= 25:
            return AllocationTier(side, "put_low", "21-25", cfg.PUT_TIER_ALLOC["put_low"])
        return None

    return None


def market_breadth_on_or_before(as_of: Optional[date] = None) -> Optional[float]:
    from database.models.core import MarketBreadth

    query = MarketBreadth.select(MarketBreadth.breadth_score).where(
        MarketBreadth.breadth_score.is_null(False)
    )
    if as_of is not None:
        query = query.where(MarketBreadth.date <= _as_date(as_of))
    row = query.order_by(MarketBreadth.date.desc()).first()
    return float(row.breadth_score) if row else None


def breadth_alloc_scale(cfg, breadth: Optional[float], *, is_put: bool) -> float:
    if breadth is None or not getattr(cfg, "BREADTH_ALLOC_ENABLED", True):
        return 1.0

    if is_put:
        if breadth <= cfg.F3F_PUT_THRESH:
            scale = 1.0
        elif breadth >= cfg.F3F_PUT_HIGH:
            scale = cfg.F3F_PUT_FLOOR
        else:
            scale = 1.0 - (
                (breadth - cfg.F3F_PUT_THRESH)
                / (cfg.F3F_PUT_HIGH - cfg.F3F_PUT_THRESH)
                * (1.0 - cfg.F3F_PUT_FLOOR)
            )
    else:
        if breadth >= cfg.F3F_CALL_THRESH:
            scale = 1.0
        elif breadth <= cfg.F3F_CALL_LOW:
            scale = cfg.F3F_CALL_FLOOR
        else:
            scale = cfg.F3F_CALL_FLOOR + (
                (breadth - cfg.F3F_CALL_LOW)
                / (cfg.F3F_CALL_THRESH - cfg.F3F_CALL_LOW)
                * (1.0 - cfg.F3F_CALL_FLOOR)
            )

    return max(cfg.ALLOC_SCALE_FLOOR, min(cfg.ALLOC_SCALE_CEIL, scale))


def practical_exposure_enabled(cfg) -> bool:
    return bool(getattr(cfg, "PRACTICAL_EXPOSURE_ENABLED", False))


def practical_allocation_base(cfg, portfolio_value: float) -> float:
    if practical_exposure_enabled(cfg) and getattr(cfg, "PRACTICAL_CAPITAL_CEILING", 0.0) > 0:
        return min(float(portfolio_value), float(cfg.PRACTICAL_CAPITAL_CEILING))
    return float(portfolio_value)


def opportunity_saturation_scale(cfg, pressure: int, *, is_put: bool) -> float:
    if not practical_exposure_enabled(cfg):
        return 1.0
    ref = cfg.OPP_SAT_PUT_REF if is_put else cfg.OPP_SAT_CALL_REF
    if ref <= 0 or pressure <= ref:
        return 1.0
    scale = (ref / max(1.0, float(pressure))) ** max(0.0, cfg.OPP_SAT_POWER)
    return max(cfg.OPP_SAT_FLOOR, min(1.0, scale))


def premium_cap_remaining(
    cfg,
    *,
    side: str,
    allocation_base: float,
    total_open_premium: float,
    side_open_premium: float,
) -> Optional[float]:
    if not practical_exposure_enabled(cfg):
        return None
    remaining = float("inf")
    if cfg.GROSS_PREMIUM_CAP > 0:
        remaining = min(remaining, allocation_base * cfg.GROSS_PREMIUM_CAP - total_open_premium)
    if side == "call" and cfg.CALL_PREMIUM_CAP > 0:
        remaining = min(remaining, allocation_base * cfg.CALL_PREMIUM_CAP - side_open_premium)
    if side == "put" and cfg.PUT_PREMIUM_CAP > 0:
        remaining = min(remaining, allocation_base * cfg.PUT_PREMIUM_CAP - side_open_premium)
    return max(0.0, remaining)


def sector_breadth_on_or_before(
    as_of: date,
    *,
    cache_path: Optional[Path] = None,
) -> Tuple[Optional[float], Optional[date]]:
    if cache_path is None:
        cache_path = (
            Path(__file__).resolve().parent
            / ".cache"
            / "sector_etf_screen"
            / "sector_breadth_daily_2020plus.csv"
        )

    if not cache_path.exists():
        return None, None

    target = _as_date(as_of)
    rows = []
    with cache_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                rows.append((date.fromisoformat(row["date"]), float(row["sec_brd_ema50"])))
            except (KeyError, TypeError, ValueError):
                continue
    if not rows:
        return None, None

    rows.sort(key=lambda item: item[0])
    dates = [item[0] for item in rows]
    idx = bisect.bisect_right(dates, target) - 1
    if idx < 0:
        return 50.0, None
    return rows[idx][1], rows[idx][0]


def saw_put_ucurve_scale(
    cfg,
    as_of: Optional[date],
    *,
    sector_breadth: Optional[float] = None,
) -> Tuple[float, Optional[float], Optional[date]]:
    if not getattr(cfg, "SAW_PUT_UCURVE_ENABLED", False):
        return 1.0, None, None

    sector_breadth_date = None
    if sector_breadth is None:
        if as_of is None:
            return 1.0, None, None
        sector_breadth, sector_breadth_date = sector_breadth_on_or_before(as_of)
    if sector_breadth is None:
        return 1.0, None, None

    floor = cfg.SAW_PUT_UCURVE_FLOOR
    ceil = cfg.SAW_PUT_UCURVE_CEIL
    if cfg.SAW_PUT_UCURVE_SHAPE == "sigmoid":
        lo_thresh = cfg.SAW_PUT_UCURVE_MIDPOINT - cfg.SAW_PUT_UCURVE_HALFWIDTH
        hi_thresh = cfg.SAW_PUT_UCURVE_MIDPOINT + cfg.SAW_PUT_UCURVE_HALFWIDTH
        try:
            sig_lo = 1.0 / (
                1.0 + math.exp(-(lo_thresh - sector_breadth) / max(0.5, cfg.SAW_PUT_UCURVE_K))
            )
        except OverflowError:
            sig_lo = 0.0 if (lo_thresh - sector_breadth) < 0 else 1.0
        try:
            sig_hi = 1.0 / (
                1.0 + math.exp(-(sector_breadth - hi_thresh) / max(0.5, cfg.SAW_PUT_UCURVE_K))
            )
        except OverflowError:
            sig_hi = 0.0 if (sector_breadth - hi_thresh) < 0 else 1.0
        activation = min(1.0, sig_lo + sig_hi)
        return floor + activation * (ceil - floor), sector_breadth, sector_breadth_date

    d_norm = abs(sector_breadth - cfg.SAW_PUT_UCURVE_MIDPOINT) / max(
        1.0, cfg.SAW_PUT_UCURVE_HALFWIDTH
    )
    if d_norm > 1.0:
        d_norm = 1.0
    return floor + (d_norm ** cfg.SAW_PUT_UCURVE_POWER) * (ceil - floor), sector_breadth, sector_breadth_date


def allocation_breakdown_for_score(
    score: float,
    side: str,
    *,
    strategy: str = "30dte",
    breadth: Optional[float] = None,
    signal_date: Optional[date] = None,
    sector_breadth: Optional[float] = None,
    dd_scale: float = 1.0,
) -> Optional[AllocationBreakdown]:
    cfg = strategy_config_for(strategy)
    tier = tier_for_score(score, side, cfg)
    if tier is None:
        return None

    if breadth is None:
        breadth = market_breadth_on_or_before(signal_date)
    f3f_scale = breadth_alloc_scale(cfg, breadth, is_put=(side == "put"))
    if side == "put":
        saw_scale, sec_brd, sec_brd_date = saw_put_ucurve_scale(
            cfg,
            signal_date,
            sector_breadth=sector_breadth,
        )
    else:
        saw_scale, sec_brd, sec_brd_date = 1.0, None, None

    return AllocationBreakdown(
        tier=tier,
        f3f_scale=f3f_scale,
        saw_put_scale=saw_scale,
        dd_scale=dd_scale,
        breadth_score=breadth,
        sector_breadth=sec_brd,
        sector_breadth_date=sec_brd_date,
    )


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Expected date-like value, got {type(value).__name__}")
