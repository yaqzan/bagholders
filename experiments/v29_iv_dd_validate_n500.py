"""Phase EVS-IV-DD validation — N=500 × 8 windows for top-3 Bayesian candidates.

Validates whether the rank-1 candidate (which passed 80% DD-C floor at N=200
on 22-now) holds at full N=500 across ALL 8 windows (per-year + dip + 22-now + 5y).

Top-3 from evs_iv_dd_top_candidates.json (by utility):
  R1: ULTRA=0.10, top=0.10, mid=0.12, put_top=0.08, HARD=-0.40, MaxPos=12, F3f=0.60/0.50
      22-now N=200: +27.2B%, DD-C=78.9% (ship-eligible)
  R2: ULTRA=0.10, top=0.10, mid=0.10, put_top=0.08, HARD=-0.35, MaxPos=12, F3f=0.60/0.50
      22-now N=200: +9.5B%, DD-C=81.6% (just over floor)
  R3: ULTRA=0.12, top=0.08, mid=0.10, put_top=0.08, HARD=-0.30, MaxPos=10, F3f=0.40/0.40
      22-now N=200: +13.0B%, DD-C=81.4%

Plus reference:
  V0:  v28 + IV stochastic (true baseline under realistic pricing)
  V1:  v29 + IV stochastic (V6 unmodified — DD-C 90.0% on 22-now at N=500)

Ship gate:
  - All 8 Conservative DD-C <= 80%
  - 22-now Realistic >= V0 baseline
  - No Realistic-mean window losing >25% vs V0
  - 0% collapse on every cell

Usage:
    PYTHONIOENCODING=utf-8 python -u experiments/v29_iv_dd_validate_n500.py
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

# Each cell: (id, version, env_overrides, note)
# All IV stochastic. V3 sweep top-3 with DD circuit breaker.
# Fine-tune around R3 — sweep BREAKER threshold tighter to fix 2021/2022 marginal breaches
CELLS = [
    ('B70', '8473cba', {
        'IV_CRUSH_ENABLED': '1', 'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42',
        'DD_CIRCUIT_BREAKER': '0.70',
        'TIER_ALLOC_ULTRA': '0.18', 'TIER_ALLOC_TOP': '0.12',
        'PUT_TIER_PUT_TOP': '0.10', 'HARD_SELL_LOSS': '-0.40', 'MAX_POSITIONS': '14',
    }, 'B70: BREAKER=0.70 ULTRA=0.18 (R3 baseline from prior validation)'),
    ('B68', '8473cba', {
        'IV_CRUSH_ENABLED': '1', 'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42',
        'DD_CIRCUIT_BREAKER': '0.68',
        'TIER_ALLOC_ULTRA': '0.18', 'TIER_ALLOC_TOP': '0.12',
        'PUT_TIER_PUT_TOP': '0.10', 'HARD_SELL_LOSS': '-0.40', 'MAX_POSITIONS': '14',
    }, 'B68: BREAKER=0.68 ULTRA=0.18'),
    ('B65', '8473cba', {
        'IV_CRUSH_ENABLED': '1', 'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42',
        'DD_CIRCUIT_BREAKER': '0.65',
        'TIER_ALLOC_ULTRA': '0.18', 'TIER_ALLOC_TOP': '0.12',
        'PUT_TIER_PUT_TOP': '0.10', 'HARD_SELL_LOSS': '-0.40', 'MAX_POSITIONS': '14',
    }, 'B65: BREAKER=0.65 ULTRA=0.18'),
    ('B60', '8473cba', {
        'IV_CRUSH_ENABLED': '1', 'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42',
        'DD_CIRCUIT_BREAKER': '0.60',
        'TIER_ALLOC_ULTRA': '0.18', 'TIER_ALLOC_TOP': '0.12',
        'PUT_TIER_PUT_TOP': '0.10', 'HARD_SELL_LOSS': '-0.40', 'MAX_POSITIONS': '14',
    }, 'B60: BREAKER=0.60 ULTRA=0.18'),
]

WINDOWS_VALIDATE = '2021,2022,2023,2024,2025,dip,22-now,5y'
N_VALIDATE = 500


def run_cell(cell_id, version, env_overrides, note):
    print(f"\n{'='*100}")
    print(f"CELL {cell_id}: {note}")
    print(f"  version={version}  windows={WINDOWS_VALIDATE}  N={N_VALIDATE}")
    print('='*100, flush=True)

    env = dict(os.environ)
    env['MC_NO_MP'] = '0'
    env['N_ITER_OVERRIDE'] = str(N_VALIDATE)
    env['WINDOWS_OVERRIDE'] = WINDOWS_VALIDATE
    env['ALGORITHM_VERSION_PIN'] = version
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    for k, v in env_overrides.items():
        env[k] = v

    t0 = time.time()
    log_path = os.path.join(os.path.dirname(__file__), f'v29_iv_dd_validate_{cell_id}.out')
    with open(log_path, 'w') as f:
        proc = subprocess.run(
            [sys.executable, '-u', '-c', _RUNNER_CODE],
            env=env, stdout=f, stderr=subprocess.STDOUT, text=True, cwd=ROOT,
        )
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print(f"  FAIL  rc={proc.returncode}  elapsed={elapsed:.0f}s  log={log_path}")
        return None
    print(f"  OK   elapsed={elapsed:.0f}s  log={log_path}")
    return parse_log(log_path)


# We need to apply overrides via env vars — these env names must match what we read
# in the runner code below. Simplest: have the runner monkey-patch monte_carlo's
# constants from env BEFORE main().
_RUNNER_CODE = """
import os, sys
import monte_carlo as mc

