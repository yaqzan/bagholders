"""Stage A: per-trade ICH retune sweep against v43 substrate (pre-ICH baseline).

v44 ICH was originally calibrated against v43 substrate which had contaminated
breadth driving an inflated regime multiplier. v45 fixed the contamination —
regime distribution is now tamer, so the same ICH dampener over-reaches: it
strips signals that no longer have inflated regime carrying them, hurting
compound while gaining DD.

This sweep widens the ICH param space (vs Phase H refinement) to find the
optimal dampener under the post-v45 substrate. The v43 parquet is used because
kijun_pct is price-based (substrate-independent) and pre-ICH `overall` is what
the dampener takes as input. Stage B will validate top candidates under full
N=300×8-window canonical MC against the LIVE v45 substrate.

Param space (8 axes, ~120 evals via Latin Hypercube):
  K_CALL          [0.15, 0.55]    -- v44=0.359
  K_CALL_POWER    [1.0, 4.5]      -- v44=2.68
  KIJ_SAT_CALL    [10, 28]        -- v44=18.4
  TARGET_CALL     [60, 70]        -- v44=63.8
  GATE_CALL_LO    [66, 74]        -- v44=69
  K_PUT           [0.10, 0.45]    -- v44=0.278
  KIJ_SAT_PUT     [5, 16]         -- v44=8.8
  TARGET_PUT      [30, 38]        -- v44=33.4

Gate: H1 strict (≥+0.3pp on ≥3 call tiers, none regress >-1.0pp) + H3
N-stability ±15% per primary tier. Top 5 candidates by composite metric →
Stage B canonical MC.

The composite metric is intentionally conservative on N-loss: each %-point of
N-drop above 10% costs (excess/5)^2 in composite score. v44 had ~14% drop on
85+/90+ which costs ~0.6 composite — that's the v44 op. point. We want
candidates that achieve comparable per-trade alpha at LOWER N drop (better
compound retention) OR higher per-trade alpha at SAME N drop (better DD).
"""
from __future__ import annotations

import io
import math
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / '.cache' / 'weekly_avwap' / 'calls_v43_1825d_min0.parquet'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'v44_v45_joint'
OUT_DIR.mkdir(parents=True, exist_ok=True)

N_VARIANTS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
RNG_SEED = 42

# Wider ranges than Phase H (recalibration, not refinement)
PARAM_RANGES = {
    'GATE_CALL_LO':     (66.0, 74.0),
    'GATE_CALL_HI':     (78.0, 92.0),
    'K_CALL_BASE':      (0.15, 0.55),
    'K_CALL_POWER':     (1.0, 4.5),
    'KIJ_SAT_CALL':     (10.0, 28.0),
    'LIFT_TARGET_CALL': (60.0, 70.0),
    'GATE_PUT_LO':      (8.0, 14.0),
    'GATE_PUT_HI':      (24.0, 28.0),
    'K_PUT':            (0.10, 0.45),
    'KIJ_SAT_PUT':      (5.0, 16.0),
    'LIFT_TARGET_PUT':  (30.0, 38.0),
}

CATEGORICAL = {
    'IND_RAMP_CALL': ['linear', 'log'],
    'IND_RAMP_PUT':  ['linear', 'log'],
}


def latin_hypercube(n: int, dims: int, rng: np.random.Generator) -> np.ndarray:
    cuts = np.linspace(0, 1, n + 1)
    u = rng.uniform(size=(n, dims))
    a = cuts[:n]; b = cuts[1:]
    rdpoints = u * (b - a)[:, None] + a[:, None]
    H = np.zeros_like(rdpoints)
    for d in range(dims):
        order = rng.permutation(n)
        H[:, d] = rdpoints[order, d]
    return H


