"""Time AAPL × 10y × force=True under the new default (daily-only)."""
import sys, os, time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recalculate_scores import (
    recalculate, _load_regime_map, _load_spy_wk_composite_map,
    _load_mis_stress_map, DAILY_COMPONENTS, ALL_MODES,
)

regime = _load_regime_map()
spy = _load_spy_wk_composite_map()
ms = _load_mis_stress_map()
print("Maps loaded.")

# Warm imports so we measure steady-state, not import overhead
from market_regime import compute_regime_multiplier as _w
from dte_recommendation import recommend_dte as _w2

for label, comps in [('DAILY (new default)', DAILY_COMPONENTS), ('ALL (old default)', ALL_MODES)]:
    t0 = time.perf_counter()
    u, s, e = recalculate('AAPL', 3770, comps, use_batch=True,
                           force=True, inner_progress=False,
                           regime_map=regime, spy_wk_map=spy, mis_stress_map=ms)
    dt = time.perf_counter() - t0
    print(f'  {label:24s}  {dt:6.2f}s   updated={u}')
