"""Phase B addendum — apples-to-apples: asymmetric-K winner vs v44-current ICH.

Both applied to the SAME v43 baseline puts data, so we can directly compare
the lift each mechanism extracts.
"""
from __future__ import annotations
import io, math, sys
from pathlib import Path

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


def _ramp_linear(x, sat):
    if sat <= 0: return 0.0
    return min(1.0, max(0.0, x / sat))


def apply_v44_current(overall, kijun_pct):
    """Current v44 ICH put-side: symmetric log ramp."""
    GATE_LO, GATE_HI = 10, 27
    K, KIJ_SAT, TARGET = 0.278, 8.8, 33.4
    if kijun_pct >= 0 or overall > GATE_HI:
        return overall
    sg_p = _ramp_log(GATE_HI - overall, GATE_HI - GATE_LO)
    ig_p = _ramp_log(-kijun_pct, KIJ_SAT)
    w = sg_p * ig_p
    if w <= 0:
        return overall
    lift = K * w * (TARGET - overall)
    return int(max(0, min(100, round(overall + lift))))


def apply_asym_k(overall, kijun_pct, K, POWER, GATE_LO, GATE_HI, KIJ_SAT, TARGET, ind_ramp):
    """Asymmetric-K: score_norm peaks at boundary."""
    if kijun_pct >= 0 or overall > GATE_HI or overall <= GATE_LO:
        return overall
    score_range = max(1, GATE_HI - GATE_LO)
    score_norm = max(0.0, min(1.0, (overall - GATE_LO) / score_range))
    k_eff = K * (score_norm ** POWER)
    ig = _ramp_linear(-kijun_pct, KIJ_SAT) if ind_ramp == 'linear' else _ramp_log(-kijun_pct, KIJ_SAT)
    if k_eff <= 0 or ig <= 0:
        return overall
    lift = k_eff * ig * (TARGET - overall)
    return int(max(0, min(100, round(overall + lift)))) if lift > 0 else overall


def summarize(df, label):
    sub = df.filter(pl.col('new_score') <= PUT_GATE_OUT)
    if len(sub) == 0:
        return None
    out = {'label': label, 'n_kept': len(sub),
           'wr7_kept': float(sub['wr7'].mean()) * 100}
    for lo, hi, lbl in [(0,5,'5'), (0,10,'10'), (0,15,'15'),
                         (0,20,'20'), (0,25,'25')]:
        b = sub.filter(pl.col('new_score').is_between(lo, hi))
        out[f'wr_le{lbl}'] = float(b['wr7'].mean()) * 100 if len(b) > 0 else None
        out[f'n_le{lbl}'] = len(b)
    return out


def main():
    df = pl.read_parquet(V43_PARQUET)
    print(f'v43 baseline: {len(df):,} puts <=30')

    overalls = df['overall'].to_numpy()
    kjs = df['kijun_pct'].to_numpy()

    # Variants to compare
    variants = {
        'v43_baseline (no ICH)': lambda i: int(overalls[i]),
        'v44_current_ICH (sym log)': lambda i: apply_v44_current(int(overalls[i]), float(kjs[i])),
        'asymK_winner (K=0.42 POW=2.12 LO=18 HI=25 SAT=10.7 TGT=33.4 log)':
            lambda i: apply_asym_k(int(overalls[i]), float(kjs[i]),
                                   0.42, 2.12, 18, 25, 10.7, 33.4, 'log'),
        'asymK_strong (K=0.54 POW=1.66 LO=18 HI=30 SAT=5.0 TGT=35.1 lin)':
            lambda i: apply_asym_k(int(overalls[i]), float(kjs[i]),
                                   0.54, 1.66, 18, 30, 5.0, 35.1, 'linear'),
        'asymK_safe (K=0.30 POW=2.46 LO=12 HI=26 SAT=4.5 TGT=35.4 log)':
            lambda i: apply_asym_k(int(overalls[i]), float(kjs[i]),
                                   0.30, 2.46, 12, 26, 4.5, 35.4, 'log'),
    }

    rows = []
    for name, fn in variants.items():
        new_scores = [fn(i) for i in range(len(df))]
        work = df.with_columns(pl.Series('new_score', new_scores, dtype=pl.Int64))
        s = summarize(work, name)
        if s:
            rows.append(s)

    print()
    print('=' * 110)
    print('VARIANT COMPARISON (v43 baseline → variant ICH applied → cumulative WR7 by put bucket)')
    print('=' * 110)
    hdr = '{:>55} | {:>6} | {:>6} | {:>14} | {:>14} | {:>14} | {:>14} | {:>14}'
    print(hdr.format('variant', 'n_kept', 'WR7%', '<=5  WR/N', '<=10 WR/N', '<=15 WR/N', '<=20 WR/N', '<=25 WR/N'))
    print('-' * 200)
    for r in rows:
        cells = []
        for lbl in ('5', '10', '15', '20', '25'):
            wr, n = r[f'wr_le{lbl}'], r[f'n_le{lbl}']
            cells.append('{:>5.2f}/{:>5}'.format(wr, n) if wr else '   --/--')
        print(hdr.format(r['label'][:55], r['n_kept'],
                         '{:.2f}'.format(r['wr7_kept']),
                         *cells))


if __name__ == '__main__':
    main()
