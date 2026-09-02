"""Print a compact status report of all Bayesian phases.

Usage:  python experiments/bayes_status.py
"""

import json, os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')

PHASES = [
    (1, 'phase_1_put_tpsl.jsonl',    'Put TP/SL'),
    (2, 'phase_2_put_breadth.jsonl', 'Put breadth-adaptive TP/SL'),
    (3, 'phase_3_put_cascade.jsonl', 'Put cascade allocation'),
    (4, 'phase_4_maxpos.jsonl',      'MaxPos structure'),
]


def _load_events(path):
    if not os.path.exists(path): return []
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def _summarize_phase(num, logfile, name):
    path = os.path.join(LOG_DIR, logfile)
    events = _load_events(path)
    if not events:
        print(f'\nPHASE {num}: {name}')
        print(f'  status: not started')
        return

    start = [e for e in events if e.get('event') == 'start']
    evals = [e for e in events if e.get('event') == 'eval']
    ends  = [e for e in events if e.get('event') == 'end']
    conv  = [e for e in events if e.get('event') == 'converged']

    status = 'COMPLETE' if ends else ('RUNNING' if evals else 'STARTED')
    if conv: status += ' (converged)'
    best = max(evals, key=lambda e: e['utility']) if evals else None

    print(f'\nPHASE {num}: {name}  [{status}]')
    if start:
        t0 = start[0].get('_ts', 0)
        tn = events[-1].get('_ts', t0)
        dur_min = (tn - t0) / 60
        print(f'  started: {datetime.fromtimestamp(t0):%H:%M:%S}  elapsed: {dur_min:.1f} min')
    print(f'  evaluated: {len(evals)} configs')
    if best:
        p = best.get('params', {})
        print(f'  best utility: {best["utility"]:+.3f}  maxDD: {best.get("max_dd", 0)*100:.1f}%  params: {p}')

    if len(evals) >= 1:
        print(f'  top 5 configs:')
        top = sorted(evals, key=lambda e: e['utility'], reverse=True)[:5]
        for i, e in enumerate(top):
            p = e.get('params', {})
            print(f'    {i+1}. util={e["utility"]:+7.3f}  ret_log={e.get("log_return",0):+5.2f}  '
                  f'maxDD={e.get("max_dd",0)*100:4.1f}%  {p}')


def _summarize_phase5():
    out = os.path.join(LOG_DIR, 'phase_5_validation.json')
    print(f'\nPHASE 5: Final validation  [{"COMPLETE" if os.path.exists(out) else "not started"}]')
    if os.path.exists(out):
        print(f'  file: {out}')


def main():
    print('=' * 72)
    print(f'Bayesian MC Optimization Status  —  {datetime.now():%Y-%m-%d %H:%M:%S}')
    print('=' * 72)

    for num, logfile, name in PHASES:
        _summarize_phase(num, logfile, name)
    _summarize_phase5()


if __name__ == '__main__':
    main()
