"""Monte Carlo validation for Bayesian daily alpha RC seeds.

Experiment-local only. This patches `monte_carlo.run_single_sim` in-process so
Bayesian AlphaParams can scale allocation, MaxPos, and put caps at the actual
fill point. It never writes scores or strategy constants and disables MC DB
persistence by default.
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
from experiments.daily_opportunity_allocation.bayes_alpha_sweep import (
    AlphaParams,
    _policy_scale,
    _read_daily,
    baseline_params,
)


EXP_DIR = Path(__file__).resolve().parent

WINDOWS = {
    "2020-crash": (date(2020, 2, 18), date(2020, 4, 30)),
    "2022": (date(2022, 1, 1), date(2022, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "22-now": (date(2022, 1, 1), date(2026, 4, 24)),
    "5y": (date(2021, 1, 1), date(2026, 4, 15)),
}

_ORIGINAL_HASH = builtins.hash


def _install_stable_label_hash(seed_offset: int) -> None:
    """Make monte_carlo.run_window label seeds reproducible in this process.

    monte_carlo uses `hash(label)` to derive per-window seeds. Python randomizes
    string hashes per process, so experiment-only validators need a stable
    replacement for explicit `hash(str)` calls.
    """

    def stable_hash(value: Any) -> int:
        if isinstance(value, str):
            payload = f"{seed_offset}:{value}".encode("utf-8")
            return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
        return _ORIGINAL_HASH(value)

    builtins.hash = stable_hash


def _restore_hash() -> None:
    builtins.hash = _ORIGINAL_HASH


def candidate_params() -> dict[str, AlphaParams]:
    return {
        "baseline": baseline_params(),
        "bayes_rc_seed": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=2,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
        ),
        "bayes_raw_top_utility": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.60,
            put_extra_throttle=0.00,
            slot_cut=6,
            put_cap=0,
            recent_tp_floor=0.5133,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.0,
            reentry_dd=0.12,
            reentry_call_trigger=0.70,
        ),
        "bayes_seed_balanced": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.70,
            put_extra_throttle=0.00,
            slot_cut=2,
            put_cap=5,
            recent_tp_floor=0.5133,
            recent_pnl_floor=0.0674,
            open_put_trigger=7.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.78,
            breadth_floor=51.27,
            wave_threshold=3.72,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.0,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.40,
        ),
        "bayes_seed_no_reentry": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=2,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.0,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
        ),
        "bayes_seed_no_put_cap": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=2,
            put_cap=0,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
        ),
        "bayes_seed_no_slot_cut": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=0,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
        ),
        "bayes_seed_soft": AlphaParams(
            throttle_strength=0.18,
            floor_scale=0.80,
            put_extra_throttle=0.08,
            slot_cut=0,
            put_cap=7,
            recent_tp_floor=0.5133,
            recent_pnl_floor=0.0674,
            open_put_trigger=7.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.78,
            breadth_floor=51.27,
            wave_threshold=3.72,
            put_weight=1.0,
            scarce_weight=0.5,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.40,
        ),
        "bayes_seed_benign_guard": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=0,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
            benign_breadth_floor=58.0,
            benign_dd_ceiling=0.08,
        ),
        "bayes_seed_high_breadth_guard": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=0,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
            benign_breadth_floor=65.0,
            benign_dd_ceiling=0.08,
        ),
        "bayes_seed_wide_benign_guard": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=0,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
            benign_breadth_floor=45.0,
            benign_dd_ceiling=0.50,
        ),
        "bayes_seed_severe_dd_only": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=0,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
            benign_breadth_floor=0.0,
            benign_dd_ceiling=0.50,
        ),
        "bayes_seed_crash_breadth_only": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=0,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
            benign_breadth_floor=30.0,
            benign_dd_ceiling=1.0,
        ),
        "bayes_seed_panic_breadth_only": AlphaParams(
            throttle_strength=0.26,
            floor_scale=0.50,
            put_extra_throttle=0.16,
            slot_cut=0,
            put_cap=3,
            recent_tp_floor=0.50,
            recent_pnl_floor=0.0674,
            open_put_trigger=9.0,
            sparse_call_trigger=0.20,
            sparse_total_trigger=0.52,
            breadth_floor=51.27,
            wave_threshold=10.0,
            put_weight=1.0,
            scarce_weight=1.0,
            wave_weight=1.5,
            reentry_strength=0.12,
            reentry_dd=0.0956,
            reentry_call_trigger=0.70,
            benign_breadth_floor=15.0,
            benign_dd_ceiling=1.0,
        ),
    }


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


def _dynamic_max_positions(base: int, params: AlphaParams, risk: dict[str, float]) -> int:
    if params.slot_cut <= 0:
        return base
    return max(6, min(base, base - round(params.slot_cut * risk["gross_risk"])))


def make_run_single_sim(params: AlphaParams, daily: dict[date, dict[str, Any]]):
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

        cap_call = mc.MAX_POSITIONS_CALL if mc.MAX_POSITIONS_CALL is not None else mc.MAX_POSITIONS
        cap_put = mc.MAX_POSITIONS_PUT if mc.MAX_POSITIONS_PUT is not None else mc.MAX_POSITIONS
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
            _, day_risk = _policy_scale("call", row, params, dd)
            max_positions = _dynamic_max_positions(int(mc.MAX_POSITIONS), params, day_risk)
            put_cap = params.put_cap if params.put_cap > 0 and day_risk["put_risk"] >= 0.50 else 0

            open_syms = {p.sym_id for p in positions}
            call_open = sum(1 for p in positions if p.side == "call")
            put_open = sum(1 for p in positions if p.side == "put")

            reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = mc.alloc_scale_for(reg_mult, is_put=False)
            reg_scale_p = mc.alloc_scale_for(reg_mult, is_put=True)

            def _try_fill_call(sym_id, score, key, ct, ern):
                nonlocal cash, call_open, portfolio_value
                if len(positions) >= max_positions or (side_capped and call_open >= cap_call):
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
                policy_scale, _ = _policy_scale("call", row, params, dd)
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
                if len(positions) >= max_positions or (side_capped and put_open >= cap_put):
                    return False
                if put_cap > 0 and put_open >= put_cap:
                    return False
                tier = mc.CT_PUT_TIER if ct == "ct_put" else mc.put_score_to_tier(score)
                policy_scale, _ = _policy_scale("put", row, params, dd)
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
                    if put_cap > 0 and put_open >= put_cap:
                        break
                    _try_fill_put(sym_id, score, key, ct)

            if mc.PUT_PRIORITY == "puts_first":
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
    badly_worse = [r for r in rows if float(r["mean_dd_delta"]) > 1.0 and float(r["mean_ret_delta"]) < 0.0]
    return {
        "policy": policy,
        "pass": not worse and not badly_worse,
        "windows": len(rows),
        "mean_ret_delta_avg": statistics.fmean(float(r["mean_ret_delta"]) for r in rows),
        "worst_dd_delta_max": max(float(r["worst_dd_delta"]) for r in rows),
        "mean_dd_delta_avg": statistics.fmean(float(r["mean_dd_delta"]) for r in rows),
        "p_coll_delta_max": max(float(r["p_coll_delta"]) for r in rows),
        "worse_windows": [r["window"] for r in worse],
    }


def _write_markdown(
    path: Path,
    rows: list[dict[str, Any]],
    scored: list[dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    policies = [p for p in meta["policies"] if p != "baseline"]
    policy_reads = [_summarize_policy(scored, p, meta["windows"]) for p in policies]
    lines = [
        "# Bayesian Alpha MC Validation",
        "",
        "Experiment-local MC screen. This is not a ship result.",
        "",
        "## Metadata",
        "",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- generated_at: {meta['generated_at']}",
        f"- iterations: {meta['n']}",
        f"- seed_offset: {meta.get('seed_offset', 0)}",
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
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Promote only if named windows do not worsen worst DD materially.",
            "- Favor DD reduction over small compound-return deltas.",
            "- A pass here still needs a larger N Stage 3 validation before implementation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(EXP_DIR / "daily_state.csv"))
    parser.add_argument("--out-dir", default=str(EXP_DIR))
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--n", type=int, default=80)
    parser.add_argument("--windows", default="2020-crash,2022,2024,2025,22-now,5y")
    parser.add_argument("--policies", default="baseline,bayes_rc_seed,bayes_raw_top_utility")
    parser.add_argument("--seed-offset", type=int, default=0)
    args = parser.parse_args(argv)

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_MP"] = "1"
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "0"
    os.environ["REALLOC_STRATEGY"] = ""
    _install_stable_label_hash(args.seed_offset)

    import monte_carlo as mc

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / "bayes_alpha_mc_status.json"
    started_at = datetime.now(timezone.utc).isoformat()
    status.write_text(
        json.dumps({"phase": "bayes_alpha_mc", "state": "running", "started_at": started_at}, indent=2),
        encoding="utf-8",
    )

    daily = _read_daily(Path(args.daily))
    av = AlgorithmVersion.get_by_id(args.version_id) if args.version_id else AlgorithmVersion.get_active_scores_version()
    policies = candidate_params()
    wanted_policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    wanted_windows = [w.strip() for w in args.windows.split(",") if w.strip()]

    rows: list[dict[str, Any]] = []
    original_run_single = mc.run_single_sim
    try:
        total = len(wanted_policies) * len(wanted_windows)
        completed = 0
        for policy_name in wanted_policies:
            params = policies[policy_name]
            mc.run_single_sim = make_run_single_sim(params, daily)
            print(f"\n[policy] {policy_name}", flush=True)
            for window_name in wanted_windows:
                d_start, d_end = WINDOWS[window_name]
                t0 = time.time()
                row = dict(mc.run_window(window_name, d_start, d_end, av)["seeded"])
                row["window"] = window_name
                row["policy"] = policy_name
                row["n"] = args.n
                row["elapsed_s"] = round(time.time() - t0, 2)
                row["params"] = json.dumps(asdict(params), sort_keys=True)
                rows.append(row)
                completed += 1
                _write_csv(out_dir / "bayes_alpha_mc.csv", rows)
                scored = _score_rows(rows)
                _write_csv(out_dir / "bayes_alpha_mc_ranked.csv", scored)
                status.write_text(
                    json.dumps(
                        {
                            "phase": "bayes_alpha_mc",
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
    out_csv = out_dir / "bayes_alpha_mc.csv"
    ranked_csv = out_dir / "bayes_alpha_mc_ranked.csv"
    out_md = out_dir / "bayes_alpha_mc_summary.md"
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
    (out_dir / "done.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
    print(f"Wrote {out_csv}", flush=True)
    print(f"Wrote {ranked_csv}", flush=True)
    print(f"Wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
