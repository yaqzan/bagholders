"""Mine narrow daily allocation policy cases from conditional alpha.

This is experiment-only Stage 3 research. It takes the cohort-level rules found
by the conditional alpha and drawdown-signature miners, applies one concrete
portfolio action at a time, and replays the deterministic cascade path.

No score rows are written. No production config is mutated.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.daily_opportunity_allocation.bayes_alpha_sweep import (
    DEFAULT_INITIAL,
    WINDOWS,
    _as_date,
    _float,
    _read_daily,
    _window_days,
    utility as broad_utility,
)
from strategy_config import STRATEGY_30DTE


EXP_DIR = Path(__file__).resolve().parent
DEFAULT_DAILY = EXP_DIR / "daily_state.csv"


@dataclass(frozen=True)
class RuleSpec:
    name: str
    family: str
    description: str
    predicate: Callable[[dict[str, Any], float], bool]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    call_scale: float = 1.0
    put_scale: float = 1.0
    max_pos_delta: int = 0
    put_cap: int = 0


@dataclass(frozen=True)
class PolicyCase:
    rule: RuleSpec
    action: ActionSpec

    @property
    def name(self) -> str:
        if self.rule.name == "never":
            return "baseline"
        return f"{self.rule.name}__{self.action.name}"


def _rule_specs() -> list[RuleSpec]:
    return [
        RuleSpec(
            "put_overhang_pnl",
            "throttle",
            "open_put_n>=7 and prev5_entry_avg_pnl_pct<=0.07378",
            lambda row, _dd: _float(row.get("open_put_n")) >= 7.0
            and _float(row.get("prev5_entry_avg_pnl_pct"), 1.0) <= 0.07378,
        ),
        RuleSpec(
            "put_overhang_tp",
            "throttle",
            "open_put_n>=7 and prev5_entry_tp_rate<=0.539",
            lambda row, _dd: _float(row.get("open_put_n")) >= 7.0
            and _float(row.get("prev5_entry_tp_rate"), 1.0) <= 0.539,
        ),
        RuleSpec(
            "sparse_total_tp",
            "throttle",
            "post_total_primary_alloc_demand<=0.78 and prev5_entry_tp_rate<=0.4489",
            lambda row, _dd: _float(row.get("post_total_primary_alloc_demand"), 99.0) <= 0.78
            and _float(row.get("prev5_entry_tp_rate"), 1.0) <= 0.4489,
        ),
        RuleSpec(
            "sparse_total_pnl",
            "throttle",
            "post_total_primary_alloc_demand<=0.78 and prev5_entry_avg_pnl_pct<=0.004213",
            lambda row, _dd: _float(row.get("post_total_primary_alloc_demand"), 99.0) <= 0.78
            and _float(row.get("prev5_entry_avg_pnl_pct"), 1.0) <= 0.004213,
        ),
        RuleSpec(
            "sparse_call_tp",
            "throttle",
            "post_call_primary_alloc_demand<=0.20 and prev5_entry_tp_rate<=0.4489",
            lambda row, _dd: _float(row.get("post_call_primary_alloc_demand"), 99.0) <= 0.20
            and _float(row.get("prev5_entry_tp_rate"), 1.0) <= 0.4489,
        ),
        RuleSpec(
            "sparse_call_pnl",
            "throttle",
            "post_call_primary_alloc_demand<=0.20 and prev5_entry_avg_pnl_pct<=0.004213",
            lambda row, _dd: _float(row.get("post_call_primary_alloc_demand"), 99.0) <= 0.20
            and _float(row.get("prev5_entry_avg_pnl_pct"), 1.0) <= 0.004213,
        ),
        RuleSpec(
            "wave_divergence_pnl",
            "throttle",
            "prev5_entry_avg_pnl_pct<=0.06736 and market_wave_signed>=3.72",
            lambda row, _dd: _float(row.get("prev5_entry_avg_pnl_pct"), 1.0) <= 0.06736
            and _float(row.get("breadth_sector_etf_market_wave_signed"), -99.0) >= 3.72,
        ),
        RuleSpec(
            "wave_divergence_tp",
            "throttle",
            "prev5_entry_tp_rate<=0.5133 and market_wave_signed>=3.72",
            lambda row, _dd: _float(row.get("prev5_entry_tp_rate"), 1.0) <= 0.5133
            and _float(row.get("breadth_sector_etf_market_wave_signed"), -99.0) >= 3.72,
        ),
        RuleSpec(
            "weak_recent_combo",
            "throttle",
            "prev5_entry_tp_rate<=0.4489 and prev5_entry_avg_pnl_pct<=0.004213",
            lambda row, _dd: _float(row.get("prev5_entry_tp_rate"), 1.0) <= 0.4489
            and _float(row.get("prev5_entry_avg_pnl_pct"), 1.0) <= 0.004213,
        ),
        RuleSpec(
            "put_vix_pressure",
            "throttle",
            "filled_put_n>=2 and regime_vix_close>=19.32",
            lambda row, _dd: _float(row.get("filled_put_n")) >= 2.0
            and _float(row.get("regime_vix_close")) >= 19.32,
        ),
        RuleSpec(
            "high_total_net_call",
            "boost",
            "post_total_primary_alloc_demand>=1.68 and post_net_call_minus_put_demand>=0",
            lambda row, _dd: _float(row.get("post_total_primary_alloc_demand")) >= 1.68
            and _float(row.get("post_net_call_minus_put_demand"), -99.0) >= 0.0,
        ),
        RuleSpec(
            "high_total_dd",
            "boost",
            "post_total_primary_alloc_demand>=1.12 and running dd>=0.09559",
            lambda row, dd: _float(row.get("post_total_primary_alloc_demand")) >= 1.12 and dd >= 0.09559,
        ),
        RuleSpec(
            "high_call_total",
            "boost",
            "post_call_primary_alloc_demand>=0.70 and post_total_primary_alloc_demand>=1.68",
            lambda row, _dd: _float(row.get("post_call_primary_alloc_demand")) >= 0.70
            and _float(row.get("post_total_primary_alloc_demand")) >= 1.68,
        ),
        RuleSpec(
            "low_concentration_high_call_hhi",
            "boost",
            "post_call_max_sector_share<=0.3158 and call_sector_hhi_x_call_demand>=0.156",
            lambda row, _dd: _float(row.get("post_call_max_sector_share"), 99.0) <= 0.3158
            and _float(row.get("call_sector_hhi_x_call_demand")) >= 0.156,
        ),
    ]


def _actions_for_family(family: str) -> list[ActionSpec]:
    if family == "boost":
        return [
            ActionSpec("calls_110", call_scale=1.10),
            ActionSpec("calls_120", call_scale=1.20),
            ActionSpec("all_110", call_scale=1.10, put_scale=1.10),
            ActionSpec("maxpos_plus2", max_pos_delta=2),
            ActionSpec("calls110_plus2", call_scale=1.10, max_pos_delta=2),
        ]
    return [
        ActionSpec("all_85", call_scale=0.85, put_scale=0.85),
        ActionSpec("all_70", call_scale=0.70, put_scale=0.70),
        ActionSpec("all_50", call_scale=0.50, put_scale=0.50),
        ActionSpec("calls_70", call_scale=0.70),
        ActionSpec("calls_50", call_scale=0.50),
        ActionSpec("puts_70", put_scale=0.70),
        ActionSpec("puts_50", put_scale=0.50),
        ActionSpec("skip_calls", call_scale=0.0),
        ActionSpec("skip_puts", put_scale=0.0),
        ActionSpec("put_cap_5", put_cap=5),
        ActionSpec("put_cap_3", put_cap=3),
        ActionSpec("puts70_cap5", put_scale=0.70, put_cap=5),
        ActionSpec("maxpos_minus2", max_pos_delta=-2),
        ActionSpec("maxpos_minus4", max_pos_delta=-4),
        ActionSpec("all85_minus2", call_scale=0.85, put_scale=0.85, max_pos_delta=-2),
    ]


def _baseline_case() -> PolicyCase:
    return PolicyCase(
        RuleSpec("never", "baseline", "never active", lambda _row, _dd: False),
        ActionSpec("baseline"),
    )


def _candidate_cases() -> list[PolicyCase]:
    out = [_baseline_case()]
    for rule in _rule_specs():
        out.extend(PolicyCase(rule, action) for action in _actions_for_family(rule.family))
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


def run_policy_case(
    case: PolicyCase,
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
    signal_days = filled_days = 0
    active_days = active_signal_days = 0
    actioned_fills = put_cap_skips = max_pos_blocks = 0
    scale_sum = 0.0
    scale_n = 0
    open_counts: list[int] = []

    base_max_pos = int(STRATEGY_30DTE.MAX_POSITIONS)
    base_call_cap = STRATEGY_30DTE.MAX_POSITIONS_CALL
    base_put_cap = STRATEGY_30DTE.MAX_POSITIONS_PUT

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
        if equity > peak:
            peak = equity
        running_dd = 1.0 - equity / peak if peak > 0 else 0.0
        row = daily.get(today, {})
        active = case.rule.predicate(row, running_dd)
        if active:
            active_days += 1

        max_pos = base_max_pos
        if active and case.action.max_pos_delta:
            max_pos = max(6, min(30, base_max_pos + case.action.max_pos_delta))
        put_cap = case.action.put_cap if active and case.action.put_cap > 0 else 0
        if active and len(open_pos) >= max_pos:
            max_pos_blocks += 1

        open_syms = {p.outcome.symbol for p in open_pos}
        day_outcomes = outcomes_by_date.get(today, [])
        entry_allowed = today >= entry_start and (entry_end is None or today <= entry_end)
        if day_outcomes and entry_allowed:
            signal_days += 1
            if active:
                active_signal_days += 1
        n_before = len(open_pos)

        for outcome in day_outcomes if entry_allowed else []:
            if outcome.symbol in open_syms:
                continue
            if len(open_pos) >= max_pos:
                if active:
                    max_pos_blocks += 1
                break

            is_put = getattr(outcome, "side", "call") == "put"
            if is_put:
                if base_put_cap is not None and sum(1 for p in open_pos if p.outcome.side == "put") >= int(base_put_cap):
                    continue
            elif base_call_cap is not None and sum(1 for p in open_pos if p.outcome.side == "call") >= int(base_call_cap):
                continue
            if is_put and put_cap > 0:
                put_open = sum(1 for p in open_pos if p.outcome.side == "put")
                if put_open >= put_cap:
                    put_cap_skips += 1
                    continue

            policy_scale = 1.0
            if active:
                policy_scale = case.action.put_scale if is_put else case.action.call_scale
            if policy_scale <= 0.0:
                actioned_fills += 1
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
            premium = alloc_frac * reg_scale * dd_scale * saw_scale * policy_scale * equity
            if cash < premium or premium < 10.0:
                continue
            if active and policy_scale != 1.0:
                actioned_fills += 1
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
        "active_signal_days": active_signal_days,
        "actioned_fills": actioned_fills,
        "put_cap_skips": put_cap_skips,
        "max_pos_blocks": max_pos_blocks,
        "avg_open_slots": sum(open_counts) / len(open_counts) if open_counts else None,
        "avg_policy_scale": scale_sum / scale_n if scale_n else 1.0,
    }


def evaluate_case(
    case: PolicyCase,
    outcomes_by_date: dict[date, list[Any]],
    all_days: list[date],
    daily: dict[date, dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    initial: float,
) -> dict[str, dict[str, Any]]:
    return {
        label: run_policy_case(
            case,
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


def _case_verdict(windows: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    info = broad_utility(windows, baseline)
    reasons: list[str] = []

    def dd_delta(label: str) -> float:
        return (baseline[label]["max_dd"] - windows[label]["max_dd"]) * 100.0

    def log_delta(label: str) -> float:
        return windows[label]["log_return"] - baseline[label]["log_return"]

    def trade_ratio(label: str) -> float:
        base_trades = baseline[label]["trades"]
        return windows[label]["trades"] / base_trades if base_trades else 1.0

    if windows["full"]["actioned_fills"] < 8 and windows["full"]["put_cap_skips"] < 8:
        reasons.append("too few actioned fills")
    for label in ["test", "full", "22-now"]:
        if dd_delta(label) < -0.25:
            reasons.append(f"{label} DD worse {abs(dd_delta(label)):.2f}pp")
        min_ratio = 0.88 if label == "test" else 0.92
        if trade_ratio(label) < min_ratio:
            reasons.append(f"{label} trade ratio {trade_ratio(label):.1%}")
    for label in ["2020-crash", "2022", "2024", "2025"]:
        if dd_delta(label) < -0.25:
            reasons.append(f"{label} DD worse {abs(dd_delta(label)):.2f}pp")
    if log_delta("test") < -0.40 and dd_delta("test") < 0.50:
        reasons.append("test return regression without DD payback")
    if log_delta("full") < -0.80 and dd_delta("full") < 0.50:
        reasons.append("full return regression without DD payback")

    dd_payoff = (
        3.0 * dd_delta("test")
        + 2.0 * dd_delta("full")
        + 2.5 * dd_delta("22-now")
        + dd_delta("2020-crash")
        + dd_delta("2022")
        + 1.5 * dd_delta("2024")
        + dd_delta("2025")
    )
    return_payoff = 0.25 * log_delta("test") + 0.20 * log_delta("full") + 0.20 * log_delta("22-now")
    throughput_penalty = max(0.0, 0.95 - trade_ratio("full")) * 20.0
    action_penalty = 2.0 if "too few actioned fills" in reasons else 0.0
    usable_score = info["utility"] + dd_payoff + return_payoff - throughput_penalty - action_penalty

    return {
        **info,
        "usable_pass": not reasons,
        "usable_score": usable_score,
        "usable_reasons": reasons,
        "test_dd_delta_pp": dd_delta("test"),
        "full_dd_delta_pp": dd_delta("full"),
        "twonow_dd_delta_pp": dd_delta("22-now"),
        "crash_dd_delta_pp": dd_delta("2020-crash"),
        "y2024_dd_delta_pp": dd_delta("2024"),
        "test_log_delta": log_delta("test"),
        "full_log_delta": log_delta("full"),
        "twonow_log_delta": log_delta("22-now"),
        "full_trade_ratio": trade_ratio("full"),
        "test_trade_ratio": trade_ratio("test"),
    }


def _flatten_record(rank: int, rec: dict[str, Any]) -> dict[str, Any]:
    case: PolicyCase = rec["case"]
    info = rec["verdict"]
    out: dict[str, Any] = {
        "rank": rank,
        "policy": case.name,
        "rule": case.rule.name,
        "family": case.rule.family,
        "action": case.action.name,
        "description": case.rule.description,
        "usable_score": info["usable_score"],
        "usable_pass": info["usable_pass"],
        "rc_pass": info["rc_pass"],
        "reasons": "; ".join(info["usable_reasons"][:10]),
        "broad_utility": info["utility"],
        "test_dd_delta_pp": info["test_dd_delta_pp"],
        "full_dd_delta_pp": info["full_dd_delta_pp"],
        "twonow_dd_delta_pp": info["twonow_dd_delta_pp"],
        "crash_dd_delta_pp": info["crash_dd_delta_pp"],
        "y2024_dd_delta_pp": info["y2024_dd_delta_pp"],
        "test_log_delta": info["test_log_delta"],
        "full_log_delta": info["full_log_delta"],
        "twonow_log_delta": info["twonow_log_delta"],
        "full_trade_ratio": info["full_trade_ratio"],
        "test_trade_ratio": info["test_trade_ratio"],
        **{f"action_{k}": v for k, v in asdict(case.action).items()},
    }
    for label, row in rec["windows"].items():
        out[f"{label}_log"] = row["log_return"]
        out[f"{label}_max_dd"] = row["max_dd"]
        out[f"{label}_trades"] = row["trades"]
        out[f"{label}_active_days"] = row["active_days"]
        out[f"{label}_actioned_fills"] = row["actioned_fills"]
        out[f"{label}_put_cap_skips"] = row["put_cap_skips"]
        out[f"{label}_max_pos_blocks"] = row["max_pos_blocks"]
        out[f"{label}_avg_policy_scale"] = row["avg_policy_scale"]
    return out


def _write_summary(path: Path, baseline: dict[str, Any], ranked: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    lines = [
        "# Usable Daily Policy Case Mining",
        "",
        "Experiment-local Stage 3 deterministic screen. This is not a ship result.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- candidates: {meta['candidates']}",
        f"- daily_state: `{meta['daily_state']}`",
        "",
        "## Baseline",
        "",
        "| window | log_return | max DD | trades |",
        "|---|---:|---:|---:|",
    ]
    for label in [w[0] for w in WINDOWS]:
        row = baseline[label]
        lines.append(f"| {label} | {row['log_return']:+.3f} | {row['max_dd']:.1%} | {row['trades']} |")

    lines.extend(
        [
            "",
            "## Top Cases",
            "",
            "| rank | usable | rule | action | score | test DD d | full DD d | 22-now DD d | 2024 DD d | full trades | reasons |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for idx, rec in enumerate(ranked[:25], start=1):
        info = rec["verdict"]
        case = rec["case"]
        reasons = "; ".join(info["usable_reasons"][:3])
        lines.append(
            f"| {idx} | {str(info['usable_pass'])} | `{case.rule.name}` | `{case.action.name}` | "
            f"{info['usable_score']:+.2f} | {info['test_dd_delta_pp']:+.2f}pp | "
            f"{info['full_dd_delta_pp']:+.2f}pp | {info['twonow_dd_delta_pp']:+.2f}pp | "
            f"{info['y2024_dd_delta_pp']:+.2f}pp | {info['full_trade_ratio']:.1%} | {reasons} |"
        )

    usable = [rec for rec in ranked if rec["verdict"]["usable_pass"]]
    lines.extend(["", "## Read", ""])
    if usable:
        lines.append(f"- Deterministic usable cases found: {len(usable)}.")
        best = usable[0]
        lines.append(
            f"- Best case: `{best['case'].name}` with full DD delta "
            f"{best['verdict']['full_dd_delta_pp']:+.2f}pp and 2024 DD delta "
            f"{best['verdict']['y2024_dd_delta_pp']:+.2f}pp."
        )
        lines.append("- Next step is stable-seed MC on the top one to three cases, especially 2020-crash and 2024.")
    else:
        lines.append("- No narrow deterministic policy case cleared all usable guardrails.")
        lines.append("- Positive rows remain alpha diagnostics only; do not promote without MC-safe DD behavior.")
    lines.append("- Guardrails require non-no-op action count, no material 2020/2024 DD regression, and preserved trade throughput.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--start", default=date(2020, 1, 1).isoformat())
    parser.add_argument("--end", default=None)
    parser.add_argument("--initial", type=float, default=DEFAULT_INITIAL)
    parser.add_argument("--limit", type=int, default=0, help="Optional candidate limit for smoke tests.")
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "usable_policy_status.json"
    eval_log = out_dir / "usable_policy_evals.jsonl"
    if eval_log.exists():
        eval_log.unlink()

    started_at = datetime.now(timezone.utc).isoformat()
    _write_status(
        status_path,
        {
            "phase": "usable_policy_mining",
            "state": "loading",
            "started_at": started_at,
            "daily_state": str(args.daily),
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

    baseline_case = _baseline_case()
    print("Evaluating baseline...", flush=True)
    baseline = evaluate_case(
        baseline_case,
        outcomes_by_date,
        all_days,
        daily,
        regime_dates,
        regime_map,
        args.initial,
    )

    candidates = [case for case in _candidate_cases() if case.name != "baseline"]
    if args.limit > 0:
        candidates = candidates[: args.limit]

    records: list[dict[str, Any]] = []
    total = len(candidates)
    for idx, case in enumerate(candidates, start=1):
        t0 = time.time()
        windows = evaluate_case(case, outcomes_by_date, all_days, daily, regime_dates, regime_map, args.initial)
        verdict = _case_verdict(windows, baseline)
        rec = {"case": case, "windows": windows, "verdict": verdict, "duration_s": time.time() - t0}
        records.append(rec)
        with eval_log.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "idx": idx,
                        "policy": case.name,
                        "rule": case.rule.name,
                        "action": asdict(case.action),
                        "verdict": verdict,
                        "duration_s": rec["duration_s"],
                    },
                    default=str,
                )
                + "\n"
            )
        best = max(records, key=lambda row: row["verdict"]["usable_score"])
        _write_status(
            status_path,
            {
                "phase": "usable_policy_mining",
                "state": "running",
                "started_at": started_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "completed": idx,
                "total": total,
                "current_policy": case.name,
                "best_policy": best["case"].name,
                "best_usable_pass": best["verdict"]["usable_pass"],
                "best_usable_score": best["verdict"]["usable_score"],
                "outputs": {"jsonl": str(eval_log), "status": str(status_path)},
            },
        )
        print(
            f"[{idx:03d}/{total}] {case.name} score={verdict['usable_score']:+.2f} "
            f"usable={verdict['usable_pass']} testDDd={verdict['test_dd_delta_pp']:+.2f}pp "
            f"fullDDd={verdict['full_dd_delta_pp']:+.2f}pp 2024DDd={verdict['y2024_dd_delta_pp']:+.2f}pp "
            f"dur={rec['duration_s']:.1f}s",
            flush=True,
        )

    ranked = sorted(records, key=lambda row: row["verdict"]["usable_score"], reverse=True)
    flat_ranked = [_flatten_record(i, rec) for i, rec in enumerate(ranked, start=1)]
    ranked_csv = out_dir / "usable_policy_cases_ranked.csv"
    all_csv = out_dir / "usable_policy_cases_all.csv"
    summary_md = out_dir / "usable_policy_cases_summary.md"
    best_json = out_dir / "usable_policy_cases_best.json"
    _write_csv(ranked_csv, flat_ranked)
    _write_csv(all_csv, [_flatten_record(i, rec) for i, rec in enumerate(records, start=1)])
    meta = {
        "version_id": version_id,
        "commit": getattr(av, "git_commit", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": len(candidates),
        "daily_state": str(args.daily),
    }
    _write_summary(summary_md, baseline, ranked, meta)
    if ranked:
        best = ranked[0]
        best_json.write_text(
            json.dumps(
                {
                    "policy": best["case"].name,
                    "rule": best["case"].rule.name,
                    "rule_description": best["case"].rule.description,
                    "action": asdict(best["case"].action),
                    "verdict": best["verdict"],
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
        "best_policy": ranked[0]["case"].name if ranked else None,
        "best_usable_pass": ranked[0]["verdict"]["usable_pass"] if ranked else False,
        "outputs": {
            "jsonl": str(eval_log),
            "ranked": str(ranked_csv),
            "all": str(all_csv),
            "summary": str(summary_md),
            "best": str(best_json),
        },
    }
    _write_status(status_path, done)
    (out_dir / "done.json").write_text(json.dumps(done, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {summary_md}", flush=True)
    print(f"Wrote {ranked_csv}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
