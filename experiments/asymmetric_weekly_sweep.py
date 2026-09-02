"""
Asymmetric weekly scaling sweep — calls fixed at 1.0x, puts swept 1.0-1.75x.

The weekly adjustment returns a signed `total`: positive pushes toward call (high),
negative toward put (low). Asymmetric scaling applies `put_scale` only when
total < 0, leaving call-direction adjustments untouched.

Rationale: weekly_magnitude_sweep.py (2026-04-15) found puts improve monotonically
with scale (60.7% -> 65.7% WR30 at <25 from 0.0x to 1.5x) while calls peak at 1.0x.
This sweep tests whether the put-side lift can be captured without touching calls.

Scales: 1.0 (baseline), 1.15, 1.25, 1.4, 1.5, 1.75
Single 5y data load — re-runs diff-assess per scale.
"""
from __future__ import annotations
import sys
import database.utils.scoring as scoring_mod

_ORIGINAL_WEEKLY_ADJ = scoring_mod.calculate_weekly_adjustment


def make_asymmetric(put_scale: float, call_scale: float = 1.0):
    def fn(*args, **kwargs):
        total, detail = _ORIGINAL_WEEKLY_ADJ(*args, **kwargs)
        if total is None:
            return total, detail
        if total < 0:
            total *= put_scale
        else:
            total *= call_scale
        if detail is not None:
            detail = dict(detail)
            detail['w_adj'] = round(total, 1)
        return total, detail
    return fn


def main():
    days = 1825
    if len(sys.argv) > 1:
        tok = sys.argv[1].lower()
        if tok.endswith('y'):
            days = int(float(tok[:-1]) * 365)
        else:
            days = int(tok)

    put_scales = [1.0, 1.15, 1.25, 1.4, 1.5, 1.75]

    from simulator import ScoreSimulator
    sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)

    for ps in put_scales:
        print("\n" + "=" * 100)
        print(f"  PUT SCALE = {ps:.2f}x  (call scale = 1.00x)")
        print("=" * 100)
        scoring_mod.calculate_weekly_adjustment = make_asymmetric(ps, 1.0)
        scores = sim.simulate()
        sim.diff_assess(scores, db_version=17)

    scoring_mod.calculate_weekly_adjustment = _ORIGINAL_WEEKLY_ADJ


if __name__ == '__main__':
    main()
