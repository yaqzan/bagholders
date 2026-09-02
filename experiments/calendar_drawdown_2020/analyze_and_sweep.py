"""Calendar drawdown investigation for the Mar-Jun 2020 cluster.

Read-only experiment. It runs the current deterministic 30-DTE cascade once,
then replays cached trade outcomes through isolated monthly simulations and
portfolio-stage variants focused on market breadth and sector breadth.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
elif hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

import backtest_cascade as bc
from database.models.core import AlgorithmVersion, MarketBreadth, Stock


SECTOR_ETF_MAP = {
    "technology": "XLK",
    "healthcare": "XLV",
    "consumer-cyclical": "XLY",
    "industrials": "XLI",
    "basic-materials": "XLB",
    "real-estate": "XLRE",
    "financial-services": "XLF",
    "energy": "XLE",
    "consumer-defensive": "XLP",
    "utilities": "XLU",
    "communication-services": "XLC",
}

TARGET_MONTHS = [(2020, 3), (2020, 4), (2020, 5), (2020, 6)]
REFERENCE_MONTHS = [(2026, 5), (2023, 6), (2023, 7), (2023, 1), (2022, 10), (2020, 10), (2020, 9)]
PRESERVE_MONTHS = [(2021, 1), (2021, 2), (2021, 11), (2023, 11), (2024, 2), (2024, 11), (2025, 4)]


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def month_bounds(yr: int, mo: int) -> tuple[date, date]:
    start = date(yr, mo, 1)
    if mo == 12:
        end = date(yr, 12, 31)
    else:
        end = date(yr, mo + 1, 1) - timedelta(days=1)
    return start, end


def month_key(yr: int, mo: int) -> str:
    return f"{yr}-{mo:02d}"


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


class Status:
    def __init__(self, run_dir: Path, total: int | None = None):
        self.run_dir = run_dir
        self.path = run_dir / "status.json"
        self.started_at = iso_now()
        self.total = total

    def write(self, phase: str, state: str = "running", completed: int | None = None, **extra) -> None:
        payload = {
            "phase": phase,
            "state": state,
            "started_at": self.started_at,
            "updated_at": iso_now(),
            "completed": completed,
            "total": self.total,
            "output_paths": {
                "run_dir": str(self.run_dir),
                "log": str(self.run_dir / "run.log"),
                "monthly_diagnostics": str(self.run_dir / "monthly_diagnostics.csv"),
                "variant_screen": str(self.run_dir / "variant_screen.csv"),
                "variant_full": str(self.run_dir / "variant_full.csv"),
            },
        }
        payload.update(extra)
        write_json(self.path, payload)


@dataclass
class Variant:
    name: str
    family: str
    cfg: dict
    filter_kind: str | None = None
    filter_params: dict | None = None
    put_stress: dict | None = None
    saw: dict | None = None


def month_return(result: dict, outcomes_by_date: dict, yr: int, mo: int,
                 cfg: dict | None = None, initial: float = 50_000.0) -> dict:
    start, end = month_bounds(yr, mo)
    settle_end = end + timedelta(days=bc.HOLD_CALENDAR_DAYS + 10)
    mo_outs = {d: outs for d, outs in outcomes_by_date.items() if start <= d <= end}
    mo_days = [d for d in result["all_dates"] if start <= d <= settle_end]
    if not mo_outs or not mo_days:
        return {
            "month": month_key(yr, mo), "return_pct": None, "max_dd_pct": None,
            "n_trades": 0, "call_n": 0, "put_n": 0, "call_pnl": 0.0, "put_pnl": 0.0,
        }
    vac = bc.run_backtest(
        mo_outs,
        mo_days,
        initial,
        regime_dates=result["regime_dates"],
        regime_map=result["regime_map"],
        cfg=cfg or {},
        ph=result.get("ph") if (cfg or {}).get("realloc_strategy") else None,
    )
    eq = vac["equity_curve"][-1][1] if vac["equity_curve"] else initial
    trades = vac["trade_log"]
    calls = [t for t in trades if t.get("side", "call") == "call"]
    puts = [t for t in trades if t.get("side") == "put"]
    return {
        "month": month_key(yr, mo),
        "return_pct": round((eq / initial - 1.0) * 100, 2),
        "max_dd_pct": round(vac["max_dd"] * 100, 2),
        "n_trades": len(trades),
        "call_n": len(calls),
        "put_n": len(puts),
        "tp_rate": round(sum(1 for t in trades if t["outcome"] == "tp") / len(trades) * 100, 1) if trades else None,
        "call_tp_rate": round(sum(1 for t in calls if t["outcome"] == "tp") / len(calls) * 100, 1) if calls else None,
        "put_tp_rate": round(sum(1 for t in puts if t["outcome"] == "tp") / len(puts) * 100, 1) if puts else None,
        "call_pnl": round(sum(float(t.get("pnl") or 0) for t in calls), 2),
        "put_pnl": round(sum(float(t.get("pnl") or 0) for t in puts), 2),
    }


def feature_maps() -> dict:
    stock_sector = {s.symbol: (s.sector or "unknown") for s in Stock.select(Stock.symbol, Stock.sector)}
    b_rows = (MarketBreadth
              .select(MarketBreadth.date, MarketBreadth.breadth_score)
              .where(MarketBreadth.breadth_score.is_null(False))
              .order_by(MarketBreadth.date)
              .tuples())
    market_breadth = {d.isoformat(): float(v) for d, v in b_rows}

    cache = ROOT / ".cache" / "sector_etf_screen"
    wide_path = cache / "etf_features_wide.parquet"
    sec_path = cache / "sector_breadth_daily_2020plus.csv"
    if not wide_path.exists() or not sec_path.exists():
        raise RuntimeError(f"Missing sector ETF cache under {cache}; run experiments/sector_etf_screen/build_features.py first")

    etf_wide = pl.read_parquet(wide_path).with_columns(pl.col("date").cast(pl.Utf8))
    sec_brd = pl.read_csv(sec_path).with_columns(pl.col("date").cast(pl.Utf8))
    etf_by_date = {r["date"]: r for r in etf_wide.iter_rows(named=True)}
    sec_by_date = {r["date"]: r for r in sec_brd.iter_rows(named=True)}
    return {
        "stock_sector": stock_sector,
        "market_breadth": market_breadth,
        "etf_by_date": etf_by_date,
        "sec_by_date": sec_by_date,
    }


def feature_for(outcome, fmap: dict) -> dict:
    d = outcome.signal_date.isoformat()
    sector = fmap["stock_sector"].get(outcome.symbol, "unknown")
    etf = SECTOR_ETF_MAP.get(sector)
    etf_row = fmap["etf_by_date"].get(d, {})
    sec_row = fmap["sec_by_date"].get(d, {})
    return {
        "market_breadth": fmap["market_breadth"].get(d),
        "sector": sector,
        "sector_etf": etf,
        "sec_etf_rsi": float(etf_row.get(f"{etf}_rsi")) if etf and etf_row.get(f"{etf}_rsi") is not None else None,
        "sec_etf_pct_ema50": float(etf_row.get(f"{etf}_pct_ema50")) if etf and etf_row.get(f"{etf}_pct_ema50") is not None else None,
        "sec_brd_ema50": float(sec_row.get("sec_brd_ema50")) if sec_row.get("sec_brd_ema50") is not None else None,
        "sec_avg_rsi": float(sec_row.get("sec_avg_rsi")) if sec_row.get("sec_avg_rsi") is not None else None,
    }


def flatten_outcomes(outcomes_by_date: dict, fmap: dict) -> list[dict]:
    rows = []
    for d, outs in outcomes_by_date.items():
        for o in outs:
            f = feature_for(o, fmap)
            rows.append({
                "symbol": o.symbol,
                "date": d,
                "side": o.side,
                "score": float(o.score),
                "tier": o.tier,
                "outcome": o.outcome,
                "net_return": float(o.net_return),
                **f,
            })
    return rows


def filter_outcomes(outcomes_by_date: dict, variant: Variant, feature_lookup: dict) -> dict:
    if not variant.filter_kind:
        return outcomes_by_date
    out = {}
    p = variant.filter_params or {}
    for d, outs in outcomes_by_date.items():
        kept = []
        for o in outs:
            f = feature_lookup[(o.symbol, o.signal_date, o.side, o.score)]
            drop = False
            if variant.filter_kind == "calls_only":
                drop = o.side == "put"
            elif variant.filter_kind == "puts_only":
                drop = o.side != "put"
            elif variant.filter_kind == "drop_put_market_low":
                brd = f.get("market_breadth")
                drop = o.side == "put" and brd is not None and brd <= p["breadth_le"] and o.score > p["score_gt"]
            elif variant.filter_kind == "drop_put_sector_rsi_low":
                rsi = f.get("sec_etf_rsi")
                drop = o.side == "put" and rsi is not None and rsi <= p["rsi_le"] and o.score > p["score_gt"]
            elif variant.filter_kind == "drop_put_sector_pct_low":
                pct = f.get("sec_etf_pct_ema50")
                drop = o.side == "put" and pct is not None and pct <= p["pct_le"] and o.score > p["score_gt"]
            elif variant.filter_kind == "drop_call_sector_hot":
                rsi = f.get("sec_etf_rsi")
                pct = f.get("sec_etf_pct_ema50")
                drop = o.side == "call" and (
                    (rsi is not None and rsi >= p.get("rsi_ge", 999)) or
                    (pct is not None and pct >= p.get("pct_ge", 999))
                )
            if not drop:
                kept.append(o)
        if kept:
            out[d] = kept
    return out


@contextmanager
def patch_variant(variant: Variant):
    orig_alloc = bc.alloc_scale_for
    orig_saw = bc.saw_put_ucurve_scale
    orig_regime = bc.regime_on_or_before
    try:
        if variant.cfg.get("_alloc_source") == "sector_breadth":
            sec_path = ROOT / ".cache" / "sector_etf_screen" / "sector_breadth_daily_2020plus.csv"
            sec = pl.read_csv(sec_path).with_columns(pl.col("date").cast(pl.Utf8))
            brd_map = {date.fromisoformat(r["date"]): float(r["sec_brd_ema50"]) for r in sec.iter_rows(named=True)}
            dates = sorted(brd_map)

            def sector_regime_on_or_before(sorted_dates, rmap, d, breadth_enabled=None):
                if breadth_enabled is False:
                    return orig_regime(sorted_dates, rmap, d, breadth_enabled=breadth_enabled)
                import bisect
                i = bisect.bisect_right(dates, d) - 1
                return brd_map[dates[i]] if i >= 0 else 50.0

            bc.regime_on_or_before = sector_regime_on_or_before

        if variant.put_stress:
            ps = variant.put_stress

            def alloc_with_put_stress(value, is_put=False, params=None):
                base = orig_alloc(value, is_put=is_put, params=params)
                if not is_put or value is None:
                    return base
                thresh = ps["thresh"]
                low = ps["low"]
                floor = ps["floor"]
                if value >= thresh:
                    return base
                if value <= low:
                    mult = floor
                else:
                    frac = (thresh - value) / max(1.0, thresh - low)
                    mult = 1.0 - frac * (1.0 - floor)
                return max(bc.ALLOC_SCALE_FLOOR, min(bc.ALLOC_SCALE_CEIL, base * mult))

            bc.alloc_scale_for = alloc_with_put_stress

        if variant.saw is not None:
            saw = variant.saw
            sec_path = ROOT / ".cache" / "sector_etf_screen" / "sector_breadth_daily_2020plus.csv"
            sec = pl.read_csv(sec_path).with_columns(pl.col("date").cast(pl.Utf8))
            brd_map = {date.fromisoformat(r["date"]): float(r["sec_brd_ema50"]) for r in sec.iter_rows(named=True)}
            dates = sorted(brd_map)

            def brd_on_or_before(d):
                import bisect
                i = bisect.bisect_right(dates, d) - 1
                return brd_map[dates[i]] if i >= 0 else None

            def saw_scale(d):
                brd = brd_on_or_before(d)
                if brd is None:
                    return 1.0
                if saw["mode"] == "off":
                    return 1.0
                if saw["mode"] == "stress_cut":
                    if brd >= saw["thresh"]:
                        return 1.0
                    if brd <= saw["low"]:
                        return saw["floor"]
                    frac = (saw["thresh"] - brd) / max(1.0, saw["thresh"] - saw["low"])
                    return 1.0 - frac * (1.0 - saw["floor"])
                midpoint = saw["midpoint"]
                halfwidth = saw["halfwidth"]
                floor = saw["floor"]
                ceil = saw["ceil"]
                power = saw.get("power", 2.0)
                d_norm = min(1.0, abs(brd - midpoint) / max(1.0, halfwidth))
                return floor + (d_norm ** power) * (ceil - floor)

            bc.saw_put_ucurve_scale = saw_scale

        yield
    finally:
        bc.alloc_scale_for = orig_alloc
        bc.saw_put_ucurve_scale = orig_saw
        bc.regime_on_or_before = orig_regime


def run_variant(result: dict, variant: Variant, outcomes_by_date: dict,
                feature_lookup: dict, months: list[tuple[int, int]],
                full: bool = False) -> dict:
    filtered = filter_outcomes(outcomes_by_date, variant, feature_lookup)
    with patch_variant(variant):
        month_rows = [month_return(result, filtered, yr, mo, cfg=variant.cfg) for yr, mo in months]
        row = {
            "name": variant.name,
            "family": variant.family,
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
                filtered,
                result["all_dates"],
                result["initial"],
                regime_dates=result["regime_dates"],
                regime_map=result["regime_map"],
                cfg=variant.cfg,
                ph=result.get("ph") if variant.cfg.get("realloc_strategy") else None,
            )
            eq = full_result["equity_curve"][-1][1] if full_result["equity_curve"] else result["initial"]
            row["full_return_pct"] = round((eq / result["initial"] - 1.0) * 100, 2)
            row["full_max_dd_pct"] = round(full_result["max_dd"] * 100, 2)
            row["full_trades"] = len(full_result["trade_log"])
            row["full_puts"] = sum(1 for t in full_result["trade_log"] if t.get("side") == "put")
    return row


def build_variants() -> list[Variant]:
    variants = [
        Variant("baseline", "baseline", {}),
        Variant("calls_only", "ablation", {}, filter_kind="calls_only"),
        Variant("puts_only", "ablation", {}, filter_kind="puts_only"),
        Variant("sector_alloc_current_f3f", "sector_breadth_alloc_source", {"_alloc_source": "sector_breadth"}),
        Variant("saw_off", "sector_breadth_saw", {}, saw={"mode": "off"}),
        Variant("saw_no_amplify", "sector_breadth_saw", {}, saw={
            "mode": "ucurve", "midpoint": 72.0, "halfwidth": 18.0, "floor": 0.55, "ceil": 1.0, "power": 3.0,
        }),
    ]
    for thresh in [20, 25, 30, 35, 40]:
        for floor in [0.70, 0.80, 0.90]:
            variants.append(Variant(
                f"put_stress_T{thresh}_F{int(floor*100)}",
                "market_breadth_put_stress_scale",
                {},
                put_stress={"thresh": float(thresh), "low": 10.0, "floor": float(floor)},
            ))
    for brd in [20, 25, 30, 35, 40]:
        for score_gt in [10, 15, 20]:
            variants.append(Variant(
                f"drop_put_mbrd_le{brd}_scoregt{score_gt}",
                "market_breadth_put_drop",
                {},
                filter_kind="drop_put_market_low",
                filter_params={"breadth_le": float(brd), "score_gt": float(score_gt)},
            ))
    for rsi in [20, 25, 30, 35, 40]:
        for score_gt in [10, 15, 20]:
            variants.append(Variant(
                f"drop_put_sec_rsi_le{rsi}_scoregt{score_gt}",
                "sector_breadth_put_drop",
                {},
                filter_kind="drop_put_sector_rsi_low",
                filter_params={"rsi_le": float(rsi), "score_gt": float(score_gt)},
            ))
    for pct in [-10, -8, -5, -3, 0]:
        variants.append(Variant(
            f"drop_put_sec_pct_le{pct}",
            "sector_breadth_put_drop",
            {},
            filter_kind="drop_put_sector_pct_low",
            filter_params={"pct_le": float(pct), "score_gt": 10.0},
        ))
    for floor in [0.25, 0.35, 0.50]:
        for low in [30, 40, 50]:
            variants.append(Variant(
                f"call_f3f_low{low}_floor{int(floor*100)}",
                "market_breadth_call_scale",
                {"f3f_call_low": float(low), "f3f_call_floor": float(floor), "f3f_call_thresh": 60.0},
            ))
            variants.append(Variant(
                f"sector_call_f3f_low{low}_floor{int(floor*100)}",
                "sector_breadth_call_scale",
                {"_alloc_source": "sector_breadth", "f3f_call_low": float(low), "f3f_call_floor": float(floor), "f3f_call_thresh": 60.0},
            ))
    for thresh in [10, 15, 20, 25]:
        for low in [0, 5, 10]:
            if low >= thresh:
                continue
            for floor in [0.25, 0.40, 0.55, 0.70]:
                variants.append(Variant(
                    f"sector_call_crash_T{thresh}_L{low}_F{int(floor*100)}",
                    "sector_breadth_call_crash_scale",
                    {
                        "_alloc_source": "sector_breadth",
                        "f3f_call_low": float(low),
                        "f3f_call_floor": float(floor),
                        "f3f_call_thresh": float(thresh),
                    },
                ))
    for thresh in [15, 20, 25]:
        for low in [0, 5, 10]:
            if low >= thresh:
                continue
            for floor in [0.40, 0.55, 0.70]:
                for saw_t, saw_f in [(20, 0.75), (25, 0.75), (30, 0.75)]:
                    variants.append(Variant(
                        f"combo_callT{thresh}_L{low}_F{int(floor*100)}_sawT{saw_t}F{int(saw_f*100)}",
                        "combo_sector_call_crash_plus_put_saw",
                        {
                            "_alloc_source": "sector_breadth",
                            "f3f_call_low": float(low),
                            "f3f_call_floor": float(floor),
                            "f3f_call_thresh": float(thresh),
                        },
                        saw={"mode": "stress_cut", "thresh": float(saw_t), "low": 5.0, "floor": float(saw_f)},
                    ))
    for thresh in [20, 25, 30, 35, 40]:
        for floor in [0.70, 0.80, 0.90]:
            variants.append(Variant(
                f"sector_put_stress_T{thresh}_F{int(floor*100)}",
                "sector_breadth_put_stress_scale",
                {"_alloc_source": "sector_breadth"},
                put_stress={"thresh": float(thresh), "low": 5.0, "floor": float(floor)},
            ))
    for rsi in [70, 75]:
        variants.append(Variant(
            f"drop_call_sec_hot_rsi{rsi}",
            "sector_breadth_call_hot_drop",
            {},
            filter_kind="drop_call_sector_hot",
            filter_params={"rsi_ge": float(rsi), "pct_ge": 999.0},
        ))
    for thresh in [15, 20, 25, 30]:
        for floor in [0.60, 0.75, 0.85]:
            variants.append(Variant(
                f"saw_stress_cut_T{thresh}_F{int(floor*100)}",
                "sector_breadth_saw_stress_cut",
                {},
                saw={"mode": "stress_cut", "thresh": float(thresh), "low": 5.0, "floor": float(floor)},
            ))
    return variants


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = sorted({k for r in rows for k in r.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def monthly_diagnostics(result: dict, fmap: dict, run_dir: Path) -> list[dict]:
    outcomes = result["outcomes_by_date"]
    rows = []
    focus = TARGET_MONTHS + REFERENCE_MONTHS + PRESERVE_MONTHS
    for yr, mo in focus:
        full = month_return(result, outcomes, yr, mo)
        calls = month_return(result, {d: [o for o in outs if o.side == "call"] for d, outs in outcomes.items()}, yr, mo)
        puts = month_return(result, {d: [o for o in outs if o.side == "put"] for d, outs in outcomes.items()}, yr, mo)
        rows.append({
            **{f"full_{k}": v for k, v in full.items()},
            "month": month_key(yr, mo),
            "calls_only_return": calls["return_pct"],
            "puts_only_return": puts["return_pct"],
            "calls_only_trades": calls["n_trades"],
            "puts_only_trades": puts["n_trades"],
        })

    flat = flatten_outcomes(outcomes, fmap)
    by_month_side = defaultdict(list)
    for r in flat:
        ym = (r["date"].year, r["date"].month)
        if ym in focus:
            by_month_side[(ym, r["side"])].append(r)
    feature_rows = []
    for (ym, side), vals in sorted(by_month_side.items()):
        feature_rows.append({
            "month": month_key(*ym),
            "side": side,
            "n_outcomes": len(vals),
            "mean_market_breadth": round(sum(v["market_breadth"] for v in vals if v["market_breadth"] is not None) / max(1, sum(1 for v in vals if v["market_breadth"] is not None)), 2),
            "mean_sector_rsi": round(sum(v["sec_etf_rsi"] for v in vals if v["sec_etf_rsi"] is not None) / max(1, sum(1 for v in vals if v["sec_etf_rsi"] is not None)), 2),
            "mean_sector_pct_ema50": round(sum(v["sec_etf_pct_ema50"] for v in vals if v["sec_etf_pct_ema50"] is not None) / max(1, sum(1 for v in vals if v["sec_etf_pct_ema50"] is not None)), 2),
            "mean_sec_brd_ema50": round(sum(v["sec_brd_ema50"] for v in vals if v["sec_brd_ema50"] is not None) / max(1, sum(1 for v in vals if v["sec_brd_ema50"] is not None)), 2),
        })
    write_csv(run_dir / "monthly_diagnostics.csv", rows)
    write_csv(run_dir / "monthly_features_by_side.csv", feature_rows)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    ap.add_argument("--to", dest="to_date", default=None)
    ap.add_argument("--top-n", type=int, default=24)
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "calendar_drawdown_2020" / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Status(run_dir)
    status.write("launch", cwd=str(ROOT), run_dir=str(run_dir))

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
    symbols = {o.symbol for outs in result["outcomes_by_date"].values() for o in outs}
    result["ph"] = bc.load_price_history(symbols, result["start_date"])

    print("[features] loading breadth and sector ETF maps", flush=True)
    status.write("feature_join")
    fmap = feature_maps()
    feature_lookup = {}
    for d, outs in result["outcomes_by_date"].items():
        for o in outs:
            feature_lookup[(o.symbol, o.signal_date, o.side, o.score)] = feature_for(o, fmap)

    print("[diagnostics] writing monthly ablations", flush=True)
    status.write("monthly_diagnostics")
    monthly_diagnostics(result, fmap, run_dir)

    variants = build_variants()
    all_months = TARGET_MONTHS + REFERENCE_MONTHS + PRESERVE_MONTHS
    screen_rows = []
    baseline_row = None
    print(f"[screen] {len(variants)} variants on {len(all_months)} month slices", flush=True)
    status.total = len(variants)
    for i, variant in enumerate(variants, start=1):
        row = run_variant(result, variant, result["outcomes_by_date"], feature_lookup, all_months, full=False)
        if variant.name == "baseline":
            baseline_row = row
        screen_rows.append(row)
        if i % 5 == 0 or i == len(variants):
            write_csv(run_dir / "variant_screen.csv", screen_rows)
            status.write("variant_screen", completed=i, current_variant=variant.name)
            print(f"[screen] {i}/{len(variants)} {variant.name}", flush=True)

    if baseline_row is None:
        baseline_row = screen_rows[0]
    for row in screen_rows:
        row["target_delta_vs_base"] = round(row["target_sum"] - baseline_row["target_sum"], 2)
        row["reference_delta_vs_base"] = round(row["reference_sum"] - baseline_row["reference_sum"], 2)
        row["preserve_delta_vs_base"] = round(row["preserve_sum"] - baseline_row["preserve_sum"], 2)
        row["score"] = round(
            row["target_delta_vs_base"] +
            0.65 * row["reference_delta_vs_base"] +
            min(0.0, row["preserve_delta_vs_base"]) * 1.25,
            2,
        )
    screen_rows.sort(key=lambda r: r["score"], reverse=True)
    write_csv(run_dir / "variant_screen.csv", screen_rows)

    finalists = [r["name"] for r in screen_rows[:args.top_n]]
    variant_by_name = {v.name: v for v in variants}
    full_rows = []
    print(f"[full] replaying top {len(finalists)} variants on full window", flush=True)
    status.total = len(finalists)
    for i, name in enumerate(finalists, start=1):
        variant = variant_by_name[name]
        row = run_variant(result, variant, result["outcomes_by_date"], feature_lookup, all_months, full=True)
        row["target_delta_vs_base"] = round(row["target_sum"] - baseline_row["target_sum"], 2)
        row["reference_delta_vs_base"] = round(row["reference_sum"] - baseline_row["reference_sum"], 2)
        row["preserve_delta_vs_base"] = round(row["preserve_sum"] - baseline_row["preserve_sum"], 2)
        full_rows.append(row)
        write_csv(run_dir / "variant_full.csv", full_rows)
        status.write("full_replay", completed=i, current_variant=name)
        print(f"[full] {i}/{len(finalists)} {name}", flush=True)

    summary = {
        "finished_at": iso_now(),
        "active_version": av.id,
        "baseline": baseline_row,
        "top_screen": screen_rows[:10],
        "top_full": sorted(full_rows, key=lambda r: (r.get("target_delta_vs_base", -999), -r.get("full_max_dd_pct", 999)), reverse=True)[:10],
    }
    write_json(run_dir / "done.json", summary)
    status.write("done", state="done", completed=len(finalists), done_json=str(run_dir / "done.json"))
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
