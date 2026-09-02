"""Validate Sentinel-adjacent N-capacity risk profiles in Monte Carlo.

This runner reuses the legacy combined validation engine but supplies a focused
candidate grid around daily opportunity saturation refs:

- lower refs / caps: higher WR-quality tolerance, less crowded exposure;
- Sentinel: current practical exposure profile;
- higher refs / caps: more breadth and compounding velocity.

It is experiment-only. It does not write score rows or change strategy config.
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiments.daily_opportunity_allocation import validate_portfolio_combo_mc as combo


ROOT = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def candidate_pool() -> list[combo.ComboCandidate]:
    base = {
        "capital_ceiling": 25_000_000,
        "max_positions": 14,
        "call_max": 12,
    }
    return [
        combo.ComboCandidate("baseline"),
        combo.ComboCandidate(
            "n12_3_defensive_g70_c58_p18_floor50",
            **base,
            gross_cap=0.70,
            call_cap=0.58,
            put_cap=0.18,
            call_ref=12,
            put_ref=3,
            sat_power=0.50,
            sat_floor=0.50,
            put_max=8,
        ),
        combo.ComboCandidate(
            "n12_4_quality_g80_c65_p25_floor58",
            **base,
            gross_cap=0.80,
            call_cap=0.65,
            put_cap=0.25,
            call_ref=12,
            put_ref=4,
            sat_power=0.50,
            sat_floor=0.58,
            put_max=8,
        ),
        combo.ComboCandidate(
            "n14_3_balanced_g75_c62_p20_floor52",
            **base,
            gross_cap=0.75,
            call_cap=0.62,
            put_cap=0.20,
            call_ref=14,
            put_ref=3,
            sat_power=0.50,
            sat_floor=0.52,
            put_max=8,
        ),
        combo.ComboCandidate(
            "n16_4_shipped_g80_c65_p25_floor55",
            **base,
            gross_cap=0.80,
            call_cap=0.65,
            put_cap=0.25,
            call_ref=16,
            put_ref=4,
            sat_power=0.50,
            sat_floor=0.55,
            put_max=8,
        ),
        combo.ComboCandidate(
            "n16_3_putthin_g80_c68_p20_floor58",
            **base,
            gross_cap=0.80,
            call_cap=0.68,
            put_cap=0.20,
            call_ref=16,
            put_ref=3,
            sat_power=0.50,
            sat_floor=0.58,
            put_max=7,
        ),
        combo.ComboCandidate(
            "n18_4_growth_g85_c70_p22_floor58",
            **base,
            gross_cap=0.85,
            call_cap=0.70,
            put_cap=0.22,
            call_ref=18,
            put_ref=4,
            sat_power=0.50,
            sat_floor=0.58,
            put_max=8,
        ),
        combo.ComboCandidate(
            "n20_4_growth_g90_c72_p20_floor60",
            **base,
            gross_cap=0.90,
            call_cap=0.72,
            put_cap=0.20,
            call_ref=20,
            put_ref=4,
            sat_power=0.50,
            sat_floor=0.60,
            put_max=8,
        ),
        combo.ComboCandidate(
            "n20_3_growth_putthin_g85_c70_p20_floor58",
            **base,
            gross_cap=0.85,
            call_cap=0.70,
            put_cap=0.20,
            call_ref=20,
            put_ref=3,
            sat_power=0.50,
            sat_floor=0.58,
            put_max=7,
        ),
        combo.ComboCandidate(
            "n24_5_highqueue_g90_c72_p22_floor62",
            **base,
            gross_cap=0.90,
            call_cap=0.72,
            put_cap=0.22,
            call_ref=24,
            put_ref=5,
            sat_power=0.50,
            sat_floor=0.62,
            put_max=8,
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--daily-state", type=Path, default=combo.DEFAULT_DAILY)
    parser.add_argument("--wave-predictions", type=Path, default=combo.DEFAULT_WAVE)
    parser.add_argument("--n", type=int, default=160)
    parser.add_argument("--versions", default="v60")
    parser.add_argument("--windows", default="2020_crash,covid_peak,2020_full,2022,2024,2025,2025_dip,2022_now,5y")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--wealth-cap", type=float, default=5_000_000)
    parser.add_argument("--wealth-floor", type=float, default=2_000_000)
    args = parser.parse_args(argv)

    run_dir = args.out_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    for terminal_name in ("done.json", "failed.json"):
        try:
            (run_dir / terminal_name).unlink()
        except FileNotFoundError:
            pass

    combo.RUN_DIR = run_dir
    combo.STARTED_AT = utc_now()
    combo.RECENT_LINES.clear()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    meta: dict[str, Any] = {
        "phase1_versions": combo.parse_csv_list(args.versions),
        "phase1_windows": combo.parse_csv_list(args.windows),
        "phase1_n": args.n,
        "phase2_versions": [],
        "phase2_windows": [],
        "phase2_n": 0,
        "phase3_versions": [],
        "phase3_windows": [],
        "phase3_n": 0,
        "run_phase2": False,
        "run_phase3": False,
        "versions": combo.parse_csv_list(args.versions),
        "windows": combo.parse_csv_list(args.windows),
        "n": args.n,
        "workers": args.workers,
        "wealth_cap": args.wealth_cap,
        "wealth_floor": args.wealth_floor,
        "daily_state": str(args.daily_state),
        "wave_predictions": str(args.wave_predictions),
        "candidate_count": len(candidate_pool()),
    }

    try:
        combo.note("starting N-capacity Sentinel risk-profile screen")
        combo.write_status(state="starting", phase="init", meta=meta, outputs=combo.output_paths())
        versions = combo.resolve_versions(meta["versions"])
        candidates = candidate_pool()
        _rows, summary = combo.run_phase(
            "phase1",
            candidates,
            versions,
            meta["windows"],
            args.n,
            run_dir / "phase1_results.csv",
            run_dir / "phase1_summary.csv",
            args.wealth_cap,
            args.wealth_floor,
            args.daily_state,
            args.wave_predictions,
            args.workers,
        )
        combo.write_findings({"phase1": summary}, meta)
        done = {
            "state": "done",
            "started_at": combo.STARTED_AT,
            "finished_at": utc_now(),
            "top_candidate": summary[0]["candidate"] if summary else None,
            "outputs": combo.output_paths(),
            "meta": meta,
        }
        combo.atomic_json(run_dir / "done.json", done)
        combo.write_status(**done)
        combo.note(f"done; top_candidate={done['top_candidate']}")
        return 0
    except Exception as exc:
        failed = {
            "state": "failed",
            "started_at": combo.STARTED_AT,
            "finished_at": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "outputs": combo.output_paths(),
            "meta": meta,
        }
        combo.atomic_json(run_dir / "failed.json", failed)
        combo.write_status(**failed)
        combo.note(f"failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
