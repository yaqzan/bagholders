"""Sector breadth-thrust score-modifier sweep for the 2020 calendar drawdown.

Read-only experiment. It approximates a score-side modifier by transforming
cached deterministic trade outcomes before the cascade allocator sees them:

* negative sector breadth thrust dampens CALL scores toward neutral;
* positive rebound thrust after a recent sector washout dampens PUT scores
  toward neutral.

This deliberately does not modify production scoring code.
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
from datetime import date, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
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


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        self.started_at = iso_now()

    def write(self, phase: str, state: str = "running", completed: int | None = None,
              total: int | None = None, **extra) -> None:
        payload = {
            "phase": phase,
            "state": state,
            "completed": completed,
            "total": total,
            "started_at": self.started_at,
            "updated_at": iso_now(),
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


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class Variant:
    name: str
    call_k: float = 0.0
    call_target: float = 60.0
    call_level_hi: float = 35.0
    call_level_lo: float = 5.0
    call_neg_start: float = 5.0
    call_neg_full: float = 35.0
    call_power: float = 1.0
    put_k: float = 0.0
    put_target: float = 35.0
    put_wash_hi: float = 25.0
    put_wash_lo: float = 0.0
    put_pos_start: float = 5.0
    put_pos_full: float = 35.0
    put_level_start: float = 55.0
    put_level_full: float = 90.0
    put_power: float = 1.0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class SectorThrust:
    def __init__(self, rows: list[dict]):
        self.by_date = {r["date"]: r for r in rows}
        self.dates = sorted(self.by_date)

    @classmethod
    def load(cls) -> "SectorThrust":
        path = ROOT / ".cache" / "sector_etf_screen" / "sector_breadth_daily_2020plus.csv"
        if not path.exists():
            raise RuntimeError(f"Missing sector breadth cache: {path}")
        df = pl.read_csv(path).with_columns(pl.col("date").cast(pl.Utf8))
        raw = []
        for r in df.iter_rows(named=True):
            raw.append({
                "date": date.fromisoformat(r["date"]),
                "brd": float(r["sec_brd_ema50"]) if r.get("sec_brd_ema50") is not None else None,
                "avg_rsi": float(r["sec_avg_rsi"]) if r.get("sec_avg_rsi") is not None else None,
            })
        raw.sort(key=lambda r: r["date"])
        brds = [r["brd"] for r in raw]
        for i, r in enumerate(raw):
            b = r["brd"]
            if b is None:
                r.update({"d5": None, "d10": None, "min20": None})
                continue
            r["d5"] = b - brds[i - 5] if i >= 5 and brds[i - 5] is not None else 0.0
            r["d10"] = b - brds[i - 10] if i >= 10 and brds[i - 10] is not None else 0.0
            win = [x for x in brds[max(0, i - 19): i + 1] if x is not None]
            r["min20"] = min(win) if win else b
        return cls(raw)

    def on_or_before(self, d: date) -> dict | None:
        import bisect
        i = bisect.bisect_right(self.dates, d) - 1
        return self.by_date[self.dates[i]] if i >= 0 else None


def call_pressure(f: dict | None, v: Variant) -> float:
    if not f or f.get("brd") is None:
        return 0.0
    brd = float(f["brd"])
    d5 = float(f.get("d5") or 0.0)
    d10 = float(f.get("d10") or 0.0)
    level = clamp((v.call_level_hi - brd) / max(1.0, v.call_level_hi - v.call_level_lo))
    neg = clamp((max(-d5, -d10 * 0.75) - v.call_neg_start) / max(1.0, v.call_neg_full - v.call_neg_start))
    pressure = max(level, neg)
    return pressure ** v.call_power


def put_rebound_pressure(f: dict | None, v: Variant) -> float:
    if not f or f.get("brd") is None:
        return 0.0
    brd = float(f["brd"])
    d5 = float(f.get("d5") or 0.0)
    d10 = float(f.get("d10") or 0.0)
    min20 = float(f.get("min20") if f.get("min20") is not None else brd)
    washed = clamp((v.put_wash_hi - min20) / max(1.0, v.put_wash_hi - v.put_wash_lo))
    thrust = clamp((max(d5, d10 * 0.75) - v.put_pos_start) / max(1.0, v.put_pos_full - v.put_pos_start))
    level = clamp((brd - v.put_level_start) / max(1.0, v.put_level_full - v.put_level_start))
    pressure = washed * max(thrust, level)
    return pressure ** v.put_power


def transform_outcomes(outcomes_by_date: dict, thrust: SectorThrust, v: Variant) -> tuple[dict, dict]:
    out = defaultdict(list)
    stats = {
        "call_adjusted": 0,
        "call_dropped": 0,
        "put_adjusted": 0,
        "put_dropped": 0,
        "call_score_drop_sum": 0.0,
        "put_score_lift_sum": 0.0,
    }
    for d, outs in outcomes_by_date.items():
        for o in outs:
            f = thrust.on_or_before(o.signal_date)
            new_score = float(o.score)
            if o.side == "call" and v.call_k > 0:
                p = call_pressure(f, v)
                if p > 0:
                    candidate = new_score - v.call_k * p * max(0.0, new_score - v.call_target)
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
            elif o.side == "put" and v.put_k > 0:
                p = put_rebound_pressure(f, v)
                if p > 0:
                    candidate = new_score + v.put_k * p * max(0.0, v.put_target - new_score)
                    candidate = round(max(0.0, min(100.0, candidate)))
                    if candidate > new_score:
                        stats["put_adjusted"] += 1
                        stats["put_score_lift_sum"] += candidate - new_score
                    new_score = candidate
                if new_score > 25:
                    stats["put_dropped"] += 1
                    continue
                tier = o.tier if o.tier == bc.CT_PUT_TIER else bc.put_score_to_tier(new_score)
                out[d].append(replace(o, score=new_score, tier=tier))
            else:
                out[d].append(o)

    def sort_key(o):
        side_order = 0 if o.side == "call" else 1
        ct_priority = 0 if (o.side == "call" and o.tier == bc.CT_CALL_TIER and o.score < 95) \
            or (o.side == "put" and o.tier == bc.CT_PUT_TIER and o.score > 15) else 1
        score_key = -o.score if o.side == "call" else o.score
        return side_order, ct_priority, score_key, o.symbol

    for d in list(out):
        out[d].sort(key=sort_key)
    return dict(out), stats


def run_variant(result: dict, thrust: SectorThrust, v: Variant, months: list[tuple[int, int]],
                full: bool = False) -> dict:
    transformed, stats = transform_outcomes(result["outcomes_by_date"], thrust, v)
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


def build_variants() -> list[Variant]:
    variants = [Variant("baseline")]

    for k in [0.40, 0.60, 0.80, 1.00]:
        for level_hi in [25.0, 35.0, 45.0]:
            for target in [55.0, 60.0, 65.0]:
                variants.append(Variant(
                    name=f"call_crash_K{int(k*100)}_L{int(level_hi)}_T{int(target)}",
                    call_k=k,
                    call_target=target,
                    call_level_hi=level_hi,
                    call_level_lo=5.0,
                    call_neg_start=5.0,
                    call_neg_full=35.0,
                    call_power=1.0,
                ))

    for k in [0.40, 0.60, 0.80, 1.00]:
        for wash_hi in [20.0, 30.0, 40.0]:
            for target in [30.0, 35.0, 40.0]:
                variants.append(Variant(
                    name=f"put_rebound_K{int(k*100)}_W{int(wash_hi)}_T{int(target)}",
                    put_k=k,
                    put_target=target,
                    put_wash_hi=wash_hi,
                    put_wash_lo=0.0,
                    put_pos_start=5.0,
                    put_pos_full=35.0,
                    put_level_start=50.0,
                    put_level_full=85.0,
                    put_power=1.0,
                ))

    for call_k in [0.40, 0.60, 0.80]:
        for put_k in [0.40, 0.60, 0.80, 1.00]:
            for call_level_hi in [25.0, 35.0, 45.0]:
                for wash_hi in [20.0, 30.0, 40.0]:
                    variants.append(Variant(
                        name=(
                            f"combo_CK{int(call_k*100)}_CL{int(call_level_hi)}"
                            f"_PK{int(put_k*100)}_W{int(wash_hi)}"
                        ),
                        call_k=call_k,
                        call_target=60.0,
                        call_level_hi=call_level_hi,
                        call_level_lo=5.0,
                        call_neg_start=5.0,
                        call_neg_full=35.0,
                        call_power=1.0,
                        put_k=put_k,
                        put_target=35.0,
                        put_wash_hi=wash_hi,
                        put_wash_lo=0.0,
                        put_pos_start=5.0,
                        put_pos_full=35.0,
                        put_level_start=50.0,
                        put_level_full=85.0,
                        put_power=1.0,
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    ap.add_argument("--to", dest="to_date", default=None)
    ap.add_argument("--top-n", type=int, default=30)
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "calendar_drawdown_2020" / "runs" / f"breadth_thrust_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Status(run_dir)
    write_json(run_dir / "launch.json", {
        "started_at": iso_now(),
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

    thrust = SectorThrust.load()
    variants = build_variants()
    months = TARGET_MONTHS + REFERENCE_MONTHS + PRESERVE_MONTHS
    rows = []
    baseline = None
    status.write("variant_screen", completed=0, total=len(variants))
    print(f"[screen] {len(variants)} variants across {len(months)} month slices", flush=True)
    for i, v in enumerate(variants, 1):
        row = run_variant(result, thrust, v, months, full=False)
        rows.append(row)
        if v.name == "baseline":
            baseline = row
        if i % 10 == 0 or i == len(variants):
            if baseline:
                add_deltas(rows, baseline)
            write_csv(run_dir / "variant_screen.csv", rows)
            status.write("variant_screen", completed=i, total=len(variants), current_variant=v.name)
            print(f"[screen] {i}/{len(variants)} {v.name}", flush=True)

    if baseline is None:
        raise RuntimeError("baseline variant missing")
    add_deltas(rows, baseline)
    rows.sort(key=lambda r: r["screen_score"], reverse=True)
    write_csv(run_dir / "variant_screen.csv", rows)

    finalists = rows[:args.top_n]
    by_name = {v.name: v for v in variants}
    full_rows = []
    print(f"[full] replaying top {len(finalists)} variants", flush=True)
    status.write("full_replay", completed=0, total=len(finalists))
    for i, screen_row in enumerate(finalists, 1):
        v = by_name[screen_row["name"]]
        row = run_variant(result, thrust, v, months, full=True)
        add_deltas([row], baseline)
        full_rows.append(row)
        write_csv(run_dir / "variant_full.csv", full_rows)
        status.write("full_replay", completed=i, total=len(finalists), current_variant=v.name)
        print(f"[full] {i}/{len(finalists)} {v.name}", flush=True)

    full_sorted = sorted(
        full_rows,
        key=lambda r: (
            r.get("target_delta_vs_base", -999),
            -r.get("full_max_dd_pct", 999),
            r.get("reference_delta_vs_base", -999),
        ),
        reverse=True,
    )
    summary = {
        "finished_at": iso_now(),
        "active_version": av.id,
        "from_date": from_date,
        "to_date": to_date,
        "baseline": baseline,
        "top_screen": rows[:12],
        "top_full": full_sorted[:12],
    }
    write_json(run_dir / "done.json", summary)
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
            write_json(run_dir_arg / "failed.json", {"finished_at": iso_now(), "error": repr(exc)})
        raise
