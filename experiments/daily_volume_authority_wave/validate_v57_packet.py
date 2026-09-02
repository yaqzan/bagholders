from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from database.utils.scoring import _apply_daily_volume_authority_wave
from strategy_config import CALIBRATION_CUTOFF_DATE


CALL_TIERS = (75, 80, 85, 90)


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _status(path: Path | None, **updates: Any) -> None:
    if path is None:
        return
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(updates)
    existing["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(path, existing)


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _load_packet(path: Path, *, validation: bool) -> pl.DataFrame:
    df = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Utf8))
    df = df.filter(pl.col("date") <= CALIBRATION_CUTOFF_DATE)
    if validation:
        df = df.filter(pl.col("date") < "2020-01-01")
    return df.with_columns(
        [
            pl.col("volume_signal").fill_null("NEUTRAL"),
            pl.col("volume_magnitude").fill_null(0.0),
            pl.col("pct_from_ema50").fill_null(0.0),
            pl.col("wv_force1").cast(pl.Float64),
            pl.col("price_impulse_sigma").fill_null(0.0),
            pl.col("log_dv_expansion").fill_null(0.0),
            pl.col("win_15d").cast(pl.Float64),
            pl.col("opt_result_15").cast(pl.Float64),
        ]
    )


def _score_rows(df: pl.DataFrame, *, packet_extras: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in df.iter_rows(named=True):
        new_score, meta = _apply_daily_volume_authority_wave(
            int(r["overall"]),
            vol_sig=r.get("volume_signal") or "NEUTRAL",
            vol_mag=float(r.get("volume_magnitude") or 0.0),
            pct_from_ema50=r.get("pct_from_ema50"),
            wv_force1=r.get("wv_force1"),
            price_impulse_sigma=(r.get("price_impulse_sigma") if packet_extras else None),
            log_dv_expansion=(r.get("log_dv_expansion") if packet_extras else None),
        )
        row = {
            "symbol": r["symbol"],
            "date": r["date"],
            "date_ord": datetime.fromisoformat(r["date"]).date().toordinal(),
            "overall": int(r["overall"]),
            "new_score": int(new_score),
            "win_15d": _clean_value(r.get("win_15d")),
            "opt_result_15": _clean_value(r.get("opt_result_15")),
            "dvaw_delta": int(new_score) - int(r["overall"]),
            "dvaw_meta": meta,
        }
        rows.append(row)
    return rows


def _baseline_rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in df.iter_rows(named=True):
        score = int(r["overall"])
        rows.append(
            {
                "symbol": r["symbol"],
                "date": r["date"],
                "date_ord": datetime.fromisoformat(r["date"]).date().toordinal(),
                "overall": score,
                "new_score": score,
                "win_15d": _clean_value(r.get("win_15d")),
                "opt_result_15": _clean_value(r.get("opt_result_15")),
                "dvaw_delta": 0,
            }
        )
    return rows


def _extract_peaks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["new_score"] >= 70:
            by_symbol[row["symbol"]].append(row)

    selected: list[dict[str, Any]] = []
    for sym_rows in by_symbol.values():
        date_set = {row["date_ord"] for row in sym_rows}
        pruned: set[int] = set()
        for row in sorted(sym_rows, key=lambda x: x["new_score"] - 50, reverse=True):
            ord_date = row["date_ord"]
            if ord_date in pruned:
                continue
            selected.append(row)
            for direction in (-1, 1):
                walk = ord_date + direction
                while walk in date_set and walk not in pruned:
                    pruned.add(walk)
                    walk += direction
    return selected


def _rate(values: list[Any]) -> tuple[float | None, int]:
    resolved = [v for v in values if v is not None]
    if not resolved:
        return None, 0
    return 100.0 * sum(1 for v in resolved if int(v) == 1) / len(resolved), len(resolved)


def _wins(rate: float | None, n: int) -> float:
    return 0.0 if rate is None else rate * n / 100.0


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    peaks = _extract_peaks(rows)
    tiers = {}
    for tier in CALL_TIERS:
        sub = [r for r in peaks if r["new_score"] >= tier]
        wr15, n15 = _rate([r["win_15d"] for r in sub])
        opt15, nopt = _rate([r["opt_result_15"] for r in sub])
        tiers[f"{tier}+"] = {
            "n": len(sub),
            "wr15": wr15,
            "wr15_n": n15,
            "wr15_wins": _wins(wr15, n15),
            "opt15": opt15,
            "opt15_n": nopt,
        }
    return {"peaks": len(peaks), "tiers": tiers}


def _deltas(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for tier in (f"{t}+" for t in CALL_TIERS):
        c = candidate["tiers"][tier]
        b = baseline["tiers"][tier]
        c_wr = c["wr15"]
        b_wr = b["wr15"]
        c_opt = c["opt15"]
        b_opt = b["opt15"]
        base_n = b["n"]
        out[tier] = {
            "n_delta": c["n"] - b["n"],
            "n_delta_pct": None if base_n == 0 else 100.0 * (c["n"] - base_n) / base_n,
            "wr15_delta_pp": None if c_wr is None or b_wr is None else c_wr - b_wr,
            "wr15_wins_delta": c["wr15_wins"] - b["wr15_wins"],
            "opt15_delta_pp": None if c_opt is None or b_opt is None else c_opt - b_opt,
        }
    return out


def _score_change_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [r["dvaw_delta"] for r in rows]
    changed = [d for d in deltas if d != 0]
    high_changed = [
        r for r in rows
        if r["overall"] >= 90 and r["new_score"] != r["overall"]
    ]
    return {
        "rows": len(rows),
        "changed": len(changed),
        "lifted": sum(1 for d in changed if d > 0),
        "dampened": sum(1 for d in changed if d < 0),
        "mean_delta_changed": None if not changed else sum(changed) / len(changed),
        "min_delta": None if not changed else min(changed),
        "max_delta": None if not changed else max(changed),
        "changed_90_plus": len(high_changed),
    }


def validate_dataset(name: str, path: Path, *, validation: bool) -> dict[str, Any]:
    df = _load_packet(path, validation=validation)
    baseline_rows = _baseline_rows(df)
    baseline = _metrics(baseline_rows)
    modes = {}
    for mode, packet_extras in (
        ("production_available_inputs", False),
        ("packet_full_features", True),
    ):
        rows = _score_rows(df, packet_extras=packet_extras)
        metrics = _metrics(rows)
        modes[mode] = {
            "metrics": metrics,
            "deltas": _deltas(metrics, baseline),
            "score_changes": _score_change_summary(rows),
        }
    return {
        "name": name,
        "path": str(path),
        "rows": df.height,
        "baseline": baseline,
        "modes": modes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-packet", required=True, type=Path)
    parser.add_argument("--validation-packet", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()

    started_at = datetime.now().isoformat(timespec="seconds")
    _status(args.status, phase="start", state="running", started_at=started_at)

    result = {
        "started_at": started_at,
        "cutoff": CALIBRATION_CUTOFF_DATE,
        "datasets": {},
    }
    for idx, (name, path, validation) in enumerate(
        (
            ("full_2020_now", args.full_packet, False),
            ("validation_pre2020", args.validation_packet, True),
        ),
        start=1,
    ):
        _status(
            args.status,
            phase=f"dataset:{name}",
            state="running",
            completed=idx - 1,
            total=2,
            current_packet=str(path),
        )
        result["datasets"][name] = validate_dataset(name, path, validation=validation)

    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(args.out, result)
    _status(
        args.status,
        phase="complete",
        state="complete",
        completed=2,
        total=2,
        output=str(args.out),
        finished_at=result["finished_at"],
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
