"""One-cell smoke: prove MC_UNIVERSE_FILE engages end-to-end before arms B/C.

Runs a single cheap cell (core, window 2024, N=20) with the 811 allow-list and
asserts the '[universe-filter]' lines appear in the MC subprocess log with a
kept-count strictly below the total. Exit 0 = safe to launch the real arms.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments._mc_pinned_runner import resolve_pinned_version, run_one_window  # noqa: E402
from experiments.deep_crash_screen.run_screen import CORE_ENV  # noqa: E402

UNIVERSE_FILE = os.path.join(HERE, 'survivor_universe_811.txt')
OUT = os.path.join(HERE, 'results', 'smoke', '2024.json')


def main():
    if os.path.exists(OUT):
        os.remove(OUT)   # smoke must actually run, never resume
    pin, pin_id = resolve_pinned_version()
    env = dict(CORE_ENV)
    env['MC_UNIVERSE_FILE'] = UNIVERSE_FILE
    res = run_one_window(env, '2024', 20, OUT, pin, deep=True, timeout_s=1800, cpu=8)
    log_path = OUT[:-5] + '.log'
    log = ''
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            log = f.read()
    lines = [ln for ln in log.splitlines() if 'universe-filter' in ln]
    print('--- universe-filter lines from MC log ---', flush=True)
    print('\n'.join(lines) if lines else '(NONE)', flush=True)
    ok = res is not None and lines
    m = re.search(r"call: kept ([\d,]+)/([\d,]+)", log)
    if m:
        kept, tot = (int(x.replace(',', '')) for x in m.groups())
        print(f"call signals kept {kept}/{tot}", flush=True)
        ok = ok and kept < tot
    else:
        print('no call kept/total line found', flush=True)
        ok = False
    print('SMOKE ' + ('PASS' if ok else 'FAIL'), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
