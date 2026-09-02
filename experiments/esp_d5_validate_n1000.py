"""N=1000 final validation of ESP_16_20_d5 vs B baseline.

N=500 result: 5y +36.2%, 22-now +6.5%, all gates pass. Per-trade evidence
is weak at d=5 (-0.4pp WR15 on 16-20). Bumping to N=1000 to disambiguate
whether the +36% is real signal or heavy-tail variance.

Also runs ESP_16_20 d=3 at N=1000 for symmetric comparison — at N=500 d=3
showed -12% vs +36% for d=5, a 50pp swing that suggests noise dominance.
N=1000 should narrow this band to a few pp.
"""
from __future__ import annotations
import io, sys, os, subprocess
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('B_n1000',           {}),
    ('ESP_d3_n1000',      {'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '3', 'EARN_SUPP_PUT_MIN_OV': '16', 'EARN_SUPP_PUT_MAX_OV': '20'}),
    ('ESP_d5_n1000',      {'EARN_SUPP_PUT': '1', 'EARN_SUPP_PUT_DAYS': '5', 'EARN_SUPP_PUT_MIN_OV': '16', 'EARN_SUPP_PUT_MAX_OV': '20'}),
]

N_ITER = 1000
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
    print('\n*** N=1000 validation complete.')


if __name__ == '__main__':
    main()
