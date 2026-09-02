"""V37 portfolio validation — same baseline + D + E variants, pinned to v37.

V37 scores are byte-identical to v36 on the call side (PCD only changes puts),
so the per-trade signal transfers. But put count dropped ~30%, changing
cascade dynamics. This re-validates the portfolio result on v37 scores.

N=300 × 8 windows. Uses ALGORITHM_VERSION_PIN=6f9afda.
"""
from __future__ import annotations
import io, sys, json, os, time, subprocess
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = Path(__file__).parent / 'validate_v37.jsonl'

VARIANTS = [
    ('baseline', {}),
    ('D_70-84_w0_stoch35', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'84','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'35'}),
    ('E_70-79_w0',         {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'79','WEAK_WEEKLY_CALL_WADJ':'0'}),
]

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER = 300


def run(label, env_kv, window):
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = window
    env['PYTHONHASHSEED'] = '0'
    env['ALGORITHM_VERSION_PIN'] = '6f9afda'  # v37
    for k, v in env_kv.items():
        env[k] = v

    t0 = time.time()
    p = subprocess.run(['python', '-u', 'monte_carlo.py'],
                       env=env, cwd=str(ROOT),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    dur = time.time() - t0
    if p.returncode != 0:
        return {'label': label, 'window': window, 'error': p.stderr[-500:], 'dur': dur}
    out = {'label': label, 'window': window, 'dur': dur, 'env': env_kv}
    for line in p.stdout.splitlines():
        if line.strip().startswith('seeded') and '%' in line:
            parts = line.split()
            try:
                out['call_tp']  = float(parts[1].rstrip('%'))
                out['put_tp']   = float(parts[2].rstrip('%'))
                out['c_trd']    = float(parts[3])
                out['p_trd']    = float(parts[4])
                out['mean_ret'] = float(parts[5].rstrip('%').replace('+',''))
                out['med_ret']  = float(parts[6].rstrip('%').replace('+',''))
                out['worst_dd'] = float(parts[7].rstrip('%'))
                out['mean_dd']  = float(parts[8].rstrip('%'))
                out['p_coll']   = float(parts[9].rstrip('%'))
            except Exception as e:
                out['parse_error'] = str(e); out['raw'] = line
            break
    return out


def main():
    # Resume support
    completed = set()
    rows = []
    if LOG.exists():
        with open(LOG) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if 'mean_ret' in r and r.get('c_trd', 0) >= 50:
                        completed.add((r['label'], r['window']))
                        rows.append(r)
                except Exception: pass
        print(f'[v37-val] resuming with {len(completed)} valid runs already complete', flush=True)

    print(f'[v37-val] N_ITER={N_ITER} variants={len(VARIANTS)} windows={len(WINDOWS)} '
          f'-> {len(VARIANTS)*len(WINDOWS)} runs', flush=True)
    for vlabel, env_kv in VARIANTS:
        for window in WINDOWS:
            if (vlabel, window) in completed: continue
            r = run(vlabel, env_kv, window)
            rows.append(r)
            with open(LOG, 'a', encoding='utf-8') as f:
                f.write(json.dumps(r, default=str) + '\n')
            tp = r.get('call_tp', '--')
            mr = r.get('med_ret')
            dd = r.get('worst_dd')
            mr_s = f'{mr:.2e}%' if isinstance(mr, (int,float)) else '--'
            dd_s = f'{dd:.1f}%' if isinstance(dd, (int,float)) else '--'
            print(f'  {vlabel:24s} {window:6s} | CTP={tp}% MedRet={mr_s} DD={dd_s} ({r.get("dur",0):.0f}s)', flush=True)

    # Compare
    by_label = {}
    for r in rows:
        if 'mean_ret' not in r: continue
        by_label.setdefault(r['label'], {})[r['window']] = r
    base = by_label.get('baseline', {})
    print('\n' + '='*120)
    print('V37 P1-P6 GATE EVALUATION (vs v37 baseline, N=300 × 8 windows)')
    print('='*120)
    for vlabel in [v[0] for v in VARIANTS if v[0] != 'baseline']:
        v = by_label.get(vlabel, {})
        if not v: continue
        print(f'\n--- {vlabel} ---')
        print(f'{"window":7s} | {"baseTP":>6s} {"varTP":>6s} | {"baseMed":>13s} {"varMed":>13s} {"Δret":>8s} | {"baseDD":>6s} {"varDD":>6s} {"ΔDD":>7s}')
        worst_ret_pct = 0.0; max_dd_var = 0.0; max_collapse = 0.0
        for w in WINDOWS:
            b = base.get(w); x = v.get(w)
            if b is None or x is None: continue
            d_ret = (x['med_ret'] - b['med_ret']) / abs(b['med_ret']) * 100 if b['med_ret'] != 0 else 0
            d_dd  = x['worst_dd'] - b['worst_dd']
            print(f'{w:7s} | {b["call_tp"]:>5.1f}% {x["call_tp"]:>5.1f}% | '
                  f'{b["med_ret"]:>12.2e}% {x["med_ret"]:>12.2e}% {d_ret:>+7.1f}% | '
                  f'{b["worst_dd"]:>5.1f}% {x["worst_dd"]:>5.1f}% {d_dd:>+6.1f}pp')
            worst_ret_pct = min(worst_ret_pct, d_ret)
            max_dd_var = max(max_dd_var, x['worst_dd'])
            max_collapse = max(max_collapse, x['p_coll'])
        b5 = base.get('5y'); x5 = v.get('5y')
        b22 = base.get('22-now'); x22 = v.get('22-now')
        p3 = (x5['med_ret'] >= b5['med_ret']) and (x22['med_ret'] >= b22['med_ret']) if (b5 and x5 and b22 and x22) else False
        p4 = worst_ret_pct >= -25.0
        p5 = max_collapse <= 0.0
        print(f'  P3 (5y≥base AND 22-now≥base): {"PASS" if p3 else "FAIL"}')
        print(f'  P4 (no annual <-25%): {"PASS" if p4 else "FAIL"} (worst {worst_ret_pct:+.1f}%)')
        print(f'  P5 (0 collapses): {"PASS" if p5 else "FAIL"} (max collapse {max_collapse:.1f}%)')
        print(f'  P6 (max DD): {max_dd_var:.1f}%')


if __name__ == '__main__':
    main()
