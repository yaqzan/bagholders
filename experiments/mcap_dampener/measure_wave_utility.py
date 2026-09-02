"""Measure portfolio utility for smooth MCD wave candidates.

Experiment-local only. This does not patch production scoring code.

The wave sweep ranked candidates by per-trade TP lift and signal shrinkage. This
runner asks a different question: after the N drop, can the production-like
14-slot, calls-first book still recycle enough high-quality opportunities?

Outputs:
  - utility_summary.json
  - utility_table.csv
  - daily_exposure.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter
from experiments.mcap_dampener.wave_mcd_sweep import Variant, apply_shipped_mcd, apply_wave
from strategy_config import STRATEGY_30DTE

CACHE = ROOT / ".cache" / "mcap_dampener" / "calls_v39_3650.parquet"
BARRIER_DB = ROOT / ".cache" / "barrier_outcomes.db"
BARRIER_PARQUET = ROOT / ".cache" / "barrier_outcomes.parquet"
DEFAULT_SWEEP_RUN = ROOT / "experiments" / "mcap_dampener" / "runs" / "wave_mcd_20260512_154105"
COVID_START = date(2020, 3, 4)
COVID_TROUGH = date(2020, 3, 23)
COVID_LOAD_START = date(2020, 2, 14)
COVID_LOAD_END = date(2020, 3, 31)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def parse_version_id(path: Path) -> int:
    m = re.search(r"calls_v(\d+)_", path.name)
    if not m:
        raise ValueError(f"could not infer version id from {path.name}")
    return int(m.group(1))


def fetch_score_rows(version_id: int, start: date, end: date, predicate: str) -> list[tuple[str, str, int]]:
    from database.trader_database import DB

    rows: list[tuple[str, str, int]] = []
    DB.connect(reuse_if_open=True)
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    for yr in range(start.year, end.year + 1):
        y_start = max(start, date(yr, 1, 1)).isoformat()
        y_end = min(end, date(yr, 12, 31)).isoformat()
        cur = DB.execute_sql(
            f"""
            SELECT symbol, date, overall
            FROM scores
            WHERE version_id = {version_id}
              AND date >= '{y_start}' AND date <= '{y_end}'
              AND {predicate}
            """
        )
        rows.extend(
            (
                str(r[0]),
                r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]),
                int(r[2]),
            )
            for r in cur.fetchall()
        )
    DB.close()
    return rows


def fetch_call_rows_with_mcap(version_id: int, start: date, end: date) -> pl.DataFrame:
    from database.trader_database import DB

    DB.connect(reuse_if_open=True)
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    cur = DB.execute_sql(
        f"""
        SELECT s.symbol, s.date, s.overall, st.market_cap
        FROM scores s
        LEFT JOIN stocks st ON st.symbol = s.symbol
        WHERE s.version_id = {version_id}
          AND s.date >= '{start.isoformat()}' AND s.date <= '{end.isoformat()}'
          AND s.overall >= 70
        """
    )
    rows = cur.fetchall()
    DB.close()
    return pl.DataFrame(
        {
            "symbol": [str(r[0]) for r in rows],
            "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
            "mcap_b": [None if r[3] is None else float(r[3]) / 1e9 for r in rows],
        }
    )


def fetch_trading_dates(version_id: int, start: date, end: date) -> list[date]:
    from database.trader_database import DB

    out: set[date] = set()
    DB.connect(reuse_if_open=True)
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    for yr in range(start.year, end.year + 1):
        y_start = max(start, date(yr, 1, 1)).isoformat()
        y_end = min(end, date(yr, 12, 31)).isoformat()
        cur = DB.execute_sql(
            f"""
            SELECT DISTINCT date
            FROM scores
            WHERE version_id = {version_id}
              AND date >= '{y_start}' AND date <= '{y_end}'
            """
        )
        for (d,) in cur.fetchall():
            if hasattr(d, "isoformat"):
                out.add(d)
            else:
                out.add(date.fromisoformat(str(d)))
    DB.close()
    return sorted(out)


def load_barriers(start: date, end: date, side: str) -> pl.DataFrame:
    if BARRIER_PARQUET.exists():
        return (
            pl.scan_parquet(BARRIER_PARQUET)
            .filter(
                (pl.col("side") == side)
                & (pl.col("barrier_set") == "30dte_opt")
                & (pl.col("w_days") == 15)
                & (pl.col("date") >= start)
                & (pl.col("date") <= end)
            )
            .select(
                [
                    "symbol",
                    pl.col("date").cast(pl.Utf8),
                    "result",
                    "exit_bars",
                    "exit_return",
                    "mae_pct",
                    "mfe_pct",
                ]
            )
            .collect()
        )

    conn = __import__("sqlite3").connect(str(BARRIER_DB))
    try:
        df = pl.read_database(
            f"""
            SELECT symbol, date,
                   result AS result,
                   exit_bars AS exit_bars,
                   exit_return AS exit_return,
                   mae_pct AS mae_pct,
                   mfe_pct AS mfe_pct
            FROM barrier_outcomes
            WHERE side='{side}'
              AND barrier_set='30dte_opt'
              AND w_days = 15
              AND date >= '{start.isoformat()}' AND date <= '{end.isoformat()}'
            """,
            connection=conn,
        )
    finally:
        conn.close()
    return df.with_columns(pl.col("date").cast(pl.Utf8))


def load_barriers_sqlite(start: date, end: date, side: str) -> pl.DataFrame:
    conn = __import__("sqlite3").connect(str(BARRIER_DB))
    try:
        df = pl.read_database(
            f"""
            SELECT symbol, date,
                   result AS result,
                   exit_bars AS exit_bars,
                   exit_return AS exit_return,
                   mae_pct AS mae_pct,
                   mfe_pct AS mfe_pct
            FROM barrier_outcomes
            WHERE side='{side}'
              AND barrier_set='30dte_opt'
              AND w_days = 15
              AND date >= '{start.isoformat()}' AND date <= '{end.isoformat()}'
            """,
            connection=conn,
        )
    finally:
        conn.close()
    return df.with_columns(pl.col("date").cast(pl.Utf8))


def variant_from_record(rec: dict[str, Any]) -> Variant | str:
    shape = rec["shape"]
    if shape in {"baseline", "shipped_mcd"}:
        return shape
    return Variant(
        label=rec["label"],
        shape=shape,
        alpha=float(rec["alpha"]),
        target=float(rec["target"]),
        mcap_mid=float(rec["mcap_mid"]),
        mcap_scale=float(rec["mcap_scale"]),
        mcap_power=float(rec["mcap_power"]),
        score_center=None if rec.get("score_center") is None else float(rec["score_center"]),
        score_sigma=None if rec.get("score_sigma") is None else float(rec["score_sigma"]),
        rise_mid=None if rec.get("rise_mid") is None else float(rec["rise_mid"]),
        rise_scale=None if rec.get("rise_scale") is None else float(rec["rise_scale"]),
        fall_mid=None if rec.get("fall_mid") is None else float(rec["fall_mid"]),
        fall_scale=None if rec.get("fall_scale") is None else float(rec["fall_scale"]),
    )


def load_candidate_records(sweep_run: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    results = sweep_run / "results.jsonl"
    with results.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))

    selected: list[dict[str, Any]] = []
    by_label: set[str] = set()

    def add(rec: dict[str, Any], alias: str | None = None) -> None:
        label = alias or rec["label"]
        if label in by_label:
            return
        clone = dict(rec)
        clone["label"] = label
        selected.append(clone)
        by_label.add(label)

    add(next(r for r in records if r["label"] == "baseline"))
    add(next(r for r in records if r["label"] == "shipped_mcd"))

    soft = [r for r in records if r.get("soft_pass") and r.get("jump_mcap8_70_100", 99) <= 2]
    soft.sort(key=lambda r: r.get("composite", -999), reverse=True)
    if soft:
        add(soft[0], "top_wave")

    for keep in [45, 40, 35, 30, 25]:
        pool = [r for r in soft if r.get("n_drop_75_5y") is not None and r["n_drop_75_5y"] >= -keep]
        if pool:
            pool.sort(key=lambda r: r.get("composite", -999), reverse=True)
            add(pool[0], f"best_keep_{100 - keep}pct_n")

    return selected


def apply_candidate(call_df: pl.DataFrame, variant: Variant | str) -> pl.DataFrame:
    if variant == "baseline":
        return call_df.with_columns(pl.col("overall").alias("score"))
    if variant == "shipped_mcd":
        return apply_shipped_mcd(call_df).rename({"new_overall": "score"})
    return apply_wave(call_df, variant).rename({"new_overall": "score"})


def tp_rate(df: pl.DataFrame, result_col: str = "result") -> float | None:
    n = df.height
    if n == 0:
        return None
    return float(df.select((pl.col(result_col) == 1).cast(pl.Float64).mean()).item() * 100.0)


def build_signals(call_variant: pl.DataFrame, put_df: pl.DataFrame) -> pl.DataFrame:
    calls = (
        call_variant.filter(pl.col("score") >= STRATEGY_30DTE.PRIMARY_THRESHOLD)
        .select(
            [
                "symbol",
                "date",
                pl.col("score").cast(pl.Int64),
                pl.lit("call").alias("trade_side"),
                pl.col("result").cast(pl.Int64),
                pl.col("exit_bars").cast(pl.Int64),
                pl.col("exit_return").cast(pl.Float64),
            ]
        )
    )
    puts = (
        put_df.filter(pl.col("score") <= STRATEGY_30DTE.PUT_THRESHOLD)
        .select(
            [
                "symbol",
                "date",
                pl.col("score").cast(pl.Int64),
                pl.lit("put").alias("trade_side"),
                pl.col("result").cast(pl.Int64),
                pl.col("exit_bars").cast(pl.Int64),
                pl.col("exit_return").cast(pl.Float64),
            ]
        )
    )
    return pl.concat([calls, puts], how="vertical").with_columns(
        pl.col("date").cast(pl.Utf8),
        pl.when(pl.col("exit_bars").is_null() | (pl.col("exit_bars") < 1))
        .then(pl.lit(STRATEGY_30DTE.HOLD_DAYS))
        .otherwise(pl.col("exit_bars"))
        .cast(pl.Int64)
        .alias("exit_bars"),
    )


def simulate_slots(
    signals: pl.DataFrame,
    trading_dates: list[date],
    *,
    calls_only: bool,
    start: date,
    end: date,
) -> dict[str, Any]:
    dates = [d for d in trading_dates if start <= d <= end]
    date_to_idx = {d.isoformat(): i for i, d in enumerate(dates)}
    by_date: dict[str, list[dict[str, Any]]] = {d.isoformat(): [] for d in dates}

    sim_signals = signals.filter(
        (pl.col("date") >= start.isoformat())
        & (pl.col("date") <= end.isoformat())
        & ((pl.col("trade_side") == "call") if calls_only else pl.lit(True))
    )
    eligible = sim_signals.height
    for row in sim_signals.to_dicts():
        if row["date"] in by_date:
            by_date[row["date"]].append(row)

    open_pos: list[dict[str, Any]] = []
    filled: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    skipped_cap = 0
    skipped_symbol = 0
    slot_days = 0
    call_slot_days = 0
    put_slot_days = 0
    days_at_cap = 0
    fill_attempts = 0

    for idx, d in enumerate(dates):
        d_str = d.isoformat()
        open_pos = [p for p in open_pos if p["exit_idx"] > idx]
        open_symbols = {p["symbol"] for p in open_pos}

        rows = by_date.get(d_str, [])
        rows.sort(
            key=lambda r: (
                0 if r["trade_side"] == "call" else 1,
                -int(r["score"]) if r["trade_side"] == "call" else int(r["score"]),
                r["symbol"],
            )
        )
        for row in rows:
            fill_attempts += 1
            if row["symbol"] in open_symbols:
                skipped_symbol += 1
                continue
            if len(open_pos) >= STRATEGY_30DTE.MAX_POSITIONS:
                skipped_cap += 1
                continue
            hold = max(1, int(row["exit_bars"]))
            exit_idx = min(idx + hold, len(dates))
            rec = {
                **row,
                "entry_date": d_str,
                "exit_idx": exit_idx,
                "hold_bars": hold,
            }
            open_pos.append(rec)
            open_symbols.add(row["symbol"])
            filled.append(rec)

        n_open = len(open_pos)
        n_call = sum(1 for p in open_pos if p["trade_side"] == "call")
        n_put = n_open - n_call
        slot_days += n_open
        call_slot_days += n_call
        put_slot_days += n_put
        if n_open >= STRATEGY_30DTE.MAX_POSITIONS:
            days_at_cap += 1
        daily.append(
            {
                "date": d_str,
                "open_total": n_open,
                "open_calls": n_call,
                "open_puts": n_put,
                "new_signals": len(rows),
            }
        )

    years = len(dates) / 252.0 if dates else 0.0
    filled_n = len(filled)
    wins = sum(1 for r in filled if int(r["result"]) == 1)
    filled_calls = [r for r in filled if r["trade_side"] == "call"]
    filled_puts = [r for r in filled if r["trade_side"] == "put"]
    avg_hold = sum(r["hold_bars"] for r in filled) / filled_n if filled_n else None
    capacity_at_avg_hold = (
        STRATEGY_30DTE.MAX_POSITIONS * 252.0 / avg_hold if avg_hold and avg_hold > 0 else None
    )

    covid_days = [
        r
        for r in daily
        if COVID_START.isoformat() <= r["date"] <= COVID_TROUGH.isoformat()
    ]
    covid_call_slot_days = sum(r["open_calls"] for r in covid_days)
    covid_put_slot_days = sum(r["open_puts"] for r in covid_days)

    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "days": len(dates),
        "years": years,
        "eligible_signals": eligible,
        "fill_attempts": fill_attempts,
        "filled_trades": filled_n,
        "filled_calls": len(filled_calls),
        "filled_puts": len(filled_puts),
        "skipped_cap": skipped_cap,
        "skipped_symbol": skipped_symbol,
        "slot_utilization_pct": (slot_days / (STRATEGY_30DTE.MAX_POSITIONS * len(dates)) * 100.0)
        if dates
        else None,
        "avg_open_total": slot_days / len(dates) if dates else None,
        "avg_open_calls": call_slot_days / len(dates) if dates else None,
        "avg_open_puts": put_slot_days / len(dates) if dates else None,
        "days_at_cap_pct": days_at_cap / len(dates) * 100.0 if dates else None,
        "avg_hold_bars_filled": avg_hold,
        "fills_per_slot_year": filled_n / STRATEGY_30DTE.MAX_POSITIONS / years if years else None,
        "annual_capacity_at_avg_hold": capacity_at_avg_hold,
        "annual_filled_trades": filled_n / years if years else None,
        "annual_eligible_signals": eligible / years if years else None,
        "win_rate_filled_pct": wins / filled_n * 100.0 if filled_n else None,
        "call_win_rate_filled_pct": (
            sum(1 for r in filled_calls if int(r["result"]) == 1) / len(filled_calls) * 100.0
            if filled_calls
            else None
        ),
        "put_win_rate_filled_pct": (
            sum(1 for r in filled_puts if int(r["result"]) == 1) / len(filled_puts) * 100.0
            if filled_puts
            else None
        ),
        "covid_open_call_slot_days": covid_call_slot_days,
        "covid_open_put_slot_days": covid_put_slot_days,
        "covid_avg_open_calls": covid_call_slot_days / len(covid_days) if covid_days else None,
        "covid_avg_open_puts": covid_put_slot_days / len(covid_days) if covid_days else None,
        "daily": daily,
    }


def flatten_metrics(label: str, rec: dict[str, Any], scope: str) -> dict[str, Any]:
    keys = [
        "eligible_signals",
        "filled_trades",
        "filled_calls",
        "filled_puts",
        "skipped_cap",
        "slot_utilization_pct",
        "avg_open_total",
        "avg_open_calls",
        "avg_open_puts",
        "days_at_cap_pct",
        "avg_hold_bars_filled",
        "fills_per_slot_year",
        "annual_capacity_at_avg_hold",
        "annual_filled_trades",
        "annual_eligible_signals",
        "win_rate_filled_pct",
        "call_win_rate_filled_pct",
        "put_win_rate_filled_pct",
        "covid_avg_open_calls",
        "covid_avg_open_puts",
    ]
    out = {"label": label, "scope": scope}
    out.update({k: rec.get(k) for k in keys})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_SWEEP_RUN)
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()

    out_dir = args.out_dir or (args.run_dir / "utility")
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "status.json"
    write_json(status_path, {"state": "running", "phase": "load_calls", "started_at": now_iso()})

    version_id = parse_version_id(CACHE)
    call_base = pl.read_parquet(CACHE).with_columns(pl.col("date").cast(pl.Utf8))
    call_base = pre_cutoff_filter(call_base)
    assert_no_holdout_leak(call_base, "mcap_dampener/measure_wave_utility calls")
    call_base = call_base.filter(pl.col("opt_result_15").is_not_null())
    start_s = call_base.select(pl.col("date").min()).item()
    end_s = call_base.select(pl.col("date").max()).item()
    start = date.fromisoformat(start_s)
    end = date.fromisoformat(end_s)
    five_year_start = max(start, date.fromisoformat(end_s).replace(year=date.fromisoformat(end_s).year - 5))

    write_json(
        status_path,
        {
            "state": "running",
            "phase": "load_puts_and_barriers",
            "updated_at": now_iso(),
            "version_id": version_id,
            "date_start": start_s,
            "date_end": end_s,
        },
    )

    put_rows = fetch_score_rows(version_id, start, end, "overall <= 25")
    puts = pl.DataFrame(
        {
            "symbol": [r[0] for r in put_rows],
            "date": [r[1] for r in put_rows],
            "score": [r[2] for r in put_rows],
        }
    )
    put_barriers = load_barriers(start, end, "high")
    call_barriers = load_barriers(start, end, "low")
    puts = puts.join(put_barriers, on=["symbol", "date"], how="inner")
    call_base = (
        call_base.drop([c for c in ["result", "exit_bars", "exit_return", "mae_pct", "mfe_pct"] if c in call_base.columns])
        .join(call_barriers, on=["symbol", "date"], how="inner")
    )
    assert_no_holdout_leak(puts, "mcap_dampener/measure_wave_utility puts")

    trading_dates = fetch_trading_dates(version_id, start, end)
    covid_trading_dates = fetch_trading_dates(version_id, COVID_LOAD_START, COVID_LOAD_END)
    covid_call_base = fetch_call_rows_with_mcap(version_id, COVID_LOAD_START, COVID_LOAD_END)
    covid_put_rows = fetch_score_rows(version_id, COVID_LOAD_START, COVID_LOAD_END, "overall <= 25")
    covid_puts = pl.DataFrame(
        {
            "symbol": [r[0] for r in covid_put_rows],
            "date": [r[1] for r in covid_put_rows],
            "score": [r[2] for r in covid_put_rows],
        }
    )
    covid_call_barriers = load_barriers_sqlite(COVID_LOAD_START, COVID_LOAD_END, "low")
    covid_put_barriers = load_barriers_sqlite(COVID_LOAD_START, COVID_LOAD_END, "high")
    covid_call_base = covid_call_base.join(covid_call_barriers, on=["symbol", "date"], how="inner")
    covid_puts = covid_puts.join(covid_put_barriers, on=["symbol", "date"], how="inner")
    candidates = load_candidate_records(args.run_dir)

    write_json(
        status_path,
        {
            "state": "running",
            "phase": "simulate",
            "updated_at": now_iso(),
            "candidate_count": len(candidates),
            "puts": puts.height,
            "calls": call_base.height,
            "covid_puts": covid_puts.height,
            "covid_calls": covid_call_base.height,
        },
    )

    summary: dict[str, Any] = {
        "started_at": now_iso(),
        "version_id": version_id,
        "date_start": start_s,
        "date_end": end_s,
        "max_positions": STRATEGY_30DTE.MAX_POSITIONS,
        "hold_days_config": STRATEGY_30DTE.HOLD_DAYS,
        "call_threshold": STRATEGY_30DTE.PRIMARY_THRESHOLD,
        "put_threshold": STRATEGY_30DTE.PUT_THRESHOLD,
        "covid_interval": {"start": COVID_START.isoformat(), "trough": COVID_TROUGH.isoformat()},
        "covid_proxy_load_window": {
            "start": COVID_LOAD_START.isoformat(),
            "end": COVID_LOAD_END.isoformat(),
            "note": "Exposure proxy uses v39 score rows plus 30dte_opt barrier rows for the narrow 2020 crash window; it is not a full MC max-DD rerun.",
        },
        "candidates": {},
    }
    table_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []

    for rec in candidates:
        label = rec["label"]
        variant = variant_from_record(rec)
        call_variant = apply_candidate(call_base, variant)
        call_variant = call_variant.select(
            [
                "symbol",
                "date",
                "overall",
                "score",
                "result",
                "exit_bars",
                "exit_return",
            ]
        )
        signals = build_signals(call_variant, puts)
        call_eligible = signals.filter(pl.col("trade_side") == "call")
        put_eligible = signals.filter(pl.col("trade_side") == "put")

        all_start = start
        five_start = five_year_start
        call_only_5y = simulate_slots(
            signals, trading_dates, calls_only=True, start=five_start, end=end
        )
        combined_5y = simulate_slots(
            signals, trading_dates, calls_only=False, start=five_start, end=end
        )
        combined_all = simulate_slots(
            signals, trading_dates, calls_only=False, start=all_start, end=end
        )

        covid_call_variant = apply_candidate(covid_call_base, variant).select(
            [
                "symbol",
                "date",
                "overall",
                "score",
                "result",
                "exit_bars",
                "exit_return",
            ]
        )
        covid_signals = build_signals(covid_call_variant, covid_puts)
        covid_combined = simulate_slots(
            covid_signals,
            covid_trading_dates,
            calls_only=False,
            start=COVID_LOAD_START,
            end=COVID_LOAD_END,
        )
        covid_call_only = simulate_slots(
            covid_signals,
            covid_trading_dates,
            calls_only=True,
            start=COVID_LOAD_START,
            end=COVID_LOAD_END,
        )

        cand = {
            "sweep_metrics": {k: rec.get(k) for k in [
                "shape",
                "composite",
                "lift_75_5y",
                "lift_75_10y",
                "n_drop_75_5y",
                "jump_mcap8_70_100",
                "spill_85_5y",
                "spill_90_5y",
            ]},
            "variant": asdict(variant) if isinstance(variant, Variant) else str(variant),
            "signal_quality": {
                "call_eligible_n": call_eligible.height,
                "call_eligible_tp_pct": tp_rate(call_eligible),
                "put_eligible_n": put_eligible.height,
                "put_eligible_tp_pct": tp_rate(put_eligible),
            },
            "call_only_5y": {k: v for k, v in call_only_5y.items() if k != "daily"},
            "calls_first_with_puts_5y": {k: v for k, v in combined_5y.items() if k != "daily"},
            "calls_first_with_puts_all": {k: v for k, v in combined_all.items() if k != "daily"},
            "covid_proxy_call_only": {k: v for k, v in covid_call_only.items() if k != "daily"},
            "covid_proxy_calls_first_with_puts": {
                k: v for k, v in covid_combined.items() if k != "daily"
            },
        }
        summary["candidates"][label] = cand
        table_rows.extend(
            [
                flatten_metrics(label, call_only_5y, "call_only_5y"),
                flatten_metrics(label, combined_5y, "calls_first_with_puts_5y"),
                flatten_metrics(label, combined_all, "calls_first_with_puts_all"),
                flatten_metrics(label, covid_call_only, "covid_proxy_call_only"),
                flatten_metrics(label, covid_combined, "covid_proxy_calls_first_with_puts"),
            ]
        )
        for row in combined_5y["daily"]:
            daily_rows.append({"label": label, **row})

    summary["finished_at"] = now_iso()
    write_json(out_dir / "utility_summary.json", summary)

    with (out_dir / "utility_table.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(table_rows[0].keys()))
        writer.writeheader()
        writer.writerows(table_rows)
    with (out_dir / "daily_exposure.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(daily_rows[0].keys()))
        writer.writeheader()
        writer.writerows(daily_rows)

    write_json(
        status_path,
        {
            "state": "done",
            "phase": "complete",
            "finished_at": now_iso(),
            "summary": str(out_dir / "utility_summary.json"),
            "table": str(out_dir / "utility_table.csv"),
            "daily": str(out_dir / "daily_exposure.csv"),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
