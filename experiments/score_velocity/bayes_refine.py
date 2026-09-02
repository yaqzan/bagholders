"""Bayesian-style refinement around g0_t65_a95_k3 SVD winner.

Strategy: dense local grid around the grid-sweep winner, evaluating ALL variants
at 5y AND 10y (10y data now available post-recalc). Picks ship candidate by
maximizing avg(75+ Δ at 5y and 10y) with constraints on:
  - 75+ N drop ≤ 15%
  - Spillover on each tier (95+, 90+, 85+, 80+) within ±0.4pp
  - Multi-window 75+ sign-consistency: all of {1y, 3y, 5y, 10y} positive

Runs ~500 evals at ~0.5s each = ~4 min.
"""
from __future__ import annotations
import io, sys, time, json, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LOG = Path(__file__).parent / 'bayes_refine.jsonl'

CALL_BUCKETS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]


def apply_svd(df, gate_vel, target, alpha, k, gate_score=75):
    if alpha <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))
    weakness = (-(pl.col('velocity_5d') - gate_vel) / k).clip(0.0, 1.0)
    in_zone = (
        (pl.col('overall') >= gate_score)
        & (pl.col('velocity_5d').is_not_null())
        & (pl.col('velocity_5d') < gate_vel)
    )
    return df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') - alpha * weakness * (pl.col('overall') - target))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )


def evaluate(df, window_start, gate_vel, target, alpha, k):
    sub = df.filter(pl.col('date') >= window_start)
    sub = apply_svd(sub, gate_vel, target, alpha, k)
    sub = sub.filter(pl.col('opt_result_15').is_not_null())
    out = {}
    for thr, label in CALL_BUCKETS:
        cell = sub.filter(pl.col('new_overall') >= thr)
        n = len(cell)
        wins = int((cell['opt_result_15'] == 1).sum()) if n > 0 else 0
        out[label] = {
            'n': n,
            'tp_pct': (wins / n * 100) if n > 0 else None,
        }
    return out


