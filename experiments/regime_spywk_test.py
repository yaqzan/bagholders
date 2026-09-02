"""
SPY Weekly Composite — Regime Enhancement Experiment
=====================================================
Tests whether incorporating SPY's weekly composite score into the
regime composite reduces false put signals during market recoveries.

PROBLEM (April 17, 2026): A recovering market produced many put signals
≤25, but SPY's weekly composite was already bullish (74). The current
regime (VIX + inverted breadth) lags the recovery. VIX hasn't fully
normalized and breadth recovers slowly, resulting in a LOW multiplier
that amplifies borderline put scores past the ≤25 threshold.

HYPOTHESIS: Adding SPY_wk DIRECTLY (not inverted) raises the composite
on recovery days. Higher composite → higher multiplier → puts SUPPRESSED
(2.0 - mult is smaller → less amplification of pre_regime put scores).

Directional mechanics (same as production):
  - HIGH composite → HIGH multiplier → puts closer to 50 (suppressed)
  - LOW composite  → LOW multiplier  → puts further from 50 (amplified)
  - SPY_wk = 74 (bullish) added directly → RAISES composite → SUPPRESSES puts ✓
  - SPY_wk = 30 (bearish) added directly → LOWERS composite → AMPLIFIES puts ✓

Variants tested (B is the baseline for delta computation):
  B    current         — production (VIX + inverted breadth, dynamic sigmoid)
  J1   spywk_10pct     — 10% SPY_wk direct, 90% current composite
  J2   spywk_15pct     — 15% SPY_wk direct (primary test)
  J3   spywk_20pct     — 20% SPY_wk direct
  J4   spywk_25pct     — 25% SPY_wk direct
  J5   spywk_30pct     — 30% SPY_wk direct
  J6   spywk_inv_15    — 15% (100 - SPY_wk) INVERTED — wrong-direction control

Usage:
    python experiments/regime_spywk_test.py [days]
    python experiments/regime_spywk_test.py 1825    # 5y (default)
    python experiments/regime_spywk_test.py 730     # 2y
    python experiments/regime_spywk_test.py --start=2022-01-01 --end=2022-12-31
"""

from __future__ import annotations

import io
import sys as _sys
if hasattr(_sys.stdout, 'buffer'):
    _sys.stdout = io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')

import bisect
import json
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

# -- project imports ----------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))      # experiments/

from database.models.core import (
    Score, MarketRegime, MarketBreadth, AlgorithmVersion, WeeklyScore,
)
from database.models.technical import PriceHistory

# Import shared infrastructure from regime_ab_test to avoid duplication
from regime_ab_test import (
    _apply_bands, _BANDS_CURRENT, _apply_to_score,
    _vix_gradient, _breadth_inverted, _vix_sigmoid_weight, _composite_current,
    _load_active_version, _load_peaks_with_pre_regime,
    _load_regime_cache, _load_price_cache,
    _run_barrier, _run_variant, _score_bucket, _make_variant,
    _win_rate, _avg_ret, _wr_fmt, _delta_fmt,
    _print_variant_table, _print_detailed_variant, _print_pearson_summary,
    CALL_THRESHOLD, PUT_THRESHOLD, MIN_N_REPORT,
    DISPLAY_PERIODS, CALL_BUCKETS, PUT_BUCKETS, ALL_BUCKETS,
)

# -- constants ----------------------------------------------------------------
DEFAULT_LOOKBACK = 1825   # 5 years

# Put-dominated day definition: many puts, few/no calls
PUT_DOM_MIN_PUTS  = 20    # ≥ this many put-qualifying scores on the day
PUT_DOM_MAX_CALLS = 5     # AND ≤ this many call-qualifying scores on the day

# SPY_wk conditional analysis buckets
SPY_WK_BUCKETS = [
    (0,  45,  'SPY_wk<45  (bearish)  '),
    (45, 60,  'SPY_wk 45-60 (neutral)'),
    (60, 75,  'SPY_wk 60-75 (bullish)'),
    (75, 101, 'SPY_wk≥75  (strong)   '),
]


# -- SPY weekly score loading -------------------------------------------------

def _load_spy_wk_forward_fill(cutoff: date) -> Tuple[Callable, int]:
    """Load SPY weekly composite scores from WeeklyScore table.

    Returns (lookup_fn, n_rows) where lookup_fn(d) -> Optional[float]
    returns the most recent SPY weekly composite on or before date d.
    """
    rows = list(
        WeeklyScore.select(WeeklyScore.date, WeeklyScore.composite)
        .where(
            WeeklyScore.symbol_id == 'SPY',
            WeeklyScore.date >= cutoff - timedelta(days=21),  # extra buffer for fill
        )
        .order_by(WeeklyScore.date)
    )

    if not rows:
        print(f"  {Fore.YELLOW}No SPY weekly scores found in WeeklyScore table.{Style.RESET_ALL}")
        return lambda d: None, 0

    wk_list = sorted(
        [(r.date, float(r.composite)) for r in rows if r.composite is not None]
    )

    if wk_list:
        vals = [v for _, v in wk_list]
        print(f"  SPY weekly: {len(wk_list)} rows, "
              f"{wk_list[0][0]} → {wk_list[-1][0]}, "
              f"value range [{min(vals):.0f}, {max(vals):.0f}], "
              f"mean {np.mean(vals):.1f}")

    sorted_dates = [d for d, _ in wk_list]
    date_to_val  = {d: v for d, v in wk_list}

    def lookup(target: date) -> Optional[float]:
        """Return most recent SPY_wk on or before target."""
        idx = bisect.bisect_right(sorted_dates, target) - 1
        if idx < 0:
            return None
        return date_to_val[sorted_dates[idx]]

    return lookup, len(wk_list)


