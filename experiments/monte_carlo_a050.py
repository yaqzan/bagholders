"""
Canonical 3-mode MC for A050 conviction-dampening regime variant.

Overrides monte_carlo's load_signals / load_put_signals to:
  1. Read raw v21 scores with weight_info (for pre_regime)
  2. Apply conviction-dampening: effective_mult = 1 + (stored_mult - 1) * (1 - c*0.5)
     where c = min(1, |pre_regime - 50| / 30)
  3. Recompute overall from pre_regime × effective_mult
  4. Filter by qualifying thresholds using the new overall

All other strategy constants (cascade, TP/SL, MaxPos, CT-PROMOTE, regime-slope
sizing) come from monte_carlo.py untouched.

Runs BASE and A050 for direct comparison. Use `python monte_carlo_a050.py [a050|base|both]`.

Prints per-year + 22-now + 5y for each variant with Realistic/Conservative/
Optimistic modes.
"""
from __future__ import annotations

import json
import sys
import os
from collections import defaultdict, namedtuple
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# monte_carlo.py handles stdout encoding on import — do not double-wrap
import monte_carlo as mc
from database.models.core import Score, AlgorithmVersion
from database.trader_database import DB


# Mini score object — matches the attributes run_window uses
SigRow = namedtuple('SigRow', ['symbol_id', 'date', 'overall', 'trend'])


def _apply_mult(pre_regime, mult):
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * mult
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - mult)
    return int(max(0, min(100, round(adj))))


def _conviction(pre_regime):
    return min(1.0, abs(pre_regime - 50) / 30.0)


def _a050_overall(pre_regime, stored_mult):
    """Apply A050 conviction-dampening to pre_regime + stored_mult -> new overall."""
    c = _conviction(pre_regime)
    damp = 1.0 - c * 0.50
    eff_mult = 1.0 + (stored_mult - 1.0) * damp
    return _apply_mult(pre_regime, eff_mult)


def load_a050_signals_window(version, d_start, d_end, *, side='call'):
    """Load v21 scores, transform overall via A050, return SigRows filtered by threshold."""
    rows = DB.execute_sql(f"""
        SELECT symbol, date, overall, trend, regime_multiplier, weight_info
        FROM scores
        WHERE version_id = {version.id}
          AND date >= '{d_start.isoformat()}' AND date <= '{d_end.isoformat()}'
          AND weight_info IS NOT NULL
        ORDER BY date, overall {'DESC' if side == 'call' else 'ASC'}
    """).fetchall()

    out = []
    for sym_id, d, overall, trend, sm, wi in rows:
        try:
            wi_d = json.loads(wi) if isinstance(wi, str) else wi
            pr = wi_d.get('pre_regime')
            if pr is None:
                # fall back to stored overall
                a050_overall = int(overall)
            else:
                stored_mult = float(sm) if sm is not None else 1.0
                a050_overall = _a050_overall(int(pr), stored_mult)
        except Exception:
            a050_overall = int(overall)
        if side == 'call':
            if a050_overall >= mc.OVERFLOW_THRESHOLD:
                out.append(SigRow(sym_id, d, a050_overall,
                                  int(trend) if trend is not None else None))
        else:
            if a050_overall <= mc.PUT_THRESHOLD:
                out.append(SigRow(sym_id, d, a050_overall,
                                  int(trend) if trend is not None else None))
    # re-sort to match monte_carlo's expectation
    if side == 'call':
        out.sort(key=lambda r: (r.date, -r.overall))
    else:
        out.sort(key=lambda r: (r.date, r.overall))
    return out


def install_a050():
    """Monkey-patch monte_carlo's signal loaders to use A050 scores."""
    def _call_loader(version, d_start, d_end):
        return load_a050_signals_window(version, d_start, d_end, side='call')
    def _put_loader(version, d_start, d_end):
        return load_a050_signals_window(version, d_start, d_end, side='put')
    mc.load_signals = _call_loader
    mc.load_put_signals = _put_loader


