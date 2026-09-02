"""Run `trader recalculate --force --full` end-to-end with phase timing.

Streams subprocess output, timestamps each line, detects phase headers
(── name ──), and prints a duration breakdown at the end.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(os.path.dirname(__file__), 'full_recalc_run.log')

cmd = [sys.executable, 'trader.py', 'recalculate', '--force', '--full']
print(f"Running: {' '.join(cmd)}", flush=True)
print(f"Log:      {LOG}", flush=True)

t0 = time.time()
phases = [('recalc', 0.0)]  # phase name → start_elapsed

env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'

proc = subprocess.Popen(
    cmd, cwd=ROOT,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    encoding='utf-8', errors='replace', bufsize=1, env=env,
)

with open(LOG, 'w', encoding='utf-8') as f:
    for line in proc.stdout:
        elapsed = time.time() - t0
        out = f'[{elapsed:7.1f}s] {line}'
        f.write(out)
        f.flush()
        sys.stdout.write(out)
        sys.stdout.flush()
        # Detect phase headers like "── historic-update ──" or "── assess 1y ──"
        s = line.strip()
        # Strip ANSI escape codes
        if '\x1b[' in s:
            import re
            s = re.sub(r'\x1b\[[0-9;]*m', '', s)
        if s.startswith('── ') and s.endswith(' ──'):
            name = s[3:-3].strip()
            phases.append((name, elapsed))

rc = proc.wait()
total = time.time() - t0

# Compute phase durations
print(f'\n{"="*60}', flush=True)
print(f'Total wall: {total:.1f}s ({total/60:.2f} min), exit={rc}', flush=True)
print(f'{"="*60}', flush=True)
print(f'{"Phase":30s}  {"Start":>9s}  {"Duration":>10s}', flush=True)
for i, (name, start) in enumerate(phases):
    end = phases[i+1][1] if i+1 < len(phases) else total
    dur = end - start
    print(f'  {name:28s}  {start:8.1f}s  {dur:9.1f}s  ({dur/60:.2f} min)', flush=True)
