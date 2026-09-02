"""Phase EVR-1 — Pre-event earnings volume calibration on v31 baseline.

Re-validates the V6 log-gradient earnings volume suppression now that two
architectural shifts have landed:

  1. Strict-future earnings semantics (v31, `f3ec7c1`, 2026-04-30).  The
     `_earn_nearest_d_*` helpers now use `bisect_right` and require
     `1 <= delta <= W`, eliminating the D0 leak that biased the original
     V6/V7 sweep numbers.
  2. Option-pricing-aware MC with bounded random fill + AMC-aware
     `effective_date` (v30/v31).  Theta + sampled vega are now modelled
     explicitly; the volume amplifier may have been compensating for that
     fudge factor.

The most important variant in this sweep is OFF.  If "no earnings volume
suppression" ties or beats the current production gradient under v31 +
bounded-fill MC, we can simplify by deleting `_earnings_supp_strength`
entirely.

Variants
--------
    B       W=2  M=1.0  log    pre-only   (current production = V6 ship)
    OFF                                   (no earnings suppression)
    W1      W=1  M=1.0  log    pre-only   (tighter window)
    W3      W=3  M=1.0  log    pre-only   (wider with gentle D-3 tail)
    M075    W=2  M=0.75 log    pre-only   (gentler peak)
    M050    W=2  M=0.50 log    pre-only   (half peak)
    TANH    W=2  M=1.0  tanh   pre-only   (smooth shape: M*tanh((W-d+1)/k))

All variants are PRE-ONLY (post-event handling reserved for EVR-2).

Decision gate
-------------
Per-trade WR15 on 95+/85+/<25/<15 within +/-0.5pp of B (or improvement).
Carry the top per-trade variants into canonical seeded MC at N=300+ x 8
windows AFTER Phase OP2 lands a fresh portfolio baseline (current shipped
knobs are structurally too aggressive under bounded-fill MC).

Run
---
    python -m experiments.evr_optimization.phase_evr1_per_trade           # 2y default
    python -m experiments.evr_optimization.phase_evr1_per_trade 1825      # 5y
    python -m experiments.evr_optimization.phase_evr1_per_trade 3y        # 3y

The script monkey-patches `volume_amplifier.get_volume_multiplier_from_cache`
in-process (bypassing production V6) and runs `simulator.simulate()` per
variant, then `diff_assess` against the active DB version (v31).  No DB
writes.
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


# --- Variant state (mutated per iteration) -----------------------------------

STRATEGY = 'log'      # 'off' | 'log' | 'tanh'
WINDOW_W = 2          # half-width in calendar days
MAX_SUPP = 1.0        # peak strength at d=1 (closest qualifying day under strict-future)
TANH_K   = 1.0        # tanh bandwidth (only used by 'tanh')


def _supp_strength(nearest_d: int) -> float:
    """Return suppression strength s in [0, 1] for current STRATEGY.

    Pre-only semantics: only positive nearest_d in [1, W] qualifies.
    nearest_d <= 0 (same-day or post-event) returns 0.
    Mirrors production `_earnings_supp_strength` boundary conditions.
    """
    if STRATEGY == 'off':
        return 0.0

    if nearest_d is None or nearest_d < 1 or nearest_d > WINDOW_W:
        return 0.0

    W = WINDOW_W

    if STRATEGY == 'log':
        return MAX_SUPP * math.log(W + 1 - nearest_d) / math.log(W + 1)

    if STRATEGY == 'tanh':
        # Smooth: peaks at d=1, decays to 0 at d=W+1
        # M * tanh((W - d + 1) / k); normalize so s(d=1) = MAX_SUPP at large W
        norm = math.tanh(W / TANH_K)
        if norm <= 0:
            return 0.0
        return MAX_SUPP * math.tanh((W - nearest_d + 1) / TANH_K) / norm

    return 0.0


# --- Patched volume-amplifier accessor ---------------------------------------

_orig_get_vol_mult = va.get_volume_multiplier_from_cache
_EMPTY_SET: frozenset = frozenset()


def patched_get_volume_multiplier_from_cache(
    today, ph_list_desc, prior_vol_signals, earnings_dates_set,
    pre_volume_overall=None,
):
    # Disable production V6 by passing empty earnings set; we handle suppression here.
    result = _orig_get_vol_mult(
        today, ph_list_desc, prior_vol_signals, _EMPTY_SET, pre_volume_overall,
    )
    vol_mult, vol_raw, sig_type, vol_mag, blend_w, vol_target = result

    if not earnings_dates_set or STRATEGY == 'off':
        return result

    # Find nearest STRICTLY-FUTURE earnings (strict-future semantics, v31).
    nearest_d = None
    for delta in range(1, WINDOW_W + 1):
        if (today + timedelta(days=delta)) in earnings_dates_set:
            nearest_d = delta
            break

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


# --- Variant table -----------------------------------------------------------

VARIANTS = [
    # (label,    strategy,  W,   max_supp,  tanh_k)
    # Reordered for 5y: most-different first to surface signal early.
    # W1 dropped — proven mathematically identical to OFF at 2y
    # (log(W+1-d)/log(W+1) = log(0+...)/log(2) = 0 for d=W=1).
    ('TANH',    'tanh',    2,    1.0,       1.0),    # smoothest, strongest peak
    ('W3',      'log',     3,    1.0,       1.0),    # wider window (d=1+d=2 fire)
    ('B',       'log',     2,    1.0,       1.0),    # current production
    ('OFF',     'off',     0,    0.0,       1.0),    # no suppression
    ('M075',    'log',     2,    0.75,      1.0),    # 0.75x peak
    ('M050',    'log',     2,    0.50,      1.0),    # 0.50x peak
]


# --- Main --------------------------------------------------------------------

def _parse_days(tok: str) -> int:
    tok = tok.strip().lower()
    if tok.endswith('y'):
        return int(float(tok[:-1]) * 365)
    return int(tok)


def main():
    global STRATEGY, WINDOW_W, MAX_SUPP, TANH_K

    days = 730
    if len(sys.argv) > 1:
        days = _parse_days(sys.argv[1])

    print(f"[evr1] lookback={days}d", flush=True)

    va.get_volume_multiplier_from_cache = patched_get_volume_multiplier_from_cache

    try:
        from simulator import ScoreSimulator
        from database.models.core import AlgorithmVersion

        active_v = AlgorithmVersion.get_active_scores_version()
        active_vid = active_v.id
        commit_short = active_v.git_commit[:10] if active_v.git_commit else 'none'
        print(f"[evr1] active DB version: id={active_vid} ({commit_short})", flush=True)

        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for label, strat, w, m, k in VARIANTS:
            STRATEGY = strat
            WINDOW_W = w
            MAX_SUPP = m
            TANH_K   = k

            print("\n" + "=" * 100, flush=True)
            params = (
                f"strategy={strat} W={w} M={m} k={k}"
                if strat != 'off' else "strategy=off"
            )
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
