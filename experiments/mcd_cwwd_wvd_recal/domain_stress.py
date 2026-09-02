"""Broad-domain stress check for the Participation Wave candidate.

The first pass only used the existing 70+ call-peak cache. This script expands
the eligible score domain down to 60 so rows that could be naturally promoted
into the 70+ call ladder are visible before any production implementation.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

from experiments._holdout import assert_no_holdout_leak, cutoff_iso, pre_cutoff_filter
from experiments.mcd_cwwd_wvd_recal.participation_wave import (
    CALL_BUCKETS,
    CALL_TIERS,
    LOCAL_CACHE,
    LOOKBACK_DAYS,
    VERSION_ID,
    WaveParams,
    _eval_rows,
    _row_dicts,
    apply_wave,
)
from experiments.mcd_cwwd_wvd_recal.stage1_gate_check import (
    _capacity_gate,
    _discrete_bucket_gate,
    _gradient_gate,
    _period_gate,
    _window_affected,
    _with_baseline_new,
)


DEFAULT_PARAMS = WaveParams(
    k=0.5017373724274821,
    cap=7.555858896541027,
    dv_mid=-0.24148461232315205,
    dv_slope=2.024219288154666,
    impulse_mid=0.2578362781694005,
    impulse_slope=2.757542384168292,
    vel_mid=2.0451319450979835,
    vel_slope=0.2509520500710184,
    wadj_mid=8.114603099748178,
    wadj_slope=0.5130872479229367,
    force_peak=-0.06510393307463695,
    force_width=0.8989872330347107,
    pressure_floor=2.5447923378625577,
)

FOLDED_NO_VELOCITY_PARAMS = WaveParams(
    k=0.37573770294818865,
    cap=7.555858896541027,
    dv_mid=-0.24148461232315205,
    dv_slope=2.024219288154666,
    impulse_mid=0.2578362781694005,
    impulse_slope=2.757542384168292,
    vel_mid=0.0,
    vel_slope=0.0,
    wadj_mid=8.114603099748178,
    wadj_slope=0.5130872479229367,
    force_peak=-0.06510393307463695,
    force_width=0.8989872330347107,
    pressure_floor=2.5447923378625577,
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _load_weight_info(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _month_ranges(start_iso: str, stop_iso: str) -> list[tuple[str, str]]:
    start = date.fromisoformat(start_iso)
    stop = date.fromisoformat(stop_iso)
    ranges: list[tuple[str, str]] = []
    cur = date(start.year, start.month, 1)
    while cur <= stop:
        if cur.month == 12:
            next_month = date(cur.year + 1, 1, 1)
        else:
            next_month = date(cur.year, cur.month + 1, 1)
        lo = max(cur, start)
        hi = min(next_month - timedelta(days=1), stop)
        ranges.append((lo.isoformat(), hi.isoformat()))
        cur = next_month
    return ranges


def _cache_span(min_score: int, start_date: str | None, end_date: str | None) -> str:
    if start_date or end_date:
        start_part = f"from{start_date.replace('-', '')}" if start_date else f"last{LOOKBACK_DAYS}"
        end_part = f"_to{end_date.replace('-', '')}" if end_date else ""
        return f"{start_part}{end_part}_min{min_score}"
    return f"{LOOKBACK_DAYS}_min{min_score}"


def _scores_cache_path(min_score: int, start_date: str | None, end_date: str | None) -> Path:
    return LOCAL_CACHE / f"domain_scores_v{VERSION_ID}_{_cache_span(min_score, start_date, end_date)}.parquet"


def _price_cache_path(min_score: int, start_date: str | None, end_date: str | None) -> Path:
    return LOCAL_CACHE / f"domain_price_context_v{VERSION_ID}_{_cache_span(min_score, start_date, end_date)}.parquet"


def _context_cache_path(min_score: int, start_date: str | None, end_date: str | None) -> Path:
    return LOCAL_CACHE / f"domain_context_v{VERSION_ID}_{_cache_span(min_score, start_date, end_date)}.parquet"


def _pull_scores(
    min_score: int,
    force: bool,
    start_date: str | None,
    end_date: str | None,
    status_path: Path | None = None,
) -> pl.DataFrame:
    out = _scores_cache_path(min_score, start_date, end_date)
    if out.exists() and not force:
        print(f"[cache] scores hit: {out}", flush=True)
        return pl.read_parquet(out)

    from database.trader_database import DB

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    start = start_date or (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    stop = min(end_date or cutoff_iso(), cutoff_iso())
    rows: list[tuple[Any, ...]] = []
    ranges = _month_ranges(start, stop)
    print(f"[scores] pulling v{VERSION_ID} overall>={min_score} {start}..{stop}", flush=True)
    for idx, (y_start, y_end) in enumerate(ranges, 1):
        t0 = time.perf_counter()
        cur = DB.execute_sql(
            f"""
            SELECT symbol, date, overall, trend, bb, rsi, macd, stoch,
                   technical_alignment, volume_signal, volume_magnitude,
                   weight_info, score_velocity_7d, pct_from_ema50, price
            FROM scores FORCE INDEX (scores_version_date_IDX)
            WHERE version_id = {VERSION_ID}
              AND date >= '{y_start}' AND date <= '{y_end}'
              AND overall IS NOT NULL
              AND overall >= {int(min_score)}
            """
        )
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f"[scores]   {y_start}..{y_end}: {len(chunk):,} rows ({time.perf_counter()-t0:.1f}s)", flush=True)
        if status_path is not None:
            _write_json(
                status_path,
                {
                    "phase": "pull_scores",
                    "state": "running",
                    "completed": idx,
                    "total": len(ranges),
                    "rows": len(rows),
                    "current_range": [y_start, y_end],
                    "updated_at": _now(),
                },
            )

    data: dict[str, list[Any]] = {
        "symbol": [],
        "date": [],
        "overall": [],
        "trend": [],
        "bb": [],
        "rsi": [],
        "macd": [],
        "stoch": [],
        "ta": [],
        "volume_signal": [],
        "volume_magnitude": [],
        "pre_boost": [],
        "pre_regime": [],
        "w_adj": [],
        "mcd_dampen": [],
        "mcd_mcap_b": [],
        "cwwd_dampen": [],
        "wvd_dampen": [],
        "wvd_lift": [],
        "wv_force1": [],
        "score_velocity_7d": [],
        "pct_from_ema50": [],
        "score_price": [],
    }
    for r in rows:
        wi = _load_weight_info(r[11])
        data["symbol"].append(r[0])
        data["date"].append(r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]))
        data["overall"].append(int(r[2]))
        data["trend"].append(int(r[3]) if r[3] is not None else None)
        data["bb"].append(int(r[4]) if r[4] is not None else None)
        data["rsi"].append(int(r[5]) if r[5] is not None else None)
        data["macd"].append(int(r[6]) if r[6] is not None else None)
        data["stoch"].append(int(r[7]) if r[7] is not None else None)
        data["ta"].append(int(r[8]) if r[8] is not None else None)
        data["volume_signal"].append(r[9] or "NEUTRAL")
        data["volume_magnitude"].append(float(r[10]) if r[10] is not None else 0.0)
        for key in (
            "pre_boost",
            "pre_regime",
            "w_adj",
            "mcd_dampen",
            "mcd_mcap_b",
            "cwwd_dampen",
            "wvd_dampen",
            "wvd_lift",
            "wv_force1",
        ):
            val = wi.get(key)
            try:
                data[key].append(float(val) if val is not None else 0.0)
            except Exception:
                data[key].append(0.0)
        data["score_velocity_7d"].append(float(r[12]) if r[12] is not None else 0.0)
        data["pct_from_ema50"].append(float(r[13]) if r[13] is not None else None)
        data["score_price"].append(float(r[14]) if r[14] is not None else None)

    df = pl.DataFrame(data)
    df = df.with_columns(
        (pl.col("mcd_dampen") + pl.col("cwwd_dampen") + pl.col("wvd_dampen")).alias("dampener_pressure")
    )
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "participation_wave/domain_scores")
    df.write_parquet(out)
    print(f"[scores] wrote {out} ({df.height:,} rows)", flush=True)
    return df


def _pull_price_context(
    scores: pl.DataFrame,
    min_score: int,
    force: bool,
    start_date: str | None,
    end_date: str | None,
    status_path: Path | None = None,
) -> pl.DataFrame:
    out = _price_cache_path(min_score, start_date, end_date)
    if out.exists() and not force:
        print(f"[cache] price hit: {out}", flush=True)
        return pl.read_parquet(out)

    from database.trader_database import DB

    syms = scores["symbol"].unique().sort().to_list()
    min_date = date.fromisoformat(scores["date"].min()) - timedelta(days=120)
    stop = min(end_date or cutoff_iso(), cutoff_iso())
    rows: list[tuple[Any, ...]] = []
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    print(f"[price] pulling price history for {len(syms):,} symbols", flush=True)
    chunks = list(range(0, len(syms), 80))
    for chunk_idx, i in enumerate(chunks, 1):
        chunk = syms[i : i + 80]
        quoted = "', '".join(chunk)
        t0 = time.perf_counter()
        cur = DB.execute_sql(
            f"""
            SELECT symbol, date, close, volume
            FROM price_history
            WHERE symbol IN ('{quoted}')
              AND date >= '{min_date.isoformat()}'
              AND date <= '{stop}'
            """
        )
        got = cur.fetchall()
        rows.extend(got)
        if i % 400 == 0:
            print(
                f"[price]   symbols {i+1}-{min(i+80, len(syms))}: {len(got):,} rows ({time.perf_counter()-t0:.1f}s)",
                flush=True,
            )
        if status_path is not None:
            _write_json(
                status_path,
                {
                    "phase": "pull_price",
                    "state": "running",
                    "completed": chunk_idx,
                    "total": len(chunks),
                    "rows": len(rows),
                    "current_symbol_range": [i + 1, min(i + 80, len(syms))],
                    "updated_at": _now(),
                },
            )

    ph = (
        pl.DataFrame(
            {
                "symbol": [r[0] for r in rows],
                "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
                "close": [float(r[2]) for r in rows],
                "volume": [float(r[3]) if r[3] is not None else 0.0 for r in rows],
            }
        )
        .sort(["symbol", "date"])
        .with_columns(
            [
                pl.col("close").pct_change().over("symbol").alias("ret_1d"),
                (pl.col("close") / pl.col("close").shift(3).over("symbol") - 1.0).alias("ret_3d"),
                (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0).alias("ret_5d"),
                (pl.col("close") * pl.col("volume")).alias("dollar_volume"),
            ]
        )
        .with_columns(
            [
                pl.col("ret_1d").rolling_std(window_size=20).over("symbol").alias("sigma20"),
                pl.col("dollar_volume").rolling_mean(window_size=20).over("symbol").alias("dv_ma20"),
            ]
        )
        .with_columns(
            [
                (pl.col("ret_5d") / (pl.col("sigma20") * math.sqrt(5)))
                .fill_nan(0.0)
                .fill_null(0.0)
                .alias("price_impulse_sigma"),
                (pl.col("dollar_volume") / pl.col("dv_ma20"))
                .log()
                .fill_nan(0.0)
                .fill_null(0.0)
                .alias("log_dv_expansion"),
            ]
        )
        .select(["symbol", "date", "ret_3d", "ret_5d", "price_impulse_sigma", "log_dv_expansion", "dollar_volume"])
    )
    ph = pre_cutoff_filter(ph)
    assert_no_holdout_leak(ph, "participation_wave/domain_price_context")
    ph.write_parquet(out)
    print(f"[price] wrote {out} ({ph.height:,} rows)", flush=True)
    return ph


def _join_barriers(df: pl.DataFrame) -> pl.DataFrame:
    import duckdb

    duck_path = ROOT / ".cache" / "barrier_outcomes.duckdb"
    if not duck_path.exists():
        raise FileNotFoundError(duck_path)

    pairs = df.select(["symbol", "date"]).unique().to_arrow()
    con = duckdb.connect(str(duck_path), read_only=True)
    con.register("pairs", pairs)
    generic = pl.from_arrow(
        con.execute(
            """
            SELECT p.symbol, CAST(p.date AS VARCHAR) AS date,
                   b.w_days, b.result, b.exit_return
            FROM pairs p
            JOIN barrier_outcomes b
              ON b.symbol = p.symbol
             AND CAST(b.date AS VARCHAR) = CAST(p.date AS VARCHAR)
            WHERE b.barrier_set = '30dte_generic'
              AND b.side = 'low'
              AND b.w_days IN (3, 5, 7, 15, 30)
            """
        ).to_arrow_table()
    )
    generic = pre_cutoff_filter(generic)
    assert_no_holdout_leak(generic, "participation_wave/domain_generic_barriers")
    for w in [3, 5, 7, 15, 30]:
        piece = (
            generic.filter(pl.col("w_days") == w)
            .select(
                [
                    "symbol",
                    "date",
                    pl.col("result").alias(f"win_{w}d"),
                    pl.col("exit_return").alias(f"exit_return_{w}d"),
                ]
            )
            .unique(["symbol", "date"], keep="first")
        )
        df = df.join(piece, on=["symbol", "date"], how="left")

    opt = pl.from_arrow(
        con.execute(
            """
            SELECT p.symbol, CAST(p.date AS VARCHAR) AS date,
                   b.result AS opt_result_15,
                   b.exit_return AS opt_exit_return_15
            FROM pairs p
            JOIN barrier_outcomes b
              ON b.symbol = p.symbol
             AND CAST(b.date AS VARCHAR) = CAST(p.date AS VARCHAR)
            WHERE b.barrier_set = '30dte_opt'
              AND b.side = 'low'
              AND b.w_days = 15
            """
        ).to_arrow_table()
    )
    opt = pre_cutoff_filter(opt)
    assert_no_holdout_leak(opt, "participation_wave/domain_opt_barriers")
    return df.join(opt.unique(["symbol", "date"], keep="first"), on=["symbol", "date"], how="left")


def build_context(
    min_score: int,
    force: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    status_path: Path | None = None,
) -> Path:
    out = _context_cache_path(min_score, start_date, end_date)
    if out.exists() and not force:
        print(f"[cache] context hit: {out}", flush=True)
        return out

    scores = _pull_scores(
        min_score=min_score,
        force=force,
        start_date=start_date,
        end_date=end_date,
        status_path=status_path,
    )
    price = _pull_price_context(
        scores,
        min_score=min_score,
        force=force,
        start_date=start_date,
        end_date=end_date,
        status_path=status_path,
    )
    if status_path is not None:
        _write_json(status_path, {"phase": "join_barriers", "state": "running", "updated_at": _now()})
    df = scores.join(price, on=["symbol", "date"], how="left")
    df = _join_barriers(df)
    df = df.with_columns(
        [
            pl.col("score_velocity_7d").fill_null(0.0),
            pl.col("price_impulse_sigma").fill_null(0.0),
            pl.col("log_dv_expansion").fill_null(0.0),
            pl.col("w_adj").fill_null(0.0),
            pl.col("wv_force1").fill_null(0.0),
            pl.col("volume_magnitude").fill_null(0.0),
            pl.col("dampener_pressure").fill_null(0.0),
        ]
    )
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "participation_wave/domain_context")
    df.write_parquet(out)
    print(f"[context] wrote {out} ({df.height:,} rows)", flush=True)
    return out


def _promotion_summary(varied: pl.DataFrame) -> dict[str, Any]:
    checks = {
        "60_64_to_70": (pl.col("overall").is_between(60, 64), pl.col("new_score") >= 70),
        "65_69_to_70": (pl.col("overall").is_between(65, 69), pl.col("new_score") >= 70),
        "70_74_to_75": (pl.col("overall").is_between(70, 74), pl.col("new_score") >= 75),
        "below_75_to_75": (pl.col("overall") < 75, pl.col("new_score") >= 75),
    }
    out: dict[str, Any] = {}
    for label, (src, dst) in checks.items():
        sub = varied.filter(src & dst)
        resolved = sub.filter(pl.col("win_7d").is_not_null())
        wr7 = None if resolved.is_empty() else 100.0 * resolved.filter(pl.col("win_7d") == 1).height / resolved.height
        out[label] = {
            "rows": sub.height,
            "wr7": wr7,
            "wr7_n": resolved.height,
            "symbols": sub.select("symbol").unique().height if sub.height else 0,
        }
    return out


def run(args: argparse.Namespace) -> int:
    start_slug = f"_from{args.start_date.replace('-', '')}" if args.start_date else ""
    end_slug = f"_to{args.end_date.replace('-', '')}" if args.end_date else ""
    run_dir = (
        Path(args.run_dir)
        if args.run_dir
        else ROOT
        / "experiments"
        / "mcd_cwwd_wvd_recal"
        / "runs"
        / f"domain_stress_min{args.min_score}{start_slug}{end_slug}_{datetime.now():%Y%m%d_%H%M%S}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    _write_json(
        status_path,
        {
            "phase": "setup",
            "state": "running",
            "started_at": _now(),
            "run_dir": str(run_dir),
            "min_score": args.min_score,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "params_mode": args.params_mode,
        },
    )

    context_path = build_context(
        min_score=args.min_score,
        force=args.force_build,
        start_date=args.start_date,
        end_date=args.end_date,
        status_path=status_path,
    )
    df = pl.read_parquet(context_path)
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "participation_wave/domain_stress")

    params = FOLDED_NO_VELOCITY_PARAMS if args.params_mode == "folded-no-velocity" else DEFAULT_PARAMS
    baseline = _eval_rows(_row_dicts(_with_baseline_new(df)))
    varied = apply_wave(df, params)
    candidate = _eval_rows(_row_dicts(varied))
    affected_expr = pl.col("wave_boost") >= args.affected_min_boost
    affected = varied.filter(affected_expr)

    w_windows = _window_affected(varied, affected_expr)
    w1 = w_windows["WR7"]
    w1_pass = w1["z"] is not None and w1["z"] >= 3.0 and (w1["lift_pp"] or 0) > 0
    w2_lifts = [w_windows[f"WR{w}"]["lift_pp"] for w in [3, 5, 7, 15, 30]]
    w2_pass = all(v is not None and v > 0 for v in w2_lifts)
    w3 = _period_gate(df, params)
    w4 = _discrete_bucket_gate(baseline, candidate)
    w5 = _capacity_gate(candidate)
    w6 = _gradient_gate(baseline, candidate)

    summary = {
        "generated_at": _now(),
        "context": str(context_path),
        "min_score": args.min_score,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "params_mode": args.params_mode,
        "affected_min_boost": args.affected_min_boost,
        "params": asdict(params),
        "candidate_label": params.label(),
        "scope_note": "Broad-domain stress over v46 rows down to min_score; still approximation, not production recalc.",
        "counts": {
            "rows": df.height,
            "baseline_70_plus_rows": df.filter(pl.col("overall") >= 70).height,
            "sub70_rows": df.filter(pl.col("overall") < 70).height,
            "affected_wave_boost_ge_threshold": affected.height,
            "affected_pct": 100.0 * affected.height / df.height if df.height else 0.0,
        },
        "promotion_summary": _promotion_summary(varied),
        "baseline_tiers": baseline["tiers"],
        "candidate_tiers": candidate["tiers"],
        "baseline_buckets": baseline["buckets"],
        "candidate_buckets": candidate["buckets"],
        "poet": candidate.get("poet", []),
        "gates": {
            "W1_cohort_z": {"pass": w1_pass, **w1},
            "W2_multi_window": {"pass": w2_pass, "windows": w_windows},
            "W3_time_window": w3,
            "W4_discrete_bucket_non_regression": w4,
            "W5_capacity_floor": w5,
            "W6_gradient_preservation": w6,
        },
    }
    summary["overall_pass"] = all(
        [
            summary["gates"]["W1_cohort_z"]["pass"],
            summary["gates"]["W2_multi_window"]["pass"],
            summary["gates"]["W3_time_window"]["pass"],
            summary["gates"]["W4_discrete_bucket_non_regression"]["pass"],
            summary["gates"]["W5_capacity_floor"]["pass"],
            summary["gates"]["W6_gradient_preservation"]["pass"],
        ]
    )
    summary_path = run_dir / "domain_stress_summary.json"
    _write_json(summary_path, summary)
    _write_json(
        status_path,
        {
            "phase": "done",
            "state": "done",
            "finished_at": _now(),
            "summary": str(summary_path),
            "overall_pass": summary["overall_pass"],
        },
    )
    _write_json(
        run_dir / "done.json",
        {
            "state": "done",
            "finished_at": _now(),
            "summary": str(summary_path),
            "overall_pass": summary["overall_pass"],
        },
    )
    print(
        json.dumps(
            {
                "overall_pass": summary["overall_pass"],
                "summary": str(summary_path),
                "counts": summary["counts"],
                "promotion_summary": summary["promotion_summary"],
                "gates": {k: v["pass"] for k, v in summary["gates"].items()},
            },
            indent=2,
        )
    )
    return 0 if summary["overall_pass"] else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--params-mode", choices=["original", "folded-no-velocity"], default="original")
    parser.add_argument("--affected-min-boost", type=float, default=0.50)
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
