"""Phase 3 — Bayesian sweep of put-side score-stage gradient mechanisms.

Three mechanism families, all gradient/log/tanh shapes:
  M1_depth:        depth-only dampener (lift deeper puts toward neutral)
  M2_depth_breadth: depth × breadth product (Phase 1 finding: macro-correlated days)
  M_breadth:       breadth-only dampener (regime as sole axis)

Per-trade gate uses 30dte_opt barriers + theta-aware option_pnl_pct (NOT
barrier-touch WR15 — Phase 1A showed barrier WR overstates put quality at depth).

Ship metric: avg_option_pnl_15d on puts (must improve), call tiers preserved.

Top 3-5 variants → Phase 4 canonical MC.
"""
from __future__ import annotations
import io, sys, os, sqlite3, json, time, math
from pathlib import Path
from datetime import date, timedelta
from itertools import product

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
SCORES_PQ = ROOT / '.cache' / 'runner' / 'scores_v31_1825.parquet'
OUT_DIR = ROOT / 'experiments' / 'put_dd_gradient'
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 1825
PREMIUM_MULT = 1.82
DELTA = 0.5
TOTAL_DTE = 30
HOLD_DAYS = 15
HARD_SELL_LOSS = -0.40

# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

def load_data():
    """Load scores, barriers, breadth maps. Returns dict of polars frames."""
    t0 = time.time()
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # Scores (v31, with weight_info derived columns)
    scores = pl.read_parquet(SCORES_PQ)
    print(f"[load] scores: {len(scores):,} rows ({time.time()-t0:.1f}s)")

    # Barriers (30dte_opt, w_days=15)
    t0 = time.time()
    bar_pq = ROOT / '.cache' / 'runner' / 'barriers_30opt_w15_1825.parquet'
    if bar_pq.exists():
        barriers = pl.read_parquet(bar_pq)
        print(f"[load] barriers (cached): {len(barriers):,} rows ({time.time()-t0:.1f}s)")
    else:
        conn = sqlite3.connect(str(CACHE_DB))
        barriers = pl.read_database(
            f"""SELECT symbol, date, side, w_days, result, exit_return, exit_bars,
                       entry_close, sigma_pct, fire_high, fire_low, fire_open
                FROM barrier_outcomes
                WHERE date >= '{cutoff}' AND barrier_set = '30dte_opt' AND w_days = 15""",
            connection=conn,
        )
        conn.close()
        barriers.write_parquet(bar_pq)
        print(f"[load] barriers (built): {len(barriers):,} rows ({time.time()-t0:.1f}s)")

    # Compute option_pnl_15d per barrier row (vectorized)
    side_sign = pl.when(pl.col('side') == 'low').then(1.0).otherwise(-1.0)
    sigma_safe = pl.when(pl.col('sigma_pct') > 0).then(pl.col('sigma_pct')).otherwise(1.0)
    premium_pct = (PREMIUM_MULT * sigma_safe / 100.0)
    intrinsic = side_sign * DELTA * pl.col('exit_return') / premium_pct
    theta = ((((TOTAL_DTE - pl.col('exit_bars').clip(0, TOTAL_DTE)) / TOTAL_DTE).sqrt()) - 1.0)
    raw_pnl = (intrinsic + theta).clip(-1.0, 5.0)
    pnl_expr = (
        pl.when(pl.col('result').is_null()).then(None)
          .when(pl.col('exit_bars') >= HOLD_DAYS).then(HARD_SELL_LOSS)
          .otherwise(raw_pnl)
    )
    barriers = barriers.with_columns(pnl_expr.alias('opt_pnl_15d'))

    # Breadth (date → breadth_score)
    t0 = time.time()
    brd_pq = ROOT / '.cache' / 'runner' / 'breadth_1825.parquet'
    if brd_pq.exists():
        breadth = pl.read_parquet(brd_pq)
    else:
        from database.trader_database import DB
        cur = DB.execute_sql(f"""
            SELECT date, breadth_score
            FROM market_breadth
            WHERE date >= '{cutoff}' AND breadth_score IS NOT NULL
            ORDER BY date
        """)
        rows = cur.fetchall()
        breadth = pl.DataFrame({
            'date': [r[0].isoformat() if hasattr(r[0], 'isoformat') else r[0] for r in rows],
            'breadth_score': [float(r[1]) for r in rows],
        })
        breadth.write_parquet(brd_pq)
    print(f"[load] breadth: {len(breadth):,} rows ({time.time()-t0:.1f}s)")

    return scores, barriers, breadth


