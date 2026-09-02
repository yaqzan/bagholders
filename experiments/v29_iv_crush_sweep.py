"""Phase EVS-IV — V6 gradient with IV-aware option pricing.

Re-tests Phase EVS V6 (reverted as v29 / 8473cba) under the new IV crush
model (`iv_crush_model.py`).  Hypothesis: V6's per-trade WR uplift on
earnings-window calls (95+ +1.4pp at +21% N) failed canonical MC because
the MC's flat 1.82xsigma option-pricing model under-states the cost of
earnings-window options.  Adding IV crush awareness should re-price
those signals correctly and rehabilitate V6.

Sweep matrix (6 cells, 22-now window at N=200 screening):

    ID    Version    IV Crush    EARN_SUPP_PUT    Note
    A0    v28        OFF         on (prod)        production baseline
    A1    v28        ON          on               control: IV effect alone
    B0    v29        OFF         on               reproduce original failure
    B1    v29        ON          on               KEY TEST: does IV fix V6?
    B2    v29        ON          off              rescope: no put filter
    B3    v29        ON          on (16-20, d=3)  rescope: tighter window

Ship gates (vs A0):
  - 22-now Real >= A0 (or within 5% noise)
  - DD-C <= 80%
  - 0% collapse

Top performers advance to N=500 across 8 windows for canonical validation.

Usage:
    MC_NO_MP=1 PYTHONIOENCODING=utf-8 python -u experiments/v29_iv_crush_sweep.py
"""
from __future__ import annotations
import json
import os
import sys
import time
import subprocess
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


CELLS = [
    # (id, version_commit, iv_on, env_overrides, note)
    ('A0', 'e3c8678', '0', {},                                                            'v28 baseline (IV OFF)'),
    ('A1', 'e3c8678', '1', {'IV_CRUSH_MODE': 'deterministic'},                            'v28 + IV deterministic'),
    ('A2', 'e3c8678', '1', {'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42'},       'v28 + IV stochastic'),
    ('B0', '8473cba', '0', {},                                                            'v29 (V6) original (IV OFF)'),
    ('B1', '8473cba', '1', {'IV_CRUSH_MODE': 'deterministic'},                            'v29 + IV deterministic'),
    ('B2', '8473cba', '1', {'IV_CRUSH_MODE': 'stochastic', 'IV_CRUSH_SEED': '42'},       'v29 + IV stochastic KEY'),
]

WINDOWS_FOR_SCREEN = '22-now'
N_SCREEN = 200


def run_cell(cell_id, version_commit, iv_on, env_overrides, note,
             windows=WINDOWS_FOR_SCREEN, n_iter=N_SCREEN):
    """Spawn monte_carlo as subprocess with the given config + version pin.
    Returns parsed dict of (mode -> {mean_ret, worst_dd, p_coll}).
    """
    print(f"\n{'='*100}")
    print(f"CELL {cell_id}: {note}")
    print(f"  version={version_commit}  iv_crush={iv_on}  windows={windows}  N={n_iter}")
    for k, v in env_overrides.items():
        print(f"  env {k}={v}")
    print('='*100, flush=True)

    env = dict(os.environ)
    env['MC_NO_MP']            = '0'   # use MP for speed
    env['N_ITER_OVERRIDE']     = str(n_iter)
    env['WINDOWS_OVERRIDE']    = windows
    env['IV_CRUSH_ENABLED']    = iv_on
    env['ALGORITHM_VERSION_PIN'] = version_commit  # NEW env var we'll honor in monte_carlo.py
    env['PYTHONIOENCODING']    = 'utf-8'
    env['PYTHONUNBUFFERED']    = '1'
    for k, v in env_overrides.items():
        env[k] = v

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, '-u', 'monte_carlo.py'],
        env=env, capture_output=True, text=True, cwd=ROOT,
    )
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print(f"  FAIL  rc={proc.returncode}  elapsed={elapsed:.0f}s")
        print(proc.stderr[-2000:])
        return None
    print(f"  OK   elapsed={elapsed:.0f}s")

    return parse_output(proc.stdout, windows)


def parse_output(stdout, windows):
    """Extract per-mode (mean_ret, worst_dd, p_coll, ctp, ptp) from MC output.
    Looks for the per-window block printed at the end of run_window.

    Format:
        Mode            CTP%   PTP%    CTrd    PTrd         MeanRet  ... WorstDD  ... P(col)
        conservative   62.8%  51.3%  552.6  300.3      +287125.6%   ... 44.4%    ... 0.0%
    """
    results = {windows: {}}
    in_block = False
    for line in stdout.splitlines():
        s = line.strip()
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
                ctp     = float(parts[1].rstrip('%'))
                ptp     = float(parts[2].rstrip('%'))
                ctrd    = float(parts[3])
                ptrd    = float(parts[4])
                mean_r  = float(parts[5].replace('%','').replace(',','').lstrip('+'))
                med_r   = float(parts[6].replace('%','').replace(',','').lstrip('+'))
                worst_dd= float(parts[7].rstrip('%'))
                mean_dd = float(parts[8].rstrip('%'))
                p_coll  = float(parts[9].rstrip('%'))
                results[windows][mode] = dict(
                    mean_ret=mean_r, med_ret=med_r,
                    worst_dd=worst_dd, mean_dd=mean_dd, p_coll=p_coll,
                    ctp=ctp, ptp=ptp, ctrd=ctrd, ptrd=ptrd,
                )
            except (ValueError, IndexError):
                continue

    # If only one window, return flat structure
    if len(results) == 1 and windows in results:
        return results[windows]
    return results


def main():
    cell_results = {}
    for cell_id, version, iv_on, env_overrides, note in CELLS:
        r = run_cell(cell_id, version, iv_on, env_overrides, note)
        if r is None:
            print(f"  Cell {cell_id} skipped due to error")
            continue
        cell_results[cell_id] = dict(r=r, note=note, version=version, iv_on=iv_on,
                                       env_overrides=env_overrides)

    # Summary
    print('\n' + '='*100)
    print(f"SUMMARY — {WINDOWS_FOR_SCREEN} N={N_SCREEN}, Realistic mode")
    print('='*100)
    print(f"  {'Cell':<5} {'Note':<32}  {'MeanRet':>14}  {'DD-C':>6}  {'CTP%':>5}  {'PTP%':>5}  {'CTrd':>5}  {'PTrd':>5}")
    print('-'*100)
    if 'A0' in cell_results:
        baseline_ret = cell_results['A0']['r']['realistic']['mean_ret']
    else:
        baseline_ret = None

    for cell_id, _, _, _, note in CELLS:
        if cell_id not in cell_results:
            print(f"  {cell_id:<5} {note:<32}   (failed)")
            continue
        r = cell_results[cell_id]['r']
        real = r['realistic']
        cons = r['conservative']
        delta = ''
        if baseline_ret is not None:
            d = (real['mean_ret'] - baseline_ret) / max(abs(baseline_ret), 1.0) * 100
            delta = f"  d={d:+.1f}%"
        print(f"  {cell_id:<5} {note:<32}  {real['mean_ret']:>+13,.1f}%  {cons['worst_dd']:>5.1f}%  {real['ctp']:>4.1f}%  {real['ptp']:>4.1f}%  {real['ctrd']:>5.1f}  {real['ptrd']:>5.1f}{delta}")

    # Save
    out_path = os.path.join(os.path.dirname(__file__), 'v29_iv_crush_sweep_results.json')
    with open(out_path, 'w') as f:
        json.dump(cell_results, f, indent=2, default=str)
    print(f"\nSaved results to {out_path}")


if __name__ == '__main__':
    main()
