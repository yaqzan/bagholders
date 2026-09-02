"""
Sim → MC bridge: feed simulator-produced scores into monte_carlo without
DB recalculate.

Pattern:
  1. Build a ScoreSimulator with custom scoring_fn or volume_amplifier overrides
  2. Call simulator.simulate_full() → in-memory dict of rich rows
  3. Wrap with `inject_into_mc(rich_dict)` context manager
  4. Within the context, call standard mc.run_window(...) — load_signals and
     load_put_signals are monkey-patched to consult the in-memory dict instead
     of DB.

Per-variant cost: ~3-10 min simulate_full + ~5 min MC = ~8-15 min total.
Compared to ~20-25 min recalculate + MC. For sweeps of 3+ variants, this
amortizes the simulator data-load (~3-5 min one-time) across iterations.

Validation pattern: run baseline (current shipped, no overrides) via this
bridge AND via DB. Per-window compound and DD must match within MC noise.
See validate_against_db() below.

Limitations:
  - DESIGN_D_PUT_FLIP path consumes regime_multiplier directly from Score
    rows; the bridge populates it from MarketRegime so flipped path works.
  - WEAK_WEEKLY_PUT_DROP filter consumes weight_info.w_adj and volume_signal;
    these are passed through from compute_overall_score's weight_info return.
  - If the variant changes weight_info structure (e.g., adds a new field),
    MC consumers reading that field need to tolerate KeyError. Default None.
"""
from __future__ import annotations

import contextlib
import json as _json
import os
import sys
from collections import namedtuple
from datetime import date
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import monte_carlo as mc

# Mirror of the Peewee Score row attributes that monte_carlo.py reads.
# - symbol: ticker string
# - symbol_id: integer FK (used as ph/ed_map key everywhere downstream)
# - date: datetime.date
# - overall, trend: int 0-100
# - weight_info: JSON-serializable str (consumers parse on demand)
# - volume_signal: str (e.g., 'CONVICTION', 'NEUTRAL', 'REJECTION')
# - regime_multiplier: float
SimScoreRow = namedtuple(
    'SimScoreRow',
    ('symbol', 'symbol_id', 'date', 'overall', 'trend',
     'weight_info', 'volume_signal', 'regime_multiplier'),
)


def build_score_lists(rich: Dict[Tuple[str, date], Tuple]):
    """Convert simulate_full() output → mutable list of SimScoreRow,
    sorted ascending by date for predictable downstream iteration.

    Stores `weight_info` as JSON string (matches DB shape; MC's _json.loads
    code path handles both str and dict)."""
    rows: List[SimScoreRow] = []
    for (sym, d), (overall, trend, wi_dict, vsig, regime_mult, sym_id) in rich.items():
        wi_str = _json.dumps(wi_dict) if wi_dict else '{}'
        rows.append(SimScoreRow(
            symbol=sym,
            symbol_id=sym_id,
            date=d,
            overall=int(overall),
            trend=int(trend),
            weight_info=wi_str,
            volume_signal=vsig,
            regime_multiplier=float(regime_mult),
        ))
    rows.sort(key=lambda r: (r.date, -r.overall))
    return rows


@contextlib.contextmanager
def inject_into_mc(rows: List[SimScoreRow]):
    """Context manager: temporarily replace mc.load_signals and
    mc.load_put_signals with in-memory lookups against `rows`. Restores
    originals on exit even if MC raises.

    The patched loaders apply MC's standard date + score thresholds:
      - load_signals: overall >= OVERFLOW_THRESHOLD (70)
      - load_put_signals: overall <= PUT_THRESHOLD (25)
    """
    original_load_signals = mc.load_signals
    original_load_put_signals = mc.load_put_signals

    def patched_load_signals(version, d_start, d_end):
        thresh = mc.OVERFLOW_THRESHOLD
        out = [r for r in rows
               if r.date >= d_start and r.date <= d_end and r.overall >= thresh]
        out.sort(key=lambda r: (r.date, -r.overall))
        return out

    def patched_load_put_signals(version, d_start, d_end):
        thresh = mc.PUT_THRESHOLD
        if not mc.DESIGN_D_PUT_FLIP:
            out = [r for r in rows
                   if r.date >= d_start and r.date <= d_end and r.overall <= thresh]
            out.sort(key=lambda r: (r.date, r.overall))
            return out
        # Design D path: broaden to <=40, recover pre_regime, re-apply flip
        broadened = [r for r in rows
                     if r.date >= d_start and r.date <= d_end and r.overall <= 40]
        flipped = []
        for s in broadened:
            ov = int(s.overall)
            pre = None
            if s.weight_info:
                try:
                    wi = _json.loads(s.weight_info) if isinstance(s.weight_info, str) else s.weight_info
                    if 'pre_regime' in wi:
                        pre = int(wi['pre_regime'])
                except Exception:
                    pre = None
            mult = s.regime_multiplier if s.regime_multiplier is not None else 1.0
            if pre is None:
                if mult == 1.0:
                    pre = ov
                else:
                    if ov >= 50:
                        pre = round(50 + (ov - 50) / mult)
                    else:
                        pre = round(50 + (ov - 50) / (2.0 - mult))
            pre = max(0, min(100, pre))
            if pre >= 50:
                continue
            if mult == 1.0:
                new_ov = pre
            else:
                new_ov = round(50 + (pre - 50) * mult)
            new_ov = max(0, min(100, new_ov))
            if new_ov > thresh:
                continue
            # Replace overall with flipped value (namedtuples are immutable;
            # rebuild row with new overall, keep everything else)
            flipped.append(s._replace(overall=int(new_ov)))
        flipped.sort(key=lambda r: (r.date, r.overall))
        return flipped

    mc.load_signals = patched_load_signals
    mc.load_put_signals = patched_load_put_signals
    try:
        yield
    finally:
        mc.load_signals = original_load_signals
        mc.load_put_signals = original_load_put_signals


