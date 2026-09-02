"""Portfolio-stage router for selectively using 15 DTE call outcomes.

The scoring version stays unchanged. This module only decides whether a 30 DTE
portfolio call entry should use the 15 DTE option path.
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta
from typing import Mapping, Optional, Sequence


def value_on_or_before(
    sorted_dates: Sequence[date],
    values: Mapping[date, float],
    as_of: date,
) -> Optional[float]:
    """Return the latest mapped value on or before ``as_of``."""
    idx = bisect.bisect_right(sorted_dates, as_of) - 1
    if idx < 0:
        return None
    return values.get(sorted_dates[idx])


def route_call_to_15dte(
    cfg,
    *,
    symbol: str,
    score: float,
    trend: Optional[float],
    vix: Optional[float],
    regime_composite: Optional[float],
    routed_today: int = 0,
) -> bool:
    """True when a call signal qualifies for the shipped DTE router."""
    if not getattr(cfg, "DTE_ROUTER_ENABLED", False):
        return False
    if int(getattr(cfg, "DTE_ROUTER_TARGET_DTE", 15)) != 15:
        return False
    if routed_today >= int(getattr(cfg, "DTE_ROUTER_DAY_CAP", 0)):
        return False
    if float(score) < float(getattr(cfg, "DTE_ROUTER_SCORE_MIN", 80)):
        return False
    if trend is None or float(trend) >= float(getattr(cfg, "DTE_ROUTER_TREND_LT", 35.0)):
        return False
    vix_min = float(getattr(cfg, "DTE_ROUTER_VIX_MIN", 0.0))
    if vix_min > 0.0 and (vix is None or float(vix) < vix_min):
        return False
    # CRASH-GATE (added 2026-06-03): 15DTE is a bull-tape engine but crash-RUIN
    # (small premium can't survive a gap-down; pure 15DTE = 75-100% COVID collapse).
    # Route to 15DTE ONLY when VIX is below this ceiling (low-vol/bull). When VIX is
    # elevated, fall back to 30DTE (bigger premium cushion + wide SL = survival).
    # 0.0 = disabled (back-compat). A missing VIX reading is treated as unsafe -> no route.
    vix_max = float(getattr(cfg, "DTE_ROUTER_VIX_MAX", 0.0))
    if vix_max > 0.0 and (vix is None or float(vix) > vix_max):
        return False
    regime_min = float(getattr(cfg, "DTE_ROUTER_REGIME_MIN", 0.0))
    regime_max = float(getattr(cfg, "DTE_ROUTER_REGIME_MAX", 100.0))
    if regime_min > 0.0 or regime_max < 100.0:
        if regime_composite is None:
            return False
        if float(regime_composite) < regime_min or float(regime_composite) > regime_max:
            return False
    excluded = set(getattr(cfg, "DTE_ROUTER_EXCLUDED_SYMBOLS", ()) or ())
    if str(symbol).upper() in excluded:
        return False
    return True


def routed_alloc_score(cfg, score: float) -> float:
    """Allocation score for a routed call entry."""
    cap = getattr(cfg, "DTE_ROUTER_ALLOC_SCORE_CAP", None)
    if cap is None or float(cap) <= 0:
        return float(score)
    return min(float(score), float(cap))


def load_router_market_maps(d_start: date, d_end: date):
    """Load VIX and regime-composite maps for router eligibility."""
    from database.models.core import MarketRegime

    rows = list(
        MarketRegime.select(
            MarketRegime.date,
            MarketRegime.vix_close,
            MarketRegime.regime_composite,
        )
        .where(
            (MarketRegime.date >= d_start - timedelta(days=80))
            & (MarketRegime.date <= d_end)
        )
        .order_by(MarketRegime.date)
        .namedtuples()
    )
    vix_map = {
        r.date: float(r.vix_close)
        for r in rows
        if r.vix_close is not None
    }
    regime_map = {
        r.date: float(r.regime_composite)
        for r in rows
        if r.regime_composite is not None
    }
    router_dates = sorted({*vix_map.keys(), *regime_map.keys()})
    return router_dates, vix_map, regime_map