def restore_base():
    """Reload monte_carlo's native loaders (call before BASE run)."""
    # Reimport the originals
    import importlib
    importlib.reload(mc)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'both'

    print('='*100)
    print("CANONICAL MC — A050 Conviction-Dampening vs v21 BASE")
    print('='*100)
    print(f"Variant: A050  (effective_mult = 1 + (stored-1) * (1 - c*0.5))")
    print(f"Windows: per-year 2021-2025 + 22-now continuous + 5y continuous")
    print(f"Modes  : Conservative, Realistic, Optimistic  |  N={mc.N_ITER}  start=${mc.STARTING_CASH:,.0f}")
    print()

    v = AlgorithmVersion.get_active_scores_version()
    print(f"Active scores version: v{v.id} ({v.git_commit[:8]})\n")

    all_results = {}

    if which in ('base', 'both'):
        print('#'*100)
        print("# RUNNING BASE (v21 stored scores — current production)")
        print('#'*100)
        # monte_carlo's own loaders already use stored overall
        base_results = {}
        for label, d_start, d_end in [
            ('2021', date(2021, 1, 1), date(2021, 12, 31)),
            ('2022', date(2022, 1, 1), date(2022, 12, 31)),
            ('2023', date(2023, 1, 1), date(2023, 12, 31)),
            ('2024', date(2024, 1, 1), date(2024, 12, 31)),
            ('2025', date(2025, 1, 1), date(2025, 12, 31)),
            ('22-now', date(2022, 1, 1), date.today()),
            ('5y',     date(2021, 4, 25), date.today()),
        ]:
            base_results[label] = mc.run_window(label, d_start, d_end, v)
        all_results['BASE'] = base_results

    if which in ('a050', 'both'):
        print('\n' + '#'*100)
        print("# RUNNING A050 (conviction-dampening, alpha=0.5)")
        print('#'*100)
        install_a050()
        a050_results = {}
        for label, d_start, d_end in [
            ('2021', date(2021, 1, 1), date(2021, 12, 31)),
            ('2022', date(2022, 1, 1), date(2022, 12, 31)),
            ('2023', date(2023, 1, 1), date(2023, 12, 31)),
            ('2024', date(2024, 1, 1), date(2024, 12, 31)),
            ('2025', date(2025, 1, 1), date(2025, 12, 31)),
            ('22-now', date(2022, 1, 1), date.today()),
            ('5y',     date(2021, 4, 25), date.today()),
        ]:
            a050_results[label] = mc.run_window(label, d_start, d_end, v)
        all_results['A050'] = a050_results

    # Side-by-side summary
    if which == 'both':
        print('\n' + '='*130)
        print("SIDE-BY-SIDE SUMMARY — Realistic mode")
        print('='*130)
        print(f"{'Window':<8} | {'BASE MeanRet':>15} {'A050 MeanRet':>15} {'A050 %Δ':>10} | "
              f"{'BASE DD-C':>10} {'A050 DD-C':>10} | {'BASE CTP%':>10} {'A050 CTP%':>10} | "
              f"{'BASE PTP%':>10} {'A050 PTP%':>10}")
        print('-' * 130)
        for label in ['2021', '2022', '2023', '2024', '2025', '22-now', '5y']:
            b = all_results['BASE'].get(label, {}).get('realistic', {})
            a = all_results['A050'].get(label, {}).get('realistic', {})
            b_dd = all_results['BASE'].get(label, {}).get('conservative', {}).get('worst_dd', 0)
            a_dd = all_results['A050'].get(label, {}).get('conservative', {}).get('worst_dd', 0)
            if not (b and a):
                continue
            b_mr = b.get('mean_ret', 0)
            a_mr = a.get('mean_ret', 0)
            # Use log-ratio-ish display for large numbers
            if abs(b_mr) > 1000:
                b_str = f"{b_mr:+.0f}%"
                a_str = f"{a_mr:+.0f}%"
            else:
                b_str = f"{b_mr:+.1f}%"
                a_str = f"{a_mr:+.1f}%"
            pct = (a_mr - b_mr) / (abs(b_mr) + 1) * 100 if b_mr else 0
            print(f"{label:<8} | {b_str:>15} {a_str:>15} {pct:>+9.1f}% | "
                  f"{b_dd:>9.1f}% {a_dd:>9.1f}% | "
                  f"{b.get('call_tp', 0):>9.1f}% {a.get('call_tp', 0):>9.1f}% | "
                  f"{b.get('put_tp', 0):>9.1f}% {a.get('put_tp', 0):>9.1f}%")


if __name__ == '__main__':
    main()
