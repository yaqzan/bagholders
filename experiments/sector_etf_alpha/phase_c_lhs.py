"""Phase C — Stage 1 LHS blast radius for SWPM.

100 LHS variants over 8-dim parameter space. Each variant takes ~0.5 sec on
the 33k-row cohort. Total ~1-2 min.

Output:
  - phase_c_results.jsonl   per-variant metrics
  - phase_c_top.csv         top 25 by utility for Phase D drill
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

# Search space (LHS blast radius)
SPACE = {
    'EMA_K':           (3.0, 12.0),
    'RSI_K':           (15.0, 35.0),
    'RSI_W':           (0.0, 1.0),
    'PHASE_POWER':     (1.0, 3.0),
    'SCORE_POWER':     (1.0, 2.5),
    'ALPHA_DOWN':      (0.30, 1.00),
    'TARGET_DOWN':     (55.0, 70.0),
    'ALPHA_UP':        (0.30, 1.00),
    'TARGET_UP':       (80.0, 92.0),
    'ALPHA_PUT_DOWN':  (0.30, 1.00),
    'TARGET_PUT':      (28.0, 38.0),
    'ALPHA_PUT_UP':    (0.0, 0.6),
    'TARGET_PUT_UP':   (8.0, 16.0),
}

N_VARIANTS = 120


def lhs_sample(n_samples: int, n_dims: int, seed: int = 42) -> np.ndarray:
    """Latin Hypercube Sampling on [0,1]^n_dims."""
    rng = np.random.default_rng(seed)
    out = np.zeros((n_samples, n_dims))
    for j in range(n_dims):
        # Uniform within each stratum, then random permutation
        cuts = (np.arange(n_samples) + rng.uniform(0, 1, n_samples)) / n_samples
        rng.shuffle(cuts)
        out[:, j] = cuts
    return out


def sample_to_params(sample_row: np.ndarray, dim_names: list[str]) -> dict:
    out = {}
    for v, name in zip(sample_row, dim_names):
        lo, hi = SPACE[name]
        out[name] = lo + v * (hi - lo)
    return out


def main():
    coh = pl.read_parquet(COH_PATH)
    print(f'[load] cohort: {len(coh):,} rows', flush=True)

    dim_names = list(SPACE.keys())
    samples = lhs_sample(N_VARIANTS, len(dim_names), seed=42)

    results = []
    t0 = time.time()
    for i, row in enumerate(samples):
        kw = sample_to_params(row, dim_names)
        # Cast types
        kw_clean = {}
        for k, v in kw.items():
            if k.startswith('GATE') or 'TARGET' in k:
                kw_clean[k] = float(v)
            else:
                kw_clean[k] = float(v)
        kw_clean['GATE_LO_C'] = 70
        kw_clean['GATE_HI_C'] = 84
        kw_clean['GATE_LO_P'] = 16
        kw_clean['GATE_HI_P'] = 25
        params = SWPMParams(**kw_clean)
        m = evaluate(coh, params)

        # Compact row for ranking
        row_out = {
            'variant_id': i,
            **{k: kw_clean[k] for k in dim_names},
            'util': m['util'],
            'W4_breaches': m['W4_breaches'],
            'W5_breaches': m['W5_breaches'],
            'W6_breaches': m['W6_breaches'],
            'sec_phase_coverage': m['sec_phase_coverage'],
            'n_changed': m['n_changed'],
            'affected_n': m['affected_n'],
        }
        for t in m['tiers_call']:
            row_out[f'call_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            row_out[f'call_{t["tier"]}_dN%'] = t['n_delta_pct']
            row_out[f'call_{t["tier"]}_n_new'] = t['n_new']
            row_out[f'call_{t["tier"]}_dWR1'] = t['wr1_delta_pp']
            row_out[f'call_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            row_out[f'call_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
        for t in m['tiers_put']:
            row_out[f'put_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            row_out[f'put_{t["tier"]}_dN%'] = t['n_delta_pct']
            row_out[f'put_{t["tier"]}_n_new'] = t['n_new']
            row_out[f'put_{t["tier"]}_dWR1'] = t['wr1_delta_pp']
            row_out[f'put_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            row_out[f'put_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
        results.append(row_out)

        if (i+1) % 20 == 0:
            print(f'[lhs] {i+1}/{N_VARIANTS}  elapsed={time.time()-t0:.0f}s', flush=True)

    print(f'[lhs] done in {time.time()-t0:.1f}s', flush=True)

    # Save full
    out_jsonl = OUT_DIR / 'phase_c_results.jsonl'
    with open(out_jsonl, 'w') as f:
        for r in results:
            f.write(json.dumps(r) + '\n')
    print(f'[save] {out_jsonl}', flush=True)

    # Top 25 by util
    df = pl.DataFrame(results).sort('util', descending=True)
    df.head(25).write_csv(OUT_DIR / 'phase_c_top.csv')
    print(f'[save] phase_c_top.csv')

    print(f'\n=== TOP 15 by utility ===')
    cols_show = ['variant_id', 'util', 'W4_breaches', 'W5_breaches', 'W6_breaches',
                 'call_75+_dWR7', 'call_80+_dWR7', 'call_85+_dWR7', 'call_90+_dWR7', 'call_95+_dWR7',
                 'put_<=15_dWR7', 'put_<=25_dWR7',
                 'EMA_K', 'RSI_K', 'RSI_W', 'PHASE_POWER', 'SCORE_POWER',
                 'ALPHA_DOWN', 'TARGET_DOWN', 'ALPHA_UP', 'TARGET_UP',
                 'ALPHA_PUT_DOWN', 'TARGET_PUT', 'ALPHA_PUT_UP']
    available = [c for c in cols_show if c in df.columns]
    top = df.select(available).head(15)
    print(top)


if __name__ == '__main__':
    main()
