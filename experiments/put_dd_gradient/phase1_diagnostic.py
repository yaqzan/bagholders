"""Phase 1 diagnostic — attribute put DD damage to specific sub-buckets.

Three independent analyses:
  A. Per-bucket per-trade decomposition (WR15, TP%15, avg_option_pnl_15d, exit_bars)
     - splits puts into <5, 5-10, 10-15, 15-20, 20-25 sub-buckets
     - parallel breakdown for calls 70-74, 75-79, 80-84, 85-89, 90+
     - reports option_pnl computed via option_pricing model + bimodal fill assumption

  B. Concurrent-fire density: per trading day, count of puts that fire at
     overall<=25. Distribution + correlation with portfolio-DD episodes.

  C. Quick MC ablation: drop_lt15 vs drop_lt10 vs baseline on dip+5y at N=200.
     Direct test of "do deepest puts cause the DD?"

Output: experiments/put_dd_gradient/phase1_findings.md + json artifacts.
"""
from __future__ import annotations
import io, sys, os, sqlite3, json, time, math
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict, Counter

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR  = ROOT / 'experiments' / 'put_dd_gradient'
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 1825  # 5y

# v31 active version
def get_active_version_id():
    from database.models.core import AlgorithmVersion
    return AlgorithmVersion.get_active_scores_version().id