def _augment_regime_cache_with_spywk(regime_cache: Dict, spy_wk_lookup: Callable) -> None:
    """Add 'spy_wk' field to every entry in regime_cache (in-place)."""
    n_found = 0
    for d, reg in regime_cache.items():
        val = spy_wk_lookup(d)
        reg['spy_wk'] = val
        if val is not None:
            n_found += 1
    pct = n_found / len(regime_cache) * 100 if regime_cache else 0
    print(f"  SPY_wk matched to {n_found}/{len(regime_cache)} regime dates ({pct:.0f}%)")


# -- Composite functions for SPY_wk variants ---------------------------------

def _composite_spywk_blend(weight: float, invert: bool = False):
    """Factory: returns a composite fn that blends SPY_wk with the current composite.

    weight  : fraction of composite assigned to SPY_wk (0.0 – 1.0)
    invert  : if True, use (100 - spy_wk) instead of spy_wk (wrong-direction control)
    """
    w_spywk   = weight
    w_current = 1.0 - weight

    def _fn(reg, fg=None):
        if reg is None:
            return None
        base = _composite_current(reg, fg)
        spy_wk = reg.get('spy_wk')

        if spy_wk is None:
            # No SPY_wk available → fall back to current composite unchanged
            return base

        spy_component = (100.0 - spy_wk) if invert else float(spy_wk)
        spy_component = max(0.0, min(100.0, spy_component))

        if base is None:
            # Current composite unavailable → use spy_wk alone (scaled to band range)
            return spy_component

        result = w_current * base + w_spywk * spy_component
        return max(0.0, min(100.0, result))

    return _fn


# -- Variant registries -------------------------------------------------------

# Symmetric variants: one composite for both calls and puts
VARIANTS = [
    _make_variant('B: current',      composite_fn=_composite_current,  bands=_BANDS_CURRENT),
    _make_variant('J1: spywk_10pct', composite_fn=_composite_spywk_blend(0.10), bands=_BANDS_CURRENT),
    _make_variant('J2: spywk_15pct', composite_fn=_composite_spywk_blend(0.15), bands=_BANDS_CURRENT),
    _make_variant('J3: spywk_20pct', composite_fn=_composite_spywk_blend(0.20), bands=_BANDS_CURRENT),
    _make_variant('J4: spywk_25pct', composite_fn=_composite_spywk_blend(0.25), bands=_BANDS_CURRENT),
    _make_variant('J5: spywk_30pct', composite_fn=_composite_spywk_blend(0.30), bands=_BANDS_CURRENT),
    _make_variant('J6: spywk_inv_15',composite_fn=_composite_spywk_blend(0.15, invert=True), bands=_BANDS_CURRENT),
]

# Asymmetric variants: calls use standard composite (B), puts use SPY_wk blend.
# Format: (label, call_composite_fn, put_composite_fn)
# The call_fn is always _composite_current — puts carry all the SPY_wk change.
def _asym_call_fn(reg, fg=None):
    return _apply_bands(_composite_current(reg, fg) or 50.0, _BANDS_CURRENT)

VARIANTS_ASYM = [
    # (label, call_mult_fn, put_mult_fn)
    ('JA: B (asym reference)',
     _asym_call_fn,
     lambda reg, fg=None: _apply_bands(_composite_current(reg, fg) or 50.0, _BANDS_CURRENT)),
    ('JA2: put_15pct',
     _asym_call_fn,
     lambda reg, fg=None: _apply_bands(_composite_spywk_blend(0.15)(reg, fg) or 50.0, _BANDS_CURRENT)),
    ('JA3: put_20pct',
     _asym_call_fn,
     lambda reg, fg=None: _apply_bands(_composite_spywk_blend(0.20)(reg, fg) or 50.0, _BANDS_CURRENT)),
    ('JA4: put_25pct',
     _asym_call_fn,
     lambda reg, fg=None: _apply_bands(_composite_spywk_blend(0.25)(reg, fg) or 50.0, _BANDS_CURRENT)),
    ('JA5: put_30pct',
     _asym_call_fn,
     lambda reg, fg=None: _apply_bands(_composite_spywk_blend(0.30)(reg, fg) or 50.0, _BANDS_CURRENT)),
]


