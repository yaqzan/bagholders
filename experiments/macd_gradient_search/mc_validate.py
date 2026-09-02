"""Monte Carlo validation for guarded MACD gradient sweep survivors."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.macd_gradient_search.sweep import (
    Variant,
    _build_features,
    _candidate_score,
    _logging,
    _now,
    _parse_date,
    _status,
    _write_json,
)
from experiments.sim_mc_bridge import run_windows


def _variant_from_result(result: dict[str, Any]) -> Variant:
    return Variant(**result["variant"])


def _load_candidates(sweep_run: Path, top_n: int, labels: list[str] | None = None) -> list[dict[str, Any]]:
    ranked_path = sweep_run / "sweep_ranked.json"
    data = json.loads(ranked_path.read_text(encoding="utf-8"))
    by_label = {r["label"]: r for r in data["ranked"]}
    baseline = next(r for r in data["ranked"] if r["label"] == "baseline_cliff")
    if labels:
        missing = [label for label in labels if label not in by_label]
        if missing:
            raise ValueError(f"candidate labels not found in sweep results: {missing}")
        return [baseline] + [by_label[label] for label in labels]
    survivors = [r for r in data["ranked"] if r.get("passes_guardrails") and r["label"] != "baseline_cliff"]
    return [baseline] + survivors[:top_n]


def _rich_from_features(features, variant: Variant) -> dict[tuple[str, date], tuple]:
    rich = {}
    for f in features:
        score = _candidate_score(f, variant)
        wi = dict(f.weight_info or {})
        if score != f.base:
            wi["macd_gradient"] = {
                "label": variant.label,
                "base": int(f.base),
                "full": int(f.full),
                "score": int(score),
                "pre_no_macd": round(float(f.pre_no_macd), 3),
                "macd": round(float(f.macd), 3),
            }
        rich[(f.symbol, f.d)] = (
            int(score),
            int(f.trend),
            wi,
            f.vol_signal,
            float(f.regime_mult),
            f.symbol,
        )
    return rich


def _window_defs(end: date) -> list[tuple[str, date, date]]:
    return [
        ("covid_crash", date(2020, 2, 18), date(2020, 6, 30)),
        ("2020_now", date(2020, 1, 1), end),
        ("22_now", date(2022, 1, 1), end),
    ]


def _deltas(results: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for window, metrics in results.items():
        b = baseline.get(window, {})
        out[window] = {}
        for key in ("mean_ret", "med_ret", "mean_dd", "worst_dd", "p_coll", "call_tp", "put_tp", "call_trades", "put_trades"):
            rv = metrics.get(key)
            bv = b.get(key)
            out[window][key + "_delta"] = None if rv is None or bv is None else rv - bv
    return out


def run(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    sweep_run = Path(args.sweep_run).resolve()
    end = _parse_date(args.end, date.today())
    start = _parse_date(args.start, date(2020, 1, 1))
    labels = [label.strip() for label in args.labels.split(",") if label.strip()] if args.labels else None
    candidates = _load_candidates(sweep_run, args.top_n, labels=labels)
    _write_json(run_dir / "launch.json", {
        "started_at": _now(),
        "pid": os.getpid(),
        "cwd": str(ROOT),
        "argv": sys.argv,
        "sweep_run": str(sweep_run),
        "top_n": args.top_n,
        "n_iter": args.n_iter,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "candidate_labels": [c["label"] for c in candidates],
        "env": {
            "HOLDOUT_DISABLE": os.environ.get("HOLDOUT_DISABLE"),
            "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING"),
            "PYTHONUTF8": os.environ.get("PYTHONUTF8"),
        },
    })
    (run_dir / "pid.txt").write_text(str(os.getpid()), encoding="utf-8")
    _status(run_dir, state="running", phase="build_features", started_at=_now())
    print(f"[launch] run_dir={run_dir}", flush=True)
    print(f"[launch] sweep_run={sweep_run}", flush=True)
    print(f"[launch] candidates={[c['label'] for c in candidates]}", flush=True)
    print(f"[launch] start={start} end={end} n_iter={args.n_iter}", flush=True)

    features = _build_features(run_dir, start, end)
    windows = _window_defs(end)
    results: dict[str, Any] = {}
    baseline_results: dict[str, Any] | None = None

    for idx, candidate in enumerate(candidates, start=1):
        label = candidate["label"]
        variant = _variant_from_result(candidate)
        _status(run_dir, state="running", phase="mc", completed=idx - 1, total=len(candidates), current=label)
        print(f"\n[mc {idx}/{len(candidates)}] {label}", flush=True)
        t0 = time.time()
        rich = _rich_from_features(features, variant)
        window_results = run_windows(windows, rich, n_iter=args.n_iter)
        if label == "baseline_cliff":
            baseline_results = window_results
        if baseline_results is None:
            raise RuntimeError("baseline_cliff must run before candidates")
        results[label] = {
            "label": label,
            "variant": candidate["variant"],
            "sweep_objective": candidate.get("objective"),
            "sweep_bucket_deltas": candidate.get("bucket_deltas"),
            "sweep_score_changes": candidate.get("score_changes"),
            "elapsed_s": time.time() - t0,
            "windows": window_results,
            "deltas_vs_baseline": _deltas(window_results, baseline_results),
        }
        _write_json(run_dir / "mc_results.json", {
            "results": results,
            "baseline_label": "baseline_cliff",
            "windows": [(label, s.isoformat(), e.isoformat()) for label, s, e in windows],
            "updated_at": _now(),
        })

    _write_json(run_dir / "done.json", {
        "finished_at": _now(),
        "state": "done",
        "candidate_count": len(candidates),
        "mc_results": str(run_dir / "mc_results.json"),
    })
    _status(run_dir, state="done", phase="done", completed=len(candidates), total=len(candidates))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--sweep-run", required=True)
    parser.add_argument("--top-n", type=int, default=6)
    parser.add_argument("--labels")
    parser.add_argument("--n-iter", type=int, default=300)
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    with _logging(run_dir):
        try:
            run(args)
            return 0
        except Exception as exc:
            print("[failed]", exc, flush=True)
            traceback.print_exc()
            _write_json(run_dir / "failed.json", {
                "finished_at": _now(),
                "state": "failed",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            _status(run_dir, state="failed", phase="failed", error=str(exc))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
