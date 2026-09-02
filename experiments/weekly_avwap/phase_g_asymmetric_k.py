"""Phase G: asymmetric-K-across-score-range sweep.

Hypothesis: a uniform K_CALL is sub-optimal. Cohort analysis showed Ichimoku
spread of +18.96pp at 85+ but only +7.87pp at 75+ — signal scales non-linearly
with score. A power-law K that's stronger at top tiers should catch more 95+
bad peaks while preserving 85-89 N.

Mechanism extension over Phase E:

  score_norm = (overall - GATE_CALL_LO) / (GATE_CALL_HI - GATE_CALL_LO)
              # NO clipping at upper bound — continues increasing past 1.0
  K_eff      = K_CALL_BASE * (score_norm ** K_CALL_POWER)
  ind_grad   = ramp(max(0, -kijun_pct), KIJ_SAT_CALL)
  overall   -= K_eff * ind_grad * (overall - LIFT_TARGET_CALL)

Note: this REPLACES the score_grad term. The shape function (linear/log) for
score_grad is collapsed into the power exponent. Indicator gradient still uses
RAMP_SHAPE.

When K_CALL_POWER=1.0: linear ramp (matches Phase E linear)
When K_CALL_POWER=2.0: 95+ gets 78% more strength than Phase E uniform K
When K_CALL_POWER=3.0: very concentrated at top — risk of overshoot

Locks put-side at Phase C Rank #1 (proven +1.10pp <25 winner).
Output: phase_g_asymmetric_v43_results.parquet.
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
N_VARIANTS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
RNG_SEED = 42

LOCKED_PUT = {
    'GATE_PUT_LO':     10,
    'GATE_PUT_HI':     26,
    'K_PUT':           0.358,
    'KIJ_SAT_PUT':     10.335,
    'LIFT_TARGET_PUT': 35.196,
}

PARAM_RANGES = {
    'GATE_CALL_LO':     (60.0, 78.0),
    'GATE_CALL_HI':     (78.0, 95.0),
    'K_CALL_BASE':      (0.05, 0.40),    # base lower than Phase E since power amplifies
    'K_CALL_POWER':     (0.7, 3.2),      # 1.0 = Phase E linear; >1 concentrates at top
    'KIJ_SAT_CALL':     (4.0, 20.0),
    'LIFT_TARGET_CALL': (50.0, 72.0),
}

CATEGORICAL = {
    'IND_RAMP_SHAPE': ['linear', 'log'],   # for indicator only; score uses power-law
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


def apply_dampener(df: pl.DataFrame, p: dict) -> pl.DataFrame:
    """Asymmetric-K dampener: score component uses power-law on score_norm
    (NO upper clip, so 95+ gets stronger lift than 90+)."""
    if p['IND_RAMP_SHAPE'] == 'log':
        ind_ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ind_ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    # CALL — power-law K_eff over score_norm; no upper clip on score_norm so K_eff
    # continues increasing past GATE_CALL_HI. Lower clip at 0 (don't fire below LO).
    score_range_call = max(1, p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    score_norm_c = ((pl.col('overall') - p['GATE_CALL_LO']) / score_range_call).clip(0.0, None)
    # score_norm_c ** power — but Polars Expression doesn't support **, use pow
    k_eff_c = p['K_CALL_BASE'] * score_norm_c.pow(p['K_CALL_POWER'])
    ind_grad_c = ind_ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_d = -k_eff_c * ind_grad_c * (pl.col('overall') - p['LIFT_TARGET_CALL'])
    # Only fire when call would actually dampen (score >= GATE_LO)
    call_d = pl.when(pl.col('overall') >= p['GATE_CALL_LO']).then(call_d).otherwise(0.0)

    # PUT (locked, log ramp) — same as Phase E
    put_ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    put_sg = put_ramp(p['GATE_PUT_HI'] - pl.col('overall'),
                      p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = put_ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_w  = put_sg * put_ig
    put_d  = p['K_PUT'] * put_w * (p['LIFT_TARGET_PUT'] - pl.col('overall'))

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
            return float('nan'), 0, 0, float('nan'), 0
        wr_new = float(new[col].mean()) * 100 if len(new) else float('nan')
        wr_base = float(base[col].mean()) * 100
        dn = (len(new) / len(base) - 1) * 100
        return wr_new - wr_base, len(base), len(new), wr_base, dn

    for label, lo in [('70', 70), ('75', 75), ('80', 80), ('85', 85), ('90', 90), ('95', 95)]:
        dwr, n_b, n_n, wr_b, dn = wr_dn(
            pl.col('overall_new') >= lo, pl.col('overall') >= lo, 'opt_result_15'
        )
        out[f'call_{label}p_dwr'] = dwr
        out[f'call_{label}p_n_base'] = n_b
        out[f'call_{label}p_n_new'] = n_n
        out[f'call_{label}p_dn'] = dn

    for label, hi in [('30', 30), ('25', 25), ('20', 20), ('15', 15), ('10', 10)]:
        dwr, n_b, n_n, wr_b, dn = wr_dn(
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

    composite = 0.0
    composite += out['call_70p_dwr'] * 1.5
    composite += out['put_lt25_dwr']
    # Bonus for 95+ alpha (the key metric we're trying to push)
    composite += max(0.0, out['call_95p_dwr']) * 0.5
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
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    print(f'[joined] {time.time()-t0:.0f}s', flush=True)

    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] {len(variants)} variants — asymmetric-K (Phase G)', flush=True)
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

    res = pl.DataFrame(results)

    # Version-suffix output
    parquet_stem = Path(PARQUET).stem
    version_tag = ''
    if parquet_stem.startswith('calls_v'):
        rest = parquet_stem.split('_', 2)
        if len(rest) >= 2:
            version_tag = '_' + rest[1]
    out_path = CACHE / f'phase_g_asymmetric{version_tag}_results.parquet'
    res.write_parquet(out_path)
    print(f'[done] → {out_path.name}  ({time.time()-t0:.0f}s)', flush=True)

    # Top of passing variants
    print('\n' + '=' * 145)
    print('CONFIGS PASSING H1 STRICT (calls) AND H3 STRICT (call N within ±15%)')
    print('=' * 145)
    passing = res.filter(pl.col('both_h1_h3_call')).sort('composite', descending=True)
    print(f'  Passing: {len(passing)} of {len(res)}')

    if len(passing) > 0:
        print(
            f'\n  {"rk":<3s}{"shape":<7s}'
            f'{"GtCLo-Hi":<10s}{"K_BASE":<8s}{"POWER":<7s}{"SatC":<6s}{"TgtC":<6s}'
            f'{"call70 ΔWR/ΔN":<14s}{"call85 ΔWR/ΔN":<14s}{"call95 ΔWR/ΔN":<14s}'
            f'{"put25 ΔWR/ΔN":<14s}{"comp":<7s}'
        )
        print('-' * 150)
        for i, r in enumerate(passing.head(15).iter_rows(named=True)):
            print(
                f'  {i+1:<3d}{r["IND_RAMP_SHAPE"][:5]:<7s}'
                f'{r["GATE_CALL_LO"]:>3d}-{r["GATE_CALL_HI"]:<5d} '
                f'{r["K_CALL_BASE"]:<6.3f}  {r["K_CALL_POWER"]:<5.2f}  '
                f'{r["KIJ_SAT_CALL"]:<5.1f} {r["LIFT_TARGET_CALL"]:<5.1f} '
                f'{r["call_70p_dwr"]:>+5.2f}/{r["call_70p_dn"]:>+5.1f}%  '
                f'{r["call_85p_dwr"]:>+5.2f}/{r["call_85p_dn"]:>+5.1f}%  '
                f'{r["call_95p_dwr"]:>+5.2f}/{r["call_95p_dn"]:>+5.1f}%  '
                f'{r["put_lt25_dwr"]:>+5.2f}/{r["put_lt25_dn"]:>+5.1f}%  '
                f'{r["composite"]:>+5.2f}'
            )

        # Detail on top-3
        print('\n' + '=' * 110)
        print('TOP-3 DETAIL — all tiers')
        print('=' * 110)
        for i, r in enumerate(passing.head(3).iter_rows(named=True)):
            print(f'\n--- Rank #{i+1} ---')
            print(f'  IND_RAMP={r["IND_RAMP_SHAPE"]}  CALL: gate={r["GATE_CALL_LO"]}-{r["GATE_CALL_HI"]} '
                  f'K_BASE={r["K_CALL_BASE"]:.3f} POWER={r["K_CALL_POWER"]:.2f} '
                  f'sat={r["KIJ_SAT_CALL"]:.1f} target={r["LIFT_TARGET_CALL"]:.1f}')
            for k in ['95p', '90p', '85p', '80p', '75p', '70p']:
                print(f'  CALL {k:<4s}  ΔWR={r[f"call_{k}_dwr"]:+5.2f}pp  '
                      f'N: {r[f"call_{k}_n_base"]:>5,} → {r[f"call_{k}_n_new"]:>5,} '
                      f'(Δ{r[f"call_{k}_dn"]:+5.1f}%)')
            for k in ['lt30', 'lt25', 'lt20', 'lt15', 'lt10']:
                print(f'  PUT  {k:<4s} ΔWR={r[f"put_{k}_dwr"]:+5.2f}pp  ΔN={r[f"put_{k}_dn"]:+5.1f}%')
    else:
        print('  ⚠ No variants pass both gates strictly. Showing top-10 by composite:')
        top = res.sort('composite', descending=True).head(10)
        for i, r in enumerate(top.iter_rows(named=True)):
            print(f'  {i+1:<3d}power={r["K_CALL_POWER"]:.2f}  '
                  f'gate={r["GATE_CALL_LO"]}-{r["GATE_CALL_HI"]}  K_BASE={r["K_CALL_BASE"]:.3f}  '
                  f'95+={r["call_95p_dwr"]:+5.2f}/{r["call_95p_dn"]:+5.1f}%  '
                  f'70+={r["call_70p_dwr"]:+5.2f}/{r["call_70p_dn"]:+5.1f}%  '
                  f'<25={r["put_lt25_dwr"]:+5.2f}  H1={r["h1_call_count"]}  H3v={r["h3_call_violations"]}')


if __name__ == '__main__':
    main()
