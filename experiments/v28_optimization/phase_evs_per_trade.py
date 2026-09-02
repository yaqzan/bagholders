"""Phase EVS — Earnings Volume Suppression: per-trade A/B (Step 1 of 3).

Tests whether the current binary `±1d` earnings suppression in volume_amplifier
should be:
  V0 - kept as-is (current production semantic)
  V1 - removed entirely (does v28's score-stage earnings boost obviate it?)
  V2-V7 - replaced by a log-gradient blend (smooth ramp from neutral at d=0
          back to full amplifier at d=W)

Mechanism (gradient variants):
    nearest_d  = (nearest earnings date) - today, in calendar days
                 (positive => earnings is upcoming; negative => earnings just passed)
    if |nearest_d| <= W:
        if pre_only  and nearest_d  < 0: skip suppression
        if post_only and nearest_d  > 0: skip suppression
        s = MAX_SUPP * log(W + 1 - |nearest_d|) / log(W + 1)
        vol_mult_new   = vol_mult   * (1 - s) + 1.0 * s
        vol_mag_new    = vol_mag    * (1 - s)
        blend_w_new    = blend_w    * (1 - s)

The log shape mirrors v28's earnings-boost proximity formula
(`log(W+1-d)/log(W+1)`) so the two stages compose on the same curve.

Decision gate for this step
---------------------------
Per-trade results are NOT a ship signal (v24 lesson - portfolio MC required).
What we want here:
  (a) V1 (no suppression) within +/-0.5pp WR15 on 70+ AND <25 buckets
      => the score-stage v28 boost may already absorb the noise filter, and
         the simpler V1 becomes a strong baseline for canonical MC.
  (b) Any gradient variant that lifts WR15 vs V0 on either side
      => carry into Step 2 (Bayesian N=100 screen on 22-now x 3 modes).

Run
---
    python -m experiments.v28_optimization.phase_evs_per_trade           # default 730d (2y)
    python -m experiments.v28_optimization.phase_evs_per_trade 1825      # 5y
    python -m experiments.v28_optimization.phase_evs_per_trade 3y        # 3y

The script monkey-patches `volume_amplifier.get_volume_multiplier_from_cache`
in-process, runs `simulator.simulate()` per variant, then `diff_assess`
against the active DB version.  No DB writes.
"""
from __future__ import annotations

import io
import math
import os
import sys
from datetime import timedelta

# UTF-8 stdout for Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import volume_amplifier as va


# ────────────────────────────────────────────────────────────────────────
# Variant configuration (mutated per-iteration via globals)
# ────────────────────────────────────────────────────────────────────────

# Active strategy parameters - the wrapper reads these.
STRATEGY = 'binary'   # 'binary' | 'off' | 'log'
WINDOW_W = 1          # half-width in calendar days (only used by 'log')
MAX_SUPP = 1.0        # peak suppression strength at d=0 (only used by 'log')
DIRECTION = 'sym'     # 'sym' | 'pre' | 'post'  (only used by 'log')


def _supp_strength(nearest_d: int) -> float:
    """Return suppression strength s in [0, 1] for current STRATEGY."""
    abs_d = abs(nearest_d)

    if STRATEGY == 'off':
        return 0.0

    if STRATEGY == 'binary':
        return 1.0 if abs_d <= 1 else 0.0

    # 'log' gradient
    if abs_d > WINDOW_W:
        return 0.0
    if DIRECTION == 'pre' and nearest_d < 0:
        return 0.0
    if DIRECTION == 'post' and nearest_d > 0:
        return 0.0
    # log proximity, 1.0 at d=0, 0.0 at |d|=W
    return MAX_SUPP * math.log(WINDOW_W + 1 - abs_d) / math.log(WINDOW_W + 1)


# ────────────────────────────────────────────────────────────────────────
# Patched volume-amplifier accessor
# ────────────────────────────────────────────────────────────────────────

_orig_get_vol_mult = va.get_volume_multiplier_from_cache

# Empty set instance reused to disable original suppression branch.
_EMPTY_SET: frozenset = frozenset()


