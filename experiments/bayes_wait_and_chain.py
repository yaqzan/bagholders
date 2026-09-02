"""Wait for Phase 1 to complete, then run phases 2-5 via the orchestrator.

Polls the phase 1 log every 30s for an 'end' event. Once found, invokes
bayes_orchestrator.py with --from 2 (which will skip completed phases and run
phases 2 through 5 sequentially).
"""

import json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, 'experiments', 'bayes_logs')
PHASE1_LOG = os.path.join(LOG_DIR, 'phase_1_put_tpsl.jsonl')


def is_phase1_done():
    if not os.path.exists(PHASE1_LOG):
        return False
    try:
        with open(PHASE1_LOG, 'r', encoding='utf-8') as f:
            for line in f:
                rec = json.loads(line)
                if rec.get('event') == 'end':
                    return True
    except Exception:
        return False
    return False


def main():
    print(f'[chain] waiting for phase 1 to complete...  log={PHASE1_LOG}', flush=True)
    poll_interval = 30
    while not is_phase1_done():
        time.sleep(poll_interval)
    print(f'[chain] phase 1 complete — starting orchestrator for phases 2-5', flush=True)

    cmd = [sys.executable, os.path.join('experiments', 'bayes_orchestrator.py'), '--from', '2']
    out_path = os.path.join('experiments', 'bayes_chain.out')
    with open(out_path, 'w', encoding='utf-8') as out:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
    print(f'[chain] orchestrator exited with code {proc.returncode} — see {out_path}', flush=True)


if __name__ == '__main__':
    main()
