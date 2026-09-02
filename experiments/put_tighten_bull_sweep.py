"""Inverse PUT_TIGHTEN sweep — drop marginal puts during BULL breadth.

The first sweep (drop puts in (20,25] when brd <= LE) regressed across all
windows including the dip itself. Hypothesis was wrong-signed: 21-25 puts
during stress are HIGH-EV; dropping them removes prime put-firing volume.

Inverted hypothesis: 21-25 puts during BULL tape (high breadth) are
mean-reversion bounce traps with lower per-trade WR. Symmetric to the
F3f put alloc cut at high breadth (which scales 21-25 down at brd>=75)
but uses threshold drop instead of allocation scaling — frees slot
capacity entirely.

Variants:
  PT_BG65_n200    drop puts in (20,25] when breadth >= 65
  PT_BG75_n200    drop when breadth >= 75
  PT_BG85_n200    drop when breadth >= 85

Compared to the existing PT_B_n200 baseline run.
"""
from __future__ import annotations
import io, sys, os, subprocess
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('PT_BG65_n200',  {'PUT_TIGHTEN_BREADTH_GE': '65', 'PUT_TIGHTEN_THRESH': '20'}),
    ('PT_BG75_n200',  {'PUT_TIGHTEN_BREADTH_GE': '75', 'PUT_TIGHTEN_THRESH': '20'}),
    ('PT_BG85_n200',  {'PUT_TIGHTEN_BREADTH_GE': '85', 'PUT_TIGHTEN_THRESH': '20'}),
]

N_ITER = 200
WINDOWS = '2022,2024,2025,dip,22-now,5y'


def run_variant(label, env_extra):
    env = os.environ.copy()
    env.update(env_extra)
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = WINDOWS
    env['PYTHONIOENCODING'] = 'utf-8'
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> running {label} N={N_ITER} windows={WINDOWS}")
    sys.stdout.flush()
    proc = subprocess.run(['python', 'monte_carlo.py'],
                          cwd=str(ROOT), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  FAILED rc={proc.returncode}\n{proc.stderr[-3000:]}")
        return False
    out_path = ROOT / 'experiments' / f'{label}.out'
    out_path.write_text(proc.stdout, encoding='utf-8', errors='replace')
    print(f"  -> wrote {out_path.name}")
    return True


def main():
    for label, env in VARIANTS:
        run_variant(label, env)
    print('\n*** PUT_TIGHTEN BULL N=200 sweep complete.')


if __name__ == '__main__':
    main()
