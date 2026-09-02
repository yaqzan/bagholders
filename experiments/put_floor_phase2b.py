"""Phase 2b: complete the variants Phase 2 timed out on, with K=1.0 baseline.

Phase 2 found K=1.0 dominant on the K axis. Phase 2b explores the OTHER
axes (wadj cutoff, lift target, smooth weakness) with K=1.0 fixed.

Also adds a HYBRID variant combining K=1.0 + lift_target=30 — the most
aggressive in the parameter space — to identify the upper bound.

Variants:
  P2b_K10      reference (K=1.0, default cutoff -13, target 26, linear weakness)
  P2b_W10      cutoff=-10 (broader weak threshold)
  P2b_W16      cutoff=-16 (narrower)
  P2b_T30      lift target = 30
  P2b_TANH_W   tanh-smoothed weakness curve
  P2b_K10_T30  K=1.0 + target=30 (most aggressive)
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import database.utils.scoring as scoring_mod

# Re-use the patched function from put_floor_phase2.py
sys.path.insert(0, 'experiments')
from put_floor_phase2 import patched_compute_overall_score
import put_floor_phase2 as pfp2

_ORIGINAL_COMPUTE = scoring_mod.compute_overall_score


VARIANTS = [
    # (label,         enabled, k_lift, wadj_cut, score_gate, lift_target, use_tanh)
    ('P2b_K10',         True,    1.0,  -13.0,    25,         26,          False),  # K10 reference
    ('P2b_W10',         True,    1.0,  -10.0,    25,         26,          False),
    ('P2b_W16',         True,    1.0,  -16.0,    25,         26,          False),
    ('P2b_T30',         True,    1.0,  -13.0,    25,         30,          False),
    ('P2b_TANH_W',      True,    1.0,  -13.0,    25,         26,          True),
    ('P2b_K10_T30',     True,    1.0,  -13.0,    25,         30,          False),  # same as T30 — leaving for clarity
    ('P2b_T35',         True,    1.0,  -13.0,    25,         35,          False),  # push to 35
]


def main():
    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    scoring_mod.compute_overall_score = patched_compute_overall_score

    try:
        from simulator import ScoreSimulator
        from database.models.core import AlgorithmVersion
        active_v = AlgorithmVersion.get_active_scores_version()
        active_vid = active_v.id
        print(f"[setup] active DB version: id={active_vid} ({active_v.git_commit[:10]})")
        sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

        for v in VARIANTS:
            label, enabled, k, wc, sg, lt, ut = v
            pfp2.ENABLED = enabled
            pfp2.K_LIFT = k
            pfp2.WADJ_CUTOFF = wc
            pfp2.SCORE_GATE = sg
            pfp2.LIFT_TARGET = lt
            pfp2.USE_TANH_WEAKNESS = ut

            print("\n" + "=" * 100, flush=True)
            params = f"k={k} wadj_cut={wc} score_gate={sg} lift_target={lt} tanh_w={ut}"
            print(f"  VARIANT: {label}    {params}", flush=True)
            print("=" * 100, flush=True)
            scores = sim.simulate()
            sys.stdout.flush()
            sim.diff_assess(scores, db_version=active_vid)
            sys.stdout.flush()
    finally:
        scoring_mod.compute_overall_score = _ORIGINAL_COMPUTE


if __name__ == '__main__':
    main()