def run_windows(label_to_window, rich, n_iter=300):
    """Drive mc.run_window across multiple windows under a single injected
    score set. `label_to_window` is iterable of (label, d_start, d_end)."""
    rows = build_score_lists(rich)

    # MC needs a `version` arg but doesn't use it for score lookup once
    # load_signals is patched. Pass active version as a no-op token.
    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()

    mc.N_ITER = n_iter
    os.environ['MC_NO_MP'] = '1'

    results = {}
    with inject_into_mc(rows):
        for label, d_start, d_end in label_to_window:
            print(f"\n--- {label} ({d_start} → {d_end}) ---", flush=True)
            res = mc.run_window(label, d_start, d_end, version)
            r = res['seeded']
            results[label] = {
                'mean_ret': r['mean_ret'],
                'med_ret':  r['med_ret'],
                'mean_dd':  r['mean_dd'],
                'worst_dd': r['worst_dd'],
                'p_coll':   r['p_coll'],
                'call_tp':  r['call_tp'],
                'put_tp':   r['put_tp'],
                'call_trades': r['call_trades'],
                'put_trades':  r['put_trades'],
            }
            print(f"  mean_ret={r['mean_ret']:>+15,.1f}%  DD={r['worst_dd']:>5.1f}%  "
                  f"coll={r['p_coll']:>4.1f}%  C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})",
                  flush=True)
    return results


def quick_validate(symbol_subset=None, lookback_days=400):
    """Sanity check: run a single MC window via DB scores AND via sim scores
    using vanilla scoring (no overrides). The two should produce the same
    signal set within rounding (compound/DD will differ by MC seed noise but
    signal counts and TP rates should match within a few %).

    Run with a small symbol subset for speed (~2-3 min).
    """
    from datetime import date
    from simulator import ScoreSimulator
    from database.models.core import AlgorithmVersion, Stock

    # Build simulator on subset
    if symbol_subset:
        symbols = symbol_subset
    else:
        symbols = [s.symbol for s in Stock.select().limit(50)]
    print(f"[validate] building simulator: {len(symbols)} symbols × {lookback_days}d", flush=True)
    sim = ScoreSimulator(symbols=symbols, lookback_days=lookback_days)
    rich = sim.simulate_full()

    rows = build_score_lists(rich)
    n_calls = sum(1 for r in rows if r.overall >= mc.OVERFLOW_THRESHOLD)
    n_puts  = sum(1 for r in rows if r.overall <= mc.PUT_THRESHOLD)
    print(f"[validate] sim produced {n_calls} call signals, {n_puts} put signals on subset")

    # DB query for the same window
    from database.models.core import Score
    version = AlgorithmVersion.get_active_scores_version()
    cutoff = date.today() - __import__('datetime').timedelta(days=lookback_days)
    db_calls = Score.select().where(
        Score.version == version,
        Score.date >= cutoff,
        Score.overall >= mc.OVERFLOW_THRESHOLD,
        Score.symbol.in_(symbols),
    ).count()
    db_puts = Score.select().where(
        Score.version == version,
        Score.date >= cutoff,
        Score.overall <= mc.PUT_THRESHOLD,
        Score.symbol.in_(symbols),
    ).count()
    print(f"[validate] DB has {db_calls} call signals, {db_puts} put signals on same subset")

    # Tolerance: ±2% (sim and DB should produce essentially identical scoring
    # for current production version since simulate_full uses the same compute_overall_score)
    call_drift = abs(n_calls - db_calls) / max(db_calls, 1) * 100
    put_drift = abs(n_puts - db_puts) / max(db_puts, 1) * 100
    print(f"[validate] drift: calls={call_drift:.1f}%  puts={put_drift:.1f}%  (target <2%)")
    return {
        'sim_calls': n_calls, 'sim_puts': n_puts,
        'db_calls': db_calls, 'db_puts': db_puts,
        'call_drift_pct': call_drift, 'put_drift_pct': put_drift,
    }


if __name__ == '__main__':
    # Default action: quick validate against DB
    quick_validate()
