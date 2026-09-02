"""N=500 confirmation: weak-weekly put filter.

N=200 results:
  WW_A (drop 0-10 + wadj>-13):  5y -18%, 22-now -25% → reject (narrow target loses)
  WW_B (drop 0-15 + wadj>-13):  5y +187%, 22-now +85%, dip +65%, 2024 +44%, 2025 -3.6%, 2022 -25.2%
  WW_C (=5 + REJ + wadj>-13):   5y +78%, 22-now +67%, 2025 +44%, 2022 -23.5%

WW_B is the headline candidate. WW_C is the safe knife-edge. Both have 2022
borderline at the 25% gate. N=500 needed to disambiguate (200 noise floor ~20%
on 5y compound).

Also testing:
  WW_B15 (drop 0-15 + wadj>-15) — stricter weekly threshold, drops less, may
  preserve 2022 better while keeping the 5y win.
"""
from __future__ import annotations
import io, sys, os, subprocess
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('WW_BASELINE_n500', {}),  # baseline rerun for matched-N comparison
    ('WW_B_n500', {
        'WEAK_WEEKLY_PUT_DROP': '1',
        'WEAK_WEEKLY_PUT_MIN_OV': '0',
        'WEAK_WEEKLY_PUT_MAX_OV': '15',
        'WEAK_WEEKLY_PUT_WADJ': '-13.0',
    }),
    ('WW_B15_n500', {
        'WEAK_WEEKLY_PUT_DROP': '1',
        'WEAK_WEEKLY_PUT_MIN_OV': '0',
        'WEAK_WEEKLY_PUT_MAX_OV': '15',
        'WEAK_WEEKLY_PUT_WADJ': '-15.0',  # stricter — drop only weakest weeklies
    }),
    ('WW_C_n500', {
        'WEAK_WEEKLY_PUT_DROP': '1',
        'WEAK_WEEKLY_PUT_MIN_OV': '0',
        'WEAK_WEEKLY_PUT_MAX_OV': '5',
        'WEAK_WEEKLY_PUT_WADJ': '-13.0',
        'WEAK_WEEKLY_PUT_VSIG_REJ_ONLY': '1',
    }),
]

N_ITER = 500
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
    print('\n*** WEAK_WEEKLY N=500 confirmation complete.')


if __name__ == '__main__':
    main()
