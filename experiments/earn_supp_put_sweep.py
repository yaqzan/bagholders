"""
Earnings-window put suppression sweep.

Per-trade A/B (experiments/earn_supp_put_pretest.py, 5y):
  bucket  EarningsWindow WR15/WR30 (N)   Other WR15/WR30 (N)   ΔWR15  ΔWR30
  16-20         64.9/64.9 (N=735)         68.0/68.9 (N=15885)   -3.1   -4.0   <- suppress
  21-25         65.8/67.2 (N=1761)        66.2/66.3 (N=36820)   -0.4   +0.9   <- neutral
  6-15          71-75% / 73-80%           71-73 / 72-74         +0..+6 +2..+6 <- DO NOT suppress
  <=5           67.7/71.0 (N=31)          71.8/72.1 (N=841)     -4.1   -1.2   <- small N

Variants:
  B          : production v25
  ESP_16_20  : suppress puts overall in [16, 20] when earnings in (D, D+3 trd days]
  ESP_16_25  : suppress puts overall in [16, 25] (broader, includes neutral 21-25)
  ESP_18_20  : tighter — only [18, 20]
  ESP_16_20_d2 : same as ESP_16_20 but with 2 trading days lookahead
  ESP_16_20_d5 : same as ESP_16_20 but with 5 trading days lookahead
"""
from __future__ import annotations
import io, sys, os, subprocess, json
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('B',           {}),
    ('ESP_16_20',   {'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '3', 'EARN_SUPP_PUT_MIN_OV': '16', 'EARN_SUPP_PUT_MAX_OV': '20'}),
    ('ESP_16_25',   {'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '3', 'EARN_SUPP_PUT_MIN_OV': '16', 'EARN_SUPP_PUT_MAX_OV': '25'}),
    ('ESP_18_20',   {'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '3', 'EARN_SUPP_PUT_MIN_OV': '18', 'EARN_SUPP_PUT_MAX_OV': '20'}),
    ('ESP_16_20_d2', {'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '2', 'EARN_SUPP_PUT_MIN_OV': '16', 'EARN_SUPP_PUT_MAX_OV': '20'}),
    ('ESP_16_20_d5', {'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '5', 'EARN_SUPP_PUT_MIN_OV': '16', 'EARN_SUPP_PUT_MAX_OV': '20'}),
]

N_ITER = int(os.environ.get('SWEEP_N_ITER', '200'))
WINDOWS = os.environ.get('SWEEP_WINDOWS', '2022,2024,2025,dip,22-now,5y')


def run_variant(label, env_extra):
    env = os.environ.copy()
    env.update(env_extra)
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = WINDOWS
    env['PYTHONIOENCODING'] = 'utf-8'
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> running {label}: env={env_extra} N={N_ITER} windows={WINDOWS}")
    sys.stdout.flush()
    proc = subprocess.run(['python', 'monte_carlo.py'],
                          cwd=str(ROOT), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  FAILED rc={proc.returncode}")
        print(proc.stderr[-3000:])
        return False
    out_path = ROOT / 'experiments' / f'earnsweep_{label}.out'
    out_path.write_text(proc.stdout, encoding='utf-8', errors='replace')
    print(f"  -> wrote {out_path.name}")
    return True


def main():
    for label, env in VARIANTS:
        run_variant(label, env)
    print('\n*** Sweep complete. Compare via:')
    print(f'    for v in B ESP_16_20 ESP_16_25 ESP_18_20 ESP_16_20_d2 ESP_16_20_d5; do echo "=== $v ==="; awk \'/SUMMARY - Mean Return/,/SUMMARY - Worst/\' experiments/earnsweep_$v.out | grep -E "^(2022|2024|2025|dip|22-now|5y)\\s"; done')


if __name__ == '__main__':
    main()
