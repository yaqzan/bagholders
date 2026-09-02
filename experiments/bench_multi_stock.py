"""Time N stocks back-to-back in one process to measure steady-state cost.

The single-stock profile included a ~2s one-time import chain. Running multiple
stocks amortizes that and gives the real per-stock number that scales with
fleet size.
"""
import sys
import os
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models.core import Stock
from recalculate_scores import (
    _load_regime_map, _load_spy_wk_composite_map, _load_mis_stress_map,
)

SYMBOLS = sys.argv[1].split(',') if len(sys.argv) > 1 else ['AAPL', 'MSFT', 'NVDA', 'GOOG', 'META']
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365


def main():
    print(f"Bench: {SYMBOLS}, {DAYS}d, force=True\n")

    regime_map = _load_regime_map()
    spy_wk_map = _load_spy_wk_composite_map()
    mis_stress_map = _load_mis_stress_map()
    print(f"Maps loaded.\n")

    cutoff_start = date.today() - timedelta(days=DAYS)
    components = {'overall'}

    times = []
    total_t0 = time.perf_counter()
    for sym in SYMBOLS:
        stock = Stock.get_or_none(Stock.symbol == sym)
        if not stock:
            print(f"  {sym}: not found, skipping")
            continue
        t0 = time.perf_counter()
        u, s, e = stock.recalculate_scores_full(
            cutoff_start, components, force=True, show_progress=False,
            regime_map=regime_map, spy_wk_map=spy_wk_map, mis_stress_map=mis_stress_map,
        )
        dt = time.perf_counter() - t0
        times.append((sym, dt, u))
        print(f"  {sym:6s}  {dt:6.2f}s   updated={u}")
    total_dt = time.perf_counter() - total_t0

    n = len(times)
    if n == 0:
        return
    print(f"\nTotal: {total_dt:.2f}s for {n} stocks")
    print(f"First call: {times[0][1]:.2f}s (includes one-time import)")
    if n > 1:
        rest = [t for _, t, _ in times[1:]]
        avg_steady = sum(rest) / len(rest)
        print(f"Steady-state: {avg_steady:.2f}s/stock (avg of {len(rest)} after first)")
        print(f"\nFleet projection (740 stocks, 8 workers, 8 stages):")
        per_worker_per_stage = (740 / 8) * avg_steady
        print(f"  Per worker per stage: {per_worker_per_stage:.0f}s = {per_worker_per_stage/60:.1f} min")
        print(f"  Total recalc: {per_worker_per_stage * 8 / 60:.0f} min  (estimate)")


if __name__ == '__main__':
    main()
