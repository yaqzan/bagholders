"""Stage 1 Phase A — cohort validation for put-side wadj tanh-amp hypothesis.

Hypothesis (from known-issues.md Priority #4 leg-2):
  Current production puts get 1.5× weekly amp regardless of |wadj| magnitude.
  This inflates noise — puts with mildly negative wadj (|wadj| < 13) are
  admitted into put-qualifying zone (overall ≤ 25) via the same boost as
  strong-wadj puts (|wadj| > 30). Tanh-saturating amp would dampen the
  weak-wadj amp without touching strong wadj.

Proposed mechanism (untested):
    # current: total *= 1.5 if total < 0 else 1.0   (in calculate_weekly_adjustment)
    # tanh:    total *= 1.0 + 0.5 * tanh(|total|/k)  for total < 0  (puts)

Phase A goal — Stage 1 W1 gate (z ≥ +3):
  Cohort: put signals (overall ≤ 25) split by wadj magnitude.
  Test: do weak-wadj puts (wadj ∈ (-13, 0)) miss WR7 at a measurably
        higher rate than strong-wadj puts (wadj ≤ -13)?

  z-score formula: (cohort_miss_rate - neighbor_miss_rate) / SE_diff
  where SE_diff = sqrt(p1*(1-p1)/n1 + p2*(1-p2)/n2) on miss rates.
  z ≥ +3 means weak-wadj cohort misses MORE than strong-wadj (consistent
  with hypothesis that 1.5× amp is admitting noise).

Output:
  - Cohort × wadj-bin WR7 table
  - z-score for the targeted weak-wadj cohort
  - PASS/FAIL on W1 (z ≥ +3)
  - Affected N/year by tier (W5 floor check)
"""
from __future__ import annotations
import io, json, sqlite3, sys, time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / '.cache' / 'wadj_put_tanh'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

LOOKBACK_DAYS = 1825


