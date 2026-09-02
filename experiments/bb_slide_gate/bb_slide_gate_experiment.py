"""
BB Slide Gate experiment — gate the upper-slide penalty by trend strength.

Hypothesis
----------
In `Stock.calculate_bollinger_bands_score`, the BOLLINGER SLIDE DETECTION
section penalizes recent upper-band proximity as a "topping/distribution"
signal. The penalty severity actually INCREASES with trend_bias:
    severity = 25 + 15 * max(0, trend_bias)

But in confirmed uptrends (trend_bias ≥ 0.8), upper-band proximity is
*expected* momentum behavior, not topping. After the stock pulls back
to the middle band, the BASE score correctly identifies the buy zone
(bb_pct ≈ 0.50-0.65 vs ideal=0.65 in strong uptrend → base ~60-70).
The upper-slide penalty then drags slide_score down to ~14-30, pulling
the BB component score from ~64 down to ~50 — exactly what was observed
on RKLB/IRM Apr 29 2026.

Fix
---
Gate the upper-slide penalty by a trend_gate that fades to 0 in strong
uptrends:

    trend_gate = max(0.0, 1.0 - max(0, trend_bias) / 0.8)
    severity = (25 + 15 * max(0, trend_bias)) * trend_gate

At trend_bias=0:    severity=25 (full penalty - neutral upper-hug IS warning)
At trend_bias=0.4:  severity=15.5 (mild penalty)
At trend_bias=0.8:  severity=0 (no penalty - confirmed uptrend)
At trend_bias=0.99: severity=0 (no penalty - strong uptrend)

The lower-slide penalty (bearish drift) is left untouched.

Usage
-----
    python -m experiments.bb_slide_gate.bb_slide_gate_experiment             # 5y full universe
    python -m experiments.bb_slide_gate.bb_slide_gate_experiment 1095        # 3y
    python -m experiments.bb_slide_gate.bb_slide_gate_experiment 365         # 1y
    python -m experiments.bb_slide_gate.bb_slide_gate_experiment RKLB IRM 90 # spot check on specific symbols
"""
from __future__ import annotations

import sys
import numpy as np

from database.models.core import Stock, AlgorithmVersion
from database.models.technical import Indicator, WeeklyIndicator
from simulator import ScoreSimulator


_ORIGINAL_BB = Stock.calculate_bollinger_bands_score


def patched_bb_score(
    self,
    target_date,
    lookback=10,
    weekly=False,
    trend_score=None,
    _ind_cache=None,
    _ph_cache=None,
):
    """Trend-gated upper-slide penalty. All other logic identical to original."""
    IndicatorModel = WeeklyIndicator if weekly else Indicator
    if _ind_cache is not None:
        indicators = _ind_cache[: lookback + 1]
    else:
        indicators_query = self.weekly_indicators if weekly else self.indicators
        indicators = list(
            indicators_query
            .where(IndicatorModel.date <= target_date)
            .order_by(IndicatorModel.date.desc())
            .limit(lookback + 1)
        )

    if len(indicators) < 5:
        return None

    current = indicators[0]
    ph = (_ph_cache.get(current.date) if _ph_cache is not None
          else current.price_history())
    price = float(ph.close) if ph and ph.close else None
    if None in (current.upper_band, current.lower_band, current.middle_band, price):
        return None

    upper = float(current.upper_band)
    lower = float(current.lower_band)
    middle = float(current.middle_band)
    band_range = upper - lower
    if band_range == 0:
        return None

    bb_pct = (price - lower) / band_range

    if trend_score is None:
        trend_score = 50

    trend_bias = float(np.tanh((trend_score - 50) * 0.06))
    trend_strength = abs(trend_bias)

    # === 1. TREND-AWARE BASE SCORE (70%) === (unchanged)
    ideal = 0.50 + 0.15 * trend_bias
    sensitivity = 2.5 + 0.5 * trend_strength
    base = 50 + 40 * np.tanh((ideal - bb_pct) * sensitivity)

    # === 1b. V-RECOVERY / V-DROP DETECTION === (unchanged)
    if len(indicators) >= 3:
        recent_pcts = []
        for ind in indicators[1:min(11, len(indicators))]:
            p = (_ph_cache.get(ind.date) if _ph_cache is not None
                 else ind.price_history())
            if p and None not in (ind.upper_band, ind.lower_band):
                bw = float(ind.upper_band) - float(ind.lower_band)
                if bw > 0:
                    recent_pcts.append((float(p.close) - float(ind.lower_band)) / bw)
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

    # === 1c. TREND-STRENGTH FLOOR === (unchanged)
    if trend_score > 50 and bb_pct > 0.7:
        ts = max(0, (trend_score - 50) / 50)
        base = max(base, 30 + 15 * ts)

    # === 2. BOLLINGER SLIDE DETECTION (20%) === (PATCHED)
    slide_score = 50
    check_bars = min(len(indicators), lookback)
    proximity_lower, proximity_upper = 0.0, 0.0

    for ind in indicators[:check_bars]:
        p = (_ph_cache.get(ind.date) if _ph_cache is not None
             else ind.price_history())
        if not p or None in (ind.upper_band, ind.lower_band):
            continue
        cp = float(p.close)
        bw = float(ind.upper_band) - float(ind.lower_band)
        if bw == 0:
            continue
        pct = (cp - float(ind.lower_band)) / bw
        proximity_lower += max(0, 1.0 - pct / 0.25)
        proximity_upper += max(0, (pct - 0.75) / 0.25)

    if check_bars > 0:
        slide_lower = proximity_lower / check_bars
        slide_upper = proximity_upper / check_bars
        if slide_lower > slide_upper:
            # Lower-slide (bearish drift): unchanged.
            severity = 25 + 20 * max(0, -trend_bias)
            slide_score = 50 - severity * float(np.tanh(slide_lower * 3))
        elif slide_upper > slide_lower:
            # *** PATCH: gate upper-slide penalty by trend strength. ***
            # In confirmed uptrends (trend_bias >= 0.8), recent upper-band
            # proximity is healthy momentum. The base score already values
            # the current bb_pct correctly; an upper-slide penalty on top
            # misfires on healthy pullbacks (e.g. RKLB Apr 29 2026).
            trend_gate = max(0.0, 1.0 - max(0, trend_bias) / 0.8)
            severity = (25 + 15 * max(0, trend_bias)) * trend_gate
            slide_score = 50 - severity * float(np.tanh(slide_upper * 3))
        else:
            slide_score = 55

    # === 3. SQUEEZE DETECTION (10%) === (unchanged)
    squeeze_score = 50
    if len(indicators) >= 5:
        widths = []
        for ind in indicators[:check_bars]:
            if None in (ind.upper_band, ind.lower_band):
                continue
            widths.append(float(ind.upper_band) - float(ind.lower_band))
        if len(widths) >= 5:
            avg_width = sum(widths) / len(widths)
            width_ratio = widths[0] / avg_width if avg_width > 0 else 1.0
            squeeze = max(0, (0.8 - width_ratio) / 0.3)
            expansion = max(0, (width_ratio - 1.3) / 0.3)
            squeeze_score = 50 + 10 * squeeze * trend_bias - 10 * expansion

    raw_score = base * 0.70 + slide_score * 0.20 + squeeze_score * 0.10
    return int(max(0, min(100, round(raw_score))))


