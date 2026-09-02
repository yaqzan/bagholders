"""Phase 5b: simulator-path null-result revisits.

Tests stack the new floor lift K0.95_W-17_T50_G28 with previously-null mechanisms:

  B1: Floor lift + Phase 1 tanh weekly amplifier (k=10, k=12, k=15)
       - Phase 1 tanh weekly was per-trade null (-2.3pp on <10).
       - Hypothesis: with floor lift catching the dud puts post-score,
         pre-score weekly modulation may stack additively.

  B2: Floor lift + Phase 1 amp k=12 + lift_target=55
       - Combined extension test.

Each variant runs through the simulator (~12 min via fast_assess after first load).
Total Phase 5b: ~40-50 min for 4 variants.

Output: experiments/phase5b.out
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))

import database.utils.scoring as scoring_mod

_ORIG_CWA = scoring_mod.calculate_weekly_adjustment
_ORIG_COS = scoring_mod.compute_overall_score

# Variant config
TANH_K = None  # None = production weekly; else apply tanh scaling
FLOOR_K = 0.95
FLOOR_WCUT = -17.0
FLOOR_GATE = 28
FLOOR_TARGET = 50

MIS_STRESS_CALL_DAMPEN = scoring_mod.MIS_STRESS_CALL_DAMPEN


def patched_cwa(trend, rsi, macd, prev_trend=None, prev_rsi=None, prev_macd=None):
    """Calculate weekly adjustment with optional tanh amplifier modification."""
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

    if TANH_K is None:
        # Production: 1.5x for puts, 1.0x for calls
        if total < 0:
            total *= 1.5
    else:
        # Tanh-modulated weekly amplifier
        abs_t = abs(total)
        if total < 0:
            scale = 1.5 * float(np.tanh(abs_t / TANH_K))
        else:
            scale = 0.5 + 0.5 * float(np.tanh(abs_t / (TANH_K + 2)))
        total *= scale

    return total, {
        'w_comp': round(composite, 1), 'w_bias': round(base_bias, 1),
        'w_mom': round(momentum_bias, 1), 'w_adj': round(total, 1)
    }


def patched_compute_overall_score(*args, **kwargs):
    """Standard production scoring + new floor lift at the end."""
    overall, weight_info, vol_update = _ORIG_COS(*args, **kwargs)
    if overall is None:
        return overall, weight_info, vol_update

    # Apply floor lift
    if weight_info and 'w_adj' in weight_info:
        wadj = float(weight_info.get('w_adj', 0.0) or 0.0)
        if overall < FLOOR_GATE and wadj > FLOOR_WCUT:
            weakness = max(0.0, min(1.0, (wadj - FLOOR_WCUT) / abs(FLOOR_WCUT)))
            if weakness > 0:
                lift = FLOOR_K * weakness * (FLOOR_TARGET - overall)
                overall = int(min(100, max(0, round(overall + lift))))
                weight_info['floor_lift'] = round(lift, 2)

    return overall, weight_info, vol_update


def run_variant(label, tanh_k):
    global TANH_K
    TANH_K = tanh_k

    scoring_mod.calculate_weekly_adjustment = patched_cwa
    scoring_mod.compute_overall_score = patched_compute_overall_score

    try:
        from simulator import ScoreSimulator
        from database.models.core import AlgorithmVersion
        active_v = AlgorithmVersion.get_active_scores_version()
        active_vid = active_v.id

        print("\n" + "=" * 100, flush=True)
        param_str = f"floor=K{FLOOR_K}_W{FLOOR_WCUT}_G{FLOOR_GATE}_T{FLOOR_TARGET}"
        param_str += f", tanh_k={tanh_k}" if tanh_k is not None else ", weekly=production"
        print(f"  VARIANT: {label}    {param_str}", flush=True)
        print("=" * 100, flush=True)

        sim = ScoreSimulator(symbols=None, lookback_days=1825, scoring_fn=None)
        scores = sim.simulate()
        sys.stdout.flush()
        sim.diff_assess(scores, db_version=active_vid)
        sys.stdout.flush()
    finally:
        scoring_mod.calculate_weekly_adjustment = _ORIG_CWA
        scoring_mod.compute_overall_score = _ORIG_COS


def main():
    variants = [
        ('B0_floor_only',         None),    # baseline: floor lift only, production weekly
        ('B1a_floor_tanh_k10',    10.0),
        ('B1b_floor_tanh_k12',    12.0),
        ('B1c_floor_tanh_k15',    15.0),
    ]
    for label, tanh_k in variants:
        t0 = time.time()
        run_variant(label, tanh_k)
        print(f"  [{label}] elapsed: {time.time()-t0:.1f}s", flush=True)


if __name__ == '__main__':
    main()
