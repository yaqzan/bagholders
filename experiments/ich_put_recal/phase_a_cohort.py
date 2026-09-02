"""Phase A — W1 cohort-z pre-flight for ICH put-side recalibration.

Tests whether the v44 ICH put-side cohort signal (puts with kijun_pct < 0
underperform vs puts with kijun_pct >= 0) still survives at v46 baseline.

If z >= +3 on the targeted cohort, proceed to Phase B sweep.
If z < +3, abandon recalibration — disable ICH put-side instead of tuning it.

Run: python experiments/ich_put_recal/phase_a_cohort.py
"""
from __future__ import annotations

import io
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'

LOOKBACK_DAYS = 1825   # 5y
MAX_SCORE = 30          # put peaks
W_DAYS = 7              # WR7 primary
BARRIER_SET = '30dte_generic'  # K=1σ/M=2σ for puts (HIGH side)


def _s(v) -> str:
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return str(v)[:10]


def load_put_peaks(av_id: int, start: str, cutoff: str) -> pl.DataFrame:
    """v46 put peaks (overall ≤ 30) within the 5y window."""
    from database.trader_database import DB
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {av_id}
          AND s.date >= '{start}'
          AND s.date <= '{cutoff}'
          AND s.overall <= {MAX_SCORE}
    """)
    rows = cur.fetchall()
    df = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [_s(r[1]) for r in rows],
        'overall':[int(r[2]) for r in rows],
    })
    print(f'[load] v{av_id} put peaks (<={MAX_SCORE}): {len(df):,}  ({time.time()-t0:.1f}s)', flush=True)
    return df


def load_weekly_kijun(buf_start: str, cutoff: str) -> pl.DataFrame:
    """Compute kijun_pct per (symbol, weekly_date).

    kijun = midpoint of last 26 weeks' high/low.
    kijun_pct = (close - kijun) / kijun * 100.
    """
    from database.trader_database import DB
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT symbol, date, high, low, close
        FROM weekly_price_history
        WHERE date >= '{buf_start}' AND date <= '{cutoff}'
        ORDER BY symbol, date
    """)
    rows = cur.fetchall()
    raw = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [_s(r[1]) for r in rows],
        'high':   [float(r[2]) for r in rows],
        'low':    [float(r[3]) for r in rows],
        'close':  [float(r[4]) for r in rows],
    })

    # Per-symbol rolling max/min over 26 weeks for kijun midpoint.
    out = []
    for sym, grp in raw.group_by('symbol'):
        g = grp.sort('date').with_columns([
            pl.col('high').rolling_max(window_size=26).alias('high_26w'),
            pl.col('low').rolling_min(window_size=26).alias('low_26w'),
        ]).with_columns(
            ((pl.col('high_26w') + pl.col('low_26w')) / 2.0).alias('kijun')
        ).with_columns(
            ((pl.col('close') - pl.col('kijun')) / pl.col('kijun') * 100.0)
            .alias('kijun_pct')
        )
        out.append(g.select(['symbol', 'date', 'kijun_pct']))
    df = pl.concat(out)
    df = df.filter(pl.col('kijun_pct').is_not_null())
    print(f'[load] weekly kijun_pct: {len(df):,} rows  ({time.time()-t0:.1f}s)', flush=True)
    return df


def join_kijun_to_peaks(peaks: pl.DataFrame, kijun: pl.DataFrame) -> pl.DataFrame:
    """For each peak, find the most recent COMPLETED weekly bar's kijun_pct.

    Lookup date = scoring_date - 7 calendar days (to ensure last fully-closed
    weekly bar — same convention as production scoring's `build_kijun_pct_map`).
    """
    t0 = time.time()
    # Build per-symbol sorted weekly date arrays for bisect.
    kj_by_sym = defaultdict(list)
    for r in kijun.iter_rows():
        sym, dt, val = r
        kj_by_sym[sym].append((dt, val))
    for sym in kj_by_sym:
        kj_by_sym[sym].sort()

    import bisect
    results = []
    for sym, dt, ov in peaks.iter_rows():
        arr = kj_by_sym.get(sym)
        if not arr:
            results.append(None)
            continue
        # lookup_date = dt - 7 cal days, find latest weekly date <= lookup_date
        scoring_date = date.fromisoformat(dt)
        lookup = (scoring_date - timedelta(days=7)).isoformat()
        dates = [t[0] for t in arr]
        idx = bisect.bisect_right(dates, lookup) - 1
        if idx < 0:
            results.append(None)
        else:
            results.append(arr[idx][1])

    out = peaks.with_columns(pl.Series('kijun_pct', results, dtype=pl.Float64))
    out = out.filter(pl.col('kijun_pct').is_not_null())
    print(f'[join] peaks with kijun_pct: {len(out):,}  ({time.time()-t0:.1f}s)', flush=True)
    return out


