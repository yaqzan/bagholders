"""Fine 3x3 grid around the D winner at N=300 × 8 windows.

Locks WADJ_LT=0 (per quick_sweep — narrower -3 underperformed). Sweeps
STOCH_GE ∈ {30, 35, 40} × MAX_OV ∈ {79, 84, 89}, plus baseline for reference.

Pass criteria for "robust" classification:
- ≥5 of 8 perturbations pass P1-P6
- 5y compound within ±50% of D-shipped (+232.8%)

If only D passes → fall back to E. If 5+ pass → ship D with confidence.
"""
from __future__ import annotations
import io, sys, json, os, time, subprocess
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = Path(__file__).parent / 'fine_grid.jsonl'

VARIANTS = [
    ('baseline', {}),
    # 3x3 grid: stoch_ge x max_ov, wadj=0 locked
    ('s30_o79', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'79','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'30'}),
    ('s30_o84', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'84','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'30'}),
    ('s30_o89', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'89','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'30'}),
    ('s35_o79', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'79','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'35'}),
    ('s35_o84_D', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'84','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'35'}),  # shipped D
    ('s35_o89', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'89','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'35'}),
    ('s40_o79', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'79','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'40'}),
    ('s40_o84', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'84','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'40'}),
    ('s40_o89', {'WEAK_WEEKLY_CALL_DROP':'1','WEAK_WEEKLY_CALL_MIN_OV':'70','WEAK_WEEKLY_CALL_MAX_OV':'89','WEAK_WEEKLY_CALL_WADJ':'0','WEAK_WEEKLY_CALL_STOCH_GE':'40'}),
]

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER = 300


def run(label, env_kv, window):
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = window
    env['PYTHONHASHSEED'] = '0'
    env['ALGORITHM_VERSION_PIN'] = 'd5ef1f5'  # v36 — pinned to match miss-ledger source
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
    # Resume mode: read existing jsonl and skip completed runs
    completed = set()
    if LOG.exists():
        with open(LOG) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if 'mean_ret' in r and r.get('c_trd', 0) > 50:  # exclude contaminated v37 rows
                        completed.add((r['label'], r['window']))
                except Exception: pass
        print(f'[fine] resuming with {len(completed)} valid runs already complete', flush=True)
    print(f'[fine] N_ITER={N_ITER} variants={len(VARIANTS)} windows={len(WINDOWS)} '
          f'-> {len(VARIANTS)*len(WINDOWS)} runs total, {len(VARIANTS)*len(WINDOWS)-len(completed)} remaining', flush=True)
    rows = []
    # First load any pre-existing valid runs
    if LOG.exists():
        with open(LOG) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if (r.get('label'), r.get('window')) in completed:
                        rows.append(r)
                except Exception: pass
    for vlabel, env_kv in VARIANTS:
        for window in WINDOWS:
            if (vlabel, window) in completed:
                continue
            r = run(vlabel, env_kv, window)
            rows.append(r)
            with open(LOG, 'a', encoding='utf-8') as f:
                f.write(json.dumps(r, default=str) + '\n')
            tp = r.get('call_tp', '--')
            mr = r.get('med_ret')
            dd = r.get('worst_dd')
            mr_s = f'{mr:.2e}%' if isinstance(mr, (int,float)) else '--'
            dd_s = f'{dd:.1f}%' if isinstance(dd, (int,float)) else '--'
            print(f'  {vlabel:12s} {window:6s} | CTP={tp}% MedRet={mr_s} DD={dd_s} ({r.get("dur",0):.0f}s)', flush=True)

    # Build per-variant pass/fail vs baseline
    by_label = {}
    for r in rows:
        if 'mean_ret' not in r: continue
        by_label.setdefault(r['label'], {})[r['window']] = r
    base = by_label.get('baseline', {})

    print('\n' + '='*120)
    print('FINE GRID — P1-P6 PASS TABLE (vs baseline)')
    print('='*120)
    print(f'{"variant":12s} | {"5y Δret":>9s} {"5y ΔDD":>7s} | {"22-now Δret":>11s} {"22-now ΔDD":>10s} | '
          f'{"worst Δret":>10s} {"worst ΔDD":>9s} | {"max DD":>6s} | P3 P4 P5 | RANK')
    print('-'*120)

    summary = []
    for vlabel in [v[0] for v in VARIANTS if v[0] != 'baseline']:
        v = by_label.get(vlabel, {})
        if not v:
            print(f'{vlabel}: missing'); continue
        b5 = base.get('5y'); v5 = v.get('5y')
        b22 = base.get('22-now'); v22 = v.get('22-now')
        if not (b5 and v5 and b22 and v22): continue
        d5_ret = (v5['med_ret'] - b5['med_ret']) / abs(b5['med_ret']) * 100 if b5['med_ret'] != 0 else 0
        d5_dd  = v5['worst_dd'] - b5['worst_dd']
        d22_ret = (v22['med_ret'] - b22['med_ret']) / abs(b22['med_ret']) * 100 if b22['med_ret'] != 0 else 0
        d22_dd  = v22['worst_dd'] - b22['worst_dd']

        worst_d_ret = 0.0; worst_d_dd = 0.0; max_dd = 0.0; max_coll = 0.0
        for w in WINDOWS:
            b = base.get(w); x = v.get(w)
            if b is None or x is None: continue
            d_r = (x['med_ret'] - b['med_ret']) / abs(b['med_ret']) * 100 if b['med_ret'] != 0 else 0
            d_d = x['worst_dd'] - b['worst_dd']
            if d_r < worst_d_ret: worst_d_ret = d_r
            if d_d > worst_d_dd:  worst_d_dd  = d_d
            if x['worst_dd'] > max_dd: max_dd = x['worst_dd']
            if x['p_coll'] > max_coll: max_coll = x['p_coll']

        p3 = (v5['med_ret'] >= b5['med_ret']) and (v22['med_ret'] >= b22['med_ret'])
        p4 = worst_d_ret >= -25.0
        p5 = max_coll <= 0.0
        passed = p3 and p4 and p5
        rank_s = 'PASS' if passed else 'FAIL'
        print(f'{vlabel:12s} | {d5_ret:>+8.1f}% {d5_dd:>+6.1f}pp | {d22_ret:>+10.1f}% {d22_dd:>+9.1f}pp | '
              f'{worst_d_ret:>+9.1f}% {worst_d_dd:>+8.1f}pp | {max_dd:>5.1f}% | '
              f'{"Y" if p3 else "n"}  {"Y" if p4 else "n"}  {"Y" if p5 else "n"}  | {rank_s}')
        summary.append((vlabel, passed, d5_ret, d5_dd, d22_ret, max_dd))

    # Rank passers by 5y compound
    passers = [s for s in summary if s[1]]
    print(f'\nVariants passing P1-P6: {len(passers)} / {len(summary)}')
    if passers:
        print('\nRanked by 5y compound (top to bottom):')
        for vlabel, _, d5_ret, d5_dd, d22_ret, max_dd in sorted(passers, key=lambda x: -x[2]):
            print(f'  {vlabel:12s} 5y={d5_ret:+.1f}% / 5yDD={d5_dd:+.1f}pp / 22-now={d22_ret:+.1f}% / maxDD={max_dd:.1f}%')


if __name__ == '__main__':
    main()
