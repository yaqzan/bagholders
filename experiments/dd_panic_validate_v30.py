"""High-fidelity validation of panic-close v30_3d vs BASELINE.

The N=150 grid sweep (dd_panic_param_sweep.py) found v30_3d (VIX +30% in 3d,
close calls at MTM) to be the only variant with positive 22-now return AND
DD reduction. This script validates at N=500 across all 3 collision modes
and 6 windows to confirm the result isn't sampling noise.
"""
from __future__ import annotations
import os, sys, random, statistics
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments.dd_panic_param_sweep import (
    load_spy_vix, compute_panic_flags, run_sim, prep_window
)
from database.models.core import AlgorithmVersion

N_ITER = 500

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]

VARIANTS = [
    ('BASELINE', None, None),
    ('v30_3d',   0.30, 3),
]


def run_variant(label, vname, panic_flags, data):
    (trading_days, ph_by_sym, calls_by_date, call_outcomes,
     puts_by_date, put_outcomes, regime_dates, regime_map) = data
    modes = ['conservative', 'realistic', 'optimistic']
    results = {}
    for mode in modes:
        finals = []; dds = []; collapses = 0; pcl = []
        for it in range(N_ITER):
            rng = random.Random(1000 * hash(label + vname) + it)
            r = run_sim(trading_days, ph_by_sym, calls_by_date, call_outcomes,
                        puts_by_date, put_outcomes, mode, rng,
                        regime_dates, regime_map, panic_flags)
            finals.append(r['final']); dds.append(r['max_dd'])
            pcl.append(r['panic_closes'])
            if r['final'] <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD: collapses += 1
        mean_ret = (statistics.mean(finals) / mc.STARTING_CASH - 1) * 100
        median_ret = (statistics.median(finals) / mc.STARTING_CASH - 1) * 100
        results[mode] = dict(
            mean_ret=mean_ret,
            median_ret=median_ret,
            worst_dd=max(dds) * 100,
            mean_dd=statistics.mean(dds) * 100,
            p_coll=collapses / N_ITER * 100,
            mean_panic=statistics.mean(pcl),
        )
    return results


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")
    print(f"N_ITER = {N_ITER}  (validation of N=150 sweep)")

    all_results = {}
    day_counts = {}

    for label, d_start, d_end in WINDOWS:
        print(f"\n{'='*100}\nWINDOW: {label}\n{'='*100}")
        data = prep_window(label, d_start, d_end, version)
        trading_days = data[0]
        spy_vix = data[-1]
        core = data[:-1]
        all_results[label] = {}
        day_counts[label] = {}

        for vname, pct, days in VARIANTS:
            flags = None if pct is None else compute_panic_flags(trading_days, spy_vix, pct, days)
            nd = 0 if flags is None else sum(flags)
            day_counts[label][vname] = nd
            print(f"  {vname:<10}  panic_days={nd:>3}/{len(trading_days)}  running N={N_ITER}...", flush=True)
            r = run_variant(label, vname, flags, core)
            all_results[label][vname] = r
            for mode in ['realistic', 'conservative', 'optimistic']:
                rr = r[mode]
                print(f"    {mode:<13}: mean={rr['mean_ret']:>+15,.1f}%  med={rr['median_ret']:>+15,.1f}%  "
                      f"DD={rr['worst_dd']:>5.1f}%  closes={rr['mean_panic']:>5.1f}  collapse={rr['p_coll']:.1f}%")

    # Comparison tables
    print(f"\n{'='*110}\nFINAL COMPARISON — v30_3d vs BASELINE (N={N_ITER})\n{'='*110}")
    for mode in ['realistic', 'conservative', 'optimistic']:
        print(f"\n{mode.upper()}")
        print(f"{'Window':<10} {'Baseline Ret':>18} {'v30_3d Ret':>18} {'Δ Ret':>12} | "
              f"{'Base DD':>8} {'v30 DD':>8} {'Δ DD':>8} | {'PanicDays':>10}")
        print('-' * 110)
        for label, _, _ in WINDOWS:
            b = all_results[label]['BASELINE'][mode]
            v = all_results[label]['v30_3d'][mode]
            dret = (v['mean_ret'] - b['mean_ret']) / max(abs(b['mean_ret']), 1.0) * 100
            ddd  = v['worst_dd'] - b['worst_dd']
            nd   = day_counts[label]['v30_3d']
            print(f"{label:<10} {b['mean_ret']:>+17,.0f}% {v['mean_ret']:>+17,.0f}% {dret:>+10.1f}% | "
                  f"{b['worst_dd']:>7.1f}% {v['worst_dd']:>7.1f}% {ddd:>+7.1f} | {nd:>10}")


if __name__ == '__main__':
    main()
