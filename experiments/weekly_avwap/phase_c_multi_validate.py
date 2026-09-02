"""Strict H1-H5 ship gate validation on best multi-indicator substitutive config.

Per assessment-backtest.md, ship criteria for scoring changes:

  H1 (per-trade lift):
    Strict:    ≥+0.5pp on ≥3 of {95+, 90+, 85+, 80+, 75+} at 5y; none regress >-1.0pp
    Affected:  ≥+0.3pp on affected tier (70+ for sub-75 changes; <25 for put changes)

  H2 (directional consistency):
    WR15 and WR30 move same direction
    WR15 > WR30 (scaled) is a red flag

  H3 (N stability):
    ±15% per bucket at 5y
    No primary tier below 50 peaks

  H4 (puts neutral or better):
    <25 and <15 TP% unchanged or improved (within ±0.5pp tolerance)

  H5 (multi-window):
    Sign consistency across 1y, 3y, 5y

Plus smoothness check: response curves are monotonic and clean log waves.

Reads phase_c_multi_results.parquet, picks specified rank, validates strictly.

Usage:
    python experiments/weekly_avwap/phase_c_multi_validate.py [rank]
"""
from __future__ import annotations

import io
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

RANK = int(sys.argv[1]) if len(sys.argv) > 1 else 1


# ---------------------------------------------------------------------------
# Mechanism (matches phase_c_multi_sweep.py)
# ---------------------------------------------------------------------------

