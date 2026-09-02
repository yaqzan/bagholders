"""Smoke test: one config, one window, low iter count. Verifies end-to-end plumbing."""

import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from datetime import date
from experiments.bayes_mc import (
    DEFAULT_CONFIG, run_config, utility_from_results,
)

def main():
    cfg = dict(DEFAULT_CONFIG)
    cfg['N_ITER'] = 25

    windows = [
        ('2024', date(2024, 1, 1), date(2024, 12, 31)),
    ]

    t0 = time.time()
    res = run_config(cfg, windows=windows, silent=False)
    dur = time.time() - t0

    util = utility_from_results(res)
    print('\n---- smoke result ----')
    print(f'duration: {dur:.1f}s')
    for label, r in res.items():
        print(f'  {label}: mean_ret={r["mean_ret"]:+.1f}%  worst_dd={r["worst_dd"]:.1f}%  '
              f'call_tp={r["call_tp"]:.1f}%  put_tp={r["put_tp"]:.1f}%  '
              f'call_tr={r["call_trades"]:.0f}  put_tr={r["put_trades"]:.0f}')
    print(f'utility: {util["utility"]:+.3f}')
    print(f'log_return: {util["log_return"]:+.3f}')
    print(f'dd_penalty: {util["dd_penalty"]:+.3f}')
    print(f'max_dd: {util["max_dd"]*100:.1f}%')


if __name__ == '__main__':
    main()
