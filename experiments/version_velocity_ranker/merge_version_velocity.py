"""Merge version velocity chunk outputs into one final ranking."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.version_velocity_ranker.run_version_velocity import (  # noqa: E402
    ANNUAL_WINDOWS,
    PORTFOLIO_WINDOWS,
    _atomic_write_json,
    _atomic_write_text,
    _load_window_metrics_csv,
    _report,
    _sort_rankings,
    _summarize_group,
    _write_csv,
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", nargs="+", required=True, help="window_metrics.csv files")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--profiles", default="sentinel")
    ap.add_argument("--versions", default="merged chunks")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    requested_labels = {label for label, _start, _end in ANNUAL_WINDOWS + PORTFOLIO_WINDOWS}

    by_key = {}
    input_paths = [Path(p).resolve() for p in args.inputs]
    for input_path in input_paths:
        for row in _load_window_metrics_csv(input_path):
            key = (row.version_id, row.profile, row.window)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = row
                continue
            if existing.error and not row.error:
                by_key[key] = row
            elif existing.error == row.error:
                by_key[key] = row

    rows = list(by_key.values())
    groups = {}
    for row in rows:
        groups.setdefault((row.version_id, row.profile), []).append(row)

    complete_groups = {}
    dropped = []
    for key, group_rows in groups.items():
        labels = {row.window for row in group_rows}
        errors = [row for row in group_rows if row.error]
        missing = sorted(requested_labels - labels)
        if missing or errors:
            dropped.append({
                "version_id": key[0],
                "profile": key[1],
                "missing_windows": missing,
                "error_windows": [
                    {"window": row.window, "error": row.error}
                    for row in errors
                ],
            })
            continue
        complete_groups[key] = group_rows

    rankings = _sort_rankings([_summarize_group(group_rows) for group_rows in complete_groups.values()])
    windows = ANNUAL_WINDOWS + PORTFOLIO_WINDOWS

    rows_path = run_dir / "window_metrics.csv"
    rankings_path = run_dir / "rankings.csv"
    rankings_json_path = run_dir / "rankings.json"
    dropped_path = run_dir / "dropped_groups.json"
    report_path = run_dir / "REPORT.md"

    _write_csv(rows_path, rows)
    _write_csv(rankings_path, rankings)
    _atomic_write_json(rankings_json_path, {
        "inputs": [str(p) for p in input_paths],
        "rankings": [asdict(row) for row in rankings],
        "dropped_groups": dropped,
    })
    _atomic_write_json(dropped_path, {"dropped_groups": dropped})
    report_args = SimpleNamespace(versions=args.versions, profiles=args.profiles)
    _atomic_write_text(report_path, _report(rankings, windows, report_args))
    _atomic_write_json(run_dir / "done.json", {
        "state": "done",
        "inputs": [str(p) for p in input_paths],
        "rows": len(rows),
        "rankings": len(rankings),
        "dropped_groups": len(dropped),
        "outputs": {
            "window_metrics": str(rows_path),
            "rankings": str(rankings_path),
            "rankings_json": str(rankings_json_path),
            "dropped_groups": str(dropped_path),
            "report": str(report_path),
        },
    })
    print(f"Merged {len(input_paths)} inputs -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
