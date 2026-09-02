"""Phase EVS-IV — N=500 × 8-window canonical validation for top stochastic-mode cells.

Runs ONLY if v29_iv_crush_sweep_stoch_n200 showed:
  - B2 (v29 + stochastic IV) >= A2 (v28 + stochastic IV) on 22-now Realistic
  - DD-C <= 80% on 22-now (Conservative mode)

Validation cells (3):
  V0  v28 + stochastic IV  (production-equivalent under realistic IV pricing)
  V1  v29 + stochastic IV  (KEY: does V6 win at N=500 across 8 windows?)
  V2  v28 + IV OFF         (legacy reference: production reading)

Usage:
    PYTHONIOENCODING=utf-8 python -u experiments/v29_iv_crush_validate_n500.py
"""
from __future__ import annotations
import json
import os
import sys
import time
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CELLS = [
    ('V2', 'e3c8678', '0', {},                                                            'v28 + IV OFF (legacy reference)'),
    ('V0', 'e3c8678', '1', {'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42'},       'v28 + IV stochastic (true baseline)'),
    ('V1', '8473cba', '1', {'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42'},       'v29 (V6) + IV stochastic (KEY)'),
]

WINDOWS_VALIDATE = '2021,2022,2023,2024,2025,dip,22-now,5y'
N_VALIDATE = 500


def run_cell(cell_id, version, iv_on, env_overrides, note):
    print(f"\n{'='*100}")
    print(f"CELL {cell_id}: {note}")
    print(f"  version={version}  iv_on={iv_on}  windows={WINDOWS_VALIDATE}  N={N_VALIDATE}")
    for k, v in env_overrides.items():
        print(f"  env {k}={v}")
    print('='*100, flush=True)

    env = dict(os.environ)
    env['MC_NO_MP'] = '0'
    env['N_ITER_OVERRIDE'] = str(N_VALIDATE)
    env['WINDOWS_OVERRIDE'] = WINDOWS_VALIDATE
    env['IV_CRUSH_ENABLED'] = iv_on
    env['ALGORITHM_VERSION_PIN'] = version
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    for k, v in env_overrides.items():
        env[k] = v

    t0 = time.time()
    log_path = os.path.join(os.path.dirname(__file__), f'v29_iv_crush_validate_{cell_id}.out')
    with open(log_path, 'w') as f:
        proc = subprocess.run(
            [sys.executable, '-u', 'monte_carlo.py'],
            env=env, stdout=f, stderr=subprocess.STDOUT, text=True, cwd=ROOT,
        )
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print(f"  FAIL  rc={proc.returncode}  elapsed={elapsed:.0f}s")
        return None
    print(f"  OK   elapsed={elapsed:.0f}s  log={log_path}")

    # Parse from the log file
    return parse_log(log_path)


def parse_log(log_path):
    """Extract per-window per-mode (mean_ret, worst_dd, p_coll) from the MC log."""
    results = {}  # window -> mode -> dict
    current_window = None
    in_block = False
    with open(log_path) as f:
        for line in f:
            s = line.strip()
            if s.startswith('WINDOW: '):
                # WINDOW: 2024  (2024-01-01 -> 2024-12-31)
                current_window = s.split()[1]
                results[current_window] = {}
                in_block = False
                continue
            if s.startswith('Mode') and 'MeanRet' in s:
                in_block = True
                continue
            if in_block and s.startswith('-'):
                continue
            if in_block:
                if s == '' or s.startswith('=') or s.startswith('SUMMARY'):
                    in_block = False
                    continue
                parts = s.split()
                if len(parts) < 10:
                    continue
                mode = parts[0]
                try:
                    mean_r  = float(parts[5].replace('%','').replace(',','').lstrip('+'))
                    worst_dd= float(parts[7].rstrip('%'))
                    p_coll  = float(parts[9].rstrip('%'))
                    if current_window:
                        results[current_window][mode] = dict(
                            mean_ret=mean_r, worst_dd=worst_dd, p_coll=p_coll
                        )
                except (ValueError, IndexError):
                    continue
    return results


def main():
    cell_results = {}
    for cell_id, version, iv_on, env_overrides, note in CELLS:
        r = run_cell(cell_id, version, iv_on, env_overrides, note)
        if r is None:
            continue
        cell_results[cell_id] = dict(r=r, note=note, version=version, iv_on=iv_on,
                                       env_overrides=env_overrides)

    # Summary table
    print('\n' + '='*120)
    print(f"SUMMARY — N={N_VALIDATE}, Realistic mode mean return per window")
    print('='*120)
    windows = WINDOWS_VALIDATE.split(',')
    print(f"  {'Cell':<5} {'Note':<35}  " + '  '.join(f"{w:>14}" for w in windows))
    print('-'*120)
    for cell_id, _, _, _, note in CELLS:
        if cell_id not in cell_results:
            continue
        r = cell_results[cell_id]['r']
        row = f"  {cell_id:<5} {note:<35}  "
        for w in windows:
            cell = r.get(w, {}).get('realistic', {})
            ret = cell.get('mean_ret')
            row += f"{ret:>+13,.0f}%  " if ret is not None else f"{'-':>14}  "
        print(row)

    print()
    print(f"  {'Cell':<5} {'Note':<35}  " + '  '.join(f"{w+' DD-C':>14}" for w in windows))
    print('-'*120)
    for cell_id, _, _, _, note in CELLS:
        if cell_id not in cell_results:
            continue
        r = cell_results[cell_id]['r']
        row = f"  {cell_id:<5} {note:<35}  "
        for w in windows:
            cell = r.get(w, {}).get('conservative', {})
            dd = cell.get('worst_dd')
            row += f"{dd:>13.1f}%  " if dd is not None else f"{'-':>14}  "
        print(row)

    out_path = os.path.join(os.path.dirname(__file__), 'v29_iv_crush_validate_n500_results.json')
    with open(out_path, 'w') as f:
        json.dump(cell_results, f, indent=2, default=str)
    print(f"\nSaved results to {out_path}")


if __name__ == '__main__':
    main()
