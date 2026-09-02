"""H5 multi-window validation on Phase E top configs.

Quick validation pass: loads phase_e_refinement_results.parquet, picks the
specified rank from the constrained-passing subset, runs 1y/3y/5y check on
all call tiers + put<25.
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

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

RANK = int(sys.argv[1]) if len(sys.argv) > 1 else 1
RESULTS_FILE = sys.argv[2] if len(sys.argv) > 2 else 'phase_e_refinement_results.parquet'


def apply_ichimoku(df: pl.DataFrame, p: dict) -> pl.DataFrame:
    if p['RAMP_SHAPE'] == 'log':
        ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)
    sc = pl.col('overall')

    call_sg = ramp(sc - p['GATE_CALL_LO'], p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    call_ig = ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_d = -p['K_CALL'] * call_sg * call_ig * (sc - p['LIFT_TARGET_CALL'])

    put_sg = ramp(p['GATE_PUT_HI'] - sc, p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_d = p['K_PUT'] * put_sg * put_ig * (p['LIFT_TARGET_PUT'] - sc)

    return df.with_columns(
        (sc + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0).alias('overall_new')
    )


def main():
    res = pl.read_parquet(CACHE / RESULTS_FILE)
    passing = res.filter(pl.col('both_h1_h3_call')).sort('composite', descending=True)
    if len(passing) < RANK:
        print(f'[ERROR] Rank #{RANK} not in passing set (only {len(passing)} pass)', flush=True)
        return
    p = passing.head(RANK).tail(1).row(0, named=True)

    print(f'=== Phase E Rank #{RANK} multi-window validation ===\n')
    print(f'  RAMP={p["RAMP_SHAPE"]}  CALL: gate={p["GATE_CALL_LO"]}-{p["GATE_CALL_HI"]} '
          f'K={p["K_CALL"]:.2f} sat={p["KIJ_SAT_CALL"]:.1f} target={p["LIFT_TARGET_CALL"]:.1f}')
    print(f'  PUT (locked): gate={p["GATE_PUT_LO"]}-{p["GATE_PUT_HI"]} '
          f'K={p["K_PUT"]:.2f} sat={p["KIJ_SAT_PUT"]:.1f} target={p["LIFT_TARGET_PUT"]:.1f}\n')

    # Load
    # Infer parquet from RESULTS_FILE — phase_e_refinement_v43_results.parquet → calls_v43_1825d_min0.parquet
    if '_v' in RESULTS_FILE and '_results' in RESULTS_FILE:
        ver = RESULTS_FILE.split('_v', 1)[1].split('_results')[0]
        feature_pq = f'calls_v{ver}_1825d_min0.parquet'
    else:
        feature_pq = 'calls_v39_1825d_min0.parquet'
    df = pl.read_parquet(CACHE / feature_pq)
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())

    print('[load] put-side WR15...', flush=True)
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
        opt_wr30 = pl.read_database(
            """SELECT symbol, date, result AS opt_result_30
               FROM barrier_outcomes
               WHERE side='low' AND barrier_set='30dte_opt' AND w_days=30""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    df = df.join(opt_wr30, on=['symbol', 'date'], how='left')

    df_v = apply_ichimoku(df, p)

    today = date.today()
    print('\nH5 multi-window check (1y/3y/5y, all call tiers + puts):')
    print(f'\n{"Tier":<10s} {"1y ΔWR":<10s} {"3y ΔWR":<10s} {"5y ΔWR":<10s} {"signs":<10s} {"verdict":<20s}')
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
        ns_new = []
        for days, _ in [(365, '1y'), (1095, '3y'), (1825, '5y')]:
            cutoff = (today - timedelta(days=days)).isoformat()
            sub = df_v.filter(pl.col('date') >= cutoff)
            new = sub.filter(mask_new & pl.col(col).is_not_null())
            base = sub.filter(mask_base & pl.col(col).is_not_null())
            if len(base) == 0:
                deltas.append(0); ns_base.append(0); ns_new.append(0)
                continue
            wr_base = float(base[col].mean()) * 100
            wr_new = float(new[col].mean()) * 100 if len(new) else float('nan')
            deltas.append(wr_new - wr_base)
            ns_base.append(len(base))
            ns_new.append(len(new))

        signs = ['+' if d > 0.05 else ('-' if d < -0.05 else '0') for d in deltas]
        consistent = (all(s in ('+', '0') for s in signs) or
                      all(s in ('-', '0') for s in signs))
        verdict = '✓ sign-consistent' if consistent else '✗ sign-flips'
        # Note when 1y N is too small to trust
        if ns_base[0] < 30:
            verdict += f'  (1y N={ns_base[0]}; small)'
        print(f'{tier_label:<10s} {deltas[0]:>+8.2f}  {deltas[1]:>+8.2f}  {deltas[2]:>+8.2f}  '
              f'{",".join(signs):<8s}  {verdict}')
        summary[tier_label] = (consistent, deltas)

    # WR30 check on top tiers (H2)
    print('\nH2 directional check (WR15 vs WR30) — 5y window:')
    for tier_label, mask_new, mask_base in [
        ('Call 95+', pl.col('overall_new') >= 95, pl.col('overall') >= 95),
        ('Call 90+', pl.col('overall_new') >= 90, pl.col('overall') >= 90),
        ('Call 85+', pl.col('overall_new') >= 85, pl.col('overall') >= 85),
        ('Call 80+', pl.col('overall_new') >= 80, pl.col('overall') >= 80),
        ('Call 75+', pl.col('overall_new') >= 75, pl.col('overall') >= 75),
        ('Call 70+', pl.col('overall_new') >= 70, pl.col('overall') >= 70),
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

    # Verdict
    print('\n' + '=' * 70)
    print('FINAL VERDICT')
    print('=' * 70)
    h5_pass = all(c for c, _ in summary.values())
    print(f'  H5 multi-window all tiers sign-consistent: {"✓ PASS" if h5_pass else "✗ FAIL"}')
    if not h5_pass:
        flips = [t for t, (c, _) in summary.items() if not c]
        print(f'    Tiers with sign-flips: {flips}')


if __name__ == '__main__':
    main()