def build_features(version_id: int, refresh: bool = False) -> pl.DataFrame:
    """Pull v{N} put scores + wadj from weight_info, join WR7 (30dte_generic at w=7).

    Returns one row per put signal (overall ≤ 25) with:
      symbol, date, overall, wadj, pre_regime, wr7 (1=hit, 0=miss, NULL=expire)
    """
    cache_pq = CACHE_DIR / f'puts_v{version_id}_{LOOKBACK_DAYS}.parquet'
    if cache_pq.exists() and not refresh:
        t0 = time.time()
        df = pl.read_parquet(cache_pq)
        print(f'[load] put feature parquet {len(df):,} rows ({time.time()-t0:.1f}s)', flush=True)
        return df

    from database.trader_database import DB
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[build] pulling v{version_id} put scores (overall ≤ 25, date >= {cutoff})...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.weight_info
        FROM scores s
        WHERE s.version_id = {version_id}
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
          AND s.overall <= 25
    """)
    rows = cur.fetchall()
    print(f'  {len(rows):,} put rows ({time.time()-t0:.0f}s)', flush=True)

    syms, dates, overalls, wadjs, pre_regs = [], [], [], [], []
    for sym, d, ov, winfo in rows:
        if not winfo:
            continue
        try:
            wi = json.loads(winfo) if isinstance(winfo, str) else winfo
        except Exception:
            continue
        wadj = float(wi.get('w_adj', 0.0) or 0.0)
        pre_reg = int(wi.get('pre_regime', ov) or ov)
        syms.append(sym)
        dates.append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        overalls.append(int(ov))
        wadjs.append(wadj)
        pre_regs.append(pre_reg)

    df_scores = pl.DataFrame({
        'symbol': syms,
        'date': dates,
        'overall': overalls,
        'wadj': wadjs,
        'pre_regime': pre_regs,
    })
    print(f'[parse] {len(df_scores):,} rows with wadj parsed', flush=True)

    # Join WR7 (30dte_generic at w=7, side='high' for puts)
    print('[join] WR7 from barrier_outcomes (30dte_generic w=7 side=high)...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        wr = pl.read_database(
            "SELECT symbol, date, result AS wr7 "
            "FROM barrier_outcomes "
            "WHERE side='high' AND barrier_set='30dte_generic' AND w_days=7",
            connection=conn,
        )
    finally:
        conn.close()
    print(f'  {len(wr):,} WR7 rows ({time.time()-t0:.0f}s)', flush=True)

    df = df_scores.join(wr, on=['symbol', 'date'], how='left')
    df.write_parquet(cache_pq)
    print(f'[done] -> {cache_pq}  rows={len(df):,}', flush=True)
    return df


def cohort_zscore(df: pl.DataFrame, cohort_filter, neighbor_filter, label: str):
    """Compute z-score of cohort vs neighbor on miss rate (1 - WR7).

    Returns dict with N, miss_rate, z, p_value (approx).
    """
    cohort = df.filter(cohort_filter)
    neighbor = df.filter(neighbor_filter)
    cohort_resolved = cohort.filter(pl.col('wr7').is_not_null())
    neighbor_resolved = neighbor.filter(pl.col('wr7').is_not_null())

    n_c = len(cohort_resolved)
    n_n = len(neighbor_resolved)
    if n_c < 30 or n_n < 30:
        return dict(label=label, n_cohort=n_c, n_neighbor=n_n, status='INSUFFICIENT_N')

    miss_c = 1.0 - float(cohort_resolved['wr7'].mean())
    miss_n = 1.0 - float(neighbor_resolved['wr7'].mean())
    p_c = miss_c
    p_n = miss_n
    se = ((p_c * (1 - p_c) / n_c) + (p_n * (1 - p_n) / n_n)) ** 0.5
    z = (miss_c - miss_n) / se if se > 0 else 0.0
    return dict(
        label=label,
        n_cohort=n_c, n_neighbor=n_n,
        wr7_cohort=1 - miss_c, wr7_neighbor=1 - miss_n,
        miss_cohort=miss_c, miss_neighbor=miss_n,
        delta_pp=(miss_c - miss_n) * 100,
        z=z,
    )


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: id={av.id} commit={av.git_commit}', flush=True)

    df = build_features(av.id)
    print()
    df = df.filter(pl.col('wr7').is_not_null())
    print(f'[filter] resolved WR7 only: {len(df):,} rows', flush=True)

    # Overall WR7 baseline (all puts)
    base_wr7 = float(df['wadj'].count() and df['wr7'].mean()) * 100
    print(f'\n=== Baseline WR7 (all puts ≤25, resolved) ===')
    print(f'  N={len(df):,}  WR7={base_wr7:.2f}%')

    # WR7 by wadj bin (5y, all puts)
    print('\n=== WR7 by wadj bin (puts overall ≤ 25, 5y) ===')
    bins = [(-100, -30), (-30, -20), (-20, -13), (-13, -7), (-7, 0), (0, 999)]
    for lo, hi in bins:
        sub = df.filter((pl.col('wadj') >= lo) & (pl.col('wadj') < hi))
        if len(sub) == 0:
            continue
        wr = float(sub['wr7'].mean()) * 100
        print(f'  wadj [{lo:>4}, {hi:>4}): N={len(sub):>6,}  WR7={wr:5.2f}%')

    # WR7 by (wadj bin × overall bucket)
    print('\n=== WR7 by overall bucket × wadj bin ===')
    overall_bins = [('<5', 0, 6), ('5-10', 6, 11), ('11-15', 11, 16),
                    ('16-20', 16, 21), ('21-25', 21, 26)]
    print(f'{"bucket":>8s}  {"all_N":>7s}  {"all_WR7":>8s}  '
          f'{"weak[-13,0)":>13s}  {"strong<=-13":>13s}  {"D=weak-strong":>14s}  z')
    for tag, ov_lo, ov_hi in overall_bins:
        sub = df.filter((pl.col('overall') >= ov_lo) & (pl.col('overall') < ov_hi))
        if len(sub) < 30:
            continue
        all_wr = float(sub['wr7'].mean()) * 100
        weak = sub.filter((pl.col('wadj') >= -13) & (pl.col('wadj') < 0))
        strong = sub.filter(pl.col('wadj') < -13)
        if len(weak) >= 30 and len(strong) >= 30:
            wr_weak = float(weak['wr7'].mean()) * 100
            wr_strong = float(strong['wr7'].mean()) * 100
            n_w, n_s = len(weak), len(strong)
            p_w = (1 - wr_weak/100); p_s = (1 - wr_strong/100)
            se = ((p_w*(1-p_w)/n_w) + (p_s*(1-p_s)/n_s)) ** 0.5
            z = ((1-wr_weak/100) - (1-wr_strong/100)) / se if se > 0 else 0
            print(f'{tag:>8s}  {len(sub):>7,}  {all_wr:>7.2f}%  '
                  f'N={n_w:>4} WR={wr_weak:>5.2f}%  '
                  f'N={n_s:>4} WR={wr_strong:>5.2f}%  '
                  f'{wr_weak-wr_strong:>+13.2f}pp  z={z:+.2f}')
        else:
            print(f'{tag:>8s}  N too small in weak/strong split')

    # PRIMARY W1 GATE — cohort = weak-wadj puts (<= 25, wadj ∈ [-13, 0))
    #                  neighbor = strong-wadj puts (<= 25, wadj < -13)
    # If weak misses MORE (positive delta in miss rate), z > 0 → hypothesis supported.
    print('\n=== Stage 1 W1 cohort z-score gate ===')
    print('Cohort:   puts (overall ≤ 25) ∧ wadj ∈ [-13, 0)')
    print('Neighbor: puts (overall ≤ 25) ∧ wadj < -13')
    print('Hypothesis: weak-wadj puts MISS WR7 at higher rate (z > 0)')
    res = cohort_zscore(
        df,
        cohort_filter=(pl.col('wadj') >= -13) & (pl.col('wadj') < 0),
        neighbor_filter=pl.col('wadj') < -13,
        label='weak_wadj_puts',
    )
    print()
    if res.get('status') == 'INSUFFICIENT_N':
        print(f'  INSUFFICIENT N: cohort={res["n_cohort"]}  neighbor={res["n_neighbor"]}')
    else:
        print(f'  N cohort   = {res["n_cohort"]:>6,}  WR7 = {res["wr7_cohort"]*100:.2f}%')
        print(f'  N neighbor = {res["n_neighbor"]:>6,}  WR7 = {res["wr7_neighbor"]*100:.2f}%')
        print(f'  Δ miss rate = {res["delta_pp"]:+.2f}pp  (positive = cohort misses more)')
        print(f'  z = {res["z"]:+.2f}')
        passed = res['z'] >= 3.0
        print()
        print(f'  W1 gate (z >= +3): {"PASS - proceed to Phase B" if passed else "FAIL - DO NOT calibrate noise"}')

    # Cross-cut: in current production, what fraction of puts ≤25 are weak-wadj?
    weak_pct = 100.0 * len(df.filter((pl.col('wadj') >= -13) & (pl.col('wadj') < 0))) / max(1, len(df))
    print(f'\n  weak-wadj puts as %% of all ≤25 puts: {weak_pct:.1f}%')

    # Annual N/year for capacity floor check
    n_per_yr = len(df) / 5.0
    print(f'  put signals/year (resolved): {n_per_yr:.0f}')


if __name__ == '__main__':
    main()
