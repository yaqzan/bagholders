"""Orthogonality check: do weekly-volume signals overlap with v44 ICH (kijun_pct)?

We need to know if a smooth-gradient wv_force1 dampener targets the SAME signals
v44 already touches via kijun_pct, or if it's an independent alpha source.

Method:
  1. Pull v44 weight_info per peak (has kijun_pct + ich_dampen) — but those aren't in
     the parquet. Workaround: rebuild kijun_pct for the same peak set and check
     correlation with wv_force1 / wv_cmf4 / wv_z12.
  2. Show 2D cohort table: WR15 by (kijun_pct sign × wv_force1 sign) at calls 75+.
     If wv_force1 alpha persists in BOTH kijun_pct >= 0 (ICH-not-firing) and
     kijun_pct < 0 (ICH-firing) sub-cohorts, the signal is orthogonal.
"""
from __future__ import annotations
import io, sys, sqlite3, time, math, bisect
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
import numpy as np

CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / 'calls_v44_1825.parquet'
PUTS_PARQ  = ROOT / '.cache' / 'weekly_volume' / 'puts_v44_1825.parquet'


def build_kijun_pct_for_peaks(peaks_df: pl.DataFrame) -> pl.DataFrame:
    """For each (sym, date) in peaks_df, compute kijun_pct using same logic as
    v44 ICH (26-week kijun, lookup at date-7days)."""
    from database.models.technical import WeeklyPriceHistory, PriceHistory
    from database.trader_database import DB

    # Group by sym
    syms = sorted(peaks_df.get_column('symbol').unique().to_list())
    print(f'[orth] computing kijun_pct for {len(syms):,} symbols...', flush=True)

    cutoff = peaks_df.select(pl.col('date').min()).item()
    weekly_cutoff = (date.fromisoformat(cutoff) - timedelta(days=400)).isoformat()

    # Bulk load weekly bars
    weekly_by_sym = defaultdict(list)
    BATCH = 50
    t0 = time.time()
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        sym_csv = ','.join(f"'{s}'" for s in batch)
        cur = DB.execute_sql(f"""
            SELECT symbol, date, high, low FROM weekly_price_history
            WHERE symbol IN ({sym_csv}) AND date >= '{weekly_cutoff}'
            ORDER BY symbol, date
        """)
        for sym, d, h, l in cur.fetchall():
            weekly_by_sym[sym].append((d, float(h), float(l)))
    print(f'[orth] weekly bars loaded ({time.time()-t0:.1f}s)', flush=True)

    # Bulk load daily closes
    closes_by_sym = defaultdict(dict)
    t0 = time.time()
    for i in range(0, len(syms), BATCH):
        batch = syms[i:i + BATCH]
        sym_csv = ','.join(f"'{s}'" for s in batch)
        cur = DB.execute_sql(f"""
            SELECT symbol, date, close FROM price_history
            WHERE symbol IN ({sym_csv}) AND date >= '{cutoff}'
        """)
        for sym, d, c in cur.fetchall():
            closes_by_sym[sym][d] = float(c)
    print(f'[orth] daily closes loaded ({time.time()-t0:.1f}s)', flush=True)

    # Compute kijun for each weekly bar (rolling 26-bar high/low midpoint)
    kijun_by_sym = {}  # sym -> list of (date, kijun_value)
    for sym, bars in weekly_by_sym.items():
        kij_list = []
        for i in range(25, len(bars)):
            window = bars[i - 25:i + 1]
            highs = [b[1] for b in window]
            lows = [b[2] for b in window]
            kij = (max(highs) + min(lows)) / 2.0
            kij_list.append((bars[i][0], kij))
        kijun_by_sym[sym] = kij_list

    # Map peaks → kijun_pct
    out_kp = []
    for row in peaks_df.iter_rows(named=True):
        sym = row['symbol']
        d_str = row['date']
        d = date.fromisoformat(d_str)
        kij_list = kijun_by_sym.get(sym, [])
        if not kij_list:
            out_kp.append(None)
            continue
        lookup = d - timedelta(days=7)
        # Binary search latest kijun bar with date <= lookup
        kij_dates = [k[0] for k in kij_list]
        idx = bisect.bisect_right(kij_dates, lookup) - 1
        if idx < 0:
            out_kp.append(None)
            continue
        kijun = kij_list[idx][1]
        close = closes_by_sym.get(sym, {}).get(d)
        if close is None or kijun <= 0:
            out_kp.append(None)
            continue
        kp = (close - kijun) / kijun * 100.0
        out_kp.append(float(kp))

    return peaks_df.with_columns(pl.Series('kijun_pct', out_kp))


