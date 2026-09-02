"""Phase D — Bayesian-style random drill around Phase C winner.

Two stages:
  D1 — wide drill: 300 random samples in basin ±25% of Phase C variant 41 params
  D2 — tight drill: 300 random samples in basin ±10% of D1 winner
"""
from __future__ import annotations
import io, sys, json, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.sector_etf_alpha.swpm import SWPMParams, evaluate

LOCAL = ROOT / '.cache' / 'sector_etf_alpha'
COH_PATH = LOCAL / 'cohort_v46_1825.parquet'
OUT_DIR = ROOT / 'experiments' / 'sector_etf_alpha'

# Phase C variant 41 — drill anchor
ANCHOR = {
    'EMA_K': 8.65,
    'RSI_K': 25.86,
    'RSI_W': 0.40,
    'PHASE_POWER': 1.97,
    'SCORE_POWER': 1.80,
    'ALPHA_DOWN': 0.44,
    'TARGET_DOWN': 68.07,
    'ALPHA_UP': 0.86,
    'TARGET_UP': 91.72,
    'ALPHA_PUT_DOWN': 0.48,
    'TARGET_PUT': 32.41,
    'ALPHA_PUT_UP': 0.12,
    'TARGET_PUT_UP': 15.70,
}

# Hard bounds (anchored on global SPACE)
HARD_BOUNDS = {
    'EMA_K': (3.0, 12.0),
    'RSI_K': (15.0, 35.0),
    'RSI_W': (0.0, 1.0),
    'PHASE_POWER': (1.0, 3.0),
    'SCORE_POWER': (1.0, 2.5),
    'ALPHA_DOWN': (0.30, 1.00),
    'TARGET_DOWN': (55.0, 70.0),
    'ALPHA_UP': (0.30, 1.00),
    'TARGET_UP': (80.0, 92.0),
    'ALPHA_PUT_DOWN': (0.30, 1.00),
    'TARGET_PUT': (28.0, 38.0),
    'ALPHA_PUT_UP': (0.0, 0.6),
    'TARGET_PUT_UP': (8.0, 16.0),
}


def random_basin(anchor: dict, frac: float, n: int, seed: int) -> list[dict]:
    """n random samples within ±frac of anchor, clipped to hard bounds."""
    rng = np.random.default_rng(seed)
    samples = []
    for i in range(n):
        s = {}
        for k, v in anchor.items():
            lo_h, hi_h = HARD_BOUNDS[k]
            span_anchor = max(0.5, abs(v) * frac, (hi_h - lo_h) * frac * 0.5)
            lo = max(lo_h, v - span_anchor)
            hi = min(hi_h, v + span_anchor)
            s[k] = float(rng.uniform(lo, hi))
        samples.append(s)
    return samples


def run_drill(samples: list[dict], coh: pl.DataFrame, label: str) -> list[dict]:
    results = []
    t0 = time.time()
    for i, kw in enumerate(samples):
        kw_full = dict(kw)
        kw_full['GATE_LO_C'] = 70
        kw_full['GATE_HI_C'] = 84
        kw_full['GATE_LO_P'] = 16
        kw_full['GATE_HI_P'] = 25
        params = SWPMParams(**kw_full)
        m = evaluate(coh, params)
        row = {'variant_id': i, **kw}
        row['util'] = m['util']
        row['W4_breaches'] = m['W4_breaches']
        row['W5_breaches'] = m['W5_breaches']
        row['W6_breaches'] = m['W6_breaches']
        row['n_changed'] = m['n_changed']
        for t in m['tiers_call']:
            row[f'call_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            row[f'call_{t["tier"]}_dWR1'] = t['wr1_delta_pp']
            row[f'call_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            row[f'call_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
            row[f'call_{t["tier"]}_dN%'] = t['n_delta_pct']
            row[f'call_{t["tier"]}_n_new'] = t['n_new']
        for t in m['tiers_put']:
            row[f'put_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            row[f'put_{t["tier"]}_dWR1'] = t['wr1_delta_pp']
            row[f'put_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            row[f'put_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
            row[f'put_{t["tier"]}_dN%'] = t['n_delta_pct']
            row[f'put_{t["tier"]}_n_new'] = t['n_new']
        results.append(row)
        if (i+1) % 50 == 0:
            print(f'[{label}] {i+1}/{len(samples)}  elapsed={time.time()-t0:.0f}s', flush=True)
    print(f'[{label}] done in {time.time()-t0:.1f}s', flush=True)
    return results


def main():
    coh = pl.read_parquet(COH_PATH)
    print(f'[load] cohort: {len(coh):,} rows', flush=True)

    # D1 — wide drill
    print('\n[D1] wide drill ±25% of Phase C variant 41 anchor (n=300)')
    d1_samples = random_basin(ANCHOR, frac=0.25, n=300, seed=101)
    d1_results = run_drill(d1_samples, coh, 'D1')
    df1 = pl.DataFrame(d1_results).sort('util', descending=True)
    df1.write_csv(OUT_DIR / 'phase_d1_results.csv')

    print('\n=== D1 TOP 10 ===')
    cols = ['variant_id', 'util', 'W4_breaches', 'call_75+_dWR7', 'call_80+_dWR7', 'call_85+_dWR7',
            'call_90+_dWR7', 'put_<=15_dWR7', 'put_<=20_dWR7']
    print(df1.select([c for c in cols if c in df1.columns]).head(10))

    # D2 — tight drill around D1 winner
    d1_top = df1.head(1).to_dicts()[0]
    new_anchor = {k: d1_top[k] for k in ANCHOR.keys()}
    print(f'\n[D2] tight drill ±10% of D1 winner (n=300)')
    print(f'   anchor: {json.dumps(new_anchor, indent=2)}')
    d2_samples = random_basin(new_anchor, frac=0.12, n=300, seed=202)
    d2_results = run_drill(d2_samples, coh, 'D2')
    df2 = pl.DataFrame(d2_results).sort('util', descending=True)
    df2.write_csv(OUT_DIR / 'phase_d2_results.csv')

    print('\n=== D2 TOP 10 ===')
    print(df2.select([c for c in cols if c in df2.columns]).head(10))

    # Save anchor history
    final = df2.head(1).to_dicts()[0]
    final_anchor = {k: final[k] for k in ANCHOR.keys()}
    with open(OUT_DIR / 'phase_d_winner.json', 'w') as f:
        json.dump({
            'phase_c_anchor': ANCHOR,
            'd1_winner': new_anchor,
            'd1_winner_util': d1_top['util'],
            'd2_winner': final_anchor,
            'd2_winner_util': final['util'],
        }, f, indent=2)
    print(f'\n[save] phase_d_winner.json')
    print(f'\n=== Phase D winner util={final["util"]:+.3f} ===')
    for k, v in final_anchor.items():
        print(f'  {k}={v:.3f}')


if __name__ == '__main__':
    main()