def gen_variants(n: int, seed: int = RNG_SEED) -> list[dict]:
    rng = np.random.default_rng(seed)
    cont_keys = list(PARAM_RANGES.keys())
    cat_keys  = list(CATEGORICAL.keys())
    H = latin_hypercube(n, len(cont_keys), rng)
    variants = []

    # Seed: v44 ship config as anchor variant
    v44 = {
        'GATE_CALL_LO': 69, 'GATE_CALL_HI': 90,
        'K_CALL_BASE': 0.359, 'K_CALL_POWER': 2.68,
        'KIJ_SAT_CALL': 18.4, 'LIFT_TARGET_CALL': 63.8,
        'GATE_PUT_LO': 10, 'GATE_PUT_HI': 27,
        'K_PUT': 0.278, 'KIJ_SAT_PUT': 8.8, 'LIFT_TARGET_PUT': 33.4,
        'IND_RAMP_CALL': 'linear', 'IND_RAMP_PUT': 'log',
    }
    variants.append(v44)

    # Seed: zero-ICH (baseline check)
    v0 = dict(v44)
    v0.update({'K_CALL_BASE': 0.0, 'K_PUT': 0.0})
    variants.append(v0)

    # Seed: half-strength v44 (DD-leaning)
    v_half = dict(v44)
    v_half.update({'K_CALL_BASE': 0.18, 'K_PUT': 0.14})
    variants.append(v_half)

    for i in range(n - 3):
        v = {}
        for j, key in enumerate(cont_keys):
            lo, hi = PARAM_RANGES[key]
            val = lo + (hi - lo) * H[i, j]
            v[key] = int(round(val)) if 'GATE' in key else round(val, 3)
        for key in cat_keys:
            v[key] = CATEGORICAL[key][rng.integers(0, len(CATEGORICAL[key]))]
        if v['GATE_CALL_LO'] >= v['GATE_CALL_HI']:
            v['GATE_CALL_HI'] = v['GATE_CALL_LO'] + 8
        if v['GATE_PUT_LO'] >= v['GATE_PUT_HI'] - 5:
            v['GATE_PUT_LO'] = v['GATE_PUT_HI'] - 8
        variants.append(v)
    return variants


