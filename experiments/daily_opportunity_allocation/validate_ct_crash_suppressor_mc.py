"""MC validation for an acute crash CT-call suppressor.

Experiment-only. This patches the MC call signal loader in-process to suppress
or demote CT-call promotion for marginal 70-74 calls only when the mined
market-structure crash state is active. It does not write scores, strategy
config, algorithm metadata, or MC DB rows.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.daily_opportunity_allocation.validate_score_dampen_proxy_mc import (
    _as_date,
    _float,
    _read_wave,
    _weight_info,
)
from experiments.daily_opportunity_allocation.validate_usable_policy_mc import (
    WINDOWS as BASE_WINDOWS,
    _apply_v60_portfolio_env,
    _install_stable_label_hash,
    _restore_hash,
    _run_window_with_retry,
    _score_rows,
    _write_csv,
)
from strategy_config import STRATEGY_30DTE


DEFAULT_WAVE = ROOT / ".codex" / "runs" / "v60_extra_call_wave_20260519_215034" / "daily_wave_predictions.csv"

WINDOWS = {
    **BASE_WINDOWS,
    "2020-full": (date(2020, 1, 1), date(2020, 12, 31)),
    "covid-peak": (date(2020, 3, 1), date(2020, 3, 31)),
}


@dataclass(frozen=True)
class CtPolicy:
    name: str
    action: str = "none"  # none | suppress | demote_low
    wave_threshold: float = 0.4729721681250059
    min_sector_crash: float = 0.55
    max_breadth_ema50: float = 25.0
    max_mcclellan: float = -40.0
    min_overall: int = 70
    max_overall: int = 74
    max_trend: int = 20
    demote_score: int = 75


def policies() -> dict[str, CtPolicy]:
    return {
        "baseline": CtPolicy("baseline", action="none"),
        "ct_crash_suppress": CtPolicy("ct_crash_suppress", action="suppress"),
        "ct_crash_demote_low": CtPolicy("ct_crash_demote_low", action="demote_low"),
    }


def _read_daily_rows(path: Path) -> dict[date, dict[str, Any]]:
    rows: dict[date, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows[_as_date(row["date"])] = row
    return rows


def _matches_ct_crash(sig: Any, policy: CtPolicy, wave: dict[date, float], daily: dict[date, dict[str, Any]]) -> bool:
    if policy.action == "none":
        return False
    ov = int(getattr(sig, "overall", 0) or 0)
    if ov < policy.min_overall or ov > policy.max_overall:
        return False
    trend = _float(getattr(sig, "trend", None))
    if trend is None or trend > policy.max_trend:
        return False
    risk = wave.get(sig.date)
    if risk is None or risk < policy.wave_threshold:
        return False
    wi = _weight_info(getattr(sig, "weight_info", None))
    sector = wi.get("sector_breadth_wave")
    crash = _float(sector.get("crash") if isinstance(sector, dict) else None, 0.0)
    if crash is None or crash < policy.min_sector_crash:
        return False
    day = daily.get(sig.date, {})
    ema50 = _float(day.get("day_breadth_pct_above_ema50"))
    if ema50 is None or ema50 > policy.max_breadth_ema50:
        return False
    mcc = _float(day.get("day_breadth_mcclellan_oscillator"))
    if mcc is None or mcc > policy.max_mcclellan:
        return False
    return True


def _mutate_signal(sig: Any, policy: CtPolicy) -> Any:
    """Return a local in-memory signal with CT-call promotion neutralized."""

    out = copy.copy(sig)
    # CT_CALL_TREND_MAX is 20 in v60. Moving trend just above it disables the
    # CT tag without changing date/symbol/weight metadata.
    out.trend = policy.max_trend + 1
    if policy.action == "demote_low":
        # Preserve a small normal call allocation instead of full suppression.
        out.overall = max(policy.demote_score, int(getattr(out, "overall", 0) or 0))
    return out


def make_load_signals(
    original,
    policy: CtPolicy,
    wave: dict[date, float],
    daily: dict[date, dict[str, Any]],
    stats: dict[tuple[str, str], dict[str, int]],
    current_window: dict[str, str],
):
    def load_signals(version, d_start, d_end):
        sigs = list(original(version, d_start, d_end))
        if policy.action == "none":
            return sigs
        out = []
        touched = 0
        days: set[date] = set()
        for sig in sigs:
            if _matches_ct_crash(sig, policy, wave, daily):
                touched += 1
                days.add(sig.date)
                out.append(_mutate_signal(sig, policy))
            else:
                out.append(sig)
        key = (policy.name, current_window.get("window", "unknown"))
        stats[key] = {"suppressed_calls": touched, "suppressed_days": len(days), "loaded_calls": len(sigs)}
        print(
            f"  CT_CRASH_SUPPRESSOR={policy.name}: touched {touched}/{len(sigs)} calls across {len(days)} days",
            flush=True,
        )
        return out

    return load_signals


def _write_markdown(path: Path, rows: list[dict[str, Any]], scored: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    lines = [
        "# CT-Call Crash Suppressor MC Validation",
        "",
        "Experiment-local MC screen for suppressing acute-crash CT-call promotion.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- iterations: {meta['n']}",
        f"- seed_offset: {meta.get('seed_offset', 0)}",
        f"- wave_predictions: {meta.get('wave_predictions')}",
        f"- windows: {', '.join(meta['windows'])}",
        "",
        "## Candidate Deltas Vs Baseline",
        "",
        "| window | policy | touched | mean ret d | med ret d | worst DD d | mean DD d | p(coll) d |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in scored:
        lines.append(
            f"| {row['window']} | `{row['policy']}` | {int(float(row.get('suppressed_calls', 0) or 0))} | "
            f"{float(row['mean_ret_delta']):+.2f} | {float(row['med_ret_delta']):+.2f} | "
            f"{float(row['worst_dd_delta']):+.2f} | {float(row['mean_dd_delta']):+.2f} | "
            f"{float(row['p_coll_delta']):+.2f} |"
        )
    lines.extend(["", "## Read", ""])
    for policy_name in [p for p in meta["policies"] if p != "baseline"]:
        p_rows = [r for r in scored if r["policy"] == policy_name]
        if not p_rows:
            continue
        worse = [r for r in p_rows if float(r["worst_dd_delta"]) > 1.0 or float(r["mean_dd_delta"]) > 0.5]
        verdict = "pass" if not worse else "no pass"
        lines.append(
            f"- `{policy_name}`: {verdict}; max worst-DD delta "
            f"{max(float(r['worst_dd_delta']) for r in p_rows):+.2f}, max mean-DD delta "
            f"{max(float(r['mean_dd_delta']) for r in p_rows):+.2f}."
        )
        if worse:
            lines.append(f"  Worse windows: {', '.join(r['window'] for r in worse)}.")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This is Stage 3 portfolio/cascade research, not a score-version candidate.",
            "- `ct_crash_suppress` leaves the signal in the candidate list but disables CT promotion, so 70-74 calls fall back to overflow allocation.",
            "- `ct_crash_demote_low` disables CT promotion but lifts the signal to the normal 75 low tier, approximating a partial allocation fade.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave-predictions", type=Path, default=DEFAULT_WAVE)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version-id", type=int, default=60)
    parser.add_argument("--n", type=int, default=240)
    parser.add_argument("--windows", default="2020-crash,covid-peak,2020-full,2022,2024,2025,22-now,5y")
    parser.add_argument("--policies", default="baseline,ct_crash_suppress,ct_crash_demote_low")
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--db-attempts", type=int, default=5)
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    status = args.out_dir / "ct_crash_suppressor_status.json"
    started_at = datetime.now(timezone.utc).isoformat()
    status.write_text(
        json.dumps({"phase": "ct_crash_suppressor_mc", "state": "running", "started_at": started_at}, indent=2),
        encoding="utf-8",
    )

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_MP"] = "1"
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "0"
    os.environ["REALLOC_STRATEGY"] = ""
    _apply_v60_portfolio_env()
    _install_stable_label_hash(args.seed_offset)

    import monte_carlo as mc

    mc.TRADE_TAPE_ENABLED = False
    mc.MAX_POSITIONS_CALL = STRATEGY_30DTE.MAX_POSITIONS_CALL
    mc.MAX_POSITIONS_PUT = STRATEGY_30DTE.MAX_POSITIONS_PUT

    wave = _read_wave(args.wave_predictions)
    daily = _read_daily_rows(args.wave_predictions)
    av = AlgorithmVersion.get_by_id(args.version_id)
    all_policies = policies()
    wanted_policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    wanted_windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    missing = [p for p in wanted_policies if p not in all_policies]
    if missing:
        raise KeyError(f"Unknown policies: {missing}")
    missing_windows = [w for w in wanted_windows if w not in WINDOWS]
    if missing_windows:
        raise KeyError(f"Unknown windows: {missing_windows}")

    rows: list[dict[str, Any]] = []
    stats: dict[tuple[str, str], dict[str, int]] = {}
    current_window: dict[str, str] = {}
    original_load_signals = mc.load_signals
    total = len(wanted_policies) * len(wanted_windows)
    completed = 0
    try:
        for policy_name in wanted_policies:
            policy = all_policies[policy_name]
            mc.load_signals = make_load_signals(original_load_signals, policy, wave, daily, stats, current_window)
            print(f"\n[policy] {policy_name}", flush=True)
            for window_name in wanted_windows:
                current_window["window"] = window_name
                d_start, d_end = WINDOWS[window_name]
                t0 = time.time()
                result = dict(_run_window_with_retry(mc, window_name, d_start, d_end, av, attempts=args.db_attempts)["seeded"])
                result["window"] = window_name
                result["policy"] = policy_name
                result["n"] = args.n
                result["elapsed_s"] = round(time.time() - t0, 2)
                result["policy_config"] = json.dumps(asdict(policy), sort_keys=True)
                result.update(stats.get((policy_name, window_name), {"suppressed_calls": 0, "suppressed_days": 0, "loaded_calls": 0}))
                rows.append(result)
                completed += 1
                scored = _score_rows(rows)
                _write_csv(args.out_dir / "ct_crash_suppressor_mc.csv", rows)
                _write_csv(args.out_dir / "ct_crash_suppressor_ranked.csv", scored)
                status.write_text(
                    json.dumps(
                        {
                            "phase": "ct_crash_suppressor_mc",
                            "state": "running",
                            "started_at": started_at,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "completed": completed,
                            "total": total,
                            "current_policy": policy_name,
                            "current_window": window_name,
                            "best": scored[0] if scored else None,
                            "run_log": str(args.out_dir / "run.log"),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(
                    f"  {window_name}: mean={result['mean_ret']:+.1f}% med={result['med_ret']:+.1f}% "
                    f"worstDD={result['worst_dd']:.1f}% meanDD={result['mean_dd']:.1f}% elapsed={result['elapsed_s']:.0f}s",
                    flush=True,
                )
    finally:
        mc.load_signals = original_load_signals
        _restore_hash()

    scored = _score_rows(rows)
    out_csv = args.out_dir / "ct_crash_suppressor_mc.csv"
    ranked_csv = args.out_dir / "ct_crash_suppressor_ranked.csv"
    out_md = args.out_dir / "ct_crash_suppressor_summary.md"
    _write_csv(out_csv, rows)
    _write_csv(ranked_csv, scored)
    meta = {
        "version_id": int(av.id),
        "commit": getattr(av, "git_commit", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": args.n,
        "windows": wanted_windows,
        "policies": wanted_policies,
        "seed_offset": args.seed_offset,
        "wave_predictions": str(args.wave_predictions),
    }
    _write_markdown(out_md, rows, scored, meta)
    done = {
        "phase": "done",
        "state": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "outputs": {"csv": str(out_csv), "ranked": str(ranked_csv), "summary": str(out_md)},
    }
    status.write_text(json.dumps(done, indent=2), encoding="utf-8")
    (args.out_dir / "done.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
    print(f"Wrote {out_csv}", flush=True)
    print(f"Wrote {ranked_csv}", flush=True)
    print(f"Wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
