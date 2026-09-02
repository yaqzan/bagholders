"""Quick call-peak builder (no velocity, no buffer) for v40-vs-v39 comparison.

Pulls only `overall >= 70` rows for the requested version and joins to
barrier_outcomes (30dte_opt @ w=15). Skips the full-scores load that
build_features.py needs for velocity_5d. Runtime ~30s vs ~5min.

Usage: SVD_VERSION_ID=40 python quick_build_peaks.py [lookback_days]
"""
from __future__ import annotations
import io, sys, sqlite3, time, os
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'score_velocity'

LOOKBACK_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1825
VID = int(os.environ.get('SVD_VERSION_ID', 39))


def main():
    from database.trader_database import DB

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[qbuild] version_id={VID}  lookback={LOOKBACK_DAYS}d  cutoff={cutoff}', flush=True)

    t0 = time.time()
    cur = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores WHERE version_id = %s AND overall >= 70 AND date >= %s",
        (VID, cutoff),
    )
    rows = cur.fetchall()
    df = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'overall': [int(r[2]) for r in rows],
    })
    print(f'[qbuild] call peaks (>=70): {len(df):,}  ({time.time()-t0:.1f}s)', flush=True)

    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    barriers = pl.read_database(
        f"""SELECT symbol, date, result, exit_return
            FROM barrier_outcomes
            WHERE side='low' AND barrier_set='30dte_opt' AND w_days = 15
              AND date >= '{cutoff}'""",
        connection=conn,
    )
    conn.close()
    print(f'[qbuild] barriers: {len(barriers):,}  ({time.time()-t0:.1f}s)', flush=True)
    barriers = barriers.rename({'result': 'opt_result_15', 'exit_return': 'opt_exit_return_15'})

    df = df.join(barriers, on=['symbol', 'date'], how='left')
    print(f'[qbuild] joined: {df.shape}', flush=True)

    out = OUT_DIR / f'peaks_v{VID}_{LOOKBACK_DAYS}.parquet'
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    print(f'[qbuild] wrote {out} ({out.stat().st_size/1e6:.1f} MB)', flush=True)


if __name__ == '__main__':
    main()
