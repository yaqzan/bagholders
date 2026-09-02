"""Symmetric confidence-gated weekly amplifier.

User's formulation: scale weekly multiplier dynamically based on weekly's own
magnitude. Strong weekly = full influence; weak weekly = minimal influence.
Aggressive on puts (where existing 1.5x amplifier creates phantom amplification
of noise); mild on calls (smaller existing scale, smaller magnitude problem).

Mathematical form:
    abs_total  = |weekly_adj|
    confidence = clamp((abs_total - 6) / 7, 0, 1)   # 0 at <=6, 1 at >=13

    if total < 0:   # put direction
        scale = 0.0 + 1.5 * confidence              # 0 -> 1.5 ramp (aggressive)
    else:           # call direction
        scale = 0.5 + 0.5 * confidence              # 0.5 -> 1.0 ramp (mild)
    total *= scale

Variants tested:
  baseline    : current production (puts 1.5x flat, calls 1.0x flat)
  conf_AB     : asymmetric (puts floor=0, peak=1.5 / calls floor=0.5, peak=1.0)
  conf_AS     : aggressive symmetric (both floor=0)
  conf_MS     : mild symmetric (both floor=0.5)
  conf_AB_v2  : asymmetric with different threshold (low cutoff at 4 instead of 6)
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import database.utils.scoring as scoring_mod

_ORIGINAL_CWA = scoring_mod.calculate_weekly_adjustment

VARIANT = 'baseline'


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

    # ── VARIANT logic ──
    if VARIANT == 'baseline':
        # Current production: 1.5x on negative, 1.0x on positive
        if total < 0:
            total *= 1.5
        # else: total *= 1.0 (no-op)

    elif VARIANT == 'conf_AB':
        # Asymmetric: aggressive puts (0->1.5), mild calls (0.5->1.0)
        abs_t = abs(total)
        confidence = max(0.0, min(1.0, (abs_t - 6.0) / 7.0))
        if total < 0:
            scale = 0.0 + 1.5 * confidence
        else:
            scale = 0.5 + 0.5 * confidence
        total *= scale

    elif VARIANT == 'conf_AS':
        # Aggressive symmetric: both sides 0->peak
        abs_t = abs(total)
        confidence = max(0.0, min(1.0, (abs_t - 6.0) / 7.0))
        if total < 0:
            scale = 0.0 + 1.5 * confidence
        else:
            scale = 0.0 + 1.0 * confidence
        total *= scale

    elif VARIANT == 'conf_MS':
        # Mild symmetric: both sides floor=0.5
        abs_t = abs(total)
        confidence = max(0.0, min(1.0, (abs_t - 6.0) / 7.0))
        if total < 0:
            scale = 0.5 + 1.0 * confidence  # 0.5 -> 1.5
        else:
            scale = 0.5 + 0.5 * confidence  # 0.5 -> 1.0
        total *= scale

    elif VARIANT == 'conf_AB_v2':
        # Asymmetric with broader confidence ramp (0..1 over [4, 14])
        abs_t = abs(total)
        confidence = max(0.0, min(1.0, (abs_t - 4.0) / 10.0))
        if total < 0:
            scale = 0.0 + 1.5 * confidence
        else:
            scale = 0.5 + 0.5 * confidence
        total *= scale

    elif VARIANT == 'tanh_AB':
        # tanh-based smooth scaling, asymmetric (puts aggressive, calls mild)
        # No thresholds; saturating monotonic.
        abs_t = abs(total)
        if total < 0:
            # Put: scale ramps 0 -> 1.5 with k=10
            scale = 1.5 * float(np.tanh(abs_t / 10.0))
        else:
            # Call: scale ramps 0.5 -> 1.0 with k=12
            scale = 0.5 + 0.5 * float(np.tanh(abs_t / 12.0))
        total *= scale

    elif VARIANT == 'tanh_AB_sharp':
        # Same shape but sharper transition (k=7 puts, k=8 calls)
        abs_t = abs(total)
        if total < 0:
            scale = 1.5 * float(np.tanh(abs_t / 7.0))
        else:
            scale = 0.5 + 0.5 * float(np.tanh(abs_t / 8.0))
        total *= scale

    elif VARIANT == 'tanh_AB_soft':
        # Same shape but softer transition (k=14 puts, k=16 calls)
        abs_t = abs(total)
        if total < 0:
            scale = 1.5 * float(np.tanh(abs_t / 14.0))
        else:
            scale = 0.5 + 0.5 * float(np.tanh(abs_t / 16.0))
        total *= scale

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
        'baseline',
        'tanh_AB',           # tanh smooth, asymmetric (PRIMARY candidate)
        'tanh_AB_sharp',     # tanh, sharper k
        'tanh_AB_soft',      # tanh, softer k
        'conf_AB',           # piecewise-linear comparison
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