def patched_get_volume_multiplier_from_cache(
    today, ph_list_desc, prior_vol_signals, earnings_dates_set,
    pre_volume_overall=None,
):
    """Wraps the original.  Suppression is replaced by our gradient blend."""
    # Disable the original ±1d hard suppression by passing an empty set.
    result = _orig_get_vol_mult(
        today, ph_list_desc, prior_vol_signals, _EMPTY_SET, pre_volume_overall,
    )
    vol_mult, vol_raw, sig_type, vol_mag, blend_w, vol_target = result

    if not earnings_dates_set:
        return result

    # Find nearest earnings event within ±MAX_LOOK calendar days.
    MAX_LOOK = 7
    nearest_d = None
    for delta in range(-MAX_LOOK, MAX_LOOK + 1):
        if (today + timedelta(days=delta)) in earnings_dates_set:
            if nearest_d is None or abs(delta) < abs(nearest_d):
                nearest_d = delta

    if nearest_d is None:
        return result

    s = _supp_strength(nearest_d)
    if s <= 0.0:
        return result
    s = min(1.0, s)

    return (
        vol_mult * (1.0 - s) + 1.0 * s,    # blend toward NEUTRAL_RESULT[0]=1.0
        vol_raw,                             # categorical, leave as-is
        sig_type,                            # categorical, leave as-is
        vol_mag * (1.0 - s),                 # magnitude fades toward 0
        blend_w * (1.0 - s),                 # REVERSAL blend weight fades
        vol_target,                          # leave (only used when blend_w > 0)
    )


# ────────────────────────────────────────────────────────────────────────
# Variant table
# ────────────────────────────────────────────────────────────────────────

VARIANTS = [
    # (label,             strategy, W, max_supp, direction)
    ('V0_binary',        'binary',  1,  1.0,    'sym'),    # current production
    ('V1_off',           'off',     0,  0.0,    'sym'),    # is v28 enough?
    ('V2_log_W2_M10',    'log',     2,  1.0,    'sym'),    # smooth, narrow
    ('V3_log_W3_M10',    'log',     3,  1.0,    'sym'),    # smooth, wider
    ('V4_log_W2_M07',    'log',     2,  0.7,    'sym'),    # partial null at d=0
    ('V5_log_W3_M07',    'log',     3,  0.7,    'sym'),    # wider + partial
    ('V6_pre_W2_M10',    'log',     2,  1.0,    'pre'),    # anticipation-only
    ('V7_post_W2_M10',   'log',     2,  1.0,    'post'),   # post-news-only
]


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────

def _parse_days(tok: str) -> int:
    tok = tok.strip().lower()
    if tok.endswith('y'):
        return int(float(tok[:-1]) * 365)
    return int(tok)


def main():
    global STRATEGY, WINDOW_W, MAX_SUPP, DIRECTION

    days = 730  # 2y default for first iteration
    if len(sys.argv) > 1:
        days = _parse_days(sys.argv[1])

    print(f"[phase_evs] lookback={days}d", flush=True)

    # Activate the patch
    va.get_volume_multiplier_from_cache = patched_get_volume_multiplier_from_cache

    try:
        from simulator import ScoreSimulator
        from database.models.core import AlgorithmVersion

        active_v = AlgorithmVersion.get_active_scores_version()
        active_vid = active_v.id
        print(
            f"[phase_evs] active DB version: id={active_vid} "
            f"({active_v.git_commit[:10] if active_v.git_commit else 'none'})",
            flush=True,
        )

        # One simulator load - all variants reuse the same indicator/price data.
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for label, strat, w, m, direc in VARIANTS:
            STRATEGY = strat
            WINDOW_W = w
            MAX_SUPP = m
            DIRECTION = direc

            print("\n" + "=" * 100, flush=True)
            params = (
                f"strategy={strat} W={w} max_supp={m} dir={direc}"
                if strat == 'log' else f"strategy={strat}"
            )
            print(f"  VARIANT: {label}    {params}", flush=True)
            print("=" * 100, flush=True)

            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=active_vid)
            sys.stdout.flush()

    finally:
        # Always restore, even on exception
        va.get_volume_multiplier_from_cache = _orig_get_vol_mult


if __name__ == '__main__':
    main()
