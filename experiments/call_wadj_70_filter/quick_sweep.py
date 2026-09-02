"""Quick sweep over WEAK_WEEKLY_CALL_DROP variants on v36.

Runs N=200 × {22-now, 5y, 2024} for 1 baseline + 5 variants targeting the
70-84 wadj-neg miss cohort identified in the v36 miss ledger.

Output: experiments/call_wadj_70_filter/quick_sweep.jsonl + console table.
"""
from __future__ import annotations
import io, sys, json, os, time, subprocess
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = Path(__file__).parent / 'quick_sweep.jsonl'

VARIANTS = [
    # (label, env_kv)
    ('baseline',           {}),
    ('A_70-74_w0',         {'WEAK_WEEKLY_CALL_DROP':'1', 'WEAK_WEEKLY_CALL_MIN_OV':'70', 'WEAK_WEEKLY_CALL_MAX_OV':'74', 'WEAK_WEEKLY_CALL_WADJ':'0'}),
    ('B_70-84_w0',         {'WEAK_WEEKLY_CALL_DROP':'1', 'WEAK_WEEKLY_CALL_MIN_OV':'70', 'WEAK_WEEKLY_CALL_MAX_OV':'84', 'WEAK_WEEKLY_CALL_WADJ':'0'}),
    ('C_70-84_wneg3',      {'WEAK_WEEKLY_CALL_DROP':'1', 'WEAK_WEEKLY_CALL_MIN_OV':'70', 'WEAK_WEEKLY_CALL_MAX_OV':'84', 'WEAK_WEEKLY_CALL_WADJ':'-3'}),
    ('D_70-84_w0_stoch35', {'WEAK_WEEKLY_CALL_DROP':'1', 'WEAK_WEEKLY_CALL_MIN_OV':'70', 'WEAK_WEEKLY_CALL_MAX_OV':'84', 'WEAK_WEEKLY_CALL_WADJ':'0', 'WEAK_WEEKLY_CALL_STOCH_GE':'35'}),
    ('E_70-79_w0',         {'WEAK_WEEKLY_CALL_DROP':'1', 'WEAK_WEEKLY_CALL_MIN_OV':'70', 'WEAK_WEEKLY_CALL_MAX_OV':'79', 'WEAK_WEEKLY_CALL_WADJ':'0'}),
]

WINDOWS = ['22-now', '5y', '2024']
N_ITER = 200


def run_variant(label, env_kv, window):
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    # Use MP — workers re-import monte_carlo.py and pick up env vars
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = window
    env['PYTHONHASHSEED'] = '0'  # deterministic seed
    for k, v in env_kv.items():
        env[k] = v

    t0 = time.time()
    p = subprocess.run(['python', '-u', 'monte_carlo.py'],
                       env=env, cwd=str(ROOT),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    dur = time.time() - t0
    if p.returncode != 0:
        return {'label': label, 'window': window, 'error': p.stderr[-500:], 'dur': dur}

    # Parse seeded line
    out = {'label': label, 'window': window, 'dur': dur, 'env': env_kv}
    for line in p.stdout.splitlines():
        if line.strip().startswith('seeded') and '%' in line:
            # seeded   59.9%  44.9%  2579.9  2682.3  +1.79e26%  +5.68e24%   70.5%   66.1%   0.0%
            parts = line.split()
            try:
                out['call_tp'] = float(parts[1].rstrip('%'))
                out['put_tp']  = float(parts[2].rstrip('%'))
                out['c_trd']   = float(parts[3])
                out['p_trd']   = float(parts[4])
                out['mean_ret'] = float(parts[5].rstrip('%').replace('+',''))
                out['med_ret']  = float(parts[6].rstrip('%').replace('+',''))
                out['worst_dd'] = float(parts[7].rstrip('%'))
                out['mean_dd']  = float(parts[8].rstrip('%'))
                out['p_coll']   = float(parts[9].rstrip('%'))
            except Exception as e:
                out['parse_error'] = str(e)
                out['raw'] = line
            break
    if 'mean_ret' not in out:
        out['stdout_tail'] = p.stdout[-1000:]
    return out


def main():
    if LOG.exists():
        LOG.unlink()

    print(f'[sweep] N_ITER={N_ITER} variants={len(VARIANTS)} windows={len(WINDOWS)} '
          f'-> {len(VARIANTS)*len(WINDOWS)} runs', flush=True)
    rows = []
    for vlabel, env_kv in VARIANTS:
        for window in WINDOWS:
            t0 = time.time()
            r = run_variant(vlabel, env_kv, window)
            dur = time.time() - t0
            rows.append(r)
            with open(LOG, 'a', encoding='utf-8') as f:
                f.write(json.dumps(r, default=str) + '\n')
            tp = r.get('call_tp', '--')
            mr = r.get('med_ret')
            dd = r.get('worst_dd')
            mr_s = f'{mr:.2e}%' if isinstance(mr, (int, float)) else '--'
            dd_s = f'{dd:.1f}%' if isinstance(dd, (int, float)) else '--'
            print(f'  {vlabel:24s} {window:6s} | CTP={tp}% MedRet={mr_s} DD={dd_s} ({dur:.0f}s)', flush=True)

    # Print final comparison table
    print('\n' + '='*120)
    print('SWEEP RESULT (sorted by 22-now MedRet)')
    print('='*120)
    print(f'{"variant":24s} {"window":6s} | {"CTP%":>6s} {"PTP%":>6s} {"MedRet":>14s} {"WorstDD":>7s} {"P(col)":>6s}')
    print('-'*120)
    for r in rows:
        if 'mean_ret' not in r: continue
        mr = r['med_ret']
        mr_s = f'{mr:.2e}%' if isinstance(mr, (int,float)) else '--'
        print(f'{r["label"]:24s} {r["window"]:6s} | {r["call_tp"]:>5.1f}% {r["put_tp"]:>5.1f}% {mr_s:>14s} {r["worst_dd"]:>6.1f}% {r["p_coll"]:>5.1f}%')


if __name__ == '__main__':
    main()
