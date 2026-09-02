"""S&P 500 historical-removal manifest and metadata import.

This is a lightweight survivorship-bias helper for legacy OOS experiments. It
does not pull prices or calculate scores. The optional DB write only inserts
missing `Stock` metadata rows from the Wikipedia S&P 500 changes table and sets
`delisted_date` to the S&P removal date so the live updater skips them.

Important: an S&P removal date is not always an exchange delisting date. Use the
generated CSV as the point-in-time membership source; treat `Stock.delisted_date`
here as a conservative "historical-only/no live updates" marker for imported
legacy symbols.

Usage:
    python experiments/legacy_oos/sp500_wikipedia_import.py
    python experiments/legacy_oos/sp500_wikipedia_import.py --apply
    python experiments/legacy_oos/sp500_wikipedia_import.py --start 2015-01-01 --end 2020-01-01 --apply
"""
from __future__ import annotations

import argparse
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / ".cache" / "legacy_oos" / "sp500"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class RemovalEvent:
    removed_at: date
    symbol: str
    name: str
    added_symbol: str | None
    added_name: str | None
    reason: str


def normalize_symbol(symbol: object) -> str:
    """Normalize index tickers to yfinance/Trader style."""
    if symbol is None or pd.isna(symbol):
        return ""
    return str(symbol).strip().upper().replace(".", "-")


def parse_date(value: object) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [
            "_".join(str(part).strip().lower() for part in col if str(part) != "nan")
            for col in out.columns
        ]
    else:
        out.columns = [str(col).strip().lower() for col in out.columns]
    return out


def _pick_column(columns: Iterable[str], *needles: str) -> str:
    cols = list(columns)
    for needle in needles:
        for col in cols:
            if needle in col:
                return col
    raise ValueError(f"Could not find any of {needles!r} in columns={cols!r}")


def fetch_wikipedia_tables(url: str = SP500_URL) -> list[pd.DataFrame]:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def find_changes_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    for table in tables:
        flat = _flatten_columns(table)
        columns = set(flat.columns)
        if (
            any("effective date" in col for col in columns)
            and any("added" in col and "ticker" in col for col in columns)
            and any("removed" in col and "ticker" in col for col in columns)
        ):
            return flat
    raise ValueError("Could not find S&P 500 changes table")


def parse_removal_events(
    changes_table: pd.DataFrame,
    start: date,
    end: date,
) -> list[RemovalEvent]:
    effective_col = _pick_column(changes_table.columns, "effective date")
    added_symbol_col = _pick_column(changes_table.columns, "added_ticker", "added ticker")
    added_name_col = _pick_column(changes_table.columns, "added_security", "added security")
    removed_symbol_col = _pick_column(
        changes_table.columns,
        "removed_ticker",
        "removed ticker",
    )
    removed_name_col = _pick_column(changes_table.columns, "removed_security", "removed security")
    reason_col = _pick_column(changes_table.columns, "reason")

    events: list[RemovalEvent] = []
    for row in changes_table.to_dict("records"):
        removed_at = parse_date(row.get(effective_col))
        symbol = normalize_symbol(row.get(removed_symbol_col))
        if not removed_at or not symbol or not (start <= removed_at < end):
            continue

        events.append(
            RemovalEvent(
                removed_at=removed_at,
                symbol=symbol,
                name=str(row.get(removed_name_col) or "").strip(),
                added_symbol=normalize_symbol(row.get(added_symbol_col)) or None,
                added_name=str(row.get(added_name_col) or "").strip() or None,
                reason=str(row.get(reason_col) or "").strip(),
            )
        )
    events.sort(key=lambda e: (e.removed_at, e.symbol))
    return events


def dedupe_for_stock_import(events: Iterable[RemovalEvent]) -> list[RemovalEvent]:
    """Collapse repeated symbols to the earliest removal in the requested window."""
    by_symbol: dict[str, RemovalEvent] = {}
    for event in events:
        prior = by_symbol.get(event.symbol)
        if prior is None or event.removed_at < prior.removed_at:
            by_symbol[event.symbol] = event
    return sorted(by_symbol.values(), key=lambda e: (e.removed_at, e.symbol))


