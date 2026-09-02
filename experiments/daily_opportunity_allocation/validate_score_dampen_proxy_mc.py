"""MC validation for v60 market-structure score-dampen proxy candidates.

Experiment-only. This patches the MC call signal loader in-process to emulate
score-stage demotion of marginal 70-74 calls under a daily market-structure
risk wave. It does not write score rows or production config.
"""

from __future__ import annotations

import argparse
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
from experiments.daily_opportunity_allocation.validate_usable_policy_mc import (
    WINDOWS,
    _apply_v60_portfolio_env,
    _install_stable_label_hash,
    _restore_hash,
    _run_window_with_retry,
    _score_rows,
    _write_csv,
)
from strategy_config import STRATEGY_30DTE


DEFAULT_WAVE = ROOT / ".codex" / "runs" / "v60_extra_call_wave_20260519_215034" / "daily_wave_predictions.csv"


@dataclass(frozen=True)
class ProxyPolicy:
    name: str
    wave_threshold: float | None = None
    min_overall: int = 70
    max_overall: int = 74
    min_w_adj: float | None = None
    min_td: float | None = None
    min_sector_crash: float | None = None


def policies() -> dict[str, ProxyPolicy]:
    return {
        "baseline": ProxyPolicy("baseline"),
        "wave_q80_overflow_70_74": ProxyPolicy("wave_q80_overflow_70_74", wave_threshold=0.4398776594861369),
        "wave_q90_overflow_70_74": ProxyPolicy("wave_q90_overflow_70_74", wave_threshold=0.4729721681250059),
        "wave_q90_overflow_plus_wadj": ProxyPolicy(
            "wave_q90_overflow_plus_wadj",
            wave_threshold=0.4729721681250059,
            min_w_adj=2.0,
        ),
        "wave_q90_extra_like_overflow": ProxyPolicy(
            "wave_q90_extra_like_overflow",
            wave_threshold=0.4729721681250059,
            min_w_adj=2.0,
            min_td=0.25,
        ),
        "wave_q90_sector_crash_overflow": ProxyPolicy(
            "wave_q90_sector_crash_overflow",
            wave_threshold=0.4729721681250059,
            min_sector_crash=0.10,
        ),
        "wave_q85_sector_crash_overflow": ProxyPolicy(
            "wave_q85_sector_crash_overflow",
            wave_threshold=0.4530615755494915,
            min_sector_crash=0.10,
        ),
    }


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "None"):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def _weight_info(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _read_wave(path: Path) -> dict[date, float]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out: dict[date, float] = {}
    for row in rows:
        val = _float(row.get("wave_ml_pred_bad_rate"))
        if val is not None:
            out[_as_date(row["date"])] = val
    return out


def _should_block(sig: Any, policy: ProxyPolicy, wave: dict[date, float]) -> bool:
    if policy.name == "baseline":
        return False
    ov = int(getattr(sig, "overall", 0))
    if ov < policy.min_overall or ov > policy.max_overall:
        return False
    risk = wave.get(sig.date)
    if risk is None or policy.wave_threshold is None or risk < policy.wave_threshold:
        return False
    wi = _weight_info(getattr(sig, "weight_info", None))
    if policy.min_w_adj is not None:
        w_adj = _float(wi.get("w_adj"), 0.0)
        if w_adj is None or w_adj < policy.min_w_adj:
            return False
    if policy.min_td is not None:
        td = _float(wi.get("td"), 0.0)
        if td is None or td < policy.min_td:
            return False
    if policy.min_sector_crash is not None:
        sector = wi.get("sector_breadth_wave")
        crash = _float(sector.get("crash") if isinstance(sector, dict) else None, 0.0)
        if crash is None or crash < policy.min_sector_crash:
            return False
    return True


def make_load_signals(original, policy: ProxyPolicy, wave: dict[date, float], block_stats: dict[tuple[str, str], dict[str, int]], current_window: dict[str, str]):
    def load_signals(version, d_start, d_end):
        sigs = list(original(version, d_start, d_end))
        if policy.name == "baseline":
            return sigs
        kept = []
        blocked = 0
        blocked_days: set[date] = set()
        for sig in sigs:
            if _should_block(sig, policy, wave):
                blocked += 1
                blocked_days.add(sig.date)
                continue
            kept.append(sig)
        key = (policy.name, current_window.get("window", "unknown"))
        block_stats[key] = {"blocked_calls": blocked, "blocked_days": len(blocked_days), "loaded_calls": len(sigs)}
        print(
            f"  SCORE_DAMPEN_PROXY={policy.name}: blocked {blocked}/{len(sigs)} calls "
            f"across {len(blocked_days)} days",
            flush=True,
        )
        return kept

    return load_signals


def _write_markdown(path: Path, rows: list[dict[str, Any]], scored: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    lines = [
        "# Score-Dampen Proxy MC Validation",
        "",
        "Experiment-local MC screen for market-structure demotion of marginal 70-74 calls.",
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
        "| window | policy | blocked | mean ret d | med ret d | worst DD d | mean DD d | p(coll) d |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in scored:
        lines.append(
            f"| {row['window']} | `{row['policy']}` | {int(float(row.get('blocked_calls', 0) or 0))} | "
            f"{float(row['mean_ret_delta']):+.2f} | {float(row['med_ret_delta']):+.2f} | "
            f"{float(row['worst_dd_delta']):+.2f} | {float(row['mean_dd_delta']):+.2f} | "
            f"{float(row['p_coll_delta']):+.2f} |"
        )
    lines.extend(["", "## Read", ""])
    for policy in [p for p in meta["policies"] if p != "baseline"]:
        p_rows = [r for r in scored if r["policy"] == policy]
        if not p_rows:
            continue
        worse = [r for r in p_rows if float(r["worst_dd_delta"]) > 1.0]
        verdict = "pass" if not worse else "no pass"
        lines.append(
            f"- `{policy}`: {verdict}; max worst-DD delta "
            f"{max(float(r['worst_dd_delta']) for r in p_rows):+.2f}, avg mean-DD delta "
            f"{sum(float(r['mean_dd_delta']) for r in p_rows) / len(p_rows):+.2f}."
        )
        if worse:
            lines.append(f"  Worse-DD windows: {', '.join(r['window'] for r in worse)}.")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This emulates score demotion by removing marginal 70-74 calls at MC load time.",
            "- A pass here is not a scoring ship; it must be implemented staging-native and validated at WR15/N before any version bump.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave-predictions", type=Path, default=DEFAULT_WAVE)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version-id", type=int, default=60)
    parser.add_argument("--n", type=int, default=240)
    parser.add_argument("--windows", default="2020-crash,2024,2025,2022,22-now,5y")
    parser.add_argument(
        "--policies",
        default="baseline,wave_q90_overflow_70_74,wave_q90_overflow_plus_wadj,wave_q90_extra_like_overflow,wave_q90_sector_crash_overflow,wave_q85_sector_crash_overflow",
    )
    parser.add_argument("--seed-offset", type=int, default=0)
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    status = args.out_dir / "score_dampen_proxy_mc_status.json"
    started_at = datetime.now(timezone.utc).isoformat()
    status.write_text(json.dumps({"phase": "score_dampen_proxy_mc", "state": "running", "started_at": started_at}, indent=2), encoding="utf-8")

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_MP"] = "1"
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "0"
    os.environ["REALLOC_STRATEGY"] = ""
    _apply_v60_portfolio_env()
    _install_stable_label_hash(args.seed_offset)

    import monte_carlo as mc

    mc.MAX_POSITIONS_CALL = STRATEGY_30DTE.MAX_POSITIONS_CALL
    mc.MAX_POSITIONS_PUT = STRATEGY_30DTE.MAX_POSITIONS_PUT

    wave = _read_wave(args.wave_predictions)
    av = AlgorithmVersion.get_by_id(args.version_id)
    all_policies = policies()
    wanted_policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    wanted_windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    missing = [p for p in wanted_policies if p not in all_policies]
    if missing:
        raise KeyError(f"Unknown policies: {missing}")

    rows: list[dict[str, Any]] = []
    block_stats: dict[tuple[str, str], dict[str, int]] = {}
    current_window: dict[str, str] = {}
    original_load_signals = mc.load_signals
    try:
        total = len(wanted_policies) * len(wanted_windows)
        completed = 0
        for policy_name in wanted_policies:
            policy = all_policies[policy_name]
            mc.load_signals = make_load_signals(original_load_signals, policy, wave, block_stats, current_window)
            print(f"\n[policy] {policy_name}", flush=True)
            for window_name in wanted_windows:
                current_window["window"] = window_name
                d_start, d_end = WINDOWS[window_name]
                t0 = time.time()
                row = dict(_run_window_with_retry(mc, window_name, d_start, d_end, av)["seeded"])
                row["window"] = window_name
                row["policy"] = policy_name
                row["n"] = args.n
                row["elapsed_s"] = round(time.time() - t0, 2)
                row["policy_config"] = json.dumps(asdict(policy), sort_keys=True)
                row.update(block_stats.get((policy_name, window_name), {"blocked_calls": 0, "blocked_days": 0, "loaded_calls": 0}))
                rows.append(row)
                completed += 1
                _write_csv(args.out_dir / "score_dampen_proxy_mc.csv", rows)
                scored = _score_rows(rows)
                _write_csv(args.out_dir / "score_dampen_proxy_mc_ranked.csv", scored)
                status.write_text(
                    json.dumps(
                        {
                            "phase": "score_dampen_proxy_mc",
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
                    f"  {window_name}: mean={row['mean_ret']:+.1f}% med={row['med_ret']:+.1f}% "
                    f"worstDD={row['worst_dd']:.1f}% meanDD={row['mean_dd']:.1f}% elapsed={row['elapsed_s']:.0f}s",
                    flush=True,
                )
    finally:
        mc.load_signals = original_load_signals
        _restore_hash()

    scored = _score_rows(rows)
    out_csv = args.out_dir / "score_dampen_proxy_mc.csv"
    ranked_csv = args.out_dir / "score_dampen_proxy_mc_ranked.csv"
    out_md = args.out_dir / "score_dampen_proxy_mc_summary.md"
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
