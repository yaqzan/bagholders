"""Score-stage fix: dynamic put weekly amplification.

The portfolio entry filter (WW_B) drops puts where overall in [0,15] AND
weight_info.w_adj > -13. That works at portfolio scale (+169% 5y at N=500),
but is a downstream patch.

Cleaner alternative: prevent weak-weekly puts from reaching extreme bearish
territory IN THE FIRST PLACE by making the existing PUT_WEEKLY_SCALE = 1.5x
amplification *dynamic* — only apply full 1.5x when the weekly drag is itself
strong; weak weekly drags get less amplification.

Current production:
    if total < 0: total *= 1.5

Proposed:
    if total < 0:
        confidence = max(0, min(1, (|total| - 6) / 7))  # 0 at |total|<=6, 1 at |total|>=13
        scale = 1.0 + 0.5 * confidence    # ramps 1.0 (weak) -> 1.5 (strong)
        total *= scale

Effect on scoring:
  - Strong-weekly puts: identical to current (scale=1.5)
  - Moderate-weekly puts: partial amplification (1.0 - 1.5 ramp)
  - Weak-weekly puts: no amplification (scale=1.0) -> score stays closer to neutral

This naturally implements the user's "dynamic weekly requirement for puts" idea.

Variants tested vs current production weekly:
  - V_baseline: current 1.5x flat
  - V_dyn_a:    1.0->1.5 ramp at |total| in [6, 13]   (graded)
  - V_dyn_b:    1.0 if |total|<10 else 1.5            (binary)
  - V_dyn_c:    1.0->2.0 ramp at |total| in [6, 16]   (stronger high end)

Per-trade diff-assess against active DB version to compare bucket-wise WR.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import database.utils.scoring as scoring_mod

_ORIGINAL_CWA = scoring_mod.calculate_weekly_adjustment

VARIANT = 'baseline'  # set by main


def patched_cwa(trend, rsi, macd, prev_trend=None, prev_rsi=None, prev_macd=None):
    if None in (trend, rsi, macd):
        return 0.0, None

    composite = scoring_mod.calculate_weekly_composite(rsi, macd, trend) or 50

    deviation = (composite - 50) / 50
    base_bias = 15 * float(np.tanh(deviation * 1.5))

    signals = [(s - 50) / 50 for s in [trend, rsi, macd]]
    avg_signal = sum(signals) / len(signals)
    if abs(avg_signal) > 0.01:
        consistency = sum(max(0, s * np.sign(avg_signal)) for s in signals) / len(signals)
    else:
        consistency = 0
    agreement = 0.8 + 0.6 * consistency
    base_bias *= agreement

    momentum_bias = 0.0
    if None not in (prev_trend, prev_rsi, prev_macd):
        prev_composite = scoring_mod.calculate_weekly_composite(prev_rsi, prev_macd, prev_trend) or 50
        delta = composite - prev_composite
        momentum_bias = 8 * float(np.tanh(delta / 15))

    total = base_bias + momentum_bias

    # ── Variant logic on put-direction (total < 0) amplification ──
    if total < 0:
        if VARIANT == 'baseline':
            total *= 1.5  # current production
        elif VARIANT == 'dyn_a':
            # graded ramp 1.0 -> 1.5 over |total| in [6, 13]
            confidence = max(0.0, min(1.0, (abs(total) - 6.0) / 7.0))
            scale = 1.0 + 0.5 * confidence
            total *= scale
        elif VARIANT == 'dyn_b':
            # binary: 1.0 if weak, 1.5 if strong
            scale = 1.5 if abs(total) >= 10.0 else 1.0
            total *= scale
        elif VARIANT == 'dyn_c':
            # stronger high-end: ramp 1.0 -> 2.0 over [6, 16]
            confidence = max(0.0, min(1.0, (abs(total) - 6.0) / 10.0))
            scale = 1.0 + 1.0 * confidence
            total *= scale
        elif VARIANT == 'dyn_d':
            # only amplify when strong (>=10): step function
            scale = 1.5 if abs(total) >= 10.0 else 1.0
            total *= scale
            # plus extra boost for very strong (>=16)
            if abs(total / scale) >= 16.0:
                total *= 1.1
        else:
            total *= 1.5  # safe default

    detail = {
        'w_comp': round(composite, 1),
        'w_bias': round(base_bias, 1),
        'w_mom': round(momentum_bias, 1),
        'w_adj': round(total, 1)
    }
    return total, detail


def main():
    global VARIANT

    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    variants = [
        'baseline',  # = current 1.5x flat
        'dyn_a',     # 1.0->1.5 ramp [6, 13]
        'dyn_b',     # binary >=10 threshold
        'dyn_c',     # 1.0->2.0 ramp [6, 16]
    ]

    scoring_mod.calculate_weekly_adjustment = patched_cwa

    try:
        from simulator import ScoreSimulator
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for v in variants:
            VARIANT = v
            print("\n" + "=" * 100, flush=True)
            print(f"  VARIANT: {v}", flush=True)
            print("=" * 100, flush=True)
            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=26)
            sys.stdout.flush()
    finally:
        scoring_mod.calculate_weekly_adjustment = _ORIGINAL_CWA


if __name__ == '__main__':
    main()
