"""Phase C: Latin-hypercube calibration of the Ichimoku log-magnifier dampener.

Mechanism (substitutes v27 WCF for puts, adds new dampener for calls):

  Call side (overall ≥ GATE_CALL_LO):
    score_grad = ramp(overall - GATE_CALL_LO, GATE_CALL_HI - GATE_CALL_LO)
    ind_dist   = max(0, -price_vs_kijun_pct)              # bearish kijun
    ind_grad   = ramp(ind_dist, KIJ_SAT_CALL)
    weakness   = score_grad × ind_grad
    overall   -= K_CALL × weakness × (overall - LIFT_TARGET_CALL)   # drift toward 55

  Put side (overall ≤ GATE_PUT_HI):
    score_grad = ramp(GATE_PUT_HI - overall, GATE_PUT_HI - GATE_PUT_LO)
    ind_dist   = max(0, -price_vs_kijun_pct)              # SAME bearish kijun signal
    ind_grad   = ramp(ind_dist, KIJ_SAT_PUT)
    weakness   = score_grad × ind_grad
    overall   += K_PUT × weakness × (LIFT_TARGET_PUT - overall)    # drift up toward 35

`ramp(x, sat)` uses either log (concave) or linear (proportional) shape per
RAMP_SHAPE. For all peaks: applied to v39 production score; new score is then
bucketed for H1-H5 evaluation.

Note: this is the ADDITIVE variant — Ichimoku applied on top of v39 (which still
includes v27 WCF). True substitutive (remove v27, apply Ichimoku) is a separate
validation pass against current production, run on the winning config.

Output: sweep_results.parquet + smoothness plots for top-3 configs.
"""
from __future__ import annotations

import io
import json
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
N_VARIANTS = int(sys.argv[2]) if len(sys.argv) > 2 else 80
RNG_SEED = 42

# ---------------------------------------------------------------------------
# Parameter space
# ---------------------------------------------------------------------------

PARAM_RANGES = {
    # CALL side
    'GATE_CALL_LO':     (60.0, 75.0),
    'GATE_CALL_HI':     (78.0, 92.0),
    'K_CALL':           (0.30, 0.95),
    'KIJ_SAT_CALL':     (3.0, 15.0),
    'LIFT_TARGET_CALL': (45.0, 60.0),
    # PUT side — extended GATE_PUT_LO range to ≥0 so gradient stays smooth
    # all the way to extreme deep puts (avoids gradient saturation cliff that
    # caused <5/<10 tier collapse in the first sweep).
    'GATE_PUT_LO':      (-15.0, 15.0),  # negative values = gradient never saturates in real range
    'GATE_PUT_HI':      (24.0, 30.0),
    'K_PUT':            (0.20, 0.70),   # tighter upper bound — full sweep showed K_PUT > 0.6
                                         # produces severe N collapse without WR gain
    'KIJ_SAT_PUT':      (3.0, 15.0),
    'LIFT_TARGET_PUT':  (28.0, 38.0),   # narrowed — 38+ pulls too aggressively above
                                         # the put zone
}

CATEGORICAL = {
    'RAMP_SHAPE': ['log', 'linear'],
}


def latin_hypercube(n: int, dims: int, rng: np.random.Generator) -> np.ndarray:
    """Latin hypercube sample in [0, 1]^dims with n points."""
    cuts = np.linspace(0, 1, n + 1)
    u = rng.uniform(size=(n, dims))
    a = cuts[:n]
    b = cuts[1:]
    rdpoints = u * (b - a)[:, None] + a[:, None]   # shape (n, dims)
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
            # Round LO/HI gates to int (cleaner score boundaries)
            if 'GATE' in key:
                v[key] = int(round(v[key]))
            else:
                v[key] = round(v[key], 3)
        # Sample categoricals uniformly
        for key in cat_keys:
            v[key] = CATEGORICAL[key][rng.integers(0, len(CATEGORICAL[key]))]
        # Enforce GATE_CALL_LO < GATE_CALL_HI and GATE_PUT_LO < GATE_PUT_HI
        if v['GATE_CALL_LO'] >= v['GATE_CALL_HI']:
            v['GATE_CALL_HI'] = v['GATE_CALL_LO'] + 5
        # GATE_PUT_LO can be negative (smooth gradient down to extreme puts)
        if v['GATE_PUT_LO'] >= v['GATE_PUT_HI'] - 5:
            v['GATE_PUT_LO'] = v['GATE_PUT_HI'] - 10
        variants.append(v)
    return variants


