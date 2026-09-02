"""H5 multi-window validation for Phase G winner."""
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

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

RANK = int(sys.argv[1]) if len(sys.argv) > 1 else 1
RESULTS = sys.argv[2] if len(sys.argv) > 2 else 'phase_g_asymmetric_v43_results.parquet'

# Detect Phase H format (different ramp keys) vs Phase G
_IS_PHASE_H = 'phase_h' in RESULTS


def apply_phase_g(df: pl.DataFrame, p: dict) -> pl.DataFrame:
    """Phase G/H dampener (separate call/put ramp shapes)."""
    if _IS_PHASE_H:
        call_shape = p['IND_RAMP_CALL']
        put_shape = p['IND_RAMP_PUT']
    else:
        call_shape = p['IND_RAMP_SHAPE']
        put_shape = 'log'

    def _ramp_for(shape):
        if shape == 'log':
            return lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
        return lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    call_ramp = _ramp_for(call_shape)
    put_ramp = _ramp_for(put_shape)

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    score_range = max(1, p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    score_norm_c = ((pl.col('overall') - p['GATE_CALL_LO']) / score_range).clip(0.0, None)
    k_base = p['K_CALL_BASE'] if 'K_CALL_BASE' in p else p['K_CALL']
    k_eff_c = k_base * score_norm_c.pow(p['K_CALL_POWER'])
    ind_grad_c = call_ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_d_full = -k_eff_c * ind_grad_c * (pl.col('overall') - p['LIFT_TARGET_CALL'])
    call_d = pl.when(pl.col('overall') >= p['GATE_CALL_LO']).then(call_d_full).otherwise(0.0)

    put_sg = put_ramp(p['GATE_PUT_HI'] - pl.col('overall'),
                     p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = put_ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_d = p['K_PUT'] * put_sg * put_ig * (p['LIFT_TARGET_PUT'] - pl.col('overall'))

    return df.with_columns(
        (pl.col('overall') + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0).alias('overall_new')
    )


def main():
    res = pl.read_parquet(CACHE / RESULTS)
    passing = res.filter(pl.col('both_h1_h3_call')).sort('composite', descending=True)
    if len(passing) < RANK:
        print(f'[ERROR] Rank #{RANK} not in passing set ({len(passing)} pass)', flush=True)
        return
    p = passing.head(RANK).tail(1).row(0, named=True)

    phase_label = 'H' if _IS_PHASE_H else 'G'
    print(f'=== Phase {phase_label} Rank #{RANK} multi-window validation (v43 baseline) ===\n')
    if _IS_PHASE_H:
        print(f'  CALL_RAMP={p["IND_RAMP_CALL"]}  PUT_RAMP={p["IND_RAMP_PUT"]}')
    else:
        print(f'  IND_RAMP={p["IND_RAMP_SHAPE"]}')
    k_base = p['K_CALL_BASE'] if 'K_CALL_BASE' in p else p['K_CALL']
    print(f'  CALL: gate={p["GATE_CALL_LO"]}-{p["GATE_CALL_HI"]} '
          f'K_BASE={k_base:.3f} POWER={p["K_CALL_POWER"]:.2f} '
          f'sat={p["KIJ_SAT_CALL"]:.1f} target={p["LIFT_TARGET_CALL"]:.1f}')
    print(f'  PUT:  gate={p["GATE_PUT_LO"]}-{p["GATE_PUT_HI"]} '
          f'K={p["K_PUT"]:.3f} sat={p["KIJ_SAT_PUT"]:.2f} target={p["LIFT_TARGET_PUT"]:.2f}')

    df = pl.read_parquet(CACHE / 'calls_v43_1825d_min0.parquet')
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())

    print('\n[load] put-side WR15 + WR30...', flush=True)
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
        opt_wr30 = pl.read_database(
            """SELECT symbol, date, result AS opt_result_30
               FROM barrier_outcomes WHERE side='low' AND barrier_set='30dte_opt' AND w_days=30""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    df = df.join(opt_wr30, on=['symbol', 'date'], how='left')
    df_v = apply_phase_g(df, p)

    today = date.today()
    print('\nMulti-window 1y/3y/5y deltas (vs v43 baseline):')
    print(f'\n{"Tier":<10s} {"1y ΔWR":<10s} {"3y ΔWR":<10s} {"5y ΔWR":<10s} {"signs":<10s} {"verdict":<28s}')
    print('-' * 80)

    summary = {}
    for tier_label, mask_new, mask_base, col in [
        ('Call 95+', pl.col('overall_new') >= 95, pl.col('overall') >= 95, 'opt_result_15'),
        ('Call 90+', pl.col('overall_new') >= 90, pl.col('overall') >= 90, 'opt_result_15'),
        ('Call 85+', pl.col('overall_new') >= 85, pl.col('overall') >= 85, 'opt_result_15'),
        ('Call 80+', pl.col('overall_new') >= 80, pl.col('overall') >= 80, 'opt_result_15'),
        ('Call 75+', pl.col('overall_new') >= 75, pl.col('overall') >= 75, 'opt_result_15'),
        ('Call 70+', pl.col('overall_new') >= 70, pl.col('overall') >= 70, 'opt_result_15'),
        ('Put <25',  pl.col('overall_new') <= 25, pl.col('overall') <= 25, 'put_result_15'),
        ('Put <15',  pl.col('overall_new') <= 15, pl.col('overall') <= 15, 'put_result_15'),
    ]:
        deltas = []
        ns_base = []
        for days, _ in [(365, '1y'), (1095, '3y'), (1825, '5y')]:
            cutoff = (today - timedelta(days=days)).isoformat()
            sub = df_v.filter(pl.col('date') >= cutoff)
            new = sub.filter(mask_new & pl.col(col).is_not_null())
            base = sub.filter(mask_base & pl.col(col).is_not_null())
            if len(base) == 0:
                deltas.append(0); ns_base.append(0)
                continue
            wr_base = float(base[col].mean()) * 100
            wr_new = float(new[col].mean()) * 100 if len(new) else float('nan')
            deltas.append(wr_new - wr_base)
            ns_base.append(len(base))

        signs = ['+' if d > 0.05 else ('-' if d < -0.05 else '0') for d in deltas]
        consistent = (all(s in ('+', '0') for s in signs) or
                      all(s in ('-', '0') for s in signs))
        verdict = '✓ sign-consistent' if consistent else '✗ sign-flips'
        if ns_base[0] < 30:
            verdict += f'  (1y N={ns_base[0]}; small)'
        print(f'{tier_label:<10s} {deltas[0]:>+8.2f}  {deltas[1]:>+8.2f}  {deltas[2]:>+8.2f}  '
              f'{",".join(signs):<8s}  {verdict}')
        summary[tier_label] = (consistent, deltas)

    print('\nH2 directional check (WR15 vs WR30) — 5y window:')
    for tier_label, mask_new, mask_base in [
        ('Call 95+', pl.col('overall_new') >= 95, pl.col('overall') >= 95),
        ('Call 90+', pl.col('overall_new') >= 90, pl.col('overall') >= 90),
        ('Call 85+', pl.col('overall_new') >= 85, pl.col('overall') >= 85),
        ('Call 80+', pl.col('overall_new') >= 80, pl.col('overall') >= 80),
    ]:
        new = df_v.filter(mask_new & pl.col('opt_result_15').is_not_null())
        base = df_v.filter(mask_base & pl.col('opt_result_15').is_not_null())
        d15 = float(new['opt_result_15'].mean()) * 100 - float(base['opt_result_15'].mean()) * 100 \
            if len(new) and len(base) else 0
        new30 = df_v.filter(mask_new & pl.col('opt_result_30').is_not_null())
        base30 = df_v.filter(mask_base & pl.col('opt_result_30').is_not_null())
        d30 = float(new30['opt_result_30'].mean()) * 100 - float(base30['opt_result_30'].mean()) * 100 \
            if len(new30) and len(base30) else 0
        same_dir = (d15 * d30 >= 0) or (abs(d15) < 0.3 and abs(d30) < 0.3)
        print(f'  {tier_label:<10s}  Δ15={d15:+5.2f}  Δ30={d30:+5.2f}  '
              f'{"✓" if same_dir else "✗"}')

    print('\n' + '=' * 70)
    print('VERDICT')
    print('=' * 70)
    h5_pass = all(c for c, _ in summary.values())
    print(f'  H5 multi-window all tiers sign-consistent: {"✓ PASS" if h5_pass else "✗ FAIL"}')
    if not h5_pass:
        flips = [t for t, (c, _) in summary.items() if not c]
        print(f'    Tiers with sign-flips: {flips}')


if __name__ == '__main__':
    main()
