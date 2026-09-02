"""Experiment-local ML probe for opportunity/regime interaction discovery.

This is not a production model. It uses a time split and reports feature
importance only to prioritize Stage 3 allocation-wave candidates.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from analyze_daily_state import OPPORTUNITY_COLUMNS, REGIME_COLUMNS


ROOT = Path(__file__).resolve().parent
DEFAULT_DAILY = ROOT / "daily_state.csv"

TARGETS = [
    "next_15d_dd_increase",
    "next_30d_dd_increase",
    "next_15d_worst_equity_return",
    "next_30d_worst_equity_return",
    "entry_avg_pnl_pct",
]


def _float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _read(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _median(values: list[float]) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _r2(y_true, y_pred) -> float:
    y_bar = sum(y_true) / len(y_true)
    ss_tot = sum((y - y_bar) ** 2 for y in y_true)
    ss_res = sum((y - p) ** 2 for y, p in zip(y_true, y_pred))
    return 1.0 - ss_res / ss_tot if ss_tot else 0.0


def _build_matrix(rows: list[dict[str, Any]], target: str, features: list[str], train_cutoff: str):
    raw_values: dict[str, list[float]] = {f: [] for f in features}
    for row in rows:
        for feature in features:
            v = _float(row.get(feature))
            if v is not None:
                raw_values[feature].append(v)
    fills = {feature: _median(values) for feature, values in raw_values.items()}

    x_train = []
    y_train = []
    x_test = []
    y_test = []
    for row in rows:
        y = _float(row.get(target))
        if y is None:
            continue
        x = [_float(row.get(feature)) if _float(row.get(feature)) is not None else fills[feature] for feature in features]
        if row["date"] <= train_cutoff:
            x_train.append(x)
            y_train.append(y)
        else:
            x_test.append(x)
            y_test.append(y)
    return x_train, y_train, x_test, y_test


def _sklearn_probe(rows: list[dict[str, Any]], features: list[str], train_cutoff: str) -> dict[str, Any]:
    from sklearn.ensemble import RandomForestRegressor

    out: dict[str, Any] = {}
    for target in TARGETS:
        x_train, y_train, x_test, y_test = _build_matrix(rows, target, features, train_cutoff)
        if len(x_train) < 100 or len(x_test) < 20:
            out[target] = {"status": "insufficient_rows", "train_n": len(x_train), "test_n": len(x_test)}
            continue
        model = RandomForestRegressor(
            n_estimators=300,
            max_depth=5,
            min_samples_leaf=20,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(x_train, y_train)
        pred_train = model.predict(x_train)
        pred_test = model.predict(x_test)
        importances = sorted(
            zip(features, model.feature_importances_),
            key=lambda item: item[1],
            reverse=True,
        )
        out[target] = {
            "status": "ok",
            "train_n": len(x_train),
            "test_n": len(x_test),
            "train_r2": _r2(y_train, pred_train),
            "test_r2": _r2(y_test, pred_test),
            "top_features": [{"feature": f, "importance": float(v)} for f, v in importances[:25]],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(DEFAULT_DAILY))
    parser.add_argument("--out-dir", default=str(ROOT))
    parser.add_argument("--train-cutoff", default="2024-12-31")
    args = parser.parse_args(argv)

    rows = _read(Path(args.daily))
    if not rows:
        raise RuntimeError(f"No rows loaded from {args.daily}")
    features = [c for c in (OPPORTUNITY_COLUMNS + REGIME_COLUMNS) if c in rows[0]]
    result: dict[str, Any] = {
        "note": "Experiment-local discovery probe only. Use for candidate prioritization, not a ship gate.",
        "train_cutoff": args.train_cutoff,
        "features": features,
    }
    try:
        result["model"] = "RandomForestRegressor"
        result["targets"] = _sklearn_probe(rows, features, args.train_cutoff)
    except Exception as exc:
        result["model"] = "unavailable"
        result["error"] = str(exc)

    out_path = Path(args.out_dir) / "ml_probe.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
