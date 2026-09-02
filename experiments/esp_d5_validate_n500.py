"""N=500 validation: ESP_16_20_d5 vs B baseline on full window set.

ESP_16_20_d5 shipping candidate? At N=200 showed:
  5y compound +44.6%
  22-now      +38.7%
  All windows under -25% gate
  All DD-C under 80% floor (max 75.0% on 22-now)
  0% collapse on every cell

But N=200 noise floor on 5y is ~±20% (proven by no-op EBC_84_top variant
showing +19.0% spurious lift). So +44.6% may collapse to +25% or +60%
depending on path. N=500 reduces variance ~40% — needed for ship decision.
"""
from __future__ import annotations
import io, sys, os, subprocess
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('B_n500',           {}),
    ('ESP_16_20_d5_n500',{'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '5', 'EARN_SUPP_PUT_MIN_OV': '16', 'EARN_SUPP_PUT_MAX_OV': '20'}),
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
    print('\n*** Validation complete.')


if __name__ == '__main__':
    main()
