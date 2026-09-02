"""Phase C validation: multi-window check + smoothness verification on the
winning Bayesian config.

Two H5 questions answered here:
  1. Multi-window sign consistency: does the affected-tier alpha hold at 1y, 3y, 5y?
  2. Response curve smoothness: are score_grad × ind_grad surfaces clean log waves
     or are there cliffs / non-monotonicities that suggest overfitting?

Reads phase_c_sweep_results.parquet, picks top config by composite, validates.

Usage:
    python experiments/weekly_avwap/phase_c_validate.py [rank]      # default rank=1
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl
import numpy as np
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'

RANK = int(sys.argv[1]) if len(sys.argv) > 1 else 1


def _ramp_log(x, sat):
    """Numpy version for plotting."""
    x = np.maximum(0.0, x)
    return np.clip(np.log1p(x) / np.log1p(sat), 0, 1)


def _ramp_linear(x, sat):
    x = np.maximum(0.0, x)
    return np.clip(x / sat, 0, 1)


def smoothness_plot_text(p: dict) -> None:
    """ASCII plot of the response curves for the winning config."""
    ramp = _ramp_log if p['RAMP_SHAPE'] == 'log' else _ramp_linear

    print(f'\n--- Smoothness check for top config (RAMP={p["RAMP_SHAPE"]}) ---')

    # 1. Call-side score gradient: ramp(overall - GATE_CALL_LO)
    print(f'\n  1. Call score_grad over overall (gate {p["GATE_CALL_LO"]}-{p["GATE_CALL_HI"]}):')
    for ov in range(50, 100, 2):
        x = ov - p['GATE_CALL_LO']
        sat = p['GATE_CALL_HI'] - p['GATE_CALL_LO']
        g = ramp(np.array([x]), sat)[0]
        bar = '█' * int(g * 40)
        print(f'    overall={ov:>3d}  grad={g:.3f}  {bar}')

    # 2. Put-side score gradient: ramp(GATE_PUT_HI - overall)
    print(f'\n  2. Put score_grad over overall (gate {p["GATE_PUT_LO"]}-{p["GATE_PUT_HI"]}):')
    for ov in range(0, 35, 2):
        x = p['GATE_PUT_HI'] - ov
        sat = p['GATE_PUT_HI'] - p['GATE_PUT_LO']
        g = ramp(np.array([x]), sat)[0]
        bar = '█' * int(g * 40)
        print(f'    overall={ov:>3d}  grad={g:.3f}  {bar}')

    # 3. Indicator gradient (kijun_pct → ind_grad)
    print(f'\n  3. Indicator grad over price_vs_kijun_pct '
          f'(call SAT={p["KIJ_SAT_CALL"]}, put SAT={p["KIJ_SAT_PUT"]}):')
    for kij in range(-30, 31, 5):
        ind_dist = max(0, -kij)
        gc = ramp(np.array([ind_dist]), p['KIJ_SAT_CALL'])[0]
        gp = ramp(np.array([ind_dist]), p['KIJ_SAT_PUT'])[0]
        barC = '█' * int(gc * 30)
        barP = '·' * int(gp * 30)
        print(f'    kijun_pct={kij:>+4d}  call={gc:.3f}  put={gp:.3f}  {barC}|{barP}')

    # 4. Smoothness assertions: monotonicity check
    print('\n  4. Monotonicity check:')
    # Call score_grad must be monotonically non-decreasing in overall
    overalls = np.arange(50, 100)
    call_grads = ramp(overalls - p['GATE_CALL_LO'],
                      p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    if np.all(np.diff(call_grads) >= -1e-9):
        print('    ✓ Call score_grad is monotonically non-decreasing')
    else:
        print('    ✗ Call score_grad has non-monotonic regions')

    # Put score_grad must be monotonically non-increasing in overall
    overalls_p = np.arange(0, 35)
    put_grads = ramp(p['GATE_PUT_HI'] - overalls_p,
                     p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    if np.all(np.diff(put_grads) <= 1e-9):
        print('    ✓ Put score_grad is monotonically non-increasing')
    else:
        print('    ✗ Put score_grad has non-monotonic regions')

    # Indicator grad must be monotonically non-decreasing in (negative) kijun_pct
    kijuns = np.arange(0, 50)  # ind_dist values
    call_ind = ramp(kijuns, p['KIJ_SAT_CALL'])
    put_ind = ramp(kijuns, p['KIJ_SAT_PUT'])
    if np.all(np.diff(call_ind) >= -1e-9) and np.all(np.diff(put_ind) >= -1e-9):
        print('    ✓ Indicator grad is monotonically non-decreasing (call + put)')
    else:
        print('    ✗ Indicator grad has non-monotonic regions')


def apply_ichimoku_np(df: pl.DataFrame, p: dict) -> pl.DataFrame:
    """Same as phase_c_sweep.apply_ichimoku, copied here for self-containment."""
    if p['RAMP_SHAPE'] == 'log':
        ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    call_sg = ramp(pl.col('overall') - p['GATE_CALL_LO'],
                   p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    call_ig = ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_w = call_sg * call_ig
    call_d = -p['K_CALL'] * call_w * (pl.col('overall') - p['LIFT_TARGET_CALL'])

    put_sg = ramp(p['GATE_PUT_HI'] - pl.col('overall'),
                  p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_w = put_sg * put_ig
    put_d = p['K_PUT'] * put_w * (p['LIFT_TARGET_PUT'] - pl.col('overall'))

    return df.with_columns(
        (pl.col('overall') + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )


def multi_window_check(df: pl.DataFrame, p: dict) -> None:
    """Compute affected-tier WR15 deltas at 1y, 3y, 5y separately."""
    today = date.today()
    windows = [(365, '1y'), (1095, '3y'), (1825, '5y')]

    print('\n' + '=' * 90)
    print('Multi-window H5 sign consistency check (affected tiers only)')
    print('=' * 90)

    df_v = apply_ichimoku_np(df, p)

    print(f'\n{"window":<8s}{"call_70+ ΔWR / ΔN":<22s}{"call_75+ ΔWR / ΔN":<22s}'
          f'{"put<25 ΔWR / ΔN":<22s}{"put<20 ΔWR / ΔN":<22s}')
    print('-' * 100)

    rows = []
    for days, label in windows:
        cutoff_date = (today - timedelta(days=days)).isoformat()
        sub = df_v.filter(pl.col('date') >= cutoff_date)

        def wr_dn(mask, col, base_mask):
            base = sub.filter(base_mask & pl.col(col).is_not_null())
            new = sub.filter(mask & pl.col(col).is_not_null())
            if len(base) == 0:
                return 0, 0, 0
            return (
                (float(new[col].mean()) - float(base[col].mean())) * 100 if len(new) else 0,
                len(base),
                (len(new) / len(base) - 1) * 100 if len(base) else 0,
            )

        c70_d, c70_b, c70_n = wr_dn(pl.col('overall_new') >= 70, 'opt_result_15',
                                     pl.col('overall') >= 70)
        c75_d, c75_b, c75_n = wr_dn(pl.col('overall_new') >= 75, 'opt_result_15',
                                     pl.col('overall') >= 75)
        p25_d, p25_b, p25_n = wr_dn(pl.col('overall_new') <= 25, 'put_result_15',
                                     pl.col('overall') <= 25)
        p20_d, p20_b, p20_n = wr_dn(pl.col('overall_new') <= 20, 'put_result_15',
                                     pl.col('overall') <= 20)

        print(f'{label:<8s}'
              f'{c70_d:>+5.2f}/{c70_n:>+5.1f}% (N={c70_b:>5,d})  '
              f'{c75_d:>+5.2f}/{c75_n:>+5.1f}% (N={c75_b:>5,d})  '
              f'{p25_d:>+5.2f}/{p25_n:>+5.1f}% (N={p25_b:>5,d})  '
              f'{p20_d:>+5.2f}/{p20_n:>+5.1f}% (N={p20_b:>5,d})')
        rows.append({
            'window': label,
            'call_70p_d': c70_d, 'call_75p_d': c75_d,
            'put_25_d': p25_d, 'put_20_d': p20_d,
        })

    # Sign consistency
    print('\nSign consistency (positive across all windows = ship-ready):')
    for tier_label, key in [('call_70+', 'call_70p_d'), ('call_75+', 'call_75p_d'),
                             ('put_<25', 'put_25_d'), ('put_<20', 'put_20_d')]:
        signs = ['+' if r[key] > 0 else ('-' if r[key] < 0 else '0') for r in rows]
        verdict = '✓ sign-consistent' if all(s == '+' for s in signs) else \
                  '~ mixed' if signs[-1] == '+' else \
                  '✗ regression at recent window'
        print(f'  {tier_label:<10s}  {",".join(signs)}  {verdict}')


def main():
    sweep_results = CACHE / 'phase_c_sweep_results.parquet'
    if not sweep_results.exists():
        print(f'[ERROR] {sweep_results} not found. Run phase_c_sweep.py first.', flush=True)
        return

    res = pl.read_parquet(sweep_results)
    top = res.sort('composite', descending=True).head(RANK).tail(1).row(0, named=True)

    print(f'=== Phase C validation — Rank #{RANK} config ===\n')
    print(f'  RAMP_SHAPE       = {top["RAMP_SHAPE"]}')
    print(f'  GATE_CALL_LO/HI  = {top["GATE_CALL_LO"]}-{top["GATE_CALL_HI"]}')
    print(f'  K_CALL           = {top["K_CALL"]:.3f}')
    print(f'  KIJ_SAT_CALL     = {top["KIJ_SAT_CALL"]:.2f}')
    print(f'  LIFT_TARGET_CALL = {top["LIFT_TARGET_CALL"]:.2f}')
    print(f'  GATE_PUT_LO/HI   = {top["GATE_PUT_LO"]}-{top["GATE_PUT_HI"]}')
    print(f'  K_PUT            = {top["K_PUT"]:.3f}')
    print(f'  KIJ_SAT_PUT      = {top["KIJ_SAT_PUT"]:.2f}')
    print(f'  LIFT_TARGET_PUT  = {top["LIFT_TARGET_PUT"]:.2f}')

    print(f'\n  Composite (5y):  {top["composite"]:+.3f}')
    print(f'  Call 70+ ΔWR:    {top["call_70p_dwr"]:+.2f}pp  (ΔN {top["call_70p_dn_pct"]:+.1f}%)')
    print(f'  Put  <25 ΔWR:    {top["put_lt25_dwr"]:+.2f}pp  (ΔN {top["put_lt25_dn_pct"]:+.1f}%)')

    # 1. Smoothness plot
    smoothness_plot_text(top)

    # 2. Multi-window check — needs the parquet with put_result_15 attached
    print('\n[load] reattaching parquet for multi-window check...', flush=True)
    df = pl.read_parquet(CACHE / 'calls_v39_1825d_min0.parquet')
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())

    import sqlite3
    BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'
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

    multi_window_check(df, top)


if __name__ == '__main__':
    main()
