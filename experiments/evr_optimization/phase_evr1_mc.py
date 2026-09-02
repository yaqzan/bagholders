"""
Phase EVR-1 canonical MC validation via sim→MC bridge (2026-05-01).

Now that Phase OP2 is closed (DD060 + dead-hold + bimodal shipped), EVR-1
is unblocked. Per-trade gate already passed for OFF (admits +8 95+ peaks,
+18 90+ peaks at marginal WR ~75-77%, above call BE).

This run: V0 (production V6, M=1.0 log) vs V_OFF (M=0.0, no suppression).
Full universe × 5y simulator + canonical MC × 8 windows × N=300.

Methodology — uses experiments/sim_mc_bridge.py to feed simulated scores
into MC without DB recalculate. Per-variant cost:
  - simulator data-load: ~3-5 min one-time
  - simulate_full per variant: ~5-15 min
  - MC 8 windows × N=300: ~25 min
  - Total per variant: ~30-40 min
  - Total for 2 variants: ~60-90 min (vs ~50 min recalculate path,
    but no DB pollution and reusable for further sweeps)

Output: experiments/evr_optimization/phase_evr1_mc_results.json
"""
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from simulator import ScoreSimulator
from experiments.sim_mc_bridge import build_score_lists, inject_into_mc
import volume_amplifier as va
import monte_carlo as mc

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 30)),
    ('5y',     date(2021, 4, 30), date(2026, 4, 30)),
]
N_ITER = 300
LOOKBACK_DAYS = 1825 + 90  # 5y + buffer


def patch_earnings_supp_for_variant(variant: str):
    """Monkey-patch volume_amplifier._earnings_supp_strength for the given
    variant. Returns (saved_state) for restoration.

    Variants:
      'B' — production V6, M=1.0 log gradient (no patch, restore default)
      'OFF' — M=0.0, no suppression at all
    """
    saved = (va.EARN_SUPP_MAX_STRENGTH, va.EARN_SUPP_WINDOW_DAYS)
    if variant == 'B':
        va.EARN_SUPP_MAX_STRENGTH = 1.0
        va.EARN_SUPP_WINDOW_DAYS = 2
    elif variant == 'OFF':
        va.EARN_SUPP_MAX_STRENGTH = 0.0
        # WINDOW_DAYS irrelevant when MAX_STRENGTH=0
    else:
        raise ValueError(f"Unknown variant: {variant}")
    return saved


def restore_earnings_supp(saved):
    va.EARN_SUPP_MAX_STRENGTH, va.EARN_SUPP_WINDOW_DAYS = saved


def run_variant(variant: str, sim: ScoreSimulator):
    """Set variant's volume_amplifier override, run simulate_full, then run
    MC across all 8 windows. Returns dict of window → result."""
    print(f"\n{'='*72}", flush=True)
    print(f"VARIANT: {variant}", flush=True)
    print(f"  EARN_SUPP_MAX_STRENGTH={va.EARN_SUPP_MAX_STRENGTH}", flush=True)
    print(f"  EARN_SUPP_WINDOW_DAYS={va.EARN_SUPP_WINDOW_DAYS}", flush=True)
    print('='*72, flush=True)

    t0 = time.time()
    rich = sim.simulate_full()
    rows = build_score_lists(rich)
    sim_elapsed = time.time() - t0
    print(f"\n[sim] {len(rows)} rich rows in {sim_elapsed:.1f}s", flush=True)

    n_calls = sum(1 for r in rows if r.overall >= mc.OVERFLOW_THRESHOLD)
    n_puts = sum(1 for r in rows if r.overall <= mc.PUT_THRESHOLD)
    print(f"[sim] qualifying: {n_calls} calls (>=70), {n_puts} puts (<=25)", flush=True)

    mc.N_ITER = N_ITER
    os.environ['MC_NO_MP'] = '1'
    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()

    results = {}
    with inject_into_mc(rows):
        # MC windows in parallel via subprocess would require more plumbing
        # (the patched loaders won't survive a subprocess boundary). Run
        # sequentially within this process — adds ~3-5 min vs parallel.
        for label, d_start, d_end in WINDOWS:
            t1 = time.time()
            print(f"\n--- {variant}/{label} ---", flush=True)
            try:
                res = mc.run_window(label, d_start, d_end, version)
                r = res['seeded']
                results[label] = {
                    'mean_ret': r['mean_ret'],
                    'med_ret':  r['med_ret'],
                    'mean_dd':  r['mean_dd'],
                    'worst_dd': r['worst_dd'],
                    'p_coll':   r['p_coll'],
                    'call_tp':  r['call_tp'],
                    'put_tp':   r['put_tp'],
                    'call_trades': r['call_trades'],
                    'put_trades':  r['put_trades'],
                    'elapsed': round(time.time() - t1, 1),
                }
                print(f"  ret={r['mean_ret']:>+15,.1f}%  DD={r['worst_dd']:>5.1f}%  "
                      f"coll={r['p_coll']:>4.1f}%  C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})  "
                      f"[{(time.time() - t1):.0f}s]", flush=True)
            except Exception as e:
                results[label] = {'error': str(e)}
                print(f"  ERROR: {e}", flush=True)

    return results


