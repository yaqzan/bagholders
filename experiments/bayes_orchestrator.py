"""Orchestrate all 5 Bayesian phases sequentially.

Each phase runs to convergence, writes its JSONL log, and the next phase reads
the winner from the log. If a phase's log already exists with an 'end' event we
skip re-running it — this lets you resume after interruption without losing
progress.

Usage:
  python experiments/bayes_orchestrator.py           # run missing phases only
  python experiments/bayes_orchestrator.py --force   # rerun all phases (delete logs first)
  python experiments/bayes_orchestrator.py --from 2  # start at phase 2
"""

import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')
PY = sys.executable

PHASES = [
    (1, 'bayes_phase1_put_tpsl.py',    'phase_1_put_tpsl.jsonl'),
    (2, 'bayes_phase2_put_breadth.py', 'phase_2_put_breadth.jsonl'),
    (3, 'bayes_phase3_put_cascade.py', 'phase_3_put_cascade.jsonl'),
    (4, 'bayes_phase4_maxpos.py',      'phase_4_maxpos.jsonl'),
    (5, 'bayes_phase5_validate.py',    None),  # special: produces phase_5_validation.json
]


def _phase_complete(log_file):
    if log_file is None:  # phase 5
        return os.path.exists(os.path.join(LOG_DIR, 'phase_5_validation.json'))
    path = os.path.join(LOG_DIR, log_file)
    if not os.path.exists(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                rec = json.loads(line)
                if rec.get('event') == 'end':
                    return True
    except Exception:
        return False
    return False


def _run_phase(phase_num, script, force=False):
    print(f'\n{"#"*100}\n# PHASE {phase_num} — {script}\n{"#"*100}', flush=True)
    cmd = [PY, os.path.join('experiments', script)]
    out_path = os.path.join('experiments', f'bayes_phase{phase_num}.out')
    t0 = time.time()
    with open(out_path, 'w', encoding='utf-8') as out:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
    dur = time.time() - t0
    ok = proc.returncode == 0
    print(f'[phase{phase_num}] exit={proc.returncode}  duration={dur/60:.1f}min  output={out_path}', flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='Rerun all phases (deletes existing logs)')
    ap.add_argument('--from', dest='start', type=int, default=1, help='Start at this phase number')
    ap.add_argument('--only', type=int, default=None, help='Run only this single phase')
    args = ap.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)

    if args.force:
        for _, _, logf in PHASES:
            if logf:
                p = os.path.join(LOG_DIR, logf)
                if os.path.exists(p): os.remove(p)
        v5 = os.path.join(LOG_DIR, 'phase_5_validation.json')
        if os.path.exists(v5): os.remove(v5)
        print('[force] cleared existing phase logs.')

    for num, script, logf in PHASES:
        if args.only is not None and num != args.only: continue
        if num < args.start: continue
        if _phase_complete(logf) and not args.force:
            print(f'[skip] phase {num} already complete ({logf})')
            continue
        ok = _run_phase(num, script)
        if not ok:
            print(f'[abort] phase {num} failed — stopping.')
            sys.exit(1)

    print('\n[orchestrator] all requested phases complete.')


if __name__ == '__main__':
    main()
