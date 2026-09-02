"""Build CTSL Stage 1 calibration parquet — WR7-primary, barrier-independent.

Per the new three-stage framework in `.claude/docs/process.md` and
`.claude/docs/assessment-backtest.md` "Stage 1: Scoring Calibration Gate":
Stage 1 uses the GENERIC barrier set (K=2σ, M=5σ at w=30 reference) — NOT
the option-aligned barriers used in Stage 2.

Output: `.cache/ctsl/scores_v46_stage1_5y.parquet`

Schema:
  symbol, date, overall, trend, bb, rsi, macd, stoch, kijun_pct, side_label
  win_7d, win_15d, win_30d              ← generic barrier-touch outcomes
  exit_return_7d, exit_return_15d, exit_return_30d
  sigma_pct, entry_close                ← context

The point of WR7 (vs Phase 2.5's WR15) is to catch the "dip-then-recover"
pattern that wider barriers smooth over (v40 SVD class). For CTSL, this asks:
"do CTSL-promoted signals' very-near-term direction (7d) actually justify
their new tier?"
"""
from __future__ import annotations
import io, sys, sqlite3, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR = ROOT / '.cache' / 'ctsl'
OUT_DIR.mkdir(parents=True, exist_ok=True)

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

LOOKBACK_DAYS = 1825
VERSION_COMMIT = 'f274eb6'  # v46 (Phase 3.5 substrate)


def fetch_version_id(commit_prefix: str) -> int:
    from database.trader_database import DB
    cur = DB.execute_sql(
        f"SELECT id FROM algorithm_versions WHERE git_commit = '{commit_prefix}' LIMIT 1"
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f'Version commit {commit_prefix} not found')
    return int(row[0])


def pull_scores(version_id: int, cutoff: str) -> pl.DataFrame:
    """Pull v46 qualifying scores (overall>=70 OR <=25), with kijun_pct."""
    import json
    from database.trader_database import DB
    print(f'[stage1] pulling scores for version_id={version_id} since {cutoff}', flush=True)
    rows = []
    start_year = int(cutoff[:4])
    end_year = date.today().year + 1
    for yr in range(start_year, end_year):
        y_start = f'{yr}-01-01' if yr != start_year else cutoff
        y_end = f'{yr}-12-31'
        ti = time.time()
        cur = DB.execute_sql(f"""
            SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd, s.stoch,
                   s.weight_info
            FROM scores s
            WHERE s.version_id = {version_id}
              AND s.date >= '{y_start}' AND s.date <= '{y_end}'
              AND (s.overall >= 70 OR s.overall <= 25)
        """)
        chunk = cur.fetchall()
        rows.extend(chunk)
        print(f'[stage1]   {yr}: {len(chunk):,} ({time.time()-ti:.1f}s)', flush=True)

    kijun_pcts = []
    for r in rows:
        wi = r[8]
        if wi is None:
            kijun_pcts.append(None)
            continue
        try:
            j = json.loads(wi)
            kp = j.get('kijun_pct')
            kijun_pcts.append(float(kp) if kp is not None else None)
        except (json.JSONDecodeError, TypeError, ValueError):
            kijun_pcts.append(None)

    df = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date': [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
        'overall': [int(r[2]) for r in rows],
        'trend': [int(r[3]) if r[3] is not None else None for r in rows],
        'bb': [int(r[4]) if r[4] is not None else None for r in rows],
        'rsi': [int(r[5]) if r[5] is not None else None for r in rows],
        'macd': [int(r[6]) if r[6] is not None else None for r in rows],
        'stoch': [int(r[7]) if r[7] is not None else None for r in rows],
        'kijun_pct': kijun_pcts,
    })
    df = df.with_columns([
        pl.when(pl.col('overall') >= 70).then(pl.lit('call'))
          .when(pl.col('overall') <= 25).then(pl.lit('put'))
          .otherwise(pl.lit('?')).alias('side_label'),
    ])
    return df


