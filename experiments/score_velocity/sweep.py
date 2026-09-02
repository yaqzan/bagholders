"""SVD (Score Velocity Dampener) — sweep over (gate_vel, target, alpha, k).

Applies SVD post-hoc to v39 scores, re-buckets, computes opt_WR15 per tier per window.

Mechanism (in compute_overall_score, applied AFTER regime + existing dampeners):

    if overall >= GATE_SCORE and velocity_5d < GATE_VEL:
        weakness = clip(-(velocity_5d - GATE_VEL) / K, 0, 1)
        overall -= ALPHA * weakness * (overall - TARGET)

GATE_SCORE = 75 (constant)

Per-trade evidence (5y v39):
  - 75+ decelerating heavy (vel<=-10): 44.7% WR15 (-18pp vs baseline, z=-2.33 **)
  - 75+ decel mild (-10<vel<=-5): 51.1% WR15 (-12pp, z=-1.69 *)
  - 75+ accel (vel>+10): 64.0% (+5pp, z=+3.35 ***)

Goal: filter out deep-decel cohort to lift cumulative 75+ TP%.
"""
from __future__ import annotations
import io, sys, time, math, json
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LOG = Path(__file__).parent / 'sweep.jsonl'

CALL_BUCKETS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]


def apply_svd(df, gate_vel, target, alpha, k, gate_score=75):
    """Apply SVD: gate overall >= gate_score AND velocity_5d < gate_vel.

    weakness = clip(-(velocity_5d - gate_vel) / k, 0, 1)
    new = overall - alpha * weakness * (overall - target)
    """
    if alpha <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))

    weakness = (-(pl.col('velocity_5d') - gate_vel) / k).clip(0.0, 1.0)
    in_zone = (
        (pl.col('overall') >= gate_score)
        & (pl.col('velocity_5d').is_not_null())
        & (pl.col('velocity_5d') < gate_vel)
    )
    df = df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') - alpha * weakness * (pl.col('overall') - target))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )
    return df


def evaluate(df, window_start, gate_vel, target, alpha, k):
    sub = df.filter(pl.col('date') >= window_start)
    sub = apply_svd(sub, gate_vel, target, alpha, k)
    sub = sub.filter(pl.col('opt_result_15').is_not_null())
    out = {}
    for thr, label in CALL_BUCKETS:
        cell = sub.filter(pl.col('new_overall') >= thr)
        n = len(cell)
        wins = int((cell['opt_result_15'] == 1).sum()) if n > 0 else 0
        ret = float(cell['opt_exit_return_15'].mean()) if n > 0 else None
        out[label] = {
            'n': n,
            'tp_pct': (wins / n * 100) if n > 0 else None,
            'ret': ret,
        }
    return out


