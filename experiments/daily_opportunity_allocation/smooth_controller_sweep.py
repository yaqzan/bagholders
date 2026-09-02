"""Branch-separated smooth daily exposure-controller sweep.

This Stage 3 research harness searches transparent smooth allocation waves over
the v60 daily opportunity state. It keeps branches separate by design:

* put-overhang + weak execution put throttle
* scarce-opportunity gross exposure throttle
* Market Wave divergence throttle
* Market Wave divergence put-side exposure wave
* Market Wave divergence put-side cash-reserve wave
* healthy-call-demand drawdown re-entry boost

It does not write scores, mutate strategy constants, or bump ALGORITHM_VERSION.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.daily_opportunity_allocation.mine_conditional_alpha import _add_derived
from strategy_config import STRATEGY_30DTE


EXP_DIR = Path(__file__).resolve().parent
DEFAULT_DAILY = EXP_DIR / "daily_state.csv"
DEFAULT_START = date(2020, 1, 1)
DEFAULT_TRAIN_END = date(2024, 12, 31)
DEFAULT_TEST_START = date(2025, 1, 1)
DEFAULT_INITIAL = 50_000.0
PUT_WAVE_BRANCHES = {"wave_put_divergence", "wave_put_divergence_strict", "wave_put_divergence_reserve"}

WINDOWS = [
    ("train", DEFAULT_START, DEFAULT_TRAIN_END),
    ("test", DEFAULT_TEST_START, None),
    ("full", DEFAULT_START, None),
    ("2020-crash", date(2020, 2, 18), date(2020, 4, 30)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2024", date(2024, 1, 1), date(2024, 12, 31)),
    ("2025", date(2025, 1, 1), date(2025, 12, 31)),
    ("22-now", date(2022, 1, 1), None),
    ("5y", date(2021, 1, 1), None),
]

WINDOW_WEIGHTS = {
    "test": 3.0,
    "full": 2.2,
    "22-now": 2.6,
    "5y": 2.6,
    "2020-crash": 2.4,
    "2024": 2.4,
    "2022": 1.5,
    "2025": 1.5,
    "train": 0.25,
}


@dataclass(frozen=True)
class SmoothParams:
    branch: str
    name: str
    strength: float = 0.0
    floor_scale: float = 1.0
    boost_strength: float = 0.0
    boost_ceiling: float = 1.15
    weak_mode: str = "max"
    weak_tp_floor: float = 0.5133
    weak_tp_width: float = 0.16
    weak_pnl_floor: float = 0.0674
    weak_pnl_width: float = 0.12
    open_put_trigger: float = 7.0
    open_put_width: float = 6.0
    filled_put_trigger: float = 2.0
    filled_put_width: float = 5.0
    open_put_share_trigger: float = 0.70
    open_put_share_width: float = 0.25
    scarce_call_trigger: float = 0.20
    scarce_call_width: float = 0.40
    scarce_total_trigger: float = 0.78
    scarce_total_width: float = 0.85
    breadth_floor: float = 51.27
    breadth_width: float = 25.0
    wave_threshold: float = 3.72
    wave_width: float = 25.0
    reentry_dd: float = 0.0956
    reentry_dd_width: float = 0.20
    reentry_call_trigger: float = 0.40
    reentry_call_width: float = 0.70
    benchmark_hard_cap: bool = False


def baseline_params() -> SmoothParams:
    return SmoothParams(branch="baseline", name="baseline")


def legacy_put_cap_benchmark() -> SmoothParams:
    return SmoothParams(
        branch="benchmark",
        name="legacy_put_overhang_pnl__puts70_cap5",
        strength=0.30,
        floor_scale=0.70,
        weak_pnl_floor=0.07378,
        weak_pnl_width=0.0001,
        open_put_trigger=7.0,
        open_put_width=0.0001,
        benchmark_hard_cap=True,
    )


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _smoothstep(t: float) -> float:
    x = max(0.0, min(1.0, t))
    return x * x * (3.0 - 2.0 * x)


def _above(value: float, trigger: float, width: float) -> float:
    if width <= 0:
        return 1.0 if value >= trigger else 0.0
    return _smoothstep((value - trigger) / width)


def _below(value: float, trigger: float, width: float) -> float:
    if width <= 0:
        return 1.0 if value <= trigger else 0.0
    return _smoothstep((trigger - value) / width)


def _read_daily(path: Path) -> dict[date, dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda row: row["date"])
    for row in rows:
        row["_date"] = _as_date(row["date"])
    _add_derived(rows)
    return {_as_date(row["date"]): row for row in rows}


def _window_days(all_days: list[date], start: date, end: date | None) -> list[date]:
    settle_end = end + timedelta(days=25) if end else None
    return [d for d in all_days if d >= start and (settle_end is None or d <= settle_end)]


def risk_components(row: dict[str, Any], params: SmoothParams, running_dd: float) -> dict[str, float]:
    prev_tp = _float(row.get("prev5_entry_tp_rate"), 0.63)
    prev_pnl = _float(row.get("prev5_entry_avg_pnl_pct"), 0.16)
    weak_tp = _below(prev_tp, params.weak_tp_floor, params.weak_tp_width)
    weak_pnl = _below(prev_pnl, params.weak_pnl_floor, params.weak_pnl_width)
    if params.weak_mode == "tp":
        recent_weak = weak_tp
    elif params.weak_mode == "pnl":
        recent_weak = weak_pnl
    elif params.weak_mode == "both":
        recent_weak = weak_tp * weak_pnl
    else:
        recent_weak = max(weak_tp, weak_pnl)

    open_put = _float(row.get("open_put_n"))
    filled_put = _float(row.get("filled_put_n"))
    open_put_share = _float(row.get("open_put_share"))
    put_overhang = max(
        _above(open_put, params.open_put_trigger, params.open_put_width),
        _above(filled_put, params.filled_put_trigger, params.filled_put_width),
        _above(open_put_share, params.open_put_share_trigger, params.open_put_share_width),
    )

    call_demand = _float(row.get("post_call_primary_alloc_demand"))
    total_demand = _float(row.get("post_total_primary_alloc_demand"))
    breadth = _float(row.get("breadth_pct_above_ema50"), 60.0)
    scarce = max(
        _below(call_demand, params.scarce_call_trigger, params.scarce_call_width),
        _below(total_demand, params.scarce_total_trigger, params.scarce_total_width),
        _below(breadth, params.breadth_floor, params.breadth_width),
    )

    wave_signed = _float(row.get("breadth_sector_etf_market_wave_signed"), -20.0)
    wave_divergence = _above(wave_signed, params.wave_threshold, params.wave_width)

    dd_wave = _above(running_dd, params.reentry_dd, params.reentry_dd_width)
    call_health = _above(call_demand, params.reentry_call_trigger, params.reentry_call_width)
    reentry = dd_wave * call_health * (1.0 - 0.50 * recent_weak)

    return {
        "recent_weak": recent_weak,
        "weak_tp": weak_tp,
        "weak_pnl": weak_pnl,
        "put_overhang": put_overhang,
        "scarce": scarce,
        "wave_divergence": wave_divergence,
        "reentry": max(0.0, min(1.0, reentry)),
    }


def policy_scale(side: str, row: dict[str, Any], params: SmoothParams, running_dd: float) -> tuple[float, dict[str, float]]:
    risk = risk_components(row, params, running_dd)
    scale = 1.0
    if params.branch == "put_overhang" and side == "put":
        pressure = risk["recent_weak"] * risk["put_overhang"]
        scale = max(params.floor_scale, 1.0 - params.strength * pressure)
    elif params.branch == "scarce_opps":
        pressure = risk["recent_weak"] * risk["scarce"]
        scale = max(params.floor_scale, 1.0 - params.strength * pressure)
    elif params.branch == "wave_divergence":
        pressure = risk["recent_weak"] * risk["wave_divergence"]
        scale = max(params.floor_scale, 1.0 - params.strength * pressure)
    elif params.branch in PUT_WAVE_BRANCHES and side == "put":
        pressure = risk["recent_weak"] * risk["wave_divergence"] * (0.5 + 0.5 * risk["put_overhang"])
        scale = max(params.floor_scale, 1.0 - params.strength * pressure)
    elif params.branch == "reentry" and side == "call":
        scale = min(params.boost_ceiling, 1.0 + params.boost_strength * risk["reentry"])
    elif params.benchmark_hard_cap and side == "put":
        active = _float(row.get("open_put_n")) >= 7.0 and _float(row.get("prev5_entry_avg_pnl_pct"), 1.0) <= 0.07378
        scale = 0.70 if active else 1.0
        risk["benchmark_active"] = 1.0 if active else 0.0
    return max(0.35, min(1.35, scale)), risk


def cash_reserve_enabled(params: SmoothParams) -> bool:
    return params.branch == "wave_put_divergence_reserve"


def _portfolio_caps() -> tuple[int, int, bool]:
    max_pos = int(STRATEGY_30DTE.MAX_POSITIONS)
    cap_call = STRATEGY_30DTE.MAX_POSITIONS_CALL if STRATEGY_30DTE.MAX_POSITIONS_CALL is not None else max_pos
    cap_put = STRATEGY_30DTE.MAX_POSITIONS_PUT if STRATEGY_30DTE.MAX_POSITIONS_PUT is not None else max_pos
    return int(cap_call), int(cap_put), STRATEGY_30DTE.MAX_POSITIONS_CALL is not None or STRATEGY_30DTE.MAX_POSITIONS_PUT is not None


def run_policy(
    params: SmoothParams,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    initial: float,
    entry_start: date,
    entry_end: date | None,
) -> dict[str, Any]:
    max_pos = int(STRATEGY_30DTE.MAX_POSITIONS)
    cap_call, cap_put, side_capped = _portfolio_caps()
    cash = initial
    open_pos: list[bc.OpenPosition] = []
    trade_log: list[dict[str, Any]] = []
    equity_curve: list[tuple[date, float]] = []
    peak = initial
    max_dd = 0.0
    signal_days = filled_days = active_days = actioned_fills = hard_put_cap_skips = 0
    scale_sum = scale_n = 0.0
    risk_sum = risk_n = 0.0
    open_counts: list[int] = []
    cash_reserves: list[tuple[date, float]] = []
    reserve_pct_sum = reserve_n = 0.0
    max_reserve_pct = 0.0

    for today in _window_days(all_days, entry_start, entry_end):
        remaining: list[bc.OpenPosition] = []
        for pos in open_pos:
            if pos.outcome.outcome != "open" and pos.outcome.exit_date <= today:
                proceeds = pos.premium * (1.0 + pos.outcome.net_return)
                cash += proceeds
                trade_log.append(
                    {
                        "entry_date": pos.outcome.signal_date,
                        "exit_date": pos.outcome.exit_date,
                        "symbol": pos.outcome.symbol,
                        "score": pos.outcome.score,
                        "tier": pos.outcome.tier,
                        "premium": pos.premium,
                        "outcome": pos.outcome.outcome,
                        "hold_bars": pos.outcome.hold_bars,
                        "pnl": proceeds - pos.premium,
                        "pnl_pct": pos.outcome.net_return,
                        "stressed": pos.outcome.stressed,
                        "side": pos.outcome.side,
                    }
                )
            else:
                remaining.append(pos)
        open_pos = remaining
        cash_reserves = [(release, amount) for release, amount in cash_reserves if release > today and amount > 0]

        equity = cash + sum(p.premium for p in open_pos)
        reserved_cash = sum(amount for _, amount in cash_reserves)
        if equity > peak:
            peak = equity
        running_dd = 1.0 - equity / peak if peak > 0 else 0.0
        row = daily.get(today, {})
        _, day_risk = policy_scale("call", row, params, running_dd)
        weak_wave = day_risk.get("recent_weak", 0.0) * day_risk.get("wave_divergence", 0.0)
        branch_pressure = max(
            day_risk.get("recent_weak", 0.0) * day_risk.get("put_overhang", 0.0),
            day_risk.get("recent_weak", 0.0) * day_risk.get("scarce", 0.0),
            weak_wave,
            weak_wave * (0.5 + 0.5 * day_risk.get("put_overhang", 0.0)),
            day_risk.get("reentry", 0.0),
            day_risk.get("benchmark_active", 0.0),
        )
        risk_sum += branch_pressure
        risk_n += 1
        if branch_pressure >= 0.35:
            active_days += 1

        open_syms = {p.outcome.symbol for p in open_pos}
        day_outcomes = outcomes_by_date.get(today, [])
        entry_allowed = today >= entry_start and (entry_end is None or today <= entry_end)
        if day_outcomes and entry_allowed:
            signal_days += 1
        n_before = len(open_pos)

        for outcome in day_outcomes if entry_allowed else []:
            if outcome.symbol in open_syms:
                continue
            is_put = getattr(outcome, "side", "call") == "put"
            call_open = sum(1 for p in open_pos if p.outcome.side == "call")
            put_open = len(open_pos) - call_open
            if len(open_pos) >= max_pos:
                break
            if side_capped and not is_put and call_open >= cap_call:
                continue
            if side_capped and is_put and put_open >= cap_put:
                continue
            if params.benchmark_hard_cap and is_put and day_risk.get("benchmark_active", 0.0) and put_open >= 5:
                hard_put_cap_skips += 1
                continue

            reg_mult = (
                bc.regime_on_or_before(
                    regime_dates,
                    regime_map,
                    today,
                    breadth_enabled=bc.BREADTH_ALLOC_ENABLED,
                )
                if regime_dates
                else (50.0 if bc.BREADTH_ALLOC_ENABLED else 1.0)
            )
            reg_scale = bc.alloc_scale_for(reg_mult, is_put=is_put)
            alloc_frac = bc.TIER_ALLOC.get(outcome.tier, 0.0)
            dd_scale = 1.0
            if (
                not is_put
                and bc.DD_SOFT_BAND_HI > bc.DD_SOFT_BAND_LO
                and running_dd > bc.DD_SOFT_BAND_LO
            ):
                if running_dd >= bc.DD_SOFT_BAND_HI:
                    dd_scale = bc.DD_SOFT_CALL_FLOOR
                else:
                    t = (running_dd - bc.DD_SOFT_BAND_LO) / (bc.DD_SOFT_BAND_HI - bc.DD_SOFT_BAND_LO)
                    dd_scale = 1.0 - t * (1.0 - bc.DD_SOFT_CALL_FLOOR)
            saw_scale = bc.saw_put_ucurve_scale(today) if is_put else 1.0
            side = "put" if is_put else "call"
            pscale, _ = policy_scale(side, row, params, running_dd)
            base_premium = alloc_frac * reg_scale * dd_scale * saw_scale * equity
            premium = base_premium * pscale
            available_cash = cash - reserved_cash
            required_cash = base_premium if cash_reserve_enabled(params) and is_put and pscale < 0.999 else premium
            if available_cash < required_cash or premium < 10.0:
                continue
            if abs(pscale - 1.0) > 1e-9:
                actioned_fills += 1
            scale_sum += pscale
            scale_n += 1
            cash -= premium
            if cash_reserve_enabled(params) and is_put and pscale < 0.999:
                reserve = max(0.0, base_premium - premium)
                if reserve >= 10.0:
                    cash_reserves.append((outcome.exit_date, reserve))
                    reserved_cash += reserve
            open_pos.append(bc.OpenPosition(outcome=outcome, premium=premium, entry_eq=equity))
            open_syms.add(outcome.symbol)
            equity = cash + sum(p.premium for p in open_pos)
            reserve_pct = reserved_cash / equity if equity > 0 else 0.0
            max_reserve_pct = max(max_reserve_pct, reserve_pct)

        if len(open_pos) > n_before:
            filled_days += 1
        if equity > peak:
            peak = equity
        dd = 1.0 - equity / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        equity_curve.append((today, equity))
        open_counts.append(len(open_pos))
        reserve_pct = reserved_cash / equity if equity > 0 else 0.0
        reserve_pct_sum += reserve_pct
        reserve_n += 1
        max_reserve_pct = max(max_reserve_pct, reserve_pct)

    final = cash + sum(p.premium for p in open_pos)
    calls = [t for t in trade_log if t["side"] == "call"]
    puts = [t for t in trade_log if t["side"] == "put"]
    tp = sum(1 for t in trade_log if t["outcome"] == "tp")
    call_tp = sum(1 for t in calls if t["outcome"] == "tp")
    put_tp = sum(1 for t in puts if t["outcome"] == "tp")
    log_return = math.log(final / initial) if final > 0 and initial > 0 else -999.0
    return {
        "final_equity": final,
        "log_return": log_return,
        "max_dd": max_dd,
        "trades": len(trade_log),
        "call_trades": len(calls),
        "put_trades": len(puts),
        "tp_rate": tp / len(trade_log) if trade_log else None,
        "call_tp_rate": call_tp / len(calls) if calls else None,
        "put_tp_rate": put_tp / len(puts) if puts else None,
        "signal_days": signal_days,
        "filled_days": filled_days,
        "active_days": active_days,
        "actioned_fills": actioned_fills,
        "hard_put_cap_skips": hard_put_cap_skips,
        "avg_open_slots": sum(open_counts) / len(open_counts) if open_counts else None,
        "avg_policy_scale": scale_sum / scale_n if scale_n else 1.0,
        "avg_branch_pressure": risk_sum / risk_n if risk_n else 0.0,
        "avg_reserved_cash_pct": reserve_pct_sum / reserve_n if reserve_n else 0.0,
        "max_reserved_cash_pct": max_reserve_pct,
    }


def evaluate_params(
    params: SmoothParams,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    initial: float,
) -> dict[str, dict[str, Any]]:
    return {
        label: run_policy(params, outcomes_by_date, all_days, daily, regime_dates, regime_map, initial, start, end)
        for label, start, end in WINDOWS
    }


def _candidate_pool(branch: str, budget: int, seed: int) -> list[SmoothParams]:
    rng = random.Random(seed + sum(ord(c) for c in branch))
    out: list[SmoothParams] = []
    seen: set[str] = set()

    def add(params: SmoothParams) -> None:
        payload = json.dumps(asdict(params), sort_keys=True)
        if payload in seen:
            return
        seen.add(payload)
        out.append(params)

    if branch == "put_overhang":
        seeds = [
            dict(strength=0.14, floor_scale=0.86, weak_mode="pnl", open_put_trigger=7.0, open_put_width=7.0),
            dict(strength=0.18, floor_scale=0.82, weak_mode="pnl", open_put_trigger=7.0, open_put_width=8.0),
            dict(strength=0.22, floor_scale=0.78, weak_mode="max", open_put_trigger=7.0, open_put_width=8.0),
            dict(strength=0.26, floor_scale=0.74, weak_mode="pnl", open_put_trigger=9.0, open_put_width=6.0),
        ]
        for i, kwargs in enumerate(seeds):
            add(SmoothParams(branch=branch, name=f"{branch}_seed_{i}", **kwargs))
        while len(out) < budget:
            add(
                SmoothParams(
                    branch=branch,
                    name=f"{branch}_{len(out):04d}",
                    strength=rng.choice([0.10, 0.14, 0.18, 0.22, 0.26, 0.30]),
                    floor_scale=rng.choice([0.72, 0.76, 0.80, 0.84, 0.88, 0.92]),
                    weak_mode=rng.choice(["pnl", "tp", "max", "both"]),
                    weak_tp_floor=rng.choice([0.50, 0.5133, 0.539, 0.56]),
                    weak_pnl_floor=rng.choice([0.04, 0.0674, 0.0738, 0.09]),
                    open_put_trigger=rng.choice([5.0, 7.0, 9.0, 11.0]),
                    open_put_width=rng.choice([4.0, 6.0, 8.0, 10.0]),
                    filled_put_trigger=rng.choice([1.0, 2.0, 3.0]),
                    filled_put_width=rng.choice([3.0, 5.0, 7.0]),
                    open_put_share_trigger=rng.choice([0.55, 0.65, 0.75, 0.85]),
                    open_put_share_width=rng.choice([0.15, 0.25, 0.35]),
                )
            )
    elif branch == "scarce_opps":
        while len(out) < budget:
            add(
                SmoothParams(
                    branch=branch,
                    name=f"{branch}_{len(out):04d}",
                    strength=rng.choice([0.10, 0.16, 0.22, 0.30, 0.38]),
                    floor_scale=rng.choice([0.65, 0.72, 0.80, 0.88]),
                    weak_mode=rng.choice(["tp", "pnl", "max"]),
                    weak_tp_floor=rng.choice([0.4489, 0.50, 0.5133, 0.539]),
                    weak_pnl_floor=rng.choice([0.0, 0.04, 0.0674, 0.0738]),
                    scarce_call_trigger=rng.choice([0.10, 0.20, 0.30, 0.40]),
                    scarce_call_width=rng.choice([0.25, 0.40, 0.60]),
                    scarce_total_trigger=rng.choice([0.52, 0.78, 1.00, 1.12]),
                    scarce_total_width=rng.choice([0.50, 0.80, 1.10]),
                    breadth_floor=rng.choice([45.0, 51.27, 58.0]),
                    breadth_width=rng.choice([15.0, 25.0, 35.0]),
                )
            )
    elif branch == "wave_divergence":
        while len(out) < budget:
            add(
                SmoothParams(
                    branch=branch,
                    name=f"{branch}_{len(out):04d}",
                    strength=rng.choice([0.10, 0.16, 0.22, 0.30, 0.38]),
                    floor_scale=rng.choice([0.65, 0.72, 0.80, 0.88]),
                    weak_mode=rng.choice(["tp", "pnl", "max", "both"]),
                    weak_tp_floor=rng.choice([0.50, 0.5133, 0.539, 0.56]),
                    weak_pnl_floor=rng.choice([0.04, 0.0674, 0.0738, 0.09]),
                    wave_threshold=rng.choice([0.0, 3.72, 7.5, 10.0, 15.0]),
                    wave_width=rng.choice([12.0, 20.0, 30.0, 45.0]),
                )
            )
    elif branch == "wave_put_divergence":
        seeds = [
            dict(strength=0.35, floor_scale=0.65, weak_mode="tp", weak_tp_floor=0.5133, weak_tp_width=0.10, wave_threshold=3.72, wave_width=6.0),
            dict(strength=0.45, floor_scale=0.55, weak_mode="tp", weak_tp_floor=0.5133, weak_tp_width=0.12, wave_threshold=3.72, wave_width=10.0),
            dict(strength=0.55, floor_scale=0.45, weak_mode="tp", weak_tp_floor=0.5390, weak_tp_width=0.12, wave_threshold=3.72, wave_width=12.0),
            dict(strength=0.65, floor_scale=0.35, weak_mode="tp", weak_tp_floor=0.5600, weak_tp_width=0.16, wave_threshold=5.00, wave_width=12.0),
            dict(strength=0.35, floor_scale=0.70, weak_mode="max", weak_tp_floor=0.5133, weak_pnl_floor=0.0674, wave_threshold=3.72, wave_width=8.0),
        ]
        for i, kwargs in enumerate(seeds):
            add(SmoothParams(branch=branch, name=f"{branch}_seed_{i}", **kwargs))
        while len(out) < budget:
            add(
                SmoothParams(
                    branch=branch,
                    name=f"{branch}_{len(out):04d}",
                    strength=rng.choice([0.18, 0.25, 0.35, 0.45, 0.55, 0.65, 0.80]),
                    floor_scale=rng.choice([0.35, 0.45, 0.55, 0.65, 0.75, 0.85]),
                    weak_mode=rng.choice(["tp", "tp", "max", "both"]),
                    weak_tp_floor=rng.choice([0.48, 0.50, 0.5133, 0.539, 0.56, 0.58]),
                    weak_tp_width=rng.choice([0.06, 0.08, 0.10, 0.12, 0.16, 0.20]),
                    weak_pnl_floor=rng.choice([0.04, 0.0674, 0.0738, 0.09]),
                    weak_pnl_width=rng.choice([0.08, 0.12, 0.16]),
                    wave_threshold=rng.choice([0.0, 3.72, 5.0, 7.5, 10.0]),
                    wave_width=rng.choice([4.0, 6.0, 8.0, 12.0, 16.0, 24.0]),
                    open_put_trigger=rng.choice([5.0, 7.0, 9.0, 11.0]),
                    open_put_width=rng.choice([4.0, 6.0, 8.0, 10.0]),
                    filled_put_trigger=rng.choice([1.0, 2.0, 3.0]),
                    filled_put_width=rng.choice([3.0, 5.0, 7.0]),
                    open_put_share_trigger=rng.choice([0.45, 0.55, 0.65, 0.75]),
                    open_put_share_width=rng.choice([0.15, 0.25, 0.35]),
                )
            )
    elif branch in {"wave_put_divergence_strict", "wave_put_divergence_reserve"}:
        seeds = [
            dict(
                strength=0.65,
                floor_scale=0.35,
                weak_mode="tp",
                weak_tp_floor=0.52,
                weak_tp_width=0.08,
                open_put_trigger=13.0,
                open_put_width=6.0,
                filled_put_trigger=1.0,
                filled_put_width=3.0,
                open_put_share_trigger=0.65,
                open_put_share_width=0.15,
                wave_threshold=3.72,
                wave_width=12.0,
            ),
            dict(
                strength=0.65,
                floor_scale=0.35,
                weak_mode="tp",
                weak_tp_floor=0.50,
                weak_tp_width=0.08,
                open_put_trigger=11.0,
                open_put_width=8.0,
                filled_put_trigger=1.0,
                filled_put_width=5.0,
                open_put_share_trigger=0.75,
                open_put_share_width=0.25,
                wave_threshold=0.0,
                wave_width=12.0,
            ),
            dict(
                strength=0.55,
                floor_scale=0.45,
                weak_mode="tp",
                weak_tp_floor=0.50,
                weak_tp_width=0.08,
                open_put_trigger=11.0,
                open_put_width=8.0,
                filled_put_trigger=1.0,
                filled_put_width=5.0,
                open_put_share_trigger=0.75,
                open_put_share_width=0.25,
                wave_threshold=0.0,
                wave_width=12.0,
            ),
            dict(
                strength=0.65,
                floor_scale=0.45,
                weak_mode="tp",
                weak_tp_floor=0.50,
                weak_tp_width=0.10,
                open_put_trigger=9.0,
                open_put_width=8.0,
                filled_put_trigger=1.0,
                filled_put_width=5.0,
                open_put_share_trigger=0.65,
                open_put_share_width=0.25,
                wave_threshold=3.72,
                wave_width=12.0,
            ),
            dict(
                strength=0.80,
                floor_scale=0.45,
                weak_mode="tp",
                weak_tp_floor=0.58,
                weak_tp_width=0.10,
                open_put_trigger=7.0,
                open_put_width=8.0,
                filled_put_trigger=1.0,
                filled_put_width=7.0,
                open_put_share_trigger=0.55,
                open_put_share_width=0.15,
                wave_threshold=0.0,
                wave_width=16.0,
            ),
            dict(
                strength=0.60,
                floor_scale=0.45,
                weak_mode="tp",
                weak_tp_floor=0.52,
                weak_tp_width=0.10,
                open_put_trigger=9.0,
                open_put_width=8.0,
                filled_put_trigger=1.0,
                filled_put_width=5.0,
                open_put_share_trigger=0.65,
                open_put_share_width=0.25,
                wave_threshold=0.0,
                wave_width=14.0,
            ),
        ]
        for i, kwargs in enumerate(seeds):
            add(SmoothParams(branch=branch, name=f"{branch}_seed_{i}", **kwargs))
        while len(out) < budget:
            add(
                SmoothParams(
                    branch=branch,
                    name=f"{branch}_{len(out):04d}",
                    strength=rng.choice([0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]),
                    floor_scale=rng.choice([0.35, 0.40, 0.45, 0.50, 0.55, 0.60]),
                    weak_mode="tp",
                    weak_tp_floor=rng.choice([0.48, 0.50, 0.5133, 0.52, 0.539, 0.56]),
                    weak_tp_width=rng.choice([0.06, 0.08, 0.10, 0.12, 0.14]),
                    weak_pnl_floor=rng.choice([0.04, 0.0674, 0.0738, 0.09]),
                    weak_pnl_width=rng.choice([0.08, 0.12, 0.16]),
                    wave_threshold=rng.choice([0.0, 0.0, 3.72, 5.0, 7.5]),
                    wave_width=rng.choice([6.0, 8.0, 10.0, 12.0, 14.0, 16.0]),
                    open_put_trigger=rng.choice([7.0, 9.0, 11.0, 13.0]),
                    open_put_width=rng.choice([6.0, 8.0, 10.0, 12.0]),
                    filled_put_trigger=rng.choice([1.0, 1.0, 2.0]),
                    filled_put_width=rng.choice([3.0, 5.0, 7.0]),
                    open_put_share_trigger=rng.choice([0.55, 0.65, 0.75, 0.85]),
                    open_put_share_width=rng.choice([0.15, 0.25, 0.35]),
                )
            )
    elif branch == "reentry":
        while len(out) < budget:
            add(
                SmoothParams(
                    branch=branch,
                    name=f"{branch}_{len(out):04d}",
                    boost_strength=rng.choice([0.04, 0.06, 0.08, 0.10, 0.12]),
                    boost_ceiling=rng.choice([1.06, 1.08, 1.10, 1.12, 1.15]),
                    weak_mode=rng.choice(["tp", "pnl", "max"]),
                    weak_tp_floor=rng.choice([0.4489, 0.50, 0.5133]),
                    weak_pnl_floor=rng.choice([0.0, 0.04, 0.0674]),
                    reentry_dd=rng.choice([0.06, 0.08, 0.0956, 0.12, 0.15]),
                    reentry_dd_width=rng.choice([0.10, 0.16, 0.22, 0.30]),
                    reentry_call_trigger=rng.choice([0.30, 0.40, 0.55, 0.70]),
                    reentry_call_width=rng.choice([0.40, 0.70, 1.00]),
                )
            )
    return out


def utility(candidate: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = 0.0
    reasons: list[str] = []
    per_window: dict[str, float] = {}
    for label, cand in candidate.items():
        base = baseline[label]
        dd_delta = (base["max_dd"] - cand["max_dd"]) * 100.0
        log_delta = cand["log_return"] - base["log_return"]
        trade_ratio = cand["trades"] / base["trades"] if base["trades"] else 1.0
        tp_delta = (_float(cand.get("tp_rate")) - _float(base.get("tp_rate"))) * 100.0
        weight = WINDOW_WEIGHTS.get(label, 1.0)
        u = weight * (2.4 * dd_delta + 0.35 * max(-4.0, min(4.0, log_delta)) + 0.10 * tp_delta)
        if dd_delta < -0.50:
            u -= weight * 8.0 * abs(dd_delta)
            reasons.append(f"{label}:DD worse {abs(dd_delta):.2f}pp")
        if label in {"2020-crash", "2024"} and dd_delta < -0.10:
            u -= weight * 12.0 * abs(dd_delta)
            reasons.append(f"{label}:fail-window DD worse {abs(dd_delta):.2f}pp")
        if trade_ratio < (0.92 if label in {"test", "full", "22-now", "5y"} else 0.85):
            u -= weight * 15.0 * (1.0 - trade_ratio)
            reasons.append(f"{label}:trades {trade_ratio:.1%}")
        if label in {"2024", "test"} and log_delta < -0.80 and dd_delta < 0.50:
            u -= weight * 6.0
            reasons.append(f"{label}:return drag")
        per_window[label] = u
        total += u
    failwindow_pass = (
        (baseline["2020-crash"]["max_dd"] - candidate["2020-crash"]["max_dd"]) * 100.0 >= -0.10
        and (baseline["2024"]["max_dd"] - candidate["2024"]["max_dd"]) * 100.0 >= -0.10
    )
    return {
        "utility": total,
        "reasons": reasons,
        "per_window": per_window,
        "failwindow_pass": failwindow_pass,
        "test_dd_delta_pp": (baseline["test"]["max_dd"] - candidate["test"]["max_dd"]) * 100.0,
        "full_dd_delta_pp": (baseline["full"]["max_dd"] - candidate["full"]["max_dd"]) * 100.0,
        "twonow_dd_delta_pp": (baseline["22-now"]["max_dd"] - candidate["22-now"]["max_dd"]) * 100.0,
        "fivey_dd_delta_pp": (baseline["5y"]["max_dd"] - candidate["5y"]["max_dd"]) * 100.0,
        "crash_dd_delta_pp": (baseline["2020-crash"]["max_dd"] - candidate["2020-crash"]["max_dd"]) * 100.0,
        "y2024_dd_delta_pp": (baseline["2024"]["max_dd"] - candidate["2024"]["max_dd"]) * 100.0,
        "y2024_log_delta": candidate["2024"]["log_return"] - baseline["2024"]["log_return"],
        "test_log_delta": candidate["test"]["log_return"] - baseline["test"]["log_return"],
        "full_log_delta": candidate["full"]["log_return"] - baseline["full"]["log_return"],
    }


def _flatten_record(rank: int, rec: dict[str, Any]) -> dict[str, Any]:
    params: SmoothParams = rec["params"]
    info = rec["info"]
    out: dict[str, Any] = {
        "rank": rank,
        "policy": params.name,
        "branch": params.branch,
        "utility": info["utility"],
        "failwindow_pass": info["failwindow_pass"],
        "reasons": "; ".join(info["reasons"][:8]),
        "test_dd_delta_pp": info["test_dd_delta_pp"],
        "full_dd_delta_pp": info["full_dd_delta_pp"],
        "twonow_dd_delta_pp": info["twonow_dd_delta_pp"],
        "fivey_dd_delta_pp": info["fivey_dd_delta_pp"],
        "crash_dd_delta_pp": info["crash_dd_delta_pp"],
        "y2024_dd_delta_pp": info["y2024_dd_delta_pp"],
        "y2024_log_delta": info["y2024_log_delta"],
        "test_log_delta": info["test_log_delta"],
        "full_log_delta": info["full_log_delta"],
        "params_json": json.dumps(asdict(params), sort_keys=True),
    }
    for label, row in rec["windows"].items():
        out[f"{label}_log"] = row["log_return"]
        out[f"{label}_max_dd"] = row["max_dd"]
        out[f"{label}_trades"] = row["trades"]
        out[f"{label}_call_trades"] = row["call_trades"]
        out[f"{label}_put_trades"] = row["put_trades"]
        out[f"{label}_avg_scale"] = row["avg_policy_scale"]
        out[f"{label}_active_days"] = row["active_days"]
        out[f"{label}_actioned_fills"] = row["actioned_fills"]
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                cols.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(path: Path, baseline: dict[str, Any], ranked: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    lines = [
        "# Smooth Daily Exposure Controller Sweep",
        "",
        "Experiment-local Stage 3 deterministic screen. This is not a ship result.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- budget_per_branch: {meta['budget_per_branch']}",
        f"- branches: {', '.join(meta.get('branches', []))}",
        f"- daily_state: `{meta['daily_state']}`",
        f"- max_positions: {STRATEGY_30DTE.MAX_POSITIONS}, call_cap: {STRATEGY_30DTE.MAX_POSITIONS_CALL}",
        "",
        "## Baseline",
        "",
        "| window | log_return | max DD | trades | calls | puts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, *_ in WINDOWS:
        row = baseline[label]
        lines.append(
            f"| {label} | {row['log_return']:+.3f} | {row['max_dd']:.1%} | "
            f"{row['trades']} | {row['call_trades']} | {row['put_trades']} |"
        )

    lines.extend(
        [
            "",
            "## Top Branch Candidates",
            "",
            "| rank | branch | policy | utility | fail-window | crash DD d | 2024 DD d | 2024 log d | 5y DD d | reasons |",
            "|---:|---|---|---:|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in ranked[:40]:
        lines.append(
            f"| {row['rank']} | {row['branch']} | `{row['policy']}` | {float(row['utility']):+.2f} | "
            f"{row['failwindow_pass']} | {float(row['crash_dd_delta_pp']):+.2f}pp | "
            f"{float(row['y2024_dd_delta_pp']):+.2f}pp | {float(row['y2024_log_delta']):+.2f} | "
            f"{float(row['fivey_dd_delta_pp']):+.2f}pp | {row['reasons']} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- Branches are intentionally isolated. Do not combine until fail-window MC is stable.",
            "- The legacy hard put cap is a benchmark row only; the target candidates are smooth exposure waves.",
            "- Deterministic pass still requires stable-seed MC on 2020-crash and 2024 before broader validation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=None)
    parser.add_argument("--initial", type=float, default=DEFAULT_INITIAL)
    parser.add_argument("--budget-per-branch", type=int, default=180)
    parser.add_argument("--seed", type=int, default=20260519)
    parser.add_argument("--branches", default="put_overhang,scarce_opps,wave_divergence,reentry")
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "smooth_controller_status.json"
    eval_log = out_dir / "smooth_controller_evals.jsonl"
    if eval_log.exists():
        eval_log.unlink()

    started_at = datetime.now(timezone.utc).isoformat()
    _write_status(status_path, {"phase": "smooth_controller_sweep", "state": "loading", "started_at": started_at})

    daily = _read_daily(args.daily)
    start = _as_date(args.start)
    end = _as_date(args.end) if args.end else None
    av = AlgorithmVersion.get_by_id(args.version_id) if args.version_id else AlgorithmVersion.get_active_scores_version()
    version_id = int(av.id)
    print(f"Loading cascade outcomes for v{version_id}...", flush=True)
    result = bc.run_cascade_backtest(
        version_id=version_id,
        min_score=70.0,
        from_date=start,
        to_date=end,
        initial=args.initial,
        verbose=True,
    )
    if not result:
        raise RuntimeError("No cascade result returned.")

    print("Evaluating baseline...", flush=True)
    baseline = evaluate_params(
        baseline_params(),
        result["outcomes_by_date"],
        result["all_dates"],
        daily,
        result["regime_dates"],
        result["regime_map"],
        args.initial,
    )

    branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    candidates: list[SmoothParams] = [legacy_put_cap_benchmark()]
    for branch in branches:
        candidates.extend(_candidate_pool(branch, args.budget_per_branch, args.seed))

    records: list[dict[str, Any]] = []
    total = len(candidates)
    for idx, params in enumerate(candidates, start=1):
        t0 = time.time()
        windows = evaluate_params(
            params,
            result["outcomes_by_date"],
            result["all_dates"],
            daily,
            result["regime_dates"],
            result["regime_map"],
            args.initial,
        )
        info = utility(windows, baseline)
        rec = {"params": params, "windows": windows, "info": info, "duration_s": time.time() - t0}
        records.append(rec)
        with eval_log.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "idx": idx,
                        "params": asdict(params),
                        "info": info,
                        "duration_s": rec["duration_s"],
                    },
                    default=str,
                )
                + "\n"
            )
        ranked_partial = sorted(records, key=lambda row: row["info"]["utility"], reverse=True)
        best = ranked_partial[0]
        _write_status(
            status_path,
            {
                "phase": "smooth_controller_sweep",
                "state": "running",
                "started_at": started_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "completed": idx,
                "total": total,
                "current_policy": params.name,
                "current_branch": params.branch,
                "best_policy": best["params"].name,
                "best_branch": best["params"].branch,
                "best_utility": best["info"]["utility"],
                "best_failwindow_pass": best["info"]["failwindow_pass"],
                "outputs": {"jsonl": str(eval_log), "status": str(status_path)},
            },
        )
        print(
            f"[{idx:04d}/{total}] {params.branch}:{params.name} util={info['utility']:+.2f} "
            f"fw={info['failwindow_pass']} crashDD={info['crash_dd_delta_pp']:+.2f}pp "
            f"2024DD={info['y2024_dd_delta_pp']:+.2f}pp 2024log={info['y2024_log_delta']:+.2f} "
            f"dur={rec['duration_s']:.1f}s",
            flush=True,
        )

    ranked_records = sorted(records, key=lambda row: row["info"]["utility"], reverse=True)
    flat_ranked = [_flatten_record(i, rec) for i, rec in enumerate(ranked_records, start=1)]
    ranked_csv = out_dir / "smooth_controller_ranked.csv"
    all_csv = out_dir / "smooth_controller_all.csv"
    top_csv = out_dir / "smooth_controller_top_candidates.csv"
    summary_md = out_dir / "smooth_controller_summary.md"
    best_json = out_dir / "smooth_controller_best.json"
    _write_csv(ranked_csv, flat_ranked)
    _write_csv(all_csv, [_flatten_record(i, rec) for i, rec in enumerate(records, start=1)])

    top_by_branch: list[dict[str, Any]] = []
    branch_order = list(dict.fromkeys([*branches, "benchmark"]))
    for branch in branch_order:
        branch_rows = [row for row in flat_ranked if row["branch"] == branch]
        top_by_branch.extend(branch_rows[:5])
    _write_csv(top_csv, top_by_branch)

    meta = {
        "version_id": version_id,
        "commit": getattr(av, "git_commit", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget_per_branch": args.budget_per_branch,
        "branches": branches,
        "daily_state": str(args.daily),
    }
    _write_summary(summary_md, baseline, flat_ranked, meta)
    if ranked_records:
        best = ranked_records[0]
        best_json.write_text(
            json.dumps(
                {
                    "policy": best["params"].name,
                    "branch": best["params"].branch,
                    "params": asdict(best["params"]),
                    "info": best["info"],
                    "windows": best["windows"],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    done = {
        "phase": "done",
        "state": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "completed": len(records),
        "total": total,
        "best_policy": ranked_records[0]["params"].name if ranked_records else None,
        "best_branch": ranked_records[0]["params"].branch if ranked_records else None,
        "outputs": {
            "jsonl": str(eval_log),
            "ranked": str(ranked_csv),
            "all": str(all_csv),
            "top_candidates": str(top_csv),
            "summary": str(summary_md),
            "best": str(best_json),
        },
    }
    _write_status(status_path, done)
    print(f"Wrote {ranked_csv}", flush=True)
    print(f"Wrote {top_csv}", flush=True)
    print(f"Wrote {summary_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
