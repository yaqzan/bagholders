"""Build weekly-volume feature parquet for cohort profiling.

For each peak (call >= 70 or put <= 25) on the active scoring version, attach
weekly-volume features computed from the last completed weekly bar (lookup at
peak_date - 7 days, mirroring v44 ICH's kijun_pct convention to avoid the
partial-week-bar bias that drives COHR-class whiplash).

Features:
  wv_z12     : (last_week_vol - mean_12w) / stdev_12w   — vol z-score
  wv_mom4    : last_week_vol / mean(prev_4_weeks)        — short-term ramp
  wv_cmf4    : 4-week Chaikin Money Flow                 — accumulation strength
  wv_force1  : (close - prev_close) * volume / 4w_avg_vol — normalized force
  wv_align   : wv_z12 * sign(close[-1] - close[-4])      — volume × trend
  wv_obv_slope8 : 8-week linear regression slope of cumulative weekly OBV (sign-only)

Also attaches kijun_pct snapshot (for v44 orthogonality check) and
30dte_opt @ w=15 barrier outcomes.

Output:
  .cache/weekly_volume/calls_v{N}_1825.parquet
  .cache/weekly_volume/puts_v{N}_1825.parquet

Holdout-locked at CALIBRATION_CUTOFF_DATE per Priority #11.
"""
from __future__ import annotations
import io, sys, sqlite3, time, math
import bisect
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
import numpy as np

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
OUT_DIR  = ROOT / '.cache' / 'weekly_volume'
OUT_DIR.mkdir(parents=True, exist_ok=True)

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

LOOKBACK_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1825


def compute_weekly_features(weekly_rows_asc, peak_date):
    """Given ascending list of WeeklyPriceHistory rows for ONE symbol and a peak_date,
    return a dict of features computed from the last completed weekly bar
    (the latest bar with bar_date <= peak_date - 7 days). Returns None if insufficient
    history (< 12 weekly bars before lookup). All values None-safe.
    """
    if not weekly_rows_asc:
        return None
    lookup = peak_date - timedelta(days=7)
    # Find latest weekly bar where date <= lookup
    bar_dates = [w.date for w in weekly_rows_asc]
    idx = bisect.bisect_right(bar_dates, lookup) - 1
    if idx < 11:  # need at least 12 prior bars (idx points at position 11+)
        return None

    # Window of last 12 weeks ending at idx (inclusive)
    win = weekly_rows_asc[idx - 11:idx + 1]   # 12 bars
    closes = []
    highs = []
    lows = []
    vols = []
    for w in win:
        try:
            closes.append(float(w.close))
            highs.append(float(w.high))
            lows.append(float(w.low))
            vols.append(float(w.volume))
        except (TypeError, ValueError):
            return None
    if any(v is None for v in (*closes, *highs, *lows, *vols)):
        return None
    if min(vols) <= 0:
        return None

    last_vol = vols[-1]
    last_close = closes[-1]

    # wv_z12: z-score of last week vol vs 12-week mean
    mean12 = float(np.mean(vols))
    std12  = float(np.std(vols, ddof=1)) if len(vols) > 1 else 0.0
    wv_z12 = (last_vol - mean12) / std12 if std12 > 0 else 0.0

    # wv_mom4: last week vs prev 4-week mean
    if len(vols) >= 5:
        prev4 = float(np.mean(vols[-5:-1]))
        wv_mom4 = last_vol / prev4 if prev4 > 0 else 1.0
    else:
        wv_mom4 = 1.0

    # wv_cmf4: 4-week Chaikin Money Flow
    cmf_num = 0.0
    cmf_den = 0.0
    for i in range(-4, 0):
        h, l, c, v = highs[i], lows[i], closes[i], vols[i]
        rng = h - l
        if rng <= 0 or v <= 0:
            continue
        mfm = ((c - l) - (h - c)) / rng     # money-flow-multiplier in [-1, 1]
        cmf_num += mfm * v
        cmf_den += v
    wv_cmf4 = cmf_num / cmf_den if cmf_den > 0 else 0.0

    # wv_force1: normalized 1-week force index
    if len(closes) >= 2:
        delta_c = last_close - closes[-2]
        avg_v4 = float(np.mean(vols[-5:-1])) if len(vols) >= 5 else mean12
        # Express as % move × volume ratio (dimensionless)
        if closes[-2] > 0 and avg_v4 > 0:
            wv_force1 = (delta_c / closes[-2]) * (last_vol / avg_v4)
        else:
            wv_force1 = 0.0
    else:
        wv_force1 = 0.0

    # wv_align: vol z-score signed by 4-week price direction
    if len(closes) >= 4:
        trend4_dir = 1.0 if last_close > closes[-4] else (-1.0 if last_close < closes[-4] else 0.0)
        wv_align = wv_z12 * trend4_dir
    else:
        wv_align = 0.0

    # wv_obv_slope8: 8-week OBV slope, sign-only diagnostic.
    # Approximated from weekly close moves × volume.
    if len(closes) >= 8:
        obv = [0.0]
        for i in range(-7, 0):
            chg = closes[i] - closes[i - 1]
            sign = 1.0 if chg > 0 else (-1.0 if chg < 0 else 0.0)
            obv.append(obv[-1] + sign * vols[i])
        # Linear regression slope vs index
        xs = np.arange(len(obv), dtype=float)
        ys = np.array(obv, dtype=float)
        if ys.std() > 0:
            slope = float(np.polyfit(xs, ys, 1)[0])
            wv_obv_slope8 = slope / (mean12 + 1.0)   # normalize by avg vol
        else:
            wv_obv_slope8 = 0.0
    else:
        wv_obv_slope8 = 0.0

    return {
        'wv_z12':          float(wv_z12),
        'wv_mom4':         float(wv_mom4),
        'wv_cmf4':         float(wv_cmf4),
        'wv_force1':       float(wv_force1),
        'wv_align':        float(wv_align),
        'wv_obv_slope8':   float(wv_obv_slope8),
        'wv_lookback_idx': int(idx),
        'wv_last_wk_date': bar_dates[idx],
    }