# ──────────────────────────────────────────────────────────────────────────────
# Mechanism transforms (return new_overall column)
# ──────────────────────────────────────────────────────────────────────────────

def apply_mechanism(df, mech, **params):
    """Compute new_overall from base overall + features per mechanism.

    Mechanisms:
      M0_baseline        — no change
      M1_depth           — depth dampener: lift = K * depth_factor(overall) * (target - overall)
      M2_depth_breadth   — multiplicative depth × stress factors
      M_breadth          — breadth-only dampener

    Common params:
      K            — lift coefficient (0..1)
      score_gate   — only lift puts with overall < this
      target       — lift toward this score
      depth_shape  — 'linear' | 'log' | 'tanh'
      depth_steep  — for tanh: controls how fast depth_factor saturates

    M2/M_breadth also accept:
      brd_thresh   — breadth_score below this triggers stress factor
      brd_floor    — min breadth_score for stress saturation (e.g. 10)
    """
    if mech == 'M0_baseline':
        return df.with_columns(pl.col('overall').alias('new_overall'))

    K = params['K']
    gate = params['score_gate']
    target = params['target']
    shape = params.get('depth_shape', 'linear')

    if mech in ('M1_depth', 'M2_depth_breadth'):
        if shape == 'linear':
            df_factor = ((gate - pl.col('overall')) / gate).clip(0.0, 1.0)
        elif shape == 'log':
            df_factor = (
                (((gate - pl.col('overall')).clip(0, gate) + 1.0).log() / math.log(gate + 1.0))
                .clip(0.0, 1.0)
            )
        elif shape == 'tanh':
            steep = params.get('depth_steep', 8.0)
            df_factor = (1.0 - (pl.col('overall') / steep).tanh()).clip(0.0, 1.0)
        else:
            raise ValueError(f"bad depth_shape: {shape}")
    else:
        df_factor = pl.lit(1.0)

    if mech in ('M2_depth_breadth', 'M_breadth'):
        brd_thresh = params['brd_thresh']
        brd_floor = params.get('brd_floor', 10.0)
        # stress_factor: 0 at brd>=thresh, 1 at brd<=floor, linear ramp
        stress = ((brd_thresh - pl.col('breadth_score')) / max(1.0, brd_thresh - brd_floor)).clip(0.0, 1.0)
        sf = stress
    else:
        sf = pl.lit(1.0)

    # Apply lift only to puts (overall <= 25). Calls untouched.
    lift = K * df_factor * sf * (target - pl.col('overall'))
    new_ov = (
        pl.when((pl.col('overall') < gate) & (pl.col('overall') <= 25))
          .then((pl.col('overall') + lift).round().clip(0, 100).cast(pl.Int64))
          .otherwise(pl.col('overall'))
          .alias('new_overall')
    )
    return df.with_columns(new_ov)


# ──────────────────────────────────────────────────────────────────────────────
# Aggregation: per-bucket OptTP15 + avg_option_pnl_15d + put N reduction
# ──────────────────────────────────────────────────────────────────────────────

BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [25, 20, 15, 10, 5]


