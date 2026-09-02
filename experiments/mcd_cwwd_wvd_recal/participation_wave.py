"""Participation Wave Magnifier experiment.

Tests a smooth, empirically constrained relief term for MCD/CWWD/WVD pressure.

This is intentionally NOT a boundary rule. The wave is a continuous product of:
  - dollar-volume expansion,
  - price impulse in realized-vol units,
  - score velocity,
  - weekly alignment,
  - weekly-volume force shape.

It only acts where existing dampeners already created pressure, so the question
is: "when participation expands coherently, is some dampener pressure actually
small-cap continuation alpha rather than exhaustion/noise?"
"""
from __future__ import annotations

import argparse
import io
import json
import math
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter, cutoff_iso


VERSION_ID = 46
LOOKBACK_DAYS = 1825
LOCAL_CACHE = ROOT / ".cache" / "mcd_cwwd_wvd_recal"
FEATURES = LOCAL_CACHE / f"features_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet"
CONTEXT = LOCAL_CACHE / f"participation_context_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet"
CALL_TIERS = [70, 75, 80, 85, 90, 95]
CALL_BUCKETS = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 94), (95, 100)]
POET_FOCUS_DATES = {"2025-10-03", "2026-05-05", "2026-05-06", "2026-05-07"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _sigmoid_expr(x: pl.Expr, mid: float, slope: float) -> pl.Expr:
    z = ((x - mid) * slope).clip(-40.0, 40.0)
    return 1.0 / (1.0 + (-z).exp())


def _pull_score_context(force: bool) -> pl.DataFrame:
    out = LOCAL_CACHE / f"score_context_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet"
    if out.exists() and not force:
        return pl.read_parquet(out)

    from database.trader_database import DB

    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    stop = cutoff_iso()
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    rows: list[tuple[Any, ...]] = []
    print(f"[ctx] pulling score context v{VERSION_ID} {start}..{stop}", flush=True)
    for yr in range(int(start[:4]), int(stop[:4]) + 1):
        y_start = max(f"{yr}-01-01", start)
        y_end = min(f"{yr}-12-31", stop)
        if y_start > y_end:
            continue
        t0 = time.perf_counter()
        cur = DB.execute_sql(
            f"""
            SELECT symbol, date, score_velocity_7d, pct_from_ema50,
                   price, volume_signal, volume_magnitude
            FROM scores
            WHERE version_id = {VERSION_ID}
              AND date >= '{y_start}' AND date <= '{y_end}'
              AND overall >= 70
            """
        )
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f"[ctx]   {yr}: {len(chunk):,} ({time.perf_counter()-t0:.1f}s)", flush=True)

    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "score_velocity_7d": [float(r[2]) if r[2] is not None else 0.0 for r in rows],
            "pct_from_ema50": [float(r[3]) if r[3] is not None else None for r in rows],
            "score_price": [float(r[4]) if r[4] is not None else None for r in rows],
            "volume_signal": [r[5] or "NEUTRAL" for r in rows],
            "volume_magnitude": [float(r[6]) if r[6] is not None else 0.0 for r in rows],
        }
    )
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "participation_wave/score_context")
    df.write_parquet(out)
    return df


def _pull_price_context(features: pl.DataFrame, force: bool) -> pl.DataFrame:
    out = LOCAL_CACHE / f"price_context_v{VERSION_ID}_{LOOKBACK_DAYS}.parquet"
    if out.exists() and not force:
        return pl.read_parquet(out)

    from database.trader_database import DB

    syms = features["symbol"].unique().sort().to_list()
    min_date = date.fromisoformat(features["date"].min()) - timedelta(days=120)
    stop = cutoff_iso()
    rows: list[tuple[Any, ...]] = []
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    print(f"[ctx] pulling price history for {len(syms):,} symbols", flush=True)
    for i in range(0, len(syms), 80):
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
            print(f"[ctx]   symbols {i+1}-{min(i+80, len(syms))}: {len(got):,} rows ({time.perf_counter()-t0:.1f}s)", flush=True)

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
                (pl.col("close").pct_change().over("symbol")).alias("ret_1d"),
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
                (pl.col("ret_5d") / (pl.col("sigma20") * math.sqrt(5))).fill_nan(0.0).fill_null(0.0).alias("price_impulse_sigma"),
                (pl.col("dollar_volume") / pl.col("dv_ma20")).log().fill_nan(0.0).fill_null(0.0).alias("log_dv_expansion"),
            ]
        )
        .select(["symbol", "date", "ret_3d", "ret_5d", "price_impulse_sigma", "log_dv_expansion", "dollar_volume"])
    )
    ph = pre_cutoff_filter(ph)
    assert_no_holdout_leak(ph, "participation_wave/price_context")
    ph.write_parquet(out)
    return ph


