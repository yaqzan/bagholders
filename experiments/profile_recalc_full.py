"""Profile recalculate the way `--force --full` actually invokes it.

Mirrors trader.py: components = ALL (trend, bb, rsi, macd, stoch, ta,
weekly, dte, overall), 10-year lookback, force=True. Single stock so the
profile is interpretable.
"""
import sys
import os
import cProfile
import pstats
import io
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recalculate_scores import (
    recalculate, _load_regime_map, _load_spy_wk_composite_map,
    _load_mis_stress_map, ALL_MODES,
)

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else 'AAPL'
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 3770   # 10y
OUT_PROF = os.path.join(os.path.dirname(__file__), 'recalc_full.prof')


def main():
    print(f"Profiling: {SYMBOL} × {DAYS}d × ALL components × force=True")
    regime = _load_regime_map()
    spy = _load_spy_wk_composite_map()
    ms = _load_mis_stress_map()

    # Warm imports so the profile doesn't double-count yfinance load
    from market_regime import compute_regime_multiplier as _w
    from dte_recommendation import recommend_dte as _w2

    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    u, s, e = recalculate(
        SYMBOL, DAYS, ALL_MODES, use_batch=True,
        force=True, inner_progress=False,
        regime_map=regime, spy_wk_map=spy, mis_stress_map=ms,
    )
    pr.disable()
    elapsed = time.perf_counter() - t0
    print(f"Done in {elapsed:.2f}s — updated={u}, skipped={s}, errors={e}\n")

    pr.dump_stats(OUT_PROF)

    print("=" * 100)
    print("TOP 25 BY tottime")
    print("=" * 100)
    s_buf = io.StringIO()
    pstats.Stats(pr, stream=s_buf).sort_stats('tottime').print_stats(25)
    print(s_buf.getvalue())

    print("=" * 100)
    print("TOP 25 BY cumtime")
    print("=" * 100)
    s_buf = io.StringIO()
    pstats.Stats(pr, stream=s_buf).sort_stats('cumtime').print_stats(25)
    print(s_buf.getvalue())


if __name__ == '__main__':
    main()