def main():
    args = sys.argv[1:]
    symbols = []
    days = 1825  # 5y default
    metric = 'wr'  # 'wr' (default) or 'tp'

    i = 0
    while i < len(args):
        a = args[i]
        if a == '--metric':
            metric = args[i + 1]
            i += 2
            continue
        if a.isdigit():
            days = int(a)
        elif a.endswith('y'):
            days = int(a[:-1]) * 365
        elif a.endswith('d'):
            days = int(a[:-1])
        else:
            symbols.append(a.upper())
        i += 1

    print(f"\n{'='*72}")
    print(f"BB SLIDE GATE EXPERIMENT — trend-gated upper-slide penalty")
    print(f"{'='*72}")
    print(f"Lookback: {days}d  |  Symbols: {symbols if symbols else 'ALL'}  |  Metric: {metric}")
    print(f"Active version: {AlgorithmVersion.get_active_scores_version().git_commit}")
    print(f"{'='*72}\n")

    # If metric=tp, set option-aligned barriers
    if metric == 'tp':
        from assess_scores import set_dte_strategy
        prev = set_dte_strategy('30', 'tp')
        print(f"[metric=tp] set_dte_strategy('30', 'tp') applied — option-aligned K/M barriers active\n")

    # Apply the patch
    Stock.calculate_bollinger_bands_score = patched_bb_score
    print(f"[patched] Stock.calculate_bollinger_bands_score → trend-gated upper-slide\n")

    try:
        sim = ScoreSimulator(
            symbols=(symbols or None),
            lookback_days=days,
            scoring_fn=None,  # use unmodified compute_overall_score
        )
        sim_scores = sim.simulate()
        print(f"\n[sim] Generated {len(sim_scores)} scores\n")

        # Run diff-assess against active DB version (avoid version pollution)
        active = AlgorithmVersion.get_active_scores_version()
        sim.diff_assess(sim_scores, symbol=None, db_version=active)
    finally:
        Stock.calculate_bollinger_bands_score = _ORIGINAL_BB
        print(f"\n[restored] Stock.calculate_bollinger_bands_score → original\n")
        if metric == 'tp':
            from assess_scores import set_dte_strategy
            set_dte_strategy('30', 'wr')
            print(f"[restored] set_dte_strategy('30', 'wr') — barriers reset to default\n")


if __name__ == '__main__':
    main()
