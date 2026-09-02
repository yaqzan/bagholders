"""Regime-gated PUT_THRESHOLD tightening sweep.

Mechanism: when breadth_score on signal date <= threshold, drop puts with
overall in (PUT_TIGHTEN_THRESH, PUT_THRESHOLD]. Selectively tightens to
<=PUT_TIGHTEN_THRESH only during stressed-breadth tape.

Hypothesis: combines the live "max put 20 in stress" observation with the
put_concentration_sweep finding that D+PT20 wins +119% on 5-month dip but
loses 5y compound. A breadth-gated tightening should capture the dip alpha
without the long-window cost.

Variants (all with PUT_TIGHTEN_THRESH=20):
  B            baseline (no tightening)
  G30          tighten when breadth <= 30  (deepest stress only)
  G40          tighten when breadth <= 40  (moderate stress)
  G50          tighten when breadth <= 50  (mild stress; matches existing breadth-adaptive TP/SL trigger)

N=200 first pass for direction. Per project gate: N>=500 needed for ship.
"""
from __future__ import annotations
import io, sys, os, subprocess
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('PT_B_n200',    {}),
    ('PT_G30_n200',  {'PUT_TIGHTEN_BREADTH_LE': '30', 'PUT_TIGHTEN_THRESH': '20'}),
    ('PT_G40_n200',  {'PUT_TIGHTEN_BREADTH_LE': '40', 'PUT_TIGHTEN_THRESH': '20'}),
    ('PT_G50_n200',  {'PUT_TIGHTEN_BREADTH_LE': '50', 'PUT_TIGHTEN_THRESH': '20'}),
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
    print('\n*** PUT_TIGHTEN N=200 sweep complete.')


if __name__ == '__main__':
    main()