def build_context(force: bool = False) -> Path:
    if CONTEXT.exists() and not force:
        print(f"[cache] context hit: {CONTEXT}", flush=True)
        return CONTEXT
    if not FEATURES.exists():
        raise FileNotFoundError(f"Missing {FEATURES}; run mcd_cwwd_wvd_recal/sweep.py first")

    features = pl.read_parquet(FEATURES)
    features = pre_cutoff_filter(features)
    assert_no_holdout_leak(features, "participation_wave/base_features")
    score_ctx = _pull_score_context(force=force)
    price_ctx = _pull_price_context(features, force=force)
    df = features.join(score_ctx, on=["symbol", "date"], how="left")
    df = df.join(price_ctx, on=["symbol", "date"], how="left")
    df = df.with_columns(
        [
            (pl.col("mcd_dampen") + pl.col("cwwd_dampen") + pl.col("wvd_dampen")).alias("dampener_pressure"),
            pl.col("score_velocity_7d").fill_null(0.0),
            pl.col("price_impulse_sigma").fill_null(0.0),
            pl.col("log_dv_expansion").fill_null(0.0),
            pl.col("w_adj").fill_null(0.0),
            pl.col("wv_force1").fill_null(0.0),
            pl.col("volume_magnitude").fill_null(0.0),
        ]
    )
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "participation_wave/context")
    df.write_parquet(CONTEXT)
    print(f"[ctx] wrote {CONTEXT} ({len(df):,} rows)", flush=True)
    return CONTEXT


@dataclass(frozen=True)
class WaveParams:
    k: float
    cap: float
    dv_mid: float
    dv_slope: float
    impulse_mid: float
    impulse_slope: float
    vel_mid: float
    vel_slope: float
    wadj_mid: float
    wadj_slope: float
    force_peak: float
    force_width: float
    pressure_floor: float

    def label(self) -> str:
        return (
            f"k{self.k:.2f}_cap{self.cap:.1f}_dv{self.dv_mid:.1f}_"
            f"imp{self.impulse_mid:.1f}_vel{self.vel_mid:.1f}_"
            f"wadj{self.wadj_mid:.1f}_fp{self.force_peak:.1f}_fw{self.force_width:.1f}_"
            f"pf{self.pressure_floor:.1f}"
        )


def random_params(n: int, seed: int) -> list[WaveParams]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append(
            WaveParams(
                k=rng.choice([0.25, 0.40, 0.55, 0.70, 0.90, 1.10, 1.35]),
                cap=rng.choice([2.0, 3.0, 4.5, 6.0, 8.0]),
                dv_mid=rng.choice([-0.2, 0.0, 0.35, 0.70, 1.10]),
                dv_slope=rng.choice([1.5, 2.5, 4.0]),
                impulse_mid=rng.choice([0.0, 0.5, 1.0, 1.5, 2.0]),
                impulse_slope=rng.choice([1.0, 1.8, 2.8]),
                vel_mid=rng.choice([-2.0, 0.0, 3.0, 6.0, 10.0]),
                vel_slope=rng.choice([0.20, 0.35, 0.55]),
                wadj_mid=rng.choice([-2.0, 0.0, 4.0, 8.0, 12.0]),
                wadj_slope=rng.choice([0.25, 0.45, 0.70]),
                force_peak=rng.choice([-0.1, 0.0, 0.2, 0.45, 0.70]),
                force_width=rng.choice([0.15, 0.30, 0.55, 0.90]),
                pressure_floor=rng.choice([0.0, 0.5, 1.5, 3.0]),
            )
        )
    # Explicit current/no-op baseline.
    out.append(WaveParams(0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.2, 0.0, 0.25, 0.0, 1.0, 0.0))
    return out


