"""
Earnings-window call BOOST sweep.

Per-trade A/B (experiments/earn_call_pretest.py, 5y) — calls firing within
3 trd days BEFORE earnings have huge WR uplift:
  75-79 PRE-ern: 88.3% WR15 (N=265) vs other 72.2%  -> +16.1pp
  80-84 PRE-ern: 85.5% WR15 (N=62)  vs other 71.8%  -> +13.7pp
  85-94 PRE-ern: 80.0% WR15 (N=25)  vs other 76.6%  -> +3.4pp
  70-74 PRE-ern: 80.8% WR15 (N=992) vs other 70.8%  -> +10.0pp (overflow disabled)

Variants test promotion strength + bucket coverage:
  B           : production v25 baseline
  EBC_77_top  : promote calls in [75,79] to 'top' tier (15%, was already 15% — sanity check, no effect)
  EBC_77_ult  : promote calls in [75,79] to 'ultra' tier (25%)
  EBC_84_ult  : promote calls in [75,84] to 'ultra' tier (25%) — wider
  EBC_77_top_d5 : promote calls in [75,79] to 'top' with 5-day window
  EBC_70_low  : ENABLE 70-74 overflow signal at 'low' tier (15%) ONLY when earnings-window
"""
from __future__ import annotations
import io, sys, os, subprocess
from pathlib import Path
from datetime import datetime
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('B',           {}),
    ('EBC_77_ult',  {'EARN_BOOST_CALL': '1', 'EARN_BOOST_CALL_DAYS': '3', 'EARN_BOOST_CALL_MIN_OV': '75', 'EARN_BOOST_CALL_MAX_OV': '79', 'EARN_BOOST_CALL_TIER': 'ultra'}),
    ('EBC_84_ult',  {'EARN_BOOST_CALL': '1', 'EARN_BOOST_CALL_DAYS': '3', 'EARN_BOOST_CALL_MIN_OV': '75', 'EARN_BOOST_CALL_MAX_OV': '84', 'EARN_BOOST_CALL_TIER': 'ultra'}),
    ('EBC_77_ult_d5',{'EARN_BOOST_CALL': '1', 'EARN_BOOST_CALL_DAYS': '5', 'EARN_BOOST_CALL_MIN_OV': '75', 'EARN_BOOST_CALL_MAX_OV': '79', 'EARN_BOOST_CALL_TIER': 'ultra'}),
    ('EBC_84_top',  {'EARN_BOOST_CALL': '1', 'EARN_BOOST_CALL_DAYS': '3', 'EARN_BOOST_CALL_MIN_OV': '75', 'EARN_BOOST_CALL_MAX_OV': '84', 'EARN_BOOST_CALL_TIER': 'top'}),
    ('EBC_77_top_d2',{'EARN_BOOST_CALL': '1', 'EARN_BOOST_CALL_DAYS': '2', 'EARN_BOOST_CALL_MIN_OV': '75', 'EARN_BOOST_CALL_MAX_OV': '79', 'EARN_BOOST_CALL_TIER': 'top'}),
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
        print(f"  FAILED rc={proc.returncode}\n{proc.stderr[-3000:]}")
        return False
    out_path = ROOT / 'experiments' / f'ebcsweep_{label}.out'
    out_path.write_text(proc.stdout, encoding='utf-8', errors='replace')
    print(f"  -> wrote {out_path.name}")
    return True


def main():
    for label, env in VARIANTS:
        run_variant(label, env)
    print('\n*** Sweep complete.')


if __name__ == '__main__':
    main()
