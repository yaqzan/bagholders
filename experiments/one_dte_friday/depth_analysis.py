"""Friday 1DTE score-depth analysis.

Measures the part that matters for Friday-expiring options before choosing any
TP/SL translation: how deep the Friday open-to-close move is, adjusted by each
symbol's recent daily volatility.

Signal contract:
  - use prior trading session score, usually Thursday
  - HIGH score => call direction, LOW score => put direction
  - entry = Friday open, exit = Friday close
  - no hold dynamics, no TP/SL/dead-hold, no option-chain coverage dependency

Outputs:
  - signals_depth.csv: one row per Friday signal
  - bucket_summary.csv: score/depth bucket statistics
  - friday_summary.csv: one-day equal-weight Friday aggregates
  - summary.json: compact verdict inputs
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.trader_database import DB  # noqa: E402
from experiments.one_dte_friday.prelim_1dte_friday import (  # noqa: E402
    RunStatus,
    friday_signal_pairs,
    load_realized_sigma,
    load_score_signals,
    load_trading_days,
    quantile,
    write_csv,
)


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def latest_price_date() -> date:
    row = DB.execute_sql("SELECT MAX(date) FROM price_history").fetchone()
    if not row or row[0] is None:
        raise RuntimeError("price_history has no max date")
    return row[0]


def mean(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(vals) / len(vals) if vals else None


def median(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(vals) if vals else None


def pct(value: float | None, ndigits: int = 2) -> float | None:
    return None if value is None else round(value * 100.0, ndigits)


def rnd(value: float | None, ndigits: int = 3) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, ndigits)


def score_bucket(side: str, overall: int) -> str:
    if side == "put":
        lo = max(0, (overall // 5) * 5)
        hi = min(25, lo + 4)
        return f"PUT_{lo:02d}_{hi:02d}"
    lo = max(75, (overall // 5) * 5)
    hi = min(100, lo + 4)
    return f"CALL_{lo:02d}_{hi:02d}"


def depth_bucket(depth: int) -> str:
    lo = max(25, (depth // 5) * 5)
    hi = min(50, lo + 4)
    return f"DEPTH_{lo:02d}_{hi:02d}"


def attach_depth(signals: list[dict[str, Any]], sigma: dict[tuple[str, date], float]) -> None:
    for s in signals:
        sig = sigma.get((s["symbol"], s["signal_date"]))
        s["sigma_60d"] = sig
        s["score_depth"] = abs(int(s["overall"]) - 50)
        s["score_bucket"] = score_bucket(s["side"], int(s["overall"]))
        s["depth_bucket"] = depth_bucket(int(s["score_depth"]))
        if sig is not None and sig > 0:
            s["signed_sigma_depth"] = s["signed_move"] / (sig / 100.0)
            s["abs_sigma_depth"] = abs(s["signed_sigma_depth"])
        else:
            s["signed_sigma_depth"] = None
            s["abs_sigma_depth"] = None
        s["signed_move_pct"] = s["signed_move"] * 100.0
        s["win"] = s["signed_move"] > 0


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_sigma = [r for r in rows if r.get("signed_sigma_depth") is not None]
    wins = [r for r in with_sigma if r["signed_sigma_depth"] > 0]
    losses = [r for r in with_sigma if r["signed_sigma_depth"] <= 0]
    raw_wins = [r for r in rows if r.get("signed_move") is not None and r["signed_move"] > 0]
    friday_count = len({r["friday"] for r in rows})
    return {
        "n": len(rows),
        "n_sigma": len(with_sigma),
        "fridays": friday_count,
        "avg_signals_per_friday": rnd(len(rows) / friday_count if friday_count else None, 2),
        "direction_wr_pct": pct(mean([1.0 if r["win"] else 0.0 for r in rows])),
        "avg_total_move_pct": rnd(mean([r["signed_move_pct"] for r in rows]), 3),
        "median_total_move_pct": rnd(median([r["signed_move_pct"] for r in rows]), 3),
        "avg_win_move_pct": rnd(mean([r["signed_move_pct"] for r in raw_wins]), 3),
        "median_win_move_pct": rnd(median([r["signed_move_pct"] for r in raw_wins]), 3),
        "avg_total_sigma_depth": rnd(mean([r["signed_sigma_depth"] for r in with_sigma]), 3),
        "median_total_sigma_depth": rnd(median([r["signed_sigma_depth"] for r in with_sigma]), 3),
        "avg_win_sigma_depth": rnd(mean([r["signed_sigma_depth"] for r in wins]), 3),
        "median_win_sigma_depth": rnd(median([r["signed_sigma_depth"] for r in wins]), 3),
        "p60_win_sigma_depth": rnd(quantile([r["signed_sigma_depth"] for r in wins], 0.60)),
        "p75_win_sigma_depth": rnd(quantile([r["signed_sigma_depth"] for r in wins], 0.75)),
        "p90_win_sigma_depth": rnd(quantile([r["signed_sigma_depth"] for r in wins], 0.90)),
        "avg_loss_sigma_depth": rnd(mean([r["signed_sigma_depth"] for r in losses]), 3),
        "p10_total_sigma_depth": rnd(quantile([r["signed_sigma_depth"] for r in with_sigma], 0.10)),
        "p90_total_sigma_depth": rnd(quantile([r["signed_sigma_depth"] for r in with_sigma], 0.90)),
    }


def bucket_summaries(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in signals:
        groups[(s["side"], s["score_bucket"], s["depth_bucket"])].append(s)
    out = []
    for (side, sb, db), rows in sorted(groups.items(), key=lambda x: (x[0][0], x[0][2], x[0][1])):
        rec = {"side": side, "score_bucket": sb, "depth_bucket": db}
        rec.update(summarize_rows(rows))
        out.append(rec)
    return out


def friday_aggregates(signals: list[dict[str, Any]], selectors: list[int | str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for side in ("put", "call", "all"):
        side_rows = [s for s in signals if side == "all" or s["side"] == side]
        by_friday: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in side_rows:
            by_friday[row["friday"]].append(row)
        for friday, rows in sorted(by_friday.items()):
            ranked = sorted(rows, key=lambda r: r["score_depth"], reverse=True)
            for selector in selectors:
                selected = ranked if selector == "all" else ranked[: int(selector)]
                if not selected:
                    continue
                sigma_vals = [r["signed_sigma_depth"] for r in selected if r.get("signed_sigma_depth") is not None]
                rec = {
                    "side": side,
                    "selector": selector,
                    "friday": friday.isoformat(),
                    "n": len(selected),
                    "n_available": len(rows),
                    "avg_signed_move_pct": rnd(mean([r["signed_move_pct"] for r in selected]), 3),
                    "avg_signed_sigma_depth": rnd(mean(sigma_vals)),
                    "best_signed_sigma_depth": rnd(max(sigma_vals) if sigma_vals else None),
                    "worst_signed_sigma_depth": rnd(min(sigma_vals) if sigma_vals else None),
                    "symbols": ";".join(r["symbol"] for r in selected),
                }
                out.append(rec)
    return out


def portfolio_depth_summary(friday_rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in friday_rows:
        groups[(r["side"], str(r["selector"]))].append(r)

    out: dict[str, Any] = {}
    for (side, selector), rows in sorted(groups.items()):
        rows = [r for r in rows if r.get("avg_signed_move_pct") is not None]
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        returns = []
        sigmas = []
        for r in rows:
            ret = float(r["avg_signed_move_pct"]) / 100.0
            equity *= max(0.0, 1.0 + ret)
            peak = max(peak, equity)
            max_dd = max(max_dd, 0.0 if peak <= 0 else 1.0 - equity / peak)
            returns.append(ret)
            if r.get("avg_signed_sigma_depth") is not None:
                sigmas.append(float(r["avg_signed_sigma_depth"]))
        key = f"{side}_{selector}"
        out[key] = {
            "side": side,
            "selector": selector,
            "fridays": len(rows),
            "avg_names_per_friday": rnd(mean([float(r["n"]) for r in rows]), 2),
            "one_day_win_rate_pct": pct(mean([1.0 if r > 0 else 0.0 for r in returns])),
            "compounded_underlying_return_pct": pct(equity - 1.0),
            "max_compounded_drawdown_pct": pct(max_dd),
            "worst_friday_underlying_return_pct": pct(min(returns) if returns else None),
            "best_friday_underlying_return_pct": pct(max(returns) if returns else None),
            "avg_friday_sigma_depth": rnd(mean(sigmas)),
            "median_friday_sigma_depth": rnd(median(sigmas)),
            "worst_friday_sigma_depth": rnd(min(sigmas) if sigmas else None),
            "best_friday_sigma_depth": rnd(max(sigmas) if sigmas else None),
        }
    return out


def parse_selectors(value: str) -> list[int | str]:
    out: list[int | str] = []
    for part in value.split(","):
        token = part.strip().lower()
        if not token:
            continue
        out.append("all" if token == "all" else int(token))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--version", type=int, default=58)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--years", type=float, default=None)
    parser.add_argument("--score-hi", type=int, default=75)
    parser.add_argument("--score-lo", type=int, default=25)
    parser.add_argument("--strict-thursday", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--selectors", default="3,5,8,10,all")
    args = parser.parse_args()

    started = time.time()
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    status = RunStatus(run_dir)
    status.write("init")

    DB.connect(reuse_if_open=True)
    end = parse_date(args.end) or latest_price_date()
    if args.start:
        start = parse_date(args.start)
    elif args.years:
        start = end - timedelta(days=int(args.years * 365.25))
    else:
        start = end - timedelta(days=370)
    assert start is not None

    print(f"[calendar] depth window={start}..{end} version=v{args.version}", flush=True)
    trading_days = load_trading_days(start - timedelta(days=10), end)
    pairs = friday_signal_pairs(trading_days, start, end, args.strict_thursday)
    print(f"[calendar] friday pairs={len(pairs)}", flush=True)
    status.write("score_extract", completed=0, total=len(pairs), start=start, end=end)

    signals = load_score_signals(pairs, args.version, args.score_hi, args.score_lo, status)
    if not signals:
        raise RuntimeError("no matching Friday score signals")

    status.write("sigma_extract", signals=len(signals))
    sigma = load_realized_sigma(signals, start, end)
    attach_depth(signals, sigma)

    buckets = bucket_summaries(signals)
    friday_rows = friday_aggregates(signals, parse_selectors(args.selectors))
    portfolio = portfolio_depth_summary(friday_rows)

    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "version": args.version,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "settings": {
            "score_hi": args.score_hi,
            "score_lo": args.score_lo,
            "strict_thursday": args.strict_thursday,
            "selectors": args.selectors,
        },
        "coverage": {
            "friday_pairs": len(pairs),
            "signals": len(signals),
            "puts": sum(1 for s in signals if s["side"] == "put"),
            "calls": sum(1 for s in signals if s["side"] == "call"),
            "sigma_rows": sum(1 for s in signals if s.get("signed_sigma_depth") is not None),
        },
        "overall": summarize_rows(signals),
        "by_side": {
            side: summarize_rows([s for s in signals if s["side"] == side])
            for side in ("put", "call")
        },
        "portfolio_depth": portfolio,
        "runtime_sec": round(time.time() - started, 2),
    }

    write_csv(run_dir / "signals_depth.csv", signals)
    write_csv(run_dir / "bucket_summary.csv", buckets)
    write_csv(run_dir / "friday_summary.csv", friday_rows)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (run_dir / "done.json").write_text(
        json.dumps({
            "state": "done",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "summary_json": str(run_dir / "summary.json"),
            "signals_csv": str(run_dir / "signals_depth.csv"),
            "bucket_csv": str(run_dir / "bucket_summary.csv"),
            "friday_csv": str(run_dir / "friday_summary.csv"),
        }, indent=2),
        encoding="utf-8",
    )
    status.write("done", state="done", runtime_sec=summary["runtime_sec"])
    print(f"[done] summary={run_dir / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
