"""
Smoke test for BB slide gate fix on the original cases (RKLB, IRM Apr 29 2026).

Reads cached indicator + price data for the two stocks, computes the BB score
under both original and patched logic, and prints the delta + projected impact
on the stored overall score.
"""
from __future__ import annotations

from datetime import date

from database.models.core import Stock, AlgorithmVersion, Score
from experiments.bb_slide_gate.bb_slide_gate_experiment import patched_bb_score, _ORIGINAL_BB


def main():
    target = date(2026, 4, 29)
    av = AlgorithmVersion.get_active_scores_version()

    print(f"Active version: v{av.id} ({av.git_commit})\n")
    print(f"{'sym':<6}{'orig BB':>10}{'patch BB':>10}{'ΔBB':>8}{'TREND':>7}{'overall':>9}{'Δ overall':>11}")
    print("-" * 72)

    for sym in ['RKLB', 'IRM']:
        stock = Stock.get(Stock.symbol == sym)

        # Get TREND from stored score (so we use the same TREND input for both)
        row = (Score.select()
               .where(Score.symbol == sym, Score.date == target, Score.version == av.id)
               .first())
        trend = row.trend
        orig_bb = row.bb
        orig_overall = row.overall

        # Compute the new BB score with the patch
        new_bb = patched_bb_score(stock, target, trend_score=trend)

        delta_bb = new_bb - orig_bb if new_bb is not None else 0

        # Estimated impact on overall score:
        # weighted_sum delta = (new_bb - orig_bb) * w_bb / 100, where w_bb=18
        # then weighted_sum * regime_mult — RKLB used 0.857
        regime_mult = float(row.regime_multiplier or 1.0)
        ws_delta = delta_bb * 18 / 100  # raw weighted-sum delta
        # Pre-regime delta -> overall delta scaled by regime_mult
        overall_delta_est = ws_delta * regime_mult

        new_overall_est = round(orig_overall + overall_delta_est)

        print(f"{sym:<6}{orig_bb:>10}{new_bb:>10}{delta_bb:>+8}{trend:>7}{orig_overall:>9}"
              f"{new_overall_est - orig_overall:>+11} -> {new_overall_est}")


if __name__ == '__main__':
    main()
