"""Multi-window H5 check for the wave-modulator top variant.
Reports ΔWR15 + ΔWR30 across 1y/3y/5y for the candidate.
"""
from __future__ import annotations
import io, sys, sqlite3, time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / 'calls_v45_1825.parquet'
LOG = open(ROOT / 'experiments' / 'weekly_volume' / 'wave_multiwindow.out', 'w', encoding='utf-8', buffering=1)
def L(m=''):
    LOG.write(m + '\n')
    LOG.flush()


sys.path.insert(0, str(ROOT / 'experiments' / 'weekly_volume'))
from wave_sweep import apply_wave_modulator


def cum_wr_n(df, score_col, tier, rcol):
    sub = df.filter(pl.col(score_col) >= tier).filter(pl.col(rcol).is_not_null())
    n = sub.height
    if n == 0:
        return None, 0
    wr = sub.select((pl.col(rcol) == 1).cast(pl.Float64).mean()).item() * 100
    return wr, n


def slice_window(df, days):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return df.filter(pl.col('date') >= cutoff)


def main():
    L('Wave-modulator multi-window  (v45 baseline, top-1 variant)')
    L('  gate=70-85, peak=0, width=0.08, K_LIFT=0.15, target=82')
    L('  climax_thresh=0.05, K_DAMPEN=0.40, target_dampen=55\n')

    df = pl.read_parquet(CALLS_PARQ).select(
        ['symbol', 'date', 'overall', 'wv_force1', 'wv_cmf4', 'opt_result_15']
    )
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    conn = sqlite3.connect(str(CACHE_DB))
    barriers30 = pl.read_database(
        f"""SELECT symbol, date, result AS opt_result_30
            FROM barrier_outcomes
            WHERE side='low' AND barrier_set='30dte_opt'
              AND date >= '{cutoff}' AND w_days = 30""",
        connection=conn,
    )
    conn.close()
    df = df.join(barriers30, on=['symbol', 'date'], how='left')

    params = dict(gate_lo=70, gate_hi=85, score_power=1.0,
                  peak=0.0, width=0.08, k_lift=0.15, target_lift=82,
                  climax_thresh=0.05, climax_sat=0.15,
                  k_dampen=0.40, target_dampen=55)

    new_df = apply_wave_modulator(df, **params)

    sign_track = {(t, w): [] for t in [75, 80, 85, 90] for w in [15, 30]}

    for label, days in [('1y', 365), ('3y', 1095), ('5y', 1825)]:
        L('=' * 90)
        L(f'  Window {label}')
        L('=' * 90)
        sub_b = slice_window(df, days)
        sub_n = slice_window(new_df, days)
        for w_days in [15, 30]:
            rcol = f'opt_result_{w_days}'
            row = f'  {label} TP{w_days}: '
            for t in [75, 80, 85, 90]:
                bw, bn = cum_wr_n(sub_b, 'overall', t, rcol)
                nw, nn = cum_wr_n(sub_n, 'new_overall', t, rcol)
                d_wr = (nw - bw) if (nw is not None and bw is not None) else None
                d_n = ((nn - bn) / bn * 100) if bn else None
                if d_wr is None:
                    row += f' +{t}: N/A '
                else:
                    row += f' +{t}: {d_wr:>+5.2f}/{d_n:>+5.1f}%   '
                    sign_track[(t, w_days)].append(d_wr)
            L(row)
        L('')

    L('=' * 90)
    L('  H5 sign-consistency (3 windows × 2 barriers = 6 cells per tier)')
    L('=' * 90)
    for t in [75, 80, 85, 90]:
        all_signs = [v for k, lst in sign_track.items() if k[0] == t for v in lst]
        pos = sum(1 for v in all_signs if v > 0)
        neg = sum(1 for v in all_signs if v < 0)
        zero = sum(1 for v in all_signs if v == 0)
        tag = '✓' if neg == 0 else ('⚠' if neg <= 1 else '✗')
        L(f'    +{t}: {tag} pos={pos} neg={neg} zero={zero}    {all_signs}')


if __name__ == '__main__':
    main()
