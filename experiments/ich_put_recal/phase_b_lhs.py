"""Phase B — LHS blast radius for ICH put-side asymmetric-K.

Based on Phase A finding (2026-05-09): the v44 ICH put-side cohort signal lives
ENTIRELY at the boundary zone [21,30] (z=-6.17). Mid [11,20] has z=+0.94, deep
tail [0,10] has z=+1.08 — both noisy/inverted. Current symmetric-log-ramp
mechanism dampens hardest at deep tail (no signal) and weakest at boundary
(strong signal). Asymmetric-K with score_norm peaking at HI gate boundary
should fix this.

Methodology:
  Use v43 baseline (pre-ICH) puts for "what ICH would do" simulation.
  For each candidate, apply the variant's ICH lift to the v43 score, compute
  new score, and the SURVIVING puts (new_score <= 25, still cascade-admitted)
  are the post-ICH cohort. Score variants by the surviving cohort's WR7.

Run: python experiments/ich_put_recal/phase_b_lhs.py
"""
from __future__ import annotations

import io
import math
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
CACHE_DIR = ROOT / '.cache' / 'ich_put_recal'
V43_PARQUET = CACHE_DIR / 'puts_v43_1825d.parquet'
RESULTS_PATH = CACHE_DIR / 'phase_b_lhs_results.parquet'

V43_VID = 43
LOOKBACK_DAYS = 1825
W_DAYS = 7
PUT_GATE_OUT = 25       # surviving cascade entry: new_score <= 25
N_VARIANTS = 150


def _s(v):
    return v.isoformat() if hasattr(v, 'isoformat') else str(v)[:10]


