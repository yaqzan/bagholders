"""Phase D substitutive sweep: optimize Ichimoku to REPLACE v27, not add to it.

Phase C optimized "best Ichimoku addition on top of v39 (with v27 active)."
Phase F revealed v27 carries ~1.42pp put alpha that pure-Ichimoku-additive
captures only partially — leaving a -0.44pp regression in true substitution.

This sweep re-optimizes against the true substitution objective:
  - Apply Ichimoku to overall_pre_v27 (= overall - wcf_lift)
  - Compare WR15 to v39 production baseline (current `overall`)
  - Find Ichimoku params that REPLACE + IMPROVE v27's put alpha

Extended put parameter ranges to allow Ichimoku to fully capture v27's cohort:
  - LIFT_TARGET_PUT extended to (28, 50) — v27 lifts toward 50, Ichimoku might
    need similar reach in substitutive mode
  - K_PUT full range (0.3, 0.95)
  - GATE_PUT_HI extended slightly (24, 32)

Output: phase_d_sub_results.parquet + top-10 console table.
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
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d_min0.parquet'
N_VARIANTS = int(sys.argv[2]) if len(sys.argv) > 2 else 120
RNG_SEED = 42

# Extended ranges for substitutive mode
PARAM_RANGES = {
    'GATE_CALL_LO':     (60.0, 75.0),
    'GATE_CALL_HI':     (78.0, 92.0),
    'K_CALL':           (0.30, 0.95),
    'KIJ_SAT_CALL':     (3.0, 15.0),
    'LIFT_TARGET_CALL': (45.0, 60.0),
    # PUT ranges extended for substitution
    'GATE_PUT_LO':      (-20.0, 15.0),    # negative = no saturation in real range
    'GATE_PUT_HI':      (24.0, 32.0),     # extended upper
    'K_PUT':            (0.30, 0.95),     # full range
    'KIJ_SAT_PUT':      (3.0, 15.0),
    'LIFT_TARGET_PUT':  (28.0, 50.0),     # extended to v27's lift target
}

CATEGORICAL = {
    'RAMP_SHAPE': ['log', 'linear'],
}


def latin_hypercube(n: int, dims: int, rng: np.random.Generator) -> np.ndarray:
    cuts = np.linspace(0, 1, n + 1)
    u = rng.uniform(size=(n, dims))
    a = cuts[:n]
    b = cuts[1:]
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
    for i in range(n):
        v = {}
        for j, key in enumerate(cont_keys):
            lo, hi = PARAM_RANGES[key]
            v[key] = lo + (hi - lo) * H[i, j]
            if 'GATE' in key:
                v[key] = int(round(v[key]))
            else:
                v[key] = round(v[key], 3)
        for key in cat_keys:
            v[key] = CATEGORICAL[key][rng.integers(0, len(CATEGORICAL[key]))]
        # Enforce ordering
        if v['GATE_CALL_LO'] >= v['GATE_CALL_HI']:
            v['GATE_CALL_HI'] = v['GATE_CALL_LO'] + 5
        if v['GATE_PUT_LO'] >= v['GATE_PUT_HI'] - 5:
            v['GATE_PUT_LO'] = v['GATE_PUT_HI'] - 10
        variants.append(v)
    return variants


def apply_ichimoku(df: pl.DataFrame, p: dict, score_col: str) -> pl.DataFrame:
    """Apply log-magnifier dampener to df[score_col], output as 'overall_new'."""
    if p['RAMP_SHAPE'] == 'log':
        ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    call_sg = ramp(pl.col(score_col) - p['GATE_CALL_LO'],
                   p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    call_ig = ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_w  = call_sg * call_ig
    call_d  = -p['K_CALL'] * call_w * (pl.col(score_col) - p['LIFT_TARGET_CALL'])

    put_sg = ramp(p['GATE_PUT_HI'] - pl.col(score_col),
                  p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_w  = put_sg * put_ig
    put_d  = p['K_PUT'] * put_w * (p['LIFT_TARGET_PUT'] - pl.col(score_col))

    return df.with_columns(
        (pl.col(score_col) + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )


def evaluate_substitutive(df: pl.DataFrame, p: dict) -> dict:
    """Apply Ichimoku to pre-v27 score; compare to v39 production baseline."""
    df_v = apply_ichimoku(df, p, score_col='overall_pre_v27')

    out = {}

    def wr_n(mask, col, mask_base=None):
        """If mask_base given, baseline uses original `overall` mask."""
        new = df_v.filter(mask & pl.col(col).is_not_null())
        n_new = len(new)
        wr_new = float(new[col].mean()) * 100 if n_new > 0 else float('nan')
        if mask_base is not None:
            base = df_v.filter(mask_base & pl.col(col).is_not_null())
            n_base = len(base)
            wr_base = float(base[col].mean()) * 100 if n_base > 0 else float('nan')
            return wr_new, n_new, wr_base, n_base
        return wr_new, n_new

    # CALL tiers — compare new score to v39 production baseline
    for label, lo in [('70', 70), ('75', 75), ('80', 80), ('85', 85), ('90', 90), ('95', 95)]:
        wr_new, n_new, wr_base, n_base = wr_n(
            pl.col('overall_new') >= lo, 'opt_result_15',
            mask_base=pl.col('overall') >= lo,
        )
        out[f'call_{label}p_wr_base'] = wr_base
        out[f'call_{label}p_n_base'] = n_base
        out[f'call_{label}p_wr_new'] = wr_new
        out[f'call_{label}p_n_new'] = n_new
        out[f'call_{label}p_dwr'] = wr_new - wr_base
        out[f'call_{label}p_dn_pct'] = (n_new / n_base - 1) * 100 if n_base > 0 else float('nan')

    # PUT tiers
    for label, hi in [('30', 30), ('25', 25), ('20', 20), ('15', 15), ('10', 10), ('5', 5)]:
        wr_new, n_new, wr_base, n_base = wr_n(
            pl.col('overall_new') <= hi, 'put_result_15',
            mask_base=pl.col('overall') <= hi,
        )
        out[f'put_lt{label}_wr_base'] = wr_base
        out[f'put_lt{label}_n_base'] = n_base
        out[f'put_lt{label}_wr_new'] = wr_new
        out[f'put_lt{label}_n_new'] = n_new
        out[f'put_lt{label}_dwr'] = wr_new - wr_base
        out[f'put_lt{label}_dn_pct'] = (n_new / n_base - 1) * 100 if n_base > 0 else float('nan')

    # Composite score
    call_dwr = out['call_70p_dwr']
    put_dwr  = out['put_lt25_dwr']

    def n_penalty(dn_pct):
        if dn_pct is None or math.isnan(dn_pct):
            return 0.0
        excess = max(0.0, abs(dn_pct) - 15.0)
        return -(excess / 10.0) ** 2

    out['composite'] = (
        call_dwr + put_dwr
        + n_penalty(out['call_70p_dn_pct'])
        + n_penalty(out['put_lt25_dn_pct'])
    )

    out['put_h1_pass'] = put_dwr >= 0.3
    out['call_h1_pass'] = call_dwr >= 0.3
    out['both_pass'] = out['put_h1_pass'] and out['call_h1_pass']
    return out


def main():
    pq = CACHE / PARQUET
    df = pl.read_parquet(pq)
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())
    print(f'[load] parquet: {len(df):,} rows', flush=True)

    # Put barriers
    print('[load] put-side WR15...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    print(f'[joined] {time.time()-t0:.0f}s', flush=True)

    # wadj cache (has wcf_lift for v27 reversal)
    wadj_df = pl.read_parquet(CACHE / 'wadj_v39_1825d_min0.parquet')
    df = df.join(wadj_df, on=['symbol', 'date'], how='left')

    # Compute pre-v27 score
    df = df.with_columns(
        (pl.col('overall') - pl.col('wcf_lift').fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_pre_v27')
    )

    n_lift = df.filter(pl.col('wcf_lift').is_not_null() & (pl.col('wcf_lift') > 0)).height
    n_changed = df.filter(pl.col('overall') != pl.col('overall_pre_v27')).height
    print(f'[v27] {n_lift:,} peaks had wcf_lift > 0  |  '
          f'{n_changed:,} have score changed by reversal', flush=True)

    # Sweep
    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] {len(variants)} variants — substitutive mode', flush=True)

    results = []
    t0 = time.time()
    for i, p in enumerate(variants):
        m = evaluate_substitutive(df, p)
        m.update(p)
        results.append(m)
        if (i + 1) % 20 == 0:
            print(f'[sweep] {i+1}/{len(variants)}  ({time.time()-t0:.0f}s)', flush=True)

    res = pl.DataFrame(results)
    out_path = CACHE / 'phase_d_sub_results.parquet'
    res.write_parquet(out_path)
    print(f'[done] → {out_path.name}  ({time.time()-t0:.0f}s)', flush=True)

    # ============================================================================
    # TOP-10 by composite (sub mode)
    # ============================================================================
    print('\n' + '=' * 130)
    print('TOP-10 SUBSTITUTIVE configs (Ichimoku replaces v27 — comparison vs v39 production)')
    print('=' * 130)
    top = res.sort('composite', descending=True).head(10)
    print(
        f'\n{"rank":<5s}{"shape":<8s}'
        f'{"GtCLo-Hi":<10s}{"K_C":<6s}{"SatC":<6s}{"TgtC":<6s}'
        f'{"GtPLo-Hi":<11s}{"K_P":<6s}{"SatP":<6s}{"TgtP":<6s}'
        f'{"call70 ΔWR/ΔN":<14s}{"put25 ΔWR/ΔN":<14s}{"comp":<7s}{"H1":<3s}'
    )
    print('-' * 130)
    for i, r in enumerate(top.iter_rows(named=True)):
        h1 = '✓' if r['both_pass'] else ('+' if r['put_h1_pass'] else ('-' if r['call_h1_pass'] else ' '))
        print(
            f'{i+1:<5d}{r["RAMP_SHAPE"]:<8s}'
            f'{r["GATE_CALL_LO"]:>3d}-{r["GATE_CALL_HI"]:<5d} '
            f'{r["K_CALL"]:<5.2f} {r["KIJ_SAT_CALL"]:<5.1f} {r["LIFT_TARGET_CALL"]:<5.1f} '
            f'{r["GATE_PUT_LO"]:>+4d}-{r["GATE_PUT_HI"]:<5d} '
            f'{r["K_PUT"]:<5.2f} {r["KIJ_SAT_PUT"]:<5.1f} {r["LIFT_TARGET_PUT"]:<5.1f} '
            f'{r["call_70p_dwr"]:>+5.2f}/{r["call_70p_dn_pct"]:>+5.1f}%  '
            f'{r["put_lt25_dwr"]:>+5.2f}/{r["put_lt25_dn_pct"]:>+5.1f}%  '
            f'{r["composite"]:>+5.2f}  {h1:<3s}'
        )

    # Detail on top-3
    print('\n' + '=' * 110)
    print('TOP-3 DETAIL — substitutive mode, ALL TIERS')
    print('=' * 110)
    for i, r in enumerate(top.head(3).iter_rows(named=True)):
        print(f'\n--- Rank #{i+1} ---')
        print(f'  RAMP={r["RAMP_SHAPE"]}  CALL: gate={r["GATE_CALL_LO"]}-{r["GATE_CALL_HI"]} '
              f'K={r["K_CALL"]:.2f} sat={r["KIJ_SAT_CALL"]:.1f} target={r["LIFT_TARGET_CALL"]:.1f}')
        print(f'                       PUT:  gate={r["GATE_PUT_LO"]}-{r["GATE_PUT_HI"]} '
              f'K={r["K_PUT"]:.2f} sat={r["KIJ_SAT_PUT"]:.1f} target={r["LIFT_TARGET_PUT"]:.1f}')

        for tier in ['95p', '90p', '85p', '80p', '75p', '70p']:
            wr_b = r[f'call_{tier}_wr_base']; wr_n = r[f'call_{tier}_wr_new']
            n_b = r[f'call_{tier}_n_base']; n_n = r[f'call_{tier}_n_new']
            print(f'  CALL {tier:<4s}  WR15: {wr_b:5.2f}% → {wr_n:5.2f}% (Δ{wr_n-wr_b:+5.2f})  '
                  f'N: {n_b:>5,} → {n_n:>5,} ({(n_n/n_b-1)*100 if n_b else 0:+5.1f}%)')

        for tier in ['lt30', 'lt25', 'lt20', 'lt15', 'lt10', 'lt5']:
            wr_b = r[f'put_{tier}_wr_base']; wr_n = r[f'put_{tier}_wr_new']
            n_b = r[f'put_{tier}_n_base']; n_n = r[f'put_{tier}_n_new']
            print(f'  PUT  {tier:<4s} WR15: {wr_b:5.2f}% → {wr_n:5.2f}% (Δ{wr_n-wr_b:+5.2f})  '
                  f'N: {n_b:>5,} → {n_n:>5,} ({(n_n/n_b-1)*100 if n_b else 0:+5.1f}%)')


if __name__ == '__main__':
    main()