def pull_barriers(cutoff: str, w_days: list[int]) -> pl.DataFrame:
    """Pull generic-barrier outcomes for specified w_days. Side='low'=call, side='high'=put."""
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    w_clause = ','.join(str(w) for w in w_days)
    barriers = pl.read_database(
        f"""SELECT symbol, date, side, w_days,
                   result      AS bw_result,
                   exit_return AS bw_exit_return,
                   sigma_pct   AS sigma_pct,
                   entry_close AS entry_close
            FROM barrier_outcomes
            WHERE barrier_set='30dte_generic'
              AND w_days IN ({w_clause})
              AND date >= '{cutoff}'""",
        connection=conn,
    )
    conn.close()
    barriers = barriers.with_columns(
        pl.when(pl.col('side') == 'low').then(pl.lit('call'))
          .otherwise(pl.lit('put'))
          .alias('side_label')
    ).drop('side')
    print(f'[stage1] barriers: {len(barriers):,} rows ({time.time()-t0:.1f}s)', flush=True)
    return barriers


def pivot_barriers(barriers: pl.DataFrame, w_days: list[int]) -> pl.DataFrame:
    """Pivot long-form barrier results to wide: win_7d, win_15d, win_30d columns."""
    pieces = []
    for w in w_days:
        sub = barriers.filter(pl.col('w_days') == w).select([
            'symbol', 'date', 'side_label',
            pl.col('bw_result').alias(f'win_{w}d'),
            pl.col('bw_exit_return').alias(f'exit_return_{w}d'),
        ])
        if pieces:
            sub = sub.select(['symbol', 'date', 'side_label',
                              f'win_{w}d', f'exit_return_{w}d'])
        pieces.append(sub)

    # Join all windows together
    wide = pieces[0]
    for piece in pieces[1:]:
        wide = wide.join(piece, on=['symbol', 'date', 'side_label'], how='outer_coalesce')

    # Pull sigma_pct + entry_close once (from any w; they're per-(symbol,date))
    ctx = (barriers
           .filter(pl.col('w_days') == w_days[0])
           .select(['symbol', 'date', 'side_label', 'sigma_pct', 'entry_close']))
    wide = wide.join(ctx, on=['symbol', 'date', 'side_label'], how='left')
    return wide


def main():
    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=600000')

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[stage1] lookback={LOOKBACK_DAYS}d cutoff={cutoff}', flush=True)

    vid = fetch_version_id(VERSION_COMMIT)
    print(f'[stage1] v46 (commit={VERSION_COMMIT}) version_id={vid}', flush=True)

    scores = pull_scores(vid, cutoff)
    print(f'[stage1] scores total: {len(scores):,}', flush=True)

    w_days = [7, 15, 30]
    barriers = pull_barriers(cutoff, w_days)
    print(f'[stage1] pivoting barriers ({len(barriers):,} rows -> wide)...', flush=True)
    wide = pivot_barriers(barriers, w_days)
    print(f'[stage1] pivoted: {wide.shape}', flush=True)

    df = scores.join(wide, on=['symbol', 'date', 'side_label'], how='left')
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, 'ctsl/build_stage1_features')

    n_resolved_7 = df.filter(pl.col('win_7d').is_not_null()).height
    n_resolved_15 = df.filter(pl.col('win_15d').is_not_null()).height
    n_resolved_30 = df.filter(pl.col('win_30d').is_not_null()).height
    print(f'[stage1] resolved outcomes: WR7={n_resolved_7:,}  WR15={n_resolved_15:,}  WR30={n_resolved_30:,}',
          flush=True)

    # Cohort breakdown
    n_call_70 = df.filter(pl.col('overall') >= 70).height
    n_put_25 = df.filter(pl.col('overall') <= 25).height
    n_ct_call = df.filter((pl.col('overall') >= 70) & (pl.col('trend') <= 20)).height
    n_ct_put = df.filter((pl.col('overall') <= 25) & (pl.col('trend') >= 80)).height
    print(f'[stage1] cohorts:', flush=True)
    print(f'  call70+:  {n_call_70:>7,}   ct_call (trend<=20): {n_ct_call:>5,} ({100*n_ct_call/n_call_70:.2f}%)', flush=True)
    print(f'  put<=25:  {n_put_25:>7,}   ct_put  (trend>=80): {n_ct_put:>5,} ({100*n_ct_put/n_put_25:.2f}%)', flush=True)

    out = OUT_DIR / f'scores_v46_stage1_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out)
    print(f'[stage1] wrote {out} ({out.stat().st_size/1e6:.1f} MB, {len(df):,} rows)', flush=True)


if __name__ == '__main__':
    main()
