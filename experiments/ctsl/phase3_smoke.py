"""Phase 3 smoke test: run 3-config matrix at N=20 × 22-now to verify
the three configs produce expected differences before committing to N=500.

Config A (production):     CTSL_ENABLED=0  CT_PROMOTE=1
Config B (stack):          CTSL_ENABLED=1  CT_PROMOTE=1
Config C (substitute):     CTSL_ENABLED=1  CT_PROMOTE=0
"""
from __future__ import annotations
import os, sys, json, subprocess, re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / 'experiments' / 'ctsl'
LOG_DIR.mkdir(parents=True, exist_ok=True)

CONFIGS = {
    'A_baseline':  {'CTSL_ENABLED': '0', 'CT_PROMOTE': '1'},
    'B_stack':     {'CTSL_ENABLED': '1', 'CT_PROMOTE': '1'},
    'C_substitute':{'CTSL_ENABLED': '1', 'CT_PROMOTE': '0'},
}

N_ITER = 20
WINDOW = '22-now'

ENV_BASE = {
    'PYTHONIOENCODING': 'utf-8',
    'N_ITER_OVERRIDE': str(N_ITER),
    'WINDOWS_OVERRIDE': WINDOW,
    'MC_NO_MP': '1',
}


def run_one(label: str, overrides: dict) -> dict:
    env = {**os.environ, **ENV_BASE, **overrides}
    log_path = LOG_DIR / f'phase3_smoke_{label}.log'
    print(f'\n[smoke] running {label}: {overrides}', flush=True)
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as f:
        proc = subprocess.run(
            [sys.executable, '-u', str(ROOT / 'monte_carlo.py')],
            env=env, stdout=f, stderr=subprocess.STDOUT,
            timeout=600,
        )
    dur = time.time() - t0
    print(f'[smoke] {label} done in {dur:.1f}s (rc={proc.returncode})', flush=True)
    # Parse summary line
    text = log_path.read_text(encoding='utf-8', errors='replace')
    m = re.search(
        r'^\s*' + re.escape(WINDOW) + r'\s+([+-][\d,\.]+)%\s+([+-][\d,\.]+)%\s+(\d+\.\d+)%\s+(\d+\.\d+)%\s+(\d+\.\d+)%',
        text, re.MULTILINE,
    )
    if m:
        out = {
            'label': label,
            'mean_ret_str': m.group(1),
            'med_ret_str': m.group(2),
            'worst_dd': float(m.group(3)),
            'mean_dd': float(m.group(4)),
            'p_collapse': float(m.group(5)),
            'duration_s': dur,
        }
    else:
        out = {'label': label, 'parse_error': True, 'duration_s': dur,
               'log_tail': text[-2000:]}
    # Also pull CTRD/PTRD and TP rates
    m2 = re.search(r'seeded\s+(\d+\.\d+)%\s+(\d+\.\d+)%\s+(\d+\.\d+)\s+(\d+\.\d+)', text)
    if m2:
        out['ctp_pct'] = float(m2.group(1))
        out['ptp_pct'] = float(m2.group(2))
        out['ctrd'] = float(m2.group(3))
        out['ptrd'] = float(m2.group(4))
    return out


def main():
    results = {}
    for label, overrides in CONFIGS.items():
        results[label] = run_one(label, overrides)

    print('\n' + '='*100)
    print(' PHASE 3 SMOKE — 3-config comparison (N=20, 22-now)')
    print('='*100)
    print(f'  {"label":<18}  {"mean_ret":>22}  {"WorstDD":>9}  {"MeanDD":>8}  {"P(coll)":>8}  {"CTP%":>6}  {"PTP%":>6}  {"CTrd":>7}  {"PTrd":>7}')
    for label in ('A_baseline', 'B_stack', 'C_substitute'):
        r = results.get(label, {})
        if 'parse_error' in r:
            print(f'  {label:<18}  PARSE ERROR — see log')
            continue
        print(f'  {label:<18}  {r.get("mean_ret_str", "?"):>22}  '
              f'{r.get("worst_dd", -1):>8.1f}%  {r.get("mean_dd", -1):>7.1f}%  '
              f'{r.get("p_collapse", -1):>7.1f}%  '
              f'{r.get("ctp_pct", -1):>5.1f}%  {r.get("ptp_pct", -1):>5.1f}%  '
              f'{r.get("ctrd", -1):>7.1f}  {r.get("ptrd", -1):>7.1f}')

    # Persist
    with open(LOG_DIR / 'phase3_smoke_summary.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, default=str, indent=2)


if __name__ == '__main__':
    main()
