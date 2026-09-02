"""Hard crash-controller research for v60 portfolio drawdown.

Experiment-only Stage 3 research. This script does not write score rows and does
not mutate production strategy config. It mines live-safe daily risk triggers,
adds cross-version v54/v59/v60 overlap features, then evaluates hard portfolio
actions that the smooth controller could not express:

- call-entry halts and call caps
- put-first admission and extra put scale
- max-position contraction
- forced call liquidation under risk state

The output is a ranked deterministic replay surface intended to identify
candidate controller shapes before MC validation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from database.trader_database import DB
from experiments.daily_opportunity_allocation.mine_conditional_alpha import _add_derived
from strategy_config import STRATEGY_30DTE


DEFAULT_DAILY = ROOT / ".codex" / "runs" / "v60_daily_opportunity_smooth_20260519_082415" / "daily_state.csv"
DEFAULT_START = date(2020, 1, 1)
DEFAULT_TEST_START = date(2025, 1, 1)
DEFAULT_TRAIN_END = date(2024, 12, 31)
DEFAULT_INITIAL = 50_000.0
BASE_CALL_CAP = int(STRATEGY_30DTE.MAX_POSITIONS_CALL or STRATEGY_30DTE.MAX_POSITIONS)

WINDOWS: list[tuple[str, date, date | None]] = [
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
    "train": 0.30,
    "test": 2.50,
    "full": 1.50,
    "2020-crash": 4.00,
    "2022": 1.75,
    "2024": 2.50,
    "2025": 1.75,
    "22-now": 2.25,
}

FEATURES = [
    "breadth_breadth_score",
    "breadth_pct_above_ema50",
    "breadth_pct_above_ema200",
    "breadth_sector_etf_pct_above_ema50",
    "breadth_sector_etf_pct_above_ema200",
    "breadth_sector_etf_breadth_1d_change",
    "breadth_sector_etf_breadth_5d_change",
    "breadth_sector_etf_breadth_10d_change",
    "breadth_sector_etf_breadth_avg5",
    "breadth_sector_etf_breadth_10d_position",
    "breadth_sector_etf_breadth_30d_position",
    "breadth_sector_etf_market_wave_score",
    "breadth_sector_etf_market_wave_signed",
    "regime_vix_close",
    "regime_vix_10d_change",
    "regime_spy_pct_from_ema50",
    "regime_spy_pct_from_ema200",
    "regime_internal_breadth_score",
    "post_call_primary_alloc_demand",
    "post_put_primary_alloc_demand",
    "post_total_primary_alloc_demand",
    "post_net_call_minus_put_demand",
    "post_call_sector_hhi",
    "post_call_max_sector_share",
    "open_call_n",
    "open_put_n",
    "open_put_share",
    "open_premium_pct",
    "free_slots",
    "dd",
    "prev5_entry_tp_rate",
    "prev5_entry_call_tp_rate",
    "prev5_entry_avg_pnl_pct",
    "prev5_entry_trade_n",
    "prev5_dd",
    "dd_5d_delta",
    "v54_call_eligible_n",
    "v54_put_n",
    "v59_call_eligible_n",
    "v59_put_n",
    "v60_call_eligible_n",
    "v60_put_n",
    "v60_extra_calls_vs_v59",
    "v59_only_puts_vs_v60",
    "call_jaccard_59_60",
    "put_jaccard_59_60",
]


@dataclass(frozen=True)
class Trigger:
    name: str
    kind: str
    feature: str = ""
    op: str = "<="
    threshold: float = 0.0
    feature2: str = ""
    op2: str = "<="
    threshold2: float = 0.0
    threshold3: float = 0.0


@dataclass(frozen=True)
class HardPolicy:
    name: str
    trigger: Trigger
    call_scale: float
    put_scale: float
    risk_max_pos: int
    risk_call_cap: int
    put_reserve: int
    put_first: bool
    liquidate_mode: str
    liquidate_loss_floor: float
    liquidate_min_age: int
    cooloff_days: int
    dd_trigger: float
    trim_calls_to_cap: bool


def _as_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _read_daily(path: Path) -> tuple[list[dict[str, Any]], dict[date, dict[str, Any]]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda row: row["date"])
    _add_derived(rows)
    by_date = {_as_date(row["date"]): row for row in rows}
    return rows, by_date


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _status(path: Path, payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(path, payload)


def _cond(value: float | None, op: str, threshold: float) -> bool:
    if value is None:
        return False
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == ">":
        return value > threshold
    raise ValueError(f"Unsupported op: {op}")


def build_overlap_features(
    rows: list[dict[str, Any]],
    out_csv: Path,
    versions: tuple[int, int, int] = (54, 59, 60),
) -> None:
    dates = [_as_date(row["date"]) for row in rows]
    start = min(dates)
    end = max(dates)
    overlap_start = start
    overlap_end = min(end, date(2020, 12, 31))
    by_vd: dict[tuple[int, str], dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "puts": 0, "hi75": 0, "lo15": 0}
    )
    call_intersections: dict[str, int] = defaultdict(int)
    put_intersections: dict[str, int] = defaultdict(int)
    chunk_start = overlap_start
    while chunk_start <= overlap_end:
        chunk_end = min(end, chunk_start + timedelta(days=13))
        chunk_sets: dict[tuple[int, str], dict[str, set[str]]] = defaultdict(
            lambda: {"calls": set(), "puts": set(), "hi75": set(), "lo15": set()}
        )
        cur = DB.execute_sql(
            """
            SELECT version_id, `date`, symbol, overall
            FROM scores FORCE INDEX(scores_version_date_IDX)
            WHERE version_id IN (%s, %s, %s)
              AND `date` BETWEEN %s AND %s
              AND overall IS NOT NULL
            """,
            (versions[0], versions[1], versions[2], chunk_start.isoformat(), chunk_end.isoformat()),
        )
        for version_id, d, sym, overall in cur.fetchall():
            day = str(d)
            ov = int(overall)
            rec = chunk_sets[(int(version_id), day)]
            symbol = str(sym)
            if ov >= 70:
                rec["calls"].add(symbol)
            if ov >= 75:
                rec["hi75"].add(symbol)
            if ov <= 25:
                rec["puts"].add(symbol)
            if ov <= 15:
                rec["lo15"].add(symbol)

        chunk_days = sorted({day for _, day in chunk_sets})
        for day in chunk_days:
            for version_id in versions:
                src = chunk_sets[(version_id, day)]
                dst = by_vd[(version_id, day)]
                dst["calls"] = len(src["calls"])
                dst["puts"] = len(src["puts"])
                dst["hi75"] = len(src["hi75"])
                dst["lo15"] = len(src["lo15"])
            call_intersections[day] = len(chunk_sets[(59, day)]["calls"] & chunk_sets[(60, day)]["calls"])
            put_intersections[day] = len(chunk_sets[(59, day)]["puts"] & chunk_sets[(60, day)]["puts"])
        chunk_start = chunk_end + timedelta(days=1)

    overlap_rows: list[dict[str, Any]] = []
    for row in rows:
        d = row["date"]
        r54 = by_vd[(54, d)]
        r59 = by_vd[(59, d)]
        r60 = by_vd[(60, d)]
        c59, c60 = r59["calls"], r60["calls"]
        p59, p60 = r59["puts"], r60["puts"]
        c_inter = call_intersections.get(d, 0)
        p_inter = put_intersections.get(d, 0)
        c_union = c59 + c60 - c_inter
        p_union = p59 + p60 - p_inter
        call_j = c_inter / c_union if c_union else 1.0
        put_j = p_inter / p_union if p_union else 1.0
        features = {
            "v54_call_eligible_n": r54["calls"],
            "v54_put_n": r54["puts"],
            "v59_call_eligible_n": c59,
            "v59_put_n": p59,
            "v60_call_eligible_n": c60,
            "v60_put_n": p60,
            "v60_extra_calls_vs_v59": max(0, c60 - c_inter),
            "v59_only_puts_vs_v60": max(0, p59 - p_inter),
            "call_jaccard_59_60": call_j,
            "put_jaccard_59_60": put_j,
            "v60_hi75_n": r60["hi75"],
            "v60_lo15_n": r60["lo15"],
        }
        row.update(features)
        overlap_rows.append({"date": d, **features})
    _write_csv(out_csv, overlap_rows)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int(round((len(vals) - 1) * q))
    return vals[max(0, min(len(vals) - 1, idx))]


def mine_triggers(rows: list[dict[str, Any]], out_csv: Path, limit: int = 40) -> list[Trigger]:
    train = [row for row in rows if row["date"] <= DEFAULT_TRAIN_END.isoformat()]
    test = [row for row in rows if row["date"] >= DEFAULT_TEST_START.isoformat()]

    target_values = [
        _float(row.get("next_15d_dd_increase"), 0.0) or 0.0
        for row in train
        if _float(row.get("next_15d_dd_increase")) is not None
    ]
    worst_values = [
        _float(row.get("next_15d_worst_equity_return"), 0.0) or 0.0
        for row in train
        if _float(row.get("next_15d_worst_equity_return")) is not None
    ]
    dd_cut = _quantile(target_values, 0.80)
    worst_cut = _quantile(worst_values, 0.20)

    def is_bad(row: dict[str, Any]) -> bool:
        dd = _float(row.get("next_15d_dd_increase"), 0.0) or 0.0
        worst = _float(row.get("next_15d_worst_equity_return"), 0.0) or 0.0
        return dd >= dd_cut or worst <= worst_cut

    base_train = sum(1 for row in train if is_bad(row)) / max(1, len(train))
    base_test = sum(1 for row in test if is_bad(row)) / max(1, len(test))
    mined: list[dict[str, Any]] = []

    for feature in FEATURES:
        values = [_float(row.get(feature)) for row in train]
        vals = [v for v in values if v is not None]
        if len(vals) < 100:
            continue
        for op, qs in (("<=", [0.10, 0.15, 0.20, 0.25, 0.33, 0.40]), (">=", [0.60, 0.67, 0.75, 0.80, 0.85, 0.90])):
            for q in qs:
                thr = _quantile(vals, q)
                train_hits = [row for row in train if _cond(_float(row.get(feature)), op, thr)]
                test_hits = [row for row in test if _cond(_float(row.get(feature)), op, thr)]
                if len(train_hits) < 20:
                    continue
                train_bad = sum(1 for row in train_hits if is_bad(row)) / len(train_hits)
                test_bad = sum(1 for row in test_hits if is_bad(row)) / len(test_hits) if test_hits else 0.0
                train_lift = train_bad - base_train
                test_lift = test_bad - base_test
                coverage = len(train_hits) / max(1, len(train))
                score = train_lift * 2.0 + test_lift + min(coverage, 0.25) * 0.25
                if train_lift <= 0:
                    continue
                mined.append(
                    {
                        "trigger_name": f"stump_{feature}_{op}_{thr:.4g}",
                        "feature": feature,
                        "op": op,
                        "threshold": thr,
                        "train_n": len(train_hits),
                        "test_n": len(test_hits),
                        "train_bad_rate": train_bad,
                        "test_bad_rate": test_bad,
                        "base_train_bad_rate": base_train,
                        "base_test_bad_rate": base_test,
                        "train_lift": train_lift,
                        "test_lift": test_lift,
                        "coverage": coverage,
                        "score": score,
                    }
                )

    mined.sort(key=lambda row: (-float(row["score"]), -float(row["train_lift"]), row["trigger_name"]))
    _write_csv(out_csv, mined)
    triggers = [
        Trigger(
            name=str(row["trigger_name"]),
            kind="feature",
            feature=str(row["feature"]),
            op=str(row["op"]),
            threshold=float(row["threshold"]),
        )
        for row in mined[:limit]
    ]
    return triggers


def curated_triggers() -> list[Trigger]:
    return [
        Trigger("sector_panic_ema50_le35", "feature", "breadth_sector_etf_pct_above_ema50", "<=", 35.0),
        Trigger("sector_panic_ema50_le45", "feature", "breadth_sector_etf_pct_above_ema50", "<=", 45.0),
        Trigger("sector_shock_5d_le_neg20", "feature", "breadth_sector_etf_breadth_5d_change", "<=", -20.0),
        Trigger("market_breadth_le45", "feature", "breadth_pct_above_ema50", "<=", 45.0),
        Trigger("vix_ge30", "feature", "regime_vix_close", ">=", 30.0),
        Trigger("v59_mimic_extra_calls_ge2", "feature", "v60_extra_calls_vs_v59", ">=", 2.0),
        Trigger("v59_mimic_call_jaccard_le70", "feature", "call_jaccard_59_60", "<=", 0.70),
        Trigger(
            "sector_and_vix",
            "pair",
            "breadth_sector_etf_pct_above_ema50",
            "<=",
            50.0,
            "regime_vix_close",
            ">=",
            24.0,
        ),
        Trigger(
            "weak_exec_positive_wave",
            "pair",
            "prev5_entry_avg_pnl_pct",
            "<=",
            0.0674,
            "breadth_sector_etf_market_wave_signed",
            ">=",
            3.72,
        ),
        Trigger(
            "sector_shock_v59_disagree",
            "pair",
            "breadth_sector_etf_breadth_5d_change",
            "<=",
            -10.0,
            "v60_extra_calls_vs_v59",
            ">=",
            1.0,
        ),
    ]


def trigger_active(trigger: Trigger, row: dict[str, Any]) -> bool:
    if trigger.kind == "feature":
        return _cond(_float(row.get(trigger.feature)), trigger.op, trigger.threshold)
    if trigger.kind == "pair":
        return _cond(_float(row.get(trigger.feature)), trigger.op, trigger.threshold) and _cond(
            _float(row.get(trigger.feature2)), trigger.op2, trigger.threshold2
        )
    raise ValueError(f"Unknown trigger kind: {trigger.kind}")


def _window_days(all_days: list[date], start: date, end: date | None) -> list[date]:
    return [d for d in all_days if d >= start and (end is None or d <= end)]


def _load_close_maps(outcomes_by_date: dict[date, list[Any]], all_days: list[date]) -> tuple[dict[str, dict[date, float]], dict[date, int]]:
    symbols = {outcome.symbol for outcomes in outcomes_by_date.values() for outcome in outcomes}
    earliest = min(all_days)
    ph = bc.load_price_history(symbols, earliest)
    close_by_sym_date: dict[str, dict[date, float]] = {}
    for sym, rows in ph.items():
        close_by_sym_date[sym] = {r.date: float(r.close) for r in rows}
    return close_by_sym_date, {d: i for i, d in enumerate(all_days)}


def _mtm_pnl(pos: Any, today: date, close_by_sym_date: dict[str, dict[date, float]], td_idx: dict[date, int]) -> float:
    sym = pos.outcome.symbol
    today_close = close_by_sym_date.get(sym, {}).get(today)
    if today_close is None or pos.outcome.entry_price <= 0:
        return 0.0
    bars_held = td_idx.get(today, 0) - td_idx.get(pos.outcome.signal_date, 0)
    if bars_held <= 0:
        return 0.0
    sigma = pos.outcome.sigma_daily
    if sigma <= 0:
        return 0.0
    premium_pct = bc.PREMIUM_MULT * sigma / 100.0
    try:
        from option_pricing import option_pnl_pct

        pnl = option_pnl_pct(
            pos.outcome.side,
            today_close,
            pos.outcome.entry_price,
            bars_held,
            premium_pct=premium_pct,
            total_dte=bc.DEFAULT_TOTAL_DTE,
            vega_ratio=1.0,
            delta=bc.DELTA,
        )
        return max(-1.0, min(5.0, float(pnl)))
    except Exception:
        return 0.0


def _close_position(pos: Any, today: date, cash: float, pnl_pct: float, outcome_name: str) -> tuple[float, dict[str, Any]]:
    proceeds = pos.premium * (1.0 + pnl_pct)
    cash += proceeds
    trade = {
        "entry_date": pos.outcome.signal_date,
        "exit_date": today,
        "symbol": pos.outcome.symbol,
        "score": pos.outcome.score,
        "tier": pos.outcome.tier,
        "sigma": pos.outcome.sigma_daily,
        "premium": pos.premium,
        "outcome": outcome_name,
        "hold_bars": None,
        "pnl": proceeds - pos.premium,
        "pnl_pct": pnl_pct,
        "stressed": getattr(pos.outcome, "stressed", False),
        "side": pos.outcome.side,
        "entry_price": pos.outcome.entry_price,
        "exit_price": pos.outcome.exit_price,
    }
    return cash, trade


def run_policy(
    policy: HardPolicy,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    close_by_sym_date: dict[str, dict[date, float]],
    td_idx: dict[date, int],
    initial: float,
    entry_start: date,
    entry_end: date | None,
    keep_logs: bool = False,
) -> dict[str, Any]:
    cash = initial
    open_pos: list[bc.OpenPosition] = []
    trade_log: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    peak = initial
    max_dd = 0.0
    max_dd_peak_date: date | None = None
    max_dd_trough_date: date | None = None
    peak_date: date | None = None
    cooldown_until = -1
    risk_days = 0
    liq_count = 0
    risk_entry_days = 0

    for today in _window_days(all_days, entry_start, entry_end):
        today_idx = td_idx.get(today, 0)

        remaining: list[bc.OpenPosition] = []
        for pos in open_pos:
            if pos.outcome.outcome != "open" and pos.outcome.exit_date <= today:
                cash, trade = _close_position(pos, today, cash, pos.outcome.net_return, pos.outcome.outcome)
                trade["hold_bars"] = pos.outcome.hold_bars
                trade_log.append(trade)
            else:
                remaining.append(pos)
        open_pos = remaining

        equity = cash + sum(p.premium for p in open_pos)
        if equity > peak:
            peak = equity
            peak_date = today
        running_dd = 1.0 - equity / peak if peak > 0 else 0.0
        row = daily.get(today, {})
        raw_risk = trigger_active(policy.trigger, row)
        if raw_risk:
            cooldown_until = max(cooldown_until, today_idx + policy.cooloff_days)
        risk = raw_risk or today_idx <= cooldown_until or running_dd >= policy.dd_trigger
        if risk:
            risk_days += 1

        if risk and policy.liquidate_mode != "none":
            survivors: list[bc.OpenPosition] = []
            call_positions: list[tuple[float, bc.OpenPosition]] = []
            for pos in open_pos:
                if pos.outcome.side != "call":
                    survivors.append(pos)
                    continue
                age = today_idx - td_idx.get(pos.outcome.signal_date, today_idx)
                mtm = _mtm_pnl(pos, today, close_by_sym_date, td_idx)
                close_it = False
                if age >= policy.liquidate_min_age:
                    if policy.liquidate_mode == "all_calls":
                        close_it = True
                    elif policy.liquidate_mode == "losing_calls" and mtm <= policy.liquidate_loss_floor:
                        close_it = True
                if close_it:
                    cash, trade = _close_position(pos, today, cash, mtm, "policy_liq")
                    trade["hold_bars"] = age
                    trade_log.append(trade)
                    liq_count += 1
                else:
                    call_positions.append((mtm, pos))
            open_pos = survivors + [pos for _, pos in call_positions]

            if policy.trim_calls_to_cap:
                call_open = [pos for pos in open_pos if pos.outcome.side == "call"]
                if len(call_open) > policy.risk_call_cap:
                    scored = sorted(
                        [
                            (_mtm_pnl(pos, today, close_by_sym_date, td_idx), pos)
                            for pos in call_open
                        ],
                        key=lambda item: item[0],
                    )
                    trim_set = {id(pos) for _, pos in scored[: len(call_open) - policy.risk_call_cap]}
                    kept: list[bc.OpenPosition] = []
                    for pos in open_pos:
                        if id(pos) not in trim_set:
                            kept.append(pos)
                            continue
                        mtm = _mtm_pnl(pos, today, close_by_sym_date, td_idx)
                        cash, trade = _close_position(pos, today, cash, mtm, "policy_trim")
                        trade["hold_bars"] = today_idx - td_idx.get(pos.outcome.signal_date, today_idx)
                        trade_log.append(trade)
                        liq_count += 1
                    open_pos = kept

        equity = cash + sum(p.premium for p in open_pos)
        running_dd = 1.0 - equity / peak if peak > 0 else 0.0
        max_pos = policy.risk_max_pos if risk else bc.MAX_POSITIONS
        call_cap = policy.risk_call_cap if risk else BASE_CALL_CAP
        day_outcomes = list(outcomes_by_date.get(today, []))
        if risk and policy.put_first:
            day_outcomes.sort(key=lambda o: (0 if o.side == "put" else 1, o.score if o.side == "put" else -o.score, o.symbol))
        if risk and day_outcomes:
            risk_entry_days += 1

        open_syms = {p.outcome.symbol for p in open_pos}
        for outcome in day_outcomes:
            if today < entry_start or (entry_end is not None and today > entry_end):
                continue
            if outcome.symbol in open_syms:
                continue
            is_put = getattr(outcome, "side", "call") == "put"
            call_open = sum(1 for p in open_pos if p.outcome.side == "call")
            put_open = sum(1 for p in open_pos if p.outcome.side == "put")
            effective_max = max_pos
            if risk and policy.put_reserve > 0 and not is_put:
                effective_max = max(0, max_pos - max(0, policy.put_reserve - put_open))
            if len(open_pos) >= effective_max:
                break
            if risk and (not is_put) and call_open >= call_cap:
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
            policy_scale = 1.0
            if risk:
                policy_scale = policy.put_scale if is_put else policy.call_scale
            premium = alloc_frac * reg_scale * dd_scale * saw_scale * policy_scale * equity
            if cash < premium or premium < 10.0:
                continue
            cash -= premium
            open_pos.append(bc.OpenPosition(outcome=outcome, premium=premium, entry_eq=equity))
            open_syms.add(outcome.symbol)
            equity = cash + sum(p.premium for p in open_pos)

        if equity > peak:
            peak = equity
            peak_date = today
        dd = 1.0 - equity / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            max_dd_peak_date = peak_date
            max_dd_trough_date = today
        if keep_logs:
            equity_curve.append(
                {
                    "date": today,
                    "equity": equity,
                    "cash": cash,
                    "open_premium": sum(p.premium for p in open_pos),
                    "open_positions": len(open_pos),
                    "open_calls": sum(1 for p in open_pos if p.outcome.side == "call"),
                    "open_puts": sum(1 for p in open_pos if p.outcome.side == "put"),
                    "risk": int(risk),
                    "dd": dd,
                }
            )

    final = cash + sum(p.premium for p in open_pos)
    calls = [t for t in trade_log if t["side"] == "call"]
    puts = [t for t in trade_log if t["side"] == "put"]
    tp = sum(1 for t in trade_log if t["outcome"] == "tp")
    call_tp = sum(1 for t in calls if t["outcome"] == "tp")
    put_tp = sum(1 for t in puts if t["outcome"] == "tp")
    out = {
        "final_equity": final,
        "log_return": math.log(final / initial) if final > 0 and initial > 0 else -999.0,
        "max_dd": max_dd,
        "max_dd_peak_date": max_dd_peak_date,
        "max_dd_trough_date": max_dd_trough_date,
        "trades": len(trade_log),
        "call_trades": len(calls),
        "put_trades": len(puts),
        "tp_rate": tp / len(trade_log) if trade_log else None,
        "call_tp_rate": call_tp / len(calls) if calls else None,
        "put_tp_rate": put_tp / len(puts) if puts else None,
        "risk_days": risk_days,
        "risk_entry_days": risk_entry_days,
        "policy_liquidations": liq_count,
    }
    if keep_logs:
        out["trade_log"] = trade_log
        out["equity_curve"] = equity_curve
    return out


def evaluate_policy(
    policy: HardPolicy,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    close_by_sym_date: dict[str, dict[date, float]],
    td_idx: dict[date, int],
    initial: float,
) -> dict[str, Any]:
    return {
        label: run_policy(
            policy,
            outcomes_by_date,
            all_days,
            daily,
            regime_dates,
            regime_map,
            close_by_sym_date,
            td_idx,
            initial,
            start,
            end,
        )
        for label, start, end in WINDOWS
    }


def _policy_record(policy: HardPolicy) -> dict[str, Any]:
    payload = asdict(policy)
    trigger = payload.pop("trigger")
    return {**payload, **{f"trigger_{k}": v for k, v in trigger.items()}}


def score_policy(windows: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    utility = 0.0
    cliffs: list[str] = []
    for label, base in baseline.items():
        cand = windows[label]
        weight = WINDOW_WEIGHTS[label]
        dd_delta = float(base["max_dd"]) - float(cand["max_dd"])
        log_delta = float(cand["log_return"]) - float(base["log_return"])
        trade_ratio = cand["trades"] / base["trades"] if base["trades"] else 1.0
        utility += weight * (dd_delta * 160.0 + log_delta * 0.25)
        if trade_ratio < 0.35:
            utility -= weight * 12.0 * (0.35 - trade_ratio)
            cliffs.append(f"{label}:trade_ratio {trade_ratio:.1%}")
        if label in {"2020-crash", "2024"} and cand["max_dd"] > base["max_dd"] + 0.005:
            utility -= weight * 50.0 * (cand["max_dd"] - base["max_dd"])
            cliffs.append(f"{label}:dd_worse {(cand['max_dd'] - base['max_dd']):.2%}")

    full_log_ratio = windows["full"]["log_return"] / baseline["full"]["log_return"] if baseline["full"]["log_return"] else 1.0
    crash_dd_delta = baseline["2020-crash"]["max_dd"] - windows["2020-crash"]["max_dd"]
    if full_log_ratio < 0.35:
        utility -= (0.35 - full_log_ratio) * 40.0
        cliffs.append(f"full:log_ratio {full_log_ratio:.1%}")
    rc_pass = (
        crash_dd_delta >= 0.25
        and windows["2020-crash"]["max_dd"] <= 0.75
        and windows["2024"]["max_dd"] <= baseline["2024"]["max_dd"] + 0.005
        and windows["22-now"]["max_dd"] <= baseline["22-now"]["max_dd"] + 0.02
        and full_log_ratio >= 0.35
    )
    return {
        "utility": utility,
        "rc_pass": rc_pass,
        "full_log_ratio": full_log_ratio,
        "crash_dd_delta": crash_dd_delta,
        "cliffs": ";".join(cliffs),
    }


def flatten_result(policy: HardPolicy, windows: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    score = score_policy(windows, baseline)
    row = {"policy": policy.name, **_policy_record(policy), **score}
    for label, cand in windows.items():
        base = baseline[label]
        row[f"{label}_log_return"] = cand["log_return"]
        row[f"{label}_log_delta"] = cand["log_return"] - base["log_return"]
        row[f"{label}_max_dd"] = cand["max_dd"]
        row[f"{label}_dd_delta"] = base["max_dd"] - cand["max_dd"]
        row[f"{label}_trades"] = cand["trades"]
        row[f"{label}_trade_ratio"] = cand["trades"] / base["trades"] if base["trades"] else 1.0
        row[f"{label}_calls"] = cand["call_trades"]
        row[f"{label}_puts"] = cand["put_trades"]
        row[f"{label}_liq"] = cand["policy_liquidations"]
        row[f"{label}_risk_days"] = cand["risk_days"]
        row[f"{label}_peak"] = cand["max_dd_peak_date"]
        row[f"{label}_trough"] = cand["max_dd_trough_date"]
    return row


def action_templates() -> list[dict[str, Any]]:
    return [
        {"call_scale": 0.0, "put_scale": 1.0, "risk_max_pos": 0, "risk_call_cap": 0, "put_reserve": 0, "liquidate_mode": "none", "trim_calls_to_cap": False},
        {"call_scale": 0.0, "put_scale": 1.35, "risk_max_pos": 6, "risk_call_cap": 0, "put_reserve": 3, "liquidate_mode": "all_calls", "trim_calls_to_cap": True},
        {"call_scale": 0.10, "put_scale": 1.50, "risk_max_pos": 8, "risk_call_cap": 2, "put_reserve": 3, "liquidate_mode": "losing_calls", "trim_calls_to_cap": True},
        {"call_scale": 0.25, "put_scale": 1.35, "risk_max_pos": 8, "risk_call_cap": 4, "put_reserve": 2, "liquidate_mode": "losing_calls", "trim_calls_to_cap": True},
        {"call_scale": 0.35, "put_scale": 1.20, "risk_max_pos": 10, "risk_call_cap": 6, "put_reserve": 2, "liquidate_mode": "none", "trim_calls_to_cap": False},
        {"call_scale": 0.50, "put_scale": 1.10, "risk_max_pos": 12, "risk_call_cap": 8, "put_reserve": 1, "liquidate_mode": "none", "trim_calls_to_cap": False},
        {"call_scale": 0.0, "put_scale": 2.00, "risk_max_pos": 8, "risk_call_cap": 0, "put_reserve": 4, "liquidate_mode": "all_calls", "trim_calls_to_cap": True},
        {"call_scale": 0.15, "put_scale": 1.75, "risk_max_pos": 6, "risk_call_cap": 1, "put_reserve": 4, "liquidate_mode": "losing_calls", "trim_calls_to_cap": True},
    ]


def make_policies(triggers: list[Trigger], budget: int, seed: int) -> list[HardPolicy]:
    rng = random.Random(seed)
    actions = action_templates()
    policies: list[HardPolicy] = [
        HardPolicy(
            name="baseline",
            trigger=Trigger("never", "feature", "breadth_breadth_score", "<=", -1.0),
            call_scale=1.0,
            put_scale=1.0,
            risk_max_pos=bc.MAX_POSITIONS,
            risk_call_cap=BASE_CALL_CAP,
            put_reserve=0,
            put_first=False,
            liquidate_mode="none",
            liquidate_loss_floor=-1.0,
            liquidate_min_age=99,
            cooloff_days=0,
            dd_trigger=2.0,
            trim_calls_to_cap=False,
        )
    ]
    combos: list[tuple[Trigger, dict[str, Any], int, float, int]] = []
    for trigger in triggers:
        for action in actions:
            for cooloff in (0, 2, 5, 10):
                for dd_trigger in (0.18, 0.28, 0.40, 2.0):
                    for min_age in (1, 3):
                        combos.append((trigger, action, cooloff, dd_trigger, min_age))
    rng.shuffle(combos)
    for idx, (trigger, action, cooloff, dd_trigger, min_age) in enumerate(combos[:budget], start=1):
        policies.append(
            HardPolicy(
                name=f"hard_{idx:04d}_{trigger.name}",
                trigger=trigger,
                call_scale=float(action["call_scale"]),
                put_scale=float(action["put_scale"]),
                risk_max_pos=int(action["risk_max_pos"]),
                risk_call_cap=int(action["risk_call_cap"]),
                put_reserve=int(action["put_reserve"]),
                put_first=True,
                liquidate_mode=str(action["liquidate_mode"]),
                liquidate_loss_floor=-0.05 if action["liquidate_mode"] == "losing_calls" else -1.0,
                liquidate_min_age=min_age,
                cooloff_days=cooloff,
                dd_trigger=dd_trigger,
                trim_calls_to_cap=bool(action["trim_calls_to_cap"]),
            )
        )
    return policies


def write_summary(path: Path, ranked: list[dict[str, Any]], baseline: dict[str, dict[str, Any]], meta: dict[str, Any]) -> None:
    lines: list[str] = [
        "# v60 Hard Crash Controller Research",
        "",
        "Experiment-only Stage 3 deterministic replay. No score rows or production configs were written.",
        "",
        "## Baseline",
        "",
        "| window | log_return | max_dd | trades | calls | puts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, _, _ in WINDOWS:
        row = baseline[label]
        lines.append(
            f"| {label} | {row['log_return']:+.3f} | {row['max_dd']:.1%} | "
            f"{row['trades']} | {row['call_trades']} | {row['put_trades']} |"
        )
    lines.extend(["", "## Top Candidates", ""])
    if ranked:
        lines.extend(
            [
                "| policy | utility | rc | crash dd delta | crash max_dd | 2024 dd delta | full log ratio | actions |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in ranked[:20]:
            actions = (
                f"call={float(row['call_scale']):.2f}, put={float(row['put_scale']):.2f}, "
                f"max={int(row['risk_max_pos'])}, cap={int(row['risk_call_cap'])}, "
                f"liq={row['liquidate_mode']}, cool={int(row['cooloff_days'])}"
            )
            lines.append(
                f"| `{row['policy']}` | {float(row['utility']):+.2f} | {row['rc_pass']} | "
                f"{float(row['crash_dd_delta']):+.1%} | {float(row['2020-crash_max_dd']):.1%} | "
                f"{float(row['2024_dd_delta']):+.1%} | {float(row['full_log_ratio']):.1%} | {actions} |"
            )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- ranked: `{meta['ranked_csv']}`",
            f"- all evals: `{meta['eval_jsonl']}`",
            f"- overlap features: `{meta['overlap_csv']}`",
            f"- mined triggers: `{meta['trigger_csv']}`",
            f"- best trade log: `{meta['best_trade_log']}`",
            f"- best equity curve: `{meta['best_equity_curve']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", default=str(DEFAULT_DAILY))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--version-id", type=int, default=60)
    parser.add_argument("--budget", type=int, default=500)
    parser.add_argument("--seed", type=int, default=6060)
    parser.add_argument("--trigger-limit", type=int, default=45)
    parser.add_argument("--initial", type=float, default=DEFAULT_INITIAL)
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "status.json"
    eval_jsonl = out_dir / "hard_controller_evals.jsonl"
    ranked_csv = out_dir / "hard_controller_ranked.csv"
    all_csv = out_dir / "hard_controller_all.csv"
    best_json = out_dir / "hard_controller_best.json"
    overlap_csv = out_dir / "version_overlap_features.csv"
    trigger_csv = out_dir / "stump_triggers_ranked.csv"
    summary_md = out_dir / "v60_hard_crash_controller_summary.md"
    best_trade_log = out_dir / "best_policy_trade_log.csv"
    best_equity_curve = out_dir / "best_policy_equity_curve.csv"

    started_at = datetime.now(timezone.utc).isoformat()
    _status(
        status_path,
        {
            "phase": "start",
            "state": "running",
            "started_at": started_at,
            "budget": args.budget,
            "version_id": args.version_id,
            "out_dir": str(out_dir),
        },
    )

    rows, daily = _read_daily(Path(args.daily))
    start = _as_date(args.start) or DEFAULT_START
    end = _as_date(args.end)
    rows = [
        row for row in rows
        if (_as_date(row["date"]) or DEFAULT_START) >= start
        and (end is None or (_as_date(row["date"]) or DEFAULT_START) <= end)
    ]
    daily = {_as_date(row["date"]): row for row in rows}
    _status(status_path, {"phase": "overlap", "state": "running", "started_at": started_at})
    build_overlap_features(rows, overlap_csv)
    daily = {_as_date(row["date"]): row for row in rows}

    _status(status_path, {"phase": "mine_triggers", "state": "running", "started_at": started_at})
    triggers = curated_triggers() + mine_triggers(rows, trigger_csv, limit=args.trigger_limit)
    policies = make_policies(triggers, budget=args.budget, seed=args.seed)

    av = AlgorithmVersion.get_by_id(args.version_id)
    version_id = int(av.id)
    print(f"Loading v{version_id} cascade outcomes from {start} to {end or 'latest'}", flush=True)
    _status(
        status_path,
        {
            "phase": "load_outcomes",
            "state": "running",
            "started_at": started_at,
            "version_id": version_id,
            "policy_total": len(policies),
        },
    )
    result = bc.run_cascade_backtest(
        version_id=version_id,
        min_score=70.0,
        from_date=start,
        to_date=end,
        initial=args.initial,
        verbose=True,
    )
    if not result:
        raise RuntimeError("run_cascade_backtest returned no result")
    outcomes_by_date = result["outcomes_by_date"]
    all_days = result["all_dates"]
    regime_dates = result["regime_dates"]
    regime_map = result["regime_map"]
    close_by_sym_date, td_idx = _load_close_maps(outcomes_by_date, all_days)

    baseline_policy = policies[0]
    print("Evaluating baseline", flush=True)
    baseline = evaluate_policy(
        baseline_policy,
        outcomes_by_date,
        all_days,
        daily,
        regime_dates,
        regime_map,
        close_by_sym_date,
        td_idx,
        args.initial,
    )

    evaluated: list[dict[str, Any]] = []
    total = len(policies) - 1
    for idx, policy in enumerate(policies[1:], start=1):
        t0 = time.time()
        windows = evaluate_policy(
            policy,
            outcomes_by_date,
            all_days,
            daily,
            regime_dates,
            regime_map,
            close_by_sym_date,
            td_idx,
            args.initial,
        )
        row = flatten_result(policy, windows, baseline)
        row["idx"] = idx
        row["duration_s"] = time.time() - t0
        evaluated.append(row)
        with eval_jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        if idx % 10 == 0 or idx == total:
            ranked_partial = sorted(evaluated, key=lambda r: (-float(r["rc_pass"]), -float(r["utility"])))
            best = ranked_partial[0] if ranked_partial else {}
            _status(
                status_path,
                {
                    "phase": "evaluate",
                    "state": "running",
                    "started_at": started_at,
                    "completed": idx,
                    "total": total,
                    "best_policy": best.get("policy"),
                    "best_utility": best.get("utility"),
                    "best_crash_dd_delta": best.get("crash_dd_delta"),
                    "best_crash_max_dd": best.get("2020-crash_max_dd"),
                    "best_2024_dd_delta": best.get("2024_dd_delta"),
                    "ranked_csv": str(ranked_csv),
                    "run_log": str(out_dir / "run.log"),
                },
            )

    ranked = sorted(evaluated, key=lambda r: (-int(bool(r["rc_pass"])), -float(r["utility"])))
    _write_csv(all_csv, evaluated)
    _write_csv(ranked_csv, ranked)
    if ranked:
        best_policy_name = ranked[0]["policy"]
        best_policy = next(policy for policy in policies if policy.name == best_policy_name)
        best_windows = evaluate_policy(
            best_policy,
            outcomes_by_date,
            all_days,
            daily,
            regime_dates,
            regime_map,
            close_by_sym_date,
            td_idx,
            args.initial,
        )
        best_full = run_policy(
            best_policy,
            outcomes_by_date,
            all_days,
            daily,
            regime_dates,
            regime_map,
            close_by_sym_date,
            td_idx,
            args.initial,
            DEFAULT_START,
            None,
            keep_logs=True,
        )
        _write_json(best_json, {"policy": asdict(best_policy), "ranked": ranked[0], "windows": best_windows})
        _write_csv(best_trade_log, best_full.get("trade_log", []))
        _write_csv(best_equity_curve, best_full.get("equity_curve", []))

    meta = {
        "ranked_csv": str(ranked_csv),
        "eval_jsonl": str(eval_jsonl),
        "overlap_csv": str(overlap_csv),
        "trigger_csv": str(trigger_csv),
        "best_trade_log": str(best_trade_log),
        "best_equity_curve": str(best_equity_curve),
    }
    write_summary(summary_md, ranked, baseline, meta)
    done = {
        "phase": "done",
        "state": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "version_id": version_id,
        "policy_total": total,
        "best_policy": ranked[0]["policy"] if ranked else None,
        "best_rc_pass": ranked[0]["rc_pass"] if ranked else False,
        "best_crash_dd_delta": ranked[0]["crash_dd_delta"] if ranked else None,
        "best_crash_max_dd": ranked[0]["2020-crash_max_dd"] if ranked else None,
        "best_2024_dd_delta": ranked[0]["2024_dd_delta"] if ranked else None,
        "summary": str(summary_md),
        "ranked_csv": str(ranked_csv),
        "status": str(status_path),
    }
    _write_json(out_dir / "done.json", done)
    _status(status_path, done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
