from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.temporal_echo import analyze_transitions, build_features, sweep

RUN_ROOT = ROOT / ".cache" / "temporal_echo" / "runs"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


class Status:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.started_at = utc_now()

    def update(self, phase: str, state: str, **extra) -> None:
        payload = {
            "phase": phase,
            "state": state,
            "started_at": self.started_at,
            "updated_at": utc_now(),
            **extra,
        }
        write_json(self.run_dir / "status.json", payload)
        print(f"[status] {phase}: {state}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=1825)
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--min-gap", type=int, default=1)
    parser.add_argument("--max-gap", type=int, default=90)
    parser.add_argument("--variants", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260511)
    parser.add_argument("--mode", choices=["random", "drill"], default="random")
    parser.add_argument("--max-wr-drop", type=float, default=None)
    parser.add_argument("--max-opt-drop", type=float, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir) if args.run_dir else RUN_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Status(run_dir)
    cmdline = " ".join(["python", "-u", "experiments/temporal_echo/run_pipeline.py", *sys.argv[1:]])
    write_json(
        run_dir / "launch.json",
        {
            "command": cmdline,
            "cwd": str(ROOT),
            "started_at": status.started_at,
            "lookback_days": args.lookback_days,
            "min_gap": args.min_gap,
            "max_gap": args.max_gap,
            "variants": args.variants,
            "seed": args.seed,
            "mode": args.mode,
            "max_wr_drop": args.max_wr_drop,
            "max_opt_drop": args.max_opt_drop,
        },
    )

    try:
        status.update("build_features", "running", completed=0, total=3, run_dir=str(run_dir))
        build_args = [
            "--lookback-days",
            str(args.lookback_days),
            "--min-gap",
            str(args.min_gap),
            "--max-gap",
            str(args.max_gap),
        ]
        if args.version_id is not None:
            build_args += ["--version-id", str(args.version_id)]
        if args.force:
            build_args.append("--force")
        build_info = build_features.main(build_args)

        status.update("analyze_transitions", "running", completed=1, total=3, **build_info)
        stats_info = analyze_transitions.main(
            [
                "--signals",
                build_info["signals_path"],
                "--priors",
                build_info["priors_path"],
                "--out-dir",
                str(run_dir),
            ]
        )

        status.update("sweep", "running", completed=2, total=3, **build_info, **stats_info)
        sweep_info = sweep.main(
            [
                "--signals",
                build_info["signals_path"],
                "--priors",
                build_info["priors_path"],
                "--out-dir",
                str(run_dir),
                "--variants",
                str(args.variants),
                "--seed",
                str(args.seed),
                "--mode",
                args.mode,
            ]
            + ([] if args.max_wr_drop is None else ["--max-wr-drop", str(args.max_wr_drop)])
            + ([] if args.max_opt_drop is None else ["--max-opt-drop", str(args.max_opt_drop)])
        )

        status.update("complete", "succeeded", completed=3, total=3, **build_info, **stats_info, **sweep_info)
        write_json(
            run_dir / "done.json",
            {
                "state": "succeeded",
                "started_at": status.started_at,
                "finished_at": utc_now(),
                "outputs": {**build_info, **stats_info, **sweep_info},
            },
        )
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        print(tb, flush=True)
        status.update("failed", "failed", error=str(exc))
        write_json(
            run_dir / "failed.json",
            {
                "state": "failed",
                "started_at": status.started_at,
                "finished_at": utc_now(),
                "error": str(exc),
                "traceback": tb,
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
