"""Sector ETF reverse-breadth-thrust echo sweep.

Read-only experiment. This tests a score post-processing shape for CALL
withholding after a sector-breadth cliff:

    seed = max(low sector breadth, negative 5d thrust, oversold sector RSI)
    echo = max(seed, prior_echo * decay * repair_relief)

The score modifier then moves call scores toward a neutral target while echo is
active. It approximates a future production score modifier without touching
production scoring code.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
elif hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

import backtest_cascade as bc
from database.models.core import AlgorithmVersion
from experiments.calendar_drawdown_2020.analyze_and_sweep import (
    PRESERVE_MONTHS,
    REFERENCE_MONTHS,
    TARGET_MONTHS,
    month_key,
    month_return,
)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


class Status:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.path = run_dir / "status.json"
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def write(self, phase: str, state: str = "running", completed: int | None = None,
              total: int | None = None, **extra) -> None:
        payload = {
            "phase": phase,
            "state": state,
            "completed": completed,
            "total": total,
            "started_at": self.started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "output_paths": {
                "run_dir": str(self.run_dir),
                "run_log": str(self.run_dir / "run.log"),
                "run_err": str(self.run_dir / "run.err"),
                "variant_screen": str(self.run_dir / "variant_screen.csv"),
                "variant_full": str(self.run_dir / "variant_full.csv"),
                "done_json": str(self.run_dir / "done.json"),
                "failed_json": str(self.run_dir / "failed.json"),
            },
        }
        payload.update(extra)
        write_json(self.path, payload)


@dataclass(frozen=True)
class EchoVariant:
    name: str
    k: float
    target: float
    decay: float
    repair_k: float
    seed_level: float
    seed_velocity: float
    seed_rsi: float
    relief_brd_start: float
    relief_brd_full: float
    relief_avg5_start: float
    relief_avg5_full: float
    power: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class EchoSeries:
    def __init__(self, rows: list[dict], echo_by_variant: dict[str, dict[date, float]]):
        self.rows = rows
        self.by_date = {r["date"]: r for r in rows}
        self.dates = sorted(self.by_date)
        self.echo_by_variant = echo_by_variant

    @classmethod
    def load(cls, variants: list[EchoVariant]) -> "EchoSeries":
        path = ROOT / ".cache" / "sector_etf_screen" / "sector_breadth_daily_2020plus.csv"
        if not path.exists():
            raise RuntimeError(f"Missing sector breadth cache: {path}")
        df = pl.read_csv(path).with_columns(pl.col("date").cast(pl.Utf8))
        rows = []
        for r in df.iter_rows(named=True):
            rows.append({
                "date": date.fromisoformat(r["date"]),
                "brd": float(r["sec_brd_ema50"]) if r.get("sec_brd_ema50") is not None else None,
                "rsi": float(r["sec_avg_rsi"]) if r.get("sec_avg_rsi") is not None else None,
            })
        rows.sort(key=lambda r: r["date"])
        brds = [r["brd"] for r in rows]
        for i, r in enumerate(rows):
            b = r["brd"]
            r["d5"] = b - brds[i - 5] if i >= 5 and b is not None and brds[i - 5] is not None else 0.0
            r["d10"] = b - brds[i - 10] if i >= 10 and b is not None and brds[i - 10] is not None else 0.0
            win5 = [x for x in brds[max(0, i - 4): i + 1] if x is not None]
            win10 = [x for x in brds[max(0, i - 9): i + 1] if x is not None]
            r["avg5"] = sum(win5) / len(win5) if win5 else None
            r["avg10"] = sum(win10) / len(win10) if win10 else None

        echo_by_variant = {}
        for v in variants:
            e = 0.0
            m = {}
            for r in rows:
                seed = echo_seed(r, v)
                relief = repair_relief(r, v)
                e = max(seed, e * v.decay * (1.0 - v.repair_k * relief))
                e = clamp(e)
                m[r["date"]] = e
            echo_by_variant[v.name] = m
        return cls(rows, echo_by_variant)

    def row_on_or_before(self, d: date) -> dict | None:
        import bisect
        i = bisect.bisect_right(self.dates, d) - 1
        return self.by_date[self.dates[i]] if i >= 0 else None

    def echo_on_or_before(self, d: date, variant_name: str) -> float:
        import bisect
        i = bisect.bisect_right(self.dates, d) - 1
        if i < 0:
            return 0.0
        return self.echo_by_variant[variant_name].get(self.dates[i], 0.0)


def echo_seed(r: dict, v: EchoVariant) -> float:
    brd = float(r.get("brd") or 50.0)
    rsi = float(r.get("rsi") or 50.0)
    d5 = float(r.get("d5") or 0.0)
    d10 = float(r.get("d10") or 0.0)
    low = clamp((v.seed_level - brd) / max(1.0, v.seed_level))
    velocity = clamp((max(-d5, -d10 * 0.75) - v.seed_velocity) / 40.0)
    oversold = clamp((v.seed_rsi - rsi) / max(1.0, v.seed_rsi - 15.0))
    return max(low, velocity, oversold)


def repair_relief(r: dict, v: EchoVariant) -> float:
    brd = float(r.get("brd") or 0.0)
    avg5 = float(r.get("avg5") or 0.0)
    br = clamp((brd - v.relief_brd_start) / max(1.0, v.relief_brd_full - v.relief_brd_start))
    ar = clamp((avg5 - v.relief_avg5_start) / max(1.0, v.relief_avg5_full - v.relief_avg5_start))
    return min(br, ar)


def transform_outcomes(outcomes_by_date: dict, series: EchoSeries, v: EchoVariant) -> tuple[dict, dict]:
    out = defaultdict(list)
    stats = {
        "call_adjusted": 0,
        "call_dropped": 0,
        "call_score_drop_sum": 0.0,
        "max_echo": 0.0,
    }
    for d, outs in outcomes_by_date.items():
        for o in outs:
            if o.side != "call":
                out[d].append(o)
                continue
            echo = series.echo_on_or_before(o.signal_date, v.name)
            stats["max_echo"] = max(stats["max_echo"], echo)
            new_score = float(o.score)
            if echo > 0:
                pressure = echo ** v.power
                candidate = new_score - v.k * pressure * max(0.0, new_score - v.target)
                candidate = round(max(0.0, min(100.0, candidate)))
                if candidate < new_score:
                    stats["call_adjusted"] += 1
                    stats["call_score_drop_sum"] += new_score - candidate
                new_score = candidate
            if new_score < 70:
                stats["call_dropped"] += 1
                continue
            tier = o.tier if o.tier == bc.CT_CALL_TIER else bc.score_to_tier(new_score)
            out[d].append(replace(o, score=new_score, tier=tier))

    def sort_key(o):
        side_order = 0 if o.side == "call" else 1
        ct_priority = 0 if (o.side == "call" and o.tier == bc.CT_CALL_TIER and o.score < 95) \
            or (o.side == "put" and o.tier == bc.CT_PUT_TIER and o.score > 15) else 1
        score_key = -o.score if o.side == "call" else o.score
        return side_order, ct_priority, score_key, o.symbol

    for d in list(out):
        out[d].sort(key=sort_key)
    return dict(out), stats


def run_variant(result: dict, series: EchoSeries, v: EchoVariant, months: list[tuple[int, int]],
                full: bool = False) -> dict:
    transformed, stats = transform_outcomes(result["outcomes_by_date"], series, v)
    month_rows = [month_return(result, transformed, yr, mo) for yr, mo in months]
    row = {
        "name": v.name,
        **v.as_dict(),
        **{k: round(val, 2) if isinstance(val, float) else val for k, val in stats.items()},
        "target_sum": round(sum(r["return_pct"] or 0 for r in month_rows if r["month"] in {month_key(*m) for m in TARGET_MONTHS}), 2),
        "reference_sum": round(sum(r["return_pct"] or 0 for r in month_rows if r["month"] in {month_key(*m) for m in REFERENCE_MONTHS}), 2),
        "preserve_sum": round(sum(r["return_pct"] or 0 for r in month_rows if r["month"] in {month_key(*m) for m in PRESERVE_MONTHS}), 2),
    }
    for r in month_rows:
        row[f"{r['month']}_ret"] = r["return_pct"]
        row[f"{r['month']}_trades"] = r["n_trades"]
        row[f"{r['month']}_calls"] = r["call_n"]
        row[f"{r['month']}_puts"] = r["put_n"]
    if full:
        full_result = bc.run_backtest(
            transformed,
            result["all_dates"],
            result["initial"],
            regime_dates=result["regime_dates"],
            regime_map=result["regime_map"],
            cfg={},
        )
        eq = full_result["equity_curve"][-1][1] if full_result["equity_curve"] else result["initial"]
        row["full_return_pct"] = round((eq / result["initial"] - 1.0) * 100, 2)
        row["full_max_dd_pct"] = round(full_result["max_dd"] * 100, 2)
        row["full_trades"] = len(full_result["trade_log"])
        row["full_calls"] = sum(1 for t in full_result["trade_log"] if t.get("side", "call") == "call")
        row["full_puts"] = sum(1 for t in full_result["trade_log"] if t.get("side") == "put")
    return row


def build_variants() -> list[EchoVariant]:
    variants = []
    for k in [0.25, 0.40, 0.55, 0.70]:
        for target in [55.0, 60.0, 65.0]:
            for decay in [0.90, 0.94, 0.96]:
                for relief in [(50.0, 75.0, 35.0, 70.0), (60.0, 90.0, 50.0, 75.0)]:
                    name = f"echo_K{int(k*100)}_T{int(target)}_D{int(decay*100)}_R{int(relief[0])}_{int(relief[2])}"
                    variants.append(EchoVariant(
                        name=name,
                        k=k,
                        target=target,
                        decay=decay,
                        repair_k=0.45,
                        seed_level=20.0,
                        seed_velocity=50.0,
                        seed_rsi=35.0,
                        relief_brd_start=relief[0],
                        relief_brd_full=relief[1],
                        relief_avg5_start=relief[2],
                        relief_avg5_full=relief[3],
                        power=1.0,
                    ))
    return variants


def add_deltas(rows: list[dict], baseline: dict) -> None:
    for row in rows:
        row["target_delta_vs_base"] = round(row["target_sum"] - baseline["target_sum"], 2)
        row["reference_delta_vs_base"] = round(row["reference_sum"] - baseline["reference_sum"], 2)
        row["preserve_delta_vs_base"] = round(row["preserve_sum"] - baseline["preserve_sum"], 2)
        row["screen_score"] = round(
            row["target_delta_vs_base"]
            + 0.65 * row["reference_delta_vs_base"]
            + min(0.0, row["preserve_delta_vs_base"]) * 1.25,
            2,
        )


def repair_summary(series: EchoSeries, v: EchoVariant) -> list[dict]:
    dates = [
        date(2020, 2, 21), date(2020, 2, 24), date(2020, 2, 27),
        date(2020, 3, 23), date(2020, 4, 9), date(2020, 4, 17),
        date(2020, 4, 29), date(2020, 5, 27), date(2020, 6, 1),
    ]
    rows = []
    for d in dates:
        r = series.row_on_or_before(d) or {}
        rows.append({
            "date": d.isoformat(),
            "sec_brd_ema50": round(float(r.get("brd") or 0.0), 2),
            "sec_brd_avg5": round(float(r.get("avg5") or 0.0), 2),
            "sec_avg_rsi": round(float(r.get("rsi") or 0.0), 2),
            "echo": round(series.echo_on_or_before(d, v.name), 4),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    ap.add_argument("--to", dest="to_date", default=None)
    ap.add_argument("--top-n", type=int, default=36)
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "calendar_drawdown_2020" / "runs" / f"breadth_echo_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Status(run_dir)
    write_json(run_dir / "launch.json", {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "cwd": str(ROOT),
        "argv": sys.argv,
        "git_commit": os.popen("git rev-parse HEAD").read().strip(),
    })

    av = AlgorithmVersion.get_active_scores_version()
    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date) if args.to_date else None
    print(f"[baseline] active v{av.id}, from {from_date}, to {to_date or 'latest'}", flush=True)
    status.write("baseline_backtest", active_version=av.id)
    result = bc.run_cascade_backtest(av.id, from_date=from_date, to_date=to_date, initial=50_000.0, verbose=True)
    if not result:
        raise RuntimeError("No baseline result")

    variants = build_variants()
    baseline = {
        "name": "baseline",
        "target_sum": 0.0,
        "reference_sum": 0.0,
        "preserve_sum": 0.0,
    }
    months = TARGET_MONTHS + REFERENCE_MONTHS + PRESERVE_MONTHS
    baseline.update({
        "target_sum": round(sum(month_return(result, result["outcomes_by_date"], yr, mo)["return_pct"] or 0 for yr, mo in TARGET_MONTHS), 2),
        "reference_sum": round(sum(month_return(result, result["outcomes_by_date"], yr, mo)["return_pct"] or 0 for yr, mo in REFERENCE_MONTHS), 2),
        "preserve_sum": round(sum(month_return(result, result["outcomes_by_date"], yr, mo)["return_pct"] or 0 for yr, mo in PRESERVE_MONTHS), 2),
    })
    for yr, mo in months:
        mr = month_return(result, result["outcomes_by_date"], yr, mo)
        baseline[f"{mr['month']}_ret"] = mr["return_pct"]
        baseline[f"{mr['month']}_trades"] = mr["n_trades"]

    series = EchoSeries.load(variants)
    rows = []
    print(f"[screen] {len(variants)} echo variants", flush=True)
    status.write("variant_screen", completed=0, total=len(variants))
    for i, v in enumerate(variants, 1):
        row = run_variant(result, series, v, months, full=False)
        rows.append(row)
        if i % 10 == 0 or i == len(variants):
            add_deltas(rows, baseline)
            write_csv(run_dir / "variant_screen.csv", rows)
            status.write("variant_screen", completed=i, total=len(variants), current_variant=v.name)
            print(f"[screen] {i}/{len(variants)} {v.name}", flush=True)

    add_deltas(rows, baseline)
    rows.sort(key=lambda r: r["screen_score"], reverse=True)
    write_csv(run_dir / "variant_screen.csv", rows)

    finalists = rows[:args.top_n]
    by_name = {v.name: v for v in variants}
    full_rows = []
    print(f"[full] replaying top {len(finalists)}", flush=True)
    status.write("full_replay", completed=0, total=len(finalists))
    for i, screen_row in enumerate(finalists, 1):
        v = by_name[screen_row["name"]]
        row = run_variant(result, series, v, months, full=True)
        add_deltas([row], baseline)
        full_rows.append(row)
        write_csv(run_dir / "variant_full.csv", full_rows)
        status.write("full_replay", completed=i, total=len(finalists), current_variant=v.name)
        print(f"[full] {i}/{len(finalists)} {v.name}", flush=True)

    full_sorted = sorted(
        full_rows,
        key=lambda r: (
            r.get("target_delta_vs_base", -999),
            r.get("reference_delta_vs_base", -999),
            r.get("preserve_delta_vs_base", -999),
            -r.get("full_max_dd_pct", 999),
        ),
        reverse=True,
    )
    best_v = by_name[full_sorted[0]["name"]] if full_sorted else variants[0]
    write_csv(run_dir / "echo_repair_path.csv", repair_summary(series, best_v))
    write_json(run_dir / "done.json", {
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "active_version": av.id,
        "baseline": baseline,
        "top_screen": rows[:12],
        "top_full": full_sorted[:12],
        "repair_path_for_best": repair_summary(series, best_v),
    })
    status.write("done", state="done", completed=len(finalists), total=len(finalists))
    print(f"[done] {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        run_dir_arg = None
        for i, arg in enumerate(sys.argv):
            if arg == "--run-dir" and i + 1 < len(sys.argv):
                run_dir_arg = Path(sys.argv[i + 1])
        if run_dir_arg:
            write_json(run_dir_arg / "failed.json", {
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "error": repr(exc),
            })
        raise