def _events_frame(events: Iterable[RemovalEvent], existing_symbols: set[str]) -> pd.DataFrame:
    rows = []
    for event in events:
        rows.append(
            {
                "removed_at": event.removed_at.isoformat(),
                "symbol": event.symbol,
                "name": event.name,
                "added_symbol": event.added_symbol,
                "added_name": event.added_name,
                "reason": event.reason,
                "exists_in_stock": event.symbol in existing_symbols,
            }
        )
    return pd.DataFrame(rows)


def _load_existing_symbols() -> set[str]:
    from database import Stock
    from database.trader_database import DB

    DB.connect(reuse_if_open=True)
    return {s.symbol for s in Stock.select(Stock.symbol)}


def apply_missing_stock_rows(events: Iterable[RemovalEvent], mark_existing: bool = False) -> dict[str, int]:
    from database import Stock
    from database.trader_database import DB

    DB.connect(reuse_if_open=True)
    Stock.ensure_schema()
    inserted = 0
    updated_existing = 0
    skipped_existing = 0

    with DB.atomic():
        for event in dedupe_for_stock_import(events):
            stock = Stock.get_or_none(Stock.symbol == event.symbol)
            if stock is None:
                Stock.create(
                    symbol=event.symbol,
                    name=event.name or event.symbol,
                    delisted_date=event.removed_at,
                )
                inserted += 1
                continue

            if mark_existing and stock.delisted_date is None:
                stock.delisted_date = event.removed_at
                if event.name and not stock.name:
                    stock.name = event.name
                stock.save()
                updated_existing += 1
            else:
                skipped_existing += 1

    return {
        "inserted": inserted,
        "updated_existing": updated_existing,
        "skipped_existing": skipped_existing,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2015-01-01", help="Inclusive start date")
    parser.add_argument("--end", default="2020-01-01", help="Exclusive end date")
    parser.add_argument("--apply", action="store_true", help="Insert missing Stock rows")
    parser.add_argument(
        "--mark-existing",
        action="store_true",
        help=(
            "Also set Stock.delisted_date for existing rows with no delisted_date. "
            "Use carefully: S&P removals are not always true delistings."
        ),
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Do not write the manifest CSV under .cache/legacy_oos/sp500",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if end <= start:
        raise SystemExit("--end must be after --start")

    tables = fetch_wikipedia_tables()
    events = parse_removal_events(find_changes_table(tables), start, end)
    unique_events = dedupe_for_stock_import(events)
    existing_symbols = _load_existing_symbols()
    missing = [event for event in unique_events if event.symbol not in existing_symbols]

    if not args.no_csv:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = CACHE_DIR / f"sp500_removed_{start.isoformat()}_{end.isoformat()}.csv"
        _events_frame(unique_events, existing_symbols).to_csv(csv_path, index=False)
        print(f"[sp500] wrote {csv_path.relative_to(ROOT)}")

    print(f"[sp500] removal rows: {len(events)}")
    print(f"[sp500] unique removed symbols: {len(unique_events)}")
    print(f"[sp500] missing from stocks: {len(missing)}")
    if missing:
        preview = ", ".join(event.symbol for event in missing[:30])
        suffix = " ..." if len(missing) > 30 else ""
        print(f"[sp500] missing preview: {preview}{suffix}")

    if args.apply:
        result = apply_missing_stock_rows(unique_events, mark_existing=args.mark_existing)
        print(
            "[sp500] applied: "
            f"inserted={result['inserted']} "
            f"updated_existing={result['updated_existing']} "
            f"skipped_existing={result['skipped_existing']}"
        )
    else:
        print("[sp500] dry run only; rerun with --apply to insert missing Stock rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