def gate_check(base_5y, var_5y, base_3y, var_3y, base_1y, var_1y, base_10y=None, var_10y=None):
    """Standard H1-H5: ≥+0.5pp on ≥3 of {95+,90+,85+,80+,75+}; none regress >-1pp."""
    out = {}
    deltas = {}
    for thr in ('95+', '90+', '85+', '80+', '75+'):
        b = base_5y[thr]; v = var_5y[thr]
        if b['tp_pct'] is None or v['tp_pct'] is None:
            deltas[thr] = None
        else:
            deltas[thr] = v['tp_pct'] - b['tp_pct']
    out['deltas_5y'] = deltas
    out['n_pct_75plus'] = ((var_5y['75+']['n'] - base_5y['75+']['n']) / base_5y['75+']['n'] * 100) if base_5y['75+']['n'] else 0

    # H1: ≥+0.5pp on ≥3 tiers; none regress > -1pp
    n_tiers_above_05 = sum(1 for d in deltas.values() if d is not None and d >= 0.5)
    worst_regression = min((d for d in deltas.values() if d is not None), default=0)
    out['h1_tiers_lift'] = n_tiers_above_05
    out['h1_worst'] = worst_regression

    # Multi-window 75+ sign consistency
    d_3y = (var_3y['75+']['tp_pct'] - base_3y['75+']['tp_pct']) if (base_3y['75+']['tp_pct'] is not None and var_3y['75+']['tp_pct'] is not None) else None
    d_1y = (var_1y['75+']['tp_pct'] - base_1y['75+']['tp_pct']) if (base_1y['75+']['tp_pct'] is not None and var_1y['75+']['tp_pct'] is not None) else None
    out['mw_3y'] = d_3y; out['mw_1y'] = d_1y
    if base_10y is not None and var_10y is not None and base_10y['75+']['tp_pct'] is not None and var_10y['75+']['tp_pct'] is not None:
        out['mw_10y'] = var_10y['75+']['tp_pct'] - base_10y['75+']['tp_pct']
    else:
        out['mw_10y'] = None

    # Affected-tier framework (sub-75 H1 fix): use 75+ cumulative as primary metric,
    # since SVD primarily affects 75-79 (drops some out to 70-74).
    # But also accept H1 standard if it passes.
    h1_pass = n_tiers_above_05 >= 3 and worst_regression > -1.0
    affected_pass = (
        deltas['75+'] is not None
        and deltas['75+'] >= 0.3
        and out['n_pct_75plus'] >= -15.0
        and (d_3y is None or d_3y >= -0.2)
        and (d_1y is None or d_1y >= -0.5)
        and worst_regression > -1.0
        and (out['mw_10y'] is None or out['mw_10y'] >= -0.3)
    )
    out['h1_pass'] = h1_pass
    out['affected_pass'] = affected_pass
    out['soft_pass'] = h1_pass or affected_pass
    return out


