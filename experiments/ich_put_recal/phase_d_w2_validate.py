"""Phase D — W2 multi-barrier-window directional consistency for asymK winner.

Loads barrier outcomes at W=3, 5, 7, 15, 30 for v43 puts, applies the
asymK_winner ICH variant, and checks all five barrier widths move same
direction on the surviving cohort.
"""
from __future__ import annotations
import io, math, sqlite3, sys
from pathlib import Path
from collections import defaultdict
from datetime import date, timedelta

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
V43_PARQUET = ROOT / '.cache' / 'ich_put_recal' / 'puts_v43_1825d.parquet'
PUT_GATE_OUT = 25


def _ramp_log(x, sat):
    if sat <= 0: return 0.0
    z = max(0.0, x) / sat
    if z <= 0: return 0.0
    if z >= 1: return 1.0
    return math.log1p(z * (math.e - 1))


def apply_asymk_winner(overall, kijun_pct):
    K, POWER, GATE_LO, GATE_HI = 0.42, 2.12, 18, 25
    KIJ_SAT, TARGET = 10.7, 33.4
    if kijun_pct >= 0 or overall > GATE_HI or overall <= GATE_LO:
        return overall
    score_norm = max(0.0, min(1.0, (overall - GATE_LO) / max(1, GATE_HI - GATE_LO)))
    k_eff = K * (score_norm ** POWER)
    ig = _ramp_log(-kijun_pct, KIJ_SAT)
    if k_eff <= 0 or ig <= 0:
        return overall
    lift = k_eff * ig * (TARGET - overall)
    return int(max(0, min(100, round(overall + lift)))) if lift > 0 else overall


def load_wr_at_w(symbols, w_days):
    """Load barrier outcomes at given W, for the symbols set."""
    placeholders = ','.join('?' for _ in symbols)
    conn = sqlite3.connect(str(ROOT / '.cache' / 'barrier_outcomes.db'))
    try:
        cur = conn.execute(f"""
            SELECT symbol, date, result
            FROM barrier_outcomes
            WHERE side='high' AND w_days={w_days}
              AND barrier_set='30dte_generic'
              AND symbol IN ({placeholders})
        """, symbols)
        rows = cur.fetchall()
    finally:
        conn.close()
    syms_col = [r[0] for r in rows]
    dates_col = [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1])[:10] for r in rows]
    res_col = [int(r[2]) if r[2] is not None else None for r in rows]
    df = pl.DataFrame({
        'symbol': pl.Series(syms_col, dtype=pl.Utf8),
        'date':   pl.Series(dates_col, dtype=pl.Utf8),
        f'wr{w_days}': pl.Series(res_col, dtype=pl.Int64),
    })
    return df.filter(pl.col(f'wr{w_days}').is_not_null())


def main():
    df = pl.read_parquet(V43_PARQUET)  # has wr7 column
    print(f'v43 puts loaded: {len(df):,}')

    # Apply asymK winner to compute new_score
    overalls = df['overall'].to_numpy()
    kjs = df['kijun_pct'].to_numpy()
    new_scores = [apply_asymk_winner(int(overalls[i]), float(kjs[i]))
                  for i in range(len(df))]
    df = df.with_columns(pl.Series('new_score', new_scores, dtype=pl.Int64))

    syms = sorted(df['symbol'].unique().to_list())

    # Load WR3, WR5, WR15, WR30 (WR7 already in parquet)
    print('Loading multi-window barrier outcomes...')
    for w in (3, 5, 15, 30):
        bo = load_wr_at_w(syms, w)
        df = df.join(bo, on=['symbol', 'date'], how='left')
        print(f'  W={w}: {df[f"wr{w}"].is_not_null().sum():,} non-null', flush=True)

    # Compare baseline (no ICH) vs asymK winner
    base = df.filter(pl.col('overall') <= PUT_GATE_OUT)
    survived = df.filter(pl.col('new_score') <= PUT_GATE_OUT)

    print()
    print('=' * 100)
    print('W2 MULTI-BARRIER-WINDOW CONSISTENCY (asymK_winner vs v43 baseline)')
    print('=' * 100)
    print(f'Baseline (no ICH): N={len(base):,}')
    print(f'asymK_winner kept: N={len(survived):,} (-{len(base) - len(survived):,} displaced)')
    print()

    print('{:>4} | {:>10} | {:>10} | {:>10} | {:>10}'.format(
        'W', 'base WR%', 'asymK WR%', 'lift_pp', 'sign'))
    print('-' * 60)

    all_positive = True
    for w in (3, 5, 7, 15, 30):
        b = base.filter(pl.col(f'wr{w}').is_not_null())
        s = survived.filter(pl.col(f'wr{w}').is_not_null())
        if len(b) < 100 or len(s) < 100:
            print('{:>4} {:>15}'.format(w, 'small N'))
            continue
        wb = float(b[f'wr{w}'].mean()) * 100
        ws = float(s[f'wr{w}'].mean()) * 100
        lift = ws - wb
        sign = '+' if lift > 0 else '-' if lift < 0 else '='
        if lift <= 0:
            all_positive = False
        print('{:>4} | {:>10.2f} | {:>10.2f} | {:>+10.2f} | {:>10}'.format(
            w, wb, ws, lift, sign))

    print()
    print('W2 VERDICT:', 'PASS — all 5 barrier windows positive' if all_positive
          else 'FAIL — at least one barrier window does not move with WR7')

    # W3: multi-time-window slicing (1y / 3y / 5y)
    print()
    print('=' * 100)
    print('W3 MULTI-TIME-WINDOW WR7 CONSISTENCY')
    print('=' * 100)

    today = date.today()
    cutoff_iso = today.isoformat()
    try:
        from strategy_config import CALIBRATION_CUTOFF_DATE
        if cutoff_iso > CALIBRATION_CUTOFF_DATE:
            cutoff_iso = CALIBRATION_CUTOFF_DATE
    except Exception:
        pass
    cutoff = date.fromisoformat(cutoff_iso)

    print('{:>6} | {:>8} | {:>8} | {:>10}'.format('window', 'base WR7', 'asymK WR7', 'lift_pp'))
    print('-' * 60)
    for years, label in [(1, '1y'), (3, '3y'), (5, '5y')]:
        start = (cutoff - timedelta(days=365 * years)).isoformat()
        b = base.filter(pl.col('date') >= start)
        s = survived.filter(pl.col('date') >= start)
        if len(b) < 50 or len(s) < 50:
            print('{:>6} | {:>8}'.format(label, 'small N'))
            continue
        wb = float(b['wr7'].mean()) * 100
        ws = float(s['wr7'].mean()) * 100
        print('{:>6} | {:>8.2f} | {:>8.2f} | {:>+10.2f}'.format(
            label, wb, ws, ws - wb))


if __name__ == '__main__':
    main()