def _run_variant_asym(
    variant_label: str,
    call_fn: Callable,
    put_fn: Callable,
    peaks_raw: List[dict],
    regime_cache: Dict,
    price_cache: Dict,
    fg_cache: Dict,
) -> Dict:
    """Like _run_variant but selects multiplier by pre_regime direction.

    pre_regime >= 50  → call_fn(reg) multiplier  (calls unchanged from baseline)
    pre_regime <  50  → put_fn(reg)  multiplier  (puts get SPY_wk-blended composite)
    """
    results = defaultdict(list)

    for pk in peaks_raw:
        reg  = regime_cache.get(pk['date'])
        mult = call_fn(reg, fg_cache) if pk['pre_regime'] >= 50 else put_fn(reg, fg_cache)

        new_score = _apply_to_score(pk['pre_regime'], mult)
        bucket    = _score_bucket(new_score)
        if bucket is None:
            continue

        price_rows = price_cache.get(pk['symbol'], [])
        r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
        if r is None:
            continue

        results[bucket].append(r)

    return dict(results)


# -- SPY_wk conditional analysis ----------------------------------------------

def _spy_wk_conditional_wr(
    peaks_raw: List[dict],
    regime_cache: Dict,
    price_cache: Dict,
    variant_fn: Callable,
    period: str = '15d',
) -> Dict:
    """For each SPY_wk bucket, compute WR for put peaks under this variant.

    Returns {bucket_label: {'n': int, 'wr': Optional[float]}}
    """
    from collections import defaultdict as ddict
    bucket_results = {label: [] for _, _, label in SPY_WK_BUCKETS}

    for pk in peaks_raw:
        pre = pk['pre_regime']
        if not (pre <= 35):  # only consider pre_regime put territory
            continue

        reg = regime_cache.get(pk['date'])
        spy_wk = reg.get('spy_wk') if reg else None
        if spy_wk is None:
            continue

        mult = variant_fn(reg)
        new_score = _apply_to_score(pre, mult)
        if new_score > PUT_THRESHOLD:
            continue  # doesn't qualify as a put under this variant

        price_rows = price_cache.get(pk['symbol'], [])
        r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
        if r is None:
            continue

        # Assign to SPY_wk bucket
        for lo, hi, label in SPY_WK_BUCKETS:
            if lo <= spy_wk < hi:
                bucket_results[label].append(r)
                break

    result = {}
    for _, _, label in SPY_WK_BUCKETS:
        entries = bucket_results[label]
        wr = _win_rate(entries, period)
        result[label] = {'n': len(entries), 'wr': wr}
    return result


def _print_spywk_conditional(
    variant_results_for_puts: Dict[str, Dict],
    regime_cache: Dict,
    peaks_raw: List[dict],
    price_cache: Dict,
    period: str = '15d',
) -> None:
    """Print WR table conditioned on SPY_wk level, across variants, for put peaks only.

    variant_results_for_puts: variant_label → result_dict from _run_variant (puts only)
    """
    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}PUT WR{period} conditioned on SPY weekly composite (spy_wk){Style.RESET_ALL}")
    print(f"  Shows put peak WR{period} under each variant, split by SPY's weekly composite on signal date.")
    print(f"  Higher spy_wk = market recovering/bullish. Target: variants J1-J5 should SUPPRESS puts on high-spy_wk days.")

    variants = [v[0] for v in VARIANTS]
    col_w = 14
    hdr = f"{'SPY_wk bucket':<30}  {'N(B)':>6}  " + ''.join(f"{v[:col_w]:>{col_w}}" for v in variants)
    print(f"\n{hdr}")
    print('-' * 110)

    # Per-variant, per-SPY_wk bucket: compute WR for puts
    for v_label, v_fn in VARIANTS:
        pass  # pre-compute below

    # Build per-variant spy_wk conditional results
    variant_spywk = {}
    for v_label, v_fn in VARIANTS:
        res = _spy_wk_conditional_wr(peaks_raw, regime_cache, price_cache, v_fn, period)
        variant_spywk[v_label] = res

    # Get baseline (B) N per bucket for reference
    b_res = variant_spywk.get('B: current', {})

    for _, _, label in SPY_WK_BUCKETS:
        b_data = b_res.get(label, {'n': 0, 'wr': None})
        b_wr   = b_data['wr']
        row = f"{label}  {b_data['n']:>6}  "
        for v_label, _ in VARIANTS:
            data = variant_spywk.get(v_label, {}).get(label, {'n': 0, 'wr': None})
            wr = data['wr']
            if v_label == 'B: current':
                row += f"{_wr_fmt(wr):>14}"
            else:
                delta = (wr - b_wr) if (wr is not None and b_wr is not None) else None
                row += f"  {_wr_fmt(wr)}/{_delta_fmt(delta)}"
        print(row)

    print('-' * 110)
    print(f"  Delta interpretation: +X.X = WR improved vs B (better suppression of bad puts)")
    print(f"  GOAL: strong SPY_wk buckets (≥60) should show LOWER N (fewer qualifying puts)")
    print(f"  (lower N means the variant suppressed borderline puts back above the ≤25 threshold)")


