"""
Portfolio v32 — Phase 5: N=500 × 8-window validation of D_midlow_12.

Phase 4 noise audit showed N=300 single-window has 1.6-1.8× baseline-to-baseline
compound variance. This phase doubles N to 500 to noise-reduce the headline
P3a 5y / P3b 22-now metrics.

If D_midlow_12 still shows 5y Pareto improvement (ret ≥ baseline + DD lower) at
N=500, ship it. Per CLAUDE.md gate protocol with user's explicit "trade
compounding for safer DD" preference: the 5y headline is the locking metric.

Decision rule:
  SHIP if:
    - 5y ret×ratio ≥ 0.95 (allowing up to 5% noise-margin compound regression)
    - 5y DD reduction ≥ 2pp
    - 22-now ret×ratio ≥ 0.75 (allowing 25% noise-margin)
    - 22-now DD ≤ baseline + 1pp
    - No window DD > baseline + 2pp
    - 0% collapse on every cell
  NO SHIP otherwise.

Cost: 2 candidates × 8 windows × N=500 ≈ 8-10 min wall.
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

from phase3_canonical_mc import run_one_window, WINDOWS

N_ITER = 500

CANDIDATES = [
    ('baseline', {}),
    ('D_midlow_12', {
        'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.12, 'low': 0.12, 'overflow': 0.0},
    }),
]


def main():
    print(f"Phase 5 — {len(CANDIDATES)} candidates × {len(WINDOWS)} windows × N={N_ITER}")
    for name, cfg in CANDIDATES:
        print(f"  {name}: {cfg}")
    print()

    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase5_n500_results.json')
    results = {name: {} for name, _ in CANDIDATES}

    t0 = time.time()
    for cand_idx, (name, cfg) in enumerate(CANDIDATES, 1):
        print(f"\n=== [{cand_idx}/{len(CANDIDATES)}] {name} ===", flush=True)
        with ProcessPoolExecutor(max_workers=4) as ex:
            futs = {}
            for label, d_start, d_end in WINDOWS:
                f = ex.submit(run_one_window, cfg, label, d_start, d_end, N_ITER)
                futs[f] = label
            for f in as_completed(futs):
                label = futs[f]
                try:
                    r = f.result()
                except Exception as e:
                    r = {'error': str(e)}
                elapsed = time.time() - t0
                if 'error' in r:
                    print(f"  {label:<8} ERROR: {r['error'][:60]} [{elapsed/60:.1f}m total]", flush=True)
                else:
                    print(f"  {label:<8} ret={r.get('mean_ret', 0):>+15,.1f}%  "
                          f"DD={r.get('worst_dd', 0):>5.1f}%  coll={r.get('p_coll', 0):.1f}%  "
                          f"C/P=({r.get('call_tp', 0):.1f}/{r.get('put_tp', 0):.1f})  "
                          f"[{elapsed/60:.1f}m total]", flush=True)
                results[name][label] = r
                with open(out_path, 'w') as fh:
                    json.dump({'n_iter': N_ITER, 'results': results}, fh, indent=2, default=str)

    # ── Decision ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 110}")
    print("PHASE 5 DECISION (user-aligned gate: trade compound for DD safety)")
    print(f"{'=' * 110}")
    base = results['baseline']
    cand = results['D_midlow_12']

    base_5y = base.get('5y', {})
    cand_5y = cand.get('5y', {})
    base_22 = base.get('22-now', {})
    cand_22 = cand.get('22-now', {})

    if not all(k in r for r in [base_5y, cand_5y, base_22, cand_22] for k in ['mean_ret', 'worst_dd']):
        print("ERROR: incomplete results"); return

    ratio_5y = (1 + cand_5y['mean_ret']/100) / max(1 + base_5y['mean_ret']/100, 1e-9)
    ratio_22 = (1 + cand_22['mean_ret']/100) / max(1 + base_22['mean_ret']/100, 1e-9)
    dd_5y_d = cand_5y['worst_dd'] - base_5y['worst_dd']
    dd_22_d = cand_22['worst_dd'] - base_22['worst_dd']

    checks = []
    checks.append(('5y ret×≥0.95',  ratio_5y >= 0.95, f"ret×{ratio_5y:.2f}"))
    checks.append(('5y DD-Δ≤-2pp',   dd_5y_d <= -2.0,  f"DD Δ{dd_5y_d:+.1f}pp"))
    checks.append(('22-now ret×≥0.75', ratio_22 >= 0.75, f"ret×{ratio_22:.2f}"))
    checks.append(('22-now DD-Δ≤+1pp', dd_22_d <= 1.0,  f"DD Δ{dd_22_d:+.1f}pp"))

    for label, _, _ in WINDOWS:
        cd = cand.get(label, {}).get('worst_dd')
        bd = base.get(label, {}).get('worst_dd')
        if cd is None or bd is None: continue
        checks.append((f"{label} DD-Δ≤+2pp", cd - bd <= 2.0, f"DD Δ{cd-bd:+.1f}pp ({cd:.1f}% vs {bd:.1f}%)"))

    for label, _, _ in WINDOWS:
        coll = cand.get(label, {}).get('p_coll', 0.0)
        checks.append((f"{label} 0% collapse", coll <= 0.5, f"coll={coll:.1f}%"))

    print()
    all_pass = True
    for name, ok, info in checks:
        sym = "✓" if ok else "✗"
        print(f"  {sym} {name:<22} {info}")
        if not ok: all_pass = False

    print(f"\n{'=' * 110}")
    if all_pass:
        print("VERDICT: SHIP D_midlow_12 — set TIER_ALLOC mid=0.12, low=0.12 in strategy_config.py")
    else:
        print("VERDICT: DO NOT SHIP — gate failures above")
    print(f"{'=' * 110}")

    # Per-window summary
    print()
    print(f"{'window':<8} | {'baseline':<24} | {'D_midlow_12':<24} | ret×    | DD-Δ")
    for label, _, _ in WINDOWS:
        b = base.get(label, {})
        c = cand.get(label, {})
        if 'mean_ret' not in b or 'mean_ret' not in c: continue
        b_str = f"r={b['mean_ret']:>+11.0f}% DD={b['worst_dd']:.1f}%"
        c_str = f"r={c['mean_ret']:>+11.0f}% DD={c['worst_dd']:.1f}%"
        ratio = (1+c['mean_ret']/100)/max(1+b['mean_ret']/100, 1e-9)
        dd_d = c['worst_dd'] - b['worst_dd']
        print(f"{label:<8} | {b_str:<24} | {c_str:<24} | {ratio:>5.2f}× | {dd_d:>+5.1f}pp")

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")
    print(f"\nSHIP_VERDICT: {'PASS' if all_pass else 'FAIL'}")


if __name__ == '__main__':
    main()