def main():
    parquet = ROOT / '.cache' / 'score_velocity' / 'calls_v39_3650.parquet'
    df = pl.read_parquet(parquet)
    print(f'[refine] loaded {len(df):,} call peaks (70+) from {parquet.name}', flush=True)

    today = date.today()
    w10y = (today - timedelta(days=3650)).isoformat()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    # Baseline (alpha=0)
    print('[refine] baseline...', flush=True)
    base_10y = evaluate(df, w10y, 0, 70, 0, 5)
    base_5y = evaluate(df, w5y, 0, 70, 0, 5)
    base_3y = evaluate(df, w3y, 0, 70, 0, 5)
    base_1y = evaluate(df, w1y, 0, 70, 0, 5)

    # ── DENSE LOCAL GRID around g0_t65_a95_k3 ─────────────────────────
    # Coarse grid said best is gate=0, target=65, alpha=0.95, k=3
    # Refine: gate ∈ {0, -1, -2, -3}, target ∈ {62..72}, alpha ∈ {0.70..1.00}, k ∈ {1..5}
    candidates = []
    for gate in (0, -1, -2, -3):
        for target in (62, 64, 65, 66, 68, 70, 72):
            for alpha in (0.70, 0.80, 0.90, 0.95, 1.00):
                for k in (1, 2, 3, 4, 5):
                    candidates.append((gate, target, alpha, k))
    print(f'[refine] {len(candidates)} variants in dense local grid', flush=True)

    if LOG.exists(): LOG.unlink()

    results = []
    t_start = time.time()
    for gate, target, alpha, k in candidates:
        v_10y = evaluate(df, w10y, gate, target, alpha, k)
        v_5y = evaluate(df, w5y, gate, target, alpha, k)
        v_3y = evaluate(df, w3y, gate, target, alpha, k)
        v_1y = evaluate(df, w1y, gate, target, alpha, k)

        deltas = {}
        for thr in ('95+', '90+', '85+', '80+', '75+'):
            b = base_5y[thr]; v = v_5y[thr]
            deltas[thr] = (v['tp_pct'] - b['tp_pct']) if (b['tp_pct'] is not None and v['tp_pct'] is not None) else None

        d_10y_75 = (v_10y['75+']['tp_pct'] - base_10y['75+']['tp_pct']) if (base_10y['75+']['tp_pct'] is not None and v_10y['75+']['tp_pct'] is not None) else None
        d_3y_75 = (v_3y['75+']['tp_pct'] - base_3y['75+']['tp_pct']) if (base_3y['75+']['tp_pct'] is not None and v_3y['75+']['tp_pct'] is not None) else None
        d_1y_75 = (v_1y['75+']['tp_pct'] - base_1y['75+']['tp_pct']) if (base_1y['75+']['tp_pct'] is not None and v_1y['75+']['tp_pct'] is not None) else None
        n_75 = ((v_5y['75+']['n'] - base_5y['75+']['n']) / base_5y['75+']['n'] * 100) if base_5y['75+']['n'] else 0

        # Constraints
        d75_5y = deltas['75+'] or 0
        worst_spillover_neg = min((deltas[t] for t in ('95+', '90+', '85+', '80+') if deltas[t] is not None), default=0)
        worst_spillover_neg = min(worst_spillover_neg, 0)  # only count negative spillover

        # Soft pass criteria (multi-window all positive on 75+, N drop <= 15%)
        all_pos = (d75_5y > 0
                   and (d_3y_75 is None or d_3y_75 >= 0)
                   and (d_1y_75 is None or d_1y_75 >= -0.1)  # tiny tolerance for 1y noise
                   and (d_10y_75 is None or d_10y_75 >= 0)
                   and n_75 >= -15.0
                   and worst_spillover_neg > -0.4)

        # Composite score: avg(5y, 10y) lift on 75+, penalized by negative spillover
        composite = ((d75_5y + (d_10y_75 or 0)) / 2.0) + min(0, worst_spillover_neg)

        rec = {
            'gate_vel': gate, 'target': target, 'alpha': alpha, 'k': k,
            'd75_1y': d_1y_75, 'd75_3y': d_3y_75, 'd75_5y': d75_5y, 'd75_10y': d_10y_75,
            'spillover_neg': worst_spillover_neg,
            'n75_pct': n_75,
            'all_pos': all_pos,
            'composite': composite,
            'deltas_5y': deltas,
        }
        results.append(rec)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    dur = time.time() - t_start
    print(f'[refine] {len(candidates)} variants done in {dur:.1f}s', flush=True)

    # ── RANKINGS ────────────────────────────────────────────────────────
    print('\n=== TOP 10 BY COMPOSITE (avg 5y+10y lift on 75+, penalized by neg spillover) ===')
    results.sort(key=lambda r: -r['composite'])
    for r in results[:10]:
        flag = '✓' if r['all_pos'] else ' '
        d75_3y_s = f"{r['d75_3y']:+.2f}" if r['d75_3y'] is not None else '--'
        d75_1y_s = f"{r['d75_1y']:+.2f}" if r['d75_1y'] is not None else '--'
        d75_10y_s = f"{r['d75_10y']:+.2f}" if r['d75_10y'] is not None else '--'
        print(f"  {flag} g={r['gate_vel']:>3} t={r['target']:>2} a={r['alpha']:.2f} k={r['k']} | "
              f"75+ 1y={d75_1y_s} 3y={d75_3y_s} 5y={r['d75_5y']:+.2f} 10y={d75_10y_s} | "
              f"spill={r['spillover_neg']:+.2f} N75={r['n75_pct']:+.1f}% | "
              f"comp={r['composite']:+.3f}")

    print('\n=== TOP 10 ALL-POSITIVE (multi-window sign-consistent) ===')
    pos_results = [r for r in results if r['all_pos']]
    pos_results.sort(key=lambda r: -r['composite'])
    for r in pos_results[:10]:
        d75_3y_s = f"{r['d75_3y']:+.2f}" if r['d75_3y'] is not None else '--'
        d75_1y_s = f"{r['d75_1y']:+.2f}" if r['d75_1y'] is not None else '--'
        d75_10y_s = f"{r['d75_10y']:+.2f}" if r['d75_10y'] is not None else '--'
        print(f"  g={r['gate_vel']:>3} t={r['target']:>2} a={r['alpha']:.2f} k={r['k']} | "
              f"75+ 1y={d75_1y_s} 3y={d75_3y_s} 5y={r['d75_5y']:+.2f} 10y={d75_10y_s} | "
              f"spill={r['spillover_neg']:+.2f} N75={r['n75_pct']:+.1f}% | "
              f"comp={r['composite']:+.3f}")

    if pos_results:
        winner = pos_results[0]
        print(f"\n--- BAYES-REFINE WINNER ---")
        print(f"  gate_vel={winner['gate_vel']}, target={winner['target']}, alpha={winner['alpha']}, k={winner['k']}")
        print(f"  Multi-window 75+: 1y={winner['d75_1y']} 3y={winner['d75_3y']} 5y={winner['d75_5y']:+.2f} 10y={winner['d75_10y']}")
        print(f"  Composite: {winner['composite']:+.3f}")

        # Compare to grid-sweep winner
        grid_winner_match = next((r for r in results if r['gate_vel']==0 and r['target']==65 and r['alpha']==0.95 and r['k']==3), None)
        if grid_winner_match:
            print(f"\n  Grid-sweep winner (g0_t65_a95_k3): composite = {grid_winner_match['composite']:+.3f}")
            print(f"  Improvement: {winner['composite'] - grid_winner_match['composite']:+.3f}")


if __name__ == '__main__':
    main()