def apply_wave(df: pl.DataFrame, p: WaveParams) -> pl.DataFrame:
    if p.k <= 0.0 or p.cap <= 0.0:
        return df.with_columns(pl.col("overall").alias("new_score"), pl.lit(0.0).alias("wave_boost"))

    dv = _sigmoid_expr(pl.col("log_dv_expansion"), p.dv_mid, p.dv_slope)
    impulse = _sigmoid_expr(pl.col("price_impulse_sigma"), p.impulse_mid, p.impulse_slope)
    vel = _sigmoid_expr(pl.col("score_velocity_7d"), p.vel_mid, p.vel_slope)
    wadj = _sigmoid_expr(pl.col("w_adj"), p.wadj_mid, p.wadj_slope)
    force = (-(((pl.col("wv_force1") - p.force_peak) / max(p.force_width, 0.01)) ** 2)).exp()
    pressure = (pl.col("dampener_pressure") - p.pressure_floor).clip(0.0, None)
    wave = dv * impulse * vel * wadj * force
    boost = (p.k * wave * pressure).clip(0.0, p.cap)
    return df.with_columns(
        [
            boost.alias("wave_boost"),
            (pl.col("overall") + boost).round().clip(0, 100).cast(pl.Int64).alias("new_score"),
        ]
    )


