"""Phase C-multi: dual-indicator dampener — Ichimoku + smooth-wadj combined.

If Phase D shows pure-Ichimoku-substitutive can't fully recover v27's put alpha,
the next step is a multi-indicator gradient that captures BOTH cohorts:

  Ichimoku catches: bearish weekly Ichimoku state (cloud_position ≤ 0,
                    kijun_pct < 0). Different cohort than v27.
  Wadj-smooth:      v27's original cohort but with smooth gradient instead of
                    threshold. Replaces v27's `wadj < 1` cliff with
                    `ramp(wadj_dist, WADJ_SAT)` log/linear shape.

Combined put dampener:
  ind_grad_ichimoku = ramp(max(0, -kijun_pct), KIJ_SAT_PUT)
  ind_grad_wadj     = ramp(max(0, wadj - WADJ_LO_PUT), WADJ_SAT_PUT)
  ind_grad_combined = max(ind_grad_ichimoku, ind_grad_wadj)
                      # OR: ramp(combined_dist_sum, COMBINED_SAT)
                      # OR: independent dampeners summed

  weakness = score_grad × ind_grad_combined
  overall += K_PUT × weakness × (LIFT_TARGET_PUT - overall)

The wadj-smooth indicator inherently captures v27's cohort. The Ichimoku adds
orthogonal alpha. Combined: should beat v27 alone (the theoretical ceiling).

This sweep evaluates the combined dampener in SUBSTITUTIVE mode (v27 stripped).

NOTE: This script intentionally uses a max-of-gradients combination. Could
later be extended to test sum/independent variants.
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
N_VARIANTS = int(sys.argv[2]) if len(sys.argv) > 2 else 150
RNG_SEED = 42

# Same call params; put params now have BOTH kijun and wadj indicators.
PARAM_RANGES = {
    'GATE_CALL_LO':      (60.0, 75.0),
    'GATE_CALL_HI':      (78.0, 92.0),
    'K_CALL':            (0.30, 0.95),
    'KIJ_SAT_CALL':      (3.0, 15.0),
    'LIFT_TARGET_CALL':  (45.0, 60.0),
    # PUT side
    'GATE_PUT_LO':       (-20.0, 15.0),
    'GATE_PUT_HI':       (24.0, 32.0),
    'K_PUT':             (0.30, 0.95),
    'LIFT_TARGET_PUT':   (28.0, 50.0),
    # Put indicator: kijun
    'KIJ_SAT_PUT':       (3.0, 15.0),
    # Put indicator: smooth wadj — replaces v27's threshold
    # wadj_dist = max(0, wadj - WADJ_LO_PUT)  → fires when wadj > WADJ_LO_PUT
    # WADJ_LO_PUT controls where the wadj gradient starts firing.
    # v27's threshold was wadj > -17 (full strength at wadj=0).
    'WADJ_LO_PUT':       (-25.0, 0.0),
    'WADJ_SAT_PUT':      (5.0, 25.0),
}

CATEGORICAL = {
    'RAMP_SHAPE':       ['log', 'linear'],
    # 'product' = AND-combiner — fires ONLY when both indicators agree on bearish
    # state. Protects the rare bullish-cloud-but-weak-wadj cohort that has
    # +20pp WR (per put_tier_compare findings).
    'COMBINE_MODE':     ['max', 'sum_clip', 'product'],
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
            if 'GATE' in key or 'WADJ_LO' in key:
                v[key] = int(round(val))
            else:
                v[key] = round(val, 3)
        for key in cat_keys:
            v[key] = CATEGORICAL[key][rng.integers(0, len(CATEGORICAL[key]))]
        if v['GATE_CALL_LO'] >= v['GATE_CALL_HI']:
            v['GATE_CALL_HI'] = v['GATE_CALL_LO'] + 5
        if v['GATE_PUT_LO'] >= v['GATE_PUT_HI'] - 5:
            v['GATE_PUT_LO'] = v['GATE_PUT_HI'] - 10
        variants.append(v)
    return variants


def apply_dampener(df: pl.DataFrame, p: dict, score_col: str) -> pl.DataFrame:
    """Multi-indicator dampener. Call side uses kijun only; put side combines
    kijun + smooth-wadj per p['COMBINE_MODE']."""
    if p['RAMP_SHAPE'] == 'log':
        ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    # Indicator distances
    kij_dist  = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)
    wadj_dist = (pl.col('w_adj') - p['WADJ_LO_PUT']).clip(0.0, None)

    # CALL (Ichimoku-only — no wadj on this side per Phase F finding)
    call_sg = ramp(pl.col(score_col) - p['GATE_CALL_LO'],
                   p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    call_ig = ramp(kij_dist, p['KIJ_SAT_CALL'])
    call_w  = call_sg * call_ig
    call_d  = -p['K_CALL'] * call_w * (pl.col(score_col) - p['LIFT_TARGET_CALL'])

    # PUT — combined kijun + wadj
    put_sg = ramp(p['GATE_PUT_HI'] - pl.col(score_col),
                  p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig_kij  = ramp(kij_dist, p['KIJ_SAT_PUT'])
    put_ig_wadj = ramp(wadj_dist, p['WADJ_SAT_PUT'])

    if p['COMBINE_MODE'] == 'max':
        put_ig_combined = pl.max_horizontal(put_ig_kij, put_ig_wadj)
    elif p['COMBINE_MODE'] == 'sum_clip':
        # clip(kij + wadj, 0, 1) — fires when EITHER indicator is bearish
        put_ig_combined = (put_ig_kij + put_ig_wadj).clip(0.0, 1.0)
    else:
        # product (AND) — fires only when BOTH indicators agree on bearish state
        # Protects bullish-cloud puts (+20pp WR cohort) even when wadj is weak
        put_ig_combined = put_ig_kij * put_ig_wadj

    put_w = put_sg * put_ig_combined
    put_d = p['K_PUT'] * put_w * (p['LIFT_TARGET_PUT'] - pl.col(score_col))

    return df.with_columns(
        (pl.col(score_col) + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )


def evaluate(df: pl.DataFrame, p: dict) -> dict:
    df_v = apply_dampener(df, p, score_col='overall_pre_v27')
    out = {}

    def wr_n(mask, col, mask_base):
        new = df_v.filter(mask & pl.col(col).is_not_null())
        n_new = len(new)
        wr_new = float(new[col].mean()) * 100 if n_new > 0 else float('nan')
        base = df_v.filter(mask_base & pl.col(col).is_not_null())
        n_base = len(base)
        wr_base = float(base[col].mean()) * 100 if n_base > 0 else float('nan')
        return wr_new, n_new, wr_base, n_base

    for label, lo in [('70', 70), ('75', 75), ('80', 80), ('85', 85), ('90', 90), ('95', 95)]:
        wr_new, n_new, wr_base, n_base = wr_n(
            pl.col('overall_new') >= lo, 'opt_result_15',
            pl.col('overall') >= lo,
        )
        out[f'call_{label}p_wr_base'] = wr_base
        out[f'call_{label}p_n_base'] = n_base
        out[f'call_{label}p_wr_new'] = wr_new
        out[f'call_{label}p_n_new'] = n_new
        out[f'call_{label}p_dwr'] = wr_new - wr_base
        out[f'call_{label}p_dn_pct'] = (n_new / n_base - 1) * 100 if n_base > 0 else float('nan')

    for label, hi in [('30', 30), ('25', 25), ('20', 20), ('15', 15), ('10', 10), ('5', 5)]:
        wr_new, n_new, wr_base, n_base = wr_n(
            pl.col('overall_new') <= hi, 'put_result_15',
            pl.col('overall') <= hi,
        )
        out[f'put_lt{label}_wr_base'] = wr_base
        out[f'put_lt{label}_n_base'] = n_base
        out[f'put_lt{label}_wr_new'] = wr_new
        out[f'put_lt{label}_n_new'] = n_new
        out[f'put_lt{label}_dwr'] = wr_new - wr_base
        out[f'put_lt{label}_dn_pct'] = (n_new / n_base - 1) * 100 if n_base > 0 else float('nan')

    def n_penalty(dn_pct):
        if dn_pct is None or math.isnan(dn_pct):
            return 0.0
        excess = max(0.0, abs(dn_pct) - 15.0)
        return -(excess / 10.0) ** 2

    out['composite'] = (
        out['call_70p_dwr'] + out['put_lt25_dwr']
        + n_penalty(out['call_70p_dn_pct'])
        + n_penalty(out['put_lt25_dn_pct'])
    )
    out['put_h1_pass']  = out['put_lt25_dwr']  >= 0.3
    out['call_h1_pass'] = out['call_70p_dwr'] >= 0.3
    out['both_pass']    = out['put_h1_pass'] and out['call_h1_pass']
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

    wadj_df = pl.read_parquet(CACHE / 'wadj_v39_1825d_min0.parquet')
    df = df.join(wadj_df, on=['symbol', 'date'], how='left')

    df = df.with_columns(
        (pl.col('overall') - pl.col('wcf_lift').fill_null(0))
        .clip(0.0, 100.0).alias('overall_pre_v27')
    )
    # The wadj indicator only works where wadj is populated (~5.6% of universe)
    # For peaks without wadj, the wadj_dist will be 0 (no contribution from that
    # indicator). Ichimoku still fires from kijun. So mechanism degrades gracefully.
    n_with_wadj = df['w_adj'].drop_nulls().len()
    print(f'[wadj] {n_with_wadj:,} of {len(df):,} have w_adj; rest fall back to '
          f'kijun-only', flush=True)

    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] {len(variants)} variants — multi-indicator substitutive mode',
          flush=True)

    results = []
    t0 = time.time()
    for i, p in enumerate(variants):
        m = evaluate(df, p)
        m.update(p)
        results.append(m)
        if (i + 1) % 25 == 0:
            print(f'[sweep] {i+1}/{len(variants)}  ({time.time()-t0:.0f}s)', flush=True)

    res = pl.DataFrame(results)
    out_path = CACHE / 'phase_c_multi_results.parquet'
    res.write_parquet(out_path)
    print(f'[done] → {out_path.name}  ({time.time()-t0:.0f}s)', flush=True)

    # Top-10
    print('\n' + '=' * 145)
    print('TOP-10 MULTI-INDICATOR SUBSTITUTIVE configs')
    print('=' * 145)
    top = res.sort('composite', descending=True).head(10)
    print(
        f'\n{"rk":<3s}{"shape":<5s}{"comb":<5s}'
        f'{"GtCLo-Hi":<10s}{"K_C":<5s}{"SatC":<5s}{"TgtC":<6s}'
        f'{"GtPLo-Hi":<11s}{"K_P":<5s}{"SatP":<5s}{"TgtP":<6s}'
        f'{"WadjLo":<7s}{"WadjSat":<8s}'
        f'{"call70 ΔWR/ΔN":<14s}{"put25 ΔWR/ΔN":<14s}{"comp":<6s}{"H1":<3s}'
    )
    print('-' * 150)
    for i, r in enumerate(top.iter_rows(named=True)):
        h1 = '✓' if r['both_pass'] else ('+' if r['put_h1_pass'] else ('-' if r['call_h1_pass'] else ' '))
        print(
            f'{i+1:<3d}{r["RAMP_SHAPE"][:3]:<5s}{r["COMBINE_MODE"][:3]:<5s}'
            f'{r["GATE_CALL_LO"]:>3d}-{r["GATE_CALL_HI"]:<5d} '
            f'{r["K_CALL"]:<4.2f} {r["KIJ_SAT_CALL"]:<4.1f} {r["LIFT_TARGET_CALL"]:<5.1f} '
            f'{r["GATE_PUT_LO"]:>+4d}-{r["GATE_PUT_HI"]:<5d} '
            f'{r["K_PUT"]:<4.2f} {r["KIJ_SAT_PUT"]:<4.1f} {r["LIFT_TARGET_PUT"]:<5.1f} '
            f'{r["WADJ_LO_PUT"]:>+5d}  {r["WADJ_SAT_PUT"]:>5.1f}  '
            f'{r["call_70p_dwr"]:>+5.2f}/{r["call_70p_dn_pct"]:>+5.1f}%  '
            f'{r["put_lt25_dwr"]:>+5.2f}/{r["put_lt25_dn_pct"]:>+5.1f}%  '
            f'{r["composite"]:>+5.2f} {h1:<3s}'
        )

    # Detail on top-3
    print('\n' + '=' * 110)
    print('TOP-3 DETAIL — multi-indicator substitutive mode')
    print('=' * 110)
    for i, r in enumerate(top.head(3).iter_rows(named=True)):
        print(f'\n--- Rank #{i+1} ({r["RAMP_SHAPE"]} ramp, {r["COMBINE_MODE"]} combine) ---')
        print(f'  CALL: gate={r["GATE_CALL_LO"]}-{r["GATE_CALL_HI"]} '
              f'K={r["K_CALL"]:.2f} sat={r["KIJ_SAT_CALL"]:.1f} target={r["LIFT_TARGET_CALL"]:.1f}')
        print(f'  PUT:  gate={r["GATE_PUT_LO"]}-{r["GATE_PUT_HI"]} '
              f'K={r["K_PUT"]:.2f} kij_sat={r["KIJ_SAT_PUT"]:.1f} '
              f'target={r["LIFT_TARGET_PUT"]:.1f}  '
              f'wadj_lo={r["WADJ_LO_PUT"]} wadj_sat={r["WADJ_SAT_PUT"]:.1f}')

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
