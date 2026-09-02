"""Chain driver: Phase B -> Phase C -> Phase D.

Use when Phase B is already running OR has just completed and you want
the rest of the calibration pipeline executed without manual intervention.

Skips a phase if its results.jsonl exists and is non-empty.

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/saw_put_ucurve_15dte/run_chain.py
"""
from __future__ import annotations
import os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(ROOT, 'experiments', 'saw_put_ucurve_15dte')


def _exists_nonempty(p):
    return os.path.exists(p) and os.path.getsize(p) > 0


def run_phase(phase: str, env_overrides: dict, log_name: str):
    script = os.path.join(DIR, f'sweep_phase_{phase}.py')
    log    = os.path.join(DIR, log_name)
    print(f"\n{'='*60}\n[chain] Phase {phase.upper()} -> {log}\n{'='*60}")
    env = os.environ.copy()
    env.update(env_overrides)
    env['PYTHONIOENCODING'] = 'utf-8'
    t0 = time.time()
    with open(log, 'w', encoding='utf-8') as f:
        proc = subprocess.run(
            [sys.executable, '-u', script],
            stdout=f, stderr=subprocess.STDOUT, env=env, cwd=ROOT,
        )
    elapsed = time.time() - t0
    print(f"[chain] Phase {phase.upper()} exit={proc.returncode}  elapsed={elapsed/60:.1f}min")
    if proc.returncode != 0:
        print(f"[chain] Phase {phase.upper()} FAILED -- inspect {log}")
        sys.exit(1)


def main():
    # Phase B
    pb_results = os.path.join(DIR, 'phase_b_results.jsonl')
    if _exists_nonempty(pb_results):
        # Check if Phase B completed (look for "done]" in log)
        log_path = os.path.join(DIR, 'phase_b.log')
        done = False
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                done = '[done]' in f.read()
        if done:
            print(f"[chain] Phase B already complete (results: {pb_results}). Skipping.")
        else:
            print(f"[chain] Phase B in progress -- waiting for done marker...")
            # Poll until [done] appears or 4 hours elapse
            t0 = time.time()
            while time.time() - t0 < 4*3600:
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    if '[done]' in f.read():
                        print(f"[chain] Phase B done after {(time.time()-t0)/60:.1f}min wait.")
                        break
                time.sleep(60)
            else:
                print(f"[chain] Phase B did not complete in 4hr -- aborting chain.")
                sys.exit(1)
    else:
        run_phase('b', {'BUDGET': '40', 'N_ITER': '200'}, 'phase_b.log')

    # Phase C
    pc_results = os.path.join(DIR, 'phase_c_results.jsonl')
    if _exists_nonempty(pc_results):
        print(f"[chain] Phase C already complete (results: {pc_results}). Skipping.")
    else:
        run_phase('c', {'TOPK': '5', 'N_ITER': '300'}, 'phase_c.log')

    # Phase D
    pd_results = os.path.join(DIR, 'phase_d_results.jsonl')
    if _exists_nonempty(pd_results):
        print(f"[chain] Phase D already complete (results: {pd_results}). Skipping.")
    else:
        run_phase('d', {'N_ITER': '500'}, 'phase_d.log')

    print(f"\n{'='*60}\n[chain] ALL PHASES COMPLETE\n{'='*60}")
    print(f"  Phase B: {DIR}/phase_b.log + phase_b_results.jsonl")
    print(f"  Phase C: {DIR}/phase_c.log + phase_c_results.jsonl")
    print(f"  Phase D: {DIR}/phase_d.log + phase_d_results.jsonl")


if __name__ == '__main__':
    main()
