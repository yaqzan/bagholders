"""Phase 15A validation — top-K from sweep at full N=500 across 8 windows.

Reads experiments/bayes_logs/phase_v15dte_phase_15a.jsonl, picks top-K by
utility, runs each at N=500 across all 8 canonical windows, prints comparison
vs the 30 DTE baseline (mc_30dte_n200.out).

Decision gate:
  - 22-now Real compound > 30 DTE H5 baseline
  - Every Realistic-mean window within +-25% of 30 DTE
  - Every DD-C <= 80%
  - 0% collapse on every cell
"""
from __future__ import annotations
import os, sys, json, math, re
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo_15dte as mc15
from experiments.v15dte_optimization.phase_15a_joint_sweep import (
    apply_15a_config, BASE_CONFIG
)
from database.models.core import AlgorithmVersion

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

N_ITER_VAL = int(os.environ.get('VALIDATE_N', '200'))
TOP_K = int(os.environ.get('VALIDATE_K', '3'))


def load_top_k(jsonl_path: str, k: int):
    with open(jsonl_path) as f:
        evals = [json.loads(l) for l in f if json.loads(l).get('event') == 'eval']
    evals.sort(key=lambda e: e['utility'], reverse=True)
    return evals[:k]


def parse_30dte_baseline():
    """Pull 30 DTE results from mc_30dte_n200.out for side-by-side comparison."""
    out_path = os.path.join(ROOT, 'experiments', 'mc_30dte_n200.out')
    if not os.path.exists(out_path):
        return {}
    out = {}
    cur = None
    with open(out_path) as f:
        for line in f:
            m = re.match(r'WINDOW:\s+(\S+)\s+\(', line)
            if m: cur = m.group(1); out[cur] = {}; continue
            m = re.match(r'^(conservative|realistic|optimistic)\s+'
                         r'([+-]?\d+\.\d+)%\s+([+-]?\d+\.\d+)%\s+'
                         r'([\d\.]+)\s+([\d\.]+)\s+'
                         r'([+-]?\d+\.\d+)%\s+([+-]?\d+\.\d+)%\s+'
                         r'([\d\.]+)%\s+([\d\.]+)%\s+([\d\.]+)%', line)
            if m and cur:
                mode = m.group(1)
                out[cur][mode] = {
                    'ret': float(m.group(6)), 'dd': float(m.group(8)),
                    'p_coll': float(m.group(10)),
                }
    return out


def run_one_config(cfg: dict, version, n_iter: int):
    """Run one config across all 8 windows. Returns {window: {mode: {ret,dd,p_coll}}}."""
    cfg2 = dict(cfg)
    cfg2['N_ITER'] = n_iter
    cfg2['COLLISION_MODES'] = ['conservative', 'realistic', 'optimistic']
    apply_15a_config(cfg2)
    out = {}
    for label, d_start, d_end in WINDOWS:
        wr = mc15.run_window(label, d_start, d_end, version)
        out[label] = {}
        for mode in ['conservative', 'realistic', 'optimistic']:
            r = wr.get(mode, {})
            out[label][mode] = {
                'ret':    float(r.get('mean_ret', 0)),
                'dd':     float(r.get('worst_dd', 0)),
                'p_coll': float(r.get('p_coll', 0)),
                'ctp':    float(r.get('call_tp', 0)),
                'ptp':    float(r.get('put_tp', 0)),
            }
    return out


def fmt_pct(x):
    if x is None: return '—'
    if abs(x) >= 1e9: return f"{x/1e9:.2f}B%"
    if abs(x) >= 1e6: return f"{x/1e6:.2f}M%"
    if abs(x) >= 1e3: return f"{x/1e3:.2f}k%"
    return f"{x:+.1f}%"


def main():
    jsonl = os.path.join(os.path.dirname(__file__), '..', '..', 'experiments', 'bayes_logs',
                         'phase_v15dte_phase_15a.jsonl')
    print(f'Reading {jsonl}', flush=True)
    top = load_top_k(jsonl, TOP_K)
    print(f'Top {len(top)} candidates by sweep utility:')
    for i, e in enumerate(top, 1):
        p = e['params']
        print(f'  #{i}  util={e["utility"]:+.2f}  '
              f'TP={p["TP_BASE"]:.2f}/SL={p["SL_BASE"]:.2f} '
              f'PUT={p["PUT_TP"]:.2f}/{p["PUT_SL"]:.2f} '
              f'u={p["TIER_ALLOC.ultra"]:.2f} pt={p["PUT_TIER_ALLOC.put_top"]:.2f} '
              f'mp={p["MAX_POSITIONS"]}', flush=True)

    baseline_30 = parse_30dte_baseline()
    print(f'\n30 DTE baseline parsed: {len(baseline_30)} windows', flush=True)

    version = AlgorithmVersion.get_active_scores_version()
    print(f'\nValidating at N={N_ITER_VAL} on all 8 windows × 3 modes ...\n', flush=True)

    all_results = []
    for idx, e in enumerate(top, 1):
        cfg = dict(BASE_CONFIG)
        cfg.update(e['params'])  # PhaseOptimizer-style: dotted keys merge into cfg
        # Convert dotted to nested for apply_15a_config (it handles both)
        for k, v in list(e['params'].items()):
            if '.' in k:
                outer, inner = k.split('.', 1)
                cfg[outer] = dict(cfg.get(outer, {}))
                cfg[outer][inner] = v
        print(f'\n=== Candidate #{idx} ({e["params"]}) ===', flush=True)
        wr = run_one_config(cfg, version, N_ITER_VAL)
        all_results.append({'rank': idx, 'params': e['params'], 'sweep_util': e['utility'], 'windows': wr})
        # Quick window summary
        for label, _, _ in WINDOWS:
            r = wr[label]['realistic']
            c = wr[label]['conservative']
            floor_ok = '✓' if c['dd'] <= 80.0 else '✗'
            print(f'  {label:<8}  Real={fmt_pct(r["ret"]):>14}  '
                  f'DD-C={c["dd"]:>5.1f}%{floor_ok}  '
                  f'CTP={r["ctp"]:.1f}% PTP={r["ptp"]:.1f}%', flush=True)

    # Cross-comparison summary
    print('\n' + '=' * 130)
    print(f'  PHASE 15A VALIDATION SUMMARY (N={N_ITER_VAL})')
    print('=' * 130)
    print(f"  {'Window':<10}  {'30DTE Real':>16}  " +
          ''.join(f"{f'C{i+1} Real':>16}  {f'C{i+1} DD-C':>9}  " for i in range(len(top))))
    print('  ' + '-' * 130)
    for label, _, _ in WINDOWS:
        b = baseline_30.get(label, {}).get('realistic', {})
        row = f"  {label:<10}  {fmt_pct(b.get('ret', 0)):>16}  "
        for r in all_results:
            real = r['windows'][label]['realistic']
            cons = r['windows'][label]['conservative']
            floor = '✓' if cons['dd'] <= 80.0 else '✗'
            row += f"{fmt_pct(real['ret']):>16}  {cons['dd']:>7.1f}%{floor}  "
        print(row)

    # Save JSON
    out_path = os.path.join(os.path.dirname(__file__), 'phase_15a_validation_results.json')
    with open(out_path, 'w') as f:
        json.dump([{'rank': r['rank'], 'params': r['params'],
                    'sweep_util': r['sweep_util'],
                    'windows': r['windows']} for r in all_results], f, indent=2, default=str)
    print(f'\nResults saved to {out_path}')


if __name__ == '__main__':
    main()
