"""Daily attribution for the COVID crash window.

Experiment-only utility. It compares baseline cascade behavior with a
sector-breadth dual-wave variant and writes daily portfolio/trade attribution.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import backtest_cascade as bc
from experiments.calendar_drawdown_2020 import bayes_breadth_dual_wave as dual


BAYES_075 = {
    "crash_k": 0.7066050700236579,
    "call_target": 65.20572525574642,
    "crash_decay": 0.9065225850404621,
    "repair_k": 0.18462727822368538,
    "seed_level": 11.968163196245538,
    "seed_velocity": 23.149756577095793,
    "seed_rsi": 30.877270776587743,
    "relief_brd_start": 36.9817402985341,
    "relief_brd_full": 96.97423215494882,
    "relief_avg5_start": 57.37976044484751,
    "relief_avg5_full": 62.37976044484751,
    "crash_power": 1.6324453278092423,
    "bull_release_k": 0.9911972874757449,
    "put_k": 0.207354557985646,
    "put_target": 39.10165477139365,
    "bull_decay": 0.9305814224774835,
    "bull_velocity": 33.88709405160278,
    "bull_level": 50.21010668080062,
    "bull_wash_level": 44.94447029251013,
    "bull_power": 0.7367677878142324,
}

BAYES_048 = {
    "crash_k": 0.7424326908918757,
    "call_target": 64.81266287027962,
    "crash_decay": 0.8628961496181846,
    "repair_k": 0.3911900998403809,
    "seed_level": 12.383601047549469,
    "seed_velocity": 25.14579043465625,
    "seed_rsi": 33.2925739717179,
    "relief_brd_start": 40.893174978664646,
    "relief_brd_full": 98.29706204432779,
    "relief_avg5_start": 62.658074126997214,
    "relief_avg5_full": 71.6748737557196,
    "crash_power": 1.8387451250790003,
    "bull_release_k": 0.8912810483574242,
    "put_k": 0.05901291649671349,
    "put_target": 39.06864700655217,
    "bull_decay": 0.9174715237321504,
    "bull_velocity": 21.8724761846605,
    "bull_level": 51.149277829194645,
    "bull_wash_level": 23.92930817430166,
    "bull_power": 0.7520687346599473,
}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def variant_from_named(name: str) -> dual.DualVariant:
    variants = {
        "bayes_075": BAYES_075,
        "bayes_048": BAYES_048,
    }
    if name not in variants:
        raise ValueError(f"unknown built-in variant {name!r}")
    return dual.DualVariant(name=name, **variants[name])


def score_to_reason(o, new_score: float, kept: bool, crash: float, bull: float) -> str:
    if o.side == "call":
        if not kept:
            return f"call damped below 70 by crash_echo={crash:.3f}, bull_release={bull:.3f}"
        if new_score < float(o.score):
            return f"call damped {float(o.score):.0f}->{new_score:.0f} by crash_echo={crash:.3f}"
        return "call unchanged"
    if o.side == "put":
        if not kept:
            return f"put neutralized above 25 by bull_wave={bull:.3f}"
        if new_score > float(o.score):
            return f"put softened {float(o.score):.0f}->{new_score:.0f} by bull_wave={bull:.3f}"
        return "put unchanged"
    return "unchanged"


def transform_with_audit(outcomes_by_date: dict, series: dual.DualWaveSeries, variant: dual.DualVariant) -> tuple[dict, list[dict]]:
    out = defaultdict(list)
    audits: list[dict] = []
    for d, outs in outcomes_by_date.items():
        for o in outs:
            crash, bull = series.values_on_or_before(o.signal_date)
            new_score = float(o.score)
            kept = True
            if o.side == "call":
                effective_crash = crash * (1.0 - variant.bull_release_k * (bull ** variant.bull_power))
                if effective_crash > 0:
                    pressure = effective_crash ** variant.crash_power
                    new_score = round(max(0.0, min(100.0, new_score - variant.crash_k * pressure * max(0.0, new_score - variant.call_target))))
                if new_score < 70:
                    kept = False
            elif o.side == "put":
                if bull > 0 and variant.put_k > 0:
                    pressure = bull ** variant.bull_power
                    new_score = round(max(0.0, min(100.0, new_score + variant.put_k * pressure * max(0.0, variant.put_target - new_score))))
                if new_score > 25:
                    kept = False

            audits.append({
                "date": d,
                "symbol": o.symbol,
                "side": o.side,
                "original_score": round(float(o.score), 2),
                "variant_score": round(float(new_score), 2),
                "tier": o.tier,
                "kept": kept,
                "crash_echo": round(float(crash), 6),
                "bull_wave": round(float(bull), 6),
                "reason": score_to_reason(o, new_score, kept, crash, bull),
            })
            if not kept:
                continue
            if o.side == "call":
                tier = o.tier if o.tier == bc.CT_CALL_TIER else bc.score_to_tier(new_score)
            elif o.side == "put":
                tier = o.tier if o.tier == bc.CT_PUT_TIER else bc.put_score_to_tier(new_score)
            else:
                tier = o.tier
            out[d].append(replace(o, score=new_score, tier=tier))

    def sort_key(o):
        side_order = 0 if o.side == "call" else 1
        ct_priority = 0 if (o.side == "call" and o.tier == bc.CT_CALL_TIER and o.score < 95) \
            or (o.side == "put" and o.tier == bc.CT_PUT_TIER and o.score > 15) else 1
        score_key = -o.score if o.side == "call" else o.score
        return side_order, ct_priority, score_key, o.symbol

    for dd in list(out):
        out[dd].sort(key=sort_key)
    return dict(out), audits


def entry_rows(result: dict) -> list[dict]:
    rows = []
    for t in result.get("trade_log", []):
        rows.append({
            "entry_date": t["entry_date"],
            "exit_date": t["exit_date"],
            "symbol": t["symbol"],
            "side": t.get("side"),
            "score": t.get("score"),
            "tier": t.get("tier"),
            "premium": t.get("premium"),
            "outcome": t.get("outcome"),
            "pnl_pct": t.get("pnl_pct"),
            "pnl": t.get("pnl"),
            "source": "closed",
        })
    for t in result.get("open_holdings", []):
        rows.append({
            "entry_date": t["entry_date"],
            "exit_date": None,
            "symbol": t["symbol"],
            "side": t.get("side"),
            "score": t.get("score"),
            "tier": t.get("tier"),
            "premium": t.get("premium"),
            "outcome": "open",
            "pnl_pct": None,
            "pnl": None,
            "source": "open",
        })
    return rows


def daily_report(label: str, result: dict, outcomes_by_date: dict, audits: list[dict] | None, series: dual.DualWaveSeries | None, start: date, end: date) -> tuple[list[dict], list[dict], dict]:
    eq_by_date = dict(result.get("equity_curve", []))
    all_days = [d for d in result.get("all_dates", []) if start <= d <= end]
    entries = entry_rows(result)
    entries_by_day = defaultdict(list)
    for r in entries:
        if start <= r["entry_date"] <= end:
            entries_by_day[r["entry_date"]].append(r)

    audit_by_day = defaultdict(list)
    for a in audits or []:
        if start <= a["date"] <= end:
            audit_by_day[a["date"]].append(a)

    daily = []
    for d in all_days:
        entries_today = entries_by_day.get(d, [])
        candidates = list(outcomes_by_date.get(d, []))
        audit_today = audit_by_day.get(d, [])
        kept = sum(1 for a in audit_today if a["kept"]) if audits is not None else len(candidates)
        dropped = sum(1 for a in audit_today if not a["kept"])
        adjusted = sum(1 for a in audit_today if a["kept"] and a["variant_score"] != a["original_score"])
        dropped_call = sum(1 for a in audit_today if (not a["kept"]) and a["side"] == "call")
        dropped_put = sum(1 for a in audit_today if (not a["kept"]) and a["side"] == "put")
        entry_sides = Counter(r["side"] for r in entries_today)
        candidate_sides = Counter(getattr(o, "side", None) for o in candidates)
        if series is not None:
            crash, bull = series.values_on_or_before(d)
            raw = series.by_date.get(max([x for x in series.dates if x <= d], default=series.dates[0])) if series.dates else {}
        else:
            crash = bull = 0.0
            raw = {}
        if entries_today:
            why = f"opened {len(entries_today)} trades ({dict(entry_sides)})"
        elif audits is not None and dropped and kept == 0:
            why = f"breadth wave dropped all {dropped} candidates"
        elif candidates and not entries_today:
            why = "signals existed but portfolio constraints/re-entry/cash prevented new opens"
        else:
            why = "no qualifying score signals"
        daily.append({
            "variant": label,
            "date": d.isoformat(),
            "equity": round(float(eq_by_date.get(d, 0.0)), 2) if d in eq_by_date else None,
            "drawdown_pct": None,
            "entries": len(entries_today),
            "entry_calls": entry_sides.get("call", 0),
            "entry_puts": entry_sides.get("put", 0),
            "candidate_signals": len(candidates),
            "candidate_calls": candidate_sides.get("call", 0),
            "candidate_puts": candidate_sides.get("put", 0),
            "kept_after_breadth": kept,
            "dropped_by_breadth": dropped,
            "dropped_calls": dropped_call,
            "dropped_puts": dropped_put,
            "adjusted_kept": adjusted,
            "sector_breadth": round(float(raw.get("brd")), 3) if raw.get("brd") is not None else None,
            "sector_rsi": round(float(raw.get("rsi")), 3) if raw.get("rsi") is not None else None,
            "breadth_d5": round(float(raw.get("d5") or 0.0), 3),
            "crash_echo": round(float(crash), 4),
            "bull_wave": round(float(bull), 4),
            "why": why,
        })

    peak = None
    for row in daily:
        if row["equity"] is None:
            continue
        peak = row["equity"] if peak is None else max(peak, row["equity"])
        row["drawdown_pct"] = round((1.0 - row["equity"] / peak) * 100.0, 2) if peak else 0.0

    trade_rows = []
    for d, rows in entries_by_day.items():
        for r in rows:
            trade_rows.append({"variant": label, **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}})

    summary = {
        "variant": label,
        "start_equity": daily[0]["equity"] if daily else None,
        "end_equity": daily[-1]["equity"] if daily else None,
        "return_pct": round((daily[-1]["equity"] / daily[0]["equity"] - 1.0) * 100.0, 2) if daily and daily[0]["equity"] else None,
        "max_window_dd_pct": max((r["drawdown_pct"] or 0.0 for r in daily), default=None),
        "entries": sum(r["entries"] for r in daily),
        "entry_calls": sum(r["entry_calls"] for r in daily),
        "entry_puts": sum(r["entry_puts"] for r in daily),
        "candidate_signals": sum(r["candidate_signals"] for r in daily),
        "dropped_by_breadth": sum(r["dropped_by_breadth"] for r in daily),
        "adjusted_kept": sum(r["adjusted_kept"] for r in daily),
        "worst_days": sorted(daily, key=lambda r: r["drawdown_pct"] or 0.0, reverse=True)[:10],
    }
    return daily, trade_rows, summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version-id", type=int, default=48)
    ap.add_argument("--from", dest="from_date", default="2020-02-15")
    ap.add_argument("--to", dest="to_date", default="2020-06-30")
    ap.add_argument("--report-start", default="2020-02-15")
    ap.add_argument("--report-end", default="2020-06-30")
    ap.add_argument("--variant", default="bayes_075")
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "launch.json", vars(args))

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)
    report_start = date.fromisoformat(args.report_start)
    report_end = date.fromisoformat(args.report_end)
    result = bc.run_cascade_backtest(args.version_id, from_date=from_date, to_date=to_date, initial=50_000.0, verbose=True)

    baseline_daily, baseline_trades, baseline_summary = daily_report(
        "baseline", result, result["outcomes_by_date"], None, None, report_start, report_end
    )

    variant = variant_from_named(args.variant)
    series = dual.DualWaveSeries.load(variant)
    transformed, audits = transform_with_audit(result["outcomes_by_date"], series, variant)
    variant_result = bc.run_backtest(
        transformed,
        result["all_dates"],
        result["initial"],
        regime_dates=result["regime_dates"],
        regime_map=result["regime_map"],
        cfg={},
    )
    variant_result["all_dates"] = result["all_dates"]
    variant_daily, variant_trades, variant_summary = daily_report(
        args.variant, variant_result, result["outcomes_by_date"], audits, series, report_start, report_end
    )

    write_csv(run_dir / "daily_attribution.csv", baseline_daily + variant_daily)
    write_csv(run_dir / "trade_entries.csv", baseline_trades + variant_trades)
    write_csv(run_dir / "signal_audit.csv", [
        {**a, "date": a["date"].isoformat()} for a in audits if report_start <= a["date"] <= report_end
    ])
    summary = {
        "version_id": args.version_id,
        "score_window": [str(from_date), str(to_date)],
        "report_window": [str(report_start), str(report_end)],
        "baseline": baseline_summary,
        args.variant: variant_summary,
        "outputs": {
            "daily_attribution": str(run_dir / "daily_attribution.csv"),
            "trade_entries": str(run_dir / "trade_entries.csv"),
            "signal_audit": str(run_dir / "signal_audit.csv"),
        },
    }
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "done.json", {"summary": str(run_dir / "summary.json")})
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
