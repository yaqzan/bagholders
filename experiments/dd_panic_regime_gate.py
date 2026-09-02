"""Regime-gated panic close — test whether restricting PANIC_CLOSE to
non-BULL regimes recovers the 2024 opportunity cost.

The N=500 validation showed panic close v30_3d is near-flat on 22-now compound
(Realistic -1.5%, Conservative -5.6%) because 2024's -19% cost cancels 2025's
+27% win. Hypothesis: 2024 was nearly all HEALTHY/BULL (regime_mult >= 1.00),
so VIX spikes there were false alarms. Gating panic close on regime_mult
should eliminate the 2024 cost while preserving 2025's DD protection.

Variants tested:
  BASELINE          : panic off
  v30_3d            : original — fires whenever VIX > 30% in 3d (reference)
  v30_3d_reg100     : panic fires only if regime_mult < 1.00 (not HEALTHY+)
  v30_3d_reg095     : panic fires only if regime_mult < 0.95 (CAUTION or worse)
  v30_3d_reg088     : panic fires only if regime_mult < 0.88 (mid-CAUTION+)
  v25_3d_reg100     : tighter VIX threshold with same regime gate (see if the
                       broader trigger now works once BULL is filtered out)
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

N_ITER = 300

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]

VARIANTS = [
    # (name, vix_pct, vix_days, regime_cutoff)  — None cutoff = no regime gate
    ('BASELINE',       None, None, None),
    ('v30_3d',         0.30, 3,    None),
    ('v30_3d_reg100',  0.30, 3,    1.00),
    ('v30_3d_reg095',  0.30, 3,    0.95),
    ('v30_3d_reg088',  0.30, 3,    0.88),
    ('v25_3d_reg100',  0.25, 3,    1.00),
]


def apply_regime_gate(trading_days, flags, regime_dates, regime_map, cutoff):
    """Zero out flags on days where regime_mult >= cutoff."""
    if flags is None or cutoff is None:
        return flags
    gated = [False] * len(trading_days)
    for i, d in enumerate(trading_days):
        if not flags[i]:
            continue
        mult = mc.regime_on_or_before(regime_dates, regime_map, d) if regime_dates else 1.0
        if mult < cutoff:
            gated[i] = True
    return gated


def run_variant(label, vname, panic_flags, data):
    (trading_days, ph_by_sym, calls_by_date, call_outcomes,
     puts_by_date, put_outcomes, regime_dates, regime_map) = data
    modes = ['realistic', 'conservative']
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
        results[mode] = dict(
            mean_ret=mean_ret,
            worst_dd=max(dds) * 100,
            p_coll=collapses / N_ITER * 100,
            mean_panic=statistics.mean(pcl),
        )
    return results


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")
    print(f"N_ITER = {N_ITER}")

    all_results = {}
    day_counts = {}

    for label, d_start, d_end in WINDOWS:
        print(f"\n{'='*100}\nWINDOW: {label}\n{'='*100}")
        data = prep_window(label, d_start, d_end, version)
        trading_days = data[0]
        spy_vix = data[-1]
        regime_dates = data[-3]
        regime_map = data[-2]
        core = data[:-1]
        all_results[label] = {}
        day_counts[label] = {}

        for vname, pct, days, cutoff in VARIANTS:
            if pct is None:
                flags = None
            else:
                raw = compute_panic_flags(trading_days, spy_vix, pct, days)
                flags = apply_regime_gate(trading_days, raw, regime_dates, regime_map, cutoff)
            nd = 0 if flags is None else sum(flags)
            day_counts[label][vname] = nd
            print(f"  {vname:<18}  panic_days={nd:>3}/{len(trading_days)}  running...", flush=True)
            r = run_variant(label, vname, flags, core)
            all_results[label][vname] = r
            for mode in ['realistic', 'conservative']:
                rr = r[mode]
                print(f"    {mode:<13}: ret={rr['mean_ret']:>+14,.1f}%  DD={rr['worst_dd']:>5.1f}%  "
                      f"closes={rr['mean_panic']:>5.1f}")

    for mode in ['realistic', 'conservative']:
        print(f"\n{'='*135}\n{mode.upper()} — Δ Return vs BASELINE, and absolute Worst DD\n{'='*135}")
        header = f"{'Window':<10}"
        for vname, _, _, _ in VARIANTS:
            header += f" | {vname:>18}"
        print(header)
        print('-' * len(header))

        for label, _, _ in WINDOWS:
            base = all_results[label]['BASELINE'][mode]['mean_ret']
            row_ret = f"{label:<10}"
            row_dd  = f"{' '*10}"
            for vname, _, _, _ in VARIANTS:
                r = all_results[label][vname][mode]
                if vname == 'BASELINE':
                    row_ret += f" | {r['mean_ret']:>+15,.0f}%  "
                    row_dd  += f" | {r['worst_dd']:>12.1f}%    "
                else:
                    delta = (r['mean_ret'] - base) / max(abs(base), 1.0) * 100
                    nd = day_counts[label][vname]
                    row_ret += f" | {delta:>+8.1f}% (n={nd:>3})"
                    row_dd  += f" | DD={r['worst_dd']:>5.1f}%         "
            print(row_ret)
            print(row_dd)

    # Focus table: 22-now summary
    print(f"\n{'='*100}\n22-NOW FOCUS (compound window — the headline metric)\n{'='*100}")
    print(f"{'Variant':<18} {'Real Δret':>12} {'Real DD Δ':>12} {'Cons Δret':>12} {'Cons DD Δ':>12} {'Panic N':>10}")
    base_real = all_results['22-now']['BASELINE']['realistic']
    base_cons = all_results['22-now']['BASELINE']['conservative']
    for vname, _, _, _ in VARIANTS:
        r_real = all_results['22-now'][vname]['realistic']
        r_cons = all_results['22-now'][vname]['conservative']
        nd = day_counts['22-now'][vname]
        if vname == 'BASELINE':
            print(f"{vname:<18} {'—':>12} {'—':>12} {'—':>12} {'—':>12} {nd:>10}")
        else:
            d_real_ret = (r_real['mean_ret'] - base_real['mean_ret']) / max(abs(base_real['mean_ret']), 1.0) * 100
            d_real_dd = r_real['worst_dd'] - base_real['worst_dd']
            d_cons_ret = (r_cons['mean_ret'] - base_cons['mean_ret']) / max(abs(base_cons['mean_ret']), 1.0) * 100
            d_cons_dd = r_cons['worst_dd'] - base_cons['worst_dd']
            print(f"{vname:<18} {d_real_ret:>+11.1f}% {d_real_dd:>+11.1f}pp "
                  f"{d_cons_ret:>+11.1f}% {d_cons_dd:>+11.1f}pp {nd:>10}")


if __name__ == '__main__':
    main()
