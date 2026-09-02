"""Canonize Sentinel/Core/Apex selections from a completed profile sweep."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import portfolio_profiles


PROFILE_META = {
    "sentinel": {
        "name": "Sentinel",
        "risk": "safe",
        "color": "green",
        "status": "active",
        "description": "DD-safe current Sentinel portfolio profile on active score rows.",
    },
    "core": {
        "name": "Core",
        "risk": "balanced",
        "color": "yellow",
        "status": "candidate",
        "description": "Balanced compounding profile with moderate drawdown tolerance.",
    },
    "apex": {
        "name": "Apex",
        "risk": "explosive",
        "color": "red",
        "status": "active",
        "description": "Explosive compounding profile using the full portfolio allocation base.",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _float(value: Any) -> float:
    try:
        out = float(value)
        return out if out == out else 0.0
    except (TypeError, ValueError):
        return 0.0


def _candidate_params(row: dict[str, Any]) -> dict[str, Any]:
    params = portfolio_profiles.params_from_sweep_row(row)
    if row.get("candidate") == "sentinel_v1_current" and not params:
        params = portfolio_profiles.load_registry()["profiles"][0]["params"]
    return params


def _distinct_winners(run_dir: Path, phase: str, winners: dict[str, Any]) -> dict[str, Any]:
    core = winners.get("core") or {}
    apex = winners.get("apex") or {}
    if not core.get("candidate") or core.get("candidate") != apex.get("candidate"):
        return winners

    rows = _load_csv(run_dir / f"{phase}_summary.csv")
    alternatives = [
        row for row in rows
        if row.get("candidate") != core.get("candidate")
        and _float(row.get("max_p_coll_pct")) <= 5.0
        and _float(row.get("avg_log_final_delta")) > 0.0
    ]
    if alternatives:
        winners = dict(winners)
        winners["apex"] = max(alternatives, key=lambda r: _float(r.get("apex_score")))
    return winners


def build_registry(run_dir: Path, *, phase: str, base_score_version: str) -> dict[str, Any]:
    winners = _load_json(run_dir / "profile_winners.json")
    winners = _distinct_winners(run_dir, phase, winners)
    done = _load_json(run_dir / "done.json") if (run_dir / "done.json").exists() else {}
    current_profiles = portfolio_profiles.profile_map(ROOT)
    profiles = []
    for key in portfolio_profiles.PROFILE_ORDER:
        selected = winners.get(key) or {}
        candidate = selected.get("candidate") or "sentinel_v1_current"
        meta = PROFILE_META[key]
        current = current_profiles.get(key) or {}
        version = int(current.get("version") or 1)
        if current.get("candidate") and current.get("candidate") != candidate:
            version += 1
        profiles.append(
            {
                "key": key,
                "name": meta["name"],
                "version": version,
                "risk": meta["risk"],
                "color": meta["color"],
                "candidate": candidate,
                "status": meta["status"],
                "description": meta["description"],
                "params": _candidate_params(selected),
                "selection_metrics": {
                    k: selected.get(k)
                    for k in (
                        "core_score",
                        "apex_score",
                        "avg_log_final_delta",
                        "log_5y_delta",
                        "log_2022_now_delta",
                        "avg_worst_dd_worse_pp",
                        "max_worst_dd_worse_pp",
                        "max_candidate_worst_dd_pct",
                        "v60_2025_dip_worse_pp",
                        "max_p_coll_pct",
                    )
                    if k in selected
                },
            }
        )

    return {
        "schema_version": portfolio_profiles.SCHEMA_VERSION,
        "base_score_version": base_score_version,
        "generated_at": utc_now(),
        "evidence": {
            "run_dir": str(run_dir),
            "phase": phase,
            "done_json": str(run_dir / "done.json"),
            "profile_winners_json": str(run_dir / "profile_winners.json"),
            "findings_md": str(run_dir / "findings.md"),
            "n_iter": (done.get("meta") or {}).get(f"{phase}_n"),
            "windows": (done.get("meta") or {}).get(f"{phase}_windows"),
        },
        "profiles": profiles,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--phase", default="phase3")
    parser.add_argument("--base-score-version", default="v60")
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    registry = build_registry(run_dir, phase=args.phase, base_score_version=args.base_score_version)
    path = portfolio_profiles.save_registry(registry, ROOT)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
