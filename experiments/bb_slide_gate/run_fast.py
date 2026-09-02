"""
Fast BB slide gate experiment runner.

Uses FastBBRunner: parquet-cached bulk load + multiprocessing + cached barrier
outcomes. Skips RSI/MACD/Stoch/TA recomputation (read from existing v31 scores).

Usage:
    python -m experiments.bb_slide_gate.run_fast              # 5y default
    python -m experiments.bb_slide_gate.run_fast 365          # 1y
    python -m experiments.bb_slide_gate.run_fast 1095         # 3y
    python -m experiments.bb_slide_gate.run_fast --refresh    # rebuild parquet caches
"""
from __future__ import annotations

import sys

import numpy as np

from experiments.bb_slide_gate.fast_bb_runner import FastBBRunner, print_bucket_diff


# ---------------------------------------------------------------------------
# BB scoring functions — must be top-level for pickling
# ---------------------------------------------------------------------------

def _bb_base(trend_score, indicator_window, ph_close_window):
    """Re-implementation of calculate_bollinger_bands_score that takes the
    indicator window directly (avoiding ORM lookups). Mirrors the existing
    production logic exactly. ORIGINAL — used for sanity checks."""
    return _bb_compute(trend_score, indicator_window, ph_close_window, gated=False)


def _bb_gated(trend_score, indicator_window, ph_close_window):
    """PATCHED: gate the upper-slide penalty by trend strength. In strong
    uptrends (trend_bias >= 0.8), the upper-slide penalty fades to zero."""
    return _bb_compute(trend_score, indicator_window, ph_close_window, gated=True)