def main():
    from database.models.core import AlgorithmVersion
    from database.models.technical import WeeklyPriceHistory
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    print(f'[build] Active version: id={av.id} ({av.git_commit[:8]})', flush=True)

    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    DB.execute_sql('SET SESSION MAX_EXECUTION_TIME=300000')

    # ── 1. Load peaks (calls >=70, puts <=25) chunked by year ──
    print(f'[build] querying call/put peaks since {cutoff}...', flush=True)
    peaks_rows = []
    start_year = int(cutoff[:4])
    end_year = date.today().year + 1
    for yr in range(start_year, end_year):
        y_start = f'{yr}-01-01' if yr != start_year else cutoff
        y_end = f'{yr}-12-31'
        ti = time.time()
        cur = DB.execute_sql(f"""
            SELECT s.symbol, s.date, s.overall
            FROM scores s
            WHERE s.version_id = {av.id}
              AND s.date >= '{y_start}' AND s.date <= '{y_end}'
              AND (s.overall >= 70 OR s.overall <= 25)
        """)
        chunk = cur.fetchall()
        peaks_rows.extend(chunk)
        print(f'[build]   {yr}: {len(chunk):,} ({time.time()-ti:.1f}s)', flush=True)
    print(f'[build] total {len(peaks_rows):,} peaks', flush=True)

    # Group by symbol
    peaks_by_sym = defaultdict(list)
    for sym, d, ov in peaks_rows:
        if hasattr(d, 'isoformat'):
            d = d
        peaks_by_sym[sym].append((d, int(ov)))

    sym_list = sorted(peaks_by_sym.keys())
    print(f'[build] {len(sym_list):,} symbols with peaks', flush=True)

    # ── 2. Load weekly bars per symbol with sufficient history (1y of weeks back) ──
    # We need weekly bars going back ~1y from earliest peak date for the 12-week window.
    weekly_cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS + 365)).isoformat()
    print(f'[build] loading weekly bars since {weekly_cutoff}...', flush=True)
    t0 = time.time()
    weekly_by_sym = defaultdict(list)
    # Chunk by symbol-batches of 50
    BATCH = 50
    for i in range(0, len(sym_list), BATCH):
        batch_syms = sym_list[i:i + BATCH]
        sym_csv = ','.join(f"'{s}'" for s in batch_syms)
        cur = DB.execute_sql(f"""
            SELECT symbol, date, high, low, close, volume
            FROM weekly_price_history
            WHERE symbol IN ({sym_csv})
              AND date >= '{weekly_cutoff}'
            ORDER BY symbol, date
        """)
        for row in cur.fetchall():
            sym, d, h, l, c, v = row
            class _W:
                __slots__ = ('date', 'high', 'low', 'close', 'volume')
            w = _W()
            w.date = d
            w.high = h
            w.low = l
            w.close = c
            w.volume = v
            weekly_by_sym[sym].append(w)
    print(f'[build] weekly bars loaded for {len(weekly_by_sym):,} symbols ({time.time()-t0:.1f}s)', flush=True)

    # ── 3. Compute features per peak ──
    print(f'[build] computing features...', flush=True)
    t0 = time.time()
    feature_rows = []
    skipped = 0
    for sym in sym_list:
        wks = weekly_by_sym.get(sym, [])
        for d, ov in peaks_by_sym[sym]:
            feat = compute_weekly_features(wks, d)
            if feat is None:
                skipped += 1
                continue
            row = {
                'symbol': sym,
                'date': d.isoformat() if hasattr(d, 'isoformat') else d,
                'overall': ov,
            }
            row.update(feat)
            row['wv_last_wk_date'] = (
                row['wv_last_wk_date'].isoformat()
                if hasattr(row['wv_last_wk_date'], 'isoformat')
                else row['wv_last_wk_date']
            )
            feature_rows.append(row)
    print(f'[build] features computed: {len(feature_rows):,} ({skipped:,} skipped insufficient hist) ({time.time()-t0:.1f}s)', flush=True)

    df = pl.DataFrame(feature_rows)

    # ── 4. Join barriers (calls = side='low', puts = side='high') ──
    t0 = time.time()
    conn = sqlite3.connect(str(CACHE_DB))
    barriers_call = pl.read_database(
        f"""SELECT symbol, date, result AS opt_result_15,
                   exit_return AS opt_exit_return_15,
                   mae_pct AS opt_mae_15, mfe_pct AS opt_mfe_15
            FROM barrier_outcomes
            WHERE side='low' AND barrier_set='30dte_opt'
              AND date >= '{cutoff}' AND w_days = 15""",
        connection=conn,
    )
    barriers_put = pl.read_database(
        f"""SELECT symbol, date, result AS opt_result_15,
                   exit_return AS opt_exit_return_15,
                   mae_pct AS opt_mae_15, mfe_pct AS opt_mfe_15
            FROM barrier_outcomes
            WHERE side='high' AND barrier_set='30dte_opt'
              AND date >= '{cutoff}' AND w_days = 15""",
        connection=conn,
    )
    conn.close()
    print(f'[build] barriers loaded: calls {len(barriers_call):,} | puts {len(barriers_put):,} ({time.time()-t0:.1f}s)', flush=True)

    calls_df = df.filter(pl.col('overall') >= 70).join(barriers_call, on=['symbol', 'date'], how='left')
    puts_df  = df.filter(pl.col('overall') <= 25).join(barriers_put,  on=['symbol', 'date'], how='left')

    # ── 5. Holdout filter ──
    calls_df = pre_cutoff_filter(calls_df)
    puts_df  = pre_cutoff_filter(puts_df)
    assert_no_holdout_leak(calls_df, 'weekly_volume/build_features:calls')
    assert_no_holdout_leak(puts_df,  'weekly_volume/build_features:puts')

    # ── 6. Diagnostics ──
    n_calls_resolved = calls_df.filter(pl.col('opt_result_15').is_not_null()).height
    n_puts_resolved  = puts_df.filter(pl.col('opt_result_15').is_not_null()).height
    print(f'[build] calls: {len(calls_df):,} ({n_calls_resolved:,} resolved)')
    print(f'[build] puts:  {len(puts_df):,} ({n_puts_resolved:,} resolved)')
    print(f'[build] feature distributions (calls, resolved subset):')
    sub = calls_df.filter(pl.col('opt_result_15').is_not_null())
    for col in ['wv_z12', 'wv_mom4', 'wv_cmf4', 'wv_force1', 'wv_align', 'wv_obv_slope8']:
        try:
            qs = sub.select([
                pl.col(col).quantile(0.10).alias('p10'),
                pl.col(col).quantile(0.25).alias('p25'),
                pl.col(col).quantile(0.50).alias('p50'),
                pl.col(col).quantile(0.75).alias('p75'),
                pl.col(col).quantile(0.90).alias('p90'),
            ]).row(0)
            print(f'  {col:<15s} p10={qs[0]:>7.3f}  p25={qs[1]:>7.3f}  p50={qs[2]:>7.3f}  p75={qs[3]:>7.3f}  p90={qs[4]:>7.3f}')
        except Exception:
            pass

    # ── 7. Write parquets ──
    out_calls = OUT_DIR / f'calls_v{av.id}_{LOOKBACK_DAYS}.parquet'
    out_puts  = OUT_DIR / f'puts_v{av.id}_{LOOKBACK_DAYS}.parquet'
    calls_df.write_parquet(out_calls)
    puts_df.write_parquet(out_puts)
    print(f'[build] wrote {out_calls} ({out_calls.stat().st_size/1e6:.1f} MB)')
    print(f'[build] wrote {out_puts} ({out_puts.stat().st_size/1e6:.1f} MB)')


if __name__ == '__main__':
    main()
