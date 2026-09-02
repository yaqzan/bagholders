"""Phase 2 — combined v34 boost variants and portfolio-weighted ranking.

Key insight from Phase 1: H3 N stability gate ±15% catches RE-BUCKETING (where
boost promotes signals into higher tiers) as if it were N filtering. For a
calibration that legitimately promotes high-WR signals up the cascade, the
right gate is the portfolio-weighted alpha contribution per tier:

  alpha_tier = WR_tier × N_tier × cascade_alloc_tier

Phase 7 cascade allocs (from strategy_config.py):
  95+ : 20%
  85-94 : 15%   (cumulative bucket so 90+ and 85+)
  80-84 : 12%
  75-79 : 10%

We measure cumulative-bucket WR × N for each tier and weigh by alloc.

Variants explored:
  - Stack of best Phase 1 winners: NC15 + W7
  - MAX × NORM combinations to find the saturation/magnitude sweet spot
  - Add NORM_PUT alongside MAX (for put-side amplification)
  - Conservative-MAX + W7 to keep call quality + extend window
"""
from __future__ import annotations
import io, sys, os, json, math, time, copy
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

# Reuse Phase 1 helpers
sys.path.insert(0, str(ROOT / 'experiments' / 'v34_calibration'))
from sweep_boost_params import (
    Variant, build_data_cache, load_barriers, evaluate, h1_h5_gate, diff_against,
    CALL_BUCKETS, PUT_BUCKETS, CACHE_DIR
)

# Phase 7 cascade alloc by cumulative bucket. Cumulative buckets overlap, so we
# compute incremental bucket WR/N from cumulative differences.
TIER_ALLOC_INCR = {  # mid-tier → tier weight (Phase 7 monotonic 30 DTE)
    '95+': 0.20,        # ULTRA
    '90-94': 0.15,      # TOP (we'll combine 90-94 with 85-89 below since cascade has 85-94=0.15)
    '85-89': 0.15,
    '80-84': 0.12,
    '75-79': 0.10,
}
PUT_TIER_ALLOC_INCR = {
    '0-15': 0.12,       # put_top (≤15)
    '16-20': 0.10,
    '21-25': 0.08,
}


def incremental_buckets(results):
    """Convert cumulative bucket results to incremental (95+, 90-94, 85-89, 80-84, 75-79).
    Returns {bucket: (n, wr15)} for incremental bucket only.
    """
    cum = results
    incr = {}
    # 95+ stays as is
    incr['95+'] = cum['95+'][:2]
    # 90-94 = 90+ minus 95+
    n_90_plus, wr_90_plus = cum['90+'][:2]
    n_95_plus, wr_95_plus = cum['95+'][:2]
    n_90_94 = n_90_plus - n_95_plus
    if n_90_94 > 0 and wr_90_plus and wr_95_plus:
        # weighted: WR_90+ × N_90+ = WR_95+ × N_95+ + WR_90-94 × N_90-94
        wr_90_94 = (wr_90_plus * n_90_plus - wr_95_plus * n_95_plus) / n_90_94
    else:
        wr_90_94 = None
    incr['90-94'] = (n_90_94, wr_90_94)
    # 85-89 = 85+ minus 90+
    n_85_plus, wr_85_plus = cum['85+'][:2]
    n_85_89 = n_85_plus - n_90_plus
    wr_85_89 = (wr_85_plus * n_85_plus - wr_90_plus * n_90_plus) / n_85_89 if n_85_89 > 0 else None
    incr['85-89'] = (n_85_89, wr_85_89)
    # 80-84 = 80+ minus 85+
    n_80_plus, wr_80_plus = cum['80+'][:2]
    n_80_84 = n_80_plus - n_85_plus
    wr_80_84 = (wr_80_plus * n_80_plus - wr_85_plus * n_85_plus) / n_80_84 if n_80_84 > 0 else None
    incr['80-84'] = (n_80_84, wr_80_84)
    # 75-79 = 75+ minus 80+
    n_75_plus, wr_75_plus = cum['75+'][:2]
    n_75_79 = n_75_plus - n_80_plus
    wr_75_79 = (wr_75_plus * n_75_plus - wr_80_plus * n_80_plus) / n_75_79 if n_75_79 > 0 else None
    incr['75-79'] = (n_75_79, wr_75_79)
    return incr