def _row_dicts(df: pl.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for r in df.iter_rows(named=True):
        d = dict(r)
        d["date_ord"] = date.fromisoformat(d["date"]).toordinal()
        for key in ("win_7d", "win_15d", "win_30d", "opt_result_15"):
            if d.get(key) is None or (isinstance(d.get(key), float) and math.isnan(d[key])):
                d[key] = None
        rows.append(d)
    return rows


def _extract_peaks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["new_score"] >= 70:
            by_symbol[r["symbol"]].append(r)

    selected = []
    for sym_rows in by_symbol.values():
        date_set = {r["date_ord"] for r in sym_rows}
        by_date = {r["date_ord"]: r for r in sym_rows}
        pruned: set[int] = set()
        for r in sorted(sym_rows, key=lambda x: x["new_score"] - 50, reverse=True):
            ord_date = r["date_ord"]
            if ord_date in pruned:
                continue
            selected.append(r)
            for direction in (-1, 1):
                walk = ord_date + direction
                while walk in date_set and walk not in pruned:
                    pruned.add(walk)
                    walk += direction
    return selected


def _wr(vals: list[Any]) -> tuple[float | None, int]:
    resolved = [v for v in vals if v is not None]
    if not resolved:
        return None, 0
    return 100.0 * sum(1 for v in resolved if int(v) == 1) / len(resolved), len(resolved)


def _eval_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    peaks = _extract_peaks(rows)
    tiers = {}
    for t in CALL_TIERS:
        sub = [r for r in peaks if r["new_score"] >= t]
        wr7, n7 = _wr([r["win_7d"] for r in sub])
        opt, nopt = _wr([r["opt_result_15"] for r in sub])
        tiers[f"{t}+"] = {"n": len(sub), "wr7": wr7, "wr7_n": n7, "opt15": opt, "opt15_n": nopt}

    buckets = {}
    for lo, hi in CALL_BUCKETS:
        sub = [r for r in peaks if lo <= r["new_score"] <= hi]
        wr7, n7 = _wr([r["win_7d"] for r in sub])
        buckets[f"{lo}+" if hi == 100 else f"{lo}-{hi}"] = {"n": len(sub), "wr7": wr7, "wr7_n": n7}

    poet = [
        {
            "date": r["date"],
            "base": r["overall"],
            "new": r["new_score"],
            "boost": round(r.get("wave_boost") or 0.0, 3),
            "pressure": round(r.get("dampener_pressure") or 0.0, 3),
            "dv": round(r.get("log_dv_expansion") or 0.0, 3),
            "imp": round(r.get("price_impulse_sigma") or 0.0, 3),
            "vel": round(r.get("score_velocity_7d") or 0.0, 3),
            "wadj": round(r.get("w_adj") or 0.0, 3),
            "wv_force1": round(r.get("wv_force1") or 0.0, 3),
            "wr7": r["win_7d"],
            "opt15": r["opt_result_15"],
        }
        for r in peaks
        if r["symbol"] == "POET" and (r["date"] in POET_FOCUS_DATES or r["new_score"] >= 75)
    ]
    return {"tiers": tiers, "buckets": buckets, "poet": poet}


def _delta(new: float | None, old: float | None) -> float:
    if new is None or old is None:
        return 0.0
    return new - old


def _utility(res: dict[str, Any], base: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    d70 = _delta(res["tiers"]["70+"]["wr7"], base["tiers"]["70+"]["wr7"])
    d75 = _delta(res["tiers"]["75+"]["wr7"], base["tiers"]["75+"]["wr7"])
    d80 = _delta(res["tiers"]["80+"]["wr7"], base["tiers"]["80+"]["wr7"])
    dopt75 = _delta(res["tiers"]["75+"]["opt15"], base["tiers"]["75+"]["opt15"])
    n75_base = base["tiers"]["75+"]["wr7_n"] or 1
    n75_new = res["tiers"]["75+"]["wr7_n"] or 0
    n75_delta = 100.0 * (n75_new - n75_base) / n75_base
    poet_focus_75 = sum(1 for p in res["poet"] if p["date"] in POET_FOCUS_DATES and p["new"] >= 75)

    breaches = []
    for label, b in res["buckets"].items():
        if (b.get("wr7_n") or 0) < 30 or (base["buckets"][label].get("wr7_n") or 0) < 30:
            continue
        d = _delta(b.get("wr7"), base["buckets"][label].get("wr7"))
        if d < -0.50:
            breaches.append({"bucket": label, "d_wr7": d, "n": b.get("wr7_n")})

    penalty = 0.0
    if d70 < -0.25:
        penalty += (-d70 - 0.25) * 8.0
    if d75 < -0.25:
        penalty += (-d75 - 0.25) * 8.0
    if d80 < -0.50:
        penalty += (-d80 - 0.50) * 5.0
    if dopt75 < -0.50:
        penalty += (-dopt75 - 0.50) * 3.0
    if n75_delta > 12.0:
        penalty += (n75_delta - 12.0) * 0.20
    penalty += len(breaches) * 3.0

    utility = 3.0 * d75 + 1.0 * d70 + 0.8 * dopt75 + 0.20 * poet_focus_75 - penalty
    diag = {
        "d_wr7_70": d70,
        "d_wr7_75": d75,
        "d_wr7_80": d80,
        "d_opt15_75": dopt75,
        "n75_delta_pct": n75_delta,
        "poet_focus_75": poet_focus_75,
        "bucket_breaches": breaches,
        "penalty": penalty,
        "utility": utility,
    }
    return utility, diag


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / f"participation_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    results_path = run_dir / "participation_results.jsonl"
    summary_path = run_dir / "participation_summary.json"
    _write_json(status_path, {"phase": "setup", "state": "running", "started_at": _now(), "run_dir": str(run_dir)})

    context_path = build_context(force=args.force_build)
    df = pl.read_parquet(context_path)
    baseline_rows = _row_dicts(df.with_columns(pl.col("overall").alias("new_score"), pl.lit(0.0).alias("wave_boost")))
    baseline = _eval_rows(baseline_rows)
    params = random_params(args.samples, args.seed)
    print(f"[sweep] rows={len(df):,} variants={len(params):,}", flush=True)

    ranked = []
    with results_path.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(params, 1):
            varied = apply_wave(df, p)
            rows = _row_dicts(varied)
            res = _eval_rows(rows)
            utility, diag = _utility(res, baseline)
            rec = {
                "label": p.label(),
                "params": asdict(p),
                "utility": utility,
                "diagnostics": diag,
                "tiers": res["tiers"],
                "buckets": res["buckets"],
                "poet": res["poet"],
            }
            fh.write(json.dumps(rec, default=str) + "\n")
            ranked.append(rec)
            if i % 25 == 0 or i == len(params):
                best = max(ranked, key=lambda r: r["utility"])
                _write_json(
                    status_path,
                    {
                        "phase": "sweep",
                        "state": "running",
                        "completed": i,
                        "total": len(params),
                        "best_so_far": {
                            "label": best["label"],
                            "utility": best["utility"],
                            "diagnostics": best["diagnostics"],
                        },
                        "updated_at": _now(),
                        "results": str(results_path),
                    },
                )
                print(f"[sweep] {i}/{len(params)} best={best['label']} util={best['utility']:.3f}", flush=True)

    ranked.sort(key=lambda r: r["utility"], reverse=True)
    summary = {"baseline": baseline, "top": ranked[:30], "context": str(context_path), "generated_at": _now()}
    _write_json(summary_path, summary)
    _write_json(run_dir / "done.json", {"state": "done", "finished_at": _now(), "summary": str(summary_path), "results": str(results_path)})
    _write_json(status_path, {"phase": "done", "state": "done", "completed": len(params), "total": len(params), "summary": str(summary_path), "finished_at": _now()})
    print(f"[done] {summary_path}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=800)
    parser.add_argument("--seed", type=int, default=20260511)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--force-build", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
