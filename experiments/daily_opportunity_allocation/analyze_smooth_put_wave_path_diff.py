"""Trade-path decomposition for smooth Market Wave put-throttle candidates.

Experiment-only. Runs identical-seed MC trade tapes for baseline and one smooth
put-wave candidate, then separates sizing effects on shared trades from
removed/added path effects. It does not write scores, strategy constants, or
algorithm metadata.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.daily_opportunity_allocation.analyze_score_dampen_path_diff import (
    _clear_label_artifacts,
    _copy_tape_artifacts,
    _install_robust_tape_dump,
    _read_episodes,
    _read_tape,
)
from experiments.daily_opportunity_allocation.smooth_controller_sweep import (
    SmoothParams,
    _float,
    policy_scale,
)
from experiments.daily_opportunity_allocation.validate_smooth_controller_mc import (
    WINDOWS,
    _apply_v60_portfolio_env,
    _install_stable_label_hash,
    _load_candidates,
    _read_daily,
    _restore_hash,
    _run_window_with_retry,
    _score_rows,
    _write_csv,
    make_run_single_sim,
)
from strategy_config import STRATEGY_30DTE


def _write_status(out_dir: Path, started_at: str, **payload: Any) -> None:
    body = {
        "phase": "smooth_put_wave_path_diff",
        "state": "running",
        "started_at": started_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "run_log": str(out_dir / "run.log"),
        **payload,
    }
    tmp = out_dir / "smooth_put_wave_path_diff_status.json.tmp"
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    tmp.replace(out_dir / "smooth_put_wave_path_diff_status.json")


def _prep_tape(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    out["seed"] = out["seed"].astype(str)
    out["sym_id"] = out["sym_id"].astype(str)
    out["entry_date"] = out["entry_date"].astype(str).str.slice(0, 10)
    out["exit_date"] = out["exit_date"].astype(str).str.slice(0, 10)
    out["side"] = out["side"].astype(str)
    out["score"] = pd.to_numeric(out["score"], errors="coerce")
    out["premium_cost"] = pd.to_numeric(out["premium_cost"], errors="coerce").fillna(0.0)
    out["option_pnl"] = pd.to_numeric(out["option_pnl"], errors="coerce").fillna(0.0)
    out["dollar_pnl"] = out["premium_cost"] * out["option_pnl"]
    out["month"] = out["entry_date"].str.slice(0, 7)
    out["trade_key"] = (
        out["seed"].astype(str)
        + "|"
        + out["sym_id"]
        + "|"
        + out["entry_date"]
        + "|"
        + out["side"]
    )
    return out


def _daily_context(entry_date: str, daily: dict[Any, dict[str, Any]], params: SmoothParams) -> dict[str, Any]:
    from datetime import date

    try:
        d = date.fromisoformat(entry_date[:10])
    except ValueError:
        return {}
    row = daily.get(d, {})
    running_dd = _float(row.get("dd"))
    put_scale, risk = policy_scale("put", row, params, running_dd)
    return {
        "policy_put_scale": put_scale,
        "risk_recent_weak": risk.get("recent_weak", 0.0),
        "risk_weak_tp": risk.get("weak_tp", 0.0),
        "risk_weak_pnl": risk.get("weak_pnl", 0.0),
        "risk_put_overhang": risk.get("put_overhang", 0.0),
        "risk_wave_divergence": risk.get("wave_divergence", 0.0),
        "prev5_entry_tp_rate": _float(row.get("prev5_entry_tp_rate")),
        "prev5_entry_avg_pnl_pct": _float(row.get("prev5_entry_avg_pnl_pct")),
        "open_put_n": _float(row.get("open_put_n")),
        "filled_put_n": _float(row.get("filled_put_n")),
        "open_put_share": _float(row.get("open_put_share")),
        "post_call_primary_alloc_demand": _float(row.get("post_call_primary_alloc_demand")),
        "post_total_primary_alloc_demand": _float(row.get("post_total_primary_alloc_demand")),
        "breadth_pct_above_ema50": _float(row.get("breadth_pct_above_ema50")),
        "market_wave_signed": _float(row.get("breadth_sector_etf_market_wave_signed")),
        "baseline_daily_dd": running_dd,
    }


def _scope_summary(
    window: str,
    policy: str,
    scope: str,
    shared: pd.DataFrame,
    removed: pd.DataFrame,
    added: pd.DataFrame,
) -> dict[str, Any]:
    if scope != "all":
        shared = shared[shared["side"] == scope]
        removed = removed[removed["side"] == scope]
        added = added[added["side"] == scope]
    row = {"window": window, "policy": policy, "scope": scope}
    row["shared_trades"] = int(len(shared))
    row["shared_scaled_down"] = int((shared["premium_delta"] < -1e-6).sum()) if not shared.empty else 0
    row["shared_scaled_up"] = int((shared["premium_delta"] > 1e-6).sum()) if not shared.empty else 0
    row["shared_premium_delta"] = float(shared["premium_delta"].sum()) if not shared.empty else 0.0
    row["shared_dollar_pnl_delta"] = float(shared["dollar_pnl_delta"].sum()) if not shared.empty else 0.0
    row["shared_base_dollar_pnl"] = float(shared["dollar_pnl_base"].sum()) if not shared.empty else 0.0
    row["shared_candidate_dollar_pnl"] = float(shared["dollar_pnl_candidate"].sum()) if not shared.empty else 0.0
    row["shared_base_avg_option_pnl"] = float(shared["option_pnl_base"].mean()) if not shared.empty else 0.0
    row["shared_candidate_avg_option_pnl"] = float(shared["option_pnl_candidate"].mean()) if not shared.empty else 0.0
    row["removed_trades"] = int(len(removed))
    row["removed_dollar_pnl"] = float(removed["dollar_pnl"].sum()) if not removed.empty else 0.0
    row["removed_avg_option_pnl"] = float(removed["option_pnl"].mean()) if not removed.empty else 0.0
    row["added_trades"] = int(len(added))
    row["added_dollar_pnl"] = float(added["dollar_pnl"].sum()) if not added.empty else 0.0
    row["added_avg_option_pnl"] = float(added["option_pnl"].mean()) if not added.empty else 0.0
    row["net_trade_dollar_pnl_delta"] = (
        row["shared_dollar_pnl_delta"] + row["added_dollar_pnl"] - row["removed_dollar_pnl"]
    )
    return row


def _trade_diff(
    baseline_tape: pd.DataFrame,
    candidate_tape: pd.DataFrame,
    baseline_ep: pd.DataFrame,
    candidate_ep: pd.DataFrame,
    window: str,
    policy: str,
    daily: dict[Any, dict[str, Any]],
    params: SmoothParams,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    base = _prep_tape(baseline_tape)
    cand = _prep_tape(candidate_tape)
    base_keys = set(base["trade_key"]) if not base.empty else set()
    cand_keys = set(cand["trade_key"]) if not cand.empty else set()
    removed = base[base["trade_key"].isin(base_keys - cand_keys)].copy() if not base.empty else base
    added = cand[cand["trade_key"].isin(cand_keys - base_keys)].copy() if not cand.empty else cand
    shared = base.merge(cand, on="trade_key", how="inner", suffixes=("_base", "_candidate"))
    if not shared.empty:
        shared["seed"] = shared["seed_base"]
        shared["sym_id"] = shared["sym_id_base"]
        shared["entry_date"] = shared["entry_date_base"]
        shared["month"] = shared["month_base"]
        shared["side"] = shared["side_base"]
        shared["premium_delta"] = shared["premium_cost_candidate"] - shared["premium_cost_base"]
        shared["dollar_pnl_delta"] = shared["dollar_pnl_candidate"] - shared["dollar_pnl_base"]
        shared["option_pnl_delta"] = shared["option_pnl_candidate"] - shared["option_pnl_base"]
    else:
        for col in ("seed", "sym_id", "entry_date", "month", "side", "premium_delta", "dollar_pnl_delta", "option_pnl_delta"):
            shared[col] = pd.Series(dtype="object")

    summary_rows = [
        _scope_summary(window, policy, scope, shared, removed, added)
        for scope in ("all", "call", "put")
    ]

    date_rows: list[dict[str, Any]] = []
    all_dates = sorted(
        set(shared["entry_date"].tolist() if not shared.empty else [])
        | set(removed["entry_date"].tolist() if not removed.empty else [])
        | set(added["entry_date"].tolist() if not added.empty else [])
    )
    for entry_date in all_dates:
        s = shared[shared["entry_date"] == entry_date] if not shared.empty else shared
        r = removed[removed["entry_date"] == entry_date] if not removed.empty else removed
        a = added[added["entry_date"] == entry_date] if not added.empty else added
        row: dict[str, Any] = {
            "window": window,
            "policy": policy,
            "entry_date": entry_date,
            "month": entry_date[:7],
            "shared_trades": int(len(s)),
            "shared_puts": int((s["side"] == "put").sum()) if not s.empty else 0,
            "shared_put_scaled_down": int(((s["side"] == "put") & (s["premium_delta"] < -1e-6)).sum()) if not s.empty else 0,
            "shared_put_premium_delta": float(s.loc[s["side"] == "put", "premium_delta"].sum()) if not s.empty else 0.0,
            "shared_put_dollar_pnl_delta": float(s.loc[s["side"] == "put", "dollar_pnl_delta"].sum()) if not s.empty else 0.0,
            "shared_call_dollar_pnl_delta": float(s.loc[s["side"] == "call", "dollar_pnl_delta"].sum()) if not s.empty else 0.0,
            "removed_trades": int(len(r)),
            "removed_puts": int((r["side"] == "put").sum()) if not r.empty else 0,
            "removed_dollar_pnl": float(r["dollar_pnl"].sum()) if not r.empty else 0.0,
            "added_trades": int(len(a)),
            "added_puts": int((a["side"] == "put").sum()) if not a.empty else 0,
            "added_dollar_pnl": float(a["dollar_pnl"].sum()) if not a.empty else 0.0,
        }
        row["net_trade_dollar_pnl_delta"] = (
            row["shared_put_dollar_pnl_delta"]
            + row["shared_call_dollar_pnl_delta"]
            + row["added_dollar_pnl"]
            - row["removed_dollar_pnl"]
        )
        row.update(_daily_context(entry_date, daily, params))
        date_rows.append(row)

    month_rows = []
    if date_rows:
        by_month = pd.DataFrame(date_rows)
        numeric_cols = [
            c
            for c in by_month.columns
            if c not in {"window", "policy", "entry_date", "month"}
            and pd.api.types.is_numeric_dtype(by_month[c])
        ]
        sums = {
            "shared_trades",
            "shared_puts",
            "shared_put_scaled_down",
            "shared_put_premium_delta",
            "shared_put_dollar_pnl_delta",
            "shared_call_dollar_pnl_delta",
            "removed_trades",
            "removed_puts",
            "removed_dollar_pnl",
            "added_trades",
            "added_puts",
            "added_dollar_pnl",
            "net_trade_dollar_pnl_delta",
        }
        for month, g in by_month.groupby("month", sort=True):
            row = {"window": window, "policy": policy, "month": month, "days": int(g["entry_date"].nunique())}
            for col in numeric_cols:
                row[col] = float(g[col].sum()) if col in sums else float(g[col].mean())
            month_rows.append(row)

    top_rows: list[dict[str, Any]] = []
    if not shared.empty:
        for _, r in shared.sort_values("dollar_pnl_delta").head(80).iterrows():
            top_rows.append(
                {
                    "window": window,
                    "policy": policy,
                    "kind": "worst_shared_delta",
                    "seed": r["seed"],
                    "sym_id": r["sym_id"],
                    "entry_date": r["entry_date"],
                    "side": r["side"],
                    "score": r.get("score_base"),
                    "premium_base": r["premium_cost_base"],
                    "premium_candidate": r["premium_cost_candidate"],
                    "premium_delta": r["premium_delta"],
                    "option_pnl_base": r["option_pnl_base"],
                    "option_pnl_candidate": r["option_pnl_candidate"],
                    "dollar_pnl_delta": r["dollar_pnl_delta"],
                }
            )
        for _, r in shared.sort_values("dollar_pnl_delta", ascending=False).head(80).iterrows():
            top_rows.append(
                {
                    "window": window,
                    "policy": policy,
                    "kind": "best_shared_delta",
                    "seed": r["seed"],
                    "sym_id": r["sym_id"],
                    "entry_date": r["entry_date"],
                    "side": r["side"],
                    "score": r.get("score_base"),
                    "premium_base": r["premium_cost_base"],
                    "premium_candidate": r["premium_cost_candidate"],
                    "premium_delta": r["premium_delta"],
                    "option_pnl_base": r["option_pnl_base"],
                    "option_pnl_candidate": r["option_pnl_candidate"],
                    "dollar_pnl_delta": r["dollar_pnl_delta"],
                }
            )

    episode_rows: list[dict[str, Any]] = []
    if not baseline_ep.empty and not candidate_ep.empty:
        b0 = baseline_ep[baseline_ep["rank"].astype(int) == 0].copy()
        c0 = candidate_ep[candidate_ep["rank"].astype(int) == 0].copy()
        if not b0.empty and not c0.empty:
            b0["seed"] = b0["seed"].astype(str)
            c0["seed"] = c0["seed"].astype(str)
            merged = b0.merge(c0, on="seed", how="inner", suffixes=("_baseline", "_candidate"))
            if not merged.empty:
                merged["magnitude_baseline"] = pd.to_numeric(merged["magnitude_baseline"], errors="coerce")
                merged["magnitude_candidate"] = pd.to_numeric(merged["magnitude_candidate"], errors="coerce")
                merged["magnitude_delta"] = merged["magnitude_candidate"] - merged["magnitude_baseline"]
                for _, er in merged.sort_values("magnitude_delta", ascending=False).head(80).iterrows():
                    episode_rows.append(
                        {
                            "window": window,
                            "policy": policy,
                            "seed": er["seed"],
                            "baseline_start": er.get("start_date_baseline"),
                            "baseline_trough": er.get("trough_date_baseline"),
                            "candidate_start": er.get("start_date_candidate"),
                            "candidate_trough": er.get("trough_date_candidate"),
                            "baseline_magnitude_pct": float(er["magnitude_baseline"] * 100),
                            "candidate_magnitude_pct": float(er["magnitude_candidate"] * 100),
                            "delta_pct": float(er["magnitude_delta"] * 100),
                        }
                    )
    return summary_rows, date_rows, month_rows, top_rows + episode_rows


def _write_markdown(path: Path, rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]], month_rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    scored = _score_rows(rows)
    summary = pd.DataFrame(summary_rows)
    lines = [
        "# Smooth Put-Wave Path Diff",
        "",
        "Experiment-local decomposition of baseline vs smooth put-wave MC trade tapes.",
        "",
        "## Metadata",
        "",
        f"- generated_at: {meta['generated_at']}",
        f"- version: v{meta['version_id']} {meta.get('commit')}",
        f"- iterations: {meta['n']}",
        f"- windows: {', '.join(meta['windows'])}",
        f"- policies: {', '.join(meta['policies'])}",
        "",
        "## MC Window Deltas",
        "",
        "| window | policy | mean ret d | med ret d | worst DD d | mean DD d | put shared pnl d | added pnl | removed pnl |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in scored:
        if row["policy"] == "baseline":
            continue
        srow = {}
        if not summary.empty:
            match = summary[(summary["window"] == row["window"]) & (summary["policy"] == row["policy"]) & (summary["scope"] == "put")]
            if not match.empty:
                srow = match.iloc[0].to_dict()
        lines.append(
            f"| {row['window']} | `{row['policy']}` | {float(row['mean_ret_delta']):+.2f} | "
            f"{float(row['med_ret_delta']):+.2f} | {float(row['worst_dd_delta']):+.2f} | "
            f"{float(row['mean_dd_delta']):+.2f} | {float(srow.get('shared_dollar_pnl_delta', 0.0)):+.0f} | "
            f"{float(srow.get('added_dollar_pnl', 0.0)):+.0f} | {float(srow.get('removed_dollar_pnl', 0.0)):+.0f} |"
        )

    lines.extend(["", "## Monthly Put Sizing Read", ""])
    lines.append("| window | month | scaled puts | put premium d | put pnl d | net trade pnl d | put scale | wave | weak | overhang |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in sorted(month_rows, key=lambda r: (r["window"], r["month"])):
        lines.append(
            f"| {row['window']} | {row['month']} | {int(row.get('shared_put_scaled_down', 0) or 0)} | "
            f"{float(row.get('shared_put_premium_delta', 0.0)):+.0f} | "
            f"{float(row.get('shared_put_dollar_pnl_delta', 0.0)):+.0f} | "
            f"{float(row.get('net_trade_dollar_pnl_delta', 0.0)):+.0f} | "
            f"{float(row.get('policy_put_scale', 1.0)):.3f} | "
            f"{float(row.get('risk_wave_divergence', 0.0)):.2f} | "
            f"{float(row.get('risk_recent_weak', 0.0)):.2f} | "
            f"{float(row.get('risk_put_overhang', 0.0)):.2f} |"
        )

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- `put shared pnl d` is the same-key put-trade P&L delta; path divergence can still change stochastic fills.",
            "- `added pnl` and `removed pnl` are secondary path effects from freed cash/slots/tie-breaks.",
            "- A shippable smooth put wave should make 2024 put shared P&L less negative without cutting profitable 2025 puts.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, default=ROOT / ".codex" / "runs" / "v60_daily_opportunity_smooth_20260519_082415" / "daily_state.csv")
    parser.add_argument("--candidate-csv", type=Path, default=ROOT / ".codex" / "runs" / "v60_wave_put_strict_sweep_20260520_120809" / "smooth_controller_ranked.csv")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version-id", type=int, default=60)
    parser.add_argument("--n", type=int, default=240)
    parser.add_argument("--windows", default="2024,2025")
    parser.add_argument("--policies", default="wave_put_divergence_strict_0298")
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--label-prefix", default="smooth_put_wave_pathdiff")
    parser.add_argument("--db-attempts", type=int, default=8)
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    _write_status(args.out_dir, started_at, completed=0, total=0, current="init")

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_MP"] = "1"
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "1"
    os.environ["REALLOC_STRATEGY"] = ""
    _apply_v60_portfolio_env()
    _install_stable_label_hash(args.seed_offset)

    import monte_carlo as mc

    mc.TRADE_TAPE_ENABLED = True
    _install_robust_tape_dump(mc)
    mc.MAX_POSITIONS_CALL = STRATEGY_30DTE.MAX_POSITIONS_CALL
    mc.MAX_POSITIONS_PUT = STRATEGY_30DTE.MAX_POSITIONS_PUT

    daily = _read_daily(args.daily)
    av = AlgorithmVersion.get_by_id(args.version_id)
    requested = [p.strip() for p in args.policies.split(",") if p.strip()]
    candidates = _load_candidates(args.candidate_csv, max_per_branch=999, include_benchmark=False, policies=requested)
    missing = [p for p in requested if p not in candidates]
    if missing:
        raise KeyError(f"Unknown policies in candidate csv: {missing}")
    wanted_policies = ["baseline"] + requested
    wanted_windows = [w.strip() for w in args.windows.split(",") if w.strip()]

    rows: list[dict[str, Any]] = []
    artifact_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    date_rows: list[dict[str, Any]] = []
    month_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    original_run_single = mc.run_single_sim
    total = len(wanted_windows) * len(wanted_policies)
    completed = 0
    try:
        for window_name in wanted_windows:
            d_start, d_end = WINDOWS[window_name]
            label = f"{args.label_prefix}_{window_name}"
            baseline_tape: pd.DataFrame | None = None
            baseline_ep: pd.DataFrame | None = None
            for policy_name in wanted_policies:
                params = candidates[policy_name]
                mc.run_single_sim = make_run_single_sim(params, daily)
                print(f"\n[path-diff] window={window_name} policy={policy_name} label={label}", flush=True)
                _clear_label_artifacts(label)
                t0 = time.time()
                result = dict(_run_window_with_retry(mc, label, d_start, d_end, av, attempts=args.db_attempts)["seeded"])
                result["window"] = window_name
                result["policy"] = policy_name
                result["branch"] = params.branch
                result["n"] = args.n
                result["elapsed_s"] = round(time.time() - t0, 2)
                result["params"] = json.dumps(asdict(params), sort_keys=True)
                rows.append(result)
                copied = _copy_tape_artifacts(label, policy_name, window_name, args.out_dir)
                artifact_rows.append({"window": window_name, "policy": policy_name, **copied})
                if not copied["tape"] or not copied["episodes"]:
                    raise RuntimeError(f"Missing tape artifact for {policy_name}/{window_name}: {copied}")
                tape = _read_tape(Path(copied["tape"]))
                episodes = _read_episodes(Path(copied["episodes"]))
                if policy_name == "baseline":
                    baseline_tape = tape
                    baseline_ep = episodes
                else:
                    if baseline_tape is None or baseline_ep is None:
                        raise RuntimeError("Baseline tape must run before policy tapes")
                    s_rows, d_rows, m_rows, details = _trade_diff(
                        baseline_tape,
                        tape,
                        baseline_ep,
                        episodes,
                        window_name,
                        policy_name,
                        daily,
                        params,
                    )
                    summary_rows.extend(s_rows)
                    date_rows.extend(d_rows)
                    month_rows.extend(m_rows)
                    detail_rows.extend(details)

                completed += 1
                _write_csv(args.out_dir / "smooth_put_wave_path_diff_mc.csv", rows)
                _write_csv(args.out_dir / "smooth_put_wave_path_diff_ranked.csv", _score_rows(rows))
                _write_csv(args.out_dir / "smooth_put_wave_trade_delta_summary.csv", summary_rows)
                _write_csv(args.out_dir / "smooth_put_wave_trade_delta_by_date.csv", date_rows)
                _write_csv(args.out_dir / "smooth_put_wave_trade_delta_by_month.csv", month_rows)
                _write_csv(args.out_dir / "smooth_put_wave_trade_delta_details.csv", detail_rows)
                _write_csv(args.out_dir / "smooth_put_wave_path_diff_artifacts.csv", artifact_rows)
                scored_rows = _score_rows(rows)
                _write_status(
                    args.out_dir,
                    started_at,
                    completed=completed,
                    total=total,
                    current=f"{policy_name}/{window_name}",
                    best=scored_rows[0] if scored_rows else None,
                )
                print(
                    f"  {policy_name}/{window_name}: mean={result['mean_ret']:+.1f}% "
                    f"worstDD={result['worst_dd']:.1f}% meanDD={result['mean_dd']:.1f}% "
                    f"elapsed={result['elapsed_s']:.0f}s",
                    flush=True,
                )
    finally:
        mc.run_single_sim = original_run_single
        _restore_hash()

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version_id": int(av.id),
        "commit": getattr(av, "git_commit", None),
        "n": args.n,
        "windows": wanted_windows,
        "policies": wanted_policies,
        "daily": str(args.daily),
        "candidate_csv": str(args.candidate_csv),
    }
    out_md = args.out_dir / "smooth_put_wave_path_diff_summary.md"
    _write_markdown(out_md, rows, summary_rows, month_rows, meta)
    done = {
        "phase": "done",
        "state": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "mc": str(args.out_dir / "smooth_put_wave_path_diff_mc.csv"),
            "ranked": str(args.out_dir / "smooth_put_wave_path_diff_ranked.csv"),
            "summary": str(args.out_dir / "smooth_put_wave_trade_delta_summary.csv"),
            "by_date": str(args.out_dir / "smooth_put_wave_trade_delta_by_date.csv"),
            "by_month": str(args.out_dir / "smooth_put_wave_trade_delta_by_month.csv"),
            "details": str(args.out_dir / "smooth_put_wave_trade_delta_details.csv"),
            "artifacts": str(args.out_dir / "smooth_put_wave_path_diff_artifacts.csv"),
            "markdown": str(out_md),
        },
    }
    _write_status(args.out_dir, started_at, state="done", completed=completed, total=total, current="done")
    (args.out_dir / "done.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
    print(f"Wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
