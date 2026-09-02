"""
Weekly magnitude sweep — scales the weekly adjustment (base_bias + momentum_bias)
by a factor and diff-assesses each against v17 DB scores.

Monkey-patches database.utils.scoring.calculate_weekly_adjustment so the
ScoreSimulator (with scoring_fn=None) picks up the scaled adjustment through
the normal compute_overall_score path.

Scales: 0.0 (no weekly), 0.5, 0.75, 1.0 (baseline), 1.25, 1.5
Single 5y data load — re-runs diff-assess per scale.
"""
from __future__ import annotations
import sys
import database.utils.scoring as scoring_mod

_ORIGINAL_WEEKLY_ADJ = scoring_mod.calculate_weekly_adjustment


def make_scaled(scale: float):
    def fn(*args, **kwargs):
        total, detail = _ORIGINAL_WEEKLY_ADJ(*args, **kwargs)
        return total * scale, detail
    return fn


def main():
    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    scales = [0.0, 0.5, 0.75, 1.0, 1.25, 1.5]

    from simulator import ScoreSimulator
    sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

    for scale in scales:
        print("\n" + "=" * 100)
        print(f"  WEEKLY SCALE = {scale:.2f}x")
        print("=" * 100)
        scoring_mod.calculate_weekly_adjustment = make_scaled(scale)
        scores = sim.simulate()
        sim.diff_assess(scores, db_version=17)

    scoring_mod.calculate_weekly_adjustment = _ORIGINAL_WEEKLY_ADJ


if __name__ == '__main__':
    main()