def aggregate(scores, barriers):
    """Filter to peaks, join barriers, compute per-cumulative-bucket stats."""
    peaks = scores.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    peaks = peaks.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    j = peaks.join(barriers, on=['symbol', 'date', 'side'], how='inner')

    # Cumulative buckets
    parts = []
    for t in BUY_THRESHOLDS:
        sub = j.filter(pl.col('new_overall') >= t).with_columns(pl.lit(f'{t}+').alias('bucket'))
        parts.append(sub)
    for t in SELL_THRESHOLDS:
        sub = j.filter(pl.col('new_overall') <= t).with_columns(pl.lit(f'<{t}').alias('bucket'))
        parts.append(sub)
    if not parts:
        return {}
    bk = pl.concat(parts, how='vertical')

    agg = bk.group_by('bucket').agg([
        pl.len().alias('n'),
        pl.col('result').filter(pl.col('result').is_not_null()).count().alias('n_resolved'),
        pl.col('result').sum().alias('wins'),
        pl.col('opt_pnl_15d').mean().alias('avg_opt_pnl'),
        pl.col('opt_pnl_15d').filter(pl.col('opt_pnl_15d') >= 0.30).count().alias('n_opt_tp'),
    ])

    out = {}
    for row in agg.iter_rows(named=True):
        n_res = row['n_resolved'] or 0
        out[row['bucket']] = {
            'n': row['n'],
            'wr15': (row['wins'] / n_res * 100) if n_res else None,
            'opt_tp15': (row['n_opt_tp'] / n_res * 100) if n_res else None,
            'avg_opt_pnl': row['avg_opt_pnl'],
        }
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Sweep
# ──────────────────────────────────────────────────────────────────────────────

def gen_variants():
    """Generate variant configurations for M1/M2/M_breadth."""
    variants = []
    variants.append(('M0_baseline', {}))

    # M1_depth: K × score_gate × shape × target
    for K in [0.3, 0.5, 0.7, 1.0]:
        for gate in [10, 15, 20, 25]:
            for shape in ['linear', 'log', 'tanh']:
                for target in [25, 35, 50]:
                    if target <= gate: continue
                    label = f"M1d_K{K}_G{gate}_T{target}_{shape}"
                    variants.append((label, {'mech': 'M1_depth', 'K': K, 'score_gate': gate,
                                              'target': target, 'depth_shape': shape}))

    # M2_depth_breadth: K × gate × shape × target × brd_thresh
    for K in [0.5, 0.7, 1.0]:
        for gate in [15, 20, 25]:
            for shape in ['linear', 'log']:
                for target in [35, 50]:
                    for bt in [30, 40, 50]:
                        label = f"M2db_K{K}_G{gate}_T{target}_B{bt}_{shape}"
                        variants.append((label, {'mech': 'M2_depth_breadth', 'K': K, 'score_gate': gate,
                                                  'target': target, 'depth_shape': shape, 'brd_thresh': bt}))

    # M_breadth: K × gate × target × brd_thresh
    for K in [0.3, 0.5, 0.7, 1.0]:
        for gate in [25]:
            for target in [35, 50]:
                for bt in [30, 40, 50]:
                    label = f"Mbrd_K{K}_T{target}_B{bt}"
                    variants.append((label, {'mech': 'M_breadth', 'K': K, 'score_gate': gate,
                                              'target': target, 'brd_thresh': bt}))
    return variants


