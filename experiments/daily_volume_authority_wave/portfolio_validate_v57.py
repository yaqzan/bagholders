from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
import monte_carlo as mc
from database.models.core import AlgorithmVersion, Score
from simulator import ScoreSimulator


MC_WINDOW_MAP = {label: (start, end) for label, start, end in mc.WINDOWS}
DET_WINDOW_MAP = {
    "2020-now": (date(2020, 1, 1), date(2026, 4, 24)),
    "2021-now": (date(2021, 1, 1), date(2026, 4, 24)),
    "22-now": (date(2022, 1, 1), date(2026, 4, 24)),
    "dip": (date(2025, 11, 1), date(2026, 4, 24)),
    "5y": (date(2021, 1, 1), date(2026, 4, 15)),
}


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


def _copy_score_row(row) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=row.symbol,
        symbol_id=row.symbol_id,
        date=row.date,
        overall=int(row.overall),
        trend=int(row.trend) if row.trend is not None else None,
        weight_info=row.weight_info,
        volume_signal=row.volume_signal,
        regime_multiplier=row.regime_multiplier,
        stoch=getattr(row, "stoch", None),
    )


def _rich_to_rows(rich: dict[tuple[str, date], tuple]) -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    for (sym, d), (overall, trend, weight_info, volume_signal, regime_mult, sym_id) in rich.items():
        rows.append(
            SimpleNamespace(
                symbol=sym,
                symbol_id=sym_id,
                date=d,
                overall=int(overall),
                trend=int(trend) if trend is not None else None,
                weight_info=json.dumps(weight_info or {}),
                volume_signal=volume_signal,
                regime_multiplier=float(regime_mult),
                stoch=None,
            )
        )
    rows.sort(key=lambda r: (r.date, -r.overall, r.symbol))
    return rows


def _fresh_rows(rows: Iterable[SimpleNamespace], predicate) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(**vars(row))
        for row in rows
        if predicate(row)
    ]


@contextlib.contextmanager
def _inject_mc_rows(rows: list[SimpleNamespace]):
    original_load_signals = mc.load_signals
    original_load_put_signals = mc.load_put_signals

    def patched_load_signals(version, d_start, d_end):
        selected = _fresh_rows(
            rows,
            lambda r: d_start <= r.date <= d_end and int(r.overall) >= mc.OVERFLOW_THRESHOLD,
        )
        selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        return mc._apply_ctsl_to_signals(selected, "call")

    def patched_load_put_signals(version, d_start, d_end):
        if mc.DESIGN_D_PUT_FLIP:
            raise RuntimeError("DVAW validation bridge does not support DESIGN_D_PUT_FLIP=1")
        selected = _fresh_rows(
            rows,
            lambda r: d_start <= r.date <= d_end and int(r.overall) <= mc.PUT_THRESHOLD,
        )
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
def _inject_backtest_rows(rows: list[SimpleNamespace]):
    original_load_signals = bc.load_signals
    original_load_put_signals = bc.load_put_signals

    def patched_load_signals(version_id, min_score, from_date=None, to_date=None, flagged_only=False):
        start = from_date or bc.MIN_DATE
        end = to_date or date.max
        selected = _fresh_rows(
            rows,
            lambda r: start <= r.date <= end and int(r.overall) >= min_score,
        )
        selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        if bc.CTSL_ENABLED:
            selected = bc._apply_ctsl_to_signals(selected, "call")
            selected.sort(key=lambda r: (r.date, -int(r.overall), r.symbol))
        return selected

    def patched_load_put_signals(version_id, max_put_score=None, from_date=None, to_date=None, flagged_only=False):
        threshold = max_put_score if max_put_score is not None else bc.PUT_THRESHOLD
        start = from_date or bc.MIN_DATE
        end = to_date or date.max
        selected = _fresh_rows(
            rows,
            lambda r: start <= r.date <= end and int(r.overall) <= threshold,
        )
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


def _score_delta_summary(base: dict[tuple[str, date], tuple], cand: dict[tuple[str, date], tuple]) -> dict[str, Any]:
    keys = sorted(set(base) & set(cand))
    changed = []
    changed_90 = 0
    for key in keys:
        old = int(base[key][0])
        new = int(cand[key][0])
        if old == new:
            continue
        delta = new - old
        changed.append(delta)
        if old >= 90 or new >= 90:
            changed_90 += 1
    return {
        "overlap": len(keys),
        "changed": len(changed),
        "lifted": sum(1 for d in changed if d > 0),
        "dampened": sum(1 for d in changed if d < 0),
        "min_delta": min(changed) if changed else None,
        "max_delta": max(changed) if changed else None,
        "mean_delta_changed": statistics.mean(changed) if changed else None,
        "changed_90_plus": changed_90,
    }


def _db_drift_summary(version: AlgorithmVersion, sim_base: dict[tuple[str, date], tuple], start: date, end: date) -> dict[str, Any]:
    rows = (
        Score.select(Score.symbol, Score.date, Score.overall)
        .where(Score.version == version, Score.date >= start, Score.date <= end)
        .tuples()
    )
    db = {(sym, d): int(overall) for sym, d, overall in rows}
    deltas = []
    for key, sim in sim_base.items():
        if key in db:
            deltas.append(int(sim[0]) - db[key])
    changed = [d for d in deltas if d != 0]
    return {
        "db_rows": len(db),
        "sim_rows": len(sim_base),
        "overlap": len(deltas),
        "changed": len(changed),
        "changed_pct": None if not deltas else 100.0 * len(changed) / len(deltas),
        "mean_delta": statistics.mean(deltas) if deltas else None,
        "mean_abs_delta": statistics.mean([abs(d) for d in deltas]) if deltas else None,
        "min_delta": min(changed) if changed else None,
        "max_delta": max(changed) if changed else None,
    }


