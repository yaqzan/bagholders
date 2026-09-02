from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb
import polars as pl

from experiments._holdout import CUTOFF, assert_no_holdout_leak, cutoff_iso

CACHE_DIR = ROOT / ".cache" / "temporal_echo"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BARRIER_DUCK = ROOT / ".cache" / "barrier_outcomes.duckdb"
ALGO_FILE = ROOT / "ALGORITHM_VERSION"

PERIODS = (1, 3, 5, 7, 15, 30, 60, 90)


def _side_from_raw(raw_score: int) -> str:
    return "low" if raw_score >= 50 else "high"


def _bucket_from_raw(raw_score: int) -> str:
    if raw_score >= 95:
        return "95+"
    if raw_score >= 90:
        return "90-94"
    if raw_score >= 85:
        return "85-89"
    if raw_score >= 80:
        return "80-84"
    if raw_score >= 75:
        return "75-79"
    if raw_score >= 70:
        return "70-74"
    if raw_score >= 65:
        return "65-69"
    if raw_score >= 60:
        return "60-64"
    if raw_score >= 55:
        return "55-59"
    if raw_score >= 50:
        return "50-54"
    if raw_score <= 5:
        return "0-5"
    if raw_score <= 10:
        return "6-10"
    if raw_score <= 15:
        return "11-15"
    if raw_score <= 20:
        return "16-20"
    if raw_score <= 25:
        return "21-25"
    if raw_score <= 30:
        return "26-30"
    if raw_score <= 35:
        return "31-35"
    if raw_score <= 40:
        return "36-40"
    if raw_score <= 45:
        return "41-45"
    return "46-49"


def _parse_pre_boost(overall: int, weight_info: str | None) -> tuple[int, int]:
    if not weight_info:
        return overall, 0
    try:
        wi = json.loads(weight_info)
        raw = wi.get("pre_boost", overall)
        cont = wi.get("cont_lift", 0)
        return int(round(float(raw))), int(round(float(cont or 0)))
    except Exception:
        return overall, 0


def active_version_id() -> tuple[int, str]:
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    return int(av.id), str(av.git_commit)


def load_scores(version_id: int, start_date: str, end_date: str) -> pl.DataFrame:
    from database.trader_database import DB

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    rows: list[tuple] = []
    print(f"[scores] version={version_id} range={start_date}..{end_date}", flush=True)
    cursor = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        chunk_end = min(end, next_month - timedelta(days=1))
        c_start = cursor.isoformat()
        c_end = chunk_end.isoformat()
        t0 = time.perf_counter()
        cur = DB.execute_sql(
            """
            SELECT symbol, date, overall, weight_info
            FROM scores
            WHERE version_id = %s
              AND date BETWEEN %s AND %s
              AND overall IS NOT NULL
            """,
            (version_id, c_start, c_end),
        )
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f"[scores]   {c_start}..{c_end}: {len(chunk):,} rows ({time.perf_counter() - t0:.1f}s)", flush=True)
        cursor = next_month

    out_rows = []
    for sym, d, overall, wi in rows:
        overall_i = int(overall)
        raw, cont_lift = _parse_pre_boost(overall_i, wi)
        out_rows.append(
            {
                "symbol": sym,
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "overall": overall_i,
                "raw_score": raw,
                "cont_lift": cont_lift,
                "side": _side_from_raw(raw),
                "bucket": _bucket_from_raw(raw),
                "distance": abs(raw - 50),
            }
        )
    df = pl.DataFrame(out_rows)
    df = df.with_columns(pl.col("date").str.to_date())
    df = df.filter(pl.col("date") <= CUTOFF)
    assert_no_holdout_leak(df, "temporal_echo/build_features scores")
    return df


