"""
Portfolio v32 — Phase 2: 4-window N=150 validation of top-3 candidates from Phase 1.

Phase OP1 lesson: N=80 22-now screening + N=150 4-window validation can flip at
N=300 × 8 windows. This phase reduces — but does not eliminate — that risk.
Final ship gate is Phase 3 (N=300 × 8 windows).

Reads phase1_screen_results.json, picks top-3 + baseline, re-runs at N=150 across
{2022, 2024, 22-now, 5y}. Cheap-but-strong signal:
  - 2022 = bear stress
  - 2024 = bull (high signal density)
  - 22-now = continuous compounding
  - 5y = the headline canonical window

Usage:
    PYTHONIOENCODING=utf-8 python experiments/portfolio_v32/phase2_validate.py [TOP_K]
"""
import json
import os
import sys
import time
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'experiments', 'op2'))

from phase3_canonical_mc import run_one_window

WINDOWS = [
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 30)),
    ('5y',     date(2021, 4, 30), date(2026, 4, 30)),
]
N_ITER = 150
TOP_K = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def main():
    p1_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase1_screen_results.json')
    if not os.path.exists(p1_path):
        sys.exit(f"Phase 1 results missing at {p1_path}")

    p1 = json.load(open(p1_path))
    base = p1['results'].get('baseline', {}).get('result', {})
    if 'mean_ret' not in base:
        sys.exit("Phase 1 baseline result missing")
    base_ret = base.get('mean_ret', 0.0)
    base_dd = base.get('worst_dd', 0.0)

    # Rank by phase 1 utility
    def utility(r):
        if 'mean_ret' not in r:
            return -999
        cand_mult = max(1.0 + r['mean_ret']/100.0, 1e-9)
        base_mult = max(1.0 + base_ret/100.0, 1e-9)
        log_ratio = math.log(cand_mult / base_mult)
        dd_excess = max(0.0, r['worst_dd'] - base_dd) / 100.0
        dd_penalty = 50.0 * dd_excess ** 2
        coll_penalty = 0.05 * r.get('p_coll', 0.0)
        return log_ratio - dd_penalty - coll_penalty

    ranked = []
    for name, entry in p1['results'].items():
        if name == 'baseline':
            continue
        r = entry.get('result', {})
        if 'error' in r or 'mean_ret' not in r:
            continue
        ranked.append((utility(r), name, entry['cand']['cfg']))
    ranked.sort(reverse=True)

    selected = [('baseline', {})]
    for u, name, cfg in ranked[:TOP_K]:
        selected.append((name, cfg))

    print(f"Phase 2 — {len(selected)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    for name, cfg in selected:
        print(f"  {name}: {cfg}")
    print()

    # Run all (candidate × window) tuples in parallel
    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase2_validate_results.json')
    results = {name: {} for name, _ in selected}

    t0 = time.time()
    with ProcessPoolExecutor(max_workers=4) as ex:
        futs = {}
        for name, cfg in selected:
            for label, d_start, d_end in WINDOWS:
                f = ex.submit(run_one_window, cfg, label, d_start, d_end, N_ITER)
                futs[f] = (name, label)
        completed = 0
        total = len(futs)
        for f in as_completed(futs):
            name, label = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {'error': str(e)}
            completed += 1
            elapsed = time.time() - t0
            if 'error' in r:
                print(f"  [{completed:>2}/{total}] {name:<22} {label:<7} ERROR: {r['error'][:60]} "
                      f"[{elapsed/60:.1f}m]", flush=True)
            else:
                print(f"  [{completed:>2}/{total}] {name:<22} {label:<7} "
                      f"ret={r.get('mean_ret', 0):>+15,.1f}%  DD={r.get('worst_dd', 0):>5.1f}%  "
                      f"coll={r.get('p_coll', 0):.1f}%  [{elapsed/60:.1f}m]",
                      flush=True)
            results[name][label] = r
            with open(out_path, 'w') as fh:
                json.dump({'n_iter': N_ITER, 'windows': [w[0] for w in WINDOWS],
                           'results': results}, fh, indent=2, default=str)

    # ── Summary table ────────────────────────────────────────────────────
    print(f"\n{'=' * 110}")
    print(f"SUMMARY (N={N_ITER}, vs baseline)")
    print(f"{'=' * 110}")
    print(f"{'name':<22} | " + " | ".join(f"{w:<22}" for w, _, _ in WINDOWS))
    print("-" * 110)
    for name, _ in selected:
        row = [name]
        for label, _, _ in WINDOWS:
            r = results[name].get(label, {})
            if 'error' in r or 'mean_ret' not in r:
                row.append("ERR")
            else:
                row.append(f"r={r['mean_ret']:>+12.1f}% DD={r['worst_dd']:.1f}%")
        print(f"  {row[0]:<22} | " + " | ".join(f"{c:<22}" for c in row[1:]))

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")


if __name__ == '__main__':
    main()
