"""Honest v69 baseline: CURRENT strategy_config params, N x 8 windows.

This is the honest baseline the whole retune compares against. With the current
(inflated-tuned) params it is expected to be far worse than the old inflated MC;
the 5y window in particular may collapse.
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


def main():
    n_iter = int(os.environ.get('N_ITER', '150'))
    out_path = os.path.join(os.path.dirname(__file__), f'baseline_n{n_iter}.json')
    print(f'[baseline] CURRENT params, N={n_iter}, windows={ALL}', flush=True)
    t0 = time.time()
    # Empty params dict => engine uses strategy_config defaults for everything.
    res = run_candidate({}, n_iter=n_iter, windows=ALL, tag='baseline')
    dur = time.time() - t0
    print(f'[baseline] done in {dur:.0f}s', flush=True)
    hdr = f"{'window':<8} {'MedRet%':>16} {'MeanRet%':>16} {'WorstDD':>9} {'MeanDD':>8} {'P(col)':>7} {'CTP':>6} {'PTP':>6} {'CTrd':>7} {'PTrd':>7}"
    print(hdr, flush=True)
    print('-' * len(hdr), flush=True)
    for w in ALL:
        r = res[w]
        print(f"{w:<8} {r['med_ret']:>+16,.1f} {r['mean_ret']:>+16,.1f} "
              f"{r['worst_dd']:>8.1f}% {r['mean_dd']:>7.1f}% {r['p_collapse']:>6.1f}% "
              f"{r.get('call_tp',0):>5.1f}% {r.get('put_tp',0):>5.1f}% "
              f"{r.get('call_trades',0):>7.1f} {r.get('put_trades',0):>7.1f}", flush=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'n_iter': n_iter, 'windows': res, 'duration_s': dur}, f, indent=2)
    print(f'[baseline] wrote {out_path}', flush=True)


if __name__ == '__main__':
    main()
