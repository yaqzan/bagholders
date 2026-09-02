"""Phase B+ check: does the Ichimoku signal survive after conditioning on w_adj?

v32 CWCF already dampens calls when `overall >= 75 AND w_adj < 1`, drifting the
score toward 55.  If the ~5pp Ichimoku spread we found in profile.py is mostly
concentrated in the `w_adj < 0` cohort that CWCF already addresses, there is
no marginal alpha left for a new Ichimoku-based dampener.

Test: split 75+ peaks by w_adj into three cohorts:
  - wadj < 0           (bearish weekly — v32 CWCF active)
  - 0 <= wadj < 1      (neutral weekly — v32 CWCF still active, gate is wadj<1)
  - wadj >= 1          (bullish weekly — v32 CWCF does NOT fire)

Within each cohort, compute the Ichimoku spread (tenkan_kijun_dir, cloud_position).
Real marginal alpha would show up as a >=3pp spread in the wadj>=1 cohort, where
CWCF doesn't already fire.

Note: the v32 CWCF dampener pulls qualifying scores TOWARD 55, so most pre-CWCF
75+ peaks with wadj<1 may have already dropped out of the 75+ universe in v39.
This means the "wadj<0 in 75+" cohort we observe is the RESIDUE — peaks where
the dampener fired but didn't pull below 75.  That's a different population than
"all wadj<0 calls" — interpret accordingly.
"""
from __future__ import annotations

import io
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d.parquet'


def load_wadj_for_peaks(peak_keys: list[tuple[str, str]], av_id: int) -> dict[tuple[str, str], float]:
    """Bulk-load w_adj for (symbol, date) peak keys via single SQL query."""
    from database.trader_database import DB

    cur = DB.execute_sql(f"""
        SELECT symbol, date, weight_info
        FROM scores
        WHERE version_id = {av_id}
          AND overall >= 70
          AND weight_info IS NOT NULL
          AND weight_info != ''
    """)
    out: dict[tuple[str, str], float] = {}
    for sym, dt, wi in cur.fetchall():
        try:
            d = json.loads(wi) if isinstance(wi, str) else wi
            wadj = d.get('w_adj')
            if wadj is not None:
                key = (sym, dt.isoformat() if hasattr(dt, 'isoformat') else str(dt))
                out[key] = float(wadj)
        except Exception:
            continue
    return out


def cohort_wr(df: pl.DataFrame, col: str) -> tuple[float, int, int]:
    sub = df.filter(pl.col(col).is_not_null())
    n = len(sub)
    if n == 0:
        return float('nan'), 0, 0
    wins = int(sub[col].sum())
    return wins / n * 100, wins, n


