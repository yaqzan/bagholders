"""All-8-window comparison of the baseline + leading survivors on honest v69.

Runs each config across all 8 canonical windows at N, prints the per-window
MedRet / WorstDD / Collapse / CTP / PTP table, and a T-gate-style readout.

Configs (from the 22-now survival screen):
  cur               : CURRENT shipped params (collapses on honest v69).
  callsonly         : cs55 call cascade, puts ~off, MaxPos 8/7/2 (best 22-now: 58% DD, +30% med).
  lowput            : cs55 call cascade, ps0.20, MaxPos 8/7/3 (74% DD, keeps small put hedge).
  mid               : cs45 call cascade, ps0.20, MaxPos 8/7/3 (72% DD).
  callsonly_mp10    : cs55 call cascade, puts off, MaxPos 10/9/2 (more call slots).

Usage:
  PYTHONIOENCODING=utf-8 N_ITER=120 python -u experiments/v69_portfolio_retune/finalists.py
"""
from __future__ import annotations
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

ALL = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']


def cc(s):
    return dict(TIER_ULTRA=round(0.20 * s, 4), TIER_TOP=round(0.15 * s, 4),
               TIER_MID=round(0.10 * s, 4), TIER_LOW=round(0.10 * s, 4))


def pc(s):
    return dict(PUT_TIER_TOP=round(0.12 * s, 4), PUT_TIER_MID=round(0.10 * s, 4),
               PUT_TIER_LOW=round(0.08 * s, 4))


CONFIGS = {
    'cur': {},
    'callsonly':      {**cc(0.55), **pc(0.01), 'MAX_POSITIONS': 8, 'MAX_POSITIONS_CALL': 7, 'MAX_POSITIONS_PUT': 2},
    'callsonly_mp10': {**cc(0.55), **pc(0.01), 'MAX_POSITIONS': 10, 'MAX_POSITIONS_CALL': 9, 'MAX_POSITIONS_PUT': 2},
    'lowput':         {**cc(0.55), **pc(0.20), 'MAX_POSITIONS': 8, 'MAX_POSITIONS_CALL': 7, 'MAX_POSITIONS_PUT': 3},
    'mid':            {**cc(0.45), **pc(0.20), 'MAX_POSITIONS': 8, 'MAX_POSITIONS_CALL': 7, 'MAX_POSITIONS_PUT': 3},
}


def main():
    n_iter = int(os.environ.get('N_ITER', '120'))
    only = os.environ.get('ONLY', '')
    configs = CONFIGS if not only else {k: CONFIGS[k] for k in only.split(',')}
    out_path = os.path.join(os.path.dirname(__file__), f'finalists_n{n_iter}.json')
    results = {}
    for name, params in configs.items():
        t0 = time.time()
        print(f'\n### {name}  N={n_iter}  params={params}', flush=True)
        try:
            res = run_candidate(params, n_iter=n_iter, windows=ALL, tag=f'fin_{name}')
        except Exception as e:
            print(f'  [FAIL] {name}: {e}', flush=True)
            continue
        results[name] = {'params': params, 'windows': res, 'duration_s': time.time() - t0}
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, default=str)
        hdr = f"{'win':<8}{'MedRet%':>16}{'WorstDD':>9}{'Coll':>6}{'CTP':>6}{'PTP':>6}{'CTrd':>7}{'PTrd':>7}"
        print(hdr, flush=True)
        print('-' * len(hdr), flush=True)
        for w in ALL:
            r = res[w]
            print(f"{w:<8}{r['med_ret']:>+16,.1f}{r['worst_dd']:>8.1f}%{r['p_collapse']:>5.0f}%"
                  f"{r.get('call_tp',0):>5.1f}%{r.get('put_tp',0):>5.1f}%"
                  f"{r.get('call_trades',0):>7.1f}{r.get('put_trades',0):>7.1f}", flush=True)
        mc = max(res[w]['p_collapse'] for w in ALL)
        md = max(res[w]['worst_dd'] for w in ALL)
        print(f"  ==> maxDD={md:.1f}%  maxColl={mc:.0f}%  "
              f"{'SURVIVES (DD<80, 0-coll)' if md <= 80 and mc == 0 else 'BREACHES'}", flush=True)

    # cross-config DD summary
    print('\n' + '=' * 90, flush=True)
    print('CROSS-CONFIG WorstDD / Collapse by window', flush=True)
    print(f"{'config':<16}" + ''.join(f"{w:>11}" for w in ALL), flush=True)
    for name in configs:
        if name not in results:
            continue
        res = results[name]['windows']
        print(f"{name:<16}" + ''.join(f"{res[w]['worst_dd']:>7.0f}/{res[w]['p_collapse']:>2.0f}c" for w in ALL), flush=True)
    print(f'\nwrote {out_path}', flush=True)


if __name__ == '__main__':
    main()