# -- Put-dominated day analysis -----------------------------------------------

def _find_put_dominated_dates(peaks_raw: List[dict], regime_cache: Dict) -> Dict[date, dict]:
    """Identify dates with ≥PUT_DOM_MIN_PUTS put peaks and ≤PUT_DOM_MAX_CALLS calls.

    Uses production multiplier (B: current) to determine qualifying scores.
    Returns {date: {put_peaks: [...], call_peaks: [...], spy_wk: float}}
    """
    # Get production variant function
    b_fn = dict(VARIANTS).get('B: current')
    if b_fn is None:
        return {}

    date_groups: Dict[date, dict] = defaultdict(lambda: {'puts': [], 'calls': []})
    for pk in peaks_raw:
        reg = regime_cache.get(pk['date'])
        mult = b_fn(reg) if reg else 1.0
        score = _apply_to_score(pk['pre_regime'], mult)
        if score <= PUT_THRESHOLD:
            date_groups[pk['date']]['puts'].append(pk)
        elif score >= CALL_THRESHOLD:
            date_groups[pk['date']]['calls'].append(pk)

    event_dates = {}
    for d, grp in date_groups.items():
        if len(grp['puts']) >= PUT_DOM_MIN_PUTS and len(grp['calls']) <= PUT_DOM_MAX_CALLS:
            reg = regime_cache.get(d, {})
            event_dates[d] = {
                'put_peaks': grp['puts'],
                'call_peaks': grp['calls'],
                'spy_wk':    reg.get('spy_wk'),
                'vix':       reg.get('vix_close'),
                'breadth':   reg.get('breadth'),
                'mult_B':    float(reg.get('multiplier') or 1.0),
                'n_puts':    len(grp['puts']),
                'n_calls':   len(grp['calls']),
            }
    return event_dates


