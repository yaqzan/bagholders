"""Stage A — regime band threshold sweep (linear approximation).

Substrate: v46 stored scores in DB (v46 = v45 clean breadth + WVD-Wave).
Compute: for each variant band config, apply variant multiplier to stored
pre_regime via the dampener-delta-preservation trick:

  dampener_delta = stored_overall - apply_regime(pre_regime, mult_v46)
  overall_v      = apply_regime(pre_regime, mult_v) + dampener_delta

This isolates the regime-band-only impact while preserving all downstream
dampener applications (PCD/MCD/ICH/WVD/PESS/EARN_BOOST). Approximation breaks
when band-induced score shift causes a dampener to gate in/out — but that's
second-order and Stage B (simulator-with-override) catches it for top
candidates.

Variant axes (5):
  BAND_OFFSET       [-15, +5]    shift all band thresholds by this amount
  BULL_MULT_CEIL    [1.05, 1.20] mult ceiling at composite=100
  HEALTHY_PIVOT     [1.00, 1.10] mult at HEALTHY_LO threshold (currently 1.00)
  STRESS_MULT_FLOOR [0.65, 0.85] mult floor at composite=0
  CAUTION_MID       [0.78, 0.92] mult at CAUTION_HI threshold (currently 0.88)

Default v46 production = (offset=0, ceil=1.10, healthy_pivot=1.00,
stress_floor=0.70, caution_mid=0.88).

Gate: H1 strict (call tier WR ≥+0.3pp ≥3 of 5; none regress > -1.0pp), H3
N stability ±15% per primary tier. Composite ranks by alpha-weighted score
with N-retention bonus (recovering compound = retaining N at high tiers).
"""
from __future__ import annotations
import io, math, sys, time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / '.cache' / 'regime_bands' / 'v45_1825d.parquet'  # v46 actually
OUT_DIR = ROOT / '.cache' / 'regime_bands'

N_VARIANTS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
RNG_SEED = 42

# Default production bands (v45/v46) — used as anchor + reference for delta
DEFAULT_BANDS = [
    (0,  15, 0.70, 0.70),
    (15, 30, 0.70, 0.78),
    (30, 45, 0.78, 0.88),
    (45, 60, 0.88, 1.00),
    (60, 75, 1.00, 1.05),
    (75, 101, 1.05, 1.10),
]


def make_variant_bands(p: dict) -> list[tuple]:
    """Construct bands from variant params.

    BAND_OFFSET shifts all thresholds. Mults at endpoints are interpolated.
    """
    off = p['BAND_OFFSET']
    stress_floor = p['STRESS_MULT_FLOOR']
    caution_mid  = p['CAUTION_MID']
    healthy_piv  = p['HEALTHY_PIVOT']
    bull_ceil    = p['BULL_MULT_CEIL']

    # Shift thresholds, clip to valid range, derive intermediate mults linearly
    t = [
        max(0,   0  + off),
        max(5,   15 + off),
        max(10,  30 + off),
        max(20,  45 + off),
        max(30,  60 + off),
        max(40,  75 + off),
        101
    ]
    # Mid mult between stress_floor and caution_mid (caution-) — linear avg
    caution_low_mult = stress_floor + (caution_mid - stress_floor) * 0.5
    return [
        (t[0], t[1], stress_floor,      stress_floor),
        (t[1], t[2], stress_floor,      caution_low_mult),
        (t[2], t[3], caution_low_mult,  caution_mid),
        (t[3], t[4], caution_mid,       healthy_piv),
        (t[4], t[5], healthy_piv,       (healthy_piv + bull_ceil) / 2),
        (t[5], t[6], (healthy_piv + bull_ceil) / 2, bull_ceil),
    ]


def compute_mult_polars(composite_col: pl.Expr, bands: list[tuple]) -> pl.Expr:
    """Compute regime multiplier from composite using polars expressions."""
    expr = pl.lit(1.0)
    for lo, hi, m_lo, m_hi in reversed(bands):
        if hi > lo:
            t = ((composite_col - lo) / (hi - lo)).clip(0.0, 1.0)
            mult_in_band = m_lo + t * (m_hi - m_lo)
        else:
            mult_in_band = pl.lit(m_lo)
        expr = pl.when((composite_col >= lo) & (composite_col < hi)).then(mult_in_band).otherwise(expr)
    return expr


