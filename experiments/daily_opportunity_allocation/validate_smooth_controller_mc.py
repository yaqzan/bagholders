"""Stable-seed MC validation for smooth daily exposure-controller candidates.

Experiment-local only. The script patches `monte_carlo.run_single_sim` in
process so SmoothParams can scale allocations at the actual fill point. It
never writes score rows or strategy constants.
"""

from __future__ import annotations

import argparse
import builtins
import csv
import hashlib
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.daily_opportunity_allocation.smooth_controller_sweep import (
    SmoothParams,
    _read_daily,
    baseline_params,
    cash_reserve_enabled,
    legacy_put_cap_benchmark,
    policy_scale,
)
from strategy_config import STRATEGY_30DTE
from database.trader_database import DB


EXP_DIR = Path(__file__).resolve().parent

WINDOWS = {
    "2020-crash": (date(2020, 2, 18), date(2020, 4, 30)),
    "2021": (date(2021, 1, 1), date(2021, 12, 31)),
    "2022": (date(2022, 1, 1), date(2022, 12, 31)),
    "2023": (date(2023, 1, 1), date(2023, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "dip": (date(2025, 11, 1), date(2026, 4, 24)),
    "22-now": (date(2022, 1, 1), date(2026, 4, 24)),
    "5y": (date(2021, 1, 1), date(2026, 4, 15)),
}

_ORIGINAL_HASH = builtins.hash


def _install_stable_label_hash(seed_offset: int) -> None:
    def stable_hash(value: Any) -> int:
        if isinstance(value, str):
            payload = f"{seed_offset}:{value}".encode("utf-8")
            return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
        return _ORIGINAL_HASH(value)

    builtins.hash = stable_hash


def _restore_hash() -> None:
    builtins.hash = _ORIGINAL_HASH


def _apply_v60_portfolio_env() -> None:
    if STRATEGY_30DTE.MAX_POSITIONS_CALL is None:
        os.environ.pop("MAX_POSITIONS_CALL", None)
    else:
        os.environ["MAX_POSITIONS_CALL"] = str(STRATEGY_30DTE.MAX_POSITIONS_CALL)
    if STRATEGY_30DTE.MAX_POSITIONS_PUT is None:
        os.environ.pop("MAX_POSITIONS_PUT", None)
    else:
        os.environ["MAX_POSITIONS_PUT"] = str(STRATEGY_30DTE.MAX_POSITIONS_PUT)


def _load_candidates(path: Path, max_per_branch: int, include_benchmark: bool, policies: list[str] | None) -> dict[str, SmoothParams]:
    out: dict[str, SmoothParams] = {"baseline": baseline_params()}
    if include_benchmark:
        bench = legacy_put_cap_benchmark()
        out[bench.name] = bench
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_branch: dict[str, int] = {}
    for row in rows:
        name = row.get("policy") or ""
        if policies and name not in policies:
            continue
        try:
            params = SmoothParams(**json.loads(row["params_json"]))
        except Exception:
            continue
        if params.name in out:
            continue
        if policies is None:
            branch = params.branch
            if branch == "benchmark" and not include_benchmark:
                continue
            if by_branch.get(branch, 0) >= max_per_branch:
                continue
            by_branch[branch] = by_branch.get(branch, 0) + 1
        out[params.name] = params
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                cols.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_baseline_rows(path: Path, windows: list[str], n: int) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    wanted = set(windows)
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("policy") == "baseline" and row.get("window") in wanted and int(float(row.get("n") or 0)) == n:
                out.append(dict(row))
    seen = {row["window"] for row in out}
    return out if wanted.issubset(seen) else []


def _refresh_db_connection() -> None:
    timeout_s = int(os.environ.get("MC_DB_READ_TIMEOUT", "120"))
    DB.connect_params["read_timeout"] = max(int(DB.connect_params.get("read_timeout", 0) or 0), timeout_s)
    DB.connect_params["write_timeout"] = max(int(DB.connect_params.get("write_timeout", 0) or 0), timeout_s)
    try:
        DB.close()
    except Exception:
        # A timed-out PyMySQL handle can remain marked open inside Peewee.
        # Reset the thread-local state so the next attempt gets a fresh socket.
        try:
            DB._state.reset()
        except Exception:
            pass
    if not DB.is_closed():
        try:
            DB._state.reset()
        except Exception:
            pass
    DB.connect(reuse_if_open=False)


def _run_window_with_retry(mc, window_name: str, d_start: date, d_end: date, av: AlgorithmVersion, attempts: int = 8) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _refresh_db_connection()
            return mc.run_window(window_name, d_start, d_end, av)
        except Exception as exc:
            last_exc = exc
            print(
                f"  [retry] {window_name} attempt {attempt}/{attempts} failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            try:
                if not DB.is_closed():
                    DB.close()
            except Exception:
                pass
            if attempt < attempts:
                time.sleep(min(90, 10 * attempt))
    assert last_exc is not None
    raise last_exc


def _tape_row(pos: Any, exit_date: date) -> tuple[Any, ...]:
    return (
        pos.sym_id,
        pos.entry_date,
        exit_date,
        pos.side,
        pos.score,
        pos.tier,
        pos.ct,
        pos.ern,
        pos.premium_cost,
        pos.option_pnl,
        pos.outcome,
        pos.entry_value,
        pos.entry_peak,
        pos.entry_dd,
        pos.entry_open_calls,
        pos.entry_open_puts,
    )


def _drawdown_episodes(values: list[float]) -> list[tuple[int, int, float, float]]:
    if not values:
        return []
    peak_idx = trough_idx = 0
    peak_val = trough_val = values[0]
    current_peak_idx = 0
    current_peak = values[0]
    episodes: list[tuple[int, int, float, float]] = []
    for idx, value in enumerate(values):
        if value > current_peak:
            if trough_val < peak_val:
                episodes.append((peak_idx, trough_idx, peak_val, trough_val))
            current_peak_idx = idx
            current_peak = value
            peak_idx = idx
            peak_val = value
            trough_idx = idx
            trough_val = value
        elif value < trough_val:
            peak_idx = current_peak_idx
            peak_val = current_peak
            trough_idx = idx
            trough_val = value
    if trough_val < peak_val:
        episodes.append((peak_idx, trough_idx, peak_val, trough_val))
    episodes.sort(key=lambda e: (e[2] - e[3]) / e[2] if e[2] > 0 else 0.0, reverse=True)
    return episodes[:3]


def make_run_single_sim(params: SmoothParams, daily: dict[date, dict[str, Any]]):
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

        tp_c = sl_c = hard_c = 0
        tp_p = sl_p = hard_p = 0
        trade_rows: list[tuple[Any, ...]] | None = [] if collect_tape else None
        daily_values: list[float] | None = [] if collect_tape else None
        cash_reserves: list[tuple[int, float]] = []
        reserve_pct_sum = reserve_n = 0.0
        max_reserve_pct = 0.0

        cap_call = mc.MAX_POSITIONS_CALL if mc.MAX_POSITIONS_CALL is not None else mc.MAX_POSITIONS
        cap_put = mc.MAX_POSITIONS_PUT if mc.MAX_POSITIONS_PUT is not None else mc.MAX_POSITIONS
        side_capped = (mc.MAX_POSITIONS_CALL is not None) or (mc.MAX_POSITIONS_PUT is not None)

        for day_idx, today in enumerate(trading_days):
            keep = []
            for p in positions:
                base = day_to_idx.get(p.entry_date, -999)
                if base + p.exit_bar <= day_idx:
                    cash += p.premium_cost * (1 + p.option_pnl)
                    if trade_rows is not None:
                        trade_rows.append(_tape_row(p, today))
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
            cash_reserves = [(release_idx, amount) for release_idx, amount in cash_reserves if release_idx > day_idx and amount > 0]

            portfolio_value = cash + sum(p.premium_cost for p in positions)
            reserved_cash = sum(amount for _, amount in cash_reserves)
            if portfolio_value > peak_value:
                peak_value = portfolio_value
            dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0.0
            max_dd = max(max_dd, dd)
            if daily_values is not None:
                daily_values.append(portfolio_value)
            if portfolio_value <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD:
                break

            row = daily.get(today, {})
            _, day_risk = policy_scale("call", row, params, dd)
            hard_put_cap_active = params.benchmark_hard_cap and day_risk.get("benchmark_active", 0.0) > 0.0

            open_syms = {p.sym_id for p in positions}
            call_open = sum(1 for p in positions if p.side == "call")
            put_open = sum(1 for p in positions if p.side == "put")

            reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = mc.alloc_scale_for(reg_mult, is_put=False)
            reg_scale_p = mc.alloc_scale_for(reg_mult, is_put=True)

            def _try_fill_call(sym_id, score, key, ct, ern):
                nonlocal cash, call_open, portfolio_value
                if len(positions) >= mc.MAX_POSITIONS or (side_capped and call_open >= cap_call):
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
                pscale, _ = policy_scale("call", row, params, dd)
                premium_cost = portfolio_value * mc.TIER_ALLOC[tier] * reg_scale_c * dd_scale * pscale
                if premium_cost > cash - reserved_cash or premium_cost <= 0:
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
                nonlocal cash, put_open, portfolio_value, reserved_cash, max_reserve_pct
                if len(positions) >= mc.MAX_POSITIONS or (side_capped and put_open >= cap_put):
                    return False
                if hard_put_cap_active and put_open >= 5:
                    return False
                tier = mc.CT_PUT_TIER if ct == "ct_put" else mc.put_score_to_tier(score)
                pscale, _ = policy_scale("put", row, params, dd)
                base_premium = (
                    portfolio_value
                    * mc.PUT_TIER_ALLOC[tier]
                    * reg_scale_p
                    * mc.saw_put_ucurve_scale(today)
                )
                premium_cost = base_premium * pscale
                required_cash = base_premium if cash_reserve_enabled(params) and pscale < 0.999 else premium_cost
                if required_cash > cash - reserved_cash or premium_cost <= 0:
                    return False
                o = put_outcomes[key]
                outcome, pnl = mc.resolve(o, rng)
                cash -= premium_cost
                if cash_reserve_enabled(params) and pscale < 0.999:
                    reserve = max(0.0, base_premium - premium_cost)
                    if reserve >= 10.0:
                        cash_reserves.append((day_idx + int(o["exit_bar"]), reserve))
                        reserved_cash += reserve
                        max_reserve_pct = max(
                            max_reserve_pct,
                            reserved_cash / portfolio_value if portfolio_value > 0 else 0.0,
                        )
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
                    if len(positions) >= mc.MAX_POSITIONS:
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
                    if len(positions) >= mc.MAX_POSITIONS:
                        break
                    if side_capped and put_open >= cap_put:
                        break
                    if hard_put_cap_active and put_open >= 5:
                        break
                    _try_fill_put(sym_id, score, key, ct)

            if mc.PUT_PRIORITY == "puts_first":
                _do_puts()
                _do_calls()
            else:
                _do_calls()
                _do_puts()
            if portfolio_value > 0:
                reserve_pct_sum += reserved_cash / portfolio_value
                reserve_n += 1
                max_reserve_pct = max(max_reserve_pct, reserved_cash / portfolio_value)

        for p in positions:
            cash += p.premium_cost * (1 + mc.NET_HARD_SELL)
            if trade_rows is not None:
                trade_rows.append(_tape_row(p, trading_days[-1] if trading_days else p.entry_date))
            if p.side == "call":
                hard_c += 1
            else:
                hard_p += 1

        portfolio_value = cash
        final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0.0
        max_dd = max(max_dd, final_dd)
        ct = tp_c + sl_c + hard_c or 1
        pt = tp_p + sl_p + hard_p or 1
        result = {
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
            "avg_reserved_cash_pct": reserve_pct_sum / reserve_n * 100 if reserve_n else 0.0,
            "max_reserved_cash_pct": max_reserve_pct * 100,
        }
        if trade_rows is not None:
            result["_tape"] = trade_rows
            result["_episodes"] = _drawdown_episodes(daily_values or [portfolio_value])
        return result

    return run_single_sim


def _score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_window_policy = {(r["window"], r["policy"]): r for r in rows}
    scored = []
    for row in rows:
        if row["policy"] == "baseline":
            continue
        base = by_window_policy.get((row["window"], "baseline"))
        if not base:
            continue
        scored.append(
            {
                **row,
                "mean_ret_delta": float(row["mean_ret"]) - float(base["mean_ret"]),
                "med_ret_delta": float(row["med_ret"]) - float(base["med_ret"]),
                "worst_dd_delta": float(row["worst_dd"]) - float(base["worst_dd"]),
                "mean_dd_delta": float(row["mean_dd"]) - float(base["mean_dd"]),
                "p_coll_delta": float(row["p_coll"]) - float(base["p_coll"]),
            }
        )
    scored.sort(key=lambda r: (float(r["worst_dd_delta"]), -float(r["mean_ret_delta"])))
    return scored


def _summarize_policy(scored: list[dict[str, Any]], policy: str, windows: list[str]) -> dict[str, Any]:
    rows = [r for r in scored if r["policy"] == policy and r["window"] in windows]
    if not rows:
        return {"policy": policy, "pass": False, "reason": "missing rows"}
    worse = [r for r in rows if float(r["worst_dd_delta"]) > 1.0]
    bad_return_drag = [
        r
        for r in rows
        if r["window"] in {"2024", "2025"} and float(r["mean_ret_delta"]) < 0.0 and float(r["worst_dd_delta"]) > -0.50
    ]
    return {
        "policy": policy,
        "pass": not worse and not bad_return_drag,
        "windows": len(rows),
        "mean_ret_delta_avg": statistics.fmean(float(r["mean_ret_delta"]) for r in rows),
        "worst_dd_delta_max": max(float(r["worst_dd_delta"]) for r in rows),
        "mean_dd_delta_avg": statistics.fmean(float(r["mean_dd_delta"]) for r in rows),
        "p_coll_delta_max": max(float(r["p_coll_delta"]) for r in rows),
        "worse_windows": [r["window"] for r in worse],
        "return_drag_windows": [r["window"] for r in bad_return_drag],
    }


def _write_markdown(path: Path, rows: list[dict[str, Any]], scored: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    policies = [p for p in meta["policies"] if p != "baseline"]
    policy_reads = [_summarize_policy(scored, p, meta["windows"]) for p in policies]
    lines = [
        "# Smooth Controller MC Validation",
        "",
        "Experiment-local stable-seed MC screen. This is not a ship result.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- iterations: {meta['n']}",
        f"- seed_offset: {meta.get('seed_offset', 0)}",
        f"- windows: {', '.join(meta['windows'])}",
        f"- max_positions: {STRATEGY_30DTE.MAX_POSITIONS}, call_cap: {STRATEGY_30DTE.MAX_POSITIONS_CALL}",
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
    for item in policy_reads:
        verdict = "pass" if item["pass"] else "no pass"
        lines.append(
            f"- `{item['policy']}`: {verdict}; avg mean-return delta "
            f"{float(item.get('mean_ret_delta_avg', 0.0)):+.2f}, max worst-DD delta "
            f"{float(item.get('worst_dd_delta_max', 0.0)):+.2f}, avg mean-DD delta "
            f"{float(item.get('mean_dd_delta_avg', 0.0)):+.2f}."
        )
        if item.get("worse_windows"):
            lines.append(f"  Worse-DD windows: {', '.join(item['worse_windows'])}.")
        if item.get("return_drag_windows"):
            lines.append(f"  Return-drag windows: {', '.join(item['return_drag_windows'])}.")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- 2020-crash and 2024 must be stable before broader validation.",
            "- A pass here still requires full Stage 3 N=500x8 validation before implementation.",
            "- The benchmark hard cap is not a smooth candidate.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(EXP_DIR / "daily_state.csv"))
    parser.add_argument("--candidate-csv", type=Path, default=EXP_DIR / "smooth_controller_top_candidates.csv")
    parser.add_argument("--out-dir", default=str(EXP_DIR))
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--n", type=int, default=80)
    parser.add_argument("--windows", default="2020-crash,2024")
    parser.add_argument("--policies", default="")
    parser.add_argument("--max-per-branch", type=int, default=2)
    parser.add_argument("--include-benchmark", action="store_true")
    parser.add_argument("--baseline-csv", type=Path, default=None)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--output-prefix", default="smooth_controller_mc")
    args = parser.parse_args(argv)

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

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / f"{args.output_prefix}_status.json"
    started_at = datetime.now(timezone.utc).isoformat()
    status.write_text(json.dumps({"phase": args.output_prefix, "state": "running", "started_at": started_at}, indent=2), encoding="utf-8")

    daily = _read_daily(Path(args.daily))
    av = AlgorithmVersion.get_by_id(args.version_id) if args.version_id else AlgorithmVersion.get_active_scores_version()
    requested = [p.strip() for p in args.policies.split(",") if p.strip()] or None
    candidates = _load_candidates(args.candidate_csv, args.max_per_branch, args.include_benchmark, requested)
    wanted_windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    baseline_rows = _load_baseline_rows(args.baseline_csv, wanted_windows, args.n) if args.baseline_csv else []
    baseline_ready = bool(baseline_rows)
    wanted_policies = ([] if baseline_ready else ["baseline"]) + [name for name in candidates if name != "baseline"]

    rows: list[dict[str, Any]] = list(baseline_rows)
    original_run_single = mc.run_single_sim
    try:
        total = len(rows) + len(wanted_policies) * len(wanted_windows)
        completed = len(rows)
        for policy_name in wanted_policies:
            params = candidates[policy_name]
            mc.run_single_sim = make_run_single_sim(params, daily)
            print(f"\n[policy] {policy_name}", flush=True)
            for window_name in wanted_windows:
                d_start, d_end = WINDOWS[window_name]
                t0 = time.time()
                row = dict(_run_window_with_retry(mc, window_name, d_start, d_end, av)["seeded"])
                row["window"] = window_name
                row["policy"] = policy_name
                row["branch"] = params.branch
                row["n"] = args.n
                row["elapsed_s"] = round(time.time() - t0, 2)
                row["params"] = json.dumps(asdict(params), sort_keys=True)
                rows.append(row)
                completed += 1
                out_csv = out_dir / f"{args.output_prefix}.csv"
                ranked_csv = out_dir / f"{args.output_prefix}_ranked.csv"
                _write_csv(out_csv, rows)
                scored = _score_rows(rows)
                _write_csv(ranked_csv, scored)
                status.write_text(
                    json.dumps(
                        {
                            "phase": args.output_prefix,
                            "state": "running",
                            "started_at": started_at,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "completed": completed,
                            "total": total,
                            "current_policy": policy_name,
                            "current_window": window_name,
                            "best": scored[0] if scored else None,
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
    out_csv = out_dir / f"{args.output_prefix}.csv"
    ranked_csv = out_dir / f"{args.output_prefix}_ranked.csv"
    out_md = out_dir / f"{args.output_prefix}_summary.md"
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
    print(f"Wrote {out_csv}", flush=True)
    print(f"Wrote {ranked_csv}", flush=True)
    print(f"Wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