def _print_put_dominated_analysis(
    event_dates: Dict[date, dict],
    peaks_raw: List[dict],
    regime_cache: Dict,
    price_cache: Dict,
    period: str = '15d',
) -> None:
    """Print analysis of put-dominated days across all variants."""
    if not event_dates:
        print(f"\n{Fore.YELLOW}No put-dominated days found "
              f"(≥{PUT_DOM_MIN_PUTS} puts, ≤{PUT_DOM_MAX_CALLS} calls){Style.RESET_ALL}")
        return

    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Put-Dominated Days Analysis (≥{PUT_DOM_MIN_PUTS} put signals, ≤{PUT_DOM_MAX_CALLS} call signals){Style.RESET_ALL}")
    print(f"  {len(event_dates)} event dates identified. "
          f"Showing WR{period} for put peaks on these days vs. non-event days.\n")

    # Sort event dates
    sorted_events = sorted(event_dates.items(), key=lambda x: x[0])

    # Print event date table (top 20, sorted by spy_wk desc)
    sorted_by_spywk = sorted(sorted_events,
                             key=lambda x: (x[1]['spy_wk'] or 0), reverse=True)
    print(f"{'Date':<12}  {'N_puts':>6}  {'N_calls':>7}  {'SPY_wk':>7}  "
          f"{'VIX':>6}  {'Breadth':>8}  {'Mult_B':>7}")
    print('-' * 70)
    for d, info in sorted_by_spywk[:25]:
        spy_wk_str = f"{info['spy_wk']:.0f}" if info['spy_wk'] is not None else "  --"
        vix_str    = f"{info['vix']:.1f}"    if info['vix']    is not None else "  --"
        brd_str    = f"{info['breadth']:.0f}" if info['breadth'] is not None else "  --"
        print(f"{str(d):<12}  {info['n_puts']:>6}  {info['n_calls']:>7}  "
              f"{spy_wk_str:>7}  {vix_str:>6}  {brd_str:>8}  {info['mult_B']:>7.3f}")
    if len(sorted_by_spywk) > 25:
        print(f"  ... ({len(sorted_by_spywk) - 25} more dates not shown)")
    print()

    # For each variant, compute: (a) how many put peaks qualify, (b) WR on those peaks
    all_put_peaks = [pk for info in event_dates.values() for pk in info['put_peaks']]
    print(f"Total put peaks on event days: {len(all_put_peaks)}")
    print()

    print(f"{'Variant':<20}  {'N_puts':>8}  {'vs B':>7}  {'WR'+period:>8}  {'Delta':>7}")
    print('-' * 60)

    b_n = 0
    b_wr = None

    for v_label, v_fn in VARIANTS:
        qualifying = []
        for pk in all_put_peaks:
            reg = regime_cache.get(pk['date'])
            mult = v_fn(reg) if reg else 1.0
            new_score = _apply_to_score(pk['pre_regime'], mult)
            if new_score <= PUT_THRESHOLD:
                price_rows = price_cache.get(pk['symbol'], [])
                r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
                if r is not None:
                    qualifying.append(r)

        n = len(qualifying)
        wr = _win_rate(qualifying, period)

        if v_label == 'B: current':
            b_n  = n
            b_wr = wr
            n_str  = f"{n:>8}"
            vs_str = "  (ref)"
        else:
            n_delta = n - b_n
            n_str  = f"{n:>8}"
            vs_str = f"  {'+' if n_delta >= 0 else ''}{n_delta}"

        wr_str = _wr_fmt(wr)
        delta  = (wr - b_wr) if (wr is not None and b_wr is not None) else None
        d_str  = _delta_fmt(delta) if v_label != 'B: current' else ""
        print(f"  {v_label:<20}  {n_str}  {vs_str:>7}  {wr_str}  {d_str}")

    print()
    print(f"  GOAL: N_puts should DECREASE on event days when SPY_wk is HIGH.")
    print(f"  If variants J1-J5 show fewer qualifying puts (negative vs B),")
    print(f"  the SPY_wk blend is successfully suppressing false signals.")

    # Split by SPY_wk level: high vs low
    high_spywk_days = {d: info for d, info in event_dates.items()
                       if info['spy_wk'] is not None and info['spy_wk'] >= 65}
    low_spywk_days  = {d: info for d, info in event_dates.items()
                       if info['spy_wk'] is None or info['spy_wk'] < 65}

    if high_spywk_days:
        print(f"\n  --- High SPY_wk event days (spy_wk ≥ 65, n={len(high_spywk_days)} dates) ---")
        high_peaks = [pk for info in high_spywk_days.values() for pk in info['put_peaks']]
        print(f"  {'Variant':<20}  {'N_puts':>8}  {'vs B':>7}  {'WR'+period:>8}  {'Delta':>7}")
        b_n_h, b_wr_h = 0, None
        for v_label, v_fn in VARIANTS:
            qualifying = []
            for pk in high_peaks:
                reg = regime_cache.get(pk['date'])
                mult = v_fn(reg) if reg else 1.0
                new_score = _apply_to_score(pk['pre_regime'], mult)
                if new_score <= PUT_THRESHOLD:
                    price_rows = price_cache.get(pk['symbol'], [])
                    r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
                    if r is not None:
                        qualifying.append(r)
            n   = len(qualifying)
            wr  = _win_rate(qualifying, period)
            if v_label == 'B: current':
                b_n_h, b_wr_h = n, wr
                vs_s = "  (ref)"
            else:
                vs_s = f"  {'+' if n - b_n_h >= 0 else ''}{n - b_n_h}"
            d_s = _delta_fmt((wr - b_wr_h) if wr and b_wr_h else None) if v_label != 'B: current' else ""
            print(f"  {v_label:<20}  {n:>8}  {vs_s:>7}  {_wr_fmt(wr)}  {d_s}")

    if low_spywk_days:
        print(f"\n  --- Low SPY_wk event days (spy_wk < 65, n={len(low_spywk_days)} dates) ---")
        low_peaks = [pk for info in low_spywk_days.values() for pk in info['put_peaks']]
        print(f"  {'Variant':<20}  {'N_puts':>8}  {'vs B':>7}  {'WR'+period:>8}  {'Delta':>7}")
        b_n_l, b_wr_l = 0, None
        for v_label, v_fn in VARIANTS:
            qualifying = []
            for pk in low_peaks:
                reg = regime_cache.get(pk['date'])
                mult = v_fn(reg) if reg else 1.0
                new_score = _apply_to_score(pk['pre_regime'], mult)
                if new_score <= PUT_THRESHOLD:
                    price_rows = price_cache.get(pk['symbol'], [])
                    r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
                    if r is not None:
                        qualifying.append(r)
            n   = len(qualifying)
            wr  = _win_rate(qualifying, period)
            if v_label == 'B: current':
                b_n_l, b_wr_l = n, wr
                vs_s = "  (ref)"
            else:
                vs_s = f"  {'+' if n - b_n_l >= 0 else ''}{n - b_n_l}"
            d_s = _delta_fmt((wr - b_wr_l) if wr and b_wr_l else None) if v_label != 'B: current' else ""
            print(f"  {v_label:<20}  {n:>8}  {vs_s:>7}  {_wr_fmt(wr)}  {d_s}")


# -- Composite delta diagnostic -----------------------------------------------

