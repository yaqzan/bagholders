"""Build a daily opportunity-state dataset for Stage 3 allocation research.

The dataset intentionally separates:
  - score-surface opportunity supply from persisted Score rows;
  - admissible option-outcome supply from the deterministic cascade builder;
  - filled/open-book state from the portfolio replay;
  - market/regime state from MarketBreadth and MarketRegime.

This is read-only research plumbing. It writes only experiment artifacts.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from database.trader_database import DB
from strategy_config import STRATEGY_30DTE


OUT_DIR = Path(__file__).resolve().parent
DEFAULT_START = date(2020, 1, 1)
DEFAULT_INITIAL = 50_000.0

CALL_TIERS = ("call_95_plus", "call_85_94", "call_80_84", "call_75_79", "call_70_74")
PUT_TIERS = ("put_le_15", "put_16_20", "put_21_25")
ALL_TIERS = CALL_TIERS + PUT_TIERS
SCORE_STAGES = ("post", "pre_regime", "pre_boost")
MAX_POSITIONS = int(STRATEGY_30DTE.MAX_POSITIONS)
MAX_POSITIONS_CALL = STRATEGY_30DTE.MAX_POSITIONS_CALL
MAX_POSITIONS_PUT = STRATEGY_30DTE.MAX_POSITIONS_PUT


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"Expected date-like value, got {type(value).__name__}")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(num: float, den: float) -> float | None:
    return (num / den) if den else None


def _tier_for_score(score: float) -> str | None:
    if score >= 95:
        return "call_95_plus"
    if score >= 85:
        return "call_85_94"
    if score >= 80:
        return "call_80_84"
    if score >= 75:
        return "call_75_79"
    if score >= 70:
        return "call_70_74"
    if score <= 15:
        return "put_le_15"
    if score <= 20:
        return "put_16_20"
    if score <= 25:
        return "put_21_25"
    return None


def _tier_alloc(tier: str | None) -> float:
    if tier is None:
        return 0.0
    if tier == "call_95_plus":
        return float(STRATEGY_30DTE.TIER_ALLOC["ultra"])
    if tier == "call_85_94":
        return float(STRATEGY_30DTE.TIER_ALLOC["top"])
    if tier == "call_80_84":
        return float(STRATEGY_30DTE.TIER_ALLOC["mid"])
    if tier == "call_75_79":
        return float(STRATEGY_30DTE.TIER_ALLOC["low"])
    if tier == "call_70_74":
        return float(STRATEGY_30DTE.TIER_ALLOC["overflow"])
    if tier == "put_le_15":
        return float(STRATEGY_30DTE.PUT_TIER_ALLOC["put_top"])
    if tier == "put_16_20":
        return float(STRATEGY_30DTE.PUT_TIER_ALLOC["put_mid"])
    if tier == "put_21_25":
        return float(STRATEGY_30DTE.PUT_TIER_ALLOC["put_low"])
    return 0.0


def _call_primary(tier: str | None) -> bool:
    return tier in {"call_95_plus", "call_85_94", "call_80_84", "call_75_79"}


def _put_primary(tier: str | None) -> bool:
    return tier in {"put_le_15", "put_16_20", "put_21_25"}


def _add_score_opportunity(row: dict[str, Any], prefix: str, score: float | None, sector: str | None) -> None:
    if score is None:
        return
    tier = _tier_for_score(score)
    if tier is None:
        return
    side = "call" if tier.startswith("call") else "put"
    row[f"{prefix}_{side}_n"] += 1
    row[f"{prefix}_{tier}_n"] += 1
    row[f"{prefix}_total_n"] += 1
    row[f"{prefix}_{side}_alloc_demand"] += _tier_alloc(tier)
    row[f"{prefix}_total_alloc_demand"] += _tier_alloc(tier)
    if _call_primary(tier):
        row[f"{prefix}_call_primary_n"] += 1
        row[f"{prefix}_call_primary_alloc_demand"] += _tier_alloc(tier)
    if _put_primary(tier):
        row[f"{prefix}_put_primary_n"] += 1
        row[f"{prefix}_put_primary_alloc_demand"] += _tier_alloc(tier)
    if sector:
        key = f"_{prefix}_{side}_sector_counts"
        row[key][sector] += 1


def _init_day(date_value: date) -> dict[str, Any]:
    row: dict[str, Any] = {"date": date_value.isoformat()}
    for prefix in SCORE_STAGES:
        for side in ("call", "put"):
            row[f"{prefix}_{side}_n"] = 0
            row[f"{prefix}_{side}_alloc_demand"] = 0.0
        row[f"{prefix}_call_primary_n"] = 0
        row[f"{prefix}_put_primary_n"] = 0
        row[f"{prefix}_call_primary_alloc_demand"] = 0.0
        row[f"{prefix}_put_primary_alloc_demand"] = 0.0
        row[f"{prefix}_total_n"] = 0
        row[f"{prefix}_total_alloc_demand"] = 0.0
        for tier in ALL_TIERS:
            row[f"{prefix}_{tier}_n"] = 0
        row[f"_{prefix}_call_sector_counts"] = defaultdict(int)
        row[f"_{prefix}_put_sector_counts"] = defaultdict(int)

    for prefix in ("outcome", "effective", "filled", "open"):
        for side in ("call", "put"):
            row[f"{prefix}_{side}_n"] = 0
            row[f"{prefix}_{side}_premium"] = 0.0
        row[f"{prefix}_total_n"] = 0
        row[f"{prefix}_total_premium"] = 0.0
        for tier in ALL_TIERS:
            row[f"{prefix}_{tier}_n"] = 0

    row.update(
        {
            "equity": None,
            "cash": None,
            "open_premium": None,
            "open_slots": 0,
            "free_slots": None,
            "pool_full": False,
            "cash_bound": False,
            "dd": None,
            "entry_trade_n": 0,
            "entry_call_trade_n": 0,
            "entry_put_trade_n": 0,
            "entry_tp_rate": None,
            "entry_call_tp_rate": None,
            "entry_put_tp_rate": None,
            "entry_avg_hold_bars": None,
            "entry_avg_pnl_pct": None,
            "offered_tp_rate": None,
            "offered_call_tp_rate": None,
            "offered_put_tp_rate": None,
            "offered_avg_hold_bars": None,
            "next_15d_equity_return": None,
            "next_30d_equity_return": None,
            "next_15d_worst_equity_return": None,
            "next_30d_worst_equity_return": None,
            "next_15d_dd_increase": None,
            "next_30d_dd_increase": None,
        }
    )
    return row


def _load_score_surface(version_id: int, start: date, end: date | None) -> dict[str, dict[str, Any]]:
    print("Loading score opportunity surface...", flush=True)
    DB.connect(reuse_if_open=True)
    sql = """
        SELECT s.date, s.overall, s.weight_info, st.sector
        FROM scores s
        LEFT JOIN stocks st ON st.symbol = s.symbol
        WHERE s.version_id = %s
          AND s.date >= %s
          AND s.date <= %s
    """
    out: dict[str, dict[str, Any]] = {}
    total_rows = 0
    final_end = end or date.today()
    y = start.year
    while y <= final_end.year:
        y_start = max(start, date(y, 1, 1))
        y_end = min(final_end, date(y, 12, 31))
        print(f"  score rows {y_start}..{y_end}", flush=True)
        cur = DB.execute_sql(sql, [version_id, y_start, y_end])
        year_rows = 0
        while True:
            batch = cur.fetchmany(5000)
            if not batch:
                break
            year_rows += len(batch)
            total_rows += len(batch)
            for d_raw, overall_raw, weight_info, sector in batch:
                d = _as_date(d_raw)
                key = d.isoformat()
                row = out.setdefault(key, _init_day(d))
                post = _as_float(overall_raw)
                pre_regime = None
                pre_boost = None
                if weight_info:
                    try:
                        info = json.loads(weight_info)
                        pre_regime = _as_float(info.get("pre_regime"))
                        pre_boost = _as_float(info.get("pre_boost"))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass

                _add_score_opportunity(row, "post", post, sector)
                _add_score_opportunity(row, "pre_regime", pre_regime, sector)
                _add_score_opportunity(row, "pre_boost", pre_boost, sector)

                post_call = post is not None and post >= 75
                post_put = post is not None and post <= 25
                pre_call = pre_boost is not None and pre_boost >= 75
                pre_put = pre_boost is not None and pre_boost <= 25
                row["pre_boost_call_admitted_by_post_n"] = row.get("pre_boost_call_admitted_by_post_n", 0) + int(post_call and not pre_call)
                row["pre_boost_call_suppressed_by_post_n"] = row.get("pre_boost_call_suppressed_by_post_n", 0) + int(pre_call and not post_call)
                row["pre_boost_put_admitted_by_post_n"] = row.get("pre_boost_put_admitted_by_post_n", 0) + int(post_put and not pre_put)
                row["pre_boost_put_suppressed_by_post_n"] = row.get("pre_boost_put_suppressed_by_post_n", 0) + int(pre_put and not post_put)
        print(f"    loaded {year_rows:,} rows; cumulative days={len(out):,}", flush=True)
        y += 1

    print(f"  score rows: {total_rows:,}; days: {len(out):,}", flush=True)
    return out


def _sector_stats(row: dict[str, Any]) -> None:
    for prefix in SCORE_STAGES:
        for side in ("call", "put"):
            counts: dict[str, int] = row.pop(f"_{prefix}_{side}_sector_counts", {})
            total = sum(counts.values())
            max_count = max(counts.values()) if counts else 0
            row[f"{prefix}_{side}_sector_count"] = len(counts)
            row[f"{prefix}_{side}_max_sector_n"] = max_count
            row[f"{prefix}_{side}_max_sector_share"] = _safe_div(max_count, total) or 0.0
            row[f"{prefix}_{side}_sector_hhi"] = (
                sum((v / total) ** 2 for v in counts.values()) if total else 0.0
            )


def _normalize_outcome_tier(tier: str) -> str | None:
    mapping = {
        "95+": "call_95_plus",
        "85-94": "call_85_94",
        "80-84": "call_80_84",
        "75-79": "call_75_79",
        "70-74": "call_70_74",
        "p<=15": "put_le_15",
        "p16-20": "put_16_20",
        "p21-25": "put_21_25",
    }
    return mapping.get(tier)


def _add_outcome_supply(row: dict[str, Any], outcome: Any, prefix: str) -> None:
    side = getattr(outcome, "side", "call")
    tier = _normalize_outcome_tier(getattr(outcome, "tier", ""))
    row[f"{prefix}_{side}_n"] += 1
    row[f"{prefix}_total_n"] += 1
    if tier:
        row[f"{prefix}_{tier}_n"] += 1


def _group_entry_trades(result: dict[str, Any]) -> tuple[dict[date, list[dict]], dict[date, list[dict]]]:
    by_entry: dict[date, list[dict]] = defaultdict(list)
    by_exit: dict[date, list[dict]] = defaultdict(list)
    for trade in result.get("trade_log", []):
        by_entry[_as_date(trade["entry_date"])].append(trade)
        by_exit[_as_date(trade["exit_date"])].append(trade)
    for holding in result.get("open_holdings", []):
        h = dict(holding)
        h["outcome"] = "open"
        h["pnl_pct"] = 0.0
        by_entry[_as_date(h["entry_date"])].append(h)
    return by_entry, by_exit


def _replay_current_portfolio(result: dict[str, Any], initial: float) -> dict[str, Any]:
    """Replay precomputed outcomes with current 30DTE portfolio caps.

    backtest_cascade.py computes the option outcomes and applies most shipped
    portfolio constants, but its deterministic replay path does not have the
    v60 12-call side cap. Keep this correction experiment-local so daily_state
    is built from the current shipped 30DTE portfolio without touching the
    production deterministic backtest during research.
    """

    outcomes_by_date = result.get("outcomes_by_date") or {}
    all_dates = result.get("all_dates") or []
    regime_dates = result.get("regime_dates") or []
    regime_map = result.get("regime_map") or {}

    cap_call = MAX_POSITIONS_CALL if MAX_POSITIONS_CALL is not None else MAX_POSITIONS
    cap_put = MAX_POSITIONS_PUT if MAX_POSITIONS_PUT is not None else MAX_POSITIONS
    side_capped = MAX_POSITIONS_CALL is not None or MAX_POSITIONS_PUT is not None

    cash = initial
    open_pos: list[bc.OpenPosition] = []
    trade_log: list[dict[str, Any]] = []
    equity_curve: list[tuple[date, float]] = []
    peak_equity = initial
    max_dd = 0.0

    for today in all_dates:
        n_day_start = len(trade_log)
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
                        "sigma": pos.outcome.sigma_daily,
                        "premium": pos.premium,
                        "outcome": pos.outcome.outcome,
                        "hold_bars": pos.outcome.hold_bars,
                        "pnl": proceeds - pos.premium,
                        "pnl_pct": pos.outcome.net_return,
                        "stressed": pos.outcome.stressed,
                        "side": pos.outcome.side,
                        "entry_price": pos.outcome.entry_price,
                        "exit_price": pos.outcome.exit_price,
                    }
                )
            else:
                remaining.append(pos)
        open_pos = remaining

        equity = cash + sum(p.premium for p in open_pos)
        running_dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0
        open_syms = {p.outcome.symbol for p in open_pos}

        for outcome in outcomes_by_date.get(today, []):
            if outcome.symbol in open_syms:
                continue
            is_put = getattr(outcome, "side", "call") == "put"
            call_open = sum(1 for p in open_pos if p.outcome.side == "call")
            put_open = len(open_pos) - call_open
            if len(open_pos) >= MAX_POSITIONS:
                break
            if side_capped and not is_put and call_open >= cap_call:
                continue
            if side_capped and is_put and put_open >= cap_put:
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
            premium = alloc_frac * reg_scale * dd_scale * saw_scale * equity
            if cash < premium or premium < 10.0:
                continue

            cash -= premium
            open_pos.append(bc.OpenPosition(outcome=outcome, premium=premium, entry_eq=equity))
            open_syms.add(outcome.symbol)
            equity = cash + sum(p.premium for p in open_pos)

        if equity > peak_equity:
            peak_equity = equity
        dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0
        max_dd = max(max_dd, dd)
        equity_curve.append((today, equity))
        for idx in range(n_day_start, len(trade_log)):
            trade_log[idx]["portfolio_value"] = equity

    open_holdings = [
        {
            "entry_date": pos.outcome.signal_date,
            "hard_sell_date": pos.outcome.deadline,
            "symbol": pos.outcome.symbol,
            "score": pos.outcome.score,
            "tier": pos.outcome.tier,
            "sigma": pos.outcome.sigma_daily,
            "premium": pos.premium,
            "side": pos.outcome.side,
            "entry_price": pos.outcome.entry_price,
            "current_price": pos.outcome.exit_price,
            "tp_price": pos.outcome.tp_price,
            "sl_price": pos.outcome.sl_price,
            "hold_bars": pos.outcome.hold_bars,
        }
        for pos in open_pos
    ]

    replay = dict(result)
    replay.update(
        {
            "equity_curve": equity_curve,
            "trade_log": trade_log,
            "open_holdings": open_holdings,
            "final_equity": equity_curve[-1][1] if equity_curve else initial,
            "max_dd": max_dd,
            "initial": initial,
            "portfolio_replay_source": "daily_opportunity_allocation_v60_caps",
        }
    )
    return replay


def _reconstruct_open_positions(result: dict[str, Any], days: list[date]) -> list[list[dict[str, Any]]]:
    idx = {d: i for i, d in enumerate(days)}
    open_by_day: list[list[dict[str, Any]]] = [[] for _ in days]
    for trade in result.get("trade_log", []):
        e = idx.get(_as_date(trade["entry_date"]))
        x = idx.get(_as_date(trade["exit_date"]))
        if e is None:
            continue
        if x is None:
            x = len(days)
        for i in range(e, max(e, x)):
            open_by_day[i].append(trade)
    for holding in result.get("open_holdings", []):
        e = idx.get(_as_date(holding["entry_date"]))
        if e is None:
            continue
        h = dict(holding)
        h["outcome"] = "open"
        h["pnl_pct"] = 0.0
        for i in range(e, len(days)):
            open_by_day[i].append(h)
    return open_by_day


def _summarize_trades(row: dict[str, Any], trades: list[dict[str, Any]]) -> None:
    if not trades:
        return
    row["entry_trade_n"] = len(trades)
    row["entry_call_trade_n"] = sum(1 for t in trades if t.get("side", "call") == "call")
    row["entry_put_trade_n"] = sum(1 for t in trades if t.get("side") == "put")
    closed = [t for t in trades if t.get("outcome") in {"tp", "sl", "hard", "realloc"}]
    if closed:
        row["entry_tp_rate"] = sum(1 for t in closed if t.get("outcome") == "tp") / len(closed)
        call_closed = [t for t in closed if t.get("side", "call") == "call"]
        put_closed = [t for t in closed if t.get("side") == "put"]
        if call_closed:
            row["entry_call_tp_rate"] = sum(1 for t in call_closed if t.get("outcome") == "tp") / len(call_closed)
        if put_closed:
            row["entry_put_tp_rate"] = sum(1 for t in put_closed if t.get("outcome") == "tp") / len(put_closed)
        row["entry_avg_hold_bars"] = sum(float(t.get("hold_bars") or 0) for t in closed) / len(closed)
        row["entry_avg_pnl_pct"] = sum(float(t.get("pnl_pct") or 0) for t in closed) / len(closed)


def _summarize_outcomes(row: dict[str, Any], outcomes: Iterable[Any]) -> None:
    rows = list(outcomes)
    closed = [o for o in rows if getattr(o, "outcome", None) in {"tp", "sl", "hard"}]
    if not closed:
        return
    row["offered_tp_rate"] = sum(1 for o in closed if o.outcome == "tp") / len(closed)
    calls = [o for o in closed if getattr(o, "side", "call") == "call"]
    puts = [o for o in closed if getattr(o, "side", None) == "put"]
    if calls:
        row["offered_call_tp_rate"] = sum(1 for o in calls if o.outcome == "tp") / len(calls)
    if puts:
        row["offered_put_tp_rate"] = sum(1 for o in puts if o.outcome == "tp") / len(puts)
    row["offered_avg_hold_bars"] = sum(float(getattr(o, "hold_bars", 0) or 0) for o in closed) / len(closed)


def _rows_by_date(table: str, columns: list[str], start: date, end: date | None) -> tuple[list[date], dict[date, dict[str, Any]]]:
    end_clause = "AND date <= %s" if end else ""
    params: list[Any] = [start]
    if end:
        params.append(end)
    sql = f"SELECT date, {', '.join(columns)} FROM {table} WHERE date >= %s {end_clause} ORDER BY date"
    rows = DB.execute_sql(sql, params).fetchall()
    dates: list[date] = []
    data: dict[date, dict[str, Any]] = {}
    for raw in rows:
        d = _as_date(raw[0])
        dates.append(d)
        data[d] = {columns[i]: raw[i + 1] for i in range(len(columns))}
    return dates, data


def _latest_on_or_before(dates: list[date], data: dict[date, dict[str, Any]], d: date) -> dict[str, Any]:
    idx = bisect.bisect_right(dates, d) - 1
    if idx < 0:
        return {}
    return data.get(dates[idx], {})


def _apply_regime_features(rows: dict[str, dict[str, Any]], start: date, end: date | None) -> None:
    print("Joining breadth/regime features...", flush=True)
    breadth_cols = [
        "breadth_score",
        "pct_above_ema50",
        "pct_above_ema200",
        "mcclellan_oscillator",
        "mcclellan_summation",
        "ad_ratio",
        "trin",
        "sector_etf_pct_above_ema50",
        "sector_etf_pct_above_ema200",
        "sector_etf_avg_rsi",
        "sector_etf_breadth_1d_change",
        "sector_etf_breadth_5d_change",
        "sector_etf_breadth_10d_change",
        "sector_etf_breadth_15d_change",
        "sector_etf_breadth_avg5",
        "sector_etf_breadth_10d_position",
        "sector_etf_breadth_30d_position",
        "sector_etf_market_wave_score",
        "sector_etf_market_wave_signed",
        "sector_etf_market_wave_state",
    ]
    regime_cols = [
        "vix_close",
        "vix_10d_change",
        "spy_close",
        "spy_ema50",
        "spy_ema200",
        "internal_breadth_score",
        "vix_score",
        "market_trend_score",
        "regime_composite",
        "regime_multiplier",
        "fg_composite",
        "fg_multiplier",
        "credit_spread_score",
        "haven_score",
        "skew_score",
    ]
    b_dates, b_data = _rows_by_date("market_breadth", breadth_cols, start, end)
    r_dates, r_data = _rows_by_date("market_regime", regime_cols, start, end)

    for key, row in rows.items():
        d = _as_date(key)
        b = _latest_on_or_before(b_dates, b_data, d)
        r = _latest_on_or_before(r_dates, r_data, d)
        for col in breadth_cols:
            row[f"breadth_{col}"] = b.get(col)
        for col in regime_cols:
            row[f"regime_{col}"] = r.get(col)
        spy_close = _as_float(r.get("spy_close"))
        spy_ema50 = _as_float(r.get("spy_ema50"))
        spy_ema200 = _as_float(r.get("spy_ema200"))
        row["regime_spy_pct_from_ema50"] = (
            (spy_close - spy_ema50) / spy_ema50 * 100.0 if spy_close and spy_ema50 else None
        )
        row["regime_spy_pct_from_ema200"] = (
            (spy_close - spy_ema200) / spy_ema200 * 100.0 if spy_close and spy_ema200 else None
        )


def _apply_portfolio_replay(rows: dict[str, dict[str, Any]], version_id: int, start: date, initial: float, end: date | None) -> dict[str, Any]:
    print("Running deterministic cascade replay...", flush=True)
    raw_result = bc.run_cascade_backtest(
        version_id=version_id,
        min_score=70.0,
        from_date=start,
        to_date=end,
        initial=initial,
        verbose=True,
    )
    if not raw_result:
        raise RuntimeError("No backtest result returned.")
    result = _replay_current_portfolio(raw_result, initial)

    days = [_as_date(d) for d, _ in result["equity_curve"]]
    equity_by_date = {_as_date(d): float(eq) for d, eq in result["equity_curve"]}
    open_by_day = _reconstruct_open_positions(result, days)
    entry_trades, _ = _group_entry_trades(result)
    peak = initial
    dd_by_date: dict[date, float] = {}

    for i, d in enumerate(days):
        key = d.isoformat()
        row = rows.setdefault(key, _init_day(d))
        equity = equity_by_date[d]
        peak = max(peak, equity)
        dd = 1.0 - equity / peak if peak else 0.0
        dd_by_date[d] = dd

        open_positions = open_by_day[i]
        open_premium = sum(float(p.get("premium") or 0.0) for p in open_positions)
        row["equity"] = equity
        row["open_premium"] = open_premium
        row["cash"] = equity - open_premium
        row["open_slots"] = len(open_positions)
        row["free_slots"] = max(0, MAX_POSITIONS - len(open_positions))
        row["pool_full"] = len(open_positions) >= MAX_POSITIONS
        row["dd"] = dd

        for pos in open_positions:
            side = pos.get("side", "call")
            tier = _normalize_outcome_tier(str(pos.get("tier", "")))
            premium = float(pos.get("premium") or 0.0)
            row[f"open_{side}_n"] += 1
            row[f"open_{side}_premium"] += premium
            row["open_total_n"] += 1
            row["open_total_premium"] += premium
            if tier:
                row[f"open_{tier}_n"] += 1

        open_syms = {p.get("symbol") for p in open_positions}
        outcomes = result["outcomes_by_date"].get(d, [])
        for outcome in outcomes:
            _add_outcome_supply(row, outcome, "outcome")
            if getattr(outcome, "symbol", None) not in open_syms:
                _add_outcome_supply(row, outcome, "effective")
        _summarize_outcomes(row, outcomes)
        _summarize_trades(row, entry_trades.get(d, []))

        for trade in entry_trades.get(d, []):
            side = trade.get("side", "call")
            tier = _normalize_outcome_tier(str(trade.get("tier", "")))
            premium = float(trade.get("premium") or 0.0)
            row[f"filled_{side}_n"] += 1
            row[f"filled_{side}_premium"] += premium
            row["filled_total_n"] += 1
            row["filled_total_premium"] += premium
            if tier:
                row[f"filled_{tier}_n"] += 1

    for i, d in enumerate(days):
        row = rows[d.isoformat()]
        equity = equity_by_date[d]
        dd = dd_by_date[d]
        for horizon in (15, 30):
            window = days[i + 1 : i + 1 + horizon]
            if not window:
                continue
            end_eq = equity_by_date[window[-1]]
            worst_eq = min(equity_by_date[x] for x in window)
            worst_dd = max(dd_by_date[x] for x in window)
            row[f"next_{horizon}d_equity_return"] = (end_eq - equity) / equity if equity else None
            row[f"next_{horizon}d_worst_equity_return"] = (worst_eq - equity) / equity if equity else None
            row[f"next_{horizon}d_dd_increase"] = max(0.0, worst_dd - dd)

    return result


def _finalize_rows(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for key in sorted(rows):
        row = rows[key]
        _sector_stats(row)
        row["post_call_put_ratio"] = _safe_div(row["post_call_primary_n"], row["post_put_primary_n"])
        row["post_put_call_ratio"] = _safe_div(row["post_put_primary_n"], row["post_call_primary_n"])
        row["post_net_call_minus_put_n"] = row["post_call_primary_n"] - row["post_put_primary_n"]
        row["post_net_call_minus_put_demand"] = (
            row["post_call_primary_alloc_demand"] - row["post_put_primary_alloc_demand"]
        )
        row["post_total_primary_n"] = row["post_call_primary_n"] + row["post_put_primary_n"]
        row["post_total_primary_alloc_demand"] = (
            row["post_call_primary_alloc_demand"] + row["post_put_primary_alloc_demand"]
        )
        row["pre_boost_total_primary_n"] = row["pre_boost_call_primary_n"] + row["pre_boost_put_primary_n"]
        row["score_processing_call_delta_n"] = row["post_call_primary_n"] - row["pre_boost_call_primary_n"]
        row["score_processing_put_delta_n"] = row["post_put_primary_n"] - row["pre_boost_put_primary_n"]
        if row.get("equity") and row.get("cash") is not None:
            min_cost = min(v for v in STRATEGY_30DTE.TIER_ALLOC.values() if v > 0) * float(row["equity"])
            row["cash_bound"] = row["cash"] < min_cost and row.get("effective_total_n", 0) > 0
            row["cash_pct"] = row["cash"] / row["equity"]
            row["open_premium_pct"] = row["open_premium"] / row["equity"] if row["equity"] else None
        finalized.append({k: _serializable(v) for k, v in row.items()})
    return finalized


def _serializable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)) or value is None:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    return str(value)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("No rows to write.")
    columns: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=None)
    parser.add_argument("--initial", type=float, default=DEFAULT_INITIAL)
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    start = _as_date(args.start)
    end = _as_date(args.end) if args.end else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    av = AlgorithmVersion.get_by_id(args.version_id) if args.version_id else AlgorithmVersion.get_active_scores_version()
    version_id = int(av.id)
    commit = getattr(av, "git_commit", None)
    print(f"Active scoring version: v{version_id} {commit}", flush=True)

    rows = _load_score_surface(version_id, start, end)
    result = _apply_portfolio_replay(rows, version_id, start, args.initial, end)
    _apply_regime_features(rows, start, end)
    final_rows = _finalize_rows(rows)

    daily_csv = out_dir / "daily_state.csv"
    meta_json = out_dir / "daily_state_meta.json"
    _write_csv(daily_csv, final_rows)
    meta = {
        "version_id": version_id,
        "commit": commit,
        "start": start.isoformat(),
        "end": final_rows[-1]["date"] if final_rows else None,
        "n_days": len(final_rows),
        "n_trades": len(result.get("trade_log", [])),
        "n_open_holdings": len(result.get("open_holdings", [])),
        "max_positions": MAX_POSITIONS,
        "max_positions_call": MAX_POSITIONS_CALL,
        "max_positions_put": MAX_POSITIONS_PUT,
        "initial": args.initial,
        "output": str(daily_csv),
        "portfolio_replay_source": result.get("portfolio_replay_source"),
    }
    meta_json.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {daily_csv}", flush=True)
    print(f"Wrote {meta_json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
