"""Mine conditional daily allocation alpha from opportunity-state features.

This is experiment-only Stage 3 research. It reads a completed daily_state.csv
and ranks transparent one- and two-condition state rules. The output is an
alpha ledger, not a ship recommendation.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DAILY = ROOT / "daily_state.csv"
DEFAULT_TRAIN_END = date(2024, 12, 31)
DEFAULT_TEST_START = date(2025, 1, 1)

BASE_FEATURES = [
    "post_call_primary_n",
    "post_put_primary_n",
    "post_total_primary_n",
    "post_call_primary_alloc_demand",
    "post_put_primary_alloc_demand",
    "post_total_primary_alloc_demand",
    "post_net_call_minus_put_n",
    "post_net_call_minus_put_demand",
    "score_processing_call_delta_n",
    "score_processing_put_delta_n",
    "filled_call_n",
    "filled_put_n",
    "open_call_n",
    "open_put_n",
    "free_slots",
    "cash_pct",
    "open_premium_pct",
    "dd",
    "post_call_max_sector_share",
    "post_call_sector_hhi",
    "post_put_max_sector_share",
    "post_put_sector_hhi",
    "breadth_pct_above_ema50",
    "breadth_pct_above_ema200",
    "breadth_mcclellan_oscillator",
    "breadth_sector_etf_pct_above_ema50",
    "breadth_sector_etf_breadth_5d_change",
    "breadth_sector_etf_breadth_10d_change",
    "breadth_sector_etf_breadth_15d_change",
    "breadth_sector_etf_breadth_avg5",
    "breadth_sector_etf_market_wave_score",
    "breadth_sector_etf_market_wave_signed",
    "regime_vix_close",
    "regime_vix_10d_change",
    "regime_internal_breadth_score",
    "regime_vix_score",
    "regime_market_trend_score",
    "regime_regime_composite",
    "regime_regime_multiplier",
    "regime_spy_pct_from_ema50",
    "regime_spy_pct_from_ema200",
]

DERIVED_FEATURES = [
    "open_put_share",
    "filled_put_share",
    "post_call_primary_share",
    "post_put_primary_share",
    "call_sector_hhi_x_call_demand",
    "put_pressure_x_open_put",
    "net_call_x_breadth",
    "call_demand_x_market_wave",
    "put_pressure_x_market_wave",
    "prev5_entry_tp_rate",
    "prev5_entry_call_tp_rate",
    "prev5_entry_put_tp_rate",
    "prev5_entry_avg_pnl_pct",
    "prev5_entry_trade_n",
    "prev5_dd",
    "dd_5d_delta",
]

TARGETS = [
    "entry_tp_rate",
    "entry_avg_pnl_pct",
    "next_15d_worst_equity_return",
    "next_30d_worst_equity_return",
    "next_15d_dd_increase",
    "next_30d_dd_increase",
]

WINDOWS = {
    "2020-crash": (date(2020, 2, 18), date(2020, 4, 30)),
    "2022": (date(2022, 1, 1), date(2022, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "22-now": (date(2022, 1, 1), date(2026, 12, 31)),
}


@dataclass(frozen=True)
class Predicate:
    name: str
    feature: str
    op: str
    threshold: float


@dataclass
class Stats:
    days: int
    support: float
    entry_tp_edge: float
    pnl_edge: float
    worst15_edge: float
    worst30_edge: float
    dd15_edge: float
    dd30_edge: float
    score: float


def _as_date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _safe(value: float | None, default: float = 0.0) -> float:
    return value if value is not None and math.isfinite(value) else default


def _quantile(values: list[float], q: float) -> float | None:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return None
    idx = max(0, min(len(clean) - 1, round((len(clean) - 1) * q)))
    return clean[idx]


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if math.isfinite(v)]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _std(values: list[float]) -> float:
    clean = [v for v in values if math.isfinite(v)]
    if len(clean) < 2:
        return 1.0
    avg = sum(clean) / len(clean)
    var = sum((v - avg) ** 2 for v in clean) / (len(clean) - 1)
    return math.sqrt(var) or 1.0


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda row: row["date"])
    return rows


def _add_derived(rows: list[dict[str, Any]]) -> None:
    tp5: deque[float] = deque(maxlen=5)
    call_tp5: deque[float] = deque(maxlen=5)
    put_tp5: deque[float] = deque(maxlen=5)
    pnl5: deque[float] = deque(maxlen=5)
    trade5: deque[float] = deque(maxlen=5)
    dd5: deque[float] = deque(maxlen=5)

    for row in rows:
        open_call = _safe(_float(row.get("open_call_n")))
        open_put = _safe(_float(row.get("open_put_n")))
        filled_call = _safe(_float(row.get("filled_call_n")))
        filled_put = _safe(_float(row.get("filled_put_n")))
        call_primary = _safe(_float(row.get("post_call_primary_n")))
        put_primary = _safe(_float(row.get("post_put_primary_n")))
        total_primary = call_primary + put_primary
        call_demand = _safe(_float(row.get("post_call_primary_alloc_demand")))
        put_demand = _safe(_float(row.get("post_put_primary_alloc_demand")))
        net_call = _safe(_float(row.get("post_net_call_minus_put_demand")))
        call_hhi = _safe(_float(row.get("post_call_sector_hhi")))
        wave = _safe(_float(row.get("breadth_sector_etf_market_wave_signed")))
        breadth = _safe(_float(row.get("breadth_pct_above_ema50")))
        dd = _safe(_float(row.get("dd")))

        row["open_put_share"] = open_put / (open_call + open_put) if open_call + open_put else 0.0
        row["filled_put_share"] = filled_put / (filled_call + filled_put) if filled_call + filled_put else 0.0
        row["post_call_primary_share"] = call_primary / total_primary if total_primary else 0.0
        row["post_put_primary_share"] = put_primary / total_primary if total_primary else 0.0
        row["call_sector_hhi_x_call_demand"] = call_hhi * call_demand
        row["put_pressure_x_open_put"] = put_demand * open_put
        row["net_call_x_breadth"] = net_call * breadth
        row["call_demand_x_market_wave"] = call_demand * wave
        row["put_pressure_x_market_wave"] = put_demand * wave
        row["prev5_entry_tp_rate"] = _mean(list(tp5))
        row["prev5_entry_call_tp_rate"] = _mean(list(call_tp5))
        row["prev5_entry_put_tp_rate"] = _mean(list(put_tp5))
        row["prev5_entry_avg_pnl_pct"] = _mean(list(pnl5))
        row["prev5_entry_trade_n"] = _mean(list(trade5))
        row["prev5_dd"] = _mean(list(dd5))
        row["dd_5d_delta"] = dd - dd5[0] if len(dd5) == 5 else 0.0

        for key, bucket in [
            ("entry_tp_rate", tp5),
            ("entry_call_tp_rate", call_tp5),
            ("entry_put_tp_rate", put_tp5),
            ("entry_avg_pnl_pct", pnl5),
            ("entry_trade_n", trade5),
        ]:
            value = _float(row.get(key))
            if value is not None:
                bucket.append(value)
        dd5.append(dd)


def _feature_values(rows: list[dict[str, Any]], feature: str) -> list[float]:
    return [v for row in rows if (v := _float(row.get(feature))) is not None]


def _build_predicates(rows: list[dict[str, Any]], features: list[str]) -> list[Predicate]:
    predicates: list[Predicate] = []
    for feature in features:
        values = _feature_values(rows, feature)
        if len(values) < 100:
            continue
        if len(set(round(v, 10) for v in values)) < 6:
            continue
        for label, q in [("q20", 0.20), ("q40", 0.40), ("q60", 0.60), ("q80", 0.80)]:
            threshold = _quantile(values, q)
            if threshold is None:
                continue
            if label in {"q20", "q40"}:
                predicates.append(Predicate(f"{feature}<={label}({threshold:.4g})", feature, "<=", threshold))
            if label in {"q60", "q80"}:
                predicates.append(Predicate(f"{feature}>={label}({threshold:.4g})", feature, ">=", threshold))
    return predicates


def _match(row: dict[str, Any], predicate: Predicate) -> bool:
    value = _float(row.get(predicate.feature))
    if value is None:
        return False
    if predicate.op == "<=":
        return value <= predicate.threshold
    return value >= predicate.threshold


def _mask(rows: list[dict[str, Any]], predicates: tuple[Predicate, ...]) -> list[bool]:
    return [all(_match(row, predicate) for predicate in predicates) for row in rows]


def _slice(rows: list[dict[str, Any]], start: date | None, end: date | None) -> list[int]:
    out = []
    for idx, row in enumerate(rows):
        d = row["_date"]
        if start is not None and d < start:
            continue
        if end is not None and d > end:
            continue
        out.append(idx)
    return out


def _target_edge(rows: list[dict[str, Any]], indexes: list[int], mask: list[bool], target: str) -> float:
    selected = [_float(rows[idx].get(target)) for idx in indexes if mask[idx]]
    other = [_float(rows[idx].get(target)) for idx in indexes if not mask[idx]]
    selected_clean = [v for v in selected if v is not None]
    other_clean = [v for v in other if v is not None]
    if not selected_clean or not other_clean:
        return 0.0
    return (sum(selected_clean) / len(selected_clean)) - (sum(other_clean) / len(other_clean))


def _stats(rows: list[dict[str, Any]], indexes: list[int], mask: list[bool], stdevs: dict[str, float]) -> Stats:
    days = sum(1 for idx in indexes if mask[idx])
    support = days / len(indexes) if indexes else 0.0
    entry_tp_edge = _target_edge(rows, indexes, mask, "entry_tp_rate")
    pnl_edge = _target_edge(rows, indexes, mask, "entry_avg_pnl_pct")
    worst15_edge = _target_edge(rows, indexes, mask, "next_15d_worst_equity_return")
    worst30_edge = _target_edge(rows, indexes, mask, "next_30d_worst_equity_return")
    dd15_edge = _target_edge(rows, indexes, mask, "next_15d_dd_increase")
    dd30_edge = _target_edge(rows, indexes, mask, "next_30d_dd_increase")
    score = (
        (pnl_edge / stdevs["entry_avg_pnl_pct"])
        + 0.70 * (entry_tp_edge / stdevs["entry_tp_rate"])
        + 0.70 * (worst15_edge / stdevs["next_15d_worst_equity_return"])
        + 0.35 * (worst30_edge / stdevs["next_30d_worst_equity_return"])
        - 0.45 * (dd15_edge / stdevs["next_15d_dd_increase"])
        - 0.25 * (dd30_edge / stdevs["next_30d_dd_increase"])
    )
    return Stats(
        days=days,
        support=support,
        entry_tp_edge=entry_tp_edge,
        pnl_edge=pnl_edge,
        worst15_edge=worst15_edge,
        worst30_edge=worst30_edge,
        dd15_edge=dd15_edge,
        dd30_edge=dd30_edge,
        score=score,
    )


def _record(
    name: str,
    kind: str,
    predicates: tuple[Predicate, ...],
    train: Stats,
    test: Stats,
    windows: dict[str, Stats],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "conditions": " AND ".join(predicate.name for predicate in predicates),
        "train_days": train.days,
        "train_support": train.support,
        "train_score": train.score,
        "train_entry_tp_edge": train.entry_tp_edge,
        "train_pnl_edge": train.pnl_edge,
        "train_worst15_edge": train.worst15_edge,
        "train_dd15_edge": train.dd15_edge,
        "test_days": test.days,
        "test_support": test.support,
        "test_score": test.score,
        "test_entry_tp_edge": test.entry_tp_edge,
        "test_pnl_edge": test.pnl_edge,
        "test_worst15_edge": test.worst15_edge,
        "test_dd15_edge": test.dd15_edge,
        "stability_score": min(train.score, test.score) if kind == "boost" else min(-train.score, -test.score),
    }
    for label, stats in windows.items():
        out[f"{label}_days"] = stats.days
        out[f"{label}_score"] = stats.score
        out[f"{label}_entry_tp_edge"] = stats.entry_tp_edge
        out[f"{label}_pnl_edge"] = stats.pnl_edge
        out[f"{label}_worst15_edge"] = stats.worst15_edge
        out[f"{label}_dd15_edge"] = stats.dd15_edge
    return out


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    fields = list(records[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}pp"


def _summary(records: list[dict[str, Any]], args: argparse.Namespace) -> str:
    boost = [r for r in records if r["kind"] == "boost"][:12]
    throttle = [r for r in records if r["kind"] == "throttle"][:12]
    lines = [
        "# Conditional Daily Allocation Alpha Ledger",
        "",
        "Experiment-local alpha mining output. This is not a ship result.",
        "",
        "## Inputs",
        "",
        f"- daily_state: `{args.daily}`",
        f"- train_end: {args.train_end}",
        f"- test_start: {args.test_start}",
        f"- evaluated_rules: {len(records)} kept after support/stability filters",
        "",
        "## Best Boost Candidates",
        "",
        "| rank | condition | test days | test score | TP edge | PnL edge | worst15 edge | DD15 edge | 2020 score | 2024 score |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(boost, start=1):
        lines.append(
            f"| {idx} | `{row['conditions']}` | {row['test_days']} | {row['test_score']:+.2f} | "
            f"{_fmt_pct(row['test_entry_tp_edge'])} | {_fmt_pct(row['test_pnl_edge'])} | "
            f"{_fmt_pct(row['test_worst15_edge'])} | {_fmt_pct(row['test_dd15_edge'])} | "
            f"{row.get('2020-crash_score', 0.0):+.2f} | {row.get('2024_score', 0.0):+.2f} |"
        )
    lines.extend(
        [
            "",
            "## Best Throttle Candidates",
            "",
            "| rank | condition | test days | test badness | TP edge | PnL edge | worst15 edge | DD15 edge | 2020 badness | 2024 badness |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, row in enumerate(throttle, start=1):
        lines.append(
            f"| {idx} | `{row['conditions']}` | {row['test_days']} | {-row['test_score']:+.2f} | "
            f"{_fmt_pct(row['test_entry_tp_edge'])} | {_fmt_pct(row['test_pnl_edge'])} | "
            f"{_fmt_pct(row['test_worst15_edge'])} | {_fmt_pct(row['test_dd15_edge'])} | "
            f"{-row.get('2020-crash_score', 0.0):+.2f} | {-row.get('2024_score', 0.0):+.2f} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- Boost candidates are states where allocation might be allowed to expand.",
            "- Throttle candidates are states where exposure should shrink or side caps should tighten.",
            "- Same-day entry metrics are targets, not inputs. `prev5_*` features are shifted and safer for live formulas.",
            "- Any candidate still needs deterministic replay and MC validation before promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--train-end", default=str(DEFAULT_TRAIN_END))
    parser.add_argument("--test-start", default=str(DEFAULT_TEST_START))
    parser.add_argument("--min-train-days", type=int, default=80)
    parser.add_argument("--min-test-days", type=int, default=30)
    parser.add_argument("--max-rules", type=int, default=250)
    args = parser.parse_args()

    rows = _read_rows(args.daily)
    for row in rows:
        row["_date"] = _as_date(row["date"])
    _add_derived(rows)

    train_end = _as_date(args.train_end)
    test_start = _as_date(args.test_start)
    train_indexes = _slice(rows, None, train_end)
    test_indexes = _slice(rows, test_start, None)
    train_rows = [rows[idx] for idx in train_indexes]

    features = [feature for feature in BASE_FEATURES + DERIVED_FEATURES if _feature_values(train_rows, feature)]
    stdevs = {target: _std(_feature_values(train_rows, target)) for target in TARGETS}
    predicates = _build_predicates(train_rows, features)
    combos: list[tuple[Predicate, ...]] = [(predicate,) for predicate in predicates]

    pairable = [
        predicate
        for predicate in predicates
        if predicate.feature
        in {
            "post_call_primary_alloc_demand",
            "post_put_primary_alloc_demand",
            "post_total_primary_alloc_demand",
            "post_net_call_minus_put_demand",
            "post_call_sector_hhi",
            "post_call_max_sector_share",
            "filled_put_n",
            "open_put_n",
            "breadth_pct_above_ema50",
            "breadth_sector_etf_breadth_15d_change",
            "breadth_sector_etf_market_wave_signed",
            "regime_vix_close",
            "regime_internal_breadth_score",
            "prev5_entry_tp_rate",
            "prev5_entry_avg_pnl_pct",
            "dd",
            "dd_5d_delta",
            "open_put_share",
            "call_sector_hhi_x_call_demand",
            "put_pressure_x_open_put",
        }
    ]
    for left, right in itertools.combinations(pairable, 2):
        if left.feature == right.feature:
            continue
        combos.append((left, right))

    window_indexes = {label: _slice(rows, start, end) for label, (start, end) in WINDOWS.items()}
    records: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()

    for combo in combos:
        condition_key = " AND ".join(predicate.name for predicate in combo)
        if condition_key in seen_conditions:
            continue
        seen_conditions.add(condition_key)
        mask = _mask(rows, combo)
        train = _stats(rows, train_indexes, mask, stdevs)
        test = _stats(rows, test_indexes, mask, stdevs)
        if train.days < args.min_train_days or test.days < args.min_test_days:
            continue
        if not (0.05 <= train.support <= 0.65 and 0.05 <= test.support <= 0.75):
            continue
        windows = {
            label: _stats(rows, indexes, mask, stdevs)
            for label, indexes in window_indexes.items()
            if indexes
        }
        if train.score > 0.15 and test.score > 0.15:
            records.append(_record(f"boost_{len(records) + 1}", "boost", combo, train, test, windows))
        if train.score < -0.15 and test.score < -0.15:
            records.append(_record(f"throttle_{len(records) + 1}", "throttle", combo, train, test, windows))

    records.sort(key=lambda row: row["stability_score"], reverse=True)
    kept = records[: args.max_rules]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.out_dir / "conditional_alpha_ranked.csv", kept)
    (args.out_dir / "conditional_alpha_summary.md").write_text(_summary(kept, args), encoding="utf-8")
    (args.out_dir / "conditional_alpha_ledger.json").write_text(
        json.dumps({"records": kept, "daily_state": str(args.daily), "features": features}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(kept)} records to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