def load_barriers(start_date: str, end_date: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    if not BARRIER_DUCK.exists():
        raise SystemExit(f"Missing DuckDB barrier mirror: {BARRIER_DUCK}")
    con = duckdb.connect(str(BARRIER_DUCK), read_only=True)
    period_csv = ",".join(str(p) for p in PERIODS)
    print("[barriers] loading 30dte_generic outcomes", flush=True)
    generic = con.execute(
        f"""
        SELECT symbol, CAST(date AS DATE) AS date, side, w_days, result, result_u,
               exit_return, mae_pct, mfe_pct, entry_close, sigma_pct
        FROM barrier_outcomes
        WHERE barrier_set = '30dte_generic'
          AND w_days IN ({period_csv})
          AND date BETWEEN ? AND ?
        """,
        [start_date, end_date],
    ).pl()
    print(f"[barriers] generic rows: {len(generic):,}", flush=True)

    print("[barriers] loading 30dte_opt W15 outcomes", flush=True)
    opt = con.execute(
        """
        SELECT symbol, CAST(date AS DATE) AS date, side,
               result AS opt_result_15,
               exit_return AS opt_exit_return_15,
               mae_pct AS opt_mae_15,
               mfe_pct AS opt_mfe_15
        FROM barrier_outcomes
        WHERE barrier_set = '30dte_opt'
          AND w_days = 15
          AND date BETWEEN ? AND ?
        """,
        [start_date, end_date],
    ).pl()
    con.close()
    print(f"[barriers] opt rows: {len(opt):,}", flush=True)
    return generic, opt


def join_current_outcomes(scores: pl.DataFrame, generic: pl.DataFrame, opt: pl.DataFrame) -> pl.DataFrame:
    pieces = []
    for w in PERIODS:
        sub = (
            generic.filter(pl.col("w_days") == w)
            .select(
                [
                    "symbol",
                    "date",
                    "side",
                    pl.col("result").alias(f"win_{w}d"),
                    pl.col("result_u").alias(f"win_u_{w}d"),
                    pl.col("exit_return").alias(f"ret_{w}d"),
                    pl.col("mae_pct").alias(f"mae_{w}d"),
                    pl.col("mfe_pct").alias(f"mfe_{w}d"),
                    pl.col("entry_close").alias(f"entry_close_{w}d"),
                    pl.col("sigma_pct").alias(f"sigma_{w}d"),
                ]
            )
        )
        pieces.append(sub)

    out = scores
    for sub in pieces:
        out = out.join(sub, on=["symbol", "date", "side"], how="left")
    out = out.join(opt, on=["symbol", "date", "side"], how="left")
    return out


def build_prior_pairs(current_signals: pl.DataFrame, prior_context: pl.DataFrame, min_gap: int, max_gap: int) -> pl.DataFrame:
    print(f"[priors] building same-side prior pairs gap={min_gap}..{max_gap}", flush=True)
    t0 = time.perf_counter()
    prior_cols = prior_context.select(
        [
            "symbol",
            "date",
            "side",
            "raw_score",
            "overall",
            "distance",
            *[f"win_{w}d" for w in PERIODS],
        ]
    )
    by_sym: dict[str, list[dict]] = {}
    for row in prior_cols.iter_rows(named=True):
        by_sym.setdefault(row["symbol"], []).append(row)
    for rows in by_sym.values():
        rows.sort(key=lambda r: r["date"])
    dates_by_sym = {sym: [r["date"] for r in rows] for sym, rows in by_sym.items()}

    pair_rows = []
    for current in current_signals.select(["symbol", "date", "side", "raw_score", "overall"]).iter_rows(named=True):
        sym = current["symbol"]
        rows = by_sym.get(sym)
        if not rows:
            continue
        dates = dates_by_sym[sym]
        d = current["date"]
        lo = d - timedelta(days=max_gap)
        hi = d - timedelta(days=min_gap)
        left = bisect.bisect_left(dates, lo)
        right = bisect.bisect_right(dates, hi)
        for prior in rows[left:right]:
            if prior["side"] != current["side"]:
                continue
            if prior["side"] == "low":
                if prior["raw_score"] < 70:
                    continue
            else:
                if prior["raw_score"] > 30:
                    continue
            gap = (d - prior["date"]).days
            rec = {
                "symbol": sym,
                "date": d,
                "side": current["side"],
                "current_raw": int(current["raw_score"]),
                "current_overall": int(current["overall"]),
                "prior_date": prior["date"],
                "gap_days": int(gap),
                "prior_raw": int(prior["raw_score"]),
                "prior_overall": int(prior["overall"]),
                "prior_distance": int(prior["distance"]),
            }
            for w in PERIODS:
                val = prior.get(f"win_{w}d")
                rec[f"prior_win_{w}d"] = int(val) if val is not None and gap >= w else None
            pair_rows.append(rec)

    schema = {
        "symbol": pl.Utf8,
        "date": pl.Date,
        "side": pl.Utf8,
        "current_raw": pl.Int64,
        "current_overall": pl.Int64,
        "prior_date": pl.Date,
        "gap_days": pl.Int64,
        "prior_raw": pl.Int64,
        "prior_overall": pl.Int64,
        "prior_distance": pl.Int64,
        **{f"prior_win_{w}d": pl.Int64 for w in PERIODS},
    }
    if not pair_rows:
        return pl.DataFrame(schema=schema)
    df = pl.DataFrame(pair_rows, schema=schema)
    print(f"[priors] rows={len(df):,} ({time.perf_counter() - t0:.1f}s)", flush=True)
    assert_no_holdout_leak(df.select(["date"]), "temporal_echo/build_features priors")
    return df


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=1825)
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--min-gap", type=int, default=1)
    parser.add_argument("--max-gap", type=int, default=90)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    version_id, commit = active_version_id()
    if args.version_id is not None:
        version_id = args.version_id
        commit = "manual"

    end_date = cutoff_iso()
    start_date = (date.fromisoformat(end_date) - timedelta(days=args.lookback_days + args.max_gap + 95)).isoformat()
    suffix = f"v{version_id}_{args.lookback_days}_g{args.min_gap}_{args.max_gap}"
    signals_path = CACHE_DIR / f"signals_{suffix}.parquet"
    priors_path = CACHE_DIR / f"priors_{suffix}.parquet"

    deps = [ALGO_FILE, BARRIER_DUCK]
    fresh = (
        not args.force
        and signals_path.exists()
        and priors_path.exists()
        and all(not d.exists() or signals_path.stat().st_mtime >= d.stat().st_mtime for d in deps)
        and all(not d.exists() or priors_path.stat().st_mtime >= d.stat().st_mtime for d in deps)
    )
    if fresh:
        print(f"[build] cache hit: {signals_path}", flush=True)
        return {
            "version_id": version_id,
            "commit": commit,
            "signals_path": str(signals_path),
            "priors_path": str(priors_path),
            "cached": True,
        }

    scores = load_scores(version_id, start_date, end_date)
    # Keep tradable-direction rows plus near-threshold context. Neutral raw=50 is call-side by assessment convention.
    scores = scores.filter((pl.col("raw_score") >= 50) | (pl.col("raw_score") < 50))
    generic, opt = load_barriers(start_date, end_date)
    signal_context = join_current_outcomes(scores, generic, opt)
    cutoff_start = date.fromisoformat(end_date) - timedelta(days=args.lookback_days)
    signals = signal_context.filter((pl.col("date") >= cutoff_start) & (pl.col("date") <= CUTOFF))
    assert_no_holdout_leak(signals, "temporal_echo/build_features joined signals")
    priors = build_prior_pairs(signals, signal_context, args.min_gap, args.max_gap)

    signals.write_parquet(signals_path)
    priors.write_parquet(priors_path)
    print(f"[build] wrote {signals_path} ({signals_path.stat().st_size / 1e6:.1f} MB, {len(signals):,} rows)", flush=True)
    print(f"[build] wrote {priors_path} ({priors_path.stat().st_size / 1e6:.1f} MB, {len(priors):,} rows)", flush=True)
    return {
        "version_id": version_id,
        "commit": commit,
        "signals_path": str(signals_path),
        "priors_path": str(priors_path),
        "cached": False,
    }


if __name__ == "__main__":
    main()
