"""
Runtime accessor for the `earnings_jumps` cache table.

For a list of symbols, returns the per-stock earnings-jump magnitude (`j_pct`)
to use in the variance-additive 15-DTE premium formula. Aggregation rule:

    per_stock_j[sym] = median(last N_RECENT realized jumps from cache)
    only if at least MIN_N events exist; otherwise sym is omitted from the
    result and the caller falls back to the universe constant
    (monte_carlo_15dte.EARN_JUMP_PCT, currently 9.3).

Median (not mean) is chosen because single-event 25-30% guidance-bomb gaps
would corrupt mean too easily and bias upward.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Iterable

from database.trader_database import DB
from database.models.core import EarningsJump


# Defaults — tuned for quarterly reporters: 8 events ~= 2 years of history.
# Min 3 events to insulate against single-event noise. Both override-able by callers.
N_RECENT_DEFAULT = 8
MIN_N_DEFAULT    = 3


def load_per_stock_jumps(
    symbols: Iterable[str],
    n_recent: int = N_RECENT_DEFAULT,
    min_n:    int = MIN_N_DEFAULT,
) -> dict[str, float]:
    """Returns {symbol: median_j_pct} for symbols with cached events >= min_n.

    Symbols not present in the returned dict have insufficient cached history
    and the caller should fall back to the universe constant.
    """
    syms = sorted({s for s in symbols})
    if not syms:
        return {}

    # Bulk pull all rows for these symbols, ordered desc by date so we can
    # take the first N per group cheaply in Python.
    by_sym: dict[str, list[float]] = defaultdict(list)
    chunk = 200
    for i in range(0, len(syms), chunk):
        chunk_syms = syms[i:i+chunk]
        ph = ','.join(['%s'] * len(chunk_syms))
        cur = DB.execute_sql(
            f"SELECT symbol, jump_pct FROM earnings_jumps "
            f"WHERE symbol IN ({ph}) "
            f"ORDER BY symbol, earnings_date DESC",
            chunk_syms,
        )
        for sym, jp in cur.fetchall():
            lst = by_sym[sym]
            if len(lst) < n_recent:
                lst.append(float(jp))

    out: dict[str, float] = {}
    for sym, jumps in by_sym.items():
        if len(jumps) >= min_n:
            out[sym] = statistics.median(jumps)
    return out
