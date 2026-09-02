"""Test whether sector ETF confirmation can rescue the folded Participation Wave.

Experiment-local only. This joins the folded wave's affected rows to same-day
sector ETF scores, then searches smooth sector-confirmation multipliers that
can prune weak wave boosts without adding a hard post-score barrier.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl
import numpy as np

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter
from experiments.mcd_cwwd_wvd_recal.domain_stress import FOLDED_NO_VELOCITY_PARAMS
from experiments.mcd_cwwd_wvd_recal.participation_wave import _eval_rows, _row_dicts, apply_wave
from experiments.mcd_cwwd_wvd_recal.stage1_gate_check import (
    _discrete_bucket_gate,
    _gradient_gate,
    _window_affected,
    _with_baseline_new,
)

VERSION_ID = 46
LOCAL_CACHE = ROOT / ".cache" / "mcd_cwwd_wvd_recal"
RUNS = ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs"

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
SECTOR_ETFS = sorted(set(SECTOR_ETF_MAP.values()))


@dataclass(frozen=True)
class SectorConfirmParams:
    label: str
    sec_center: float
    sec_width: float
    breadth_center: float
    breadth_width: float
    floor: float
    sec_power: float
    breadth_power: float


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _sigmoid_value(x: pl.Expr, center: float, width: float) -> pl.Expr:
    z = ((x - center) / max(width, 0.01)).clip(-40.0, 40.0)
    return 1.0 / (1.0 + (-z).exp())


def _slug(start_date: str, end_date: str | None) -> str:
    start = f"from{start_date.replace('-', '')}"
    end = f"_to{end_date.replace('-', '')}" if end_date else ""
    return f"{start}{end}_min60"


def _context_path(start_date: str, end_date: str | None) -> Path:
    return LOCAL_CACHE / f"domain_context_v{VERSION_ID}_{_slug(start_date, end_date)}.parquet"


def _sector_cache_path(start_date: str, end_date: str | None) -> Path:
    return LOCAL_CACHE / f"sector_etf_join_v{VERSION_ID}_{_slug(start_date, end_date)}.parquet"


def _load_context(start_date: str, end_date: str | None) -> pl.DataFrame:
    path = _context_path(start_date, end_date)
    if not path.exists():
        raise FileNotFoundError(f"missing {path}; build domain_stress context first")
    df = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Utf8))
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, f"sector_breadth_rescue/context/{start_date}/{end_date or 'open'}")
    return df


def _fetch_sector_join(base: pl.DataFrame, start_date: str, end_date: str | None, force: bool) -> pl.DataFrame:
    cache = _sector_cache_path(start_date, end_date)
    if cache.exists() and not force:
        return pl.read_parquet(cache)

    from database.trader_database import DB

    min_date = base["date"].min()
    max_date = base["date"].max()
    etfs = "', '".join(SECTOR_ETFS)
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    stock_rows = DB.execute_sql("SELECT symbol, sector FROM stocks").fetchall()
    etf_rows = DB.execute_sql(
        f"""
        SELECT symbol, date, overall
        FROM scores FORCE INDEX (scores_version_date_IDX)
        WHERE version_id = {VERSION_ID}
          AND symbol IN ('{etfs}')
          AND date >= '{min_date}' AND date <= '{max_date}'
          AND overall IS NOT NULL
        """
    ).fetchall()

    stock_meta = pl.DataFrame(
        {
            "symbol": [r[0] for r in stock_rows],
            "sector": [(r[1] or "unknown") for r in stock_rows],
        }
    ).unique("symbol", keep="first")
    sec_map = pl.DataFrame({"sector": list(SECTOR_ETF_MAP), "sector_etf": list(SECTOR_ETF_MAP.values())})
    etf_scores = pl.DataFrame(
        {
            "sector_etf": [r[0] for r in etf_rows],
            "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in etf_rows],
            "sec_etf_overall": [float(r[2]) for r in etf_rows],
        }
    )
    etf_scores = pre_cutoff_filter(etf_scores)
    assert_no_holdout_leak(etf_scores, "sector_breadth_rescue/etf_scores")

    breadth = (
        etf_scores.group_by("date")
        .agg(
            [
                (pl.col("sec_etf_overall") >= 55).mean().alias("etf_breadth_55"),
                (pl.col("sec_etf_overall") >= 60).mean().alias("etf_breadth_60"),
                (pl.col("sec_etf_overall") >= 65).mean().alias("etf_breadth_65"),
                (pl.col("sec_etf_overall") >= 70).mean().alias("etf_breadth_70"),
                pl.col("sec_etf_overall").mean().alias("etf_mean_overall"),
                pl.len().alias("etf_score_count"),
            ]
        )
        .sort("date")
    )
    joined = (
        base.select(["symbol", "date"])
        .join(stock_meta, on="symbol", how="left")
        .join(sec_map, on="sector", how="left")
        .join(etf_scores, on=["sector_etf", "date"], how="left")
        .join(breadth, on="date", how="left")
    )
    joined.write_parquet(cache)
    return joined


def _apply_sector_confirm(df: pl.DataFrame, p: SectorConfirmParams) -> pl.DataFrame:
    sec = _sigmoid_value(pl.col("sec_etf_overall").fill_null(50.0), p.sec_center, p.sec_width)
    breadth = _sigmoid_value(pl.col("etf_breadth_60").fill_null(0.0), p.breadth_center, p.breadth_width)
    sec_part = sec ** p.sec_power
    breadth_part = breadth ** p.breadth_power
    confirm = (p.floor + (1.0 - p.floor) * sec_part * breadth_part).clip(0.0, 1.0)
    boost = pl.col("base_wave_boost") * confirm
    return df.with_columns(
        [
            confirm.alias("sector_confirm"),
            boost.alias("wave_boost"),
            (pl.col("overall") + boost).round().clip(0, 100).cast(pl.Int64).alias("new_score"),
        ]
    )


def _wr(df: pl.DataFrame, col: str = "win_7d") -> tuple[float | None, int]:
    resolved = df.filter(pl.col(col).is_not_null())
    if resolved.is_empty():
        return None, 0
    return 100.0 * resolved.filter(pl.col(col) == 1).height / resolved.height, resolved.height


def _diag_bins(df: pl.DataFrame) -> dict[str, Any]:
    bins: dict[str, pl.Expr] = {
        "sec_missing": pl.col("sec_etf_overall").is_null(),
        "sec_lt55": pl.col("sec_etf_overall") < 55,
        "sec_55_59": pl.col("sec_etf_overall").is_between(55, 59),
        "sec_60_64": pl.col("sec_etf_overall").is_between(60, 64),
        "sec_65_69": pl.col("sec_etf_overall").is_between(65, 69),
        "sec_ge70": pl.col("sec_etf_overall") >= 70,
        "breadth60_lt40": pl.col("etf_breadth_60") < 0.40,
        "breadth60_40_59": pl.col("etf_breadth_60").is_between(0.40, 0.599999),
        "breadth60_60_79": pl.col("etf_breadth_60").is_between(0.60, 0.799999),
        "breadth60_ge80": pl.col("etf_breadth_60") >= 0.80,
    }
    out: dict[str, Any] = {}
    for label, expr in bins.items():
        sub = df.filter(expr)
        wr7, n7 = _wr(sub, "win_7d")
        wr15, n15 = _wr(sub, "win_15d")
        wr30, n30 = _wr(sub, "win_30d")
        out[label] = {"rows": sub.height, "wr7": wr7, "wr7_n": n7, "wr15": wr15, "wr15_n": n15, "wr30": wr30, "wr30_n": n30}
    return out


def _summarize_candidate(df: pl.DataFrame) -> dict[str, Any]:
    affected_expr = pl.col("wave_boost") >= 0.5
    windows = _window_affected(df, affected_expr)
    w1 = windows["WR7"]
    baseline = _eval_rows(_row_dicts(_with_baseline_new(df)))
    candidate = _eval_rows(_row_dicts(df))
    w4 = _discrete_bucket_gate(baseline, candidate)
    w6 = _gradient_gate(baseline, candidate)
    affected = df.filter(affected_expr)
    promos = {
        "65_69_to_70": affected.filter((pl.col("overall").is_between(65, 69)) & (pl.col("new_score") >= 70)).height,
        "70_74_to_75": affected.filter((pl.col("overall").is_between(70, 74)) & (pl.col("new_score") >= 75)).height,
    }
    return {
        "affected_n": affected.height,
        "affected_wr7": w1["affected_wr"],
        "rest_wr7": w1["rest_wr"],
        "lift_pp": w1["lift_pp"],
        "z": w1["z"],
        "w2_lifts": {k: v["lift_pp"] for k, v in windows.items()},
        "w4_pass": w4["pass"],
        "w6_pass": w6["pass"],
        "promotion_counts": promos,
        "tiers": candidate["tiers"],
        "poet": candidate["poet"],
        "gradient_worsened": w6["worsened_breaches"],
    }


def _load_enriched(start_date: str, end_date: str | None, force: bool) -> pl.DataFrame:
    base = _load_context(start_date, end_date)
    varied = apply_wave(base, FOLDED_NO_VELOCITY_PARAMS).rename({"wave_boost": "base_wave_boost"})
    sector = _fetch_sector_join(base, start_date, end_date, force=force)
    df = varied.join(sector, on=["symbol", "date"], how="left")
    return df.with_columns(
        [
            pl.col("sec_etf_overall").cast(pl.Float64),
            pl.col("etf_breadth_60").cast(pl.Float64),
            pl.col("base_wave_boost").fill_null(0.0),
        ]
    )


def _grid() -> list[SectorConfirmParams]:
    params: list[SectorConfirmParams] = []
    for sec_center in [52.5, 55.0, 57.5, 60.0, 62.5, 65.0]:
        for sec_width in [5.0, 8.0, 12.0, 18.0]:
            for breadth_center in [0.35, 0.45, 0.55, 0.65]:
                for breadth_width in [0.08, 0.12, 0.18, 0.25]:
                    for floor in [0.0, 0.15, 0.30, 0.50]:
                        for sec_power in [0.75, 1.0, 1.5]:
                            for breadth_power in [0.0, 0.75, 1.0, 1.5]:
                                params.append(
                                    SectorConfirmParams(
                                        label=(
                                            f"sec{sec_center:g}_w{sec_width:g}_br{breadth_center:g}_"
                                            f"bw{breadth_width:g}_fl{floor:g}_sp{sec_power:g}_bp{breadth_power:g}"
                                        ),
                                        sec_center=sec_center,
                                        sec_width=sec_width,
                                        breadth_center=breadth_center,
                                        breadth_width=breadth_width,
                                        floor=floor,
                                        sec_power=sec_power,
                                        breadth_power=breadth_power,
                                    )
                                )
    return params


def _quick_score(df: pl.DataFrame, p: SectorConfirmParams) -> dict[str, Any]:
    candidate = _apply_sector_confirm(df, p)
    affected_expr = pl.col("wave_boost") >= 0.5
    windows = _window_affected(candidate, affected_expr)
    w1 = windows["WR7"]
    lifts = [windows[f"WR{w}"]["lift_pp"] for w in [3, 5, 7, 15, 30]]
    affected_n = w1["affected_n"] or 0
    z = w1["z"] or -99.0
    min_lift = min(v for v in lifts if v is not None) if all(v is not None for v in lifts) else -99.0
    utility = z + 0.04 * min_lift + min(0.0, (affected_n - 80) / 80.0)
    return {
        "label": p.label,
        "params": asdict(p),
        "affected_n": affected_n,
        "affected_wr7": w1["affected_wr"],
        "rest_wr7": w1["rest_wr"],
        "lift_pp": w1["lift_pp"],
        "z": w1["z"],
        "min_w2_lift": min_lift,
        "utility": utility,
    }


def _prepare_quick_arrays(df: pl.DataFrame) -> dict[str, Any]:
    return {
        "base_boost": df["base_wave_boost"].fill_null(0.0).to_numpy(),
        "sec_score": df["sec_etf_overall"].fill_null(50.0).to_numpy(),
        "breadth60": df["etf_breadth_60"].fill_null(0.0).to_numpy(),
        "wins": {w: df[f"win_{w}d"].fill_null(-1).to_numpy() for w in [3, 5, 7, 15, 30]},
    }


def _quick_z(p1: float, n1: int, p0: float, n0: int) -> float | None:
    if n1 <= 0 or n0 <= 0:
        return None
    p1f = p1 / 100.0
    p0f = p0 / 100.0
    pooled = ((p1f * n1) + (p0f * n0)) / (n1 + n0)
    se = math.sqrt(max(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n0), 1e-12))
    return (p1f - p0f) / se


def _quick_wr(win: np.ndarray, mask: np.ndarray) -> tuple[float | None, int]:
    resolved = mask & (win >= 0)
    n = int(resolved.sum())
    if n == 0:
        return None, 0
    return float(100.0 * (win[resolved] == 1).sum() / n), n


def _quick_score_arrays(arr: dict[str, Any], p: SectorConfirmParams) -> dict[str, Any]:
    sec_z = np.clip((arr["sec_score"] - p.sec_center) / max(p.sec_width, 0.01), -40.0, 40.0)
    br_z = np.clip((arr["breadth60"] - p.breadth_center) / max(p.breadth_width, 0.01), -40.0, 40.0)
    sec = 1.0 / (1.0 + np.exp(-sec_z))
    breadth = 1.0 / (1.0 + np.exp(-br_z))
    confirm = np.clip(p.floor + (1.0 - p.floor) * (sec**p.sec_power) * (breadth**p.breadth_power), 0.0, 1.0)
    affected = (arr["base_boost"] * confirm) >= 0.5
    rest = ~affected
    windows: dict[int, dict[str, Any]] = {}
    for w, win in arr["wins"].items():
        wr_aff, n_aff = _quick_wr(win, affected)
        wr_rest, n_rest = _quick_wr(win, rest)
        lift = None if wr_aff is None or wr_rest is None else wr_aff - wr_rest
        z = None if wr_aff is None or wr_rest is None else _quick_z(wr_aff, n_aff, wr_rest, n_rest)
        windows[w] = {
            "affected_wr": wr_aff,
            "affected_n": n_aff,
            "rest_wr": wr_rest,
            "rest_n": n_rest,
            "lift_pp": lift,
            "z": z,
        }
    w1 = windows[7]
    lifts = [windows[w]["lift_pp"] for w in [3, 5, 7, 15, 30]]
    affected_n = w1["affected_n"] or 0
    z = w1["z"] or -99.0
    min_lift = min(v for v in lifts if v is not None) if all(v is not None for v in lifts) else -99.0
    utility = z + 0.04 * min_lift + min(0.0, (affected_n - 80) / 80.0)
    return {
        "label": p.label,
        "params": asdict(p),
        "affected_n": affected_n,
        "affected_wr7": w1["affected_wr"],
        "rest_wr7": w1["rest_wr"],
        "lift_pp": w1["lift_pp"],
        "z": w1["z"],
        "min_w2_lift": min_lift,
        "utility": utility,
    }


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else RUNS / f"sector_breadth_rescue_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    _write_json(
        status_path,
        {
            "phase": "load",
            "state": "running",
            "started_at": _now(),
            "run_dir": str(run_dir),
        },
    )
    left = _load_enriched("2016-01-01", "2020-12-31", force=args.force_sector_cache)
    right = _load_enriched("2020-01-01", None, force=args.force_sector_cache)

    left_base = _apply_sector_confirm(
        left,
        SectorConfirmParams("no_sector", 0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0),
    )
    right_base = _apply_sector_confirm(
        right,
        SectorConfirmParams("no_sector", 0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0),
    )
    left_affected = left_base.filter(pl.col("wave_boost") >= 0.5)
    right_affected = right_base.filter(pl.col("wave_boost") >= 0.5)

    rows = []
    top: list[tuple[float, SectorConfirmParams, dict[str, Any]]] = []
    grid = _grid()
    quick_arrays = _prepare_quick_arrays(left)
    for idx, p in enumerate(grid, 1):
        left_score = _quick_score_arrays(quick_arrays, p)
        rows.append(left_score)
        top.append((left_score["utility"], p, left_score))
        top.sort(key=lambda x: x[0], reverse=True)
        if len(top) > args.top:
            top.pop()
        if idx % 1000 == 0:
            print(f"[grid] {idx:,} tested; best z={top[0][2]['z']}", flush=True)
            _write_json(
                status_path,
                {
                    "phase": "grid",
                    "state": "running",
                    "completed": idx,
                    "total": len(grid),
                    "best": top[0][2],
                    "updated_at": _now(),
                    "run_dir": str(run_dir),
                },
            )

    flat_rows = []
    for row in rows:
        item = dict(row)
        item["params_json"] = json.dumps(item.pop("params"), sort_keys=True)
        flat_rows.append(item)
    scored = pl.DataFrame(flat_rows).sort("utility", descending=True)
    scored.write_csv(run_dir / "sector_grid_2016_2020.csv")

    detailed = []
    for _, p, left_score in top:
        left_candidate = _apply_sector_confirm(left, p)
        right_candidate = _apply_sector_confirm(right, p)
        detailed.append(
            {
                "label": p.label,
                "params": asdict(p),
                "left_quick": left_score,
                "left_full": _summarize_candidate(left_candidate),
                "right_full": _summarize_candidate(right_candidate),
            }
        )

    summary = {
        "generated_at": _now(),
        "run_dir": str(run_dir),
        "params_source": "folded-no-velocity Participation Wave plus smooth sector ETF confirmation multiplier",
        "left_period": "2016-01-01..2020-12-31",
        "right_period": "2020-01-01..holdout_cutoff",
        "coverage": {
            "left_rows": left.height,
            "left_sector_score_coverage": left.filter(pl.col("sec_etf_overall").is_not_null()).height,
            "right_rows": right.height,
            "right_sector_score_coverage": right.filter(pl.col("sec_etf_overall").is_not_null()).height,
        },
        "baseline_folded": {
            "left": _summarize_candidate(left_base),
            "right": _summarize_candidate(right_base),
        },
        "diagnostic_bins": {
            "left_affected": _diag_bins(left_affected),
            "right_affected": _diag_bins(right_affected),
        },
        "top_candidates": detailed,
    }
    _write_json(run_dir / "sector_breadth_rescue_summary.json", summary)
    _write_json(
        status_path,
        {
            "phase": "done",
            "state": "done",
            "completed": len(grid),
            "total": len(grid),
            "summary": str(run_dir / "sector_breadth_rescue_summary.json"),
            "grid": str(run_dir / "sector_grid_2016_2020.csv"),
            "updated_at": _now(),
            "run_dir": str(run_dir),
        },
    )
    _write_json(
        run_dir / "done.json",
        {
            "finished_at": _now(),
            "summary": str(run_dir / "sector_breadth_rescue_summary.json"),
            "grid": str(run_dir / "sector_grid_2016_2020.csv"),
        },
    )
    print(f"[save] {run_dir / 'sector_breadth_rescue_summary.json'}", flush=True)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--force-sector-cache", action="store_true")
    ap.add_argument("--run-dir")
    args = ap.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
