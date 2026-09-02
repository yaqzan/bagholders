"""
Multi-window validation of top-3 Bayesian-optimal candidates.

Per assessment-backtest.md H5 gate: TP%/WR15 sign-consistent across 1y/3y/5y.

Reads opt_result.json from Phase 2, plus 2 hand-picked alternatives near the
optimum, then runs evaluate() at lookback ∈ {365, 1095, 1825} for each.
"""
from __future__ import annotations
import io
import json
import sys
import time
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module
runner_mod = import_module('01_runner')
WadjDowRunner = runner_mod.WadjDowRunner

OUT_DIR = Path(__file__).resolve().parent
RESULT_PATH = OUT_DIR / 'opt_result.json'
VALIDATE_PATH = OUT_DIR / 'multiwindow_validate.json'


def extract(stats, bucket, period='15d'):
    s = stats['bucketed_stats'].get(bucket, {})
    return s.get(f'win_rate_{period}'), s.get('sample_count', 0)


def neighbor(coeffs, idx, delta):
    c = list(coeffs)
    c[idx] = max(0.0, min(1.5, c[idx] + delta))
    return c


def run():
    print('Loading runner...', flush=True)
    r = WadjDowRunner()
    r.load(verbose=False)
    print(f'  v{r.version_id}, {len(r.df):,} score rows', flush=True)

    if RESULT_PATH.exists():
        opt = json.loads(RESULT_PATH.read_text())
        b = opt['best_coeffs']
        best = [b['mon'], b['tue'], b['wed'], b['thu'], b['fri']]
        print(f'\nBest from Phase 2: {[round(x, 3) for x in best]}')
    else:
        print('  no Phase 2 result yet — using neutral [1,1,1,1,1] as the candidate')
        best = [1.0]*5

    candidates = {
        'best': best,
        'neutral_baseline': [1.0]*5,
    }
    # Round-numbered candidates near best for sanity-check robustness
    candidates['best_rounded_0.1'] = [round(x*10)/10 for x in best]
    # A "smooth ramp 0.4-1.0" sanity reference
    candidates['ramp_04_06_08_09_10'] = [0.4, 0.6, 0.8, 0.9, 1.0]

    windows = {'1y': 365, '3y': 1095, '5y': 1825}
    out = {}
    print('\nMulti-window validation:')
    for cand_name, coeffs in candidates.items():
        out[cand_name] = {'coeffs': coeffs, 'windows': {}}
        print(f'\n--- {cand_name}: {[round(c,3) for c in coeffs]} ---')
        for win_label, days in windows.items():
            t0 = time.time()
            stats = r.evaluate(coeffs, lookback_days=days)
            dt = time.time() - t0
            res = {}
            print(f'  {win_label} ({days}d, {dt:.1f}s):')
            for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']:
                wr15, n = extract(stats, b, '15d')
                wr30, _ = extract(stats, b, '30d')
                res[b] = {'wr15': wr15, 'wr30': wr30, 'n': n}
                if wr15 is not None:
                    print(f'    {b:<6} N={n:>6}  WR15={wr15:>5.2f}%  WR30={wr30:>5.2f}%')
            out[cand_name]['windows'][win_label] = res

    # Sign-consistency check: for primary tiers, does Δ_WR15 vs neutral_baseline
    # have the same sign across 1y/3y/5y?
    print('\n=== H5 sign-consistency check (Δ_WR15 vs neutral_baseline) ===')
    base = out['neutral_baseline']['windows']
    for cand_name, cand_data in out.items():
        if cand_name == 'neutral_baseline':
            continue
        print(f'\n{cand_name}:')
        for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']:
            deltas = []
            for w in ['1y', '3y', '5y']:
                cv = cand_data['windows'][w].get(b, {}).get('wr15')
                bv = base[w].get(b, {}).get('wr15')
                if cv is not None and bv is not None:
                    deltas.append((w, cv - bv))
            if not deltas:
                continue
            signs = {'+' if d > 0 else '-' if d < 0 else '0' for _, d in deltas}
            consistent = '✓' if len(signs - {'0'}) <= 1 else '✗'
            delta_str = '  '.join(f'{w}={d:+.2f}pp' for w, d in deltas)
            print(f'  {b:<6} {consistent}  {delta_str}')

    VALIDATE_PATH.write_text(json.dumps(out, indent=2))
    print(f'\n[OK] saved {VALIDATE_PATH}', flush=True)


if __name__ == '__main__':
    run()