def _f(k, default=None):
    v = os.environ.get(k)
    if v is None: return default
    return float(v)

def _i(k, default=None):
    v = os.environ.get(k)
    if v is None: return default
    return int(v)

# Tier alloc overrides
ta = dict(mc.TIER_ALLOC)
if _f('TIER_ALLOC_ULTRA') is not None: ta['ultra'] = _f('TIER_ALLOC_ULTRA')
if _f('TIER_ALLOC_TOP') is not None:   ta['top']   = _f('TIER_ALLOC_TOP')
if _f('TIER_ALLOC_MID') is not None:   ta['mid']   = _f('TIER_ALLOC_MID')
if _f('TIER_ALLOC_LOW') is not None:   ta['low']   = _f('TIER_ALLOC_LOW')
mc.TIER_ALLOC = ta

# Put tier alloc overrides
pta = dict(mc.PUT_TIER_ALLOC)
if _f('PUT_TIER_PUT_TOP') is not None: pta['put_top'] = _f('PUT_TIER_PUT_TOP')
if _f('PUT_TIER_PUT_MID') is not None: pta['put_mid'] = _f('PUT_TIER_PUT_MID')
if _f('PUT_TIER_PUT_LOW') is not None: pta['put_low'] = _f('PUT_TIER_PUT_LOW')
mc.PUT_TIER_ALLOC = pta

# Hard sell override (and net derived)
if _f('HARD_SELL_LOSS') is not None:
    mc.HARD_SELL_LOSS = _f('HARD_SELL_LOSS')
    mc.NET_HARD_SELL = mc.HARD_SELL_LOSS + mc.SLIP_ENTRY + mc.SLIP_HARD

# Max positions
if _i('MAX_POSITIONS') is not None:
    mc.MAX_POSITIONS = _i('MAX_POSITIONS')

# F3f floors
if _f('F3F_CALL_FLOOR') is not None: mc.F3F_CALL_FLOOR = _f('F3F_CALL_FLOOR')
if _f('F3F_PUT_FLOOR') is not None:  mc.F3F_PUT_FLOOR  = _f('F3F_PUT_FLOOR')

# DD circuit breaker
if _f('DD_CIRCUIT_BREAKER') is not None: mc.DD_CIRCUIT_BREAKER = _f('DD_CIRCUIT_BREAKER')

mc.main()
"""


def parse_log(log_path):
    """Extract per-window per-mode metrics from the MC log."""
    results = {}
    current_window = None
    in_block = False
    with open(log_path) as f:
        for line in f:
            s = line.strip()
            if s.startswith('WINDOW: '):
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
    for cell_id, version, env_overrides, note in CELLS:
        r = run_cell(cell_id, version, env_overrides, note)
        if r is None:
            continue
        cell_results[cell_id] = dict(r=r, note=note, version=version, env=env_overrides)

    # Print per-window summary
    windows = WINDOWS_VALIDATE.split(',')

    print('\n' + '='*150)
    print(f"SUMMARY — N={N_VALIDATE}, Realistic mode mean return per window")
    print('='*150)
    print(f"  {'Cell':<5}  " + '  '.join(f"{w:>14}" for w in windows))
    print('-'*150)
    for cell_id, _, _, _ in CELLS:
        if cell_id not in cell_results: continue
        r = cell_results[cell_id]['r']
        row = f"  {cell_id:<5}  "
        for w in windows:
            cell = r.get(w, {}).get('realistic', {})
            ret = cell.get('mean_ret')
            row += f"{ret:>+13,.0f}%  " if ret is not None else f"{'-':>14}  "
        print(row)

    print()
    print(f"  {'Cell':<5}  " + '  '.join(f"{w+' DD-C':>14}" for w in windows))
    print('-'*150)
    for cell_id, _, _, _ in CELLS:
        if cell_id not in cell_results: continue
        r = cell_results[cell_id]['r']
        row = f"  {cell_id:<5}  "
        for w in windows:
            cell = r.get(w, {}).get('conservative', {})
            dd = cell.get('worst_dd')
            mark = '✗' if dd and dd > 80 else ' '
            row += f"{dd:>12.1f}%{mark}  " if dd is not None else f"{'-':>14}  "
        print(row)

    out_path = os.path.join(os.path.dirname(__file__), 'v29_iv_dd_validate_n500_results.json')
    with open(out_path, 'w') as f:
        json.dump(cell_results, f, indent=2, default=str)
    print(f"\nSaved to {out_path}")


if __name__ == '__main__':
    main()