def _run_mc_group(name: str, rows: list[SimpleNamespace] | None, version: AlgorithmVersion,
                  windows: list[tuple[str, date, date]], n_iter: int) -> dict[str, Any]:
    mc.N_ITER = n_iter
    os.environ["MC_NO_DB_PERSIST"] = "1"
    results: dict[str, Any] = {}
    cm = _inject_mc_rows(rows) if rows is not None else contextlib.nullcontext()
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


def _run_backtest_group(name: str, rows: list[SimpleNamespace] | None, version: AlgorithmVersion,
                        windows: list[tuple[str, date, date]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    cm = _inject_backtest_rows(rows) if rows is not None else contextlib.nullcontext()
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="v57")
    parser.add_argument("--lookback-days", type=int, default=2400)
    parser.add_argument("--mc-windows", default="2022,22-now,5y,dip")
    parser.add_argument("--det-windows", default="2020-now,22-now,dip")
    parser.add_argument("--n-iter", type=int, default=300)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--skip-db-mc", action="store_true")
    parser.add_argument("--skip-db-backtest", action="store_true")
    args = parser.parse_args()

    started_at = datetime.now().isoformat(timespec="seconds")
    mc_windows = _parse_windows(args.mc_windows, MC_WINDOW_MAP)
    det_windows = _parse_windows(args.det_windows, DET_WINDOW_MAP)
    earliest = min([w[1] for w in mc_windows + det_windows])
    latest = max([w[2] for w in mc_windows + det_windows])
    version = _resolve_version(args.version)

    _status(args.status, state="running", phase="setup", started_at=started_at, version_id=version.id)

    # Staging scoring must be equivalent to v57 direct Market Wave; copy/symlink
    # the cache before running this script if this assertion fails.
    market_wave_path = ROOT / ".cache" / "market_wave" / "predictive_market_wave_v57_source.csv"
    if not market_wave_path.exists():
        raise SystemExit(f"Missing required Market Wave cache: {market_wave_path}")

    import database.utils.scoring as scoring

    result: dict[str, Any] = {
        "started_at": started_at,
        "version": {"id": version.id, "git_commit": version.git_commit, "git_message": version.git_message},
        "lookback_days": args.lookback_days,
        "mc_windows": [label for label, _, _ in mc_windows],
        "det_windows": [label for label, _, _ in det_windows],
        "n_iter": args.n_iter,
        "outputs": {},
    }

    _status(args.status, state="running", phase="load_simulator")
    sim = ScoreSimulator(lookback_days=args.lookback_days)

    _status(args.status, state="running", phase="simulate_baseline", completed=0, total=2)
    scoring.DAILY_VOLUME_AUTHORITY_WAVE_ENABLED = False
    rich_base = sim.simulate_full(since=earliest)

    _status(args.status, state="running", phase="simulate_candidate", completed=1, total=2)
    scoring.DAILY_VOLUME_AUTHORITY_WAVE_ENABLED = True
    rich_candidate = sim.simulate_full(since=earliest)

    base_rows = _rich_to_rows(rich_base)
    candidate_rows = _rich_to_rows(rich_candidate)
    result["score_delta"] = _score_delta_summary(rich_base, rich_candidate)
    result["db_vs_sim_baseline"] = _db_drift_summary(version, rich_base, earliest, latest)
    _write_json(args.out, result)

    _status(args.status, state="running", phase="mc_sim_baseline", output=str(args.out))
    result["outputs"]["mc_sim_baseline"] = _run_mc_group("sim_baseline", base_rows, version, mc_windows, args.n_iter)
    _write_json(args.out, result)

    _status(args.status, state="running", phase="mc_candidate")
    result["outputs"]["mc_candidate"] = _run_mc_group("candidate", candidate_rows, version, mc_windows, args.n_iter)
    result["outputs"]["mc_candidate_vs_sim_baseline"] = _diff_group(
        result["outputs"]["mc_candidate"],
        result["outputs"]["mc_sim_baseline"],
        ("mean_ret", "med_ret", "mean_dd", "worst_dd", "p_coll", "call_tp", "put_tp", "call_trades", "put_trades"),
    )
    _write_json(args.out, result)

    if not args.skip_db_mc:
        _status(args.status, state="running", phase="mc_db_v57")
        result["outputs"]["mc_db_v57"] = _run_mc_group("db_v57", None, version, mc_windows, args.n_iter)
        _write_json(args.out, result)

    _status(args.status, state="running", phase="backtest_sim_baseline")
    result["outputs"]["backtest_sim_baseline"] = _run_backtest_group("sim_baseline", base_rows, version, det_windows)
    _write_json(args.out, result)

    _status(args.status, state="running", phase="backtest_candidate")
    result["outputs"]["backtest_candidate"] = _run_backtest_group("candidate", candidate_rows, version, det_windows)
    result["outputs"]["backtest_candidate_vs_sim_baseline"] = _diff_group(
        result["outputs"]["backtest_candidate"],
        result["outputs"]["backtest_sim_baseline"],
        ("total_return_pct", "max_dd", "n_trades", "n_call_trades", "n_put_trades"),
    )
    _write_json(args.out, result)

    if not args.skip_db_backtest:
        _status(args.status, state="running", phase="backtest_db_v57")
        result["outputs"]["backtest_db_v57"] = _run_backtest_group("db_v57", None, version, det_windows)
        _write_json(args.out, result)

    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(args.out, result)
    _status(args.status, state="complete", phase="complete", output=str(args.out), finished_at=result["finished_at"])
    print(json.dumps(result, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
