"""
5-D Bayesian optimization of day-of-week wadj dampener coefficients.

Loss function (per-trade WR15-based, mirrors H1-H5 ship gate spirit):
    Maximize: weighted sum of TP-rate (avg_return-derived hit rate) on key buckets,
              minus a penalty for N collapse on any primary tier.

Specifically:
    affected_buckets = {'95+': 0.5, '90+': 1.0, '85+': 1.5, '80+': 2.0, '75+': 2.0,
                         '70+': 1.5, '<5': 0.8, '<15': 1.5, '<25': 1.5}
    objective = sum(weight × WR15_delta_pp) − N_penalty
    N_penalty = 5 if any tier in {75+, 80+, 85+, 90+} drops >15% in N

Bounds: all 5 coefficients ∈ [0.0, 1.5]. (Allow >1 to test amplification on Fri,
just in case the "all weekly is undertrusted" hypothesis is right.)

80 evaluations via skopt's gp_minimize. Random_state=42.
"""
from __future__ import annotations
import io
import json
import sys
import time
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
from skopt import gp_minimize
from skopt.space import Real

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module
runner_mod = import_module('01_runner')
WadjDowRunner = runner_mod.WadjDowRunner

OUT_DIR = Path(__file__).resolve().parent
RESULT_PATH = OUT_DIR / 'opt_result.json'

BUCKET_WEIGHTS = {
    '95+': 0.5, '90+': 1.0, '85+': 1.5, '80+': 2.0, '75+': 2.0, '70+': 1.5,
    '<5': 0.8, '<15': 1.5, '<25': 1.5,
}
PRIMARY_TIERS = ['75+', '80+', '85+', '90+']  # tiers with hard N-floor


def extract_metric(stats, bucket, period='15d', metric='win_rate'):
    s = stats['bucketed_stats'].get(bucket, {})
    return s.get(f'{metric}_{period}'), s.get('sample_count', 0)


def loss_fn(coeffs, baseline_stats, runner, lookback_days=1825):
    stats = runner.evaluate(list(coeffs), lookback_days=lookback_days)
    delta_sum = 0.0
    n_penalty = 0.0

    for bucket, w in BUCKET_WEIGHTS.items():
        wr_var, n_var = extract_metric(stats, bucket)
        wr_base, n_base = extract_metric(baseline_stats, bucket)
        if wr_var is None or wr_base is None:
            continue
        delta_sum += w * (wr_var - wr_base)

        # N-floor penalty on primary call tiers
        if bucket in PRIMARY_TIERS and n_base > 0:
            if n_var < n_base * 0.85:  # >15% drop
                n_penalty += 5.0 * w

    # Maximize delta_sum - n_penalty → minimize -delta_sum + n_penalty
    return -(delta_sum - n_penalty)


def sweep(loop_id):
    print(f'[{loop_id}] Loading runner...', flush=True)
    runner = WadjDowRunner()
    runner.load(verbose=False)
    print(f'[{loop_id}] version_id={runner.version_id}, score rows={len(runner.df):,}', flush=True)

    print(f'[{loop_id}] Computing baseline (5y, all dampeners=1.0)...', flush=True)
    t0 = time.time()
    baseline = runner.evaluate([1.0]*5, lookback_days=1825)
    print(f'  baseline ready in {time.time()-t0:.1f}s', flush=True)
    print(f'  baseline metrics:')
    for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']:
        wr, n = extract_metric(baseline, b)
        wr30, _ = extract_metric(baseline, b, '30d')
        if wr is not None:
            print(f'    {b:<5} N={n:>6}  WR15={wr:.2f}%  WR30={wr30:.2f}%', flush=True)

    space = [
        Real(0.0, 1.5, name='d_mon'),
        Real(0.0, 1.5, name='d_tue'),
        Real(0.0, 1.5, name='d_wed'),
        Real(0.0, 1.5, name='d_thu'),
        Real(0.0, 1.5, name='d_fri'),
    ]

    eval_log = []
    iter_count = [0]

    def obj(coeffs):
        iter_count[0] += 1
        loss = loss_fn(coeffs, baseline, runner)
        eval_log.append({'iter': iter_count[0], 'coeffs': list(coeffs), 'loss': float(loss)})
        if iter_count[0] % 5 == 0 or iter_count[0] <= 3:
            print(f'  [{iter_count[0]:3d}] coeffs={[round(c,3) for c in coeffs]}  loss={loss:.4f}',
                  flush=True)
        return loss

    print(f'\n[{loop_id}] Running gp_minimize (n_calls=80)...', flush=True)
    t0 = time.time()
    result = gp_minimize(obj, space, n_calls=80, random_state=42,
                         acq_func='EI', verbose=False)
    print(f'  optimization done in {time.time()-t0:.1f}s', flush=True)

    best = list(result.x)
    print(f'\nBest coefficients (loss={result.fun:.4f}):')
    print(f'  Mon={best[0]:.3f}  Tue={best[1]:.3f}  Wed={best[2]:.3f}  Thu={best[3]:.3f}  Fri={best[4]:.3f}')

    # Detail on best variant
    print(f'\nBest variant detail vs baseline:')
    best_stats = runner.evaluate(best, lookback_days=1825)
    print(f'  {"bucket":<6} {"N_var":>7} {"N_base":>7} {"WR15_var":>9} {"WR15_base":>9} {"ΔWR15":>7}')
    for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']:
        wr_v, n_v = extract_metric(best_stats, b)
        wr_b, n_b = extract_metric(baseline, b)
        if wr_v is not None and wr_b is not None:
            print(f'  {b:<6} {n_v:>7} {n_b:>7}  {wr_v:>8.2f}%  {wr_b:>8.2f}%  {wr_v-wr_b:>+6.2f}pp', flush=True)

    # Stability metric on best variant
    print(f'\nStability metric (day-over-day |Δoverall|):')
    stab_base = runner.stability_metric([1.0]*5)
    stab_best = runner.stability_metric(best)
    print(f'  baseline: {stab_base:.3f}')
    print(f'  best:     {stab_best:.3f}  ({(stab_best - stab_base)/stab_base*100:+.1f}%)')

    out = {
        'version_id': runner.version_id,
        'lookback_days': 1825,
        'baseline': {b: {'wr15': extract_metric(baseline, b)[0],
                          'n': extract_metric(baseline, b)[1]}
                      for b in BUCKET_WEIGHTS},
        'best_coeffs': {'mon': best[0], 'tue': best[1], 'wed': best[2],
                         'thu': best[3], 'fri': best[4]},
        'best_loss': float(result.fun),
        'best_stats': {b: {'wr15': extract_metric(best_stats, b)[0],
                            'n': extract_metric(best_stats, b)[1]}
                        for b in BUCKET_WEIGHTS},
        'stability_baseline': stab_base,
        'stability_best': stab_best,
        'eval_log': eval_log,
    }
    RESULT_PATH.write_text(json.dumps(out, indent=2))
    print(f'\n[OK] saved {RESULT_PATH}', flush=True)
    return out


if __name__ == '__main__':
    sweep('main')
