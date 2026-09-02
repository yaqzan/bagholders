"""
Backfill the `earnings_jumps` cache table (Tier B: realized-move only).

For every `EarningsDate` row that has `effective_date` populated, computes
   jump_pct = |close_{eff_date} / close_{eff_date − 1trd} − 1| × 100
from PriceHistory and upserts into `earnings_jumps`.

The 15-DTE earnings-aware premium formula at runtime aggregates a stock's
recent rows (median of last N events, require N>=3) into a per-stock j_i.
Falls back to the universe constant `EARN_JUMP_PCT` (9.3%) when insufficient
history exists.

Usage:
    python backfill_earnings_jumps.py            # full backfill (all events)
    python backfill_earnings_jumps.py --since 2024-01-01
    python backfill_earnings_jumps.py --symbols AAPL,MSFT
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys
from collections import defaultdict
from datetime import date as DateType, datetime
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.trader_database import DB
from database.models.core import EarningsDate, EarningsJump


CHUNK = 60   # symbols per price_history query batch
BATCH = 500  # rows per upsert


def _parse_date(s: str | None) -> DateType | None:
    if not s:
        return None
    return datetime.strptime(s, '%Y-%m-%d').date()


def _load_events(since: DateType | None, symbols: list[str] | None) -> list:
    q = (EarningsDate
         .select(EarningsDate.symbol, EarningsDate.effective_date)
         .where(EarningsDate.effective_date.is_null(False)))
    if since is not None:
        q = q.where(EarningsDate.effective_date >= since)
    if symbols:
        q = q.where(EarningsDate.symbol.in_(symbols))
    return list(q.order_by(EarningsDate.symbol, EarningsDate.effective_date))


def _load_ph(symbols: list[str], earliest: DateType | None) -> dict:
    """Returns {symbol: [(date, close), ...]} sorted by date asc."""
    if not symbols:
        return {}
    out: dict[str, list[tuple]] = defaultdict(list)
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i+CHUNK]
        ph = ','.join(['%s'] * len(chunk))
        sql = f"SELECT symbol, date, close FROM price_history WHERE symbol IN ({ph})"
        params: list = list(chunk)
        if earliest is not None:
            sql += " AND date >= %s"
            params.append(earliest)
        sql += " ORDER BY symbol, date"
        cur = DB.execute_sql(sql, params)
        for sym, d, c in cur.fetchall():
            out[sym].append((d, float(c)))
    return dict(out)


def _compute_jump(events: list, ph_by_sym: dict) -> tuple[list[dict], int, int]:
    """Returns (rows_to_upsert, skipped_no_data, skipped_zero_close)."""
    upserts: list[dict] = []
    skipped_no_data = 0
    skipped_zero_close = 0
    now = datetime.now()
    for evt in events:
        sym = evt.symbol_id
        eff = evt.effective_date
        rows = ph_by_sym.get(sym)
        if not rows:
            skipped_no_data += 1
            continue
        dates = [r[0] for r in rows]
        idx = bisect.bisect_left(dates, eff)
        # Need: row exists AT effective_date AND a prior row exists.
        if idx >= len(rows) or dates[idx] != eff or idx == 0:
            skipped_no_data += 1
            continue
        prev_close = rows[idx-1][1]
        eff_close  = rows[idx][1]
        if prev_close <= 0 or eff_close <= 0:
            skipped_zero_close += 1
            continue
        jump_pct = abs(eff_close / prev_close - 1.0) * 100.0
        upserts.append(dict(
            symbol=sym,
            earnings_date=eff,
            jump_pct=jump_pct,
            source='realized_move',
            iv_used=None,
            sigma_used=None,
            computed_at=now,
        ))
    return upserts, skipped_no_data, skipped_zero_close


def _upsert(rows: list[dict]) -> None:
    if not rows:
        return
    with DB.atomic():
        for i in range(0, len(rows), BATCH):
            EarningsJump.insert_many(rows[i:i+BATCH]).on_conflict_replace().execute()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='ISO date — only process events on/after this effective_date')
    ap.add_argument('--symbols', help='Comma-separated symbols (default: all)')
    ap.add_argument('--dry-run', action='store_true', help="Don't write")
    args = ap.parse_args()

    since = _parse_date(args.since)
    symbols = [s.strip() for s in args.symbols.split(',')] if args.symbols else None

    DB.connect(reuse_if_open=True)
    EarningsJump.ensure_schema()
    print(f"Loading earnings events  since={since or 'beginning'}  symbols={'all' if not symbols else len(symbols)}")
    events = _load_events(since, symbols)
    print(f"  {len(events):,} events with effective_date set")
    if not events:
        return

    syms = sorted({e.symbol_id for e in events})
    earliest = min(e.effective_date for e in events)
    print(f"Loading price_history for {len(syms):,} symbols since {earliest} …")
    ph_by_sym = _load_ph(syms, earliest)
    n_ph_rows = sum(len(v) for v in ph_by_sym.values())
    print(f"  {n_ph_rows:,} price_history rows across {len(ph_by_sym):,} symbols")

    print("Computing jumps …")
    upserts, skipped_no_data, skipped_zero_close = _compute_jump(events, ph_by_sym)
    print(f"  computed: {len(upserts):,}  skipped(no_data): {skipped_no_data:,}  skipped(zero_close): {skipped_zero_close:,}")

    # Quick distribution sanity-check
    if upserts:
        jp = sorted(r['jump_pct'] for r in upserts)
        n = len(jp)
        p50 = jp[n // 2]
        p10 = jp[max(0, n // 10)]
        p90 = jp[min(n - 1, 9 * n // 10)]
        p99 = jp[min(n - 1, 99 * n // 100)]
        print(f"  jump_pct distribution: p10={p10:.2f}% p50={p50:.2f}% p90={p90:.2f}% p99={p99:.2f}%")

    if args.dry_run:
        print("DRY RUN — no DB writes")
    else:
        print("Upserting …")
        _upsert(upserts)
        print(f"Done. {len(upserts):,} rows written to earnings_jumps.")

    DB.close()


if __name__ == '__main__':
    main()
