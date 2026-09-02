#!/usr/bin/env python
"""Repair suspicious market-data rows, refresh indicators, and rescore.

This script is intentionally operational rather than exploratory:

1. Load affected symbols from a market_data_quality_audit.py artifact, or query
   current DB abnormalities directly.
2. Refetch the affected daily yfinance windows.
3. Persist only valid fetched OHLCV rows, tracking rows that changed.
4. Rebuild daily/weekly indicators for symbols whose prices changed, plus
   symbols with missing weekly indicators.
5. Force-recalculate affected score windows for the current algorithm and all
   runtime-loadable historical algorithm silos.

It writes durable JSON/CSV artifacts so the repair can be audited afterwards.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import sleep
from typing import Any

import yfinance as yf

from database.models.core import AlgorithmVersion, Stock
from database.models.technical import Indicator, PriceHistory, WeeklyIndicator, WeeklyPriceHistory
from database.trader_database import DB
from recalculate_scores import DAILY_COMPONENTS, run as recalculate_run


PRICE_EPSILON = 0.01


@dataclass
class SymbolPlan:
    symbol: str
    first_issue_date: date | None = None
    last_issue_date: date | None = None
    daily_zero_rows: int = 0
    daily_price_issue_rows: int = 0
    daily_missing_indicator_rows: int = 0
    daily_indicator_null_rows: int = 0
    weekly_zero_rows: int = 0
    weekly_price_issue_rows: int = 0
    weekly_missing_indicator_rows: int = 0
    weekly_indicator_null_rows: int = 0
    reasons: set[str] = field(default_factory=set)

    def add_range(self, first_date: date | None, last_date: date | None) -> None:
        if first_date is not None:
            self.first_issue_date = min(self.first_issue_date, first_date) if self.first_issue_date else first_date
        if last_date is not None:
            self.last_issue_date = max(self.last_issue_date, last_date) if self.last_issue_date else last_date


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _connect() -> None:
    DB.connect(reuse_if_open=True)


def _execute(sql: str, params: tuple[Any, ...] | list[Any] | None = None, retries: int = 2):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            if DB.is_closed():
                DB.connect(reuse_if_open=True)
            return DB.execute_sql(sql, params or ())
        except Exception as exc:
            last_exc = exc
            try:
                if not DB.is_closed():
                    DB.close()
            except Exception:
                pass
            if attempt < retries:
                sleep(2 + attempt)
    raise last_exc


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _is_bad_num(value: Any) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value)) or math.isinf(float(value))
    except Exception:
        return True


def _price_issue_predicate(alias: str = "ph") -> str:
    eps = PRICE_EPSILON
    return (
        f"{alias}.open <= 0 OR {alias}.high <= 0 OR {alias}.low <= 0 "
        f"OR {alias}.close <= 0 OR {alias}.volume < 0 "
        f"OR {alias}.high + {eps} < {alias}.low "
        f"OR {alias}.high + {eps} < {alias}.open "
        f"OR {alias}.high + {eps} < {alias}.close "
        f"OR {alias}.low - {eps} > {alias}.open "
        f"OR {alias}.low - {eps} > {alias}.close"
    )


def _valid_ohlcv(row: dict[str, Any]) -> tuple[bool, str | None]:
    for field_name in ("open", "high", "low", "close", "volume"):
        if _is_bad_num(row.get(field_name)):
            return False, f"{field_name}_missing_or_nan"
    opn = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    volume = float(row["volume"])
    if opn <= 0 or high <= 0 or low <= 0 or close <= 0:
        return False, "nonpositive_ohlc"
    if volume < 0:
        return False, "negative_volume"
    if high + PRICE_EPSILON < low:
        return False, "high_below_low"
    if high + PRICE_EPSILON < opn or high + PRICE_EPSILON < close:
        return False, "high_below_open_or_close"
    if low - PRICE_EPSILON > opn or low - PRICE_EPSILON > close:
        return False, "low_above_open_or_close"
    return True, None


def _same_price(existing: Any, fetched: dict[str, Any]) -> bool:
    if existing is None:
        return False
    for field_name in ("open", "high", "low", "close"):
        if abs(float(getattr(existing, field_name)) - float(fetched[field_name])) > PRICE_EPSILON:
            return False
    try:
        if int(existing.volume) != int(float(fetched["volume"])):
            return False
    except Exception:
        return False
    return True


def _value_from_row(row, field_name: str, symbol: str):
    key = (field_name, symbol)
    if key in row.index:
        return row[key]
    if field_name in row.index:
        value = row[field_name]
        try:
            if hasattr(value, "__getitem__") and symbol in value.index:
                return value[symbol]
        except Exception:
            pass
        return value
    try:
        return row[field_name][symbol]
    except Exception:
        return None


def _row_from_yf(index, row, symbol: str) -> dict[str, Any] | None:
    d = index.date() if hasattr(index, "date") else index
    out = {
        "date": d,
        "open": _value_from_row(row, "Open", symbol),
        "high": _value_from_row(row, "High", symbol),
        "low": _value_from_row(row, "Low", symbol),
        "close": _value_from_row(row, "Close", symbol),
        "volume": _value_from_row(row, "Volume", symbol),
    }
    valid, _reason = _valid_ohlcv(out)
    if not valid:
        return None
    out["volume"] = int(float(out["volume"]))
    return out


def _latest_audit_dir() -> Path | None:
    candidates: list[Path] = []
    for base in (Path(".cache") / "codex_runs", Path(".cache") / "data_quality"):
        if not base.exists():
            continue
        candidates.extend(base.glob("market_data_quality_audit_*/artifacts/*/summary.json"))
        candidates.extend(base.glob("*/summary.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime).parent


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_pre_scope_followup(audit_dir: Path | None, run_dir: Path, start_date: date | None) -> None:
    if audit_dir is None or start_date is None:
        return

    followup: dict[tuple[str, str], dict[str, Any]] = {}

    def add_row(symbol: str, reason: str, row_date: date | None) -> None:
        if not symbol or row_date is None or row_date >= start_date:
            return
        key = (symbol, reason)
        entry = followup.setdefault(
            key,
            {
                "symbol": symbol,
                "reason": reason,
                "rows": 0,
                "first_date": row_date,
                "last_date": row_date,
                "deferred_until_after": start_date,
            },
        )
        entry["rows"] += 1
        entry["first_date"] = min(entry["first_date"], row_date)
        entry["last_date"] = max(entry["last_date"], row_date)

    for filename, reason in (
        ("daily_price_issues.csv", "daily_price_issue"),
        ("daily_zero_volume_rows.csv", "daily_zero_volume"),
        ("weekly_price_issues.csv", "weekly_price_issue"),
        ("weekly_zero_volume_rows.csv", "weekly_zero_volume"),
        ("weekly_missing_indicators.csv", "weekly_missing_indicator"),
    ):
        for row in _read_csv(audit_dir / filename):
            add_row(row.get("symbol", "").strip().upper(), reason, _as_date(row.get("date")))

    rows = sorted(followup.values(), key=lambda r: (r["symbol"], r["reason"]))
    keys = ["symbol", "reason", "rows", "first_date", "last_date", "deferred_until_after"]
    with (run_dir / "pre_scope_followup.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "scope_start_date": start_date,
        "deferred_symbol_reason_pairs": len(rows),
        "deferred_rows": sum(int(r["rows"]) for r in rows),
        "deferred_symbols": len({r["symbol"] for r in rows}),
    }
    (run_dir / "pre_scope_followup_summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _completed_symbols_from_resume(path: Path | None) -> set[str]:
    if path is None:
        return set()
    csv_path = path
    if csv_path.is_dir():
        csv_path = csv_path / "affected_symbols.csv"
    completed: set[str] = set()
    for row in _read_csv(csv_path):
        symbol = row.get("symbol", "").strip().upper()
        if symbol and not row.get("error"):
            completed.add(symbol)
    return completed


def _load_plans_from_audit(audit_dir: Path | None, symbols_filter: set[str] | None) -> dict[str, SymbolPlan]:
    plans: dict[str, SymbolPlan] = {}

    def plan_for(symbol: str) -> SymbolPlan:
        plans.setdefault(symbol, SymbolPlan(symbol=symbol))
        return plans[symbol]

    if audit_dir is not None:
        for name, reason, count_field in (
            ("daily_zero_volume_by_symbol.csv", "daily_zero_volume", "daily_zero_rows"),
            ("daily_price_issues_by_symbol.csv", "daily_price_issue", "daily_price_issue_rows"),
            ("weekly_zero_volume_by_symbol.csv", "weekly_zero_volume", "weekly_zero_rows"),
            ("weekly_price_issues_by_symbol.csv", "weekly_price_issue", "weekly_price_issue_rows"),
        ):
            for row in _read_csv(audit_dir / name):
                sym = row.get("symbol", "").strip().upper()
                if not sym or (symbols_filter and sym not in symbols_filter):
                    continue
                p = plan_for(sym)
                p.reasons.add(reason)
                setattr(p, count_field, int(row.get("rows") or 0))
                p.add_range(_as_date(row.get("first_date")), _as_date(row.get("last_date")))

        for name, reason, count_field in (
            ("daily_missing_indicators.csv", "daily_missing_indicator", "daily_missing_indicator_rows"),
            ("daily_indicator_nulls_after_warmup.csv", "daily_indicator_null_after_warmup", "daily_indicator_null_rows"),
            ("weekly_missing_indicators.csv", "weekly_missing_indicator", "weekly_missing_indicator_rows"),
            ("weekly_indicator_nulls_after_warmup.csv", "weekly_indicator_null_after_warmup", "weekly_indicator_null_rows"),
        ):
            for row in _read_csv(audit_dir / name):
                sym = row.get("symbol", "").strip().upper()
                if not sym or (symbols_filter and sym not in symbols_filter):
                    continue
                d = _as_date(row.get("date"))
                p = plan_for(sym)
                p.reasons.add(reason)
                setattr(p, count_field, int(getattr(p, count_field)) + 1)
                p.add_range(d, d)

    if not plans:
        symbols = sorted(symbols_filter) if symbols_filter else [
            r[0] for r in _execute(
                "SELECT symbol FROM stocks WHERE delisted_date IS NULL ORDER BY symbol"
            ).fetchall()
        ]
        for sym in symbols:
            zero = _execute(
                """
                SELECT COUNT(*), MIN(date), MAX(date)
                FROM price_history
                WHERE symbol=%s AND volume=0
                """,
                (sym,),
            ).fetchone()
            issue = _execute(
                f"""
                SELECT COUNT(*), MIN(date), MAX(date)
                FROM price_history ph
                WHERE symbol=%s AND ({_price_issue_predicate("ph")})
                """,
                (sym,),
            ).fetchone()
            missing = _execute(
                """
                SELECT COUNT(*), MIN(wph.date), MAX(wph.date)
                FROM weekly_price_history wph
                WHERE wph.symbol=%s
                  AND NOT EXISTS (
                      SELECT 1 FROM weekly_indicators wi
                      WHERE wi.symbol=wph.symbol AND wi.date=wph.date
                  )
                """,
                (sym,),
            ).fetchone()
            daily_missing = _execute(
                """
                SELECT COUNT(*), MIN(ph.date), MAX(ph.date)
                FROM price_history ph
                WHERE ph.symbol=%s
                  AND NOT EXISTS (
                      SELECT 1 FROM indicators i
                      WHERE i.symbol=ph.symbol AND i.date=ph.date
                  )
                """,
                (sym,),
            ).fetchone()
            total = (
                int(zero[0] or 0)
                + int(issue[0] or 0)
                + int(missing[0] or 0)
                + int(daily_missing[0] or 0)
            )
            if total == 0:
                continue
            p = plan_for(sym)
            if int(zero[0] or 0):
                p.reasons.add("daily_zero_volume")
                p.daily_zero_rows = int(zero[0])
                p.add_range(zero[1], zero[2])
            if int(issue[0] or 0):
                p.reasons.add("daily_price_issue")
                p.daily_price_issue_rows = int(issue[0])
                p.add_range(issue[1], issue[2])
            if int(missing[0] or 0):
                p.reasons.add("weekly_missing_indicator")
                p.weekly_missing_indicator_rows = int(missing[0])
                p.add_range(missing[1], missing[2])
            if int(daily_missing[0] or 0):
                p.reasons.add("daily_missing_indicator")
                p.daily_missing_indicator_rows = int(daily_missing[0])
                p.add_range(daily_missing[1], daily_missing[2])
    return plans


def _fetch_prices(symbol: str, start_date: date, end_date: date, attempts: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fetched: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    dl_kwargs = dict(
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
        interval="1d",
        rounding=True,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    data = None
    last_error = None
    for attempt in range(attempts):
        try:
            data = yf.download(symbol, **dl_kwargs)
            if data is not None and not data.empty:
                break
        except Exception as exc:
            last_error = str(exc)
        sleep(1 + attempt)
    if data is None or data.empty:
        invalid.append({"symbol": symbol, "date": None, "reason": f"fetch_empty:{last_error or 'no_data'}"})
        return fetched, invalid
    for index, row in data.iterrows():
        raw = {
            "date": index.date() if hasattr(index, "date") else index,
            "open": _value_from_row(row, "Open", symbol),
            "high": _value_from_row(row, "High", symbol),
            "low": _value_from_row(row, "Low", symbol),
            "close": _value_from_row(row, "Close", symbol),
            "volume": _value_from_row(row, "Volume", symbol),
        }
        valid, reason = _valid_ohlcv(raw)
        if not valid:
            invalid.append({"symbol": symbol, "date": raw["date"], "reason": reason})
            continue
        raw["volume"] = int(float(raw["volume"]))
        fetched.append(raw)
    return fetched, invalid


def _existing_prices(symbol: str, start_date: date, end_date: date) -> dict[date, Any]:
    rows = PriceHistory.select().where(
        (PriceHistory.symbol == symbol)
        & (PriceHistory.date >= start_date)
        & (PriceHistory.date <= end_date)
    )
    return {r.date: r for r in rows}


def _runtime_score_tokens() -> tuple[list[str], list[dict[str, str]]]:
    from algorithm_versions.runtime import validate_runtime_versions

    current = AlgorithmVersion.get_or_create_current()
    usable: list[str] = []
    skipped: list[dict[str, str]] = []
    for version in AlgorithmVersion.production_versions():
        token = version.production_label
        if int(version.id) == int(current.id):
            continue
        try:
            validate_runtime_versions([token])
            usable.append(token)
        except Exception as exc:
            skipped.append({"version": token, "reason": str(exc)})
    return usable, skipped


def _write_status(run_dir: Path, status: dict[str, Any]) -> None:
    status["updated_at"] = datetime.now().isoformat(timespec="seconds")
    (run_dir / "status.json").write_text(
        json.dumps(status, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _write_symbol_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = [
        "symbol",
        "first_issue_date",
        "last_issue_date",
        "fetch_start",
        "fetch_end",
        "recalc_days",
        "reasons",
        "daily_missing_indicator_rows",
        "daily_indicator_null_rows",
        "weekly_zero_rows",
        "weekly_price_issue_rows",
        "weekly_missing_indicator_rows",
        "weekly_indicator_null_rows",
        "fetched_rows",
        "changed_rows",
        "written_rows",
        "daily_indicators_rebuilt",
        "weekly_indicators_rebuilt",
        "recalculated",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill bad market data and recalculate affected scores.")
    parser.add_argument("--audit-dir", type=Path, help="Audit artifact directory. Defaults to latest summary artifact.")
    parser.add_argument("--output-dir", type=Path, help="Run artifact directory. Defaults to .codex/runs/market_data_backfill_TIMESTAMP.")
    parser.add_argument("--symbol", action="append", help="Restrict to one symbol. Repeatable.")
    parser.add_argument("--max-symbols", type=int, help="Process only the first N planned symbols.")
    parser.add_argument("--start-date", type=_as_date, help="Clamp repair/recalculation to issues on or after this date.")
    parser.add_argument("--end-date", type=_as_date, help="Clamp repair/recalculation to issues on or before this date.")
    parser.add_argument("--buffer-days", type=int, default=5, help="Extra calendar days around affected price windows to refetch.")
    parser.add_argument("--fetch-attempts", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and compare, but do not write DB rows or recalculate.")
    parser.add_argument("--no-recalculate", action="store_true", help="Repair prices/indicators but skip score recalculation.")
    parser.add_argument("--workers", type=int, default=1, help="Workers passed to recalculate_scores.run per symbol. Use 0 for auto.")
    parser.add_argument("--include-unchanged-recalc", action="store_true", help="Recalculate even if refetched prices did not change.")
    parser.add_argument("--force-recalc-symbol", action="append", help="Force recalculation for a specific symbol even if refetched rows compare unchanged. Repeatable.")
    parser.add_argument("--resume-from", type=Path, help="Prior run directory or affected_symbols.csv whose successful symbols should be skipped.")
    parser.add_argument("--stop-file", type=Path, help="If this file exists before a new symbol starts, checkpoint and exit paused.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _connect()
    audit_dir = args.audit_dir or _latest_audit_dir()
    symbols_filter = {s.upper() for s in args.symbol} if args.symbol else None
    force_recalc_symbols = {s.upper() for s in args.force_recalc_symbol} if args.force_recalc_symbol else set()
    run_dir = args.output_dir or Path(".codex") / "runs" / f"market_data_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=False)

    plans = _load_plans_from_audit(audit_dir, symbols_filter)
    _write_pre_scope_followup(audit_dir, run_dir, args.start_date)
    if args.start_date or args.end_date:
        scoped_plans: dict[str, SymbolPlan] = {}
        for sym, plan in plans.items():
            first_issue = plan.first_issue_date
            last_issue = plan.last_issue_date
            if args.start_date and last_issue and last_issue < args.start_date:
                continue
            if args.end_date and first_issue and first_issue > args.end_date:
                continue
            scoped = SymbolPlan(
                symbol=plan.symbol,
                first_issue_date=first_issue,
                last_issue_date=last_issue,
                daily_zero_rows=plan.daily_zero_rows,
                daily_price_issue_rows=plan.daily_price_issue_rows,
                weekly_missing_indicator_rows=plan.weekly_missing_indicator_rows,
                reasons=set(plan.reasons),
            )
            if args.start_date and (scoped.first_issue_date is None or scoped.first_issue_date < args.start_date):
                scoped.first_issue_date = args.start_date
            if args.end_date and (scoped.last_issue_date is None or scoped.last_issue_date > args.end_date):
                scoped.last_issue_date = args.end_date
            scoped_plans[sym] = scoped
        plans = scoped_plans
    ordered_plans = sorted(plans.values(), key=lambda p: (p.first_issue_date or date.max, p.symbol))
    resume_completed_symbols = _completed_symbols_from_resume(args.resume_from)
    if resume_completed_symbols:
        ordered_plans = [p for p in ordered_plans if p.symbol not in resume_completed_symbols]
    if args.max_symbols:
        ordered_plans = ordered_plans[: args.max_symbols]

    sidecar_tokens, skipped_versions = _runtime_score_tokens()
    status = {
        "state": "running",
        "phase": "starting",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": None,
        "audit_dir": str(audit_dir) if audit_dir else None,
        "run_dir": str(run_dir),
        "total_symbols": len(ordered_plans),
        "completed_symbols": 0,
        "current_symbol": None,
        "runtime_sidecar_versions": sidecar_tokens,
        "skipped_versions": skipped_versions,
        "scope_start_date": args.start_date,
        "scope_end_date": args.end_date,
        "resume_from": str(args.resume_from) if args.resume_from else None,
        "resume_completed_symbols": len(resume_completed_symbols),
        "force_recalc_symbols": sorted(force_recalc_symbols),
        "stop_file": str(args.stop_file) if args.stop_file else None,
        "dry_run": args.dry_run,
        "no_recalculate": args.no_recalculate,
    }
    _write_status(run_dir, status)
    (run_dir / "skipped_versions.json").write_text(
        json.dumps(skipped_versions, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (run_dir / "plans.json").write_text(
        json.dumps([p.__dict__ for p in ordered_plans], indent=2, default=_json_default),
        encoding="utf-8",
    )

    symbol_results: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    paused = False

    for idx, plan in enumerate(ordered_plans, start=1):
        if args.stop_file and args.stop_file.exists():
            paused = True
            status["state"] = "paused"
            status["phase"] = "paused_before_symbol"
            status["pause_requested_by"] = str(args.stop_file)
            status["completed_symbols"] = idx - 1
            status["current_symbol"] = None
            status["paused_at"] = datetime.now().isoformat(timespec="seconds")
            _write_status(run_dir, status)
            break
        status["phase"] = "symbol"
        status["current_symbol"] = plan.symbol
        status["completed_symbols"] = idx - 1
        _write_status(run_dir, status)
        result: dict[str, Any] = {
            "symbol": plan.symbol,
            "first_issue_date": plan.first_issue_date,
            "last_issue_date": plan.last_issue_date,
            "reasons": ",".join(sorted(plan.reasons)),
            "daily_missing_indicator_rows": plan.daily_missing_indicator_rows,
            "daily_indicator_null_rows": plan.daily_indicator_null_rows,
            "weekly_zero_rows": plan.weekly_zero_rows,
            "weekly_price_issue_rows": plan.weekly_price_issue_rows,
            "weekly_missing_indicator_rows": plan.weekly_missing_indicator_rows,
            "weekly_indicator_null_rows": plan.weekly_indicator_null_rows,
            "fetched_rows": 0,
            "changed_rows": 0,
            "written_rows": 0,
            "daily_indicators_rebuilt": False,
            "weekly_indicators_rebuilt": False,
            "recalculated": False,
            "error": "",
        }
        try:
            stock = Stock.get_or_none(Stock.symbol == plan.symbol)
            if stock is None:
                result["error"] = "stock_not_found"
                symbol_results.append(result)
                continue

            first_issue = plan.first_issue_date or date.today()
            last_issue = plan.last_issue_date or date.today()
            fetch_start = max(date(1900, 1, 1), first_issue - timedelta(days=args.buffer_days))
            fetch_end = min(date.today(), last_issue + timedelta(days=args.buffer_days))
            result["fetch_start"] = fetch_start
            result["fetch_end"] = fetch_end
            result["recalc_days"] = max(1, (date.today() - fetch_start).days + 1)

            status["phase"] = "fetch_prices"
            _write_status(run_dir, status)
            fetched_rows, invalid = _fetch_prices(plan.symbol, fetch_start, fetch_end, args.fetch_attempts)
            invalid_rows.extend(invalid)
            result["fetched_rows"] = len(fetched_rows)
            status["phase"] = "compare_prices"
            _write_status(run_dir, status)
            existing = _existing_prices(plan.symbol, fetch_start, fetch_end)
            changed_rows = [r for r in fetched_rows if not _same_price(existing.get(r["date"]), r)]
            result["changed_rows"] = len(changed_rows)

            should_rebuild_indicators = (
                bool(changed_rows)
                or bool(plan.daily_missing_indicator_rows)
                or bool(plan.daily_indicator_null_rows)
                or bool(plan.weekly_zero_rows)
                or bool(plan.weekly_price_issue_rows)
                or bool(plan.weekly_missing_indicator_rows)
                or bool(plan.weekly_indicator_null_rows)
            )
            should_recalculate = should_rebuild_indicators or args.include_unchanged_recalc or plan.symbol in force_recalc_symbols

            if not args.dry_run and fetched_rows:
                status["phase"] = "write_prices"
                _write_status(run_dir, status)
                written, _weekly_written = PriceHistory.bulk_build(plan.symbol, fetched_rows)
                result["written_rows"] = written

            if not args.dry_run and should_rebuild_indicators:
                status["phase"] = "rebuild_indicators"
                _write_status(run_dir, status)
                stock.calculate_indicators(full=True, silent=True)
                stock.calculate_indicators(full=True, weekly=True, silent=True)
                result["daily_indicators_rebuilt"] = True
                result["weekly_indicators_rebuilt"] = True

            if not args.dry_run and should_recalculate and not args.no_recalculate:
                status["phase"] = "recalculate_scores"
                _write_status(run_dir, status)
                recalculate_run(
                    plan.symbol,
                    int(result["recalc_days"]),
                    DAILY_COMPONENTS.copy(),
                    workers=None if args.workers == 0 else args.workers,
                    use_batch=True,
                    force=True,
                    score_versions=sidecar_tokens,
                )
                result["recalculated"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            (run_dir / f"{plan.symbol}_error.txt").write_text(
                traceback.format_exc(),
                encoding="utf-8",
            )
        symbol_results.append(result)
        _write_symbol_csv(run_dir / "affected_symbols.csv", symbol_results)
        (run_dir / "invalid_fetched_rows.json").write_text(
            json.dumps(invalid_rows, indent=2, default=_json_default),
            encoding="utf-8",
        )

    status["state"] = "paused" if paused else "done"
    status["phase"] = "paused" if paused else "finished"
    status["completed_symbols"] = len(symbol_results)
    status["current_symbol"] = None
    status["finished_at" if not paused else "paused_at"] = datetime.now().isoformat(timespec="seconds")
    _write_status(run_dir, status)
    summary = {
        "state": "paused" if paused else "done",
        "run_dir": str(run_dir),
        "audit_dir": str(audit_dir) if audit_dir else None,
        "symbols_planned": len(ordered_plans),
        "symbols_skipped_from_resume": len(resume_completed_symbols),
        "symbols_with_changed_rows": sum(1 for r in symbol_results if int(r.get("changed_rows") or 0) > 0),
        "symbols_recalculated": sum(1 for r in symbol_results if r.get("recalculated")),
        "changed_rows": sum(int(r.get("changed_rows") or 0) for r in symbol_results),
        "written_rows": sum(int(r.get("written_rows") or 0) for r in symbol_results),
        "invalid_fetched_rows": len(invalid_rows),
        "errors": [r for r in symbol_results if r.get("error")],
        "runtime_sidecar_versions": sidecar_tokens,
        "skipped_versions": skipped_versions,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default),
        encoding="utf-8",
    )
    if paused:
        (run_dir / "paused.json").write_text(
            json.dumps(
                {
                    "paused_at": status.get("paused_at"),
                    "run_dir": str(run_dir),
                    "affected_symbols": str(run_dir / "affected_symbols.csv"),
                    "resume_hint": "Relaunch with --resume-from this run directory.",
                },
                indent=2,
                default=_json_default,
            ),
            encoding="utf-8",
        )
    print(json.dumps(summary, indent=2, default=_json_default))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise
