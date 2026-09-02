"""Apples-to-apples MC baseline: run current monte_carlo.py code against v21
scores so the v25 result has a true comparison point (same F3f, same cascade,
same CT promotion, same exit rules).

Pinned to AlgorithmVersion id=21 (aba4f5d) regardless of active pointer.
"""
from __future__ import annotations

import statistics
import time

import monte_carlo as mc
from database.models.core import AlgorithmVersion


def main():
    av = AlgorithmVersion.get(AlgorithmVersion.id == 21)
    print(f"\n[mc_v21_baseline] forcing version v{av.id} ({av.git_commit})")

    print('='*110)
    print(f"Monte Carlo  |  v21 baseline reference  |  N_ITER={mc.N_ITER}  modes={mc.COLLISION_MODES}")
    print('='*110)

    all_results = {}
    for label, d_start, d_end in mc.WINDOWS:
        t0 = time.time()
        all_results[label] = mc.run_window(label, d_start, d_end, av)
        print(f"  [{label} done in {time.time()-t0:.0f}s]")

    print('\n' + '='*110)
    print("SUMMARY (v21 baseline) - Mean Return by Window x Collision Mode")
    print('='*110)
    print(f"{'Window':<8}  " + '  '.join(f"{m+' MeanRet':>22}" for m in mc.COLLISION_MODES))
    print('-'*110)
    for label, _, _ in mc.WINDOWS:
        row = f"{label:<8}  "
        for mode in mc.COLLISION_MODES:
            r = all_results[label][mode]
            row += f"{r['mean_ret']:>+18,.1f}%     "
        print(row)

    print('\n' + '='*110)
    print("SUMMARY (v21 baseline) - Worst Drawdown")
    print('='*110)
    for label, _, _ in mc.WINDOWS:
        row = f"{label:<8}  "
        for mode in mc.COLLISION_MODES:
            row += f"{all_results[label][mode]['worst_dd']:>22.1f}%     "
        print(row)

    print('\n' + '='*110)
    print("SUMMARY (v21 baseline) - P(collapse)")
    print('='*110)
    for label, _, _ in mc.WINDOWS:
        row = f"{label:<8}  "
        for mode in mc.COLLISION_MODES:
            row += f"{all_results[label][mode]['p_collapse']:>22.1f}%     "
        print(row)


if __name__ == '__main__':
    main()
