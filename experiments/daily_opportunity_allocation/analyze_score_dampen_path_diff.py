"""Trade-path decomposition for v60 score-dampen proxy candidates.

Experiment-only. Runs identical-seed MC trade tapes for baseline and candidate
market-structure call dampeners, then separates direct blocked trades from
replacement/secondary path effects. No scores, strategy config, or algorithm
metadata are written.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.daily_opportunity_allocation.validate_score_dampen_proxy_mc import (
    ProxyPolicy,
    _as_date,
    _float,
    _read_wave,
    _should_block,
    make_load_signals,
)
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
DEFAULT_SIGNALS = ROOT / ".codex" / "runs" / "v60_extra_call_wave_20260519_215034" / "v60_call_signal_dataset.csv"
DD_LEDGER_DIR = ROOT / ".cache" / "dd_ledger"


@dataclass(frozen=True)
class PathPolicy:
    name: str
    proxy: ProxyPolicy
    max_breadth_ema50: float | None = None
    max_mcclellan: float | None = None
    max_trend: float | None = None
    min_sector_crash: float | None = None
    note: str = ""


def path_policies() -> dict[str, PathPolicy]:
    """Candidate policies to decompose.

    The first candidate is the best precision lead from the previous MC pass.
    The second is intentionally narrower: acute global breadth washout plus
    sector-crash pressure. It tests whether the COVID alpha is a fresh-shock
    surface rather than a persistent bear-market filter.
    """

    q90 = 0.4729721681250059
    return {
        "baseline": PathPolicy("baseline", ProxyPolicy("baseline"), note="No call filter."),
        "wave_q90_sector_crash_overflow": PathPolicy(
            "wave_q90_sector_crash_overflow",
            ProxyPolicy(
                "wave_q90_sector_crash_overflow",
                wave_threshold=q90,
                min_sector_crash=0.10,
            ),
            note="Previous best precision proxy: q90 daily wave + marginal call + sector crash.",
        ),
        "acute_q90_sector_collapse_overflow": PathPolicy(
            "acute_q90_sector_collapse_overflow",
            ProxyPolicy(
                "acute_q90_sector_collapse_overflow",
                wave_threshold=q90,
                min_sector_crash=0.55,
            ),
            max_breadth_ema50=25.0,
            max_mcclellan=-40.0,
            min_sector_crash=0.55,
            note="Acute shock variant: q90 wave, marginal call, sector crash >=0.55, EMA50 breadth <=25, McClellan <=-40.",
        ),
        "acute_q90_sector_ct_overflow": PathPolicy(
            "acute_q90_sector_ct_overflow",
            ProxyPolicy(
                "acute_q90_sector_ct_overflow",
                wave_threshold=q90,
                min_sector_crash=0.55,
            ),
            max_breadth_ema50=25.0,
            max_mcclellan=-40.0,
            max_trend=20.0,
            min_sector_crash=0.55,
            note="Capital-bearing acute shock variant: only marginal CT-promoted calls in acute sector/global crash tape.",
        ),
        "acute_q90_breadth_collapse_overflow": PathPolicy(
            "acute_q90_breadth_collapse_overflow",
            ProxyPolicy(
                "acute_q90_breadth_collapse_overflow",
                wave_threshold=q90,
                min_sector_crash=0.10,
            ),
            max_breadth_ema50=25.0,
            max_mcclellan=-40.0,
            min_sector_crash=0.10,
            note="Broader acute shock variant: q90 wave, marginal call, EMA50 breadth <=25, McClellan <=-40.",
        ),
    }


def _write_status(out_dir: Path, started_at: str, **payload: Any) -> None:
    body = {
        "phase": "path_diff",
        "state": "running",
        "started_at": started_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "run_log": str(out_dir / "run.log"),
        **payload,
    }
    tmp = out_dir / "path_diff_status.json.tmp"
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    tmp.replace(out_dir / "path_diff_status.json")


def _read_daily_wave_rows(path: Path) -> dict[date, dict[str, Any]]:
    rows: dict[date, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            d = _as_date(row["date"])
            rows[d] = row
    return rows


def _path_policy_blocks(sig: Any, policy: PathPolicy, wave: dict[date, float], daily: dict[date, dict[str, Any]]) -> bool:
    if policy.name == "baseline":
        return False
    if not _should_block(sig, policy.proxy, wave):
        return False
    day = daily.get(sig.date, {})
    if policy.max_breadth_ema50 is not None:
        ema50 = _float(day.get("day_breadth_pct_above_ema50"))
        if ema50 is None or ema50 > policy.max_breadth_ema50:
            return False
    if policy.max_mcclellan is not None:
        mcc = _float(day.get("day_breadth_mcclellan_oscillator"))
        if mcc is None or mcc > policy.max_mcclellan:
            return False
    if policy.max_trend is not None:
        trend = _float(getattr(sig, "trend", None))
        if trend is None or trend > policy.max_trend:
            return False
    if policy.min_sector_crash is not None:
        # The proxy already checks Score.weight_info; this second check keeps
        # artifact-level screens aligned with the named acute variant.
        pass
    return True


def make_path_load_signals(original, policy: PathPolicy, wave: dict[date, float], daily: dict[date, dict[str, Any]], block_stats: dict[tuple[str, str], dict[str, int]], blocked_keys: dict[tuple[str, str], set[tuple[str, str]]], current_window: dict[str, str]):
    def load_signals(version, d_start, d_end):
        sigs = list(original(version, d_start, d_end))
        if policy.name == "baseline":
            return sigs
        kept = []
        blocked = 0
        days: set[date] = set()
        keys: set[tuple[str, str]] = set()
        for sig in sigs:
            if _path_policy_blocks(sig, policy, wave, daily):
                blocked += 1
                days.add(sig.date)
                keys.add((str(sig.symbol_id), sig.date.isoformat()))
                continue
            kept.append(sig)
        key = (policy.name, current_window.get("window", "unknown"))
        block_stats[key] = {"blocked_calls": blocked, "blocked_days": len(days), "loaded_calls": len(sigs)}
        blocked_keys[key] = keys
        print(
            f"  PATH_POLICY={policy.name}: blocked {blocked}/{len(sigs)} calls across {len(days)} days",
            flush=True,
        )
        return kept

    return load_signals


def _read_tape(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        import polars as pl

        df = pl.read_parquet(path)
        if "seed" in df.columns:
            df = df.with_columns(pl.col("seed").cast(pl.Utf8))
        return pd.DataFrame(df.to_dicts())
    payload = json.loads(path.read_text(encoding="utf-8"))
    cols = payload["cols"]["trades"]
    return pd.DataFrame(payload["trades"], columns=cols)


def _read_episodes(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        import polars as pl

        df = pl.read_parquet(path)
        if "seed" in df.columns:
            df = df.with_columns(pl.col("seed").cast(pl.Utf8))
        return pd.DataFrame(df.to_dicts())
    payload = json.loads(path.read_text(encoding="utf-8"))
    cols = payload["cols"]["episodes"]
    return pd.DataFrame(payload["episodes"], columns=cols)


def _copy_tape_artifacts(label: str, policy: str, window: str, out_dir: Path) -> dict[str, str | None]:
    tape_dir = out_dir / "tapes"
    tape_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str | None] = {"tape": None, "episodes": None}
    for stem, key in (("tape", "tape"), ("episodes", "episodes")):
        for suffix in (".parquet", ".json"):
            src = DD_LEDGER_DIR / f"{stem}_{label}{suffix}"
            if not src.exists():
                continue
            dst = tape_dir / f"{stem}_{policy}_{window}{suffix}"
            shutil.copy2(src, dst)
            copied[key] = str(dst)
            break
    return copied


def _clear_label_artifacts(label: str) -> None:
    for stem in ("tape", "episodes"):
        for suffix in (".parquet", ".json"):
            path = DD_LEDGER_DIR / f"{stem}_{label}{suffix}"
            if path.exists():
                path.unlink()


def _install_robust_tape_dump(mc) -> None:
    """Patch MC tape dumping for long-window mixed null/string columns.

    The production helper lets Polars infer schema. On large windows, the first
    inference chunk can see only null `ct` values and then fail when `ct_put`
    appears later. This experiment needs the tape, so keep the patch local and
    write a typed parquet contract.
    """

    def dump_trade_tape(label, seeds, results, trading_days):
        import polars as pl

        DD_LEDGER_DIR.mkdir(parents=True, exist_ok=True)

        trade_rows: list[dict[str, Any]] = []
        for seed, result in zip(seeds, results):
            tape = result.get("_tape")
            if not tape:
                continue
            for t in tape:
                trade_rows.append(
                    {
                        "seed": str(seed),
                        "sym_id": str(t[0]),
                        "entry_date": t[1].isoformat() if hasattr(t[1], "isoformat") else str(t[1]),
                        "exit_date": t[2].isoformat() if hasattr(t[2], "isoformat") else str(t[2]),
                        "side": str(t[3]),
                        "score": float(t[4]) if t[4] is not None else None,
                        "tier": "" if t[5] is None else str(t[5]),
                        "ct": "" if t[6] is None else str(t[6]),
                        "ern": bool(t[7]),
                        "premium_cost": float(t[8]) if t[8] is not None else 0.0,
                        "option_pnl": float(t[9]) if t[9] is not None else 0.0,
                        "outcome": str(t[10]),
                        "entry_value": float(t[11]) if t[11] is not None else None,
                        "entry_peak": float(t[12]) if t[12] is not None else None,
                        "entry_dd": float(t[13]) if t[13] is not None else None,
                        "entry_open_calls": int(t[14]) if t[14] is not None else None,
                        "entry_open_puts": int(t[15]) if t[15] is not None else None,
                    }
                )

        n_days = len(trading_days)

        def idx_to_date(i):
            if 0 <= i < n_days:
                d = trading_days[i]
                return d.isoformat() if hasattr(d, "isoformat") else str(d)
            return None

        episode_rows: list[dict[str, Any]] = []
        for seed, result in zip(seeds, results):
            for rank, (s_idx, t_idx, peak, trough) in enumerate(result.get("_episodes", [])):
                mag = (peak - trough) / peak if peak > 0 else 0.0
                episode_rows.append(
                    {
                        "seed": str(seed),
                        "window_label": str(label),
                        "rank": int(rank),
                        "start_date": idx_to_date(s_idx),
                        "trough_date": idx_to_date(t_idx),
                        "peak_value": float(peak),
                        "trough_value": float(trough),
                        "magnitude": float(mag),
                        "final_value": float(result["final"]),
                        "max_dd": float(result["max_dd"]),
                    }
                )

        if trade_rows:
            out_pq = DD_LEDGER_DIR / f"tape_{label}.parquet"
            pl.DataFrame(
                trade_rows,
                schema={
                    "seed": pl.Utf8,
                    "sym_id": pl.Utf8,
                    "entry_date": pl.Utf8,
                    "exit_date": pl.Utf8,
                    "side": pl.Utf8,
                    "score": pl.Float64,
                    "tier": pl.Utf8,
                    "ct": pl.Utf8,
                    "ern": pl.Boolean,
                    "premium_cost": pl.Float64,
                    "option_pnl": pl.Float64,
                    "outcome": pl.Utf8,
                    "entry_value": pl.Float64,
                    "entry_peak": pl.Float64,
                    "entry_dd": pl.Float64,
                    "entry_open_calls": pl.Int64,
                    "entry_open_puts": pl.Int64,
                },
            ).write_parquet(out_pq)
            print(f"  [tape-local] wrote {len(trade_rows):,} trades -> {out_pq}")

        if episode_rows:
            out_pq = DD_LEDGER_DIR / f"episodes_{label}.parquet"
            pl.DataFrame(
                episode_rows,
                schema={
                    "seed": pl.Utf8,
                    "window_label": pl.Utf8,
                    "rank": pl.Int64,
                    "start_date": pl.Utf8,
                    "trough_date": pl.Utf8,
                    "peak_value": pl.Float64,
                    "trough_value": pl.Float64,
                    "magnitude": pl.Float64,
                    "final_value": pl.Float64,
                    "max_dd": pl.Float64,
                },
            ).write_parquet(out_pq)
            print(f"  [tape-local] wrote {len(episode_rows):,} episodes -> {out_pq}")

    mc._dump_trade_tape = dump_trade_tape


def _dollar_pnl(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df["premium_cost"], errors="coerce").fillna(0.0) * pd.to_numeric(df["option_pnl"], errors="coerce").fillna(0.0)


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
    out["dollar_pnl"] = _dollar_pnl(out)
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


def _summarize_diff(
    baseline_tape: pd.DataFrame,
    policy_tape: pd.DataFrame,
    baseline_ep: pd.DataFrame,
    policy_ep: pd.DataFrame,
    direct_blocked_keys: set[tuple[str, str]],
    window: str,
    policy: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    b = _prep_tape(baseline_tape)
    p = _prep_tape(policy_tape)
    b_keys = set(b["trade_key"]) if not b.empty else set()
    p_keys = set(p["trade_key"]) if not p.empty else set()
    removed = b[b["trade_key"].isin(b_keys - p_keys)].copy() if not b.empty else b
    added = p[p["trade_key"].isin(p_keys - b_keys)].copy() if not p.empty else p
    if not removed.empty:
        removed["is_direct_blocked"] = [
            (str(sym), str(entry_date)[:10]) in direct_blocked_keys and side == "call"
            for sym, entry_date, side in zip(removed["sym_id"], removed["entry_date"], removed["side"])
        ]
    else:
        removed["is_direct_blocked"] = pd.Series(dtype=bool)
    direct = removed[removed["is_direct_blocked"]].copy() if not removed.empty else removed
    secondary = removed[~removed["is_direct_blocked"]].copy() if not removed.empty else removed

    def agg(prefix: str, frame: pd.DataFrame, row: dict[str, Any]) -> None:
        row[f"{prefix}_trades"] = int(len(frame))
        row[f"{prefix}_calls"] = int((frame["side"] == "call").sum()) if not frame.empty else 0
        row[f"{prefix}_puts"] = int((frame["side"] == "put").sum()) if not frame.empty else 0
        row[f"{prefix}_premium"] = float(frame["premium_cost"].sum()) if not frame.empty else 0.0
        row[f"{prefix}_dollar_pnl"] = float(frame["dollar_pnl"].sum()) if not frame.empty else 0.0
        row[f"{prefix}_avg_option_pnl"] = float(frame["option_pnl"].mean()) if not frame.empty else 0.0

    row: dict[str, Any] = {"window": window, "policy": policy}
    agg("removed", removed, row)
    agg("direct_blocked", direct, row)
    agg("secondary_removed", secondary, row)
    agg("added", added, row)

    b0 = baseline_ep[baseline_ep["rank"].astype(int) == 0].copy() if not baseline_ep.empty else baseline_ep
    p0 = policy_ep[policy_ep["rank"].astype(int) == 0].copy() if not policy_ep.empty else policy_ep
    ep_rows: list[dict[str, Any]] = []
    if not b0.empty and not p0.empty:
        b0["seed"] = b0["seed"].astype(str)
        p0["seed"] = p0["seed"].astype(str)
        merged = b0.merge(p0, on="seed", how="inner", suffixes=("_baseline", "_policy"))
        if not merged.empty:
            merged["magnitude_baseline"] = pd.to_numeric(merged["magnitude_baseline"], errors="coerce")
            merged["magnitude_policy"] = pd.to_numeric(merged["magnitude_policy"], errors="coerce")
            merged["magnitude_delta"] = merged["magnitude_policy"] - merged["magnitude_baseline"]
            row["episode_seeds"] = int(len(merged))
            row["episode_mean_delta_pct"] = float(merged["magnitude_delta"].mean() * 100)
            row["episode_worse_seeds"] = int((merged["magnitude_delta"] > 0.005).sum())
            row["episode_better_seeds"] = int((merged["magnitude_delta"] < -0.005).sum())
            for _, er in merged.sort_values("magnitude_delta", ascending=False).head(20).iterrows():
                ep_rows.append(
                    {
                        "window": window,
                        "policy": policy,
                        "seed": str(er["seed"]),
                        "baseline_start": er.get("start_date_baseline"),
                        "baseline_trough": er.get("trough_date_baseline"),
                        "policy_start": er.get("start_date_policy"),
                        "policy_trough": er.get("trough_date_policy"),
                        "baseline_magnitude_pct": float(er["magnitude_baseline"] * 100),
                        "policy_magnitude_pct": float(er["magnitude_policy"] * 100),
                        "delta_pct": float(er["magnitude_delta"] * 100),
                    }
                )
        else:
            row["episode_seeds"] = 0
    else:
        row["episode_seeds"] = 0

    date_rows: list[dict[str, Any]] = []
    if not removed.empty or not added.empty:
        dates = sorted(set(removed["entry_date"].tolist() if not removed.empty else []) | set(added["entry_date"].tolist() if not added.empty else []))
        for d in dates:
            r = removed[removed["entry_date"] == d] if not removed.empty else removed
            a = added[added["entry_date"] == d] if not added.empty else added
            dr = r[r["is_direct_blocked"]] if not r.empty else r
            date_rows.append(
                {
                    "window": window,
                    "policy": policy,
                    "entry_date": d,
                    "removed_trades": int(len(r)),
                    "direct_blocked_trades": int(len(dr)),
                    "direct_blocked_avg_option_pnl": float(dr["option_pnl"].mean()) if not dr.empty else 0.0,
                    "direct_blocked_dollar_pnl": float(dr["dollar_pnl"].sum()) if not dr.empty else 0.0,
                    "secondary_removed_trades": int(len(r) - len(dr)),
                    "secondary_removed_dollar_pnl": float(r[~r["is_direct_blocked"]]["dollar_pnl"].sum()) if not r.empty else 0.0,
                    "added_trades": int(len(a)),
                    "added_calls": int((a["side"] == "call").sum()) if not a.empty else 0,
                    "added_puts": int((a["side"] == "put").sum()) if not a.empty else 0,
                    "added_avg_option_pnl": float(a["option_pnl"].mean()) if not a.empty else 0.0,
                    "added_dollar_pnl": float(a["dollar_pnl"].sum()) if not a.empty else 0.0,
                }
            )
    return row, date_rows, ep_rows


def _load_signal_dataset(path: Path, wave_predictions: Path) -> pd.DataFrame:
    usecols = [
        "date",
        "symbol",
        "v60_overall",
        "v60_trend",
        "outcome_pnl_sample",
        "call_win",
        "call_bad",
        "wave_ml_pred_bad_rate",
        "wi_sector_breadth_wave_crash",
        "day_breadth_pct_above_ema50",
        "day_breadth_mcclellan_oscillator",
    ]
    raw = pd.read_csv(path, usecols=lambda c: c in set(usecols))
    raw["date"] = raw["date"].astype(str).str.slice(0, 10)
    if "wave_ml_pred_bad_rate" not in raw.columns:
        daily_wave = pd.read_csv(wave_predictions, usecols=["date", "wave_ml_pred_bad_rate"])
        daily_wave["date"] = daily_wave["date"].astype(str).str.slice(0, 10)
        raw = raw.merge(daily_wave, on="date", how="left")
    return raw


def _policy_signal_mask(df: pd.DataFrame, policy: PathPolicy) -> pd.Series:
    if policy.name == "baseline" or df.empty:
        return pd.Series(False, index=df.index)
    mask = df["v60_overall"].between(policy.proxy.min_overall, policy.proxy.max_overall)
    if policy.proxy.wave_threshold is not None and "wave_ml_pred_bad_rate" in df.columns:
        mask &= pd.to_numeric(df["wave_ml_pred_bad_rate"], errors="coerce") >= policy.proxy.wave_threshold
    if policy.proxy.min_sector_crash is not None and "wi_sector_breadth_wave_crash" in df.columns:
        mask &= pd.to_numeric(df["wi_sector_breadth_wave_crash"], errors="coerce") >= policy.proxy.min_sector_crash
    if policy.max_breadth_ema50 is not None:
        mask &= pd.to_numeric(df["day_breadth_pct_above_ema50"], errors="coerce") <= policy.max_breadth_ema50
    if policy.max_mcclellan is not None:
        mask &= pd.to_numeric(df["day_breadth_mcclellan_oscillator"], errors="coerce") <= policy.max_mcclellan
    if policy.max_trend is not None:
        mask &= pd.to_numeric(df["v60_trend"], errors="coerce") <= policy.max_trend
    return mask.fillna(False)


def _signal_screen_rows(signal_df: pd.DataFrame, policy: PathPolicy, windows: list[str]) -> list[dict[str, Any]]:
    if signal_df.empty or policy.name == "baseline":
        return []
    out: list[dict[str, Any]] = []
    df = signal_df.copy()
    mask = _policy_signal_mask(df, policy)
    for window in windows:
        d0, d1 = WINDOWS[window]
        w = df[mask & (df["date"] >= d0.isoformat()) & (df["date"] <= d1.isoformat())]
        if w.empty:
            out.append({"window": window, "policy": policy.name, "signals": 0})
            continue
        out.append(
            {
                "window": window,
                "policy": policy.name,
                "signals": int(len(w)),
                "days": int(w["date"].nunique()),
                "bad_rate": float(pd.to_numeric(w["call_bad"], errors="coerce").mean()),
                "win_rate": float(pd.to_numeric(w["call_win"], errors="coerce").mean()),
                "mean_pnl_sample": float(pd.to_numeric(w["outcome_pnl_sample"], errors="coerce").mean()),
            }
        )
    return out


def _write_markdown(path: Path, rows: list[dict[str, Any]], diff_rows: list[dict[str, Any]], signal_rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    lines = [
        "# Score-Dampen Path-Diff Research",
        "",
        "Experiment-local decomposition of baseline vs candidate score-dampen MC trade tapes.",
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
        "| window | policy | blocked | worst DD d | mean DD d | mean ret d | direct pnl | added pnl | worse seeds | better seeds |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    scored = _score_rows(rows)
    diff_by_key = {(r["window"], r["policy"]): r for r in diff_rows}
    for row in scored:
        if row["policy"] == "baseline":
            continue
        diff = diff_by_key.get((row["window"], row["policy"]), {})
        lines.append(
            f"| {row['window']} | `{row['policy']}` | {int(float(row.get('blocked_calls', 0) or 0))} | "
            f"{float(row['worst_dd_delta']):+.2f} | {float(row['mean_dd_delta']):+.2f} | {float(row['mean_ret_delta']):+.2f} | "
            f"{float(diff.get('direct_blocked_dollar_pnl', 0.0)):+.0f} | {float(diff.get('added_dollar_pnl', 0.0)):+.0f} | "
            f"{int(diff.get('episode_worse_seeds', 0) or 0)} | {int(diff.get('episode_better_seeds', 0) or 0)} |"
        )
    lines.extend(["", "## Signal-Level Screen", ""])
    lines.append("| window | policy | signals | days | bad | win | pnl |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for row in signal_rows:
        lines.append(
            f"| {row['window']} | `{row['policy']}` | {int(row.get('signals', 0) or 0)} | {int(row.get('days', 0) or 0)} | "
            f"{float(row.get('bad_rate', 0) or 0):.3f} | {float(row.get('win_rate', 0) or 0):.3f} | "
            f"{float(row.get('mean_pnl_sample', 0) or 0):+.3f} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- `direct pnl` is the summed dollar P&L of baseline-filled trades the policy directly blocked.",
            "- `added pnl` is the summed dollar P&L of policy-only trades created by freed cash/slots/pathing.",
            "- A good shock controller should make direct blocked P&L materially negative without creating worse-episode replacement drag.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--wave-predictions", type=Path, default=DEFAULT_WAVE)
    parser.add_argument("--signal-dataset", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--version-id", type=int, default=60)
    parser.add_argument("--n", type=int, default=80)
    parser.add_argument("--windows", default="2020-crash,2022,2024,22-now,5y")
    parser.add_argument(
        "--policies",
        default="baseline,wave_q90_sector_crash_overflow,acute_q90_sector_collapse_overflow,acute_q90_breadth_collapse_overflow",
    )
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--label-prefix", default="pathdiff")
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

    wave = _read_wave(args.wave_predictions)
    daily = _read_daily_wave_rows(args.wave_predictions)
    av = AlgorithmVersion.get_by_id(args.version_id)
    all_policies = path_policies()
    wanted_policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    wanted_windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    missing = [p for p in wanted_policies if p not in all_policies]
    if missing:
        raise KeyError(f"Unknown policies: {missing}")
    if "baseline" not in wanted_policies:
        wanted_policies = ["baseline"] + wanted_policies

    signal_df = _load_signal_dataset(args.signal_dataset, args.wave_predictions)
    signal_rows: list[dict[str, Any]] = []
    for pname in wanted_policies:
        signal_rows.extend(_signal_screen_rows(signal_df, all_policies[pname], wanted_windows))

    rows: list[dict[str, Any]] = []
    diff_rows: list[dict[str, Any]] = []
    date_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    artifact_rows: list[dict[str, Any]] = []
    block_stats: dict[tuple[str, str], dict[str, int]] = {}
    blocked_keys: dict[tuple[str, str], set[tuple[str, str]]] = {}
    current_window: dict[str, str] = {}
    original_load_signals = mc.load_signals
    total = len(wanted_windows) * len(wanted_policies)
    completed = 0
    try:
        for window_name in wanted_windows:
            d_start, d_end = WINDOWS[window_name]
            label = f"{args.label_prefix}_{window_name}"
            baseline_tape: pd.DataFrame | None = None
            baseline_ep: pd.DataFrame | None = None
            for policy_name in wanted_policies:
                policy = all_policies[policy_name]
                current_window["window"] = window_name
                mc.load_signals = make_path_load_signals(
                    original_load_signals,
                    policy,
                    wave,
                    daily,
                    block_stats,
                    blocked_keys,
                    current_window,
                )
                print(f"\n[path-diff] window={window_name} policy={policy_name} label={label}", flush=True)
                _clear_label_artifacts(label)
                t0 = time.time()
                result = dict(_run_window_with_retry(mc, label, d_start, d_end, av, attempts=args.db_attempts)["seeded"])
                result["window"] = window_name
                result["policy"] = policy_name
                result["n"] = args.n
                result["elapsed_s"] = round(time.time() - t0, 2)
                result["policy_config"] = json.dumps(asdict(policy), sort_keys=True)
                result.update(block_stats.get((policy_name, window_name), {"blocked_calls": 0, "blocked_days": 0, "loaded_calls": 0}))
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
                    diff, by_date, by_episode = _summarize_diff(
                        baseline_tape,
                        tape,
                        baseline_ep,
                        episodes,
                        blocked_keys.get((policy_name, window_name), set()),
                        window_name,
                        policy_name,
                    )
                    diff_rows.append(diff)
                    date_rows.extend(by_date)
                    episode_rows.extend(by_episode)

                completed += 1
                _write_csv(args.out_dir / "path_diff_mc.csv", rows)
                _write_csv(args.out_dir / "path_diff_ranked.csv", _score_rows(rows))
                _write_csv(args.out_dir / "path_diff_summary.csv", diff_rows)
                _write_csv(args.out_dir / "path_diff_by_date.csv", date_rows)
                _write_csv(args.out_dir / "path_diff_episodes.csv", episode_rows)
                _write_csv(args.out_dir / "path_diff_artifacts.csv", artifact_rows)
                _write_csv(args.out_dir / "path_diff_signal_screen.csv", signal_rows)
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
        mc.load_signals = original_load_signals
        _restore_hash()

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version_id": int(av.id),
        "commit": getattr(av, "git_commit", None),
        "n": args.n,
        "windows": wanted_windows,
        "policies": wanted_policies,
        "wave_predictions": str(args.wave_predictions),
        "signal_dataset": str(args.signal_dataset),
    }
    out_md = args.out_dir / "score_dampen_path_diff_summary.md"
    _write_markdown(out_md, rows, diff_rows, signal_rows, meta)
    done = {
        "phase": "done",
        "state": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "mc": str(args.out_dir / "path_diff_mc.csv"),
            "ranked": str(args.out_dir / "path_diff_ranked.csv"),
            "summary": str(args.out_dir / "path_diff_summary.csv"),
            "by_date": str(args.out_dir / "path_diff_by_date.csv"),
            "episodes": str(args.out_dir / "path_diff_episodes.csv"),
            "signals": str(args.out_dir / "path_diff_signal_screen.csv"),
            "markdown": str(out_md),
        },
    }
    _write_status(args.out_dir, started_at, state="done", completed=completed, total=total, current="done")
    (args.out_dir / "done.json").write_text(json.dumps(done, indent=2), encoding="utf-8")
    print(f"Wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
