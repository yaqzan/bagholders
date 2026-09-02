#! python3
"""Remove rows dated after a stock's historical/delisted cutoff.

The stock table's ``delisted_date`` is used in this project as the final valid
market-data date for historical-only symbols. Rows after that date are almost
always ticker-reuse or stale-source contamination.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from database.trader_database import DB


ROOT = Path(__file__).resolve().parents[1]
BARRIER_DB = ROOT / ".cache" / "barrier_outcomes.db"


MYSQL_TABLES = [
    ("scores", "date"),
    ("weekly_scores", "date"),
    ("staging_scores", "date"),
    ("score_assessment_option_results", "peak_date"),
    ("historic_peaks", "peak_date"),
    ("dte_recommendations", "date"),
    ("earnings_jumps", "earnings_date"),
    ("earnings_dates", "date"),
    ("split_events", "split_date"),
    ("indicators", "date"),
    ("weekly_indicators", "date"),
    ("price_history", "date"),
    ("weekly_price_history", "date"),
    ("historicals", "date"),
]


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _mysql_count(table: str, date_col: str) -> int:
    sql = (
        f"SELECT COUNT(*) FROM `{table}` t "
        "JOIN `stocks` s ON s.`symbol` = t.`symbol` "
        "WHERE s.`delisted_date` IS NOT NULL "
        f"AND t.`{date_col}` > s.`delisted_date`"
    )
    return int(DB.execute_sql(sql).fetchone()[0])


def _mysql_delete(
    table: str,
    date_col: str,
    symbols: list[tuple[str, date]],
    chunk_size: int,
) -> int:
    sql = (
        f"DELETE FROM `{table}` WHERE `symbol`=%s AND `{date_col}` > %s "
        f"ORDER BY `{date_col}` LIMIT {int(chunk_size)}"
    )
    deleted = 0
    for symbol, cutoff in symbols:
        while True:
            cur = DB.execute_sql(sql, (symbol, cutoff))
            n = int(getattr(cur, "rowcount", 0) or 0)
            deleted += n
            if n < chunk_size:
                break
    return deleted


def _affected_symbols(limit: int = 50) -> list[dict]:
    sql = """
        SELECT
            s.symbol,
            s.delisted_date,
            COUNT(sc.symbol) AS score_rows,
            MIN(sc.date) AS first_score_date,
            MAX(sc.date) AS last_score_date
        FROM stocks s
        JOIN scores sc ON sc.symbol = s.symbol
        WHERE s.delisted_date IS NOT NULL
          AND sc.date > s.delisted_date
        GROUP BY s.symbol, s.delisted_date
        ORDER BY score_rows DESC, s.symbol
        LIMIT %s
    """
    rows = DB.execute_sql(sql, (limit,)).fetchall()
    return [
        {
            "symbol": symbol,
            "delisted_date": delisted_date,
            "score_rows": int(score_rows),
            "first_score_date": first_score_date,
            "last_score_date": last_score_date,
        }
        for symbol, delisted_date, score_rows, first_score_date, last_score_date in rows
    ]


def _delisted_symbols() -> list[tuple[str, date]]:
    rows = DB.execute_sql(
        "SELECT `symbol`, `delisted_date` FROM `stocks` "
        "WHERE `delisted_date` IS NOT NULL ORDER BY `symbol`"
    ).fetchall()
    return [(symbol, cutoff) for symbol, cutoff in rows]


def _barrier_counts(symbols: list[tuple[str, date]], window_days: int) -> dict:
    result = {
        "path": str(BARRIER_DB),
        "exists": BARRIER_DB.exists(),
        "direct_post_cutoff_rows": 0,
        "horizon_invalidated_rows": 0,
        "window_days": window_days,
    }
    if not BARRIER_DB.exists():
        return result

    conn = sqlite3.connect(str(BARRIER_DB), timeout=120)
    try:
        cur = conn.cursor()
        for symbol, cutoff in symbols:
            direct = cur.execute(
                "SELECT COUNT(*) FROM barrier_outcomes WHERE symbol=? AND date > ?",
                (symbol, cutoff.isoformat()),
            ).fetchone()[0]
            horizon_cutoff = cutoff - timedelta(days=window_days)
            horizon = cur.execute(
                "SELECT COUNT(*) FROM barrier_outcomes WHERE symbol=? AND date > ?",
                (symbol, horizon_cutoff.isoformat()),
            ).fetchone()[0]
            result["direct_post_cutoff_rows"] += int(direct)
            result["horizon_invalidated_rows"] += int(horizon)
    finally:
        conn.close()
    return result


def _barrier_delete(symbols: list[tuple[str, date]], window_days: int) -> int:
    if not BARRIER_DB.exists():
        return 0
    conn = sqlite3.connect(str(BARRIER_DB), timeout=120)
    try:
        cur = conn.cursor()
        deleted = 0
        for symbol, cutoff in symbols:
            horizon_cutoff = cutoff - timedelta(days=window_days)
            cur.execute(
                "DELETE FROM barrier_outcomes WHERE symbol=? AND date > ?",
                (symbol, horizon_cutoff.isoformat()),
            )
            deleted += int(cur.rowcount or 0)
        conn.commit()
        return deleted
    finally:
        conn.close()


def _rebuild_duck_mirror() -> str | None:
    if not BARRIER_DB.exists():
        return None
    from database.barrier_cache import rebuild_duck_mirror

    return str(rebuild_duck_mirror(verbose=True))


def run(
    *,
    apply: bool,
    report_path: Path | None,
    rebuild_duck: bool,
    barrier_window_days: int,
    mysql_chunk_size: int,
    mysql_timeout: int,
) -> dict:
    if not DB.is_closed():
        DB.close()
    DB.connect_params["read_timeout"] = max(
        int(DB.connect_params.get("read_timeout") or 0),
        mysql_timeout,
    )
    DB.connect_params["write_timeout"] = max(
        int(DB.connect_params.get("write_timeout") or 0),
        mysql_timeout,
    )
    DB.connect(reuse_if_open=True)
    started_at = datetime.now()

    before_symbols = _affected_symbols()
    symbols = _delisted_symbols()
    mysql = []
    total_before = 0
    total_deleted = 0
    for table, date_col in MYSQL_TABLES:
        before = _mysql_count(table, date_col)
        deleted = _mysql_delete(table, date_col, symbols, mysql_chunk_size) if apply and before else 0
        after = _mysql_count(table, date_col)
        print(
            f"{table}: before={before:,} deleted={deleted:,} after={after:,}",
            flush=True,
        )
        total_before += before
        total_deleted += deleted
        mysql.append(
            {
                "table": table,
                "date_column": date_col,
                "before": before,
                "deleted": deleted,
                "after": after,
            }
        )

    barrier_before = _barrier_counts(symbols, barrier_window_days)
    print(
        "barrier: direct_before="
        f"{barrier_before['direct_post_cutoff_rows']:,} "
        "horizon_before="
        f"{barrier_before['horizon_invalidated_rows']:,}",
        flush=True,
    )
    barrier_deleted = _barrier_delete(symbols, barrier_window_days) if apply else 0
    barrier_after = _barrier_counts(symbols, barrier_window_days)
    print(
        f"barrier: deleted={barrier_deleted:,} "
        f"direct_after={barrier_after['direct_post_cutoff_rows']:,} "
        f"horizon_after={barrier_after['horizon_invalidated_rows']:,}",
        flush=True,
    )
    duck_mirror = _rebuild_duck_mirror() if apply and rebuild_duck and barrier_deleted else None

    report = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "apply": apply,
        "mysql_total_before": total_before,
        "mysql_total_deleted": total_deleted,
        "mysql": mysql,
        "affected_score_symbols_before": before_symbols,
        "barrier_before": barrier_before,
        "barrier_deleted": barrier_deleted,
        "barrier_after": barrier_after,
        "duck_mirror": duck_mirror,
        "mysql_chunk_size": mysql_chunk_size,
        "mysql_timeout": mysql_timeout,
    }

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete rows; default is dry-run")
    parser.add_argument("--report", type=Path, help="write JSON report to this path")
    parser.add_argument(
        "--skip-duck-rebuild",
        action="store_true",
        help="do not rebuild the DuckDB barrier mirror after deleting SQLite rows",
    )
    parser.add_argument(
        "--barrier-window-days",
        type=int,
        default=90,
        help="also invalidate barrier rows this many days before cutoff because horizons cross the cutoff",
    )
    parser.add_argument(
        "--mysql-chunk-size",
        type=int,
        default=500,
        help="maximum rows per MySQL DELETE statement",
    )
    parser.add_argument(
        "--mysql-timeout",
        type=int,
        default=600,
        help="temporary MySQL read/write timeout for this cleanup process",
    )
    args = parser.parse_args()
    report = run(
        apply=args.apply,
        report_path=args.report,
        rebuild_duck=not args.skip_duck_rebuild,
        barrier_window_days=args.barrier_window_days,
        mysql_chunk_size=args.mysql_chunk_size,
        mysql_timeout=args.mysql_timeout,
    )
    print(json.dumps(report, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
