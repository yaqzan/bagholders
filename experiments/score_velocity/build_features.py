"""Build call-peak features with score velocity for the SVD investigation.

For each call peak (overall >= 70 on the active version, 5y), attach:
  - velocity_5d  = overall_today - overall_5_trading_days_ago
  - velocity_3d  = overall_today - overall_3_trading_days_ago
  - prior_score_5d, prior_score_3d
  - barrier outcomes from 30dte_opt at w=15 (option-aligned)

Output: .cache/score_velocity/calls_v{N}_1825.parquet

Hypothesis (Priority #12, known-issues.md): Among signals at score=78, those that
came from {65, 72, 78} carry higher per-trade WR than those from {85, 81, 78}.
If accelerating cohort has +3pp WR over decelerating at multi-tier, design CT-style
cascade promotion or score-stage micro-boost.
"""
from __future__ import annotations
import io, sys, sqlite3, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'score_velocity'

import sys as _sys
LOOKBACK_DAYS = int(_sys.argv[1]) if len(_sys.argv) > 1 else 3650  # default 10y


def main():
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    print(f'[build] Active version: id={av.id} ({av.git_commit[:8]})', flush=True)

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    # Buffer: need 5 trading days BEFORE cutoff for velocity computation
    buffer_cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS + 30)).isoformat()

    # --- Load ALL scores in window (need history for velocity, not just peaks) ---
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {av.id}
          AND s.date >= '{buffer_cutoff}'
          AND s.overall IS NOT NULL
    """)
    rows = cur.fetchall()
    all_scores = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'overall': [int(r[2]) for r in rows],
    }).sort(['symbol', 'date'])
    print(f'[build] all scores: {len(all_scores):,} ({time.time()-t0:.1f}s)', flush=True)

    # --- Compute velocity_5d, velocity_3d per (symbol, date) ---
    t0 = time.time()
    all_scores = all_scores.with_columns([
        pl.col('overall').shift(5).over('symbol').alias('prior_score_5d'),
        pl.col('overall').shift(3).over('symbol').alias('prior_score_3d'),
    ])
    all_scores = all_scores.with_columns([
        (pl.col('overall') - pl.col('prior_score_5d')).alias('velocity_5d'),
        (pl.col('overall') - pl.col('prior_score_3d')).alias('velocity_3d'),
    ])
    print(f'[build] velocity computed ({time.time()-t0:.1f}s)', flush=True)

    # --- Filter to call peaks (overall >= 70) in actual lookback window ---
    peaks = all_scores.filter(
        (pl.col('overall') >= 70) & (pl.col('date') >= cutoff)
    )
    print(f'[build] call peaks (>=70): {len(peaks):,}', flush=True)

    # --- Barrier outcomes from 30dte_opt, side='low' (calls), w=15 ---
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    barriers = pl.read_database(
        f"""SELECT symbol, date, result, exit_return, mae_pct, mfe_pct
            FROM barrier_outcomes
            WHERE side='low' AND barrier_set='30dte_opt'
              AND date >= '{cutoff}'
              AND w_days = 15""",
        connection=conn,
    )
    conn.close()
    print(f'[build] barriers: {len(barriers):,} rows ({time.time()-t0:.1f}s)', flush=True)
    barriers = barriers.rename({
        'result': 'opt_result_15',
        'exit_return': 'opt_exit_return_15',
        'mae_pct': 'opt_mae_15',
        'mfe_pct': 'opt_mfe_15',
    })
    peaks = peaks.join(barriers, on=['symbol', 'date'], how='left')
    print(f'[build] final shape: {peaks.shape}', flush=True)

    out = OUT_DIR / f'calls_v{av.id}_{LOOKBACK_DAYS}.parquet'
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    peaks.write_parquet(out)
    print(f'[build] wrote {out} ({out.stat().st_size/1e6:.1f} MB)', flush=True)


if __name__ == '__main__':
    main()
