"""Stable-seed MC validation for v60 hard crash-controller candidates.

Experiment-local only. This patches `monte_carlo.run_single_sim` in-process so
the deterministic hard-controller leads can be tested at the stochastic
portfolio-fill layer. It never writes scores or production strategy constants.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
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
from experiments.daily_opportunity_allocation.bayes_alpha_sweep import _read_daily
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


EXP_DIR = Path(__file__).resolve().parent
DEFAULT_DAILY = ROOT / ".codex" / "runs" / "v60_daily_opportunity_smooth_20260519_082415" / "daily_state.csv"
DEFAULT_OVERLAP = ROOT / ".codex" / "runs" / "v60_hard_crash_controller_20260519_110907" / "version_overlap_features.csv"


@dataclass(frozen=True)
class Trigger:
    name: str
    feature: str
    op: str
    threshold: float


@dataclass(frozen=True)
class HardAction:
    name: str
    trigger: Trigger
    call_scale: float = 1.0
    put_scale: float = 1.0
    risk_max_pos: int = STRATEGY_30DTE.MAX_POSITIONS
    risk_call_cap: int = STRATEGY_30DTE.MAX_POSITIONS_CALL or STRATEGY_30DTE.MAX_POSITIONS
    put_reserve: int = 0
    put_first: bool = True
    cooloff_days: int = 0
    dd_trigger: float = 2.0


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def _cond(value: float | None, op: str, threshold: float) -> bool:
    if value is None:
        return False
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == ">":
        return value > threshold
    raise ValueError(f"Unsupported op {op!r}")


def _trigger_active(trigger: Trigger, row: dict[str, Any]) -> bool:
    return _cond(_float(row.get(trigger.feature)), trigger.op, trigger.threshold)


def candidate_policies() -> dict[str, HardAction]:
    base_cap = STRATEGY_30DTE.MAX_POSITIONS_CALL or STRATEGY_30DTE.MAX_POSITIONS
    base = HardAction(
        name="baseline",
        trigger=Trigger("never", "breadth_breadth_score", "<=", -1.0),
        call_scale=1.0,
        put_scale=1.0,
        risk_max_pos=STRATEGY_30DTE.MAX_POSITIONS,
        risk_call_cap=base_cap,
        put_reserve=0,
        put_first=False,
    )
    policies = [
        base,
        HardAction(
            name="book_underhedged_open_put_share_014",
            trigger=Trigger("open_put_share_le_01429", "open_put_share", "<=", 0.1429),
            call_scale=0.50,
            put_scale=1.10,
            risk_max_pos=12,
            risk_call_cap=8,
            put_reserve=1,
        ),
        HardAction(
            name="open_call_12_guard",
            trigger=Trigger("open_call_n_ge_12", "open_call_n", ">=", 12.0),
            call_scale=0.50,
            put_scale=1.10,
            risk_max_pos=12,
            risk_call_cap=8,
            put_reserve=1,
        ),
        HardAction(
            name="breadth_score_4015_guard",
            trigger=Trigger("breadth_score_le_4015", "breadth_breadth_score", "<=", 40.15),
            call_scale=0.35,
            put_scale=1.20,
            risk_max_pos=10,
            risk_call_cap=6,
            put_reserve=2,
        ),
        HardAction(
            name="v59_extra_calls_guard",
            trigger=Trigger("v60_extra_calls_vs_v59_ge_2", "v60_extra_calls_vs_v59", ">=", 2.0),
            call_scale=0.50,
            put_scale=1.10,
            risk_max_pos=12,
            risk_call_cap=8,
            put_reserve=1,
            cooloff_days=10,
        ),
    ]
    return {p.name: p for p in policies}


def read_daily(daily_path: Path, overlap_path: Path | None) -> dict[date, dict[str, Any]]:
    daily = _read_daily(daily_path)
    if overlap_path and overlap_path.exists():
        with overlap_path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                d = date.fromisoformat(row["date"][:10])
                if d in daily:
                    daily[d].update(row)
    return daily


def make_run_single_sim(policy: HardAction, daily: dict[date, dict[str, Any]]):
    import monte_carlo as mc

    def run_single_sim(
        trading_days,
        calls_by_date,
        call_outcomes,
        puts_by_date,
        put_outcomes,
        rng,
        regime_dates=None,
        regime_map=None,
        collect_tape=False,
        close_by_sym_idx=None,
    ):
        cash = mc.STARTING_CASH
        positions = []
        peak_value = mc.STARTING_CASH
        max_dd = 0.0
        day_to_idx = {d: i for i, d in enumerate(trading_days)}
        cooldown_until = -1

        tp_c = sl_c = hard_c = 0
        tp_p = sl_p = hard_p = 0

        base_cap_call = mc.MAX_POSITIONS_CALL if mc.MAX_POSITIONS_CALL is not None else mc.MAX_POSITIONS
        base_cap_put = mc.MAX_POSITIONS_PUT if mc.MAX_POSITIONS_PUT is not None else mc.MAX_POSITIONS
        side_capped = (mc.MAX_POSITIONS_CALL is not None) or (mc.MAX_POSITIONS_PUT is not None)

        for day_idx, today in enumerate(trading_days):
            keep = []
            for p in positions:
                base = day_to_idx.get(p.entry_date, -999)
                if base + p.exit_bar <= day_idx:
                    cash += p.premium_cost * (1 + p.option_pnl)
                    if p.side == "call":
                        if p.outcome == "tp":
                            tp_c += 1
                        elif p.outcome == "sl":
                            sl_c += 1
                        else:
                            hard_c += 1
                    else:
                        if p.outcome == "tp":
                            tp_p += 1
                        elif p.outcome == "sl":
                            sl_p += 1
                        else:
                            hard_p += 1
                else:
                    keep.append(p)
            positions = keep

            portfolio_value = cash + sum(p.premium_cost for p in positions)
            if portfolio_value > peak_value:
                peak_value = portfolio_value
            dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0.0
            max_dd = max(max_dd, dd)
            if portfolio_value <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD:
                break

            row = daily.get(today, {})
            raw_active = _trigger_active(policy.trigger, row)
            if raw_active and policy.cooloff_days > 0:
                cooldown_until = max(cooldown_until, day_idx + policy.cooloff_days)
            active = raw_active or day_idx <= cooldown_until or dd >= policy.dd_trigger

            max_positions = int(policy.risk_max_pos) if active else int(mc.MAX_POSITIONS)
            cap_call = int(policy.risk_call_cap) if active else int(base_cap_call)
            cap_put = int(base_cap_put)

            open_syms = {p.sym_id for p in positions}
            call_open = sum(1 for p in positions if p.side == "call")
            put_open = sum(1 for p in positions if p.side == "put")

            reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = mc.alloc_scale_for(reg_mult, is_put=False)
            reg_scale_p = mc.alloc_scale_for(reg_mult, is_put=True)

            def _try_fill_call(sym_id, score, key, ct, ern):
                nonlocal cash, call_open, portfolio_value
                effective_max = max_positions
                if active and policy.put_reserve > 0:
                    effective_max = max(0, max_positions - max(0, policy.put_reserve - put_open))
                if len(positions) >= effective_max:
                    return False
                if side_capped and call_open >= cap_call:
                    return False
                policy_scale = policy.call_scale if active else 1.0
                if policy_scale <= 0.0:
                    return False
                if ct == "ct_call":
                    tier = mc.CT_CALL_TIER
                elif ern and mc.EARN_BOOST_CALL:
                    tier = mc.EARN_BOOST_CALL_TIER
                else:
                    tier = mc.score_to_tier(score)
                dd_scale = 1.0
                if mc.DD_SOFT_BAND_HI > mc.DD_SOFT_BAND_LO and dd > mc.DD_SOFT_BAND_LO:
                    if dd >= mc.DD_SOFT_BAND_HI:
                        dd_scale = mc.DD_SOFT_CALL_FLOOR
                    else:
                        t = (dd - mc.DD_SOFT_BAND_LO) / (mc.DD_SOFT_BAND_HI - mc.DD_SOFT_BAND_LO)
                        dd_scale = 1.0 - t * (1.0 - mc.DD_SOFT_CALL_FLOOR)
                premium_cost = portfolio_value * mc.TIER_ALLOC[tier] * reg_scale_c * dd_scale * policy_scale
                if premium_cost > cash or premium_cost <= 0:
                    return False
                o = call_outcomes[key]
                outcome, pnl = mc.resolve(o, rng)
                cash -= premium_cost
                positions.append(
                    mc.Position(
                        sym_id,
                        today,
                        o["exit_bar"],
                        premium_cost,
                        pnl,
                        outcome,
                        "call",
                        score=score,
                        tier=tier,
                        ct=ct,
                        ern=ern,
                        entry_value=portfolio_value,
                        entry_peak=peak_value,
                        entry_dd=dd,
                        entry_open_calls=call_open,
                        entry_open_puts=put_open,
                        entry_idx=day_idx,
                        entry_underlying=o.get("entry"),
                        premium_pct=o.get("premium_pct"),
                    )
                )
                open_syms.add(sym_id)
                call_open += 1
                portfolio_value = cash + sum(p.premium_cost for p in positions)
                return True

            def _try_fill_put(sym_id, score, key, ct):
                nonlocal cash, put_open, portfolio_value
                if len(positions) >= max_positions:
                    return False
                if side_capped and put_open >= cap_put:
                    return False
                policy_scale = policy.put_scale if active else 1.0
                if policy_scale <= 0.0:
                    return False
                tier = mc.CT_PUT_TIER if ct == "ct_put" else mc.put_score_to_tier(score)
                premium_cost = (
                    portfolio_value
                    * mc.PUT_TIER_ALLOC[tier]
                    * reg_scale_p
                    * mc.saw_put_ucurve_scale(today)
                    * policy_scale
                )
                if premium_cost > cash or premium_cost <= 0:
                    return False
                o = put_outcomes[key]
                outcome, pnl = mc.resolve(o, rng)
                cash -= premium_cost
                positions.append(
                    mc.Position(
                        sym_id,
                        today,
                        o["exit_bar"],
                        premium_cost,
                        pnl,
                        outcome,
                        "put",
                        score=score,
                        tier=tier,
                        ct=ct,
                        ern=False,
                        entry_value=portfolio_value,
                        entry_peak=peak_value,
                        entry_dd=dd,
                        entry_open_calls=call_open,
                        entry_open_puts=put_open,
                        entry_idx=day_idx,
                        entry_underlying=o.get("entry"),
                        premium_pct=o.get("premium_pct"),
                    )
                )
                open_syms.add(sym_id)
                put_open += 1
                portfolio_value = cash + sum(p.premium_cost for p in positions)
                return True

            def _do_calls():
                eligible = [
                    (sid, sc, k, ct, ern)
                    for sid, sc, k, ct, ern in calls_by_date.get(today, [])
                    if k in call_outcomes and sid not in open_syms
                ]
                primary = [e for e in eligible if e[1] >= mc.PRIMARY_THRESHOLD or e[3] == "ct_call"]
                overflow = [e for e in eligible if e[1] < mc.PRIMARY_THRESHOLD and e[3] != "ct_call"]
                primary.sort(key=lambda x: (0 if x[3] == "ct_call" else 1, -x[1], rng.random()))
                overflow.sort(key=lambda x: (-x[1], rng.random()))
                for sym_id, score, key, ct, ern in primary + overflow:
                    if len(positions) >= max_positions:
                        break
                    if side_capped and call_open >= cap_call:
                        break
                    _try_fill_call(sym_id, score, key, ct, ern)

            def _do_puts():
                eligible = [
                    (sid, sc, k, ct)
                    for sid, sc, k, ct in puts_by_date.get(today, [])
                    if k in put_outcomes and sid not in open_syms
                ]
                eligible.sort(key=lambda x: (0 if x[3] == "ct_put" else 1, x[1], rng.random()))
                for sym_id, score, key, ct in eligible:
                    if len(positions) >= max_positions:
                        break
                    if side_capped and put_open >= cap_put:
                        break
                    _try_fill_put(sym_id, score, key, ct)

            if active and policy.put_first:
                _do_puts()
                _do_calls()
            elif mc.PUT_PRIORITY == "puts_first":
                _do_puts()
                _do_calls()
            else:
                _do_calls()
                _do_puts()

        for p in positions:
            cash += p.premium_cost * (1 + mc.NET_HARD_SELL)
            if p.side == "call":
                hard_c += 1
            else:
                hard_p += 1

        portfolio_value = cash
        final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0.0
        max_dd = max(max_dd, final_dd)
        ct = tp_c + sl_c + hard_c or 1
        pt = tp_p + sl_p + hard_p or 1
        return {
            "final": portfolio_value,
            "max_dd": max_dd,
            "call_tp": tp_c / ct * 100,
            "call_sl": sl_c / ct * 100,
            "call_hard": hard_c / ct * 100,
            "put_tp": tp_p / pt * 100,
            "put_sl": sl_p / pt * 100,
            "put_hard": hard_p / pt * 100,
            "call_trades": tp_c + sl_c + hard_c,
            "put_trades": tp_p + sl_p + hard_p,
        }

    return run_single_sim


def _summarize_policy(scored: list[dict[str, Any]], policy: str, windows: list[str]) -> dict[str, Any]:
    rows = [r for r in scored if r["policy"] == policy and r["window"] in windows]
    if not rows:
        return {"policy": policy, "pass": False, "reason": "missing rows"}
    worse = [r for r in rows if float(r["worst_dd_delta"]) > 1.0]
    failwindow_worse = [
        r for r in rows
        if r["window"] in {"2020-crash", "2024", "2025"} and float(r["worst_dd_delta"]) > 0.5
    ]
    return {
        "policy": policy,
        "pass": not worse and not failwindow_worse,
        "windows": len(rows),
        "mean_ret_delta_avg": statistics.fmean(float(r["mean_ret_delta"]) for r in rows),
        "worst_dd_delta_max": max(float(r["worst_dd_delta"]) for r in rows),
        "mean_dd_delta_avg": statistics.fmean(float(r["mean_dd_delta"]) for r in rows),
        "p_coll_delta_max": max(float(r["p_coll_delta"]) for r in rows),
        "worse_windows": [r["window"] for r in worse],
        "failwindow_worse": [r["window"] for r in failwindow_worse],
    }


def _write_markdown(path: Path, rows: list[dict[str, Any]], scored: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    policies = [p for p in meta["policies"] if p != "baseline"]
    reads = [_summarize_policy(scored, p, meta["windows"]) for p in policies]
    lines = [
        "# Hard Controller MC Validation",
        "",
        "Experiment-local MC screen. No score rows or production configs were written.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- iterations: {meta['n']}",
        f"- seed_offset: {meta['seed_offset']}",
        f"- windows: {', '.join(meta['windows'])}",
        "",
        "## Candidate Deltas Vs Baseline",
        "",
        "| window | policy | mean ret d | med ret d | worst DD d | mean DD d | p(coll) d |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in scored:
        lines.append(
            f"| {row['window']} | `{row['policy']}` | {float(row['mean_ret_delta']):+.2f} | "
            f"{float(row['med_ret_delta']):+.2f} | {float(row['worst_dd_delta']):+.2f} | "
            f"{float(row['mean_dd_delta']):+.2f} | {float(row['p_coll_delta']):+.2f} |"
        )
    lines.extend(["", "## Read", ""])
    for item in reads:
        verdict = "pass" if item["pass"] else "no pass"
        lines.append(
            f"- `{item['policy']}`: {verdict}; avg mean-return delta "
            f"{float(item.get('mean_ret_delta_avg', 0.0)):+.2f}, max worst-DD delta "
            f"{float(item.get('worst_dd_delta_max', 0.0)):+.2f}, avg mean-DD delta "
            f"{float(item.get('mean_dd_delta_avg', 0.0)):+.2f}."
        )
        if item.get("worse_windows"):
            lines.append(f"  Worse-DD windows: {', '.join(item['worse_windows'])}.")
        if item.get("failwindow_worse"):
            lines.append(f"  Fail-window worse: {', '.join(item['failwindow_worse'])}.")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Treat this as an MC validation gate, not an implementation ship.",
            "- A candidate should not worsen 2020-crash, 2024, or 2025 worst DD.",
            "- Prefer interpretable controls that preserve full-window trade throughput and compound.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(DEFAULT_DAILY))
    parser.add_argument("--overlap", default=str(DEFAULT_OVERLAP))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--version-id", type=int, default=60)
    parser.add_argument("--n", type=int, default=240)
    parser.add_argument("--windows", default="2020-crash,2024,2025,2022,22-now,5y")
    parser.add_argument(
        "--policies",
        default="baseline,book_underhedged_open_put_share_014,open_call_12_guard,breadth_score_4015_guard,v59_extra_calls_guard",
    )
    parser.add_argument("--seed-offset", type=int, default=0)
    args = parser.parse_args(argv)

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_MP"] = "1"
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "0"
    os.environ["REALLOC_STRATEGY"] = ""
    _apply_v60_portfolio_env()
    _install_stable_label_hash(args.seed_offset)

    import monte_carlo as mc

    mc.MAX_POSITIONS = STRATEGY_30DTE.MAX_POSITIONS
    mc.MAX_POSITIONS_CALL = STRATEGY_30DTE.MAX_POSITIONS_CALL
    mc.MAX_POSITIONS_PUT = STRATEGY_30DTE.MAX_POSITIONS_PUT

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / "hard_controller_mc_status.json"
    started_at = datetime.now(timezone.utc).isoformat()
    status.write_text(
        json.dumps({"phase": "hard_controller_mc", "state": "running", "started_at": started_at}, indent=2),
        encoding="utf-8",
    )

    daily = read_daily(Path(args.daily), Path(args.overlap) if args.overlap else None)
    av = AlgorithmVersion.get_by_id(args.version_id)
    policies = candidate_policies()
    wanted_policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    wanted_windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    missing = [p for p in wanted_policies if p not in policies]
    if missing:
        raise KeyError(f"Unknown policies: {missing}")

    rows: list[dict[str, Any]] = []
    original_run_single = mc.run_single_sim
    try:
        total = len(wanted_policies) * len(wanted_windows)
        completed = 0
        for policy_name in wanted_policies:
            policy = policies[policy_name]
            mc.run_single_sim = make_run_single_sim(policy, daily)
            print(f"\n[policy] {policy_name}", flush=True)
            for window_name in wanted_windows:
                d_start, d_end = WINDOWS[window_name]
                t0 = time.time()
                row = dict(_run_window_with_retry(mc, window_name, d_start, d_end, av)["seeded"])
                row["window"] = window_name
                row["policy"] = policy_name
                row["n"] = args.n
                row["elapsed_s"] = round(time.time() - t0, 2)
                row["trigger"] = json.dumps(asdict(policy.trigger), sort_keys=True)
                row["action"] = json.dumps(asdict(policy), sort_keys=True)
                rows.append(row)
                completed += 1
                _write_csv(out_dir / "hard_controller_mc.csv", rows)
                scored = _score_rows(rows)
                _write_csv(out_dir / "hard_controller_mc_ranked.csv", scored)
                status.write_text(
                    json.dumps(
                        {
                            "phase": "hard_controller_mc",
                            "state": "running",
                            "started_at": started_at,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "completed": completed,
                            "total": total,
                            "current_policy": policy_name,
                            "current_window": window_name,
                            "best": scored[0] if scored else None,
                            "run_log": str(out_dir / "run.log"),
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
        mc.run_single_sim = original_run_single
        _restore_hash()

    scored = _score_rows(rows)
    _write_csv(out_dir / "hard_controller_mc.csv", rows)
    _write_csv(out_dir / "hard_controller_mc_ranked.csv", scored)
    meta = {
        "version_id": int(av.id),
        "commit": getattr(av, "git_commit", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": args.n,
        "windows": wanted_windows,
        "policies": wanted_policies,
        "seed_offset": args.seed_offset,
        "daily": str(args.daily),
        "overlap": str(args.overlap),
    }
    _write_markdown(out_dir / "hard_controller_mc_summary.md", rows, scored, meta)
    done = {
        "phase": "done",
        "state": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "csv": str(out_dir / "hard_controller_mc.csv"),
            "ranked": str(out_dir / "hard_controller_mc_ranked.csv"),
            "summary": str(out_dir / "hard_controller_mc_summary.md"),
        },
        "policies": wanted_policies,
        "windows": wanted_windows,
        "n": args.n,
    }
    (out_dir / "done.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
    status.write_text(json.dumps(done, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
