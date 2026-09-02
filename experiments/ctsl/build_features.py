"""Build CTSL (Counter-Trend Score Lift) calibration parquet.

Phase 1 — pull both call and put cohorts for two reference versions, joined to
the option-aligned 30dte_opt @ w=15 barrier outcomes. The point of two versions
is sensitivity:

  - v44 (d8024b9): post-MCD + post-ICH baseline. The CT-cohort distribution
    that CTSL would actually be applied on top of in production. Primary.
  - v39 (200f33a): pre-MCD + pre-ICH baseline. The CT-cohort *before* recent
    score-stage dampeners reshaped 70-84 calls and kij<0 cohorts. Sensitivity.

If CTSL alpha holds at v44 (post-dampeners), the mechanism is robust against
the current production stack. If alpha only holds on v39 and disappears on
v44 → important null: MCD/ICH already absorbed the CT-cohort lift, CT_PROMOTE
is empirically obsolete, and CTSL has nothing to add either. That answers the
removal question more decisively than running an MC sweep.

Pulls all qualifying signals (overall >= 70 OR overall <= 25), retains the
trend component for cohort filtering, joins to 30dte_opt @ w=15 barriers.

Filters to <= CALIBRATION_CUTOFF_DATE (Priority #11 holdout lock).

Output: .cache/ctsl/scores_v{ID}_1825.parquet (separate file per version).
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

LOOKBACK_DAYS = 1825  # 5y — sufficient for CT cohort N (~1-3% of all signals)

# Version pins to query. {label: git_commit_prefix}.
VERSIONS = {
    'v46': 'f274eb6',  # Phase 2.5 primary baseline (v46 = v44 ICH + v45 breadth + v46 WVD-Wave)
    'v44': 'd8024b9',  # sensitivity (post-MCD/ICH but pre-WVD)
    'v39': '200f33a',  # sensitivity (pre-MCD/ICH stable baseline)
}


def fetch_version_id(commit_prefix: str) -> int:
    from database.trader_database import DB
    cur = DB.execute_sql(
        f"SELECT id FROM algorithm_versions WHERE git_commit = %s LIMIT 1",
        (commit_prefix,),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f'Version with git_commit={commit_prefix!r} not found')
    return int(row[0])


def pull_scores_for_version(version_id: int, cutoff: str) -> pl.DataFrame:
    """Pull all scores in [overall<=25 OR overall>=70] for given version_id, chunked yearly.

    Extracts kijun_pct from weight_info JSON for multi-feature CTSL — the v44+
    Ichimoku weekly state is orthogonal to daily TREND and was the canonical
    cohort-discrimination feature for v44 ICH. CTSL Phase 2.5 tests whether
    combining (TREND, kijun_pct) captures more CT alpha than TREND alone."""
    import json
    from database.trader_database import DB
    print(f'[build] pulling scores for version_id={version_id} since {cutoff}', flush=True)
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
        print(f'[build]   {yr}: {len(chunk):,} ({time.time()-ti:.1f}s)', flush=True)

    # Parse kijun_pct out of weight_info JSON (None if missing or pre-v44)
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


def pull_barriers(cutoff: str) -> pl.DataFrame:
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    # Side='low' = call (peak-from-below); side='high' = put (peak-from-above).
    # We pull both and tag with side_label so downstream join is direction-aware.
    barriers = pl.read_database(
        f"""SELECT symbol, date, side,
                   result      AS opt_result_15,
                   exit_return AS opt_exit_return_15,
                   exit_bars   AS opt_exit_bars_15,
                   mae_pct     AS opt_mae_15,
                   mfe_pct     AS opt_mfe_15,
                   sigma_pct   AS sigma_pct_signal,
                   entry_close AS entry_close
            FROM barrier_outcomes
            WHERE barrier_set='30dte_opt'
              AND w_days = 15
              AND date >= '{cutoff}'""",
        connection=conn,
    )
    conn.close()
    barriers = barriers.with_columns(
        pl.when(pl.col('side') == 'low').then(pl.lit('call'))
          .otherwise(pl.lit('put'))
          .alias('side_label')
    ).drop('side')
    print(f'[build] barriers: {len(barriers):,} ({time.time()-t0:.1f}s)', flush=True)
    return barriers


def build_one(label: str, commit: str, cutoff: str, barriers: pl.DataFrame) -> Path:
    vid = fetch_version_id(commit)
    print(f'[build] === {label} ({commit}) version_id={vid} ===', flush=True)
    scores = pull_scores_for_version(vid, cutoff)
    print(f'[build] {label} total: {len(scores):,}', flush=True)

    # Cohort breakdown by (side_label, score_bucket, ct flag)
    n_call_70p = scores.filter(pl.col('overall') >= 70).height
    n_put_le25 = scores.filter(pl.col('overall') <= 25).height
    n_ct_call = scores.filter((pl.col('overall') >= 70) & (pl.col('trend') <= 20)).height
    n_ct_put = scores.filter((pl.col('overall') <= 25) & (pl.col('trend') >= 80)).height
    print(f'[build] {label} cohorts:')
    print(f'  call70+:  {n_call_70p:>7,}   ct_call (trend<=20): {n_ct_call:>6,} ({100.0*n_ct_call/max(n_call_70p,1):.2f}%)')
    print(f'  put<=25:  {n_put_le25:>7,}   ct_put  (trend>=80): {n_ct_put:>6,} ({100.0*n_ct_put/max(n_put_le25,1):.2f}%)')

    df = scores.join(barriers, on=['symbol', 'date', 'side_label'], how='left')

    # Holdout gate
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, f'ctsl/build_features:{label}')

    n_resolved = df.filter(pl.col('opt_result_15').is_not_null()).height
    print(f'[build] {label} resolved barrier outcomes: {n_resolved:,} / {len(df):,}', flush=True)

    out = OUT_DIR / f'scores_{label}_{LOOKBACK_DAYS}.parquet'
    df.write_parquet(out)
    print(f'[build] wrote {out} ({out.stat().st_size/1e6:.1f} MB)', flush=True)
    return out


def main(only_labels=None):
    from database.trader_database import DB
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=600000')

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[build] lookback={LOOKBACK_DAYS}d cutoff={cutoff}', flush=True)

    targets = {k: v for k, v in VERSIONS.items() if (only_labels is None or k in only_labels)}
    if not targets:
        print(f'[build] no version labels matched {only_labels}', file=sys.stderr)
        return

    # Pull barriers ONCE (shared across versions; barrier outcome only depends on
    # symbol+date, not on which scoring version produced the signal).
    barriers = pull_barriers(cutoff)

    for label, commit in targets.items():
        build_one(label, commit, cutoff, barriers)


if __name__ == '__main__':
    only = sys.argv[1:] or None
    main(only_labels=only)