def evaluate_gates(res: dict, baseline: dict = None):
    breaches = []
    for label, _, _ in WINDOWS:
        r = res.get(label, {})
        if 'error' in r:
            breaches.append(f"{label}: ERROR")
            continue
        dd = r.get('worst_dd', 100.0)
        coll = r.get('p_coll', 100.0)
        if dd > 80.0:
            breaches.append(f"{label}: DD={dd:.1f}% (>80%)")
        if coll > 0.5:
            breaches.append(f"{label}: coll={coll:.1f}%")
    out = {'breaches': breaches, 'pass': len(breaches) == 0}
    if baseline:
        # Annual gate: cand within ±25% of baseline on each annual window
        for label in ('2021', '2022', '2023', '2024', '2025'):
            rb = baseline.get(label, {})
            rc = res.get(label, {})
            if 'error' in rc or 'error' in rb:
                continue
            rb_ret = rb.get('mean_ret', 0)
            rc_ret = rc.get('mean_ret', 0)
            if rb_ret > 0 and rc_ret < rb_ret * 0.75:
                pct = (rc_ret/rb_ret - 1.0) * 100
                out['breaches'].append(f"{label}: ret {pct:+.1f}% vs baseline (>25%)")
    out['pass'] = len(out['breaches']) == 0
    return out


def main():
    t_start = time.time()
    print(f"=== Phase EVR-1 canonical MC via sim→MC bridge ===", flush=True)
    print(f"N_ITER={N_ITER}, lookback={LOOKBACK_DAYS}d, windows={len(WINDOWS)}", flush=True)
    print(f"Variants: B (production V6) vs OFF (M=0.0)", flush=True)
    print(f"Baseline: DD060 + dead-hold + bimodal (current shipped)", flush=True)

    print("\n[init] Building simulator (full universe × 5y)...", flush=True)
    t0 = time.time()
    from database.models.core import Stock
    symbols = [s.symbol for s in Stock.select()]
    print(f"  loading {len(symbols)} symbols...", flush=True)
    sim = ScoreSimulator(symbols=symbols, lookback_days=LOOKBACK_DAYS)
    print(f"[init] simulator ready in {(time.time() - t0):.1f}s", flush=True)

    all_results = {}
    for variant in ('B', 'OFF'):
        saved = patch_earnings_supp_for_variant(variant)
        try:
            all_results[variant] = run_variant(variant, sim)
        finally:
            restore_earnings_supp(saved)

    print(f"\n\n{'='*72}", flush=True)
    print("VERDICT", flush=True)
    print('='*72, flush=True)

    print(f"\n{'window':<10} {'B (V6 prod)':<35} {'OFF':<35}", flush=True)
    for label, _, _ in WINDOWS:
        cells = []
        for v in ('B', 'OFF'):
            r = all_results.get(v, {}).get(label, {})
            if 'error' in r:
                cells.append("ERROR".ljust(34))
            else:
                cells.append(f"ret={r.get('mean_ret', 0):+12,.0f}%  DD={r.get('worst_dd', 0):>5.1f}%  coll={r.get('p_coll', 0):>4.1f}%".ljust(34))
        print(f"{label:<10} {cells[0]} {cells[1]}", flush=True)

    print("\nGATES:", flush=True)
    base_gate = evaluate_gates(all_results.get('B', {}))
    print(f"  B (V6 prod): {'PASS ✓' if base_gate['pass'] else 'FAIL ✗'}", flush=True)
    for b in base_gate['breaches']:
        print(f"    {b}", flush=True)
    off_gate = evaluate_gates(all_results.get('OFF', {}), baseline=all_results.get('B'))
    print(f"  OFF: {'PASS ✓' if off_gate['pass'] else 'FAIL ✗'}", flush=True)
    for b in off_gate['breaches']:
        print(f"    {b}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'phase_evr1_mc_results.json')
    payload = {
        'n_iter': N_ITER,
        'baseline': 'DD060+dead-hold+bimodal',
        'results': all_results,
        'gates': {'B': base_gate, 'OFF': off_gate},
        'elapsed_min': round((time.time() - t_start) / 60.0, 1),
    }
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\nResults → {out_path}", flush=True)
    print(f"Total elapsed: {payload['elapsed_min']:.1f} min", flush=True)


if __name__ == '__main__':
    main()