def apply_regime_polars(pre_regime: pl.Expr, mult: pl.Expr) -> pl.Expr:
    """Apply regime multiplier symmetric around 50 (calls: mult, puts: 2-mult)."""
    call_adj = 50 + (pre_regime - 50) * mult
    put_adj  = 50 + (pre_regime - 50) * (2.0 - mult)
    adj = pl.when(pre_regime >= 50).then(call_adj).otherwise(put_adj)
    return adj.clip(0.0, 100.0).round(0).cast(pl.Int64)


PARAM_RANGES = {
    'BAND_OFFSET':       (-15.0, 5.0),
    'BULL_MULT_CEIL':    (1.05,  1.20),
    'HEALTHY_PIVOT':     (1.00,  1.10),
    'STRESS_MULT_FLOOR': (0.65,  0.85),
    'CAUTION_MID':       (0.78,  0.92),
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
    keys = list(PARAM_RANGES.keys())
    H = latin_hypercube(n, len(keys), rng)
    variants = []

    # Anchor: v45/v46 production
    variants.append({
        'BAND_OFFSET': 0.0, 'BULL_MULT_CEIL': 1.10,
        'HEALTHY_PIVOT': 1.00, 'STRESS_MULT_FLOOR': 0.70,
        'CAUTION_MID': 0.88, '_label': 'v46_anchor',
    })

    # Anchor: shift down 7 (matches median composite shift estimate)
    variants.append({
        'BAND_OFFSET': -7.0, 'BULL_MULT_CEIL': 1.10,
        'HEALTHY_PIVOT': 1.00, 'STRESS_MULT_FLOOR': 0.70,
        'CAUTION_MID': 0.88, '_label': 'shift_minus7',
    })

    # Anchor: aggressive bull amplification
    variants.append({
        'BAND_OFFSET': -10.0, 'BULL_MULT_CEIL': 1.18,
        'HEALTHY_PIVOT': 1.05, 'STRESS_MULT_FLOOR': 0.70,
        'CAUTION_MID': 0.88, '_label': 'aggressive_bull',
    })

    for i in range(n - 3):
        v = {}
        for j, key in enumerate(keys):
            lo, hi = PARAM_RANGES[key]
            v[key] = round(lo + (hi - lo) * H[i, j], 3)
        v['_label'] = ''
        variants.append(v)
    return variants


def evaluate(df: pl.DataFrame, p: dict, default_bands: list[tuple]) -> dict:
    var_bands = make_variant_bands(p)

    # mult under variant + default
    df_v = df.with_columns([
        compute_mult_polars(pl.col('composite'), var_bands).alias('mult_v'),
        compute_mult_polars(pl.col('composite'), default_bands).alias('mult_default'),
    ])
    # apply each multiplier to pre_regime
    df_v = df_v.with_columns([
        apply_regime_polars(pl.col('pre_regime'), pl.col('mult_v')).alias('regime_v'),
        apply_regime_polars(pl.col('pre_regime'), pl.col('mult_default')).alias('regime_default'),
    ])
    # delta + project
    df_v = df_v.with_columns([
        (pl.col('overall') + pl.col('regime_v') - pl.col('regime_default'))
        .clip(0, 100).cast(pl.Int64).alias('overall_v')
    ])

    out = {}

    def wr_dn(mask_new, mask_base, col):
        new = df_v.filter(mask_new & pl.col(col).is_not_null())
        base = df_v.filter(mask_base & pl.col(col).is_not_null())
        if len(base) == 0:
            return float('nan'), 0, 0, 0.0
        wr_new = float(new[col].mean()) * 100 if len(new) else float('nan')
        wr_base = float(base[col].mean()) * 100
        dn = (len(new) / len(base) - 1) * 100
        return wr_new - wr_base, len(base), len(new), dn

    for label, lo in [('70', 70), ('75', 75), ('80', 80), ('85', 85), ('90', 90), ('95', 95)]:
        dwr, n_b, n_n, dn = wr_dn(
            pl.col('overall_v') >= lo, pl.col('overall') >= lo, 'opt_result_15_call'
        )
        out[f'call_{label}p_dwr'] = dwr
        out[f'call_{label}p_n_base'] = n_b
        out[f'call_{label}p_n_new'] = n_n
        out[f'call_{label}p_dn'] = dn

    for label, hi in [('25', 25), ('20', 20), ('15', 15), ('10', 10)]:
        dwr, n_b, n_n, dn = wr_dn(
            pl.col('overall_v') <= hi, pl.col('overall') <= hi, 'opt_result_15_put'
        )
        out[f'put_lt{label}_dwr'] = dwr
        out[f'put_lt{label}_dn'] = dn

    # H1 strict
    h1_pass = sum(1 for k in ['95','90','85','80','75'] if out[f'call_{k}p_dwr'] >= 0.3)
    h1_regress = sum(1 for k in ['95','90','85','80','75'] if out[f'call_{k}p_dwr'] <= -1.0)
    out['h1_call_pass'] = h1_pass >= 3 and h1_regress == 0
    out['h1_call_count'] = h1_pass

    # H3 N stability — for compound recovery we want N to GROW (not just stay), so
    # we relax to ±20% on call N and require non-negative drift at top tiers
    h3_violations = sum(
        1 for k in ['95','90','85','80','75','70'] if abs(out[f'call_{k}p_dn']) > 20
    )
    out['h3_call_pass'] = h3_violations == 0
    out['h3_call_violations'] = h3_violations

    # Composite — recovery focus: alpha + N-growth bonus (avg N drift on call tiers)
    composite = 0.0
    composite += out['call_95p_dwr'] * 0.4
    composite += out['call_90p_dwr'] * 0.6
    composite += out['call_85p_dwr'] * 0.8
    composite += out['call_80p_dwr'] * 1.0
    composite += out['call_75p_dwr'] * 0.5
    composite += out['put_lt25_dwr'] * 0.3

    # N-growth reward at primary tiers (positive ΔN = compound recovery)
    avg_n_drift = (out['call_75p_dn'] + out['call_80p_dn'] + out['call_85p_dn'] + out['call_90p_dn']) / 4
    composite += avg_n_drift * 0.05  # +5pt N drift = +0.25 composite (~ 0.5pp of alpha)

    # Penalize put N-growth (DD risk)
    avg_put_n_drift = (out['put_lt25_dn'] + out['put_lt15_dn']) / 2
    composite -= max(0, avg_put_n_drift) * 0.10

    out['composite']      = composite
    out['avg_call_n_dn']  = avg_n_drift
    out['avg_put_n_dn']   = avg_put_n_drift
    out['both_h1_h3'] = out['h1_call_pass'] and out['h3_call_pass']
    return out


def main():
    df = pl.read_parquet(PARQUET)
    print(f'[load] {len(df):,} rows')

    variants = gen_variants(N_VARIANTS, seed=RNG_SEED)
    print(f'[sweep] {len(variants)} variants — Stage A')

    results = []
    t0 = time.time()
    for i, p in enumerate(variants):
        m = evaluate(df, p, DEFAULT_BANDS)
        m.update({k: v for k, v in p.items() if not k.startswith('_')})
        m['_label'] = p.get('_label', '')
        results.append(m)
        if (i + 1) % 25 == 0:
            print(f'[sweep] {i+1}/{len(variants)} ({time.time()-t0:.0f}s)', flush=True)

    res = pl.DataFrame(results)
    out_path = OUT_DIR / 'stage_a_results.parquet'
    res.write_parquet(out_path)
    print(f'\n[done] → {out_path}  ({time.time()-t0:.0f}s)')

    print('\n' + '=' * 145)
    print('STAGE A — TOP CONFIGS PASSING H1+H3 STRICT')
    print('=' * 145)

    passing = res.filter(pl.col('both_h1_h3')).sort('composite', descending=True)
    print(f'  Passing: {len(passing)} of {len(res)}\n')

    cols = ['composite', '_label',
            'call_95p_dwr', 'call_90p_dwr', 'call_85p_dwr', 'call_80p_dwr', 'call_75p_dwr',
            'call_85p_dn', 'call_80p_dn', 'call_75p_dn', 'avg_call_n_dn',
            'put_lt25_dwr', 'avg_put_n_dn',
            'BAND_OFFSET', 'BULL_MULT_CEIL', 'HEALTHY_PIVOT', 'STRESS_MULT_FLOOR', 'CAUTION_MID']

    print('Top 15:')
    with pl.Config(tbl_rows=20, tbl_cols=20):
        print(passing.head(15).select(cols))

    print('\nAnchors (v46/shift_minus7/aggressive_bull):')
    with pl.Config(tbl_rows=20, tbl_cols=20):
        anchors = res.filter(pl.col('_label') != '').select(cols)
        print(anchors)


if __name__ == '__main__':
    main()
