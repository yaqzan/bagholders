from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
from collections import namedtuple
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ROOT_STR = str(ROOT)
sys.path = [ROOT_STR] + [p for p in sys.path if str(Path(p or ".").resolve()) != ROOT_STR]

import backtest_cascade as bc
import monte_carlo as mc
from database.models.core import AlgorithmVersion, Score
from database.utils.scoring import _apply_daily_volume_authority_wave


MC_WINDOW_MAP = {label: (start, end) for label, start, end in mc.WINDOWS}
DET_WINDOW_MAP = {
    "2020-now": (date(2020, 1, 1), date(2026, 4, 24)),
    "2021-now": (date(2021, 1, 1), date(2026, 4, 24)),
    "22-now": (date(2022, 1, 1), date(2026, 4, 24)),
    "dip": (date(2025, 11, 1), date(2026, 4, 24)),
    "5y": (date(2021, 1, 1), date(2026, 4, 15)),
}

BTScoreRow = namedtuple(
    "BTScoreRow",
    "symbol date overall trend weight_info stoch volume_signal symbol_id",
)


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _status(path: Path | None, **updates: Any) -> None:
    if path is None:
        return
    current: dict[str, Any] = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(updates)
    current["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(path, current)


def _resolve_version(token: str) -> AlgorithmVersion:
    token = token.strip()
    if token.lower().startswith("v") and token[1:].isdigit():
        return AlgorithmVersion.get_by_id(int(token[1:]))
    if token.isdigit():
        return AlgorithmVersion.get_by_id(int(token))
    try:
        return AlgorithmVersion.get(AlgorithmVersion.git_commit == token)
    except AlgorithmVersion.DoesNotExist:
        matches = list(AlgorithmVersion.select().where(AlgorithmVersion.git_commit.startswith(token)))
        if len(matches) != 1:
            raise SystemExit(f"Could not resolve version {token!r}: {len(matches)} matches")
        return matches[0]


def _parse_windows(raw: str, mapping: dict[str, tuple[date, date]]) -> list[tuple[str, date, date]]:
    labels = [x.strip() for x in raw.split(",") if x.strip()]
    windows = []
    for label in labels:
        if label not in mapping:
            raise SystemExit(f"Unknown window {label!r}. Available: {', '.join(mapping)}")
        start, end = mapping[label]
        windows.append((label, start, end))
    return windows


def _row_from_db(score_row, *, source: str) -> dict[str, Any]:
    symbol = str(score_row.symbol)
    return {
        "symbol": symbol,
        "symbol_id": symbol,
        "date": score_row.date,
        "overall": int(score_row.overall),
        "trend": None if score_row.trend is None else int(score_row.trend),
        "weight_info": getattr(score_row, "weight_info", None),
        "stoch": getattr(score_row, "stoch", None),
        "volume_signal": getattr(score_row, "volume_signal", None),
        "source": source,
        "dvaw_delta": 0,
    }


def _load_db_put_rows(version: AlgorithmVersion, start: date, end: date) -> dict[tuple[str, date], dict]:
    rows = (
        Score.select(
            Score.symbol,
            Score.date,
            Score.overall,
            Score.trend,
            Score.weight_info,
            Score.stoch,
            Score.volume_signal,
        )
        .where(
            Score.version == version,
            Score.date >= start,
            Score.date <= end,
            Score.overall <= mc.PUT_THRESHOLD,
        )
        .order_by(Score.date, Score.symbol)
        .namedtuples()
    )
    puts: dict[tuple[str, date], dict] = {}
    for row in rows:
        item = _row_from_db(row, source="db_v57")
        key = (item["symbol"], item["date"])
        puts[key] = item
    return puts


def _clean_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if value != value:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_packet_feature_map(path: Path, start: date, end: date) -> dict[tuple[str, date], dict[str, Any]]:
    df = (
        pl.read_parquet(path)
        .with_columns(pl.col("date").cast(pl.Utf8))
        .filter(pl.col("date") >= start.isoformat())
        .filter(pl.col("date") <= end.isoformat())
        .select(
            [
                "symbol",
                "date",
                "overall",
                "trend",
                "stoch",
                "w_adj",
                "pre_regime",
                "volume_signal",
                "volume_magnitude",
                "pct_from_ema50",
                "wv_force1",
                "price_impulse_sigma",
                "log_dv_expansion",
            ]
        )
    )
    features: dict[tuple[str, date], dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        d = date.fromisoformat(row["date"])
        features[(str(row["symbol"]), d)] = row
    return features


def _packet_high_rows(packet_features: dict[tuple[str, date], dict[str, Any]]) -> dict[tuple[str, date], dict]:
    rows: dict[tuple[str, date], dict] = {}
    for key, feat in packet_features.items():
        symbol, d = key
        wi = {
            "w_adj": feat.get("w_adj"),
            "pre_regime": feat.get("pre_regime"),
            "packet_source": "domain_context_v57_from20200101_min60",
        }
        rows[key] = {
            "symbol": symbol,
            "symbol_id": symbol,
            "date": d,
            "overall": int(feat["overall"]),
            "trend": None if feat.get("trend") is None else int(feat["trend"]),
            "weight_info": json.dumps(wi, separators=(",", ":"), default=str),
            "stoch": feat.get("stoch"),
            "volume_signal": feat.get("volume_signal"),
            "source": "packet_v57",
            "dvaw_delta": 0,
        }
    return rows


def _apply_dvaw_to_db_high_rows(
    db_high_rows: dict[tuple[str, date], dict],
    packet_features: dict[tuple[str, date], dict[str, Any]],
) -> tuple[list[dict], dict[str, Any]]:
    candidate_rows: list[dict] = []
    deltas: list[int] = []
    changed_90_plus = 0
    missing_features = 0

    for key, db_row in db_high_rows.items():
        feat = packet_features.get(key)
        item = dict(db_row)
        if feat is None:
            missing_features += 1
            item["source"] = "db_v57_missing_packet_features"
            candidate_rows.append(item)
            continue

        new_score, meta = _apply_daily_volume_authority_wave(
            int(db_row["overall"]),
            vol_sig=feat.get("volume_signal") or "NEUTRAL",
            vol_mag=_clean_float(feat.get("volume_magnitude")),
            pct_from_ema50=feat.get("pct_from_ema50"),
            wv_force1=feat.get("wv_force1"),
            price_impulse_sigma=None,
            log_dv_expansion=None,
        )
        delta = int(new_score) - int(db_row["overall"])
        deltas.append(delta)
        if delta and (int(db_row["overall"]) >= 90 or int(new_score) >= 90):
            changed_90_plus += 1
        item["overall"] = int(new_score)
        item["source"] = "db_v57_plus_dvaw_packet_features"
        item["dvaw_delta"] = delta
        if meta:
            item["weight_info"] = _merge_weight_info(item.get("weight_info"), {"daily_volume_authority_wave": meta})
        candidate_rows.append(item)

    changed = [d for d in deltas if d != 0]
    return candidate_rows, {
        "source_high_rows": len(db_high_rows),
        "packet_feature_rows": len(packet_features),
        "missing_packet_features_for_source_high": missing_features,
        "changed": len(changed),
        "lifted": sum(1 for d in changed if d > 0),
        "dampened": sum(1 for d in changed if d < 0),
        "min_delta": min(changed) if changed else None,
        "max_delta": max(changed) if changed else None,
        "mean_delta_changed": statistics.mean(changed) if changed else None,
        "changed_90_plus": changed_90_plus,
    }


def _call_select_fields() -> list:
    fields = [Score.symbol, Score.date, Score.overall, Score.trend]
    if mc.WEAK_WEEKLY_CALL_DROP or bc.WEAK_WEEKLY_CALL_DROP:
        fields.extend([Score.weight_info, Score.stoch])
    return fields


def _load_candidate_call_rows(
    version: AlgorithmVersion,
    start: date,
    end: date,
    min_score: float,
    packet_features: dict[tuple[str, date], dict[str, Any]],
) -> list[dict[str, Any]]:
    query = (
        Score.select(*_call_select_fields())
        .where(
            Score.version == version,
            Score.date >= start,
            Score.date <= end,
            Score.overall >= min_score,
        )
        .order_by(Score.date, Score.overall.desc(), Score.symbol)
        .namedtuples()
    )
    rows: list[dict[str, Any]] = []
    for score_row in query:
        item = _row_from_db(score_row, source="db_v57_plus_dvaw_window")
        feat = packet_features.get((item["symbol"], item["date"]))
        if feat is not None:
            new_score, meta = _apply_daily_volume_authority_wave(
                int(item["overall"]),
                vol_sig=feat.get("volume_signal") or "NEUTRAL",
                vol_mag=_clean_float(feat.get("volume_magnitude")),
                pct_from_ema50=feat.get("pct_from_ema50"),
                wv_force1=feat.get("wv_force1"),
                price_impulse_sigma=None,
                log_dv_expansion=None,
            )
            if int(new_score) != int(item["overall"]):
                item["dvaw_delta"] = int(new_score) - int(item["overall"])
                item["overall"] = int(new_score)
                if meta:
                    item["weight_info"] = _merge_weight_info(item.get("weight_info"), {"daily_volume_authority_wave": meta})
        rows.append(item)
    return rows


def _merge_weight_info(raw: Any, patch: dict[str, Any]) -> str:
    if isinstance(raw, str) and raw:
        try:
            base = json.loads(raw)
        except Exception:
            base = {}
    elif isinstance(raw, dict):
        base = dict(raw)
    else:
        base = {}
    base.update(patch)
    return json.dumps(base, separators=(",", ":"), default=str)


def _as_mc_row(row: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=row["symbol"],
        symbol_id=row["symbol_id"],
        date=row["date"],
        overall=int(row["overall"]),
        trend=row.get("trend"),
        weight_info=row.get("weight_info"),
        stoch=row.get("stoch"),
        volume_signal=row.get("volume_signal"),
    )


def _as_bt_row(row: dict[str, Any]) -> BTScoreRow:
    return BTScoreRow(
        row["symbol"],
        row["date"],
        int(row["overall"]),
        row.get("trend"),
        row.get("weight_info"),
        row.get("stoch"),
        row.get("volume_signal"),
        row["symbol_id"],
    )


def _fresh_rows(rows: Iterable[dict], predicate) -> list[dict]:
    return [dict(row) for row in rows if predicate(row)]


@contextlib.contextmanager
def _inject_mc_rows(rows: list[dict]):
    original_load_signals = mc.load_signals
    original_load_put_signals = mc.load_put_signals

    def patched_load_signals(version, d_start, d_end):
        selected = [
            _as_mc_row(row)
            for row in _fresh_rows(
                rows,
                lambda r: d_start <= r["date"] <= d_end and int(r["overall"]) >= mc.OVERFLOW_THRESHOLD,
            )
        ]
        selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        return mc._apply_ctsl_to_signals(selected, "call")

    def patched_load_put_signals(version, d_start, d_end):
        if mc.DESIGN_D_PUT_FLIP:
            raise RuntimeError("DVAW packet bridge does not support DESIGN_D_PUT_FLIP=1")
        selected = [
            _as_mc_row(row)
            for row in _fresh_rows(
                rows,
                lambda r: d_start <= r["date"] <= d_end and int(r["overall"]) <= mc.PUT_THRESHOLD,
            )
        ]
        selected.sort(key=lambda r: (r.date, int(r.overall), r.symbol))
        return mc._apply_ctsl_to_signals(selected, "put")

    mc.load_signals = patched_load_signals
    mc.load_put_signals = patched_load_put_signals
    try:
        yield
    finally:
        mc.load_signals = original_load_signals
        mc.load_put_signals = original_load_put_signals


@contextlib.contextmanager
def _inject_backtest_rows(rows: list[dict]):
    original_load_signals = bc.load_signals
    original_load_put_signals = bc.load_put_signals

    def patched_load_signals(version_id, min_score, from_date=None, to_date=None, flagged_only=False):
        start = from_date or bc.MIN_DATE
        end = to_date or date.max
        selected = [
            _as_bt_row(row)
            for row in _fresh_rows(
                rows,
                lambda r: start <= r["date"] <= end and int(r["overall"]) >= min_score,
            )
        ]
        selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        if bc.CTSL_ENABLED:
            selected = bc._apply_ctsl_to_signals(selected, "call")
            selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        return selected

    def patched_load_put_signals(version_id, max_put_score=None, from_date=None, to_date=None, flagged_only=False):
        threshold = max_put_score if max_put_score is not None else bc.PUT_THRESHOLD
        start = from_date or bc.MIN_DATE
        end = to_date or date.max
        selected = [
            _as_bt_row(row)
            for row in _fresh_rows(
                rows,
                lambda r: start <= r["date"] <= end and int(r["overall"]) <= threshold,
            )
        ]
        selected.sort(key=lambda r: (r.date, int(r.overall), r.symbol))
        if bc.CTSL_ENABLED:
            selected = bc._apply_ctsl_to_signals(selected, "put")
            selected.sort(key=lambda r: (r.date, int(r.overall), r.symbol))
        return selected

    bc.load_signals = patched_load_signals
    bc.load_put_signals = patched_load_put_signals
    try:
        yield
    finally:
        bc.load_signals = original_load_signals
        bc.load_put_signals = original_load_put_signals


@contextlib.contextmanager
def _patch_mc_candidate_calls(packet_features: dict[tuple[str, date], dict[str, Any]]):
    original_load_signals = mc.load_signals
    cache: dict[tuple[int, date, date, float], list[dict[str, Any]]] = {}

    def patched_load_signals(version, d_start, d_end):
        key = (version.id, d_start, d_end, float(mc.OVERFLOW_THRESHOLD))
        if key not in cache:
            cache[key] = _load_candidate_call_rows(
                version,
                d_start,
                d_end,
                float(mc.OVERFLOW_THRESHOLD),
                packet_features,
            )
        selected = [_as_mc_row(row) for row in cache[key]]
        selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        return mc._apply_ctsl_to_signals(selected, "call")

    mc.load_signals = patched_load_signals
    try:
        yield
    finally:
        mc.load_signals = original_load_signals


@contextlib.contextmanager
def _patch_backtest_candidate_calls(packet_features: dict[tuple[str, date], dict[str, Any]]):
    original_load_signals = bc.load_signals
    cache: dict[tuple[int, date, date, float], list[dict[str, Any]]] = {}

    def patched_load_signals(version_id, min_score, from_date=None, to_date=None, flagged_only=False):
        if flagged_only:
            raise RuntimeError("DVAW packet bridge does not support flagged_only candidate validation")
        start = from_date or bc.MIN_DATE
        end = to_date or date.max
        version = AlgorithmVersion.get_by_id(version_id)
        key = (version_id, start, end, float(min_score))
        if key not in cache:
            cache[key] = _load_candidate_call_rows(version, start, end, float(min_score), packet_features)
        selected = [_as_bt_row(row) for row in cache[key]]
        selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        if bc.CTSL_ENABLED:
            selected = bc._apply_ctsl_to_signals(selected, "call")
            selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        return selected

    bc.load_signals = patched_load_signals
    try:
        yield
    finally:
        bc.load_signals = original_load_signals


def _run_mc_group(
    name: str,
    version: AlgorithmVersion,
    windows: list[tuple[str, date, date]],
    n_iter: int,
    packet_features: dict[tuple[str, date], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    mc.N_ITER = n_iter
    os.environ["MC_NO_DB_PERSIST"] = "1"
    results: dict[str, Any] = {}
    cm = _patch_mc_candidate_calls(packet_features) if packet_features is not None else contextlib.nullcontext()
    with cm:
        for label, start, end in windows:
            print(f"\n[mc:{name}] {label} {start} -> {end}", flush=True)
            raw = mc.run_window(label, start, end, version)["seeded"]
            results[label] = {
                "mean_ret": raw["mean_ret"],
                "med_ret": raw["med_ret"],
                "mean_dd": raw["mean_dd"],
                "worst_dd": raw["worst_dd"],
                "p_coll": raw["p_coll"],
                "call_tp": raw["call_tp"],
                "put_tp": raw["put_tp"],
                "call_trades": raw["call_trades"],
                "put_trades": raw["put_trades"],
            }
    return results


def _run_backtest_group(
    name: str,
    version: AlgorithmVersion,
    windows: list[tuple[str, date, date]],
    packet_features: dict[tuple[str, date], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    cm = _patch_backtest_candidate_calls(packet_features) if packet_features is not None else contextlib.nullcontext()
    with cm:
        for label, start, end in windows:
            print(f"\n[bt:{name}] {label} {start} -> {end}", flush=True)
            result = bc.run_cascade_backtest(
                version.id,
                min_score=70.0,
                from_date=start,
                to_date=end,
                initial=50_000.0,
                verbose=False,
            )
            if not result:
                results[label] = {"empty": True}
                continue
            trade_log = result["trade_log"]
            equity_curve = result["equity_curve"]
            final_eq = equity_curve[-1][1] if equity_curve else 50_000.0
            n_call = sum(1 for t in trade_log if t.get("side", "call") == "call")
            n_put = sum(1 for t in trade_log if t.get("side") == "put")
            results[label] = {
                "final_equity": round(final_eq, 2),
                "total_return_pct": round((final_eq / 50_000.0 - 1.0) * 100, 2),
                "max_dd": round(result["max_dd"] * 100, 2),
                "n_trades": len(trade_log),
                "n_call_trades": n_call,
                "n_put_trades": n_put,
                "start_date": str(result.get("start_date")),
                "end_date": str(result.get("end_date")),
            }
    return results


def _diff_group(candidate: dict[str, Any], baseline: dict[str, Any], metrics: tuple[str, ...]) -> dict[str, Any]:
    out = {}
    for label, cand in candidate.items():
        base = baseline.get(label)
        if not base or cand.get("empty") or base.get("empty"):
            continue
        out[label] = {f"{m}_delta": cand.get(m) - base.get(m) for m in metrics}
    return out


def _count_summary(rows: list[dict], windows: list[tuple[str, date, date]]) -> dict[str, Any]:
    out = {}
    for label, start, end in windows:
        in_window = [r for r in rows if start <= r["date"] <= end]
        out[label] = {
            "calls_70_plus": sum(1 for r in in_window if int(r["overall"]) >= 70),
            "calls_75_plus": sum(1 for r in in_window if int(r["overall"]) >= 75),
            "puts_25_minus": sum(1 for r in in_window if int(r["overall"]) <= mc.PUT_THRESHOLD),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="v57")
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--mc-windows", default="2022,22-now,5y,dip")
    parser.add_argument("--det-windows", default="2020-now,22-now,dip")
    parser.add_argument("--n-iter", type=int, default=300)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()

    started_at = datetime.now().isoformat(timespec="seconds")
    version = _resolve_version(args.version)
    mc_windows = _parse_windows(args.mc_windows, MC_WINDOW_MAP)
    det_windows = _parse_windows(args.det_windows, DET_WINDOW_MAP)
    earliest = min([w[1] for w in mc_windows + det_windows])
    latest = max([w[2] for w in mc_windows + det_windows])

    _status(args.status, state="running", phase="load_packet", started_at=started_at, version_id=version.id)
    packet_features = _load_packet_feature_map(args.packet, earliest, latest)
    packet_high_rows = _packet_high_rows(packet_features)
    _status(args.status, state="running", phase="score_packet_preview", version_id=version.id, packet_high_rows=len(packet_high_rows))
    packet_candidate_rows, score_delta = _apply_dvaw_to_db_high_rows(packet_high_rows, packet_features)

    result: dict[str, Any] = {
        "started_at": started_at,
        "version": {"id": version.id, "git_commit": version.git_commit, "git_message": version.git_message},
        "packet": str(args.packet),
        "mc_windows": [label for label, _, _ in mc_windows],
        "det_windows": [label for label, _, _ in det_windows],
        "n_iter": args.n_iter,
        "bridge_quality": score_delta,
        "packet_call_domain_preview": {
            "baseline": _count_summary(
                [dict(r) for r in packet_high_rows.values() if int(r["overall"]) >= 70],
                list({w[0]: w for w in mc_windows + det_windows}.values()),
            ),
            "candidate": _count_summary(
                [r for r in packet_candidate_rows if int(r["overall"]) >= 70],
                list({w[0]: w for w in mc_windows + det_windows}.values()),
            ),
        },
        "outputs": {},
    }
    _write_json(args.out, result)

    _status(args.status, state="running", phase="mc_baseline", output=str(args.out), completed=0, total=4)
    result["outputs"]["mc_baseline"] = _run_mc_group("baseline", version, mc_windows, args.n_iter)
    _write_json(args.out, result)

    _status(args.status, state="running", phase="mc_candidate", completed=1, total=4)
    result["outputs"]["mc_candidate"] = _run_mc_group("candidate", version, mc_windows, args.n_iter, packet_features)
    result["outputs"]["mc_candidate_vs_baseline"] = _diff_group(
        result["outputs"]["mc_candidate"],
        result["outputs"]["mc_baseline"],
        ("mean_ret", "med_ret", "mean_dd", "worst_dd", "p_coll", "call_tp", "put_tp", "call_trades", "put_trades"),
    )
    _write_json(args.out, result)

    _status(args.status, state="running", phase="backtest_baseline", completed=2, total=4)
    result["outputs"]["backtest_baseline"] = _run_backtest_group("baseline", version, det_windows)
    _write_json(args.out, result)

    _status(args.status, state="running", phase="backtest_candidate", completed=3, total=4)
    result["outputs"]["backtest_candidate"] = _run_backtest_group("candidate", version, det_windows, packet_features)
    result["outputs"]["backtest_candidate_vs_baseline"] = _diff_group(
        result["outputs"]["backtest_candidate"],
        result["outputs"]["backtest_baseline"],
        ("total_return_pct", "max_dd", "n_trades", "n_call_trades", "n_put_trades"),
    )
    _write_json(args.out, result)

    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(args.out, result)
    _status(args.status, state="complete", phase="complete", completed=4, total=4, output=str(args.out), finished_at=result["finished_at"])
    print(json.dumps(result, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
