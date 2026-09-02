"""
Phase EVR-1 N=1000 disambiguation on 2021 + 5y (2026-05-01).

EVR-1 N=300 sweep: OFF wins 22-now +83%, 5y +13%, 4 of 5 annual windows —
but FAILS strict gate on 2021 (-54% vs B). Per CLAUDE.md noise documentation,
N=300 has 4-order-of-magnitude swings on 5y compound from rng seed alone;
DD signal reliable but compound mean needs N=1000+ for ship decisions.

This run: tighten the 2021 regression call by running BOTH variants at N=1000
on 2021 + 5y (the two windows that matter — 2021 because it's the gate
breach, 5y because it's the headline).

Per-variant: ~21 min sim + ~10 min MC at N=1000 (2 windows). Total ~62 min.

Output: experiments/evr_optimization/phase_evr1_n1000_results.json
"""
import json
import os
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from simulator import ScoreSimulator
from experiments.sim_mc_bridge import build_score_lists, inject_into_mc
import volume_amplifier as va
import monte_carlo as mc

WINDOWS = [
    ('2021', date(2021, 1, 1), date(2021, 12, 31)),
    ('5y',   date(2021, 4, 30), date(2026, 4, 30)),
]
N_ITER = 1000
LOOKBACK_DAYS = 1825 + 90


def patch_for_variant(variant: str):
    saved = (va.EARN_SUPP_MAX_STRENGTH, va.EARN_SUPP_WINDOW_DAYS)
    if variant == 'B':
        va.EARN_SUPP_MAX_STRENGTH = 1.0
        va.EARN_SUPP_WINDOW_DAYS = 2
    elif variant == 'OFF':
        va.EARN_SUPP_MAX_STRENGTH = 0.0
    return saved


def restore(saved):
    va.EARN_SUPP_MAX_STRENGTH, va.EARN_SUPP_WINDOW_DAYS = saved


def run_variant(variant, sim):
    print(f"\n{'='*72}\nVARIANT: {variant}  (N={N_ITER})\n{'='*72}", flush=True)
    print(f"  EARN_SUPP_MAX_STRENGTH={va.EARN_SUPP_MAX_STRENGTH}", flush=True)
    t0 = time.time()
    rich = sim.simulate_full()
    rows = build_score_lists(rich)
    print(f"[sim] {len(rows)} rows in {(time.time()-t0):.1f}s", flush=True)

    mc.N_ITER = N_ITER
    os.environ['MC_NO_MP'] = '1'
    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()

    results = {}
    with inject_into_mc(rows):
        for label, d_start, d_end in WINDOWS:
            t1 = time.time()
            print(f"\n--- {variant}/{label} ---", flush=True)
            res = mc.run_window(label, d_start, d_end, version)
            r = res['seeded']
            results[label] = {
                'mean_ret': r['mean_ret'], 'med_ret': r['med_ret'],
                'mean_dd': r['mean_dd'], 'worst_dd': r['worst_dd'],
                'p_coll': r['p_coll'],
                'call_tp': r['call_tp'], 'put_tp': r['put_tp'],
                'call_trades': r['call_trades'], 'put_trades': r['put_trades'],
                'elapsed': round(time.time()-t1, 1),
            }
            print(f"  ret={r['mean_ret']:>+15,.1f}%  med={r['med_ret']:>+15,.1f}%  "
                  f"DD={r['worst_dd']:>5.1f}%  coll={r['p_coll']:>4.1f}%  "
                  f"C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})  [{(time.time()-t1):.0f}s]", flush=True)
    return results


def main():
    t_start = time.time()
    print(f"=== Phase EVR-1 N=1000 disambiguation (2021 + 5y) ===", flush=True)

    print("\n[init] Building simulator (full universe × 5y)...", flush=True)
    from database.models.core import Stock
    symbols = [s.symbol for s in Stock.select()]
    sim = ScoreSimulator(symbols=symbols, lookback_days=LOOKBACK_DAYS)
    print(f"[init] simulator ready ({len(symbols)} symbols)", flush=True)

    all_results = {}
    for variant in ('B', 'OFF'):
        saved = patch_for_variant(variant)
        try:
            all_results[variant] = run_variant(variant, sim)
        finally:
            restore(saved)

    print(f"\n\n{'='*72}\nVERDICT (N={N_ITER})\n{'='*72}", flush=True)
    print(f"\n{'window':<10} {'B (V6 prod)':<55} {'OFF':<55} {'Δret':>10}", flush=True)
    for label, _, _ in WINDOWS:
        rb = all_results.get('B', {}).get(label, {})
        ro = all_results.get('OFF', {}).get(label, {})
        bcell = f"ret={rb.get('mean_ret', 0):+15,.0f}%  med={rb.get('med_ret', 0):+12,.0f}%  DD={rb.get('worst_dd', 0):.1f}%"
        ocell = f"ret={ro.get('mean_ret', 0):+15,.0f}%  med={ro.get('med_ret', 0):+12,.0f}%  DD={ro.get('worst_dd', 0):.1f}%"
        if rb.get('mean_ret'):
            delta_pct = (ro.get('mean_ret', 0) / rb.get('mean_ret', 1) - 1.0) * 100
            dpct = f"{delta_pct:+7.1f}%"
        else:
            dpct = "n/a"
        print(f"{label:<10} {bcell:<55} {ocell:<55} {dpct:>10}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'phase_evr1_n1000_results.json')
    payload = {
        'n_iter': N_ITER,
        'baseline': 'DD060+dead-hold+bimodal',
        'results': all_results,
        'elapsed_min': round((time.time() - t_start) / 60.0, 1),
    }
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nResults → {out_path}", flush=True)
    print(f"Total elapsed: {payload['elapsed_min']:.1f} min", flush=True)


if __name__ == '__main__':
    main()
