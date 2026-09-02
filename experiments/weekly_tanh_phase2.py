"""Phase 2: Bayesian-style targeted tanh sweep.

Phase 1 (weekly_confidence_gate.py) tested 4 variants over a wide k range:
  baseline, tanh_AB (k=10/12), tanh_AB_sharp (k=7/8), tanh_AB_soft (k=14/16),
  conf_AB (threshold reference)

Phase 2 narrows around the Phase 1 best, varying k_put, peak_put, and
floor_call independently to identify which shape parameter dominates.

User priority: aggressive put N reduction toward call parity (currently
put:call ~4:1 at matched conviction). Per-trade WR matters MORE than N
preservation. Variants below explore the aggressive end of the gradient.

Strategy: 6 variants, 3 axes of variation:
  - k_put axis: 4, 5, 6, 8 (sharper than Phase 1 default 10)
  - peak_put axis: 1.25, 1.5, 1.75
  - floor_call axis: 0, 0.3, 0.5

Variants chosen to span the parameter space but be informative when
combined with Phase 1 data:
  P2_K5      k_put=5,  peak_put=1.5, k_call=8,  peak_call=1.0, floor_call=0.5
  P2_K6      k_put=6,  peak_put=1.5, k_call=8,  peak_call=1.0, floor_call=0.5
  P2_K4      k_put=4,  peak_put=1.5, k_call=8,  peak_call=1.0, floor_call=0.5  (extra-sharp)
  P2_PEAK2   k_put=8,  peak_put=2.0, k_call=8,  peak_call=1.0, floor_call=0.5  (higher peak)
  P2_CFLOOR0 k_put=8,  peak_put=1.5, k_call=8,  peak_call=1.0, floor_call=0.0  (call also aggressive)
  P2_PEAK125 k_put=8,  peak_put=1.25, k_call=8, peak_call=1.0, floor_call=0.5  (lower peak)
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import database.utils.scoring as scoring_mod

_ORIGINAL_CWA = scoring_mod.calculate_weekly_adjustment

# Variant config — set globally per run
K_PUT = 10.0
PEAK_PUT = 1.5
K_CALL = 12.0
PEAK_CALL = 1.0
FLOOR_CALL = 0.5
ENABLED = False  # baseline = disabled


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

    if not ENABLED:
        # Baseline: current production (1.5x flat on negative)
        if total < 0:
            total *= 1.5
    else:
        # Tanh-gated: smooth saturating, asymmetric
        abs_t = abs(total)
        if total < 0:
            scale = PEAK_PUT * float(np.tanh(abs_t / K_PUT))
        else:
            scale = FLOOR_CALL + (PEAK_CALL - FLOOR_CALL) * float(np.tanh(abs_t / K_CALL))
        total *= scale

    detail = {
        'w_comp': round(composite, 1),
        'w_bias': round(base_bias, 1),
        'w_mom': round(momentum_bias, 1),
        'w_adj': round(total, 1)
    }
    return total, detail


VARIANTS = [
    # (label,         enabled, k_put, peak_put, k_call, peak_call, floor_call)
    ('P2_baseline',   False,   10.0,  1.5,      12.0,   1.0,        0.5),  # current production
    ('P2_K5',         True,    5.0,   1.5,      8.0,    1.0,        0.5),
    ('P2_K6',         True,    6.0,   1.5,      8.0,    1.0,        0.5),
    ('P2_K4',         True,    4.0,   1.5,      8.0,    1.0,        0.5),
    ('P2_PEAK2',      True,    8.0,   2.0,      8.0,    1.0,        0.5),
    ('P2_CFLOOR0',    True,    8.0,   1.5,      8.0,    1.0,        0.0),
    ('P2_PEAK125',    True,    8.0,   1.25,     8.0,    1.0,        0.5),
]


def main():
    global K_PUT, PEAK_PUT, K_CALL, PEAK_CALL, FLOOR_CALL, ENABLED

    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    scoring_mod.calculate_weekly_adjustment = patched_cwa

    try:
        from simulator import ScoreSimulator
        from database.models.core import AlgorithmVersion
        active_v = AlgorithmVersion.get_active_scores_version()
        active_vid = active_v.id
        print(f"[setup] active DB version: id={active_vid} ({active_v.git_commit[:10]})")
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for v in VARIANTS:
            label, enabled, k_p, p_p, k_c, p_c, f_c = v
            ENABLED = enabled
            K_PUT = k_p
            PEAK_PUT = p_p
            K_CALL = k_c
            PEAK_CALL = p_c
            FLOOR_CALL = f_c

            print("\n" + "=" * 100, flush=True)
            params_str = (f"k_put={k_p} peak_put={p_p} k_call={k_c} peak_call={p_c} floor_call={f_c}"
                          if enabled else "(baseline 1.5x flat)")
            print(f"  VARIANT: {label}    {params_str}", flush=True)
            print("=" * 100, flush=True)
            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=active_vid)
            sys.stdout.flush()
    finally:
        scoring_mod.calculate_weekly_adjustment = _ORIGINAL_CWA


if __name__ == '__main__':
    main()
