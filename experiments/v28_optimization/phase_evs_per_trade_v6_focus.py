"""Phase EVS — V6 focus at 5y (Step 1b of 3).

Phase EVS per-trade @ 2y (`phase_evs_per_trade.py`) found:
  - V1 (no suppression) FAILS — top-tier dilution (95+ N 10->36 at -4.3pp WR15)
  - V6 (pre-only log W=2 M=1.0) WINS — 95+ WR15 +2.9pp at modest N growth
  - V7 (post-only) confirms directional asymmetry: post-earnings carries
    informational signal; pre-earnings is anticipation noise
  - W=2 vs W=3 ~ identical; M=0.7 dilutes (V4/V5)

This script firms up the V6 finding at 5y (95+ N=14 at 2y is small) and
tests neighbors that fill gaps in the original sweep:
  - V6 with W=3   : does a wider pre-earnings ramp help?
  - V6 with M=0.85: does partial-strength at d=0 help when calls aren't
                    blocked post-earnings (i.e. does the dilution issue
                    seen in V4/V5 disappear under pre-only direction)?
  - V8 hybrid     : pre-only at full strength + tiny post-only dampener
                    (M_post=0.3) — tests whether a small post-earnings
                    fade catches a sliver of news-day noise without the
                    catastrophic V7 effect
  - V0 + V6       : carry-forward locks for direct comparison

The V8 hybrid mechanism uses two strengths (pre/post) with the larger
applied:
    s_pre  = (M_pre  if d >= 0 else 0) * log(W+1-|d|)/log(W+1)  for |d|<=W
    s_post = (M_post if d <= 0 else 0) * log(W+1-|d|)/log(W+1)  for |d|<=W
    s = max(s_pre, s_post)

Run
---
    python -m experiments.v28_optimization.phase_evs_per_trade_v6_focus
"""
from __future__ import annotations

import io
import math
import os
import sys
from datetime import timedelta

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import volume_amplifier as va


# ────────────────────────────────────────────────────────────────────────
# Variant configuration
# ────────────────────────────────────────────────────────────────────────

STRATEGY = 'binary'    # 'binary' | 'off' | 'log' | 'hybrid'
WINDOW_W = 1
MAX_SUPP_PRE = 1.0     # used by 'log' (when DIRECTION='pre' or 'sym') and 'hybrid'
MAX_SUPP_POST = 0.0    # used by 'log' (when DIRECTION='post') and 'hybrid'
DIRECTION = 'sym'      # 'sym' | 'pre' | 'post'  (only for 'log')


def _supp_strength(nearest_d: int) -> float:
    """Return suppression strength s in [0, 1] for current STRATEGY."""
    abs_d = abs(nearest_d)

    if STRATEGY == 'off':
        return 0.0

    if STRATEGY == 'binary':
        return 1.0 if abs_d <= 1 else 0.0

    if STRATEGY == 'log':
        if abs_d > WINDOW_W:
            return 0.0
        if DIRECTION == 'pre' and nearest_d < 0:
            return 0.0
        if DIRECTION == 'post' and nearest_d > 0:
            return 0.0
        m = MAX_SUPP_PRE  # in 'log' mode, the directional gate above already excluded the wrong side
        return m * math.log(WINDOW_W + 1 - abs_d) / math.log(WINDOW_W + 1)

    if STRATEGY == 'hybrid':
        if abs_d > WINDOW_W:
            return 0.0
        prox = math.log(WINDOW_W + 1 - abs_d) / math.log(WINDOW_W + 1)
        s_pre  = (MAX_SUPP_PRE  if nearest_d >= 0 else 0.0) * prox
        s_post = (MAX_SUPP_POST if nearest_d <= 0 else 0.0) * prox
        return max(s_pre, s_post)

    return 0.0


# ────────────────────────────────────────────────────────────────────────
# Patched volume-amplifier accessor
# ────────────────────────────────────────────────────────────────────────

_orig_get_vol_mult = va.get_volume_multiplier_from_cache
_EMPTY_SET: frozenset = frozenset()


def patched_get_volume_multiplier_from_cache(
    today, ph_list_desc, prior_vol_signals, earnings_dates_set,
    pre_volume_overall=None,
):
    result = _orig_get_vol_mult(
        today, ph_list_desc, prior_vol_signals, _EMPTY_SET, pre_volume_overall,
    )
    vol_mult, vol_raw, sig_type, vol_mag, blend_w, vol_target = result

    if not earnings_dates_set:
        return result

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
        vol_mult * (1.0 - s) + 1.0 * s,
        vol_raw,
        sig_type,
        vol_mag * (1.0 - s),
        blend_w * (1.0 - s),
        vol_target,
    )


# ────────────────────────────────────────────────────────────────────────
# Variant table (V0 + V6 carried forward; V6 neighbors added)
# ────────────────────────────────────────────────────────────────────────

VARIANTS = [
    # (label,            strategy, W, M_pre, M_post, direction)
    ('V0_binary',        'binary', 1,  1.0,  0.0,   'sym'),  # current production
    ('V6_pre_W2_M10',    'log',    2,  1.0,  0.0,   'pre'),  # 2y winner
    ('V6_pre_W3_M10',    'log',    3,  1.0,  0.0,   'pre'),  # wider ramp
    ('V6_pre_W2_M85',    'log',    2,  0.85, 0.0,   'pre'),  # partial strength
    ('V6_pre_W3_M85',    'log',    3,  0.85, 0.0,   'pre'),  # wider + partial
    ('V8_hybrid_M10_30', 'hybrid', 2,  1.0,  0.3,   'sym'),  # full pre + mild post
    ('V8_hybrid_M10_50', 'hybrid', 2,  1.0,  0.5,   'sym'),  # full pre + medium post
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
    global STRATEGY, WINDOW_W, MAX_SUPP_PRE, MAX_SUPP_POST, DIRECTION

    days = 1825  # 5y default
    if len(sys.argv) > 1:
        days = _parse_days(sys.argv[1])

    print(f"[phase_evs_v6_focus] lookback={days}d", flush=True)

    va.get_volume_multiplier_from_cache = patched_get_volume_multiplier_from_cache

    try:
        from simulator import ScoreSimulator
        from database.models.core import AlgorithmVersion

        active_v = AlgorithmVersion.get_active_scores_version()
        active_vid = active_v.id
        print(
            f"[phase_evs_v6_focus] active DB version: id={active_vid} "
            f"({active_v.git_commit[:10] if active_v.git_commit else 'none'})",
            flush=True,
        )

        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for label, strat, w, m_pre, m_post, direc in VARIANTS:
            STRATEGY = strat
            WINDOW_W = w
            MAX_SUPP_PRE = m_pre
            MAX_SUPP_POST = m_post
            DIRECTION = direc

            print("\n" + "=" * 100, flush=True)
            if strat == 'log':
                params = f"strategy={strat} W={w} M={m_pre} dir={direc}"
            elif strat == 'hybrid':
                params = f"strategy={strat} W={w} M_pre={m_pre} M_post={m_post}"
            else:
                params = f"strategy={strat}"
            print(f"  VARIANT: {label}    {params}", flush=True)
            print("=" * 100, flush=True)

            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=active_vid)
            sys.stdout.flush()

    finally:
        va.get_volume_multiplier_from_cache = _orig_get_vol_mult


if __name__ == '__main__':
    main()
