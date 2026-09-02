"""Evaluate WVD ship candidate against BOTH 30dte_opt @ w=15 AND w=30 barriers.

Streams to a log file directly (avoids stdout-redirection issues).
Loads w=30 barriers ONCE per side, joins to pre-existing parquets in polars.
"""
from __future__ import annotations
import os, sys, sqlite3, time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
_VID = os.environ.get('WVD_VID', '45')
CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / f'calls_v{_VID}_1825.parquet'
PUTS_PARQ  = ROOT / '.cache' / 'weekly_volume' / f'puts_v{_VID}_1825.parquet'
LOG_PATH   = Path(__file__).parent / 'tp15_tp30_v45.out'

LOG = open(LOG_PATH, 'w', encoding='utf-8', buffering=1)
def L(msg=''):
    LOG.write(msg + '\n')
    LOG.flush()


sys.path.insert(0, str(ROOT / 'experiments' / 'weekly_volume'))
from sweep import apply_wvd_calls, apply_wvd_puts


def load_w30_barriers(side, cutoff_iso):
    conn = sqlite3.connect(str(CACHE_DB))
    L(f'[load] {side} w=30 barriers since {cutoff_iso} ...')
    t0 = time.time()
    barriers = pl.read_database(
        f"""SELECT symbol, date, result AS opt_result_30
            FROM barrier_outcomes
            WHERE side='{side}' AND barrier_set='30dte_opt'
              AND date >= '{cutoff_iso}' AND w_days = 30""",
        connection=conn,
    )
    conn.close()
    L(f'[load]   loaded {len(barriers):,} rows in {time.time()-t0:.0f}s')
    return barriers


def cum_wr_n(df, side, score_col, tier, result_col):
    if side == 'call':
        sub = df.filter(pl.col(score_col) >= tier)
    else:
        sub = df.filter(pl.col(score_col) <= tier)
    sub = sub.filter(pl.col(result_col).is_not_null())
    n = sub.height
    if n == 0:
        return None, 0
    wr = sub.select((pl.col(result_col) == 1).cast(pl.Float64).mean()).item() * 100
    return wr, n


def slice_window(df, days):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return df.filter(pl.col('date') >= cutoff)


def eval_call(df, params, w_days):
    new_df = apply_wvd_calls(df, params['gate_lo'], params['gate_hi'],
                              params['k_call'], params['k_power'],
                              params['force_sat'], params['target_call'])
    rcol = f'opt_result_{w_days}'
    out = {}
    for t in [75, 80, 85, 90]:
        b_wr, b_n = cum_wr_n(df, 'call', 'overall', t, rcol)
        n_wr, n_n = cum_wr_n(new_df, 'call', 'new_overall', t, rcol)
        out[t] = {
            'b_wr': b_wr, 'n_wr': n_wr, 'b_n': b_n, 'n_n': n_n,
            'd_wr': (n_wr - b_wr) if (n_wr is not None and b_wr is not None) else None,
            'd_n_pct': ((n_n - b_n) / b_n * 100) if b_n else None,
        }
    return out


def eval_put(df, params, w_days):
    new_df = apply_wvd_puts(df, params['put_lo'], params['put_hi'],
                              params['k_put'], params['k_put_power'],
                              params['cmf_sat'], params['target_put'])
    rcol = f'opt_result_{w_days}'
    out = {}
    for t in [25, 20, 15]:
        b_wr, b_n = cum_wr_n(df, 'put', 'overall', t, rcol)
        n_wr, n_n = cum_wr_n(new_df, 'put', 'new_overall', t, rcol)
        out[t] = {
            'b_wr': b_wr, 'n_wr': n_wr, 'b_n': b_n, 'n_n': n_n,
            'd_wr': (n_wr - b_wr) if (n_wr is not None and b_wr is not None) else None,
            'd_n_pct': ((n_n - b_n) / b_n * 100) if b_n else None,
        }
    return out


