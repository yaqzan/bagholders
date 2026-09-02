"""MCD/CWWD/WVD recalibration sweep.

Goal: test whether the current call dampener stack is too harsh on POET-like
small-cap continuation winners without optimizing to POET alone.

This is a cheap first pass: it uses the dampener magnitudes already persisted in
Score.weight_info and re-scales them, instead of modifying production scoring or
running a full recalc for every candidate. Winners from this script still need a
full faithful recalc + assess before ship.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import product
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


LOCAL_CACHE = ROOT / ".cache" / "mcd_cwwd_wvd_recal"
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)
RUNNER_CACHE = ROOT / ".cache" / "runner"

LOOKBACK_DAYS = 1825
CALL_TIERS = [70, 75, 80, 85, 90, 95]
CALL_BUCKETS = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 94), (95, 100)]
POET_FOCUS_DATES = {"2026-05-05", "2026-05-06", "2026-05-07", "2025-10-03"}


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


def _fetch_active_version() -> tuple[int, str]:
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    return int(av.id), av.git_commit


def _pull_scores(version_id: int, cutoff: str, force: bool) -> pl.DataFrame:
    cache_path = LOCAL_CACHE / f"candidate_scores_v{version_id}_{LOOKBACK_DAYS}.parquet"
    if cache_path.exists() and not force:
        print(f"[cache] scores hit: {cache_path}", flush=True)
        return pl.read_parquet(cache_path)

    from database.trader_database import DB

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=600000")
    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    stop = cutoff_iso()
    start_year = int(start[:4])
    end_year = int(stop[:4]) + 1
    rows: list[tuple[Any, ...]] = []

    print(f"[scores] pulling v{version_id} current call peaks {start}..{stop}", flush=True)
    for yr in range(start_year, end_year):
        y_start = max(f"{yr}-01-01", start)
        y_end = min(f"{yr}-12-31", stop)
        if y_start > y_end:
            continue
        t0 = time.perf_counter()
        cur = DB.execute_sql(
            f"""
            SELECT symbol, date, overall, trend, bb, rsi, macd, stoch,
                   technical_alignment, volume_signal, volume_magnitude,
                   weight_info
            FROM scores
            WHERE version_id = {version_id}
              AND date >= '{y_start}' AND date <= '{y_end}'
              AND overall IS NOT NULL
              AND overall >= 70
            """
        )
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f"[scores]   {yr}: {len(chunk):,} rows ({time.perf_counter()-t0:.1f}s)", flush=True)

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
        "vol_sig": [],
        "vol_mag": [],
        "pre_boost": [],
        "pre_regime": [],
        "w_adj": [],
        "mcd_dampen": [],
        "mcd_mcap_b": [],
        "cwwd_dampen": [],
        "wvd_dampen": [],
        "wvd_lift": [],
        "wv_force1": [],
    }
    for r in rows:
        wi = _load_weight_info(r[11])
        d = r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1])
        overall = int(r[2])
        data["symbol"].append(r[0])
        data["date"].append(d)
        data["overall"].append(overall)
        data["trend"].append(int(r[3]) if r[3] is not None else None)
        data["bb"].append(int(r[4]) if r[4] is not None else None)
        data["rsi"].append(int(r[5]) if r[5] is not None else None)
        data["macd"].append(int(r[6]) if r[6] is not None else None)
        data["stoch"].append(int(r[7]) if r[7] is not None else None)
        data["ta"].append(int(r[8]) if r[8] is not None else None)
        data["vol_sig"].append(r[9] or "NEUTRAL")
        data["vol_mag"].append(float(r[10]) if r[10] is not None else 0.0)
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

    df = pl.DataFrame(data)
    df = df.with_columns(
        (
            pl.col("overall")
            + pl.col("mcd_dampen")
            + pl.col("cwwd_dampen")
            + pl.col("wvd_dampen")
        ).alias("max_recoverable")
    )
    df = df.filter(pl.col("overall") >= 70)
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "mcd_cwwd_wvd_recal/scores")
    df.write_parquet(cache_path)
    print(f"[scores] wrote {cache_path} ({len(df):,} rows)", flush=True)
    return df


def _load_baseline_peaks(version_id: int) -> pl.DataFrame:
    """Load current v46 Stage-1 call peaks so baseline assessment is complete.

    The DB pull above intentionally only fetches rows that can change under this
    experiment. Baseline rows with no MCD/CWWD/WVD activity still need to be
    present so tier WR/N comparisons use the real current score distribution.
    """
    path = ROOT / ".cache" / "ctsl" / f"scores_v{version_id}_stage1_1825.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    df = (
        pl.read_parquet(path)
        .filter(pl.col("side_label") == "call")
        .select(
            [
                "symbol",
                "date",
                "overall",
                "trend",
                "bb",
                "rsi",
                "macd",
                "stoch",
                pl.lit(None).cast(pl.Int64).alias("ta"),
                pl.lit("NEUTRAL").alias("vol_sig"),
                pl.lit(0.0).alias("vol_mag"),
                pl.lit(0.0).alias("pre_boost"),
                pl.lit(0.0).alias("pre_regime"),
                pl.lit(0.0).alias("w_adj"),
                pl.lit(0.0).alias("mcd_dampen"),
                pl.lit(0.0).alias("mcd_mcap_b"),
                pl.lit(0.0).alias("cwwd_dampen"),
                pl.lit(0.0).alias("wvd_dampen"),
                pl.lit(0.0).alias("wvd_lift"),
                pl.lit(0.0).alias("wv_force1"),
            ]
        )
    )
    return pre_cutoff_filter(df)


def _load_barriers() -> tuple[pl.DataFrame, pl.DataFrame]:
    generic_path = RUNNER_CACHE / "barriers_1825.parquet"
    opt_path = RUNNER_CACHE / "barriers_30opt_w15_1825.parquet"
    if not generic_path.exists():
        raise FileNotFoundError(generic_path)
    if not opt_path.exists():
        raise FileNotFoundError(opt_path)

    generic = (
        pl.read_parquet(generic_path)
        .filter((pl.col("side") == "low") & pl.col("w_days").is_in([7, 15, 30]))
        .select(["symbol", "date", "w_days", "result", "exit_return"])
    )
    pieces = []
    for w in [7, 15, 30]:
        pieces.append(
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
    wide = pieces[0]
    for piece in pieces[1:]:
        wide = wide.join(piece, on=["symbol", "date"], how="outer_coalesce")

    opt = (
        pl.read_parquet(opt_path)
        .filter((pl.col("side") == "low") & (pl.col("w_days") == 15))
        .select(
            [
                "symbol",
                "date",
                pl.col("result").alias("opt_result_15"),
                pl.col("exit_return").alias("opt_exit_return_15"),
            ]
        )
        .unique(["symbol", "date"], keep="first")
    )
    return wide, opt


def build_features(force: bool = False, version_id_override: int | None = None) -> Path:
    active_id, commit = _fetch_active_version()
    version_id = int(version_id_override or active_id)
    out = LOCAL_CACHE / f"features_v{version_id}_{LOOKBACK_DAYS}.parquet"
    if out.exists() and not force:
        print(f"[cache] features hit: {out}", flush=True)
        return out

    affected = _pull_scores(version_id, commit, force=force)
    baseline = _load_baseline_peaks(version_id)
    scores = (
        pl.concat([baseline, affected], how="diagonal_relaxed")
        .unique(["symbol", "date"], keep="last")
    )
    generic, opt = _load_barriers()
    df = scores.join(generic, on=["symbol", "date"], how="left")
    df = df.join(opt, on=["symbol", "date"], how="left")
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "mcd_cwwd_wvd_recal/features")
    df.write_parquet(out)
    print(f"[features] wrote {out} ({len(df):,} rows)", flush=True)
    return out


@dataclass(frozen=True)
class Variant:
    mcd_scale: float
    cwwd_scale: float
    wvd_dampen_scale: float
    wvd_lift_scale: float
    mcd_wadj_relief_k: float
    mcd_wadj_relief_lo: float

    def label(self) -> str:
        return (
            f"mcd{self.mcd_scale:.2f}_cwwd{self.cwwd_scale:.2f}_"
            f"wvdd{self.wvd_dampen_scale:.2f}_wvdl{self.wvd_lift_scale:.2f}_"
            f"mrk{self.mcd_wadj_relief_k:.2f}_mrl{self.mcd_wadj_relief_lo:.0f}"
        )


def _variant_grid(quick: bool) -> list[Variant]:
    if quick:
        return [
            Variant(1.0, 1.0, 1.0, 1.0, 0.0, 4.0),
            Variant(0.75, 1.0, 1.0, 1.0, 0.0, 4.0),
            Variant(0.50, 1.0, 1.0, 1.0, 0.0, 4.0),
            Variant(1.0, 1.0, 0.75, 1.0, 0.0, 4.0),
            Variant(1.0, 1.0, 0.50, 1.0, 0.0, 4.0),
            Variant(0.75, 1.0, 0.75, 1.0, 0.0, 4.0),
        ]

    grid = []
    for mcd_scale, cwwd_scale, wvd_dampen_scale, wvd_lift_scale, relief_k, relief_lo in product(
        [0.35, 0.50, 0.65, 0.80, 1.00],
        [0.60, 0.80, 1.00],
        [0.35, 0.50, 0.65, 0.80, 1.00],
        [0.90, 1.00, 1.10],
        [0.0, 0.35, 0.70],
        [4.0, 8.0],
    ):
        # If global MCD is already relaxed, skip most redundant relief variants.
        if mcd_scale < 0.80 and relief_k > 0.0:
            continue
        grid.append(
            Variant(
                float(mcd_scale),
                float(cwwd_scale),
                float(wvd_dampen_scale),
                float(wvd_lift_scale),
                float(relief_k),
                float(relief_lo),
            )
        )
    return grid


def _score_rows(rows: list[dict[str, Any]], variant: Variant) -> list[tuple[str, int, int, int]]:
    out: list[tuple[str, int, int, int]] = []
    for idx, r in enumerate(rows):
        relief = 0.0
        if variant.mcd_wadj_relief_k > 0.0 and r["w_adj"] > variant.mcd_wadj_relief_lo:
            relief = variant.mcd_wadj_relief_k * min(
                1.0, (r["w_adj"] - variant.mcd_wadj_relief_lo) / 8.0
            )
        mcd_eff = variant.mcd_scale * (1.0 - relief)
        raw = (
            r["overall"]
            + (1.0 - mcd_eff) * r["mcd_dampen"]
            + (1.0 - variant.cwwd_scale) * r["cwwd_dampen"]
            + (1.0 - variant.wvd_dampen_scale) * r["wvd_dampen"]
            + (variant.wvd_lift_scale - 1.0) * r["wvd_lift"]
        )
        score = int(max(0, min(100, round(raw))))
        if score >= 70:
            out.append((r["symbol"], r["date_ord"], score, idx))
    return out


def _extract_variant_peaks(rows: list[dict[str, Any]], variant: Variant) -> list[tuple[int, int]]:
    by_symbol: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for sym, ord_date, score, idx in _score_rows(rows, variant):
        by_symbol[sym].append((ord_date, score, idx))

    selected: list[tuple[int, int]] = []
    for sym_rows in by_symbol.values():
        date_map = {d: (score, idx) for d, score, idx in sym_rows}
        ranked = sorted(sym_rows, key=lambda x: x[1] - 50, reverse=True)
        pruned: set[int] = set()
        for ord_date, score, idx in ranked:
            if ord_date in pruned:
                continue
            selected.append((idx, score))
            for direction in (-1, 1):
                walk = ord_date + direction
                while walk in date_map and walk not in pruned:
                    pruned.add(walk)
                    walk += direction
    return selected


def _wr(vals: list[Any]) -> tuple[float | None, int]:
    resolved = [v for v in vals if v is not None]
    if not resolved:
        return None, 0
    return 100.0 * sum(1 for v in resolved if int(v) == 1) / len(resolved), len(resolved)


def _bucket_label(lo: int, hi: int) -> str:
    return f"{lo}+" if hi >= 100 else f"{lo}-{hi}"


def _evaluate_selected(rows: list[dict[str, Any]], selected: list[tuple[int, int]]) -> dict[str, Any]:
    recs = []
    for idx, score in selected:
        r = rows[idx]
        rec = dict(r)
        rec["new_score"] = score
        recs.append(rec)

    tiers: dict[str, Any] = {}
    for t in CALL_TIERS:
        sub = [r for r in recs if r["new_score"] >= t]
        wr7, n7 = _wr([r["win_7d"] for r in sub])
        wr15, n15 = _wr([r["win_15d"] for r in sub])
        wr30, n30 = _wr([r["win_30d"] for r in sub])
        opt, nopt = _wr([r["opt_result_15"] for r in sub])
        tiers[f"{t}+"] = {
            "n": len(sub),
            "wr7": wr7,
            "wr7_n": n7,
            "wr15": wr15,
            "wr15_n": n15,
            "wr30": wr30,
            "wr30_n": n30,
            "opt15": opt,
            "opt15_n": nopt,
        }

    buckets: dict[str, Any] = {}
    for lo, hi in CALL_BUCKETS:
        sub = [r for r in recs if lo <= r["new_score"] <= hi]
        wr7, n7 = _wr([r["win_7d"] for r in sub])
        buckets[_bucket_label(lo, hi)] = {"n": len(sub), "wr7": wr7, "wr7_n": n7}

    poet = []
    for r in recs:
        if r["symbol"] == "POET" and (r["date"] in POET_FOCUS_DATES or r["new_score"] >= 75):
            poet.append(
                {
                    "date": r["date"],
                    "base": r["overall"],
                    "new": r["new_score"],
                    "mcd": r["mcd_dampen"],
                    "cwwd": r["cwwd_dampen"],
                    "wvd_dampen": r["wvd_dampen"],
                    "wvd_lift": r["wvd_lift"],
                    "wr7": r["win_7d"],
                    "opt15": r["opt_result_15"],
                }
            )

    return {"tiers": tiers, "buckets": buckets, "poet": poet}


def _delta(new: float | None, base: float | None) -> float | None:
    if new is None or base is None:
        return None
    return new - base


def _score_variant(result: dict[str, Any], baseline: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    tiers = result["tiers"]
    bt = baseline["tiers"]
    buckets = result["buckets"]
    bb = baseline["buckets"]

    d75 = _delta(tiers["75+"]["wr7"], bt["75+"]["wr7"]) or 0.0
    d70 = _delta(tiers["70+"]["wr7"], bt["70+"]["wr7"]) or 0.0
    d80 = _delta(tiers["80+"]["wr7"], bt["80+"]["wr7"]) or 0.0
    opt75 = _delta(tiers["75+"]["opt15"], bt["75+"]["opt15"]) or 0.0
    n75_base = bt["75+"]["wr7_n"] or 1
    n75_new = tiers["75+"]["wr7_n"] or 0
    n75_delta_pct = 100.0 * (n75_new - n75_base) / n75_base

    bucket_breaches = []
    for label, b in buckets.items():
        if (b.get("wr7_n") or 0) < 30 or (bb[label].get("wr7_n") or 0) < 30:
            continue
        d = _delta(b.get("wr7"), bb[label].get("wr7"))
        if d is not None and d < -0.5:
            bucket_breaches.append({"bucket": label, "d_wr7": d, "n": b.get("wr7_n")})

    poet_75 = sum(1 for p in result["poet"] if p["new"] >= 75)
    poet_focus_75 = sum(1 for p in result["poet"] if p["date"] in POET_FOCUS_DATES and p["new"] >= 75)

    penalty = 0.0
    if d70 < -0.25:
        penalty += (-d70 - 0.25) * 6.0
    if d80 < -0.50:
        penalty += (-d80 - 0.50) * 5.0
    if opt75 < -0.50:
        penalty += (-opt75 - 0.50) * 2.0
    if n75_delta_pct > 25.0:
        penalty += (n75_delta_pct - 25.0) * 0.08
    penalty += len(bucket_breaches) * 3.0

    utility = 2.0 * d75 + 0.75 * d70 + 0.35 * opt75 + 0.35 * poet_focus_75 - penalty
    diagnostics = {
        "d_wr7_70": d70,
        "d_wr7_75": d75,
        "d_wr7_80": d80,
        "d_opt15_75": opt75,
        "n75_delta_pct": n75_delta_pct,
        "bucket_breaches": bucket_breaches,
        "poet_75": poet_75,
        "poet_focus_75": poet_focus_75,
        "penalty": penalty,
        "utility": utility,
    }
    return utility, diagnostics


def _row_dicts(df: pl.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in df.iter_rows(named=True):
        d = dict(r)
        d["date_ord"] = date.fromisoformat(d["date"]).toordinal()
        for key in (
            "mcd_dampen",
            "cwwd_dampen",
            "wvd_dampen",
            "wvd_lift",
            "w_adj",
            "overall",
        ):
            d[key] = float(d.get(key) or 0.0)
        for key in ("win_7d", "win_15d", "win_30d", "opt_result_15"):
            if d.get(key) is None or (isinstance(d.get(key), float) and math.isnan(d[key])):
                d[key] = None
        rows.append(d)
    return rows


def run_sweep(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    result_path = run_dir / "results.jsonl"
    summary_path = run_dir / "summary.json"

    _write_json(
        status_path,
        {
            "phase": "setup",
            "state": "running",
            "started_at": _now(),
            "updated_at": _now(),
            "run_dir": str(run_dir),
        },
    )
    try:
        features_path = build_features(force=args.force_build, version_id_override=args.version_id)
        df = pl.read_parquet(features_path)
        rows = _row_dicts(df)
        variants = _variant_grid(quick=args.quick)
        baseline_variant = Variant(1.0, 1.0, 1.0, 1.0, 0.0, 4.0)

        print(f"[sweep] rows={len(rows):,} variants={len(variants):,}", flush=True)
        baseline = _evaluate_selected(rows, _extract_variant_peaks(rows, baseline_variant))
        _write_json(
            status_path,
            {
                "phase": "sweep",
                "state": "running",
                "completed": 0,
                "total": len(variants),
                "started_at": _now(),
                "updated_at": _now(),
                "features": str(features_path),
                "result_path": str(result_path),
            },
        )

        ranked = []
        with result_path.open("w", encoding="utf-8") as fh:
            for i, v in enumerate(variants, start=1):
                selected = _extract_variant_peaks(rows, v)
                res = _evaluate_selected(rows, selected)
                utility, diag = _score_variant(res, baseline)
                rec = {
                    "variant": v.__dict__,
                    "label": v.label(),
                    "utility": utility,
                    "diagnostics": diag,
                    "tiers": res["tiers"],
                    "buckets": res["buckets"],
                    "poet": res["poet"],
                }
                fh.write(json.dumps(rec, default=str) + "\n")
                ranked.append(rec)
                if i % 25 == 0 or i == len(variants):
                    best = max(ranked, key=lambda x: x["utility"])
                    _write_json(
                        status_path,
                        {
                            "phase": "sweep",
                            "state": "running",
                            "completed": i,
                            "total": len(variants),
                            "best_so_far": {
                                "label": best["label"],
                                "utility": best["utility"],
                                "diagnostics": best["diagnostics"],
                            },
                            "updated_at": _now(),
                            "features": str(features_path),
                            "result_path": str(result_path),
                        },
                    )
                    print(f"[sweep] {i}/{len(variants)} best={best['label']} util={best['utility']:.3f}", flush=True)

        ranked.sort(key=lambda x: x["utility"], reverse=True)
        summary = {
            "run_dir": str(run_dir),
            "features": str(features_path),
            "baseline": baseline,
            "top": ranked[:25],
            "generated_at": _now(),
        }
        _write_json(summary_path, summary)
        _write_json(
            run_dir / "done.json",
            {
                "state": "done",
                "finished_at": _now(),
                "summary": str(summary_path),
                "results": str(result_path),
                "features": str(features_path),
            },
        )
        _write_json(
            status_path,
            {
                "phase": "done",
                "state": "done",
                "completed": len(variants),
                "total": len(variants),
                "finished_at": _now(),
                "summary": str(summary_path),
                "results": str(result_path),
            },
        )
        print(f"[done] {summary_path}", flush=True)
        return 0
    except Exception as exc:
        _write_json(
            run_dir / "failed.json",
            {
                "state": "failed",
                "finished_at": _now(),
                "error": repr(exc),
            },
        )
        _write_json(
            status_path,
            {
                "phase": "failed",
                "state": "failed",
                "finished_at": _now(),
                "error": repr(exc),
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--version-id", type=int, default=None)
    args = parser.parse_args()
    return run_sweep(args)


if __name__ == "__main__":
    raise SystemExit(main())
