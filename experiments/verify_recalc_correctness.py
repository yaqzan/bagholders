"""Confirm the windowing+hoisting refactor produces identical scores.

Strategy:
  1. Snapshot current Score.overall values for [today-DAYS, today] for SYMBOL.
     These were written by the same scoring formula that the new code runs;
     the refactor only touches data-loading speed, so values must match.
  2. Run the new code with force=True (recomputes via the optimized path).
  3. Diff. Any mismatch on overall (or component scores when --deep) => regression.

Run: python experiments/verify_recalc_correctness.py [SYMBOL] [DAYS]
Defaults: MSFT 365d.
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models.core import Stock, Score, AlgorithmVersion
from recalculate_scores import (
    _load_regime_map,
    _load_spy_wk_composite_map,
    _load_mis_stress_map,
)

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else 'MSFT'
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365


def snapshot(symbol, cutoff_start, version):
    rows = list(
        Score.select(
            Score.date, Score.overall, Score.trend, Score.bb, Score.rsi,
            Score.macd, Score.stoch, Score.technical_alignment,
            Score.volume, Score.regime_multiplier,
        )
        .where(
            (Score.symbol == symbol) &
            (Score.version == version) &
            (Score.date >= cutoff_start)
        )
        .order_by(Score.date.asc())
    )
    return {r.date: r for r in rows}


def main():
    stock = Stock.get_or_none(Stock.symbol == SYMBOL)
    if not stock:
        print(f"Stock {SYMBOL} not found"); sys.exit(2)

    cutoff_start = date.today() - timedelta(days=DAYS)
    version = AlgorithmVersion.get_or_create_current()
    print(f"Verifying {SYMBOL} from {cutoff_start} ({DAYS}d), version={version.git_commit}")

    # 1. Snapshot existing scores
    before = snapshot(SYMBOL, cutoff_start, version)
    print(f"Snapshotted {len(before)} score rows")
    if not before:
        print("ERROR: no existing scores to compare against. Pick a stock with prior scores.")
        sys.exit(2)

    # 2. Run optimized path with force=True
    regime_map = _load_regime_map()
    spy_wk_map = _load_spy_wk_composite_map()
    mis_stress_map = _load_mis_stress_map()
    u, s, e = stock.recalculate_scores_full(
        cutoff_start, {'overall'}, force=True, show_progress=False,
        regime_map=regime_map, spy_wk_map=spy_wk_map, mis_stress_map=mis_stress_map,
    )
    print(f"Recompute: updated={u}, skipped={s}, errors={e}")

    # 3. Diff
    after = snapshot(SYMBOL, cutoff_start, version)

    fields = ['overall', 'trend', 'bb', 'rsi', 'macd', 'stoch',
              'technical_alignment', 'volume', 'regime_multiplier']
    EPS = 0.01
    drift_count = 0
    drift_examples = []
    only_before = set(before) - set(after)
    only_after = set(after) - set(before)
    for d, b in before.items():
        a = after.get(d)
        if a is None:
            continue
        for f in fields:
            bv, av = getattr(b, f), getattr(a, f)
            if bv is None and av is None:
                continue
            if bv is None or av is None:
                drift_count += 1
                drift_examples.append((d, f, bv, av))
                continue
            if abs(float(bv) - float(av)) > EPS:
                drift_count += 1
                drift_examples.append((d, f, float(bv), float(av)))

    print(f"\nResult: {drift_count} field drift(s) across {len(before)} rows")
    if only_before:
        print(f"  WARNING: {len(only_before)} dates present before but not after: {sorted(only_before)[:5]}")
    if only_after:
        print(f"  WARNING: {len(only_after)} dates present after but not before: {sorted(only_after)[:5]}")
    if drift_examples:
        print("  First 15 mismatches (date, field, before, after):")
        for d, f, bv, av in drift_examples[:15]:
            print(f"    {d}  {f:24s}  {bv} -> {av}")
        sys.exit(1)
    print("  PASS: all overall + component values match within EPS=0.01")


if __name__ == '__main__':
    main()
