"""
Phase OP2 Phase 3 — canonical MC validation on top-K candidates.

Reads experiments/op2/phase2_results.json. For top-K candidates (default 3),
runs canonical monte_carlo.py at N=300 across 8 windows. Applies ship gate:

  - 5y compound ≥ baseline AND
  - 22-now compound ≥ baseline AND
  - every per-window WorstDD ≤ 80% AND
  - every annual within ±25% of baseline AND
  - 0% collapse rate on every window

Architecture: subprocess-per-config via op2_runner.py (Windows MP pool isolation).
Within each subprocess, MC runs single-process at N=300 (no MC pool — to ensure
in-process monkey-patches propagate).

Per-candidate cost: ~50 min (8 windows × N=300 single-process).
Top-3 = ~2.5h total.

Usage:
    PYTHONIOENCODING=utf-8 python experiments/op2/phase3_canonical_mc.py [TOP_K]
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

N_ITER  = 300
DD_FLOOR = 0.80


# ── Map Phase 2 axis_values → cfg dict consumed by op2_runner ──────
def axis_values_to_cfg(axis_values: dict) -> dict:
    cfg = {}
    for axis, val in axis_values.items():
        if axis == 'DD':
            cfg['DD_CIRCUIT_BREAKER'] = float(val)
        elif axis == 'PREM':
            cfg['PREM_STOP_LOSS'] = -abs(float(val))
        elif axis == 'SL':
            cfg['SL_BASE']   = -abs(float(val))
            cfg['SL_STRESS'] = -abs(float(val) + 0.05)
        elif axis == 'HOLD':
            cfg['HOLD_DAYS'] = int(val)
        elif axis == 'MAXPOS':
            cfg['MAX_POSITIONS'] = int(val)
        elif axis == 'ULTRA':
            ultra = float(val)
            cfg['TIER_ALLOC'] = {
                'ultra': ultra, 'top': max(0.10, ultra - 0.06),
                'mid': 0.15, 'low': 0.15, 'overflow': 0.0,
            }
        elif axis == 'CASCADE':
            mid_low = float(val)
            cfg['TIER_ALLOC'] = {
                'ultra': 0.18, 'top': 0.12, 'mid': mid_low,
                'low': mid_low, 'overflow': 0.0,
            }
        elif axis.startswith('RA_'):
            strat_map = {'RA_eslow':'entry_score_low','RA_pnllow':'current_pnl_low',
                         'RA_pnlhigh':'current_pnl_high','RA_old':'days_held_high',
                         'RA_far':'sigma_distance_low'}
            cfg['REALLOC_STRATEGY']      = strat_map[axis]
            cfg['REALLOC_MIN_ADVANTAGE'] = float(val)
    return cfg


def run_one_window(cfg: dict, label: str, d_start, d_end, n_iter: int):
    """Subprocess-per-window invocation of op2_runner."""
    runner = os.path.join(ROOT, 'experiments', 'op2', 'op2_runner.py')
    out_path = tempfile.mktemp(suffix='.json')
    cmd = [sys.executable, '-u', runner,
           json.dumps(cfg), label, d_start.isoformat(), d_end.isoformat(),
           str(n_iter), '1', out_path]
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['MC_NO_MP'] = '1'
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return {'error': 'timeout', 'elapsed': time.time() - t0}
    elapsed = time.time() - t0
    if not os.path.exists(out_path):
        return {'error': 'no output', 'tail': (proc.stderr or proc.stdout)[-1500:],
                'elapsed': elapsed}
    with open(out_path) as fh:
        out = json.load(fh)
    try:
        os.remove(out_path)
    except OSError:
        pass
    out['elapsed'] = round(elapsed, 1)
    return out


def run_canonical_for_candidate(name: str, axis_values: dict, n_iter: int = N_ITER):
    """Run all 8 windows for a candidate IN PARALLEL via multiprocessing.
    Returns {window: result_dict}.

    Each window is a fresh subprocess (op2_runner.py) so module-level patches
    don't leak between configs. Within each subprocess, MC runs single-process
    (MC_NO_MP=1) at n_iter iterations.

    Parallel workers: 4 (one per CPU core). 8 windows / 4 workers = 2 batches.
    """
    cfg = axis_values_to_cfg(axis_values)
    print(f"\n=== {name} ===")
    print(f"  cfg: {cfg}")
    sys.stdout.flush()

    # Submit all 8 windows in parallel
    from concurrent.futures import ProcessPoolExecutor, as_completed
    futs = {}
    with ProcessPoolExecutor(max_workers=4) as ex:
        for label, d_start, d_end in WINDOWS:
            futs[ex.submit(run_one_window, cfg, label, d_start, d_end, n_iter)] = label
        results = {}
        for f in as_completed(futs):
            label = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {'error': str(e)}
            if 'error' in r:
                print(f"  {label:<8} ERROR: {r['error']}  [{r.get('elapsed',0):.0f}s]")
                results[label] = r
            else:
                print(f"  {label:<8} mean_ret={r['mean_ret']:>+15,.1f}%  "
                      f"DD={r['worst_dd']:>5.1f}%  collapse={r['p_coll']:>4.1f}%  "
                      f"C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})  [{r['elapsed']:.0f}s]")
                results[label] = r
            sys.stdout.flush()
    return results


def evaluate_ship_gate(cand: dict, baseline: dict) -> dict:
    diag = {'gates': {}, 'pass': True}
    breaches_dd, breaches_coll, breaches_annual = [], [], []
    for w, r in cand.items():
        if 'error' in r:
            breaches_dd.append(f"{w}:err"); breaches_coll.append(f"{w}:err"); continue
        if r['worst_dd'] > DD_FLOOR * 100: breaches_dd.append(f"{w}:{r['worst_dd']:.1f}%")
        if r['p_coll'] > 0:                breaches_coll.append(f"{w}:{r['p_coll']:.1f}%")
    diag['gates']['dd_floor_80']  = {'pass': not breaches_dd,   'breaches': breaches_dd}
    diag['gates']['no_collapse']  = {'pass': not breaches_coll, 'breaches': breaches_coll}
    cand_5y = cand.get('5y', {}).get('mean_ret')
    base_5y = baseline.get('5y', {}).get('mean_ret')
    diag['gates']['5y_compound']  = {
        'pass': cand_5y is not None and base_5y is not None and cand_5y >= base_5y,
        'cand': cand_5y, 'baseline': base_5y,
    }
    cand_22 = cand.get('22-now', {}).get('mean_ret')
    base_22 = baseline.get('22-now', {}).get('mean_ret')
    diag['gates']['22-now_compound'] = {
        'pass': cand_22 is not None and base_22 is not None and cand_22 >= base_22,
        'cand': cand_22, 'baseline': base_22,
    }
    for w in ['2021','2022','2023','2024','2025']:
        cr = cand.get(w, {}).get('mean_ret'); br = baseline.get(w, {}).get('mean_ret')
        if cr is None or br is None:
            breaches_annual.append(f"{w}:err"); continue
        if br > 0 and cr < 0.75 * br:
            breaches_annual.append(f"{w}:Δ{(cr/br-1)*100:.1f}%")
    diag['gates']['annual_25pct'] = {'pass': not breaches_annual, 'breaches': breaches_annual}
    diag['pass'] = all(g['pass'] for g in diag['gates'].values())
    return diag


def main():
    p2_path = os.path.join(ROOT, 'experiments', 'op2', 'phase2_results.json')
    if not os.path.exists(p2_path):
        print(f"ERROR: phase2 results not found at {p2_path}.")
        sys.exit(1)
    with open(p2_path) as fh:
        p2 = json.load(fh)

    top_k = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    valid = [c for c in p2['candidates'] if 'error' not in c]
    valid.sort(key=lambda c: -c['utility'])
    candidates = valid[:top_k]
    print(f"PHASE 3 — canonical MC validation on top-{top_k} candidates")
    print(f"Each candidate × {len(WINDOWS)} windows × N={N_ITER} iters")

    print(f"\n{'-'*100}\nBASELINE (current shipped config)\n{'-'*100}")
    baseline_results = run_canonical_for_candidate('baseline', {}, N_ITER)

    out = {'baseline': baseline_results, 'candidates': []}
    for cand in candidates:
        name = ', '.join(f"{k}={v}" for k, v in cand['axis_values'].items())
        cand_results = run_canonical_for_candidate(name, cand['axis_values'], N_ITER)
        gate = evaluate_ship_gate(cand_results, baseline_results)
        out['candidates'].append({
            'axis_values': cand['axis_values'],
            'phase2_util': cand['utility'],
            'mc_results':  cand_results,
            'gate':        gate,
        })
        gate_str = "PASS [SHIP]" if gate['pass'] else "FAIL"
        print(f"\n  SHIP GATE: {gate_str}")
        for gname, gdata in gate['gates'].items():
            print(f"    {'PASS' if gdata['pass'] else 'FAIL'} {gname}: {gdata}")
        sys.stdout.flush()

    out_path = os.path.join(ROOT, 'experiments', 'op2', 'phase3_canonical_results.json')
    with open(out_path, 'w') as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nSaved -> {out_path}")
    print("\n" + "=" * 110)
    print("PHASE 3 — SHIP GATE SUMMARY")
    print("=" * 110)
    passing = [c for c in out['candidates'] if c['gate']['pass']]
    print(f"Candidates passing all gates: {len(passing)} of {len(out['candidates'])}")
    if passing:
        print("\nSHIP CANDIDATES (in priority order):")
        for c in passing:
            av = ', '.join(f"{k}={v}" for k, v in c['axis_values'].items())
            print(f"  - util={c['phase2_util']:+.2f}  [{av}]")


if __name__ == '__main__':
    main()