def cross_tab_2d(df: pl.DataFrame, score_min: int, side: str, feat: str, kij_thresh: float = 0.0):
    """2D cohort: WR by (kijun_pct sign) × (feat sign).

    For calls: score >= score_min; for puts: score <= score_min.
    Reports WR + N for each of 4 cells.
    """
    if side == 'call':
        sub = df.filter(pl.col('overall') >= score_min)
    else:
        sub = df.filter(pl.col('overall') <= score_min)
    sub = sub.filter(
        pl.col('opt_result_15').is_not_null()
        & pl.col('kijun_pct').is_not_null()
        & pl.col(feat).is_not_null()
    )
    n_total = sub.height
    if n_total < 200:
        return None

    base_wr = float(sub.get_column('opt_result_15').mean()) * 100

    cells = {}
    for kp_label, kp_pred in [('kij<0', pl.col('kijun_pct') < kij_thresh),
                              ('kij>=0', pl.col('kijun_pct') >= kij_thresh)]:
        for f_label, f_pred in [(f'{feat}<0', pl.col(feat) < 0),
                                 (f'{feat}>=0', pl.col(feat) >= 0)]:
            cell = sub.filter(kp_pred & f_pred)
            n = cell.height
            wr = float(cell.get_column('opt_result_15').mean()) * 100 if n > 0 else float('nan')
            cells[(kp_label, f_label)] = {'n': n, 'wr': wr}

    return {'base_wr': base_wr, 'n_total': n_total, 'cells': cells}


def main():
    print('Orthogonality Check: weekly-volume features vs v44 ICH (kijun_pct)\n')

    calls = pl.read_parquet(CALLS_PARQ)
    puts  = pl.read_parquet(PUTS_PARQ)
    print(f'[setup] loaded calls={len(calls):,}  puts={len(puts):,}\n')

    # Combine for kijun_pct compute
    combined = pl.concat([calls, puts], how='diagonal_relaxed')
    combined = build_kijun_pct_for_peaks(combined)
    n_with_kp = combined.filter(pl.col('kijun_pct').is_not_null()).height
    print(f'[orth] {n_with_kp:,} of {len(combined):,} peaks got kijun_pct\n')

    calls_k = combined.filter(pl.col('overall') >= 70)
    puts_k  = combined.filter(pl.col('overall') <= 25)

    # 2D cross-tabs
    for side, df_k, gates in [('call', calls_k, [75, 80, 85]),
                              ('put',  puts_k,  [25, 20, 15])]:
        for tier in gates:
            for feat in ['wv_force1', 'wv_z12', 'wv_cmf4', 'wv_obv_slope8']:
                ct = cross_tab_2d(df_k, tier, side, feat)
                if ct is None:
                    continue
                sym = '+' if side == 'call' else '<='
                print(f'[{side} {sym}{tier}, feat={feat}]  baseline WR={ct["base_wr"]:.2f}%  N={ct["n_total"]}')
                cells = ct['cells']
                for kp_lab in ['kij<0', 'kij>=0']:
                    for f_lab in [f'{feat}<0', f'{feat}>=0']:
                        c = cells[(kp_lab, f_lab)]
                        delta = c['wr'] - ct['base_wr'] if not math.isnan(c['wr']) else 0.0
                        print(f'  {kp_lab:<8s} ∧ {f_lab:<14s}  N={c["n"]:>5d}  WR={c["wr"]:>5.1f}%  Δ={delta:>+5.1f}pp')
                # Compute marginal effect of feat sign WITHIN kij<0 (ICH-firing) and kij>=0 (ICH-not-firing) subsets
                kn = cells[('kij<0', f'{feat}<0')]
                kp = cells[('kij<0', f'{feat}>=0')]
                kjn = cells[('kij>=0', f'{feat}<0')]
                kjp = cells[('kij>=0', f'{feat}>=0')]
                if kn['n'] > 30 and kp['n'] > 30:
                    eff_in_ich = kp['wr'] - kn['wr']
                    print(f'  [{feat} effect within kij<0  (ICH-firing)]:  {eff_in_ich:+.1f}pp  ({kp["n"]+kn["n"]} total)')
                if kjn['n'] > 30 and kjp['n'] > 30:
                    eff_no_ich = kjp['wr'] - kjn['wr']
                    print(f'  [{feat} effect within kij>=0 (ICH-not-firing)]: {eff_no_ich:+.1f}pp  ({kjn["n"]+kjp["n"]} total)')
                print()


if __name__ == '__main__':
    main()
