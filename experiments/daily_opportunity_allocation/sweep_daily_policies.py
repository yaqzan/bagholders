"""Sweep deterministic daily allocation policies from opportunity-state features.

This is an experiment-local Stage 3 probe. It does not write scores, mutate
strategy_config, or change shipped behavior. The purpose is to test whether the
daily opportunity state can be turned into allocation, slot, and call/put spread
rules that improve downstream portfolio behavior.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
from database.models.core import AlgorithmVersion


EXP_DIR = Path(__file__).resolve().parent
DEFAULT_START = date(2020, 1, 1)
DEFAULT_TRAIN_END = date(2024, 12, 31)
DEFAULT_TEST_START = date(2025, 1, 1)
DEFAULT_INITIAL = 50_000.0


@dataclass(frozen=True)
class Policy:
    name: str
    call_mode: str = "flat"
    put_mode: str = "flat"
    slot_mode: str = "static"
    sector_guard: bool = False
    side_cap_mode: str = "none"
    total_floor: float = 0.90
    total_ceil: float = 1.18
    total_lo_quantile: str = "q40"
    total_hi_quantile: str = "q80"
    put_pressure_floor: float = 0.70
    put_pressure_ceil: float = 1.05
    sector_guard_floor: float = 0.78
    dd_guard: bool = False


POLICIES = [
    Policy("baseline"),
    Policy("call_demand_wave", call_mode="demand"),
    Policy("call_demand_sector_guard", call_mode="demand", sector_guard=True),
    Policy("total_demand_wave", call_mode="total", put_mode="total"),
    Policy("put_pressure_throttle", put_mode="put_pressure"),
    Policy("net_call_tilt", call_mode="net", put_mode="net"),
    Policy("dynamic_slots_quality", call_mode="demand", put_mode="put_pressure", slot_mode="quality"),
    Policy("sparse_opportunity_concentrate", call_mode="sparse", put_mode="sparse", slot_mode="sparse"),
    Policy("call_put_spread_guard", call_mode="net", put_mode="put_pressure", side_cap_mode="spread"),
]


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
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return v


def _read_daily(path: Path) -> dict[date, dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {_as_date(r["date"]): r for r in rows}


def _quantile(values: list[float], q: float) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return 0.0
    idx = max(0, min(len(clean) - 1, int(round((len(clean) - 1) * q))))
    return clean[idx]


def _bounds(daily: dict[date, dict[str, Any]], train_end: date) -> dict[str, dict[str, float]]:
    features = [
        "post_call_primary_alloc_demand",
        "post_put_primary_alloc_demand",
        "post_total_primary_alloc_demand",
        "post_net_call_minus_put_demand",
        "post_call_max_sector_share",
    ]
    out: dict[str, dict[str, float]] = {}
    train_rows = [r for d, r in daily.items() if d <= train_end]
    for feature in features:
        vals = [_float(r.get(feature)) for r in train_rows]
        out[feature] = {
            "q20": _quantile(vals, 0.20),
            "q40": _quantile(vals, 0.40),
            "q50": _quantile(vals, 0.50),
            "q60": _quantile(vals, 0.60),
            "q80": _quantile(vals, 0.80),
        }
    return out


def _wave(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _feature(row: dict[str, Any], name: str) -> float:
    return _float(row.get(name))


def _policy_scales(
    policy: Policy,
    side: str,
    row: dict[str, Any],
    bounds: dict[str, dict[str, float]],
    running_dd: float,
) -> float:
    mode = policy.put_mode if side == "put" else policy.call_mode
    call_demand = _feature(row, "post_call_primary_alloc_demand")
    put_demand = _feature(row, "post_put_primary_alloc_demand")
    total_demand = _feature(row, "post_total_primary_alloc_demand")
    net_demand = _feature(row, "post_net_call_minus_put_demand")
    sector_share = _feature(row, "post_call_max_sector_share")

    call_b = bounds["post_call_primary_alloc_demand"]
    put_b = bounds["post_put_primary_alloc_demand"]
    total_b = bounds["post_total_primary_alloc_demand"]
    net_b = bounds["post_net_call_minus_put_demand"]
    sector_b = bounds["post_call_max_sector_share"]

    scale = 1.0
    if mode == "demand":
        scale = _lerp(0.85, 1.25, _wave(call_demand, call_b["q40"], call_b["q80"]))
    elif mode == "total":
        lo = total_b.get(policy.total_lo_quantile, total_b["q40"])
        hi = total_b.get(policy.total_hi_quantile, total_b["q80"])
        scale = _lerp(policy.total_floor, policy.total_ceil, _wave(total_demand, lo, hi))
    elif mode == "put_pressure":
        t = _wave(put_demand, put_b["q40"], put_b["q80"])
        scale = _lerp(policy.put_pressure_ceil, policy.put_pressure_floor, t)
    elif mode == "net":
        t = _wave(net_demand, net_b["q20"], net_b["q80"])
        scale = _lerp(0.82, 1.22, t) if side == "call" else _lerp(1.10, 0.72, t)
    elif mode == "sparse":
        sparse = 1.0 - _wave(total_demand, total_b["q20"], total_b["q60"])
        if side == "call":
            scale = _lerp(1.00, 1.25, sparse) if call_demand > 0 else 0.80
        else:
            scale = _lerp(0.95, 1.10, sparse) if put_demand > 0 else 0.75

    if policy.sector_guard and side == "call":
        conc = _wave(sector_share, sector_b["q60"], sector_b["q80"])
        scale *= _lerp(1.0, policy.sector_guard_floor, conc)

    if policy.dd_guard and running_dd > 0.25:
        scale *= 0.88 if side == "call" else 0.95
    elif policy.dd_guard and running_dd > 0.15 and side == "call":
        scale *= 0.94

    return max(0.35, min(1.45, scale))


def _policy_max_positions(
    policy: Policy,
    row: dict[str, Any],
    bounds: dict[str, dict[str, float]],
    running_dd: float,
) -> int:
    base = int(bc.MAX_POSITIONS)
    if policy.slot_mode == "static":
        return base

    total = _feature(row, "post_total_primary_alloc_demand")
    total_b = bounds["post_total_primary_alloc_demand"]
    if policy.slot_mode == "quality":
        add = round(4 * _wave(total, total_b["q50"], total_b["q80"]))
        cut = round(4 * _wave(running_dd, 0.12, 0.30))
        return max(8, min(18, base + add - cut))
    if policy.slot_mode == "sparse":
        sparse = 1.0 - _wave(total, total_b["q20"], total_b["q60"])
        return max(6, min(14, base - round(5 * sparse)))
    return base


def _side_caps(policy: Policy, max_pos: int, row: dict[str, Any]) -> tuple[int | None, int | None]:
    if policy.side_cap_mode != "spread":
        return None, None
    net = _feature(row, "post_net_call_minus_put_demand")
    if net > 0.4:
        return max_pos, max(2, min(5, max_pos // 3))
    if net < -0.4:
        return max(4, max_pos // 2), max_pos
    return None, None


def _window_days(all_days: list[date], start: date, end: date | None) -> list[date]:
    settle_end = end + timedelta(days=25) if end else None
    return [d for d in all_days if d >= start and (settle_end is None or d <= settle_end)]


def run_policy(
    policy: Policy,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    bounds: dict[str, dict[str, float]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    initial: float,
    entry_start: date,
    entry_end: date | None,
) -> dict[str, Any]:
    cash = initial
    open_pos: list[bc.OpenPosition] = []
    equity_curve: list[tuple[date, float]] = []
    trade_log: list[dict[str, Any]] = []
    open_counts: list[int] = []
    peak = initial
    max_dd = 0.0
    full_days = 0
    signal_days = 0
    filled_days = 0

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
        max_pos = _policy_max_positions(policy, row, bounds, running_dd)
        call_cap, put_cap = _side_caps(policy, max_pos, row)
        if len(open_pos) >= max_pos:
            full_days += 1

        open_syms = {p.outcome.symbol for p in open_pos}
        day_outcomes = outcomes_by_date.get(today, [])
        entry_allowed = today >= entry_start and (entry_end is None or today <= entry_end)
        if day_outcomes and entry_allowed:
            signal_days += 1
        n_before = len(open_pos)
        for outcome in day_outcomes if entry_allowed else []:
            if outcome.symbol in open_syms:
                continue
            call_open = sum(1 for p in open_pos if p.outcome.side == "call")
            put_open = len(open_pos) - call_open
            if len(open_pos) >= max_pos:
                break
            is_put = getattr(outcome, "side", "call") == "put"
            if is_put and put_cap is not None and put_open >= put_cap:
                continue
            if not is_put and call_cap is not None and call_open >= call_cap:
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
            policy_scale = _policy_scales(policy, "put" if is_put else "call", row, bounds, running_dd)
            premium = alloc_frac * reg_scale * dd_scale * saw_scale * policy_scale * equity
            if cash < premium or premium < 10.0:
                continue
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
    return {
        "policy": policy.name,
        "entry_start": entry_start.isoformat(),
        "entry_end": entry_end.isoformat() if entry_end else "",
        "final_equity": final,
        "log_return": log_return,
        "max_dd": max_dd,
        "dd_primary_utility": log_return - (10.0 * max_dd),
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
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
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


def _write_markdown(path: Path, rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    full = [r for r in rows if r["window"] == "full"]
    test = [r for r in rows if r["window"] == "test"]
    full_sorted = sorted(full, key=lambda r: (r["dd_primary_utility"], -r["max_dd"]), reverse=True)
    test_sorted = sorted(test, key=lambda r: (r["dd_primary_utility"], -r["max_dd"]), reverse=True)

    def table(title: str, subset: list[dict[str, Any]]) -> list[str]:
        lines = [
            f"## {title}",
            "",
            "| policy | utility | log_return | max_dd | trades | call_tp | put_tp |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in subset[:10]:
            lines.append(
                "| {policy} | {utility:.3f} | {logr:.3f} | {dd:.1%} | {trades} | {ctp} | {ptp} |".format(
                    policy=row["policy"],
                    utility=float(row["dd_primary_utility"]),
                    logr=float(row["log_return"]),
                    dd=float(row["max_dd"]),
                    trades=int(row["trades"]),
                    ctp="" if row["call_tp_rate"] is None else f"{float(row['call_tp_rate']):.1%}",
                    ptp="" if row["put_tp_rate"] is None else f"{float(row['put_tp_rate']):.1%}",
                )
            )
        return lines

    lines = [
        "# Daily Opportunity Allocation Policy Sweep",
        "",
        "Experiment-local deterministic Stage 3 probe. Results are candidates for validation only.",
        "",
        "## Run Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- daily_state: {meta['daily_state']}",
        f"- train_end: {meta['train_end']}",
        f"- test_start: {meta['test_start']}",
        "",
    ]
    lines.extend(table("Full Window Ranking", full_sorted))
    lines.append("")
    lines.extend(table("Forward Test Ranking", test_sorted))
    lines.append("")
    lines.extend(
        [
            "## Interpretation Guardrails",
            "",
            "- A winner here is not a ship. It still needs a focused replay, stress-window validation, and MC-style stochastic validation.",
            "- Thresholds are learned only from the training window quantiles, then reused for the 2025+ forward slice.",
            "- This sweep intentionally tests smooth opportunity waves, not hard score-stage changes.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(EXP_DIR / "daily_state.csv"))
    parser.add_argument("--out-dir", default=str(EXP_DIR))
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=None)
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--initial", type=float, default=DEFAULT_INITIAL)
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END.isoformat())
    parser.add_argument("--test-start", default=DEFAULT_TEST_START.isoformat())
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / "policy_status.json"
    status.write_text(
        json.dumps(
            {
                "phase": "daily_policy_sweep",
                "state": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    daily_path = Path(args.daily)
    daily = _read_daily(daily_path)
    train_end = _as_date(args.train_end)
    test_start = _as_date(args.test_start)
    bounds = _bounds(daily, train_end)
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

    rows: list[dict[str, Any]] = []
    windows = [
        ("train", start, train_end),
        ("test", test_start, end),
        ("full", start, end),
    ]
    for policy in POLICIES:
        print(f"[policy] {policy.name}", flush=True)
        for label, w_start, w_end in windows:
            rec = run_policy(
                policy,
                outcomes_by_date,
                all_days,
                daily,
                bounds,
                regime_dates,
                regime_map,
                args.initial,
                w_start,
                w_end,
            )
            rec["window"] = label
            rows.append(rec)
            print(
                f"  {label}: util={rec['dd_primary_utility']:+.3f} "
                f"log={rec['log_return']:+.3f} dd={rec['max_dd']:.1%} trades={rec['trades']}",
                flush=True,
            )

    out_csv = out_dir / "daily_policy_sweep.csv"
    out_md = out_dir / "daily_policy_summary.md"
    _write_csv(out_csv, rows)
    meta = {
        "version_id": version_id,
        "commit": getattr(av, "git_commit", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "daily_state": str(daily_path),
        "train_end": train_end.isoformat(),
        "test_start": test_start.isoformat(),
    }
    _write_markdown(out_md, rows, meta)
    status.write_text(
        json.dumps(
            {
                "phase": "done",
                "state": "done",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "outputs": {"csv": str(out_csv), "summary": str(out_md)},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {out_csv}", flush=True)
    print(f"Wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
