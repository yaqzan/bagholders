"""Structured live allocation plan for the dashboard.

This mirrors the live `trader alloc` cascade surface: active score rows,
DTE-aware tier sizing, F3f breadth allocation, SAW put scaling, practical
exposure caps, and calls-first slot fill.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from peewee import fn


class AllocationPlanError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, payload: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def build_live_allocation_plan(
    portfolio_value: float,
    *,
    currency: str = "CAD",
    strategy: str = "30dte",
    portfolio_profile: str = "sentinel",
    staging_variant: Optional[str] = None,
    version: Optional[str] = None,
) -> dict[str, Any]:
    currency = (currency or "CAD").upper()
    if currency not in ("CAD", "USD"):
        raise AllocationPlanError("currency must be CAD or USD")
    try:
        portfolio_value = float(portfolio_value)
    except (TypeError, ValueError):
        raise AllocationPlanError("amount must be a positive number")
    if portfolio_value <= 0:
        raise AllocationPlanError("amount must be a positive number")

    strategy = _normalize_strategy(strategy)
    if staging_variant:
        staging_variant = staging_variant.lower()
        if staging_variant != "sector-etf":
            raise AllocationPlanError("staging must be sector-etf when provided")
    if staging_variant and version:
        raise AllocationPlanError("version cannot be combined with staging")

    cad_usd_rate, rate_is_fallback = _get_cad_usd_rate()
    if currency == "CAD":
        portfolio_cad = portfolio_value
        portfolio_usd = portfolio_value * cad_usd_rate
    else:
        portfolio_usd = portfolio_value
        portfolio_cad = portfolio_value / cad_usd_rate if cad_usd_rate else None

    warnings: list[str] = []
    if rate_is_fallback:
        warnings.append(
            f"No cached CAD/USD rate found; using fallback {cad_usd_rate:.4f}."
        )

    (
        score_model,
        version_field,
        active_version,
        version,
        staging_version,
        latest_date,
    ) = _resolve_score_source(staging_variant, version)
    if latest_date is None:
        raise AllocationPlanError("No scores found in DB.", status_code=404)

    if staging_variant == "sector-etf":
        from database.models.core import Score, StagingScore

        active_latest = (
            Score.select(fn.MAX(Score.date))
            .where(Score.version == active_version)
            .scalar()
        )
        if active_latest and latest_date != active_latest:
            warnings.append(
                f"sector-etf staging date {latest_date} differs from active date {active_latest}."
            )
        active_count = (
            Score.select()
            .where(
                Score.version == active_version,
                Score.date == latest_date,
                Score.overall.is_null(False),
            )
            .count()
        )
        staging_count = (
            StagingScore.select()
            .where(
                StagingScore.staging_version == staging_version,
                StagingScore.date == latest_date,
            )
            .count()
        )
        if staging_count < active_count:
            warnings.append(
                f"sector-etf staging coverage {staging_count}/{active_count} for {latest_date}."
            )

    call_signals_75 = list(
        score_model.select()
        .where(
            version_field == version,
            score_model.date == latest_date,
            score_model.overall >= 75,
        )
        .order_by(score_model.overall.desc())
    )
    call_signals_70 = list(
        score_model.select()
        .where(
            version_field == version,
            score_model.date == latest_date,
            score_model.overall >= 70,
            score_model.overall < 75,
        )
        .order_by(score_model.overall.desc())
    )
    all_call_signals = call_signals_75 + call_signals_70

    put_signals = list(
        score_model.select()
        .where(
            version_field == version,
            score_model.date == latest_date,
            score_model.overall <= 25,
        )
        .order_by(score_model.overall.asc())
    )
    earn_suppressed_puts = _suppress_earnings_puts(put_signals, latest_date)
    put_signals = earn_suppressed_puts["signals"]
    if earn_suppressed_puts["dropped"]:
        warnings.append(
            "EARN_SUPP_PUT removed "
            f"{earn_suppressed_puts['dropped']} put signal(s) in "
            f"[{earn_suppressed_puts['min_score']},{earn_suppressed_puts['max_score']}] "
            f"with earnings within {earn_suppressed_puts['days']} trading days."
        )

    import portfolio_profiles
    import strategy_config
    from database.models.core import MarketRegime
    from portfolio_allocation import (
        breadth_alloc_scale,
        market_breadth_on_or_before,
        opportunity_saturation_scale,
        practical_allocation_base,
        practical_exposure_enabled,
        premium_cap_remaining,
        saw_put_ucurve_scale,
        tier_for_score,
    )
    from dte_router import route_call_to_15dte, routed_alloc_score

    is_15dte = strategy == "15dte"
    alloc_cfg = strategy_config.STRATEGY_15DTE if is_15dte else strategy_config.STRATEGY_30DTE
    router_target_cfg = strategy_config.STRATEGY_15DTE
    try:
        alloc_cfg, profile = portfolio_profiles.profiled_strategy_config(alloc_cfg, portfolio_profile)
    except KeyError as exc:
        raise AllocationPlanError(str(exc))
    profile_key = profile["key"]
    opt = alloc_cfg.option

    # Effective option tenor for DISPLAY: a profile may carry its own honest-calendar
    # DTE (e.g. Apex elbow = 15) on the shared 30DTE base via NOMINAL_CAL_DTE.
    eff_calendar = bool(getattr(alloc_cfg, "CALENDAR_HOLD", False))
    eff_dte = int(getattr(alloc_cfg, "NOMINAL_CAL_DTE", 30)) if eff_calendar else (15 if is_15dte else 30)
    base_dte_label = f"{eff_dte} DTE"
    eff_hold = int(getattr(alloc_cfg, "HOLD_CAL_DAYS", alloc_cfg.HOLD_DAYS)) if eff_calendar else int(alloc_cfg.HOLD_DAYS)

    breadth_score = None
    regime_mult = 1.0
    router_vix = None
    router_regime_composite = None
    try:
        breadth_score = market_breadth_on_or_before(latest_date)
        rg_mult = (
            MarketRegime.select(
                MarketRegime.regime_multiplier,
            )
            .where(
                (MarketRegime.date <= latest_date)
                & MarketRegime.regime_multiplier.is_null(False)
            )
            .order_by(MarketRegime.date.desc())
            .first()
        )
        if rg_mult:
            regime_mult = float(rg_mult.regime_multiplier)
        rg_router = (
            MarketRegime.select(
                MarketRegime.vix_close,
                MarketRegime.regime_composite,
            )
            .where(MarketRegime.date <= latest_date)
            .order_by(MarketRegime.date.desc())
            .first()
        )
        if rg_router:
            router_vix = float(rg_router.vix_close) if rg_router.vix_close is not None else None
            router_regime_composite = (
                float(rg_router.regime_composite) if rg_router.regime_composite is not None else None
            )
    except Exception:
        warnings.append("Market breadth/regime lookup failed; allocation scales used neutral fallbacks.")

    stressed = breadth_score is not None and breadth_score <= opt.BREADTH_THRESHOLD
    call_tp = (opt.TP_STRESS if stressed else opt.TP_BASE) * 100.0
    call_sl = abs(opt.SL_STRESS if stressed else opt.SL_BASE) * 100.0
    put_tp = opt.PUT_TP * 100.0
    put_sl = abs(opt.PUT_SL) * 100.0

    call_scale = breadth_alloc_scale(alloc_cfg, breadth_score, is_put=False)
    put_scale = breadth_alloc_scale(alloc_cfg, breadth_score, is_put=True)
    saw_put_scale, saw_sector_breadth, saw_sector_date = saw_put_ucurve_scale(
        alloc_cfg,
        latest_date,
    )
    effective_put_scale = put_scale * saw_put_scale

    practical_enabled = practical_exposure_enabled(alloc_cfg)
    allocation_base_usd = practical_allocation_base(alloc_cfg, portfolio_usd)
    call_pressure = len(all_call_signals)
    put_pressure = len(put_signals)
    call_sat_scale = opportunity_saturation_scale(alloc_cfg, call_pressure, is_put=False)
    put_sat_scale = opportunity_saturation_scale(alloc_cfg, put_pressure, is_put=True)

    max_positions = alloc_cfg.MAX_POSITIONS
    max_positions_call = alloc_cfg.MAX_POSITIONS_CALL
    max_positions_put = alloc_cfg.MAX_POSITIONS_PUT
    cash_remaining = float(portfolio_usd)
    positions = 0
    n_calls = 0
    n_puts = 0
    call_deployed = 0.0
    put_deployed = 0.0
    allocated_calls: list[dict[str, Any]] = []
    allocated_puts: list[dict[str, Any]] = []
    skipped = {"calls": 0, "puts": 0}
    routed_call_decisions = 0
    routed_call_fills = 0

    def add_signal(signal, side: str) -> None:
        nonlocal cash_remaining, positions, n_calls, n_puts, call_deployed, put_deployed
        nonlocal routed_call_decisions, routed_call_fills
        if positions >= max_positions:
            return
        if side == "call" and max_positions_call is not None and n_calls >= max_positions_call:
            return
        if side == "put" and max_positions_put is not None and n_puts >= max_positions_put:
            return

        score = float(signal.overall)
        symbol_text = _symbol_text(signal)
        trend = float(signal.trend) if getattr(signal, "trend", None) is not None else None
        routed_15dte = (
            side == "call"
            and not is_15dte
            and route_call_to_15dte(
                alloc_cfg,
                symbol=symbol_text,
                score=score,
                trend=trend,
                vix=router_vix,
                regime_composite=router_regime_composite,
                routed_today=routed_call_decisions,
            )
        )
        score_for_alloc = routed_alloc_score(alloc_cfg, score) if routed_15dte else score
        if routed_15dte:
            routed_call_decisions += 1
        ct_promoted = (
            routed_15dte
            and bool(getattr(alloc_cfg, "CT_PROMOTE", False))
            and trend is not None
            and trend <= float(getattr(alloc_cfg, "CT_CALL_TREND_MAX", -1))
        )
        tier_score = 95.0 if ct_promoted else score_for_alloc
        tier = tier_for_score(tier_score, side, alloc_cfg)
        if tier is None:
            skipped["calls" if side == "call" else "puts"] += 1
            return

        if side == "call":
            side_scale = call_scale * call_sat_scale
            cost_usd = allocation_base_usd * tier.base_frac * side_scale
            cap_remaining = premium_cap_remaining(
                alloc_cfg,
                side="call",
                allocation_base=allocation_base_usd,
                total_open_premium=call_deployed + put_deployed,
                side_open_premium=call_deployed,
            )
        else:
            side_scale = effective_put_scale * put_sat_scale
            cost_usd = allocation_base_usd * tier.base_frac * side_scale
            cap_remaining = premium_cap_remaining(
                alloc_cfg,
                side="put",
                allocation_base=allocation_base_usd,
                total_open_premium=call_deployed + put_deployed,
                side_open_premium=put_deployed,
            )
        if cap_remaining is not None:
            cost_usd = min(cost_usd, cap_remaining)
        if cost_usd <= 0 or cost_usd > cash_remaining:
            skipped["calls" if side == "call" else "puts"] += 1
            return

        dte_cfg = router_target_cfg if routed_15dte else alloc_cfg
        dte_opt = dte_cfg.option
        dte_stressed = breadth_score is not None and breadth_score <= dte_opt.BREADTH_THRESHOLD
        row_call_tp = (dte_opt.TP_STRESS if dte_stressed else dte_opt.TP_BASE) * 100.0
        row_call_sl = abs(dte_opt.SL_STRESS if dte_stressed else dte_opt.SL_BASE) * 100.0

        row = {
            "symbol": symbol_text,
            "score": round(score, 1),
            "allocation_score": round(score_for_alloc, 1),
            "side": side,
            "tier": tier.label,
            "dte": "15dte" if routed_15dte else strategy,
            "dte_label": "15 DTE" if routed_15dte else base_dte_label,
            "routed_15dte": routed_15dte,
            "ct_promoted": ct_promoted,
            "base_pct": round(tier.base_frac * 100.0, 2),
            "effective_pct": round(tier.base_frac * side_scale * 100.0, 2),
            "allocation": _money(cost_usd, cad_usd_rate),
            "exits": {
                "hold_days": dte_cfg.HOLD_DAYS if routed_15dte else eff_hold,
                "hard_sell_pct": round(abs(dte_cfg.HARD_SELL_LOSS) * 100.0, 1),
                "tp_pct": round(row_call_tp if side == "call" else put_tp, 1),
                "sl_pct": round(row_call_sl if side == "call" else put_sl, 1),
            },
            "multipliers": {
                "f3f": round(call_scale if side == "call" else put_scale, 4),
                "saw": round(1.0 if side == "call" else saw_put_scale, 4),
                "saturation": round(call_sat_scale if side == "call" else put_sat_scale, 4),
                "total": round(side_scale, 4),
            },
        }
        cash_remaining -= cost_usd
        positions += 1
        if side == "call":
            n_calls += 1
            call_deployed += cost_usd
            allocated_calls.append(row)
            if routed_15dte:
                routed_call_fills += 1
        else:
            n_puts += 1
            put_deployed += cost_usd
            allocated_puts.append(row)

    for signal in all_call_signals:
        add_signal(signal, "call")
    for signal in put_signals:
        add_signal(signal, "put")

    deployed = float(portfolio_usd) - cash_remaining
    total_candidates = len(all_call_signals) + len(put_signals)

    return {
        "generated_at": datetime.now().isoformat(),
        "input": {
            "amount": round(portfolio_value, 2),
            "currency": currency,
            "strategy": strategy,
            "portfolio_profile": profile_key,
            "staging": staging_variant,
        },
        "portfolio_profile": profile,
        "portfolio": {
            "cad": round(portfolio_cad, 2) if portfolio_cad is not None else None,
            "usd": round(portfolio_usd, 2),
            "cad_usd_rate": round(cad_usd_rate, 6),
            "rate_is_fallback": rate_is_fallback,
            "allocation_base": _money(allocation_base_usd, cad_usd_rate),
        },
        "version": _version_payload(active_version, version, staging_version),
        "signals_date": latest_date.isoformat(),
        "market": {
            "breadth_score": _round_or_none(breadth_score, 2),
            "breadth_state": "stressed" if stressed else "calm",
            "regime_multiplier": round(regime_mult, 4),
            "regime_state": _regime_state(regime_mult),
            "sector_breadth": _round_or_none(saw_sector_breadth, 2),
            "sector_breadth_date": saw_sector_date.isoformat() if saw_sector_date else None,
            "vix_close": _round_or_none(router_vix, 2),
            "regime_composite": _round_or_none(router_regime_composite, 2),
        },
        "guidelines": {
            "dte_label": base_dte_label,
            "hold_days": eff_hold,
            "hard_sell_pct": round(abs(alloc_cfg.HARD_SELL_LOSS) * 100.0, 1),
            "max_positions": max_positions,
            "max_positions_call": max_positions_call,
            "max_positions_put": max_positions_put,
            "fill_order": "calls first by score, then puts; same-symbol conflicts are blocked by the score side.",
            "exits": {
                "call": {
                    "tp_pct": round(call_tp, 1),
                    "sl_pct": round(call_sl, 1),
                    "stress_tp_pct": round(opt.TP_STRESS * 100.0, 1),
                    "stress_sl_pct": round(abs(opt.SL_STRESS) * 100.0, 1),
                    "breadth_threshold": opt.BREADTH_THRESHOLD,
                },
                "put": {
                    "tp_pct": round(put_tp, 1),
                    "sl_pct": round(put_sl, 1),
                },
            },
            "dd": {
                "breaker_pct": round(getattr(alloc_cfg, "DD_CIRCUIT_BREAKER", 0.60) * 100.0, 1),
                "soft_band_low_pct": round(alloc_cfg.DD_SOFT_BAND_LO * 100.0, 1),
                "soft_band_high_pct": round(alloc_cfg.DD_SOFT_BAND_HI * 100.0, 1),
                "soft_call_floor": round(alloc_cfg.DD_SOFT_CALL_FLOOR, 4),
            },
            "practical_exposure": {
                "enabled": practical_enabled,
                "capital_ceiling": _money(getattr(alloc_cfg, "PRACTICAL_CAPITAL_CEILING", 0.0), cad_usd_rate),
                "gross_premium_cap_pct": round(getattr(alloc_cfg, "GROSS_PREMIUM_CAP", 0.0) * 100.0, 1),
                "call_premium_cap_pct": round(getattr(alloc_cfg, "CALL_PREMIUM_CAP", 0.0) * 100.0, 1),
                "put_premium_cap_pct": round(getattr(alloc_cfg, "PUT_PREMIUM_CAP", 0.0) * 100.0, 1),
                "call_pressure": call_pressure,
                "put_pressure": put_pressure,
                "call_ref": getattr(alloc_cfg, "OPP_SAT_CALL_REF", None),
                "put_ref": getattr(alloc_cfg, "OPP_SAT_PUT_REF", None),
                "floor": getattr(alloc_cfg, "OPP_SAT_FLOOR", None),
            },
            "dte_router": {
                "enabled": bool(getattr(alloc_cfg, "DTE_ROUTER_ENABLED", False)) and not is_15dte,
                "target_dte": getattr(alloc_cfg, "DTE_ROUTER_TARGET_DTE", None),
                "score_min": getattr(alloc_cfg, "DTE_ROUTER_SCORE_MIN", None),
                "trend_lt": getattr(alloc_cfg, "DTE_ROUTER_TREND_LT", None),
                "vix_min": getattr(alloc_cfg, "DTE_ROUTER_VIX_MIN", None),
                "regime_min": getattr(alloc_cfg, "DTE_ROUTER_REGIME_MIN", None),
                "regime_max": getattr(alloc_cfg, "DTE_ROUTER_REGIME_MAX", None),
                "day_cap": getattr(alloc_cfg, "DTE_ROUTER_DAY_CAP", None),
                "alloc_score_cap": getattr(alloc_cfg, "DTE_ROUTER_ALLOC_SCORE_CAP", None),
                "excluded_symbols": list(getattr(alloc_cfg, "DTE_ROUTER_EXCLUDED_SYMBOLS", ()) or ()),
                "routed_call_decisions": routed_call_decisions,
                "routed_call_fills": routed_call_fills,
            },
            "saw_put_ucurve": {
                "enabled": bool(getattr(alloc_cfg, "SAW_PUT_UCURVE_ENABLED", False)),
                "shape": getattr(alloc_cfg, "SAW_PUT_UCURVE_SHAPE", None),
                "midpoint": getattr(alloc_cfg, "SAW_PUT_UCURVE_MIDPOINT", None),
                "halfwidth": getattr(alloc_cfg, "SAW_PUT_UCURVE_HALFWIDTH", None),
                "floor": getattr(alloc_cfg, "SAW_PUT_UCURVE_FLOOR", None),
                "ceil": getattr(alloc_cfg, "SAW_PUT_UCURVE_CEIL", None),
            },
            "ctsl": {
                "enabled": bool(getattr(alloc_cfg, "CTSL_ENABLED", False)),
                "call_target": getattr(alloc_cfg, "CTSL_CALL_TARGET", None),
                "put_target": getattr(alloc_cfg, "CTSL_PUT_TARGET", None),
            },
            "dead_hold": {
                "enabled": bool(getattr(alloc_cfg, "DEAD_HOLD_ENABLED", False)),
                "trigger_pct": round(getattr(alloc_cfg, "DEAD_HOLD_TRIGGER_PNL", 0.0) * 100.0, 1),
                "popout_pct": round(getattr(alloc_cfg, "DEAD_HOLD_POPOUT_PNL", 0.0) * 100.0, 1),
                "popout_entry_multiple": round(1.0 + getattr(alloc_cfg, "DEAD_HOLD_POPOUT_PNL", 0.0), 4),
            },
        },
        "multipliers": {
            "call_f3f": round(call_scale, 4),
            "put_f3f": round(put_scale, 4),
            "put_saw": round(saw_put_scale, 4),
            "put_effective_pre_saturation": round(effective_put_scale, 4),
            "call_saturation": round(call_sat_scale, 4),
            "put_saturation": round(put_sat_scale, 4),
        },
        "tiers": {
            "calls": _tier_rows(
                alloc_cfg,
                "call",
                allocation_base_usd,
                cad_usd_rate,
                call_scale * call_sat_scale,
            ),
            "puts": _tier_rows(
                alloc_cfg,
                "put",
                allocation_base_usd,
                cad_usd_rate,
                effective_put_scale * put_sat_scale,
            ),
        },
        "allocations": {
            "calls": allocated_calls,
            "puts": allocated_puts,
            "all": allocated_calls + allocated_puts,
        },
        "counts": {
            "call_candidates": len(all_call_signals),
            "call_strong_candidates": len(call_signals_75),
            "call_overflow_candidates": len(call_signals_70),
            "put_candidates": len(put_signals),
            "earn_suppressed_puts": earn_suppressed_puts["dropped"],
            "routed_15dte_call_decisions": routed_call_decisions,
            "routed_15dte_call_fills": routed_call_fills,
            "total_candidates": total_candidates,
            "skipped": skipped,
        },
        "summary": {
            "deployed": _money(deployed, cad_usd_rate),
            "remaining": _money(cash_remaining, cad_usd_rate),
            "call_deployed": _money(call_deployed, cad_usd_rate),
            "put_deployed": _money(put_deployed, cad_usd_rate),
            "positions_used": positions,
            "call_positions": n_calls,
            "put_positions": n_puts,
            "max_positions": max_positions,
            "open_base_pct": round(deployed / allocation_base_usd * 100.0, 1)
            if allocation_base_usd
            else None,
            "call_base_pct": round(call_deployed / allocation_base_usd * 100.0, 1)
            if allocation_base_usd
            else None,
            "put_base_pct": round(put_deployed / allocation_base_usd * 100.0, 1)
            if allocation_base_usd
            else None,
        },
        "warnings": warnings,
    }


def _normalize_strategy(strategy: str) -> str:
    token = (strategy or "30dte").lower().replace(" ", "")
    if token in ("30", "30dte"):
        return "30dte"
    if token in ("15", "15dte"):
        return "15dte"
    raise AllocationPlanError("strategy must be 30dte or 15dte")


def _get_cad_usd_rate() -> tuple[float, bool]:
    from database.models.core import ExchangeRate

    try:
        ExchangeRate.ensure_schema()
        cached = ExchangeRate.get_latest("CAD", "USD")
        if cached:
            return float(cached), False
    except Exception:
        pass
    return 0.73, True


def _resolve_score_source(staging_variant: Optional[str], version_token: Optional[str] = None):
    from database.models.core import AlgorithmVersion, Score, StagingScore

    active_version = AlgorithmVersion.get_active_scores_version()
    if active_version is None:
        raise AllocationPlanError("No active algorithm version found.", status_code=404)

    score_model = Score
    version = active_version
    staging_version = None

    if staging_variant == "sector-etf":
        from sector_etf_staging import get_sector_etf_staging_version

        staging_version = get_sector_etf_staging_version(active_version, create=False)
        if staging_version is None:
            raise AllocationPlanError(
                "No sector-etf staging score version found. Run trader update first.",
                status_code=404,
            )
        score_model = StagingScore
        version = staging_version
    elif version_token:
        resolved = _resolve_production_version(version_token)
        if resolved is None:
            raise AllocationPlanError(
                f"Algorithm version not found: {version_token}",
                status_code=404,
            )
        version = resolved

    version_field = score_model.staging_version if staging_version is not None else score_model.version
    latest_date = (
        score_model.select(fn.MAX(score_model.date))
        .where(version_field == version)
        .scalar()
    )
    return score_model, version_field, active_version, version, staging_version, latest_date


def _resolve_production_version(req_version: str):
    from database.models.core import AlgorithmVersion

    token = (req_version or "").strip()
    if not token:
        return None
    resolved = None
    if token.lower().startswith("v") and token[1:].isdigit():
        resolved = AlgorithmVersion.get_by_production_label(int(token[1:]))
    elif token.lower().startswith("db:") and token[3:].isdigit():
        resolved = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(token[3:]))
    elif token.isdigit():
        resolved = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(token))
    if resolved is None:
        resolved = (
            AlgorithmVersion.select()
            .where(
                AlgorithmVersion.git_commit.startswith(token)
                & (~(AlgorithmVersion.git_commit.startswith("shadow/")))
            )
            .first()
        )
    if resolved and AlgorithmVersion.is_legacy_staging_commit(resolved.git_commit):
        return None
    return resolved


def _suppress_earnings_puts(put_signals, latest_date) -> dict[str, Any]:
    if not put_signals:
        return {
            "signals": put_signals,
            "dropped": 0,
            "days": 0,
            "min_score": None,
            "max_score": None,
        }

    from database.models.core import EarningsDate
    from database.utils.trading_calendar import is_trading_day
    import strategy_config

    supp_cfg = strategy_config.STRATEGY_30DTE
    supp_days = supp_cfg.EARN_SUPP_PUT_DAYS
    supp_min = supp_cfg.EARN_SUPP_PUT_MIN_OV
    supp_max = supp_cfg.EARN_SUPP_PUT_MAX_OV

    win_end = latest_date
    remaining_days = supp_days
    while remaining_days > 0:
        win_end += timedelta(days=1)
        if is_trading_day(win_end):
            remaining_days -= 1

    put_symbols = [s.symbol_id for s in put_signals]
    ed_rows = list(
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
        .where(
            (EarningsDate.symbol.in_(put_symbols))
            & (EarningsDate.date > latest_date)
            & (EarningsDate.date <= win_end)
        )
        .tuples()
    )
    ed_symbols = {sym for sym, _date in ed_rows}
    filtered = [
        s
        for s in put_signals
        if not (supp_min <= int(s.overall) <= supp_max and s.symbol_id in ed_symbols)
    ]
    return {
        "signals": filtered,
        "dropped": len(put_signals) - len(filtered),
        "days": supp_days,
        "min_score": supp_min,
        "max_score": supp_max,
    }


def _tier_rows(cfg, side: str, allocation_base_usd: float, cad_usd_rate: float, side_scale: float):
    from portfolio_allocation import AllocationTier

    if side == "call":
        tiers = [
            AllocationTier("call", "ultra", "95+", cfg.TIER_ALLOC["ultra"]),
            AllocationTier("call", "top", "85-94", cfg.TIER_ALLOC["top"]),
            AllocationTier("call", "mid", "80-84", cfg.TIER_ALLOC["mid"]),
            AllocationTier("call", "low", "75-79", cfg.TIER_ALLOC["low"]),
            AllocationTier("call", "overflow", "70-74", cfg.TIER_ALLOC["overflow"]),
        ]
    else:
        tiers = [
            AllocationTier("put", "put_top", "<=15", cfg.PUT_TIER_ALLOC["put_top"]),
            AllocationTier("put", "put_mid", "16-20", cfg.PUT_TIER_ALLOC["put_mid"]),
            AllocationTier("put", "put_low", "21-25", cfg.PUT_TIER_ALLOC["put_low"]),
        ]

    return [
        {
            "label": tier.label,
            "key": tier.key,
            "base_pct": round(tier.base_frac * 100.0, 2),
            "effective_pct": round(tier.base_frac * side_scale * 100.0, 2),
            "amount": _money(allocation_base_usd * tier.base_frac * side_scale, cad_usd_rate),
            "disabled": tier.base_frac <= 0,
        }
        for tier in tiers
    ]


def _money(usd: float, cad_usd_rate: float) -> dict[str, Optional[float]]:
    usd = float(usd or 0.0)
    return {
        "usd": round(usd, 2),
        "cad": round(usd / cad_usd_rate, 2) if cad_usd_rate else None,
    }


def _round_or_none(value, digits: int):
    if value is None:
        return None
    return round(float(value), digits)


def _symbol_text(score_row) -> str:
    symbol_id = getattr(score_row, "symbol_id", None)
    if isinstance(symbol_id, str):
        return symbol_id
    return str(score_row.symbol)


def _regime_state(regime_mult: float) -> str:
    if regime_mult >= 1.02:
        return "bull"
    if regime_mult <= 0.95:
        return "stress"
    return "neutral"


def _version_payload(active_version, selected_version, staging_version=None) -> dict[str, Any]:
    source = "staging" if staging_version is not None else (
        "active" if selected_version.id == active_version.id else "historical"
    )
    payload = {
        "active": {
            "id": active_version.id,
            "label": active_version.production_label,
            "git_commit": active_version.git_commit,
            "git_message": active_version.git_message,
        },
        "source": source,
    }
    if staging_version is not None:
        payload["staging"] = {
            "id": staging_version.id,
            "variant": staging_version.variant,
            "label": staging_version.label,
            "status": staging_version.status,
            "base_version_id": staging_version.base_version_id,
            "base_label": staging_version.base_version.production_label,
        }
        payload["selected"] = {
            "id": staging_version.id,
            "label": f"staging s{staging_version.id}",
            "git_commit": None,
            "git_message": staging_version.label,
        }
    else:
        payload["selected"] = {
            "id": selected_version.id,
            "label": selected_version.production_label,
            "git_commit": selected_version.git_commit,
            "git_message": getattr(selected_version, "git_message", None),
        }
    return payload
