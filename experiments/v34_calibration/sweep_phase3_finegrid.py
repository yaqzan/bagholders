"""Phase 3 — fine-grained grid around V34_MAX55_NC15 winner from Phase 2.

Goal: confirm local optimum and explore neighbors. Sweep MAX_BOOST ×
NORM_CALL with NORM_PUT pinned, plus a few NORM_PUT variants.

Validates winner at 1y/3y/5y windows with full detail.
"""
from __future__ import annotations
import io, sys, os, json, time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

sys.path.insert(0, str(ROOT / 'experiments' / 'v34_calibration'))
from sweep_boost_params import (
    Variant, build_data_cache, load_barriers, evaluate, h1_h5_gate, diff_against,
    CACHE_DIR
)


def main():
    t0 = time.time()
    df = build_data_cache(1825)
    barriers = load_barriers()
    print(f"[data] {len(df):,} rows, barriers {len(barriers):,}", flush=True)

    v27_path = str(ROOT / 'experiments' / 'v27_optimization' / 'phase_tp3b_lift_table.json')
    v34_path = str(ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34_preboost.json')

    variants = [Variant('PROD_v27', v27_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, window=5)]

    # 2D grid around (MAX=0.55, NC=15)
    for mb in [0.50, 0.52, 0.55, 0.57, 0.60]:
        for nc in [12, 14, 15, 17, 20]:
            variants.append(Variant(f'V34_M{int(mb*100)}_NC{nc}', v34_path,
                                     max_boost=mb, norm_call=nc, norm_put=16.3, window=5))

    # Add NORM_PUT variants on the winning shape
    for np_ in [12, 16.3, 20, 25]:
        variants.append(Variant(f'V34_M55_NC15_NP{int(np_)}', v34_path,
                                 max_boost=0.55, norm_call=15.0, norm_put=np_, window=5))

    results_5y = {}
    for v in variants:
        results_5y[v.name] = evaluate(v, df, barriers)

    multi = {'1y': (date.today()-timedelta(days=365)).isoformat(),
             '3y': (date.today()-timedelta(days=3*365)).isoformat()}
    multi_results = {v.name: {} for v in variants}
    for v in variants:
        for k, since in multi.items():
            multi_results[v.name][k] = evaluate(v, df, barriers, since=since)

    baseline = results_5y['PROD_v27']
    base_1y = multi_results['PROD_v27']['1y']
    base_3y = multi_results['PROD_v27']['3y']

    # Score each variant by:
    #  - sum of (WR - base_WR) at 5y across {95+, 90+, 85+, 80+}
    #  - subtract penalty for 1y/3y inconsistency
    print("\n" + "="*140)
    print("PHASE 3 — FINE-GRID 5y RESULTS (vs PROD_v27)")
    print("="*140)
    print(f"{'Variant':<22} | {'95+ Δ':>10} | {'90+ Δ':>10} | {'85+ Δ':>10} | {'80+ Δ':>10} | {'75+ Δ':>10} | {'<5 Δ':>10} | {'<15 Δ':>10} | {'sum_call':>10} | gate")
    print('-' * 140)
    rows = []
    for v in variants:
        if v.name == 'PROD_v27': continue
        cells = [v.name]
        sum_call = 0.0
        for tier in ['95+', '90+', '85+', '80+', '75+', '<5', '<15']:
            bwr = baseline.get(tier, (0, None))[1]
            cwr = results_5y[v.name].get(tier, (0, None))[1]
            if bwr is None or cwr is None:
                cells.append('-'); continue
            d = cwr - bwr
            cells.append(f'{d:+.2f}pp')
            if tier in ['95+', '90+', '85+', '80+', '75+']:
                sum_call += d
        cells.append(f'{sum_call:+.2f}')
        passed, _ = h1_h5_gate(baseline, results_5y[v.name])
        cells.append('PASS' if passed else 'fail')
        rows.append((v.name, sum_call, cells))
        print(' | '.join([f'{c:>10}' if i > 0 else f'{c:<22}' for i, c in enumerate(cells)]))

    # Sort by sum_call descending
    rows.sort(key=lambda x: -x[1])
    print("\nTop 5 by sum-call ΔWR:")
    for name, score, _ in rows[:5]:
        print(f"  {name:<22} | sum={score:+.2f}pp")

    # Detail top-3 with multi-window
    print("\n" + "="*140)
    print("TOP-3 — Multi-window detail (1y / 3y / 5y)")
    print("="*140)
    for name, score, _ in rows[:3]:
        print(f"\n>>> {name} (sum_call={score:+.2f}pp)")
        for w_label, base_w, cand_w in [('1y', base_1y, multi_results[name]['1y']),
                                         ('3y', base_3y, multi_results[name]['3y']),
                                         ('5y', baseline, results_5y[name])]:
            print(f"  {w_label}:")
            for tier in ['95+', '90+', '85+', '80+', '75+']:
                bn, bwr, _ = base_w.get(tier, (0, None, 0))
                cn, cwr, _ = cand_w.get(tier, (0, None, 0))
                dwr = (cwr - bwr) if (bwr and cwr) else None
                dn  = (100*(cn-bn)/bn) if bn else None
                if dwr is not None:
                    print(f"    {tier:<5}: Base={bwr:.2f}%/N={bn:,} → Cand={cwr:.2f}%/N={cn:,}  ΔWR={dwr:+.2f}pp  ΔN={dn:+.1f}%")

    # Save
    out = {
        'top10': [{'name': n, 'sum_call_5y': s} for n, s, _ in rows[:10]],
        'all_5y': {v.name: {b: list(r) for b, r in results_5y[v.name].items()} for v in variants},
    }
    with open(CACHE_DIR / 'phase3_results.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n[saved] {CACHE_DIR / 'phase3_results.json'}")
    print(f"[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