def apply_dampener(df: pl.DataFrame, p: dict) -> pl.DataFrame:
    """Phase H+ dampener — same shape as v44 production."""
    def _ramp(shape):
        if shape == 'log':
            return lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
        return lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    call_ramp = _ramp(p['IND_RAMP_CALL'])
    put_ramp_fn  = _ramp(p['IND_RAMP_PUT'])

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    score_range = max(1, p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    score_norm_c = ((pl.col('overall') - p['GATE_CALL_LO']) / score_range).clip(0.0, None)
    k_eff_c = p['K_CALL_BASE'] * score_norm_c.pow(p['K_CALL_POWER'])
    ind_grad_c = call_ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_d_full = -k_eff_c * ind_grad_c * (pl.col('overall') - p['LIFT_TARGET_CALL'])
    call_d = pl.when(pl.col('overall') >= p['GATE_CALL_LO']).then(call_d_full).otherwise(0.0)

    put_sg = put_ramp_fn(p['GATE_PUT_HI'] - pl.col('overall'),
                         p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = put_ramp_fn(ind_dist, p['KIJ_SAT_PUT'])
    put_d  = p['K_PUT'] * put_sg * put_ig * (p['LIFT_TARGET_PUT'] - pl.col('overall'))

    return df.with_columns(
        (pl.col('overall') + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )


def evaluate(df: pl.DataFrame, p: dict) -> dict:
    df_v = apply_dampener(df, p)
    out = {}

    def wr_dn(mask_new, mask_base, col):
        new = df_v.filter(mask_new & pl.col(col).is_not_null())
        base = df_v.filter(mask_base & pl.col(col).is_not_null())
        if len(base) == 0:
            return float('nan'), 0, 0, 0
        wr_new = float(new[col].mean()) * 100 if len(new) else float('nan')
        wr_base = float(base[col].mean()) * 100
        dn = (len(new) / len(base) - 1) * 100
        return wr_new - wr_base, len(base), len(new), dn

    for label, lo in [('70', 70), ('75', 75), ('80', 80), ('85', 85), ('90', 90), ('95', 95)]:
        dwr, n_b, n_n, dn = wr_dn(
            pl.col('overall_new') >= lo, pl.col('overall') >= lo, 'opt_result_15'
        )
        out[f'call_{label}p_dwr'] = dwr
        out[f'call_{label}p_n_base'] = n_b
        out[f'call_{label}p_n_new'] = n_n
        out[f'call_{label}p_dn'] = dn

    for label, hi in [('25', 25), ('20', 20), ('15', 15), ('10', 10)]:
        dwr, n_b, n_n, dn = wr_dn(
            pl.col('overall_new') <= hi, pl.col('overall') <= hi, 'put_result_15'
        )
        out[f'put_lt{label}_dwr'] = dwr
        out[f'put_lt{label}_dn'] = dn

    # H1 strict — affected-tier framework
    h1_call = sum(1 for k in ['95', '90', '85', '80', '75'] if out[f'call_{k}p_dwr'] >= 0.3)
    h1_call_regress = sum(1 for k in ['95', '90', '85', '80', '75'] if out[f'call_{k}p_dwr'] <= -1.0)
    out['h1_call_pass'] = h1_call >= 3 and h1_call_regress == 0
    out['h1_call_count'] = h1_call

    # H3 N-stability ±15%
    h3_violations = sum(
        1 for k in ['95', '90', '85', '80', '75', '70']
        if abs(out[f'call_{k}p_dn']) > 15
    )
    out['h3_call_pass'] = h3_violations == 0
    out['h3_call_violations'] = h3_violations

    # Composite — balances per-trade alpha against N retention (compound proxy)
    # v44 gets composite ~3.5 on this metric; we want >3.5 with smaller N-drop.
    composite = 0.0
    composite += out['call_95p_dwr'] * 0.6
    composite += out['call_90p_dwr'] * 0.8
    composite += out['call_85p_dwr'] * 1.0
    composite += out['call_80p_dwr'] * 1.0
    composite += out['call_75p_dwr'] * 0.5
    composite += out['call_70p_dwr'] * 0.3
    composite += out['put_lt25_dwr'] * 0.6
    composite += out['put_lt15_dwr'] * 0.3

    # N-loss penalty — quadratic above 10%, applied to each call tier
    for k in ['95', '90', '85', '80', '75']:
        dn = out[f'call_{k}p_dn']
        if dn is not None and not math.isnan(dn):
            excess = max(0, abs(dn) - 10)
            composite -= (excess / 5) ** 2

    out['composite'] = composite
    out['both_h1_h3_call'] = out['h1_call_pass'] and out['h3_call_pass']
    return out


def main():
    df = pl.read_parquet(PARQUET)
    df = df.filter(
        pl.col('price_vs_kijun_pct').is_not_null() &
        pl.col('price_vs_kijun_pct').is_finite()
    )
    print(f'[load] parquet: {len(df):,} rows', flush=True)

    print('[load] put-side WR15...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            "SELECT symbol, date, result AS put_result_15 "
            "FROM barrier_outcomes WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    print(f'[joined] {time.time()-t0:.0f}s', flush=True)

    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] {len(variants)} variants — Stage A', flush=True)

    results = []
    t0 = time.time()
    for i, p in enumerate(variants):
        m = evaluate(df, p)
        m.update(p)
        m['_seed_label'] = (
            'v44_anchor' if i == 0 else
            'zero_ich' if i == 1 else
            'half_v44' if i == 2 else ''
        )
        results.append(m)
        if (i + 1) % 25 == 0:
            print(f'[sweep] {i+1}/{len(variants)}  ({time.time()-t0:.0f}s)', flush=True)

    res = pl.DataFrame(results)
    out_path = OUT_DIR / 'stage_a_results.parquet'
    res.write_parquet(out_path)
    print(f'[done] → {out_path.name}  ({time.time()-t0:.0f}s)', flush=True)

    print('\n' + '=' * 145)
    print('STAGE A — TOP CONFIGS PASSING H1+H3 STRICT')
    print('=' * 145)
    passing = res.filter(pl.col('both_h1_h3_call')).sort('composite', descending=True)
    print(f'  Passing: {len(passing)} of {len(res)}')

    cols = ['composite', '_seed_label',
            'call_95p_dwr', 'call_90p_dwr', 'call_85p_dwr', 'call_80p_dwr', 'call_75p_dwr',
            'call_85p_dn', 'call_80p_dn', 'call_75p_dn',
            'put_lt25_dwr',
            'GATE_CALL_LO', 'K_CALL_BASE', 'K_CALL_POWER', 'KIJ_SAT_CALL', 'LIFT_TARGET_CALL',
            'K_PUT', 'KIJ_SAT_PUT', 'LIFT_TARGET_PUT',
            'IND_RAMP_CALL', 'IND_RAMP_PUT']

    print('\nTop 15:')
    print(passing.head(15).select(cols))

    print('\n--- Anchors (v44/zero/half) ---')
    anchors = res.filter(pl.col('_seed_label') != '').select(cols)
    print(anchors)


if __name__ == '__main__':
    main()