def main():
    L(f'WVD Eval — TP15 + TP30 + multi-window  (v{_VID} baseline)\n')

    calls = pl.read_parquet(CALLS_PARQ)
    puts  = pl.read_parquet(PUTS_PARQ)
    L(f'[setup] calls parquet: {len(calls):,}  puts parquet: {len(puts):,}')

    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    barriers_call_30 = load_w30_barriers('low', cutoff)
    barriers_put_30  = load_w30_barriers('high', cutoff)

    # Join w=30 barriers (calls already have opt_result_15 from build_features)
    calls_full = calls.select(['symbol','date','overall','wv_force1','wv_cmf4','wv_z12','opt_result_15']) \
                       .join(barriers_call_30, on=['symbol','date'], how='left')
    puts_full  = puts.select(['symbol','date','overall','wv_force1','wv_cmf4','wv_z12','opt_result_15']) \
                       .join(barriers_put_30, on=['symbol','date'], how='left')
    L(f'[setup] calls w15-resolved: {calls_full.filter(pl.col("opt_result_15").is_not_null()).height:,}')
    L(f'[setup] calls w30-resolved: {calls_full.filter(pl.col("opt_result_30").is_not_null()).height:,}')
    L(f'[setup] puts w15-resolved:  {puts_full.filter(pl.col("opt_result_15").is_not_null()).height:,}')
    L(f'[setup] puts w30-resolved:  {puts_full.filter(pl.col("opt_result_30").is_not_null()).height:,}\n')

    call_top = {
        'name': 'WVD-Call-Top  (gl70_gh85_k0.40_p1.5_fs0.10_t55) [v45-tuned]',
        'gate_lo': 70, 'gate_hi': 85, 'k_call': 0.40,
        'k_power': 1.5, 'force_sat': 0.10, 'target_call': 55.0,
    }
    call_orig = {
        'name': 'WVD-Call-Orig (gl70_gh95_k0.20_p1.0_fs0.15_t60) [v44-original, milder]',
        'gate_lo': 70, 'gate_hi': 95, 'k_call': 0.20,
        'k_power': 1.0, 'force_sat': 0.15, 'target_call': 60.0,
    }
    put_top = {
        'name': 'WVD-Put-Top   (pl10_ph25_k0.50_p1.0_cs0.40_t33) [v45-tuned]',
        'put_lo': 10, 'put_hi': 25, 'k_put': 0.50,
        'k_put_power': 1.0, 'cmf_sat': 0.40, 'target_put': 33.0,
    }
    put_orig = {
        'name': 'WVD-Put-Orig  (pl5_ph25_k0.50_p1.0_cs0.30_t33) [v44-original]',
        'put_lo': 5, 'put_hi': 25, 'k_put': 0.50,
        'k_put_power': 1.0, 'cmf_sat': 0.30, 'target_put': 33.0,
    }

    windows = [('1y', 365), ('3y', 1095), ('5y', 1825)]

    for cand in [call_top, call_orig]:
        L('=' * 130)
        L(f'  {cand["name"]}')
        L('=' * 130)
        L(f'  {"window":<6s} {"barr":<6s} {"+75 (ΔWR/ΔN)":<18s} {"+80 (ΔWR/ΔN)":<18s} {"+85 (ΔWR/ΔN)":<18s} {"+90 (ΔWR/ΔN)":<18s}')
        L('  ' + '-' * 96)
        sign_track = {(t, w): [] for t in [75, 80, 85, 90] for w in [15, 30]}
        for label, days in windows:
            for w_days in [15, 30]:
                sub = slice_window(calls_full, days)
                results = eval_call(sub, cand, w_days)
                row = f'  {label:<6s} TP{w_days:<4d}'
                for t in [75, 80, 85, 90]:
                    r = results.get(t, {})
                    if r.get('d_wr') is None:
                        row += f' {"N/A":<18s}'
                    else:
                        row += f' +{t}: {r["d_wr"]:>+5.2f}/{r["d_n_pct"]:>+5.1f}%   '
                        sign_track[(t, w_days)].append(r['d_wr'])
                L(row)
            L('')
        # Summary sign-consistency
        L('  ' + '-' * 96)
        L(f'  H5 sign-consistency (3 windows × 2 barriers = 6 cells per tier):')
        for t in [75, 80, 85, 90]:
            tot_pos = sum(1 for k, lst in sign_track.items() if k[0] == t for v in lst if v > 0)
            tot_neg = sum(1 for k, lst in sign_track.items() if k[0] == t for v in lst if v < 0)
            tot_zero = sum(1 for k, lst in sign_track.items() if k[0] == t for v in lst if v == 0)
            tag = '✓' if tot_neg == 0 and tot_pos > 0 else ('⚠' if tot_neg > 0 else '·')
            L(f'    +{t}: {tag} pos={tot_pos} / neg={tot_neg} / zero={tot_zero}')
        L('')

    for cand in [put_top, put_orig]:
        L('=' * 130)
        L(f'  {cand["name"]}')
        L('=' * 130)
        L(f'  {"window":<6s} {"barr":<6s} {"<25 (ΔWR/ΔN)":<18s} {"<20 (ΔWR/ΔN)":<18s} {"<15 (ΔWR/ΔN)":<18s}')
        L('  ' + '-' * 78)
        sign_track = {(t, w): [] for t in [25, 20, 15] for w in [15, 30]}
        for label, days in windows:
            for w_days in [15, 30]:
                sub = slice_window(puts_full, days)
                results = eval_put(sub, cand, w_days)
                row = f'  {label:<6s} TP{w_days:<4d}'
                for t in [25, 20, 15]:
                    r = results.get(t, {})
                    if r.get('d_wr') is None:
                        row += f' {"N/A":<18s}'
                    else:
                        row += f' <{t}: {r["d_wr"]:>+5.2f}/{r["d_n_pct"]:>+5.1f}%   '
                        sign_track[(t, w_days)].append(r['d_wr'])
                L(row)
            L('')
        L('  ' + '-' * 78)
        L(f'  H5 sign-consistency:')
        for t in [25, 20, 15]:
            tot_pos = sum(1 for k, lst in sign_track.items() if k[0] == t for v in lst if v > 0)
            tot_neg = sum(1 for k, lst in sign_track.items() if k[0] == t for v in lst if v < 0)
            tot_zero = sum(1 for k, lst in sign_track.items() if k[0] == t for v in lst if v == 0)
            tag = '✓' if tot_neg == 0 and tot_pos > 0 else ('⚠' if tot_neg > 0 else '·')
            L(f'    <{t}: {tag} pos={tot_pos} / neg={tot_neg} / zero={tot_zero}')
        L('')

    L('[done] eval complete.')


if __name__ == '__main__':
    main()