def _bb_compute(trend_score, indicator_window, ph_close_window, gated: bool):
    """Shared BB scoring math. `gated=True` applies the trend-gated upper-slide.

    indicator_window: list of dicts (DESCENDING by date), keys: upper_band,
                      middle_band, lower_band, close.
    ph_close_window: parallel list of close prices (DESCENDING) — included
                      for API compat; in current implementation indicator_window
                      already has 'close'.
    """
    if len(indicator_window) < 5:
        return None

    cur = indicator_window[0]
    upper = cur.get('upper_band')
    lower = cur.get('lower_band')
    middle = cur.get('middle_band')
    price = cur.get('close')
    if None in (upper, lower, middle, price):
        return None
    upper, lower, middle, price = float(upper), float(lower), float(middle), float(price)
    band_range = upper - lower
    if band_range <= 0:
        return None

    bb_pct = (price - lower) / band_range

    trend_bias = float(np.tanh((trend_score - 50) * 0.06))
    trend_strength = abs(trend_bias)

    # === 1. BASE SCORE (70%) ===
    ideal = 0.50 + 0.15 * trend_bias
    sensitivity = 2.5 + 0.5 * trend_strength
    base = 50 + 40 * np.tanh((ideal - bb_pct) * sensitivity)

    # === 1b. V-RECOVERY / V-DROP DETECTION ===
    if len(indicator_window) >= 3:
        recent_pcts = []
        for ind in indicator_window[1:min(11, len(indicator_window))]:
            ub = ind.get('upper_band')
            lb = ind.get('lower_band')
            cp = ind.get('close')
            if None not in (ub, lb, cp):
                bw = float(ub) - float(lb)
                if bw > 0:
                    recent_pcts.append((float(cp) - float(lb)) / bw)
        if recent_pcts:
            bull_gate = max(0, trend_bias * 2)
            if bull_gate > 0 and bb_pct > 0.45:
                min_pct = min(recent_pcts)
                recovery = bb_pct - min_pct
                if min_pct < 0.25 and recovery > 0.3:
                    bonus = min(25, recovery * 35) * bull_gate
                    if bb_pct > 1.0:
                        bonus *= 0.7
                    base = max(base, 50 + bonus)
            bear_gate = max(0, -trend_bias * 2)
            if bear_gate > 0 and bb_pct < 0.55:
                max_pct = max(recent_pcts)
                drop = max_pct - bb_pct
                if max_pct > 0.75 and drop > 0.3:
                    penalty = min(25, drop * 35) * bear_gate
                    if bb_pct < 0.0:
                        penalty *= 0.7
                    base = min(base, 50 - penalty)

    # === 1c. TREND-STRENGTH FLOOR ===
    if trend_score > 50 and bb_pct > 0.7:
        ts = max(0, (trend_score - 50) / 50)
        base = max(base, 30 + 15 * ts)

    # === 2. SLIDE DETECTION (20%) ===
    slide_score = 50
    check_bars = min(len(indicator_window), 10)
    proximity_lower, proximity_upper = 0.0, 0.0
    for ind in indicator_window[:check_bars]:
        ub = ind.get('upper_band')
        lb = ind.get('lower_band')
        cp = ind.get('close')
        if None in (ub, lb, cp):
            continue
        bw = float(ub) - float(lb)
        if bw == 0:
            continue
        pct = (float(cp) - float(lb)) / bw
        proximity_lower += max(0, 1.0 - pct / 0.25)
        proximity_upper += max(0, (pct - 0.75) / 0.25)

    if check_bars > 0:
        slide_lower = proximity_lower / check_bars
        slide_upper = proximity_upper / check_bars
        if slide_lower > slide_upper:
            severity = 25 + 20 * max(0, -trend_bias)
            slide_score = 50 - severity * float(np.tanh(slide_lower * 3))
        elif slide_upper > slide_lower:
            if gated:
                # PATCHED: gate the upper-slide penalty by trend strength.
                trend_gate = max(0.0, 1.0 - max(0, trend_bias) / 0.8)
                severity = (25 + 15 * max(0, trend_bias)) * trend_gate
            else:
                severity = 25 + 15 * max(0, trend_bias)
            slide_score = 50 - severity * float(np.tanh(slide_upper * 3))
        else:
            slide_score = 55

    # === 3. SQUEEZE DETECTION (10%) ===
    squeeze_score = 50
    if len(indicator_window) >= 5:
        widths = []
        for ind in indicator_window[:check_bars]:
            ub = ind.get('upper_band')
            lb = ind.get('lower_band')
            if None not in (ub, lb):
                widths.append(float(ub) - float(lb))
        if len(widths) >= 5:
            avg_width = sum(widths) / len(widths)
            width_ratio = widths[0] / avg_width if avg_width > 0 else 1.0
            squeeze = max(0, (0.8 - width_ratio) / 0.3)
            expansion = max(0, (width_ratio - 1.3) / 0.3)
            squeeze_score = 50 + 10 * squeeze * trend_bias - 10 * expansion

    raw_score = base * 0.70 + slide_score * 0.20 + squeeze_score * 0.10
    return int(max(0, min(100, round(raw_score))))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    days = 1825
    refresh = False
    metric = 'wr'

    for i, a in enumerate(args):
        if a == '--refresh':
            refresh = True
        elif a == '--metric' and i + 1 < len(args):
            metric = args[i + 1]
        elif a.isdigit():
            days = int(a)

    print(f"\n{'=' * 72}")
    print(f"FAST BB SLIDE GATE — lookback={days}d, metric={metric}, refresh={refresh}")
    print(f"{'=' * 72}\n")

    runner = FastBBRunner(lookback_days=days, metric=metric)
    runner.load(verbose=True, refresh=refresh)

    # Sanity: baseline result (using stored v31 BB scores, no recompute)
    print("\n--- Baseline (stored v31 scores) ---")
    baseline = runner.evaluate_baseline(label='v31_DB')

    # Original BB (recomputed via our reimplementation) — should ~match baseline
    print("\n--- Original BB (reimplemented, no patch) ---")
    original_recomputed = runner.evaluate_bb_variant(_bb_base, label='v31_recomputed')

    # Patched BB
    print("\n--- Patched BB (trend-gated upper-slide) ---")
    patched = runner.evaluate_bb_variant(_bb_gated, label='trend-gated')

    # Diff: baseline vs patched
    print_bucket_diff(baseline, patched, label_b='v31_DB', label_v='gated')

    # Diff: baseline vs original_recomputed (sanity check — should be near-identical)
    print("\n--- Sanity: baseline vs original_recomputed (should be ~0 delta) ---")
    print_bucket_diff(baseline, original_recomputed, label_b='v31_DB', label_v='v31_recomp')


if __name__ == '__main__':
    main()