# ------------------------------------------------------------------
# Phase 1A — per-bucket decomposition
# ------------------------------------------------------------------
def phase1a_bucket_decomp():
    print("=" * 70)
    print("Phase 1A — Per-bucket put/call decomposition (5y, v31)")
    print("=" * 70)
    t0 = time.time()
    version_id = get_active_version_id()
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # --- Load scores ---
    from database.trader_database import DB
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {version_id}
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
    """)
    rows = cur.fetchall()
    scores_df = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'overall': [int(r[2]) for r in rows],
    })
    # Filter to peaks only
    peaks = scores_df.filter(
        (pl.col('overall') >= 70) | (pl.col('overall') <= 25)
    )
    peaks = peaks.with_columns(
        pl.when(pl.col('overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high'))
          .alias('side')
    )
    print(f"[A] Loaded {len(scores_df):,} scores; {len(peaks):,} qualifying peaks ({time.time()-t0:.1f}s)")

    # --- Load barrier outcomes for option-aligned barriers (30dte_opt) ---
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    bar_df = pl.read_database(
        f"""SELECT symbol, date, side, w_days, result, exit_return, exit_bars,
                   entry_close, sigma_pct, fire_high, fire_low, fire_open
            FROM barrier_outcomes
            WHERE date >= '{cutoff}' AND barrier_set = '30dte_opt' AND w_days = 15""",
        connection=conn,
    )
    conn.close()
    print(f"[A] Loaded {len(bar_df):,} 30dte_opt barrier rows for w_days=15 ({time.time()-t0:.1f}s)")

    # --- Join ---
    joined = peaks.join(bar_df, on=['symbol', 'date', 'side'], how='inner')
    print(f"[A] Joined to {len(joined):,} peak-outcome rows")

    # --- Compute option_pnl per row using option_pricing model ---
    # For each row, we have:
    #  - exit_return (underlying % from entry to exit_close)
    #  - exit_bars (bar idx of fire, or hold_days if hard sell)
    #  - sigma_pct (60d daily realized vol, %)
    #  - result (1=TP, 0=SL/expire, NULL=insufficient)
    # Assumption: PREMIUM_MULT = 1.82 (30 DTE), DELTA = 0.5
    # For TP/SL fires, simulate bimodal fill (intraday triggers fill at barrier;
    # gap-throughs sample within bar). For w_days=15, exits within hold window.
    PREMIUM_MULT = 1.82
    DELTA = 0.5
    TOTAL_DTE = 30
    HOLD_DAYS = 15
    HARD_SELL_LOSS = -0.40  # current shipped

    def compute_option_pnl(row):
        result = row['result']
        if result is None:
            return None
        exit_return = row['exit_return'] or 0.0
        exit_bars = row['exit_bars'] or 0
        sigma = row['sigma_pct'] or 0.0
        if sigma <= 0:
            return None
        # premium as fraction of stock price
        premium_pct = PREMIUM_MULT * sigma / 100.0
        # underlying ratio at exit
        u_ratio = 1.0 + exit_return
        # delta term: sign × delta × move / premium_pct
        side = row['side']
        sign = 1.0 if side == 'low' else -1.0  # 'low' = call, 'high' = put
        intrinsic = sign * DELTA * exit_return / premium_pct
        # theta drag
        theta = math.sqrt(max(0, (TOTAL_DTE - exit_bars)) / TOTAL_DTE) - 1.0
        pnl = intrinsic + theta
        # If hard sell at hold_days
        if exit_bars >= HOLD_DAYS:
            return HARD_SELL_LOSS
        return max(-1.0, pnl)

    # Vectorized computation
    side_sign = pl.when(pl.col('side') == 'low').then(1.0).otherwise(-1.0)
    sigma_safe = pl.when(pl.col('sigma_pct') > 0).then(pl.col('sigma_pct')).otherwise(1.0)
    premium_pct = (PREMIUM_MULT * sigma_safe / 100.0)
    intrinsic = side_sign * DELTA * pl.col('exit_return') / premium_pct
    theta = ((((TOTAL_DTE - pl.col('exit_bars').clip(0, TOTAL_DTE)) / TOTAL_DTE).sqrt()) - 1.0)
    raw_pnl = (intrinsic + theta).clip(-1.0, 5.0)
    # If exit_bars >= HOLD_DAYS treat as hard sell
    pnl_expr = pl.when(pl.col('exit_bars') >= HOLD_DAYS).then(HARD_SELL_LOSS).otherwise(raw_pnl)

    joined = joined.with_columns([
        pnl_expr.alias('option_pnl_15d'),
    ])

    # --- Bucket rows ---
    def bucket_label(ov):
        if ov >= 95: return '95+'
        if ov >= 90: return '90-94'
        if ov >= 85: return '85-89'
        if ov >= 80: return '80-84'
        if ov >= 75: return '75-79'
        if ov >= 70: return '70-74'
        if ov <= 5:  return '<=5'
        if ov <= 10: return '6-10'
        if ov <= 15: return '11-15'
        if ov <= 20: return '16-20'
        if ov <= 25: return '21-25'
        return None

    joined = joined.with_columns(
        pl.col('overall').map_elements(bucket_label, return_dtype=pl.Utf8).alias('bucket')
    )

    # --- Aggregate ---
    agg = joined.group_by(['side', 'bucket']).agg([
        pl.count().alias('n'),
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
        pl.col('result').sum().alias('wins'),
        pl.col('exit_return').mean().alias('avg_underlying_ret'),
        pl.col('exit_bars').mean().alias('avg_exit_bars'),
        pl.col('option_pnl_15d').mean().alias('avg_option_pnl'),
        pl.col('option_pnl_15d').filter(pl.col('option_pnl_15d') >= 0.30).count().alias('n_option_tp'),
    ])

    rows = agg.iter_rows(named=True)
    bucket_order = {
        'low': ['70-74', '75-79', '80-84', '85-89', '90-94', '95+'],
        'high': ['21-25', '16-20', '11-15', '6-10', '<=5'],
    }
    summary = []
    for row in rows:
        n_res = row['n_resolved'] or 0
        wins = row['wins'] or 0
        n_opt_tp = row['n_option_tp'] or 0
        wr15 = (wins / n_res * 100) if n_res else None
        opt_tp_pct = (n_opt_tp / n_res * 100) if n_res else None
        summary.append({
            'side': row['side'],
            'bucket': row['bucket'],
            'n': row['n'],
            'wr15': wr15,
            'opt_tp15': opt_tp_pct,
            'avg_option_pnl': row['avg_option_pnl'],
            'avg_exit_bars': row['avg_exit_bars'],
            'avg_underlying_ret': row['avg_underlying_ret'],
        })

    # Print sorted
    print()
    print(f"{'Side':<5} {'Bucket':<8} {'N':>7} {'WR15%':>7} {'OptTP%':>7} {'AvgPnl':>8} {'AvgBars':>8} {'AvgURet':>8}")
    print("-" * 70)
    for side_key in ['low', 'high']:
        for bk in bucket_order[side_key]:
            for s in summary:
                if s['side'] == side_key and s['bucket'] == bk:
                    print(
                        f"{('CALL' if side_key=='low' else 'PUT'):<5} "
                        f"{s['bucket']:<8} "
                        f"{s['n']:>7,} "
                        f"{s['wr15'] or 0:>7.1f} "
                        f"{s['opt_tp15'] or 0:>7.1f} "
                        f"{(s['avg_option_pnl'] or 0)*100:>7.1f}% "
                        f"{s['avg_exit_bars'] or 0:>8.2f} "
                        f"{(s['avg_underlying_ret'] or 0)*100:>7.2f}%"
                    )

    # Save artifact
    with open(OUT_DIR / 'phase1a_bucket_decomp.json', 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n[A] saved -> phase1a_bucket_decomp.json")
    return summary


# ------------------------------------------------------------------
# Phase 1B — concurrent-fire density distribution
# ------------------------------------------------------------------
def phase1b_density():
    print("\n" + "=" * 70)
    print("Phase 1B — Concurrent put-fire density distribution (5y, v31)")
    print("=" * 70)
    t0 = time.time()
    version_id = get_active_version_id()
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    from database.trader_database import DB
    cur = DB.execute_sql(f"""
        SELECT s.date, s.overall
        FROM scores s
        WHERE s.version_id = {version_id}
          AND s.date >= '{cutoff}'
          AND (s.overall <= 25 OR s.overall >= 70)
    """)
    rows = cur.fetchall()
    df = pl.DataFrame({
        'date': [r[0].isoformat() if hasattr(r[0], 'isoformat') else r[0] for r in rows],
        'overall': [int(r[1]) for r in rows],
    })
    df = df.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('call')).otherwise(pl.lit('put'))
          .alias('kind')
    )

    by_day = df.group_by(['date', 'kind']).agg(pl.count().alias('count'))
    pivot = by_day.pivot(index='date', columns='kind', values='count').fill_null(0)
    if 'call' not in pivot.columns:
        pivot = pivot.with_columns(pl.lit(0).alias('call'))
    if 'put' not in pivot.columns:
        pivot = pivot.with_columns(pl.lit(0).alias('put'))

    print(f"[B] {len(pivot)} trading days")
    print(f"[B] Total: {pivot['call'].sum():,} call signals, {pivot['put'].sum():,} put signals")
    print(f"    (ratio put:call = {pivot['put'].sum()/max(1,pivot['call'].sum()):.2f}x)")

    # Distribution
    print("\n  Per-day put count distribution (5y):")
    put_arr = np.array(pivot['put'].to_list())
    for q in [0, 25, 50, 75, 90, 95, 99, 100]:
        v = np.percentile(put_arr, q)
        print(f"    p{q:>3}: {v:>6.1f} puts/day")

    # High-density days
    threshold_high = 50
    high_days = pivot.filter(pl.col('put') >= threshold_high)
    print(f"\n  Days with >={threshold_high} put signals: {len(high_days)} of {len(pivot)} "
          f"({len(high_days)/max(1,len(pivot))*100:.1f}%)")

    # Top 20 highest-density days
    top = pivot.sort('put', descending=True).head(20)
    print("\n  Top 20 highest put-density days:")
    print(f"  {'Date':<12} {'Put':>5} {'Call':>5}")
    for row in top.iter_rows(named=True):
        print(f"  {row['date']:<12} {row['put']:>5} {row['call']:>5}")

    pivot.write_parquet(OUT_DIR / 'phase1b_daily_counts.parquet')
    print(f"\n[B] saved -> phase1b_daily_counts.parquet")


# ------------------------------------------------------------------
# Phase 1C — quick MC ablation
# ------------------------------------------------------------------
def phase1c_ablation():
    print("\n" + "=" * 70)
    print("Phase 1C — MC ablation: drop_lt5, drop_lt10, drop_lt15 vs baseline")
    print("=" * 70)
    print("[C] Run via wrapper: experiments/put_dd_gradient/phase1c_mc_ablation.py")
    print("[C] (separate script — uses subprocess to apply env-gated PUT_MIN_OVERALL filter)")


if __name__ == '__main__':
    phase1a_bucket_decomp()
    phase1b_density()
    phase1c_ablation()
    print("\n" + "=" * 70)
    print("Phase 1 diagnostic (A+B) complete. Run phase1c_mc_ablation.py for MC.")
    print("=" * 70)
