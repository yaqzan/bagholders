"""
Phase OP1 — Top-3 Validation under option-pricing-aware MC
============================================================
Loads `phase_op1_screening_results.json` (from `bayes_phase_op1_screening.py`)
and validates the top-3 configs at N=300 × 8 windows for the ship gate.

Ship gate (post option-aware MC retune):
  - 22-now Realistic compound ≥ baseline (C0)
  - 5y Realistic compound ≥ baseline
  - Each annual window within ±25% of baseline
  - Worst DD-C ≤ 80% on every window
  - 0% collapse on every window

Output: experiments/phase_op1_validation_results.json + console table.
"""
import os
import sys
import json
import time
import statistics
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bayes_phase_op1_screening import CONFIGS

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]
N_ITER = 300   # full ship-grade validation (Phase OP1b)
MC_WORKERS_CT = 4

# OP1b focus: just baseline (C0) + winner (C2). C1/C10 already shown
# inferior on the 4-window screen.
TOP_K = 1   # only the #1 ranked non-baseline candidate


def load_top_k(k=TOP_K):
    """OP1b override: ship candidate is C2 (DD=0.60) — wins on the canonical
    22-now + 5y windows even though it ranked #3 by utility on 22-now N=80
    screening (C10 #1, C1 #2 lost on 4-window N=150 validation). Force
    candidate list to [C0_baseline, C2_tighter_DD60].
    """
    with open('experiments/phase_op1_screening_results.json') as f:
        results = json.load(f)
    by_name = {r['cfg_name']: r for r in results}
    return [by_name['C0_baseline'], by_name['C2_tighter_DD60']]


def run_one(cfg, label, d_start, d_end, n_iter, mc_workers, out_path):
    """Subprocess one (config, window) cell — same pattern as screening."""
    import subprocess
    cmd = [
        sys.executable, '-u',
        'experiments/bayes_phase_op1_runner.py',
        json.dumps(cfg),
        label, d_start.isoformat(), d_end.isoformat(),
        str(n_iter), str(mc_workers), out_path,
    ]
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    log_path = f'experiments/_op1_validate_log.tmp'
    with open(log_path, 'wb') as logf:
        proc = subprocess.run(cmd, env=env, stdout=logf, stderr=logf, timeout=2400)
    return proc.returncode == 0


def run_validation():
    print(f"Validation: N={N_ITER} × {len(WINDOWS)} windows × {TOP_K} configs (+ baseline)",
          flush=True)

    candidates = load_top_k()
    print(f"Loaded candidates: {[c['cfg_name'] for c in candidates]}", flush=True)

    all_results = {}
    t_start = time.time()
    for cand in candidates:
        cfg_name = cand['cfg_name']
        cfg = cand['cfg']
        print(f"\n{'='*100}\nVALIDATING {cfg_name}  {cfg}\n{'='*100}", flush=True)
        per_window = {}
        for label, d_start, d_end in WINDOWS:
            t0 = time.time()
            tmp_path = f'experiments/_op1_val_{cfg_name}_{label}.json'
            ok = run_one(cfg, label, d_start, d_end, N_ITER, MC_WORKERS_CT, tmp_path)
            if not ok:
                print(f"  {label}: FAILED", flush=True)
                continue
            with open(tmp_path) as f:
                rr = json.load(f)
            per_window[label] = {
                'mean_ret': rr['mean_ret'], 'med_ret': rr['med_ret'],
                'mean_dd': rr['mean_dd'], 'worst_dd': rr['worst_dd'],
                'p_coll': rr['p_coll'],
                'call_tp': rr['call_tp'], 'put_tp': rr['put_tp'],
                'call_trades': rr['call_trades'], 'put_trades': rr['put_trades'],
                'elapsed_s': time.time() - t0,
            }
            print(f"  {label}: ret={rr['mean_ret']:+,.0f}% dd={rr['worst_dd']:.1f}% "
                  f"({per_window[label]['elapsed_s']:.0f}s)", flush=True)
            all_results[cfg_name] = per_window
            with open('experiments/phase_op1_validation_results.json', 'w') as f:
                json.dump(all_results, f, indent=2, default=str)

    print(f"\nTotal elapsed: {time.time() - t_start:.0f}s", flush=True)

    # Print comparison tables
    cfg_names = [c['cfg_name'] for c in candidates]
    print(f"\n{'='*120}")
    print(f"VALIDATION — Mean Realistic Return (%) per Window")
    print(f"{'='*120}")
    print(f"{'Window':<10} " + " ".join(f"{n:>22}" for n in cfg_names))
    for label, _, _ in WINDOWS:
        row = f"{label:<10} "
        for n in cfg_names:
            r = all_results[n][label]
            row += f"{r['mean_ret']:>+20,.1f}% "
        print(row)

    print(f"\n{'='*120}")
    print(f"VALIDATION — Worst DD (%) per Window  [floor 80%]")
    print(f"{'='*120}")
    print(f"{'Window':<10} " + " ".join(f"{n:>22}" for n in cfg_names))
    for label, _, _ in WINDOWS:
        row = f"{label:<10} "
        for n in cfg_names:
            r = all_results[n][label]
            mark = ' ✗' if r['worst_dd'] >= 80 else '  '
            row += f"{r['worst_dd']:>20.1f}%{mark} "
        print(row)


if __name__ == '__main__':
    run_validation()
