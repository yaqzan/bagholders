"""Phase H: focused refinement of Phase G — push 80+/85+ alpha while preserving 95+.

Phase G's top 18 configs all hit 95+ ΔWR=+5.77pp (capped by small N=26 cohort
composition) but showed 0.5-1.5pp variance on 80+/85+. This sweep probes:

  - Tighter ranges around BOTH Phase G basins (Rank 1: gate=68-85; Rank 7: 70-80)
  - K_CALL_POWER extended to 5.0 (Phase G capped at 3.2)
  - Put-side allowed to vary modestly around Phase C Rank #1 winning values
  - Composite metric weights 80+/85+ alpha more heavily

Expected upside: +0.5-1.5pp on 80+/85+ relative to Phase G Rank #1.
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

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v43_1825d_min0.parquet'
N_VARIANTS = int(sys.argv[2]) if len(sys.argv) > 2 else 250
RNG_SEED = 42

# Tightened ranges around Phase G winners (Rank 1 and Rank 7 basins)
PARAM_RANGES = {
    'GATE_CALL_LO':     (64.0, 76.0),    # tightened around Phase G basins
    'GATE_CALL_HI':     (78.0, 92.0),
    'K_CALL_BASE':      (0.05, 0.50),    # widened lower (high POWER amplifies)
    'K_CALL_POWER':     (0.8, 5.0),      # extended upper bound
    'KIJ_SAT_CALL':     (5.0, 22.0),
    'LIFT_TARGET_CALL': (55.0, 72.0),    # narrower than Phase G
    # PUT — allowed to vary, but Phase C Rank #1 is the proven winner
    'GATE_PUT_LO':      (5.0, 15.0),
    'GATE_PUT_HI':      (24.0, 28.0),
    'K_PUT':            (0.25, 0.50),
    'KIJ_SAT_PUT':      (7.0, 14.0),
    'LIFT_TARGET_PUT':  (32.0, 40.0),
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
    for i in range(n):
        v = {}
        for j, key in enumerate(cont_keys):
            lo, hi = PARAM_RANGES[key]
            val = lo + (hi - lo) * H[i, j]
            v[key] = int(round(val)) if 'GATE' in key else round(val, 3)
        for key in cat_keys:
            v[key] = CATEGORICAL[key][rng.integers(0, len(CATEGORICAL[key]))]
        if v['GATE_CALL_LO'] >= v['GATE_CALL_HI']:
            v['GATE_CALL_HI'] = v['GATE_CALL_LO'] + 5
        if v['GATE_PUT_LO'] >= v['GATE_PUT_HI'] - 5:
            v['GATE_PUT_LO'] = v['GATE_PUT_HI'] - 8
        variants.append(v)
    return variants


def apply_dampener(df: pl.DataFrame, p: dict) -> pl.DataFrame:
    """Phase G+ dampener with separate ramp shapes for call and put indicators."""
    def _ramp(shape):
        if shape == 'log':
            return lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
        return lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    call_ramp = _ramp(p['IND_RAMP_CALL'])
    put_ramp_fn  = _ramp(p['IND_RAMP_PUT'])

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    # CALL — asymmetric K (power-law on score_norm)
    score_range = max(1, p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    score_norm_c = ((pl.col('overall') - p['GATE_CALL_LO']) / score_range).clip(0.0, None)
    k_eff_c = p['K_CALL_BASE'] * score_norm_c.pow(p['K_CALL_POWER'])
    ind_grad_c = call_ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_d_full = -k_eff_c * ind_grad_c * (pl.col('overall') - p['LIFT_TARGET_CALL'])
    call_d = pl.when(pl.col('overall') >= p['GATE_CALL_LO']).then(call_d_full).otherwise(0.0)

    # PUT — symmetric ramp (no power-law); ramp shape configurable
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

    for label, hi in [('30', 30), ('25', 25), ('20', 20), ('15', 15), ('10', 10)]:
        dwr, n_b, n_n, dn = wr_dn(
            pl.col('overall_new') <= hi, pl.col('overall') <= hi, 'put_result_15'
        )
        out[f'put_lt{label}_dwr'] = dwr
        out[f'put_lt{label}_dn'] = dn

    h1_call = sum(1 for k in ['95', '90', '85', '80', '75'] if out[f'call_{k}p_dwr'] >= 0.5)
    h1_call_regress = sum(1 for k in ['95', '90', '85', '80', '75'] if out[f'call_{k}p_dwr'] <= -1.0)
    out['h1_call_pass'] = h1_call >= 3 and h1_call_regress == 0
    out['h1_call_count'] = h1_call

    h3_violations = sum(
        1 for k in ['95', '90', '85', '80', '75', '70']
        if abs(out[f'call_{k}p_dn']) > 15
    )
    out['h3_call_pass'] = h3_violations == 0
    out['h3_call_violations'] = h3_violations

    # Composite — weighted toward multi-tier alpha (Phase H goal)
    composite = 0.0
    composite += out['call_70p_dwr'] * 0.5
    composite += out['call_75p_dwr'] * 0.5
    composite += out['call_80p_dwr'] * 1.0     # heavier weight on 80+
    composite += out['call_85p_dwr'] * 1.0     # heavier weight on 85+
    composite += out['call_90p_dwr'] * 0.5
    composite += out['call_95p_dwr'] * 0.3     # already capped, less weight
    composite += out['put_lt25_dwr'] * 0.7
    for k in ['95', '90', '85', '80', '75', '70']:
        dn = out[f'call_{k}p_dn']
        if dn is not None and not math.isnan(dn):
            excess = max(0, abs(dn) - 10)
            composite -= (excess / 5) ** 2
    out['composite'] = composite
    out['both_h1_h3_call'] = out['h1_call_pass'] and out['h3_call_pass']
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

    print('[load] put-side WR15...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    print(f'[joined] {time.time()-t0:.0f}s', flush=True)

    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] {len(variants)} variants — Phase H refinement', flush=True)

    results = []
    t0 = time.time()
    for i, p in enumerate(variants):
        m = evaluate(df, p)
        m.update(p)
        results.append(m)
        if (i + 1) % 25 == 0:
            print(f'[sweep] {i+1}/{len(variants)}  ({time.time()-t0:.0f}s)', flush=True)

    res = pl.DataFrame(results)
    parquet_stem = Path(PARQUET).stem
    version_tag = ''
    if parquet_stem.startswith('calls_v'):
        rest = parquet_stem.split('_', 2)
        if len(rest) >= 2:
            version_tag = '_' + rest[1]
    out_path = CACHE / f'phase_h_refine{version_tag}_results.parquet'
    res.write_parquet(out_path)
    print(f'[done] → {out_path.name}  ({time.time()-t0:.0f}s)', flush=True)

    print('\n' + '=' * 145)
    print('PHASE H — TOP CONFIGS PASSING H1 STRICT + H3 STRICT')
    print('=' * 145)
    passing = res.filter(pl.col('both_h1_h3_call')).sort('composite', descending=True)
    print(f'  Passing: {len(passing)} of {len(res)}')

    if len(passing) > 0:
        print(
            f'\n  {"rk":<3s}{"call/put ramp":<13s}'
            f'{"GtCLo-Hi":<10s}{"K_BASE":<8s}{"POWER":<7s}{"SatC":<6s}{"TgtC":<6s}'
            f'{"call70 ΔWR/ΔN":<14s}{"call80 ΔWR/ΔN":<14s}{"call85 ΔWR/ΔN":<14s}'
            f'{"call95 ΔWR/ΔN":<14s}{"put25 ΔWR/ΔN":<14s}{"comp":<7s}'
        )
        print('-' * 165)
        for i, r in enumerate(passing.head(15).iter_rows(named=True)):
            print(
                f'  {i+1:<3d}{r["IND_RAMP_CALL"][:3]+"/"+r["IND_RAMP_PUT"][:3]:<13s}'
                f'{r["GATE_CALL_LO"]:>3d}-{r["GATE_CALL_HI"]:<5d} '
                f'{r["K_CALL_BASE"]:<6.3f}  {r["K_CALL_POWER"]:<5.2f}  '
                f'{r["KIJ_SAT_CALL"]:<5.1f} {r["LIFT_TARGET_CALL"]:<5.1f} '
                f'{r["call_70p_dwr"]:>+5.2f}/{r["call_70p_dn"]:>+5.1f}%  '
                f'{r["call_80p_dwr"]:>+5.2f}/{r["call_80p_dn"]:>+5.1f}%  '
                f'{r["call_85p_dwr"]:>+5.2f}/{r["call_85p_dn"]:>+5.1f}%  '
                f'{r["call_95p_dwr"]:>+5.2f}/{r["call_95p_dn"]:>+5.1f}%  '
                f'{r["put_lt25_dwr"]:>+5.2f}/{r["put_lt25_dn"]:>+5.1f}%  '
                f'{r["composite"]:>+5.2f}'
            )

        print('\n' + '=' * 110)
        print('TOP-3 DETAIL — all tiers')
        print('=' * 110)
        for i, r in enumerate(passing.head(3).iter_rows(named=True)):
            print(f'\n--- Rank #{i+1} ---')
            print(f'  CALL_RAMP={r["IND_RAMP_CALL"]} PUT_RAMP={r["IND_RAMP_PUT"]}')
            print(f'  CALL: gate={r["GATE_CALL_LO"]}-{r["GATE_CALL_HI"]} '
                  f'K_BASE={r["K_CALL_BASE"]:.3f} POWER={r["K_CALL_POWER"]:.2f} '
                  f'sat={r["KIJ_SAT_CALL"]:.1f} target={r["LIFT_TARGET_CALL"]:.1f}')
            print(f'  PUT:  gate={r["GATE_PUT_LO"]}-{r["GATE_PUT_HI"]} '
                  f'K={r["K_PUT"]:.3f} sat={r["KIJ_SAT_PUT"]:.1f} target={r["LIFT_TARGET_PUT"]:.1f}')
            for k in ['95p', '90p', '85p', '80p', '75p', '70p']:
                print(f'  CALL {k:<4s}  ΔWR={r[f"call_{k}_dwr"]:+5.2f}pp  '
                      f'N: {r[f"call_{k}_n_base"]:>5,} → {r[f"call_{k}_n_new"]:>5,} '
                      f'(Δ{r[f"call_{k}_dn"]:+5.1f}%)')
            for k in ['lt30', 'lt25', 'lt20', 'lt15', 'lt10']:
                print(f'  PUT  {k:<4s} ΔWR={r[f"put_{k}_dwr"]:+5.2f}pp  ΔN={r[f"put_{k}_dn"]:+5.1f}%')


if __name__ == '__main__':
    main()