def build_v43_puts():
    """Build v43 (pre-ICH) puts parquet by reusing phase_a logic."""
    if V43_PARQUET.exists():
        df = pl.read_parquet(V43_PARQUET)
        print(f'[cache] {V43_PARQUET} hit: {len(df):,} rows', flush=True)
        return df

    from database.trader_database import DB
    today = date.today()
    cutoff_iso = today.isoformat()
    try:
        from strategy_config import CALIBRATION_CUTOFF_DATE
        if cutoff_iso > CALIBRATION_CUTOFF_DATE:
            cutoff_iso = CALIBRATION_CUTOFF_DATE
    except Exception:
        pass

    start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    buf_start = (today - timedelta(days=LOOKBACK_DAYS + 400)).isoformat()

    print(f'[build] v{V43_VID} put peaks (<=30) [{start}..{cutoff_iso}]', flush=True)
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {V43_VID}
          AND s.date >= '{start}'
          AND s.date <= '{cutoff_iso}'
          AND s.overall <= 30
    """)
    rows = cur.fetchall()
    peaks = pl.DataFrame({
        'symbol': [r[0] for r in rows],
        'date':   [_s(r[1]) for r in rows],
        'overall':[int(r[2]) for r in rows],
    })
    print(f'[build] v{V43_VID} put peaks: {len(peaks):,}', flush=True)

    # kijun_pct
    print('[build] weekly kijun_pct...', flush=True)
    cur = DB.execute_sql(f"""
        SELECT symbol, date, high, low, close
        FROM weekly_price_history
        WHERE date >= '{buf_start}' AND date <= '{cutoff_iso}'
        ORDER BY symbol, date
    """)
    wrows = cur.fetchall()
    raw = pl.DataFrame({
        'symbol': [r[0] for r in wrows],
        'date':   [_s(r[1]) for r in wrows],
        'high':   [float(r[2]) for r in wrows],
        'low':    [float(r[3]) for r in wrows],
        'close':  [float(r[4]) for r in wrows],
    })
    out_kj = []
    for sym, g in raw.group_by('symbol'):
        gg = g.sort('date').with_columns([
            pl.col('high').rolling_max(window_size=26).alias('high_26w'),
            pl.col('low').rolling_min(window_size=26).alias('low_26w'),
        ]).with_columns(
            ((pl.col('high_26w') + pl.col('low_26w')) / 2.0).alias('kijun')
        ).with_columns(
            ((pl.col('close') - pl.col('kijun')) / pl.col('kijun') * 100.0)
            .alias('kijun_pct')
        )
        out_kj.append(gg.select(['symbol', 'date', 'kijun_pct']))
    kj = pl.concat(out_kj).filter(pl.col('kijun_pct').is_not_null())

    # Join kijun by lookup_date = scoring_date - 7 cal days
    import bisect
    by_sym = defaultdict(list)
    for r in kj.iter_rows():
        by_sym[r[0]].append((r[1], r[2]))
    for s in by_sym:
        by_sym[s].sort()

    kj_vals = []
    for sym, dt, ov in peaks.iter_rows():
        arr = by_sym.get(sym)
        if not arr:
            kj_vals.append(None)
            continue
        sd = date.fromisoformat(dt)
        lookup = (sd - timedelta(days=7)).isoformat()
        dates = [t[0] for t in arr]
        idx = bisect.bisect_right(dates, lookup) - 1
        kj_vals.append(arr[idx][1] if idx >= 0 else None)

    peaks = peaks.with_columns(pl.Series('kijun_pct', kj_vals, dtype=pl.Float64))
    peaks = peaks.filter(pl.col('kijun_pct').is_not_null())

    # WR7 outcomes
    print('[build] barrier_outcomes WR7...', flush=True)
    syms = sorted(peaks['symbol'].unique().to_list())
    placeholders = ','.join('?' for _ in syms)
    conn = sqlite3.connect(str(ROOT / '.cache' / 'barrier_outcomes.db'))
    try:
        cur = conn.execute(f"""
            SELECT symbol, date, result
            FROM barrier_outcomes
            WHERE side = 'high' AND w_days = {W_DAYS}
              AND barrier_set = '30dte_generic'
              AND symbol IN ({placeholders})
        """, syms)
        bo = cur.fetchall()
    finally:
        conn.close()
    cache = pl.DataFrame({
        'symbol': [r[0] for r in bo],
        'date':   [_s(r[1]) for r in bo],
        'wr7':    [int(r[2]) if r[2] is not None else None for r in bo],
    }).filter(pl.col('wr7').is_not_null())

    out = peaks.join(cache, on=['symbol', 'date'], how='inner')
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out.write_parquet(V43_PARQUET)
    print(f'[build] {V43_PARQUET}: {len(out):,} rows', flush=True)
    return out


# --------------------------------------------------------------------------
# ICH put variant simulation
# --------------------------------------------------------------------------

def _ramp_log(x, sat):
    if sat <= 0:
        return 0.0
    z = max(0.0, x) / sat
    if z <= 0:
        return 0.0
    if z >= 1:
        return 1.0
    return math.log1p(z * (math.e - 1)) / 1.0  # log_{e}(1 + z*(e-1)); equals 1 at z=1


def _ramp_linear(x, sat):
    if sat <= 0:
        return 0.0
    return min(1.0, max(0.0, x / sat))


def apply_variant(overall, kijun_pct, params):
    """Apply an asymmetric-K ICH put-side variant. Returns new score (int).

    params = (K, POWER, GATE_LO, GATE_HI, KIJ_SAT, TARGET, ind_ramp)
    score_norm peaks at GATE_HI (boundary), tapers to 0 at GATE_LO.
    """
    K, POWER, GATE_LO, GATE_HI, KIJ_SAT, TARGET, ind_ramp = params
    if kijun_pct >= 0 or overall > GATE_HI:
        return overall
    if overall <= GATE_LO:
        return overall  # below gate floor, untouched (preserves <5/<10 N)

    score_range = max(1, GATE_HI - GATE_LO)
    score_norm = max(0.0, min(1.0, (overall - GATE_LO) / score_range))
    k_eff = K * (score_norm ** POWER)
    ind_dist = -kijun_pct
    if ind_ramp == 'linear':
        ig = _ramp_linear(ind_dist, KIJ_SAT)
    else:
        ig = _ramp_log(ind_dist, KIJ_SAT)
    if k_eff <= 0 or ig <= 0:
        return overall
    lift = k_eff * ig * (TARGET - overall)
    if lift <= 0:
        return overall
    new = overall + lift
    return int(max(0, min(100, round(new))))


# --------------------------------------------------------------------------
# Sweep
# --------------------------------------------------------------------------

def lhs_sample(n, ranges, seed=42):
    """Latin Hypercube Sample over ranges = [(lo, hi), ...]."""
    import random
    rng = random.Random(seed)
    d = len(ranges)
    samples = [[] for _ in range(d)]
    for i in range(d):
        bins = list(range(n))
        rng.shuffle(bins)
        lo, hi = ranges[i]
        for b in bins:
            samples[i].append(lo + (hi - lo) * (b + rng.random()) / n)
    return list(zip(*samples))


def evaluate(df: pl.DataFrame, params) -> dict:
    """Evaluate variant on v43 puts; return summary metrics."""
    overalls = df['overall'].to_numpy()
    kjs = df['kijun_pct'].to_numpy()
    wr7s = df['wr7'].to_numpy()

    new_scores = [apply_variant(int(overalls[i]), float(kjs[i]), params)
                  for i in range(len(overalls))]
    new_scores = pl.Series('new_score', new_scores, dtype=pl.Int64)

    work = df.with_columns(new_scores)
    surviving = work.filter(pl.col('new_score') <= PUT_GATE_OUT)
    if len(surviving) == 0:
        return {'n_kept': 0}

    # Per-discrete-bucket WR7
    def bkt_wr(lo, hi):
        b = surviving.filter(pl.col('new_score').is_between(lo, hi))
        if len(b) == 0:
            return None, 0
        return float(b['wr7'].mean()) * 100, len(b)

    wr_5, n_5     = bkt_wr(0, 5)
    wr_10, n_10   = bkt_wr(0, 10)
    wr_15, n_15   = bkt_wr(0, 15)
    wr_20, n_20   = bkt_wr(0, 20)
    wr_25, n_25   = bkt_wr(0, 25)

    return {
        'n_kept': len(surviving),
        'wr7_kept': float(surviving['wr7'].mean()) * 100,
        'wr_le5': wr_5,    'n_le5': n_5,
        'wr_le10': wr_10,  'n_le10': n_10,
        'wr_le15': wr_15,  'n_le15': n_15,
        'wr_le20': wr_20,  'n_le20': n_20,
        'wr_le25': wr_25,  'n_le25': n_25,
    }


def baseline_metrics(df):
    """v43 baseline (no ICH applied)."""
    sub = df.filter(pl.col('overall') <= PUT_GATE_OUT)
    wr_kept = float(sub['wr7'].mean()) * 100

    def bkt(lo, hi):
        b = sub.filter(pl.col('overall').is_between(lo, hi))
        return (float(b['wr7'].mean()) * 100, len(b)) if len(b) > 0 else (None, 0)

    out = {'n_kept': len(sub), 'wr7_kept': wr_kept}
    for lo, hi, lbl in [(0,5,'5'), (0,10,'10'), (0,15,'15'), (0,20,'20'), (0,25,'25')]:
        wr, n = bkt(lo, hi)
        out[f'wr_le{lbl}'] = wr
        out[f'n_le{lbl}'] = n
    return out


def main():
    df = build_v43_puts()
    print(f'\nv43 baseline: {len(df):,} put peaks loaded')

    base = baseline_metrics(df)
    print('\nv43 BASELINE (pre-ICH):')
    print(f"  n_kept (<=25): {base['n_kept']:,}  WR7 = {base['wr7_kept']:.2f}%")
    for tag in ['5', '10', '15', '20', '25']:
        wr = base[f'wr_le{tag}']
        n = base[f'n_le{tag}']
        if wr is not None:
            print(f"  <={tag:>2}: WR7={wr:>5.2f}%  N={n:,}")

    # LHS over 7 params
    # K, POWER, GATE_LO, GATE_HI, KIJ_SAT, TARGET, ind_ramp
    ranges = [
        (0.15, 0.55),    # K
        (1.0, 4.0),      # POWER
        (10.0, 22.0),    # GATE_LO (raised range vs current 10)
        (24.0, 32.0),    # GATE_HI (around current 27)
        (4.0, 14.0),     # KIJ_SAT (around current 8.8)
        (28.0, 40.0),    # TARGET (around current 33.4)
    ]
    samples = lhs_sample(N_VARIANTS, ranges, seed=42)

    print(f'\nRunning {N_VARIANTS} LHS variants...')
    results = []
    t0 = time.time()
    for i, raw in enumerate(samples):
        K, POWER, GATE_LO_f, GATE_HI_f, KIJ_SAT, TARGET = raw
        GATE_LO = int(round(GATE_LO_f))
        GATE_HI = int(round(GATE_HI_f))
        if GATE_HI <= GATE_LO + 2:
            continue
        for ind_ramp in ('linear', 'log'):
            params = (K, POWER, GATE_LO, GATE_HI, KIJ_SAT, TARGET, ind_ramp)
            r = evaluate(df, params)
            r.update({'K': K, 'POWER': POWER, 'GATE_LO': GATE_LO,
                      'GATE_HI': GATE_HI, 'KIJ_SAT': KIJ_SAT,
                      'TARGET': TARGET, 'ind_ramp': ind_ramp})
            results.append(r)
        if (i + 1) % 25 == 0:
            print(f'  variant {i+1}/{N_VARIANTS}  ({time.time()-t0:.1f}s)', flush=True)

    res = pl.DataFrame(results)
    res = res.with_columns(
        (pl.col('wr7_kept') - base['wr7_kept']).alias('lift_pp')
    )
    # W4 hard constraint: <5 and <10 WR7 >= baseline -0.5pp
    base_5  = base['wr_le5']  or 0.0
    base_10 = base['wr_le10'] or 0.0
    res = res.with_columns([
        (pl.col('wr_le5')  >= (base_5  - 0.5)).alias('w4_le5_pass'),
        (pl.col('wr_le10') >= (base_10 - 0.5)).alias('w4_le10_pass'),
    ])
    res = res.with_columns(
        (pl.col('w4_le5_pass') & pl.col('w4_le10_pass')).alias('w4_pass')
    )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    res.write_parquet(RESULTS_PATH)
    print(f'\n[cache] wrote {RESULTS_PATH}: {len(res):,} variants')

    # Top 15 by lift, separately for W4-pass and W4-fail
    print('\n' + '='*100)
    print('TOP 15 candidates by WR7 lift, W4_PASS only (preserves <5 and <10)')
    print('='*100)
    pas = res.filter(pl.col('w4_pass'))
    print(f'W4_pass count: {len(pas)} of {len(res)}')
    if len(pas) > 0:
        top = pas.sort('lift_pp', descending=True).head(15)
        print('{:>5} {:>5} {:>4} {:>4} {:>5} {:>5} {:>4} {:>7} {:>7} {:>7} {:>7} {:>7} {:>5}'.format(
            'K', 'POW', 'LO', 'HI', 'SAT', 'TGT', 'rmp', 'wr_kpt', 'lift', 'wr_le5', 'wr_le10', 'wr_le25', 'n_kpt'))
        for r in top.iter_rows(named=True):
            print('{:>5.2f} {:>5.2f} {:>4} {:>4} {:>5.1f} {:>5.1f} {:>4} {:>7.2f} {:>+7.2f} {:>7} {:>7} {:>7} {:>5}'.format(
                r['K'], r['POWER'], r['GATE_LO'], r['GATE_HI'],
                r['KIJ_SAT'], r['TARGET'], r['ind_ramp'][:3],
                r['wr7_kept'], r['lift_pp'],
                f"{r['wr_le5']:.1f}" if r['wr_le5'] is not None else '--',
                f"{r['wr_le10']:.1f}" if r['wr_le10'] is not None else '--',
                f"{r['wr_le25']:.1f}" if r['wr_le25'] is not None else '--',
                r['n_kept']))

    print('\n' + '='*100)
    print('TOP 5 W4-FAILED candidates (for reference — would regress <5 or <10)')
    print('='*100)
    fail = res.filter(~pl.col('w4_pass'))
    if len(fail) > 0:
        topf = fail.sort('lift_pp', descending=True).head(5)
        for r in topf.iter_rows(named=True):
            print('{:>5.2f} {:>5.2f} {:>4} {:>4} {:>5.1f} {:>5.1f} {:>4} lift={:>+5.2f} wr_le5={:>5} wr_le10={:>5}'.format(
                r['K'], r['POWER'], r['GATE_LO'], r['GATE_HI'],
                r['KIJ_SAT'], r['TARGET'], r['ind_ramp'][:3],
                r['lift_pp'],
                f"{r['wr_le5']:.1f}" if r['wr_le5'] is not None else '--',
                f"{r['wr_le10']:.1f}" if r['wr_le10'] is not None else '--'))


if __name__ == '__main__':
    main()
