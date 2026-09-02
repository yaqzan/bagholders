"""
Portfolio v32 — Phase 2 (custom selection): 4-window N=150 validation.

Hand-picked from Phase 1 results to test BOTH directions:
- "Win-win" candidates that improved BOTH ret AND DD at N=100
- "Safer-DD" candidates that traded return for DD reduction (user's stated preference)
- Priority modes are EXCLUDED — Phase 1 showed both modes lose ~100% (catastrophic).

Windows: {2022, 2024, 22-now, 5y} — covers bear/bull/headline windows.
"""
import json
import os
import sys
import time
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

CANDIDATES = [
    # Baseline
    ('baseline', {}),

    # Win-win at N=100 (lower DD AND higher ret)
    ('D_midlow_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.12, 'low': 0.12, 'overflow': 0.0},
    }),
    ('B_alloc_x0.85', {
        'TIER_ALLOC':     {'ultra': 0.153, 'top': 0.102, 'mid': 0.128, 'low': 0.128, 'overflow': 0.0},
        'PUT_TIER_ALLOC': {'put_top': 0.085, 'put_mid': 0.102, 'put_low': 0.102},
    }),

    # Safer-DD trade-off candidates (user's "trade compounding for DD" hypothesis)
    ('D_midlow_08', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.08, 'low': 0.08, 'overflow': 0.0},
    }),
    ('H_x0.75_MP12', {
        'TIER_ALLOC':     {'ultra': 0.135, 'top': 0.090, 'mid': 0.113, 'low': 0.113, 'overflow': 0.0},
        'PUT_TIER_ALLOC': {'put_top': 0.075, 'put_mid': 0.090, 'put_low': 0.090},
        'MAX_POSITIONS':  12,
    }),

    # F_maxpos_16 — high-utility low-DD MaxPos variant
    ('F_maxpos_16', {'MAX_POSITIONS': 16}),
]


def main():
    print(f"Phase 2 custom — {len(CANDIDATES)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    for name, cfg in CANDIDATES:
        print(f"  {name}: {cfg}")
    print()

    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase2_custom_results.json')
    results = {name: {} for name, _ in CANDIDATES}

    t0 = time.time()
    with ProcessPoolExecutor(max_workers=4) as ex:
        futs = {}
        for name, cfg in CANDIDATES:
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

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 130}")
    print(f"SUMMARY (N={N_ITER})")
    print(f"{'=' * 130}")
    print(f"{'name':<22}", end='')
    for label, _, _ in WINDOWS:
        print(f" | {label:<24}", end='')
    print()
    print("-" * 130)
    for name, _ in CANDIDATES:
        print(f"  {name:<22}", end='')
        for label, _, _ in WINDOWS:
            r = results[name].get(label, {})
            if 'error' in r or 'mean_ret' not in r:
                cell = "ERR"
            else:
                cell = f"r={r['mean_ret']:>+11,.0f}% DD={r['worst_dd']:.1f}%"
            print(f" | {cell:<24}", end='')
        print()

    print(f"\n{'=' * 130}")
    print("vs BASELINE")
    print(f"{'=' * 130}")
    base = results['baseline']
    for name, _ in CANDIDATES:
        if name == 'baseline':
            continue
        print(f"\n  {name}")
        for label, _, _ in WINDOWS:
            cand = results[name].get(label, {})
            b = base.get(label, {})
            if 'error' in cand or 'error' in b:
                continue
            ret_ratio = (1 + cand['mean_ret']/100) / max(1 + b['mean_ret']/100, 1e-9)
            d_dd = cand.get('worst_dd', 0) - b.get('worst_dd', 0)
            print(f"    {label:<8} ret×{ret_ratio:>8.2f}  DD Δ{d_dd:>+5.1f}pp  "
                  f"(cand DD={cand.get('worst_dd', 0):.1f}%, base={b.get('worst_dd', 0):.1f}%)")

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")


if __name__ == '__main__':
    main()