def _print_composite_shift(regime_cache: Dict) -> None:
    """Show average composite shift from SPY_wk blend on high vs low SPY_wk days."""
    print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Composite Shift Diagnostic — how much does SPY_wk move the composite?{Style.RESET_ALL}")
    print(f"  (On days where multiplier is already computed by production variant B)")
    print(f"\n  SPY_wk level  |  N dates  |  Avg base comp  |  Avg delta @15%  |  Avg delta @25%")
    print(f"  {'-'*75}")

    buckets = {label: {'base': [], 'j2': [], 'j4': []} for _, _, label in SPY_WK_BUCKETS}

    j2_fn = _composite_spywk_blend(0.15)
    j4_fn = _composite_spywk_blend(0.25)

    for d, reg in regime_cache.items():
        spy_wk = reg.get('spy_wk')
        if spy_wk is None:
            continue
        base = _composite_current(reg)
        if base is None:
            continue
        c_j2 = j2_fn(reg)
        c_j4 = j4_fn(reg)

        for lo, hi, label in SPY_WK_BUCKETS:
            if lo <= spy_wk < hi:
                buckets[label]['base'].append(base)
                buckets[label]['j2'].append(c_j2 - base if c_j2 is not None else 0)
                buckets[label]['j4'].append(c_j4 - base if c_j4 is not None else 0)
                break

    for _, _, label in SPY_WK_BUCKETS:
        b = buckets[label]
        n = len(b['base'])
        if n == 0:
            print(f"  {label}  {n:>9}  {'no data':>17}")
            continue
        avg_base = np.mean(b['base'])
        avg_j2   = np.mean(b['j2'])
        avg_j4   = np.mean(b['j4'])
        j2_col   = Fore.GREEN if avg_j2 > 0 else (Fore.RED if avg_j2 < -0.5 else Fore.WHITE)
        j4_col   = Fore.GREEN if avg_j4 > 0 else (Fore.RED if avg_j4 < -0.5 else Fore.WHITE)
        print(f"  {label}  {n:>9}  {avg_base:>17.1f}  "
              f"{j2_col}{avg_j2:>+18.2f}{Style.RESET_ALL}  "
              f"{j4_col}{avg_j4:>+16.2f}{Style.RESET_ALL}")

    print(f"\n  Positive delta = composite RAISED = multiplier boosted = puts suppressed ✓")
    print(f"  Negative delta = composite LOWERED = multiplier cut = puts amplified ✗")


# -- Asymmetric put-dominated day analysis -----------------------------------

def _print_put_dominated_analysis_asym(
    event_dates: Dict[date, dict],
    peaks_raw: List[dict],
    regime_cache: Dict,
    price_cache: Dict,
    period: str = '15d',
) -> None:
    """Put-dominated day analysis for asymmetric variants (put_fn only; calls unchanged).

    Extracts put_fn from VARIANTS_ASYM and applies it to put peaks on event days.
    The key insight: call counts/WR are identical across all JA variants — only
    put N and WR change, making this a clean test of the suppression hypothesis.
    """
    put_fns = [(label, put_fn) for label, _, put_fn in VARIANTS_ASYM]

    all_put_peaks = [pk for info in event_dates.values() for pk in info['put_peaks']]

    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Put-Dominated Days — ASYMMETRIC Variants (put_fn only; calls always use B){Style.RESET_ALL}")
    print(f"  {len(event_dates)} event dates, {len(all_put_peaks)} total put peaks on those days")
    print(f"  Negative 'vs JA' = variant suppressed borderline puts back above ≤25 threshold ✓")

    print(f"\n  {'Variant':<25}  {'N_puts':>8}  {'vs JA':>7}  {'WR'+period:>8}  {'Delta':>7}")
    print('  ' + '-' * 65)

    b_n, b_wr = 0, None

    for v_label, put_fn in put_fns:
        qualifying = []
        for pk in all_put_peaks:
            reg = regime_cache.get(pk['date'])
            mult = put_fn(reg) if reg else 1.0
            new_score = _apply_to_score(pk['pre_regime'], mult)
            if new_score <= PUT_THRESHOLD:
                price_rows = price_cache.get(pk['symbol'], [])
                r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
                if r is not None:
                    qualifying.append(r)

        n   = len(qualifying)
        wr  = _win_rate(qualifying, period)

        if v_label == 'JA: B (asym reference)':
            b_n, b_wr = n, wr
            vs_s  = "  (ref)"
            d_str = ""
        else:
            vs_s  = f"  {'+' if n - b_n >= 0 else ''}{n - b_n}"
            d_str = _delta_fmt((wr - b_wr) if (wr is not None and b_wr is not None) else None)

        print(f"  {v_label:<25}  {n:>8}  {vs_s:>7}  {_wr_fmt(wr)}  {d_str}")

    # Sub-split: high vs low SPY_wk event days
    high_spywk_days = {d: info for d, info in event_dates.items()
                       if info['spy_wk'] is not None and info['spy_wk'] >= 65}
    low_spywk_days  = {d: info for d, info in event_dates.items()
                       if info['spy_wk'] is None or info['spy_wk'] < 65}

    if high_spywk_days:
        high_peaks = [pk for info in high_spywk_days.values() for pk in info['put_peaks']]
        print(f"\n  --- High SPY_wk event days (spy_wk ≥ 65, n={len(high_spywk_days)} dates, "
              f"{len(high_peaks)} peaks) — TARGET: suppression should concentrate here ---")
        print(f"  {'Variant':<25}  {'N_puts':>8}  {'vs JA':>7}  {'WR'+period:>8}  {'Delta':>7}")
        b_n_h, b_wr_h = 0, None
        for v_label, put_fn in put_fns:
            qualifying = []
            for pk in high_peaks:
                reg = regime_cache.get(pk['date'])
                mult = put_fn(reg) if reg else 1.0
                new_score = _apply_to_score(pk['pre_regime'], mult)
                if new_score <= PUT_THRESHOLD:
                    price_rows = price_cache.get(pk['symbol'], [])
                    r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
                    if r is not None:
                        qualifying.append(r)
            n   = len(qualifying)
            wr  = _win_rate(qualifying, period)
            if v_label == 'JA: B (asym reference)':
                b_n_h, b_wr_h = n, wr
                vs_s  = "  (ref)"
                d_str = ""
            else:
                vs_s  = f"  {'+' if n - b_n_h >= 0 else ''}{n - b_n_h}"
                d_str = _delta_fmt((wr - b_wr_h) if (wr is not None and b_wr_h is not None) else None)
            print(f"  {v_label:<25}  {n:>8}  {vs_s:>7}  {_wr_fmt(wr)}  {d_str}")

    if low_spywk_days:
        low_peaks = [pk for info in low_spywk_days.values() for pk in info['put_peaks']]
        print(f"\n  --- Low SPY_wk event days (spy_wk < 65, n={len(low_spywk_days)} dates, "
              f"{len(low_peaks)} peaks) — GUARD: suppression should NOT fire here ---")
        print(f"  {'Variant':<25}  {'N_puts':>8}  {'vs JA':>7}  {'WR'+period:>8}  {'Delta':>7}")
        b_n_l, b_wr_l = 0, None
        for v_label, put_fn in put_fns:
            qualifying = []
            for pk in low_peaks:
                reg = regime_cache.get(pk['date'])
                mult = put_fn(reg) if reg else 1.0
                new_score = _apply_to_score(pk['pre_regime'], mult)
                if new_score <= PUT_THRESHOLD:
                    price_rows = price_cache.get(pk['symbol'], [])
                    r = _run_barrier(pk['symbol'], pk['date'], new_score, price_rows)
                    if r is not None:
                        qualifying.append(r)
            n   = len(qualifying)
            wr  = _win_rate(qualifying, period)
            if v_label == 'JA: B (asym reference)':
                b_n_l, b_wr_l = n, wr
                vs_s  = "  (ref)"
                d_str = ""
            else:
                vs_s  = f"  {'+' if n - b_n_l >= 0 else ''}{n - b_n_l}"
                d_str = _delta_fmt((wr - b_wr_l) if (wr is not None and b_wr_l is not None) else None)
            print(f"  {v_label:<25}  {n:>8}  {vs_s:>7}  {_wr_fmt(wr)}  {d_str}")


