"""Phase E: focused call-side refinement on the additive mechanism.

Per Phase F + 470-variant exploration: full v27 substitution can't beat v27 on
puts. So ship architecture is ADDITIVE — Ichimoku rides on top of v39 (with
v27 still active for puts). The call-side and put-side Ichimoku each capture
orthogonal alpha.

Phase E refines ONLY the call-side params to reduce H3 N-drop violations
while preserving H1 alpha. Put-side params locked at Phase C Rank #1 (the
proven +1.10pp <25 winner).

Key levers explored:
  LIFT_TARGET_CALL (extended to 50-72): higher target = smaller displacement.
    A 95 peak pulled to target=50 with full weakness drops by ~20 → out of 95+.
    Same peak pulled to target=68 drops by ~12 → stays in 85+.
  K_CALL (extended to 0.15-0.80): lower K = less aggressive lift.
  KIJ_SAT_CALL (extended to 4-20): higher SAT = ind_grad ramps slower,
    only fires fully on deeply bearish kijun.

Objective: composite that heavily penalizes ΔN > 10% on any call tier above
70+. We want H3 strict pass AND H1 strict pass.
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
N_VARIANTS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
RNG_SEED = 42

# Locked put-side params from Phase C Rank #1 (best additive put alpha)
LOCKED_PUT = {
    'GATE_PUT_LO':     10,
    'GATE_PUT_HI':     26,
    'K_PUT':           0.358,
    'KIJ_SAT_PUT':     10.335,
    'LIFT_TARGET_PUT': 35.196,
}

# Call-side ranges — extended for N preservation
PARAM_RANGES = {
    'GATE_CALL_LO':     (60.0, 78.0),    # narrowing helps focus dampener
    'GATE_CALL_HI':     (80.0, 95.0),
    'K_CALL':           (0.15, 0.80),    # lowered floor for milder lift
    'KIJ_SAT_CALL':     (4.0, 20.0),     # extended upper — slower ind_grad ramp
    'LIFT_TARGET_CALL': (50.0, 72.0),    # CRUCIAL — higher target = less displacement
}

CATEGORICAL = {
    'RAMP_SHAPE': ['log', 'linear'],
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
        v = dict(LOCKED_PUT)
        for j, key in enumerate(cont_keys):
            lo, hi = PARAM_RANGES[key]
            val = lo + (hi - lo) * H[i, j]
            v[key] = int(round(val)) if 'GATE' in key else round(val, 3)
        for key in cat_keys:
            v[key] = CATEGORICAL[key][rng.integers(0, len(CATEGORICAL[key]))]
        if v['GATE_CALL_LO'] >= v['GATE_CALL_HI']:
            v['GATE_CALL_HI'] = v['GATE_CALL_LO'] + 5
        variants.append(v)
    return variants


def apply_ichimoku(df: pl.DataFrame, p: dict, score_col: str = 'overall') -> pl.DataFrame:
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


def evaluate(df: pl.DataFrame, p: dict) -> dict:
    df_v = apply_ichimoku(df, p, score_col='overall')
    out = {}

    def wr_dn(mask_new, mask_base, col):
        new = df_v.filter(mask_new & pl.col(col).is_not_null())
        base = df_v.filter(mask_base & pl.col(col).is_not_null())
        if len(base) == 0:
            return float('nan'), 0, 0, float('nan'), 0
        wr_new = float(new[col].mean()) * 100 if len(new) else float('nan')
        wr_base = float(base[col].mean()) * 100
        dn = (len(new) / len(base) - 1) * 100
        return wr_new - wr_base, len(base), len(new), wr_base, dn

    # CALL all tiers
    for label, lo in [('70', 70), ('75', 75), ('80', 80), ('85', 85), ('90', 90), ('95', 95)]:
        dwr, n_b, n_n, wr_b, dn = wr_dn(
            pl.col('overall_new') >= lo, pl.col('overall') >= lo, 'opt_result_15'
        )
        out[f'call_{label}p_dwr'] = dwr
        out[f'call_{label}p_n_base'] = n_b
        out[f'call_{label}p_n_new'] = n_n
        out[f'call_{label}p_dn'] = dn

    # PUT tiers
    for label, hi in [('30', 30), ('25', 25), ('20', 20), ('15', 15), ('10', 10)]:
        dwr, n_b, n_n, wr_b, dn = wr_dn(
            pl.col('overall_new') <= hi, pl.col('overall') <= hi, 'put_result_15'
        )
        out[f'put_lt{label}_dwr'] = dwr
        out[f'put_lt{label}_dn'] = dn

    # H1 strict pass count (call tiers ≥+0.5pp)
    h1_call = sum(1 for k in ['95', '90', '85', '80', '75'] if out[f'call_{k}p_dwr'] >= 0.5)
    h1_call_regress = sum(1 for k in ['95', '90', '85', '80', '75'] if out[f'call_{k}p_dwr'] <= -1.0)
    out['h1_call_pass'] = h1_call >= 3 and h1_call_regress == 0
    out['h1_call_count'] = h1_call

    # H3 strict pass count (call tiers ΔN within ±15%)
    h3_call_violations = sum(
        1 for k in ['95', '90', '85', '80', '75', '70']
        if abs(out[f'call_{k}p_dn']) > 15
    )
    out['h3_call_pass'] = h3_call_violations == 0
    out['h3_call_violations'] = h3_call_violations

    # Affected tier metrics
    out['call_70p_dwr'] = out['call_70p_dwr']
    out['put_lt25_dwr'] = out['put_lt25_dwr']

    # Composite — heavily penalize N drop on call tiers
    composite = 0.0
    composite += out['call_70p_dwr'] * 1.5     # affected tier weight
    composite += out['put_lt25_dwr']

    # Strong penalty for any call tier ΔN exceeding 10% (stricter than H3 gate)
    for k in ['95', '90', '85', '80', '75', '70']:
        dn = out[f'call_{k}p_dn']
        if dn is not None and not math.isnan(dn):
            excess = max(0, abs(dn) - 10)
            composite -= (excess / 5) ** 2  # quadratic, 0 at ≤10%, -1 at 15%, -4 at 20%

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
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    print(f'[joined] {time.time()-t0:.0f}s', flush=True)

    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] {len(variants)} call-side variants (put-side locked at Phase C #1)',
          flush=True)
    print(f'  Locked PUT: GATE={LOCKED_PUT["GATE_PUT_LO"]}-{LOCKED_PUT["GATE_PUT_HI"]} '
          f'K={LOCKED_PUT["K_PUT"]:.2f} sat={LOCKED_PUT["KIJ_SAT_PUT"]:.1f} '
          f'target={LOCKED_PUT["LIFT_TARGET_PUT"]:.1f}', flush=True)

    results = []
    t0 = time.time()
    for i, p in enumerate(variants):
        m = evaluate(df, p)
        m.update(p)
        results.append(m)
        if (i + 1) % 25 == 0:
            print(f'[sweep] {i+1}/{len(variants)}  ({time.time()-t0:.0f}s)', flush=True)

    # Version-suffix output so we don't clobber prior runs against different
    # algorithm versions. Parse "calls_v{N}_..." from the input parquet name.
    version_tag = ''
    parquet_stem = Path(PARQUET).stem
    if parquet_stem.startswith('calls_v'):
        rest = parquet_stem.split('_', 2)
        if len(rest) >= 2:
            version_tag = '_' + rest[1]   # e.g. _v43

    res = pl.DataFrame(results)
    out_path = CACHE / f'phase_e_refinement{version_tag}_results.parquet'
    res.write_parquet(out_path)
    print(f'[done] → {out_path.name}  ({time.time()-t0:.0f}s)', flush=True)

    # ============================================================================
    # CONSTRAINED RANKING: H1+H3 both pass first, then by composite
    # ============================================================================
    res = res.with_columns(pl.col('both_h1_h3_call').cast(pl.Int8).alias('_pass_int'))

    print('\n' + '=' * 130)
    print('CONFIGS PASSING BOTH H1 STRICT (calls) AND H3 STRICT (call N ≤ ±15%)')
    print('=' * 130)
    passing = res.filter(pl.col('both_h1_h3_call'))
    print(f'  Passing variants: {len(passing)} of {len(res)}')

    if len(passing) > 0:
        print(f'\n  Top-10 of passing variants by composite:')
        top = passing.sort('composite', descending=True).head(10)
        print(
            f'\n  {"rk":<3s}{"shape":<8s}'
            f'{"GtCLo-Hi":<10s}{"K_C":<6s}{"SatC":<6s}{"TgtC":<6s}'
            f'{"call70 ΔWR/ΔN":<14s}{"call75 ΔWR/ΔN":<14s}{"call85 ΔWR/ΔN":<14s}'
            f'{"put25 ΔWR/ΔN":<14s}{"comp":<7s}'
        )
        print('-' * 130)
        for i, r in enumerate(top.iter_rows(named=True)):
            print(
                f'  {i+1:<3d}{r["RAMP_SHAPE"]:<8s}'
                f'{r["GATE_CALL_LO"]:>3d}-{r["GATE_CALL_HI"]:<5d} '
                f'{r["K_CALL"]:<5.2f} {r["KIJ_SAT_CALL"]:<5.1f} {r["LIFT_TARGET_CALL"]:<5.1f} '
                f'{r["call_70p_dwr"]:>+5.2f}/{r["call_70p_dn"]:>+5.1f}%  '
                f'{r["call_75p_dwr"]:>+5.2f}/{r["call_75p_dn"]:>+5.1f}%  '
                f'{r["call_85p_dwr"]:>+5.2f}/{r["call_85p_dn"]:>+5.1f}%  '
                f'{r["put_lt25_dwr"]:>+5.2f}/{r["put_lt25_dn"]:>+5.1f}%  '
                f'{r["composite"]:>+5.2f}'
            )

        # Detail on top-3 of passing variants
        print('\n' + '=' * 110)
        print('TOP-3 of passing variants — ALL TIERS')
        print('=' * 110)
        for i, r in enumerate(top.head(3).iter_rows(named=True)):
            print(f'\n--- Rank #{i+1} (passes both H1+H3 strict) ---')
            print(f'  RAMP={r["RAMP_SHAPE"]}  CALL: gate={r["GATE_CALL_LO"]}-{r["GATE_CALL_HI"]} '
                  f'K={r["K_CALL"]:.2f} sat={r["KIJ_SAT_CALL"]:.1f} target={r["LIFT_TARGET_CALL"]:.1f}')
            for k in ['95p', '90p', '85p', '80p', '75p', '70p']:
                print(f'  CALL {k:<4s}  ΔWR={r[f"call_{k}_dwr"]:+5.2f}pp  '
                      f'N: {r[f"call_{k}_n_base"]:>5,} → {r[f"call_{k}_n_new"]:>5,} '
                      f'(Δ{r[f"call_{k}_dn"]:+5.1f}%)')
            for k in ['lt30', 'lt25', 'lt20', 'lt15', 'lt10']:
                print(f'  PUT  {k:<4s} ΔWR={r[f"put_{k}_dwr"]:+5.2f}pp  ΔN={r[f"put_{k}_dn"]:+5.1f}%')
    else:
        # No variants pass both gates strictly — show top by composite anyway
        print('\n  ⚠ No variants pass H1+H3 strictly. Showing top-10 by composite:')
        top = res.sort('composite', descending=True).head(10)
        print(
            f'\n  {"rk":<3s}{"shape":<6s}'
            f'{"GtCLo-Hi":<10s}{"K_C":<6s}{"SatC":<6s}{"TgtC":<6s}'
            f'{"H1?":<4s}{"H3vio":<6s}'
            f'{"call70 ΔWR/ΔN":<14s}{"call85 ΔWR/ΔN":<14s}{"comp":<7s}'
        )
        for i, r in enumerate(top.iter_rows(named=True)):
            print(
                f'  {i+1:<3d}{r["RAMP_SHAPE"]:<6s}'
                f'{r["GATE_CALL_LO"]:>3d}-{r["GATE_CALL_HI"]:<5d} '
                f'{r["K_CALL"]:<5.2f} {r["KIJ_SAT_CALL"]:<5.1f} {r["LIFT_TARGET_CALL"]:<5.1f} '
                f'{"✓" if r["h1_call_pass"] else "✗":<4s}'
                f'{r["h3_call_violations"]:<6d}'
                f'{r["call_70p_dwr"]:>+5.2f}/{r["call_70p_dn"]:>+5.1f}%  '
                f'{r["call_85p_dwr"]:>+5.2f}/{r["call_85p_dn"]:>+5.1f}%  '
                f'{r["composite"]:>+5.2f}'
            )


if __name__ == '__main__':
    main()