# ---------------------------------------------------------------------------
# Score transformation (vectorized in polars)
# ---------------------------------------------------------------------------

def _ramp_log(x_expr, sat: float):
    """log(1+x) / log(1+sat), clipped [0, 1]."""
    return (
        (((x_expr.clip(0.0, None) + 1).log()) / math.log(1 + sat))
        .clip(0.0, 1.0)
    )


def _ramp_linear(x_expr, sat: float):
    """Linear ramp 0 → 1 over [0, sat]."""
    return (x_expr.clip(0.0, None) / sat).clip(0.0, 1.0)


def apply_ichimoku(df: pl.DataFrame, p: dict) -> pl.DataFrame:
    """Apply the log-magnifier dampener and return df with 'overall_new' column."""
    if p['RAMP_SHAPE'] == 'log':
        ramp = _ramp_log
    else:
        ramp = _ramp_linear

    # Indicator: magnitude of "below kijun" — same for both sides
    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    # CALL side: only fires when overall in [GATE_CALL_LO, GATE_CALL_HI+]
    call_score_grad = ramp(
        pl.col('overall') - p['GATE_CALL_LO'],
        p['GATE_CALL_HI'] - p['GATE_CALL_LO'],
    )
    call_ind_grad = ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_weakness = call_score_grad * call_ind_grad
    call_delta = -p['K_CALL'] * call_weakness * (pl.col('overall') - p['LIFT_TARGET_CALL'])

    # PUT side: only fires when overall in [..GATE_PUT_HI]
    put_score_grad = ramp(
        p['GATE_PUT_HI'] - pl.col('overall'),
        p['GATE_PUT_HI'] - p['GATE_PUT_LO'],
    )
    put_ind_grad = ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_weakness = put_score_grad * put_ind_grad
    put_delta = p['K_PUT'] * put_weakness * (p['LIFT_TARGET_PUT'] - pl.col('overall'))

    # Both deltas can technically apply if score is in the middle, but the
    # gradients design makes them effectively zero in the neutral zone. Sum
    # is safe.
    df = df.with_columns(
        (pl.col('overall') + call_delta.fill_null(0) + put_delta.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )
    return df


# ---------------------------------------------------------------------------
# Evaluation: H1-H5 affected-tier metrics on the SAME cohort
# ---------------------------------------------------------------------------

def evaluate_variant(df: pl.DataFrame, p: dict) -> dict:
    """Apply variant, recompute affected-tier WR15, return metrics dict.

    Tier definitions match the canonical buckets:
      Calls: 95+, 90+, 85+, 80+, 75+, 70+
      Puts:  <30, <25, <20, <15, <10, <5
    """
    out = {}
    df_v = apply_ichimoku(df, p)

    # Helper: compute WR15 for a tier mask
    def wr_n(mask, col='opt_result_15'):
        sub = df_v.filter(mask & pl.col(col).is_not_null())
        n = len(sub)
        if n == 0:
            return float('nan'), 0
        return float(sub[col].mean()) * 100, n

    # --- CALL tiers ---
    for tier_label, lo in [('70', 70), ('75', 75), ('80', 80), ('85', 85), ('90', 90), ('95', 95)]:
        # Baseline (using original overall)
        b_wr, b_n = wr_n(pl.col('overall') >= lo)
        # New (using overall_new)
        n_wr, n_n = wr_n(pl.col('overall_new') >= lo)
        out[f'call_{tier_label}p_wr_base'] = b_wr
        out[f'call_{tier_label}p_n_base'] = b_n
        out[f'call_{tier_label}p_wr_new'] = n_wr
        out[f'call_{tier_label}p_n_new'] = n_n
        out[f'call_{tier_label}p_dwr'] = n_wr - b_wr
        out[f'call_{tier_label}p_dn_pct'] = (n_n / b_n - 1) * 100 if b_n > 0 else float('nan')

    # --- PUT tiers (use put-side WR) ---
    for tier_label, hi in [('30', 30), ('25', 25), ('20', 20), ('15', 15), ('10', 10), ('5', 5)]:
        b_wr, b_n = wr_n(pl.col('overall') <= hi, col='put_result_15')
        n_wr, n_n = wr_n(pl.col('overall_new') <= hi, col='put_result_15')
        out[f'put_lt{tier_label}_wr_base'] = b_wr
        out[f'put_lt{tier_label}_n_base'] = b_n
        out[f'put_lt{tier_label}_wr_new'] = n_wr
        out[f'put_lt{tier_label}_n_new'] = n_n
        out[f'put_lt{tier_label}_dwr'] = n_wr - b_wr
        out[f'put_lt{tier_label}_dn_pct'] = (n_n / b_n - 1) * 100 if b_n > 0 else float('nan')

    # --- Composite score: weighted average of affected-tier deltas with N-stability penalty ---
    # Affected tiers per side:
    #   Calls: 70+ (catches 70-74 + 75+ effects)
    #   Puts:  <25 (catches the cohort hypothesized to invert)
    call_dwr = out['call_70p_dwr']
    call_dn = out['call_70p_dn_pct']
    put_dwr = out['put_lt25_dwr']
    put_dn = out['put_lt25_dn_pct']

    # Penalty: ±15% N drift = 0 penalty, beyond = quadratic
    def n_penalty(dn_pct):
        if dn_pct is None or math.isnan(dn_pct):
            return 0.0
        excess = max(0.0, abs(dn_pct) - 15.0)
        return -(excess / 10.0) ** 2

    composite = (
        call_dwr + put_dwr
        + n_penalty(call_dn) + n_penalty(put_dn)
    )
    out['composite'] = composite

    # H1-strict pass: dWR ≥ +0.3 on at least one side, no major regression on the other
    h1_call = call_dwr >= 0.3 and put_dwr >= -0.5
    h1_put  = put_dwr >= 0.3 and call_dwr >= -0.5
    out['h1_pass'] = h1_call or h1_put

    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pq = CACHE / PARQUET
    if not pq.exists():
        print(f'[ERROR] parquet not found: {pq}', flush=True)
        return

    # Load parquet
    print(f'[load] {pq.name}...', flush=True)
    df = pl.read_parquet(pq)
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    print(f'[load] parquet: {len(df):,} rows', flush=True)

    # Attach put-side WR15
    print('[load] put-side WR15 from barrier_cache...', flush=True)
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
    print(f'[joined] put_result_15: {df["put_result_15"].drop_nulls().len():,} '
          f'({time.time()-t0:.1f}s)', flush=True)

    # Drop rows with no kijun (can't dampen)
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())
    print(f'[filter] kijun non-null: {len(df):,}', flush=True)

    # Generate variants
    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] generated {len(variants)} variants via Latin hypercube', flush=True)

    # Run sweep
    results = []
    t0 = time.time()
    for i, p in enumerate(variants):
        m = evaluate_variant(df, p)
        m.update(p)
        results.append(m)
        if (i + 1) % 10 == 0:
            print(f'[sweep] {i+1}/{len(variants)}  ({time.time()-t0:.0f}s)', flush=True)

    res = pl.DataFrame(results)
    out_path = CACHE / 'phase_c_sweep_results.parquet'
    res.write_parquet(out_path)
    print(f'[done] {len(res)} variants → {out_path.name}  ({time.time()-t0:.0f}s)', flush=True)

    # Top-10 by composite
    print('\n' + '=' * 110)
    print('TOP-10 by composite score (call_70p_dwr + put_lt25_dwr − N-drift penalty)')
    print('=' * 110)
    top = res.sort('composite', descending=True).head(10)

    print(
        f'\n{"rank":<5s}{"shape":<8s}'
        f'{"GtCLo-Hi":<10s}{"K_C":<6s}{"SatC":<6s}{"TgtC":<6s}'
        f'{"GtPLo-Hi":<10s}{"K_P":<6s}{"SatP":<6s}{"TgtP":<6s}'
        f'{"call70 ΔWR/ΔN":<14s}{"put25 ΔWR/ΔN":<14s}{"compos":<8s}{"h1":<3s}'
    )
    print('-' * 130)
    for i, r in enumerate(top.iter_rows(named=True)):
        print(
            f'{i+1:<5d}{r["RAMP_SHAPE"]:<8s}'
            f'{r["GATE_CALL_LO"]:>3d}-{r["GATE_CALL_HI"]:<5d} '
            f'{r["K_CALL"]:<5.2f} {r["KIJ_SAT_CALL"]:<5.1f} {r["LIFT_TARGET_CALL"]:<5.1f} '
            f'{r["GATE_PUT_LO"]:>3d}-{r["GATE_PUT_HI"]:<5d} '
            f'{r["K_PUT"]:<5.2f} {r["KIJ_SAT_PUT"]:<5.1f} {r["LIFT_TARGET_PUT"]:<5.1f} '
            f'{r["call_70p_dwr"]:>+5.2f}/{r["call_70p_dn_pct"]:>+5.1f}%  '
            f'{r["put_lt25_dwr"]:>+5.2f}/{r["put_lt25_dn_pct"]:>+5.1f}%  '
            f'{r["composite"]:>+5.2f}  '
            f'{"✓" if r["h1_pass"] else "✗"}'
        )

    # Detail on top-3
    print('\n' + '=' * 110)
    print('TOP-3 DETAIL — all-tier WR15 deltas')
    print('=' * 110)
    for i, r in enumerate(top.head(3).iter_rows(named=True)):
        print(f'\n--- Rank #{i+1} ---')
        print(f'  RAMP={r["RAMP_SHAPE"]}  CALL: gate={r["GATE_CALL_LO"]}-{r["GATE_CALL_HI"]} '
              f'K={r["K_CALL"]} sat={r["KIJ_SAT_CALL"]} target={r["LIFT_TARGET_CALL"]}')
        print(f'                       PUT:  gate={r["GATE_PUT_LO"]}-{r["GATE_PUT_HI"]} '
              f'K={r["K_PUT"]} sat={r["KIJ_SAT_PUT"]} target={r["LIFT_TARGET_PUT"]}')

        for tier in ['95p', '90p', '85p', '80p', '75p', '70p']:
            wr_b = r[f'call_{tier}_wr_base']
            wr_n = r[f'call_{tier}_wr_new']
            n_b = r[f'call_{tier}_n_base']
            n_n = r[f'call_{tier}_n_new']
            print(f'  CALL {tier:<4s}  WR15: {wr_b:5.2f}% → {wr_n:5.2f}% (Δ{wr_n-wr_b:+5.2f})  '
                  f'N: {n_b:>5,} → {n_n:>5,} ({(n_n/n_b-1)*100 if n_b else 0:+5.1f}%)')

        for tier in ['lt30', 'lt25', 'lt20', 'lt15', 'lt10', 'lt5']:
            wr_b = r[f'put_{tier}_wr_base']
            wr_n = r[f'put_{tier}_wr_new']
            n_b = r[f'put_{tier}_n_base']
            n_n = r[f'put_{tier}_n_new']
            print(f'  PUT  {tier:<4s} WR15: {wr_b:5.2f}% → {wr_n:5.2f}% (Δ{wr_n-wr_b:+5.2f})  '
                  f'N: {n_b:>5,} → {n_n:>5,} ({(n_n/n_b-1)*100 if n_b else 0:+5.1f}%)')


if __name__ == '__main__':
    main()
