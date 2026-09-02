"""Targeted historical barrier backfill for Participation Wave stress tests."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.barrier_cache import compute_outcomes_for_symbol, get_conn, rebuild_duck_mirror, upsert_rows


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _parse_csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def run(args: argparse.Namespace) -> int:
    from database.models.core import Stock
    from database.models.technical import PriceHistory

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "mcd_cwwd_wvd_recal" / "runs" / f"historical_barriers_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    done_path = run_dir / "done.json"
    failed_path = run_dir / "failed.json"

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("--end-date must be >= --start-date")

    periods = _parse_csv(args.periods)
    barrier_sets = _parse_csv(args.barrier_sets)
    price_start = start - timedelta(days=args.history_pad_days)
    price_end = end + timedelta(days=args.forward_pad_days)

    symbols = [s.symbol for s in Stock.select().order_by(Stock.symbol)]
    _write_json(
        status_path,
        {
            "phase": "setup",
            "state": "running",
            "started_at": _now(),
            "start_date": args.start_date,
            "end_date": args.end_date,
            "periods": periods,
            "barrier_sets": barrier_sets,
            "symbols_total": len(symbols),
        },
    )

    conn = get_conn()
    total_rows = 0
    touched_symbols = 0
    try:
        for idx, sym in enumerate(symbols, 1):
            bars = list(
                PriceHistory.select(
                    PriceHistory.date,
                    PriceHistory.close,
                    PriceHistory.high,
                    PriceHistory.low,
                    PriceHistory.open,
                )
                .where(
                    (PriceHistory.symbol == sym)
                    & (PriceHistory.date >= price_start)
                    & (PriceHistory.date <= price_end)
                )
                .order_by(PriceHistory.date)
                .tuples()
            )
            if len(bars) < 30:
                n = 0
            else:
                rows = [
                    row
                    for row in compute_outcomes_for_symbol(
                        sym,
                        bars,
                        barrier_sets=barrier_sets,
                        periods=periods,
                    )
                    if args.start_date <= row[1] <= args.end_date
                ]
                n = upsert_rows(conn, rows) if rows else 0
                total_rows += n
                if n:
                    touched_symbols += 1

            if idx % args.status_every == 0 or idx == len(symbols):
                _write_json(
                    status_path,
                    {
                        "phase": "backfill",
                        "state": "running",
                        "completed": idx,
                        "total": len(symbols),
                        "current_symbol": sym,
                        "inserted_rows": total_rows,
                        "touched_symbols": touched_symbols,
                        "updated_at": _now(),
                    },
                )
                print(f"[backfill] {idx}/{len(symbols)} {sym}: +{n:,} rows total={total_rows:,}", flush=True)
    except Exception as exc:
        _write_json(
            failed_path,
            {
                "state": "failed",
                "finished_at": _now(),
                "reason": str(exc),
                "inserted_rows": total_rows,
            },
        )
        raise
    finally:
        conn.close()

    _write_json(status_path, {"phase": "rebuild_duck", "state": "running", "inserted_rows": total_rows, "updated_at": _now()})
    duck_path = rebuild_duck_mirror(verbose=True)

    result = {
        "state": "done",
        "finished_at": _now(),
        "inserted_rows": total_rows,
        "touched_symbols": touched_symbols,
        "duckdb": str(duck_path),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "periods": periods,
        "barrier_sets": barrier_sets,
    }
    _write_json(done_path, result)
    _write_json(status_path, {"phase": "done", **result})
    print(json.dumps(result, indent=2), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument("--end-date", default="2020-12-31")
    parser.add_argument("--periods", default="7d,15d,30d")
    parser.add_argument("--barrier-sets", default="30dte_generic,30dte_opt")
    parser.add_argument("--history-pad-days", type=int, default=120)
    parser.add_argument("--forward-pad-days", type=int, default=120)
    parser.add_argument("--status-every", type=int, default=25)
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
