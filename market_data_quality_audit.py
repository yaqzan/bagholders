#!/usr/bin/env python
"""Read-only audit for suspicious price and indicator data.

The goal is to find persisted data that can silently weaken scoring: zero
volume rows, impossible OHLC envelopes, missing indicator rows, and indicator
nulls that occur well past normal TA warmup periods.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from database.trader_database import DB


PRICE_TABLES = {
    "daily": ("price_history", "indicators"),
    "weekly": ("weekly_price_history", "weekly_indicators"),
}

CRITICAL_INDICATOR_FIELDS = (
    "rsi",
    "stoch_rsi",
    "stoch",
    "macd",
    "macd_signal",
    "macd_hist",
    "upper_band",
    "middle_band",
    "lower_band",
    "ma_9",
    "ema_9",
    "ma_21",
    "ema_21",
    "ma_50",
    "ema_50",
    "ma_200",
    "ema_200",
    "obv",
)


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _connect() -> None:
    DB.connect(reuse_if_open=True)


def _make_run_dir(base: Path | None) -> Path:
    root = base or Path(".cache") / "data_quality"
    run_dir = root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _scope_clause(
    alias: str,
    args: argparse.Namespace,
    *,
    include_stock_join: bool = True,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if include_stock_join and not args.include_delisted:
        clauses.append("s.delisted_date IS NULL")
    if args.since:
        clauses.append(f"{alias}.date >= %s")
        params.append(args.since)
    if args.until:
        clauses.append(f"{alias}.date <= %s")
        params.append(args.until)
    if args.symbol:
        placeholders = ", ".join(["%s"] * len(args.symbol))
        clauses.append(f"{alias}.symbol IN ({placeholders})")
        params.extend(args.symbol)

    return (" AND ".join(clauses) if clauses else "1=1"), params


def _execute(sql: str, params: list[Any] | tuple[Any, ...] | None = None):
    return DB.execute_sql(sql, params or ())


def _scalar(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> int:
    row = _execute(sql, params).fetchone()
    return int(row[0] or 0)


def _write_query_csv(
    path: Path,
    sql: str,
    params: list[Any] | tuple[Any, ...] | None = None,
) -> int:
    cursor = _execute(sql, params)
    columns = [desc[0] for desc in cursor.description]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in cursor.fetchall():
            writer.writerow(row)
            count += 1
    return count


def _write_status(run_dir: Path, status: dict[str, Any]) -> None:
    status["updated_at"] = datetime.now().isoformat(timespec="seconds")
    (run_dir / "status.json").write_text(
        json.dumps(status, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _price_issue_predicate(alias: str, eps: float) -> str:
    return (
        f"{alias}.open <= 0 OR {alias}.high <= 0 OR {alias}.low <= 0 "
        f"OR {alias}.close <= 0 OR {alias}.volume < 0 "
        f"OR {alias}.high + {eps} < {alias}.low "
        f"OR {alias}.high + {eps} < {alias}.open "
        f"OR {alias}.high + {eps} < {alias}.close "
        f"OR {alias}.low - {eps} > {alias}.open "
        f"OR {alias}.low - {eps} > {alias}.close"
    )


def _scoped_symbols(args: argparse.Namespace) -> list[str]:
    clauses: list[str] = []
    params: list[Any] = []
    if not args.include_delisted:
        clauses.append("delisted_date IS NULL")
    if args.symbol:
        placeholders = ", ".join(["%s"] * len(args.symbol))
        clauses.append(f"symbol IN ({placeholders})")
        params.extend(args.symbol)
    where = " AND ".join(clauses) if clauses else "1=1"
    return [
        row[0]
        for row in _execute(
            f"SELECT symbol FROM stocks WHERE {where} ORDER BY symbol",
            params,
        ).fetchall()
    ]


def _date_filter_sql(alias: str, args: argparse.Namespace) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if args.since:
        clauses.append(f"{alias}.date >= %s")
        params.append(args.since)
    if args.until:
        clauses.append(f"{alias}.date <= %s")
        params.append(args.until)
    return (" AND ".join(clauses), params)


def _audit_price_table(
    run_dir: Path,
    label: str,
    price_table: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    symbols = _scoped_symbols(args)
    date_sql, date_params = _date_filter_sql("ph", args)
    date_clause = f" AND {date_sql}" if date_sql else ""
    issue_predicate = _price_issue_predicate("ph", args.ohlc_epsilon)

    total_rows = 0
    zero_volume_rows = 0
    flat_zero_volume_rows = 0
    price_issue_rows = 0
    zero_groups: list[tuple[str, int, Any, Any]] = []
    issue_groups: list[tuple[str, int, Any, Any]] = []
    zero_rows = 0
    issue_rows = 0

    zero_rows_path = run_dir / f"{label}_zero_volume_rows.csv"
    issue_rows_path = run_dir / f"{label}_price_issues.csv"
    with zero_rows_path.open("w", newline="", encoding="utf-8") as zero_fh, issue_rows_path.open(
        "w", newline="", encoding="utf-8"
    ) as issue_fh:
        zero_writer = csv.writer(zero_fh)
        issue_writer = csv.writer(issue_fh)
        zero_writer.writerow(("symbol", "date", "open", "high", "low", "close", "volume", "pulled_at"))
        issue_writer.writerow(
            (
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "pulled_at",
                "nonpositive_ohlc",
                "negative_volume",
                "high_below_low",
                "high_below_open",
                "high_below_close",
                "low_above_open",
                "low_above_close",
            )
        )

        for symbol in symbols:
            base_params = [symbol] + date_params
            base_where = f"ph.symbol = %s{date_clause}"

            total_rows += _scalar(
                f"SELECT COUNT(*) FROM {price_table} ph WHERE {base_where}",
                base_params,
            )

            zero_stats = _execute(
                f"""
                SELECT COUNT(*), MIN(ph.date), MAX(ph.date)
                FROM {price_table} ph
                WHERE {base_where} AND ph.volume = 0
                """,
                base_params,
            ).fetchone()
            zero_count = int(zero_stats[0] or 0)
            zero_volume_rows += zero_count
            if zero_count:
                zero_groups.append((symbol, zero_count, zero_stats[1], zero_stats[2]))
                remaining = args.limit - zero_rows
                if remaining > 0:
                    cursor = _execute(
                        f"""
                        SELECT ph.symbol, ph.date, ph.open, ph.high, ph.low, ph.close, ph.volume, ph.pulled_at
                        FROM {price_table} ph
                        WHERE {base_where} AND ph.volume = 0
                        ORDER BY ph.date DESC
                        LIMIT %s
                        """,
                        base_params + [remaining],
                    )
                    for row in cursor.fetchall():
                        zero_writer.writerow(row)
                        zero_rows += 1

            flat_zero_volume_rows += _scalar(
                f"""
                SELECT COUNT(*)
                FROM {price_table} ph
                WHERE {base_where}
                  AND ph.volume = 0
                  AND ph.open = ph.high AND ph.high = ph.low AND ph.low = ph.close
                """,
                base_params,
            )

            issue_stats = _execute(
                f"""
                SELECT COUNT(*), MIN(ph.date), MAX(ph.date)
                FROM {price_table} ph
                WHERE {base_where} AND ({issue_predicate})
                """,
                base_params,
            ).fetchone()
            issue_count = int(issue_stats[0] or 0)
            price_issue_rows += issue_count
            if issue_count:
                issue_groups.append((symbol, issue_count, issue_stats[1], issue_stats[2]))
                remaining = args.limit - issue_rows
                if remaining > 0:
                    cursor = _execute(
                        f"""
                        SELECT
                            ph.symbol,
                            ph.date,
                            ph.open,
                            ph.high,
                            ph.low,
                            ph.close,
                            ph.volume,
                            ph.pulled_at,
                            CASE WHEN ph.open <= 0 OR ph.high <= 0 OR ph.low <= 0 OR ph.close <= 0
                                 THEN 1 ELSE 0 END AS nonpositive_ohlc,
                            CASE WHEN ph.volume < 0 THEN 1 ELSE 0 END AS negative_volume,
                            CASE WHEN ph.high + {args.ohlc_epsilon} < ph.low THEN 1 ELSE 0 END AS high_below_low,
                            CASE WHEN ph.high + {args.ohlc_epsilon} < ph.open THEN 1 ELSE 0 END AS high_below_open,
                            CASE WHEN ph.high + {args.ohlc_epsilon} < ph.close THEN 1 ELSE 0 END AS high_below_close,
                            CASE WHEN ph.low - {args.ohlc_epsilon} > ph.open THEN 1 ELSE 0 END AS low_above_open,
                            CASE WHEN ph.low - {args.ohlc_epsilon} > ph.close THEN 1 ELSE 0 END AS low_above_close
                        FROM {price_table} ph
                        WHERE {base_where} AND ({issue_predicate})
                        ORDER BY ph.date DESC
                        LIMIT %s
                        """,
                        base_params + [remaining],
                    )
                    for row in cursor.fetchall():
                        issue_writer.writerow(row)
                        issue_rows += 1

    zero_groups.sort(key=lambda row: (-row[1], row[0]))
    issue_groups.sort(key=lambda row: (-row[1], row[0]))
    with (run_dir / f"{label}_zero_volume_by_symbol.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(("symbol", "rows", "first_date", "last_date"))
        for row in zero_groups[: args.limit]:
            writer.writerow(row)
    with (run_dir / f"{label}_price_issues_by_symbol.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(("symbol", "rows", "first_date", "last_date"))
        for row in issue_groups[: args.limit]:
            writer.writerow(row)

    zero_by_symbol = min(len(zero_groups), args.limit)
    issue_by_symbol = min(len(issue_groups), args.limit)

    return {
        "table": price_table,
        "rows_scanned": total_rows,
        "zero_volume_rows": zero_volume_rows,
        "flat_zero_volume_rows": flat_zero_volume_rows,
        "price_issue_rows": price_issue_rows,
        "csv_rows": {
            f"{label}_zero_volume_by_symbol.csv": zero_by_symbol,
            f"{label}_zero_volume_rows.csv": zero_rows,
            f"{label}_price_issues.csv": issue_rows,
            f"{label}_price_issues_by_symbol.csv": issue_by_symbol,
        },
    }


def _audit_indicator_table(
    run_dir: Path,
    label: str,
    price_table: str,
    indicator_table: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    ph_scope, ph_params = _scope_clause("ph", args)
    ind_scope, ind_params = _scope_clause("i", args)
    stock_join_ph = "JOIN stocks s ON s.symbol = ph.symbol"
    stock_join_i = "JOIN stocks s ON s.symbol = i.symbol"
    symbols = _scoped_symbols(args)
    date_sql, date_params = _date_filter_sql("ph", args)
    ph_date_clause = f" AND {date_sql}" if date_sql else ""
    ind_date_sql, ind_date_params = _date_filter_sql("i", args)
    ind_date_clause = f" AND {ind_date_sql}" if ind_date_sql else ""

    missing_indicator_rows = 0
    indicator_without_price_rows = None
    orphan_csv_rows = 0
    missing_csv_rows = 0
    missing_path = run_dir / f"{label}_missing_indicators.csv"
    with missing_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(("symbol", "date", "open", "high", "low", "close", "volume"))
        for symbol in symbols:
            base_params = [symbol] + date_params
            base_where = f"ph.symbol = %s{ph_date_clause}"
            missing_count = _scalar(
                f"""
                SELECT COUNT(*)
                FROM {price_table} ph
                WHERE {base_where}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {indicator_table} i
                      WHERE i.symbol = ph.symbol AND i.date = ph.date
                  )
                """,
                base_params,
            )
            missing_indicator_rows += missing_count
            remaining = args.limit - missing_csv_rows
            if missing_count == 0 or remaining <= 0:
                continue
            cursor = _execute(
                f"""
                SELECT ph.symbol, ph.date, ph.open, ph.high, ph.low, ph.close, ph.volume
                FROM {price_table} ph
                WHERE {base_where}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {indicator_table} i
                      WHERE i.symbol = ph.symbol AND i.date = ph.date
                  )
                ORDER BY ph.date DESC
                LIMIT %s
                """,
                base_params + [remaining],
            )
            for row in cursor.fetchall():
                writer.writerow(row)
                missing_csv_rows += 1

    if args.check_orphan_indicators:
        indicator_without_price_rows = 0
        orphan_path = run_dir / f"{label}_indicators_without_price.csv"
        with orphan_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(("symbol", "date"))
            for symbol in symbols:
                base_params = [symbol] + ind_date_params
                base_where = f"i.symbol = %s{ind_date_clause}"
                orphan_count = _scalar(
                    f"""
                    SELECT COUNT(*)
                    FROM {indicator_table} i
                    WHERE {base_where}
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {price_table} ph
                          WHERE ph.symbol = i.symbol AND ph.date = i.date
                      )
                    """,
                    base_params,
                )
                indicator_without_price_rows += orphan_count
                remaining = args.limit - orphan_csv_rows
                if orphan_count == 0 or remaining <= 0:
                    continue
                cursor = _execute(
                    f"""
                    SELECT i.symbol, i.date
                    FROM {indicator_table} i
                    WHERE {base_where}
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {price_table} ph
                          WHERE ph.symbol = i.symbol AND ph.date = i.date
                      )
                    ORDER BY i.date DESC
                    LIMIT %s
                    """,
                    base_params + [remaining],
                )
                for row in cursor.fetchall():
                    writer.writerow(row)
                    orphan_csv_rows += 1

    warmup_days = args.weekly_warmup_days if label == "weekly" else args.daily_warmup_days
    null_predicate = " OR ".join(f"i.{field} IS NULL" for field in CRITICAL_INDICATOR_FIELDS)
    null_columns_expr = "CONCAT_WS(',', " + ", ".join(
        f"CASE WHEN i.{field} IS NULL THEN '{field}' ELSE NULL END"
        for field in CRITICAL_INDICATOR_FIELDS
    ) + ")"

    first_clauses: list[str] = []
    first_params: list[Any] = []
    if not args.include_delisted:
        first_clauses.append("s.delisted_date IS NULL")
    if args.symbol:
        placeholders = ", ".join(["%s"] * len(args.symbol))
        first_clauses.append(f"ph.symbol IN ({placeholders})")
        first_params.extend(args.symbol)
    first_where = " AND ".join(first_clauses) if first_clauses else "1=1"
    first_dates = _execute(
        f"""
        SELECT ph.symbol, MIN(ph.date) AS first_price_date
        FROM {price_table} ph
        JOIN stocks s ON s.symbol = ph.symbol
        WHERE {first_where}
        GROUP BY ph.symbol
        ORDER BY ph.symbol
        """,
        first_params,
    ).fetchall()

    null_rows_after_warmup = 0
    null_csv_rows = 0
    null_csv_path = run_dir / f"{label}_indicator_nulls_after_warmup.csv"
    with null_csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(("symbol", "date", "first_price_date", "null_fields"))
        for symbol, first_price_date in first_dates:
            threshold = first_price_date + timedelta(days=warmup_days)
            lower = threshold.isoformat()
            if args.since and args.since > lower:
                lower = args.since

            clauses = ["i.symbol = %s", "i.date >= %s", f"({null_predicate})"]
            params_for_symbol: list[Any] = [symbol, lower]
            if args.until:
                clauses.append("i.date <= %s")
                params_for_symbol.append(args.until)
            where_for_symbol = " AND ".join(clauses)

            symbol_count = _scalar(
                f"SELECT COUNT(*) FROM {indicator_table} i WHERE {where_for_symbol}",
                params_for_symbol,
            )
            null_rows_after_warmup += symbol_count
            remaining = args.limit - null_csv_rows
            if symbol_count == 0 or remaining <= 0:
                continue

            cursor = _execute(
                f"""
                SELECT i.symbol, i.date, %s AS first_price_date, {null_columns_expr} AS null_fields
                FROM {indicator_table} i
                WHERE {where_for_symbol}
                ORDER BY i.date DESC
                LIMIT %s
                """,
                [first_price_date] + params_for_symbol + [remaining],
            )
            for row in cursor.fetchall():
                writer.writerow(row)
                null_csv_rows += 1

    return {
        "table": indicator_table,
        "missing_indicator_rows": missing_indicator_rows,
        "indicator_without_price_rows": indicator_without_price_rows,
        "indicator_null_rows_after_warmup": null_rows_after_warmup,
        "warmup_days": warmup_days,
        "csv_rows": {
            f"{label}_missing_indicators.csv": missing_csv_rows,
            f"{label}_indicators_without_price.csv": orphan_csv_rows,
            f"{label}_indicator_nulls_after_warmup.csv": null_csv_rows,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit persisted market data for price and indicator abnormalities."
    )
    parser.add_argument("--since", help="Only audit rows on or after YYYY-MM-DD.")
    parser.add_argument("--until", help="Only audit rows on or before YYYY-MM-DD.")
    parser.add_argument(
        "--symbol",
        action="append",
        help="Restrict to a symbol. Repeat for multiple symbols.",
    )
    parser.add_argument(
        "--include-delisted",
        action="store_true",
        help="Include stocks with delisted_date set. Defaults to active stocks only.",
    )
    parser.add_argument(
        "--daily-only",
        action="store_true",
        help="Skip weekly price/indicator tables.",
    )
    parser.add_argument(
        "--check-orphan-indicators",
        action="store_true",
        help=(
            "Also scan for indicator rows without matching price rows. This is "
            "expensive on the current MySQL indexes, so it is off by default."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Max rows per detail CSV. Summary counts are not limited.",
    )
    parser.add_argument(
        "--ohlc-epsilon",
        type=float,
        default=0.01,
        help="Tolerance for OHLC envelope checks, in price units.",
    )
    parser.add_argument(
        "--daily-warmup-days",
        type=int,
        default=320,
        help="Calendar days after first daily price before indicator nulls are suspicious.",
    )
    parser.add_argument(
        "--weekly-warmup-days",
        type=int,
        default=1500,
        help="Calendar days after first weekly price before weekly indicator nulls are suspicious.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Parent directory for timestamped audit artifacts. Default: .cache/data_quality",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    if args.symbol:
        args.symbol = [s.upper() for s in args.symbol]

    _connect()
    run_dir = _make_run_dir(args.output_dir)

    summary: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "scope": {
            "since": args.since,
            "until": args.until,
            "symbols": args.symbol or "ALL",
            "include_delisted": args.include_delisted,
            "limit_per_csv": args.limit,
            "ohlc_epsilon": args.ohlc_epsilon,
        },
        "tables": {},
    }
    status = {
        "state": "running",
        "phase": "starting",
        "started_at": summary["started_at"],
        "updated_at": summary["started_at"],
        "output_dir": str(run_dir),
    }
    _write_status(run_dir, status)

    labels = ["daily"] if args.daily_only else ["daily", "weekly"]
    for label in labels:
        price_table, indicator_table = PRICE_TABLES[label]
        status["phase"] = f"{label}_price"
        _write_status(run_dir, status)
        price_result = _audit_price_table(run_dir, label, price_table, args)
        status["phase"] = f"{label}_indicator"
        _write_status(run_dir, status)
        indicator_result = _audit_indicator_table(run_dir, label, price_table, indicator_table, args)
        summary["tables"][label] = {
            "price": price_result,
            "indicator": indicator_result,
        }
        status[f"{label}_complete"] = True
        _write_status(run_dir, status)

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    status["state"] = "done"
    status["phase"] = "finished"
    status["summary_path"] = str(summary_path)
    status["finished_at"] = summary["finished_at"]
    _write_status(run_dir, status)

    print(f"Audit artifacts: {run_dir}")
    for label, table_result in summary["tables"].items():
        price = table_result["price"]
        indicator = table_result["indicator"]
        print(
            f"{label}: rows={price['rows_scanned']:,} "
            f"zero_volume={price['zero_volume_rows']:,} "
            f"flat_zero_volume={price['flat_zero_volume_rows']:,} "
            f"price_issues={price['price_issue_rows']:,} "
            f"missing_indicators={indicator['missing_indicator_rows']:,} "
            f"indicator_nulls_after_warmup={indicator['indicator_null_rows_after_warmup']:,}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
