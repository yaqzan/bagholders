"""Bayesian-style sweep over combined daily allocation alpha variants.

This is experiment-only Stage 3 research. It merges the alpha families from the
daily opportunity ledger into one transparent exposure controller, evaluates
candidate policies with deterministic cascade replay, and ranks release
candidate seeds for later MC validation.

No score rows are written. No production config is mutated.
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


EXP_DIR = Path(__file__).resolve().parent
DEFAULT_DAILY = EXP_DIR / "daily_state.csv"
DEFAULT_START = date(2020, 1, 1)
DEFAULT_TRAIN_END = date(2024, 12, 31)
DEFAULT_TEST_START = date(2025, 1, 1)
DEFAULT_INITIAL = 50_000.0

WINDOWS = [
    ("train", DEFAULT_START, DEFAULT_TRAIN_END),
    ("test", DEFAULT_TEST_START, None),
    ("full", DEFAULT_START, None),
    ("2020-crash", date(2020, 2, 18), date(2020, 4, 30)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2024", date(2024, 1, 1), date(2024, 12, 31)),
    ("2025", date(2025, 1, 1), date(2025, 12, 31)),
    ("22-now", date(2022, 1, 1), None),
]

WINDOW_WEIGHTS = {
    "train": 0.35,
    "test": 3.00,
    "full": 2.00,
    "2020-crash": 2.00,
    "2022": 2.00,
    "2024": 1.25,
    "2025": 2.00,
    "22-now": 2.50,
}


@dataclass(frozen=True)
class AlphaParams:
    throttle_strength: float = 0.0
    floor_scale: float = 1.0
    put_extra_throttle: float = 0.0
    slot_cut: int = 0
    put_cap: int = 0
    recent_tp_floor: float = 0.5133
    recent_pnl_floor: float = 0.0674
    open_put_trigger: float = 7.0
    sparse_call_trigger: float = 0.20
    sparse_total_trigger: float = 0.78
    breadth_floor: float = 51.27
    wave_threshold: float = 3.72
    put_weight: float = 1.0
    scarce_weight: float = 1.0
    wave_weight: float = 1.0
    reentry_strength: float = 0.0
    reentry_dd: float = 0.0956
    reentry_call_trigger: float = 0.40
    benign_breadth_floor: float = 101.0
    benign_dd_ceiling: float = -1.0


PARAM_SPACE: dict[str, list[Any]] = {
    "throttle_strength": [0.10, 0.18, 0.26, 0.34, 0.42],
    "floor_scale": [0.50, 0.60, 0.70, 0.80],
    "put_extra_throttle": [0.00, 0.08, 0.16, 0.24],
    "slot_cut": [0, 2, 4, 6],
    "put_cap": [0, 3, 5, 7],
    "recent_tp_floor": [0.4489, 0.50, 0.5133, 0.539, 0.56],
    "recent_pnl_floor": [0.00, 0.04, 0.0674, 0.0738, 0.09],
    "open_put_trigger": [5.0, 7.0, 9.0],
    "sparse_call_trigger": [0.10, 0.20, 0.40],
    "sparse_total_trigger": [0.52, 0.78, 1.12],
    "breadth_floor": [45.0, 51.27, 58.0],
    "wave_threshold": [0.0, 3.72, 10.0, 20.0],
    "put_weight": [0.0, 0.50, 1.0, 1.50],
    "scarce_weight": [0.0, 0.50, 1.0, 1.50],
    "wave_weight": [0.0, 0.50, 1.0, 1.50],
    "reentry_strength": [0.0, 0.06, 0.12, 0.18],
    "reentry_dd": [0.08, 0.0956, 0.12, 0.15],
    "reentry_call_trigger": [0.30, 0.40, 0.70],
    "benign_breadth_floor": [0.0, 15.0, 30.0, 45.0, 58.0, 65.0, 101.0],
    "benign_dd_ceiling": [0.04, 0.08, 0.12, 0.50, 1.0, -1.0],
}


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


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _wave_above(value: float, threshold: float, width: float) -> float:
    if width <= 0:
        return 1.0 if value >= threshold else 0.0
    return _clip((value - threshold) / width)


def _wave_below(value: float, threshold: float, width: float) -> float:
    if width <= 0:
        return 1.0 if value <= threshold else 0.0
    return _clip((threshold - value) / width)


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


def _risk_components(row: dict[str, Any], params: AlphaParams, running_dd: float) -> dict[str, float]:
    prev_tp = _float(row.get("prev5_entry_tp_rate"), 0.63)
    prev_pnl = _float(row.get("prev5_entry_avg_pnl_pct"), 0.16)
    recent_weak = max(
        _wave_below(prev_tp, params.recent_tp_floor, 0.16),
        _wave_below(prev_pnl, params.recent_pnl_floor, 0.12),
    )

    open_put = _float(row.get("open_put_n"))
    filled_put = _float(row.get("filled_put_n"))
    open_put_share = _float(row.get("open_put_share"))
    put_overhang = max(
        _wave_above(open_put, params.open_put_trigger, 6.0),
        _wave_above(filled_put, 2.0, 5.0),
        _wave_above(open_put_share, 0.75, 0.25),
    )

    call_demand = _float(row.get("post_call_primary_alloc_demand"))
    total_demand = _float(row.get("post_total_primary_alloc_demand"))
    sparse = max(
        _wave_below(call_demand, params.sparse_call_trigger, 0.35),
        _wave_below(total_demand, params.sparse_total_trigger, 0.80),
    )
    weak_breadth = _wave_below(_float(row.get("breadth_pct_above_ema50"), 60.0), params.breadth_floor, 25.0)

    wave_signed = _float(row.get("breadth_sector_etf_market_wave_signed"), -20.0)
    market_wave = _wave_above(wave_signed, params.wave_threshold, 25.0)

    put_risk = recent_weak * put_overhang
    scarce_risk = recent_weak * max(sparse, weak_breadth)
    wave_risk = recent_weak * market_wave
    gross_risk = _clip(max(params.put_weight * put_risk, params.scarce_weight * scarce_risk, params.wave_weight * wave_risk))

    breadth = _float(row.get("breadth_pct_above_ema50"), 60.0)
    benign_disable = (
        params.benign_breadth_floor <= 100.0
        and params.benign_dd_ceiling >= 0.0
        and breadth >= params.benign_breadth_floor
        and running_dd <= params.benign_dd_ceiling
    )
    if benign_disable:
        put_risk = 0.0
        scarce_risk = 0.0
        wave_risk = 0.0
        gross_risk = 0.0

    dd_wave = _wave_above(running_dd, params.reentry_dd, 0.20)
    call_health = _wave_above(call_demand, params.reentry_call_trigger, 0.60)
    reentry = 0.0 if benign_disable else dd_wave * call_health * (1.0 - 0.70 * recent_weak)

    return {
        "recent_weak": recent_weak,
        "put_overhang": put_overhang,
        "sparse": sparse,
        "weak_breadth": weak_breadth,
        "market_wave": market_wave,
        "put_risk": put_risk,
        "scarce_risk": scarce_risk,
        "wave_risk": wave_risk,
        "gross_risk": gross_risk,
        "reentry": _clip(reentry),
        "benign_disable": 1.0 if benign_disable else 0.0,
    }


def _policy_scale(side: str, row: dict[str, Any], params: AlphaParams, running_dd: float) -> tuple[float, dict[str, float]]:
    risk = _risk_components(row, params, running_dd)
    scale = 1.0 - params.throttle_strength * risk["gross_risk"]
    scale = max(params.floor_scale, scale)
    if side == "put" and params.put_extra_throttle > 0:
        scale *= max(params.floor_scale, 1.0 - params.put_extra_throttle * risk["put_risk"])
    if side == "call" and params.reentry_strength > 0:
        scale *= 1.0 + params.reentry_strength * risk["reentry"]
    return max(0.35, min(1.35, scale)), risk


def _policy_max_positions(params: AlphaParams, risk: dict[str, float]) -> int:
    base = int(bc.MAX_POSITIONS)
    if params.slot_cut <= 0:
        return base
    return max(6, min(base, base - round(params.slot_cut * risk["gross_risk"])))


def run_alpha_policy(
    params: AlphaParams,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    initial: float,
    entry_start: date,
    entry_end: date | None,
) -> dict[str, Any]:
    cash = initial
    open_pos: list[bc.OpenPosition] = []
    trade_log: list[dict[str, Any]] = []
    equity_curve: list[tuple[date, float]] = []
    peak = initial
    max_dd = 0.0
    signal_days = filled_days = full_days = 0
    risk_days = wave_days = reentry_days = 0
    risk_sum = 0.0
    scale_sum = 0.0
    scale_n = 0
    open_counts: list[int] = []

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

        equity = cash + sum(p.premium for p in open_pos)
        running_dd = 1.0 - equity / peak if peak > 0 else 0.0
        row = daily.get(today, {})
        _, day_risk = _policy_scale("call", row, params, running_dd)
        max_pos = _policy_max_positions(params, day_risk)
        put_cap = params.put_cap if params.put_cap > 0 and day_risk["put_risk"] >= 0.50 else 0
        if len(open_pos) >= max_pos:
            full_days += 1
        if day_risk["gross_risk"] >= 0.50:
            risk_days += 1
        if day_risk["wave_risk"] >= 0.50:
            wave_days += 1
        if day_risk["reentry"] >= 0.35:
            reentry_days += 1
        risk_sum += day_risk["gross_risk"]

        open_syms = {p.outcome.symbol for p in open_pos}
        day_outcomes = outcomes_by_date.get(today, [])
        entry_allowed = today >= entry_start and (entry_end is None or today <= entry_end)
        if day_outcomes and entry_allowed:
            signal_days += 1
        n_before = len(open_pos)

        for outcome in day_outcomes if entry_allowed else []:
            if outcome.symbol in open_syms:
                continue
            if len(open_pos) >= max_pos:
                break

            is_put = getattr(outcome, "side", "call") == "put"
            if is_put and put_cap > 0:
                put_open = sum(1 for p in open_pos if p.outcome.side == "put")
                if put_open >= put_cap:
                    continue

            reg_mult = bc.regime_on_or_before(
                regime_dates,
                regime_map,
                today,
                breadth_enabled=bc.BREADTH_ALLOC_ENABLED,
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
            policy_scale, _ = _policy_scale("put" if is_put else "call", row, params, running_dd)
            premium = alloc_frac * reg_scale * dd_scale * saw_scale * policy_scale * equity
            if cash < premium or premium < 10.0:
                continue
            scale_sum += policy_scale
            scale_n += 1
            cash -= premium
            open_pos.append(bc.OpenPosition(outcome=outcome, premium=premium, entry_eq=equity))
            open_syms.add(outcome.symbol)
            equity = cash + sum(p.premium for p in open_pos)

        if len(open_pos) > n_before:
            filled_days += 1
        if equity > peak:
            peak = equity
        dd = 1.0 - equity / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        equity_curve.append((today, equity))
        open_counts.append(len(open_pos))

    final = cash + sum(p.premium for p in open_pos)
    calls = [t for t in trade_log if t["side"] == "call"]
    puts = [t for t in trade_log if t["side"] == "put"]
    tp = sum(1 for t in trade_log if t["outcome"] == "tp")
    call_tp = sum(1 for t in calls if t["outcome"] == "tp")
    put_tp = sum(1 for t in puts if t["outcome"] == "tp")
    log_return = math.log(final / initial) if final > 0 and initial > 0 else -999.0
    day_count = len(equity_curve)
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
        "pool_full_days": full_days,
        "avg_open_slots": sum(open_counts) / len(open_counts) if open_counts else None,
        "risk_day_rate": risk_days / day_count if day_count else 0.0,
        "wave_day_rate": wave_days / day_count if day_count else 0.0,
        "reentry_day_rate": reentry_days / day_count if day_count else 0.0,
        "avg_risk": risk_sum / day_count if day_count else 0.0,
        "avg_policy_scale": scale_sum / scale_n if scale_n else 1.0,
    }


def evaluate_params(
    params: AlphaParams,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    initial: float,
) -> dict[str, dict[str, Any]]:
    return {
        label: run_alpha_policy(
            params,
            outcomes_by_date,
            all_days,
            daily,
            regime_dates,
            regime_map,
            initial,
            start,
            end,
        )
        for label, start, end in WINDOWS
    }


def _window_utility(cand: dict[str, Any], base: dict[str, Any], label: str) -> tuple[float, list[str]]:
    cliffs: list[str] = []
    weight = WINDOW_WEIGHTS.get(label, 1.0)
    dd_delta_pp = (base["max_dd"] - cand["max_dd"]) * 100.0
    log_delta = cand["log_return"] - base["log_return"]
    tp_delta_pp = ((_float(cand.get("tp_rate"), 0.0) - _float(base.get("tp_rate"), 0.0)) * 100.0)
    trade_ratio = cand["trades"] / base["trades"] if base["trades"] else 1.0

    util = weight * (2.0 * dd_delta_pp + 0.35 * max(-4.0, min(4.0, log_delta)) + 0.20 * tp_delta_pp)
    if dd_delta_pp < -1.0:
        util -= weight * 7.5 * abs(dd_delta_pp)
        cliffs.append(f"{label}:DD worse {abs(dd_delta_pp):.2f}pp")
    min_trade_ratio = 0.78 if label in {"test", "full", "22-now"} else 0.65
    if trade_ratio < min_trade_ratio:
        util -= weight * 15.0 * (min_trade_ratio - trade_ratio)
        cliffs.append(f"{label}:trades {trade_ratio:.1%}")
    if label in {"test", "full", "22-now"} and cand["log_return"] < base["log_return"] - 2.5:
        util -= weight * 4.0
        cliffs.append(f"{label}:log regression")
    return util, cliffs


def utility(candidate: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = 0.0
    cliffs: list[str] = []
    per_window: dict[str, float] = {}
    for label in candidate:
        u, c = _window_utility(candidate[label], baseline[label], label)
        per_window[label] = u
        total += u
        cliffs.extend(c)

    rc_pass = not cliffs
    for label in ["test", "full", "22-now"]:
        if candidate[label]["max_dd"] > baseline[label]["max_dd"] - 0.0025:
            rc_pass = False
    for label in ["2020-crash", "2022", "2024", "2025"]:
        if candidate[label]["max_dd"] > baseline[label]["max_dd"] + 0.0025:
            rc_pass = False
    return {
        "utility": total,
        "hard_cliffs": cliffs,
        "rc_pass": rc_pass,
        "per_window": per_window,
        "test_dd_delta_pp": (baseline["test"]["max_dd"] - candidate["test"]["max_dd"]) * 100.0,
        "full_dd_delta_pp": (baseline["full"]["max_dd"] - candidate["full"]["max_dd"]) * 100.0,
        "twonow_dd_delta_pp": (baseline["22-now"]["max_dd"] - candidate["22-now"]["max_dd"]) * 100.0,
        "test_log_delta": candidate["test"]["log_return"] - baseline["test"]["log_return"],
        "full_log_delta": candidate["full"]["log_return"] - baseline["full"]["log_return"],
    }


def baseline_params() -> AlphaParams:
    return AlphaParams(
        throttle_strength=0.0,
        floor_scale=1.0,
        put_extra_throttle=0.0,
        slot_cut=0,
        put_cap=0,
        put_weight=0.0,
        scarce_weight=0.0,
        wave_weight=0.0,
        reentry_strength=0.0,
    )


def seed_params() -> list[AlphaParams]:
    return [
        baseline_params(),
        AlphaParams(throttle_strength=0.22, floor_scale=0.70, slot_cut=2, put_cap=5, put_weight=1.0, scarce_weight=1.0, wave_weight=0.0),
        AlphaParams(throttle_strength=0.26, floor_scale=0.65, slot_cut=4, put_cap=5, put_weight=1.5, scarce_weight=1.0, wave_weight=0.0),
        AlphaParams(throttle_strength=0.24, floor_scale=0.70, slot_cut=2, put_cap=0, put_weight=0.0, scarce_weight=1.0, wave_weight=1.0),
        AlphaParams(throttle_strength=0.26, floor_scale=0.65, slot_cut=4, put_cap=5, put_weight=1.0, scarce_weight=1.0, wave_weight=1.0),
        AlphaParams(throttle_strength=0.18, floor_scale=0.75, slot_cut=2, put_cap=5, put_weight=1.0, scarce_weight=0.5, wave_weight=1.5),
        AlphaParams(throttle_strength=0.18, floor_scale=0.75, slot_cut=2, put_cap=0, put_weight=0.5, scarce_weight=1.0, wave_weight=1.0, reentry_strength=0.06),
        AlphaParams(throttle_strength=0.26, floor_scale=0.70, slot_cut=2, put_cap=5, put_weight=1.0, scarce_weight=1.0, wave_weight=1.0, reentry_strength=0.12),
        AlphaParams(throttle_strength=0.34, floor_scale=0.60, put_extra_throttle=0.16, slot_cut=4, put_cap=3, put_weight=1.5, scarce_weight=1.0, wave_weight=1.0, reentry_strength=0.0),
        AlphaParams(throttle_strength=0.18, floor_scale=0.80, put_extra_throttle=0.08, slot_cut=0, put_cap=7, put_weight=1.0, scarce_weight=0.5, wave_weight=1.5, reentry_strength=0.12),
    ]


def _params_key(params: AlphaParams) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, repr(v)) for k, v in asdict(params).items()))


def _random_params(rng: random.Random) -> AlphaParams:
    return AlphaParams(**{key: rng.choice(vals) for key, vals in PARAM_SPACE.items()})


def _candidate_pool(size: int, seed: int) -> list[AlphaParams]:
    rng = random.Random(seed)
    out: list[AlphaParams] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for params in seed_params():
        key = _params_key(params)
        if key not in seen:
            out.append(params)
            seen.add(key)
    tries = 0
    while len(out) < size and tries < size * 20:
        tries += 1
        params = _random_params(rng)
        key = _params_key(params)
        if key not in seen:
            out.append(params)
            seen.add(key)
    return out


def _normalize(params: AlphaParams) -> dict[str, float]:
    data = asdict(params)
    out: dict[str, float] = {}
    for key, vals in PARAM_SPACE.items():
        value = data[key]
        if value not in vals:
            numeric = [float(v) for v in vals]
            lo, hi = min(numeric), max(numeric)
            out[key] = (float(value) - lo) / (hi - lo) if hi > lo else 0.5
        elif all(isinstance(v, (int, float)) for v in vals):
            lo, hi = min(vals), max(vals)
            out[key] = (float(value) - float(lo)) / (float(hi) - float(lo)) if hi > lo else 0.5
        else:
            out[key] = vals.index(value) / (len(vals) - 1) if len(vals) > 1 else 0.5
    return out


def _l1(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(abs(a[k] - b[k]) for k in a)


def _surrogate(candidate_norm: dict[str, float], evaluated: list[dict[str, Any]], bandwidth: float) -> tuple[float, float]:
    if not evaluated:
        return 0.0, 1.0
    weights = []
    utils = []
    for rec in evaluated:
        d = _l1(candidate_norm, rec["norm"])
        w = math.exp(-d / bandwidth)
        weights.append(w)
        utils.append(rec["utility"])
    wsum = sum(weights)
    if wsum <= 0:
        return 0.0, 1.0
    mu = sum(w * u for w, u in zip(weights, utils)) / wsum
    var = sum(w * (u - mu) ** 2 for w, u in zip(weights, utils)) / wsum
    std = math.sqrt(var + 1.0 / (1.0 + wsum))
    return mu, std


def _pick_next(pool: list[AlphaParams], evaluated: list[dict[str, Any]], bandwidth: float, kappa: float) -> AlphaParams | None:
    done = {_params_key(rec["params_obj"]) for rec in evaluated}
    scored = []
    for params in pool:
        if _params_key(params) in done:
            continue
        norm = _normalize(params)
        mu, std = _surrogate(norm, evaluated, bandwidth)
        scored.append((mu + kappa * std, mu, std, params))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][3]


def _flatten_record(rank: int, rec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rank": rank,
        "utility": rec["utility"],
        "rc_pass": rec["util_info"]["rc_pass"],
        "hard_cliffs": "; ".join(rec["util_info"]["hard_cliffs"][:8]),
        **{f"param_{k}": v for k, v in asdict(rec["params_obj"]).items()},
        "test_dd_delta_pp": rec["util_info"]["test_dd_delta_pp"],
        "full_dd_delta_pp": rec["util_info"]["full_dd_delta_pp"],
        "twonow_dd_delta_pp": rec["util_info"]["twonow_dd_delta_pp"],
        "test_log_delta": rec["util_info"]["test_log_delta"],
        "full_log_delta": rec["util_info"]["full_log_delta"],
    }
    for label, row in rec["windows"].items():
        out[f"{label}_log"] = row["log_return"]
        out[f"{label}_max_dd"] = row["max_dd"]
        out[f"{label}_trades"] = row["trades"]
        out[f"{label}_tp_rate"] = row["tp_rate"]
        out[f"{label}_avg_policy_scale"] = row["avg_policy_scale"]
        out[f"{label}_risk_day_rate"] = row["risk_day_rate"]
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    seen = set()
    for row in rows:
        for col in row:
            if col not in seen:
                cols.append(col)
                seen.add(col)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(path: Path, baseline: dict[str, Any], ranked: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    lines = [
        "# Bayesian Daily Alpha Sweep",
        "",
        "Experiment-local Stage 3 deterministic screen. This is not a ship result.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- budget: {meta['budget']}",
        f"- candidate_pool: {meta['candidate_pool']}",
        f"- daily_state: `{meta['daily_state']}`",
        "",
        "## Baseline",
        "",
        "| window | log_return | max DD | trades | TP |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in [w[0] for w in WINDOWS]:
        row = baseline[label]
        tp = "" if row["tp_rate"] is None else f"{row['tp_rate']:.1%}"
        lines.append(f"| {label} | {row['log_return']:+.3f} | {row['max_dd']:.1%} | {row['trades']} | {tp} |")

    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
            "| rank | utility | rc | test DD d | full DD d | 22-now DD d | test log d | cliffs | key params |",
            "|---:|---:|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for idx, rec in enumerate(ranked[:20], start=1):
        info = rec["util_info"]
        p = asdict(rec["params_obj"])
        key = (
            f"thr={p['throttle_strength']}, floor={p['floor_scale']}, slot={p['slot_cut']}, "
            f"putcap={p['put_cap']}, weights=({p['put_weight']},{p['scarce_weight']},{p['wave_weight']}), "
            f"re={p['reentry_strength']}"
        )
        cliffs = "; ".join(info["hard_cliffs"][:3])
        lines.append(
            f"| {idx} | {rec['utility']:+.2f} | {str(info['rc_pass'])} | "
            f"{info['test_dd_delta_pp']:+.2f}pp | {info['full_dd_delta_pp']:+.2f}pp | "
            f"{info['twonow_dd_delta_pp']:+.2f}pp | {info['test_log_delta']:+.2f} | "
            f"{cliffs} | `{key}` |"
        )

    best_rc = next((rec for rec in ranked if rec["util_info"]["rc_pass"]), None)
    lines.extend(["", "## Read", ""])
    if best_rc:
        lines.append(
            f"- Deterministic release-candidate seed found: utility {best_rc['utility']:+.2f}, "
            f"params `{asdict(best_rc['params_obj'])}`."
        )
        lines.append("- Next step is targeted MC validation, not production promotion.")
    else:
        lines.append("- No deterministic release-candidate seed passed all guardrails.")
        lines.append("- Keep top utility rows as alpha diagnostics, but do not promote.")
    lines.append("- Guardrails are DD-primary and preserve trade throughput; broad N expansion remains demoted.")
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
    parser.add_argument("--budget", type=int, default=80)
    parser.add_argument("--candidate-pool", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260519)
    parser.add_argument("--bandwidth", type=float, default=0.32)
    parser.add_argument("--kappa", type=float, default=2.0)
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "bayes_alpha_status.json"
    log_path = out_dir / "bayes_alpha_evals.jsonl"
    if log_path.exists():
        log_path.unlink()
    _write_status(
        status_path,
        {
            "phase": "bayesian_alpha_sweep",
            "state": "loading",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "budget": args.budget,
            "candidate_pool": args.candidate_pool,
        },
    )

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

    outcomes_by_date = result["outcomes_by_date"]
    all_days = result["all_dates"]
    regime_dates = result["regime_dates"]
    regime_map = result["regime_map"]

    print("Evaluating baseline...", flush=True)
    baseline = evaluate_params(
        baseline_params(),
        outcomes_by_date,
        all_days,
        daily,
        regime_dates,
        regime_map,
        args.initial,
    )
    pool = _candidate_pool(args.candidate_pool, args.seed)
    evaluated: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()

    def evaluate_one(params: AlphaParams, phase: str, idx: int) -> None:
        t0 = time.time()
        windows = evaluate_params(params, outcomes_by_date, all_days, daily, regime_dates, regime_map, args.initial)
        info = utility(windows, baseline)
        rec = {
            "idx": idx,
            "phase": phase,
            "params_obj": params,
            "params": asdict(params),
            "norm": _normalize(params),
            "windows": windows,
            "util_info": info,
            "utility": info["utility"],
            "duration_s": time.time() - t0,
        }
        evaluated.append(rec)
        with log_path.open("a", encoding="utf-8") as fh:
            payload = dict(rec)
            payload.pop("params_obj", None)
            fh.write(json.dumps(payload, default=str) + "\n")
        best = max(evaluated, key=lambda row: row["utility"])
        _write_status(
            status_path,
            {
                "phase": "bayesian_alpha_sweep",
                "state": "running",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "completed": len(evaluated),
                "total": args.budget,
                "current_phase": phase,
                "best_utility": best["utility"],
                "best_rc_pass": best["util_info"]["rc_pass"],
                "best_params": asdict(best["params_obj"]),
                "best_test_dd_delta_pp": best["util_info"]["test_dd_delta_pp"],
                "best_full_dd_delta_pp": best["util_info"]["full_dd_delta_pp"],
                "outputs": {
                    "jsonl": str(log_path),
                    "status": str(status_path),
                },
            },
        )
        print(
            f"[{idx:03d}/{args.budget} {phase}] util={info['utility']:+.2f} "
            f"rc={info['rc_pass']} testDDd={info['test_dd_delta_pp']:+.2f}pp "
            f"fullDDd={info['full_dd_delta_pp']:+.2f}pp dur={rec['duration_s']:.1f}s "
            f"params={asdict(params)}",
            flush=True,
        )

    idx = 0
    for params in seed_params():
        if idx >= args.budget:
            break
        key = _params_key(params)
        if key in seen:
            continue
        seen.add(key)
        idx += 1
        evaluate_one(params, "seed", idx)

    while idx < args.budget:
        progress = idx / max(1, args.budget)
        kappa = args.kappa * (1.0 - 0.45 * progress)
        params = _pick_next(pool, evaluated, args.bandwidth, kappa)
        if params is None:
            break
        key = _params_key(params)
        if key in seen:
            continue
        seen.add(key)
        idx += 1
        evaluate_one(params, "bayes", idx)

    ranked = sorted(evaluated, key=lambda row: row["utility"], reverse=True)
    ranked_csv = out_dir / "bayes_alpha_ranked.csv"
    all_csv = out_dir / "bayes_alpha_all.csv"
    summary_md = out_dir / "bayes_alpha_summary.md"
    best_json = out_dir / "bayes_alpha_best.json"
    flat_ranked = [_flatten_record(i, rec) for i, rec in enumerate(ranked, start=1)]
    _write_csv(ranked_csv, flat_ranked)
    _write_csv(all_csv, [_flatten_record(i, rec) for i, rec in enumerate(evaluated, start=1)])
    meta = {
        "version_id": version_id,
        "commit": getattr(av, "git_commit", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget": args.budget,
        "candidate_pool": args.candidate_pool,
        "daily_state": str(args.daily),
    }
    _write_summary(summary_md, baseline, ranked, meta)
    best_payload = dict(ranked[0])
    best_payload["params_obj"] = asdict(best_payload["params_obj"])
    best_json.write_text(json.dumps(best_payload, indent=2, default=str), encoding="utf-8")
    _write_status(
        status_path,
        {
            "phase": "done",
            "state": "done",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "completed": len(evaluated),
            "total": args.budget,
            "best_utility": ranked[0]["utility"] if ranked else None,
            "best_rc_pass": ranked[0]["util_info"]["rc_pass"] if ranked else False,
            "best_params": asdict(ranked[0]["params_obj"]) if ranked else None,
            "outputs": {
                "jsonl": str(log_path),
                "ranked": str(ranked_csv),
                "all": str(all_csv),
                "summary": str(summary_md),
                "best": str(best_json),
            },
        },
    )
    print(f"Wrote {summary_md}", flush=True)
    print(f"Wrote {ranked_csv}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