def main():
    pq = CACHE / PARQUET
    df = pl.read_parquet(pq)
    print(f'[load] parquet: {len(df):,} rows', flush=True)

    # Filter to non-null w52_low_pct (drop the inf rows)
    df = df.with_columns([
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct'),
    ])

    # ------------------------------------------------------------------
    # Attach w_adj
    # ------------------------------------------------------------------
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()

    t0 = time.time()
    keys = list(zip(df['symbol'].to_list(), df['date'].to_list()))
    wadj_map = load_wadj_for_peaks(keys, av.id)
    print(f'[load] w_adj for {len(wadj_map):,} peaks  ({time.time()-t0:.1f}s)', flush=True)

    df = df.with_columns(
        pl.struct(['symbol', 'date']).map_elements(
            lambda s: wadj_map.get((s['symbol'], s['date'])),
            return_dtype=pl.Float64,
        ).alias('w_adj')
    )

    n_with_wadj = df['w_adj'].drop_nulls().len()
    print(f'[joined] {n_with_wadj:,} of {len(df):,} peaks have w_adj', flush=True)

    # ------------------------------------------------------------------
    # Attach WR30(opt)
    # ------------------------------------------------------------------
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        wr30 = pl.read_database(
            """SELECT symbol, date, result AS opt_result_30
               FROM barrier_outcomes
               WHERE side='low' AND barrier_set='30dte_opt' AND w_days=30""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(wr30, on=['symbol', 'date'], how='left')
    print(f'[load] WR30(opt) joined  ({time.time()-t0:.1f}s)', flush=True)

    # ------------------------------------------------------------------
    # Show w_adj distribution at 75+
    # ------------------------------------------------------------------
    tier = df.filter((pl.col('overall') >= 75) & pl.col('w_adj').is_not_null())
    print(f'\n75+ tier with w_adj: N={len(tier):,}')
    print(f'  wadj distribution: '
          f'min={tier["w_adj"].min():.2f} '
          f'p25={tier["w_adj"].quantile(0.25):.2f} '
          f'median={tier["w_adj"].median():.2f} '
          f'p75={tier["w_adj"].quantile(0.75):.2f} '
          f'max={tier["w_adj"].max():.2f}')

    # ------------------------------------------------------------------
    # Decompose by w_adj cohort
    # ------------------------------------------------------------------
    cohorts = [
        ('wadj < 0   (CWCF fires)',  pl.col('w_adj') < 0),
        ('0 <= wadj < 1 (CWCF fires)', (pl.col('w_adj') >= 0) & (pl.col('w_adj') < 1)),
        ('wadj >= 1  (CWCF off)',    pl.col('w_adj') >= 1),
    ]

    for cohort_label, cohort_filter in cohorts:
        sub = tier.filter(cohort_filter)
        n_total = len(sub)
        if n_total == 0:
            print(f'\n--- {cohort_label} --- (empty)')
            continue

        n15 = sub.filter(pl.col('opt_result_15').is_not_null()).height
        n30 = sub.filter(pl.col('opt_result_30').is_not_null()).height
        base15, _, _ = cohort_wr(sub, 'opt_result_15')
        base30, _, _ = cohort_wr(sub, 'opt_result_30')
        share = n_total / len(tier) * 100

        print(f'\n--- {cohort_label} | N={n_total:,} ({share:.1f}% of tier) | '
              f'baseline WR15={base15:.2f}% / WR30(opt)={base30:.2f}% ---')

        # tenkan_kijun_dir split
        for v in (-1, 0, 1):
            grp = sub.filter(pl.col('tenkan_kijun_dir') == v)
            if len(grp) < 30:
                continue
            wr15, _, n15g = cohort_wr(grp, 'opt_result_15')
            wr30, _, n30g = cohort_wr(grp, 'opt_result_30')
            d15 = wr15 - base15
            d30 = wr30 - base30
            print(f'  tenkan_kijun_dir = {v:+d}  N={n15g:>5,}  '
                  f'WR15={wr15:5.2f}% (Δ{d15:+5.2f})  '
                  f'WR30(opt)={wr30:5.2f}% (Δ{d30:+5.2f})')

        # cloud_position split
        for v in (-1, 0, 1):
            grp = sub.filter(pl.col('cloud_position') == v)
            if len(grp) < 30:
                continue
            wr15, _, n15g = cohort_wr(grp, 'opt_result_15')
            wr30, _, n30g = cohort_wr(grp, 'opt_result_30')
            d15 = wr15 - base15
            d30 = wr30 - base30
            print(f'  cloud_position   = {v:+d}  N={n15g:>5,}  '
                  f'WR15={wr15:5.2f}% (Δ{d15:+5.2f})  '
                  f'WR30(opt)={wr30:5.2f}% (Δ{d30:+5.2f})')

        # price_vs_kijun_pct tercile split (continuous, also strong on profile.py)
        kij = sub.filter(pl.col('price_vs_kijun_pct').is_not_null())
        if len(kij) >= 90:
            q33 = kij['price_vs_kijun_pct'].quantile(0.333)
            q67 = kij['price_vs_kijun_pct'].quantile(0.667)
            for label, sub2 in [
                ('low  ', kij.filter(pl.col('price_vs_kijun_pct') <= q33)),
                ('mid  ', kij.filter((pl.col('price_vs_kijun_pct') > q33)
                                     & (pl.col('price_vs_kijun_pct') <= q67))),
                ('high ', kij.filter(pl.col('price_vs_kijun_pct') > q67)),
            ]:
                if len(sub2) < 30:
                    continue
                wr15, _, n15g = cohort_wr(sub2, 'opt_result_15')
                wr30, _, n30g = cohort_wr(sub2, 'opt_result_30')
                d15 = wr15 - base15
                d30 = wr30 - base30
                print(f'  kijun_pct {label}  N={n15g:>5,}  '
                      f'WR15={wr15:5.2f}% (Δ{d15:+5.2f})  '
                      f'WR30(opt)={wr30:5.2f}% (Δ{d30:+5.2f})')

    # ------------------------------------------------------------------
    # Headline: spread within wadj>=1 cohort (orthogonal alpha test)
    # ------------------------------------------------------------------
    print('\n' + '=' * 90)
    print('HEADLINE: Ichimoku spread WITHIN wadj>=1 cohort (where v32 CWCF does NOT fire)')
    print('=' * 90)
    bull_wadj = tier.filter(pl.col('w_adj') >= 1)
    print(f'\nN={len(bull_wadj):,} ({len(bull_wadj)/len(tier)*100:.1f}% of 75+ tier)')

    for feat in ['tenkan_kijun_dir', 'cloud_position']:
        wrs15 = []
        wrs30 = []
        for v in (-1, 0, 1):
            grp = bull_wadj.filter(pl.col(feat) == v)
            if len(grp) < 30:
                wrs15.append(None); wrs30.append(None)
                continue
            wr15, _, _ = cohort_wr(grp, 'opt_result_15')
            wr30, _, _ = cohort_wr(grp, 'opt_result_30')
            wrs15.append(wr15)
            wrs30.append(wr30)
        nonnull15 = [x for x in wrs15 if x is not None]
        nonnull30 = [x for x in wrs30 if x is not None]
        sp15 = max(nonnull15) - min(nonnull15) if len(nonnull15) >= 2 else 0
        sp30 = max(nonnull30) - min(nonnull30) if len(nonnull30) >= 2 else 0
        print(f'  {feat:<22s}  WR15 spread={sp15:5.2f}pp   WR30(opt) spread={sp30:5.2f}pp')


if __name__ == '__main__':
    main()
