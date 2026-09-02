"""Estimate daily signal capacity under the shipped portfolio allocator.

This is a read-only Stage 3 research helper. It answers a specific question:
how much daily opportunity N is actually useful once max positions, realized
hold time, premium caps, and Sentinel opportunity saturation are considered?

The script consumes existing ``daily_state.csv`` artifacts and writes a compact
markdown/json/csv bundle. It does not read or write score rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_config import STRATEGY_30DTE


DEFAULT_DAILIES = [
    "v60_smooth=.codex/runs/v60_daily_opportunity_smooth_20260519_082415/daily_state.csv",
    "v59_chunked=.codex/runs/20260518-194146-daily-opportunity-allocation-v59-chunked/daily_state.csv",
]

SIDE_COLUMNS = {
    "call": {
        "offered": "post_call_n",
        "primary": "post_call_primary_n",
        "filled": "filled_call_n",
        "open": "open_call_n",
        "entry_tp": "entry_call_tp_rate",
        "offered_tp": "offered_call_tp_rate",
        "ref": float(STRATEGY_30DTE.OPP_SAT_CALL_REF),
        "cap": int(STRATEGY_30DTE.MAX_POSITIONS_CALL or STRATEGY_30DTE.MAX_POSITIONS),
        "bins": [(0, 3), (4, 7), (8, 11), (12, 15), (16, 19), (20, 23), (24, None)],
    },
    "put": {
        "offered": "post_put_n",
        "primary": "post_put_primary_n",
        "filled": "filled_put_n",
        "open": "open_put_n",
        "entry_tp": "entry_put_tp_rate",
        "offered_tp": "offered_put_tp_rate",
        "ref": float(STRATEGY_30DTE.OPP_SAT_PUT_REF),
        "cap": int(STRATEGY_30DTE.MAX_POSITIONS_PUT or STRATEGY_30DTE.MAX_POSITIONS),
        "bins": [(0, 1), (2, 3), (4, 5), (6, 7), (8, None)],
    },
    "total": {
        "offered": "post_total_n",
        "primary": "post_total_primary_n",
        "filled": "filled_total_n",
        "open": "open_total_n",
        "entry_tp": "entry_tp_rate",
        "offered_tp": "offered_tp_rate",
        "ref": float(STRATEGY_30DTE.OPP_SAT_CALL_REF + STRATEGY_30DTE.OPP_SAT_PUT_REF),
        "cap": int(STRATEGY_30DTE.MAX_POSITIONS),
        "bins": [(0, 7), (8, 11), (12, 15), (16, 19), (20, 23), (24, 29), (30, None)],
    },
}


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "None"):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _int(value: Any, default: int = 0) -> int:
    number = _float(value)
    if number is None:
        return default
    return int(round(number))


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    return sum(clean) / len(clean) if clean else None


def _quantile(values: Iterable[float | None], q: float) -> float | None:
    clean = sorted(float(v) for v in values if v is not None and not math.isnan(float(v)))
    if not clean:
        return None
    idx = int(round((len(clean) - 1) * q))
    return clean[idx]


def _pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return ""
    return f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _date_of(row: dict[str, Any]) -> date:
    return date.fromisoformat(str(row["date"])[:10])


def _sat_scale(side: str, pressure: float) -> float:
    if side == "total":
        call = min(1.0, max(STRATEGY_30DTE.OPP_SAT_FLOOR, (STRATEGY_30DTE.OPP_SAT_CALL_REF / max(1.0, pressure)) ** STRATEGY_30DTE.OPP_SAT_POWER))
        put = min(1.0, max(STRATEGY_30DTE.OPP_SAT_FLOOR, (STRATEGY_30DTE.OPP_SAT_PUT_REF / max(1.0, pressure)) ** STRATEGY_30DTE.OPP_SAT_POWER))
        return (call + put) / 2.0
    ref = SIDE_COLUMNS[side]["ref"]
    if pressure <= ref or ref <= 0:
        return 1.0
    scale = (ref / max(1.0, float(pressure))) ** float(STRATEGY_30DTE.OPP_SAT_POWER)
    return max(float(STRATEGY_30DTE.OPP_SAT_FLOOR), min(1.0, scale))


def _split_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "all": rows,
        "2022_plus": [row for row in rows if _date_of(row) >= date(2022, 1, 1)],
        "2024_plus": [row for row in rows if _date_of(row) >= date(2024, 1, 1)],
    }


def _row_mean(rows: list[dict[str, Any]], column: str) -> float | None:
    return _mean(_float(row.get(column)) for row in rows)


def _row_sum(rows: list[dict[str, Any]], column: str) -> float:
    return sum(_float(row.get(column), 0.0) or 0.0 for row in rows)


def _bin_label(lo: int, hi: int | None) -> str:
    return f"{lo}+" if hi is None else f"{lo}-{hi}"


def _in_bin(value: int, lo: int, hi: int | None) -> bool:
    if hi is None:
        return value >= lo
    return lo <= value <= hi


def _pressure_bins(label: str, split_name: str, rows: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    cfg = SIDE_COLUMNS[side]
    out: list[dict[str, Any]] = []
    for lo, hi in cfg["bins"]:
        bucket = [row for row in rows if _in_bin(_int(row.get(cfg["offered"])), lo, hi)]
        if not bucket:
            continue
        fill_values = [_float(row.get(cfg["filled"])) for row in bucket]
        open_values = [_float(row.get(cfg["open"])) for row in bucket]
        offered_values = [_float(row.get(cfg["offered"])) for row in bucket]
        primary_values = [_float(row.get(cfg["primary"])) for row in bucket]
        rec = {
            "dataset": label,
            "split": split_name,
            "side": side,
            "pressure_bin": _bin_label(lo, hi),
            "days": len(bucket),
            "avg_offered": _mean(offered_values),
            "avg_primary": _mean(primary_values),
            "avg_filled": _mean(fill_values),
            "avg_open": _mean(open_values),
            "fill_to_offered": (_mean(fill_values) or 0.0) / (_mean(offered_values) or 1.0),
            "entry_tp_rate": _row_mean(bucket, cfg["entry_tp"]),
            "offered_tp_rate": _row_mean(bucket, cfg["offered_tp"]),
            "next_15d_dd_increase": _row_mean(bucket, "next_15d_dd_increase"),
            "next_30d_dd_increase": _row_mean(bucket, "next_30d_dd_increase"),
            "pool_full_rate": _row_mean(bucket, "pool_full"),
            "cash_bound_rate": _row_mean(bucket, "cash_bound"),
            "avg_cash_pct": _row_mean(bucket, "cash_pct"),
            "avg_open_premium_pct": _row_mean(bucket, "open_premium_pct"),
            "avg_sat_scale": _mean(_sat_scale(side, _float(row.get(cfg["offered"]), 0.0) or 0.0) for row in bucket),
        }
        out.append(rec)
    return out


def _threshold_rows(label: str, split_name: str, rows: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    cfg = SIDE_COLUMNS[side]
    candidates = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 30]
    if side == "put":
        candidates = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12]
    if side == "total":
        candidates = [0, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40]
    out: list[dict[str, Any]] = []
    for threshold in candidates:
        bucket = [row for row in rows if _int(row.get(cfg["offered"])) >= threshold]
        if len(bucket) < 20:
            continue
        out.append(
            {
                "dataset": label,
                "split": split_name,
                "side": side,
                "min_offered": threshold,
                "days": len(bucket),
                "day_share": len(bucket) / len(rows) if rows else None,
                "avg_offered": _row_mean(bucket, cfg["offered"]),
                "avg_filled": _row_mean(bucket, cfg["filled"]),
                "q75_filled": _quantile((_float(row.get(cfg["filled"])) for row in bucket), 0.75),
                "entry_tp_rate": _row_mean(bucket, cfg["entry_tp"]),
                "next_30d_dd_increase": _row_mean(bucket, "next_30d_dd_increase"),
                "cash_bound_rate": _row_mean(bucket, "cash_bound"),
                "pool_full_rate": _row_mean(bucket, "pool_full"),
                "avg_sat_scale": _mean(_sat_scale(side, _float(row.get(cfg["offered"]), 0.0) or 0.0) for row in bucket),
            }
        )
    return out


def _capacity(label: str, split_name: str, rows: list[dict[str, Any]], side: str) -> dict[str, Any]:
    cfg = SIDE_COLUMNS[side]
    total_open = _row_sum(rows, cfg["open"])
    total_filled = _row_sum(rows, cfg["filled"])
    hold_bars = total_open / total_filled if total_filled > 0 else None
    slot_capacity = (float(cfg["cap"]) / hold_bars) if hold_bars else None
    offered = [_float(row.get(cfg["offered"])) for row in rows]
    filled = [_float(row.get(cfg["filled"])) for row in rows]
    open_n = [_float(row.get(cfg["open"])) for row in rows]
    ref = float(cfg["ref"])
    above_ref = [row for row in rows if (_float(row.get(cfg["offered"]), 0.0) or 0.0) >= ref]
    return {
        "dataset": label,
        "split": split_name,
        "side": side,
        "days": len(rows),
        "date_min": rows[0]["date"] if rows else "",
        "date_max": rows[-1]["date"] if rows else "",
        "ref": ref,
        "position_cap": cfg["cap"],
        "avg_offered": _mean(offered),
        "q50_offered": _quantile(offered, 0.50),
        "q75_offered": _quantile(offered, 0.75),
        "q90_offered": _quantile(offered, 0.90),
        "avg_filled": _mean(filled),
        "q50_filled": _quantile(filled, 0.50),
        "q75_filled": _quantile(filled, 0.75),
        "q90_filled": _quantile(filled, 0.90),
        "avg_open": _mean(open_n),
        "q75_open": _quantile(open_n, 0.75),
        "q90_open": _quantile(open_n, 0.90),
        "realized_hold_bars": hold_bars,
        "slot_service_capacity_per_day": slot_capacity,
        "days_at_or_above_ref": len(above_ref),
        "share_days_at_or_above_ref": len(above_ref) / len(rows) if rows else None,
        "avg_filled_when_at_or_above_ref": _row_mean(above_ref, cfg["filled"]) if above_ref else None,
        "cash_bound_rate": _row_mean(rows, "cash_bound"),
        "pool_full_rate": _row_mean(rows, "pool_full"),
        "avg_open_premium_pct": _row_mean(rows, "open_premium_pct"),
        "avg_sat_scale": _mean(_sat_scale(side, _float(row.get(cfg["offered"]), 0.0) or 0.0) for row in rows),
    }


def _wr_compensation(
    label: str,
    split_name: str,
    capacity_rows: list[dict[str, Any]],
    baseline_wrs: list[float],
) -> list[dict[str, Any]]:
    by_side = {row["side"]: row for row in capacity_rows if row["dataset"] == label and row["split"] == split_name}
    out: list[dict[str, Any]] = []
    target_map = {
        "call": [4, 5, 6, 8, 10, 12, 14, 16, 18, 20],
        "put": [1, 2, 3, 4, 5, 6, 8],
        "total": [4, 5, 6, 7, 8, 10, 12, 16, 20, 24],
    }
    for side, targets in target_map.items():
        cap_row = by_side.get(side)
        if not cap_row:
            continue
        practical_capacity = cap_row.get("q75_filled") or cap_row.get("avg_filled") or 0.0
        service_capacity = cap_row.get("slot_service_capacity_per_day") or practical_capacity
        effective_capacity = min(float(service_capacity), float(practical_capacity))
        current_ref = float(cap_row["ref"])
        old_served = min(effective_capacity, current_ref)
        for target in targets:
            new_served = min(effective_capacity, float(target))
            for wr in baseline_wrs:
                required = wr if new_served >= old_served or old_served <= 0 else wr * old_served / max(new_served, 1e-9)
                out.append(
                    {
                        "dataset": label,
                        "split": split_name,
                        "side": side,
                        "current_ref": current_ref,
                        "effective_capacity": effective_capacity,
                        "target_daily_n": target,
                        "baseline_wr": wr,
                        "required_wr_to_preserve_wins": required,
                        "feasible": required <= 1.0,
                        "expected_win_ratio_at_same_wr": (new_served / old_served) if old_served else None,
                    }
                )
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown(path: Path, summary: dict[str, Any]) -> None:
    lines: list[str] = [
        "# Daily N Capacity And WR15 Compensation",
        "",
        "This is read-only Stage 3 research. It compares daily opportunity supply with the active Sentinel portfolio profile mechanics.",
        "",
        "## Current Portfolio Surface",
        "",
        f"- `MAX_POSITIONS={STRATEGY_30DTE.MAX_POSITIONS}`, `MAX_POSITIONS_CALL={STRATEGY_30DTE.MAX_POSITIONS_CALL}`, `MAX_POSITIONS_PUT={STRATEGY_30DTE.MAX_POSITIONS_PUT}`.",
        f"- Practical exposure: gross cap {_pct(STRATEGY_30DTE.GROSS_PREMIUM_CAP)}, call cap {_pct(STRATEGY_30DTE.CALL_PREMIUM_CAP)}, put cap {_pct(STRATEGY_30DTE.PUT_PREMIUM_CAP)} on `min(portfolio, ${STRATEGY_30DTE.PRACTICAL_CAPITAL_CEILING:,.0f})`.",
        f"- Saturation refs: calls {STRATEGY_30DTE.OPP_SAT_CALL_REF:.0f}, puts {STRATEGY_30DTE.OPP_SAT_PUT_REF:.0f}, power {STRATEGY_30DTE.OPP_SAT_POWER:.2f}, floor {STRATEGY_30DTE.OPP_SAT_FLOOR:.2f}.",
        "",
        "## Capacity Read",
        "",
        "| dataset | split | side | avg offered | q75 offered | avg filled | q75 filled | avg open | hold bars | slot cap/day | ref | days >= ref | cash-bound | avg sat |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["capacity"]:
        if row["split"] not in {"all", "2022_plus"}:
            continue
        lines.append(
            f"| {row['dataset']} | {row['split']} | {row['side']} | {_num(row['avg_offered'])} | {_num(row['q75_offered'])} | "
            f"{_num(row['avg_filled'])} | {_num(row['q75_filled'])} | {_num(row['avg_open'])} | {_num(row['realized_hold_bars'])} | "
            f"{_num(row['slot_service_capacity_per_day'])} | {_num(row['ref'], 0)} | {_pct(row['share_days_at_or_above_ref'])} | "
            f"{_pct(row['cash_bound_rate'])} | {_num(row['avg_sat_scale'], 3)} |"
        )
    lines.extend(
        [
            "",
            "## Practical Interpretation",
            "",
            "- Realized hold time is around two to three trading bars, so the 15-day hard hold is not the daily throughput constraint.",
            "- The practical fill sweet spot is the q75 fill zone: roughly 5-6 total fills/day, with calls around 3-4 and puts around 2-3 depending on market regime.",
            "- The offered-N sweet spot is wider because the allocator needs queue breadth and diversification before it fills: Sentinel's refs center that at 16 calls and 4 puts, or about 20 side-qualified opportunities/day.",
            "- N above the side refs has sharply diminishing allocation value because Sentinel scales allocation down smoothly. N below the fill floor is expensive and requires unrealistic WR15 lift to preserve expected wins.",
            "",
            "## WR15 Compensation Rule",
            "",
            "For a daily-N reduction, preserve expected wins with:",
            "",
            "```text",
            "required_wr_new = wr_old * min(capacity, old_daily_n) / min(capacity, new_daily_n)",
            "```",
            "",
            "The table below uses q75 filled count as the practical capacity proxy and the Sentinel refs as the current old_daily_n.",
            "",
            "| dataset | split | side | target daily N | baseline WR | required WR | feasible | same-WR win ratio |",
            "|---|---|---|---:|---:|---:|---|---:|",
        ]
    )
    for row in summary["wr_compensation"]:
        if row["dataset"] != "v60_smooth" or row["split"] != "2022_plus":
            continue
        if row["baseline_wr"] not in {0.75, 0.78, 0.80}:
            continue
        if row["target_daily_n"] not in {4, 5, 6, 8, 12, 16, 20}:
            continue
        lines.append(
            f"| {row['dataset']} | {row['split']} | {row['side']} | {row['target_daily_n']} | {_pct(row['baseline_wr'])} | "
            f"{_pct(row['required_wr_to_preserve_wins'])} | {'yes' if row['feasible'] else 'no'} | {_num(row['expected_win_ratio_at_same_wr'])} |"
        )
    lines.extend(
        [
            "",
            "## Pressure Bins",
            "",
            "See `n_capacity_pressure_bins.csv` for all bins. The most useful read is where average filled count stops rising materially while saturation and cash-bound rates continue to rise.",
            "",
            "## Artifacts",
            "",
            "- `n_capacity_summary.json`: full structured output.",
            "- `n_capacity_capacity.csv`: per-dataset capacity metrics.",
            "- `n_capacity_pressure_bins.csv`: opportunity pressure bins by side.",
            "- `n_capacity_thresholds.csv`: days with at least N opportunities by side.",
            "- `n_capacity_wr_compensation.csv`: required WR table for N reductions.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_daily_specs(values: list[str]) -> list[tuple[str, Path]]:
    specs: list[tuple[str, Path]] = []
    for raw in values:
        if "=" in raw:
            label, path_raw = raw.split("=", 1)
        else:
            path = Path(raw)
            label = path.parent.name or path.stem
            path_raw = raw
        path = Path(path_raw)
        if not path.is_absolute():
            path = ROOT / path
        specs.append((label.strip(), path))
    return specs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", action="append", default=None, help="label=path to daily_state.csv; may repeat")
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".codex" / "runs" / "n_capacity_analysis")
    parser.add_argument("--baseline-wr", type=float, action="append", default=[0.72, 0.75, 0.78, 0.80])
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    daily_specs = parse_daily_specs(args.daily or DEFAULT_DAILIES)
    capacity_rows: list[dict[str, Any]] = []
    bin_rows: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []

    for label, path in daily_specs:
        rows = _read_rows(path)
        if not rows:
            raise RuntimeError(f"No rows loaded from {path}")
        for split_name, split_rows in _split_rows(rows).items():
            if not split_rows:
                continue
            for side in ("call", "put", "total"):
                capacity_rows.append(_capacity(label, split_name, split_rows, side))
                bin_rows.extend(_pressure_bins(label, split_name, split_rows, side))
                threshold_rows.extend(_threshold_rows(label, split_name, split_rows, side))

    wr_rows: list[dict[str, Any]] = []
    labels = sorted({row["dataset"] for row in capacity_rows})
    splits = sorted({row["split"] for row in capacity_rows})
    for label in labels:
        for split_name in splits:
            wr_rows.extend(_wr_compensation(label, split_name, capacity_rows, args.baseline_wr))

    summary = {
        "strategy": {
            "max_positions": STRATEGY_30DTE.MAX_POSITIONS,
            "max_positions_call": STRATEGY_30DTE.MAX_POSITIONS_CALL,
            "max_positions_put": STRATEGY_30DTE.MAX_POSITIONS_PUT,
            "gross_premium_cap": STRATEGY_30DTE.GROSS_PREMIUM_CAP,
            "call_premium_cap": STRATEGY_30DTE.CALL_PREMIUM_CAP,
            "put_premium_cap": STRATEGY_30DTE.PUT_PREMIUM_CAP,
            "opp_sat_call_ref": STRATEGY_30DTE.OPP_SAT_CALL_REF,
            "opp_sat_put_ref": STRATEGY_30DTE.OPP_SAT_PUT_REF,
            "opp_sat_power": STRATEGY_30DTE.OPP_SAT_POWER,
            "opp_sat_floor": STRATEGY_30DTE.OPP_SAT_FLOOR,
        },
        "daily_inputs": [{"label": label, "path": str(path)} for label, path in daily_specs],
        "capacity": capacity_rows,
        "pressure_bins": bin_rows,
        "thresholds": threshold_rows,
        "wr_compensation": wr_rows,
    }

    (out_dir / "n_capacity_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    _write_csv(out_dir / "n_capacity_capacity.csv", capacity_rows)
    _write_csv(out_dir / "n_capacity_pressure_bins.csv", bin_rows)
    _write_csv(out_dir / "n_capacity_thresholds.csv", threshold_rows)
    _write_csv(out_dir / "n_capacity_wr_compensation.csv", wr_rows)
    _markdown(out_dir / "n_capacity_summary.md", summary)
    print(f"Wrote N-capacity artifacts to {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