def join_barrier_outcomes(peaks: pl.DataFrame) -> pl.DataFrame:
    """Look up generic-barrier WR7 outcomes from barrier_outcomes cache."""
    t0 = time.time()
    if not CACHE_DB.exists():
        raise FileNotFoundError(f'barrier_outcomes cache missing: {CACHE_DB}')

    # Build symbol set for cache query.
    syms = sorted(peaks['symbol'].unique().to_list())
    placeholders = ','.join('?' for _ in syms)
    conn = sqlite3.connect(str(CACHE_DB))
    try:
        cur = conn.execute(f"""
            SELECT symbol, date, result
            FROM barrier_outcomes
            WHERE side = 'high'
              AND w_days = {W_DAYS}
              AND barrier_set = '{BARRIER_SET}'
              AND symbol IN ({placeholders})
        """, syms)
        rows = cur.fetchall()
    finally:
        conn.close()

    cache = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [_s(r[1]) for r in rows],
        'wr7':    [int(r[2]) if r[2] is not None else None for r in rows],
    }).filter(pl.col('wr7').is_not_null())

    print(f'[load] barrier_outcomes for puts W={W_DAYS}: {len(cache):,}  ({time.time()-t0:.1f}s)', flush=True)

    out = peaks.join(cache, on=['symbol', 'date'], how='inner')
    print(f'[join] peaks with WR7: {len(out):,}', flush=True)
    return out


