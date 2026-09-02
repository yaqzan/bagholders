"""One-time audit for historical EarningsDate duplicates.

Finds same-symbol historical earnings rows whose dates are within 30 calendar
days, checks the current yfinance historical earnings feed for that symbol, and
deletes DB rows that are absent from yfinance when at least one sibling in the
cluster is present in yfinance.

Default is dry-run:
    python experiments/earnings_historical_duplicate_audit.py

Apply deletions:
    python experiments/earnings_historical_duplicate_audit.py --apply
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models.core import EarningsDate, EarningsJump

WINDOW_DAYS = 30


def _decimal_to_float(value):
    return None if value is None else float(value)


def _row_dict(row):
    return {
        "id": row.id,
        "symbol": row.symbol_id,
        "date": row.date.isoformat(),
        "effective_date": row.effective_date.isoformat() if row.effective_date else None,
        "call_time": row.call_time,
        "eps_estimate": _decimal_to_float(row.eps_estimate),
        "reported_eps": _decimal_to_float(row.reported_eps),
        "surprise_pct": _decimal_to_float(row.surprise_pct),
    }


def find_historical_clusters(window_days=WINDOW_DAYS, symbols=None, min_age_days=7):
    today = date.today()
    cutoff = today - timedelta(days=min_age_days)
    query = (
        EarningsDate
        .select()
        .where(EarningsDate.date <= cutoff)
        .order_by(EarningsDate.symbol, EarningsDate.date)
    )
    if symbols:
        query = query.where(EarningsDate.symbol.in_([s.upper() for s in symbols]))

    by_symbol = defaultdict(list)
    for row in query:
        by_symbol[row.symbol_id].append(row)

    clusters = []
    for sym, rows in by_symbol.items():
        current = []
        for row in rows:
            if not current:
                current = [row]
                continue
            if (row.date - current[-1].date).days <= window_days:
                current.append(row)
            else:
                if len(current) > 1:
                    clusters.append((sym, current))
                current = [row]
        if len(current) > 1:
            clusters.append((sym, current))
    return clusters


def load_yfinance_dates(symbol):
    ticker = yf.Ticker(symbol)
    earnings_dates = ticker.earnings_dates
    if earnings_dates is None or earnings_dates.empty:
        return set(), []

    dates = []
    for ts in earnings_dates.index:
        dt = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
        dates.append(dt)
    return set(dates), sorted(d.isoformat() for d in dates)


def classify_cluster(symbol, rows, yf_dates, yf_dates_iso):
    present = [row for row in rows if row.date in yf_dates]
    absent = [row for row in rows if row.date not in yf_dates]

    if not yf_dates:
        action = "skip_no_yfinance_dates"
        delete_rows = []
    elif not present:
        action = "skip_no_cluster_match"
        delete_rows = []
    elif not absent:
        action = "keep_all_yfinance_matches"
        delete_rows = []
    else:
        action = "delete_absent_siblings"
        delete_rows = absent

    return {
        "symbol": symbol,
        "action": action,
        "rows": [_row_dict(row) for row in rows],
        "yf_dates": yf_dates_iso,
        "delete_ids": [row.id for row in delete_rows],
        "delete_rows": [_row_dict(row) for row in delete_rows],
        "keep_ids": [row.id for row in rows if row.id not in {r.id for r in delete_rows}],
    }


def delete_rows(rows):
    deleted_jumps = 0
    for row in rows:
        if row.effective_date is None:
            continue
        deleted_jumps += (
            EarningsJump
            .delete()
            .where(
                (EarningsJump.symbol == row.symbol_id)
                & (EarningsJump.earnings_date == row.effective_date)
            )
            .execute()
        )
    deleted = (
        EarningsDate
        .delete()
        .where(EarningsDate.id.in_([row.id for row in rows]))
        .execute()
        if rows else 0
    )
    return deleted, deleted_jumps


def run(apply=False, symbols=None, output_dir=None, min_age_days=7):
    clusters = find_historical_clusters(symbols=symbols, min_age_days=min_age_days)
    by_symbol = defaultdict(list)
    for sym, rows in clusters:
        by_symbol[sym].append(rows)

    decisions = []
    to_delete = []
    errors = []
    for symbol in sorted(by_symbol):
        try:
            yf_dates, yf_dates_iso = load_yfinance_dates(symbol)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            continue

        for rows in by_symbol[symbol]:
            decision = classify_cluster(symbol, rows, yf_dates, yf_dates_iso)
            decisions.append(decision)
            delete_ids = set(decision["delete_ids"])
            to_delete.extend(row for row in rows if row.id in delete_ids)

    deleted = 0
    deleted_jumps = 0
    if apply and to_delete:
        deleted, deleted_jumps = delete_rows(to_delete)

    summary = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if apply else "dry_run",
        "window_days": WINDOW_DAYS,
        "min_age_days": min_age_days,
        "clusters": len(decisions),
        "symbols": len(by_symbol),
        "candidate_delete_rows": len(to_delete),
        "deleted": deleted,
        "deleted_jumps": deleted_jumps,
        "errors": errors,
        "decisions": decisions,
    }

    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Delete yfinance-absent sibling rows")
    parser.add_argument("--symbols", nargs="*", help="Optional symbol subset")
    parser.add_argument("--output-dir", help="Directory for summary.json")
    parser.add_argument("--min-age-days", type=int, default=7,
                        help="Only audit clusters whose newest row is at least this many days old")
    args = parser.parse_args()

    summary = run(
        apply=args.apply,
        symbols=args.symbols,
        output_dir=args.output_dir,
        min_age_days=args.min_age_days,
    )
    print(json.dumps({
        "mode": summary["mode"],
        "clusters": summary["clusters"],
        "symbols": summary["symbols"],
        "candidate_delete_rows": summary["candidate_delete_rows"],
        "deleted": summary["deleted"],
        "deleted_jumps": summary["deleted_jumps"],
        "errors": len(summary["errors"]),
    }, indent=2))
    for decision in summary["decisions"]:
        if decision["delete_ids"]:
            print(
                f'{decision["symbol"]}: delete {decision["delete_ids"]} '
                f'action={decision["action"]}'
            )
    if summary["errors"]:
        print("Errors:")
        for err in summary["errors"]:
            print(f'  {err["symbol"]}: {err["error"]}')


if __name__ == "__main__":
    main()
