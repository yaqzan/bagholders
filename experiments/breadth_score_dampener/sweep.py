"""BSD (Breadth Score Dampener) — sweep over (gate_breadth, target, alpha).

Applies BSD post-hoc to existing v39 scores, re-buckets call peaks, computes
option-aligned barrier WR15 per tier per window. No DB writes.

Mechanism (in compute_overall_score, applied AFTER regime + existing dampeners):

    if overall >= GATE_SCORE and breadth_score is not None and breadth_score <= GATE_BREADTH:
        weakness  = clip((GATE_BREADTH - breadth_score) / GATE_BREADTH, 0, 1)
        overall  -= ALPHA * weakness * (overall - TARGET)

GATE_SCORE = 75 (constant — only fires on cascade-eligible calls).

Per-trade gate (assessment-backtest.md affected-tier framework):
  - Affected tier: 75+ cumulative TP% must improve ≥+0.3pp
  - 80+/85+/90+/95+: within ±0.3pp (no spillover regression)
  - 75+ N drop: ≤ ~15%
  - Multi-window: sign-consistent 1y/3y/5y/10y on 75+
  - Puts: unchanged (BSD gate is overall ≥ 75; puts can't be touched)

Output: console table + JSONL log of all variants.
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


def apply_bsd(df, gate_breadth, target, alpha, gate_score=75):
    """Apply BSD dampener: gate overall >= gate_score AND breadth_score <= gate_breadth.

    weakness = clip((gate_breadth - breadth_score) / gate_breadth, 0, 1)
    new = overall - alpha * weakness * (overall - target)
    """
    if alpha <= 0 or gate_breadth <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))

    weakness = ((gate_breadth - pl.col('breadth_score')) / gate_breadth).clip(0.0, 1.0)
    in_zone = (
        (pl.col('overall') >= gate_score)
        & (pl.col('breadth_score').is_not_null())
        & (pl.col('breadth_score') <= gate_breadth)
    )
    df = df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') - alpha * weakness * (pl.col('overall') - target))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )
    return df


def evaluate(df, window_start, gate_breadth, target, alpha):
    """Per-bucket opt_TP%/N for one BSD variant in the given window."""
    sub = df.filter(pl.col('date') >= window_start)
    sub = apply_bsd(sub, gate_breadth, target, alpha)
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
    """Affected-tier gate: 75+ cumulative TP%."""
    out = {}
    b75 = base_5y['75+']; v75 = var_5y['75+']
    out['affected_delta_5y'] = (v75['tp_pct'] - b75['tp_pct']) if (b75['tp_pct'] is not None and v75['tp_pct'] is not None) else 0
    out['affected_n_pct_5y'] = ((v75['n'] - b75['n']) / b75['n'] * 100) if b75['n'] else 0

    spillover = []
    for thr in ('80+', '85+', '90+', '95+'):
        b = base_5y[thr]; v = var_5y[thr]
        if b['tp_pct'] is None or v['tp_pct'] is None:
            spillover.append((thr, None)); continue
        spillover.append((thr, v['tp_pct'] - b['tp_pct']))
    out['spillover'] = spillover

    d_3y = (var_3y['75+']['tp_pct'] - base_3y['75+']['tp_pct']) if (base_3y['75+']['tp_pct'] is not None and var_3y['75+']['tp_pct'] is not None) else None
    d_1y = (var_1y['75+']['tp_pct'] - base_1y['75+']['tp_pct']) if (base_1y['75+']['tp_pct'] is not None and var_1y['75+']['tp_pct'] is not None) else None
    out['mw_3y'] = d_3y; out['mw_1y'] = d_1y

    if base_10y is not None:
        d_10y = (var_10y['75+']['tp_pct'] - base_10y['75+']['tp_pct']) if (base_10y['75+']['tp_pct'] is not None and var_10y['75+']['tp_pct'] is not None) else None
        out['mw_10y'] = d_10y
    else:
        out['mw_10y'] = None

    soft_pass = (
        out['affected_delta_5y'] >= 0.3
        and all((d is None or abs(d) <= 0.4) for _, d in spillover)
        and out['affected_n_pct_5y'] >= -15.0
        and (d_3y is None or d_3y >= -0.2)
        and (d_1y is None or d_1y >= -0.5)
        and (out['mw_10y'] is None or out['mw_10y'] >= -0.3)
    )
    out['soft_pass'] = soft_pass
    return out


def main(features_parquet, lookback_label='10y'):
    df = pl.read_parquet(features_parquet)
    print(f'[sweep] loaded {len(df):,} call peaks from {features_parquet.name}', flush=True)
    print(f'[sweep] breadth_score range: [{df["breadth_score"].min():.1f}, {df["breadth_score"].max():.1f}]', flush=True)
    print(f'[sweep] date range: [{df["date"].min()}, {df["date"].max()}]', flush=True)

    today = date.today()
    w10y = (today - timedelta(days=3650)).isoformat()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    # Baseline: alpha=0
    print('\n[baseline] computing 10y/5y/3y/1y windows...', flush=True)
    base_10y = evaluate(df, w10y, 30, 70, 0)
    base_5y  = evaluate(df, w5y,  30, 70, 0)
    base_3y  = evaluate(df, w3y,  30, 70, 0)
    base_1y  = evaluate(df, w1y,  30, 70, 0)

    print('[baseline 5y] CALL TP% (option-aligned barrier, w15):')
    for thr, label in CALL_BUCKETS:
        b = base_5y[label]
        if b['tp_pct'] is not None:
            print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.1f}%")
    print('[baseline 10y]:')
    for thr, label in CALL_BUCKETS:
        b = base_10y[label]
        if b['tp_pct'] is not None:
            print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.1f}%")

    # ── COARSE GRID SWEEP ─────────────────────────────────────────────────
    # gate_breadth ∈ {25, 30, 35, 40}  — where the threshold lives
    # target       ∈ {65, 70, 73}      — where to drift toward
    # alpha        ∈ {0.50, 0.75, 0.95} — strength
    # Anchor: gate=30 (matches F3F_CALL_LOW), target=70 (drifts to overflow zone), alpha=0.95 (mirror v32 CWCF)
    candidates = []
    for gate in (25, 30, 35, 40):
        for target in (65, 70, 73):
            for alpha in (0.50, 0.75, 0.95):
                candidates.append((f'g{gate}_t{target}_a{int(alpha*100):02d}', gate, target, alpha))

    if LOG.exists(): LOG.unlink()

    print(f'\n[sweep] evaluating {len(candidates)} BSD variants...\n', flush=True)
    results = []
    for label, gate, target, alpha in candidates:
        t0 = time.time()
        v_10y = evaluate(df, w10y, gate, target, alpha)
        v_5y  = evaluate(df, w5y,  gate, target, alpha)
        v_3y  = evaluate(df, w3y,  gate, target, alpha)
        v_1y  = evaluate(df, w1y,  gate, target, alpha)
        gate_res = gate_check(base_5y, v_5y, base_3y, v_3y, base_1y, v_1y, base_10y, v_10y)
        dur = time.time() - t0
        rec = {
            'label': label, 'gate_breadth': gate, 'target': target, 'alpha': alpha,
            'gate': gate_res, 'v_10y': v_10y, 'v_5y': v_5y, 'dur': dur,
        }
        results.append(rec)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        flag = '✓' if gate_res['soft_pass'] else ' '
        spillover_max = max((abs(d) for _, d in gate_res['spillover'] if d is not None), default=0.0)
        mw3 = gate_res['mw_3y']; mw1 = gate_res['mw_1y']; mw10 = gate_res['mw_10y']
        s3 = '--' if mw3 is None else f'{mw3:+.2f}'
        s1 = '--' if mw1 is None else f'{mw1:+.2f}'
        s10 = '--' if mw10 is None else f'{mw10:+.2f}'
        print(f"  {label:14s} g={gate} t={target} α={alpha} | "
              f"{flag} 75+ Δ={gate_res['affected_delta_5y']:>+5.2f}pp / N {gate_res['affected_n_pct_5y']:>+5.1f}% | "
              f"max spill ±{spillover_max:.2f}pp | "
              f"1y={s1} 3y={s3} 10y={s10} ({dur:.1f}s)", flush=True)

    # ── RANKING ──────────────────────────────────────────────────────────
    print('\n' + '=' * 110)
    print('SHIP CANDIDATES (soft-pass) — ranked by 75+ Δ at 5y')
    print('=' * 110)
    passed = [r for r in results if r['gate']['soft_pass']]
    passed.sort(key=lambda r: -r['gate']['affected_delta_5y'])
    for r in passed:
        g = r['gate']
        s10 = '--' if g['mw_10y'] is None else f"{g['mw_10y']:+.2f}"
        print(f"  {r['label']:14s} g={r['gate_breadth']} t={r['target']} α={r['alpha']} | "
              f"75+ Δ={g['affected_delta_5y']:+.2f}pp N {g['affected_n_pct_5y']:+.1f}% | "
              f"1y {g['mw_1y']:+.2f} 3y {g['mw_3y']:+.2f} 10y {s10}")

    if passed:
        winner = passed[0]
        print(f"\n--- TOP CANDIDATE: {winner['label']} (g={winner['gate_breadth']}, t={winner['target']}, α={winner['alpha']}) ---")
        print('  CALLS (5y):')
        for thr, lbl in CALL_BUCKETS:
            b = base_5y[lbl]; v = winner['v_5y'][lbl]
            if b['tp_pct'] is None or v['tp_pct'] is None:
                print(f"    {lbl:>6} | N={b['n']} (no data)"); continue
            d = v['tp_pct'] - b['tp_pct']
            d_n = (v['n'] - b['n']) / b['n'] * 100 if b['n'] else 0
            print(f"    {lbl:>6} | N {v['n']:>5} (Δ{d_n:+.1f}%) | TP%={v['tp_pct']:>5.1f} (Δ{d:+.2f}pp)")
        print('  CALLS (10y):')
        for thr, lbl in CALL_BUCKETS:
            b = base_10y[lbl]; v = winner['v_10y'][lbl]
            if b['tp_pct'] is None or v['tp_pct'] is None: continue
            d = v['tp_pct'] - b['tp_pct']
            d_n = (v['n'] - b['n']) / b['n'] * 100 if b['n'] else 0
            print(f"    {lbl:>6} | N {v['n']:>5} (Δ{d_n:+.1f}%) | TP%={v['tp_pct']:>5.1f} (Δ{d:+.2f}pp)")


if __name__ == '__main__':
    import os
    # BSD_PARQUET env var lets the queue-runner select the active version's parquet
    # (e.g. calls_v42_1825.parquet) without editing this file.
    override = os.environ.get('BSD_PARQUET')
    if override:
        parquet = Path(override)
        print(f'[sweep] using BSD_PARQUET={parquet}', flush=True)
    else:
        parquet = ROOT / '.cache' / 'breadth_score_dampener' / 'calls_v39_3650.parquet'
        if not parquet.exists():
            # Fallback to 5y if 10y not built yet
            parquet = ROOT / '.cache' / 'breadth_score_dampener' / 'calls_v39_1825.parquet'
            print(f'[sweep] WARNING: 10y parquet not found, using 5y at {parquet.name}', flush=True)
    main(parquet)
