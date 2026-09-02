"""Compare regular MarketBreadth vs sector ETF breadth around drawdown periods.

Read-only analysis after `trader breadth-backfill 2400` has populated 2020
MarketBreadth rows. Outputs CSV/JSON artifacts under a run directory.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

import backtest_cascade as bc
from database.models.core import AlgorithmVersion, MarketBreadth


TARGET_MONTHS = [(2020, 3), (2020, 4), (2020, 5), (2020, 6)]
REFERENCE_MONTHS = [(2026, 5), (2023, 6), (2023, 7), (2023, 1), (2022, 10), (2020, 10), (2020, 9)]
PRESERVE_MONTHS = [(2021, 1), (2021, 2), (2021, 11), (2023, 11), (2024, 2), (2024, 11), (2025, 4)]
FOCUS_MONTHS = TARGET_MONTHS + REFERENCE_MONTHS + PRESERVE_MONTHS


def month_key(yr: int, mo: int) -> str:
    return f"{yr}-{mo:02d}"


def month_bounds(yr: int, mo: int) -> tuple[date, date]:
    start = date(yr, mo, 1)
    if mo == 12:
        return start, date(yr, 12, 31)
    return start, date(yr, mo + 1, 1) - timedelta(days=1)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def load_market_breadth() -> dict:
    rows = list(
        MarketBreadth.select()
        .where(MarketBreadth.breadth_score.is_null(False))
        .order_by(MarketBreadth.date)
    )
    return {
        r.date: {
            "breadth_score": float(r.breadth_score) if r.breadth_score is not None else None,
            "pct_above_ema50": float(r.pct_above_ema50) if r.pct_above_ema50 is not None else None,
            "pct_above_ema200": float(r.pct_above_ema200) if r.pct_above_ema200 is not None else None,
            "mcclellan_oscillator": float(r.mcclellan_oscillator) if r.mcclellan_oscillator is not None else None,
            "mcclellan_summation": float(r.mcclellan_summation) if r.mcclellan_summation is not None else None,
            "trin": float(r.trin) if r.trin is not None else None,
            "ad_diff": int(r.ad_diff) if r.ad_diff is not None else None,
            "total_issues": int(r.total_issues) if r.total_issues is not None else None,
        }
        for r in rows
    }


def load_sector_breadth() -> dict:
    path = ROOT / ".cache" / "sector_etf_screen" / "sector_breadth_daily_2020plus.csv"
    if not path.exists():
        raise RuntimeError(f"Missing sector breadth cache: {path}")
    df = pl.read_csv(path).with_columns(pl.col("date").cast(pl.Utf8))
    out = {}
    for r in df.iter_rows(named=True):
        out[date.fromisoformat(r["date"])] = {
            "sec_brd_ema50": float(r["sec_brd_ema50"]) if r.get("sec_brd_ema50") is not None else None,
            "sec_brd_ema200": float(r["sec_brd_ema200"]) if r.get("sec_brd_ema200") is not None else None,
            "sec_avg_rsi": float(r["sec_avg_rsi"]) if r.get("sec_avg_rsi") is not None else None,
        }
    return out


def on_or_before(dates: list[date], mapping: dict, d: date):
    import bisect
    i = bisect.bisect_right(dates, d) - 1
    return mapping[dates[i]] if i >= 0 else None


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx == 0 or deny == 0:
        return None
    return round(num / (denx * deny), 4)


def summarize_months(market: dict, sector: dict) -> list[dict]:
    rows = []
    for yr, mo in FOCUS_MONTHS:
        start, end = month_bounds(yr, mo)
        cur = start
        vals = []
        while cur <= end:
            m = market.get(cur)
            s = sector.get(cur)
            if m or s:
                vals.append((cur, m or {}, s or {}))
            cur += timedelta(days=1)

        def metric_values(path):
            arr = []
            for _, m, s in vals:
                if path.startswith("prod_"):
                    src = m
                    key = path.removeprefix("prod_")
                else:
                    src = s
                    key = path.removeprefix("sector_")
                if src.get(key) is not None:
                    arr.append(float(src[key]))
            return arr

        def avg(path):
            arr = metric_values(path)
            return round(sum(arr) / len(arr), 2) if arr else None

        def minv(path):
            arr = metric_values(path)
            return round(min(arr), 2) if arr else None

        def maxv(path):
            arr = metric_values(path)
            return round(max(arr), 2) if arr else None

        rows.append({
            "month": month_key(yr, mo),
            "days_with_prod": sum(1 for _, m, _ in vals if m.get("breadth_score") is not None),
            "days_with_sector": sum(1 for _, _, s in vals if s.get("sec_brd_ema50") is not None),
            "prod_breadth_avg": avg("prod_breadth_score"),
            "prod_breadth_min": minv("prod_breadth_score"),
            "prod_breadth_max": maxv("prod_breadth_score"),
            "prod_pct_ema50_avg": avg("prod_pct_above_ema50"),
            "prod_mcclellan_avg": avg("prod_mcclellan_oscillator"),
            "sec_brd_ema50_avg": avg("sector_sec_brd_ema50"),
            "sec_brd_ema50_min": minv("sector_sec_brd_ema50"),
            "sec_brd_ema50_max": maxv("sector_sec_brd_ema50"),
            "sec_avg_rsi_avg": avg("sector_sec_avg_rsi"),
        })
    return rows


def daily_drawdown_rows(market: dict, sector: dict) -> list[dict]:
    rows = []
    cur = date(2020, 3, 1)
    end = date(2020, 6, 30)
    while cur <= end:
        m = market.get(cur, {})
        s = sector.get(cur, {})
        if m or s:
            rows.append({
                "date": cur.isoformat(),
                **{f"prod_{k}": v for k, v in m.items()},
                **{f"sector_{k}": v for k, v in s.items()},
            })
        cur += timedelta(days=1)
    return rows


def breadth_correlation(market: dict, sector: dict) -> dict:
    xs, ys = [], []
    for d in sorted(set(market) & set(sector)):
        pb = market[d].get("breadth_score")
        sb = sector[d].get("sec_brd_ema50")
        if pb is not None and sb is not None:
            xs.append(pb)
            ys.append(sb)
    return {
        "n_overlap_days": len(xs),
        "pearson_prod_vs_sector": pearson(xs, ys),
        "prod_mean": round(sum(xs) / len(xs), 2) if xs else None,
        "sector_mean": round(sum(ys) / len(ys), 2) if ys else None,
    }


def bin_label(v: float | None, cuts: list[tuple[float, str]]) -> str:
    if v is None:
        return "missing"
    for hi, label in cuts:
        if v <= hi:
            return label
    return cuts[-1][1]


def outcome_quality(out_dir: Path, market: dict, sector: dict, from_date: date) -> list[dict]:
    av = AlgorithmVersion.get_active_scores_version()
    print(f"[outcomes] running cascade outcomes for v{av.id} from {from_date}", flush=True)
    result = bc.run_cascade_backtest(av.id, from_date=from_date, initial=50_000.0, verbose=True)
    if not result:
        return []
    md = sorted(market)
    sd = sorted(sector)
    rows = []
    for outs in result["outcomes_by_date"].values():
        for o in outs:
            m = on_or_before(md, market, o.signal_date) or {}
            s = on_or_before(sd, sector, o.signal_date) or {}
            rows.append({
                "date": o.signal_date,
                "side": o.side,
                "outcome": o.outcome,
                "net_return": float(o.net_return),
                "prod_breadth": m.get("breadth_score"),
                "sector_breadth": s.get("sec_brd_ema50"),
            })

    prod_cuts = [(20, "p00_20"), (35, "p20_35"), (50, "p35_50"), (65, "p50_65"), (80, "p65_80"), (101, "p80_100")]
    sec_cuts = [(20, "s00_20"), (35, "s20_35"), (50, "s35_50"), (65, "s50_65"), (80, "s65_80"), (101, "s80_100")]
    agg = defaultdict(list)
    for r in rows:
        agg[("prod", r["side"], bin_label(r["prod_breadth"], prod_cuts))].append(r)
        agg[("sector", r["side"], bin_label(r["sector_breadth"], sec_cuts))].append(r)

    out = []
    for (source, side, bucket), vals in sorted(agg.items()):
        n = len(vals)
        if n == 0:
            continue
        out.append({
            "source": source,
            "side": side,
            "bucket": bucket,
            "n": n,
            "tp_rate": round(sum(1 for v in vals if v["outcome"] == "tp") / n * 100, 2),
            "sl_rate": round(sum(1 for v in vals if v["outcome"] == "sl") / n * 100, 2),
            "avg_net_return": round(sum(v["net_return"] for v in vals) / n * 100, 3),
        })
    write_csv(out_dir / "breadth_quality_by_side.csv", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--from", dest="from_date", default="2020-01-01")
    args = ap.parse_args()

    out_dir = Path(args.run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    market = load_market_breadth()
    sector = load_sector_breadth()

    coverage = {
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "market_rows": len(market),
        "market_first": min(market).isoformat() if market else None,
        "market_last": max(market).isoformat() if market else None,
        "sector_rows": len(sector),
        "sector_first": min(sector).isoformat() if sector else None,
        "sector_last": max(sector).isoformat() if sector else None,
        **breadth_correlation(market, sector),
    }
    write_json(out_dir / "coverage_and_correlation.json", coverage)
    write_csv(out_dir / "drawdown_2020_daily_breadth.csv", daily_drawdown_rows(market, sector))
    write_csv(out_dir / "focus_month_breadth_summary.csv", summarize_months(market, sector))
    quality = outcome_quality(out_dir, market, sector, date.fromisoformat(args.from_date))
    write_json(out_dir / "done.json", {"finished_at": datetime.now().isoformat(timespec="seconds"), "coverage": coverage, "quality_rows": len(quality)})
    print(f"[done] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