def portfolio_alpha(results, baseline_results=None):
    """Compute portfolio-weighted alpha as Σ(tier_alloc × n × wr_diff_vs_baseline_pp).

    If baseline_results=None, return Σ(tier_alloc × n × wr) absolute alpha.
    """
    incr = incremental_buckets(results)
    if baseline_results is not None:
        base_incr = incremental_buckets(baseline_results)
    total = 0.0
    breakdown = {}
    for tier, alloc in TIER_ALLOC_INCR.items():
        n, wr = incr.get(tier, (0, None))
        if n == 0 or wr is None: continue
        if baseline_results:
            bn, bwr = base_incr.get(tier, (0, None))
            if bn and bwr:
                # (cand_wr - base_wr) × cand_n × alloc — alpha vs baseline scaled by deployment
                contrib = (wr - bwr) * n * alloc / 100.0  # in $ per signal-base assuming 1.0 base
                total += contrib
                breakdown[tier] = (n, wr, bn, bwr, contrib)
        else:
            contrib = wr * n * alloc / 100.0
            total += contrib
            breakdown[tier] = (n, wr, contrib)
    return total, breakdown


def main():
    t0 = time.time()
    df = build_data_cache(1825)
    print(f"[data] {len(df):,} rows in {time.time()-t0:.1f}s", flush=True)
    barriers = load_barriers()
    print(f"[barriers] {len(barriers):,} rows W=15", flush=True)

    v27_path = str(ROOT / 'experiments' / 'v27_optimization' / 'phase_tp3b_lift_table.json')
    v34_path = str(ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34_preboost.json')

    variants = [
        Variant('PROD_v27',          v27_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, window=5),
        # Phase 1 winners + stacks
        Variant('V34_NC15_W7',       v34_path, max_boost=0.50, norm_call=15.0, norm_put=16.3, window=7),
        Variant('V34_W7',            v34_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, window=7),
        Variant('V34_NC15',          v34_path, max_boost=0.50, norm_call=15.0, norm_put=16.3, window=5),
        # MAX × NORM gradient — find the magnitude/saturation sweet spot
        Variant('V34_MAX55_NC15',    v34_path, max_boost=0.55, norm_call=15.0, norm_put=16.3, window=5),
        Variant('V34_MAX55_NC20',    v34_path, max_boost=0.55, norm_call=20.0, norm_put=16.3, window=5),
        Variant('V34_MAX60_NC15',    v34_path, max_boost=0.60, norm_call=15.0, norm_put=16.3, window=5),
        Variant('V34_MAX60_NC20',    v34_path, max_boost=0.60, norm_call=20.0, norm_put=16.3, window=5),
        Variant('V34_MAX65_NC30',    v34_path, max_boost=0.65, norm_call=30.0, norm_put=16.3, window=5),
        # MAX + W7 (gain from window extension + magnitude)
        Variant('V34_MAX55_W7',      v34_path, max_boost=0.55, norm_call=22.3, norm_put=16.3, window=7),
        Variant('V34_MAX60_W7',      v34_path, max_boost=0.60, norm_call=22.3, norm_put=16.3, window=7),
        Variant('V34_MAX55_NC15_W7', v34_path, max_boost=0.55, norm_call=15.0, norm_put=16.3, window=7),
        Variant('V34_MAX60_NC20_W7', v34_path, max_boost=0.60, norm_call=20.0, norm_put=16.3, window=7),
        # PUT-side combinations
        Variant('V34_NP25_NC20',     v34_path, max_boost=0.50, norm_call=20.0, norm_put=25.0, window=5),
        Variant('V34_PUT_ADMIT_W7',  v34_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, window=7, put_admit=True),
    ]

    results_5y = {}
    for v in variants:
        t1 = time.time()
        r = evaluate(v, df, barriers)
        print(f"[5y] {v.name}: {time.time()-t1:.1f}s, n_boosted={r['70+'][2]:,}", flush=True)
        results_5y[v.name] = r

    baseline = results_5y['PROD_v27']

    # Multi-window check
    multi_windows = {
        '1y': (date.today() - timedelta(days=365)).isoformat(),
        '3y': (date.today() - timedelta(days=3*365)).isoformat(),
    }
    multi_results = {v.name: {} for v in variants}
    for v in variants:
        for label, since in multi_windows.items():
            multi_results[v.name][label] = evaluate(v, df, barriers, since=since)

    # Print headline table
    print("\n" + "="*130)
    print("PHASE 2 — PORTFOLIO-WEIGHTED ALPHA RANKING (5y, vs PROD_v27)")
    print("="*130)
    rankings = []
    for v in variants:
        if v.name == 'PROD_v27': continue
        alpha_total, breakdown = portfolio_alpha(results_5y[v.name], baseline)
        rankings.append((v.name, alpha_total, breakdown, results_5y[v.name]))
    rankings.sort(key=lambda x: -x[1])
    print(f"{'Variant':<22} | {'Alpha Δ':>10} | {'95+ ΔWR/Δalloc':<22} | {'90-94 Δ':<13} | {'85-89 Δ':<13} | {'80-84 Δ':<13} | {'75-79 Δ':<13}")
    for name, alpha, brk, r in rankings:
        cells = [name, f'{alpha:+.2f}']
        for tier in ['95+', '90-94', '85-89', '80-84', '75-79']:
            if tier in brk:
                n, wr, bn, bwr, contrib = brk[tier]
                wr_d = wr - bwr
                n_d_pct = (n - bn) / bn * 100 if bn else 0
                cells.append(f'{wr_d:+.1f}/Δ{n_d_pct:+.0f}%')
            else:
                cells.append('-')
        print(f"{cells[0]:<22} | {cells[1]:>10} | " + " | ".join(f"{c:<13}" for c in cells[2:]))

    # Multi-window sign consistency check on key tiers
    print("\nH5 multi-window check (call tiers 75+, 80+, 85+, 90+):")
    print(f"{'Variant':<22} | {'75+':<25} | {'80+':<25} | {'85+':<25} | {'90+':<25}")
    for name, _, _, _ in rankings:
        cells = [name]
        for tier in ['75+', '80+', '85+', '90+']:
            d1 = multi_results[name]['1y'][tier][1] - multi_results['PROD_v27']['1y'][tier][1] \
                 if (multi_results[name]['1y'][tier][1] and multi_results['PROD_v27']['1y'][tier][1]) else None
            d3 = multi_results[name]['3y'][tier][1] - multi_results['PROD_v27']['3y'][tier][1] \
                 if (multi_results[name]['3y'][tier][1] and multi_results['PROD_v27']['3y'][tier][1]) else None
            d5 = results_5y[name][tier][1] - baseline[tier][1] \
                 if (results_5y[name][tier][1] and baseline[tier][1]) else None
            cells.append(f"{d1:+.1f}/{d3:+.1f}/{d5:+.1f}" if all(x is not None for x in [d1,d3,d5]) else "-")
        print(f"{cells[0]:<22} | " + " | ".join(f"{c:<25}" for c in cells[1:]))

    # Detailed top-3 candidates
    print("\n" + "="*130)
    print("TOP-3 CANDIDATES — DETAILED 5y vs PROD_v27")
    print("="*130)
    for name, alpha, brk, r in rankings[:3]:
        diff_against(baseline, r, name)
        passed, fails = h1_h5_gate(baseline, r)
        print(f"  H1-H5 5y gate (strict): {'PASS' if passed else 'FAIL'} (alpha Δ={alpha:+.2f})")
        for f in fails: print(f"    - {f}")

    # Save
    out = {
        'baseline': 'PROD_v27',
        'rankings': [(name, alpha) for name, alpha, _, _ in rankings],
        'top3': [{'name': name, 'alpha_delta': alpha,
                  'cum_5y': {k: list(v) for k, v in r.items()},
                  'multi_1y': {k: list(v) for k, v in multi_results[name]['1y'].items()},
                  'multi_3y': {k: list(v) for k, v in multi_results[name]['3y'].items()}}
                 for name, alpha, _, r in rankings[:3]],
    }
    with open(CACHE_DIR / 'phase2_results.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n[saved] {CACHE_DIR / 'phase2_results.json'}")
    print(f"[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