def run_sweep():
    print("=" * 80)
    print("Phase 3 — Bayesian sweep (per-trade screening on OptTP15 + avg_option_pnl)")
    print("=" * 80, flush=True)

    scores, barriers, breadth = load_data()

    # Join breadth into scores
    scores = scores.join(breadth, on='date', how='left').with_columns(
        pl.col('breadth_score').fill_null(50.0)  # neutral fallback
    )

    variants = gen_variants()
    print(f"\nSweep: {len(variants)} variants on 5y v31 scores\n")

    results = []
    baseline_stats = None
    t_total = time.time()

    for i, (label, params) in enumerate(variants):
        t0 = time.time()
        if 'mech' in params:
            mech = params.pop('mech')
        else:
            mech = 'M0_baseline'

        df = apply_mechanism(scores, mech, **params)
        stats = aggregate(df, barriers)
        # Restore mech so we can serialize
        params['mech'] = mech
        elapsed = time.time() - t0

        if mech == 'M0_baseline':
            baseline_stats = stats

        results.append({
            'label': label,
            'mech': mech,
            'params': {k: v for k, v in params.items() if k != 'mech'},
            'stats': stats,
            'elapsed': elapsed,
        })
        if (i + 1) % 25 == 0 or i == 0:
            print(f"  [{i+1}/{len(variants)}] {label} ({elapsed:.2f}s)", flush=True)

    print(f"\nTotal sweep time: {time.time()-t_total:.1f}s")

    # Save raw results
    with open(OUT_DIR / 'phase3_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"saved -> phase3_results.json")

    # Score variants by composite metric:
    #   - put_avg_pnl_lift: avg_opt_pnl on '<25' bucket vs baseline
    #   - put_n_reduction: |Δn| / baseline_n on '<25'
    #   - call_preservation: 70+/85+ avg_opt_pnl within ±0.5pp of baseline
    base_p25 = baseline_stats.get('<25', {})
    base_70 = baseline_stats.get('70+', {})
    base_85 = baseline_stats.get('85+', {})

    print("\n" + "=" * 100)
    print(f"{'Variant':<32} {'<25 N':>7} {'<25 OPnl':>9} {'<25 OptTP':>10} "
          f"{'70+ OPnl':>9} {'85+ OPnl':>9} {'ΔPut OPnl':>10} {'ΔPut N%':>9}")
    print("-" * 100)

    rankings = []
    for r in results:
        s_p25 = r['stats'].get('<25', {})
        s_70 = r['stats'].get('70+', {})
        s_85 = r['stats'].get('85+', {})
        p25_n = s_p25.get('n') or 0
        p25_pnl = s_p25.get('avg_opt_pnl')
        p25_opttp = s_p25.get('opt_tp15')
        c70_pnl = s_70.get('avg_opt_pnl')
        c85_pnl = s_85.get('avg_opt_pnl')

        bp_pnl = base_p25.get('avg_opt_pnl')
        bp_n = base_p25.get('n') or 1
        b70 = base_70.get('avg_opt_pnl')
        b85 = base_85.get('avg_opt_pnl')

        d_pnl = (p25_pnl - bp_pnl) if (p25_pnl is not None and bp_pnl is not None) else None
        d_n_pct = (p25_n - bp_n) / bp_n * 100 if bp_n else None

        # Call regression check (must be > -1pp for both 70+ and 85+)
        call_70_ok = (c70_pnl is not None and b70 is not None and (c70_pnl - b70) >= -0.01)
        call_85_ok = (c85_pnl is not None and b85 is not None and (c85_pnl - b85) >= -0.01)
        call_safe = call_70_ok and call_85_ok

        rankings.append({
            'label': r['label'],
            'p25_n': p25_n,
            'p25_pnl': p25_pnl,
            'p25_opttp': p25_opttp,
            'c70_pnl': c70_pnl,
            'c85_pnl': c85_pnl,
            'd_pnl': d_pnl,
            'd_n_pct': d_n_pct,
            'call_safe': call_safe,
        })

    # Sort by put-side avg_opt_pnl improvement (descending), call-safe variants first
    rankings.sort(key=lambda r: (
        not r['call_safe'],
        -(r['d_pnl'] or -999),
    ))

    # Print top 25 + baseline
    for r in rankings[:25]:
        d_pnl_s = f"{r['d_pnl']*100:+6.2f}pp" if r['d_pnl'] is not None else '   --   '
        d_n_s = f"{r['d_n_pct']:+7.1f}%" if r['d_n_pct'] is not None else '   --   '
        flag = '' if r['call_safe'] else '  (CALL REGRESS)'
        print(
            f"{r['label']:<32} "
            f"{r['p25_n']:>7,} "
            f"{(r['p25_pnl'] or 0)*100:>8.1f}% "
            f"{(r['p25_opttp'] or 0):>9.1f}% "
            f"{(r['c70_pnl'] or 0)*100:>8.1f}% "
            f"{(r['c85_pnl'] or 0)*100:>8.1f}% "
            f"{d_pnl_s:>10} "
            f"{d_n_s:>9}{flag}"
        )

    # Print baseline for reference
    print("-" * 100)
    print(f"{'BASELINE M0_baseline':<32} "
          f"{base_p25.get('n', 0):>7,} "
          f"{(base_p25.get('avg_opt_pnl') or 0)*100:>8.1f}% "
          f"{(base_p25.get('opt_tp15') or 0):>9.1f}% "
          f"{(base_70.get('avg_opt_pnl') or 0)*100:>8.1f}% "
          f"{(base_85.get('avg_opt_pnl') or 0)*100:>8.1f}%")

    # Save rankings
    with open(OUT_DIR / 'phase3_rankings.json', 'w') as f:
        json.dump(rankings, f, indent=2, default=str)
    print(f"\nsaved -> phase3_rankings.json")
    return rankings


if __name__ == '__main__':
    run_sweep()