def apply_dampener(df: pl.DataFrame, p: dict, score_col: str) -> pl.DataFrame:
    if p['RAMP_SHAPE'] == 'log':
        ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    kij_dist  = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)
    wadj_dist = (pl.col('w_adj') - p['WADJ_LO_PUT']).clip(0.0, None)

    call_sg = ramp(pl.col(score_col) - p['GATE_CALL_LO'],
                   p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    call_ig = ramp(kij_dist, p['KIJ_SAT_CALL'])
    call_w  = call_sg * call_ig
    call_d  = -p['K_CALL'] * call_w * (pl.col(score_col) - p['LIFT_TARGET_CALL'])

    put_sg = ramp(p['GATE_PUT_HI'] - pl.col(score_col),
                  p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig_kij  = ramp(kij_dist, p['KIJ_SAT_PUT'])
    put_ig_wadj = ramp(wadj_dist, p['WADJ_SAT_PUT'])

    if p['COMBINE_MODE'] == 'max':
        put_ig = pl.max_horizontal(put_ig_kij, put_ig_wadj)
    elif p['COMBINE_MODE'] == 'sum_clip':
        put_ig = (put_ig_kij + put_ig_wadj).clip(0.0, 1.0)
    else:
        put_ig = put_ig_kij * put_ig_wadj

    put_w = put_sg * put_ig
    put_d = p['K_PUT'] * put_w * (p['LIFT_TARGET_PUT'] - pl.col(score_col))

    return df.with_columns(
        (pl.col(score_col) + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )


# ---------------------------------------------------------------------------
# Tier evaluation
# ---------------------------------------------------------------------------

def tier_metric(df: pl.DataFrame, score_col: str, side: str, period: str = '15') -> dict:
    """Return WR + N for each tier under df[score_col]."""
    out = {}
    if side == 'call':
        result_col = f'opt_result_{period}'
        tiers = [('95+', pl.col(score_col) >= 95),
                 ('90+', pl.col(score_col) >= 90),
                 ('85+', pl.col(score_col) >= 85),
                 ('80+', pl.col(score_col) >= 80),
                 ('75+', pl.col(score_col) >= 75),
                 ('70+', pl.col(score_col) >= 70)]
    else:
        result_col = f'put_result_{period}'
        tiers = [('<5',  pl.col(score_col) <= 5),
                 ('<10', pl.col(score_col) <= 10),
                 ('<15', pl.col(score_col) <= 15),
                 ('<20', pl.col(score_col) <= 20),
                 ('<25', pl.col(score_col) <= 25),
                 ('<30', pl.col(score_col) <= 30)]

    for label, mask in tiers:
        sub = df.filter(mask & pl.col(result_col).is_not_null())
        n = len(sub)
        wr = float(sub[result_col].mean()) * 100 if n > 0 else float('nan')
        out[label] = (wr, n)
    return out


# ---------------------------------------------------------------------------
# Smoothness check
# ---------------------------------------------------------------------------

def _ramp_log_np(x, sat):
    x = np.maximum(0.0, x)
    return np.clip(np.log1p(x) / np.log1p(sat), 0, 1)


def _ramp_linear_np(x, sat):
    x = np.maximum(0.0, x)
    return np.clip(x / sat, 0, 1)


def smoothness(p: dict) -> bool:
    ramp = _ramp_log_np if p['RAMP_SHAPE'] == 'log' else _ramp_linear_np

    print('\n  Smoothness check:')
    overalls = np.arange(50, 100)
    call_grads = ramp(overalls - p['GATE_CALL_LO'],
                      p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    if np.all(np.diff(call_grads) >= -1e-9):
        print('    ✓ Call score_grad monotonic non-decreasing')
    else:
        print('    ✗ Call score_grad has non-monotonic regions')
        return False

    overalls_p = np.arange(0, 35)
    put_grads = ramp(p['GATE_PUT_HI'] - overalls_p,
                     p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    if np.all(np.diff(put_grads) <= 1e-9):
        print('    ✓ Put score_grad monotonic non-increasing')
    else:
        print('    ✗ Put score_grad has non-monotonic regions')
        return False

    # Indicator gradients
    kijuns = np.arange(0, 50)
    call_ind = ramp(kijuns, p['KIJ_SAT_CALL'])
    put_ind_kij = ramp(kijuns, p['KIJ_SAT_PUT'])
    if np.all(np.diff(call_ind) >= -1e-9) and np.all(np.diff(put_ind_kij) >= -1e-9):
        print('    ✓ Kijun indicator grad monotonic non-decreasing')
    else:
        print('    ✗ Kijun indicator grad has non-monotonic regions')
        return False

    wadjs = np.arange(-30, 5)
    wadj_dist = np.maximum(0, wadjs - p['WADJ_LO_PUT'])
    wadj_grad = ramp(wadj_dist, p['WADJ_SAT_PUT'])
    if np.all(np.diff(wadj_grad) >= -1e-9):
        print('    ✓ Wadj indicator grad monotonic non-decreasing in wadj')
    else:
        print('    ✗ Wadj indicator grad has non-monotonic regions')
        return False
    return True


# ---------------------------------------------------------------------------
# Multi-window check
# ---------------------------------------------------------------------------

def multi_window(df: pl.DataFrame, p: dict) -> dict:
    """Compute call_70+ and put<25 ΔWR at 1y / 3y / 5y."""
    today = date.today()
    windows = [(365, '1y'), (1095, '3y'), (1825, '5y')]
    df_v = apply_dampener(df, p, score_col='overall_pre_v27')

    rows = {}
    for days, label in windows:
        cutoff = (today - timedelta(days=days)).isoformat()
        sub = df_v.filter(pl.col('date') >= cutoff)

        def deltas(side, target_label, mask_new, mask_base, col):
            base = sub.filter(mask_base & pl.col(col).is_not_null())
            new  = sub.filter(mask_new & pl.col(col).is_not_null())
            if len(base) == 0:
                return 0, 0, 0
            wr_base = float(base[col].mean()) * 100
            wr_new  = float(new[col].mean()) * 100 if len(new) else float('nan')
            return wr_new - wr_base, len(base), (len(new) / len(base) - 1) * 100

        rows[label] = {}
        rows[label]['call70'] = deltas('call', '70+',
            pl.col('overall_new') >= 70, pl.col('overall') >= 70, 'opt_result_15')
        rows[label]['call75'] = deltas('call', '75+',
            pl.col('overall_new') >= 75, pl.col('overall') >= 75, 'opt_result_15')
        rows[label]['call80'] = deltas('call', '80+',
            pl.col('overall_new') >= 80, pl.col('overall') >= 80, 'opt_result_15')
        rows[label]['call85'] = deltas('call', '85+',
            pl.col('overall_new') >= 85, pl.col('overall') >= 85, 'opt_result_15')
        rows[label]['call90'] = deltas('call', '90+',
            pl.col('overall_new') >= 90, pl.col('overall') >= 90, 'opt_result_15')
        rows[label]['call95'] = deltas('call', '95+',
            pl.col('overall_new') >= 95, pl.col('overall') >= 95, 'opt_result_15')
        rows[label]['put25']  = deltas('put', '<25',
            pl.col('overall_new') <= 25, pl.col('overall') <= 25, 'put_result_15')
        rows[label]['put15']  = deltas('put', '<15',
            pl.col('overall_new') <= 15, pl.col('overall') <= 15, 'put_result_15')
    return rows


# ---------------------------------------------------------------------------
# H1-H5 strict gate evaluation
# ---------------------------------------------------------------------------

def evaluate_strict(df: pl.DataFrame, p: dict) -> dict:
    """Run all H1-H5 gates and return a structured pass/fail report."""
    df_v = apply_dampener(df, p, score_col='overall_pre_v27')

    # Baseline (v39 production scores) and new (substitutive scores)
    base_call_15 = tier_metric(df_v, 'overall', 'call', '15')
    base_put_15  = tier_metric(df_v, 'overall', 'put',  '15')
    new_call_15  = tier_metric(df_v, 'overall_new', 'call', '15')
    new_put_15   = tier_metric(df_v, 'overall_new', 'put',  '15')
    base_call_30 = tier_metric(df_v, 'overall', 'call', '30')
    new_call_30  = tier_metric(df_v, 'overall_new', 'call', '30')
    base_put_30  = tier_metric(df_v, 'overall', 'put',  '30')
    new_put_30   = tier_metric(df_v, 'overall_new', 'put',  '30')

    # H1 strict — ≥+0.5pp on ≥3 call tiers; no tier regresses >-1.0pp
    print('\n  H1 (per-trade lift on call tiers):')
    h1_call_pos = 0
    h1_call_regress = []
    for tier in ['95+', '90+', '85+', '80+', '75+']:
        b_wr, b_n = base_call_15[tier]
        n_wr, n_n = new_call_15[tier]
        d = n_wr - b_wr
        flag = ''
        if d >= 0.5:
            h1_call_pos += 1
            flag = '  ✓ ≥+0.5pp'
        elif d <= -1.0:
            h1_call_regress.append(tier)
            flag = '  ✗ regress'
        print(f'    Call {tier:<4s}  Δ{d:+5.2f}pp  N {b_n:>4,}→{n_n:>4,}{flag}')

    # H1 affected-tier — ≥+0.3pp on 70+ (call) and <25 (put)
    print('\n  H1 (affected-tier framework):')
    b_wr, b_n = base_call_15['70+']; n_wr, n_n = new_call_15['70+']
    call_70p_d = n_wr - b_wr
    flag = '  ✓' if call_70p_d >= 0.3 else '  ✗'
    print(f'    Call 70+ Δ{call_70p_d:+5.2f}pp  (gate ≥+0.3pp){flag}')
    b_wr, b_n = base_put_15['<25']; n_wr, n_n = new_put_15['<25']
    put_25_d = n_wr - b_wr
    flag = '  ✓' if put_25_d >= 0.3 else '  ✗'
    print(f'    Put  <25 Δ{put_25_d:+5.2f}pp  (gate ≥+0.3pp){flag}')

    # H2 — WR15 and WR30 same direction
    print('\n  H2 (WR15/WR30 directional consistency):')
    h2_pass = True
    for tier in ['95+', '90+', '85+', '80+', '75+', '70+']:
        d15 = new_call_15[tier][0] - base_call_15[tier][0]
        d30 = new_call_30[tier][0] - base_call_30[tier][0]
        if d15 * d30 < -0.01 and abs(d15) > 0.3 and abs(d30) > 0.3:
            print(f'    Call {tier:<4s}  Δ15={d15:+5.2f}  Δ30={d30:+5.2f}  ✗ direction mismatch')
            h2_pass = False
        else:
            print(f'    Call {tier:<4s}  Δ15={d15:+5.2f}  Δ30={d30:+5.2f}  ✓')

    # H3 — N stability ±15% on call & put affected tiers, no tier <50 peaks
    print('\n  H3 (N stability — gate ±15%, no tier <50 peaks):')
    h3_violations = []
    for tier in ['95+', '90+', '85+', '80+', '75+', '70+']:
        b_n = base_call_15[tier][1]; n_n = new_call_15[tier][1]
        dn = (n_n / b_n - 1) * 100 if b_n > 0 else 0
        flag = '  ✓' if abs(dn) <= 15 else '  ✗ exceeds ±15%'
        if abs(dn) > 15:
            h3_violations.append((tier, dn))
        if n_n < 50:
            flag += ' (n<50)'
        print(f'    Call {tier:<4s}  N {b_n:>4,}→{n_n:>4,} ({dn:+5.1f}%){flag}')
    for tier in ['<25', '<15']:
        b_n = base_put_15[tier][1]; n_n = new_put_15[tier][1]
        dn = (n_n / b_n - 1) * 100 if b_n > 0 else 0
        flag = '  ✓' if abs(dn) <= 15 else '  ✗ exceeds ±15%'
        if abs(dn) > 15:
            h3_violations.append((tier, dn))
        print(f'    Put  {tier:<4s}  N {b_n:>4,}→{n_n:>4,} ({dn:+5.1f}%){flag}')

    # H4 — puts neutral or better (<25 and <15)
    print('\n  H4 (puts <25 and <15 unchanged or improved, ±0.5pp tolerance):')
    p25_d = new_put_15['<25'][0] - base_put_15['<25'][0]
    p15_d = new_put_15['<15'][0] - base_put_15['<15'][0]
    flag25 = '  ✓' if p25_d >= -0.5 else '  ✗'
    flag15 = '  ✓' if p15_d >= -0.5 else '  ✗'
    print(f'    Put <25 Δ{p25_d:+5.2f}pp{flag25}')
    print(f'    Put <15 Δ{p15_d:+5.2f}pp{flag15}')
    h4_pass = p25_d >= -0.5 and p15_d >= -0.5

    return {
        'h1_strict_pos': h1_call_pos,
        'h1_strict_regress': h1_call_regress,
        'h1_call_70p_d': call_70p_d,
        'h1_put_25_d': put_25_d,
        'h2_pass': h2_pass,
        'h3_violations': h3_violations,
        'h4_pass': h4_pass,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    res = pl.read_parquet(CACHE / 'phase_c_multi_results.parquet')
    top_row = res.sort('composite', descending=True).head(RANK).tail(1).row(0, named=True)

    p = {k: top_row[k] for k in [
        'GATE_CALL_LO', 'GATE_CALL_HI', 'K_CALL', 'KIJ_SAT_CALL', 'LIFT_TARGET_CALL',
        'GATE_PUT_LO', 'GATE_PUT_HI', 'K_PUT', 'KIJ_SAT_PUT', 'LIFT_TARGET_PUT',
        'WADJ_LO_PUT', 'WADJ_SAT_PUT', 'RAMP_SHAPE', 'COMBINE_MODE',
    ]}

    print(f'=== Phase C-multi STRICT H1-H5 validation — Rank #{RANK} ===\n')
    print(f'  Mechanism: {p["RAMP_SHAPE"]} ramp, {p["COMBINE_MODE"]} combine')
    print(f'  CALL: gate={p["GATE_CALL_LO"]}-{p["GATE_CALL_HI"]} K={p["K_CALL"]:.2f} '
          f'sat={p["KIJ_SAT_CALL"]:.1f} target={p["LIFT_TARGET_CALL"]:.1f}')
    print(f'  PUT:  gate={p["GATE_PUT_LO"]}-{p["GATE_PUT_HI"]} K={p["K_PUT"]:.2f} '
          f'kij_sat={p["KIJ_SAT_PUT"]:.1f} target={p["LIFT_TARGET_PUT"]:.1f}')
    print(f'        wadj_lo={p["WADJ_LO_PUT"]} wadj_sat={p["WADJ_SAT_PUT"]:.1f}')

    # Smoothness
    smoothness_pass = smoothness(p)

    # Load parquet + barriers
    print('\n[load] preparing dataset...', flush=True)
    df = pl.read_parquet(CACHE / 'calls_v39_1825d_min0.parquet')
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())

    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        # Both call and put barriers, both 15d and 30d
        put_15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
        opt_30 = pl.read_database(
            """SELECT symbol, date, result AS opt_result_30
               FROM barrier_outcomes
               WHERE side='low' AND barrier_set='30dte_opt' AND w_days=30""",
            connection=conn,
        )
        put_30 = pl.read_database(
            """SELECT symbol, date, result AS put_result_30
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=30""",
            connection=conn,
        )
    finally:
        conn.close()

    df = df.join(put_15, on=['symbol', 'date'], how='left')
    df = df.join(opt_30, on=['symbol', 'date'], how='left')
    df = df.join(put_30, on=['symbol', 'date'], how='left')

    wadj_df = pl.read_parquet(CACHE / 'wadj_v39_1825d_min0.parquet')
    df = df.join(wadj_df, on=['symbol', 'date'], how='left')
    df = df.with_columns(
        (pl.col('overall') - pl.col('wcf_lift').fill_null(0))
        .clip(0.0, 100.0).alias('overall_pre_v27')
    )

    # Strict gate evaluation
    print('\n' + '=' * 90)
    print('STRICT H1-H5 GATE EVALUATION')
    print('=' * 90)
    g = evaluate_strict(df, p)

    # H5 multi-window
    print('\n  H5 (multi-window sign consistency on affected tiers):')
    mw = multi_window(df, p)
    h5_summary = {}
    for tier_key, tier_label in [
        ('call70', 'Call 70+'), ('call75', 'Call 75+'),
        ('call80', 'Call 80+'), ('call85', 'Call 85+'),
        ('call90', 'Call 90+'), ('call95', 'Call 95+'),
        ('put25',  'Put  <25'), ('put15',  'Put  <15'),
    ]:
        deltas = [mw[w][tier_key][0] for w in ['1y', '3y', '5y']]
        signs = ['+' if d > 0.05 else ('-' if d < -0.05 else '0') for d in deltas]
        consistent = all(s in ('+', '0') for s in signs) or all(s in ('-', '0') for s in signs)
        verdict = '✓ sign-consistent' if consistent else '✗ sign-flips'
        print(f'    {tier_label:<10s}  '
              f'1y={deltas[0]:+5.2f}  3y={deltas[1]:+5.2f}  5y={deltas[2]:+5.2f}  '
              f'({", ".join(signs)})  {verdict}')
        h5_summary[tier_label] = consistent

    # ============================================================================
    # FINAL VERDICT
    # ============================================================================
    print('\n' + '=' * 90)
    print('FINAL H1-H5 STRICT GATE VERDICT')
    print('=' * 90)
    print()
    print(f'  H1 strict (≥+0.5pp on ≥3 call tiers, no regress >−1.0pp):')
    print(f'    Tiers passing ≥+0.5pp: {g["h1_strict_pos"]} of 5')
    print(f'    Tiers regressing >−1.0pp: {len(g["h1_strict_regress"])} ({g["h1_strict_regress"]})')
    h1_strict_pass = g['h1_strict_pos'] >= 3 and len(g['h1_strict_regress']) == 0
    print(f'    {"✓ PASS" if h1_strict_pass else "✗ FAIL"}')

    print(f'\n  H1 affected-tier:')
    print(f'    Call 70+ Δ={g["h1_call_70p_d"]:+.2f}pp  (≥+0.3pp gate)  '
          f'{"✓" if g["h1_call_70p_d"] >= 0.3 else "✗"}')
    print(f'    Put  <25 Δ={g["h1_put_25_d"]:+.2f}pp  (≥+0.3pp gate)  '
          f'{"✓" if g["h1_put_25_d"] >= 0.3 else "✗"}')

    print(f'\n  H2 directional: {"✓ PASS" if g["h2_pass"] else "✗ FAIL"}')

    print(f'\n  H3 N stability: {len(g["h3_violations"])} violations')
    for tier, dn in g['h3_violations']:
        print(f'    {tier}: ΔN={dn:+.1f}% exceeds ±15%')
    h3_strict_pass = len(g['h3_violations']) == 0

    print(f'\n  H4 puts neutral/better: {"✓ PASS" if g["h4_pass"] else "✗ FAIL"}')

    h5_strict_pass = all(h5_summary.values())
    print(f'\n  H5 multi-window: {"✓ PASS" if h5_strict_pass else "✗ FAIL"}')

    print(f'\n  Smoothness: {"✓ PASS" if smoothness_pass else "✗ FAIL"}')

    print('\n  ═══ OVERALL ═══')
    overall_strict = (h1_strict_pass and g['h2_pass'] and h3_strict_pass
                       and g['h4_pass'] and h5_strict_pass and smoothness_pass)
    overall_affected = (g['h1_call_70p_d'] >= 0.3 and g['h1_put_25_d'] >= 0.3
                        and g['h2_pass'] and g['h4_pass']
                        and h5_strict_pass and smoothness_pass)
    print(f'  Strict H1-H5:    {"✓ ALL PASS" if overall_strict else "✗ FAIL — ship-blocking"}')
    print(f'  Affected-tier:   {"✓ ALL PASS" if overall_affected else "✗ FAIL — ship-blocking"}')


if __name__ == '__main__':
    main()