def main():
    # Prefer 10y if available (post-recalc), else 5y
    p10 = ROOT / '.cache' / 'score_velocity' / 'calls_v39_3650.parquet'
    p5 = ROOT / '.cache' / 'score_velocity' / 'calls_v39_1825.parquet'
    parquet = p10 if p10.exists() else p5
    df = pl.read_parquet(parquet)
    print(f'[sweep] loaded {len(df):,} call peaks (70+) from {parquet.name}', flush=True)
    print(f'[sweep] velocity_5d range: [{df["velocity_5d"].min()}, {df["velocity_5d"].max()}]', flush=True)
    print(f'[sweep] date range: [{df["date"].min()}, {df["date"].max()}]', flush=True)

    today = date.today()
    w10y = (today - timedelta(days=3650)).isoformat()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    # Baseline (alpha=0)
    print('\n[baseline] computing 10y/5y/3y/1y windows...', flush=True)
    base_10y = evaluate(df, w10y, 0, 70, 0, 5)
    base_5y = evaluate(df, w5y, 0, 70, 0, 5)
    base_3y = evaluate(df, w3y, 0, 70, 0, 5)
    base_1y = evaluate(df, w1y, 0, 70, 0, 5)
    print('[baseline 5y] CALL TP% (option-aligned barrier, w15):')
    for thr, label in CALL_BUCKETS:
        b = base_5y[label]
        if b['tp_pct'] is not None:
            print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.1f}%")

    # ── COARSE GRID SWEEP ─────────────────────────────────────────────────
    # gate_vel ∈ {0, -3, -5, -10}  — velocity below which dampener fires
    # target   ∈ {65, 70, 73}      — drift target
    # alpha    ∈ {0.50, 0.75, 0.95}
    # k        ∈ {3, 5, 10}        — saturation distance
    candidates = []
    for gate_vel in (0, -3, -5, -10):
        for target in (65, 70, 73):
            for alpha in (0.50, 0.75, 0.95):
                for k in (3, 5, 10):
                    candidates.append((f'g{gate_vel}_t{target}_a{int(alpha*100):02d}_k{k}', gate_vel, target, alpha, k))

    if LOG.exists(): LOG.unlink()

    print(f'\n[sweep] evaluating {len(candidates)} SVD variants...\n', flush=True)
    results = []
    for label, gate_vel, target, alpha, k in candidates:
        t0 = time.time()
        v_10y = evaluate(df, w10y, gate_vel, target, alpha, k)
        v_5y = evaluate(df, w5y, gate_vel, target, alpha, k)
        v_3y = evaluate(df, w3y, gate_vel, target, alpha, k)
        v_1y = evaluate(df, w1y, gate_vel, target, alpha, k)
        gate_res = gate_check(base_5y, v_5y, base_3y, v_3y, base_1y, v_1y, base_10y, v_10y)
        dur = time.time() - t0
        rec = {
            'label': label, 'gate_vel': gate_vel, 'target': target, 'alpha': alpha, 'k': k,
            'gate': gate_res, 'v_5y': v_5y, 'dur': dur,
        }
        results.append(rec)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        flag = '✓' if gate_res['soft_pass'] else ' '
        d75 = gate_res['deltas_5y']['75+'] or 0
        d80 = gate_res['deltas_5y']['80+'] or 0
        d85 = gate_res['deltas_5y']['85+'] or 0
        d90 = gate_res['deltas_5y']['90+'] or 0
        d95 = gate_res['deltas_5y']['95+'] or 0
        mw3 = gate_res['mw_3y']; mw3_s = f'{mw3:+.2f}' if mw3 is not None else '   --'
        print(f"  {label:24s} gv={gate_vel:>3} t={target} a={alpha} k={k:>2} | "
              f"{flag} 75+={d75:+.2f} 80+={d80:+.2f} 85+={d85:+.2f} 90+={d90:+.2f} 95+={d95:+.2f}pp | "
              f"N75 {gate_res['n_pct_75plus']:>+5.1f}% | "
              f"3y {mw3_s}", flush=True)

    # ── RANKING ──────────────────────────────────────────────────────────
    print('\n' + '=' * 130)
    print('SHIP CANDIDATES (soft-pass) — ranked by H1 lift count then 75+ Δ')
    print('=' * 130)
    passed = [r for r in results if r['gate']['soft_pass']]
    passed.sort(key=lambda r: (-r['gate']['h1_tiers_lift'], -(r['gate']['deltas_5y']['75+'] or 0)))
    for r in passed[:20]:
        g = r['gate']
        d75 = g['deltas_5y']['75+'] or 0
        d80 = g['deltas_5y']['80+'] or 0
        d85 = g['deltas_5y']['85+'] or 0
        d90 = g['deltas_5y']['90+'] or 0
        d95 = g['deltas_5y']['95+'] or 0
        h1 = '✓' if g['h1_pass'] else ' '
        af = '✓' if g['affected_pass'] else ' '
        print(f"  {r['label']:24s} gv={r['gate_vel']:>3} t={r['target']} a={r['alpha']} k={r['k']:>2} | "
              f"H1{h1} AF{af} | 75+={d75:+.2f} 80+={d80:+.2f} 85+={d85:+.2f} 90+={d90:+.2f} 95+={d95:+.2f}pp | "
              f"worst={g['h1_worst']:+.2f} N75{g['n_pct_75plus']:+.1f}%")

    if passed:
        winner = passed[0]
        print(f"\n--- TOP CANDIDATE: {winner['label']} ---")
        print('  CALLS (5y):')
        for thr, lbl in CALL_BUCKETS:
            b = base_5y[lbl]; v = winner['v_5y'][lbl]
            if b['tp_pct'] is None or v['tp_pct'] is None:
                continue
            d = v['tp_pct'] - b['tp_pct']
            d_n = (v['n'] - b['n']) / b['n'] * 100 if b['n'] else 0
            print(f"    {lbl:>6} | N {v['n']:>5} (Δ{d_n:+.1f}%) | TP%={v['tp_pct']:>5.1f} (Δ{d:+.2f}pp)")


if __name__ == '__main__':
    main()
