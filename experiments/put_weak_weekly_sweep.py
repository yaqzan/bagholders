"""Canonical 3-mode MC sweep: weak-weekly put filter.

Per-trade evidence (put_wadj_cross_buckets.py): puts with weight_info.w_adj
> -13 carry WR15 -8 to -15pp below strong-weekly siblings across every bucket.
The =5 dip (66.9% WR vs neighbors 73-75%) is half-composed of weak-weekly
REJECTION puts at 52% WR (N=75 / 5y).

Variants:
  WW_A_n200   drop overall in [0,10] AND wadj > -13         (~230/yr volume)
  WW_B_n200   drop overall in [0,15] AND wadj > -13         (~860/yr volume)
  WW_C_n200   drop overall in [0,5]  AND wadj > -13 AND vsig=REJECTION (~50/yr)

Baseline reuses PT_B_n200 from earlier sweep (identical N=200 and windows).
"""
from __future__ import annotations
import io, sys, os, subprocess
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('WW_A_n200', {
        'WEAK_WEEKLY_PUT_DROP': '1',
        'WEAK_WEEKLY_PUT_MIN_OV': '0',
        'WEAK_WEEKLY_PUT_MAX_OV': '10',
        'WEAK_WEEKLY_PUT_WADJ': '-13.0',
    }),
    ('WW_B_n200', {
        'WEAK_WEEKLY_PUT_DROP': '1',
        'WEAK_WEEKLY_PUT_MIN_OV': '0',
        'WEAK_WEEKLY_PUT_MAX_OV': '15',
        'WEAK_WEEKLY_PUT_WADJ': '-13.0',
    }),
    ('WW_C_n200', {
        'WEAK_WEEKLY_PUT_DROP': '1',
        'WEAK_WEEKLY_PUT_MIN_OV': '0',
        'WEAK_WEEKLY_PUT_MAX_OV': '5',
        'WEAK_WEEKLY_PUT_WADJ': '-13.0',
        'WEAK_WEEKLY_PUT_VSIG_REJ_ONLY': '1',
    }),
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
    print('\n*** WEAK_WEEKLY N=200 sweep complete.')


if __name__ == '__main__':
    main()