# -- Main --------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    lookback_days = DEFAULT_LOOKBACK
    start_date: Optional[date] = None
    end_date:   Optional[date] = None

    for arg in args:
        if arg.startswith('--start='):
            start_date = date.fromisoformat(arg.split('=', 1)[1])
        elif arg.startswith('--end='):
            end_date = date.fromisoformat(arg.split('=', 1)[1])
        elif arg.isdigit():
            lookback_days = int(arg)

    cutoff = start_date if start_date else (date.today() - timedelta(days=lookback_days))

    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    if start_date or end_date:
        window_str = f"{cutoff} to {end_date or 'today'}"
        print(f"{Fore.CYAN}Regime SPY Weekly Test — date window {window_str}{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}Regime SPY Weekly Test — {lookback_days}d lookback  (cutoff {cutoff}){Style.RESET_ALL}")
    print(f"  Variants: {', '.join(v[0] for v in VARIANTS)}")
    print(f"  Baseline for deltas: B: current")
    print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}")

    # 1. Active algorithm version
    version = _load_active_version()
    print(f"\nAlgorithm version: {version.git_commit[:8] if version else 'unknown'} "
          f"({version.git_message[:60] if version else ''})")

    # 2. Load peaks
    print(f"\nLoading peaks...")
    peaks_raw = _load_peaks_with_pre_regime(lookback_days, version,
                                            start_date=cutoff, end_date=end_date)
    symbols = {p['symbol'] for p in peaks_raw}
    print(f"  {len(peaks_raw)} qualifying peaks  ({len(symbols)} symbols)")

    if not peaks_raw:
        print(f"{Fore.RED}No peaks found. Run trader update + recalculate first.{Style.RESET_ALL}")
        return

    # 3. Load regime cache + SPY weekly
    print(f"\nLoading market context...")
    regime_cache = _load_regime_cache(cutoff)
    print(f"  {len(regime_cache)} regime dates")

    print(f"  Loading SPY weekly composite scores...")
    spy_wk_lookup, n_spy_wk = _load_spy_wk_forward_fill(cutoff)

    if n_spy_wk == 0:
        print(f"{Fore.RED}SPY weekly scores not available. "
              f"Run 'trader update' to ensure WeeklyScore table is populated.{Style.RESET_ALL}")
        # Continue without spy_wk — J variants will fall back to current composite
    else:
        _augment_regime_cache_with_spywk(regime_cache, spy_wk_lookup)

    # 4. Load price data
    print(f"  Loading price history for {len(symbols)} symbols...")
    price_cache = _load_price_cache(symbols, cutoff)

    # 5. Run all variants
    print(f"\nRunning variants...")
    variant_results: Dict[str, Dict] = {}
    for v_label, v_fn in VARIANTS:
        print(f"  {v_label}...", end=' ', flush=True)
        res = _run_variant(v_label, v_fn, peaks_raw, regime_cache, price_cache, None)
        variant_results[v_label] = res
        total = sum(len(v) for v in res.values())
        print(f"{total} peaks")

    # 6. Standard output — peak counts + WR tables
    _print_pearson_summary(variant_results)
    _print_variant_table(variant_results, baseline_key='B: current', period='15d')
    _print_variant_table(variant_results, baseline_key='B: current', period='30d')

    # 7. Detailed breakdown for B and best J variants
    for v_label in ['B: current', 'J2: spywk_15pct', 'J4: spywk_25pct']:
        if v_label in variant_results:
            _print_detailed_variant(v_label, variant_results[v_label])

    # 8. Composite shift diagnostic
    _print_composite_shift(regime_cache)

    # 9. SPY_wk conditional analysis (put peaks split by SPY_wk level)
    if n_spy_wk > 0:
        b_fn = dict(VARIANTS).get('B: current')
        _print_spywk_conditional(variant_results, regime_cache, peaks_raw, price_cache, period='15d')

    # 10. Put-dominated day analysis
    print(f"\nIdentifying put-dominated days...")
    event_dates = _find_put_dominated_dates(peaks_raw, regime_cache)
    print(f"  Found {len(event_dates)} put-dominated dates")

    if event_dates:
        _print_put_dominated_analysis(event_dates, peaks_raw, regime_cache, price_cache, period='15d')

    # 11. Asymmetric variants — SPY_wk on puts only, calls unchanged from B
    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}ASYMMETRIC VARIANTS — SPY_wk applied to put multiplier only{Style.RESET_ALL}")
    print(f"  Calls always use the standard B composite (no SPY_wk influence).")
    print(f"  This eliminates call dilution while still suppressing false puts on recovery days.")
    print(f"  Variants: {', '.join(v[0] for v in VARIANTS_ASYM)}")
    print(f"  Baseline for deltas: JA: B (asym reference)")
    print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}")

    print(f"\nRunning asymmetric variants...")
    asym_results: Dict[str, Dict] = {}
    for v_label, call_fn, put_fn in VARIANTS_ASYM:
        print(f"  {v_label}...", end=' ', flush=True)
        res = _run_variant_asym(v_label, call_fn, put_fn, peaks_raw, regime_cache, price_cache, None)
        asym_results[v_label] = res
        total = sum(len(v) for v in res.values())
        print(f"{total} peaks")

    _print_variant_table(asym_results, baseline_key='JA: B (asym reference)', period='15d')
    _print_variant_table(asym_results, baseline_key='JA: B (asym reference)', period='30d')

    # Detailed breakdown for JA baseline and best asymmetric candidates
    for v_label in ['JA: B (asym reference)', 'JA3: put_20pct', 'JA4: put_25pct']:
        if v_label in asym_results:
            _print_detailed_variant(v_label, asym_results[v_label])

    # Asymmetric put-dominated day analysis
    if event_dates:
        _print_put_dominated_analysis_asym(event_dates, peaks_raw, regime_cache, price_cache, period='15d')

    # 12. Interpretation guide
    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Interpretation Guide{Style.RESET_ALL}")
    print(f"  B vs J variants: J variants add SPY_wk to the composite.")
    print(f"  Higher SPY_wk → composite raised → multiplier raised → puts suppressed.")
    print()
    print(f"  What 'good' looks like:")
    print(f"    - Call WR15: J variants near B (SPY_wk shouldn't hurt calls)")
    print(f"    - Put WR15: J variants slightly HIGHER than B (suppressing bad puts improves quality)")
    print(f"    - Put N: J variants LOWER than B on high-SPY_wk days (fewer false signals qualify)")
    print(f"    - SPY_wk≥65 put events: WR15 should INCREASE under J variants")
    print()
    print(f"  What 'bad' looks like:")
    print(f"    - Call WR15: meaningful regression (J is suppressing good call signals)")
    print(f"    - Put WR15 on LOW SPY_wk days: decrease (J is hurting genuine bearish puts)")
    print(f"    - J6 (inverted) outperforms J2-J5: direction assumption is wrong")
    print()
    print(f"  Primary shipping criterion:")
    print(f"    Call 70+ WR15 ≥ B with δ ≥ -0.5pp  AND  Put <25 WR15 ≥ B + 0.5pp")
    print(f"    AND  Put N on high-SPY_wk event days < B (i.e., fewer false signals)")
    print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}\n")


if __name__ == '__main__':
    main()
