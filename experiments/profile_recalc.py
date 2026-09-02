"""Profile recalculate_scores_full for one stock × 1y to find real hotspots.

Run: python experiments/profile_recalc.py [SYMBOL] [DAYS]
Defaults: AAPL 365d. Writes raw cProfile to experiments/recalc.prof and
prints top hotspots by tottime and cumtime.
"""
import sys
import os
import cProfile
import pstats
import io
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models.core import Stock
from recalculate_scores import _load_regime_map

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else 'AAPL'
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365

OUT_PROF = os.path.join(os.path.dirname(__file__), 'recalc.prof')


def main():
    stock = Stock.get_or_none(Stock.symbol == SYMBOL)
    if not stock:
        print(f"Stock {SYMBOL} not found", file=sys.stderr)
        sys.exit(2)

    cutoff_start = date.today() - timedelta(days=DAYS)
    components = {'overall'}
    print(f"Profiling: {SYMBOL} from {cutoff_start} ({DAYS}d), components={components}, force=True")

    # Pre-load regime map (mimics what run() does, so we don't profile the loader itself)
    regime_map = _load_regime_map()
    print(f"Regime map size: {len(regime_map)}")

    # Warm up: import paths, peewee model attrs, etc., shouldn't dominate the profile.
    _ = list(stock.__class__.select().limit(1))

    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    u, s, e = stock.recalculate_scores_full(
        cutoff_start, components, force=True, show_progress=False,
        regime_map=regime_map,
    )
    pr.disable()
    elapsed = time.perf_counter() - t0
    print(f"Done in {elapsed:.2f}s — updated={u}, skipped={s}, errors={e}")
    if u + s == 0:
        print("WARNING: no rows touched. Profile likely empty. Pick a stock with data in window.")

    pr.dump_stats(OUT_PROF)
    print(f"Raw profile: {OUT_PROF}\n")

    # Top 30 by total time spent IN the function (excluding callees)
    print("=" * 100)
    print("TOP 30 BY tottime (self time, excludes callees) — where Python actually spends cycles")
    print("=" * 100)
    s_buf = io.StringIO()
    ps = pstats.Stats(pr, stream=s_buf).sort_stats('tottime')
    ps.print_stats(30)
    print(s_buf.getvalue())

    # Top 30 by cumulative time — call-tree picture
    print("=" * 100)
    print("TOP 30 BY cumtime (incl. callees) — call-tree picture")
    print("=" * 100)
    s_buf = io.StringIO()
    ps = pstats.Stats(pr, stream=s_buf).sort_stats('cumtime')
    ps.print_stats(30)
    print(s_buf.getvalue())

    # Callers of the heaviest leaf functions
    print("=" * 100)
    print("CALLERS of top-5 tottime functions (who's invoking the hotspots)")
    print("=" * 100)
    s_buf = io.StringIO()
    ps = pstats.Stats(pr, stream=s_buf).sort_stats('tottime')
    ps.print_callers(5)
    print(s_buf.getvalue())


if __name__ == '__main__':
    main()