def cohort_z(df: pl.DataFrame, cohort_mask: pl.Expr, label: str) -> dict:
    """Compute cohort z-score: cohort WR7 vs non-cohort WR7."""
    cohort = df.filter(cohort_mask)
    other = df.filter(~cohort_mask)
    n_c, n_o = len(cohort), len(other)
    if n_c < 30 or n_o < 30:
        return {'label': label, 'z': None, 'reason': f'small N: cohort={n_c} other={n_o}'}
    p_c = cohort['wr7'].mean()
    p_o = other['wr7'].mean()
    p_pool = (p_c * n_c + p_o * n_o) / (n_c + n_o)
    se = (p_pool * (1 - p_pool) * (1.0 / n_c + 1.0 / n_o)) ** 0.5
    if se == 0:
        return {'label': label, 'z': None, 'reason': 'zero SE'}
    z = (p_c - p_o) / se
    return {
        'label': label,
        'cohort_n': n_c, 'cohort_wr7': p_c * 100,
        'other_n': n_o, 'other_wr7': p_o * 100,
        'lift_pp': (p_c - p_o) * 100,
        'z': z,
    }


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: v{av.id} commit={av.git_commit[:7] if av.git_commit else "?"}', flush=True)

    today = date.today()
    cutoff = today.isoformat()
    start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    buf_start = (today - timedelta(days=LOOKBACK_DAYS + 400)).isoformat()

    # Holdout enforcement (Priority #11)
    try:
        from experiments._holdout import pre_cutoff_filter
        _ = pre_cutoff_filter
        from strategy_config import CALIBRATION_CUTOFF_DATE
        if cutoff > CALIBRATION_CUTOFF_DATE:
            cutoff = CALIBRATION_CUTOFF_DATE
            print(f'[holdout] cutoff clamped to CALIBRATION_CUTOFF_DATE={CALIBRATION_CUTOFF_DATE}', flush=True)
    except Exception:
        pass

    peaks = load_put_peaks(av.id, start, cutoff)
    if len(peaks) == 0:
        print('NO PUT PEAKS FOUND. Active version may have empty score rows.', flush=True)
        return

    kijun = load_weekly_kijun(buf_start, cutoff)
    peaks_kj = join_kijun_to_peaks(peaks, kijun)
    peaks_full = join_barrier_outcomes(peaks_kj)

    if len(peaks_full) < 100:
        print(f'INSUFFICIENT N for cohort analysis: {len(peaks_full)}', flush=True)
        return

    # Persist for downstream phases (avoids the 100s barrier_outcomes reload).
    out_path = ROOT / '.cache' / 'ich_put_recal' / f'puts_v{av.id}_1825d.parquet'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    peaks_full.write_parquet(out_path)
    print(f'[cache] wrote {out_path}: {len(peaks_full):,} rows', flush=True)

    print()
    print('=' * 80)
    print(f'W1 PRE-FLIGHT — ICH put-side cohort signal at v{av.id}')
    print('=' * 80)
    print(f'Total put peaks (≤{MAX_SCORE}) with kijun_pct + WR7: {len(peaks_full):,}')
    print(f'WR7 = generic barrier (K=1σ/M=2σ for puts) at W={W_DAYS}d')
    print()

    # Targeted cohort: v44 ICH put-side hypothesis was kijun_pct < 0
    # (price below 26w midpoint = bearish weekly).  Test if this still
    # discriminates put WR7 at v46.
    print('TARGETED COHORT TESTS:')
    print('-' * 80)
    print('{:>50} {:>8} {:>10} {:>10} {:>10}'.format(
        'cohort', 'N', 'WR7%', 'lift_pp', 'z'))
    print('-' * 80)

    tests = [
        ('all puts: kijun_pct < 0 vs kijun_pct >= 0',
         pl.col('kijun_pct') < 0),
        ('puts in [21,30] (boundary): kijun<0 vs kijun>=0',
         (pl.col('overall').is_between(21, 30)) & (pl.col('kijun_pct') < 0)),
        ('puts in [11,20] (mid): kijun<0 vs kijun>=0',
         (pl.col('overall').is_between(11, 20)) & (pl.col('kijun_pct') < 0)),
        ('puts in [0,10] (deep tail): kijun<0 vs kijun>=0',
         (pl.col('overall').is_between(0, 10)) & (pl.col('kijun_pct') < 0)),
    ]

    for label, mask in tests:
        if 'boundary' in label or 'mid' in label or 'deep' in label:
            # restrict baseline to the same score sub-range
            score_lo, score_hi = (
                (21, 30) if 'boundary' in label
                else (11, 20) if 'mid' in label
                else (0, 10)
            )
            sub = peaks_full.filter(pl.col('overall').is_between(score_lo, score_hi))
            if len(sub) < 100:
                print('{:>50} {:>8} {:>10}'.format(
                    label, len(sub), 'small N'))
                continue
            cohort_mask = pl.col('kijun_pct') < 0
            r = cohort_z(sub, cohort_mask, label)
        else:
            r = cohort_z(peaks_full, mask, label)
        if r.get('z') is None:
            print('{:>50} {:>8} {:>10}'.format(label, '?', r.get('reason', '?')))
        else:
            print('{:>50} {:>8} {:>10.2f} {:>+10.2f} {:>+10.2f}'.format(
                label, r['cohort_n'], r['cohort_wr7'], r['lift_pp'], r['z']))

    print()
    print('W1 GATE INTERPRETATION:')
    print('-' * 80)
    print('  z <= -3  -> kijun<0 cohort underperforms by 3+ standard errors -> ICH lift is justified')
    print('  z >= +3  -> kijun<0 cohort OUTPERFORMS -> ICH lift is INVERTED, would HURT WR7')
    print('  |z| < 3  -> no clear cohort signal -> abandon recalibration, consider disabling ICH puts')
    print()
    print('Per-score-zone breakdown lets us see where the v44 mechanism was actually')
    print('helping vs. hurting. Concentrating dampening at the score zone with the')
    print('strongest negative z is the asymmetric-K hypothesis.')


if __name__ == '__main__':
    main()
